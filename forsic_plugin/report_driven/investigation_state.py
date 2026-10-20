"""Case-local question mutations and native-tool mission linkage; no scheduler/model."""
from copy import deepcopy
import hashlib
import json
import re
import time
import uuid

PRIORITIES = {'decision_critical', 'material', 'contextual', 'unassessed'}
WORK_STATES = {'open', 'active', 'held', 'scoped_closed', 'blocked_internal', 'blocked_external', 'budget_deferred'}
QUESTION_FIELDS = ('work_state', 'assessment', 'priority', 'closure_rationale', 'deferred_reason',
                   'reopen_conditions', 'claim_refs', 'remaining_gap_ids', 'judgment_review_required', 'review_responses')
NOTE_FIELDS = ('question', 'target_proposition', 'scope', 'answer', 'evidence_ids', 'status',
               'alternatives', 'gaps', 'critical_gaps', 'next_checks', 'timeline', 'correction_reason', 'forensic', 'report_slots', 'reader_explanation')


def hash_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def definition_version(note, default_scope=''):
    return hash_value({'target_proposition': note.get('target_proposition') or note['question'],
                       'scope': note.get('scope', default_scope)})


class MutationConflictError(ValueError):
    def __init__(self):
        self.details = {'error_code': 'mutation_payload_conflict', 'write_applied': False,
            'recovery': {'read_call': {'tool': 'forsic_reporting', 'arguments': {'action': 'state'}},
                'meaning': 'This operation_id already committed different content. Read current state before making a deliberate update with a fresh operation_id and current revision. Identical retries reuse the original operation_id; changing the ID alone does not repair a rejected payload.'}}
        super().__init__('operation_id already belongs to a different mutation payload')


def mutation_replay(db, operation_id, request):
    if not operation_id:
        return None
    if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 128:
        raise ValueError('operation_id must be a nonempty string of at most 128 characters')
    row = db.execute("SELECT data FROM events WHERE kind='mutation_receipt' AND json_extract(data,'$.operation_id')=? ORDER BY rowid DESC LIMIT 1", (operation_id,)).fetchone()
    if row:
        stored = json.loads(row['data'])
        if stored['request_hash'] != hash_value(request):
            raise MutationConflictError()
        return deepcopy(stored['response'])
    return None


def record_mutation(db, operation_id, request, response):
    if operation_id:
        data = dict(operation_id=operation_id, request_hash=hash_value(request), response=response)
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (uuid.uuid4().hex, time.time(), 'mutation_receipt', '', json.dumps(data, ensure_ascii=False)))


def latest_note(db, note_id):
    row = db.execute("SELECT data FROM events WHERE kind='note' AND json_extract(data,'$.note_id')=? ORDER BY rowid DESC LIMIT 1", (note_id,)).fetchone()
    return json.loads(row['data']) if row else None


def note_gap_records(note, question, cited):
    """Project actual declared gaps; adopted direct judgments need no synthetic test gap."""
    from .host import ref
    reasons = list(dict.fromkeys(note.get('critical_gaps', []) + note.get('gaps', [])))
    if not reasons and not note.get('declared_gaps_only'):
        reasons = ['현재 답과 원문을 판별 조건에 따라 평가해야 함']
    return [dict(id=f"G-{question['id']}-{hash_value(reason)[:16]}", version=str(note['revision']),
        question_ref=ref('question', question), section_targets=['E01','E02','A05','A11'],
        original_obligation=reason, kind='unassessed_result' if cited else 'unpresented_source',
        disposition='open', impact='decision_critical' if reason in note.get('critical_gaps', []) else 'unassessed',
        reason=reason, basis_refs=cited, feasible_next_action='evaluate_result' if cited else 'read_source',
        missing_preconditions=[], reopen_conditions=['새 원문·반론·질문 범위 정정'], resolution_refs=[])
        for reason in reasons]


def disposed_gaps(gaps, dispositions, question):
    """Resolve the analyst's exact gap ID or text, without choosing a gap for them."""
    from .host import ref
    changed = []; selected = set()
    for index, d in enumerate(dispositions):
        path = f'payload.gap_dispositions[{index}]'
        selectors = [k for k in ('gap_id', 'gap_text') if k in d]
        if len(selectors) != 1:
            raise ValueError(path+': select exactly one gap_id or exact gap_text')
        key = selectors[0]; field = 'id' if key == 'gap_id' else 'original_obligation'
        found = [g for g in gaps if g[field] == d[key]]
        if len(found) != 1:
            prefix = 'Unknown gaps ID: '+d[key]+'. ' if key == 'gap_id' else ''
            raise ValueError(path+': '+prefix+'Gap selection does not identify one current question gap. New questions use gap_text copied exactly from gaps/critical_gaps; existing questions can use returned gap_id. No mutation was saved.')
        g = found[0]
        if g['id'] in selected:
            raise ValueError(path+': duplicate gap disposition for '+g['id'])
        selected.add(g['id'])
        basis = list(g['basis_refs']) or [ref('question', question)]
        changed.append({**g, 'disposition': d['disposition'], 'reason': d['reason'],
            'feasible_next_action': 'none', 'reopen_conditions': d['reopen_conditions'],
            'resolution_refs': basis if d['disposition'] == 'resolved' else []})
    return changed


