"""Case ledger -> versioned report state and bounded, source-bound proposals.

Only the native tool entry point writes events. Reading/projecting has no model,
scheduler, source-file access, or state-changing side effects. Historical notes
remain candidates; host adoption validates provenance, not semantic truth.
"""
from copy import deepcopy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from ..notes import EVIDENCE_TOOLS, file_time, epoch_time, log_time, sources as verified_sources
from .contracts import ReportState, Mission, Gap, Requirement
from .gap_audit import audit, find_refs

ROOT = Path(__file__).resolve().parents[1]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def definition(value):
    """Logical contract identity, excluding projection revision and work status."""
    if isinstance(value,dict):return {k:definition(v) for k,v in value.items() if k not in ('version','state')}
    if isinstance(value,list):return [definition(v) for v in value]
    return value


def ref(kind, record):
    return dict(kind=kind, id=record['id'], version=record['version'])


def ledger(case):
    with closing(sqlite3.connect(case.db.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        return [{**dict(r), 'data': json.loads(r['data'])} for r in db.execute('SELECT rowid,* FROM events ORDER BY rowid')]


def pointer(value, path):
    if not path.startswith('/'):
        raise ValueError('Use an exact JSON pointer into the retained tool result')
    for key in path[1:].split('/'):
        key = key.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    if not isinstance(value, (str, int, float, bool)) or value is None:
        raise ValueError('Select a scalar original field, not a metadata summary of a body')
    return str(value)


def catalog():
    # Use Hermes's existing YAML dependency, without adding another package.
    from ruamel.yaml import YAML
    return YAML(typ='safe').load((ROOT/'skills/forsic-report-driven/references/section_catalog.yaml').read_text())['requirements']


def source_record(event):
    data = event['data']
    external = data.get('source_kind') == 'external_intelligence'
    return dict(id=event['id'], version=digest(data), label=('외부 평판 · ' + str(data.get('indicator',''))) if external else str(data.get('file_path') or data.get('path') or data['tool']),
                source_role='reference_material' if external else 'tool_result', locator='activity.sqlite3#'+event['id'], content_sha256=digest(data),
                provenance=data['tool']+' / '+str(data.get('started_id', '')),
                coverage=json.dumps({k: data[k] for k in ('scope','line_start','line_end','byte_start','byte_end','next_line','next_offset','coverage_complete','complete_file','outcome','error','source_url','lookup_at','limitation') if k in data}, ensure_ascii=False) or '도구 반환 범위만 보존됨; 전체 조사 범위는 미확인',
                source_generation_group=str((data.get('reused_from') or event['id']) if external else (data.get('job_id') or data.get('started_id') or event['id'])))


def execution_state(events):
    sessions={e['session'] for e in events if e['session']}
    try:
        from hermes_cli.config import get_hermes_home
        path=get_hermes_home()/'state.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            goals=[json.loads(row[1]) for row in db.execute("SELECT key,value FROM state_meta WHERE key LIKE 'goal:%'") if row[0][5:] in sessions]
        if goals:
            status=goals[-1].get('status')
            return ('running' if status=='active' else 'stopped', '저장된 조사 목표 상태 '+str(status)+'; active는 현재 프로세스 생존·진전 증명이 아니며 done은 분석 통과가 아님')
    except (ImportError,OSError,sqlite3.Error):pass
    return 'unknown','이 스냅샷에 연결된 native goal 상태가 없어 실행 상태를 확인하지 못함'


def current(case):
    events = ledger(case)
    source_events = {e['id']: e for e in events if e['kind']=='tool_result' and e['data'].get('tool') in EVIDENCE_TOOLS}
    sources = [source_record(e) for e in source_events.values()]
    source_map = {s['id']:s for s in sources}
    notes = {}
    for e in events:
        if e['kind']=='note': notes[e['data']['note_id']] = e['data']
    relevant = [e for e in events if e['kind'] in ('note','indicator','report_change','goal_started','turn_complete','turn_start') or e['id'] in source_events]
    scope_text = str(case.config.get('scope') or '증거 선택 범위 외의 조사 범위는 아직 합의되지 않음')
    scope = dict(id='scope', version=digest({k:case.config.get(k) for k in ('scope','question','selected_files','evidence_root')}),
                 original_question=case.config.get('question') or '조사 질문 미지정', approved_scope=scope_text,
                 excluded_scope='선택한 증거 밖·원문 명령 실행·외부 수집은 승인되지 않음', display_timezone='Asia/Seoul', evidence_labels=case.config.get('selected_files') or [case.config.get('label','선택된 증거')])
    if any(e['data'].get('source_kind') == 'external_intelligence' for e in source_events.values()):
        scope['external_transmission_authorized'] = True
        scope['external_transmission_scope'] = '설정에서 허용된 지표만 GTI 기존 보고서 조회에 전송. 파일·원문 업로드 및 재분석 요청은 제외.'
        scope['excluded_scope'] = 'GTI 지표 조회 외 외부 수집·전송 및 원문 명령 실행은 승인되지 않음'
        scope['version'] = digest({k:v for k,v in scope.items() if k != 'version'})
    state = dict(meta=dict(data_mode='synthetic' if case.config.get('synthetic') else 'live', case_id=case.config['case_id'],
                 run_id=next((e['session'] for e in events if e['kind']=='goal_started'), '') or '실제 실행 식별 미제공',
                 report_id='report-'+case.config['case_id'], snapshot_id='', ledger_cutoff=str(relevant[-1]['id'] if relevant else 'empty'),
                 generated_at=datetime.fromtimestamp(relevant[-1]['time'] if relevant else 0,timezone.utc).isoformat(),
                 classification='내부 검토용 · 외부 배포 미승인', prepared_by='Forsic 원장 투영', reviewed_by=None, approved_by=None),
                 status=dict(execution='stopped', investigation='partial', report='draft', integrity_gate='unverified', semantic_gate='not_reviewed', layout_gate='not_rendered', approval_gate='not_requested', explicit_limitations=['실행 상태 미확인; 보고 도구는 실행 종료나 조사 완료를 선언하지 않음','근거 결속 검사는 자연어 해석 정확성·기관 승인 검증이 아님']),
                 scope=scope, sources=sources, observations=[], claims=[], questions=[], hypotheses=[], tests=[], assessments=[], gaps=[], missions=[], timeline=[], actions=[], requirements=[],
                 coverage_summary=f'서로 다른 도구 반환 {len(sources)}개. 독립 증거 수·사건 수가 아님.',
                 untriaged_inventory_summary='전체 증거 목록의 검토 완료율은 원장에 없음', missing_metrics=['전체 실패·미상 비용','정답 기준 의미 정확도','중요 단서 회수율'])
    state['status']['execution'], state['status']['explicit_limitations'][0] = execution_state(events)
    indicator_rows = {}
    for e in events:
        if e['kind'] == 'indicator':
            indicator_rows[e['data']['indicator_id']] = e['data']
    state['indicators'] = sorted(indicator_rows.values(), key=lambda r: (r['type'], r['canonical_value']))
    for note in notes.values():
        qid='Q-'+note['note_id']; ver=str(note['revision'])
        q=dict(id=qid,version=ver,question=note['question'],target_proposition=note['question'],answer='기존 노트 제안: '+note['answer'],assessment='undetermined',work_state='open',claim_refs=[],hypothesis_refs=[],remaining_gap_ids=[],scope=scope_text,closure_rationale=None)
        state['questions'].append(q)
        cited=[ref('source',source_map[x]) for x in note.get('evidence_ids',[]) if x in source_map]
        state['claims'].append(dict(id='F-'+note['note_id'],version=ver,title=note['question'],statement=note['answer'],assertion_kind='interpretation',status='candidate',scope=scope_text,source_refs=cited,counterevidence_refs=[],limitations=note.get('gaps',[])+note.get('critical_gaps',[])+['기존 노트는 독립 검증·채택 영수증 없음'],adoption_receipt=None,semantic_review='not_reviewed'))
        for i, explanation in enumerate(note.get('alternatives',[])):
            h=dict(id=f'H-{qid}-{i}',version=ver,question_ref=ref('question',q),explanation=explanation,trigger_refs=cited,support_refs=[],counterevidence_refs=[],assumptions=['기존 노트의 경쟁 설명; 판별 미완료'],prediction='다음 미션에서 판별 조건 설정',compatibility='양립 가능성 미평가',status='candidate')
            state['hypotheses'].append(h);q['hypothesis_refs'].append(ref('hypothesis',h))
        reasons=list(dict.fromkeys(note.get('critical_gaps',[])+note.get('gaps',[]))) or ['현재 답과 원문을 판별 조건에 따라 평가해야 함']
        for i,reason in enumerate(reasons):
            g=dict(id=f'G-{qid}-{i}',version=ver,question_ref=ref('question',q),section_targets=['E01','E02','A05','A11'],original_obligation=reason,kind='unassessed_result' if cited else 'unpresented_source',disposition='open',impact='decision_critical' if reason in note.get('critical_gaps',[]) else 'unassessed',reason=reason,basis_refs=cited,feasible_next_action='evaluate_result' if cited else 'read_source',missing_preconditions=[],reopen_conditions=['새 원문·반론·질문 범위 정정'],resolution_refs=[])
            state['gaps'].append(g);q['remaining_gap_ids'].append(g['id'])
        for i, action in enumerate(note.get('next_checks',[])):
            state['actions'].append(dict(id=f'ACT-{qid}-{i}',version=ver,action_kind='recommended',title=action,rationale=note['question'],basis_refs=cited,execution_receipt=None,owner=None,due_at=None,verification='결과와 질문 답의 실제 변화 확인'))
        for i,tm in enumerate(note.get('timeline',[])):
            origin=tm.get('time_source',{});eid=origin.get('evidence_id');field=origin.get('field')
            if field == 'log' and eid in source_events:
                try:
                    data = verified_sources(case, [eid])[0]['data']
                    raw, dt = log_time(data, origin)
                except (ValueError, KeyError, TypeError):
                    state['status']['explicit_limitations'].append('로그 시각 원문 연결 확인 필요: '+str(tm.get('description','')))
                    continue
                obs=dict(id=f'O-time-{qid}-{i}',version=ver,source_ref=ref('source',source_map[eid]),statement='로그에 기록된 시각',field_pointer=origin['pointer'],literal=raw,canonical_sha256=hashlib.sha256(raw.encode()).hexdigest(),presented_view='exact_excerpt',coordinate_basis='retained text field UTF-8',byte_start=origin['byte_start'],byte_end=origin['byte_start']+len(raw.encode()),interpretation_limit='로그에 기록된 시각; 성공·행위자·인과관계를 별도로 평가해야 함')
                state['observations'].append(obs)
                state['timeline'].append(dict(id=f'TM-{qid}-{i}',version=ver,observation_ref=ref('observation',obs),claim_refs=[],time_kind='record',shape='point' if dt else 'unknown',raw_values=[raw],normalized_values=[dt.isoformat()] if dt else [],timezone_basis='원문에 명시된 UTC offset' if dt else None,year_basis='원문 날짜' if raw[:4].isdigit() and len(raw)>4 and raw[4]=='-' else None,comparable=bool(dt),explanation=tm.get('description','로그 시각'),limitation='로그 기록의 시각이며 실행 성공·공격자 귀속은 별도 판단' if dt else '연도·시간대 또는 시각 형식 미확인; 절대시각 비교 불가',file_time_type=None))
                continue
            if eid not in source_events or field not in ('mtime','ctime','atime'):
                state['status']['explicit_limitations'].append('기존 시각 노트 원문 결속 미확인: '+str(tm.get('raw_time',tm.get('time','')))+' / '+str(tm.get('description','')))
                continue
            try:
                _, time_pointer = file_time(source_events[eid]['data'], origin)
                raw = pointer(source_events[eid]['data'], time_pointer)
            except (ValueError, KeyError, TypeError):
                state['status']['explicit_limitations'].append('기존 시각 노트의 정확한 파일·시각 필드 연결 필요: '+str(tm.get('description','')))
                continue
            obs=dict(id=f'O-time-{qid}-{i}',version=ver,source_ref=ref('source',source_map[eid]),statement='파일 메타데이터 '+field,field_pointer=time_pointer,literal=raw,canonical_sha256=hashlib.sha256(raw.encode()).hexdigest(),presented_view='metadata',coordinate_basis='retained filesystem field UTF-8',byte_start=0,byte_end=len(raw.encode()),interpretation_limit='파일 시각이며 행위 발생시각이 아님')
            state['observations'].append(obs)
            state['timeline'].append(dict(id=f'TM-{qid}-{i}',version=ver,observation_ref=ref('observation',obs),claim_refs=[],time_kind='file_metadata',shape='point',raw_values=[raw],normalized_values=[epoch_time(raw).isoformat()],timezone_basis='도구 파일 메타데이터 Unix epoch / Asia/Seoul',year_basis='Unix epoch',comparable=True,explanation=tm.get('description','파일 시각'),limitation='동일 파일 필드만 확인; 생성·실행·변경 주체를 입증하지 않음',file_time_type=field))
    if not state['questions']:
        state['questions'].append(dict(id='Q-intake',version=scope['version'],question=scope['original_question'],target_proposition=scope['original_question'],answer='근거에 결속한 질문 답이 아직 없습니다.',assessment='undetermined',work_state='open',claim_refs=[],hypothesis_refs=[],remaining_gap_ids=['G-intake'],scope=scope_text,closure_rationale=None))
        q=state['questions'][0]
        state['gaps'].append(dict(id='G-intake',version=q['version'],question_ref=ref('question',q),section_targets=['E01','A01'],original_obligation=q['question'],kind='unpresented_source',disposition='open',impact='unassessed',reason='먼저 질문·대상 범위를 정하고 관련 원문을 제시해야 함',basis_refs=[ref('scope',scope)],feasible_next_action='read_source',missing_preconditions=[],reopen_conditions=['사용자 질문·증거 제공'],resolution_refs=[]))
    definitions={q['id']:q['version'] for q in state['questions']}
    stale=[];requirements={}
    # Changes are source-bound; a note revision makes its old missions/assessments
    # historical instead of silently adopting them against a changed proposition.
    for e in events:
        if e['kind']!='report_change':continue
        d=e['data'];qref=d.get('question_ref')
        if any(definitions.get(key)!=ver for key,ver in d.get('definition_versions',{}).items()):
            stale.append(e['id']);continue
        if 'requirement' in d:
            requirements[d['requirement']['requirement_id']]=d['requirement']
        for field, records in d.get('records',{}).items():
            for record in records:
                state[field]=[r for r in state[field] if r['id']!=record['id']]+[deepcopy(record)]
        if 'answer' in d:
            q=next(q for q in state['questions'] if q['id']==qref['id'])
            q.update(deepcopy(d['answer']))
    for item in catalog():
        candidate=requirements.get(item['id'])
        state['requirements'].append(candidate or dict(requirement_id=item['id'],section_targets=item['section_targets'],applicability='applicable' if item['class']=='core' else 'conditional_unassessed',disposition='open',rationale=item['applicability_trigger']+' / '+item['sufficiency'],basis_refs=[ref('scope',scope)],gap_ids=[g['id'] for g in state['gaps'] if set(g['section_targets'])&set(item['section_targets'])]))
    if stale: state['status']['explicit_limitations'].append(f'질문 정정으로 과거 미션·평가 {len(stale)}건을 현재 판단에서 제외; 이전 원장 보존')
    state['status']['explicit_limitations'].extend(g['reason'] for g in state['gaps'] if g['impact'] in ('material','decision_critical') and g['disposition']!='resolved')
    # Mutable projection records share an exact ledger revision. This avoids cyclic
    # content hashes (question <-> gap <-> mission) while ensuring a changed answer
    # never keeps its old version. Immutable source versions remain byte hashes.
    revision=digest([(e['id'],e['data']) for e in relevant])
    fields=('questions','claims','observations','hypotheses','tests','assessments','gaps','missions','timeline','actions')
    versions={('source',r['id']):r['version'] for r in state['sources']}
    versions[('scope',scope['id'])]=scope['version']
    for field in fields:
        kind={'hypotheses':'hypothesis','observations':'observation','assessments':'assessment'}.get(field,field[:-1])
        for record in state[field]:record['version']=revision;versions[(kind,record['id'])]=revision
    for _,r in find_refs(state):
        if (r['kind'],r['id']) in versions:r['version']=versions[(r['kind'],r['id'])]
    state['timeline'].sort(key=lambda t: (not t['comparable'], datetime.fromisoformat(t['normalized_values'][0]).timestamp() if t['comparable'] and t['normalized_values'] else 0, t['raw_values'], t['id']))
    state['meta']['snapshot_id']=digest({'state':state,'revisions':[(e['id'],e['data']) for e in relevant]})
    return ReportState.model_validate(state).model_dump()


def checked(state):
    result=audit(state)
    if result['structural_issues']:raise ValueError(json.dumps(result['structural_issues'],ensure_ascii=False))
    return result


def find(state, field, key):
    return next(r for r in state[field] if r['id']==key)


def validate_refs(state, values):
    registry={(kind,r['id']):r['version'] for field,kind in (('sources','source'),('observations','observation'),('claims','claim'),('questions','question'),('hypotheses','hypothesis'),('tests','test'),('assessments','assessment'),('gaps','gap'),('missions','mission')) for r in state[field]}
    registry[('scope',state['scope']['id'])]=state['scope']['version']
    for _, r in find_refs(values):
        if registry.get((r['kind'],r['id']))!=r['version']:raise ValueError('Foreign or stale reference: '+r['id'])


def write(case, state, change):
    # All mutations in the existing activity DB, one atomic event, CAS inside lock.
    with case.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if current(case)['meta']['snapshot_id']!=state['meta']['snapshot_id']:
            raise ValueError('Report state changed; read current state before proposing')
        definitions={}
        for e in ledger(case):
            if e['kind']=='note':definitions['Q-'+e['data']['note_id']]=str(e['data']['revision'])
        owners={r['id'] for _,r in find_refs(change) if r['kind']=='question'}
        if 'Q-intake' in owners:definitions['Q-intake']=state['scope']['version']
        change={**change,'definition_versions':{key:definitions[key] for key in owners}}
        eid=uuid.uuid4().hex
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(eid,time.time(),'report_change','',json.dumps({**change,'input_snapshot':state['meta']['snapshot_id']},ensure_ascii=False)))
    return eid


def review_material(case,s,question_ids=None):
    """Selected answers and exact cited bodies, not control/projection bookkeeping."""
    selected=set(question_ids if question_ids is not None else [q['id'] for q in s['questions']])
    if not selected or selected-{q['id'] for q in s['questions']}:raise ValueError('Select current question_ids from state')
    questions=[q for q in s['questions'] if q['id'] in selected]
    claim_ids={r['id'] for q in questions for r in q['claim_refs']}
    claim_ids.update('F-'+q['id'][2:] for q in questions if q['id'].startswith('Q-'))
    draft={'scope':s['scope'],'questions':questions,
           'claims':[c for c in s['claims'] if c['id'] in claim_ids]}
    for key in ('hypotheses','gaps','missions','tests'):
        draft[key]=[r for r in s[key] if r['question_ref']['id'] in selected]
    tests={r['id'] for r in draft['tests']}
    draft['assessments']=[r for r in s['assessments'] if r['test_ref']['id'] in tests]
    observation_ids={r['id'] for _,r in find_refs(draft) if r['kind']=='observation'}
    linked_times=[r for r in s['timeline'] if r['id'].startswith(tuple('TM-'+q+'-' for q in selected))]
    observation_ids.update(r['observation_ref']['id'] for r in linked_times)
    draft['observations']=[r for r in s['observations'] if r['id'] in observation_ids]
    draft['timeline']=[r for r in s['timeline'] if r['observation_ref']['id'] in observation_ids]
    # Counterevidence may be a claim/observation in another question. Follow
    # those existing refs instead of losing its original body at a UI selection.
    record_kinds=(('claims','claim'),('observations','observation'),('hypotheses','hypothesis'),
                  ('tests','test'),('assessments','assessment'),('gaps','gap'),('missions','mission'))
    registry={(kind,r['id']):(field,r) for field,kind in record_kinds for r in s[field]}
    seen={(kind,r['id']) for field,kind in record_kinds for r in draft.get(field,[])}
    while True:
        pending={(r['kind'],r['id']) for _,r in find_refs(draft)}-seen
        pending &= registry.keys()
        if not pending:break
        for key in sorted(pending):
            field,record=registry[key];draft.setdefault(field,[]).append(record);seen.add(key)
    # Definition revisions matter, but a new Hermes turn changes all projected
    # versions without changing a question. Keep the former, omit the latter.
    definitions={}
    for e in ledger(case):
        if e['kind']=='note' and 'Q-'+e['data']['note_id'] in selected:
            definitions['Q-'+e['data']['note_id']]=e['data']['revision']
        if e['id']==s['meta']['ledger_cutoff']:break
    cited={r['id'] for _,r in find_refs(draft) if r['kind']=='source'}
    draft['indicators']=[r for r in s.get('indicators',[]) if not question_ids or any(x['source_id'] in cited for x in r['source_refs'])]
    cited.update(x['source_id'] for r in draft['indicators'] for x in r['source_refs'])
    originals=[]
    for source in s['sources']:
        if source['id'] not in cited:continue
        event=case.event(source['id']);request=case.event(event['data'].get('started_id',''))
        originals.append({'source_id':source['id'],'source_version':source['version'],
                          'result':event['data'],'request':request if request and request['kind']=='tool_start' else None})
    immutable=cited|{s['scope']['id']}
    def material(value):
        if isinstance(value,dict):return {k:material(v) for k,v in value.items() if k!='version' or value.get('id') in immutable}
        if isinstance(value,list):return [material(v) for v in value]
        return value
    return {'report_draft':material(draft),'question_ids':sorted(selected),
            'question_definition_versions':definitions,'original_results':originals,
            'omitted_question_ids':[q['id'] for q in s['questions'] if q['id'] not in selected],
            'unpresented_source_ids':[r['id'] for r in s['sources'] if r['id'] not in cited],
            'coverage':'Selected questions, alternatives and gaps only. Exact cited tool results and requests are retained without trimming; omitted sources are not reviewed.'}


def invoke(case,args):
    action=args['action'];s=current(case)
    from .model_pages import state_view, source_view
    if action in ('state','gaps'):return state_view(s,audit(s),args)
    if action=='source':
        source=find(s,'sources',args['source_id']);e=case.event(source['id'])
        return source_view(source,e['data'],args)
    if action=='publish':
        return {'published':False,'blocked':'기관의 배포 승인·수신자 권한 adapter가 아직 연결되지 않았습니다. 모델이나 상태 문자열로 승인하지 않습니다.','snapshot_id':s['meta']['snapshot_id']}
    if action=='review':
        if args.get('snapshot_id')!=s['meta']['snapshot_id']:raise ValueError('Read current snapshot_id first')
        from ..review import review_payload
        return review_payload(case,review_material(case,s,args.get('question_ids')),s['meta']['snapshot_id'])
    if action=='render':
        from .views import bundle
        return bundle(case,s,args.get('redact',[]),review_id=args.get('review_id'))
    if action=='finalize':
        if args.get('snapshot_id')!=s['meta']['snapshot_id']:raise ValueError('Read current snapshot_id first')
        from .finalization import finalize
        return finalize(case,s,args.get('bundle_id'))
    if args.get('snapshot_id')!=s['meta']['snapshot_id']:raise ValueError('Read current snapshot_id first')
    p=args.get('payload',{})
    if action=='requirement':
        r=Requirement.model_validate(p).model_dump();validate_refs(s,r)
        item=next(x for x in catalog() if x['id']==r['requirement_id'])
        if r['section_targets']!=item['section_targets']:raise ValueError('Keep the catalog section targets')
        if not r['rationale'].strip() or not r['basis_refs']:raise ValueError('Applicability needs scope/source reasons, not null or missing data')
        if r['disposition']=='scoped_unknown':
            if not r['gap_ids']:raise ValueError('Unknown requires remaining obligations')
            for gid in r['gap_ids']:
                g=find(s,'gaps',gid)
                if not g['reopen_conditions'] or not g['basis_refs']:raise ValueError('Unknown needs attempted scope, basis and reopening conditions')
        trial=deepcopy(s);trial['requirements']=[x for x in trial['requirements'] if x['requirement_id']!=r['requirement_id']]+[r];checked(trial)
        return {'receipt':write(case,s,{'requirement':r}),'requirement':r,'semantic_approval':False}
    if action=='gap':
        g=Gap.model_validate(p).model_dump();validate_refs(s,g)
        if g['disposition'] not in ('open','assessed_unresolved') or g['resolution_refs']:raise ValueError('A proposed gap cannot grant resolution')
        if not g['reason'].strip() or not g['reopen_conditions']:raise ValueError('Reason and reopening conditions required')
        trial=deepcopy(s);trial['gaps']=[r for r in trial['gaps'] if r['id']!=g['id']]+[g];checked(trial)
        return {'receipt':write(case,s,{'question_ref':g['question_ref'],'records':{'gaps':[g]}}),'gap':g}
    if action=='mission':
        m=Mission.model_validate(p).model_dump();validate_refs(s,m)
        g=find(s,'gaps',m['gap_ref']['id']);q=find(s,'questions',m['question_ref']['id'])
        if g['question_ref']!=m['question_ref'] or m['original_obligation_id']!=g['id'] or m['exact_target_scope']!=q['scope']:
            raise ValueError('Mission must retain the exact gap, question owner and approved scope')
        if m['mission_kind']!=g['feasible_next_action']:raise ValueError('Mission must address the classified gap action')
        if m['state']!='draft' or m['budget']['authority']!='unallocated' or any(m['budget'][k] is not None for k in ('model_calls','input_tokens','output_tokens','wall_seconds')):
            raise ValueError('Record a draft; budget and execution remain with Hermes and the existing tools')
        if m['mission_kind']=='evaluate_result' and not m['reuse_result_refs']:raise ValueError('Evaluate the already returned result instead of collecting again')
        if not m['inconclusive_rule'] or not m['does_not_resolve'] or not m['reopen_conditions']:raise ValueError('Keep inconclusive conditions, limits and reopening conditions')
        if m['proposed_tool_name'] and m['proposed_tool_name'] not in EVIDENCE_TOOLS:raise ValueError('Unsupported forensic tool')
        old=next((r for r in s['missions'] if r['id']==m['id']),None)
        if old:
            if definition(old)==definition(m):return {'mission':old,'reused':True}
            raise ValueError('Mission is immutable; preserve old contract and make a materially changed draft')
        for old in s['missions']:
            if definition({k:v for k,v in old.items() if k!='id'})==definition({k:v for k,v in m.items() if k!='id'}):
                return {'mission':old,'reused':True}
        return {'receipt':write(case,s,{'question_ref':m['question_ref'],'records':{'missions':[m]}}),'mission':m,'execution_authorized':False}
    if action=='assess':return assess(case,s,p)
    raise ValueError('Supported: state, gaps, source, requirement, gap, mission, assess, review, render, publish')


def assess(case,s,p):
    allowed={'mission_id','mission_version','result_id','result_version','outcome','reasoning_summary','answer','citations','limitations','next_check'}
    if set(p)-allowed:raise ValueError('Assessment proposals cannot set adoption, resolution or approval fields')
    m=find(s,'missions',p['mission_id']);g=find(s,'gaps',m['gap_ref']['id'])
    if m['version']!=p['mission_version']:raise ValueError('Stale mission')
    result=find(s,'sources',p['result_id'])
    if result['version']!=p['result_version']:raise ValueError('Stale result')
    if m['reuse_result_refs'] and ref('source',result) not in m['reuse_result_refs']:raise ValueError('Result is not owned by this mission')
    if not m['reuse_result_refs']:raise ValueError('Read/classify the actual result, then record an after-result evaluation mission')
    outcome=p['outcome']
    if outcome not in ('supports','refutes','inconclusive','found','no_match_in_scope','partial','unavailable'):raise ValueError('Invalid outcome')
    if outcome in ('supports','refutes') and not m['support_rule' if outcome=='supports' else 'refute_rule']:raise ValueError('Missing discriminating condition')
    if not p.get('reasoning_summary') or not p.get('answer') or not p.get('limitations') or not p.get('next_check'):
        raise ValueError('Assessment requires answer, reasoning, limitations and next/reopening check')
    data=case.event(result['id'])['data'];observations=[]
    for c in p.get('citations',[]):
        src=find(s,'sources',c['source_id'])
        if src['version']!=c['source_version']:raise ValueError('Stale citation')
        allowed_sources={r['id'] for r in m['reuse_result_refs']+m['input_refs']+m['preserved_counterevidence_refs'] if r['kind']=='source'}
        if src['id'] not in allowed_sources:raise ValueError('Citation was not part of the mission input')
        raw=pointer(case.event(src['id'])['data'],c['pointer']);literal=c['literal'];start=int(c['byte_start']);end=start+len(literal.encode())
        if start<0 or not literal or raw.encode()[start:end]!=literal.encode():raise ValueError('Literal is not the exact retained UTF-8 field span')
        # Quoting the value of /path or /scope is metadata, not body access.
        body=('/text' in c['pointer'] or c['pointer']=='/stdout')
        presented='full_field' if start==0 and end==len(raw.encode()) else 'exact_excerpt'
        if not body:presented='metadata'
        if m['required_view']=='full_field' and presented!='full_field':raise ValueError('Mission requires a complete original body field, not metadata or a fragment')
        ob=dict(id='O-'+digest(c)[:20],version=src['version'],source_ref=ref('source',src),statement='원문 필드에 기록된 문자열',field_pointer=c['pointer'],literal=literal,canonical_sha256=hashlib.sha256(literal.encode()).hexdigest(),presented_view=presented,coordinate_basis='retained tool-result scalar UTF-8 bytes; not original image offset',byte_start=start,byte_end=end,interpretation_limit='이 필드·제시 구간의 기록만 확인; 성공·승인·귀속·전체 부재는 별도 판별')
        observations.append(ob)
    if outcome in ('supports','refutes','found') and not observations:raise ValueError('Substantive outcomes require exact source observations')
    if data.get('error') or data.get('exit_code',0)!=0:
        if outcome!='unavailable':raise ValueError('Failed tool result is unavailable, not a negative forensic result')
    ident=digest({'mission':definition(m),'proposal':{k:v for k,v in p.items() if k!='mission_version'}})[:20]
    existing=next((a for a in s['assessments'] if a['id']=='AS-'+ident),None)
    if existing:return {'assessment':existing,'reused':True}
    tid='T-'+m['id'];test=dict(id=tid,version=result['version'],question_ref=m['question_ref'],target_proposition=m['target_proposition'],purpose='discriminate' if m['support_rule'] or m['refute_rule'] else 'discover',immediate_observable=m['capability_requirement'],input_refs=m['input_refs'],required_view=m['required_view'],tool_capability='retained_result_read',target_scope=m['exact_target_scope'],support_rule=m['support_rule'],refute_rule=m['refute_rule'],inconclusive_rule=m['inconclusive_rule'],physical_job_ref=data.get('started_id'),result_ref=ref('source',result),result_scope=result['coverage'],execution_state='returned' if not data.get('error') else 'failed',assessment_state='assessed',design_timing=m['design_timing'])
    if test['purpose']=='discover' and outcome in ('supports','refutes'):raise ValueError('Discovery is not hypothesis discrimination')
    receipt='report-adoption-'+ident
    assessment=dict(id='AS-'+ident,version='1',test_ref=ref('test',test),result_ref=ref('source',result),observation_refs=[ref('observation',o) for o in observations],outcome=outcome,reasoning_summary=p['reasoning_summary'],validation_receipt=receipt,adoption_receipt=receipt,resolved_gap_ids=[],remaining_gap_ids=[g['id']])
    claim=dict(id='F-'+m['id'],version=ident,title=m['target_proposition'],statement=p['answer'],assertion_kind='interpretation',status='adopted',scope=m['exact_target_scope'],source_refs=[ref('observation',o) for o in observations] or [ref('source',result)],counterevidence_refs=m['preserved_counterevidence_refs'],limitations=p['limitations']+m['does_not_resolve']+['근거 결속을 확인한 모델 해석; 독립 의미 검토 미완료'],adoption_receipt=receipt,semantic_review='not_reviewed')
    # Completing an evaluation is not resolving the underlying forensic obligation.
    gap={**g,'disposition':'assessed_unresolved','reason':p['reasoning_summary'],'feasible_next_action':'design_test','reopen_conditions':[p['next_check']]+g['reopen_conditions']}
    mission={**m,'state':'partial'}
    answer=dict(answer=p['answer'],assessment={'supports':'supported','refutes':'refuted'}.get(outcome,'undetermined'),work_state='open',claim_refs=[ref('claim',claim)],remaining_gap_ids=list(dict.fromkeys(find(s,'questions',m['question_ref']['id'])['remaining_gap_ids']+[g['id']])))
    records=dict(tests=[test],observations=observations,assessments=[assessment],claims=[claim],gaps=[gap],missions=[mission])
    trial=deepcopy(s)
    for field,rows in records.items():trial[field]=[x for x in trial[field] if x['id'] not in {r['id'] for r in rows}]+rows
    find(trial,'questions',m['question_ref']['id']).update(answer);checked(trial)
    eid=write(case,s,dict(question_ref=m['question_ref'],records=records,answer=answer,validation={'receipt':receipt,'checks':['case/owner/version','canonical field/span','purpose/outcome','counterevidence retained'],'semantic_approval':False}))
    return {'receipt':eid,'assessment':assessment,'question_update':answer,'original_obligation_resolved':False}
