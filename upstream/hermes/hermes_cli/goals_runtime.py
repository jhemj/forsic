"""One durable runtime allowance for a native goal, shared by every turn.

An active interval includes WAIT and time across process restarts. Explicit pause
freezes it. Wall time is persisted because a monotonic clock cannot cross restarts;
backward clock jumps never subtract already accounted time.
"""
import math
import time


RUNTIME_BUDGET_REASON = "goal runtime budget exhausted"
RUNTIME_HISTORY_REASON = "goal runtime history unavailable"
RUNTIME_HANDOFF_REASON = "runtime_budget_handoff"
RUNTIME_CHECKPOINT_FAILED_REASON = "runtime_budget_checkpoint_failed"
RUNTIME_CHECKPOINT_THRESHOLDS = (12 * 3600, 18 * 3600)


def runtime_checkpoint_due(state, *, now=None):
    """Earliest unreceipted milestone; never authorize work beyond the shared cap."""
    if (state is None or state.status != "active" or state.pause_requested
            or runtime_remaining(state, now=now) in (None, 0)):
        return None
    if any(item.get("decision") == "finish" for item in state.runtime_checkpoints):
        return None  # the model already chose closing; do not restart discovery decisions
    seen = {item.get("threshold_seconds") for item in state.runtime_checkpoints}
    control = getattr(state, "runtime_control", None) or {}
    seen.update(int(k) for k, v in (control.get("checkpoint_dispositions") or {}).items()
                if k in {"43200", "64800"} and v.get("status") in {"decision_unavailable", "not_performed_due_to_deadline"})
    elapsed = runtime_elapsed(state, now=now)
    return next((threshold for threshold in RUNTIME_CHECKPOINT_THRESHOLDS
                 if threshold < state.runtime_budget_seconds and threshold <= elapsed
                 and threshold not in seen), None)


def runtime_phase(state, *, now=None, work=None):
    """Project the pinned policy; settlement takes priority over late milestones.

    This is a projection of the same durable allowance as the hard deadline, never
    another timer or a resettable per-turn budget. Paused goals authorize no phase.
    """
    if state is None or state.status != "active" or state.pause_requested:
        return None
    from hermes_cli.goals_completion import enabled, control_projection
    if enabled(state):
        control = control_projection(state, work, now=now)
        phase = control['phase']
        return ('checkpoint' if control['checkpoint_due'] is not None
                and phase not in {'settlement', 'exhausted'} else phase)
    remaining = runtime_remaining(state, now=now)
    if remaining is None:
        return None
    if remaining <= 0:
        return "exhausted"
    if remaining <= state.runtime_budget_seconds * 0.05:
        return "handoff"
    if any(item.get("decision") == "finish" for item in state.runtime_checkpoints):
        return "closing"
    if runtime_checkpoint_due(state, now=now) is not None:
        return "checkpoint"
    if (getattr(state, "runtime_control", None) or {}).get("phase") == "closing":
        return "closing"
    return "closing" if remaining <= state.runtime_budget_seconds * 0.1 else "investigation"


def finish_runtime_handoff(db, session_id, *, expected_goal_id, checkpoint_saved, now=None):
    """Pause this exact goal after a closing checkpoint, without minting time or completion.

    A human pause/replacement wins. The hard deadline retains priority if it elapsed
    during checkpoint persistence. Returns the reason committed here, or None on a race.
    """
    from hermes_cli.goals import GoalState, _meta_key
    expected = db.get_meta(_meta_key(session_id))
    state = GoalState.from_json(expected) if expected else None
    if (state is None or state.status != "active" or state.pause_requested
            or runtime_identity(state) != expected_goal_id):
        return None
    if not pause_expired_runtime(state, now=now):
        settle_runtime(state, now=now)
        state.status, state.runtime_started_at = "paused", None
        state.paused_reason = RUNTIME_HANDOFF_REASON if checkpoint_saved else RUNTIME_CHECKPOINT_FAILED_REASON
        state.last_verdict = "partial" if checkpoint_saved else "checkpoint_failed"
        state.last_reason = state.paused_reason
        state.clear_wait()
        state.evaluation_receipt = None
    return state.paused_reason if db.compare_and_swap_meta(_meta_key(session_id), expected, state.to_json()) else None


