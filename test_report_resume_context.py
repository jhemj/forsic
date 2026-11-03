"""Native handoff restores authored report navigation without adopting old work."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from hermes_cli.goals import GoalState, goal_identity
from forsic_plugin import investigation_context as context
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.template_contract import TEMPLATE_VERSION
from forsic_plugin.turn_context import assemble, section_id
import test_investigation_context as fixtures


class ReportResumeContextTests(unittest.TestCase):
    setUp = fixtures.InvestigationContextTests.setUp
    bind = fixtures.InvestigationContextTests.bind
    session = fixtures.InvestigationContextTests.session
    save_note = fixtures.InvestigationContextTests.save_note
    kwargs = fixtures.InvestigationContextTests.kwargs

    def handoff(self):
        from forsic_plugin.report_driven.views import bundle
        self.original_id = 'Q-' + self.note['note_id']
        self.saved = bundle(self.case, host.current(self.case), question_ids=[self.original_id])
        previous = goal_identity(self.goal)
        self.goal = GoalState(goal='Rewrite the selected report from retained results.', created_at=2.0,
            runtime_followup_of=previous, runtime_followup_user_row_id=7)
        self.session(self.sid, goal=self.goal)
        request = deepcopy(self.note)
        request.update(note_id='editorial-request', question=self.goal.goal,
                       goal_id=goal_identity(self.goal), evidence_ids=[], citations=[])
        self.case.record('note', request, self.sid)

    def authored(self, **changes):
        self.case.record('report_narrative', {
            'schema': 'forsic-report-narrative-2', 'template_version': TEMPLATE_VERSION,
            'question_ids': [self.original_id], 'goal_id': goal_identity(self.goal),
            'session_id': self.sid, 'basis_id': 'synthetic-basis', 'revision': 1,
            'draft_revision': 1, 'draft_hash': 'synthetic-draft', 'fields': {},
            'review': None, 'saved_at': 1, **changes}, self.sid)

    def test_native_resume_and_compression_restore_report_but_not_historical_write_scope(self):
        self.handoff()
        self.authored()
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('compressed-editorial', parent=self.sid)
        before = {p: p.read_bytes() for p in (self.case.db, self.native)}
        for sid in (self.sid, 'compressed-editorial'):
            with self.subTest(session=sid), patch('socket.socket', side_effect=AssertionError('No inference/network')):
                projected = context.post_context_compaction(**self.kwargs(sid), include_runtime_clock=False)
                self.assertEqual(projected['status'], 'ready', projected)
                text = projected['context']
                body = json.loads(text)
                self.assertLessEqual(len(text.encode()), context.MAX_CONTEXT_BYTES)
                report = body['reports']
                self.assertEqual(report['bundle_id'], self.saved['bundle_id'])
                self.assertEqual(report['question_ids'], [self.original_id])
                self.assertEqual(report['authoring']['draft_revision'], 1)
                self.assertFalse(report['authoring']['ready'])
                self.assertFalse(report['artifact_is_completion'])
                self.assertTrue(report['authoring']['missing_groups'])
                self.assertEqual(report['read_call']['arguments'],
                                 {'action': 'compose', 'question_ids': [self.original_id]})
                self.assertEqual([q['question_id'] for q in body['answer_updates']['questions']],
                                 ['Q-editorial-request'])
                self.assertEqual({q['id'] for q in body['active_questions']}, {'Q-editorial-request'})
                self.assertEqual(body['work_inventory']['planned_missions'], 0)
                envelope = json.loads(assemble('Principles', section_ids=(section_id('Principles'),),
                                              projection=projected, max_chars=10000))
                self.assertEqual(envelope['projection']['context']['reports'], report)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_no_saved_selection_does_not_adopt_previous_report(self):
        self.handoff()
        self.authored(goal_id='other-goal')
        projected = context.post_context_compaction(**self.kwargs(), include_runtime_clock=False)
        self.assertEqual(projected['status'], 'ready')
        report = json.loads(projected['context'])['reports']
        self.assertEqual(report['status'], 'none_for_this_scope')
        self.assertNotIn('question_ids', report)

    def test_report_and_current_answer_survive_long_pending_review_navigation(self):
        from forsic_plugin import independent_review as review
        self.handoff()
        self.authored()
        for i in range(16):
            self.case.record('tool_result', {'tool': 'forsic_search', 'path': f'log-{i}',
                'search_text': 'selected', 'coverage_complete': False, 'scan_exhausted': False,
                'bytes_scanned': 80, 'next_cursor': {'state': 'retained'}}, self.sid)
        resolved = context.resolve_context(**self.kwargs())
        state = host.current(self.case)
        projected_review = review.review_state(review.native_report_review_scope(resolved), state)
        findings = [{'review_id': 'a'*64, 'finding_id': 'finding-'+str(i)*24,
                     'question': str(i)+'질'*179} for i in range(5)]
        projected_review.update(status='stale', requires_followup=True, pending_findings=findings,
                                pending_finding_ids=[f['finding_id'] for f in findings])
        with patch.object(review, 'review_state', return_value=projected_review):
            text, _, _ = context.project_context(resolved, state)
        body = json.loads(text)
        self.assertLessEqual(len(text.encode()), context.MAX_CONTEXT_BYTES)
        self.assertEqual([q['id'] for q in body['active_questions']], ['Q-editorial-request'])
        self.assertEqual(body['reports']['question_ids'], [self.original_id])
        self.assertEqual(body['independent_review']['pending_count'], 5)
        self.assertEqual(body['independent_review']['lookup']['arguments'], {'action': 'review_inbox'})
        self.assertEqual(body['omitted']['active_questions'], 0)
        self.assertGreater(body['omitted']['observed_scope'], 0)

    def test_foreign_selection_is_unavailable_not_replaced_with_old_report(self):
        self.handoff()
        self.authored(question_ids=['Q-foreign'])
        projected = context.post_context_compaction(**self.kwargs(), include_runtime_clock=False)
        self.assertEqual(projected['status'], 'unavailable')

    def test_report_selection_requires_native_human_handoff(self):
        self.handoff()
        self.goal.runtime_followup_user_row_id = None
        self.session(self.sid, goal=self.goal)
        self.authored()
        projected = context.post_context_compaction(**self.kwargs(), include_runtime_clock=False)
        self.assertEqual(projected['status'], 'ready')
        self.assertEqual(json.loads(projected['context'])['reports']['status'], 'none_for_this_scope')


if __name__ == '__main__':
    unittest.main()
