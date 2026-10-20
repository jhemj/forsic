"""Read retained independent advice and answer it through the single note writer."""
from copy import deepcopy


class ReviewInputError(ValueError):
    """Rejected response fields; no inferred answer, citation or write retry."""
    def __init__(self, problems, *, question_id=None):
        import json
        fields = deepcopy(problems[:8])
        # Keep exact whole diagnostics; unusually large field sets are omitted,
        # never shortened into a different selector or proposed value.
        while fields and len(json.dumps(fields, ensure_ascii=False).encode('utf-8')) > 6000:
            fields.pop()
        recovery = {'action': 'review_response', 'input_location': 'payload',
            'read_call': {'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox'}},
            'meaning': 'Repair the named fields in the original payload. Select the current question revision from review_inbox; no answer or evidence was substituted.'}
        if isinstance(question_id, str) and 0 < len(question_id) <= 160:
            recovery['question_id'] = question_id
        self.details = {'error_code': 'invalid_review_response', 'write_applied': False,
            'invalid_fields': fields, 'invalid_field_count': len(problems),
            'omitted_invalid_fields': max(0, len(problems)-len(fields)), 'recovery': recovery}
        super().__init__('Invalid review response: '+json.dumps(fields, ensure_ascii=False)+
                         '. No question was changed. Repair the named fields in payload.')


def response_schema(*, native=False):
    from .review_responses import schema
    item = deepcopy(schema()['items'])
    text = {'type': 'string', 'minLength': 1, 'pattern': r'\S'}
    item['properties'].update(operation_id={**text, 'maxLength': 128}, question_id=text,
                              expected_revision={'type': 'integer', 'minimum': 1})
    item['properties']['limitation']['description'] = 'Required for scope_limited: exact unperformed scope and reason.'
    item['properties']['citation_refs']['description'] = 'Select actual retained C1/C2 references; investigated/refuted require at least one. scope_limited may use an empty array.'
    item['required'] += ['operation_id', 'question_id', 'expected_revision']
    if native:
        # Native transports remove root combinators. Keep the field explicit in
        # their grammar without inventing a non-applicable limitation.
        item['properties']['limitation'] = {'type': 'string',
            'description': 'Required for scope_limited: exact unperformed scope and reason, nonblank. For investigated/refuted, use an empty string when no limitation applies.'}
        item['required'].append('limitation')
    return item


def native_response_view(result, *, legacy_input=False):
    """Project canonical payload diagnostics onto the native tool's flat inputs."""
    if result.get('error_code') != 'invalid_review_response':
        return result
    view = deepcopy(result)
    for field in view.get('invalid_fields', []):
        path = field.get('path', '')
        if path == 'payload' or path.startswith('payload.'):
            field['path'] = 'top_level' if path == 'payload' else path[len('payload.'):]
    view['recovery'].update(tool='forsic_review_response', input_location='top_level',
        meaning='Repair the named top-level fields without action/payload. Select the current question revision from review_inbox; no answer or evidence was substituted.')
    view['recovery'].pop('action', None)
    if legacy_input:
        # Runtime registration does not prove an older session pinned this tool.
        view['recovery']['legacy_input'] = {
            'when': 'Only if forsic_review_response is absent from the pinned tool menu for this session.',
            'tool': 'forsic_reporting', 'arguments': {'action': 'review_response'},
            'input_location': 'payload', 'fields': 'Put the same repaired fields inside payload.'}
    import json
    view['error'] = 'Invalid review response: '+json.dumps(view['invalid_fields'], ensure_ascii=False)+'. No question was changed. Repair the named top-level fields in forsic_review_response.'
    return view


def _resolve(case, args):
    from ..investigation_context import resolve_context
    options = args.get('_independent_review_options') or {}
    if not options.get('session_id') or not options.get('intake_root'):
        raise ValueError('Independent review lookup requires the current native conversation binding.')
    resolved = resolve_context(options['session_id'], intake_root=options['intake_root'],
                               native_db=options.get('native_db'))
    if (not resolved or not resolved['goal_id'] or resolved['case'].manifest != case.manifest
            or resolved['goal_id'] != args.get('_goal_id')
            or resolved['conversation_id'] != args.get('_session_id')):
        raise ValueError('Independent review belongs to another case, conversation or goal.')
    return resolved


