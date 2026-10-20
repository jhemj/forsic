"""Export selected retained evidence without collecting or reopening originals.

The package proves the integrity of retained scalar fields and their excerpts.
It does not contain original files or establish their authenticity. Full native
results and request arguments are deliberately omitted. Their canonical hashes
remain identifiers, not independently verified original-file digests.
"""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile


SCHEMA = 'forsic-selected-evidence-1'
FROZEN_FIELDS = frozenset(('forensic', 'forensic_context', 'temporal', 'temporal_context', 'report_slots'))


def _separate_frozen(value, path=''):
    """Preserve host-defined frozen declarations outside the current graph."""
    bindings = []
    def separate(item, pointer):
        if isinstance(item, dict):
            current = {}
            for key, child in item.items():
                child_path = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
                if key in FROZEN_FIELDS:
                    bindings.append({'path': child_path, 'field': key, 'value': deepcopy(child)})
                else:
                    current[key] = separate(child, child_path)
            return current
        if isinstance(item, list):
            return [separate(child, pointer + '/' + str(index)) for index, child in enumerate(item)]
        return deepcopy(item)
    return separate(value, path), bindings


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _hash(raw, target):
    return {'algorithm': 'sha256', 'hex': hashlib.sha256(raw).hexdigest(), 'object': target}


