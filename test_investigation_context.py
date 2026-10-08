"""Synthetic read-only case/goal projection and completion-boundary tests."""
from contextlib import ExitStack, closing
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hermes_cli.goals import GoalState, GoalContract, goal_identity
from forsic_plugin import investigation_context as context
from forsic_plugin.report_driven.host import current, invoke, find
from forsic_plugin.report_driven.investigation_state import validate_execution_context
from test_report_driven import fixture


class InvestigationContextTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.intake = self.root / 'intake'
        self.home = self.root / 'native'; self.home.mkdir()
        self.stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': str(self.home)}))
        self.native = self.home / 'state.db'
        self.case, self.sources, self.note = fixture(self.intake / 'cases' / 'synthetic')
        self.sid = 'synthetic-native-root'
        self.goal = GoalState(goal='Answer the synthetic scope', created_at=1.0,
                              contract=GoalContract(verification='Explain the supplied records'))
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,end_reason TEXT,source TEXT,model_config TEXT)')
            db.execute('CREATE TABLE state_meta(key TEXT PRIMARY KEY,value TEXT)')
        self.session(self.sid, goal=self.goal)
        self.bind(self.sid, self.case)
        self.save_note(priority='decision_critical')

    def bind(self, sid, case):
        path = self.intake / (hashlib.sha256(sid.encode()).hexdigest() + '.json')
        path.write_text(json.dumps({'manifest': str(case.manifest)}))

    def session(self, sid, *, parent=None, goal=None, end=None, config=None):
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?)',
                       (sid, parent, end, 'tui', json.dumps(config or {})))
            if goal:
                db.execute('INSERT OR REPLACE INTO state_meta VALUES (?,?)', ('goal:' + sid, goal.to_json()))

    def save_note(self, **fields):
        self.note = deepcopy(self.note)
        self.note['revision'] += 1
        self.note.update(goal_id=goal_identity(self.goal), session_id=self.sid)
        self.note.setdefault('investigation', {}).update(fields)
        self.case.record('note', self.note, self.sid)

    def kwargs(self, sid=None):
        return {'session_id': sid or self.sid, 'intake_root': self.intake, 'native_db': self.native}

    def prepare(self):
        return context.goal_evaluation_context(goal_id=goal_identity(self.goal), **self.kwargs())

    def validate(self, prepared):
        return context.goal_evaluation_context(goal_id=goal_identity(self.goal), phase='validate',
            expected_revision={'forsic': prepared['revision']}, **self.kwargs())

    def test_prepare_is_goal_bound_and_marks_model_state_not_evidence(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'incomplete')
        body = json.loads(result['context'])
        self.assertIn('NOT original evidence', body['notice'])
        self.assertEqual(body['goal_id'], goal_identity(self.goal))
        self.assertEqual(body['active_questions'][0]['priority'], 'decision_critical')
        self.assertTrue(body['active_questions'][0]['alternatives'])
        self.assertTrue(body['active_questions'][0]['next_checks'])

    def test_mixed_work_states_survive_clipping_and_do_not_create_a_verdict(self):
        resolved = context.resolve_context(**self.kwargs())
        state = current(self.case)
        q = deepcopy(state['questions'][0]); state['questions'] = []
        for i in range(40):
            value = deepcopy(q)
            value.update(id='Q-mixed-' + str(i), question='Large question ' * 200,
                         work_state='blocked_external' if i < 39 else 'active')
            state['questions'].append(value)
        text, _, _ = context.project_context(resolved, state)
        body = json.loads(text); inventory = body['work_inventory']
        self.assertEqual(inventory['important_question_states']['blocked_external'], 39)
        self.assertEqual(inventory['important_question_states']['active'], 1)
        self.assertGreater(body['omitted']['active_questions'], 0)
        self.assertNotIn('verdict', inventory)
        self.assertLessEqual(len(text.encode('utf-8')), 5000)
        self.assertTrue(body['completion']['pending_count'])

    def test_work_inventory_excludes_other_goal_and_reports_unknown_priority(self):
        self.save_note(priority='unassessed', work_state='open')
        resolved = context.resolve_context(**self.kwargs()); state = current(self.case)
        other = deepcopy(state['questions'][0]); other.update(id='Q-other', goal_id='other-goal', work_state='blocked_external')
        state['questions'].append(other)
        text, _, _ = context.project_context(resolved, state)
        body = json.loads(text)
        self.assertEqual(body['work_inventory']['priority_unassessed'], 1)
        self.assertEqual(body['work_inventory']['question_states']['blocked_external'], 0)
        self.assertEqual(body['omitted']['other_goal_or_unbound_questions'], 1)
        self.assertEqual(body['active_questions'][0]['revision'], self.note['revision'])

    def test_contextual_open_is_not_an_all_open_completion_gate(self):
        self.save_note(priority='contextual', deferred_reason='Low impact auxiliary detail')
        prepared = self.prepare()
        self.assertEqual(prepared['status'], 'ready')
        self.assertEqual(self.validate(prepared)['status'], 'ready')
        self.assertIn('Low impact', prepared['context'])

    def test_priority_unassessed_is_a_declared_classification_gap(self):
        self.save_note(priority='unassessed')
        self.assertIn('importance has not', self.prepare()['reason'])

    def test_uncertain_scoped_closure_can_finish_and_is_indexed(self):
        self.save_note(priority='decision_critical', work_state='scoped_closed',
                       closure_rationale='The supplied records cannot distinguish the competing explanations',
                       reopen_conditions=['A shared identity record becomes available'])
        result = self.prepare()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(self.validate(result)['status'], 'ready')
        body = json.loads(result['context'])
        self.assertFalse(body['active_questions'])
        self.assertEqual(len(body['closed_index']), 1)
        self.assertFalse(body['completion']['truth_approval'])

    def test_important_deferred_work_is_not_scoped_completion(self):
        for state in ('held', 'blocked_internal', 'blocked_external', 'budget_deferred'):
            with self.subTest(state=state):
                self.save_note(work_state=state, deferred_reason='A required input is missing', reopen_conditions=['Input arrives'])
                self.assertEqual(self.prepare()['status'], 'incomplete')

    def test_changed_answer_stales_done_candidate_but_receipts_do_not(self):
        self.save_note(priority='contextual')
        prepared = self.prepare()
        self.case.record('turn_complete', {'summary': 'Synthetic receipt'}, self.sid)
        self.assertEqual(self.validate(prepared)['status'], 'ready')
        self.note['answer'] = 'Updated analytical interpretation'
        self.save_note(priority='contextual')
        self.assertEqual(self.validate(prepared)['status'], 'stale')

    def test_native_goal_rescope_never_substitutes_other_context(self):
        prepared = self.prepare()
        new_goal = GoalState(goal='Different requested scope', created_at=2.0)
        self.session(self.sid, goal=new_goal)
        result = self.validate(prepared)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('context', result)

    def test_same_case_other_conversation_questions_are_not_injected(self):
        other = GoalState(goal='Independent question', created_at=5.0)
        self.session('other-conversation', goal=other)
        self.bind('other-conversation', self.case)
        result = context.goal_evaluation_context(goal_id=goal_identity(other), **self.kwargs('other-conversation'))
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn(self.note['answer'], result['context'])
        self.assertEqual(json.loads(result['context'])['omitted']['other_goal_or_unbound_questions'], 1)

    def test_native_compression_lineage_resolves_without_creating_binding(self):
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('compressed-child', parent=self.sid)
        before = set(self.intake.iterdir())
        result = context.native_goal_binding(**self.kwargs('compressed-child'))
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['session_id'], self.sid)
        self.assertEqual(result['requested_session_id'], 'compressed-child')
        self.assertEqual(result['goal_id'], goal_identity(self.goal))
        self.assertEqual(set(self.intake.iterdir()), before)

    def test_branch_without_goal_does_not_borrow_parent_goal(self):
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('branch', parent=self.sid, config={'_branched_from': self.sid})
        result = context.native_goal_binding(**self.kwargs('branch'))
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['goal_id'], '')
        compacted = context.post_context_compaction(**self.kwargs('branch'))
        self.assertNotIn(self.note['answer'], compacted['context'])

    def test_focused_session_without_goal_restores_its_own_questions(self):
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('DELETE FROM state_meta')
        focused = deepcopy(self.note)
        focused.update(note_id='focused', goal_id='', session_id=self.sid, question='Focused request', answer='Session-owned tentative assessment')
        self.case.record('note', focused, self.sid)
        binding = context.native_goal_binding(**self.kwargs())
        self.assertEqual(binding['status'], 'ready')
        self.assertEqual(binding['goal_id'], '')
        projected = context.post_context_compaction(**self.kwargs())
        self.assertEqual(projected['status'], 'ready')
        self.assertIn('Session-owned tentative assessment', projected['context'])
        self.assertNotIn(self.note['answer'], projected['context'])
        self.assertEqual(self.prepare()['status'], 'unavailable')

    def test_conflicting_case_binding_on_compression_lineage_is_unavailable(self):
        other, _, _ = fixture(self.intake / 'cases' / 'other')
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('compressed-child', parent=self.sid, goal=self.goal)
        self.bind('compressed-child', other)
        result = context.goal_evaluation_context(**self.kwargs('compressed-child'))
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('conflicting explicit case', result['reason'])

    def test_malformed_binding_is_explicit_unavailable(self):
        (self.intake / (hashlib.sha256(self.sid.encode()).hexdigest()+'.json')).write_text('[]')
        self.assertEqual(self.prepare()['status'], 'unavailable')

    def test_global_current_pointer_is_never_used(self):
        (self.intake / 'current.json').write_text(json.dumps({'manifest': str(self.case.manifest), 'session_id': self.sid}))
        self.session('unbound', goal=GoalState(goal='other', created_at=3.0))
        self.assertEqual(context.goal_evaluation_context(**self.kwargs('unbound'))['status'], 'not_applicable')

    def test_missing_goal_or_ledger_is_unavailable_not_an_empty_success(self):
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('DELETE FROM state_meta')
        self.assertEqual(self.prepare()['status'], 'unavailable')
        self.session(self.sid, goal=self.goal)
        self.case.db.unlink()
        self.assertEqual(self.prepare()['status'], 'unavailable')
        self.assertFalse(self.case.db.exists())

    def test_parent_cycle_is_explicit_failure(self):
        self.session(self.sid, parent='cycle', goal=self.goal)
        self.session('cycle', parent=self.sid)
        self.assertEqual(self.prepare()['status'], 'unavailable')

    def test_goal_bound_unassessed_results_and_stale_refs_block_completion(self):
        resolved = context.resolve_context(**self.kwargs())
        state = current(self.case)
        state['questions'][0]['priority'] = 'contextual'
        state['missions'] = [{'id': 'M-synthetic', 'version': 'v1', 'question_ref': {'kind':'question', 'id':state['questions'][0]['id'], 'version':state['questions'][0]['version']},
                              'state': 'unassessed', 'result_ids': ['retained-result']}]
        self.assertTrue(any('unassessed' in x for x in context.completion_state(resolved, state)))
        state['missions'][0]['state'] = 'completed'
        state['missions'][0]['question_ref']['version'] = 'stale'
        self.assertTrue(any('stale source' in x for x in context.completion_state(resolved, state)))

    def test_planned_mission_restores_id_version_and_next_tool(self):
        resolved = context.resolve_context(**self.kwargs())
        state = current(self.case); q = state['questions'][0]
        state['missions'] = [{'id':'M-planned', 'version':'current-mission-version',
            'question_ref':{'kind':'question','id':q['id'],'version':q['version']},
            'state':'draft', 'proposed_tool_name':'forsic_read', 'tool_arguments':{'path':'request.log'},
            'exact_target_scope':'Supplied request record', 'why_it_matters':'Distinguish request from execution'}]
        text, _, _ = context.project_context(resolved, state)
        mission = json.loads(text)['active_missions'][0]
        self.assertEqual(mission['mission_id'], 'M-planned')
        self.assertEqual(mission['mission_version'], 'current-mission-version')
        self.assertEqual(mission['tool'], 'forsic_read')

    def test_failed_actual_result_needs_assessment_and_unavailable_assessment_suffices(self):
        self.save_note(priority='contextual')
        state = current(self.case); question = state['questions'][0]
        owner = {'_goal_id': goal_identity(self.goal), '_session_id': self.sid}
        result = invoke(self.case, {**owner, 'action':'mission', 'payload':{
            'operation_id':'plan-failed-reader', 'question_id':question['id'],
            'expected_revision':question['revision'], 'gap_id':state['gaps'][0]['id'],
            'why_it_matters':'Determine whether the supplied reader can inspect the scope',
            'tool_name':'forsic_read', 'tool_arguments':{'path':'request.log'},
            'inconclusive_rule':'An unsupported read does not establish absence',
            'limits':['No execution attribution'], 'reopen_conditions':['A supported reader becomes available']}})
        mission = result['mission']
        admission = validate_execution_context(self.case, mission['id'], mission['version'],
            'forsic_read', {'path':'request.log'}, goal_id=goal_identity(self.goal), session_id=self.sid)
        started = self.case.record('tool_start', {'tool':'forsic_read', 'arguments':{'path':'request.log'},
                                                'mission_context':admission}, self.sid)
        rid = self.case.record('tool_result', {'tool':'forsic_read', 'started_id':started,
            'mission_context':admission, 'error':'Synthetic unsupported reader', 'error_type':'ValueError'}, self.sid)
        prepared = self.prepare()
        self.assertEqual(prepared['status'], 'incomplete')
        self.assertIn('execution failed', prepared['reason'])
        state = current(self.case)
        mission = find(state, 'missions', mission['id']); source = find(state, 'sources', rid)
        resolved = context.resolve_context(**self.kwargs())
        duplicate = deepcopy(state)
        duplicate['missions'][0]['state'] = 'unassessed'
        reasons = context.completion_state(resolved, duplicate)
        self.assertEqual(len(reasons), 1)  # one returned result is not counted twice through Mission/Test
        question = find(state, 'questions', question['id'])
        invoke(self.case, {**owner, 'action':'assess', 'payload':{
            'operation_id':'assess-reader-limitation', 'expected_revision':question['revision'],
            'mission_id':mission['id'], 'mission_version':mission['version'],
            'result_id':rid, 'result_version':source['version'], 'outcome':'unavailable',
            'reasoning_summary':'The failed read establishes a reader limitation, not absence',
            'answer':'The supplied reader cannot inspect this scope; no absence conclusion is drawn',
            'citations':[], 'limitations':['The affected scope remains unknown'],
            'next_check':'Reconsider only when a supported reader is available'}})
        self.assertEqual(self.prepare()['status'], 'ready')  # contextual unresolved scope is still visible

    def test_report_context_observes_freshness_without_becoming_completion_gate(self):
        from forsic_plugin.report_driven.views import bundle
        self.save_note(priority='contextual')
        before = self.prepare()
        saved = bundle(self.case, current(self.case))
        result = self.prepare()
        report = json.loads(result['context'])['reports']
        self.assertEqual(report['bundle_id'], saved['bundle_id'])
        self.assertFalse(report['stale'])
        self.assertFalse(report['artifact_is_completion'])
        self.assertEqual(self.validate(before)['status'], 'stale')
        self.note['answer'] = 'Updated assessment after report'
        self.save_note(priority='contextual')
        result = self.prepare()
        self.assertTrue(json.loads(result['context'])['reports']['stale'])
        self.assertEqual(result['status'], 'ready')  # judge assesses requested deliverable scope

    def test_report_integrity_failure_is_unavailable_not_silently_missing(self):
        from forsic_plugin.report_driven.views import bundle
        saved = bundle(self.case, current(self.case))
        Path(saved['files'][0]).write_text('changed artifact')
        result = self.prepare()
        self.assertEqual(result['status'], 'unavailable')

    def test_projection_is_utf8_bounded_with_explicit_omissions(self):
        resolved = context.resolve_context(**self.kwargs())
        state = current(self.case)
        example = state['questions'][0]
        state['questions'] = []
        for i in range(60):
            q = deepcopy(example); q.update(id='Q-' + str(i), question='긴질문' * 500,
                                            answer='긴판단' * 500, alternatives=['반론' * 500])
            state['questions'].append(q)
        text, _, _ = context.project_context(resolved, state)
        self.assertLessEqual(len(text.encode('utf-8')), 5000)
        self.assertGreater(json.loads(text)['omitted']['active_questions'], 0)
        self.assertIn('…', text)

    def test_reads_do_not_change_case_native_files_or_call_network(self):
        paths = [self.case.db, self.case.manifest, self.native, self.intake / (hashlib.sha256(self.sid.encode()).hexdigest()+'.json')]
        before = {p: p.read_bytes() for p in paths}
        with patch('socket.socket', side_effect=AssertionError('Network forbidden')), patch(
                'forsic_plugin.investigation_context.Case', wraps=context.Case) as factory:
            first = self.prepare()
            self.validate(first)
            compacted = context.post_context_compaction(**self.kwargs())
        self.assertEqual(compacted['status'], 'ready')
        self.assertTrue(all(call.kwargs.get('read_only') is True for call in factory.call_args_list))
        self.assertEqual(before, {p: p.read_bytes() for p in paths})


if __name__ == '__main__':
    unittest.main()
