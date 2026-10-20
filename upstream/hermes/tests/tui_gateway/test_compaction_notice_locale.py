"""Presentation localization preserves the gateway's compaction lifecycle edges."""
import re
from unittest.mock import Mock

from agent import conversation_compression as compression
from agent.i18n import reset_language_cache
from tui_gateway import server


def test_compaction_progress_and_done_keep_classification_with_korean_display(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_LANGUAGE", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("display:\n  language: ko\n")
    emit = Mock()
    monkeypatch.setattr(server, "_emit", emit)
    reset_language_cache()
    try:
        for original in compression.ROUTINE_COMPRESSION_STATUS_SAMPLES:
            done = original == compression.COMPACTION_DONE_STATUS
            server._status_update("fixture", "compacted" if done else "lifecycle", original)
            event, sid, payload = emit.call_args.args
            assert (event, sid) == ("status.update", "fixture")
            assert payload["kind"] == ("compacted" if done else "compacting")
            assert re.search("[가-힣]", payload["text"])
        blocked = compression.CONTEXT_OVERFLOW_BLOCKED_WARNING_TEMPLATE.format(
            tokens=123456, threshold=100000, reason="cooldown: 60s")
        server._status_update("fixture", "warn", blocked)
        assert emit.call_args.args[2] == {"kind": "warn", "text": blocked}
        custom = "Custom engine is compressing its context"
        server._status_update("fixture", "lifecycle", custom)
        assert emit.call_args.args[2] == {"kind": "compacting", "text": custom}
        # The native one-shot done callback remains canonical; only its sink translates.
        agent = type("Agent", (), {"status_callback": Mock()})()
        compression._emit_compaction_done(agent)
        agent.status_callback.assert_called_once_with("compacted", compression.COMPACTION_DONE_STATUS)
    finally:
        reset_language_cache()
