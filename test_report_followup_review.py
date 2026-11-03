"""Native accepted-human provenance carries report dialogue, never writer authority."""
from copy import deepcopy
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

from forsic_plugin import independent_review as review
from forsic_plugin.investigation_context import report_followup_selection, native_goal_binding
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.report_reference import scope_from_args, question_in_scope
from hermes_cli.goals import GoalState, goal_identity
from hermes_cli.goals_runtime import RUNTIME_HANDOFF_REASON
import test_executive_followup as executive_fixtures
import test_independent_review as review_fixtures
from test_report_driven import fixture


class ReportFollowupReviewTests(unittest.TestCase):
    setUp = executive_fixtures.ExecutiveFollowupTests.setUp
    bind = executive_fixtures.ExecutiveFollowupTests.bind
    session = executive_fixtures.ExecutiveFollowupTests.session
    save_note = executive_fixtures.ExecutiveFollowupTests.save_note
    setup_review = executive_fixtures.ExecutiveFollowupTests.setup_review
    add_sources = executive_fixtures.ExecutiveFollowupTests.add_sources
    kwargs = executive_fixtures.ExecutiveFollowupTests.kwargs
    request_paths = executive_fixtures.ExecutiveFollowupTests.request_paths
    resolved = executive_fixtures.ExecutiveFollowupTests.resolved
    prepare = executive_fixtures.ExecutiveFollowupTests.prepare
    response = executive_fixtures.ExecutiveFollowupTests.response
    call = executive_fixtures.ExecutiveFollowupTests.call
    answer = executive_fixtures.ExecutiveFollowupTests.answer

    def handoff(self, rounds=2):
        self.prepare()
        for _ in range(rounds):
            self.call()
            self.answer(review.review_state(self.resolved())['pending_findings'])
        self.previous = self.resolved()
        self.selected = [q['id'] for q in review._question_records(self.previous)]
        self.case.record('report_narrative', {'schema':'forsic-report-narrative-2',
            'goal_id': self.previous['goal_id'], 'question_ids':self.selected,
            'draft_hash':'previous-report', 'revision':1, 'draft_revision':0, 'basis_id':'prior-basis', 'fields':{},
            'sections':{'synthesis':{'text':'Prior supported explanation.'}}}, self.sid)
        self.goal = GoalState(goal='Regenerate the selected report with all material hypotheses and limits.',
            created_at=50.0, runtime_followup_of=self.previous['goal_id'], runtime_followup_user_row_id=1)
        self.session(self.sid, goal=self.goal)
        self.receipt = {'previous_goal_id': self.previous['goal_id'], 'goal_id':goal_identity(self.goal),
            'accepted_user_row_id':1, 'previous_status':'paused', 'previous_paused_reason':RUNTIME_HANDOFF_REASON}
        with sqlite3.connect(self.native) as db:
            db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, active INTEGER, display_kind TEXT, display_metadata TEXT)')
            db.execute('INSERT INTO messages VALUES (1,?,?,?,?,?)', (self.sid,'user',1,None,
                json.dumps({'runtime_handoff_followup':self.receipt})))
        self.case.record('report_narrative', {'schema':'forsic-report-narrative-2',
            'goal_id':goal_identity(self.goal), 'question_ids':self.selected, 'draft_hash':'current-report',
            'revision':2, 'draft_revision':0, 'basis_id':'current-basis', 'fields':{}, 'session_id':self.sid,
            'sections':{'synthesis':{'text':'Strongest supported explanation; competing hypothesis; material limits.'}}}, self.sid)
        return review.report_review_scope(self.resolved(), self.selected)

    def before_first_compose(self, rounds=2):
        resolved = self.handoff(rounds)
        with self.case.connect() as db:
            db.execute("DELETE FROM events WHERE kind='report_narrative' AND json_extract(data,'$.goal_id')=?",
                       (resolved['goal_id'],))
            db.commit()
        binding = native_goal_binding(self.sid, intake_root=self.intake, native_db=self.native)
        return {'_session_id':self.sid, '_goal_id':resolved['goal_id'],
            '_report_source_goal_id':binding['report_source_goal_id'],
            '_report_source_question_ids':binding['report_source_question_ids']}

    def test_before_first_compose_keeps_old_rounds_and_never_dispatches_new_round_one(self):
        self.before_first_compose()
        before = review.review_state(self.resolved())
        self.assertEqual(len(before['executive_followup']['rounds']), 2)
        self.assertEqual(len(before['dispositions']), 10)
        self.assertFalse(before['final_review_current'])
        self.assertEqual(before['followup_action'], 'compose_report')
        for stage in ('final', 'final', 'exploration'):
            result = review.goal_advisory_context(**self.kwargs(stage=stage, executive_followup=True, turns_used=20))
            self.assertEqual(result['status'], 'report_composition_required')
            self.assertTrue(result['requires_followup'])
            self.assertEqual(result['recovery']['arguments'], {'action':'compose','question_ids':self.selected})
            self.assertEqual(len(self.calls), 2)
            self.assertFalse(review.review_state(self.resolved())['final_review_current'])

    def test_default_public_compose_then_save_restores_same_dialogue_and_dispatches_only_round_three(self):
        from test_report_narrative import ReportNarrativeTests
        from forsic_plugin.report_driven.narrative import basis, material
        args = self.before_first_compose()
        packet = host.invoke(self.case, {'action':'compose', 'section':'major_findings', **args})
        self.assertEqual(packet['question_ids'], self.selected)
        author = ReportNarrativeTests('test_native_render_requests_authored_prose_not_blank_template')
        author.case = self.case
        _, selected = basis(host.current(self.case), packet['question_ids'])
        keys = [row['key'] for row in material(selected) if row['kind']=='question'
                or row['kind']=='gap' and row.get('impact') in ('material','decision_critical')]
        saved = host.invoke(self.case, {'action':'compose', 'section':'major_findings',
            'operation_id':'first-native-compose-save', 'edit_ref':packet['save_contract']['edit_ref'],
            'question_ids':packet['question_ids'], 'draft':author.draft('major_findings',packet,keys=keys), **args})
        self.assertTrue(saved['saved'])
        after = review.review_state(self.resolved())
        self.assertEqual(len(after['executive_followup']['rounds']), 2)
        self.assertEqual(len(after['dispositions']), 10)
        self.assertFalse(after['final_review_current'])
        self.assertEqual(after['followup_action'], 'next_review_round')
        result = self.call()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual([row['round'] for row in result['executive_followup']['rounds']], [1,2,3])

    def test_explicit_and_current_authored_selection_precede_exact_source_default(self):
        from forsic_plugin.report_driven.narrative import selection
        args = self.before_first_compose()
        state = host.current(self.case)
        subset = self.selected[:1]
        self.assertEqual(selection(state, {**args, 'question_ids':subset}), subset)
        self.assertEqual(selection(state, args), self.selected)
        state['report_narratives'].append({'goal_id':args['_goal_id'], 'session_id':self.sid,
            'question_ids':subset, 'revision':99, 'saved_at':999})
        self.assertEqual(selection(state, args), subset)
        self.assertEqual(selection(state, {**args, 'question_ids':self.selected}), self.selected)

    def test_missing_draft_is_not_a_current_receipt_even_after_all_fifteen_answers(self):
        self.before_first_compose(3)
        result = self.call()
        state = review.review_state(self.resolved())
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(result['status'], 'report_composition_required')
        self.assertTrue(result['requires_followup'])
        self.assertEqual(len(state['dispositions']), 15)
        self.assertTrue(state['executive_followup']['complete'])
        self.assertFalse(state['final_review_current'])

    def test_new_normal_goal_default_remains_its_own_questions(self):
        from forsic_plugin.report_driven.narrative import selection
        self.prepare()
        state = host.current(self.case)
        args = {'_session_id':self.sid, '_goal_id':self.resolved()['goal_id']}
        expected = sorted(q['id'] for q in state['questions'] if q['goal_id']==args['_goal_id'])
        self.assertEqual(selection(state, args), expected)

    def test_two_prior_rounds_dispatch_only_the_missing_third_under_current_goal(self):
        resolved = self.handoff(2)
        paths = self.request_paths()
        original = {path:hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        inherited = review.review_state(resolved)
        self.assertEqual(len(inherited['executive_followup']['rounds']), 2)
        self.assertEqual(len(inherited['dispositions']), 10)
        self.assertFalse(inherited['final_review_current'])
        result = self.call(report_question_ids=self.selected)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(result['executive_followup']['rounds'][-1]['round'], 3)
        self.assertEqual(len(review.review_state(resolved)['dispositions']), 10)
        self.assertEqual(len(review.review_state(resolved)['pending_findings']), 5)
        checkpoint = review.goal_advisory_context(**self.kwargs(stage='exploration',executive_followup=True,turns_used=20))
        self.assertNotEqual(checkpoint['status'], 'unavailable')
        self.assertEqual(len(checkpoint['pending_finding_ids']), 5)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(review.review_state(self.resolved())['executive_followup']['rounds']), 3)
        newest = max(self.request_paths(), key=lambda path:review._read(path)['created_ns'])
        self.assertEqual(review._read(newest)['identity']['goal_id'], goal_identity(self.goal))
        self.assertEqual({path:hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}, original)
        packet = json.loads(self.calls[-1]['messages'][1]['content'])
        self.assertEqual(len(packet['executive_followup']['previous_round']['questions']), 5)
        self.assertIn('competing hypothesis', packet['analyst_state']['report_draft']['content'])

    def test_complete_old_dialogue_requires_actual_current_draft_receipt(self):
        resolved = self.handoff(3)
        before = review.review_state(resolved)
        self.assertTrue(before['executive_followup']['complete'])
        self.assertEqual(len(before['dispositions']), 15)
        self.assertFalse(before['final_review_current'])
        self.assertTrue(before['requires_followup'])
        self.fake.side_effect = lambda **kw: review_fixtures.IndependentReviewTests.empty_response(self, **kw)
        result = self.call(report_question_ids=self.selected)
        self.assertEqual(len(self.calls), 4)
        request = review._read(max(self.request_paths(), key=lambda path:review._read(path)['created_ns']))
        self.assertNotIn('executive_round', request)
        self.assertEqual(request['identity']['goal_id'], goal_identity(self.goal))
        current = review.review_state(resolved)
        self.assertTrue(current['final_review_current'])
        self.assertFalse(result['requires_followup'])
        self.assertEqual(len(current['dispositions']), 15)
        self.assertEqual(len(current['executive_followup']['rounds']), 3)

    def test_missing_or_mismatched_native_user_provenance_cannot_inherit(self):
        resolved = self.handoff(2)
        self.assertIsNotNone(report_followup_selection(resolved))
        for change in ({'previous_goal_id':'f'*64}, {'goal_id':'e'*64}, {'accepted_user_row_id':2}):
            with self.subTest(change=change):
                with sqlite3.connect(self.native) as db:
                    db.execute('UPDATE messages SET display_metadata=?',
                        (json.dumps({'runtime_handoff_followup':{**self.receipt,**change}}),))
                self.assertIsNone(report_followup_selection(resolved))
                self.assertEqual(review._receipts(resolved), [])
        with sqlite3.connect(self.native) as db:
            db.execute('UPDATE messages SET display_metadata=?,role=?',
                (json.dumps({'runtime_handoff_followup':self.receipt}),'assistant'))
        self.assertIsNone(report_followup_selection(resolved))
        self.assertEqual(review._receipts(resolved), [])

    def test_hard_runtime_stop_provenance_keeps_the_same_readonly_dialogue(self):
        from hermes_cli.goals_runtime import RUNTIME_BUDGET_REASON
        resolved = self.handoff(2)
        receipt = {**self.receipt, 'previous_paused_reason':RUNTIME_BUDGET_REASON}
        with sqlite3.connect(self.native) as db:
            db.execute('UPDATE messages SET display_metadata=?',
                (json.dumps({'runtime_handoff_followup':receipt}),))
        self.assertIsNotNone(report_followup_selection(resolved))
        self.assertEqual(len(review._receipts(resolved)), 2)

    def test_native_compression_preserves_user_provenance_but_rewind_revokes_it(self):
        resolved = self.handoff(2)
        with sqlite3.connect(self.native) as db:
            db.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
            db.execute('UPDATE messages SET active=0,compacted=1')
        self.assertIsNotNone(report_followup_selection(resolved))
        self.assertEqual(len(review._receipts(resolved)), 2)
        with sqlite3.connect(self.native) as db:
            db.execute('UPDATE messages SET compacted=0')
        self.assertIsNone(report_followup_selection(resolved))
        self.assertEqual(review._receipts(resolved), [])

    def test_case_conversation_branch_and_unselected_scope_cannot_inherit(self):
        resolved = self.handoff(2)
        self.assertEqual(len(review._receipts(resolved)), 2)
        self.assertEqual(review._receipts({**resolved, '_report_question_ids':[self.selected[0]]}), [])
        self.assertEqual(review._receipts({**resolved, 'conversation_id':'unrelated-conversation'}), [])
        other,_,_ = fixture(self.intake/'cases'/'unrelated')
        self.assertEqual(review._receipts({**resolved, 'case':other, 'case_id':other.config['case_id']}), [])
        self.sid += '-branch'
        self.session(self.sid, parent=resolved['session_id'], goal=self.goal,
                     config={'_branched_from':resolved['session_id']})
        self.bind(self.sid, self.case)
        branch = self.resolved()
        self.assertIsNone(report_followup_selection(branch))

    def test_foreign_tenant_cannot_resolve_report_dialogue_provenance(self):
        from forsic_plugin.tenants import bind_scope
        self.handoff(2)
        foreign = {'tenant_id':'foreign', 'profile_home':str(self.home),
                   'intake_root':str(self.root/'foreign-intake')}
        with bind_scope(foreign):
            binding = native_goal_binding(self.sid, intake_root=self.intake, native_db=self.native)
        self.assertEqual(binding['status'], 'unavailable')
        self.assertNotIn('report_source_question_ids', binding)

    def test_new_ordinary_findings_still_require_saved_responses(self):
        from forsic_plugin.report_driven.finalization import _review_gate
        resolved = self.handoff(3)
        result = self.call(report_question_ids=self.selected)
        self.assertTrue(result['requires_followup'])
        pending = review.review_state(resolved)['pending_findings']
        self.assertEqual(len(pending), 1)
        self.assertIn('pending_review_responses', result['context'])
        _, blocked = _review_gate(self.case, host.current(self.case), {'bundle_id':'synthetic'},
                                 {'enabled':True,'resolved':resolved})
        self.assertEqual(blocked['delivery_status'], 'independent_review_pending')
        self.answer(pending, limited=True)
        self.assertEqual(review.review_state(resolved)['pending_findings'], [])
        self.fake.side_effect = lambda **kw: review_fixtures.IndependentReviewTests.empty_response(self, **kw)
        self.call(report_question_ids=self.selected)
        saved = review.review_state(resolved)
        self.assertEqual(len(saved['dispositions']), 16)
        self.assertTrue(saved['final_review_current'])
        self.assertFalse(saved['requires_followup'])

    def test_exhausted_current_report_budget_stays_explicitly_incomplete(self):
        resolved = self.handoff(3)
        with patch.object(review, 'MAX_CALLS', 0):
            result = self.call(report_question_ids=self.selected)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertTrue(result['requires_followup'])
        self.assertFalse(result['followup_actionable'])
        self.assertEqual(result['diagnostic']['code'], 'final_review_budget')
        self.assertFalse(review.review_state(resolved)['final_review_current'])

    def test_operator_lift_clears_budget_diagnostic_preserves_history_and_requires_fresh_review(self):
        resolved = self.handoff(3)
        self.fake.side_effect = lambda **kw: review_fixtures.IndependentReviewTests.empty_response(self, **kw)
        for number in range(4):
            self.case.record('report_narrative', {'schema':'forsic-report-narrative-2',
                'goal_id':resolved['goal_id'], 'question_ids':self.selected,
                'draft_hash':f'changed-report-{number}', 'revision':number+3,
                'draft_revision':0, 'basis_id':'current-basis', 'fields':{}, 'session_id':self.sid,
                'sections':{'synthesis':{'text':f'Supported explanation revision {number} with remaining limits.'}}}, self.sid)
            result = self.call(report_question_ids=self.selected)
        self.assertEqual(len(self.calls), 6)  # Three executive rounds and three ordinary finals.
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(result['diagnostic']['code'], 'final_review_budget')
        before = review.review_state(resolved)
        original = {path:path.read_bytes() for path in self.request_paths()}
        self.config['forsic'] = {'independent_review':{'call_budgets':{review._goal_key(resolved):None}}}
        projected = review._with_state(review._result('received','',3500), resolved)
        self.assertNotEqual(projected.get('diagnostic',{}).get('code'), 'final_review_budget')
        self.assertTrue(projected['requires_followup'])
        self.assertFalse(review.review_state(resolved)['final_review_current'])
        self.assertEqual(projected['executive_followup'], before['executive_followup'])
        self.assertEqual(len(self.calls), 6)
        result = self.call(report_question_ids=self.selected)
        self.assertEqual(len(self.calls), 7)
        current = review.review_state(resolved)
        self.assertTrue(current['final_review_current'])
        self.assertFalse(result['requires_followup'])
        self.assertEqual(current['dispositions'], before['dispositions'])
        self.assertEqual(len(current['executive_followup']['rounds']), 3)
        self.assertEqual({path:path.read_bytes() for path in original}, original)
        self.call(report_question_ids=self.selected)
        self.assertEqual(len(self.calls), 7)

    def test_report_selection_is_read_only_and_does_not_adopt_old_writer_authority(self):
        resolved = self.handoff(2)
        binding = native_goal_binding(self.sid, intake_root=self.intake, native_db=self.native)
        self.assertEqual(binding['report_source_question_ids'], self.selected)
        scope = scope_from_args({'_session_id':self.sid, '_goal_id':resolved['goal_id'],
            '_report_source_goal_id':binding['report_source_goal_id'],
            '_report_source_question_ids':binding['report_source_question_ids']})
        old = next(q for q in host.current(self.case)['questions'] if q['id']==self.selected[0])
        self.assertTrue(question_in_scope(old, scope))
        with self.assertRaisesRegex(ValueError, 'another native goal'):
            host.invoke(self.case, {'action':'question', 'payload':{'operation_id':'forbidden-old-write',
                'question_id':old['id'], 'expected_revision':old['revision'], 'answer':'Overwrite previous authority'},
                '_session_id':self.sid, '_goal_id':resolved['goal_id'], '_session_lineage':[self.sid]})
        self.assertEqual(next(q for q in host.current(self.case)['questions'] if q['id']==old['id']), old)


if __name__ == '__main__':
    unittest.main()
