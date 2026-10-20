"""Optional question-owned forensic reasoning; projections never adopt or run work.

Inputs are analyst declarations. Returned acquisition scope/lineage and version
changes are program observations. Neither establishes semantic truth, absence,
independence, or a requirement to read every file.
"""
from copy import deepcopy
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1, max_length=1200, pattern=r'\S')]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class WorkbenchRef(Strict):
    kind: Literal['source','observation','claim','question','scope','time_event']
    id: Annotated[str, Field(min_length=1, max_length=160)]
    version: Annotated[str, Field(min_length=1, max_length=128)]


class AbsenceScope(Strict):
    declared_scope: Text
    source_refs: Annotated[list[WorkbenchRef], Field(min_length=1, max_length=8)]
    query_or_decoder: Text
    excluded_scope: Annotated[list[Text], Field(max_length=8)] = Field(default_factory=list)
    limitation: Annotated[str, Field(max_length=1200)] = ''
    claim_ref: WorkbenchRef | None = None


class DiscriminatingCheck(Strict):
    selected_link: Text
    competing_explanation: Text
    discriminating_observation: Text
    feasible_next_check: Text
    expected_decision_change: Text
    disposition: Literal['planned','investigated','refuted','scope_limited'] = 'planned'
    claim_ref: WorkbenchRef | None = None
    evidence_refs: Annotated[list[WorkbenchRef], Field(max_length=8)] = Field(default_factory=list)
    reason: Annotated[str, Field(max_length=1200)] = ''

    @model_validator(mode='after')
    def disposition_basis(self):
        if self.disposition!='planned' and not self.reason.strip():
            raise ValueError('A selected check disposition requires its reason')
        if self.disposition in ('investigated','refuted') and not self.evidence_refs:
            raise ValueError('Investigation/refutation selects actual retained references')
        return self


class DeclaredLineage(Strict):
    source_ref: WorkbenchRef
    parent_source_refs: Annotated[list[WorkbenchRef], Field(min_length=1, max_length=8)]
    reason: Text


class Dependency(Strict):
    reference: WorkbenchRef
    reason: Text


class ForensicWorkbench(Strict):
    absence: Annotated[list[AbsenceScope], Field(max_length=4)] = Field(default_factory=list)
    checks: Annotated[list[DiscriminatingCheck], Field(max_length=6)] = Field(default_factory=list)
    declared_lineage: Annotated[list[DeclaredLineage], Field(max_length=8)] = Field(default_factory=list)
    dependencies: Annotated[list[Dependency], Field(max_length=12)] = Field(default_factory=list)
    # Written only by the existing question writer; omitted from the tool schema.
    basis: Annotated[list[WorkbenchRef], Field(max_length=64)] = Field(default_factory=list)
    definition_basis: Annotated[str, Field(max_length=128)] = ''
    basis_omitted: Annotated[int, Field(ge=0)] = 0
    own_claim_ids: Annotated[list[Annotated[str, Field(max_length=160)]], Field(max_length=64)] = Field(default_factory=list)


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def schema():
    """Inline schema for one optional question field, without a second tool."""
    value=ForensicWorkbench.model_json_schema();definitions=value.pop('$defs',{})
    def inline(node):
        if isinstance(node,list):return [inline(v) for v in node]
        if not isinstance(node,dict):return node
        if '$ref' in node:return inline(definitions[node['$ref'].rsplit('/',1)[-1]])
        return {k:inline(v) for k,v in node.items() if k not in ('title','default')}
    value=inline(value)
    for field in ('basis','definition_basis','basis_omitted','own_claim_ids'):value['properties'].pop(field)
    return value


def _question_value(question,key,default=None):
    return question.get('investigation',{}).get(key,question.get(key,default))


def _question_id(question):
    return question.get('id') or ('Q-'+question['note_id'] if question.get('note_id') else '')


def _definition(state,question):
    from .investigation_state import definition_version
    return question.get('definition_version') or definition_version(question,state.get('scope',{}).get('approved_scope',''))


def _registry(state):
    records={(kind,r['id']):r for field,kind in (('sources','source'),('observations','observation'),
        ('claims','claim'),('questions','question')) for r in state.get(field,[])}
    if state.get('scope'):records[('scope',state['scope']['id'])]=state['scope']
    for row in state.get('timeline',[]):
        event=row.get('event_ref')
        ident=event.get('id') if isinstance(event,dict) else event
        if ident:records[('time_event',ident)]=row
    return records


