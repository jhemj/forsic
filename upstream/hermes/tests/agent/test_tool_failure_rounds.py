"""Same-tool recovery counts feedback opportunities, not sibling results. Offline only."""
import json
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from agent.tool_guardrails import (
    LoopCapConfig, ToolCallGuardrailConfig, ToolCallGuardrailController,
)
from tools.registry import registry


def controller(**overrides):
    return ToolCallGuardrailController(ToolCallGuardrailConfig(
        hard_stop_enabled=True, same_tool_failure_halt_after=3, same_tool_failure_warn_after=1, **overrides))


def failure(guard, index, tool='fixture_reader'):
    return guard.after_call(tool, {'index': index}, '{"error":"synthetic"}', failed=True)


def test_native_model_batches_allow_feedback_before_same_tool_halt():
    from run_agent import AIAgent

    guard = controller()
    agent = object.__new__(AIAgent)
    agent._tool_guardrails = guard
    decisions = []
    def execute(message, *_args):
        for call in message.tool_calls:
            decisions.append(guard.after_call(call.function.name, json.loads(call.function.arguments),
                                              '{"error":"synthetic"}', failed=True))
    agent._execute_tool_calls_concurrent = execute
    agent._execute_tool_calls_sequential = execute
    # Real native model-round dispatcher, with offline execution at its tool boundary.
    with patch('run_agent.get_active_env', return_value=None):
        for round_index in range(3):
            calls = [NS(id=f'{round_index}:{index}', function=NS(
                name='read_file', arguments=json.dumps({"path": f"synthetic-{round_index}-{index}"}))) for index in range(3)]
            agent._execute_tool_calls(NS(tool_calls=calls), [], 'fixture')
            group = decisions[-3:]
            assert [decision.count for decision in group] == [round_index + 1] * 3
            assert all(decision.should_halt == (round_index == 2) for decision in group)
    assert decisions[-1].code == 'same_tool_failure_halt'
    assert not agent._executing_tools
    guard.reset_for_turn()
    def interrupted(*_args):
        failure(guard, 0)
        raise RuntimeError('synthetic executor failure')
    agent._execute_tool_calls_sequential = interrupted
    with pytest.raises(RuntimeError):
        agent._execute_tool_calls(NS(tool_calls=[calls[0]]), [], 'fixture')
    assert not agent._executing_tools
    assert failure(guard, 1).count == 2


def test_nested_exception_scope_restores_standalone_accounting_and_turn_reset():
    guard = controller()
    with pytest.raises(RuntimeError), guard.feedback_batch():
        assert failure(guard, 1).count == 1
        with guard.feedback_batch():
            assert failure(guard, 2).count == 1
        assert failure(guard, 3).count == 1
        raise RuntimeError('synthetic executor failure')
    assert failure(guard, 4).count == 2
    assert failure(guard, 5).should_halt
    guard.reset_for_turn()
    assert [failure(guard, index).count for index in range(3)] == [1, 2, 3]
    guard.reset_for_turn()
    with guard.feedback_batch():
        assert failure(guard, 6).count == 1


def test_success_resets_only_its_failure_group_and_mutations_keep_progress_semantics():
    guard = controller()
    with guard.feedback_batch():
        failure(guard, 0)
        guard.after_call('unrelated_reader', {}, 'ok', failed=False)
        assert failure(guard, 1).count == 1
    with guard.feedback_batch():
        assert failure(guard, 2).count == 2
        guard.after_call('fixture_reader', {}, 'ok', failed=False)
        assert failure(guard, 3).count == 1
        assert failure(guard, 4).count == 1
    with guard.feedback_batch():
        assert failure(guard, 5).count == 2
        guard.after_call('write_file', {}, 'ok', failed=False)
        assert failure(guard, 6).count == 1
    with guard.feedback_batch():
        assert failure(guard, 7).count == 2
    with guard.feedback_batch():
        assert failure(guard, 8).should_halt


def test_registered_aliases_share_one_batch_count_but_arguments_cannot_select_group():
    names = ['fixture_batch_alias_a', 'fixture_batch_alias_b']
    for name in names:
        registry.register(name, 'fixture', {'name': name, 'parameters': {}}, lambda **kw: '',
                          loop_comparison=lambda args, result: {'failure_group': 'fixture_batch_reader'})
    try:
        guard = controller()
        for round_index in range(3):
            with guard.feedback_batch():
                a = failure(guard, round_index, names[0])
                b = failure(guard, round_index, names[1])
                assert a.count == b.count == round_index + 1
                other = guard.after_call('fixture_unregistered', {'failure_group': 'fixture_batch_reader'},
                                         '{"failure_group":"fixture_batch_reader"}', failed=True)
                assert other.count == round_index + 1
        assert a.should_halt and b.should_halt
    finally:
        for name in names:
            registry.deregister(name)


def test_exact_replay_caps_and_identical_cycles_remain_per_call_inside_batch():
    guard = controller(exact_failure_block_after=2, loop_caps=LoopCapConfig(max_web_searches=64))
    with guard.feedback_batch():
        failure(guard, 'same')
        assert failure(guard, 'same').count == 2  # Exact failure count remains per call.
        assert guard.before_call('fixture_reader', {'index': 'same'}).code == 'repeated_exact_failure_block'
        for index in range(64):
            assert guard.before_call('web_search', {'query': str(index)}).allows_execution
        assert guard.before_call('web_search', {'query': '65'}).code == 'loop_web_search_cap'
    guard = controller(no_progress_block_after=3)
    with guard.feedback_batch():
        for index in range(6):
            guard.observe_call('fixture_cycle', {'index': index % 2}, 'same-result', failed=False)
    assert guard.halt_decision.code == 'identical_cycle_halt'
