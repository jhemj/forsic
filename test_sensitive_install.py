"""Supply-chain tests; optional real candidate smoke is synthetic and offline."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("sensitive_installer", ROOT / "tools/install_forensic_tools.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class SensitiveInstallTest(unittest.TestCase):
    def test_trusted_configuration_integrity(self):
        lock = json.loads(installer.LOCK.read_text())
        installer.check_trusted_sensitive(lock)
        altered = json.loads(json.dumps(lock))
        altered['sensitive_python']['trusted_files']['gitleaks.toml'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'configuration mismatch'):
            installer.check_trusted_sensitive(altered)

    def test_offline_install_verifies_extracted_binary_before_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def asset(path):
                return {'filename': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            hay = root / 'hay.zip'
            with zipfile.ZipFile(hay, 'w') as archive:
                archive.writestr('hayabusa-fixture', 'synthetic')
            packages = {'hayabusa': {'assets': {'amd64': asset(hay)}}}
            for package, binary in [('yara-x', 'yr'), ('gitleaks', 'gitleaks')]:
                path = root / (package + '.tar.gz')
                with tarfile.open(path, 'w:gz') as archive:
                    member = tarfile.TarInfo(binary)
                    member.size = 9
                    archive.addfile(member, io.BytesIO(b'synthetic'))
                pinned = asset(path)
                pinned['binary_sha256'] = hashlib.sha256(b'synthetic').hexdigest()
                packages[package] = {'assets': {'amd64': pinned}}
            lock = {'packages': packages, 'licenses': []}
            with patch.object(installer.urllib.request, 'urlopen', side_effect=AssertionError('network forbidden')):
                installer.install(lock, root, root / 'good', 'amd64')
                self.assertTrue(os.access(root / 'good/bin/gitleaks', os.X_OK))
                lock['packages']['gitleaks']['assets']['amd64']['binary_sha256'] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'binary SHA256 mismatch'):
                    installer.install(lock, root, root / 'bad', 'amd64')
            self.assertFalse((root / 'bad').exists())

    @unittest.skipUnless(os.environ.get('FORSIC_SENSITIVE_IMAGE'), 'set candidate image to run real isolated synthetic scanner smoke')
    def test_real_candidate_synthetic_offline(self):
        # Any socket/connect/send attempt kills the process, rather than merely
        # blocking successful traffic. Mounts contain no evidence or case state.
        profile = {'defaultAction': 'SCMP_ACT_ALLOW', 'syscalls': [{
            'names': ['socket', 'socketpair', 'connect', 'sendto', 'sendmsg', 'sendmmsg'],
            'action': 'SCMP_ACT_KILL_PROCESS'}]}
        script = r'''
import importlib.util,json,resource,subprocess,sys,time
p='/opt/forensic-tools/sensitive/presidio_local.py'
s=importlib.util.spec_from_file_location('local',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
start=time.monotonic();recognizers=m.load_recognizers()
assert not any(n == 'spacy' or n.startswith('spacy.') or n == 'presidio_analyzer.analyzer_engine' for n in sys.modules)
assert set(recognizers)=={'KR_RRN','KR_FRN','KR_PASSPORT','KR_DRIVER_LICENSE','KR_BRN','CREDIT_CARD','EMAIL_ADDRESS','PHONE_NUMBER'}
text='연락처=010-2345-6789 이메일=synthetic.person@example.invalid 카드=4111-1111-1111-1111 여권=M123A4567 운전면허=11-20-123456-78 주민번호=900101-1234567 외국인번호=900101-5234567 사업자=123-45-67891'
counts={}
for entity,r in recognizers.items():
 counts[entity]=len(r.analyze(text=text,entities=[entity],nlp_artifacts=None))
for entity in recognizers: assert counts[entity]>0,entity
assert recognizers['KR_RRN'].validate_result('900101-1234567') is None
cli='/opt/forensic-tools/bin/gitleaks'
assert subprocess.check_output([cli,'version'],text=True).strip()=='8.30.1'
helptext=subprocess.check_output([cli,'stdin','--help'],text=True)
for flag in ['--ignore-gitleaks-allow','--gitleaks-ignore-path','--max-decode-depth','--max-archive-depth']: assert flag in helptext
# Evidence-controlled config and ignore material cannot suppress stdin detection.
from pathlib import Path
Path('/tmp/.gitleaks.toml').write_text('invalid untrusted config')
Path('/tmp/.gitleaksignore').write_text('stdin:github-pat:1')
Path('/tmp/trusted-cwd').mkdir()
token='ghp_'+'7Kp9xR2vL8mQ4zB6tN3yH5wC1dF0sJ9aU2eV'
args=[cli,'stdin','--config','/opt/forensic-tools/sensitive/gitleaks.toml','--gitleaks-ignore-path','/opt/forensic-tools/sensitive/empty.gitleaksignore','--ignore-gitleaks-allow','--max-archive-depth','0','--max-decode-depth','0','--no-banner','--no-color','--log-level','error','--report-format','json','--report-path','/tmp/findings.json','--exit-code','0']
result=subprocess.run(args,input='한국어 token='+token+' # gitleaks:allow\n',text=True,capture_output=True,cwd='/tmp/trusted-cwd')
assert result.returncode==0,result.returncode
findings=json.loads(Path('/tmp/findings.json').read_text())
assert any(x['Secret']==token for x in findings), 'synthetic inline-allow token not found'
print(json.dumps({'versions':{'gitleaks':'8.30.1','presidio':'2.2.364'},'recognizers':counts,'secret_hits':len(findings),'cold_seconds':round(time.monotonic()-start,4),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'network_syscalls':'kill_on_attempt; none observed','nlp_modules_loaded':False}))
'''
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'no-network.json'
            path.write_text(json.dumps(profile))
            result = subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'none',
                '--read-only', '--memory', '8g', '--cpus', '2', '--pids-limit', '64',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--security-opt', 'seccomp=' + str(path), '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
                os.environ['FORSIC_SENSITIVE_IMAGE'], 'python', '-'], input=script,
                text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
