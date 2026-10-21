"""Case-bound derived analysis in the existing read-only Docker execution boundary."""
from contextlib import contextmanager
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess
import time
import uuid

from .execution_contract import ExecutionFailure
from .image_process import run_container, _raw_output, bind_mount

IMAGE = 'forsic-analysis:1'
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_COMMAND_BYTES = 64 * 1024
PREVIEW_BYTES = 6000
FILE_SIZE_LIMIT = 256 * 1024 * 1024
MAX_EVIDENCE_ENTRIES = 250000
MAX_EVIDENCE_DEPTH = 128
MAX_EVIDENCE_SCAN_SECONDS = 10

# Cwd containment is resolved in the actual container, after mounts exist. An
# arbitrary script can read /evidence, but cannot redirect its cwd through a
# workspace symlink into evidence or another container directory.
_CWD_EXEC = """import os,sys
cwd=os.path.realpath(sys.argv[1])
if os.path.commonpath(['/work',cwd])!='/work':
    sys.stderr.write('Requested cwd resolves outside /work\\n');sys.exit(126)
os.chdir(cwd)
os.execv('/bin/bash',['/bin/bash','-e','-o','pipefail','-c',sys.argv[2]])
"""


def schema():
    from .sensitive_policy import properties
    from .triage_policy import properties as triage_properties
    triage = triage_properties()
    shared = properties()
    # The shared target envelope serves both actions. Preserve sensitive-scan
    # fields/limits while exposing the profile-specific assertion to the model;
    # each action still applies its own stricter validator before any execution.
    shared['targets']['items']['properties']['triage_format'] = triage['targets']['items']['properties']['triage_format']
    shared['targets']['items']['anyOf']=[{'required':['source_handle']},{'required':['source_id','source_version']}]
    shared['targets']['items']['properties']['kind']['description']=('Required for triage: evidence_file or workspace_export. '
        'For sensitive_scan only, omitted kind defaults to retained_text.')
    shared['targets']['description'] += ' Each target needs source_handle or source_id plus source_version, with an exact retained pointer. For triage, kind is required (evidence_file or workspace_export); format=text and encoding=auto retain binary bytes. linux_persistence requires triage_format; linux_login_parser requires login_format.'
    return {'type':'object', 'properties':{
        **triage, **shared,
        'action':{'type':'string','enum':['run','capabilities','sensitive_scan','triage','triage_query'],'default':'run',
                  'description':'triage runs a bounded selected-file profile and returns candidates only. run requires command. capabilities checks installed tools. sensitive_scan runs selected local secrets/pii_kr detectors on retained targets; no command, network or online validation. Read scan-sensitive-data for selection/coverage.'},
        'command':{'type':'string','minLength':1,'maxLength':MAX_COMMAND_BYTES,
                   'description':'Bash -e -o pipefail; at most 64 KiB UTF-8. Handle expected failures with if/||. A Python here-doc consumes stdin: pass data via a quoted file argument, not a pipe into the here-doc. Retain producer stderr and status; a later command succeeding does not repair a failed producer.'},
        'cwd':{'type':'string','default':'/work',
               'description':'Absolute directory under /work; it must already exist. Symlink resolution must stay under /work.'},
        'timeout_seconds':{'type':'integer','minimum':1,'maximum':1800,'default':300}},
        'required':[], 'additionalProperties':False}


