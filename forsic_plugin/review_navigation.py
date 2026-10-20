"""Small pending-work view of validated review state, never an answer or evidence."""
import json


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
