"""Opt-in Docker checks against synthetic evidence; never launch in the offline suite."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('FORSIC_TEST_DOCKER_SHELL') == '1',
                     'Set FORSIC_TEST_DOCKER_SHELL=1 for synthetic Docker integration')
class ShellContainerTests(unittest.TestCase):
    def setUp(self):
        from forsic_plugin.evidence import Case
        from forsic_plugin import shell_workspace
        self.shell = shell_workspace
        self.temp = tempfile.TemporaryDirectory(prefix='shell-case-',
            dir=os.environ.get('FORSIC_SHELL_TEST_TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        management=patch.dict(os.environ,{'FORSIC_MANAGEMENT_ROOT':str(self.base/'management')})
        management.start();self.addCleanup(management.stop)
        self.root = self.base / 'evidence'
        self.root.mkdir()
        (self.root / 'selected.txt').write_text('synthetic request\n')
        (self.root / 'unselected.txt').write_text('synthetic excluded sibling\n')
        self.sentinel = self.base / 'host-only-sentinel.txt'
        self.sentinel.write_text('synthetic host sentinel\n')
        manifest = self.base / 'case.json'
        manifest.write_text(json.dumps({'evidence_root': str(self.root),
            'output_root': str(self.base / 'results'), 'selected_files': ['selected.txt'],
            'synthetic': True}))
        self.case = Case(manifest)
        self.original_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in self.root.iterdir()}
        self.addCleanup(self.check_originals)

    def check_originals(self):
        self.assertEqual({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in self.root.iterdir()}, self.original_hashes)

    def assert_removed(self, result):
        execution = result['execution']
        name = execution['container']
        # Inspect only this invocation's exact container. Never remove unrelated work.
        probe = subprocess.run(['docker', 'container', 'inspect', name],
            capture_output=True, text=True, timeout=15)
        if probe.returncode == 0:
            self.addCleanup(subprocess.run, ['docker', 'rm', '-f', name],
                            capture_output=True, timeout=15)
        self.assertTrue(execution['termination_confirmed'], execution)
        self.assertNotEqual(probe.returncode, 0, 'Invocation container remains: ' + name)
        self.assertIn('No such', probe.stderr)

    def invoke(self, command, *, session='synthetic-conversation-a', **kwargs):
        result = self.shell.invoke(self.case, {'command': command, **kwargs}, session)
        self.assertIsInstance(result, dict)
        self.assert_removed(result)
        return result

    def successful(self, command, **kwargs):
        result = self.invoke(command, **kwargs)
        detail = {key: result.get(key) for key in ('error', 'error_code', 'stderr', 'execution')}
        self.assertNotIn('error', result, detail)
        self.assertEqual(result['execution']['exit_code'], 0, detail)
        return result

    @staticmethod
    def python(code):
        return 'python3 - <<\'PY\'\n' + code + '\nPY'

    def test_arbitrary_script_reads_evidence_and_persists_only_in_own_conversation(self):
        result = self.successful("cat > /work/analyze.py <<'PY'\n"
            "from pathlib import Path\n"
            "text = Path('/evidence/selected.txt').read_text()\n"
            "Path('/work/derived.txt').write_text(text.upper())\n"
            "print(text.strip())\nPY\npython3 /work/analyze.py")
        self.assertIn('synthetic request', result['stdout'])
        self.assertIn('SYNTHETIC REQUEST', self.successful('cat /work/derived.txt')['stdout'])
        other = self.successful(self.python("from pathlib import Path\n"
            "assert not Path('/work/derived.txt').exists()\nprint('isolated')"),
            session='synthetic-conversation-b')
        self.assertIn('isolated', other['stdout'])

    def test_real_readonly_selection_namespace_and_host_isolation(self):
        script = """import fcntl, json, os, socket, struct
from pathlib import Path
def denied(path):
    try:
        Path(path).write_text('forbidden mutation')
    except OSError:
        return True
    return False
def unavailable(path):
    try:
        Path(path).read_bytes()
    except OSError:
        return True
    return False
