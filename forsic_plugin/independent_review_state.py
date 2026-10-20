"""Read-only independent review receipts joined to current question dispositions.

Receipts preserve reviewer suggestions; current notes alone own their disposition.
Exact quote occurrence is a coordinate check, never semantic endorsement.
"""
import json
import sqlite3


def _presented(request):
    messages=request.get('messages',[])
    body=json.loads(next(m['content'] for m in messages if m.get('role')=='user'))
    result={}
    for source in body.get('sources',[]):
        values=result.setdefault(source['source_id'],{})
        for field in source.get('fields',[]):
            existing=values.get(field['pointer'])
            if existing is None:values[field['pointer']]=field['value']
            elif existing!=field['value']:
                values[field['pointer']]=(existing if isinstance(existing,list) else [existing])+[field['value']]
    return result


def _presented_handles(request):
    from . import independent_review as review
    if request.get('schema_version')!=3:return None
    body=json.loads(next(m['content'] for m in request['messages'] if m.get('role')=='user'))
    result={}
    for source in body.get('sources',[]):
        if (source.get('source_version_basis')!='retained_event_json_bytes_sha256' or
                not isinstance(source.get('source_version'),str) or
                not source['source_version'].startswith('event-sha256:')):
            raise ValueError('Review source version is unavailable')
        for field in source.get('fields',[]):
            handle=review._field_handle(source,field)
            if field.get('review_ref')!=handle:raise ValueError('Review field handle does not match its exact presented value')
            result[handle]=review._review_coordinate(source,field)
    return result



