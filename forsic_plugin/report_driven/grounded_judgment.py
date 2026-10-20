"""Explicit evidence selection for the existing note writer; no mission or model."""
from copy import deepcopy
import hashlib
import re

DIRECT = re.compile(r'C2:([0-9a-f]{32}):([0-9a-f]{64}):(0|[1-9][0-9]*)\Z')


ANALYSIS_FIELDS = ('counter_citation_refs', 'inference_strength', 'assumptions', 'limitations')


def schema(contract_version='v1'):
    string={'type':'string'}
    strings={'type':'array','items':string}
    aggregate = {'type':'object','properties':{
        'citation_refs':{**strings,'description':'Copy returned C1/C2 refs for the supporting retained text or whole finite number. Follow source field read_calls for other values; do not substitute a nearby field. No preparation or mission required.'},
        'counter_citation_refs':strings,
        'assertion_kind':{'type':'string','enum':['fact','interpretation']},
        'inference_strength':{'type':'string','enum':['favored','plausible','unrated']},
        'reasoning_summary':{**string,'description':'Compare the strongest answer fact to cited text/acquisition_context: observations, inferred links, strongest alternative. Commands/search patterns are not outputs; byte previews are not decoded coverage. Interpretation labels/later caveats cannot justify stronger facts.'},
        'assumptions':strings,'limitations':strings},
        'required':(['citation_refs','assertion_kind','reasoning_summary'] if contract_version == 'v2' else
                    ['citation_refs','counter_citation_refs','assertion_kind','inference_strength','reasoning_summary','assumptions','limitations']),
        'additionalProperties':False}
    claim = deepcopy(aggregate)
    claim['properties'].update(statement={**string,'pattern':r'\S'},title={**string,'pattern':r'\S'})
    claim['properties']['citation_refs']['minItems']=1
    claim['properties']['reasoning_summary']['pattern']=r'\S'
    claim['required'] = [*claim['required'],'statement']
    return {'description':'Use one compound judgment, or explicitly author claims with their own exact citations. Claims do not split or replace the full question answer automatically.',
            'anyOf':[aggregate, {'type':'object','properties':{'claims':{
                'type':'array','minItems':1,'items':claim,
                'description':'Explicit analyst-authored claims; each retains its own support, counterevidence and uncertainty. No inherited citations or semantic approval.'}},
                'required':['claims'],'additionalProperties':False}]}


def direct_choices(case, eid):
    from ..notes import EVIDENCE_TOOLS
    from .host import source_record
    from .measurement import field_role
    from .source_locator import nodes
    from .investigation_state import pending_result, failed_result
    event=case.event(eid)
    if not event or event['kind']!='tool_result' or event['data'].get('tool') not in EVIDENCE_TOOLS:
        raise ValueError('Select an actual retained evidence result')
    data=event['data'];start=case.event(data.get('started_id',''))
    if not start or start['kind']!='tool_start' or start['session']!=event['session'] or start['data'].get('execution_rejected'):
        raise ValueError('Evidence has no matching native execution receipt')
    if pending_result(data) or failed_result(data):
        raise ValueError('Failed or pending execution is not a body observation')
    source=source_record(event)
    choices=[]
    for pointer,value in nodes(data):
        if isinstance(value,str) and value and field_role(data,pointer)=='body':
            choices.append(dict(source_id=eid,source_version=source['version'],pointer=pointer,literal=value,byte_start=0))
            if len(choices)==8:break
    return source,choices


def read_refs(case, view):
    """Decorate only unchanged body fields actually included in the model return."""
    from .model_pages import select
    if view.get('error') or not view.get('evidence_id'):return view
    try:source,choices=direct_choices(case,view['evidence_id'])
    except (ValueError,KeyError,TypeError):return view
    refs=[]
    for index,c in enumerate(choices):
        try:presented=select(view,c['pointer'])
        except (ValueError,KeyError,IndexError,TypeError):continue
        if presented!=c['literal']:continue
        refs.append({'pointer':c['pointer'],'citation_ref':f"C2:{source['id']}:{source['content_sha256']}:{index}",
                     'presented_view':'full_field', 'text_preview':c['literal'][:120],
                     'preview_complete':len(c['literal'])<=120})
    if not refs:return view
    return {**view,'presented_spans':refs,'citation_scope':'Up to 8 displayed fields have direct refs; use source lookup for other spans. These references select only the displayed retained fields; not the entire source file, execution success or attribution.'}


