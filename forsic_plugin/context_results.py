"""Execution outcomes and lookup/scope crumbs for native pruning, never new evidence."""
import json
from copy import deepcopy

from .notes import EVIDENCE_TOOLS


def source_recovery(body):
    """Preserve executable navigation inputs as a unit, never repair them here."""
    codes = {'source_version_mismatch', 'source_pointer_absent', 'source_page_invalid', 'unknown_retained_source', 'source_handle_rejected', 'state_pointer_absent', 'state_page_invalid', 'state_snapshot_mismatch'}
    if body.get('error_code') not in codes:
        return {}
    result = {}
    failed = body.get('failed_source')
    if isinstance(failed, dict):
        kept = {key: failed[key] for key in ('source_id', 'source_handle', 'source_version', 'pointer', 'offset')
                if key in failed and (isinstance(failed[key], str) or type(failed[key]) is int)}
        omitted = sorted(set(failed) - set(kept))
        result['failed_source'] = kept
        if omitted:
            result['failed_source_omitted_fields'] = omitted
    failed_state = body.get('failed_state')
    if isinstance(failed_state, dict):
        result['failed_state'] = {key: failed_state[key] for key in
            ('action', 'snapshot_id', 'question_id', 'question_ids', 'pointer', 'offset') if key in failed_state}
    call = body.get('next_call')
    if isinstance(call, dict):
        args = call.get('arguments') if call.get('tool') == 'forsic_reporting' else call
        if isinstance(args, dict):
            allowed = {'action', 'source_id', 'source_handle', 'source_version', 'pointer', 'offset', 'limit'}
            source_call = (set(args) <= allowed and args.get('action') == 'source'
                           and (isinstance(args.get('source_id'), str) or isinstance(args.get('source_handle'), str)))
            state_call = body['error_code'] in ('unknown_retained_source', 'source_handle_rejected') and args == {'action': 'state'}
            if body['error_code'].startswith('state_'):
                state_call = (set(args) <= {'action', 'snapshot_id', 'question_id', 'question_ids', 'pointer', 'offset'}
                              and args.get('action') in ('state', 'gaps'))
            if (source_call or state_call) and len(json.dumps(call, ensure_ascii=False)) <= 1500:
                result['next_call'] = deepcopy(call)
            else:
                result['recovery_call_omitted'] = True
    # Coordinates are indivisible. Spend the budget on exact selections, then
    # disclose whole-field omissions if an unusual coordinate cannot fit.
    for field in ('failed_source', 'failed_state'):
        while len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))) > 1700 and result.get(field):
            key = max(result[field], key=lambda k: len(json.dumps(result[field][k], ensure_ascii=False)))
            result[field].pop(key)
            result.setdefault(field + '_omitted_fields', []).append(key)
    return result


def review_inbox_navigation(body):
    """Retain a small index; retrieve one complete finding before answering it."""
    if body.get('kind') != 'independent_review_inbox':
        return {}
    def identity(value):
        return isinstance(value, str) and 0 < len(value) <= 160
    findings = body.get('findings')
    if not isinstance(findings, list):
        return {}
    scope = body.get('write_scope', {})
    ids = scope.get('question_ids', []) if isinstance(scope, dict) else []
    ids = [qid for qid in ids if identity(qid)] if isinstance(ids, list) else []
    selection = {'question_id': ids[0]} if len(ids) == 1 else {}
    view = {'state_at_delivery': True,
            'lookup': {'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox', **selection}},
            'pending_count': body.get('pending_count') if type(body.get('pending_count')) is int else None,
            'questions': [], 'findings': [], 'omitted_findings': len(findings),
            'meaning': 'Navigation only. Read one finding for its complete question, exact source lookup and current save inputs; saved revisions may change.'}
    if isinstance(scope, dict) and identity(scope.get('goal_id')):
        view['write_scope'] = {'goal_id': scope['goal_id'], 'question_ids': ids[:5],
                               'omitted_question_ids': max(0, len(ids)-5)}
    for question in body.get('questions', []):
        if (isinstance(question, dict) and identity(question.get('id'))
                and type(question.get('revision')) is int and question['id'] in ids):
            candidate = {'id': question['id'], 'revision': question['revision']}
            if len(view['questions']) < 5 and len(json.dumps({**view, 'questions': view['questions'] + [candidate]}, ensure_ascii=False)) <= 900:
                view['questions'].append(candidate)
    for finding in findings:
        if not isinstance(finding, dict) or not all(identity(finding.get(key)) for key in ('review_id', 'finding_id')):
            continue
        call = {'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox',
                'review_id': finding['review_id'], 'finding_id': finding['finding_id'], **selection}}
        candidate = {'read_call': call}
        trial = {**view, 'findings': view['findings'] + [candidate],
                 'omitted_findings': len(findings)-len(view['findings'])-1}
        if len(json.dumps(trial, ensure_ascii=False, separators=(',', ':'))) > 1700:
            break
        view = trial
    return {'review_inbox': view}


