"""Real Forsic hooks + native inline clarify; synthetic callbacks, no network/model."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from contextvars import copy_context
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

# Exercise the vendored source, including new native modules that an older
# editable-install module inventory may not have discovered yet.
ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT/'upstream/hermes')]

from forsic_plugin import register
from forsic_plugin.evidence import Case
from hermes_cli import plugins
from hermes_cli.config import save_config
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from hermes_state import SessionDB
from telegram_mirror import Mirror


class NativeClarificationNotificationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.home = self.root/'home'; self.home.mkdir()
        self.stack.enter_context(patch.dict(os.environ, {'HERMES_HOME':str(self.home)}))
        token = set_hermes_home_override(self.home)
        self.stack.callback(reset_hermes_home_override, token)
        self.stack.enter_context(patch.object(plugins, '_plugin_manager', None))
        self.stack.enter_context(patch.object(plugins, '_plugin_managers_by_home', {}))
        self.stack.enter_context(patch.object(plugins, 'discover_plugins'))
        self.stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('offline test only')))
        self.stack.enter_context(patch('agent.auxiliary_client.call_llm', side_effect=AssertionError('no model calls')))
        self.intake = self.home/'intake'
        save_config({'plugins':{'enabled':['forsic'],'entries':{'forsic':{'settings':{'intake_root':str(self.intake)}}}}})
        db = SessionDB(db_path=self.home/'state.db')
        db.create_session('synthetic-session', source='web')
        db.close()
        evidence = self.root/'evidence'; evidence.mkdir()
        manifest = self.intake/'cases'/'synthetic-case'/'case.json'; manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({'case_id':'synthetic-case','label':'Synthetic case',
            'evidence_root':str(evidence),'output_root':str(self.root/'results')}))
        self.case = Case(manifest)
        (self.intake/(hashlib.sha256(b'synthetic-session').hexdigest()+'.json')).write_text(
            json.dumps({'manifest':str(manifest),'stage':'ready'}))
        plugin_path = Path(__file__).parent/'forsic_plugin'
        plugin_manifest = plugins.parse_manifest_file(plugin_path/'plugin.yaml',plugin_path,'user','')
        self.manager = plugins.get_plugin_manager()
        register(plugins.PluginContext(plugin_manifest,self.manager))
        self.addCleanup(self.manager.unload,'forsic')
        self.mirror = Mirror(self.home,self.intake,self.root/'mirror.json','synthetic-session',-123)
        self.mirror.initialize()

    def execute(self, callback, identity):
        """Use the actual pre/post hook dispatch; stub only unrelated runner I/O."""
        from agent import tool_executor as native
        args = {'questions':[{'question':'합성 사용자에게 조사 범위를 확인합니다','choices':['선택한 범위','확장 범위']}]}
        agent = SimpleNamespace(session_id='synthetic-session',_current_turn_id='turn-'+identity,
            _current_api_request_id='api-'+identity,clarify_callback=callback,verbose_logging=False,
            _tool_guardrails=SimpleNamespace(before_call=lambda *_:SimpleNamespace(allows_execution=True)))
        ref = native._ToolCallRef('clarify',args,'synthetic-session','call-'+identity,[])
        state = native._ManagedToolResult(None,args,[],False,False)
        dispatch = native._resolve_sequential_dispatch(agent,ref,[])
        with patch.object(native,'_begin_tool_execution'), \
             patch.object(native,'_run_with_activity_heartbeat',side_effect=lambda _,__,execute:execute()), \
             patch('agent.terminal_approval_batch.prepare_current_terminal'), \
             patch.object(native,'apply_transform_tool_result',side_effect=lambda _,**kw:kw['result']), \
             patch.object(native,'_commit_tool_result',return_value=None):
            state.result = native._dispatch_authorized_once(agent,state,ref,execute=dispatch.execute,
                scope_block=None,display_index=None,begin_execution=None,authorization_gate=None)
            native._publish_sequential_result(agent,[],ref,state,tool_duration=0,index=1,budget=None,
                transform_applied=dispatch.transform_applied)
        return json.loads(state.result)

    def events(self, kind):
        with self.case.connect() as db:
            return [json.loads(row['data']) for row in db.execute('SELECT data FROM events WHERE kind=? ORDER BY rowid',(kind,))]

    def check_wait_and_settlement(self, outcome, answer):
        entered, release = threading.Event(), threading.Event()
        observed = []
        def callback(questions):
            observed.extend(questions); entered.set()
            if not release.wait(5): raise TimeoutError('synthetic callback was not released')
            return {'answers':{'q0':answer} if answer is not None else {},'outcome':outcome}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(copy_context().run,self.execute,callback,outcome)
            try:
                self.assertTrue(entered.wait(5),'native clarify callback was not entered')
                requested = self.events('user_input_requested')
                self.assertEqual(len(requested),1)
                self.assertEqual(self.events('user_input_resolved'),[])
                self.assertEqual(requested[0]['native_call'],{'session_id':'synthetic-session',
                    'tool_call_id':'call-'+outcome,'turn_id':'turn-'+outcome,'api_request_id':'api-'+outcome})
                pending = self.mirror.batch()
                self.assertEqual(len(pending),1)
                self.assertTrue(pending[0][0].startswith('clarify:'))
                self.assertIn('합성 사용자에게 조사 범위를 확인합니다',pending[0][1])
                self.assertEqual(observed[0]['qid'],'q0')
            finally:
                release.set()
            result = future.result(timeout=5)
        resolved = self.events('user_input_resolved')
        self.assertEqual(len(resolved),1)
        self.assertEqual(requested[0]['request_id'],resolved[0]['request_id'])
        self.assertEqual(requested[0]['native_call'],resolved[0]['native_call'])
        self.assertEqual(self.mirror.batch(),[])
        self.assertEqual(result['outcome'],outcome)
        self.assertEqual(result['responses'][0]['user_response'],answer)
        self.assertNotIn('answers',resolved[0])

    def test_registered_native_hooks_show_waiting_question_then_hide_answered_request(self):
        self.check_wait_and_settlement('submitted','선택한 범위')

    def test_registered_native_hooks_hide_cancelled_request_without_inventing_answer(self):
        self.check_wait_and_settlement('cancelled',None)


if __name__ == '__main__': unittest.main()
