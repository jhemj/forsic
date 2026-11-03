"""Read-only effective policy diagnostics across independent synthetic profiles."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import deploy_forsic


class ToolPolicyDiagnosticsTests(unittest.TestCase):
    def test_profiles_report_native_effective_modes_without_inheritance_or_writes(self):
        from tools.tool_search import ToolSearchConfig
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / 'state/hermes'
            values = {'default': {'tools': {'tool_search': {'enabled': False}}},
                      'tenant-a': {'model': {'api_key': 'SYNTHETIC_PRIVATE_VALUE'}},
                      'tenant-b': {'tools': {'tool_search': {'enabled': True}}}}
            paths = {}
            for name, value in values.items():
                path = home if name == 'default' else home / 'profiles' / name
                path.mkdir(parents=True)
                paths[name] = path / 'config.yaml'
                paths[name].write_text(json.dumps(value))
            before = {name: path.read_bytes() for name, path in paths.items()}
            with patch('socket.create_connection', side_effect=AssertionError('network forbidden')):
                first = deploy_forsic.tool_search_policies(root)
                second = deploy_forsic.tool_search_policies(root)
            self.assertEqual(first, second)
            indexed = {row['profile']: row for row in first}
            for name, value in values.items():
                raw = value.get('tools', {}).get('tool_search')
                self.assertEqual(indexed[name]['enabled'], ToolSearchConfig.from_raw(raw).enabled)
            self.assertEqual(indexed['tenant-a']['source'], 'native_default')
            self.assertEqual(indexed['default']['source'], 'profile_config')
            self.assertNotEqual(indexed['tenant-a']['enabled'], indexed['default']['enabled'])
            self.assertNotIn('SYNTHETIC_PRIVATE_VALUE', json.dumps(first))
            self.assertEqual(before, {name: path.read_bytes() for name, path in paths.items()})

    def test_bad_config_is_unknown_without_echoing_or_following_a_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / 'state/hermes'
            tenant = home / 'profiles/tenant-a'
            tenant.mkdir(parents=True)
            (home / 'config.yaml').write_text('{ broken: [ SYNTHETIC_PRIVATE_VALUE')
            secret = root / 'outside-config'
            secret.write_text('SYNTHETIC_PRIVATE_VALUE')
            (tenant / 'config.yaml').symlink_to(secret)
            result = deploy_forsic.tool_search_policies(root)
            self.assertTrue(all(row['status'] == 'unavailable' for row in result))
            self.assertNotIn('SYNTHETIC_PRIVATE_VALUE', json.dumps(result))
            self.assertEqual(secret.read_text(), 'SYNTHETIC_PRIVATE_VALUE')


if __name__ == '__main__':
    unittest.main()
