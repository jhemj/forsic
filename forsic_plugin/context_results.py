"""Execution outcomes and lookup/scope crumbs for native pruning, never new evidence."""
import json

from .notes import EVIDENCE_TOOLS


def summarize(tool, args, content):
    try:
        body = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    ref = body.get('source_ref')
    if not isinstance(ref, dict) or not isinstance(ref.get('id'), str):
        ref = {'id': body.get('evidence_id')} if tool in EVIDENCE_TOOLS | {'forsic_image_read', 'forsic_archive_read'} else {}
    view = {'tool': tool, 'pruned': True, 'body_omitted': True,
            'meaning': 'Body omitted from this compacted view, not evidence that the earlier tool message lacked body. This is neither current judgment nor semantic approval; use preserved current calls/state and retrieve only the uncertain source span.'}
    from .report_driven.source_locator import nodes
    shown = body.get('page', body.get('result', body))
    # Detect actual displayed text even in paged source collections. False means
    # no text field detected in this message, not an unread evidence file.
    view['text_fields_present_in_original_message'] = any(
        isinstance(value,str) and bool(value) and pointer.endswith('/text')
        for pointer,value in nodes(shown))
    if isinstance(ref.get('id'), str) and len(ref['id']) <= 160:
        lookup = {'action': 'source', 'source_id': ref['id']}
        if isinstance(ref.get('version'), str) and len(ref['version']) <= 160:
            lookup['source_version'] = ref['version']
        view['lookup'] = {'tool': 'forsic_reporting', 'arguments': lookup}
    if isinstance(body.get('evidence_id'), str) and len(body['evidence_id']) <= 160:
        view['retained_result_id'] = body['evidence_id']
    error = body.get('error')
    if error:
        # Even pre-execution/binding failures without a source ID must survive.
        # Do not infer rollback or successful storage from submitted arguments.
        view['error_present'] = True
        message = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
        view['error'] = message if len(message) <= 600 else message[:300] + ' … ' + message[-297:]
        view['error_truncated'] = len(message) > 600
        for key in ('error_type', 'error_code', 'failure_stage', 'execution_status'):
            if isinstance(body.get(key), str) and len(body[key]) <= 120:
                view[key] = body[key]
    else:
        # Only returned records acknowledge a write. Requested outcome/answer is intent.
        fields = ('id', 'question_id', 'note_id', 'version', 'revision', 'definition_version',
                  'work_state', 'state', 'outcome')
        records = {}
        for key in ('question', 'mission', 'assessment'):
            record = body.get(key)
            if isinstance(record, dict):
                records[key] = {k: record[k] for k in fields if k in record and
                    (type(record[k]) is int or isinstance(record[k], str) and len(record[k]) <= 160)}
        if records:
            view['returned_records'] = records
        if isinstance(body.get('receipt'), str) and len(body['receipt']) <= 160:
            view['receipt'] = body['receipt']
        for key in ('reused', 'semantic_approval', 'execution_authorized'):
            if type(body.get(key)) is bool:
                view[key] = body[key]
    if 'lookup' not in view and not error and not view.get('returned_records'):
        return None
    # Inputs and actual returned scope are separate. Omitted paths/fields are not guessed.
    requested = ('action', 'path', 'file_path', 'volume_offset', 'member_index',
                 'start_line', 'offset', 'length', 'text', 'recursive')
    observed = ('representation', 'encoding', 'decode_lossy', 'nul_bytes', 'records_decoded',
                'content_bytes', 'complete_content', 'status', 'verified', 'returncode', 'exit_code', 'job_id', 'execution_status', 'selected_volume_offset', 'file_path', 'search_path', 'member_index', 'byte_start', 'byte_end',
                'line_start', 'line_end', 'total_lines', 'identity_strength',
                'next_line', 'next_offset', 'complete', 'complete_file', 'complete_member', 'coverage_complete',
                'scan_exhausted', 'stop_reason', 'bytes_scanned', 'files_scanned', 'files_completed',
                'files_skipped', 'search_text', 'match_type', 'has_next_cursor', 'truncated', 'error_code', 'execution_rejected', 'backend_started')
    for key, source, fields in (('requested_scope', args, requested), ('returned_scope', body, observed)):
        view[key] = {k: source[k] for k in fields if k in source and
                     (source[k] is None or type(source[k]) in (bool, int, float) or
                      isinstance(source[k], str) and len(source[k]) <= 240)}
        omitted = [k for k in fields if k in source and k not in view[key]]
        if omitted:
            view[key]['omitted_fields'] = omitted
    if 'error' in body:
        view['error_present'] = bool(body['error'])
    for field in ('matches', 'lines', 'entries', 'skipped'):
        if isinstance(body.get(field), list):
            view['returned_' + field + '_count'] = len(body[field])
    if 'next_cursor' in body:
        view['continuation_available'] = body['next_cursor'] is not None
    elif 'has_next_cursor' in body:
        view['continuation_available'] = body['has_next_cursor']
    if body.get('continuation_kind') == 'retained_search' and isinstance(body.get('next_call'), dict):
        # Exact selection survives pruning. Still a query continuation, never a body citation.
        view['next_call'] = body['next_call']
    # Preserve intact references, not chopped JSON, when unusual paths consume the budget.
    encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    if len(encoded) > 2000:
        view.pop('next_call', None)
        view['continuation_call_omitted'] = bool(body.get('next_call'))
        view.pop('requested_scope', None)
        view['requested_scope_omitted'] = True
        encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    if len(encoded) > 2000 and 'returned_scope' in view:
        scope = view['returned_scope']
        essential = {k: v for k, v in scope.items() if v is None or type(v) in (bool, int, float)
                     or k in ('representation', 'encoding', 'identity_strength', 'execution_status', 'status', 'stop_reason')}
        essential['omitted_fields'] = sorted(set(scope) - set(essential))
        view['returned_scope'] = essential
        encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    for field in ('requested_scope', 'returned_records'):
        if len(encoded) <= 2000:
            break
        if field in view:
            view.pop(field)
            view[field + '_omitted'] = True
            encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    return encoded if len(encoded) <= 2000 else None
