"""Small pending-work view of validated review state, never an answer or evidence."""
import json


def response_context(state, *, max_chars=3500):
    """Bound the native handoff by whole values, retaining exact inbox navigation.

    A reviewer's proposed tests are preserved in the inbox, not promoted into
    an executable plan by copying them into every continuation prompt.
    """
    pending = state.get('pending_findings', [])
    lookup = {'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox'}}
    body = {'kind': 'pending_review_responses', 'status': state.get('status', 'unknown'),
            'pending_count': len(pending),
            'lookup': lookup, 'response_tool': 'forsic_review_response',
            'authority': 'Current native goal defines scope. Reviewer-proposed checks are unadopted advice in the inbox, not an assigned work plan.',
            'response_contract': 'Read retained evidence and current revision/save inputs via read_call. Author an answer and disposition: investigated/refuted uses C1/C2 citations; scope_limited explains the unperformed scope, consequence and reopening condition.',
            'pending_items': [], 'omitted_pending_items': len(pending)}
    if state.get('rejected_findings'):
        body['rejected_findings_count'] = len(state['rejected_findings'])
    encode = lambda: json.dumps(body, ensure_ascii=False, separators=(',', ':'))
    if len(encode()) > max_chars:
        body.pop('response_contract')
        body.pop('authority')
    if len(encode()) > max_chars:
        return 'Review context unavailable; forsic_reporting(action=review_inbox).' if max_chars >= 66 else 'unavailable'
    for finding in pending:
        rid, fid = finding.get('review_id'), finding.get('finding_id')
        if not all(isinstance(v, str) and 0 < len(v) <= 160 for v in (rid, fid)):
            continue
        item = {'question': finding.get('question'), 'read_call': {
            'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox',
                'review_id': rid, 'finding_id': fid}}}
        body['pending_items'].append(item)
        body['omitted_pending_items'] -= 1
        if len(encode()) > max_chars:
            item.pop('question')
            item['question_omitted'] = True
        if len(encode()) > max_chars:
            body['pending_items'].pop()
            body['omitted_pending_items'] += 1
    return encode()


def pending_items(state, *, max_bytes=1000):
    """Preserve whole question text and exact lookups before optional excerpts.

    The caller supplies validated, goal-owned review state. No source text,
    reviewer reasoning, or verdict is copied. Long questions are retrievable,
    not shortened into a different question.
    """
    pending = state.get('pending_findings', [])
    result = {'pending_items': [], 'omitted_pending_items': len(pending)}
    if state.get('status') in ('unknown', 'unavailable'):
        return {'pending_items': [], 'omitted_pending_items': None}
    for finding in pending[:2]:
        rid, fid = finding.get('review_id'), finding.get('finding_id')
        if not all(isinstance(v, str) and 0 < len(v) <= 160 for v in (rid, fid)):
            continue
        item = {'review_id': rid, 'finding_id': fid,
                'read_call': {'tool': 'forsic_reporting', 'arguments': {
                    'action': 'review_inbox', 'review_id': rid, 'finding_id': fid}}}
        question = finding.get('question')
        if isinstance(question, str) and len(question.encode('utf-8')) <= 640:
            item['question'] = question
        else:
            item['question_omitted'] = True
        result['pending_items'].append(item)
        result['omitted_pending_items'] -= 1
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > max_bytes:
            item.pop('question', None)
            item['question_omitted'] = True
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > max_bytes:
            result['pending_items'].pop()
            result['omitted_pending_items'] += 1
    return result
