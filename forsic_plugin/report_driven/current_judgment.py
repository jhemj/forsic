"""Pure view of the existing note-owned answer and its selected claim records."""
from copy import deepcopy


def question_claims(state, question, *, include_pending_candidates=False):
    """Select the note-owned current claims, never historical adoption receipts."""
    selected = {r['id'] for r in question.get('claim_refs', [])}
    if selected:
        registry={c['id']:c for c in state['claims']}
        return [registry[r['id']] for r in question['claim_refs'] if r['id'] in registry
                and registry[r['id']]['status'] not in ('retracted', 'superseded')]
    if question.get('judgment_review_required', False) and not include_pending_candidates:
        return []
    return [c for c in state['claims'] if c['id'] == 'F-' + question['id'][2:]
            and c['status'] == 'candidate']


def question_gaps(state, question):
    """Disposition, rather than an old note's remaining_gap_ids, owns applicability."""
    return sorted((g for g in state['gaps'] if g['question_ref']['id'] == question['id']
                   and g['disposition'] not in ('resolved', 'not_applicable')), key=lambda g:g['id'])


def analysis_declaration(claim):
    """Absence in old records is unknown; v2 omissions were never reviewed empty."""
    fields = ('counter_citation_refs','inference_strength','assumptions','limitations')
    declared = claim.get('declared_analysis_fields')
    return {'declaration_status':'not_recorded' if declared is None else 'recorded',
            'undeclared_analysis_fields':list(fields) if declared is None else [k for k in fields if k not in declared]}


def current_judgment(state, question, *, include_pending_candidates=False):
    claims = question_claims(state, question, include_pending_candidates=include_pending_candidates)
    tests={t['id']:t for t in state['tests'] if t['question_ref']['id']==question['id']}
    # Local test reasoning is explicitly separate from the aggregate judgment.
    local=[{'id':a['id'],'version':a['version'],'test_id':a['test_ref']['id'],'outcome':a['outcome'],
            'reasoning_summary':a['reasoning_summary'],'limitations':deepcopy(a.get('limitations',[])),
            'next_check':a.get('next_check',''),
            'result_ref':deepcopy(a.get('result_ref')),
            'observation_refs':deepcopy(a.get('observation_refs',[])),
            'target_scope':deepcopy(tests[a['test_ref']['id']].get('target_scope')),
            'result_scope':deepcopy(tests[a['test_ref']['id']].get('result_scope'))}
           for a in state['assessments'] if a['test_ref']['id'] in tests]
    return {'question_id':question['id'],'revision':question['revision'],
            'definition_version':question['definition_version'],'target_proposition':question['target_proposition'],
            'scope':question['scope'],'answer':question['answer'],'assessment':question['assessment'],
            'review_required':question.get('judgment_review_required',False),
            'claim_refs':[{'kind':'claim', 'id':c['id'], 'version':c['version']} for c in claims
                          if c['id'] in {r['id'] for r in question.get('claim_refs', [])}],
            'judgments':[{**{k:deepcopy(c.get(k,[] if k in ('assumptions','limitations','source_refs','counterevidence_refs') else 'unrated' if k=='inference_strength' else ''))
                          for k in ('id','version','statement','scope','status','assertion_kind','inference_strength','assumptions','reasoning_summary','limitations','source_refs','counterevidence_refs')},
                          **analysis_declaration(c)} for c in claims],
            'remaining_gaps':[{k:deepcopy(g[k]) for k in
                              ('id','version','original_obligation','disposition','impact','reason',
                               'basis_refs','feasible_next_action','reopen_conditions') if k in g}
                              for g in question_gaps(state, question)],
            'resolved_gaps':[{k:deepcopy(g[k]) for k in
                              ('id','version','original_obligation','disposition','reason',
                               'resolution_refs','reopen_conditions') if k in g}
                             for g in state['gaps'] if g['question_ref']['id']==question['id']
                             and g['disposition'] in ('resolved','not_applicable')],
            'alternatives':deepcopy(question.get('alternatives',[])),
            'next_checks':deepcopy(question.get('next_checks',[])),
            'reopen_conditions':deepcopy(question.get('reopen_conditions',[])),
            'forensic_context':deepcopy(question.get('forensic_context',{})),
            'local_assessments':local,
            'detail_pointer':'/questions/'+str(state['questions'].index(question))}


def current_claims(state, *, include_pending_candidates=False):
    selected={c['id'] for q in state['questions'] for c in question_claims(
        state, q, include_pending_candidates=include_pending_candidates)}
    return [c for c in state['claims'] if c['id'] in selected]


