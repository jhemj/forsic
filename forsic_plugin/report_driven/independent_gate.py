"""Trusted native routing for final-report review, without another execution loop."""
import hashlib
import json
import os
import tempfile


def _attempt_path(resolved):
    from .. import independent_review as review
    identity = review._identity(resolved)
    selected = resolved.get('_report_question_ids')
    key = [identity, selected] if selected is not None else identity
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    return resolved['case'].output / ('report-review-attempt-' + digest + '.json')


def _basis(resolved, projection=None):
    from .. import independent_review as review
    # Response-only repairs intentionally do not stale semantic review receipts,
    # but they must invalidate a failure encountered before that repair.
    progress = review.review_state(resolved) if projection is None else projection
    return {**review.freshness(resolved),
            'progress_fingerprint': progress.get('progress_fingerprint')}


def _attempt_details(attempt):
    result = {k: attempt[k] for k in ('status', 'review_id', 'requires_followup',
        'followup_actionable', 'followup_action', 'diagnostic', 'context',
        'progress_fingerprint') if k in attempt}
    # This is routing metadata, not another copy of provider requests/responses.
    if len(json.dumps(result, ensure_ascii=False).encode()) > 32768:
        result = {k: result[k] for k in ('status', 'review_id') if k in result}
        result.update(requires_followup=True, followup_actionable=False, followup_action='repair',
            diagnostic={'code': 'review_attempt_details_exceeded', 'phase': 'attempt_retention',
                        'reason': 'Review attempt diagnostics exceed the bounded local record.'})
    return result


def attempt_for_context(review_context, projection=None):
    attempt = review_context.get('attempt')
    basis = review_context.get('attempt_basis')
    if basis is not None and basis != _basis(review_context['resolved'], projection):
        return None
    return attempt


def retained_attempt(resolved, *, authored_selection=False):
    """Read a current failure without dispatching or overriding canonical receipts."""
    from .. import independent_review as review
    if authored_selection:
        resolved = review.native_report_review_scope(resolved)
        draft = review._report_draft(resolved, review._review_questions(resolved))
        if draft:
            resolved = review.report_review_scope(resolved, draft['question_ids'])
    path = _attempt_path(resolved)
    if not path.exists():
        return None
    if path.stat().st_size > 65536:
        return None
    saved = review._read(path)
    # Legacy status/id-only records cannot establish a current repair failure.
    if (saved.get('schema') != 'forsic-report-review-attempt-2'
            or saved.get('identity') != review._identity(resolved)
            or saved.get('question_ids') != resolved.get('_report_question_ids')
            or saved.get('basis') != _basis(resolved)):
        return None
    return saved


def context(case, state, args, *, dispatch=False, question_ids=None, read_attempt=True):
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
    resolved = review.report_review_scope(resolved, question_ids)
    result = {'enabled': True, 'resolved': resolved,
              'executive_followup_required': options.get('executive_followup') is True}
    # Only availability/attempt metadata is cached here. Original model requests
    # and responses remain in the independent review's immutable receipts.
    identity = review._identity(resolved)
    if dispatch:
        basis = _basis(resolved)
        from tools.interrupt import is_thread_interrupted, acting_for_tid
        import threading
        thread = threading.get_ident()
        acting = acting_for_tid.get()
        cancelled = lambda: is_thread_interrupted(thread) or bool(acting and is_thread_interrupted(acting))
        attempt = review.goal_advisory_context(session_id=options['session_id'],
            goal_id=resolved['goal_id'], goal_text=resolved['goal'].goal,
            turns_used=resolved['goal'].turns_used, stage='final', enabled=True,
            executive_followup=options.get('executive_followup') is True,
            report_question_ids=resolved.get('_report_question_ids'),
            intake_root=options['intake_root'], native_db=options.get('native_db'), cancel_check=cancelled)
        if cancelled() or attempt['status'] in ('cancelled', 'stale', 'in_progress'):
            raise ValueError('검토가 취소되었거나 아직 확정되지 않아 최종본을 선택하지 않았습니다.')
        saved = {'schema': 'forsic-report-review-attempt-2', 'identity': identity,
                 'question_ids': resolved.get('_report_question_ids'), 'basis': basis,
                 'attempt': _attempt_details(attempt)}
        fd, temp = tempfile.mkstemp(prefix='.report-review-attempt-', dir=case.output)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(saved, stream, ensure_ascii=False)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, _attempt_path(resolved))
        finally:
            if os.path.exists(temp): os.unlink(temp)
        result['attempt'] = saved['attempt']
        result['attempt_basis'] = basis
    elif read_attempt:
        saved = retained_attempt(resolved)
        if saved:
            result['attempt'] = saved['attempt']
            result['attempt_basis'] = saved['basis']
    return result


def input_preflight(case, state, args, progress):
    """Measure the current complete projection without scanning sources or dispatching."""
    checked = context(case, state, args, question_ids=progress['question_ids'], read_attempt=False)
    if checked is None:
        return None
    from .. import independent_review as review
    resolved = checked['resolved']
    draft = review._report_draft(resolved, review._review_questions(resolved))
    identity = {key: progress.get(key) for key in ('basis_id', 'draft_hash', 'draft_revision')}
    if draft is None:
        # Accepted native handoff exposes the exact old report for editing,
        # but does not turn it into a current-goal review receipt. Before the
        # first explicit save, this is missing authorship, not a changed draft.
        from ..investigation_context import report_followup_selection
        from .template_contract import WRITING_ORDER
        inherited = report_followup_selection(resolved)
        if inherited and inherited['question_ids'] == sorted(progress['question_ids']):
            event = case.event(inherited['report_event_id'])
            prior = event['data'] if event else {}
            if (prior.get('goal_id') == inherited['source_goal_id']
                    and all(prior.get(key) == value for key, value in identity.items())):
                return {'status': 'current_goal_authoring_required', **identity,
                    'model_dispatched': False, 'source_scan_performed': False,
                    'semantic_approval': False,
                    'recovery': {'tool': 'forsic_reporting', 'arguments': {
                        'action': 'compose', 'question_ids': progress['question_ids'],
                        'section': WRITING_ORDER[0]}},
                    'meaning': 'The accepted native handoff retains this exact prior report and review '
                        'history for editing. No current-goal draft has been saved yet. Follow a '
                        'saved_fields edit_read_call or recovery, make the intended correction and '
                        'save with its current edit_ref. Do not reread the unchanged root review. '
                        'A current draft still requires input preflight and independent final review.'}
    if not draft or any(draft.get(key) != value for key, value in identity.items()):
        raise ValueError('Report changed before input preflight; read compose review again')
    try:
        result = review._collect(resolved, 'final', report_input_only=True)
    except ValueError as exc:
        result = {'status': 'unavailable', **identity,
                  'diagnostic': review._unavailable(exc, 'report_input_preflight')['diagnostic']}
        result['diagnostic']['recovery'] = {'tool': 'forsic_reporting', 'arguments': {
            'action': 'compose', 'section': 'review', 'question_ids': progress['question_ids']}}
    measured = result.get('diagnostic', {}).get('input_budget', result)
    if any(measured.get(key) != identity[key] for key in ('draft_hash', 'draft_revision')):
        raise ValueError('Report changed during input preflight; read compose review again')
    result.update(model_dispatched=False, source_scan_performed=False, semantic_approval=False,
        meaning='This measures only the complete authored projection before source collection. '
                'It is not independent review, source coverage, rendering or final approval. '
                'Resolve a reported input overflow before rendering for finalization; '
                'preserve material evidence and limits. Full final-packet checks still apply.')
    return result
