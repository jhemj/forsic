"""Whole-goal time allowance across native turns, controls, restarts and aux cancellation."""
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hermes_cli import goals, goals_runtime as runtime
from hermes_state import SessionDB


@pytest.fixture
def clock_store(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    # A separate clock object avoids replacing time.time process-wide.
    fake_time = SimpleNamespace(time=lambda: clock.now)
    monkeypatch.setattr(goals, "time", fake_time)
    monkeypatch.setattr(runtime, "time", fake_time)
    db = SessionDB(tmp_path / "state.db")
    monkeypatch.setattr(goals, "_get_session_db", lambda: db)
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: 100.0)
    monkeypatch.setattr(goals, "judge_goal", lambda *a, **k: ("continue", "unfinished", False, None, False))
    monkeypatch.setattr("hermes_cli.goals_advisory.goal_advisory_result",
                        lambda **kw: {"context": "", "requires_followup": False})
    yield clock, db
    db.close()


def test_multiple_turns_and_restarts_share_one_allowance(clock_store):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Retain the partial investigation")
    clock.now += 30
    assert manager.evaluate_after_turn("More work remains")["should_continue"]
    assert runtime.runtime_remaining(manager.state) == 70
    # New objects/processes load the same interval; time between turns also counts.
    restarted = goals.GoalManager("task")
    clock.now += 40
    assert restarted.evaluate_after_turn("Still incomplete")["should_continue"]
    assert runtime.runtime_remaining(restarted.state) == 30
    clock.now += 30
    decision = restarted.evaluate_after_turn("Cannot finish within allowance")
    assert decision["status"] == "paused"
    assert decision["verdict"] == "runtime_budget"
    assert restarted.state.turns_used == 2
    assert restarted.state.goal == "Retain the partial investigation"
    assert restarted.state.last_verdict != "done"
    assert goals.GoalManager("task").next_continuation_prompt() is None


def test_manual_pause_freezes_time_and_resume_never_renews(clock_store):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Synthetic goal")
    clock.now += 35
    manager.pause()
    assert manager.state.runtime_elapsed_seconds == 35
    clock.now += 500
    restarted = goals.GoalManager("task")
    assert restarted.state.status == "paused"
    assert restarted.state.paused_reason == "user-paused"
    assert runtime.runtime_remaining(restarted.state) == 65
    restarted.resume()
    clock.now += 65
    restarted.evaluate_after_turn("Partial")
    assert restarted.state.status == "paused"
    assert restarted.resume().status == "paused"
    assert runtime.runtime_remaining(restarted.state) == 0
    from hermes_cli.goal_command import dispatch_goal_command
    result = dispatch_goal_command(restarted, "resume", authorize_gate=lambda *args: None)
    assert result.error and result.prompt is None
    assert "remains paused" in result.output


def test_new_goal_explicitly_gets_new_allowance(clock_store):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Old scope")
    clock.now += 100
    assert manager.next_continuation_prompt() is None
    manager.set("New explicit scope")
    assert manager.state.status == "active"
    assert runtime.runtime_remaining(manager.state) == 100


def test_wait_is_bounded_by_same_allowance(clock_store):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Wait for independent work")
    manager.wait_for_seconds(1000)
    clock.now += 100
    decision = manager.evaluate_after_turn("Waiting")
    assert decision["status"] == "paused"
    assert manager.state.waiting_until == 0
    assert manager.state.turns_used == 0


@pytest.mark.parametrize("value", [None, 0, -1, "", "bad", True, float("inf"), float("nan")])
def test_empty_or_invalid_allowance_keeps_legacy_behavior(clock_store, monkeypatch, value):
    clock, db = clock_store
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: value)
    manager = goals.GoalManager("task")
    manager.set("Legacy unlimited runtime", max_turns=2)
    clock.now += 100000
    assert manager.evaluate_after_turn("Partial")["should_continue"]
    assert manager.state.runtime_budget_seconds is None
    assert manager.evaluate_after_turn("Partial")["status"] == "paused"
    assert "turn budget" in manager.state.paused_reason


