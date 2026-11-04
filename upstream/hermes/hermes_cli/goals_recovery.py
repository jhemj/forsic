"""Bounded native recovery intents in the existing goal row, with exact-row CAS.

This is state, not an executor. TUI's existing periodic scheduler/poller owns waking it.
A claim is retained until a turn-start receipt; unknown delivery is settled, never replayed.
"""
from __future__ import annotations

import time
import uuid

from hermes_cli.goals import GoalState, goal_identity
from hermes_cli.goals_runtime import (runtime_identity, runtime_remaining, runtime_wall_deadline,
                                      settle_runtime)

CONTEXT_EPISODE_SECONDS = 900.0
CONTEXT_RETRY_SECONDS = 10.0
CONTEXT_MAX_ATTEMPTS = 3
CONTEXT_EXHAUSTION_LIMIT = 1
OWNER_LEASE_SECONDS = 30.0


def read_context(db, session_id):
    raw = db.get_meta("goal:" + session_id)
    state = GoalState.from_json(raw) if raw else None
    context = dict((getattr(state, "runtime_recovery", {}) or {}).get("context") or {}) if state else {}
    return raw, state, context


def _identity_matches(state, context):
    return bool(state and list(runtime_identity(state)) == context.get("goal_identity"))


def _writable(state):
    return bool(state and state.status == "active" and not state.pause_requested)


def _save(db, session_id, raw, state, context):
    state.runtime_recovery = {**(getattr(state, "runtime_recovery", {}) or {}), "context": context}
    settle_runtime(state)
    saved = db.compare_and_swap_meta("goal:" + session_id, raw, state.to_json())
    if saved and context.get("status") == "terminal":
        from hermes_cli.goals_completion import settle_native
        # Failure leaves the original explicit runtime_failure receipt intact.
        try:
            receipt = settle_native(db, session_id, expected_identity=runtime_identity(state),
                                    reason=context["terminal_reason"])
            if receipt.get("status") == "settlement_failed":
                raise RuntimeError("native settlement could not be committed")
        except Exception:
            # Keep an explicit failure; no successful settlement receipt exists.
            latest = db.get_meta("goal:" + session_id)
            current = GoalState.from_json(latest) if latest else None
            if current and runtime_identity(current) == runtime_identity(state):
                current.runtime_settlement = {**current.runtime_settlement, "status": "settlement_failed"}
                db.compare_and_swap_meta("goal:" + session_id, latest, current.to_json())
    return saved


def _terminal(state, context, reason, now):
    # Terminal recovery is not successful investigation/report completion.
    settle_runtime(state, now=now)
    if context.get("status") == "pending":
        context["wait_elapsed_seconds"] = float(context.get("wait_elapsed_seconds") or 0) + max(
            0., now - float(context.get("wait_started_at") or now))
    state.status = "paused"
    state.runtime_started_at = None
    state.paused_reason = reason
    state.last_reason = reason
    state.clear_wait()
    state.runtime_settlement = {**(getattr(state, "runtime_settlement", {}) or {}),
                                "status": "settlement_pending", "reason": reason, "at": now}
    context.update(status="terminal", terminal_reason=reason, settled_at=now, next_due=None,
                   next_owner=None, owner_lease_until=None)


def prepare_context_recovery(db, session_id, *, expected_scope, cause, source_turn, owner, retry_at=None, now=None):
    """Commit a bounded intent before releasing the current turn. Return state disposition."""
    now = time.time() if now is None else now
    raw, state, previous = read_context(db, session_id)
    if not _writable(state) or expected_scope != goal_identity(state):
        return "superseded"
    if previous.get("source_turn") == source_turn and source_turn:
        return previous.get("status", "superseded")
    remaining = runtime_remaining(state, now=now)
    if remaining is None or remaining <= 0:
        context = dict(previous)
        _terminal(state, context, "context_recovery_budget_unavailable", now)
    elif _identity_matches(state, previous) and previous.get("status") == "terminal":
        context = dict(previous)
        _terminal(state, context, previous.get("terminal_reason") or "context_recovery_exhausted", now)
    else:
        same_episode = (_identity_matches(state, previous)
                        and previous.get("status") not in {"recovered", "terminal"})
        deadline = previous.get("original_deadline") if same_episode else None
        from hermes_cli.goals_completion import enabled
        ready_deadline = runtime_wall_deadline(state)
        if enabled(state):
            ready_deadline -= state.runtime_policy["settlement_seconds"]
        else:
            ready_deadline -= state.runtime_budget_seconds * .05  # pinned legacy handoff cutoff
        deadline = min(float(deadline or now + CONTEXT_EPISODE_SECONDS), now + remaining,
                       ready_deadline, float(getattr(state, "wall_deadline", None) or float("inf")))
        attempts = int(previous.get("attempts", 0)) if same_episode else 0
        exhausted = int(previous.get("exhaustion_attempts", 0)) if _identity_matches(state, previous) else 0
        from math import isfinite
        retry_at = (float(retry_at) if isinstance(retry_at, (int, float))
                    and not isinstance(retry_at, bool) and isfinite(retry_at) else now)
        waited = float(previous.get("wait_elapsed_seconds") or 0) if same_episode else 0.
        if same_episode and previous.get("status") == "pending":
            waited += max(0., now - float(previous.get("wait_started_at") or now))
        context = {**previous, "schema": 1, "goal_identity": list(runtime_identity(state)),
                   "episode_id": previous.get("episode_id") if same_episode else uuid.uuid4().hex,
                   "status": "pending", "cause": cause, "source_turn": source_turn,
                   "episode_started_at": previous.get("episode_started_at", now) if same_episode else now,
                   "wait_started_at": now,
                   "wait_elapsed_seconds": waited,
                   "original_deadline": deadline, "attempts": attempts,
                   "exhaustion_attempts": exhausted, "next_due": min(max(now + CONTEXT_RETRY_SECONDS, retry_at), deadline),
                   "next_owner": "native_goal_recovery", "owner": owner,
                   "owner_lease_until": now + OWNER_LEASE_SECONDS, "claim_id": None,
                   "progress_saved": True}
        if cause == "compression_exhausted":
            if exhausted >= CONTEXT_EXHAUSTION_LIMIT:
                _terminal(state, context, "context_recovery_exhausted", now)
            else:
                context["exhaustion_attempts"] = exhausted + 1
        if not owner:
            _terminal(state, context, "context_recovery_owner_unavailable", now)
        elif deadline <= now or attempts >= CONTEXT_MAX_ATTEMPTS:
            _terminal(state, context, "context_recovery_exhausted", now)
        if context["status"] == "pending":
            state.waiting_until = context["next_due"]
            state.waiting_since = now
            state.waiting_reason = cause
    return context["status"] if _save(db, session_id, raw, state, context) else "superseded"


