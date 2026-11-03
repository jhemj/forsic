"""Exact report recovery survives bounded old-result projection, without a new write."""
from copy import deepcopy
import json
import unittest

from forsic_plugin.context_results import summarize
from forsic_plugin.report_driven.host import invoke
import test_report_narrative as fixtures


class AuthoringContextDiagnosticsTests(unittest.TestCase):
    setUp = fixtures.ReportNarrativeTests.setUp
    tearDown = fixtures.ReportNarrativeTests.tearDown
    draft = fixtures.ReportNarrativeTests.draft

    def error(self):
        packet = invoke(self.case, {'action': 'compose', 'section': 'overall_judgment'})
        draft = self.draft('overall_judgment', packet)
        draft['entries'][0]['cells'] = ['']
        args = {'action': 'compose', 'section': 'overall_judgment',
                'question_ids': packet['question_ids'], 'draft': draft,
                'operation_id': 'invalid-synthetic-draft',
                'edit_ref': packet['save_contract']['edit_ref']}
        return args, json.loads(self.case.invoke('forsic_reporting', args))

    def test_real_rejection_retains_field_diagnostics_and_exact_read_recovery(self):
        args, original = self.error()
        view = json.loads(summarize('forsic_reporting', args, json.dumps(original)))
        self.assertEqual(view['error_code'], 'report_authoring_input_invalid')
        self.assertEqual(view['error_subtype'], original['error_subtype'])
        self.assertEqual(view['input_problems'], original['input_problems'])
        self.assertEqual(view['recovery'], original['recovery'])
        recovered = invoke(self.case, view['recovery']['arguments'])
        self.assertIn('save_contract', recovered)
        self.assertNotIn('draft', view['recovery']['arguments'])
        self.assertNotIn('edit_ref', view['recovery']['arguments'])

    def test_size_bound_omits_whole_problems_and_counts_them_without_mutating_source(self):
        args, original = self.error()
        original['input_problems'] = [
            {'path': f'draft.entries[{i}].cells[0]', 'requirement': 'synthetic field constraint ' * 20}
            for i in range(16)]
        original['omitted_problem_count'] = 3
        before = deepcopy(original)
        encoded = summarize('forsic_reporting', args, json.dumps(original))
        self.assertIsNotNone(encoded)
        self.assertLessEqual(len(encoded), 2000)
        view = json.loads(encoded)
        self.assertEqual(view['recovery'], original['recovery'])
        self.assertEqual(view['input_problems'], original['input_problems'][:len(view['input_problems'])])
        self.assertEqual(len(view['input_problems']) + view['omitted_problem_count'], 19)
        self.assertEqual(original, before)


if __name__ == '__main__':
    unittest.main()
