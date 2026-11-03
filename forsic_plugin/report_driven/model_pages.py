"""Bounded model views; full state, evidence and report/API outputs stay intact."""
import base64
import binascii
import hashlib
import json
import re

MAX_BYTES = 6000
SOURCE_POINTER_BASIS = 'JSON pointers address the retained tool-result root. Display envelopes (result, page, value) add no pointer prefix; those names are used only when they are actual retained keys.'


def source_read_call(source, path, source_handle=None):
    selection = ({'source_handle': source_handle} if source_handle else
                 {'source_id': source['id'], 'source_version': source['version']})
    return {'action': 'source', **selection, 'pointer': path, 'offset': 0}


def state_read_call(snapshot, path, args):
    selection = {key: args[key] for key in ('question_id', 'question_ids') if key in args}
    return {'tool': 'forsic_reporting', 'arguments': {
        'action': args.get('action', 'state'), 'snapshot_id': snapshot,
        **selection, 'pointer': path, 'offset': 0}}


def shell_text_call(source, path, source_handle=None):
    return {**source_read_call(source, path, source_handle), 'representation': 'shell_utf8', 'limit': 4096}


def shell_text_page(source, data, args, handle):
    """Decode one retained stream for reading without changing citation coordinates."""
    if args['representation'] != 'shell_utf8':
        raise ValueError('Unsupported retained source representation')
    if 'query' in args or 'locator_cursor' in args:
        raise ValueError('shell_utf8 requires a stream pointer; query uses retained scalar coordinates')
    path = args.get('pointer')
    if data.get('tool') != 'forsic_shell' or path not in (
            '/raw_output/stdout_base64', '/raw_output/stderr_base64'):
        raise ValueError('shell_utf8 requires a retained forsic_shell raw_output stream pointer')
    encoded = select_source(source, data, path, args, handle)
    if not isinstance(encoded, str):
        raise ValueError('Retained shell stream is not a base64 scalar')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Retained shell stream is not valid base64; no decoded output returned') from None
    offset = integer(args, 'offset', 0)
    limit = integer(args, 'limit', 2048, 4096)
    if offset > len(raw):
        raise ValueError('offset is beyond the decoded retained stream')
    result = {
        'source_ref': {'id': source['id'], 'version': source['version']},
        **({'source_handle': handle} if handle else {}),
        'view_kind': 'retained_shell_text', 'representation': 'shell_utf8',
        'derived': True, 'original_evidence': False, 'coverage_not_established': True,
        'pointer': path, 'partial_view': True,
        'coordinate_basis': 'Decoded captured stream bytes; not UTF-8 byte coordinates of the retained base64 scalar',
        'citation_contract': 'Readable derived projection only; no exact retained-scalar citation is issued. Use raw_read_call for unchanged base64 scalar coordinates.',
        'raw_read_call': source_read_call(source, path, handle),
        'decoded_total_bytes': len(raw), 'decoded_sha256': hashlib.sha256(raw).hexdigest(),
        'capture': {key: data['raw_output'][key] for key in ('complete', 'truncated', 'limit_bytes')
                    if key in data['raw_output']},
        'next_call': None,
    }
    if size(result) > MAX_BYTES - 1000:
        raise ValueError('Field identity exceeds the page budget')
    try:
        raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        return {**result, 'status': 'not_utf8', 'invalid_byte_start': exc.start,
                'invalid_byte_end': exc.end,
                'meaning': 'Captured stream is not valid UTF-8. No replacement text or decoded span returned; exact bytes remain available through raw_read_call.'}
    if any(byte < 32 and byte not in (9, 10, 13) or byte == 127 for byte in raw):
        return {**result, 'status': 'binary_controls',
                'meaning': 'Valid UTF-8 stream contains binary control bytes. No readable span returned; exact bytes remain available through raw_read_call.'}
    try:
        raw[:offset].decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('offset must be a decoded UTF-8 character boundary; use next_call') from None
    # Keep decoded offsets out of the retained-scalar fields used by C1 citations.
    continuation = {**shell_text_call(source, path, handle), 'offset': 9999999999999999999,
                    'limit': limit}
    end = min(len(raw), offset + limit)
    while True:
        try:
            text = raw[offset:end].decode('utf-8')
        except UnicodeDecodeError:
            end -= 1
            continue
        result.update(status='readable', decoded_text=text, decoded_byte_start=offset,
                      decoded_byte_end=end, decoded_next_offset=end if end < len(raw) else None,
                      full_decoded_stream=offset == 0 and end == len(raw),
                      next_call=continuation if end < len(raw) else None)
        if size(result) <= MAX_BYTES:
            break
        end = offset + (end - offset) // 2
    if end == offset and offset < len(raw):
        raise ValueError('limit is too small for the next decoded UTF-8 character')
    if result['next_call']:
        result['next_call']['offset'] = end
    return result


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
        if type(value) in (int, float) and envelope.get('source_ref'):
            from .retained_scalar import number_literal, number_identity, NUMBER_COORDINATES
            text = number_literal(value)
            numeric = {**result, 'value': value, 'text': text, 'byte_start': 0, 'byte_end': len(text.encode()),
                    'total_bytes': len(text.encode()), 'full_field': True,
                    'retained_scalar': number_identity(value), 'coordinate_basis': NUMBER_COORDINATES,
                    'next_offset': None, 'complete_value': True}
            if size(numeric) > MAX_BYTES:
                raise ValueError('Whole numeric scalar exceeds source page budget; no partial number was returned')
            return numeric
        return {**result, 'value': value, 'next_offset': None, 'complete_value': True}
    entries = list(value.items()) if isinstance(value, dict) else list(enumerate(value))
    if offset > len(entries):
        raise ValueError('offset is beyond this collection')
    result.update(total=len(entries), items=[], next_offset=None, complete_value=False)
    count = integer(args, 'limit', 5, 25)
    for key, item in entries[offset:offset + count]:
        pointer = child(path, key)
        entry = {'pointer': pointer, 'value': item} if size(item) <= 3000 else descriptor(item, pointer)
        source_ref = envelope.get('source_ref')
        if source_ref and envelope.get('source_coordinate_contract'):
            entry['read_call'] = source_read_call(source_ref, pointer, envelope.get('source_handle'))
        if envelope.get('state_coordinate_contract'):
            entry['read_call'] = state_read_call(envelope['snapshot_id'], pointer,
                                                envelope['read_call']['arguments'])
        if isinstance(value, dict):
            entry['key'] = key
        trial = {**result, 'items': result['items'] + [entry], 'next_offset': offset + len(result['items']) + 1}
        if size(trial) > MAX_BYTES:
            if result['items']:
                break
            entry = descriptor(item, pointer)
            if source_ref and envelope.get('source_coordinate_contract'):
                entry['read_call'] = source_read_call(source_ref, pointer, envelope.get('source_handle'))
            if envelope.get('state_coordinate_contract'):
                entry['read_call'] = state_read_call(envelope['snapshot_id'], pointer,
                                                    envelope['read_call']['arguments'])
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
    try:
        return _state_view(state, audit, args)
    except ValueError as exc:
        message = str(exc)
        if isinstance(exc, RetainedSourceError):
            raise
        if message.startswith('State changed or snapshot_id missing'):
            raise StateReadError(state, args, 'state_snapshot_mismatch', message) from None
        if message in ('Pointer is absent from this retained view', 'Pointer continues beyond a scalar field') or message.startswith('Pointer is outside the selected question'):
            raise StateReadError(state, args, 'state_pointer_absent', message) from None
        if message.startswith(('offset is beyond', 'offset must be a UTF-8', 'Scalar values have no record offset')):
            raise StateReadError(state, args, 'state_page_invalid', message) from None
        raise


