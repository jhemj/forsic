import hashlib
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sqlite3

from forsic_plugin import evidence as module


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        from test_environment_support import offline_environment
        offline_environment(self)
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
        from test_execution_contract import Process
        (self.root/'disk.e01').write_bytes(b'fixture')
        with patch('forsic_plugin.image_process.subprocess.Popen', return_value=Process(b'{"volumes":[]}')) as runner, \
             patch('forsic_plugin.image_process._cleanup', return_value={'confirmed':True}) as cleanup:
            result = self.call('image_files', {'path': 'disk.e01', 'action': 'volumes'})
        runner.assert_called_once()
        cleanup.assert_called_once()
        command = runner.call_args.args[0]
        self.assertNotIn('--rm', command)
        self.assertIn('--read-only', command)
        self.assertEqual(command[command.index('--network')+1], 'none')
        self.assertIn('no-new-privileges', command)
        self.assertEqual(command[command.index('--cap-drop')+1], 'ALL')
        self.assertIn('--user', command)
        self.assertIn('--memory', command)
        self.assertIn('--pids-limit', command)
        self.assertTrue(any('dst=/evidence,readonly' in arg for arg in command))
        self.assertTrue(any('dst=/reader.py,readonly' in arg for arg in command))
        self.assertEqual(result['volumes'], [])

    def test_image_parser_mount_csv_preserves_gvfs_and_option_like_literal_path(self):
        renamed=self.base/'smb-share:server=192.0.2.7,share=synthetic"readonly=false'
        self.root.rename(renamed);self.root=renamed
        self.manifest.write_text(json.dumps({'evidence_root':str(self.root),'output_root':str(self.base/'results')}))
        self.case=module.Case(self.manifest)
        (self.root/'disk.e01').write_bytes(b'synthetic fixture')
        with patch('forsic_plugin.image_process.run_parser',return_value={'volumes':[]}) as runner:
            result=self.call('image_files',{'path':'disk.e01','action':'volumes'})
        self.assertEqual(result['volumes'],[])
        command=runner.call_args.args[0]
        mounts=[next(csv.reader([command[index+1]],strict=True))
                for index,item in enumerate(command) if item=='--mount']
        self.assertEqual(mounts[0],['type=bind','src='+str(self.root),'dst=/evidence','readonly'])
        self.assertEqual(len(mounts),3)
        for mount,destination in zip(mounts,('/evidence','/reader.py','/literal_search.py')):
            self.assertEqual(len(mount),4)
            self.assertEqual(mount[-2:],['dst='+destination,'readonly'])

    def test_image_parser_rejects_mount_linebreak_before_launch(self):
        renamed=self.base/'evidence\nnewline'
        self.root.rename(renamed);self.root=renamed
        self.manifest.write_text(json.dumps({'evidence_root':str(self.root),'output_root':str(self.base/'results')}))
        self.case=module.Case(self.manifest)
        (self.root/'disk.e01').write_bytes(b'synthetic fixture')
        with patch('forsic_plugin.image_process.run_parser') as runner:
            result=self.call('image_files',{'path':'disk.e01','action':'volumes'})
        self.assertIn('NUL or newlines',result['error'])
        runner.assert_not_called()

    def test_image_timeout_cleans_only_its_container(self):
        from test_execution_contract import Process
        (self.root/'disk.e01').write_bytes(b'fixture')
        process = Process(error=subprocess.TimeoutExpired('docker', 300))
        with patch('forsic_plugin.image_process.subprocess.Popen', return_value=process) as runner, \
             patch('forsic_plugin.image_process.time.monotonic', side_effect=[0, 0, 301]), \
             patch('forsic_plugin.image_process.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'', b'')) as cleanup:
            result = self.call('image_files', {'path': 'disk.e01', 'action': 'volumes'})
        command = runner.call_args.args[0]
        name = command[command.index('--name')+1]
        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0], ['docker','rm','-f',name])
        self.assertEqual(result['error_type'], 'TimeoutExpired')
        self.assertTrue(result['execution']['termination_confirmed'])


if __name__ == '__main__':
    unittest.main()
