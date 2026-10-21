"""Initial scan intent uses real native admission; the hook never executes tools."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from forsic_plugin.capability_policy import policy
from forsic_plugin.onboarding import initial_triage
from forsic_plugin.turn_context import section_id
import test_question_reference_integration as fixtures


class InitialTriageContextTests(unittest.TestCase):
    def plan(self, environment='unknown', purpose='comprehensive', capabilities=None):
        return initial_triage({'onboarding': {'purpose': purpose, 'environment': environment}},
                              capabilities or policy())

    def test_declared_os_never_becomes_an_observed_os_or_executed_scan(self):
        unknown = self.plan()
        self.assertEqual(unknown['profiles'], [])
        self.assertEqual(unknown['state'], 'os_identification_required')
        self.assertEqual(self.plan('mixed')['profiles'], [])
        linux = self.plan('linux')
        self.assertIn('linux_persistence', linux['profiles'])
        self.assertNotIn('windows_evtx', linux['profiles'])
        self.assertEqual(linux['environment_basis'], 'user_selection_not_observed')
        self.assertFalse(linux['runtime_verified'])
        self.assertTrue(linux['execution_requested'])
        self.assertEqual(linux['role'], 'initial_scan_request')
        self.assertEqual(self.plan('macos')['state'], 'no_registered_profile')

    def test_disabled_scanning_retains_request_and_reason_without_an_invalid_lookup(self):
        disabled = self.plan(capabilities=policy({'tools': {'forsic_shell': False}}))
        self.assertEqual(disabled['state'], 'tool_disabled')
        self.assertTrue(disabled['execution_requested'])
        self.assertEqual(disabled['profiles'], [])
        self.assertNotIn('lookup', disabled)
        self.assertIn('record', disabled)

    def test_non_comprehensive_requests_are_unchanged_and_legacy_choice_has_same_intent(self):
        self.assertIsNone(self.plan(purpose='report'))
        self.assertIsNone(self.plan(purpose='focused'))
        self.assertIsNone(self.plan(purpose='custom'))
        self.assertNotIn('procedure', self.plan(capabilities=policy({'skills': {'source-triage': False}})))
        self.assertIsNone(initial_triage({}, policy()))
        legacy = initial_triage({'question': '종합 침해 분석'}, policy())
        self.assertTrue(legacy['execution_requested'])
        self.assertEqual(legacy['state'], 'os_identification_required')
        self.assertIsNone(initial_triage({'question': '종합 침해 분석',
            'onboarding': {'purpose': 'report'}}, policy()))


class InitialNativeAdmissionTests(unittest.TestCase):
    setUp = fixtures.QuestionReferenceIntegrationTests.setUp

    def admit(self, turn_id):
        procedure = (Path(__file__).parent/'config/AGENTS.md').read_text()
        result = self.before(session_id=self.f.sid, platform='tui', user_message=self.f.goal.goal,
            turn_id=turn_id, system_prompt_section_ids=(section_id(procedure),))
        return json.loads(result['context'])

    def test_initial_native_question_requests_scan_once_without_running_or_fabricating_it(self):
        f = self.f
        config = json.loads(f.manifest.read_text())
        config['onboarding'] = {'purpose': 'comprehensive', 'environment': 'unknown', 'question': ''}
        f.manifest.write_text(json.dumps(config))
        procedure = (Path(__file__).parent/'config/AGENTS.md').read_text()
        args = dict(session_id=f.sid, platform='tui',
                    system_prompt_section_ids=(section_id(procedure),))
        with patch('forsic_plugin.shell_workspace.invoke') as scanner:
            status = self.before(**args, user_message='진행 상태', turn_id='status')
            self.assertNotIn('Initial triage planning:', status['context'])
            first = self.before(**args, user_message=f.goal.goal, turn_id='start')
            body = json.loads(first['context'])
            plan = json.JSONDecoder().raw_decode(body['intake'].split('Initial triage planning: ', 1)[1])[0]
            self.assertEqual(plan['role'], 'initial_scan_request')
            self.assertTrue(plan['execution_requested'])
            self.assertEqual(plan['state'], 'os_identification_required')
            self.assertEqual(plan['profiles'], [])
            self.assertIn('record', plan)
            resumed = self.before(**args, user_message=f.goal.goal, turn_id='resume')
            self.assertNotIn('Initial triage planning:', resumed['context'])
            scanner.assert_not_called()
        from forsic_plugin.report_driven import host
        state = host.current(f.case)
        self.assertEqual(len(state['questions']), 1)
        self.assertTrue(all(c['status'] == 'candidate' and not c['source_refs']
                            for c in state['claims']))
        self.assertFalse(state['sources'])

    def test_linux_native_admission_carries_feasible_profile_intent_without_claiming_runtime(self):
        f = self.f
        config = json.loads(f.manifest.read_text())
        config['onboarding'] = {'purpose': 'comprehensive', 'environment': 'linux', 'question': ''}
        f.manifest.write_text(json.dumps(config))
        first = self.admit('linux-start')
        # Decode the actual registered before_turn output, after the real neutral question save.
        plan = json.JSONDecoder().raw_decode(first['intake'].split('Initial triage planning: ', 1)[1])[0]
        self.assertEqual(plan['profiles'], ['linux_persistence', 'linux_login_parser'])
        self.assertEqual(plan['environment_basis'], 'user_selection_not_observed')
        self.assertFalse(plan['runtime_verified'])
        self.assertTrue(plan['execution_requested'])
        from forsic_plugin.report_driven import host
        state = host.current(f.case)
        self.assertEqual(len(state['questions']), 1)
        self.assertFalse(state['sources'])

    def test_non_comprehensive_native_admission_does_not_request_initial_scan(self):
        f = self.f
        config = json.loads(f.manifest.read_text())
        config['onboarding'] = {'purpose': 'report', 'environment': 'linux', 'question': ''}
        f.manifest.write_text(json.dumps(config))
        first = self.admit('report-start')
        self.assertNotIn('Initial triage planning:', first['intake'])
        from forsic_plugin.report_driven import host
        self.assertEqual(len(host.current(f.case)['questions']), 1)

    def export_presentation(self, receipt_id):
        from forsic_plugin.report_driven.source_handles import source_handle
        source = {'id': receipt_id, 'version': 'synthetic-version-' + receipt_id}
        handle = source_handle(self.f.case, source)
        return {'tool': 'forsic_shell', 'evidence_id': receipt_id, 'source_ref': source,
            'source_handle': handle, 'stdout': 'unchanged derived export receipts',
            'retained_export_targets': [{'source_handle': handle, 'kind': 'workspace_export',
                'pointer': '/stdout', 'export_line': line} for line in (2, 4)]}

    def compare_exports(self, body):
        from forsic_plugin.loop_comparison import project
        return project('forsic_shell', {'command': 'synthetic exports'}, json.dumps(body))['result']

    def test_export_selector_receipt_identity_is_not_native_loop_progress(self):
        one, two = self.export_presentation('receipt-one'), self.export_presentation('receipt-two')
        original = json.loads(json.dumps(one))
        self.assertNotEqual(one['retained_export_targets'], two['retained_export_targets'])
        self.assertEqual(self.compare_exports(one), self.compare_exports(two))
        self.assertEqual(one, original)
        for target in self.compare_exports(one)['retained_export_targets']:
            self.assertEqual(set(target), {'kind', 'pointer', 'export_line'})

    def test_export_selector_scope_and_external_identity_remain_loop_progress(self):
        original = self.export_presentation('receipt-one')
        for key, value in (('export_line', 3), ('pointer', '/different'), ('kind', 'retained_text')):
            changed = json.loads(json.dumps(original))
            changed['retained_export_targets'][0][key] = value
            self.assertNotEqual(self.compare_exports(original), self.compare_exports(changed))
        external = self.export_presentation('external')['source_handle']
        changed = json.loads(json.dumps(original))
        changed['retained_export_targets'][0]['source_handle'] = external
        target = self.compare_exports(changed)['retained_export_targets'][0]
        self.assertEqual(target['source_handle'], external)
        self.assertNotEqual(self.compare_exports(original), self.compare_exports(changed))
        changed['retained_export_targets'][1]['source_version'] = 'conflicting-version'
        self.assertIn('source_handle', self.compare_exports(changed)['retained_export_targets'][1])


if __name__ == '__main__':
    unittest.main()
