"""Native turn lease owns and cancels the whole-goal deadline without a second loop."""
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

from agent.turn_facade_lease import DurableTurnLease
from hermes_cli import goals, goals_runtime as runtime
from hermes_state import SessionDB


@pytest.fixture
def runtime_turn(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=1000.)
    fake_time = SimpleNamespace(time=lambda: clock.now)
    monkeypatch.setattr(goals, "time", fake_time)
    monkeypatch.setattr(runtime, "time", fake_time)
    db = SessionDB(tmp_path / "state.db")
    state = goals.GoalState("Synthetic", created_at=1000., runtime_budget_seconds=100.,
                            runtime_started_at=1000.)
    db.set_meta("goal:task", state.to_json())
    agent = SimpleNamespace(session_id="task", run_budget_seconds=100., interrupt=Mock())
    lease = DurableTurnLease(agent, db, "task", "holder")
    lease.turn_active = True
    scheduled = []
    def schedule(callback, seconds):
        handle = SimpleNamespace(cancel=Mock())
        scheduled.append((callback, seconds, handle))
        return handle
    monkeypatch.setattr("agent.periodic_scheduler.schedule", schedule)
    yield clock, db, agent, lease, scheduled
    db.close()


def test_one_lifetime_deadline_and_native_hard_cancel(runtime_turn):
    clock, db, agent, lease, scheduled = runtime_turn
    clock.now += 60
    lease._start_goal_runtime_deadline()
    assert len(scheduled) == 1 and scheduled[0][1] == 40
    clock.now += 40
    assert scheduled[0][0]() is False
    state = goals.GoalState.from_json(db.get_meta("goal:task"))
    assert state.status == "paused" and state.runtime_elapsed_seconds == 100
    agent.interrupt.assert_called_once_with(
        lease.interrupt_message, hard_cancel=True, tool_reason=runtime.RUNTIME_BUDGET_REASON)
    lease.stop_refresher()
    lease.join_threads()
    scheduled[0][2].cancel.assert_called_once_with(wait=1.0)


@pytest.mark.parametrize("control", ["user-paused", "new-goal", "turn-finished", "prepaused"])
def test_late_deadline_respects_native_controls_and_goal_identity(runtime_turn, control):
    clock, db, agent, lease, scheduled = runtime_turn
    lease._start_goal_runtime_deadline()
    state = goals.GoalState.from_json(db.get_meta("goal:task"))
    if control == "user-paused":
        state.status, state.paused_reason = "paused", "user-paused"
    elif control == "new-goal":
        state.goal = "Explicit replacement scope"
    elif control == "turn-finished":
        lease.stop_refresher()
    else:
        runtime.pause_expired_runtime(state, now=1100)
    db.set_meta("goal:task", state.to_json())
    clock.now = 1100
    scheduled[0][0]()
    if control == "prepaused":
        # A status reader may persist budget pause before the scheduled callback.
        agent.interrupt.assert_called_once()
    else:
        agent.interrupt.assert_not_called()


def test_deadline_resolves_rotated_session_in_owning_db(runtime_turn):
    clock, db, agent, lease, scheduled = runtime_turn
    lease._start_goal_runtime_deadline()
    db.set_meta("goal:compacted", db.get_meta("goal:task"))
    original = goals.GoalState.from_json(db.get_meta("goal:task"))
    original.status = "cleared"
    db.set_meta("goal:task", original.to_json())
    agent.session_id = "compacted"
    clock.now += 100
    scheduled[0][0]()
    assert goals.GoalState.from_json(db.get_meta("goal:compacted")).status == "paused"
    assert goals.GoalState.from_json(db.get_meta("goal:task")).status == "cleared"
    agent.interrupt.assert_called_once()


def test_no_allowance_has_no_deadline_handle(runtime_turn):
    clock, db, agent, lease, scheduled = runtime_turn
    state = goals.GoalState("Legacy", created_at=1000.)
    db.set_meta("goal:task", state.to_json())
    agent.run_budget_seconds = None
    lease._start_goal_runtime_deadline()
    assert scheduled == []
    agent.interrupt.assert_not_called()


