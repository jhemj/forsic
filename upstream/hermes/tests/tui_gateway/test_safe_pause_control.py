"""Native RPC, queue and notification paths honor durable safe pause, offline."""
import contextlib
import logging
import json
import hashlib
import time
import threading
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from hermes_cli import goals
from hermes_state import SessionDB
from tui_gateway import methods_session_control, prompt_turn, session_auto_continue, session_notifications


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(tmp_path / "state.db")
    monkeypatch.setattr(goals, "_get_session_db", lambda: db)
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: None)
    manager = goals.GoalManager("synthetic-safe-pause")
    manager.set("Inspect synthetic evidence")
    session = {"session_key": manager.session_id, "profile_home": str(tmp_path), "history_lock": threading.RLock(), "running": True}
    handlers = {}
    server = NS(Path=Path, contextlib=contextlib, logger=logging.getLogger(__name__), json=json, hashlib=hashlib, time=time,
        _hermes_home=tmp_path, _load_cfg=lambda: {}, _emit=Mock(),
        _methods=handlers, _profile_scoped=lambda fn: fn,
        _sess_nowait=lambda params, rid: (session, None),
        _ok=lambda rid, value: {"result": value},
        _err=lambda rid, code, message: {"error": {"code": code, "message": message}})
    server.register_method = lambda name, fn: handlers.setdefault(name, fn)
    session_auto_continue.register(server)
    session_notifications.register(server)
    prompt_turn.register(server)
    methods_session_control.register(server)
    server._release_hosted_room_turn_slot = Mock()
    server._load_loop_state = server._load_heartbeat_state = lambda key: None
    @contextlib.contextmanager
    def admission(session):
        with session["history_lock"]:
            yield True
    server._session_turn_admission = admission
    server._hook_failure = lambda what, exc: (_ for _ in ()).throw(exc)
    yield server, handlers, session, manager, db
    db.close()


def test_safe_pause_rpc_preserves_work_and_rejects_early_resume(runtime):
    server, handlers, session, manager, db = runtime
    reply = handlers["session.control"]("request", {"session_id": "runtime", "action": "goal.pause_safe"})
    assert reply["result"]["control"]["goal"]["pause_requested"]
    assert reply["result"]["control"]["goal"]["status"] == "active"
    assert session["_turn_cancel_requested"]
    assert session["_queued_prompt_generation"] == 1
    assert "agent" not in session  # no agent build or interrupt was needed
    assert handlers["session.control"]("request", {"session_id": "runtime", "action": "goal.resume"})["error"]["code"] == 4009
    assert goals.load_goal(manager.session_id).pause_requested
    # Repeated read/request does not resume or spend a goal turn.
    assert handlers["session.control.read"]("read", {"session_id": "runtime"})["result"]["control"]["goal"]["pause_requested"]
    assert goals.load_goal(manager.session_id).turns_used == 0


def test_idle_rpc_pauses_immediately_and_preserves_existing_human_stop(runtime):
    server, handlers, session, manager, db = runtime
    manager.pause(reason="explicit human stop")
    session["running"] = False
    before = db.get_meta("goal:" + manager.session_id)
    handlers["session.control"]("request", {"session_id": "runtime", "action": "goal.pause_safe"})
    # Repeating a safe pause never changes the meaning of an existing manual pause.
    assert db.get_meta("goal:" + manager.session_id) == before


@pytest.mark.parametrize("pending", [True, False])
def test_durable_control_blocks_notifications_queued_and_followup_turns_after_runtime_loss(runtime, pending):
    server, handlers, session, manager, db = runtime
    manager.request_pause() if pending else manager.pause()
    session["running"] = False
    session.pop("_turn_cancel_requested", None)  # a new process has no transient stop latch
    session["queued_prompt"] = {"text": "Queued synthetic input"}
    assert server._session_goal_paused(session)
    assert not server._notif_claim_turn(session)
    assert not server._drain_queued_prompt("queue", "runtime", session)
    assert session["queued_prompt"]["text"] == "Queued synthetic input"
    assert server._run_prompt_submit("notification", "runtime", session, "Automatic work", respect_goal_pause=True) is False
    assert not session["running"]
    server._dispatch_followup_turn = Mock(side_effect=AssertionError("No followup after user pause"))
    server._run_post_turn_followups("followup", "runtime", session, {}, "Stale native continuation")
    server._dispatch_followup_turn.assert_not_called()


