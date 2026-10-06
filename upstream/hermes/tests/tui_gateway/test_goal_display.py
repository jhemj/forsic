"""Goal automation stays model-visible without impersonating a human bubble."""
from contextlib import nullcontext
import threading
from unittest.mock import Mock

from tui_gateway import server


def test_goal_followup_uses_hidden_projection_without_changing_prompt(monkeypatch):
    session = {"running": False, "history_lock": threading.RLock()}
    prompt = "[Continuing toward your standing goal]\nGoal: investigate\n\ncontinue"
    dispatch = Mock()
    monkeypatch.setattr(server, "_drain_queued_prompt", lambda *args: False)
    monkeypatch.setattr(server, "_session_turn_admission", lambda *args: nullcontext(True))
    monkeypatch.setattr(server, "_dispatch_followup_turn", dispatch)
    monkeypatch.setattr(server, "_session_profile_runtime_scope", lambda *args: nullcontext())
    monkeypatch.setattr(server, "_notif_handle_ready", lambda *args, **kwargs: None)
    from tools.process_registry import process_registry
    monkeypatch.setattr(process_registry, "drain_notifications", lambda **kwargs: [])
    server._run_post_turn_followups("r", "s", session, {}, prompt)
    dispatch.assert_called_once_with("r", "s", session, prompt, "goal continuation dispatch", display_kind="hidden")
    assert session["running"] is True


def test_dispatch_preserves_prompt_and_passes_display_only_hint(monkeypatch):
    submit = Mock()
    emit = Mock()
    monkeypatch.setattr(server, "_run_prompt_submit", submit)
    monkeypatch.setattr(server, "_emit", emit)
    session = {"running": True}
    server._dispatch_followup_turn("r", "s", session, "exact goal body", "goal", display_kind="hidden")
    submit.assert_called_once_with("r", "s", session, "exact goal body", display_kind="hidden")
    emit.assert_called_once_with("message.start", "s")
