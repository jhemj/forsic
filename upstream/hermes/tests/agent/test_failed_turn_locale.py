"""Offline localized failure boundaries preserve native persistence and retry semantics."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.i18n import reset_language_cache, t
from agent.turn_failure_copy import (
    FAILED_TURN_NOTICE, PARTIAL_FAILED_TURN_NOTICE, failed_turn_notice,
    untyped_failed_turn_display_kind,
)


@pytest.mark.parametrize("tool_tail", [[], [{"role": "tool", "content": "effect"}],
    [{"role": "assistant", "tool_calls": [{"id": "call", "type": "function",
        "function": {"name": "fixture", "arguments": "{}"}}]}]])
def test_korean_boundary_preserves_failure_and_retry_state(tmp_path, monkeypatch, tool_tail):
    from agent.conversation_loop import _close_durable_failed_turn
    from hermes_state import SessionDB

    monkeypatch.delenv("HERMES_LANGUAGE", raising=False)
    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text("display:\n  language: ko\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    reset_language_cache()
    db = SessionDB(home / "state.db")
    try:
        db.create_session("fixture", "cli")
        db.append_message("fixture", "user", "fixture request")
        messages = [{"role": "user", "content": "fixture request"}, *deepcopy(tool_tail)]
        result = {"messages": messages, "completed": False, "failed": True,
            "failure_reason": "server_error", "failure_retryable": True,
            "error": "synthetic provider offline", "current_turn_user_idx": 0,
            "final_response": "synthetic provider detail"}
        original = deepcopy(result)
        agent = SimpleNamespace(_session_db=db, session_id="fixture", provider="openai", model="fixture")
        agent._flush_messages_to_session_db = lambda rows: db.append_messages_batch(agent.session_id, rows[-1:])
        _close_durable_failed_turn(agent, result)
        row = db.get_messages("fixture")[-1]
        assert row["content"] == ("이번 요청을 완료하지 못했습니다. 일부 작업은 이미 실행되었을 수 있으니, 다시 보내기 전에 실행 결과를 확인해 주세요."
            if tool_tail else "요청이 처리되지 않았습니다. 계속 진행하려면 요청을 다시 보내 주세요.")
        assert row["display_kind"] == "failed_turn"
        assert row["display_metadata"]["error"] == original["error"]
        assert row["display_metadata"]["error_surface"]["code"] == "server_error"
        assert not any(value for key, value in row.items() if key.startswith("reasoning"))
        assert {k: v for k, v in result.items() if k != "messages"} == {k: v for k, v in original.items() if k != "messages"}
        assert messages[:-1] == original["messages"]
        _close_durable_failed_turn(agent, result)
        assert len(db.get_messages("fixture")) == 2
        # Context-pressure rollback/rotation owns its tail; localization must not close it.
        for flag in ("compression_exhausted", "compression_deferred", "context_overflow"):
            db.create_session(flag, "cli")
            db.append_message(flag, "user", "oversized")
            agent.session_id = flag
            excluded = {"messages": [{"role": "user", "content": "oversized"}], "completed": False}
            excluded.update({"failure_reason": flag} if flag == "context_overflow" else {flag: True})
            before = deepcopy(excluded)
            _close_durable_failed_turn(agent, excluded)
            assert excluded == before
            assert db.latest_conversation_role(flag) == "user"
    finally:
        db.close()
        reset_language_cache()


def test_gateway_runtime_locale_and_legacy_history_remain_distinct(tmp_path, monkeypatch):
    from gateway.run_turn import GatewayTurnMixin

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reset_language_cache()
    gateway = GatewayTurnMixin()
    gateway.async_session_store = SimpleNamespace(
        transcript_tail_role=AsyncMock(side_effect=["user", "assistant"]),
        append_to_transcript=AsyncMock(),
    )
    try:
        monkeypatch.setenv("HERMES_LANGUAGE", "en")
        assert failed_turn_notice([]) == FAILED_TURN_NOTICE
        assert failed_turn_notice([{"role": "tool"}]) == PARTIAL_FAILED_TURN_NOTICE
        monkeypatch.setenv("HERMES_LANGUAGE", "ko")
        result = {"messages": [{"role": "user", "content": "fixture"},
            {"role": "tool", "content": "effect"}], "history_offset": 0}
        notice = gateway._hmwa_failed_turn_notice(result)
        assert notice == t("agent.failed_turn.partial")
        assert "일부 작업" in notice
        assert gateway._hmwa_add_failed_turn_notice("provider guidance", notice) == f"provider guidance\n\n{notice}"
        asyncio.run(gateway._hmwa_close_failed_turn("fixture", notice))
        asyncio.run(gateway._hmwa_close_failed_turn("fixture", notice))
        gateway.async_session_store.append_to_transcript.assert_awaited_once()
        row = gateway.async_session_store.append_to_transcript.call_args.args[1]
        assert row["display_kind"] == "failed_turn" and row["content"] == notice
        for old in (FAILED_TURN_NOTICE, PARTIAL_FAILED_TURN_NOTICE):
            assert untyped_failed_turn_display_kind("assistant", old) == "failed_turn"
            assert untyped_failed_turn_display_kind("user", old) is None
            assert untyped_failed_turn_display_kind("assistant", "quoted: " + old) is None
    finally:
        reset_language_cache()