# The verifier has no application dependency, file extraction, network, or tool
# execution. Run it against the ZIP itself, including after copying the bundle.
VERIFIER = r'''"""Verify retained scalar/excerpt integrity, not original evidence or truth."""
import hashlib
import json
import sys
import zipfile


def verify(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive members')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest['schema'] != 'forsic-selected-evidence-1':
            raise ValueError('Unsupported evidence package')
        indexed = manifest['files']
        if set(names) != {'manifest.json'} | {item['name'] for item in indexed}:
            raise ValueError('Unexpected or missing archive members')
        if len(indexed) != len({item['name'] for item in indexed}):
            raise ValueError('Duplicate file index')
        for item in indexed:
            raw = archive.read(item['name'])
            if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('Member hash mismatch: ' + item['name'])
        sources = {s['source']['source_id']: s for s in json.loads(archive.read('sources.json'))}
        chunks = {s['chunk_id']: s for s in json.loads(archive.read('chunks.json'))}
        receipts = {r['receipt_id']: r for r in json.loads(archive.read('receipts.json'))}
        cards = json.loads(archive.read('cards.json'))
        selection = json.loads(archive.read('selection.json'))
        if selection['case_id'] != manifest['case_id'] or selection['snapshot_id'] != manifest['snapshot_id']:
            raise ValueError('Selection case or snapshot mismatch')
        # The frozen-declaration extension is paired and optional under the
        # existing schema. Old ZIPs retain their original strict ref checks.
        historical_keys = {'historical_report_declarations', 'historical_bindings'}
        historical_files = {'report-slot-declarations.json', 'historical-bindings.json'}
        has_historical = bool(historical_keys & manifest.keys() or historical_files & set(names))
        if has_historical:
            if not historical_keys <= manifest.keys() or not historical_files <= set(names):
                raise ValueError('Incomplete historical declaration extension')
            historical = manifest['historical_report_declarations']
            if historical != {'file': 'report-slot-declarations.json',
                              'role': 'frozen_analyst_declarations_not_current_evidence'}:
                raise ValueError('Historical report declaration role mismatch')
            declarations = json.loads(archive.read(historical['file']))
            if (declarations['schema'] != 'forsic-report-slot-declarations-1' or
                    declarations['case_id'] != manifest['case_id'] or
                    declarations['snapshot_id'] != manifest['snapshot_id'] or
                    declarations['role'] != historical['role'] or
                    declarations['current_reference_verification'] is not False or
                    declarations['projection']['snapshot_id'] != manifest['snapshot_id']):
                raise ValueError('Historical report declaration identity mismatch')
            historical_bindings = manifest['historical_bindings']
            if historical_bindings != {'file': 'historical-bindings.json',
                                       'role': 'frozen_analyst_bindings_not_current_evidence'}:
                raise ValueError('Historical binding role mismatch')
            bindings = json.loads(archive.read(historical_bindings['file']))
            if (bindings['schema'] != 'forsic-historical-bindings-1' or
                    bindings['case_id'] != manifest['case_id'] or
                    bindings['snapshot_id'] != manifest['snapshot_id'] or
                    bindings['role'] != historical_bindings['role'] or
                    bindings['current_reference_verification'] is not False):
                raise ValueError('Historical binding identity mismatch')
            # Its member hash is verified above. Frozen analyst references are
            # historical selections, explicitly outside the current evidence graph.
            def contains_frozen(value):
                if isinstance(value, dict):
                    return any(k in ('forensic', 'forensic_context', 'temporal', 'temporal_context', 'report_slots')
                               or contains_frozen(v) for k, v in value.items())
                return isinstance(value, list) and any(contains_frozen(v) for v in value)
            if contains_frozen(selection):
                raise ValueError('Frozen declarations cannot enter current reference verification')
        index = manifest['selection_index']
        actual = {
            'source': {key: s['source']['source_version'] for key, s in sources.items()},
            'observation': {c['card_id']: c['observation_version'] for c in cards},
            'claim': {c['id']: c['version'] for c in selection['claims']},
            'question': {q['id']: {'version': q['version'], 'revision': q.get('revision')}
                         for q in selection['questions']}}
        if actual != index or len(cards) != len(actual['observation']):
            raise ValueError('Selected record version or question revision mismatch')
        def references(value):
            if isinstance(value, dict):
                if set(value) == {'kind', 'id', 'version'}:
                    yield value
                else:
                    for child in value.values():
                        yield from references(child)
            elif isinstance(value, list):
                for child in value:
                    yield from references(child)
        for reference in references(selection):
            kind = reference['kind']
            if kind in ('source', 'observation', 'claim'):
                if index[kind].get(reference['id']) != reference['version']:
                    raise ValueError('Selected reference version mismatch')
        for source in sources.values():
            if (source['source']['case_id'] != manifest['case_id'] or
                    source['retained_result_hash']['hex'] != source['source']['source_version'] or
                    source['retained_result_hash']['algorithm'] != 'sha256' or
                    source['retained_result_hash']['object'] != 'retained_result_canonical_json'):
                raise ValueError('Retained source identity mismatch')
        for card in cards:
            if card['case_id'] != manifest['case_id'] or card['snapshot_id'] != manifest['snapshot_id']:
                raise ValueError('Card case or snapshot mismatch')
            source = sources[card['source']['source_id']]
            if source['source'] != card['source'] or source['source']['case_id'] != manifest['case_id']:
                raise ValueError('Source identity mismatch')
            if receipts[card['native_receipt_id']]['result_id'] != card['source']['source_id']:
                raise ValueError('Receipt identity mismatch')
            span = card['retained_span']
            chunk = chunks[span['chunk_id']]
            if (chunk['source_ref'] != card['source_ref'] or
                    chunk['field_pointer'] != span['field_pointer'] or
                    span['coordinate_space'] != 'retained_utf8_scalar_bytes' or
                    span['encoding'] != 'utf-8' or chunk['encoding'] != 'utf-8'):
                raise ValueError('Scalar identity or coordinates mismatch')
            raw = archive.read(chunk['path'])
            raw.decode('utf-8')
            if (chunk['hash']['object'] != 'retained_utf8_scalar' or
                    chunk['hash']['algorithm'] != 'sha256' or
                    card['content_hash'] != chunk['hash'] or
                    hashlib.sha256(raw).hexdigest() != chunk['hash']['hex']):
                raise ValueError('Scalar hash mismatch')
            start, end = span['byte_start'], span['byte_end']
            if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(raw):
                raise ValueError('Invalid UTF-8 scalar span')
            raw[:start].decode('utf-8')
            literal = raw[start:end].decode('utf-8')
            excerpt = archive.read(card['excerpt_path'])
            if (literal != span['literal'] or excerpt != literal.encode('utf-8') or
                    span['hash']['object'] != 'retained_utf8_span' or
                    span['hash']['algorithm'] != 'sha256' or
                    hashlib.sha256(excerpt).hexdigest() != span['hash']['hex']):
                raise ValueError('Excerpt differs from retained scalar')
        return {'verified_cards': len(cards), 'snapshot_id': manifest['snapshot_id'],
                'original_source_verification': 'not_available',
                'full_retained_result_verification': 'not_available'}


if __name__ == '__main__':
    print(json.dumps(verify(sys.argv[1]), ensure_ascii=False, sort_keys=True))
'''