def test_pending_crash_marker_never_restarts_the_native_turn(runtime):
    server, handlers, session, manager, db = runtime
    manager.request_pause()
    server.read_turn_marker = lambda *_: {"auto_continue": True, "prompt": "Synthetic request"}
    server._start_agent_build = Mock(side_effect=AssertionError("A pending user pause must not build an agent"))
    assert server._maybe_schedule_auto_continue("runtime", session, manager.session_id) is None
    server._start_agent_build.assert_not_called()


def test_final_or_failed_turn_settles_pending_pause_before_judge_and_preserves_manual_reason(runtime, monkeypatch):
    server, handlers, session, manager, db = runtime
    manager.request_pause()
    judge = Mock(side_effect=AssertionError("No judge after safe pause"))
    monkeypatch.setattr(goals, "judge_goal", judge)
    result = {"interrupted": True, "completed": False, "turn_exit_reason": "safe_paused_by_user"}
    assert server._goal_followup_after_turn("runtime", session, result, "interrupted", "Retained") is None
    assert goals.load_goal(manager.session_id).status == "paused"
    assert goals.load_goal(manager.session_id).paused_reason == "user-safe-paused"
    assert goals.load_goal(manager.session_id).turns_used == 0
    judge.assert_not_called()


def test_externally_owned_loop_rejects_safe_pause_without_mutation(runtime):
    server, handlers, session, manager, db = runtime
    session["agent"] = NS(api_mode="codex_app_server", interrupt=Mock())
    before = db.get_meta("goal:" + manager.session_id)
    reply = handlers["session.control"]("request", {"session_id": "runtime", "action": "goal.pause_safe"})
    assert reply["error"]["code"] == 4004 and "unsupported" in reply["error"]["message"]
    assert db.get_meta("goal:" + manager.session_id) == before
    assert "_turn_cancel_requested" not in session
    session["agent"].interrupt.assert_not_called()


def test_immediate_native_interrupt_wins_and_is_not_reported_as_safe_completion(runtime):
    server, handlers, session, manager, db = runtime
    manager.request_pause()
    result = {"interrupted": True, "completed": False, "turn_exit_reason": "interrupted_by_user"}
    assert server._goal_followup_after_turn("runtime", session, result, "interrupted", "Partial") is None
    saved = goals.load_goal(manager.session_id)
    assert saved.status == "paused" and not saved.pause_requested
    assert saved.paused_reason == "native work interrupted before safe pause"


def test_pending_steer_is_requeued_before_durable_pause_gate(runtime):
    server, handlers, session, manager, db = runtime
    manager.pause()
    session["running"] = False
    held = []
    server._enqueue_prompt = lambda owner, text, transport: held.append((text, transport))
    server._drain_queued_prompt = Mock(side_effect=AssertionError("Paused steer must not start a turn"))
    server._run_post_turn_followups("followup", "runtime", session, {"pending_steer": "Retained user guidance"}, None)
    assert held == [("Retained user guidance", None)]
    server._drain_queued_prompt.assert_not_called()


def test_queued_pause_race_restores_exact_envelope_without_rewriting_accepted_row(runtime):
    server, handlers, session, manager, db = runtime
    session["running"] = False
    envelope = {"text": "Accepted queued request"}
    session["queued_prompt"] = envelope
    server._session_uses_compute_host = lambda owner: False
    moved = []
    def move_row(owner, entry, **kwargs):
        moved.append(entry)
        manager.request_pause()  # raced after the queue claim/generation check
        return None
    server._replace_queued_user_row_for_turn = move_row
    assert server._drain_queued_prompt("queued", "runtime", session)
    assert session["queued_prompt"] is envelope
    assert session.get("queued_prompts", []) == []
    assert moved == [envelope]
    assert not session["running"]
    assert "_submit_user_row" not in session
    # A second poll leaves the same accepted envelope untouched while paused.
    assert not server._drain_queued_prompt("queued", "runtime", session)
    assert moved == [envelope]


