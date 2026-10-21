"""Native model presentation only. The case ledger keeps the complete returns."""
import base64
import json


def _mission_summary(record):
    """Separate analyst-selected retained results from actual native executions."""
    retained = [item['id'] for item in record.get('reuse_result_refs', []) if item['kind'] == 'source']
    executed = list(record.get('result_ids', []))
    rules = {'purpose': 'discriminate' if record.get('support_rule') or record.get('refute_rule') else 'discover',
             'clipped_fields': []}
    for key in ('target_proposition', 'support_rule', 'refute_rule', 'inconclusive_rule'):
        value = record.get(key)
        if isinstance(value, str) and len(value) > 320:
            value = value[:320] + '…'
            rules['clipped_fields'].append(key)
        rules[key] = value
    return {'design_timing': record['design_timing'],
        'retained_result_ids': retained[:20], 'retained_result_count': len(retained),
        'omitted_retained_result_count': max(0, len(retained) - 20),
        'execution_result_ids': executed[:20], 'execution_result_count': len(executed),
        'omitted_execution_result_count': max(0, len(executed) - 20),
        'assessment_rules': rules}


def _shell_view(view, source):
    """Omit only known repeated metadata; execution and restrictions stay visible."""
    from .report_driven.model_pages import source_read_call

    def call(pointer):
        return {'tool': 'forsic_reporting', 'arguments': {
            **source_read_call(source, pointer, view['source_handle']), 'limit': 25}}

    policy = view.get('execution_policy')
    if isinstance(policy, dict):
        repeated = ('triage', 'image_access', 'structured_query', 'login_records', 'sensitive_scan')
        compact = {k: v for k, v in policy.items() if k not in (*repeated, 'environment')}
        details = {k: call('/execution_policy/' + k) for k in repeated if k in policy}
        if 'environment' in policy:
            details['environment'] = call('/execution_policy/environment')
        view['execution_policy'] = compact
        view['policy_details_calls'] = details
        view['omitted_fields'].extend('execution_policy/' + k for k in details)
        triage = policy.get('triage', {})
        view['capability_menu'] = {
            'triage_profiles': {name: {'target_os': item['target_os'], 'kind': item['kind']}
                for name, item in triage.get('profiles', {}).items()},
            'unavailable_profiles': triage.get('unavailable_profiles', {}),
            'availability': 'Declared profiles; installed engines and target-format support require checking. Unsupported is not absent evidence.',
            'helpers': {name: {key: policy[name][key] for key in ('help_command', 'skill', 'reference') if key in policy[name]}
                for name in ('image_access', 'structured_query', 'login_records') if name in policy},
            'sensitive_scan': {'action': 'sensitive_scan', 'skill': 'scan-sensitive-data'},
            'discovery_call': {'tool': 'forsic_shell', 'arguments': {'action': 'capabilities'}}}
    if view.get('action') == 'capabilities':
        # The discovery script and JSON stdout duplicate structured capabilities.
        view['command_call'] = call('/command')
        view.pop('command', None)
        view['omitted_fields'].append('command')
        if view.get('runtime_capabilities', {}).get('state') == 'observed':
            view['stdout_call'] = call('/stdout')
            view.pop('stdout', None)
            view['omitted_fields'].append('stdout')
    return view


def _export_target(view):
    """Select only a complete retained receipt; staging still verifies its file."""
    from .sensitive_policy import export_receipt, export_receipt_lines, workspace_export_scope
    selectors, seen, total = [], set(), 0
    # The helper mirrors completed exports to stderr. Prefer stdout and omit
    # identical mirrored declarations without changing either retained stream.
    for stream in ('stdout', 'stderr'):
        pointer = '/' + stream
        try:
            workspace_export_scope(view, pointer)
        except ValueError:
            continue
        text = view.get(stream, '')
        try:
            receipts = [(None, export_receipt(text))]
        except (TypeError, ValueError):
            receipts = export_receipt_lines(text)
        for line, receipt in receipts:
            identity = json.dumps(receipt, sort_keys=True, ensure_ascii=False)
            # Repeated receipts within one stream retain their exact selectors.
            if stream == 'stderr' and identity in seen:
                continue
            total += 1
            if len(selectors) < 64:
                target = {'source_handle': view['source_handle'], 'pointer': pointer, 'kind': 'workspace_export'}
                if line is not None:
                    target['export_line'] = line
                selectors.append(target)
            if stream == 'stdout':
                seen.add(identity)
    if not selectors:
        return
    if total == 1 and 'export_line' not in selectors[0]:
        view['retained_export_target'] = selectors[0]
    else:
        view.update(retained_export_targets=selectors, retained_export_target_count=total,
            omitted_retained_export_target_count=total-len(selectors))
    view['retained_export_selection_note'] = ('These are derived selectors over unchanged retained stdout/stderr. '
        'Copy one exact target, including export_line when present, after choosing a profile and its required format/ABI. '
        'Admission still verifies source version, conversation ownership, complete receipt, file boundaries and exported-byte hash; this selector is not admission or coverage.')


