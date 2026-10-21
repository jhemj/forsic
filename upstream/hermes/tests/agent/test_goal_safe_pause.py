"""Offline native safe-pause boundaries with real goal and transcript persistence."""
import contextlib
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from agent.goal_pause import SAFE_PAUSE_REASON, settle_requested_goal_pause
from agent.session_persistence import SessionPersistenceMixin
from hermes_cli import goals
from hermes_state import SessionDB


class OfflineAgent(SessionPersistenceMixin):
    pass


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    db = SessionDB(tmp_path / "state.db")
    monkeypatch.setattr(goals, "_get_session_db", lambda: db)
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: None)
    manager = goals.GoalManager("synthetic-safe-pause")
    manager.set("Inspect synthetic evidence")
    db.create_session(manager.session_id, "test")
    agent = OfflineAgent()
    agent._session_db, agent.session_id = db, manager.session_id
    agent._session_db_created, agent._last_flushed_db_idx = True, 0
    agent._flushed_db_message_ids = set()
    agent._interrupt_requested = False
    yield agent, manager, db
    db.close()


def test_pending_pause_is_durable_and_preserves_accepted_batch_authority(runtime):
    agent, manager, db = runtime
    manager.request_pause()
    restarted = goals.GoalManager(manager.session_id)
    assert restarted.state.status == "active"
    assert restarted.state.pause_requested
    assert restarted.next_continuation_prompt() is None
    with pytest.raises(RuntimeError, match="execution boundary"):
        restarted.resume()
    assert settle_requested_goal_pause(agent)
    assert not settle_requested_goal_pause(agent)
    stopped = goals.GoalManager(manager.session_id)
    assert stopped.state.status == "paused" and not stopped.state.pause_requested
    assert stopped.state.paused_reason == "user-safe-paused"
    assert not agent._interrupt_requested
    assert stopped.resume().status == "active"


def test_preexisting_manual_pause_and_new_goal_win(runtime, monkeypatch):
    agent, manager, db = runtime
    manager.pause(reason="explicit human stop")
    before = db.get_meta("goal:" + manager.session_id)
    manager.request_pause()
    assert not settle_requested_goal_pause(agent)
    assert db.get_meta("goal:" + manager.session_id) == before
    manager.resume()
    manager.request_pause()
    original_cas = db.compare_and_swap_meta

    def replace_before_commit(key, expected, replacement):
        newer = goals.GoalState("New explicit scope").to_json()
        db.set_meta(key, newer)
        return original_cas(key, expected, replacement)

    monkeypatch.setattr(db, "compare_and_swap_meta", replace_before_commit)
    assert settle_requested_goal_pause(agent)
    assert goals.load_goal(manager.session_id).goal == "New explicit scope"
    assert goals.load_goal(manager.session_id).status == "active"


def test_pause_settlement_uses_owning_agent_db_instead_of_ambient_profile(runtime, tmp_path, monkeypatch):
    agent, manager, db = runtime
    manager.request_pause()
    other = SessionDB(tmp_path / "other.db")
    try:
        monkeypatch.setattr(goals, "_get_session_db", lambda: other)
        foreign = goals.GoalManager(manager.session_id)
        foreign.set("Other profile scope")
        assert settle_requested_goal_pause(agent)
        assert goals.GoalState.from_json(db.get_meta("goal:" + manager.session_id)).status == "paused"
        assert goals.load_goal(manager.session_id).goal == "Other profile scope"
        assert goals.load_goal(manager.session_id).status == "active"
    finally:
        other.close()


