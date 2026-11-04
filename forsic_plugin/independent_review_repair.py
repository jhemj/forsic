"""One bounded validation repair per executive round, preserving immutable receipts.

Repair uses the original presented evidence. It is a new accounted model call,
not a delivery retry, evidence adoption, round reset, or semantic approval.
"""
import json


def parse_receipt(request, response, rejected):
    from . import independent_review as review
    from .independent_review_state import _presented, _presented_handles
    body=json.loads(next(m['content'] for m in request['messages'] if m.get('role')=='user'))
    return review._parse(response['raw_advice'],_presented(request),request['request_id'],rejected,
        review_refs=_presented_handles(request),executive_round=request.get('executive_round',0),
        previous_questions=body.get('executive_followup',{}).get('previous_questions',()),
        allow_partial=True,expected_count=request.get('executive_repair_count'))


def ordinary_receipts(receipts):
    return [(p,r) for p,r in receipts if not r.get('executive_round') or r.get('executive_repair_of')]


def plan(resolved, receipts, rounds):
    from . import independent_review as review
    for entry in rounds:
        if entry['question_count'] >= review.EXECUTIVE_QUESTIONS:continue
        rid=entry['review_id']
        path,request=next((p,r) for p,r in receipts if r['request_id']==rid)
        response=review._response_for(path,request)
        # Unknown delivery, truncation, cancellation and missing receipts are not
        # format failures. Never resend them through this narrowly scoped path.
        if not response or response.get('finish_reason')!='stop' or response.get('status') not in ('received','invalid_advice'):
            return None
        if any(r.get('executive_repair_of')==rid for _,r in receipts):return None
        limit=review._call_budget(resolved)
        if limit is not None and len(ordinary_receipts(receipts))>=limit:return None
        return {'review_id':rid,'round':request['executive_round'],
                'missing_count':review.EXECUTIVE_QUESTIONS-entry['question_count'],
                'attempt_limit':1,'counts_against':'ordinary_review_budget'}
    return None


def snapshot(resolved, original, projection, base):
    from . import independent_review as review
    from .independent_review_state import _presented, _presented_handles
    repair=projection['review_repair']
    body=json.loads(next(m['content'] for m in original['messages'] if m.get('role')=='user'))
    valid=[f for f in projection['findings'] if f.get('executive_round')==repair['round']
           and f.get('kind')=='executive_question']
    body['executive_followup']['previous_questions'] += [f['question'] for f in valid]
    body['executive_followup']['questions_required']=repair['missing_count']
    body['executive_followup']['validation_repair']={
        'request_id':original['request_id'],'accepted_questions':[f['question'] for f in valid],
        'instruction':'Return only the missing questions, using exact presented field review_ref handles. '
                      'Do not repeat accepted questions, change the evidence, or approve the round.'}
    system=original['messages'][0]['content'].replace('exactly 5 findings:',
        f"exactly {repair['missing_count']} findings:")
    prompt=review._prompt_json(body)
    # Repair inherits the immutable original contract, never a newer base packet's
    # allowance. Older receipts without a declared bound retain the ordinary cap.
    input_limit=original.get('input_manifest',{}).get('input_limit',review.MAX_INPUT)
    if type(input_limit) is not int or input_limit not in (review.MAX_INPUT,review.MAX_REPORT_INPUT):
        raise ValueError('Invalid executive validation repair input bound')
    if len(system)+len(prompt)>input_limit:raise ValueError('Executive validation repair exceeds input bound')
    return {**base,'prompt':prompt,'system':system,'input_limit':input_limit,
        'refs':_presented(original),'review_refs':_presented_handles(original),
        'fingerprint':original['material_fingerprint'],
        'evaluation_fingerprint':original['evaluation_fingerprint'],
        'report_draft_fingerprint':original.get('report_draft_fingerprint'),
        'input_fingerprint':review._input_fingerprint(system,body),
        'executive_round':repair['round'],'executive_repair_of':original['request_id'],
        'executive_repair_count':repair['missing_count'],
        'previous_questions':body['executive_followup']['previous_questions']}
