"""Synthetic native scan/source/question/local-report contracts; no model calls."""
from copy import deepcopy
import csv
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from forsic_plugin import sensitive_policy as policy
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.views import bundle, artifact
from forsic_plugin.report_driven.word_export import export_word
import test_shell_integration as shell_tests


class SensitiveIntegrationTests(unittest.TestCase):
    call = shell_tests.ShellIntegrationTests.call
    secret = 'synthetic-person@example.test'

    def setUp(self):
        # The Docker daemon shares the workspace, not this process's private /tmp.
        if self._testMethodName == 'test_real_container_through_registered_native_tool':
            root=Path(__file__).parent/'artifacts/sensitive-synthetic/native'
            root.mkdir(parents=True,exist_ok=True)
            with patch('tempfile.tempdir',str(root)):
                shell_tests.ShellIntegrationTests.setUp(self)
            management=patch.dict(os.environ,{'FORSIC_MANAGEMENT_ROOT':str(Path(self.temp.name)/'management')})
            management.start();self.addCleanup(management.stop)
        else:
            shell_tests.ShellIntegrationTests.setUp(self)

    def target(self, *, content=None, kind='evidence_file', encoding='auto'):
        path = self.case.root / 'selected.txt'
        path.write_bytes((content if content is not None else self.secret + '\n').encode('utf-8'))
        source = self.call('forsic_list', {})
        index = next(i for i, x in enumerate(source['files']) if x['path'] == path.name)
        version = host.source_record(self.case.event(source['evidence_id']))['version']
        return {'kind': kind, 'source_id': source['evidence_id'], 'source_version': version,
                'pointer': f'/files/{index}/path', 'encoding': encoding}

    def args(self, **changes):
        return {'action': 'sensitive_scan', 'targets': [self.target()], 'profiles': ['pii_kr'],
                'mode': 'scope_count', **changes}

    def backend(self, command, container, native, **kwargs):
        mounts = {}
        for i, part in enumerate(command):
            if part == '--mount':
                values = dict(x.split('=', 1) for x in next(csv.reader([command[i+1]])) if '=' in x)
                mounts[values['dst']] = Path(values['src'])
        self.assertEqual(set(mounts), {'/private', '/scan-input', '/opt/forsic/sensitive_scan.py',
                                     '/private/request.json','/private/hmac.key'})
        self.assertIn('--network', command)
        self.assertEqual(command[command.index('--network')+1], 'none')
        self.assertNotIn(str(self.case.root), command)
        private = mounts['/private']
        request = json.loads(mounts['/private/request.json'].read_text())
        original = (mounts['/scan-input'] / 'F0001').read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), request['inputs'][0]['sha256'])
        safe = {
            'schema': policy.SCHEMA, 'scan_id': request['scan_id'], 'engines': [
                {'name': 'presidio_local', 'version': '2.2.364', 'state': 'available',
                 'recognizers': ['EMAIL_ADDRESS']}],
            'coverage': {'state': 'complete', 'selected_files': 1, 'fully_processed_files': 1,
                'partial_files': 0, 'failed_files': 0, 'excluded_files': 0,
                'inputs': [{'input_id': 'F0001', 'state': 'complete', 'bytes_total': len(original),
                    'bytes_read': len(original), 'ranges': [{'start': 0, 'end': len(original)}],
                    'profile_states': {'pii_kr': 'complete'}, 'reason_codes': []}]},
            'summary': {'pii_occurrences_observed': 1, 'secret_occurrences_observed': 0,
                'organization_identifier_occurrences_observed': 0, 'distinct_values_observed': 1,
                'counts_are_lower_bounds': False, 'distinct_people': None},
            'findings': [{'finding_id': 'D00001', 'input_id': 'F0001',
                'entity_type': 'EMAIL_ADDRESS', 'masked_excerpt': self.secret,
                'source_locator': {'byte_start': 0, 'byte_end': len(original)-1,
                    'line_start': 1, 'line_end': 1, 'column_start': 1, 'column_end': len(original)},
                'observations': [{'engine': 'presidio_local', 'format_validation': None,
                                  'raw_value': self.secret}]}],
            'findings_omitted': 0, 'raw': self.secret, 'stderr': self.secret}
        protected = {'schema': policy.SCHEMA, 'scan_id': request['scan_id'], 'findings': [{
            'finding_id': 'D00001', 'input_id': 'F0001', 'encoding': 'utf-8',
            'excerpt': original.decode('utf-8'), 'excerpt_byte_start': 0, 'excerpt_byte_end': len(original),
            'match_byte_start': 0, 'match_byte_end': len(original)-1,
            'value_hmac': 'a'*64}]}
        policy._write(private/'safe.json', policy._json(safe))
        policy._write(private/'result.json', policy._json(protected))
        return {'stdout': self.secret.encode(), 'stderr': self.secret.encode(),
            'execution': {'state': 'returned', 'exit_code': 0, 'termination_confirmed': True,
                          'cleanup': {'confirmed': True, 'stderr': self.secret}}}

    def scan(self, args=None, backend=None):
        with patch('forsic_plugin.shell_workspace._image_id', return_value='sha256:'+'1'*64), \
                patch('forsic_plugin.shell_workspace.run_container', side_effect=backend or self.backend) as runner:
            result = self.call('forsic_shell', args or self.args())
        return result, runner

    def test_native_call_masked_source_exact_question_and_local_original_report(self):
        args = self.args()
        result, runner = self.scan(args)
        self.assertNotIn('error', result, result)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(result['summary']['distinct_people'], None)
        self.assertEqual(result['findings'][0]['masked_excerpt'], '<PII:D00001>')
        self.assertEqual(result['findings'][0]['group_id'], 'G000001')
        wire = json.dumps(result)
        self.assertNotIn(self.secret, wire)
        self.assertNotIn('raw_output_calls', wire)
        saved = self.case.event(result['evidence_id'])
        self.assertNotIn(self.secret, json.dumps(saved))
        lookup = self.call('forsic_reporting', {'action': 'source', **{
            'source_id': result['source_ref']['id'], 'source_version': result['source_ref']['version']},
            'pointer': '/findings/0/masked_excerpt'})
        self.assertNotIn(self.secret, json.dumps(lookup))
        self.assertEqual(lookup['text'], '<PII:D00001>')
        self.assertEqual(lookup['acquisition_context']['returned_scope']['representation'], 'derived_masked_detection_result')
        mutation = self.call('forsic_question', {'operation_id': 'synthetic-sensitive-question',
            'question': 'Does the selected file contain a personal-data candidate?', 'scope': 'Selected synthetic file',
            'answer': 'One detector candidate; real identity and disclosure remain unassessed.',
            'reason': 'Retain the detector candidate, not a leakage conclusion', 'priority': 'contextual',
            'judgment': {'citation_refs': [lookup['citation_ref']], 'assertion_kind': 'fact',
                         'reasoning_summary': 'Only the recorded masked detector candidate is asserted.'}})
        self.assertNotIn('error', mutation, mutation)
        state = host.current(self.case)
        self.assertNotIn(self.secret, json.dumps(state))
        output = bundle(self.case, state, question_ids=[mutation['question']['question_id']])
        self.assertTrue(output['local_only'])
        self.assertIn(self.secret, artifact(self.case, output['bundle_id'], 'integrated.html').read_text())
        original_manifest=Path(output['manifest']).read_bytes()
        self.assertFalse(any(Path(path).suffix=='.docx' for path in output['files']))
        word=export_word(self.case,output['bundle_id'])
        self.assertTrue(word['local_only'])
        self.assertEqual(word['snapshot_id'],json.loads(original_manifest)['snapshot_id'])
        self.assertEqual(Path(output['manifest']).read_bytes(),original_manifest)
        with zipfile.ZipFile(word['path']) as archive:
            self.assertIn(self.secret, archive.read('word/document.xml').decode())
        with zipfile.ZipFile(artifact(self.case, output['bundle_id'], 'selected-evidence.zip')) as archive:
            self.assertFalse(any(self.secret.encode() in archive.read(n) for n in archive.namelist()))
        self.assertNotIn(self.secret, json.dumps(output))

    def test_same_material_reuses_completed_scan_even_reason_changes(self):
        args = self.args()
        first, _ = self.scan(args)
        second, runner = self.scan({**args, 'reason': 'New wording does not justify repeated collection'})
        self.assertEqual(runner.call_count, 0)
        self.assertTrue(second['reused'])
        self.assertEqual(first['scan_id'], second['scan_id'])

    def test_changed_input_cannot_reuse_supplied_resume_ref(self):
        args = self.args()
        first, _ = self.scan(args)
        base = policy._base(self.case)
        original_manifest = (base/first['scan_id']/'manifest.json').read_bytes()
        (self.case.root/'selected.txt').write_text('changed input')
        result, runner = self.scan({**args, 'resume_ref': first['resume_ref']})
        self.assertIn('error', result)
        self.assertEqual(runner.call_count, 0)
        self.assertEqual((base/first['scan_id']/'manifest.json').read_bytes(),original_manifest)
        self.assertEqual(list(base.glob('*/manifest.json')),[base/first['scan_id']/'manifest.json'])

    def test_private_storage_cannot_overlap_evidence_in_either_direction(self):
        output = Path(self.temp.name)/'new-output'
        for evidence in (output, output/policy.PRIVATE_NAME,
                         output/policy.PRIVATE_NAME/'selected-subdirectory',output.parent):
            fake = SimpleNamespace(root=evidence,output=output,
                                   config={'output_root':str(output)})
            with self.subTest(evidence=evidence),self.assertRaises(ValueError):
                policy._base(fake)
            self.assertFalse((output/policy.PRIVATE_NAME).exists())

    def test_exact_resume_reuses_and_changed_key_fails_closed(self):
        args = self.args()
        first, _ = self.scan(args)
        replay, runner = self.scan({**args, 'resume_ref': first['resume_ref']})
        self.assertTrue(replay['reused'])
        self.assertEqual(runner.call_count, 0)
        policy._write(policy._base(self.case)/'hmac.key', b'z'*32, replace=True)
        failed, runner = self.scan(args)
        self.assertEqual(failed['error_code'], 'sensitive_comparison_key_changed')
        self.assertEqual(runner.call_count, 0)

    def test_source_version_foreign_handle_and_arbitrary_path_rejected(self):
        args = self.args()
        variants = []
        stale = deepcopy(args); stale['targets'][0]['source_version'] = '0'*64; variants.append(stale)
        path = deepcopy(args); path['targets'][0]['path'] = '/etc/passwd'; variants.append(path)
        unknown = deepcopy(args); unknown['targets'][0]['source_id'] = '0'*32; variants.append(unknown)
        command = {**args, 'command': 'echo forbidden'}; variants.append(command)
        for value in variants:
            with self.subTest(value=value):
                result, runner = self.scan(value)
                self.assertIn('error', result)
                self.assertEqual(runner.call_count, 0)

    def test_source_symlink_and_duplicate_selections_rejected(self):
        args = self.args()
        duplicate = {**args, 'targets': args['targets']*2}
        result, runner = self.scan(duplicate)
        self.assertIn('error', result); self.assertEqual(runner.call_count, 0)
        path = self.case.root/'selected.txt'
        original = path.read_bytes(); path.unlink()
        outside = self.case.output/'outside'; outside.write_bytes(original)
        path.symlink_to(outside)
        result, runner = self.scan(args)
        self.assertIn('error', result); self.assertEqual(runner.call_count, 0)

    def test_empty_unknown_and_paused_goal_do_not_execute(self):
        args = self.args()
        self.binding['goal_status'] = 'paused'
        result, runner = self.scan(args)
        self.assertEqual(result['error_code'], 'shell_goal_inactive')
        self.assertEqual(runner.call_count, 0)

    def test_timeout_raw_diagnostics_and_unknown_cleanup_never_leak(self):
        from forsic_plugin.execution_contract import ExecutionFailure
        def fail(*args, **kwargs):
            raise ExecutionFailure(self.secret, stderr=self.secret, raw_output={'stdout_base64': self.secret},
                execution={'state': 'completion_unknown', 'termination_confirmed': False,
                           'cleanup': {'confirmed': False, 'stderr': self.secret}})
        args = self.args()
        first, _ = self.scan(args, fail)
        self.assertNotIn(self.secret, json.dumps(first))
        self.assertEqual(first['execution']['state'], 'completion_unknown')
        second, runner = self.scan(args)
        self.assertEqual(second['error_code'], 'execution_completion_unknown')
        self.assertEqual(runner.call_count, 0)

    def test_corrupt_result_after_execution_not_reported_as_unstarted(self):
        def corrupt(*args, **kwargs):
            value = self.backend(*args, **kwargs)
            base = policy._base(self.case)
            job = next(p for p in base.iterdir() if p.is_dir())
            policy._write(job/'output'/'safe.json', b'bad private result', replace=True)
            return value
        result, _ = self.scan(backend=corrupt)
        self.assertIn('error', result)
        self.assertEqual(result['execution']['state'], 'returned')
        self.assertTrue(result['execution']['termination_confirmed'])
        self.assertNotIn(self.secret, json.dumps(result))

    def test_private_write_failure_keeps_unconfirmed_execution_state(self):
        from forsic_plugin.execution_contract import ExecutionFailure
        def execute(*args, **kwargs):
            raise ExecutionFailure(self.secret,execution={
                'state':'completion_unknown','termination_confirmed':False})
        write = policy._write
        def disk_full(path, *args, **kwargs):
            if path.name == 'failure.json':
                raise OSError('synthetic disk full')
            return write(path,*args,**kwargs)
        args = self.args()
        with patch.object(policy,'_write',side_effect=disk_full):
            result,_ = self.scan(args,execute)
        self.assertEqual(result['execution']['state'],'completion_unknown')
        self.assertFalse(result['execution']['termination_confirmed'])
        self.assertNotIn(self.secret,json.dumps(result))
        repeated,runner = self.scan(args)
        self.assertEqual(repeated['error_code'],'execution_completion_unknown')
        self.assertEqual(runner.call_count,0)

    def test_group_write_failure_keeps_confirmed_execution_state(self):
        with patch.object(policy,'_groups',side_effect=OSError('synthetic disk full')):
            result,runner = self.scan()
        self.assertEqual(runner.call_count,1)
        self.assertEqual(result['execution']['state'],'returned')
        self.assertTrue(result['execution']['termination_confirmed'])
        self.assertNotIn(self.secret,json.dumps(result))

    def test_presentation_failure_still_returns_only_masked_receipt(self):
        args = self.args()
        with patch('forsic_plugin.model_results.evidence_view', side_effect=ValueError('synthetic display error')):
            result, runner = self.scan(args)
        self.assertEqual(runner.call_count, 1)
        self.assertNotIn(self.secret, json.dumps(result))
        self.assertIn('presentation_warning', result)

    def test_restricted_permissions_and_appendix_integrity_omissions(self):
        result, _ = self.scan()
        base = policy._base(self.case)
        for path in base.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        appendix = policy.report_appendix(self.case, [result['evidence_id']], max_findings=0)
        self.assertEqual(appendix['omitted_findings'], 1)
        self.assertEqual(appendix['omitted_bytes'], len((self.secret+'\n').encode()))
        self.assertFalse(appendix['contains_sensitive_originals'])
        job = base/result['scan_id']
        policy._write(job/'inputs'/'F0001', b'changed', replace=True)
        appendix = policy.report_appendix(self.case, [result['evidence_id']])
        self.assertFalse(appendix['entries']); self.assertTrue(appendix['limitations'])
        self.assertEqual(appendix['unavailable_findings'],1)

    def test_partial_scan_does_not_claim_zero_unretained_candidates(self):
        def partial(*args,**kwargs):
            execution = self.backend(*args,**kwargs)
            path = next(policy._base(self.case).glob('*/output/safe.json'))
            safe = json.loads(path.read_text())
            safe['coverage']['state'] = 'partial'
            safe['summary']['counts_are_lower_bounds'] = True
            policy._write(path,policy._json(safe),replace=True)
            return execution
        result,_ = self.scan(backend=partial)
        appendix = policy.report_appendix(self.case,[result['evidence_id']])
        self.assertTrue(appendix['counts_are_lower_bounds'])
        self.assertIsNone(appendix['unretained_findings'])

    def test_many_inputs_are_paged_without_losing_totals_or_context_signals(self):
        result,_ = self.scan()
        full = deepcopy(self.case.event(result['evidence_id'])['data'])
        full['source_ref'] = result['source_ref']
        full['coverage']['inputs'] *= 64
        full['coverage']['selected_files'] = 64
        full['input_refs'] *= 64
        full['findings'] *= 100
        full['findings'][0]['context_signals'] = ['korean_email_label_nearby']
        displayed = policy.model_view(full)
        self.assertLessEqual(len(policy._json(displayed)),8000)
        self.assertEqual(displayed['coverage']['selected_files'],64)
        self.assertEqual(displayed['coverage']['display_omitted_inputs'],59)
        self.assertEqual(displayed['display_omitted_input_refs'],59)
        self.assertEqual(displayed['display_omitted_findings']+len(displayed['findings']),100)
        self.assertEqual(displayed['findings'][0]['context_signals'],['korean_email_label_nearby'])
        self.assertEqual(displayed['coverage_call']['arguments']['pointer'],'/coverage/inputs')

    @unittest.skipUnless(os.environ.get('FORSIC_SENSITIVE_IMAGE'), 'Explicit isolated candidate image required')
    def test_real_container_through_registered_native_tool(self):
        import subprocess
        image = subprocess.check_output(['docker','image','inspect',os.environ['FORSIC_SENSITIVE_IMAGE'],
                                         '--format','{{.Id}}'],text=True).strip()
        args = self.args()
        with patch('forsic_plugin.shell_workspace._image_id',return_value=image):
            result = self.call('forsic_shell',args)
        self.assertNotIn('error',result,result)
        self.assertEqual(result['execution']['exit_code'],0)
        self.assertTrue(result['execution']['termination_confirmed'])
        self.assertEqual(result['coverage']['fully_processed_files'],1)
        self.assertEqual(result['summary']['pii_occurrences_observed'],1)
        self.assertNotIn(self.secret,json.dumps(result))
        appendix=policy.report_appendix(self.case,[result['evidence_id']])
        self.assertEqual(appendix['returned_findings'],1,appendix)
        self.assertIn(self.secret,appendix['entries'][0]['excerpt'])


if __name__ == '__main__':
    unittest.main()
