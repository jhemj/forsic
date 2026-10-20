"""Offline selected evidence export integrity/privacy contracts; no model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_report_driven import fixture, worked
from forsic_plugin.report_driven.evidence_export import export_evidence
from forsic_plugin.report_driven.host import current, ref, source_record
from forsic_plugin.report_driven.views import selected_report_state
from forsic_plugin.notes import note


class EvidenceExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.case, self.results, self.saved_note = fixture(self.tmp.name)
        self.state = worked(self.case)

    def tearDown(self):
        self.tmp.cleanup()

    def output(self, name='one'):
        parent = self.case.output / ('.report-stage-' + name)
        parent.mkdir(exist_ok=True)
        return parent / 'selected-evidence.zip'

    def read(self, path, name):
        with zipfile.ZipFile(path) as archive:
            return json.loads(archive.read(name))

    def verify(self, path):
        with zipfile.ZipFile(path) as archive:
            verifier = archive.read('verify.py')
        script = Path(self.tmp.name) / 'verify_evidence.py'
        script.write_bytes(verifier)
        return subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)

    def unicode_state(self):
        # A real retained result containing a multi-byte prefix and excerpt.
        started = self.case.record('tool_start', {'tool': 'forsic_read',
            'arguments': {'secret_environment': 'NEVER_EXPORT_NATIVE_CREDENTIAL'},
            'native_call': {'session_id': 'session-test', 'turn_id': 'turn-test',
                            'tool_call_id': 'call-test', 'api_request_id': 'request-test',
                            'authorization': 'NEVER_EXPORT_NATIVE_CREDENTIAL'}}, 'session-test')
        data = {'tool': 'forsic_read', 'started_id': started, 'path': 'unicode.log',
                'lines': [{'line': 9, 'text': '앞🙂 끝증거🙂 tail'}],
                'unselected_private_field': 'NEVER_EXPORT_UNSELECTED_FIELD',
                'byte_start': 4000, 'byte_end': 5000, 'complete_file': False}
        eid = self.case.record('tool_result', data, 'session-test')
        source = source_record(self.case.event(eid))
        literal = '끝증거🙂'
        start = len('앞🙂 '.encode('utf-8'))
        observation = {'id': 'O-unicode', 'version': source['version'], 'source_ref': ref('source', source),
                       'field_pointer': '/lines/0/text', 'literal': literal,
                       'byte_start': start, 'byte_end': start + len(literal.encode('utf-8')),
                       'canonical_sha256': hashlib.sha256(literal.encode('utf-8')).hexdigest(),
                       'presented_view': 'exact_excerpt', 'interpretation_limit': 'Recorded text only.'}
        state = deepcopy(self.state)
        state['sources'] = [source]
        state['observations'] = [observation]
        state['questions'] = [{'id': 'Q-unicode', 'version': '1', 'revision': 3,
                               'question': 'What is recorded?', 'answer': 'A retained excerpt.',
                               'claim_refs': [{'kind': 'claim', 'id': 'F-unicode', 'version': '1'}]}]
        state['claims'] = [{'id': 'F-unicode', 'version': '1', 'statement': 'Recorded text.',
                            'source_refs': [ref('observation', observation)],
                            'counterevidence_refs': [], 'limitations': ['No execution attribution.']}]
        for field in ('hypotheses', 'tests', 'assessments', 'missions', 'gaps', 'timeline', 'requirements', 'actions', 'indicators'):
            state[field] = []
        return state, eid

    def test_real_retained_sources_verify_without_original_or_network_access(self):
        path = self.output()
        before = self.case.db.read_bytes()
        original = deepcopy(self.state)
        with patch.object(self.case, 'path', side_effect=AssertionError('Original access forbidden')), \
                patch('socket.socket', side_effect=AssertionError('Network forbidden')):
            result = export_evidence(self.case, self.state, path)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['snapshot_id'], self.state['meta']['snapshot_id'])
        self.assertEqual(result['card_count'], len(self.state['observations']))
        self.assertEqual(self.case.db.read_bytes(), before)
        self.assertEqual(self.state, original)
        verified = self.verify(path)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)['verified_cards'], len(self.state['observations']))

    def test_unicode_scalar_coordinates_are_not_original_image_offsets(self):
        state, eid = self.unicode_state()
        path = self.output()
        export_evidence(self.case, state, path)
        card = self.read(path, 'cards.json')[0]
        self.assertEqual(card['reader_label'], 'E001')
        self.assertEqual(card['retained_span']['byte_start'], len('앞🙂 '.encode()))
        self.assertEqual(card['retained_span']['literal'], '끝증거🙂')
        self.assertEqual(card['retained_span']['coordinate_space'], 'retained_utf8_scalar_bytes')
        self.assertEqual(card['original_locator']['coordinate_space'], 'original_record_id')
        self.assertEqual(card['original_locator']['line_number'], 9)
        self.assertIsNone(card['original_locator']['byte_start'])
        self.assertEqual(card['source']['source_id'], eid)
        self.assertEqual(card['content_hash']['object'], 'retained_utf8_scalar')
        self.assertEqual(card['retained_span']['hash']['object'], 'retained_utf8_span')
        self.assertEqual(card['coverage']['original_scope_completeness'], 'unknown')
        self.assertEqual(self.verify(path).returncode, 0)

    def test_only_selected_scalars_and_safe_actual_receipt_metadata_are_exported(self):
        state, eid = self.unicode_state()
        path = self.output()
        export_evidence(self.case, state, path)
        with zipfile.ZipFile(path) as archive:
            payloads = b''.join(archive.read(name) for name in archive.namelist())
        self.assertNotIn(b'NEVER_EXPORT_NATIVE_CREDENTIAL', payloads)
        self.assertNotIn(b'NEVER_EXPORT_UNSELECTED_FIELD', payloads)
        receipts = self.read(path, 'receipts.json')
        self.assertEqual(receipts[0]['receipt_id'], eid)
        self.assertEqual(receipts[0]['start_status'], 'matched')
        self.assertEqual(receipts[0]['native_call']['tool_call_id'], 'call-test')
        self.assertNotIn('authorization', receipts[0]['native_call'])

    def test_altered_source_and_invented_excerpt_fail_before_writing(self):
        state, eid = self.unicode_state()
        with self.case.connect() as db:
            data = self.case.event(eid)['data']
            data['lines'][0]['text'] = 'changed source'
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(data), eid))
        path = self.output()
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            export_evidence(self.case, state, path)
        self.assertFalse(path.exists())
        state = deepcopy(self.state)
        state['observations'][0]['literal'] = 'Invented'
        with self.assertRaisesRegex(ValueError, 'exact retained'):
            export_evidence(self.case, state, path)
        self.assertFalse(path.exists())

    def test_missing_source_or_wrong_case_is_never_faked(self):
        path = self.output()
        state = deepcopy(self.state)
        state['meta']['case_id'] = 'another-case'
        with self.assertRaisesRegex(ValueError, 'another case'):
            export_evidence(self.case, state, path)
        state, eid = self.unicode_state()
        with self.case.connect() as db:
            db.execute('DELETE FROM events WHERE id=?', (eid,))
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            export_evidence(self.case, state, path)
        self.assertFalse(path.exists())

    def test_selected_question_export_excludes_unrelated_sources_and_claims(self):
        (self.case.root / 'unrelated.log').write_text('NEVER_EXPORT_OTHER_QUESTION')
        result = json.loads(self.case.invoke('forsic_read', {'path': 'unrelated.log'}))
        note(self.case, {'action': 'save', 'question': 'Other unrelated question',
                        'answer': 'NEVER_EXPORT_OTHER_QUESTION', 'evidence_ids': [result['evidence_id']]})
        state = current(self.case)
        selected = selected_report_state(state, [self.state['questions'][0]['id']])
        path = self.output()
        export_evidence(self.case, selected, path)
        self.assertEqual(self.read(path, 'manifest.json')['question_ids'], [self.state['questions'][0]['id']])
        self.assertNotIn(result['evidence_id'], [s['source']['source_id'] for s in self.read(path, 'sources.json')])
        with zipfile.ZipFile(path) as archive:
            self.assertNotIn(b'NEVER_EXPORT_OTHER_QUESTION', b''.join(archive.read(n) for n in archive.namelist()))

    def test_counterevidence_and_limits_survive_and_source_only_has_no_fake_span(self):
        state = deepcopy(self.state)
        expected = state['claims'][0]['counterevidence_refs']
        path = self.output()
        export_evidence(self.case, state, path)
        claims = self.read(path, 'selection.json')['claims']
        self.assertEqual(claims[0]['counterevidence_refs'], expected)
        self.assertEqual(claims[0]['limitations'], state['claims'][0]['limitations'])
        state = current(self.case)
        state['observations'] = []
        for field in ('assessments', 'claims', 'timeline'):
            state[field] = []
        for question in state['questions']:
            question['claim_refs'] = []
        path = self.output('source-only')
        export_evidence(self.case, state, path)
        self.assertEqual(self.read(path, 'cards.json'), [])
        self.assertTrue(self.read(path, 'sources.json'))

    def test_deterministic_bytes_hashes_and_existing_artifact_preservation(self):
        first, second = self.output(), self.output('two')
        result = export_evidence(self.case, self.state, first)
        other = export_evidence(self.case, self.state, second)
        self.assertEqual(result, other)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(result['sha256'], hashlib.sha256(first.read_bytes()).hexdigest())
        with self.assertRaises(FileExistsError):
            export_evidence(self.case, self.state, first)

    def test_generated_report_is_never_original_and_unknown_stays_explicit(self):
        state, eid = self.unicode_state()
        with self.case.connect() as db:
            data = self.case.event(eid)['data']
            data['provenance'] = {'source_role': 'generated_report'}
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(data), eid))
        source = source_record(self.case.event(eid))
        state['sources'] = [source]
        state['observations'][0]['source_ref'] = ref('source', source)
        path = self.output()
        export_evidence(self.case, state, path)
        self.assertEqual(self.read(path, 'cards.json')[0]['source']['source_role'], 'generated_report')
        self.assertEqual(self.read(path, 'sources.json')[0]['original_locator']['kind'], 'generated_document')
        path = self.output('unknown')
        export_evidence(self.case, self.state, path)
        for source in self.read(path, 'sources.json'):
            self.assertEqual(source['source']['source_role'], 'derived')
            self.assertEqual(source['provenance_status'], 'original_provenance_unknown')

    def test_redacted_export_refused_without_creating_or_leaking_original_payload(self):
        path = self.output()
        with self.assertRaisesRegex(ValueError, 'redacted reports'):
            export_evidence(self.case, self.state, path, redact=['maintenance'])
        self.assertFalse(path.exists())

    def test_verifier_detects_modified_member_and_modified_span_even_with_updated_member_hash(self):
        path = self.output()
        export_evidence(self.case, self.state, path)
        with zipfile.ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        cards = json.loads(members['cards.json'])
        cards[0]['retained_span']['literal'] = 'invented'
        members['cards.json'] = json.dumps(cards).encode()
        manifest = json.loads(members['manifest.json'])
        index = next(f for f in manifest['files'] if f['name'] == 'cards.json')
        index.update(bytes=len(members['cards.json']), sha256=hashlib.sha256(members['cards.json']).hexdigest())
        members['manifest.json'] = json.dumps(manifest).encode()
        bad = self.output('altered')
        with zipfile.ZipFile(bad, 'w') as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
        verified = self.verify(bad)
        self.assertNotEqual(verified.returncode, 0)
        self.assertIn('Excerpt differs', verified.stderr)

    def test_verifier_recomputes_member_digests(self):
        path = self.output()
        export_evidence(self.case, self.state, path)
        with zipfile.ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        excerpt = next(name for name in members if name.startswith('excerpts/'))
        members[excerpt] += b'altered bytes'
        bad = self.output('changed-member')
        with zipfile.ZipFile(bad, 'w') as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
        verified = self.verify(bad)
        self.assertNotEqual(verified.returncode, 0)
        self.assertIn('Member hash mismatch', verified.stderr)

    def test_output_boundary_and_symlink_rejected(self):
        with self.assertRaisesRegex(ValueError, 'report staging'):
            export_evidence(self.case, self.state, Path(self.tmp.name) / 'selected-evidence.zip')
        path = self.output()
        path.symlink_to(Path(self.tmp.name) / 'not-created')
        with self.assertRaisesRegex(ValueError, 'report staging'):
            export_evidence(self.case, self.state, path)

    def test_verifier_checks_claim_references_and_selected_question_revisions(self):
        path = self.output()
        export_evidence(self.case, self.state, path)
        with zipfile.ZipFile(path) as archive:
            original = {name: archive.read(name) for name in archive.namelist()}
        for field in ('claim_reference', 'question_revision'):
            with self.subTest(field=field):
                members = dict(original)
                selection = json.loads(members['selection.json'])
                if field == 'claim_reference':
                    claim = next(c for c in selection['claims'] if c['source_refs'])
                    claim['source_refs'][0]['version'] = 'changed-selected-reference'
                else:
                    selection['questions'][0]['revision'] += 1
                members['selection.json'] = json.dumps(selection).encode()
                manifest = json.loads(members['manifest.json'])
                index = next(f for f in manifest['files'] if f['name'] == 'selection.json')
                index.update(bytes=len(members['selection.json']),
                             sha256=hashlib.sha256(members['selection.json']).hexdigest())
                members['manifest.json'] = json.dumps(manifest).encode()
                bad = self.output(field)
                with zipfile.ZipFile(bad, 'w') as archive:
                    for name, raw in members.items():
                        archive.writestr(name, raw)
                verified = self.verify(bad)
                self.assertNotEqual(verified.returncode, 0)
                self.assertIn('mismatch', verified.stderr)

    def test_image_volume_archive_navigation_and_original_ranges_stay_separate(self):
        state, eid = self.unicode_state()
        event = self.case.event(eid)
        data = event['data']
        data.update(tool='forsic_image_files', path='synthetic.E01', volume_offset=1048576,
                    file_path='/logs/archive.tar', inode=27,
                    member={'member_index': 4, 'name': 'inside.log', 'header_offset': 2048,
                            'data_offset': 2560, 'extra_secret': 'NEVER_EXPORT_MEMBER_FIELD'},
                    base64='YWJj', byte_start=100, byte_end=103)
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(data), eid))
        source = source_record(self.case.event(eid))
        state['sources'] = [source]
        state['observations'][0]['source_ref'] = ref('source', source)
        path = self.output()
        export_evidence(self.case, state, path)
        locator = self.read(path, 'cards.json')[0]['original_locator']
        self.assertEqual(locator['image_path'], 'synthetic.E01')
        self.assertEqual(locator['volume_offset'], 1048576)
        self.assertEqual(locator['path'], '/logs/archive.tar')
        self.assertEqual(locator['member']['member_index'], 4)
        self.assertEqual(locator['member']['data_offset'], 2560)
        self.assertNotIn('extra_secret', locator['member'])
        self.assertIsNone(locator['byte_start'])
        self.assertEqual(locator['returned_original_range']['coordinate_space'], 'archive_member_bytes')
        self.assertEqual(locator['returned_original_range']['byte_start'], 100)
        self.assertEqual(self.read(path, 'cards.json')[0]['retained_span']['byte_start'], len('앞🙂 '.encode()))


if __name__ == '__main__':
    unittest.main()