def save_note_locked(case, db, args, *, question_update=None):
    """The only writer for a question's definition, answer and work disposition.

    Caller owns BEGIN IMMEDIATE. Legacy notes retain revision conflict behavior;
    report assessment and its answer can share this transaction.
    """
    note_id = args.get('note_id') or uuid.uuid4().hex
    old = latest_note(db, note_id)
    if args.get('note_id') and not old:
        raise ValueError('Unknown note_id in this case')
    if old and args.get('revision') != old['revision']:
        raise ValueError(f"Note revision conflict: expected current revision={old['revision']}, received revision={args.get('revision')!r}. No note was saved. Call forsic_note(action='get', note_id={note_id!r}), review its current note and preserve other changes, then save with that note.revision; do not increment revision yourself. The tool creates the next revision.")
    if old and old.get('session_id') and old['session_id'] != args.get('_session_id',''):
        raise ValueError('Question belongs to another native conversation')
    if old and old.get('goal_id') and old['goal_id'] != args.get('_goal_id',''):
        raise ValueError('Question belongs to another native goal; create a question for the current goal')
    value = deepcopy(old) if old else {'note_id': note_id, 'status': 'open', 'timeline': []}
    contract_version = args.get('_question_contract', 'v1')
    if old and old.get('question_contract_version') == 'v2' and contract_version != 'v2':
        raise ValueError('v2 question writes are disabled on this adapter; historical records remain readable')
    if not old and contract_version == 'v2':
        value.update(question_contract_version='v2', declared_gaps_only=True)
    if 'report_slots' in args and contract_version != 'v2':
        raise ValueError('report_slots declarations require the v2 question contract')
    value.update({k: deepcopy(args[k]) for k in NOTE_FIELDS if k in args})
    if 'timeline' in args:
        from ..notes import sources, timeline
        sources(case, [eid for row in args['timeline'] for eid in row.get('evidence_ids', [])])
        value['timeline'] = timeline(args['timeline'], case)
    if args.get('_session_id') and not value.get('session_id'):
        value['session_id'] = args['_session_id']
    if args.get('_goal_id') and not value.get('goal_id'):
        value['goal_id'] = args['_goal_id']
    if not value.get('question') or not value.get('answer'):
        raise ValueError('A note needs question and answer')
    default_scope = str(case.config.get('scope') or '증거 선택 범위 외의 조사 범위는 아직 합의되지 않음')
    value.setdefault('scope', default_scope)
    # Editing a legacy question also edits its proposition unless a separate
    # proposition was explicitly supplied by the analyst.
    if 'question' in args and 'target_proposition' not in args and (not old or old.get('target_proposition', old['question']) == old['question']):
        value['target_proposition'] = value['question']
    value.setdefault('target_proposition', value['question'])
    value['definition_version'] = definition_version(value, default_scope)
    if old:
        from .review_responses import bind_existing
        bind_existing(case, value, old)
    if old and definition_version(old, default_scope) != value['definition_version']:
        prior_responses = value.get('investigation', {}).get('review_responses', [])
        value['investigation'] = {'review_responses': prior_responses, 'work_state': 'open', 'assessment': 'undetermined', 'claim_refs': [],
                                  'remaining_gap_ids': [], 'closure_rationale': None, 'deferred_reason': None,
                                  'reopen_conditions': [], 'priority': old.get('investigation', {}).get('priority', 'unassessed')}
    changed = old and any(old.get(k) != value.get(k) for k in
                          ('answer', 'evidence_ids', 'alternatives'))
    claim_action = args.get('_claim_action')
    if claim_action == 'retract' and 'answer' not in args:
        # The previous answer remains in the prior note event, not as today's answer.
        value['answer'] = '이전 답변이 철회되었습니다. 현재 판단은 재검토가 필요합니다.'
    if claim_action == 'retain' and not args.get('correction_reason'):
        raise ValueError('Retaining a judgment across an edit requires a reason explaining unchanged meaning')
    if old and (changed or claim_action in ('replace', 'retract')) and claim_action != 'retain':
        # Read the committed prior judgment; the caller may already have inserted
        # the replacement claim in this uncommitted write transaction.
        from .host import current
        previous = current(case)
        selected = {x['id'] for x in old.get('investigation', {}).get('claim_refs', [])}
        retired = [{**c, 'status': 'retracted' if claim_action == 'retract' else 'superseded'}
                   for c in previous['claims'] if c['id'] in selected]
        if retired:
            change = {'question_ref': {'kind':'question','id':'Q-'+note_id,'version':str(old['revision'])},
                      'definition_versions': {'Q-'+note_id: definition_version(old,default_scope)},
                      'basis_definitions': [], 'records': {'claims':retired},
                      'correction_reason':args.get('correction_reason','Current answer changed')}
            db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                       (uuid.uuid4().hex,time.time(),'report_change','',json.dumps(change,ensure_ascii=False)))
        value.setdefault('investigation', {}).update(claim_refs=[], assessment='undetermined',
                                                     judgment_review_required=True)
    if question_update:
        value.setdefault('investigation', {}).update(deepcopy(question_update))
    # A finding response records the analyst's investigation of that concern,
    # separately from the current verdict. Keep it across answer/grounding edits;
    # projection checks its question definition and exact cited source versions.
    if '_review_responses' in args:
        from .review_responses import validate_and_merge
        from .host import current
        value.setdefault('investigation', {})['review_responses'] = validate_and_merge(
            case, current(case), value, args['_review_responses'],
            lineage=args.get('_session_lineage') or [args.get('_session_id', '')])
    if not args.get('_judgment') and 'report_slots' not in args and old and all(old.get(k) == value.get(k) for k in value if k not in ('correction_reason', 'updated_at', 'revision')):
        return {'note': old, 'reused': True}
    value['revision'] = old['revision'] + 1 if old else 1
    value['updated_at'] = time.time()
    analytical = None
    if '_judgment' in args:
        from .grounded_judgment import records
        from .host import current, ref
        # No inferred mission/test: the same note transaction stores exact
        # observations and the analyst's explicitly authored claim selections.
        analytical, selected_sources = records(case,current(case),value,args['_judgment'],contract_version)
        claim=analytical['claims'][0]
        value['evidence_ids']=selected_sources
        value.setdefault('investigation',{}).update(claim_refs=[ref('claim',c) for c in analytical['claims']],judgment_review_required=False)
        if not old:
            # Preserve historical generated gaps/mission dependencies. New direct
            # judgments project only the analyst's declared gaps.
            value['declared_gaps_only'] = True
        change={'question_ref':{'kind':'question','id':'Q-'+note_id,'version':str(value['revision'])},
                'definition_versions':{'Q-'+note_id:value['definition_version']},'basis_definitions':[],
                'records':analytical,'validation':{'receipt':claim['adoption_receipt'],
                'checks':['explicit retained selection','source version','exact UTF-8 span'],
                'semantic_approval':False}}
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
            (uuid.uuid4().hex,time.time(),'report_change','',json.dumps(change,ensure_ascii=False)))
    if 'forensic' in args or 'report_slots' in args or 'reader_explanation' in args:
        from .host import current, content_version
        # New judgment records belong to this same uncommitted transaction.
        # Project just those records in memory; never commit a second note to
        # attach the workbench, and never overwrite its previously saved basis
        # on an unrelated answer edit.
        provisional = current(case)
        if analytical:
            for field, records in analytical.items():
                changed_ids = {record['id'] for record in records}
                provisional[field] = [r for r in provisional[field] if r['id'] not in changed_ids]
                provisional[field].extend({**deepcopy(r), 'version':content_version(r)} for r in records)
        if 'timeline' in args:
            from .time_projection import project as project_time
            qid = 'Q-'+value['note_id']
            provisional['timeline'] = [r for r in provisional['timeline'] if
                r.get('question_id') != qid and not r['id'].startswith('TM-'+qid+'-')]
            source_map = {r['id']:r for r in provisional['sources']}
            for index, row in enumerate(value['timeline']):
                if not row.get('temporal'):
                    continue
                assertion, observations = project_time(case, {'id':qid,'revision':value['revision']}, row, index, source_map)
                provisional['timeline'].append({**assertion,'version':content_version(assertion)})
                known = {r['id'] for r in provisional['observations']}
                provisional['observations'].extend({**r,'version':content_version(r)} for r in observations if r['id'] not in known)
        if 'reader_explanation' in args:
            from .reader_explanation import bind as bind_reader_explanation
            value['reader_explanation'] = bind_reader_explanation(provisional, value, args['reader_explanation'])
        if 'report_slots' in args:
            from .report_slots import bind as bind_report_slots
            value['report_slots'] = bind_report_slots(provisional, value, args['report_slots'])
        if 'forensic' in args:
            from .forensic_workbench import validate as validate_forensic
            value['forensic'] = validate_forensic(case, provisional, value, args['forensic'])
    db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (uuid.uuid4().hex, value['updated_at'], 'note', '', json.dumps(value, ensure_ascii=False)))
    if '_new_gap_dispositions' in args:
        from .host import ref, current, find
        q = {'id': 'Q-'+note_id, 'version': str(value['revision'])}
        state = current(case)
        cited = [ref('source', find(state, 'sources', eid)) for eid in value.get('evidence_ids', [])]
        gaps = note_gap_records(value, q, cited)
        changes = disposed_gaps(gaps, args['_new_gap_dispositions'], q)
        if value.get('investigation', {}).get('work_state') == 'scoped_closed' and {g['id'] for g in gaps} - {g['id'] for g in changes}:
            raise ValueError('Account for every declared remaining gap before scope closure using exact gap_text and its disposition')
        if changes:
            change = {'question_ref': ref('question', q), 'definition_versions': {q['id']: value['definition_version']},
                'basis_definitions': [], 'records': {'gaps': changes}}
            db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (uuid.uuid4().hex, time.time(), 'report_change', '', json.dumps(change, ensure_ascii=False)))
    return {'note': value}


def question_receipt(note):
    return {'question_id': 'Q-' + note['note_id'], 'revision': note['revision'],
            'contract_version': note.get('question_contract_version', 'v1'),
            'definition_version': note['definition_version'],
            'work_state': note.get('investigation', {}).get('work_state', 'open'),
            'read_call': {'tool': 'forsic_reporting', 'arguments': {'action': 'state', 'question_id': 'Q-' + note['note_id']}},
            'gap_lookup': {'tool': 'forsic_reporting', 'arguments': {'action': 'gaps', 'question_id': 'Q-' + note['note_id']}},
            'semantic_approval': False}


