"""Offline remote product routes with native HTTP auth and synthetic case data."""
import io
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from forsic_plugin.dashboard import plugin_api as api
from forsic_plugin.evidence_collection_export import verify_collection
from forsic_plugin.remote_policy import ForsicRemoteAccessMiddleware, ForsicRemotePolicy
from hermes_cli.dashboard_auth.middleware import gated_auth_middleware
from test_report_driven import fixture, worked


PREFIX = '/api/plugins/forsic'


class RemoteCollectionRoutesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.case, _, _ = fixture(temporary.name)
        worked(self.case)
        resolved = dict(case=self.case, case_id=self.case.config['case_id'],
                        requested_session_id='native-test', session_id='native-test',
                        conversation_id='native-test', session_lineage=['native-test'],
                        goal_id='goal-test', goal=None)

        def resolve(session_id, profile):
            if session_id != 'native-test' or profile != 'default':
                raise api.HTTPException(404, 'Not owned')
            return resolved

        for target, kwargs in (
            ('forsic_plugin.dashboard.plugin_api.investigation_scope', {'side_effect': resolve}),
            ('hermes_cli.dashboard_auth.middleware._verify_access_token',
             {'side_effect': lambda request, *, access_token, **kw:
              SimpleNamespace() if access_token == 'synthetic-auth' else None}),
            ('hermes_cli.dashboard_auth.middleware.audit_log', {}),
        ):
            patched = patch(target, **kwargs)
            patched.start()
            self.addCleanup(patched.stop)
        app = FastAPI()
        app.state.initial_profile = 'default'
        app.state.auth_required = True
        app.include_router(api.router, prefix=PREFIX)
        app.middleware('http')(gated_auth_middleware)
        self.policy = ForsicRemotePolicy({'remote_evidence_directory_only': True}, temporary.name)
        app.add_middleware(ForsicRemoteAccessMiddleware, policy=self.policy)
        self.remote = TestClient(app, base_url='http://localhost', client=('192.0.2.20', 50000),
                                 headers={'Authorization': 'Bearer synthetic-auth'})
        self.local = TestClient(app, base_url='http://localhost', client=('127.0.0.1', 50000),
                                headers={'Authorization': 'Bearer synthetic-auth'})
        self.query = dict(case_id=self.case.config['case_id'], session_id='native-test', profile='default')

    def payload(self):
        listing = self.remote.get(PREFIX + '/evidence-collection', params=self.query)
        self.assertEqual(listing.status_code, 200, listing.text)
        current = listing.json()
        self.assertFalse(current['permissions']['sensitive_originals'])
        first = next(item for item in current['items'] if item['downloadable'])
        return dict(**self.query, source_snapshot_id=current['source_snapshot_id'],
                    selection='selected', items=[{key: first[key] for key in ('evidence_item_id', 'item_version')}],
                    idempotency_key='synthetic-remote-export')

    def test_exact_product_routes_reach_owned_handlers_and_download_verified_bytes(self):
        self.assertEqual(self.remote.get(PREFIX + '/skills-ui-copy').status_code, 200)
        with patch('forsic_plugin.review_chat.review_chat', return_value={'items': [], 'status': 'ready'}) as project:
            response = self.remote.get(PREFIX + '/review-chat', params=self.query)
            self.assertEqual(response.status_code, 200, response.text)
            project.assert_called_once()
        created = self.remote.post(PREFIX + '/evidence-collection/exports', json=self.payload())
        self.assertEqual(created.status_code, 200, created.text)
        base = PREFIX + '/evidence-collection/exports/' + created.json()['export_id']
        self.assertEqual(self.remote.get(base, params=self.query).status_code, 200)
        download = self.remote.get(base + '/download', params=self.query)
        self.assertEqual(download.status_code, 200, download.text if not download.is_success else '')
        self.assertEqual(verify_collection(io.BytesIO(download.content))['verified_items'], 1)

    def test_wrong_scope_and_sensitive_export_remain_forbidden(self):
        payload = self.payload()
        created = self.remote.post(PREFIX + '/evidence-collection/exports', json=payload)
        self.assertEqual(created.status_code, 200, created.text)
        base = PREFIX + '/evidence-collection/exports/' + created.json()['export_id']
        for change in ({'profile': 'other'}, {'session_id': 'foreign'}, {'case_id': 'foreign'}):
            for route in ('/review-chat', '/evidence-collection', base.removeprefix(PREFIX), base.removeprefix(PREFIX) + '/download'):
                with self.subTest(route=route, change=change):
                    self.assertIn(self.remote.get(PREFIX + route, params={**self.query, **change}).status_code, (403, 404, 409))
            self.assertIn(self.remote.post(PREFIX + '/evidence-collection/exports', json={**payload, **change}).status_code, (403, 404, 409))
        sensitive = {**payload, 'include_sensitive_originals': True, 'idempotency_key': 'synthetic-sensitive-export'}
        self.assertEqual(self.remote.post(PREFIX + '/evidence-collection/exports', json=sensitive,
                         headers={'X-Forwarded-For': '127.0.0.1'}).status_code, 403)
        local = self.local.post(PREFIX + '/evidence-collection/exports', json=sensitive)
        self.assertEqual(local.status_code, 200, local.text)
        base = PREFIX + '/evidence-collection/exports/' + local.json()['export_id']
        for route in (base, base + '/download'):
            self.assertEqual(self.remote.get(route, params=self.query).status_code, 403)

    def test_all_new_routes_require_native_http_authentication(self):
        routes = [('GET', '/skills-ui-copy'), ('GET', '/review-chat'), ('GET', '/evidence-collection'),
                  ('POST', '/investigations/synthetic-case/close'),
                  ('POST', '/evidence-collection/exports'),
                  ('GET', '/evidence-collection/exports/' + 'a' * 40),
                  ('GET', '/evidence-collection/exports/' + 'a' * 40 + '/download')]
        unauthenticated = TestClient(self.remote.app, base_url='http://localhost', client=('192.0.2.20', 50000))
        for method, route in routes:
            for headers in ({}, {'Authorization': 'Bearer invalid-synthetic-auth'}):
                with self.subTest(method=method, route=route, headers=headers):
                    self.assertEqual(unauthenticated.request(method, PREFIX + route, headers=headers).status_code, 401)

    def test_unknown_admin_methods_and_profile_overrides_stay_closed(self):
        denied = [('GET', '/evidence-collection/exports'), ('GET', '/evidence-collection/admin'),
                  ('GET', '/evidence-collection/exports/id/admin'), ('GET', '/review-chat/admin'),
                  ('GET', '/skills-ui-copy/extra'), ('GET', '/connections'),
                  ('POST', '/review-chat'), ('POST', '/skills-ui-copy'),
                  ('POST', '/evidence-collection'), ('POST', '/evidence-collection/exports/id'),
                  ('PUT', '/evidence-collection/exports'), ('DELETE', '/evidence-collection/exports/id'),
                  ('HEAD', '/evidence-collection'), ('OPTIONS', '/evidence-collection/exports')]
        for method, route in denied:
            with self.subTest(method=method, route=route):
                self.assertEqual(self.remote.request(method, PREFIX + route).status_code, 403)
        for route in ('/skills-ui-copy', '/review-chat', '/evidence-collection', '/evidence-collection/exports/id/download'):
            self.assertEqual(self.remote.get(PREFIX + route, params='profile=default&profile=other').status_code, 403)


if __name__ == '__main__':
    unittest.main()