def _state_view(state, audit, args):
    snapshot = state['meta']['snapshot_id']
    goal_id,session_id=args.get('_goal_id'),args.get('_session_id')
    bound=bool(goal_id and session_id)
    def owned(q):return bound and q.get('goal_id')==goal_id and q.get('session_id')==session_id
    context={'binding_available':bound,'owned_question_count':sum(owned(q) for q in state['questions']),
        'other_question_count':sum(not owned(q) for q in state['questions']),
        'new_question_tool':'forsic_question',
        'case_history_call':{'tool':'forsic_reporting','arguments':{'action':'state','snapshot_id':snapshot,'pointer':'/questions'}},
        'meaning':'Previews show current-conversation questions when bound. case_history_call lists case records; read one with action=state and its question_id. Historical reads do not transfer ownership.'}
    question_id = args.get('question_id')
    if question_id is not None:
        if args['action'] not in ('state','gaps') or not any(q['id'] == question_id for q in state['questions']):
            raise ValueError('question_id must select an existing question for state/gaps')
    research_selection = {**({'question_id': question_id} if question_id else {}),
        **({'question_ids': args['question_ids']} if args.get('question_ids') is not None else {})}
    research_call = {'tool': 'forsic_reporting', 'arguments': {
        'action': 'state', 'snapshot_id': snapshot, 'pointer': '/report_research',
        **research_selection}}
    context['report_research_call'] = research_call
    # Expensive report review is opt-in and exists only at this request boundary;
    # it is neither saved state nor a new native goal/context projection.
    path = args.get('pointer', '')
    if path == '/report_research' or path.startswith('/report_research/'):
        if args.get('snapshot_id') != snapshot:
            raise ValueError('State changed or snapshot_id missing; read state again before report review')
        from .report_research import report_research_review
        review = report_research_review(state, args)
        continuation_call = {'tool': 'forsic_reporting', 'arguments': {
            'action': args['action'], 'snapshot_id': snapshot, 'pointer': path,
            'offset': 9999999999999999999, **research_selection}}
        result = page(select({'report_research': review}, path), path, args,
            {'snapshot_id': snapshot, 'view_kind': 'report_research',
             'state_coordinate_contract': 'state-coordinates-v1',
             'execution_authorized': False, 'read_call': state_read_call(snapshot, path, args),
             'next_call': continuation_call})
        if result.get('next_offset') is not None:
            result['next_call']['arguments']['offset'] = result['next_offset']
        else:
            result['next_call'] = None
        return result
    if question_id and args['action'] == 'state':
        from .current_judgment import current_judgment, judgment_read_call
        index = next(i for i,q in enumerate(state['questions']) if q['id'] == question_id)
        question = state['questions'][index]
        root = '/questions/'+str(index)
        path = args.get('pointer',root)
        if path != root and not path.startswith(root+'/'):
            raise ValueError('Pointer is outside the selected question; use its returned canonical pointer')
        if ('pointer' in args or args.get('offset',0)) and args.get('snapshot_id') != snapshot:
            raise ValueError('State changed or snapshot_id missing; read this question again before paging')
        judgment_call = judgment_read_call(state, question)
        judgment_path = judgment_call['arguments']['pointer']
        derived = path == judgment_path or path.startswith(judgment_path+'/')
        selected = (select(current_judgment(state, question), path[len(judgment_path):])
                    if derived else select(state,path))
        continuation_call = {'tool':'forsic_reporting','arguments':{
            'action':'state','question_id':question_id,'snapshot_id':snapshot,'pointer':path,'offset':9999999999999999999,
            **({'limit':args['limit']} if 'limit' in args else {})}}
        result=page(selected,path,args,{'snapshot_id':snapshot,
            'view_kind':'current_judgment' if derived else 'question_state',
            **({'derived':True,'original_evidence':False} if derived else {}),
            'state_coordinate_contract':'state-coordinates-v1',
            'conversation_scope':context,'next_call':continuation_call,
            'editable_in_current_conversation':owned(question),
            'read_call':state_read_call(snapshot,path,args),
            'judgment_read_call':judgment_call,
            'gap_lookup':{'tool':'forsic_reporting','arguments':{'action':'gaps','question_id':question_id}}})
        if result.get('next_offset') is not None:
            result['next_call']['arguments']['offset']=result['next_offset']
        else:
            result['next_call']=None
        return result
    # Preserve canonical state pointers while paging only the selected questions.
    # Explicit historical selection remains readable; it grants no write ownership.
    if args['action'] == 'gaps':
        selected_ids = {question_id} if question_id else {q['id'] for q in state['questions'] if owned(q) or not bound}
        gaps = {str(i): g for i, g in enumerate(state['gaps']) if g['question_ref']['id'] in selected_ids}
        path = args.get('pointer', '/gaps')
        if ('pointer' in args or args.get('offset', 0)) and args.get('snapshot_id') != snapshot:
            raise ValueError('State changed or snapshot_id missing; read gaps again before paging')
        if path == '/gaps':
            selected = gaps
        else:
            match = re.match(r'/gaps/(0|[1-9][0-9]*)(?:/|$)', path)
            if not match or match[1] not in gaps:
                raise ValueError('Pointer is outside the selected question gaps; use its returned canonical pointer')
            selected = select(state, path)
        envelope = {'snapshot_id': snapshot, 'view_kind': 'report_state', 'conversation_scope': context,
                    'state_coordinate_contract': 'state-coordinates-v1', 'read_call': state_read_call(snapshot, path, args),
                    'audit': audit_view(audit), 'audit_scope': 'case-wide summary, independent of this gap filter',
                    'requirement_count': len(state['requirements']), 'requirements_pointer': '/requirements',
                    'gap_scope': {'question_id': question_id, 'mode': 'explicit_question' if question_id else 'current_conversation' if bound else 'case',
                                  'selected_count': len(gaps), 'other_gap_count': len(state['gaps']) - len(gaps)},
                    'meaning': 'This view is only the selected gap scope. Case-wide records remain at state pointer /gaps; no records are changed.'}
        continuation_call = {'tool': 'forsic_reporting', 'arguments': {
            'action': 'gaps', 'snapshot_id': snapshot, 'pointer': path,
            'offset': 9999999999999999999, **({'question_id': question_id} if question_id else {})}}
        # Reserve the continuation envelope within the same bounded page budget.
        result = page(selected, path, args, {**envelope, 'next_call': continuation_call})
        if result.get('next_offset') is not None:
            result['next_call']['arguments']['offset'] = result['next_offset']
        else:
            result['next_call'] = None
        return result
    continuation = 'pointer' in args or args.get('offset', 0) != 0
    if continuation and args.get('snapshot_id') != snapshot:
        raise ValueError('State changed or snapshot_id missing; read state again before paging')
    if continuation:
        path = args.get('pointer', '/gaps' if args['action'] == 'gaps' else '')
        selected=select(state,path)
        read_call = state_read_call(snapshot, path, args)
        envelope={'snapshot_id': snapshot, 'view_kind': 'report_state','conversation_scope':context,
                  'state_coordinate_contract': 'state-coordinates-v1', 'read_call': read_call,
                  'next_call': {**read_call, 'arguments': {**read_call['arguments'],
                                'offset': 9999999999999999999,
                                **({'limit': args['limit']} if 'limit' in args else {})}}}
        mission_call = None
        # Existing saved missions also get today's exact native entry call on lookup.
        if re.fullmatch(r'/missions/(0|[1-9][0-9]*)',path) and isinstance(selected,dict):
            from .investigation_state import image_read_reference_call
            question=next((q for q in state['questions'] if q['id']==selected['question_ref']['id']),None)
            call=image_read_reference_call(selected) if question and owned(question) and question['work_state'] in ('open','active') else None
            if call and size(call)<MAX_BYTES-2000:mission_call=call
        if mission_call:
            # Preserve the existing mission entry contract; this change adds
            # read navigation without replacing a previously exposed action.
            envelope['next_call'] = mission_call
        result = page(selected, path, args, envelope)
        if mission_call:
            return result
        if result.get('next_offset') is not None:
            result['next_call']['arguments']['offset'] = result['next_offset']
        else:
            result['next_call'] = mission_call
        return result
    from .current_judgment import navigation_view
    visible_questions = [q for q in state['questions'] if owned(q) or not bound]
    questions = [dict(editable_in_current_conversation=owned(q),current_judgment=navigation_view(state,q), id=q['id'], version=q['version'], revision=q['revision'], definition_version=q['definition_version'], question_preview=q['question'][:120],
                      assessment=q['assessment'], work_state=q['work_state'],
                      read_call={'tool':'forsic_reporting','arguments':{'action':'state','question_id':q['id']}})
                 for q in visible_questions[:2]]
    result = {'snapshot_id': snapshot,'conversation_scope':context, 'state': {'meta': state['meta'],
              'status': {k: v for k, v in state['status'].items() if not isinstance(v, list)},
              'scope': descriptor(state['scope'], '/scope'), 'questions': questions},
              'audit': audit_view(audit), 'view_kind': 'overview', 'partial_view': True,
              'sections': {k: {'count': len(v), 'pointer': '/' + k,
                  'read_call': state_read_call(snapshot, '/' + k, {'action': 'state'})}
                  for k, v in state.items() if isinstance(v, list)},
              'omitted_counts': {k: len(v) - (len(questions) if k == 'questions' else 0)
                                 for k, v in state.items() if isinstance(v, list)},
              'next_step': 'Read needed pointer with this snapshot_id, offset and limit. Previews/indexes are not original evidence; omitted records are not shown on this page; their review status belongs to the records.'}
    if size(result) > MAX_BYTES:
        result['state']['questions'] = [{k:v for k,v in q.items() if k!='current_judgment'} for q in questions]
        result['omitted_counts']['questions'] = len(state['questions'])-len(questions)
        result['audit'] = {'snapshot_id': snapshot, 'counts': {k: len(v) for k, v in audit.items() if isinstance(v, list)}}
    if size(result) > MAX_BYTES:
        result['state'] = descriptor(state, '')
    return result


