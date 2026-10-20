"""Offline compaction projections: native commit/fence with a read-only provider.

The model summary and unrelated lifecycle setup are stubbed. Plugin dispatch,
projection folding, token growth guard and SQLite archive/active rows are real.
"""
import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from agent import conversation_compression as cc
from agent.context_compressor import ContextCompressor, _SUMMARY_END_MARKER, _MERGED_SUMMARY_DELIMITER, SUMMARY_PREFIX
from agent.model_metadata import estimate_messages_tokens_rough
from hermes_cli import plugins
from hermes_state import SessionDB


class PluginCompactionProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'HERMES_HOME': str(self.root)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.manager = plugins.PluginManager()
        self.manager._discovered = True
        self.manager_patch = patch.object(plugins, 'get_plugin_manager', return_value=self.manager)
        self.manager_patch.start(); self.addCleanup(self.manager_patch.stop)
        self.state = self.root / 'case-state.json'
        self.state.write_text(json.dumps({'revision': 'revision-2', 'active': ['mission-two']}))
        self.calls = []
        self.manager._hooks['post_context_compaction'] = [self.provider]
        self.sid = 'synthetic-owner'

    def provider(self, session_id, max_bytes):
        self.calls.append((session_id, max_bytes))
        current = json.loads(self.state.read_text())
        return {'provider': 'synthetic', 'status': 'ready', 'revision': current['revision'],
                'context': json.dumps(current, sort_keys=True)}

    def summary(self, body='Earlier observations remain uncertain.'):
        return {'role': 'assistant', 'content': SUMMARY_PREFIX + '\n' + body + '\n\n' + _SUMMARY_END_MARKER,
                '_compressed_summary': True, 'message_uid': 'summary-occurrence'}

    def test_refresh_is_single_stable_and_preserves_human_tool_identity(self):
        old = f'{cc._PLUGIN_CONTEXT_OPEN}\nobsolete mission-one\n{cc._PLUGIN_CONTEXT_CLOSE}'
        human = {'role': 'user', 'content': 'Investigate this scope', 'message_uid': 'human-uid', '_row_id': 4}
        tool = {'role': 'tool', 'content': 'observed bytes', 'tool_call_id': 'call-a', 'message_uid': 'tool-uid'}
        candidate = [self.summary('Old summary\n' + old), human, tool]
        original = copy.deepcopy(candidate)
        payload = cc._prepare_plugin_context_snapshot(SimpleNamespace(session_id=self.sid))
        self.assertTrue(cc._fold_plugin_context_snapshot(candidate, payload))
        self.assertEqual(candidate[1:], original[1:])
        self.assertEqual(candidate[0]['message_uid'], original[0]['message_uid'])
        self.assertEqual(candidate[0]['content'].count(cc._PLUGIN_CONTEXT_OPEN), 1)
        self.assertNotIn('obsolete mission-one', candidate[0]['content'])
        self.assertIn('mission-two', candidate[0]['content'])
        self.assertIn('not original evidence', candidate[0]['content'])
        refreshed = copy.deepcopy(candidate)
        cc._fold_plugin_context_snapshot(candidate, cc._prepare_plugin_context_snapshot(SimpleNamespace(session_id=self.sid)))
        self.assertEqual(candidate, refreshed)
        self.assertEqual(self.calls, [(self.sid, 6000), (self.sid, 6000)])
        self.assertGreater(estimate_messages_tokens_rough(candidate), estimate_messages_tokens_rough(original))

    def test_swallowed_provider_failure_is_explicit_unavailable_not_empty(self):
        self.manager._hooks['post_context_compaction'] = [lambda **_: (_ for _ in ()).throw(OSError('synthetic read failure'))]
        payload = cc._prepare_plugin_context_snapshot(SimpleNamespace(session_id=self.sid))
        self.assertIn('"status": "unavailable"', payload)
        self.assertIn('not an empty or completed task list', payload)
        self.assertNotIn('synthetic read failure', payload)
        candidate = [self.summary()]
        self.assertTrue(cc._fold_plugin_context_snapshot(candidate, payload))
        self.assertEqual(len(candidate), 1)

    def test_provider_budget_is_utf8_and_never_silently_truncates_tasks(self):
        self.manager._hooks['post_context_compaction'] = [lambda **_: {
            'provider': 'synthetic', 'status': 'ready', 'revision': 'revision-2', 'context': '한' * 3000}]
        payload = cc._prepare_plugin_context_snapshot(SimpleNamespace(session_id=self.sid))
        self.assertIn('unavailable', payload)
        self.assertLess(len(payload.encode('utf-8')), 6000)
        self.assertNotIn('한', payload)

    def test_not_applicable_removes_stale_state_and_no_hook_is_noop(self):
        candidate = [self.summary()]
        cc._fold_plugin_context_snapshot(candidate, cc._prepare_plugin_context_snapshot(SimpleNamespace(session_id=self.sid)))
        self.manager._hooks['post_context_compaction'] = [lambda **_: {'provider': 'synthetic', 'status': 'not_applicable'}]
        self.assertEqual(cc._prepare_plugin_context_snapshot(SimpleNamespace(session_id=self.sid)), '')
        cc._fold_plugin_context_snapshot(candidate, '')
        self.assertNotIn(cc._PLUGIN_CONTEXT_OPEN, candidate[0]['content'])
        self.manager._hooks.clear()
        before = copy.deepcopy(candidate)
        payload = cc._prepare_plugin_context_snapshot(SimpleNamespace(session_id=self.sid))
        self.assertIsNone(payload)
        self.assertTrue(cc._fold_plugin_context_snapshot(candidate, payload))
        self.assertEqual(candidate, before)

    def test_native_summary_end_marker_survives_stable_refresh_for_both_content_shapes(self):
        payload = cc._prepare_plugin_context_snapshot(SimpleNamespace(session_id=self.sid))
        for as_list in (False, True):
            with self.subTest(as_list=as_list):
                content = SUMMARY_PREFIX + '\nNative handoff.\n\n' + _SUMMARY_END_MARKER
                summary = self.summary()
                summary['content'] = [{'type': 'text', 'text': content}] if as_list else content
                candidate = [summary]
                cc._fold_plugin_context_snapshot(candidate, payload)
                self.assertTrue(cc._message_text(candidate[0]).endswith(_SUMMARY_END_MARKER))
                first = copy.deepcopy(candidate)
                cc._fold_plugin_context_snapshot(candidate, payload)
                self.assertEqual(candidate, first)
                self.assertEqual(len(cc._plugin_context_blocks(candidate)), 1)

    def test_summary_like_human_content_is_not_a_synthetic_carrier(self):
        candidate = [{'role': 'user', 'content': SUMMARY_PREFIX + '\nQuoted reference', 'message_uid': 'human'}]
        original = copy.deepcopy(candidate)
        payload = cc._prepare_plugin_context_snapshot(SimpleNamespace(session_id=self.sid))
        self.assertFalse(cc._fold_plugin_context_snapshot(candidate, payload))
        self.assertEqual(candidate, original)

    def test_native_merged_carriers_preserve_live_text_media_uid_and_alternation(self):
        compressor = ContextCompressor.__new__(ContextCompressor)
        compressor._summary_has_user_turn = True
        for leading in (False, True):
            for as_list in (False, True):
                with self.subTest(leading=leading, as_list=as_list):
                    content = 'Current human request; do not reinterpret me.'
                    if as_list:
                        content = [{'type': 'text', 'text': content},
                                   {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,synthetic'}}]
                    carrier = {'role': 'user', 'content': content, 'message_uid': 'live-carrier', '_row_id': 10}
                    compressor._merge_summary_into_tail_row(carrier, SUMMARY_PREFIX + '\nEarlier context.', 'user', leading)
                    original_live = ContextCompressor._strip_context_summary_handoff_message(carrier)
                    candidate = [carrier]
                    payload = cc._prepare_plugin_context_snapshot(SimpleNamespace(session_id=self.sid))
                    self.assertTrue(cc._fold_plugin_context_snapshot(candidate, payload))
                    self.assertEqual(ContextCompressor._strip_context_summary_handoff_message(candidate[0]), original_live)
                    self.assertEqual(candidate[0]['message_uid'], 'live-carrier')
                    self.assertEqual([m['role'] for m in candidate], ['user'])
                    before = copy.deepcopy(candidate)
                    cc._fold_plugin_context_snapshot(candidate, payload)
                    self.assertEqual(candidate, before)
                    self.assertEqual(len(cc._plugin_context_blocks(candidate)), 1)

    def test_provider_quoted_delimiters_do_not_break_single_projection_replacement(self):
        self.manager._hooks['post_context_compaction'] = [lambda **_: {
            'provider': 'synthetic', 'status': 'ready', 'revision': 'revision-2',
            'context': cc._PLUGIN_CONTEXT_OPEN + _SUMMARY_END_MARKER + _MERGED_SUMMARY_DELIMITER + cc._PLUGIN_CONTEXT_CLOSE}]
        payload = cc._prepare_plugin_context_snapshot(SimpleNamespace(session_id=self.sid))
        candidate = [self.summary()]
        cc._fold_plugin_context_snapshot(candidate, payload)
        before = copy.deepcopy(candidate)
        cc._fold_plugin_context_snapshot(candidate, payload)
        self.assertEqual(candidate, before)
        self.assertEqual(len(cc._plugin_context_blocks(candidate)), 1)
        self.assertEqual(candidate[0]['content'].count(_SUMMARY_END_MARKER), 1)
        self.assertNotIn(_MERGED_SUMMARY_DELIMITER, candidate[0]['content'])

    def test_native_growth_salvage_cannot_drop_current_projection(self):
        original = [{'role': 'user', 'content': 'old context ' * 1000}]
        candidate = [self.summary('new oversized handoff ' * 1000 + '\n\n' + _SUMMARY_END_MARKER),
                     {'role': 'user', 'content': 'Continue'}]
        payload = cc._prepare_plugin_context_snapshot(SimpleNamespace(session_id=self.sid))
        cc._fold_plugin_context_snapshot(candidate, payload)
        agent = SimpleNamespace(session_id=self.sid, _cached_system_prompt='system', _emit_warning=Mock(),
                                context_compressor=SimpleNamespace(record_rejected_compaction=Mock()))
        from agent.context_compressor import salvage_grown_transcript
        salvaged = salvage_grown_transcript(original, candidate)
        self.assertIsNotNone(salvaged)
        self.assertNotEqual(cc._plugin_context_blocks(salvaged), cc._plugin_context_blocks(candidate))
        with patch.object(cc, '_existing_system_prompt', return_value='system'), \
                patch.object(cc, '_emit_aborted_attempt_telemetry'):
            accepted, prompt = cc._salvage_or_refuse_grown_transcript(agent, original, candidate,
                system_message='system', attempt_started_at=time.monotonic(), attempt_snapshot={})
        self.assertIsNone(accepted)
        self.assertEqual(prompt, 'system')
        agent.context_compressor.record_rejected_compaction.assert_called_once()

    def run_boundary(self, *, cancel=False, fail_commit=False, no_summary=False, use_fence=True):
        db = SessionDB(db_path=self.root / 'native.db')
        self.addCleanup(db.close)
        db.create_session(self.sid, 'test')
        db.append_message(self.sid, 'user', 'Previous large context ' * 2000, message_uid='prior-human')
        db.append_message(self.sid, 'assistant', 'Previous response ' * 2000, message_uid='prior-assistant')
        db.append_message(self.sid, 'user', 'Continue investigation', message_uid='current-human')
        messages = db.get_messages_as_conversation(self.sid, include_row_ids=True)
        original = copy.deepcopy(messages)
        candidate = ([self.summary()] if not no_summary else []) + [copy.deepcopy(messages[-1])]
        compressor = SimpleNamespace(compression_count=1, _last_compression_made_progress=True,
            _last_summary_fallback_used=False, _last_feasibility_skip=False)
        agent = SimpleNamespace(session_id=self.sid, api_mode='chat_completions', compression_in_place=True,
            _compression_feasibility_checked=True, context_compressor=compressor, _session_db=db,
            _hard_interrupt_requested=threading.Event(), commit_memory_session=Mock(), _cached_system_prompt='system',
            _todo_store=SimpleNamespace(format_for_injection=lambda: '', has_items=lambda: False), tools=[])
        attempt = SimpleNamespace(started_at=time.monotonic(), snapshot={}, generation=None, restore_compressor=Mock())
        lease = SimpleNamespace(watermark=max(m['_row_id'] for m in messages), holder=None,
                                finish_lock_setup=Mock(), release=Mock())
        phase = SimpleNamespace(abort_prompt=None, messages=messages, compressed=candidate,
            messages_before_compression=copy.deepcopy(messages), approx_tokens=20000, pre_msg_count=len(messages))
        fence = cc.CompressionCommitFence() if use_fence else None
        if cancel:
            def cancelling_provider(session_id, max_bytes):
                result = self.provider(session_id, max_bytes)
                if fence is not None:
                    self.assertTrue(fence.cancel_before_commit(agent._hard_interrupt_requested))
                else:
                    agent._hard_interrupt_requested.set()
                return result
            self.manager._hooks['post_context_compaction'] = [cancelling_provider]
        before_case = self.state.read_bytes()
        stubs = {'_begin_compression_attempt': attempt, '_announce_compression_start': SimpleNamespace(commit_status='aborted'),
            '_acquire_compression_lease': (lease, None), '_adopt_if_parent_rotated': None,
            '_capture_authoritative_cooldown_under_lease': (None, None), '_run_summary_phase': phase,
            '_candidate_rejected': False, '_warn_summary_or_aux_fallback': None,
            '_ensure_compressed_has_user_turn': 'preserved', '_rebuild_system_prompt_at_boundary': 'system',
            '_finish_compaction_boundary': 123, '_emit_compression_attempt_telemetry': None,
            '_emit_aborted_attempt_telemetry': None, '_record_stall_interrupted_backoff': False,
            '_existing_system_prompt': 'system'}
        with ExitStack() as stack:
            for name, value in stubs.items(): stack.enter_context(patch.object(cc, name, return_value=value))
            stack.enter_context(patch('agent.conversation_compression_reply_anchor._ensure_compressed_keeps_last_assistant_reply', return_value=None))
            if fail_commit: stack.enter_context(patch.object(db, 'archive_and_compact', side_effect=RuntimeError('synthetic failed transaction')))
            returned, prompt = cc.compress_context(agent, messages, 'system', force=True, commit_fence=fence)
        self.assertEqual(prompt, 'system')
        self.assertEqual(self.state.read_bytes(), before_case)
        self.assertEqual(agent.session_id, self.sid)
        self.assertEqual(self.calls, [(self.sid, 6000)])
        lease.release.assert_called_once()
        return db, original, returned, attempt

    def test_successful_native_compaction_persists_projection_once_and_keeps_active_uid(self):
        db, original, returned, _ = self.run_boundary()
        active = db.get_messages_as_conversation(self.sid, include_row_ids=True)
        self.assertEqual([m['role'] for m in active], ['assistant', 'user'])
        self.assertEqual(active[-1]['message_uid'], original[-1]['message_uid'])
        self.assertEqual(active[-1]['content'], original[-1]['content'])
        self.assertEqual(sum(m['content'].count(cc._PLUGIN_CONTEXT_OPEN) for m in active), 1)
        self.assertIn('revision-2', active[0]['content'])
        self.assertEqual(active[0]['message_uid'], returned[0]['message_uid'])
        self.assertEqual(len(db.get_messages_as_conversation(self.sid, include_inactive=True)), 5)

    def test_cancel_during_provider_read_keeps_native_active_rows_and_case_state(self):
        db, original, returned, attempt = self.run_boundary(cancel=True)
        self.assertEqual(returned, original)
        self.assertEqual(db.get_messages_as_conversation(self.sid, include_row_ids=True), original)
        attempt.restore_compressor.assert_called_once()

    def test_native_transaction_failure_keeps_original_active_rows(self):
        db, original, returned, _ = self.run_boundary(fail_commit=True)
        self.assertEqual(returned, original)
        self.assertEqual(db.get_messages_as_conversation(self.sid, include_row_ids=True), original)

    def test_hard_cancel_during_provider_read_without_fence_keeps_native_state(self):
        db, original, returned, attempt = self.run_boundary(cancel=True, use_fence=False)
        self.assertEqual(returned, original)
        self.assertEqual(db.get_messages_as_conversation(self.sid, include_row_ids=True), original)
        attempt.restore_compressor.assert_called_once()

    def test_custom_engine_without_summary_cannot_rewrite_human_as_projection(self):
        db, original, returned, _ = self.run_boundary(no_summary=True)
        self.assertEqual(returned, original)
        self.assertEqual(db.get_messages_as_conversation(self.sid, include_row_ids=True), original)


if __name__ == '__main__':
    unittest.main()
