"""Real SDK consumption under accepted absolute deadlines; no network or model."""
import json
import threading
import time
from unittest.mock import Mock

import httpx
import pytest
from openai import OpenAI

from agent import chat_completion_helpers as h
from agent.deadline import NativeOperationExpired, native_operation_scope
from test_stream_transport_observability import agent as agent_fixture

REQUEST = {"model": "offline", "messages": [{"role": "user", "content": "Synthetic bounded request"}]}
COMPLETE = {"id": "synthetic", "object": "chat.completion", "created": 0, "model": "offline",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}

@pytest.fixture
def a():
    value = agent_fixture.__wrapped__()
    for name in ("_fire_stream_delta", "_fire_reasoning_delta", "_fire_tool_gen_started", "_emit_stream_start", "_emit_stream_end", "_emit_api_thread_status"):
        setattr(value, name, Mock())
    value.stream_delta_callback = None
    value._compute_non_stream_stale_timeout = Mock(return_value=1800.)
    value._client_log_context = lambda: "synthetic"
    return value


class ContinuousBody(httpx.SyncByteStream):
    def __init__(self, count=100, delay=.01, callback=None):
        self.count, self.delay, self.callback = count, delay, callback
        self.closed = threading.Event()
        self.arrivals = []
    def __iter__(self):
        for _ in range(self.count):
            if self.closed.wait(self.delay):
                return
            if self.callback:
                self.callback()
            self.arrivals.append(time.time())
            data = {"id": "synthetic", "object": "chat.completion.chunk", "created": 0, "model": "offline",
                    "choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": None}]}
            yield ("data: " + json.dumps(data) + "\n\n").encode()
        data = {"id": "synthetic", "object": "chat.completion.chunk", "created": 0, "model": "offline",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        yield ("data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n").encode()
    def close(self):
        self.closed.set()


def sdk(a, handler, max_retries=0):
    client = OpenAI(api_key="synthetic", base_url=a.base_url,
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=max_retries)
    a._create_request_openai_client = lambda **_: client
    # Warm the real SDK's lazy transformations outside the measured request.
    client.chat.completions.create(**REQUEST)
    return client


@pytest.mark.parametrize("entry", ["main", "summary", "checkpoint"])
def test_continuously_arriving_stream_stops_at_original_accepted_deadline(a, monkeypatch, entry):
    body = ContinuousBody()
    wires, emitted = [], []
    def handle(request):
        if not json.loads(request.content).get("stream"):
            return httpx.Response(200, json=COMPLETE)
        wires.append(time.time())
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
    client = sdk(a, handle)
    a._fire_stream_delta = lambda *args, **kwargs: emitted.append(time.time())
    a._abort_request_openai_client = lambda *args, **kwargs: body.close()
    start = time.time(); deadline = start + .18
    try:
        with native_operation_scope(deadline), pytest.raises(NativeOperationExpired):
            if entry == "main":
                h.interruptible_streaming_api_call(a, REQUEST)
            else:
                from hermes_cli import goals_completion
                # Admission tests cover grant derivation. This oracle measures the
                # actual managed-summary/checkpoint SDK consumption after admission.
                a._native_operation_deadline = deadline
                if entry == "checkpoint": a._runtime_checkpoint_deadline = deadline
                monkeypatch.setattr(goals_completion, "admit_model_request", lambda *args: None)
                h._managed_summary_call(a, "summary-1", REQUEST,
                    lambda request: h.interruptible_streaming_api_call(a, request), retry_count=0)
        assert time.time() - start < .65
        assert len(wires) == 1 and len(body.arrivals) >= 2
        assert len(body.arrivals) < body.count
        assert all(t < deadline for t in emitted)
        assert not a._interrupt_requested
    finally:
        body.close(); client.close()


@pytest.mark.parametrize("inline", [False, True])
def test_nonstreaming_actual_sdk_call_aborts_request_only(a, inline):
    release = threading.Event(); wires = []
    warming = True
    def handle(request):
        if not warming:
            wires.append(time.time())
            assert release.wait(2), "native cancellation failed to abort the accepted request"
        return httpx.Response(200, json=COMPLETE)
    client = sdk(a, handle); warming = False
    a._abort_request_openai_client = lambda *args, **kwargs: release.set()
    start = time.time()
    try:
        with native_operation_scope(start + .12), pytest.raises(NativeOperationExpired):
            (h.direct_api_call if inline else h.interruptible_api_call)(a, REQUEST)
        assert .09 < time.time() - start < .65
        assert len(wires) == 1 and release.is_set()
        assert not a._interrupt_requested
    finally:
        release.set(); client.close()


def test_sdk_transform_queue_cannot_dispatch_after_expiry(a, monkeypatch):
    wires = []
    client = sdk(a, lambda request: (wires.append(time.time()), httpx.Response(200, json=COMPLETE))[1])
    wires.clear()
    prepare = client._prepare_request
    def queued(request):
        time.sleep(.12)
        return prepare(request)
    monkeypatch.setattr(client, "_prepare_request", queued)
    try:
        with native_operation_scope(time.time() + .05), pytest.raises(NativeOperationExpired):
            h.interruptible_api_call(a, REQUEST)
        assert wires == [], "SDK transformations/queue wait bypassed the accepted dispatch fence"
    finally:
        client.close()


def test_sdk_retry_does_not_renew_original_deadline(a, monkeypatch):
    wires=[]; warming=True
    def handle(request):
        if warming: return httpx.Response(200, json=COMPLETE)
        wires.append(time.time())
        return httpx.Response(500, json={"error": {"message": "synthetic unavailable"}})
    client=sdk(a, handle, max_retries=1);warming=False
    monkeypatch.setattr(client, "_calculate_retry_timeout", lambda *args, **kwargs: .25)
    try:
        with native_operation_scope(time.time()+.1), pytest.raises(NativeOperationExpired):
            h.interruptible_api_call(a, REQUEST)
        time.sleep(.3)  # allow the already-started SDK retry sleep to unwind
        assert len(wires)==1, "retry sent after the original accepted deadline"
        assert not a._interrupt_requested
    finally:
        client.close()


def test_late_timer_from_successful_request_cannot_cancel_next_request(a, monkeypatch):
    from agent import periodic_scheduler
    callbacks=[]; schedule=periodic_scheduler.schedule
    def observe(callback, *args, **kwargs):
        if getattr(callback, "__name__", "") == "expire": callbacks.append(callback)
        return schedule(callback, *args, **kwargs)
    monkeypatch.setattr(periodic_scheduler, "schedule", observe)
    bodies=[]
    def handle(request):
        if not json.loads(request.content).get("stream"):return httpx.Response(200,json=COMPLETE)
        body=ContinuousBody(count=2, delay=.01,
            callback=(callbacks[0] if bodies else None))
        bodies.append(body)
        return httpx.Response(200,headers={"content-type":"text/event-stream"},stream=body)
    client=sdk(a,handle)
    try:
        with native_operation_scope(time.time()+.4):
            assert h.interruptible_streaming_api_call(a,REQUEST)
        assert client._client.event_hooks["request"]==[]
        time.sleep(.42)
        a._current_api_request_id="request-2"
        with native_operation_scope(time.time()+.5):
            assert h.interruptible_streaming_api_call(a,REQUEST)
        assert len(bodies)==2 and len(bodies[1].arrivals)==2
        assert not a._abort_request_openai_client.called and not a._interrupt_requested
        assert client._client.event_hooks["request"]==[]
    finally:client.close()


@pytest.mark.parametrize("stream", [False, True])
def test_auxiliary_actual_sdk_wait_obeys_inherited_deadline_without_closing_shared_client(a, stream):
    from agent import auxiliary_client as aux
    release=threading.Event();body=ContinuousBody();wires=[];warming=True
    def handle(request):
        if warming:return httpx.Response(200,json=COMPLETE)
        wires.append(time.time())
        if json.loads(request.content).get("stream"):
            return httpx.Response(200,headers={"content-type":"text/event-stream"},stream=body)
        release.wait(1)
        return httpx.Response(200,json=COMPLETE)
    client=sdk(a,handle);warming=False
    start=time.time()
    try:
        with native_operation_scope(start+.16), pytest.raises(aux.AuxiliaryExplicitCancellation):
            aux._relay_sync_completion(client,REQUEST,
                create=lambda request: aux._create_with_progress(client,request,force_stream=stream))
        assert .12 < time.time()-start < .5
        assert len(wires)==1 and not client.is_closed()
        if stream:
            assert len(body.arrivals)>=2 and len(body.arrivals)<body.count
        assert not a._interrupt_requested
    finally:
        release.set();body.close();time.sleep(.04);client.close()


@pytest.mark.parametrize("stream", [False, True])
def test_async_auxiliary_sdk_consumption_obeys_original_native_deadline(stream):
    import asyncio
    from openai import AsyncOpenAI
    from agent import auxiliary_client as aux
    async def scenario():
        arrivals=[];wires=[];closed=asyncio.Event();warming=True
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                for _ in range(100):
                    await asyncio.sleep(.01);arrivals.append(time.time())
                    yield b'data: {"id":"s","object":"chat.completion.chunk","created":0,"model":"offline","choices":[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}\n\n'
            async def aclose(self):closed.set()
        async def handle(request):
            if warming:return httpx.Response(200,json=COMPLETE)
            wires.append(time.time())
            if stream:return httpx.Response(200,headers={"content-type":"text/event-stream"},stream=Body())
            await asyncio.sleep(2)
            return httpx.Response(200,json=COMPLETE)
        client=AsyncOpenAI(api_key='synthetic',base_url='https://synthetic.invalid/v1',
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),max_retries=0)
        await client.chat.completions.create(**REQUEST);warming=False
        start=time.time()
        try:
            with native_operation_scope(start+.15),pytest.raises(aux.AuxiliaryExplicitCancellation):
                await aux._relay_async_completion(client,REQUEST,
                    create=lambda request:aux._acreate_with_progress(client,request,force_stream=stream))
            assert .11<time.time()-start<.5 and len(wires)==1
            await asyncio.sleep(.03)
            if stream:assert closed.is_set() and 2<=len(arrivals)<100
            assert not client.is_closed() and client._client.event_hooks['request']==[]
        finally:await client.close()
    asyncio.run(scenario())


