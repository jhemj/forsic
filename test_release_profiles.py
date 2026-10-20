"""Profiles filter verified source; they never edit a live source or state tree."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import deploy_forsic as deploy


class ReleaseProfileTests(unittest.TestCase):
    def baseline(self, root):
        names = ['run.py', 'test_deployment.py', 'HARNESS_ACCEPTANCE.txt', 'DESIGN.txt',
                 'docs/ARCHITECTURE.md', 'LICENSE', 'THIRD_PARTY_NOTICES.txt',
                 'tests/acceptance/truth/synthetic.json',
                 'forsic_plugin/plugin.yaml', 'forsic_plugin/ui/skills.ko.json',
                 'upstream/hermes/apps/shared/package.json', 'upstream/hermes/pm/lock.json',
                 'upstream/hermes/agent/run.py', 'upstream/hermes/web/package.json',
                 'upstream/hermes/web/src/components/ForsicWorkbench.test.tsx']
        names += ['config/' + name for name in deploy.CONFIG_FILES]
        names += ['cases/smoke/evidence/' + name for name in deploy.FIXTURES]
        manifest = {'schema': 'forsic.source-release.v2', 'upstream_commit': deploy.PIN,
                    'profile': 'forsic-linux-web-source', 'files': {}, 'modes': {},
                    'upstream_omitted': {}, 'upstream_changed_files': {}, 'upstream_deleted_by_fork': []}
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic only\n')
            path.chmod(0o644)
            manifest['files'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest['modes'][name] = 0o644
        (root / 'source-manifest.json').write_text(json.dumps(manifest))
        return manifest

    def test_default_preserves_development_and_slim_keeps_native_build_and_fixtures(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'; root.mkdir()
            baseline = self.baseline(root)
            self.assertEqual(deploy.release_plan(root), baseline)
            slim = deploy.release_plan(root, 'slim')
            self.assertEqual(len(slim['profile_omitted']), 5)
            for name in baseline['files']:
                self.assertEqual(name in slim['files'], not bool(deploy.slim_omission(name)))
            self.assertTrue((root / 'test_deployment.py').exists())
            self.assertNotIn('tests/acceptance/truth/synthetic.json', slim['files'])
            self.assertFalse((root / 'state').exists())

    def test_excluded_file_tampering_still_rejected_before_filtering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.baseline(root)
            (root / 'test_deployment.py').write_text('unapproved change')
            with self.assertRaisesRegex(ValueError, 'Exported source changed'):
                deploy.release_plan(root, 'slim')

    def test_slim_export_init_and_reexport_are_reproducible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'; root.mkdir(); self.baseline(root)
            target = Path(temp) / 'slim'
            deploy.export_source(root, target, 'slim')
            self.assertEqual(deploy.release_plan(target), deploy.release_plan(root, 'slim'))
            first, second = Path(temp) / 'first.tgz', Path(temp) / 'second.tgz'
            self.assertEqual(deploy.bundle(root, first, 'slim')['sha256'], deploy.bundle(target, second)['sha256'])
            with self.assertRaisesRegex(ValueError, 'cannot reconstruct'):
                deploy.release_plan(target, 'development')
            result = deploy.initialize(target, 'http://127.0.0.1:11434/v1', 'synthetic', Path('/usr/bin/python3'))
            self.assertFalse(result['service_started'])
            self.assertEqual(result['model_requests'], 0)

    def test_checkout_profile_uses_pin_and_delta_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.baseline(root)
            (root / 'upstream/hermes/.git').mkdir()
            # Exercise the real checkout branch with a synthetic pinned git inventory.
            with patch.object(deploy, 'ROOT_FILES', ('run.py', 'test_deployment.py')), \
                    patch.object(deploy, 'REQUIRED_PUBLIC', ('LICENSE',)), \
                    patch('subprocess.check_output', side_effect=[deploy.PIN + '\n', b'apps/shared/package.json\0pm/lock.json\0agent/run.py\0web/package.json\0', b'']):
                slim = deploy.release_plan(root, 'slim')
            self.assertNotIn('test_deployment.py', slim['files'])
            self.assertIn('upstream/hermes/agent/run.py', slim['files'])


if __name__ == '__main__': unittest.main()
