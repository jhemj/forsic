"""Read-only case evidence derived from adopted observations, never report output.

The router supplies verified native ownership. A context is not a bearer token:
it must be freshly verified for every request before calling these functions.
"""
from copy import deepcopy
import base64
import hashlib
import json
from collections import OrderedDict
from contextlib import closing
import sqlite3
from pathlib import Path

from .report_driven.host import current, pointer, find_refs
from .report_driven.evidence_export import _source_identity, _original_locator

MAX_PAGE = 50
MAX_ITEMS = 10000
MAX_ITEM_BYTES = 128 * 1024
MAX_PUBLIC_BYTES = 8 * 1024 * 1024


class CollectionError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def checked_context(case, context):
    if not isinstance(context, dict):
        raise CollectionError('Verified case context required', 403)
    for key in ('case_id', 'profile_id', 'session_id', 'conversation_id'):
        if not isinstance(context.get(key), str) or not context[key]:
            raise CollectionError('Verified ownership context incomplete', 403)
    sessions = context.get('session_ids')
    conversations = context.get('conversations') or {context['conversation_id']: sessions}
    if (context['case_id'] != case.config['case_id'] or not isinstance(sessions, (list, tuple)) or
            context['session_id'] not in sessions or not isinstance(conversations, dict) or
            context['session_id'] not in conversations.get(context['conversation_id'], []) or
            any(not isinstance(s, str) or not s for s in sessions) or
            any(not isinstance(v, (list, tuple)) or not set(v) <= set(sessions) for v in conversations.values())):
        raise CollectionError('Case/session/conversation ownership mismatch', 403)
    return {**{k: deepcopy(context[k]) for k in ('case_id', 'profile_id', 'session_id', 'conversation_id', 'session_ids')}, 'conversations': deepcopy(conversations)}


def _link_index(state):
    registry = {(kind, x['id']): x for kind, field in (
        ('claim', 'claims'), ('observation', 'observations'), ('assessment', 'assessments'),
        ('hypothesis', 'hypotheses'), ('test', 'tests'), ('mission', 'missions'), ('gap', 'gaps'))
        for x in state.get(field, [])}
    result = {}
    def reachable(record):
        seen, found, pending = set(), set(), [record]
        while pending:
            for _, reference in find_refs(pending.pop(), include_frozen=False):
                key = (reference['kind'], reference['id'])
                if key in seen:
                    continue
                seen.add(key)
                if reference['kind'] == 'observation':
                    found.add(reference['id'])
                elif key in registry:
                    pending.append(registry[key])
        return found
    for field in ('questions', 'claims'):
        for record in state.get(field, []):
            if record.get('status') in ('retracted', 'superseded'):
                continue
            linked = {'id': record['id'], 'version': record['version'],
                      'title': record.get('question', record.get('title', record.get('statement', '')))[:240]}
            if field == 'questions':
                linked['revision'] = record.get('revision')
                linked['session_id'] = record.get('session_id', '')
            for ident in reachable(record):
                result.setdefault(ident, {'questions': [], 'claims': [], 'timeline': False})[field].append(linked)
    for time in state.get('timeline', []):
        for ident in reachable(time):
            result.setdefault(ident, {'questions': [], 'claims': [], 'timeline': False})['timeline'] = True
    return result


