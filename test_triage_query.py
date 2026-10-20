"""Retained safe candidate selection; synthetic bytes, mocked container, no models."""
import csv
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from forsic_plugin import shell_workspace as shell, triage_policy as policy, triage_scan as scan
import test_triage_profiles as fixtures


class RetainedQueryTests(unittest.TestCase):
    setUp = fixtures.TriageBoundaryTests.setUp
    call = fixtures.TriageBoundaryTests.call
    target = fixtures.TriageBoundaryTests.target
    args = fixtures.TriageBoundaryTests.args
    invoke = fixtures.TriageBoundaryTests.invoke
    secret = fixtures.TriageBoundaryTests.secret

    def backend(self, command, container, native, **kwargs):
        result = fixtures.TriageBoundaryTests.backend(self, command, container, native, **kwargs)
        mounts = {}
        for i, part in enumerate(command):
            if part == '--mount':
                values = dict(x.split('=', 1) for x in next(csv.reader([command[i+1]])) if '=' in x)
                mounts[values['dst']] = Path(values['src'])
        request = json.loads(mounts['/private/request.json'].read_bytes())
        path = mounts['/private']/'result.json'
        body = json.loads(path.read_bytes())
        rows = []
        for scope in body['inputs']:
            scope.update(state='partial', candidate_count=60, records_parsed=60,
                records_available=61, counts_complete=False, reason_codes=['record_limit'])
            rows.extend({'input_id': scope['input_id'], 'ordinal': i+1, 'record_index': i,
                'record_type': 7 if i % 2 else 8, 'byte_start': i*384, 'byte_end': (i+1)*384,
                'ut_user': self.secret, 'host': self.secret,
                **({'rule_id':'12345678-abcd-abcd-abcd-123456789abc', 'rule_title':self.secret,
                    'rule_sha256':'not-a-hash'} if i % 3 == 0 else {})} for i in range(60))
        body['candidates'] = rows[:request['candidate_limit']]
        body['candidate_index'] = rows
        path.write_bytes(policy.storage._json(body))
        return result

    def prepare(self, **changes):
        targets = changes.pop('targets', None) or [self.target(payload=b'\0'*(61*384))]
        value, runner = self.invoke(self.args(targets=targets, **changes))
        self.assertNotIn('error', value, value)
        self.assertEqual(runner.call_count, 1)
        self.job = self.case.output/policy.PRIVATE_NAME/value['scan_id']
        self.manifest = json.loads((self.job/'manifest.json').read_bytes())
        self.session = self.manifest['identity']['session_id']
        self.query_args = value['candidate_query']['next_call']['arguments']
        return value

    def query(self, **changes):
        with patch.object(shell, 'run_container', side_effect=AssertionError('parser rerun')), \
                patch.object(shell, '_image_id', side_effect=AssertionError('runtime probe')):
            return policy.query(self.case, {**self.query_args, **changes}, self.session)

    def test_pages_beyond_initial_twenty_preserve_partial_scope_and_exact_recheck(self):
        first = self.prepare()
        self.assertEqual(len(first['candidates']), 20)
        page = self.query(offset=20, limit=5)
        self.assertEqual([c['source_recheck']['record_index'] for c in page['candidates']], list(range(20,25)))
        self.assertEqual(page['coverage'], first['coverage'])
        self.assertFalse(page['summary']['counts_complete'])
        self.assertEqual(page['summary']['candidate_count'], 60)
        self.assertEqual(page['summary']['omitted_candidates'], 55)
        self.assertEqual(page['selection']['next_call']['arguments']['offset'], 25)
        self.assertEqual(page['candidates'][0]['candidate_id'], 'C0021')
        self.assertEqual(page['candidates'][0]['original_byte_call']['arguments'],
            {'path': 'selected.wtmp', 'offset': 20*384, 'length':384})
        exact = self.call(**{'tool': page['candidates'][0]['original_byte_call']['tool'],
                            'args': page['candidates'][0]['original_byte_call']['arguments']})
        self.assertNotIn('error', exact, exact)
        self.assertNotIn(self.secret, json.dumps(page))
        self.assertEqual(page['restricted_artifact']['model_access'], False)
        self.assertEqual(self.query(offset=60)['candidates'], [])
        self.assertEqual(self.query(offset=61)['error_code'], 'triage_query_rejected')

    def test_numeric_filter_stable_pagination_and_empty_not_absence(self):
        self.prepare()
        page = self.query(record_type=7, record_index_min=21, record_index_max=29, limit=2)
        self.assertEqual([c['source_recheck']['record_index'] for c in page['candidates']], [21,23])
        self.assertEqual(page['selection']['matched_indexed_candidates'], 5)
        continuation = page['selection']['next_call']['arguments']
        next_page = policy.query(self.case, continuation, self.session)
        self.assertEqual([c['source_recheck']['record_index'] for c in next_page['candidates']], [25,27])
        empty = self.query(event_record_id=123)
        self.assertEqual(empty['candidates'], [])
        self.assertEqual(empty['coverage']['state'], 'partial')
        self.assertTrue(empty['coverage_not_established'])
        self.assertEqual(empty['selection']['count_scope'], 'retained_safe_candidate_index_not_semantic_review')

    def test_registered_native_handler_queries_retained_page_without_execution_or_raw_rows(self):
        first = self.prepare()
        args = {**self.query_args, 'offset': 20, 'limit': 3}
        with patch.object(shell, 'run_container', side_effect=AssertionError('parser rerun')), \
                patch.object(shell, '_image_id', side_effect=AssertionError('runtime probe')):
            page = self.call('forsic_shell', args, turn_id='query-turn',
                             tool_call_id='query-call', api_request_id='query-request')
            rejected = self.call('forsic_shell', {**args, 'index_sha256': 'f'*64})
        self.assertNotIn('error', page, page)
        self.assertEqual(page['query_state'], 'returned')
        self.assertEqual([c['source_recheck']['record_index'] for c in page['candidates']], [20, 21, 22])
        self.assertEqual(page['coverage'], first['coverage'])
        self.assertFalse(page['summary']['counts_complete'])
        self.assertEqual(page['selection']['next_call']['arguments']['offset'], 23)
        self.assertEqual(page['execution_role'], 'retained_parser_execution_not_query_execution')
        start = self.case.event(page['started_id'])
        retained = self.case.event(page['evidence_id'])
        self.assertEqual(start['session'], self.session)
        self.assertEqual(retained['session'], self.session)
        self.assertEqual(start['data']['arguments'], args)
        self.assertEqual(start['data']['native_call']['tool_call_id'], 'query-call')
        self.assertEqual(retained['data']['selection'], page['selection'])
        self.assertNotIn(self.secret, json.dumps(page))
        self.assertNotIn(self.secret, json.dumps(retained))
        self.assertNotIn('presented_spans', page)
        self.assertEqual(rejected['error_code'], 'triage_query_rejected')

    def test_uuid_filter_and_index_byte_budget(self):
        with patch.object(policy, 'MAX_INDEX_BYTES', 6000):
            first = self.prepare()
        self.assertTrue(first['candidate_query']['index_truncated'])
        self.assertLess(first['candidate_query']['indexed_candidates'], 60)
        page = self.query(rule_id='12345678-ABCD-ABCD-ABCD-123456789ABC')
        self.assertTrue(page['candidates'])
        self.assertTrue(all(c['source_recheck']['record_index'] % 3 == 0 for c in page['candidates']))
        self.assertTrue(page['selection']['index_truncated'])
        self.assertNotIn(self.secret, json.dumps(page))
        self.assertTrue((self.job/'candidate-index.json').stat().st_size <= 6000)

    def test_multiple_input_filter_does_not_mix_sources(self):
        targets = [self.target('one.wtmp', b'\0'*(61*384)), self.target('two.wtmp', b'\0'*(61*384))]
        self.prepare(targets=targets)
        value = self.query(input_id='F0002', record_index_min=55)
        self.assertEqual(len(value['candidates']), 5)
        self.assertEqual({c['input_id'] for c in value['candidates']}, {'F0002'})
        self.assertEqual({c['original_byte_call']['arguments']['path'] for c in value['candidates']}, {'two.wtmp'})
        self.assertEqual(self.query(input_id='F9999')['error_code'], 'triage_query_rejected')

    def test_case_conversation_digest_version_and_private_artifact_boundaries(self):
        self.prepare()
        with patch.dict(self.case.config, {'case_id': 'another-case'}):
            self.assertEqual(self.query()['error_code'], 'triage_query_rejected')
        self.assertEqual(policy.query(self.case, self.query_args, 'another-conversation')['error_code'], 'triage_query_rejected')
        self.assertEqual(self.query(index_sha256='f'*64)['error_code'], 'triage_query_rejected')
        with patch('forsic_plugin.report_driven.host.source_record', return_value={'version':'f'*64}):
            self.assertEqual(self.query()['error_code'], 'triage_query_rejected')
        path = self.job/'candidate-index.json'
        raw = path.read_bytes()
        path.write_bytes(raw+b' ')
        self.assertEqual(self.query()['error_code'], 'triage_query_rejected')
        path.write_bytes(raw)
        path.chmod(0o644)
        self.assertEqual(self.query()['error_code'], 'triage_query_rejected')
        path.chmod(0o600)
        staged = self.job/'inputs/F0001'
        staged.write_bytes(b'changed')
        self.assertEqual(self.query()['error_code'], 'triage_query_rejected')

    def test_old_scan_and_unknown_completion_never_rerun(self):
        self.prepare()
        manifest = dict(self.manifest)
        manifest.pop('candidate_index_sha256')
        (self.job/'manifest.json').write_bytes(policy.storage._json(manifest))
        self.assertEqual(self.query()['error_code'], 'candidate_index_unavailable')
        manifest['state'] = 'completion_unknown'
        (self.job/'manifest.json').write_bytes(policy.storage._json(manifest))
        self.assertEqual(self.query()['error_code'], 'triage_query_completion_unknown')

    def test_safe_index_cap_and_unknown_counts_stay_visible(self):
        first = self.prepare()
        # An intentionally bounded index does not turn unindexed records into absence.
        path = self.job/'candidate-index.json'; index = json.loads(path.read_bytes())
        index['candidates'] = index['candidates'][:25]
        raw = policy.storage._json(index); path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        self.manifest['candidate_index_sha256'] = digest
        safe_path = self.job/'safe.json'; safe = json.loads(safe_path.read_bytes())
        safe['candidate_query'].update(index_sha256=digest, indexed_candidates=25, index_truncated=True)
        safe_raw = policy.storage._json(safe); safe_path.write_bytes(safe_raw)
        self.manifest['safe_sha256'] = hashlib.sha256(safe_raw).hexdigest()
        (self.job/'manifest.json').write_bytes(policy.storage._json(self.manifest))
        self.query_args['index_sha256'] = digest
        page = self.query(offset=20)
        self.assertEqual(len(page['candidates']), 5)
        self.assertTrue(page['selection']['index_truncated'])
        self.assertFalse(page['summary']['counts_complete'])
        self.assertIsNone(page['selection']['next_call'])

    def test_schema_filters_reject_raw_strings_paths_and_unbounded_queries(self):
        self.prepare()
        self.assertIn('triage_query', shell.schema()['properties']['action']['enum'])
        with patch.object(shell, 'run_container', side_effect=AssertionError('parser rerun')):
            direct = shell.invoke(self.case, self.query_args, self.session)
        self.assertEqual(direct['query_state'], 'returned')
        for change in ({'limit':21}, {'offset':True}, {'record_type': 'secret'}, {'rule_id':'secret'},
                {'record_index_min':5,'record_index_max':4}, {'command':'cat private'},
                {'scan_id':'../private'}, {'path':'private'}, {'user':'secret'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                shell.validate({**self.query_args, **change})


class SafeIndexHelperTests(unittest.TestCase):
    setUp = fixtures.TriageHelperTests.setUp
    request = fixtures.TriageHelperTests.request
    actual_parser = fixtures.TriageHelperTests.actual_parser
    execute = fixtures.TriageHelperTests.execute

    def test_real_binary_parser_retains_safe_metadata_beyond_first_page(self):
        body = self.execute(self.request(b'\0'*(40*384), candidate_index_limit=policy.MAX_INDEX_CANDIDATES, record_limit=40))
        self.assertEqual(len(body['candidates']), 2)
        self.assertEqual(len(body['candidate_index']),40)
        self.assertEqual(body['candidate_index'][35]['record_index'],35)
        self.assertEqual(set(body['candidate_index'][35]),
            {'input_id','ordinal','record_index','record_type','byte_start','byte_end'})

    def test_helper_index_bound_does_not_change_observed_counts(self):
        with patch.object(scan, 'MAX_INDEX_CANDIDATES', 5):
            body = self.execute(self.request(b'\0'*(40*384), candidate_index_limit=20000, record_limit=40))
        self.assertEqual(len(body['candidate_index']),5)
        self.assertEqual(body['inputs'][0]['candidate_count'],40)


class InitialRecommendationsTests(unittest.TestCase):
    def test_unknown_os_and_unavailable_purpose_do_not_fallback_or_gate(self):
        self.assertEqual(policy.recommendations('unknown','quick')['profiles'],[])
        self.assertEqual(policy.recommendations('unknown','quick')['state'],'os_identification_required')
        self.assertEqual(policy.recommendations('macos','quick')['profiles'],[])
        self.assertEqual(policy.recommendations('linux','account')['profiles'],['linux_login_parser'])
        self.assertEqual(policy.recommendations('windows','persistence')['profiles'],['windows_evtx'])
        self.assertEqual(policy.recommendations('linux','data_movement')['state'],'no_registered_profile')
        self.assertIn('omit',policy.recommendations('windows','quick')['skip_guidance'])
        self.assertEqual(policy.PROFILES['linux_login_parser']['target_os'],'linux')
        with self.assertRaises(ValueError): policy.recommendations('guess','quick')


if __name__ == '__main__':
    unittest.main()
