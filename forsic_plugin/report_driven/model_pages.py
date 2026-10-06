"""Bounded model views; full state, evidence and report/API outputs stay intact."""
import json
import re

MAX_BYTES = 6000


def size(value):
    # Match Case.invoke's actual wire serialization, including its spaces.
    return len(json.dumps(value, ensure_ascii=False).encode())


def child(path, key):
    return path + '/' + str(key).replace('~', '~0').replace('/', '~1')


def select(value, path):
    if path == '':
        return value
    if not isinstance(path, str) or not path.startswith('/') or re.search(r'~(?![01])', path):
        raise ValueError('Use a valid JSON pointer returned by this reporting tool')
    try:
        for part in path[1:].split('/'):
            key = part.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                if not key.isascii() or not key.isdigit() or (key != '0' and key.startswith('0')):
                    raise ValueError('List pointers use a non-negative decimal index')
                value = value[int(key)]
            elif isinstance(value, dict):
                value = value[key]
            else:
                raise ValueError('Pointer continues beyond a scalar field')
        return value
    except (KeyError, IndexError):
        raise ValueError('Pointer is absent from this retained view') from None


def descriptor(value, path):
    result = {'pointer': path, 'type': type(value).__name__, 'presented': False}
    if isinstance(value, (dict, list)):
        result['count'] = len(value)
    elif isinstance(value, str):
        result['utf8_bytes'] = len(value.encode())
    if isinstance(value, dict):
        identity = {k: value[k] for k in ('id', 'version', 'requirement_id') if k in value}
        if size(identity) < 600:
            result['ref'] = identity
    return result


def integer(args, key, default, maximum=None):
    value = args.get(key, default)
    if type(value) is not int or value < (1 if key == 'limit' else 0):
        raise ValueError(key + ' must be a non-negative integer (limit must be positive)')
    return min(value, maximum) if maximum is not None else value


def page(value, path, args, envelope):
    """Exact field bytes or child pointers, never a silently shortened record."""
    offset = integer(args, 'offset', 0)
    result = {**envelope, 'pointer': path, 'offset': offset, 'partial_view': True}
    if size(result) > MAX_BYTES - 1000:
        raise ValueError('Field identity exceeds the page budget')
    if isinstance(value, str):
        raw = value.encode('utf-8')
        if offset > len(raw):
            raise ValueError('offset is beyond the retained field')
        try:
            raw[:offset].decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('offset must be a UTF-8 character boundary; use next_offset') from None
        end = min(len(raw), offset + integer(args, 'limit', 2048, 4096))
        while True:
            try:
                text = raw[offset:end].decode('utf-8')
            except UnicodeDecodeError:
                end -= 1
                continue
            result.update(text=text, byte_start=offset, byte_end=end, total_bytes=len(raw),
                          next_offset=end if end < len(raw) else None,
                          full_field=offset == 0 and end == len(raw),
                          coordinate_basis='UTF-8 bytes of the retained scalar field')
            if size(result) <= MAX_BYTES:
                break
            end = offset + (end - offset) // 2
        if end == offset and offset < len(raw):
            raise ValueError('limit is too small for the next UTF-8 character')
        return result
    if not isinstance(value, (list, dict)):
        if offset:
            raise ValueError('Scalar values have no record offset')
        return {**result, 'value': value, 'next_offset': None, 'complete_value': True}
    entries = list(value.items()) if isinstance(value, dict) else list(enumerate(value))
    if offset > len(entries):
        raise ValueError('offset is beyond this collection')
    result.update(total=len(entries), items=[], next_offset=None, complete_value=False)
    count = integer(args, 'limit', 5, 25)
    for key, item in entries[offset:offset + count]:
        pointer = child(path, key)
        entry = {'pointer': pointer, 'value': item} if size(item) <= 3000 else descriptor(item, pointer)
        if isinstance(value, dict):
            entry['key'] = key
        trial = {**result, 'items': result['items'] + [entry], 'next_offset': offset + len(result['items']) + 1}
        if size(trial) > MAX_BYTES:
            if result['items']:
                break
            entry = descriptor(item, pointer)
            if size({**result, 'items': [entry]}) > MAX_BYTES:
                raise ValueError('Field identity exceeds the page budget')
        result['items'].append(entry)
    end = offset + len(result['items'])
    result.update(next_offset=end if end < len(entries) else None,
                  complete_value=offset == 0 and end == len(entries)
                  and all('value' in item for item in result['items']))
    return result


def audit_view(audit):
    result = {k: v for k, v in audit.items() if not isinstance(v, list)}
    result['omitted_counts'] = {}
    for key, value in audit.items():
        if isinstance(value, list):
            kept = value[:5]
            if size(kept) > 250:
                kept = []
            result[key] = kept
            result['omitted_counts'][key] = len(value) - len(kept)
    return result


def state_view(state, audit, args):
    snapshot = state['meta']['snapshot_id']
    continuation = 'pointer' in args or args.get('offset', 0) != 0
    if continuation and args.get('snapshot_id') != snapshot:
        raise ValueError('State changed or snapshot_id missing; read state again before paging')
    if continuation:
        path = args.get('pointer', '/gaps' if args['action'] == 'gaps' else '')
        return page(select(state, path), path, args, {'snapshot_id': snapshot, 'view_kind': 'report_state'})
    if args['action'] == 'gaps':
        return page(state['gaps'], '/gaps', args, {'snapshot_id': snapshot, 'view_kind': 'report_state',
                    'audit': audit_view(audit), 'requirement_count': len(state['requirements']),
                    'requirements_pointer': '/requirements'})
    questions = [dict(id=q['id'], version=q['version'], question_preview=q['question'][:120],
                      answer_preview=q['answer'][:160], assessment=q['assessment'], work_state=q['work_state'],
                      pointer='/questions/' + str(i)) for i, q in enumerate(state['questions'][:2])]
    result = {'snapshot_id': snapshot, 'state': {'meta': state['meta'],
              'status': {k: v for k, v in state['status'].items() if not isinstance(v, list)},
              'scope': descriptor(state['scope'], '/scope'), 'questions': questions},
              'audit': audit_view(audit), 'view_kind': 'overview', 'partial_view': True,
              'sections': {k: {'count': len(v), 'pointer': '/' + k} for k, v in state.items() if isinstance(v, list)},
              'omitted_counts': {k: len(v) - (len(questions) if k == 'questions' else 0)
                                 for k, v in state.items() if isinstance(v, list)},
              'next_step': 'Read needed pointer with this snapshot_id, offset and limit. Previews/indexes are not original evidence; omitted records are unreviewed.'}
    if size(result) > MAX_BYTES:
        result['state']['questions'] = []
        result['omitted_counts']['questions'] = len(state['questions'])
        result['audit'] = {'snapshot_id': snapshot, 'counts': {k: len(v) for k, v in audit.items() if isinstance(v, list)}}
    if size(result) > MAX_BYTES:
        result['state'] = descriptor(state, '')
    return result


def source_view(source, data, args):
    continuation = 'pointer' in args or args.get('offset', 0) != 0
    if continuation and args.get('source_version') != source['version']:
        raise ValueError('Use this retained source_version for each source page')
    envelope = {'source_ref': {'id': source['id'], 'version': source['version']},
                'meaning': '보존된 도구 반환 재제시; 새 수집·독립 근거가 아님'}
    original = {'source': source, 'result': data, 'meaning': envelope['meaning']}
    if not continuation and size(original) <= MAX_BYTES:
        return original
    path = args.get('pointer', '')
    return page(select(data, path), path, args, envelope)
