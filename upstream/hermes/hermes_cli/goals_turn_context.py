"""Small runtime outcome carried across delivery; never derived from assistant prose."""
from agent.turn_failure_copy import is_max_iteration_handoff


def goal_turn_result(result):
    """Keep stop facts, not the conversation or provider request, on delivery events."""
    if not isinstance(result, dict):
        return None
    return {key: result[key] for key in (
        "completed", "failed", "interrupted", "turn_exit_reason", "final_response",
        "guardrail",
    ) if key in result}


def goal_turn_context(result, *, remaining_goal_turns):
    if not is_max_iteration_handoff(result):
        return None
    if (result.get("guardrail") or {}).get("action") in {"block", "halt"}:
        return None
    return {"stop_cause": "iteration_limit_handoff", "turn_completed": False,
            "remaining_goal_turns": remaining_goal_turns}
