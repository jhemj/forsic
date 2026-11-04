"""Completion reservations on the native goal clock; no executor or second ledger.

Plugin work is a read-only projection. Only native goal CAS owns phase, episodes
and receipts. An absent projection costs the pinned cold-start reserve, never zero.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid

POLICY_REVISION = "report-completion-v2"
from pathlib import Path
_LOADED_FINGERPRINT = {"module": __name__, "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       "loaded_at": time.time(), "policy_revision": POLICY_REVISION}


def loaded_fingerprint():
    return dict(_LOADED_FINGERPRINT)



def make_policy(budget, completion_contract=None):
    return {"revision": POLICY_REVISION, "settlement_seconds": math.ceil(max(900., budget / 48) / 60) * 60,
            "cold_start_fraction": .25, "checkpoint_lateness_seconds": 300.,
            "completion_contract": dict(completion_contract or {}), "model_bound_seconds": 1800., "cancel_seconds": 15.}


def configured_policy(budget):
    from hermes_cli.config import load_config_readonly
    cfg = (load_config_readonly() or {}).get("goals") or {}
    if budget is None or cfg.get("runtime_policy") != POLICY_REVISION:
        return {}
    return make_policy(budget, cfg.get("completion_contract"))


def enabled(state):
    return (getattr(state, "runtime_policy", None) or {}).get("revision") == POLICY_REVISION


def read_work_projection(session_id, state):
    from hermes_cli import plugins
    if not plugins.has_hook("goal_work_projection"):
        return {"status": "unknown", "reason": "work_projection_unavailable"}
    values = plugins.invoke_hook("goal_work_projection", session_id=session_id,
        goal_identity=list((state.created_at, state.goal)), mode="read",
        completion_contract=(getattr(state, "runtime_policy", None) or {}).get("completion_contract", {}))
    applicable = [v for v in values if isinstance(v, dict) and v.get("status") != "not_applicable"]
    if len(applicable) != 1:
        return {"status": "unknown", "reason": "work_projection_unavailable_or_ambiguous"}
    work = dict(applicable[0])
    if work.get('status') == 'ready':
        work['domain_status'] = 'ready'
        work['status'] = 'known' 
    if work.get("digest") and work.get("basis") and work.get("identity"):
        work.setdefault("status", "known")
    else:
        work.update(status="unknown", reason="work_projection_basis_unavailable")
    return work


def control_projection(state, work=None, *, now=None):
    from hermes_cli.goals_runtime import runtime_remaining, runtime_checkpoint_due, runtime_wall_deadline
    now = time.time() if now is None else now
    remaining = runtime_remaining(state, now=now)
    policy = getattr(state, "runtime_policy", None) or {}
    control = getattr(state, "runtime_control", None) or {}
    result = {"policy_revision": policy.get("revision", "legacy-v1"), "remaining_seconds": remaining,
              "phase": None, "reason": None, "checkpoint_due": None}
    if state.status != "active" or state.pause_requested:
        return {**result, "reason": "user_control"}
    if remaining is None or remaining <= 0:
        return {**result, "phase": "exhausted", "reason": "deadline_expired_or_accounting_unknown"}
    if not enabled(state):
        raise ValueError("completion projection requires a pinned policy")
    estimate = (work or {}).get("estimate") or {}
    raw = estimate.get("remaining_seconds")
    known = (estimate.get("known") is True and isinstance(raw, (int, float)) and not isinstance(raw, bool)
             and math.isfinite(raw) and raw >= 0 and (work or {}).get("status") == "known")
    settlement = policy["settlement_seconds"]
    queue = estimate.get("queue_seconds")
    uncertainty = estimate.get("uncertainty_seconds")
    known = known and isinstance(queue, (int, float)) and queue >= 0 and isinstance(uncertainty, (int, float)) and uncertainty >= 0
    # Queue already included in W must be explicitly labelled; never add it twice.
    projected = (raw + (0 if estimate.get("queue_included") else queue) + uncertainty + settlement) if known else 0
    reserve = max(projected, 0 if known else state.runtime_budget_seconds * policy["cold_start_fraction"])
    reserve = max(settlement, reserve)
    deadline = runtime_wall_deadline(state)
    ready_deadline = deadline - settlement
    finish = any(r.get("decision") == "finish" for r in state.runtime_checkpoints)
    sticky = control.get("phase") == "closing" or finish
    slack = remaining - reserve
    quota = (work or {}).get("quota") or {}
    def numeric(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    infeasible = False
    unknown_quota = []
    for name, resource in quota.items():
        if not isinstance(resource, dict):
            unknown_quota.append(name)
            continue
        available = resource.get('remaining')
        if available is None and numeric(resource.get('cap')) and numeric(resource.get('used')):
            available = max(0, resource['cap'] - resource['used'])
        required = resource.get('required_reserved')
        if not numeric(available) or not numeric(required):
            unknown_quota.append(name)
        else:
            infeasible |= required > available
    phase = "settlement" if remaining <= settlement else "closing" if sticky or slack <= 0 or infeasible else "investigation"
    reason = "slack_available"
    if sticky:
        reason = control.get("reason") or "model_finish"
    elif infeasible:
        reason = "completion_resources_reserved"
    elif slack <= 0:
        reason = "report_reserve_enforced"
    if phase == "settlement":
        reason = "report_target_reached"
    return {**result, "phase": phase, "reason": reason, "reserve_seconds": reserve, "slack_seconds": slack,
            "estimate_source": "measured" if known else "cold_start", "settlement_seconds": settlement,
            "deadline": deadline, "ready_deadline": ready_deadline, "latest_safe_close": deadline - reserve,
            "checkpoint_due": runtime_checkpoint_due(state, now=now), "work_digest": (work or {}).get("digest"),
            "quota_infeasible": infeasible, "unknown_quota": unknown_quota}


def _read(db, sid):
    from hermes_cli.goals import GoalState, _meta_key
    raw = db.get_meta(_meta_key(sid))
    return raw, GoalState.from_json(raw) if raw else None


def _same_active(state, identity):
    from hermes_cli.goals_runtime import runtime_identity, runtime_remaining
    return (state is not None and state.status == "active" and not state.pause_requested
            and runtime_identity(state) == tuple(identity) and runtime_remaining(state) not in (None, 0)
            and state.turns_used < state.max_turns)


def technical_closing(db, sid, identity, reason, *, source_uid=None):
    from hermes_cli.goals import _meta_key
    raw, state = _read(db, sid)
    if not _same_active(state, identity):
        return False
    state.runtime_control = {**state.runtime_control, "phase": "closing", "reason": reason,
        "decision_origin": "native", "changed_at": time.time(), "goal_identity": list(identity),
        **({"source_message_id": source_uid} if source_uid else {})}
    if reason == "checkpoint_decision_unavailable" and source_uid:
        threshold = (state.runtime_recovery.get("checkpoint") or {}).get("threshold")
        if threshold is not None:
            dispositions = dict(state.runtime_control.get("checkpoint_dispositions") or {})
            dispositions[str(threshold)] = {"status": "decision_unavailable", "source_message_id": source_uid}
            state.runtime_control["checkpoint_dispositions"] = dispositions
    return db.compare_and_swap_meta(_meta_key(sid), raw, state.to_json())


def claim_recovery(db, sid, kind, *, expected_identity, max_attempts=2, deadline, threshold=None):
    """Claim BEFORE dispatch. Unknown delivery keeps the attempt spent across restart.

    A normal later milestone can start a new episode only after a durable decision
    before that later threshold. Overdue thresholds share the original episode.
    """
    from hermes_cli.goals import _meta_key
    from hermes_cli.goals_runtime import runtime_elapsed, runtime_wall_deadline
    for _ in range(3):
        raw, state = _read(db, sid)
        if not _same_active(state, expected_identity):
            return {"allowed": False, "reason": "user_control_or_deadline"}
        now = time.time(); previous = dict(state.runtime_recovery.get(kind) or {})
        rec = previous
        advance = (kind == "checkpoint" and previous and threshold != previous.get("threshold")
                   and previous.get("completed_source_uid") and previous.get("observed_elapsed", float("inf")) < threshold
                   and any(r.get("source_message_id") == previous["completed_source_uid"] for r in state.runtime_checkpoints))
        if not previous or advance:
            rec = {"episode_id": uuid.uuid4().hex, "started_at": now, "attempts_used": 0,
                   "total_attempts": previous.get("total_attempts", previous.get("attempts_used", 0)),
                   "deadline": min(deadline, runtime_wall_deadline(state)), "max_attempts": max_attempts,
                   "threshold": threshold, "observed_elapsed": runtime_elapsed(state)}
        if rec.get("completed_source_uid") and threshold == rec.get("threshold"):
            return {**rec, "allowed": False, "reason": "already_committed"}
        rec["deadline"] = min(rec["deadline"], deadline, runtime_wall_deadline(state))
        if now >= rec["deadline"] or rec["attempts_used"] >= rec["max_attempts"]:
            return {**rec, "allowed": False, "reason": "recovery_episode_exhausted"}
        rec.update(attempts_used=rec["attempts_used"] + 1, total_attempts=rec["total_attempts"] + 1,
                   last_attempt_at=now, operation_id=uuid.uuid4().hex, delivery_status="intent")
        state.runtime_recovery[kind] = rec
        if db.compare_and_swap_meta(_meta_key(sid), raw, state.to_json()):
            return {**rec, "allowed": True, "reason": "claimed"}
    return {"allowed": False, "reason": "state_conflict"}


def finish_recovery(db, sid, kind, expected_identity, source_uid):
    from hermes_cli.goals import _meta_key
    raw, state = _read(db, sid)
    if not _same_active(state, expected_identity):
        return False
    if not any(r.get("source_message_id") == source_uid for r in state.runtime_checkpoints):
        return False
    rec = dict(state.runtime_recovery.get(kind) or {})
    rec.update(completed_source_uid=source_uid, completed_at=time.time(), delivery_status="received")
    state.runtime_recovery[kind] = rec
    return db.compare_and_swap_meta(_meta_key(sid), raw, state.to_json())


def refresh_control(db, sid, state, work):
    from hermes_cli.goals_runtime import runtime_identity
    projection = control_projection(state, work)
    if projection["phase"] == "closing" and state.runtime_control.get("phase") != "closing":
        if not technical_closing(db, sid, runtime_identity(state), projection["reason"]):
            raise RuntimeError("goal changed during completion admission")
        _, state = _read(db, sid)
        projection = control_projection(state, work)
    return state, projection


def admit_action(agent, *, purpose, max_seconds, operation_id=None, blocker=None, scope=None, work=None):
    """One dispatch guard for native model, tools, reviews and child operations."""
    from hermes_cli.goals_runtime import runtime_identity, runtime_remaining
    if getattr(agent, '_runtime_turn_identity_error', None):
        return {"allowed": False, "reason": "original_goal_identity_unavailable"}
    db = getattr(agent, "_session_db", None); sid = getattr(agent, "session_id", None)
    if db is None or not sid:
        return {"allowed": True, "deadline": None, "reason": "standalone"}
    _, state = _read(db, sid)
    original = getattr(agent, '_runtime_turn_identity', None)
    if original is not None and (state is None or runtime_identity(state) != tuple(original)):
        return {"allowed": False, "reason": "goal_replaced"}
    if state is not None and not _same_active(state, runtime_identity(state)):
        return {"allowed": False, "reason": "user_control_or_deadline"}
    if state is None or not enabled(state):
        return {"allowed": True, "deadline": None, "reason": "legacy_policy"}
    work = read_work_projection(sid, state) if work is None else work
    state, control = refresh_control(db, sid, state, work)
    phase = control["phase"]; now = time.time()
    if phase in {None, "settlement", "exhausted"}:
        return {"allowed": False, "reason": control["reason"], "control": control}
    if purpose == "discovery" and (phase == "closing" or max_seconds + state.runtime_policy.get("cancel_seconds", 15.) + control["reserve_seconds"] > runtime_remaining(state)):
        technical_closing(db, sid, runtime_identity(state), "report_reserve_enforced")
        return {"allowed": False, "reason": "report_reserve_enforced", "control": control}
    if phase == "closing" and purpose == "targeted_verification":
        if not blocker or blocker not in (work.get("blockers") or []) or not scope:
            return {"allowed": False, "reason": "unbound_report_verification", "control": control}
    deadline = control["ready_deadline"]
    if purpose == "discovery":
        deadline = min(deadline, control["latest_safe_close"])
    due = control.get("checkpoint_due")
    if due is None and purpose == "discovery":
        from hermes_cli.goals_runtime import RUNTIME_CHECKPOINT_THRESHOLDS, runtime_elapsed
        elapsed = runtime_elapsed(state)
        future = [x for x in RUNTIME_CHECKPOINT_THRESHOLDS if elapsed < x < state.runtime_budget_seconds]
        if future:
            deadline = min(deadline, now + min(future) - elapsed + state.runtime_policy["checkpoint_lateness_seconds"])
    from agent.deadline import native_operation_deadline
    inherited = native_operation_deadline()
    if inherited is not None:
        deadline = min(deadline, inherited)
    for attr in ("_runtime_checkpoint_deadline",):
        bound = getattr(agent, attr, None)
        if isinstance(bound, (int, float)):
            deadline = min(deadline, bound)
    deadline = min(deadline, now + max_seconds)
    if deadline <= now:
        return {"allowed": False, "reason": "operation_deadline_expired", "control": control}
    # The lease and native control are revalidated at actual dispatch too; this grant
    # does not reserve a provider quota or confer authority after a manual control.
    return {"allowed": True, "deadline": deadline, "goal_identity": list(runtime_identity(state)),
            "operation_id": operation_id or uuid.uuid4().hex, "work_digest": work.get("digest"),
            "purpose": purpose, "control": control}


def operation_timeout(agent, configured):
    """Apply original operation, phase and native deadlines even to explicit timeouts."""
    from hermes_cli.goals_runtime import runtime_wall_deadline
    db = getattr(agent, "_session_db", None);sid = getattr(agent, "session_id", None)
    deadlines = [getattr(agent, "_runtime_checkpoint_deadline", None)]
    if db is not None and isinstance(sid, str) and sid:
        _, state = _read(db, sid)
        if state is not None:
            deadlines.append(runtime_wall_deadline(state))
            if enabled(state) and state.status == "active":
                deadlines.append(control_projection(state).get("ready_deadline"))
    from agent.deadline import native_operation_deadline
    deadlines.append(native_operation_deadline())
    valid = [x for x in deadlines if isinstance(x, (int, float)) and math.isfinite(x)]
    return min(configured, max(.001, min(valid) - time.time())) if valid else configured


def settle_native(db, sid, *, expected_identity, reason, work=None, pause=False):
    """Model-free local receipt. Artifact, review and delivery are independent axes."""
    from hermes_cli.goals import _meta_key
    from hermes_cli.goals_runtime import runtime_identity, runtime_elapsed
    raw, state = _read(db, sid)
    if state is None or runtime_identity(state) != tuple(expected_identity):
        return {"status": "superseded"}
    work = read_work_projection(sid, state) if work is None else work
    artifact = work.get("artifact_status", "partial_checkpoint")
    if artifact not in {"final_ready", "final_ready_with_limitations", "draft_preserved", "partial_checkpoint"}:
        artifact = "partial_checkpoint"
    receipt = {"schema": "native-goal-settlement-1", "goal_identity": list(expected_identity),
               "at": time.time(), "reason": reason, "artifact_status": artifact,
               "delivery_status": work.get("delivery_status", "unconfirmed"), "goal_status": state.status,
               "work_digest": work.get("digest"), "basis": work.get("basis"), "blockers": work.get("blockers"),
               "artifact_paths": [], "elapsed_seconds": runtime_elapsed(state), "retained_native_rows": True}
    # Paths in a projection are advisory. Verify every claimed existing artifact.
    from pathlib import Path
    for item in work.get("artifacts", []):
        p = Path(item["path"])
        try:
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
            if actual != item.get("sha256"):
                raise ValueError("artifact_changed")
            receipt["artifact_paths"].append({"path": str(p), "sha256": actual, "bytes": p.stat().st_size})
        except (OSError, ValueError) as exc:
            receipt.update(artifact_status="draft_preserved", artifact_error=type(exc).__name__)
    if artifact.startswith("final_ready") and not receipt["artifact_paths"]:
        receipt["artifact_status"] = "draft_preserved"
    if pause and state.status == "active" and not state.pause_requested:
        from hermes_cli.goals_runtime import settle_runtime
        settle_runtime(state)
        state.status, state.paused_reason, state.last_reason = "paused", reason, reason
        state.clear_wait()
        receipt["goal_status"] = "paused"
    state.runtime_settlement = receipt
    if not db.compare_and_swap_meta(_meta_key(sid), raw, state.to_json()):
        return {"status": "settlement_failed", "reason": "state_conflict"}
    return {**receipt, "status": "preserved"}


class NativeAdmissionDenied(InterruptedError):
    """A native control decision, never an upstream provider failure."""


def _deny(agent, reason):
    agent._runtime_completion_denied = {"reason": reason}
    interrupt = getattr(agent, 'interrupt', None)
    if callable(interrupt):
        interrupt('Native completion boundary: ' + reason, hard_cancel=True,
                  tool_reason='native_completion_boundary')
    raise NativeAdmissionDenied(reason)


def admit_model_request(agent, request_id):
    if getattr(agent, '_runtime_turn_identity_error', None):
        _deny(agent, 'original_goal_identity_unavailable')
    from agent.deadline import native_operation_deadline
    inherited = native_operation_deadline()
    if inherited is not None and inherited <= time.time():
        _deny(agent, 'original_operation_deadline_expired')
    db = getattr(agent, '_session_db', None);sid = getattr(agent, 'session_id', None)
    if db is None or not isinstance(sid, str) or not sid:
        return
    _, state = _read(db, sid)
    from hermes_cli.goals_runtime import runtime_identity, runtime_remaining
    original = getattr(agent, '_runtime_turn_identity', None)
    if original is not None and (state is None or runtime_identity(state) != tuple(original)):
        _deny(agent, 'goal_replaced')
    if state is not None and (state.status != 'active' or state.pause_requested or
            (state.runtime_budget_seconds is not None and runtime_remaining(state) in (None, 0))):
        _deny(agent, 'user_control_or_deadline')
    if state is None or not enabled(state):
        return
    if getattr(agent, '_native_operation_id', None) != request_id:
        agent._native_operation_deadline = None
        agent._native_operation_id = request_id
    purpose = 'checkpoint' if getattr(agent, '_runtime_checkpoint_request', False) else 'report_model'
    work = read_work_projection(sid, state)
    review = work.get('review_work') or {}
    artifacts = work.get('artifact_work') or {}
    # Reserve from an EXISTING iteration allowance, not a new closing quota.
    # These are lower bounds on dependent native requests, not model predictions.
    minimum = max(1, 2 * int(review.get('remaining_rounds') or 0)
        + int(bool(review.get('unanswered_findings')))
        + int(bool(review.get('final_review_needed')))
        + sum(int(bool(artifacts.get(k))) for k in ('render_needed', 'export_needed', 'selection_needed')))
    remaining_calls = getattr(getattr(agent, 'iteration_budget', None), 'remaining', None)
    if (purpose != 'checkpoint' and isinstance(remaining_calls, (int, float))
            and not isinstance(remaining_calls, bool) and remaining_calls <= minimum):
        technical_closing(db, sid, (state.created_at, state.goal), 'main_request_slots_reserved')
    packet_digest = getattr(agent, '_runtime_checkpoint_work_digest', None)
    if purpose == 'checkpoint' and packet_digest is not None and packet_digest != work.get('digest'):
        _deny(agent, 'checkpoint_work_basis_changed')
    grant = admit_action(agent, purpose=purpose, max_seconds=state.runtime_policy['model_bound_seconds'], operation_id=request_id, work=work)
    if not grant['allowed']:
        agent._runtime_completion_denied = grant
        agent.interrupt('Native completion boundary: ' + grant['reason'], hard_cancel=True,
                        tool_reason='native_completion_boundary')
        _deny(agent, grant['reason'])
    agent._native_operation_deadline = grant['deadline']
    # A queued retry is tied to the accepted input basis and original deadline.
    previous = getattr(agent, '_native_model_grant', None)
    if previous and previous['operation_id'] == request_id:
        grant['deadline'] = min(grant['deadline'], previous['deadline'])
        if grant['deadline'] <= time.time():
            _deny(agent, 'original_operation_deadline_expired')
        if previous['goal_identity'] != grant['goal_identity']:
            _deny(agent, 'goal_replaced')
    agent._native_operation_deadline = grant['deadline']
    if previous and previous['operation_id'] == request_id and previous.get('work_digest') != grant.get('work_digest'):
        _deny(agent, 'accepted_work_basis_changed')
    agent._native_model_grant = grant


def admit_tool_request(agent, name, arguments, call_id):
    if getattr(agent, '_runtime_turn_identity_error', None):
        return json.dumps({'error':'original_goal_identity_unavailable','tool_not_executed':True}), arguments
    from hermes_cli import plugins
    db = getattr(agent, '_session_db', None);sid = getattr(agent, 'session_id', None)
    from agent.deadline import native_operation_deadline
    inherited = native_operation_deadline()
    if inherited is not None and inherited <= time.time():
        return json.dumps({'error':'original_operation_deadline_expired','tool_not_executed':True}), arguments
    if db is None or not isinstance(sid, str) or not sid:
        return None, arguments
    _, state = _read(db, sid)
    from hermes_cli.goals_runtime import runtime_identity, runtime_remaining
    from agent.deadline import native_operation_deadline
    inherited = native_operation_deadline()
    if inherited is not None and inherited <= time.time():
        return json.dumps({'error':'original_operation_deadline_expired','tool_not_executed':True}), arguments
    original = getattr(agent, '_runtime_turn_identity', None)
    if original is not None and (state is None or runtime_identity(state) != tuple(original)):
        return json.dumps({'error':'goal_replaced','tool_not_executed':True}), arguments
    if state is not None and (state.status != 'active' or state.pause_requested or
            (state.runtime_budget_seconds is not None and runtime_remaining(state) in (None, 0))):
        return json.dumps({'error':'user_control_or_deadline','tool_not_executed':True}), arguments
    if state is None or not enabled(state):
        return None, arguments
    args = dict(arguments);envelope = args.pop('_native_action', None)
    work = read_work_projection(sid, state)
    classified = plugins.invoke_hook('goal_work_projection', session_id=sid,
        goal_identity=list((state.created_at, state.goal)), mode='classify_action',
        completion_contract=state.runtime_policy.get('completion_contract', {}),
        action={'tool': name, 'arguments': args})
    actions = [v for v in classified if isinstance(v, dict) and v.get('purpose')]
    action = actions[0] if len(actions) == 1 else {'purpose': 'discovery'}
    purpose = action['purpose'];scope = None;blocker = None
    bound = action.get('max_seconds') or 420.
    binding = action.get('verification_binding') or {}
    args_digest = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if purpose == 'targeted_verification':
        scope, blocker = binding.get('scope'), binding.get('blocker')
        bound = binding.get('max_seconds') or bound
        targets = (scope or {}).get('targets') if isinstance(scope, dict) else None
        output = binding.get('max_output_bytes')
        if (binding.get('arguments_sha256') != args_digest or blocker not in (work.get('blockers') or [])
                or not isinstance(targets, list) or not 0 < len(targets) <= 4
                or not all(isinstance(x, str) and x.strip() for x in targets)
                or not isinstance(bound, (int, float)) or isinstance(bound, bool)
                or not math.isfinite(bound) or bound <= 0
                or not isinstance(output, int) or isinstance(output, bool) or output <= 0):
            return json.dumps({'error':'native_action_scope_invalid','tool_not_executed':True}), args
    if envelope is not None:
        # Purpose alone is no authority. A narrow verification must name a current
        # canonical blocker and finite input/output/time bounds; shell remains a
        # forensic action with the existing sandbox and read-only permissions.
        if not isinstance(envelope, dict):
            return json.dumps({'error':'native_action_scope_invalid','tool_not_executed':True}), args
        targets = (envelope.get('scope') or {}).get('targets')
        maximum = envelope.get('max_seconds') if isinstance(envelope, dict) else None
        output = envelope.get('max_output_bytes') if isinstance(envelope, dict) else None
        if (action.get('verification_binding') and action['verification_binding'].get('arguments_sha256') == args_digest
                and purpose == 'targeted_verification' and envelope.get('purpose') == 'targeted_verification'
                and envelope.get('blocker') in (work.get('blockers') or [])
                and action['verification_binding'].get('blocker') == envelope.get('blocker')
                and action['verification_binding'].get('scope') == envelope.get('scope')
                and action['verification_binding'].get('max_output_bytes') == envelope.get('max_output_bytes')
                and action['verification_binding'].get('max_seconds') == envelope.get('max_seconds')
                and isinstance(targets, list) and 0 < len(targets) <= 4
                and all(isinstance(x, str) and x.strip() for x in targets)
                and isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and 0 < maximum <= bound
                and isinstance(output, int) and not isinstance(output, bool) and 0 < output
                and isinstance(envelope.get('expected_result'), str) and envelope['expected_result'].strip()):
            purpose, scope, blocker, bound = 'targeted_verification', envelope['scope'], envelope['blocker'], maximum
        else:
            return json.dumps({'error': 'native_action_scope_invalid', 'next_step': 'Use a current blocker and finite verification scope; a report label is insufficient.'}), args
    grant = admit_action(agent, purpose=purpose, max_seconds=bound, operation_id=call_id, blocker=blocker, scope=scope, work=work)
    if not grant['allowed']:
        return json.dumps({'error': grant['reason'], 'native_control': grant.get('control'),
            'next_action': work.get('next_action'), 'tool_not_executed': True}, ensure_ascii=False), args
    # Keep the operation claim through retries. Parallel tools have individual
    # call IDs; the current native lease remains the execution owner.
    grants = getattr(agent, '_native_tool_grants', None)
    if grants is None:
        grants = agent._native_tool_grants = {}
    previous = grants.get(call_id)
    if previous:
        if previous['goal_identity'] != grant['goal_identity']:
            return json.dumps({'error':'goal_replaced','tool_not_executed':True}), args
        if previous.get('work_digest') != grant.get('work_digest') or previous.get('args_digest') != args_digest:
            return json.dumps({'error':'accepted_work_basis_changed','tool_not_executed':True}), args
        grant['deadline'] = min(grant['deadline'], previous['deadline'])
    grant.update(args_digest=args_digest, blocker=blocker, scope=scope, max_seconds=bound)
    grants[call_id] = grant
    # Context propagation to delegated workers carries this original bound.
    return None, args


def migrate_policy(db, sid, *, expected_identity, completion_contract, authorization_ref):
    """Explicit paused-goal migration. Consumption, receipts and stop reason survive."""
    from hermes_cli.goals import _meta_key
    from hermes_cli.goals_runtime import runtime_identity, runtime_remaining
    raw, state = _read(db, sid)
    if (state is None or state.status != 'paused' or state.pause_requested
            or runtime_identity(state) != tuple(expected_identity) or not authorization_ref
            or state.runtime_budget_seconds is None or runtime_remaining(state) is None):
        raise ValueError('policy_migration_requires_verified_paused_goal_and_authorization')
    if state.runtime_recovery:
        raise ValueError('pending_recovery_requires_current_policy_reconciliation')
    before = dict(state.runtime_policy)
    state.runtime_policy = make_policy(state.runtime_budget_seconds, completion_contract)
    state.runtime_policy['migration'] = {'from': before.get('revision', 'legacy-v1'), 'at':time.time(),
        'authorization_ref':authorization_ref, 'elapsed_seconds':state.runtime_elapsed_seconds,
        'turns_used':state.turns_used, 'legacy_tool_cutoff_fraction': .95}
    return db.compare_and_swap_meta(_meta_key(sid), raw, state.to_json())