def compact_input_schemas(contract_version='v2'):
    from .contracts import Assessment
    from ..notes import timeline_schema
    string = {'type': 'string'}
    strings = {'type': 'array', 'items': string}
    nonblank = {**string, 'minLength': 1, 'pattern': r'\S'}
    common = {'operation_id': string, 'expected_revision': {'type': 'integer', 'minimum': 1}, 'question_id': string}
    question = {**common, **{k: string for k in ('question', 'target_proposition', 'scope', 'answer', 'reason')},
                **{k: strings for k in ('evidence_ids', 'alternatives', 'gaps', 'critical_gaps', 'next_checks', 'reopen_conditions')},
                'timeline': timeline_schema(require_source=True),
                'work_state': {'type': 'string', 'enum': sorted(WORK_STATES)},
                'priority': {'type': 'string', 'enum': sorted(PRIORITIES)},
                'assessment': {'type': 'string', 'enum': ['supported', 'refuted', 'conflicting', 'undetermined']},
                'gap_dispositions': {'type': 'array', 'items': {'type': 'object', 'properties': {
                    'gap_id': {**string, 'minLength': 1, 'description': 'Existing exact gap ID from this question. Choose gap_id OR gap_text.'},
                    'gap_text': {**string, 'minLength': 1, 'description': 'Exact gaps/critical_gaps text, including new update gaps. Unsaved gaps require gap_text, never provisional IDs.'},
                    'disposition': {'type': 'string', 'enum': ['resolved', 'not_applicable', 'assessed_unresolved']},
                    'reason': nonblank, 'reopen_conditions': {'type': 'array', 'items': nonblank, 'minItems': 1,
                        'description': 'At least one explicit reopening condition, including for resolved gaps.'}}, 'required': ['disposition', 'reason', 'reopen_conditions'],
                    'oneOf': [{'required': ['gap_id']}, {'required': ['gap_text']}], 'additionalProperties': False}}}
    from .grounded_judgment import schema as judgment_schema
    question['judgment'] = judgment_schema(contract_version)
    from .review_responses import schema as review_response_schema
    question['review_responses'] = review_response_schema()
    from .forensic_workbench import schema as forensic_schema
    question['forensic'] = forensic_schema()
    from .reader_explanation import schema as reader_explanation_schema
    question['reader_explanation'] = reader_explanation_schema()
    if contract_version == 'v2':
        from .report_slots import schema as report_slots_schema
        question['report_slots'] = report_slots_schema()
        question['closure'] = {'type':'object', 'properties':{
            'basis_snapshot':nonblank, 'reason':nonblank,
            'retain_unresolved':{'type':'boolean','enum':[True]},
            'reopen_conditions':{'type':'array','items':nonblank,'minItems':1}},
            'required':['basis_snapshot','reason','retain_unresolved','reopen_conditions'],
            'additionalProperties':False,
            'description':"Existing question, unchanged definition/gaps. Use closure OR top-level work_state/reopen_conditions; reason belongs here. Keep this snapshot's unresolved limits, reassess next_checks and dispose each material/planned work item. Limits remain unresolved."}
    question['claim_action'] = {'type':'string','enum':['retain','replace','retract']}
    mission = {**common, **{k: string for k in ('gap_id', 'why_it_matters', 'tool_name', 'support_rule', 'refute_rule', 'inconclusive_rule', 'target_proposition')},
               'tool_arguments': {'type': 'object'},
               'required_view': {'type': 'string', 'enum': ['metadata', 'exact_excerpt', 'full_field']},
               **{k: strings for k in ('result_ids', 'input_ids', 'counterevidence_ids', 'limits', 'reopen_conditions')}}
    assess = {'operation_id': string, 'expected_revision': {'type': 'integer', 'minimum': 1},
              **{k: string for k in ('mission_id', 'mission_version', 'result_id', 'result_version', 'outcome', 'reasoning_summary', 'answer', 'next_check')},
              'limitations': strings, 'citations': {'type': 'array', 'items': {'type': 'object', 'properties': {
                  'source_id': string, 'source_version': string, 'pointer': string, 'literal': string, 'byte_start': {'type': 'integer', 'minimum': 0}},
                  'required': ['source_id', 'source_version', 'pointer', 'literal', 'byte_start'], 'additionalProperties': False}}}
    assess['outcome'] = {**Assessment.model_json_schema()['properties']['outcome'],
        'description': 'Relationship of this local test result to its mission proposition. Use supports/refutes for support/refutation; question_update.assessment has separate values.'}
    assess['question_update'] = {'type':'object','properties': {
        **{k:string for k in ('answer','reason','reasoning_summary')},
        'assessment':question['assessment'], 'assertion_kind':{'type':'string','enum':['fact','interpretation']},
        'inference_strength':{'type':'string','enum':['favored','plausible','unrated']},
        **{k:strings for k in ('assumptions','alternatives','limitations','next_checks','reopen_conditions')}
        }, 'required':['answer','assessment','reason'], 'additionalProperties':False}
    def schema(properties, required):
        return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}
    retained = deepcopy({k: v for k, v in mission.items() if k not in ('tool_name', 'tool_arguments')})
    retained['result_ids'] = {'type': 'array', 'minItems': 1, 'uniqueItems': True,
        'items': {'type': 'string', 'minLength': 1},
        'description': 'Explicit successful or failed retained result evidence_ids selected for this after-result assessment. No new evidence call is planned or executed.'}
    for key in ('why_it_matters', 'inconclusive_rule', 'support_rule', 'refute_rule'):
        retained[key] = {**retained[key], 'minLength': 1}
    return {'question': schema(question, ['operation_id']),
            'mission': schema(mission, ['operation_id', 'question_id', 'expected_revision', 'why_it_matters', 'inconclusive_rule', 'limits', 'reopen_conditions']),
            'mission_from_results': schema(retained, ['operation_id', 'question_id', 'expected_revision', 'gap_id', 'result_ids', 'why_it_matters', 'inconclusive_rule', 'limits', 'reopen_conditions']),
            'assess': schema(assess, ['operation_id', 'expected_revision', 'mission_id', 'mission_version', 'result_id', 'result_version', 'outcome', 'reasoning_summary', 'limitations', 'next_check', 'citations'])}



def input_problems(payload, schema, path='payload'):
    problems=[]
    def check(value, spec, path):
        kind=spec.get('type')
        types={'object':isinstance(value,dict),'array':isinstance(value,list),
               'string':isinstance(value,str),'integer':type(value) is int,
               'number':type(value) in (int,float),'boolean':type(value) is bool,
               'null':value is None}
        valid=any(types.get(k,False) for k in kind) if isinstance(kind,list) else types.get(kind,True)
        if not valid:
            problems.append({'path':path,'expected_type':kind});return
        for union in ('anyOf', 'oneOf'):
            if union in spec:
                matches=sum(not input_problems(value, branch, path) for branch in spec[union])
                if (union=='oneOf' and matches!=1) or (union=='anyOf' and matches==0):
                    problems.append({'path':path, union+'_required':[b.get('required',[]) for b in spec[union]],
                                     'matched_branches':matches})
        if isinstance(value,str) and len(value)<spec.get('minLength',0):
            problems.append({'path':path,'minLength':spec['minLength']})
        if isinstance(value,str) and len(value)>spec.get('maxLength',len(value)):
            problems.append({'path':path,'maxLength':spec['maxLength']})
        if isinstance(value,str) and 'pattern' in spec and not re.search(spec['pattern'],value):
            problems.append({'path':path,'pattern':spec['pattern']})
        if 'enum' in spec and value not in spec['enum']:
            problems.append({'path':path,'allowed_values':spec['enum']})
        if type(value) in (int,float):
            for bound in ('minimum','maximum','exclusiveMinimum'):
                if bound in spec and ((bound=='minimum' and value<spec[bound]) or
                    (bound=='maximum' and value>spec[bound]) or (bound=='exclusiveMinimum' and value<=spec[bound])):
                    problems.append({'path':path,bound:spec[bound]})
        if isinstance(value,dict):
            unknown=sorted(set(value)-set(spec.get('properties',{}))) if spec.get('additionalProperties') is False else []
            missing=sorted(set(spec.get('required',[]))-set(value))
            if unknown or missing:problems.append({'path':path,'unknown_fields':unknown,'missing_fields':missing})
            for k,v in value.items():
                if k in spec.get('properties',{}):check(v,spec['properties'][k],path+'.'+k)
        if isinstance(value,list):
            if len(value)<spec.get('minItems',0):
                problems.append({'path':path,'minItems':spec['minItems'],'actual_items':len(value)})
            if len(value)>spec.get('maxItems',len(value)):
                problems.append({'path':path,'maxItems':spec['maxItems'],'actual_items':len(value)})
            if spec.get('uniqueItems') and len({hash_value(v) for v in value})!=len(value):
                problems.append({'path':path,'uniqueItems':True})
            for i,v in enumerate(value):check(v,spec.get('items',{}),path+f'[{i}]')
    check(payload,schema,path)
    return problems


def native_plan_contract(name, arguments, schemas):
    schema=deepcopy(schemas[name])
    if name=='forsic_image_files':
        from ..image_reader import image_action_contract
        action=arguments.get('action') if isinstance(arguments,dict) else None
        schema['required']=image_action_contract(action if isinstance(action,str) else None)['required']
    return schema


