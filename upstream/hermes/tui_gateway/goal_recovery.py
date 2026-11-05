"""Wake durable goal recovery through native session admission and the shared timer.

No model, transport, or session authority is recreated from an error string. The
stored session runtime and compression tip are used exactly as for cold resume.
"""
from __future__ import annotations

import contextlib
import threading
import time
import uuid
from pathlib import Path

from .method_ctx import bind_module

_GOAL_RECOVERY_LOCK = threading.Lock()
_GOAL_RECOVERY_HANDLE = None
_GOAL_RECOVERY_OWNER = "tui-goal-recovery:" + uuid.uuid4().hex
_GOAL_RECOVERY_INTERVAL = 5.0


def _start_goal_recovery() -> str:
    global _GOAL_RECOVERY_HANDLE
    from agent.periodic_scheduler import schedule
    with _GOAL_RECOVERY_LOCK:
        if _GOAL_RECOVERY_HANDLE is None or _GOAL_RECOVERY_HANDLE.cancelled:
            _GOAL_RECOVERY_HANDLE = schedule(_sweep_goal_recovery, _GOAL_RECOVERY_INTERVAL)
    return _GOAL_RECOVERY_OWNER


def _goal_recovery_db(session):
    from hermes_state import SessionDB
    return SessionDB(_session_home(session) / "state.db")


def _register_goal_recovery(session, result, expected_identity):
    from agent.turn_outcome import turn_outcome
    from hermes_cli.goals_recovery import prepare_context_recovery
    outcome = turn_outcome(result)
    source = (result.get("turn_outcome") or {}).get("source_turn") or uuid.uuid4().hex
    try:
        owner = _start_goal_recovery()
    except Exception:
        owner = None  # cannot publish a pending intent with no live wake owner
    with contextlib.closing(_goal_recovery_db(session)) as db:
        return prepare_context_recovery(db, str(session["session_key"]),
            expected_scope=expected_identity, cause=outcome.cause, source_turn=source, owner=owner,
            retry_at=(result.get("turn_outcome") or {}).get("retry_at"))


def _ack_goal_recovery_start(session, metadata):
    """Fence dispatch just after native admission, before creating the turn worker."""
    claim = (metadata or {}).get("goal_recovery")
    if not isinstance(claim, dict):
        return True
    if claim.get('kind') == 'review_wait':
        from hermes_cli.goals_advisory import review_wait_turn_started
        with contextlib.closing(_goal_recovery_db(session)) as db:
            return review_wait_turn_started(db,str(session['session_key']),str(claim.get('claim_id') or ''))
    from hermes_cli.goals_recovery import context_turn_started
    with contextlib.closing(_goal_recovery_db(session)) as db:
        return context_turn_started(db, str(session["session_key"]), str(claim.get("claim_id") or ""))


def _goal_recovery_execution_deadline(session, metadata):
    """Resolve the accepted bound from native state, never from client-supplied metadata."""
    claim = (metadata or {}).get("goal_recovery")
    if not isinstance(claim, dict):
        return None
    if claim.get('kind') == 'review_wait':
        from hermes_cli.goals_advisory import read_review_wait
        from hermes_cli.goals_runtime import runtime_identity, runtime_wall_deadline
        with contextlib.closing(_goal_recovery_db(session)) as db:
            _,state,progress=read_review_wait(db,str(session['session_key']))
        if (not state or state.status!='active' or state.pause_requested
                or list(runtime_identity(state))!=progress.get('goal_identity')
                or progress.get('status')!='dispatched' or progress.get('claim_id')!=claim.get('claim_id')
                or float(progress.get('original_deadline') or 0)<=time.time()):
            raise InterruptedError('native review continuation was superseded before execution')
        return runtime_wall_deadline(state)
    from hermes_cli.goals_recovery import read_context
    from hermes_cli.goals_runtime import runtime_identity, runtime_remaining
    with contextlib.closing(_goal_recovery_db(session)) as db:
        _, state, context = read_context(db, str(session["session_key"]))
    if (state is None or state.status != "active" or state.pause_requested
            or list(runtime_identity(state)) != context.get("goal_identity")
            or context.get("status") != "dispatched" or context.get("claim_id") != claim.get("claim_id")
            or context.get("original_deadline", 0) <= time.time()
            or runtime_remaining(state) in (None, 0)):
        raise InterruptedError("native recovery was superseded before execution")
    return context["original_deadline"]


