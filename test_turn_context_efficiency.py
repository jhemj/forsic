"""Registered Forsic hook through native user/API assembly; no model or network."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forsic_plugin import register
from forsic_plugin.intake import Intake, write_json
from forsic_plugin.investigation_context import post_context_compaction
from hermes_cli import plugins
from hermes_cli.plugins import PluginContext, PluginManager, format_system_prompt_sections
from hermes_cli.plugins_manifest import PluginManifest
import test_investigation_context as context_fixture


class TurnContextEfficiencyTests(unittest.TestCase):
    def test_memory_correction_reaches_native_turn_without_rewriting_frozen_prompt(self):
        from forsic_plugin.tenants import TenantRegistry, bind_scope
        from forsic_plugin.tenant_memory import MemoryService, native_policy, trusted_ui_source
        from tools.memory_tool_store import MemoryStore
        import uuid
        f = self.fixture
        registry = TenantRegistry(f.home)
        scope = registry.bootstrap_legacy(intake_root=f.intake, library_store=f.root/'library.sqlite3')
        with bind_scope(scope):
            store = MemoryStore(owner_policy=native_policy(scope), session_id=f.sid)
            store.load_from_disk()
            frozen = store.format_for_system_prompt('memory')
            saved = MemoryService(scope).mutate({
                'action':'add', 'operation_id':str(uuid.uuid4()),
                'content':'Synthetic inventory job role; scope and period unknown.',
            }, source=trusted_ui_source(scope))
            self.assertEqual(saved['status'], 'committed')
            agent = self.agent('warm')
            original_system = agent._cached_system_prompt
            context, wire = self.collect_and_send(agent, [
                {'role':'user','content':'Continue synthetic investigation'}])
            notice = json.loads(context)['memory_update']
            self.assertEqual(notice['loaded_snapshot_revision'], 0)
            self.assertGreater(notice['current_revision'], 0)
            self.assertEqual(notice['mode'], 'manual')
            self.assertNotIn('Synthetic inventory', context)
            self.assertEqual(agent._cached_system_prompt, original_system)
            self.assertEqual(store.format_for_system_prompt('memory'), frozen)
            self.assertIn('memory_update', wire[-1]['content'])

    def setUp(self):
        self.fixture = f = context_fixture.InvestigationContextTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.server_time = f.stack.enter_context(patch('forsic_plugin.runtime_clock.datetime'))
        self.server_time.now.return_value.astimezone.return_value = datetime(2032, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        from forsic_plugin.runtime_clock import current_context
        self.clock = current_context()
        self.procedure = (Path(__file__).parent / 'config' / 'AGENTS.md').read_text()
        self.manager = PluginManager(scope_key=str(f.home))
        self.manager._discovered = True
        ctx = PluginContext(PluginManifest(name='forsic'), self.manager)
        # Freeze a retained advisory result before registration captures the callback.
        f.stack.enter_context(patch('forsic_plugin.independent_review.latest_advice', return_value='한글 독립 검토 의견 상세 ' * 4000))
        with patch.object(ctx, 'get_config', side_effect=lambda key, default=None:
                          str(f.intake) if key == 'intake_root' else True if key == 'independent_review' else default):
            register(ctx)
        f.stack.enter_context(patch.object(plugins, 'get_plugin_manager', return_value=self.manager))
        f.stack.enter_context(patch.object(plugins, '_resolve_hook_callback_timeout', return_value=0))
        self.sections = tuple(self.manager.render_system_prompt_sections({'session_id': f.sid}))
        self.cap = 8000
        f.stack.enter_context(patch('tools.hook_output_spill.get_spill_config', return_value={
            'enabled': True, 'max_chars': self.cap, 'preview_head': 100, 'preview_tail': 100,
            'directory': str(f.home / 'hook_outputs')}))
        write_json(Intake(f.intake).path(f.sid), {'manifest': str(f.case.manifest),
            'stage': 'focused_question', 'question': 'Approved synthetic intake scope ' * 1600})
        manifest = json.loads(f.case.manifest.read_text())
        manifest['selected_files'] = [f'synthetic-only/{index:04d}-' + 'bounded-scope-' * 12 for index in range(300)]
        write_json(f.case.manifest, manifest)
        self.gap = 'CRITICAL: the observed request has no matching execution identity; successful execution remains unverified.'
        f.note['critical_gaps'] = [self.gap]
        f.note['answer'] = 'LATEST synthetic answer: a request alone does not establish execution.'
        f.save_note(priority='decision_critical')
        self.stop_reason = 'synthetic_read_error: partial returned data; continue from the exact retained field'
        self.failed_source = f.case.record('tool_result', {'tool': 'forsic_read', 'path': 'synthetic-failed.log',
            'complete_file': False, 'stop_reason': self.stop_reason, 'error': 'synthetic read failed'}, f.sid)

    def agent(self, mode):
        prompt = 'Original stable native system prompt'
        agent = SimpleNamespace(session_id=self.fixture.sid, model='synthetic-model', provider='custom',
            _current_turn_timestamp=1, ephemeral_system_prompt='',
            _copy_reasoning_content_for_api=lambda *_: None, _should_sanitize_tool_calls=lambda: False)
        if mode != 'legacy':
            prompt += '\n\n' + format_system_prompt_sections(self.sections) + '\n\nConversation started: synthetic'
        agent._cached_system_prompt = prompt
        if mode == 'restored':
            from agent.system_prompt import restore_plugin_prompt_sections
            restore_plugin_prompt_sections(agent, prompt)
        else:
            agent._plugin_system_prompt_sections_snapshot = self.sections if mode == 'warm' else ()
        return agent

    def collect_and_send(self, agent, messages):
        from agent.turn_context import _collect_pre_llm_call_context, _stamp_api_content_sidecar, build_api_messages
        index = len(messages) - 1
        context = _collect_pre_llm_call_context(agent, effective_task_id='synthetic-task', turn_id='synthetic-turn',
            original_user_message=messages[index]['content'], messages=messages, conversation_history=messages[:index])
        _stamp_api_content_sidecar(agent, messages, index, '', context, preflight_compressed=False)
        wire, system = build_api_messages(agent, messages, current_turn_user_idx=index, ext_prefetch_cache='',
            plugin_user_context=context, moa_config=None, active_system_prompt=agent._cached_system_prompt)
        self.assertEqual(system, agent._cached_system_prompt)
        self.assertEqual(wire[-1]['content'], messages[-1]['api_content'])
        self.assertEqual(messages[-1]['content'], 'Continue synthetic investigation')
        self.assertLessEqual(len(context), self.cap)
        self.assertNotIn('output truncated', context)
        self.assertFalse(list((self.fixture.home / 'hook_outputs').glob('**/*.txt')))
        return context, wire

    def test_whole_projection_survives_long_optional_context_and_replays_exact_prior_wire(self):
        f = self.fixture
        for mode in ('warm', 'restored', 'legacy'):
            with self.subTest(mode=mode):
                agent = self.agent(mode)
                past = [{'role': 'user', 'content': 'Old display ask', 'api_content': 'Old exact wire ask\n  preserved'},
                        {'role': 'assistant', 'content': 'Old display answer', 'api_content': 'Old exact wire answer\n  preserved'}]
                messages = deepcopy(past) + [{'role': 'user', 'content': 'Continue synthetic investigation'}]
                expected = post_context_compaction(session_id=f.sid, intake_root=f.intake, native_db=f.native, include_runtime_clock=False)
                context, wire = self.collect_and_send(agent, messages)
                if mode == 'legacy':
                    self.assertTrue(context.startswith(self.procedure + '\n'))
                    body = json.loads(context[len(self.procedure) + 1:])
                    self.assertEqual(body['principles'], 'legacy_fallback')
                else:
                    body = json.loads(context)
                    self.assertEqual(body['principles'], 'session_section')
                    self.assertEqual(body['runtime_clock'], self.clock)
                    self.assertNotIn(self.procedure, context)
                    self.assertEqual(wire[0]['content'].count(self.procedure), 1)
                expected_context = json.loads(expected['context'])
                if body['projection']['status']=='omitted':
                    self.assertEqual(mode, 'legacy')
                    self.assertTrue(body['omitted']['projection_body'])
                    projected = body['projection']
                    self.assertEqual(projected['answer_updates'], expected_context['answer_updates'])
                    self.assertEqual(projected['lookup']['arguments']['action'], 'state')
                else:
                    self.assertEqual(body['projection'], {**expected, 'context': expected_context})
                    projected = body['projection']['context']
                    question = projected['active_questions'][0]
                    self.assertEqual(question['revision'], f.note['revision'])
                    self.assertIn(self.gap, json.dumps(question, ensure_ascii=False))
                    self.assertIn(self.stop_reason, json.dumps(projected, ensure_ascii=False))
                self.assertIn('omitted', projected)
                self.assertTrue({'review_advice', 'intake', 'case'} <= set(body['omitted']))
                self.assertEqual(body['omitted']['review_advice']['lookup']['arguments']['action'], 'review_inbox')
                self.assertEqual(projected['answer_updates']['questions'][0]['question_id'], expected_context['active_questions'][0]['id'])
                self.assertEqual(projected['answer_updates']['questions'][0]['expected_revision'], f.note['revision'])
                self.assertEqual(projected['independent_review']['lookup']['arguments']['action'], 'review_inbox')
                for name in ('intake', 'case'):
                    self.assertEqual(body['omitted'][name]['lookup'],
                                     {'tool': 'forsic_reporting', 'arguments': {'action': 'case'}})
                self.assertEqual(messages[:2], past)
                self.assertEqual([row['content'] for row in wire[1:3]], [row['api_content'] for row in past])
                previous = deepcopy(messages)
                previous_wire = deepcopy(wire)
                f.note['answer'] = 'UPDATED current synthetic answer after the next retained inspection.'
                f.save_note(priority='decision_critical')
                messages += [{'role': 'assistant', 'content': 'Synthetic progress'},
                             {'role': 'user', 'content': 'Continue synthetic investigation'}]
                next_context, next_wire = self.collect_and_send(agent, messages)
                self.assertEqual(messages[:len(previous)], previous)
                self.assertEqual(next_wire[:len(previous_wire)], previous_wire)
                self.assertIn(str(f.note['revision']), next_context)
                current_body = json.loads(next_context[len(self.procedure) + 1:] if mode == 'legacy' else next_context)
                current_projected = current_body['projection'].get('context', current_body['projection'])
                self.assertEqual(current_projected['answer_updates']['questions'][0]['expected_revision'], f.note['revision'])
                self.assertEqual(agent._cached_system_prompt, previous_wire[0]['content'])

    def test_budget_omitted_projection_keeps_navigation_before_optional_context(self):
        from forsic_plugin.turn_context import assemble
        f = self.fixture
        projected = post_context_compaction(session_id=f.sid, intake_root=f.intake, native_db=f.native)
        expected = json.loads(projected['context'])
        for mode in ('warm', 'legacy'):
            with self.subTest(mode=mode):
                prefix = '' if mode=='warm' else self.procedure+'\n'
                frozen = tuple(section.id for section in self.sections) if mode=='warm' else ()
                context = assemble(self.procedure, section_ids=frozen,
                    max_chars=len(prefix)+3000, projection=projected, advice='의견 상세 '*4000)
                body = json.loads(context[len(prefix):])
                self.assertEqual(body['projection']['status'], 'omitted')
                self.assertEqual(body['projection']['answer_updates'], expected['answer_updates'])
                self.assertEqual(body['projection']['independent_review'], expected['independent_review'])
                self.assertEqual(body['omitted']['review_advice']['lookup']['arguments']['action'], 'review_inbox')

    def test_registered_compaction_snapshot_reaches_native_wire_without_a_new_user_turn(self):
        from agent import conversation_compression as compression
        from agent.context_compressor import SUMMARY_PREFIX, _SUMMARY_END_MARKER
        f = self.fixture
        snapshot = compression._prepare_plugin_context_snapshot(SimpleNamespace(session_id=f.sid))
        providers = json.loads(snapshot.splitlines()[-2])
        self.assertEqual(providers[0]['status'], 'ready')
        projected = json.loads(providers[0]['context'])
        question = projected['answer_updates']['questions'][0]
        self.assertEqual(question['question_id'], 'Q-'+f.note['note_id'])
        self.assertEqual(question['expected_revision'], f.note['revision'])
        self.assertEqual(projected['independent_review']['lookup']['arguments']['action'], 'review_inbox')
        messages = [{'role': 'assistant', 'content': SUMMARY_PREFIX+'\nSynthetic summary\n'+_SUMMARY_END_MARKER,
            '_compressed_summary': True, 'message_uid': 'synthetic-summary'},
            {'role': 'user', 'content': 'Continue synthetic investigation', 'message_uid': 'synthetic-user'}]
        original = deepcopy(messages)
        self.assertTrue(compression._fold_plugin_context_snapshot(messages, snapshot))
        self.assertEqual(len(messages), len(original))
        self.assertEqual(messages[1], original[1])
        self.assertEqual(messages[0]['message_uid'], original[0]['message_uid'])
        _, wire = self.collect_and_send(self.agent('warm'), messages)
        self.assertIn(snapshot, wire[1]['content'])
        self.assertEqual(wire[1]['content'].count(compression._PLUGIN_CONTEXT_OPEN), 1)

    def test_resumed_pinned_session_refreshes_clock_after_year_boundary(self):
        # Existing native section restored from a prior session: only the current
        # API sidecar changes; the cached prompt and prior wire bytes stay stable.
        agent = self.agent('restored')
        agent._cached_system_prompt += '\nOld model statement: current year is 2024.'
        original_system = agent._cached_system_prompt
        messages = [{'role': 'user', 'content': 'Continue synthetic investigation'}]
        context, first_wire = self.collect_and_send(agent, messages)
        self.assertEqual(json.loads(context)['runtime_clock']['current_datetime'], '2032-01-01T00:00:01+00:00')
        self.assertEqual(context.count('"runtime_clock"'), 1)
        self.server_time.now.return_value.astimezone.return_value = datetime(2041, 1, 1, tzinfo=timezone.utc)
        messages += [{'role': 'assistant', 'content': 'Synthetic progress'},
                     {'role': 'user', 'content': 'Continue synthetic investigation'}]
        refreshed, next_wire = self.collect_and_send(agent, messages)
        self.assertEqual(json.loads(refreshed)['runtime_clock']['current_datetime'], '2041-01-01T00:00:00+00:00')
        self.assertEqual(refreshed.count('"runtime_clock"'), 1)
        self.assertEqual(next_wire[:len(first_wire)], first_wire)
        self.assertEqual(agent._cached_system_prompt, original_system)
        # A compression boundary refreshes without invoking before_turn or writing
        # evidence/session state, even when no new user turn has arrived.
        from agent import conversation_compression as compression
        before = self.fixture.case.db.read_bytes()
        snapshot = compression._prepare_plugin_context_snapshot(agent)
        provider = json.loads(snapshot.splitlines()[-2])[0]
        state = json.loads(provider['context'])
        self.assertEqual(state['runtime_clock']['current_datetime'], '2041-01-01T00:00:00+00:00')
        self.assertEqual(provider['status'], 'ready')
        self.assertEqual(self.fixture.case.db.read_bytes(), before)

    def test_unavailable_projection_remains_explicit_in_the_native_api_message(self):
        f = self.fixture
        f.native.unlink()
        agent = self.agent('warm')
        context, wire = self.collect_and_send(agent, [{'role': 'user', 'content': 'Continue synthetic investigation'}])
        body = json.loads(context)
        self.assertEqual(body['projection']['status'], 'unavailable')
        self.assertTrue(body['projection']['reason'])
        self.assertIn('unavailable', wire[-1]['content'])
        self.assertNotIn('active_questions', body['projection'])


if __name__ == '__main__':
    unittest.main()
