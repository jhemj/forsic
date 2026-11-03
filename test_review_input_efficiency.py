"""Synthetic rejected-review diagnostics and unchanged single-writer contracts."""
from copy import deepcopy
import json
import unittest
import uuid
from unittest.mock import patch

from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.review_actions import ReviewInputError, native_response_view
import test_review_actions as fixtures


class ReviewInputEfficiencyTests(unittest.TestCase):
    for _name in ('setUp', 'bind', 'session', 'save_note', 'setup_review', 'add_sources',
                  'kwargs', 'response', 'request_paths', 'resolved', 'prepare', 'call',
                  'args', 'start', 'cite', 'response_args'):
        locals()[_name] = getattr(fixtures.ReviewActionsTests, _name)
    del _name

    def rejected(self, args):
        before = self.case.db.read_bytes()
        calls = len(self.calls)
        with self.assertRaises(ReviewInputError) as caught:
            host.invoke(self.case, args)
        self.assertEqual(self.case.db.read_bytes(), before)
        self.assertEqual(len(self.calls), calls)
        self.assertFalse(caught.exception.details['write_applied'])
        return caught.exception.details

    def test_unknown_action_and_bad_citation_rejected_together_without_writes(self):
        finding = self.start()['findings'][0]
        args = self.response_args(finding, action='review_response', citation_refs=['C2:invalid'])
        details = self.rejected(args)
        self.assertTrue(any('action' in p.get('unknown_fields', []) for p in details['invalid_fields']))
        self.assertTrue(any(p['path'] == 'payload.citation_refs[0]' for p in details['invalid_fields']))
        self.assertEqual(host.invoke(self.case, self.args())['pending_count'], 5)

    def test_unknown_and_foreign_questions_skip_evidence_without_revision_leak(self):
        finding = self.start()['findings'][0]
        args = self.response_args(finding, action='review_response', citation_refs=['C2:invalid'])
        unknown = deepcopy(args)
        unknown['payload']['question_id'] = 'Q-unknown'
        with patch('forsic_plugin.report_driven.review_responses._citations',
                   side_effect=AssertionError('No evidence check without ownership')):
            unknown_details = self.rejected(unknown)
            # A real retained question in the same case, owned by another goal.
            self.note['goal_id'] = 'foreign-goal'
            self.note['revision'] += 1
            self.case.record('note', self.note, self.sid)
            foreign_details = self.rejected(args)
            self.note['goal_id'] = self.resolved()['goal_id']
            self.note['session_id'] = 'foreign-conversation'
            self.note['revision'] += 1
            self.case.record('note', self.note, 'foreign-conversation')
            foreign_session_details = self.rejected(args)
        self.assertEqual(foreign_session_details['invalid_fields'], foreign_details['invalid_fields'])
        self.assertEqual(unknown_details['invalid_fields'], foreign_details['invalid_fields'])
        self.assertTrue(any(p.get('status') == 'not_checked' for p in foreign_details['invalid_fields']))
        self.assertNotIn('current_revision', json.dumps(foreign_details))
        self.assertNotIn('foreign-goal', json.dumps(foreign_details))

    def test_unknown_review_identity_is_not_approved_or_used_for_citation_lookup(self):
        finding = self.start()['findings'][0]
        args = self.response_args(finding, action='review_response', review_id='unknown-review',
                                  citation_refs=['C2:invalid'])
        with patch('forsic_plugin.report_driven.review_responses._citations',
                   side_effect=AssertionError('No evidence check without review identity')):
            details = self.rejected(args)
        self.assertTrue(any(p.get('status') == 'not_checked' for p in details['invalid_fields']))

    def test_reference_diagnostics_are_bounded_and_oversized_arrays_not_checked(self):
        finding = self.start()['findings'][0]
        args = self.response_args(finding, action='review_response',
                                  citation_refs=[f'C2:invalid-{i}' for i in range(16)])
        details = self.rejected(args)
        self.assertEqual(details['invalid_field_count'], 17)
        self.assertLessEqual(len(details['invalid_fields']), 8)
        self.assertGreater(details['omitted_invalid_fields'], 0)
        self.assertLessEqual(len(json.dumps(details['invalid_fields'], ensure_ascii=False).encode()), 6000)
        args['payload']['citation_refs'].append('C2:too-many')
        with patch('forsic_plugin.report_driven.review_responses._citations',
                   side_effect=AssertionError('Do not traverse oversized arrays')):
            details = self.rejected(args)
        self.assertTrue(any(p.get('status') == 'not_checked' for p in details['invalid_fields']))

    def test_focused_save_replay_preserves_answer_and_competing_revision_is_rejected(self):
        finding = self.start()['findings'][0]
        args = self.response_args(finding)
        competing = deepcopy(args)
        competing['payload']['operation_id'] = uuid.uuid4().hex
        before = deepcopy(host.current(self.case)['questions'])
        accepted = host.invoke(self.case, args)
        saved = self.case.db.read_bytes()
        self.assertEqual(host.invoke(self.case, args), accepted)
        self.assertEqual(self.case.db.read_bytes(), saved)
        details = self.rejected(competing)
        self.assertTrue(any(p.get('error') == 'Question revision conflict' for p in details['invalid_fields']))
        after = host.current(self.case)['questions']
        self.assertEqual(after[0]['answer'], before[0]['answer'])
        self.assertEqual(after[0]['claim_refs'], before[0]['claim_refs'])
        self.assertEqual(after[0]['revision'], before[0]['revision'] + 1)
        self.assertEqual(len(after[0]['review_responses']), 1)

    def test_legacy_hint_requires_discovery_before_using_existing_fallback(self):
        result = self.start()
        legacy = result['response_contract']['legacy_input']
        self.assertIn('canonical tool discovery confirms', legacy['when'])
        self.assertIn('Direct menu absence alone is insufficient', legacy['when'])
        self.assertEqual(legacy['arguments'], {'action': 'review_response'})
        details = self.rejected(self.args('review_response', payload={}))
        native = native_response_view(details, legacy_input=True)
        self.assertEqual(native['recovery']['legacy_input']['when'], legacy['when'])


if __name__ == '__main__':
    unittest.main()
