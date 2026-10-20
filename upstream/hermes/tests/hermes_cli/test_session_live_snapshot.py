"""Read-only live lookup and durable native pause behavior on synthetic stores."""

import contextlib
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

from hermes_cli import goals, profiles
from hermes_cli.web_routers import sessions
from hermes_constants import get_hermes_home
from hermes_state import SessionDB
from tui_gateway import methods_session, methods_tools, session_auto_continue, turn_marker
from tui_gateway.method_ctx import rebind


@pytest.fixture
def stores(tmp_path, monkeypatch):
    default_home = get_hermes_home()
    homes = {"default": default_home, "worker": default_home / "profiles" / "worker"}
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: default_home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: default_home / "profiles")
    for name, home in homes.items():
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text("{}\n")
        with contextlib.closing(SessionDB(home / "state.db")) as db:
            db.create_session("synthetic-root", source="desktop")
            db.create_session("synthetic-tip", source="desktop", parent_session_id="synthetic-root")
            db.append_message("synthetic-tip", "assistant", "Synthetic persisted response")
            db.set_meta("goal:synthetic-tip", goals.GoalState(
                goal=f"Synthetic {name} task", status="paused", paused_reason="user-paused",
                turns_used=3, created_at=12).to_json())
    import hermes_state
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", default_home / "state.db")
    return homes


@pytest.fixture
def client(stores):
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN
    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return client


@pytest.fixture
def registry(monkeypatch):
    records = []
    gateway = SimpleNamespace(_snapshot_sessions=lambda rid: (list(records), None))
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "tui_gateway.server", gateway)
        yield records


def _get(client, profile="worker", session="synthetic-root"):
    response = client.get(f"/api/sessions/{session}/live", params={"profile": profile})
    assert response.status_code == 200, response.text
    return response.json()


def test_saved_pause_visible_without_runtime_and_reads_never_write(client, stores, registry, monkeypatch):
    original = SessionDB.__init__
    opened = []

    def readonly(self, *args, **kwargs):
        assert kwargs.get("read_only") is True
        opened.append(kwargs["db_path"])
        original(self, *args, **kwargs)

    monkeypatch.setattr(SessionDB, "__init__", readonly)
    monkeypatch.setattr(goals, "_get_session_db", Mock(side_effect=AssertionError("No manager bootstrap")))
    monkeypatch.setattr(sessions, "_open_session_db_for_profile", Mock(side_effect=AssertionError("No schema heal")))
    before = {home / "state.db": (home / "state.db").read_bytes() for home in stores.values()}
    for profile in ("worker", "default", "worker"):
        snapshot = _get(client, profile)
        assert snapshot["profile_name"] == profile
        assert snapshot["requested_session_id"] == "synthetic-root"
        assert snapshot["stored_session_id"] == "synthetic-tip"
        assert snapshot["available"] is False
        assert snapshot["runtime_session_id"] is None
        assert snapshot["running"] is None
        assert snapshot["control"]["goal"]["title"] == f"Synthetic {profile} task"
        assert snapshot["control"]["goal"]["status"] == "paused"
        assert snapshot["control"]["goal"]["paused_reason"] == "user-paused"
    after = {p: p.read_bytes() for p in before}
    assert after == before
    # SQLite mode=ro may create its empty WAL/shared-memory coordination files.
    assert all(not p.read_bytes() for home in stores.values() for p in home.glob("state.db-wal"))
    assert opened == [stores[p] / "state.db" for p in ("worker", "default", "worker")]


def test_only_exact_profile_and_current_compaction_tip_are_live(client, stores, registry):
    registry.extend([
        ("other-profile", {"profile_home": str(stores["default"]), "session_key": "synthetic-tip",
                           "agent": SimpleNamespace(session_id="synthetic-tip"), "running": True}),
        ("exact-runtime", {"profile_home": str(stores["worker"]), "session_key": "synthetic-root",
                           "agent": SimpleNamespace(session_id="synthetic-tip"), "running": False}),
    ])
    snapshot = _get(client)
    assert snapshot["available"] is True
    assert snapshot["runtime_session_id"] == "exact-runtime"
    assert snapshot["running"] is False
    assert _get(client, "default")["runtime_session_id"] == "other-profile"
    assert _get(client)["runtime_session_id"] == "exact-runtime"


