"""Actual synthetic image export CLI → native receipt → private triage staging.

Only container execution and the image filesystem are substituted; no Docker,
live evidence, model or network is used.
"""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from forsic_plugin import image_access as access
from forsic_plugin import shell_workspace as shell
from forsic_plugin.context_results import summarize
from forsic_plugin.report_driven.host import source_record
import test_image_access as image_tests
import test_linux_persistence as persistence_tests
import test_shell_integration as shell_tests
import test_triage_profiles as triage_tests


class ExportTriageContractTests(unittest.TestCase):
    setUp = shell_tests.ShellIntegrationTests.setUp
    call = shell_tests.ShellIntegrationTests.call
    args = triage_tests.TriageBoundaryTests.args
    backend = triage_tests.TriageBoundaryTests.backend
    invoke = triage_tests.TriageBoundaryTests.invoke
    secret = triage_tests.TriageBoundaryTests.secret

    def export(self, *, suppress_stdout=True, suppress_stderr=False, fail=False,
               execution=None, prefix='', preview=None, payload=b'\0' * 1152):
        fixture = image_tests.ImageAccessTests('test_export_preserves_exact_binary_and_source')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.payload.write_bytes(payload)
        workspace = shell._workspace(self.case, 'shell-conversation')
        destination = '/work/selected.wtmp'
        original_destination = access.new_destination

        def mounted_destination(path, work_root=Path('/work')):
            return original_destination(workspace / Path(path).relative_to('/work'), work_root=workspace)

        def container(*unused, **kwargs):
            stdout, stderr = io.StringIO(), io.StringIO()
            # Execute the real CLI and real streaming export, with /work mapped
            # to the same synthetic conversation workspace the scanner stages.
            with open(os.devnull, 'w') as sink, \
                    patch.object(access, 'evidence_image', return_value=fixture.image), \
                    patch.object(access, 'new_destination', side_effect=mounted_destination), \
                    redirect_stdout(sink if suppress_stdout else stdout), \
                    redirect_stderr(sink if suppress_stderr else stderr):
                if fail:
                    with patch.object(fixture.node, 'open', side_effect=OSError('synthetic read failure')):
                        code = access.main(['/evidence/synthetic.e01', 'export', '--file-path', '/synthetic.bin',
                                            '--destination', destination])
                else:
                    code = access.main(['/evidence/synthetic.e01', 'export', '--file-path', '/synthetic.bin',
                                        '--destination', destination])
            self.cli_stdout, self.cli_stderr = stdout.getvalue(), stderr.getvalue()
            return {'stdout': self.cli_stdout.encode(),
                    'stderr': b'' if suppress_stderr else (prefix + self.cli_stderr).encode(),
                    'execution': execution or {'state': 'returned', 'exit_code': code,
                                              'termination_confirmed': True}}

        command = ('python /opt/forsic/image_access.py /evidence/synthetic.e01 export '
                   '--file-path /synthetic.bin --destination /work/selected.wtmp')
        if suppress_stdout:
            command += ' >/dev/null'
        if suppress_stderr:
            command += ' 2>/dev/null'
        with patch.object(shell, 'run_container', side_effect=container):
            result = self.call('forsic_shell', {'command': command})
        if preview:
            # Test the retention guard using the exact already-produced bytes.
            data = deepcopy(self.case.event(result['evidence_id'])['data'])
            data['preview_truncated'] = preview
            with patch.object(shell, 'invoke', return_value=data):
                result = self.call('forsic_shell', {'command': command})
        return result, workspace

    def test_redirected_stdout_keeps_exact_stderr_target_into_scanner(self):
        source, workspace = self.export(prefix='synthetic diagnostic\n')
        self.assertEqual(source['stdout'], '')
        self.assertEqual(self.cli_stdout, '')
        self.assertTrue(json.loads(self.cli_stderr)['complete_file'])
        self.assertNotIn('error', source, source)
        target = source['retained_export_targets'][0]
        self.assertEqual((target['pointer'], target['export_line']), ('/stderr', 2))
        pruned = json.loads(summarize('forsic_shell', {}, json.dumps(source)))
        self.assertEqual(pruned['retained_export_targets'][0], target)
        self.assertEqual(pruned['shell_result_lookup']['arguments']['pointer'], '/stderr')
        value, runner = self.invoke(self.args(targets=[target]))
        self.assertNotIn('error', value, value)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual((workspace/'selected.wtmp').read_bytes(), b'\0' * 1152)
        recheck = value['candidates'][0]['source_call']
        selected = self.call(recheck['tool'], recheck['arguments'])
        self.assertNotIn('error', selected, selected)
        self.assertEqual(selected['pointer'], '/stderr')
        identity = json.loads((self.case.output/'.triage-artifacts'/value['scan_id']/'manifest.json').read_text())['identity']
        item = identity['inputs'][0]
        self.assertEqual(item['source_ref']['pointer'], '/stderr')
        self.assertEqual(item['source_ref']['export_line'], 2)
        self.assertEqual(item['coverage']['original_linkage'], 'retained_command_declaration')

    def test_mirrored_receipt_prefers_stdout_without_duplicate_target(self):
        source, _ = self.export(suppress_stdout=False)
        self.assertEqual(source['stdout'], source['stderr'])
        self.assertEqual(source['retained_export_target']['pointer'], '/stdout')
        self.assertNotIn('retained_export_targets', source)

    def test_redirected_export_to_real_persistence_helper_preserves_candidate_range(self):
        payload = b'# synthetic comment\r\n@reboot /bin/synthetic-job\r\n'
        source, workspace = self.export(payload=payload)
        target = source['retained_export_target']
        args = persistence_tests.PersistenceBoundaryTests.persistence_args(self, target)
        backend = persistence_tests.PersistenceBoundaryTests.persistence_backend.__get__(self)
        value, runner = self.invoke(args, backend)
        self.assertNotIn('error', value, value)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(value['coverage']['state'], 'complete')
        self.assertEqual(value['summary']['candidate_count'], 1)
        candidate = value['candidates'][0]
        self.assertEqual(candidate['status'], 'candidate_not_adopted')
        self.assertEqual(candidate['entry_kind'], 'cron_macro')
        span = candidate['source_recheck']
        self.assertEqual((span['line_start'], span['line_end']), (2, 2))
        self.assertEqual((workspace/'selected.wtmp').read_bytes()[span['byte_start']:span['byte_end']],
                         b'@reboot /bin/synthetic-job\r\n')
        self.assertEqual(span['source_ref'], {**source['source_ref'], 'pointer': '/stderr'})
        selected = self.call(candidate['source_call']['tool'], candidate['source_call']['arguments'])
        self.assertNotIn('error', selected, selected)
        self.assertEqual(selected['pointer'], '/stderr')
        self.assertNotIn('original_byte_call', candidate)

    def test_failed_incomplete_uncertain_or_lost_receipt_fails_closed(self):
        for options in ({'fail': True}, {'suppress_stderr': True},
                        {'execution': {'state': 'returned', 'exit_code': 1, 'termination_confirmed': True}},
                        {'execution': {'state': 'completion_unknown', 'exit_code': 0, 'termination_confirmed': False}},
                        {'preview': {'stdout': False, 'stderr': True}}):
            with self.subTest(options=options):
                source, workspace = self.export(**options)
                self.assertNotIn('retained_export_target', source)
                self.assertNotIn('retained_export_targets', source)
                if options.get('fail'):
                    self.assertEqual(self.cli_stderr, '')
                    self.assertFalse((workspace/'selected.wtmp').exists())
                # Each invocation uses the same deliberately selected name.
                (workspace/'selected.wtmp').unlink(missing_ok=True)

    def test_incomplete_receipt_cannot_be_submitted_from_successful_shell(self):
        source, _ = self.export()
        data = deepcopy(self.case.event(source['evidence_id'])['data'])
        receipt = json.loads(data['stderr'])
        receipt['complete_file'] = False
        stderr = json.dumps(receipt).encode()
        output = shell._present(data, shell._raw_output(b'', stderr, complete=True, limit=shell.MAX_OUTPUT_BYTES))
        with patch.object(shell, 'invoke', return_value=output):
            invalid = self.call('forsic_shell', {'command': 'synthetic incomplete retained receipt'})
        self.assertNotIn('retained_export_target', invalid)
        self.assertNotIn('retained_export_targets', invalid)
        target = {'source_handle': invalid['source_handle'], 'kind': 'workspace_export', 'pointer': '/stderr'}
        value, runner = self.invoke(self.args(targets=[target]))
        self.assertIn('error', value, value)
        self.assertEqual(runner.call_count, 0)

    def test_exact_source_version_session_and_hash_remain_required(self):
        source, workspace = self.export()
        target = source['retained_export_target']
        data = self.case.event(source['evidence_id'])['data']
        wrong_session = self.case.record('tool_result', data, 'other-conversation')
        other = self.case.event(wrong_session)
        wrong_source = self.call('forsic_list', {})
        targets = [
            {**target, 'source_handle': 'invented-source'},
            {'kind': 'workspace_export', 'source_id': source['source_ref']['id'], 'source_version': '0'*64, 'pointer': '/stderr'},
            {'kind': 'workspace_export', 'source_id': wrong_session, 'source_version': source_record(other)['version'], 'pointer': '/stderr'},
            {'kind': 'workspace_export', 'source_id': wrong_source['source_ref']['id'], 'source_version': wrong_source['source_ref']['version'], 'pointer': '/stderr'},
        ]
        for selected in targets:
            with self.subTest(target=selected):
                value, runner = self.invoke(self.args(targets=[selected]))
                self.assertIn('error', value, value)
                self.assertEqual(runner.call_count, 0)
        (workspace/'selected.wtmp').write_bytes(b'changed derived bytes')
        value, runner = self.invoke(self.args(targets=[target]))
        self.assertIn('error', value, value)
        self.assertEqual(runner.call_count, 0)


if __name__ == '__main__':
    unittest.main()
