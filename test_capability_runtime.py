"""Synthetic operator capability edits preserve native turn and config ownership."""
from contextlib import nullcontext
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from forsic_plugin import capability_runtime as runtime
from forsic_plugin.dashboard import plugin_api as api
from forsic_plugin.remote_policy import ForsicRemotePolicy
from hermes_cli.backend_retirement import RetirementFence


class IdleBoundaryTests(unittest.TestCase):
    def test_excludes_turn_starts_until_apply_unwinds_and_restores_on_error(self):
        fence = RetirementFence()
        with patch('hermes_cli.web_server_idle_proof.idle_proof', return_value={'idle': True}):
            with self.assertRaisesRegex(RuntimeError, 'synthetic'):
                with fence.idle_boundary() as idle:
                    self.assertTrue(idle)
                    observations = []
                    worker = threading.Thread(target=lambda: observations.append(fence.acquire()))
                    worker.start()
                    worker.join(2)
                    self.assertEqual(observations, [False])
                    self.assertFalse(fence.prepare()['ok'])
                    raise RuntimeError('synthetic')
        self.assertTrue(fence.acquire())
        fence.release()

    def test_busy_or_unknown_never_admits_update(self):
        for verdict in (False, None):
            fence = RetirementFence()
            with patch('hermes_cli.web_server_idle_proof.idle_proof', return_value={'idle': verdict}):
                with fence.idle_boundary() as idle:
                    self.assertFalse(idle)
            self.assertTrue(fence.acquire())
            fence.release()
        fence = RetirementFence()
        self.assertTrue(fence.acquire())
        with fence.idle_boundary() as idle:
            self.assertFalse(idle)
        fence.release()


class CapabilityRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'HERMES_HOME': str(self.home)}).start()
        from hermes_cli.config import save_config
        save_config({'model': {'default': 'synthetic-preserved', 'api_key': 'secret-not-public'},
                     'plugins': {'enabled': ['forsic'], 'entries': {'forsic': {'settings': {'unrelated': 'preserved'}}}}})
        self.path = self.home / 'state.db'
        with sqlite3.connect(self.path) as db:
            db.executescript('CREATE TABLE sessions(id TEXT PRIMARY KEY,tool_names TEXT);'
                             'CREATE TABLE system_prompts(hash TEXT PRIMARY KEY,prompt TEXT);'
                             'CREATE TABLE session_turn_leases(conversation_id TEXT,expires_at REAL);'
                             'CREATE TABLE compression_locks(session_id TEXT,expires_at REAL);')
        self.app = FastAPI()
        self.app.include_router(api.router, prefix='/api/plugins/forsic')
        self.client = TestClient(self.app, base_url='http://localhost:8860', client=('127.0.0.1', 1234))
        from tui_gateway import server
        self.server = server
        patch.object(server, '_sessions', {}).start()
        patch('hermes_cli.web_server_idle_proof.idle_proof', return_value={'idle': True}).start()
        patch.object(runtime, '_busy', return_value=False).start()
        self.fence = RetirementFence()
        patch('hermes_cli.backend_retirement.retirement', self.fence).start()
        self.body = {'target_os': 'linux', 'tools': {'forsic_hash': False}, 'skills': {}}

    def get(self):
        response = self.client.get('/api/plugins/forsic/capabilities')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def put(self, **overrides):
        return self.client.put('/api/plugins/forsic/capabilities', json={**self.body, 'expected_revision': self.get()['revision'], **overrides})

    def config_bytes(self):
        return (self.home / 'config.yaml').read_bytes()

    def test_get_is_readonly_secret_free_and_noop_does_not_write(self):
        before = self.config_bytes()
        with patch('hermes_cli.config.save_config') as write, patch.object(self.server, '_refresh_live_sessions') as refresh:
            value = self.get()
            response = self.client.put('/api/plugins/forsic/capabilities', json=value['settings'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['applied'])
        self.assertNotIn('secret-not-public', json.dumps(value))
        self.assertNotIn(str(self.home), json.dumps(value))
        write.assert_not_called()
        refresh.assert_not_called()
        self.assertEqual(self.config_bytes(), before)

    def test_changed_save_requires_revision_preserves_other_config(self):
        before = self.config_bytes()
        response = self.client.put('/api/plugins/forsic/capabilities', json=self.body)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.config_bytes(), before)
        response = self.put()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['applied'])
        from hermes_cli.config import require_readable_config_before_write
        cfg = require_readable_config_before_write()
        self.assertEqual(cfg['model']['default'], 'synthetic-preserved')
        self.assertEqual(cfg['plugins']['entries']['forsic']['settings']['unrelated'], 'preserved')
        self.assertEqual(cfg['plugins']['entries']['forsic']['settings']['capabilities'], self.body)
        before = self.config_bytes()
        response = self.put(expected_revision='stale')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.config_bytes(), before)

    def test_busy_and_durable_lease_or_compression_refuse_before_mutation(self):
        before = self.config_bytes()
        self.fence.acquire()
        self.assertEqual(self.put().status_code, 409)
        self.fence.release()
        for table, field in (('session_turn_leases', 'conversation_id'), ('compression_locks', 'session_id')):
            with sqlite3.connect(self.path) as db:
                db.execute(f'INSERT INTO {table}({field},expires_at) VALUES(?,?)', ('synthetic', 9999999999))
            self.assertEqual(self.put().status_code, 409)
            self.assertEqual(self.config_bytes(), before)
            with sqlite3.connect(self.path) as db:
                db.execute(f'DELETE FROM {table}')

    def test_running_building_and_unreadable_native_state_are_busy(self):
        from hermes_cli.web_server_idle_proof import idle_proof
        from hermes_cli.web_server_idle_exit import busy_ledger
        before = self.config_bytes()
        for state in ({'running': True}, {'_agent_build_thread': SimpleNamespace(is_alive=lambda: True)}):
            self.server._sessions['synthetic'] = state
            with patch('hermes_cli.web_server_idle_proof.idle_proof', side_effect=lambda: idle_proof(turn_probe=busy_ledger, input_probe=lambda: 0)):
                self.assertEqual(self.put().status_code, 409)
        self.server._sessions.clear()
        with patch.object(runtime, '_durable_idle', side_effect=sqlite3.OperationalError('synthetic')):
            self.assertEqual(self.put().status_code, 409)
        self.assertEqual(self.config_bytes(), before)

    def test_local_auth_profile_and_strict_input_boundaries(self):
        before = self.config_bytes()
        remote = TestClient(self.app, base_url='https://forsic.example.invalid', client=('127.0.0.1', 1234))
        self.assertFalse(remote.get('/api/plugins/forsic/capabilities').json()['local_admin'])
        self.assertEqual(remote.put('/api/plugins/forsic/capabilities', json=self.body).status_code, 403)
        self.assertEqual(self.client.get('/api/plugins/forsic/capabilities?profile=other').status_code, 403)
        self.assertEqual(self.client.put('/api/plugins/forsic/capabilities?profile=other', json=self.body).status_code, 403)
        for body in ({**self.body, 'model': 'forbidden'}, {**self.body, 'tools': {'forsic_hash': 'false'}},
                     {**self.body, 'tools': {'unknown': True}}):
            self.assertIn(self.client.put('/api/plugins/forsic/capabilities', json=body).status_code, (400, 422))
        self.assertEqual(self.config_bytes(), before)
        for headers in ({'origin': 'https://evil.invalid'}, {'x-forwarded-for': '127.0.0.1'}):
            self.assertEqual(self.client.put('/api/plugins/forsic/capabilities', json=self.body, headers=headers).status_code, 403)
        from hermes_cli import web_server as native
        app = FastAPI()
        app.state.auth_required = False
        app.include_router(api.router, prefix='/api/plugins/forsic', dependencies=[Depends(native._require_token)])
        unauthenticated = TestClient(app, base_url='http://localhost:8860', client=('127.0.0.1', 1234))
        self.assertEqual(unauthenticated.get('/api/plugins/forsic/capabilities').status_code, 401)
        self.assertEqual(unauthenticated.put('/api/plugins/forsic/capabilities', json=self.body).status_code, 401)
        policy = ForsicRemotePolicy({'remote_evidence_directory_only': True}, self.home)
        scope = {'path': '/api/plugins/forsic/capabilities', 'query_string': b''}
        self.assertTrue(policy.http_allowed({**scope, 'method': 'GET'}))
        self.assertFalse(policy.http_allowed({**scope, 'method': 'PUT'}))

    def test_foreign_request_scope_never_writes_and_returns_to_served_home(self):
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        before = self.config_bytes()
        other = self.home / 'other'
        other.mkdir()
        token = set_hermes_home_override(str(other))
        try:
            # TestClient propagates ContextVars like native async scope -> sync handler.
            self.assertEqual(self.client.get('/api/plugins/forsic/capabilities').status_code, 403)
            self.assertEqual(self.client.put('/api/plugins/forsic/capabilities', json=self.body).status_code, 403)
        finally:
            reset_hermes_home_override(token)
        self.assertEqual(self.config_bytes(), before)
        self.assertFalse((other / 'config.yaml').exists())
        self.assertTrue(self.get()['local_admin'])

    def live_agent(self, real_presentation=False):
        tools = [{'type': 'function', 'function': {'name': 'forsic_hash', 'parameters': {'type': 'object'}}}]
        agent = SimpleNamespace(tools=tools, valid_tool_names={'forsic_hash'}, session_id='synthetic',
                                enabled_toolsets=[], disabled_toolsets=[], platform='tui',
                                model='synthetic', provider='ollama', api_key='no-key-required')
        def persist(session_id, pin):
            with sqlite3.connect(self.path) as db:
                db.execute('INSERT OR REPLACE INTO sessions(id,tool_names) VALUES(?,?)', (session_id, json.dumps(pin)))
        agent._session_db = SimpleNamespace(update_session_tool_names=persist)
        self.server._sessions['runtime'] = {'agent': agent, 'profile_home': str(self.home),
                                           'history_lock': threading.Lock(), 'goal_user_paused': True,
                                           'cwd': str(self.home), 'transport': SimpleNamespace(write=Mock(return_value=True))}
        patch.object(self.server, '_session_profile_runtime_scope', return_value=nullcontext()).start()
        patch.object(self.server, '_load_enabled_toolsets', return_value=[]).start()
        patch.object(self.server, '_load_disabled_toolsets', return_value=[]).start()
        if not real_presentation:
            patch.object(self.server, '_session_info', return_value={}).start()
            patch.object(self.server, '_emit').start()
        patch('model_tools.get_tool_definitions', return_value=[]).start()
        patch('tools.mcp_tool_agent._reinject_post_build_tools', return_value=set()).start()
        patch('tools.mcp_tool_agent._reinject_authorized_dynamic_tools').start()
        patch('tools.mcp_tool_agent._drop_side_agent_tools', side_effect=lambda _, defs, names: (defs, names)).start()
        return agent

    def test_native_refresh_verifies_actual_pin_and_preserves_user_pause(self):
        agent = self.live_agent(real_presentation=True)
        response = self.put()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['applied'])
        self.assertEqual(agent.tools, [])
        self.assertEqual(runtime._pin(self.home, 'synthetic')['tools'], [])
        self.assertTrue(self.server._sessions['runtime']['goal_user_paused'])
        frame = self.server._sessions['runtime']['transport'].write.call_args.args[0]
        self.assertEqual(frame['params']['type'], 'session.info')

    def test_clarify_menu_mismatch_cannot_be_reported_as_applied_even_with_matching_pin(self):
        agent = self.live_agent()
        clarify = {'type':'function','function':{'name':'clarify','parameters':{'type':'object'}}}
        session = self.server._sessions['runtime']
        for current, expected in (([],[clarify]),([clarify],[])):
            with self.subTest(current=current,expected=expected):
                agent.tools = current
                agent.valid_tool_names = {item['function']['name'] for item in current}
                agent._session_db.update_session_tool_names('synthetic',{'tools':current})
                with patch('model_tools.get_tool_definitions',return_value=expected):
                    self.assertFalse(runtime._verify_session(self.home,session))
        agent.tools = [clarify]; agent.valid_tool_names = {'clarify'}
        agent._session_db.update_session_tool_names('synthetic',{'tools':[clarify]})
        with patch('model_tools.get_tool_definitions',return_value=[clarify]):
            self.assertTrue(runtime._verify_session(self.home,session))
        self.assertTrue(session['goal_user_paused'])

    def test_swallowed_refresh_or_pin_failure_returns_pending_and_can_retry(self):
        agent = self.live_agent()
        with patch.object(self.server, '_refresh_live_sessions'):
            result = self.put().json()
        self.assertFalse(result['applied'])
        self.assertTrue(result['pending_runtime_update'])
        self.assertEqual(result['settings'], self.body)
        agent._session_db.update_session_tool_names = Mock(side_effect=RuntimeError('synthetic pin failure'))
        result = self.put().json()
        self.assertFalse(result['applied'])
        self.assertEqual(result['apply_status'], 'unapplied')
        self.assertTrue(self.server._sessions['runtime']['goal_user_paused'])

    def test_lazy_saved_policy_survives_actual_native_pin_restore_off_on(self):
        import model_tools
        from agent.conversation_loop import _restore_pinned_tools
        from forsic_plugin.capability_policy import current_policy
        from forsic_plugin.capability_schemas import schema_overrides
        from tools.mcp_tool_agent import tool_pin_version
        from tools.registry import ToolRegistry, no_cache_check_fn
        registry = ToolRegistry()
        schemas = {
            'forsic_hash': {'parameters': {'type': 'object', 'properties': {}}},
            'forsic_skill': {'parameters': {'type': 'object', 'properties': {
                'name': {'type': 'string', 'enum': ['source-triage', 'inspect-artifacts']}}}},
            'forsic_mission': {'parameters': {'type': 'object', 'properties': {
                'tool_name': {'type': 'string', 'enum': ['forsic_hash', 'forsic_read']},
                'tool_arguments': {'anyOf': [{'title': 'forsic_hash'}, {'title': 'forsic_read'}]}}}},
        }
        for name, schema in schemas.items():
            registry.register(name=name, toolset='forsic', schema=schema, handler=lambda args: '{}',
                check_fn=no_cache_check_fn(lambda name=name: current_policy().tool_enabled(name)),
                dynamic_schema_overrides=lambda name=name, schema=schema: schema_overrides(name, schema),
                pin_config_sensitive=True)
        patch('tools.registry.registry', registry).start()
        patch.object(model_tools, 'registry', registry).start()
        def fresh():
            return registry.get_definitions(set(schemas), quiet=True)
        def persist(session_id, pin):
            with sqlite3.connect(self.path) as db:
                db.execute('INSERT OR REPLACE INTO sessions(id,tool_names) VALUES(?,?)', (session_id, json.dumps(pin)))
        db = SimpleNamespace(update_session_tool_names=persist)
        old = fresh()
        persist('synthetic-reopen', {'version': tool_pin_version(), 'tools': old})
        self.server._sessions['lazy'] = {'agent': None, 'profile_home': str(self.home),
                                        'history_lock': threading.Lock(), 'goal_user_paused': True}
        for enabled in (False, True):
            response = self.put(tools={'forsic_hash': enabled}, skills={'source-triage': enabled})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['applied'])
            built = fresh()
            agent = SimpleNamespace(tools=deepcopy(built), valid_tool_names={tool['function']['name'] for tool in built},
                session_id='synthetic-reopen', enabled_toolsets=['forsic'], disabled_toolsets=[], _session_db=db)
            # Native first-turn restoration consumes the persisted pre-edit pin.
            pin = runtime._pin(self.home, 'synthetic-reopen')
            _restore_pinned_tools(agent, {'tool_names': json.dumps(pin)})
            keyed = lambda tools: {tool['function']['name']: tool for tool in tools}
            self.assertEqual(keyed(agent.tools), keyed(built))
            self.assertEqual(runtime._pin(self.home, 'synthetic-reopen')['tools'], agent.tools)
            self.assertEqual(agent.valid_tool_names, {tool['function']['name'] for tool in built})
            self.assertTrue(self.server._sessions['lazy']['goal_user_paused'])
            # A second cold reopen under unchanged settings preserves exact bytes.
            stable = deepcopy(agent.tools)
            agent.tools = deepcopy(built)
            db.update_session_tool_names = Mock(side_effect=persist)
            _restore_pinned_tools(agent, {'tool_names': json.dumps(runtime._pin(self.home, 'synthetic-reopen'))})
            self.assertEqual(agent.tools, stable)
            db.update_session_tool_names.assert_not_called()
            db.update_session_tool_names = persist


