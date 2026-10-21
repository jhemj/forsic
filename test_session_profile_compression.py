"""Offline product/native contracts for profile policy across compression."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from forsic_plugin.investigations import Investigations
from hermes_state import SessionDB


class SessionProfileCompressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'hermes'
        self.home.mkdir()
        self.env = patch.dict(os.environ, {
            'HERMES_HOME': str(self.home), 'HERMES_RUNTIME_DIR': str(self.home / 'runtime')})
        self.env.start()
        self.addCleanup(self.env.stop)

    def database(self):
        db = SessionDB(self.home / 'state.db')
        self.addCleanup(db.close)
        return db

    def compress(self, db, parent, child, config=None):
        db.publish_compression_child(
            parent_session_id=parent, child_session_id=child, source='cli',
            model='synthetic-child-model', model_config=config,
            messages=[{'role': 'user', 'content': 'Synthetic handoff only.'}],
            require_compression_lease=False)
        return json.loads(db.get_session(child)['model_config'] or '{}')

    def test_product_created_conversation_keeps_profile_policy_across_compression(self):
        evidence = Path(self.tmp.name) / 'evidence' / 'synthetic.txt'
        evidence.parent.mkdir()
        evidence.write_text('Synthetic evidence fixture only.\n')
        catalog = Investigations(self.home, self.home / 'intake')
        result = catalog.create_conversation(uuid.uuid4(), path=str(evidence))
        db = self.database()
        parent = db.get_session(result['session_id'])
        self.assertTrue(json.loads(parent['model_config'])['follow_profile_config'])
        child = self.compress(db, parent['id'], 'child', {'max_iterations': 10})
        self.assertTrue(child['follow_profile_config'])
        self.assertTrue(self.compress(db, 'child', 'grandchild')['follow_profile_config'])

    def test_compression_preserves_only_profile_provenance_and_uses_child_runtime(self):
        db = self.database()
        provenance = {'model': 'synthetic-profile-model', 'provider': 'synthetic-provider'}
        db.create_session('parent', source='cli', model='synthetic-old-model', model_config={
            'follow_profile_config': True, 'composer_override_profile': provenance,
            'room_plumbing': True, 'provider': 'synthetic-old-provider',
            'reasoning_config': {'effort': 'low'}, 'unrelated': 'parent-only'})
        child_config = {'provider': 'synthetic-new-provider', 'max_iterations': 12}
        child = self.compress(db, 'parent', 'child', child_config)
        self.assertEqual(child, {**child_config, 'follow_profile_config': True,
                                'composer_override_profile': provenance, 'room_plumbing': True})
        self.assertEqual(db.get_session('child')['model'], 'synthetic-child-model')
        self.assertEqual(child_config, {'provider': 'synthetic-new-provider', 'max_iterations': 12})

    def test_explicit_child_policy_takes_precedence(self):
        db = self.database()
        db.create_session('parent', source='cli', model_config={
            'follow_profile_config': True, 'composer_override_profile': {'model': 'old'},
            'room_plumbing': True})
        requested = {'follow_profile_config': False, 'composer_override_profile': None,
                     'room_plumbing': False}
        self.assertEqual(self.compress(db, 'parent', 'child', requested), requested)

    def test_malformed_or_unmarked_parent_does_not_invent_profile_policy(self):
        db = self.database()
        for index, raw in enumerate(('not-json', '[]', 'null', '42', '{"provider":"old"}')):
            with self.subTest(raw=raw):
                parent, child = f'parent-{index}', f'child-{index}'
                db.create_session(parent, source='cli')
                db.update_session_meta(parent, raw)
                self.assertEqual(self.compress(db, parent, child, {'max_iterations': 8}),
                                 {'max_iterations': 8})


if __name__ == '__main__':
    unittest.main()