def test_shared_async_aux_client_keeps_other_goal_alive_after_first_deadline():
    import asyncio
    from openai import AsyncOpenAI
    from agent import auxiliary_client as aux
    async def scenario():
        warming=True;wires=[]
        async def handle(request):
            if not warming:
                wires.append(json.loads(request.content)['messages'][0]['content'])
                await asyncio.sleep(.23)
            return httpx.Response(200,json=COMPLETE)
        client=AsyncOpenAI(api_key='synthetic',base_url='https://synthetic.invalid/v1',
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),max_retries=0)
        await client.chat.completions.create(**REQUEST);warming=False
        async def one(label,seconds):
            with native_operation_scope(time.time()+seconds):
                try:
                    value=await aux._relay_async_completion(client,{**REQUEST,'messages':[{'role':'user','content':label}]})
                    return value.choices[0].message.content
                except aux.AuxiliaryExplicitCancellation:return 'cancelled'
        try:
            results=await asyncio.gather(one('short',.08),one('long',.5))
            assert results==['cancelled','ok'] and sorted(wires)==['long','short']
            assert not client.is_closed() and client._client.event_hooks['request']==[]
        finally:await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('human', [False, True])
def test_actual_expired_sdk_request_has_native_failed_outcome_and_no_retry(a,monkeypatch,human):
    from dataclasses import dataclass
    from types import SimpleNamespace as NS
    from agent import conversation_loop
    from agent.turn_outcome import turn_outcome
    from agent.turn_failure_copy import exit_reason_failure
    body=ContinuousBody();wires=[]
    def handle(request):
        if not json.loads(request.content).get('stream'):return httpx.Response(200,json=COMPLETE)
        wires.append(time.time())
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=body)
    client=sdk(a,handle)
    a._abort_request_openai_client=lambda *args,**kw:body.close()
    a.thinking_callback=None;a._has_pending_redirect=lambda:False;a._vprint=Mock();a.log_prefix=''
    a._drop_trailing_empty_response_scaffolding=Mock();a._strip_think_blocks=lambda value:value
    a._persist_session=Mock()
    @dataclass
    class Continue:action:str='fallthrough'
    monkeypatch.setattr(conversation_loop,'nous_rate_limit_guard',lambda agent:Continue())
    monkeypatch.setattr(conversation_loop,'build_api_request',lambda agent:Continue())
    def dispatch(agent):
        try:
            with native_operation_scope(time.time()+.1):return h.interruptible_streaming_api_call(agent,REQUEST)
        except NativeOperationExpired:
            if human:agent._interrupt_requested=True
            raise
    monkeypatch.setattr(conversation_loop,'perform_api_call',dispatch)
    state=NS(_retry=NS(),thinking_spinner=None,messages=[],conversation_history=[],api_start_time=time.time(),
        interrupted=False,failed=False,final_response='',api_request_id='request-1',effective_task_id='task',
        turn_id='turn',api_call_count=1,api_kwargs=REQUEST,retry_count=0,max_retries=3,_turn_exit_reason='')
    try:
        assert conversation_loop._run_api_retry_loop(a,state) is None
        assert len(wires)==1 and state.retry_count==0
        if human:assert state.interrupted and not state.failed
        else:
            assert state.failed and not state.interrupted
            assert state._turn_exit_reason=='native_operation_deadline_expired'
            assert exit_reason_failure(state._turn_exit_reason).retryable is False
            assert turn_outcome({'failed':True,'interrupted':False,'failure_reason':'native_operation_deadline'}).kind=='terminal_error'
    finally:body.close();client.close()


