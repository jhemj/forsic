"""Explicit retained search-page selection; the existing reader owns execution."""
from copy import deepcopy


def reference_schema():
    return {'type': 'object', 'required': ['id', 'version'],
        'properties': {k: {'type': 'string', 'minLength': 1} for k in ('id', 'version')},
        'additionalProperties': False,
        'description': 'Copy source_ref from the chosen search page to continue its exact cursor. Keep the same explicit path, query and scope; never assemble cursor contents.'}


def is_search(tool, args):
    return tool == 'forsic_search' or tool == 'forsic_image_files' and args.get('action') == 'search'


def resolve(case, tool, args, session, context):
    from .image_reader import InvalidArguments
    from .report_driven.investigation_state import input_problems
    from .report_driven.host import source_record

    problems = input_problems(args['cursor_ref'], reference_schema(), 'arguments.cursor_ref')
    if problems:
        raise InvalidArguments(problems, reference_schema())
    if not is_search(tool, args) or any(k in args for k in ('cursor', 'offset')):
        raise ValueError('cursor_ref is only for search, without cursor or offset. Copy next_call from the selected page.')
    if (context.get('status') != 'ready' or not session or context.get('requested_session_id') != session
            or context.get('case_id') != case.config['case_id'] or not context.get('conversation_id')):
        raise ValueError('Current case/session binding is unavailable; a search reference grants no access.')
    status = context.get('goal_status')
    if status != 'active' and (status is not None or context.get('goal_id')):
        raise ValueError('The owning goal is not active; search continuation cannot resume it.')
    ref = args['cursor_ref']
    event = case.event(ref['id'])
    if not event or event['kind'] != 'tool_result' or event['data'].get('tool') != tool:
        raise ValueError('Select a retained result of this search tool in the current case.')
    if source_record(event)['version'] != ref['version']:
        raise ValueError('Search result version changed; keep the selected version and inspect its source.')
    data = event['data']; start = case.event(data.get('started_id', ''))
    if (not start or start['kind'] != 'tool_start' or start['session'] != event['session']
            or start['time'] > event['time'] or start['data'].get('tool') != tool
            or not is_search(tool, start['data'].get('arguments', {}))
            or any(r.get('error') or r.get('execution_rejected') or r.get('pending')
                   or r.get('status') in ('pending', 'running', 'cancelled', 'failed', 'unknown')
                   for r in (data, start['data']))):
        raise ValueError('Search selection needs a successful committed page and its matching execution.')
    with case.connect() as db:
        count = db.execute("SELECT COUNT(*) FROM events WHERE kind='tool_result' AND json_extract(data,'$.started_id')=?", (start['id'],)).fetchone()[0]
    if count != 1 or not isinstance(data.get('next_cursor'), dict) or not data['next_cursor']:
        raise ValueError('This result has no unambiguous continuation. Inspect the selected page; do not restart it as a continuation.')
    original = start['data']['arguments']
    # Require the caller's target and query. Only opaque parser state is restored.
    for key in ('path', 'action', 'file_path', 'text', 'recursive', 'volume_offset'):
        default = False if key == 'recursive' else None
        if args.get(key, default) != original.get(key, default):
            raise ValueError('Search continuation changed '+key+'; copy the selected page next_call or explicitly start a new search.')
    case.path(original.get('path', '.'))
    effective = {k: deepcopy(v) for k, v in args.items() if k != 'cursor_ref'}
    effective['cursor'] = deepcopy(data['next_cursor'])
    return effective, {'kind': 'retained_search_cursor', 'source_ref': deepcopy(ref), 'started_id': start['id']}


def result_view(result, case):
    from .report_driven.host import source_record
    if result.get('error') or not result.get('evidence_id'):
        return result
    event = case.event(result['evidence_id']); data = event['data']
    start = case.event(data.get('started_id', ''))
    if not start or not is_search(start['data'].get('tool'), start['data'].get('arguments', {})):
        return result
    source = source_record(event); ref = {k: source[k] for k in ('id', 'version')}
    view = {k: v for k, v in result.items() if k != 'next_cursor'}
    view.update(source_ref=ref, continuation_kind='retained_search',
                has_next_cursor=isinstance(data.get('next_cursor'), dict) and bool(data['next_cursor']), next_call=None,
                omitted_fields=[*result.get('omitted_fields', []), 'next_cursor'])
    if view['has_next_cursor']:
        args = {k: deepcopy(v) for k, v in start['data']['arguments'].items()
                if k not in ('cursor', 'cursor_ref', 'offset', 'mission_id', 'mission_version') and not k.startswith('_')}
        view['next_call'] = {'tool': start['data']['tool'], 'arguments': {**args, 'cursor_ref': ref}}
    return view
