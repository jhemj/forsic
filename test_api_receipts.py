"""Registered API hooks persist bounded observations in a synthetic case only."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from forsic_plugin import register
from forsic_plugin.evidence import Case


class ApiReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.intake = root / 'intake'; self.intake.mkdir()
        evidence = root / 'evidence'; evidence.mkdir()
        manifest = root / 'case.json'
        manifest.write_text(json.dumps({'case_id': 'SYN-api-receipt', 'synthetic': True,
            'evidence_root': str(evidence), 'output_root': str(root / 'results')}))
        self.case = Case(manifest)
        self.sid = 'synthetic-api-session'
        (self.intake / (hashlib.sha256(self.sid.encode()).hexdigest() + '.json')).write_text(
            json.dumps({'manifest': str(manifest), 'stage': 'goal_active'}))
        environment = patch.dict(os.environ, {'HERMES_HOME': str(root / 'native')})
        environment.start(); self.addCleanup(environment.stop)
        ctx = Mock()
        ctx.get_config.side_effect = lambda name, default=None: str(self.intake) if name == 'intake_root' else default
        register(ctx)
        self.hooks = {call.args[0]: call.args[1] for call in ctx.register_hook.call_args_list}

    def receipt(self, kind, **kwargs):
        self.hooks[kind](session_id=self.sid, **kwargs)
        event = self.case.events(1)[0]
        self.assertEqual(event['kind'], kind)
        self.assertEqual(event['session'], self.sid)
        return event['data']

    def test_pre_request_preserves_budget_and_estimate_without_raw_request(self):
        metadata = dict(api_request_id='synthetic-request', api_mode='chat_completions',
            api_call_count=1, retry_count=0, approx_input_tokens=93154, request_char_count=200000,
            max_tokens=4096, message_count=93, tool_count=25, started_at=1000.25)
        observed = self.receipt('pre_api_request', **metadata,
            request={'body': {'messages': [{'content': 'PRIVATE_BODY'}]},
                'extra_headers': {'Authorization': 'PRIVATE_SECRET'},
                'boundary': {'stage': 'pre_sdk_merged_body', 'http_wire_observed': False}})
        self.assertEqual({key: observed[key] for key in metadata}, metadata)
        self.assertEqual(observed['request_boundary']['http_wire_observed'], False)
        self.assertNotIn('PRIVATE', json.dumps(observed))
        self.assertNotIn('stream_diagnostics', observed)

    def test_headers_first_chunk_and_api_return_remain_distinct(self):
        diagnostics = dict(phase='post_chunk', attempt=1, http_status=200,
            started_at=1000.0, headers_received_at=1002.0, first_chunk_at=1005.0,
            ended_at=1010.0, elapsed_seconds=10.0, headers_wait_seconds=2.0,
            first_chunk_wait_seconds=5.0, termination_cause='completed')
        observed = self.receipt('post_api_request', api_request_id='synthetic-request',
            started_at=1000.0, ended_at=1010.0, api_duration=10.0, first_chunk_at=1005.0,
            context_length=131072, assistant_content_chars=3, assistant_tool_call_count=0,
            stream_diagnostics={**diagnostics, 'headers': {'Authorization': 'PRIVATE_SECRET'},
                'body': 'PRIVATE_BODY', 'unknown': ['PRIVATE_BODY']})
        self.assertEqual(observed['stream_diagnostics'], diagnostics)
        self.assertLess(observed['stream_diagnostics']['headers_received_at'], observed['first_chunk_at'])
        self.assertLess(observed['first_chunk_at'], observed['ended_at'])
        self.assertNotIn('PRIVATE', json.dumps(observed))

    def test_error_retains_watchdog_phase_without_claiming_http_status(self):
        diagnostics = dict(phase='before_headers', attempt=1, http_status=None,
            started_at=1000.0, headers_received_at=None, first_chunk_at=None,
            ended_at=2800.0, elapsed_seconds=1800.0, headers_wait_seconds=1800.0,
            first_chunk_wait_seconds=1800.0, termination_cause='stale_stream_timeout')
        observed = self.receipt('api_request_error', status_code=None, retry_count=0,
            max_retries=2, retryable=True, first_chunk_at=None, stream_diagnostics=diagnostics)
        self.assertEqual(observed['stream_diagnostics'], diagnostics)
        self.assertIsNone(observed['status_code'])
        self.assertIsNone(observed['first_chunk_at'])
        self.assertEqual((observed['retry_count'], observed['max_retries'], observed['retryable']), (0, 2, True))
        diagnostics.update(phase='first_chunk', http_status=200, headers_received_at=1002.0,
                           headers_wait_seconds=2.0)
        observed = self.receipt('api_request_error', stream_diagnostics=diagnostics)
        self.assertEqual(observed['stream_diagnostics']['phase'], 'first_chunk')
        self.assertEqual(observed['stream_diagnostics']['http_status'], 200)
        self.assertIsNone(observed['stream_diagnostics']['first_chunk_at'])

    def test_invalid_or_unbounded_new_observations_are_omitted(self):
        observed = self.receipt('api_request_error', api_mode=['PRIVATE'],
            approx_input_tokens=True, request_char_count=-1, max_tokens=1.5,
            message_count=2**80, api_call_count='1', retry_count=True,
            max_retries=-1, retryable=1, status_code=True, started_at=float('nan'),
            ended_at=float('inf'), first_chunk_at='PRIVATE', api_duration=-1,
            stream_diagnostics={'phase': 'PRIVATE', 'attempt': True, 'http_status': 999,
                'termination_cause': 'PRIVATE', 'elapsed_seconds': float('inf'),
                'started_at': -1, 'headers_received_at': 'PRIVATE', 'headers': {'secret': 'PRIVATE'}})
        self.assertEqual(observed, {})
        observed = self.receipt('pre_api_request', max_tokens=None, status_code=429,
            approx_input_tokens=0, retry_count=0, retryable=False)
        self.assertEqual(observed, dict(max_tokens=None, status_code=429,
            approx_input_tokens=0, retry_count=0, retryable=False))

    def test_unbound_session_does_not_create_case_events(self):
        self.hooks['pre_api_request'](session_id='other-session', approx_input_tokens=42)
        self.assertEqual(self.case.events(), [])


if __name__ == '__main__':
    unittest.main()