def validate_native_plan(payload, schemas):
    """Check syntax against the registered evidence tool before saving a plan."""
    if not isinstance(payload,dict) or payload.get('result_ids') or payload.get('reuse_result_refs'):
        return
    name=payload.get('tool_name') or payload.get('proposed_tool_name')
    if not name: return  # The existing mission contract explains missing identity.
    if schemas is not None and (not isinstance(name,str) or name not in schemas):
        raise ValueError('Unsupported planned evidence tool: '+str(name))
    if name in ('forsic_image_files','forsic_image_read'):
        from ..image_reader import validate_image_action
        validate_image_action(payload.get('tool_arguments'),'tool_arguments')
    if name=='forsic_image_search':
        from ..image_reader import validate_image_action, image_search_entry_schema
        arguments=payload.get('tool_arguments')
        problems=input_problems(arguments,image_search_entry_schema(),'tool_arguments')
        if problems:
            raise ValueError('Invalid planned image search arguments: '+json.dumps(problems,ensure_ascii=False))
        validate_image_action({**arguments,'action':'search'},'tool_arguments')
    if schemas is None:return  # Legacy callers have no registry; image admission still applies.
    problems=input_problems(payload.get('tool_arguments'),native_plan_contract(name,payload.get('tool_arguments'),schemas),'tool_arguments')
    if problems:
        raise ValueError('Invalid planned '+name+' arguments: '+json.dumps(problems,ensure_ascii=False)+
            '. No mission was saved. Copy the required fields from that native tool; changing only operation_id does not repair the input.')


class QuestionInputError(ValueError):
    """A rejected question draft, with repair data and no inferred correction."""
    def __init__(self, problems, recovery, *, include_recovery=False):
        self.details = {'error_code':'invalid_question_fields', 'invalid_fields':problems,
                        'recovery':recovery}
        def message():
            shown=self.details['invalid_fields'];repair=self.details['recovery']
            simple = (len(shown)==1 and shown[0].get('path')=='payload' and
                      set(shown[0])=={'path','missing_fields','error'})
            displayed=[{k:v for k,v in problem.items() if k not in ('missing_gap_texts','gap_text')}
                       for problem in shown]
            diagnostic = shown[0]['error'] if simple else 'Invalid question fields: '+json.dumps(displayed,ensure_ascii=False)
            return (diagnostic+'. No question was changed. Correct these fields in forsic_question; no action/payload wrapper.'+
                    (' Recovery: '+json.dumps(repair,ensure_ascii=False) if include_recovery and repair else ''))
        def size(value):return len(json.dumps(value,ensure_ascii=False).encode('utf-8'))
        # Reserve room for the native result's IDs, input hint and allowed fields.
        # Only the rejected response is projected; validation and atomic writing
        # continue to use the full draft/projection and every selected disposition.
        budget=8192
        if recovery and recovery.get('scope')=='update' and size({'error':message(),**self.details})>budget:
            self.details=deepcopy(self.details)
            self.details['invalid_field_count']=len(problems)
            self.details['repair_presentation']={'byte_budget':budget,
                'meaning':'Whole exact items only. Omitted committed gaps remain available through recovery.read_call; omitted proposed gaps use the unchanged original draft text.'}
            lists=[(self.details,'invalid_fields')]
            lists += [(self.details['recovery'],key) for key in ('committed_gaps','proposed_gaps','gap_choices')
                      if isinstance(self.details['recovery'].get(key),list)]
            for problem in self.details['invalid_fields'][:16]:
                lists += [(problem,key) for key,value in problem.items() if isinstance(value,list)]
            # Cap candidate cardinality before repeated byte accounting. No exact
            # string, selector, path or fingerprint is shortened to fit the view.
            for owner,key in lists:
                if len(owner[key])>16:
                    owner['omitted_'+key]=len(owner[key])-16
                    owner[key]=owner[key][:16]
            while size({'error':message(),**self.details})>budget:
                active=[(owner,key) for owner,key in lists if owner[key]]
                if not active:break
                owner,key=max(active,key=lambda pair:size(pair[0][pair[1]]))
                owner[key].pop()
                owner['omitted_'+key]=owner.get('omitted_'+key,0)+1
        super().__init__(message())


def new_question_problems(payload):
    """Check declared text before an ID exists; never choose a disposition."""
    problems=[]
    missing=[k for k in ('operation_id','question','scope','answer')
             if not isinstance(payload.get(k),str) or not payload[k].strip()]
    if missing:
        problems.append({'path':'payload','missing_fields':missing,
                         'error':'New question needs nonempty operation_id, question, its investigation scope and provisional answer'})
    if payload.get('work_state')!='scoped_closed':return problems,[]
    if not payload.get('judgment'):
        problems.append({'path':'payload.judgment','error':'A new scoped answer needs explicit judgment selections from actual reads'})
    if payload.get('priority','unassessed')=='unassessed':
        problems.append({'path':'payload.priority','error':'Assess this question priority before scope closure',
                         'allowed_values':['decision_critical','material','contextual']})
    if any(not isinstance(payload.get(k,[]),list) or
           any(not isinstance(text,str) for text in payload[k]) for k in ('gaps','critical_gaps') if k in payload):
        return problems,[]  # The shape errors explain why no exact gap set is available.
    texts=list(dict.fromkeys(payload.get('critical_gaps',[])+payload.get('gaps',[])))
    dispositions=payload.get('gap_dispositions',[])
    selected=set()
    if isinstance(dispositions,list):
        for index,d in enumerate(dispositions):
            if not isinstance(d,dict):continue
            path=f'payload.gap_dispositions[{index}]'
            if 'gap_id' in d:
                problems.append({'path':path+'.gap_id','error':'New questions use exact gap_text from gaps/critical_gaps; no gap ID exists yet',
                                 'omit_fields':['gap_id'],'required_fields':['gap_text']})
            if 'gap_text' not in d or not isinstance(d['gap_text'],str):continue
            if d['gap_text'] not in texts:
                problems.append({'path':path+'.gap_text','error':'Select exact gap_text from this draft gaps/critical_gaps'})
            elif d['gap_text'] in selected:
                problems.append({'path':path,'error':'Duplicate gap disposition','gap_text':d['gap_text']})
            else:selected.add(d['gap_text'])
    remaining=[text for text in texts if text not in selected]
    if remaining:
        problems.append({'path':'payload.gap_dispositions',
            'error':'Account for every declared remaining gap before scope closure using exact gap_text and its disposition',
            'missing_gap_texts':remaining})
    return problems,[{'gap_text':text} for text in texts]


def question_state_problems(payload, state, host_args):
    """Independent identity/basis checks, including when the draft shape is invalid."""
    problems=[]
    question_id=payload.get('question_id')
    old=next((q for q in state['questions'] if q['id']==question_id),None)
    closure=payload.get('closure')
    if question_id and not old:
        problems.append({'path':'payload.question_id','error':'Unknown question; select an existing question from this case'})
    if old:
        if type(payload.get('expected_revision')) is not int or payload['expected_revision']!=old['revision']:
            problems.append({'path':'payload.expected_revision','error':'Question revision conflict','current_revision':old['revision']})
        for field,label in (('session_id','conversation'),('goal_id','goal')):
            if old.get(field) and old[field]!=host_args.get('_'+field,''):
                problems.append({'path':'payload.question_id','error':'Question belongs to another native '+label})
    elif not question_id and 'expected_revision' in payload:
        problems.append({'path':'payload.expected_revision','error':'New questions omit expected_revision and question_id; updates require both current values'})
    if 'closure' in payload:
        if not old or old['id']=='Q-intake':
            problems.append({'path':'payload.closure','error':'closure selects an existing question revision; save the answer before reading its closure basis'})
        if isinstance(closure,dict) and closure.get('basis_snapshot')!=state['meta']['snapshot_id']:
            problems.append({'path':'payload.closure.basis_snapshot','error':'Closure basis changed; use exact snapshot_id from the question read, not a description'})
        if old and old.get('next_checks') and payload.get('next_checks')!=[]:
            problems.append({'path':'payload.next_checks','error':'Explicitly reassess next_checks before closure; keep valuable checks active or set next_checks=[] with the closure rationale'})
    destination='scoped_closed' if isinstance(closure,dict) else payload.get('work_state')
    if old and destination not in (None,'open','active'):
        proposed={k:payload.get(k,old[k]) for k in ('question','target_proposition','scope')}
        if 'question' in payload and 'target_proposition' not in payload and old['question']==old['target_proposition']:
            proposed['target_proposition']=payload['question']
        if definition_version(proposed)!=old['definition_version']:
            problems.append({'path':'payload.scope','error':'A changed proposition/scope must first be investigated as an open question'})
    if old and destination=='scoped_closed' and payload.get('priority',old.get('priority','unassessed'))=='unassessed':
        problems.append({'path':'payload.priority','error':'Assess this question priority before scope closure: decision_critical, material or contextual'})
    recovery={'read_call':{'tool':'forsic_reporting','arguments':{'action':'state',**({'question_id':old['id']} if old else {})}},
              'meaning':'Read current state before revising this draft. No inputs were corrected or saved.'}
    recovery['scope']='update' if question_id else 'new'
    if old:
        recovery.update(question_id=old['id'],revision=old['revision'],snapshot_id=state['meta']['snapshot_id'],
            editable_in_current_conversation=all(not old.get(k) or old[k]==host_args.get('_'+k,'') for k in ('goal_id','session_id')))
        if not recovery['editable_in_current_conversation']:
            recovery['meaning']='This historical question is read-only here. Create a question for the current conversation; do not reuse its identity or transfer ownership.'
    elif 'closure' in payload:
        recovery['meaning']='closure requires a saved question. Save a new provisional or grounded answer, then use its returned question_id/read_call to inspect the exact revision and snapshot before closure.'
    elif not question_id:
        recovery.pop('read_call')
        recovery.update(required_fields=['operation_id','question','scope','answer'],
            omit_fields=['question_id','expected_revision'], gap_selector='gap_text',
            meaning='Repair original native arguments; preserve other fields and exact citation_refs. No question or gap IDs were saved. Choose dispositions explicitly.')
    return problems,recovery


