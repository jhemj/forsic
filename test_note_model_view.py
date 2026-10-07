"""Model context stays current; full forensic notes and revisions stay lossless."""
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from forsic_plugin import register
from forsic_plugin.intake import Intake, write_json
from forsic_plugin.notes import model_view, note
import test_workflow  # Native skill-loader stubs for offline registration.


class NoteModelViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        evidence = root / 'evidence'; evidence.mkdir()
        (evidence / 'events.log').write_text('request only\n')
        self.intake = Intake(root / 'intake', synthetic_roots=[evidence])
        self.intake.before('session', str(evidence))
        self.intake.inspect('session', {'path': str(evidence)})
        self.case = self.intake.case('session')
        ctx = Mock()
        ctx.get_config.side_effect = {'intake_root': str(root / 'intake'), 'synthetic_roots': [str(evidence)]}.get
        register(ctx)
        call = next(c for c in ctx.register_tool.call_args_list if c.kwargs['name'] == 'forsic_note')
        self.schema = call.kwargs['schema']
        self.handler = call.kwargs['handler']
        self.first = note(self.case, {'action': 'save', 'question': 'What executed?',
            'answer': 'Initial interpretation ' * 100, 'critical_gaps': ['Execution not yet established'],
            'alternatives': ['Another operation'], 'next_checks': ['Read outcome record']})['note']
        self.latest = note(self.case, {**self.first, 'action': 'save',
            'answer': 'Only a request is observed ' * 100, 'correction_reason': 'Scope correction'})['note']

    def tearDown(self): self.tmp.cleanup()

    def call(self, **args):
        return json.loads(self.handler(args, session_id='session'))

    def test_registered_get_keeps_complete_current_note_without_old_claims(self):
        response = self.call(action='get', note_id=self.first['note_id'])
        self.assertEqual(response['note'], self.latest)
        self.assertNotIn('history', response)
        self.assertEqual(response['history_total'], 2)
        self.assertNotIn(self.first['answer'], json.dumps(response))
        retained = self.case.event(response['evidence_id'])['data']
        self.assertEqual(retained['history'], [self.latest, self.first])
        self.assertIn('include_history', self.schema['parameters']['properties'])

    def test_history_is_explicit_paginated_and_preserved(self):
        first = self.call(action='get', note_id=self.first['note_id'], include_history=True, limit=1)
        self.assertEqual(first['history'], [self.latest])
        self.assertEqual(first['history_next_offset'], 1)
        second = self.call(action='get', note_id=self.first['note_id'], include_history=True,
                           limit=1, offset=first['history_next_offset'])
        self.assertEqual(second['history'], [self.first])
        self.assertIsNone(second['history_next_offset'])

    def test_list_is_marked_index_and_does_not_hide_gap_counts(self):
        index = self.call(action='list', query='executed', limit=1)
        row = index['notes'][0]
        self.assertFalse(row['detail_included'])
        self.assertEqual(row['counts']['critical_gaps'], 1)
        self.assertNotIn('answer', row)
        detail = self.call(action='get', note_id=row['note_id'])['note']
        self.assertEqual(detail['critical_gaps'], self.latest['critical_gaps'])
        self.assertEqual(detail['alternatives'], self.latest['alternatives'])

    def test_save_ack_keeps_revision_and_full_result_in_receipt(self):
        saved = self.call(**{**self.latest, 'action': 'save', 'answer': 'Outcome still unknown'})
        self.assertTrue(saved['saved'])
        self.assertFalse(saved['detail_included'])
        self.assertEqual(saved['note']['revision'], 3)
        self.assertNotIn('answer', saved['note'])
        retained = self.case.event(saved['evidence_id'])['data']['note']
        self.assertEqual(retained['answer'], 'Outcome still unknown')
        self.assertEqual(retained['critical_gaps'], self.latest['critical_gaps'])
        self.assertEqual(self.call(action='get', note_id=saved['note']['note_id'])['note'], retained)

    def test_conflict_is_not_turned_into_success_and_can_recover(self):
        result = self.call(**{**self.first, 'action': 'save', 'answer': 'stale change'})
        self.assertNotIn('saved', result)
        self.assertIn('expected current revision=2', result['error'])
        current = self.call(action='get', note_id=self.first['note_id'])['note']
        updated = self.call(**{**current, 'action': 'save', 'answer': 'current change'})
        self.assertEqual(updated['note']['revision'], 3)

    def test_projection_is_pure_and_reduces_only_model_presentation(self):
        full = note(self.case, {'action': 'get', 'note_id': self.first['note_id']})
        before = deepcopy(full)
        projected = model_view(full, {'action': 'get'})
        self.assertEqual(full, before)
        self.assertLess(len(json.dumps(projected)), len(json.dumps(full)) * .6)
        self.assertEqual(projected['note'], before['note'])


if __name__ == '__main__':
    unittest.main()
