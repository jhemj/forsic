"""Remote source selection is confined to the explicit shared root; synthetic only."""
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from forsic_plugin.dashboard import plugin_api as api
from forsic_plugin.intake import Intake
from forsic_plugin.investigations import Investigations
from forsic_plugin.onboarding import EvidenceAccess


class RemoteEvidenceAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shared = self.root / 'shared'
        self.shared.mkdir()
        self.child = self.shared / 'case-one'
        self.child.mkdir()
        (self.child / 'sample.txt').write_text('synthetic only')
        self.outside = self.root / 'outside'
        self.outside.mkdir()
        (self.shared / 'escape').symlink_to(self.outside, target_is_directory=True)
        (self.shared / 'internal-link').symlink_to(self.child, target_is_directory=True)
        self.label = 'smb://example.invalid/archive/archive'
        self.settings = {'evidence_directory': str(self.shared), 'evidence_directory_label': self.label,
                         'remote_evidence_directory_only': True}
        self.config = patch.object(api, 'load_config', side_effect=lambda: {
            'plugins': {'entries': {'forsic': {'settings': self.settings}}}})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.catalog = Mock(home=self.root / 'private-home', intake=self.root / 'private-intake')
        self.catalog.create_conversation.return_value = {'case_id': 'synthetic', 'session_id': 'synthetic'}
        self.directory = patch.object(api, 'directory', return_value=self.catalog)
        self.directory.start()
        self.addCleanup(self.directory.stop)
        self.app = FastAPI()
        self.app.include_router(api.router, prefix='/api/plugins/forsic')
        self.prefix = '/api/plugins/forsic'

    def client(self, peer='172.30.1.50'):
        return TestClient(self.app, client=(peer, 12345))

    def test_remote_default_picker_is_shared_root_with_smb_display_and_no_parent(self):
        client = self.client()
        sources = client.get(self.prefix+'/evidence-sources').json()
        self.assertTrue(sources['restricted'])
        self.assertTrue(sources['remote'])
        self.assertTrue(sources['available'])
        self.assertEqual(sources['default_path'], str(self.shared))
        self.assertEqual(sources['display_path'], self.label)
        self.assertEqual(sources['sources'][0]['display_path'], self.label)
        page = client.get(self.prefix+'/evidence-browser').json()
        self.assertEqual(page['path'], str(self.shared))
        self.assertEqual(page['display_path'], self.label)
        self.assertIsNone(page['parent'])
        self.assertEqual([e['name'] for e in page['entries']], ['case-one'])
        selected = client.get(self.prefix+'/evidence-connection', params={'path': str(self.child)}).json()
        self.assertEqual(selected['display_path'], self.label+'/case-one')
        self.assertEqual(selected['access'], 'readable')

    def test_remote_outside_manual_paths_traversal_and_symlink_escapes_are_denied(self):
        client = self.client()
        paths = [str(self.outside), str(self.shared/'escape'), str(self.shared/'internal-link'),
                 str(self.shared/'..'/'outside'), str(self.root)]
        for path in paths:
            for endpoint in ('evidence-browser', 'evidence-connection'):
                with self.subTest(path=path, endpoint=endpoint):
                    response = client.get(self.prefix+'/'+endpoint, params={'path': path})
                    self.assertIn(response.status_code, (400, 403))
            response = client.post(self.prefix+'/investigations', json={
                'request_id': str(uuid.uuid4()), 'path': path})
            self.assertIn(response.status_code, (400, 403))
        self.catalog.create_conversation.assert_not_called()
        self.assertFalse(self.catalog.intake.exists())

    def test_remote_direct_post_validates_before_registration_and_sets_intake_limit(self):
        response = self.client().post(self.prefix+'/investigations', json={
            'request_id': str(uuid.uuid4()), 'path': str(self.child)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.catalog.create_conversation.call_args.kwargs['evidence_root_limit'], self.shared)

    def test_existing_case_conversation_cannot_select_outside_shared_scope(self):
        self.catalog.get.return_value = {'scope': str(self.outside)}
        response = self.client().post(self.prefix+'/investigations/synthetic/conversations',
                                     json={'request_id': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 403)
        self.catalog.create_conversation.assert_not_called()

    def test_forwarded_headers_cannot_make_a_remote_peer_local(self):
        for peer in ('172.30.1.50', '8.8.8.8', '', 'testclient'):
            response = self.client(peer).get(self.prefix+'/evidence-browser',
                params={'path': str(self.outside)}, headers={
                    'X-Forwarded-For': '127.0.0.1', 'Forwarded': 'for=127.0.0.1'})
            self.assertEqual(response.status_code, 403)
        for peer in ('127.0.0.1', '::1'):
            response = self.client(peer).get(self.prefix+'/evidence-browser', params={'path': str(self.outside)})
            self.assertEqual(response.status_code, 200)
            metadata = self.client(peer).get(self.prefix+'/evidence-sources').json()
            self.assertFalse(metadata['restricted'])
            self.assertFalse(metadata['remote'])

    def test_missing_unavailable_and_invalid_shared_root_fail_closed(self):
        for root in ('', str(self.shared/'absent'), str(self.child/'sample.txt')):
            self.settings['evidence_directory'] = root
            client = self.client()
            sources = client.get(self.prefix+'/evidence-sources').json()
            self.assertTrue(sources['restricted'])
            self.assertFalse(sources['available'])
            self.assertEqual(sources['sources'], [])
            self.assertEqual(client.get(self.prefix+'/evidence-browser').status_code, 403)
            self.assertIn(client.post(self.prefix+'/investigations', json={
                'request_id': str(uuid.uuid4()), 'path': str(self.child)}).status_code, (400, 403))
        self.catalog.create_conversation.assert_not_called()

    def test_default_policy_preserves_existing_local_selection_contract(self):
        self.settings.clear()
        self.assertEqual(self.client().get(self.prefix+'/evidence-sources').json(), {'sources': []})
        self.assertEqual(self.client().get(self.prefix+'/evidence-browser',
                                          params={'path': str(self.outside)}).status_code, 200)

    def test_intake_rechecks_canonical_path_after_preflight(self):
        intake = Intake(self.root/'isolated-intake')
        session = 'synthetic-race'
        selected = self.shared/'swapped'
        selected.mkdir()
        access = EvidenceAccess(self.settings, '172.30.1.50')
        allowed = access.root()
        access.require(selected)
        intake.before(session, str(selected))
        selected.rmdir()
        selected.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(PermissionError):
            intake.inspect(session, {'path': str(selected)}, allowed_root=allowed)
        self.assertFalse(intake.root.exists())
        catalog = Investigations(self.root/'unused-home', self.root/'unused-intake')
        with self.assertRaises(PermissionError):
            catalog.create_conversation(uuid.uuid4(), path=str(selected), evidence_root_limit=allowed)
        self.assertFalse(catalog.intake.exists())


if __name__ == '__main__':
    unittest.main()
