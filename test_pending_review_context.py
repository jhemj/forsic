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

    def test_resume_advice_navigates_to_pending_answer_without_adopting_proposed_test(self):
        x = self.fixture
        initial = x.start()
        finding = initial['findings'][0]
        before = deepcopy(host.current(x.case)['questions'])
        count = len(x.calls)
        with patch('agent.auxiliary_client.call_llm', side_effect=AssertionError('No extra model call')):
            advice = review.latest_advice(x.resolved())
            self.assertNotIn(finding['next_check'], advice)
            from forsic_plugin.turn_context import assemble, section_id
            projection = post_context_compaction(session_id=x.sid, intake_root=x.intake,
                                                 native_db=x.native)
            envelope = json.loads(assemble('', section_ids=[section_id('')], case=x.case,
                projection=projection, advice=advice,
                intake='The current native goal is authoritative.'))
            self.assertEqual(envelope['projection']['status'], 'ready')
            self.assertEqual(envelope['review_advice'], advice)
            self.assertNotIn(finding['next_check'], json.dumps(envelope, ensure_ascii=False))
            self.assertEqual(envelope['projection']['context']['independent_review']['pending_count'], 5)
            lookups = [item['read_call'] for item in json.loads(advice)['pending_items']]
            selected = next(call for call in lookups
                            if call['arguments']['finding_id'] == finding['finding_id'])
            self.assertEqual(selected['tool'], 'forsic_reporting')
            result = host.invoke(x.case, x.args(**selected['arguments']))
            item = result['findings'][0]
            self.assertEqual(item['question'], finding['question'])
            self.assertEqual(item['next_check'], finding['next_check'])
            self.assertIn('Unadopted', item['next_check_role'])
            self.assertEqual(item['status'], 'pending')
            self.assertEqual(item['response_input']['save_call']['tool'], 'forsic_review_response')
            self.assertEqual(item['response_input']['save_call']['arguments']['finding_id'], finding['finding_id'])
            self.assertEqual(host.current(x.case)['questions'], before)
        self.assertEqual(len(x.calls), count)

    def test_five_maximum_questions_keep_exact_navigation_without_proposed_tests(self):
        findings = [{'kind': 'executive_question', 'executive_round': 2,
                     'review_id': 'a'*64, 'finding_id': 'finding-'+str(i)*24,
                     'question': str(i)+'질'*179, 'next_check': 'UNADOPTED_TEST_'+str(i)}
                    for i in range(5)]
        state = {'status': 'received', 'review_id': 'a'*64, 'requires_followup': True,
                 'pending_findings': findings, 'pending_finding_ids': [f['finding_id'] for f in findings],
                 'executive_followup': {'complete': False, 'rounds': []}}
        original = deepcopy(state)
        with patch.object(review, 'review_state', return_value=state):
            result = review._with_state({'context': ''}, {}, 3500)
        self.assertLessEqual(len(result['context']), 3500)
        lookups = [item['read_call'] for item in json.loads(result['context'])['pending_items']]
        self.assertEqual(len(lookups), 5)
        self.assertEqual({call['arguments']['finding_id'] for call in lookups},
                         {f['finding_id'] for f in findings})
        for finding in findings:
            self.assertIn(finding['question'], result['context'])
            self.assertNotIn(finding['next_check'], result['context'])
        self.assertEqual(state, original)

    def test_long_generic_advice_keeps_whole_calls_and_explicit_omissions(self):
        findings = [{'review_id': 'b'*64, 'finding_id': 'finding-'+str(i).zfill(24),
                     'question': str(i)+'長'*999, 'next_check': 'UNADOPTED_'+str(i)}
                    for i in range(20)]
        state = {'status': 'stale', 'review_id': 'b'*64, 'requires_followup': True,
                 'pending_findings': findings, 'pending_finding_ids': [f['finding_id'] for f in findings],
                 'rejected_findings': [{'index': 21, 'reason': 'Rejected synthetic coordinate'}]}
        for limit in (3500, 1000, 400):
            with self.subTest(limit=limit), patch.object(review, 'review_state', return_value=state):
                result = review._with_state({'context': 'historical advice'}, {}, limit)
                self.assertLessEqual(len(result['context']), limit)
                context = json.loads(result['context'])
                self.assertEqual(context['pending_count'], 20)
                self.assertEqual(len(context['pending_items'])+context['omitted_pending_items'], 20)
                self.assertGreater(context['omitted_pending_items'], 0)
                self.assertEqual(context['rejected_findings_count'], 1)
                self.assertEqual(context['lookup']['arguments'], {'action': 'review_inbox'})
                for item in context['pending_items']:
                    args = item['read_call']['arguments']
                    finding = next(f for f in findings if f['finding_id'] == args['finding_id'])
                    self.assertEqual(args, {'action': 'review_inbox', 'review_id': finding['review_id'],
                                            'finding_id': finding['finding_id']})
                    if 'question' in item:
                        self.assertEqual(item['question'], finding['question'])
                    else:
                        self.assertTrue(item['question_omitted'])
                    self.assertNotIn(finding['next_check'], result['context'])

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
