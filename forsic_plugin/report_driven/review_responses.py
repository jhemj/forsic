"""Explicit reviewer dispositions through the existing atomic question writer."""
from copy import deepcopy


def schema():
    text = {'type': 'string', 'minLength': 1, 'pattern': r'\S'}
    return {'type': 'array', 'maxItems': 12, 'items': {
        'type': 'object', 'properties': {
            'review_id': deepcopy(text), 'finding_id': deepcopy(text),
            'disposition': {'type': 'string', 'enum': ['investigated', 'refuted', 'scope_limited']},
            'reason': deepcopy(text), 'limitation': deepcopy(text),
            'citation_refs': {'type': 'array', 'maxItems': 16, 'uniqueItems': True, 'items': deepcopy(text)}},
        'required': ['review_id', 'finding_id', 'disposition', 'reason', 'citation_refs'],
        'anyOf': [
            {'properties': {'disposition': {'enum': ['scope_limited']}},
             'required': ['limitation']},
            {'properties': {'disposition': {'enum': ['investigated', 'refuted']},
                            'citation_refs': {'minItems': 1}}}],
        'additionalProperties': False},
        'description': 'Handle exact independent review findings in this current answer. investigated/refuted require actual C1/C2 retained citations from the current conversation; scope_limited requires an explicit limitation. A review_ref selects advice only and is not a C1/C2 evidence citation.'}


def _citations(case,state,references,*,lineage,saved=None):
    from .assessment_basis import _citation
    from .grounded_judgment import selection
    from .host import source_record
    if not isinstance(references,list) or len(references)>16 or any(not isinstance(ref,str) for ref in references):
        raise ValueError('Review dispositions require actual C1/C2 evidence selections, not source dictionaries.')
    if len(set(references))!=len(references):raise ValueError('Select each review disposition citation once.')
    if saved is not None and (not isinstance(saved,list) or len(saved)!=len(references)):
        raise ValueError('Review disposition has no matching retained citation selections.')
    # The boundary projection deliberately does not load a full report state.
    # Reconstruct only sources explicitly selected by this saved disposition.
    if state is None:
        sources={}
        for citation in saved or []:
            if not isinstance(citation,dict):raise ValueError('Review disposition citation is malformed.')
            event=case.event(citation.get('source_id',''))
            if event is None:raise ValueError('Review disposition source is unavailable.')
            sources[event['id']]=source_record(event)
        state={'sources':list(sources.values())}
    citations=[]
    for index,reference in enumerate(references):
        selected=selection(case,state,reference)
        source,span=_citation(case,state,selected)
        event=case.event(source['id'])
        if event['session'] not in lineage:
            raise ValueError('Review followup evidence belongs to another conversation.')
        citation={**selected,'byte_end':span['byte_end'],
                  'source_ref':{'kind':'source','id':source['id'],'version':source['version']}}
        if saved is not None and saved[index]!=citation:
            raise ValueError('Review disposition citation does not match its current retained selection.')
        citations.append(citation)
    return citations


def origin_definition(case, question_id, response):
    """Recover a legacy answer's original note scope, never today's new scope."""
    import json
    from .investigation_state import definition_version
    keys=('review_id','finding_id','disposition','reason','limitation','citation_refs','citations')
    def identity(value):return {key:value.get(key, '' if key=='limitation' else []) for key in keys}
    with case.connect() as db:
        rows=db.execute("SELECT json_extract(e.data,'$.question'),json_extract(e.data,'$.target_proposition'),json_extract(e.data,'$.scope'),length(j.value),substr(j.value,1,300001) FROM events e,json_each(e.data,'$.investigation.review_responses') j WHERE e.kind='note' AND json_extract(e.data,'$.note_id')=? AND json_extract(j.value,'$.review_id')=? AND json_extract(j.value,'$.finding_id')=? ORDER BY e.rowid",(question_id.removeprefix('Q-'),response['review_id'],response['finding_id']))
        for question,proposition,scope,size,raw in rows:
            if size>300000:raise ValueError('Legacy disposition exceeds its bounded retained read.')
            if identity(json.loads(raw))==identity(response):
                return definition_version({'question':question,'target_proposition':proposition,'scope':scope or ''})
    raise ValueError('Legacy disposition has no retained question definition; explicitly reassess it.')


def bind_existing(case, note, old):
    """Preserve the original applicability identity before an answer/definition edit."""
    for response in note.get('investigation',{}).get('review_responses',[]):
        if not response.get('question_definition_version'):
            try:
                response['question_definition_version']=origin_definition(case,'Q-'+old['note_id'],response)
            except (ValueError,KeyError,TypeError):
                # Uncertain historical applicability remains pending in the read
                # projection; it must not block an unrelated note correction.
                pass


