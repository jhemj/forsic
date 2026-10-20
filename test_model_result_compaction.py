"""Presentation-only shell compaction; synthetic ledger and mocked backend."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from forsic_plugin import shell_workspace as shell
from forsic_plugin.report_driven.host import invoke
from forsic_plugin.context_results import summarize
import test_shell_integration as integration


class ModelResultCompactionTests(unittest.TestCase):
    setUp = integration.ShellIntegrationTests.setUp
    call = integration.ShellIntegrationTests.call
    output = integration.ShellIntegrationTests.output

    def retain(self, output):
        with patch.object(shell, 'invoke', return_value=output):
            return self.call('forsic_shell', {'command': 'synthetic bounded command'})

    def test_policy_is_compact_and_exact_details_remain_in_ledger(self):
        policy = shell.workspace_info()
        policy.update(environment={'helpers': {'synthetic': 'large manifest' * 300}},
                      mounts=[{'destination': '/evidence', 'readonly': True}],
                      extension={'restrictions': ['synthetic restriction']}, timeout_seconds=30)
        original = self.output(execution_policy=policy)
        saved = deepcopy(original)
        view = self.retain(original)
        retained = self.case.event(view['evidence_id'])['data']
        self.assertEqual(original, saved)
        self.assertEqual(retained['execution_policy'], policy)
        self.assertEqual(view['execution'], original['execution'])
        self.assertEqual(view['execution_policy']['mounts'], policy['mounts'])
        self.assertEqual(view['execution_policy']['extension'], policy['extension'])
        self.assertEqual(view['capability_menu']['unavailable_profiles'], policy['triage']['unavailable_profiles'])
        detail = view['policy_details_calls']['environment']['arguments']
        exact = invoke(self.case, {**detail, 'pointer': '/execution_policy/environment/helpers/synthetic', 'limit': 50})
        self.assertEqual(exact['pointer'], '/execution_policy/environment/helpers/synthetic')
        self.assertIn('large manifest', json.dumps(exact))
        self.assertEqual(detail['source_handle'], view['source_handle'])
        self.assertLess(len(json.dumps(view)), len(json.dumps(retained)))

    def test_discovery_deduplicates_script_stdout_but_keeps_observed_failures(self):
        runtime = {'state': 'observed', 'executables': {'missing': {'state': 'missing_executable'},
                   'failed': {'state': 'available', 'probes': [{'state': 'probe_failed'}]}},
                   'target_format_support': 'not_checked'}
        original = self.output(json.dumps(runtime), action='capabilities', runtime_capabilities=runtime)
        view = self.retain(original)
        self.assertNotIn('stdout', view)
        self.assertNotIn('command', view)
        self.assertEqual(view['runtime_capabilities'], runtime)
        self.assertIn('stdout_call', view)
        failed = self.retain(self.output('bad metadata', action='capabilities', error='failed',
                            runtime_capabilities={'state': 'not_observed'}))
        self.assertEqual(failed['stdout'], 'bad metadata')
        self.assertEqual(failed['error'], 'failed')

    def test_uncertain_execution_coverage_and_new_restrictions_survive(self):
        policy = {k: v for k, v in shell.workspace_info().items() if k != 'declared_capabilities'}
        policy['new_restriction'] = {'state': 'restricted', 'reason': 'synthetic'}
        output = self.output('partial stdout', execution_policy=policy,
            error='Cleanup unknown', error_code='command_cleanup_unknown', failure_stage='cleanup',
            execution={'state': 'completion_unknown', 'termination_confirmed': False},
            coverage={'state': 'partial', 'unchecked': ['synthetic range']},
            uncertainty=['synthetic uncertainty'])
        view = self.retain(output)
        for key in ('execution', 'error', 'error_code', 'failure_stage', 'coverage', 'uncertainty', 'stdout'):
            self.assertEqual(view[key], output[key])
        self.assertEqual(view['execution_policy']['new_restriction'], policy['new_restriction'])

    def test_complete_export_target_is_exact_without_inferred_profile(self):
        receipt = {'derived': True, 'complete_file': True, 'hash_scope': 'exported_bytes',
                   'destination': '/work/export', 'sha256': hashlib.sha256(b'x').hexdigest()}
        original = self.output(json.dumps(receipt))
        view = self.retain(original)
        self.assertEqual(view['retained_export_target'], {'kind': 'workspace_export', 'pointer': '/stdout',
                         'source_handle': view['source_handle']})
        for changes in ({'complete_file': False}, {'sha256': 'bad'}, {'destination': '/work/../outside'}):
            with self.subTest(changes=changes):
                invalid = self.retain(self.output(json.dumps({**receipt, **changes})))
                self.assertNotIn('retained_export_target', invalid)
        for changes in ({'error': 'failed'}, {'preview_truncated': {'stdout': True}},
                        {'execution': {'exit_code': 0, 'termination_confirmed': False}}):
            with self.subTest(changes=changes):
                invalid = self.retain(self.output(json.dumps(receipt), **changes))
                self.assertNotIn('retained_export_target', invalid)

    def test_labelled_batch_has_exact_bounded_line_selectors_and_unchanged_stdout(self):
        receipt = {'derived': True, 'complete_file': True, 'hash_scope': 'exported_bytes',
                   'destination': '/work/export', 'sha256': hashlib.sha256(b'x').hexdigest()}
        stdout = '\n'.join('== selected\n' + json.dumps(receipt) for _ in range(66)) + '\n'
        original = self.output(stdout)
        saved = deepcopy(original)
        view = self.retain(original)
        self.assertEqual(original, saved)
        self.assertEqual(view['stdout'], stdout)
        retained = self.case.event(view['evidence_id'])['data']
        self.assertEqual(retained['stdout'], stdout)
        self.assertEqual(retained['raw_output'], original['raw_output'])
        self.assertNotIn('retained_export_targets', retained)
        self.assertNotIn('retained_export_target', view)
        self.assertEqual(view['retained_export_target_count'], 66)
        self.assertEqual(view['omitted_retained_export_target_count'], 2)
        self.assertEqual(len(view['retained_export_targets']), 64)
        for index, target in enumerate(view['retained_export_targets']):
            self.assertEqual(target, {'kind': 'workspace_export', 'pointer': '/stdout',
                'source_handle': view['source_handle'], 'export_line': 2 * (index + 1)})
        pruned = json.loads(summarize('forsic_shell', {}, json.dumps(view)))
        self.assertGreaterEqual(len(pruned['retained_export_targets']), 1)
        self.assertEqual(pruned['retained_export_target_count'], 66)
        self.assertEqual(pruned['omitted_retained_export_target_count'], 66-len(pruned['retained_export_targets']))
        self.assertEqual(pruned['retained_export_targets'][0], view['retained_export_targets'][0])
        self.assertEqual(pruned['shell_result_lookup']['arguments']['pointer'], '/stdout')
        self.assertNotIn('stdout', pruned)

    def test_single_pretty_json_stays_compatible_and_failed_batches_have_no_selectors(self):
        receipt = {'derived': True, 'complete_file': True, 'hash_scope': 'exported_bytes',
                   'destination': '/work/export', 'sha256': hashlib.sha256(b'x').hexdigest()}
        pretty = self.retain(self.output(json.dumps(receipt, indent=2)))
        self.assertEqual(pretty['retained_export_target']['pointer'], '/stdout')
        self.assertNotIn('export_line', pretty['retained_export_target'])
        stdout = '== selected\n' + json.dumps(receipt) + '\n'
        for changes in ({'preview_truncated': {'stdout': True}}, {'error': 'failed'},
                {'execution': {'exit_code': 1, 'termination_confirmed': True}},
                {'execution': {'exit_code': 0, 'termination_confirmed': False}}):
            with self.subTest(changes=changes):
                view = self.retain(self.output(stdout, **changes))
                self.assertNotIn('retained_export_targets', view)