def retained_navigation(body):
    """Keep exact page selections through native pruning without source content."""
    from .report_driven.source_handles import handle_reference
    source = body.get('source_coordinate_contract') == 'source-coordinates-v1'
    state = body.get('state_coordinate_contract') == 'state-coordinates-v1'
    if not (source or state):
        return {}
    result = {}
    for key in ('read_call', 'next_call'):
        call = body.get(key)
        if call is None:
            if key == 'next_call':
                result[key] = None
            continue
        args = call.get('arguments') if isinstance(call, dict) and call.get('tool') == 'forsic_reporting' else call
        if not isinstance(args, dict) or len(json.dumps(call, ensure_ascii=False)) > 900:
            continue
        if args.get('pointer') != body.get('pointer'):
            continue
        offset = 0 if key == 'read_call' else body.get('next_offset')
        if type(offset) is not int or args.get('offset') != offset:
            continue
        if source:
            allowed = {'action', 'source_id', 'source_version', 'source_handle', 'pointer', 'offset', 'limit'}
            ref = body.get('source_ref')
            selected = handle_reference(args['source_handle']) if 'source_handle' in args else {
                'id': args.get('source_id'), 'version': args.get('source_version')}
            valid = args.get('action') == 'source' and isinstance(ref, dict) and selected == ref
        else:
            allowed = {'action', 'snapshot_id', 'question_id', 'question_ids', 'pointer', 'offset', 'limit'}
            valid = (args.get('action') in ('state', 'gaps') and isinstance(body.get('snapshot_id'), str)
                     and args.get('snapshot_id') == body['snapshot_id'])
        if valid and set(args) <= allowed:
            result[key] = deepcopy(call)
    return result if result.get('read_call') else {}


def execution_summary(body):
    """Keep returned lifecycle uncertainty, never infer it from requested actions."""
    def bounded(data, keys):
        return {k:data[k] for k in keys if k in data and
                (type(data[k]) in (bool,int) or isinstance(data[k],str) and len(data[k])<=120)}
    view = bounded(body, ('completion','result_commit','delivery','process_liveness'))
    if isinstance(body.get('command_outcome'), dict):
        view['command_outcome'] = bounded(body['command_outcome'], ('status', 'basis', 'individual_steps'))
    execution = body.get('execution')
    if isinstance(execution,dict):
        view['execution'] = bounded(execution,('state','backend','termination_confirmed','exit_code'))
        if isinstance(execution.get('cleanup'),dict):
            view['execution']['cleanup'] = bounded(execution['cleanup'],('confirmed','error_type','returncode'))
    call = body.get('execution_call')
    started_id = body.get('execution_started_id')
    if isinstance(call,dict) and call.get('tool')=='forsic_reporting':
        args = call.get('arguments',{})
        if isinstance(args,dict) and args.get('action')=='execution':started_id=args.get('started_id')
    if isinstance(started_id,str) and 0<len(started_id)<=160:
        from .execution_contract import lookup_call
        view['execution_lookup'] = lookup_call(started_id)
    return view


