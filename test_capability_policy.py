"""Offline capability controls; no model, service, external traffic or real case."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from forsic_plugin import register
from forsic_plugin.capability_policy import (
    CORE_TOOLS, SKILLS, TOOLS, normalize, policy, policy_from_config, public, save,
)


class CapabilityPolicyTests(unittest.TestCase):
    def test_absent_settings_preserve_every_current_capability(self):
        selected = policy_from_config({})
        self.assertTrue(all(selected.tool_enabled(name) for name in TOOLS))
        self.assertTrue(all(selected.skill_enabled(name) for name in SKILLS))

    def test_os_presets_use_evidence_os_and_explicit_override_wins(self):
        for target in ('all', 'windows', 'macos', 'linux'):
            with self.subTest(target=target):
                selected = policy({'target_os': target})
                self.assertEqual(selected.skill_enabled('scan-windows-events'), target in ('all', 'windows'))
                self.assertTrue(selected.skill_enabled('inspect-artifacts'))
                self.assertTrue(all(selected.tool_enabled(name) for name in TOOLS))
        self.assertTrue(policy({'target_os': 'linux', 'skills': {'scan-windows-events': True}}).skill_enabled('scan-windows-events'))
        self.assertFalse(policy({'target_os': 'windows', 'skills': {'scan-windows-events': False}}).skill_enabled('scan-windows-events'))

    def test_optional_off_cannot_remove_core_affordances(self):
        selected = policy({'tools': {name: False for name in TOOLS if name not in CORE_TOOLS}})
        self.assertTrue(all(selected.tool_enabled(name) for name in CORE_TOOLS))
        self.assertFalse(selected.tool_enabled('forsic_shell'))
        self.assertFalse(selected.tool_enabled('unknown'))
        self.assertFalse(selected.skill_enabled('unknown'))
        for name in CORE_TOOLS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                normalize({'tools': {name: False}})

    def test_reject_invalid_values_instead_of_silently_enabling(self):
        for value in ([], {'other': True}, {'target_os': 'host'}, {'target_os': []}, {'tools': []},
                      {'tools': {'forsic_shell': 'false'}}, {'skills': {'unknown': True}},
                      {'skills': {'scan-windows-events': 1}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize(value)

    def test_preference_migration_and_settings_precedence(self):
        cfg = {'plugins': {'entries': {'forsic': {'config': {'capabilities': {'target_os': 'linux'}}}}}}
        self.assertFalse(policy_from_config(cfg).skill_enabled('scan-windows-events'))
        cfg['plugins']['entries']['forsic']['settings'] = {'capabilities': {'target_os': 'windows'}}
        self.assertTrue(policy_from_config(cfg).skill_enabled('scan-windows-events'))

    def test_public_catalog_distinguishes_defaults_override_and_required(self):
        cfg = {'plugins': {'entries': {'forsic': {'settings': {'capabilities': {
            'target_os': 'linux', 'skills': {'scan-windows-events': True}, 'tools': {'forsic_shell': False}}}}}}}
        value = public(cfg)
        skill = next(item for item in value['skills'] if item['name'] == 'scan-windows-events')
        self.assertTrue(skill['enabled'])
        self.assertFalse(skill['default_enabled'])
        self.assertTrue(skill['overridden'])
        self.assertEqual(skill['target_os'], ['windows'])
        self.assertEqual({item['name'] for item in value['tools']}, set(TOOLS))
        self.assertEqual({item['name'] for item in value['skills']}, set(SKILLS))
        self.assertTrue(next(item for item in value['tools'] if item['name'] == 'forsic_question')['required'])
        self.assertEqual(value['scope'], 'profile')

    def test_save_uses_atomic_native_settings_writer(self):
        with patch('hermes_cli.plugins_state.save_plugin_setting') as write, \
                patch('forsic_plugin.capability_policy.public', return_value={'saved': True}):
            self.assertEqual(save({'target_os': 'macos'}), {'saved': True})
        write.assert_called_once_with('forsic', ('capabilities',), {'target_os': 'macos', 'tools': {}, 'skills': {}}, replace=True)
        with patch('hermes_cli.plugins_state.save_plugin_setting') as write:
            with self.assertRaises(ValueError):
                save({'tools': {'forsic_question': False}})
            write.assert_not_called()

    def test_native_failed_config_read_cannot_silently_enable_optional_work(self):
        from hermes_cli.config_read_errors import FailedConfigRead
        from forsic_plugin.capability_policy import current_policy, runtime_policy
        for cfg in (None, FailedConfigRead(error=OSError('unavailable'))):
            with self.subTest(cfg=type(cfg).__name__), patch('hermes_cli.config.load_config_readonly', return_value=cfg):
                for selected in (current_policy(), runtime_policy(None)):
                    self.assertFalse(selected.tool_enabled('forsic_shell'))
                    self.assertTrue(selected.tool_enabled('forsic_question'))
                    self.assertFalse(selected.skill_enabled('source-triage'))
                with self.assertRaises(ValueError):
                    policy_from_config(cfg)

    def fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = {'intake_root': str(Path(directory.name)/'intake')}
        ctx = Mock()
        ctx.get_config.side_effect = settings.get
        register(ctx)
        tools = {call.kwargs['name']: call.kwargs for call in ctx.register_tool.call_args_list}
        return settings, ctx, tools

    def test_cached_handler_rechecks_current_user_policy_before_case_or_execution(self):
        settings, _, tools = self.fixture()
        handler = tools['forsic_shell']['handler']
        settings['capabilities'] = {'tools': {'forsic_shell': False}}
        with patch('forsic_plugin.intake.Intake.case', side_effect=AssertionError('disabled call must not bind or execute')):
            result = json.loads(handler({'action': 'run', 'command': 'ignored'}, session_id='synthetic-disabled'))
        self.assertEqual(result['error_code'], 'capability_disabled')
        self.assertEqual(result['capability'], 'forsic_shell')
        self.assertFalse(result['retryable'])

    def test_cached_mission_cannot_plan_a_disabled_tool(self):
        settings, _, tools = self.fixture()
        settings['capabilities'] = {'tools': {'forsic_shell': False}}
        with patch('forsic_plugin.intake.Intake.case', side_effect=AssertionError('disabled plan must not bind or save')):
            result = json.loads(tools['forsic_mission']['handler']({
                'tool_name': 'forsic_shell', 'tool_arguments': {'action': 'run', 'command': 'ignored'}},
                session_id='synthetic-disabled'))
        self.assertEqual(result['error_code'], 'capability_disabled')
        self.assertEqual(result['capability'], 'forsic_shell')

    def test_native_registry_availability_changes_without_cached_grace_period(self):
        from tools.registry import ToolRegistry
        settings, _, tools = self.fixture()
        registry = ToolRegistry()
        for name in ('forsic_shell', 'forsic_question'):
            entry = tools[name]
            registry.register(name=name, toolset='forsic', schema=entry['schema'],
                              handler=entry['handler'], check_fn=entry['check_fn'])
        names = {'forsic_shell', 'forsic_question'}
        def menu():
            return {item['function']['name'] for item in registry.get_definitions(names, quiet=True)}
        self.assertEqual(menu(), names)
        settings['capabilities'] = {'tools': {'forsic_shell': False}}
        self.assertEqual(menu(), {'forsic_question'})
        settings['capabilities']['tools']['forsic_shell'] = True
        self.assertEqual(menu(), names)

    def test_skill_catalog_and_cached_alias_reads_obey_current_policy(self):
        settings, ctx, tools = self.fixture()
        self.assertEqual({call.kwargs['name'] for call in ctx.register_skill.call_args_list}, set(SKILLS))
        handler = tools['forsic_skill']['handler']
        settings['capabilities'] = {'skills': {'source-triage': False, 'forsic-report-driven': False}}
        catalog = json.loads(handler({}))
        self.assertNotIn('source-triage', {item['name'] for item in catalog['skills']})
        with patch('tools.skills_tool.skill_view', side_effect=AssertionError('disabled skills must not reach native reader')):
            for args in ({'name': 'source-triage'}, {'name': 'recover-content'},
                         {'name': 'report-executive'}, {'name': 'report-analyst'}):
                self.assertEqual(json.loads(handler(args))['error_code'], 'capability_disabled')
        settings['capabilities'] = {'skills': {'source-triage': True}}
        self.assertIn('source-triage', {item['name'] for item in json.loads(handler({}))['skills']})


if __name__ == '__main__':
    unittest.main()
