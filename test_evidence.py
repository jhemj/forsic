import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sqlite3

source = Path(__file__).parent / 'forsic_plugin/evidence.py'
if not source.exists():
    source = Path(__file__).parent / 'plugin/evidence.py'
spec = importlib.util.spec_from_file_location('evidence', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'evidence'
        self.root.mkdir()
        (self.root / 'log.txt').write_text('started\nexit=1\napproved\n')
        self.manifest = self.base / 'case.json'
        self.manifest.write_text(json.dumps({'evidence_root': str(self.root), 'output_root': str(self.base/'results')}))
        self.case = module.Case(self.manifest)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, name, args):
        return json.loads(self.case.invoke('forsic_' + name, args))

    def test_database_scope_commits_and_closes(self):
        with self.case.connect() as db:
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)", ('commit', 1, 'test', '', '{}'))
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')
        self.assertEqual(self.case.event('commit')['kind'], 'test')

    def test_database_scope_rolls_back_and_closes_on_failure(self):
        with self.assertRaisesRegex(ValueError, 'fixture failure'):
            with self.case.connect() as db:
                db.execute("INSERT INTO events VALUES (?,?,?,?,?)", ('rollback', 1, 'test', '', '{}'))
                raise ValueError('fixture failure')
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')
        self.assertIsNone(self.case.event('rollback'))

    def test_read_pagination_and_citation(self):
        result = self.call('read', {'path': 'log.txt', 'line_count': 2})
        self.assertEqual(result['next_line'], 3)
        self.assertFalse(result['complete_file'])
        self.assertEqual(self.case.event(result['evidence_id'])['data']['lines'][1]['text'], 'exit=1')
        final = self.call('read', {'path': 'log.txt', 'start_line': 3})
        self.assertIsNone(final['next_line'])
        self.assertEqual(final['lines'][0]['line'], 3)

    def test_traversal_and_symlink_escape(self):
        self.assertIn('error', self.call('read', {'path': '../case.json'}))
        (self.root/'escape').symlink_to(self.manifest)
        self.assertIn('error', self.call('read', {'path': 'escape'}))

    def test_hash_does_not_mutate(self):
        before = (self.root/'log.txt').read_bytes()
        result = self.call('hash', {'path': 'log.txt'})
        self.assertEqual(result['sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual((self.root/'log.txt').read_bytes(), before)

    def test_search_coverage(self):
        (self.root/'binary').write_bytes(b'\x00something')
        result = self.call('search', {'text': 'not here'})
        self.assertEqual(result['matches'], [])
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(result['files_skipped'], 1)

    def test_binary_and_long_record_not_truncated(self):
        (self.root/'binary').write_bytes(b'\x00abc')
        self.assertIn('error', self.call('read', {'path': 'binary'}))
        (self.root/'long').write_text('a'*18000)
        self.assertIn('error', self.call('read', {'path': 'long'}))

    def test_missing_path_records_failure_and_retry_guidance(self):
        result = self.call('read', {'path': 'missing'})
        self.assertIn('error', result)
        self.assertIn('Do not repeat', result['next_step'])
        self.assertEqual(self.case.event(result['evidence_id'])['kind'], 'tool_result')

    def test_report_requires_existing_successful_source(self):
        self.assertIn('error', self.call('report', {'markdown': 'wrong', 'evidence_ids': ['invented']}))
        result = self.call('read', {'path': 'log.txt'})
        report = self.call('report', {'markdown': '# Result\nThe process failed.', 'evidence_ids': [result['evidence_id']]})
        self.assertTrue((self.case.output/report['report']).is_file())
        self.assertIn(result['evidence_id'], (self.case.output/report['report']).read_text())

    def test_image_parser_is_readonly_and_isolated(self):
        (self.root/'disk.e01').write_bytes(b'fixture')
        with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{"volumes":[]}', b'')) as runner:
            result = self.call('image_files', {'path': 'disk.e01', 'action': 'volumes'})
        command = runner.call_args.args[0]
        self.assertIn('--read-only', command)
        self.assertEqual(command[command.index('--network')+1], 'none')
        self.assertIn('no-new-privileges', command)
        self.assertTrue(any('dst=/evidence,readonly' in arg for arg in command))
        self.assertEqual(result['volumes'], [])

    def test_image_timeout_cleans_only_its_container(self):
        (self.root/'disk.e01').write_bytes(b'fixture')
        with patch.object(module.subprocess, 'run', side_effect=[subprocess.TimeoutExpired('docker', 300), subprocess.CompletedProcess([], 0)]) as runner:
            result = self.call('image_files', {'path': 'disk.e01', 'action': 'volumes'})
        command = runner.call_args_list[0].args[0]
        name = command[command.index('--name')+1]
        self.assertEqual(runner.call_args_list[1].args[0], ['docker','rm','-f',name])
        self.assertEqual(result['error_type'], 'TimeoutExpired')


if __name__ == '__main__':
    unittest.main()