def _finish_goal_recovery(session, result, expected_identity):
    from agent.turn_outcome import turn_outcome
    from hermes_cli.goals_recovery import read_context, settle_context_recovery
    from hermes_cli.goals import goal_identity
    if turn_outcome(result).kind != "complete":
        return
    with contextlib.closing(_goal_recovery_db(session)) as db:
        _, state, _ = read_context(db, str(session["session_key"]))
        if state is not None and goal_identity(state) == expected_identity:
            settle_context_recovery(db, str(session["session_key"]))
            from hermes_cli.goals_advisory import settle_review_wait
            settle_review_wait(db,str(session['session_key']))


def _maybe_fire_goal_recovery(sid, session):
    # The process-wide wakeup may carry a different profile's inherited context.
    # Scope admission/config reads as well as the eventual agent construction.
    with _session_profile_runtime_scope(session, hydrate_secrets=False):
        return _fire_goal_recovery_scoped(sid, session)


def _fire_goal_recovery_scoped(sid, session):
    from hermes_cli.goals import GoalManager
    from hermes_cli.goals_recovery import claim_context_recovery, settle_context_recovery
    from hermes_cli.goals_advisory import read_review_wait, claim_review_wait, settle_review_wait
    key = str(session.get("session_key") or "")
    if not key:
        return False
    # Pollers and startup sweep share the same native in-memory admission lock.
    with _session_turn_admission(session) as admitted:
        if (not admitted or session.get("running") or session.get("_turn_cancel_requested")
                or _session_goal_paused(session)):
            return False
        # A foreign live executor wins before *any* recovery mutation, including deadline settlement.
        # Native admission revalidates this lease again at actual turn submission.
        if _ensure_active_session_slot(sid, session) is not None:
            return False
        # Never recreate a parent executor after compression. Its current tip owns the goal.
        with contextlib.closing(_goal_recovery_db(session)) as db:
            if db.resolve_resume_session_id(key) != key:
                return False
            _,_,progress=read_review_wait(db,key)
            review_wait=progress.get('status') in {'waiting','claimed','dispatched'}
            claim = (claim_review_wait(db,key,owner=_GOAL_RECOVERY_OWNER) if review_wait else
                     claim_context_recovery(db, key, owner=_GOAL_RECOVERY_OWNER, reconcile_abandoned=True))
        if not claim:
            return False
        session["running"] = True
    try:
        with _session_profile_runtime_scope(session):
            _start_agent_build(sid, session)
            remaining = max(0., min(30., claim["original_deadline"] - time.time()))
            if _wait_agent(session, "__goal_recovery__", timeout=remaining) or session.get("agent") is None:
                raise RuntimeError("recovery owner cannot restore the stored runtime")
            prompt = GoalManager(key).next_continuation_prompt()
            if not prompt:
                raise RuntimeError("recovery goal changed before dispatch")
            ok = _run_prompt_submit("__goal_recovery__" + claim["claim_id"], sid, session, prompt,
                respect_goal_pause=True, display_kind="hidden",
                display_metadata={"goal_recovery": {"claim_id": claim["claim_id"],
                                                     "goal_identity": claim["goal_identity"],
                                                     "kind":"review_wait" if review_wait else "context"}})
            if ok is False:
                raise RuntimeError("recovery turn was not admitted")
        return True
    except Exception:
        # Definite pre-dispatch refusal or unknown acceptance is recorded, never blindly replayed.
        with contextlib.closing(_goal_recovery_db(session)) as db:
            if review_wait:
                settle_review_wait(db,key,claim_id=claim['claim_id'],reason='review_wait_dispatch_unavailable')
            else:
                settle_context_recovery(db, key, claim_id=claim["claim_id"],
                                        reason="context_recovery_owner_unavailable")
        with session["history_lock"]:
            session["running"] = False
        logger.warning("native goal recovery could not dispatch session %s", key, exc_info=True)
        return False


def _recovery_automatic_end_reason(reason):
    from hermes_state_common import is_automatic_end_reason
    return is_automatic_end_reason(reason)