def test_inflight_tool_batch_finishes_and_persists_each_result_once_before_pause(runtime, monkeypatch):
    from agent import turn_tool_round as tools
    from agent.turn_tool_validation import ToolValidationVerdict

    agent, manager, db = runtime
    agent.quiet_mode, agent.verbose_logging = True, False
    agent.valid_tool_names = {"synthetic_read"}
    agent.stream_delta_callback = None
    agent._tool_guardrail_halt_decision = None
    agent._deduplicate_tool_calls = agent._cap_delegate_task_calls = lambda calls: calls
    calls = [NS(id=str(i), function=NS(name="synthetic_read", arguments="{}")) for i in range(2)]
    response = NS(content="", tool_calls=calls)
    messages = [{"role": "user", "content": "Inspect"}]
    agent._build_assistant_message = lambda message, reason: {"role": "assistant", "content": message.content,
        "tool_calls": [{"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": "{}"}} for call in message.tool_calls]}
    agent._interim_assistant_visible_text = lambda message: message.get("content") or ""
    agent._emit_interim_assistant_message = Mock()
    monkeypatch.setattr(tools, "validate_tool_calls", lambda *a, **kw: ToolValidationVerdict("ok", None, False))
    monkeypatch.setattr("hermes_cli.observability.shared_metrics_harness.finish_tool_round", lambda agent: None)
    compression = Mock(side_effect=AssertionError("No compression/model work after safe pause"))
    monkeypatch.setattr(tools, "compress_after_tool_results", compression)
    started, returned = threading.Event(), threading.Event()
    errors = []

    def execute(message, rows, *_):
        started.set()
        assert returned.wait(5)
        # The accepted batch still has active-goal authority and no cancel bits.
        state = goals.load_goal(manager.session_id)
        assert state.status == "active" and state.pause_requested
        assert not agent._interrupt_requested
        for call in message.tool_calls:
            rows.append({"role": "tool", "name": call.function.name, "tool_call_id": call.id, "content": f"result-{call.id}"})
            assert agent._flush_messages_to_session_db(rows)
    agent._execute_tool_calls = execute
    outcome = []

    def run():
        try:
            outcome.append(tools.run_tool_round(agent, assistant_message=response, finish_reason="tool_calls",
                messages=messages, conversation_history=None, api_call_count=1, effective_task_id="synthetic",
                user_message="Inspect", system_message=None, active_system_prompt="stable", compression_attempts=0,
                max_compression_attempts=3, final_response=None, failed=False, _turn_exit_reason="unknown",
                truncated_tool_call_retries=0, current_turn_user_idx=0))
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(5)
    manager.request_pause()
    assert not agent._interrupt_requested
    returned.set()
    worker.join(5)
    assert not worker.is_alive() and not errors
    assert outcome[0].action == "break" and outcome[0]._turn_exit_reason == SAFE_PAUSE_REASON
    assert agent._flush_messages_to_session_db(messages)
    assert agent._flush_messages_to_session_db(messages)
    saved = db.get_messages(manager.session_id)
    assert [row["content"] for row in saved if row["role"] == "tool"] == ["result-0", "result-1"]
    assert goals.load_goal(manager.session_id).status == "paused"
    compression.assert_not_called()


def test_completed_text_response_is_preserved_without_stop_gate_or_empty_recovery(runtime, monkeypatch):
    from agent import turn_final_response as text

    agent, manager, db = runtime
    manager.request_pause()
    response = NS(content="Observed synthetic result.", tool_calls=[])
    agent._build_assistant_message = lambda message, reason: {"role": "assistant", "content": message.content, "reasoning": "retained synthetic reasoning"}
    forbidden = Mock(side_effect=AssertionError("No optional model work after pause"))
    monkeypatch.setattr(text, "recover_empty_response", forbidden)
    monkeypatch.setattr(text, "apply_stop_gates", forbidden)
    rows = [{"role": "user", "content": "Inspect"}]
    result = text.finish_text_response(agent, assistant_message=response, response=None, finish_reason="stop",
        messages=rows, api_messages=rows, conversation_history=None, api_call_count=1, user_message="Inspect",
        active_system_prompt="stable", final_response=None, _turn_exit_reason="unknown",
        _preflight_compression_blocked=False, codex_ack_continuations=0, truncated_response_parts=[],
        length_continue_retries=0, _pending_verification_response=None,
        _pending_verification_response_previewed=False, effective_task_id="synthetic")
    assert result.action == "break" and result.final_response == response.content
    assert result._turn_exit_reason == SAFE_PAUSE_REASON
    assert agent._flush_messages_to_session_db(rows)
    assert db.get_messages(manager.session_id)[-1]["content"] == response.content
    forbidden.assert_not_called()