def review_navigation(body):
    """Keep delivered pending questions as navigation, not as current judgment."""
    advice = body.get('independent_review')
    if not isinstance(advice, dict):
        return {}
    status = advice.get('status')
    status = status if isinstance(status, str) and len(status)<=120 else 'unknown'
    pending = advice.get('pending_finding_ids')
    count = len(pending) if isinstance(pending, list) else None
    # Reprojection of our small navigation has no finding bodies/ID array.
    # Preserve its delivered count only for that complete presentation shape;
    # an arbitrary same-named field is neither a review count nor validation.
    if (count is None and advice.get('advice_body_omitted') is True
            and advice.get('role') == 'independent_advice_not_source_evidence'
            and advice.get('pending_scope') == 'material'
            and advice.get('lookup') == {'tool':'forsic_reporting','arguments':{'action':'review_inbox'}}
            and type(advice.get('pending_count')) is int and 0 <= advice['pending_count'] <= 2**53-1):
        count = advice['pending_count']
    view = {'status': status,
            'pending_count': count if status not in ('unavailable', 'unknown') else None,
            'pending_scope': 'material', 'advice_body_omitted': True,
            'lookup': {'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox'}},
            'role': 'independent_advice_not_source_evidence'}
    if type(advice.get('requires_followup')) is bool and status not in ('unavailable', 'unknown'):
        view['requires_followup'] = advice['requires_followup']
    review_id = advice.get('review_id')
    if isinstance(review_id, str) and 0<len(review_id)<=160:
        view['review_id'] = review_id
    # Already validated by the advisory producer. Select only whole delivered
    # questions and stable identities; omit quotes and reviewer private context.
    items = advice.get('pending_items')
    if isinstance(items, list):
        from .review_navigation import pending_items
        selected = pending_items({'status': status, 'pending_findings': items}, max_bytes=750)
        view.update(selected)
        omitted = advice.get('omitted_pending_items')
        if type(omitted) is int and type(view['omitted_pending_items']) is int:
            view['omitted_pending_items'] += omitted
        view['state_at_delivery'] = True
    return {'independent_review': view}


def shell_navigation(body, lookup):
    """Whole bounded export selectors, with no output body."""
    if body.get('tool') != 'forsic_shell' or not isinstance(lookup, dict):
        return {}
    from .report_driven.source_handles import handle_reference
    ref, handle = body.get('source_ref'), body.get('source_handle')
    def target(value):
        return (isinstance(value, dict)
            and set(value) <= {'kind', 'source_handle', 'pointer', 'export_line'}
            and value.get('kind') == 'workspace_export' and value.get('source_handle') == handle
            and isinstance(handle, str) and handle_reference(handle) == ref
            and value.get('pointer') in ('/stdout', '/stderr')
            and ('export_line' not in value or type(value['export_line']) is int and value['export_line'] >= 1)
            and len(json.dumps(value, ensure_ascii=False)) <= 900)
    result = {}
    if target(body.get('retained_export_target')):
        result['retained_export_target'] = deepcopy(body['retained_export_target'])
    selectors = body.get('retained_export_targets')
    if isinstance(selectors, list):
        selected = [value for value in selectors if target(value)]
        total = body.get('retained_export_target_count', len(selectors))
        total = max(len(selectors), total) if type(total) is int else len(selectors)
        result.update(retained_export_targets=deepcopy(selected[:4]), retained_export_target_count=total,
            omitted_retained_export_target_count=total-min(4, len(selected)))
    first = result.get('retained_export_target') or next(iter(result.get('retained_export_targets', [])), None)
    if first:
        result['shell_result_lookup'] = {'tool': 'forsic_reporting', 'arguments': {
            **lookup['arguments'], 'pointer': first['pointer'], 'limit': 4096}}
        # Keep the exact target above; retrieve the retained profile contract
        # instead of carrying all preparation options through compression.
        if isinstance(body.get('execution_policy', {}).get('triage'), dict) or 'triage' in body.get('policy_details_calls', {}):
            result['triage_profile_lookup'] = {'tool': 'forsic_reporting', 'arguments': {
                **lookup['arguments'], 'pointer': '/execution_policy/triage', 'limit': 4}}
    return result


