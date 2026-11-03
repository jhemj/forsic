"""Predispatch failure routing; local synthetic cases and fake reviewer only."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from forsic_plugin import independent_review as review
from forsic_plugin.investigation_context import resolve_context
from forsic_plugin.report_driven import host, independent_gate as gate, review_actions
from forsic_plugin.report_driven.finalization import _review_gate
import test_independent_review as fixtures


class ReviewAttemptDiagnosticsTests(unittest.TestCase):
    bind = fixtures.IndependentReviewTests.bind
    session = fixtures.IndependentReviewTests.session
    save_note = fixtures.IndependentReviewTests.save_note
    setup_review = fixtures.IndependentReviewTests.setup_review
    add_sources = fixtures.IndependentReviewTests.add_sources
    response = fixtures.IndependentReviewTests.empty_response
    kwargs = fixtures.IndependentReviewTests.kwargs
    request_paths = fixtures.IndependentReviewTests.request_paths

    def setUp(self):
        fixtures.IndependentReviewTests.setUp(self)
        self.setup_review()
        for key in ('answer', 'summary', 'last_response'):
            self.note.get('investigation', {}).pop(key, None)
        self.save_note()
        self.resolved = resolve_context(self.sid, intake_root=self.intake, native_db=self.native)
        self.ids = [q['id'] for q in review._question_records(self.resolved)]
        self.scoped = review.report_review_scope(self.resolved, self.ids)
        self.args = {'_session_id': self.resolved['conversation_id'], '_goal_id': self.resolved['goal_id'],
            '_independent_review_options': {'enabled': True, 'session_id': self.sid,
                'intake_root': str(self.intake), 'native_db': str(self.native), 'executive_followup': True}}
        self.draft()
        self.failure = review._unavailable(ValueError(
            'Complete executive question/answer history exceeds the review input bound'), 'final_packet')

    def draft(self, marker='first'):
        self.case.record('report_narrative', {'schema': 'forsic-report-narrative-2',
            'question_ids': self.ids, 'goal_id': self.resolved['goal_id'], 'revision': 1, 'draft_revision': 1,
            'draft_hash': marker, 'basis_id': 'synthetic-basis', 'fields': {},
            'sections': {'scope': {'text': 'Synthetic authored report ' + marker}}}, self.sid)

    def retain_failure(self):
        with patch.object(review, 'goal_advisory_context', return_value=self.failure) as call:
            result = gate.context(self.case, host.current(self.case), self.args,
                                  dispatch=True, question_ids=self.ids)
        self.assertEqual(call.call_count, 1)
        return result

    def test_failure_survives_finalize_and_inbox_without_becoming_pending(self):
        before = self.case.db.read_bytes()
        canonical = review.review_state(self.scoped)
        context = self.retain_failure()
        self.assertEqual(context['attempt']['diagnostic']['code'], 'executive_history_budget')
        projection, blocked = _review_gate(self.case, host.current(self.case),
            {'bundle_id': 'synthetic'}, context)
        self.assertFalse(blocked['finalized'])
        self.assertEqual(blocked['delivery_status'], 'independent_review_incomplete')
        self.assertEqual(blocked['followup_action'], 'repair')
        self.assertFalse(blocked['followup_actionable'])
        self.assertEqual(blocked['diagnostic']['phase'], 'final_packet')
        inbox = review_actions.inbox(self.case, self.args)
        self.assertEqual(inbox['status'], 'unavailable')
        self.assertEqual(inbox['pending_count'], 0)
        self.assertEqual(inbox['followup_action'], 'repair')
        self.assertEqual(inbox['diagnostic'], blocked['diagnostic'])
        self.assertEqual(review.review_state(self.scoped), canonical)
        # After compaction the bounded projection retains this same read route;
        # lookup restores the failure instead of treating pending_count=0 as success.
        from forsic_plugin.investigation_context import project_context
        text, _, _ = project_context(self.resolved, host.current(self.case))
        call = json.loads(text)['independent_review']['lookup']
        restored = host.invoke(self.case, {**call['arguments'], **self.args})
        self.assertEqual(restored['diagnostic'], blocked['diagnostic'])
        self.assertEqual(self.case.db.read_bytes(), before)
        self.fake.assert_not_called()

    def test_failure_does_not_hide_material_pending_findings(self):
        self.fake.side_effect = fixtures.IndependentReviewTests.response.__get__(self)
        review.goal_advisory_context(**self.kwargs(stage='final', report_question_ids=self.ids))
        self.retain_failure()
        inbox = review_actions.inbox(self.case, self.args)
        self.assertGreater(inbox['pending_count'], 0)
        self.assertTrue(inbox['findings'])
        self.assertEqual(inbox['followup_action'], 'respond')
        self.assertTrue(inbox['followup_actionable'])
        self.assertEqual(inbox['diagnostic']['code'], 'executive_history_budget')

    def test_retained_failure_is_not_replayed_after_report_source_or_scope_change(self):
        self.retain_failure()
        self.assertIsNotNone(gate.retained_attempt(self.scoped))
        self.assertIsNone(gate.retained_attempt({**self.scoped, 'goal_id': 'foreign-goal'}))
        self.assertIsNone(gate.retained_attempt(self.resolved))  # exact selected scope only
        self.draft('changed')
        self.assertIsNone(gate.retained_attempt(self.scoped))
        self.retain_failure()
        self.add_sources(1)
        self.assertIsNotNone(gate.retained_attempt(self.scoped))  # Unselected reads do not change this report.
        with self.case.connect() as db:
            source_id=db.execute("SELECT id FROM events WHERE kind='tool_result' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.note['evidence_ids'].append(source_id)
        self.save_note()  # Explicitly adopt the new source into the selected question.
        self.assertIsNone(gate.retained_attempt(self.scoped))

    def test_response_progress_invalidates_failure_without_staling_semantic_review(self):
        self.fake.side_effect = fixtures.IndependentReviewTests.empty_response.__get__(self)
        review.goal_advisory_context(**self.kwargs(stage='final', report_question_ids=self.ids))
        before = review.freshness(self.scoped)
        context = self.retain_failure()
        self.save_note()  # same answer, new question revision (as with focused response writes)
        self.assertEqual(review.freshness(self.scoped), before)
        self.assertIsNone(gate.retained_attempt(self.scoped))
        self.assertIsNone(gate.attempt_for_context(context))

    def test_fresh_dispatch_is_not_suppressed_and_success_replaces_failure(self):
        self.retain_failure()
        with patch.object(review, 'goal_advisory_context', return_value={
                'status': 'received', 'review_id': 'repaired', 'requires_followup': False}) as call:
            context = gate.context(self.case, host.current(self.case), self.args,
                                  dispatch=True, question_ids=self.ids)
        self.assertEqual(call.call_count, 1)
        self.assertNotIn('diagnostic', context['attempt'])
        saved = gate.retained_attempt(self.scoped)
        self.assertEqual(saved['attempt']['review_id'], 'repaired')
        self.assertNotIn('diagnostic', review_actions.inbox(self.case, self.args))

    def test_legacy_and_oversized_records_do_not_become_permanent_repair_state(self):
        path = gate._attempt_path(self.scoped)
        path.write_text(json.dumps({'identity': review._identity(self.scoped),
            'fingerprint': review.freshness(self.scoped)['evaluation_fingerprint'],
            'attempt': {'status': 'unavailable', 'review_id': ''}}))
        self.assertIsNone(gate.retained_attempt(self.scoped))
        path.write_text(' ' * 65537)
        self.assertIsNone(gate.retained_attempt(self.scoped))

    def test_only_bounded_routing_metadata_is_cached(self):
        self.failure.update(raw_response='PRIVATE_PROVIDER_BODY', request='PRIVATE_REQUEST')
        self.retain_failure()
        text = gate._attempt_path(self.scoped).read_text()
        self.assertNotIn('PRIVATE_', text)
        huge = deepcopy(self.failure)
        huge['context'] = 'x' * 40000
        retained = gate._attempt_details(huge)
        self.assertLess(len(json.dumps(retained)), 1000)
        self.assertEqual(retained['followup_action'], 'repair')


if __name__ == '__main__':
    unittest.main()
