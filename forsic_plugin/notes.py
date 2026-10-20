"""Small versioned investigation notes, not another execution engine."""
import json
import math
import re
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo


EVIDENCE_TOOLS = {'forsic_read', 'forsic_read_bytes', 'forsic_search', 'forsic_hash',
                  'forsic_list', 'forsic_image_info', 'forsic_image_files', 'forsic_verify', 'forsic_intel', 'forsic_shell'}


def timeline_schema(*, require_source=False):
    """Legacy input remains readable; new question events bind an observed time."""
    string = {'type': 'string'}
    origin = {'type': 'object', 'properties': {
        'evidence_id': string,
        'field': {'type': 'string', 'enum': ['mtime', 'ctime', 'atime', 'log']},
        'entry_path': {**string, 'description': 'Exact returned entry path, required for a directory listing.'},
        'pointer': {**string, 'description': 'For log, exact returned text field, e.g. /lines/0/text.'},
        'literal': {**string, 'description': 'For log, timestamp exactly as written, without an invented year/timezone.'},
        'byte_start': {'type': 'integer', 'minimum': 0,
                       'description': 'UTF-8 start of literal inside pointer, not inside the image.'}},
        'required': ['evidence_id', 'field'],
        'description': 'Retained stat/listing/log timestamp; keep unknown year/timezone unknown.'}
    event = {'type': 'object', 'properties': {
        'time_source': origin, 'description': string,
        'evidence_ids': {'type': 'array', 'items': string}},
        'required': ['description', 'evidence_ids']}
    from .temporal import temporal_schema, MAX_EVENTS
    event['properties'].update(
        event_ref={'type': 'string', 'pattern': '^TE-[A-Za-z0-9._-]{1,80}$',
                   'description': 'Optional stable question-local event ID; preserve across inference corrections.'},
        temporal=temporal_schema())
    if require_source:
        event['anyOf'] = [{'required': ['time_source']},
                         {'required': ['temporal'], 'properties': {'temporal': {'required': ['basis_refs']}}}]
        event['additionalProperties'] = False
        event['properties']['evidence_ids']['minItems'] = 1
        origin['additionalProperties'] = False
        origin['anyOf'] = [
            {'properties': {'field': {'enum': ['log']}}, 'required': ['pointer', 'literal', 'byte_start']},
            {'properties': {'field': {'enum': ['mtime', 'ctime', 'atime']}}}]
    else:
        event['properties']['time'] = {**string, 'description': 'Original log timestamp, not a model-calculated date.'}
    return {'type': 'array', 'items': event, 'maxItems': MAX_EVENTS}


def sources(case, ids):
    rows = []
    for eid in dict.fromkeys(ids):
        event = case.event(eid)
        if (not event or event['kind'] != 'tool_result'
                or event['data'].get('tool') not in EVIDENCE_TOOLS
                or (event['data'].get('error') and event['data'].get('outcome') != 'not_found')
                or event['data'].get('exit_code', 0) != 0):
            raise ValueError(f'No successful source result for evidence_id: {eid}')
        rows.append(event)
    return rows


def epoch_time(value):
    return datetime.fromtimestamp(float(value), ZoneInfo('Asia/Seoul'))


def file_time(source, origin):
    """Resolve one retained stat field or one explicitly selected directory entry."""
    field = origin.get('field')
    if field not in ('mtime', 'ctime', 'atime'):
        raise ValueError('time_source.field must be mtime, ctime or atime')
    entry_path = origin.get('entry_path')
    if 'entries' in source:
        if not entry_path:
            raise ValueError('This evidence is a directory listing, not a single-file stat. '
                             'Set time_source.entry_path to the exact returned entry path for mtime, '
                             'or call forsic_image_files(action=stat, file_path=the target) and cite that result. '
                             'Do not guess a file from the description or drop the unresolved timestamp.')
        matches = [(i, e) for i, e in enumerate(source['entries']) if e.get('path') == entry_path]
        if len(matches) != 1:
            raise ValueError('time_source.entry_path must match exactly one entry in this returned listing page')
        index, entry = matches[0]
        key = field + '_epoch'
        if key not in entry:
            raise ValueError(f'This listing entry has no {field}; use forsic_image_files(action=stat) for that file')
        value, pointer = entry[key], f'/entries/{index}/{key}'
    else:
        if entry_path and entry_path != source.get('file_path'):
            raise ValueError('time_source.entry_path does not match the cited stat file_path')
        if field not in source.get('times', {}):
            raise ValueError(f'The cited result has no stat {field}; cite a successful '
                             'forsic_image_files(action=stat) result, or an exact listing entry_path')
        value, pointer = source['times'][field], '/times/' + field
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('The retained filesystem timestamp is not a finite Unix epoch')
    try:
        epoch_time(value)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError('The retained filesystem timestamp is outside the supported date range') from exc
    return value, pointer


