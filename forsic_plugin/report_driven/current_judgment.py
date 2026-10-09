"""Pure view of the existing note-owned answer and its selected claim records."""
from copy import deepcopy


def current_judgment(state, question):
    selected={r['id'] for r in question['claim_refs']}
    claims=[c for c in state['claims'] if c['id'] in selected and c['status'] not in ('retracted','superseded')]
    tests={t['id'] for t in state['tests'] if t['question_ref']['id']==question['id']}
    # Local test reasoning is explicitly separate from the aggregate judgment.
    local=[{'id':a['id'],'test_id':a['test_ref']['id'],'outcome':a['outcome'],
            'reasoning_summary':a['reasoning_summary'],'limitations':a.get('limitations',[]),
            'next_check':a.get('next_check','')} for a in state['assessments'] if a['test_ref']['id'] in tests]
    return {'question_id':question['id'],'revision':question['revision'],
            'definition_version':question['definition_version'],'target_proposition':question['target_proposition'],
            'scope':question['scope'],'answer':question['answer'],'assessment':question['assessment'],
            'review_required':question.get('judgment_review_required',False),
            'claim_refs':deepcopy(question['claim_refs']),
            'judgments':[{k:deepcopy(c.get(k,[] if k in ('assumptions','limitations','source_refs','counterevidence_refs') else 'unrated' if k=='inference_strength' else ''))
                          for k in ('id','assertion_kind','inference_strength','assumptions','reasoning_summary','limitations','source_refs','counterevidence_refs')} for c in claims],
            'alternatives':deepcopy(question.get('alternatives',[])),
            'next_checks':deepcopy(question.get('next_checks',[])),
            'reopen_conditions':deepcopy(question.get('reopen_conditions',[])),
            'local_assessments':local,
            'detail_pointer':'/questions/'+str(state['questions'].index(question))}


def current_claims(state):
    selected={r['id'] for q in state['questions'] for r in q['claim_refs']}
    candidates={'F-'+q['id'][2:] for q in state['questions'] if not q['claim_refs']}
    return [c for c in state['claims'] if c['id'] in selected|candidates and c['status'] not in ('retracted','superseded')]


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
    judgment=[{'id':c['id'],'assertion_kind':c['assertion_kind'],'inference_strength':c['inference_strength'],
               'assumptions':items(c['assumptions']),'limitations':items(c['limitations']),
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
            'latest_local_assessment_not_aggregate':local,'detail_pointer':full['detail_pointer']}
    result['omitted_details']=omitted
    return result