def review_state(resolved,state=None):
    from . import independent_review as review
    result={'status':'not_reviewed','review_id':'','findings':[],'pending_findings':[],
            'pending_finding_ids':[],'requires_followup':False,'dispositions':[],
            'historical_review_ids':[],'stale_review_ids':[],'latest_stage':None,
            'final_review_current':False,'latest_final_review_id':'','latest_final_status':None,
            'inflight_review_ids':[],'rejected_findings':[],'invalid_dispositions':[],
            'judgment_review_questions':[]}
    try:
        receipts=review._receipts(resolved)
        if not receipts:return result
        inflight = set(review.inflight_review_ids(resolved))
        # Avoid loading all source result bodies through the report projection.
        questions=review._question_records(resolved) if state is None else [q for q in state.get('questions',[])
            if q.get('goal_id')==resolved['goal_id'] and q.get('session_id') in resolved['session_lineage']]
        result['judgment_review_questions']=[{'question_id':q['id'],'revision':q.get('revision',q.get('version')),
            'read_call':{'tool':'forsic_reporting','arguments':{'action':'state','question_id':q['id']}}}
            for q in questions if q.get('judgment_review_required')]
        answers={}
        from .report_driven.review_responses import validate_saved
        for question in questions:
            for answer in question.get('review_responses',[]):
                try:
                    validated=validate_saved(resolved['case'],state,answer,lineage=resolved['session_lineage'],question=question)
                    answers[(answer.get('review_id'),answer.get('finding_id'))]=dict(validated,question_id=question['id'],
                        question_revision=question.get('revision',question.get('version')))
                except (ValueError,KeyError,TypeError) as exc:
                    result['invalid_dispositions'].append({'question_id':question['id'],
                        'review_id':answer.get('review_id') if isinstance(answer,dict) else None,
                        'finding_id':answer.get('finding_id') if isinstance(answer,dict) else None,'reason':str(exc)})
        snapshot=(review.freshness(resolved) if resolved.get('goal') is not None else
                  {'fingerprint':None,'evaluation_fingerprint':None})
        current=snapshot['fingerprint']
        executive=[]
        for path,request in receipts:
            rid=request['request_id'];result['review_id']=rid;result['latest_stage']=request.get('stage','checkpoint')
            response=review._response_for(path,request)
            live = not response and rid in inflight
            if live:
                result['inflight_review_ids'].append(rid)
            if response and response.get('request_id')!=rid:raise ValueError('Review receipt identity mismatch')
            receipt_status=response.get('status','delivery_unconfirmed') if response else 'in_progress' if live else 'delivery_unconfirmed'
            result['status']=receipt_status
            if request.get('schema_version') not in (2,3):
                result['historical_review_ids'].append(rid)
                if request is receipts[-1][1]:result['status']='historical_unvalidated'
                continue
            if (current!=request.get('material_fingerprint') or request.get('stage')=='final'
                    and snapshot['evaluation_fingerprint']!=request.get('evaluation_fingerprint')):
                result['stale_review_ids'].append(rid)
                if request is receipts[-1][1]:result['status']='stale'
            validated=[];parsed=False;rejected=[]
            if response:
                # Recheck the original presented values, rather than trust mutable status flags.
                raw=response.get('raw_advice')
                if isinstance(raw,str) and response.get('finish_reason')=='stop':
                    try:
                        body=json.loads(next(m['content'] for m in request['messages'] if m.get('role')=='user'))
                        validated=review._parse(raw,_presented(request),rid,rejected,review_refs=_presented_handles(request),
                            executive_round=request.get('executive_round',0),
                            previous_questions=body.get('executive_followup',{}).get('previous_questions',()))
                        parsed=True
                        result['rejected_findings'].extend(dict(item,review_id=rid) for item in rejected)
                    except (ValueError,KeyError,TypeError,StopIteration) as exc:
                        result['rejected_findings'].append({'review_id':rid,'index':None,'reason':str(exc)[:500],
                            'role':'rejected_advice_not_evidence',
                            'recovery':'Use retained source evidence in the current question and record an explicit limitation; do not resubmit this review.'})
            if receipt_status=='received' and (not parsed or rejected and not validated):
                receipt_status='invalid_advice'
                if result['status']!='stale':result['status']=receipt_status
            if request.get('stage')=='final':
                result['latest_final_review_id']=rid
                result['latest_final_status']=receipt_status
                result['final_review_current']=(receipt_status=='received'
                    and current==request.get('material_fingerprint')
                    and snapshot['evaluation_fingerprint']==request.get('evaluation_fingerprint'))
            if request.get('executive_round'):
                number=request['executive_round']
                if type(number) is not int or number!=len(executive)+1 or number>review.EXECUTIVE_ROUNDS:
                    raise ValueError('Invalid executive round sequence')
                answered=sum((rid,f['finding_id']) in answers for f in validated)
                executive.append({'round':number,'review_id':rid,'status':receipt_status,
                    'question_count':len(validated),'answered':answered,
                    'complete':receipt_status=='received' and len(validated)==review.EXECUTIVE_QUESTIONS
                               and answered==review.EXECUTIVE_QUESTIONS})
            result['findings'].extend(validated)
            if not live and not validated and receipt_status in ('delivery_unconfirmed','incomplete','invalid_advice'):
                result['findings'].append({'review_id':rid,'finding_id':'availability-'+rid[:24],
                    'kind':'availability','impact':'material','question':'독립 검토 결과의 이용 가능성과 현재 답의 한계를 확인하세요.',
                    'why':'검토 호출은 기록됐지만 검증 가능한 완전한 조언이 수신되지 않았습니다.',
                    'next_check':'같은 입력을 재전송하지 말고 현재 근거로 조사한 뒤 검토 제한을 명시적으로 처분하세요.',
                    'quote_validation':'not_applicable'})
        known={(f['review_id'],f['finding_id']) for f in result['findings']}
        for key,answer in answers.items():
            if key not in known:
                result['invalid_dispositions'].append({'question_id':answer['question_id'],
                    'review_id':key[0],'finding_id':key[1],
                    'reason':'Disposition does not select a retained finding in this case, conversation and goal.'})
        for finding in result['findings']:
            response=answers.get((finding['review_id'],finding['finding_id']))
            if response:result['dispositions'].append(response)
            elif finding.get('impact')=='material':result['pending_findings'].append(finding)
        result['pending_finding_ids']=[f['finding_id'] for f in result['pending_findings']]
        result['requires_followup']=bool(result['pending_findings'] or result['inflight_review_ids'] or result['judgment_review_questions'])
        if executive:
            complete=len(executive)==review.EXECUTIVE_ROUNDS and all(r['complete'] for r in executive)
            blocked=next((r for r in executive if r['status'] not in ('received','in_progress')),None)
            result['executive_followup']={'round_limit':review.EXECUTIVE_ROUNDS,'questions_per_round':review.EXECUTIVE_QUESTIONS,
                'rounds':executive,'complete':complete,'semantic_approval':False,
                'status':'complete' if complete else 'blocked' if blocked else 'pending',
                'blocked_status':blocked['status'] if blocked else None}
            # The requested dialogue ends after the main investigator answers
            # round three. Requiring its pre-answer fingerprint would force a
            # fourth review forever; current validated dispositions are the fence.
            result['final_review_current']=complete and not result['judgment_review_questions']
            if complete:result['status']='received'
            elif all(r['complete'] for r in executive):result['requires_followup']=True
        if result['judgment_review_questions']:result['final_review_current']=False
        return result
    except (OSError,ValueError,KeyError,TypeError,StopIteration,sqlite3.Error):
        # Unknown read state is not evidence of no pending findings.
        result.update(status='unavailable',requires_followup=True)
        return result


