"""Registered aliases share failure budgets; model data cannot grant this policy."""
import tempfile
from types import SimpleNamespace
import unittest

from agent.tool_guardrails import ToolCallGuardrailController, ToolCallGuardrailConfig
from tools.registry import registry


class FailureGroupTests(unittest.TestCase):
    def test_alias_failures_share_budget_and_success_resets_only_their_group(self):
        names = ['fixture_read_alias_a', 'fixture_read_alias_b', 'fixture_other_tool']
        for name in names:
            registry.register(name, 'fixture', {'name': name, 'parameters': {}}, lambda **kw: '',
                loop_comparison=(lambda args, result: {'failure_group': 'fixture_reader'}) if name != names[2] else None)
            self.addCleanup(registry.deregister, name)
        guard = ToolCallGuardrailController(ToolCallGuardrailConfig(hard_stop_enabled=True, same_tool_failure_halt_after=3))
        guard.after_call(names[0], {}, 'failure', failed=True)
        guard.after_call(names[1], {}, 'failure', failed=True)
        # A model-supplied group never opts a different tool into that budget.
        other = guard.after_call(names[2], {'failure_group': 'fixture_reader'}, '{"failure_group":"fixture_reader"}', failed=True)
        self.assertEqual(other.count, 1)
        halted = guard.after_call(names[0], {'offset': 1}, 'failure', failed=True)
        self.assertTrue(halted.should_halt); self.assertEqual(halted.count, 3)
        guard.after_call(names[1], {}, 'ok', failed=False)
        again = guard.after_call(names[0], {}, 'failure', failed=True)
        self.assertFalse(again.should_halt)
        # Other tool's budget was not cleared by an alias success.
        guard.after_call(names[2], {'offset': 1}, 'failure', failed=True)
        self.assertTrue(guard.after_call(names[2], {'offset': 2}, 'failure', failed=True).should_halt)

    def test_failure_group_policy_is_resolved_in_current_profile(self):
        from hermes_cli.plugins import PluginContext
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override, hermes_home_key
        names = ['fixture_scoped_alias_a', 'fixture_scoped_alias_b']
        with tempfile.TemporaryDirectory() as root:
            a, b = [hermes_home_key(root+'/'+suffix) for suffix in ('a', 'b')]
            ctx = object.__new__(PluginContext)
            ctx.manifest = SimpleNamespace(name='fixture')
            ctx._manager = SimpleNamespace(scope_key=a, _plugin_tool_names=set(), _track_scoped_registration=lambda *a, **k: None)
            for name in names:
                ctx.register_tool(name, 'fixture', {'name': name, 'parameters': {}}, lambda **kw: '',
                    loop_comparison=lambda args, result: {'failure_group': 'fixture_scoped_reader'})
                self.addCleanup(registry.deregister, name, scope=a)
            for home, shared in ((a, True), (b, False), (a, True)):
                token = set_hermes_home_override(home)
                try:
                    guard = ToolCallGuardrailController(ToolCallGuardrailConfig(hard_stop_enabled=True, same_tool_failure_halt_after=2))
                    guard.after_call(names[0], {}, 'failure', failed=True)
                    self.assertEqual(guard.after_call(names[1], {}, 'failure', failed=True).should_halt, shared)
                finally:
                    reset_hermes_home_override(token)