def commit_runtime_milestone(db, session_id, *, expected_goal_id, row, now=None):
    """CAS a decision only after its native source row is durable. Safe to replay.

    The row's native metadata also recovers a crash between row insert and this
    CAS; evidence text cannot supply a decision or a goal identity.
    """
    from hermes_cli.goals import GoalState, _meta_key
    from agent.message_metadata import MESSAGE_UID
    metadata = (row.get("display_metadata") or {}).get("runtime_milestone")
    uid = row.get(MESSAGE_UID)
    if (not isinstance(metadata, dict) or not uid or row.get("role") != "assistant"
            or metadata.get("goal_identity") != list(expected_goal_id)
            or metadata.get("threshold_seconds") not in RUNTIME_CHECKPOINT_THRESHOLDS
            or metadata.get("decision") not in {"continue", "finish"}
            or not isinstance(metadata.get("reason"), str) or not metadata["reason"].strip()):
        return "invalid"
    source = next((item for item in db.get_messages(session_id)
                   if item.get(MESSAGE_UID) == uid), None)
    if source is None or source.get("content") != row.get("content") or source.get("display_metadata") != row.get("display_metadata"):
        return "invalid"
    if metadata.get("report_work_digest") is not None:
        from hermes_cli.goals_completion import read_work_projection
        current_raw = db.get_meta(_meta_key(session_id))
        current_state = GoalState.from_json(current_raw) if current_raw else None
        if (current_state is None or runtime_identity(current_state) != expected_goal_id
                or read_work_projection(session_id, current_state).get("digest") != metadata["report_work_digest"]):
            return "invalid"
    expected = db.get_meta(_meta_key(session_id))
    state = GoalState.from_json(expected) if expected else None
    if (state is None or state.status != "active" or state.pause_requested
            or runtime_identity(state) != expected_goal_id):
        return None
    if pause_expired_runtime(state, now=now):
        return "exhausted" if db.compare_and_swap_meta(_meta_key(session_id), expected, state.to_json()) else None
    threshold = metadata["threshold_seconds"]
    existing = next((item for item in state.runtime_checkpoints if item["threshold_seconds"] == threshold), None)
    if existing:
        return existing["decision"] if existing["source_message_id"] == uid else None
    if runtime_checkpoint_due(state, now=now) != threshold:
        return None
    settle_runtime(state, now=now)
    state.runtime_checkpoints.append({"threshold_seconds": threshold, "decision": metadata["decision"],
                                     "reason": metadata["reason"], "decided_at": time.time() if now is None else now,
                                     "elapsed_seconds": state.runtime_elapsed_seconds, "source_message_id": uid})
    state.last_verdict, state.last_reason = "continue", metadata["reason"]
    if metadata["decision"] == "finish":
        # Closing intent is not completion or a pause. The same native turn loop
        # can still save, verify and render the answer/report inside its old cap.
        state.last_verdict = "closing"
        state.clear_wait()
        state.evaluation_receipt = None
    return metadata["decision"] if db.compare_and_swap_meta(_meta_key(session_id), expected, state.to_json()) else None


def recover_runtime_milestones(db, session_id):
    """Recover only native labelled, persisted decisions at a safe loop boundary."""
    from hermes_cli.goals import GoalState, _meta_key
    raw = db.get_meta(_meta_key(session_id))
    state = GoalState.from_json(raw) if raw else None
    if runtime_checkpoint_due(state) is None:
        return
    identity = runtime_identity(state)
    for row in db.get_messages(session_id):
        if (row.get("display_metadata") or {}).get("runtime_milestone"):
            result = commit_runtime_milestone(db, session_id, expected_goal_id=identity, row=row)
            if result in {"continue", "finish"}:
                from hermes_cli.goals_completion import finish_recovery
                from agent.message_metadata import MESSAGE_UID
                finish_recovery(db, session_id, "checkpoint", identity, row[MESSAGE_UID])
            if result in {"finish", "exhausted"}:
                return


def runtime_identity(state):
    # Contract/subgoal edits re-scope judging, but do not create a new allowance.
    return state.created_at, state.goal


