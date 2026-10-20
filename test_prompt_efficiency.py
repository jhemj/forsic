"""Offline registered-hook payload efficiency without changing retained evidence."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forsic_plugin import register
from forsic_plugin.context_results import summarize
from forsic_plugin.intake import Intake, write_json
from hermes_cli.plugins import PluginContext, PluginManager
from hermes_cli.plugins_manifest import PluginManifest
import test_investigation_context as context_fixture


class PromptEfficiencyTests(unittest.TestCase):
    def test_forsic_native_gates_remove_coding_briefs_and_preserve_autonomous_tool_guidance(self):
        from ruamel.yaml import YAML
        from agent.coding_context import resolve_runtime_mode
        from agent.prompt_builder import (OPENAI_MODEL_EXECUTION_GUIDANCE,
            PARALLEL_TOOL_CALL_GUIDANCE, STEER_CHANNEL_NOTE,
            TASK_COMPLETION_GUIDANCE, TOOL_USE_ENFORCEMENT_GUIDANCE)
        from agent.system_prompt import _guidance_parts
        from deploy_forsic import initial_config

        example = YAML(typ='safe').load((Path(__file__).parent / 'config/config.example.yaml').read_text())
        config = initial_config('http://127.0.0.1:11434/v1', 'gpt-synthetic')
        for key in ('coding_context', 'execution_guidance'):
            self.assertEqual(config['agent'][key], example['agent'][key])
        agent = SimpleNamespace(valid_tool_names={'forsic_read'}, model='gpt-synthetic',
            _tool_use_enforcement='auto', _execution_guidance=config['agent']['execution_guidance'])
        actual = [part for part in _guidance_parts(agent) if part]
        for retained in (TASK_COMPLETION_GUIDANCE, PARALLEL_TOOL_CALL_GUIDANCE,
                         STEER_CHANNEL_NOTE, TOOL_USE_ENFORCEMENT_GUIDANCE):
            self.assertIn(retained, actual)
        self.assertNotIn(OPENAI_MODEL_EXECUTION_GUIDANCE, actual)
        agent._execution_guidance = 'auto'
        baseline = [part for part in _guidance_parts(agent) if part]
        self.assertEqual(baseline, actual + [OPENAI_MODEL_EXECUTION_GUIDANCE])

        # The native coding gate controls posture, not the configured Forsic tools.
        # An off posture must not even probe Git/the workspace for a prompt snapshot.
        with patch('agent.coding_context.build_coding_workspace_block',
                   side_effect=AssertionError('investigation posture must not probe a coding workspace')):
            mode = resolve_runtime_mode(platform='desktop', cwd=Path(__file__).parent,
                                        config=config, model=agent.model)
            self.assertFalse(mode.is_coding)
            self.assertIsNone(mode.toolset_selection(config))
            self.assertEqual(mode.system_prompt_parts(valid_tool_names=agent.valid_tool_names), ([], [], []))
            prior = resolve_runtime_mode(platform='desktop', cwd=Path(__file__).parent,
                config={**config, 'agent': {**config['agent'], 'coding_context': 'on'}}, model=agent.model)
            self.assertTrue(prior.is_coding)
            coding = prior.system_prompt_parts(valid_tool_names=agent.valid_tool_names, workspace_block='')
            self.assertTrue(coding[0])
        self.assertTrue(all(toolsets == ['forsic', 'clarify'] for toolsets in config['platform_toolsets'].values()))
        print('Native generic/coding brief chars omitted:', len(OPENAI_MODEL_EXECUTION_GUIDANCE),
              '+', sum(len(part) for parts in coding for part in parts))

    def test_registered_turn_hook_preserves_projection_as_json_value_and_unavailable_state(self):
        fixture = context_fixture.InvestigationContextTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        binding = Intake(fixture.intake).path(fixture.sid)
        write_json(binding, {'manifest': str(fixture.case.manifest), 'stage': 'goal_active'})
        manager = PluginManager(scope_key=str(fixture.home))
        ctx = PluginContext(PluginManifest(name='forsic'), manager)
        with patch.object(ctx, 'get_config', side_effect=lambda key, default=None:
                          str(fixture.intake) if key == 'intake_root' else default):
            register(ctx)
        # The real provider resolves session ownership and reads the synthetic ledger.
        from forsic_plugin.investigation_context import post_context_compaction
        expected = post_context_compaction(session_id=fixture.sid, intake_root=fixture.intake,
                                            native_db=fixture.native, include_runtime_clock=False)
        self.assertEqual(expected['status'], 'ready')
        original = deepcopy(expected)
        sections = manager.render_system_prompt_sections({'session_id': fixture.sid})
        frozen_ids = tuple(section.id for section in sections)
        first = manager.invoke_hook('pre_llm_call', session_id=fixture.sid, user_message='Continue', system_prompt_section_ids=frozen_ids)[0]['context']
        envelope = json.loads(first)
        self.assertEqual(envelope['kind'], 'forsic_turn_context')
        self.assertEqual(envelope['principles'], 'session_section')
        actual = envelope['projection']
        self.assertEqual(actual, {**expected, 'context': json.loads(expected['context'])})
        self.assertEqual(expected, original)
        old = json.dumps(expected, ensure_ascii=False)
        new = json.dumps(actual, ensure_ascii=False, separators=(',', ':'))
        self.assertLess(len(new.encode()), len(old.encode()))
        self.assertIsInstance(actual['context']['active_questions'], list)
        self.assertIn('operation_id', actual['context']['answer_updates']['required_update_fields'])
        compacted = manager.invoke_hook('post_context_compaction', session_id=fixture.sid)[0]
        compacted_context = json.loads(compacted['context'])
        self.assertEqual(compacted_context['answer_updates'], actual['context']['answer_updates'])
        self.assertEqual(compacted_context['independent_review']['lookup']['arguments']['action'], 'review_inbox')
        # Repeated user turns must render the same owned projection without changing it.
        again = manager.invoke_hook('pre_llm_call', session_id=fixture.sid, user_message='Continue', system_prompt_section_ids=frozen_ids)[0]['context']
        self.assertEqual(json.loads(again)['projection'], actual)
        fixture.native.unlink()
        unavailable = manager.invoke_hook('pre_llm_call', session_id=fixture.sid, user_message='Continue', system_prompt_section_ids=frozen_ids)[0]['context']
        self.assertEqual(json.loads(unavailable)['projection']['status'], 'unavailable')
        print('Synthetic projection envelope bytes:', len(old.encode()), '->', len(new.encode()))

    def test_compact_read_crumb_retains_lookup_scope_error_and_original_body(self):
        args = {'path': 'fixture.log', 'start_line': 9}
        body = {'source_ref': {'id': 'source-synthetic', 'version': 'version-synthetic'},
                'evidence_id': 'source-synthetic', 'text': 'retained original\n' * 300,
                'complete_file': False, 'error': 'partial read failed', 'exit_code': 2}
        original = deepcopy(body)
        result = json.loads(summarize('forsic_read', args, json.dumps(body)))
        self.assertEqual(result['lookup'], {'tool': 'forsic_reporting', 'arguments': {
            'action': 'source', 'source_id': 'source-synthetic', 'source_version': 'version-synthetic'}})
        self.assertEqual(result['requested_scope'], args)
        self.assertFalse(result['returned_scope']['complete_file'])
        self.assertEqual(result['error'], body['error'])
        self.assertTrue(result['text_fields_present_in_original_message'])
        self.assertTrue(result['body_omitted'])
        self.assertNotIn('text', result)
        self.assertEqual(body, original)
        # Compare the identical semantic payload with the former explanatory wording.
        old = {**result, 'meaning': 'Body omitted from this compacted view, not evidence that the earlier tool message lacked body. This is neither current judgment nor semantic approval; use preserved current calls/state and retrieve only the uncertain source span.'}
        encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
        self.assertLess(len(encode(result)), len(encode(old)))
        print('Synthetic partial-read crumb bytes:', len(encode(old)), '->', len(encode(result)))

    def test_advice_bearing_read_pruning_retains_bounded_inbox_navigation_only(self):
        body = {'source_ref': {'id': 'synthetic-source', 'version': 'synthetic-version'},
            'text': '원문은 그대로 보존됩니다. '*1000,
            'independent_review': {'status': 'received', 'review_id': 'synthetic-review',
                'pending_finding_ids': ['finding-one', 'finding-two'], 'requires_followup': True,
                'context': '긴 독립 검토 의견 '*3000}}
        original = deepcopy(body)
        pruned = summarize('forsic_read', {'path': 'synthetic.log'}, json.dumps(body))
        view = json.loads(pruned)
        review = view['independent_review']
        self.assertEqual(review['lookup'], {'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox'}})
        self.assertEqual(review['review_id'], 'synthetic-review')
        self.assertEqual(review['pending_count'], 2)
        self.assertTrue(review['requires_followup'])
        self.assertTrue(review['advice_body_omitted'])
        self.assertNotIn('context', review)
        self.assertLessEqual(len(pruned), 2000)
        self.assertEqual(body, original)
        # A read without delivered advice gains no synthetic reviewer reminder.
        body.pop('independent_review')
        self.assertNotIn('independent_review', json.loads(summarize('forsic_read', {}, json.dumps(body))))
        body['independent_review'] = {'status': 'unavailable', 'pending_finding_ids': [], 'requires_followup': False}
        review = json.loads(summarize('forsic_read', {}, json.dumps(body)))['independent_review']
        self.assertIsNone(review['pending_count'])
        self.assertNotIn('requires_followup', review)


if __name__ == '__main__':
    unittest.main()
