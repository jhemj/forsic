"""Offline HTTP/SDK boundaries distinguish header waits from silent open streams."""

import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import APIConnectionError, OpenAI

from agent.api_request_hooks import ApiRequestHooksMixin
from agent.chat_completion_helpers import _StreamingCall
from agent.chat_completion_wait_notice import WaitNoticeState
from agent.stream_diag import stream_diag_capture_response, stream_diag_init


class Agent(ApiRequestHooksMixin, SimpleNamespace):
    pass


@pytest.fixture
def agent():
    value = Agent(
        model="offline", provider="custom", api_mode="chat_completions",
        base_url="https://synthetic.invalid/v1", session_id="synthetic",
        platform="cli", _current_api_request_id="request-1", _interrupt_requested=False,
        _stream_diag_init=stream_diag_init, _touch_activity=Mock(),
        _emit_wait_notice=Mock(), _buffer_diagnostic_status=Mock(), _emit_diagnostic_wait=Mock(),
        _capture_rate_limits=Mock(), _capture_credits=Mock(), _capture_nous_model_switch=Mock(),
        _check_openrouter_cache_status=Mock(), _abort_request_openai_client=Mock(),
        _close_request_openai_client=Mock(), _is_provider_stream_parse_error=lambda _: False,
        _api_request_payload_for_hook=lambda _: {},
    )
    value._stream_diag_capture_response = lambda diag, response: stream_diag_capture_response(value, diag, response)
    return value


def call_for(agent):
    call = _StreamingCall(agent, {"model": "offline", "messages": [{"role": "user", "content": "synthetic input"}]}, None)
    call._stream_stale_timeout = 1800.0
    call._start_stream_attempt()
    call._new_diag()
    call._mon = SimpleNamespace(wait_notice=WaitNoticeState(), last_heartbeat=time.time())
    call._monitor_interrupted = {"yes": False}
    return call


def test_transport_phase_and_timings_follow_http_boundaries_without_changing_patience(agent, monkeypatch, caplog):
    from agent import chat_completion_helpers

    agent.base_url = "http://127.0.0.1:11434/v1"
    monkeypatch.setattr(chat_completion_helpers, "get_provider_stale_timeout", lambda *args: 1800.0)
    monkeypatch.setattr(chat_completion_helpers, "get_provider_request_timeout", lambda *args: 1800.0)
    release_headers, header_requested, headers_open = threading.Event(), threading.Event(), threading.Event()
    release_chunk, chunk_requested = threading.Event(), threading.Event()
    errors, requests = [], []

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            chunk_requested.set()
            assert release_chunk.wait(3), "test did not release the synthetic stream"
            payload = {"id": "synthetic", "object": "chat.completion.chunk", "created": 0,
                       "model": "offline", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}]}
            yield ("data: " + json.dumps(payload) + "\n\n").encode()

    def handle(request):
        requests.append(request)
        header_requested.set()
        assert release_headers.wait(3), "test did not release synthetic response headers"
        return httpx.Response(200, headers={"content-type": "text/event-stream", "x-request-id": "private-provider-id"}, stream=Body())

    client = OpenAI(api_key="synthetic", base_url=agent.base_url,
                    http_client=httpx.Client(transport=httpx.MockTransport(handle)), max_retries=0)
    agent._create_request_openai_client = lambda **_: client
    call = call_for(agent)
    call._resolve_stale_timeout()
    assert call._stream_stale_timeout == 1800
    assert call._stream_timeouts() == (1800, 1800, 60)
    caplog.set_level("INFO", logger="agent.chat_completion_helpers")

    def consume():
        raw = None
        try:
            raw = call._open_chat_stream({**call.api_kwargs, "stream": True,
                                         "timeout": httpx.Timeout(1800, connect=30, pool=30)})
            call._chat_stream_created(raw)
            headers_open.set()
            call._count_chunk(call.clients.diag, next(iter(raw)))
        except BaseException as error:
            errors.append(error)
        finally:
            if raw is not None:
                raw.close()

    worker = threading.Thread(target=consume)
    worker.start()
    try:
        assert header_requested.wait(3)
        call._heartbeat(60)
        assert "response headers" in agent._emit_wait_notice.call_args.args[0]
        assert agent._last_stream_diagnostics["phase"] == "before_headers"
        assert agent._last_stream_diagnostics["http_status"] is None

        release_headers.set()
        assert headers_open.wait(3) and chunk_requested.wait(3)
        call._heartbeat(60)
        assert "first stream chunk" in agent._emit_wait_notice.call_args.args[0]
        before_chunk = dict(agent._last_stream_diagnostics)
        assert before_chunk["phase"] == "first_chunk"
        assert before_chunk["http_status"] == 200
        assert before_chunk["first_chunk_at"] is None

        release_chunk.set()
        worker.join(3)
        assert not worker.is_alive() and not errors
        call._heartbeat(60)
        assert "without stream output" in agent._emit_wait_notice.call_args.args[0]
        call._publish_stream_diagnostics(termination_cause="completed", ended=True)
        result = agent._last_stream_diagnostics
        assert result["phase"] == "post_chunk" and result["termination_cause"] == "completed"
        assert result["started_at"] <= result["headers_received_at"] <= result["first_chunk_at"] <= result["ended_at"]
        assert result["headers_wait_seconds"] >= 0 and result["first_chunk_wait_seconds"] >= 0
        assert "private-provider-id" not in json.dumps(result) + caplog.text
        assert json.loads(requests[0].content)["messages"] == call.api_kwargs["messages"]
        assert requests[0].extensions["timeout"]["read"] == 1800
        assert all("watchdog in 1740s" in entry.args[0] for entry in agent._emit_wait_notice.call_args_list)
    finally:
        release_headers.set()
        release_chunk.set()
        worker.join(3)
        client.close()


