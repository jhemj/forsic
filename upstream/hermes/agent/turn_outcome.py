"""Native turn outcome adapter; control flow never depends on rendered error text."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class TurnOutcome:
    kind: Literal["complete", "yielded", "recoverable_error", "terminal_error", "interrupted"]
    cause: str
    progress_saved: bool = False
    next_owner: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def display_status(self) -> str:
        return {"complete": "complete", "yielded": "yielded", "recoverable_error": "yielded",
                "terminal_error": "error", "interrupted": "interrupted"}[self.kind]


def compression_outcome(cause: str) -> TurnOutcome:
    if cause in {"compression_lock_busy", "compression_transient_guard"}:
        return TurnOutcome("yielded", cause, True, "native_goal_recovery")
    # An unavailable lock store is not evidence that another compression owns it.
    return TurnOutcome("terminal_error", "compression_lock_state_unknown", True)


def turn_outcome(result: Any) -> TurnOutcome:
    if not isinstance(result, dict):
        return TurnOutcome("complete", "response")
    if result.get("interrupted"):
        return TurnOutcome("interrupted", str(result.get("turn_exit_reason") or "interrupted"))
    typed = result.get("turn_outcome")
    if isinstance(typed, dict) and result.get("compression_deferred"):
        outcome = compression_outcome(str(typed.get("cause") or "unknown"))
        if (typed.get("kind") != outcome.kind or typed.get("progress_saved") is not True
                or typed.get("next_owner") != outcome.next_owner
                or bool(result.get("failed")) != (outcome.kind == "terminal_error")
                or result.get("completed") is True):
            return TurnOutcome("terminal_error", "invalid_turn_outcome")
        return outcome
    if result.get("compression_deferred"):
        if result.get("failed") or result.get("completed") is True:
            return TurnOutcome("terminal_error", "invalid_turn_outcome")
        # The legacy flag guarantees a defer, but cannot establish lock ownership.
        return TurnOutcome("yielded", "compression_deferred", True, "native_goal_recovery")
    if result.get("compression_exhausted"):
        return TurnOutcome("recoverable_error", "compression_exhausted", True, "native_goal_recovery")
    if result.get("error") or result.get("failed"):
        return TurnOutcome("terminal_error", str(result.get("failure_reason") or "turn_error"))
    return TurnOutcome("complete", str(result.get("turn_exit_reason") or "response"), True)