def test_legacy_migration_never_reconstructs_paused_history(clock_store):
    clock, db = clock_store
    old = {"goal": "Old paused task", "created_at": 1, "last_turn_at": 400,
           "status": "paused", "paused_reason": "user-paused"}
    db.set_meta("goal:paused", json.dumps(old))
    state = goals.GoalManager("paused").state
    assert state.status == "paused" and state.paused_reason == "user-paused"
    assert state.runtime_started_at is None
    assert state.runtime_elapsed_seconds == 0
    assert state.runtime_accounting_known is False
    assert runtime.runtime_remaining(state) is None
    assert goals.GoalManager("paused").resume().status == "paused"
    old.update(status="active", goal="Old active task")
    db.set_meta("goal:active", json.dumps(old))
    state = goals.GoalManager("active").state
    assert state.status == "paused"
    assert state.paused_reason == runtime.RUNTIME_BUDGET_REASON


def test_budget_cap_pinned_despite_config_change(clock_store, monkeypatch):
    clock, db = clock_store
    goals.GoalManager("task").set("Synthetic")
    clock.now += 20
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: 10000)
    state = goals.GoalManager("task").state
    assert state.runtime_budget_seconds == 100
    assert runtime.runtime_remaining(state) == 80


def test_backward_clock_never_subtracts_checkpointed_elapsed(clock_store):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Synthetic")
    clock.now += 40
    manager.evaluate_after_turn("Partial")
    clock.now -= 100
    assert runtime.runtime_remaining(manager.state) == 60
    manager.evaluate_after_turn("Partial")
    assert runtime.runtime_remaining(manager.state) == 60
    assert manager.state.runtime_started_at == 1040


def test_pending_review_does_not_extend_budget_or_gain_approval(clock_store, monkeypatch):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Synthetic")
    calls = []
    def advisory(**kwargs):
        calls.append(kwargs)
        clock.now += 50
        return {"context": "Unresolved finding", "requires_followup": True}
    monkeypatch.setattr("hermes_cli.goals_advisory.goal_advisory_result", advisory)
    assert manager.evaluate_after_turn("Candidate")['should_continue']
    assert manager.evaluate_after_turn("Candidate")['status'] == "paused"
    assert len(calls) == 2
    assert manager.state.last_verdict != "done"
    assert manager.state.evaluation_receipt is None


def test_late_done_verdict_cannot_mark_expired_goal_done(clock_store, monkeypatch):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Synthetic")
    def late_judge(*args, **kwargs):
        clock.now += 101
        return "done", "claims completion", False, None, False
    monkeypatch.setattr(goals, "judge_goal", late_judge)
    assert manager.evaluate_after_turn("Candidate")["status"] == "paused"
    assert manager.state.last_verdict != "done"


def test_inflight_aux_owner_cancelled_at_lifetime_deadline(clock_store, monkeypatch):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Synthetic")
    entered, release = threading.Event(), threading.Event()
    from agent.auxiliary_client import _run_protected_sync_provider_call
    def blocked_provider(kwargs):
        entered.set()
        release.wait(5)
        return "late completion"
    def judge(*args, **kwargs):
        _run_protected_sync_provider_call(blocked_provider, {})
        return "done", "late", False, None, False
    monkeypatch.setattr(goals, "judge_goal", judge)
    result = []
    thread = threading.Thread(target=lambda: result.append(manager.evaluate_after_turn("Candidate")))
    thread.start()
    try:
        assert entered.wait(5)
        clock.now += 101
        thread.join(5)
        assert not thread.is_alive()
        assert result[0]["status"] == "paused"
        assert manager.state.paused_reason == runtime.RUNTIME_BUDGET_REASON
    finally:
        release.set()
        thread.join(5)


def test_manual_pause_wins_over_inflight_evaluation(clock_store, monkeypatch):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Synthetic")
    def judge(*args, **kwargs):
        goals.GoalManager("task").pause()
        clock.now += 101
        return "done", "late", False, None, False
    monkeypatch.setattr(goals, "judge_goal", judge)
    assert manager.evaluate_after_turn("Candidate")["should_continue"] is False
    assert manager.state.status == "paused"
    assert manager.state.paused_reason == "user-paused"


