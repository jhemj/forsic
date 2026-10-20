"""Synthetic current-note reads retain history without decoding superseded payloads."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.notes import current_notes


class NoteProjectionMemoryTests(unittest.TestCase):
    def test_latest_projection_decodes_current_revisions_and_preserves_full_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'case.db'

            class Case:
                @contextmanager
                def connect(self):
                    db = sqlite3.connect(path)
                    db.row_factory = sqlite3.Row
                    try:
                        yield db
                    finally:
                        db.close()

            case = Case()
            expected = {}
            with case.connect() as db:
                db.execute('CREATE TABLE events (kind TEXT, data TEXT)')
                for revision in range(100):
                    for note_index in range(12):
                        note = {'note_id': f'synthetic-{note_index}', 'revision': revision,
                                'body': f'revision {revision}: ' + 'historical observation ' * 128,
                                'evidence_ids': [f'fixture-source-{revision}']}
                        db.execute('INSERT INTO events VALUES (?,?)', ('note', json.dumps(note)))
                        expected[note['note_id']] = note
                db.execute('INSERT INTO events VALUES (?,?)', ('tool_result', '{"note_id":"unrelated"}'))
                db.commit()
                before = list(db.execute('SELECT kind,data FROM events ORDER BY rowid'))
            digest = lambda rows: hashlib.sha256(json.dumps([tuple(r) for r in rows]).encode()).hexdigest()
            with patch('forsic_plugin.notes.json.loads', wraps=json.loads) as decode:
                result = current_notes(case)
            self.assertEqual(result, list(reversed(list(expected.values()))))
            self.assertEqual(decode.call_count, len(expected))
            with case.connect() as db:
                after = list(db.execute('SELECT kind,data FROM events ORDER BY rowid'))
                # Exact revision retrieval remains possible after projection.
                first = db.execute("SELECT data FROM events WHERE kind='note' "
                                   "AND json_extract(data,'$.note_id')=? ORDER BY rowid LIMIT 1",
                                   ('synthetic-0',)).fetchone()
            self.assertEqual(digest(before), digest(after))
            self.assertEqual(json.loads(first['data'])['revision'], 0)
            self.assertEqual(len(after), 1201)


if __name__ == '__main__':
    unittest.main()
