"""Hermetic native goal evaluation contracts; no model or network requests."""
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hermes_cli import goals, plugins
from hermes_state import SessionDB


class NativeGoalEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {"HERMES_HOME": self.tmp}))
        self.db = SessionDB(Path(self.tmp) / "native.db")
        self.stack.callback(self.db.close)
        self.stack.enter_context(patch.object(goals, "_get_session_db", return_value=self.db))
        self.plugins = plugins.PluginManager(scope_key=self.tmp)
        self.plugins._discovered = True
        self.stack.enter_context(patch.object(plugins, "get_plugin_manager", return_value=self.plugins))
        self.stack.enter_context(patch.object(plugins, "_resolve_hook_callback_timeout", return_value=0))
        self.manager = goals.GoalManager("synthetic-conversation")
        self.manager.set("Resolve the supplied synthetic question", contract=goals.GoalContract(
            verification="Compare the current result with its sources and explain unresolved scope."))
        self.judge = self.stack.enter_context(patch.object(
            goals, "judge_goal", return_value=("done", "judged complete", False, None, False)))

    def hook(self, callback):
        self.plugins._hooks["goal_evaluation_context"] = [callback]

    @staticmethod
    def ready(**kwargs):
        return {"provider": "fixture", "status": "ready", "revision": "content-v1",
                "context": "Source-backed synthetic decision; report bound to current result."}

    def test_no_hook_preserves_single_judge_and_done_behavior(self):
        decision = self.manager.evaluate_after_turn("Finished.")
        self.assertEqual(decision["status"], "done")
        self.judge.assert_called_once()
        self.assertNotIn("evaluation_context", self.judge.call_args.kwargs)
        self.assertIsNone(goals.load_goal(self.manager.session_id).evaluation_receipt)

    def test_gateway_delivered_and_streamed_turns_keep_runtime_cause(self):
        import asyncio
        from unittest.mock import AsyncMock
        from gateway.run_goals import GatewayGoalsMixin
        from hermes_cli.goals_turn_context import goal_turn_result
        result = dict(completed=False, turn_exit_reason='max_iterations_reached(20/20)',
                      final_response='Partial findings; review remains.')
        source = SimpleNamespace()
        for delivered in (result, None):
            runner = GatewayGoalsMixin()
            runner.async_session_store = SimpleNamespace(get_or_create_session=AsyncMock(return_value=SimpleNamespace(session_id='fixture')))
            runner._post_turn_goal_continuation = AsyncMock()
            runner._post_turn_loop_completion = AsyncMock()
            event = SimpleNamespace(_goal_turn_result=goal_turn_result(result), _streamed_final_response=result['final_response'])
            asyncio.run(runner._run_post_turn_hooks(agent_result=delivered, source=source, is_internal=False, event=event))
            runner._post_turn_goal_continuation.assert_awaited_once()
            self.assertEqual(runner._post_turn_goal_continuation.call_args.kwargs['turn_result'], result)
            self.assertNotIn('turn_result', runner._post_turn_loop_completion.call_args.kwargs)

    def test_context_is_separate_and_revision_is_bound_to_done_receipt(self):
        calls = []

        def provider(**kwargs):
            calls.append(kwargs)
            return self.ready(**kwargs)

        self.hook(provider)
        identity = goals.goal_identity(self.manager.state)
        decision = self.manager.evaluate_after_turn("Visible assistant response.")
        self.assertEqual(decision["status"], "done")
        self.judge.assert_called_once()
        self.assertEqual(self.judge.call_args.args[1], "Visible assistant response.")
        self.assertIn("Source-backed", self.judge.call_args.kwargs["evaluation_context"])
        self.assertEqual([x["phase"] for x in calls], ["prepare", "validate"])
        self.assertEqual(calls[1]["expected_revision"], {"fixture": "content-v1"})
        self.assertEqual(calls[0]["conversation_id"], self.manager.session_id)
        self.assertEqual(calls[0]["goal_text"], self.manager.state.goal)
        self.assertEqual(calls[0]["contract"], self.manager.state.contract.to_dict())
        receipt = goals.load_goal(self.manager.session_id).evaluation_receipt
        self.assertEqual(receipt["goal_id"], identity)
        self.assertEqual(receipt["revisions"], {"fixture": "content-v1"})

    def test_async_provider_uses_native_dispatch(self):
        async def provider(**kwargs):
            return self.ready(**kwargs)
        self.hook(provider)
        self.assertEqual(self.manager.evaluate_after_turn("done")["status"], "done")
        self.judge.assert_called_once()

    def test_unbound_provider_is_explicitly_not_applicable(self):
        self.hook(lambda **_: {"provider": "fixture", "status": "not_applicable"})
        self.assertEqual(self.manager.evaluate_after_turn("done")["status"], "done")
        self.assertNotIn("evaluation_context", self.judge.call_args.kwargs)
        self.assertIsNone(self.manager.state.evaluation_receipt)

    def test_unavailable_state_pauses_before_judge(self):
        self.hook(lambda **_: {"provider": "fixture", "status": "unavailable", "reason": "read failed"})
        result = self.manager.evaluate_after_turn("all done")
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["verdict"], "evaluation_unavailable")
        self.assertIn("read failed", self.manager.state.paused_reason)
        self.judge.assert_not_called()

    def test_swallowed_callback_failure_cannot_become_empty_context(self):
        def provider(**kwargs):
            raise OSError("synthetic state failure")
        self.hook(provider)
        result = self.manager.evaluate_after_turn("all done")
        self.assertEqual(result["status"], "paused")
        self.judge.assert_not_called()

    def test_none_or_oversized_projection_is_unavailable(self):
        for callback in (lambda **_: None, lambda **_: dict(self.ready(), context="x" * 12001)):
            with self.subTest(callback=callback):
                self.manager.resume()
                self.hook(callback)
                self.assertEqual(self.manager.evaluate_after_turn("done")["status"], "paused")
        self.judge.assert_not_called()

    def test_changed_content_revision_cannot_complete(self):
        def provider(**kwargs):
            value = self.ready()
            if kwargs["phase"] == "validate":
                value["revision"] = "content-v2"
            return value
        self.hook(provider)
        result = self.manager.evaluate_after_turn("all done")
        self.assertEqual(result["status"], "active")
        self.assertTrue(result["should_continue"])
        self.assertIn("revision changed", result["continuation_prompt"])
        self.assertIsNone(self.manager.state.evaluation_receipt)
        self.judge.assert_called_once()

    def test_pending_work_is_continue_not_gate_failure_or_pause(self):
        def provider(**kwargs):
            return dict(self.ready(), status="incomplete", reason="A relevant comparison remains feasible.")
        self.hook(provider)
        result = self.manager.evaluate_after_turn("all done")
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["verdict"], "continue")
        self.assertIn("comparison remains", result["continuation_prompt"])
        self.assertEqual(self.manager.state.gates, [])
        self.assertEqual(self.manager.state.consecutive_parse_failures, 0)
        self.judge.assert_called_once()

    def test_validation_read_failure_pauses_after_only_one_judge(self):
        def provider(**kwargs):
            if kwargs["phase"] == "validate":
                return {"provider": "fixture", "status": "unavailable", "reason": "storage unavailable"}
            return self.ready()
        self.hook(provider)
        self.assertEqual(self.manager.evaluate_after_turn("done")["status"], "paused")
        self.judge.assert_called_once()

    def test_same_manager_pause_during_judge_wins(self):
        def judge(*args, **kwargs):
            self.manager.pause("human pause")
            return "done", "stale success", False, None, False
        self.judge.side_effect = judge
        result = self.manager.evaluate_after_turn("all done")
        self.assertEqual(result["verdict"], "stale_evaluation")
        self.assertFalse(result["should_continue"])
        self.assertEqual(self.manager.state.status, "paused")
        self.assertEqual(self.manager.state.paused_reason, "human pause")
        self.assertEqual(goals.load_goal(self.manager.session_id).status, "paused")

    def test_pause_while_judge_is_waiting_on_another_thread(self):
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []

        def judge(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise AssertionError("test did not release the synthetic judge")
            return "done", "stale success", False, None, False

        def evaluate():
            try:
                results.append(self.manager.evaluate_after_turn("all done"))
            except Exception as exc:
                errors.append(exc)

        self.judge.side_effect = judge
        worker = threading.Thread(target=evaluate)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.manager.pause("human control while judge waits")
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["verdict"], "stale_evaluation")
        self.assertEqual(self.manager.state.status, "paused")
        self.assertEqual(self.manager.state.paused_reason, "human control while judge waits")

    def test_provider_timeout_is_explicit_unavailable(self):
        release = threading.Event()

        def provider(**kwargs):
            release.wait(2)
            return self.ready()

        self.hook(provider)
        try:
            with patch.object(plugins, "_resolve_hook_callback_timeout", return_value=0.02):
                result = self.manager.evaluate_after_turn("all done")
        finally:
            release.set()
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["verdict"], "evaluation_unavailable")
        self.judge.assert_not_called()

    def test_concurrent_clear_or_rescope_during_judge_wins(self):
        for action in ("clear", "contract", "new_goal", "delete"):
            with self.subTest(action=action):
                self.manager.set("original question")
                other = goals.GoalManager(self.manager.session_id)

                def judge(*args, **kwargs):
                    if action == "clear":
                        other.clear()
                    elif action == "contract":
                        other.set_contract(goals.GoalContract(verification="New required scope"))
                    elif action == "new_goal":
                        other.set("new question")
                    else:
                        self.db._write_sql("DELETE FROM state_meta WHERE key = ?", ("goal:" + self.manager.session_id,))
                    return "done", "stale success", False, None, False

                self.judge.side_effect = judge
                result = self.manager.evaluate_after_turn("all done")
                self.assertEqual(result["verdict"], "stale_evaluation")
                self.assertFalse(result["should_continue"])
                current = goals.load_goal(self.manager.session_id)
                if action == "clear":
                    self.assertEqual(current.status, "cleared")
                elif action == "contract":
                    self.assertEqual(current.contract.verification, "New required scope")
                elif action == "new_goal":
                    self.assertEqual(current.goal, "new question")
                else:
                    self.assertIsNone(current)

    def test_every_verdict_is_cas_guarded_not_just_done(self):
        for verdict, wait in (("continue", None), ("blocked", None), ("wait", {"seconds": 30})):
            with self.subTest(verdict=verdict):
                self.manager.set("synthetic question")

                def judge(*args, **kwargs):
                    self.manager.pause("manual")
                    return verdict, "obsolete decision", False, wait, False

                self.judge.side_effect = judge
                result = self.manager.evaluate_after_turn("response")
                self.assertEqual(result["verdict"], "stale_evaluation")
                self.assertEqual(self.manager.state.status, "paused")
                self.assertFalse(result["should_continue"])

    def test_pause_during_gate_is_not_overwritten_and_judge_not_run(self):
        self.manager.state.gates = [goals.GoalGate(command="synthetic gate")]
        self.manager._save()

        def gate(*args, **kwargs):
            self.manager.pause("manual during gate")
            return False, 1, "synthetic failure"

        with patch.object(goals, "run_gate", side_effect=gate), patch.object(
                goals, "_gate_workspace", return_value=(self.tmp, None)):
            result = self.manager.evaluate_after_turn("response")
        self.assertEqual(result["verdict"], "stale_evaluation")
        self.assertEqual(self.manager.state.status, "paused")
        self.judge.assert_not_called()

    def test_compare_and_swap_is_atomic_across_connections_and_never_recreates(self):
        other = SessionDB(Path(self.tmp) / "native.db")
        self.addCleanup(other.close)
        self.db.set_meta("fixture", "before")
        before = self.db.get_meta("fixture")
        other.set_meta("fixture", "concurrent change")
        self.assertFalse(self.db.compare_and_swap_meta("fixture", before, "stale"))
        self.assertEqual(other.get_meta("fixture"), "concurrent change")
        self.assertTrue(self.db.compare_and_swap_meta("fixture", "concurrent change", "after"))
        self.assertFalse(self.db.compare_and_swap_meta("absent", "old", "new"))
        self.assertIsNone(other.get_meta("absent"))

    def test_active_goal_budget_handoff_preserves_history_without_summary_call(self):
        import copy
        import logging
        from unittest.mock import Mock
        from agent.turn_finalizer import _resolve_budget_fallback
        agent = SimpleNamespace(session_id=self.manager.session_id, _session_db=self.db,
            max_iterations=2, iteration_budget=SimpleNamespace(remaining=0), quiet_mode=True,
            _handle_max_iterations=Mock(return_value="new unsupported interpretation"),
            _emit_diagnostic_status=Mock())
        messages = [{"role":"system", "content":"fixed prompt"},
            {"role":"user", "content":"Compare the records"},
            {"role":"assistant", "content":None, "tool_calls":[{"id":"read-a"}]},
            {"role":"tool", "tool_call_id":"read-a", "content":"Exact observation"}]
        before = copy.deepcopy(messages)
        response, reason, preserved, interrupted = _resolve_budget_fallback(agent,
            final_response=None, api_call_count=2, interrupted=False, failed=False,
            messages=messages, _turn_exit_reason="budget_exhausted",
            _pending_verification_response=None, _pending_verification_response_previewed=False,
            logger=logging.getLogger(__name__))
        agent._handle_max_iterations.assert_not_called()
        self.assertEqual(messages, before)
        self.assertEqual(reason, "max_iterations_reached(2/2)")
        self.assertFalse(preserved or interrupted)
        self.assertNotIn("new unsupported interpretation", response)
        self.judge.return_value = ("continue", "Compare unresolved observations", False, None, False)
        self.hook(lambda **kw: {**self.ready(), "pending_reason":"Unresolved comparison"})
        decision = self.manager.evaluate_after_turn(response, turn_result={
            "completed":False, "turn_exit_reason":reason, "final_response":response})
        self.assertTrue(decision["should_continue"])
        self.assertIn("evaluation_context", self.judge.call_args.kwargs)
        self.assertEqual(self.judge.call_args.kwargs["turn_context"]["stop_cause"], "iteration_limit_handoff")

    def test_goal_handoff_keeps_pending_answers_interrupts_and_non_goal_summary(self):
        import logging
        from unittest.mock import Mock
        from agent.turn_finalizer import _resolve_budget_fallback
        for pending, interrupted, failed, status, expected_calls in (
                ("candidate awaiting verification", False, False, "active", 0),
                (None, True, False, "active", 0), (None, False, True, "active", 0),
                (None, False, False, "paused", 1), (None, False, False, None, 1)):
            with self.subTest(pending=pending, interrupted=interrupted, failed=failed, status=status):
                if status is None:
                    self.db.set_meta("goal:"+self.manager.session_id, "")
                else:
                    self.manager.state.status=status;self.manager._save()
                agent=SimpleNamespace(session_id=self.manager.session_id, _session_db=self.db,
                    max_iterations=2, iteration_budget=SimpleNamespace(remaining=0), quiet_mode=True,
                    _handle_max_iterations=Mock(return_value="ordinary summary"), _emit_diagnostic_status=Mock())
                response, _, preserved, got_interrupt = _resolve_budget_fallback(agent,
                    final_response=None, api_call_count=2, interrupted=interrupted, failed=failed,
                    messages=[], _turn_exit_reason="budget_exhausted",
                    _pending_verification_response=pending, _pending_verification_response_previewed=bool(pending),
                    logger=logging.getLogger(__name__))
                self.assertEqual(agent._handle_max_iterations.call_count, expected_calls)
                self.assertEqual(got_interrupt, interrupted)
                if pending:
                    self.assertEqual(response,pending);self.assertTrue(preserved);self.assertTrue(agent._response_was_previewed)
                elif interrupted or failed:self.assertIsNone(response)
                else:self.assertEqual(response,"ordinary summary")

    def test_native_limit_outcome_reaches_judge_through_tui_and_budget_still_wins(self):
        from tui_gateway import server as prompt_turn
        result = {"completed": False, "failed": False, "interrupted": False,
                  "turn_exit_reason": "max_iterations_reached(20/20)",
                  "final_response": "Partial findings; the report is unfinished. Please ask me to continue."}
        self.judge.return_value = ("continue", "Finish the remaining supported comparison", False, None, False)
        session = {"session_key": self.manager.session_id, "agent": SimpleNamespace(session_id=self.manager.session_id)}
        with patch.object(prompt_turn, "_active_goal_manager", return_value=self.manager), patch.object(
                prompt_turn, "_plan_goal_compression_recovery", return_value=(None, None)), patch.object(
                prompt_turn, "_emit"), patch.object(goals, "gather_background_processes", return_value=[]), patch.object(
                goals, "count_active_delegations", return_value=0):
            followup = prompt_turn._goal_followup_after_turn("ui", session, result, "complete", result["final_response"])
        self.assertTrue(followup)
        self.assertEqual(self.manager.state.turns_used, 1)
        self.assertEqual(self.judge.call_args.args[1], result["final_response"])
        self.assertEqual(self.judge.call_args.kwargs["turn_context"], {
            "stop_cause": "iteration_limit_handoff", "turn_completed": False,
            "remaining_goal_turns": self.manager.state.max_turns - 1})
        self.manager.state.max_turns = 2
        self.manager._save()
        decision = self.manager.evaluate_after_turn(result["final_response"], turn_result=result)
        self.assertFalse(decision["should_continue"])
        self.assertEqual(self.manager.state.status, "paused")
        self.assertIn("turn budget exhausted", self.manager.state.paused_reason)
        before = self.judge.call_count
        self.manager.evaluate_after_turn(result["final_response"], turn_result=result)
        self.assertEqual(self.judge.call_count, before)

    def test_runtime_handoff_context_is_not_inferred_from_text_or_failed_and_halted_turns(self):
        from hermes_cli.goals_turn_context import goal_turn_context, goal_turn_result
        text = "Hit the tool limit; please resume."
        base = {"completed": False, "turn_exit_reason": "max_iterations_reached(3/3)", "final_response": text}
        for changes in ({"failed": True}, {"interrupted": True}, {"guardrail": {"action": "halt"}},
                        {"guardrail": {"action": "block"}}, {"turn_exit_reason": "timeout"}, {"completed": True}):
            self.assertIsNone(goal_turn_context({**base, **changes}, remaining_goal_turns=10))
        self.assertIsNone(goal_turn_context({"final_response": text}, remaining_goal_turns=10))
        transported = goal_turn_result({**base, "messages": ["private history"], "api_key": "secret"})
        self.assertNotIn("messages", transported)
        self.assertNotIn("api_key", transported)
        context = goal_turn_context(transported, remaining_goal_turns=10)
        with patch("agent.auxiliary_client.call_llm") as llm:
            llm.return_value = SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"verdict":"blocked","reason":"An independent permission is missing"}'))])
            verdict = REAL_JUDGE("question", text, turn_context=context, evaluation_context="Current source state")
        self.assertEqual(verdict[0], "blocked")  # No unconditional continuation/semantic override.
        prompt = llm.call_args.kwargs["messages"][1]["content"]
        self.assertIn(text, prompt)
        self.assertIn('"stop_cause": "iteration_limit_handoff"', prompt)
        self.assertIn("Current source state", prompt)

    def test_real_judge_prompt_gets_separate_context_once(self):
        with patch("agent.auxiliary_client.call_llm") as llm:
            llm.return_value = SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"verdict":"continue","reason":"pending"}'))])
            verdict = REAL_JUDGE("question", "Visible response", evaluation_context="Current state fact.")
        self.assertEqual(verdict[0], "continue")
        llm.assert_called_once()
        prompt = llm.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Agent's most recent response:\nVisible response", prompt)
        self.assertIn("Read-only evaluation context", prompt)
        self.assertIn("Current state fact.", prompt)


REAL_JUDGE = goals.judge_goal


if __name__ == "__main__":
    unittest.main()
