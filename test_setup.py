"""Offline first-run setup behavior over native config and secret files."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from starlette.requests import Request

from forsic_plugin import setup as s, connections
from forsic_plugin.dashboard import plugin_api as api
from forsic_plugin.remote_policy import ForsicRemotePolicy, install_remote_access


class SetupFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.env = patch.dict(os.environ, {'HERMES_HOME': str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        from hermes_cli.config import save_config
        save_config({'model': {'provider': 'custom', 'base_url': 'http://localhost:11434/v1', 'default': 'old'},
                     'dashboard': {}, 'plugins': {'enabled': ['forsic'], 'entries': {'forsic': {'settings': {}}}}})
        self.app = FastAPI()
        self.app.include_router(api.router, prefix='/api/plugins/forsic')
        self.client = TestClient(self.app, base_url='http://localhost:8860', client=('127.0.0.1', 40000))
        self.body = {'llm_base_url': 'http://localhost:11434/v1', 'llm_model': 'new', 'llm_context_length': None,
                     'evidence_directory': str(self.evidence), 'evidence_directory_label': '자료', 'hosting_mode': 'local',
                     'private_lan_host': '', 'public_url': '', 'login_username': ''}

    def cfg(self):
        return connections.config()

    def request(self, *, host='localhost:8860', peer='127.0.0.1', headers=()):
        return Request({'type': 'http', 'path': '/', 'query_string': b'', 'client': (peer, 40000),
                        'scheme': 'http', 'server': ('127.0.0.1', 8860), 'headers': [(b'host', host.encode()), *headers]})

    def save(self, body=None):
        response = self.client.put('/api/plugins/forsic/setup', json=body or self.body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class SetupTests(SetupFixture, unittest.TestCase):
    def test_legacy_install_initialized_and_read_has_no_network_or_model(self):
        with patch('httpx.Client') as network, patch('subprocess.run') as process:
            result = self.client.get('/api/plugins/forsic/setup').json()
        self.assertTrue(result['completed'])
        self.assertFalse(result['required'])
        network.assert_not_called()
        process.assert_not_called()

    def test_fresh_marker_requires_setup_and_pending_does_not_apply_live(self):
        from hermes_cli.config import save_config
        cfg = self.cfg()
        s.settings(cfg)['setup_completed'] = False
        save_config(cfg)
        self.assertTrue(self.client.get('/api/plugins/forsic/setup').json()['required'])
        with patch('httpx.Client') as network, patch('subprocess.run') as process:
            result = self.save()
        self.assertTrue(result['pending'])
        self.assertFalse(result['required'])
        self.assertFalse(result['completed'])
        self.assertEqual(self.cfg()['model']['default'], 'old')
        self.assertNotIn('evidence_directory', s.settings(self.cfg()))
        network.assert_not_called()
        process.assert_not_called()
        connections.apply_pending_llm()
        s.apply_pending_setup()
        self.assertEqual(self.cfg()['model']['default'], 'new')
        self.assertTrue(s.settings(self.cfg())['setup_completed'])
        self.assertFalse(self.client.get('/api/plugins/forsic/setup').json()['pending'])

    def test_secrets_not_returned_and_only_rotated_at_launch(self):
        from hermes_cli.config import save_env_value, load_env, get_env_path
        save_env_value(s.LLM_KEY, 'old-api-secret')
        result = self.save({**self.body, 'llm_api_key': 'new-api-secret'})
        self.assertNotIn('new-api-secret', json.dumps(result))
        self.assertNotIn('new-api-secret', (self.home/'config.yaml').read_text())
        self.assertEqual(load_env()[s.LLM_KEY], 'old-api-secret')
        self.assertEqual(load_env()[s.LLM_PENDING_KEY], 'new-api-secret')
        self.assertEqual(get_env_path().stat().st_mode & 0o777, 0o600)
        connections.apply_pending_llm()
        s.apply_pending_setup()
        self.assertEqual(load_env()[s.LLM_KEY], 'new-api-secret')
        self.assertNotIn(s.LLM_PENDING_KEY, load_env())
        self.assertEqual(self.cfg()['model']['key_env'], s.LLM_KEY)

    def test_invalid_folder_or_host_is_rejected_before_any_secret_write(self):
        before = (self.home/'config.yaml').read_bytes()
        for changes in ({'evidence_directory': 'relative'}, {'evidence_directory': str(self.root/'missing')},
                        {'evidence_directory': str(self.home)}, {'evidence_directory': '/etc'},
                        {'hosting_mode': 'private_lan', 'private_lan_host': '0.0.0.0'},
                        {'hosting_mode': 'private_lan', 'private_lan_host': '8.8.8.8'},
                        {'hosting_mode': 'private_lan', 'private_lan_host': '127.0.0.1'},
                        {'hosting_mode': 'online', 'public_url': 'http://example.com'},
                        {'hosting_mode': 'online', 'public_url': 'https://localhost'},
                        {'hosting_mode': 'online', 'public_url': 'https://example.com/app'}):
            with self.subTest(changes=changes), patch('hermes_cli.config.save_env_value') as write:
                response = self.client.put('/api/plugins/forsic/setup', json={**self.body, 'llm_api_key': 'secret', **changes})
                self.assertEqual(response.status_code, 400)
                write.assert_not_called()
                self.assertEqual((self.home/'config.yaml').read_bytes(), before)

    def test_symlink_to_private_folder_cannot_be_shared(self):
        link = self.root / 'linked-home'
        link.symlink_to(self.home, target_is_directory=True)
        response = self.client.put('/api/plugins/forsic/setup', json={**self.body, 'evidence_directory': str(link)})
        self.assertEqual(response.status_code, 400)

    def test_local_admin_checks_real_peer_host_origin_forwarding_and_profile(self):
        self.assertTrue(s.local_admin(self.request()))
        for request in (self.request(peer='192.168.1.10'), self.request(peer=''), self.request(host='example.com'),
                        self.request(host='localhost/path'), self.request(headers=[(b'origin', b'https://evil.example')]),
                        self.request(headers=[(b'x-forwarded-for', b'127.0.0.1')]),
                        self.request(headers=[(b'x-real-ip', b'127.0.0.1')]),
                        self.request(headers=[(b'forwarded', b'for=127.0.0.1')])):
            self.assertFalse(s.local_admin(request))
        response = self.client.put('/api/plugins/forsic/setup?profile=other', json=self.body)
        self.assertEqual(response.status_code, 403)

    def test_explicit_unscoped_host_entry_keeps_served_profile_and_bound_checks(self):
        request = self.request()
        request.state.forsic_tenant_scope = None
        with patch('forsic_plugin.tenants.current_scope', side_effect=PermissionError('unknown active tenant')) as current:
            self.assertTrue(s.local_admin(request))
            current.assert_not_called()
            other = self.request()
            other.state.forsic_tenant_scope = None
            other.scope['query_string'] = b'profile=other'
            self.assertFalse(s.local_admin(other))
            current.assert_not_called()
        request.state.forsic_tenant_scope = {'profile_name': 'selected'}
        with patch('forsic_plugin.tenants.current_scope', side_effect=PermissionError('stale tenant ownership')):
            with self.assertRaisesRegex(PermissionError, 'stale tenant'):
                s.local_admin(request)

    def test_remote_projection_has_no_private_values_and_no_mutation(self):
        client = TestClient(self.app, base_url='https://forsic.example.com', client=('127.0.0.1', 40000))
        value = client.get('/api/plugins/forsic/setup').json()
        self.assertFalse(value['local_admin'])
        self.assertNotIn('values', value)
        self.assertNotIn(str(self.evidence), json.dumps(value))
        for path in ('/setup', '/setup/check-model'):
            method = client.put if path == '/setup' else client.post
            self.assertEqual(method('/api/plugins/forsic'+path, json=self.body if path == '/setup' else {'llm_base_url': self.body['llm_base_url'], 'llm_model': 'new'}).status_code, 403)

    def test_native_auth_dependency_still_rejects_setup_without_session(self):
        from hermes_cli import web_server as native
        app = FastAPI()
        app.state.auth_required = False
        app.include_router(api.router, prefix='/api/plugins/forsic', dependencies=[Depends(native._require_token)])
        client = TestClient(app, base_url='http://localhost:8860', client=('127.0.0.1', 40000))
        self.assertEqual(client.get('/api/plugins/forsic/setup').status_code, 401)
        self.assertEqual(client.get('/api/plugins/forsic/setup', headers={native._SESSION_HEADER_NAME: native._SESSION_TOKEN}).status_code, 200)

    def test_initial_setup_blocks_http_and_websocket_execution(self):
        from starlette.websockets import WebSocketDisconnect
        settings = {'setup_completed': False}
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/forsic')
        install_remote_access(app, settings=settings, home=self.home)
        client = TestClient(app, base_url='http://localhost:8860', client=('127.0.0.1', 40000))
        self.assertEqual(client.post('/api/plugins/forsic/investigations', json={}).status_code, 409)
        self.assertEqual(client.post('/api/anything', json={}).status_code, 409)
        self.assertEqual(client.get('/api/plugins/forsic/setup').status_code, 200)
        with self.assertRaises(WebSocketDisconnect) as raised:
            with client.websocket_connect('/api/ws'):
                pass
        self.assertEqual(raised.exception.code, 4409)

    def test_check_only_gets_bounded_catalog_and_never_follows_redirect_or_uses_active_key_for_edited_url(self):
        from hermes_cli.config import save_env_value
        save_env_value(s.LLM_KEY, 'old-api-secret')
        self.save({**self.body, 'llm_base_url': 'https://different.example/v1', 'clear_llm_key': True})
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={'data': [{'id': 'new'}]})
        original = httpx.Client
        with patch.object(httpx, 'Client', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            result = s.check_model({'llm_base_url': 'https://different.example/v1', 'llm_model': 'new'})
        self.assertTrue(result['model_found'])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].method, 'GET')
        self.assertEqual(str(seen[0].url), 'https://different.example/v1/models')
        self.assertNotIn('authorization', seen[0].headers)
        self.assertFalse(seen[0].content)

    def test_model_check_errors_are_generic_and_catalog_size_is_bounded(self):
        original = httpx.Client
        for response in (httpx.Response(302, headers={'location': 'https://elsewhere.example'}),
                         httpx.Response(500, text='provider-sensitive-secret'), httpx.Response(200, content=b'x'*262145)):
            seen = []
            def handler(request):
                seen.append(request)
                return response
            with self.subTest(response=response.status_code), patch.object(httpx, 'Client', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
                result = s.check_model({'llm_base_url': 'http://localhost:11434/v1', 'llm_model': 'new'})
                self.assertFalse(result['reachable'])
                self.assertEqual(len(seen), 1)
                self.assertNotIn('provider-sensitive-secret', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