class RetainedSourceError(ValueError):
    """A rejected retained-view read with explicit, non-executing recovery inputs."""


class StateReadError(RetainedSourceError):
    def __init__(self, state, args, code, message):
        snapshot = state['meta']['snapshot_id']
        selection = {key: args[key] for key in ('question_id', 'question_ids') if key in args}
        discovery = {'tool': 'forsic_reporting', 'arguments': {
            'action': args.get('action', 'state'), **selection}}
        default_path = '/gaps' if args.get('action') == 'gaps' else ''
        if args.get('action') == 'state' and args.get('question_id'):
            default_path = next(('/questions/' + str(i) for i, question in enumerate(state['questions'])
                                 if question['id'] == args['question_id']), '')
        path = args.get('pointer', default_path)
        call = discovery
        if code == 'state_page_invalid':
            # The path and snapshot were already validated. Reset only paging;
            # keep the selected scope and do not substitute another record.
            call = state_read_call(snapshot, path, args)
        elif code == 'state_pointer_absent' and args.get('action') == 'state' and not selection:
            parent = ''
            for part in path[1:].split('/'):
                candidate = parent + '/' + part
                try:
                    select(state, candidate)
                except ValueError:
                    break
                if size(state_read_call(snapshot, candidate, args)) > 1200:
                    break
                parent = candidate
            call = state_read_call(snapshot, parent, args)
        self.details = {
            'error_code': code,
            'failed_state': {key: args[key] for key in
                ('action', 'snapshot_id', 'question_id', 'question_ids', 'pointer', 'offset') if key in args},
            'next_call': call,
            'write_applied': False,
            'next_step': 'Use next_call to rediscover the selected state or verified parent, then copy a returned child read_call/next_call. State indexes are navigation, not original evidence. No judgment, selected record or citation was replaced.'}
        if args.get('action') == 'state' and args.get('question_id'):
            question = next((q for q in state['questions'] if q['id'] == args['question_id']), None)
            if question is not None:
                from .current_judgment import judgment_read_call
                self.details['judgment_read_call'] = judgment_read_call(state, question)
        super().__init__(message + '; no state page was returned. Use the exact recovery call.')


