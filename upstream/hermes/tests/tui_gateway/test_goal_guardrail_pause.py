"""A native halt must durably pause the exact active goal without calling its judge."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.tool_guardrails import ToolGuardrailDecision
from hermes_cli import goals
from hermes_state import SessionDB
from tui_gateway import prompt_turn


@pytest.fixture
def goal_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(tmp_path / "state.db")
    monkeypatch.setattr(goals, "_get_session_db", lambda: db)
    judge = Mock(side_effect=AssertionError("Halt must not call a model judge"))
    monkeypatch.setattr(goals, "judge_goal", judge)
    server = SimpleNamespace(_load_cfg=lambda: {}, _emit=Mock())
    prompt_turn.register(server)
    # Unexpected hook errors should fail the test rather than be logged and hidden.
    server._hook_failure = lambda what, exc: (_ for _ in ()).throw(exc)
    yield server, judge
    db.close()


@pytest.mark.parametrize("action", ["block", "halt"])
def test_guardrail_halt_persists_only_exact_active_goal(goal_runtime, action):
    server, judge = goal_runtime
    target = goals.GoalManager("synthetic-active")
    target.set("Investigate the supplied synthetic evidence")
    other = goals.GoalManager("synthetic-other")
    other.set("Independent request")
    result = {"completed": False, "turn_exit_reason": "guardrail_halt",
              "guardrail": ToolGuardrailDecision(action=action, code="same_tool_failure_halt").to_metadata()}
    for _ in range(2):
        assert server._goal_followup_after_turn("runtime", {"session_key": target.session_id}, result, "complete", "Stopped") is None
    saved = goals.load_goal(target.session_id)
    assert saved.status == "paused"
    assert "same_tool_failure_halt" in saved.paused_reason
    assert saved.turns_used == 0
    assert goals.load_goal(other.session_id).status == "active"
    assert goals.GoalManager(target.session_id).next_continuation_prompt() is None
    judge.assert_not_called()


def test_guardrail_does_not_replace_user_pause_or_create_absent_goal(goal_runtime):
    server, judge = goal_runtime
    target = goals.GoalManager("synthetic-user-stop")
    target.set("Investigate")
    target.pause(reason="user")
    result = {"completed": False, "guardrail": ToolGuardrailDecision(action="halt", code="exact_failure_halt").to_metadata()}
    for key in (target.session_id, "synthetic-no-goal"):
        assert server._goal_followup_after_turn("runtime", {"session_key": key}, result, "complete", "Stopped") is None
    assert goals.load_goal(target.session_id).paused_reason == "user"
    assert goals.load_goal("synthetic-no-goal") is None
    judge.assert_not_called()
