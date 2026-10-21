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

    def test_model_default_publishes_while_hosting_requires_launch(self):
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
        self.assertEqual(self.cfg()['model']['default'], 'new')
        self.assertNotIn('evidence_directory', s.settings(self.cfg()))
        network.assert_not_called()
        process.assert_not_called()
        connections.apply_pending_llm()
        s.apply_pending_setup()
        self.assertEqual(self.cfg()['model']['default'], 'new')
        self.assertTrue(s.settings(self.cfg())['setup_completed'])
        self.assertFalse(self.client.get('/api/plugins/forsic/setup').json()['pending'])

    def test_secrets_publish_without_retiring_previously_bound_slots(self):
        from hermes_cli.config import save_env_value, load_env, get_env_path
        save_env_value(s.LLM_KEY, 'old-api-secret')
        result = self.save({**self.body, 'llm_api_key': 'new-api-secret'})
        self.assertNotIn('new-api-secret', json.dumps(result))
        self.assertNotIn('new-api-secret', (self.home/'config.yaml').read_text())
        self.assertEqual(load_env()[s.LLM_KEY], 'old-api-secret')
        pending_slot = self.cfg()['model']['key_env']
        self.assertEqual(load_env()[pending_slot], 'new-api-secret')
        self.assertEqual(get_env_path().stat().st_mode & 0o777, 0o600)
        connections.apply_pending_llm()
        s.apply_pending_setup()
        self.assertEqual(load_env()[self.cfg()['model']['key_env']], 'new-api-secret')
        self.assertNotIn(s.LLM_PENDING_KEY, load_env())
        self.assertEqual(self.cfg()['model']['key_env'], pending_slot)

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


