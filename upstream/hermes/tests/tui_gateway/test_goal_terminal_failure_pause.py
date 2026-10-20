"""A failed turn must leave a durable, exact-goal recovery reason without a judge."""
import contextlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hermes_cli import goals
from hermes_state import SessionDB
from tui_gateway import prompt_turn


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(tmp_path / "state.db")
    monkeypatch.setattr(goals, "_get_session_db", lambda: db)
    judge = Mock(side_effect=AssertionError("Failed turns must not call the goal judge"))
    monkeypatch.setattr(goals, "judge_goal", judge)
    server = SimpleNamespace(_load_cfg=lambda: {}, _emit=Mock())
    prompt_turn.register(server)
    server._hook_failure = lambda what, exc: (_ for _ in ()).throw(exc)
    yield server, judge, db
    db.close()


def test_terminal_transport_failure_is_durably_paused(runtime):
    server, judge, db = runtime
    manager = goals.GoalManager("synthetic-failed-turn")
    manager.set("Investigate synthetic evidence")
    result = {"failed": True, "completed": False, "failure_reason": "server_error",
              "error": "HTTP 502: upstream unavailable"}
    session = {"session_key": manager.session_id}
    identity = server._turn_goal_identity(session)
    assert server._goal_followup_after_turn(
        "runtime", session, result, "error", "Failure", expected_goal_identity=identity) is None
    saved = goals.load_goal(manager.session_id)
    assert saved.status == "paused"
    assert saved.turns_used == 0
    assert "server_error" in saved.paused_reason
    # Discard the runtime, as orphan cleanup does; recovery comes from native storage.
    session.clear()
    restored = goals.GoalManager(manager.session_id)
    assert restored.state.paused_reason == saved.paused_reason
    assert restored.next_continuation_prompt() is None
    assert "HTTP" not in saved.paused_reason
    judge.assert_not_called()


@pytest.mark.parametrize("change", ["paused", "replaced", "cleared", "absent"])
def test_failure_preserves_current_goal_control(runtime, change):
    server, judge, db = runtime
    manager = goals.GoalManager("synthetic-controlled")
    manager.set("Original scope")
    session = {"session_key": manager.session_id}
    identity = server._turn_goal_identity(session)
    if change == "paused":
        manager.pause(reason="user stop")
    elif change == "replaced":
        manager.set("New requested scope")
    elif change == "cleared":
        manager.clear()
    else:
        session["session_key"] = "synthetic-no-goal"
    key = session["session_key"]
    before = db.get_meta("goal:" + key)
    server._pause_failed_turn_goal(session, identity, RuntimeError("private exception detail"))
    assert db.get_meta("goal:" + key) == before
    judge.assert_not_called()


def test_concurrent_goal_replacement_wins_native_cas(runtime, monkeypatch):
    server, judge, db = runtime
    manager = goals.GoalManager("synthetic-concurrent")
    manager.set("Original scope")
    session = {"session_key": manager.session_id}
    identity = server._turn_goal_identity(session)
    replacement = goals.GoalManager("synthetic-replacement-definition")
    replacement.set("New requested scope")
    replacement_raw = replacement.state.to_json()
    original_save = goals.GoalManager._save

    def replace_before_cas(current):
        db.set_meta("goal:" + manager.session_id, replacement_raw)
        return original_save(current)

    monkeypatch.setattr(goals.GoalManager, "_save", replace_before_cas)
    server._pause_failed_turn_goal(session, identity, RuntimeError("Synthetic failure"))
    assert db.get_meta("goal:" + manager.session_id) == replacement_raw
    judge.assert_not_called()


def test_caught_exception_also_persists_failure_pause(runtime, tmp_path):
    server, judge, db = runtime
    manager = goals.GoalManager("synthetic-dispatcher-error")
    manager.set("Synthetic investigation")
    session = {"session_key": manager.session_id}
    st = server._TurnRun(SimpleNamespace(), None, None, True)
    st.goal_identity = server._turn_goal_identity(session)
    server.contextlib = contextlib
    server.os = SimpleNamespace(makedirs=Mock(), path=__import__("os").path)
    server.time = __import__("time")
    server.sys = __import__("sys")
    server._CRASH_LOG = str(tmp_path / "synthetic-crash.log")
    server._restore_agent_history_after_turn_error = Mock()
    server._emit_terminal_turn_error = Mock()
    server._turn_failure_detail = Mock(return_value="synthetic failure")
    server._recover_turn_exception("runtime", session, st, RuntimeError("private detail"))
    saved = goals.load_goal(manager.session_id)
    assert saved.status == "paused"
    assert saved.turns_used == 0
    assert "private detail" not in saved.paused_reason
    assert st.error_retained
    server._emit_terminal_turn_error.assert_called_once()
    judge.assert_not_called()


@pytest.mark.parametrize("status", ["complete", "interrupted"])
def test_non_error_turns_do_not_take_failure_pause_path(runtime, status):
    server, judge, db = runtime
    manager = goals.GoalManager("synthetic-non-error")
    manager.set("Synthetic investigation")
    session = {"session_key": manager.session_id}
    identity = server._turn_goal_identity(session)
    # A completed=False handoff or user interrupt is not a terminal provider failure.
    result = {"completed": False, "interrupted": status == "interrupted"}
    assert server._goal_followup_after_turn(
        "runtime", session, result, status, "Stopped", expected_goal_identity=identity) is None
    assert goals.load_goal(manager.session_id).status == "active"
    judge.assert_not_called()


def test_turn_preparation_captures_goal_before_dispatcher_work(runtime, monkeypatch):
    from tools import approval_context

    server, judge, db = runtime
    manager = goals.GoalManager("synthetic-turn-preparation")
    manager.set("Synthetic investigation")
    session = {"session_key": manager.session_id}
    st = server._TurnRun(SimpleNamespace(), None, None, True)
    monkeypatch.setattr(approval_context, "set_current_session_key", Mock())
    monkeypatch.setattr(approval_context, "set_prompts_wait_for_answer", Mock())
    server._set_session_context = Mock(return_value=())
    server._profile_runtime_scope_tokens = Mock(return_value=None)
    server._wire_callbacks = Mock(side_effect=RuntimeError("synthetic preparation failure"))
    with pytest.raises(RuntimeError, match="synthetic preparation failure"):
        server._prepare_turn_input("runtime", session, st, "Synthetic request", [])
    assert st.goal_identity == goals.goal_identity(manager.state)
    judge.assert_not_called()


def test_compression_exhaustion_keeps_native_recovery_policy(runtime):
    server, judge, db = runtime
    manager = goals.GoalManager("synthetic-compression")
    manager.set("Synthetic investigation")
    session = {"session_key": manager.session_id}
    identity = server._turn_goal_identity(session)
    server._plan_goal_compression_recovery = Mock(return_value=("Native recovery prompt", None))
    result = {"failed": True, "compression_exhausted": True}
    assert server._goal_followup_after_turn(
        "runtime", session, result, "error", "Compression exhausted",
        expected_goal_identity=identity) == "Native recovery prompt"
    assert goals.load_goal(manager.session_id).status == "active"
    judge.assert_not_called()
