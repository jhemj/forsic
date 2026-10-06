"""Offline note conflict recovery: synthetic data only, no models or services."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from threading import Barrier
import unittest

from forsic_plugin.evidence import Case
from forsic_plugin.notes import note


class NoteRevisionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / 'evidence').mkdir()
        manifest = root / 'case.json'
        manifest.write_text(json.dumps({'case_id': 'synthetic-revisions', 'synthetic': True,
                                       'evidence_root': str(root / 'evidence'),
                                       'output_root': str(root / 'results')}))
        self.case = Case(manifest)
        first = note(self.case, {'action': 'save', 'question': '무엇을 확인했나?',
                                 'answer': '아직 미확인', 'critical_gaps': ['추가 원문 확인 필요']})['note']
        self.saved = note(self.case, {**first, 'action': 'save', 'answer': '요청 기록만 확인'})['note']
        self.assertEqual(self.saved['revision'], 2)

    def tearDown(self):
        self.tmp.cleanup()

    def history(self):
        return note(self.case, {'action': 'get', 'note_id': self.saved['note_id']})

    def test_get_exposes_current_note_and_keeps_newest_first_history(self):
        before = self.case.db.read_bytes()
        result = self.history()
        self.assertEqual(result['note'], self.saved)
        self.assertEqual([n['revision'] for n in result['history']], [2, 1])
        self.assertEqual(result['note'], result['history'][0])
        self.assertIn('current revision', result['revision_hint'])
        self.assertIn('not the next revision', result['revision_hint'])
        self.assertEqual(before, self.case.db.read_bytes())

    def test_current_two_is_supplied_unchanged_and_success_saves_three(self):
        before = deepcopy(self.history()['history'])
        saved = note(self.case, {**self.saved, 'action': 'save', 'revision': 2,
                                 'answer': '요청은 있으나 실제 실행 성공은 미확인'})['note']
        self.assertEqual(saved['revision'], 3)
        self.assertEqual(self.history()['history'][1:], before)

    def test_stale_future_and_missing_revision_explain_recovery_without_writing(self):
        for supplied in (1, 3, None):
            with self.subTest(supplied=supplied):
                before = self.case.db.read_bytes()
                with self.assertRaises(ValueError) as failure:
                    note(self.case, {**self.saved, 'action': 'save', 'revision': supplied,
                                     'answer': '경합한 변경'})
                message = str(failure.exception)
                self.assertIn('expected current revision=2', message)
                self.assertIn(f'received revision={supplied!r}', message)
                self.assertIn('No note was saved', message)
                self.assertIn("action='get'", message)
                self.assertIn(self.saved['note_id'], message)
                self.assertIn('do not increment', message)
                self.assertEqual(before, self.case.db.read_bytes())

    def test_read_current_then_reapply_preserves_other_change(self):
        stale = deepcopy(self.saved)
        changed = note(self.case, {**self.saved, 'action': 'save',
                                   'alternatives': ['다른 정상 작업일 수도 있음']})['note']
        with self.assertRaises(ValueError):
            note(self.case, {**stale, 'action': 'save', 'answer': '추가 판별 필요'})
        refreshed = self.history()['note']
        saved = note(self.case, {**refreshed, 'action': 'save', 'answer': '추가 판별 필요'})['note']
        self.assertEqual(saved['revision'], 4)
        self.assertEqual(saved['alternatives'], changed['alternatives'])
        self.assertEqual(saved['critical_gaps'], stale['critical_gaps'])
        self.assertEqual(self.history()['history'][1], changed)

    def test_identical_current_save_reuses_without_new_revision(self):
        before = self.case.db.read_bytes()
        result = note(self.case, {**self.saved, 'action': 'save'})
        self.assertTrue(result['reused'])
        self.assertEqual(result['note'], self.saved)
        self.assertEqual(before, self.case.db.read_bytes())
        with self.assertRaises(ValueError):
            note(self.case, {**self.saved, 'action': 'save', 'revision': 1})

    def test_tool_response_carries_recovery_and_preserves_failed_attempt_receipt(self):
        failed = json.loads(self.case.invoke('forsic_note', {
            **self.saved, 'action': 'save', 'revision': 3, 'answer': '추가 확인 필요'}))
        self.assertEqual(failed['error_type'], 'ValueError')
        self.assertIn('expected current revision=2', failed['error'])
        self.assertIn('received revision=3', failed['error'])
        self.assertEqual([n['revision'] for n in self.history()['history']], [2, 1])
        self.assertEqual(self.case.event(failed['evidence_id'])['data']['error'], failed['error'])
        refreshed = json.loads(self.case.invoke('forsic_note', {
            'action': 'get', 'note_id': self.saved['note_id']}))['note']
        saved = json.loads(self.case.invoke('forsic_note', {
            **refreshed, 'action': 'save', 'answer': '추가 확인 필요'}))['note']
        self.assertEqual(saved['revision'], 3)

    def test_simultaneous_updates_cannot_overwrite_the_same_revision(self):
        barrier = Barrier(2)

        def update(answer):
            barrier.wait(timeout=5)
            try:
                return note(self.case, {**self.saved, 'action': 'save', 'answer': answer})
            except ValueError as exc:
                return {'conflict': str(exc)}

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(update, ['첫 번째 변경', '두 번째 변경']))
        successes = [r['note'] for r in results if 'note' in r]
        failures = [r['conflict'] for r in results if 'conflict' in r]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(successes[0]['revision'], 3)
        self.assertIn('expected current revision=3', failures[0])
        self.assertIn('received revision=2', failures[0])
        history = self.history()['history']
        self.assertEqual([n['revision'] for n in history], [3, 2, 1])
        self.assertEqual(history[0], successes[0])
        self.assertEqual(history[1], self.saved)


if __name__ == '__main__':
    unittest.main()
