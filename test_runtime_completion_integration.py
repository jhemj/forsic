"""H59/H60/H63: deterministic native recovery → checkpoint → real case/report writes.

Only clock, provider replies, gateway submission/transport and plugin registration are
replaced. Native CAS, recovery owner receipts, case state, report review and rendering
are real temporary stores. This is not a live model E2E or a wall-clock soak.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS, MethodType
import unittest
from unittest.mock import Mock, patch
import uuid

from agent import chat_completion_helpers as helpers, conversation_loop as loop, runtime_checkpoint, turn_finalizer
from agent.session_persistence import SessionPersistenceMixin
from forsic_plugin import independent_review, report_work
from forsic_plugin.evidence import Case
from forsic_plugin.investigation_context import resolve_context
from forsic_plugin.notes import note
from forsic_plugin.report_driven import host
from hermes_cli import goals, goals_runtime, goals_completion, goals_recovery, plugins
from hermes_state import SessionDB
from tui_gateway import goal_recovery, prompt_turn, session_lifecycle
import test_report_completion_work as report_fixture

CONTRACT = {'required_formats': ['html'], 'required_review': True,
            'layout_required': False, 'completion_target': 'final_ready'}
GOOD = {'decision': 'continue', 'reason': '저장된 근거를 확인할 유용한 작업 존재',
        'findings': '합성 사건의 요청 및 생성 기록 확인', 'limitations': '공통 작업 식별 미확인',
        'next_tests': '현재 판단과 근거를 대조한 보고서 저장'}


class _Agent(SessionPersistenceMixin):
    pass


class RuntimeCompletionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.home = self.root / 'native'; self.home.mkdir()
        self.intake = self.root / 'intake'; self.intake.mkdir()
        self.db_path = self.home / 'state.db'; self.db = SessionDB(self.db_path)
        self.addCleanup(lambda: self.db.close())
        self.stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': str(self.home)}))
        self.clock = NS(now=1000.)
        self.fake_time = NS(time=lambda: self.clock.now, monotonic=lambda: self.clock.now)
        for module in (goals, goals_runtime, goals_completion, goals_recovery, loop):
            self.stack.enter_context(patch.object(module, 'time', self.fake_time))
        self.stack.enter_context(patch.object(goals, '_get_session_db', lambda: self.db))
        self.stack.enter_context(patch.object(goals, 'configured_runtime_budget', lambda: 86400.))
        self.stack.enter_context(patch.object(goals, 'judge_goal', side_effect=AssertionError('No extra goal judge')))
        config = {'model': {'provider': 'custom', 'default': 'synthetic', 'base_url': 'http://127.0.0.1:1/v1'}}
        self.stack.enter_context(patch('hermes_cli.config.load_config_readonly', return_value=config))
        self.stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('No network')))
        self.stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('No network')))
        self.review_calls = []
        def reviewer(**kwargs):
            self.review_calls.append(deepcopy(kwargs))
            return iter([NS(choices=[NS(index=0, delta=NS(content='{"findings":[]}'), finish_reason='stop')], usage=None)])
        self.stack.enter_context(patch('agent.auxiliary_client.call_llm', side_effect=reviewer))
        self.stack.enter_context(patch('agent.relay_llm.complete_logical_call', Mock()))
        self.cases = {}; self.submissions = []; self.replies = {}; self.provider_calls = []
        for label in ('alpha', 'beta'):
            self._make_case(label)
        self.stack.enter_context(patch.object(plugins, 'has_hook', lambda name: name == 'goal_work_projection'))
        def hook(name, **kwargs):
            if name != 'goal_work_projection': return []
            return [report_work.goal_work_projection(**kwargs, intake_root=self.intake,
                native_db=self.db_path, enabled=True, executive_followup=False)]
        self.stack.enter_context(patch.object(plugins, 'invoke_hook', hook))
        def provider(agent, messages, request_id):
            def complete(_):
                self.provider_calls.append((agent.session_id, deepcopy(messages)))
                response = self.replies[agent.session_id].pop(0)
                if isinstance(response, Exception): raise response
                return response
            return complete
        self.stack.enter_context(patch.object(helpers, '_checkpoint_summary_attempt', provider))
        self.stack.enter_context(patch('hermes_cli.profiles.profiles_to_serve', lambda **kw: [('default', self.home)]))
        self.server, self.live = self._server('first-process')

    def _make_case(self, label):
        root = self.intake / 'cases' / label; evidence = root / 'evidence'; evidence.mkdir(parents=True)
        for name, content in [('request.log', f'case={label} job=maintenance requested target=/opt/task\n'),
                              ('process.log', f'case={label} process=task run_id={label}-77 state=created\n')]:
            (evidence / name).write_text(content)
        manifest = root / 'case.json'
        manifest.write_text(json.dumps({'case_id': 'SYN-' + label, 'label': label, 'evidence_root': str(evidence),
            'output_root': str(root / 'results'), 'scope': label + ' synthetic observations',
            'question': '요청과 생성 기록은 같은 작업인가?', 'synthetic': True}))
        case = Case(manifest); sid = 'native-' + label
        self.db.create_session(sid, 'desktop', cwd=str(self.home))
        manager = goals.GoalManager(sid); state = manager.set('Complete synthetic ' + label + ' report', max_turns=32)
        state.runtime_policy = goals_completion.make_policy(86400., CONTRACT)
        self.db.set_meta('goal:' + sid, state.to_json())
        (self.intake / (hashlib.sha256(sid.encode()).hexdigest() + '.json')).write_text(json.dumps({'manifest': str(manifest)}))
        sources = [json.loads(case.invoke('forsic_read', {'path': name}, sid)) for name in ('request.log', 'process.log')]
        saved = note(case, {'action': 'save', 'question': '요청과 생성 기록은 같은 작업인가?',
            'answer': label + ': 이름의 유사성만으로 공통 작업 여부 미확정',
            'evidence_ids': [s['evidence_id'] for s in sources], 'alternatives': ['서로 다른 작업 가능성'],
            'critical_gaps': ['공통 작업 identity 연결 미확인'], 'gaps': ['승인·최종 실행 성공은 범위 밖'],
            'next_checks': ['공통 작업 식별 기록 확인']})['note']
        saved = deepcopy(saved); saved['revision'] += 1
        saved.update(goal_id=goals.goal_identity(state), session_id=sid)
        saved.setdefault('investigation', {})['priority'] = 'decision_critical'
        case.record('note', saved, sid)
        self.cases[label] = NS(case=case, sid=sid, goal=state, manager=manager, sources=sources, note=saved)

    def _server(self, owner):
        live = {}
        server = NS(Path=Path, contextlib=__import__('contextlib'), time=self.fake_time, threading=threading, uuid=uuid,
            logger=logging.getLogger(__name__), _session_turn_admission=session_lifecycle._session_turn_admission,
            _session_home=lambda session: self.home, _launch_home=lambda: self.home,
            _session_profile_runtime_scope=lambda session, **kw: nullcontext(),
            _session_goal_paused=lambda session: bool((s := goals.load_goal(session['session_key'])) and (s.status != 'active' or s.pause_requested)),
            _find_live_session_by_key=lambda key, profile: next(((k,s) for k,s in live.items() if s['session_key']==key), None),
            _stored_session_runtime_overrides=lambda row: {'provider_override': 'same-synthetic-provider'},
            _ensure_active_session_slot=lambda *a: None,
            _claim_active_session_slot=lambda *a, **kw: (NS(release=lambda: None), None),
            _detached_ws_transport=object(), _start_agent_build=lambda sid,s: s.update(agent=NS(session_id=s['session_key'])),
            _wait_agent=lambda *a, **kw: None)
        goal_recovery.register(server); prompt_turn.register(server)
        server._GOAL_RECOVERY_OWNER = owner
        server._start_goal_recovery = lambda: owner  # deterministic native poll tick; no real timer
        server._load_cfg = lambda: {}; server._emit = Mock(); server._hook_failure = lambda what, exc: (_ for _ in ()).throw(exc)
        server._deferred_session_record = lambda key, **kw: {'session_key': key, 'history_lock': threading.RLock(),
            'running': False, 'agent': None, 'profile_home': kw['profile_home'], 'source': kw['source']}
        server._claim_or_reuse_live = lambda sid,key,record,lease: live.__setitem__(sid,record)
        def submit(rid, sid, session, text, **kwargs):
            self.assertTrue(kwargs['respect_goal_pause'])
            self.assertTrue(server._ack_goal_recovery_start(session, kwargs['display_metadata']))
            claim_id = kwargs['display_metadata']['goal_recovery']['claim_id']
            self.submissions.append((session['session_key'], claim_id))
            self.db.append_message(session['session_key'], 'assistant', 'Resumed ' + session['session_key'], message_uid=claim_id)
            server._finish_goal_recovery(session, {'completed': True, 'failed': False, 'final_response': 'Recovered'},
                                         goals.goal_identity(goals.load_goal(session['session_key'])))
            session['running'] = False
            return True
        server._run_prompt_submit = submit
        return server, live

    def _defer(self, label):
        item = self.cases[label]
        agent = NS(session_id=item.sid, _flush_status_buffer=lambda: None)
        result = loop._compression_deferred_result(agent, [], 0)
        raw, status, _ = self.server._turn_outcome(result)
        self.assertEqual(status, 'yielded')
        session = {'session_key': item.sid, 'profile_home': str(self.home), 'agent': agent}
        self.server._goal_followup_after_turn(label, session, result, status, raw,
            expected_goal_identity=goals.goal_identity(goals.load_goal(item.sid)))
        state = goals.load_goal(item.sid); rec = state.runtime_recovery.get('context', {})
        self.assertEqual(state.status, 'active')
        self.assertEqual(rec.get('next_owner'), 'native_goal_recovery', 'ownerless yielded goal')
        self.assertGreater(rec['next_due'], self.clock.now)
        return rec['original_deadline']

    def _restart_and_wake(self, labels):
        self.db.close(); self.db = SessionDB(self.db_path)
        self.server, self.live = self._server('replacement-process')
        self.clock.now += 31
        self.server._sweep_goal_recovery()
        self.assertEqual({sid for sid,_ in self.submissions}, {self.cases[label].sid for label in labels})
        for label in labels:
            item = self.cases[label]; state = goals.load_goal(item.sid); rec = state.runtime_recovery['context']
            self.assertEqual(rec['status'], 'recovered'); self.assertEqual(rec['attempts'], 1)
            self.assertEqual(len(rec['receipts']), 2)
            self.assertIn(rec['receipts'][-1]['claim_id'], {row['message_uid'] for row in self.db.get_messages(item.sid)})

    def _checkpoint(self, label, threshold, replies):
        item = self.cases[label]; self.replies[item.sid] = list(replies)
        agent = _Agent(); agent._session_db = self.db; agent.session_id = item.sid
        agent._session_db_created = True; agent._last_flushed_db_idx = 0; agent._flushed_db_message_ids = set()
        agent._interrupt_requested = False; agent._runtime_milestone_threshold = threshold
        agent._runtime_handoff_identity = goals_runtime.runtime_identity(goals.load_goal(item.sid))
        agent._runtime_turn_identity = agent._runtime_handoff_identity
        agent._runtime_milestone_decision = None; agent.suppress_status_output = True; agent.api_mode = 'chat_completions'
        agent._handle_max_iterations = MethodType(helpers.handle_max_iterations, agent)
        rows = [{'role': 'user', 'content': 'Checkpoint ' + label}]
        text, reason, interrupted = turn_finalizer._resolve_runtime_checkpoint(agent, None, 'runtime_budget_handoff', False, False, rows, 1)
        self.assertFalse(interrupted)
        agent._flush_messages_to_session_db(rows); agent._runtime_checkpoint_row = rows[-1]
        committed = turn_finalizer._commit_runtime_checkpoint(agent, rows, text, reason, [])
        return agent, rows, committed

    def _ordinary_final(self, label):
        item = self.cases[label]
        resolved = resolve_context(item.sid, intake_root=self.intake, native_db=self.db_path)
        self.case = item.case
        self.native = {'_session_id': resolved['conversation_id'], '_goal_id': resolved['goal_id'],
            '_independent_review_options': {'enabled': True, 'executive_followup': False, 'session_id': item.sid,
                'intake_root': str(self.intake), 'native_db': str(self.db_path)}}
        # Reuse the existing synthetic authoring fixture; every write uses host.invoke.
        report_fixture.ReportCompletionWorkTests.author(self)
        report = host.invoke(item.case, {'action': 'render', **self.native})
        result = host.invoke(item.case, {'action': 'finalize', 'bundle_id': report['bundle_id'], **self.native})
        self.assertEqual(result['delivery_status'], 'independent_review_rerender_required', result)
        self.assertTrue(result['review']['final_review_current'], result['review'])
        report = host.invoke(item.case, {'action': 'render', **self.native})
        result = host.invoke(item.case, {'action': 'finalize', 'bundle_id': report['bundle_id'], **self.native})
        self.assertTrue(result['finalized'], result)
        self.assertEqual(len(self.review_calls), 1, 'unchanged report must reuse its current review')
        with item.case.connect() as db:
            selected = [json.loads(row[0]) for row in db.execute("SELECT data FROM events WHERE kind='report_finalized'")]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['bundle_id'], report['bundle_id'])
        return report

    def _assert_continue_receipt(self, label, threshold, expected_attempts):
        state = goals.load_goal(self.cases[label].sid)
        receipt = next(r for r in state.runtime_checkpoints if r['threshold_seconds'] == threshold)
        self.assertEqual(receipt['decision'], 'continue', 'native must not force model finish')
        self.assertEqual(state.runtime_recovery['checkpoint']['attempts_used'], expected_attempts, 'retry counter reset')
        source = next(r for r in self.db.get_messages(self.cases[label].sid) if r['message_uid'] == receipt['source_message_id'])
        self.assertEqual(json.loads(source['display_metadata']['runtime_milestone']['raw_response'])['decision'], receipt['decision'])
        return receipt

    def _scenario(self, *, second_error=False):
        labels = ('alpha', 'beta') if second_error else ('alpha',)
        original = {label: self._defer(label) for label in labels}
        self._restart_and_wake(labels)
        for label in labels:
            self.assertEqual(goals.load_goal(self.cases[label].sid).runtime_recovery['context']['original_deadline'], original[label])
        self.clock.now = 1000. + 12.4*3600
        if second_error:
            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(self._checkpoint, 'alpha', 43200, ['invalid-json', json.dumps(GOOD)])
                b = pool.submit(self._checkpoint, 'beta', 43200, [TimeoutError('synthetic unknown delivery')])
                self.assertEqual(a.result()[2], 'runtime_checkpoint_decided')
                b.result()
        else:
            self.assertEqual(self._checkpoint('alpha', 43200, ['invalid-json', json.dumps(GOOD)])[2], 'runtime_checkpoint_decided')
        first = self._assert_continue_receipt('alpha', 43200, 2)
        self.db.close(); self.db = SessionDB(self.db_path)
        self.clock.now = 1000. + 18*3600
        # The native cold-start reserve starts closing at 18h; a real continue
        # receipt may not erase that intent or reopen broad discovery.
        agent = NS(_session_db=self.db, session_id=self.cases['alpha'].sid,
            _runtime_turn_identity=goals_runtime.runtime_identity(goals.load_goal(self.cases['alpha'].sid)))
        blocked,_ = goals_completion.admit_tool_request(agent, 'forsic_shell', {'action':'run','command':'find / -type f'}, 'broad-search')
        self.assertTrue(blocked, 'guidance without enforced discovery boundary')
        self.assertEqual(self._checkpoint('alpha', 64800, [json.dumps(GOOD)])[2], 'runtime_checkpoint_decided')
        second = self._assert_continue_receipt('alpha', 64800, 1)
        self.assertNotEqual(first['source_message_id'], second['source_message_id'])
        state = goals.load_goal(self.cases['alpha'].sid)
        self.assertEqual(state.runtime_recovery['checkpoint']['total_attempts'], 3)
        self.assertEqual(goals_runtime.runtime_phase(state), 'closing')
        blocked,_ = goals_completion.admit_tool_request(agent, 'forsic_shell', {'action':'run','command':'find / -type f'}, 'after-continue')
        self.assertTrue(blocked)
        self.clock.now = 1000. + 19*3600
        report = self._ordinary_final('alpha')
        work = goals_completion.read_work_projection(self.cases['alpha'].sid, goals.load_goal(self.cases['alpha'].sid))
        self.assertEqual(work['artifact_status'], 'final_ready', work)
        self.assertFalse(work['review_work']['final_review_needed'])
        self.assertTrue(work['artifact_work']['review_satisfied'])
        self.assertEqual(work['artifact_work']['bundle_id'], report['bundle_id'])
        receipt = goals_completion.settle_native(self.db, self.cases['alpha'].sid,
            expected_identity=agent._runtime_turn_identity, reason='synthetic_final_ready', pause=True)
        self.assertEqual(receipt['artifact_status'], 'final_ready')
        self.assertTrue(receipt['artifact_paths']); self.assertEqual(receipt['delivery_status'], 'local_ready')
        self.assertLess(receipt['elapsed_seconds'], 86400. - state.runtime_policy['settlement_seconds'])
        alpha_settlement = goals.load_goal(self.cases['alpha'].sid).runtime_settlement
        self.assertEqual(alpha_settlement['artifact_paths'], receipt['artifact_paths'])
        self.assertEqual(alpha_settlement['artifact_status'], 'final_ready')
        if second_error:
            beta = self.cases['beta']; before = goals.load_goal(beta.sid)
            self.assertEqual(before.runtime_recovery['checkpoint']['total_attempts'], 1)
            self.assertEqual(before.runtime_checkpoints, [])
            self.clock.now = 1000. + 86400.
            partial = goals_completion.settle_native(self.db, beta.sid,
                expected_identity=goals_runtime.runtime_identity(before), reason='synthetic_hard_cap', pause=True)
            self.assertEqual(partial['artifact_status'], 'partial_checkpoint')
            self.assertFalse(partial['artifact_paths'])
            self.assertFalse(list(beta.case.output.glob('independent-review-*-request.json')))
            self.assertFalse(list(beta.case.output.glob('report-bundle-*')))
            body = json.loads(self.review_calls[0]['messages'][1]['content'])
            reviewed = {source['source_id'] for source in body['sources']}
            self.assertTrue(reviewed.intersection(source['evidence_id'] for source in self.cases['alpha'].sources))
            self.assertFalse(reviewed.intersection(source['evidence_id'] for source in beta.sources))
            a_rows = {r['message_uid'] for r in self.db.get_messages(self.cases['alpha'].sid)}
            b_rows = {r['message_uid'] for r in self.db.get_messages(beta.sid)}
            self.assertFalse(a_rows & b_rows)
            for item in self.cases.values():
                self.assertEqual(goals.load_goal(item.sid).runtime_budget_seconds, 86400.)
            self.assertEqual(goals.load_goal(self.cases['alpha'].sid).runtime_settlement, alpha_settlement)
        return report

    def test_H59_deferred_repair_continue_current_review_real_artifact_and_selection(self):
        self._scenario()

    def test_H60_two_active_cases_restart_one_error_preserves_separate_authority(self):
        self._scenario(second_error=True)

    def test_H63_classification_only_mutant_is_killed_by_owner_oracle(self):
        with patch.object(self.server, '_register_goal_recovery', return_value='pending'):
            with self.assertRaisesRegex(AssertionError, 'ownerless'):
                self._scenario()

    def test_H63_forced_18h_finish_mutant_is_killed_by_source_decision_oracle(self):
        real = runtime_checkpoint.parse_decision
        def forced(text):
            result = real(text)
            return {**result, 'decision':'finish'} if self.clock.now >= 1000.+64800 else result
        with patch.object(runtime_checkpoint, 'parse_decision', forced):
            with self.assertRaisesRegex(AssertionError, 'force model finish'):
                self._scenario()

    def test_H63_guidance_only_mutant_is_killed_by_actual_admission_oracle(self):
        with patch.object(goals_completion, 'admit_tool_request', lambda agent,name,args,call: (None,args)):
            with self.assertRaisesRegex(AssertionError, 'guidance without enforced'):
                self._scenario()

    def test_H63_retry_reset_mutant_is_killed_by_persisted_counter_oracle(self):
        real = goals_completion.claim_recovery
        def reset(db,sid,kind,**kwargs):
            state = goals.load_goal(sid); state.runtime_recovery.pop(kind, None)
            db.set_meta('goal:'+sid,state.to_json())
            return real(db,sid,kind,**kwargs)
        with patch.object(goals_completion, 'claim_recovery', reset):
            with self.assertRaisesRegex(AssertionError, 'retry counter reset'):
                self._scenario()


if __name__ == '__main__': unittest.main()
