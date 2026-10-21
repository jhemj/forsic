"""Preserved Docker environments and case-owned selections, outside investigation state.

All writes use local files, flock and atomic rename. Management never receives a
case mount; an execution uses image IDs and helper bytes from an immutable record.
"""
from contextlib import contextmanager
import copy
from contextvars import ContextVar
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid

from .execution_contract import ExecutionFailure

SCHEMA = 1
API_VERSION = 1
SOURCE = Path(__file__).resolve().parents[1]
HELPERS = ('image_reader.py', 'literal_search.py', 'image_access.py',
           'structured_query.py', 'login_records.py', 'triage_scan.py', 'sensitive_scan.py')
IMAGE_RE = re.compile(r'sha256:[0-9a-f]{64}')
SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
MAX_BUNDLE_BYTES = 32 * 1024**3
MAX_FILES = 20000
_threads = {}
_turn_snapshot = ContextVar("forsic_environment_snapshot", default=None)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _id(value):
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError('Invalid management identifier')
    return value


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            os.chmod(temporary, 0o600)
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _lock(path, *, shared=False, nonblocking=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) |
                    (fcntl.LOCK_NB if nonblocking else 0))
        yield
    finally:
        os.close(fd)


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return copy.deepcopy(default)


def default_root():
    try:
        from .tenants import current_scope
    except ImportError:
        current_scope = None
    if current_scope is not None:
        scope = current_scope(required=False)
        if scope is not None:
            return Path(scope.get('management_root') or Path(scope['profile_home']) / 'forsic-management')
    try:
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home())
    except ImportError:
        home = Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes'))
    return Path(os.environ.get('FORSIC_MANAGEMENT_ROOT', home / 'forsic-management'))


def _process_identity(pid):
    """Boot ID + kernel start ticks identify a process, unlike a recycled PID."""
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'start_ticks': stat[19],
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    except (OSError, IndexError):
        return None


