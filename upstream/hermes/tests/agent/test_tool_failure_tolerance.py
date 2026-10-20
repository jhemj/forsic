"""Configured failure recovery budgets survive the successful-output loop detector."""
import json

import pytest

from agent.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController


def configured_guard(tmp_path):
    from hermes_cli.config import atomic_config_write
    from hermes_cli.config_effective import load_user_config_effective

    path = tmp_path / 'config.yaml'
    atomic_config_write(path, {'tool_loop_guardrails': {
        'hard_stop_enabled': True,
        'hard_stop_after': {'exact_failure': 8, 'same_tool_failure': 8},
    }})
    config = load_user_config_effective(path)['tool_loop_guardrails']
    return ToolCallGuardrailController(ToolCallGuardrailConfig.from_mapping(config, platform='desktop'))


@pytest.mark.parametrize('period', [1, 2])
def test_exact_failures_keep_eight_attempts_and_successful_loops_keep_five(tmp_path, period):
    guard = configured_guard(tmp_path)
    error = json.dumps({'exit_code': 1, 'error': 'synthetic recoverable failure'})
    notices = []
    # terminal's varied failing commands are diagnosis; exact replays have a limit.
    for index in range(8 * period):
        args = {'command': f'synthetic-{index % period}'}
        assert guard.before_call('terminal', args).allows_execution
        guard.after_call('terminal', args, error)
        observed = guard.observe_call('terminal', args, error, failed=True)
        notices.append(observed.notice)
        assert not observed.stub
        assert guard.halt_decision is None
    assert any(notices)  # Failure loop guidance remains visible.
    assert guard.before_call('terminal', {'command': 'synthetic-0'}).code == 'repeated_exact_failure_block'
    guard.reset_for_turn()
    assert guard.before_call('terminal', {'command': 'synthetic-0'}).allows_execution
    for index in range(5 * period):
        args = {'command': f'synthetic-{index % period}'}
        guard.after_call('terminal', args, 'successful output', failed=False)
        guard.observe_call('terminal', args, 'successful output', failed=False)
        assert bool(guard.halt_decision) == (index == 5 * period - 1)


def test_same_tool_failure_rounds_halt_at_eight_and_real_success_resets(tmp_path):
    guard = configured_guard(tmp_path)
    error = '{"error":"synthetic recoverable failure"}'

    def run_round(round_index):
        decisions = []
        with guard.feedback_batch():
            for sibling in range(3):
                args = {'path': f'synthetic-{round_index}-{sibling}'}
                assert guard.before_call('read_file', args).allows_execution
                decisions.append(guard.after_call('read_file', args, error))
                guard.observe_call('read_file', args, error, failed=True)
        return decisions

    for index in range(7):
        decisions = run_round(index)
        assert all(not item.should_halt for item in decisions)
        assert guard.halt_decision is None
    guard.after_call('read_file', {'path': 'recovered'}, 'observed source', failed=False)
    for index in range(8):
        decisions = run_round(100 + index)
        assert all(item.should_halt == (index == 7) for item in decisions)
        if index >= 2:
            assert all(item.count == index + 1 for item in decisions)
    assert guard.halt_decision.code == 'same_tool_failure_halt'
    assert guard.halt_decision.count == 8
    guard.reset_for_turn()
    assert guard.halt_decision is None
    assert all(not item.should_halt for item in run_round(200))
