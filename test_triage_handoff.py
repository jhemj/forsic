"""Typed triage remains candidate metadata through source, citation and pruning."""
import json
import unittest

from forsic_plugin.context_results import summarize
from forsic_plugin.report_driven.grounded_judgment import read_refs
from forsic_plugin.report_driven.measurement import acquisition_context, field_role, measurement_view
import test_efficiency_contracts as fixtures


class TriageHandoffTests(unittest.TestCase):
    def test_query_navigation_and_original_partial_scope_survive_compression(self):
        from forsic_plugin.capabilities import capability
        call = {'tool': 'forsic_shell', 'arguments': {'action': 'triage_query',
            'scan_id': 'a' * 32, 'index_sha256': 'b' * 64, 'offset': 20}}
        data = {'schema': 'forsic.triage.v1', 'action': 'triage_query',
            'evidence_id': 'retained-query', 'scan_id': 'a' * 32, 'query_state': 'returned',
            'profile': 'windows_evtx', 'coverage': {'state': 'partial'},
            'candidate_query': {'available': True, 'index_sha256': 'b' * 64},
            'selection': {'index_truncated': True, 'matched_indexed_candidates': 35,
                          'next_call': call, 'filters': {'event_record_id': 42}},
            'summary': {'candidate_count': 100, 'returned_candidates': 20,
                        'omitted_candidates': 80, 'counts_complete': False},
            'candidates': [{'private_unexpected_field': 'do not retain this in context'}]}
        pruned = json.loads(summarize('forsic_shell', call['arguments'], json.dumps(data)))
        self.assertEqual(pruned['triage']['selection']['next_call'], call)
        self.assertTrue(pruned['triage']['selection']['index_truncated'])
        self.assertFalse(pruned['triage']['summary']['counts_complete'])
        self.assertEqual(pruned['triage']['coverage_state'], 'partial')
        self.assertNotIn('private_unexpected_field', json.dumps(pruned))
        effects = capability('forsic_shell', call['arguments'])
        self.assertTrue(effects['comparison_readonly'])
        self.assertTrue(effects['case_ledger_write'])
        for effect in ('workspace_write', 'subprocess', 'target_code_execution', 'network'):
            self.assertFalse(effects[effect])

    def test_candidate_source_has_no_direct_body_citation_or_adopted_claim(self):
        x = fixtures.EfficiencyContracts()
        x.setUp()
        self.addCleanup(x.doCleanups)
        data = {'schema': 'forsic.triage.v1', 'tool': 'forsic_shell',
            'profile': 'linux_login_parser', 'representation': 'derived_triage_candidates',
            'coverage': {'state': 'partial', 'selected_files': 1},
            'summary': {'candidate_count': 3, 'returned_candidates': 1, 'omitted_candidates': 2, 'counts_complete': False},
            'candidates': [{'candidate_id': 'C0001', 'status': 'candidate_not_adopted',
                            'record_type_label': 'USER_PROCESS'}],
            'execution': {'state': 'returned', 'exit_code': 0, 'termination_confirmed': True}}
        data['started_id'] = x.case.record('tool_start',
            {'tool': 'forsic_shell', 'arguments': {'action': 'triage'}}, 'synthetic-session')
        source = x.case.record('tool_result', data, 'synthetic-session')
        view = read_refs(x.case, {**data, 'evidence_id': source})
        self.assertNotIn('presented_spans', view)
        for pointer in ('/candidates/0/record_type_label', '/stdout', '/profile'):
            self.assertEqual(field_role(data, pointer), 'metadata')
        context = acquisition_context(data, '/candidates/0/record_type_label')
        self.assertTrue(context['derivation']['candidates_only'])
        self.assertFalse(context['returned_scope']['original_evidence'])
        self.assertEqual(measurement_view(data)['representation'], 'derived_triage_candidates')
        pruned = json.loads(summarize('forsic_shell', {'action': 'triage'}, json.dumps(view)))
        self.assertEqual(pruned['triage']['coverage_state'], 'partial')
        self.assertFalse(pruned['triage']['summary']['counts_complete'])
        self.assertEqual(pruned['lookup']['arguments']['source_id'], source)
        self.assertNotIn('C0001', json.dumps(pruned))


if __name__ == '__main__':
    unittest.main()
