"""Source-bound local indicator register; no lookup, execution or incident verdict."""
import csv
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit
import uuid

from .notes import sources

TYPES = ('ip', 'domain', 'url', 'hash', 'path', 'account')
STATUSES = ('observed', 'suspicious', 'benign', 'undetermined', 'withdrawn')
MEANING = ('Local observed indicators and recorded interpretations, not proof of compromise, '
           'execution, attribution or independent analyst approval. Benign means a recorded '
           'benign explanation, not proof of safety. updated_at is register time, not event time.')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def domain(value):
    value = value.rstrip('.').encode('idna').decode('ascii').lower()
    if len(value) > 253 or not value or any(not re.fullmatch(
            r'[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?', label) for label in value.split('.')):
        raise ValueError('Invalid domain indicator')
    return value


def canonical(kind, value):
    if kind not in TYPES or not isinstance(value, str) or not value.strip() or len(value) > 8192:
        raise ValueError('Use a supported indicator type and a nonempty value of at most 8192 characters')
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('Indicator values cannot contain control characters')
    if kind in ('path', 'account'):
        # Filesystem/account case and path semantics depend on the source system.
        return value
    value = value.strip()
    if kind == 'ip':
        return str(ipaddress.ip_address(value))
    if kind == 'domain':
        return domain(value)
    if kind == 'hash':
        if not re.fullmatch(r'(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{40}|[a-fA-F0-9]{64})', value):
            raise ValueError('Hash indicators must be MD5, SHA1 or SHA256 hex values')
        return value.lower()
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in ('http', 'https', 'ftp') or not parsed.hostname:
        raise ValueError('Use an absolute http, https or ftp URL; it is stored locally and never opened')
    # Validate the port, preserve its explicit presence and preserve path/query case.
    port = parsed.port
    try:
        host = str(ipaddress.ip_address(parsed.hostname))
        if ':' in host:
            host = '[' + host + ']'
    except ValueError:
        host = domain(parsed.hostname)
    userinfo = parsed.netloc.rsplit('@', 1)[0] + '@' if '@' in parsed.netloc else ''
    netloc = userinfo + host + (':' + str(port) if port is not None else '')
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, parsed.query, parsed.fragment))