@pytest.mark.parametrize("launch_profile", ["default", "worker"])
@pytest.mark.parametrize("owner_field", [{}, {"profile_home": None}])
def test_native_launch_home_sentinel_matches_only_captured_owner(client, stores, registry, launch_profile, owner_field):
    gateway = sys.modules["tui_gateway.server"]
    gateway._hermes_home = stores[launch_profile]
    registry.append(("launch-runtime", {
        **owner_field, "session_key": "synthetic-tip",
        "agent": SimpleNamespace(session_id="synthetic-tip"), "running": True,
    }))
    other_profile = "worker" if launch_profile == "default" else "default"
    for profile in (launch_profile, other_profile, launch_profile):
        snapshot = _get(client, profile)
        assert snapshot["profile_name"] == profile
        assert snapshot["available"] is (profile == launch_profile)
        assert snapshot["runtime_session_id"] == ("launch-runtime" if profile == launch_profile else None)
        assert snapshot["running"] is (True if profile == launch_profile else None)


@pytest.mark.parametrize("kind", ["wrong-key", "missing-owner", "lazy-viewer", "finalized", "ambiguous"])
def test_unproven_runtime_never_enables_controls(client, stores, registry, kind):
    record = {"profile_home": str(stores["worker"]), "session_key": "synthetic-tip",
              "agent": SimpleNamespace(session_id="synthetic-tip"), "running": True}
    if kind == "wrong-key":
        record["agent"].session_id = "synthetic-root"
    elif kind == "missing-owner":
        record.pop("profile_home")
    elif kind == "lazy-viewer":
        record["agent"] = None
    elif kind == "finalized":
        record["_finalized"] = True
    registry.append(("runtime", record))
    if kind == "ambiguous":
        registry.append(("second-runtime", dict(record)))
    snapshot = _get(client)
    assert snapshot["available"] is False
    assert snapshot["runtime_session_id"] is None
    assert snapshot["control"]["goal"]["status"] == "paused"


def test_missing_store_and_missing_session_are_not_created(client, stores):
    (stores["worker"] / "state.db").unlink()
    before = set(stores["worker"].iterdir())
    assert client.get("/api/sessions/synthetic-root/live", params={"profile": "worker"}).status_code == 404
    assert set(stores["worker"].iterdir()) == before
    assert client.get("/api/sessions/absent/live", params={"profile": "default"}).status_code == 404
    assert client.get("/api/sessions/synthetic-root/live", params={"profile": "unknown"}).status_code == 404


def test_live_route_retains_dashboard_auth(client):
    client.headers.clear()
    assert client.get("/api/sessions/synthetic-root/live", params={"profile": "worker"}).status_code == 401


def test_live_and_history_use_same_native_tip_with_empty_continuation_and_branch(client, stores, registry):
    with contextlib.closing(SessionDB(stores["worker"] / "state.db")) as db:
        db.create_session("synthetic-empty", source="desktop", parent_session_id="synthetic-tip")
        db.create_session("synthetic-branch", source="desktop", parent_session_id="synthetic-tip",
                          model_config={"_branched_from": "synthetic-tip"})
        db.append_message("synthetic-branch", "assistant", "Independent branch response")
    registry.append(("runtime", {"profile_home": str(stores["worker"]),
                                 "session_key": "synthetic-tip",
                                 "agent": SimpleNamespace(session_id="synthetic-tip")}))
    snapshot = _get(client)
    history = client.get("/api/sessions/synthetic-root/messages", params={
        "profile": "worker", "order": "latest", "include_compacted": True}).json()
    assert snapshot["stored_session_id"] == history["session_id"] == "synthetic-tip"
    assert snapshot["available"] is True
    assert snapshot["control"]["goal"]["status"] == "paused"
    branch = _get(client, session="synthetic-branch")
    assert branch["stored_session_id"] == "synthetic-branch"
    assert branch["available"] is False