def test_active_model_response_finishes_and_prevents_followup(runtime, monkeypatch):
    from agent import conversation_loop as loop

    agent, manager, db = runtime
    state = NS(retry_count=0, max_retries=3, interrupted=False, final_response=None, _turn_exit_reason="unknown")
    calls = []
    def phase(fn, *_args, **_kw):
        calls.append(fn.__name__)
        if fn is loop.perform_api_call:
            manager.request_pause()  # arrives while the accepted provider request runs
        return NS(action="break" if fn is loop.check_api_response else "fallthrough")
    monkeypatch.setattr(loop, "_run_phase", phase)
    assert loop._run_api_retry_loop(agent, state) is None
    assert calls.count("perform_api_call") == 1
    assert goals.load_goal(manager.session_id).pause_requested
    assert loop._run_api_retry_loop(agent, state) is None
    assert state.interrupted and state._turn_exit_reason == SAFE_PAUSE_REASON
    assert calls.count("perform_api_call") == 1
    assert "handle_api_error" not in calls
    assert not agent._interrupt_requested


def test_immediate_native_cancel_keeps_priority_over_safe_pause(runtime):
    agent, manager, db = runtime
    manager.request_pause()
    agent._interrupt_requested = True
    assert not settle_requested_goal_pause(agent)
    assert goals.load_goal(manager.session_id).pause_requested


@pytest.mark.parametrize("failed", [False, True])
def test_native_finalizer_preserves_completed_text_and_failure_precedence(runtime, monkeypatch, failed):
    from agent import turn_finalizer as final

    agent, manager, db = runtime
    manager.request_pause()
    text = "Observed synthetic result with its original content."
    rows = [{"role": "user", "content": "Inspect"}, {"role": "assistant", "content": text}]
    agent.max_iterations = 64
    agent.iteration_budget = NS(remaining=63)
    agent.model, agent.provider, agent.base_url = "synthetic", "synthetic", ""
    agent.context_compressor = NS(last_prompt_tokens=0)
    agent.request_overrides = {}
    agent._tool_guardrail_halt_decision = None
    agent._interrupt_message = None
    agent._last_persistence_error_cause = "locked"
    agent._skill_nudge_interval, agent._iters_since_skill = 0, 0
    agent._save_trajectory = agent._cleanup_task_resources = Mock()
    agent._sync_external_memory_for_turn = Mock()
    agent._drain_pending_steer = lambda: None
    agent.clear_interrupt = Mock()
    agent._persist_disabled = True  # suppress external session-end hook, not this fixture's flush below
    def persist(messages, history):
        agent._persist_disabled = False
        try:
            assert agent._flush_messages_to_session_db(messages, history)
        finally:
            agent._persist_disabled = True
    agent._persist_session = persist
    for key in final._SESSION_TOKEN_KEYS + final._SESSION_COST_KEYS:
        setattr(agent, "session_" + key, 0)
    monkeypatch.setattr(final, "result_model_fields", lambda agent: {})
    monkeypatch.setattr(final, "_log_turn_exit", lambda *a: None)
    monkeypatch.setattr("hermes_cli.observability.shared_metrics_harness.finish_turn", lambda *a, **kw: None)
    monkeypatch.setattr("agent.conversation_loop._notify_context_engine_turn_complete", lambda *a, **kw: None)
    forbidden = Mock(side_effect=AssertionError("No auxiliary model work or text transform after safe pause"))
    monkeypatch.setattr("agent.turn_context.start_deferred_title_upgrade", forbidden)
    monkeypatch.setattr(final, "_micro_compact_after_turn", forbidden)
    monkeypatch.setattr(final, "apply_llm_output_transform", forbidden)
    result = final.finalize_turn(agent, final_response=text, api_call_count=1, interrupted=False, failed=failed,
        messages=rows, conversation_history=None, effective_task_id="synthetic", turn_id="synthetic-turn",
        user_message="Inspect", original_user_message="Inspect", _should_review_memory=False,
        _turn_exit_reason="session_persistence_failed" if failed else "text_response(finish_reason=stop)")
    assert result["final_response"] == text
    assert result["failed"] is failed
    assert result["interrupted"] is (not failed)
    assert result["turn_exit_reason"] == ("session_persistence_failed" if failed else SAFE_PAUSE_REASON)
    assert goals.load_goal(manager.session_id).paused_reason == ("native work failed before safe pause" if failed else "user-safe-paused")
    persist(rows, None)
    saved = db.get_messages(manager.session_id)
    assert [row["content"] for row in saved if row["role"] == "assistant"] == [text]
    assert "cleanup_errors" not in result
    forbidden.assert_not_called()


