"""Fresh bootstrap boundaries with synthetic files; never download or start an agent."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import deploy_forsic as deploy
import setup_forsic as setup


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'new installation'
        for name in ('run.py', 'forsic_plugin/plugin.yaml', 'upstream/hermes/pm/lock.json',
                     *('config/' + n for n in deploy.CONFIG_FILES),
                     *('cases/smoke/evidence/' + n for n in deploy.FIXTURES)):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic fixture\n')
        uid = patch('os.geteuid', return_value=1000)
        uid.start()
        self.addCleanup(uid.stop)

    def test_wizard_is_unconfigured_and_never_contacts_model(self):
        with patch('subprocess.run', side_effect=AssertionError('network/process forbidden')):
            deploy.initialize(self.root, 'http://127.0.0.1:11434/v1', '', Path('/usr/bin/python3'), wizard=True)
        cfg = json.loads((self.root / 'state/hermes/config.yaml').read_text())
        self.assertFalse(cfg['plugins']['entries']['forsic']['settings']['setup_completed'])
        self.assertEqual(cfg['model']['default'], '')
        self.assertNotIn('context_length', cfg['model'])
        self.assertNotIn('ollama_num_ctx', cfg['model'])
        self.assertEqual(cfg['auxiliary']['goal_judge']['model'], '')
        self.assertFalse((self.root / 'state/hermes/state.db').exists())

    def test_bootstrap_uses_native_dependencies_and_builders_without_start(self):
        calls = []
        setup.prepare(self.root, '/usr/bin/python3', runner=lambda argv, **kw: calls.append((argv, kw)))
        self.assertEqual([c[0] for c in calls], [cmd for _, cmd in setup.steps('/usr/bin/python3')])
        self.assertTrue(all(c[1]['cwd'] == self.root and c[1]['check'] for c in calls))
        self.assertTrue(all(c[1]['env']['HERMES_HOME'] == str(self.root / 'state/hermes') for c in calls))
        self.assertEqual(json.loads((self.root / 'state/deployment/bootstrap.json').read_text())['status'], 'ready')
        self.assertFalse((self.root / 'state/hermes/state.db').exists())

    def test_failure_stops_before_later_steps_and_can_resume_only_before_launch(self):
        calls = []
        def fail(argv, **_):
            calls.append(argv)
            raise subprocess.CalledProcessError(1, argv)
        with self.assertRaises(subprocess.CalledProcessError):
            setup.prepare(self.root, '/usr/bin/python3', runner=fail)
        self.assertEqual(len(calls), 1)
        before = (self.root / 'state/hermes/config.yaml').read_bytes()
        setup.check_target(self.root, True)
        (self.root / 'state/hermes/state.db').touch()
        with self.assertRaises(ValueError):
            setup.prepare(self.root, '/usr/bin/python3', resume=True, runner=fail)
        self.assertEqual((self.root / 'state/hermes/config.yaml').read_bytes(), before)
        self.assertEqual(len(calls), 1)

    def test_existing_install_is_never_overwritten(self):
        (self.root / 'state').mkdir()
        with self.assertRaises(ValueError):
            setup.prepare(self.root, '/usr/bin/python3', runner=lambda *a, **k: self.fail('must not run'))
        self.assertEqual(list((self.root / 'state').iterdir()), [])

    def test_plan_has_no_side_effects(self):
        with patch('setup_forsic.prepare', side_effect=AssertionError('must not prepare')), patch('builtins.print'):
            self.assertEqual(setup.main(['--plan']), 0)

    def test_release_rejects_nested_credentials_and_runtime_files(self):
        for name in ('forsic_plugin/.env.production', 'forsic_plugin/cache/session.db',
                     'forsic_plugin/cert.key', 'forsic_plugin/artifacts/log.json',
                     'forsic_plugin/cache/activity.sqlite3-wal'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                deploy.checked_source(self.root, Path(name))


if __name__ == '__main__':
    unittest.main()
