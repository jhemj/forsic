"""Offline source coordinate recovery and pruning; synthetic retained data only."""
from copy import deepcopy
from functools import partial
import json
import tempfile
import unittest

from agent.context_compressor import ContextCompressor
from tools.registry import registry
from forsic_plugin.context_results import summarize
from forsic_plugin.report_driven.assessment_basis import _source_choices
from forsic_plugin.report_driven.host import current, source_record
from forsic_plugin.report_driven.model_pages import (
    MAX_BYTES, RetainedSourceError, SourcePointerError, SourceVersionMismatch, size, source_view)
from test_report_driven import fixture


class SourceRecoveryContractTests(unittest.TestCase):
    def source(self, data):
        return source_record({'id': 'E-synthetic-recovery', 'data': data})

    def test_omitted_pointer_discovers_but_empty_pointer_requires_version(self):
        data = {'tool': 'forsic_read', 'complete': True, 'text': 'observed'}
        source = self.source(data)
        discovery = source_view(source, data, {})
        self.assertEqual({item['key']: item['value'] for item in discovery['items']}, data)
        for item in discovery['items']:
            field = source_view(source, data, item['read_call'])
            self.assertEqual(field.get('text', field.get('value')), data[item['key']])
        self.assertEqual(source_view(source, data, {'offset': 0}), discovery)
        self.assertIn('retained tool-result root', discovery['pointer_basis'])
        self.assertIn('Explicit pointer=""', discovery['source_input_contract'])
        for args in ({'pointer': ''}, {'pointer': '', 'snapshot_id': source['version']},
                     {'source_version': 'old'}, {'pointer': '/text', 'source_version': 'old'}):
            with self.subTest(args=args), self.assertRaises(SourceVersionMismatch) as caught:
                source_view(source, data, args)
            self.assertEqual(caught.exception.details['next_call'],
                             {'action': 'source', 'source_id': source['id']})
        root = source_view(source, data, discovery['read_call'])
        complete = next(item for item in root['items'] if item['pointer'] == '/complete')
        self.assertTrue(source_view(source, data, complete['read_call'])['value'])
        self.assertLessEqual(size(root), MAX_BYTES)

    def test_absent_display_prefix_returns_verified_parent_never_substitutes(self):
        data = {'tool': 'forsic_read', 'complete': True, 'text': 'actual root'}
        source = self.source(data)
        args = {'source_id': source['id'], 'source_version': source['version'], 'pointer': '/result/complete'}
        with self.assertRaises(SourcePointerError) as caught:
            source_view(source, data, args)
        error = caught.exception
        self.assertIsInstance(error, RetainedSourceError)
        self.assertEqual(error.details['failed_source'], args)
        self.assertEqual(error.details['next_call']['pointer'], '')
        self.assertNotIn('value', error.details)
        parent = source_view(source, data, error.details['next_call'])
        self.assertEqual(parent['pointer'], '')
        self.assertEqual({call['pointer'] for call in error.details['recovery']['choices']},
                         {'/tool', '/complete', '/text'})
        actual = next(item for item in parent['items'] if item['pointer'] == '/complete')
        self.assertTrue(source_view(source, data, actual['read_call'])['value'])
        self.assertEqual(data['text'], 'actual root')

    def test_real_result_and_value_keys_remain_real_coordinates(self):
        data = {'tool': 'forsic_read', 'complete': True,
                'result': {'complete': False}, 'value': {'complete': 'literal value key'}}
        source = self.source(data)
        for path, expected in (('/complete', True), ('/result/complete', False),
                               ('/value/complete', 'literal value key')):
            page = source_view(source, data, {'source_version': source['version'], 'pointer': path})
            self.assertEqual(page.get('value', page.get('text')), expected)
        for path in ('/result/text', '/value/text'):
            with self.assertRaises(SourcePointerError) as caught:
                source_view(source, data, {'source_version': source['version'], 'pointer': path})
            self.assertEqual(caught.exception.details['next_call']['pointer'], path.rsplit('/', 1)[0])

    def test_scalar_read_call_uses_default_page_size_and_utf8_boundaries(self):
        text = '한글🙂' * 400
        data = {'tool': 'forsic_read', 'text': text}
        source = self.source(data)
        discovery = source_view(source, data, {})
        root = source_view(source, data, discovery['read_call'])
        call = next(item['read_call'] for item in root['items'] if item['pointer'] == '/text')
        self.assertNotIn('limit', call)
        scalar = source_view(source, data, call)
        self.assertGreater(scalar['byte_end'], 5)
        self.assertLessEqual(scalar['byte_end'], 2048)
        self.assertGreater(scalar['byte_end'], 2044)
        self.assertEqual(scalar['text'].encode(), text.encode()[:scalar['byte_end']])
        self.assertEqual(scalar['next_offset'], scalar['byte_end'])
        self.assertLessEqual(size(scalar), MAX_BYTES)

    def test_missing_nested_and_escaped_keys_offer_only_existing_children(self):
        data = {'tool': 'forsic_read', 'a/b~c': {'records': [{'text': 'retained'}]}}
        source = self.source(data)
        args = {'source_version': source['version'], 'pointer': '/a~1b~0c/records/9/text'}
        with self.assertRaises(SourcePointerError) as caught:
            source_view(source, data, args)
        details = caught.exception.details
        self.assertEqual(details['next_call']['pointer'], '/a~1b~0c/records')
        self.assertEqual([call['pointer'] for call in details['recovery']['choices']],
                         ['/a~1b~0c/records/0'])
        self.assertTrue(details['recovery']['choices_complete'])
        self.assertLessEqual(size(details), MAX_BYTES)
        with self.assertRaises(ValueError) as invalid:
            source_view(source, data, {'source_version': source['version'], 'pointer': '/bad~2escape'})
        self.assertNotIsInstance(invalid.exception, SourcePointerError)

    def test_large_discovery_and_recovery_choices_are_bounded(self):
        data = {'tool': 'forsic_read', **{str(i): 'x' * 3500 for i in range(100)}}
        source = self.source(data)
        original = deepcopy(data)
        discovery = source_view(source, data, {})
        self.assertLessEqual(size(discovery), MAX_BYTES)
        for item in discovery['items']:
            self.assertEqual(item['read_call']['pointer'], item['pointer'])
            self.assertEqual(item['read_call']['source_version'], source['version'])
            self.assertLessEqual(size(source_view(source, data, item['read_call'])), MAX_BYTES)
        with self.assertRaises(SourcePointerError) as caught:
            source_view(source, data, {'source_version': source['version'], 'pointer': '/absent'})
        self.assertLessEqual(len(caught.exception.details['recovery']['choices']), 5)
        self.assertFalse(caught.exception.details['recovery']['choices_complete'])
        self.assertEqual(data, original)

    def test_native_errors_recover_in_same_case_without_changing_judgment(self):
        with tempfile.TemporaryDirectory() as tmp:
            case, _, _ = fixture(tmp)
            source = current(case)['sources'][0]
            retained = deepcopy(case.event(source['id']))
            before = current(case)
            for args, code in (({'source_id': source['id'], 'source_version': 'stale',
                                 'pointer': '/lines/0/text'}, 'source_version_mismatch'),
                               ({'source_id': source['id'], 'source_version': source['version'],
                                 'pointer': '/result/lines/0/text'}, 'source_pointer_absent'),
                               ({'source_id': 'missing-source'}, 'unknown_retained_source')):
                with self.subTest(code=code):
                    failed = json.loads(case.invoke('forsic_reporting', {'action': 'source', **args}))
                    self.assertEqual(failed['error_code'], code)
                    self.assertEqual(failed['failed_source']['source_id'], args['source_id'])
                    self.assertNotIn('result', failed)
                    recovered = json.loads(case.invoke('forsic_reporting', failed['next_call']))
                    self.assertNotIn('error', recovered)
                    if code == 'unknown_retained_source':
                        self.assertIn('sources', recovered['sections'])
                    else:
                        self.assertEqual(recovered['source_ref']['id'], source['id'])
            self.assertEqual(case.event(source['id']), retained)
            after = current(case)
            for field in ('questions', 'assessments', 'observations', 'claims'):
                self.assertEqual(after[field], before[field])

    def test_historical_and_current_source_receipts_replay_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            case, _, _ = fixture(tmp)
            sid = current(case)['sources'][0]['id']
            source = source_record(case.event(sid))
            args = {'action': 'source', 'source_id': sid, 'source_version': source['version'],
                    'pointer': '/lines/0/text'}
            for acquisition in (False, True):
                for continuation in (False, True):
                    for coordinates in (False, True):
                        with self.subTest(acquisition=acquisition, continuation=continuation, coordinates=coordinates):
                            shown = source_view(source, case.event(sid)['data'], args,
                                include_acquisition_context=acquisition, include_continuation=continuation,
                                include_coordinate_calls=coordinates)
                            started = case.record('tool_start', {'tool': 'forsic_reporting', 'arguments': args}, 'synthetic')
                            record = {'tool': 'forsic_reporting', 'started_id': started, **shown}
                            eid = case.record('tool_result', record, 'synthetic')
                            expected, choices = _source_choices(case, current(case), eid)
                            self.assertEqual(expected, shown)
                            self.assertEqual(choices[0]['literal'], shown['text'])
                            self.assertEqual(choices[0]['source_version'], source['version'])
                            altered = case.record('tool_result', {**record, 'source_coordinate_contract': 'unknown'}, 'synthetic')
                            with self.assertRaises(ValueError):
                                _source_choices(case, current(case), altered)
                            altered = case.record('tool_result', {**record, 'text': 'invented'}, 'synthetic')
                            with self.assertRaises(ValueError):
                                _source_choices(case, current(case), altered)


