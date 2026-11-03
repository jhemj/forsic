"""Deterministic native prune -> API build contracts; no provider or evidence calls."""
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from agent.context_compressor import ContextCompressor, _tool_calls_by_id
from agent.tool_dispatch_helpers import make_tool_result_message
from agent.tool_guardrails import ToolGuardrailDecision, append_toolguard_guidance
from agent.turn_context import build_api_messages
from hermes_constants import hermes_home_key, set_hermes_home_override, reset_hermes_home_override
from tools.registry import registry


class NativeContextTransportTests(unittest.TestCase):
    name = 'synthetic_context_transport'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.scope = hermes_home_key(str(Path(self.temp.name) / 'profile-a'))
        self.token = set_hermes_home_override(self.scope)
        self.addCleanup(reset_hermes_home_override, self.token)
        self.received = []
        self.register(self.scope, 'profile-a')
        self.addCleanup(registry.deregister, self.name, scope=self.scope)
        self.args = {'path': '/synthetic/missing', 'scope': 'selected-volume'}
        self.body = {'source_ref': {'id': 'synthetic-source', 'version': 'immutable-version'},
                     'error_code': 'not_found', 'status': 'returned', 'scope': 'selected-volume',
                     'next_call': {'action': 'retained', 'source_id': 'synthetic-source'},
                     'padding': 'retained detail ' * 500}

    def register(self, scope, label):
        def summarize(args, content):
            self.received.append((label, deepcopy(args), content))
            body = json.loads(content)
            return json.dumps({key: body[key] for key in
                               ('source_ref', 'error_code', 'status', 'scope', 'next_call') if key in body})
        registry.register(self.name, 'synthetic', {'parameters': {'type': 'object',
            'properties': {'path': {'type': 'string'}, 'scope': {'type': 'string'}}}},
            lambda **kwargs: self.fail('No tool may execute'), scope=scope, result_summary=summarize)

    def messages(self, bridge=True, body=None):
        outer = 'tool_call' if bridge else self.name
        args = {'calls': [{'name': self.name, 'arguments': self.args}]} if bridge else self.args
        return [{'role': 'user', 'content': 'Synthetic investigation'},
                {'role': 'assistant', 'content': '', 'tool_calls': [{
                    'id': 'pair-id', 'type': 'function',
                    'function': {'name': outer, 'arguments': json.dumps(args)}}]},
                make_tool_result_message(self.name, json.dumps(body or self.body), 'pair-id',
                                         effect_disposition='none')]

    def prune(self, messages):
        before = deepcopy(messages)
        selection = _tool_calls_by_id(messages)
        self.assertEqual(messages, before, 'Resolving must not change transcript/cache bytes')
        changed = ContextCompressor._demote_tool_result_at(messages, 2, selection, 100)
        self.assertEqual(messages[1], before[1])
        self.assertEqual(messages[2]['tool_call_id'], before[2]['tool_call_id'])
        self.assertEqual(messages[2]['effect_disposition'], before[2]['effect_disposition'])
        return changed

    def wire(self, messages):
        agent = SimpleNamespace(_current_turn_timestamp=1, ephemeral_system_prompt='',
            _copy_reasoning_content_for_api=lambda *args: None,
            _should_sanitize_tool_calls=lambda: False)
        wire, _ = build_api_messages(agent, messages, current_turn_user_idx=0,
            ext_prefetch_cache=None, plugin_user_context=None, moa_config=None, active_system_prompt='')
        return wire

    def test_direct_and_single_bridge_reach_registered_summary_and_real_api_build(self):
        for bridge in (False, True):
            messages = self.messages(bridge)
            self.assertTrue(self.prune(messages))
            body = self.wire(messages)[-1]['content']
            parsed = json.loads(body.split('\n', 1)[1])
            self.assertEqual(parsed['source_ref'], self.body['source_ref'])
            self.assertEqual(parsed['error_code'], 'not_found')
            self.assertEqual(parsed['next_call'], self.body['next_call'])
            self.assertEqual(self.received[-1][1], self.args)
            self.assertEqual(body, messages[-1]['content'])
            self.assertFalse(self.prune(messages), 'Repeated pruning must be idempotent')

    def test_native_warning_and_hard_stop_survive_with_exact_recovery_and_cancelled_state(self):
        for action in ('warn', 'halt'):
            for bridge in (False, True):
                body = {**self.body, 'status': 'cancelled'}
                messages = self.messages(bridge, body)
                decision = ToolGuardrailDecision(action=action, code='idempotent_no_progress_warning',
                    count=2, message='Use the retained result; do not repeat.', tool_name=self.name)
                suffix = append_toolguard_guidance('', decision)
                messages[-1]['content'] = append_toolguard_guidance(messages[-1]['content'], decision)
                self.assertTrue(self.prune(messages))
                content = self.wire(messages)[-1]['content']
                self.assertTrue(content.endswith(suffix))
                self.assertIn('"status": "cancelled"', content)
                self.assertIn('immutable-version', content)
                self.assertIn('synthetic-source', content)
                self.assertEqual(json.loads(self.received[-1][2])['status'], 'cancelled')
                self.assertLessEqual(len(content), 2048)

    def test_arbitrary_trailing_text_and_oversized_runtime_guidance_are_not_discarded(self):
        for suffix in ('\n\nUnexpected diagnostic; preserve it.', '\n\n{"second": "record"}',
                       '\n\n[Tool loop warning: retry_warning; count=2; ' + 'x' * 2500 + ']'):
            messages = self.messages()
            messages[-1]['content'] += suffix
            before = messages[-1]['content']
            self.assertFalse(self.prune(messages))
            self.assertEqual(self.wire(messages)[-1]['content'], before)

    def test_malformed_bridge_wrong_name_missing_identity_and_multiple_results_stay_opaque(self):
        for problem in ('invalid-json', 'unknown-fields', 'wrong-name', 'missing-identity', 'wrong-pair-id', 'multiple-results', 'duplicate-call-id'):
            messages = self.messages()
            if problem == 'invalid-json':
                messages[1]['tool_calls'][0]['function']['arguments'] = '{broken'
            elif problem == 'unknown-fields':
                messages[1]['tool_calls'][0]['function']['arguments'] = json.dumps({'bogus': True})
            elif problem == 'wrong-name':
                messages[2]['tool_name'] = 'other-tool'
            elif problem == 'missing-identity':
                messages[2].pop('tool_name')
            elif problem == 'wrong-pair-id':
                messages[2]['tool_call_id'] = 'unpaired-result'
            elif problem == 'duplicate-call-id':
                messages[1]['tool_calls'].append(deepcopy(messages[1]['tool_calls'][0]))
            else:
                messages.append(deepcopy(messages[-1]))
            before = deepcopy(messages)
            self.assertFalse(self.prune(messages), problem)
            self.assertEqual(messages, before)
        self.assertEqual(self.received, [])

    def test_builtin_summary_preserves_guardrail_and_nonzero_exit_with_repeated_pruning(self):
        messages = self.messages(False)
        messages[1]['tool_calls'][0]['function'] = {
            'name': 'terminal', 'arguments': json.dumps({'command': 'synthetic-only'})}
        messages[2]['tool_name'] = 'terminal'
        messages[2]['content'] = append_toolguard_guidance(
            json.dumps({'exit_code': 137, 'output': 'x' * 8000}),
            ToolGuardrailDecision(action='halt', code='repeated_failure', count=4,
                                  message='Runtime halted; no automatic retry.'))
        self.assertTrue(self.prune(messages))
        content = self.wire(messages)[-1]['content']
        self.assertIn('exit 137', content)
        self.assertIn('Tool loop hard stop', content)
        self.assertFalse(self.prune(messages))

    def test_local_multi_call_bridge_never_borrows_one_result_identity(self):
        messages = self.messages()
        messages[1]['tool_calls'][0]['function']['arguments'] = json.dumps({'calls': [
            {'name': self.name, 'arguments': self.args}, {'name': self.name, 'arguments': {'path': '/other'}}]})
        self.assertFalse(self.prune(messages))
        self.assertEqual(self.received, [])

    def test_profile_registry_a_b_a_uses_only_current_profile_callback(self):
        other = hermes_home_key(str(Path(self.temp.name) / 'profile-b'))
        self.register(other, 'profile-b')
        self.addCleanup(registry.deregister, self.name, scope=other)
        for scope, label in ((self.scope, 'profile-a'), (other, 'profile-b'), (self.scope, 'profile-a')):
            token = set_hermes_home_override(scope)
            try:
                messages = self.messages()
                self.assertTrue(self.prune(messages))
                self.assertEqual(self.received[-1][0], label)
            finally:
                reset_hermes_home_override(token)
        registry.deregister(self.name, scope=other)
        token = set_hermes_home_override(other)
        try:
            before = len(self.received)
            messages = self.messages()
            self.prune(messages)
            self.assertEqual(len(self.received), before, 'Never use another profile callback')
        finally:
            reset_hermes_home_override(token)

    def test_lean_tail_and_pressure_pruning_agree_on_bridge_and_runtime_warning(self):
        messages = self.messages()
        decision = ToolGuardrailDecision(action='warn', code='retry_warning', count=2, message='Preserve warning')
        messages[-1]['content'] = append_toolguard_guidance(messages[-1]['content'], decision)
        for i in range(10):
            messages.extend([{'role': 'assistant', 'content': 'Later'},
                             {'role': 'tool', 'tool_call_id': str(i), 'content': 'Recent'}])
        compressor = object.__new__(ContextCompressor)
        compressor.quiet_mode = True
        compressor._session_id = ''
        before = deepcopy(messages)
        result = compressor._demote_stale_tail_tools(messages, 0)
        self.assertEqual(messages, before)
        self.assertIn('immutable-version', result[2]['content'])
        self.assertIn('Tool loop warning', result[2]['content'])
        self.assertEqual(result, compressor._demote_stale_tail_tools(result, 0))

    def test_real_plugin_absence_and_snapshot_recovery_survive_bridge_to_api_build(self):
        from forsic_plugin.context_results import summarize
        scoped_read = {'tool': 'forsic_reporting', 'arguments': {
            'action': 'state', 'question_id': 'Q-synthetic'}}
        for tool, body in (
            ('forsic_image_files', {**self.body, 'tool': 'forsic_image_files',
                'evidence_id': 'synthetic-source', 'error': 'Synthetic path absent',
                'file_path': '/synthetic/missing'}),
            ('forsic_reporting', {'tool': 'forsic_reporting', 'error': 'Stale synthetic snapshot',
                'error_code': 'state_snapshot_mismatch', 'write_applied': False,
                'failed_state': {'action': 'state', 'question_id': 'Q-synthetic',
                                 'snapshot_id': 'old-snapshot', 'pointer': '/questions/0/answer'},
                'next_call': scoped_read, 'padding': 'x' * 8000}),
        ):
            registry.deregister(self.name, scope=self.scope)
            registry.register(self.name, 'synthetic', {'parameters': {}},
                lambda **kwargs: self.fail('No execution'), scope=self.scope,
                result_summary=partial(summarize, tool))
            messages = self.messages(body=body)
            warning = ToolGuardrailDecision(action='warn', code='retry_warning', count=2,
                                           message='Retained recovery is available.')
            suffix = append_toolguard_guidance('', warning)
            messages[-1]['content'] = append_toolguard_guidance(messages[-1]['content'], warning)
            self.assertTrue(self.prune(messages))
            content = self.wire(messages)[-1]['content']
            self.assertTrue(content.endswith(suffix))
            crumb = json.loads(content.split('\n', 1)[1][:-len(suffix)])
            self.assertEqual(crumb['error_code'], body['error_code'])
            if tool == 'forsic_reporting':
                self.assertEqual(crumb['next_call'], scoped_read)
                self.assertEqual(crumb['failed_state'], body['failed_state'])
                self.assertFalse(crumb['write_applied'])
            else:
                self.assertEqual(crumb['lookup']['arguments']['source_version'], 'immutable-version')


if __name__ == '__main__':
    unittest.main()