def validate(args):
    if isinstance(args,dict) and args.get('action')=='triage_query':
        from .triage_policy import validate_query
        return validate_query(args)
    if isinstance(args,dict) and args.get('action')=='triage':
        from .triage_policy import validate as validate_triage
        return validate_triage(args)
    if isinstance(args,dict) and args.get('action')=='sensitive_scan':
        from .sensitive_policy import validate as validate_scan
        return validate_scan(args)
    if not isinstance(args,dict) or set(args)-{'action','command','cwd','timeout_seconds'}:
        raise ValueError('Shell arguments are action, command, cwd and timeout_seconds only')
    action=args.get('action','run')
    if action not in ('run','capabilities'):
        raise ValueError('action must be run or capabilities')
    if action=='capabilities':
        if set(args)-{'action'}:
            raise ValueError('capabilities accepts action only; it does not accept commands or target paths')
        return {'action':'capabilities'}
    command=args.get('command')
    if not isinstance(command,str) or not command.strip() or '\0' in command:
        raise ValueError('command must be a nonempty string without NUL bytes')
    try:
        size=len(command.encode('utf-8'))
    except UnicodeEncodeError as exc:
        raise ValueError('command must contain valid UTF-8 text') from exc
    if size>MAX_COMMAND_BYTES:
        raise ValueError('command exceeds the 64 KiB UTF-8 limit')
    cwd=args.get('cwd','/work')
    if (not isinstance(cwd,str) or '\0' in cwd or len(cwd)>4096 or
            not cwd.startswith('/') or '..' in PurePosixPath(cwd).parts or
            PurePosixPath(cwd).parts[:2]!=('/','work')):
        raise ValueError('cwd must be an absolute directory under /work without parent traversal')
    timeout=args.get('timeout_seconds',300)
    if type(timeout) is not int or not 1<=timeout<=1800:
        raise ValueError('timeout_seconds must be an integer from 1 to 1800')
    return {'command':command,'cwd':str(PurePosixPath(cwd)),'timeout_seconds':timeout}



# Metadata/help only: no evidence paths, recovery, or host-runtime inspection.
_RECOVERY_DISCOVERY = """import hashlib,json,shutil,subprocess
from pathlib import Path
commands={
    'photorec':[['/version']], 'testdisk':[['/version']],
    'gitleaks':[['version']],
    'tsk_recover':[['-V']], 'ewfexport':[['-V']],
    'fsstat':[['-f','list']], 'img_stat':[['-i','list']],
    'fls':[], 'istat':[], 'icat':[], 'mmls':[]}
result={'runtime_verified':True,'verification_scope':'executable metadata/help and mounted helper hashes only',
        'target_format_support':'not_checked','recovery_execution':'not_attempted',
        'authorization':'not_assessed','executables':{},'helpers':{}}
for name in ('image_reader.py','image_access.py','literal_search.py','login_records.py','structured_query.py'):
    path=Path('/opt/forsic')/name
    try:
        payload=path.read_bytes()
        result['helpers'][name]={'state':'available','path':str(path),'bytes':len(payload),
                                 'sha256':hashlib.sha256(payload).hexdigest(),
                                 'target_execution':'not_attempted'}
    except OSError as exc:
        result['helpers'][name]={'state':'not_readable','error_type':type(exc).__name__}
for name,probes in commands.items():
    path=shutil.which(name)
    item={'state':'available' if path else 'missing_executable','path':path,'probes':[]}
    result['executables'][name]=item
    if not path:
        continue
    for flags in probes:
        probe={'argv':[path,*flags]}
        try:
            run=subprocess.run(probe['argv'],capture_output=True,timeout=3,check=False)
            probe.update(state='returned',exit_code=run.returncode,
                         stdout=run.stdout[:1600].decode('utf-8',errors='replace'),
                         stderr=run.stderr[:1600].decode('utf-8',errors='replace'),
                         output_truncated=len(run.stdout)>1600 or len(run.stderr)>1600)
        except (OSError,subprocess.SubprocessError) as exc:
            probe.update(state='probe_failed',error_type=type(exc).__name__)
        item['probes'].append(probe)
print(json.dumps(result,ensure_ascii=False))
"""


