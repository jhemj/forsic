"""Bounded, staged advice at native goal boundaries; no evidence writer or agent loop."""
from collections import deque
from copy import deepcopy
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from .investigation_context import resolve_context
from .notes import EVIDENCE_TOOLS
from .runtime_clock import current_context
from .report_review_packet import REPORT_SYSTEM, EXECUTIVE_COPYEDIT, EXECUTIVE_READING_PLAN, compact as compact_report, expand as expand_report

PROVIDER='forsic-independent'
MAX_INPUT=40000
MAX_REPORT_INPUT=56000  # Complete authored body + current claims/gaps + retained spans; finite, report-only.
MAX_RESPONSE=20000
MAX_RECEIPT=300000
MAX_CALLS=3
REPORT_REVIEW_CONTRACT='complete-reader-prose-selected-grounding-v7'
REPORT_REQUIRED_INPUT_ERROR=('Complete report prose, current claims and consequential gap dispositions '
    'exceed the independent review input budget; the saved draft is unchanged and no partial final '
    'review was sent. Reduce repeated editorial text or use a supported larger-scope review '
    'workflow; do not remove material evidence or limits.')
EXECUTIVE_ROUNDS=3
EXECUTIVE_QUESTIONS=5
SYSTEM=REPORT_SYSTEM.replace("\n" + EXECUTIVE_COPYEDIT, "").replace(
    'You are an independent forensic reviewer of the current final report.',
    'You are an independent forensic reviewer. Exploration/checkpoint receives observations without analyst answers; '
    'final additionally receives current analyst_state and candidate_response, never original evidence.') + """
Commands/settings differ from successful execution; a completed check alone does not resolve a question.
Triage metadata identifies candidates only, not adoption, execution or original-source reinspection.
"""
EXECUTIVE_SYSTEM=SYSTEM.replace('You are an independent forensic reviewer.',
    'You are an independent executive reader scrutinizing an investigation report.').replace(
    'Return one JSON object with only the findings key and at most 3 findings; use {"findings":[]} if no issue:',
    'Return one JSON object with only the findings key and exactly 5 findings:') + """
Ask distinct unanswered questions after reading previous main-investigation answers; never repeat previous_questions.
Each question and next_check must be at most 180 characters and why at most 240 characters; impact is material.
Only ask questions bound to presented review_ref handles; do not answer them or execute tools.
Distinguish material, technical, permission and investigative-value limits using actual observations.
"""

SCALARS=('path','file_path','volume_offset','representation','listing_complete','content_not_measured','complete',
         'complete_file','complete_member','coverage_complete','search_complete','scan_exhausted',
         'bytes_scanned','bytes_returned','line_start','line_end','byte_start','byte_end','stop_reason',
         'allocated_only','truncated','next_offset','next_line','search_text','match_type','outcome',
         'filesystem_view','filesystem_selection','volume_selection','scope','scope_note','limitation',
         'files_scanned','files_completed','files_skipped')
ARRAYS={'entries':('path','bytes','mode','name'), 'files':('path','bytes','mode'),
        'lines':('line','text'), 'matches':('path','file_path','line','text'),
        'skips':('path','file_path','reason','error_type'),
        'target_mounts':('path','filesystem_type'), 'volumes':('offset','bytes','name')}
SHELL_POINTERS=('/representation','/original_evidence','/coverage_not_established','/started_id',
                '/execution/state','/execution/exit_code','/execution/termination_confirmed',
                '/raw_output/stdout_bytes','/raw_output/stderr_bytes','/raw_output/retained_bytes',
                '/raw_output/complete','/raw_output/truncated','/preview_truncated/stdout',
                '/preview_truncated/stderr','/preview_encoding')


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _input_fingerprint(system, body):
    """Reuse unchanged inputs without packet-clock or navigation-snapshot churn.

    The retained packet keeps the exact timestamp. Local date, zone/offset and
    every other temporal instruction remain part of review applicability.
    """
    identity = deepcopy(body)
    # An unrelated report edit can change navigation CAS coordinates without
    # changing this report's text, judgments, sources or actionable task state.
    # Keep the exact call in the receipt, but do not buy another review for it.
    for task in identity.get('analyst_state',{}).get('task_focus',{}).get('tasks',[]):
        if isinstance(task,dict):
            arguments=task.get('details_call',{}).get('arguments',{})
            if isinstance(arguments,dict):arguments.pop('snapshot_id',None)
    clock = body.get('runtime_clock')
    if isinstance(clock, dict):
        try:
            instant = datetime.fromisoformat(clock['current_datetime'])
        except (KeyError, TypeError, ValueError):
            pass  # Unknown clock shapes retain their complete identity.
        else:
            if instant.utcoffset() is not None:
                identity['runtime_clock'] = {**clock, 'current_datetime': {
                    'date': instant.date().isoformat(), 'utc_offset': instant.strftime('%z')}}
    return _hash({'system': system, 'prompt': _json(identity)})


def _retained_input_fingerprint(request):
    """Compare only complete retained packets, never infer one from a digest."""
    messages = request.get('messages')
    if not isinstance(messages, list) or len(messages) != 2:
        return None
    for message, role in zip(messages, ('system', 'user')):
        if (not isinstance(message, dict) or message.get('role') != role
                or not isinstance(message.get('content'), str)):
            return None
    try:
        body = json.loads(messages[1]['content'])
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    return _input_fingerprint(messages[0]['content'], body)


def _review_coordinate(source,field):
    value=field['value']
    coordinate={'source_id':source['source_id'],'source_version':source['source_version'],
                'pointer':field['pointer'],'quote':value if isinstance(value,str) else _json(value)}
    if 'byte_start' in field:coordinate['byte_start']=field['byte_start']
    return coordinate


def _remember_field(refs,pointer,value):
    """Separate excerpts at one original pointer must remain separate literals."""
    if pointer not in refs:refs[pointer]=value
    elif refs[pointer]!=value:
        refs[pointer]=(refs[pointer] if isinstance(refs[pointer],list) else [refs[pointer]])+[value]


