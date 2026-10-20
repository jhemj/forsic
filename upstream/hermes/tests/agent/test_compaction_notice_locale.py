"""Native compression copy is localized only when presented to the user."""
import re
from unittest.mock import Mock

from agent import conversation_compression as compression
from agent.i18n import reset_language_cache
from agent.status_output import StatusOutputMixin
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def test_cli_profile_locale_preserves_canonical_callbacks_and_numeric_details(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_LANGUAGE", raising=False)
    homes = {}
    for language in ("ko", "en", "ja"):
        home = tmp_path / language
        home.mkdir()
        (home / "config.yaml").write_text(f"display:\n  language: {language}\n")
        homes[language] = home
    agent = StatusOutputMixin()
    agent.log_prefix = "fixture: "
    agent._vprint = Mock()
    agent.status_callback = Mock()
    unknown = [
        "Plugin compressing private context: custom progress",
        "quoted: " + compression.COMPACTION_STATUS,
        compression.CONTEXT_OVERFLOW_BLOCKED_WARNING_TEMPLATE.format(
            tokens=123456, threshold=100000, reason="cooldown: 60s"),
    ]
    reset_language_cache()
    try:
        # Real disk configs, A→B→A, no cache reset between profiles.
        for language in ("ko", "en", "ko", "ja"):
            token = set_hermes_home_override(homes[language])
            try:
                for original in compression.ROUTINE_COMPRESSION_STATUS_SAMPLES:
                    agent._emit_status(original)
                    presented = agent._vprint.call_args.args[0].removeprefix(agent.log_prefix)
                    agent.status_callback.assert_called_with("lifecycle", original)
                    if language == "ko":
                        assert re.search("[가-힣]", presented)
                        assert presented != original
                    else:
                        assert presented == original
                    assert sorted(re.findall(r"[0-9][0-9,]*", presented)) == sorted(
                        re.findall(r"[0-9][0-9,]*", original))
                for original in unknown:
                    agent._emit_status(original)
                    assert agent._vprint.call_args.args[0] == agent.log_prefix + original
                    agent.status_callback.assert_called_with("lifecycle", original)
            finally:
                reset_hermes_home_override(token)
    finally:
        reset_language_cache()
