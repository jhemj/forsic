"""Read-only public chat projection of immutable review receipts and saved dispositions.

This module never dispatches review, resumes goals, or writes a conversation. Cached
results are invalidated by retained ledger/receipt versions, not by a polling clock.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import heapq
import json
import sqlite3
import threading

from . import independent_review as review
from .independent_review_state import _presented, _presented_handles

MAX_RECEIPTS = 24
MAX_SCAN_FILES = 256
PAGE_SIZE = 40
_CACHE = OrderedDict()
_SELECTION_CACHE = OrderedDict()
_LOCK = threading.Lock()
_STATUS = {
    'in_progress': '검토 중', 'delivery_unconfirmed': '검토 응답 수신 미확인',
    'invalid_advice': '검토 응답 형식 또는 참조 확인 실패', 'incomplete': '검토 응답 불완전',
    'cancelled': '검토 취소됨', 'stale': '이전 자료 기준 검토',
    'unavailable': '검토 기록을 확인할 수 없음', 'received': '추가 질문 없음',
}


def _version(path):
    try:
        stat = path.stat()
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    except FileNotFoundError:
        return None


def _time(request, response):
    observed = (response or {}).get('observed_at')
    if isinstance(observed, str):
        try:
            parsed = datetime.fromisoformat(observed.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                return parsed.isoformat(), 'response_observed'
        except ValueError:
            pass
    ns = request.get('created_ns')
    if type(ns) is int and ns > 0:
        try:
            return datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat(), 'request_recorded'
        except (ValueError, OverflowError, OSError):
            pass
    return None, 'unknown'


def _project(resolved, paths):
    case = resolved['case']
    items, questions_by_goal = [], {}
    # Exact source hashing occurs only when the retained ledger/goal version changes.
    snapshot = None
    fingerprint_checked = False
    inflight = set(review.inflight_review_ids(resolved))
    receipts = [(path, review._read(path)) for path in paths]
    for path, request in sorted(receipts, key=lambda pair: (pair[1].get('created_ns', 0), pair[0].name)):
        identity = request.get('identity')
        if not isinstance(identity, dict):
            raise ValueError('Invalid review identity')
        if (identity.get('case_id') != resolved['case_id'] or
                identity.get('case_manifest') != str(case.manifest) or
                identity.get('conversation_id') != resolved['conversation_id'] or
                request.get('session_id') not in resolved['session_lineage']):
            continue
        rid, goal = request['request_id'], identity.get('goal_id')
        if not isinstance(rid, str) or not rid or not isinstance(goal, str):
            raise ValueError('Invalid review ownership identity')
        response = review._response_for(path, request)
        status = response.get('status', 'delivery_unconfirmed') if response else ('in_progress' if rid in inflight else 'delivery_unconfirmed')
        current_goal = goal == resolved['goal_id']
        if current_goal and not fingerprint_checked:
            snapshot = review.freshness(resolved) if resolved.get('goal') is not None else None
            fingerprint_checked = True
        freshness = 'unknown'
        if not current_goal or status == 'stale':
            freshness = 'stale'
        elif snapshot:
            freshness = 'current' if (snapshot['fingerprint'] == request.get('material_fingerprint') and
                snapshot['evaluation_fingerprint'] == request.get('evaluation_fingerprint')) else 'stale'
        timestamp, basis = _time(request, response)
        stage = request.get('stage', 'checkpoint')
        number = request.get('executive_round')
        if stage not in ('exploration', 'checkpoint', 'final'):
            status = 'invalid_advice'
        if number is not None:
            if (type(number) is not int or not 1 <= number <= review.EXECUTIVE_ROUNDS or stage != 'final'):
                status = 'invalid_advice'
        base = dict(case_id=resolved['case_id'], conversation_id=resolved['conversation_id'], goal_id=goal,
            review_id=rid, stage=stage if stage in ('exploration', 'checkpoint', 'final') else 'unknown',
            round=number if type(number) is int and 1 <= number <= review.EXECUTIVE_ROUNDS else None,
            observed_at=timestamp, time_basis=basis, freshness=freshness,
            historical=not current_goal, semantic_approval=False, response_ref=None)
        findings, rejected = [], []
        parsed = False
        if request.get('schema_version') in (2, 3) and response and response.get('finish_reason') == 'stop' and status not in ('invalid_advice', 'incomplete', 'cancelled'):
            try:
                body = json.loads(next(m['content'] for m in request['messages'] if m.get('role') == 'user'))
                findings = review._parse(response['raw_advice'], _presented(request), rid, rejected,
                    review_refs=_presented_handles(request), executive_round=number or 0,
                    previous_questions=body.get('executive_followup', {}).get('previous_questions', ()))
                parsed = True
            except (ValueError, KeyError, TypeError, StopIteration):
                status = 'invalid_advice'
        if status == 'received' and (not parsed or rejected and not findings):
            status = 'invalid_advice'
        base['receipt_state'] = status if status in _STATUS else 'unavailable'
        if goal not in questions_by_goal:
            owned = dict(resolved, goal_id=goal)
            questions_by_goal[goal] = review._question_records(owned)
        answers = {}
        from .report_driven.review_responses import validate_saved
        for question in questions_by_goal[goal]:
            for answer in question.get('review_responses', []):
                try:
                    validated = validate_saved(case, None, answer, lineage=resolved['session_lineage'], question=question)
                    answers[(validated['review_id'], validated['finding_id'])] = dict(
                        question_id=question['id'], revision=question.get('revision'),
                        disposition=validated['disposition'], reason=validated['reason'],
                        limitation=validated.get('limitation'), citation_refs=validated['citation_refs'])
                except (ValueError, KeyError, TypeError):
                    continue
        for finding in findings:
            fid = finding['finding_id']
            answer = answers.get((rid, fid))
            items.append(dict(base, id='review:'+rid+':'+fid, finding_id=fid, speaker='ren',
                kind='executive_question' if base['round'] else 'review_question',
                public_text=finding['question'], public_reason=finding.get('why'), next_check=finding.get('next_check'),
                disposition=answer, response_ref=({'question_id':answer['question_id'], 'revision':answer['revision'],
                    'finding_id':fid} if answer else None)))
        if not findings or rejected or status != 'received':
            items.append(dict(base, id='review:'+rid+':status', finding_id=None, speaker='system',
                kind='review_status', public_text=_STATUS.get(status, _STATUS['unavailable']),
                public_reason='일부 질문은 형식 또는 참조 확인에 실패했습니다.' if rejected else None,
                next_check=None, disposition=None))
    return items


def review_chat(resolved, *, profile, session_id, cursor=''):
    """Use only after authenticated resolve_view; paginate an immutable public snapshot."""
    if resolved.get('requested_session_id') != session_id or not profile.strip():
        raise ValueError('Review chat scope does not match the selected native session')
    case = resolved['case']
    candidates, count = [], 0
    for path in case.output.glob('independent-review-*-request.json'):
        if path.is_symlink() or not path.is_file():
            continue
        count += 1
        heapq.heappush(candidates, (path.stat().st_mtime_ns, path.name))
        if len(candidates) > MAX_SCAN_FILES:
            heapq.heappop(candidates)
    scanned = [case.output / name for _, name in sorted(candidates)]
    scan_versions = tuple((p.name, _version(p)) for p in scanned)
    selection_key = (str(case.manifest), resolved['case_id'], resolved['conversation_id'],
                     tuple(resolved['session_lineage']), scan_versions)
    with _LOCK:
        selected = _SELECTION_CACHE.get(selection_key)
    if selected is None:
        owned, unreadable = [], 0
        for path in scanned:
            try:
                request = review._read(path)
                identity = request.get('identity')
                if not isinstance(identity, dict):
                    raise ValueError('Invalid review identity')
                if (identity.get('case_id') == resolved['case_id'] and
                        identity.get('case_manifest') == str(case.manifest) and
                        identity.get('conversation_id') == resolved['conversation_id'] and
                        request.get('session_id') in resolved['session_lineage']):
                    created = request.get('created_ns', 0)
                    if type(created) is not int:
                        raise ValueError('Invalid review request timestamp')
                    owned.append((created, path.name))
            except (OSError, ValueError, KeyError, TypeError):
                unreadable += 1
        owned.sort()
        selected = ([name for _, name in owned[-MAX_RECEIPTS:]], len(owned), unreadable)
        with _LOCK:
            _SELECTION_CACHE[selection_key] = selected
            while len(_SELECTION_CACHE) > 8:
                _SELECTION_CACHE.popitem(last=False)
    names, owned_count, unreadable = selected
    paths = [case.output / name for name in names]
    versions = tuple((p.name, _version(p), _version(p.with_name(p.name.replace('-request.json', '-response.json')))) for p in paths)
    ledger_version = (_version(case.db), _version(case.db.with_name(case.db.name+'-wal')))
    key = (str(case.manifest), resolved['case_id'], resolved['conversation_id'], resolved['goal_id'],
           tuple(resolved['session_lineage']), ledger_version, versions,
           tuple(review.inflight_review_ids(resolved)), getattr(resolved.get('goal'), 'goal', None))
    with _LOCK:
        cached = _CACHE.get(key)
    status = 'unavailable' if unreadable and not paths else 'ready'
    if cached is None:
        try:
            items = _project(resolved, paths)
            # A concurrent ledger write must not be cached under an older version.
            if ledger_version != (_version(case.db), _version(case.db.with_name(case.db.name+'-wal'))):
                raise ValueError('Retained review snapshot changed during projection')
            cached = items
            with _LOCK:
                _CACHE[key] = deepcopy(items)
                while len(_CACHE) > 8:
                    _CACHE.popitem(last=False)
        except (OSError, ValueError, KeyError, TypeError, StopIteration, sqlite3.Error):
            cached, status = [], 'unavailable'
    items = deepcopy(cached)
    revision = hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
    offset = 0
    if cursor:
        try:
            expected, raw = cursor.split(':')
            offset = int(raw)
            if expected != revision or offset < 0 or offset % PAGE_SIZE or offset >= len(items):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Review chat cursor is stale; reload the first page') from None
    return dict(status=status, profile=profile, requested_session_id=session_id,
        case_id=resolved['case_id'], conversation_id=resolved['conversation_id'], goal_id=resolved['goal_id'],
        items=items[offset:offset+PAGE_SIZE], snapshot_id=revision,
        next_cursor=f'{revision}:{offset+PAGE_SIZE}' if offset+PAGE_SIZE < len(items) else None,
        coverage=dict(scanned_receipts=len(scanned), owned_receipts=owned_count,
            omitted_receipts=max(0, owned_count-len(paths)),
            unscanned_receipts=max(0, count-len(scanned)), unreadable_receipts=unreadable))
