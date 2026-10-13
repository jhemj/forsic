"""Resolve an explicit retained archive listing through the existing case reader."""
from copy import deepcopy

from .image_reader import InvalidArguments


def entry_schema():
    return {'type': 'object', 'required': ['listing_ref', 'member_index'],
        'properties': {
            'listing_ref': {'type': 'object', 'required': ['id', 'version'],
                'properties': {key: {'type': 'string', 'minLength': 1} for key in ('id', 'version')},
                'additionalProperties': False,
                'description': 'Copy source_ref from the retained archive_list page containing the selected member.'},
            'member_index': {'type': 'integer', 'minimum': 0,
                'description': 'Exact global ordinal shown in that page; not a row number or member name.'},
            'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
            'length': {'type': 'integer', 'minimum': 1, 'maximum': 12000, 'default': 4096}},
        'additionalProperties': False}


class SelectionError(ValueError):
    def __init__(self, message, code='invalid_archive_selection', **details):
        self.details = {'error_code': code, **details}
        super().__init__(message)


def recovery_calls(ref, original, volume_offset):
    """Only for a verified listing/start pair, never guessed from invalid input."""
    arguments = {k: deepcopy(original[k]) for k in
        ('path', 'action', 'file_path', 'volume_offset', 'offset', 'archive_scan_bytes') if k in original}
    if type(volume_offset) is int:
        arguments['volume_offset'] = volume_offset
    return {'source_call': {'tool': 'forsic_reporting', 'arguments': {'action': 'source',
                'source_id': ref['id'], 'source_version': ref['version']}},
            'relist_call': {'tool': 'forsic_image_files', 'arguments': arguments}}