def test_compression_migration_carries_pending_pause_to_current_native_owner(runtime):
    agent, manager, db = runtime
    manager.request_pause()
    child = "synthetic-compressed-continuation"
    db.create_session(child, "test")
    assert goals.migrate_goal_to_session(manager.session_id, child, reason="synthetic compression")
    state = goals.load_goal(child)
    assert state.status == "active" and state.pause_requested
    assert goals.load_goal(manager.session_id).status == "cleared"
    agent.session_id = child  # native compression publishes the current owner
    assert settle_requested_goal_pause(agent)
    restored = goals.GoalManager(child)
    assert restored.state.status == "paused" and not restored.state.pause_requested
    assert restored.next_continuation_prompt() is None


def test_pending_pause_preserves_inflight_scratchpad_response_before_recovery_guards(runtime, monkeypatch):
    from agent import turn_response_intake as intake
    agent, manager, db = runtime
    manager.request_pause()
    response = NS(content="<REASONING_SCRATCHPAD>unfinished native response", finish_reason="length", tool_calls=[])
    agent.quiet_mode, agent.tool_progress_callback = True, None
    monkeypatch.setattr(intake, "normalize_response_for_agent", lambda owner, raw: response)
    monkeypatch.setattr(intake, "splice_provider_projection", lambda *a: None)
    monkeypatch.setattr(intake, "_fire_post_api_request_hook", lambda *a, **kw: None)
    monkeypatch.setattr("hermes_cli.observability.shared_metrics_harness.record_reply_content", lambda *a: None)
    result = intake.normalize_model_response(agent, response=response, messages=[], api_messages=[],
        conversation_history=None, api_call_count=1, api_duration=1, api_start_time=0,
        api_request_id="synthetic", effective_task_id="synthetic", turn_id="synthetic")
    assert result.action == "fallthrough" and result.assistant_message.content == response.content
    assert goals.load_goal(manager.session_id).pause_requested  # accepted tools keep authority until batch ends


@pytest.mark.parametrize("replacement_status", ["cleared", "done"])
def test_pending_pause_does_not_resurrect_an_explicitly_finished_or_cleared_goal(runtime, replacement_status):
    agent, manager, db = runtime
    manager.request_pause()
    state = goals.load_goal(manager.session_id)
    state.status = replacement_status
    goals.save_goal(manager.session_id, state)
    before = db.get_meta("goal:" + manager.session_id)
    assert not settle_requested_goal_pause(agent)
    assert not goals.GoalManager(manager.session_id).settle_pause()
    assert db.get_meta("goal:" + manager.session_id) == before


