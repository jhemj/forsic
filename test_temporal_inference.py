"""Synthetic analyst time constraints; no model, network, image reader or deployment."""
from copy import deepcopy
import json
import tempfile
import unittest

from jsonschema import Draft202012Validator
from test_report_driven import fixture
from forsic_plugin.notes import note, timeline, timeline_schema
from forsic_plugin.temporal import MAX_EVENTS, MAX_REFS, event_identity, temporal_projection


class TemporalInferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.case, _, _ = fixture(self.tmp.name)
        self.stat = self.case.record('tool_result', {'tool': 'forsic_image_files', 'file_path': '/copied-object',
            'times': {'ctime': 1790866800, 'mtime': 1262304000}, 'filesystem': 'ext4',
            'time_basis': 'filesystem epoch; ctime is not necessarily creation'})
        self.log = self.case.record('tool_result', {'tool': 'forsic_read', 'path': 'synthetic.log',
            'lines': [{'line': 1, 'text': '관측 Oct  2 09:02:00 request run_id=R-5'},
                      {'line': 2, 'text': 'copy command preserved the input modification timestamp'}]})

    def basis(self, eid=None, **changes):
        value = {'evidence_id': eid or self.stat, 'pointer': '/times/ctime', 'value': 1790866800}
        value.update(changes)
        return value

    def model(self, **changes):
        value = {'observed_or_inferred': 'inferred', 'timestamp_role': 'event_occurrence',
            'clock_domain': 'synthetic-host/system-clock', 'timezone_basis': 'Unknown source timezone; proposed bounds explicitly use UTC.',
            'year_basis': 'Candidate year only; no year inferred from the current date.',
            'earliest': None, 'latest': None, 'assumptions': ['Selected records concern the same proposed event; this is not established by filename.'],
            'confidence_basis': 'Limited by missing clock calibration and execution records.',
            'basis_refs': [self.basis()]}
        value.update(changes)
        return value

    def row(self, event='TE-one', **changes):
        value = {'event_ref': event, 'description': 'Proposed event; source timestamp kept separately.',
                 'evidence_ids': [self.stat, self.log], 'time_source': {'evidence_id': self.stat, 'field': 'ctime'},
                 'temporal': self.model()}
        value.update(changes)
        return value

    def relation(self, target, relation='before', **changes):
        value = {'target_event_ref': target, 'relation': relation,
                 'reason': 'Selected evidence supports this analyst-proposed ordering.', 'basis_refs': [self.basis()]}
        value.update(changes)
        return value

    def save(self, rows, **changes):
        value = {'action': 'save', 'question': 'When could the proposed event have occurred?',
                 'answer': 'A bounded hypothesis with unresolved clock and event-time assumptions.',
                 'evidence_ids': [self.stat, self.log], 'timeline': rows}
        value.update(changes)
        return note(self.case, value)['note']

    def test_linux_ctime_is_metadata_and_event_time_remains_an_inference(self):
        row = self.row()
        row['temporal'].update(earliest='2026-10-01T00:00:00Z', latest='2026-10-03T00:00:00Z',
            filesystem_basis='Retained parser identifies ext4: ctime reflects inode status changes, not birth time.',
            limitations=['Metadata time does not identify the actor, creation event or successful execution.'])
        result = timeline([row], self.case)[0]
        self.assertEqual(result['raw_time'], 1790866800)
        self.assertEqual(result['literal_timestamp_ref']['pointer'], '/times/ctime')
        projected = temporal_projection(result, self.case)
        self.assertEqual(projected['timestamp_role'], 'event_occurrence')
        self.assertEqual(projected['observed_or_inferred'], 'inferred')
        self.assertIn('ctime을 생성 시각이나 사건 발생 시각으로 자동 해석하지 않습니다', ' '.join(projected['temporal_limitations']))
        bad = deepcopy(row); bad['temporal']['observed_or_inferred'] = 'observed'
        with self.assertRaisesRegex(ValueError, 'event occurrence'):
            timeline([bad], self.case)

    def test_missing_year_timezone_preserves_raw_and_multiple_explicit_candidates(self):
        literal = 'Oct  2 09:02:00'
        source = self.case.event(self.log)['data']['lines'][0]['text']
        row = self.row(time_source={'evidence_id': self.log, 'field': 'log', 'pointer': '/lines/0/text',
            'literal': literal, 'byte_start': source.encode().index(literal.encode())})
        row['temporal']['candidates'] = [
            {'earliest': '2025-10-02T09:02:00+09:00', 'latest': '2025-10-02T09:03:00+09:00', 'basis': 'Prior-year candidate under the stated local clock assumption.'},
            {'earliest': '2026-10-02T09:02:00+09:00', 'latest': '2026-10-02T09:03:00+09:00', 'basis': 'Alternative year candidate; the source does not select it.'}]
        result = timeline([row], self.case)[0]
        self.assertEqual(result['raw_time'], literal); self.assertEqual(result['time'], literal)
        self.assertFalse(result['comparable'])
        self.assertIsNone(result['temporal']['earliest'])
        self.assertEqual(len(temporal_projection(result)['temporal_candidates']), 2)
        self.assertEqual(result['literal_timestamp_ref']['literal'], literal)

    def test_selected_evidence_without_timestamp_can_express_relative_order(self):
        first = self.row(); first.pop('time_source')
        first['temporal']['basis_refs'] = [self.basis(self.log, pointer='/lines/0/text', value='관측 Oct  2 09:02:00 request run_id=R-5')]
        first['temporal']['relations'] = [self.relation('TE-two')]
        second = self.row('TE-two'); second.pop('time_source')
        rows = [first, second]
        self.assertEqual(list(Draft202012Validator(timeline_schema(require_source=True)).iter_errors(rows)), [])
        saved = self.save(rows)
        self.assertIsNone(saved['timeline'][0]['raw_time'])
        self.assertEqual(saved['timeline'][0]['temporal_conflicts'], [])
        self.assertTrue(saved['timeline'][0]['temporal']['relations'][0]['relation_ref'].startswith('TR-'))

    def test_malformed_interval_and_coordinate_fail_before_note_commit(self):
        before = len(self.case.events(500))
        for change in ({'earliest': '2026-10-03T00:00:00Z', 'latest': '2026-10-01T00:00:00Z'},
                       {'earliest': 'Oct 2'}, {'earliest': '2026-10-01T00:00:00'},
                       {'basis_refs': [self.basis(value=1790866801)]}, {'confidence': 0.95},
                       {'basis_refs': [self.basis(evidence_id='unselected')]}):
            with self.subTest(change=change):
                row = self.row(); row['temporal'].update(change)
                with self.assertRaises(ValueError): self.save([row])
                self.assertEqual(len(self.case.events(500)), before)

    def test_contradictory_valid_constraints_are_retained_as_recheck_hints(self):
        first, second = self.row(), self.row('TE-two')
        first['temporal'].update(earliest='2026-10-04T00:00:00Z', latest='2026-10-05T00:00:00Z',
                                 relations=[self.relation('TE-two')])
        second['temporal'].update(earliest='2026-10-01T00:00:00Z', latest='2026-10-02T00:00:00Z')
        saved = self.save([first, second])
        conflicts = saved['timeline'][0]['temporal_conflicts']
        self.assertEqual(conflicts[0]['reason'], 'declared_relation_conflicts_with_bounds')
        self.assertTrue(temporal_projection(saved['timeline'][0])['temporal_requires_recheck'])
        # Unknown clock alignment prevents an automatic absolute comparison.
        second['temporal']['clock_domain'] = 'other-host/uncalibrated'
        self.assertEqual(timeline([first, second], self.case)[0]['temporal_conflicts'], [])
        first['temporal']['relations'][0]['clock_alignment'] = 'Analyst assumes a common calibrated UTC clock, cited basis remains reviewable.'
        self.assertTrue(timeline([first, second], self.case)[0]['temporal_conflicts'])

    def test_relative_cycle_is_a_signal_even_when_all_dates_unknown(self):
        first, second = self.row(), self.row('TE-two')
        first['temporal']['relations'] = [self.relation('TE-two')]
        second['temporal']['relations'] = [self.relation('TE-one')]
        result = timeline([first, second], self.case)
        self.assertTrue(all(any(c['reason'] == 'strict_relative_order_cycle' for c in row['temporal_conflicts']) for row in result))
        self.assertTrue(all(row['temporal']['earliest'] is None for row in result))

    def test_misleading_preserved_mtime_does_not_become_copy_occurrence(self):
        row = self.row(time_source={'evidence_id': self.stat, 'field': 'mtime'})
        row['temporal'].update(earliest='2026-10-01T00:00:00Z', latest='2026-10-02T23:59:59Z',
            basis_refs=[self.basis(), self.basis(self.log, pointer='/lines/1/text', value='copy command preserved the input modification timestamp')],
            assumptions=['The cited copy record concerns this destination; preserved mtime may describe an older source object.'])
        result = timeline([row], self.case)[0]
        self.assertEqual(result['raw_time'], 1262304000)
        self.assertTrue(result['time'].startswith('2010-01-01'))
        self.assertTrue(temporal_projection(result)['earliest'].startswith('2026-10-01'))
        self.assertIn('복사·복원 후에도 이전 mtime', ' '.join(temporal_projection(result)['temporal_limitations']))

    def test_inference_correction_keeps_event_identity_and_old_note_history(self):
        row = self.row(); row.pop('event_ref')
        first = self.save([row])
        changed = deepcopy(row); changed['temporal'].update(latest='2026-10-09T00:00:00Z', confidence_basis='New bounded candidate; original raw source remains unchanged.')
        second = self.save([changed], note_id=first['note_id'], revision=first['revision'])
        self.assertEqual(first['timeline'][0]['event_ref'], second['timeline'][0]['event_ref'])
        history = note(self.case, {'action': 'get', 'note_id': first['note_id']})['history']
        self.assertIsNone(history[1]['timeline'][0]['temporal']['latest'])
        self.assertEqual(len(history), 2)

    def test_source_correction_marks_only_the_dependent_event(self):
        first, unrelated = timeline([self.row()], self.case)[0], self.row('TE-unrelated')
        unrelated['temporal']['basis_refs'] = [self.basis(self.log, pointer='/lines/1/text', value='copy command preserved the input modification timestamp')]
        unrelated.pop('time_source')
        unrelated = timeline([unrelated], self.case)[0]
        source = self.case.event(self.stat)['data']; source['times']['ctime'] += 3600
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(source), self.stat))
        self.assertTrue(temporal_projection(first, self.case)['temporal_requires_recheck'])
        self.assertFalse(temporal_projection(unrelated, self.case)['temporal_requires_recheck'])
        with self.assertRaisesRegex(ValueError, 'source_version changed'):
            timeline([first], self.case)

    def test_other_question_event_requires_explicit_current_note_selection(self):
        target = self.save([self.row('TE-target')])
        row = self.row('TE-source'); row['temporal']['relations'] = [self.relation('TE-target')]
        with self.assertRaisesRegex(ValueError, 'current batch event'):
            timeline([row], self.case)
        relation = row['temporal']['relations'][0]
        relation.update(target_note_id=target['note_id'], target_note_revision=target['revision'])
        saved = timeline([row], self.case)[0]
        self.assertFalse(temporal_projection(saved, self.case)['temporal_requires_recheck'])
        corrected = self.row('TE-target'); corrected['temporal']['confidence_basis'] = 'Updated assessment of the selected clock evidence.'
        self.save([corrected], note_id=target['note_id'], revision=target['revision'])
        projected = temporal_projection(saved, self.case)
        self.assertTrue(projected['temporal_requires_recheck'])
        self.assertEqual(projected['conflicting_constraints'][0]['reason'], 'temporal_relation_target_changed')

    def test_bounded_arrays_and_shell_source_role(self):
        row = self.row(); row['temporal']['basis_refs'] *= MAX_REFS + 1
        with self.assertRaises(ValueError): timeline([row], self.case)
        with self.assertRaises(ValueError): timeline([self.row()] * (MAX_EVENTS + 1), self.case)
        shell = self.case.record('tool_result', {'tool': 'forsic_shell', 'stdout': 'program says copied at 08:00'})
        row = self.row(); row.pop('time_source'); row['evidence_ids'].append(shell)
        row['temporal']['basis_refs'] = [self.basis(shell, pointer='/stdout', value='program says copied at 08:00')]
        saved = timeline([row], self.case)[0]
        self.assertEqual(saved['temporal']['basis_refs'][0]['source_role'], 'derived_command_output')
        row['temporal']['basis_refs'][0]['source_role'] = 'retained_tool_result'
        with self.assertRaisesRegex(ValueError, 'source role'): timeline([row], self.case)

    def test_minimal_optional_annotation_reuses_origin_and_defaults_unknowns(self):
        row = self.row(temporal={'observed_or_inferred': 'inferred', 'timestamp_role': 'event_occurrence',
                                 'confidence_basis': 'Useful relative hypothesis; exact event time remains unknown.'})
        self.assertEqual(list(Draft202012Validator(timeline_schema(require_source=True)).iter_errors([row])), [])
        saved = timeline([row], self.case)[0]
        self.assertEqual(saved['temporal']['clock_domain'], 'unknown')
        self.assertIsNone(saved['temporal']['earliest']); self.assertIsNone(saved['temporal']['latest'])
        self.assertEqual(saved['temporal']['assumptions'], [])
        self.assertEqual(saved['temporal']['basis_refs'], [saved['literal_timestamp_ref']])
        row['temporal']['latest'] = '2026-10-03T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'timezone/year'):
            timeline([row], self.case)
        row['temporal'].update(timestamp_role='source_timestamp')
        anchored = timeline([row], self.case)[0]['temporal']
        self.assertIn('Unix epoch', anchored['year_basis'])
        self.assertTrue(anchored['assumptions'])
        # An unrelated legacy row acquires no inferred clock or identity fields.
        old = {'description': 'Original timestamp only', 'evidence_ids': [self.stat],
               'time_source': {'evidence_id': self.stat, 'field': 'mtime'}}
        result = timeline([old], self.case)[0]
        self.assertNotIn('temporal', result); self.assertNotIn('event_ref', result)

    def test_explicit_cross_question_relation_compares_only_selected_target(self):
        target = self.row('TE-target')
        target['temporal'].update(earliest='2026-10-01T00:00:00Z', latest='2026-10-02T00:00:00Z')
        saved = self.save([target])
        source = self.row('TE-source')
        source['temporal'].update(earliest='2026-10-04T00:00:00Z', latest='2026-10-05T00:00:00Z',
            relations=[self.relation('TE-target', target_note_id=saved['note_id'], target_note_revision=saved['revision'])])
        result = timeline([source], self.case)[0]
        conflict = result['temporal_conflicts'][0]
        self.assertEqual(conflict['reason'], 'declared_relation_conflicts_with_bounds')
        self.assertEqual(conflict['event_refs'], ['TE-source'])
        self.assertEqual(conflict['target_note_id'], saved['note_id'])


if __name__ == '__main__':
    unittest.main()
