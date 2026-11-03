"""Offline native review scope and saved report selection regression contracts."""
from copy import deepcopy
import json
import unittest
import uuid

from hermes_cli.goals import GoalState, goal_identity
from forsic_plugin import independent_review as review
from forsic_plugin.report_driven import host
import test_executive_followup as executive_fixture
import test_goal_advisory as advisory_fixture
import test_native_goal_evaluation as goal_fixture


class NativeReviewScopeTests(unittest.TestCase):
    _agent = advisory_fixture.GoalAdvisoryTests._agent
    _stop = advisory_fixture.GoalAdvisoryTests._stop
    pending = advisory_fixture.GoalAdvisoryTests.pending

    def test_current_goal_reaches_both_native_continuations_without_replacing_review(self):
        self.x = goal_fixture.NativeGoalEvaluationTests()
        self.x.setUp()
        self.addCleanup(self.x.doCleanups)
        objective = 'Rewrite the selected report from preserved results only; do not acquire new evidence.'
        self.x.manager.set(objective)
        self.x.plugins._hooks['goal_advisory_context'] = [self.pending]
        decision = self.x.manager.evaluate_after_turn('Draft report')
        self.assertTrue(decision['should_continue'])
        self.assertIn(objective, decision['continuation_prompt'])
        self.assertIn('review-1', decision['continuation_prompt'])
        _, messages, _, verdict = self._stop(candidate='Edited report')
        self.assertTrue(verdict.continue_turn)
        self.assertIn(objective, messages[-1]['content'])
        self.assertIn('finding-1', messages[-1]['content'])
        self.assertIn('unperformed scope', messages[-1]['content'])
        self.x.judge.assert_not_called()


