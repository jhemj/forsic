"""Native goal pause at response/tool-batch boundaries; never signals running work."""
from __future__ import annotations

import threading

SAFE_PAUSE_REASON = "safe_paused_by_user"
SAFE_PAUSE_NOTICE = "Paused at the current response/tool boundary."


def goal_pause_requested(agent) -> bool:
    """Read pending intent without revoking accepted tool-batch authority."""
    if getattr(agent, "_interrupt_requested", False):
        return False
    db = getattr(agent, "_session_db", None)
    session_id = getattr(agent, "session_id", None)
    if db is None or not isinstance(session_id, str) or not session_id:
        return False
    from hermes_cli.goals import GoalState
    raw = db.get_meta("goal:" + session_id)
    state = GoalState.from_json(raw) if isinstance(raw, str) and raw else None
    return bool(state and state.status == "active" and state.pause_requested)


def settle_requested_goal_pause(agent, *, reason: str = "user-safe-paused") -> bool:
    """Use the owning native SessionDB; profileless/forked agents have no goal to pause.

    Call only when no model response or tool batch is in flight. The persisted request
    survives transport loss and process restart. GoalManager's CAS preserves newer controls.
    """
    if getattr(agent, "_interrupt_requested", False):
        return False  # immediate native cancellation retains priority and attribution
    db = getattr(agent, "_session_db", None)
    session_id = getattr(agent, "session_id", None)
    if db is None or not isinstance(session_id, str) or not session_id:
        return False
    from hermes_cli.goals import GoalManager, GoalState, _StaleGoalEvaluation

    raw = db.get_meta("goal:" + session_id)
    state = GoalState.from_json(raw) if isinstance(raw, str) and raw else None
    if state is None or state.status != "active" or not state.pause_requested:
        return False
    # Use the same isolated CAS worker as native goal evaluation, pinned to the
    # agent's own DB rather than an off-thread/default profile lookup.
    manager = object.__new__(GoalManager)
    manager._state_lock = threading.RLock()
    manager.session_id = session_id
    manager._state = state
    manager.default_max_turns = manager._state.max_turns
    manager._evaluation_db, manager._evaluation_expected = db, raw
    try:
        manager.settle_pause(reason=reason)
    except _StaleGoalEvaluation:
        pass  # newer pause/clear/replacement wins; the old turn still stops
    return True