def test_host_aux_cancellation_never_fabricates_budget_exhaustion(clock_store, monkeypatch):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Synthetic")
    from agent.auxiliary_client import AuxiliaryExplicitCancellation
    def cancelled(*args, **kwargs):
        raise AuxiliaryExplicitCancellation()
    monkeypatch.setattr(goals, "judge_goal", cancelled)
    result = manager.evaluate_after_turn("Candidate")
    assert result["verdict"] == "evaluation_cancelled"
    assert result["should_continue"] is False
    assert manager.state.status == "active"
    assert manager.state.paused_reason is None


def test_fenced_expiry_cannot_overwrite_concurrent_pause(clock_store, monkeypatch):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Synthetic")
    clock.now += 101
    original = db.compare_and_swap_meta
    def pause_first(key, expected, new):
        state = goals.GoalState.from_json(expected)
        state.status, state.paused_reason = "paused", "user-paused"
        db.set_meta(key, state.to_json())
        return original(key, expected, new)
    monkeypatch.setattr(db, "compare_and_swap_meta", pause_first)
    state, expired = runtime.enforce_goal_runtime(db, "task")
    assert not expired and state.paused_reason == "user-paused"


def test_budget_migrates_with_native_session(clock_store):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Synthetic")
    clock.now += 35
    assert goals.migrate_goal_to_session("task", "compacted")
    restored = goals.GoalManager("compacted")
    assert runtime.runtime_remaining(restored.state) == 65
    assert goals.load_goal("task").status == "cleared"


def test_config_reader_uses_existing_profile_allowance(monkeypatch):
    monkeypatch.setattr("hermes_cli.config.load_config_readonly",
                        lambda: {"agent": {"run_budget_seconds": "43200"}})
    assert runtime.configured_runtime_budget() == 43200
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: {})
    assert runtime.configured_runtime_budget() is None


@pytest.mark.parametrize("field,value", [
    ("runtime_elapsed_seconds", -1), ("runtime_elapsed_seconds", float("nan")),
    ("runtime_elapsed_seconds", float("inf")), ("runtime_elapsed_seconds", "bad"),
    ("runtime_started_at", -1), ("runtime_started_at", float("nan")),
    ("runtime_started_at", float("inf")), ("runtime_started_at", "bad"),
])
def test_invalid_persisted_clock_cannot_grant_more_time(clock_store, field, value):
    clock, db = clock_store
    data = json.loads(goals.GoalState("Synthetic", created_at=1000., runtime_budget_seconds=100.,
                                     runtime_started_at=1000.).to_json())
    data[field] = value
    db.set_meta("goal:task", json.dumps(data))
    state = goals.GoalManager("task").state
    assert state.status == "paused"
    assert not state.runtime_accounting_known
    assert state.paused_reason == runtime.RUNTIME_HISTORY_REASON


def test_actual_profile_config_and_session_store_do_not_share_allowance(tmp_path):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    homes = [tmp_path / "profile-a", tmp_path / "profile-b"]
    for index, home in enumerate(homes):
        home.mkdir()
        (home / "config.yaml").write_text(f"agent:\n  run_budget_seconds: {1000 + index * 1000}\n")
    try:
        for index in (0, 1, 0):
            token = set_hermes_home_override(homes[index])
            try:
                manager = goals.GoalManager("same-session")
                if not manager.has_goal():
                    manager.set(f"Scope {index}")
                assert manager.state.goal == f"Scope {index}"
                assert manager.state.runtime_budget_seconds == 1000 + index * 1000
            finally:
                reset_hermes_home_override(token)
    finally:
        for home in homes:
            db = goals._DB_CACHE.pop(str(home), None)
            if db is not None:
                goals._release_session_db(db)