def authoring_judgment(state, question):
    """Material answer dependency, without navigation or unrelated note revisions."""
    # A review gate may hide an unadopted legacy candidate from readers, but
    # must not pretend its underlying statement changed. This dependency view
    # does not adopt it; status()/save() still enforce the pending judgment gate.
    full = current_judgment(state, question, include_pending_candidates=True)
    result = {key: deepcopy(full[key]) for key in
            ('question_id', 'definition_version', 'target_proposition', 'scope', 'answer',
             'assessment', 'claim_refs', 'judgments', 'remaining_gaps', 'resolved_gaps',
             'alternatives', 'next_checks', 'reopen_conditions', 'local_assessments')}
    def stable(value):
        if isinstance(value, dict):
            return {key:stable(item) for key,item in value.items()
                    if key != 'version' or value.get('kind') != 'question'}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value
    return stable(result)


def judgment_read_call(state, question):
    index = next(i for i, q in enumerate(state['questions']) if q['id'] == question['id'])
    return {'tool':'forsic_reporting','arguments':{
        'action':'state','question_id':question['id'],
        'snapshot_id':state['meta']['snapshot_id'],
        'pointer':'/questions/'+str(index)+'/current_judgment','offset':0}}


def navigation_view(state, question):
    """Bounded navigation; long judgment sentences are omitted, never half-asserted."""
    full=current_judgment(state,question)
    omitted=0
    def text(value,limit=260):
        nonlocal omitted
        if len(str(value).encode())<=limit:return value
        omitted+=1
        return '[omitted; read detail_pointer/current claim]'
    def items(values,limit=1):
        nonlocal omitted
        omitted+=max(0,len(values)-limit)
        return [text(v,180) for v in values[:limit]]
    claims=full['judgments'][:1]
    omitted+=max(0,len(full['judgments'])-1)
    def references(values):
        nonlocal omitted
        omitted+=max(0,len(values)-1)
        return deepcopy(values[:1])
    judgment=[{'id':c['id'],'status':c['status'],'assertion_kind':c['assertion_kind'],'inference_strength':c['inference_strength'],
               'declaration_status':c['declaration_status'],'undeclared_analysis_fields':c['undeclared_analysis_fields'],
               'assumptions':items(c['assumptions']),'limitations':items(c['limitations']),
               'source_refs':references(c['source_refs']),'source_ref_count':len(c['source_refs']),
               'counterevidence_refs':references(c['counterevidence_refs']),
               'counterevidence_ref_count':len(c['counterevidence_refs']),
               'reasoning_summary':text(c['reasoning_summary'])} for c in claims]
    local=full['local_assessments'][-1:]
    omitted+=max(0,len(full['local_assessments'])-1)
    local=[{'id':a['id'],'outcome':a['outcome'],'reasoning_summary':text(a['reasoning_summary']),
            'limitations':items(a['limitations']),'next_check':text(a['next_check'])} for a in local]
    result={'id':question['id'],'revision':question['revision'],'definition_version':question['definition_version'],
            'assessment':question['assessment'],'review_required':full['review_required'],
            'scope':text(question['scope']), 'answer_is_model_assessment':text(full['answer']),
            'judgments':judgment,'alternatives':items(full['alternatives']),
            'next_checks':items(full['next_checks']), 'reopen':items(full['reopen_conditions']),
            'latest_local_assessment_not_aggregate':local,
            'detail_pointer':judgment_read_call(state,question)['arguments']['pointer'],
            'details_call':judgment_read_call(state,question)}
    result['remaining_gap_count']=len(full['remaining_gaps'])
    result['remaining_gap_examples']=[{k:(text(g[k],200) if k=='reason' else deepcopy(g[k]))
                                     for k in ('id','disposition','reason','feasible_next_action')}
                                    for g in full['remaining_gaps'][:2]]
    omitted+=max(0,len(full['remaining_gaps'])-2)
    result['omitted_details']=omitted
    context = full['forensic_context']
    times = [t for t in state.get('timeline',[]) if t.get('question_id')==question['id']]
    if context or times:
        result['forensic']={'counts':context.get('counts',{}),
            'temporal_events':len(times),
            'temporal_recheck_count':sum(bool(t.get('temporal_context',{}).get('temporal_requires_recheck')) for t in times),
            'selected_dependency_changes':context.get('counts',{}).get('change_impacts',0),
            'meaning':'선택 근거·시간 관계의 변화입니다. 결론 유지·정정과 다음 검사 가치는 분석가가 판단합니다.',
            'detail_call':result['details_call']}
    return result