def _reclaimed_cli_has_gateway_recovery(db, key, row):
    """A CLI-created row is not CLI-owned after a proven gateway recovery/reclaim.

    Do not adopt arbitrary CLI sessions. The bounded intent must belong to the
    same active goal, and native admission still fences any foreign live lease.
    """
    from math import isfinite
    from hermes_cli.goals_recovery import read_context
    from hermes_cli.goals_runtime import runtime_identity, runtime_remaining
    from tui_gateway.session_lifecycle import _RECLAIM_END_REASONS
    if row.get("ended_at") is None or row.get("end_reason") not in _RECLAIM_END_REASONS:
        return False
    _, state, context = read_context(db, key)
    if not state or state.status != "active" or state.pause_requested or runtime_remaining(state) in (None, 0):
        return False

    def current(intent):
        deadline = intent.get("original_deadline")
        return (intent.get("goal_identity") == list(runtime_identity(state))
                and type(deadline) in (int, float) and isfinite(deadline) and deadline > time.time())

    def gateway_owner(intent):
        return isinstance(intent.get("owner"), str) and intent["owner"].startswith("tui-goal-recovery:")

    progress = state.advisory_progress or {}
    if progress.get("status") in {"waiting", "claimed", "dispatched"}:
        # Waiting is created before a continuation has an owner/claim. The gateway
        # reclaim plus the current bounded request is its provenance; admission
        # still reads the original review's alive/ready receipt before any model.
        waiting = progress["status"] == "waiting"
        requests = progress.get("review_waits")
        return bool(current(progress) and isinstance(requests, list) and requests
            and all(isinstance(item, dict) and isinstance(item.get("request_id"), str)
                    and item["request_id"] for item in requests)
            and progress.get("next_owner") == ("native_review_wait" if waiting else "native_review_continuation")
            and (gateway_owner(progress) or waiting and progress.get("owner") is None))
    return bool(current(context) and context.get("status") in {"pending", "claimed", "dispatched"}
        and context.get("next_owner") == "native_goal_recovery"
        and gateway_owner(context) and context.get("episode_id"))


def _restore_goal_recovery_owner(db, key, home):
    """Reuse cold-resume primitives without requiring a websocket or changing the provider."""
    profile_home = None if Path(home).resolve() == _launch_home().resolve() else Path(home)
    live = _find_live_session_by_key(key, profile_home)
    if live:
        return live
    row = db.get_session(key)
    if not row or (row.get("source") not in {"desktop", "tui", "web"}
                   and not (row.get("source") == "cli" and _reclaimed_cli_has_gateway_recovery(db, key, row))):
        return None
    # Explicitly closed conversations are user controls, not unattended recovery targets.
    if row.get("ended_at") is not None and not _recovery_automatic_end_reason(row.get("end_reason")):
        return None
    from tui_gateway.transport import bind_transport, reset_transport
    token = bind_transport(_detached_ws_transport)
    try:
        with _session_profile_runtime_scope({"profile_home": str(profile_home) if profile_home else None},
                                            hydrate_secrets=False):
            overrides = _stored_session_runtime_overrides(row)
            history = db.get_messages_as_conversation(key, repair_alternation=True, include_row_ids=True)
            cwd = row.get("cwd") or str(home)
            record = _deferred_session_record(key, cols=80, cwd=cwd, history=history, lease=None,
                source=row["source"], profile_home=profile_home, explicit_cwd=True,
                model_override=overrides.get("model_override"), resume_runtime_overrides=overrides)
            sid = uuid.uuid4().hex[:8]
            winner = _claim_or_reuse_live(sid, key, record, None)
            return winner or (sid, record)
    finally:
        reset_transport(token)


def _settle_missing_goal_owner(db, key, home, reason):
    """No-runtime settlement also respects a sibling process's native active-session lease."""
    from hermes_cli.goals_recovery import settle_context_recovery
    profile_home = None if Path(home).resolve() == _launch_home().resolve() else str(home)
    with _session_profile_runtime_scope({"profile_home": profile_home}, hydrate_secrets=False):
        lease, refusal = _claim_active_session_slot(key, live_session_id="recovery-" + uuid.uuid4().hex,
                                                   surface="desktop", profile_home=home)
        if refusal is not None:
            return False
        try:
            from hermes_cli.goals_advisory import read_review_wait, settle_review_wait
            _,_,progress=read_review_wait(db,key)
            if progress.get('status') in {'waiting','claimed','dispatched'}:
                return settle_review_wait(db,key,reason=reason.replace('context_recovery','review_wait'))
            return settle_context_recovery(db, key, reason=reason)
        finally:
            if lease is not None:
                lease.release()


