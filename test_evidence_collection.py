"""Synthetic collection contracts: actual ledger replay, immutable source bytes, no model/network."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_report_driven import fixture, worked
from forsic_plugin.evidence_collection import CollectionError, list_collection, snapshot, digest
from forsic_plugin import evidence_collection as collection
from forsic_plugin import evidence_collection_export as exports
from forsic_plugin.report_driven.host import current, ref, source_record
from forsic_plugin.notes import note


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case, self.results, self.saved_note = fixture(self.tmp.name)
        self.state = worked(self.case)
        self.context = {'case_id': self.case.config['case_id'], 'profile_id': 'profile-one',
                        'session_id': 'session-one', 'conversation_id': 'session-one',
                        'session_ids': ['session-one', 'session-compressed']}

    def page(self, **kwargs):
        return list_collection(self.case, context=self.context, **kwargs)

    def create(self, **kwargs):
        return exports.create_export(self.case, context=self.context,
            source_snapshot_id=kwargs.pop('source_snapshot_id', self.page()['source_snapshot_id']),
            selection=kwargs.pop('selection', 'all_eligible'),
            idempotency_key=kwargs.pop('idempotency_key', 'synthetic-key-one'), **kwargs)

    def bytes(self, result):
        return exports.export_download(self.case, result['export_id'], context=self.context)[0].getvalue()

    def adopted(self, literals, *, session='session-one', suffix='many', linked=True):
        # One real retained scalar with unselected neighbors; each observation
        # selects exactly its own byte range and is explicitly linked to a claim.
        text = 'NEIGHBOR_CANARY\n' + '\n'.join(literals) + '\nUNSELECTED_TAIL_CANARY'
        event_id = self.case.record('tool_result', {'tool': 'forsic_read', 'path': suffix + '.txt',
            'lines': [{'line': 1, 'text': text}], 'unselected_private_field': 'UNRELATED_FIELD_CANARY'}, session)
        source = source_record(self.case.event(event_id))
        observations = []
        position = 0
        for index, literal in enumerate(literals):
            start = text.index(literal, position)
            position = start + len(literal)
            byte_start = len(text[:start].encode())
            observations.append({'id': 'O-' + suffix + '-' + str(index), 'version': '1',
                'source_ref': ref('source', source), 'statement': literal,
                'field_pointer': '/lines/0/text', 'literal': literal,
                'byte_start': byte_start, 'byte_end': byte_start + len(literal.encode()),
                'canonical_sha256': hashlib.sha256(literal.encode()).hexdigest(),
                'presented_view': 'exact_excerpt', 'coordinate_basis': 'retained scalar UTF-8', 'interpretation_limit': 'Recorded text only.'})
        records = {'observations': observations}
        if linked:
            records['claims'] = [{'id': 'F-' + suffix, 'version': '1', 'statement': 'Chosen retained observations',
                'title': 'Chosen retained observations', 'assertion_kind': 'fact', 'status': 'adopted',
                'scope': 'Synthetic selected excerpts', 'source_refs': [ref('observation', x) for x in observations],
                'counterevidence_refs': [], 'limitations': ['No attribution claim.'],
                'adoption_receipt': None, 'semantic_review': 'not_reviewed'}]
        self.case.record('report_change', {'records': records})
        return event_id, observations

    def test_report_independent_adopted_items_only_no_write_original_or_network(self):
        self.adopted(['chosen🙂'], linked=False)
        before = self.case.db.read_bytes()
        with patch.object(self.case, 'path', side_effect=AssertionError('original access')), \
             patch('socket.socket', side_effect=AssertionError('network')):
            page = self.page()
        self.assertEqual(page['counts']['total'], 2)
        self.assertEqual(self.case.db.read_bytes(), before)
        self.assertFalse(list(self.case.output.glob('report-*')))
        self.assertTrue(all(x['linked_claims'] for x in page['items']))

    def test_exact_counts_beyond_old_limit_pagination_cursor_binding_and_identity(self):
        self.adopted(['marker-%03d' % i for i in range(135)])
        first = self.page(page_size=25)
        self.assertEqual(first['counts']['total'], 137)
        self.assertEqual(first['counts']['downloadable'], 137)
        ids = set()
        page = first
        while True:
            ids.update(x['evidence_item_id'] for x in page['items'])
            if not page['next_cursor']:
                break
            page = self.page(page_size=25, cursor=page['next_cursor'])
        self.assertEqual(len(ids), 137)
        with self.assertRaises(CollectionError):
            self.page(cursor=first['next_cursor'], search='marker')
        filtered = self.page(search='marker-007')
        self.assertEqual(filtered['counts']['filtered'], 1)
        chosen = filtered['items'][0]
        self.assertIn(chosen['evidence_item_id'], ids)
        result = self.create()
        self.assertEqual(result['item_count'], 137)  # all_eligible ignores search

    def test_case_snapshot_stable_across_conversations_and_conversation_filter(self):
        self.adopted(['one🙂'], session='session-compressed', suffix='one')
        self.adopted(['two🙂'], session='session-two', suffix='two')
        page = self.page()
        other = {**self.context, 'session_id': 'session-two', 'conversation_id': 'session-two', 'session_ids': ['session-two']}
        alternate = list_collection(self.case, context=other)
        self.assertEqual(page['source_snapshot_id'], alternate['source_snapshot_id'])
        self.assertEqual(page['items'], alternate['items'])
        scoped = self.page(scope='conversation')
        self.assertEqual([x['excerpt'] for x in scoped['items']], ['one🙂'])
        two = list_collection(self.case, context=other, scope='conversation')
        self.assertEqual([x['excerpt'] for x in two['items']], ['two🙂'])

    def test_dedup_coordinates_retains_all_claim_links_distinct_sources_not_merged(self):
        eid, observations = self.adopted(['same'], suffix='one')
        duplicate = deepcopy(observations[0]); duplicate['id'] = 'O-second-link'
        self.case.record('report_change', {'records': {'observations': [duplicate], 'claims': [{
            **current(self.case)['claims'][-1], 'id': 'F-second-link', 'source_refs': [ref('observation', duplicate)]}]}})
        self.adopted(['same'], suffix='other-source')
        items = [x for x in self.page()['items'] if x['excerpt'] == 'same']
        self.assertEqual(len(items), 2)
        merged = next(x for x in items if x['source_ref']['id'] == eid)
        self.assertEqual(len(merged['observation_refs']), 2)
        self.assertEqual(len(merged['linked_claims']), 2)

    def test_unselected_large_raw_results_never_decoded_or_hashed(self):
        raw = 'UNSELECTED_LARGE_CANARY' * 100000
        self.case.record('tool_result', {'tool': 'forsic_shell', 'stdout': raw}, 'session-one')
        original_loads = collection.json.loads
        def guarded(value, *args, **kwargs):
            if isinstance(value, str) and 'UNSELECTED_LARGE_CANARY' in value:
                self.fail('Unselected raw result entered Python JSON decoding')
            return original_loads(value, *args, **kwargs)
        with patch.object(collection.json, 'loads', side_effect=guarded):
            self.assertEqual(self.page()['counts']['total'], 2)

    def test_capacity_unknown_fails_instead_of_truncated_zero_counts(self):
        self.adopted(['small'], suffix='one')
        with patch.object(collection, 'MAX_SOURCE_BYTES', 10):
            collection._CACHE.clear()
            with self.assertRaises(CollectionError) as raised:
                self.page()
        self.assertEqual(raised.exception.status_code, 413)

    def test_source_tamper_invalidates_cache_and_fails_exact_excerpt(self):
        eid, _ = self.adopted(['selected-marker'], suffix='tamper')
        self.assertEqual(self.page()['counts']['downloadable'], 3)
        event = self.case.event(eid)
        event['data']['lines'][0]['text'] = 'changed retained scalar'
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(event['data']), eid))
        page = self.page()
        self.assertGreater(page['counts']['errors'], 0)
        self.assertTrue(any(not x['downloadable'] for x in page['items']))

    def test_missing_source_is_unavailable_evidence_never_recollected_or_zero(self):
        eid, _ = self.adopted(['preserved-missing'], suffix='missing')
        with self.case.connect() as db:
            db.execute('DELETE FROM events WHERE id=?', (eid,))
        with patch.object(self.case, 'path', side_effect=AssertionError('must not recollect')):
            page = self.page()
        missing = next(x for x in page['items'] if x['source_ref']['id'] == eid)
        self.assertFalse(missing['downloadable'])
        self.assertEqual(missing['verification_status'], 'unavailable')
        self.assertEqual(page['counts']['total'], 3)
        self.assertGreater(page['counts']['errors'], 0)

    def test_verifier_rejects_case_and_source_version_rebinding_without_extraction(self):
        raw = self.bytes(self.create())
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            saved = {name: archive.read(name) for name in archive.namelist()}
        for field in ('case_id', 'source_index'):
            members = dict(saved)
            manifest = json.loads(members['manifest.json'])
            if field == 'case_id':
                manifest['case_id'] = 'another-case'
            else:
                manifest['source_index'] = {key: '0' * 64 for key in manifest['source_index']}
            members['manifest.json'] = json.dumps(manifest).encode()
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                for name, body in members.items():
                    archive.writestr(name, body)
            with patch('zipfile.ZipFile.extractall', side_effect=AssertionError('no extraction')), \
                 patch('socket.socket', side_effect=AssertionError('no network')):
                with self.assertRaises(ValueError):
                    exports.verify_collection(io.BytesIO(buffer.getvalue()))

    def test_selected_exact_unicode_excerpt_no_neighbors_native_arguments_or_full_scalar(self):
        _, _ = self.adopted(['선택🙂'], suffix='unicode')
        chosen = self.page(search='선택🙂')['items'][0]
        result = self.create(selection='selected', items=[{'evidence_item_id': chosen['evidence_item_id'], 'item_version': chosen['item_version']}])
        raw = self.bytes(result)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            payload = b''.join(archive.read(name) for name in archive.namelist())
            for secret in (b'NEIGHBOR_CANARY', b'UNSELECTED_TAIL_CANARY', b'UNRELATED_FIELD_CANARY'):
                self.assertNotIn(secret, payload)
            self.assertEqual(archive.read('excerpts/' + chosen['evidence_item_id'] + '.txt'), '선택🙂'.encode())
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['full_retained_scalar_verification'], 'not_available')
            self.assertEqual(manifest['original_source_verification'], 'not_available')
            script = Path(self.tmp.name) / 'verify.py'; script.write_bytes(archive.read('verify.py'))
        path = Path(self.tmp.name) / 'download.zip'; path.write_bytes(raw)
        verified = subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)['verified_items'], 1)

    def test_selected_versions_and_snapshot_rejected_after_correction_stale_distinct(self):
        page = self.page()
        selected = page['items'][0]
        latest = note(self.case, {'action': 'get', 'note_id': self.saved_note['note_id']})['note']
        note(self.case, {'action': 'save', 'note_id': latest['note_id'], 'revision': latest['revision'],
            'question': 'Corrected proposition', 'answer': 'Unassessed', 'evidence_ids': [], 'correction_reason': 'Synthetic correction'})
        with self.assertRaises(CollectionError) as raised:
            self.create(source_snapshot_id=page['source_snapshot_id'], selection='selected',
                        items=[{k: selected[k] for k in ('evidence_item_id', 'item_version')}])
        self.assertEqual(raised.exception.status_code, 409)
        current_page = self.page()
        stale = self.page(status='stale')
        self.assertEqual(current_page['counts']['stale'], 2)
        self.assertEqual(len(stale['items']), 2)
        self.assertTrue(all(not x['downloadable'] and not x['linked_claims'] for x in stale['items']))
        self.assertFalse(current_page['items'])

    def test_judgment_correction_during_build_preserves_fixed_selection(self):
        original = exports._build
        def during(*args):
            raw, manifest = original(*args)
            self.case.record('note', {**self.saved_note, 'revision': self.saved_note['revision'] + 1,
                                      'answer': 'Revised synthetic answer'})
            return raw, manifest
        with patch.object(exports, '_build', side_effect=during):
            result = self.create()
        self.assertEqual(result['status'], 'ready')
        self.assertTrue(result['stale'])
        self.assertEqual(exports.verify_collection(io.BytesIO(self.bytes(result)))['verified_items'], 2)

    def test_idempotent_same_input_reuses_after_revision_different_input_conflicts(self):
        result = self.create()
        self.case.record('indicator', {'indicator_id': 'synthetic', 'type': 'domain', 'canonical_value': 'example.test'})
        with patch.object(exports, '_build', side_effect=AssertionError('must not rebuild')):
            again = self.create(source_snapshot_id=result['source_snapshot_id'])
        self.assertEqual(again['export_id'], result['export_id'])
        self.assertEqual(again['sha256'], result['sha256'])
        with self.assertRaises(CollectionError) as raised:
            self.create(source_snapshot_id='0' * 64)
        self.assertEqual(raised.exception.status_code, 409)

    def test_failed_or_unknown_reservation_not_reexecuted(self):
        with patch.object(exports, '_build', side_effect=RuntimeError('interrupted synthetic build')):
            with self.assertRaises(RuntimeError):
                self.create()
        with patch.object(exports, '_build', side_effect=AssertionError('must not rebuild')):
            failed = self.create()
        self.assertEqual(failed['status'], 'failed')
        with self.assertRaises(CollectionError):
            exports.export_download(self.case, failed['export_id'], context=self.context)
        folder = self.case.output / '.evidence-collection' / failed['export_id']
        (folder / 'failed.json').unlink()
        with patch.object(exports, '_build', side_effect=AssertionError('must not rebuild')):
            unknown = self.create()
        self.assertEqual(unknown['status'], 'building')

    def test_auth_context_case_profile_session_download_boundary_and_no_paths(self):
        result = self.create()
        for context in ({**self.context, 'case_id': 'other-case'}, {**self.context, 'profile_id': 'other-profile'},
                        {**self.context, 'session_id': 'unbound-session'}):
            with self.subTest(context=context):
                with self.assertRaises(CollectionError):
                    exports.export_status(self.case, result['export_id'], context=context)
                with self.assertRaises(CollectionError):
                    exports.export_download(self.case, result['export_id'], context=context)
        self.assertNotIn(str(self.case.output), json.dumps(result))
        with self.assertRaises(CollectionError):
            list_collection(self.case, context=None)

    def test_symlinks_traversal_duplicate_members_tamper_failclosed(self):
        result = self.create()
        folder = self.case.output / '.evidence-collection' / result['export_id']
        original = (folder / 'evidence.zip').read_bytes()
        (folder / 'evidence.zip').unlink()
        outside = Path(self.tmp.name) / 'outside.zip'; outside.write_bytes(original)
        (folder / 'evidence.zip').symlink_to(outside)
        with self.assertRaises(CollectionError):
            exports.export_download(self.case, result['export_id'], context=self.context)
        for name in ('../escape', '/absolute', 'safe/../escape', 'safe\\escape'):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr(name, 'bad')
            with self.assertRaises(ValueError):
                exports.verify_collection(io.BytesIO(stream.getvalue()))
        stream = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as old, zipfile.ZipFile(stream, 'w') as archive:
            for info in old.infolist():
                archive.writestr(info, old.read(info.filename))
            archive.writestr('manifest.json', old.read('manifest.json'))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            exports.verify_collection(io.BytesIO(stream.getvalue()))
        with self.assertRaises(CollectionError):
            exports.export_status(self.case, '../escape', context=self.context)

    def test_artifact_bytes_bound_time_limit_csv_formula_and_not_auto_report(self):
        self.adopted(['=1+2\t🙂'], suffix='csv')
        result = self.create()
        with zipfile.ZipFile(io.BytesIO(self.bytes(result))) as archive:
            csv = archive.read('evidence-index.csv').decode()
            self.assertIn("'=1+2 🙂", csv)
            self.assertIn('=1+2\t🙂', archive.read('excerpts/' + next(x['evidence_item_id'] for x in self.page()['items'] if x['excerpt'].startswith('=')) + '.txt').decode())
        with patch.object(exports, 'MAX_EXPORT_ITEMS', 1):
            with self.assertRaises(CollectionError) as raised:
                self.create(idempotency_key='synthetic-bound')
            self.assertEqual(raised.exception.status_code, 413)
        with patch.object(exports, 'MAX_EXPORT_BYTES', 50):
            with self.assertRaises(CollectionError):
                self.create(idempotency_key='synthetic-bytes')
        with patch.object(exports, 'MAX_SECONDS', -1):
            with self.assertRaises(CollectionError):
                self.create(idempotency_key='synthetic-time')
        self.assertFalse(list(self.case.output.glob('report-*.json')))
        self.assertTrue((self.case.output / '.evidence-collection').is_dir())


if __name__ == '__main__':
    unittest.main()