def _scalar(value, pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('Use an exact JSON pointer into the retained source result')
    try:
        for key in pointer[1:].split('/'):
            if re.search(r'~(?![01])', key):
                raise ValueError('Invalid JSON pointer escape')
            key = key.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                if not re.fullmatch(r'0|[1-9][0-9]*', key):
                    raise ValueError('Use a nonnegative exact list index')
                value = value[int(key)]
            else:
                value = value[key]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError('The source pointer does not resolve') from exc
    if not isinstance(value, str):
        raise ValueError('Indicator provenance must select an original text field')
    return value


def checked_sources(case, kind, value, refs):
    if not isinstance(refs, list) or not refs or len(refs) > 100:
        raise ValueError('Provide 1 to 100 exact source_refs for an indicator upsert')
    result = []
    for ref in refs:
        event = sources(case, [ref['source_id']])[0]
        data = event['data']
        version = digest(data)
        if ref.get('source_version', version) != version:
            raise ValueError('Indicator source version changed')
        pointer = ref['pointer']
        field = _scalar(data, pointer)
        parts = [part.replace('~1', '/').replace('~0', '~') for part in pointer.split('/')]
        if any(part in ('search_text', 'search_path', 'arguments', 'reason', 'meaning',
                        'scope', 'scope_note', 'error', 'limitation', 'tool', 'started_id') for part in parts):
            raise ValueError('Query inputs and tool commentary are not observed indicator provenance')
        if (data.get('source_kind') != 'external_intelligence'
                and data.get('outcome') in ('not_found', 'unavailable')):
            raise ValueError('A missing or unavailable local result is not an observed indicator')
        literal = ref['literal']
        start = ref.get('byte_start', 0)
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise ValueError('byte_start must be a nonnegative UTF-8 byte offset')
        if not isinstance(literal, str) or not literal:
            raise ValueError('Provide the exact observed indicator literal')
        end = start + len(literal.encode())
        if field.encode()[start:end] != literal.encode():
            raise ValueError('Indicator literal does not match the retained UTF-8 source span')
        if canonical(kind, literal) != value:
            raise ValueError('Source literal must identify this exact canonical indicator')
        role = 'external_reference' if data.get('source_kind') == 'external_intelligence' else 'local_observation'
        if kind == 'hash':
            if 'sha256_slice' in parts:
                raise ValueError('A sha256_slice is not a whole-file hash indicator')
            role = 'external_reference' if role == 'external_reference' else 'recorded_hash'
            if pointer == '/sha256' and data.get('tool') == 'forsic_hash':
                role = 'whole_file_hash'
            elif pointer == '/sha256' and data.get('tool') == 'forsic_image_files':
                if data.get('complete_file') is not True and data.get('hash_scope') != 'whole_file':
                    raise ValueError('Image hash provenance must explicitly cover the whole file')
                role = 'whole_file_hash'
        record = dict(source_id=event['id'], source_version=version, pointer=pointer,
                      literal=literal, byte_start=start, byte_end=end, source_kind=role,
                      source_path=str(data.get('file_path') or data.get('path') or ''))
        if record not in result:
            result.append(record)
    return result


def current(case):
    with case.connect() as db:
        rows = db.execute("SELECT data FROM events WHERE kind='indicator' ORDER BY rowid").fetchall()
    latest = {}
    for row in rows:
        record = json.loads(row['data'])
        latest[record['indicator_id']] = record
    return sorted(latest.values(), key=lambda row: (row['type'], row['canonical_value']))


def _selected(case, args):
    records = current(case)
    if args.get('type'):
        if args['type'] not in TYPES:
            raise ValueError('Unknown indicator type')
        records = [row for row in records if row['type'] == args['type']]
    if args.get('status'):
        if args['status'] not in STATUSES:
            raise ValueError('Unknown indicator status')
        records = [row for row in records if row['status'] == args['status']]
    query = str(args.get('query') or '').casefold()
    return [row for row in records if not query or query in json.dumps(row, ensure_ascii=False).casefold()]


def upsert(case, args):
    kind = args['type']
    value = canonical(kind, args['value'])
    ident = 'IOC-' + digest([kind, value])[:24]
    refs = checked_sources(case, kind, value, args.get('source_refs'))
    if 'status' in args and args['status'] not in STATUSES:
        raise ValueError('Unknown indicator status')
    if 'summary' in args and (not isinstance(args['summary'], str) or len(args['summary']) > 16000):
        raise ValueError('summary must be text of at most 16000 characters')
    if 'limitations' in args and (not isinstance(args['limitations'], list) or len(args['limitations']) > 100
            or any(not isinstance(item, str) or not item.strip() or len(item) > 8000 for item in args['limitations'])):
        raise ValueError('limitations must contain at most 100 nonempty text items')
    with case.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute("SELECT data FROM events WHERE kind='indicator' AND json_extract(data,'$.indicator_id')=? ORDER BY rowid DESC LIMIT 1", (ident,)).fetchone()
        old = json.loads(previous['data']) if previous else None
        if old and args.get('revision') != old['revision']:
            raise ValueError(f"Indicator revision conflict: read get and pass current revision={old['revision']}")
        if not old and args.get('revision') not in (None, 0):
            raise ValueError('A new indicator has no prior revision')
        record = dict(indicator_id=ident, type=kind, canonical_value=value,
                      raw_values=list(old['raw_values']) if old else [],
                      source_refs=list(old['source_refs']) if old else [],
                      status=args.get('status', old['status'] if old else 'observed'),
                      summary=args.get('summary', old['summary'] if old else ''),
                      limitations=args.get('limitations', old['limitations'] if old else []))
        if record['status'] != 'observed' and not record['summary'].strip():
            raise ValueError('A status interpretation needs a summary explaining its evidence and limits')
        for ref in refs:
            if ref not in record['source_refs']:
                record['source_refs'].append(ref)
            if ref['literal'] not in record['raw_values']:
                record['raw_values'].append(ref['literal'])
        if old and all(old[key] == val for key, val in record.items()):
            return {'indicator': old, 'reused': True, 'meaning': MEANING}
        record.update(revision=old['revision'] + 1 if old else 1, updated_at=time.time())
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                   (uuid.uuid4().hex, record['updated_at'], 'indicator', '', json.dumps(record, ensure_ascii=False)))
    return {'indicator': record, 'reused': False, 'meaning': MEANING}


