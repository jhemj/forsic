"""H11–22: real native rows/CAS and provider-wire seams; no live model or case data."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS, MethodType
from unittest.mock import Mock

import pytest

from agent import runtime_checkpoint as checkpoint, chat_completion_helpers as helpers, turn_finalizer
from agent.context_compressor import _DB_PERSISTED_MARKER
from agent.session_persistence import SessionPersistenceMixin
from hermes_cli import goals, goals_runtime, goals_completion
from hermes_state import SessionDB


GOOD = {"decision": "continue", "reason": "판별 가능한 검사 존재", "findings": "저장된 관측 보존",
        "limitations": "행위자 미확인", "next_tests": "독립 자료의 실행 기록 확인"}


class Agent(SessionPersistenceMixin):
    pass


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    clock = NS(now=1000.)
    for module in (goals, goals_runtime, goals_completion):
        monkeypatch.setattr(module, "time", NS(time=lambda: clock.now))
    db = SessionDB(tmp_path / "state.db")
    db.create_session("checkpoint", "test")
    monkeypatch.setattr(goals, "_get_session_db", lambda: db)
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: 86400.)
    monkeypatch.setattr(goals_completion, "read_work_projection", lambda sid, state: {
        "status": "known", "digest": "canonical-r7", "checkpoint_context": {
            "answer": "저장된 관측", "evidence_pointers": ["synthetic:7"],
            "unresolved_questions": ["행위자"], "next_tests": ["독립 실행 기록 확인"]}})
    manager = goals.GoalManager("checkpoint")
    manager.set("Investigate synthetic evidence")
    clock.now += 12.4 * 3600
    agent = Agent()
    agent._session_db, agent.session_id = db, "checkpoint"
    agent._session_db_created, agent._last_flushed_db_idx = True, 0
    agent._flushed_db_message_ids = set()
    agent._interrupt_requested, agent._runtime_milestone_threshold = False, 43200
    agent._runtime_handoff_identity = goals_runtime.runtime_identity(manager.state)
    agent._runtime_milestone_decision = None
    agent.suppress_status_output, agent.api_mode = True, "chat_completions"
    agent._handle_max_iterations = MethodType(helpers.handle_max_iterations, agent)
    agent._cached_system_prompt = "DO NOT COPY FULL SYSTEM"
    agent.ephemeral_system_prompt, agent.prefill_messages = "DO NOT COPY EPHEMERA", [{"role": "user", "content": "PREFILL"}]
    rows = [{"role": "user", "content": "Original user request"},
            {"role": "assistant", "content": "Original saved answer"},
            {"role": "user", "content": "Large tool-origin data " * 50000}]
    agent._flush_messages_to_session_db(rows)
    monkeypatch.setattr("agent.relay_llm.complete_logical_call", Mock())
    yield clock, agent, manager, db, rows
    db.close()


def responses(monkeypatch, values):
    calls = []
    iterator = iter(values)
    def build(agent, messages, request_id):
        calls.append(deepcopy(messages))
        def call(_):
            value = next(iterator)
            if isinstance(value, Exception):
                raise value
            return value
        return call
    monkeypatch.setattr(helpers, "_checkpoint_summary_attempt", build)
    return calls


@pytest.mark.parametrize("invalid", ["not-json", "", json.dumps({**GOOD, "extra": "field"}),
    json.dumps({**GOOD, "reason": " "}), json.dumps({**GOOD, "decision": "done"}),
    json.dumps({**GOOD, "decision": ["continue"]}),
    checkpoint.CheckpointError("tool_only_response", repairable=True)])
def test_h11_h12_repair_uses_same_small_packet_and_keeps_original_rows(runtime, monkeypatch, invalid):
    clock, agent, manager, db, rows = runtime
    original = deepcopy(rows)
    calls = responses(monkeypatch, [invalid, "```json\n" + json.dumps(GOOD) + "\n```"])
    text = checkpoint.resolve_milestone(agent, rows, 3)
    assert len(calls) == 2 and all(len(request) == 2 for request in calls)
    assert all(len(json.dumps(request)) < 20000 for request in calls)
    assert all("DO NOT COPY" not in str(request) and "Large tool-origin" not in str(request) for request in calls)
    assert rows[:len(original)] == original
    assert rows[-1]["display_metadata"]["runtime_milestone"]["attempts_used"] == 2
    agent._flush_messages_to_session_db(rows)
    agent._runtime_checkpoint_row = rows[-1]
    assert turn_finalizer._commit_runtime_checkpoint(agent, rows, text, "runtime_budget_handoff", []) == "runtime_checkpoint_decided"
    state = goals.load_goal(agent.session_id)
    assert state.status == "active" and state.runtime_checkpoints[0]["decision"] == "continue"
    assert state.runtime_recovery["checkpoint"]["attempts_used"] == 2
    assert state.runtime_checkpoints[0]["source_message_id"] == rows[-1]["message_uid"]


@pytest.mark.parametrize("mode", ["chat_completions", "codex_responses", "anthropic_messages"])
def test_h13_provider_request_is_toolless_and_tool_only_response_is_typed(monkeypatch, mode):
    received = []
    transport = NS(build_kwargs=lambda **kw: {**kw, "tools": [{"name": "forbidden"}], "tool_choice": "auto"},
                   normalize_response=lambda response, **kw: NS(content="", tool_calls=[{"name": "forbidden"}]))
    agent = NS(api_mode=mode, model="synthetic", provider="synthetic", base_url="", reasoning_config={},
               _is_anthropic_oauth=False, _anthropic_preserve_dots=lambda: False,
               context_compressor=NS(context_length=8192), _get_transport=lambda: transport,
               _build_api_kwargs=lambda rows: {"model": "synthetic", "messages": rows, "tools": ["forbidden"],
                    "tool_choice": "auto", "parallel_tool_calls": True, "max_tokens": 10000},
               _interruptible_api_call=lambda kw: received.append(deepcopy(kw)) or object())
    monkeypatch.setattr(helpers, "sanitize_outbound_kwargs", lambda *args: None)
    monkeypatch.setattr(helpers, "_merge_nous_portal_messages_extra_body", lambda agent, kw: kw)
    monkeypatch.setattr(helpers, "_managed_summary_call", lambda agent, rid, req, cb, **kw: cb(req))
    attempt = helpers._checkpoint_summary_attempt(agent, [{"role": "user", "content": "small packet"}], "synthetic")
    with pytest.raises(checkpoint.CheckpointError, match="tool_only_response"):
        attempt(0)
    assert len(received) == 1
    assert not {"tools", "tool_choice", "parallel_tool_calls"}.intersection(received[0])
    assert agent._runtime_checkpoint_request is False


@pytest.mark.parametrize("mode", ["chat_completions", "codex_responses", "anthropic_messages"])
def test_h13_h15_actual_provider_adapters_emit_small_toolless_wire_request(monkeypatch, mode):
    from agent.transports.chat_completions import ChatCompletionsTransport
    from agent.transports.codex import ResponsesApiTransport
    from agent.transports.anthropic import AnthropicTransport
    transport = {"chat_completions": ChatCompletionsTransport, "codex_responses": ResponsesApiTransport,
                 "anthropic_messages": AnthropicTransport}[mode]()
    received = []
    monkeypatch.setattr(transport, "normalize_response", lambda response, **kw: NS(content=json.dumps(GOOD), tool_calls=[]))
    forbidden = [{"type": "function", "function": {"name": "synthetic_discovery", "description": "Not available here",
                  "parameters": {"type": "object", "properties": {}}}}]
    agent = NS(api_mode=mode, model="synthetic", provider="synthetic", base_url="", reasoning_config={"enabled": False},
               _is_anthropic_oauth=False, _anthropic_preserve_dots=lambda: False,
               context_compressor=NS(context_length=8192), _get_transport=lambda: transport,
               _build_api_kwargs=lambda rows: transport.build_kwargs(model="synthetic", messages=rows,
                    tools=forbidden, max_tokens=8192, reasoning_config={"enabled": False}, request_overrides={}),
               _interruptible_api_call=lambda kw: received.append(deepcopy(kw)) or object())
    monkeypatch.setattr(helpers, "sanitize_outbound_kwargs", lambda *args: None)
    monkeypatch.setattr(helpers, "_merge_nous_portal_messages_extra_body", lambda agent, kw: kw)
    monkeypatch.setattr(helpers, "_managed_summary_call", lambda agent, rid, req, cb, **kw: cb(req))
    rows = [{"role": "system", "content": checkpoint.SYSTEM}, {"role": "user", "content": "Small saved state"}]
    assert helpers._checkpoint_summary_attempt(agent, rows, "synthetic")(0) == json.dumps(GOOD)
    wire = received[0]
    assert not {"tools", "tool_choice", "parallel_tool_calls"}.intersection(wire)
    assert "Small saved state" in json.dumps(wire)
    assert agent._runtime_checkpoint_request_budget["estimated_input_tokens"] > 0
    assert ("instructions" in wire and "input" in wire) if mode == "codex_responses" else "messages" in wire


def test_h15_output_margin_and_cjk_wire_tokens_are_checked_before_provider(runtime, monkeypatch):
    _, agent, _, _, _ = runtime
    agent.context_compressor = NS(context_length=4096)
    small = checkpoint.preflight_request(agent, {"messages": [{"role": "user", "content": "가" * 500}]})
    assert small["estimated_input_tokens"] >= 500 and small["output_tokens"] == 2048
    with pytest.raises(checkpoint.CheckpointError, match="checkpoint_context_overflow"):
        checkpoint.preflight_request(agent, {"messages": [{"role": "user", "content": "가" * 1900}]})


@pytest.mark.parametrize("capacity", [8192, 32000])
def test_h15_anthropic_manual_thinking_keeps_adapter_output_bound(monkeypatch, capacity):
    from agent.transports.anthropic import AnthropicTransport
    transport = AnthropicTransport()
    received = []
    monkeypatch.setattr(transport, "normalize_response", lambda response, **kw: NS(content=json.dumps(GOOD), tool_calls=[]))
    agent = NS(api_mode="anthropic_messages", model="claude-3-7-sonnet-latest", provider="anthropic", base_url="",
               reasoning_config={"enabled": True, "effort": "medium"}, _is_anthropic_oauth=False,
               _anthropic_preserve_dots=lambda: False, context_compressor=NS(context_length=capacity),
               _get_transport=lambda: transport,
               _interruptible_api_call=lambda kw: received.append(deepcopy(kw)) or object())
    monkeypatch.setattr(helpers, "sanitize_outbound_kwargs", lambda *args: None)
    monkeypatch.setattr(helpers, "_merge_nous_portal_messages_extra_body", lambda agent, kw: kw)
    monkeypatch.setattr(helpers, "_managed_summary_call", lambda agent, rid, req, cb, **kw: cb(req))
    rows = [{"role": "user", "content": "Small saved state"}]
    if capacity == 8192:
        with pytest.raises(checkpoint.CheckpointError, match="checkpoint_context_overflow"):
            helpers._checkpoint_summary_attempt(agent, rows, "synthetic")
        assert received == []
    else:
        helpers._checkpoint_summary_attempt(agent, rows, "synthetic")(0)
        assert received[0]["thinking"]["budget_tokens"] < received[0]["max_tokens"]
        assert agent._runtime_checkpoint_request_budget["output_tokens"] == received[0]["max_tokens"]


@pytest.mark.parametrize("change", ["pause", "replacement", "deadline"])
def test_h16_summary_rechecks_native_control_after_relay_wait(runtime, monkeypatch, change):
    clock, agent, manager, _, _ = runtime
    agent._runtime_turn_identity = agent._runtime_handoff_identity
    received = []
    def queued(request, dispatch, **kwargs):
        if change == "pause":
            manager.pause(reason="user stop while queued")
        elif change == "replacement":
            manager.set("A different investigation")
        else:
            clock.now = 1000. + 86401.
        return dispatch(request)
    monkeypatch.setattr("agent.relay_llm.execute_current", queued)
    with pytest.raises(goals_completion.NativeAdmissionDenied):
        helpers._managed_summary_call(agent, "summary", {"messages": []},
                                      lambda request: received.append(request), retry_count=0)
    assert received == []


def test_h16_summary_operation_scope_is_restored_after_call(monkeypatch):
    import time
    from agent.deadline import native_operation_deadline, native_operation_scope
    agent = NS(_native_operation_deadline=time.time() + 30.)
    monkeypatch.setattr("agent.relay_llm.execute_current", lambda request, dispatch, **kwargs: dispatch(request))
    with native_operation_scope(time.time() + 60.) as parent:
        seen = helpers._managed_summary_call(agent, "summary", {}, lambda request: native_operation_deadline(), retry_count=0)
        assert seen == agent._native_operation_deadline
        assert native_operation_deadline() == parent
    assert native_operation_deadline() is None


def test_h17_changed_work_basis_leaves_response_unapplied_without_repair(runtime, monkeypatch):
    _, agent, _, _, rows = runtime
    calls = []
    def attempt(agent, messages, request_id):
        def complete(_):
            calls.append(request_id)
            monkeypatch.setattr(goals_completion, "read_work_projection", lambda sid, state: {
                "status": "known", "digest": "canonical-r8", "checkpoint_context": {"answer": "Changed observation"}})
            return json.dumps(GOOD)
        return complete
    monkeypatch.setattr(helpers, "_checkpoint_summary_attempt", attempt)
    text, reason, interrupted = turn_finalizer._resolve_runtime_checkpoint(
        agent, None, "runtime_budget_handoff", False, False, rows, 1)
    assert reason == "runtime_budget_handoff" and not interrupted
    assert len(calls) == 1 and agent._runtime_checkpoint_degraded
    assert agent._runtime_milestone_decision is None
    assert rows[-2]["display_metadata"]["runtime_checkpoint_attempt"]["code"] == "stale_checkpoint_basis"
    assert not any("runtime_milestone" in row.get("display_metadata", {}) for row in rows)
    assert rows[-1]["content"] == text


@pytest.mark.parametrize("control", ["storage", "pause", "replace", "cas"])
def test_h16_h17_checkpoint_never_grants_discovery_after_storage_or_control_failure(runtime, monkeypatch, control):
    _, agent, manager, db, rows = runtime
    responses(monkeypatch, [json.dumps(GOOD)])
    text = checkpoint.resolve_milestone(agent, rows, 1)
    agent._runtime_checkpoint_row = rows[-1]
    if control != "storage":
        agent._flush_messages_to_session_db(rows)
    if control == "pause":
        manager.pause(reason="user stop")
    elif control == "replace":
        manager.set("New explicit scope")
    elif control == "cas":
        monkeypatch.setattr(db, "compare_and_swap_meta", lambda *args: False)
    result = turn_finalizer._commit_runtime_checkpoint(agent, rows, text, "runtime_budget_handoff",
                                                       ["persist_session: failed"] if control == "storage" else [])
    assert result != "runtime_checkpoint_decided"
    if control == "cas":
        assert result == "runtime_budget_checkpoint_failed"
    assert goals.load_goal(agent.session_id).runtime_checkpoints == []


def test_h18_source_saved_before_crash_recovers_same_uid_without_model(runtime, monkeypatch):
    clock, agent, _, db, rows = runtime
    calls = responses(monkeypatch, [json.dumps(GOOD)])
    checkpoint.resolve_milestone(agent, rows, 1)
    agent._flush_messages_to_session_db(rows)
    uid = rows[-1]["message_uid"]
    goals_runtime.recover_runtime_milestones(db, agent.session_id)
    goals_runtime.recover_runtime_milestones(db, agent.session_id)
    state = goals.load_goal(agent.session_id)
    assert len(calls) == 1 and len(state.runtime_checkpoints) == 1
    assert state.runtime_checkpoints[0]["source_message_id"] == uid
    clock.now = 1000 + 18 * 3600
    next_claim = goals_completion.claim_recovery(db, agent.session_id, "checkpoint",
        expected_identity=agent._runtime_handoff_identity, threshold=64800, deadline=clock.now + 300)
    assert next_claim["allowed"] and next_claim["total_attempts"] == 2


def test_h22_exhausted_format_repair_is_native_closing_not_model_finish(runtime, monkeypatch):
    _, agent, _, db, rows = runtime
    calls = responses(monkeypatch, ["invalid", "still invalid"])
    text, reason, interrupted = turn_finalizer._resolve_runtime_checkpoint(
        agent, None, "runtime_budget_handoff", False, False, rows, 1)
    assert not interrupted and len(calls) == 2
    agent._flush_messages_to_session_db(rows)
    agent._runtime_checkpoint_row = rows[-1]
    result = turn_finalizer._commit_runtime_checkpoint(agent, rows, text, reason, [])
    state = goals.load_goal(agent.session_id)
    assert result == "runtime_checkpoint_closing_due"
    assert state.status == "active" and state.runtime_checkpoints == []
    assert state.runtime_control["reason"] == "checkpoint_decision_unavailable"
    assert state.runtime_control["decision_origin"] == "native"


def test_h21_episode_exhaustion_survives_reconstruction_without_new_request(runtime, monkeypatch):
    _, agent, _, db, rows = runtime
    calls = responses(monkeypatch, ["invalid", "invalid"])
    with pytest.raises(checkpoint.CheckpointError, match="checkpoint_decision_unavailable"):
        checkpoint.resolve_milestone(agent, rows, 1)
    before = deepcopy(goals.load_goal(agent.session_id).runtime_recovery)
    with pytest.raises(checkpoint.CheckpointError, match="recovery_episode_exhausted"):
        checkpoint.resolve_milestone(agent, rows, 1)
    assert len(calls) == 2 and goals.load_goal(agent.session_id).runtime_recovery == before


def test_h20_overdue_milestones_share_one_episode_deadline(runtime, monkeypatch):
    clock, agent, _, db, rows = runtime
    clock.now = 1000 + 22 * 3600
    calls = responses(monkeypatch, [json.dumps(GOOD), json.dumps(GOOD)])
    text = checkpoint.resolve_milestone(agent, rows, 1)
    agent._flush_messages_to_session_db(rows)
    agent._runtime_checkpoint_row = rows[-1]
    turn_finalizer._commit_runtime_checkpoint(agent, rows, text, "runtime_budget_handoff", [])
    before = deepcopy(goals.load_goal(agent.session_id).runtime_recovery["checkpoint"])
    agent._runtime_milestone_threshold = 64800
    checkpoint.resolve_milestone(agent, rows, 1)
    after = goals.load_goal(agent.session_id).runtime_recovery["checkpoint"]
    assert after["deadline"] == before["deadline"] and after["episode_id"] == before["episode_id"]
    assert after["attempts_used"] == 2 and len(calls) == 2


def test_h21_repair_cannot_mint_time_after_crossing_later_threshold(runtime, monkeypatch):
    clock, agent, _, db, rows = runtime
    calls = []
    def build(*args):
        def attempt(_):
            calls.append(1)
            clock.now = 1000 + 18.1 * 3600
            return "invalid"
        return attempt
    monkeypatch.setattr(helpers, "_checkpoint_summary_attempt", build)
    with pytest.raises(checkpoint.CheckpointError, match="recovery_episode_exhausted"):
        checkpoint.resolve_milestone(agent, rows, 1)
    rec = goals.load_goal(agent.session_id).runtime_recovery["checkpoint"]
    assert len(calls) == 1 and rec["threshold"] == 43200 and rec["attempts_used"] == 1


def test_unknown_timeout_delivery_is_not_blindly_replayed(runtime, monkeypatch):
    _, agent, _, _, rows = runtime
    agent._auto_recovery_cycles, agent._auto_recovery_budget_seconds = 2, 120
    calls = responses(monkeypatch, [TimeoutError("synthetic unconfirmed delivery"), json.dumps(GOOD)])
    with pytest.raises(checkpoint.CheckpointError, match="provider_timeout"):
        checkpoint.resolve_milestone(agent, rows, 1)
    assert len(calls) == 1


def test_confirmed_transient_rejection_uses_existing_native_recovery_budget(runtime, monkeypatch):
    clock, agent, _, db, rows = runtime
    agent._auto_recovery_cycles, agent._auto_recovery_budget_seconds = 2, 90
    agent._auto_recovery_request_timeout_seconds = 30
    error = RuntimeError("synthetic service unavailable")
    error.status_code = 503
    calls = responses(monkeypatch, [error, json.dumps(GOOD)])
    checkpoint.resolve_milestone(agent, rows, 1)
    rec = goals.load_goal(agent.session_id).runtime_recovery["checkpoint"]
    assert len(calls) == 2 and rec["deadline"] == clock.now + 90


def test_long_response_crossing_ready_deadline_settles_before_overdue_checkpoint(runtime, monkeypatch):
    from agent import conversation_loop
    clock, agent, manager, db, rows = runtime
    manager.state.runtime_policy = goals_completion.make_policy(86400.)
    db.set_meta("goal:" + agent.session_id, manager.state.to_json())
    agent._runtime_turn_identity = agent._runtime_handoff_identity
    agent._runtime_turn_policy_enabled = True
    agent._runtime_settlement_due = False
    # No milestone has a receipt. The response began before D_ready, while the
    # original 12h checkpoint was overdue, then returned inside settlement.
    ready = 1000. + 86400. - manager.state.runtime_policy["settlement_seconds"]
    clock.now = ready - 1.
    assert goals_runtime.runtime_checkpoint_due(goals.load_goal(agent.session_id)) == 43200
    assert conversation_loop._run_budget_phase(agent) == "checkpoint"
    clock.now = ready + 1.
    assert conversation_loop._run_budget_phase(agent) == "settlement"
    original = deepcopy(rows)
    forbidden = Mock(side_effect=AssertionError("No new checkpoint provider after D_ready"))
    monkeypatch.setattr(turn_finalizer, "_resolve_runtime_milestone", forbidden)
    agent._handle_max_iterations = forbidden
    text, reason, interrupted = turn_finalizer._resolve_runtime_checkpoint(
        agent, "Already returned observations", "text_response(finish_reason=stop)", False, False, rows, 1)
    forbidden.assert_not_called()
    assert reason == "runtime_settlement_preserved" and not interrupted
    state = goals.load_goal(agent.session_id)
    assert state.status == "paused" and state.paused_reason == "report_target_settlement"
    assert state.runtime_settlement["artifact_status"] == "partial_checkpoint"
    assert not state.runtime_checkpoints and not state.runtime_recovery.get("checkpoint")
    assert rows[:len(original)] == original
    assert rows[-1]["content"] == text
    assert rows[-1]["display_metadata"]["runtime_settlement"]["status"] == "preserved"


@pytest.mark.parametrize("control", ["replacement", "cleared", "manual_pause"])
def test_late_response_keeps_original_goal_identity_and_respects_new_control(runtime, monkeypatch, control):
    clock, agent, manager, db, rows = runtime
    agent._runtime_turn_identity = agent._runtime_handoff_identity
    agent._runtime_turn_policy_enabled = True
    agent._runtime_settlement_due = False
    original_identity = agent._runtime_turn_identity
    clock.now = 2000.
    if control == "replacement":
        manager.set("User replacement goal")
        manager.state.runtime_policy = goals_completion.make_policy(86400.)
        db.set_meta("goal:" + agent.session_id, manager.state.to_json())
    elif control == "cleared":
        manager.clear()
    else:
        manager.pause(reason="human stop")
    clock.now = 2000. + 86400. - 1800. + 1.
    before = db.get_meta("goal:" + agent.session_id)
    native_rows = deepcopy(db.get_messages(agent.session_id))
    forbidden = Mock(side_effect=AssertionError("Old turn cannot invoke a model for new user control"))
    monkeypatch.setattr(turn_finalizer, "_resolve_runtime_milestone", forbidden)
    agent._handle_max_iterations = forbidden
    _, reason, interrupted = turn_finalizer._resolve_runtime_checkpoint(
        agent, "Late old-goal answer", "text_response(finish_reason=stop)", False, False, rows, 1)
    forbidden.assert_not_called()
    assert reason == "runtime_budget_handoff_superseded" and not interrupted
    assert db.get_meta("goal:" + agent.session_id) == before
    assert db.get_messages(agent.session_id) == native_rows
    assert agent._runtime_turn_identity == original_identity


@pytest.mark.parametrize("exit_kind", ["normal", "failed", "interrupted", "paused", "replacement", "settlement", "storage"])
def test_v2_finalizer_records_actual_artifacts_without_model_or_control_override(runtime, monkeypatch, exit_kind):
    from agent import conversation_loop
    from agent.iteration_budget import IterationBudget
    clock, agent, manager, db, rows = runtime
    clock.now = 3000.
    manager.state.runtime_policy = goals_completion.make_policy(86400.)
    db.set_meta("goal:" + agent.session_id, manager.state.to_json())
    agent._runtime_turn_identity = agent._runtime_handoff_identity
    agent._runtime_turn_policy_enabled = True
    agent._runtime_milestone_threshold = None
    agent._runtime_settlement_due = exit_kind == "settlement"
    if exit_kind == "paused":
        manager.pause(reason="human stop")
    elif exit_kind == "replacement":
        manager.set("Replacement scope")
    elif exit_kind == "storage":
        monkeypatch.setattr(goals_completion, "settle_native", Mock(side_effect=OSError("synthetic storage failure")))
    forbidden = Mock(side_effect=AssertionError("No provider in native settlement"))
    agent._handle_max_iterations = forbidden
    agent.max_iterations, agent.iteration_budget = 64, IterationBudget(64)
    agent.model, agent.provider, agent.base_url = "synthetic", "synthetic", ""
    agent.context_compressor, agent.request_overrides = NS(last_prompt_tokens=0), {}
    agent._tool_guardrail_halt_decision, agent._interrupt_message = None, None
    agent._skill_nudge_interval, agent._iters_since_skill = 0, 0
    agent._save_trajectory = agent._cleanup_task_resources = Mock()
    agent._sync_external_memory_for_turn = Mock()
    agent._drain_pending_steer = lambda: None
    agent.clear_interrupt, agent._safe_print = Mock(), Mock()
    agent._persist_disabled = True
    for key in turn_finalizer._SESSION_TOKEN_KEYS + turn_finalizer._SESSION_COST_KEYS:
        setattr(agent, "session_" + key, 0)
    monkeypatch.setattr(turn_finalizer, "result_model_fields", lambda agent: {})
    monkeypatch.setattr(turn_finalizer, "_log_turn_exit", lambda *a: None)
    monkeypatch.setattr("hermes_cli.observability.shared_metrics_harness.finish_turn", lambda *a, **kw: None)
    monkeypatch.setattr(conversation_loop, "_notify_context_engine_turn_complete", lambda *a, **kw: None)
    monkeypatch.setattr("agent.turn_context.start_deferred_title_upgrade", lambda *a: None)
    monkeypatch.setattr(turn_finalizer, "_micro_compact_after_turn", lambda *a: None)
    monkeypatch.setattr(turn_finalizer, "apply_llm_output_transform", lambda agent, text, **kw: (text, False, None))
    monkeypatch.setattr(turn_finalizer, "_append_file_mutation_footer", lambda agent, text, logger: text)
    monkeypatch.setattr(turn_finalizer, "_explain_abnormal_exit", lambda agent, text, *a: text)
    monkeypatch.setattr(turn_finalizer, "_apply_output_hooks", lambda agent, text, *a, **kw: (text, False, None))
    def persist(messages, history):
        agent._persist_disabled = False
        try:
            agent._flush_messages_to_session_db(messages, history)
        finally:
            agent._persist_disabled = True
    agent._persist_session = persist
    result = turn_finalizer.finalize_turn(agent, final_response="Partial observations preserved.", api_call_count=1,
        interrupted=exit_kind in {"interrupted", "paused"}, failed=exit_kind == "failed",
        messages=rows, conversation_history=None, effective_task_id="synthetic", turn_id="turn",
        user_message="Inspect", original_user_message="Inspect", _should_review_memory=False,
        _turn_exit_reason="text_response(finish_reason=stop)")
    forbidden.assert_not_called()
    state = goals.load_goal(agent.session_id)
    receipt = result["runtime_settlement"]
    if exit_kind == "replacement":
        assert receipt["status"] == "superseded" and not state.runtime_settlement
        assert state.goal == "Replacement scope"
    elif exit_kind == "storage":
        assert receipt["status"] == "settlement_failed" and result["failed"] and not result["completed"]
    else:
        assert receipt["status"] == "preserved" and receipt["artifact_status"] == "partial_checkpoint"
        assert receipt["delivery_status"] == "unconfirmed"
        if exit_kind == "paused":
            assert state.paused_reason == "human stop"
        if exit_kind == "settlement":
            assert state.status == "paused" and state.paused_reason == "report_target_settlement"
            assert result["turn_exit_reason"] == "runtime_settlement_preserved" and not result["completed"]
            agent._sync_external_memory_for_turn.assert_not_called()