def _refs(value):
    if isinstance(value,dict):
        if set(value)=={'kind','id','version'}:yield value
        else:
            for child in value.values():yield from _refs(child)
    elif isinstance(value,list):
        for child in value:yield from _refs(child)


def _time_id(row):
    value=row.get('event_ref')
    return value.get('id') if isinstance(value,dict) else value


def _time_basis_refs(row):
    context=row.get('temporal_context') or {};temporal=row.get('temporal') or {}
    bases=[*temporal.get('basis_refs',[]),*context.get('temporal_basis_refs',[])]
    if context.get('literal_timestamp_ref'):bases.append(context['literal_timestamp_ref'])
    for relation in (*temporal.get('relations',[]),*context.get('temporal_relations',[])):
        bases.extend(relation.get('basis_refs',[]))
    references=[*_refs(row.get('observation_ref',{})),*_refs(context.get('observation_refs',[]))]
    for basis in bases:
        if isinstance(basis,dict) and basis.get('evidence_id') and basis.get('source_version'):
            references.append({'kind':'source','id':basis['evidence_id'],'version':basis['source_version']})
        else:references.extend(_refs(basis))
    seen=set()
    for reference in references:
        key=(reference['kind'],reference['id'],reference['version'])
        if key not in seen:seen.add(key);yield reference


def _automatic_refs(state,question):
    """Only current selected claim ancestry and explicit time bases, never all files."""
    registry=_registry(state);seen=set();result=[]
    selected=[*(_question_value(question,'claim_refs',[]) or []),
              *({'kind':'source','id':sid} for sid in question.get('evidence_ids',[])),
              *({'kind':'time_event','id':_time_id(row)} for row in state.get('timeline',[])
                if row.get('question_id')==_question_id(question) and _time_id(row))]
    pending=list(reversed(selected))
    while pending:
        reference=pending.pop();key=(reference.get('kind'),reference.get('id'))
        if key in seen:continue
        seen.add(key);record=registry.get(key)
        if not record:continue
        result.append({'kind':key[0],'id':key[1],'version':record['version']})
        children=[]
        if key[0]=='claim':children=[*record.get('source_refs',[]),*record.get('counterevidence_refs',[])]
        elif key[0]=='observation':children=[record['source_ref']]
        elif key[0]=='time_event':children=list(_time_basis_refs(record))
        pending.extend(reversed(children))
    return result


def capture_basis(state,question,value=None):
    """Call only on an explicit question write, after new judgment records exist."""
    value=deepcopy(value if value is not None else _question_value(question,'forensic',{}) or {})
    registry=_registry(state);selected={}
    explicit={k:v for k,v in value.items() if k not in ('basis','definition_basis','basis_omitted','own_claim_ids','dependencies')}
    for reference in [*_refs(value.get('dependencies',[])),*_refs(explicit),*_automatic_refs(state,question)]:
        key=(reference['kind'],reference['id'])
        if key==('question',_question_id(question)):continue  # no self-version cycle
        current=registry.get(key)
        if current:selected[key]={'kind':key[0],'id':key[1],'version':current['version']}
    value['basis']=list(selected.values())[:64]
    value['basis_omitted']=max(0,len(selected)-64)
    own={r['id'] for r in _question_value(question,'claim_refs',[]) or []}
    value['own_claim_ids']=[r['id'] for r in value['basis'] if r['kind']=='claim' and r['id'] in own]
    value['definition_basis']=_definition(state,question)
    return ForensicWorkbench.model_validate(value).model_dump(exclude_none=True)