def test_deadline_native_interrupt_aborts_inflight_request_and_children(runtime_turn):
    clock, db, agent, lease, scheduled = runtime_turn
    from agent.interrupt_control import InterruptControlMixin, interrupted_during_api_call_reason
    abort = threading.Event()
    agent._hard_interrupt_requested = threading.Event()
    agent._execution_thread_id = None
    agent._active_children_lock = threading.Lock()
    child = SimpleNamespace(interrupt=Mock())
    agent._active_children = [child]
    agent.quiet_mode = True
    agent._active_request_abort = lambda reason: abort.set()
    agent.interrupt = lambda *args, **kwargs: InterruptControlMixin.interrupt(agent, *args, **kwargs)
    entered = threading.Event()
    def blocked_request():
        entered.set()
        abort.wait(5)
    request = threading.Thread(target=blocked_request)
    request.start()
    try:
        assert entered.wait(5)
        lease._start_goal_runtime_deadline()
        clock.now += 100
        scheduled[0][0]()
        request.join(5)
        assert not request.is_alive() and abort.is_set()
        assert agent._hard_interrupt_requested.is_set()
        assert agent._tool_interrupt_reason == runtime.RUNTIME_BUDGET_REASON
        assert interrupted_during_api_call_reason(agent) == "interrupted_during_api_call(goal_runtime_budget_exhausted)"
        child.interrupt.assert_called_once()
    finally:
        abort.set()
        request.join(5)


def test_contract_and_subgoal_edits_never_disarm_deadline(runtime_turn):
    clock, db, agent, lease, scheduled = runtime_turn
    lease._start_goal_runtime_deadline()
    state = goals.GoalState.from_json(db.get_meta("goal:task"))
    state.subgoals.append("Also check the competing explanation")
    state.contract.verification = "Inspect the persisted result"
    db.set_meta("goal:task", state.to_json())
    clock.now += 100
    scheduled[0][0]()
    assert goals.GoalState.from_json(db.get_meta("goal:task")).status == "paused"
    agent.interrupt.assert_called_once()


def test_native_monotonic_deadline_survives_backward_wall_clock(runtime_turn):
    clock, db, agent, lease, scheduled = runtime_turn
    lease._start_goal_runtime_deadline()
    assert scheduled[0][1] == 100
    clock.now -= 500
    # The native scheduler invokes this callback after 100 monotonic seconds.
    scheduled[0][0]()
    state = goals.GoalState.from_json(db.get_meta("goal:task"))
    assert state.status == "paused" and state.runtime_elapsed_seconds == 100
    agent.interrupt.assert_called_once()


@pytest.mark.parametrize("control", ["replacement", "user-pause"])
def test_control_after_expiry_cas_wins_before_interrupt_publication(runtime_turn, monkeypatch, control):
    clock, db, agent, lease, scheduled = runtime_turn
    lease._start_goal_runtime_deadline()
    clock.now += 100
    enforce = runtime.enforce_goal_runtime
    def raced(*args, **kwargs):
        state, expired = enforce(*args, **kwargs)
        assert expired
        if control == "replacement":
            replacement = goals.GoalState("New explicit goal", created_at=clock.now,
                runtime_budget_seconds=200., runtime_started_at=clock.now)
        else:
            replacement = goals.GoalState.from_json(db.get_meta("goal:task"))
            replacement.paused_reason = "user-paused"
        db.set_meta("goal:task", replacement.to_json())
        return state, expired
    monkeypatch.setattr(runtime, "enforce_goal_runtime", raced)
    scheduled[0][0]()
    current = goals.GoalState.from_json(db.get_meta("goal:task"))
    if control == "replacement":
        assert current.status == "active" and current.goal == "New explicit goal"
    else:
        assert current.status == "paused" and current.paused_reason == "user-paused"
    agent.interrupt.assert_not_called()


def test_runtime_deadline_interrupt_copy_uses_korean_catalog(runtime_turn, monkeypatch):
    clock, db, agent, lease, scheduled = runtime_turn
    monkeypatch.setattr("agent.i18n._resolve_language", lambda *args: "ko")
    lease._start_goal_runtime_deadline()
    clock.now += 100
    scheduled[0][0]()
    assert "전체 실행 시간 한도" in lease.interrupt_message
    agent.interrupt.assert_called_once_with(lease.interrupt_message,
        hard_cancel=True, tool_reason=runtime.RUNTIME_BUDGET_REASON)