def summarize(tool, args, content):
    try:
        body = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    if body.get('schema')=='forsic.sensitive.scan.v1':
        return {'tool':tool,'pruned':True,'body_omitted':True,'schema':body['schema'],
                **review_navigation(body),
                **{k:body[k] for k in ('source_ref','source_handle','execution','summary','resume_ref',
                                      'scan_id','error','error_code','limitations','findings_call',
                                      'coverage_call','input_refs_call') if k in body},
                'coverage':{k:v for k,v in body.get('coverage',{}).items() if k!='inputs'},
                'meaning':'Masked detector candidates in the retained scope. Not real people, live credential validity or exfiltration. Original excerpts belong only in the local report appendix.'}
    ref = body.get('source_ref')
    if not isinstance(ref, dict) or not isinstance(ref.get('id'), str):
        ref = {'id': body.get('evidence_id')} if tool in EVIDENCE_TOOLS | {'forsic_image_read', 'forsic_image_search', 'forsic_archive_read', 'forsic_archive_relist'} else {}
    view = {'tool': tool, 'pruned': True, 'body_omitted': True,
            'meaning': 'Body omitted here, not absent from the earlier result. Not current judgment or approval; check preserved calls/state and retrieve uncertain source spans.'}
    view.update(review_navigation(body))
    navigation = retained_navigation(body)
    navigation.update(review_inbox_navigation(body))
    view.update(navigation)
    # Unavailable capabilities describe failed work, never a verdict about the
    # submitted indicator. Keep prerequisite and exact schema discovery calls.
    capability_repair = {}
    for key in ('capability_recovery', 'prerequisite'):
        value = body.get(key)
        if isinstance(value, dict) and len(json.dumps(value, ensure_ascii=False)) <= 700:
            capability_repair[key] = deepcopy(value)
    if body.get('error_code') == 'prerequisite_unavailable':
        for key in ('outcome', 'delivery', 'scope', 'retry_when', 'indicator', 'indicator_type'):
            if isinstance(body.get(key), str) and len(body[key]) <= 240:
                capability_repair[key] = body[key]
        if body.get('automatic_retry') is False:
            capability_repair['automatic_retry'] = False
    view.update(capability_repair)
    if body.get('schema') == 'forsic.triage.v1':
        view['triage'] = {
            'profile': body.get('profile'),
            'coverage_state': body.get('coverage', {}).get('state'),
            'summary': {key: value for key, value in body.get('summary', {}).items()
                        if key in ('candidate_count', 'returned_candidates', 'omitted_candidates', 'counts_complete')
                        and type(value) in (int, bool)},
            'candidates_pointer': '/candidates', 'coverage_pointer': '/coverage',
            'adoption': 'candidates_only; source_recheck_before_explicit_note_adoption'}
        for key in ('scan_id', 'query_state', 'candidate_query', 'selection'):
            value = body.get(key)
            if value is not None and len(json.dumps(value, ensure_ascii=False)) <= 1800:
                view['triage'][key] = deepcopy(value)
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
        from .report_driven.source_handles import handle_reference
        handle = body.get('source_handle')
        if isinstance(handle, str) and len(handle) <= 512 and handle_reference(handle) == ref:
            view['source_handle'] = handle
    if isinstance(body.get('evidence_id'), str) and len(body['evidence_id']) <= 160:
        view['retained_result_id'] = body['evidence_id']
    view.update(execution_summary(body))
    view.update(shell_navigation(body, view.get('lookup')))
    error = body.get('error')
    question_repair = (body.get('error_code') in ('invalid_question_fields', 'invalid_review_response', 'invalid_citation_selection') and
                       isinstance(body.get('invalid_fields'),list) and isinstance(body.get('recovery'),dict))
    bounded_repair = (body.get('error_code') in ('mutation_payload_conflict', 'report_authoring_reference_invalid', 'report_authoring_input_invalid')
                       and isinstance(body.get('recovery'), dict)
                       and len(json.dumps(body['recovery'], ensure_ascii=False)) <= 1000)
    authoring_repair = (bounded_repair and body.get('error_code') == 'report_authoring_input_invalid'
                        and isinstance(body.get('input_problems'), list))
    source_repair = source_recovery(body) if error else {}
    argument_repair = {}
    recovery = body.get('argument_recovery')
    if error and body.get('error_code') == 'invalid_arguments' and isinstance(recovery, dict):
        call = recovery.get('schema_call', {})
        params = call.get('arguments') if isinstance(call, dict) else None
        names = params.get('names') if isinstance(params, dict) else None
        if (isinstance(call, dict) and call.get('tool') == 'tool_describe'
                and isinstance(names, list) and len(names) == 1
                and isinstance(names[0], str) and names[0].startswith('forsic_')
                and recovery.get('automatic_retry') is False
                and len(json.dumps(recovery, ensure_ascii=False)) <= 700):
            argument_repair['argument_recovery'] = deepcopy(recovery)
    if error:
        # Even pre-execution/binding failures without a source ID must survive.
        # Do not infer rollback or successful storage from submitted arguments.
        view['error_present'] = True
        message = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
        view['error'] = message if len(message) <= 600 else message[:300] + ' … ' + message[-297:]
        view['error_truncated'] = len(message) > 600
        if type(body.get('write_applied')) is bool:
            view['write_applied'] = body['write_applied']
        if bounded_repair:
            view['recovery'] = deepcopy(body['recovery'])
        if authoring_repair:
            view['input_problems'] = deepcopy(body['input_problems'])
            count = body.get('omitted_problem_count', 0)
            view['omitted_problem_count'] = count if type(count) is int and count >= 0 else 0
        if question_repair:
            view['invalid_fields']=deepcopy(body['invalid_fields'])
            view['recovery']=deepcopy(body['recovery'])
            for key in ('invalid_field_count', 'omitted_invalid_fields'):
                if type(body.get(key)) is int and body[key] >= 0:
                    view[key] = body[key]
        for key in ('error_type', 'error_code', 'error_subtype', 'failure_stage', 'execution_status'):
            if isinstance(body.get(key), str) and len(body[key]) <= 120:
                view[key] = body[key]
        from .sensitive_policy import REMEDIES
        admission = body.get('admission_failure_code')
        if isinstance(admission, str) and 'sensitive_' + admission in REMEDIES:
            view.update(admission_failure_code=admission, next_step=REMEDIES['sensitive_' + admission])
        view.update(source_repair)
        view.update(argument_repair)
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
    if 'lookup' not in view and not error and not view.get('returned_records') and 'execution_lookup' not in view and 'independent_review' not in view and not navigation:
        return None
    # Inputs and actual returned scope are separate. Omitted paths/fields are not guessed.
    requested = ('action', 'path', 'file_path', 'volume_offset', 'member_index',
                 'start_line', 'offset', 'length', 'text', 'recursive')
    from .report_driven.measurement import scope_view
    view['returned_scope'] = scope_view(body, ('status','verified','returncode','exit_code','job_id',
        'execution_status','selected_volume_offset','file_path','search_path','member_index','total_lines',
        'identity_strength','complete','has_next_cursor','truncated','error_code','execution_rejected','backend_started'))
    for key, source, fields in (('requested_scope', args, requested),):
        view[key] = {k: source[k] for k in fields if k in source and
                     (source[k] is None or type(source[k]) in (bool, int, float) or
                      isinstance(source[k], str) and len(source[k]) <= 240)}
        omitted = [k for k in fields if k in source and k not in view[key]]
        if omitted:
            view[key]['omitted_fields'] = omitted
    if 'error' in body:
        view['error_present'] = bool(body['error'])
    def image_selection(value):
        return (isinstance(value, dict) and set(value) == {'id', 'version', 'index'}
                and all(isinstance(value[k], str) and 0 < len(value[k]) <= 160 for k in ('id', 'version'))
                and type(value['index']) is int and value['index'] >= 0)
    if not error and image_selection(body.get('image_ref')):
        view['image_ref'] = deepcopy(body['image_ref'])
    if not error and isinstance(body.get('files'), list):
        selected = [row for row in body['files'] if isinstance(row, dict) and image_selection(row.get('image_ref'))]
        if selected:
            view['image_selections'] = [{'path': row['path'], 'image_ref': deepcopy(row['image_ref'])}
                for row in selected[:4] if isinstance(row.get('path'), str) and len(row['path']) <= 240]
            view['omitted_image_selections'] = len(selected) - len(view['image_selections'])
    for field in ('matches', 'lines', 'entries', 'skipped'):
        if isinstance(body.get(field), list):
            view['returned_' + field + '_count'] = len(body[field])
    if (body.get('representation') == 'directory_inventory' and 'lookup' in view
            and isinstance(body.get('entries'), list)):
        # A bounded navigation selection, never a body citation or review verdict.
        selected_ref = {'id': view['lookup']['arguments']['source_id']}
        if 'source_version' in view['lookup']['arguments']:
            selected_ref['version'] = view['lookup']['arguments']['source_version']
        inventory = {'source_ref': selected_ref, 'entries': [], 'omitted_entries': len(body['entries'])}
        for index, entry in enumerate(body['entries'][:8]):
            if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
                continue
            item = {'pointer': '/entries/' + str(index), 'path': entry['path']}
            for key in ('bytes', 'mode'):
                if key in entry:
                    item[key] = entry[key]
            if len(json.dumps(item, ensure_ascii=False)) > 700:
                continue
            inventory['entries'].append(item); inventory['omitted_entries'] -= 1
        view['inventory'] = inventory
    if 'next_cursor' in body:
        view['continuation_available'] = body['next_cursor'] is not None
    elif 'has_next_cursor' in body:
        view['continuation_available'] = body['has_next_cursor']
    if body.get('continuation_kind') == 'retained_search' and isinstance(body.get('next_call'), dict):
        # Exact selection survives pruning. Still a query continuation, never a body citation.
        view['next_call'] = body['next_call']
    relist = body.get('relist_call')
    if isinstance(relist, dict) and relist.get('tool') == 'forsic_archive_relist':
        arguments = relist.get('arguments')
        selection = arguments.get('listing_ref') if isinstance(arguments, dict) else None
        if (isinstance(arguments, dict) and set(arguments) == {'listing_ref'}
                and isinstance(selection, dict) and set(selection) == {'id', 'version'}
                and all(isinstance(selection[key], str) and 0 < len(selection[key]) <= 160 for key in selection)):
            view['relist_call'] = deepcopy(relist)
    # Preserve intact references, not chopped JSON, when unusual paths consume the budget.
    encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    while len(encoded) > 2000 and view.get('image_selections'):
        view['image_selections'].pop(); view['omitted_image_selections'] += 1
        encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    while len(encoded) > 2000 and view.get('inventory', {}).get('entries'):
        view['inventory']['entries'].pop(); view['inventory']['omitted_entries'] += 1
        encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    if len(encoded) > 2000:
        if not source_repair and not navigation:
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
    # Long diagnostics must not erase the exact execution lookup or uncertainty.
    review = view.get('independent_review', {})
    while len(encoded) > 2000 and review.get('pending_items'):
        review['pending_items'].pop()
        if type(review.get('omitted_pending_items')) is int:
            review['omitted_pending_items'] += 1
        encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    for field in ('error', 'returned_scope', 'meaning'):
        if len(encoded) <= 2000:break
        if field in view:
            view.pop(field)
            view[field + '_omitted'] = True
            encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    # Drop complete navigation items with counts, never chop an exact selector.
    while len(encoded) > 2000 and view.get('retained_export_targets'):
        view['retained_export_targets'].pop()
        view['omitted_retained_export_target_count'] += 1
        encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    if source_repair or argument_repair or capability_repair:
        # Recovery inputs outrank previews and repeated identity. Preserve the
        # failed version and requested pointer separately from the retry call.
        for field in ('lookup', 'retained_result_id', 'text_fields_present_in_original_message',
                      'error_type', 'failure_stage'):
            if len(encoded) <= 2000:
                break
            view.pop(field, None)
            encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
        if len(encoded) > 2000:
            view = {key: view[key] for key in ('tool', 'pruned', 'body_omitted', 'error_present', 'error_code')}
            view.update(source_repair)
            view.update(argument_repair)
            view.update(capability_repair)
            encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    if navigation and not error and len(encoded) > 2000:
        # These are exact navigation inputs, not adopted evidence. Keep both the
        # current field and continuation instead of falling back to root lookup.
        view = {key: view[key] for key in ('tool', 'pruned', 'body_omitted')}
        view.update(navigation)
        encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    if question_repair and len(encoded)>2000:
        # Exact selections are indivisible. Omit complete choices/problems with
        # counts instead of cutting IDs, gap text, or recovery JSON mid-string.
        choices=view['recovery'].pop('gap_choices',[])
        if choices:view['recovery']['omitted_gap_choices']=view['recovery'].get('omitted_gap_choices',0)+len(choices)
        for key in ('committed_gaps', 'proposed_gaps'):
            choices=view['recovery'].pop(key,[])
            if choices:view['recovery']['omitted_'+key]=view['recovery'].get('omitted_'+key,0)+len(choices)
        encoded=json.dumps(view,ensure_ascii=False,separators=(',',':'))
        while len(encoded)>2000 and view['invalid_fields']:
            view['invalid_fields'].pop()
            view['omitted_invalid_fields']=view.get('omitted_invalid_fields',0)+1
            encoded=json.dumps(view,ensure_ascii=False,separators=(',',':'))
    if authoring_repair:
        while len(encoded) > 2000 and view['input_problems']:
            view['input_problems'].pop()
            view['omitted_problem_count'] += 1
            encoded = json.dumps(view, ensure_ascii=False, separators=(',', ':'))
    return encoded if len(encoded) <= 2000 else None
