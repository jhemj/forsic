"""One durable runtime allowance for a native goal, shared by every turn.

An active interval includes WAIT and time across process restarts. Explicit pause
freezes it. Wall time is persisted because a monotonic clock cannot cross restarts;
backward clock jumps never subtract already accounted time.
"""
import math
import time


RUNTIME_BUDGET_REASON = "goal runtime budget exhausted"
RUNTIME_HISTORY_REASON = "goal runtime history unavailable"


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
    return max(0.0, state.runtime_budget_seconds - runtime_elapsed(state, now=now))


def runtime_wall_deadline(state):
    if (state is None or state.status != "active" or state.runtime_budget_seconds is None
            or not state.runtime_accounting_known or state.runtime_started_at is None):
        return None
    return state.runtime_started_at + max(0.0, state.runtime_budget_seconds - state.runtime_elapsed_seconds)


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
