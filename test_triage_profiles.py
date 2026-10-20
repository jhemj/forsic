"""Synthetic bounded triage contracts. No Docker, case data, model or network."""
import csv
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forsic_plugin import shell_workspace as shell
from forsic_plugin import triage_policy as policy
from forsic_plugin import triage_scan as scan
from forsic_plugin import login_records as login
from forsic_plugin.execution_contract import ExecutionFailure
from forsic_plugin.report_driven import host
import test_shell_integration as integration


class TriageBoundaryTests(unittest.TestCase):
    call = integration.ShellIntegrationTests.call
    setUp = integration.ShellIntegrationTests.setUp
    secret = 'synthetic-user secret=password@example.test'

    def target(self, name='selected.wtmp', payload=b'\x00'*384):
        path = self.case.root/name
        path.write_bytes(payload)
        source = self.call('forsic_list', {})
        index = next(i for i, value in enumerate(source['files']) if value['path'] == name)
        version = host.source_record(self.case.event(source['evidence_id']))['version']
        return {'kind': 'evidence_file', 'source_id': source['evidence_id'], 'source_version': version,
            'pointer': f'/files/{index}/path'}

    def args(self, **changes):
        return {'action': 'triage', 'profile': 'linux_login_parser',
            'login_format': 'linux-glibc-x86-le-384', 'targets': changes.pop('targets', None) or [self.target()], **changes}

    def backend(self, command, container, native, **kwargs):
        self.command = command
        mounts = {}
        for i, part in enumerate(command):
            if part == '--mount':
                values = dict(x.split('=', 1) for x in next(csv.reader([command[i+1]])) if '=' in x)
                mounts[values['dst']] = Path(values['src'])
        self.assertEqual(set(mounts), {'/private', '/scan-input', '/private/request.json',
            '/opt/forsic/triage_scan.py', '/opt/forsic/login_records.py'})
        self.assertNotIn(str(self.case.root), command)
        self.assertNotIn('/work', mounts)
        self.assertEqual(command[command.index('--network')+1], 'none')
        request = json.loads(mounts['/private/request.json'].read_bytes())
        inputs, candidates = [], []
        for item in request['inputs']:
            raw = (mounts['/scan-input']/Path(item['path']).name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item['sha256'])
            inputs.append({'input_id': item['input_id'], 'state': 'complete', 'candidate_count': 3,
                'records_parsed': 3, 'records_available': 3, 'counts_complete': True,
                'reason_codes': [], 'raw': self.secret})
            candidates.extend({'input_id': item['input_id'], 'ordinal': i+1,
                'record_index': i, 'record_type': 7, 'byte_start': i*384, 'byte_end': (i+1)*384,
                'ut_user': self.secret, 'raw_record_hex': self.secret, 'rule_id': self.secret} for i in range(3))
        body = {'schema': policy.SCHEMA, 'scan_id': request['scan_id'], 'profile': request['profile'],
            'inputs': inputs, 'candidates': candidates,
            'engine_identity': {'binary_sha256': 'b'*64, 'version': self.secret}, 'stderr': self.secret}
        policy.storage._write(mounts['/private']/'result.json', policy.storage._json(body))
        return {'stdout': self.secret.encode(), 'stderr': self.secret.encode(),
            'execution': {'state': 'returned', 'exit_code': 0, 'termination_confirmed': True,
                'cleanup': {'confirmed': True, 'stderr': self.secret}}}

    def invoke(self, args=None, backend=None):
        with patch.object(shell, '_image_id', return_value='sha256:'+'1'*64), \
                patch.object(shell, 'run_container', side_effect=backend or self.backend) as runner:
            value = self.call('forsic_shell', args or self.args(targets=[self.target(payload=b'\0'*1152)]), turn_id='triage-turn', tool_call_id='triage-call')
        return value, runner

    def test_schema_catalog_and_strict_profile_limits(self):
        catalog = shell.workspace_info()['triage']
        self.assertEqual(catalog['profiles']['linux_login_parser']['kind'], 'record_parser')
        self.assertIn('yara_x', catalog['unavailable_profiles'])
        args = self.args()
        for change in ({'targets': args['targets']*9}, {'candidate_limit': 21}, {'record_limit': True},
                {'timeout_seconds': 301}, {'profile': 'yara_x'}, {'login_format': None},
                {'command': 'echo secret'}, {'targets': [{**args['targets'][0], 'kind': 'retained_text'}]},
                {'targets': [{**args['targets'][0], 'encoding': 'cp949'}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                shell.validate({**args, **change})
        self.assertEqual(shell.validate({'action': 'triage', 'profile': 'windows_evtx', 'targets': args['targets']})['profile'], 'windows_evtx')
        self.assertEqual(shell.schema()['properties']['targets']['maxItems'], 64)  # Sensitive contract preserved.

    def test_bounded_private_candidates_native_retention_and_no_adoption(self):
        args = self.args(targets=[self.target(payload=b'\0'*1152)], candidate_limit=2)
        before = host.current(self.case)
        value, runner = self.invoke(args)
        self.assertNotIn('error', value, value)
        self.assertEqual(runner.call_count, 1)
        self.assertNotIn(self.secret, json.dumps(value))
        self.assertEqual(value['summary'], {'candidate_count': 3, 'returned_candidates': 2,
            'omitted_candidates': 1, 'counts_complete': True, 'count_scope': 'observed_output_candidates_only'})
        self.assertEqual(value['coverage']['state'], 'complete')
        self.assertFalse(value['original_evidence'])
        self.assertTrue(all(c['status'] == 'candidate_not_adopted' for c in value['candidates']))
        self.assertEqual(value['candidates'][0]['source_recheck']['byte_end'], 384)
        source_call = value['candidates'][0]['source_call']
        selected = self.call(source_call['tool'], source_call['arguments'])
        self.assertNotIn('error', selected, selected)
        retained = self.case.event(value['evidence_id'])['data']
        self.assertNotIn(self.secret, json.dumps(retained))
        after = host.current(self.case)
        for key in ('questions', 'claims', 'observations'):
            self.assertEqual(before[key], after[key])
        native = runner.call_args.args[2]
        self.assertEqual(native.record()['session_id'], 'shell-native')
        private = self.case.output/policy.PRIVATE_NAME/value['scan_id']
        self.assertIn(self.secret, (private/'stdout.bin').read_text())
        self.assertEqual(private.stat().st_mode & 0o077, 0)

    def test_stable_reuse_and_changed_versions_rejected(self):
        args = self.args(targets=[self.target(payload=b'\0'*1152)])
        first, runner = self.invoke(args)
        second, runner = self.invoke({**args, 'resume_ref': first['resume_ref']})
        self.assertTrue(second['reused'])
        self.assertEqual(runner.call_count, 0)
        self.assertEqual(first['scan_id'], second['scan_id'])
        changed, runner = self.invoke({**args, 'candidate_limit': 1, 'resume_ref': first['resume_ref']})
        self.assertEqual(changed['error_code'], 'triage_rejected')
        self.assertEqual(runner.call_count, 0)
        wrong = {**args, 'targets': [{**args['targets'][0], 'source_version': 'f'*64}]}
        changed, runner = self.invoke(wrong)
        self.assertEqual(changed['error_code'], 'triage_rejected')
        self.assertEqual(runner.call_count, 0)

    def test_workspace_admission_is_sequential_and_shared_with_shell(self):
        args = self.args()
        workspace = shell._workspace(self.case, 'shell-conversation')
        with shell._workspace_lock(workspace):
            value, runner = self.invoke(args)
        self.assertEqual(runner.call_count, 0)
        self.assertEqual(value['execution']['state'], 'not_started')

    def test_native_cancel_before_admission_has_no_probe_or_launch(self):
        args = self.args()
        native = SimpleNamespace(interrupted=lambda: True)
        with patch.object(shell, '_image_id') as image, patch.object(shell, 'run_container') as runner:
            value = shell.invoke(self.case, args, 'conversation', native)
        self.assertEqual(value['error_code'], 'cancelled_before_start')
        image.assert_not_called(); runner.assert_not_called()

    def test_failed_execution_never_claims_zero_or_complete(self):
        def failed(*args, **kwargs):
            return {'stdout': self.secret.encode(), 'stderr': self.secret.encode(),
                'execution': {'state': 'timed_out', 'exit_code': None, 'termination_confirmed': True}}
        value, _ = self.invoke(backend=failed)
        self.assertEqual(value['coverage']['state'], 'failed')
        self.assertNotIn('summary', value)
        self.assertNotIn(self.secret, json.dumps(value))

    def test_post_execution_faults_keep_actual_receipt_identity_and_failed_reuse(self):
        for fault in ('missing_result', 'corrupt_result', 'changed_input', 'projection'):
            with self.subTest(fault=fault):
                args = self.args(targets=[self.target(fault+'.wtmp', b'\0'*1152)])
                def backend(command, container, native, **kwargs):
                    returned = self.backend(command, container, native, **kwargs)
                    mounts = {}
                    for i, part in enumerate(command):
                        if part == '--mount':
                            values = dict(x.split('=', 1) for x in next(csv.reader([command[i+1]])) if '=' in x)
                            mounts[values['dst']] = Path(values['src'])
                    result = mounts['/private']/'result.json'
                    if fault == 'missing_result':
                        result.unlink()
                    elif fault == 'corrupt_result':
                        result.write_bytes(b'not JSON '+self.secret.encode())
                    elif fault == 'changed_input':
                        (mounts['/scan-input']/'F0001').write_bytes(b'changed staged bytes')
                    return returned
                if fault == 'projection':
                    with patch.object(policy, '_project', side_effect=ValueError(self.secret)):
                        value, runner = self.invoke(args, backend)
                else:
                    value, runner = self.invoke(args, backend)
                self.assertEqual(runner.call_count, 1)
                self.assertEqual(value['error_code'], 'triage_result_processing_failed')
                self.assertEqual(value['failure_stage'], 'result_processing')
                self.assertEqual(value['execution']['state'], 'returned')
                self.assertEqual(value['execution']['exit_code'], 0)
                self.assertTrue(value['execution']['termination_confirmed'])
                self.assertEqual(value['scan_id'], value['resume_ref'])
                self.assertIn('provenance', value)
                self.assertNotIn(self.secret, json.dumps(value))
                manifest = json.loads((self.case.output/policy.PRIVATE_NAME/value['scan_id']/'manifest.json').read_bytes())
                self.assertEqual(manifest['state'], 'retained')
                again, runner = self.invoke(args)
                runner.assert_not_called()
                self.assertTrue(again['reused'])
                self.assertEqual(again['scan_id'], value['scan_id'])
                self.assertEqual(again['error_code'], value['error_code'])
                self.assertEqual(again['execution'], value['execution'])

    def test_retention_write_fault_keeps_actual_execution_and_scan_identity(self):
        args = self.args(targets=[self.target(payload=b'\0'*1152)])
        original = policy.storage._write
        def write(path, raw, **kwargs):
            if path.name == 'safe.json':
                raise OSError(self.secret)
            return original(path, raw, **kwargs)
        with patch.object(policy.storage, '_write', side_effect=write):
            value, runner = self.invoke(args)
        self.assertEqual(value['failure_stage'], 'retention')
        self.assertEqual(value['execution']['state'], 'returned')
        self.assertEqual(value['execution']['exit_code'], 0)
        self.assertTrue(value['execution']['termination_confirmed'])
        self.assertEqual(value['scan_id'], value['resume_ref'])
        again, runner = self.invoke(args)
        runner.assert_not_called()
        self.assertIn('error', again)

    def test_post_execution_lock_mismatch_retains_failure_and_reuses_without_launch(self):
        args = {'action': 'triage', 'profile': 'windows_evtx',
            'targets': [self.target('lock-mismatch.evtx', b'ElfFile\0'+b'\0'*1144)]}
        # The fake backend omits the required installed lock identity.
        value, runner = self.invoke(args)
        self.assertEqual(value['error_code'], 'triage_result_processing_failed')
        self.assertEqual(value['failure_stage'], 'result_processing')
        self.assertEqual(value['execution']['state'], 'returned')
        self.assertEqual(value['execution']['exit_code'], 0)
        again, runner = self.invoke(args)
        runner.assert_not_called()
        self.assertTrue(again['reused'])
        self.assertEqual(again['scan_id'], value['scan_id'])

    def test_cancel_during_execution_retains_native_interrupt_and_unknown_completion(self):
        error = KeyboardInterrupt()
        error.forsic_execution = {'state': 'cancelled', 'termination_confirmed': False}
        args = self.args()
        with patch.object(shell, '_image_id', return_value='sha256:'+'1'*64), \
                patch.object(shell, 'run_container', side_effect=error), self.assertRaises(KeyboardInterrupt):
            shell.invoke(self.case, args, 'conversation')
        value = error.forsic_shell_result
        self.assertEqual(value['error_code'], 'cancel_requested')
        self.assertFalse(value['execution']['termination_confirmed'])
        manifest = json.loads((self.case.output/policy.PRIVATE_NAME/value['scan_id']/'manifest.json').read_bytes())
        self.assertEqual(manifest['state'], 'completion_unknown')

    def test_cancel_and_receipt_write_failure_reraises_same_interrupt_without_replay(self):
        original = policy.storage._write
        for filename in ('safe.json', 'manifest.json'):
            with self.subTest(filename=filename):
                error = KeyboardInterrupt('synthetic cancellation')
                error.forsic_execution = {'state': 'cancelled', 'termination_confirmed': True,
                    'cleanup': {'confirmed': True}}
                args = self.args(targets=[self.target(filename+'.wtmp')])
                def write(path, raw, **kwargs):
                    if path.name == filename and (filename != 'manifest.json' or kwargs.get('replace')):
                        raise OSError(self.secret)
                    return original(path, raw, **kwargs)
                with patch.object(shell, '_image_id', return_value='sha256:'+'1'*64), \
                        patch.object(shell, 'run_container', side_effect=error) as runner, \
                        patch.object(policy.storage, '_write', side_effect=write), \
                        self.assertRaises(KeyboardInterrupt) as caught:
                    shell.invoke(self.case, args, 'conversation')
                self.assertIs(caught.exception, error)
                self.assertEqual(runner.call_count, 1)
                value = error.forsic_shell_result
                self.assertEqual(value['error_code'], 'cancel_requested')
                self.assertEqual(value['execution']['state'], 'cancelled')
                self.assertTrue(value['execution']['termination_confirmed'])
                self.assertEqual(error.forsic_execution, value['execution'])
                self.assertEqual(value['scan_id'], value['resume_ref'])
                self.assertIn('provenance', value)
                self.assertEqual(value['retention_failure']['error_code'], 'triage_retention_failed')
                self.assertNotIn(self.secret, json.dumps(value))
                with patch.object(shell, '_image_id', return_value='sha256:'+'1'*64), \
                        patch.object(shell, 'run_container') as runner:
                    repeated = shell.invoke(self.case, args, 'conversation')
                runner.assert_not_called()
                self.assertIn('error', repeated)

    def test_input_aggregate_budget_prevents_launch(self):
        args = self.args()
        with patch.object(policy.storage, 'MAX_JOB_BYTES', 1):
            value, runner = self.invoke(args)
        self.assertEqual(value['error_code'], 'triage_rejected')
        runner.assert_not_called()

    def test_windows_dispatch_preserves_binary_with_evtx_staging_suffix(self):
        payload = b'ElfFile\0' + bytes(range(256))
        args = {'action': 'triage', 'profile': 'windows_evtx', 'targets': [self.target('selected.evtx', payload)]}
        def backend(command, container, native, **kwargs):
            mounts = {}
            for i, part in enumerate(command):
                if part == '--mount':
                    values = dict(x.split('=', 1) for x in next(csv.reader([command[i+1]])) if '=' in x)
                    mounts[values['dst']] = Path(values['src'])
            request = json.loads(mounts['/private/request.json'].read_bytes())
            item = request['inputs'][0]
            self.assertTrue(item['path'].endswith('.evtx'))
            self.assertEqual((mounts['/scan-input']/Path(item['path']).name).read_bytes(), payload)
            body = {'schema': policy.SCHEMA, 'scan_id': request['scan_id'], 'profile': 'windows_evtx',
                'inputs': [{'input_id': item['input_id'], 'state': 'partial', 'candidate_count': 0,
                    'counts_complete': True, 'reason_codes': ['engine_diagnostics']}], 'candidates': [],
                'engine_identity': {'lock_sha256': scan.sha(Path(__file__).parent/'tools/forensic-tools.lock.json')}}
            policy.storage._write(mounts['/private']/'result.json', policy.storage._json(body))
            return {'stdout': b'', 'stderr': b'', 'execution': {
                'state': 'returned', 'exit_code': 0, 'termination_confirmed': True}}
        value, runner = self.invoke(args, backend)
        self.assertNotIn('error', value, value)
        self.assertEqual(value['coverage']['state'], 'partial')

    def test_workspace_export_recheck_call_retrieves_exact_retained_receipt(self):
        payload = b'\0'*1152
        workspace = shell._workspace(self.case, 'shell-conversation')
        (workspace/'selected.wtmp').write_bytes(payload)
        receipt = {'derived': True, 'complete_file': True, 'hash_scope': 'exported_bytes',
            'sha256': hashlib.sha256(payload).hexdigest(), 'destination': '/work/selected.wtmp',
            'source': {'image': 'synthetic.E01', 'file_path': '/var/log/wtmp'}}
        output = integration.ShellIntegrationTests.output(self, json.dumps(receipt))
        with patch.object(shell, 'invoke', return_value=output):
            source = self.call('forsic_shell', {'command': 'synthetic export receipt'})
        target = {'kind': 'workspace_export', 'source_id': source['source_ref']['id'],
            'source_version': source['source_ref']['version'], 'pointer': '/stdout'}
        value, runner = self.invoke(self.args(targets=[target]))
        self.assertNotIn('error', value, value)
        call = value['candidates'][0]['source_call']
        selected = self.call(call['tool'], call['arguments'])
        self.assertNotIn('error', selected, selected)
        self.assertIn('/work/selected.wtmp', json.dumps(selected))


class TriageHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def request(self, payload, **changes):
        path = self.directory/'input'
        path.write_bytes(payload)
        return {'schema': policy.SCHEMA, 'scan_id': 'a'*32, 'profile': 'linux_login_parser',
            'login_format': 'linux-glibc-x86-le-384', 'record_limit': 10, 'candidate_limit': 2,
            'timeout_seconds': 5, 'inputs': [{'input_id': 'F0001', 'path': str(path),
                'sha256': hashlib.sha256(payload).hexdigest()}], **changes}

    def actual_parser(self, argv, stem, deadline):
        # Only this repository's standard-library parser on synthetic bytes.
        argv = [sys.executable, '-B', str(Path(__file__).parent/'forsic_plugin/login_records.py'), *argv[3:]]
        return scan.run_original(argv, stem, deadline)

    def execute(self, request):
        with patch.object(scan, 'engine', return_value=({'binary_sha256': 'b'*64}, None)), \
                patch.object(scan, 'run_original', scan.run, create=True), \
                patch.object(scan, 'run', side_effect=self.actual_parser):
            return scan.execute(request, self.directory)

    def test_actual_login_parser_complete_counts_and_private_strings(self):
        entry = struct.Struct('<h2xi32s4s32s256shhiii16s20s')
        payload = entry.pack(7, 1, b'tty', b'id', b'synthetic-secret', b'private-host', 0, 0, 0, 1, 0, b'\0'*16, b'\0'*20)
        body = self.execute(self.request(payload*3))
        self.assertEqual(body['inputs'][0]['state'], 'complete')
        self.assertEqual(body['inputs'][0]['candidate_count'], 3)
        self.assertEqual(len(body['candidates']), 2)
        self.assertNotIn('synthetic-secret', json.dumps(body))
        self.assertIn('synthetic-secret', (self.directory/'F0001.stdout').read_text())

    def test_partial_limit_and_truncated_tail_counts_are_explicit(self):
        body = self.execute(self.request(b'\0'*1153, record_limit=1))
        scope = body['inputs'][0]
        self.assertEqual(scope['state'], 'partial')
        self.assertEqual(scope['records_available'], 3)
        self.assertEqual(scope['records_parsed'], 1)
        self.assertEqual(set(scope['reason_codes']), {'record_limit', 'truncated_tail'})
        self.assertFalse(scope['counts_complete'])

    def test_maximum_record_budget_fits_maximally_escaped_rows_and_returns_partial(self):
        # Each string byte expands to six JSON characters; use long signed
        # integers and both warnings to cover the larger fixed-field variants.
        extreme = login.ENTRY.pack(32767, -2147483648, b'\xff'*32, b'\xff'*4,
            b'\xff'*32, b'\xff'*256, -32768, -32768, -2147483648,
            -2147483648, -2147483648, b'\xff'*16, b'\xff'*20)
        rendered = login._json(login.decode_record(extreme, policy.MAX_RECORDS-1,
            '/scan-input/F0001', 'f'*64)).encode()+b'\n'
        self.assertLess(len(rendered), 4096)
        self.assertLess(policy.MAX_RECORDS*4096+16384, scan.OUTPUT_LIMIT)
        body = self.execute(self.request(extreme*(policy.MAX_RECORDS+1), record_limit=policy.MAX_RECORDS,
            timeout_seconds=30))
        scope = body['inputs'][0]
        self.assertEqual(scope['state'], 'partial')
        self.assertEqual(scope['records_parsed'], policy.MAX_RECORDS)
        self.assertEqual(scope['records_available'], policy.MAX_RECORDS+1)
        self.assertEqual(scope['candidate_count'], policy.MAX_RECORDS)
        self.assertIn('record_limit', scope['reason_codes'])
        self.assertNotIn('output_invalid', scope['reason_codes'])
        self.assertLess((self.directory/'F0001.stdout').stat().st_size, scan.OUTPUT_LIMIT)

    def test_windows_zero_rows_still_partial_and_engine_failure_separate(self):
        request = self.request(b'ElfFile\0'+b'\0'*30, profile='windows_evtx')
        def engine(argv, stem, deadline):
            Path(str(stem)+'.jsonl').write_text('')
            Path(str(stem)+'.stderr').write_text('')
            Path(str(stem)+'.stdout').write_text('')
            return 0, None
        with patch.object(scan, 'engine', return_value=({}, None)), patch.object(scan, 'run', side_effect=engine):
            body = scan.execute(request, self.directory)
        self.assertEqual(body['inputs'][0]['state'], 'partial')
        self.assertEqual(body['inputs'][0]['candidate_count'], 0)
        with patch.object(scan, 'engine', return_value=({}, 'rules_unverified')):
            body = scan.execute(request, self.directory)
        self.assertEqual(body['inputs'][0]['state'], 'skipped')
        self.assertFalse(body['inputs'][0]['counts_complete'])

    def test_windows_metadata_comes_from_verified_rule_not_event_row(self):
        ident = '12345678-1234-1234-1234-123456789abc'
        path = self.directory/'rule.yml'
        path.write_text(f'title: Trusted execution rule\nid: {ident}\nlevel: high\n')
        metadata = scan.rule_metadata(path)
        self.assertEqual(metadata['rule_title'], 'Trusted execution rule')
        request = self.request(b'ElfFile\0', profile='windows_evtx')
        def engine(argv, stem, deadline):
            Path(str(stem)+'.jsonl').write_text(json.dumps({'RuleID': ident, 'RecordID': '47',
                'RuleTitle': 'PRIVATE EVENT SECRET', 'Level': 'low', 'Computer': 'private-host'})+'\n')
            return 0, None
        with patch.object(scan, 'engine', return_value=({'_rule_catalog': {ident: metadata}}, None)), \
                patch.object(scan, 'run', side_effect=engine):
            body = scan.execute(request, self.directory)
        self.assertEqual(body['candidates'][0]['severity'], 'high')
        self.assertEqual(body['candidates'][0]['event_record_id'], 47)
        self.assertNotIn('PRIVATE EVENT SECRET', json.dumps(body))
        self.assertNotIn('private-host', json.dumps(body))

    def test_windows_changed_output_schema_is_failed_not_zero_detection(self):
        request = self.request(b'ElfFile\0', profile='windows_evtx')
        def engine(argv, stem, deadline):
            Path(str(stem)+'.jsonl').write_text('{"UnexpectedField":"private"}\n')
            return 0, None
        with patch.object(scan, 'engine', return_value=({}, None)), patch.object(scan, 'run', side_effect=engine):
            body = scan.execute(request, self.directory)
        self.assertEqual(body['inputs'][0]['state'], 'failed')
        self.assertEqual(body['inputs'][0]['reason_codes'], ['output_invalid'])
        self.assertFalse(body['inputs'][0]['counts_complete'])

    def test_files_are_processed_sequentially_and_global_candidate_cap(self):
        request = self.request(b'\0'*384)
        request['inputs'] *= 3
        order = []
        def parse(item, request, directory, deadline):
            order.append('enter'); self.assertEqual(order.count('enter')-order.count('exit'), 1)
            order.append('exit')
            return {'input_id': item['input_id'], 'state': 'complete'}, [1, 2]
        with patch.object(scan, 'engine', return_value=({}, None)), patch.object(scan, 'linux', side_effect=parse):
            body = scan.execute(request, self.directory)
        self.assertEqual(order, ['enter', 'exit']*3)
        self.assertEqual(len(body['candidates']), 2)


if __name__ == '__main__':
    unittest.main()