def test_enabling_cap_does_not_double_count_existing_tracked_time(clock_store, monkeypatch):
    clock, db = clock_store
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: None)
    manager = goals.GoalManager("task")
    manager.set("Tracked before allowance was configured")
    clock.now += 30
    manager.pause()
    manager.resume()
    clock.now += 20
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: 100)
    restored = goals.GoalManager("task")
    assert runtime.runtime_remaining(restored.state) == 50


@pytest.mark.parametrize("change", ["subgoal", "contract"])
@pytest.mark.parametrize("pause_reason", ["user-paused", runtime.RUNTIME_BUDGET_REASON])
def test_stale_public_control_preserves_existing_pause(clock_store, change, pause_reason):
    clock, db = clock_store
    stale = goals.GoalManager("task")
    stale.set("Synthetic")
    if pause_reason == runtime.RUNTIME_BUDGET_REASON:
        clock.now += 100
        runtime.enforce_goal_runtime(db, "task")
    else:
        goals.GoalManager("task").pause()
    if change == "subgoal":
        stale.add_subgoal("An additional criterion")
    else:
        stale.set_contract(goals.GoalContract(verification="Check the result"))
    state = goals.load_goal("task")
    assert state.status == "paused" and state.paused_reason == pause_reason
    assert stale.next_continuation_prompt() is None
    if pause_reason == runtime.RUNTIME_BUDGET_REASON:
        assert runtime.runtime_remaining(state) == 0


@pytest.mark.parametrize("change", ["subgoal", "contract"])
def test_stale_public_control_preserves_replacement_goal(clock_store, change):
    clock, db = clock_store
    stale = goals.GoalManager("task")
    stale.set("Old goal")
    clock.now += 10
    replacement = goals.GoalManager("task").set("Explicit replacement")
    if change == "subgoal":
        stale.add_subgoal("Additional criterion for current goal")
    else:
        stale.set_contract(goals.GoalContract(verification="Check current goal"))
    state = goals.load_goal("task")
    assert state.goal == replacement.goal and state.created_at == replacement.created_at
    assert state.runtime_elapsed_seconds == 0
    assert runtime.runtime_remaining(state) == 100


@pytest.mark.parametrize("change", ["subgoal", "contract"])
@pytest.mark.parametrize("concurrent", ["pause", "replacement"])
def test_public_control_cas_conflict_does_not_retry_over_user_state(clock_store, monkeypatch, change, concurrent):
    clock, db = clock_store
    manager = goals.GoalManager("task")
    manager.set("Old goal")
    cas = db.compare_and_swap_meta
    calls = []
    def raced(key, expected, new):
        calls.append(key)
        if concurrent == "pause":
            state = goals.GoalState.from_json(expected)
            state.status, state.paused_reason = "paused", "user-paused"
        else:
            state = goals.GoalState("Explicit replacement", created_at=1010.)
        db.set_meta(key, state.to_json())
        return cas(key, expected, new)
    monkeypatch.setattr(db, "compare_and_swap_meta", raced)
    with pytest.raises(goals._StaleGoalEvaluation):
        if change == "subgoal":
            manager.add_subgoal("Stale criterion")
        else:
            manager.set_contract(goals.GoalContract(verification="Stale verification"))
    assert len(calls) == 1
    state = goals.load_goal("task")
    assert state.subgoals == [] and state.contract.is_empty()
    if concurrent == "pause":
        assert state.status == "paused" and state.paused_reason == "user-paused"
    else:
        assert state.goal == "Explicit replacement"


def test_runtime_pause_and_resume_copy_use_korean_catalog(clock_store, monkeypatch):
    clock, db = clock_store
    from agent.i18n import t
    from hermes_cli.goal_command import dispatch_goal_command
    monkeypatch.setattr("agent.i18n._resolve_language", lambda *args: "ko")
    manager = goals.GoalManager("task")
    manager.set("Synthetic")
    clock.now += 100
    decision = manager.evaluate_after_turn("Partial")
    assert "전체 실행 시간 한도" in decision["message"]
    result = dispatch_goal_command(manager, "resume", authorize_gate=lambda: None,
                                   render=lambda key, default, **kw: t(key, **kw))
    assert result.error and result.prompt is None
    assert "일시정지 상태를 유지" in result.output
