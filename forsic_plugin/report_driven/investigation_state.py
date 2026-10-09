"""Case-local question mutations and native-tool mission linkage; no scheduler/model."""
from copy import deepcopy
import hashlib
import json
import time
import uuid

PRIORITIES = {'decision_critical', 'material', 'contextual', 'unassessed'}
WORK_STATES = {'open', 'active', 'held', 'scoped_closed', 'blocked_internal', 'blocked_external', 'budget_deferred'}
QUESTION_FIELDS = ('work_state', 'assessment', 'priority', 'closure_rationale', 'deferred_reason',
                   'reopen_conditions', 'claim_refs', 'remaining_gap_ids', 'judgment_review_required')
NOTE_FIELDS = ('question', 'target_proposition', 'scope', 'answer', 'evidence_ids', 'status',
               'alternatives', 'gaps', 'critical_gaps', 'next_checks', 'timeline', 'correction_reason')


def hash_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def definition_version(note, default_scope=''):
    return hash_value({'target_proposition': note.get('target_proposition') or note['question'],
                       'scope': note.get('scope', default_scope)})


def mutation_replay(db, operation_id, request):
    if not operation_id:
        return None
    if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 128:
        raise ValueError('operation_id must be a nonempty string of at most 128 characters')
    row = db.execute("SELECT data FROM events WHERE kind='mutation_receipt' AND json_extract(data,'$.operation_id')=? ORDER BY rowid DESC LIMIT 1", (operation_id,)).fetchone()
    if row:
        stored = json.loads(row['data'])
        if stored['request_hash'] != hash_value(request):
            raise ValueError('operation_id already belongs to a different mutation payload')
        return deepcopy(stored['response'])
    return None


def record_mutation(db, operation_id, request, response):
    if operation_id:
        data = dict(operation_id=operation_id, request_hash=hash_value(request), response=response)
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (uuid.uuid4().hex, time.time(), 'mutation_receipt', '', json.dumps(data, ensure_ascii=False)))


def latest_note(db, note_id):
    row = db.execute("SELECT data FROM events WHERE kind='note' AND json_extract(data,'$.note_id')=? ORDER BY rowid DESC LIMIT 1", (note_id,)).fetchone()
    return json.loads(row['data']) if row else None


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
    value.update({k: deepcopy(args[k]) for k in NOTE_FIELDS if k in args})
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
    if old and definition_version(old, default_scope) != value['definition_version']:
        value['investigation'] = {'work_state': 'open', 'assessment': 'undetermined', 'claim_refs': [],
                                  'remaining_gap_ids': [], 'closure_rationale': None, 'deferred_reason': None,
                                  'reopen_conditions': [], 'priority': old.get('investigation', {}).get('priority', 'unassessed')}
    changed = old and any(old.get(k) != value.get(k) for k in
                          ('answer', 'evidence_ids', 'alternatives'))
    claim_action = args.get('_claim_action')
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
    if old and all(old.get(k) == value.get(k) for k in value if k not in ('correction_reason', 'updated_at', 'revision')):
        return {'note': old, 'reused': True}
    value['revision'] = old['revision'] + 1 if old else 1
    value['updated_at'] = time.time()
    db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (uuid.uuid4().hex, value['updated_at'], 'note', '', json.dumps(value, ensure_ascii=False)))
    return {'note': value}


def question_receipt(note):
    return {'question_id': 'Q-' + note['note_id'], 'revision': note['revision'],
            'definition_version': note['definition_version'],
            'work_state': note.get('investigation', {}).get('work_state', 'open'),
            'semantic_approval': False}


