"""Actual native plugin registration, scoped menus and skill reads; synthetic only."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin import register
from forsic_plugin.capability_policy import SKILLS, TOOLS, save as save_preferences
from hermes_cli import plugins
from hermes_cli.config import read_user_config_raw, save_config
from hermes_cli.plugins_state import save_plugin_setting
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from tools.registry import registry
from tools.skills_tool import skill_view


class NativeCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.base = Path(self.temp)
        self.homes = {name: self.base / name for name in ('a', 'b')}
        self.stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': str(self.base / 'host')}))
        self.stack.enter_context(patch.object(plugins, '_plugin_manager', None))
        self.stack.enter_context(patch.object(plugins, '_plugin_managers_by_home', {}))
        # Discovery is unrelated to this test: register the actual plugin explicitly.
        self.stack.enter_context(patch.object(plugins, 'discover_plugins'))
        self.stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('offline only')))
        self.stack.enter_context(patch('agent.auxiliary_client.call_llm', side_effect=AssertionError('no model calls')))
        self.managers = {}
        plugin_path = Path(__file__).parent / 'forsic_plugin'
        manifest = plugins.parse_manifest_file(plugin_path / 'plugin.yaml', plugin_path, 'user', '')
        self.assertIsNotNone(manifest)
        for name in self.homes:
            with self.current_home(name):
                save_config({'plugins': {'enabled': ['forsic'], 'entries': {'forsic': {'settings': {
                    'intake_root': str(self.homes[name] / 'intake'),
                }}}}})
                manager = plugins.get_plugin_manager()
                self.assertIsInstance(manager, plugins.PluginManager)
                self.managers[name] = manager
                register(plugins.PluginContext(manifest, manager))
                self.addCleanup(manager.unload, 'forsic')
        self.original_schemas = {
            name: {tool: deepcopy(registry.get_entry(tool, scope=manager.scope_key).schema)
                   for tool in TOOLS}
            for name, manager in self.managers.items()
        }

    @contextmanager
    def current_home(self, name):
        home = self.homes[name]
        home.mkdir(parents=True, exist_ok=True)
        token = set_hermes_home_override(home)
        try:
            yield home
        finally:
            reset_hermes_home_override(token)

    def write(self, **values):
        return save_preferences(values)

    def menu(self):
        from model_tools import get_tool_definitions
        definitions = get_tool_definitions(enabled_toolsets=['forsic'], quiet_mode=True,
                                           skip_tool_search_assembly=True)
        return {item['function']['name']: item['function'] for item in definitions}

    def read_skill(self, name='source-triage', **kwargs):
        return json.loads(skill_view('forsic:' + name, preprocess=False, **kwargs))

    def assert_original_schemas(self):
        for name, manager in self.managers.items():
            current = {tool: registry.get_entry(tool, scope=manager.scope_key).schema for tool in TOOLS}
            self.assertEqual(current, self.original_schemas[name])

    def assert_menu_policy(self, menu, *, enabled):
        self.assertEqual('forsic_shell' in menu, enabled)
        self.assertIn('forsic_question', menu)
        skill_enum = menu['forsic_skill']['parameters']['properties']['name']['enum']
        self.assertEqual('source-triage' in skill_enum, enabled)
        mission = menu['forsic_mission']['parameters']['properties']
        self.assertEqual('forsic_shell' in mission['tool_name']['enum'], enabled)
        branches = {branch['title'] for branch in mission['tool_arguments']['anyOf']}
        self.assertEqual('forsic_shell' in branches, enabled)
        self.assertEqual(branches, set(mission['tool_name']['enum']))

    def test_config_changes_filter_and_restore_same_registered_tools_and_skills(self):
        with self.current_home('a'):
            shell_entry = registry.get_entry('forsic_shell')
            mission_entry = registry.get_entry('forsic_mission')
            self.assertIsNotNone(mission_entry.dynamic_schema_overrides)
            self.assert_menu_policy(self.menu(), enabled=True)
            self.assertTrue(self.read_skill()['success'])
            self.write(target_os='all', tools={'forsic_shell': False}, skills={'source-triage': False})
            self.assert_menu_policy(self.menu(), enabled=False)
            with patch('tools.skills_tool_plugin._read_skill_text', side_effect=AssertionError('disabled skill bytes must not be read')) as read:
                for kwargs in ({}, {'file_path': 'references/archives-and-recovery.md'}):
                    denied = self.read_skill(**kwargs)
                    self.assertFalse(denied['success'])
                    self.assertIn('disabled', denied['error'])
                read.assert_not_called()
            self.assertIs(registry.get_entry('forsic_shell'), shell_entry)
            self.assertIs(registry.get_entry('forsic_mission'), mission_entry)
            self.assertNotIn('source-triage', self.managers['a'].list_plugin_skills('forsic'))
            # Cached dispatch and mission planning both recheck the new preference.
            for tool, args in (('forsic_shell', {'action': 'run', 'command': 'ignored'}),
                               ('forsic_mission', {'tool_name': 'forsic_shell', 'tool_arguments': {}})):
                result = json.loads(registry.dispatch(tool, args, session_id='synthetic-native'))
                self.assertEqual(result['error_code'], 'capability_disabled')
            self.write(target_os='all', tools={'forsic_shell': True}, skills={'source-triage': True})
            self.assert_menu_policy(self.menu(), enabled=True)
            restored = self.read_skill()
            self.assertTrue(restored['success'], restored)
            reference = self.read_skill(file_path='references/archives-and-recovery.md')
            self.assertTrue(reference['success'], reference)
            self.assertIn('source-triage', self.managers['a'].list_plugin_skills('forsic'))
        self.assert_original_schemas()

    def test_profile_switch_a_b_a_does_not_cross_policy_menu_or_native_reader(self):
        with self.current_home('a'):
            self.write(target_os='windows', tools={}, skills={})
            a = self.menu()
            self.assert_menu_policy(a, enabled=True)
            self.assertTrue(self.read_skill()['success'])
        with self.current_home('b'):
            self.write(target_os='linux', tools={'forsic_shell': False}, skills={'source-triage': False})
            self.assertIs(plugins.get_plugin_manager(), self.managers['b'])
            b = self.menu()
            self.assert_menu_policy(b, enabled=False)
            self.assertNotIn('scan-windows-events', b['forsic_skill']['parameters']['properties']['name']['enum'])
            self.assertFalse(self.read_skill()['success'])
            self.assertIsNone(self.managers['b'].find_plugin_skill('forsic:source-triage'))
        with self.current_home('a'):
            self.assertIs(plugins.get_plugin_manager(), self.managers['a'])
            self.assertEqual(self.menu(), a)
            self.assertTrue(self.read_skill()['success'])
            self.assertIsNotNone(self.managers['a'].find_plugin_skill('forsic:source-triage'))
        self.assert_original_schemas()

    def test_every_skill_off_is_catalog_only_and_cannot_fall_through_to_local_copy(self):
        with self.current_home('a') as home:
            fallback = home / 'skills' / 'forsic' / 'source-triage' / 'SKILL.md'
            fallback.parent.mkdir(parents=True)
            fallback.write_text('---\nname: source-triage\ndescription: Synthetic fallback.\n---\nSYNTHETIC_LOCAL_FALLBACK\n')
            self.write(target_os='all', tools={}, skills={name: False for name in SKILLS})
            menu = self.menu()
            skill = menu['forsic_skill']['parameters']
            self.assertNotIn('name', skill['properties'])
            self.assertNotIn('file_path', skill['properties'])
            def check_no_empty_enum(value):
                if isinstance(value, dict):
                    self.assertNotEqual(value.get('enum'), [])
                    for child in value.values(): check_no_empty_enum(child)
                elif isinstance(value, list):
                    for child in value: check_no_empty_enum(child)
            check_no_empty_enum(menu)
            self.assertEqual(self.managers['a'].list_plugin_skills('forsic'), [])
            self.assertEqual(self.managers['a'].list_plugin_skill_metadata(), [])
            result = self.read_skill()
            self.assertFalse(result['success'])
            self.assertIn('disabled', result['error'])
            self.assertNotIn('SYNTHETIC_LOCAL_FALLBACK', json.dumps(result))
            catalog = json.loads(registry.dispatch('forsic_skill', {}))
            self.assertEqual(catalog['skills'], [])
            self.write(target_os='all', tools={}, skills={})
            self.assertEqual(set(self.menu()['forsic_skill']['parameters']['properties']['name']['enum']), set(SKILLS))
            self.assertTrue(self.read_skill()['success'])
        self.assert_original_schemas()


class NativePluginSettingWriterTests(unittest.TestCase):
    """The explicit replacement option leaves existing merge callers unchanged."""
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': str(self.home)}))
        token = set_hermes_home_override(self.home)
        self.stack.callback(reset_hermes_home_override, token)
        save_config({'model': {'default': 'synthetic-model'},
                     'plugins': {'entries': {
                         'forsic': {'settings': {'unrelated': {'preserved': True}}},
                         'other': {'settings': {'preserved': ['synthetic']}}}}})

    def test_capability_replacement_removes_omitted_overrides_and_preserves_siblings(self):
        original = read_user_config_raw()
        save_preferences({'target_os': 'windows',
                          'tools': {'forsic_shell': False, 'forsic_hash': False},
                          'skills': {'source-triage': False, 'inspect-documents': False}})
        save_preferences({'target_os': 'all', 'tools': {'forsic_hash': False},
                          'skills': {'inspect-documents': False}})
        cfg = read_user_config_raw()
        settings = cfg['plugins']['entries']['forsic']['settings']
        self.assertEqual(settings['capabilities']['tools'], {'forsic_hash': False})
        self.assertEqual(settings['capabilities']['skills'], {'inspect-documents': False})
        result = save_preferences({'target_os': 'all', 'tools': {}, 'skills': {}})
        cfg = read_user_config_raw()
        self.assertEqual(cfg['plugins']['entries']['forsic']['settings']['capabilities'],
                         {'target_os': 'all', 'tools': {}, 'skills': {}})
        self.assertTrue(all(item['enabled'] for item in result['tools']))
        self.assertTrue(all(item['enabled'] for item in result['skills']))
        self.assertEqual(cfg['model'], original['model'])
        self.assertEqual(cfg['plugins']['entries']['other'], original['plugins']['entries']['other'])
        self.assertEqual(cfg['plugins']['entries']['forsic']['settings']['unrelated'],
                         original['plugins']['entries']['forsic']['settings']['unrelated'])

    def test_existing_merge_semantics_and_explicit_replace_are_both_supported(self):
        save_plugin_setting('forsic', ('nested',), {'first': 1, 'second': 2})
        save_plugin_setting('forsic', ('nested',), {'first': 3})
        self.assertEqual(read_user_config_raw()['plugins']['entries']['forsic']['settings']['nested'],
                         {'first': 3, 'second': 2})
        save_plugin_setting('forsic', ('nested',), {'first': 4}, replace=True)
        self.assertEqual(read_user_config_raw()['plugins']['entries']['forsic']['settings']['nested'],
                         {'first': 4})
        save_plugin_setting('forsic', ('nested',), {}, replace=True)
        self.assertEqual(read_user_config_raw()['plugins']['entries']['forsic']['settings']['nested'], {})

    def test_replacement_retains_native_atomic_writer_and_config_comments(self):
        from hermes_cli import config
        path = self.home / 'config.yaml'
        path.write_text('# synthetic retained comment\n' + path.read_text())
        original = config.atomic_config_replace
        with patch.object(config, 'atomic_config_replace', wraps=original) as atomic:
            save_plugin_setting('forsic', ('capabilities',), {'tools': {}, 'skills': {}}, replace=True)
        atomic.assert_called_once()
        self.assertIn('# synthetic retained comment', path.read_text())
        self.assertEqual(read_user_config_raw()['plugins']['entries']['forsic']['settings']['capabilities'],
                         {'tools': {}, 'skills': {}})

    def test_replacement_refuses_unreadable_config_and_managed_settings(self):
        path = self.home / 'config.yaml'
        before = path.read_bytes()
        with patch('hermes_cli.config.is_managed', return_value=True):
            with self.assertRaises(PermissionError):
                save_plugin_setting('forsic', ('capabilities',), {}, replace=True)
        self.assertEqual(path.read_bytes(), before)
        with patch('hermes_cli.managed_scope.is_key_managed', return_value=True):
            with self.assertRaises(PermissionError):
                save_plugin_setting('forsic', ('capabilities',), {}, replace=True)
        self.assertEqual(path.read_bytes(), before)
        corrupt = b'plugins: [unclosed\n'
        path.write_bytes(corrupt)
        with self.assertRaises(RuntimeError):
            save_plugin_setting('forsic', ('capabilities',), {}, replace=True)
        self.assertEqual(path.read_bytes(), corrupt)


if __name__ == '__main__':
    unittest.main()
