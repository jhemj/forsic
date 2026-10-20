"""Native advisory checkpoints use the existing evaluator and respect user pause."""
import unittest
import logging
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from test_native_goal_evaluation import NativeGoalEvaluationTests
from hermes_cli import plugins

REAL_PLUGIN_MANAGER = plugins.get_plugin_manager


class GoalAdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.x = NativeGoalEvaluationTests()
        self.x.setUp()
        self.addCleanup(self.x.doCleanups)

    def test_blind_checkpoint_advice_reaches_judge_once(self):
        calls = []
        def reviewer(**args):
            calls.append(args)
            return {'provider': 'synthetic', 'status': 'received',
                    'context': '선택 목록의 작은 설정 파일은 본문 미검토입니다.'}
        self.x.plugins._hooks['goal_advisory_context'] = [reviewer]
        self.x.hook(self.x.ready)
        decision = self.x.manager.evaluate_after_turn('PRIVATE_MAIN_CONCLUSION')
        self.assertEqual(decision['status'], 'done')
        self.assertEqual(len(calls), 1)  # completion validation does not repeat review
        self.assertNotIn('PRIVATE_MAIN_CONCLUSION', str(calls))
        self.assertEqual(calls[0]['turns_used'], 1)
        self.assertIn('본문 미검토', self.x.judge.call_args.kwargs['evaluation_context'])
        self.assertNotIn('선택 목록', self.x.judge.call_args.args[1])

    def test_pause_during_review_prevents_judge_and_continuation(self):
        def reviewer(**args):
            self.x.manager.pause('user-paused')
            self.assertTrue(args['cancel_check']())
            return {'provider': 'synthetic', 'status': 'cancelled', 'context': ''}
        self.x.plugins._hooks['goal_advisory_context'] = [reviewer]
        decision = self.x.manager.evaluate_after_turn('Finished')
        self.assertEqual(decision['status'], 'paused')
        self.assertFalse(decision['should_continue'])
        self.x.judge.assert_not_called()
        self.assertEqual(self.x.manager.state.paused_reason, 'user-paused')

    def test_unavailable_advice_is_disclosed_without_fabricated_approval(self):
        def unavailable(**args):
            raise OSError('test-only unavailable')
        self.x.plugins._hooks['goal_advisory_context'] = [unavailable]
        self.x.manager.evaluate_after_turn('Partial')
        self.assertIn('검토 통과를 뜻하지 않습니다', self.x.judge.call_args.kwargs['evaluation_context'])


    def pending(self, **kwargs):
        return {'provider': 'synthetic', 'status': 'received', 'review_id': 'review-1',
                'requires_followup': True, 'pending_finding_ids': ['finding-1'],
                'context': 'Compare the unopened source against the claimed conclusion.'}

    def test_pending_advice_goes_to_main_before_judge_can_finish(self):
        self.x.plugins._hooks['goal_advisory_context'] = [self.pending]
        for _ in range(4):
            decision = self.x.manager.evaluate_after_turn('Complete')
            self.assertEqual(decision['status'], 'active')
            self.assertTrue(decision['should_continue'])
            self.assertIn('review-1', decision['continuation_prompt'])
            self.assertIn('finding-1', decision['continuation_prompt'])
        self.x.judge.assert_not_called()
        self.assertEqual(self.x.manager.state.turns_used, 4)

    def test_handled_followup_allows_judge_and_legacy_advice_stays_advisory(self):
        for requires in (False, None):
            with self.subTest(requires=requires):
                self.x.manager.set('Synthetic question')
                value = self.pending()
                if requires is None:
                    value.pop('requires_followup')
                else:
                    value['requires_followup'] = requires
                self.x.plugins._hooks['goal_advisory_context'] = [lambda **_: value]
                self.assertEqual(self.x.manager.evaluate_after_turn('Complete')['status'], 'done')
        self.assertEqual(self.x.judge.call_count, 2)

    def test_pending_advice_respects_max_turn_pause(self):
        self.x.manager.state.max_turns = 1
        self.x.manager._save()
        self.x.plugins._hooks['goal_advisory_context'] = [self.pending]
        decision = self.x.manager.evaluate_after_turn('Complete')
        self.assertEqual(decision['status'], 'paused')
        self.assertFalse(decision['should_continue'])
        self.assertEqual(self.x.manager.state.last_verdict, 'continue')
        self.x.judge.assert_not_called()

    def _agent(self):
        return SimpleNamespace(session_id=self.x.manager.session_id, _session_db=self.x.db,
            _interrupt_requested=False, _emit_interim_assistant_message=Mock(),
            _flush_messages_to_session_db=Mock(), _interim_content_was_streamed=Mock(return_value=True),
            max_iterations=2, iteration_budget=SimpleNamespace(remaining=0), quiet_mode=True,
            _handle_max_iterations=Mock(), _emit_diagnostic_status=Mock())

    def _stop(self, agent=None, candidate='Candidate conclusion'):
        from agent.turn_stop_gates import apply_stop_gates
        agent = agent or self._agent()
        messages = [{'role': 'system', 'content': 'Byte-stable system'},
                    {'role': 'user', 'content': 'Synthetic investigation'}]
        before = deepcopy(messages)
        final_msg = {'role': 'assistant', 'content': candidate}
        verdict = apply_stop_gates(agent, final_msg, final_response=candidate, messages=messages,
            conversation_history=[], pending_verification_response=None,
            pending_verification_response_previewed=False)
        self.assertEqual(messages[:2], before)
        return agent, messages, final_msg, verdict

    def test_final_gate_passes_candidate_before_delivery_as_interim(self):
        events, calls = [], []
        agent = self._agent()
        def review(**kwargs):
            events.append('review')
            calls.append(kwargs)
            return self.pending()
        self.x.plugins._hooks['goal_advisory_context'] = [review]
        agent._emit_interim_assistant_message.side_effect = lambda *_: events.append('interim')
        agent._flush_messages_to_session_db.side_effect = lambda *_: events.append('persist')
        _, messages, candidate, verdict = self._stop(agent)
        self.assertEqual(events, ['review', 'interim', 'persist'])
        self.assertTrue(verdict.continue_turn)
        self.assertIsNone(verdict.final_response)
        self.assertEqual(candidate['finish_reason'], 'goal_advisory_required')
        self.assertEqual([m['role'] for m in messages], ['system', 'user', 'assistant', 'user'])
        self.assertEqual(messages[-2]['content'], 'Candidate conclusion')
        self.assertIn('finding-1', messages[-1]['content'])
        self.assertEqual(calls[0]['stage'], 'final')
        self.assertEqual(calls[0]['draft_response'], 'Candidate conclusion')
        self.assertNotEqual(verdict.pending_verification_response, 'Candidate conclusion')
        self.assertFalse(verdict.pending_verification_response_previewed)

    def test_final_gate_budget_returns_incomplete_checkpoint_and_keeps_candidate_interim(self):
        from agent.turn_finalizer import (_resolve_budget_fallback,
            _drop_verification_continuation_scaffolding, _close_transcript_tail)
        self.x.plugins._hooks['goal_advisory_context'] = [self.pending]
        with patch.dict('os.environ', {'HERMES_LANGUAGE': 'ko'}):
            agent, messages, _, verdict = self._stop()
        response, reason, preserved, interrupted = _resolve_budget_fallback(agent,
            final_response=None, api_call_count=2, interrupted=False, failed=False,
            messages=messages, _turn_exit_reason='budget_exhausted',
            _pending_verification_response=verdict.pending_verification_response,
            _pending_verification_response_previewed=verdict.pending_verification_response_previewed,
            logger=logging.getLogger(__name__))
        self.assertIn('완료되지 않았습니다', response)
        self.assertNotIn('Candidate conclusion', response)
        self.assertEqual(reason, 'max_iterations_reached(2/2)')
        self.assertFalse(interrupted)
        self.assertTrue(preserved)
        agent._handle_max_iterations.assert_not_called()
        self.assertFalse(getattr(agent, '_response_was_previewed', False))
        _drop_verification_continuation_scaffolding(messages)
        _close_transcript_tail(agent, messages, response, False, False)
        self.assertEqual(messages[-3]['content'], 'Candidate conclusion')
        self.assertEqual(messages[-3]['finish_reason'], 'goal_advisory_required')
        self.assertEqual(messages[-1]['content'], response)
        self.assertEqual([m['role'] for m in messages],
                         ['system', 'user', 'assistant', 'user', 'assistant'])

    def test_final_gate_ignores_no_hook_no_goal_paused_and_interrupted(self):
        calls = []
        def review(**kwargs):
            calls.append(kwargs)
            return self.pending()
        # No hook remains the ordinary final response path.
        _, _, _, verdict = self._stop()
        self.assertFalse(verdict.continue_turn)
        self.x.plugins._hooks['goal_advisory_context'] = [review]
        for status in ('paused', 'done', 'cleared'):
            self.x.manager.state.status = status
            self.x.manager._save()
            _, _, _, verdict = self._stop()
            self.assertFalse(verdict.continue_turn)
        self.x.manager.resume()
        agent = self._agent()
        agent._interrupt_requested = True
        self.assertFalse(self._stop(agent)[-1].continue_turn)
        self.x.db.set_meta('goal:' + agent.session_id, '')
        self.assertFalse(self._stop()[-1].continue_turn)
        self.assertEqual(calls, [])

    def test_pause_during_final_review_never_dispatches_stale_continuation(self):
        def review(**kwargs):
            self.x.manager.pause('human pause')
            self.assertTrue(kwargs['cancel_check']())
            return self.pending()
        self.x.plugins._hooks['goal_advisory_context'] = [review]
        self.assertFalse(self._stop()[-1].continue_turn)
        self.assertEqual(self.x.manager.state.status, 'paused')

    def test_followup_survives_empty_or_oversized_context(self):
        for context in ('', 'x' * 3501):
            with self.subTest(size=len(context)):
                self.x.plugins._hooks['goal_advisory_context'] = [lambda **_: dict(self.pending(), context=context)]
                self.assertTrue(self._stop()[-1].continue_turn)

    def test_narrow_legacy_callback_accepts_additive_stage_fields(self):
        calls = []
        def review(*, session_id, goal_id, goal_text, turns_used, cancel_check, max_chars):
            calls.append(session_id)
            return {'status': 'received', 'context': 'Advice without mandatory handling.'}
        self.x.plugins._hooks['goal_advisory_context'] = [review]
        self.assertFalse(self._stop()[-1].continue_turn)
        self.assertEqual(self.x.manager.evaluate_after_turn('Complete')['status'], 'done')
        self.assertEqual(len(calls), 2)


    def test_final_gate_uses_owning_profile_a_b_a_and_restores_ambient_scope(self):
        from hermes_constants import (get_hermes_home, set_hermes_home_override,
                                      reset_hermes_home_override)
        from hermes_state import SessionDB
        home_a = Path(self.x.tmp).resolve()
        home_b = home_a / 'second-profile'
        home_b.mkdir()
        db_b = SessionDB(home_b / 'native.db')
        self.addCleanup(db_b.close)
        db_b.set_meta('goal:' + self.x.manager.session_id,
                      self.x.db.get_meta('goal:' + self.x.manager.session_id))
        seen = []
        managers = {}
        for home in (home_a, home_b):
            manager = plugins.PluginManager(scope_key=str(home))
            manager._discovered = True
            def review(**kwargs):
                seen.append(get_hermes_home().resolve())
                return self.pending()
            manager._hooks['goal_advisory_context'] = [review]
            managers[home] = manager
        with patch.object(plugins, 'get_plugin_manager', REAL_PLUGIN_MANAGER), patch.object(
                plugins, '_plugin_managers_by_home', managers), patch.object(plugins, '_plugin_manager', None):
            token = set_hermes_home_override(home_b)
            try:
                for db in (self.x.db, db_b, self.x.db):
                    agent = self._agent()
                    agent._session_db = db
                    self.assertTrue(self._stop(agent)[-1].continue_turn)
                    self.assertEqual(get_hermes_home().resolve(), home_b)
            finally:
                reset_hermes_home_override(token)
        self.assertEqual(seen, [home_a, home_b, home_a])

    def test_real_text_finalization_continues_main_then_allows_handled_answer(self):
        from agent.turn_final_response import finish_text_response
        from agent.turn_finalizer import _drop_verification_continuation_scaffolding
        agent = self._agent()
        agent.api_mode = 'chat_completions'
        agent.valid_tool_names = set()
        agent._stall_guards = False
        agent._has_content_after_think_block = lambda text: bool(text)
        agent._strip_think_blocks = lambda text: text
        agent._build_assistant_message = lambda msg, reason: {
            'role': 'assistant', 'content': msg.content, 'finish_reason': reason}
        agent._emit_pending_fallback_notice = Mock()
        agent._clear_status_buffer = Mock()
        calls = []
        pending = [True]
        def review(**kwargs):
            calls.append(kwargs)
            return dict(self.pending(), requires_followup=pending[0])
        self.x.plugins._hooks['goal_advisory_context'] = [review]
        messages = [{'role': 'system', 'content': 'Stable prompt'},
                    {'role': 'user', 'content': 'Investigate synthetic sources'}]
        def finish(candidate, previous=None):
            return finish_text_response(agent,
                assistant_message=SimpleNamespace(content=candidate, tool_calls=[]), response=None,
                finish_reason='stop', messages=messages, api_messages=messages,
                conversation_history=[], api_call_count=1, user_message='Investigate synthetic sources',
                active_system_prompt='Stable prompt', final_response=None, _turn_exit_reason='unknown',
                _preflight_compression_blocked=False, codex_ack_continuations=0,
                truncated_response_parts=[], length_continue_retries=0,
                _pending_verification_response=previous._pending_verification_response if previous else None,
                _pending_verification_response_previewed=False, effective_task_id='synthetic')
        with patch('agent.turn_finalizer.apply_llm_output_transform', side_effect=lambda a, value, **kw: (value, False, None)) as transform:
            first = finish('Initial candidate')
            self.assertEqual(first.action, 'continue')
            self.assertIsNone(first.final_response)
            transform.assert_not_called()
            # Simulate the main investigator recording a reasoned handling disposition.
            pending[0] = False
            second = finish('Revised source-backed answer', first)
            self.assertEqual(second.action, 'break')
            self.assertEqual(second.final_response, 'Revised source-backed answer')
            transform.assert_called_once()
        _drop_verification_continuation_scaffolding(messages)
        self.assertEqual([m['role'] for m in messages],
                         ['system', 'user', 'assistant', 'user', 'assistant'])
        self.assertEqual(messages[2]['finish_reason'], 'goal_advisory_required')
        self.assertEqual(messages[-1]['content'], 'Revised source-backed answer')
        self.assertEqual([call['draft_response'] for call in calls],
                         ['Initial candidate', 'Revised source-backed answer'])


    def test_native_finalizer_advisory_exhaustion_is_incomplete_with_shared_or_local_budget(self):
        from agent import turn_finalizer
        self.x.plugins._hooks['goal_advisory_context'] = [self.pending]
        for calls_used, local_max in ((2, 2), (1, 10)):
            with self.subTest(calls_used=calls_used, local_max=local_max):
                agent, messages, candidate, verdict = self._stop()
                agent.max_iterations = local_max
                agent.model, agent.provider, agent.base_url = 'fixture', 'fixture', 'fixture://offline'
                agent.context_compressor = SimpleNamespace(last_prompt_tokens=0)
                for key in (*turn_finalizer._SESSION_TOKEN_KEYS, *turn_finalizer._SESSION_COST_KEYS):
                    setattr(agent, 'session_' + key, 0)
                agent._tool_guardrail_halt_decision = None
                agent._skill_nudge_interval = 0
                agent.valid_tool_names = set()
                agent._drain_pending_steer = Mock(return_value=None)
                for method in ('_save_trajectory', '_cleanup_task_resources', '_persist_session',
                               '_sync_external_memory_for_turn', 'clear_interrupt',
                               '_drop_trailing_empty_response_scaffolding'):
                    setattr(agent, method, Mock())
                with patch.object(turn_finalizer, '_log_turn_exit'), patch.object(
                        turn_finalizer, '_micro_compact_after_turn'), patch.object(
                        turn_finalizer, 'apply_llm_output_transform',
                        side_effect=lambda a, value, **kw: (value, False, None)), patch.object(
                        turn_finalizer, '_apply_output_hooks',
                        side_effect=lambda a, value, *args, **kw: (value, False, None)):
                    result = turn_finalizer.finalize_turn(agent,
                        final_response=None, api_call_count=calls_used, interrupted=False, failed=False,
                        messages=messages, conversation_history=[], effective_task_id='synthetic',
                        turn_id='synthetic-turn', user_message='Investigate', original_user_message='Investigate',
                        _should_review_memory=False, _turn_exit_reason='budget_exhausted',
                        _pending_verification_response=verdict.pending_verification_response,
                        _pending_verification_response_previewed=False)
                self.assertFalse(result['completed'])
                self.assertEqual(result['final_response'], verdict.pending_verification_response)
                self.assertEqual(messages[-1]['content'], result['final_response'])
                self.assertEqual(candidate['finish_reason'], 'goal_advisory_required')
                self.assertIn(candidate, messages)
                agent._handle_max_iterations.assert_not_called()


    def test_aggregate_budget_preserves_pending_finding_ids(self):
        import json
        from hermes_cli.goals_advisory import goal_advisory_result
        ids = ['finding-' + str(index).zfill(24) for index in range(9)]
        value = dict(self.pending(), review_id='r' * 64,
                     context='x' * 3400 + ' retained pending-action details' * 3,
                     pending_finding_ids=ids)
        self.x.plugins._hooks['goal_advisory_context'] = [lambda **_: value]
        result = goal_advisory_result(session_id=self.x.manager.session_id, goal_id='fixture',
            goal_text='Synthetic', turns_used=1, cancel_check=lambda: False)
        self.assertTrue(result['requires_followup'])
        self.assertLessEqual(len(result['context']), 4000)
        data = json.loads(result['context'])[0]
        self.assertEqual(data['pending_finding_ids'], ids)
        self.assertTrue(data['context'].endswith('retained pending-action details'))


    def test_real_persisted_reviewer_blocks_native_until_main_records_dispositions(self):
        from functools import partial
        import test_review_dispositions as review_fixtures
        from forsic_plugin import independent_review as review
        from hermes_cli import goals
        fixture = review_fixtures.ReviewDispositionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.setup_review()
        # The privacy fixture injects intentionally unknown note fields; the real
        # question writer accepts only its schema, so prepare a valid current note.
        fixture.note['investigation'].pop('summary', None)
        fixture.note['investigation'].pop('last_response', None)
        fixture.note['investigation'].pop('answer', None)
        fixture.save_note()
        self.x.db.create_session(fixture.sid, 'tui')
        self.x.db.set_meta('goal:' + fixture.sid, fixture.goal.to_json())
        fixture.native = Path(self.x.db.db_path)
        manager = goals.GoalManager(fixture.sid)
        self.x.plugins._hooks['goal_advisory_context'] = [partial(review.goal_advisory_context,
            enabled=True, intake_root=fixture.intake, native_db=fixture.native)]
        decision = manager.evaluate_after_turn('PRIVATE_MAIN_DRAFT')
        self.assertTrue(decision['should_continue'])
        self.x.judge.assert_not_called()
        self.assertEqual(len(fixture.calls), 1)
        self.assertNotIn('PRIVATE_MAIN_DRAFT', str(fixture.calls))
        agent = self._agent()
        agent.session_id = fixture.sid
        for _ in range(2):
            self.assertTrue(self._stop(agent, 'Candidate answer')[-1].continue_turn)
        self.assertEqual(len(fixture.calls), 2)  # identical candidate only reads its receipt
        pending = review.review_state(fixture.resolved())['pending_findings']
        self.assertEqual(len(pending), 2)
        from forsic_plugin.report_driven.host import current, invoke, find
        question = find(current(fixture.case), 'questions', 'Q-' + fixture.note['note_id'])
        written = invoke(fixture.case, dict(action='question', payload=dict(
            operation_id='native-review-handling', question_id=question['id'],
            expected_revision=question['revision'], answer='Current answer after investigation',
            review_responses=[dict(review_id=item['review_id'], finding_id=item['finding_id'],
                disposition='scope_limited', reason='Main investigator assessed the acquired scope.',
                limitation='The acquired logs do not establish the contents of this user configuration.',
                citation_refs=[]) for item in pending]),
            _session_id=fixture.sid, _goal_id=goals.goal_identity(fixture.goal),
            _session_lineage=[fixture.sid]))
        self.assertNotIn('error', written)
        self.assertGreater(written['question']['revision'], question['revision'])
        saved_question=find(current(fixture.case), 'questions', question['id'])
        saved_responses=deepcopy(saved_question['review_responses'])
        self.assertEqual(len(saved_responses),len(pending))
        followup=review.review_state(fixture.resolved())
        self.assertEqual(followup['pending_findings'],[])
        self.assertEqual(len(followup['dispositions']),len(pending))
        self.assertTrue(saved_question['judgment_review_required'])
        self.assertTrue(followup['requires_followup'])
        self.assertEqual([q['question_id'] for q in followup['judgment_review_questions']],[question['id']])
        # Dispositions close the concerns; an ungrounded answer edit still needs
        # the main investigator's current judgment before native completion.
        decision=manager.evaluate_after_turn('Current answer after investigation')
        self.assertTrue(decision['should_continue'])
        self.x.judge.assert_not_called()
        self.assertEqual(len(fixture.calls),2)
        from forsic_plugin.report_driven.grounded_judgment import read_refs
        data={'tool':'forsic_read','path':'synthetic-followup.log',
              'lines':[{'line':1,'text':'The retained record configures a job; successful execution is unconfirmed.'}]}
        data['started_id']=fixture.case.record('tool_start',{'tool':'forsic_read',
            'arguments':{'path':data['path']}},fixture.sid)
        source_id=fixture.case.record('tool_result',data,fixture.sid)
        citation=read_refs(fixture.case,{**data,'evidence_id':source_id})['presented_spans'][0]['citation_ref']
        repaired=invoke(fixture.case,dict(action='question',payload=dict(
            operation_id='native-current-judgment-repair',question_id=question['id'],
            expected_revision=saved_question['revision'],answer='Revised source-backed answer',
            reason='The current retained configuration supports this bounded answer; execution remains unconfirmed.',
            judgment=dict(citation_refs=[citation],assertion_kind='interpretation',
                reasoning_summary='The record establishes configuration only; the missing execution record remains a limitation.')),
            _session_id=fixture.sid,_goal_id=goals.goal_identity(fixture.goal),
            _session_lineage=[fixture.sid],_question_contract='v2'))
        self.assertNotIn('error',repaired)
        current_question=find(current(fixture.case),'questions',question['id'])
        self.assertEqual(current_question['answer'],'Revised source-backed answer')
        self.assertFalse(current_question['judgment_review_required'])
        self.assertTrue(current_question['claim_refs'])
        self.assertEqual(current_question['review_responses'],saved_responses)
        self.assertFalse(review.review_state(fixture.resolved())['requires_followup'])
        fixture.model_findings([])
        self.assertFalse(self._stop(agent, 'Revised source-backed answer')[-1].continue_turn)
        self.assertEqual(len(fixture.calls), 3)
        decision = manager.evaluate_after_turn('Revised source-backed answer')
        self.assertEqual(decision['status'], 'done')
        self.x.judge.assert_called_once()
        self.assertEqual(len(fixture.calls), 3)


if __name__ == '__main__':
    unittest.main()