def test_same_goal_cas_conflict_rearms_expired_deadline(runtime_turn, monkeypatch):
    clock, db, agent, lease, scheduled = runtime_turn
    lease._start_goal_runtime_deadline()
    clock.now += 100
    cas = db.compare_and_swap_meta
    first = True
    def race_once(key, expected, new):
        nonlocal first
        if first:
            first = False
            state = goals.GoalState.from_json(expected)
            state.last_reason = "A concurrent same-goal control"
            db.set_meta(key, state.to_json())
        return cas(key, expected, new)
    monkeypatch.setattr(db, "compare_and_swap_meta", race_once)
    scheduled[0][0]()
    agent.interrupt.assert_not_called()
    assert len(scheduled) == 2 and scheduled[1][1] == 1.0
    clock.now += scheduled[1][1]
    scheduled[1][0]()
    current = goals.GoalState.from_json(db.get_meta("goal:task"))
    assert current.status == "paused" and current.paused_reason == runtime.RUNTIME_BUDGET_REASON
    agent.interrupt.assert_called_once()


@pytest.mark.parametrize("control", ["same-goal", "user-pause", "replacement"])
def test_initial_budget_attach_conflict_keeps_native_deadline_owner(runtime_turn, monkeypatch, control):
    clock, db, agent, lease, scheduled = runtime_turn
    legacy = {"goal": "Legacy active scope", "status": "active", "created_at": 1000.}
    import json
    db.set_meta("goal:task", json.dumps(legacy))
    cas = db.compare_and_swap_meta
    first = True
    def race_once(key, expected, new):
        nonlocal first
        if first:
            first = False
            state = goals.GoalState.from_json(expected)
            state.last_reason = "Concurrent same-goal edit before first attachment"
            db.set_meta(key, state.to_json())
        return cas(key, expected, new)
    monkeypatch.setattr(db, "compare_and_swap_meta", race_once)
    lease._start_goal_runtime_deadline()
    current = goals.GoalState.from_json(db.get_meta("goal:task"))
    assert current.runtime_budget_seconds is None and current.status == "active"
    assert len(scheduled) == 1 and scheduled[0][1] == 1.0
    if control == "user-pause":
        current.status, current.paused_reason = "paused", "user-paused"
    elif control == "replacement":
        current = goals.GoalState("Different explicit scope", created_at=1001.)
    db.set_meta("goal:task", current.to_json())
    clock.now += 1
    scheduled[0][0]()
    current = goals.GoalState.from_json(db.get_meta("goal:task"))
    if control == "same-goal":
        assert current.runtime_budget_seconds == 100
        assert len(scheduled) == 2 and scheduled[1][1] == 99
        clock.now += 99
        scheduled[1][0]()
        current = goals.GoalState.from_json(db.get_meta("goal:task"))
        assert current.status == "paused" and current.paused_reason == runtime.RUNTIME_BUDGET_REASON
        agent.interrupt.assert_called_once()
    else:
        assert len(scheduled) == 1
        if control == "user-pause":
            assert current.status == "paused" and current.paused_reason == "user-paused"
        else:
            assert current.goal == "Different explicit scope" and current.runtime_budget_seconds is None
        agent.interrupt.assert_not_called()


@pytest.mark.parametrize("control", ["user-pause", "replacement"])
def test_control_during_initial_attachment_is_not_adopted_as_old_goal(runtime_turn, monkeypatch, control):
    clock, db, agent, lease, scheduled = runtime_turn
    state = goals.GoalState("Legacy active scope", created_at=1000.)
    db.set_meta("goal:task", state.to_json())
    cas = db.compare_and_swap_meta
    def raced(key, expected, new):
        current = goals.GoalState.from_json(expected)
        if control == "replacement":
            current = goals.GoalState("New explicit scope", created_at=1001.)
        else:
            current.status, current.paused_reason = "paused", "user-paused"
        db.set_meta(key, current.to_json())
        return cas(key, expected, new)
    monkeypatch.setattr(db, "compare_and_swap_meta", raced)
    lease._start_goal_runtime_deadline()
    current = goals.GoalState.from_json(db.get_meta("goal:task"))
    assert scheduled == []
    assert current.runtime_budget_seconds is None
    if control == "replacement":
        assert current.goal == "New explicit scope" and current.status == "active"
    else:
        assert current.status == "paused" and current.paused_reason == "user-paused"
    agent.interrupt.assert_not_called()