def test_ordinary_paused_goal_keeps_direct_human_admission_and_new_store_semantics(runtime, tmp_path):
    server, handlers, session, manager, db = runtime
    manager.pause(reason="budget exhausted")
    server._ensure_session_db_row = Mock(side_effect=RuntimeError("Reached normal human admission"))
    with pytest.raises(RuntimeError, match="normal human admission"):
        server._run_prompt_submit("human", "runtime", session, "A new human question")
    fresh = {"session_key": "first-session", "profile_home": str(tmp_path / "fresh")}
    assert not server._session_goal_paused(fresh)
    assert not (tmp_path / "fresh" / "state.db").exists()


def test_paused_auto_refusal_completes_receipt_and_releases_claimed_slot(runtime):
    server, handlers, session, manager, db = runtime
    manager.request_pause()
    callback = Mock()
    assert not server._run_prompt_submit("automatic", "runtime", session, "Automatic turn",
        respect_goal_pause=True, terminal_callback=callback)
    callback.assert_called_once()
    assert callback.call_args.args[0]["status"] == "failed"
    server._release_hosted_room_turn_slot.assert_called_once_with(session)
    assert not session["running"]


def test_notification_pause_race_releases_delivery_without_consuming_it(runtime, monkeypatch):
    server, handlers, session, manager, db = runtime
    manager.request_pause()
    server._session_profile_runtime_scope = lambda owner: contextlib.nullcontext()
    server._notif_log_failure = Mock()
    event, claim = {"type": "completion", "session_id": "synthetic-process"}, object()
    claim_delivery = Mock(return_value=claim)
    released, completed = Mock(), Mock()
    monkeypatch.setattr("tools.async_delegation.claim_event_delivery", claim_delivery)
    monkeypatch.setattr("tools.async_delegation.release_event_delivery", released)
    monkeypatch.setattr("tools.async_delegation.complete_event_delivery", completed)
    server._notif_dispatch_event("runtime", session, event, "Retained process result")
    released.assert_called_once_with(event, claim)
    completed.assert_not_called()
    assert not session["running"]


def test_isolated_queue_admission_refusal_restores_accepted_envelope(runtime):
    from tui_gateway import compute_host_bridge
    server, handlers, session, manager, db = runtime
    manager.request_pause()
    compute_host_bridge.register(server)
    held = {"text": "Accepted isolated request"}
    session["_compute_host_queued_prompt"] = held
    server._compute_host_adopt_frame_meta = Mock()
    server._clear_inflight_turn = Mock()
    server._apply_compute_host_metadata_mirror = Mock()
    server._release_deferred_active_session_lease = Mock()
    server._compute_host_session_info = lambda owner: {}
    server._on_compute_host_turn_done("worker", "runtime", session,
        {"type": "turn.end", "turn_admitted": False})
    assert session["queued_prompt"] is held
    assert "_compute_host_queued_prompt" not in session
    assert not session["running"]


def test_control_targets_current_native_session_after_compression(runtime):
    server, handlers, session, manager, db = runtime
    child = "synthetic-current-continuation"
    assert goals.migrate_goal_to_session(manager.session_id, child, reason="synthetic compression")
    session["session_key"] = child
    session["agent"] = NS(session_id=child, api_mode="chat_completions")
    reply = handlers["session.control"]("request", {"session_id": "runtime", "action": "goal.pause_safe"})
    assert reply["result"]["control"]["goal"]["pause_requested"]
    assert goals.load_goal(child).pause_requested
    assert goals.load_goal(manager.session_id).status == "cleared"


