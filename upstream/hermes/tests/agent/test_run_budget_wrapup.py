"""Offline durable-clock closing and real native checkpoint persistence."""
from copy import deepcopy
from types import SimpleNamespace as NS, MethodType
from unittest.mock import Mock

import pytest

from agent import conversation_loop as loop, turn_context, turn_finalizer as final
from agent.session_persistence import SessionPersistenceMixin
from hermes_cli import goals, goals_runtime as runtime, goals_completion
from hermes_state import SessionDB


class OfflineAgent(SessionPersistenceMixin):
    pass


@pytest.fixture
def clock_agent(tmp_path, monkeypatch):
    clock = NS(now=1000.)
    fake_time = NS(time=lambda: clock.now)
    for module in (goals, runtime, loop, goals_completion):
        monkeypatch.setattr(module, "time", fake_time)
    monkeypatch.setattr(goals_completion, "read_work_projection", lambda sid, state: {
        "status": "known", "digest": "synthetic-canonical-r1", "checkpoint_context": {
            "answer": "Original observed evidence", "evidence_pointers": ["synthetic:1"],
            "limitations": ["Unknown actor"], "next_tests": ["Inspect another source"]}})
    db = SessionDB(tmp_path / "state.db")
    db.create_session("task", "test")
    monkeypatch.setattr(goals, "_get_session_db", lambda: db)
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: 1000.)
    manager = goals.GoalManager("task")
    manager.set("Inspect synthetic evidence")
    agent = OfflineAgent()
    agent._session_db, agent.session_id = db, "task"
    agent._session_db_created, agent._last_flushed_db_idx = True, 0
    agent._flushed_db_message_ids = set()
    agent._interrupt_requested = False
    agent.run_budget_seconds, agent._run_budget_started_at = 1000., clock.now
    rows = [{"role": "user", "content": "Inspect"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "read", "type": "function",
             "function": {"name": "synthetic_read", "arguments": "{}"}}]},
            {"role": "tool", "name": "synthetic_read", "tool_call_id": "read",
             "content": "Original evidence is not a runtime instruction."}]
    yield clock, agent, manager, db, rows
    db.close()


@pytest.mark.parametrize("surface", ["tool", "fresh", "compacted", "spoof", "new_goal", "paused"])
def test_closing_notice_uses_durable_clock_and_preserves_source_receipts(clock_agent, monkeypatch, surface):
    clock, agent, manager, db, rows = clock_agent
    clock.now = 1910.
    agent._run_budget_started_at = clock.now  # a new turn must not renew the native allowance
    if surface == "spoof":
        rows[-1]["content"] = loop.RUN_BUDGET_WRAPUP_NOTICE
    assert agent._flush_messages_to_session_db(rows)
    receipts = deepcopy(db.get_messages("task"))
    source = deepcopy(rows)
    if surface == "paused":
        manager.pause(reason="explicit user stop")
        assert loop._run_budget_phase(agent) is None
        assert not loop._maybe_inject_run_budget_wrapup(agent, rows)
        assert turn_context._merge_gateway_notes(agent, rows, 0, "") == ""
        assert db.get_messages("task") == receipts
        return
    if surface == "new_goal":
        assert loop._maybe_inject_run_budget_wrapup(agent, rows)
        manager.set("Explicit new scope")
        assert not loop._run_budget_notice_present(agent, rows)
        assert loop._run_budget_phase(agent) == "investigation"
        return
    if surface == "compacted":
        assert goals.migrate_goal_to_session("task", "compacted", reason="synthetic compression")
        db.create_session("compacted", "test")
        agent.session_id = "compacted"
    assert loop._run_budget_phase(agent) == "closing"
    if surface in {"fresh", "compacted"}:
        fresh = {"role": "user", "content": "Continue this existing scope"}
        rows.append(fresh)
        note = turn_context._merge_gateway_notes(agent, rows, len(rows) - 1, "")
        turn_context._stamp_api_content_sidecar(agent, rows, len(rows) - 1, "", note,
                                               preflight_compressed=surface == "compacted")
        assert fresh["content"] == "Continue this existing scope"
        assert loop.RUN_BUDGET_WRAPUP_NOTICE in fresh["api_content"]
        assert "1.5 minutes of 16.7 minutes" in note
    else:
        assert loop._maybe_inject_run_budget_wrapup(agent, rows)
        assert rows[-1]["display_kind"] == "runtime_notice"
        assert "1.5 minutes of 16.7 minutes" in rows[-1]["content"]
    assert rows[:len(source)] == source
    assert db.get_messages("task") == receipts  # persisted evidence was never rewritten
    assert loop._run_budget_notice_present(agent, rows)
    assert not loop._maybe_inject_run_budget_wrapup(agent, rows)