def workspace_info():
    """Static model-visible capability information; no creation or runtime probe."""
    from .triage_policy import catalog
    return {'triage':catalog(), 'tool':'forsic_shell', 'evidence_path':'/evidence', 'evidence_readonly':True,
            'workspace_path':'/work', 'workspace_scope':'current case and conversation',
            'workspace_persists_between_calls':True, 'network':False,
            'representation':'derived_command_output', 'original_evidence':False,
            'coverage_not_established':True, 'aggregate_workspace_quota_enforced':False,
            'file_size_limit_bytes':FILE_SIZE_LIMIT,
            'shell_contract':{'executable':'/bin/bash','options':['-e','-o','pipefail'],
                'meaning':'Unhandled simple-command and pipeline failures stop the shell. Bash exceptions and explicitly handled failures apply; exit 0 does not prove every internal operation succeeded.'},
            'image_access':{'help_command':'python /opt/forsic/image_access.py --help',
                'skill':'source-triage','reference':'references/image-access.md',
                'meaning':'Trusted helper files are mounted read-only per invocation; actual mounts are recorded in execution_policy.mounts.'},
            'structured_query':{'help_command':'python /opt/forsic/structured_query.py --help',
                'skill':'query-structured-logs', 'meaning':'Bounded CSV/JSONL SELECT with retained source hashes and byte/row coordinates; inputs and outputs stay under /work.'},
            'login_records':{'help_command':'python /opt/forsic/login_records.py --help',
                'skill':'inspect-artifacts', 'meaning':'Explicit Linux glibc record layout; raw type/time, byte offsets and partial-tail diagnostics. Derived output, not original evidence attestation.'},
            'sensitive_scan':{'action':'sensitive_scan','skill':'scan-sensitive-data',
                'profiles':['secrets','pii_kr'],'selection':'retained source/version/pointer only',
                'meaning':'Typed offline detector path; safe findings and local original report appendix. It verifies installed engines when invoked. Never print raw scanner results through a generic command.'},
            'declared_capabilities':{
                'source':'Dockerfile.analysis', 'image':IMAGE, 'runtime_verified':False,
                'meaning':'Build declarations only; installed image contents, optional plugins and APIs have not been verified by this result.',
                'python_base_image':'forsic-dissect:3.25.1', 'shell':'/bin/bash', 'python_command':'python',
                'base_image':'log2timeline/plaso@sha256:a8d42e64dcd3af2dffed8ddb25d34f34def69b672eec97ea9af62885c9332950',
                'python_packages':{'dissect.target':'3.25.1','dissect.xfs':'3.13','duckdb':'1.5.6',
                    'python-docx':'1.2.0','lxml':'6.1.3','typing-extensions':'4.15.0'},
                'command_versions':{'hayabusa':'4.2.0','yr':'1.21.0','log2timeline.py':'20260720','psort.py':'20260720',
                    'gitleaks':'8.30.1',
                    'libreoffice':'26.2.6.3','pdftotext':'26.01.0','pdftoppm':'26.01.0',
                    'photorec':'7.2','testdisk':'7.2','tsk_recover':'4.12.1','ewfexport':'20140816'},
                'deleted_recovery':{'skill':'source-triage','reference':'references/deleted-recovery.md',
                    'carving':'PhotoRec signature recovery from raw images or explicit exported ranges',
                    'filesystem':'Sleuth Kit metadata recovery on supported filesystems; XFS is not supported by this build',
                    'ewf':'TSK supports EWF; PhotoRec requires an ewfexport-derived raw copy in this build',
                    'meaning':'Optional derived recovery under /work. Check installed format support, selected range and file-size limit before execution. Availability does not establish recovery or coverage.'},
                'document_rendering':{'font':'Noto Sans CJK KR',
                    'meaning':'python-docx creates derived DOCX; headless LibreOffice Writer converts derived copies to PDF under /work. Use a temporary UserInstallation profile under /tmp. Native report.render remains the investigation-report path; do not install packages in the network-disabled shell.'},
                'discovery':{
                    'runtime_call':{'tool':'forsic_shell','arguments':{'action':'capabilities'}},
                    'runtime_meaning':'Observe executable presence, versions and format lists in the actual analysis container. Missing executable, failed probe and untested target support are distinct. Discovery neither attempts recovery nor assesses authorization.',
                    'package_metadata_command':
                        'python -c "from importlib.metadata import distribution; '
                        "d=distribution('dissect.target'); print(d.metadata['Name'], d.version); "
                        "print('\\n'.join(e.name for e in d.entry_points if e.group == 'console_scripts'))\"",
                    'cli':'Discover installed console scripts from package metadata, then use the relevant command --help before choosing flags. An entry point does not establish optional plugin support.',
                    'python_api':'Use help(), dir() and inspect.signature() on the installed module or object before choosing constructors, methods or keyword arguments. Do not infer APIs from the package name or version.'}},
            'meaning':'Arbitrary local scripts may read approved evidence and write analysis files under /work. Output is derived material; an aggregate workspace disk quota is not enforced.'}


