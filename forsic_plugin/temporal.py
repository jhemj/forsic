"""Analyst-declared time bounds and relations, anchored to retained coordinates.

No year, timezone, event meaning, probability or filesystem semantics are inferred.
Only exact provenance and structural time constraints are checked here.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import re

MAX_EVENTS = 100
MAX_REFS = 24
MAX_RELATIONS = 16
MAX_CANDIDATES = 12


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


def temporal_schema():
    text = {'type': 'string', 'minLength': 1, 'maxLength': 2000}
    endpoint = {'type': ['string', 'null'], 'maxLength': 64,
                'description': 'Analyst bound: offset-dated ISO time or null if unknown; never guess source time.'}
    basis = {'type': 'object', 'additionalProperties': False,
        'properties': {'evidence_id': {**text, 'maxLength': 128}, 'pointer': {**text, 'maxLength': 256},
            'literal': {**text, 'maxLength': 2048}, 'byte_start': {'type': 'integer', 'minimum': 0},
            'value': {'type': ['string', 'number', 'boolean', 'null'], 'maxLength': 2048},
            'source_version': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
            'source_role': {'type': 'string', 'enum': ['retained_tool_result', 'derived_command_output']}},
        'required': ['evidence_id', 'pointer'],
        'oneOf': [{'required': ['literal', 'byte_start']}, {'required': ['value']}],
        'description': 'Exact retained scalar value or UTF-8 literal span; program binds its source version.'}
    refs = {'type': 'array', 'items': basis, 'minItems': 1, 'maxItems': MAX_REFS}
    candidate = {'type': 'object', 'additionalProperties': False,
        'properties': {'earliest': endpoint, 'latest': endpoint, 'basis': text},
        'required': ['earliest', 'latest', 'basis']}
    relation = {'type': 'object', 'additionalProperties': False,
        'properties': {'target_event_ref': {'type': 'string', 'pattern': '^TE-[A-Za-z0-9._-]{1,80}$'},
            'relation_ref': {'type': 'string', 'pattern': '^TR-[a-f0-9]{32}$'},
            'relation': {'type': 'string', 'enum': ['before', 'after', 'overlap', 'same_boot']},
            'reason': text, 'basis_refs': refs, 'clock_alignment': text,
            'target_note_id': {**text, 'maxLength': 128}, 'target_note_revision': {'type': 'integer', 'minimum': 1}},
        'required': ['target_event_ref', 'relation', 'reason'],
        'description': 'Relative constraint; cross-question targets need exact current target_note_id/revision.'}
    return {'type': 'object', 'additionalProperties': False,
        'properties': {
            'observed_or_inferred': {'type': 'string', 'enum': ['observed', 'inferred']},
            'timestamp_role': {'type': 'string', 'enum': ['source_timestamp', 'event_occurrence']},
            'clock_domain': text, 'timezone_basis': text, 'year_basis': text,
            'earliest': endpoint, 'latest': endpoint,
            'candidates': {'type': 'array', 'items': candidate, 'maxItems': MAX_CANDIDATES},
            'assumptions': {'type': 'array', 'items': text, 'maxItems': 16},
            'confidence_basis': text,
            'confidence': {'type': 'string', 'enum': ['low', 'moderate', 'high', 'undetermined']},
            'limitations': {'type': 'array', 'items': text, 'maxItems': 16},
            'filesystem_basis': text, 'boot_scope': text,
            'basis_refs': refs, 'relations': {'type': 'array', 'items': relation, 'maxItems': MAX_RELATIONS}},
        'required': ['observed_or_inferred', 'timestamp_role', 'confidence_basis'],
        'description': 'Optional analyst time model, separate from raw source time; source time alone does not prove event occurrence.'}


def _text(value, name, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(name + ' must be nonempty bounded text')
    return value


def _list(value, name, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(name + ' exceeds its bounded array contract')
    return value


def _instant(value):
    if value is None:
        return None
    _text(value, 'time bound', 64)
    if not re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}', value):
        raise ValueError('A time bound needs a full date and explicit UTC offset; use null for unknown')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError('missing offset')
        result.utcoffset()
        return result
    except (ValueError, OverflowError) as exc:
        raise ValueError('A time bound needs a valid full date and explicit UTC offset') from exc


def _bounds(value):
    if 'earliest' not in value or 'latest' not in value:
        raise ValueError('Declare earliest/latest explicitly, using null for unknown')
    first, last = _instant(value['earliest']), _instant(value['latest'])
    if first is not None and last is not None and first > last:
        raise ValueError('earliest must not exceed latest; preserve competing observations as separate candidates')
    return first, last


def _ref(case, value, evidence_ids):
    from .notes import sources
    if not isinstance(value, dict):
        raise ValueError('Temporal basis must be an exact evidence coordinate')
    allowed = {'evidence_id', 'pointer', 'literal', 'byte_start', 'value', 'source_version', 'source_role'}
    if set(value) - allowed:
        raise ValueError('Unknown temporal basis fields')
    eid = _text(value.get('evidence_id'), 'basis evidence_id', 128)
    pointer = _text(value.get('pointer'), 'basis pointer', 256)
    if eid not in evidence_ids:
        raise ValueError('Temporal basis must reference a selected evidence_id')
    source = sources(case, [eid])[0]['data']
    version = _hash(source)
    if value.get('source_version', version) != version:
        raise ValueError('Temporal basis source_version changed; review this dependent time assertion')
    if not pointer.startswith('/'):
        raise ValueError('Temporal basis pointer must select a retained scalar')
    selected = source
    try:
        for token in pointer[1:].split('/'):
            if re.search(r'~(?![01])', token):
                raise ValueError('invalid JSON pointer escape')
            token = token.replace('~1', '/').replace('~0', '~')
            selected = selected[int(token)] if isinstance(selected, list) and re.fullmatch(r'0|[1-9]\d*', token) else selected[token]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError('Temporal basis pointer is not present in the retained result') from exc
    result = deepcopy(value)
    if 'literal' in value:
        literal = _text(value['literal'], 'basis literal', 2048)
        start = value.get('byte_start')
        if 'value' in value or not isinstance(selected, str) or type(start) is not int or start < 0:
            raise ValueError('A temporal literal needs an exact UTF-8 span, separate from value')
        raw = literal.encode('utf-8')
        if selected.encode('utf-8')[start:start + len(raw)] != raw:
            raise ValueError('Temporal basis literal does not match the retained coordinate')
    else:
        exact = value.get('value')
        if 'value' not in value or 'byte_start' in value or isinstance(exact, (dict, list)):
            raise ValueError('Temporal basis needs an exact scalar value or literal span')
        if isinstance(exact, str) and len(exact) > 2048:
            raise ValueError('Temporal basis scalar exceeds its bounded size')
        if isinstance(exact, float) and not math.isfinite(exact):
            raise ValueError('Temporal basis must be a finite scalar')
        if isinstance(selected, (dict, list)) or selected != exact or isinstance(selected, bool) != isinstance(exact, bool):
            raise ValueError('Temporal basis value does not match the retained scalar')
    role = 'derived_command_output' if source['tool'] == 'forsic_shell' else 'retained_tool_result'
    if value.get('source_role', role) != role:
        raise ValueError('Temporal basis cannot change the retained source role')
    result.update(source_version=version, source_role=role)
    return result


def event_identity(row):
    """Stable within the owning question, independent of proposed time bounds."""
    if row.get('event_ref') is not None:
        value = row['event_ref']
        if not isinstance(value, str) or not re.fullmatch(r'TE-[A-Za-z0-9._-]{1,80}', value):
            raise ValueError('event_ref must be a bounded TE-* identifier')
        return value
    anchor = row.get('time_source')
    if not anchor:
        refs = (row.get('temporal') or {}).get('basis_refs', [])
        anchor = {k: refs[0][k] for k in ('evidence_id', 'pointer', 'literal', 'byte_start', 'value') if k in refs[0]} if refs else None
    if not anchor:
        raise ValueError('A temporal event needs retained timestamp or selected evidence coordinates')
    return 'TE-' + _hash(anchor)[:32]


def _refs(case, values, ids):
    _list(values, 'basis_refs', MAX_REFS)
    if not values:
        raise ValueError('A temporal assertion or relation needs selected evidence basis')
    return [_ref(case, value, ids) for value in values]


def _target_note(case, relation):
    _text(relation['target_note_id'], 'target_note_id', 128)
    revision = relation.get('target_note_revision')
    if type(revision) is not int or revision < 1:
        raise ValueError('Cross-question time relations need the exact current target note revision')
    with case.connect() as db:
        selected = db.execute("SELECT data FROM events WHERE kind='note' AND json_extract(data,'$.note_id')=? ORDER BY rowid DESC LIMIT 1",
                              (relation['target_note_id'],)).fetchone()
    if selected is None:
        raise ValueError('Temporal relation target note is not present in this case')
    selected = json.loads(selected['data'])
    if selected['revision'] != revision:
        raise ValueError('Temporal relation target note changed; select its current revision')
    matches = []
    for item in selected.get('timeline', []):
        if item.get('time_source') or item.get('temporal') or item.get('event_ref'):
            if event_identity(item) == relation['target_event_ref']:
                matches.append(item)
    if len(matches) != 1:
        raise ValueError('Temporal relation target event is not present in the selected current note')
    return matches[0]


def annotate(rows, case):
    """Validate optional assertions in one timeline batch; old rows stay unchanged.

    Semantic contradictions are preserved as recheck hints; malformed single
    intervals, stale coordinates and missing relation targets reject the write.
    """
    if not any('temporal' in row or 'event_ref' in row for row in rows):
        return rows
    _list(rows, 'timeline events', MAX_EVENTS)
    if case is None:
        raise ValueError('Temporal assertions require a case with retained selected evidence')
    result = deepcopy(rows)
    registry, external_targets = {}, {}
    for row in result:
        if not (row.get('time_source') or 'temporal' in row or 'event_ref' in row):
            continue
        eid = event_identity(row)
        if eid in registry:
            raise ValueError('Distinct timeline events need distinct stable event_ref values')
        registry[eid] = row
        if 'temporal' in row or 'event_ref' in row:
            row['event_ref'] = eid
    for row in result:
        if 'temporal' not in row:
            continue
        value = row['temporal']
        if not isinstance(value, dict) or set(value) - set(temporal_schema()['properties']):
            raise ValueError('Unknown temporal assertion fields')
        required = temporal_schema()['required']
        if any(key not in value for key in required):
            raise ValueError('Temporal assertion needs explicit scope, bases, bounds and assumptions')
        explicit = set(value)
        for key in ('clock_domain', 'timezone_basis', 'year_basis'):
            value.setdefault(key, 'unknown')
        value.setdefault('earliest', None); value.setdefault('latest', None)
        value.setdefault('assumptions', [])
        for key in ('clock_domain', 'timezone_basis', 'year_basis', 'confidence_basis'):
            _text(value[key], key)
        if value['observed_or_inferred'] not in ('observed', 'inferred') or value['timestamp_role'] not in ('source_timestamp', 'event_occurrence'):
            raise ValueError('Declare observed_or_inferred and timestamp_role explicitly')
        if value['timestamp_role'] == 'event_occurrence' and value['observed_or_inferred'] != 'inferred':
            raise ValueError('Source timestamps do not directly establish event occurrence; declare an inference')
        if value.get('confidence', 'undetermined') not in ('low', 'moderate', 'high', 'undetermined'):
            raise ValueError('Confidence is an analyst label with a basis, never a numeric probability')
        for key in ('assumptions', 'limitations'):
            for text in _list(value.get(key, []), key, 16):
                _text(text, key)
        for key in ('filesystem_basis', 'boot_scope'):
            if key in value:
                _text(value[key], key)
        first, last = _bounds(value)
        candidates = _list(value.get('candidates', []), 'time candidates', MAX_CANDIDATES)
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) != {'earliest', 'latest', 'basis'}:
                raise ValueError('A time candidate needs earliest/latest and an explicit basis')
            _bounds(candidate); _text(candidate['basis'], 'candidate basis')
        if value['observed_or_inferred'] == 'inferred' and (first is not None or last is not None or candidates):
            origin = row.get('time_source') or {}
            if value['timestamp_role'] == 'source_timestamp' and origin.get('field') in ('mtime', 'ctime', 'atime'):
                if 'timezone_basis' not in explicit:
                    value['timezone_basis'] = 'Selected parser field is a Unix epoch; proposed bounds carry explicit offsets.'
                if 'year_basis' not in explicit:
                    value['year_basis'] = 'Selected filesystem timestamp Unix epoch.'
                if not value['assumptions']:
                    value['assumptions'] = ['The selected parser field represents a filesystem Unix epoch, not event occurrence.']
            elif not {'timezone_basis', 'year_basis'}.issubset(explicit) or not value['assumptions']:
                raise ValueError('Inferred absolute time needs explicit timezone/year bases and assumptions')
        if value['observed_or_inferred'] == 'observed':
            observed = _instant(row.get('time')) if row.get('time_source') and row.get('comparable', True) else None
            if candidates or any(bound is not None and (observed is None or bound != observed) for bound in (first, last)):
                raise ValueError('Changed source time or candidate dates must be declared inferred, not observed')
        origin = row.get('time_source')
        if origin:
            from .notes import sources
            source = sources(case, [origin['evidence_id']])[0]['data']
            if origin['field'] == 'log':
                selected = {key: origin[key] for key in ('evidence_id', 'pointer', 'literal', 'byte_start')}
            else:
                from .notes import file_time
                timestamp, pointer = file_time(source, origin)
                selected = {'evidence_id': origin['evidence_id'], 'pointer': pointer, 'value': timestamp}
            row['literal_timestamp_ref'] = _ref(case, selected, row.get('evidence_ids', []))
        selected_refs = value.get('basis_refs')
        if selected_refs is None and row.get('literal_timestamp_ref'):
            selected_refs = [row['literal_timestamp_ref']]
        value['basis_refs'] = _refs(case, selected_refs, row.get('evidence_ids', []))
        for relation in _list(value.get('relations', []), 'time relations', MAX_RELATIONS):
            allowed = set(temporal_schema()['properties']['relations']['items']['properties'])
            if not isinstance(relation, dict) or set(relation) - allowed:
                raise ValueError('Unknown temporal relation fields')
            target = relation.get('target_event_ref')
            if not isinstance(target, str) or not re.fullmatch(r'TE-[A-Za-z0-9._-]{1,80}', target):
                raise ValueError('A temporal relation needs a stable target_event_ref')
            if relation.get('relation') not in ('before', 'after', 'overlap', 'same_boot'):
                raise ValueError('Unsupported temporal relation')
            _text(relation.get('reason'), 'relation reason')
            if 'clock_alignment' in relation:
                _text(relation['clock_alignment'], 'clock_alignment')
            relation['basis_refs'] = _refs(case, relation.get('basis_refs', value['basis_refs']), row.get('evidence_ids', []))
            target_row = registry.get(target)
            if relation.get('target_note_id') is not None:
                target_row = _target_note(case, relation)
                external_targets[(relation['target_note_id'], target)] = target_row
            elif 'target_note_revision' in relation:
                raise ValueError('target_note_revision requires an explicitly selected target_note_id')
            if target_row is None:
                raise ValueError('Temporal relation must select a current batch event or explicit other note event')
            relation_ref = 'TR-' + _hash({'source': row['event_ref'], 'target': target,
                'target_note_id': relation.get('target_note_id'), 'relation': relation['relation']})[:32]
            if relation.get('relation_ref', relation_ref) != relation_ref:
                raise ValueError('Temporal relation_ref does not match its stable selected endpoints')
            relation['relation_ref'] = relation_ref
    conflicts = constraint_conflicts(result, external_targets=external_targets)
    for row in result:
        if 'temporal' in row:
            row['temporal_conflicts'] = [c for c in conflicts if row['event_ref'] in c['event_refs']]
    return result


def constraint_conflicts(rows, *, external_targets=None):
    """Bounded structural hints only; never alteration/causality conclusions."""
    registry = {event_identity(row): row for row in rows if row.get('time_source') or 'temporal' in row or 'event_ref' in row}
    edges = {}
    conflicts = []
    external_targets = external_targets or {}
    for source, row in registry.items():
        model = row.get('temporal') or {}
        if model:
            first, last = _bounds(model)
            for candidate in model.get('candidates', []):
                cfirst, clast = _bounds(candidate)
                if (first is not None and clast is not None and clast < first or
                        last is not None and cfirst is not None and cfirst > last):
                    conflicts.append({'event_refs': [source], 'reason': 'candidate_outside_declared_interval', 'requires_recheck': True})
        for relation in model.get('relations', []):
            target = relation['target_event_ref']
            external = relation.get('target_note_id')
            selected = external_targets.get((external, target)) if external else registry.get(target)
            other = (selected or {}).get('temporal') or {}
            kind = relation['relation']
            conflict_refs = {'event_refs': [source] if external else [source, target],
                             'relation_refs': [relation['relation_ref']]}
            if external:
                conflict_refs.update(target_event_ref=target, target_note_id=external)
            if (kind == 'same_boot' and model.get('boot_scope') and other.get('boot_scope')
                    and model['boot_scope'] != other['boot_scope']):
                conflicts.append({**conflict_refs,
                    'reason': 'declared_same_boot_scopes_differ', 'requires_recheck': True})
            if not external and kind in ('before', 'after'):
                a, b = (source, target) if kind == 'before' else (target, source)
                edges.setdefault(a, set()).add(b)
            same_clock = model.get('clock_domain') not in (None, 'unknown') and model.get('clock_domain') == other.get('clock_domain')
            if not other or not same_clock and not relation.get('clock_alignment'):
                continue
            lo, hi = _bounds(model); olo, ohi = _bounds(other)
            contradiction = ((kind == 'before' and lo is not None and ohi is not None and lo >= ohi) or
                (kind == 'after' and hi is not None and olo is not None and hi <= olo) or
                (kind == 'overlap' and ((hi is not None and olo is not None and hi < olo) or
                                       (ohi is not None and lo is not None and ohi < lo))))
            if contradiction:
                conflicts.append({**conflict_refs,
                    'reason': 'declared_relation_conflicts_with_bounds', 'requires_recheck': True})
    # At most MAX_EVENTS vertices/MAX_RELATIONS edges per event; no graph store.
    for start in edges:
        pending, seen = list(edges[start]), set()
        while pending:
            node = pending.pop()
            if node == start:
                conflicts.append({'event_refs': [start], 'reason': 'strict_relative_order_cycle', 'requires_recheck': True})
                break
            if node not in seen:
                seen.add(node); pending.extend(edges.get(node, ()))
    return conflicts


def temporal_projection(row, case=None):
    """Optional TimeAssertion extras plus exact, version-bound basis provenance.

    Root projection owns question namespacing and observation IDs. When case is
    supplied, changed basis versions mark only this dependent event for recheck.
    """
    if 'temporal' not in row:
        return {}
    model = row['temporal']
    conflicts = deepcopy(row.get('temporal_conflicts', []))
    if case is not None:
        refs = model['basis_refs'] + [ref for relation in model.get('relations', []) for ref in relation['basis_refs']]
        if row.get('literal_timestamp_ref'):
            refs = refs + [row['literal_timestamp_ref']]
        for ref in refs:
            try:
                _ref(case, ref, row.get('evidence_ids', []))
            except ValueError as exc:
                conflicts.append({'event_refs': [row['event_ref']], 'reason': 'temporal_basis_changed_or_unavailable',
                                  'source_id': ref['evidence_id'], 'detail': str(exc), 'requires_recheck': True})
        for relation in model.get('relations', []):
            if relation.get('target_note_id'):
                try:
                    _target_note(case, relation)
                except ValueError as exc:
                    conflicts.append({'event_refs': [row['event_ref']], 'relation_refs': [relation['relation_ref']],
                        'reason': 'temporal_relation_target_changed', 'detail': str(exc), 'requires_recheck': True})
    result = {key: deepcopy(model.get(key)) for key in ('observed_or_inferred', 'timestamp_role', 'clock_domain',
        'timezone_basis', 'year_basis', 'earliest', 'latest', 'assumptions', 'confidence_basis')}
    result.update(event_ref=row['event_ref'], temporal_candidates=deepcopy(model.get('candidates', [])),
                  literal_timestamp_ref=deepcopy(row.get('literal_timestamp_ref')),
                  temporal_basis_refs=deepcopy(model['basis_refs']), temporal_relations=deepcopy(model.get('relations', [])),
                  conflicting_constraints=conflicts, temporal_requires_recheck=bool(conflicts),
                  temporal_limitations=deepcopy(model.get('limitations', [])))
    for key in ('confidence', 'filesystem_basis', 'boot_scope'):
        if key in model:
            result[key] = model[key]
    if (row.get('time_source') or {}).get('field') in ('ctime', 'mtime', 'atime'):
        result['temporal_limitations'].append('파일 시각의 의미는 파일시스템·파서에 따라 다릅니다. ctime을 생성 시각이나 사건 발생 시각으로 자동 해석하지 않습니다. 복사·복원 후에도 이전 mtime이 보존될 수 있습니다.')
    return result
