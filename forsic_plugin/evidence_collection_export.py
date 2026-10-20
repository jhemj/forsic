"""Bounded, immutable, local-only collection ZIPs; no report rendering or collection."""
from contextlib import contextmanager
from copy import deepcopy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid
import zipfile

from .evidence_collection import CollectionError, checked_context, digest, encoded, snapshot
from .report_driven.evidence_export import _receipt

SCHEMA = 'forsic-evidence-collection-1'
MAX_EXPORT_ITEMS = 250
MAX_EXPORT_BYTES = 8 * 1024 * 1024
MAX_ZIP_BYTES = MAX_EXPORT_BYTES + 1024 * 1024
MAX_SECONDS = 10
IDENTIFIER = re.compile(r'^[a-f0-9]{40}$')

VERIFIER = r'''"""Check narrowed package integrity, never original authenticity or truth.
Reads ZIP members in memory, without extraction, imports from the package, or network.
"""
import hashlib
import io
import json
import re
import stat
import sys
import zipfile

MAX_BYTES = 9 * 1024 * 1024

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')

def verify(path):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [x.filename for x in infos]
        if len(names) != len(set(names)) or len(names) > 1100:
            raise ValueError('Duplicate or excessive members')
        if sum(x.file_size for x in infos) > MAX_BYTES:
            raise ValueError('Archive size exceeds bound')
        for info in infos:
            name = info.filename
            mode = info.external_attr >> 16
            if (name.startswith('/') or '\\' in name or ':' in name or
                    any(p in ('', '.', '..') for p in name.split('/')) or
                    stat.S_IFMT(mode) not in (0, stat.S_IFREG) or mode & 0o111 or info.is_dir()):
                raise ValueError('Unsafe archive member')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest['schema'] != 'forsic-evidence-collection-1':
            raise ValueError('Unsupported collection schema')
        files = manifest['files']
        if len(files) != len({x['name'] for x in files}) or set(names) != {'manifest.json'} | {x['name'] for x in files}:
            raise ValueError('Unexpected or missing archive members')
        for entry in files:
            raw = archive.read(entry['name'])
            if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError('Member digest mismatch')
        index = json.loads(archive.read('evidence-index.json'))
        receipts = json.loads(archive.read('receipts/selected-executions.json'))
        receipt_ids = {x['result_id'] for x in receipts}
        if len(receipts) != len(receipt_ids):
            raise ValueError('Duplicate receipts')
        selected = [(x['evidence_item_id'], x['item_version']) for x in index]
        if (len(index) != manifest['item_count'] or len(selected) != len(set(x[0] for x in selected)) or
                hashlib.sha256(canonical(selected)).hexdigest() != manifest['selection_digest']):
            raise ValueError('Selection mismatch')
        for item in index:
            ident = item['evidence_item_id']
            if not re.fullmatch(r'E-[a-f0-9]{32}', ident):
                raise ValueError('Invalid evidence identity')
            provenance = json.loads(archive.read('provenance/' + ident + '.json'))
            if (provenance['case_id'] != manifest['case_id'] or
                    provenance['source_snapshot_id'] != manifest['source_snapshot_id'] or
                    provenance['item'] != item or item['source_ref']['id'] not in receipt_ids):
                raise ValueError('Case/snapshot/source reference mismatch')
            if manifest['source_index'].get(item['source_ref']['id']) != item['source_ref']['version']:
                raise ValueError('Selected source version mismatch')
            span = item['retained_span']
            identity = {'case_id': manifest['case_id'], 'source_ref': item['source_ref'], **span}
            if ident != 'E-' + hashlib.sha256(canonical(identity)).hexdigest()[:32]:
                raise ValueError('Evidence coordinate identity mismatch')
            if (span['coordinate_space'] != 'retained_utf8_scalar_bytes' or span['encoding'] != 'utf-8' or
                    type(span['byte_start']) is not int or type(span['byte_end']) is not int or
                    not 0 <= span['byte_start'] <= span['byte_end']):
                raise ValueError('Invalid excerpt coordinates')
            raw = archive.read('excerpts/' + ident + '.txt')
            if (raw.decode('utf-8') != item['excerpt'] or len(raw) != span['byte_end'] - span['byte_start'] or
                    hashlib.sha256(raw).hexdigest() != item['excerpt_sha256']):
                raise ValueError('Excerpt digest/span mismatch')
            if hashlib.sha256(canonical({k:v for k,v in item.items() if k != 'item_version'})).hexdigest() != item['item_version']:
                raise ValueError('Item version mismatch')
        restricted = json.loads(archive.read('restricted-index.json')) if 'restricted-index.json' in names else []
        if bool(restricted) != manifest['contains_sensitive_originals']:
            raise ValueError('Restricted disclosure mismatch')
        for entry in restricted:
            if entry['evidence_item_id'] not in {x['evidence_item_id'] for x in index if x['sensitive']}:
                raise ValueError('Unselected restricted original')
            raw = archive.read(entry['path'])
            if hashlib.sha256(raw).hexdigest() != entry['sha256'] or len(raw) != entry['bytes']:
                raise ValueError('Restricted excerpt mismatch')
        if (manifest['full_retained_scalar_verification'] != 'not_available' or
                manifest['original_source_verification'] != 'not_available'):
            raise ValueError('Invalid verification scope')
        return {'verified_items': len(index), 'source_snapshot_id': manifest['source_snapshot_id'],
                'full_retained_scalar_verification': 'not_available', 'original_source_verification': 'not_available'}

if __name__ == '__main__':
    print(json.dumps(verify(sys.argv[1]), ensure_ascii=False, sort_keys=True))
'''


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def _store(case, create=False):
    """Walk every component with O_NOFOLLOW; later I/O is directory-fd relative."""
    path = Path(case.output).absolute()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        if create:
            try:
                os.mkdir('.evidence-collection', 0o700, dir_fd=fd)
            except FileExistsError:
                pass
        child = os.open('.evidence-collection', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        info = os.fstat(child)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            os.close(child)
            raise CollectionError('Evidence artifacts require owner-only storage', 409)
        os.close(fd)
        fd = child
        yield fd
    except (OSError, ValueError) as exc:
        if isinstance(exc, CollectionError):
            raise
        raise CollectionError('Evidence artifact storage unavailable', 409) from None
    finally:
        os.close(fd)


def _read(fd, name, maximum=MAX_ZIP_BYTES):
    opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(opened, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum or
                info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise CollectionError('Unsafe or oversized stored artifact', 409)
        raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise CollectionError('Stored artifact exceeds byte bound', 413)
        return raw


def _publish(fd, name, raw):
    temp = '.stage-' + uuid.uuid4().hex
    opened = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(opened, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.fsync(fd)
    finally:
        os.unlink(temp, dir_fd=fd)


def _ownership(context):
    return {k: context[k] for k in ('case_id', 'profile_id', 'session_id', 'conversation_id')}


def _csv_cell(value):
    value = ''.join(ch if ord(ch) >= 32 and ord(ch) != 127 else ' ' for ch in str(value))
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def _restricted(case, snap, selected):
    from .sensitive_policy import report_appendix
    entries = []
    budget = 32768
    for item in selected:
        if not item['sensitive']:
            continue
        event = snap['_events'][item['source_ref']['id']]
        pointer = item['retained_span']['field_pointer']
        match = re.match(r'^/findings/(\d+)(?:/|$)', pointer)
        if not match:
            raise CollectionError('Restricted originals require a specific selected finding', 409)
        try:
            finding_id = event['data']['findings'][int(match[1])]['finding_id']
        except (KeyError, IndexError, TypeError):
            raise CollectionError('Selected sensitive finding unavailable', 409) from None
        appendix = report_appendix(case, [item['source_ref']['id']], max_findings=40, max_bytes=32768)
        if appendix.get('limitations') or appendix.get('unavailable_findings'):
            raise CollectionError('Sensitive original hash/input verification failed', 409)
        chosen = [x for x in appendix['entries'] if x['finding_id'] == finding_id]
        if not chosen:
            raise CollectionError('Selected sensitive original unavailable or exceeds bound', 409)
        for entry in chosen:
            from .report_driven.host import source_record
            original_source = case.event(entry['source_ref']['id'])
            if not original_source or source_record(original_source)['version'] != entry['source_ref']['version']:
                raise CollectionError('Sensitive input source version changed', 409)
            # Match-only bytes avoid the unrelated surrounding private context.
            start = entry['match_byte_start'] - entry['excerpt_byte_start']
            end = entry['match_byte_end'] - entry['excerpt_byte_start']
            original = entry['excerpt'].encode(entry['encoding'])
            if not 0 <= start < end <= len(original):
                raise CollectionError('Sensitive original coordinates unavailable', 409)
            text = original[start:end].decode(entry['encoding'])
            raw = text.encode('utf-8')
            budget -= len(raw)
            if budget < 0 or len(entries) >= 40:
                raise CollectionError('Sensitive originals exceed bounded limit', 413)
            entries.append({'evidence_item_id': item['evidence_item_id'], 'finding_id': entry['finding_id'],
                            'input_id': entry['input_id'], 'source_ref': deepcopy(entry['source_ref']),
                            'coordinate_space': 'retained_scanner_input_bytes', 'input_encoding': entry['encoding'],
                            'byte_start': entry['match_byte_start'], 'byte_end': entry['match_byte_end'],
                            'raw': raw})
    return entries


def _build(case, snap, selected, include_sensitive_originals, started):
    members = {'verify.py': VERIFIER.encode('utf-8'), 'README.txt': (
        'Forsic case evidence collection. Local-only; no automatic remote delivery.\n'
        'Run python3 verify.py <this.zip>. The verifier reads bounded ZIP members without extraction or network.\n'
        'Only selected UTF-8 excerpts are included. Whole scalars, complete native results, original files,\n'
        'scanner diagnostics and HMAC keys are omitted. Digests verify exported bytes and reference bindings;\n'
        'they do not establish original authenticity, factual truth, semantic support, original-file offsets,\n'
        'or full retained scalar/result hashes. Original/whole scalar verification: not_available.\n'
        'General excerpts may contain undetected sensitive data; this package is not a DLP or anonymization guarantee.\n'
        'CSV is an escaped derived display. Exact allowed excerpt bytes are in excerpts/.\n'
        'restricted/ exists only after explicit local-admin opt-in and verified bounded finding extraction.\n'
    ).encode('utf-8')}
    class ReceiptCase:
        def event(self, ident):
            return snap['_events'].get(ident)
    receipt_case = ReceiptCase()
    receipts = {}
    for item in selected:
        ident = item['evidence_item_id']
        members['excerpts/' + ident + '.txt'] = item['excerpt'].encode('utf-8')
        members['provenance/' + ident + '.json'] = encoded({'case_id': snap['case_id'],
            'source_snapshot_id': snap['source_snapshot_id'], 'item': item,
            'lineage': 'Source/version and retained UTF-8 scalar coordinates; original locator is separate. No source recollection.'})
        source_id = item['source_ref']['id']
        receipts[source_id] = _receipt(receipt_case, snap['_events'][source_id])
    members['evidence-index.json'] = encoded(selected)
    display = io.StringIO(newline='')
    writer = csv.writer(display)
    writer.writerow(['evidence_item_id', 'title', 'kind', 'source_id', 'excerpt', 'currentness'])
    for item in selected:
        writer.writerow([_csv_cell(item[k] if k != 'source_id' else item['source_ref']['id'])
                         for k in ('evidence_item_id', 'title', 'kind', 'source_id', 'excerpt', 'currentness')])
    members['evidence-index.csv'] = display.getvalue().encode('utf-8')
    members['receipts/selected-executions.json'] = encoded(list(receipts.values()))
    restricted = _restricted(case, snap, selected) if include_sensitive_originals else []
    restricted_index = []
    for number, entry in enumerate(restricted):
        entry = dict(entry)
        raw = entry.pop('raw')
        path = 'restricted/' + entry['evidence_item_id'] + '-%02d.txt' % number
        members[path] = raw
        restricted_index.append({**entry, 'path': path, 'bytes': len(raw), 'sha256': _hash(raw)})
    if restricted_index:
        members['restricted-index.json'] = encoded(restricted_index)
    manifest = {'schema': SCHEMA, 'case_id': snap['case_id'],
        'source_snapshot_id': snap['source_snapshot_id'], 'ledger_cutoff': snap['ledger_cutoff'],
        'selection_digest': digest([(x['evidence_item_id'], x['item_version']) for x in selected]),
        'item_count': len(selected), 'contains_sensitive_originals': bool(restricted_index),
        'source_index': {x['source_ref']['id']: x['source_ref']['version'] for x in selected},
        'restricted_source_refs': list({digest(x['source_ref']): x['source_ref'] for x in restricted_index}.values()),
        'local_only': True, 'full_retained_scalar_verification': 'not_available',
        'full_retained_result_verification': 'not_available', 'original_source_verification': 'not_available',
        'files': [{'name': name, 'bytes': len(raw), 'sha256': _hash(raw)} for name, raw in sorted(members.items())]}
    members['manifest.json'] = encoded(manifest)
    if sum(map(len, members.values())) > MAX_EXPORT_BYTES:
        raise CollectionError('Selected evidence exceeds byte bound', 413)
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        written = 0
        for name, raw in sorted(members.items()):
            written += len(raw)
            if written > MAX_EXPORT_BYTES or time.monotonic() - started > MAX_SECONDS:
                raise CollectionError('Evidence export exceeds byte/time bound', 413)
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o600 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw)
    raw = result.getvalue()
    if len(raw) > MAX_ZIP_BYTES:
        raise CollectionError('Evidence archive exceeds byte bound', 413)
    verify_collection(io.BytesIO(raw))
    return raw, manifest


def verify_collection(path):
    namespace = {'__name__': 'collection_verifier'}
    exec(VERIFIER, namespace)
    return namespace['verify'](path)


def create_export(case, *, context, source_snapshot_id, selection='selected', items=None,
                  include_sensitive_originals=False, local_admin=False, idempotency_key):
    started = time.monotonic()
    context = checked_context(case, context)
    if type(include_sensitive_originals) is not bool or (include_sensitive_originals and local_admin is not True):
        raise CollectionError('Sensitive original export requires local administrator', 403)
    if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128:
        raise CollectionError('An idempotency key of 8–128 characters is required')
    if selection not in ('selected', 'all_eligible') or not isinstance(source_snapshot_id, str):
        raise CollectionError('Invalid evidence selection')
    if items is None:
        items = []
    if (not isinstance(items, list) or len(items) > MAX_EXPORT_ITEMS or
            any(not isinstance(x, dict) or set(x) != {'evidence_item_id', 'item_version'} or
                not all(isinstance(v, str) for v in x.values()) for x in items) or
            len({x['evidence_item_id'] for x in items}) != len(items) or selection == 'all_eligible' and items):
        raise CollectionError('Invalid selected evidence identities')
    items = sorted(items, key=lambda x: x['evidence_item_id'])
    request = {'context': _ownership(context), 'source_snapshot_id': source_snapshot_id,
               'selection': selection, 'items': items, 'include_sensitive_originals': include_sensitive_originals}
    request_hash = digest(request)
    export_id = digest({'owner': _ownership(context), 'idempotency_key': idempotency_key})[:40]
    # Look up an existing reservation BEFORE requiring current snapshot. A retry
    # after a correction still returns the original immutable artifact.
    with _store(case, create=True) as store:
        try:
            existing = os.open(export_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=store)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            try:
                prior = json.loads(_read(existing, 'request.json'))
                if digest(prior) != request_hash:
                    raise CollectionError('Idempotency key reused with different input', 409)
            except FileNotFoundError:
                raise CollectionError('Export reservation completion unknown; do not recreate', 409) from None
            finally:
                os.close(existing)
            return export_status(case, export_id, context=context, local_admin=local_admin)
        snap = snapshot(case, context=context)
        if snap['source_snapshot_id'] != source_snapshot_id:
            raise CollectionError('Selected evidence snapshot changed; refresh and select again', 409)
        eligible = {x['evidence_item_id']: x for x in snap['items'] if x['downloadable']}
        if selection == 'all_eligible':
            selected = list(eligible.values())
        else:
            selected = []
            for requested in items:
                found = eligible.get(requested['evidence_item_id'])
                if not found or found['item_version'] != requested['item_version']:
                    raise CollectionError('Selected item version is stale or unavailable', 409)
                selected.append(found)
        selected.sort(key=lambda x: x['evidence_item_id'])
        if not selected:
            raise CollectionError('No downloadable evidence selected', 409)
        if len(selected) > MAX_EXPORT_ITEMS:
            raise CollectionError('Selected evidence exceeds item bound', 413)
        if sum(len(encoded(x)) * 3 + len(x['excerpt'].encode('utf-8')) for x in selected) > MAX_EXPORT_BYTES:
            raise CollectionError('Selected excerpts and metadata exceed byte bound', 413)
        try:
            os.mkdir(export_id, 0o700, dir_fd=store)
        except FileExistsError:
            raise CollectionError('Export reservation exists; retry status', 409) from None
        fd = os.open(export_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=store)
        try:
            _publish(fd, 'request.json', encoded(request))
            _publish(fd, 'building.json', encoded({'export_id': export_id, 'status': 'building'}))
            try:
                raw, manifest = _build(case, snap, selected, include_sensitive_originals, started)
                # Source content is immutable by contract; fail if altered while
                # building. Judgment edits are allowed: saved selection stays fixed.
                from .report_driven.host import source_record
                source_refs = [x['source_ref'] for x in selected] + manifest['restricted_source_refs']
                for source_ref in {digest(x): x for x in source_refs}.values():
                    event = case.event(source_ref['id'])
                    if not event or source_record(event)['version'] != source_ref['version']:
                        raise CollectionError('Retained source changed during export', 409)
                if time.monotonic() - started > MAX_SECONDS:
                    raise CollectionError('Evidence export exceeds time bound', 413)
                _publish(fd, 'evidence.zip', raw)
                metadata = {'export_id': export_id, 'status': 'ready', **_ownership(context),
                    'source_snapshot_id': source_snapshot_id, 'selection_digest': manifest['selection_digest'],
                    'item_count': len(selected), 'include_sensitive_originals': include_sensitive_originals,
                    'contains_sensitive_originals': manifest['contains_sensitive_originals'], 'local_only': True,
                    'sha256': _hash(raw), 'bytes': len(raw), 'schema': SCHEMA,
                    'filename': '증거-모음-' + export_id[:12] + '.zip', 'created_at': time.time()}
                _publish(fd, 'ready.json', encoded(metadata))
            except Exception:
                _publish(fd, 'failed.json', encoded({'export_id': export_id, 'status': 'failed', 'reason': 'Evidence export could not be verified or exceeded its bound'}))
                raise
        finally:
            os.close(fd)
    return export_status(case, export_id, context=context, local_admin=local_admin)


def _stored(case, export_id, context, local_admin=False):
    context = checked_context(case, context)
    if not isinstance(export_id, str) or not IDENTIFIER.fullmatch(export_id):
        raise CollectionError('Invalid export identifier', 404)
    with _store(case) as store:
        try:
            fd = os.open(export_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=store)
        except FileNotFoundError:
            raise CollectionError('Evidence export unavailable', 404) from None
        try:
            request = json.loads(_read(fd, 'request.json'))
            if request['context'] != _ownership(context):
                raise CollectionError('Evidence export belongs to another ownership context', 403)
            if request['include_sensitive_originals'] and local_admin is not True:
                raise CollectionError('Sensitive original artifact requires local administrator', 403)
            try:
                metadata = json.loads(_read(fd, 'ready.json'))
            except FileNotFoundError:
                try:
                    metadata = json.loads(_read(fd, 'failed.json'))
                except FileNotFoundError:
                    metadata = json.loads(_read(fd, 'building.json'))
                return metadata, None
            if (any(metadata[k] != request['context'][k] for k in _ownership(context)) or
                    metadata['source_snapshot_id'] != request['source_snapshot_id'] or
                    metadata['include_sensitive_originals'] != request['include_sensitive_originals'] or
                    metadata['export_id'] != export_id or metadata['status'] != 'ready'):
                raise CollectionError('Stored evidence context mismatch', 409)
            raw = _read(fd, 'evidence.zip')
            if len(raw) != metadata['bytes'] or _hash(raw) != metadata['sha256']:
                raise CollectionError('Stored evidence archive digest mismatch', 409)
            verified = verify_collection(io.BytesIO(raw))
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                manifest = json.loads(archive.read('manifest.json'))
            if (manifest['case_id'] != context['case_id'] or
                    verified['source_snapshot_id'] != request['source_snapshot_id'] or
                    manifest['selection_digest'] != metadata['selection_digest'] or
                    manifest['item_count'] != metadata['item_count'] or
                    manifest['contains_sensitive_originals'] != metadata['contains_sensitive_originals']):
                raise CollectionError('Stored evidence manifest identity mismatch', 409)
            metadata['stale'] = snapshot(case, context=context)['source_snapshot_id'] != request['source_snapshot_id']
            return metadata, raw
        except CollectionError:
            raise
        except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile):
            raise CollectionError('Stored evidence artifact cannot be verified', 409) from None
        finally:
            os.close(fd)


def export_status(case, export_id, *, context, local_admin=False):
    return _stored(case, export_id, context, local_admin)[0]


def export_download(case, export_id, *, context, local_admin=False):
    metadata, raw = _stored(case, export_id, context, local_admin)
    if metadata['status'] != 'ready' or raw is None:
        raise CollectionError('Evidence export is not ready', 409)
    return io.BytesIO(raw), metadata
