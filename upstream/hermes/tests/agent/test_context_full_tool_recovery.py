"""Offline returned-length recovery and accounting, with real native usage/overflow paths."""
from copy import deepcopy
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from agent.api_request_hooks import ApiRequestHooksMixin
from agent.context_compressor import ContextCompressor
from agent.turn_response_check import check_api_response
from agent.turn_response_intake import normalize_model_response
from agent.turn_retry_state import TurnRetryState
from hermes_state import SessionDB


class OfflineAgent(ApiRequestHooksMixin):
    pass


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from hermes_cli import lifecycle
    db = SessionDB(tmp_path / 'state.db')
    db.create_session('synthetic-native-recovery', 'test')
    agent = OfflineAgent()
    agent.session_id = 'synthetic-native-recovery'
    agent._session_db, agent._session_db_created = db, True
    agent.platform = 'test'
    agent.provider, agent.api_mode, agent.model = 'synthetic', 'chat_completions', 'synthetic'
    agent.base_url, agent.api_key, agent.client = '', '', None
    agent.quiet_mode, agent.verbose_logging, agent.log_prefix = True, False, ''
    agent._interrupt_requested = False
    agent.thinking_callback = None
    agent.tools, agent.max_tokens = [], None
    agent._ephemeral_max_output_tokens = None
    agent._fallback_index, agent._fallback_chain = 0, []
    agent._persist_session = Mock()
    agent._vprint = agent._buffer_vprint = agent._buffer_diagnostic_status = Mock()
    agent._flush_status_buffer = agent._cleanup_task_resources = agent._touch_activity = Mock()
    agent._should_treat_stop_as_truncated = lambda *args: False
    agent._requested_output_cap_from_api_kwargs = lambda kwargs: kwargs.get('max_tokens')
    agent._last_compaction_in_place = True
    agent._compress_context = Mock()
    agent.context_compressor = ContextCompressor('synthetic', config_context_length=131072, quiet_mode=True)
    for name in ('api_calls', 'prompt_tokens', 'completion_tokens', 'total_tokens', 'input_tokens',
                 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens',
                 'estimated_cost_usd'):
        setattr(agent, 'session_' + name, 0)
    agent._get_transport = lambda: NS(validate_response=lambda response: bool(response.choices),
        normalize_response=lambda response: response.choices[0].message)
    receipts = []
    monkeypatch.setattr(lifecycle, 'has_hook', lambda name: name == 'post_api_request')
    monkeypatch.setattr(lifecycle, 'invoke_hook', lambda name, **kwargs: receipts.append((name, kwargs)))
    monkeypatch.setattr('agent.turn_overflow.time.sleep', lambda seconds: None)
    yield agent, db, receipts
    db.close()


def response(prompt=130744, output=328, finish='length', tools=True):
    calls = [NS(id='incomplete-call', function=NS(name='synthetic_read', arguments='{"path":'))] if tools else []
    message = NS(content='', tool_calls=calls, finish_reason=finish)
    return NS(id='physical-response', model='synthetic', usage=NS(prompt_tokens=prompt,
        completion_tokens=output, total_tokens=prompt + output),
        choices=[NS(message=message, finish_reason=finish)])


def check(agent, returned, rows, **overrides):
    retry = overrides.pop('_retry', TurnRetryState())
    args = dict(response=returned, _retry=retry, thinking_spinner=None, messages=rows,
        api_messages=[{'role': 'system', 'content': 'stable-system'}, *rows], api_kwargs={},
        active_system_prompt='stable-system', system_message='stable-system', conversation_history=list(rows),
        finish_reason='stop', retry_count=0, max_retries=3, compression_attempts=0,
        max_compression_attempts=2, length_continue_retries=0, truncated_response_parts=[],
        truncated_tool_call_retries=0, current_turn_user_idx=0, api_call_count=1,
        api_request_id='physical-request', api_start_time=time.time(), effective_task_id='synthetic',
        turn_id='synthetic-turn', _preflight_compression_blocked=False, _last_preflight_pressure=None,
        approx_tokens=120000, _provider_overflow_recovery_pending=False)
    args.update(overrides)
    from agent.conversation_loop import _run_phase
    state = NS(**args)
    agent._offline_loop_state = state
    return _run_phase(check_api_response, agent, state), retry


