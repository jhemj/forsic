"""Bounded read-only UI projection of explicitly owned, retained investigation records."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import heapq
import json
import re
import sqlite3
from urllib.parse import quote, urlencode

from .notes import EVIDENCE_TOOLS
from .report_driven.host import content_version, source_record

MAX_QUESTIONS = 40
MAX_SOURCES = 120
MAX_OBSERVATIONS = 120
MAX_TIMES = 120
MAX_REPORTS = 20
MAX_SESSIONS = 80
MAX_REVIEW_QUESTIONS = 80
MAX_REVIEW_RECEIPTS = 24
MAX_RECORD_BYTES = 128 * 1024
MAX_SOURCE_BYTES = 1024 * 1024
MAX_VIEW_BYTES = 256 * 1024


def resolve_view(session_id, profile):
    """An explicit profile and real native session are required; no global case fallback."""
    if not session_id.strip() or not profile.strip() or profile.lower() == 'current':
        raise ValueError('대화와 프로필을 명시해서 조사 기록을 조회하세요.')
    from hermes_cli.profiles import validate_profile_name, profile_exists, get_profile_dir
    from .intake import Intake
    from .investigation_context import resolve_context
    from .tenants import resolve_request, bind_scope, native_db
    scope = resolve_request(profile_name=profile, session_id=session_id)
    if scope:
        with bind_scope(scope):
            resolved = resolve_context(session_id, intake_root=scope['intake_root'], native_db=native_db(scope))
        if resolved is None:
            raise LookupError('이 대화에 연결된 사건이 없습니다.')
        resolved['_view_native_db'] = native_db(scope)
        return resolved
    import hermes_yaml as yaml
    validate_profile_name(profile)
    if not profile_exists(profile):
        raise LookupError('프로필을 찾을 수 없습니다.')
    home = get_profile_dir(profile).resolve()
    native = home / 'state.db'
    if not native.is_file():
        raise LookupError('저장된 대화를 찾을 수 없습니다.')
    with closing(sqlite3.connect(native.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        columns = {r[1] for r in db.execute('PRAGMA table_info(sessions)')}
        fields = 'id,profile_name' if 'profile_name' in columns else 'id'
        row = db.execute('SELECT ' + fields + ' FROM sessions WHERE id=?', (session_id,)).fetchone()
        if row is None or len(row) > 1 and row[1] not in (None, '', profile):
            raise LookupError('이 프로필에 연결된 대화가 없습니다.')
    config_file = home / 'config.yaml'
    try:
        config = yaml.safe_load(config_file.read_text()) if config_file.is_file() else {}
    except yaml.YAMLError as exc:
        raise ValueError('프로필 설정을 읽을 수 없습니다.') from exc
    settings = ((config or {}).get('plugins', {}).get('entries', {}).get('forsic', {}).get('settings') or {})
    resolved = resolve_context(session_id, intake_root=Intake(settings.get('intake_root')).root, native_db=native)
    if resolved is None:
        raise LookupError('이 대화에 연결된 사건이 없습니다.')
    resolved['_view_native_db'] = native
    return resolved


def _connection(case):
    db = sqlite3.connect(case.db.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')  # one retained snapshot for counts, records and versions
    db.create_function('record_version', 1, lambda value:
        content_version(json.loads(value)) if len(value.encode()) <= MAX_RECORD_BYTES else '')
    def time_observation_id(question_id,basis):
        key = json.dumps({'question_id':question_id,'basis':json.loads(basis)},
                         ensure_ascii=False,sort_keys=True,separators=(',', ':'))
        return 'O-time-' + hashlib.sha256(key.encode()).hexdigest()[:24]
    db.create_function('time_observation_id',2,time_observation_id)
    return db


def _queries(resolved):
    # The explicitly resolved case ledger is the boundary. Choose the latest
    # note before projecting provenance; earlier ownership must never reappear.
    sql = """WITH ranked_notes AS (
      SELECT rowid,data,row_number() OVER (
        PARTITION BY json_extract(data,'$.note_id') ORDER BY rowid DESC) AS rank
      FROM events WHERE kind='note'), owned AS (
      SELECT rowid,data FROM ranked_notes WHERE rank=1), changes AS (
      SELECT e.rowid,e.data FROM events e JOIN owned n
        ON json_extract(e.data,'$.question_ref.id')='Q-'||json_extract(n.data,'$.note_id')
      WHERE e.kind='report_change' AND (
        json_extract(e.data,'$.definition_versions.'||'"Q-'||json_extract(n.data,'$.note_id')||'"')
          = json_extract(n.data,'$.definition_version') OR (
        json_extract(e.data,'$.definition_versions') IS NULL
        AND json_extract(e.data,'$.question_ref.version')=cast(json_extract(n.data,'$.revision') AS TEXT)))),
      ranked_claims AS (
      SELECT c.rowid,r.value AS value,json_extract(c.data,'$.question_ref.id') AS qid,row_number() OVER (
        PARTITION BY json_extract(r.value,'$.id') ORDER BY c.rowid DESC) AS rank
      FROM changes c,json_each(c.data,'$.records.claims') r), claims AS (
      SELECT DISTINCT c.rowid,c.value,c.qid FROM ranked_claims c,owned n,
        json_each(n.data,'$.investigation.claim_refs') selected
      WHERE c.rank=1 AND c.qid='Q-'||json_extract(n.data,'$.note_id')
        AND json_extract(c.value,'$.id')=json_extract(selected.value,'$.id')
        AND json_extract(selected.value,'$.version') IN (
          json_extract(c.value,'$.version'),record_version(c.value))
        AND json_extract(c.value,'$.status') NOT IN ('retracted','superseded')),
      ranked_times AS (
      SELECT c.rowid,r.value AS value,json_extract(n.data,'$.session_id') AS session_id,row_number() OVER (
        PARTITION BY json_extract(r.value,'$.id') ORDER BY c.rowid DESC) AS rank
      FROM changes c,json_each(c.data,'$.records.timeline') r,owned n
      WHERE json_extract(c.data,'$.question_ref.id')='Q-'||json_extract(n.data,'$.note_id')
        AND (json_extract(c.data,'$.question_ref.version')=cast(json_extract(n.data,'$.revision') AS TEXT)
             OR c.rowid>=n.rowid)),
      saved_times AS (SELECT rowid,value,session_id FROM ranked_times WHERE rank=1),
      note_times AS (
      SELECT 'Q-'||json_extract(n.data,'$.note_id') AS qid,t.value,t.key AS position
      FROM owned n,json_each(n.data,'$.timeline') t),
      time_basis AS (
      SELECT qid,b.value AS basis FROM note_times t,json_each(t.value,'$.temporal.basis_refs') b
      UNION SELECT qid,json_extract(value,'$.literal_timestamp_ref') FROM note_times
        WHERE json_type(value,'$.literal_timestamp_ref')='object'
      UNION SELECT qid,b.value FROM note_times t,json_each(t.value,'$.temporal.relations') r,
        json_each(r.value,'$.basis_refs') b),
      observation_refs AS (
      SELECT json_extract(r.value,'$.id') AS id,json_extract(r.value,'$.version') AS version,c.rowid AS cutoff
        FROM claims c,json_each(c.value,'$.source_refs') r WHERE json_extract(r.value,'$.kind')='observation'
      UNION SELECT json_extract(r.value,'$.id'),json_extract(r.value,'$.version'),c.rowid
        FROM claims c,json_each(c.value,'$.counterevidence_refs') r WHERE json_extract(r.value,'$.kind')='observation'
      UNION SELECT json_extract(value,'$.observation_ref.id'),json_extract(value,'$.observation_ref.version'),rowid FROM saved_times
      UNION SELECT json_extract(r.value,'$.id'),json_extract(r.value,'$.version'),t.rowid FROM saved_times t,
        json_each(t.value,'$.temporal_context.observation_refs') r),
      observation_records AS (
      SELECT c.rowid,r.value AS value FROM changes c,json_each(c.data,'$.records.observations') r),
      observations AS (
      SELECT DISTINCT (SELECT o.value FROM observation_records o
        WHERE json_extract(o.value,'$.id')=selected.id AND o.rowid<=selected.cutoff
          AND selected.version IN (json_extract(o.value,'$.version'),record_version(o.value))
        ORDER BY o.rowid DESC LIMIT 1) AS value FROM observation_refs selected),
      source_ids AS (
      SELECT r.value AS id FROM owned n,json_each(n.data,'$.evidence_ids') r
      UNION SELECT json_extract(value,'$.source_ref.id') FROM observations WHERE value IS NOT NULL)
    """
    return sql, []


def _recheck_relations(db, assertion, targets):
    """Revalidate only bounded retained target-note metadata, within this SQL snapshot."""
    from .temporal import MAX_EVENTS, event_identity
    context = assertion.setdefault('temporal_context',{})
    relations = context.get('temporal_relations', (assertion.get('temporal') or {}).get('relations',[]))
    stale = False
    for relation in relations:
        target_id = relation.get('target_note_id')
        if not target_id:
            continue
        if target_id not in targets:
            target = None
            if len(targets) < MAX_TIMES * 16:
                row = db.execute("SELECT json_extract(data,'$.revision') AS revision, "
                    "CASE WHEN length(cast(json_extract(data,'$.timeline') AS BLOB))<=? "
                    "THEN json_extract(data,'$.timeline') END AS timeline FROM events "
                    "WHERE kind='note' AND json_extract(data,'$.note_id')=? ORDER BY rowid DESC LIMIT 1",
                    (MAX_RECORD_BYTES,target_id)).fetchone()
                if row and row['timeline'] is not None:
                    timeline = json.loads(row['timeline'])
                    if isinstance(timeline,list) and len(timeline) <= MAX_EVENTS:
                        events = []
                        for item in timeline:
                            if item.get('time_source') or item.get('temporal') or item.get('event_ref'):
                                try:
                                    events.append(event_identity(item))
                                except ValueError:
                                    continue
                        target = (row['revision'],events)
                targets[target_id] = target
        target = targets.get(target_id)
        if (target is None or target[0] != relation.get('target_note_revision')
                or target[1].count(relation.get('target_event_ref')) != 1):
            stale = True
            context.setdefault('conflicting_constraints',[]).append(dict(
                event_refs=[assertion.get('event_ref',assertion['id'])],
                relation_refs=[relation.get('relation_ref','')],
                reason='temporal_relation_target_changed',requires_recheck=True))
    if stale:
        context['temporal_requires_recheck'] = True
        assertion.update(comparable=False,normalized_values=[])
        assertion['limitation'] = assertion.get('limitation','') + ' · 시간 관계의 대상 노트 버전·사건 연결 재확인 필요'


def _strings(value):
    return [item for item in value if isinstance(item,str)] if isinstance(value,list) else []


def _question_details(db, prefix, params, note, question, observation_versions=None):
    """Expose declared analysis only; missing fields are not reviewed-empty lists."""
    for field, stored in [('counterarguments','alternatives'), ('next_checks','next_checks')]:
        if stored in note:
            question[field] = _strings(note[stored])
    if 'gaps' in note or 'critical_gaps' in note:
        question['gaps'] = list(dict.fromkeys(_strings(note.get('critical_gaps'))+_strings(note.get('gaps'))))
    limitations = _strings(note.get('limitations'))
    declared = 'limitations' in note
    explanation_claims = []
    for row in db.execute(prefix + 'SELECT value FROM claims WHERE qid=? '
                          'AND length(cast(value AS BLOB))<=? LIMIT ?',
                          [*params, question['id'], MAX_RECORD_BYTES, MAX_QUESTIONS]):
        claim = json.loads(row[0])
        for reference in [*claim.get('source_refs', []), *claim.get('counterevidence_refs', [])]:
            if reference.get('kind') == 'observation':
                reference['version'] = (observation_versions or {}).get((reference['id'], reference['version']), reference['version'])
        explanation_claims.append(claim)
        declared = declared or 'limitations' in claim
        limitations.extend(_strings(claim.get('limitations')))
    from .report_driven.reader_explanation import project as reader_explanation
    question['reader_explanation'] = reader_explanation(note, explanation_claims)
    if declared:
        question['limitations'] = list(dict.fromkeys(limitations))


def _sessions(db, result, counts):
    # Retained origins only: presence does not assert that a native session still
    # exists or remains bound to this case, and must not enable a resume link.
    query = """WITH session_ids AS (
      SELECT session AS id FROM events WHERE session<>''
      UNION SELECT json_extract(data,'$.session_id') FROM events WHERE kind='note')
      SELECT id FROM session_ids WHERE typeof(id)='text' AND id<>''"""
    counts['sessions'] = db.execute('SELECT count(*) FROM ('+query+')').fetchone()[0]
    result['sessions'] = [dict(id=row[0],label='보존된 대화 '+row[0],available=False) for row in
                          db.execute(query+' ORDER BY id LIMIT ?', (MAX_SESSIONS,))]


def _report_created_at(value):
    """Return an explicit ISO instant; do not invent zones for naive dates."""
    try:
        if isinstance(value,bool):
            return None
        if isinstance(value,(int,float)):
            instant = datetime.fromtimestamp(value,timezone.utc)
        elif isinstance(value,str):
            instant = datetime.fromisoformat(value)
            if instant.tzinfo is None:
                return None
        else:
            return None
        return instant.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
    except (ValueError,OverflowError,OSError):
        return None


def _report_current_basis(case):
    """Match the native report projection only when its full input fits a cap.

    Metadata-only event bodies are omitted by the same contract as current().
    Large histories remain unknown, rather than turning a bounded sidebar read
    into a full evidence-ledger read or declaring a partial projection current.
    """
    ignored=('pre_api_request','post_api_request','pre_auxiliary_call','post_auxiliary_call',
             'api_request_error','turn_start','turn_complete','intake','goal_started',
             'mutation_receipt','report_finalized')
    projected='CASE WHEN kind IN ('+','.join('?' for _ in ignored)+") AND json_valid(data) AND json_type(data)='object' THEN '{}' ELSE data END"
    from .report_driven.host import current
    with closing(sqlite3.connect(case.db.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        if db.execute('SELECT count(*) FROM events').fetchone()[0]>20000:return None
        size=db.execute('SELECT coalesce(sum(length(cast(('+projected+') AS BLOB))),0) FROM events',ignored).fetchone()[0]
        if size>4*1024*1024:return None
        events=[]
        for row in db.execute('SELECT rowid,id,time,kind,session,'+projected+' AS data FROM events ORDER BY rowid',ignored):
            event=dict(row);event['data']=json.loads(event['data']);events.append(event)
        return current(case,events=events)


def _report_changes(saved, now, manifest):
    """Compare authored judgments and exact selected evidence, never JSON diffs."""
    chosen=set(manifest.get('question_ids',[])) if manifest.get('selection_mode')=='selected_questions' else None
    before={q['id']:q for q in saved['questions'] if chosen is None or q['id'] in chosen}
    after={q['id']:q for q in now['questions'] if chosen is None or q['id'] in chosen}
    def material(state,question):
        claims={c['id']:c for c in state['claims']}
        selected=[claims[r['id']] for r in question.get('claim_refs',[]) if r['id'] in claims]
        answer=[question.get(k) for k in ('question','answer','scope','assessment')]
        answer.extend([{k:c.get(k) for k in ('statement','assertion_kind','inference_strength','reasoning_summary')} for c in selected])
        sources={s['id']:s.get('version') for s in state['sources']}
        evidence=[('source',sid,sources.get(sid)) for sid in question.get('evidence_ids',[])]
        evidence.extend((r.get('kind'),r.get('id'),r.get('version')) for c in selected
                        for r in [*c.get('source_refs',[]),*c.get('counterevidence_refs',[])])
        limits=[question.get(k) for k in ('scope','deferred_reason','remaining_gap_ids')]
        explanation=question.get('reader_explanation') or {}
        limits.append(explanation.get('limitations',[]))
        limits.extend([{k:c.get(k) for k in ('limitations','assumptions')} for c in selected])
        explanation_text=({k:explanation.get(k) for k in ('status','title','paragraphs')}
                          if explanation.get('paragraphs') else None)
        return answer,sorted(set(evidence),key=str),limits,explanation_text
    changes=[]
    for key in sorted(set(before)|set(after)):
        question=after.get(key) or before[key]
        if key not in before:kinds=['added']
        elif key not in after:kinds=['removed']
        else:
            old=material(saved,before[key]);new=material(now,after[key])
            kinds=[kind for kind,left,right in zip(('answer','evidence','limits','explanation'),old,new) if left!=right]
        if kinds:changes.append(dict(question_id=key,title=question.get('reader_explanation',{}).get('title') or question['question'],kinds=kinds))
    return changes[:MAX_QUESTIONS],max(0,len(changes)-MAX_QUESTIONS)


def _reports(case, case_id, result, counts, *, current_basis=None):
    """Bound metadata reads; build local case-bound URLs without trusting saved URLs."""
    candidates = []
    counts['reports'] = 0
    for path in case.output.iterdir():
        if path.is_symlink():
            continue
        bundle = bool(re.fullmatch(r'report-bundle-[a-f0-9]{64}',path.name)) and path.is_dir()
        legacy = (path.name.startswith('report-') and path.suffix=='.md' and path.is_file())
        downloadable = bool(re.fullmatch(r'report-[a-f0-9]{16}-(executive|analyst)\.(html|docx)',path.name)) and path.is_file()
        if not (bundle or legacy or downloadable):
            continue
        counts['reports'] += 1
        candidate = (path.stat().st_mtime_ns,path.name,bundle)
        if MAX_REPORTS > 0:
            heapq.heappush(candidates,candidate)
            if len(candidates)>MAX_REPORTS:
                heapq.heappop(candidates)
    query = '?'+urlencode({'case_id':case_id})
    current_basis=[] if current_basis is None else [current_basis]
    def basis():
        if not current_basis:
            try:current_basis.append(_report_current_basis(case))
            except (OSError,ValueError,KeyError,TypeError,AttributeError,sqlite3.Error):current_basis.append(None)
        return current_basis[0]
    allowed = {'integrated.html','integrated.docx','executive.html','executive.docx','practitioner.html','practitioner.docx','selected-evidence.zip'}
    for _,name,bundle in sorted(candidates,reverse=True):
        path = case.output/name
        if bundle:
            manifest_path = path/'manifest.json'
            if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size>MAX_RECORD_BYTES:
                continue
            try:
                manifest = json.loads(manifest_path.read_text())
            except (OSError,ValueError):
                continue
            ident = name.removeprefix('report-bundle-')
            if not isinstance(manifest,dict) or manifest.get('case_id')!=case_id or manifest.get('bundle_id')!=ident:
                continue
            files = []
            entries = manifest.get('files',[])
            if not isinstance(entries,list):
                continue
            for entry in entries[:16]:
                filename = entry.get('name') if isinstance(entry,dict) else None
                if not isinstance(filename,str) or filename not in allowed or (path/filename).is_symlink() or not (path/filename).is_file():
                    continue
                files.append(dict(name=filename,url='/api/plugins/forsic/report-bundles/'+ident+'/'+filename+query))
            if not files:
                continue
            fallback = ('정보시스템 침해사고 조사 결과' if any(f['name'].startswith('integrated.') for f in files)
                        else '조사 보고서')
            item = dict(id=ident,name=name,title=str(manifest.get('title') or fallback)[:160],files=files,scope='case')
            lazy = manifest.get('lazy_word') or {}
            if (manifest.get('document_formats') == 'html-with-lazy-word-v1'
                    and isinstance(lazy, dict)
                    and lazy.get('schema') == 'forsic-word-export-1' and lazy.get('status') == 'available'
                    and re.fullmatch('[a-f0-9]{64}', ident)):
                from .report_driven.host import digest
                if (manifest.get('manifest_identity') == 'sha256-v1'
                        and digest({k:v for k,v in manifest.items() if k != 'bundle_id'}) == ident):
                    item['word_export'] = dict(name='integrated.docx', method='POST',
                        url='/api/plugins/forsic/report-bundles/'+ident+'/word'+query)
            if manifest.get('editorial_status') in ('authored_current','technical_partial'):
                item['editorial_status']=manifest['editorial_status']
            created_at = _report_created_at(manifest.get('created_at'))
            if created_at is None and 'created_at' not in manifest:
                created_at=_report_created_at(path.stat().st_mtime)
                item['timestamp_basis']='saved_bundle_directory'
            if created_at is not None:
                item['created_at'] = created_at
            item['comparison_status']='unavailable'
            snapshot_id=manifest.get('snapshot_id')
            if isinstance(snapshot_id,str) and re.fullmatch('[a-f0-9]{64}',snapshot_id):
                item['snapshot_id']=snapshot_id
                saved_path=case.output/('report-state-'+snapshot_id+'.json')
                try:
                    if saved_path.is_symlink() or saved_path.stat().st_size>4*1024*1024:raise ValueError('Unbounded report snapshot')
                    raw=saved_path.read_bytes()
                    if hashlib.sha256(raw).hexdigest()!=manifest.get('state_sha256'):raise ValueError('Unverified report snapshot')
                    saved=json.loads(raw)
                    if saved['meta']['case_id']!=case_id or saved['meta']['snapshot_id']!=snapshot_id:raise ValueError('Foreign report snapshot')
                    now=basis()
                    if now is not None:
                        from .report_driven.views import bundle_stale
                        stale=bundle_stale(case,manifest,now)
                        changes,omitted=_report_changes(saved,now,manifest)
                        item.update(current=not stale,comparison_status='changed' if stale else 'current',
                                    changes=changes,omitted_changes=omitted)
                except (OSError,ValueError,KeyError,TypeError):pass
        else:
            route = 'reports' if path.suffix=='.md' else 'download'
            item = dict(id=name,name=name,title=name,scope='case',files=[dict(name=name,
                        url='/api/plugins/forsic/'+route+'/'+quote(name,safe='')+query)])
        result['reports'].append(item)


def _review_questions(resolved, notes, result, counts):
    """Bounded case-local advice receipts; these never become main-owned questions."""
    from . import independent_review as review
    from .independent_review_state import _presented, _presented_handles
    from .report_driven.investigation_state import definition_version
    from .report_driven.review_responses import validate_saved
    from .investigation_context import _compression_link
    case = resolved['case']
    lineages = {}
    def conversation(session_id):
        if not isinstance(session_id,str) or not session_id:
            return None
        if session_id in lineages:
            return lineages[session_id]
        native = resolved.get('_view_native_db')
        if native is None:
            # Direct internal projections can verify only their resolved lineage.
            lineage = resolved['session_lineage']
            value = (lineage[-1],lineage) if session_id in lineage else None
        else:
            value = None
            with closing(sqlite3.connect(native.as_uri()+'?mode=ro',uri=True)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                chain = []
                current = session_id
                while current and len(chain) < MAX_SESSIONS:
                    if current in {entry['id'] for entry in chain}:
                        break
                    row = db.execute('SELECT id,parent_session_id,end_reason,source,model_config '
                        'FROM sessions WHERE id=?',(current,)).fetchone()
                    if row is None:
                        break
                    entry = dict(row)
                    if chain and not _compression_link(chain[-1],entry):
                        value = (chain[-1]['id'],[entry['id'] for entry in chain])
                        break
                    chain.append(entry)
                    current = entry.get('parent_session_id')
                    if not current:
                        value = (chain[-1]['id'],[entry['id'] for entry in chain])
        lineages[session_id] = value
        return value
    candidates = []
    receipt_count = 0
    for path in case.output.glob('independent-review-*-request.json'):
        if path.is_symlink() or not path.is_file():
            continue
        receipt_count += 1
        heapq.heappush(candidates,(path.stat().st_mtime_ns,path.name))
        if len(candidates) > MAX_REVIEW_RECEIPTS:
            heapq.heappop(candidates)
    dispositions = {}
    for note in notes:
        try:
            owner = conversation(note.get('session_id'))
            if not owner or not note.get('goal_id'):
                continue
            question = dict(note, id='Q-'+note['note_id'],
                definition_version=definition_version(note,case.config.get('scope','')))
            for response in (note.get('investigation') or {}).get('review_responses',[]):
                try:
                    validated = validate_saved(case,None,response,lineage=owner[1],question=question)
                    # notes are newest first: retain the newest valid current note.
                    dispositions.setdefault((owner[0],note['goal_id'],validated['review_id'],validated['finding_id']),dict(
                        question_id=question['id'],revision=note.get('revision'),
                        disposition=validated['disposition'],reason=validated['reason'],
                        limitation=validated.get('limitation'),citation_refs=validated['citation_refs']))
                except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
                    continue
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
            continue
    invalid = 0
    for _, name in sorted(candidates):
        try:
            path = case.output/name
            request = review._read(path)
            identity = request.get('identity') or {}
            if (identity.get('case_id') != resolved['case_id'] or
                    identity.get('case_manifest') != str(case.manifest)):
                continue
            if request.get('schema_version') not in (2,3):
                invalid += 1; continue
            response = review._response_for(path,request)
            if not response or response.get('finish_reason') != 'stop':
                continue
            body = json.loads(next(message['content'] for message in request['messages']
                                   if message.get('role') == 'user'))
            rejected = []
            findings = review._parse(response['raw_advice'],_presented(request),request['request_id'],rejected,
                review_refs=_presented_handles(request),executive_round=request.get('executive_round',0),
                previous_questions=body.get('executive_followup',{}).get('previous_questions',()))
            invalid += len(rejected)
            for finding in findings:
                answer = None
                owner = conversation(request.get('session_id'))
                if owner and identity.get('conversation_id') == owner[0]:
                    answer = dispositions.get((owner[0],identity.get('goal_id'),finding['review_id'],finding['finding_id']))
                allowed = ('review_id','finding_id','question','quote_validation','impact','source_id','pointer',
                           'source_version','byte_start','byte_end','review_ref','quote','retrieval','kind')
                item = {key:finding[key] for key in allowed if key in finding}
                item.update(id=finding['finding_id'], role='independent_advice_not_source_evidence',
                    disposition=answer['disposition'] if answer else 'not_recorded',
                    scope='case',goal_id=identity.get('goal_id'),
                    current_goal=bool(identity.get('goal_id')) and identity.get('goal_id') == resolved.get('goal_id'),
                    currentness='not_revalidated',
                    disposition_validation='validated_saved_response' if answer else 'unverified_or_not_recorded',
                    semantic_approval=False)
                if isinstance(identity.get('conversation_id'),str):
                    item['session_id'] = identity['conversation_id']
                if answer:
                    item['question_id'] = answer['question_id']
                    item['response'] = answer
                result['review_questions'].append(item)
        except (OSError,ValueError,KeyError,TypeError,StopIteration,sqlite3.Error):
            invalid += 1
    counts['review_questions'] = len(result['review_questions'])
    result['review_question_coverage'] = dict(scope='case',receipt_count=receipt_count,
        scanned_receipts=len(candidates),omitted_receipts=max(0,receipt_count-len(candidates)),
        invalid_or_unavailable=invalid,currentness='not_revalidated',
        current_goal_questions=sum(item['current_goal'] for item in result['review_questions']),
        historical_goal_questions=sum(not item['current_goal'] for item in result['review_questions']),
        count_basis='validated advice questions in bounded retained receipts; separate from main questions')


def project_view(resolved, *, profile=''):
    """Read bounded selected bodies, never the full ledger or original evidence."""
    from .report_driven.time_projection import project as project_time
    from .investigation_display import source_display, observation_display, timestamp_projection
    case = resolved['case']
    prefix, params = _queries(resolved)
    counts = {}
    result = dict(case_id=resolved['case_id'], session_id=resolved['requested_session_id'], profile=profile,
                  scope='case', questions=[], sources=[], observations=[], timeline=[],
                  reports=[], sessions=[], review_questions=[])
    with closing(_connection(case)) as db:
        for key, query in {
            'questions': 'SELECT count(*) FROM owned',
            'observations': 'SELECT count(*) FROM observations WHERE value IS NOT NULL',
            'sources': 'SELECT count(*) FROM source_ids',
            'timeline': """SELECT count(*) FROM (
              SELECT 'TM-'||qid||'-'||coalesce(json_extract(value,'$.event_ref'),position) AS id FROM note_times
              UNION SELECT json_extract(value,'$.id') FROM saved_times)""",
        }.items():
            counts[key] = db.execute(prefix + query, params).fetchone()[0]
        selected_claims = db.execute(prefix +
            "SELECT count(*) FROM owned n,json_each(n.data,'$.investigation.claim_refs')", params).fetchone()[0]
        available_claims = db.execute(prefix + 'SELECT count(*) FROM claims', params).fetchone()[0]
        missing_observations = db.execute(prefix + """SELECT count(*) FROM (
            SELECT DISTINCT selected.id,selected.version FROM observation_refs selected
            WHERE selected.id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM observation_records o WHERE json_extract(o.value,'$.id')=selected.id
                AND o.rowid<=selected.cutoff AND selected.version IN (
                  json_extract(o.value,'$.version'),record_version(o.value))))""", params).fetchone()[0]
        missing_references = max(0,selected_claims-available_claims) + missing_observations
        counts['observations'] += db.execute(prefix + """SELECT count(*) FROM time_basis t
            WHERE time_observation_id(t.qid,t.basis) NOT IN (
              SELECT json_extract(value,'$.id') FROM observations WHERE value IS NOT NULL)""", params).fetchone()[0]
        notes = [json.loads(r['data']) for r in db.execute(prefix +
            'SELECT data FROM owned WHERE length(cast(data AS BLOB))<=? ORDER BY rowid DESC LIMIT ?',
            [*params, MAX_RECORD_BYTES, MAX_QUESTIONS])]
        # Canonical source versions require the exact retained JSON body. Only a
        # capped selected set is decoded; unrelated tool/API bodies are untouched.
        source_rows = db.execute(prefix + """SELECT e.id,e.data,e.session FROM events e
            JOIN source_ids s ON e.id=s.id WHERE e.kind='tool_result'
            AND length(cast(e.data AS BLOB))<=? ORDER BY e.rowid DESC LIMIT ?""",
            [*params, MAX_SOURCE_BYTES, MAX_SOURCES])
        source_bodies = {}
        for row in source_rows:
            data = json.loads(row['data'])
            if data.get('tool') in EVIDENCE_TOOLS:
                source = source_record({'id':row['id'], 'data':data})
                if row['session']:
                    source['session_id'] = row['session']
                start = db.execute("SELECT data FROM events WHERE kind='tool_start' AND id=? "
                    "AND length(cast(data AS BLOB))<=?", (data.get('started_id',''),MAX_RECORD_BYTES)).fetchone()
                source.update(source_display(source,data,json.loads(start[0]) if start else None))
                source_bodies[source['id']] = data
                result['sources'].append(source)
        sources = {r['id']:r for r in result['sources']}
        relation_targets = {}
        observation_versions = {}
        for row in db.execute(prefix +
                'SELECT value FROM observations WHERE value IS NOT NULL AND length(cast(value AS BLOB))<=? LIMIT ?',
                [*params, MAX_RECORD_BYTES, MAX_OBSERVATIONS]):
            ob = json.loads(row[0]); source = sources.get(ob['source_ref']['id'])
            if source and ob['source_ref']['version'] == source['version']:
                original_version = ob['version']
                ob['version'] = content_version(ob)
                observation_versions[(ob['id'],original_version)] = ob['version']
                observation_versions[(ob['id'],ob['version'])] = ob['version']
                if source.get('session_id'):
                    ob['session_id'] = source['session_id']
                result['observations'].append(ob)
        for row in db.execute(prefix +
                'SELECT value,session_id FROM saved_times WHERE length(cast(value AS BLOB))<=? LIMIT ?',
                [*params, MAX_RECORD_BYTES, MAX_TIMES]):
            assertion = json.loads(row[0])
            primary = assertion.get('observation_ref')
            if primary and (primary['id'],primary['version']) in observation_versions:
                primary['version'] = observation_versions[(primary['id'],primary['version'])]
            else:
                assertion.update(comparable=False,normalized_values=[])
                assertion.setdefault('temporal_context',{})['temporal_requires_recheck'] = True
                assertion['limitation'] = assertion.get('limitation','') + ' · 정확한 보존 시각 근거·버전의 재확인 필요'
            _recheck_relations(db,assertion,relation_targets)
            assertion['version'] = content_version(assertion)
            if row['session_id']:
                assertion['session_id'] = row['session_id']
            result['timeline'].append(assertion)
        for note in notes:
            qid = 'Q-' + note['note_id']
            judgment = note.get('investigation') or {}
            evidence_refs = [dict(kind='source', id=eid, version=sources[eid]['version'])
                             for eid in note.get('evidence_ids', []) if eid in sources]
            question = dict(id=qid, version=str(note['revision']), question=note['question'],
                answer=note['answer'], assessment=judgment.get('assessment','not_recorded'),
                work_state=judgment.get('work_state','open'), evidence_ids=note.get('evidence_ids',[]),
                evidence_refs=evidence_refs)
            if note.get('session_id'):
                question['session_id'] = note['session_id']
            _question_details(db,prefix,params,note,question,observation_versions)
            result['questions'].append(question)
            for index, row in enumerate(note.get('timeline', [])):
                if len(result['timeline']) >= MAX_TIMES:
                    break
                if row.get('temporal'):
                    assertion, observations = project_time(None, {'id':qid,'revision':note['revision']}, row, index, sources)
                    _recheck_relations(db,assertion,relation_targets)
                    assertion['version'] = content_version(assertion)
                    if note.get('session_id'):
                        assertion['session_id'] = note['session_id']
                    result['timeline'] = [t for t in result['timeline'] if t['id'] != assertion['id']]
                    result['timeline'].append(assertion)
                    for observation in observations:
                        if observation['id'] not in {o['id'] for o in result['observations']}:
                            source = sources.get(observation.get('source_ref',{}).get('id'))
                            if source and source.get('session_id'):
                                observation['session_id'] = source['session_id']
                            result['observations'].append(observation)
                else:
                    # Older notes without frozen coordinates remain visibly
                    # unknown. Delivery/ledger timestamps are never event time.
                    result['timeline'].append(dict(id=f'TM-{qid}-{index}', version=str(note['revision']),
                        question_id=qid, observation_ref=None, time_kind='unknown', shape='unknown',
                        raw_values=[str(row.get('raw_time',row.get('time','')))], normalized_values=[],
                        comparable=False, explanation=row.get('description',''),
                        limitation='정확한 보존 시각 근거·버전의 재확인 필요; 절대시각 비교 불가',
                        file_time_type=(row.get('time_source') or {}).get('field'),
                        **({'session_id':note['session_id']} if note.get('session_id') else {})))
        # Optional analyst assertions remain separate. These additional rows only
        # expose literal dates in selected retained returns, never answer/ledger time.
        for source in result['sources']:
            observations, times, coverage = timestamp_projection(source,source_bodies[source['id']])
            source['timestamp_scan'] = coverage
            # Reuse an already selected exact observation, while still exposing
            # its observed clock when no optional analyst time assertion was saved.
            def span(ob):
                ref = ob.get('source_ref') or {}
                return (ref.get('id'),ref.get('version'),ob.get('field_pointer'),
                        ob.get('byte_start'),ob.get('literal'))
            selected_spans = {span(ob):ob for ob in result['observations']}
            added = []
            reused = {}
            for observation in observations:
                existing = selected_spans.get(span(observation))
                if existing:
                    reused[observation['id']] = dict(kind='observation',id=existing['id'],version=existing['version'])
                else:
                    added.append(observation)
            for time in times:
                reference = reused.get(time['observation_ref']['id'])
                if reference:
                    time['observation_ref'] = reference
                    time['version'] = content_version(time)
            result['observations'].extend(added); result['timeline'].extend(times)
            counts['observations'] += len(added); counts['timeline'] += len(times)
        for observation in result['observations']:
            observation.update(observation_display(observation,sources.get(observation.get('source_ref',{}).get('id'))))
        _review_questions(resolved,notes,result,counts)
        _sessions(db,result,counts)
    from .task_focus import retained_task_state
    basis = retained_task_state(case)
    if basis is None:
        result['task_focus'] = {'status':'unavailable','tasks':[], 'omitted_tasks':None}
    else:
        from .task_focus import project_tasks
        versions = {(q['id'],q['version']) for q in result['questions']}
        matching = [q for q in basis['questions'] if (q['id'],str(q['revision'])) in versions]
        result['task_focus'] = project_tasks(basis,matching)
        result['task_focus']['omitted_tasks'] += max(0, counts['questions']-len(matching))
    _reports(case,resolved['case_id'],result,counts,current_basis=basis)
    kept = {key: [] for key in counts}
    budget = len(json.dumps(result['task_focus'],ensure_ascii=False).encode())
    for key, limit in [('questions',MAX_QUESTIONS),('sources',MAX_SOURCES),
                       ('observations',MAX_OBSERVATIONS),('timeline',MAX_TIMES),
                       ('reports',MAX_REPORTS),('sessions',MAX_SESSIONS),
                       ('review_questions',MAX_REVIEW_QUESTIONS)]:
        for row in result[key][:limit]:
            size = len(json.dumps(row,ensure_ascii=False).encode())
            if budget + size <= MAX_VIEW_BYTES:
                kept[key].append(row); budget += size
        result[key] = kept[key]
    omitted = {key:max(0,counts[key]-len(result[key])) for key in counts}
    if missing_references:
        omitted['unavailable_references'] = missing_references
    if result['review_question_coverage']['omitted_receipts']:
        omitted['review_receipts'] = result['review_question_coverage']['omitted_receipts']
    result.update(counts=counts, omitted_counts=omitted, partial=any(omitted.values()))
    return result


def selected_source(resolved, source_id, source_version, *, pointer='', offset=0, limit=2048):
    """One exact retained source version; collection and UTF-8 paging are bounded."""
    from .report_driven.model_pages import source_view
    case = resolved['case']
    with closing(_connection(case)) as db:
        row = db.execute("SELECT id,data FROM events WHERE kind='tool_result' AND id=? "
                         'AND length(cast(data AS BLOB))<=?', (source_id,MAX_SOURCE_BYTES)).fetchone()
    if row is None:
        raise ValueError('보존된 출처가 없거나 원문 조회 크기 제한을 초과했습니다.')
    data = json.loads(row['data'])
    if data.get('tool') not in EVIDENCE_TOOLS:
        raise ValueError('이 사건의 보존된 근거를 선택하세요.')
    source = source_record({'id':row['id'],'data':data})
    result = source_view(source,data,dict(source_version=source_version,pointer=pointer,offset=offset,limit=limit))
    result['source_ref']['kind'] = 'source'
    result['has_more'] = result.get('next_offset') is not None
    return result
