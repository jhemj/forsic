"""Native bridge provenance resets format failures, preserving real tool budgets."""
import json
from types import SimpleNamespace as NS

import pytest

from agent import tool_executor as executor
from agent.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController
from tools.registry import registry


LOCAL = 'fixture_bridge_reader'
ERROR = '{"error":"synthetic failure"}'


@pytest.fixture
def bridge_agent(monkeypatch):
    registry.register(LOCAL, 'fixture', {
        'name': LOCAL, 'parameters': {'type': 'object', 'properties': {'index': {'type': 'integer'}},
                                     'required': ['index']}}, lambda **kw: 'ok')
    monkeypatch.setattr(executor, '_tool_search_scoped_names', lambda agent: frozenset({LOCAL}))
    monkeypatch.setattr('hermes_cli.observability.shared_metrics_harness.observe_tool_outcome', lambda *a: None)
    guard = ToolCallGuardrailController(ToolCallGuardrailConfig(hard_stop_enabled=True))
    agent = NS(_tool_guardrails=guard)
    # Stop at the native guardrail observation boundary, before persistence/UI.
    # Parsing, provenance transport and controller behavior use real code.
    class Observed(Exception):
        pass

    def observe(name, args, result, *, failed, tool_call_id):
        agent.decision = guard.after_call(name, args, result, failed=failed)
        raise Observed

    agent._append_guardrail_observation = observe

    def publish(parsed, *, failed):
        with pytest.raises(Observed):
            executor._commit_tool_result(agent, [], parsed.ref('fixture'), ERROR if failed else 'ok',
                budget=None, tool_duration=0, is_error=failed, blocked=False,
                effect_disposition=None, observed=True)
        return agent.decision

    agent.publish = publish
    yield agent
    registry.deregister(LOCAL)


def parse(agent, args):
    return executor._parse_tool_call(agent, NS(id='synthetic-call', function=NS(
        name='tool_call', arguments=json.dumps(args))))


def valid(index=0):
    return {'calls': [{'name': LOCAL, 'arguments': {'index': index}}]}


@pytest.mark.parametrize('invalid', [
    {'calls': '[invalid JSON'},
    {'calls': [{'arguments': {}}]},
    {'calls': [{'name': LOCAL, 'arguments': '{invalid JSON'}]},
    {'calls': [{'name': LOCAL, 'arguments': {}}, {'name': LOCAL, 'arguments': {}}]},
    {'calls': [{'name': LOCAL, 'arguments': {}}, {'name': 'connectors__fixture__read', 'arguments': {}}]},
    {'name': 'fixture_unknown_name', 'arguments': {}},
])
def test_recovered_bridge_does_not_accumulate_historic_format_errors(bridge_agent, invalid):
    agent = bridge_agent
    for index in range(10):
        broken = parse(agent, invalid)
        assert broken.name == 'tool_call'
        assert broken.bridge_resolution == ('tool_call', invalid, False)
        assert agent._tool_guardrails.before_call(broken.name, broken.args).allows_execution
        decision = agent.publish(broken, failed=True)
        assert decision.count == 1 and not decision.should_halt
        recovered = parse(agent, valid(index))
        assert recovered.name == LOCAL
        agent.publish(recovered, failed=False)
        assert agent._tool_guardrails.halt_decision is None


def test_ordered_sibling_results_reset_only_after_valid_result_is_observed(bridge_agent):
    agent = bridge_agent
    broken = {'calls': 'invalid JSON'}
    # Concurrent executors parse every sibling before publishing in input order.
    parsed = [parse(agent, args) for args in (broken, valid(), broken)]
    with agent._tool_guardrails.feedback_batch():
        assert agent.publish(parsed[0], failed=True).count == 1
        agent.publish(parsed[1], failed=False)
        assert agent.publish(parsed[2], failed=True).count == 1
    assert not agent.publish(parse(agent, {'calls': []}), failed=True).should_halt
    decision = agent.publish(parse(agent, {'calls': None}), failed=True)
    assert decision.code == 'same_tool_failure_warning' and decision.count == 3


def test_valid_bridge_envelopes_do_not_reset_persistent_underlying_failures(bridge_agent):
    agent = bridge_agent
    for index in range(8):
        agent.publish(parse(agent, {'calls': []}), failed=True)
        decision = agent.publish(parse(agent, valid(index)), failed=True)
        assert decision.should_halt == (index == 7)
    assert decision.code == 'same_tool_failure_halt'
    assert decision.tool_name == LOCAL and decision.count == 8


def test_valid_connector_failures_keep_their_budget_after_local_recovery(bridge_agent):
    agent = bridge_agent
    for index in range(8):
        agent.publish(parse(agent, valid(index)), failed=False)
        agent.publish(parse(agent, {'calls': []}), failed=True)
        connector = parse(agent, {'calls': [{'name': 'connectors__fixture__read', 'arguments': {'index': index}}]})
        assert connector.bridge_resolution[2] is True
        decision = agent.publish(connector, failed=True)
        assert decision.should_halt == (index == 7)
    assert decision.code == 'same_tool_failure_halt'
    assert decision.tool_name == 'tool_call' and decision.count == 8


def test_unrecovered_format_and_exact_underlying_failures_still_stop(bridge_agent):
    agent = bridge_agent
    for index in range(8):
        broken = parse(agent, {'calls': [{'name': f'fixture_unknown_{index}', 'arguments': {}}]})
        decision = agent.publish(broken, failed=True)
        assert decision.should_halt == (index == 7)
    assert decision.count == 8
    agent._tool_guardrails.reset_for_turn()
    call = parse(agent, valid())
    for _ in range(5):
        agent.publish(call, failed=True)
    assert agent._tool_guardrails.before_call(call.name, call.args).code == 'repeated_exact_failure_block'
    agent._tool_guardrails.reset_for_turn()
    assert agent._tool_guardrails.before_call(call.name, call.args).allows_execution


def test_malformed_outer_json_and_invalid_concrete_args_never_unwrap(bridge_agent):
    malformed = executor._parse_tool_call(bridge_agent, NS(id='bad', function=NS(
        name='tool_call', arguments='{invalid JSON')))
    assert malformed.parse_error and malformed.bridge_resolution is None
    bad_args = parse(bridge_agent, {'calls': [{'name': LOCAL, 'arguments': {}}]})
    assert bad_args.name == 'tool_call' and bad_args.scope_block
    assert bad_args.bridge_resolution[2] is True  # envelope parsed; concrete schema still blocks