def _receipt(case, event):
    data = event['data']
    started = data.get('started_id')
    start = case.event(started) if started else None
    matched = bool(start and start['kind'] == 'tool_start' and
                   start.get('session') == event.get('session') and
                   start['data'].get('tool') == data.get('tool'))
    native = start['data'].get('native_call') if matched else None
    # Never copy start arguments, environment, provider configuration, or native
    # request envelopes. Only actual execution identities and scalar status.
    return {'receipt_id': event['id'], 'result_id': event['id'],
            'result_kind': event['kind'], 'tool': data['tool'],
            'started_id': started, 'start_status': 'matched' if matched else 'unavailable',
            'native_call': {k: native[k] for k in ('session_id', 'turn_id', 'tool_call_id', 'api_request_id')
                            if isinstance(native, dict) and isinstance(native.get(k), str)} if native else None,
            'execution': {k: data['execution'][k] for k in ('state', 'exit_code', 'termination_confirmed')
                          if k in data.get('execution', {}) and
                          type(data['execution'][k]) in (str, int, bool, type(None))},
            'meaning': 'Retained tool result identity; missing start/native IDs remain unavailable. Not proof of execution on the investigated host.'}


def _source_identity(case_id, source, data):
    provenance = data.get('provenance')
    declared = provenance.get('source_role') if isinstance(provenance, dict) else None
    generated = (declared == 'generated_report' or data.get('source_role') == 'generated_report' or
                 data.get('source_kind') == 'generated_report' or data.get('generated_report') is True)
    return {'case_id': case_id, 'source_id': source['id'], 'source_version': source['version'],
            'source_role': 'generated_report' if generated else 'derived',
            'native_source_role': source['source_role'],
            'generation_group': source.get('source_generation_group')}


def _original_locator(identity, data, field_pointer=None):
    generated = identity['source_role'] == 'generated_report'
    image = data.get('tool') == 'forsic_image_files'
    row = {}
    if field_pointer and field_pointer.startswith(('/lines/', '/matches/')):
        from .model_pages import select
        candidate = select(data, field_pointer.rsplit('/', 1)[0])
        if isinstance(candidate, dict):
            row = candidate
    path = row.get('file_path') or row.get('path') or data.get('file_path') or data.get('path')
    line = row.get('line')
    member = data.get('member') if isinstance(data.get('member'), dict) else {}
    original_range = None
    if 'base64' in data and type(data.get('byte_start')) is int and type(data.get('byte_end')) is int:
        original_range = {'coordinate_space': 'archive_member_bytes' if member else 'original_file_bytes',
                          'byte_start': data['byte_start'], 'byte_end': data['byte_end'],
                          'meaning': 'Range returned by the original acquisition receipt, not the excerpt UTF-8 scalar range.'}
    return {'availability': 'not_retained',
            'kind': 'generated_document' if generated else 'image_member' if image else 'original_file' if path else 'unavailable',
            'source_ref': {k: identity[k] for k in ('case_id', 'source_id', 'source_version')},
            'path': path if isinstance(path, str) else None,
            'image_path': data.get('path') if image and isinstance(data.get('path'), str) else None,
            'volume_offset': data.get('volume_offset') if type(data.get('volume_offset')) is int else None,
            'inode': data.get('inode') if type(data.get('inode')) is int else None,
            'member': {k: member[k] for k in ('member_index', 'name', 'header_offset', 'data_offset')
                       if k in member and type(member[k]) in (str, int)} or None,
            'returned_original_range': original_range,
            'line_number': line if type(line) is int else None,
            'record_id': str(line) if type(line) is int else None,
            'coordinate_space': 'original_record_id' if type(line) is int else 'not_available',
            'byte_start': None, 'byte_end': None,
            'explanation': 'Original bytes are not included or re-read. Known receipt navigation and returned ranges are separate from excerpt coordinates. Retained scalar offsets are never original-file/image offsets.'}