def normalize_runtime_budget(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def normalize_runtime_clock(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def configured_runtime_budget():
    """Use the existing run allowance; pin it in the goal instead of renewing it each turn."""
    from hermes_cli.config import load_config_readonly
    config = load_config_readonly() or {}
    return normalize_runtime_budget((config.get("agent") or {}).get("run_budget_seconds"))


def runtime_elapsed(state, *, now=None):
    now = time.time() if now is None else now
    elapsed = state.runtime_elapsed_seconds
    if state.status == "active" and state.runtime_started_at is not None:
        elapsed += max(0.0, now - state.runtime_started_at)
    return elapsed


def runtime_remaining(state, *, now=None):
    if state is None or state.runtime_budget_seconds is None or not state.runtime_accounting_known:
        return None
    remaining = max(0.0, state.runtime_budget_seconds - runtime_elapsed(state, now=now))
    wall = getattr(state, "wall_deadline", None)
    return min(remaining, max(0.0, wall - (time.time() if now is None else now))) if wall is not None else remaining


def runtime_wall_deadline(state):
    if (state is None or state.status != "active" or state.runtime_budget_seconds is None
            or not state.runtime_accounting_known or state.runtime_started_at is None):
        return None
    deadline = state.runtime_started_at + max(0.0, state.runtime_budget_seconds - state.runtime_elapsed_seconds)
    wall = getattr(state, "wall_deadline", None)
    return min(deadline, wall) if wall is not None else deadline


def settle_runtime(state, *, now=None):
    now = time.time() if now is None else now
    state.runtime_elapsed_seconds = runtime_elapsed(state, now=now)
    if state.status == "active":
        state.runtime_started_at = max(now, state.runtime_started_at or now)
    else:
        state.runtime_started_at = None


def attach_runtime_budget(state, budget, *, now=None):
    """Migrate once. Historical pauses of legacy rows cannot be reconstructed.

    Active legacy goals use their recorded creation time as the conservative
    active interval. Paused legacy goals stay paused with unknown past active time;
    they cannot silently obtain a fresh allowance on resume.
    """
    budget = normalize_runtime_budget(budget)
    if budget is None or state.runtime_budget_seconds is not None:
        return False
    now = time.time() if now is None else now
    state.runtime_budget_seconds = budget
    if state.status == "active":
        if not state.runtime_accounting_known:
            state.runtime_started_at = state.created_at if state.created_at > 0 else now
            state.runtime_accounting_known = state.created_at > 0
        elif state.runtime_started_at is None:
            state.runtime_started_at = now
    return True


def pause_expired_runtime(state, *, now=None):
    unknown = state.runtime_budget_seconds is not None and not state.runtime_accounting_known
    if state.status != "active" or (not unknown and runtime_remaining(state, now=now) != 0):
        return False
    settle_runtime(state, now=now)
    state.status = "paused"
    state.runtime_started_at = None
    state.paused_reason = RUNTIME_HISTORY_REASON if unknown else RUNTIME_BUDGET_REASON
    state.clear_wait()
    state.evaluation_receipt = None
    return True


def enforce_goal_runtime(db, session_id, *, budget=None, now=None, expected_goal_id=None, due_deadline=None):
    """Read and CAS only this native goal row. Return the state and whether THIS call paused it.

    The caller supplies its owning SessionDB, so timer threads never infer a
    profile from process globals. A concurrent manual control wins the CAS.
    """
    from hermes_cli.goals import GoalState, _meta_key
    expected = db.get_meta(_meta_key(session_id))
    if not expected:
        return None, False
    state = GoalState.from_json(expected)
    if expected_goal_id is not None and runtime_identity(state) != expected_goal_id:
        return state, False
    # The shared native scheduler fires after monotonic elapsed time. Preserve
    # that live countdown if wall time moves backwards, provided pause/resume
    # has not changed the durable deadline in the meantime.
    current_deadline = runtime_wall_deadline(state)
    if (due_deadline is not None and current_deadline is not None
            and math.isclose(current_deadline, due_deadline, rel_tol=0.0, abs_tol=0.000001)):
        now = max(time.time() if now is None else now, due_deadline)
    changed = attach_runtime_budget(state, budget, now=now)
    expired = pause_expired_runtime(state, now=now)
    if changed or expired:
        if not db.compare_and_swap_meta(_meta_key(session_id), expected, state.to_json()):
            latest = db.get_meta(_meta_key(session_id))
            return (GoalState.from_json(latest) if latest else None), False
    return state, expired
