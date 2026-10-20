"""Public progress uses native assistant content; no model or network calls."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent.message_metadata import append_message
from agent.turn_tool_round import run_tool_round
from hermes_state import SessionDB
from run_agent import AIAgent


class PublicBriefingTests(unittest.TestCase):
    def test_halt_notice_follows_profile_language_without_changing_guard_decision(self):
        from agent.i18n import reset_language_cache, t
        from agent.status_output import tool_guardrail_halt_reason
        from agent.tool_guardrails import ToolCallGuardrailController, ToolCallGuardrailConfig
        guard = ToolCallGuardrailController(ToolCallGuardrailConfig(
            hard_stop_enabled=True, same_tool_failure_halt_after=3))
        for offset in range(3):
            decision = guard.after_call('fixture_question', {'offset': offset}, 'invalid input', failed=True)
        self.assertTrue(decision.should_halt)
        self.assertEqual((decision.code, decision.count), ('same_tool_failure_halt', 3))
        metadata = decision.to_metadata()
        for lang in ('ko', 'en', 'ko'):
            home = self.root / lang
            home.mkdir(exist_ok=True)
            (home / 'config.yaml').write_text('display:\n  language: ' + lang + '\n')
            with patch.dict(os.environ, {'HERMES_HOME': str(home)}):
                reset_language_cache()
                text = self.agent._toolguard_controlled_halt_response(decision)
                self.assertEqual(text, t('display.notice.tool_guardrail_halt_detail', lang=lang,
                                         reason=tool_guardrail_halt_reason(decision)))
                self.assertIn('오류가 누적 3회' if lang == 'ko' else '3 accumulated failures', text)
                self.assertNotIn('진전 없이', text)
                self.assertNotIn(decision.code, text)
                self.assertEqual(decision.to_metadata(), metadata)
        reset_language_cache()

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': str(self.root)}))
        self.db = SessionDB(self.root / 'state.db')
        self.stack.callback(self.db.close)
        self.sid = 'synthetic-public-progress'
        self.db.create_session(self.sid, 'tui')
        # Real normalization, persistence and interim delivery without constructing a
        # provider client. Only tool execution and post-tool compaction are replaced.
        self.agent = AIAgent.__new__(AIAgent)
        self.agent.__dict__.update(
            quiet_mode=True, verbose_logging=False, valid_tool_names={'forsic_read'},
            _session_db=self.db, _session_db_created=True, session_id=self.sid,
            reasoning_callback=None, stream_delta_callback=None, _stream_callback=None,
            _tool_guardrail_halt_decision=None, _incremental_persistence_failed=False,
            _mute_post_response=False, _last_content_with_tools=None,
            _last_content_tools_all_housekeeping=False, _streamed_assistant_text='',
        )
        self.agent._cap_delegate_task_calls = lambda calls: calls
        self.agent._deduplicate_tool_calls = lambda calls: calls
        self.agent._has_stream_consumers = lambda: False
        self.agent._should_emit_quiet_tool_messages = lambda: False
        self.agent._needs_thinking_reasoning_pad = lambda: False
        self.agent._touch_activity = Mock()
        self.agent._enqueue_stream_hook = Mock()
        self.messages = [{'role': 'user', 'content': '합성 자료의 기록을 확인해주세요.'}]
        self.visible = []

        def interim(text, **kwargs):
            # Public commentary may only follow a durable assistant append.
            rows = self.db.get_messages_as_conversation(self.sid, include_row_ids=True)
            row = next(r for r in reversed(rows) if r['role'] == 'assistant')
            self.assertEqual(row['content'], text)
            self.visible.append((text, row['message_uid'], row['_row_id']))

        self.agent.interim_assistant_callback = interim

        def execute(message, messages, task_id, iteration):
            append_message(messages, {'role': 'tool', 'name': 'forsic_read',
                'tool_call_id': message.tool_calls[0].id, 'content': '{"lines": []}'})
            self.assertTrue(self.agent._flush_messages_to_session_db(messages, []))

        self.agent._execute_tool_calls = Mock(side_effect=execute)
        self.stack.enter_context(patch('agent.turn_tool_round.validate_tool_calls',
            return_value=SimpleNamespace(action='ok', mixed_invalid_batch=False)))
        self.stack.enter_context(patch('hermes_cli.observability.shared_metrics_harness.finish_tool_round'))
        self.stack.enter_context(patch('agent.turn_tool_round.compress_after_tool_results',
            side_effect=lambda agent, **kw: SimpleNamespace(**{
                key: kw[key] for key in ('messages', 'active_system_prompt', 'conversation_history',
                    'compression_attempts', 'final_response', 'turn_exit_reason', 'current_turn_user_idx')},
                end_turn=False)))

    def run_round(self, text):
        call = SimpleNamespace(id='call-' + str(len(self.messages)), call_id='call-' + str(len(self.messages)),
            response_item_id='fc-synthetic', type='function', function=SimpleNamespace(
                name='forsic_read', arguments=json.dumps({'path': 'fixture.log',
                    'reason': '요청 기록과 실행 결과를 구별해 확인합니다.'})))
        message = SimpleNamespace(content=text, tool_calls=[call],
            reasoning_content='PRIVATE_SYNTHETIC_REASONING')
        return run_tool_round(self.agent, assistant_message=message, finish_reason='tool_calls',
            messages=self.messages, conversation_history=[], api_call_count=1, effective_task_id=self.sid,
            user_message='합성 조사', system_message='stable system', active_system_prompt='stable system',
            compression_attempts=0, max_compression_attempts=1, final_response='', failed=False,
            _turn_exit_reason='', truncated_tool_call_retries=0, current_turn_user_idx=0)

    def test_korean_milestones_are_persisted_before_delivery_and_survive_reload(self):
        briefings = [
            '요청 기록은 확인했지만 실행 성공은 아직 확인되지 않았습니다. 다음으로 결과 로그를 대조합니다. [fixture.log:1]',
            '결과 로그를 읽지 못해 확인 범위를 조정했습니다. 보존된 요청 기록을 기준으로 다른 자료를 확인합니다.',
        ]
        for text in briefings:
            self.assertEqual(self.run_round(text).action, 'continue')
        self.assertEqual([v[0] for v in self.visible], briefings)
        cold = SessionDB(self.root / 'state.db')
        self.addCleanup(cold.close)
        restored = cold.get_messages_as_conversation(self.sid, include_row_ids=True)
        rows = [r for r in restored if r['role'] == 'assistant']
        self.assertEqual([(r['content'], r['message_uid'], r['_row_id']) for r in rows], self.visible)
        self.assertTrue(all(r['reasoning_content'] == 'PRIVATE_SYNTHETIC_REASONING' for r in rows))
        self.assertNotIn('PRIVATE_SYNTHETIC_REASONING', '\n'.join(v[0] for v in self.visible))
        self.assertEqual([r['role'] for r in restored], ['user', 'assistant', 'tool', 'assistant', 'tool'])
        from tui_gateway import server
        transcript = server._history_to_messages(restored, profile_home=self.root)
        public_rows = [r for r in transcript if r['role'] == 'assistant']
        self.assertEqual([(r['text'], r['row_id']) for r in public_rows],
                         [(text, row_id) for text, uid, row_id in self.visible])
        self.assertNotIn('PRIVATE_SYNTHETIC_REASONING', '\n'.join(r['text'] for r in public_rows))
        tool_rows = [r for r in transcript if r['role'] == 'tool']
        self.assertTrue(all(r['args']['reason'] == '요청 기록과 실행 결과를 구별해 확인합니다.'
                            for r in tool_rows))
        # Native markers prevent a later safety-net flush from duplicating a briefing.
        self.assertTrue(self.agent._flush_messages_to_session_db(restored, []))
        self.assertEqual(len(cold.get_messages(self.sid)), len(restored))

    def test_tool_only_round_does_not_invent_a_discovery_or_public_reasoning(self):
        self.assertEqual(self.run_round('').action, 'continue')
        self.assertEqual(self.visible, [])
        self.agent._execute_tool_calls.assert_called_once()

    def test_failed_canonical_append_emits_nothing_and_runs_no_tool(self):
        with patch.object(self.agent, '_flush_messages_to_session_db', return_value=False):
            verdict = self.run_round('확인 범위를 바꾸어 다음 검사를 진행합니다.')
        self.assertEqual(verdict.action, 'break')
        self.assertEqual(verdict._turn_exit_reason, 'session_persistence_failed')
        self.assertEqual(self.visible, [])
        self.agent._execute_tool_calls.assert_not_called()
        self.assertEqual(self.db.get_messages(self.sid), [])

    def test_native_tool_start_keeps_public_reason_in_existing_activity_path(self):
        from tui_gateway import server
        args = {'path': 'fixture.log', 'reason': '요청 기록과 실행 결과를 구별해 확인합니다.'}
        with patch.object(server, '_sessions', {self.sid: {}}), \
             patch.object(server, '_tool_progress_enabled', return_value=True), \
             patch.object(server, '_emit') as emit:
            server._on_tool_start(self.sid, 'call-activity', 'forsic_read', args)
        emit.assert_called_once()
        event, sid, payload = emit.call_args.args
        self.assertEqual((event, sid), ('tool.start', self.sid))
        self.assertEqual(payload['tool_id'], 'call-activity')
        self.assertEqual(payload['args'], args)


if __name__ == '__main__':
    unittest.main()
