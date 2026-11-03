"""Read-only case history, separate from the native current review queue.

Enumerate retained receipts and original note revisions; never start a review or
adopt a historical answer. Pagination is over the complete retained history.
"""
from contextlib import closing
import hashlib
import json
import sqlite3


def _conversation_reader(resolved):
    from .investigation_context import _compression_link
    cache = {}
    def conversation(session_id):
        if not isinstance(session_id, str) or not session_id:
            return None
        if session_id in cache:
            return cache[session_id]
        native = resolved.get('_view_native_db')
        if native is None:
            lineage = resolved['session_lineage']
            value = (lineage[-1], lineage) if session_id in lineage else None
        else:
            value = None
            with closing(sqlite3.connect(native.as_uri() + '?mode=ro', uri=True)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                chain, seen = [], set()
                current = session_id
                while current and current not in seen:
                    seen.add(current)
                    row = db.execute('SELECT id,parent_session_id,end_reason,source,model_config '
                                     'FROM sessions WHERE id=?', (current,)).fetchone()
                    if row is None:
                        break
                    entry = dict(row)
                    if chain and not _compression_link(chain[-1], entry):
                        value = (chain[-1]['id'], [item['id'] for item in chain])
                        break
                    chain.append(entry)
                    current = entry.get('parent_session_id')
                    if not current:
                        value = (chain[-1]['id'], [item['id'] for item in chain])
        cache[session_id] = value
        return value
    return conversation


def _responses(case, conversation):
    """Keep changed answers and their original revisions; collapse carried copies."""
    from .report_driven.review_responses import validate_saved
    from .report_driven.investigation_state import definition_version
    # Extract note metadata only, never tool/source result bodies. Release the
    # SQL read transaction before validating citations through other connections.
    with closing(sqlite3.connect(case.db.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute("SELECT id,time,json_object('note_id',json_extract(data,'$.note_id'),"
            "'revision',json_extract(data,'$.revision'),'session_id',coalesce(nullif(json_extract(data,'$.session_id'),''),session),"
            "'goal_id',json_extract(data,'$.goal_id'),'question',json_extract(data,'$.question'),"
            "'target_proposition',json_extract(data,'$.target_proposition'),'scope',json_extract(data,'$.scope'),"
            "'definition_version',json_extract(data,'$.definition_version'),'investigation',"
            "json_object('review_responses',json(coalesce(json_extract(data,'$.investigation.review_responses'),'[]')))) "
            "FROM events WHERE kind='note' ORDER BY rowid").fetchall()
    notes = [(eid, ts, json.loads(raw)) for eid, ts, raw in rows]
    for _, _, note in notes:
        if note.get('scope') is None:
            note.pop('scope', None)
    latest = {note['note_id']: note for _, _, note in notes if note.get('note_id')}
    result, previous_by_note, latest_response, unlinked = {}, {}, {}, []
    for event_id, timestamp, note in notes:
        previous = previous_by_note.get(note.get('note_id'), set())
        raw_responses = (note.get('investigation') or {}).get('review_responses') or []
        previous_by_note[note.get('note_id')] = {json.dumps(response, sort_keys=True, ensure_ascii=False)
            for response in raw_responses} if isinstance(raw_responses, list) else set()
        owner = conversation(note.get('session_id'))
        if not note.get('goal_id') or not note.get('note_id'):
            continue
        question = dict(note, id='Q-' + note['note_id'])
        question['definition_version'] = definition_version(note, case.config.get('scope', ''))
        responses = (note.get('investigation') or {}).get('review_responses') or []
        if not isinstance(responses, list):
            continue
        for response in responses:
            if not isinstance(response, dict) or any(not isinstance(response.get(k), str) for k in ('review_id', 'finding_id', 'reason', 'disposition')):
                continue
            key = (owner[0] if owner else note.get('session_id'), note['goal_id'], response['review_id'], response['finding_id'])
            signature = json.dumps(response, sort_keys=True, ensure_ascii=False)
            if signature in previous:
                continue
            valid = False
            try:
                if owner:
                    validate_saved(case, None, response, lineage=owner[1], question=question)
                    valid = True
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                pass
            current = latest[note['note_id']]
            current_owner = conversation(current.get('session_id'))
            current_definition = definition_version(current, case.config.get('scope', ''))
            current_response = (current.get('investigation') or {}).get('review_responses') or []
            applicable = (valid and owner and current_owner and current_owner[0] == owner[0]
                and current.get('goal_id') == note['goal_id']
                and current_definition == question['definition_version'] and response in current_response)
            item = {k: response[k] for k in ('disposition', 'reason', 'limitation', 'citation_refs') if k in response}
            item.update(question_id=question['id'], revision=note.get('revision'), note_event_id=event_id,
                        recorded_at=timestamp, validation='validated_saved_response' if valid else 'unverified_saved_response',
                        applicability='current_note' if applicable else 'historical_note', semantic_approval=False)
            response_key = (note['note_id'], response['review_id'], response['finding_id'])
            if response_key in latest_response:
                latest_response[response_key]['applicability'] = 'historical_note'
            latest_response[response_key] = item
            if owner:
                result.setdefault(key, []).append(item)
            else:
                unlinked.append({**item, 'review_id': response['review_id'], 'finding_id': response['finding_id'],
                    'question': note.get('question'), 'session_id': note.get('session_id')})
    return result, unlinked


def project_history(resolved, *, profile='', offset=0, limit=30, snapshot=None):
    from . import independent_review as review
    from .independent_review_state import _presented, _presented_handles
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Invalid history page')
    case = resolved['case']
    conversation = _conversation_reader(resolved)
    answers, unlinked = _responses(case, conversation)
    receipts, items, issues, versions = [], [], [], []
    for path in sorted(case.output.glob('independent-review-*-request.json')):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            request = review._read(path)
            identity = request.get('identity') or {}
            if identity.get('case_id') != resolved['case_id'] or identity.get('case_manifest') != str(case.manifest):
                continue
            response = review._response_for(path, request)
            versions.append([request, response])
            receipts.append((request.get('created_ns', 0), path.name, request, response))
        except (OSError, ValueError, KeyError, TypeError):
            issues.append({'status': 'unavailable_receipt', 'message': '보존된 검토 기록 하나를 확인하지 못했습니다.'})
    for review_number, (_, _, request, response) in enumerate(sorted(receipts), 1):
        rid = request['request_id']
        identity = request['identity']
        metadata = dict(review_number=review_number, review_id=rid, goal_id=identity.get('goal_id'),
            session_id=identity.get('conversation_id'), stage=request.get('stage', 'checkpoint'),
            executive_round=request.get('executive_round'), created_ns=request.get('created_ns'),
            report_question_ids=request.get('report_question_ids'),
            current_goal=bool(identity.get('goal_id')) and identity.get('goal_id') == resolved.get('goal_id'),
            currentness='not_revalidated', semantic_approval=False)
        if not response or response.get('finish_reason') != 'stop':
            issues.append({**metadata, 'status': 'response_unavailable', 'message': '완전한 검토 응답이 보존되어 있지 않습니다.'})
            continue
        try:
            body = json.loads(next(m['content'] for m in request['messages'] if m.get('role') == 'user'))
            draft = body.get('analyst_state', {}).get('report_draft')
            if isinstance(draft, dict):
                metadata['report_draft_revision'] = draft.get('draft_revision')
            metadata['report_version'] = request.get('report_draft_fingerprint')
            rejected = []
            findings = review._parse(response['raw_advice'], _presented(request), rid, rejected,
                review_refs=_presented_handles(request), executive_round=request.get('executive_round', 0),
                previous_questions=body.get('executive_followup', {}).get('previous_questions', ()))
            for rejected_finding in rejected:
                issues.append({**metadata, 'status': 'unvalidated_finding',
                    'message': '검토 의견의 원문 연결을 검증하지 못했습니다.',
                    'question': rejected_finding.get('question'), 'reason': rejected_finding.get('reason')})
            owner = conversation(request.get('session_id'))
            for finding in findings:
                responses = answers.get((owner[0], identity.get('goal_id'), rid, finding['finding_id']), []) if owner and identity.get('conversation_id') == owner[0] else []
                current_answers = [answer for answer in responses if answer['applicability'] == 'current_note']
                items.append({**finding, **metadata, 'responses': responses,
                    'status': 'answered' if current_answers else 'historical_answer' if responses else 'pending',
                    'disposition_validation': 'validated_saved_response' if current_answers else 'unverified_or_not_recorded'})
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            issues.append({**metadata, 'status': 'unvalidated_receipt', 'message': '보존된 검토 응답의 형식을 검증하지 못했습니다.'})
    digest = hashlib.sha256(json.dumps([versions, items, issues, unlinked], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if snapshot is not None and snapshot != digest:
        raise ValueError('History changed; refresh from the first page')
    total = len(items)
    return dict(case_id=resolved['case_id'], session_id=resolved.get('requested_session_id'), profile=profile,
        scope='case', snapshot=digest, items=items[offset:offset+limit], total=total, offset=offset,
        next_offset=offset+limit if offset+limit < total else None,
        counts=dict(total=total, answered=sum(item['status'] == 'answered' for item in items),
            pending=sum(item['status'] == 'pending' for item in items),
            historical_answer=sum(item['status'] == 'historical_answer' for item in items),
            historical_goal=sum(not item['current_goal'] for item in items), receipts=len(receipts)),
        issues=issues, unlinked_responses=unlinked, currentness='not_revalidated')
