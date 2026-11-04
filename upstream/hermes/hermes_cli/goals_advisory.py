"""Optional bounded advice at native goal checkpoints, never a second executor."""
import json
import hashlib
import math
import time


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
    required_progress = []
    followup_actions = []
    actionable = True
    awaiting_review = False
    review_waits = []
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
            action = result.get('followup_action', 'respond')
            action = action if isinstance(action, str) and action in _FOLLOWUP_INSTRUCTIONS else 'unknown'
            if action not in followup_actions:
                followup_actions.append(action)
            item['requires_followup'] = True
            awaiting_review |= result.get('status') == 'in_progress'
            wait = result.get('review_wait')
            if (isinstance(wait, dict) and wait.get('state') == 'alive'
                    and isinstance(wait.get('request_id'), str) and wait['request_id']
                    and isinstance(wait.get('deadline_at'), (int, float))
                    and not isinstance(wait['deadline_at'], bool) and math.isfinite(wait['deadline_at'])
                    and isinstance(wait.get('owner_pid'), int) and wait['owner_pid'] > 0
                    and isinstance(wait.get('thread_id'), int)):
                review_waits.append(dict(wait))
            can_act = result.get('followup_actionable', result.get('status') not in
                                 ('unavailable', 'unknown', 'in_progress', 'cancelled')) is True
            can_act &= item.get('status') != 'unavailable'
            actionable &= can_act
            progress = result.get('progress_fingerprint')
            if not isinstance(progress, str) or not 0 < len(progress) <= 200:
                # Old providers can still request follow-up. Stable finding and
                # receipt identities bound their re-entry; candidate prose does not.
                progress = hashlib.sha256(json.dumps({key: result.get(key) for key in
                    ('provider', 'status', 'review_id', 'pending_finding_ids', 'context', 'followup_action')},
                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            required_progress.append({'provider': str(result.get('provider', 'unknown'))[:200],
                                      'revision': progress, 'actionable': can_act})
            for key in ('followup_action', 'diagnostic'):
                value = result.get(key)
                if isinstance(value, (str, dict)) and len(json.dumps(value, ensure_ascii=False)) <= 1200:
                    item[key] = value
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
    fingerprint = hashlib.sha256(json.dumps(required_progress, sort_keys=True).encode()).hexdigest()
    return {'context': value, 'requires_followup': requires_followup,
            'followup_actionable': actionable, 'progress_fingerprint': fingerprint,
            'progress': required_progress, 'awaiting_review': awaiting_review,
            'followup_actions': followup_actions, 'review_waits': review_waits}


def advisory_continuation(state, advisory, *, final_boundary=False):
    """One concrete opportunity per durable revision, shared by both native gates.

    Only a final text stop consumes an opportunity: active tool work, compression,
    and an ordinary goal checkpoint must not count as failure to make progress.
    This is a receipt on the existing goal, not another loop or task scheduler.
    """
    fingerprint = advisory.get('progress_fingerprint', '')
    previous = state.advisory_progress or {}
    waits = advisory.get('review_waits') or []
    if (waits and advisory.get('followup_actions') == ['wait']
            and advisory.get('followup_actionable') is True):
        from hermes_cli.goals_runtime import runtime_wall_deadline
        now = time.time()
        deadline = min(item['deadline_at'] for item in waits)
        identity=[state.created_at,state.goal]
        same_wait=(previous.get('status')=='waiting' and previous.get('goal_identity')==identity
                   and {item['request_id'] for item in previous.get('review_waits',[])}
                       =={item['request_id'] for item in waits})
        if same_wait:deadline=min(deadline,float(previous.get('original_deadline') or deadline))
        wall = runtime_wall_deadline(state)
        from hermes_cli.goals_completion import enabled
        if wall is not None and enabled(state):wall -= state.runtime_policy['settlement_seconds']
        if wall is not None:deadline = min(deadline, wall)
        if deadline > now:
            since=state.waiting_since if same_wait and state.waiting_since else now
            state.clear_wait()
            state.waiting_until = deadline
            state.waiting_since = since
            state.waiting_reason = 'Existing independent review worker; awaiting its original request receipt.'
            state.advisory_progress = {'fingerprint': fingerprint, 'status': 'waiting',
                'review_waits': waits, 'final_opportunity': False,
                'goal_identity': identity, 'original_deadline':deadline,
                'next_owner':'native_review_wait', 'next_due':now,
                'claim_id':None, 'receipts':previous.get('receipts',[]) if same_wait else []}
            return 'wait', state.waiting_reason
        advisory = {**advisory, 'followup_actionable': False, 'awaiting_review': False}
    unavailable = advisory.get('followup_actionable') is False
    unchanged = final_boundary and previous.get('fingerprint') == fingerprint and previous.get('final_opportunity') is True
    if unavailable or unchanged:
        pending = unavailable and advisory.get('awaiting_review') is True
        code = 'advisory_pending' if pending else 'advisory_unavailable' if unavailable else 'advisory_no_progress'
        reason = ('Required review outcome is still pending after its bounded native wait; the task remains incomplete.' if pending else
                  'Required review has no actionable native follow-up.' if unavailable else
                  'Required review state did not change after the final follow-up opportunity.')
        state.advisory_progress = {'fingerprint': fingerprint, 'status': code,
            'final_opportunity': previous.get('final_opportunity', False),
            'progress': advisory.get('progress', []), 'context': advisory.get('context', '')}
        return code, reason
    if final_boundary:
        state.advisory_progress = {'fingerprint': fingerprint, 'status': 'pending',
            'final_opportunity': True, 'progress': advisory.get('progress', [])}
    return 'continue', ''


def goal_advisory_context(**kwargs):
    """Compatibility string view for advisory-only consumers."""
    return goal_advisory_result(**kwargs)['context']


def read_review_wait(db, session_id):
    from hermes_cli.goals import GoalState
    raw = db.get_meta('goal:' + session_id)
    state = GoalState.from_json(raw) if raw else None
    progress = dict(state.advisory_progress or {}) if state else {}
    return raw, state, progress


def review_wait_status(state, session_id):
    """Inspect immutable requests and live workers; never request a new review."""
    from hermes_cli import plugins
    from hermes_cli.goals import goal_identity
    expected = {item['request_id'] for item in (state.advisory_progress or {}).get('review_waits', [])}
    if not expected or not plugins.has_hook('goal_advisory_context'):
        return 'unavailable'
    results = plugins.invoke_hook('goal_advisory_context', session_id=session_id,
        goal_id=goal_identity(state), goal_text=state.goal, turns_used=state.turns_used,
        cancel_check=lambda: False, stage='checkpoint', read_only=True,
        wait_request_ids=sorted(expected), max_chars=3500)
    found = {}
    for result in results:
        if not isinstance(result, dict) or result.get('status') != 'wait_status':continue
        for item in result.get('review_waits', []):
            if isinstance(item, dict) and item.get('request_id') in expected:
                found[item['request_id']] = item.get('state')
    if set(found) != expected or any(value not in {'alive','ready'} for value in found.values()):
        return 'unavailable'
    return 'alive' if 'alive' in found.values() else 'ready'


def settle_review_wait(db, session_id, *, reason=None, claim_id=None):
    """Persist explicit failed ownership or consumed dispatch without replay."""
    from hermes_cli.goals_runtime import settle_runtime, runtime_identity
    raw, state, progress = read_review_wait(db, session_id)
    if (not state or state.status != 'active' or state.pause_requested
            or progress.get('goal_identity') != list(runtime_identity(state))
            or progress.get('status') not in {'waiting','claimed','dispatched'}
            or claim_id is not None and progress.get('claim_id') != claim_id):return False
    if reason is None and progress.get('status')!='dispatched':return False
    now = time.time()
    progress.update(status='terminal' if reason else 'resumed', next_due=None, next_owner=None,
                    settled_at=now, terminal_reason=reason)
    state.advisory_progress = progress
    state.clear_wait()
    if reason:
        settle_runtime(state, now=now)
        state.status,state.pause_requested='paused',False
        state.runtime_started_at=None
        state.last_verdict='advisory_unavailable'
        state.last_reason=state.paused_reason=reason
        state.runtime_settlement={**state.runtime_settlement,'status':'settlement_pending',
                                  'reason':reason,'at':now}
    saved=db.compare_and_swap_meta('goal:' + session_id, raw, state.to_json())
    if saved and reason:
        from hermes_cli.goals_completion import settle_native
        try:
            receipt=settle_native(db,session_id,expected_identity=runtime_identity(state),reason=reason)
            if receipt.get('status')=='settlement_failed':raise RuntimeError('settlement conflict')
        except Exception:
            latest,current,_=read_review_wait(db,session_id)
            if current and runtime_identity(current)==runtime_identity(state):
                current.runtime_settlement={**current.runtime_settlement,'status':'settlement_failed'}
                db.compare_and_swap_meta('goal:'+session_id,latest,current.to_json())
    return saved


def claim_review_wait(db, session_id, *, owner):
    """One durable follow-up claim after the existing request publishes a receipt."""
    import uuid
    from hermes_cli.goals_runtime import runtime_identity, runtime_remaining, runtime_wall_deadline
    raw, state, progress = read_review_wait(db, session_id)
    if (not state or state.status != 'active' or state.pause_requested
            or progress.get('goal_identity') != list(runtime_identity(state))
            or progress.get('status') not in {'waiting','claimed','dispatched'}):return None
    now=time.time()
    deadline=float(progress.get('original_deadline') or 0)
    wall=runtime_wall_deadline(state)
    from hermes_cli.goals_completion import enabled
    if wall is not None and enabled(state):wall-=state.runtime_policy['settlement_seconds']
    if wall is not None:deadline=min(deadline,wall)
    if state.turns_used>=state.max_turns:
        settle_review_wait(db,session_id,reason='review_wait_turn_budget');return None
    if deadline<=now or runtime_remaining(state,now=now) in (None,0):
        settle_review_wait(db,session_id,reason='review_wait_deadline');return None
    if progress['status']!='waiting':
        settle_review_wait(db,session_id,reason='review_wait_delivery_unknown');return None
    try:status=review_wait_status(state,session_id)
    except Exception:status='unavailable'
    if status=='alive':return None
    if status!='ready':
        settle_review_wait(db,session_id,reason='review_wait_owner_unavailable');return None
    progress.update(status='claimed',claim_id=uuid.uuid4().hex,owner=owner,claimed_at=now,
                    next_due=None,next_owner='native_review_continuation')
    progress['receipts']=[*(progress.get('receipts') or []),
        {'event':'claimed','claim_id':progress['claim_id'],'at':now}]
    state.advisory_progress=progress
    state.clear_wait()
    return progress if db.compare_and_swap_meta('goal:'+session_id,raw,state.to_json()) else None


def review_wait_turn_started(db, session_id, claim_id):
    from hermes_cli.goals_runtime import runtime_identity
    raw,state,progress=read_review_wait(db,session_id)
    if (not state or state.status!='active' or state.pause_requested
            or progress.get('goal_identity')!=list(runtime_identity(state))
            or progress.get('status')!='claimed' or progress.get('claim_id')!=claim_id
            or float(progress.get('original_deadline') or 0)<=time.time()):return False
    progress.update(status='dispatched',started_at=time.time())
    progress['receipts']=[*(progress.get('receipts') or []),
        {'event':'turn_started','claim_id':claim_id,'at':progress['started_at']}]
    state.advisory_progress=progress
    return db.compare_and_swap_meta('goal:'+session_id,raw,state.to_json())


# Actions describe the provider's current obligation, not permission to run it.
# Missing actions retain the legacy response contract; unknown actions stay explicit.
_FOLLOWUP_INSTRUCTIONS = {
    'repair_review': ('Repair only the rejected items of the existing review round through its provided recovery call. '
                      'Preserve valid questions, original receipts, dispositions and spent budget; do not restart the dialogue.'),
    'respond': ('Assess pending findings within the current user-authorized task, reusing preserved results first. '
                'Record an explicit answer and disposition with reasons through the existing tools; revise the answer as needed.'),
    'revise_judgment': ('Assess the current judgment against preserved sources and update it with reasons through the existing tools.'),
    'next_review_round': ('This provider requests the next review round. '
                          'If ready, present a candidate answer at the next native final boundary to obtain the next review round.'),
    'final_report_review': ('This provider requests final report review. '
                            'If ready, present the current report as a candidate at the next native final boundary for final review.'),
    'compose_report': ('Compose or update the report through the existing tools using preserved answers, reasons and limitations; '
                       'when ready, present a candidate answer at the next native final boundary.'),
    'create_continuation_question': ('Use the provided existing question writer call to preserve the current human follow-up scope, '
                                     'then read the review inbox for its owned response inputs. Preserve prior questions and conclusions.'),
    'wait': ('Review delivery remains pending. Reuse preserved work and return to the native review boundary when ready; '
             'do not create another reviewer or polling loop.'),
    'unknown': ('Inspect the preserved advisory state and resolve or disclose the required follow-up; '
                'an unrecognized action does not establish that review is complete.'),
}


def advisory_followup_reason(advisory):
    return ('Independent advisory follow-up awaits main-agent handling: '
            + ', '.join(advisory.get('followup_actions') or ['respond']) + '.')


def advisory_followup_prompt(context, *, goal_text='', followup_actions=None):
    """A mandatory response obligation never widens the user's active task."""
    task = ("Current native goal (scope remains authoritative):\n" + goal_text + "\n\n") if goal_text else ''
    actions = followup_actions or ['respond']
    instructions = ' '.join(_FOLLOWUP_INSTRUCTIONS[action] for action in actions)
    return (task + "Independent advisory review requires main-agent follow-up before completion. "
            + instructions + ' '
            "Decide whether useful investigation remains within the authorized scope or a candidate final boundary is appropriate. "
            "Required review does not authorize additional investigation. For checks outside the task, record "
            "the specific unperformed scope, its consequence, and reopening condition instead. "
            "Review advice is not evidence or completion approval.\n" + context)
