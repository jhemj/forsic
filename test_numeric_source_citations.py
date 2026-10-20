"""Explicit finite numeric retained fields: offline synthetic native receipts."""
import json
from copy import deepcopy
import unittest
from unittest.mock import patch

import test_efficiency_contracts as efficiency
import test_grounded_judgment as grounded
from forsic_plugin.report_driven.host import current, find
from forsic_plugin.report_driven.assessment_basis import _citation
from forsic_plugin.report_driven.grounded_judgment import selection, read_refs


class NumericSourceCitationTests(unittest.TestCase):
    setUp = efficiency.EfficiencyContracts.setUp
    call = efficiency.EfficiencyContracts.call
    payload = grounded.GroundedJudgmentTests.payload

    def result(self, value):
        start = self.case.record('tool_start', {'tool': 'forsic_image_files',
            'arguments': {'action': 'stat', 'file_path': '/synthetic-object'}}, 'synthetic-session')
        return self.case.record('tool_result', {'tool': 'forsic_image_files', 'started_id': start,
            'file_path': '/synthetic-object', 'times': {'mtime': value}}, 'synthetic-session')

    def page(self, sid, pointer='/times/mtime'):
        source = find(current(self.case), 'sources', sid)
        return self.call('forsic_reporting', {'action': 'source', 'source_id': sid,
            'source_version': source['version'], 'pointer': pointer})

    def source_items(self, page):
        from forsic_plugin.report_driven.model_pages import MAX_BYTES, size
        items = []
        while True:
            self.assertNotIn('error', page, page)
            raw = self.case.event(page['evidence_id'])['data']
            self.assertLessEqual(size({k: v for k, v in raw.items() if k not in ('tool', 'started_id')}), MAX_BYTES)
            items.extend(page['items'])
            if page['next_call'] is None:
                return items
            page = self.call('forsic_reporting', page['next_call'])

    def test_discovery_returns_child_reads_for_small_and_large_stat_results(self):
        for large in (False, True):
            with self.subTest(large=large):
                start = self.case.record('tool_start', {'tool': 'forsic_image_files',
                    'arguments': {'action': 'stat', 'file_path': '/synthetic-object'}}, 'synthetic-session')
                data = {'tool': 'forsic_image_files', 'started_id': start,
                    'file_path': '/synthetic-object', 'description': 'x' * (20000 if large else 1),
                    'volume_offset': 0, 'inode': 12, 'bytes': 123,
                    'times': {'atime': 10, 'modified/~timestamp': 0, 'ctime': 20}}
                sid = self.case.record('tool_result', data, 'synthetic-session')
                discovery = self.call('forsic_reporting', {'action': 'source', 'source_id': sid})
                self.assertNotIn('error', discovery, discovery)
                self.assertNotIn('citation_ref', discovery)
                self.assertTrue(discovery['partial_view'])
                fields = self.source_items(discovery)
                self.assertEqual([field['key'] for field in fields], list(data))
                for field in fields:
                    if 'value' in field:
                        self.assertEqual(field['value'], data[field['key']])
                    self.assertEqual(field['read_call']['source_handle'], discovery['source_handle'])
                times = next(field for field in fields if field['key'] == 'times')
                times_page = self.call('forsic_reporting', {**times['read_call'], 'limit': 1})
                numeric_fields = self.source_items(times_page)
                self.assertEqual([field['value'] for field in numeric_fields], [10, 0, 20])
                field = numeric_fields[1]
                self.assertEqual(field['pointer'], '/times/modified~1~0timestamp')
                page = self.call('forsic_reporting', field['read_call'])
                self.assertEqual(page['text'], '0')
                self.assertEqual(selection(self.case, current(self.case), page['citation_ref'])['pointer'], field['pointer'])

    def test_zero_search_coverage_fields_are_navigable_without_inventing_absence(self):
        start = self.case.record('tool_start', {'tool': 'forsic_search',
            'arguments': {'path': '/synthetic-scope', 'query': 'absent'}}, 'synthetic-session')
        sid = self.case.record('tool_result', {'tool': 'forsic_search', 'started_id': start,
            'search_text': 'absent', 'matches': [], 'files_scanned': 0, 'files_completed': 0,
            'bytes_scanned': 0, 'coverage_complete': False, 'scan_exhausted': False,
            'next_cursor': None}, 'synthetic-session')
        discovery = self.call('forsic_reporting', {'action': 'source', 'source_id': sid})
        fields = {field['pointer']: field for field in self.source_items(discovery)}
        self.assertNotIn('/match_count', fields)
        self.assertFalse(json.loads(discovery['retained_coverage'])['coverage_complete'])
        for pointer in ('/files_scanned', '/files_completed', '/bytes_scanned'):
            page = self.call('forsic_reporting', fields[pointer]['read_call'])
            self.assertEqual(page['text'], '0')
            citation = selection(self.case, current(self.case), page['citation_ref'])
            self.assertEqual(_citation(self.case, current(self.case), citation)[1]['presented_view'], 'metadata')
            self.assertFalse(page['acquisition_context']['returned_scope']['coverage_complete'])
        for pointer in ('/matches', '/coverage_complete', '/next_cursor'):
            self.assertNotIn('citation_ref', self.call('forsic_reporting', fields[pointer]['read_call']))
        located = self.call('forsic_reporting', {'action': 'source', 'source_id': sid, 'query': 'missing'})
        self.assertEqual(located['matches'], [])
        self.assertNotIn('citation_ref', located)

    def test_numeric_navigation_preserves_existing_text_page_and_direct_citations(self):
        sid = self.results[0]['evidence_id']
        event = self.case.event(sid)
        direct = read_refs(self.case, {**event['data'], 'evidence_id': sid})
        self.assertTrue(direct['presented_spans'])
        citation = selection(self.case, current(self.case), direct['presented_spans'][0]['citation_ref'])
        page = self.page(sid, citation['pointer'])
        self.assertEqual(page['text'], citation['literal'])
        self.assertEqual(selection(self.case, current(self.case), page['citation_ref']), citation)

    def test_numeric_page_c1_adopts_whole_typed_metadata_value(self):
        values = (0, -7, 2**70, 0.125, -0.0, 1e30, 1e-30)
        for index, value in enumerate(values):
            with self.subTest(value=value):
                sid = self.result(value)
                page = self.page(sid)
                self.assertNotIn('error', page, page)
                self.assertIn('citation_ref', page)
                self.assertTrue(page['citation_ref'].startswith('C1:'))
                literal = json.dumps(value, allow_nan=False)
                self.assertEqual(page['text'], literal)
                self.assertEqual(page['value'], value)
                self.assertEqual(page['retained_scalar']['value_type'], type(value).__name__)
                payload = self.payload([page['citation_ref']])
                payload['operation_id'] = 'number-'+str(index)
                saved = self.call('forsic_question', payload)
                self.assertNotIn('error', saved, saved)
                state = current(self.case)
                q = find(state, 'questions', saved['question']['question_id'])
                claim = find(state, 'claims', q['claim_refs'][0]['id'])
                observation = find(state, 'observations', claim['source_refs'][0]['id'])
                self.assertEqual(observation['literal'], literal)
                self.assertEqual((observation['byte_start'], observation['byte_end']), (0, len(literal)))
                self.assertEqual(observation['presented_view'], 'metadata')
                self.assertEqual(observation['acquisition_context']['field_role'], 'metadata')
                self.assertEqual(observation['acquisition_context']['retained_scalar'], page['retained_scalar'])
                self.assertIn('canonical JSON', observation['coordinate_basis'])
                self.assertFalse(q['judgment_review_required'])

    def test_numbers_are_explicit_metadata_not_automatic_body_or_locator_matches(self):
        sid = self.result(123456)
        data = self.case.event(sid)['data']
        self.assertNotIn('presented_spans', read_refs(self.case, {**data, 'evidence_id': sid}))
        parent = self.page(sid, '/times')
        self.assertNotIn('citation_ref', parent)
        located = self.call('forsic_reporting', {'action': 'source', 'source_id': sid, 'query': '123456'})
        self.assertEqual(located['matches'], [])
        c = selection(self.case, current(self.case), self.page(sid)['citation_ref'])
        self.assertEqual(_citation(self.case, current(self.case), c)[1]['presented_view'], 'metadata')
        for changed in ({'literal': '123'}, {'literal': '23456', 'byte_start': 1},
                        {'literal': '123456.0'}, {'byte_start': True}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                _citation(self.case, current(self.case), {**c, **changed})

    def test_boolean_null_composite_and_nonfinite_fields_never_gain_numeric_citations(self):
        from forsic_plugin.report_driven.retained_scalar import number_literal
        for value in (True, False, None, [], {}, float('inf'), float('-inf'), float('nan')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    number_literal(value)
                page = self.page(self.result(value))
                self.assertNotIn('citation_ref', page)
                self.assertNotIn('presented_spans', page)

    def test_changed_type_or_value_and_tampered_source_receipt_are_rejected(self):
        sid = self.result(1)
        page = self.page(sid)
        state = current(self.case)

        def replace(event):
            with self.case.connect() as db:
                db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(event['data']), event['id']))

        for eid, change in (
            (sid, lambda d: d['times'].update(mtime=1.0)),
            (sid, lambda d: d['times'].update(mtime=True)),
            (sid, lambda d: d['times'].update(mtime=2)),
            (page['evidence_id'], lambda d: d.update(text='2')),
            (page['evidence_id'], lambda d: d.update(value=True)),
            (page['evidence_id'], lambda d: d.update(value=1.0)),
            (page['evidence_id'], lambda d: d['retained_scalar'].update(value_type='float')),
            (page['evidence_id'], lambda d: d.update(byte_start=1)),
        ):
            with self.subTest(eid=eid, change=change):
                old = self.case.event(eid)
                changed = deepcopy(old)
                change(changed['data'])
                replace(changed)
                try:
                    with self.assertRaises(ValueError):
                        selection(self.case, state, page['citation_ref'])
                finally:
                    replace(old)
        with self.assertRaises(ValueError):
            selection(self.case, {**state, 'sources': []}, page['citation_ref'])
        stale = deepcopy(state)
        find(stale, 'sources', sid)['version'] = 'stale'
        with self.assertRaises(ValueError):
            selection(self.case, stale, page['citation_ref'])

    def test_numeric_assessment_preserves_metadata_contract_and_body_requirement(self):
        sid = self.result(86400.25)
        page = self.page(sid)
        for view in ('full_field', 'metadata'):
            q = find(current(self.case), 'questions', self.qid)
            mission = self.call('forsic_mission', dict(operation_id='number-mission-'+view,
                question_id=q['id'], expected_revision=q['revision'], gap_id=q['remaining_gap_ids'][0],
                result_ids=[sid], required_view=view, why_it_matters='Inspect returned metadata only',
                inconclusive_rule='Metadata does not establish an execution event',
                limits=['No occurrence time established'], reopen_conditions=['Independent event record']))
            self.assertNotIn('error', mission, mission)
            q = find(current(self.case), 'questions', self.qid)
            with patch('socket.socket', side_effect=AssertionError('offline only')):
                result = self.call('forsic_assess', dict(operation_id='number-assess-'+view,
                    mission_id=mission['mission']['id'], expected_revision=q['revision'], result_id=sid,
                    citation_refs=[page['citation_ref']], outcome='inconclusive',
                    reasoning_summary='A retained metadata value is observed; no event inferred',
                    limitations=['Only the selected retained numeric field'], next_check='Independent event record'))
            if view == 'full_field':
                self.assertIn('error', result)
                self.assertIn('metadata', result['error'])
            else:
                self.assertNotIn('error', result, result)
                state = current(self.case)
                assessment = find(state, 'assessments', result['assessment']['id'])
                observation = find(state, 'observations', assessment['observation_refs'][0]['id'])
                self.assertEqual(observation['presented_view'], 'metadata')
                self.assertEqual(observation['acquisition_context']['retained_scalar'], page['retained_scalar'])
                self.assertIn('canonical JSON', observation['coordinate_basis'])


if __name__ == '__main__':
    unittest.main()