def _item(case, state, observation, events, links, source_records, scalars, stale=False):
    source_ref = observation.get('source_ref', {})
    event = events.get(source_ref.get('id'))
    from .notes import EVIDENCE_TOOLS
    data = event['data'] if event and isinstance(event.get('data'), dict) else {}
    if data.get('representation') == 'directory_inventory':
        return None
    source = source_records.get(event['id']) if event else None
    valid = bool(source and source['version'] == source_ref.get('version') and
                 event['kind'] == 'tool_result' and data.get('tool') in EVIDENCE_TOOLS and
                 not data.get('error') and not data.get('pending') and
                 data.get('execution', {}).get('state') not in ('failed', 'cancelled', 'running', 'not_started', 'completion_unknown'))
    literal = observation.get('literal', '')
    start, end = observation.get('byte_start'), observation.get('byte_end')
    span = {'field_pointer': observation.get('field_pointer'), 'byte_start': start, 'byte_end': end,
            'coordinate_space': 'retained_utf8_scalar_bytes', 'encoding': 'utf-8'}
    if valid:
        try:
            key = (event['id'], span['field_pointer'])
            if key not in scalars:
                scalars[key] = pointer(event['data'], span['field_pointer']).encode('utf-8')
            raw = scalars[key]
            selected = literal.encode('utf-8')
            valid = (type(start) is int and type(end) is int and 0 <= start <= end <= len(raw) and
                     raw[start:end] == selected and hashlib.sha256(selected).hexdigest() == observation['canonical_sha256'])
            if valid and (start < len(raw) and raw[start] & 0xc0 == 0x80 or end < len(raw) and raw[end] & 0xc0 == 0x80):
                valid = False
        except (ValueError, KeyError, TypeError, UnicodeError):
            valid = False
    # Case identity, version and coordinates, not text equality or row numbering.
    identity = {'case_id': case.config['case_id'], 'source_ref': source_ref, **span}
    ident = 'E-' + digest(identity)[:32]
    sensitive = bool(event and event['data'].get('tool') == 'forsic_sensitive_scan')
    if event and 'sensitive' in str(event['data'].get('schema', '')):
        sensitive = True
    linked = links.get(observation['id'], {})
    if not stale and not linked:
        return None
    questions, claims = ([], []) if stale else (deepcopy(linked['questions']), deepcopy(linked['claims']))
    sessions = {event.get('session', '')} if event else set()
    for q in questions:
        if q.get('session_id'):
            sessions.add(q['session_id'])
    conversations = sorted(s for s in sessions if s)
    # Source and linked-note sessions remain stable across UI conversation changes.
    title = '민감정보 탐지 · 가림 발췌' if sensitive else observation.get('statement') or observation.get('interpretation_limit') or '보존 발췌'
    # Only scanner's safe retained result is used; never private hydration here.
    item = {'evidence_item_id': ident, 'title': str(title)[:240],
            'kind': 'sensitive_detection' if sensitive else 'derived_analysis' if event and event['data'].get('tool') not in ('forsic_read', 'forsic_image_files', 'forsic_search') else 'retained_excerpt',
            'source_ref': deepcopy(source_ref), 'retained_span': span,
            'excerpt': literal if valid else '', 'excerpt_sha256': observation.get('canonical_sha256'),
            'linked_questions': questions, 'linked_claims': claims,
            'conversation_ids': conversations, 'origin_session_ids': conversations, 'currentness': 'stale' if stale else 'current',
            'sensitive': sensitive, 'sensitive_originals': 'restricted_not_included' if sensitive else 'not_applicable',
            'downloadable': valid and not stale, 'download_reason': 'stale_history' if stale else None if valid else 'source_or_excerpt_verification_failed',
            'verification_status': 'exact_retained_excerpt' if valid else 'unavailable',
            'collected_at': event.get('time') if event else None,
            'collection_basis': 'adopted_observation',
            'source_role': _source_identity(case.config['case_id'], source, data)['source_role'] if source else 'unavailable',
            'derivation': {'tool': data.get('tool'),
                           'representation': data.get('representation'),
                           'original_evidence': data.get('original_evidence'),
                           'input_refs': deepcopy(data.get('input_refs', [])) if sensitive else []},
            'observation_refs': [{'id': observation['id'], 'version': observation['version']}],
            'original_locator': None if sensitive or not valid else _original_locator(_source_identity(case.config['case_id'], source, event['data']), event['data'], span['field_pointer']),
            'full_retained_scalar_verification': 'not_available', 'original_source_verification': 'not_available'}
    item['item_version'] = digest(item)
    return item


MAX_LEDGER_BYTES = 16 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_CHANGE_EVENTS = 20000
_CACHE = OrderedDict()