def final_grounding(resolved,questions,max_chars=7000):
    """Selected analyst linkage plus bounded, independently checked retained spans.

    This is a coordinate/version check. It never establishes that a cited fact
    implies an answer, nor that a local excerpt covers a broader negative claim.
    """
    import hashlib
    from . import independent_review as review
    from .notes import EVIDENCE_TOOLS
    from .report_driven.host import source_record
    from .report_driven.model_pages import select
    from .report_driven.retained_scalar import NUMBER_COORDINATES, citation_context, number_literal
    result={'role':'analyst_claim_linkage_not_semantic_approval','claims':[],
            'observations':[],'unmet_expectations':[],'omitted':{'claims':0,'observations':0,'source_fields':0}}
    fields=[];remaining_claims=8;remaining_observations=12
    def retain(field,item):
        result[field].append(item)
        if len(review._json(result))+len(review._json(fields))>max_chars:
            result[field].pop();return False
        return True
    def unmet(kind,reference,reason):
        retain('unmet_expectations',{'kind':kind,'reference':reference,'reason':reason})
    with resolved['case'].connect() as db:
        def record(kind,reference):
            query="SELECT length(CAST(j.value AS BLOB)),substr(j.value,1,?) FROM events e,json_each(e.data,?) j WHERE e.kind='report_change' AND json_extract(j.value,'$.id')=? ORDER BY e.rowid DESC LIMIT 1"
            row=db.execute(query,(review.MAX_RECEIPT,'$.records.'+kind,reference['id'])).fetchone()
            if not row or row[0]>review.MAX_RECEIPT:return None
            value=json.loads(row[1])
            return value if value.get('version')==reference.get('version') else None
        def source(sid):
            meta=db.execute("SELECT rowid,kind,session,length(CAST(data AS BLOB)),json_extract(data,'$.tool') FROM events WHERE id=?",(sid,)).fetchone()
            value=None
            if meta and meta[1]=='tool_result' and meta[4] in EVIDENCE_TOOLS and meta[4]!='forsic_shell' and meta[3]<=review.MAX_RECEIPT:
                raw=db.execute('SELECT substr(data,1,?) FROM events WHERE rowid=?',(review.MAX_RECEIPT,meta[0])).fetchone()[0]
                data=json.loads(raw)
                if not data.get('error') and not data.get('exit_code') and not data.get('reused_from'):
                    event={'id':sid,'kind':meta[1],'session':meta[2],'data':data}
                    value=(event,source_record(event))
            return value
        for question in questions:
            references=question.get('claim_refs',[])
            for reference in references:
                if remaining_claims<=0:
                    result['omitted']['claims']+=1;continue
                remaining_claims-=1
                claim=record('claims',reference)
                if not claim:
                    result['omitted']['claims']+=1
                    unmet('claim',reference,'Selected claim record/version is unavailable or exceeds the bounded read.');continue
                view={k:claim[k] for k in ('id','version','status','statement','scope','assertion_kind','inference_strength',
                    'reasoning_summary','limitations','assumptions','source_refs','counterevidence_refs','semantic_review') if k in claim}
                view.update(question_id=question['id'],role='analyst_claim_not_source_evidence',semantic_validation=False)
                for key in ('statement','reasoning_summary','scope'):
                    if isinstance(view.get(key),str) and len(view[key])>1200:
                        view[key+'_omitted_characters']=len(view[key])-1200;view[key]=view[key][:1200]
                if not retain('claims',view):result['omitted']['claims']+=1;continue
                for role in ('source_refs','counterevidence_refs'):
                    for observation_ref in claim.get(role,[]):
                        if observation_ref.get('kind')!='observation':
                            unmet('observation',observation_ref,'Claim selects a source without an exact observation span.');continue
                        if remaining_observations<=0:
                            result['omitted']['observations']+=1;continue
                        remaining_observations-=1
                        observation=record('observations',observation_ref)
                        if not observation:
                            result['omitted']['observations']+=1
                            unmet('observation',observation_ref,'Selected observation record/version is unavailable.');continue
                        origin=observation.get('source_ref',{})
                        coordinate={k:observation.get(k) for k in ('id','version','source_ref','field_pointer','byte_start','byte_end','presented_view','coordinate_basis')}
                        coordinate.update(claim_id=claim['id'],claim_relation=role,role='analyst_selected_observation',
                                          retrieval={'action':'source','source_id':origin.get('id'),'source_version':origin.get('version'),
                                                     'pointer':observation.get('field_pointer'),'offset':observation.get('byte_start')})
                        try:
                            retained=source(origin['id'])
                            if not retained:raise ValueError('Source exceeds bounded read, is shell output, failed, or is unavailable; exact source retrieval remains required.')
                            event,metadata=retained
                            if metadata['version']!=origin.get('version'):raise ValueError('Retained source version does not match the selected observation.')
                            actual=select(event['data'],observation['field_pointer'])
                            literal=observation['literal'];start=observation['byte_start'];end=observation['byte_end']
                            numeric=not isinstance(actual,str)
                            raw=(number_literal(actual) if numeric else actual).encode()
                            if (not isinstance(literal,str) or not literal or len(literal)>1200
                                    or type(start) is not int or type(end) is not int or start<0
                                    or numeric and (start!=0 or literal.encode()!=raw)
                                    or end!=start+len(literal.encode()) or raw[start:end]!=literal.encode()
                                    or hashlib.sha256(literal.encode()).hexdigest()!=observation.get('canonical_sha256')):
                                raise ValueError('Literal/byte coordinates do not match the retained source field, or excerpt exceeds its bound.')
                            field={'source_id':origin['id'],'source_version':metadata['version'],'pointer':observation['field_pointer'],
                                   'value':literal,'byte_start':start,'byte_end':end,'presented_view':'metadata' if numeric else 'exact_excerpt'}
                            if numeric:coordinate.update(presented_view='metadata',coordinate_basis=NUMBER_COORDINATES)
                            coordinate.update(literal=literal,source_role=metadata['source_role'],acquisition_context=citation_context(event['data'],observation['field_pointer']),
                                              coordinate_validation='retained_source_version_and_exact_utf8_span_only',semantic_validation=False)
                            fields.append(field)
                            if not retain('observations',coordinate):
                                fields.pop();result['omitted']['observations']+=1;result['omitted']['source_fields']+=1
                        except (ValueError,KeyError,TypeError,IndexError) as exc:
                            result['omitted']['source_fields']+=1
                            coordinate.update(coordinate_validation='unverified',unmet_expectation=str(exc),semantic_validation=False,
                                              claimed_literal=str(observation.get('literal',''))[:1200])
                            if not retain('observations',coordinate):result['omitted']['observations']+=1
    return result,fields
