"""Presentation of retained finalization outcomes at the native model boundary.

The host/API result, report manifest and ledger keep the complete review. This
view exposes the outcome and exact recovery before optional historical detail.
"""
from copy import deepcopy
import json


def finalization_view(result):
    if not isinstance(result, dict):
        return result
    view = {key: deepcopy(value) for key, value in result.items()
            if key not in ('review', 'delivery')}
    lookup = {'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox'}}
    review = result.get('review')
    if isinstance(review, dict):
        summary = {key: deepcopy(review[key]) for key in (
            'enabled', 'status', 'review_id', 'latest_stage', 'final_review_current',
            'latest_final_review_id', 'latest_final_status', 'requires_followup',
            'fingerprint', 'availability_status', 'availability_limitation',
            'executive_followup_required', 'call_budget', 'calls_used', 'calls_remaining',
            'budget_exhausted', 'diagnostic') if key in review}
        for key, count in (('pending_finding_ids', 'pending_count'),
                           ('dispositions', 'disposition_count'),
                           ('stale_review_ids', 'stale_review_count'),
                           ('historical_review_ids', 'historical_review_count')):
            values = review.get(key) or []
            summary[count] = len(values)
        pending = review.get('pending_finding_ids') or []
        summary.update(pending_finding_ids=deepcopy(pending[:8]),
            omitted_pending_finding_ids=max(0, len(pending)-8), lookup=deepcopy(lookup),
            details_omitted=True, semantic_approval=False)
        # A review-specific lookup retrieves answered historical findings too;
        # the default inbox intentionally lists only currently pending findings.
        ids = list(dict.fromkeys(item['review_id'] for item in review.get('dispositions', [])
                                 if isinstance(item, dict) and isinstance(item.get('review_id'), str)))
        summary['history_calls'] = [{'tool': 'forsic_reporting', 'arguments': {
            'action': 'review_inbox', 'review_id': rid}} for rid in ids[-4:]]
        summary['omitted_history_calls'] = max(0, len(ids)-4)
        executive = review.get('executive_followup')
        if isinstance(executive, dict):
            summary['executive_followup'] = {key: deepcopy(executive[key]) for key in (
                'status', 'complete', 'round_limit', 'questions_per_round', 'blocked_status',
                'semantic_approval') if key in executive}
            summary['executive_followup']['round_count'] = len(executive.get('rounds', []))
        attempt = review.get('last_attempt')
        if isinstance(attempt, dict):
            summary['last_attempt'] = {key: deepcopy(attempt[key]) for key in (
                'status', 'diagnostic', 'followup_action', 'followup_actionable') if key in attempt}
        view['review'] = summary
    delivery = result.get('delivery')
    if isinstance(delivery, dict):
        if len(json.dumps(delivery, ensure_ascii=False).encode('utf-8')) <= 6000:
            view['delivery'] = deepcopy(delivery)
        else:
            summary = {key: deepcopy(delivery[key]) for key in
                       ('schema', 'snapshot_id', 'meaning', 'retrieval') if key in delivery}
            omitted = delivery.get('omitted') or {}
            summary['omitted'] = {key: len(delivery.get(key) or []) + omitted.get(key, 0)
                                  for key in ('answers', 'evidence_excerpts')}
            summary['omitted']['scope_inventory'] = int(delivery.get('scope_inventory') is not None) + omitted.get('scope_inventory', 0)
            summary.setdefault('retrieval', {'tool': 'forsic_reporting', 'arguments': {'action': 'state'}})
            summary['details_omitted'] = True
            view['delivery'] = summary
    # Copy a host-issued call as one indivisible object. In particular retain
    # selected scope and redaction options; never manufacture a new render scope.
    if isinstance(result.get('recovery'), dict):
        view['next_call'] = deepcopy(result['recovery'])
    return view
