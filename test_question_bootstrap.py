"""Synthetic native bindings and real atomic question writer; no model/network."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from hermes_cli.goals import GoalState, GoalContract, GoalManager, goal_identity
from forsic_plugin.evidence import Case
from forsic_plugin import question_bootstrap as bootstrap
from forsic_plugin.report_driven import host


class QuestionBootstrapTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.intake = self.root / 'intake'
        self.home = self.root / 'native'; self.home.mkdir()
        env = patch.dict(os.environ, {'HERMES_HOME': str(self.home)})
        env.start(); self.addCleanup(env.stop)
        self.native = self.home / 'state.db'
        self.sid = 'synthetic-session'
        self.goal = GoalState(goal='Investigate the user-selected synthetic records', created_at=42.0)
        case_root = self.intake / 'cases' / 'synthetic'; case_root.mkdir(parents=True)
        evidence = self.root / 'evidence'; evidence.mkdir()
        self.manifest = case_root / 'case.json'
        self.manifest.write_text(json.dumps(dict(case_id='SYN-bootstrap', evidence_root=str(evidence),
            output_root=str(case_root / 'results'), scope='선택한 합성 기록',
            question='합성 기록에서 침해가 확인되는가?', synthetic=True)))
        self.case = Case(self.manifest)
        self.bind(self.sid)
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,end_reason TEXT,source TEXT,model_config TEXT)')
            db.execute('CREATE TABLE state_meta(key TEXT PRIMARY KEY,value TEXT)')
        self.session(self.sid, goal=self.goal)

    def bind(self, sid):
        path = self.intake / (hashlib.sha256(sid.encode()).hexdigest() + '.json')
        path.write_text(json.dumps({'manifest': str(self.manifest)}))

    def session(self, sid, *, goal=None, parent=None, end=None):
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?)', (sid, parent, end, 'cli', '{}'))
            if goal:
                db.execute('INSERT OR REPLACE INTO state_meta VALUES (?,?)', ('goal:' + sid, goal.to_json()))

    def run_bootstrap(self, **changes):
        args = dict(intake_root=self.intake, native_db=self.native, investigation_turn=True,
            turn_id='synthetic-native-turn', expected_case_id='SYN-bootstrap',
            expected_goal_id=goal_identity(self.goal), expected_conversation_id=self.sid)
        args.update(changes)
        sid = args.pop('session_id', self.sid)
        return bootstrap.bootstrap_question(sid, **args)

    def save(self, *, goal_id=None, conversation=None, operation='agent-answer'):
        return host.invoke(self.case, dict(action='question', payload=dict(operation_id=operation,
            question='의미 있는 기존 질문', scope='선택한 합성 기록', answer='현재 제한적인 답과 한계',
            gaps=['원본의 추가 연결 미확인']), _goal_id=goal_id or goal_identity(self.goal),
            _session_id=conversation or self.sid, _question_contract='v2'))

    def test_seed_is_scoped_neutral_and_real_writer_is_idempotent_across_reinitialization(self):
        first = self.run_bootstrap()
        self.assertEqual(first['status'], 'saved', first)
        state = host.current(Case(self.manifest))
        self.assertEqual(len(state['questions']), 1)
        q = state['questions'][0]
        self.assertEqual(q['question'], self.case.config['question'])
        self.assertEqual(q['scope'], self.case.config['scope'])
        self.assertEqual(q['answer'], bootstrap.PROVISIONAL_ANSWER)
        self.assertEqual(q['session_id'], self.sid)
        self.assertEqual(q['goal_id'], goal_identity(self.goal))
        self.assertEqual(q['work_state'], 'active')
        self.assertEqual(q['priority'], 'unassessed')
        self.assertEqual(q['claim_refs'], [])
        for field in ('gaps', 'missions', 'tests', 'assessments', 'sources', 'hypotheses'):
            self.assertEqual(state[field], [], field)
        before = self.case.db.read_bytes()
        retry = self.run_bootstrap(turn_id='restart-native-turn')
        self.assertEqual(retry['status'], 'existing_question')
        self.assertEqual(self.case.db.read_bytes(), before)
        self.assertEqual(bootstrap.operation_id('SYN-bootstrap', goal_identity(self.goal), self.sid),
                         first['operation_id'])

    def test_meaningful_owned_question_is_never_duplicated_or_overwritten(self):
        self.save()
        before = host.current(self.case)
        raw = self.case.db.read_bytes()
        self.assertEqual(self.run_bootstrap()['status'], 'existing_question')
        self.assertEqual(host.current(self.case), before)
        self.assertEqual(self.case.db.read_bytes(), raw)

    def test_original_optional_user_question_is_preserved_verbatim_without_invented_subquestions(self):
        extra = '계정 "합성 사용자"의 실행 여부는?\n확인되지 않은 연결도 구분해줘.'
        config = dict(self.case.config, onboarding={'question': extra})
        self.manifest.write_text(json.dumps(config))
        self.assertEqual(self.run_bootstrap()['status'], 'saved')
        questions = host.current(Case(self.manifest))['questions']
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]['question'], config['question'] + '\n' + extra)

    def test_other_goal_and_conversation_are_not_adopted_or_modified(self):
        self.save(goal_id='foreign-goal', conversation='foreign-session')
        old = host.current(self.case)['questions'][0]
        self.assertEqual(self.run_bootstrap()['status'], 'saved')
        questions = host.current(self.case)['questions']
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0], old)

    def test_wrong_case_goal_conversation_and_session_do_not_write(self):
        before = self.case.db.read_bytes()
        for field in ('expected_case_id', 'expected_goal_id', 'expected_conversation_id'):
            with self.subTest(field=field):
                result = self.run_bootstrap(**{field: 'foreign'})
                self.assertEqual(result['reason'], 'native_ownership_changed')
        result = self.run_bootstrap(session_id='unbound-session')
        self.assertEqual(result['reason'], 'no_explicit_case_binding')
        self.assertEqual(self.case.db.read_bytes(), before)

    def test_paused_done_and_missing_goals_do_not_seed(self):
        before = self.case.db.read_bytes()
        for status in ('paused', 'done', 'cleared'):
            self.goal.status = status; self.session(self.sid, goal=self.goal)
            result = self.run_bootstrap()
            self.assertIn(result['reason'], ('native_goal_not_active', 'native_ownership_changed'))
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('DELETE FROM state_meta')
        self.assertEqual(self.run_bootstrap()['status'], 'unavailable')
        self.assertEqual(self.case.db.read_bytes(), before)

    def test_orphaned_binding_and_goal_without_a_native_session_do_not_seed(self):
        before = self.case.db.read_bytes()
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('DELETE FROM sessions WHERE id=?', (self.sid,))
        self.assertEqual(self.run_bootstrap()['reason'], 'native_session_unavailable')
        self.assertEqual(self.case.db.read_bytes(), before)

    def test_reads_compaction_and_status_only_call_do_not_even_resolve_for_a_write(self):
        with patch.object(bootstrap, 'resolve_context') as resolve:
            for changes in ({'investigation_turn': False}, {'turn_id': ''}):
                self.assertEqual(self.run_bootstrap(**changes)['status'], 'not_applicable')
            resolve.assert_not_called()

    def test_unavailable_binding_and_writer_failures_are_precise_nonthrowing_results(self):
        before = self.case.db.read_bytes()
        with patch.object(bootstrap, 'resolve_context', side_effect=ValueError('binding unavailable')):
            result = self.run_bootstrap()
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['error'], 'binding unavailable')
        with patch.object(bootstrap.host, 'invoke', side_effect=sqlite3.OperationalError('database is locked')):
            result = self.run_bootstrap()
            self.assertEqual(result['error_type'], 'OperationalError')
            self.assertEqual(result['error'], 'database is locked')
        self.assertEqual(self.case.db.read_bytes(), before)

    def test_pause_or_goal_replacement_before_save_prevents_write(self):
        original = bootstrap.resolve_context
        for replacement in ('paused', 'new-goal'):
            self.goal.status = 'active'; self.session(self.sid, goal=self.goal)
            calls = []
            def resolve(*args, **kwargs):
                calls.append(1)
                if len(calls) == 2:
                    updated = GoalState.from_json(self.goal.to_json())
                    if replacement == 'paused': updated.status = 'paused'
                    else: updated.created_at += 1
                    self.session(self.sid, goal=updated)
                return original(*args, **kwargs)
            with patch.object(bootstrap, 'resolve_context', side_effect=resolve):
                self.assertEqual(self.run_bootstrap()['reason'], 'native_ownership_changed_before_save')
        self.assertFalse(any(q.get('session_id') for q in host.current(self.case)['questions']))

    def test_pause_or_goal_replacement_during_case_writer_lock_wait_prevents_save(self):
        invoke = host.invoke
        for change in ('pause', 'replace'):
            with self.subTest(change=change):
                self.session(self.sid, goal=self.goal)
                entered = threading.Event(); proceed = threading.Event()
                def waiting_writer(*args, **kwargs):
                    entered.set()
                    if not proceed.wait(timeout=10):
                        raise ValueError('Synthetic case writer was not released')
                    return invoke(*args, **kwargs)
                with patch.object(bootstrap.host, 'invoke', side_effect=waiting_writer):
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(self.run_bootstrap)
                        try:
                            self.assertTrue(entered.wait(timeout=10))
                            with self.case.connect() as db:
                                db.execute('BEGIN IMMEDIATE')
                                proceed.set()
                                updated = GoalState.from_json(self.goal.to_json())
                                if change == 'pause': updated.status = 'paused'
                                else: updated.created_at += 1
                                self.session(self.sid, goal=updated)
                        finally:
                            proceed.set()
                        result = future.result(timeout=15)
                self.assertEqual(result['status'], 'unavailable', result)
                self.assertIn('before atomic save', result['error'])
                with self.case.connect() as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='note'").fetchone()[0], 0)

    def test_compression_resume_without_a_note_uses_existing_conversation_owner(self):
        self.session(self.sid, goal=self.goal, end='compression')
        child = 'synthetic-compressed-session'
        self.session(child, parent=self.sid)
        self.assertEqual(self.run_bootstrap(session_id=child)['status'], 'saved')
        q = host.current(self.case)['questions'][0]
        self.assertEqual(q['session_id'], self.sid)
        self.assertEqual(self.run_bootstrap(session_id=child)['status'], 'existing_question')

    def test_concurrent_bootstraps_use_one_durable_operation(self):
        barrier = threading.Barrier(2)
        invoke = host.invoke
        def together(*args, **kwargs):
            barrier.wait(timeout=10)
            return invoke(*args, **kwargs)
        with patch.object(bootstrap.host, 'invoke', side_effect=together):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: self.run_bootstrap(), range(2)))
        self.assertTrue(all(r['status'] in ('saved', 'existing_question') for r in results), results)
        self.assertEqual(len(host.current(self.case)['questions']), 1)
        with self.case.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='note'").fetchone()[0], 1)

    def test_agent_save_between_precheck_and_commit_wins_without_bootstrap_write(self):
        invoke = host.invoke
        def racing(case, args):
            invoke(self.case, dict(action='question', payload=dict(operation_id='racing-agent',
                question='조사 중 저장한 답', scope='선택한 합성 기록', answer='중요한 관측을 보존한 기존 답'),
                _goal_id=goal_identity(self.goal), _session_id=self.sid, _question_contract='v2'))
            return invoke(case, args)
        with patch.object(bootstrap.host, 'invoke', side_effect=racing):
            result = self.run_bootstrap()
        self.assertEqual(result['status'], 'existing_question', result)
        questions = host.current(self.case)['questions']
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]['answer'], '중요한 관측을 보존한 기존 답')

    def test_missing_original_question_or_scope_is_not_inferred_from_evidence_or_goal(self):
        for key in ('question', 'scope'):
            config = dict(self.case.config); config.pop(key)
            self.manifest.write_text(json.dumps(config))
            self.assertEqual(self.run_bootstrap()['reason'], 'original_question_or_scope_unavailable')
        self.assertFalse(any(q.get('session_id') for q in host.current(self.case)['questions']))

    def test_native_gate_matches_real_kickoff_resume_and_continuation_without_status_heuristics(self):
        for goal in (self.goal,
                     GoalState(goal=self.goal.goal, subgoals=['Retain unknowns']),
                     GoalState(goal=self.goal.goal, contract=GoalContract(verification='Check the records')),
                     GoalState(goal=self.goal.goal, contract=GoalContract(verification='Check the records'),
                               subgoals=['Preserve scope'])):
            manager = GoalManager.__new__(GoalManager)
            manager._state = goal
            # Use native pure continuation rendering without reloading a manager's store.
            manager._evaluation_expected = True
            self.assertTrue(bootstrap.is_native_investigation_turn(goal, goal.goal))
            self.assertTrue(bootstrap.is_native_investigation_turn(goal, manager.next_continuation_prompt()))
            self.assertFalse(bootstrap.is_native_investigation_turn(goal, '현재 진행 상황을 알려줘'))
            self.assertFalse(bootstrap.is_native_investigation_turn(goal, 'Explain this unrelated UI setting'))
            goal.status = 'paused'
            self.assertFalse(bootstrap.is_native_investigation_turn(goal, goal.goal))


if __name__ == '__main__':
    unittest.main()
