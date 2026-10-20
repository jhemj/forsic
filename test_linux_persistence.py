"""Offline synthetic persistence parsing and retained native source contracts."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from forsic_plugin import triage_policy as policy, triage_scan as scan, shell_workspace as shell
import test_triage_profiles as fixtures
import test_shell_integration as integration


class PersistenceHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def execute(self, payload, fmt='cron_user', **changes):
        source = self.directory/'selected'
        source.write_bytes(payload)
        request = {'schema': policy.SCHEMA, 'scan_id': 'a'*32, 'profile': 'linux_persistence',
            'timeout_seconds': 10, 'candidate_limit': 20, 'candidate_index_limit': 20000,
            'inputs': [{'input_id': 'F0001', 'path': str(source), 'bytes': len(payload),
                'sha256': hashlib.sha256(payload).hexdigest(), 'triage_format': fmt}], **changes}
        path = self.directory/'request.json'; path.write_text(json.dumps(request))
        (self.directory/'result.json').unlink(missing_ok=True)
        done = subprocess.run([sys.executable, '-B', str(Path(scan.__file__).resolve()),
            '--request', str(path)], capture_output=True, timeout=15, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        body = json.loads((self.directory/'result.json').read_bytes())
        self.assertEqual(body['engine_identity']['helper_sha256'], hashlib.sha256(Path(scan.__file__).read_bytes()).hexdigest())
        return body

    def test_actual_subprocess_cron_formats_macros_environment_and_exact_crlf_spans(self):
        payload = '# 설명\r\nSECRET=private-value\r\n@reboot /bin/private-command\r\n*/5 * * * * /bin/normal\r\n'.encode()
        body = self.execute(payload)
        self.assertEqual(body['inputs'][0]['state'], 'complete')
        self.assertEqual(body['inputs'][0]['candidate_count'], 2)
        self.assertEqual([c['entry_kind'] for c in body['candidates']], ['cron_macro', 'cron_schedule'])
        first = body['candidates'][0]
        self.assertEqual(first['line_start'], 3)
        self.assertEqual(payload[first['byte_start']:first['byte_end']], b'@reboot /bin/private-command\r\n')
        self.assertNotIn('private', json.dumps(body))
        system = self.execute(b'@daily root /bin/job\n* * * * * root /bin/job\n', 'cron_system')
        self.assertEqual(system['inputs'][0]['candidate_count'], 2)
        wrong = self.execute(b'@daily /bin/job\n* * * * * /bin/job\n', 'cron_system')
        self.assertEqual(wrong['inputs'][0]['state'], 'partial')
        self.assertEqual(wrong['inputs'][0]['candidate_count'], 0)

    def test_systemd_repetitions_reset_continuations_and_opaque_values(self):
        payload = b'[Service]\nExecStart=/bin/private-command\nExecStart=\nExecStart=/bin/other \\\n# comment\n --secret=value\nEnvironment=TOKEN=private-value\n'
        body = self.execute(payload, 'systemd_unit')
        self.assertEqual(body['inputs'][0]['state'], 'complete')
        self.assertEqual(body['inputs'][0]['candidate_count'], 4)
        self.assertEqual([c['value_empty'] for c in body['candidates']], [False, True, False, False])
        continued = body['candidates'][2]
        self.assertEqual((continued['line_start'], continued['line_end']), (4, 6))
        self.assertIn(b'# comment', payload[continued['byte_start']:continued['byte_end']])
        self.assertNotIn('private', json.dumps(body))
        self.assertNotIn('ExecStart', json.dumps(body))

    def test_unsupported_partial_empty_and_candidate_index_limits(self):
        for raw in (b'\xff', b'x\0y'):
            body = self.execute(raw)
            self.assertEqual(body['inputs'][0]['state'], 'skipped')
            self.assertFalse(body['inputs'][0]['counts_complete'])
        body = self.execute(b'@unknown /bin/job\n@reboot /bin/job\n')
        self.assertEqual(body['inputs'][0]['reason_codes'], ['unsupported_syntax'])
        self.assertEqual(body['inputs'][0]['state'], 'partial')
        self.assertEqual(self.execute(b'')['inputs'][0]['candidate_count'], 0)
        body = self.execute(b'@reboot /bin/job\n'*4, candidate_limit=1, candidate_index_limit=2)
        self.assertEqual(body['inputs'][0]['candidate_count'], 4)
        self.assertEqual(len(body['candidate_index']), 2)
        self.assertEqual(len(body['candidates']), 1)
        body = self.execute(b'[Service]\nExecStart=x\\', 'systemd_unit')
        self.assertEqual(body['inputs'][0]['state'], 'partial')
        self.assertFalse(body['inputs'][0]['counts_complete'])

    def test_deadline_and_line_budget_are_partial_not_clean(self):
        path = self.directory/'source'; path.write_bytes(b'@reboot job\n'*3)
        item = {'input_id': 'F0001', 'path': str(path), 'triage_format': 'cron_user'}
        request = {'candidate_limit': 20}
        with patch.object(scan, 'MAX_PERSISTENCE_LINES', 1):
            scope, candidates = scan.persistence(item, request, self.directory, time.monotonic()+10)
        self.assertEqual(scope['reason_codes'], ['line_limit'])
        self.assertEqual(len(candidates), 1)
        scope, candidates = scan.persistence(item, request, self.directory, time.monotonic()-1)
        self.assertEqual(scope['reason_codes'], ['deadline_reached'])
        self.assertFalse(scope['counts_complete'])


class PersistenceBoundaryTests(unittest.TestCase):
    setUp = fixtures.TriageBoundaryTests.setUp
    call = fixtures.TriageBoundaryTests.call
    target = fixtures.TriageBoundaryTests.target
    invoke = fixtures.TriageBoundaryTests.invoke
    args = fixtures.TriageBoundaryTests.args
    backend = fixtures.TriageBoundaryTests.backend
    secret = fixtures.TriageBoundaryTests.secret
    def persistence_backend(self, command, container, native, **kwargs):
        mounts = {}
        for i, part in enumerate(command):
            if part == '--mount':
                values = dict(x.split('=', 1) for x in next(csv.reader([command[i+1]])) if '=' in x)
                mounts[values['dst']] = Path(values['src'])
        self.assertEqual(command[command.index('--network')+1], 'none')
        request = json.loads(mounts['/private/request.json'].read_bytes())
        for item in request['inputs']:
            item['path'] = str(mounts['/scan-input']/Path(item['path']).name)
        request_path = mounts['/private']/'synthetic-request.json'
        request_path.write_text(json.dumps(request))
        done = subprocess.run([sys.executable, '-B', str(mounts['/opt/forsic/triage_scan.py']),
            '--request', str(request_path)], capture_output=True, timeout=15, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return {'stdout': done.stdout, 'stderr': done.stderr, 'execution': {
            'state': 'returned', 'exit_code': done.returncode, 'termination_confirmed': True}}

    def persistence_args(self, target):
        return {'action': 'triage', 'profile': 'linux_persistence',
            'targets': [{**target, 'triage_format': 'cron_user'}]}

    def test_real_helper_staging_projection_source_query_and_reuse(self):
        payload = b'# private-value\n@reboot /bin/private-command\n'
        args = self.persistence_args(self.target('selected.cron', payload))
        value, runner = self.invoke(args, self.persistence_backend)
        self.assertNotIn('error', value, value)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(value['coverage']['state'], 'complete')
        self.assertEqual(value['candidates'][0]['source_recheck']['line_start'], 2)
        self.assertNotIn('private', json.dumps(value))
        candidate = value['candidates'][0]
        self.assertEqual(candidate['status'], 'candidate_not_adopted')
        read = candidate['original_byte_call']
        original = self.call(read['tool'], read['arguments'])
        self.assertNotIn('error', original, original)
        source = candidate['source_call']
        self.assertNotIn('error', self.call(source['tool'], source['arguments']))
        query = value['candidate_query']['next_call']
        selected = self.call(query['tool'], query['arguments'])
        self.assertEqual(selected['candidates'], value['candidates'])
        again, runner = self.invoke(args, self.persistence_backend)
        runner.assert_not_called()
        self.assertTrue(again['reused'])

    def test_complete_image_export_handle_keeps_declared_original_source(self):
        payload = b'@reboot /bin/private-command\n'
        workspace = shell._workspace(self.case, 'shell-conversation')
        (workspace/'selected.cron').write_bytes(payload)
        receipt = {'derived': True, 'complete_file': True, 'hash_scope': 'exported_bytes',
            'sha256': hashlib.sha256(payload).hexdigest(), 'destination': '/work/selected.cron',
            'source': {'image': 'synthetic.E01', 'file_path': '/selected/original'}}
        output = integration.ShellIntegrationTests.output(self, json.dumps(receipt))
        with patch.object(shell, 'invoke', return_value=output):
            source = self.call('forsic_shell', {'command': 'synthetic export receipt'})
        target = source['retained_export_target']
        self.assertEqual(target, {'kind': 'workspace_export', 'source_handle': source['source_handle'], 'pointer': '/stdout'})
        args = self.persistence_args(target)
        value, runner = self.invoke(args, self.persistence_backend)
        self.assertNotIn('error', value, value)
        candidate = value['candidates'][0]
        self.assertEqual(candidate['source_recheck']['source_ref'], {**source['source_ref'], 'pointer': '/stdout'})
        self.assertEqual(candidate['source_recheck']['sha256'], receipt['sha256'])
        self.assertNotIn('original_byte_call', candidate)
        call = candidate['source_call']
        selected = self.call(call['tool'], call['arguments'])
        self.assertNotIn('error', selected, selected)
        self.assertIn('/selected/original', json.dumps(selected))
        (workspace/'selected.cron').write_bytes(b'changed')
        changed, runner = self.invoke(args, self.persistence_backend)
        runner.assert_not_called()
        self.assertIn('error', changed)

    def batch_export(self, **changes):
        payload = b'@reboot /bin/synthetic-command\n'
        workspace = shell._workspace(self.case, 'shell-conversation')
        (workspace/'selected.cron').write_bytes(payload)
        receipt = {'derived': True, 'complete_file': True, 'hash_scope': 'exported_bytes',
            'sha256': hashlib.sha256(payload).hexdigest(), 'destination': '/work/selected.cron',
            'source': {'image': 'synthetic.E01', 'file_path': '/selected/original'}}
        stdout = '== selected one\n' + json.dumps(receipt) + '\n== selected two\n' + json.dumps(receipt) + '\n'
        output = integration.ShellIntegrationTests.output(self, stdout, **changes)
        if changes:
            # Exercise staging admission independently of the native guard that
            # also blocks a conversation after an uncertain prior execution.
            from forsic_plugin.model_results import evidence_view
            retained = {**output, 'tool': 'forsic_shell'}
            source_id = self.case.record('tool_result', retained, session='shell-conversation')
            source = evidence_view({**retained, 'evidence_id': source_id}, self.case)
        else:
            with patch.object(shell, 'invoke', return_value=output):
                source = self.call('forsic_shell', {'command': 'synthetic batched export receipts'})
        return workspace, source

    def test_labelled_exports_select_exact_line_and_do_not_alias_replay(self):
        _, source = self.batch_export()
        first_target, second_target = source['retained_export_targets']
        self.assertEqual([first_target['export_line'], second_target['export_line']], [2, 4])
        first, runner = self.invoke(self.persistence_args(first_target), self.persistence_backend)
        self.assertNotIn('error', first, first)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(first['candidates'][0]['source_recheck']['source_ref'],
            {**source['source_ref'], 'pointer': '/stdout', 'export_line': 2})
        call = first['candidates'][0]['source_call']
        selected = self.call(call['tool'], call['arguments'])
        self.assertNotIn('error', selected, selected)
        second, runner = self.invoke(self.persistence_args(second_target), self.persistence_backend)
        self.assertNotIn('error', second, second)
        self.assertEqual(runner.call_count, 1)
        self.assertNotEqual(first['scan_id'], second['scan_id'])
        replay, runner = self.invoke(self.persistence_args(second_target), self.persistence_backend)
        runner.assert_not_called()
        self.assertEqual(replay['scan_id'], second['scan_id'])
        self.assertTrue(replay['reused'])

    def test_eleven_labelled_exports_stage_only_explicit_individual_receipts(self):
        workspace = shell._workspace(self.case, 'shell-conversation')
        receipts, lines = [], []
        for index in range(11):
            payload = f'@reboot /bin/synthetic-{index}\n'.encode()
            (workspace/f'selected-{index}').write_bytes(payload)
            receipt = {'derived': True, 'complete_file': True, 'hash_scope': 'exported_bytes',
                'sha256': hashlib.sha256(payload).hexdigest(), 'destination': f'/work/selected-{index}'}
            receipts.append(receipt)
            lines.extend([f'== selected-{index}', json.dumps(receipt)])
        stdout = '\n'.join(lines) + '\n'
        output = integration.ShellIntegrationTests.output(self, stdout)
        with patch.object(shell, 'invoke', return_value=output):
            source = self.call('forsic_shell', {'command': 'synthetic eleven exports'})
        self.assertEqual(len(source['retained_export_targets']), 11)
        for index in (1, 7, 10):
            target = source['retained_export_targets'][index]
            value, runner = self.invoke(self.persistence_args(target), self.persistence_backend)
            self.assertNotIn('error', value, value)
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(value['coverage']['selected_files'], 1)
            self.assertEqual(value['candidates'][0]['source_recheck']['sha256'], receipts[index]['sha256'])
            self.assertEqual(value['candidates'][0]['source_recheck']['source_ref']['export_line'], 2*(index+1))

    def test_batch_without_exact_line_and_invalid_line_fail_with_safe_guidance(self):
        _, source = self.batch_export()
        exact = source['retained_export_targets'][0]
        for target, reason in (({k: v for k, v in exact.items() if k != 'export_line'}, 'export_selection_required'),
                ({**exact, 'export_line': 1}, 'export_receipt_invalid'),
                ({**exact, 'export_line': 999}, 'export_receipt_invalid')):
            with self.subTest(reason=reason, target=target):
                result, runner = self.invoke(self.persistence_args(target), self.persistence_backend)
                runner.assert_not_called()
                self.assertEqual(result['error_code'], 'triage_rejected')
                self.assertEqual(result['admission_failure_code'], reason)
                self.assertIn('export_line', result['next_step'])
                self.assertEqual(result['execution']['state'], 'not_started')

    def test_export_completion_session_version_and_hash_guards_precede_launch(self):
        for changes in ({'preview_truncated': {'stdout': True}},
                {'raw_output': {'truncated': True, 'complete': True}},
                {'raw_output': {'truncated': False, 'complete': False}},
                {'execution': {'exit_code': 1, 'termination_confirmed': True}},
                {'execution': {'exit_code': 0, 'termination_confirmed': False}}):
            with self.subTest(changes=changes):
                _, source = self.batch_export(**changes)
                target = {'kind': 'workspace_export', 'source_handle': source['source_handle'],
                    'pointer': '/stdout', 'export_line': 2}
                result, runner = self.invoke(self.persistence_args(target), self.persistence_backend)
                runner.assert_not_called()
                self.assertEqual(result['admission_failure_code'], 'export_source_invalid')
        workspace, source = self.batch_export()
        target = source['retained_export_targets'][0]
        original_event = self.case.event
        def wrong_session(source_id):
            event = original_event(source_id)
            return {**event, 'session': 'other-conversation'} if source_id == source['evidence_id'] else event
        with patch.object(self.case, 'event', side_effect=wrong_session):
            result, runner = self.invoke(self.persistence_args(target), self.persistence_backend)
        runner.assert_not_called()
        self.assertEqual(result['admission_failure_code'], 'export_source_invalid')
        stale = {k: v for k, v in target.items() if k != 'source_handle'}
        stale.update(source_id=source['source_ref']['id'], source_version='f'*64)
        result, runner = self.invoke(self.persistence_args(stale), self.persistence_backend)
        runner.assert_not_called()
        self.assertEqual(result['admission_failure_code'], 'source_version_changed')
        (workspace/'selected.cron').write_bytes(b'changed')
        result, runner = self.invoke(self.persistence_args(target), self.persistence_backend)
        runner.assert_not_called()
        self.assertEqual(result['admission_failure_code'], 'export_changed')

    def test_unexpected_admission_error_does_not_expose_diagnostic(self):
        target = self.target('selected.cron', b'@reboot synthetic\n')
        with patch.object(policy.storage, '_stage', side_effect=ValueError('synthetic-private-diagnostic')):
            result, runner = self.invoke(self.persistence_args(target), self.persistence_backend)
        runner.assert_not_called()
        self.assertEqual(result['error_code'], 'triage_rejected')
        self.assertNotIn('admission_failure_code', result)
        self.assertNotIn('synthetic-private-diagnostic', json.dumps(result))

    def test_invalid_receipt_and_workspace_file_failures_use_fixed_public_codes(self):
        receipt = {'derived': True, 'complete_file': True, 'hash_scope': 'exported_bytes',
            'sha256': 'a'*64, 'destination': '/work/missing-synthetic-private-file'}
        cases = [({'complete_file': False}, 'export_receipt_invalid'),
            ({'sha256': 'synthetic-private-hash'}, 'export_receipt_invalid'),
            ({'destination': '/work/../synthetic-private-outside'}, 'export_destination_invalid'),
            ({'destination': None}, 'export_destination_invalid'),
            ({}, 'export_file_unavailable')]
        workspace = shell._workspace(self.case, 'shell-conversation')
        (workspace/'synthetic-private-link').symlink_to(self.case.root)
        cases.append(({'destination': '/work/synthetic-private-link'}, 'export_file_unavailable'))
        for changes, reason in cases:
            with self.subTest(reason=reason, changes=changes):
                stdout = '== synthetic-private-label\n' + json.dumps({**receipt, **changes}) + '\n'
                output = integration.ShellIntegrationTests.output(self, stdout)
                with patch.object(shell, 'invoke', return_value=output):
                    source = self.call('forsic_shell', {'command': 'synthetic invalid export receipt'})
                target = {'kind': 'workspace_export', 'source_handle': source['source_handle'],
                    'pointer': '/stdout', 'export_line': 2}
                result, runner = self.invoke(self.persistence_args(target), self.persistence_backend)
                runner.assert_not_called()
                self.assertEqual(result['admission_failure_code'], reason)
                self.assertIn('next_step', result)
                self.assertNotIn('synthetic-private', json.dumps(result))

    def test_formats_are_explicit_profile_specific_and_fingerprinted(self):
        target = self.target('selected.cron', b'@reboot root job\n')
        with self.assertRaises(ValueError):
            policy.validate({'action': 'triage', 'profile': 'linux_persistence', 'targets': [target]})
        with self.assertRaises(ValueError):
            policy.validate({**self.args(targets=[target]), 'targets': [{**target, 'triage_format': 'cron_user'}]})
        with self.assertRaises(ValueError):
            shell.validate({'action': 'sensitive_scan', 'profiles': ['secrets'],
                'targets': [{**target, 'triage_format': 'cron_user'}]})
        self.assertEqual(policy.recommendations('linux', 'persistence')['profiles'], ['linux_persistence'])
        args = self.persistence_args(target)
        first, _ = self.invoke(args, self.persistence_backend)
        args['targets'][0]['triage_format'] = 'cron_system'
        second, runner = self.invoke(args, self.persistence_backend)
        self.assertEqual(runner.call_count, 1)
        self.assertNotEqual(first['scan_id'], second['scan_id'])


if __name__ == '__main__':
    unittest.main()
