"""Offline native-auth/proxy/LAN hosting integration with synthetic folders."""
import json
import uuid
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_setup import SetupFixture
from forsic_plugin import connections, setup as s
from forsic_plugin.dashboard import plugin_api as api
from forsic_plugin.remote_policy import install_remote_access


class HostingTests(SetupFixture, unittest.TestCase):
    def online_body(self, **changes):
        return {**self.body, 'hosting_mode': 'online', 'public_url': 'https://forsic.example.com',
                'login_username': 'examiner', 'login_password': 'synthetic-test-password', **changes}

    def apply(self):
        s.validate_pending_setup()
        connections.apply_pending_llm()
        s.apply_pending_setup()

    def test_private_lan_uses_native_opt_in_and_preserves_loopback_primary(self):
        self.save({**self.body, 'hosting_mode': 'private_lan', 'private_lan_host': '192.168.20.4'})
        self.assertFalse(self.cfg()['dashboard'].get('trusted_private_lan_no_login'))
        self.apply()
        from hermes_cli import web_server as native
        app = FastAPI()
        with patch.object(native, 'app', app):
            native._configure_auth_gate('127.0.0.1', False, None, None)
        self.assertFalse(app.state.auth_required)
        self.assertEqual(app.state.private_lan_host, '192.168.20.4')
        self.assertEqual(app.state.private_lan_listener_hosts, frozenset({'127.0.0.1', '192.168.20.4'}))
        self.assertTrue(s.settings(self.cfg())['remote_evidence_directory_only'])

    def test_online_native_password_login_and_cookie_auth_through_local_proxy(self):
        from hermes_cli import web_server as native
        from hermes_cli.dashboard_auth.middleware import gated_auth_middleware
        from hermes_cli.dashboard_auth.routes import router as auth_router, _reset_password_rate_limit
        from hermes_cli.dashboard_auth.registry import register_global_provider, unregister_global_provider
        from plugins.dashboard_auth.basic import BasicAuthProvider, _settings
        self.save(self.online_body())
        self.assertFalse(self.cfg()['dashboard'].get('public_url'))
        self.apply()
        provider = BasicAuthProvider(**_settings())
        register_global_provider(provider)
        self.addCleanup(unregister_global_provider, provider.name, provider)
        _reset_password_rate_limit()
        app = FastAPI()
        with patch.object(native, 'app', app):
            native._configure_auth_gate('127.0.0.1', False, None, None)
        self.assertTrue(app.state.auth_required)
        self.assertFalse(app.state.trusted_private_lan_no_login)
        app.state.bound_host = '127.0.0.1'
        app.middleware('http')(gated_auth_middleware)
        app.middleware('http')(native.host_header_middleware)
        app.include_router(auth_router)
        app.include_router(api.router, prefix='/api/plugins/forsic')
        install_remote_access(app, settings=s.settings(self.cfg()), home=self.home)
        client = TestClient(app, base_url='https://forsic.example.com', client=('127.0.0.1', 40000))
        headers = {'X-Forwarded-For': '127.0.0.1', 'X-Forwarded-Proto': 'https'}
        self.assertEqual(client.get('/login', headers=headers).status_code, 200)
        self.assertEqual(client.get('/api/plugins/forsic/setup', headers=headers).status_code, 401)
        wrong = client.post('/auth/password-login', json={'provider': 'basic', 'username': 'examiner', 'password': 'wrong'}, headers=headers)
        self.assertEqual(wrong.status_code, 401)
        login = client.post('/auth/password-login', json={'provider': 'basic', 'username': 'examiner', 'password': 'synthetic-test-password'}, headers=headers)
        self.assertEqual(login.status_code, 200, login.text)
        self.assertIn('Secure', login.headers['set-cookie'])
        response = client.get('/api/plugins/forsic/setup', headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['local_admin'])
        self.assertEqual(response.json()['hosting_current']['mode'], 'online')
        self.assertEqual(client.put('/api/plugins/forsic/setup', json=self.online_body(), headers=headers).status_code, 403)
        self.assertEqual(client.get('/api/config', headers=headers).status_code, 403)
        self.assertEqual(client.get('/api/auth/me', headers=headers).status_code, 200)
        self.assertEqual(client.post('/api/auth/ws-ticket', headers=headers).status_code, 200)

    def test_online_proxy_folder_routes_cannot_escape_even_when_peer_is_loopback(self):
        self.save(self.online_body())
        self.apply()
        catalog = Mock(home=self.home, intake=self.root/'intake')
        catalog.create_conversation.return_value = {'session_id': 'synthetic'}
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/forsic')
        install_remote_access(app, settings=s.settings(self.cfg()), home=self.home)
        client = TestClient(app, base_url='https://forsic.example.com', client=('127.0.0.1', 40000))
        outside = self.root/'outside'
        outside.mkdir()
        with patch.object(api, 'directory', return_value=catalog):
            for headers in ({}, {'X-Forwarded-For': '127.0.0.1'}):
                self.assertEqual(client.get('/api/plugins/forsic/evidence-browser', params={'path': str(outside)}, headers=headers).status_code, 403)
                self.assertEqual(client.get('/api/plugins/forsic/evidence-connection', params={'path': str(outside)}, headers=headers).status_code, 403)
                response = client.post('/api/plugins/forsic/investigations', json={'path': str(outside), 'request_id': str(uuid.uuid4())}, headers=headers)
                self.assertEqual(response.status_code, 403, response.text)
        catalog.create_conversation.assert_not_called()

    def test_expired_pending_folder_preflight_prevents_hosting_after_default_save(self):
        self.save(self.online_body())
        self.evidence.rmdir()
        with self.assertRaises(ValueError):
            s.validate_pending_setup()
        self.assertEqual(self.cfg()['model']['default'], 'new')
        self.assertFalse(self.cfg()['dashboard'].get('public_url'))

    def test_endpoint_changes_require_explicit_existing_key_disposition(self):
        from hermes_cli.config import save_config, save_env_value
        cfg = self.cfg()
        cfg['model']['key_env'] = s.LLM_KEY
        save_config(cfg)
        save_env_value(s.LLM_KEY, 'active-api-secret')
        changed = {**self.body, 'llm_base_url': 'https://different.example/v1'}
        self.assertEqual(self.client.put('/api/plugins/forsic/setup', json=changed).status_code, 400)
        with self.assertRaises(ValueError):
            connections.save({'llm_base_url': changed['llm_base_url'], 'llm_model': 'new'})
        self.save({**changed, 'clear_llm_key': True})
        self.apply()
        self.assertNotIn('key_env', self.cfg()['model'])
        self.assertEqual(connections.key(s.LLM_KEY), 'active-api-secret')

    def test_setup_validation_never_echoes_malformed_credentials(self):
        for secret in ('llm_api_key', 'login_password'):
            response = self.client.put('/api/plugins/forsic/setup', json={**self.body, secret: {'secret': 'sensitive-value'}})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn('sensitive-value', response.text)

    def test_good_symlinks_and_parent_of_private_storage_cannot_be_shared(self):
        link = self.root/'linked-evidence'
        link.symlink_to(self.evidence, target_is_directory=True)
        for path in (link, self.root):
            response = self.client.put('/api/plugins/forsic/setup', json={**self.body, 'evidence_directory': str(path)})
            self.assertEqual(response.status_code, 400)

    def test_pending_secret_stays_bound_when_second_save_changes_endpoint(self):
        first = {**self.body, 'llm_base_url': 'https://first.example/v1', 'llm_api_key': 'first-endpoint-secret'}
        self.save(first)
        changed = {**self.body, 'llm_base_url': 'https://second.example/v1'}
        response = self.client.put('/api/plugins/forsic/setup', json=changed)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.cfg()['model']['base_url'], first['llm_base_url'])
        self.assertEqual(connections.model_credential(self.cfg()), first['llm_api_key'])
        self.save({**changed, 'llm_api_key': 'second-endpoint-secret'})
        self.apply()
        self.assertEqual(connections.model_credential(self.cfg()), 'second-endpoint-secret')
        self.assertEqual(self.cfg()['model']['base_url'], changed['llm_base_url'])

    def test_clear_removes_legacy_main_and_matching_auxiliary_key_references(self):
        from hermes_cli.config import save_config
        cfg = self.cfg()
        cfg['model']['api_key_env'] = 'LEGACY_MODEL_KEY'
        cfg['auxiliary'] = {'title': {'provider': 'custom', 'base_url': self.body['llm_base_url'],
                                     'api_key': 'legacy-title-secret', 'api_key_env': 'LEGACY_AUX_KEY'}}
        save_config(cfg)
        self.save({**self.body, 'llm_base_url': 'https://second.example/v1', 'clear_llm_key': True})
        self.apply()
        model, title = self.cfg()['model'], self.cfg()['auxiliary']['title']
        self.assertEqual(model.get('api_key'), 'no-key-required')
        self.assertEqual(title.get('api_key'), 'no-key-required')
        for key in ('key_env', 'api_key_env', 'api'):
            self.assertNotIn(key, model)
            self.assertNotIn(key, title)
