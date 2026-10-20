"""A live replay can include the accepted prompt in both history and in-flight state."""
from contextlib import nullcontext
from types import SimpleNamespace
import threading

import pytest

from hermes_state import SessionDB
from tui_gateway import server
from tui_gateway.contracts.sessions import InflightTurn


@pytest.mark.parametrize("queued", [False, True])
def test_live_replay_keeps_accepted_user_identity_after_staging_is_consumed(monkeypatch, tmp_path, queued):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("dialogue", source="test")
        old_id = db.append_message("dialogue", "user", content="check again")
        db.append_message("dialogue", "assistant", content="first answer")
        agent = SimpleNamespace(session_id="dialogue")
        session = {"session_key": "dialogue", "agent": agent, "history": [], "history_lock": threading.RLock()}
        monkeypatch.setattr(server, "_session_db", lambda _: nullcontext(db))
        monkeypatch.setattr(server, "_ensure_session_db_row", lambda _: True)
        monkeypatch.setattr(server, "_persist_branch_seed", lambda _: None)

        if queued:
            server._persist_submit_user_row(session, "check again", None)
            server._start_inflight_turn(session, "check again")
        else:
            server._start_inflight_turn(session, "check again")
            assert server._persist_session_row_for_submit("rpc", session, "check again") is None
        row_id = session["_submit_user_row"]["_row_id"]
        server._adopt_submit_user_row(session, agent, "check again", "check again")
        assert "_submit_user_row" not in session
        server._append_inflight_delta(session, "partial answer")

        inflight = server._inflight_snapshot(session)
        messages = server._history_to_messages(server._live_visible_history(session, db, []))
        assert InflightTurn.model_validate(inflight).user_row_id == row_id != old_id
        assert [m["row_id"] for m in messages if m["role"] == "user"] == [old_id, row_id]
        assert inflight["assistant"] == "partial answer"

        # Failed-turn replay keeps the binding; a new unstored/synthetic turn never inherits it.
        server._fail_inflight_turn(session, "temporary failure")
        assert server._inflight_snapshot(session)["user_row_id"] == row_id
        server._start_inflight_turn(session, "check again")
        assert server._inflight_snapshot(session).get("user_row_id") is None
    finally:
        db.close()


@pytest.mark.parametrize("mode", ["steer", "interrupt"])
@pytest.mark.parametrize("display_kind", [None, "hidden"])
def test_busy_correction_retains_own_visibility_under_hidden_turn(monkeypatch, mode, display_kind):
    accepted = []
    agent = SimpleNamespace(steer=lambda text: accepted.append(text) or True,
        redirect=lambda text: accepted.append(text) or True, _supports_active_turn_redirect=True)
    session = {"agent": agent, "running": True, "history_lock": threading.RLock()}
    server._start_inflight_turn(session, "private continuation", display_kind="hidden")
    server._append_inflight_delta(session, "before")
    monkeypatch.setattr(server, "_load_busy_input_mode", lambda: mode)
    monkeypatch.setattr(server, "_session_compression_in_flight", lambda _: False)
    result = server._handle_busy_submit("rpc", "sid", session, "correct this", None, display_kind=display_kind)
    assert result["result"]["status"] == ("steered" if mode == "steer" else "redirected")
    assert accepted == ["correct this"]
    snapshot = server._inflight_snapshot(session)
    assert InflightTurn.model_validate(snapshot).correction_display_kinds == [display_kind or "visible"]
    assert snapshot["display_kind"] == "hidden"
    assert snapshot["corrections"] == ["correct this"]
    assert snapshot["correction_offsets"] == [len("before")]
    # Reconnect and retained failure replay preserve the accepted correction unchanged.
    assert server._inflight_snapshot(session) == snapshot
    server._fail_inflight_turn(session, "cancelled")
    failed = server._inflight_snapshot(session)
    assert failed["correction_display_kinds"] == snapshot["correction_display_kinds"]
    assert failed["corrections"] == snapshot["corrections"]
    server._clear_inflight_turn(session)
    assert server._inflight_snapshot(session) is None


def test_correction_snapshot_filters_blank_slots_with_metadata_and_preserves_legacy_unknowns():
    session = {"inflight_turn": {"user": "private", "display_kind": "hidden", "assistant": "before",
        "corrections": ["old", " ", "private correction", "public correction"],
        "correction_offsets": [0, 1, 2, 4], "correction_display_kinds": [None, "visible", "hidden", "visible"]}}
    snapshot = server._inflight_snapshot(session)
    assert snapshot["corrections"] == ["old", "private correction", "public correction"]
    assert snapshot["correction_offsets"] == [0, 2, 4]
    assert InflightTurn.model_validate(snapshot).correction_display_kinds == [None, "hidden", "visible"]
    # Appending to a legacy turn must not shift the new metadata onto its unknown first correction.
    session["inflight_turn"].pop("correction_display_kinds")
    session["inflight_turn"].pop("correction_offsets")
    server._record_inflight_correction(session, "new public correction", display_kind="visible")
    snapshot = server._inflight_snapshot(session)
    assert snapshot["correction_display_kinds"] == [None, None, None, "visible"]
    assert "correction_offsets" not in snapshot


@pytest.mark.parametrize("accepted", [False, True])
def test_explicit_session_steer_records_public_only_when_accepted(accepted):
    session = {"agent": SimpleNamespace(steer=lambda _: accepted), "history_lock": threading.RLock()}
    server._start_inflight_turn(session, "private continuation", display_kind="hidden")
    server._apply_correction("rpc", session, "steer", "public correction", "queued")
    snapshot = server._inflight_snapshot(session)
    if accepted:
        assert snapshot["corrections"] == ["public correction"]
        assert snapshot["correction_display_kinds"] == ["visible"]
    else:
        assert "corrections" not in snapshot
        assert "correction_display_kinds" not in snapshot
