"""Offline final-review coordinates for synthetic retained scalar observations."""
from copy import deepcopy
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import test_efficiency_contracts as fixtures
import test_grounded_judgment as grounded
import test_numeric_source_citations as numeric
from forsic_plugin import independent_review as review
from forsic_plugin.independent_review_state import final_grounding
from forsic_plugin.report_driven.host import source_record
from forsic_plugin.report_driven.retained_scalar import NUMBER_COORDINATES


class ReviewNumericGroundingTests(unittest.TestCase):
    call = fixtures.EfficiencyContracts.call
    result = numeric.NumericSourceCitationTests.result
    page = numeric.NumericSourceCitationTests.page
    source_items = numeric.NumericSourceCitationTests.source_items
    payload = grounded.GroundedJudgmentTests.payload

    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        scope = patch.dict(os.environ, {'HERMES_HOME': home.name})
        scope.start(); self.addCleanup(scope.stop)
        fixtures.EfficiencyContracts.setUp(self)

    def adopt(self, value):
        sid = self.result(value)
        discovery = self.call('forsic_reporting', {'action': 'source', 'source_id': sid})
        times = next(item for item in self.source_items(discovery) if item['key'] == 'times')
        metadata = self.call('forsic_reporting', times['read_call'])
        mtime = next(item for item in self.source_items(metadata) if item['key'] == 'mtime')
        page = self.call('forsic_reporting', mtime['read_call'])
        payload = self.payload([page['citation_ref']])
        payload['operation_id'] = 'review-number-'+sid
        saved = self.call('forsic_question', payload)
        self.assertNotIn('error', saved, saved)
        questions = review._question_records({'case': self.case, 'goal_id': 'synthetic-goal',
                                              'session_lineage': ['synthetic-session']})
        question = next(q for q in questions if q['id'] == saved['question']['question_id'])
        return sid, question

    def project(self, question):
        with patch('socket.socket', side_effect=AssertionError('offline only')):
            return final_grounding({'case': self.case}, [question])

    def forged_observation(self, sid, literal, *, pointer='/times/mtime', start=0, **changes):
        source = source_record(self.case.event(sid))
        observation = dict(id='O-synthetic-review', version='1',
            source_ref={'kind': 'source', 'id': sid, 'version': source['version']},
            field_pointer=pointer, literal=literal, byte_start=start,
            byte_end=start+len(literal.encode()),
            canonical_sha256=hashlib.sha256(literal.encode()).hexdigest(),
            presented_view='metadata', coordinate_basis=NUMBER_COORDINATES)
        observation.update(changes)
        claim = dict(id='F-synthetic-review', version='1', status='adopted',
            statement='A retained metadata value is recorded; event occurrence is not established.',
            source_refs=[{'kind': 'observation', 'id': observation['id'], 'version': '1'}],
            counterevidence_refs=[], semantic_review='not_reviewed')
        self.case.record('report_change', {'records': {'claims': [claim], 'observations': [observation]}})
        return {'id': 'Q-synthetic-review', 'claim_refs': [{'kind': 'claim', 'id': claim['id'], 'version': '1'}]}

    def test_adopted_finite_numbers_reach_final_review_as_whole_typed_metadata(self):
        for value in (0, -7, 2**70, 0.125, -0.0, 1e30, 1e-30):
            with self.subTest(value=value):
                sid, question = self.adopt(value)
                grounding, fields = self.project(question)
                observation = grounding['observations'][0]
                literal = json.dumps(value, allow_nan=False)
                self.assertEqual(observation['literal'], literal)
                self.assertEqual(observation['source_ref']['id'], sid)
                self.assertEqual((observation['byte_start'], observation['byte_end']), (0, len(literal)))
                self.assertEqual(observation['presented_view'], 'metadata')
                self.assertEqual(observation['coordinate_basis'], NUMBER_COORDINATES)
                context = observation['acquisition_context']
                self.assertEqual(context['field_role'], 'metadata')
                self.assertEqual(context['retained_scalar']['value'], value)
                self.assertEqual(context['retained_scalar']['value_type'], type(value).__name__)
                self.assertEqual(observation['coordinate_validation'], 'retained_source_version_and_exact_utf8_span_only')
                self.assertFalse(observation['semantic_validation'])
                self.assertEqual(fields[0]['value'], literal)
                self.assertEqual(fields[0]['presented_view'], 'metadata')
                self.assertFalse(grounding['omitted']['source_fields'])

    def test_partial_number_changed_type_hash_and_unavailable_source_are_unverified(self):
        sid = self.result(12345)
        source = source_record(self.case.event(sid))
        for literal, changes in (
            ('234', {'start': 1}),
            ('12345.0', {}),
            ('12345', {'canonical_sha256': '0'*64}),
            ('12345', {'source_ref': {'kind': 'source', 'id': sid, 'version': 'stale'}}),
            ('12345', {'source_ref': {'kind': 'source', 'id': 'missing-source', 'version': source['version']}}),
        ):
            with self.subTest(literal=literal, changes=changes):
                question = self.forged_observation(sid, literal, **changes)
                grounding, fields = self.project(question)
                self.assertEqual(fields, [])
                self.assertEqual(grounding['observations'][0]['coordinate_validation'], 'unverified')
                self.assertFalse(grounding['observations'][0]['semantic_validation'])

    def test_source_int_to_float_change_is_not_silently_refreshed(self):
        sid, question = self.adopt(1)
        event = deepcopy(self.case.event(sid))
        event['data']['times']['mtime'] = 1.0
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(event['data']), sid))
        grounding, fields = self.project(question)
        self.assertEqual(fields, [])
        self.assertIn('version', grounding['observations'][0]['unmet_expectation'])

    def test_bool_null_composite_and_nonfinite_values_never_become_numeric_quote_fields(self):
        sid = self.result(1)
        question = self.forged_observation(sid, '1')
        for value in (True, False, None, [], {}, float('inf'), float('-inf'), float('nan')):
            with self.subTest(value=value), patch('forsic_plugin.report_driven.model_pages.select', return_value=value):
                grounding, fields = self.project(question)
                self.assertEqual(fields, [])
                self.assertEqual(grounding['observations'][0]['coordinate_validation'], 'unverified')

    def test_legacy_exact_string_excerpt_keeps_body_context(self):
        start = self.case.record('tool_start', {'tool': 'forsic_read', 'arguments': {'path': 'synthetic.log'}}, 'synthetic-session')
        sid = self.case.record('tool_result', {'tool': 'forsic_read', 'started_id': start,
            'path': 'synthetic.log', 'lines': [{'line': 1, 'text': 'Request recorded; result unknown.'}]}, 'synthetic-session')
        question = self.forged_observation(sid, 'recorded', pointer='/lines/0/text', start=8,
            presented_view='exact_excerpt', coordinate_basis='retained field UTF-8 bytes')
        grounding, fields = self.project(question)
        observation = grounding['observations'][0]
        self.assertEqual(observation['literal'], 'recorded')
        self.assertEqual(observation['acquisition_context']['field_role'], 'body')
        self.assertNotIn('retained_scalar', observation['acquisition_context'])
        self.assertEqual(fields[0]['presented_view'], 'exact_excerpt')
        self.assertFalse(observation['semantic_validation'])


if __name__ == '__main__':
    unittest.main()