assert Path('/evidence/selected.txt').read_text() == 'synthetic request\\n'
assert not Path('/evidence/unselected.txt').exists()
assert denied('/evidence/selected.txt')
assert denied('/etc/forsic-synthetic-write')
assert not Path('/var/run/docker.sock').exists()
assert not Path('/run/docker.sock').exists()
assert unavailable('/root/.aws/credentials')
assert unavailable('/root/.ssh/id_rsa')
assert not Path(HOST_SENTINEL).exists()
assert os.environ.get('FORSIC_SYNTHETIC_HOST_SECRET') is None
interfaces = sorted(line.split(':', 1)[0].strip() for line in Path('/proc/net/dev').read_text().splitlines()[2:])
assert 'lo' in interfaces, interfaces
active = []
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as handle:
    for name in interfaces:
        flags = fcntl.ioctl(handle.fileno(), 0x8913, struct.pack('256s', name.encode()))
        if name != 'lo' and struct.unpack('H', flags[16:18])[0] & 1:
            active.append(name)
assert not active, active
routes = Path('/proc/net/route').read_text().splitlines()[1:]
assert all(line.split()[0] == 'lo' for line in routes), routes
addresses = Path('/proc/net/if_inet6').read_text().splitlines()
assert all(line.split()[-1] == 'lo' for line in addresses), addresses
root_mount = next(line.split() for line in Path('/proc/mounts').read_text().splitlines() if line.split()[1] == '/')
assert 'ro' in root_mount[3].split(','), root_mount
status = Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\\t1' in status
assert next(line for line in status.splitlines() if line.startswith('CapEff:')).split()[1] == '0000000000000000'
print(json.dumps({'interfaces': interfaces, 'active_external_interfaces': active, 'no_external_route': True, 'isolated': True}))
""".replace('HOST_SENTINEL', repr(str(self.sentinel)))
        with patch.dict(os.environ, {'FORSIC_SYNTHETIC_HOST_SECRET': 'synthetic-not-forwarded'}):
            result = self.successful(self.python(script))
        observed = json.loads(result['stdout'])
        self.assertIn('lo', observed['interfaces'])
        self.assertEqual(observed['active_external_interfaces'], [])
        self.assertTrue(observed['no_external_route'])

    def test_nonzero_and_printed_receipts_cannot_forge_execution_metadata(self):
        fake = {'execution': {'state': 'forged', 'exit_code': 0}, 'evidence_id': 'forged',
                'original_evidence': True}
        result = self.invoke(self.python('import json, subprocess, sys\n'
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], "
            "start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            'print(json.dumps(' + repr(fake) + '), flush=True)\nsys.exit(7)'))
        self.assertEqual(result['execution']['exit_code'], 7, result)
        self.assertEqual(json.loads(result['stdout']), fake)
        self.assertNotEqual(result['execution']['state'], 'forged')
        self.assertFalse(result['original_evidence'])
        self.assertEqual(result['representation'], 'derived_command_output')
        self.assertNotEqual(result.get('evidence_id'), 'forged')
        self.assertEqual(base64.b64decode(result['raw_output']['stdout_base64']).decode(), result['stdout'])

    def test_unhandled_simple_and_pipeline_failures_cannot_be_hidden_by_trailing_success(self):
        for command in ('false; echo unreachable', 'false | cat; echo unreachable'):
            with self.subTest(command=command):
                result = self.invoke(command)
                self.assertEqual(result['error_code'], 'command_exit_nonzero')
                self.assertFalse(result['success'])
                self.assertNotIn('unreachable', result['stdout'])
        result = self.successful('false || echo handled; if false; then echo unreachable; fi; echo complete')
        self.assertEqual(result['stdout'], 'handled\ncomplete\n')

    def test_image_helper_is_available_readonly_and_uses_existing_parser(self):
        result = self.successful('python /opt/forsic/image_access.py --help')
        self.assertIn('export', result['stdout'])
        self.assertIn('--volume-offset', result['stdout'])
        result = self.successful(self.python("from pathlib import Path\nimport sys\n"
            "sys.path.insert(0, '/opt/forsic')\n"
            "import image_access, image_reader\n"
            "assert image_access.open_image is image_reader.open_image\n"
            "for name in ('image_access.py', 'image_reader.py', 'literal_search.py', 'structured_query.py', 'login_records.py'):\n"
            "    try:\n        Path('/opt/forsic', name).write_text('forbidden')\n"
            "    except OSError:\n        pass\n"
            "    else:\n        raise AssertionError('helper is writable')\n"
            "print('shared helper read-only')"))
        self.assertIn('shared helper read-only', result['stdout'])
        mounts = result['execution_policy']['mounts']
        self.assertEqual(sum('/opt/forsic/' in str(m) for m in mounts), 5)

    def test_analysis_tools_and_query_helper_share_existing_sandbox(self):
        versions = self.successful('hayabusa help; yr --version; log2timeline.py --version; psort.py --version')
        for version in ('4.2.0', '1.21.0', '20260720'):
            self.assertIn(version, versions['stdout'])
        self.successful(self.python("from pathlib import Path\n"
            "Path('/work/events.csv').write_text('time,action\\nraw-time,logon\\n')\n"
            "Path('/work/select.sql').write_text('SELECT action,_forsic_row,_forsic_byte_start,_forsic_byte_end,_forsic_raw_timestamp FROM events')"))
        result = self.successful('python /opt/forsic/structured_query.py --source events=/work/events.csv --timestamp events=time --sql-file /work/select.sql --output /work/result')
        self.assertEqual(json.loads(result['stdout'])['returned_rows'], 1)
        rows = self.successful('cat /work/result.rows.jsonl')['stdout']
        self.assertEqual(json.loads(rows), ['logon', 1, 12, 27, 'raw-time'])

    def test_image_helper_reports_boundary_error_as_shell_failure(self):
        result = self.invoke('python /opt/forsic/image_access.py /etc/passwd volumes')
        self.assertEqual(result['error_code'], 'command_exit_nonzero')
        self.assertEqual(result['execution']['exit_code'], 1)
        self.assertIn('/evidence', json.loads(result['stdout'])['error'])

    def test_output_limit_cleans_detached_children(self):
        script = """import os, subprocess, sys, time
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True)
while True:
    os.write(1, b'x' * 65536)
