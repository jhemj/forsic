"""Custom application chrome must not inherit the default project's identity."""
import io
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

from hermes_cli import banner
from hermes_cli.cli_render import _build_compact_banner
from hermes_cli.skin_engine import SkinConfig


@pytest.fixture
def custom_skin(monkeypatch):
    skin = SkinConfig(name="forsic", banner_logo="FORSIC", banner_hero="◉", branding={
        "agent_name": "Forsic", "banner_title": "FORSIC · 포식이",
        "banner_credit": "", "welcome": "증거에서 답을 찾는 포렌식 동료",
    })
    monkeypatch.setattr("hermes_cli.skin_engine.get_active_skin", lambda: skin)
    return skin


@pytest.mark.parametrize("width", [24, 80, 140])
@pytest.mark.parametrize("fast", ["0", "1"])
def test_compact_custom_branding(custom_skin, monkeypatch, width, fast):
    monkeypatch.setenv("HERMES_FAST_STARTUP_BANNER", fast)
    with patch("hermes_cli.cli_render.shutil.get_terminal_size", return_value=SimpleNamespace(columns=width)):
        rendered = _build_compact_banner()
    assert "Forsic" in rendered
    assert "Hermes" not in rendered
    assert "Nous Research" not in rendered


def test_full_custom_banner_preserves_model_not_upstream_brand(custom_skin, monkeypatch):
    monkeypatch.setattr(banner, "_mcp_configured", lambda: False)
    monkeypatch.setattr(banner, "_codex_runtime_active", lambda: False)
    monkeypatch.setattr(banner, "_active_profile_name", lambda: None)
    monkeypatch.setattr(banner, "get_update_result", lambda **kwargs: 0)
    with patch.object(banner, "get_latest_release_tag") as release:
        output = io.StringIO()
        banner.build_welcome_banner(
            console=Console(file=output, width=140, color_system=None),
            model="qwen-test", cwd="/case", session_id="current",
            get_toolset_for_tool=lambda name: None, availability={}, skills_by_category={},
        )
        release.assert_not_called()
    rendered = output.getvalue()
    assert "FORSIC" in rendered
    assert "qwen-test" in rendered
    assert "current" in rendered
    assert "Hermes" not in rendered
    assert "Nous Research" not in rendered


def test_default_credit_unchanged(monkeypatch):
    monkeypatch.setattr(banner, "_active_skin", lambda: SkinConfig(name="default"))
    lines = banner._banner_left_lines("test-model", "/case", None, None, None, accent="white", dim="grey")
    assert "Nous Research" in "\n".join(lines)