def transcript():
    return [{'role': 'user', 'content': 'Earlier synthetic context ' * 400},
        {'role': 'assistant', 'content': 'Previous conclusion ' * 400},
        {'role': 'user', 'content': 'Continue this explicit scope', 'message_uid': 'active-user'}]


def test_provider_filled_tool_response_uses_native_compression_before_any_output_retry(runtime):
    agent, db, receipts = runtime
    rows = transcript()
    original = deepcopy(rows)
    compacted = [deepcopy(rows[-1])]

    def compress(messages, system, **kwargs):
        # Provider usage and receipt must precede recovery; incomplete arguments never enter history.
        assert agent.session_api_calls == 1
        assert receipts[0][1]['finish_reason'] == 'length'
        assert agent.context_compressor.last_prompt_tokens == 130744
        assert messages == original
        assert system == 'stable-system'
        assert kwargs['bypass_cooldown'] and kwargs['trigger'] == 'overflow'
        assert kwargs['approx_tokens'] >= 130744
        return compacted, system
    agent._compress_context.side_effect = compress
    verdict, retry = check(agent, response(), rows)
    assert verdict.action == 'break' and retry.restart_with_compressed_messages
    assert verdict._provider_overflow_recovery_pending
    assert verdict.messages is compacted and verdict.conversation_history == compacted
    assert verdict.active_system_prompt == 'stable-system'
    assert agent._offline_loop_state.messages is compacted
    assert agent._offline_loop_state.conversation_history == compacted
    assert agent._offline_loop_state._provider_overflow_recovery_pending
    assert verdict.compression_attempts == 1 and verdict.truncated_tool_call_retries == 0
    assert agent._ephemeral_max_output_tokens is None
    assert rows == original
    assert len(receipts) == 1
    persisted = db.get_session(agent.session_id)
    assert persisted['input_tokens'] == 130744 and persisted['output_tokens'] == 328
    assert persisted['api_call_count'] == 1


@pytest.mark.parametrize('mode', ['exhausted', 'no_progress', 'locked'])
def test_filled_response_uses_native_bounded_and_deferred_outcomes(runtime, mode):
    agent, _, receipts = runtime
    rows = transcript()
    agent._compress_context.return_value = rows, 'stable-system'
    attempts = 2 if mode == 'exhausted' else 0
    if mode == 'locked':
        agent._compression_skipped_due_to_lock = 'synthetic-other-owner'
    verdict, retry = check(agent, response(), rows, compression_attempts=attempts)
    assert verdict.action == 'return'
    assert not retry.restart_with_compressed_messages
    assert verdict.messages == rows and verdict.truncated_tool_call_retries == 0
    assert agent.session_api_calls == 1 and len(receipts) == 1
    if mode == 'exhausted':
        agent._compress_context.assert_not_called()
    if mode == 'locked':
        assert not verdict.result.get('compression_exhausted')
        assert verdict.compression_attempts == 0
    else:
        assert verdict.result['compression_exhausted']
        assert verdict.result['failure_reason'] == 'context_overflow'


def test_output_only_truncations_each_account_once_before_boost_retry(runtime):
    agent, db, receipts = runtime
    rows = transcript()
    retry = TurnRetryState()
    for n in range(2):
        verdict, _ = check(agent, response(prompt=32000, output=256), rows,
            _retry=retry, truncated_tool_call_retries=n)
        assert verdict.action == 'continue'
        assert verdict.truncated_tool_call_retries == n + 1
    agent._compress_context.assert_not_called()
    assert agent.session_api_calls == 2 and agent.session_prompt_tokens == 64000
    assert agent.session_completion_tokens == 512
    assert len(receipts) == 2 and all(item[1]['finish_reason'] == 'length' for item in receipts)
    persisted = db.get_session(agent.session_id)
    assert persisted['api_call_count'] == 2 and persisted['input_tokens'] == 64000


