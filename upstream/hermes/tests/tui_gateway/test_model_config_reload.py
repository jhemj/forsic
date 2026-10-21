"""Config adoption uses native profile resolution and client swaps, with no provider requests."""
from contextlib import contextmanager
import json
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from agent import agent_runtime_helpers
from agent.secret_scope import build_profile_secret_scope, reset_secret_scope, set_secret_scope
from hermes_cli import config, runtime_provider
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from tui_gateway import server


@contextmanager
def profile(home):
    home_token = set_hermes_home_override(str(home))
    secret_token = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
    try:
        yield
    finally:
        reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)


def configure(home, url, key, model="synthetic-model"):
    home.mkdir(exist_ok=True)
    with profile(home):
        config.save_config({"model": {"provider": "custom", "base_url": url, "default": model,
                                     "api_mode": "chat_completions", "key_env": "CHOSEN_KEY"}})
        config.save_env_value("CHOSEN_KEY", key)


def make_agent(model, runtime):
    agent = SimpleNamespace(
        model=model, provider=runtime["provider"], requested_provider=runtime["provider"],
        base_url=runtime["base_url"], api_key=runtime["api_key"], api_mode=runtime["api_mode"],
        _credential_pool=runtime.get("credential_pool"), _credential_pool_entry_id=None,
        _config_context_length=None, context_compressor=None, _session_db=None,
        _client_kwargs={}, _custom_providers=[], runtime_capabilities={}, reasoning_config=None,
        _use_prompt_caching=False, _use_native_cache_layout=False,
        _read_reasoning_echo_from_config=lambda: False,
        _anthropic_prompt_cache_policy=lambda **kwargs: (False, False),
        _apply_client_headers_for_base_url=lambda url: None,
    )
    agent._create_openai_client = Mock(side_effect=lambda kwargs, **unused: SimpleNamespace(**kwargs))
    agent.client = SimpleNamespace(api_key=agent.api_key, base_url=agent.base_url)
    agent.switch_model = MethodType(agent_runtime_helpers.switch_model, agent)
    return agent


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *args: pytest.fail("offline config reload test"))
    effects = {}
    for name in ("_restart_slash_worker", "_persist_live_session_runtime", "_persist_live_session_system_prompt",
                 "_append_model_switch_marker", "_emit_session_info", "_emit"):
        effects[name] = Mock()
        monkeypatch.setattr(server, name, effects[name])
    return effects


def session_from_config(home):
    with profile(home):
        model, runtime = server._resolve_agent_model_runtime(None, None)
    return {"agent": make_agent(model, runtime), "profile_home": str(home),
            "config_model_seen": (model, "custom"), "model_override": None}


def adopt(home, session):
    assert session["profile_home"] == str(home)
    with server._session_profile_runtime_scope(session):
        server._sync_agent_model_with_config("synthetic-session", session)


def test_live_url_and_credentials_adopt_on_next_boundary_in_profile_a_b_a(tmp_path, native):
    a, b = tmp_path / "a", tmp_path / "b"
    configure(a, "https://a.synthetic.example/v1", "SYNTHETIC_A_OLD")
    configure(b, "https://b.synthetic.example/v1", "SYNTHETIC_B")
    first, second = session_from_config(a), session_from_config(b)
    original_client = first["agent"].client
    # Save during an active turn cannot mutate its already selected client.
    first["running"] = True
    configure(a, "https://a-new.synthetic.example/v1", "SYNTHETIC_A_NEW")
    assert first["agent"].client is original_client
    assert original_client.api_key == "SYNTHETIC_A_OLD"
    first["running"] = False
    for home, session, url, key in (
        (a, first, "https://a-new.synthetic.example/v1", "SYNTHETIC_A_NEW"),
        (b, second, "https://b.synthetic.example/v1", "SYNTHETIC_B"),
        (a, first, "https://a-new.synthetic.example/v1", "SYNTHETIC_A_NEW"),
    ):
        adopt(home, session)
        agent = session["agent"]
        assert (agent.base_url, agent.api_key) == (url, key)
        assert (agent.client.base_url, agent.client.api_key) == (url, key)
    assert first["agent"]._create_openai_client.call_count == 1
    assert second["agent"]._create_openai_client.call_count == 0
    assert first["agent"]._primary_runtime["base_url"] == "https://a-new.synthetic.example/v1"
    assert "SYNTHETIC_A_NEW" not in json.dumps(first["config_model_runtime_seen"])
    assert not first["model_override"]
    native["_persist_live_session_runtime"].assert_called_once_with(first)
    # New sessions resolve the same saved route without a web-server restart.
    fresh = session_from_config(a)["agent"]
    assert (fresh.base_url, fresh.api_key) == (first["agent"].base_url, first["agent"].api_key)