def _csv_cell(value):
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
    # A local IOC can legitimately start with a spreadsheet formula character.
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def _export_content(case, records, fmt):
    if fmt not in ('json', 'csv'):
        raise ValueError('Use json or csv export format')
    payload = dict(schema_version='forsic-indicators-1', case_id=case.config['case_id'],
                   meaning=MEANING, indicators=records)
    if fmt == 'json':
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode()
    else:
        stream = io.StringIO(newline='')
        fields = ('indicator_id', 'type', 'canonical_value', 'raw_values', 'status', 'summary',
                  'limitations', 'source_refs', 'revision', 'updated_at')
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows([_csv_cell(row[field]) for field in fields] for row in records)
        content = stream.getvalue().encode('utf-8-sig')
    return content


def export_content(case, format='json', filters=None):
    """Passive serialization for browser downloads; no artifact or event is written."""
    return _export_content(case, _selected(case, filters or {}), format)


def export(case, args):
    records = _selected(case, args)
    fmt = args.get('format', 'json')
    content = _export_content(case, records, fmt)
    checksum = hashlib.sha256(content).hexdigest()
    path = Path(case.output) / ('indicators-' + checksum + '.' + fmt)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise ValueError('Existing indicator export has changed')
    else:
        fd, staging = tempfile.mkstemp(prefix='.indicators-', dir=case.output)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(content)
            try:
                os.link(staging, path)
            except FileExistsError:
                if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
                    raise ValueError('Concurrent indicator export has changed')
        finally:
            os.unlink(staging)
    return {'path': str(path), 'name': path.name, 'format': fmt, 'sha256': checksum,
            'bytes': len(content), 'count': len(records), 'meaning': MEANING,
            'csv_formula_escape': "CSV prefixes spreadsheet-active cells with an apostrophe; JSON retains exact strings."}


def invoke(case, args):
    action = args.get('action', 'list')
    if action == 'upsert':
        return upsert(case, args)
    if action == 'export':
        return export(case, args)
    if action == 'get':
        with case.connect() as db:
            rows = db.execute("SELECT data FROM events WHERE kind='indicator' AND json_extract(data,'$.indicator_id')=? ORDER BY rowid DESC", (args['indicator_id'],)).fetchall()
        if not rows:
            raise ValueError('Unknown indicator_id in this case')
        history = [json.loads(row['data']) for row in rows]
        return {'indicator': history[0], 'history': history, 'meaning': MEANING}
    if action != 'list':
        raise ValueError('Supported indicator actions: list, get, upsert, export')
    records = _selected(case, args)
    offset, limit = max(0, int(args.get('offset', 0))), max(1, min(100, int(args.get('limit', 50))))
    end = offset + limit
    return {'indicators': records[offset:end], 'total': len(records),
            'next_offset': end if end < len(records) else None, 'meaning': MEANING}


def schema_properties():
    """Properties for the existing native tool registration; action is required."""
    string = {'type': 'string'}
    return {
        'action': {'type': 'string', 'enum': ['list', 'get', 'upsert', 'export']},
        'type': {'type': 'string', 'enum': list(TYPES)}, 'value': string,
        'indicator_id': string, 'revision': {'type': 'integer', 'minimum': 0},
        'status': {'type': 'string', 'enum': list(STATUSES)}, 'summary': string,
        'limitations': {'type': 'array', 'items': string}, 'query': string,
        'offset': {'type': 'integer'}, 'limit': {'type': 'integer'},
        'format': {'type': 'string', 'enum': ['json', 'csv']},
        'source_refs': {'type': 'array', 'minItems': 1, 'maxItems': 100, 'items': {
            'type': 'object', 'properties': {
                'source_id': string, 'source_version': string, 'pointer': string,
                'literal': string, 'byte_start': {'type': 'integer', 'minimum': 0}},
            'required': ['source_id', 'pointer', 'literal', 'byte_start']}},
    }
