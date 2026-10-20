"""Bounded offline CSV/JSONL queries, invoked inside forsic_shell's sandbox.

This is a CLI helper, not an agent tool. The outer network-none/read-only
container remains the security boundary for the native SQL engine.
"""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time

DUCKDB_VERSION = '1.5.6'
WORK_ROOT = Path('/work')
MAX_SOURCES = 8
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_RECORD_BYTES = 1024 * 1024
MAX_ROWS = 1000000
MAX_COLUMNS = 256
MAX_SQL_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_OUTPUT_ROWS = 1000
LIMITS = {'enable_external_access': 'false', 'memory_limit': '1GB', 'threads': '1',
          'max_temp_directory_size': '0B', 'temp_directory': '',
          'autoload_known_extensions': 'false', 'autoinstall_known_extensions': 'false',
          'allow_unsigned_extensions': 'false', 'allow_community_extensions': 'false',
          'python_enable_replacements': 'false', 'allow_persistent_secrets': 'false',
          'TimeZone': 'UTC'}
META = ['_forsic_source_sha256', '_forsic_source_path', '_forsic_row',
        '_forsic_byte_start', '_forsic_byte_end', '_forsic_raw_timestamp']


class QueryError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                      allow_nan=False)


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _parts(path, root):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or len(str(path)) > 4096:
        raise QueryError('path_rejected', 'Paths must be absolute under /work without parent traversal.')
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise QueryError('path_rejected', 'Path is outside /work.') from exc
    if not parts:
        raise QueryError('path_rejected', 'A file path is required.')
    return parts


def _parent_fd(path, root):
    parts = _parts(path, root)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _open_input(path, root, limit):
    parent, leaf = _parent_fd(path, root)
    try:
        descriptor = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
    finally:
        os.close(parent)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        os.close(descriptor)
        raise QueryError('input_rejected', 'Input must be a regular file within its byte limit.')
    return os.fdopen(descriptor, 'rb')


def _fingerprint(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _hash_file(stream, deadline, limit=MAX_SOURCE_BYTES):
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        _budget(deadline)
        size += len(chunk)
        if size > limit:
            raise QueryError('input_limit', 'Source exceeded its byte limit while reading.')
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest(), size


def _budget(deadline):
    if time.monotonic() > deadline:
        raise QueryError('timeout', 'Structured query exceeded its time limit.')


class _Lines:
    """Physical byte offsets remain exact for CRLF and quoted multiline CSV."""
    def __init__(self, stream, deadline):
        self.stream = stream
        self.deadline = deadline
        self.offset = 0
        self.row = 0
        self.digest = hashlib.sha256()

    def __iter__(self):
        return self

    def __next__(self):
        _budget(self.deadline)
        raw = self.stream.readline(MAX_RECORD_BYTES + 1)
        if not raw:
            raise StopIteration
        self.digest.update(raw)
        self.offset += len(raw)
        self.row += 1
        if len(raw) > MAX_RECORD_BYTES or self.offset > MAX_SOURCE_BYTES:
            raise QueryError('record_limit', 'Source line or input exceeds the byte limit.', row=self.row)
        return raw


def _columns(names):
    if len(names) > MAX_COLUMNS:
        raise QueryError('schema_rejected', 'Source exceeds the column limit.')
    folded = set()
    for name in names:
        if (not isinstance(name, str) or not name or len(name) > 256 or
                any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in name) or
                name.casefold().startswith('_forsic_') or name.casefold() in folded):
            raise QueryError('schema_rejected', 'Column names must be unique, nonempty, and outside the _forsic_ namespace.')
        folded.add(name.casefold())


class _Number:
    """Keep each JSON numeric token exact, including precision and exponent."""
    def __init__(self, text):
        self.text = text


