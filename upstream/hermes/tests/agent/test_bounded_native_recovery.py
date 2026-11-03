"""Post-failure recovery is bounded without rewriting the cached request or replaying tools."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from agent.error_classifier import FailoverReason
from agent.turn_retry_state import TurnRetryState
from agent import turn_recovery_autorecover as recovery


def agent_for_test():
    return NS(_auto_recovery_cycles=2, _auto_recovery_budget_seconds=600,
              _auto_recovery_request_timeout_seconds=180, _current_streamed_assistant_text="",
              _has_content_after_think_block=bool, platform="test", log_prefix="",
              _emit_diagnostic_status=Mock(), _emit_diagnostic_wait=Mock(),
              _client_log_context=lambda: "synthetic", _interrupt_requested=False,
              _touch_activity=Mock(), _vprint=Mock(), _persist_session=Mock(),
              _drop_trailing_empty_response_scaffolding=Mock(),
              clear_interrupt=lambda **kw: False)


@pytest.mark.parametrize("outcome", ["success", "persistent_failure", "cycle_exhaustion", "user_stop", "delivered"])
def test_native_ladder_preserves_progress_and_bounds_recovery(monkeypatch, outcome):
    from agent import turn_recovery, turn_api_error
    from agent.turn_api_error import settle_unrecovered_error
    from agent.turn_api_call import perform_api_call

    clock = NS(now=100.0)
    monkeypatch.setattr(recovery, "time", NS(monotonic=lambda: clock.now))
    monkeypatch.setattr(turn_recovery, "time", NS(time=lambda: clock.now,
                        sleep=lambda seconds: setattr(clock, "now", clock.now + seconds)))
    monkeypatch.setattr(recovery, "ladder_wait_seconds", lambda *args: 15.0)
    # Terminal rendering is orthogonal; preserve the native routing/loop decisions.
    monkeypatch.setattr(turn_api_error, "max_retries_exhausted_result", lambda *a, **k: {"completed": False})
    monkeypatch.setattr(turn_api_error, "settle_delivered_partial", lambda *a, **k: "")
    agent = agent_for_test()
    agent._try_recover_primary_transport = Mock(return_value=True)
    agent._has_pending_fallback = lambda: False
    agent._try_activate_fallback = Mock(return_value=False)
    retry = TurnRetryState()
    with recovery.recovery_request_scope(agent, retry):
        assert recovery.cap_recovery_timeout(agent, 1800) == 1800
    assert retry.recovery_deadline is None  # healthy initial inference keeps its patience
    rows = [{"role": "user", "content": "Continue"},
            {"role": "assistant", "tool_calls": [{"id": "already-done"}]},
            {"role": "tool", "tool_call_id": "already-done", "content": "retained result"}]
    original = deepcopy(rows)
    classified = NS(reason=FailoverReason.timeout, retryable=True, should_fallback=True)
    error = TimeoutError("synthetic timeout")
    arguments = dict(api_error=error, classified=classified, _retry=retry, status_code=None,
        error_msg="synthetic timeout", is_context_length_error=False, is_rate_limited=False,
        _is_zai_coding_overload=False, _provider="custom", _base="https://synthetic.invalid/v1",
        _model="synthetic", messages=rows, api_messages=rows, api_kwargs={},
        active_system_prompt="stable-prefix", conversation_history=rows, approx_tokens=100,
        retry_count=1, max_retries=1, compression_attempts=0, api_call_count=1)

    # The first failure arms a single shared deadline before primary transport repair.
    verdict = settle_unrecovered_error(agent, **arguments)
    assert verdict.action == "continue" and retry.primary_recovery_attempted
    deadline = retry.recovery_deadline
    assert deadline == clock.now + 600
    with recovery.recovery_request_scope(agent, retry):
        assert recovery.cap_recovery_timeout(agent, 1800) == 180
        clock.now += 30
        assert recovery.cap_recovery_timeout(agent, 1800) == 150
    assert agent._native_recovery_request_deadline is None

    if outcome == "persistent_failure":
        clock.now = deadline
        verdict = settle_unrecovered_error(agent, **arguments)
        assert verdict.action == "return" and verdict.result["recovery_exhausted"] == "time_budget"
        assert agent._try_recover_primary_transport.call_count == 1
        assert not agent._try_activate_fallback.called
        with pytest.raises(TimeoutError, match="allowance exhausted"):
            with recovery.recovery_request_scope(agent, retry):
                pytest.fail("expired recovery must not dispatch")
    elif outcome == "cycle_exhaustion":
        for expected_cycle in (1, 2):
            verdict = settle_unrecovered_error(agent, **arguments)
            assert verdict.action == "continue" and retry.auto_recovery_cycles_used == expected_cycle
            assert retry.recovery_deadline == deadline
        verdict = settle_unrecovered_error(agent, **arguments)
        assert verdict.action == "return" and retry.auto_recovery_cycles_used == 2
        assert recovery.recovery_remaining(retry) > 0  # cycles also bound fast repeated failures
        assert agent._try_recover_primary_transport.call_count == 1
    elif outcome == "delivered":
        agent._current_streamed_assistant_text = "Already delivered"
        assert recovery.auto_recover_after_exhaustion(agent, error, classified, retry,
            messages=rows, conversation_history=rows, api_call_count=1) is None
    else:
        agent._interrupt_requested = outcome == "user_stop"
        verdict = settle_unrecovered_error(agent, **arguments)
        if outcome == "user_stop":
            assert verdict.action == "return" and verdict.result["interrupted"]
        else:
            assert verdict.action == "continue" and retry.auto_recovery_cycles_used == 1
            assert retry.recovery_deadline == deadline  # backoff never renews it
            calls = []
            agent.api_mode, agent.provider, agent.model = "chat_completions", "custom", "synthetic"
            agent.base_url, agent.session_id = "https://synthetic.invalid/v1", "synthetic"
            agent._has_stream_consumers = lambda: True
            agent._has_pending_redirect = lambda: False
            def complete(payload, **kwargs):
                calls.append(deepcopy(payload))
                assert recovery.cap_recovery_timeout(agent, 1800) <= 180
                return NS(choices=[NS(message=NS(content="Complete", tool_calls=[]))])
            agent._interruptible_streaming_api_call = complete
            payload = {"model": "synthetic", "messages": [{"role": "system", "content": "stable-prefix"}, *rows]}
            result = perform_api_call(agent, api_kwargs=payload, _original_api_kwargs=deepcopy(payload),
                _llm_middleware_trace=[], _moa_prepared_request=None, _retry=retry, thinking_spinner=None,
                retry_count=0, api_call_count=1, api_request_id="request", effective_task_id="task",
                turn_id="turn", interrupted=False)
            assert result.action == "fallthrough" and len(calls) == 1
            assert calls[0] == payload and agent._native_recovery_request_deadline is None
    # Cancellation may append its terminal explanation, but cannot mutate or replay
    # the already completed tool call/result in the retained prefix.
    assert rows[:len(original)] == original
    assert len([row for row in rows if row.get("role") == "tool"]) == 1


def test_profile_config_and_transport_watchdogs_share_the_recovery_deadline(tmp_path, monkeypatch):
    from hermes_cli.config_effective import load_user_config_effective
    from agent.agent_init import _apply_agent_section
    from agent import chat_completion_helpers as helpers
    from agent.chat_completion_stream_monitor import StreamingWaitMonitor

    paths = [tmp_path / name / "config.yaml" for name in ("a", "b")]
    for path, seconds in zip(paths, (120, 240)):
        path.parent.mkdir()
        path.write_text(f"agent:\n  environment_probe: false\n  auto_recovery_budget_seconds: {seconds}\n  auto_recovery_request_timeout_seconds: 30\n")
    agent = agent_for_test()
    agent.run_budget_seconds = None
    for path, expected in ((paths[0], 120), (paths[1], 240), (paths[0], 120)):
        _apply_agent_section(agent, load_user_config_effective(path))
        assert agent._auto_recovery_budget_seconds == expected
    clock = NS(now=100.0)
    monkeypatch.setattr(recovery, "time", NS(monotonic=lambda: clock.now))
    agent._compute_non_stream_stale_timeout = lambda _: 1800.0
    agent.api_mode, agent.provider, agent.model = "chat_completions", "custom", "synthetic"
    agent.base_url = "https://synthetic.invalid/v1"
    monkeypatch.setattr(helpers, "get_provider_stale_timeout", lambda *a: 1800.0)
    monkeypatch.setattr(helpers, "get_provider_request_timeout", lambda *a: 1800.0)
    retry = TurnRetryState(recovery_deadline=200.0)
    with recovery.recovery_request_scope(agent, retry):
        call = helpers._StreamingCall(agent, {"model": "synthetic", "messages": []}, None)
        call._resolve_stale_timeout()
        assert call._stream_stale_timeout == 30
        assert max(call._stream_timeouts()) == 30
        assert helpers._resolve_direct_stale_timeout(agent, {}) == 30
        assert helpers._resolve_nonstream_watchdogs(agent, {}).stale_timeout == 30
        assert helpers._derive_stream_stale_timeout(agent, {}) == 30
        # Recent stream activity cannot extend an absolute recovery request deadline.
        clock.now += 31
        call._call_done = NS(is_set=lambda: False, wait=lambda **kw: None)
        call.last_chunk_time = {"t": helpers.time.time()}
        call._kill_stale_stream = lambda elapsed: (_ for _ in ()).throw(TimeoutError("deadline killed"))
        with pytest.raises(TimeoutError, match="deadline killed"):
            StreamingWaitMonitor._monitor_loop(call)
        # Finish the scope before expiry for this watchdog-only assertion.
        clock.now -= 31
    assert helpers._resolve_direct_stale_timeout(agent, {}) == 1800


def test_deadline_kill_unwinds_real_stream_worker_and_preserves_partial_text(monkeypatch):
    """The real monitor/kill/registry/parser run; only HTTP and its socket are synthetic."""
    import json
    import threading
    import httpx
    from openai import OpenAI
    from agent import chat_completion_helpers as helpers
    from agent.stream_diag import stream_diag_init, stream_diag_capture_response

    clock = NS(now=100.0)
    monkeypatch.setattr(recovery, "time", NS(monotonic=lambda: clock.now))
    monkeypatch.setattr(helpers, "get_provider_stale_timeout", lambda *args: 1800.0)
    monkeypatch.setattr(helpers, "get_provider_request_timeout", lambda *args: 1800.0)
    agent = agent_for_test()
    agent.api_mode, agent.provider, agent.model = "chat_completions", "custom", "synthetic"
    agent.base_url, agent.session_id = "https://synthetic.invalid/v1", "synthetic"
    agent._native_recovery_request_deadline = 130.0
    agent._current_api_request_id = "bounded-stream"
    agent.run_budget_seconds = None
    agent._stream_diag_init = stream_diag_init
    agent._stream_diag_capture_response = lambda diag, response: stream_diag_capture_response(agent, diag, response)
    for name in ("_emit_wait_notice", "_buffer_diagnostic_status", "_capture_rate_limits", "_capture_credits",
                 "_capture_nous_model_switch", "_check_openrouter_cache_status"):
        setattr(agent, name, Mock())
    agent._is_provider_stream_parse_error = lambda error: False
    agent._fire_stream_delta = lambda text: setattr(agent, "_current_streamed_assistant_text",
                                                    agent._current_streamed_assistant_text + text)
    shutdown, body_closed = threading.Event(), threading.Event()
    events, requests = [], []

    class Socket:
        def settimeout(self, value):
            events.append(("settimeout", threading.get_ident()))

        def shutdown(self, how):
            events.append(("socket_shutdown", threading.get_ident()))
            shutdown.set()

    def chunk(text):
        return ("data: " + json.dumps({"id": "synthetic", "object": "chat.completion.chunk", "created": 0,
            "model": "synthetic", "choices": [{"index": 0, "delta": {"role": "assistant", "content": text},
                                                 "finish_reason": None}]}) + "\n\n").encode()

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield chunk("Preserved partial finding")
            clock.now = 131.0  # fresh text cannot renew the absolute request deadline
            assert shutdown.wait(3), "native monitor did not shut down the request socket"
            yield chunk("LATE text must be fenced")
            raise httpx.ReadError("synthetic reader woke after shutdown")

        def close(self):
            events.append(("body_close", threading.get_ident()))
            body_closed.set()

    def handle(request):
        requests.append(request)
        events.append(("dispatch", threading.get_ident()))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body(),
                              extensions={"network_stream": NS(_sock=Socket())})

    client = OpenAI(api_key="synthetic", base_url=agent.base_url,
                    http_client=httpx.Client(transport=httpx.MockTransport(handle)), max_retries=0)
    agent._create_request_openai_client = lambda **kwargs: client
    # Pool abort deliberately cannot see a checked-out socket. The real explicit
    # shutdown path must wake the owner, which alone releases the response/client.
    agent._abort_request_openai_client = lambda value, **kwargs: events.append(("pool_abort", threading.get_ident()))
    def close_client(value, **kwargs):
        events.append(("client_close", threading.get_ident()))
        value.close()
    agent._close_request_openai_client = close_client
    call = helpers._StreamingCall(agent, {"model": "synthetic", "messages": [{"role": "user", "content": "Continue"}]}, None)
    try:
        result = call.run()
        call.worker.join(1)
        assert not call.worker.is_alive() and call._call_done.is_set()
        assert shutdown.is_set() and body_closed.is_set() and client.is_closed()
        assert call.clients.client is None and not call.managed_stream_holder
        assert len(requests) == 1  # no reconnect or second provider dispatch after expiry
        assert result.choices[0].message.content == "Preserved partial finding"
        assert not result.choices[0].message.tool_calls
        assert agent._current_streamed_assistant_text == "Preserved partial finding"
        worker_tid = next(tid for action, tid in events if action == "dispatch")
        assert all(tid == worker_tid for action, tid in events if action in {"body_close", "client_close"})
        assert all(tid != worker_tid for action, tid in events if action == "socket_shutdown")
        assert agent._last_stream_diagnostics["ended_at"] is not None
    finally:
        shutdown.set()
        if call.worker is not None:
            call.worker.join(3)
        client.close()