def test_successful_response_has_one_usage_fold_and_receipt_through_intake(runtime, monkeypatch):
    agent, db, receipts = runtime
    rows = transcript()
    returned = response(prompt=1200, output=20, finish='stop', tools=False)
    returned.choices[0].message.content = 'Synthetic final answer'
    agent.tool_progress_callback = None
    verdict, _ = check(agent, returned, rows)
    assert verdict.action == 'break'
    normalized = normalize_model_response(agent, response=returned, messages=rows,
        api_messages=rows, conversation_history=None, api_call_count=1,
        api_duration=verdict.api_duration, api_start_time=time.time(), api_request_id='physical-request',
        effective_task_id='synthetic', turn_id='synthetic-turn', active_system_prompt='stable-system')
    assert normalized.action == 'fallthrough'
    assert agent.session_api_calls == 1 and len(receipts) == 1
    assert agent.session_total_tokens == 1220
    assert db.get_session(agent.session_id)['api_call_count'] == 1


def test_refusal_accounts_original_route_before_fallback_dispatch(runtime, monkeypatch):
    from agent.turn_truncation import RefusalVerdict
    agent, _, receipts = runtime

    def refusal(*args, **kwargs):
        assert agent.session_api_calls == 1 and agent.session_prompt_tokens == 3000
        assert receipts[0][1]['finish_reason'] == 'content_filter'
        agent.provider = 'synthetic-fallback'
        return RefusalVerdict('break', None, 'stable-system')
    monkeypatch.setattr('agent.turn_response_check.handle_content_policy_refusal', refusal)
    verdict, _ = check(agent, response(prompt=3000, output=10, finish='content_filter', tools=False), transcript())
    assert verdict.action == 'break' and len(receipts) == 1
    assert receipts[0][1]['provider'] == 'synthetic'


def test_next_real_response_rearms_compression_budget_and_keeps_overflow_preflight_latch(runtime):
    agent, _, _ = runtime
    agent.context_compressor._verify_compaction_cleared_threshold = True
    verdict, _ = check(agent, response(prompt=1200, output=20, finish='stop', tools=False),
        transcript(), compression_attempts=1, _preflight_compression_blocked=True,
        _last_preflight_pressure=130744, _provider_overflow_recovery_pending=True)
    assert verdict.compression_attempts == 0
    assert not verdict._preflight_compression_blocked and verdict._last_preflight_pressure is None
    assert agent._offline_loop_state._provider_overflow_recovery_pending


@pytest.mark.parametrize('prompt', [32000, 130744])
def test_safe_pause_discards_truncated_call_without_retry_or_compaction(runtime, prompt):
    from agent.goal_pause import SAFE_PAUSE_REASON
    from hermes_cli.goals import GoalState
    agent, db, receipts = runtime
    goal = GoalState('Inspect synthetic scope')
    goal.pause_requested = True
    db.set_meta('goal:' + agent.session_id, goal.to_json())
    rows = transcript()
    original = deepcopy(rows)
    # Even a valid JSON prefix is not a complete tool response when finish_reason=length.
    returned = response(prompt=prompt)
    returned.choices[0].message.tool_calls[0].function.arguments = '{}'
    verdict, retry = check(agent, returned, rows)
    assert verdict.action == 'return' and verdict.result['interrupted']
    assert verdict.result['turn_exit_reason'] == SAFE_PAUSE_REASON
    assert not retry.restart_with_compressed_messages and not retry.restart_with_length_continuation
    agent._compress_context.assert_not_called()
    assert agent._ephemeral_max_output_tokens is None and rows == original
    assert GoalState.from_json(db.get_meta('goal:' + agent.session_id)).status == 'paused'
    assert agent.session_api_calls == 1 and len(receipts) == 1