@pytest.mark.parametrize('human',[False,True])
def test_budget_summary_native_expiry_is_failure_but_explicit_stop_is_interruption(a,monkeypatch,human):
    import logging
    from agent import turn_finalizer
    from agent.iteration_budget import IterationBudget
    from agent.turn_failure_copy import exit_reason_failure
    a.max_iterations=1;a.iteration_budget=IterationBudget(1);a.quiet_mode=True
    a._emit_diagnostic_status=Mock()
    def expired(*args,**kw):
        a._interrupt_requested=human
        raise NativeOperationExpired('native_operation_deadline_expired')
    a._handle_max_iterations=expired
    monkeypatch.setattr(turn_finalizer,'_has_active_native_goal',lambda *args:False)
    text,reason,preserved,interrupted=turn_finalizer._resolve_budget_fallback(a,final_response=None,
        api_call_count=1,interrupted=False,failed=False,messages=[],_turn_exit_reason='budget_exhausted',
        _pending_verification_response=None,_pending_verification_response_previewed=False,
        logger=logging.getLogger(__name__))
    if human:assert interrupted and reason.startswith('interrupted_during_api_call')
    else:
        assert not interrupted and reason=='native_operation_deadline_expired'
        assert exit_reason_failure(reason).fails_turn and not exit_reason_failure(reason).retryable


