"""Offline final delivery/report disclosure contracts; review outcomes are local fakes."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_report_driven import fixture
from forsic_plugin.report_driven.host import current, ledger
from forsic_plugin.report_driven.finalization import finalize
from forsic_plugin.report_driven.views import bundle, compile_views


class IndependentReviewFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.case, _, _ = fixture(self.tmp.name)
        self.state = current(self.case)
        self.context = {'enabled': True, 'resolved': {'case_id': self.case.config['case_id'], 'case': self.case,
                        'goal_id': 'synthetic-goal', 'conversation_id': 'synthetic-conversation'}}
        self.review = {'status': 'received', 'review_id': 'review-final-1', 'latest_stage': 'final',
            'final_review_current': True, 'latest_final_review_id': 'review-final-1',
            'latest_final_status': 'received', 'pending_finding_ids': [],
            'requires_followup': False, 'dispositions': [], 'stale_review_ids': [],
            'historical_review_ids': []}
        self.fake = patch('forsic_plugin.independent_review.review_state', side_effect=lambda *_: deepcopy(self.review))
        self.fake.start(); self.addCleanup(self.fake.stop)

    def rendered(self, **kwargs):
        return bundle(self.case, self.state, review_context=self.context, **kwargs)

    def selected(self):
        return [e for e in ledger(self.case) if e['kind'] == 'report_finalized']

    def test_material_pending_blocks_delivery_but_preserves_all_drafts(self):
        self.review.update(requires_followup=True, pending_finding_ids=['finding-unread'])
        report = self.rendered()
        result = finalize(self.case, self.state, report['bundle_id'], review_context=self.context)
        self.assertFalse(result['finalized']); self.assertTrue(result['draft_preserved'])
        self.assertEqual(result['delivery_status'], 'independent_review_pending')
        self.assertEqual(result['review']['pending_finding_ids'], ['finding-unread'])
        self.assertEqual(result['recovery'],{'tool':'forsic_reporting','arguments':{'action':'review_inbox'}})
        self.assertEqual(self.selected(), [])
        self.assertTrue(all(Path(p).is_file() for p in report['files']))

    def test_exploration_review_does_not_satisfy_final_review(self):
        self.review.update(latest_stage='exploration', final_review_current=False,
                           latest_final_review_id='', latest_final_status='not_reviewed')
        report = self.rendered()
        result = finalize(self.case, self.state, report['bundle_id'], review_context=self.context)
        self.assertEqual(result['delivery_status'], 'independent_review_required')
        self.assertEqual(self.selected(), [])

    def test_required_executive_rounds_cannot_be_replaced_by_availability_disclosure(self):
        self.context.update(executive_followup_required=True, attempt={'status':'unavailable'})
        self.review.update(status='not_reviewed', final_review_current=False,
                           latest_final_review_id='', latest_final_status=None)
        report=self.rendered()
        result=finalize(self.case,self.state,report['bundle_id'],review_context=self.context)
        self.assertEqual(result['delivery_status'],'independent_review_incomplete')
        self.assertTrue(result['draft_preserved'])
        self.assertEqual(self.selected(),[])

    def test_new_receipt_or_disposition_requires_report_rerender(self):
        report = self.rendered()
        self.review.update(review_id='review-final-2', latest_final_review_id='review-final-2')
        result = finalize(self.case, self.state, report['bundle_id'], review_context=self.context)
        self.assertEqual(result['delivery_status'], 'independent_review_rerender_required')
        self.assertEqual(result['recovery'], {'tool':'forsic_reporting','arguments':
            {'action':'render','question_ids':[q['id'] for q in self.state['questions']]}})
        self.assertEqual(self.selected(), [])
        newer = self.rendered()
        self.review['dispositions'].append({'review_id': 'review-final-2', 'finding_id': 'finding-a',
            'question_id': self.state['questions'][0]['id'], 'disposition': 'scope_limited',
            'reason': 'Retained material cannot establish execution.', 'citation_refs': []})
        result = finalize(self.case, self.state, newer['bundle_id'], review_context=self.context)
        self.assertEqual(result['delivery_status'], 'independent_review_rerender_required')
        final = self.rendered()
        result = finalize(self.case, self.state, final['bundle_id'], review_context=self.context)
        self.assertTrue(result['finalized']); self.assertEqual(len(self.selected()), 1)

    def test_predispatch_unavailability_must_be_disclosed_then_does_not_retry(self):
        self.review.update(status='not_reviewed', review_id='', latest_stage=None,
                           final_review_current=False, latest_final_review_id='', latest_final_status=None)
        draft = self.rendered()
        self.context['attempt'] = {'status': 'unavailable', 'context': 'Synthetic unavailable route'}
        result = finalize(self.case, self.state, draft['bundle_id'], review_context=self.context)
        self.assertEqual(result['delivery_status'], 'independent_review_rerender_required')
        report = self.rendered()
        manifest = json.loads(Path(report['manifest']).read_text())
        self.assertEqual(manifest['independent_review']['availability_status'], 'unavailable')
        self.assertTrue(manifest['independent_review']['availability_limitation'])
        for p in report['files']:
            if p.endswith('.html'):
                self.assertIn('독립 검토 한계', Path(p).read_text())
        result = finalize(self.case, self.state, report['bundle_id'], review_context=self.context)
        self.assertTrue(result['finalized'])
        self.assertTrue(finalize(self.case, self.state, report['bundle_id'], review_context=self.context)['reused'])

    def test_unavailability_cannot_hide_pending_or_unread_receipt_state(self):
        self.context['attempt'] = {'status': 'unavailable'}
        self.review.update(status='unavailable', final_review_current=False, requires_followup=True)
        report = self.rendered()
        result = finalize(self.case, self.state, report['bundle_id'], review_context=self.context)
        self.assertEqual(result['delivery_status'], 'independent_review_pending')
        self.assertEqual(self.selected(), [])

    def test_handled_durable_availability_limit_can_be_delivered_without_retry(self):
        self.review.update(status='incomplete', final_review_current=False, latest_final_status='incomplete',
            dispositions=[{'review_id': 'review-final-1', 'finding_id': 'availability-one',
                           'disposition': 'scope_limited', 'reason': 'Review response unavailable; direct evidence limits retained.',
                           'citation_refs': []}])
        report = self.rendered()
        manifest = json.loads(Path(report['manifest']).read_text())
        self.assertEqual(manifest['independent_review']['availability_status'], 'incomplete')
        self.assertTrue(finalize(self.case, self.state, report['bundle_id'], review_context=self.context)['finalized'])

    def test_both_readers_show_dispositions_and_scope_limitations(self):
        state = deepcopy(self.state)
        state['questions'][0]['review_responses'] = [
            {'review_id': 'review-one', 'finding_id': 'finding-x', 'disposition': 'investigated',
             'reason': 'Observed another exact record.', 'citation_refs': ['C1:receipt-one:0']},
            {'review_id': 'review-one', 'finding_id': 'finding-y', 'disposition': 'refuted',
             'reason': 'A retained counterexample explains the assertion.', 'citation_refs': ['C1:receipt-two:0']},
            {'review_id': 'review-one', 'finding_id': 'finding-z', 'disposition': 'scope_limited',
             'reason': 'Execution remains unknown in this acquisition.', 'limitation': 'No independent execution trace was acquired.', 'citation_refs': []}]
        for view in compile_views(state).values():
            text = json.dumps(view, ensure_ascii=False)
            for literal in ('finding-x', 'finding-y', 'finding-z', '조사 후 처리', '근거로 반박',
                            '한계로 명시', 'Execution remains unknown', 'No independent execution trace', 'C1:receipt-two:0'):
                self.assertIn(literal, text)

    def test_legacy_none_disabled_and_current_success(self):
        report = bundle(self.case, self.state)
        self.assertTrue(finalize(self.case, self.state, report['bundle_id'])['finalized'])
        self.assertTrue(finalize(self.case, self.state, report['bundle_id'], review_context={'enabled': False})['finalized'])
        # Enabling the gate cannot silently select an older unreviewed bundle.
        result = finalize(self.case, self.state, report['bundle_id'], review_context=self.context)
        self.assertEqual(result['delivery_status'], 'independent_review_rerender_required')
        reviewed = self.rendered()
        self.context['attempt'] = {'status': 'received', 'review_id': 'review-final-1'}
        self.assertTrue(finalize(self.case, self.state, reviewed['bundle_id'], review_context=self.context)['finalized'])

    def test_busy_review_receipt_lock_preserves_draft_without_rerun(self):
        from forsic_plugin.independent_review import _goal_key
        report = self.rendered()
        path = self.case.output / ('independent-review-' + _goal_key(self.context['resolved']) + '.lock')
        with path.open('a') as writer:
            fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = finalize(self.case, self.state, report['bundle_id'], review_context=self.context)
        self.assertEqual(result['delivery_status'], 'independent_review_in_progress')
        self.assertFalse(result['finalized']); self.assertEqual(self.selected(), [])
        self.assertTrue(finalize(self.case, self.state, report['bundle_id'], review_context=self.context)['finalized'])

    def test_concurrent_note_writer_cannot_commit_between_gate_and_selection(self):
        from forsic_plugin.report_driven import finalization
        report = self.rendered()
        attempted = threading.Event(); committed = threading.Event(); failures = []
        note = deepcopy(next(e['data'] for e in reversed(ledger(self.case)) if e['kind'] == 'note'))
        note['revision'] += 1; note['answer'] = 'A concurrent correction changes the saved answer.'
        def write_correction():
            try:
                attempted.set()
                self.case.record('note', note)
                committed.set()
            except BaseException as exc:
                failures.append(exc)
        writer = threading.Thread(target=write_correction)
        original = finalization._review_gate
        def checked_gate(*args):
            writer.start()
            self.assertTrue(attempted.wait(2))
            # An actual competing write must remain blocked while the finalizer
            # validates and selects its protected snapshot.
            self.assertFalse(committed.wait(.15))
            return original(*args)
        with patch.object(finalization, '_review_gate', side_effect=checked_gate):
            result = finalize(self.case, self.state, report['bundle_id'], review_context=self.context)
        writer.join(3)
        self.assertFalse(writer.is_alive()); self.assertEqual(failures, [])
        self.assertTrue(result['finalized']); self.assertTrue(committed.is_set())
        events = ledger(self.case)
        selection = next(i for i, event in enumerate(events) if event['kind'] == 'report_finalized')
        correction = next(i for i, event in enumerate(events) if event['kind'] == 'note' and event['data']['revision'] == note['revision'])
        self.assertLess(selection, correction)
        # A caller that retains the old report after the correction cannot select
        # it again; the first valid selection preceded the correction's commit.
        with self.assertRaisesRegex(ValueError, 'Case changed'):
            finalize(self.case, self.state, report['bundle_id'], review_context=self.context)


if __name__ == '__main__':
    unittest.main()