def question_gap_projection(state, question, payload, note):
    """One exact draft gap set for validation and writing; no proposal is saved."""
    from .host import ref
    if any(not isinstance(payload.get(k,[]),list) or
           any(not isinstance(text,str) for text in payload[k]) for k in ('gaps','critical_gaps') if k in payload):
        return None
    existing=[g for g in state['gaps'] if g['question_ref']['id']==question['id']]
    proposed=[]
    if 'gaps' in payload or 'critical_gaps' in payload:
        draft={**note,**{k:deepcopy(payload[k]) for k in ('gaps','critical_gaps') if k in payload}}
        ids=payload.get('evidence_ids',question.get('evidence_ids',[]))
        # Shape/source validity is checked separately before the atomic writer.
        sources={s['id']:s for s in state['sources']}
        cited=[ref('source',sources[sid]) for sid in (ids if isinstance(ids,list) else [])
               if isinstance(sid,str) and sid in sources]
        proposed=note_gap_records(draft,question,cited)
    combined={g['id']:g for g in [*proposed,*existing]}
    committed_ids={g['id'] for g in existing}
    recovery=dict(committed_gaps=[{'gap_id':g['id'],'gap_text':g['original_obligation']} for g in existing],
        proposed_gaps=[{'gap_text':g['original_obligation']} for g in proposed if g['id'] not in committed_ids],
        draft_fingerprint=hash_value({'question_id':question['id'],'revision':question['revision'],
                                     'operation_id':payload.get('operation_id'),'draft':payload}),
        gap_selection='Use committed gap_id or exact current draft gap_text. Proposed gaps are not saved; changing text changes the draft. Choose each disposition explicitly.')
    return dict(existing=existing,proposed=proposed,combined=list(combined.values()),recovery=recovery)


def question_gap_problems(payload, state, question, projection):
    """Collect independent selector/coverage errors without choosing dispositions."""
    problems=[];selected=set()
    dispositions=payload.get('gap_dispositions',[])
    if not isinstance(dispositions,list):return problems
    for index,d in enumerate(dispositions):
        if not isinstance(d,dict):continue
        selectors=[k for k in ('gap_id','gap_text') if k in d]
        if len(selectors)!=1:continue  # The schema already explains this shape error.
        key=selectors[0];field='id' if key=='gap_id' else 'original_obligation'
        pool=projection['existing'] if key=='gap_id' else projection['combined']
        found=[g for g in pool if g[field]==d[key]]
        path=f'payload.gap_dispositions[{index}]'
        if len(found)!=1:
            message=('Unknown gaps ID: '+str(d[key])+'. ' if key=='gap_id' else '')
            problems.append({'path':path+'.'+key,'error':message+'Select one committed gap_id or exact current draft gap_text; no mutation was saved.'})
        elif found[0]['id'] in selected:
            problems.append({'path':path,'error':'Duplicate gap disposition'})
        else:selected.add(found[0]['id'])
    closure=payload.get('closure')
    if not isinstance(closure,dict) and payload.get('work_state')!='scoped_closed':return problems
    planned={m['gap_ref']['id'] for m in state['missions'] if m['question_ref']['id']==question['id'] and
             m['state'] in ('draft','candidate','ready','blocked')}
    required=[g for g in projection['combined'] if g['disposition'] not in ('resolved','not_applicable') and
              ((g['impact'] in ('decision_critical','material') or g['id'] in planned) if isinstance(closure,dict)
               else g['feasible_next_action']!='none')]
    missing=[g for g in required if g['id'] not in selected]
    if missing:
        committed={g['id'] for g in projection['existing']}
        problems.append({'path':'payload.gap_dispositions',
            'error':('Material or planned work requires its own explicit disposition' if isinstance(closure,dict) else
                     'Account for remaining question gaps with explicit dispositions before scope closure'),
            'missing_gap_count':len(missing),
            'missing_gap_ids':[g['id'] for g in missing if g['id'] in committed],
            'missing_gap_texts':[g['original_obligation'] for g in missing if g['id'] not in committed]})
    return problems


def validate_input(action, payload, *, compact=True, contract_version='v2', state=None, host_args=None, gap_projection=None):
    schema = deepcopy(compact_input_schemas(contract_version)[action])
    if not compact:
        for key in ('operation_id','expected_revision'):
            schema['properties'].pop(key,None)
        schema['required'] = ['mission_id','mission_version','result_id','result_version','outcome']
    problems=input_problems(payload,schema)
    recovery=None
    # Collect independent shape and available state errors before any write;
    # repairing a shape must not hide a stale or foreign closure identity.
    if action == 'question' and isinstance(payload, dict):
        closure = payload.get('closure')
        if isinstance(closure, dict):
            incompatible = sorted(set(payload) & {'question','target_proposition','scope','gaps',
                                                  'critical_gaps','work_state','reopen_conditions'})
            if incompatible:
                problems.append({'path':'payload.closure','incompatible_fields':incompatible})
            if 'reason' in payload and payload['reason'] != closure.get('reason'):
                problems.append({'path':'payload.reason','must_equal':'closure.reason'})
        elif payload.get('work_state') in ('held','scoped_closed','blocked_internal','blocked_external','budget_deferred'):
            disposition = {'type':'object','properties':{
                'reason':{'type':'string','minLength':1,'pattern':r'\S'},
                'reopen_conditions':{'type':'array','items':{'type':'string'},'minItems':1}},
                'required':['reason','reopen_conditions']}
            problems += input_problems(payload, disposition)
        if state is not None:
            state_problems,recovery=question_state_problems(payload,state,host_args or {})
            problems += state_problems
            if gap_projection is not None:
                old=next(q for q in state['questions'] if q['id']==payload['question_id'])
                problems += question_gap_problems(payload,state,old,gap_projection)
                recovery.update(gap_projection['recovery'])
        if not payload.get('question_id'):
            draft_problems,gap_choices=new_question_problems(payload)
            problems += draft_problems
            if recovery is not None and gap_choices:
                recovery['gap_choices']=gap_choices
    if problems:
        if action == 'question':
            raise QuestionInputError(problems,recovery,include_recovery=isinstance(payload,dict) and 'closure' in payload)
        prefix = 'Invalid '+action+' fields'
        if action=='assess':
            if any(x.get('unknown_fields') for x in problems): prefix = 'Unsupported assess fields'
            elif any(x.get('missing_fields') for x in problems): prefix = 'Missing assess fields'
        raise ValueError(prefix+': '+json.dumps(problems,ensure_ascii=False)+
            '. action='+action+'; allowed_fields='+','.join(schema['properties'])+
            '. No assessment was saved; no question or mission was changed. Read current state for question revision, mission/result/source versions. '
            'Use payload.reason for question correction/disposition; outer reason is the call purpose. '+
            ('Example: '+json.dumps({'operation_id':'new-request-id','expected_revision':'<current integer>',
              'mission_id':'<mission.id>','mission_version':'<mission.version>','result_id':'<source.id>',
              'result_version':'<source.version>','outcome':'inconclusive','reasoning_summary':'Meaning of this test only',
              'limitations':['Remaining limit'],'next_check':'Next useful check or no useful check with reason','citations':[]}) if action=='assess' else 'Example: '+json.dumps({'action':'question','payload':{'operation_id':'new-request-id','question':'One bounded question','scope':'The available acquisition scope','answer':'Undetermined; next discriminating check pending'}}) if action=='question' else 'Read the registered action input schema.'))


def mission_version(mission):
    def material(value):
        if isinstance(value,dict):
            return {k:(deepcopy(v) if k=='tool_arguments' else material(v)) for k,v in value.items() if k not in ('version','state','execution_start_ids','result_ids')}
        if isinstance(value,list):return [material(v) for v in value]
        return value
    return hash_value(material(mission))


