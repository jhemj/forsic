"""Native halt notices describe the actual counter and preserve mechanical codes."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.i18n import reset_language_cache, t
from agent.status_output import tool_guardrail_halt_reason
from agent.tool_guardrails import ToolGuardrailDecision
from agent import turn_tool_round
from tui_gateway import prompt_turn


@pytest.fixture(autouse=True)
def isolated_locale(monkeypatch):
    monkeypatch.setenv("HERMES_LANGUAGE", "ko")
    reset_language_cache()
    yield
    reset_language_cache()


@pytest.mark.parametrize("code, expected", [
    ("same_tool_failure_halt", "오류가 누적 3회"),
    ("repeated_exact_failure_block", "동일한 인수"),
    ("idempotent_no_progress_block", "같은 결과를 3회"),
    ("identical_call_streak_halt", "연속 3회"),
    ("identical_cycle_halt", "호출 주기가 3회"),
])
def test_counter_semantics(code, expected):
    decision = ToolGuardrailDecision(action="halt", code=code, tool_name="synthetic_tool", count=3)
    reason = tool_guardrail_halt_reason(decision)
    assert expected in reason
    assert "synthetic_tool" in reason
    if code == "same_tool_failure_halt":
        assert "진전" not in reason and "연속" not in reason
    final = t("display.notice.tool_guardrail_halt_detail", reason=reason)
    assert "조사 완료를 뜻하지 않습니다" in final
    assert decision.to_metadata()["code"] == code


def test_actual_tool_round_emits_localized_status_and_preserves_halt(monkeypatch):
    from run_agent import AIAgent
    from hermes_cli.observability import shared_metrics_harness

    decision = ToolGuardrailDecision(action="halt", code="same_tool_failure_halt", tool_name="synthetic_tool", count=3)
    agent = SimpleNamespace(
        quiet_mode=True, verbose_logging=False, stream_delta_callback=Mock(),
        _tool_guardrail_halt_decision=decision,
        _deduplicate_tool_calls=lambda calls: calls, _cap_delegate_task_calls=lambda calls: calls,
        _flush_messages_to_session_db=Mock(return_value=True),
        _emit_interim_assistant_message=Mock(), _execute_tool_calls=Mock(),
        _emit_diagnostic_status=Mock(), _safe_print=Mock(),
    )
    agent._toolguard_controlled_halt_response = lambda value: AIAgent._toolguard_controlled_halt_response(agent, value)
    monkeypatch.setattr(turn_tool_round, "validate_tool_calls", lambda *a, **kw: SimpleNamespace(action="allow", mixed_invalid_batch=False))
    monkeypatch.setattr(turn_tool_round, "stage_tool_call_message", lambda *a, **kw: ({"role": "assistant", "tool_calls": []}, False))
    monkeypatch.setattr(shared_metrics_harness, "finish_tool_round", lambda agent: None)
    verdict = turn_tool_round.run_tool_round(
        agent, assistant_message=SimpleNamespace(tool_calls=[]), finish_reason="tool_calls",
        messages=[], conversation_history=[], api_call_count=1, effective_task_id="synthetic",
        user_message="synthetic", system_message="", active_system_prompt="",
        compression_attempts=0, max_compression_attempts=1, final_response=None,
        failed=False, _turn_exit_reason="", truncated_tool_call_retries=0, current_turn_user_idx=0,
    )
    status = agent._emit_diagnostic_status.call_args.args[0]
    assert "도구 보호 장치" in status and "오류가 누적 3회" in status
    assert "synthetic_tool" in status and decision.code in status
    assert verdict.action == "break" and verdict._turn_exit_reason == "guardrail_halt"
    assert "오류가 누적 3회" in verdict.final_response
    assert "진전 없이" not in verdict.final_response


def test_goal_pause_event_is_korean_but_persisted_reason_is_canonical(monkeypatch):
    manager = SimpleNamespace(pause=Mock())
    server = SimpleNamespace(_load_cfg=lambda: {}, _emit=Mock())
    prompt_turn.register(server)
    # Registered functions use the host namespace; no database or judge is involved.
    server._active_goal_manager = lambda session: manager
    server._hook_failure = lambda what, exc: (_ for _ in ()).throw(exc)
    code = "same_tool_failure_halt"
    result = {"guardrail": {"action": "halt", "code": code}}
    assert server._goal_followup_after_turn("synthetic", {"session_key": "synthetic"}, result, "complete", "") is None
    manager.pause.assert_called_once_with(reason="tool guardrail: " + code)
    event, sid, payload = server._emit.call_args.args
    assert (event, sid, payload["kind"]) == ("status.update", "synthetic", "goal")
    assert "목표가 자동으로 일시 중지" in payload["text"] and code in payload["text"]


def test_english_default_preserves_tool_and_counter(monkeypatch):
    monkeypatch.delenv("HERMES_LANGUAGE")
    reset_language_cache()
    reason = tool_guardrail_halt_reason(ToolGuardrailDecision(code="same_tool_failure_halt", tool_name="synthetic_tool", count=3))
    assert reason == "synthetic_tool reached 3 accumulated failures in this turn."