def validate(case,state,question,value):
    """Validate selected references/declarations, not meaning; never write a note."""
    if not isinstance(value,dict) or {'basis','definition_basis','basis_omitted','own_claim_ids'}&set(value):
        raise ValueError('forensic is an object; dependency basis fields are managed by the question writer')
    parsed=ForensicWorkbench.model_validate(value).model_dump(exclude_none=True)
    if len(_json(parsed).encode())>16000:raise ValueError('Selected forensic declarations exceed the 16000-byte bound')
    registry=_registry(state);owned_claims={r['id'] for r in _question_value(question,'claim_refs',[]) or []}
    for reference in _refs(parsed):
        target=registry.get((reference['kind'],reference['id']))
        if target is None or target.get('version')!=reference['version']:
            raise ValueError('Missing or stale forensic reference: '+reference['kind']+'/'+reference['id'])
        if reference['kind']=='question' and reference['id']==_question_id(question):
            raise ValueError('The owning question is implicit; select another dependency or a scope/source basis')
    for declaration in parsed['absence']:
        if any(r['kind']!='source' for r in declaration['source_refs']):
            raise ValueError('Absence scope selects actual source refs')
    for row in (*parsed['absence'],*parsed['checks']):
        reference=row.get('claim_ref')
        if reference and (reference['kind']!='claim' or reference['id'] not in owned_claims):
            raise ValueError('Selected claim belongs to another question or is not currently selected')
    for row in parsed['declared_lineage']:
        if any(r['kind']!='source' for r in [row['source_ref'],*row['parent_source_refs']]):
            raise ValueError('Declared lineage selects only source refs')
        if row['source_ref']['id'] in {r['id'] for r in row['parent_source_refs']}:
            raise ValueError('A declared source cannot be its own parent')
    return capture_basis(state,question,parsed)


# Small metadata fields only: no retained stdout/base64/line bodies are loaded.
_SCOPE_FIELDS=('tool','path','file_path','volume_offset','representation','search_text','search_path',
    'scope','scope_note','encoding','records_decoded','decode_lossy','complete_file','complete_member',
    'complete_content','complete','coverage_complete','search_complete','scan_exhausted','listing_complete',
    'content_not_measured','allocated_only','bytes_scanned','bytes_returned','line_start','line_end',
    'byte_start','byte_end','next_line','next_offset','stop_reason','truncated','files_completed',
    'files_skipped','files_scanned','error','exit_code','reused_from','started_id','job_id',
    'sha256','hash_scope','identity_strength','identity_limitation')
_NESTED=('/execution/state','/execution/exit_code','/member/member_index','/member/name',
         '/argument_resolution/kind','/argument_resolution/listing_ref')


def _metadata(case,sid):
    data={};omitted=[];pointers=(*('/'+key for key in _SCOPE_FIELDS),*_NESTED)
    paths=['$'+''.join('.'+json.dumps(part) for part in pointer.strip('/').split('/')) for pointer in pointers]
    # A single SQLite statement shares JSON parsing even for a huge shell result.
    expressions=['json_type(data,?),substr(json_extract(data,?),1,601),length(json_extract(data,?))' for _ in paths]
    query='SELECT kind,'+','.join(expressions)+",json_array_length(data,'$.matches'),json_array_length(data,'$.lines'),json_array_length(data,'$.entries'),json_array_length(data,'$.files') FROM events WHERE id=?"
    parameters=[path for path in paths for _ in range(3)]
    with case.connect() as db:row=db.execute(query,(*parameters,sid)).fetchone()
    if not row or row[0]!='tool_result':return {'read_status':'unavailable'}
    for index,pointer in enumerate(pointers):
        kind,value,length=row[1+index*3:4+index*3]
        if kind is None:continue
        if length is not None and length>600:omitted.append(pointer);continue
        if kind in ('object','array'):value=json.loads(value)
        elif kind in ('true','false'):value=kind=='true'
        elif kind=='integer':value=int(value)
        elif kind=='real':value=float(value)
        elif kind=='null':value=None
        data[pointer[1:]]=value
    data.update({key:count for key,count in zip(('returned_matches','returned_lines','returned_entries','returned_files'),row[-4:]) if count is not None})
    if omitted:data['omitted_fields']=omitted
    return data


def _acquired_status(data):
    if data.get('read_status')=='unavailable':return 'unknown'
    if data.get('error') or data.get('exit_code') not in (None,0) or data.get('execution/exit_code') not in (None,0) or data.get('execution/state') in ('failed','cancelled','completion_unknown'):
        return 'failed'
    if data.get('tool')=='forsic_shell':return 'derived_coverage_unknown'
    if (data.get('content_not_measured') or data.get('representation')=='directory_inventory'
            or data.get('tool')=='forsic_list'
            or data.get('tool')=='forsic_image_files' and ('returned_entries' in data or 'returned_files' in data) and not data.get('returned_lines')):
        return 'listing_only'
    flags=[data[key] for key in ('complete_file','complete_member','complete_content','coverage_complete','search_complete','complete') if key in data]
    if False in flags or data.get('truncated') or data.get('files_skipped',0) or data.get('next_line') is not None or data.get('next_offset') is not None:
        return 'partial'
    return 'complete_for_returned_measurement' if True in flags else 'unknown'