def test_invalid_returned_response_accounts_usage_before_native_retry(runtime, monkeypatch):
    from agent.turn_response_check import InvalidResponseVerdict
    agent, db, receipts = runtime
    returned = response(prompt=3000, output=10)
    returned.choices = []

    def invalid(*args, **kwargs):
        assert agent.session_api_calls == 1 and agent.session_prompt_tokens == 3000
        return InvalidResponseVerdict('continue', None, 'stable-system', 1, 0)
    monkeypatch.setattr('agent.turn_response_check.retry_invalid_response', invalid)
    verdict, _ = check(agent, returned, transcript())
    assert verdict.action == 'continue'
    assert db.get_session(agent.session_id)['api_call_count'] == 1
    assert not receipts  # Native invalid-response error hook owns the malformed-body receipt.


def test_usage_missing_retains_output_retry_and_counts_physical_attempt(runtime):
    agent, _, receipts = runtime
    returned = response()
    returned.usage = None
    verdict, _ = check(agent, returned, transcript())
    assert verdict.action == 'continue' and verdict.truncated_tool_call_retries == 1
    agent._compress_context.assert_not_called()
    assert agent.session_api_calls == 1 and agent.session_prompt_tokens == 0
    assert len(receipts) == 1 and receipts[0][1]['usage'] is None


def test_terminal_stream_overflow_keeps_native_terminal_contract(runtime):
    from hermes_constants import PARTIAL_STREAM_STUB_ID
    agent, _, receipts = runtime
    returned = response()
    returned.id, returned._overflow_terminal = PARTIAL_STREAM_STUB_ID, True
    rows = transcript()
    verdict, retry = check(agent, returned, rows)
    assert verdict.action == 'return' and verdict.result['compression_exhausted']
    assert verdict.result['failure_reason'] == 'context_overflow'
    assert not retry.restart_with_compressed_messages
    agent._compress_context.assert_not_called()
    assert agent.session_api_calls == 1 and len(receipts) == 1


def test_context_error_uses_same_bounded_native_compression_path(runtime):
    from agent.error_classifier import FailoverReason
    from agent.turn_overflow import recover_from_overflow
    agent, _, _ = runtime
    rows = transcript()
    compacted = [deepcopy(rows[-1])]
    agent._compress_context.return_value = compacted, 'stable-system'
    retry = TurnRetryState()
    verdict = recover_from_overflow(agent, RuntimeError('synthetic context error'),
        NS(reason=FailoverReason.context_overflow), retry, status_code=400,
        error_msg='prompt is too long: 130744 tokens', wrapped_output_cap_budget=None,
        messages=rows, api_messages=rows, system_message='stable-system',
        active_system_prompt='stable-system', conversation_history=list(rows), approx_tokens=120000,
        compression_attempts=0, max_compression_attempts=2, api_call_count=1, effective_task_id='synthetic')
    assert verdict.action == 'break' and retry.restart_with_compressed_messages
    assert verdict.provider_overflow_recovery_pending and verdict.compression_attempts == 1
    assert verdict.messages is compacted


def test_safe_pause_closes_prior_complete_tool_batch_and_cleans_task(runtime):
    from agent.goal_pause import SAFE_PAUSE_NOTICE
    from hermes_cli.goals import GoalState
    agent, db, _ = runtime
    goal = GoalState('Inspect synthetic scope')
    goal.pause_requested = True
    db.set_meta('goal:' + agent.session_id, goal.to_json())
    rows = transcript() + [
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'complete-call',
            'type': 'function', 'function': {'name': 'synthetic_read', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'complete-call', 'content': 'Retained completed result'}]
    original = deepcopy(rows)
    verdict, _ = check(agent, response(), rows)
    assert verdict.action == 'return'
    assert rows[:-1] == original and rows[-1]['content'] == SAFE_PAUSE_NOTICE
    assert rows[-1]['role'] == 'assistant'
    agent._cleanup_task_resources.assert_called_once_with('synthetic')
    agent._persist_session.assert_called_once()
