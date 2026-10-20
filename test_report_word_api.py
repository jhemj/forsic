"""Explicit saved-version Word downloads through the existing case-bound API."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hermes_state import SessionDB

from forsic_plugin.dashboard import plugin_api
from forsic_plugin.investigation_view import _reports
from forsic_plugin.notes import note
from forsic_plugin.report_driven import render_views, word_export
from forsic_plugin.report_driven.views import bundle
from test_report_driven import fixture, worked


class ReportWordAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.intake = self.root / 'intake'
        self.case, _, self.note = fixture(self.intake / 'cases' / 'synthetic')
        self.state = worked(self.case)
        self.rendered = bundle(self.case, self.state)
        self.bundle_root = Path(self.rendered['manifest']).parent
        self.case_id = self.case.config['case_id']
        db = SessionDB(self.home / 'state.db')
        try:
            db.create_session('synthetic-reader', 'web')
        finally:
            db.close()
        binding = self.intake / (hashlib.sha256(b'synthetic-reader').hexdigest() + '.json')
        binding.write_text(json.dumps({'manifest': str(self.case.manifest), 'stage': 'ready'}))
        self.env = patch.dict(os.environ, {'HERMES_HOME': str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = patch.object(plugin_api, 'load_config', return_value={
            'plugins': {'entries': {'forsic': {'settings': {'intake_root': str(self.intake)}}}}})
        self.config.start()
        self.addCleanup(self.config.stop)
        app = FastAPI()
        app.include_router(plugin_api.router, prefix='/api/plugins/forsic')
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.base = '/api/plugins/forsic/report-bundles/' + self.rendered['bundle_id']
        self.query = {'case_id': self.case_id}

    def listed(self):
        result, counts = {'reports': []}, {}
        _reports(self.case, self.case_id, result, counts)
        return result['reports']

    def word_text(self, response):
        from xml.etree import ElementTree
        with ZipFile(io.BytesIO(response.content)) as document:
            return ''.join(ElementTree.fromstring(document.read('word/document.xml')).itertext())

    def test_passive_reads_list_an_explicit_action_without_creating_word(self):
        before = self.case.db.read_bytes()
        with patch.object(render_views, 'render_docx', side_effect=AssertionError('No passive conversion')):
            reports = self.listed()
            self.assertEqual(reports[0]['word_export'], {
                'name': 'integrated.docx', 'method': 'POST',
                'url': self.base + '/word?case_id=' + self.case_id})
            response = self.client.get('/api/plugins/forsic/report-driven', params=self.query)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.client.get(self.base + '/integrated.html', params=self.query).status_code, 200)
            self.assertEqual(self.client.get(self.base + '/integrated.docx', params=self.query).status_code, 404)
            self.assertEqual(self.client.get(self.base + '/word', params=self.query).status_code, 404)
            for name in word_export.INPUT_NAMES.values():
                self.assertEqual(self.client.get(self.base + '/' + name, params=self.query).status_code, 404)
        self.assertFalse((self.case.output / '.word-exports').exists())
        self.assertEqual(self.case.db.read_bytes(), before)

    def test_requested_download_is_binary_and_reuses_the_same_saved_version(self):
        before = {p: p.read_bytes() for p in self.bundle_root.iterdir() if p.is_file()}
        db_before = self.case.db.read_bytes()
        with patch.object(render_views, 'render_docx', wraps=render_views.render_docx) as convert:
            response = self.client.post(self.base + '/word', params=self.query)
            again = self.client.post(self.base + '/word', params=self.query)
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertEqual(again.status_code, 200)
        self.assertEqual(convert.call_count, 1)
        self.assertEqual(response.content, again.content)
        self.assertIn('wordprocessingml.document', response.headers['content-type'])
        self.assertEqual(response.headers['x-forsic-report-version'], self.rendered['bundle_id'])
        self.assertEqual(int(response.headers['content-length']), len(response.content))
        self.assertIn('filename="integrated.docx"', response.headers['content-disposition'])
        self.assertIn(self.state['questions'][0]['answer'], self.word_text(response))
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(self.case.db.read_bytes(), db_before)

    def test_changed_question_does_not_change_old_download_or_allow_foreign_case(self):
        saved = note(self.case, {'action': 'get', 'note_id': self.note['note_id']})['note']
        note(self.case, {'action': 'save', 'note_id': saved['note_id'], 'revision': saved['revision'],
            'question': saved['question'], 'answer': 'A later judgment outside this saved report.',
            'evidence_ids': saved['evidence_ids']})
        with (patch('forsic_plugin.report_driven.host.current', side_effect=AssertionError('No live judgment')),
              patch('forsic_plugin.report_driven.views.compile_views', side_effect=AssertionError('No new projection'))):
            response = self.client.post(self.base + '/word', params=self.query)
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertIn(self.state['questions'][0]['answer'], self.word_text(response))
        self.assertNotIn('A later judgment outside', self.word_text(response))
        with patch.object(word_export, 'export_word', side_effect=AssertionError('Foreign case must stop first')):
            foreign = self.client.post(self.base + '/word', params={'case_id': 'unknown-case'})
        self.assertEqual(foreign.status_code, 404)

    def test_tampered_input_fails_without_file_and_retry_uses_restored_input(self):
        path = self.bundle_root / word_export.INPUT_NAMES['view']
        original = path.read_bytes()
        path.write_bytes(b'{}')
        response = self.client.post(self.base + '/word', params=self.query)
        self.assertEqual(response.status_code, 409)
        self.assertIn('Word', response.json()['detail'])
        self.assertNotIn(str(self.root), response.text)
        self.assertFalse((self.case.output / '.word-exports').exists())
        path.write_bytes(original)
        self.assertEqual(self.client.post(self.base + '/word', params=self.query).status_code, 200)

    def test_stream_verifies_final_file_and_invalid_ids_fail_closed(self):
        exported = word_export.export_word(self.case, self.rendered['bundle_id'])
        exported['path'].write_bytes(b'changed-after-conversion')
        with patch.object(word_export, 'export_word', return_value=exported):
            response = self.client.post(self.base + '/word', params=self.query)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('changed-after-conversion', response.text)
        for ident in ('invalid', 'a' * 64):
            response = self.client.post('/api/plugins/forsic/report-bundles/' + ident + '/word', params=self.query)
            self.assertEqual(response.status_code, 409)

    def test_unverified_or_malformed_descriptor_never_offers_conversion(self):
        path = self.bundle_root / 'manifest.json'
        manifest = json.loads(path.read_text())
        for descriptor in ('invalid-shape', {}, {'schema': word_export.SCHEMA, 'status': 'available'}):
            manifest['lazy_word'] = descriptor
            path.write_text(json.dumps(manifest))
            self.assertNotIn('word_export', self.listed()[0])


if __name__ == '__main__':
    unittest.main()