def evidence_view(result, case):
    from .archive_selection import result_view
    result = result_view(result, case)
    from .search_selection import result_view as search_view
    result = search_view(result, case)
    from .report_driven.host import source_record
    from .report_driven.model_pages import source_read_call
    from .report_driven.source_handles import source_handle
    event = case.event(result.get('evidence_id', ''))
    if event and event['kind'] == 'tool_result':
        source = source_record(event)
        handle = source_handle(case, source)
        result = {**result, 'source_ref': {key: source[key] for key in ('id', 'version')},
                  'source_handle': handle}
    if result.get('schema')=='forsic.sensitive.scan.v1':
        from .sensitive_policy import model_view
        return model_view(result)
    if result.get('tool')=='forsic_shell' and isinstance(result.get('raw_output'),dict):
        from .report_driven.host import source_record
        source=source_record(case.event(result['evidence_id']))
        raw=result['raw_output']
        view={k:v for k,v in result.items() if k!='raw_output'}
        view['raw_output_metadata']={k:v for k,v in raw.items() if k not in ('stdout_base64','stderr_base64')}
        view['omitted_fields']=['raw_output/stdout_base64','raw_output/stderr_base64']
        view['source_ref']={'id':source['id'],'version':source['version']}
        view['raw_output_calls']={stream:{'tool':'forsic_reporting','arguments':{
            **source_read_call(source, '/raw_output/'+stream+'_base64', view['source_handle']), 'limit':4096}}
            for stream in ('stdout','stderr')}
        view['presentation_note']='stdout/stderr are unchanged retained UTF-8 previews of derived command output. Full captured bytes remain in raw_output and are paged as base64 through raw_output_calls; neither view establishes original evidence content or coverage.'
        _export_target(view)
        return _shell_view(view, source)
    if not isinstance(result.get('text'),str) or not isinstance(result.get('base64'),str):
        return result
    from .report_driven.host import source_record
    source=source_record(case.event(result['evidence_id']))
    raw=base64.b64decode(result['base64'],validate=True)
    view={k:v for k,v in result.items() if k!='base64'}
    view.update(source_ref={'id':source['id'],'version':source['version']},
        text_matches_raw_utf8=result['text'].encode('utf-8')==raw,
        omitted_fields=['base64'],
        raw_bytes_call={'tool':'forsic_reporting','arguments':{'action':'source',
            **source_read_call(source, '/base64', view['source_handle']), 'limit':4096}})
    view['presentation_note']='Base64 remains in the retained result; omitted from this view. Citation offsets are UTF-8 bytes in retained text, not original file offsets. If text_matches_raw_utf8 is false, decoding changed bytes; use raw_bytes_call for exact bytes.'
    return view


def mutation_view(result, case):
    if result.get('error') or not any(k in result for k in ('mission','assessment')):
        return result
    from .report_driven.host import current
    view=dict(result)
    fields={'mission':('id','version','state','question_ref','required_view'),
            'assessment':('id','version','test_ref','outcome')}
    state=current(case)
    for key,keep in fields.items():
        if key not in result: continue
        record=result[key]
        view[key]={k:record[k] for k in keep if k in record}
        index=next((i for i,r in enumerate(state[key+'s']) if r['id']==record['id']),None)
        current_record=state[key+'s'][index] if index is not None else None
        if index is not None:
            view['details_call']={'tool':'forsic_reporting','arguments':{'action':'state',
                'snapshot_id':state['meta']['snapshot_id'],'pointer':'/'+key+'s/'+str(index),'limit':25}}
        if key=='mission':
            view[key].update(_mission_summary(current_record or record))
            question=next((q for q in state['questions'] if q['id']==record['question_ref']['id']),None)
            if question:
                view['current_question']={k:question[k] for k in ('id','revision','definition_version','work_state')}
            view['current_mission_state']=current_record['state'] if current_record else 'not_current'
            if current_record and current_record.get('reuse_result_refs'):
                view['next_tool']='forsic_assess'
                view['next_step']='This after-result mission has saved the explicit retained_result_ids. execution_result_ids records only native calls made under this mission and may be empty. Select one retained result and its source citation_refs for forsic_assess; no physical execution is needed. If assessment_rules lists clipped_fields, read those conditions through details_call before judging.'
        if key=='mission' and current_record and current_record['state']=='draft' and question and question['work_state'] in ('open','active') and not record.get('reuse_result_refs') and record.get('proposed_tool_name'):
            from .report_driven.investigation_state import image_read_reference_call
            view['next_call']=image_read_reference_call(current_record) or {'tool':record['proposed_tool_name'],'arguments':{
                **record['tool_arguments'],'mission_id':record['id'],'mission_version':record['version']}}
            from .search_selection import native_search_call
            view['next_call']=native_search_call(view['next_call']['tool'],view['next_call']['arguments'])
    if result.get('question_update'):
        view['question_update']={k:v for k,v in result['question_update'].items() if k in (
            'assessment','claim_refs','judgment_review_required','work_state')}
    view['partial_view']=True
    view['semantic_approval']=False
    return view
