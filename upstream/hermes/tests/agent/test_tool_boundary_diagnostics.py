"""Offline boundary and native halt behavior. No model, source evidence or network."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from agent.api_request_hooks import ApiRequestHooksMixin
from agent.chat_completion_helpers import _StreamingCall, _ToolCallAccumulator
from agent.tool_argument_diagnostics import argument_shape, request_boundary
from agent.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController
from agent.transports.chat_completions import ChatCompletionsTransport
from tui_gateway import prompt_turn


GUARD = {"hard_stop_enabled": True,
         "hard_stop_after": {"exact_failure": 2, "same_tool_failure": 3}}


class ToolBoundaryTests(unittest.TestCase):
    def test_input_measurements_preserve_prefix_and_do_not_retain_private_values(self):
        messages = [{'role': 'system', 'content': '고정 원칙'}, {'role': 'user', 'content': 'PRIVATE🙂'}]
        kwargs = {'messages': messages, 'tools': []}
        before = deepcopy(kwargs)
        first = request_boundary(kwargs)
        encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()
        self.assertEqual(first['messages_json']['bytes'], len(encode(messages)))
        self.assertEqual(first['message_json_by_role']['user']['bytes'], len(encode(messages[1])))
        self.assertNotIn('PRIVATE', json.dumps(first))
        self.assertEqual(kwargs, before)
        second = request_boundary({**kwargs, 'messages': messages + [{'role': 'assistant', 'content': 'reply'}]})
        self.assertEqual(first['tools_json'], second['tools_json'])
        self.assertEqual(first['message_json_by_role']['system'], second['message_json_by_role']['system'])
        self.assertNotEqual(first['messages_json']['sha256'], second['messages_json']['sha256'])

    def test_merged_schema_is_observed_without_mutating_request(self):
        tools = [{"type": "function", "function": {"name": "fixture", "parameters": {}}}]
        kwargs = {"tools": [], "messages": [], "extra_body": {"tools": tools}}
        old = deepcopy(kwargs)
        self.assertEqual(request_boundary(kwargs), request_boundary({"tools": tools, "messages": []}))
        self.assertEqual(request_boundary(kwargs)["tool_count"], 1)
        self.assertEqual(kwargs, old)

    def test_hook_size_limit_does_not_discard_boundary_or_expose_body(self):
        mixin = ApiRequestHooksMixin()
        with patch.object(mixin, "_hook_payload_max_chars", return_value=1000):
            payload = mixin._api_request_payload_for_hook({"messages": [{"content": "SENSITIVE" * 2000}], "tools": []})
        self.assertEqual(payload["boundary"]["message_count"], 1)
        self.assertNotIn("SENSITIVE", json.dumps(payload["boundary"]))
        self.assertFalse(payload["boundary"]["http_wire_observed"])

    def test_absent_null_empty_and_wrong_types_are_distinct(self):
        raw = json.dumps({"path": None, "file_path": "", "mission_id": False,
                          "mission_version": 0, "tool_arguments": {}, "SECRET_FIELD": "SECRET_VALUE"})
        shape = argument_shape(raw)
        self.assertEqual(shape["fields"]["path"], "null")
        self.assertEqual(shape["fields"]["file_path"], "empty_string")
        self.assertEqual(shape["fields"]["action"], "absent")
        self.assertEqual(shape["fields"]["mission_id"], "boolean")
        self.assertEqual(shape["fields"]["mission_version"], "integer")
        self.assertNotIn("SECRET", json.dumps(shape))
        self.assertEqual(argument_shape('{"path":')["json_state"], "invalid")

    def test_split_deltas_preserve_fields_and_are_not_repaired(self):
        acc = _ToolCallAccumulator()
        for piece in ['{"file_', 'path":"/PRIVATE",', '"mission_id":"PRIVATE"}']:
            acc.feed(NS(index=0, id="private-call", type="function", function=NS(name="fixture", arguments=piece)))
        calls, truncated = _StreamingCall._assemble_tool_calls(acc.materialize(), "tool_calls")
        self.assertFalse(truncated)
        self.assertEqual(json.loads(calls[0].function.arguments)["file_path"], "/PRIVATE")
        self.assertFalse(calls[0].function.args_repaired)
        mixin = ApiRequestHooksMixin()
        mixin.provider, mixin.api_mode = "fixture", "chat_completions"
        response = NS(model="fixture", usage=None)
        payload = mixin._api_response_payload_for_hook(response, NS(tool_calls=calls), finish_reason="tool_calls")
        boundary = payload["boundary"]
        self.assertNotIn("PRIVATE", json.dumps(boundary))
        self.assertEqual(boundary["calls"][0]["parsed"], boundary["calls"][0]["accumulated_before_repair"])

    def test_repair_is_observed_but_truncated_stream_is_not_repaired(self):
        acc = {0: {"id": "fixture", "type": "function", "function": {"name": "fixture", "arguments": '{"path":"x"'}}}
        with patch('agent.chat_completion_helpers._repair_tool_call_arguments', return_value='{"path":"x"}') as repair:
            calls, truncated = _StreamingCall._assemble_tool_calls(acc, "tool_calls")
            self.assertFalse(truncated)
            self.assertTrue(calls[0].function.args_repaired)
            self.assertEqual(calls[0].function._arguments_before_repair_shape["json_state"], "invalid")
            repair.reset_mock()
            calls, truncated = _StreamingCall._assemble_tool_calls(acc, None)
            self.assertTrue(truncated)
            repair.assert_not_called()

    def test_stream_observation_survives_real_transport_normalization(self):
        mixin = ApiRequestHooksMixin()
        mixin.provider, mixin.api_mode = "fixture", "chat_completions"
        transport = ChatCompletionsTransport()
        complete = '{"path":"PRIVATE","file_path":"/PRIVATE"}'
        for raw, repaired in ((complete, False), (complete[:-1], True)):
            with self.subTest(repaired=repaired):
                acc = {0: {"id": "fixture", "type": "function", "function": {
                    "name": "fixture", "arguments": raw}}}
                with patch('agent.chat_completion_helpers._repair_tool_call_arguments', return_value=complete):
                    calls, truncated = _StreamingCall._assemble_tool_calls(acc, "tool_calls")
                self.assertFalse(truncated)
                response = NS(model="fixture", usage=None, choices=[NS(
                    message=NS(content=None, tool_calls=calls), finish_reason="tool_calls")])
                normalized = transport.normalize_response(response)
                self.assertEqual(normalized.tool_calls[0].function.arguments, complete)
                payload = mixin._api_response_payload_for_hook(
                    response, normalized, finish_reason="tool_calls")
                boundary = payload["boundary"]
                self.assertEqual(boundary["calls"][0]["accumulated_before_repair"], argument_shape(raw))
                self.assertEqual(boundary["calls"][0]["args_repaired"], repaired)
                self.assertEqual(boundary["calls"][0]["parsed"], argument_shape(complete))
                self.assertNotIn("PRIVATE", json.dumps(boundary))
                self.assertFalse(boundary["provider_raw_observed"])

    def test_ui_default_warns_and_enabled_guard_counts_changed_reason(self):
        self.assertFalse(ToolCallGuardrailConfig.from_mapping({}, platform="tui").hard_stop_enabled)
        for name, missing_args in (
            ("forsic_image_files", {"path": "synthetic.e01", "action": "read"}),
            ("forsic_image_read", {"path": "synthetic.e01", "action": "read"}),
            ("forsic_image_read_mission", {"path": "synthetic.e01"}),
        ):
            with self.subTest(tool=name):
                guard = ToolCallGuardrailController(ToolCallGuardrailConfig.from_mapping(GUARD, platform="tui"))
                for i in range(3):
                    args = {**missing_args, "reason": str(i)}
                    self.assertTrue(guard.before_call(name, args).allows_execution)
                    decision = guard.after_call(name, args, '{"error":"invalid_arguments"}')
                self.assertEqual(decision.action, "halt")
                self.assertEqual(decision.count, 3)
                manager = Mock()
                result = {"completed": True, "guardrail": decision.to_metadata()}
                with patch.dict(prompt_turn._goal_followup_after_turn.__globals__, {
                    "_emit": Mock(),
                    "_plan_goal_compression_recovery": lambda *a, **k: (None, None),
                    "_active_goal_manager": lambda *_: manager}):
                    self.assertIsNone(prompt_turn._goal_followup_after_turn(
                        "fixture", {"session_key": "fixture"}, result, "complete", "Stopped"))
                manager.evaluate_after_turn.assert_not_called()
                manager.pause.assert_called_once_with(reason="tool guardrail: same_tool_failure_halt")

    def test_exact_replay_blocks_but_corrected_call_can_succeed(self):
        guard = ToolCallGuardrailController(ToolCallGuardrailConfig.from_mapping(GUARD, platform="tui"))
        args = {"path": "synthetic.e01", "action": "read"}
        for _ in range(2):
            guard.after_call("forsic_image_files", args, '{"error":"invalid_arguments"}')
        self.assertFalse(guard.before_call("forsic_image_files", args).allows_execution)
        guard.reset_for_turn()
        guard.after_call("forsic_image_files", args, '{"error":"invalid_arguments"}')
        fixed = dict(args, file_path="/synthetic")
        self.assertTrue(guard.before_call("forsic_image_files", fixed).allows_execution)
        self.assertFalse(guard.after_call("forsic_image_files", fixed, '{"text":"valid"}').should_halt)

    def test_halt_never_calls_goal_judge_or_continues(self):
        manager = Mock()
        for action in ("block", "halt"):
            result = {"completed": True, "guardrail": {"action": action}}
            with patch.dict(prompt_turn._goal_followup_after_turn.__globals__, {
                "_emit": Mock(),
                "_plan_goal_compression_recovery": lambda *a, **k: (None, None),
                "_active_goal_manager": lambda *_: manager}):
                self.assertIsNone(prompt_turn._goal_followup_after_turn("fixture", {"session_key": "fixture"}, result, "complete", "Stopped"))
            manager.evaluate_after_turn.assert_not_called()
        self.assertEqual(manager.pause.call_count, 2)
        self.assertTrue(prompt_turn._is_successful_goal_turn({"completed": True, "guardrail": {"action": "warn"}}, "complete", "Answer"))
        self.assertFalse(prompt_turn._is_successful_goal_turn({"interrupted": True}, "interrupted", "Stopped"))


if __name__ == "__main__":
    unittest.main()
