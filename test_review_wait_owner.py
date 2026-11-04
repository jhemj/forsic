"""Live auxiliary barriers through the existing native wait/receipt boundaries."""
import threading
import time
import unittest
import contextlib
import logging
import uuid
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from forsic_plugin import independent_review as review, independent_review_overlap as overlap
from hermes_cli import goals, goals_advisory, plugins
from hermes_state import SessionDB
import test_review_overlap as fixture


class ReviewWaitOwnerTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ReviewOverlapTests('test_duplicate_invocations_and_final_boundary_do_not_start_a_second_call')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def advisory(self, value):
        with (patch.object(plugins, 'has_hook', return_value=True),
              patch.object(plugins, 'iter_hook_callbacks', return_value=[object()]),
              patch.object(plugins, 'invoke_hook', return_value=[value])):
            return goals_advisory.goal_advisory_result(session_id=self.f.sid, goal_id='scope',
                goal_text=self.f.goal.goal, turns_used=1, cancel_check=lambda: False)

    def test_live_owner_parks_actual_native_goal_and_result_reuses_original_request(self):
        from agent.turn_stop_gates import _goal_advisory_stop
        value = self.f.launch()
        self.assertEqual(value['review_wait']['state'], 'alive')
        advisory = self.advisory(value)
        db = SessionDB(self.f.root / 'wait-state.db'); self.addCleanup(db.close)
        db.set_meta('goal:' + self.f.sid, self.f.goal.to_json())
        agent = SimpleNamespace(_session_db=db, session_id=self.f.sid, _interrupt_requested=False)
        with patch.object(goals_advisory, 'goal_advisory_result', return_value=advisory):
            stopped = _goal_advisory_stop(agent, 'Saved incomplete candidate')
        self.assertTrue(stopped[2])
        saved = goals.GoalState.from_json(db.get_meta('goal:' + self.f.sid))
        self.assertEqual(saved.status, 'active')
        self.assertGreater(saved.waiting_until, time.time())
        self.assertLessEqual(saved.waiting_until, value['review_wait']['deadline_at'])
        self.assertEqual(saved.turns_used, self.f.goal.turns_used)
        self.assertTrue(agent._goal_advisory_waiting)
        with patch.object(goals, '_get_session_db', return_value=db):
            manager = goals.GoalManager(self.f.sid)
            self.assertTrue(manager.is_waiting())
            with patch('hermes_cli.goals.time.time', return_value=saved.waiting_until + 1):
                self.assertFalse(manager.is_waiting())
        settled = self.f.settle()
        self.assertNotEqual(settled['status'], 'in_progress')
        self.assertEqual(len(self.f.calls), 1)
        self.assertEqual(len(self.f.request_paths()), 1)

    def test_dead_or_unknown_worker_does_not_get_wait_or_resend(self):
        resolved = self.f.resolved()
        key = str(resolved['case'].output / ('independent-review-' + review._goal_key(resolved) + '.lock'))
        dead = threading.Thread(target=lambda: None); dead.start(); dead.join()
        job = overlap.PendingReview('retained-uncertain-request', (self.f.sid,), str(self.f.native), thread=dead)
        with overlap._lock:overlap._jobs[key] = job
        try:
            self.assertEqual(overlap.owner_status(key)['state'], 'dead')
            value = review._in_progress(resolved)
            self.assertFalse(value['followup_actionable'])
            self.assertEqual(value['diagnostic']['code'], 'review_owner_dead')
            self.assertEqual(overlap.wait_existing(key), 'owner_lost')
            self.assertTrue(job.done.is_set())
            self.assertEqual(overlap.owner_status(key)['state'], 'unknown')
            advisory = self.advisory(review._in_progress(resolved))
            self.assertEqual(goals_advisory.advisory_continuation(self.f.goal, advisory)[0], 'advisory_unavailable')
            self.assertEqual(self.f.calls, [])
        finally:
            with overlap._lock:overlap._jobs.pop(key, None)

    def test_wait_cannot_extend_original_worker_or_goal_deadline(self):
        value = self.f.launch(); advisory = self.advisory(value)
        state = self.f.goal
        state.runtime_budget_seconds = 5; state.runtime_started_at = time.time()
        state.runtime_elapsed_seconds = 0; state.runtime_accounting_known = True
        state.wall_deadline = time.time() + 2
        verdict, _ = goals_advisory.advisory_continuation(state, advisory, final_boundary=True)
        self.assertEqual(verdict, 'wait')
        self.assertLessEqual(state.waiting_until, state.wall_deadline)
        with patch('hermes_cli.goals_advisory.time.time', return_value=state.wall_deadline + 1):
            verdict, _ = goals_advisory.advisory_continuation(state, advisory, final_boundary=True)
        self.assertEqual(verdict, 'advisory_unavailable')
        self.assertEqual(len(self.f.calls), 1)

    def gateway(self, value):
        from tui_gateway import goal_recovery, session_lifecycle
        home=self.f.root/'gateway-profile';home.mkdir()
        db=SessionDB(home/'state.db');self.addCleanup(db.close)
        db.create_session(self.f.sid,'desktop',cwd=str(home))
        state=goals.GoalState.from_json(self.f.goal.to_json())
        state.runtime_budget_seconds=3600;state.runtime_started_at=time.time()
        state.runtime_elapsed_seconds=0;state.runtime_accounting_known=True;state.runtime_policy={}
        self.assertEqual(goals_advisory.advisory_continuation(state,self.advisory(value))[0],'wait')
        db.set_meta('goal:'+self.f.sid,state.to_json())
        self.f.stack.enter_context(patch.object(goals,'_get_session_db',return_value=db))
        self.f.stack.enter_context(patch('hermes_cli.profiles.profiles_to_serve',return_value=[]))
        live={};dispatches=[];scopes=[]
        work={'status':'unknown','artifact_status':'partial_checkpoint','delivery_status':'unconfirmed'}
        @contextlib.contextmanager
        def scope(session,**kwargs):
            scopes.append((session.get('profile_home'),kwargs))
            yield
        server=SimpleNamespace(Path=Path,contextlib=contextlib,time=time,threading=threading,uuid=uuid,
            logger=logging.getLogger(__name__),_session_turn_admission=session_lifecycle._session_turn_admission,
            _session_home=lambda session:home,_launch_home=lambda:home,_session_profile_runtime_scope=scope,
            _session_goal_paused=lambda session:bool((s:=goals.load_goal(session['session_key'])) and (s.status!='active' or s.pause_requested)),
            _find_live_session_by_key=lambda key,profile:next(((sid,s) for sid,s in live.items() if s['session_key']==key),None),
            _stored_session_runtime_overrides=lambda row:{'provider_override':'stored-synthetic-provider'},
            _ensure_active_session_slot=lambda *a:None,
            _claim_active_session_slot=lambda *a,**kw:(SimpleNamespace(release=lambda:None),None),
            _detached_ws_transport=object(),_start_agent_build=lambda sid,s:s.update(agent=SimpleNamespace(session_id=s['session_key'])),
            _wait_agent=lambda *a,**kw:None)
        goal_recovery.register(server);server._GOAL_RECOVERY_OWNER='synthetic-wait-owner'
        def record(key,**kwargs):
            self.assertEqual(kwargs['resume_runtime_overrides']['provider_override'],'stored-synthetic-provider')
            return {'session_key':key,'history_lock':threading.RLock(),'running':False,'agent':None,
                    'profile_home':kwargs['profile_home'],'source':kwargs['source']}
        server._deferred_session_record=record
        server._claim_or_reuse_live=lambda sid,key,record,lease:live.__setitem__(sid,record)
        def dispatch(rid,sid,session,text,**kwargs):
            self.assertTrue(server._ack_goal_recovery_start(session,kwargs['display_metadata']))
            self.assertIsNotNone(server._goal_recovery_execution_deadline(session,kwargs['display_metadata']))
            dispatches.append(kwargs['display_metadata'])
            db.append_message(self.f.sid,'assistant','Synthetic native continuation',message_uid=rid)
            return True
        server._run_prompt_submit=dispatch
        def probe(name,**kwargs):
            if name=='goal_work_projection':return [dict(work)]
            self.assertEqual(name,'goal_advisory_context');self.assertTrue(kwargs['read_only'])
            with patch.object(review,'_collect',side_effect=AssertionError('idle wake must not compose another review')):
                return [review.goal_advisory_context(**{**self.f.kwargs(),**kwargs})]
        self.f.stack.enter_context(patch.object(plugins,'has_hook',return_value=True))
        self.f.stack.enter_context(patch.object(plugins,'invoke_hook',side_effect=probe))
        return SimpleNamespace(server=server,db=db,state=state,live=live,dispatches=dispatches,scopes=scopes,work=work)

    def test_gateway_shared_tick_waits_for_actual_receipt_then_dispatches_once(self):
        value=self.f.launch();rt=self.gateway(value)
        before=rt.db.get_meta('goal:'+self.f.sid)
        self.assertTrue(rt.server._sweep_goal_recovery())
        self.assertEqual(rt.db.get_meta('goal:'+self.f.sid),before)
        self.assertFalse(rt.dispatches);self.assertEqual(len(self.f.calls),1)
        self.f.settle()
        self.assertTrue(rt.server._sweep_goal_recovery())
        self.assertEqual(len(rt.dispatches),1);self.assertEqual(len(self.f.calls),1)
        saved=goals.load_goal(self.f.sid)
        self.assertEqual(saved.advisory_progress['status'],'dispatched')
        self.assertEqual(saved.advisory_progress['original_deadline'],rt.state.advisory_progress['original_deadline'])
        self.assertEqual(saved.turns_used,rt.state.turns_used)
        self.assertTrue(rt.scopes)
        rt.server._sweep_goal_recovery();self.assertEqual(len(rt.dispatches),1)
        session=next(iter(rt.live.values()))
        rt.server._finish_goal_recovery(session,{'completed':True,'failed':False},goals.goal_identity(saved))
        session['running']=False
        rt.server._sweep_goal_recovery();self.assertEqual(len(rt.dispatches),1)
        self.assertEqual(goals.load_goal(self.f.sid).advisory_progress['status'],'resumed')

    def test_gateway_wait_honors_manual_pause_and_foreign_executor(self):
        rt=self.gateway(self.f.launch())
        before=rt.db.get_meta('goal:'+self.f.sid)
        rt.server._ensure_active_session_slot=lambda *a:'another executor owns the session'
        rt.server._sweep_goal_recovery()
        self.assertEqual(rt.db.get_meta('goal:'+self.f.sid),before);self.assertFalse(rt.dispatches)
        goals.GoalManager(self.f.sid).pause('manual stop')
        rt.server._ensure_active_session_slot=lambda *a:None
        rt.server._sweep_goal_recovery()
        self.assertFalse(rt.dispatches);self.assertEqual(goals.load_goal(self.f.sid).paused_reason,'manual stop')

    def test_gateway_unknown_request_after_restart_stops_without_replaying(self):
        rt=self.gateway(self.f.launch())
        # A process restart loses its worker; the immutable request remains unconfirmed.
        job=self.f.jobs[-1]
        with overlap._lock:
            key=next(key for key,value in overlap._jobs.items() if value is job)
            del overlap._jobs[key]
        try:
            rt.server._sweep_goal_recovery()
            saved=goals.load_goal(self.f.sid)
            self.assertEqual(saved.status,'paused')
            self.assertEqual(saved.paused_reason,'review_wait_owner_unavailable')
            self.assertFalse(rt.dispatches);self.assertEqual(len(self.f.calls),1)
            rt.server._sweep_goal_recovery();self.assertFalse(rt.dispatches)
        finally:
            with overlap._lock:overlap._jobs[key]=job

    def test_gateway_original_deadline_prevents_new_followup(self):
        rt=self.gateway(self.f.launch())
        saved=goals.load_goal(self.f.sid);saved.advisory_progress['original_deadline']=time.time()-1
        rt.db.set_meta('goal:'+self.f.sid,saved.to_json())
        rt.server._sweep_goal_recovery()
        self.assertEqual(goals.load_goal(self.f.sid).paused_reason,'review_wait_deadline')
        self.assertFalse(rt.dispatches);self.assertEqual(len(self.f.calls),1)

    def test_gateway_lost_dispatched_owner_never_replays_same_review_followup(self):
        rt=self.gateway(self.f.launch());self.f.settle()
        rt.server._sweep_goal_recovery();self.assertEqual(len(rt.dispatches),1)
        next(iter(rt.live.values()))['running']=False
        rt.server._sweep_goal_recovery()
        self.assertEqual(goals.load_goal(self.f.sid).paused_reason,'review_wait_delivery_unknown')
        self.assertEqual(len(rt.dispatches),1);self.assertEqual(len(self.f.calls),1)

    def test_gateway_manual_pause_after_claim_wins_before_actual_admission(self):
        rt=self.gateway(self.f.launch());self.f.settle()
        def dispatch(rid,sid,session,text,**kwargs):
            goals.GoalManager(self.f.sid).pause('manual stop after claim')
            self.assertFalse(rt.server._ack_goal_recovery_start(session,kwargs['display_metadata']))
            return False
        rt.server._run_prompt_submit=dispatch
        rt.server._sweep_goal_recovery()
        self.assertEqual(goals.load_goal(self.f.sid).paused_reason,'manual stop after claim')
        self.assertFalse(rt.dispatches);self.assertEqual(len(self.f.calls),1)

    def test_gateway_missing_native_runtime_settles_review_wait_without_new_request(self):
        rt=self.gateway(self.f.launch())
        rt.server._restore_goal_recovery_owner=lambda *a:None
        rt.server._sweep_goal_recovery()
        self.assertEqual(goals.load_goal(self.f.sid).paused_reason,'review_wait_owner_unavailable')
        self.assertFalse(rt.dispatches);self.assertEqual(len(self.f.calls),1)
        receipt=goals.load_goal(self.f.sid).runtime_settlement
        self.assertEqual(receipt['artifact_status'],'partial_checkpoint')
        self.assertEqual(receipt['goal_status'],'paused')
        self.assertEqual(receipt['delivery_status'],'unconfirmed')

    def test_gateway_unrelated_compression_child_cannot_hide_or_adopt_wait(self):
        rt=self.gateway(self.f.launch())
        rt.db.create_session('unrelated-child','desktop',parent_session_id=self.f.sid)
        rt.db.end_session(self.f.sid,'compression')
        child=goals.GoalState.from_json(rt.state.to_json())
        child.created_at+=1;child.goal='A different child goal';child.advisory_progress=None
        rt.db.set_meta('goal:unrelated-child',child.to_json())
        before=rt.db.get_meta('goal:unrelated-child')
        rt.server._sweep_goal_recovery()
        self.assertEqual(goals.load_goal(self.f.sid).paused_reason,'review_wait_lineage_unavailable')
        self.assertEqual(rt.db.get_meta('goal:unrelated-child'),before)
        self.assertFalse(rt.dispatches);self.assertEqual(len(self.f.calls),1)

    def test_gateway_terminal_wait_settlement_preserves_actual_draft_artifact(self):
        rt=self.gateway(self.f.launch())
        report=self.f.root/'preserved-draft.html';report.write_text('<p>Preserved partial finding</p>')
        digest=hashlib.sha256(report.read_bytes()).hexdigest()
        rt.work.update(artifact_status='draft_preserved',artifacts=[{'path':str(report),'sha256':digest}])
        rt.server._restore_goal_recovery_owner=lambda *a:None
        rt.server._sweep_goal_recovery()
        receipt=goals.load_goal(self.f.sid).runtime_settlement
        self.assertEqual(receipt['artifact_status'],'draft_preserved')
        self.assertEqual(receipt['artifact_paths'],[{'path':str(report),'sha256':digest,'bytes':report.stat().st_size}])
        self.assertEqual(receipt['reason'],'review_wait_owner_unavailable')
        self.assertEqual(receipt['goal_identity'],[rt.state.created_at,rt.state.goal])
        self.assertFalse(rt.dispatches);self.assertEqual(len(self.f.calls),1)

    def test_gateway_terminal_wait_settlement_failure_is_explicit(self):
        rt=self.gateway(self.f.launch())
        rt.server._restore_goal_recovery_owner=lambda *a:None
        with patch('hermes_cli.goals_completion.settle_native',side_effect=OSError('synthetic persistence failure')):
            rt.server._sweep_goal_recovery()
        saved=goals.load_goal(self.f.sid)
        self.assertEqual(saved.status,'paused')
        self.assertEqual(saved.runtime_settlement['status'],'settlement_failed')
        self.assertEqual(saved.advisory_progress['status'],'terminal')
        self.assertFalse(rt.dispatches);self.assertEqual(len(self.f.calls),1)


if __name__ == '__main__':unittest.main()