def normalized_tool_arguments(arguments):
    return {k: v for k, v in arguments.items() if k != 'reason' and not k.startswith(('mission_', '_'))}


def image_read_reference_schema():
    return {'type':'object','properties':{
        'path':{'type':'string','minLength':1,'description':'Exact evidence image path from the saved mission.'},
        'mission_id':{'type':'string','minLength':1,'description':'Exact ID of the saved, unstarted image read/read_bytes mission.'},
        'mission_version':{'type':'string','minLength':1,'description':'Unchanged saved mission version from next_call or current mission details.'},
        'retry_of':{'type':'string','minLength':1,'description':'Only from a returned retry_call: exact transient failed result ID. One retry maximum; no input overrides or uncertain-work retry.'},
        'reason':{'type':'string'}},'required':['path','mission_id','mission_version'],'additionalProperties':False}


def image_read_reference_call(mission):
    """A proposed native call, never execution authority or a guessed mission."""
    arguments=mission.get('tool_arguments') or {}
    if (mission.get('proposed_tool_name')!='forsic_image_files' or
            mission.get('design_timing')!='before_result' or mission.get('reuse_result_refs') or
            mission.get('state') not in ('draft','candidate','ready') or
            mission.get('execution_start_ids') or mission.get('result_ids') or
            not isinstance(arguments,dict) or
            arguments.get('action') not in ('read','read_bytes') or
            not all(isinstance(value,str) and value for value in
                    (arguments.get('path'),mission.get('id'),mission.get('version')))):
        return None
    return {'tool':'forsic_image_read_mission','arguments':{'path':arguments['path'],
        'mission_id':mission['id'],'mission_version':mission['version']}}


def resolve_image_read_reference(case, arguments, schemas):
    """Resolve explicit immutable input inside Case.invoke's admission transaction.

    No I/O, latest-mission selection, mutation, retry or goal transition happens here.
    The caller still validates owner/version/definition/state and records the start.
    """
    from .host import current, find
    allowed={'path','action','mission_id','mission_version','reason','retry_of'}
    if set(arguments)-allowed:
        raise ValueError('from_mission accepts only path, action, mission_id, mission_version and reason; direct argument overrides are not allowed')
    if not isinstance(arguments.get('path'),str) or not arguments['path']:
        raise ValueError('from_mission requires an explicit nonempty evidence path')
    if schemas is None or 'forsic_image_files' not in schemas:
        raise ValueError('Saved-mission execution requires the registered native tool contract')
    mission=find(current(case),'missions',arguments['mission_id'])
    if arguments.get('retry_of'):
        validate_read_retry(case, mission, arguments['retry_of'])
    elif (mission.get('state') not in ('draft','candidate','ready') or
            mission.get('execution_start_ids') or mission.get('result_ids')):
        raise ValueError('from_mission requires an unstarted plan; returned, assessed or uncertain executions cannot be retried by reference')
    stored=mission.get('tool_arguments')
    if (mission.get('proposed_tool_name')!='forsic_image_files' or
            mission.get('design_timing')!='before_result' or mission.get('reuse_result_refs') or
            not isinstance(stored,dict) or stored.get('action') not in ('read','read_bytes')):
        raise ValueError('from_mission supports only a saved before-result image read/read_bytes plan')
    if arguments['path']!=stored.get('path'):
        raise ValueError('Submitted evidence path differs from this saved mission; no path was inferred')
    # Revalidate old plans against today's actual native contract before any backend.
    validate_native_plan({'tool_name':'forsic_image_files','tool_arguments':stored},schemas)
    return deepcopy(stored)


def pending_result(data):
    return data.get('status') in ('queued', 'running', 'pending') or data.get('state') in ('queued', 'running', 'pending')


def failed_result(data):
    return bool(data.get('error') or data.get('exit_code', 0) != 0 or
                data.get('status') in ('failed','timed_out','worker_unavailable','not_started') or
                (data.get('tool')=='forsic_verify' and not pending_result(data) and data.get('verification_complete') is not True))


def validate_read_retry(case, mission, result_id):
    # Only a committed, explicitly classified transient failure is retryable.
    # A new tool_start fences this receipt immediately, including crash windows.
    starts=mission.get('execution_start_ids', [])
    results=mission.get('result_ids', [])
    if (mission['state']!='failed' or len(starts)!=1 or len(results)!=1
            or results[-1]!=result_id or mission.get('proposed_tool_name')!='forsic_image_files'
            or mission.get('tool_arguments',{}).get('action') not in ('read','read_bytes')):
        raise ValueError('Retry requires the single latest committed transient read failure; successful, running, uncertain or already retried work cannot be repeated')
    event=case.event(result_id);data=event['data']
    if (event['kind']!='tool_result' or data.get('started_id')!=starts[0]
            or data.get('retryable_read_failure') is not True or not data.get('error')
            or data.get('execution',{}).get('termination_confirmed') is not True
            or data.get('execution',{}).get('state')!='failed'):
        raise ValueError('Selected result is not a confirmed transient read failure')


def validate_execution_context(case, mission_id, expected_revision, tool_name, arguments, *, goal_id='', session_id='', retry_of=None):
    """Read-only admission. Caller atomically stores this with tool_start.

    This checks identity and the analyst's exact intended input, not whether the
    analytical test or conclusion is good. Unbound tools remain available.
    """
    from .host import current, find
    s = current(case)
    m = find(s, 'missions', mission_id)
    q = find(s, 'questions', m['question_ref']['id'])
    if q.get('session_id') and q['session_id'] != session_id:
        raise ValueError('Mission belongs to another native conversation')
    if q.get('goal_id') and q['goal_id'] != goal_id:
        raise ValueError('Mission belongs to another native goal')
    if expected_revision != m['version']:
        raise ValueError('Stale mission version; read the current mission before execution')
    if m.get('question_definition_version') != q['definition_version']:
        raise ValueError('Mission belongs to a superseded question definition')
    if q['work_state'] != 'active' and q['work_state'] != 'open':
        raise ValueError('Reopen the question before executing its mission')
    if retry_of:
        validate_read_retry(case, m, retry_of)
    elif m['state'] in ('completed', 'partial', 'failed', 'unassessed'):
        raise ValueError('Mission already returned; evaluate the retained result or create a materially changed mission')
    actual = normalized_tool_arguments(arguments)
    planned = normalized_tool_arguments(m.get('tool_arguments', {}))
    poll = False
    if tool_name == m['proposed_tool_name'] == 'forsic_verify' and actual.get('action') == 'status':
        pending = [case.event(eid)['data'] for eid in m['result_ids'] if pending_result(case.event(eid)['data'])]
        poll = bool(pending) and actual.get('path') == planned.get('path') and {k:v for k,v in actual.items() if k!='action'} == {k:v for k,v in planned.items() if k!='action'}
    if m['state']=='running' and not poll:
        raise ValueError('Mission already has a running native call; inspect its result before another execution')
    if tool_name != m['proposed_tool_name'] or (actual != planned and not poll):
        differences={'missing_fields':sorted(set(planned)-set(actual)),
            'unexpected_fields':sorted(set(actual)-set(planned)),
            'different_fields':sorted(k for k in actual.keys() & planned.keys() if actual[k]!=planned[k])}
        raise ValueError('Native tool and exact arguments differ from this mission plan: '+json.dumps(differences)+
            '. Use the planning receipt next_call unchanged for the same check. A changed scope needs a new mission; no execution occurred.')
    return {'mission_id': m['id'], 'mission_version': m['version'], 'question_id': q['id'],
            'question_definition_version': q['definition_version'], 'tool_name': tool_name,
            'arguments_hash': hash_value(actual)}