class EnvironmentManager:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else default_root()

    def _state(self):
        return _read(self.root / 'state.json', {'schema_version': SCHEMA, 'revision': 0,
                                                'default_environment_id': None})

    def manifest(self, environment_id, *, verify=True):
        directory = self.root / 'environments' / _id(environment_id)
        manifest = _read(directory / 'manifest.json')
        if not manifest or manifest.get('environment_id') != environment_id:
            raise ValueError('Preserved environment does not exist')
        identity = {k: v for k, v in manifest.items() if k != 'environment_id'}
        if 'env-' + _sha(_json(identity)) != environment_id:
            raise ValueError('Environment manifest integrity mismatch')
        if manifest.get('schema_version') != SCHEMA or manifest.get('extension_api') != API_VERSION:
            raise ValueError('Environment API is incompatible')
        _validate_components(manifest)
        if verify:
            for name, entry in manifest['helpers'].items():
                if name not in HELPERS or _file_sha(directory / 'helpers' / name) != entry['sha256']:
                    raise ValueError('Preserved helper integrity mismatch: ' + name)
        return manifest

    def catalog(self):
        records = []
        for path in sorted((self.root / 'environments').glob('*/manifest.json')):
            try:
                records.append(self.manifest(path.parent.name))
            except (ValueError, OSError) as exc:
                records.append({'environment_id': path.parent.name, 'state': 'unavailable', 'error': str(exc)})
        operations = [self.operation(p.stem) for p in sorted((self.root / 'operations').glob('*.json'))[-50:] if not p.stem.endswith('.fixture')]
        return {**self._state(), 'environments': records, 'operations': operations,
                'supported_platforms': ['linux/amd64'], 'host_runtime': 'not managed or restored'}

    def _inspect(self, image):
        result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{json .}}', image],
                                capture_output=True, timeout=20, check=False)
        if result.returncode:
            raise ValueError('Local image unavailable; no pull attempted: ' + image)
        value = json.loads(result.stdout)
        if not IMAGE_RE.fullmatch(value.get('Id', '')):
            raise ValueError('Docker returned no immutable image ID')
        return {'image_id': value['Id'], 'repo_digests': value.get('RepoDigests') or [],
                'os': value['Os'], 'architecture': value['Architecture'],
                'platform': value['Os'] + '/' + value['Architecture']}

    def _change_operation(self, operation, **changes):
        path = self.root/'operations'/(_id(operation)+'.json')
        with _lock(path.with_suffix('.record.lock')):
            record = _read(path)
            record.update(changes)
            _write(path, record)
            return record

    def _run(self, operation, argv, *, timeout=1800, cwd=None, output_limit=4*1024**2):
        """Bounded subprocess with durable identity and operation-scoped cancellation."""
        from .image_process import _BoundedCapture
        if self.operation(operation).get('cancel_requested'):
            raise InterruptedError('Management operation cancelled')
        argv = list(argv)
        container = None
        if argv[:2] == ['docker','run']:
            if '--name' in argv:
                container = argv[argv.index('--name')+1]
            else:
                container = 'forsic-management-' + uuid.uuid4().hex
                argv[2:2] = ['--name',container]
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        self._change_operation(operation, process=_process_identity(process.pid), argv=argv,
                               container=container, started_at=time.time())
        capture = _BoundedCapture(process, output_limit)
        start = time.monotonic()
        try:
            while True:
                cancelled = self.operation(operation).get('cancel_requested')
                if cancelled or time.monotonic()-start > timeout or capture.exceeded:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=15)
                    if cancelled:
                        raise InterruptedError('Management operation cancelled')
                    raise ValueError('Management process timed out or exceeded its output bound')
                if capture.step(.1) and process.poll() is not None:
                    break
            stdout, stderr = capture.output()
            if process.returncode:
                raise ValueError('Management command failed: ' + stderr.decode(errors='replace')[-1500:])
            return stdout
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=15)
            stdout, stderr = capture.output()
            with (self.root / 'operations' / (operation + '.log')).open('ab') as log:
                log.write(b'\n$ ' + _json(argv) + b'\n' + stdout + b'\n' + stderr)
            capture.close()
            process.stdout.close()
            process.stderr.close()
            cleanup = None
            if container:
                removed = subprocess.run(['docker','rm','-f',container],capture_output=True,timeout=15,check=False)
                cleanup = {'container':container,'confirmed':removed.returncode==0 or b'No such container' in removed.stderr}
            self._change_operation(operation,process=None,last_exit_code=process.returncode,cleanup=cleanup)
            if cleanup is not None and not cleanup['confirmed']:
                raise ExecutionFailure('Management container cleanup could not be confirmed.',
                    error_code='management_cleanup_unknown',failure_stage='cleanup',
                    execution={'state':'completion_unknown','backend':'docker','cleanup':cleanup,'termination_confirmed':False})

    def _start(self, kind, inputs, operation_id, work, *, background=True):
        operation_id = _id(operation_id)
        with _lock(self.root / '.lock'):
            path = self.root / 'operations' / (operation_id + '.json')
            existing = _read(path)
            fingerprint = _sha(_json({'kind': kind, 'inputs': inputs}))
            if existing:
                if existing['input_sha256'] != fingerprint:
                    raise ValueError('Operation ID reused with different inputs')
                return self.operation(operation_id)
            _write(path, {'schema_version': SCHEMA, 'operation_id': operation_id,
                'kind': kind, 'input_sha256': fingerprint, 'inputs': inputs, 'state': 'queued',
                'stage': 'queued', 'created_at': time.time(), 'process': None,
                'log': str(path.with_suffix('.log'))})
        def execute():
            # Keep an OS lease across the complete operation, including gaps
            # between subprocesses. Reopening the app cannot invent liveness.
            with _lock(path.with_suffix('.lock')):
                try:
                    self._stage(operation_id, 'preparing')
                    result = work(operation_id)
                    record = self.operation(operation_id)
                    record.update(state='cancelled' if record.get('cancel_requested') else 'succeeded',
                                  result=result, finished_at=time.time())
                except Exception as exc:
                    record = self.operation(operation_id)
                    record.update(state='cancelled' if isinstance(exc, InterruptedError) else 'failed',
                                  error=str(exc), error_type=type(exc).__name__,
                                  retry_stage=record['stage'], finished_at=time.time(),
                                  failure=getattr(exc,'details',{}))
                self._change_operation(operation_id, **{key:value for key,value in record.items() if key != 'cancel_requested'})
        if background:
            thread = threading.Thread(target=execute, name='forsic-env-' + operation_id, daemon=True)
            _threads[(str(self.root), operation_id)] = thread
            thread.start()
        else:
            execute()
        return self.operation(operation_id)

    def _stage(self, operation, stage):
        record = self.operation(operation)
        if record.get('cancel_requested'):
            raise InterruptedError('Management operation cancelled')
        self._change_operation(operation,stage=stage,state='running')

    def operation(self, operation_id):
        operation_id = _id(operation_id)
        path = self.root / 'operations' / (operation_id + '.json')
        record = _read(path)
        if record is None:
            raise ValueError('Management operation not found')
        if record['state'] in ('running', 'queued'):
            thread = _threads.get((str(self.root), operation_id))
            if not thread or not thread.is_alive():
                try:
                    with _lock(path.with_suffix('.lock'), nonblocking=True):
                        if time.time() - record['created_at'] > 2:
                            identity=record.get('process')
                            alive=bool(identity and _process_identity(identity['pid'])==identity)
                            record.update(state='completion_unknown' if alive else 'interrupted',
                                process_identity_verified=alive,
                                error='Operation worker lease is absent; inspect logs and exact process identity before retry')
                except BlockingIOError:
                    pass
        return record

    def cancel(self, operation_id):
        with _lock(self.root / '.lock'):
            record = self.operation(operation_id)
            if record['state'] in ('running', 'queued'):
                return self._change_operation(operation_id,cancel_requested=True)
            if record['state'] in ('interrupted','completion_unknown'):
                identity=record.get('process')
                current=_process_identity(identity['pid']) if identity else None
                signal_error=None
                if identity and identity['pid'] != os.getpid() and current == identity:
                    try:
                        os.killpg(identity['pid'],signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except OSError as exc:
                        signal_error=type(exc).__name__
                elif identity and current == identity:
                    raise ValueError('Refusing to terminate the hosting application process')
                # Sending SIGKILL is a request, not evidence of termination.
                # A changed start identity also confirms that the original
                # process is gone without ever signalling the reused PID.
                process_absent=not identity or _process_identity(identity['pid']) != identity
                container=record.get('container')
                cleanup=None
                if container and container.startswith(('forsic-management-','forsic-env-','forsic-fixture-')):
                    try:
                        removed=subprocess.run(['docker','rm','-f',container],capture_output=True,timeout=15,check=False)
                        cleanup={'container':container,'confirmed':removed.returncode==0 or b'No such container' in removed.stderr}
                    except (OSError,subprocess.SubprocessError) as exc:
                        cleanup={'container':container,'confirmed':False,'error_type':type(exc).__name__}
                container_absent=not container or bool(cleanup and cleanup['confirmed'])
                confirmed=process_absent and container_absent
                record=self._change_operation(operation_id,cancel_requested=True,
                    state='cancelled' if confirmed else 'completion_unknown',
                    process=None if process_absent else identity,cleanup=cleanup,
                    signal_error=signal_error,termination_confirmed=confirmed,
                    termination_scope='Verified management process/container only; build daemon cleanup may need inspection')

            return record

    def _smoke(self, images, operation):
        receipts = {}
        script = "import hashlib,json; data=b'forsic synthetic runtime'; assert hashlib.sha256(data).hexdigest()=='" + _sha(b'forsic synthetic runtime') + "'; print(json.dumps({'functional_check':'sha256 synthetic bytes','ok':True}))"
        for role, image in images.items():
            name = 'forsic-env-' + _sha(str(self.root.resolve()).encode())[:12] + '-' + operation + '-' + role
            try:
                output = self._run(operation, ['docker', 'run', '--rm', '--name', name,
                    '--pull=never', '--network=none', '--read-only', '--cap-drop=ALL',
                    '--security-opt=no-new-privileges', '--memory=1g', '--pids-limit=64',
                    image['image_id'], 'python', '-B', '-c', script], timeout=120)
                receipts[role] = json.loads(output)
            finally:
                subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15, check=False)
        return receipts

    def _mount_smoke(self, images, operation, helper_directory):
        """Qualify exact preserved helper binds and an owning-scope work leaf."""
        from .image_process import bind_mount
        expected = {name: _file_sha(helper_directory / name) for name in HELPERS}
        script = """import hashlib,json,os,sys
from pathlib import Path
expected=json.loads(sys.argv[1])
for name,digest in expected.items():
    path=Path('/opt/forsic')/name
    assert os.statvfs(path).f_flag & os.ST_RDONLY, 'Helper bind is not read-only: '+name
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest, 'Helper bind content mismatch: '+name
path=Path('/work')/('mount-check-'+sys.argv[2])
try:
    with path.open('xb') as stream: stream.write(sys.argv[2].encode())
    assert path.read_bytes()==sys.argv[2].encode(), 'Workspace write/read mismatch'
finally:
    path.unlink(missing_ok=True)
print(json.dumps({'helper_sha256':expected,'helpers_readonly':True,'workspace_write_read':True}))
"""
        receipts = {}
        wanted = {'helper_sha256': expected, 'helpers_readonly': True, 'workspace_write_read': True}
        with tempfile.TemporaryDirectory(prefix='.mount-check-', dir=self.root) as temporary:
            for role, image in images.items():
                name = 'forsic-env-' + _sha(str(self.root.resolve()).encode())[:12] + '-' + operation + '-mount-' + role
                argv = ['docker', 'run', '--rm', '--name', name, '--pull=never', '--network=none',
                    '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                    '--user', f'{os.getuid()}:{os.getgid()}', '--memory=1g', '--pids-limit=64']
                for helper in HELPERS:
                    argv.extend(['--mount', bind_mount(helper_directory / helper, '/opt/forsic/' + helper, True)])
                argv.extend(['--mount', bind_mount(temporary, '/work', False), image['image_id'],
                             'python', '-B', '-c', script, json.dumps(expected), uuid.uuid4().hex])
                receipt = json.loads(self._run(operation, argv, timeout=120, output_limit=16384))
                if receipt != wanted:
                    raise ValueError('Preserved helper/workspace bind verification failed: ' + role)
                receipts[role] = receipt
        return receipts

    def _register(self, images, operation, *, plan=None, source_helpers=None, validation=None, lock_sha256=None):
        self._stage(operation, 'verification')
        for image in images.values():
            if image['platform'] != 'linux/amd64':
                raise ValueError('Platform not functionally qualified: ' + image['platform'])
        smoke = self._smoke(images, operation)
        helpers = source_helpers or {name: SOURCE / 'forsic_plugin' / name for name in HELPERS}
        helper_identity = {name: {'sha256': _file_sha(path), 'bytes': Path(path).stat().st_size}
                           for name, path in helpers.items()}
        identity = {'schema_version': SCHEMA, 'extension_api': API_VERSION, 'images': images,
            'helpers': helper_identity, 'created_at': time.time(), 'plan': plan or {},
            'checks': {'runtime': smoke, 'feature': validation or [],
                       'scope': 'synthetic runtime; package-specific checks are separate'},
            'app': {'version': next((line.split(':',1)[1].strip() for line in (SOURCE/'forsic_plugin/plugin.yaml').read_text().splitlines() if line.startswith('version:')),None),
                    'hermes_pin': next((line.split(':',1)[1].strip() for line in (SOURCE/'UPSTREAM.txt').read_text().splitlines() if line.startswith('Pinned commit:')),None),
                    'source_sha256': _file_sha(SOURCE / 'forsic_plugin' / '__init__.py'),
                    'restoration_scope': 'Docker images and mounted helper bytes; host/app not restored'},
            'host_utilities': _host_utilities(),
            'declared_lock_sha256': lock_sha256 or _file_sha(SOURCE / 'tools' / 'forensic-tools.lock.json')}
        environment_id = 'env-' + _sha(_json(identity))
        directory = self.root / 'environments' / environment_id
        created, published = False, False
        try:
            with _lock(self.root / '.lock'):
                directory.mkdir(parents=True, mode=0o700)
                created = True
                (directory / 'helpers').mkdir(mode=0o700)
                for name, path in helpers.items():
                    if name not in HELPERS:
                        raise ValueError('Unrecognized helper')
                    shutil.copyfile(path, directory / 'helpers' / name)
                    (directory / 'helpers' / name).chmod(0o400)
                    if _file_sha(directory / 'helpers' / name) != helper_identity[name]['sha256']:
                        raise ValueError('Preserved helper copy integrity mismatch: ' + name)
            self._stage(operation, 'mount_verification')
            mounts = self._mount_smoke(images, operation, directory / 'helpers')
            self._change_operation(operation, mount_checks=mounts,
                checks_scope='Exact preserved helper binds and disposable management workspace; future case workspace paths are not certified')
            self._stage(operation, 'registration')
            with _lock(self.root / '.lock'):
                if self.operation(operation).get('cancel_requested'):
                    raise InterruptedError('Management operation cancelled')
                manifest = {**identity, 'environment_id': environment_id}
                _write(directory / 'manifest.json', manifest)
                published = True
        finally:
            if created and not published:
                shutil.rmtree(directory)
        return manifest

    def register(self, images=None, *, operation_id, background=True):
        requested = images or {'analysis': 'forsic-analysis:1', 'parser': 'forsic-dissect:3.25.1'}
        if set(requested) != {'analysis', 'parser'}:
            raise ValueError('Both analysis and parser roles must be preserved')
        def work(op):
            images = {role: self._inspect(image) for role,image in requested.items()}
            self._change_operation(op,resolved_images=images)
            for image in images.values():
                if image['platform']!='linux/amd64':
                    raise ValueError('Platform not functionally qualified: '+image['platform'])
            self._stage(op,'installed_versions')
            installed = json.loads(self._run(op,_management_run(images['analysis']['image_id'],
                ['python','-B','-c',_INVENTORY]),timeout=120))
            return self._register(images,op,plan={'installed':installed},
                lock_sha256=installed.get('rules',{}).get('forensic-tools.lock.json',{}).get('sha256'))
        return self._start('register',requested,operation_id,work,background=background)

    def set_default(self, environment_id, *, expected_revision, operation_id):
        self.manifest(environment_id)
        _id(operation_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Expected revision must be a nonnegative integer')
        inputs={'environment_id':environment_id,'expected_revision':expected_revision}
        fingerprint=_sha(_json({'kind':'set_default','inputs':inputs}))
        with _lock(self.root / '.lock'):
            operation_path=self.root/'operations'/(operation_id+'.json')
            old=_read(operation_path)
            if old:
                if old['input_sha256'] != fingerprint:
                    raise ValueError('Operation ID reused with different inputs')
                return old['result']
            state = self._state()
            if state['revision'] != expected_revision:
                raise ValueError('Default selection revision conflict')
            state.update(revision=state['revision'] + 1, default_environment_id=environment_id,
                         operation_id=operation_id, changed_at=time.time())
            _write(self.root / 'state.json', state)
            _write(operation_path,{'schema_version':SCHEMA,'operation_id':operation_id,
                'kind':'set_default','inputs':inputs,'input_sha256':fingerprint,'state':'succeeded',
                'stage':'selection','created_at':time.time(),'result':state})
            return state

    def case_state(self, case):
        return _read(Path(case.output) / 'environment.json',
                     {'schema_version': SCHEMA, 'revision': 0, 'current': None, 'history': [], 'pending': None})

    def select_case(self, case, environment_id=None, *, extensions=None, expected_revision,
                    operation_id, reason='', sessions_safe=False):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Expected revision must be a nonnegative integer')
        if not isinstance(reason,str) or len(reason)>2048:
            raise ValueError('Selection reason exceeds limit')
        if environment_id is not None:
            self.manifest(environment_id)
        path = Path(case.output) / 'environment.json'
        request_sha256=_sha(_json({'environment_id':environment_id,'extensions':extensions,
            'expected_revision':expected_revision,'reason':reason}))
        _id(operation_id)
        operation_path=self.root/'operations'/(operation_id+'.json')
        operation_inputs={'case_identity':_sha(str(Path(case.output).resolve()).encode()),'request_sha256':request_sha256}
        operation_fingerprint=_sha(_json({'kind':'select_case','inputs':operation_inputs}))
        with _lock(path.with_suffix('.lock')):
            with _lock(self.root/'.lock'):
                old_operation=_read(operation_path)
                if old_operation and old_operation['input_sha256'] != operation_fingerprint:
                    raise ValueError('Operation ID reused with different inputs')
            state = self.case_state(case)
            if any(item.get('operation_id') == operation_id for item in [state.get('pending') or {}, *state['history']]):
                existing = next(item for item in [state.get('pending') or {}, *state['history']]
                                if item.get('operation_id') == operation_id)
                if existing.get('request_sha256') != request_sha256:
                    raise ValueError('Operation ID reused with different case selection')
                return state
            if state['revision'] != expected_revision or state.get('pending'):
                raise ValueError('Case selection revision conflict or selection already pending')
            current = state['current'] or {}
            if extensions is not None:
                from .extension_hub import normalize_selection
                extensions=normalize_selection(extensions,home=self.root.parent)
            selected = environment_id or current.get('environment_id') or self._state()['default_environment_id']
            if not selected:
                raise ValueError('No checked default environment; register an environment first')
            self.manifest(selected)
            state['pending'] = {'environment_id': selected,
                'extensions': copy.deepcopy(extensions if extensions is not None else current.get('extensions', {})),
                'operation_id': _id(operation_id), 'request_sha256':request_sha256, 'reason': reason, 'requested_at': time.time(),
                'legacy_history': 'not retroactively pinned' if not state['current'] else None,
                'boundary': 'all case sessions idle before next native turn'}
            _write(path, state)
            with _lock(self.root/'.lock'):
                _write(operation_path,{'schema_version':SCHEMA,'operation_id':operation_id,
                    'kind':'select_case','input_sha256':operation_fingerprint,'inputs':operation_inputs,
                    'state':'pending','stage':'safe_boundary','created_at':time.time(),
                    'result':{'case_revision':state['revision'],'selection':state['pending']}})
        return self.apply_pending(case, sessions_safe=sessions_safe)

    def apply_pending(self, case, *, sessions_safe=False):
        path = Path(case.output) / 'environment.json'
        if not callable(sessions_safe):
            return self.case_state(case)
        try:
            with _lock(path.with_suffix('.execution.lock'), nonblocking=True):
                with _lock(path.with_suffix('.lock')):
                    state = self.case_state(case)
                    if not sessions_safe():
                        return state
                    if state.get('pending'):
                        state['revision'] += 1
                        current = {**state['pending'], 'revision': state['revision'], 'applied_at': time.time()}
                        state.update(current=current, pending=None, history=[*state['history'], current])
                        _write(path, state)
                        operation_path=self.root/'operations'/(current['operation_id']+'.json')
                        if operation_path.exists():
                            self._change_operation(current['operation_id'],state='succeeded',stage='applied',
                                finished_at=time.time(),result={'case_revision':state['revision'],'selection':current})
                    return state
        except BlockingIOError:
            return self.case_state(case)

    def execution_snapshot(self, case):
        bound = _turn_snapshot.get()
        if bound and bound[0] == str(Path(case.output).resolve()):
            return copy.deepcopy(bound[1])
        state = self.case_state(case)
        if not state['current']:
            # Only the first future execution is pinned; past events stay untouched.
            if not self._state()['default_environment_id']:
                op = self.register(operation_id='bootstrap-' + uuid.uuid4().hex, background=False)
                if op['state'] != 'succeeded':
                    raise ValueError('Cannot preserve the installed runtime: ' + op.get('error', op['state']))
                try:
                    self.set_default(op['result']['environment_id'], expected_revision=0,
                                     operation_id='bootstrap-default-' + uuid.uuid4().hex)
                except ValueError:
                    if not self._state()['default_environment_id']:
                        raise
            try:
                from .extension_hub import selection_snapshot
                selected_extensions = selection_snapshot(home=self.root.parent).get('selection', {'packs': {}})
            except ImportError:
                selected_extensions = {'packs': {}}
            try:
                state = self.select_case(case, extensions=selected_extensions, expected_revision=0, operation_id='initial-' + uuid.uuid4().hex,
                                         reason='First managed execution; prior history remains unpinned', sessions_safe=lambda: True)
            except ValueError:
                state=self.case_state(case)
                if not state['current']:
                    raise
        if not state['current']:
            raise ValueError('First environment selection is pending a safe boundary')
        current = state['current']
        manifest = self.manifest(current['environment_id'])
        # A preserved ID disappearing must fail closed; never resolve its old tag.
        for role, image in manifest['images'].items():
            if self._inspect(image['image_id'])['image_id'] != image['image_id']:
                raise ValueError('Pinned image is unavailable: ' + role)
        directory = self.root / 'environments' / manifest['environment_id'] / 'helpers'
        return {'environment_id': manifest['environment_id'], 'case_revision': state['revision'],
            'images': manifest['images'], 'extensions': current['extensions'],
            'helpers': {name: {**value, 'path': str(directory / name)} for name, value in manifest['helpers'].items()},
            'app': manifest['app'],
            'runtime_app': {'source_sha256':_file_sha(SOURCE/'forsic_plugin/__init__.py'),
                            'shell_sha256':_file_sha(SOURCE/'forsic_plugin/shell_workspace.py'),
                            'scope':'Host orchestration source at this execution; not restored by environment selection'},
            'declared_lock_sha256': manifest['declared_lock_sha256'],
            'legacy_history': current.get('legacy_history')}

    def extension_selection(self, case):
        """Read the admitted menu selection without inspecting Docker for each entry."""
        bound=_turn_snapshot.get()
        if bound and bound[0]==str(Path(case.output).resolve()):
            return copy.deepcopy(bound[1]['extensions'])
        state=self.case_state(case)
        if state['current']:
            return copy.deepcopy(state['current']['extensions'])
        return self.execution_snapshot(case)['extensions']

    def extension_environment(self, case):
        """Read the bound environment's measured dependency versions for menu gating."""
        bound=_turn_snapshot.get()
        if bound and bound[0]==str(Path(case.output).resolve()):
            environment_id=bound[1]['environment_id']
        else:
            state=self.case_state(case)
            environment_id=(state.get('current') or {}).get('environment_id')
            if not environment_id:
                environment_id=self.execution_snapshot(case)['environment_id']
        return self.manifest(environment_id,verify=False)

    @contextmanager
    def execution(self, case, *, expected_snapshot=None):
        snapshot = expected_snapshot or self.execution_snapshot(case)
        with _lock(Path(case.output) / 'environment.execution.lock', shared=True):
            try:
                self.manifest(snapshot['environment_id'])
            except (ValueError,OSError) as exc:
                raise ExecutionFailure('Pinned environment files are unavailable or changed.',
                    error_code='environment_unavailable',failure_stage='admission',
                    environment_error=str(exc),execution={'state':'not_started','backend':'docker','termination_confirmed':True}) from exc
            # Acquire the same lock as apply_pending, then verify selection again.
            if self.case_state(case)['revision'] != snapshot['case_revision']:
                if expected_snapshot is not None:
                    raise ExecutionFailure('Case environment changed before execution admission; build a new call at the next native boundary.',
                        error_code='environment_revision_changed',failure_stage='admission',
                        execution={'state':'not_started','backend':'docker','termination_confirmed':True})
                snapshot = self.execution_snapshot(case)
            yield snapshot

    def inventory(self, environment_id, *, operation_id, background=True):
        manifest = self.manifest(environment_id)
        def work(op):
            self._stage(op, 'installed_versions')
            output = self._run(op, _management_run(manifest['images']['analysis']['image_id'],
                ['python', '-B', '-c', _INVENTORY]), timeout=120)
            return {'environment_id': environment_id, 'installed': json.loads(output),
                    'declared_lock': json.loads((SOURCE/'tools/forensic-tools.lock.json').read_text()),
                    'distinction': 'Installed measurements and declared lock are separate'}
        return self._start('inventory', {'environment_id': environment_id}, operation_id, work, background=background)

    def check_updates(self, environment_id, *, operation_id, background=True):
        manifest = self.manifest(environment_id)
        def work(op):
            self._stage(op, 'repository_query')
            # Disposable container, no mounts, no evidence, no host apt/pip.
            output = self._run(op, _management_run(manifest['images']['analysis']['image_id'],
                ['python', '-B', '-c', _UPDATES], network=True, readonly=False), timeout=600)
            result = json.loads(output)
            result.update(environment_id=environment_id, checked_at=time.time(),
                binary_lock=json.loads((SOURCE/'tools/forensic-tools.lock.json').read_text()))
            return result
        return self._start('check_updates', {'environment_id': environment_id}, operation_id, work, background=background)

    def plan_dependencies(self, base_environment_id, dependencies, *, checks=None):
        """Translate the hub's bounded apt/Python descriptors to this same builder."""
        self.manifest(base_environment_id)
        plan={'base_environment_id':base_environment_id,'apt':{},'python':[],'checks':checks or []}
        if not isinstance(dependencies,list) or len(dependencies)>128:
            raise ValueError('Extension dependencies must be a bounded list')
        seen=set()
        for item in dependencies:
            if not isinstance(item,dict) or item.get('method') not in ('apt','python'):
                raise ValueError('Only exact apt and hash-locked Python extension dependencies are supported')
            if not isinstance(item.get('name'),str) or not isinstance(item.get('version'),str):
                raise ValueError('Dependency name and exact version must be text')
            identity=(item['method'],item.get('name'))
            if identity in seen:
                raise ValueError('Duplicate extension dependency')
            seen.add(identity)
            if item['method']=='apt':
                if set(item)!={'method','name','version'}:
                    raise ValueError('apt dependency fields are method, name and version')
                plan['apt'][item['name']]=item['version']
            else:
                if set(item)!={'method','name','version','sha256'}:
                    raise ValueError('Python dependency fields are method, name, version and sha256')
                plan['python'].append({key:item[key] for key in ('name','version','sha256')})
        return _validate_plan(plan,require_checks=False) if checks is None else _validate_plan(plan)

    def prepare(self, plan, *, operation_id, background=True):
        """Build a derivative image by exact versions; no arbitrary host build scripts."""
        plan = _validate_plan(plan)
        base = self.manifest(plan['base_environment_id'])
        def work(op):
            directory = self.root / 'work' / op
            directory.mkdir(parents=True, mode=0o700)
            self._stage(op, 'plan')
            apt = plan.get('apt', {})
            requirements = plan.get('python', [])
            base_image = base['images']['analysis']['image_id']
            if plan.get('base_image'):
                self._stage(op,'base_image_download')
                self._run(op,['docker','image','pull','--platform=linux/amd64',plan['base_image']],timeout=1800)
                base_image=self._inspect(plan['base_image'])['image_id']
            base_tag = 'forsic-managed-base:' + uuid.uuid4().hex
            self._run(op, ['docker','image','tag',base_image,base_tag], timeout=30)
            lines = ['FROM ' + base_tag, 'USER root']
            if apt:
                pins = [name + '=' + version for name, version in sorted(apt.items())]
                # apt simulation is retained before installation; dependency changes
                # remain reviewable in the operation log and result manifest.
                lines += ['RUN apt-get update && apt-get -s install --no-install-recommends ' + ' '.join(pins)]
                lines += ['RUN DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ' +
                          ' '.join(pins) + ' && rm -rf /var/lib/apt/lists/*']
            if requirements:
                text = '\n'.join(item['name'] + '==' + item['version'] + ' ' +
                    ' '.join('--hash=sha256:' + sha for sha in item['sha256']) for item in requirements) + '\n'
                (directory/'requirements.txt').write_text(text)
                lines += ['COPY requirements.txt /tmp/forsic-requirements.txt',
                    'RUN python -m venv /opt/forsic-deps && /opt/forsic-deps/bin/python -m pip install --only-binary=:all: --require-hashes -r /tmp/forsic-requirements.txt']
            if plan.get('binary_lock'):
                # Reuse the existing checksummed fetch/install implementation,
                # executed only inside this disposable image build.
                lock = plan['binary_lock']
                tools = directory/'tools'
                tools.mkdir()
                shutil.copyfile(SOURCE/'tools/install_forensic_tools.py', tools/'install_forensic_tools.py')
                shutil.copytree(SOURCE/'tools/sensitive', tools/'sensitive')
                (tools/'forensic-tools.lock.json').write_bytes(_json(lock))
                lines += ['COPY tools /tmp/forsic-tools',
                    'RUN python /tmp/forsic-tools/install_forensic_tools.py fetch --cache /tmp/forsic-assets --arch amd64 && python /tmp/forsic-tools/install_forensic_tools.py install --cache /tmp/forsic-assets --prefix /opt/forensic-tools-candidate --arch amd64 && rm -rf /opt/forensic-tools && mv /opt/forensic-tools-candidate /opt/forensic-tools && rm -rf /tmp/forsic-assets']
            lines += ['WORKDIR /work', 'ENTRYPOINT []', 'CMD ["python", "--version"]']
            (directory/'Dockerfile').write_text('\n'.join(lines) + '\n')
            self._stage(op, 'build')
            iidfile = directory/'image-id'
            try:
                self._run(op, ['docker', 'build', '--pull=false', '--network=default', '--iidfile', str(iidfile),
                               '-f', str(directory/'Dockerfile'), str(directory)], timeout=3600)
            finally:
                subprocess.run(['docker','image','rm',base_tag],capture_output=True,timeout=30,check=False)
            candidate = self._inspect(iidfile.read_text().strip())
            self._stage(op, 'installed_versions')
            installed = json.loads(self._run(op, _management_run(candidate['image_id'],
                ['python', '-B', '-c', _INVENTORY]), timeout=120))
            for package, version in apt.items():
                if installed['apt'].get(package) != version:
                    raise ValueError('Requested apt version was not measured: ' + package)
            if requirements:
                actual = json.loads(self._run(op, _management_run(candidate['image_id'],
                    ['/opt/forsic-deps/bin/python', '-c', 'import importlib.metadata as m,json;print(json.dumps({d.metadata["Name"].lower().replace("_","-"):d.version for d in m.distributions()}))']), timeout=120))
                for package in requirements:
                    if actual.get(package['name'].lower().replace('_','-')) != package['version']:
                        raise ValueError('Requested isolated Python version was not measured: ' + package['name'])
                installed['isolated_python'] = actual
            checks = []
            self._stage(op, 'functional_checks')
            for check in plan.get('checks', []):
                output = self._run(op, _management_run(candidate['image_id'], check['argv']), timeout=120)
                if check['expected_stdout'] not in output.decode(errors='replace'):
                    raise ValueError('Functional check output did not match: ' + check['name'])
                checks.append({**check, 'state': 'passed', 'stdout_sha256': _sha(output)})
            images = {**base['images'], 'analysis': candidate}
            source_helpers = {name: self.root/'environments'/base['environment_id']/'helpers'/name for name in base['helpers']}
            result = self._register(images, op, plan={**plan, 'installed': installed},
                                    source_helpers=source_helpers, validation=checks,
                                    lock_sha256=installed.get('rules',{}).get('forensic-tools.lock.json',{}).get('sha256') or base['declared_lock_sha256'])
            return result
        return self._start('prepare', plan, operation_id, work, background=background)

    def export_environment(self, environment_id, destination, *, operation_id, background=True, extensions=None):
        manifest = self.manifest(environment_id)
        destination = Path(destination)
        def work(op):
            self._stage(op, 'image_save')
            if destination.exists():
                raise ValueError('Export destination already exists')
            with tempfile.TemporaryDirectory(dir=self.root/'work') as temporary:
                stage = Path(temporary)
                shutil.copytree(self.root/'environments'/environment_id, stage/'environment')
                images = sorted({entry['image_id'] for entry in manifest['images'].values()})
                self._run(op, ['docker', 'image', 'save', '-o', str(stage/'images.tar'), *images], timeout=3600)
                # Optional exact pack archives must be produced by the hub exporter;
                # never copy a whole profile or a builder workspace.
                if extensions:
                    (stage/'extensions').mkdir()
                    for name, archive in extensions.items():
                        _id(name)
                        archive = Path(archive)
                        if not archive.is_file() or archive.is_symlink():
                            raise ValueError('Extension export must be a hub archive')
                        shutil.copyfile(archive, stage/'extensions'/name)
                hashes = {str(p.relative_to(stage)): _file_sha(p) for p in stage.rglob('*') if p.is_file()}
                _write(stage/'bundle.json', {'schema_version': SCHEMA, 'environment_id': environment_id,
                    'files': hashes, 'extension_api': API_VERSION, 'platform': 'linux/amd64'})
                self._stage(op, 'archive')
                destination.parent.mkdir(parents=True, exist_ok=True)
                partial = destination.with_name(destination.name + '.' + op + '.partial')
                with tarfile.open(partial, 'w') as bundle:
                    for path in sorted(stage.rglob('*')):
                        if path.is_file():
                            bundle.add(path, arcname=str(path.relative_to(stage)), recursive=False)
                os.replace(partial, destination)
            return {'path': str(destination), 'sha256': _file_sha(destination), 'environment_id': environment_id}
        (self.root/'work').mkdir(parents=True, exist_ok=True, mode=0o700)
        return self._start('export', {'environment_id': environment_id, 'destination': str(destination),
                           'extensions': {name: {'path':str(path),'sha256':_file_sha(path)} for name, path in (extensions or {}).items()}},
                           operation_id, work, background=background)

    def import_environment(self, archive, *, operation_id, background=True):
        archive = Path(archive)
        archive_hash = _file_sha(archive)
        def work(op):
            self._stage(op, 'bundle_validation')
            (self.root/'work').mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(dir=self.root/'work') as temporary:
                stage = Path(temporary)
                _extract_bundle(archive, stage)
                if _file_sha(archive) != archive_hash:
                    raise ValueError('Offline archive changed during validation')
                bundle = _read(stage/'bundle.json')
                if bundle.get('schema_version') != SCHEMA or bundle.get('extension_api') != API_VERSION:
                    raise ValueError('Offline environment API incompatible')
                if bundle.get('platform') != 'linux/amd64':
                    raise ValueError('Offline environment platform not qualified')
                actual = {str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file()} - {'bundle.json'}
                if actual != set(bundle['files']):
                    raise ValueError('Offline environment file set mismatch')
                for name, digest in bundle['files'].items():
                    if _file_sha(stage/name) != digest:
                        raise ValueError('Offline environment checksum mismatch: ' + name)
                manifest = _read(stage/'environment/manifest.json')
                identity = {k:v for k,v in manifest.items() if k != 'environment_id'}
                environment_id = 'env-' + _sha(_json(identity))
                if environment_id != manifest['environment_id'] or environment_id != bundle['environment_id']:
                    raise ValueError('Offline environment manifest mismatch')
                _validate_components(manifest)
                if manifest.get('extension_api') != API_VERSION or set(manifest['helpers']) != set(HELPERS):
                    raise ValueError('Offline helpers or API incompatible')
                for name, value in manifest['helpers'].items():
                    if _file_sha(stage/'environment/helpers'/name) != value['sha256']:
                        raise ValueError('Offline helper checksum mismatch')
                self._stage(op, 'image_load')
                self._run(op, ['docker', 'image', 'load', '-i', str(stage/'images.tar')], timeout=3600)
                for image in manifest['images'].values():
                    if self._inspect(image['image_id']) != image:
                        # Save/load need not preserve repo digests. Identity and
                        # platform must match; digest metadata is advisory.
                        actual_image = self._inspect(image['image_id'])
                        if any(actual_image[key] != image[key] for key in ('image_id','platform')):
                            raise ValueError('Loaded image identity/platform mismatch')
                self._stage(op, 'functional_checks')
                checks = self._smoke(manifest['images'], op)
                for check in manifest['checks'].get('feature', []):
                    output = self._run(op, _management_run(manifest['images']['analysis']['image_id'], check['argv']), timeout=120)
                    if check['expected_stdout'] not in output.decode(errors='replace'):
                        raise ValueError('Imported functional check did not match')
                target = self.root/'environments'/environment_id
                with _lock(self.root/'.lock'):
                    if target.exists():
                        self.manifest(environment_id)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(stage/'environment', target)
                    extension_archives = []
                    for path in sorted((stage/'extensions').glob('*')):
                        retained = self.root/'offline-extensions'/environment_id/path.name
                        retained.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(path, retained)
                        extension_archives.append(str(retained))
                return {'environment_id': environment_id, 'checks': checks,
                    'extension_archives': extension_archives,
                    'extensions_state': 'retained; import through hub validator before activation' if extension_archives else 'none bundled',
                    'selection_changed': False}
        return self._start('import', {'archive_sha256': archive_hash,'archive':str(archive)}, operation_id, work, background=background)


    def retry(self, operation_id, *, new_operation_id, background=True):
        """A fresh bounded attempt with the same identity; no fictitious stage reuse."""
        previous = self.operation(operation_id)
        if previous['state'] in ('queued','running'):
            raise ValueError('A live operation cannot be retried')
        inputs = previous['inputs']
        kind = previous['kind']
        if kind == 'register':
            resolved=previous.get('resolved_images')
            requested={role:image['image_id'] for role,image in resolved.items()} if resolved else inputs
            return self.register(requested,operation_id=new_operation_id,background=background)
        if kind in ('inventory','check_updates'):
            return getattr(self,kind)(inputs['environment_id'],operation_id=new_operation_id,background=background)
        if kind == 'prepare':
            return self.prepare(inputs,operation_id=new_operation_id,background=background)
        if kind == 'import':
            if _file_sha(inputs['archive']) != inputs['archive_sha256']:
                raise ValueError('Retry archive identity changed')
            return self.import_environment(inputs['archive'],operation_id=new_operation_id,background=background)
        if kind == 'export':
            extensions = {}
            for name, item in inputs.get('extensions',{}).items():
                if _file_sha(item['path']) != item['sha256']:
                    raise ValueError('Retry extension identity changed')
                extensions[name]=item['path']
            return self.export_environment(inputs['environment_id'],inputs['destination'],
                extensions=extensions,operation_id=new_operation_id,background=background)
        raise ValueError('This operation must be retried with an explicit new request')


def _validate_components(manifest):
    if not isinstance(manifest.get('images'),dict) or set(manifest['images'])!={'analysis','parser'}:
        raise ValueError('Environment requires analysis and parser image roles')
    if not isinstance(manifest.get('helpers'),dict) or set(manifest['helpers'])!=set(HELPERS):
        raise ValueError('Environment helper set is incomplete')
    for image in manifest['images'].values():
        if not isinstance(image,dict) or not IMAGE_RE.fullmatch(image.get('image_id','')):
            raise ValueError('Environment requires immutable image IDs')
        if image.get('platform')!='linux/amd64' or image.get('os')!='linux' or image.get('architecture')!='amd64':
            raise ValueError('Environment platform is incompatible or unqualified')


def _host_utilities():
    result = {}
    for name in ('ewfinfo','ewfverify'):
        executable = shutil.which(name)
        if not executable:
            result[name]={'state':'not_installed','scope':'host utility; not restored'}
            continue
        try:
            probe=subprocess.run([executable,'-V'],capture_output=True,timeout=5,check=False)
            result[name]={'state':'observed','path':executable,'exit_code':probe.returncode,
                          'output':(probe.stdout+probe.stderr)[:4096].decode(errors='replace'),
                          'scope':'host utility; version recorded only, not restored'}
        except (OSError,subprocess.SubprocessError) as exc:
            result[name]={'state':'unavailable','error':type(exc).__name__,'scope':'host utility; not restored'}
    return result


def _management_run(image_id, argv, *, network=False, readonly=True, auto_remove=True):
    return ['docker', 'run', *(['--rm'] if auto_remove else []), '--pull=never', '--network=' + ('bridge' if network else 'none'),
            *(['--read-only'] if readonly else []), '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--memory=2g', '--pids-limit=128', image_id, *argv]


def _validate_plan(plan, *, require_checks=True):
    if not isinstance(plan, dict) or set(plan) - {'base_environment_id','apt','python','binary_lock','checks','base_image'}:
        raise ValueError('Unknown environment plan fields')
    _id(plan.get('base_environment_id'))
    if plan.get('base_image') and (not isinstance(plan['base_image'],str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}',plan['base_image'])):
        raise ValueError('Base image must be an exact repository digest')
    apt = plan.get('apt', {})
    if not isinstance(apt, dict) or len(apt) > 64:
        raise ValueError('Invalid apt selection')
    for name, version in apt.items():
        if not isinstance(name,str) or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', name) or not isinstance(version,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,160}', version):
            raise ValueError('apt dependencies need exact safe names and versions')
    packages = plan.get('python', [])
    if not isinstance(packages, list) or len(packages)>128:
        raise ValueError('Invalid Python dependency list')
    for item in packages:
        if not isinstance(item,dict) or set(item) != {'name','version','sha256'} or not isinstance(item['name'],str) or not isinstance(item['version'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', item['name']) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+!_-]{0,127}', item['version']):
            raise ValueError('Python dependencies need exact package versions')
        if not isinstance(item['sha256'],list) or not item['sha256'] or not all(isinstance(sha,str) and re.fullmatch(r'[0-9a-f]{64}', sha) for sha in item['sha256']):
            raise ValueError('Python dependencies need wheel SHA256 hashes')
    if plan.get('binary_lock'):
        lock = plan['binary_lock']
        if not isinstance(lock,dict) or lock.get('schema_version') != 1 or not isinstance(lock.get('packages'),dict) or set(lock.get('packages', {})) - {'hayabusa','yara-x','gitleaks'}:
            raise ValueError('Only current managed release binary families are supported')
        for package in lock['packages'].values():
            if not isinstance(package,dict) or not isinstance(package.get('assets'),dict):
                raise ValueError('Release package assets must be an object')
            asset = package.get('assets', {}).get('amd64', {})
            if not isinstance(asset,dict):
                raise ValueError('Release package platform asset must be an object')
            if not isinstance(asset.get('sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}', asset.get('sha256','')) or not isinstance(asset.get('url'),str) or not asset.get('url','').startswith('https://github.com/'):
                raise ValueError('Release asset needs fixed HTTPS source and SHA256')
    checks = plan.get('checks', [])
    if require_checks and (apt or packages or plan.get('binary_lock') or plan.get('base_image')) and not checks:
        raise ValueError('Changed packages require a small functional check with an expected result')
    if not isinstance(checks, list) or len(checks)>32:
        raise ValueError('Invalid functional checks')
    for check in checks:
        if not isinstance(check,dict) or set(check) != {'name','argv','expected_stdout'} or not isinstance(check['name'],str) or not isinstance(check['expected_stdout'],str) or not check['expected_stdout']:
            raise ValueError('Functional check needs a name, argv and expected stdout')
        if not isinstance(check['argv'], list) or not check['argv'] or len(check['argv'])>64 or not all(isinstance(v,str) and '\0' not in v and len(v)<65536 for v in check['argv']):
            raise ValueError('Functional check argv invalid')
        if any(flag in ('--help','--version','-h','-V') for flag in check['argv'][1:]):
            raise ValueError('Help/version alone is installation discovery, not a functional check')
    return copy.deepcopy(plan)


def _extract_bundle(archive, destination):
    total, names = 0, set()
    with tarfile.open(archive, 'r:*') as bundle:
        for entry in bundle:
            path = PurePosixPath(entry.name)
            if not entry.isfile() or path.is_absolute() or '..' in path.parts or str(path) != entry.name or entry.name in names:
                raise ValueError('Unsafe, duplicate or linked offline bundle entry')
            if len(names) >= MAX_FILES or entry.size < 0:
                raise ValueError('Offline bundle file count exceeds limit')
            total += entry.size
            if total > MAX_BUNDLE_BYTES:
                raise ValueError('Offline bundle expanded size exceeds limit')
            names.add(entry.name)
            target = destination / entry.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(entry) as src, target.open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024*1024)


_INVENTORY = r'''import hashlib,importlib.metadata as m,json,subprocess,pathlib,shutil
r={'apt':{},'python':{},'binaries':{},'rules':{},'platform':{}}
p=subprocess.run(['dpkg-query','-W','-f=${Package}\t${Version}\n'],capture_output=True)
if p.returncode==0:
 for line in p.stdout.decode().splitlines():
  if '\t' in line:
   name,version=line.split('\t',1); r['apt'][name]=version
r['python']={d.metadata['Name']:d.version for d in m.distributions()}
if pathlib.Path('/opt/forsic-deps/bin/python').is_file():
 p=subprocess.run(['/opt/forsic-deps/bin/python','-c','import importlib.metadata as m,json;print(json.dumps({d.metadata["Name"].lower().replace("_","-"):d.version for d in m.distributions()}))'],capture_output=True,timeout=20)
 if p.returncode==0:r['isolated_python']=json.loads(p.stdout)
 else:r['isolated_python_state']='probe_failed'
for name,flags in {'hayabusa':['--version'],'yr':['--version'],'gitleaks':['version'],'ewfexport':['-V'],'log2timeline.py':['--version'],'psort.py':['--version'],'photorec':['/version'],'testdisk':['/version']}.items():
 executable=shutil.which(name)
 if not executable:r['binaries'][name]={'state':'not_installed'};continue
 try:
  p=subprocess.run([executable,*flags],capture_output=True,timeout=10)
  r['binaries'][name]={'state':'observed','exit_code':p.returncode,'output':(p.stdout+p.stderr)[:2000].decode(errors='replace')}
 except Exception as e:r['binaries'][name]={'state':'failed','error':type(e).__name__}
for name in ('rule-config-sha256.json','forensic-tools.lock.json'):
 p=pathlib.Path('/opt/forensic-tools')/name
 if p.is_file():r['rules'][name]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
r['platform']['os_release']=pathlib.Path('/etc/os-release').read_text()
p=subprocess.run(['dpkg','--print-architecture'],capture_output=True);r['platform']['architecture']=p.stdout.decode().strip()
print(json.dumps(r))'''

_UPDATES = r'''import json,subprocess,urllib.request,importlib.metadata as m
r={'apt':{'state':'unavailable','packages':{}},'python':{'state':'unavailable','packages':{}},'release_binaries':{'state':'unavailable','packages':{}}}
p=subprocess.run(['apt-get','-o','APT::Update::Error-Mode=any','-o','APT::Sandbox::User=root','update'],capture_output=True,timeout=300)
if p.returncode==0:
 r['apt']['state']='checked'
 for name in ('libreoffice-writer-nogui','fonts-noto-cjk','poppler-utils','sleuthkit','testdisk','ewf-tools'):
  q=subprocess.run(['apt-cache','policy',name],capture_output=True)
  fields={}
  for line in q.stdout.decode().splitlines():
   if line.strip().startswith(('Installed:','Candidate:')):
    k,v=line.strip().split(':',1);fields[k.lower()]=v.strip()
  if fields.get('candidate') not in (None,'(none)'):
   simulation=subprocess.run(['apt-get','-s','install','--no-install-recommends',name+'='+fields['candidate']],capture_output=True)
   fields['dependency_plan']=simulation.stdout.decode(errors='replace')[-12000:]
   fields['dependency_plan_exit_code']=simulation.returncode
  r['apt']['packages'][name]=fields
else:r['apt'].update(state='failed_or_offline',error=p.stderr.decode(errors='replace')[-1500:])
for name in ('duckdb','python-docx','lxml','typing-extensions','dissect.target'):
 try:
  with urllib.request.urlopen('https://pypi.org/pypi/'+name+'/json',timeout=20) as response:data=json.load(response)
  files=[{'filename':f['filename'],'url':f['url'],'sha256':f['digests']['sha256'],'requires_python':f.get('requires_python')} for f in data.get('urls',[]) if f.get('packagetype')=='bdist_wheel']
  try:current=m.version(name)
  except m.PackageNotFoundError:current=None
  r['python']['packages'][name]={'state':'checked','current':current,'candidate':data['info']['version'],'wheels':files,'compatibility':'candidate only; build/platform checks required'}
 except Exception as e:r['python']['packages'][name]={'state':'failed_or_offline','error':str(e)}
r['python']['state']='checked' if all(p['state']=='checked' for p in r['python']['packages'].values()) else 'partial_or_offline'
for name,repo in {'hayabusa':'Yamato-Security/hayabusa','yara-x':'VirusTotal/yara-x','gitleaks':'gitleaks/gitleaks'}.items():
 try:
  request=urllib.request.Request('https://api.github.com/repos/'+repo+'/releases/latest',headers={'User-Agent':'Forsic-environment-manager'})
  with urllib.request.urlopen(request,timeout=20) as response:data=json.load(response)
  r['release_binaries']['packages'][name]={'state':'checked','candidate':data['tag_name'],'release':data['html_url'],'assets':[{'filename':a['name'],'url':a['browser_download_url'],'digest':a.get('digest'),'size':a['size']} for a in data['assets']],'compatibility':'candidate only; exact platform and verified checksum lock required'}
 except Exception as e:r['release_binaries']['packages'][name]={'state':'failed_or_offline','error':str(e)}
r['release_binaries']['state']='checked' if all(p['state']=='checked' for p in r['release_binaries']['packages'].values()) else 'partial_or_offline'
print(json.dumps(r))'''


@contextmanager
def bind_snapshot(case, snapshot):
    """Bind the already admitted native turn's schemas and execution to one revision."""
    token = _turn_snapshot.set((str(Path(case.output).resolve()), copy.deepcopy(snapshot)))
    try:
        yield snapshot
    finally:
        _turn_snapshot.reset(token)


def run_fixture(environment_id, pack_path, argv, fixture_path, expected, *, operation_id,
                timeout_seconds=120, max_output_bytes=1024*1024, background=True, manager=None):
    """Evidence-free builder check through the same bounded Docker executor."""
    from .image_process import bind_mount, run_container, _raw_output
    manager = manager or EnvironmentManager()
    manifest = manager.manifest(environment_id)
    pack, fixture = Path(pack_path).resolve(strict=True), Path(fixture_path).resolve(strict=True)
    if not pack.is_dir() or not fixture.is_dir():
        raise ValueError('Fixture and pack must be directories')
    if not isinstance(argv,list) or not argv or not all(isinstance(v,str) and '\0' not in v for v in argv):
        raise ValueError('Fixture requires an argv list')
    if not 1<=timeout_seconds<=300 or not 1<=max_output_bytes<=4*1024**2:
        raise ValueError('Fixture bounds exceeded')
    if not isinstance(expected,dict) or set(expected)-{'contains','json_fields'} or not expected:
        raise ValueError('Fixture requires contains or json_fields expected result')
    if 'contains' in expected and (not isinstance(expected['contains'],str) or not expected['contains']):
        raise ValueError('Fixture expected contains must be nonempty text')
    if 'json_fields' in expected and (not isinstance(expected['json_fields'],dict) or not expected['json_fields']):
        raise ValueError('Fixture expected json_fields must be a nonempty object')
    files = {}
    for prefix, directory in (('pack',pack),('fixture',fixture)):
        for path in directory.rglob('*'):
            if path.is_symlink():
                raise ValueError('Fixture/pack links are not accepted')
            if path.is_file():
                files[prefix+'/'+str(path.relative_to(directory))] = _file_sha(path)
    inputs = {'environment_id': environment_id, 'argv': argv, 'files': files, 'expected':expected,
              'timeout_seconds':timeout_seconds,'max_output_bytes':max_output_bytes}
    def work(op):
        manager._stage(op, 'functional_fixture')
        stage = manager.root/'work'/op
        workdir = stage/'fixture-work'
        workdir.mkdir(parents=True, mode=0o700)
        staged_pack, staged_inputs = stage/'pack', stage/'inputs'
        shutil.copytree(pack,staged_pack)
        shutil.copytree(fixture,staged_inputs)
        for prefix,directory in (('pack',staged_pack),('fixture',staged_inputs)):
            for path in directory.rglob('*'):
                if path.is_file():
                    if _file_sha(path) != files[prefix+'/'+str(path.relative_to(directory))]:
                        raise ValueError('Functional fixture source changed after planning')
                    path.chmod(0o444)
        # The invoking process identity plus exact named container are durable.
        # Cancellation is observed by the existing native executor loop.
        class Cancel:
            def interrupted(self):
                return manager.operation(op).get('cancel_requested',False)
        name = 'forsic-fixture-' + _sha(str(manager.root.resolve()).encode())[:12] + '-' + op
        record = manager.operation(op)
        record.update(process=None, container=name)
        _write(manager.root/'operations'/(op+'.json'), record)
        command = _management_run(manifest['images']['analysis']['image_id'], [], auto_remove=False)
        index = command.index(manifest['images']['analysis']['image_id'])
        command[index:index] = ['--name', name, '--user', f'{os.getuid()}:{os.getgid()}',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=128m', '--workdir','/work',
            '--mount',bind_mount(staged_pack,'/extension',True), '--mount',bind_mount(staged_inputs,'/inputs',True),
            '--mount',bind_mount(workdir,'/work',False)]
        command += argv
        result = run_container(command,name,Cancel(),timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,cleanup_on_return=True,retained_container=True,
            process_observer=lambda pid: manager._change_operation(op,process=_process_identity(pid)))
        manager._change_operation(op,process=None)
        stdout,stderr=result['stdout'],result['stderr']
        with (manager.root/'operations'/(op+'.log')).open('ab') as log:
            log.write(b'$ '+_json(argv)+b'\n'+stdout+b'\n'+stderr)
        passed = result['execution'].get('exit_code')==0 and result['execution'].get('termination_confirmed') is True
        if 'contains' in expected:
            passed = passed and expected['contains'] in stdout.decode(errors='replace')
        if 'json_fields' in expected:
            try:
                actual = json.loads(stdout)
                passed = passed and all(actual.get(key)==value for key,value in expected['json_fields'].items())
            except (ValueError,AttributeError):
                passed=False
        record = {'passed':passed,'environment_id':environment_id,
            'image_id':manifest['images']['analysis']['image_id'],'argv':argv,'fixture_identity':files,
            'execution':result['execution'],
            'raw_output':_raw_output(stdout,stderr,complete=True,limit=max_output_bytes),
            'stdout':stdout.decode(errors='replace'),
            'stderr':stderr.decode(errors='replace'),'stdout_sha256':_sha(stdout),'stderr_sha256':_sha(stderr)}
        if not passed:
            _write(manager.root/'operations'/(op+'.fixture.json'), record)
            raise ValueError('Functional fixture failed; retained receipt: '+op+'.fixture.json; '+stderr.decode(errors='replace')[-1500:])
        return record
    return manager._start('fixture',inputs,operation_id,work,background=background)