@pytest.mark.parametrize("outcome", ["checkpoint", "handoff_text", "empty", "exception", "persist_failure",
                                     "manual_pause", "new_goal", "hard_cap", "cas_loss", "tool_boundary",
                                     "milestone_continue", "milestone_finish", "milestone_invalid",
                                     "milestone_18", "milestone_latecap", "milestone_exception",
                                     "milestone_persist_failure", "milestone_manual_pause", "milestone_cas_loss",
                                     "milestone_fenced"])
def test_actual_finalizer_saves_partial_before_native_handoff_and_respects_controls(
        clock_agent, monkeypatch, outcome):
    from agent import chat_completion_helpers as helpers, relay_llm
    from agent.iteration_budget import IterationBudget
    from agent.turn_stop_gates import _goal_advisory_stop

    clock, agent, manager, db, rows = clock_agent
    clock.now = 1950.
    milestone = outcome.startswith("milestone_")
    if milestone:
        manager.state.runtime_budget_seconds = 86400.
        db.set_meta("goal:task", manager.state.to_json())
        clock.now = 1000. + 12.4 * 3600
        if outcome == "milestone_18":
            manager.state.runtime_checkpoints = [{"threshold_seconds": 43200, "decision": "continue",
                "reason": "Earlier native decision", "decided_at": 44200., "elapsed_seconds": 43200.,
                "source_message_id": "prior-native-checkpoint"}]
            db.set_meta("goal:task", manager.state.to_json())
            clock.now = 1000. + 18 * 3600
    assert agent._flush_messages_to_session_db(rows)
    receipts = deepcopy(db.get_messages("task"))
    agent.max_iterations, agent.iteration_budget = 64, IterationBudget(64)
    agent.model, agent.provider, agent.base_url = "synthetic", "synthetic", ""
    agent.api_mode, agent.suppress_status_output = "synthetic", True
    agent.context_compressor, agent.request_overrides = NS(last_prompt_tokens=0), {}
    agent._tool_guardrail_halt_decision, agent._interrupt_message = None, None
    agent._skill_nudge_interval, agent._iters_since_skill = 0, 0
    agent._save_trajectory = agent._cleanup_task_resources = Mock()
    agent._sync_external_memory_for_turn = Mock()
    agent._drain_pending_steer = lambda: None
    agent.clear_interrupt, agent._safe_print = Mock(), Mock()
    agent._persist_disabled = True  # suppress unrelated external session-end work
    agent._handle_max_iterations = MethodType(helpers.handle_max_iterations, agent)
    for key in final._SESSION_TOKEN_KEYS + final._SESSION_COST_KEYS:
        setattr(agent, "session_" + key, 0)
    text = "Partial findings: observed evidence retained. Gaps: unknown actor. Next: inspect another source."
    decision = "finish" if outcome == "milestone_finish" else "continue"
    if milestone:
        import json
        text = json.dumps({"decision": decision if outcome != "milestone_invalid" else "done",
                           "reason": "판별 가능한 검사가 남아 있습니다.", "findings": "관측한 결과만 보존했습니다.",
                           "limitations": "행위자는 미확인입니다.", "next_tests": "독립 자료의 실행 기록을 확인합니다."}, ensure_ascii=False)
        if outcome == "milestone_fenced":
            text = "```json\n" + text + "\n```"
    calls = []
    def build(*args):
        def attempt(retry):
            calls.append(retry)
            if outcome in {"exception", "milestone_exception"}:
                raise RuntimeError("synthetic provider failure")
            if outcome == "milestone_latecap":
                clock.now = 1000 + 86400
            return "" if outcome == "empty" else text
        return attempt
    monkeypatch.setitem(helpers._SUMMARY_ATTEMPT_BUILDERS, "synthetic", build)
    monkeypatch.setattr(helpers, "_checkpoint_summary_attempt", build)
    monkeypatch.setattr(helpers, "_iteration_summary_api_messages", lambda agent, messages: deepcopy(messages))
    monkeypatch.setattr(relay_llm, "complete_logical_call", Mock())
    forbidden = Mock(side_effect=AssertionError("No auxiliary review/judge during native partial handoff"))
    monkeypatch.setattr(goals, "judge_goal", forbidden)
    monkeypatch.setattr("hermes_cli.goals_advisory.goal_advisory_result", forbidden)
    monkeypatch.setattr(final, "result_model_fields", lambda agent: {})
    monkeypatch.setattr(final, "_log_turn_exit", lambda *a: None)
    monkeypatch.setattr("hermes_cli.observability.shared_metrics_harness.finish_turn", lambda *a, **kw: None)
    monkeypatch.setattr("agent.conversation_loop._notify_context_engine_turn_complete", lambda *a, **kw: None)
    monkeypatch.setattr("agent.turn_context.start_deferred_title_upgrade", lambda *a: None)
    monkeypatch.setattr(final, "_micro_compact_after_turn", lambda *a: None)
    monkeypatch.setattr(final, "apply_llm_output_transform", lambda agent, response, **kw: (response, False, None))
    monkeypatch.setattr(final, "_append_file_mutation_footer", lambda agent, response, logger: response)
    monkeypatch.setattr(final, "_explain_abnormal_exit", lambda agent, response, *args: response)
    monkeypatch.setattr(final, "_apply_output_hooks", lambda agent, response, *a, **kw: (response, False, None))
    def persist(messages, history):
        if outcome in {"persist_failure", "milestone_persist_failure"}:
            raise RuntimeError("synthetic storage failure")
        agent._persist_disabled = False
        try:
            assert agent._flush_messages_to_session_db(messages, history)
        finally:
            agent._persist_disabled = True
        if outcome in {"manual_pause", "milestone_manual_pause"}:
            manager.request_pause()  # races after accepted closing text is saved
        elif outcome == "new_goal":
            manager.set("New explicit user scope")
    agent._persist_session = persist
    response, reason = None, "runtime_budget_handoff"
    if outcome == "handoff_text":
        assert loop._maybe_inject_run_budget_wrapup(agent, rows)
        assert _goal_advisory_stop(agent, text, rows) is None
        rows.append({"role": "assistant", "content": text})
        response, reason = text, "text_response(finish_reason=stop)"
    else:
        if outcome == "hard_cap":
            clock.now = 2000.
        assert loop._run_budget_checkpoint_due(agent)
    if outcome == "tool_boundary":
        from agent import turn_tool_round as tool_round
        from agent.turn_tool_validation import ToolValidationVerdict
        agent.quiet_mode, agent.verbose_logging = True, False
        agent.valid_tool_names, agent.stream_delta_callback = {"synthetic_read"}, None
        agent._deduplicate_tool_calls = agent._cap_delegate_task_calls = lambda calls: calls
        call = NS(id="closing-read", function=NS(name="synthetic_read", arguments="{}"))
        message = NS(content="", tool_calls=[call])
        agent._build_assistant_message = lambda msg, reason: {"role": "assistant", "content": "", "tool_calls": [
            {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": "{}"}}]}
        agent._interim_assistant_visible_text = lambda message: ""
        agent._emit_interim_assistant_message = Mock()
        monkeypatch.setattr(tool_round, "validate_tool_calls", lambda *a, **kw: ToolValidationVerdict("ok", None, False))
        monkeypatch.setattr("hermes_cli.observability.shared_metrics_harness.finish_tool_round", lambda agent: None)
        compression = Mock(side_effect=AssertionError("No compression/model before native closing checkpoint"))
        monkeypatch.setattr(tool_round, "compress_after_tool_results", compression)
        def execute(message, messages, *args):
            messages.append({"role": "tool", "name": "synthetic_read", "tool_call_id": call.id,
                             "content": "Accepted tool result at closing boundary."})
            agent._persist_disabled = False
            try:
                assert agent._flush_messages_to_session_db(messages)
            finally:
                agent._persist_disabled = True
        agent._execute_tool_calls = execute
        agent._persist_disabled = False
        try:
            verdict = tool_round.run_tool_round(agent, assistant_message=message, finish_reason="tool_calls",
                messages=rows, conversation_history=None, api_call_count=1, effective_task_id="synthetic",
                user_message="Inspect", system_message=None, active_system_prompt="stable", compression_attempts=0,
                max_compression_attempts=3, final_response=None, failed=False, _turn_exit_reason="unknown",
                truncated_tool_call_retries=0, current_turn_user_idx=0)
        finally:
            agent._persist_disabled = True
        assert verdict.action == "break" and verdict._turn_exit_reason == "runtime_budget_handoff"
        assert db.get_messages("task")[-1]["content"] == "Accepted tool result at closing boundary."
        compression.assert_not_called()
        reason = verdict._turn_exit_reason
    if outcome in {"cas_loss", "milestone_cas_loss"}:
        original_cas = db.compare_and_swap_meta
        def control_wins(key, expected, replacement):
            state = goals.GoalState.from_json(expected)
            state.status, state.paused_reason = "paused", "explicit concurrent human pause"
            db.set_meta(key, state.to_json())
            return original_cas(key, expected, replacement)
        monkeypatch.setattr(db, "compare_and_swap_meta", control_wins)
    result = final.finalize_turn(agent, final_response=response, api_call_count=1, interrupted=False,
        failed=False, messages=rows, conversation_history=None, effective_task_id="synthetic", turn_id="turn",
        user_message="Inspect", original_user_message="Inspect", _should_review_memory=False,
        _turn_exit_reason=reason)
    assert result["completed"] is False
    state = goals.load_goal("task")
    saved = db.get_messages("task")
    assert saved[:len(receipts)] == receipts
    if milestone:
        if outcome == "milestone_invalid":
            assert state.status == "active" and state.runtime_checkpoints == []
            assert state.runtime_control["reason"] == "checkpoint_decision_unavailable"
            assert result["turn_exit_reason"] == "runtime_checkpoint_closing_due" and not result["failed"]
        elif outcome in {"milestone_exception", "milestone_persist_failure"}:
            assert state.status == "paused" and state.paused_reason == runtime.RUNTIME_CHECKPOINT_FAILED_REASON
            assert state.runtime_checkpoints == [] and result["failed"]
        elif outcome == "milestone_latecap":
            assert state.status == "paused" and state.paused_reason == runtime.RUNTIME_BUDGET_REASON
            assert state.runtime_checkpoints == [] and result["turn_exit_reason"] == "runtime_budget_exhausted"
        elif outcome == "milestone_manual_pause":
            assert state.paused_reason == "user-safe-paused" and result["interrupted"]
            assert state.runtime_checkpoints == []
        elif outcome == "milestone_cas_loss":
            assert state.paused_reason == "explicit concurrent human pause"
            assert result["turn_exit_reason"] == "runtime_budget_handoff_superseded"
            assert state.runtime_checkpoints == []
        else:
            receipt = state.runtime_checkpoints[-1]
            threshold = 64800 if outcome == "milestone_18" else 43200
            assert receipt["threshold_seconds"] == threshold and receipt["decision"] == decision
            source = next(row for row in saved if row.get("message_uid") == receipt["source_message_id"])
            assert source["display_metadata"]["runtime_milestone"]["raw_response"] == text
            assert f"{threshold // 3600}시간 조사 중간 점검" in source["content"] and source["content"] == result["final_response"]
            assert runtime.runtime_remaining(state) == 86400 - (clock.now - 1000)
            assert state.status == "active"
            assert result["turn_exit_reason"] == "runtime_checkpoint_decided"
            assert not result["failed"]
            if decision in {"continue", "finish"}:
                resumed = goals.GoalManager("task")
                assert resumed.evaluate_after_turn(result["final_response"], turn_result=result)["should_continue"]
                assert resumed.state.turns_used == 1 and resumed.state.max_turns == manager.state.max_turns
                assert runtime.runtime_checkpoint_due(resumed.state) is None
                assert not loop._run_budget_checkpoint_due(agent)
                assert resumed.state.runtime_checkpoints == state.runtime_checkpoints
                assert runtime.runtime_phase(resumed.state) == ("closing" if decision == "finish" else "investigation")
    elif outcome in {"checkpoint", "handoff_text", "tool_boundary"}:
        assert state.status == "paused" and state.paused_reason == runtime.RUNTIME_HANDOFF_REASON
        assert state.last_verdict == "partial" and state.runtime_elapsed_seconds < 1000
        assert [row["content"] for row in saved if row["role"] == "assistant" and row["content"]] == [text]
        assert result["turn_exit_reason"] == "runtime_budget_handoff" and not result["failed"]
    elif outcome in {"empty", "exception", "persist_failure"}:
        assert state.paused_reason == runtime.RUNTIME_CHECKPOINT_FAILED_REASON
        assert result["turn_exit_reason"] == "runtime_budget_checkpoint_failed" and result["failed"]
    elif outcome == "hard_cap":
        assert state.paused_reason == runtime.RUNTIME_BUDGET_REASON
        assert result["turn_exit_reason"] == "runtime_budget_exhausted"
    elif outcome == "new_goal":
        assert state.goal == "New explicit user scope" and state.status == "active"
        assert result["turn_exit_reason"] == "runtime_budget_handoff_superseded"
    elif outcome == "cas_loss":
        assert state.paused_reason == "explicit concurrent human pause"
        assert result["turn_exit_reason"] == "runtime_budget_handoff_superseded"
    else:
        assert state.paused_reason == "user-safe-paused" and result["interrupted"]
    assert calls == ([] if outcome in {"handoff_text", "hard_cap", "milestone_cas_loss"}
                     else [0, 0] if outcome == "milestone_invalid" else [0])
    if outcome not in {"manual_pause", "milestone_manual_pause", "milestone_continue", "milestone_finish", "milestone_18", "milestone_fenced", "milestone_invalid"}:
        assert not goals.GoalManager("task").evaluate_after_turn(text, turn_result=result)["should_continue"]
    forbidden.assert_not_called()


def test_finish_allows_native_report_tools_before_later_partial_handoff(clock_agent, monkeypatch):
    """Closing intent must leave the real tool round able to save/review the report."""
    from agent import turn_tool_round as tool_round
    from agent.message_metadata import append_message
    from agent.turn_tool_validation import ToolValidationVerdict
    test_actual_finalizer_saves_partial_before_native_handoff_and_respects_controls(
        clock_agent, monkeypatch, "milestone_finish")
    clock, agent, manager, db, rows = clock_agent
    # The native turn reset clears these request-local fields; goal intent persists.
    agent._runtime_milestone_threshold = agent._runtime_milestone_decision = None
    clock.now = 1000 + 13 * 3600
    assert loop._run_budget_phase(agent) == "closing"
    assert not loop._run_budget_checkpoint_due(agent)
    assert loop._maybe_inject_run_budget_wrapup(agent, rows)
    agent.quiet_mode, agent.verbose_logging = True, False
    agent.valid_tool_names, agent.stream_delta_callback = {"synthetic_report_write"}, None
    agent._deduplicate_tool_calls = agent._cap_delegate_task_calls = lambda calls: calls
    call = NS(id="save-report", function=NS(name="synthetic_report_write", arguments="{}"))
    message = NS(content="", tool_calls=[call])
    agent._build_assistant_message = lambda msg, reason: {"role": "assistant", "content": "", "tool_calls": [
        {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": "{}"}}]}
    agent._interim_assistant_visible_text = lambda message: ""
    agent._emit_interim_assistant_message = Mock()
    monkeypatch.setattr(tool_round, "validate_tool_calls", lambda *a, **kw: ToolValidationVerdict("ok", None, False))
    monkeypatch.setattr("hermes_cli.observability.shared_metrics_harness.finish_tool_round", lambda agent: None)
    def execute(message, messages, *args):
        append_message(messages, {"role": "tool", "name": call.function.name, "tool_call_id": call.id,
                                  "content": "Synthetic report draft saved; review still required."})
        agent._flush_messages_to_session_db(messages)
        clock.now = 1000 + 0.95 * 86400  # the accepted report tool finishes before safety handoff
    agent._execute_tool_calls = execute
    agent._persist_disabled = False
    verdict = tool_round.run_tool_round(agent, assistant_message=message, finish_reason="tool_calls",
        messages=rows, conversation_history=None, api_call_count=1, effective_task_id="synthetic",
        user_message="Continue closing", system_message=None, active_system_prompt="stable", compression_attempts=0,
        max_compression_attempts=3, final_response=None, failed=False, _turn_exit_reason="unknown",
        truncated_tool_call_retries=0, current_turn_user_idx=0)
    agent._persist_disabled = True
    assert verdict.action == "break" and verdict._turn_exit_reason == "runtime_budget_handoff"
    assert db.get_messages("task")[-1]["content"] == "Synthetic report draft saved; review still required."
    def closing_summary(messages, count, **kwargs):
        text = "부분 결과: 초안은 저장했으나 검토가 남았습니다. 최종 보고서 완료를 주장하지 않습니다."
        append_message(messages, {"role": "assistant", "content": text})
        agent._runtime_checkpoint_generated = True
        return text
    agent._handle_max_iterations = closing_summary
    result = final.finalize_turn(agent, final_response=None, api_call_count=1, interrupted=False,
        failed=False, messages=rows, conversation_history=None, effective_task_id="synthetic", turn_id="closing",
        user_message="Continue closing", original_user_message="Continue closing", _should_review_memory=False,
        _turn_exit_reason=verdict._turn_exit_reason)
    state = goals.load_goal("task")
    assert state.status == "paused" and state.last_verdict == "partial"
    assert result["turn_exit_reason"] == "runtime_budget_handoff" and not result["completed"]
    assert state.runtime_checkpoints[0]["decision"] == "finish"
    assert runtime.runtime_remaining(state) == 0.05 * 86400


def test_finish_closing_text_runs_required_review_without_partial_pause(clock_agent, monkeypatch):
    """Finish ends discovery; required review keeps the existing goal/clock active."""
    from hermes_cli import goals_advisory, plugins
    from agent.turn_stop_gates import _goal_advisory_stop
    real_advisory = goals_advisory.goal_advisory_result
    test_actual_finalizer_saves_partial_before_native_handoff_and_respects_controls(
        clock_agent, monkeypatch, "milestone_finish")
    clock, agent, manager, db, rows = clock_agent
    agent._runtime_milestone_threshold = agent._runtime_milestone_decision = None
    agent._runtime_checkpoint_generated = agent._runtime_checkpoint_saved = False
    agent._runtime_checkpoint_row = None
    clock.now = 1000 + 13 * 3600
    assert loop._run_budget_phase(agent) == "closing"
    assert loop._maybe_inject_run_budget_wrapup(agent, rows)
    before = goals.load_goal("task")
    calls = []
    def review(**kwargs):
        calls.append(kwargs)
        return {"provider": "synthetic", "status": "received", "requires_followup": True,
                "pending_finding_ids": [], "followup_action": "next_review_round",
                "progress_fingerprint": "preserved-round-one",
                "context": "The saved round is handled; a further required review remains."}
    plugin = plugins.PluginManager(scope_key=str(db.db_path))
    plugin._discovered = True
    plugin._hooks["goal_advisory_context"] = [review]
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: plugin)
    monkeypatch.setattr(goals_advisory, "goal_advisory_result", real_advisory)
    text = "The report draft is saved; the next required review is not complete."
    advisory = _goal_advisory_stop(agent, text, rows)
    assert advisory is not None and advisory[2] is False
    assert "next native final boundary" in advisory[0]
    assert calls[0]["stage"] == "final" and calls[0]["draft_response"] == text
    rows.append({"role": "assistant", "content": text})
    result = final.finalize_turn(agent, final_response=text, api_call_count=41, interrupted=False,
        failed=False, messages=rows, conversation_history=None, effective_task_id="synthetic", turn_id="closing",
        user_message="Continue closing", original_user_message="Continue closing", _should_review_memory=False,
        _turn_exit_reason="text_response(finish_reason=stop)")
    assert result["turn_exit_reason"] == "text_response(finish_reason=stop)"
    assert not getattr(agent, "_runtime_checkpoint_generated", False)
    current = goals.load_goal("task")
    assert current.status == "active" and runtime.runtime_phase(current) == "closing"
    assert current.runtime_budget_seconds == before.runtime_budget_seconds
    assert current.runtime_checkpoints == before.runtime_checkpoints
    assert runtime.runtime_remaining(current) == runtime.runtime_remaining(before)
    assert db.get_messages("task")[-1]["content"] == text
    decision = goals.GoalManager("task").evaluate_after_turn(text, turn_result=result)
    assert decision["should_continue"] and decision["status"] == "active"
    assert calls[-1]["stage"] == "exploration"
    assert goals.load_goal("task").runtime_checkpoints == before.runtime_checkpoints


@pytest.mark.parametrize("control", ["handoff", "paused", "pause_during_review", "replacement", "interrupted", "handoff_interrupted"])
def test_closing_review_cannot_override_handoff_or_user_controls(clock_agent, monkeypatch, control):
    from hermes_cli import goals_advisory, plugins
    from agent.turn_stop_gates import _goal_advisory_stop
    real_advisory = goals_advisory.goal_advisory_result
    test_actual_finalizer_saves_partial_before_native_handoff_and_respects_controls(
        clock_agent, monkeypatch, "milestone_finish")
    clock, agent, manager, db, rows = clock_agent
    agent._runtime_milestone_threshold = agent._runtime_milestone_decision = None
    clock.now = 1000 + (0.95 * 86400 if control.startswith("handoff") else 13 * 3600)
    assert loop._maybe_inject_run_budget_wrapup(agent, rows)
    calls = []
    def review(**kwargs):
        calls.append(kwargs)
        if control == "pause_during_review":
            manager.request_pause()
        elif control == "replacement":
            manager.set("New explicit scope wins")
        return {"provider": "synthetic", "status": "received", "requires_followup": True,
                "context": "This advice belongs only to the original goal."}
    plugin = plugins.PluginManager(scope_key=str(db.db_path))
    plugin._discovered = True
    plugin._hooks["goal_advisory_context"] = [review]
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: plugin)
    monkeypatch.setattr(goals_advisory, "goal_advisory_result", real_advisory)
    if control == "paused":
        manager.pause("explicit human stop")
    if control in {"interrupted", "handoff_interrupted"}:
        agent._interrupt_requested = True
    assert _goal_advisory_stop(agent, "A candidate for the original goal", rows) is None
    assert len(calls) == (1 if control in {"pause_during_review", "replacement"} else 0)
    current = goals.load_goal("task")
    if control == "paused":
        assert current.status == "paused" and current.paused_reason == "explicit human stop"
    elif control == "pause_during_review":
        assert current.pause_requested
    elif control == "replacement":
        assert current.goal == "New explicit scope wins" and current.advisory_progress is None
    elif control == "handoff":
        assert runtime.runtime_phase(current) == "handoff"
        assert loop.RUN_BUDGET_CHECKPOINT_REQUEST in rows[-1]["content"]
    else:
        assert agent._interrupt_requested
        if control == "handoff_interrupted":
            response, reason, interrupted = final._resolve_runtime_checkpoint(agent,
                "Interrupted handoff candidate", "text_response(finish_reason=stop)",
                False, False, rows, 41)
            assert interrupted and response == "Interrupted handoff candidate"
            assert reason != "runtime_budget_handoff"
            assert goals.load_goal("task").status == "active"


@pytest.mark.parametrize("delivery", ["stale", "tool", "fresh"])
def test_crossing_handoff_requires_its_own_notice_before_partial_pause(clock_agent, monkeypatch, delivery):
    from agent import chat_completion_helpers as helpers
    from agent.turn_stop_gates import _goal_advisory_stop
    test_actual_finalizer_saves_partial_before_native_handoff_and_respects_controls(
        clock_agent, monkeypatch, "milestone_finish")
    clock, agent, manager, db, rows = clock_agent
    agent._runtime_milestone_threshold = agent._runtime_milestone_decision = None
    agent._runtime_checkpoint_generated = agent._runtime_checkpoint_saved = False
    agent._runtime_checkpoint_row = None
    clock.now = 1000 + 13 * 3600
    assert loop._maybe_inject_run_budget_wrapup(agent, rows)
    closing_notice_row = rows[-1]
    closing_notice = deepcopy(closing_notice_row)
    before = goals.load_goal("task")
    original = "The draft is saved; I will receive the next review round."
    clock.now = 1000 + 0.95 * 86400
    assert loop._run_budget_phase(agent) == "handoff"
    assert not loop._run_budget_notice_present(agent, rows)
    assert not loop._run_budget_closing_answer(agent, rows)
    checkpoint = "Partial checkpoint: the draft is saved, review is incomplete. Automatic work pauses; explicit resume is needed."
    if delivery == "tool":
        rows.append({"role": "assistant", "content": original})
        assert loop._maybe_inject_run_budget_wrapup(agent, rows)
        assert loop.RUN_BUDGET_CHECKPOINT_REQUEST in rows[-1]["content"]
    elif delivery == "fresh":
        rows.append({"role": "user", "content": "Continue the same goal"})
        note = turn_context._merge_gateway_notes(agent, rows, len(rows)-1, "")
        turn_context._stamp_api_content_sidecar(agent, rows, len(rows)-1, "", note,
                                               preflight_compressed=False)
        assert loop.RUN_BUDGET_CHECKPOINT_REQUEST in rows[-1]["api_content"]
    text = original if delivery == "stale" else checkpoint
    if delivery != "stale":
        assert loop._run_budget_notice_present(agent, rows)
        assert loop._run_budget_closing_answer(agent, rows)
    # The hard phase never dispatches another review. If a long API call
    # crossed 95%, the old closing answer needs the existing handoff request.
    assert _goal_advisory_stop(agent, text, rows) is None
    rows.append({"role": "assistant", "content": text})
    requests = []
    def build(_agent, api_messages, _request_id):
        assert api_messages[-1]["content"] == loop.RUN_BUDGET_CHECKPOINT_REQUEST
        def attempt(retry):
            requests.append(retry)
            return checkpoint
        return attempt
    monkeypatch.setitem(helpers._SUMMARY_ATTEMPT_BUILDERS, "synthetic", build)
    result = final.finalize_turn(agent, final_response=text, api_call_count=41, interrupted=False,
        failed=False, messages=rows, conversation_history=None, effective_task_id="synthetic", turn_id="handoff",
        user_message="Continue closing", original_user_message="Continue closing", _should_review_memory=False,
        _turn_exit_reason="text_response(finish_reason=stop)")
    assert requests == ([0] if delivery == "stale" else [])
    assert result["final_response"] == checkpoint and not result["completed"]
    assert result["turn_exit_reason"] == "runtime_budget_handoff"
    current = goals.load_goal("task")
    assert current.status == "paused" and current.last_verdict == "partial"
    assert runtime.runtime_remaining(current) == 0.05 * 86400
    assert current.runtime_checkpoints == before.runtime_checkpoints
    assert any(row is closing_notice_row for row in rows)
    assert closing_notice_row["content"] == closing_notice["content"]
    assert closing_notice_row["display_metadata"] == closing_notice["display_metadata"]
    saved = db.get_messages("task")
    assert saved[-1]["content"] == checkpoint
    if delivery == "stale":
        assert [row["role"] for row in saved[-3:]] == ["assistant", "user", "assistant"]
        assert saved[-2]["content"] == loop.RUN_BUDGET_CHECKPOINT_REQUEST
