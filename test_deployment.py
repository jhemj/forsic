"""Deployment contracts in temporary directories; no model/network/service work."""
import hashlib
from contextlib import closing
import json
import os
import runpy
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
import types
from unittest.mock import patch

import deploy_forsic as deploy


class DeploymentTests(unittest.TestCase):
    def test_powershell_bytes_survive_git_publication_with_release_attributes(self):
        # Git's text/eol conversion must not invalidate exported-source hashes.
        with tempfile.TemporaryDirectory(prefix='forsic-git-release-', dir='/tmp') as temp:
            root = Path(temp)
            repository, checkout = root / 'source', root / 'checkout'
            repository.mkdir()
            attributes = Path(__file__).parent / 'upstream/hermes/.gitattributes'
            (repository / '.gitattributes').write_bytes(attributes.read_bytes())
            script = b'Write-Output "synthetic release fixture"\r\n'
            (repository / 'sample.ps1').write_bytes(script)
            def git(*args):
                return subprocess.run(['git', '-c', 'user.name=Release fixture',
                    '-c', 'user.email=fixture@example.invalid', *args], cwd=repository,
                    check=True, capture_output=True)
            git('init', '-q')
            git('add', '.')
            git('commit', '-qm', 'Synthetic byte-preservation fixture')
            self.assertEqual(git('show', 'HEAD:sample.ps1').stdout, script)
            git('clone', '-q', '--no-hardlinks', str(repository), str(checkout))
            self.assertEqual((checkout / 'sample.ps1').read_bytes(), script)

    def setUp(self):
        # A selected evidence fixture cannot live inside the native test runtime's
        # redirected ~/.hermes scratch directory: that is correctly private.
        self.temp = tempfile.TemporaryDirectory(prefix='forsic-deploy-test-', dir='/tmp')
        self.root = Path(self.temp.name) / 'relocated installation'
        self.root.mkdir()
        files = [*deploy.ROOT_FILES, *deploy.REQUIRED_PUBLIC,
                 'forsic_plugin/plugin.yaml', 'upstream/hermes/pm/lock.json']
        files += ['config/' + name for name in deploy.CONFIG_FILES]
        files += ['cases/smoke/evidence/' + name for name in deploy.FIXTURES]
        for name in files:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('synthetic fixture\n')

    def tearDown(self):
        self.temp.cleanup()

    def init(self):
        return deploy.initialize(self.root, 'http://127.0.0.1:11434/v1', 'qwen3.8:latest', Path('/usr/bin/python3'))

    def test_initialize_private_portable_and_non_running(self):
        result = self.init()
        self.assertFalse(result['service_started'])
        self.assertEqual(result['model_requests'], 0)
        home = self.root / 'state/hermes'
        config = json.loads((home / 'config.yaml').read_text())
        self.assertEqual(config['fallback_model'], {})
        self.assertEqual(config['platform_toolsets']['telegram'], ['forsic'])
        self.assertEqual((home / 'config.yaml').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / 'state').stat().st_mode & 0o777, 0o700)
        self.assertEqual((home / 'plugins/forsic').resolve(), self.root / 'forsic_plugin')
        self.assertFalse(Path(os.readlink(home / 'plugins/forsic')).is_absolute())
        manifest = Path(config['plugins']['entries']['forsic']['settings']['case_file'])
        self.assertTrue(json.loads(manifest.read_text())['synthetic'])
        unit = (self.root / 'state/deployment/forsic-web.service').read_text()
        self.assertIn('Restart=no', unit)
        self.assertIn('"' + str(self.root / 'run.py') + '"', unit)
        self.assertNotIn('TELEGRAM_BOT_TOKEN', unit)
        self.assertFalse((home / '.env').exists())
        self.assertNotIn('evidence_directory', config['plugins']['entries']['forsic']['settings'])

    def test_generic_skill_sources_are_not_shipped(self):
        for name in ('skills/creative/music/SKILL.md', 'optional-skills/dev/SKILL.md'):
            self.assertTrue(deploy.upstream_omission(name, set()))
        self.assertEqual(deploy.upstream_omission('skills/creative/LICENSE', set()), '')

    def test_launcher_uses_empty_native_skill_seed_and_keeps_plugin_registration(self):
        # Execute the launcher with a fake package manager; never start a service.
        pm = types.ModuleType('pm')
        environments = types.ModuleType('pm.environments')
        environments.project_python = lambda _: Path('/synthetic/python')
        pm.env_for = lambda _: dict(os.environ)
        launcher = Path(__file__).resolve().with_name('run.py')
        with patch.dict(sys.modules, {'pm': pm, 'pm.environments': environments}), \
                patch.dict(os.environ), patch.object(sys, 'argv', ['run.py', '--help']), \
                patch.object(sys, 'path', list(sys.path)), patch('os.chdir'), patch('os.execve') as execute:
            runpy.run_path(str(launcher), run_name='__main__')
        env = execute.call_args.args[2]
        seed = Path(env['HERMES_BUNDLED_SKILLS'])
        self.assertEqual(seed, launcher.parent / 'config/bundled-skills')
        self.assertEqual(env['HERMES_OPTIONAL_SKILLS'], str(seed))
        self.assertTrue((seed / 'README.txt').is_file())
        self.assertFalse(list(seed.rglob('SKILL.md')))
        self.assertTrue((launcher.parent / 'forsic_plugin/skills/forensic-investigation/SKILL.md').is_file())

    def test_initialize_registers_evidence_without_reading_or_starting_analysis(self):
        evidence = Path(self.temp.name) / 'selected evidence'
        evidence.mkdir()
        original = evidence / 'synthetic.txt'
        original.write_text('Synthetic evidence; preserve these bytes.\n')
        before = original.stat()
        with patch('os.scandir', side_effect=AssertionError('must not scan evidence')), \
                patch('subprocess.run', side_effect=AssertionError('must not start analysis')), \
                patch('subprocess.Popen', side_effect=AssertionError('must not start containers')), \
                patch('hashlib.sha256', side_effect=AssertionError('must not hash evidence')):
            result = deploy.initialize(self.root, 'http://127.0.0.1:11434/v1', 'qwen',
                                       Path('/usr/bin/python3'), evidence_dir=evidence)
        home = self.root / 'state/hermes'
        config = json.loads((home / 'config.yaml').read_text())
        settings = config['plugins']['entries']['forsic']['settings']
        self.assertEqual(settings['evidence_directory'], str(evidence))
        self.assertEqual((home / 'config.yaml').stat().st_mode & 0o777, 0o600)
        self.assertTrue(json.loads(Path(settings['case_file']).read_text())['synthetic'])
        self.assertFalse((self.root / 'state/intake').exists())
        self.assertFalse(result['service_started'])
        self.assertEqual(original.read_text(), 'Synthetic evidence; preserve these bytes.\n')
        self.assertEqual(original.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(sorted(path.name for path in evidence.iterdir()), ['synthetic.txt'])

    def test_invalid_evidence_directory_leaves_no_state(self):
        evidence = Path(self.temp.name) / 'selected evidence'
        evidence.mkdir()
        file = evidence / 'synthetic.txt'
        file.write_text('Synthetic fixture\n')
        link = Path(self.temp.name) / 'linked evidence'
        link.symlink_to(evidence, target_is_directory=True)
        nested = evidence / 'nested'
        nested.mkdir()
        for invalid in ('relative/evidence', evidence / 'missing', file, link, link / 'nested',
                        self.root, self.root / 'config', Path(self.temp.name), '/etc', '/',
                        '//' + str(self.root).lstrip('/')):
            with self.subTest(path=str(invalid)):
                with self.assertRaises((ValueError, OSError)):
                    deploy.initialize(self.root, 'http://127.0.0.1:11434/v1', 'qwen',
                                      Path('/usr/bin/python3'), evidence_dir=invalid)
                self.assertFalse((self.root / 'state').exists())

    @unittest.skipIf(os.geteuid() == 0, 'Root can open otherwise unreadable directories')
    def test_unreadable_evidence_directory_leaves_no_state(self):
        evidence = Path(self.temp.name) / 'unreadable evidence'
        evidence.mkdir(mode=0)
        try:
            with self.assertRaises(PermissionError):
                deploy.initialize(self.root, 'http://127.0.0.1:11434/v1', 'qwen',
                                  Path('/usr/bin/python3'), evidence_dir=evidence)
            self.assertFalse((self.root / 'state').exists())
        finally:
            evidence.chmod(0o700)

    def test_init_cli_passes_explicit_host_evidence_directory(self):
        evidence = Path(self.temp.name) / 'selected evidence'
        evidence.mkdir()
        args = ['deploy_forsic.py', '--root', str(self.root), 'init', '--evidence-dir', str(evidence)]
        with patch.object(sys, 'argv', args), patch('builtins.print'):
            self.assertEqual(deploy.main(), 0)
        config = json.loads((self.root / 'state/hermes/config.yaml').read_text())
        self.assertEqual(config['plugins']['entries']['forsic']['settings']['evidence_directory'], str(evidence))

    def test_no_overwrite_or_incomplete_partial_state(self):
        self.init()
        config = self.root / 'state/hermes/config.yaml'
        before = config.read_bytes()
        with self.assertRaisesRegex(ValueError, 'never overwritten'):
            self.init()
        self.assertEqual(config.read_bytes(), before)
        other = self.root / 'incomplete'
        other.mkdir()
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            deploy.initialize(other, 'http://127.0.0.1:11434/v1', 'qwen', Path('/usr/bin/python3'))
        self.assertFalse((other / 'state').exists())

    def test_no_external_url_credentials_or_service_injection(self):
        for value in ('https://public.example/v1', 'http://8.8.8.8/v1', 'http://0.0.0.0/v1',
                      'http://user:secret@localhost/v1', 'http://localhost/v1?key=secret'):
            with self.assertRaises(ValueError):
                deploy.private_url(value)
        for value in ('/tmp/bad\nExecStart=evil', '/tmp/%h', '/tmp/$HOME'):
            with self.assertRaises(ValueError):
                deploy.unit_text(Path(value), Path('/usr/bin/python3'))
        self.assertEqual(deploy.private_url('http://[::1]:11434/v1/'), 'http://[::1]:11434/v1')

    def native(self):
        path = self.root / 'state/hermes/state.db'
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE state_meta(key TEXT, value TEXT)')
            db.execute('CREATE TABLE session_turn_leases(conversation_id TEXT)')
        return path

    def test_idle_is_readonly_and_does_not_expire_leases(self):
        path = self.native()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('INSERT INTO state_meta VALUES(?,?)', ('goal:synthetic', '{"status":"paused"}'))
        before = path.read_bytes()
        self.assertTrue(deploy.idle_state(self.root)['observed_idle'])
        self.assertEqual(path.read_bytes(), before)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('INSERT INTO session_turn_leases VALUES(?)', ('synthetic',))
        result = deploy.idle_state(self.root)
        self.assertFalse(result['observed_idle'])
        self.assertEqual(result['turn_leases'], 1)

    def test_active_goal_or_verification_or_unknown_blocks_idle(self):
        path = self.native()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('INSERT INTO state_meta VALUES(?,?)', ('goal:synthetic', '{"status":"active"}'))
        self.assertFalse(deploy.idle_state(self.root)['observed_idle'])
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('UPDATE state_meta SET value=?', ('{"status":"paused"}',))
        verify = self.root / 'state/intake/cases/synthetic/results/verify-example.json'
        verify.parent.mkdir(parents=True)
        verify.write_text('{"status":"running","pid":999999999}')
        self.assertFalse(deploy.idle_state(self.root)['observed_idle'])
        verify.write_text('broken')
        self.assertEqual(deploy.idle_state(self.root)['unknown'], ['verification_receipt_unreadable'])

    def test_doctor_no_network_no_secret_values(self):
        self.init()
        home = self.root / 'state/hermes'
        env = home / '.env'
        env.write_text('TELEGRAM_BOT_TOKEN=private-do-not-print\n')
        env.chmod(0o644)
        with patch('socket.create_connection', side_effect=AssertionError('network forbidden')), \
                patch('subprocess.run', side_effect=AssertionError('execution forbidden')):
            result = deploy.doctor(self.root)
        self.assertFalse(result['ready_for_manual_preflight'])
        self.assertEqual(result['model_requests'], 0)
        self.assertNotIn('private-do-not-print', json.dumps(result))
        self.assertIn('private_mode:state/hermes/.env', [r['check'] for r in result['checks'] if not r['ok']])

    def test_frontend_preflight_requires_both_native_compiler_receipts(self):
        with patch('hermes_cli.source_build.source_product_current', side_effect=[True, False]) as check:
            result=deploy.frontend_preflight(self.root)
        self.assertFalse(result['ready'])
        self.assertEqual(result['products'], {'tui':True,'web':False})
        self.assertEqual([call.args[1] for call in check.call_args_list], ['tui','web'])
        with patch('hermes_cli.source_build.source_product_current', return_value=True):
            self.assertTrue(deploy.frontend_preflight(self.root)['ready'])

    def test_bundle_allowlist_preserves_hashes_not_runtime_or_git(self):
        upstream = self.root / 'upstream/hermes'
        (upstream / 'LICENSE').write_text('license retained')
        (self.root / 'DEPLOYMENT.txt').write_text('deployment guide')
        secret = self.root / 'state/hermes/.env'
        secret.parent.mkdir(parents=True)
        secret.write_text('secret-value')
        (self.root / 'config/config.yaml').write_text('real endpoint and secret')
        output = Path(self.temp.name) / 'source.tar.gz'
        with patch('subprocess.check_output', side_effect=[deploy.PIN + '\n', b'LICENSE\0pm/lock.json\0', b'']):
            receipt = deploy.bundle(self.root, output)
        self.assertFalse(receipt['uploaded'])
        with tarfile.open(output) as archive:
            names = archive.getnames()
            self.assertFalse(any('/state/' in name or '/.git/' in name or name.endswith('config/config.yaml') for name in names))
            self.assertIn('forsic/upstream/hermes/LICENSE', names)
            manifest = json.load(archive.extractfile('forsic/source-manifest.json'))
            for name, expected in manifest['files'].items():
                self.assertEqual(hashlib.sha256(archive.extractfile('forsic/' + name).read()).hexdigest(), expected)
        with self.assertRaisesRegex(ValueError, 'Output exists'):
            deploy.bundle(self.root, output)

    def test_bundle_rejects_outside_symlink_and_wrong_pin(self):
        with patch('subprocess.check_output', return_value='incorrect\n'):
            with self.assertRaisesRegex(ValueError, 'reviewed pin'):
                deploy.source_files(self.root)
        path = self.root / 'forsic_plugin/outside.py'
        path.symlink_to('/etc/passwd')
        with patch('subprocess.check_output', side_effect=[deploy.PIN + '\n', b'pm/lock.json\0', b'']):
            with self.assertRaisesRegex(ValueError, 'escapes checkout'):
                deploy.source_files(self.root)

    def test_source_manifest_dependency_pins_and_smoke_optin(self):
        source = Path(__file__).resolve().parent
        manifest = (source / 'forsic_plugin/plugin.yaml').read_text()
        for name, version in deploy.DEPENDENCIES.items():
            self.assertIn(name + '==' + version, manifest)
        for name in ('smoke.py', 'library_smoke.py'):
            text = (source / name).read_text()
            self.assertIn('if not args.live:', text)
            self.assertIn('private_url', text)
            self.assertNotIn("base_url='http", text)
            self.assertNotIn("Path('/home/", text)

    def test_public_export_preserves_dependencies_and_reproduces_without_git(self):
        upstream = self.root / 'upstream/hermes'
        kept = ('LICENSE', 'website/static/api/model-catalog.json', 'apps/shared/src/api.ts',
                'apps/desktop/package.json', 'tests/conftest.py', 'tests/_fixtures/env_filter.py')
        removed = ('website/static/big.png', 'evals/sample.py', 'apps/desktop/src/main.ts',
                   'contributors/screenshot.png', 'tests/test_unrelated.py')
        changed = 'tests/test_product_fix.py'
        for name in (*kept, *removed, changed):
            target = upstream / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('before')
        def git(*args):
            return subprocess.check_output(['git', '-C', str(upstream), *args], text=True).strip()
        git('init', '-q')
        git('add', '.')
        git('-c', 'user.name=Synthetic Test', '-c', 'user.email=test@example.invalid',
            'commit', '-qm', 'synthetic source')
        pin = git('rev-parse', 'HEAD')
        (upstream / changed).write_text('after')
        addition = 'web/src/lib/analysis-title.ts'
        target = upstream / addition
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('synthetic new product module')
        public = Path(self.temp.name) / 'public source'
        first = Path(self.temp.name) / 'first.tar.gz'
        second = Path(self.temp.name) / 'different-name.tar.gz'
        with patch.object(deploy, 'PIN', pin):
            result = deploy.export_source(self.root, public)
            self.assertFalse(result['uploaded'])
            for name in (*kept, changed, addition):
                self.assertTrue((public / 'upstream/hermes' / name).is_file(), name)
            for name in removed:
                self.assertFalse((public / 'upstream/hermes' / name).exists(), name)
            manifest = json.loads((public / 'source-manifest.json').read_text())
            self.assertEqual(set(manifest['upstream_omitted']), set(removed))
            self.assertIn(changed, manifest['upstream_changed_files'])
            self.assertFalse((public / 'upstream/hermes/.git').exists())
            self.assertEqual(deploy.bundle(self.root, first)['sha256'],
                             deploy.bundle(public, second)['sha256'])
            with self.assertRaisesRegex(ValueError, 'Output exists'):
                deploy.export_source(self.root, public)
            (public / 'run.py').write_text('changed after export')
            with self.assertRaisesRegex(ValueError, 'Exported source changed'):
                deploy.release_plan(public)

    def test_missing_required_source_and_manifest_escape_are_not_exported(self):
        (self.root / 'run.py').unlink()
        with patch('subprocess.check_output', side_effect=[deploy.PIN + '\n', b'pm/lock.json\0', b'']):
            with self.assertRaisesRegex(ValueError, 'Missing source'):
                deploy.export_source(self.root, Path(self.temp.name) / 'missing-source')
        manifest = {'schema':'forsic.source-release.v2', 'upstream_commit':deploy.PIN,
                    'files':{'../outside':'ignored'}, 'modes':{'../outside':0o644}}
        (self.root / 'source-manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'Invalid source path'):
            deploy.release_plan(self.root)


if __name__ == '__main__':
    unittest.main()
