"""Trusted native routing for final-report review, without another execution loop."""
import hashlib
import json
import os
import tempfile


def context(case, state, args, *, dispatch=False):
    options = args.get('_independent_review_options') or {}
    if options.get('enabled') is not True:
        return None
    from ..investigation_context import resolve_context
    from .. import independent_review as review
    resolved = resolve_context(options['session_id'], intake_root=options['intake_root'],
                               native_db=options.get('native_db'))
    if (not resolved or resolved['case'].manifest != case.manifest
            or resolved['goal_id'] != args.get('_goal_id')
            or resolved['conversation_id'] != args.get('_session_id')):
        raise ValueError('현재 대화·목표·사건에 연결된 보고서 검토만 수행할 수 있습니다.')
    result = {'enabled': True, 'resolved': resolved,
              'executive_followup_required': options.get('executive_followup') is True}
    # Only availability/attempt metadata is cached here. Original model requests
    # and responses remain in the independent review's immutable receipts.
    identity = review._identity(resolved)
    identity_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    path = case.output / ('report-review-attempt-' + identity_hash + '.json')
    fingerprint = review._collect(resolved, 'final')['evaluation_fingerprint']
    if dispatch:
        from tools.interrupt import is_thread_interrupted, acting_for_tid
        import threading
        thread = threading.get_ident()
        acting = acting_for_tid.get()
        cancelled = lambda: is_thread_interrupted(thread) or bool(acting and is_thread_interrupted(acting))
        attempt = review.goal_advisory_context(session_id=options['session_id'],
            goal_id=resolved['goal_id'], goal_text=resolved['goal'].goal,
            turns_used=resolved['goal'].turns_used, stage='final', enabled=True,
            executive_followup=options.get('executive_followup') is True,
            intake_root=options['intake_root'], native_db=options.get('native_db'), cancel_check=cancelled)
        if cancelled() or attempt['status'] in ('cancelled', 'stale', 'in_progress'):
            raise ValueError('검토가 취소되었거나 아직 확정되지 않아 최종본을 선택하지 않았습니다.')
        saved = {'identity': identity, 'fingerprint': fingerprint,
                 'attempt': {k: attempt.get(k) for k in ('status', 'review_id')}}
        fd, temp = tempfile.mkstemp(prefix='.report-review-attempt-', dir=case.output)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(saved, stream, ensure_ascii=False)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        result['attempt'] = saved['attempt']
    elif path.exists():
        saved = review._read(path)
        if saved.get('identity') == identity and saved.get('fingerprint') == fingerprint:
            result['attempt'] = saved.get('attempt')
    return result