def _triage_fields(db,rowid,add,item):
    """Only the native triage schema is projected; never interpret shell stdout."""
    def field(pointer):
        path='$'+''.join('['+p+']' if p.isdigit() else '.'+p for p in pointer.strip('/').split('/'))
        value,kind=db.execute("SELECT json_extract(data,?),json_type(data,?) FROM events WHERE rowid=?",
                              (path,path,rowid)).fetchone()
        if kind in ('true','false'):value=kind=='true'
        if isinstance(value,(str,int,float,bool)):add(pointer,value)
    for pointer in ('/schema','/action','/profile','/scan_id','/adoption','/coverage/state',
                    '/coverage/selected_files','/coverage/scope','/summary/candidate_count',
                    '/summary/returned_candidates','/summary/omitted_candidates','/summary/counts_complete',
                    '/summary/count_scope','/query_state','/candidate_query/available',
                    '/candidate_query/index_truncated','/selection/indexed_candidates','/selection/page_truncated'):
        field(pointer)
    for path,limit,keys in (
        ('/coverage/inputs',4,('input_id','state','candidate_count','counts_complete',
                              'records_parsed','records_available','records_with_warnings',
                              'source_recheck/source_ref/id','source_recheck/source_ref/version',
                              'source_recheck/source_ref/pointer','source_recheck/selection_kind',
                              'source_recheck/sha256','source_recheck/bytes')),
        ('/candidates',2,('candidate_id','input_id','status','entry_kind','triage_format',
                          'source_recheck/source_ref/id','source_recheck/source_ref/version',
                          'source_recheck/source_ref/pointer','source_recheck/byte_start',
                          'source_recheck/byte_end','source_recheck/line_start','source_recheck/line_end'))):
        jpath='$'+path.replace('/','.')
        total=db.execute("SELECT coalesce(json_array_length(data,?),0) FROM events WHERE rowid=?",(jpath,rowid)).fetchone()[0]
        # Representative rows preserve their original array indices, including the tail.
        indices=sorted(set([*range(min(total,(limit+1)//2)),*range(max(0,total-limit//2),total)]))
        omitted=total-len(indices)
        for index in indices:
            before=len(item['fields'])
            for key in keys:field(path+'/'+str(index)+'/'+key)
            if path=='/coverage/inputs':
                for reason in range(3):field(path+'/'+str(index)+'/reason_codes/'+str(reason))
            if len(item['fields'])==before:omitted+=1
        item.setdefault('omitted_array_rows',{})[path]=omitted


def _field_handle(source,field):
    return 'R1:'+_hash(_review_coordinate(source,field))[:24]


def _retained_version(case,sid):
    digest=hashlib.sha256()
    with case.connect() as db:
        row=db.execute("SELECT rowid FROM events WHERE id=? AND kind='tool_result'",(sid,)).fetchone()
        if not row:raise ValueError('Selected source is unavailable')
        with db.blobopen('events','data',row[0],readonly=True) as blob:
            while chunk:=blob.read(65536):digest.update(chunk)
    return 'event-sha256:'+digest.hexdigest()


def _result(status,text='',max_chars=3500):
    return {'provider':PROVIDER,'status':status,'context':text[:max(0,int(max_chars))],
            'review_id':'','requires_followup':False,'pending_finding_ids':[]}


def _unavailable(exc, phase, max_chars=3500):
    """Expose a safe local failure category; never label packet errors as transport."""
    known = {
        REPORT_REQUIRED_INPUT_ERROR: 'report_required_input_budget',
        'Complete executive question/answer history exceeds the review input bound': 'executive_history_budget',
        'Independent review input budget exceeded': 'review_input_budget',
        'Current question exceeds review budget': 'question_record_budget',
        'Report draft exceeds bounded review record budget': 'report_record_budget',
        'Requested question/scope exceeds review budget': 'scope_budget',
        'Saved report selection is unavailable or exceeds review budget': 'report_selection_budget',
        'Invalid independent review call budget configuration': 'review_call_budget_config',
    }
    message = str(exc) if isinstance(exc, ValueError) else ''
    code = known.get(message, 'review_state_read_failed')
    reason = message if message in known else 'Local review state could not be validated (' + type(exc).__name__ + ').'
    recovery = {'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox'}}
    result = _result('unavailable', '독립 검토는 미완료입니다. ' + reason +
        ' 보존된 검토 기록과 현재 보고서 선택 범위를 확인해 원인을 복구한 뒤 재개하세요. 같은 최종 답을 반복해도 복구되지 않습니다.', max_chars)
    result.update(requires_followup=True, followup_actionable=False, followup_action='repair',
        diagnostic={'code': code, 'phase': phase, 'reason': reason, 'recovery': recovery},
        progress_fingerprint=_hash({'code': code, 'phase': phase}))
    if isinstance(exc, ReportInputBudgetError):
        result['diagnostic']['input_budget'] = deepcopy(exc.input_budget)
    return result


def _packet_failure(exc, phase, resolved, max_chars):
    """A current editable report overflow is composition work, not an outage."""
    result = _unavailable(exc, phase, max_chars)
    if phase != 'final_packet' or not isinstance(exc, ReportInputBudgetError) or not resolved:
        return result
    try:
        draft = _report_draft(resolved, _review_questions(resolved))
        owned = {q['id'] for q in _question_records(resolved)}
        from .investigation_context import report_followup_selection
        inherited = report_followup_selection(resolved)
        editable = bool(draft and (set(draft['question_ids']) <= owned or inherited
            and sorted(draft['question_ids']) == inherited['question_ids']))
        if (not editable
                or any(draft.get(key) != exc.input_budget.get(key)
                       for key in ('draft_hash', 'draft_revision'))):
            return result
        limit = _call_budget(resolved)
        used = sum(not request.get('executive_round') for _, request in _receipts(resolved))
        if limit is not None and used >= limit:
            reason = f'Current report review remains incomplete; the ordinary review budget of {limit} calls is exhausted.'
            result.update(status='budget_exhausted', context=reason[:max_chars])
            result['diagnostic'].update(code='final_review_budget', reason=reason)
            return result
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as failure:
        return _unavailable(failure, 'report_input_repair', max_chars)
    recovery = {'tool': 'forsic_reporting', 'arguments': {'action': 'compose',
        'section': 'review', 'question_ids': draft['question_ids']}}
    result['diagnostic']['recovery'] = recovery
    result.update(status='report_composition_required', followup_actionable=True,
        followup_action='compose_report', recovery=recovery,
        # Only a changed saved body/basis renews the native final opportunity.
        # Read activity and revision-only writes must not hide no progress.
        progress_fingerprint=_hash({'identity': _identity(resolved),
            'question_ids': draft['question_ids'], 'basis_id': draft.get('basis_id'),
            'draft_hash': draft['draft_hash'], 'code': 'report_required_input_budget'}))
    result['context'] = ('현재 본문이 독립 검토 입력 한도를 ' +
        str(exc.input_budget['over_by_characters']) + '자 초과하여 검토 호출은 시작되지 않았습니다. '
        '현재 소유 본문은 편집 가능하며 검토 호출 예산도 남아 있습니다. 아래 compose 사전검사로 '
        '그룹별 크기를 읽고 반복 설명만 줄이세요. 중요한 사실·직접 인용·한계는 보존하고, '
        '수정한 현재판의 문서 검토와 독립 최종 검토를 이어가세요. 같은 본문으로 최종화를 '
        '반복하는 것은 진전이 아니며 검토 통과도 아닙니다. ' + _json(recovery))[:max_chars]
    return result


def _goal_key(resolved):
    return _hash(_identity(resolved))[:32]


def _call_budget(resolved):
    """Read an operator override for this exact case/goal/conversation/path."""
    from hermes_cli.config import load_config_readonly
    config=load_config_readonly()
    try:
        forsic=config.get('forsic',{})
        review=forsic.get('independent_review',{})
        budgets=review.get('call_budgets',{})
        if not isinstance(budgets,dict):raise ValueError
        key=_goal_key(resolved)
        if key not in budgets and resolved.get('_report_question_ids') is not None:
            # A runtime allowance handoff is not a review-budget reset. The
            # closest authenticated report ancestor retains its operator cap.
            from .investigation_context import report_followup_contexts
            for prior in report_followup_contexts(resolved):
                inherited_key = _goal_key(prior)
                if inherited_key in budgets:
                    key = inherited_key
                    break
        if key not in budgets:return MAX_CALLS
        limit=budgets[key]
        if limit is None or type(limit) is int and limit>=0:return limit
    except (AttributeError,TypeError,ValueError):
        pass
    raise ValueError('Invalid independent review call budget configuration')


def _identity(resolved):
    return {**{k:resolved[k] for k in ('case_id','goal_id','conversation_id')},
            'case_manifest':str(resolved['case'].manifest)}


def _read(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        if os.fstat(stream.fileno()).st_size>MAX_RECEIPT:raise ValueError('Invalid review receipt')
        raw=stream.read(MAX_RECEIPT+1)
    if len(raw)>MAX_RECEIPT:raise ValueError('Invalid review receipt')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('Invalid review receipt')
    return value


def _immutable(path,value):
    raw=(_json(value)+'\n').encode()
    if len(raw)>MAX_RECEIPT:raise ValueError('Independent receipt exceeds bounded size')
    fd,temporary=tempfile.mkstemp(prefix='.independent-review-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        # Atomic no-overwrite publication: concurrent boundaries never see half JSON.
        os.link(temporary,path,follow_symlinks=False)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        os.unlink(temporary)


def _response_for(request_path,request):
    path=request_path.with_name(request_path.name.replace('-request.json','-response.json'))
    if not path.exists():return None
    response=_read(path)
    if response.get('request_id')!=request['request_id']:raise ValueError('Review receipt identity mismatch')
    if 'response_version' in response or 'request_sha256' in response:
        if (response.get('response_version')!=1 or response.get('request_sha256')!=
                hashlib.sha256(request_path.read_bytes()).hexdigest()):
            raise ValueError('Review response does not match its immutable request version')
    return response


def _receipts(resolved):
    values=[]
    for path in resolved['case'].output.glob('independent-review-'+_goal_key(resolved)+'-*-request.json'):
        body=_read(path)
        if body.get('identity')==_identity(resolved):values.append((path,body))
    # Explicit editorial selection plus the accepted native human handoff can
    # carry the dialogue forward. Originals keep their own goal identity.
    if resolved.get('_report_question_ids') is not None:
        from .investigation_context import report_followup_selection
        selection = report_followup_selection(resolved)
        selected = sorted(resolved['_report_question_ids'])
        current_owned = {q['id'] for q in _question_records(resolved)}
        if selection and (selection['question_ids'] == selected
                          or selected and set(selected) <= current_owned):
            # A corrected current-owned report continues the same dialogue.
            # Imported rounds remain historical; only a current-scope final
            # receipt can certify this newly selected answer and authored body.
            from .investigation_context import report_followup_contexts
            contexts = report_followup_contexts(resolved)
            for index, prior in enumerate(contexts):
                scopes = [prior['_report_question_ids']]
                if index + 1 < len(contexts):
                    scopes.append(contexts[index + 1]['_report_question_ids'])
                prior_owned = {q['id'] for q in _question_records(prior)}
                for path in resolved['case'].output.glob('independent-review-'+_goal_key(prior)+'-*-request.json'):
                    body = _read(path)
                    scope = body.get('report_question_ids')
                    dialogue = bool(body.get('executive_round') and (scope is None or scope in scopes
                        or scope and set(scope) <= prior_owned))
                    # Preserve spent calls and unresolved advice for this same
                    # report. Different old report scopes do not consume its
                    # current ordinary allowance or certify its final body.
                    same_report = not body.get('executive_round') and scope == selected
                    if body.get('identity') == _identity(prior) and (dialogue or same_report):
                        values.append((path, body))
    return sorted(values,key=lambda pair:pair[1]['created_ns'])


def executive_receipt_sequence(resolved, receipts):
    """Policy rounds and retained handoff restarts, without rewriting receipts.

    A native handoff can expose a restarted request written while its inherited
    dialogue was unavailable. Its advice remains an obligation, but repeating
    an already recorded ordinal cannot restart or extend the three-round policy.
    Duplicates without verified inherited dialogue remain invalid.
    """
    from .investigation_context import report_followup_contexts
    ancestors = report_followup_contexts(resolved) if resolved.get('_report_question_ids') is not None else []
    ranks = {item['goal_id']: index for index, item in enumerate(
        [*reversed(ancestors), resolved])}
    policy, restarts, restarted_by_goal = [], [], {}
    for path, request in receipts:
        number = request.get('executive_round')
        if not number:
            continue
        if type(number) is not int or not 1 <= number <= EXECUTIVE_ROUNDS:
            raise ValueError('Invalid executive round sequence')
        if number == len(policy)+1:
            policy.append((path, request))
        elif (ancestors and number <= len(policy)
              and (owner := request.get('identity', {}).get('goal_id')) in ranks
              and ranks[owner] > ranks.get(policy[number - 1][1].get('identity', {}).get('goal_id'), 10**9)
              and number == restarted_by_goal.get(owner, 0) + 1):
            restarts.append((path, request))
            restarted_by_goal[owner] = number
        else:
            raise ValueError('Invalid executive round sequence')
    return policy, restarts


def _source_rows(db, lineage):
    query="SELECT rowid,id,json_extract(data,'$.tool') tool FROM events WHERE kind='tool_result' AND session IN ("+','.join('?' for _ in lineage)+") AND json_extract(data,'$.tool') IN ("+','.join('?' for _ in EVIDENCE_TOOLS)+") AND json_extract(data,'$.error') IS NULL AND coalesce(json_extract(data,'$.exit_code'),0)=0 AND json_extract(data,'$.reused_from') IS NULL ORDER BY rowid"
    return db.execute(query,(*lineage,*sorted(EVIDENCE_TOOLS)))


def _evaluation_fingerprint(questions):
    # A response records followthrough, not a new substantive answer. Revision
    # advances for both kinds of writes and must not make a review stale alone.
    return _hash([{k:v for k,v in question.items() if k not in ('revision','review_responses')}
                  for question in questions])


def report_review_scope(resolved, question_ids=None):
    """Bind trusted report selection without changing goal locks or call budgets."""
    if question_ids is None:
        return resolved
    if (not isinstance(question_ids, list) or not question_ids
            or any(not isinstance(qid, str) for qid in question_ids)):
        raise ValueError('Report review requires explicit question IDs')
    ids = sorted(set(question_ids))
    owned = {q['id'] for q in _question_records(resolved, include_report_source=True)}
    if not set(ids) <= owned:
        raise ValueError('Report review questions must belong to this conversation and goal')
    return {**resolved, '_report_question_ids': ids}



def native_report_review_scope(resolved):
    """Restore an authored report selection at every read-only native boundary.

    Selection comes from a saved current-goal report and the existing human
    handoff, never from keywords in a goal or a guessed case/question ID.
    """
    if resolved.get('_report_question_ids') is not None:
        return resolved
    from .investigation_context import report_source_goal_id
    if not report_source_goal_id(resolved):
        return resolved
    with resolved['case'].connect() as db:
        row = db.execute("SELECT length(cast(json_extract(data,'$.question_ids') AS BLOB)), "
            "substr(json_extract(data,'$.question_ids'),1,?) FROM events "
            "WHERE kind='report_narrative' AND session IN (" +
            ','.join('?' for _ in resolved['session_lineage']) +
            ") AND json_extract(data,'$.goal_id')=? ORDER BY rowid DESC LIMIT 1",
            (MAX_RECEIPT, *resolved['session_lineage'], resolved['goal_id'])).fetchone()
    if row is None:
        # The accepted human handoff already certifies one exact editorial
        # selection. Expose its retained dialogue before first composition,
        # without treating the old draft as this goal's authored report.
        from .investigation_context import report_followup_selection
        previous = report_followup_selection(resolved)
        return report_review_scope(resolved, previous['question_ids']) if previous else resolved
    if row[0] is None or row[0] > MAX_RECEIPT:
        raise ValueError('Saved report selection is unavailable or exceeds review budget')
    return report_review_scope(resolved, json.loads(row[1]))


def _review_questions(resolved):
    ids = resolved.get('_report_question_ids')
    questions = _question_records(resolved, include_report_source=ids is not None)
    return questions if ids is None else [q for q in questions if q['id'] in ids]


def _report_draft(resolved, questions):
    """Bounded current-scope prose, separately labeled from evidence for final review."""
    ids = {q['id'] for q in questions}
    if not ids:
        return None
    scoped = resolved.get('_report_question_ids')
    selection_sql = " AND json(json_extract(data,'$.question_ids'))=json(?)" if scoped is not None else ''
    selection_params = (_json(scoped),) if scoped is not None else ()
    with resolved['case'].connect() as db:
        rows = db.execute("SELECT length(cast(data AS BLOB)) AS size,substr(data,1,?) AS data "
            "FROM events WHERE kind='report_narrative' AND session IN (" +
            ','.join('?' for _ in resolved['session_lineage']) +
            ") AND json_extract(data,'$.goal_id')=?" + selection_sql + " ORDER BY rowid DESC LIMIT 16",
            (MAX_RECEIPT, *resolved['session_lineage'], resolved['goal_id'], *selection_params))
        for row in rows:
            if row['size'] > MAX_RECEIPT:
                raise ValueError('Report draft exceeds bounded review record budget')
            value = json.loads(row['data'])
            selected = value.get('question_ids', [])
            if selected and (set(selected) == set(scoped) if scoped is not None else set(selected) <= ids):
                # Exclude the review disposition itself: acknowledging feedback
                # cannot make that same review stale or trigger another call.
                return {key: value[key] for key in
                    ('schema', 'template_version', 'question_ids', 'basis_id', 'draft_revision', 'draft_hash', 'sections', 'fields')
                    if key in value}
    return None


def _review_evaluation(resolved, questions):
    question_identity = _evaluation_fingerprint(questions)
    draft = _report_draft(resolved, questions)
    if resolved.get('_report_question_ids') is not None:
        return _hash({'questions': question_identity, 'report_draft': draft,
                      'report_review_contract': REPORT_REVIEW_CONTRACT,
                      'report_question_ids': resolved['_report_question_ids']})
    return (_hash({'questions': question_identity, 'report_draft': draft,
                   'report_review_contract': REPORT_REVIEW_CONTRACT}) if draft else question_identity)


def _material_fingerprints(resolved, sources_sha256, questions, inventory):
    if resolved.get('_report_question_ids') is not None:
        # A final report owns an explicit judgment/source selection. Reading an
        # unrelated source is not a revision of that selection. Follow the same
        # dependency graph as rendering, including counterevidence and gaps.
        from .report_driven.host import current
        from .report_driven.views import selected_report_state
        state = current(resolved['case'])
        for question in state['questions']:
            question.pop('review_responses', None)
        selected = selected_report_state(state, resolved['_report_question_ids'])
        material = {key: selected[key] for key in ('scope', 'questions', 'claims',
            'hypotheses', 'gaps', 'missions', 'tests', 'assessments', 'observations',
            'timeline', 'actions', 'indicators', 'sources')}
        immutable = {item['id'] for item in material['sources']} | {material['scope']['id']}
        for question in material['questions']:
            for key in ('revision', 'review_responses'):
                question.pop(key, None)
        def substantive(value):
            if isinstance(value, dict):
                return {key: substantive(item) for key, item in value.items()
                        if key != 'version' or value.get('id') in immutable}
            if isinstance(value, list):
                return [substantive(item) for item in value]
            return value
        return {'fingerprint': _hash({'contract': REPORT_REVIEW_CONTRACT,
                    'goal': resolved['goal'].goal, 'selected_material': substantive(material)}),
            'evaluation_fingerprint': _review_evaluation(resolved, questions),
            'report_draft_fingerprint': _hash({'contract': REPORT_REVIEW_CONTRACT,
                'draft': _report_draft(resolved, questions)})}
    selected_ids=sorted({sid for question in questions for sid in
        [*question.get('evidence_ids',[]),*question.get('temporal_summary',{}).get('selected_source_ids',[])]})
    selected_digest=hashlib.sha256()
    with resolved['case'].connect() as db:
        for sid in selected_ids[:32]:
            selected_digest.update(sid.encode())
            row=db.execute("SELECT rowid FROM events WHERE id=? AND kind='tool_result'",(sid,)).fetchone()
            if row:
                with db.blobopen('events','data',row[0],readonly=True) as blob:
                    while chunk:=blob.read(65536):selected_digest.update(chunk)
    result = {'fingerprint':_hash({'sources_sha256':sources_sha256,'question':resolved['case'].config.get('question',''),
        'scope':resolved['case'].config.get('scope',''),'goal':resolved['goal'].goal,'observation_scope':inventory,
        'selected_sources_sha256':selected_digest.hexdigest(),'selected_sources_omitted':max(0,len(selected_ids)-32)}),
        'evaluation_fingerprint':_review_evaluation(resolved,questions)}
    if resolved.get('_report_question_ids') is not None:
        result['report_draft_fingerprint'] = _hash({'contract':REPORT_REVIEW_CONTRACT,
            'draft':_report_draft(resolved, questions)})
    return result


def freshness(resolved):
    """Exact review identities without assembling excerpts or a model payload."""
    digest=hashlib.sha256()
    with resolved['case'].connect() as db:
        for row in _source_rows(db,resolved['session_lineage']):
            digest.update(row['id'].encode())
            with db.blobopen('events','data',row['rowid'],readonly=True) as blob:
                while chunk:=blob.read(65536):digest.update(chunk)
    questions=_review_questions(resolved)
    inventory=None
    try:
        from .observation_scope import observed_scope
        inventory=observed_scope(resolved,max_targets=40,max_chars=3600)
    except ImportError:
        pass
    return _material_fingerprints(resolved,digest.hexdigest(),questions,inventory)


def _question_view(question,max_chars):
    """Keep current answers ahead of optional nested workbench/history detail.

    Exact unabridged notes still determine the evaluation fingerprint. Trimming
    this presentation neither closes a question nor validates its judgment.
    """
    values={k:v for k,v in question.items() if k!='session_id'}
    def clipped(value,chars,items,depth=0):
        if isinstance(value,str):return value[:chars]
        if isinstance(value,list):return [clipped(v,chars,items,depth+1) for v in value[:items]]
        if isinstance(value,dict):
            if depth>=4:return {'omitted_nested_value':True}
            return {k:clipped(v,chars,items,depth+1) for k,v in list(value.items())[:8]}
        return value
    def view(keys,chars,items,rich=False):
        result={k:(v[:chars] if isinstance(v,str) else v[:items] if isinstance(v,list) else v)
                if rich else clipped(v,chars,items) for k in keys if k in values for v in [values[k]]}
        for key in ('id','revision','definition_version'):
            if key in result:result[key]=values[key]
        changes={k:{'original_json_characters':len(_json(values[k])),
                    'presented_json_characters':len(_json(v))}
                 for k,v in result.items() if v!=values[k]}
        missing=[k for k in values if k not in result]
        if changes or missing:
            result['presentation_omissions']={'scope':'current_question_presentation_only',
                'truncated_fields':changes,'omitted_fields':missing}
        return result
    # Preserve rich small notes, including temporal/workbench summaries.
    full=view(values,1200,6,rich=True)
    if len(_json(full))<=max_chars:return full
    core=('id','revision','definition_version','question','answer','assessment','work_state','scope',
          'limitations','critical_gaps','gaps','next_checks','alternatives','remaining_gap_ids',
          'closure_rationale','reopen_conditions','judgment_review_required','review_responses',
          'evidence_ids','claim_refs')
    for chars,items in ((400,2),(200,1),(80,1),(32,1)):
        result=view(core,chars,items)
        if len(_json(result))<=max_chars:return result
    for chars in (200,80,32):
        result=view(('id','revision','question','answer','assessment','work_state','limitations','next_checks'),chars,1)
        if len(_json(result))<=max_chars:return result
    return None


class ReportInputBudgetError(ValueError):
    """Bounded size metadata for a refused complete report, never its prose."""
    def __init__(self, report, allowance, input_limit=None):
        super().__init__(REPORT_REQUIRED_INPUT_ERROR)
        required = len(_json(report))
        content = report['content']
        view = expand_report(json.loads(content) if isinstance(content, str) else content)
        groups = [{'group': str(name)[:80],
                   'serialized_characters': len(_json(field)),
                   'cell_characters': sum(len(text) for entry in field.get('entries', [])
                                          for text in entry.get('cells', [])),
                   'detail_characters': sum(len(text) for entry in field.get('entries', [])
                                            for text in entry.get('cell_details', []))}
                  for name, field in view.get('fields', {}).items()]
        groups.sort(key=lambda item: item['serialized_characters'], reverse=True)
        self.input_budget = {
            'measurement': 'canonical_json_characters_not_tokens',
            'input_limit': MAX_INPUT if input_limit is None else input_limit, 'projection_allowance': allowance,
            'required_projection_characters': required,
            'over_by_characters': max(0, required - allowance),
            'serialized_prose_characters': len(content) if isinstance(content, str) else len(_json(content)),
            'required_claim_characters': len(_json(report['basis_catalog'])),
            'draft_hash': str(report.get('draft_hash', ''))[:128],
            'draft_revision': report.get('draft_revision'),
            'groups': groups[:16], 'omitted_groups': max(0, len(groups) - 16),
            'meaning': 'Required projection only; the remaining input is reserved for '
                'instructions, task context and source fields. Group sizes locate repeated '
                'editorial text, not expendable evidence. Preserve material facts and limits.'}


def _report_review_projection(resolved, draft, *, max_chars=MAX_INPUT, input_limit=None):
    """All authored text, without repeated generated citation metadata.

    The full retained draft still determines freshness. Current basis labels
    remain analyst statements; only separately validated source fields can be
    returned as review_ref evidence. No sentence is summarized or truncated.
    """
    fields={}
    keys=set();nodes=[]
    for group,field in draft.get('fields',{}).items():
        entries=[]
        for entry in field.get('entries',[]):
            entries.append({key:entry[key] for key in
                ('node_id','status','cells','cell_details','basis_keys','cell_basis_keys','assessment','event_kind') if key in entry})
            if 'assessment' in entry:
                from .report_driven.template_contract import ASSESSMENT_SCALES
                entries[-1]['assessment'] = {**entry['assessment'],
                    'scale_labels': list(ASSESSMENT_SCALES[entry['node_id']])}
            keys.update(entry.get('basis_keys',[]));nodes.append(entry['node_id'])
        fields[group]={'entries':entries,'complete':field.get('complete',True)}
    view={'fields':fields}
    if draft.get('sections'):view['sections']=draft['sections']  # retained legacy authored text
    result={'role':'authored_report_not_source_evidence',
        'contract':REPORT_REVIEW_CONTRACT,'basis_id':draft.get('basis_id'),
        'draft_hash':draft.get('draft_hash'),'draft_revision':draft.get('draft_revision'),
        'content':_json(view),'omitted_characters':0,
        'executive_reading_plan':deepcopy(EXECUTIVE_READING_PLAN),
        'coverage':{'all_reader_text_present':True,'node_count':len(nodes),
            'omitted_generated_metadata':['citation_refs','cell_citation_refs','saved_at'],
            'meaning':'Full prose, not full original-evidence coverage.'},
        'basis_catalog':{},'basis_catalog_status':'not_available_for_this_basis',
        'basis_catalog_omitted_keys':sorted(keys),
        'basis_catalog_scope':'Required claims/material gaps; optional labels may be omitted. Not original evidence.',
        }
    if fields:
        from .report_driven.host import current
        from .report_driven.narrative import basis, material
        actual,selected=basis(current(resolved['case']),draft.get('question_ids',[]))
        if actual==draft.get('basis_id'):
            result['basis_catalog_status']='current_labels_not_original_evidence'
            selected_material=[item for item in material(selected) if item['key'] in keys
                or item['kind']=='gap' and item.get('impact') in ('material','decision_critical')]
            claims=[item for item in selected_material if item['kind']=='claim']
            # Optional aggregate answers must not displace a current correction
            # or strip the limits from a claim used by the reader's sentences.
            for item in claims:
                result['basis_catalog'][item['key']]={key:deepcopy(item[key]) for key in
                    ('statement','scope','assertion_kind','inference_strength','assumptions','limitations')
                    if key in item}
                result['basis_catalog_omitted_keys'].remove(item['key'])
            # Review why consequential work was deferred, not just its prose label.
            gaps=[item for item in selected_material if item['kind']=='gap']
            for item in gaps:
                result['basis_catalog'][item['key']]={key:deepcopy(item[key]) for key in
                    ('original_obligation','reason','disposition','impact','feasible_next_action',
                     'missing_preconditions','reopen_conditions') if key in item}
                if item['key'] in result['basis_catalog_omitted_keys']:
                    result['basis_catalog_omitted_keys'].remove(item['key'])
            result['coverage'].update(all_selected_claims_present=True,
                selected_claim_count=len(claims), all_material_gap_dispositions_present=True,
                material_gap_count=len(gaps))
            if len(_json(result))>max_chars:
                # Nest the lossless view directly, avoiding a second layer of JSON escaping.
                packed=compact_report(view)
                if len(_json(packed))<len(_json(result['content'])):result['content']=packed
            if (claims or gaps) and len(_json(result))>max_chars:
                raise ReportInputBudgetError(result, max_chars, input_limit)
            for item in selected_material:
                if item['kind'] in ('claim','gap'):continue
                label={key:item[key] for key in
                    ('statement','reason','answer','explanation','label') if key in item}
                if not label:continue
                result['basis_catalog'][item['key']]=label
                if len(_json(result))>max_chars:
                    result['basis_catalog'].pop(item['key'])
                else:result['basis_catalog_omitted_keys'].remove(item['key'])
    return result


def _collect(resolved,stage='checkpoint',draft_response='',*,runtime_clock=None,report_input_only=False):
    """Stream bounded source fields; only final review receives separately labeled current notes."""
    case=resolved['case'];lineage=resolved['session_lineage']
    runtime_clock=current_context() if runtime_clock is None else runtime_clock
    body={'runtime_clock':runtime_clock,'notice':'보존된 관측의 부분 발췌입니다. 독립 조언이며 원문 증거나 완료 승인이 아닙니다.',
          'requested_question':case.config.get('question',''),'approved_scope':case.config.get('scope',''),
          'goal':resolved['goal'].goal,'stage':stage,'sources':[],
          'active_task':{'authority':'native_goal','goal_id':resolved['goal_id'],
                         'objective_pointer':'/goal',
                         'report_question_ids':resolved.get('_report_question_ids'),
                         'case_scope_role':'background_not_current_task_authorization'},
          'omitted':{'sources':0,'derived_shell_outputs':0},
          'omission_scope':'review_packet_presentation_only',
          'source_coverage_basis':'Original coverage flags are retained in source fields; packet omissions do not establish missing original evidence.'}
    questions=_review_questions(resolved)
    draft=_report_draft(resolved,questions) if stage=='final' else None
    system=REPORT_SYSTEM if draft else SYSTEM
    input_limit=MAX_REPORT_INPUT if draft else MAX_INPUT
    if len(_json(body))+len(system)>input_limit:raise ValueError('Requested question/scope exceeds review budget')
    available=input_limit-len(_json(body))-len(system)
    # Optional basis summaries must not displace every direct source field.
    report=_report_review_projection(resolved,draft,max_chars=max(0,available-13000),input_limit=input_limit) if draft else None
    reserve=min(28000,available*4//5) if stage=='final' else min(5500,available//3)
    if report:
        # Allocate complete prose before sampling optional source/context views.
        # Oversize prose stays saved; a partial prefix cannot certify final review.
        needed=len(_json(report))+1200
        if needed>available:
            raise ValueError('Complete report prose exceeds the independent review input budget; the saved draft is unchanged and no partial final review was sent. Reduce repeated editorial text or use a supported larger-scope review workflow; do not remove material evidence or limits.')
        reserve=max(reserve,needed+8000)
    if report_input_only:
        if stage != 'final' or not report:
            raise ValueError('A current authored report is required for input preflight')
        return {'status': 'required_input_fits',
            **{key: report.get(key) for key in ('basis_id', 'draft_hash', 'draft_revision')},
            'measurement': 'canonical_json_characters_not_tokens',
            'input_limit': input_limit if input_limit is None else input_limit, 'projection_allowance': max(0, available-13000),
            'projection_characters': len(_json(report)),
            'coverage': report['coverage']}
    source_limit=input_limit-reserve
    report_citations = bool(draft and any(ref.get('kind') == 'observation'
        for group in draft.get('fields', {}).values() for entry in group.get('entries', [])
        for ref in entry.get('citation_refs', [])))
    selected_ids={sid for q in questions for sid in q.get('evidence_ids',[])} if stage=='final' else set()
    recent=deque(maxlen=48);inventories=deque(maxlen=24);current_sources=deque(maxlen=32)
    digest=hashlib.sha256();count=0;versions={}
    # Case.connect is read-only because resolve_context constructs Case(read_only=True).
    with case.connect() as db:
        for row in _source_rows(db,lineage):
            count+=1;digest.update(row['id'].encode())
            # Hash immutable source bytes in fixed chunks; do not load full stdout/results.
            source_digest=hashlib.sha256()
            with db.blobopen('events','data',row['rowid'],readonly=True) as blob:
                while chunk:=blob.read(65536):
                    digest.update(chunk);source_digest.update(chunk)
            source_version='event-sha256:'+source_digest.hexdigest()
            if row['tool']=='forsic_shell':body['omitted']['derived_shell_outputs']+=1
            meta=(row['rowid'],row['id'],row['tool'],source_version);recent.append(meta)
            if row['id'] in selected_ids and row['tool']!='forsic_shell':current_sources.append(meta)
            lengths=db.execute("SELECT json_array_length(data,'$.entries'),json_array_length(data,'$.files'),json_extract(data,'$.schema') FROM events WHERE rowid=?",(row['rowid'],)).fetchone()
            if any(n is not None for n in lengths[:2]) or row['tool']=='forsic_shell' and lengths[2]=='forsic.triage.v1':inventories.append(meta)
        selected={m[1]:m for m in [*current_sources,*inventories,*recent]}
        versions={m[1]:m[3] for m in selected.values()}
        # Current question selections precede inventory and recency sampling.
        ordered=list({m[1]:m for m in [*current_sources,*inventories,*reversed(recent)]}.values())
        if report_citations:
            # Exact selected report spans below take precedence over generic
            # inventory/recency sampling and duplicated analyst claim text.
            ordered=[]
        refs={};read_targets=set()
        for rowid,sid,tool,_version in selected.values():
            if tool=='forsic_shell':continue
            target=db.execute("SELECT json_extract(data,'$.path'),json_extract(data,'$.file_path'),json_extract(data,'$.volume_offset'),json_array_length(data,'$.lines'),json_extract(data,'$.complete_file'),json_extract(data,'$.content_not_measured') FROM events WHERE rowid=?",(rowid,)).fetchone()
            if (target[3] or target[4]) and not target[5]:
                read_targets.add((target[0] if target[1] else None,target[2],target[1] or target[0]))
        for rowid,sid,tool,_version in ordered:
            image,volume=db.execute("SELECT json_extract(data,'$.path'),json_extract(data,'$.volume_offset') FROM events WHERE rowid=?",(rowid,)).fetchone()
            item={'source_id':sid,'source_version':versions[sid],
                  'source_version_basis':'retained_event_json_bytes_sha256','tool':tool,'fields':[],'omitted_fields':0,
                  'omission_scope':'review_packet_fields_only'}
            if tool=='forsic_shell':
                item.update(source_role='derived_command_output',
                            notice='파생 실행 결과—원본 직접 관측·검사 범위 보장 아님')
            is_triage=tool=='forsic_shell' and db.execute("SELECT json_extract(data,'$.schema') FROM events WHERE rowid=?",(rowid,)).fetchone()[0]=='forsic.triage.v1'
            local_refs={}
            def add(pointer,value,*,unread=False,**coordinates):
                if value is None:return
                field={'pointer':pointer,'value':value,**coordinates}
                if unread:field['content_not_observed_in_selected_reads']=True
                # Exact inventory paths stay indivisible; text portions report truncation.
                if isinstance(value,str) and len(value)>1200:
                    if pointer.endswith('/text') or pointer in ('/text','/content'):
                        field.update(value=value[:1200],omitted_characters=len(value)-1200)
                    else:item['omitted_fields']+=1;return
                field['review_ref']=_field_handle(item,field)
                item['fields'].append(field)
                if len(_json(item))>(9000 if is_triage else 6500) or len(_json(body))+len(_json(item))+len(system)>source_limit-1000:
                    item['fields'].pop();item['omitted_fields']+=1
                else:_remember_field(local_refs,pointer,field['value'])
            scalar_pointers=SHELL_POINTERS if tool=='forsic_shell' else tuple('/'+k for k in SCALARS)
            scalar_sql='SELECT '+','.join("json_extract(data,'$"+pointer.replace('/','.')+"'),json_type(data,'$"+pointer.replace('/','.')+"')" for pointer in scalar_pointers)+" FROM events WHERE rowid=?"
            scalars=db.execute(scalar_sql,(rowid,)).fetchone()
            for pointer,value,kind in zip(scalar_pointers,scalars[::2],scalars[1::2]):
                # SQLite's json_extract otherwise turns JSON booleans into 0/1.
                if kind in ('true','false'):value=kind=='true'
                if isinstance(value,(str,int,float,bool)):add(pointer,value)
            if is_triage:
                item.update(source_role='derived_triage_candidates',
                            notice='선택 입력의 파생 후보·계수—원본 재확인·근거 채택·실행 입증 아님')
                _triage_fields(db,rowid,add,item)
            for array,keys in ({} if tool=='forsic_shell' else ARRAYS).items():
                total=db.execute("SELECT coalesce(json_array_length(data,?),0) FROM events WHERE rowid=?",('$.'+array,rowid)).fetchone()[0]
                query='SELECT j.key,'+','.join("json_extract(j.value,'$."+key+"')" for key in keys)+" FROM events e,json_each(e.data,?) j WHERE e.rowid=? ORDER BY CAST(j.key AS INTEGER) LIMIT 100"
                included=0
                for entry in db.execute(query,('$.'+array,rowid)):
                    for key,value in zip(keys,entry[1:]):
                        if isinstance(value,(str,int,float,bool)):
                            add('/'+array+'/'+str(entry[0])+'/'+key,value,
                                unread=array in ('entries','files') and key=='path' and
                                ((image,volume,value) if tool=='forsic_image_files' else (None,None,value)) not in read_targets)
                    included+=1
                if total>included:item['omitted_fields']+=total-included
            for key in (('stdout','stderr') if tool=='forsic_shell' else ('text','content')):
                # SQL substr is character based; retrieval offsets are UTF-8 bytes.
                # Keep shell head/tail separate at their original pointer, never join them.
                total,kind=db.execute("SELECT length(json_extract(data,?)),json_type(data,?) FROM events WHERE rowid=?",('$.'+key,'$.'+key,rowid)).fetchone()
                if kind!='text':continue
                spans=[(0,min(total,1200))]
                if tool=='forsic_shell' and total>1200:spans.append((max(1200,total-1200),total))
                included=0
                for start,end in spans:
                    text,byte_start=db.execute("SELECT substr(json_extract(data,?),?,?),length(CAST(substr(json_extract(data,?),1,?) AS BLOB)) FROM events WHERE rowid=?",('$.'+key,start+1,end-start,'$.'+key,start,rowid)).fetchone()
                    before=len(item['fields'])
                    add('/'+key,text,character_start=start,character_end=end,
                        byte_start=byte_start,byte_end=byte_start+len(text.encode()),
                        coordinate_basis='UTF-8 bytes of retained field, not original evidence',
                        total_characters=total,omitted_characters=total-len(text))
                    if len(item['fields'])>before:included+=len(text)
                item.setdefault('omitted_text_characters',{})['/'+key]=total-included
            if item['fields'] and len(_json(body))+len(_json(item))+len(system)<=source_limit:
                body['sources'].append(item);refs[sid]=local_refs
                if tool=='forsic_shell':body['omitted']['derived_shell_outputs']-=1
        body['omitted']['sources']=count-len(body['sources'])
    evaluation_fingerprint=_review_evaluation(resolved,questions)
    if stage=='final':
        # Analyst judgments are visibly separate from source observations and are never quote refs.
        body['analyst_state']={'role':'analyst_judgment_not_source_evidence','questions':[],
                               'omitted_questions':0,'evaluation_fingerprint':evaluation_fingerprint}
        if report:body['analyst_state']['report_draft']=report
        remaining=input_limit-len(_json(body))-len(system)
        analyst_budget=max(0,min(800 if report_citations else 5500,remaining//4 if report else remaining//2))
        # Share the allowance across owners; a large history cannot crowd out
        # another question's current answer. Packet omissions are not gaps.
        for index,question in enumerate(questions):
            allowance=max(0,(analyst_budget-len(_json(body['analyst_state']['questions'])))//(len(questions)-index)-1)
            selected=_question_view(question,allowance)
            if selected is None:body['analyst_state']['omitted_questions']+=1
            else:body['analyst_state']['questions'].append(selected)
        draft_budget=max(0,min(6000,(input_limit-len(_json(body))-len(system)-500)//(4 if report else 2)))
        body['candidate_response']={'role':'analyst_draft_not_source_evidence','text':str(draft_response)[:draft_budget],
                                    'omitted_characters':max(0,len(str(draft_response))-draft_budget)}
        from .task_focus import project_tasks, retained_task_state
        basis = retained_task_state(case)
        if basis is not None:
            question_versions = {(q['id'],q['revision']) for q in questions}
            matching = [q for q in basis['questions'] if (q['id'],q['revision']) in question_versions]
            focus_budget=max(200,min(4000,(input_limit-len(_json(body))-len(system)-500)//(4 if report else 3)))
            focus = project_tasks(basis, matching, max_bytes=focus_budget)
            focus['omitted_tasks'] += max(0,len(questions)-len(matching))
            body['analyst_state']['task_focus'] = focus
        else:
            body['analyst_state']['task_focus'] = {'status':'unavailable','tasks':[], 'omitted_tasks':None}
        from .independent_review_state import final_grounding, report_grounding
        grounding_budget=max(0 if report else 600,min(11000 if report_citations else 8000 if report else 7000,input_limit-len(_json(body))-len(system)-(1200 if report else 4500)))
        grounding,selected_fields=(report_grounding(resolved,draft,max_chars=grounding_budget) if report_citations
                                  else final_grounding(resolved,questions,max_chars=grounding_budget))
        if report_citations and not selected_fields:
            raise ValueError('No selected report citation span could be preserved; no final review was sent. '
                             'Read the current report citation records and retained sources, repair stale/invalid '
                             'citations or reduce repeated editorial text, then retry the same finalization path.')
        body['analyst_state']['grounding']=grounding
        for selected in selected_fields:
            sid=selected.pop('source_id')
            if sid not in versions:versions[sid]=_retained_version(case,sid)
            item=next((source for source in body['sources'] if source['source_id']==sid),None)
            if item is None:
                item={'source_id':sid,'source_version':versions[sid],
                      'source_version_basis':'retained_event_json_bytes_sha256',
                      'tool':'explicit_current_claim_source','source_role':selected.get('source_role','retained_selected_source'),
                      'fields':[],'omitted_fields':0}
                body['sources'].append(item)
            selected.pop('source_version',None)
            selected['review_ref']=_field_handle(item,selected)
            item['fields'].append(selected)
            _remember_field(refs.setdefault(sid,{}),selected['pointer'],selected['value'])
    inventory=None
    try:
        from .observation_scope import observed_scope
        inventory=observed_scope(resolved,max_targets=40,max_chars=3600)
        body['observed_scope']={'role':'observation_coverage_not_completion',
            'completeness_basis':'bounded_observation_scope_projection_not_original_inventory',
            'omission_scope':'projection_targets_only','inventory':inventory}
        if len(_json(body))+len(system)>input_limit:
            body.pop('observed_scope');body['omitted']['scope_inventory']=True
        else:
            # Add bounded literal fields for older observed targets outside the recent window.
            # Read the retained coordinate itself: an inventory's normalized identity is not a quote.
            with case.connect() as db:
                for target in inventory.get('targets',[]):
                    for origin in target.get('source_refs',[]):
                        sid=origin.get('source_id');pointer=origin.get('pointer','')
                        if pointer.startswith(('/entries/','/files/')) and pointer.rsplit('/',1)[-1].isdigit():pointer+='/path'
                        if pointer=='/member':pointer+='/name'
                        if not pointer.endswith(('/path','/file_path','/name','/member_index')):continue
                        if pointer in refs.get(sid,{}):continue
                        parts=pointer.strip('/').split('/')
                        jpath='$'+''.join('['+part+']' if part.isdigit() else '.'+json.dumps(part) for part in parts)
                        row=db.execute("SELECT substr(json_extract(data,?),1,1200),length(json_extract(data,?)) FROM events WHERE id=? AND kind='tool_result' AND session IN ("+','.join('?' for _ in lineage)+") AND json_extract(data,'$.tool') IN ("+','.join('?' for _ in EVIDENCE_TOOLS)+") AND json_extract(data,'$.error') IS NULL AND coalesce(json_extract(data,'$.exit_code'),0)=0 AND json_extract(data,'$.reused_from') IS NULL",(jpath,jpath,sid,*lineage,*sorted(EVIDENCE_TOOLS))).fetchone()
                        if not row or row[0] is None or (row[1] or 0)>1200:continue
                        if sid not in versions:versions[sid]=_retained_version(case,sid)
                        item={'source_id':sid,'source_version':versions[sid],
                              'source_version_basis':'retained_event_json_bytes_sha256',
                              'tool':'observed_scope_reference','source_role':'retained_observation_coordinate',
                              'fields':[{'pointer':pointer,'value':row[0]}],'omitted_fields':0}
                        item['fields'][0]['review_ref']=_field_handle(item,item['fields'][0])
                        if len(_json(body))+len(_json(item))+len(system)>input_limit:
                            body['omitted']['scope_reference_fields']=body['omitted'].get('scope_reference_fields',0)+1
                            continue
                        existing=next((source for source in body['sources'] if source['source_id']==sid),None)
                        if existing:existing['fields'].extend(item['fields'])
                        else:body['sources'].append(item)
                        refs.setdefault(sid,{})[pointer]=row[0]
    except ImportError:
        pass
    body['omitted']['sources']=max(0,count-len({source['source_id'] for source in body['sources']}))
    fingerprints=_material_fingerprints(resolved,digest.hexdigest(),questions,inventory)
    fingerprint=fingerprints['fingerprint']
    # A field handle binds the exact presented excerpt, including separate spans at
    # the same pointer. It is not a C1/C2 evidence selection for the analyst.
    review_refs={}
    for source in body['sources']:
        sid=source['source_id']
        if sid not in versions:
            raise ValueError('Selected review source has no retained source version')
        for field in source['fields']:
            coordinate=_review_coordinate(source,field)
            handle=_field_handle(source,field)
            field['review_ref']=handle;review_refs[handle]=coordinate
    prompt=_json(body)
    if len(prompt)+len(system)>input_limit:raise ValueError('Independent review input budget exceeded')
    if report and count and not review_refs:
        raise ValueError('Complete report prose leaves no room for preserved source fields; no partial final review was sent. Reduce repeated editorial text or use a supported larger-scope review workflow.')
    return {'prompt':prompt,'system':system,'input_limit':input_limit,'runtime_clock':runtime_clock,'fingerprint':fingerprint,'input_fingerprint':_input_fingerprint(system,body),
            'report_draft_fingerprint':fingerprints.get('report_draft_fingerprint'),
            'evaluation_fingerprint':evaluation_fingerprint,'count':count,'refs':refs,'review_refs':review_refs,
            'all_closed':bool(questions) and all(q.get('work_state')=='scoped_closed' for q in questions)}


def _question_records(resolved, *, include_report_source=False):
    """Current owned notes only; no conversation or retained tool body is loaded."""
    lineage=resolved['session_lineage'];records={}
    goals = [resolved['goal_id']]
    inherited_ids = []
    if include_report_source:
        from .investigation_context import report_source_goal_id, report_followup_selection, report_followup_contexts
        previous = report_source_goal_id(resolved)
        if previous and previous not in goals:
            goals.append(previous)
        selection = report_followup_selection(resolved)
        if selection:
            inherited_ids = selection['question_ids']
            for prior in report_followup_contexts(resolved):
                if prior['goal_id'] not in goals:
                    goals.append(prior['goal_id'])
                inherited_ids.extend(qid for qid in prior['_report_question_ids'] if qid not in inherited_ids)
    with resolved['case'].connect() as db:
        selection_sql = (" OR ('Q-'||json_extract(data,'$.note_id')) IN (" +
                         ','.join('?' for _ in inherited_ids) + ')') if inherited_ids else ''
        query="SELECT length(data),substr(data,1,?) FROM (SELECT rowid,data,row_number() OVER (PARTITION BY json_extract(data,'$.note_id') ORDER BY rowid DESC) AS rank FROM events WHERE kind='note' AND coalesce(nullif(json_extract(data,'$.session_id'),''),session) IN ("+','.join('?' for _ in lineage)+") AND (json_extract(data,'$.goal_id') IN ("+','.join('?' for _ in goals)+")"+selection_sql+")) WHERE rank=1 ORDER BY rowid"
        for size,raw in db.execute(query,(MAX_RECEIPT,*lineage,*goals,*inherited_ids)):
            if size>MAX_RECEIPT:raise ValueError('Current question exceeds review budget')
            note=json.loads(raw);inv=note.get('investigation') or {}
            fields=('question','answer','scope','target_proposition','revision','definition_version','session_id','goal_id',
                    'critical_gaps','gaps','next_checks','alternatives','evidence_ids')
            item={k:note[k] for k in fields if k in note}
            item.update({k:v for k,v in inv.items() if k in ('answer','assessment','work_state','closure_rationale',
                'reopen_conditions','remaining_gap_ids','priority','review_responses','limitations','claim_refs','judgment_review_required')})
            forensic=note.get('forensic',inv.get('forensic'))
            if isinstance(forensic,dict):
                summary=_json(forensic)
                item['forensic_summary']={'role':'analyst_workbench_not_source_evidence',
                    'text':summary[:1400],'omitted_characters':max(0,len(summary)-1400),
                    'version_sha256':_hash(forensic)}
            if note.get('timeline'):item['temporal_summary']=_temporal_summary(note['timeline'])
            item['id']='Q-'+note['note_id'];records[item['id']]=item
    return sorted(records.values(),key=lambda q:q['id'])


def _has_saved_answer(resolved):
    # A saved provisional assessment is a timing signal only; it neither proves
    # correctness nor sufficiency. Bootstrap preserves the request, not a judgment.
    from .question_bootstrap import PROVISIONAL_ANSWER
    return any(isinstance(q.get('answer'),str) and q['answer'].strip()
               and q['answer'].strip()!=PROVISIONAL_ANSWER for q in _question_records(resolved))


def _temporal_summary(timeline):
    """Bounded analyst time declarations; raw timestamps remain visibly separate."""
    events=[];source_ids=set()
    for row in timeline:
        for reference in row.get('temporal_basis_refs',[]):
            if isinstance(reference,dict) and isinstance(reference.get('evidence_id'),str):
                source_ids.add(reference['evidence_id'])
    for row in timeline[:4]:
        model=row.get('temporal') or {}
        event={key:row[key] for key in ('event_ref','raw_time','time') if key in row}
        event['clipped_fields']=[]
        for key,value in list(event.items()):
            if isinstance(value,str) and len(value)>160:
                event[key]=value[:160];event['clipped_fields'].append(key)
        event['temporal']={key:model[key] for key in ('observed_or_inferred','timestamp_role',
            'clock_domain','earliest','latest','timezone_basis','year_basis','confidence') if key in model}
        event['omitted_temporal_fields']=max(0,len(model)-len(event['temporal']))
        for key,value in list(event['temporal'].items()):
            if isinstance(value,str):event['temporal'][key]=value[:100]
        event['basis_refs']=[{key:reference[key] for key in ('evidence_id','source_version','pointer','source_role')
                             if key in reference} for reference in row.get('temporal_basis_refs',[])[:2]]
        event['omitted_basis_refs']=max(0,len(row.get('temporal_basis_refs',[]))-2)
        events.append(event)
    return {'role':'analyst_time_inference_not_source_event_fact',
        'version_sha256':_hash(timeline),'events':events,'omitted_events':max(0,len(timeline)-4),
        'selected_source_ids':sorted(source_ids)[:32],'omitted_source_ids':max(0,len(source_ids)-32)}


def _active(session_id,goal_id,goal_text,intake_root,native_db):
    resolved=resolve_context(session_id,intake_root=intake_root,native_db=native_db)
    if not resolved or not resolved['goal'] or resolved['goal_id']!=goal_id:
        raise ValueError('Native case/goal binding is unavailable or changed')
    if resolved['goal'].status!='active':raise ValueError('Native goal is paused or inactive; review was not started')
    if resolved['goal'].goal!=goal_text:raise ValueError('Native goal text changed')
    return resolved


def _parse(text,refs,review_id='',rejected=None,*,review_refs=None,executive_round=0,previous_questions=()):
    """Validate coordinates and exact quote occurrence per item, never semantic implication."""
    value=json.loads(text)
    # The previous prompt allowed an array. Normalize only this lossless
    # envelope; item counts, exact handles and field validation stay identical.
    if isinstance(value,list):value={'findings':value}
    if (not isinstance(value,dict) or set(value)!={'findings'} or not isinstance(value['findings'],list)
            or (len(value['findings'])!=EXECUTIVE_QUESTIONS if executive_round else len(value['findings'])>3)):
        raise ValueError('Invalid independent advice shape')
    findings=[];seen={' '.join(q.casefold().split()) for q in previous_questions}
    for index,finding in enumerate(value['findings']):
        try:
            required=({'review_ref','question','why','next_check'} if review_refs is not None else
                      {'source_id','pointer','quote','question','why','next_check'})
            if not isinstance(finding,dict) or not required<=set(finding) or set(finding)-required-{'impact'}:
                raise ValueError('Invalid independent finding fields')
            if any(not isinstance(finding[k],str) or not finding[k].strip() or len(finding[k])>1000 for k in required):
                raise ValueError('Invalid independent finding length')
            if executive_round:
                if any(len(finding[k])>limit for k,limit in (('question',180),('next_check',180),('why',240))):
                    raise ValueError('Executive question exceeds its complete-display bound')
                normalized=' '.join(finding['question'].casefold().split())
                if normalized in seen:raise ValueError('Executive questions must be new and distinct')
                seen.add(normalized)
            coordinate={}
            if review_refs is not None:
                coordinate=review_refs.get(finding['review_ref'])
                if not coordinate:raise ValueError('review_ref was not presented with an exact source/version/field/value')
            else:
                source=refs.get(finding['source_id'],{})
                if not isinstance(source,dict) or finding['pointer'] not in source:
                    raise ValueError('Source/pointer was not presented with a verifiable value')
                actual=source[finding['pointer']]
                values=actual if isinstance(actual,list) else [actual]
                literals=[value if isinstance(value,str) else _json(value) for value in values]
                if not any(finding['quote'] in literal for literal in literals):
                    raise ValueError('Quote is not an exact substring of the presented source field')
            impact=finding.get('impact','material')
            if impact not in ('material','contextual'):raise ValueError('Invalid finding impact')
            if executive_round and impact!='material':raise ValueError('Every executive question requires main followup')
            item={**finding,**coordinate,'impact':impact,'review_id':review_id,
                  'quote_validation':'exact_presented_field_handle' if review_refs is not None else 'exact_substring_only'}
            # Review receipts hash event bytes; source pages use a different
            # version identity. Discover the source handle before selecting a page.
            item['retrieval']={'action':'source','source_id':item['source_id']}
            item['finding_id']='finding-'+_hash({'review_id':review_id,'finding':{**finding,'impact':impact}})[:24]
            if executive_round:item.update(kind='executive_question',executive_round=executive_round)
            if not any(f['finding_id']==item['finding_id'] for f in findings):findings.append(item)
        except (ValueError,KeyError,TypeError) as exc:
            if rejected is not None:
                failure={'index':index,'reason':str(exc),'role':'rejected_advice_not_evidence',
                         'recovery':'Use the existing source lookup for retained evidence, then explicitly investigate, refute, or limit the issue in the current question; do not resubmit this review.'}
                if isinstance(finding,dict):
                    for key in ('review_ref','source_id','pointer','question','next_check'):
                        if isinstance(finding.get(key),str):failure[key]=finding[key][:1000]
                rejected.append(failure)
    if executive_round and len(findings)!=EXECUTIVE_QUESTIONS:
        raise ValueError('Executive round requires exactly five valid distinct questions')
    return findings


def _budget_executive_body(body):
    """Allocate history/current judgments before optional packet presentation.

    Reuse the bounded current views selected by _collect. Never shorten an
    answer, claim or limitation to make the follow-up appear reviewable.
    """
    state=body.get('analyst_state',{})
    optional=[]
    for owner,keys in ((state,('task_focus',)),
                       (body,('candidate_response','observed_scope'))):
        for key in keys:
            if key in owner:optional.append((owner,key,owner.pop(key)))
    sources=body['sources'];body['sources']=[]
    history=body['executive_followup']['previous_round']
    # Expanded coordinates duplicate validated native citation handles.
    expanded=[]
    for item in history['questions']:
        answer=item['main_response']
        if 'citations' in answer:expanded.append((answer,answer.pop('citations')))
    omitted=sum(len(value) for _,value in expanded)
    if omitted:
        history['citation_projection']={
            'expanded_coordinates_omitted':omitted,
            'retained':'Every citation_refs handle; complete main answers and dispositions are unchanged.',
            'lookup':{'tool':'forsic_reporting','arguments':{'action':'review_inbox'}},
            'role':'reference_handles_not_presented_source_evidence'}
    presentation=body.setdefault('executive_presentation',{
        'role':'packet_omissions_not_investigation_gaps','omitted_optional_views':[],
        'source_fields_omitted':0})
    presentation['omitted_optional_views']=[key for _,key,_ in optional]
    # Selected grounding spans have passed coordinate/version checks. Preserve
    # their exact field handles before admitting sampled source previews.
    observations=state.get('grounding',{}).get('observations',[])
    def selected(source,field):
        return any(o.get('coordinate_validation')=='retained_source_version_and_exact_utf8_span_only'
            and o.get('source_ref',{}).get('id')==source['source_id']
            and o.get('field_pointer')==field['pointer']
            and o.get('literal')==field['value']
            and o.get('byte_start')==field.get('byte_start')
            and o.get('byte_end')==field.get('byte_end') for o in observations)
    pending=[]
    for source in sources:
        required=[field for field in source['fields'] if selected(source,field)]
        pending.extend((source,field) for field in source['fields'] if field not in required)
        if required:body['sources'].append({**source,'fields':required})
    def fits():return len(_json(body))+len(EXECUTIVE_SYSTEM)<=MAX_INPUT
    presentation['source_fields_omitted']=len(pending)
    original_omitted=body['omitted']['sources']
    def source_omissions():
        body['omitted']['sources']=original_omitted+len(
            {s['source_id'] for s in sources}-{s['source_id'] for s in body['sources']})
    source_omissions()
    if not fits():
        raise ValueError('Complete executive question/answer history exceeds the review input bound')
    for source,field in pending:
        retained=next((s for s in body['sources'] if s['source_id']==source['source_id']),None)
        added=retained is None
        if added:
            retained={**source,'fields':[]};body['sources'].append(retained)
        retained['fields'].append(field)
        presentation['source_fields_omitted']-=1;source_omissions()
        if not fits():
            retained['fields'].pop()
            if added:body['sources'].remove(retained)
            presentation['source_fields_omitted']+=1;source_omissions()
    if not body['sources']:
        raise ValueError('Complete executive question/answer history exceeds the review input bound')
    for owner,key,value in optional:
        owner[key]=value;presentation['omitted_optional_views'].remove(key)
        if not fits():
            owner.pop(key);presentation['omitted_optional_views'].append(key)
    # Expanded coordinates are restored as a complete optional view.
    if expanded:
        projection=history.pop('citation_projection',None)
        for answer,value in expanded:answer['citations']=value
        if not fits():
            for answer,_ in expanded:answer.pop('citations')
            if projection:history['citation_projection']=projection


def _executive_snapshot(resolved,snapshot,round_number,projection):
    """Add prior main answers as labeled judgments; retain every prior-round question.

    Large answers fail the bounded request instead of silently dropping one of
    the five questions. Source excerpts may shrink; their exact handles survive.
    """
    body=json.loads(snapshot['prompt'])
    previous=[f for f in projection['findings'] if f.get('kind')=='executive_question'
              and f.get('executive_policy_role') != 'historical_handoff_restart']
    dispositions={(d['review_id'],d['finding_id']):d for d in projection['dispositions']}
    questions={q['id']:q for q in _question_records(resolved, include_report_source=True)}
    history=[];main_answers={}
    for finding in previous:
        if finding['executive_round']!=round_number-1:continue
        answer=dispositions.get((finding['review_id'],finding['finding_id']))
        if answer is None:raise ValueError('Every previous executive question needs a validated main answer')
        qid=answer['question_id'];note=questions[qid]
        main_answers[qid]={k:note[k] for k in ('id','revision','question','answer','assessment','scope','gaps',
            'critical_gaps','next_checks','alternatives','limitations','closure_rationale') if k in note}
        history.append({'question':finding['question'],'review_id':finding['review_id'],
                        'finding_id':finding['finding_id'],'main_response':dict(answer)})
    if round_number>1 and len(history)!=EXECUTIVE_QUESTIONS:
        raise ValueError('Previous executive round is incomplete')
    body['executive_followup']={'round':round_number,'round_limit':EXECUTIVE_ROUNDS,
        'questions_required':EXECUTIVE_QUESTIONS,'role':'questions_not_evidence_or_approval',
        'previous_questions':[f['question'] for f in previous],
        'previous_round':{'role':'main_answers_and_dispositions_not_source_evidence',
                          'questions':history,'main_answers':list(main_answers.values())}}
    if len(_json(body))+len(EXECUTIVE_SYSTEM)>MAX_INPUT:
        _budget_executive_body(body)
    prompt=_json(body)
    if len(prompt)+len(EXECUTIVE_SYSTEM)>MAX_INPUT:
        raise ValueError('Complete executive question/answer history exceeds the review input bound')
    retained={f['review_ref'] for source in body['sources'] for f in source['fields']}
    refs={}
    for source in body['sources']:
        for field in source['fields']:
            _remember_field(refs.setdefault(source['source_id'],{}),field['pointer'],field['value'])
    return {**snapshot,'prompt':prompt,'input_limit':MAX_INPUT,
            'refs':refs,
            'review_refs':{k:v for k,v in snapshot['review_refs'].items() if k in retained},
            'input_fingerprint':_input_fingerprint(EXECUTIVE_SYSTEM,body),
            'system':EXECUTIVE_SYSTEM,'executive_round':round_number,
            'previous_questions':body['executive_followup']['previous_questions']}


def review_state(resolved,state=None):
    """Read-only receipt/disposition projection; never dispatches, adopts or changes notes."""
    from .independent_review_state import review_state as project
    return project(resolved,state)


def review_finding(resolved,review_id,finding_id,state=None):
    return next((f for f in review_state(resolved,state)['findings']
                 if f['review_id']==review_id and f['finding_id']==finding_id),None)


def _with_state(result,resolved,max_chars=3500):
    resolved=native_report_review_scope(resolved)
    state=review_state(resolved)
    from .review_navigation import pending_items, response_context
    result.update(review_id=result.get('review_id') or state.get('review_id',''),
                  requires_followup=state['requires_followup'],pending_finding_ids=state['pending_finding_ids'],
                  rejected_findings=state.get('rejected_findings',[]))
    recovered=next((item for item in state.get('recovered_receipts',[])
                    if item['review_id']==result['review_id']),None)
    if recovered:
        result['receipt_recovery']=recovered
        if result.get('status')=='invalid_advice':
            result['status']='received'
            result['context']='독립 검토: 보존 응답의 배열 형식을 복구하고 원래 요청의 근거 좌표를 다시 검증했습니다. 의견의 의미는 조사자의 대조와 처분이 필요하며, 복구만으로 완료를 승인하지 않습니다.'
    result.update({key: state[key] for key in ('progress_fingerprint', 'followup_actionable', 'followup_action', 'diagnostic')
                   if key in state and key not in result})
    if state.get('historical_executive_requests'):
        result['historical_executive_requests'] = state['historical_executive_requests']
    if result.get('status') == 'unavailable':
        result.update(requires_followup=True, followup_actionable=False, followup_action='repair')
        return result
    if state.get('followup_action') == 'compose_report':
        result.update(status='report_composition_required', requires_followup=True,
            followup_actionable=True, followup_action='compose_report',
            recovery={'tool':'forsic_reporting','arguments':{'action':'compose',
                'question_ids':resolved['_report_question_ids']}})
        result['context']='현재 목표의 보고서 본문은 아직 저장되지 않았습니다. 이전 임원 검토 질문과 저장된 답은 보존되어 있으며 새 1회차를 시작하지 않습니다. 다음 compose 호출로 현재 보고서 작성을 준비하고 본문을 저장한 뒤 남은 검토를 이어가세요. '+_json(result['recovery'])
        return result
    if state.get('followup_action') == 'create_continuation_question':
        result.update(pending_items(state))
        result.update(status='continuation_question_required', requires_followup=True,
            followup_actionable=True, followup_action='create_continuation_question', recovery=state['recovery'])
        if state.get('executive_followup'):
            result['executive_followup'] = state['executive_followup']
        intro='현재 사용자 후속 요청에 속한 질문이 아직 없습니다. 이전 보고서 질문은 읽기 전용으로 보존됩니다. 일반 질문 저장 호출로 후속 요청의 범위만 만든 뒤 review_inbox에서 현재 질문 ID와 revision을 읽어 검토 의견에 답하세요. '
        call = _json(result['recovery'])
        if len(intro)+len(call) > max_chars-500:
            call = _json({'tool':'forsic_reporting','arguments':{'action':'review_inbox'}})
        result['context'] = intro+call
        remaining = max_chars-len(result['context'])-1
        if remaining >= 66:
            result['context'] += '\n'+response_context(state,max_chars=remaining)
        return result
    result.update(pending_items(state))
    if state.get('judgment_review_questions') and not state['pending_findings']:
        result['status']='executive_waiting' if state.get('executive_followup') else 'judgment_review_required'
        result['context']='기존 검토 의견의 조사 처분은 보존되어 있습니다. 현재 답의 판단이 철회되거나 근거 없이 바뀌어 재검토가 필요합니다. forsic_reporting(action=review_inbox)의 judgment_review_questions에서 현재 질문을 읽고 question으로 근거 판단을 갱신하세요.'
        return result
    if state.get('executive_followup'):
        progress=state['executive_followup'];result['executive_followup']=progress
        pending=state['pending_findings']
        if pending:
            # Five bounded questions fit the normal native limit. Smaller
            # delivery budgets omit whole values with exact lookup/counts.
            result['context']=response_context(state,max_chars=max_chars)
            return result
        if not progress['complete'] and all(r['complete'] for r in progress['rounds']):
            result['context']='임원 검토 '+str(len(progress['rounds']))+'/3의 다섯 답을 보존했습니다. 다음 native 최종 경계에서 다음 질문을 받습니다. 질문은 증거나 완료 승인이 아닙니다.'
            return result
        if progress['complete']:
            if resolved.get('_report_question_ids') is not None and not state['final_review_current']:
                result['context']='임원 독자 3회·15개 질문의 답과 처분은 보존했습니다. 현재 선택 보고서 본문은 별도 최종 검토가 필요합니다. 다음 native 최종 경계에서 기존 ordinary 예산으로 현재 본문만 검토하며 3회 질문을 다시 시작하지 않습니다.'
                ordinary = [request for _, request in _receipts(resolved) if not request.get('executive_round')]
                limit = _call_budget(resolved)
                if limit is not None and len(ordinary) >= limit:
                    result.update(status='budget_exhausted', followup_actionable=False, followup_action='repair',
                        diagnostic={'code':'final_review_budget', 'phase':'final_packet',
                            'reason':f'Current report review remains incomplete; the ordinary review budget of {limit} calls is exhausted.',
                            'recovery':{'tool':'forsic_reporting','arguments':{'action':'review_inbox'}}})
                return result
            result['status']='received'
            result['context']='임원 독자 3회·15개 질문의 현재 답과 근거·한계 처분을 보존했습니다. 후속 대화의 완료이며 독립 승인·전체 자료 검사 완료가 아닙니다.'
            return result
        if progress.get('status')=='blocked' and not state['pending_findings']:
            # Availability disposition records a limitation, not the missing
            # questions. Keep this terminal partial outcome without replaying an
            # unknown request or forcing an endless main-agent continuation.
            result['context']=('임원 독자 3회·15개 질문은 미완료입니다. 검토 응답의 가용성 한계는 기록했지만 '
                '요청한 질문과 답이 모두 수신된 것은 아닙니다. 같은 미확정 요청을 재전송하지 말고 '
                '부분 결과와 검토 중단 상태를 명시하세요.\n'+result['context'])[:max_chars]
            return result
    if state['pending_findings']:
        result['context']=response_context(state,max_chars=max_chars)
        return result
    if state.get('rejected_findings'):
        failures='\n거절된 검토 의견은 증거가 아닙니다. 같은 검토를 재호출하지 말고 기존 원문 조회로 복구하세요: '+ '; '.join(
            str(f.get('index','?'))+': '+f['reason'] for f in state['rejected_findings'])
        result['context']=(result['context'][:max(0,max_chars-len(failures))]+failures)[:max_chars]
    return result


def _advice(request,response,max_chars):
    status=response.get('status','delivery_unconfirmed') if response else 'delivery_unconfirmed'
    if request.get('schema_version') not in (2,3):status='historical_unvalidated'
    labels={'received':'의견 수신','delivery_unconfirmed':'전달 여부 미확인','incomplete':'응답 미완료',
            'historical_unvalidated':'과거 기록·정확 인용 미검증','stale':'관측 변경으로 재검토 필요','invalid_advice':'응답 근거 참조 확인 실패','cancelled':'중단됨'}
    head='독립 검토: '+labels.get(status,'확인 불가')+' · 조언이며 조사 완료 승인이나 증거가 아닙니다. 기록 '+request['request_id'][:12]
    if status!='received':
        result=_result(status,head+' 제공된 기록의 중요한 미검토 범위를 확인하세요. 같은 자료는 자동 재전송하지 않습니다.',max_chars)
        result['review_id']=request['request_id'];return result
    lines=[head];shown=0
    for f in response.get('findings',[]):
        short=lambda value,n:value if len(value)<=n else value[:n]+'…'
        line=f"\n- [{f.get('finding_id','historical')}] {short(f['question'],180)} · unadopted advice; forsic_reporting(action=review_inbox) for source and proposal."
        if len(''.join(lines))+len(line)>max_chars-70:break
        lines.append(line);shown+=1
    omitted=len(response.get('findings',[]))-shown
    if omitted:lines.append('\n의견 '+str(omitted)+'개 생략. 전체 의견은 위 검토 ID의 비공개 응답 기록에 보존됩니다.')
    if not response.get('findings'):lines.append(' 제공된 발췌에서 추가 질문을 제시하지 않았습니다. 전체 범위 검증을 뜻하지 않습니다.')
    result=_result(status,''.join(lines),max_chars);result['review_id']=request['request_id'];return result


def _latest_result(resolved,max_chars=3500):
    try:
        resolved=native_report_review_scope(resolved)
        receipts=_receipts(resolved)
        receipts=[(p,r) for p,r in receipts if r.get('stage')!='final'
                  or r.get('report_question_ids')==resolved.get('_report_question_ids')]
        if not receipts:return _result('not_reviewed','',max_chars)
        path,request=receipts[-1];response=_response_for(path,request)
        if request.get('executive_round'):
            # Main answers intentionally change the pre-answer snapshot. Their
            # validated current dispositions, rather than another review call,
            # determine whether this bounded dialogue is complete.
            return _with_state(_advice(request,response,max_chars),resolved,max_chars)
        snapshot=_collect(resolved)
        if (snapshot['fingerprint']!=request['material_fingerprint'] or request.get('stage')=='final'
                and snapshot['evaluation_fingerprint']!=request.get('evaluation_fingerprint')):
            result=_result('stale','독립 검토는 이전 관측에 대한 조언입니다. 새 자료를 포함한 검토는 아직 반영되지 않았습니다. 기존 중요 의견은 명시적 처분 전까지 남습니다.',max_chars)
            result['review_id']=request['request_id'];return _with_state(result,resolved,max_chars)
        return _with_state(_advice(request,response,max_chars),resolved,max_chars)
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        return _unavailable(exc, 'receipt_projection', max_chars)


def latest_advice(resolved,max_chars=3500):
    """Read-only string for before_turn; no model call or automatic adoption."""
    return advisory_at_boundary(resolved,max_chars)['context']


def goal_advisory_context(session_id='',goal_id='',goal_text='',turns_used=0,cancel_check=None,max_chars=3500,
                          *,intake_root,native_db=None,enabled=False,phase='checkpoint',stage='checkpoint',draft_response='',nonblocking=False,
                          executive_followup=False,report_question_ids=None,**_):
    """Single bounded auxiliary call at an opted-in active native goal checkpoint."""
    if not enabled:return _result('disabled','',max_chars)
    if phase!='checkpoint' or stage not in ('checkpoint','exploration','final'):return _result('deferred','',max_chars)
    if nonblocking and stage=='final':raise ValueError('Only early exploration may overlap collection')
    resolved=None
    try:
        from .investigation_context import _native_path
        native_db=_native_path(native_db)
        resolved=_active(session_id,goal_id,goal_text,intake_root,native_db)
        try:
            if report_question_ids is not None:
                if stage != 'final':raise ValueError('Report selection applies only to final review')
                resolved=report_review_scope(resolved,report_question_ids)
            else:
                resolved=native_report_review_scope(resolved)
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
            # Old completed rounds cannot certify an unreadable or foreign
            # current report selection. Preserve them without marking success.
            return _unavailable(exc, 'report_selection', max_chars)
        if callable(cancel_check) and cancel_check():return _result('cancelled','',max_chars)
        # A prior dialogue must never restart at round one merely because the
        # new report has not been composed yet. Preparation is the actionable
        # next step; no auxiliary dispatch occurs on this boundary.
        projected = review_state(resolved)
        if projected.get('followup_action') in ('compose_report', 'create_continuation_question'):
            return _with_state(_result('report_composition_required','',max_chars),resolved,max_chars)
        lock_path=resolved['case'].output/('independent-review-'+_goal_key(resolved)+'.lock')
        from .independent_review_overlap import pending_for
        pending=pending_for(str(lock_path),native_db=native_db,session_ids=resolved['session_lineage'])
        if pending and pending[0]!=str(lock_path) and stage!='final':
            return _in_progress(resolved,max_chars,pending[1])
        if stage=='final':
            from .independent_review_overlap import wait_existing
            waited=wait_existing(pending[0] if pending else str(lock_path),cancel_check=cancel_check)
            if waited is not None:
                if waited=='collected':
                    value=advisory_at_boundary(resolved,max_chars)
                    value['requires_followup']=True
                    value['context']=('진행 중이던 탐색 검토를 수신했습니다. 주 조사에서 의견을 확인한 뒤 다음 최종 답 경계에서 현재 초안을 별도로 검토합니다.\n'+value['context'])[:max_chars]
                    return value
                value=_in_progress(resolved,max_chars)
                if waited=='cancelled':value['status']='cancelled'
                else:
                    value.update(followup_actionable=False, followup_action='repair',
                        diagnostic={'code':'review_deadline', 'phase':'final_wait',
                            'reason':'The existing review request reached its original deadline; its outcome remains unconfirmed.',
                            'recovery':{'tool':'forsic_reporting','arguments':{'action':'review_inbox'}}})
                return value
        # Settled executive questions and their answers are readable without
        # rebuilding a model packet. Packet size must not hide the actionable
        # inbox, or break a checkpoint before the next native final boundary.
        if stage != 'final' and any(r.get('executive_round') for _,r in _receipts(resolved)):
            return _latest_result(resolved,max_chars)
        if stage == 'final' and resolved.get('_report_question_ids') is not None and projected.get('final_review_current'):
            # The reviewed report dependencies are unchanged. Optional packet
            # sampling, clock/inventory growth or disposition text need no call.
            # _with_state rechecks followthrough; finalization checks its own CAS.
            prior = next(((path, request) for path, request in _receipts(resolved)
                          if request['request_id'] == projected.get('latest_final_review_id')), None)
            if prior:
                path, request = prior
                return _with_state(_advice(request, _response_for(path, request), max_chars), resolved, max_chars)
        snapshot=_collect(resolved,stage,draft_response)
        if stage!='final' and (int(turns_used)<1 or not _has_saved_answer(resolved)):return _latest_result(resolved,max_chars)
        if not snapshot['refs']:return _with_state(_result('unavailable','독립 검토에 제시할 실제 원문 관측이 부족합니다.',max_chars),resolved,max_chars)
        fd=os.open(lock_path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return _in_progress(resolved,max_chars)
            receipts=_receipts(resolved)
            executive_mode=executive_followup or any(r.get('executive_round') for _,r in receipts)
            if stage!='final' and any(r.get('executive_round') for _,r in receipts):
                return _latest_result(resolved,max_chars)
            if executive_mode and stage=='final':
                projection=review_state(resolved)
                rounds, _ = executive_receipt_sequence(resolved, receipts)
                if projection['status']=='unavailable':
                    return _with_state(_result('unavailable','임원 후속 검토 상태를 확인하지 못했습니다.',max_chars),resolved,max_chars)
                if projection.get('judgment_review_questions') and not projection['pending_findings']:
                    return _with_state(_result('executive_waiting','현재 질문의 근거 판단을 갱신하세요.',max_chars),resolved,max_chars)
                if projection['pending_findings']:
                    return _with_state(_result('executive_waiting','기존 검토 질문을 주 조사에서 먼저 답하고 근거 또는 한계를 저장하세요.',max_chars),resolved,max_chars)
                if rounds:
                    prior_path,prior=rounds[-1];response=_response_for(prior_path,prior)
                    recovered=any(r['review_id']==prior['request_id'] for r in projection.get('recovered_receipts',[]))
                    if not response or response.get('status')!='received' and not recovered:
                        return _with_state(_advice(prior,response,max_chars),resolved,max_chars)
                    progress=projection.get('executive_followup',{})
                    if not all(r['complete'] for r in progress.get('rounds',[])):
                        return _with_state(_result('executive_waiting','이전 임원 검토의 다섯 질문을 모두 답해야 다음 회차로 진행합니다.',max_chars),resolved,max_chars)
                if len(rounds)>=EXECUTIVE_ROUNDS:
                    if projection['final_review_current']:
                        return _with_state(_result('received','임원 독자 3회·15개 질문의 현재 답과 근거·한계 처분을 보존했습니다. 독립 승인이나 전체 자료 검사 완료를 뜻하지 않습니다.',max_chars),resolved,max_chars)
                    # Completed dialogue is retained. A changed authored report
                    # gets an ordinary final review within the existing budget,
                    # never a fresh three-round dialogue or a freshness waiver.
                else:
                    snapshot=_executive_snapshot(resolved,snapshot,len(rounds)+1,projection)
            limit=_call_budget(resolved)
            matched=next(((p,r) for p,r in receipts if r.get('stage','checkpoint')==stage and
                          (stage != 'final' or r.get('identity') == _identity(resolved)) and
                          _retained_input_fingerprint(r)==snapshot['input_fingerprint']),None)
            if matched:
                p,r=matched;response=_response_for(p,r)
                return _with_state(_advice(r,response,max_chars),resolved,max_chars)
            if stage!='final':
                projection=review_state(resolved)
                if projection['status']=='unavailable':
                    return _with_state(_result('unavailable','Independent review state is unavailable; inspect existing records before requesting another review.',max_chars),resolved,max_chars)
                if projection['pending_findings']:
                    return _latest_result(resolved,max_chars)
            ordinary=[(p,r) for p,r in receipts if not r.get('executive_round')]
            if not snapshot.get('executive_round') and limit is not None and len(ordinary)>=limit:
                return _with_state(_result('budget_exhausted',f'독립 검토의 목표별 {limit}회 한도를 사용했습니다. 보존된 의견의 처분과 현재 한계를 반영해 판단하세요.',max_chars),resolved,max_chars)
            if any(not path.with_name(path.name.replace('-request.json','-response.json')).exists()
                   for path,_ in receipts):
                unknown=_with_state(_result('delivery_unconfirmed',
                    '이전 독립 검토 요청의 완료 여부를 확인하지 못했습니다. 같은 요청을 재전송하지 말고 보존 기록과 한계를 처분하세요.',max_chars),resolved,max_chars)
                if unknown['requires_followup']:return unknown
            if stage!='final' and limit is not None and len(ordinary)>=limit-1:
                return _with_state(_result('final_reserved','마지막 독립 검토 호출은 최종 답 검토를 위해 남겨 두었습니다.',max_chars),resolved,max_chars)
            if receipts and stage!='final':
                previous=receipts[-1][1]
                # More reads alone never justify another round of questions.
                if (snapshot['evaluation_fingerprint']==previous.get('evaluation_fingerprint')
                        or int(turns_used)-previous['turns_used']<4):
                    return _latest_result(resolved,max_chars)
            from hermes_cli.config import load_config_readonly
            from .connections import _lock as connection_lock, review_connection_snapshot
            with connection_lock:
                route,credential=review_connection_snapshot(load_config_readonly())
            system=snapshot.get('system',SYSTEM)
            identity={**_identity(resolved),'model':route,'stage':stage,'system':system,'prompt':snapshot['prompt']}
            rid=_hash(identity);prefix='independent-review-'+_goal_key(resolved)+'-'+rid
            request_path=resolved['case'].output/(prefix+'-request.json')
            response_path=resolved['case'].output/(prefix+'-response.json')
            # Recheck evidence, binding and cancellation immediately before recording dispatch.
            fresh=_active(session_id,goal_id,goal_text,intake_root,native_db)
            fresh=report_review_scope(fresh,resolved.get('_report_question_ids'))
            fresh_snapshot=_collect(fresh,stage,draft_response,runtime_clock=snapshot['runtime_clock'])
            if snapshot.get('executive_round'):
                fresh_snapshot=_executive_snapshot(fresh,fresh_snapshot,snapshot['executive_round'],review_state(fresh))
            if stage!='final':
                fresh_state=review_state(fresh)
                if (not _has_saved_answer(fresh) or fresh_state['pending_findings']
                        or fresh_state['status']=='unavailable'):return _latest_result(fresh,max_chars)
            if _identity(fresh)!=_identity(resolved) or fresh_snapshot['input_fingerprint']!=snapshot['input_fingerprint']:
                return _result('stale','검토 직전 관측이나 목표가 바뀌어 호출하지 않았습니다.',max_chars)
            if callable(cancel_check) and cancel_check():return _result('cancelled','',max_chars)
            request={'request_id':rid,'identity':_identity(resolved),'session_id':session_id,'model':route,
                'execution_ref':{'session_id':session_id,'goal_id':goal_id,'conversation_id':resolved['conversation_id']},
                'created_ns':time.time_ns(),'turns_used':int(turns_used),'source_count':snapshot['count'],
                'stage':stage,'schema_version':3,'execution_mode':'overlap' if nonblocking else 'boundary',
                'snapshot_revision':_hash({k:snapshot[k] for k in ('fingerprint','evaluation_fingerprint','input_fingerprint')}),
                'material_fingerprint':snapshot['fingerprint'],
                'evaluation_fingerprint':snapshot['evaluation_fingerprint'],'input_fingerprint':snapshot['input_fingerprint'],'messages':[{'role':'system','content':system},
                {'role':'user','content':snapshot['prompt']}],'delivery_status':'unconfirmed','automatic_adoption':False}
            if snapshot.get('executive_round'):request['executive_round']=snapshot['executive_round']
            if resolved.get('_report_question_ids') is not None:
                request['report_question_ids']=resolved['_report_question_ids']
                request['report_draft_fingerprint']=snapshot['report_draft_fingerprint']
            request['input_manifest']={'session_lineage':resolved['session_lineage'],
                'selected_sources':[{'source_id':sid,'pointers':sorted(pointers)} for sid,pointers in snapshot['refs'].items()],
                'omitted':json.loads(snapshot['prompt'])['omitted'],
                'input_characters':len(system)+len(snapshot['prompt']),'input_limit':snapshot.get('input_limit',MAX_INPUT)}
            _immutable(request_path,request)
            complete_options=dict(session_id=session_id,goal_id=goal_id,goal_text=goal_text,
                intake_root=intake_root,native_db=native_db,stage=stage,draft_response=draft_response,
                max_chars=max_chars,credential=credential)
            if nonblocking:
                from .independent_review_overlap import start
                # flock follows this duplicated open description until the worker
                # commits its response and closes the final descriptor.
                lease=os.fdopen(os.dup(lock.fileno()),'a')
                def still_bound():
                    fresh=_active(session_id,goal_id,goal_text,intake_root,native_db)
                    return _identity(fresh)==_identity(resolved)
                start(str(lock_path),request_id=rid,session_ids=resolved['session_lineage'],
                    native_db=native_db,lease=lease,cancel_check=cancel_check,still_bound=still_bound,
                    complete=lambda cancelled,deadline:_complete_review(resolved,snapshot,request,request_path,
                        response_path,cancel_check=cancelled,deadline=deadline,**complete_options))
                return _in_progress(resolved,max_chars,rid)
            return _complete_review(resolved,snapshot,request,request_path,response_path,
                cancel_check=cancel_check,**complete_options)
    except (OSError,ValueError,KeyError,TypeError,ImportError,AttributeError,sqlite3.Error) as exc:
        return _packet_failure(exc, 'final_packet' if stage == 'final' else 'checkpoint_packet', resolved, max_chars)


def _complete_review(resolved,snapshot,request,request_path,response_path,*,session_id,goal_id,goal_text,
                     intake_root,native_db,stage,draft_response,cancel_check,max_chars,credential,deadline=None):
    """Consume exactly the recorded bounded input, whether foreground or overlapping."""
    rid=request['request_id'];route=request['model']
    response={'request_id':rid,'status':'delivery_unconfirmed','findings':[],
              'response_version':1,'request_sha256':hashlib.sha256(request_path.read_bytes()).hexdigest()};stream=None;parts=[];size=0
    try:
        from agent.auxiliary_client import call_llm,aux_interrupt_protection,aux_stream_deadline
        deadline=deadline if deadline is not None else time.monotonic()+600
        if callable(cancel_check) and cancel_check():raise InterruptedError('Independent review cancelled')
        if _read(request_path)!=request:raise ValueError('Independent review request changed before dispatch')
        with aux_interrupt_protection(cancel_check=cancel_check),aux_stream_deadline(deadline):
            # Native stream=True bypasses auxiliary retries/fallbacks; SDK retries are disabled natively.
            stream=call_llm(task='report_review',provider='custom',model=route['default'],base_url=route['base_url'],
                api_key=credential,messages=request['messages'],temperature=0,
                max_tokens=4000 if request.get('executive_round') else 1800,timeout=600,
                reasoning_config={'enabled':False},stream=True,stream_options={'include_usage':True},api_mode='chat_completions')
            for chunk in stream:
                if time.monotonic()>deadline:raise TimeoutError('Independent review deadline')
                if callable(cancel_check) and cancel_check():raise InterruptedError('Independent review cancelled')
                usage=getattr(chunk,'usage',None)
                if usage is not None:response['usage']=usage.model_dump() if hasattr(usage,'model_dump') else dict(usage)
                for choice in getattr(chunk,'choices',[]):
                    if getattr(choice,'index',0)!=0:continue
                    content=getattr(getattr(choice,'delta',None),'content',None)
                    if isinstance(content,str):
                        size+=len(content)
                        if size>MAX_RESPONSE:raise ValueError('Independent advice exceeds response budget')
                        parts.append(content)
                    if getattr(choice,'finish_reason',None):response['finish_reason']=choice.finish_reason
        response['raw_advice']=''.join(parts)
        if response.get('finish_reason')!='stop':response['status']='incomplete'
        else:
            response['rejected_findings']=[]
            response['findings']=_parse(response['raw_advice'],snapshot['refs'],rid,response['rejected_findings'],
                                        review_refs=snapshot['review_refs'],executive_round=request.get('executive_round',0),
                                        previous_questions=snapshot.get('previous_questions',()))
            response['status']='invalid_advice' if response['rejected_findings'] and not response['findings'] else 'received'
        try:
            fresh=_active(session_id,goal_id,goal_text,intake_root,native_db)
            fresh=report_review_scope(fresh,resolved.get('_report_question_ids'))
            fresh_snapshot=_collect(fresh,stage,draft_response,runtime_clock=snapshot['runtime_clock'])
            if request.get('executive_round'):
                fresh_snapshot=_executive_snapshot(fresh,fresh_snapshot,request['executive_round'],review_state(fresh))
            current=_identity(fresh)==_identity(resolved) and fresh_snapshot['input_fingerprint']==snapshot['input_fingerprint']
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):current=False
        if not current:
            response['status']='stale'
    except BaseException as exc:
        response.update(error_type=type(exc).__name__,raw_advice=''.join(parts))
        if isinstance(exc,(InterruptedError,KeyboardInterrupt)):response['status']='cancelled'
        elif response.get('finish_reason')=='stop':response['status']='invalid_advice'
        if isinstance(exc,(SystemExit,GeneratorExit)):raise
    finally:
        if stream is not None and callable(getattr(stream,'close',None)):
            try:stream.close()
            except Exception:response['close_error']=True
        from datetime import datetime, timezone
        response['observed_at']=datetime.now(timezone.utc).isoformat()
        response['execution_ref']=request.get('execution_ref',{'session_id':session_id,'goal_id':goal_id})
        response['finished_ns']=time.time_ns();_immutable(response_path,response)
    return _with_state(_advice(request,response,max_chars),resolved,max_chars)


def inflight_review_ids(resolved):
    from .independent_review_overlap import inflight
    path=resolved['case'].output/('independent-review-'+_goal_key(resolved)+'.lock')
    request_id=inflight(str(path))
    return [request_id] if request_id else []


def _in_progress(resolved,max_chars=3500,review_id=''):
    result=_with_state(_result('in_progress',
        '독립 검토가 고정된 관측을 검토 중입니다. 주 조사는 계속할 수 있으며 결과는 다음 도구·목표 경계에서 확인합니다. 실행 중인 검토를 통과나 가용성 한계 처분으로 대신하지 마세요.',max_chars),resolved,max_chars)
    result.update(requires_followup=True, followup_action='wait', followup_actionable=True,
                  review_id=review_id or next(iter(inflight_review_ids(resolved)),result['review_id']))
    return result


def cancel_pending_reviews(*,native_db,session_id='',interrupted=False,failed=False,dispose=False,**_):
    if not (dispose or interrupted or failed):return
    from .independent_review_overlap import cancel_scope
    from .investigation_context import _native_path
    cancel_scope(native_db=_native_path(native_db),session_id=session_id or None)


def advisory_at_boundary(resolved,max_chars=3500):
    """Collect settled receipts without dispatching or waiting on model work."""
    if inflight_review_ids(resolved):return _in_progress(resolved,max_chars)
    return _latest_result(resolved,max_chars)