def test_paused_leftover_steer_uses_native_durable_accepted_row_path(runtime):
    from tui_gateway import session_workdir
    server, handlers, session, manager, db = runtime
    session_workdir.register(server)
    session["running"] = False
    manager.pause()
    db.create_session(manager.session_id, "test")
    server._session_uses_compute_host = lambda owner: False
    server._ensure_session_db_row = lambda owner: True
    server._session_db = lambda owner: contextlib.nullcontext(db)
    server._submit_row_target_key = lambda owner: owner["session_key"]
    server._run_post_turn_followups("safe", "runtime", session,
        {"pending_steer": "Retain this accepted user guidance"}, None)
    envelope = session["queued_prompt"]
    assert envelope["_submit_user_row"]["content"] == envelope["text"]
    # Drop all runtime queue state, as a restart would. A fresh read-only native
    # connection still has the accepted user text; no model or queue replay ran.
    session.clear()
    with contextlib.closing(SessionDB(db.db_path, read_only=True)) as restarted:
        rows = restarted.get_messages(manager.session_id)
        assert [row["content"] for row in rows if row["role"] == "user"] == ["Retain this accepted user guidance"]


def test_stale_isolated_queue_claim_returns_to_parent_and_explicit_resume_dispatches_once(runtime, monkeypatch):
    import sys
    import tui_gateway
    from tui_gateway import compute_host, compute_host_bridge
    parent, handlers, session, manager, db = runtime
    compute_host_bridge.register(parent)
    manager.request_pause()
    session.update(running=True, _queued_prompt_generation=1)
    held = {"text": "Accepted queued input"}
    session["_compute_host_queued_prompt"] = held
    child_session = {"session_key": manager.session_id, "history_lock": threading.RLock(),
                     "running": False, "_queued_prompt_generation": 2, "history": []}
    received = []
    def native_dispatch(rid, sid, owner, text, **kwargs):
        received.append(text)
        owner["running"] = False
        return True
    child = NS(_sessions={"runtime": child_session}, _install_borrowed_lease=Mock(),
        _start_inflight_turn=Mock(), _ensure_session_db_row=Mock(), _persist_branch_seed=Mock(),
        _run_prompt_submit=native_dispatch, _session_info=lambda *args: {})
    monkeypatch.setattr(tui_gateway, "server", child, raising=False)
    monkeypatch.setitem(sys.modules, "hermes_undo", NS(on_user_message_appended=Mock()))
    host = object.__new__(compute_host.ComputeHost)
    host._ensure_server_session = lambda server, frame: child_session
    host._progress_lock, host._progress_counter = threading.Lock(), 0
    frames = []
    host._reply = lambda kind, sid, rid, **payload: frames.append({"type": kind, "sid": sid, **payload})
    parent._clear_inflight_turn = parent._apply_compute_host_metadata_mirror = Mock()
    parent._release_deferred_active_session_lease = Mock()
    parent._compute_host_session_info = lambda owner: {}
    host._run_real_turn({"sid": "runtime", "text": held["text"], "queued_prompt_generation": 1})
    rejected = frames[-1]
    assert rejected["type"] == "turn.end" and rejected["turn_admitted"] is False
    assert rejected["queued_prompt_generation"] == 2
    assert received == []
    parent._on_compute_host_turn_done("stale", "runtime", session, rejected)
    assert session["queued_prompt"] is held and session["_queued_prompt_generation"] == 2
    manager.settle_pause(); manager.resume()  # explicit user resume after settlement
    session["_turn_cancel_requested"] = False
    parent._session_uses_compute_host = lambda owner: True
    parent._replace_queued_user_row_for_turn = lambda *args, **kwargs: None
    def dispatch(rid, sid, owner, text, **kwargs):
        frames.clear()
        host._run_real_turn({"sid": sid, "text": text, **kwargs})
        done = frames[-1]
        assert done["turn_admitted"] is True
        parent._on_compute_host_turn_done(rid, sid, owner, done)
        return {"result": {"status": "streaming"}}
    parent._submit_prompt_to_compute_host = dispatch
    assert parent._drain_queued_prompt("resume", "runtime", session)
    assert received == [held["text"]]
    assert session.get("queued_prompt") is None
    assert not parent._drain_queued_prompt("again", "runtime", session)