def inbox(case, args):
    """No model call, report snapshot, or evidence adoption; IDs survive compaction."""
    from .. import independent_review as review
    resolved = _resolve(case, args)
    projected = review.review_state(resolved)
    questions = review._question_records(resolved)
    qid, rid, fid = (args.get(k) for k in ('question_id', 'review_id', 'finding_id'))
    if qid:
        questions = [q for q in questions if q['id'] == qid]
        if not questions:
            raise ValueError('Choose a current question owned by this conversation and goal.')
    if fid and not rid:
        raise ValueError('finding_id requires its exact review_id.')
    findings = projected['findings'] if rid else projected['pending_findings']
    if rid:
        findings = [f for f in findings if f['review_id'] == rid and (not fid or f['finding_id'] == fid)]
        if not findings and projected['status'] != 'unavailable':
            raise ValueError('Unknown review_id or finding_id in this case, conversation and goal.')
    dispositions = {(d['review_id'], d['finding_id']): d for d in projected['dispositions']}
    items = []
    for finding in findings:
        key = (finding['review_id'], finding['finding_id'])
        item = {k: deepcopy(finding[k]) for k in ('review_id', 'finding_id', 'question', 'why',
            'next_check', 'kind', 'impact', 'executive_round', 'review_ref', 'quote_validation') if k in finding}
        item['status'] = 'answered' if key in dispositions else 'pending'
        if key in dispositions:
            item['response'] = deepcopy(dispositions[key])
        invalid=next((d for d in projected['invalid_dispositions']
                      if (d.get('review_id'),d.get('finding_id'))==key),None)
        if invalid:
            item['response_applicability']={'status':'requires_explicit_revalidation','reason':invalid['reason'],
                'question_id':invalid['question_id']}
            previous=next((r for q in questions if q['id']==invalid['question_id']
                           for r in q.get('review_responses',[]) if (r['review_id'],r['finding_id'])==key),None)
            if previous:
                import json
                selected={k:deepcopy(previous[k]) for k in
                    ('disposition','reason','limitation','citation_refs','question_definition_version') if k in previous}
                if len(json.dumps(selected,ensure_ascii=False))<=6000:item['previous_response']=selected
                else:item['previous_response_omitted']='Prior response exceeds inbox detail; read its current owning question.'
                item['previous_response_read_call']={'tool':'forsic_reporting','arguments':{
                    'action':'state','question_id':invalid['question_id']}}
        if item['status']=='pending' and not invalid:
            if rid and fid:
                from .review_responses import previous_response
                prior=previous_response(case,resolved,questions,rid,fid)
                if prior:item['historical_response']=prior
            else:
                item['historical_response_lookup']={'tool':'forsic_reporting','arguments':{
                    'action':'review_inbox','review_id':finding['review_id'],'finding_id':finding['finding_id'],
                    **({'question_id':qid} if qid else {})}}
        if finding.get('source_id'):

            # Review versions hash retained event bytes; source paging has its own
            # version. Discovery supplies that version before exact field paging.
            item['source_reference'] = {k: deepcopy(finding[k]) for k in
                ('source_id', 'source_version', 'pointer', 'quote', 'byte_start') if k in finding}
            item['source_lookup'] = {'tool': 'forsic_reporting', 'arguments': {
                'action': 'source', 'source_id': finding['source_id']}}
        items.append(item)
    return {'kind': 'independent_review_inbox', 'status': projected['status'],
        'case_id': resolved['case_id'], 'goal_id': resolved['goal_id'],
        'conversation_id': resolved['conversation_id'], 'requires_followup': projected['requires_followup'],
        'pending_count': len(projected['pending_findings']),
        'pending_finding_ids': projected['pending_finding_ids'],
        'executive_followup': projected.get('executive_followup'), 'findings': items,
        'invalid_dispositions': projected['invalid_dispositions'],
        'stale_review_ids': projected['stale_review_ids'],
        'final_review_current': projected['final_review_current'],
        'judgment_review_questions': projected['judgment_review_questions'],
        'questions': [{k: q[k] for k in ('id', 'revision', 'question', 'scope') if k in q} for q in questions],
        'response_contract': {'tool': 'forsic_review_response', 'input_location': 'top_level',
            'legacy_input': {'when': 'Only if forsic_review_response is absent from the pinned tool menu for this session.',
                'tool': 'forsic_reporting', 'arguments': {'action': 'review_response'}, 'input_location': 'payload',
                'fields': 'Put the same required and conditional response fields in payload.'},
            'required': response_schema(native=True)['required'],
            'dispositions': ['investigated', 'refuted', 'scope_limited'],
            'conditional_fields': {'scope_limited': {'required': ['limitation']},
                'investigated': {'citation_refs_min_items': 1},
                'refuted': {'citation_refs_min_items': 1}},
            'evidence': 'Investigate with evidence tools, then select retained C1/C2 citations; scope_limited requires limitation.',
            'question': 'Select question_id and its current integer expected_revision from questions.'},
        'semantic_approval': False}


def respond(case, args):
    """Focused adapter inside host.invoke's existing mutation/replay context."""
    from .host import current
    from .investigation_state import input_problems, update_question, QuestionInputError
    resolved = _resolve(case, args)
    payload = args.get('payload')
    if (isinstance(payload, dict) and payload.get('disposition') in ('investigated', 'refuted')
            and payload.get('limitation') == ''):
        # Empty is the native projection's explicit not-applicable value. The
        # canonical writer and old callers retain their original optional field.
        payload = deepcopy(payload)
        payload.pop('limitation')
    schema = response_schema()
    problems = input_problems(payload, schema)
    if isinstance(payload, dict):
        disposition = payload.get('disposition')
        conditional = []
        if disposition == 'scope_limited' and 'limitation' not in payload:
            conditional.append({'path': 'payload.limitation', 'missing_fields': ['limitation'],
                                'field_schema': deepcopy(schema['properties']['limitation']),
                                'error': 'scope_limited requires an explicit limitation.'})
        if disposition in ('investigated', 'refuted') and payload.get('citation_refs') == []:
            conditional.append({'path': 'payload.citation_refs', 'minItems': 1,
                                'field_schema': {**deepcopy(schema['properties']['citation_refs']), 'minItems': 1},
                                'error': 'Select actual retained C1/C2 evidence for this disposition.'})
        if conditional:
            problems = [p for p in problems if 'anyOf_required' not in p] + conditional
    if problems:
        raise ReviewInputError(problems, question_id=payload.get('question_id') if isinstance(payload, dict) else None)
    fields = ('review_id', 'finding_id', 'disposition', 'reason', 'citation_refs', 'limitation')
    response = {k: deepcopy(payload[k]) for k in fields if k in payload}
    translated = {k: payload[k] for k in ('operation_id', 'question_id', 'expected_revision')}
    translated['review_responses'] = [response]
    owned_args = {**args, '_session_lineage': resolved['session_lineage']}
    try:
        return update_question(case, current(case), translated, owned_args)
    except QuestionInputError as exc:
        # The shared writer still enforces revision/ownership. Keep this focused
        # adapter's payload shape in recovery instead of switching entry tools.
        raise ReviewInputError(exc.details['invalid_fields'],
                               question_id=payload['question_id']) from exc