@pytest.mark.parametrize("change", ["model", "url", "key", "clear"])
def test_same_provider_changes_refresh_native_client_and_clear_ambient_pool(tmp_path, native, change):
    home = tmp_path / "home"
    configure(home, "https://old.synthetic.example/v1", "SYNTHETIC_OLD")
    session = session_from_config(home)
    adopt(home, session)  # establish the successful baseline
    agent = session["agent"]
    agent._credential_pool = Mock()  # an old same-provider recovery pool must not return after key clear
    with profile(home):
        cfg = config.load_config()
        if change == "model":
            cfg["model"]["default"] = "synthetic-new-model"
        elif change == "url":
            cfg["model"]["base_url"] = "https://new.synthetic.example/v1"
        elif change == "key":
            config.save_env_value("CHOSEN_KEY", "SYNTHETIC_NEW")
        else:
            cfg["model"].pop("key_env")
            cfg["model"]["api_key"] = "no-key-required"
        config.save_config(cfg)
    adopt(home, session)
    with profile(home):
        model, expected = server._resolve_agent_model_runtime(None, None)
    assert (agent.model, agent.base_url, agent.api_key) == (model, expected["base_url"], expected["api_key"])
    assert agent._create_openai_client.call_count == 1
    assert agent._credential_pool is None
    assert agent._credential_pool_entry_id is None
    assert agent._primary_runtime["api_key"] == expected["api_key"]


def test_explicit_session_model_pin_preserves_its_route(tmp_path, native):
    home = tmp_path / "home"
    configure(home, "https://old.synthetic.example/v1", "SYNTHETIC_OLD")
    session = session_from_config(home)
    session["model_override"] = {"model": "explicit-model", "provider": "custom"}
    configure(home, "https://new.synthetic.example/v1", "SYNTHETIC_NEW", "profile-new")
    adopt(home, session)
    assert session["agent"].base_url == "https://old.synthetic.example/v1"
    assert session["model_override"]["model"] == "explicit-model"
    session["agent"]._create_openai_client.assert_not_called()


def test_failed_native_client_swap_rolls_back_and_retries_without_persisting_or_logging_keys(tmp_path, native, caplog):
    home = tmp_path / "home"
    configure(home, "https://old.synthetic.example/v1", "SYNTHETIC_OLD")
    session = session_from_config(home)
    agent = session["agent"]
    adopt(home, session)
    before_signature = session["config_model_runtime_seen"]
    before_client = agent.client
    before_pool = agent._credential_pool = Mock()
    configure(home, "https://new.synthetic.example/v1", "SYNTHETIC_NEW")
    agent._create_openai_client.side_effect = RuntimeError("SYNTHETIC_NEW rejected")
    adopt(home, session)
    assert (agent.base_url, agent.api_key, agent.client, agent._credential_pool) == (
        "https://old.synthetic.example/v1", "SYNTHETIC_OLD", before_client, before_pool)
    assert session["config_model_runtime_seen"] == before_signature
    native["_persist_live_session_runtime"].assert_not_called()
    assert "SYNTHETIC_NEW" not in caplog.text
    assert "SYNTHETIC_NEW" not in str(native["_emit"].call_args_list)
    agent._create_openai_client.side_effect = lambda kwargs, **unused: SimpleNamespace(**kwargs)
    adopt(home, session)
    assert agent.api_key == "SYNTHETIC_NEW"
    native["_persist_live_session_runtime"].assert_called_once_with(session)


def test_failed_resolution_keeps_live_runtime(tmp_path, native, monkeypatch):
    home = tmp_path / "home"
    configure(home, "https://old.synthetic.example/v1", "SYNTHETIC_OLD")
    session = session_from_config(home)
    previous = session["agent"].client
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", Mock(side_effect=ValueError("invalid config")))
    adopt(home, session)
    assert session["agent"].client is previous
    assert session["agent"].api_key == "SYNTHETIC_OLD"
    assert "config_model_runtime_seen" not in session
    native["_persist_live_session_runtime"].assert_not_called()


def test_context_only_edit_and_removal_use_existing_compression_sync(tmp_path, native):
    from agent.context_compressor import ContextCompressor
    home = tmp_path / "home"
    configure(home, "https://old.synthetic.example/v1", "SYNTHETIC_OLD")
    session = session_from_config(home)
    agent = session["agent"]
    agent.context_compressor = ContextCompressor(
        model=agent.model, provider=agent.provider, base_url=agent.base_url,
        api_key=agent.api_key, config_context_length=131072)
    for length in (262144, None):
        with profile(home):
            cfg = config.load_config()
            if length is None:
                cfg["model"].pop("context_length")
            else:
                cfg["model"]["context_length"] = length
            config.save_config(cfg)
        adopt(home, session)
        with profile(home):
            server._sync_agent_compression_with_config("synthetic-session", session)
        assert agent._config_context_length == length
        assert agent.context_compressor._config_context_length == length
    agent._create_openai_client.assert_not_called()