def test_explicit_keyboard_interrupt_is_not_relabelled_when_native_deadline_races(a):
    warming=True
    def handle(request):
        if not warming:
            time.sleep(.12)
            raise KeyboardInterrupt('Synthetic explicit terminal stop')
        return httpx.Response(200,json=COMPLETE)
    client=sdk(a,handle);warming=False
    try:
        with native_operation_scope(time.time()+.05),pytest.raises(KeyboardInterrupt):
            h.direct_api_call(a,REQUEST)
    finally:client.close()


def test_live_request_deadline_is_not_extended_by_backward_wall_clock(a,monkeypatch):
    from agent import deadline as native
    from types import SimpleNamespace as NS
    jump=[0.];body=ContinuousBody(callback=lambda:jump.__setitem__(0,1000.))
    def handle(request):
        if not json.loads(request.content).get('stream'):return httpx.Response(200,json=COMPLETE)
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=body)
    client=sdk(a,handle)
    a._abort_request_openai_client=lambda *args,**kw:body.close()
    actual_time=time.time;actual_mono=time.monotonic
    monkeypatch.setattr(native,'time',NS(time=lambda:actual_time()-jump[0],monotonic=actual_mono))
    start=actual_time()
    try:
        with native_operation_scope(start+.15),pytest.raises(NativeOperationExpired):
            h.interruptible_streaming_api_call(a,REQUEST)
        assert actual_time()-start<.65 and 2<=len(body.arrivals)<body.count
    finally:body.close();client.close()
