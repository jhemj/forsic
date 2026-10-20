"""Authenticated plugin adapter contracts with synthetic adopted evidence only."""
import io
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from forsic_plugin.dashboard import plugin_api as api
from forsic_plugin.evidence_collection_export import verify_collection
from test_report_driven import fixture, worked


class CollectionAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.case, _, _ = fixture(temporary.name)
        worked(self.case)
        self.resolved = dict(case=self.case, case_id=self.case.config['case_id'],
                             requested_session_id='native-test', session_id='native-test',
                             conversation_id='native-test', session_lineage=['native-test'],
                             goal_id='goal-test', goal=None)
        def resolve(sid, profile):
            if sid != 'native-test' or profile != 'default':
                raise api.HTTPException(404, 'Not owned')
            return self.resolved
        owner = patch.object(api, 'investigation_scope', side_effect=resolve)
        owner.start(); self.addCleanup(owner.stop)
        app = FastAPI(); app.state.initial_profile = 'default'; app.include_router(api.router)
        self.client = TestClient(app, base_url='http://localhost', client=('127.0.0.1', 50000))
        self.query = dict(case_id=self.case.config['case_id'], session_id='native-test', profile='default')

    def listing(self):
        response = self.client.get('/evidence-collection', params=self.query)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def payload(self):
        current = self.listing()
        first = next(item for item in current['items'] if item['downloadable'])
        return dict(**self.query, source_snapshot_id=current['source_snapshot_id'],
                    selection='selected', items=[{key: first[key] for key in ('evidence_item_id', 'item_version')}],
                    idempotency_key='synthetic-operation-1')

    def test_list_selected_export_download_same_bytes_without_ledger_mutation(self):
        before = self.case.db.read_bytes()
        payload = self.payload()
        created = self.client.post('/evidence-collection/exports', json=payload)
        self.assertEqual(created.status_code, 200, created.text)
        record = created.json(); self.assertEqual(record['status'], 'ready')
        self.assertEqual(record['item_count'], 1)
        url = '/evidence-collection/exports/' + record['export_id']
        self.assertEqual(self.client.get(url, params=self.query).json()['export_id'], record['export_id'])
        download = self.client.get(url + '/download', params=self.query)
        self.assertEqual(download.status_code, 200, download.text[:200] if not download.is_success else '')
        self.assertEqual(download.headers['content-type'], 'application/zip')
        self.assertEqual(download.headers['cache-control'], 'no-store')
        self.assertEqual(download.headers['x-content-type-options'], 'nosniff')
        self.assertIn("filename*=UTF-8''", download.headers['content-disposition'])
        self.assertEqual(verify_collection(io.BytesIO(download.content))['verified_items'], 1)
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertFalse(manifest['contains_sensitive_originals'])
        again = self.client.post('/evidence-collection/exports', json=payload)
        self.assertEqual(again.json()['export_id'], record['export_id'])
        self.assertEqual(self.case.db.read_bytes(), before)

    def test_scope_required_at_every_stage(self):
        payload = self.payload()
        record = self.client.post('/evidence-collection/exports', json=payload).json()
        base = '/evidence-collection/exports/' + record['export_id']
        for change in ({'case_id': 'foreign'}, {'session_id': 'foreign'}, {'profile': 'foreign'}):
            query = {**self.query, **change}
            for route in ('/evidence-collection', base, base + '/download'):
                self.assertIn(self.client.get(route, params=query).status_code, (404, 409))
            self.assertIn(self.client.post('/evidence-collection/exports', json={**payload, **change}).status_code, (404, 409))
        self.assertEqual(self.client.get('/evidence-collection').status_code, 422)
        self.assertEqual(self.client.get('/evidence-collection', params={**self.query, 'page_size': 10000}).status_code, 422)

    def test_stale_selection_and_extra_path_are_rejected(self):
        payload = self.payload()
        payload['items'][0]['item_version'] = 'stale'
        self.assertEqual(self.client.post('/evidence-collection/exports', json=payload).status_code, 409)
        payload['output_path'] = '/tmp/no-user-path'
        self.assertEqual(self.client.post('/evidence-collection/exports', json=payload).status_code, 422)

    def test_local_sensitive_capability_and_origin_are_not_inferred_from_header(self):
        self.assertTrue(self.listing()['permissions']['sensitive_originals'])
        remote = TestClient(self.client.app, base_url='http://localhost', client=('192.168.1.20', 50000))
        result = remote.get('/evidence-collection', params=self.query,
                            headers={'X-Forwarded-For': '127.0.0.1'})
        self.assertFalse(result.json()['permissions']['sensitive_originals'])
        payload = self.payload(); payload['include_sensitive_originals'] = True
        self.assertEqual(remote.post('/evidence-collection/exports', json=payload).status_code, 403)
        self.assertEqual(self.client.post('/evidence-collection/exports', json=self.payload(),
                         headers={'Origin': 'http://foreign.example'}).status_code, 403)

    def test_locale_is_display_only_and_review_projection_is_owned(self):
        response = self.client.get('/skills-ui-copy')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['skills']), 24)
        with patch('forsic_plugin.review_chat.review_chat', return_value={'items': [], 'status': 'ready'}) as project:
            result = self.client.get('/review-chat', params=self.query)
            self.assertEqual(result.json()['items'], [])
            project.assert_called_once()
            project.reset_mock()
            self.assertEqual(self.client.get('/review-chat', params={**self.query, 'case_id': 'foreign'}).status_code, 409)
            project.assert_not_called()
