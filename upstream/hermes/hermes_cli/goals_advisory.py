"""Optional bounded advice at native goal checkpoints, never a second executor."""
import json


def goal_advisory_result(*, session_id, goal_id, goal_text, turns_used, cancel_check,
                         stage='exploration', draft_response=None):
    """Read advisory context and an explicit request for main-agent follow-up.

    Missing ``requires_followup`` keeps the legacy advisory-only contract. Only the
    final boundary receives a candidate answer; exploration stays independent of it.
    Providers own persisted finding dispositions, deadlines and cancellation.
    """
    from hermes_cli import plugins
    empty = {'context': '', 'requires_followup': False}
    if cancel_check() or not plugins.has_hook('goal_advisory_context'):
        return empty
    callbacks = plugins.iter_hook_callbacks('goal_advisory_context')
    payload = dict(session_id=session_id, goal_id=goal_id, goal_text=goal_text,
                   turns_used=turns_used, cancel_check=cancel_check, max_chars=3500, stage=stage)
    if stage == 'final':
        payload['draft_response'] = draft_response
    results = plugins.invoke_hook('goal_advisory_context', **payload)
    if cancel_check():
        return empty
    advice = []
    requires_followup = False
    for result in results:
        if not isinstance(result, dict) or result.get('status') == 'not_applicable':
            continue
        required = result.get('requires_followup') is True
        requires_followup |= required
        context = result.get('context')
        if context == '' and not required:
            continue
        if not isinstance(context, str) or len(context) > 3500:
            item = {'status': 'unavailable', 'context': '검토 응답 형식 또는 크기를 확인하지 못했습니다.'}
        else:
            item = {k: result[k] for k in ('provider', 'status', 'context')
                    if isinstance(result.get(k), str)}
        if required:
            item['requires_followup'] = True
        review_id = result.get('review_id')
        if isinstance(review_id, str) and len(review_id) <= 200:
            item['review_id'] = review_id
        pending = result.get('pending_finding_ids')
        if isinstance(pending, list):
            item['pending_finding_ids'] = [value for value in pending[:32]
                                          if isinstance(value, str) and len(value) <= 200]
        advice.append(item)
    warning = ('\n독립 검토 결과를 확인하지 못했습니다. 이는 검토 통과를 뜻하지 않습니다.'
               if len(results) != len(callbacks) else '')
    value = (json.dumps(advice, ensure_ascii=False) if advice else '') + warning
    # Preserve finding identities when JSON metadata pushes a valid context over
    # the aggregate cap. Providers place pending-action details at the context tail.
    for item in advice:
        if len(value) <= 4000:
            break
        context = item.get('context', '')
        keep = max(0, len(context) - (len(value) - 3900))
        item['context'] = ('…' + context[-keep:]) if keep else ''
        value = json.dumps(advice, ensure_ascii=False) + warning
    if len(value) > 4000:
        value = '독립 검토 응답이 표시 한도를 초과했습니다. 검토 기록을 확인해야 합니다.'
    return {'context': value, 'requires_followup': requires_followup}


def goal_advisory_context(**kwargs):
    """Compatibility string view for advisory-only consumers."""
    return goal_advisory_result(**kwargs)['context']


def advisory_followup_prompt(context):
    return ("Independent advisory review requires main-agent follow-up before completion. "
            "Investigate the pending findings against the sources, record an explicit disposition "
            "with reasons using the existing investigation tools, and revise the answer as needed. "
            "Review advice is not evidence or completion approval.\n" + context)