def _source_rows(state,question,forensic):
    registry=_registry(state);ids={r['id'] for r in _automatic_refs(state,question) if r['kind']=='source'}
    ids.update(r['id'] for r in _refs(forensic) if r['kind']=='source')
    return [registry[('source',sid)] for sid in sorted(ids) if ('source',sid) in registry]


def _lineage(source,data,registry,declared):
    parents=[]
    reused=data.get('reused_from')
    if ('source',reused) in registry:
        parents.append({'kind':'source','id':reused,'version':registry[('source',reused)]['version']})
    listing=data.get('argument_resolution/listing_ref')
    if data.get('argument_resolution/kind')=='archive_listing' and isinstance(listing,dict) and ('source',listing.get('id')) in registry:
        actual=registry[('source',listing['id'])]
        if actual['version']==listing.get('version'):parents.append({'kind':'source','id':actual['id'],'version':actual['version']})
    target={k:data[k] for k in ('path','file_path','volume_offset','member/member_index') if k in data}
    shell=data.get('tool')=='forsic_shell'
    digest=data.get('sha256')
    full_hash=(isinstance(digest,str) and re.fullmatch(r'[0-9a-fA-F]{64}',digest) and not data.get('error') and
               (data.get('tool')=='forsic_hash' or data.get('tool')=='forsic_image_files' and data.get('hash_scope')=='whole_file' and data.get('complete_file') is True))
    return {'source_ref':{'kind':'source','id':source['id'],'version':source['version']},
        'observed_inputs':parents,'analyst_declared_inputs':declared,'target_metadata':target,
        'acquisition_generation':source.get('source_generation_group'),
        'transformation_receipt':data.get('started_id'),
        'output_fingerprint':{'sha256':source.get('content_sha256'),'scope':'retained_tool_result'},
        'original_content_fingerprint':digest if full_hash else None,
        'identity_check_level':'content_sha256' if full_hash else 'metadata_only' if target else 'unknown',
        'input_lineage':'unknown_arbitrary_command_inputs' if shell else 'observed_parent_receipts' if parents else 'direct_acquisition' if target else 'unknown',
        'semantic_independence':'not_determined'}


def _roots(sid,lineage,seen=None):
    roots=set();unknown=False;visited=set();pending=[(sid,set(seen or ()))]
    while pending:
        ident,ancestors=pending.pop()
        if ident in ancestors:unknown=True;continue
        if ident in visited:continue
        if len(visited)>=64:unknown=True;break
        visited.add(ident);row=lineage.get(ident)
        if row is None:roots.add(ident);unknown=True;continue
        if row['input_lineage'].startswith('unknown'):unknown=True;continue
        if not row['observed_inputs']:
            roots.add(row.get('acquisition_generation') or ident);continue
        pending.extend((parent['id'],ancestors|{ident}) for parent in row['observed_inputs'])
    return roots,unknown


def _relation(left,right,lineage):
    a,unknown_a=_roots(left['source_ref']['id'],lineage);b,unknown_b=_roots(right['source_ref']['id'],lineage)
    if a&b:relation='same_recorded_origin'
    elif (left['original_content_fingerprint'] and left['original_content_fingerprint']==right['original_content_fingerprint']):
        relation='same_observed_content_sha256'
    elif left['target_metadata'] and left['target_metadata']==right['target_metadata']:relation='same_target_metadata'
    elif unknown_a or unknown_b:relation='unknown'
    else:relation='separate_recorded_acquisitions'
    return {'left':left['source_ref']['id'],'right':right['source_ref']['id'],'relation':relation,
            'semantic_independence':'not_determined'}


