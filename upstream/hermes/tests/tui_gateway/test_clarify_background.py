"""Background clarify releases the agent, retaining native answers and pause controls."""
from contextlib import nullcontext
import json
import threading
from unittest.mock import Mock

import pytest

from hermes_cli import goals, plugins
from hermes_state import SessionDB
from tools.clarify_gateway import resolve_clarify_timeout
from tools.clarify_tool import clarify_tool
from tui_gateway import server, server_requests as requests


class Deadline:
    def __init__(self, interval, function, args=()):
        self.interval, self.function, self.args = interval, function, args
        self.cancelled = False
        self.daemon = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        # Also simulate an already-running timer racing after cancellation.
        self.function(*self.args)


@pytest.fixture
def transport(monkeypatch):
    requests.reset_for_tests()
    frames, events, timers = [], [], []
    monkeypatch.setattr(requests, "_write", frames.append)
    monkeypatch.setattr(requests, "_emit", lambda *args: events.append(args))
    monkeypatch.setattr(requests, "_answerable", lambda sid: True)
    def timer(*args, **kwargs):
        deadline = Deadline(*args, **kwargs)
        timers.append(deadline)
        return deadline
    monkeypatch.setattr(requests.threading, "Timer", timer)
    yield frames, events, timers
    requests.reset_for_tests()


@pytest.mark.parametrize("winner", ["timeout", "answer", "cancel", "error"])
def test_native_async_batch_settles_once_and_retains_only_real_partial_answers(transport, winner):
    frames, events, timers = transport
    results = []
    handle = requests.send_async("clarify", "ui", {"questions": [
        {"qid": "q0", "question": "Known?"}, {"qid": "q1", "question": "Unknown?"}]},
        results.append, qids=["q0", "q1"], timeout=3600, background=True)
    rid = handle.request_id
    assert rid == frames[0]["id"]
    assert requests.pending_kind("ui") == ""
    assert requests.lock_answer(rid, "q0", "explicit answer") == ["q1"]
    assert requests.open_requests("ui")[0]["params"]["answers"] == {"q0": "explicit answer"}
    assert results == []  # asking returned before any answer or elapsed deadline
    if winner == "timeout":
        timers[0].fire()
    elif winner == "answer":
        assert requests.lock_answer(rid, "q1", None) == []
    elif winner == "cancel":
        assert requests.cancel("ui") == 1
    else:
        assert requests.resolve_response({"id": rid, "error": {"code": -32601}})
    timers[0].fire()
    handle("timeout")
    assert not requests.resolve_response({"id": rid, "result": {"answers": {"q1": "late"}}})
    assert len(results) == 1
    assert results[0]["outcome"] == {"timeout": "timed_out", "answer": "submitted",
                                      "cancel": "cancelled", "error": "undelivered"}[winner]
    expected = {"q0": "explicit answer", **({"q1": None} if winner == "answer" else {})}
    assert results[0]["answers"] == expected
    assert results[0]["server_request_id"] == rid
    assert timers[0].cancelled
    assert requests.open_requests("ui") == []
    assert sum(event[0] == "request.cancel" for event in events) == (winner in {"timeout", "cancel"})
    assert resolve_clarify_timeout({}) == 3600
    assert resolve_clarify_timeout({}, surface="ui") == 0


@pytest.mark.parametrize("outcome", ["timed_out", "submitted", "cancelled"])
@pytest.mark.parametrize("control", ["paused", "done"])
def test_background_tool_persists_native_settlement_without_resuming_manual_pause(
        transport, tmp_path, monkeypatch, outcome, control):
    frames, _, timers = transport
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(tmp_path / "state.db")
    db.create_session("native", source="test")
    monkeypatch.setattr(goals, "_get_session_db", lambda: db)
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: None)
    manager = goals.GoalManager("native")
    manager.set("Inspect synthetic evidence")
    session = {"session_key": "native", "profile_home": str(tmp_path),
               "history": [], "history_lock": threading.RLock(), "running": True}
    monkeypatch.setitem(server._sessions, "ui", session)
    monkeypatch.setattr(server, "_load_cfg", lambda: {"clarify": {"background": True, "ui_timeout": 3600}})
    monkeypatch.setattr(server, "_session_db", lambda _: nullcontext(db))
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _: True)
    monkeypatch.setattr(server, "_persist_branch_seed", lambda _: None)
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda _: False)
    start = Mock(side_effect=AssertionError("A manual pause must never start work"))
    monkeypatch.setattr(server, "_start_session_work", start)
    hooks = Mock()
    monkeypatch.setattr(plugins, "invoke_hook", hooks)
    try:
        result = json.loads(clarify_tool([{"question": "Known?"}, {"question": "Unknown?"}],
            callback=lambda questions: server._clarify_block("ui", questions)))
        assert result["outcome"] == "pending"
        rid = result["server_request_id"]
        assert rid == frames[0]["id"]
        assert all(item["user_response"] is None for item in result["responses"])
        assert "queued_prompt" not in session
        assert requests.lock_answer(rid, "q0", "actual user answer") == ["q1"]
        if control == "paused":
            manager.pause(reason="explicit user pause")
        else:
            manager.mark_done(reason="The independent work finished before the answer deadline")
        session["running"] = False
        if outcome == "timed_out":
            timers[0].fire()
        elif outcome == "submitted":
            requests.lock_answer(rid, "q1", "actual second answer")
        else:
            requests.cancel("ui")
        start.assert_not_called()
        assert goals.load_goal("native").status == control
        hooks.assert_called_once()
        assert hooks.call_args.args == ("clarification_resolved",)
        settled = hooks.call_args.kwargs["result"]
        assert hooks.call_args.kwargs["session_id"] == "native"
        assert settled["outcome"] == outcome
        assert settled["responses"][0]["user_response"] == "actual user answer"
        if outcome == "cancelled":
            assert not session.get("queued_prompt")
            return
        if outcome == "timed_out":
            assert settled["responses"][1]["status"] == "unanswered"
            assert settled["responses"][1]["user_response"] is None
            assert "unknown" in settled["notice"] and "no permission" in settled["notice"]
        envelope = session["queued_prompt"]
        assert envelope["_queued_display_kind"] == "clarification_result"
        assert json.loads(envelope["text"].split("\n", 1)[1]) == settled
        rows = db.get_messages("native")
        assert len(rows) == 1 and rows[0]["content"] == envelope["text"]
        assert not server._drain_queued_prompt("test", "ui", session)
        assert session["queued_prompt"] is envelope
        # Later user input has its own envelope, with no metadata/text merge.
        server._enqueue_prompt(session, "actual subsequent user message", None)
        assert len(session["queued_prompts"]) == 1
        assert session["queued_prompt"]["text"] == envelope["text"]
        if control == "paused":
            assert not server._drain_queued_prompt("test", "ui", session)
            assert session["queued_prompt"] is envelope
        else:
            from tui_gateway.clarify_requests import take_background_context
            received = []
            def run(rid, sid, current, text, **kwargs):
                # This is the real native queue dispatch boundary; no model is called.
                received.append((text, take_background_context(server, current)))
                return True
            monkeypatch.setattr(server, "_run_prompt_submit", run)
            assert server._drain_queued_prompt("explicit", "ui", session)
            assert received == [("actual subsequent user message", envelope["text"])]
            assert not session.get("queued_prompt")
            assert take_background_context(server, session) == ""
    finally:
        db.close()