def _private_directory(path):
    if path.is_symlink():
        raise ValueError('Shell workspace directory cannot be a symlink')
    path.mkdir(mode=0o700,exist_ok=True)
    if not path.is_dir() or path.stat().st_uid!=os.getuid():
        raise ValueError('Shell workspace must be an owned directory')


def _workspace(case,session_id):
    if not isinstance(session_id,str) or not session_id:
        raise ValueError('Shell workspace requires the owning native conversation')
    output=Path(case.output)
    # Case normally resolves output already. Reject substituted symlinks before
    # creating anything; only the final work leaf is ever mounted writable.
    for component in (output,*output.parents):
        if component.is_symlink():
            raise ValueError('Shell workspace parent cannot be a symlink')
    root=output/'shell-workspaces'
    _private_directory(root)
    directory=root/hashlib.sha256(session_id.encode('utf-8')).hexdigest()
    _private_directory(directory)
    return directory


@contextmanager
def _workspace_lock(workspace):
    # The lock is outside /work so an arbitrary command cannot replace it.
    lock_path=workspace.with_name(workspace.name+'.lock')
    descriptor=os.open(lock_path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:
            fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ExecutionFailure('Another command owns this conversation workspace.',
                error_code='workspace_busy',failure_stage='admission',
                execution={'state':'not_started','backend':'docker','termination_confirmed':True}) from exc
        yield
    finally:
        os.close(descriptor)


def _execution_snapshot(case):
    from .environments import EnvironmentManager
    try:
        return EnvironmentManager().execution_snapshot(case)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ExecutionFailure('The pinned environment is unavailable; no tag fallback or pull was attempted.',
            error_code='environment_unavailable', failure_stage='image_resolution', error_type=type(exc).__name__,
            environment_error=str(exc), execution={'state':'not_started','backend':'docker','termination_confirmed':True}) from exc


def _image_id(case=None):
    if case is not None:
        return _execution_snapshot(case)['images']['analysis']['image_id']
    try:
        result=subprocess.run(['docker','image','inspect','--format','{{json .Id}}',IMAGE],
            capture_output=True,timeout=15,check=False)
        image=json.loads(result.stdout) if result.returncode==0 else None
    except (OSError,subprocess.SubprocessError,ValueError) as exc:
        raise ExecutionFailure('The installed analysis image could not be inspected; no image was pulled.',
            error_code='analysis_image_unavailable',failure_stage='image_resolution',
            error_type=type(exc).__name__,
            execution={'state':'not_started','backend':'docker','termination_confirmed':True}) from exc
    if not isinstance(image,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',image):
        raise ExecutionFailure('The installed analysis image is unavailable; no image was pulled.',
            error_code='analysis_image_unavailable',failure_stage='image_resolution',
            execution={'state':'not_started','backend':'docker','termination_confirmed':True})
    return image


def _mount(source,destination,readonly):
    source=str(source)
    bind_mount(source,destination,readonly)
    return {'source':source,'destination':destination,'readonly':readonly}


def _check_evidence_tree(root):
    """Metadata only: readonly mounts do not disable live sockets or devices.

    No-follow directory descriptors prevent traversing evidence symlinks. Entry,
    depth and elapsed checks fail closed rather than mounting an unchecked tail.
    """
    started=time.monotonic()
    entries=1

    def check_budget(depth):
        if entries>MAX_EVIDENCE_ENTRIES or depth>MAX_EVIDENCE_DEPTH:
            raise ValueError('Evidence metadata inspection exceeded its entry or depth limit; no analysis container was launched')
        if time.monotonic()-started>MAX_EVIDENCE_SCAN_SECONDS:
            raise ValueError('Evidence metadata inspection exceeded its time budget; no analysis container was launched')

    def visit(descriptor,depth):
        nonlocal entries
        check_budget(depth)
        with os.scandir(descriptor) as listing:
            for entry in listing:
                entries+=1
                check_budget(depth)
                mode=entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    child=os.open(entry.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=descriptor)
                    try:
                        visit(child,depth+1)
                    finally:
                        os.close(child)
                elif not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                    raise ValueError('Evidence directory contains a socket, device, FIFO or other special node; it cannot be mounted into the analysis container')

    try:
        mode=Path(root).lstat().st_mode
        if stat.S_ISREG(mode):
            return
        if not stat.S_ISDIR(mode):
            raise ValueError('Evidence root must be a regular file or directory')
        descriptor=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            visit(descriptor,0)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ValueError('Evidence directory metadata could not be safely inspected; no analysis container was launched') from exc


def _evidence_mounts(case):
    selected=case.config.get('selected_files',[])
    if not selected:
        _check_evidence_tree(case.root)
        return [_mount(case.root,'/evidence',True)]
    mounts=[]
    for name in sorted(set(selected)):
        path=case.path(name)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('Selected shell evidence must name individual approved files')
        mounts.append(_mount(path,'/evidence/'+path.relative_to(case.root).as_posix(),True))
    return mounts


def _command(args,image,mounts,container):
    if os.getuid()==0:
        raise ValueError('The analysis shell requires a non-root host UID')
    command=['docker','run','--pull=never','--name',container,'--init',
        '--network','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--user',f'{os.getuid()}:{os.getgid()}',
        '--memory','8g','--memory-swap','8g','--cpus','2','--pids-limit','64',
        '--ulimit',f'fsize={FILE_SIZE_LIMIT}:{FILE_SIZE_LIMIT}',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=256m',
        '--env','HOME=/work','--env','TMPDIR=/tmp','--workdir','/work']
    for mount in mounts:
        spec=bind_mount(mount['source'],mount['destination'],mount['readonly'],
                        recursive_readonly=mount['readonly'])
        command.extend(['--mount',spec])
    command.extend([image,'python','-B','-c',_CWD_EXEC,args['cwd'],args['command']])
    return command


def _preview(raw):
    text=raw[:PREVIEW_BYTES].decode('utf-8',errors='replace')
    return text.encode('utf-8')[:PREVIEW_BYTES].decode('utf-8',errors='ignore')


def _literal_argv(command):
    """Recognize a conservative literal simple command, never split a program.

    Decline expansions, redirects, pipelines and control syntax even inside
    quotes. This intentionally leaves some valid searches uninterpreted.
    """
    if any(char in command for char in '\n\r;&|<>()$`*?[]{}~#'):
        return None
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _atomic_search(argv):
    if not argv or argv[0] not in ('grep','/bin/grep','/usr/bin/grep','rg','/usr/bin/rg'):
        return False
    # Only content-selection options whose status 1 means no selected match.
    # In particular --files, --type-list and grep -L are not content searches.
    long_options={'--fixed-strings','--line-number','--ignore-case','--recursive',
        '--text','--word-regexp','--line-regexp','--invert-match','--with-filename',
        '--no-filename','--count','--files-with-matches','--quiet','--no-messages'}
    pattern_argument=False
    for word in argv[1:]:
        if pattern_argument:
            pattern_argument=False
        elif word=='--':
            break
        elif word in ('-e','--regexp'):
            pattern_argument=True
        elif word.startswith('--') and word not in long_options:
            return False
        elif word.startswith('-') and (word=='-' or any(c not in 'EFGinrswxaIHhlcvq' for c in word[1:])):
            return False
    return len(argv)>1 and not pattern_argument


def _command_outcome(value, stdout):
    execution=value.get('execution',{})
    state,code=execution.get('state'),execution.get('exit_code')
    outcome={'status':'failed','basis':'process_status','individual_steps':'not_observed',
             'meaning':'Process status does not establish internal step execution or evidence coverage.'}
    if state=='completion_unknown' or execution.get('termination_confirmed') is False or (state=='returned' and
            (execution.get('termination_confirmed') is not True or code is None)):
        return {**outcome,'status':'completion_unknown',
                'hint':'Inspect the existing native execution record; do not automatically rerun the command.'}
    if state=='not_started':
        return {**outcome,'status':'not_started'}
    if state in ('cancelled','timed_out') or value.get('error_code') in ('command_timeout','command_output_limit','cancel_requested'):
        return {**outcome,'status':'interrupted'}
    if state!='returned':
        return outcome
    outcome['status']='returned' if code==0 else 'failed'
    argv=_literal_argv(value['command'])
    if code not in (None,0) and argv is None:
        outcome['hint']=('Bash -e -o pipefail may stop this script before later checks or recovery branches. '
                         'Internal steps were not observed; inspect stderr and existing files before retrying. '
                         'Use separate calls for independent checks, and handle expected search exit 1 inside scripts explicitly with if/else.')
        if code==141:
            outcome['hint']+=' Status 141 can indicate SIGPIPE from a producer when a consumer such as head closes early; it is not a no-match result.'
    if code==1 and _atomic_search(argv):
        return {**outcome,'status':'no_matches','basis':'atomic_search_exit_status',
                'meaning':'No selected matches for this literal grep/rg command and its options; not a conclusion about the case.'}
    if (not argv or len(argv)<2 or argv[0] not in ('python','python3','/usr/bin/python3') or
            argv[1] not in ('/opt/forsic/login_records.py','/opt/forsic/image_access.py',
                            '/opt/forsic/structured_query.py')):
        return outcome
    try:
        if argv[1].endswith('/login_records.py'):
            rows=[json.loads(line) for line in stdout.splitlines()]
            if (code in (2,3) and len(rows)>=2 and rows[0].get('schema')=='forsic.login-records.v1'
                    and rows[-1].get('kind')=='summary' and
                    rows[-1].get('status') in ('truncated','record_warnings','limited')):
                outcome.update(status='partial',basis='derived_helper_report')
        elif code==0:
            report=json.loads(stdout)
            if argv[1].endswith('/image_access.py'):
                if any(report.get(key) is False for key in ('complete','complete_file','complete_content','listing_complete','coverage_complete')):
                    outcome.update(status='partial',basis='derived_helper_report')
                elif report.get('matches')==[] and report.get('coverage_complete') is True:
                    outcome.update(status='no_matches',basis='derived_helper_report')
            elif report.get('result_truncated') is True or report.get('rejected_rows',0)>0:
                outcome.update(status='partial',basis='derived_helper_report')
    except (ValueError,TypeError,AttributeError):
        pass
    return outcome


def _present(value,raw):
    stdout,stderr=(base64.b64decode(raw[key]) for key in ('stdout_base64','stderr_base64'))
    raw.update(stdout_sha256=hashlib.sha256(stdout).hexdigest(),stderr_sha256=hashlib.sha256(stderr).hexdigest())
    stdout_preview,stderr_preview=_preview(stdout),_preview(stderr)
    return {**value,'command_outcome':_command_outcome(value,stdout),
        'raw_output':raw,'stdout':stdout_preview,'stderr':stderr_preview,
        'preview_truncated':{'stdout':stdout_preview!=stdout.decode('utf-8',errors='replace'),
                             'stderr':stderr_preview!=stderr.decode('utf-8',errors='replace')},
        'preview_encoding':'UTF-8 with replacement; raw_output retains exact captured bytes'}


def invoke(case,args,session_id,native_execution=None, *, _pack_root=None, _extension=None, max_output_bytes=MAX_OUTPUT_BYTES):
    args=validate(args)
    if args.get('action')=='sensitive_scan':
        from .sensitive_policy import invoke as scan
        return scan(case,args,session_id,native_execution)
    if args.get('action')=='triage_query':
        from .triage_policy import query
        return query(case,args,session_id)
    if args.get('action')=='triage':
        from .triage_policy import invoke as triage
        return triage(case,args,session_id,native_execution)
    discovery=args.get('action')=='capabilities'
    if discovery:
        args={'command':'python -c '+shlex.quote(_RECOVERY_DISCOVERY),'cwd':'/work','timeout_seconds':30}
    value={'representation':'derived_command_output','original_evidence':False,
        'coverage_not_established':True,'command':args['command'],'cwd':args['cwd'],
        'meaning':'Retained bytes emitted by this command, not direct original evidence or proof of its contents, execution history, or coverage.',
        'execution_policy':{**{key:value for key,value in workspace_info().items()
                               if key!='declared_capabilities'},
            'image':IMAGE,'timeout_seconds':args['timeout_seconds'],
            'limits':{'output_bytes':max_output_bytes,'file_size_bytes':FILE_SIZE_LIMIT,
                      'aggregate_workspace_quota_enforced':False,'memory_bytes':8*1024**3,'cpus':2,'pids':64},
            'environment_forwarded':False,'container_root_readonly':True}}
    if discovery:
        value['action']='capabilities'
        value['runtime_capabilities']={'runtime_verified':False,'state':'not_observed',
            'target_format_support':'not_checked','recovery_execution':'not_attempted',
            'authorization':'not_assessed'}
    raw=_raw_output(b'',b'',complete=False,limit=max_output_bytes)
    try:
        if native_execution and native_execution.interrupted():
            raise ExecutionFailure('Native cancellation was requested before command admission.',
                error_code='cancelled_before_start',failure_stage='admission',
                execution={'state':'not_started','backend':'docker','termination_confirmed':True})
        mounts=_evidence_mounts(case)
        snapshot=_execution_snapshot(case)
        image=snapshot['images']['analysis']['image_id']
        workspace=_workspace(case,session_id)
        mounts.append(_mount(workspace,'/work',False))
        for filename in ('image_reader.py','literal_search.py','image_access.py','structured_query.py','login_records.py'):
            mounts.append(_mount(Path(snapshot['helpers'][filename]['path']),'/opt/forsic/'+filename,True))
        if _pack_root is not None:
            mounts.append(_mount(Path(_pack_root).resolve(strict=True),'/extension',True))
        value['execution_policy']['environment']=snapshot
        if _extension is not None:
            value['execution_policy']['extension']=_extension
        value['execution_policy'].update(image_id=image,mounts=mounts,
            workspace_scope_hash=hashlib.sha256(session_id.encode('utf-8')).hexdigest())
        container='forsic-shell-'+uuid.uuid4().hex
        command=_command(args,image,mounts,container)
        from .environments import EnvironmentManager
        with _workspace_lock(workspace), EnvironmentManager().execution(case,expected_snapshot=snapshot):
            result=run_container(command,container,native_execution,
                timeout_seconds=args['timeout_seconds'],max_output_bytes=max_output_bytes,
                cleanup_on_return=True,retained_container=True)
        value['execution']=result['execution']
        raw=_raw_output(result['stdout'],result['stderr'],complete=True,limit=max_output_bytes)
        if value['execution']['state']=='completion_unknown':
            value.update(error='Command returned, but container cleanup was not confirmed.',
                         error_code='command_cleanup_unknown',failure_stage='cleanup')
        elif (value['execution'].get('exit_code') not in (None,0) and
                _command_outcome(value,result['stdout'])['status'] not in ('no_matches','partial')):
            value.update(success=False,error=f"Command exited with status {value['execution']['exit_code']}.",
                         error_code='command_exit_nonzero',failure_stage='execution')
        elif discovery and value['execution'].get('exit_code')==0:
            try:
                observed=json.loads(result['stdout'])
                if not isinstance(observed,dict) or not isinstance(observed.get('executables'),dict):
                    raise ValueError('Discovery did not return executable metadata')
                value['runtime_capabilities']={**observed,'state':'observed'}
            except (ValueError,UnicodeError):
                value.update(error='Capability discovery returned invalid metadata.',
                    error_code='capability_discovery_invalid',failure_stage='discovery')
    except ExecutionFailure as exc:
        value.update(error=str(exc),**exc.details)
        raw=value.pop('raw_output',raw)
    except KeyboardInterrupt as exc:
        value.update(error='Native execution interrupted',error_code='cancel_requested',failure_stage='execution',
            execution=getattr(exc,'forsic_execution',{'state':'completion_unknown'}))
        exc.forsic_shell_result=_present(value,getattr(exc,'forsic_raw_output',raw))
        raise
    return _present(value,raw)


def invoke_extension(case,session_id,argv,pack_root,timeout_seconds=300,native_execution=None,
                     extension=None,max_output_bytes=MAX_OUTPUT_BYTES):
    """Immutable hub adapters use the existing shell boundary and case workspace."""
    if not isinstance(argv,list) or not argv or not all(isinstance(v,str) and '\0' not in v for v in argv):
        raise ValueError('Extension execution requires an argv list')
    if type(max_output_bytes) is not int or not 1<=max_output_bytes<=MAX_OUTPUT_BYTES:
        raise ValueError('Extension output bound exceeds shell limit')
    return invoke(case,{'command':shlex.join(argv),'cwd':'/work','timeout_seconds':timeout_seconds},
                  session_id,native_execution,_pack_root=pack_root,_extension=extension,
                  max_output_bytes=max_output_bytes)