def compact_input_schemas():
    string = {'type': 'string'}
    strings = {'type': 'array', 'items': string}
    common = {'operation_id': string, 'expected_revision': {'type': 'integer', 'minimum': 1}, 'question_id': string}
    question = {**common, **{k: string for k in ('question', 'target_proposition', 'scope', 'answer', 'reason')},
                **{k: strings for k in ('evidence_ids', 'alternatives', 'gaps', 'critical_gaps', 'next_checks', 'reopen_conditions')},
                'work_state': {'type': 'string', 'enum': sorted(WORK_STATES)},
                'priority': {'type': 'string', 'enum': sorted(PRIORITIES)},
                'assessment': {'type': 'string', 'enum': ['supported', 'refuted', 'conflicting', 'undetermined']},
                'gap_dispositions': {'type': 'array', 'items': {'type': 'object', 'properties': {
                    'gap_id': string, 'disposition': {'type': 'string', 'enum': ['resolved', 'not_applicable', 'assessed_unresolved']},
                    'reason': string, 'reopen_conditions': strings}, 'required': ['gap_id', 'disposition', 'reason', 'reopen_conditions'], 'additionalProperties': False}}}
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
    assess['question_update'] = {'type':'object','properties': {
        **{k:string for k in ('answer','reason','reasoning_summary')},
        'assessment':question['assessment'], 'assertion_kind':{'type':'string','enum':['fact','interpretation']},
        'inference_strength':{'type':'string','enum':['favored','plausible','unrated']},
        **{k:strings for k in ('assumptions','alternatives','limitations','next_checks','reopen_conditions')}
        }, 'required':['answer','assessment','reason'], 'additionalProperties':False}
    def schema(properties, required):
        return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}
    return {'question': schema(question, ['operation_id']),
            'mission': schema(mission, ['operation_id', 'question_id', 'expected_revision', 'why_it_matters', 'inconclusive_rule', 'limits', 'reopen_conditions']),
            'assess': schema(assess, ['operation_id', 'expected_revision', 'mission_id', 'mission_version', 'result_id', 'result_version', 'outcome', 'reasoning_summary', 'limitations', 'next_check', 'citations'])}



def validate_input(action, payload, *, compact=True):
    schema = deepcopy(compact_input_schemas()[action])
    if not compact:
        for key in ('operation_id','expected_revision'):
            schema['properties'].pop(key,None)
        schema['required'] = ['mission_id','mission_version','result_id','result_version','outcome']
    problems=[]
    def check(value, spec, path):
        kind=spec.get('type')
        valid={'object':isinstance(value,dict),'array':isinstance(value,list),
               'string':isinstance(value,str),'integer':type(value) is int}.get(kind,True)
        if not valid:
            problems.append({'path':path,'expected_type':kind});return
        if 'enum' in spec and value not in spec['enum']:
            problems.append({'path':path,'allowed_values':spec['enum']})
        if kind=='integer' and value<spec.get('minimum',value):
            problems.append({'path':path,'minimum':spec['minimum']})
        if kind=='object':
            unknown=sorted(set(value)-set(spec.get('properties',{}))) if spec.get('additionalProperties') is False else []
            missing=sorted(set(spec.get('required',[]))-set(value))
            if unknown or missing:problems.append({'path':path,'unknown_fields':unknown,'missing_fields':missing})
            for k,v in value.items():
                if k in spec.get('properties',{}):check(v,spec['properties'][k],path+'.'+k)
        if kind=='array':
            for i,v in enumerate(value):check(v,spec['items'],path+f'[{i}]')
    check(payload,schema,'payload')
    if problems:
        prefix=('Unsupported assess fields' if any(x.get('unknown_fields') for x in problems) else 'Missing assess fields') if action=='assess' else 'Invalid '+action+' fields'
        raise ValueError(prefix+': '+json.dumps(problems,ensure_ascii=False)+
            '. action='+action+'; allowed_fields='+','.join(schema['properties'])+
            '. No assessment was saved; no question or mission was changed. Read current state for question revision, mission/result/source versions. '
            'Use payload.reason for question correction/disposition; outer reason is the call purpose. '+
            ('Example: '+json.dumps({'operation_id':'new-request-id','expected_revision':'<current integer>',
              'mission_id':'<mission.id>','mission_version':'<mission.version>','result_id':'<source.id>',
              'result_version':'<source.version>','outcome':'inconclusive','reasoning_summary':'Meaning of this test only',
              'limitations':['Remaining limit'],'next_check':'Next useful check or no useful check with reason','citations':[]}) if action=='assess' else 'Read the registered action input schema.'))


def mission_version(mission):
    def material(value):
        if isinstance(value,dict):
            return {k:(deepcopy(v) if k=='tool_arguments' else material(v)) for k,v in value.items() if k not in ('version','state','execution_start_ids','result_ids')}
        if isinstance(value,list):return [material(v) for v in value]
        return value
    return hash_value(material(mission))


def normalized_tool_arguments(arguments):
    return {k: v for k, v in arguments.items() if k != 'reason' and not k.startswith(('mission_', '_'))}


def pending_result(data):
    return data.get('status') in ('queued', 'running', 'pending') or data.get('state') in ('queued', 'running', 'pending')


