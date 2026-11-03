"""Native recovery controls resume the same allowance instead of silently renewing it."""
from hermes_cli import goals
from hermes_cli.goal_command import dispatch_goal_command
from hermes_cli.goals_advisory import advisory_followup_prompt, advisory_continuation
from hermes_state import SessionDB


def test_public_resume_preserves_spent_turns_and_runtime_across_reload(tmp_path, monkeypatch):
    clock = [1700000000.0]
    monkeypatch.setattr(goals.time, "time", lambda: clock[0])
    db = SessionDB(tmp_path / "state.db")
    monkeypatch.setattr(goals, "_get_session_db", lambda: db)
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: 86400)
    manager = goals.GoalManager("synthetic-resume")
    manager.set("Finish the same report", max_turns=32)
    manager.state.turns_used = 7
    manager._save()
    clock[0] += 450
    manager.pause(reason="turn failed: provider/timeout; resume to retry")
    assert goals.GoalManager("synthetic-resume").state.turns_used == 7
    elapsed = manager.state.runtime_elapsed_seconds
    assert elapsed == 450
    restored = goals.GoalManager("synthetic-resume")
    result = dispatch_goal_command(restored, "resume", authorize_gate=lambda *a: None)
    assert not result.error
    again = goals.GoalManager("synthetic-resume")
    assert again.state.status == "active"
    assert again.state.turns_used == 7 and again.state.max_turns == 32
    assert again.state.runtime_elapsed_seconds == elapsed
    assert again.state.runtime_budget_seconds == 86400
    db.close()


def test_repair_review_is_an_actionable_existing_native_continuation():
    state = type("State", (), {"advisory_progress": None})()
    advisory = {"followup_actionable": True, "progress_fingerprint": "rejected-item-1",
                "followup_actions": ["repair_review"], "requires_followup": True}
    status, reason = advisory_continuation(state, advisory, final_boundary=True)
    assert status == "continue" and not reason
    prompt = advisory_followup_prompt("retained-review-and-exact-repair-call",
        goal_text="authorized-scope", followup_actions=advisory["followup_actions"])
    assert "retained-review-and-exact-repair-call" in prompt and "authorized-scope" in prompt
    status, reason = advisory_continuation(state, advisory, final_boundary=True)
    assert status == "advisory_no_progress"  # a loop with no changed receipt remains bounded