class SourcePageError(RetainedSourceError):
    def __init__(self, source, args, message, handle=None):
        self.details = {
            'error_code': 'source_page_invalid',
            'failed_source': failed_source(args, source['id']),
            'next_call': source_read_call(source, args.get('pointer', ''), handle),
            'next_step': 'The exact source version and pointer are unchanged. Read from offset 0, then copy next_call; collection offsets count children, scalar offsets count UTF-8 bytes. No citation or requested offset was silently changed.'}
        super().__init__(message + '; no source page was returned. Use the exact recovery call.')


def source_page(source, selected, path, args, envelope, handle):
    try:
        return page(selected, path, args, envelope)
    except ValueError as exc:
        if str(exc).startswith(('offset is beyond', 'offset must be a UTF-8', 'Scalar values have no record offset')):
            raise SourcePageError(source, args, str(exc), handle) from None
        raise


def failed_source(args, source_id):
    failed = {'source_id': source_id}
    for key in ('source_handle', 'source_version', 'pointer', 'offset'):
        if key in args:
            failed[key] = args[key]
    return failed


class SourceVersionMismatch(RetainedSourceError):
    def __init__(self, source_id, args=None):
        discovery = {'action': 'source', 'source_id': source_id}
        self.details = {'error_code': 'source_version_mismatch', 'next_call': discovery,
                        'failed_source': failed_source(args or {}, source_id),
                        'next_step': 'Choose next_call to reselect this retained source reference. Then copy its returned read_call/source_handle for the needed field, or explicitly use source_ref.version; no version or citation was replaced.'}
        super().__init__(
            'Source page rejected: source_version is missing or does not match the retained result; '
            'snapshot_id belongs to report state and cannot replace source_version. '
            'No source page was returned or investigation judgment changed. '
            'Discover the retained reference with ' + json.dumps(discovery) +
            ' (omit pointer, offset, source_version and snapshot_id). '
            'Copy the returned read_call/source_handle, or explicitly retry the needed pointer/offset/limit '
            'with source_version set to source_ref.version. If an earlier citation used a different '
            'version, recheck its original field before adopting it; do not silently replace citation versions.')