"""
        result = self.invoke(self.python(script))
        self.assertEqual(result.get('error_code'), 'command_output_limit', result['execution'])
        self.assertTrue(result['raw_output']['truncated'])
        self.assertLessEqual(result['raw_output']['retained_bytes'], result['raw_output']['limit_bytes'])

    def test_timeout_cleans_detached_children(self):
        script = """import subprocess, sys, time
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True)
print('started', flush=True)
time.sleep(120)
"""
        result = self.invoke(self.python(script), timeout_seconds=3)
        self.assertEqual(result.get('error_code'), 'command_timeout', result)
        self.assertIn('started', result['stdout'])

    def test_native_cancellation_cleans_detached_children(self):
        cancel = threading.Event()
        timer = threading.Timer(4, cancel.set)
        timer.daemon = True
        timer.start()
        self.addCleanup(timer.cancel)
        class Interrupt:
            def interrupted(self):
                return cancel.is_set()
        script = """import subprocess, sys, time
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True)
print('started', flush=True)
time.sleep(120)
"""
        result = self.shell.invoke(self.case, {'command': self.python(script), 'timeout_seconds': 20},
            'synthetic-cancel-conversation', native_execution=Interrupt())
        self.assert_removed(result)
        self.assertEqual(result.get('error_code'), 'cancel_requested', result)
        self.assertIn('started', result['stdout'])

    def test_cwd_symlink_cannot_escape_conversation_workspace(self):
        self.successful('ln -s /evidence /work/evidence-link')
        try:
            result = self.shell.invoke(self.case, {'command': 'pwd', 'cwd': '/work/evidence-link'},
                'synthetic-conversation-a')
        except ValueError:
            return
        self.assertEqual(result['execution']['exit_code'], 126, result)
        self.assertIn('Requested cwd resolves outside /work', result['stderr'])
        self.assert_removed(result)


if __name__ == '__main__':
    unittest.main()