def validate_saved(case,state,response,*,lineage,question=None):
    """Recheck persisted evidence links; a claimed disposition is not followthrough."""
    if not isinstance(response,dict) or response.get('disposition') not in ('investigated','refuted','scope_limited'):
        raise ValueError('Unknown review disposition.')
    if any(not isinstance(response.get(key),str) or not response[key].strip()
           for key in ('review_id','finding_id','reason')):
        raise ValueError('Review disposition is missing its identity or reason.')
    if question is not None:
        from .investigation_state import definition_version
        original=response.get('question_definition_version') or origin_definition(case,question['id'],response)
        current=question.get('definition_version') or definition_version(question)
        if original!=current:
            raise ValueError('Review disposition belongs to an earlier question proposition or scope; explicitly reassess it.')
    limited=response['disposition']=='scope_limited'
    if limited and (not isinstance(response.get('limitation'),str) or not response['limitation'].strip()):
        raise ValueError('scope_limited requires an explicit limitation.')
    if not limited and not response.get('citation_refs'):
        raise ValueError('Investigation/refutation requires retained C1/C2 citations.')
    citations=_citations(case,state,response.get('citation_refs'),lineage=lineage,saved=response.get('citations',[]))
    return {**deepcopy(response),'citations':citations,'semantic_approval':False}


def validate_and_merge(case, state, note, submitted, *, lineage):
    """Validate references, not the semantic correctness of the analyst's decision."""
    from ..independent_review import review_state
    owner = note.get('session_id')
    if not note.get('goal_id') or not owner or owner not in lineage:
        raise ValueError('반론 처분에는 현재 목표와 대화에 연결된 질문이 필요합니다.')
    resolved = {'case': case, 'case_id': case.config['case_id'], 'goal_id': note['goal_id'],
                'conversation_id': owner, 'session_lineage': list(lineage)}
    # Receipt projection needs a read-only case, including during this writer's transaction.
    from ..evidence import Case
    resolved['case'] = Case(case.manifest, read_only=True)
    projected = review_state(resolved, state)
    if projected['status'] == 'unavailable':
        raise ValueError('독립 검토 기록을 확인하지 못해 처분을 저장하지 않았습니다.')
    findings = {(f['review_id'], f['finding_id']): f for f in projected['findings']}
    merged = {(r['review_id'], r['finding_id']): deepcopy(r)
              for r in note.get('investigation', {}).get('review_responses', [])}
    seen = set()
    for item in submitted:
        key = (item['review_id'], item['finding_id'])
        if key in seen:
            raise ValueError('같은 반론을 한 요청에서 두 번 처분할 수 없습니다.')
        seen.add(key)
        finding = findings.get(key)
        if finding is None:
            raise ValueError('현재 사건·대화·목표에서 검증된 review_id와 finding_id를 선택하세요.')
        limited = item['disposition'] == 'scope_limited'
        if finding.get('kind') == 'availability' and not limited:
            raise ValueError('검토 결과가 불명확한 호출은 재실행 대신 scope_limited로 한계를 기록하세요.')
        if limited and not str(item.get('limitation', '')).strip():
            raise ValueError('scope_limited에는 답변과 보고서에 남길 limitation이 필요합니다.')
        if not limited and not item['citation_refs']:
            raise ValueError('조사 또는 기각 처분에는 실제 C1/C2 원문 인용이 필요합니다.')
        citations = _citations(case,state,item['citation_refs'],lineage=lineage)
        merged[key] = {**deepcopy(item), 'citations': citations,
                       'question_definition_version': note['definition_version'], 'semantic_approval': False}
    return list(merged.values())


def previous_response(case, resolved, questions, review_id, finding_id):
    """Lazy bounded history lookup; old answers are never adopted by projection."""
    import json
    qids=[q['id'].removeprefix('Q-') for q in questions]
    if not qids:return None
    with case.connect() as db:
        query="SELECT e.rowid,e.id,json_extract(e.data,'$.note_id'),json_extract(e.data,'$.revision'),length(j.value),substr(j.value,1,6001) FROM events e,json_each(e.data,'$.investigation.review_responses') j WHERE e.kind='note' AND json_extract(e.data,'$.goal_id')=? AND coalesce(nullif(json_extract(e.data,'$.session_id'),''),e.session) IN ("+','.join('?' for _ in resolved['session_lineage'])+") AND json_extract(e.data,'$.note_id') IN ("+','.join('?' for _ in qids)+") AND json_extract(j.value,'$.review_id')=? AND json_extract(j.value,'$.finding_id')=? ORDER BY e.rowid DESC LIMIT 1"
        row=db.execute(query,(resolved['goal_id'],*resolved['session_lineage'],*qids,review_id,finding_id)).fetchone()
        if not row:return None
        offset=db.execute("SELECT count(*) FROM events WHERE kind='note' AND json_extract(data,'$.note_id')=? AND rowid>?",(row[2],row[0])).fetchone()[0]
    result={'status':'historical_not_current_accepted','question_id':'Q-'+row[2],
        'note_revision':row[3],'note_event_id':row[1],
        'read_call':{'tool':'forsic_note','arguments':{'action':'get','note_id':row[2],
            'include_history':True,'offset':offset,'limit':1}}}
    if row[4]>6000:
        result['response_omitted']='Historical response exceeds the bounded inbox detail; use its exact note history read.'
    else:
        response=json.loads(row[5])
        result['response']={k:response[k] for k in
            ('review_id','finding_id','disposition','reason','limitation','citation_refs','question_definition_version') if k in response}
    return result
