"""Explicit close uses synthetic native storage and never starts an agent."""
from contextlib import closing, ExitStack, nullcontext
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hermes_cli import goals
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from hermes_state import SessionDB
from forsic_plugin.investigations import Investigations, RequestConflict
from forsic_plugin.dashboard import plugin_api as api
from forsic_plugin.remote_policy import ForsicRemoteAccessMiddleware, ForsicRemotePolicy
from tui_gateway.turn_marker import record_turn_start, read_turn_marker


class InvestigationCloseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        (self.evidence / 'original.txt').write_text('Synthetic original preserved.\n')
        self.catalog = Investigations(self.home, self.root / 'intake')
        self.created = self.catalog.create_conversation(uuid.uuid4(), path=str(self.evidence))
        self.sid = self.created['session_id']
        self.case_id = self.created['case_id']
        self.db = SessionDB(self.home / 'state.db')
        self.addCleanup(self.db.close)
        token = set_hermes_home_override(self.home)
        self.addCleanup(reset_hermes_home_override, token)
        patched = patch.object(goals, '_get_session_db', return_value=self.db)
        patched.start(); self.addCleanup(patched.stop)
        self.goal = goals.GoalState(goal='Synthetic unresolved investigation', status='active', created_at=time.time())
        self.db.set_meta('goal:' + self.sid, self.goal.to_json())
        self.payload = dict(profile='default', request_id=str(uuid.uuid4()), session_id=self.sid,
                            goal_id=goals.goal_identity(self.goal), confirmed=True)

    def close(self, **changes):
        payload = {**self.payload, **changes}
        request_id = payload.pop('request_id')
        return self.catalog.close_investigation(request_id, case_id=self.case_id, **payload)

    def test_pause_clear_preserve_records_and_idempotency_without_success(self):
        self.goal.evaluation_receipt = {'old_success_projection': True}
        self.goal.runtime_budget_seconds = 3600
        self.goal.runtime_elapsed_seconds = 5
        self.goal.runtime_started_at = time.time() - 1
        self.db.set_meta('goal:' + self.sid, self.goal.to_json())
        case = self.catalog.get(case_id=self.case_id)
        manifest = Path(case['manifest'])
        before = manifest.read_bytes()
        self.db.append_message(self.sid, 'user', 'Unresolved synthetic question')
        record_turn_start(self.home, self.sid, 'Synthetic interrupted prompt')
        result = self.close()
        self.assertEqual(result['status'], 'closed')
        self.assertTrue(result['new_investigation']['allowed'])
        state = goals.load_goal(self.sid)
        self.assertEqual(state.status, 'cleared')
        self.assertNotEqual(state.last_verdict, 'done')
        self.assertIsNone(state.evaluation_receipt)
        self.assertIsNone(state.runtime_started_at)
        self.assertGreaterEqual(state.runtime_elapsed_seconds, 6)
        self.assertEqual(state.goal, self.goal.goal)
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual((self.evidence / 'original.txt').read_text(), 'Synthetic original preserved.\n')
        self.assertTrue(self.db.get_session(self.sid))
        self.assertEqual(self.db.get_messages(self.sid)[0]['content'], 'Unresolved synthetic question')
        self.assertEqual(self.catalog.get(case_id=self.case_id)['status'], 'closed')
        self.assertIsNone(read_turn_marker(self.home, self.sid))
        self.assertEqual(self.close(), result)

    def test_wrong_identity_and_confirmation_never_pause_or_clear(self):
        original = self.db.get_meta('goal:' + self.sid)
        for changes in ({'goal_id': 'foreign'}, {'session_id': 'foreign'}, {'confirmed': False}):
            with self.subTest(changes=changes), self.assertRaises(RequestConflict):
                self.close(**changes)
            self.assertEqual(self.db.get_meta('goal:' + self.sid), original)
        self.close()
        replacement = goals.GoalState(goal='A newer explicit goal', created_at=time.time())
        self.db.set_meta('goal:' + self.sid, replacement.to_json())
        with self.assertRaises(RequestConflict):
            self.close()
        self.assertEqual(goals.load_goal(self.sid).goal, replacement.goal)

    def native_runtime(self, stack):
        from tui_gateway import server
        # Use the real native factory: its non-reentrant Lock exposed the old
        # adapter deadlock, which a hand-built RLock fixture had concealed.
        stack.enter_context(patch.object(server, '_load_show_reasoning', return_value=False))
        stack.enter_context(patch.object(server, '_load_tool_progress_mode', return_value='full'))
        runtime = server._deferred_session_record(self.sid, cols=80, cwd=str(self.root),
            history=[], lease=None, profile_home=self.home, lazy=True)
        self.assertIsInstance(runtime['history_lock'], type(threading.Lock()))
        self.assertTrue(runtime['history_lock'].acquire(blocking=False))
        self.assertFalse(runtime['history_lock'].acquire(blocking=False))
        runtime['history_lock'].release()
        stack.enter_context(patch.object(server, '_sessions', {'live-synthetic': runtime}))
        stack.enter_context(patch.object(server, '_hermes_home', self.home))
        stack.enter_context(patch.object(server, '_session_uses_compute_host', return_value=False))
        stack.enter_context(patch.object(server, '_session_profile_runtime_scope', return_value=nullcontext()))
        stack.enter_context(patch('hermes_cli.plugins.invoke_hook'))
        stack.enter_context(patch.object(server, '_clear_pending'))
        stack.enter_context(patch.object(server, '_announce_cancelled_gateway_approvals'))
        stack.enter_context(patch('tools.approval.resolve_gateway_approval'))
        stack.enter_context(patch('tools.async_delegation.interrupt_for_session'))
        stack.enter_context(patch('agent.background_review.cancel_background_review_for_live_turn'))
        stack.enter_context(patch.object(server, '_run_prompt_submit', side_effect=AssertionError('No agent turn may start')))
        return server, runtime

    def test_live_runtime_interrupt_uses_native_helper_and_lock_then_drains_before_clear(self):
        with ExitStack() as stack:
            server, runtime = self.native_runtime(stack)
            interrupt_seen, drain_allowed, drained = threading.Event(), threading.Event(), threading.Event()
            worker_errors = []
            def hard_interrupt():
                self.assertEqual(goals.load_goal(self.sid).status, 'paused')
                self.assertIsNone(runtime['queued_prompt'])
                self.assertNotIn('queued_prompts', runtime)
                self.assertTrue(runtime['_turn_cancel_requested'])
                interrupt_seen.set()
            runtime.update(agent=SimpleNamespace(session_id=self.sid, hard_interrupt=hard_interrupt),
                running=True, queued_prompt={'text': 'queued one'}, queued_prompts=[{'text': 'queued two'}],
                _queued_prompt_generation=7)
            def synthetic_worker():
                try:
                    if not interrupt_seen.wait(3):
                        raise AssertionError('Native hard interrupt was not delivered')
                    if not drain_allowed.wait(3):
                        raise AssertionError('Test did not permit worker drain')
                    with runtime['history_lock']:
                        runtime['running'] = False
                except BaseException as exc:
                    worker_errors.append(exc)
                finally:
                    drained.set()
            worker = threading.Thread(target=synthetic_worker, daemon=True)
            runtime['_run_thread'] = worker
            worker.start()
            outcome = []
            def close_on_thread():
                try:
                    outcome.append(self.close())
                except BaseException as exc:
                    outcome.append(exc)
            closer = threading.Thread(target=close_on_thread, daemon=True)
            original_clear = goals.GoalManager.clear
            def checked_clear(manager):
                self.assertTrue(drained.is_set())
                self.assertFalse(worker.is_alive())
                self.assertFalse(runtime['running'])
                self.assertEqual(runtime['_queued_prompt_generation'], 8)
                self.assertIsNone(runtime['queued_prompt'])
                return original_clear(manager)
            with patch.object(goals.GoalManager, 'clear', checked_clear):
                closer.start()
                try:
                    self.assertTrue(interrupt_seen.wait(3), 'Actual native helper must not deadlock on history_lock')
                    self.assertEqual(goals.load_goal(self.sid).status, 'paused')
                    self.assertTrue(closer.is_alive(), 'Close must wait for live worker unwind')
                finally:
                    drain_allowed.set()
                    worker.join(3)
                    closer.join(3)
            self.assertFalse(worker.is_alive())
            self.assertFalse(closer.is_alive(), 'Close adapter deadlocked with a real native lock')
            self.assertEqual(worker_errors, [])
            self.assertEqual(len(outcome), 1)
            self.assertIsInstance(outcome[0], dict, repr(outcome[0]))
            self.assertEqual(outcome[0]['status'], 'closed')
            self.assertEqual(goals.load_goal(self.sid).status, 'cleared')
            server._run_prompt_submit.assert_not_called()

    def test_native_interrupt_racing_goal_replacement_never_clears_replacement(self):
        with ExitStack() as stack:
            server, runtime = self.native_runtime(stack)
            replacement = goals.GoalState(goal='Explicit replacement during stop', created_at=time.time())
            def hard_interrupt():
                self.db.set_meta('goal:' + self.sid, replacement.to_json())
            runtime.update(agent=SimpleNamespace(session_id=self.sid, hard_interrupt=hard_interrupt),
                           running=True, queued_prompt={'text': 'synthetic old queued work'})
            with self.assertRaises(RequestConflict):
                self.close()
            state = goals.load_goal(self.sid)
            self.assertEqual(state.goal, replacement.goal)
            self.assertEqual(state.status, 'active')
            self.assertTrue(runtime['_turn_cancel_requested'])
            self.assertIsNone(runtime['queued_prompt'])
            server._run_prompt_submit.assert_not_called()

    def test_replacement_between_confirmation_and_pause_is_preserved(self):
        original_init = goals.GoalManager.__init__
        replacement = goals.GoalState(goal='New goal after confirmation', created_at=time.time())
        def replace(manager, *args, **kwargs):
            self.db.set_meta('goal:' + self.sid, replacement.to_json())
            original_init(manager, *args, **kwargs)
        with patch.object(goals.GoalManager, '__init__', replace), self.assertRaises(RequestConflict):
            self.close()
        state = goals.load_goal(self.sid)
        self.assertEqual(state.goal, replacement.goal)
        self.assertEqual(state.status, 'active')

    def test_failed_marker_retirement_stays_paused_and_same_request_can_retry(self):
        record_turn_start(self.home, self.sid, 'Synthetic crash recovery marker')
        with patch('tui_gateway.turn_marker.clear_turn_marker'), self.assertRaises(RequestConflict):
            self.close()
        self.assertEqual(goals.load_goal(self.sid).status, 'paused')
        self.assertIsNotNone(read_turn_marker(self.home, self.sid))
        self.assertEqual(self.close()['status'], 'closed')

    def test_native_session_foreign_profile_is_rejected_before_pause(self):
        with closing(sqlite3.connect(self.home / 'state.db')) as db, db:
            db.execute('UPDATE sessions SET profile_name=? WHERE id=?', ('other', self.sid))
        with self.assertRaises(RequestConflict):
            self.close()
        self.assertEqual(goals.load_goal(self.sid).status, 'active')

    def test_runtime_build_review_and_delegation_delay_clear(self):
        for busy_kind in ('building', 'review', 'delegation'):
            with self.subTest(busy_kind=busy_kind):
                self.db.set_meta('goal:' + self.sid, self.goal.to_json())
                runtime = dict(session_key=self.sid, profile_home=self.home, history_lock=threading.RLock())
                if busy_kind == 'building':
                    runtime['_agent_build_thread'] = SimpleNamespace(is_alive=lambda: True)
                if busy_kind == 'review':
                    runtime['agent'] = SimpleNamespace(_background_review_run=object())
                server = SimpleNamespace(_sessions_lock=threading.RLock(), _sessions={'live': runtime},
                                         _interrupt_session_turn=lambda *args: None, _hermes_home=self.home)
                clock = iter((0, 3))
                with patch.dict(sys.modules, {'tui_gateway.server': server}), \
                     patch('tools.async_delegation.has_live_for_session', return_value=busy_kind == 'delegation'), \
                     patch('forsic_plugin.investigations.time', SimpleNamespace(
                         time=time.time, monotonic=lambda: next(clock), sleep=lambda _: None)), \
                     self.assertRaises(RequestConflict):
                    self.close(request_id=str(uuid.uuid4()))
                self.assertEqual(goals.load_goal(self.sid).status, 'paused')

    def test_live_turn_and_compression_guards_hold_pause_and_retry_same_request(self):
        for table, column in (('session_turn_leases', 'conversation_id'), ('compression_locks', 'session_id')):
            with self.subTest(table=table):
                self.db.set_meta('goal:' + self.sid, self.goal.to_json())
                request_id = str(uuid.uuid4())
                with closing(sqlite3.connect(self.home / 'state.db')) as db, db:
                    db.execute('INSERT INTO ' + table + '(' + column + ',holder,acquired_at,expires_at) VALUES (?,?,?,?)',
                               (self.sid, 'synthetic-holder', time.time(), time.time() + 300))
                with self.assertRaises(RequestConflict):
                    self.close(request_id=request_id)
                self.assertEqual(goals.load_goal(self.sid).status, 'paused')
                self.assertFalse(self.catalog.new_investigation()['allowed'])
                with closing(sqlite3.connect(self.home / 'state.db')) as db, db:
                    db.execute('DELETE FROM ' + table)
                self.assertEqual(self.close(request_id=request_id)['status'], 'closed')

    def test_pending_intake_has_no_close_capability_and_request_conflicts(self):
        active = self.catalog.new_investigation()['active_case']
        self.assertEqual(active['goal_id'], self.payload['goal_id'])
        self.assertTrue(active['closable'])
        self.close()
        with self.assertRaises(RequestConflict):
            self.close(profile='other')
        with closing(sqlite3.connect(self.home / 'state.db')) as db, db:
            db.execute('DELETE FROM state_meta WHERE key=?', ('goal:' + self.sid,))
        active = self.catalog.new_investigation()['active_case']
        self.assertFalse(active['closable'])
        self.assertEqual(active['goal_id'], '')
        with self.assertRaises(RequestConflict):
            self.close(request_id=str(uuid.uuid4()))

    def test_api_ownership_origin_confirmation_and_remote_route(self):
        app = FastAPI()
        app.state.initial_profile = 'default'
        app.include_router(api.router, prefix='/api/plugins/forsic')
        app.add_middleware(ForsicRemoteAccessMiddleware,
                           policy=ForsicRemotePolicy({'remote_evidence_directory_only': True}, self.home))
        client = TestClient(app, base_url='http://localhost', client=('192.0.2.20', 1234))
        url = '/api/plugins/forsic/investigations/' + self.case_id + '/close'
        with patch.object(api, 'directory', return_value=self.catalog), \
             patch('hermes_cli.profiles.get_profile_dir', return_value=self.home):
            self.assertEqual(client.post(url, json={**self.payload, 'profile': 'other'}).status_code, 403)
            self.assertEqual(client.post(url, json={**self.payload, 'confirmed': False}).status_code, 409)
            self.assertEqual(client.post(url, json={**self.payload, 'confirmed': 'true'}).status_code, 422)
            self.assertEqual(client.post(url, json=self.payload, headers={'Origin': 'http://foreign.example'}).status_code, 403)
            self.assertEqual(client.post(url.replace(self.case_id, 'foreign'), json=self.payload).status_code, 409)
            self.assertEqual(client.get(url).status_code, 403)
            result = client.post(url, json=self.payload)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['status'], 'closed')


if __name__ == '__main__':
    unittest.main()