class OptionalLlmKeyTests(SetupFixture, unittest.TestCase):
    def active_key(self):
        from hermes_cli.config import save_config, save_env_value
        cfg = self.cfg()
        cfg['model']['key_env'] = s.LLM_KEY
        cfg['auxiliary'] = {
            'title': {'provider': 'custom', 'base_url': self.body['llm_base_url'], 'key_env': s.LLM_KEY},
            'other': {'provider': 'custom', 'base_url': 'https://other.example/v1', 'key_env': 'OTHER_KEY'},
        }
        save_env_value(s.LLM_KEY, 'SYNTHETIC_ACTIVE_SECRET')
        save_config(cfg)

    def connection_save(self, **extra):
        response = self.client.put('/api/plugins/forsic/connections', json={
            'llm_base_url': self.body['llm_base_url'], 'llm_model': 'new', **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def launch(self):
        s.validate_pending_setup()
        connections.apply_pending_llm()
        s.apply_pending_setup()

    def test_both_forms_publish_once_and_omission_preserves_selected_key(self):
        from hermes_cli.config import load_env
        self.active_key()
        with patch('socket.socket.connect', side_effect=AssertionError('offline settings test')):
            result = self.connection_save(llm_api_key='  SYNTHETIC_NEXT_SECRET  ')
            self.assertFalse(result['llm_pending'])
            slot = self.cfg()['model']['key_env']
            self.assertEqual(connections.model_credential(self.cfg()), 'SYNTHETIC_NEXT_SECRET')
            self.connection_save()
            self.save()
            self.assertEqual(self.cfg()['model']['key_env'], slot)
            self.launch()
        cfg = self.cfg()
        self.assertEqual(cfg['auxiliary']['title']['key_env'], slot)
        self.assertEqual(cfg['auxiliary']['other']['key_env'], 'OTHER_KEY')
        self.assertEqual(load_env()[s.LLM_KEY], 'SYNTHETIC_ACTIVE_SECRET')
        self.assertNotIn('SYNTHETIC_', (self.home / 'config.yaml').read_text())
        self.assertNotIn('SYNTHETIC_', json.dumps(result))

    def test_explicit_blank_whitespace_and_clear_publish_keyless_retain_bound_slot(self):
        from hermes_cli.config import load_env
        for extra in ({'llm_api_key': ''}, {'llm_api_key': ' \t\n '}, {'clear_llm_key': True}):
            with self.subTest(extra=extra):
                self.active_key()
                result = self.connection_save(**extra)
                self.assertFalse(result['llm']['key_configured'])
                self.assertFalse(result['llm_pending'])
                self.assertEqual(load_env()[s.LLM_KEY], 'SYNTHETIC_ACTIVE_SECRET')
                setup = self.client.get('/api/plugins/forsic/setup').json()
                self.assertFalse(setup['llm_key_configured'])
                self.launch()
                self.assertEqual(self.cfg()['model']['api_key'], 'no-key-required')
                self.assertNotIn('key_env', self.cfg()['model'])
                self.assertEqual(self.cfg()['auxiliary']['title']['api_key'], 'no-key-required')
                self.assertEqual(connections.model_credential(self.cfg()), 'no-key-required')
                self.assertIn(s.LLM_KEY, load_env())
        self.active_key()
        self.save({**self.body, 'llm_api_key': ' \n '})
        self.assertFalse(self.client.get('/api/plugins/forsic/setup').json()['llm_key_configured'])
        self.launch()
        self.assertEqual(self.cfg()['model']['api_key'], 'no-key-required')

    def test_new_endpoint_requires_explicit_choice_for_active_and_pending_credentials(self):
        self.active_key()
        changed = {'llm_base_url': 'https://changed.example/v1', 'llm_model': 'new'}
        for url, body in (('/api/plugins/forsic/connections', changed),
                          ('/api/plugins/forsic/setup', {**self.body, **changed})):
            self.assertEqual(self.client.put(url, json=body).status_code, 400)
        self.connection_save(llm_api_key='SYNTHETIC_PENDING_SECRET')
        for url, body in (('/api/plugins/forsic/connections', changed),
                          ('/api/plugins/forsic/setup', {**self.body, **changed})):
            self.assertEqual(self.client.put(url, json=body).status_code, 400)
        self.connection_save(**changed, llm_api_key='')
        self.launch()
        self.assertEqual(self.cfg()['model']['base_url'], changed['llm_base_url'])
        self.assertEqual(self.cfg()['model']['api_key'], 'no-key-required')

    def test_failed_live_publication_keeps_config_and_bound_credentials(self):
        from hermes_cli.config import load_env
        self.active_key()
        config_before = (self.home / 'config.yaml').read_bytes()
        with patch('hermes_cli.config.save_config', side_effect=OSError('synthetic write failure')):
            with self.assertRaises(OSError):
                connections.save({'llm_base_url': self.body['llm_base_url'], 'llm_model': 'new',
                                  'llm_api_key': 'SYNTHETIC_FAILED_SECRET'})
        self.assertEqual((self.home / 'config.yaml').read_bytes(), config_before)
        self.assertEqual(connections.model_credential(self.cfg()), 'SYNTHETIC_ACTIVE_SECRET')
        self.connection_save(llm_api_key='SYNTHETIC_NEXT_SECRET')
        self.assertEqual(connections.model_credential(self.cfg()), 'SYNTHETIC_NEXT_SECRET')
        self.assertEqual(load_env()[s.LLM_KEY], 'SYNTHETIC_ACTIVE_SECRET')

    def test_connection_key_choice_preserves_pending_hosting_and_password_through_launch(self):
        from hermes_cli.config import load_env
        self.save({**self.body, 'hosting_mode': 'private_lan', 'private_lan_host': '192.168.10.2',
                   'llm_api_key': 'SYNTHETIC_STAGED_SECRET', 'login_password': 'synthetic-long-passphrase'})
        self.connection_save(llm_api_key='')
        pending = s.settings(self.cfg())['setup_pending'].copy()
        self.assertTrue(pending['password_pending'])
        s.validate_pending_setup()
        connections.apply_pending_llm()
        remaining = s.settings(self.cfg())['setup_pending']
        self.assertEqual(remaining, {k: v for k, v in pending.items() if not k.startswith('llm_key_')})
        s.apply_pending_setup()
        cfg = self.cfg()
        self.assertTrue(cfg['dashboard']['trusted_private_lan_no_login'])
        self.assertEqual(cfg['dashboard']['private_lan_host'], '192.168.10.2')
        self.assertIn('HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH', load_env())
        self.assertNotIn('setup_pending', s.settings(cfg))

    def test_malformed_secret_requests_do_not_echo_values_or_write(self):
        self.active_key()
        routes = [('/api/plugins/forsic/connections', {'llm_base_url': self.body['llm_base_url'], 'llm_model': 'new'}),
                  ('/api/plugins/forsic/setup', self.body),
                  ('/api/plugins/forsic/setup/check-model', {'llm_base_url': self.body['llm_base_url'], 'llm_model': 'new'})]
        for url, body in routes:
            for value in (None, ['SYNTHETIC_PRIVATE'], {'SYNTHETIC_PRIVATE': 'SYNTHETIC_PRIVATE'}, 'SYNTHETIC_PRIVATE\ninside'):
                with self.subTest(url=url, value=value), patch('hermes_cli.config.save_config') as config_write, \
                        patch('hermes_cli.config.save_env_value') as env_write:
                    response = self.client.request('POST' if url.endswith('check-model') else 'PUT', url,
                                                   json={**body, 'llm_api_key': value})
                    self.assertIn(response.status_code, (400, 422), response.text)
                    self.assertNotIn('SYNTHETIC_PRIVATE', response.text)
                    config_write.assert_not_called()
                    env_write.assert_not_called()

    def test_catalog_check_omitted_resolves_only_matching_selected_key_and_blank_is_anonymous(self):
        self.active_key()
        seen = []
        original = httpx.Client
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={'data': [{'id': 'new'}]})
        with patch.object(httpx, 'Client', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            body = {'llm_base_url': self.body['llm_base_url'], 'llm_model': 'new'}
            for extra, expected in (({}, 'Bearer SYNTHETIC_ACTIVE_SECRET'), ({'llm_api_key': ''}, None),
                                    ({'llm_api_key': ' \t '}, None),
                                    ({'llm_base_url': 'https://unselected.example/v1'}, None)):
                response = self.client.post('/api/plugins/forsic/setup/check-model', json={**body, **extra})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(seen[-1].headers.get('authorization'), expected)
            self.connection_save(llm_model='old', llm_api_key='SYNTHETIC_STAGED_SECRET')
            self.assertNotIn('llm_pending', connections.preferences(self.cfg()))
            self.client.post('/api/plugins/forsic/setup/check-model', json=body)
            self.assertEqual(seen[-1].headers.get('authorization'), 'Bearer SYNTHETIC_STAGED_SECRET')
            self.client.post('/api/plugins/forsic/setup/check-model', json={**body, 'llm_api_key': ''})
            self.assertNotIn('authorization', seen[-1].headers)


if __name__ == '__main__':
    unittest.main()