class NativePinPolicyTests(unittest.TestCase):
    def test_native_prefix_refresh_honors_policy_and_same_name_schema_change(self):
        from tools import mcp_tool_agent as native
        from tools.registry import ToolRegistry, no_cache_check_fn
        registry = ToolRegistry()
        state = {'enabled': True, 'enum': ['old']}
        registry.register(name='fixture_policy', toolset='fixture',
            schema={'parameters': {'type': 'object', 'properties': {}}}, handler=lambda args: '{}',
            check_fn=no_cache_check_fn(lambda: state['enabled']), pin_config_sensitive=True,
            dynamic_schema_overrides=lambda: {'parameters': {'type': 'object', 'properties': {
                'value': {'enum': state['enum']}}}})
        registry.register(name='fixture_legacy', toolset='fixture',
            schema={'parameters': {'type': 'object', 'properties': {}}}, handler=lambda args: '{}',
            check_fn=no_cache_check_fn(lambda: False))
        initial = registry.get_definitions({'fixture_policy'}, quiet=True)
        initial.insert(0, {'type': 'function', 'function': {'name': 'fixture_legacy', 'description': 'pinned'}})
        agent = SimpleNamespace(tools=initial, valid_tool_names={'fixture_policy', 'fixture_legacy'},
            enabled_toolsets=['fixture'], disabled_toolsets=[], _session_db=Mock(), session_id='synthetic-refresh')
        def definitions(**kwargs):
            return registry.get_definitions({'fixture_policy', 'fixture_legacy'}, quiet=True)
        with patch('tools.registry.registry', registry), patch('model_tools.get_tool_definitions', side_effect=definitions), \
             patch.object(native, '_reinject_post_build_tools', return_value=set()), \
             patch.object(native, '_reinject_authorized_dynamic_tools'), \
             patch.object(native, '_drop_side_agent_tools', side_effect=lambda _, defs, names: (defs, names)):
            for enabled, enum in ((False, ['off']), (True, ['enabled']), (True, ['changed'])):
                state.update(enabled=enabled, enum=enum)
                native.refresh_agent_mcp_tools(agent, preserve_prefix=True)
                by_name = {item['function']['name']: item['function'] for item in agent.tools}
                self.assertEqual('fixture_policy' in by_name, enabled)
                self.assertIn('fixture_legacy', by_name)
                if enabled:
                    self.assertEqual(by_name['fixture_policy']['parameters']['properties']['value']['enum'], enum)
                self.assertEqual(agent.valid_tool_names, set(by_name))
            stable = deepcopy(agent.tools)
            agent._session_db.reset_mock()
            native.refresh_agent_mcp_tools(agent, preserve_prefix=True)
            self.assertEqual(agent.tools, stable)
            agent._session_db.update_session_tool_names.assert_not_called()

    def test_prefix_merge_drops_only_unavailable_config_sensitive_tools(self):
        from tools.mcp_tool_agent import _merge_preserving_prefix
        def definition(name, value):
            return {'type': 'function', 'function': {'name': name, 'description': value}}
        old = [definition('fixture_legacy', 'stable'), definition('fixture_policy', 'old')]
        fresh = []
        merged, names = _merge_preserving_prefix(old, fresh, {'fixture_legacy', 'fixture_policy'},
                                                  config_sensitive={'fixture_policy'})
        self.assertEqual(merged, old[:1])
        self.assertEqual(names, {'fixture_legacy'})
        fresh = [definition('fixture_policy', 'new')]
        merged, names = _merge_preserving_prefix(old, fresh, {'fixture_legacy', 'fixture_policy'},
                                                  config_sensitive={'fixture_policy'})
        self.assertEqual(merged, [old[0], fresh[0]])

    def test_legacy_same_code_pin_keeps_old_bytes(self):
        from tools import mcp_tool_agent as native
        from tools.registry import ToolRegistry
        registry = ToolRegistry()
        schema = {'parameters': {'type': 'object', 'properties': {}}}
        registry.register(name='fixture_legacy', toolset='fixture', schema=schema, handler=lambda args: '{}')
        fresh = [{'type': 'function', 'function': {'name': 'fixture_legacy', **schema, 'description': 'fresh'}}]
        old = deepcopy(fresh)
        old[0]['function']['description'] = 'pinned'
        agent = SimpleNamespace(tools=fresh, valid_tool_names={'fixture_legacy'}, _session_db=None)
        with patch('tools.registry.registry', registry):
            native.restore_agent_tool_prefix(agent, {'version': native.tool_pin_version(), 'tools': old})
        self.assertEqual(agent.tools, old)


if __name__ == '__main__':
    unittest.main()