def resolve(case, args, session, context):
    """No target guessing, mutation of old records, or authority from a reference."""
    from .report_driven.investigation_state import input_problems
    from .report_driven.host import source_record

    schema = entry_schema()
    problems = input_problems(args, schema, 'arguments')
    if isinstance(args, dict) and isinstance(args.get('listing_ref'), dict):
        for key in ('id', 'version'):
            if args['listing_ref'].get(key) == '':
                problems.append({'path': 'arguments.listing_ref.'+key, 'expected_type': 'nonempty_string'})
    if problems:
        error = InvalidArguments(problems, schema)
        error.details['input_contract'] = schema
        raise error
    if (context.get('status') != 'ready' or not session
            or context.get('requested_session_id') != session
            or context.get('case_id') != case.config['case_id']
            or not context.get('conversation_id')):
        raise SelectionError('Current native session/case binding is unavailable or inconsistent. A listing reference grants no access.', 'archive_binding_unavailable')
    status = context.get('goal_status')
    if status != 'active' and (status is not None or context.get('goal_id')):
        raise SelectionError('The owning native goal is not active. This read cannot resume paused, cancelled or unknown work.', 'archive_goal_inactive')
    ref = args['listing_ref']
    event = case.event(ref['id'])
    if not event or event['kind'] != 'tool_result':
        raise SelectionError('listing_ref must select a retained archive_list result in the currently bound case.')
    data = event['data']
    if source_record(event)['version'] != ref['version']:
        raise SelectionError('Retained listing version mismatch. Keep the selected version; do not substitute a newer listing.', 'archive_listing_version_conflict')
    start = case.event(data.get('started_id', ''))
    if (data.get('tool') != 'forsic_image_files' or not start or start['kind'] != 'tool_start'
            or start['data'].get('tool') != 'forsic_image_files'
            or start['data'].get('arguments', {}).get('action') != 'archive_list'
            or start['session'] != event['session'] or start['time'] > event['time']
            or any(item.get('error') or item.get('execution_rejected') or item.get('pending')
                   or item.get('status') in ('pending', 'running', 'cancelled', 'failed', 'unknown')
                   for item in (data, start['data']))):
        raise SelectionError('Select a successful, committed archive_list result with its matching start record. Failed or pending work is not a listing.')
    with case.connect() as db:
        count = db.execute("SELECT COUNT(*) FROM events WHERE kind='tool_result' AND json_extract(data,'$.started_id')=?",
                           (start['id'],)).fetchone()[0]
    if count != 1:
        raise SelectionError('The listing execution has ambiguous result records; inspect retained state before retrying.')
    original = start['data']['arguments']
    # Re-check current evidence scope, including any narrower selected-files scope.
    path = case.path(original['path'])
    if case.path(data['path']) != path or original.get('file_path') != data.get('file_path'):
        raise SelectionError('Listing origin does not match its recorded execution.')
    recovery = recovery_calls(ref, original, data.get('volume_offset'))
    source_call, relist = recovery['source_call'], recovery['relist_call']
    origin, identity = data.get('archive_origin'), data.get('archive_identity')
    if (data.get('archive_listing_version') != 1 or data.get('identity_strength') != 'metadata_checked'
            or not isinstance(origin, dict) or not origin.get('segments')
            or type(origin.get('volume_offset')) is not int or origin['volume_offset'] != data.get('volume_offset')
            or not isinstance(identity, dict) or 'st_size' not in identity):
        raise SelectionError('This listing lacks a bound archive identity. Explicitly re-list this archive before selecting a member.',
            'archive_identity_unavailable', source_call=source_call, relist_call=relist)
    relist['arguments']['volume_offset'] = origin['volume_offset']
    entries = [row for row in data.get('entries', []) if type(row.get('member_index')) is int
               and row['member_index'] == args['member_index']]
    if len(entries) != 1:
        raise SelectionError('member_index was not uniquely presented in this listing page. Select a displayed global ordinal.', source_call=source_call)
    member = entries[0]
    if any(key not in member for key in ('type', 'header_offset', 'data_offset', 'sparse', 'bytes')):
        raise SelectionError('This member lacks a retained header identity. Explicitly re-list the archive.',
            'archive_identity_unavailable', source_call=source_call, relist_call=relist)
    if member.get('kind') != 'file' or member['sparse'] or not member.get('readable'):
        raise SelectionError('Only regular, non-sparse members are readable; links, directories and devices are not followed.', source_call=source_call)
    offset, length = args.get('offset', 0), args.get('length', 4096)
    if offset > member['bytes']:
        raise SelectionError('offset exceeds the selected member size.', source_call=source_call)
    scan_budget = data.get('archive_scan_bytes')
    if type(scan_budget) is not int or not 10240 <= scan_budget <= 1024**3:
        raise SelectionError('Listing scan budget is unavailable; explicitly re-list the archive.', relist_call=relist)
    effective = {'path': original['path'], 'action': 'archive_read', 'file_path': original['file_path'],
                 'volume_offset': origin['volume_offset'], 'member_index': args['member_index'],
                 'offset': offset, 'length': length, 'archive_scan_bytes': scan_budget}
    selection = {'archive_origin': deepcopy(origin), 'archive_identity': deepcopy(identity), 'member': deepcopy(member)}
    resolution = {'kind': 'archive_listing', 'listing_ref': deepcopy(ref), 'member_index': args['member_index'],
                  'listing_session': event['session'], 'listing_started_id': start['id'],
                  'identity_strength': 'metadata_checked', **selection}
    return effective, selection, resolution


def result_view(result, case):
    """Derived selection/continuation instructions; never modify retained bytes."""
    from .report_driven.host import source_record
    if result.get('tool') != 'forsic_image_files' or result.get('error') or result.get('execution_rejected'):
        return result
    event = case.event(result['evidence_id'])
    start = case.event(event['data'].get('started_id', ''))
    if not start or start['data'].get('tool') != 'forsic_image_files':
        return result
    args = start['data'].get('arguments', {})
    view = dict(result)
    if args.get('action') == 'archive_list':
        source = source_record(event)
        view['source_ref'] = {key: source[key] for key in ('id', 'version')}
        if event['data'].get('archive_listing_version') == 1:
            view.update(read_tool='forsic_archive_read',
                selection_fields=['listing_ref', 'member_index'],
                selection_note='Copy source_ref as listing_ref and select one readable global member_index on this page. The reference selects a retained listing, not a body citation. Partial listings authorize only their displayed members.')
    elif args.get('action') == 'archive_read' and start['data'].get('argument_resolution', {}).get('kind') == 'archive_listing':
        submitted = start['data']['submitted_arguments']
        end = result.get('next_offset')
        view['next_call'] = {'tool': 'forsic_archive_read', 'arguments': {
            'listing_ref': submitted['listing_ref'], 'member_index': submitted['member_index'],
            'offset': end, 'length': submitted.get('length', 4096)}} if type(end) is int and 'text' in result else None
    return view