@pytest.mark.parametrize("status, expected", [(401, "auth"), (503, "overloaded")])
def test_provider_failure_during_pending_pause_is_retained_without_any_recovery(runtime, monkeypatch, status, expected):
    from agent import turn_api_error as errors
    from agent.api_error_summary import ApiErrorSummaryMixin
    agent, manager, db = runtime
    manager.request_pause()
    class ProviderFailure(RuntimeError):
        pass
    error = ProviderFailure(f"Synthetic provider failure HTTP {status}")
    error.status_code = status
    agent.thinking_callback = None
    agent.provider, agent.model, agent.base_url = "synthetic", "synthetic", ""
    agent._summarize_api_error = ApiErrorSummaryMixin._summarize_api_error
    agent._invoke_api_request_error_hook = Mock()
    rows = [{"role": "user", "content": "Retained original request"}]
    agent._persist_session = lambda messages, history: agent._flush_messages_to_session_db(messages, history)
    forbidden = Mock(side_effect=AssertionError("No retries, fallback or compression after user pause"))
    monkeypatch.setattr(errors, "recover_before_classification", forbidden)
    monkeypatch.setattr(errors, "recover_after_classification", forbidden)
    monkeypatch.setattr(errors, "recover_from_overflow", forbidden)
    result = errors.handle_api_error(agent, api_error=error, _retry=None, thinking_spinner=None,
        messages=rows, api_messages=rows, api_kwargs=None, system_message=None, active_system_prompt="stable",
        conversation_history=None, approx_tokens=10, retry_count=0, max_retries=3, compression_attempts=0,
        max_compression_attempts=3, api_call_count=1, api_request_id="synthetic", api_start_time=0,
        effective_task_id="synthetic", turn_id="synthetic")
    assert result.action == "return"
    outcome = result.result
    assert outcome["failed"] and not outcome["completed"] and not outcome["interrupted"]
    assert outcome["failure_reason"] == expected
    assert str(status) in outcome["error"] and outcome["error"] == outcome["final_response"]
    assert outcome["turn_exit_reason"] == "api_error_during_safe_pause"
    saved = goals.load_goal(manager.session_id)
    assert saved.status == "paused" and not saved.pause_requested
    assert saved.paused_reason == "provider request failed before safe pause"
    assert db.get_messages(manager.session_id)[-1]["content"] == rows[-1]["content"]
    assert agent._invoke_api_request_error_hook.call_args.kwargs["status_code"] == status
    forbidden.assert_not_called()


def test_invalid_inflight_response_keeps_provider_diagnostic_when_pause_prevents_recovery(runtime, monkeypatch):
    from agent import turn_response_check as check
    agent, manager, db = runtime
    manager.request_pause()
    agent.quiet_mode, agent.verbose_logging = True, False
    agent.thinking_callback = None
    agent.api_mode = "chat_completions"
    agent._get_transport = lambda: NS(validate_response=lambda response: False)
    agent._clean_error_message = lambda text: text
    agent._persist_session = lambda messages, history: agent._flush_messages_to_session_db(messages, history)
    rows = [{"role": "user", "content": "Retained input"}]
    forbidden = Mock(side_effect=AssertionError("No response retry after safe-pause request"))
    monkeypatch.setattr(check, "retry_invalid_response", forbidden)
    result = check.check_api_response(agent, response=NS(error={"code": 503, "message": "Synthetic service unavailable"}),
        _retry=None, thinking_spinner=None, messages=rows, api_messages=rows, api_kwargs=None,
        active_system_prompt="stable", conversation_history=None, finish_reason="stop", retry_count=0,
        max_retries=3, compression_attempts=0, max_compression_attempts=3, length_continue_retries=0,
        truncated_response_parts=[], truncated_tool_call_retries=0, current_turn_user_idx=0,
        api_call_count=1, api_request_id="synthetic", api_start_time=0, effective_task_id="synthetic",
        turn_id="synthetic", _preflight_compression_blocked=False, _last_preflight_pressure=None)
    assert result.action == "return" and result.result["failed"] and not result.result["interrupted"]
    assert "Synthetic service unavailable" in result.result["error"]
    assert result.result["turn_exit_reason"] == "invalid_response_during_safe_pause"
    assert goals.load_goal(manager.session_id).paused_reason == "provider response failed before safe pause"
    forbidden.assert_not_called()
