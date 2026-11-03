"""Authoring diagnostics through the real host boundary; synthetic case only."""
from copy import deepcopy
import json
import unittest

import test_report_narrative as narrative_tests
from forsic_plugin.report_driven.host import invoke, current
from forsic_plugin.report_driven.narrative import status


class ReportDiagnosticEfficiencyTests(unittest.TestCase):
    setUp = narrative_tests.ReportNarrativeTests.setUp
    tearDown = narrative_tests.ReportNarrativeTests.tearDown
    draft = narrative_tests.ReportNarrativeTests.draft
    write = narrative_tests.ReportNarrativeTests.write
    review = narrative_tests.ReportNarrativeTests.review
    finish = narrative_tests.ReportNarrativeTests.finish

    def host(self, request):
        return json.loads(self.case.invoke('forsic_reporting', request))

    def authoring_events(self):
        with self.case.connect() as db:
            return [tuple(row) for row in db.execute(
                "SELECT * FROM events WHERE kind IN ('report_narrative', 'mutation_receipt') ORDER BY rowid")]

    def rejected(self, request, subtype):
        # The native boundary retains tool receipts even when authoring is rejected.
        before = self.authoring_events()
        draft = status(current(self.case))['draft_hash']
        result = self.host(request)
        self.assertIn('error', result)
        self.assertEqual(result['error_subtype'], subtype)
        self.assertFalse(result['saved'])
        self.assertTrue(result['draft_preserved'])
        self.assertEqual(self.authoring_events(), before)
        self.assertEqual(status(current(self.case))['draft_hash'], draft)
        self.assertFalse(list(self.case.output.glob('report-bundle-*')))
        recovery = result['recovery']['arguments']
        self.assertEqual(recovery['question_ids'], status(current(self.case))['question_ids'])
        self.assertNotIn('pointer', recovery)
        self.assertNotIn('edit_ref', recovery)
        self.assertNotIn('draft', recovery)
        refreshed = self.host(recovery)
        self.assertNotIn('error', refreshed)
        self.assertIn('save_contract', refreshed)
        self.assertLess(len(json.dumps(refreshed).encode()), 24576)
        return result, refreshed

    def test_missing_and_stale_read_coordinates_are_distinct_and_recoverable(self):
        self.finish()
        root = invoke(self.case, {'action': 'compose', 'section': 'review'})
        review = root['previous_review']['read_call']['arguments']
        missing = {key: value for key, value in review.items() if key != 'expected_revision'}
        result, recovered = self.rejected(missing, 'coordinate_missing')
        self.assertNotIn('review changed', result['error'])
        self.assertNotIn('error', self.host(recovered['previous_review']['read_call']['arguments']))
        missing_snapshot = {key: value for key, value in review.items() if key != 'snapshot_id'}
        self.rejected(missing_snapshot, 'coordinate_missing')
        self.rejected({**review, 'snapshot_id': 'old'}, 'snapshot_mismatch')
        unchanged, request = self.review()
        self.assertEqual(unchanged['revision'], root['revision'])
        # Identical review saves are intentional no-ops. Change actual review
        # content to exercise the stale historical coordinate contract.
        request['operation_id'] += '-changed'
        request['review']['summary'] += ' 추가 설명 대조 결과 반영.'
        changed = self.host(request)
        self.assertTrue(changed['saved'], changed)
        self.assertGreater(changed['revision'], root['revision'])
        result, recovered = self.rejected(review, 'revision_mismatch')
        self.assertIn('review changed', result['error'])
        self.assertNotIn('error', self.host(recovered['previous_review']['read_call']['arguments']))

    def test_blank_cells_and_group_shape_return_field_diagnostics(self):
        packet = invoke(self.case, {'action': 'compose', 'section': 'scope'})
        original = self.draft('scope', packet)
        for invalid in ('', '   ', None):
            payload = deepcopy(original)
            payload['entries'][0]['cells'][0] = invalid
            request = dict(action='compose', section='scope', question_ids=packet['question_ids'],
                operation_id='invalid-cell', edit_ref=packet['save_contract']['edit_ref'], draft=payload)
            result, recovered = self.rejected(request, 'authoring_fields_invalid')
            self.assertEqual(result['error_code'], 'report_authoring_input_invalid')
            self.assertEqual(result['input_problems'][0]['path'], 'draft.entries[0].cells[0]')
            self.assertNotIn('change the path', result['next_step'])
        payload = deepcopy(original)
        payload['entries'][0]['cells'].append('extra')
        self.rejected({**request, 'draft': payload}, 'authoring_fields_invalid')
        valid = {**request, 'draft': original, 'edit_ref': recovered['save_contract']['edit_ref']}
        self.assertTrue(self.host(valid)['saved'])

    def test_unknown_review_field_does_not_look_like_changed_evidence(self):
        self.finish()
        _, valid = self.review()
        root = invoke(self.case, {'action': 'compose', 'section': 'review'})
        request = deepcopy(valid)
        request.update(operation_id='bad-review-field', edit_ref=root['save_contract']['edit_ref'])
        request['review']['checks'][0]['unsupported'] = True
        result, recovered = self.rejected(request, 'authoring_fields_invalid')
        problem = result['input_problems'][0]
        self.assertEqual(problem['path'], 'review.checks[0]')
        self.assertEqual(problem['unknown_fields'], ['unsupported'])
        self.assertEqual(recovered['snapshot_id'], root['snapshot_id'])
        self.assertEqual(recovered['revision'], root['revision'])
        request['review']['checks'][0].pop('unsupported')
        check = request['review']['checks'][0]['check']
        request['review']['checks'][0]['check'] = 'unknown-check'
        invalid, _ = self.rejected(request, 'authoring_fields_invalid')
        self.assertEqual(invalid['input_problems'][0]['path'], 'review.checks[0].check')
        self.assertIn(check, invalid['input_problems'][0]['allowed_values'])
        request['review']['checks'][0]['check'] = check
        request.update(operation_id='repaired-review', edit_ref=recovered['save_contract']['edit_ref'])
        self.assertTrue(self.host(request)['saved'])

    def test_invalid_payload_diagnostics_are_bounded(self):
        packet = invoke(self.case, {'action': 'compose', 'section': 'scope'})
        entry = self.draft('scope', packet)['entries'][0]
        entry['cells'] = ['']
        request = dict(action='compose', section='scope', question_ids=packet['question_ids'],
            operation_id='many-invalid-cells', edit_ref=packet['save_contract']['edit_ref'],
            draft={'entries': [deepcopy(entry) for _ in range(24)]})
        result, _ = self.rejected(request, 'authoring_fields_invalid')
        self.assertEqual(len(result['input_problems']), 16)
        self.assertGreater(result['omitted_problem_count'], 0)

    def test_oversized_problem_is_omitted_whole_and_nested_fields_stay_complete(self):
        packet = invoke(self.case, {'action': 'compose', 'section': 'scope'})
        draft = self.draft('scope', packet)
        request = dict(action='compose', section='scope', question_ids=packet['question_ids'],
            operation_id='long-unknown-field', edit_ref=packet['save_contract']['edit_ref'], draft=draft)
        long_key = 'unknown_' + '가' * 10000
        draft[long_key] = True
        result, recovered = self.rejected(request, 'authoring_fields_invalid')
        self.assertEqual(result['input_problems'], [])
        self.assertEqual(result['omitted_problem_count'], 1)
        self.assertNotIn(long_key, json.dumps(result, ensure_ascii=False))
        self.assertLess(len(json.dumps(result, ensure_ascii=False).encode()), 4096)
        draft.pop(long_key)
        unknown = ['unknown_' + str(index) for index in range(24)]
        draft.update({key: True for key in unknown})
        result, _ = self.rejected(request, 'authoring_fields_invalid')
        self.assertEqual(result['input_problems'][0]['unknown_fields'], sorted(unknown))
        self.assertEqual(result['omitted_problem_count'], 0)
        self.assertLessEqual(len(json.dumps(result['input_problems'], ensure_ascii=False).encode()), 4096)
        for key in unknown:
            draft.pop(key)
        request['edit_ref'] = recovered['save_contract']['edit_ref']
        self.assertTrue(self.host(request)['saved'])

    def test_missing_and_wrong_write_references_keep_compatibility(self):
        packet = invoke(self.case, {'action': 'compose', 'section': 'scope'})
        request = dict(action='compose', section='scope', question_ids=packet['question_ids'],
            operation_id='missing-edit', draft=self.draft('scope', packet))
        for change, subtype in (({}, 'coordinate_missing'), ({'edit_ref': 'forged'}, 'edit_reference_mismatch')):
            result, _ = self.rejected({**request, **change}, subtype)
            self.assertEqual(result['error_code'], 'report_authoring_reference_invalid')


if __name__ == '__main__':
    unittest.main()