def test_native_pause_survives_new_manager_and_database_handle(stores, monkeypatch):
    home = stores["worker"]
    with contextlib.closing(SessionDB(home / "state.db")) as db:
        monkeypatch.setattr(goals, "_get_session_db", lambda: db)
        manager = goals.GoalManager("synthetic-tip")
        manager.resume(reset_budget=False)
        manager.pause()
    with contextlib.closing(SessionDB(home / "state.db", read_only=True)) as db:
        monkeypatch.setattr(goals, "_get_session_db", lambda: db)
        restored = goals.GoalManager("synthetic-tip")
        assert restored.state.status == "paused"
        assert restored.state.paused_reason == "user-paused"
        assert restored.state.turns_used == 3
        assert restored.is_active() is False
        assert restored.next_continuation_prompt() is None


def _auto_continue_server(home):
    """Run native scheduling code with real persistence and a non-executing work queue."""
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    @contextlib.contextmanager
    def scope(session, **kwargs):
        token = set_hermes_home_override(str(session["profile_home"]))
        try:
            yield
        finally:
            reset_hermes_home_override(token)

    server = SimpleNamespace(
        Path=Path, time=time, os=os, contextlib=contextlib, logger=Mock(),
        _hermes_home=home, read_turn_marker=turn_marker.read_turn_marker,
        clear_turn_marker=turn_marker.clear_turn_marker, marker_writer_state=lambda marker: "dead",
        _session_profile_runtime_scope=scope, _auto_continue_config=lambda: (True, 900, 2),
        _start_session_work=Mock(return_value=object()), _AUTO_CONTINUE_NOTE_PREFIX="[System: Interrupted",
    )
    session_auto_continue.register(server)
    # register publishes the real config reader too; the specific policy is fixed for this test.
    server._auto_continue_config = lambda: (True, 900, 2)
    return server


@pytest.mark.parametrize("status,scheduled", [("paused", False), ("active", True), (None, True)])
def test_cold_resume_respects_saved_pause_before_crash_recovery(stores, monkeypatch, status, scheduled):
    home = stores["worker"]
    with contextlib.closing(SessionDB(home / "state.db")) as db:
        if status is None:
            db.set_meta("goal:synthetic-tip", "")
        else:
            db.set_meta("goal:synthetic-tip", goals.GoalState(
                goal="Synthetic task", status=status, paused_reason="user-paused" if status == "paused" else None).to_json())
        monkeypatch.setattr(goals, "_get_session_db", lambda: db)
        turn_marker.record_turn_start(home, "synthetic-tip", "Continue the synthetic task")
        server = _auto_continue_server(home)
        result = server._maybe_schedule_auto_continue("cold-runtime", {
            "profile_home": str(home), "session_key": "synthetic-tip", "running": False,
            "history_lock": threading.Lock(),
        }, "synthetic-tip")
        assert bool(result) is scheduled
        assert server._start_session_work.call_count == int(scheduled)
        if status == "paused":
            assert goals.GoalManager("synthetic-tip").state.status == "paused"


def test_native_interrupt_retires_both_compaction_markers(stores):
    home = stores["worker"]
    for key in ("synthetic-root", "synthetic-tip", "synthetic-other"):
        turn_marker.record_turn_start(home, key, "Synthetic request")
    record = {"profile_home": str(home), "session_key": "synthetic-tip",
              "_active_turn_marker_key": "synthetic-root", "history_lock": threading.Lock()}
    native = _auto_continue_server(home)
    native._tts_stream_stop = Mock()
    native._sess_nowait = lambda params, rid: (record, None)
    native._sess = native._sess_nowait
    native._str_param = lambda params, key: str(params.get(key) or "")
    native._session_uses_compute_host = lambda session: False
    native._interrupt_session_turn = Mock()
    native._resume_wake_after_interrupt = Mock()
    native._ok = lambda rid, result: {"result": result}
    handler = next(fn for name, fn in methods_session._registry._pending if name == "session.interrupt")
    result = rebind(handler, vars(native))(1, {"session_id": "exact-runtime"})
    assert result["result"]["status"] == "interrupted"
    native._interrupt_session_turn.assert_called_once_with("exact-runtime", record)
    assert turn_marker.read_turn_marker(home, "synthetic-root") is None
    assert turn_marker.read_turn_marker(home, "synthetic-tip") is None
    assert turn_marker.read_turn_marker(home, "synthetic-other") is not None