def _bounded(value,max_items,max_chars,prior_omissions=None):
    """Counts precede omission. A small cap omits detail, never reports zero work."""
    if int(max_chars)<256:raise ValueError('Forensic projection requires max_chars >= 256')
    result=deepcopy(value);lists=[key for key,v in result.items() if isinstance(v,list)]
    prior_omissions=prior_omissions or {}
    result['counts']={key:len(result[key])+prior_omissions.get(key,0) for key in lists}
    result['omitted_counts']={key:max(0,len(result[key])-max(0,int(max_items)))+prior_omissions.get(key,0) for key in lists}
    for key in lists:result[key]=result[key][:max(0,int(max_items))]
    result['partial']=bool(value.get('partial')) or any(result['omitted_counts'].values())
    # Remove the most expensive remaining item until the exact serialized cap fits.
    while len(_json(result))>int(max_chars):
        available=[key for key in lists if result[key]]
        if not available:
            counts=result['counts']
            compact={'counts':counts,'omitted_items':sum(counts.values()),'partial':True,'semantic_approval':False}
            if len(_json(compact))>int(max_chars):
                compact['counts']={key:counts.get(key,0) for key in ('change_impacts','dependent_questions') if key in counts}
                compact['detail_counts_omitted']=True
            return compact
        key=max(available,key=lambda name:len(_json(result[name][-1])))
        result[key].pop();result['omitted_counts'][key]+=1;result['partial']=True
    return result


def _dependency_graph(state):
    registry=_registry(state);dependents={}
    def edge(child,parent):
        dependents.setdefault((child['kind'],child['id']),set()).add(parent)
    for observation in state.get('observations',[]):edge(observation['source_ref'],('observation',observation['id']))
    for claim in state.get('claims',[]):
        for reference in (*claim.get('source_refs',[]),*claim.get('counterevidence_refs',[])):edge(reference,('claim',claim['id']))
    for row in state.get('timeline',[]):
        event=row.get('event_ref');ident=event.get('id') if isinstance(event,dict) else event
        if ident:
            for reference in _time_basis_refs(row):
                edge(reference,('time_event',ident))
    for question in state.get('questions',[]):
        key=('question',question['id'])
        for reference in [*_automatic_refs(state,question),*_refs(_question_value(question,'forensic',{}) or {})]:edge(reference,key)
    return registry,dependents


def change_impact(state,changed_refs,*,question_ids=None,max_items=12,max_chars=6000,report_snapshots=None):
    """Follow explicit ancestry only; callers choose whether a change matters.

    changed_refs describe observed changes by exact kind/id. Their version is
    returned as metadata, not used to infer meaning or silently update a baseline.
    """
    registry,dependents=_dependency_graph(state)
    selected=set(question_ids) if question_ids is not None else None
    touched=set();todo=[];changes=[]
    for raw in changed_refs:
        reference=WorkbenchRef.model_validate(raw).model_dump()
        changes.append(reference);todo.append((reference['kind'],reference['id']))
    while todo:
        key=todo.pop()
        if key in touched:continue
        touched.add(key);todo.extend(dependents.get(key,()))
    affected_questions=[];affected_claims=[]
    for kind,ident in sorted(touched):
        record=registry.get((kind,ident))
        if kind=='question' and record and (selected is None or ident in selected):
            affected_questions.append({'question_ref':{'kind':'question','id':ident,'version':record['version']},
                'previously_declared_reopen_conditions':deepcopy(record.get('reopen_conditions',[])),
                'recheck_reason':'Selected source, time, scope, or explicit dependency changed.',
                'automatic_reopening':False,'automatic_conclusion_change':False})
        elif kind=='claim' and record:affected_claims.append({'kind':'claim','id':ident,'version':record['version']})
    question_set={r['question_ref']['id'] for r in affected_questions}
    if selected is not None:
        owned={ref['id'] for q in state.get('questions',[]) if q['id'] in question_set for ref in q.get('claim_refs',[])}
        affected_claims=[r for r in affected_claims if r['id'] in owned]
    snapshots=report_snapshots
    if snapshots is None:
        meta=state.get('meta',{})
        snapshots=([{'report_id':meta.get('report_id'),'snapshot_id':meta['snapshot_id'],
                     'question_ids':[q['id'] for q in state.get('questions',[])]}] if meta.get('snapshot_id') else [])
    reports=[{'report_id':r.get('report_id'),'snapshot_id':r['snapshot_id'],
              'affected_question_ids':sorted(question_set&set(r.get('question_ids',[])))}
             for r in snapshots if question_set&set(r.get('question_ids',[]))]
    return _bounded({'changed_refs':changes,'dependent_claim_refs':affected_claims,
        'dependent_questions':affected_questions,'report_snapshot_refs':reports,
        'semantic_approval':False,'automatic_reopening':False},max_items,max_chars)