def project_execution(state, events):
    """Derive execution from actual paired ledger receipts. No tool invocation."""
    starts = {}
    for event in events:
        data = event['data']; ctx = data.get('mission_context')
        if not isinstance(ctx, dict):
            continue
        m = next((m for m in state['missions'] if m['id'] == ctx.get('mission_id')), None)
        if not m or ctx.get('question_definition_version') != m.get('question_definition_version') or ctx.get('mission_version') != mission_version(m):
            continue
        if event['kind'] == 'tool_start':
            if ctx.get('tool_name') != data.get('tool') or hash_value(normalized_tool_arguments(data.get('arguments', {}))) != ctx.get('arguments_hash'):
                continue
            starts[event['id']] = (m, ctx)
            if event['id'] not in m['execution_start_ids']:
                m['execution_start_ids'].append(event['id'])
            if m['state'] not in ('completed', 'partial'):
                m['state'] = 'running'
        elif event['kind'] == 'tool_result':
            pair = starts.get(data.get('started_id'))
            if not pair or pair[1] != ctx or pair[0]['id'] != m['id'] or data.get('tool') != ctx.get('tool_name'):
                continue
            if event['id'] not in m['result_ids']:
                m['result_ids'].append(event['id'])
            if m['state'] not in ('completed', 'partial'):
                m['state'] = ('running' if pending_result(data) else 'failed' if failed_result(data) else 'unassessed')

    source_map={r['id']:r for r in state['sources']}
    event_map={e['id']:e for e in events}
    for m in state['missions']:
        if m['design_timing']!='before_result':continue
        tid='T-'+m['id']
        existing=next((t for t in state['tests'] if t['id']==tid),None)
        # A committed assessment is already bound to the same exact Test/result.
        if existing and existing['assessment_state']=='assessed':continue
        rid=m['result_ids'][-1] if m['result_ids'] else None
        source=source_map.get(rid)
        result=event_map[rid]['data'] if rid else None
        execution=('running' if result and pending_result(result) else 'failed' if result and failed_result(result)
                   else 'returned' if result else 'running' if m['execution_start_ids'] else 'candidate')
        test=dict(id=tid,version='1',question_ref=m['question_ref'],target_proposition=m['target_proposition'],
                  purpose='discriminate' if m['support_rule'] or m['refute_rule'] else 'discover',
                  immediate_observable=m['capability_requirement'],input_refs=m['input_refs'],required_view=m['required_view'],
                  tool_capability=m['proposed_tool_name'] or 'retained_result_read',target_scope=m['exact_target_scope'],
                  support_rule=m['support_rule'],refute_rule=m['refute_rule'],inconclusive_rule=m['inconclusive_rule'],
                  physical_job_ref=(result or {}).get('job_id') or (result or {}).get('started_id') or (m['execution_start_ids'][-1] if m['execution_start_ids'] else None),
                  result_ref={'kind':'source','id':source['id'],'version':source['version']} if source else None,
                  result_scope=source['coverage'] if source else None,execution_state=execution,assessment_state='unassessed',design_timing='before_result')
        state['tests']=[t for t in state['tests'] if t['id']!=tid]+[test]


def check_question_revision(question, payload, *, goal_id='', session_id=''):
    if type(payload.get('expected_revision')) is not int or payload.get('expected_revision') != question['revision']:
        raise ValueError(f"Question revision conflict: expected_revision={question['revision']}; read the current question")
    if question.get('session_id') and question['session_id'] != session_id:
        raise ValueError('Question belongs to another native conversation')
    if question.get('goal_id') and question['goal_id'] != goal_id:
        raise ValueError('Question belongs to another native goal')


def update_question(case, state, payload, host_args):
    from .host import find, ref, write, checked
    from ..notes import sources
    contract_version = host_args.get('_question_contract', 'v1')
    old=next((q for q in state['questions'] if isinstance(payload,dict) and q['id']==payload.get('question_id')),None)
    projection=None
    if old:
        with case.connect() as db:
            note=latest_note(db,old['id'].removeprefix('Q-')) or {'revision':old['revision']}
        projection=question_gap_projection(state,old,payload,note)
    validate_input('question',payload,contract_version=contract_version,state=state,host_args=host_args,
                   gap_projection=projection)
    payload = deepcopy(payload)
    question_id=payload.get('question_id')
    if not question_id and 'expected_revision' in payload:
        raise ValueError('New questions omit expected_revision and question_id; updates require both current values')
    old=find(state,'questions',question_id) if question_id else None
    if old:check_question_revision(old,payload,goal_id=host_args.get('_goal_id',''),session_id=host_args.get('_session_id',''))
    closure = payload.get('closure')
    if closure:
        if not old or old['id'] == 'Q-intake':
            raise ValueError('closure selects an existing question revision; save the answer before reading its closure basis')
        if closure['basis_snapshot'] != state['meta']['snapshot_id']:
            raise ValueError('Closure basis changed; read current state for this question before retaining unresolved limits')
        if any(k in payload for k in ('question','target_proposition','scope','gaps','critical_gaps','work_state','reopen_conditions')):
            raise ValueError('closure uses the unchanged question definition and gap set; edit those separately before reading a new basis')
        if 'reason' in payload and payload['reason'] != closure['reason']:
            raise ValueError('closure reason and top-level reason must agree')
        if old.get('next_checks') and payload.get('next_checks') != []:
            raise ValueError('Explicitly reassess next_checks before closure; keep valuable checks active or set next_checks=[] with the closure rationale')
        payload.update(work_state='scoped_closed',reason=closure['reason'],reopen_conditions=closure['reopen_conditions'])
    args={k:deepcopy(v) for k,v in payload.items() if k in NOTE_FIELDS}
    args['_question_contract'] = contract_version
    if closure:
        args['_closure_basis_snapshot'] = closure['basis_snapshot']
    args.update({k:host_args[k] for k in ('_goal_id','_session_id','_session_lineage') if k in host_args})
    if 'review_responses' in payload:
        args['_review_responses'] = deepcopy(payload['review_responses'])
    if old and question_id!='Q-intake':
        args.update(note_id=question_id.removeprefix('Q-'),revision=old['revision'])
    elif old:
        args.setdefault('question',old['question']);args.setdefault('scope',old['scope']);args.setdefault('answer',old['answer'])
    if not old and not all(str(args.get(k,'')).strip() for k in ('question','scope','answer')):
        raise ValueError('New question needs question, its investigation scope and provisional answer')
    sources(case,args.get('evidence_ids',[]))
    update={k:deepcopy(payload[k]) for k in ('work_state','assessment','priority','reopen_conditions') if k in payload}
    if update.get('work_state','open') not in WORK_STATES or update.get('priority','unassessed') not in PRIORITIES:
        raise ValueError('Unknown work_state or priority')
    if update.get('assessment','undetermined') not in ('supported','refuted','conflicting','undetermined'):
        raise ValueError('Unknown assessment')
    if 'assessment' in payload:
        update['judgment_review_required']=False
    destination=update.get('work_state')
    if old and destination not in (None,'open','active'):
        proposed={'question':payload.get('question',old['question']),'target_proposition':payload.get('target_proposition',old['target_proposition']),'scope':payload.get('scope',old['scope'])}
        if 'question' in payload and 'target_proposition' not in payload and old['question']==old['target_proposition']:
            proposed['target_proposition']=payload['question']
        if definition_version(proposed)!=old['definition_version']:
            raise ValueError('A changed proposition/scope must first be investigated as an open question')
    reason=str(payload.get('reason','')).strip()
    if destination and destination != (old or {}).get('work_state','open') and not reason:
        raise ValueError('Work disposition changes require an explicit reason')
    if destination in ('held','scoped_closed','blocked_internal','blocked_external','budget_deferred'):
        if not reason or not payload.get('reopen_conditions'):
            raise ValueError('Closure/defer needs a reason and reopening conditions')
        update.update(closure_rationale=reason if destination=='scoped_closed' else None,
                      deferred_reason=None if destination=='scoped_closed' else reason)
    elif destination in ('open','active'):
        update.update(closure_rationale=None,deferred_reason=None)
    dispositions=payload.get('gap_dispositions',[])
    gap_changes=[]
    proposed_gaps=[]
    if old:
        # Closing while adding a new gap must account for that gap as well.
        existing=projection['existing']
        proposed_gaps=projection['proposed']
        gap_changes=disposed_gaps(projection['combined'],dispositions,old)
        if closure:
            explicit = {g['id'] for g in gap_changes}
            planned = {m['gap_ref']['id'] for m in state['missions']
                       if m['question_ref']['id'] == old['id'] and m['state'] in ('draft','candidate','ready','blocked')}
            for gap in existing:
                if gap['id'] in explicit or gap['disposition'] in ('resolved','not_applicable'):
                    continue
                if gap['impact'] in ('decision_critical','material') or gap['id'] in planned:
                    raise ValueError('Material or planned work requires its own explicit disposition: '+gap['id'])
                gap_changes.append({**gap, 'disposition':'assessed_unresolved',
                    'reason':closure['reason'], 'feasible_next_action':'none',
                    'reopen_conditions':closure['reopen_conditions'], 'resolution_refs':[]})
    else:
        # The actual note ID and selected sources are resolved by the same writer.
        args['_new_gap_dispositions']=deepcopy(dispositions)
    if destination=='scoped_closed':
        if not old and not payload.get('judgment'):
            raise ValueError('A new scoped answer needs explicit judgment selections from actual reads; no prior question registration or mission is required')
        if update.get('priority',(old or {}).get('priority','unassessed'))=='unassessed':
            raise ValueError('Assess this question priority before scope closure: decision_critical, material or contextual. Closure cannot substitute for importance assessment.')
        pending=[m for m in state['missions'] if m['question_ref']['id']==(old or {}).get('id') and m['state'] in ('running','queued','unassessed')]
        unassessed=[t for t in state['tests'] if t['question_ref']['id']==(old or {}).get('id') and
                    (t['execution_state'] in ('running','queued') or (t.get('result_ref') and t['assessment_state'] in ('unassessed','stale')))]
        if pending or unassessed:raise ValueError('Running or unassessed native work remains for this question')
        outstanding={g['id'] for g in [*state['gaps'],*proposed_gaps] if g['question_ref']['id']==(old or {}).get('id') and g['disposition'] not in ('resolved','not_applicable') and g['feasible_next_action']!='none'}
        missing=sorted(outstanding-{g['id'] for g in gap_changes})
        if missing:
            raise ValueError('Account for remaining question gaps with explicit dispositions before scope closure. No mutation was saved. '+json.dumps({
                'missing_gap_count':len(missing), 'missing_gap_ids':missing[:10],
                'omitted_gap_ids':max(0,len(missing)-10),
                'read_call':{'tool':'forsic_reporting','arguments':{'action':'gaps','question_id':old['id']}},
                'paging':'Each response may contain only a page; follow next_call until it is null.'}))
        judgment=payload.get('judgment',{})
        selected_citations=judgment.get('citation_refs') or any(c.get('citation_refs') for c in judgment.get('claims',[]))
        if not (old or {}).get('claim_refs') and not (payload.get('evidence_ids') or (old or {}).get('evidence_ids') or selected_citations):
            raise ValueError('Scope closure requires retained basis; it is not an incident verdict')
    args['correction_reason']=reason or payload.get('reason','')
    args['_claim_action']=payload.get('claim_action')
    if 'judgment' in payload:
        if not payload.get('answer','').strip() or not reason:
            raise ValueError('A grounded judgment requires an explicit answer and reason')
        if payload.get('claim_action') in ('retain','retract'):
            raise ValueError('A new grounded judgment replaces the current claim; retain/retract cannot be combined')
        args['_judgment']=deepcopy(payload['judgment'])
        args['_claim_action']='replace'
    if not old or question_id=='Q-intake':
        with case.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            op=payload['operation_id'];replay=mutation_replay(db,op,host_args)
            if replay is not None:return replay
            from ..question_bootstrap import existing_owned_receipt
            existing = existing_owned_receipt(case, host_args)
            if existing is not None:
                return existing
            result=save_note_locked(case,db,args,question_update=update)
            receipt={'question':question_receipt(result['note'])}
            record_mutation(db,op,host_args,receipt)
        return receipt
    change={'question_ref':ref('question',old),'records':{'gaps':gap_changes}}
    # The same atomic writer handles note compatibility, report records and CAS.
    receipt=write(case,state,change,note_update={'args':args,'question_update':update})
    from .host import current
    now=find(current(case),'questions',old['id'])
    return {'receipt':receipt,'question':{**{k:now[k] for k in ('id','revision','definition_version','work_state')},
        'read_call':{'tool':'forsic_reporting','arguments':{'action':'state','question_id':now['id']}},
        'gap_lookup':{'tool':'forsic_reporting','arguments':{'action':'gaps','question_id':now['id']}}},'semantic_approval':False}


