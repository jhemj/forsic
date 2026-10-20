"""Pending questions survive pruning/compaction and disappear only after a saved answer."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from forsic_plugin.context_results import summarize
from forsic_plugin.investigation_context import post_context_compaction
from forsic_plugin.review_navigation import pending_items
from forsic_plugin import independent_review as review
from forsic_plugin.report_driven import host
import test_review_recovery_contract as fixtures


class PendingReviewContextTests(unittest.TestCase):
    def setUp(self):
        self.fixture = x = fixtures.ReviewRecoveryContractTests()
        x.setUp()
        self.addCleanup(x.doCleanups)

    def test_actual_review_to_compaction_to_saved_response_without_extra_model_call(self):
        x = self.fixture
        inbox = x.start()
        finding = inbox['findings'][0]
        count = len(x.calls)
        original = deepcopy(host.current(x.case)['questions'])
        with patch('agent.auxiliary_client.call_llm', side_effect=AssertionError('No extra model call')):
            advice = review.advisory_at_boundary(x.resolved())
            self.assertEqual(advice['pending_items'][0]['finding_id'], finding['finding_id'])
            before = post_context_compaction(session_id=x.sid, intake_root=x.intake, native_db=x.native)
            self.assertEqual(before['status'], 'ready', before)
            context = json.loads(before['context'])
            selected = context['independent_review']['pending_items'][0]
            self.assertEqual(selected['question'], finding['question'])
            self.assertEqual(selected['read_call']['arguments']['finding_id'], finding['finding_id'])
            self.assertEqual(context['answer_updates']['questions'][0]['expected_revision'], inbox['questions'][0]['revision'])
            self.assertEqual(host.current(x.case)['questions'], original)
            response = x.response_args(finding)['payload']
            written = x.adapter_call(response)
            self.assertNotIn('error', written, written)
            stored = host.current(x.case)['questions'][0]
            self.assertEqual(stored['review_responses'][0]['finding_id'], finding['finding_id'])
            self.assertTrue(stored['review_responses'][0]['citations'])
            after = post_context_compaction(session_id=x.sid, intake_root=x.intake, native_db=x.native)
            updated = json.loads(after['context'])
            self.assertNotIn(finding['finding_id'],
                [item['finding_id'] for item in updated['independent_review']['pending_items']])
            self.assertEqual(updated['answer_updates']['questions'][0]['expected_revision'], stored['revision'])
        self.assertEqual(len(x.calls), count)

    def test_pruned_delivery_keeps_whole_question_without_quote_or_approval(self):
        x = self.fixture
        x.start()
        advice = review.advisory_at_boundary(x.resolved())
        advice['pending_items'][0]['quote'] = 'PRIVATE_NOT_FOR_NAVIGATION'
        before = deepcopy(advice)
        result = json.loads(summarize('forsic_read', {}, json.dumps({
            'evidence_id': 'synthetic-source', 'independent_review': advice,
            'text': 'omitted original body', 'complete_file': False})))
        pending = result['independent_review']
        self.assertEqual(pending['pending_items'][0]['question'], advice['pending_items'][0]['question'])
        self.assertTrue(pending['state_at_delivery'])
        self.assertEqual(pending['role'], 'independent_advice_not_source_evidence')
        self.assertNotIn('PRIVATE_NOT_FOR_NAVIGATION', json.dumps(result))
        self.assertNotIn('omitted original body', json.dumps(result))
        self.assertEqual(advice, before)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))), 2000)

    def test_long_question_is_omitted_whole_with_exact_lookup_and_unknown_is_not_zero(self):
        state = {'status': 'stale', 'pending_findings': [
            {'review_id': 'r', 'finding_id': 'f', 'question': '중요 조건 '*1000},
            {'review_id': 'r', 'finding_id': 'g', 'question': 'Complete short question?'}]}
        result = pending_items(state, max_bytes=800)
        self.assertNotIn('question', result['pending_items'][0])
        self.assertTrue(result['pending_items'][0]['question_omitted'])
        self.assertEqual(result['pending_items'][0]['read_call']['arguments']['finding_id'], 'f')
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 800)
        result = pending_items({**state, 'status': 'unavailable'})
        self.assertEqual(result['pending_items'], [])
        self.assertIsNone(result['omitted_pending_items'])


if __name__ == '__main__':
    unittest.main()
