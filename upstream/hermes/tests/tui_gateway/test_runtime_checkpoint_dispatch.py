"""A durable incomplete checkpoint reaches native goal admission and dispatch, offline."""
import contextlib
import logging
import threading
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from hermes_cli import goals, goals_runtime as runtime
from hermes_cli.goals_turn_context import goal_turn_result
from hermes_state import SessionDB
from tui_gateway import prompt_turn, session_auto_continue


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clock = NS(now=1000.)
    fake_time = NS(time=lambda: clock.now)
    monkeypatch.setattr(goals, "time", fake_time)
    monkeypatch.setattr(runtime, "time", fake_time)
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: 86400.)
    db = SessionDB(tmp_path / "state.db")
    db.create_session("checkpoint-task", "test")
    monkeypatch.setattr(goals, "_get_session_db", lambda: db)
    forbidden = Mock(side_effect=AssertionError("A milestone decision must not call another model"))
    monkeypatch.setattr(goals, "judge_goal", forbidden)
    monkeypatch.setattr("hermes_cli.goals_advisory.goal_advisory_result", forbidden)
    manager = goals.GoalManager("checkpoint-task")
    manager.set("Investigate synthetic evidence and save a reviewed report", max_turns=32)
    session = {"session_key": manager.session_id, "profile_home": str(tmp_path),
               "history_lock": threading.RLock(), "running": False,
               "agent": NS(session_id=manager.session_id)}
    server = NS(Path=Path, contextlib=contextlib, logger=logging.getLogger(__name__),
                _hermes_home=tmp_path, _load_cfg=lambda: {}, _emit=Mock())
    session_auto_continue.register(server)
    prompt_turn.register(server)
    server._GOAL_COMPRESSION_RECOVERY_ATTEMPTS = "_goal_compression_recovery_attempts"
    server._hook_failure = lambda what, exc: (_ for _ in ()).throw(exc)
    server._drain_queued_prompt = Mock(return_value=False)
    server._session_profile_runtime_scope = lambda owner: contextlib.nullcontext()
    server._poll_bot_live_delivery_once = Mock(return_value=False)
    server._session_turn_admission = lambda owner: contextlib.nullcontext(True)
    server._notif_handle_ready = Mock()
    server._run_prompt_submit = Mock(return_value=True)  # admission boundary; never run a model
    from tools.process_registry import process_registry
    monkeypatch.setattr(process_registry, "drain_notifications", lambda **kwargs: [])
    yield NS(server=server, session=session, manager=manager, db=db, clock=clock, forbidden=forbidden)
    forbidden.assert_not_called()
    db.close()


def checkpoint(gateway, decision):
    rt = gateway
    rt.clock.now += 12.4 * 3600
    identity = runtime.runtime_identity(rt.manager.state)
    metadata = {"runtime_milestone": {"goal_identity": list(identity), "threshold_seconds": 43200,
                                      "decision": decision, "reason": "Useful bounded next work remains."}}
    text = "12시간 중간 점검: 관측과 한계를 정리했습니다. 보고서 완료를 주장하지 않습니다."
    rt.db.append_message(rt.manager.session_id, "assistant", text,
                         message_uid="actual-checkpoint-source", display_metadata=metadata)
    row = rt.db.get_messages(rt.manager.session_id)[-1]
    assert runtime.commit_runtime_milestone(rt.db, rt.manager.session_id,
        expected_goal_id=identity, row=row) == decision
    # The native result is incomplete even though its persisted decision authorizes
    # further native work. Exercise the delivery projection that preserves its UID.
    result = goal_turn_result({"final_response": text, "completed": False, "failed": False,
        "interrupted": False, "turn_exit_reason": "runtime_checkpoint_decided",
        "runtime_checkpoint_source_message_id": row["message_uid"]})
    return result


def followup(gateway, result):
    rt = gateway
    raw, status, _ = rt.server._turn_outcome(result)
    assert status == "complete" and result["completed"] is False
    prompt = rt.server._goal_followup_after_turn("ui", rt.session, result, status, raw,
        expected_goal_identity=rt.server._turn_goal_identity(rt.session))
    rt.server._run_post_turn_followups("request", "ui", rt.session, result, prompt)
    return prompt


@pytest.mark.parametrize("decision", ["finish", "continue"])
def test_checkpoint_reaches_real_goal_manager_and_native_followup_dispatch(gateway, decision):
    rt = gateway
    result = checkpoint(rt, decision)
    remaining = runtime.runtime_remaining(goals.load_goal(rt.manager.session_id))
    prompt = followup(rt, result)
    assert prompt and "Investigate synthetic evidence" in prompt
    rt.server._run_prompt_submit.assert_called_once_with(
        "request", "ui", rt.session, prompt, respect_goal_pause=True, display_kind="hidden")
    saved = goals.load_goal(rt.manager.session_id)
    assert saved.status == "active" and saved.turns_used == 1 and saved.max_turns == 32
    assert saved.last_verdict == ("closing" if decision == "finish" else "continue")
    assert runtime.runtime_phase(saved) == ("closing" if decision == "finish" else "investigation")
    assert runtime.runtime_remaining(saved) == remaining
    assert saved.runtime_checkpoints[0]["source_message_id"] == "actual-checkpoint-source"
    assert rt.session["running"] and result["completed"] is False


