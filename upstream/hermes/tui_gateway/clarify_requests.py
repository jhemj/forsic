"""Optional background clarify using the native request and prompt queues.

Open requests replay on reconnect; like other native requests they are process-local,
and server/session shutdown cancels them rather than restoring an answer deadline.
"""
from __future__ import annotations

import json

from tools.clarify_gateway import resolve_clarify_timeout
from tools.clarify_tool import _result
from tui_gateway import server_requests


def take_background_context(server, session: dict) -> str:
    """Consume native result envelopes as a note on an already-admitted turn, never a wakeup."""
    with session["history_lock"]:
        head = session.get("queued_prompt")
        entries = ([head] if head else []) + list(session.get("queued_prompts") or [])
        results = [entry for entry in entries if entry.get("_clarification_result")]
        if not results:
            return ""
        server._ac_set_queue(session, [entry for entry in entries if not entry.get("_clarification_result")])
        for entry in results:
            staged = entry.get("_submit_user_row") or {}
            if not isinstance(staged.get("_row_id"), int):
                continue
            try:
                with server._session_db(session) as db:
                    if db is not None:
                        key = server._submit_row_owner_key(staged, session)
                        live_id = db.resolve_active_row_id(key, staged["_row_id"])
                        if live_id is not None:
                            db.deactivate_message(key, live_id)
            except Exception:
                server.logger.debug("clarification result row retirement failed", exc_info=True)
        return "\n\n".join(entry["text"] for entry in results)


def ask(server, sid: str, questions: list[dict], config: dict) -> dict:
    """Default to the existing blocking callback; background mode returns a live request id."""
    wire = [{key: entry[key] for key in ("qid", "question", "choices", "multi_select")}
            for entry in questions]
    timeout = resolve_clarify_timeout(config, surface="ui")
    timeout = timeout if timeout > 0 else None
    params = {"questions": wire}
    qids = [entry["qid"] for entry in questions]
    if (config.get("clarify") or {}).get("background") is not True:
        return server_requests.send("clarify", sid, params, timeout=timeout, qids=qids) or {
            "answers": {}, "outcome": "undelivered"}

    session = server._sessions.get(sid)
    if session is None or session.get("history_lock") is None:
        return {"answers": {}, "outcome": "undelivered"}
    with session["history_lock"]:
        if session.get("_closing") or session.get("_finalized") or session.get("_turn_cancel_requested"):
            return {"answers": {}, "outcome": "cancelled"}
        generation = int(session.get("_queued_prompt_generation", 0))
    native_session_id = str(session.get("session_key") or "")

    def on_result(reply):
        if reply is None:  # incapable client; no request was sent
            return
        with server._session_profile_runtime_scope(session):
            if reply.get("outcome") == "timed_out":
                reply = {**reply, "notice": (
                    "The answer deadline expired. Unanswered questions remain unknown. "
                    "Continue only work covered by existing authorization; silence grants no permission.")}
            result = json.loads(_result(questions, reply))
            from hermes_cli.plugins import invoke_hook
            invoke_hook("clarification_resolved", session_id=native_session_id,
                        server_request_id=reply["server_request_id"], questions=questions, result=result)
            if reply.get("outcome") not in {"submitted", "timed_out"}:
                return  # interruption/shutdown never schedules a new turn
            with session["history_lock"]:
                if (server._sessions.get(sid) is not session or session.get("_closing")
                        or session.get("_finalized")):
                    return
                # Native FIFO + accept-time durable row. The marker distinguishes this
                # transport result from words the user actually typed.
                text = "[Native clarification result]\n" + json.dumps(result, ensure_ascii=False)
                queued = session.get("queued_prompt") or {}
                envelope = server._enqueue_prompt(session, text, queued.get("transport"), merge_text=False)
                if envelope is not None:
                    envelope["_clarification_result"] = True
                    server._persist_queued_user_row(session, envelope, "clarification_result")
                if session.get("running") or reply["outcome"] != "submitted":
                    return
                if session.get("_turn_cancel_requested") or server._session_goal_paused(session):
                    return
                from hermes_cli.goals import load_goal
                goal = load_goal(str(session.get("session_key") or ""))
                if goal is not None and (goal.status != "active" or goal.pause_requested):
                    return

            def drain():
                with server._session_profile_runtime_scope(session):
                    server._drain_queued_prompt("clarify-" + reply["server_request_id"], sid, session)
            server._start_session_work(drain, name="clarify-result-" + sid)

    handle = server_requests.send_async("clarify", sid, params, on_result, qids=qids,
                                        timeout=timeout, background=True)
    if handle.request_id is None:
        return {"answers": {}, "outcome": "undelivered"}
    with session["history_lock"]:
        cancelled = (server._sessions.get(sid) is not session or session.get("_closing")
                     or session.get("_finalized") or session.get("_turn_cancel_requested")
                     or int(session.get("_queued_prompt_generation", 0)) != generation)
    if cancelled:
        # Close/interrupt can clear pending requests just before registration.
        # Withdraw a late registration too; it must never survive its owner.
        handle("interrupted")
        return {"answers": {}, "outcome": "cancelled", "server_request_id": handle.request_id}
    return {"answers": {}, "outcome": "pending", "server_request_id": handle.request_id,
            "notice": ("The question is open while independent work continues. Answers are unknown "
                       "until received; wait for explicit consent before permission-dependent actions.")}
