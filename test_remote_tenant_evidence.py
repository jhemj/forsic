"""Fresh tenant selection must retain the host's remote evidence boundary."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from forsic_plugin.dashboard import plugin_api as api
from forsic_plugin.remote_policy import ForsicRemoteAccessMiddleware, ForsicRemotePolicy
from forsic_plugin.tenants import TenantRegistry, _config, current_scope


class RemoteTenantEvidenceTests(unittest.TestCase):
    prefix = '/api/plugins/forsic'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.patch(patch.dict(os.environ, {'HERMES_HOME': str(self.home)}))
        # Native profile creation pins process globals; keep fixture state local.
        import hermes_constants
        from agent import secret_scope
        from tui_gateway import launch_profile_policy, server
        for module, name in ((hermes_constants, '_PINNED_PROCESS_HERMES_HOME'),
                             (secret_scope, '_MULTIPLEX_ACTIVE'),
                             (secret_scope, '_AUTO_PINNED_HOME'),
                             (launch_profile_policy, '_snapshot')):
            self.patch(patch.object(module, name, getattr(module, name)))
        self.patch(patch.object(server, '_served_profile_homes', set(server._served_profile_homes)))
        (self.home / 'config.yaml').write_text('model:\n  default: synthetic-no-model\n')
        registry = TenantRegistry(self.home)
        registry.bootstrap_legacy(intake_root=self.home / 'legacy-intake',
                                  library_store=self.home / 'legacy-library.sqlite3')
        self.tenant = registry.create('Fresh synthetic tenant', operation_id=str(uuid.uuid4()))
        self.params = {'tenant_id': self.tenant['tenant_id'], 'profile': self.tenant['profile_name']}
        self.shared = self.root / 'shared'
        self.shared.mkdir()
        self.child = self.shared / 'case-one'
        self.child.mkdir()
        (self.child / 'trace.txt').write_text('Synthetic only\n')
        self.outside = self.root / 'outside'
        self.outside.mkdir()
        (self.shared / 'escape').symlink_to(self.outside, target_is_directory=True)
        (self.shared / 'internal-link').symlink_to(self.child, target_is_directory=True)
        self.settings = {'remote_evidence_directory_only': True,
                         'evidence_directory': str(self.shared),
                         'evidence_directory_label': 'Shared synthetic evidence'}
        # Match real load_config's profile resolution without its cache/backups.
        self.patch(patch.object(api, 'load_config', side_effect=lambda: _config(hermes_constants.get_hermes_home())))
        self.catalog = Mock(home=Path(self.tenant['profile_home']), intake=Path(self.tenant['intake_root']))
        self.catalog.create_conversation.return_value = {'case_id': 'synthetic', 'session_id': 'synthetic'}
        def selected_catalog():
            self.assertEqual(current_scope()['tenant_id'], self.tenant['tenant_id'])
            return self.catalog
        self.patch(patch.object(api, 'directory', side_effect=selected_catalog))

    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def client(self, peer='192.0.2.20', **settings):
        app = FastAPI()
        app.state.initial_profile = 'default'
        app.include_router(api.router, prefix=self.prefix)
        app.add_middleware(ForsicRemoteAccessMiddleware, policy=ForsicRemotePolicy(
            {**self.settings, **settings}, self.home))
        return TestClient(app, base_url='http://192.0.2.50:8860', client=(peer, 1234))

    def test_fresh_profile_keeps_startup_shared_root_for_sources_and_intake(self):
        settings = _config(self.tenant['profile_home'])['plugins']['entries']['forsic']['settings']
        self.assertNotIn('remote_evidence_directory_only', settings)
        self.assertNotIn('evidence_directory', settings)
        client = self.client()
        sources = client.get(self.prefix + '/evidence-sources', params=self.params)
        self.assertEqual(sources.status_code, 200, sources.text)
        self.assertTrue(sources.json()['restricted'])
        self.assertEqual(sources.json()['default_path'], str(self.shared))
        self.assertEqual(sources.json()['sources'][0]['path'], str(self.shared))
        page = client.get(self.prefix + '/evidence-browser', params=self.params)
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.json()['path'], str(self.shared))
        self.assertIsNone(page.json()['parent'])
        self.assertEqual([entry['name'] for entry in page.json()['entries']], ['case-one'])
        selected = client.get(self.prefix + '/evidence-connection',
                              params={**self.params, 'path': str(self.child)})
        self.assertEqual(selected.status_code, 200, selected.text)
        self.assertFalse(Path(self.tenant['native_db']).exists())
        self.assertFalse(self.catalog.intake.exists())
        created = client.post(self.prefix + '/investigations', params=self.params,
                              json={'request_id': str(uuid.uuid4()), 'path': str(self.child)})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(self.catalog.create_conversation.call_args.kwargs['evidence_root_limit'], self.shared)

    def test_outside_paths_and_symlinks_never_reach_intake(self):
        client = self.client()
        for path in (self.outside, self.shared / 'escape', self.shared / 'internal-link'):
            for endpoint in ('evidence-browser', 'evidence-connection'):
                response = client.get(self.prefix + '/' + endpoint,
                                      params={**self.params, 'path': str(path)})
                self.assertIn(response.status_code, (400, 403), response.text)
            response = client.post(self.prefix + '/investigations', params=self.params,
                                   json={'request_id': str(uuid.uuid4()), 'path': str(path)})
            self.assertIn(response.status_code, (400, 403), response.text)
        self.catalog.create_conversation.assert_not_called()
        self.assertFalse(self.catalog.intake.exists())

    def test_forwarding_and_forged_settings_headers_cannot_replace_attestation(self):
        headers = {'X-Forwarded-For': '127.0.0.1', 'Forwarded': 'for=127.0.0.1',
                   'X-Forsic-Remote-Evidence-Settings': json.dumps({
                       'remote_evidence_directory_only': False, 'evidence_directory': str(self.outside)})}
        response = self.client().get(self.prefix + '/evidence-browser',
                                    params={**self.params, 'path': str(self.outside)}, headers=headers)
        self.assertEqual(response.status_code, 403, response.text)
        # Online proxies can connect over loopback; attestation forces remote.
        client = self.client(peer='127.0.0.1', online_hosting=True)
        response = client.get(self.prefix + '/evidence-sources', params=self.params, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['remote'])
        self.assertTrue(response.json()['restricted'])
        response = client.get(self.prefix + '/evidence-connection',
                              params={**self.params, 'path': str(self.outside)}, headers=headers)
        self.assertEqual(response.status_code, 403, response.text)

    def test_unattested_local_request_preserves_tenant_preferences(self):
        client = self.client(peer='127.0.0.1')
        sources = client.get(self.prefix + '/evidence-sources', params=self.params)
        self.assertEqual(sources.json(), {'sources': []})
        response = client.get(self.prefix + '/evidence-browser',
                              params={**self.params, 'path': str(self.outside)})
        self.assertEqual(response.status_code, 200, response.text)

    def test_missing_startup_root_fails_closed_for_fresh_tenant(self):
        client = self.client(evidence_directory='')
        sources = client.get(self.prefix + '/evidence-sources', params=self.params)
        self.assertEqual(sources.status_code, 200, sources.text)
        self.assertTrue(sources.json()['restricted'])
        self.assertFalse(sources.json()['available'])
        self.assertEqual(sources.json()['sources'], [])
        for endpoint in ('evidence-browser', 'evidence-connection'):
            response = client.get(self.prefix + '/' + endpoint,
                                  params={**self.params, 'path': str(self.child)})
            self.assertEqual(response.status_code, 403, response.text)
        response = client.post(self.prefix + '/investigations', params=self.params,
                               json={'request_id': str(uuid.uuid4()), 'path': str(self.child)})
        self.assertEqual(response.status_code, 403, response.text)
        self.catalog.create_conversation.assert_not_called()


if __name__ == '__main__':
    unittest.main()
