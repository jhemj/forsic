"""Real question writer -> temporal state -> report; synthetic local tools only."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from zipfile import ZipFile

import test_efficiency_contracts as fixtures
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.views import bundle, bundle_stale, compile_views, selected_report_state


class TemporalReportTests(unittest.TestCase):
    call = fixtures.EfficiencyContracts.call

    def setUp(self):
        fixtures.EfficiencyContracts.setUp(self)
        self.stat = self.case.record('tool_result', {'tool': 'forsic_image_files', 'file_path': '/synthetic-object',
            'volume_offset': 0, 'times': {'ctime': 1790866800}, 'time_basis': 'filesystem epoch; ctime is not necessarily creation'})
        self.read = self.call('forsic_read', {'path': 'request.log'})
        self.ref = {'evidence_id': self.read['evidence_id'], 'pointer': '/lines/0/text',
                    'literal': 'job=maintenance', 'byte_start': 0}

    def model(self, **changes):
        value = {'observed_or_inferred': 'inferred', 'timestamp_role': 'event_occurrence',
                 'confidence_basis': 'The record establishes a request; event bounds remain a hypothesis.'}
        value.update(changes)
        return value

    def event(self, **changes):
        value = {'description': 'A proposed occurrence with exact selected evidence and uncertain date.',
                 'evidence_ids': [self.stat, self.read['evidence_id']],
                 'temporal': self.model(basis_refs=[deepcopy(self.ref)])}
        value.update(changes)
        return value

    def payload(self, **changes):
        result = {'operation_id': 'temporal-create', 'question': 'When could this proposed event have happened?',
            'scope': 'Synthetic selected records', 'answer': 'The source records a request; its execution time is uncertain.',
            'reason': 'Preserve source time separately from analyst time inference.', 'priority': 'material',
            'timeline': [self.event()],
            'judgment': {'citation_refs': [self.read['presented_spans'][0]['citation_ref']],
                         'assertion_kind': 'interpretation', 'reasoning_summary': 'A request is not evidence of execution success.'}}
        result.update(changes)
        return result

    def save(self, **changes):
        result = self.call('forsic_question', self.payload(**changes))
        self.assertNotIn('error', result, result)
        qid = result['question'].get('question_id') or result['question']['id']
        return host.find(host.current(self.case), 'questions', qid)

    def times(self, qid, state=None):
        return [t for t in (state or host.current(self.case))['timeline'] if t['question_id'] == qid]

    def test_no_timestamp_optional_event_reaches_real_writer_and_valid_state(self):
        q = self.save()
        state = host.current(self.case); host.checked(state)
        rows = self.times(q['id'], state)
        self.assertEqual(len(rows), 1)
        tm = rows[0]
        self.assertEqual(tm['raw_values'], []); self.assertEqual(tm['normalized_values'], [])
        self.assertFalse(tm['comparable']); self.assertEqual(tm['shape'], 'unknown')
        self.assertEqual(tm['temporal_context']['clock_domain'], 'unknown')
        self.assertEqual(tm['temporal']['assumptions'], [])
        observed = host.find(state, 'observations', tm['observation_ref']['id'])
        self.assertEqual(observed['literal'], 'job=maintenance')
        self.assertEqual(observed['field_pointer'], '/lines/0/text')
        # Time-basis observations do not adopt a claim. The separately requested
        # judgment uses its original citation and its own observation identity.
        adopted = [c for c in state['claims'] if c['id'] in {ref['id'] for ref in q['claim_refs']}]
        self.assertNotIn(observed['id'], {ref['id'] for _, ref in host.find_refs(adopted) if ref['kind'] == 'observation'})

    def test_ctime_inference_never_replaces_observed_raw_or_sorts_as_point(self):
        row = self.event(time_source={'evidence_id': self.stat, 'field': 'ctime'},
            temporal=self.model(earliest='2026-09-29T00:00:00Z', latest='2026-10-03T00:00:00Z',
                timezone_basis='Analyst interval stated in UTC; original clock offset not calibrated.',
                year_basis='Selected filesystem epoch and stated event-link hypothesis.',
                assumptions=['The selected metadata status change may be related to the proposed occurrence.']))
        q = self.save(timeline=[row]); state = host.current(self.case); host.checked(state)
        tm = self.times(q['id'], state)[0]
        self.assertEqual(tm['raw_values'], ['1790866800']); self.assertEqual(tm['file_time_type'], 'ctime')
        self.assertEqual(tm['normalized_values'], []); self.assertFalse(tm['comparable'])
        self.assertEqual(tm['shape'], 'interval'); self.assertEqual(tm['time_kind'], 'event')
        self.assertEqual(tm['temporal_context']['earliest'], '2026-09-29T00:00:00Z')
        self.assertEqual(tm['temporal_context']['literal_timestamp_ref']['pointer'], '/times/ctime')
        self.assertIn('ctime을 생성 시각이나 사건 발생 시각으로 자동 해석하지 않습니다', tm['limitation'])

    def test_bad_time_bound_rolls_back_question_claim_and_time_mutations(self):
        before = host.current(self.case)
        row = self.event(temporal=self.model(earliest='2026-10-03T00:00:00Z', latest='2026-10-01T00:00:00Z',
            timezone_basis='UTC candidate.', year_basis='Explicit candidate.', assumptions=['Record linkage is assumed.'],
            basis_refs=[deepcopy(self.ref)]))
        result = self.call('forsic_question', self.payload(timeline=[row]))
        self.assertIn('error', result)
        after = host.current(self.case)
        for key in ('questions', 'claims', 'timeline', 'observations'):
            self.assertEqual(before[key], after[key])

    def test_correction_keeps_event_ref_changes_version_and_stales_report(self):
        q = self.save(); before = host.current(self.case)
        old = self.times(q['id'], before)[0]; rendered = bundle(self.case, before)
        changed = self.event(temporal=self.model(earliest='2026-10-01T00:00:00Z', latest='2026-10-02T00:00:00Z',
            timezone_basis='UTC candidate under the stated assumption.', year_basis='Proposed 2026 candidate.',
            assumptions=['Selected request belongs to the proposed interval.'], basis_refs=[deepcopy(self.ref)]))
        result = self.call('forsic_question', {'operation_id': 'temporal-correction', 'question_id': q['id'],
            'expected_revision': q['revision'], 'reason': 'State a bounded candidate without changing the raw source.',
            'timeline': [changed]})
        self.assertNotIn('error', result, result)
        after = host.current(self.case); host.checked(after)
        revised = self.times(q['id'], after)[0]
        self.assertEqual(old['event_ref'], revised['event_ref'])
        self.assertNotEqual(old['version'], revised['version'])
        self.assertTrue(bundle_stale(self.case, json.loads(Path(rendered['manifest']).read_text()), after))

    def test_overlapping_basis_deduplicates_observations_and_question_selection(self):
        row = self.event(temporal=self.model(basis_refs=[deepcopy(self.ref), deepcopy(self.ref)]))
        first = self.save(timeline=[row])
        second = self.save(operation_id='another-time-question', question='A separate event from shared raw evidence?',
            timeline=[self.event(description='PRIVATE_OTHER_QUESTION_EVENT')])
        state = host.current(self.case); host.checked(state)
        ids = [o['id'] for o in state['observations']]
        self.assertEqual(len(ids), len(set(ids)))
        first_time, second_time = self.times(first['id'], state)[0], self.times(second['id'], state)[0]
        self.assertNotEqual(first_time['observation_ref']['id'], second_time['observation_ref']['id'])
        selected = selected_report_state(state, [first['id']])
        self.assertEqual({t['question_id'] for t in selected['timeline']}, {first['id']})
        self.assertNotIn('PRIVATE_OTHER_QUESTION_EVENT', json.dumps(compile_views(selected)))

    def test_cross_question_relation_retains_reference_without_other_question_text(self):
        target = self.save(operation_id='time-target', timeline=[self.event(event_ref='TE-target', description='PRIVATE_TARGET_NARRATIVE')])
        row = self.event(event_ref='TE-source')
        row['temporal']['relations'] = [{'target_event_ref': 'TE-target', 'target_note_id': target['id'].removeprefix('Q-'),
            'target_note_revision': target['revision'], 'relation': 'before', 'reason': 'Proposed relative order under record linkage assumption.'}]
        source = self.save(operation_id='time-source', timeline=[row])
        state = host.current(self.case); host.checked(state)
        relation = self.times(source['id'], state)[0]['temporal_context']['temporal_relations'][0]
        self.assertEqual(relation['scoped_target_event_ref'], self.times(target['id'], state)[0]['event_ref'])
        selected = selected_report_state(state, [source['id']])
        self.assertNotIn('PRIVATE_TARGET_NARRATIVE', json.dumps(compile_views(selected)))

    def test_derived_shell_basis_keeps_its_label(self):
        shell = self.case.record('tool_result', {'tool': 'forsic_shell', 'stdout': 'derived timestamp candidate'})
        row = self.event(evidence_ids=[shell], temporal=self.model(basis_refs=[
            {'evidence_id': shell, 'pointer': '/stdout', 'value': 'derived timestamp candidate'}]))
        q = self.save(timeline=[row]); state = host.current(self.case); host.checked(state)
        tm = self.times(q['id'], state)[0]
        self.assertEqual(tm['temporal_context']['temporal_basis_refs'][0]['source_role'], 'derived_command_output')
        observation = host.find(state, 'observations', tm['observation_ref']['id'])
        self.assertIn('파생 명령 출력', observation['interpretation_limit'])
        self.assertIn('파생 명령 출력', tm['limitation'])

    def test_mutated_source_does_not_appear_as_current_observed_point(self):
        row = self.event(time_source={'evidence_id': self.stat, 'field': 'ctime'}, temporal={
            'observed_or_inferred': 'observed', 'timestamp_role': 'source_timestamp',
            'confidence_basis': 'The retained epoch is a parser observation, with no event-time inference.'})
        q = self.save(timeline=[row]); before = self.times(q['id'])[0]
        self.assertTrue(before['comparable'])
        data = self.case.event(self.stat)['data']; data['times']['ctime'] += 3600
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(data), self.stat))
        state = host.current(self.case); host.checked(state)
        stale = self.times(q['id'], state)[0]
        self.assertEqual(stale['raw_values'], ['1790866800'])
        self.assertFalse(stale['temporal_context']['source_basis_current'])
        self.assertTrue(stale['temporal_context']['temporal_requires_recheck'])
        self.assertIsNone(stale['observation_ref']); self.assertEqual(stale['normalized_values'], [])
        self.assertFalse(stale['comparable']); self.assertIn('재검토', stale['limitation'])

    def test_real_html_and_docx_keep_inference_source_clock_and_assumptions(self):
        row = self.event(time_source={'evidence_id': self.stat, 'field': 'ctime'}, temporal=self.model(
            earliest='2026-09-29T00:00:00Z', latest='2026-10-03T00:00:00Z',
            timezone_basis='PROPOSED_UTC_OFFSET_BASIS', year_basis='PROPOSED_YEAR_BASIS',
            clock_domain='SELECTED_HOST_CLOCK', assumptions=['ASSUMED_EVENT_LINK_NOT_PROVEN']))
        q = self.save(timeline=[row])
        report = bundle(self.case, host.current(self.case), question_ids=[q['id']])
        from forsic_plugin.report_driven.word_export import export_word
        word = export_word(self.case, report['bundle_id'])
        self.assertEqual({Path(p).suffix for p in report['files']}, {'.html'})
        for filename in [*report['files'], word['path']]:
            path = Path(filename)
            if path.suffix == '.html':
                text = path.read_text()
            else:
                with ZipFile(path) as document:
                    text = document.read('word/document.xml').decode()
            for literal in ('분석가의 시각 추정', '2026-09-29T00:00:00Z', '2026-10-03T00:00:00Z',
                            '1790866800', 'ctime', 'PROPOSED_UTC_OFFSET_BASIS', 'PROPOSED_YEAR_BASIS',
                            'SELECTED_HOST_CLOCK', 'ASSUMED_EVENT_LINK_NOT_PROVEN', 'ctime을 생성 시각이나 사건 발생 시각으로 자동 해석하지 않습니다'):
                self.assertIn(literal, text, filename)

    def test_selected_report_retains_origin_secondary_and_relation_basis_sources(self):
        extra = self.case.record('tool_result', {'tool': 'forsic_read', 'path': 'selected-secondary.log',
            'lines': [{'line': 1, 'text': 'secondary temporal linkage'}], 'complete_file': True})
        relation_source = self.case.record('tool_result', {'tool': 'forsic_read', 'path': 'selected-relation.log',
            'lines': [{'line': 1, 'text': 'relative order evidence'}], 'complete_file': True})
        row = self.event(event_ref='TE-first', time_source={'evidence_id': self.stat, 'field': 'ctime'})
        row['evidence_ids'].extend([extra, relation_source])
        row['temporal']['basis_refs'].append({'evidence_id': extra, 'pointer': '/lines/0/text', 'value': 'secondary temporal linkage'})
        row['temporal']['relations'] = [{'target_event_ref': 'TE-second', 'relation': 'before', 'reason': 'An explicit ordering hypothesis.',
            'basis_refs': [{'evidence_id': relation_source, 'pointer': '/lines/0/text', 'value': 'relative order evidence'}]}]
        q = self.save(timeline=[row, self.event(event_ref='TE-second')])
        state = host.current(self.case); host.checked(state)
        selected = selected_report_state(state, [q['id']])
        source_ids = {source['id'] for source in selected['sources']}
        self.assertTrue({self.stat, extra, relation_source, self.read['evidence_id']} <= source_ids)
        selected_observations = {o['source_ref']['id'] for o in selected['observations']}
        self.assertTrue({self.stat, extra, relation_source} <= selected_observations)
        first = next(t for t in selected['timeline'] if t['temporal_context']['local_event_ref'] == 'TE-first')
        self.assertEqual(len(first['temporal_context']['observation_refs']), 4)

    def test_generated_observation_versions_match_state_and_are_selectable_dependencies(self):
        first = self.save()
        state = host.current(self.case)
        refs = self.times(first['id'], state)[0]['temporal_context']['observation_refs']
        self.assertTrue(refs)
        for reference in refs:
            actual = host.find(state, 'observations', reference['id'])
            self.assertEqual(reference['version'], actual['version'])
        second = self.save(operation_id='select-generated-time-observation',
            question='Does this independent question rely on the selected temporal observation?',
            forensic={'dependencies': [{'reference': deepcopy(refs[0]),
                'reason': 'Explicitly select the displayed current temporal basis observation.'}]})
        selected = second['forensic']['dependencies'][0]['reference']
        self.assertEqual(selected, refs[0])
        self.assertEqual(second['forensic_context']['change_impacts'], [])
        again = host.current(self.case); host.checked(again)
        self.assertEqual(host.find(again, 'observations', refs[0]['id'])['version'], refs[0]['version'])


if __name__ == '__main__':
    unittest.main()