class ReportReviewScopeTests(unittest.TestCase):
    setUp = executive_fixture.ExecutiveFollowupTests.setUp
    bind = executive_fixture.ExecutiveFollowupTests.bind
    session = executive_fixture.ExecutiveFollowupTests.session
    save_note = executive_fixture.ExecutiveFollowupTests.save_note
    setup_review = executive_fixture.ExecutiveFollowupTests.setup_review
    add_sources = executive_fixture.ExecutiveFollowupTests.add_sources
    kwargs = executive_fixture.ExecutiveFollowupTests.kwargs
    request_paths = executive_fixture.ExecutiveFollowupTests.request_paths
    response = executive_fixture.ExecutiveFollowupTests.response
    resolved = executive_fixture.ExecutiveFollowupTests.resolved
    prepare = executive_fixture.ExecutiveFollowupTests.prepare
    call = executive_fixture.ExecutiveFollowupTests.call

    def test_handoff_final_review_reuses_report_selection_and_keeps_all_fifteen_answers(self):
        self.prepare()
        original_id = 'Q-' + self.note['note_id']
        previous_goal = goal_identity(self.goal)
        self.goal = GoalState(goal='Rewrite selected report; preserve evidence and disclose gaps.',
            created_at=2.0, runtime_followup_of=previous_goal, runtime_followup_user_row_id=7)
        self.session(self.sid, goal=self.goal)
        # The new request is an active task, not the authored report's evidence question.
        request = deepcopy(self.note)
        request.update(note_id='editorial-request', question=self.goal.goal,
                       goal_id=goal_identity(self.goal))
        self.case.record('note', request, self.sid)
        authored = {'schema':'forsic-report-narrative-2',
            'question_ids':[original_id], 'goal_id':goal_identity(self.goal),
            'basis_id':'synthetic-basis', 'draft_hash':'selected-report', 'draft_revision':1,
            'fields':{}, 'sections':{'scope':{'text':'Selected retained observations, with limitations.'}}}
        with self.case.connect() as db:
            original_results = db.execute("SELECT count(*) FROM events WHERE kind='tool_result'").fetchone()[0]
        for number in range(1, 4):
            if number == 2:
                self.case.record('report_narrative', authored, self.sid)
            result = self.call(draft_response='Readable report draft')
            self.assertEqual(result['status'], 'received', result)
            packet = json.loads(self.calls[-1]['messages'][1]['content'])
            self.assertEqual([q['id'] for q in packet['analyst_state']['questions']],
                             ['Q-editorial-request'] if number == 1 else [original_id])
            self.assertEqual(packet['active_task']['objective_pointer'], '/goal')
            self.assertEqual(packet['goal'], self.goal.goal)
            self.assertEqual(packet['active_task']['report_question_ids'], None if number == 1 else [original_id])
            if number == 1:
                self.assertNotIn('report_draft', packet['analyst_state'])
            else:
                self.assertIn('selected-report', packet['analyst_state']['report_draft']['content'])
            scoped = review.native_report_review_scope(self.resolved())
            pending = review.review_state(scoped)['pending_findings']
            self.assertEqual(len(pending), 5)
            self.assertTrue(all(f['retrieval'] == {'action':'source','source_id':f['source_id']} for f in pending))
            for index, finding in enumerate(pending):
                written = host.invoke(self.case, {'action':'question','payload':{
                    'operation_id':uuid.uuid4().hex,
                    'question':finding['question'], 'scope':'Selected preserved report results',
                    'answer':f'Round {number}, answer {index+1}: the retained observations establish configuration only; execution remains unconfirmed.',
                    'reason':'The requested report revision does not include new original-evidence inspection.',
                    'review_responses':[{'review_id':finding['review_id'], 'finding_id':finding['finding_id'],
                        'disposition':'scope_limited', 'reason':'Compared the question with the current report scope.',
                        'limitation':'New original-evidence inspection is unperformed. Execution cannot be confirmed; reconsider if that investigation is authorized.',
                        'citation_refs':[]}]},
                    '_goal_id':goal_identity(self.goal), '_session_id':self.sid,
                    '_session_lineage':[self.sid], '_question_contract':'v2'})
                self.assertNotIn('error', written, written)
        self.assertFalse(self.call()['requires_followup'])
        state = review.review_state(review.native_report_review_scope(self.resolved()))
        self.assertTrue(state['executive_followup']['complete'])
        self.assertTrue(state['final_review_current'])
        self.assertEqual(len(state['dispositions']), 15)
        self.assertEqual(len(self.calls), 3)
        rounds = state['executive_followup']['rounds']
        self.assertEqual([r['report_context']['draft_present'] for r in rounds], [False, True, True])
        self.assertEqual([r['report_context']['question_ids'] for r in rounds], [None, [original_id], [original_id]])
        from forsic_plugin.report_driven.views import _appendix_views
        appendix = _appendix_views(host.current(self.case), independent_review=state)
        self.assertIn('1회 검토 입력 · 보고서 본문 미제공', json.dumps(appendix, ensure_ascii=False))
        self.assertFalse(review.review_state(review.report_review_scope(self.resolved(), ['Q-editorial-request']))['final_review_current'])
        self.case.record('report_narrative', {**authored, 'draft_hash':'changed-report'}, self.sid)
        self.assertFalse(review.review_state(review.native_report_review_scope(self.resolved()))['final_review_current'])
        self.case.record('report_narrative', authored, self.sid)
        self.assertTrue(review.review_state(review.native_report_review_scope(self.resolved()))['final_review_current'])
        # Changing a response question's proposition invalidates its old handling.
        answered = next(q for q in host.current(self.case)['questions'] if q.get('review_responses'))
        revised = host.invoke(self.case, {'action':'question','payload':{
            'operation_id':uuid.uuid4().hex, 'question_id':answered['id'],
            'expected_revision':answered['revision'], 'scope':'Different question scope',
            'reason':'Reassess the response under the changed scope.'},
            '_goal_id':goal_identity(self.goal), '_session_id':self.sid,
            '_session_lineage':[self.sid], '_question_contract':'v2'})
        self.assertNotIn('error', revised, revised)
        changed = review.review_state(review.native_report_review_scope(self.resolved()))
        self.assertFalse(changed['final_review_current'])
        self.assertFalse(changed['executive_followup']['complete'])
        self.assertTrue(changed['requires_followup'])
        self.assertEqual(len(self.calls), 3)
        with self.case.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='tool_result'").fetchone()[0], original_results)
        # A forged draft cannot select an unrelated question even at a native boundary.
        self.case.record('report_narrative', {'question_ids':['Q-foreign'],
            'goal_id':goal_identity(self.goal)}, self.sid)
        rejected = self.call()
        self.assertEqual(rejected['status'], 'unavailable')
        self.assertEqual(len(self.calls), 3)


if __name__ == '__main__':
    unittest.main()