class SourceRecoveryPruningTests(unittest.TestCase):
    def prune(self, args, body):
        name = 'synthetic_source_recovery'
        registry.register(name, 'synthetic', {'parameters': {}}, lambda **kwargs: '',
                          result_summary=partial(summarize, 'forsic_reporting'))
        self.addCleanup(registry.deregister, name)
        messages = [{'role': 'tool', 'tool_call_id': 'source-read',
                     'content': json.dumps(body), 'message_uid': 'preserved'}]
        original = deepcopy(messages)
        self.assertTrue(ContextCompressor._demote_tool_result_at(messages, 0,
            {'source-read': (name, json.dumps(args))}, 100))
        result = json.loads(messages[0]['content'].split('\n', 1)[1])
        self.assertEqual(messages[0]['message_uid'], original[0]['message_uid'])
        self.assertLessEqual(len(messages[0]['content']), 2048)
        self.assertFalse(ContextCompressor._demote_tool_result_at(messages, 0,
            {'source-read': (name, json.dumps(args))}, 100))
        return result

    def test_failed_source_and_exact_recovery_survive_native_pruning(self):
        data = {'tool': 'forsic_read', 'complete': True}
        source = source_record({'id': 'E-recovery', 'data': data})
        for args in ({'source_id': source['id'], 'source_version': 'stale', 'pointer': '/complete'},
                     {'source_id': source['id'], 'source_version': source['version'], 'pointer': '/result/complete'}):
            with self.subTest(args=args):
                with self.assertRaises(RetainedSourceError) as caught:
                    source_view(source, data, args)
                body = {'error': str(caught.exception) + ' diagnostic' * 3000,
                        'error_type': type(caught.exception).__name__, **caught.exception.details,
                        'evidence_id': 'E-error-receipt', 'file_path': 'x' * 240,
                        'search_path': 'y' * 240, 'padding': 'irrelevant' * 1000}
                pruned = self.prune(args, body)
                self.assertTrue(pruned['error_present'])
                self.assertEqual(pruned['failed_source'], args)
                self.assertEqual(pruned['next_call'], caught.exception.details['next_call'])
                self.assertNotIn('returned_records', pruned)
                recovery = source_view(source, data, pruned['next_call'])
                self.assertEqual(recovery['source_ref']['version'], source['version'])
                self.assertEqual(pruned['failed_source']['source_version'], args['source_version'])

    def test_unknown_source_preserves_discovery_without_guessing_an_id(self):
        failed = {'source_id': 'missing-source'}
        body = {'error': 'Unknown source', 'error_code': 'unknown_retained_source',
                'failed_source': failed, 'next_call': {'action': 'state'}, 'padding': 'x' * 9000}
        pruned = self.prune(failed, body)
        self.assertEqual(pruned['failed_source'], failed)
        self.assertEqual(pruned['next_call'], {'action': 'state'})

    def test_oversize_coordinate_is_omitted_whole_with_an_explicit_flag(self):
        body = {'error': 'Source page rejected', 'error_code': 'source_version_mismatch',
                'failed_source': {'source_id': 'E-kept', 'source_version': 'old', 'pointer': '/' + 'p' * 5000},
                'next_call': {'action': 'source', 'source_id': 'E-kept'}}
        view = json.loads(summarize('forsic_reporting', {}, json.dumps(body)))
        self.assertEqual(view['failed_source'], {'source_id': 'E-kept', 'source_version': 'old'})
        self.assertEqual(view['failed_source_omitted_fields'], ['pointer'])
        self.assertEqual(view['next_call'], body['next_call'])

    def test_long_failed_pointer_is_kept_when_complete_recovery_fits_budget(self):
        failed = {'source_id': 'E-kept', 'source_version': 'old', 'pointer': '/' + 'p' * 1000}
        body = {'error': 'diagnostic ' * 3000, 'error_code': 'source_version_mismatch',
                'failed_source': failed, 'next_call': {'action': 'source', 'source_id': 'E-kept'}}
        encoded = summarize('forsic_reporting', {}, json.dumps(body))
        self.assertIsNotNone(encoded)
        self.assertLessEqual(len(encoded), 2000)
        view = json.loads(encoded)
        self.assertEqual(view['failed_source'], failed)
        self.assertEqual(view['next_call'], body['next_call'])

    def test_review_validation_repair_is_not_lost_during_pruning(self):
        recovery = {'saved': False, 'question_id': 'Q-synthetic', 'current_revision': 4,
                    'required_fields': ['limitation']}
        body = {'error': 'limitation is required', 'error_code': 'invalid_review_response',
                'invalid_fields': [{'field': 'limitation', 'problem': 'required'}],
                'recovery': recovery, 'padding': 'x' * 9000}
        pruned = self.prune({'outcome': 'scope_limited'}, body)
        self.assertEqual(pruned['recovery'], recovery)
        self.assertEqual(pruned['invalid_fields'], body['invalid_fields'])
        self.assertNotIn('next_call', pruned)

    def test_operation_conflict_retains_recovery_and_returned_write_status(self):
        from forsic_plugin.report_driven.investigation_state import MutationConflictError
        error = MutationConflictError()
        body = {'error': str(error) + ' diagnostic' * 3000, **error.details,
                'padding': 'x' * 9000, 'file_path': 'p' * 240}
        pruned = self.prune({'operation_id': 'synthetic', 'write_applied': True}, body)
        self.assertFalse(pruned['write_applied'])
        self.assertEqual(pruned['recovery'], error.details['recovery'])
        self.assertEqual(pruned['error_code'], 'mutation_payload_conflict')
        self.assertNotIn('returned_records', pruned)

    def test_returned_command_outcome_survives_native_pruning_without_inference(self):
        from forsic_plugin.context_results import execution_summary
        for status, basis, code in (('no_matches', 'atomic_search_exit_status', 1),
                                    ('partial', 'derived_helper_report', 3)):
            with self.subTest(status=status):
                outcome = {'status': status, 'basis': basis, 'individual_steps': 'not_observed',
                           'meaning': 'unneeded explanation' * 100}
                body = {'evidence_id': 'E-shell-outcome', 'execution': {'state': 'returned',
                        'termination_confirmed': True, 'exit_code': code}, 'command_outcome': outcome,
                        'error': 'nonzero return', 'padding': 'x' * 9000}
                pruned = self.prune({'command': 'invented command', 'command_outcome': {'status': 'returned'}}, body)
                self.assertEqual(pruned['command_outcome'], {key: outcome[key]
                    for key in ('status', 'basis', 'individual_steps')})
                self.assertEqual(pruned['execution']['exit_code'], code)
        raw = {'execution': {'state': 'returned', 'termination_confirmed': True, 'exit_code': 1}}
        self.assertNotIn('command_outcome', execution_summary(raw))
        self.assertEqual(execution_summary({'command_outcome': {'status': 's' * 121,
                         'basis': 'b' * 121, 'individual_steps': {'unbounded': 'data'}}})['command_outcome'], {})


if __name__ == '__main__':
    unittest.main()