def project(case,state,question,*,max_items=12,max_chars=10000):
    """Read-only bounded workbench for one current question; no host.current call."""
    forensic=ForensicWorkbench.model_validate(_question_value(question,'forensic',{}) or {}).model_dump(exclude_none=True)
    registry=_registry(state);sources=_source_rows(state,question,forensic)
    # Bound source metadata reads before traversal; omitted scope is explicit.
    selected=sources[:32];metadata={r['id']:_metadata(case,r['id']) for r in selected}
    scope_rows=[]
    for number,declaration in enumerate(forensic['absence']):
        acquired=[]
        for reference in declaration['source_refs']:
            data=metadata.get(reference['id'])
            acquired.append({'source_ref':deepcopy(reference),
                'current_source_version':registry.get(('source',reference['id']),{}).get('version'),
                'acquired_scope':deepcopy(data) if data is not None else {'read_status':'omitted'},
                'measurement_status':_acquired_status(data or {}),
                'semantic_absence_validated':False})
        scope_rows.append({'index':number,'question_id':_question_id(question),
            **deepcopy(declaration),'role':'analyst_absence_declaration','acquired':acquired,
            'semantic_scope_comparison':'analyst_required','all_files_gate':False})
    declared={}
    for row in forensic['declared_lineage']:
        declared.setdefault(row['source_ref']['id'],[]).extend(deepcopy(row['parent_source_refs']))
    lineage={r['id']:_lineage(r,metadata[r['id']],registry,declared.get(r['id'],[])) for r in selected}
    pairs=[];rows=list(lineage.values())
    for index,left in enumerate(rows):
        for right in rows[index+1:]:pairs.append(_relation(left,right,lineage))
    changes=[]
    selected_claims={r['id'] for r in _question_value(question,'claim_refs',[]) or []}
    for reference in forensic['basis']:
        actual=registry.get((reference['kind'],reference['id']))
        replaced=(reference['kind']=='claim' and (reference['id'] in forensic['own_claim_ids'] and reference['id'] not in selected_claims
                  or actual is not None and actual.get('status') in ('superseded','retracted')))
        if actual is None or actual.get('version')!=reference['version'] or replaced:
            impact=change_impact(state,[reference],question_ids=[_question_id(question)],max_items=4,max_chars=2500)
            changes.append({'selected_ref':deepcopy(reference),'current_version':actual.get('version') if actual else None,
                'recheck_reason':'Selected claim replaced or withdrawn' if replaced else 'Referenced record missing' if actual is None else 'Selected dependency version changed',
                'dependent_claim_refs':impact.get('dependent_claim_refs',[]),
                'report_snapshot_refs':impact.get('report_snapshot_refs',[]),
                'question_id':_question_id(question),'previously_declared_reopen_conditions':deepcopy(question.get('reopen_conditions',[])),
                'automatic_reopening':False,'automatic_conclusion_change':False})
    definition=_definition(state,question)
    if forensic['definition_basis'] and forensic['definition_basis']!=definition:
        meta=state.get('meta',{})
        reports=([{'report_id':meta.get('report_id'),'snapshot_id':meta['snapshot_id'],
                   'affected_question_ids':[_question_id(question)]}] if meta.get('snapshot_id') else [])
        changes.append({'question_id':_question_id(question),'selected_definition':forensic['definition_basis'],
            'current_definition':definition,'recheck_reason':'Owning question proposition or scope changed',
            'report_snapshot_refs':reports,
            'previously_declared_reopen_conditions':deepcopy(question.get('reopen_conditions',[])),
            'automatic_reopening':False,'automatic_conclusion_change':False})
    result={'question_id':_question_id(question),'role':'forensic_reasoning_projection_not_source_evidence',
        'absence_scopes':scope_rows,'selected_checks':deepcopy(forensic['checks']),
        'source_lineage':rows,'source_relations':pairs,'change_impacts':changes,
        'selected_source_count':len(sources),'omitted_source_metadata':max(0,len(sources)-32),
        'tracked_dependency_count':len(forensic['basis']),'omitted_dependency_count':forensic['basis_omitted'],
        'partial':bool(forensic['basis_omitted'] or len(sources)>32),
        'semantic_approval':False}
    return _bounded(result,max_items,max_chars)