def validate_retained_mission(payload):
    """The explicit retained-results entry never supplies a physical tool plan."""
    validate_input('mission_from_results', payload)
    strings = ('operation_id', 'question_id', 'gap_id', 'why_it_matters', 'inconclusive_rule',
               'target_proposition', 'support_rule', 'refute_rule')
    for key in strings:
        if key in payload and not payload[key].strip():
            raise ValueError(key + ' must be a nonempty string. No retained-result mission was saved.')
    for key in ('result_ids', 'limits', 'reopen_conditions'):
        if not payload[key]:
            raise ValueError(key + ' must contain an explicit nonempty selection. No retained-result mission was saved.')
    for key in ('result_ids', 'input_ids', 'counterevidence_ids', 'limits', 'reopen_conditions'):
        if any(not item.strip() for item in payload.get(key, [])):
            raise ValueError(key + ' contains an empty value. No retained-result mission was saved.')
    if len(set(payload['result_ids'])) != len(payload['result_ids']):
        raise ValueError('Select each retained result_id once; repetitions are not new evidence. No mission was saved.')


def expand_mission(state, payload, *, goal_id='', session_id=''):
    from .host import find, ref
    validate_input('mission',payload)
    q=find(state,'questions',payload['question_id']);check_question_revision(q,payload,goal_id=goal_id,session_id=session_id)
    if q['work_state'] not in ('open','active'):raise ValueError('Reopen a question before planning another mission')
    gaps=[g for g in state['gaps'] if g['question_ref']['id']==q['id'] and g['disposition'] not in ('resolved','not_applicable')]
    g=find(state,'gaps',payload['gap_id']) if payload.get('gap_id') else (gaps[0] if len(gaps)==1 else None)
    if not g or g['id'] not in {gap['id'] for gap in gaps}:
        candidates=[{'gap_id':gap['id'],'reason_preview':gap['reason'][:240]} for gap in gaps[:6]]
        raise ValueError('Select gap_id from this question when it has several gaps. No mission was saved. '
            'Use '+('forsic_mission_from_results' if payload.get('result_ids') else 'forsic_mission')+' with gap_id at the top level, not forsic_reporting(action=mission). '
            'Changing only operation_id or rationale does not fix the missing reference. '
            'Current expected_revision='+str(q['revision'])+'; gap_candidates='+json.dumps(candidates,ensure_ascii=False)+
            '; omitted_gap_count='+str(max(0,len(gaps)-len(candidates)))+
            '. Read more with forsic_reporting(action=state, pointer=/gaps, snapshot_id='+state['meta']['snapshot_id']+').')
    def source_refs(key):return [ref('source',find(state,'sources',sid)) for sid in payload.get(key,[])]
    results=source_refs('result_ids');inputs=source_refs('input_ids');counter=source_refs('counterevidence_ids')
    if not payload.get('why_it_matters') or not payload.get('inconclusive_rule') or not payload.get('limits') or not payload.get('reopen_conditions'):
        raise ValueError('Mission needs purpose, inconclusive condition, limits and reopening conditions')
    if not results and (not payload.get('tool_name') or not isinstance(payload.get('tool_arguments'),dict)):
        raise ValueError('Planned mission needs the native tool and its exact arguments')
    return dict(id='M-'+uuid.uuid4().hex,version='1',original_obligation_id=g['id'],gap_ref=ref('gap',g),question_ref=ref('question',q),
        report_targets=g['section_targets'],target_proposition=payload.get('target_proposition') or q['target_proposition'],
        why_it_matters=payload['why_it_matters'],current_answer=q['answer'],competing_explanation_refs=q['hypothesis_refs'],
        mission_kind='evaluate_result' if results else 'design_test',input_refs=inputs,
        required_view=payload.get('required_view','exact_excerpt'),capability_requirement=payload['why_it_matters'],
        proposed_tool_name=payload.get('tool_name'),exact_target_scope=q['scope'],reuse_result_refs=results,
        design_timing='after_result' if results else 'before_result',support_rule=payload.get('support_rule'),refute_rule=payload.get('refute_rule'),
        inconclusive_rule=payload['inconclusive_rule'],preserved_counterevidence_refs=counter,state='draft',budget={'authority':'unallocated'},
        material_change_required=payload['why_it_matters'],completion_proof=['Actual retained result and source-bound assessment'],
        does_not_resolve=payload['limits'],reopen_conditions=payload['reopen_conditions'],tool_arguments=payload.get('tool_arguments',{}),
        question_definition_version=q['definition_version'],compact_contract=True)


def expand_assessment(state,payload,*,goal_id='',session_id=''):
    from .host import find
    validate_input('assess',payload)
    m=find(state,'missions',payload['mission_id']);q=find(state,'questions',m['question_ref']['id'])
    check_question_revision(q,payload,goal_id=goal_id,session_id=session_id)
    result=find(state,'sources',payload['result_id'])
    p={k:deepcopy(v) for k,v in payload.items() if k not in ('operation_id','expected_revision')}
    if p.get('mission_version') != m['version'] or p.get('result_version') != result['version']:
        raise ValueError('Stale or missing mission/result version')
    for citation in p.get('citations',[]):
        if citation.get('source_version') != find(state,'sources',citation['source_id'])['version']:
            raise ValueError('Stale or missing citation source_version')
    return p