def interrupt_through_native_retry_loop(agent, call, monkeypatch):
    from dataclasses import dataclass
    from agent import conversation_loop

    agent.thinking_callback = None
    agent._has_pending_redirect = lambda: False
    agent._vprint = Mock()
    agent.log_prefix = ""
    agent._drop_trailing_empty_response_scaffolding = Mock()
    agent._strip_think_blocks = lambda value: value
    agent._persist_session = Mock()

    @dataclass
    class Continue:
        action: str = "fallthrough"

    def interrupt(agent):
        raise InterruptedError("synthetic interrupted socket")

    monkeypatch.setattr(conversation_loop, "nous_rate_limit_guard", lambda agent: Continue())
    monkeypatch.setattr(conversation_loop, "build_api_request", lambda agent: Continue())
    monkeypatch.setattr(conversation_loop, "perform_api_call", interrupt)
    state = SimpleNamespace(_retry=SimpleNamespace(), thinking_spinner=None,
        messages=[], conversation_history=[], api_start_time=call.clients.diag["started_at"],
        interrupted=False, final_response="", api_request_id="request-1", effective_task_id="task",
        turn_id="turn", api_call_count=1, api_kwargs=call.api_kwargs, retry_count=0, max_retries=3)
    assert conversation_loop._run_api_retry_loop(agent, state) is None
    assert state.interrupted is True and state.retry_count == 0


@pytest.mark.parametrize("cause", ["stale_stream_timeout", "user_interrupt", "provider_error", "completed"])
def test_terminal_receipt_retains_actual_cause_once_and_rejects_abandoned_call_publication(agent, monkeypatch, cause):
    from agent.turn_response_intake import _fire_post_api_request_hook
    from hermes_cli import lifecycle

    seen = []
    monkeypatch.setattr(lifecycle, "has_hook", lambda _: True)
    monkeypatch.setattr(lifecycle, "invoke_hook", lambda name, **kwargs: seen.append((name, kwargs)))
    call = call_for(agent)
    if cause == "completed":
        call._chat_stream_created(SimpleNamespace(response=httpx.Response(200)))
        call._count_chunk(call.clients.diag, SimpleNamespace())
        call._publish_stream_diagnostics(termination_cause="completed", ended=True)
        agent._api_response_payload_for_hook = lambda *args, **kwargs: {}
        _fire_post_api_request_hook(agent, SimpleNamespace(model="offline", usage=None),
            SimpleNamespace(content="ok", tool_calls=[]), "stop", api_messages=[], api_call_count=1,
            api_duration=1, api_start_time=call.clients.diag["started_at"], api_request_id="request-1",
            effective_task_id="task", turn_id="turn")
        assert seen[0][0] == "post_api_request"
        assert seen[0][1]["stream_diagnostics"]["phase"] == "post_chunk"
    else:
        call.clients.set_client(object())
        call._maybe_disable_streaming = Mock()
        if cause == "stale_stream_timeout":
            call._kill_stale_stream(1801)
        elif cause == "user_interrupt":
            agent._interrupt_requested = True
            call._abort_for_interrupt(60)
        error = APIConnectionError(message="Connection error.", request=httpx.Request("POST", agent.base_url))
        assert call._handle_stream_error(error, attempt=2, max_retries=2) is False
        assert agent._last_stream_diagnostics["termination_cause"] == cause
        # A subsequent stop cannot rewrite the watchdog's actual first cause.
        call._publish_stream_diagnostics(termination_cause="user_interrupt", ended=True)
        if cause == "user_interrupt":
            interrupt_through_native_retry_loop(agent, call, monkeypatch)
            assert seen[0][1]["reason"] == "interrupted"
            assert seen[0][1]["retryable"] is False
        else:
            agent._invoke_api_request_error_hook(task_id="task", turn_id="turn", api_request_id="request-1",
                api_call_count=1, api_start_time=call.clients.diag["started_at"], api_kwargs=call.api_kwargs,
                error_type=type(error).__name__, error_message=str(error), reason="connection_error")
        assert seen[0][0] == "api_request_error"
        assert seen[0][1]["stream_diagnostics"]["first_chunk_at"] is None
        assert seen[0][1]["status_code"] is None  # A local stop is not an HTTP 499 response.
    assert len(seen) == 1
    assert seen[0][1]["stream_diagnostics"]["termination_cause"] == cause
    # Outer retry keeps the same api_request_id; an old call must still lose ownership.
    replacement = call_for(agent)
    new_snapshot = agent._last_stream_diagnostics
    call._publish_stream_diagnostics(termination_cause="provider_error", ended=True)
    assert agent._last_stream_diagnostics is new_snapshot
    assert new_snapshot["termination_cause"] is None
    agent._current_api_request_id = "request-2"
    agent._last_stream_diagnostics = None
    replacement._publish_stream_diagnostics(termination_cause="provider_error", ended=True)
    assert agent._last_stream_diagnostics is None