def log_time(source, origin):
    """Resolve a literal timestamp in retained text; never guess year or timezone."""
    path = origin.get('pointer', '')
    if not re.fullmatch(r'/(?:lines/\d+/text|matches/\d+/text|text|stdout)', path):
        raise ValueError('Log time needs a retained text pointer, such as /lines/0/text')
    value = source
    try:
        for key in path[1:].split('/'):
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError('Log timestamp pointer is not present in this result') from None
    literal, start = origin.get('literal'), origin.get('byte_start')
    if (not isinstance(value, str) or not isinstance(literal, str) or not literal.strip()
            or len(literal) > 128 or type(start) is not int or start < 0):
        raise ValueError('Provide the exact timestamp literal and UTF-8 byte_start in the text field')
    raw = literal.encode('utf-8')
    if value.encode('utf-8')[start:start + len(raw)] != raw:
        raise ValueError('Log timestamp does not match the retained source at byte_start')
    # fromisoformat accepts dates and compact forms; only a full dated clock can
    # represent an absolute event instant here. Other forms remain raw records.
    dt = None
    if re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}', literal):
        try:
            parsed = datetime.fromisoformat(literal.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                dt = parsed.astimezone(ZoneInfo('Asia/Seoul'))
        except (ValueError, OverflowError):
            pass
    return literal, dt


def timeline(items, case=None):
    from .temporal import MAX_EVENTS
    if not isinstance(items, list) or len(items) > MAX_EVENTS:
        raise ValueError('timeline must be an array of at most ' + str(MAX_EVENTS) + ' selected events')
    result, seen = [], set()
    for item in items:
        row = dict(item)
        origin = row.get('time_source')
        if origin:
            if not case or origin.get('evidence_id') not in row.get('evidence_ids', []):
                raise ValueError('time_source must reference a cited evidence result')
            source = sources(case, [origin['evidence_id']])[0]['data']
            if origin.get('field') == 'log':
                literal, dt = log_time(source, origin)
                row.update(time=dt.isoformat() if dt else literal, raw_time=literal, comparable=dt is not None,
                           display_time=dt.strftime('%Y-%m-%d %H:%M:%S KST') if dt else literal + ' (연도·시간대 확인 필요)')
            else:
                epoch, _ = file_time(source, origin)
                dt = epoch_time(epoch)
                row.update(time=dt.isoformat(), raw_time=epoch,
                           display_time=dt.strftime('%Y-%m-%d %H:%M:%S KST'))
            raw = row['time']
        else:
            raw = str(row.get('time', ''))
            row['raw_time'] = raw
            try:
                dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            except ValueError:
                dt = None
            row['display_time'] = (dt.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S KST')
                                   if dt and dt.tzinfo else raw + ' (시간대 미확인)')
            if 'temporal' in row and not raw:
                row.update(time=None, raw_time=None, comparable=False,
                           display_time='원문 시각 미제공; 명시한 시간 제약 참조')
        key = (raw, row.get('description'), tuple(sorted(row.get('evidence_ids', []))),
               json.dumps(origin, sort_keys=True, ensure_ascii=False),
               row.get('event_ref'), json.dumps(row.get('temporal'), sort_keys=True, ensure_ascii=False))
        if key not in seen:
            seen.add(key)
            result.append(row)
    from .temporal import annotate
    return annotate(result, case)


def current_notes(case):
    # Keep historical payloads in SQLite: current views only need the newest
    # revision of each note, ordered by the same append order as before.
    with case.connect() as db:
        rows = db.execute("SELECT data FROM events WHERE rowid IN ("
                          "SELECT MAX(rowid) FROM events WHERE kind='note' "
                          "GROUP BY json_extract(data,'$.note_id')) ORDER BY rowid DESC")
        latest = {}
        for row in rows:
            value = json.loads(row['data'])
            latest.setdefault(value['note_id'], value)
    return list(latest.values())


def model_view(result, args):
    """Present a small navigation/acknowledgement view; retained notes stay intact.

    Only the native model handler uses this projection. The case store, web views
    and report builders still receive complete notes and revision history.
    """
    if result.get('error'):
        return result
    action = args.get('action', 'list')
    view = dict(result)
    keys = ('note_id', 'revision', 'question', 'status', 'updated_at')
    if action == 'list':
        view['notes'] = [
            {**{k: n[k] for k in keys if k in n}, 'detail_included': False,
             'counts': {k: len(n.get(k, [])) for k in
                        ('evidence_ids', 'alternatives', 'gaps', 'critical_gaps', 'next_checks', 'timeline')}}
            for n in result.get('notes', [])
        ]
        view['detail_hint'] = 'Index only. Get the selected note before using its answer, counterevidence or gaps.'
    elif action == 'get':
        if 'history_total' in result:
            if not args.get('include_history'):
                view['history_hint'] = 'Current note only. For an explicit revision audit, get with include_history=true and offset/limit.'
            return view
        history = result.get('history', [])
        view.pop('history', None)
        view['history_total'] = len(history)
        if args.get('include_history'):
            offset = max(0, int(args.get('offset', 0)))
            limit = max(1, min(10, int(args.get('limit', 1))))
            end = offset + limit
            view['history'] = history[offset:end]
            view['history_next_offset'] = end if end < len(history) else None
        else:
            view['history_hint'] = 'Current note only. For an explicit revision audit, get with include_history=true and offset/limit.'
    elif action == 'save' and 'note' in result:
        note_value = result['note']
        view['note'] = {k: note_value[k] for k in ('note_id', 'revision', 'updated_at') if k in note_value}
        view['saved'] = True
        view['detail_included'] = False
        view['detail_hint'] = 'Save receipt only, not validation of the conclusion. Get by note_id for the full stored note and normalized timeline.'
    return view


def model_read(case, args):
    """Read one current note and only the explicitly requested history page."""
    if args.get('action', 'list') != 'get':
        return note(case, args)
    note_id = args['note_id']
    where = "kind='note' AND json_extract(data,'$.note_id')=?"
    with case.connect() as db:
        db.execute('BEGIN')
        total = db.execute('SELECT count(*) FROM events WHERE '+where, (note_id,)).fetchone()[0]
        row = db.execute('SELECT rowid,data FROM events WHERE '+where+' ORDER BY rowid DESC LIMIT 1', (note_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown note_id in this case')
        current = json.loads(row['data'])
        result = {'note': current, 'history_total': total,
                  'revision_hint': 'For save, pass note.revision (the current revision, '
                                   'not the next revision). Review the current note and preserve '
                                   'other changes; a successful update increments it automatically.'}
        if args.get('include_history'):
            offset = max(0, int(args.get('offset', 0)))
            limit = max(1, min(10, int(args.get('limit', 1))))
            rows = db.execute('SELECT rowid,data FROM events WHERE '+where+
                              ' ORDER BY rowid DESC LIMIT ? OFFSET ?', (note_id,limit,offset))
            result['history'] = [current if item['rowid'] == row['rowid'] else json.loads(item['data']) for item in rows]
            result['history_next_offset'] = offset + limit if offset + limit < total else None
    return result


def note(case, args):
    action = args.get('action', 'list')
    if action == 'list':
        notes = current_notes(case)
        query = str(args.get('query') or '').casefold()
        if query:
            notes = [n for n in notes if query in json.dumps(n, ensure_ascii=False).casefold()]
        offset, limit = max(0, int(args.get('offset', 0))), max(1, min(50, int(args.get('limit', 20))))
        end = offset + limit
        return {'notes': notes[offset:end], 'total': len(notes),
                'next_offset': end if end < len(notes) else None}
    if action == 'get':
        with case.connect() as db:
            rows = db.execute("SELECT data FROM events WHERE kind='note' AND json_extract(data, '$.note_id')=? ORDER BY rowid DESC", (args['note_id'],)).fetchall()
        if not rows:
            raise ValueError('Unknown note_id in this case')
        history = [json.loads(r['data']) for r in rows]
        return {'note': history[0], 'history': history,
                'revision_hint': 'For save, pass note.revision (the current revision, '
                                 'not the next revision). Review the current note and preserve '
                                 'other changes; a successful update increments it automatically.'}
    if action != 'save':
        raise ValueError('Supported note actions: list, get, save')
    if not args.get('question') or not args.get('answer'):
        raise ValueError('A note needs question and answer')
    ids = list(args.get('evidence_ids', []))
    sources(case, ids)
    if args.get('status', 'open') not in ('answered', 'open', 'needs_input'):
        raise ValueError('Note status must be answered, open or needs_input')
    from .report_driven.investigation_state import save_note_locked, mutation_replay, record_mutation
    value = dict(args)
    operation = args.get('operation_id')
    with case.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        replay = mutation_replay(db, operation, args)
        if replay is not None:
            return replay
        result = save_note_locked(case, db, value)
        record_mutation(db, operation, args, result)
        return result
