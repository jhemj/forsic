"""Actual native plugin registration, scoped menus and skill reads; synthetic only."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import ast
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin import register
from forsic_plugin.capability_policy import CORE_TOOLS, SKILLS, TOOLS, save as save_preferences
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

    def test_launcher_pin_reaches_native_toolset_loader_without_broadening_menu(self):
        from types import SimpleNamespace
        from model_tools import get_tool_definitions
        from tui_gateway import server
        launcher = Path(__file__).with_name('run.py')
        tree = ast.parse(launcher.read_text(),filename=str(launcher))
        assignments = [node for node in tree.body if isinstance(node,ast.Assign) and any(
            isinstance(target,ast.Subscript) and isinstance(target.value,ast.Attribute) and
            isinstance(target.value.value,ast.Name) and target.value.value.id == 'os' and
            target.value.attr == 'environ' and isinstance(target.slice,ast.Constant) and
            target.slice.value == 'HERMES_TUI_TOOLSETS' for target in node.targets)]
        self.assertEqual(len(assignments),1,'Launcher must declare its native menu pin explicitly')
        # Execute the actual pin assignment without importing/launching run.py.
        environment = {}
        pin_module = ast.Module(body=assignments,type_ignores=[])
        exec(compile(pin_module,str(launcher),'exec'),{'os':SimpleNamespace(environ=environment)})
        with self.current_home('a'), patch.dict(os.environ,environment):
            enabled = server._load_enabled_toolsets(platform='web')
            selected = get_tool_definitions(enabled_toolsets=enabled,
                disabled_toolsets=server._load_disabled_toolsets(),quiet_mode=True,
                skip_tool_search_assembly=True)
        names = {item['function']['name'] for item in selected}
        self.assertIn('clarify',names)
        self.assertTrue(set(TOOLS).issubset(names))
        self.assertEqual(names-(set(TOOLS)|{'clarify','memory'}),set())

    def test_native_clarify_is_opt_in_and_remains_outside_forsic_evidence_missions(self):
        from model_tools import get_tool_definitions
        from tools.clarify_tool import CLARIFY_SCHEMA
        with self.current_home('a'):
            baseline = self.menu()
            self.assertNotIn('clarify',baseline)
            self.write(target_os='all',tools={name:False for name in TOOLS if name not in CORE_TOOLS},skills={})
            selected = get_tool_definitions(enabled_toolsets=['forsic','clarify'],quiet_mode=True,
                skip_tool_search_assembly=True)
            menu = {item['function']['name']:item['function'] for item in selected}
            self.assertEqual(menu['clarify'],CLARIFY_SCHEMA)
            self.assertNotIn('clarify',TOOLS)
            self.assertNotIn('clarify',baseline['forsic_mission']['parameters']['properties']['tool_name']['enum'])
            self.assertFalse(any(name in menu for name in ('terminal','execute_code','web_search','delegate_task')))
            disabled = get_tool_definitions(enabled_toolsets=['forsic','clarify'],disabled_toolsets=['clarify'],
                quiet_mode=True,skip_tool_search_assembly=True)
            self.assertNotIn('clarify',{item['function']['name'] for item in disabled})

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





class NativeClarifyExecutionTests(unittest.TestCase):
    def test_inline_clarify_preserves_callback_outcomes_and_keyboard_interrupt(self):
        from types import SimpleNamespace
        from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS, InlineToolContext
        args = {'questions':[{'question':'합성 조사 범위를 선택해주세요','choices':['한 호스트','두 호스트']}]}
        context = InlineToolContext(effective_task_id='synthetic-session',tool_call_id='call-clarify',messages=[])
        callback = unittest.mock.Mock(return_value={'answers':{'q0':'한 호스트'},'outcome':'answered'})
        agent = SimpleNamespace(clarify_callback=callback)
        execute = INLINE_TOOL_EXECUTORS['clarify']
        result = json.loads(execute(agent,args,context))
        self.assertEqual(callback.call_args.args[0][0]['qid'],'q0')
        self.assertEqual(result['responses'][0]['user_response'],'한 호스트')
        for outcome in ('interrupted','undelivered','timeout'):
            callback.return_value = {'answers':{},'outcome':outcome}
            result = json.loads(execute(agent,args,context))
            self.assertEqual(result['outcome'],outcome)
            self.assertEqual(result['responses'][0]['status'],'unanswered')
            self.assertIsNone(result['responses'][0]['user_response'])
        callback.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            execute(agent,args,context)

    def test_sequential_native_clarify_fires_pre_and_terminal_post_with_same_call_identity(self):
        from types import SimpleNamespace
        from agent import tool_executor as native
        seen = []
        args = {'questions':[{'question':'합성 사용자 확인','choices':['계속','보류']}]}
        def ask(questions):
            seen.append(('callback',questions))
            return {'answers':{'q0':'보류'},'outcome':'answered'}
        agent = SimpleNamespace(session_id='synthetic-session',_current_turn_id='turn-synthetic',
            _current_api_request_id='api-synthetic',clarify_callback=ask,
            _tool_guardrails=SimpleNamespace(before_call=lambda *_:SimpleNamespace(allows_execution=True)))
        ref = native._ToolCallRef('clarify',args,'synthetic-session','call-native-clarify',[])
        state = native._ManagedToolResult(None,args,[],False,False)
        def pre(name,arguments,**identity):
            seen.append(('pre',name,arguments,identity));return None,None
        def post(**event):
            seen.append(('post',event))
        dispatch = native._resolve_sequential_dispatch(agent,ref,[])
        with patch('hermes_cli.plugins._dispatch_pre_tool_call_hooks',side_effect=pre), \
             patch('model_tools._emit_post_tool_call_hook',side_effect=post), \
             patch.object(native,'_begin_tool_execution'), \
             patch.object(native,'_run_with_activity_heartbeat',side_effect=lambda _,__,execute:execute()), \
             patch('agent.terminal_approval_batch.prepare_current_terminal'), \
             patch.object(native,'apply_transform_tool_result',side_effect=lambda _,**kw:kw['result']), \
             patch.object(native,'_commit_tool_result',return_value=None):
            state.result = native._dispatch_authorized_once(agent,state,ref,execute=dispatch.execute,
                scope_block=None,display_index=None,begin_execution=None,authorization_gate=None)
            native._publish_sequential_result(agent,[],ref,state,tool_duration=0,index=1,budget=None,
                transform_applied=dispatch.transform_applied)
        self.assertEqual([event[0] for event in seen],['pre','callback','post'])
        pre_ids = seen[0][3]; post_event = seen[2][1]
        for key,value in {'session_id':'synthetic-session','tool_call_id':'call-native-clarify',
                          'turn_id':'turn-synthetic','api_request_id':'api-synthetic'}.items():
            self.assertEqual(pre_ids[key],value)
            self.assertEqual(post_event[key],value)
        self.assertEqual(post_event['function_name'],'clarify')
        self.assertEqual(json.loads(post_event['result'])['responses'][0]['user_response'],'보류')


if __name__ == '__main__':
    unittest.main()