def _stamp(case):
    values = []
    for path in (case.db, Path(str(case.db) + '-wal')):
        try:
            info = path.stat()
            values.append((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        except FileNotFoundError:
            values.append(None)
    return tuple(values)


def _read_projection(case):
    """One SQL read snapshot; unselected result bodies never enter Python.

    Replay inputs and selected source bodies have explicit aggregate and row
    limits. Capacity rejection preserves unknown counts rather than truncating.
    """
    with closing(sqlite3.connect(case.db.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        cutoff = db.execute('SELECT coalesce(max(rowid),0) FROM events').fetchone()[0]
        key = (str(case.db), cutoff, _stamp(case), digest(case.config))
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
        size = db.execute("SELECT count(*),coalesce(sum(length(CAST(data AS BLOB))),0) FROM events WHERE kind IN ('note','report_change')").fetchone()
        if size[0] > MAX_CHANGE_EVENTS or size[1] > MAX_LEDGER_BYTES:
            raise CollectionError('Collection replay exceeds bounded capacity; exact counts unavailable', 413)
        events = []
        for row in db.execute("SELECT rowid,* FROM events WHERE kind IN ('note','report_change') ORDER BY rowid"):
            event = dict(row)
            event['data'] = json.loads(event['data'])
            events.append(event)
        source_ids = set()
        for event in events:
            data = event['data']
            if event['kind'] == 'report_change':
                for obs in data.get('records', {}).get('observations', []):
                    if isinstance(obs.get('source_ref', {}).get('id'), str):
                        source_ids.add(obs['source_ref']['id'])
            else:
                for row in data.get('timeline', []):
                    for _, reference in find_refs(row):
                        if reference['kind'] == 'source':
                            source_ids.add(reference['id'])
                    if row.get('time_source', {}).get('evidence_id'):
                        source_ids.add(row['time_source']['evidence_id'])
        if len(source_ids) > MAX_ITEMS:
            raise CollectionError('Collection source set exceeds bounded capacity', 413)
        used = size[1]
        # Batches avoid SQLite's parameter limit; all use this same read snapshot.
        ordered = sorted(source_ids)
        sources = []
        for offset in range(0, len(ordered), 500):
            ids = ordered[offset:offset + 500]
            clause = ','.join('?' for _ in ids)
            sizes = db.execute("SELECT coalesce(sum(length(CAST(data AS BLOB))),0),coalesce(max(length(CAST(data AS BLOB))),0) FROM events WHERE kind='tool_result' AND id IN (" + clause + ')', ids).fetchone()
            used += sizes[0]
            if used > MAX_LEDGER_BYTES or sizes[1] > MAX_SOURCE_BYTES:
                raise CollectionError('Selected retained source exceeds bounded capacity; exact counts unavailable', 413)
            for row in db.execute("SELECT rowid,* FROM events WHERE kind='tool_result' AND id IN (" + clause + ')', ids):
                event = dict(row)
                event['data'] = json.loads(event['data'])
                sources.append(event)
        events.extend(sources)
        # Receipt starts are projected, never raw arguments/provider envelopes.
        for source in sources:
            ident = source['data'].get('started_id')
            if not isinstance(ident, str):
                continue
            row = db.execute("SELECT rowid,id,time,kind,session,json_object('tool',json_extract(data,'$.tool'),'native_call',json_object('session_id',json_extract(data,'$.native_call.session_id'),'turn_id',json_extract(data,'$.native_call.turn_id'),'tool_call_id',json_extract(data,'$.native_call.tool_call_id'),'api_request_id',json_extract(data,'$.native_call.api_request_id'))) data FROM events WHERE id=? AND kind='tool_start'", (ident,)).fetchone()
            if row:
                event = dict(row)
                event['data'] = json.loads(event['data'])
                events.append(event)
        events.sort(key=lambda e: e['rowid'])
        by_id = {e['id']: e for e in events}
        class FrozenCase:
            def __getattr__(self, name):
                return getattr(case, name)
            def event(self, ident):
                return by_id.get(ident)
        frozen = FrozenCase()
        try:
            state = current(frozen, events=events)
        except (ValueError, TypeError, KeyError):
            raise CollectionError('Evidence ledger projection cannot be verified; exact counts unavailable', 409) from None
        result = (state, events, by_id)
        _CACHE[key] = result
        while len(_CACHE) > 4:
            _CACHE.popitem(last=False)
        return result


def snapshot(case, *, context):
    context = checked_context(case, context)
    state, events_list, events = _read_projection(case)
    links = _link_index(state)
    observations = [(o, False) for o in state['observations'] if o['id'] in links]
    signature = lambda o: digest({k: o.get(k) for k in ('id', 'source_ref', 'field_pointer', 'byte_start', 'byte_end', 'literal')})
    current_signatures = {signature(o) for o in state['observations']}
    for event in events_list:
        if event['kind'] == 'report_change':
            for old in event['data'].get('records', {}).get('observations', []):
                same = signature(old) in current_signatures
                if not same:
                    observations.append((old, True))
    if len(observations) > MAX_ITEMS:
        raise CollectionError('Evidence collection exceeds bounded snapshot capacity', 413)
    merged = {}
    source_records = {x['id']: x for x in state['sources']}
    scalars = {}
    candidate_bytes = 0
    for obs, stale in observations:
        item = _item(case, state, obs, events, links, source_records, scalars, stale)
        if item is None:
            continue
        size = len(encoded(item))
        candidate_bytes += size
        if size > MAX_ITEM_BYTES or candidate_bytes > MAX_PUBLIC_BYTES:
            raise CollectionError('Evidence excerpt or metadata exceeds bounded display capacity; exact counts unavailable', 413)
        key = (item['evidence_item_id'], item['currentness'])
        if key in merged:
            prior = merged[key]
            for field in ('linked_questions', 'linked_claims', 'observation_refs'):
                prior[field] = list({digest(x): x for x in prior[field] + item[field]}.values())
            for field in ('conversation_ids', 'origin_session_ids'):
                prior[field] = sorted(set(prior[field] + item[field]))
        else:
            merged[key] = item
    items = sorted(merged.values(), key=lambda x: (x['evidence_item_id'], x['currentness']))
    public_bytes = 0
    for item in items:
        item_bytes = len(encoded(item))
        public_bytes += item_bytes
        if item_bytes > MAX_ITEM_BYTES or public_bytes > MAX_PUBLIC_BYTES:
            raise CollectionError('Evidence excerpt or metadata exceeds bounded display capacity; exact counts unavailable', 413)
        item['item_version'] = digest({k: v for k, v in item.items() if k != 'item_version'})
    snap_id = digest({'case_id': context['case_id'], 'profile_id': context['profile_id'],
                      'state_snapshot': state['meta']['snapshot_id'], 'items': items})
    return {'source_snapshot_id': snap_id, 'state_snapshot_id': state['meta']['snapshot_id'],
            'case_id': context['case_id'], 'items': items, '_events': events,
            '_context': context, 'ledger_cutoff': state['meta']['ledger_cutoff']}


def list_collection(case, *, context, scope='case', conversation_id=None, kind=None,
                    status='current', search='', cursor=None, page_size=25, source_snapshot_id=None):
    snap = snapshot(case, context=context)
    context = snap['_context']
    if source_snapshot_id and source_snapshot_id != snap['source_snapshot_id']:
        raise CollectionError('Evidence snapshot changed; refresh', 409)
    if scope not in ('case', 'conversation') or status not in ('current', 'stale', 'all'):
        raise CollectionError('Invalid collection filter')
    if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE or not isinstance(search, str) or len(search) > 200:
        raise CollectionError('Invalid page size or search')
    conversation_id = conversation_id or context['conversation_id']
    if scope == 'conversation' and conversation_id not in context['conversations']:
        raise CollectionError('Conversation not owned', 403)
    all_items = snap['items']
    filtered = [x for x in all_items if (status == 'all' or x['currentness'] == status) and
                (not kind or x['kind'] == kind) and (scope == 'case' or bool(set(x['origin_session_ids']) & set(context['conversations'][conversation_id]))) and
                (not search or search.casefold() in (x['title'] + ' ' + x['excerpt']).casefold())]
    binding = digest({'snapshot': snap['source_snapshot_id'], 'context': snap['_context'],
                      'scope': scope, 'conversation': conversation_id, 'kind': kind, 'status': status,
                      'search': search, 'page_size': page_size})
    offset = 0
    if cursor:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor.encode('ascii')))
            offset = decoded['offset']
            if decoded['binding'] != binding or type(offset) is not int or offset < 0 or offset > len(filtered) or offset % page_size:
                raise ValueError()
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise CollectionError('Cursor does not match snapshot and filters', 409)
    next_offset = offset + page_size
    next_cursor = base64.urlsafe_b64encode(encoded({'binding': binding, 'offset': next_offset})).decode('ascii') if next_offset < len(filtered) else None
    counts = {'total': len(all_items), 'downloadable': sum(x['downloadable'] for x in all_items),
              'restricted': sum(x['sensitive'] for x in all_items), 'stale': sum(x['currentness'] == 'stale' for x in all_items),
              'filtered': len(filtered), 'current_conversation': sum(bool(set(x['origin_session_ids']) & set(context['session_ids'])) for x in all_items),
              'exact': True, 'omitted': 0, 'errors': sum(x['verification_status'] == 'unavailable' for x in all_items)}
    return {'case_id': snap['case_id'], 'source_snapshot_id': snap['source_snapshot_id'],
            'selection_digest': digest([(x['evidence_item_id'], x['item_version']) for x in filtered]),
            'items': filtered[offset:next_offset], 'counts': counts, 'next_cursor': next_cursor,
            'offset': offset, 'page_size': page_size,
            'sensitive_original_limits': {'max_findings': 40, 'max_bytes': 32768},
            'disclosure': '선택 발췌만 포함합니다. 원본·보존 scalar 전체 검증은 제공하지 않습니다. 일반 발췌에 미탐지 민감정보가 있을 수 있습니다. 로컬 전용 다운로드입니다.'}
