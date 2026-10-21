"""Offline tests for saved defaults and explicit, private draft model requests."""
from contextlib import ExitStack
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forsic_plugin import connections as c
from forsic_plugin.dashboard import plugin_api as api


class DraftConnectionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': folder}))
        self.cfg = {'model': {'provider': 'custom', 'default': 'main', 'base_url': 'https://main.test/v1',
                              'key_env': 'MAIN_KEY'},
                    'plugins': {'entries': {'forsic': {'settings': {'connections': {'ren_llm': {
                        'enabled': True, 'base_url': 'https://ren.test/v1', 'model': 'review', 'key_env': 'REN_KEY'}}}}}}}
        self.stack.enter_context(patch.object(c, 'config', return_value=self.cfg))
        self.stack.enter_context(patch.object(c, 'key', side_effect=lambda name='VT_APIKEY': {
            'MAIN_KEY': 'PRIVATE_MAIN', 'REN_KEY': 'PRIVATE_REN'}.get(name, '')))
        self.config_write = self.stack.enter_context(patch('hermes_cli.config.save_config'))
        self.secret_write = self.stack.enter_context(patch('hermes_cli.config.save_env_value'))
        self.stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('offline only')))
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/forsic')
        self.client = TestClient(app, base_url='http://localhost:8860', client=('127.0.0.1', 1))
        self.original_client = httpx.Client
        self.seen = []
        self.status = 200
        self.stream_text = 'data: {"id":"test","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"PRIVATE_PROVIDER_OUTPUT"}}]}\n\ndata: [DONE]\n\n'

    def handler(self, request):
        self.seen.append(request)
        if self.status == 'timeout':
            raise httpx.ReadTimeout('PRIVATE_TRANSPORT_ERROR', request=request)
        if self.status != 200:
            return httpx.Response(self.status, json={'error': {'message': 'PRIVATE_PROVIDER_BODY'}},
                                  headers={'location': 'https://redirect.test/PRIVATE_DESTINATION'})
        return httpx.Response(200, stream=httpx.ByteStream(self.stream_text.encode()), headers={'content-type': 'text/event-stream'})

    def post(self, **body):
        handler = self.handler
        class OfflineClient(self.original_client):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx.MockTransport(handler), **kwargs)
        with patch.object(httpx, 'Client', OfflineClient):
            return self.client.post('/api/plugins/forsic/connections/test', json={
                'connection': 'main', 'base_url': 'https://main.test/v1', 'model': 'draft', **body})

    def test_main_ren_and_explicit_blank_are_scoped_without_saving(self):
        for body, expected in (({}, 'PRIVATE_MAIN'),
                               ({'connection': 'ren', 'base_url': 'https://ren.test/v1'}, 'PRIVATE_REN'),
                               ({'api_key': ''}, 'no-key-required'),
                               ({'api_key': 'DRAFT_ONLY'}, 'DRAFT_ONLY'), ({}, 'PRIVATE_MAIN')):
            response = self.post(**body)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertTrue(result['model_responded'])
            self.assertEqual(result['status'], 'ok')
            self.assertGreaterEqual(result['latency_ms'], 0)
            self.assertEqual(self.seen[-1].headers['authorization'], 'Bearer ' + expected)
            payload = json.loads(self.seen[-1].content)
            self.assertEqual(payload['messages'], [{'role': 'user', 'content': 'Reply with OK.'}])
            self.assertEqual(payload['max_tokens'], 32)
            self.assertTrue(payload['stream'])
            self.assertNotIn('PRIVATE_', response.text)
            self.assertNotIn('DRAFT_ONLY', response.text)
        self.config_write.assert_not_called()
        self.secret_write.assert_not_called()

    def test_endpoint_change_requires_explicit_key_choice(self):
        for connection in ('main', 'ren'):
            response = self.post(connection=connection, base_url='https://new.test/v1')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.seen, [])
        self.assertEqual(self.post(base_url='https://new.test/v1', api_key='').status_code, 200)

    def test_errors_are_fixed_private_status_and_no_retry(self):
        for code, status in ((401, 'authentication_error'), (404, 'model_error'), (500, 'provider_error'), (302, 'provider_error')):
            self.status = code
            before = len(self.seen)
            response = self.post()
            self.assertEqual(response.json()['status'], status)
            self.assertEqual(len(self.seen), before + 1)
            self.assertEqual(self.seen[-1].url.host, 'main.test')
            self.assertNotIn('PRIVATE_', response.text)
        self.status = 200
        self.stream_text = 'data: PRIVATE_MALFORMED\n\n'
        self.assertEqual(self.post().json()['status'], 'invalid_response')

    def test_timeout_and_oversized_responses_are_bounded_and_private(self):
        self.status = 'timeout'
        response = self.post()
        self.assertEqual(response.json()['status'], 'timeout')
        self.assertNotIn('PRIVATE_', response.text)
        self.status = 200
        self.stream_text = 'data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'content': 'x' * 70000}}]}) + '\n\n'
        response = self.post()
        self.assertEqual(response.json()['status'], 'invalid_response')
        self.assertLess(len(response.content), 500)

    def test_remote_or_malformed_input_never_calls_provider(self):
        self.client.base_url = 'http://remote.example:8860'
        self.assertEqual(self.post().status_code, 403)
        self.client.base_url = 'http://localhost:8860'
        for key in (None, ['PRIVATE_INPUT'], 'PRIVATE_INPUT\nBAD'):
            response = self.post(api_key=key)
            self.assertIn(response.status_code, (400, 422))
            self.assertNotIn('PRIVATE_INPUT', response.text)
        self.assertEqual(self.seen, [])

    def test_pending_main_key_and_missing_profile_key(self):
        opts = self.cfg['plugins']['entries']['forsic']['settings']
        opts['setup_pending'] = {'llm_key_action': 'replace', 'llm_key_base_url': 'https://pending.test/v1',
                                 'llm_key_env': 'REN_KEY'}
        opts['connections']['llm_pending'] = {'base_url': 'https://pending.test/v1', 'model': 'next'}
        self.assertEqual(self.post(base_url='https://pending.test/v1').status_code, 200)
        self.assertEqual(self.seen[-1].headers['authorization'], 'Bearer PRIVATE_REN')
        opts['setup_pending']['llm_key_env'] = 'MISSING_PROFILE_KEY'
        self.assertEqual(self.post(base_url='https://pending.test/v1').status_code, 400)


if __name__ == '__main__':
    unittest.main()