def selection(case,state,value):
    from .assessment_basis import _selection
    match=DIRECT.fullmatch(value) if isinstance(value,str) else None
    if not match:return _selection(case,state,value)
    source,choices=direct_choices(case,match[1]);index=int(match[3])
    if source['content_sha256']!=match[2] or index>=len(choices):
        raise ValueError('Stale or invalid presented-span reference; read the retained source')
    return choices[index]


def records(case,state,note,judgment,contract_version='v1'):
    """Called under the note transaction: mechanically bind analyst selections."""
    from .assessment_basis import _citation
    from .host import digest, ref
    from .contracts import Claim, Observation
    from .investigation_state import input_problems
    problems=input_problems(judgment,schema(contract_version),'judgment')
    if problems:raise ValueError('Invalid judgment: '+str(problems))
    observations=[];claims=[];source_ids=[]
    for index, submitted in enumerate(judgment.get('claims',[judgment])):
        if not submitted['citation_refs'] or not submitted['reasoning_summary'].strip():
            raise ValueError('Every grounded claim needs explicit evidence selections and reasoning')
        analysis={'counter_citation_refs':[], 'inference_strength':'unrated', 'assumptions':[], 'limitations':[], **submitted}
        if analysis['assertion_kind']=='fact' and analysis['inference_strength']!='unrated':
            raise ValueError('Inference strength describes interpretations, not observed facts')
        by_role={}
        for role in ('citation_refs','counter_citation_refs'):
            by_role[role]=[]
            for value in dict.fromkeys(analysis[role]):
                c=selection(case,state,value);source,span=_citation(case,state,c)
                source_ids.append(source['id'])
                from .retained_scalar import citation_context
                context=citation_context(case.event(source['id'])['data'],c['pointer'])
                ob=Observation.model_validate(dict(id='O-'+digest({'citation':c,'acquisition_context':context})[:20],
                    acquisition_context=context,version=source['version'],source_ref=ref('source',source),
                    statement='보존된 수치 필드의 정규 JSON 표현' if 'retained_scalar' in context else '원문 필드에 기록된 문자열',field_pointer=c['pointer'],literal=c['literal'],
                    canonical_sha256=hashlib.sha256(c['literal'].encode()).hexdigest(),presented_view=span['presented_view'],
                    coordinate_basis=context.get('coordinate_basis','retained tool-result scalar UTF-8 bytes; not original image offset'),
                    byte_start=span['byte_start'],byte_end=span['byte_end'],
                    interpretation_limit='이 필드·제시 구간의 기록만 확인; 성공·승인·귀속·전체 부재는 별도 판별')).model_dump()
                if ob['id'] not in {o['id'] for o in observations}:observations.append(ob)
                by_role[role].append(ref('observation',ob))
        # Preserve compound-judgment identities; granular claims also bind their
        # explicit index so even repeated statements remain distinct records.
        identity={'note_id':note['note_id'],'revision':note['revision'],'answer':note['answer'],'judgment':submitted}
        if 'claims' in judgment:identity['claim_index']=index
        ident=digest(identity)[:20]
        claim=Claim.model_validate(dict(id='F-judgment-'+ident,version=ident,
            title=analysis.get('title',note['target_proposition']),statement=analysis.get('statement',note['answer']),
            status='adopted',scope=note['scope'],source_refs=by_role['citation_refs'],counterevidence_refs=by_role['counter_citation_refs'],
            adoption_receipt='question-judgment-'+ident,semantic_review='not_reviewed',
            **({'declared_analysis_fields':[k for k in ANALYSIS_FIELDS if k in submitted]} if contract_version=='v2' else {}),
            **{k:deepcopy(analysis[k]) for k in ('assertion_kind','inference_strength','reasoning_summary','assumptions','limitations')})).model_dump()
        claims.append(claim)
    return {'observations':observations,'claims':claims},list(dict.fromkeys(source_ids))