def _settle_expired_ordinary_goal(db, key, home, goal):
    """Only close an expired ownerless v2 goal; never restore ordinary execution."""
    from hermes_cli.goals_completion import enabled
    from hermes_cli.goals_recovery import read_context
    from hermes_cli.goals_runtime import enforce_goal_runtime, runtime_identity, runtime_remaining
    if (not enabled(goal) or goal.runtime_budget_seconds is None
            or goal.runtime_accounting_known and runtime_remaining(goal) != 0):
        return False
    if db.resolve_resume_session_id(key) != key:
        return False
    row = db.get_session(key)
    if (not row or row.get("ended_at") is not None
            and not _recovery_automatic_end_reason(row.get("end_reason"))):
        return False
    profile_home = None if Path(home).resolve() == _launch_home().resolve() else str(home)
    with _session_profile_runtime_scope({"profile_home": profile_home}, hydrate_secrets=False):
        if _find_live_session_by_key(key, profile_home) is not None:
            return False  # a live runtime owns its existing deadline watcher
        lease, refusal = _claim_active_session_slot(key, live_session_id="expiry-" + uuid.uuid4().hex,
                                                   surface="desktop", profile_home=home)
        if refusal is not None:
            return False
        try:
            if (_find_live_session_by_key(key, profile_home) is not None
                    or db.resolve_resume_session_id(key) != key):
                return False
            _, current, _ = read_context(db, key)
            if (not current or current.status != "active" or current.pause_requested
                    or runtime_identity(current) != runtime_identity(goal)):
                return False
            _, expired = enforce_goal_runtime(db, key, expected_goal_id=runtime_identity(goal))
            return expired
        finally:
            if lease is not None:
                lease.release()


def _sweep_goal_recovery():
    """One callback on the existing periodic scheduler; no independent execution loop."""
    from hermes_state import SessionDB
    from hermes_cli.goals_recovery import read_context
    from hermes_cli.profiles import profiles_to_serve
    homes = {_launch_home(), *(Path(p) for _, p in profiles_to_serve(multiplex=True))}
    for home in homes:
        path = home / "state.db"
        if not path.is_file():
            continue
        try:
            with contextlib.closing(SessionDB(path)) as db:
                for meta_key, _ in db.list_meta_prefix("goal:"):
                    key = meta_key[5:]
                    _, goal, context = read_context(db, key)
                    progress=(goal.advisory_progress or {}) if goal else {}
                    review_wait=progress.get('status') in {'waiting','claimed','dispatched'}
                    if not goal or goal.status != "active" or goal.pause_requested:
                        continue
                    if not review_wait and context.get("status") not in {"pending", "claimed", "dispatched"}:
                        _settle_expired_ordinary_goal(db, key, home, goal)
                        continue
                    tip = db.resolve_resume_session_id(key)
                    if tip != key:
                        _, child, child_context = read_context(db, tip)
                        if review_wait:
                            child_wait=(child.advisory_progress or {}) if child else {}
                            same_owner=(child and child_wait.get('status') in {'waiting','claimed','dispatched'}
                                and all(child_wait.get(field)==progress.get(field) for field in
                                        ('goal_identity','fingerprint','claim_id','review_waits')))
                        else:
                            same_owner=(child and context.get('episode_id') is not None
                                        and child_context.get('episode_id')==context.get('episode_id'))
                        if same_owner:
                            continue  # migrated current tip is the sole wake target
                        _settle_missing_goal_owner(db, key, home, "context_recovery_lineage_unavailable")
                        continue
                    owner = _restore_goal_recovery_owner(db, key, home)
                    if owner is None:
                        _settle_missing_goal_owner(db, key, home, "context_recovery_owner_unavailable")
                        continue
                    _maybe_fire_goal_recovery(*owner)
        except Exception:
            # Read/storage failure is unknown; leave the intent for the next bounded tick.
            # Never relabel it as lock contention or consume/recreate its allowance.
            logger.warning("native recovery store unavailable for %s", home, exc_info=True)
    return True


def register(server):
    bind_module(globals(), server, skip=("_",))