def _coverage(source, data):
    from .measurement import scope_view
    returned = scope_view(data)
    partial = (any(data.get(k) is True for k in ('truncated', 'preview_truncated')) or
               bool(data.get('next_cursor')) or any(data.get(k) is False for k in
               ('complete_content', 'complete_file', 'complete_member', 'coverage_complete')))
    return {'status': 'partial' if partial else 'unknown', 'scope_id': source['id'],
            'description': 'Retained returned measurement only; original acquisition scope completeness is not established.',
            'original_scope_completeness': 'unknown', 'returned_measurement': returned,
            'search': {'performed': data.get('tool') == 'forsic_search' or
                       data.get('tool') == 'forsic_image_files' and 'matches' in data,
                       'complete': data.get('search_complete') is True,
                       'truncated': data.get('truncated') is True,
                       'has_next_cursor': bool(data.get('next_cursor'))}}


def export_evidence(case, state, output_path, *, redact=()):
    """Write a new deterministic ZIP from an already selected immutable state.

    Redacted reader reports must omit this attachment. Literal reader masking
    does not make retained scalars safe derivatives, so masked export is refused.
    Return file metadata for the parent report manifest. Never mutate the state
    or ledger, execute tools, or reopen original evidence.
    """
    from .host import source_record, pointer, find_refs
    from ..notes import EVIDENCE_TOOLS

    if redact:
        raise ValueError('Selected evidence export is unavailable for redacted reports; omit the evidence ZIP to prevent disclosure of retained originals')
    case_id = case.config['case_id']
    if state['meta']['case_id'] != case_id:
        raise ValueError('Evidence export state belongs to another case')
    output_path = Path(output_path)
    if (output_path.name != 'selected-evidence.zip' or output_path.is_symlink() or
            not output_path.parent.resolve().is_relative_to(case.output.resolve())):
        raise ValueError('Evidence export must be selected-evidence.zip within report staging')
    snapshot_id = state['meta']['snapshot_id']
    selected = deepcopy(state)
    current_selection, _ = _separate_frozen(selected)
    registries = {kind: {r['id']: r for r in selected.get(field, [])}
                  for kind, field in (('source', 'sources'), ('observation', 'observations'), ('claim', 'claims'))}
    for _, reference in find_refs(current_selection):
        if reference['kind'] in registries:
            record = registries[reference['kind']].get(reference['id'])
            if record is None or record['version'] != reference['version']:
                raise ValueError('Evidence export has a missing or changed selected reference: ' + reference['id'])

    members = {'verify.py': VERIFIER.encode('utf-8'),
               'README.txt': (
                   'Selected retained evidence export\n\n'
                   'Extract verify.py and run: python3 verify.py selected-evidence.zip\n'
                   'The verifier reads the ZIP without extracting its other members.\n'
                   'It checks member SHA-256 digests, selected record references and revisions,\n'
                   'retained UTF-8 scalar digests, and exact half-open excerpt byte ranges.\n'
                   'The report manifest supplies the trusted SHA-256 of this ZIP.\n'
                   'Integrity does not prove authenticity, factual truth, or semantic support.\n'
                   'Original files, complete native results, request arguments, and native\n'
                   'provider configuration are not exported. Only selected scalar fields and safe receipt identities\n'
                   'are included. Complete-result hashes identify retained versions but cannot\n'
                   'be recomputed from this narrowed export. Original provenance stays unknown\n'
                   'unless explicitly declared, and generated reports remain generated reports.\n'
                   'report-slot-declarations.json preserves frozen analyst report selections,\n'
                   'their chosen states, exact saved refs and current recheck indications. Its\n'
                   'file hash is checked; those historical refs are not assertions that current\n'
                   'evidence matches their saved versions and are excluded from current graph verification.\n'
                   'historical-bindings.json preserves all original frozen report, forensic and\n'
                   'temporal declarations by their JSON pointer. Its hash/identity are verified;\n'
                   'saved reference versions are historical bindings, not current evidence claims.\n'
                   'Selected scalar evidence can contain sensitive text; redacted reader reports\n'
                   'omit this ZIP rather than attaching unredacted retained evidence.\n'
               ).encode('utf-8')}
    sources, receipts, chunks, cards = [], [], [], []
    events, identities, labels = {}, {}, {}
    for number, source in enumerate(sorted(selected.get('sources', []), key=lambda r: r['id']), 1):
        event = case.event(source['id'])
        if (not event or event['kind'] != 'tool_result' or event['data'].get('tool') not in EVIDENCE_TOOLS or
                source_record(event)['version'] != source['version'] or
                source.get('content_sha256') != source['version']):
            raise ValueError('Selected retained source is missing or changed: ' + source['id'])
        data = event['data']
        events[source['id']] = event
        identity = _source_identity(case_id, source, data)
        identities[source['id']] = identity
        labels[source['id']] = 'S%03d' % number
        receipts.append(_receipt(case, event))
        sources.append({'source': identity, 'reader_label': labels[source['id']],
                        'retained_result_hash': _hash(_encoded(data), 'retained_result_canonical_json'),
                        'retained_result_payload': 'not_exported',
                        'provenance_status': 'generated_report_declared' if identity['source_role'] == 'generated_report' else 'original_provenance_unknown',
                        'original_locator': _original_locator(identity, data),
                        'coverage': _coverage(source, data), 'native_receipt_id': event['id']})
    scalar_index = {}
    for number, observation in enumerate(sorted(selected.get('observations', []), key=lambda r: r['id']), 1):
        source_ref = observation['source_ref']
        event = events.get(source_ref['id'])
        if not event or source_ref['version'] != identities[source_ref['id']]['source_version']:
            raise ValueError('Observation source is missing or changed: ' + observation['id'])
        value = pointer(event['data'], observation['field_pointer'])
        raw = value.encode('utf-8')
        literal = observation['literal'].encode('utf-8')
        start, end = observation['byte_start'], observation['byte_end']
        if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(raw) or
                end - start != len(literal) or raw[start:end] != literal or
                hashlib.sha256(literal).hexdigest() != observation['canonical_sha256']):
            raise ValueError('Observation is not the exact retained UTF-8 scalar excerpt: ' + observation['id'])
        raw[:start].decode('utf-8')
        key = (source_ref['id'], observation['field_pointer'])
        if key not in scalar_index:
            label = labels[source_ref['id']]
            index = 1 + sum(c['source_ref']['id'] == source_ref['id'] for c in chunks)
            chunk_id = label + '-%03d' % index
            path = 'retained/' + chunk_id + '.txt'
            members[path] = raw
            chunk = {'chunk_id': chunk_id, 'path': path, 'source_ref': deepcopy(source_ref),
                     'field_pointer': observation['field_pointer'], 'encoding': 'utf-8',
                     'coordinate_space': 'retained_utf8_scalar_bytes',
                     'hash': _hash(raw, 'retained_utf8_scalar')}
            chunks.append(chunk)
            scalar_index[key] = chunk
        chunk = scalar_index[key]
        reader_label = 'E%03d' % number
        excerpt_path = 'excerpts/' + reader_label + '.txt'
        members[excerpt_path] = literal
        identity = identities[source_ref['id']]
        source = registries['source'][source_ref['id']]
        from .measurement import acquisition_context
        cards.append({'schema_version': 'forsic-evidence-card-1', 'case_id': case_id,
                      'snapshot_id': snapshot_id, 'card_id': observation['id'], 'card_revision': 1,
                      'observation_version': observation['version'], 'reader_label': reader_label,
                      'source': identity, 'source_ref': deepcopy(source_ref),
                      'content_hash': chunk['hash'], 'excerpt_path': excerpt_path,
                      'original_locator': _original_locator(identity, event['data'], observation['field_pointer']),
                      'retained_span': {'chunk_id': chunk['chunk_id'], 'field_pointer': observation['field_pointer'],
                                        'encoding': 'utf-8', 'coordinate_space': 'retained_utf8_scalar_bytes',
                                        'byte_start': start, 'byte_end': end, 'literal': observation['literal'],
                                        'hash': _hash(literal, 'retained_utf8_span')},
                      'native_receipt_id': event['id'], 'coverage': _coverage(source, event['data']),
                      'semantics': {'presented_view': observation['presented_view'],
                                    'interpretation_limit': observation['interpretation_limit'],
                                    'acquisition_context': acquisition_context(event['data'], observation['field_pointer']),
                                    'semantic_approval': False},
                      'redaction': {'state': 'none', 'parent_card_ref': None, 'method': None,
                                    'redacted_fields': [], 'parent_content_hash': None}})
    # Apply the original export whitelist before capturing frozen fields. A
    # coincidentally named key in excluded native tool arguments is not a
    # report declaration and must never be hoisted into the attachment.
    selection, frozen_bindings = _separate_frozen({key: selected.get(key, []) for key in
                 ('questions', 'claims', 'hypotheses', 'gaps', 'assessments', 'timeline')})
    # Explicit whitelist: no whole case state, original directory, tool arguments,
    # or start envelopes. These records preserve adopted/counter refs and limits.
    selection.update(case_id=case_id, snapshot_id=snapshot_id,
                     selected_state_sha256=hashlib.sha256(_encoded(selected)).hexdigest())
    for name, value in (('sources.json', sources), ('receipts.json', receipts), ('chunks.json', chunks),
                        ('cards.json', cards), ('selection.json', selection)):
        members[name] = _encoded(value)
    from .report_slots import project_report_slots
    declaration_role = 'frozen_analyst_declarations_not_current_evidence'
    members['report-slot-declarations.json'] = _encoded({
        'schema': 'forsic-report-slot-declarations-1', 'case_id': case_id,
        'snapshot_id': snapshot_id, 'role': declaration_role,
        'current_reference_verification': False,
        'verification_scope': 'Member hash and case/snapshot identity only. Saved analyst states, claim details and immutable references are historical declarations; needs_recheck describes current applicability, not semantic approval.',
        'projection': project_report_slots(selected)})
    bindings_role = 'frozen_analyst_bindings_not_current_evidence'
    members['historical-bindings.json'] = _encoded({
        'schema': 'forsic-historical-bindings-1', 'case_id': case_id, 'snapshot_id': snapshot_id,
        'role': bindings_role, 'current_reference_verification': False,
        'verification_scope': 'Member hash and case/snapshot identity only. JSON pointers identify original frozen analyst declarations and derived recheck context; saved reference versions are not certified against the current evidence graph.',
        'bindings': frozen_bindings})
    manifest = {'schema': SCHEMA, 'case_id': case_id, 'snapshot_id': snapshot_id,
                'historical_report_declarations': {'file': 'report-slot-declarations.json', 'role': declaration_role},
                'historical_bindings': {'file': 'historical-bindings.json', 'role': bindings_role},
                'ledger_cutoff': state['meta'].get('ledger_cutoff'),
                'question_ids': sorted(q['id'] for q in selected.get('questions', [])),
                'card_count': len(cards), 'source_count': len(sources),
                'selection_index': {
                    'source': {s['id']: s['version'] for s in selected.get('sources', [])},
                    'observation': {o['id']: o['version'] for o in selected.get('observations', [])},
                    'claim': {c['id']: c['version'] for c in selected.get('claims', [])},
                    'question': {q['id']: {'version': q['version'], 'revision': q.get('revision')}
                                 for q in selected.get('questions', [])}},
                'redaction': 'none', 'original_source_verification': 'not_available',
                'full_retained_result_verification': 'not_available',
                'verification_scope': 'Hashes and exact UTF-8 excerpts of exported retained scalars. Canonical full-result hashes identify retained versions; omitted full results and original bytes cannot be independently verified by this package. Hash integrity is not authenticity or semantic truth.',
                'files': [{'name': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                          for name, raw in sorted(members.items())]}
    members['manifest.json'] = _encoded(manifest)
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(members.items()):
            item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o600 << 16
            item.create_system = 3
            archive.writestr(item, raw)
    raw = result.getvalue()
    # Exclusive creation avoids overwriting a prior immutable artifact/symlink.
    fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
    return {'name': output_path.name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
            'schema': SCHEMA, 'status': 'complete', 'snapshot_id': snapshot_id,
            'card_count': len(cards), 'source_count': len(sources)}