def propagate_changes(state,*,max_items=12,max_chars=7000,max_changed_refs=64):
    """Distribute observed frozen-basis changes through selected dependencies once.

    Returns new context dictionaries only. No version, baseline, answer, work
    state, ledger, or input state is changed; omitted seeds remain explicit.
    """
    registry,graph=_dependency_graph(state);questions={q['id']:q for q in state.get('questions',[])}
    seeds={};direct={}
    for qid,question in questions.items():
        forensic=_question_value(question,'forensic',{}) or {};selected_claims={r['id'] for r in _question_value(question,'claim_refs',[]) or []}
        for reference in forensic.get('basis',[]):
            key=(reference['kind'],reference['id']);actual=registry.get(key)
            replaced=reference['kind']=='claim' and (reference['id'] in forensic.get('own_claim_ids',[]) and reference['id'] not in selected_claims or actual is not None and actual.get('status') in ('retracted','superseded'))
            if actual is None or actual.get('version')!=reference['version'] or replaced:
                seeds.setdefault(key,deepcopy(reference));direct.setdefault(qid,set()).add(key)
        if forensic.get('definition_basis') and forensic['definition_basis']!=_definition(state,question):
            key=('question',qid);seeds[key]={'kind':'question','id':qid,'version':question['version']}
            direct.setdefault(qid,set()).add(key)
    chosen=list(sorted(seeds))[:max(0,min(64,int(max_changed_refs)))]
    omitted=len(seeds)-len(chosen);inherited={}
    for key in chosen:
        pending=[key];seen=set();via={}
        while pending:
            node=pending.pop()
            if node in seen:continue
            seen.add(node)
            for dependent in sorted(graph.get(node,())):
                via.setdefault(dependent,node);pending.append(dependent)
        for kind,qid in sorted(seen):
            if kind!='question' or qid not in questions or key in direct.get(qid,set()):continue
            question=questions[qid];parent=via.get(('question',qid),key)
            selected=next((deepcopy(r) for r in _refs(_question_value(question,'forensic',{}) or {})
                           if (r['kind'],r['id'])==parent),None)
            if selected is None and parent in registry:
                selected={'kind':parent[0],'id':parent[1],'version':registry[parent]['version']}
            actual=registry.get(key)
            inherited.setdefault(qid,[]).append({'question_id':qid,'changed_ref':deepcopy(seeds[key]),
                'current_version':actual.get('version') if actual else None,'via_selected_ref':selected,
                'recheck_reason':"A selected dependency's retained basis changed; reassess its use in this answer.",
                'previously_declared_reopen_conditions':deepcopy(question.get('reopen_conditions',[])),
                'automatic_reopening':False,'automatic_conclusion_change':False,'inherited_dependency_change':True})
    contexts={}
    for qid,question in questions.items():
        old=question.get('forensic_context') or {};direct_count=len(direct.get(qid,set()))
        old_rows=old.get('change_impacts',[])
        had_generated=(old.get('counts',{}).get('change_impacts',0)>direct_count
            or any(row.get('inherited_dependency_change') for row in old_rows)
            or bool(old.get('change_propagation',{}).get('inherited_change_count')))
        if qid not in inherited and not had_generated and not (omitted and question.get('forensic')):continue
        base=deepcopy(old);counts=base.pop('counts',{});base.pop('omitted_counts',None)
        base.setdefault('question_id',qid);base.setdefault('semantic_approval',False)
        # Regenerate inherited rows. Frozen basis, rather than a previous clipped
        # view, supplies the exact count of direct changes that remain omitted.
        base['change_impacts']=[row for row in old_rows if not row.get('inherited_dependency_change')]
        counts['change_impacts']=direct_count
        missing={}
        for field,count in counts.items():
            base.setdefault(field,[]);missing[field]=max(0,count-len(base[field]))
        base['change_impacts'].extend(inherited.get(qid,[]))
        base['change_propagation']={'changed_reference_count':len(seeds),'omitted_changed_references':omitted,
            'inherited_change_count':len(inherited.get(qid,[]))}
        base['partial']=bool(omitted or base.get('omitted_dependency_count') or base.get('omitted_source_metadata')
                             or base.get('detail_counts_omitted') or any(missing.values()))
        contexts[qid]=_bounded(base,max_items,max_chars,missing)
    return {'contexts':contexts,'changed_reference_count':len(seeds),'omitted_changed_references':omitted,
            'automatic_reopening':False,'semantic_approval':False}
