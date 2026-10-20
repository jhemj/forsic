"""Offline synthetic tests; no evidence, model calls, container or network use."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

MODULE = Path(__file__).parent / 'forsic_plugin' / 'structured_query.py'
_spec = importlib.util.spec_from_file_location('structured_query', MODULE)
sq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sq)
try:
    import duckdb
except ImportError:
    duckdb = None


@unittest.skipUnless(duckdb and duckdb.__version__ == sq.DUCKDB_VERSION,
                     'requires the verified duckdb==' + sq.DUCKDB_VERSION)
class StructuredQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'events.jsonl'
        self.data.write_bytes(b'{"timestamp":"2026-10-19T01:02:03.000001+09:00","user":"alice","n":1}\r\n'
                              b'{"user":"bob","n":2,"extra":true}\n')
        self.query = self.root / 'query.sql'
        self.query.write_text('SELECT user, _forsic_row FROM events ORDER BY _forsic_row')
        self.output = self.root / 'result'

    def tearDown(self):
        self.temp.cleanup()

    def run_query(self, sql=None, **kwargs):
        if sql is not None:
            self.query.write_text(sql)
        return sq.run_query([('events', self.data)], self.query, self.output,
                            _root=self.root, **kwargs)

    def receipt(self, prefix=None):
        return json.loads(Path(str(prefix or self.output) + '.receipt.json').read_text())

    def rows(self):
        return [json.loads(row) for row in Path(str(self.output) + '.rows.jsonl').read_text().splitlines()]

    def test_jsonl_provenance_timestamp_and_dynamic_schema(self):
        original = self.data.read_bytes()
        self.run_query('SELECT * FROM events ORDER BY _forsic_row', timestamps={'events':'timestamp'})
        receipt = self.receipt()
        source = receipt['sources'][0]
        self.assertEqual(source['sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(source['columns'], ['timestamp','user','n','extra'])
        self.assertEqual(source['missing_timestamp_rows'], 1)
        for row, raw in zip(self.rows(), original.splitlines(keepends=True)):
            self.assertEqual(original[row[3]:row[4]], raw)
            self.assertEqual(row[0], source['sha256'])
        self.assertEqual(self.rows()[0][5], '2026-10-19T01:02:03.000001+09:00')
        self.assertIsNone(self.rows()[1][6])
        self.assertEqual(self.rows()[1][-1], 'true')
        self.assertEqual(self.data.read_bytes(), original)

    def test_multiline_csv_offsets_preserve_crlf_and_text(self):
        self.data = self.root / 'events.csv'
        raw = b'\xef\xbb\xbftimestamp,user,n\r\n"2026-10-19 01:02:03+0900","a\r\nb",001\r\n,,002\r\n'
        self.data.write_bytes(raw)
        self.run_query('SELECT * FROM events ORDER BY _forsic_row', timestamps={'events':'timestamp'})
        rows = self.rows()
        self.assertEqual(raw[rows[0][3]:rows[0][4]], b'"2026-10-19 01:02:03+0900","a\r\nb",001\r\n')
        self.assertEqual(rows[0][5], '2026-10-19 01:02:03+0900')
        self.assertEqual(rows[0][-2:], ['a\r\nb','001'])
        self.assertEqual(rows[1][2], 2)
        self.assertEqual(rows[1][5], '')

    def test_same_query_and_inputs_make_identical_receipt(self):
        self.run_query()
        first = Path(str(self.output) + '.receipt.json').read_bytes()
        second = self.root / 'second'
        sq.run_query([('events',self.data)], self.query, second, _root=self.root)
        self.assertEqual(first, Path(str(second) + '.receipt.json').read_bytes())
        receipt = self.receipt()
        digest = receipt.pop('receipt_sha256')
        self.assertEqual(digest, hashlib.sha256(sq._json(receipt).encode()).hexdigest())
        self.assertEqual(receipt['result_sha256'], hashlib.sha256(Path(str(self.output)+'.rows.jsonl').read_bytes()).hexdigest())

    def test_aggregates_and_join_of_two_imported_tables(self):
        other = self.root / 'accounts.csv'
        other.write_text('user,role\nalice,admin\nbob,staff\n')
        self.query.write_text('WITH t AS (SELECT user, CAST(n AS INTEGER) AS n FROM events) '
                              'SELECT role, sum(n) FROM t JOIN accounts USING(user) GROUP BY role ORDER BY role')
        sq.run_query([('events',self.data),('accounts',other)], self.query, self.output, _root=self.root)
        self.assertEqual(self.rows(), [['admin',1], ['staff',2]])

    def test_mutation_and_multistatement_rejected(self):
        for sql in ['CREATE TABLE evil(x INT)', 'DELETE FROM events', 'INSERT INTO events VALUES (1)',
                    "COPY (SELECT 1) TO '/tmp/escape'", "INSTALL httpfs", "LOAD httpfs",
                    "ATTACH '/tmp/escape' AS evil", 'SET threads=8', 'PRAGMA threads=8',
                    'SELECT 1; SELECT 2', 'EXPLAIN ANALYZE SELECT 1']:
            with self.subTest(sql=sql), self.assertRaises(sq.QueryError) as caught:
                self.run_query(sql)
            self.assertEqual(caught.exception.code, 'sql_rejected')
        self.assertFalse(Path(str(self.output)+'.rows.jsonl').exists())

    def test_external_local_network_and_dynamic_query_escape_rejected(self):
        for sql in ["SELECT * FROM read_text('/etc/passwd')", "SELECT * FROM read_csv('https://example.invalid/log.csv')",
                    "SELECT * FROM read_json('s3://example.invalid/log.json')", "SELECT * FROM glob('/work/*')",
                    "SELECT * FROM query('CREATE TABLE evil(x INT)')",
                    "SELECT * FROM query('COPY (SELECT 1) TO ''/tmp/escape''')",
                    "SELECT * FROM sqlite_scan('/tmp/escape','t')"]:
            with self.subTest(sql=sql), self.assertRaises(sq.QueryError) as caught:
                self.run_query(sql)
            self.assertEqual(caught.exception.code, 'query_failed')
        self.assertFalse(Path(str(self.output)+'.rows.jsonl').exists())

    def test_security_config_is_locked_and_enforced(self):
        self.run_query("SELECT current_setting('enable_external_access'), current_setting('autoload_known_extensions'), "
                       "current_setting('autoinstall_known_extensions'), current_setting('lock_configuration'), "
                       "current_setting('threads'), current_setting('max_temp_directory_size'), current_setting('TimeZone')")
        self.assertEqual(self.rows()[0], [False,False,False,True,1,'0 bytes','UTC'])

    def test_source_and_query_and_output_path_escape(self):
        outside = self.root.parent / (self.root.name + '-outside.jsonl')
        outside.write_text('{"x":1}\n')
        try:
            symlink = self.root/'symlink.jsonl'
            symlink.symlink_to(outside)
            symlink_dir = self.root/'linked'
            symlink_dir.symlink_to(self.root.parent, target_is_directory=True)
            paths = [outside, symlink, symlink_dir/outside.name,
                     self.root/'..'/outside.name, 'https://example.invalid/log.jsonl']
            for path in paths:
                with self.subTest(path=str(path)), self.assertRaises((sq.QueryError,OSError)):
                    sq.run_query([('events',path)],self.query,self.output,_root=self.root)
            with self.assertRaises(sq.QueryError):
                sq.run_query([('events',self.data)],outside,self.output,_root=self.root)
            with self.assertRaises(sq.QueryError):
                sq.run_query([('events',self.data)],self.query,outside,_root=self.root)
        finally:
            outside.unlink()

    def test_fifo_rejected_without_blocking(self):
        import os
        fifo = self.root/'fifo.jsonl'
        os.mkfifo(fifo)
        with self.assertRaises(sq.QueryError):
            sq.run_query([('events',fifo)],self.query,self.output,_root=self.root)

    def test_existing_output_or_symlink_never_overwritten(self):
        row_path = Path(str(self.output)+'.rows.jsonl')
        row_path.symlink_to(self.data)
        before = self.data.read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_query()
        self.assertTrue(row_path.is_symlink())
        self.assertEqual(self.data.read_bytes(),before)
        row_path.unlink()
        receipt_path = Path(str(self.output)+'.receipt.json')
        receipt_path.write_text('preserve')
        with self.assertRaises(FileExistsError):
            self.run_query()
        self.assertFalse(row_path.exists())
        self.assertEqual(receipt_path.read_text(),'preserve')

    def test_invalid_rows_counted_only_with_explicit_skip(self):
        self.data.write_bytes(b'{"user":"a"}\nno json\n\xff\n[]\n{"x":1,"x":2}\n{"x":NaN}\n{"user":"b"}\n')
        with self.assertRaises(sq.QueryError) as caught:
            self.run_query()
        self.assertEqual(caught.exception.code, 'invalid_record')
        self.assertEqual(caught.exception.details['row'],2)
        self.assertFalse(Path(str(self.output)+'.rows.jsonl').exists())
        summary = self.run_query(skip_invalid=True)
        self.assertEqual(summary['accepted_rows'],2)
        self.assertEqual(summary['rejected_rows'],5)
        self.assertEqual(self.rows(),[['a',1],['b',7]])
        self.assertEqual(self.receipt()['sources'][0]['rejection_samples'][1]['byte_start'],21)

    def test_bad_csv_width_can_skip_but_encoding_or_syntax_fails_closed(self):
        self.data = self.root/'events.csv'
        self.data.write_bytes(b'user,n\na,1\nb,2,3\nc,4\n')
        self.assertEqual(self.run_query(skip_invalid=True)['rejected_rows'],1)
        for raw in [b'user,n\n\xff,1\n',b'user,n\n"unterminated,1\n']:
            self.data.write_bytes(raw)
            with self.assertRaises(sq.QueryError) as caught:
                sq.run_query([('events',self.data)],self.query,self.root/'bad',skip_invalid=True,_root=self.root)
            self.assertEqual(caught.exception.code,'csv_parse_error')

    def test_schema_and_missing_timestamp_failures(self):
        for raw in [b'{"a":1,"A":2}\n', b'{"_forsic_row":1}\n', b'{"":1}\n']:
            self.data.write_bytes(raw)
            with self.assertRaises(sq.QueryError) as caught:
                self.run_query()
            self.assertEqual(caught.exception.code,'schema_rejected')
        self.data.write_bytes(b'{"user":"a"}\n')
        with self.assertRaises(sq.QueryError) as caught:
            self.run_query(timestamps={'events':'missing'})
        self.assertEqual(caught.exception.code,'timestamp_missing')

    def test_limit_and_bounded_output(self):
        summary = self.run_query(max_rows=1)
        self.assertTrue(summary['result_truncated'])
        self.assertEqual(len(self.rows()),1)
        self.assertLess(Path(str(self.output)+'.rows.jsonl').stat().st_size,sq.MAX_OUTPUT_BYTES)
        self.output = self.root/'small'
        with patch.object(sq,'MAX_OUTPUT_BYTES',3):
            summary = self.run_query()
        self.assertTrue(summary['result_truncated'])
        self.assertEqual(summary['returned_rows'],0)
        self.data.write_bytes(b'x'*(sq.MAX_RECORD_BYTES+1))
        with self.assertRaises(sq.QueryError) as caught:
            self.run_query()
        self.assertEqual(caught.exception.code,'record_limit')

    def test_source_change_during_ingestion_rejected(self):
        original = sq._records
        def changed(stream, fmt, deadline):
            for row in original(stream,fmt,deadline):
                if row[-1] == 'source_complete':
                    self.data.write_bytes(b'{"user":"replacement"}\n')
                yield row
        with patch.object(sq,'_records',changed), self.assertRaises(sq.QueryError) as caught:
            self.run_query()
        self.assertEqual(caught.exception.code,'source_changed')

    def test_json_number_timestamp_and_nested_precision_are_lossless(self):
        self.data.write_bytes(b'{"timestamp":1.00000000000000001e+09,"nested":{"n":0.12345678901234567890}}\n')
        self.run_query('SELECT timestamp, _forsic_raw_timestamp, nested FROM events', timestamps={'events':'timestamp'})
        self.assertEqual(self.rows()[0], ['1.00000000000000001e+09','1.00000000000000001e+09',
                                         '{"n":0.12345678901234567890}'])

    def test_rejection_samples_and_total_input_budget_are_bounded(self):
        self.data.write_bytes(b'invalid\n'*20)
        result = self.run_query('SELECT count(*) FROM events',skip_invalid=True)
        self.assertEqual(result['rejected_rows'],20)
        source = self.receipt()['sources'][0]
        self.assertEqual(len(source['rejection_samples']),10)
        self.assertTrue(source['rejection_samples_truncated'])
        with patch.object(sq,'MAX_TOTAL_BYTES',2), self.assertRaises(sq.QueryError) as caught:
            self.run_query()
        self.assertEqual(caught.exception.code,'input_rejected')

    def test_bounded_native_binder_error_supports_typo_recovery(self):
        with self.assertRaises(sq.QueryError) as caught:
            self.run_query('SELECT misspelled FROM events')
        details = caught.exception.details
        self.assertEqual(details['engine_error_type'],'BinderException')
        self.assertIn('Referenced column "misspelled"',details['engine_message'])
        self.assertFalse(details['engine_message_truncated'])
        self.assertLessEqual(len(sq._json(details['engine_message'])),1000)
        self.assertEqual(self.run_query('SELECT user FROM events ORDER BY user')['returned_rows'],2)
        self.output = self.root/'long-error'
        with self.assertRaises(sq.QueryError) as caught:
            self.run_query('SELECT "' + 'missing'*1000 + '" FROM events')
        self.assertTrue(caught.exception.details['engine_message_truncated'])
        self.assertLessEqual(len(sq._json(caught.exception.details['engine_message'])),1000)

    def test_native_query_timeout_interrupts_instead_of_completing(self):
        with self.assertRaises(sq.QueryError) as caught:
            self.run_query('SELECT sum(sin(i)) FROM range(1000000000000) t(i)',timeout_seconds=1)
        self.assertEqual(caught.exception.code,'timeout')
        self.assertFalse(Path(str(self.output)+'.rows.jsonl').exists())

    def test_cli_returns_only_summary_and_bounded_errors(self):
        real = sq.run_query
        stdout,stderr=io.StringIO(),io.StringIO()
        def with_root(*args,**kwargs):
            return real(*args,**kwargs,_root=self.root)
        args=['--source',f'events={self.data}','--sql-file',str(self.query),'--output',str(self.output)]
        with patch.object(sq,'run_query',with_root),redirect_stdout(stdout),redirect_stderr(stderr):
            self.assertEqual(sq.main(args),0)
            self.assertNotIn('alice',stdout.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())['returned_rows'],2)
            self.assertEqual(sq.main(args),2)
        self.assertLess(len(stderr.getvalue()),2048)
        self.assertEqual(json.loads(stderr.getvalue())['error'],'local_io_rejected')
        result=subprocess.run([__import__('sys').executable,str(MODULE),'--help'],capture_output=True,check=False)
        self.assertEqual(result.returncode,0)
        self.assertIn(b'--sql-file',result.stdout)


if __name__ == '__main__':
    unittest.main()