@pytest.mark.parametrize("reason", ["runtime_budget_checkpoint_failed", "runtime_budget_handoff",
                                    "runtime_budget_handoff_superseded", "unknown", "text_response(stop)"])
def test_other_incomplete_outcomes_do_not_enter_goal_or_dispatch(gateway, reason):
    rt = gateway
    result = checkpoint(rt, "finish")
    result["turn_exit_reason"] = reason
    before = rt.db.get_meta("goal:" + rt.manager.session_id)
    assert followup(rt, result) is None
    assert rt.db.get_meta("goal:" + rt.manager.session_id) == before
    rt.server._run_prompt_submit.assert_not_called()


@pytest.mark.parametrize("source", [None, "different-source"])
def test_checkpoint_source_mismatch_reaches_native_rejection_without_dispatch(gateway, source):
    rt = gateway
    result = checkpoint(rt, "continue")
    result["runtime_checkpoint_source_message_id"] = source
    assert followup(rt, result) is None
    saved = goals.load_goal(rt.manager.session_id)
    assert saved.status == "paused" and saved.paused_reason == "runtime checkpoint receipt unavailable"
    rt.server._run_prompt_submit.assert_not_called()


@pytest.mark.parametrize("pending", [False, True])
def test_manual_pause_after_checkpoint_remains_authoritative(gateway, pending):
    rt = gateway
    result = checkpoint(rt, "finish")
    rt.manager.request_pause() if pending else rt.manager.pause(reason="explicit human stop")
    assert followup(rt, result) is None
    saved = goals.load_goal(rt.manager.session_id)
    assert saved.status == "paused" and saved.turns_used == 0
    assert saved.paused_reason == ("user-safe-paused" if pending else "explicit human stop")
    rt.server._run_prompt_submit.assert_not_called()


def test_manual_pause_between_goal_evaluation_and_dispatch_blocks_followup(gateway):
    rt = gateway
    result = checkpoint(rt, "finish")
    prompt = rt.server._goal_followup_after_turn("ui", rt.session, result, "complete", result["final_response"],
        expected_goal_identity=rt.server._turn_goal_identity(rt.session))
    assert prompt
    rt.manager.pause(reason="human pause before dispatch")
    rt.server._run_post_turn_followups("request", "ui", rt.session, result, prompt)
    rt.server._run_prompt_submit.assert_not_called()
    assert goals.load_goal(rt.manager.session_id).paused_reason == "human pause before dispatch"


@pytest.mark.parametrize("override", [{"failed": True}, {"interrupted": True},
                                     {"guardrail": {"action": "halt"}}])
def test_decision_stop_reason_cannot_override_failure_interrupt_or_guardrail(gateway, override):
    rt = gateway
    result = checkpoint(rt, "continue")
    result.update(override)
    assert not rt.server._is_successful_goal_turn(result, "complete", result["final_response"])
    rt.server._run_prompt_submit.assert_not_called()


def test_goal_cas_replacement_during_checkpoint_evaluation_wins(gateway, monkeypatch):
    rt = gateway
    result = checkpoint(rt, "finish")
    cas = rt.db.compare_and_swap_meta
    replacement = goals.GoalState("New explicit scope", created_at=rt.clock.now)
    def replace(key, expected, new):
        rt.db.set_meta(key, replacement.to_json())
        return cas(key, expected, new)
    monkeypatch.setattr(rt.db, "compare_and_swap_meta", replace)
    assert followup(rt, result) is None
    assert goals.load_goal(rt.manager.session_id).goal == replacement.goal
    rt.server._run_prompt_submit.assert_not_called()


@pytest.mark.parametrize("change", ["replacement", "subgoal", "missing_identity"])
def test_checkpoint_never_controls_a_different_or_unidentified_scope(gateway, change):
    rt = gateway
    result = checkpoint(rt, "finish")
    identity = rt.server._turn_goal_identity(rt.session)
    if change == "replacement":
        rt.manager.set("New explicit human scope")
    elif change == "subgoal":
        rt.manager.add_subgoal("A new required scope criterion")
    else:
        identity = None
    before = rt.db.get_meta("goal:" + rt.manager.session_id)
    prompt = rt.server._goal_followup_after_turn("ui", rt.session, result, "complete", result["final_response"],
        expected_goal_identity=identity)
    assert prompt is None
    rt.server._run_post_turn_followups("request", "ui", rt.session, result, prompt)
    assert rt.db.get_meta("goal:" + rt.manager.session_id) == before
    rt.server._run_prompt_submit.assert_not_called()
