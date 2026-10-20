"""Read one explicitly selected Linux/glibc x86 utmp/wtmp/btmp file.

Standard-library CLI for forsic_shell, not an agent tool. Layout and constants
are independently defined from glibc's bits/utmp.h (also bits/utmpx.h), verified
against local x86_64 headers. No Dissect implementation is copied here.
These headerless files cannot identify their own ABI or byte order.

stdout is JSONL: one kind=source, zero or more kind=record, then kind=summary.
Read each line with json.loads, not the whole stream with json.load. Check the
summary and exit status together; errors are JSON on stderr and stdout may be partial.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys

VERSION = '1'
FORMAT = 'linux-glibc-x86-le-384'
RECORD_SIZE = 384
MAX_BYTES = 256 * 1024 * 1024
TYPES = {0: 'EMPTY', 1: 'RUN_LVL', 2: 'BOOT_TIME', 3: 'NEW_TIME',
         4: 'OLD_TIME', 5: 'INIT_PROCESS', 6: 'LOGIN_PROCESS',
         7: 'USER_PROCESS', 8: 'DEAD_PROCESS', 9: 'ACCOUNTING'}
ENTRY = struct.Struct('<h2xi32s4s32s256shhiii16s20s')
COMPRESSED_MAGIC = (b'\x1f\x8b', b'BZh', b'\xfd7zXZ\x00', b'PK\x03\x04', b'\x28\xb5\x2f\xfd')


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)


def decode_record(raw, index, path, source_sha256):
    """Decode a complete record only; retain bytes and numeric fields verbatim."""
    (kind, pid, line, ident, user, host, termination, exit_code, session,
     seconds, microseconds, address, reserved) = ENTRY.unpack(raw)
    warnings = []
    if kind not in TYPES:
        warnings.append('unknown_ut_type: possible unsupported ABI, corruption, or extension')
    timestamp = None
    if 0 <= microseconds < 1000000:
        timestamp = (datetime(1970, 1, 1, tzinfo=timezone.utc)
                     + timedelta(seconds=seconds, microseconds=microseconds)).isoformat(timespec='microseconds')
    else:
        warnings.append('invalid_tv_usec: UTC rendering omitted; raw fields retained')
    def string(value):
        return value.split(b'\0', 1)[0].decode('utf-8', errors='replace')
    return {
        'kind': 'record', 'record_index': index,
        'source_sha256': source_sha256,
        'locator': {'path': str(path), 'byte_start': index * RECORD_SIZE,
                    'byte_end': (index + 1) * RECORD_SIZE},
        'ut_type_raw': kind, 'ut_type_name': TYPES.get(kind, 'UNKNOWN'),
        'ut_pid': pid, 'ut_line': string(line), 'ut_id': string(ident),
        'ut_user': string(user), 'ut_host': string(host),
        'ut_exit': {'termination': termination, 'exit': exit_code},
        'ut_session': session,
        'ut_tv_raw': {'tv_sec': seconds, 'tv_usec': microseconds},
        'timestamp_utc': timestamp,
        'ut_addr_v6_bytes_hex': address.hex(),
        'raw_record_hex': raw.hex(), 'warnings': warnings,
    }


def inspect_file(path, *, format_name, limit=10000, origin_locator=None, emit=print):
    """Emit JSONL records and a completeness summary; return CLI exit status.

    The caller asserts the source ABI. Size/type checks detect some incompatible
    inputs but cannot establish compatibility for arbitrary headerless bytes.
    """
    if format_name != FORMAT:
        raise ValueError(f'Unsupported format {format_name!r}; supported format: {FORMAT}')
    if limit < 1:
        raise ValueError('limit must be a positive record count')
    path = Path(path).absolute()
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Input must be a regular file')
        if before.st_size > MAX_BYTES:
            raise ValueError(f'Input exceeds the {MAX_BYTES}-byte limit; select a bounded source')
        prefix = stream.read(8)
        if any(prefix.startswith(magic) for magic in COMPRESSED_MAGIC):
            raise ValueError('Compressed/archive input unsupported; decompress a selected copy to /work and retain its origin/hash separately')
        stream.seek(0)
        digest = hashlib.sha256()
        hashed_bytes = 0
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hashed_bytes += len(block)
            if hashed_bytes > MAX_BYTES:
                raise ValueError('Input grew beyond byte limit while hashing')
            digest.update(block)
        if hashed_bytes != before.st_size:
            raise ValueError('Input changed while hashing')
        source_sha256 = digest.hexdigest()
        count, tail_bytes = divmod(before.st_size, RECORD_SIZE)
        emit(_json({
            'kind': 'source', 'schema': 'forsic.login-records.v1',
            'parser': 'forsic.login_records', 'parser_version': VERSION,
            'input_path': str(path), 'source_sha256': source_sha256,
            'origin_locator_asserted_by_caller': origin_locator,
            'format_asserted_by_caller': FORMAT, 'record_size': RECORD_SIZE,
            'endianness': 'little', 'integer_layout': 'type:int16,pid/session/sec/usec:int32',
            'timestamp_assumption': 'signed POSIX seconds and microseconds rendered in UTC; source host timezone and clock correctness unknown',
            'string_decoding': 'NUL-terminated UTF-8 with replacement; raw_record_hex retains every byte',
            'abi_detection': 'none; confirm Linux/glibc x86 compatibility from source metadata',
            'input_bytes': before.st_size, 'complete_records_available': count,
            'record_limit': limit,
        }))
        stream.seek(0)
        warnings_count = 0
        parsed = 0
        for index in range(min(count, limit)):
            raw = stream.read(RECORD_SIZE)
            if len(raw) != RECORD_SIZE:
                raise ValueError('Input changed or became truncated during parsing; emitted records are incomplete output')
            record = decode_record(raw, index, path, source_sha256)
            warnings_count += bool(record['warnings'])
            emit(_json(record))
            parsed += 1
        tail = None
        if tail_bytes:
            stream.seek(count * RECORD_SIZE)
            raw_tail = stream.read(tail_bytes)
            tail = {'byte_start': count * RECORD_SIZE, 'byte_end': before.st_size,
                    'length': tail_bytes, 'raw_hex': raw_tail.hex()}
        after = os.fstat(stream.fileno())
        fingerprint = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if fingerprint(before) != fingerprint(after):
            raise ValueError('Input changed during parsing; emitted records are incomplete output')
        limited = parsed < count
        status = ('truncated' if tail else 'record_warnings' if warnings_count
                  else 'limited' if limited else 'empty' if not count else 'complete')
        emit(_json({'kind': 'summary', 'status': status,
                    'records_emitted': parsed, 'complete_records_available': count,
                    'records_with_warnings': warnings_count, 'limited': limited,
                    'unparsed_complete_records': count - parsed, 'truncated_tail': tail,
                    'coverage': 'selected file only; no rotation discovery, authentication outcome inference, or session correlation'}))
        return 2 if tail or warnings_count else 3 if limited else 0


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    parser = Parser(description=__doc__, epilog=
        'Example: login_records.py /work/selected-wtmp --format linux-glibc-x86-le-384. '
        'Exit 0: complete/empty; 1: I/O or parse failure; 2: argument error or record/tail warnings '
        '(distinguish stderr error from stdout summary); 3: record limit. --help reads no evidence.')
    parser.add_argument('path', help='selected uncompressed regular file (read only)')
    parser.add_argument('--format', required=True, choices=[FORMAT],
                        help='explicit source ABI assertion; never inferred from filename')
    parser.add_argument('--limit', type=int, default=10000)
    parser.add_argument('--origin-locator', help='optional original file/image locator; caller assertion, not verified by this helper')
    try:
        args = parser.parse_args(argv)
    except ValueError as exc:
        print(_json({'kind': 'error', 'status': 'failed', 'error_code': 'argument_rejected',
                     'message': str(exc)}), file=sys.stderr)
        return 2
    try:
        return inspect_file(args.path, format_name=args.format, limit=args.limit,
                            origin_locator=args.origin_locator)
    except (OSError, ValueError) as exc:
        print(_json({'kind': 'error', 'status': 'failed', 'message': str(exc),
                     'partial_stdout_must_not_be_treated_as_complete': True}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
