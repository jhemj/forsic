"""Native finalization presentation keeps navigation ahead of retained history."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import test_shell_integration as fixtures
from forsic_plugin.report_driven.host import current, invoke


class FinalizationModelViewTests(unittest.TestCase):
    setUp = fixtures.ShellIntegrationTests.setUp
    call = fixtures.ShellIntegrationTests.call

    def result(self, *, pending=False):
        qid = current(self.case)['questions'][0]['id']
        return {'finalized': False, 'delivery_status': 'independent_review_pending' if pending
                else 'independent_review_rerender_required', 'draft_preserved': True,
            'bundle_id': 'a'*64, 'current_snapshot_id': 'b'*64,
            'report_reference': {'bundle_id': 'a'*64, 'snapshot_id': 'c'*64},
            'recovery': {'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox'}
                if pending else {'action': 'render', 'question_ids': [qid], 'allow_partial': True}},
            'next_step': 'Read pending findings.' if pending else 'Render current review disclosure.',
            'review': {'status': 'received', 'final_review_current': True,
                'latest_final_review_id': 'review-current', 'latest_final_status': 'received',
                'requires_followup': pending, 'pending_finding_ids': ['pending-'+str(i) for i in range(35)] if pending else [],
                'fingerprint': 'd'*64, 'call_budget': 16,
                'dispositions': [{'review_id': 'review-'+str(i), 'finding_id': 'finding-'+str(i),
                    'reason': 'Retained history qualification. '*1200,
                    'citations': [{'literal': 'RETAINED_SOURCE_LITERAL'*1000}]} for i in range(35)]}}

    def retain(self, result):
        with patch('forsic_plugin.report_driven.host._invoke', return_value=deepcopy(result)):
            return self.call('forsic_reporting', {'action': 'finalize', 'bundle_id': 'a'*64})

    def test_large_review_is_compact_and_exact_render_call_runs_without_review_restart(self):
        full = self.result()
        unchanged = deepcopy(full)
        view = self.retain(full)
        self.assertLessEqual(len(json.dumps(view, ensure_ascii=False).encode()), 12000)
        self.assertEqual(view['delivery_status'], full['delivery_status'])
        self.assertTrue(view['review']['final_review_current'])
        self.assertFalse(view['review']['requires_followup'])
        self.assertEqual(view['next_call'], full['recovery'])
        self.assertEqual(view['review']['disposition_count'], 35)
        self.assertEqual(view['review']['call_budget'], 16)
        self.assertNotIn('RETAINED_SOURCE_LITERAL', json.dumps(view))
        self.assertEqual(full, unchanged)
        retained = self.case.event(view['evidence_id'])['data']
        self.assertEqual(retained['review'], full['review'])
        with patch('agent.auxiliary_client.call_llm', side_effect=AssertionError('No review restart')):
            rendered = invoke(self.case, view['next_call']['arguments'])
        self.assertIn('bundle_id', rendered)

    def test_pending_findings_and_diagnostics_remain_visible(self):
        full = self.result(pending=True)
        full.update(error='Retained validation failure', diagnostic={'code': 'synthetic_failure'})
        view = self.retain(full)
        self.assertLessEqual(len(json.dumps(view, ensure_ascii=False).encode()), 12000)
        self.assertEqual(view['error'], full['error'])
        self.assertEqual(view['diagnostic'], full['diagnostic'])
        self.assertTrue(view['review']['requires_followup'])
        self.assertEqual(view['review']['pending_count'], 35)
        self.assertEqual(view['next_call']['arguments'], {'action': 'review_inbox'})
        self.assertEqual(view['review']['lookup']['arguments'], {'action': 'review_inbox'})

    def test_success_keeps_exact_selection_files_and_verification(self):
        full = {'finalized': True, 'delivery_status': 'ready_for_configured_case_topic',
            'bundle_id': 'a'*64, 'snapshot_id': 'b'*64, 'finalization_receipt': 'selected-receipt',
            'files': ['/retained/report.html'], 'published': False,
            'report_work': {'stage': 'deliver_selected_version', 'same_content_rewrite_needed': False,
                'verification': {'artifact_integrity': 'passed', 'independent_review_current': True,
                    'layout': {'status': 'unverified'}}},
            'delivery': {'schema': 'forsic-delivery-1', 'snapshot_id': 'b'*64,
                'answers': [{'answer': 'Long retained answer '*10000}]}}
        view = self.retain(full)
        self.assertLessEqual(len(json.dumps(view, ensure_ascii=False).encode()), 12000)
        for key in ('finalized', 'delivery_status', 'bundle_id', 'snapshot_id',
                    'finalization_receipt', 'files', 'published', 'report_work'):
            self.assertEqual(view[key], full[key], key)
        self.assertEqual(self.case.event(view['evidence_id'])['data']['delivery'], full['delivery'])

    def test_masking_and_scope_recovery_are_copied_without_inventing_a_call(self):
        full = self.result()
        full['recovery']['arguments'].update(redact=['literal private selector'], review_id='specific-review')
        view = self.retain(full)
        self.assertEqual(view['next_call'], full['recovery'])
        self.assertEqual(view['review']['history_calls'][-1]['arguments'],
                         {'action': 'review_inbox', 'review_id': 'review-34'})
        full['recovery'] = None
        view = self.retain(full)
        self.assertIsNone(view['recovery'])
        self.assertNotIn('next_call', view)


if __name__ == '__main__':
    unittest.main()