def test_explicit_native_goal_resume_returns_one_continuation_without_running_it(stores, monkeypatch):
    home = stores["worker"]
    with contextlib.closing(SessionDB(home / "state.db")) as db:
        monkeypatch.setattr(goals, "_get_session_db", lambda: db)
        native = _auto_continue_server(home)
        native._session_key_or_err = lambda *args: ("synthetic-tip", goals, None)
        native._load_cfg = lambda: {}
        native._ok = lambda rid, result: {"result": result}
        result = rebind(methods_tools._cmd_goal, vars(native))(
            1, {"session_id": "exact-runtime"}, {"profile_home": str(home)}, "goal", "resume")
        dispatch = result["result"]
        assert dispatch["type"] == "send"
        assert dispatch["display"] == "/goal resume"
        assert dispatch["message"] == goals.GoalManager("synthetic-tip").next_continuation_prompt()
        assert goals.GoalManager("synthetic-tip").state.status == "active"
        native._start_session_work.assert_not_called()


def test_crash_recovery_does_not_run_when_saved_goal_is_unreadable(stores):
    home = stores["worker"]
    with contextlib.closing(SessionDB(home / "state.db")) as db:
        db.set_meta("goal:synthetic-tip", "invalid-json")
    turn_marker.record_turn_start(home, "synthetic-tip", "Synthetic interrupted request")
    native = _auto_continue_server(home)
    assert native._maybe_schedule_auto_continue("runtime", {
        "profile_home": str(home), "session_key": "synthetic-tip",
    }, "synthetic-tip") is None
    native._start_session_work.assert_not_called()


def test_native_cold_resume_returns_idle_without_unpausing_goal(stores):
    home = stores["worker"]
    turn_marker.record_turn_start(home, "synthetic-tip", "Synthetic interrupted request")
    native = _auto_continue_server(home)
    native._profile_build_scope = lambda owner: contextlib.nullcontext()
    native._stored_session_runtime_overrides = lambda row: {}
    native._todo_state_from_history = lambda history: {}
    native._schedule_agent_build = Mock()  # exercise resume without constructing an agent
    native._schedule_session_cap_enforcement = Mock()
    native._attach_todo_state = lambda payload, record: payload
    native._ok = lambda rid, result: {"result": result}
    native._resume_response = rebind(methods_session._resume_response, vars(native))
    with contextlib.closing(SessionDB(home / "state.db", read_only=True)) as db:
        history = db.get_messages_as_conversation("synthetic-tip")
        ctx = SimpleNamespace(
            rid=1, target="synthetic-tip", profile_home=str(home),
            found=db.get_session("synthetic-tip"), omit_messages=False,
            mint=lambda: ("cold-runtime", "desktop", str(home)),
            restore=lambda: (history, history, history),
            display_prefix=lambda: [], claim=lambda sid, record: None,
            info=lambda cwd, overrides: {"profile_name": "worker"},
            messages=lambda display: display,
            record=lambda source, cwd, history, overrides, **extra: {
                "profile_home": str(home), "session_key": "synthetic-tip",
                "created_at": 12, "running": False, "history_lock": threading.Lock()},
        )
        result = rebind(methods_session._resume_cold, vars(native))(ctx)["result"]
        assert result["session_key"] == "synthetic-tip"
        assert result["running"] is False
        assert result["status"] == "idle"
        assert "auto_continue" not in result
        assert goals.GoalState.from_json(db.get_meta("goal:synthetic-tip")).status == "paused"
    native._start_session_work.assert_not_called()