def failed_result(data):
    return bool(data.get('error') or data.get('exit_code', 0) != 0 or
                data.get('status') in ('failed','timed_out','worker_unavailable','not_started') or
                (data.get('tool')=='forsic_verify' and not pending_result(data) and data.get('verification_complete') is not True))


def validate_execution_context(case, mission_id, expected_revision, tool_name, arguments, *, goal_id='', session_id=''):
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
    if m['state'] in ('completed', 'failed', 'unassessed'):
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
        raise ValueError('Native tool and exact arguments differ from this mission plan')
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
    validate_input('question',payload)
    question_id=payload.get('question_id')
    old=find(state,'questions',question_id) if question_id else None
    if old:check_question_revision(old,payload,goal_id=host_args.get('_goal_id',''),session_id=host_args.get('_session_id',''))
    args={k:deepcopy(v) for k,v in payload.items() if k in NOTE_FIELDS}
    args.update({k:host_args[k] for k in ('_goal_id','_session_id') if k in host_args})
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
    gap_changes=[]
    dispositions=payload.get('gap_dispositions',[])
    if len({d['gap_id'] for d in dispositions})!=len(dispositions):raise ValueError('Duplicate gap disposition')
    for d in dispositions:
        g=find(state,'gaps',d['gap_id'])
        if not old or g['question_ref']['id']!=old['id']:
            raise ValueError('Gap belongs to a different question')
        if d['disposition'] not in ('resolved','not_applicable','assessed_unresolved') or not d.get('reason','').strip() or not d.get('reopen_conditions'):
            raise ValueError('Each gap disposition needs a reason and reopening conditions')
        basis=list(g['basis_refs']) or [ref('question',old)]
        gap_changes.append({**g,'disposition':d['disposition'],'reason':d['reason'],
                            'feasible_next_action':'none','reopen_conditions':d['reopen_conditions'],
                            'resolution_refs':basis if d['disposition']=='resolved' else []})
    if destination=='scoped_closed':
        if not old:raise ValueError('Create and investigate a question before closing its scope')
        pending=[m for m in state['missions'] if m['question_ref']['id']==old['id'] and m['state'] in ('running','queued','unassessed')]
        unassessed=[t for t in state['tests'] if t['question_ref']['id']==old['id'] and t.get('result_ref') and t['assessment_state']=='unassessed']
        if pending or unassessed:raise ValueError('Running or unassessed native work remains for this question')
        outstanding={g['id'] for g in state['gaps'] if g['question_ref']['id']==old['id'] and g['disposition'] not in ('resolved','not_applicable') and g['feasible_next_action']!='none'}
        if outstanding-{g['id'] for g in gap_changes}:
            raise ValueError('Account for remaining question gaps with explicit dispositions before scope closure')
        if not old.get('claim_refs') and not (payload.get('evidence_ids') or old.get('evidence_ids')):
            raise ValueError('Scope closure requires retained basis; it is not an incident verdict')
    args['correction_reason']=reason or payload.get('reason','')
    args['_claim_action']=payload.get('claim_action')
    if not old or question_id=='Q-intake':
        with case.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            op=payload['operation_id'];replay=mutation_replay(db,op,host_args)
            if replay is not None:return replay
            result=save_note_locked(case,db,args,question_update=update)
            receipt={'question':question_receipt(result['note'])}
            record_mutation(db,op,host_args,receipt)
        return receipt
    change={'question_ref':ref('question',old),'records':{'gaps':gap_changes}}
    # The same atomic writer handles note compatibility, report records and CAS.
    receipt=write(case,state,change,note_update={'args':args,'question_update':update})
    from .host import current
    now=find(current(case),'questions',old['id'])
    return {'receipt':receipt,'question':{k:now[k] for k in ('id','revision','definition_version','work_state')},'semantic_approval':False}


def expand_mission(state, payload, *, goal_id='', session_id=''):
    from .host import find, ref
    validate_input('mission',payload)
    q=find(state,'questions',payload['question_id']);check_question_revision(q,payload,goal_id=goal_id,session_id=session_id)
    if q['work_state'] not in ('open','active'):raise ValueError('Reopen a question before planning another mission')
    gaps=[g for g in state['gaps'] if g['question_ref']['id']==q['id'] and g['disposition'] not in ('resolved','not_applicable')]
    g=find(state,'gaps',payload['gap_id']) if payload.get('gap_id') else (gaps[0] if len(gaps)==1 else None)
    if not g or g['question_ref']['id']!=q['id']:raise ValueError('Select gap_id from this question when it has several gaps')
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