class SourcePointerError(RetainedSourceError):
    def __init__(self, source, data, path, args, source_handle=None):
        handle = source_handle or args.get('source_handle')
        # Walk only verified ancestors. Never remove a display-looking prefix or
        # substitute another source/field, even when it happens to exist.
        parent, value = '', data
        for part in path[1:].split('/'):
            candidate = parent + '/' + part
            try:
                selected = select(data, candidate)
            except ValueError:
                break
            if size(source_read_call(source, candidate, handle)) > 1200:
                break
            parent, value = candidate, selected
        choices = []
        keys = value.keys() if isinstance(value, dict) else range(len(value)) if isinstance(value, list) else ()
        for key in keys:
            call = source_read_call(source, child(parent, key), handle)
            if len(choices) == 5 or size(choices + [call]) > 1800:
                break
            choices.append(call)
        self.details = {'error_code': 'source_pointer_absent',
                        'failed_source': failed_source(args, source['id']),
                        'pointer_basis': SOURCE_POINTER_BASIS,
                        'next_call': source_read_call(source, parent, handle),
                        'recovery': {'parent_pointer': parent, 'choices': choices,
                                     'choices_complete': len(choices) == len(value) if isinstance(value, (dict, list)) else True},
                        'next_step': 'Read the verified parent with next_call, then explicitly select a returned read_call. No requested field, version or citation was replaced.'}
        super().__init__('Pointer is absent from this retained source. Use the verified parent and canonical read calls; display envelopes do not change source coordinates.')