def claim_context_recovery(db, session_id, *, owner, reconcile_abandoned=False, now=None):
    """Atomic attempt reservation. Unknown earlier dispatch never becomes a fresh allowance."""
    now = time.time() if now is None else now
    raw, state, context = read_context(db, session_id)
    if not _writable(state) or not _identity_matches(state, context):
        return None
    status = context.get("status")
    if status not in {"pending", "claimed", "dispatched"}:
        return None
    remaining = runtime_remaining(state, now=now)
    if remaining is None or remaining <= 0 or now >= float(context.get("original_deadline") or 0):
        _terminal(state, context, "context_recovery_deadline", now)
        _save(db, session_id, raw, state, context)
        return None
    if status in {"claimed", "dispatched"}:
        if reconcile_abandoned or float(context.get("owner_lease_until") or 0) <= now:
            _terminal(state, context, "context_recovery_delivery_unknown", now)
            _save(db, session_id, raw, state, context)
        return None
    # A fresh owner lease may belong to another live backend. Startup waits for it.
    if context.get("owner") != owner and float(context.get("owner_lease_until") or 0) > now:
        return None
    if float(context.get("next_due") or 0) > now:
        return None
    from hermes_state import _compression_lock_holder_process_is_dead
    holder = db.get_compression_lock_holder(session_id) if context.get("cause") == "compression_lock_busy" else None
    if holder and not _compression_lock_holder_process_is_dead(holder):
        # Known live ownership is a wait, not another failed provider attempt.
        context.update(next_due=min(now + CONTEXT_RETRY_SECONDS, context["original_deadline"]),
                       owner=owner, owner_lease_until=now + OWNER_LEASE_SECONDS)
        state.waiting_until = context["next_due"]
        _save(db, session_id, raw, state, context)
        return None
    if int(context.get("attempts") or 0) >= CONTEXT_MAX_ATTEMPTS:
        _terminal(state, context, "context_recovery_exhausted", now)
        _save(db, session_id, raw, state, context)
        return None
    context["wait_elapsed_seconds"] = float(context.get("wait_elapsed_seconds") or 0) + max(
        0., now - float(context.get("wait_started_at") or now))
    context.update(status="claimed", claim_id=uuid.uuid4().hex, owner=owner,
                   owner_lease_until=now + OWNER_LEASE_SECONDS,
                   attempts=int(context.get("attempts") or 0) + 1, claimed_at=now)
    context["receipts"] = [*(context.get("receipts") or []),
                           {"event": "claimed", "claim_id": context["claim_id"], "at": now}]
    state.clear_wait()
    return context if _save(db, session_id, raw, state, context) else None


def context_turn_started(db, session_id, claim_id, *, now=None):
    now = time.time() if now is None else now
    raw, state, context = read_context(db, session_id)
    if (not _writable(state) or not _identity_matches(state, context)
            or context.get("status") != "claimed" or context.get("claim_id") != claim_id
            or now >= float(context.get("original_deadline") or 0)):
        return False
    context.update(status="dispatched", started_at=now,
                   owner_lease_until=context["original_deadline"])
    context["receipts"] = [*(context.get("receipts") or []),
                           {"event": "turn_started", "claim_id": claim_id, "at": now}]
    return _save(db, session_id, raw, state, context)


def settle_context_recovery(db, session_id, *, claim_id=None, reason=None, now=None):
    now = time.time() if now is None else now
    raw, state, context = read_context(db, session_id)
    if not _writable(state) or not _identity_matches(state, context):
        return False
    if claim_id and context.get("claim_id") != claim_id:
        return False
    if context.get("status") not in {"pending", "claimed", "dispatched"}:
        return False
    if reason:
        _terminal(state, context, reason, now)
    else:
        context.update(status="recovered", settled_at=now, next_due=None, next_owner=None,
                       owner_lease_until=None)
        state.clear_wait()
    return _save(db, session_id, raw, state, context)
