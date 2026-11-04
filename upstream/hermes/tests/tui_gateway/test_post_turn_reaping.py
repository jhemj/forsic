"""Detached turn cleanup must not race its own native follow-up admission."""
import contextlib
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from tui_gateway import prompt_turn, session_lifecycle, session_reaper


class ManualTimer:
    def __init__(self, interval, callback):
        self.callback = callback

    def start(self):
        pass

    def cancel(self):
        pass


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()
    activity = NS(age=0.)
    session = {
        "session_key": "synthetic-handoff", "profile_home": str(tmp_path),
        "running": False, "history_lock": threading.RLock(), "history": [],
        "_run_thread": worker, "last_active": time.time(),
        "agent": NS(get_activity_summary=lambda: {"seconds_since_activity": activity.age},
                    clear_interrupt=lambda: None),
    }
    server = NS(
        Path=Path, contextlib=contextlib, time=time, logger=logging.getLogger(__name__),
        threading=NS(Timer=ManualTimer), _sessions={"ui": session},
        _sessions_lock=threading.RLock(), _session_resume_lock=threading.RLock(),
        _pending_ws_reaps={}, _closed_session_activity={},
        _WS_ORPHAN_REAP_GRACE_S=20., _WS_ORPHAN_ACTIVITY_STALE_S=600.,
        _WS_ORPHAN_REAP_SLEEP_SLACK_S=2., _WS_ORPHAN_INTERRUPT_REAP_MAX_POLLS=3,
        _WS_ORPHAN_INTERRUPT_REAP_POLL_S=2., _emit=Mock(),
    )
    session_lifecycle.register(server)
    session_reaper.register(server)
    prompt_turn.register(server)
    server._ws_session_is_detached = lambda owner: True
    server._session_has_active_delegations = lambda sid, owner: False
    server._session_pending_kind = lambda sid: None
    server._transport_is_dead = lambda transport: True
    server._teardown_popped_session = Mock()
    server._session_turn_admission = lambda owner: contextlib.nullcontext(True)
    server._ensure_active_session_slot = lambda sid, owner: None
    server._start_inflight_turn = Mock()
    server._session_goal_paused = lambda owner: bool(owner.get("paused"))
    server._drain_queued_prompt = Mock(return_value=False)
    server._session_profile_runtime_scope = lambda owner: contextlib.nullcontext()
    server._poll_bot_live_delivery_once = Mock(return_value=False)
    server._notif_handle_ready = Mock()
    server._hook_failure = Mock()
    from tools.process_registry import process_registry
    monkeypatch.setattr(process_registry, "drain_notifications", lambda **kwargs: [])

    # Keep real follow-up dispatch and ownership/closing admission. The seam is
    # AFTER admission, before spawning a provider-facing conversation worker.
    admitted = []
    def submit(rid, sid, owner, text, **kwargs):
        if server._session_goal_paused(owner):
            return False
        if server._admit_prompt_turn(sid, owner, text, None, None, "hidden", None) is None:
            return False
        admitted.append(text)
        return True
    server._run_prompt_submit = submit
    yield NS(server=server, session=session, worker=worker, activity=activity,
             release=release, admitted=admitted)
    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()


def reap(rt, kind):
    server = rt.server
    if kind == "orphan":
        server._schedule_ws_orphan_reap("ui")
        server._pending_ws_reaps["ui"].callback()
    elif server._session_is_lru_evictable("ui", rt.session):
        server._teardown_popped_session(server._pop_session_by_id("ui"), end_reason="lru_evict")


@pytest.mark.parametrize("kind", ["orphan", "lru"])
@pytest.mark.parametrize("activity_age", [0., 601.])
def test_fresh_native_worker_survives_idle_probe_then_dispatches_once(runtime, kind, activity_age):
    rt = runtime
    rt.activity.age = activity_age
    # Force the real reaper into the interval after running is released but
    # before _run_post_turn_followups admits the next goal turn.
    assert not rt.session["running"] and rt.worker.is_alive()
    reap(rt, kind)
    rt.server._run_post_turn_followups("request", "ui", rt.session, {}, "Continue the active native goal")
    assert rt.admitted == ["Continue the active native goal"]
    assert rt.server._sessions["ui"] is rt.session
    rt.server._teardown_popped_session.assert_not_called()
    rt.server._hook_failure.assert_not_called()


@pytest.mark.parametrize("kind", ["orphan", "lru"])
@pytest.mark.parametrize("state", ["finished", "stale", "explicit_close", "paused", "cancel_requested"])
def test_handoff_protection_preserves_cleanup_and_user_control(runtime, kind, state):
    rt = runtime
    if state == "finished":
        rt.release.set()
        rt.worker.join(timeout=2)
        assert not rt.worker.is_alive()
    elif state == "stale":
        rt.activity.age = rt.server._WS_ORPHAN_ACTIVITY_STALE_S + 1
        rt.session["last_active"] -= rt.server._WS_ORPHAN_REAP_GRACE_S + 100
    elif state == "explicit_close":
        rt.server._pop_session_by_id("ui")
    elif state == "cancel_requested":
        rt.session["_turn_cancel_requested"] = True
    else:
        rt.session["paused"] = True
    if state not in {"explicit_close", "paused", "cancel_requested"}:
        reap(rt, kind)
        assert "ui" not in rt.server._sessions
        assert rt.session["_closing"]
    rt.server._run_post_turn_followups("request", "ui", rt.session, {}, "Do not start after stop")
    assert rt.admitted == []
