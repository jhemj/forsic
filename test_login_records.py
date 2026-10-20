"""Offline synthetic Linux login record regressions; no model or incident use."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).parent / 'forsic_plugin' / 'login_records.py'
spec = importlib.util.spec_from_file_location('login_records', MODULE)
login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(login)


def fixture(kind=7, seconds=1704067200, microseconds=123456, endian='<'):
    # Independent offsets from glibc bits/utmp.h, rather than parser ENTRY.pack.
    raw = bytearray(384)
    struct.pack_into(endian + 'h', raw, 0, kind)
    struct.pack_into(endian + 'i', raw, 4, 1234)
    raw[8:14] = b'pts/42'
    raw[40:44] = b'abcd'
    raw[44:49] = b'alice'
    raw[76:85] = b'localhost'
    struct.pack_into(endian + 'hhiii', raw, 332, 2, 3, 99, seconds, microseconds)
    raw[348:352] = b'\xc0\x00\x02\x01'
    return bytes(raw)


class LoginRecordsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'selected-btmp'

    def tearDown(self):
        self.temp.cleanup()

    def inspect(self, raw, **kwargs):
        self.path.write_bytes(raw)
        output = []
        code = login.inspect_file(self.path, format_name=login.FORMAT,
                                  emit=output.append, **kwargs)
        self.assertEqual(self.path.read_bytes(), raw)
        return code, [json.loads(line) for line in output]

    def test_user7_dead8_constants_offsets_and_raw_microseconds(self):
        raw = fixture(7) + fixture(8, microseconds=654321)
        code, rows = self.inspect(raw, origin_locator='image:partition-2:/var/log/btmp')
        self.assertEqual(code, 0)
        self.assertEqual(rows[0]['record_size'], 384)
        self.assertEqual(rows[0]['endianness'], 'little')
        self.assertEqual(rows[0]['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(rows[0]['origin_locator_asserted_by_caller'], 'image:partition-2:/var/log/btmp')
        first, second = rows[1:3]
        self.assertEqual((first['ut_type_raw'], first['ut_type_name']), (7, 'USER_PROCESS'))
        self.assertEqual((second['ut_type_raw'], second['ut_type_name']), (8, 'DEAD_PROCESS'))
        self.assertEqual(first['ut_pid'], 1234)
        self.assertEqual((first['ut_line'], first['ut_id'], first['ut_user'], first['ut_host']),
                         ('pts/42', 'abcd', 'alice', 'localhost'))
        self.assertEqual(first['ut_exit'], {'termination': 2, 'exit': 3})
        self.assertEqual(first['ut_session'], 99)
        self.assertEqual(first['ut_tv_raw'], {'tv_sec': 1704067200, 'tv_usec': 123456})
        self.assertEqual(first['timestamp_utc'], '2024-01-01T00:00:00.123456+00:00')
        self.assertEqual(second['timestamp_utc'], '2024-01-01T00:00:00.654321+00:00')
        for index, row in enumerate((first, second)):
            locator = row['locator']
            self.assertEqual(locator['path'], str(self.path))
            self.assertEqual(locator['byte_start'], index * 384)
            self.assertEqual(locator['byte_end'], (index + 1) * 384)
            self.assertEqual(bytes.fromhex(row['raw_record_hex']), raw[locator['byte_start']:locator['byte_end']])
        self.assertEqual(rows[-1]['status'], 'complete')
        self.assertIn('selected file only', rows[-1]['coverage'])

    def test_every_standard_type_and_unknown_value_preserved(self):
        expected = ['EMPTY', 'RUN_LVL', 'BOOT_TIME', 'NEW_TIME', 'OLD_TIME',
                    'INIT_PROCESS', 'LOGIN_PROCESS', 'USER_PROCESS', 'DEAD_PROCESS', 'ACCOUNTING']
        code, rows = self.inspect(b''.join(fixture(kind) for kind in range(10)) + fixture(123))
        self.assertEqual(code, 2)
        self.assertEqual([row['ut_type_name'] for row in rows[1:11]], expected)
        self.assertEqual(rows[-2]['ut_type_raw'], 123)
        self.assertEqual(rows[-2]['ut_type_name'], 'UNKNOWN')
        self.assertTrue(rows[-2]['warnings'])
        self.assertEqual(rows[-1]['records_with_warnings'], 1)

    def test_partial_tail_never_disappears_or_becomes_a_record(self):
        code, rows = self.inspect(fixture() + b'\x07\x00partial-tail')
        self.assertEqual(code, 2)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]['status'], 'truncated')
        self.assertEqual(rows[-1]['truncated_tail'],
                         {'byte_start': 384, 'byte_end': 398, 'length': 14,
                          'raw_hex': b'\x07\x00partial-tail'.hex()})
        # The original 38-byte misinterpretation cannot silently return zero rows.
        code, rows = self.inspect(fixture()[:38])
        self.assertEqual(code, 2)
        self.assertEqual(rows[-1]['records_emitted'], 0)
        self.assertEqual(rows[-1]['truncated_tail']['length'], 38)

    def test_invalid_microseconds_and_negative_seconds_remain_raw(self):
        code, rows = self.inspect(fixture(seconds=-1, microseconds=999999))
        self.assertEqual(code, 0)
        self.assertEqual(rows[1]['timestamp_utc'], '1969-12-31T23:59:59.999999+00:00')
        for usec in (-1, 1000000):
            code, rows = self.inspect(fixture(microseconds=usec))
            self.assertEqual(code, 2)
            self.assertEqual(rows[1]['ut_tv_raw']['tv_usec'], usec)
            self.assertIsNone(rows[1]['timestamp_utc'])

    def test_limit_empty_and_nul_bytes_are_explicit(self):
        code, rows = self.inspect(fixture() * 3 + b'x', limit=1)
        self.assertEqual(code, 2)
        self.assertTrue(rows[-1]['limited'])
        self.assertEqual(rows[-1]['unparsed_complete_records'], 2)
        code, rows = self.inspect(fixture() * 3, limit=1)
        self.assertEqual(code, 3)
        self.assertEqual(rows[-1]['status'], 'limited')
        code, rows = self.inspect(b'')
        self.assertEqual(code, 0)
        self.assertEqual(rows[-1]['status'], 'empty')
        code, rows = self.inspect(b'\0' * 384)
        self.assertEqual(rows[1]['ut_type_name'], 'EMPTY')

    def test_unsupported_format_compression_and_big_endian_not_silent(self):
        self.path.write_bytes(fixture())
        for format_name in ('auto', 'linux-glibc-x86-be-384', '38'):
            with self.assertRaisesRegex(ValueError, 'Unsupported format'):
                login.inspect_file(self.path, format_name=format_name)
        for prefix in login.COMPRESSED_MAGIC:
            with self.assertRaisesRegex(ValueError, 'Compressed/archive'):
                self.inspect(prefix + b'\0' * 400)
        # If a caller asserts the wrong layout, recognizable bad fields warn.
        # Headerless bytes cannot prove ABI; the source descriptor says this.
        code, rows = self.inspect(fixture(endian='>'))
        self.assertEqual(code, 2)
        self.assertEqual(rows[1]['ut_type_raw'], 1792)
        self.assertEqual(rows[1]['ut_type_name'], 'UNKNOWN')

    def test_non_utf8_raw_strings_remain_recoverable(self):
        raw = bytearray(fixture())
        raw[44:49] = b'a\xffb\x00c'
        code, rows = self.inspect(bytes(raw))
        self.assertEqual(code, 0)
        self.assertEqual(rows[1]['ut_user'], 'a\ufffdb')
        self.assertEqual(bytes.fromhex(rows[1]['raw_record_hex'])[44:49], b'a\xffb\x00c')

    def test_fifo_and_oversized_file_fail_without_parsing(self):
        os.mkfifo(self.path)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            login.inspect_file(self.path, format_name=login.FORMAT)
        self.path.unlink()
        with self.path.open('wb') as stream:
            stream.truncate(login.MAX_BYTES + 1)
        with self.assertRaisesRegex(ValueError, 'byte limit'):
            login.inspect_file(self.path, format_name=login.FORMAT)

    def test_cli_requires_format_and_reports_partial_exit(self):
        self.path.write_bytes(fixture(8) + b'x')
        missing = subprocess.run([sys.executable, str(MODULE), str(self.path)], capture_output=True)
        self.assertEqual(missing.returncode, 2)
        result = subprocess.run([sys.executable, str(MODULE), str(self.path), '--format', login.FORMAT],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, '')
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])['status'], 'truncated')

    def test_documented_consumer_keeps_parser_status_summary_and_stderr(self):
        reference = MODULE.parent / 'skills/inspect-artifacts/references/linux.md'
        recipe = reference.read_text().split('```sh\n', 1)[1].split('```', 1)[0]
        recipe = recipe.replace('python /opt/forsic/login_records.py',
                                shlex.quote(sys.executable) + ' ' + shlex.quote(str(MODULE)))
        work = Path(self.temp.name) / 'work with spaces'
        work.mkdir()
        recipe = recipe.replace('/work/', str(work) + '/')
        source = work / 'selected-wtmp'
        for raw, code, status, count in ((fixture(), 0, 'complete', 1),
                                         (b'', 0, 'empty', 0),
                                         (fixture() + b'x', 2, 'truncated', 1),
                                         (None, 1, None, None)):
            with self.subTest(status=status):
                if raw is None:
                    source.unlink()
                else:
                    source.write_bytes(raw)
                result = subprocess.run(['/bin/bash', '-e', '-o', 'pipefail', '-c', recipe],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report['exit_code'], code)
                self.assertEqual(report['records'], count)
                if status is None:
                    self.assertIsNone(report['summary'])
                    error = json.loads((work / 'selected-wtmp.stderr').read_text())
                    self.assertEqual(error['kind'], 'error')
                else:
                    self.assertEqual(report['summary']['status'], status)
                    self.assertEqual(source.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