def _json_value(value):
    if isinstance(value, _Number):
        return value.text
    if isinstance(value, list):
        return '[' + ','.join(_json_value(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(_json(k) + ':' + _json_value(value[k]) for k in sorted(value)) + '}'
    return _json(value)


def _cell(value):
    if isinstance(value, _Number):
        return value.text
    if value is None or isinstance(value, str):
        if isinstance(value, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError('Surrogate text is unsupported')
        return value
    return _json_value(value)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError('Nonfinite JSON number')


def _records(stream, fmt, deadline):
    lines = _Lines(stream, deadline)
    if fmt == 'jsonl':
        for raw in lines:
            start = lines.offset - len(raw)
            try:
                record = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                                    parse_constant=_bad_constant, parse_float=_Number, parse_int=_Number)
                if not isinstance(record, dict):
                    raise ValueError('Expected a JSON object')
                record = {name: _cell(value) for name, value in record.items()}
                yield record, lines.row, start, lines.offset, None
            except (UnicodeError, ValueError, RecursionError):
                yield None, lines.row, start, lines.offset, 'invalid_jsonl_record'
    else:
        def text_lines():
            for raw in lines:
                # UTF-8 decoding errors are fatal for CSV: record boundaries may
                # include subsequent physical lines and cannot be safely guessed.
                yield raw.decode('utf-8')
        csv.field_size_limit(MAX_RECORD_BYTES)
        reader = csv.reader(text_lines(), strict=True)
        try:
            header = next(reader)
            if lines.offset > MAX_RECORD_BYTES:
                raise QueryError('record_limit', 'CSV header exceeds the byte limit.')
            if header and header[0].startswith('\ufeff'):
                header[0] = header[0][1:]
            _columns(header)
            if not header:
                raise QueryError('schema_rejected', 'CSV requires a nonempty header.')
            yield {'header': header}, 0, 0, lines.offset, 'csv_header'
            row = 0
            while True:
                start = lines.offset
                try:
                    values = next(reader)
                except StopIteration:
                    break
                row += 1
                if lines.offset - start > MAX_RECORD_BYTES:
                    raise QueryError('record_limit', 'CSV record exceeds the byte limit.', row=row)
                if len(values) != len(header):
                    yield None, row, start, lines.offset, 'csv_column_count'
                else:
                    yield dict(zip(header, values)), row, start, lines.offset, None
        except StopIteration as exc:
            raise QueryError('schema_rejected', 'CSV is empty.') from exc
        except (UnicodeError, csv.Error) as exc:
            raise QueryError('csv_parse_error', 'Invalid CSV encoding or syntax; no output was accepted.',
                             physical_line=lines.row) from exc
    yield lines.digest.hexdigest(), 0, 0, lines.offset, 'source_complete'


def _ingest(connection, spec, root, deadline, skip_invalid, remaining_bytes):
    table, path, timestamp = spec
    fmt = Path(path).suffix.lower().lstrip('.')
    if fmt not in ('csv', 'jsonl'):
        raise QueryError('format_rejected', 'Only .csv and .jsonl sources are supported.')
    with _open_input(path, root, min(MAX_SOURCE_BYTES, remaining_bytes)) as stream:
        initial = _fingerprint(os.fstat(stream.fileno()))
        source_hash, size = _hash_file(stream, deadline, min(MAX_SOURCE_BYTES, remaining_bytes))
        connection.execute('CREATE TABLE ' + _quote(table) + ' (' + ','.join(
            _quote(name) + (' BIGINT' if name in META[2:5] else ' VARCHAR') for name in META) + ')')
        names = []
        accepted = rejected = seen = missing_timestamp = 0
        failures = []
        batch = []
        batch_bytes = 0

        def flush():
            nonlocal batch, batch_bytes
            if batch:
                fields = META + names
                connection.executemany('INSERT INTO ' + _quote(table) + ' (' +
                    ','.join(map(_quote, fields)) + ') VALUES (' + ','.join('?' for _ in fields) + ')', batch)
                batch = []
                batch_bytes = 0

        for record, row, start, end, error in _records(stream, fmt, deadline):
            if error == 'source_complete':
                if record != source_hash or _fingerprint(os.fstat(stream.fileno())) != initial:
                    raise QueryError('source_changed', 'Source changed during ingestion; results rejected.')
                break
            if error == 'csv_header':
                new_names = record['header']
            else:
                seen += 1
                if seen > MAX_ROWS:
                    raise QueryError('row_limit', 'Source exceeds the row limit.')
                if error:
                    rejected += 1
                    failure = {'row': row, 'byte_start': start, 'byte_end': end, 'record_error': error}
                    if len(failures) < 10:
                        failures.append(failure)
                    if not skip_invalid:
                        raise QueryError('invalid_record', 'Invalid record; use --skip-invalid only after reviewing coverage.',
                                         table=table, **failure)
                    continue
                new_names = [name for name in record if name not in names]
            if new_names:
                _columns(names + new_names)
                flush()
                for name in new_names:
                    connection.execute('ALTER TABLE ' + _quote(table) + ' ADD COLUMN ' + _quote(name) + ' VARCHAR')
                names.extend(new_names)
            if error == 'csv_header':
                continue
            raw_timestamp = record.get(timestamp) if timestamp else None
            if timestamp and raw_timestamp is None:
                missing_timestamp += 1
            batch.append([source_hash, str(path), row, start, end, raw_timestamp] + [record.get(name) for name in names])
            batch_bytes += end - start
            accepted += 1
            if len(batch) >= 512 or batch_bytes >= 2 * 1024 * 1024:
                flush()
        flush()
        if timestamp and timestamp not in names:
            raise QueryError('timestamp_missing', 'Requested timestamp column does not exist.', table=table)
        return {'table': table, 'path': str(path), 'sha256': source_hash, 'bytes': size,
                'format': fmt, 'columns': names, 'data_type': 'VARCHAR',
                'accepted_rows': accepted, 'rejected_rows': rejected,
                'rejection_samples': failures, 'rejection_samples_truncated': rejected > len(failures),
                'timestamp_field': timestamp, 'missing_timestamp_rows': missing_timestamp,
                'row_coordinate': 'physical_line_1_based' if fmt == 'jsonl' else 'data_record_1_based_header_excluded',
                'byte_coordinate': 'zero_based_half_open_original_bytes'}


def _result_cell(value):
    # JSON interchange must remain valid even for SQL NaN/Infinity, decimals,
    # blobs, timestamps and nested result types. Type names are in the receipt.
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, bytes):
        return {'hex': value.hex()}
    if isinstance(value, (list, tuple)):
        return [_result_cell(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _result_cell(v) for k, v in value.items()}
    return str(value)


def _write_outputs(prefix, rows, receipt, root):
    parent, leaf = _parent_fd(prefix, root)
    opened = []
    try:
        # Reserve both names before writing; never follow symlinks or overwrite.
        for suffix in ('.rows.jsonl', '.receipt.json'):
            name = leaf + suffix
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            opened.append((fd, name))
        for (fd, _), payload in zip(opened, (rows, (_json(receipt) + '\n').encode())):
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(fd)
    except BaseException:
        for _, name in opened:
            os.unlink(name, dir_fd=parent)
        raise
    finally:
        for fd, _ in opened:
            os.close(fd)
        os.close(parent)


def run_query(sources, sql_file, output, *, timestamps=None, skip_invalid=False,
              max_rows=100, timeout_seconds=60, _root=WORK_ROOT):
    """Trusted Python entrypoint; CLI always fixes the root to /work.

    Sources are [(table, absolute_path)]. No caller-supplied SQL executes until
    import is complete and a read-only transaction and locked config are active.
    """
    if not 1 <= len(sources) <= MAX_SOURCES:
        raise QueryError('source_limit', 'Provide between one and eight sources.')
    if type(max_rows) is not int or not 1 <= max_rows <= MAX_OUTPUT_ROWS:
        raise QueryError('output_limit', 'max_rows must be between 1 and 1000.')
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise QueryError('timeout_limit', 'timeout_seconds must be between 1 and 300.')
    timestamps = timestamps or {}
    names = [table for table, _ in sources]
    if (len(set(names)) != len(names) or any(not re.fullmatch('[a-z][a-z0-9_]{0,31}', n) for n in names)
            or set(timestamps) - set(names)):
        raise QueryError('source_rejected', 'Use unique lowercase table names and matching timestamp table names.')
    _parts(output, _root)
    with _open_input(sql_file, _root, MAX_SQL_BYTES) as stream:
        sql_bytes = stream.read(MAX_SQL_BYTES + 1)
    if len(sql_bytes) > MAX_SQL_BYTES:
        raise QueryError('sql_limit', 'SQL exceeds its byte limit.')
    try:
        sql = sql_bytes.decode('utf-8')
    except UnicodeError as exc:
        raise QueryError('sql_rejected', 'SQL must be valid UTF-8.') from exc
    try:
        import duckdb
    except ImportError as exc:
        raise QueryError('dependency_unavailable', 'Pinned DuckDB is unavailable; rebuild the tools image.') from exc
    if duckdb.__version__ != DUCKDB_VERSION:
        raise QueryError('dependency_mismatch', 'DuckDB version differs from the verified pin.')
    try:
        # Set the empty spill directory before disabling external access. Their
        # connection-option application order is unstable in DuckDB 1.5.6.
        # No caller SQL or input is handled until external access is disabled.
        connection = duckdb.connect(':memory:', config={k: v for k, v in LIMITS.items()
                                                       if k not in ('enable_external_access', 'TimeZone')})
        connection.execute("SET TimeZone = 'UTC'")
        connection.execute('SET enable_external_access = false')
    except duckdb.Error as exc:
        raise QueryError('configuration_failed', 'DuckDB security configuration could not be applied.') from exc
    deadline = time.monotonic() + timeout_seconds
    timer = threading.Timer(timeout_seconds, connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        statements = connection.extract_statements(sql)
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            raise QueryError('sql_rejected', 'Exactly one SELECT statement is required.')
        connection.execute('SET lock_configuration = true')
        manifests = []
        for table, path in sources:
            manifests.append(_ingest(connection, (table, path, timestamps.get(table)),
                                     _root, deadline, skip_invalid,
                                     MAX_TOTAL_BYTES - sum(item['bytes'] for item in manifests)))
            if sum(item['bytes'] for item in manifests) > MAX_TOTAL_BYTES:
                raise QueryError('input_limit', 'Combined source bytes exceed the limit.')
        connection.execute('BEGIN TRANSACTION READ ONLY')
        cursor = connection.execute(statements[0])
        columns = [{'name': desc[0], 'type': str(desc[1])} for desc in cursor.description]
        if len(columns) > MAX_COLUMNS:
            raise QueryError('output_limit', 'Query exceeds the output column limit.')
        output_rows = []
        output_bytes = 0
        truncated = False
        for _ in range(max_rows + 1):
            _budget(deadline)
            row = cursor.fetchone()
            if row is None:
                break
            payload = (_json([_result_cell(value) for value in row]) + '\n').encode()
            if len(output_rows) == max_rows or output_bytes + len(payload) > MAX_OUTPUT_BYTES:
                truncated = True
                break
            output_rows.append(payload)
            output_bytes += len(payload)
        connection.execute('ROLLBACK')
        rows = b''.join(output_rows)
        receipt = {'receipt_version': 1, 'helper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'duckdb_version': DUCKDB_VERSION, 'sql': sql,
                   'sql_sha256': hashlib.sha256(sql_bytes).hexdigest(), 'sources': manifests,
                   'engine_config': LIMITS, 'read_only_query': True, 'skip_invalid': skip_invalid,
                   'timeout_seconds': timeout_seconds, 'max_output_rows': max_rows,
                   'max_output_bytes': MAX_OUTPUT_BYTES, 'result_columns': columns,
                   'returned_rows': len(output_rows), 'result_bytes': len(rows),
                   'result_sha256': hashlib.sha256(rows).hexdigest(), 'result_truncated': truncated,
                   'result_encoding': 'UTF-8 JSONL arrays; columns in result_columns order',
                   'transforms': ['CSV fields remain text; no inferred types or timestamp normalization.',
                                  'JSON strings and numeric tokens remain text; bool/array/object use canonical JSON text; null stays SQL NULL.',
                                  'Metadata locates original input bytes; source is a derived /work file, not original-evidence attestation.',
                                  'Ordering and SQL nondeterminism are caller responsibilities; use explicit ORDER BY and stable expressions.']}
        receipt['receipt_sha256'] = hashlib.sha256(_json(receipt).encode()).hexdigest()
        _write_outputs(output, rows, receipt, _root)
        return {'rows_path': str(output) + '.rows.jsonl', 'receipt_path': str(output) + '.receipt.json',
                'receipt_sha256': receipt['receipt_sha256'], 'returned_rows': len(output_rows),
                'result_bytes': len(rows), 'result_sha256': receipt['result_sha256'],
                'result_truncated': truncated, 'accepted_rows': sum(x['accepted_rows'] for x in manifests),
                'rejected_rows': sum(x['rejected_rows'] for x in manifests)}
    except duckdb.Error as exc:
        code = 'timeout' if time.monotonic() > deadline else 'query_failed'
        message = str(exc)
        bounded = message[:1000]
        # Budget the escaped JSON representation as well as raw characters, so
        # native diagnostics cannot produce a huge or syntactically cut error.
        while len(_json(bounded)) > 1000:
            bounded = bounded[:-1]
        raise QueryError(code, 'DuckDB rejected or could not complete the query.',
                         engine_error_type=type(exc).__name__, engine_message=bounded,
                         engine_message_truncated=len(bounded) < len(message)) from exc
    finally:
        timer.cancel()
        timer.join()
        connection.close()


def _assignments(values, label):
    result = {}
    for value in values:
        if '=' not in value:
            raise QueryError('argument_rejected', label + ' must use NAME=VALUE.')
        name, item = value.split('=', 1)
        if name in result or not item:
            raise QueryError('argument_rejected', label + ' requires unique names and nonempty values.')
        result[name] = item
    return result


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise QueryError('argument_rejected', message)


def main(argv=None):
    parser = Parser(description=__doc__, epilog=
        'Example: structured_query.py --source events=/work/events.jsonl '
        '--sql-file /work/query.sql --output /work/new-result. '
        'Exit 0: JSON summary (check rejected_rows/result_truncated); 2: JSON error on stderr. '
        '--help needs no DuckDB and reads no evidence.')
    parser.add_argument('--source', action='append', required=True, metavar='NAME=/work/file.csv|jsonl')
    parser.add_argument('--timestamp', action='append', default=[], metavar='NAME=FIELD')
    parser.add_argument('--sql-file', required=True)
    parser.add_argument('--output', required=True, help='New /work prefix; creates .rows.jsonl and .receipt.json')
    parser.add_argument('--skip-invalid', action='store_true', help='Count and exclude invalid JSONL/CSV-width rows; malformed CSV remains fatal')
    parser.add_argument('--max-rows', type=int, default=100)
    parser.add_argument('--timeout-seconds', type=int, default=60)
    try:
        args = parser.parse_args(argv)
        result = run_query(list(_assignments(args.source, 'source').items()), args.sql_file, args.output,
                           timestamps=_assignments(args.timestamp, 'timestamp'), skip_invalid=args.skip_invalid,
                           max_rows=args.max_rows, timeout_seconds=args.timeout_seconds)
        print(_json(result))
        return 0
    except (QueryError, OSError) as exc:
        error = {'error': getattr(exc, 'code', 'local_io_rejected'),
                 'message': str(exc) if isinstance(exc, QueryError) else 'Local input or output access rejected.',
                 'details': getattr(exc, 'details', {})}
        print(_json(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
