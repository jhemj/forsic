"""Native model presentation only. The case ledger keeps the complete returns."""
import base64


def evidence_view(result, case):
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
            'source_id':source['id'],'source_version':source['version'],'pointer':'/base64','offset':0,'limit':4096}})
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
            question=next((q for q in state['questions'] if q['id']==record['question_ref']['id']),None)
            if question:
                view['current_question']={k:question[k] for k in ('id','revision','definition_version','work_state')}
            view['current_mission_state']=current_record['state'] if current_record else 'not_current'
        if key=='mission' and current_record and current_record['state']=='draft' and question and question['work_state'] in ('open','active') and not record.get('reuse_result_refs') and record.get('proposed_tool_name'):
            view['next_call']={'tool':record['proposed_tool_name'],'arguments':{
                **record['tool_arguments'],'mission_id':record['id'],'mission_version':record['version']}}
    if result.get('question_update'):
        view['question_update']={k:v for k,v in result['question_update'].items() if k in (
            'assessment','claim_refs','judgment_review_required','work_state')}
    view['partial_view']=True
    view['semantic_approval']=False
    return view
