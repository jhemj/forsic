"""Current report projection preserves full-ledger state and canonical hash bytes."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.evidence import Case
from forsic_plugin.report_driven import host


class ReportProjectionMemoryTests(unittest.TestCase):
    def test_bounded_projection_matches_full_events_and_buffered_canonical_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / 'evidence'
            evidence.mkdir()
            manifest = root / 'case.json'
            manifest.write_text(json.dumps(dict(case_id='SYN-memory', synthetic=True,
                evidence_root=str(evidence), output_root=str(root / 'results'),
                scope='Synthetic retained results', question='What was observed?')))
            case = Case(manifest)
            case.record('goal_started', {'details': 'historical goal'}, session='synthetic-session')
            stamp = '2026-01-02T03:04:05+00:00'
            source_ids = []
            for index in range(20):
                started = case.record('tool_start', {'tool': 'forsic_read',
                    'arguments': {'path': f'synthetic-{index}', 'reason': 'Retain exact acquisition reason'},
                    'mission_context': {'mission_id': 'synthetic-unmatched'}})
                source_ids.append(case.record('tool_result', {'tool': 'forsic_read',
                    'started_id': started, 'path': f'synthetic-{index}',
                    'lines': [{'line': 1, 'text': stamp + ' observed ' + '가공' * 2048}],
                    'times': {'mtime': 1767323045}, 'line_start': 1, 'line_end': 1,
                    'complete_file': False, 'coverage_complete': False}))
                for kind in ('pre_api_request', 'post_api_request'):
                    case.record(kind, {'unused_body': 'telemetry' * 1024}, session='synthetic-session')
            case.record('note', dict(note_id='synthetic-note', revision=1, question='What was observed?',
                answer='The source records an observation; later completion remains unknown.',
                evidence_ids=source_ids[:1], gaps=['Completion not observed'],
                timeline=[dict(description='Source timestamp', evidence_ids=source_ids[:1],
                    time_source=dict(evidence_id=source_ids[0], field='log',
                                     pointer='/lines/0/text', literal=stamp, byte_start=0)),
                          dict(description='File timestamp', evidence_ids=source_ids[:1],
                    time_source=dict(evidence_id=source_ids[0], field='mtime'))]))
            case.record('future_kind', {'preserved': 'Unknown future bodies stay available'})
            full_ledger = host.ledger(case)
            full_copy = deepcopy(full_ledger)
            before_bytes = case.db.read_bytes()
            optimized = host.current(case)
            # Recreate the original full-body reader and whole-buffer canonical
            # JSON encoder. Compare every state field, including both hashes.
            original_iterencode = json.JSONEncoder.iterencode

            def buffered(encoder, value, _one_shot=False):
                return iter([''.join(original_iterencode(encoder, value, _one_shot=True))])

            with patch.object(host, 'ledger', side_effect=lambda *_args, **_kwargs: full_ledger), \
                    patch.object(json.JSONEncoder, 'iterencode', buffered):
                baseline = host.current(case)
            self.assertEqual(optimized, baseline)
            self.assertEqual(full_ledger, full_copy)
            self.assertEqual(host.ledger(case), full_copy)
            self.assertEqual(case.db.read_bytes(), before_bytes)
            self.assertEqual([r['id'] for r in optimized['sources']], source_ids)
            self.assertEqual(optimized['meta']['ledger_cutoff'], full_ledger[-2]['id'])
            self.assertEqual(len(optimized['timeline']), 2)
            projection = host.ledger(case, metadata_only_kinds={'pre_api_request'})
            self.assertTrue(all(e['data'] == {} for e in projection if e['kind'] == 'pre_api_request'))
            self.assertTrue(all(e['data']['arguments']['reason'] == 'Retain exact acquisition reason'
                                for e in projection if e['kind'] == 'tool_start'))
            self.assertEqual(projection[-1]['data'], full_ledger[-1]['data'])

    def test_omitted_body_still_surfaces_malformed_json(self):
        import sqlite3
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'case.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE events (kind TEXT, data TEXT)')
                db.execute('INSERT INTO events VALUES (?,?)', ('pre_api_request', '{broken'))
            with self.assertRaises(json.JSONDecodeError):
                host.ledger(SimpleNamespace(db=path), metadata_only_kinds={'pre_api_request'})


if __name__ == '__main__':
    unittest.main()