def select_source(source, data, path, args, source_handle=None):
    try:
        return select(data, path)
    except ValueError as exc:
        if str(exc) not in ('Pointer is absent from this retained view', 'Pointer continues beyond a scalar field'):
            raise
        raise SourcePointerError(source, data, path, args, source_handle) from None


def source_view(source, data, args, *, include_acquisition_context=True, include_continuation=True,
                include_coordinate_calls=True, case=None):
    if 'source_handle' in args:
        from .source_handles import SourceHandleError, resolve_source_handle
        if case is None:
            raise SourceHandleError(args, 'source_handle requires its case ledger')
        args = resolve_source_handle(case, args)
        if args['source_id'] != source['id']:
            raise SourceHandleError(args, 'source_handle selects another retained source')
    continuation = 'pointer' in args or args.get('offset', 0) != 0
    if (continuation or 'source_version' in args or args.get('locator_cursor') is not None) and args.get('source_version') != source['version']:
        raise SourceVersionMismatch(source['id'], args)
    handle = None
    if case is not None:
        from .source_handles import source_handle
        handle = source_handle(case, source)
    if 'representation' in args:
        if args.get('source_version') != source['version']:
            raise SourceVersionMismatch(source['id'], args)
        return shell_text_page(source, data, args, handle)
    if 'query' in args:
        from .source_locator import locate
        return locate(source, data, args, source_handle=handle)
    if args.get('locator_cursor') is not None:
        raise ValueError('locator_cursor requires its original literal query')
    envelope = {'source_ref': {'id': source['id'], 'version': source['version']},
                'meaning': '보존된 도구 반환 재제시; 새 수집·독립 근거가 아님'}
    if handle:
        envelope['source_handle'] = handle
    if include_coordinate_calls:
        envelope.update(source_coordinate_contract='source-coordinates-v1',
                        pointer_basis=SOURCE_POINTER_BASIS,
                        read_call=source_read_call(source, args.get('pointer', ''), handle),
                        source_input_contract=('Copy source_handle or returned read_call/next_call to reuse this exact retained id/version. Omit pointer for discovery; explicit pointer="" reads the retained root. Pointer/paging require source_handle or source_id+source_version. Explicit conflicting id/version is rejected.' if handle else 'Omit pointer for discovery (offset absent or 0). Explicit pointer="" reads the retained root and requires source_version, as does any pointer or nonzero offset page.'))
    from .source_locator import retained_coverage
    envelope['retained_coverage'] = retained_coverage(source)
    from .measurement import measurement_view
    from .retained_scalar import citation_context
    if include_acquisition_context:
        envelope['presentation_contract'] = 'acquisition-context-v1'
        envelope['acquisition_context'] = citation_context(data, args.get('pointer', ''))
    measurement=measurement_view(data)
    if measurement:envelope['measurement']=measurement
    # Discovery uses the same child navigation as larger retained results.
    # A raw result envelope hid exact read calls for small numeric metadata and
    # empty-search coverage, even though explicit scalar pages supported C1.
    path = args.get('pointer', '')
    selected = select_source(source, data, path, args, handle)
    if not include_continuation:
        return source_page(source, selected, path, args, envelope, handle)
    # Reserve the exact continuation within the page budget, as for gap pages.
    next_call = {**source_read_call(source, path, handle), 'offset': 9999999999999999999,
                 **({'limit': args['limit']} if 'limit' in args else {})}
    result = source_page(source, selected, path, args,
                  {**envelope, 'paging_contract': 'source-continuation-v1', 'next_call': next_call}, handle)
    if result.get('next_offset') is not None:
        result['next_call']['offset'] = result['next_offset']
    else:
        result['next_call'] = None
    return result
