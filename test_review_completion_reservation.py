"""Existing receipt lock and real synthetic ledgers; auxiliary calls are fakes."""
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from forsic_plugin import independent_review as review
from forsic_plugin.independent_review_repair import completion_quota, ordinary_receipts
import test_executive_review_repair as repair_fixture
import test_independent_review as ordinary_fixture


class ReviewCompletionReservationTests(unittest.TestCase):
    def setUp(self):
        self.f = repair_fixture.ExecutiveRepairTests('test_partial_findings_one_item_repair_then_exact_three_rounds')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.prepare()

    def ordinary(self, label, **options):
        self.f.fake.side_effect = lambda **kw: ordinary_fixture.IndependentReviewTests.empty_response(self.f, **kw)
        return review.goal_advisory_context(**self.f.kwargs(stage='final', draft_response=label, **options))

    def test_two_of_three_used_cannot_spend_final_slot_on_missing_only_repair(self):
        self.ordinary('first current body')
        self.ordinary('changed current body')
        def third_round_partial(**kwargs):
            chunks = list(self.f.response(**kwargs))
            body = json.loads(kwargs['messages'][1]['content'])
            if body['executive_followup']['round'] == 3:
                parsed = json.loads(chunks[0].choices[0].delta.content)
                parsed['findings'][-1]['review_ref'] = 'R1:invalid-whole-source'
                chunks[0].choices[0].delta.content = json.dumps(parsed)
            return iter(chunks)
        self.f.fake.side_effect = third_round_partial
        for number in (1, 2):
            self.f.call()
            state = review.review_state(self.f.resolved())
            self.assertEqual(state['executive_followup']['rounds'][-1]['round'], number)
            self.f.answer(state['pending_findings'], limited=True)
        self.f.call()
        state = review.review_state(self.f.resolved())
        self.assertEqual(len(state['pending_findings']), 4)
        self.f.answer(state['pending_findings'], limited=True)
        before = {p: p.read_bytes() for p in self.f.request_paths()}
        for _ in range(2):result = self.f.call()
        state = review.review_state(self.f.resolved())
        quota = completion_quota(self.f.resolved(), review._receipts(self.f.resolved()))
        self.assertEqual((quota['used'], quota['remaining'], quota['required_reserved']), (2, 1, 1))
        self.assertEqual(result['diagnostic']['code'], 'review_final_slot_reserved')
        self.assertFalse(state['followup_actionable'])
        self.assertNotIn('review_repair', state)
        self.assertFalse(state['final_review_current'])
        self.assertEqual(state['executive_followup']['rounds'][-1]['round'], 3)
        self.assertEqual(len(state['dispositions']), 14)
        self.assertEqual(before, {p: p.read_bytes() for p in self.f.request_paths()})
        self.assertEqual(quota['available_for_optional_work'], 0)
        self.assertEqual(len(self.f.calls), 5)  # two ordinary, three executive; no repair

    def test_pending_intent_consumes_slot_and_reopening_context_cannot_refund_it(self):
        self.ordinary('saved request')
        request = self.f.request_paths()[0]
        request.with_name(request.name.replace('-request.json', '-response.json')).unlink()
        receipts = review._receipts(self.f.resolved())
        self.assertEqual(completion_quota(self.f.resolved(), receipts)['used'], 1)
        # A new resolved view is not a new allowance; no request replay occurs.
        self.ordinary('saved request')
        self.assertEqual(len(self.f.calls), 1)
        self.assertEqual(len(ordinary_receipts(review._receipts(self.f.resolved()))), 1)

    def test_receipt_lock_serializes_optional_admission_and_preserves_final_capacity(self):
        entered, release = threading.Event(), threading.Event()
        self.f.fake.side_effect = lambda **kw: ordinary_fixture.IndependentReviewTests.empty_response(self.f, **kw)
        review.goal_advisory_context(**self.f.kwargs(turns_used=1))
        self.f.add_sources(8)
        self.f.save_note(answer='Changed saved assessment supporting another bounded review')
        def blocked(**kwargs):
            entered.set()
            if not release.wait(5):raise AssertionError('test release not delivered')
            return ordinary_fixture.IndependentReviewTests.empty_response(self.f, **kwargs)
        self.f.fake.side_effect = blocked
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(review.goal_advisory_context, **self.f.kwargs(turns_used=5))
            self.assertTrue(entered.wait(5))
            second = pool.submit(review.goal_advisory_context, **self.f.kwargs(turns_used=5))
            blocked_result = second.result(timeout=5)
            # The synchronous lock holder is not a registered bounded auxiliary
            # worker. Preserve its intent, but do not invent a live wait owner.
            self.assertEqual(blocked_result['status'], 'unavailable')
            self.assertEqual(blocked_result['diagnostic']['code'], 'review_owner_unknown')
            self.assertFalse(blocked_result['followup_actionable'])
            release.set()
            self.assertEqual(first.result(timeout=5)['status'], 'received')
        quota = completion_quota(self.f.resolved(), review._receipts(self.f.resolved()))
        self.assertEqual((quota['used'], quota['remaining']), (2, 1))
        self.assertEqual(quota['available_for_optional_work'], 0)
        self.assertEqual(self.ordinary('final body after optional review')['status'], 'received')
        self.assertEqual(len(ordinary_receipts(review._receipts(self.f.resolved()))), 3)


if __name__ == '__main__':unittest.main()
