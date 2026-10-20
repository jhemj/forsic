"""Synthetic rendering/export of current evidence and frozen report declarations."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / 'upstream' / 'hermes')]

import test_report_slots as slot_fixtures
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.evidence_export import VERIFIER, export_evidence
from forsic_plugin.report_driven.views import bundle, selected_report_state


class ReportSlotTraceabilityTests(unittest.TestCase):
    setUp = slot_fixtures.ReportSlotsTests.setUp
    call = slot_fixtures.ReportSlotsTests.call
    save = slot_fixtures.ReportSlotsTests.save
    judgment = slot_fixtures.ReportSlotsTests.judgment
    revise = slot_fixtures.ReportSlotsTests.revise
    row = slot_fixtures.ReportSlotsTests.row
    grounded = slot_fixtures.ReportSlotsTests.grounded
    entries = slot_fixtures.ReportSlotsTests.entries

    def output(self, name):
        directory = self.case.output / ('.report-stage-' + name)
        directory.mkdir()
        return directory / 'selected-evidence.zip'

    def members(self, path):
        with zipfile.ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def verify(self, path):
        namespace = {'__name__': 'evidence_verifier'}
        exec(self.members(path)['verify.py'], namespace)
        return namespace['verify'](path)

    def verify_current(self, path):
        namespace = {'__name__': 'current_evidence_verifier'}
        exec(VERIFIER, namespace)
        return namespace['verify'](path)

    def write_members(self, members, name):
        result = self.output(name)
        with zipfile.ZipFile(result, 'w') as archive:
            for key, raw in members.items():
                archive.writestr(key, raw)
        return result

    def tampered(self, original, name, transform, *, rehash=False):
        members = self.members(original)
        members[name] = transform(members[name])
        if rehash:
            manifest = json.loads(members['manifest.json'])
            item = next(f for f in manifest['files'] if f['name'] == name)
            item.update(bytes=len(members[name]), sha256=hashlib.sha256(members[name]).hexdigest())
            members['manifest.json'] = json.dumps(manifest).encode()
        result = self.output('tampered-' + str(len(list(self.case.output.glob('.report-stage-*')))))
        with zipfile.ZipFile(result, 'w') as archive:
            for key, raw in members.items():
                archive.writestr(key, raw)
        return result

    def replaced(self):
        q = self.grounded(); original = deepcopy(q['report_slots'][0])
        q = self.revise(q, 'replace-report-basis', answer='The process was created; success remains uncertain.',
                        judgment=self.judgment('process.log'), reason='Use the separately observed process record')
        return q, original, host.current(self.case)

    def test_render_after_claim_replacement_preserves_frozen_trace_and_verifies_current_evidence(self):
        q, original, state = self.replaced(); before = self.case.db.read_bytes()
        with patch('socket.socket', side_effect=AssertionError('No network')):
            rendered = bundle(self.case, state, question_ids=[q['id']])
        directory = Path(rendered['manifest']).parent
        payloads = self.members(directory / 'selected-evidence.zip')
        declarations = json.loads(payloads['report-slot-declarations.json'])
        entry = next(s for s in declarations['projection']['slots'] if s['slot'] == 'entry_route')['entries'][0]
        self.assertTrue(entry['needs_recheck'])
        self.assertEqual(entry['claim_refs'], original['claim_refs'])
        self.assertEqual(entry['claim_details'], original['claim_details'])
        self.assertEqual(entry['scope'], original['scope'])
        self.assertFalse(declarations['current_reference_verification'])
        selection = json.loads(payloads['selection.json'])
        self.assertTrue(all('report_slots' not in question for question in selection['questions']))
        self.assertEqual(selection['questions'][0]['claim_refs'], q['claim_refs'])
        self.assertEqual(self.verify(directory / 'selected-evidence.zip')['snapshot_id'], state['meta']['snapshot_id'])
        html = (directory / 'integrated.html').read_text()
        self.assertIn('재검토 필요', html)
        self.assertIn(original['brief'], html)
        self.assertEqual(before, self.case.db.read_bytes())

    def test_frozen_file_hash_and_current_graph_references_are_independently_verified(self):
        _, _, state = self.replaced(); path = self.output('safe-export')
        export_evidence(self.case, state, path)
        def changed_brief(raw):
            value = json.loads(raw)
            next(s for s in value['projection']['slots'] if s['slot'] == 'entry_route')['entries'][0]['brief'] = 'Tampered declaration'
            return json.dumps(value).encode()
        tampered = self.tampered(path, 'report-slot-declarations.json', changed_brief)
        with self.assertRaisesRegex(ValueError, 'Member hash mismatch'):
            self.verify(tampered)
        def changed_current_ref(raw):
            value = json.loads(raw)
            next(c for c in value['claims'] if c['source_refs'])['source_refs'][0]['version'] = 'wrong-current-version'
            return json.dumps(value).encode()
        tampered = self.tampered(path, 'selection.json', changed_current_ref, rehash=True)
        with self.assertRaisesRegex(ValueError, 'Selected reference version mismatch'):
            self.verify(tampered)
        def changed_role(raw):
            value = json.loads(raw); value['role'] = 'current_verified_evidence'
            return json.dumps(value).encode()
        tampered = self.tampered(path, 'report-slot-declarations.json', changed_role, rehash=True)
        with self.assertRaisesRegex(ValueError, 'Historical report declaration identity mismatch'):
            self.verify(tampered)

    def test_current_selected_source_bytes_still_require_exact_observation_match(self):
        q = self.grounded(); source = next(r for r in q['report_slots'][0]['evidence_refs'] if r['kind'] == 'source')
        revised = deepcopy(self.case.event(source['id'])['data'])
        revised['lines'][0]['text'] = 'Different retained record with no original excerpt'
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(revised), source['id']))
        state = host.current(self.case); self.assertTrue(self.entries(state)[0]['needs_recheck'])
        with self.assertRaisesRegex(ValueError, 'exact retained UTF-8 scalar excerpt'):
            export_evidence(self.case, state, self.output('changed-actual-source'))

    def test_selected_question_package_excludes_other_reporting_declarations(self):
        first = self.grounded()
        self.save('unrelated-slot', scope='NEVER_EXPORT_OTHER_QUESTION_SCOPE', report_slots=[
            self.row(state='unavailable', claim_indices=[], brief='NEVER_EXPORT_OTHER_QUESTION_BRIEF',
                     basis='NEVER_EXPORT_OTHER_QUESTION_BASIS')])
        state = selected_report_state(host.current(self.case), [first['id']])
        path = self.output('selected-question')
        export_evidence(self.case, state, path)
        payloads = self.members(path)
        self.assertNotIn(b'NEVER_EXPORT_OTHER_QUESTION', b''.join(payloads.values()))
        self.assertEqual(self.verify(path)['snapshot_id'], state['meta']['snapshot_id'])

    def test_frozen_field_names_inside_excluded_native_arguments_do_not_leak(self):
        self.grounded(); state = host.current(self.case)
        # Synthetic excluded execution records exercise the export whitelist;
        # no native tool, scheduler or model is invoked.
        state['missions'].append({'id': 'synthetic-private-mission', 'version': '1',
            'tool_arguments': {'temporal': {'secret': 'NEVER_EXPORT_NATIVE_TEMPORAL_ARGUMENT'},
                               'forensic': 'NEVER_EXPORT_NATIVE_FORENSIC_ARGUMENT'}})
        state['tests'].append({'id': 'synthetic-private-test', 'version': '1',
            'temporal_context': {'secret': 'NEVER_EXPORT_EXCLUDED_TEST_FIELD'}})
        path = self.output('no-hoisted-arguments')
        export_evidence(self.case, state, path)
        payloads = self.members(path)
        self.assertNotIn(b'NEVER_EXPORT_NATIVE_', b''.join(payloads.values()))
        self.assertNotIn(b'NEVER_EXPORT_EXCLUDED_TEST_FIELD', b''.join(payloads.values()))
        self.assertEqual(self.verify(path)['snapshot_id'], state['meta']['snapshot_id'])

    def test_report_only_changes_preserve_independent_review_evaluation_fingerprint(self):
        from forsic_plugin.independent_review import _evaluation_fingerprint, _question_records
        q = self.grounded()
        original = _evaluation_fingerprint(_question_records(self.resolved))
        self.revise(q, 'editorial-report-change', report_slots=[self.row(brief='A request was observed; completion remains unverified.')])
        self.assertEqual(_evaluation_fingerprint(_question_records(self.resolved)), original)

    def test_current_standalone_verifier_accepts_old_format_and_still_checks_inline_refs(self):
        q = self.grounded(); state = host.current(self.case); path = self.output('new-format')
        export_evidence(self.case, state, path)
        members = self.members(path); manifest = json.loads(members['manifest.json'])
        old_files = {'report-slot-declarations.json', 'historical-bindings.json'}
        # Former schema-1 format: no extension descriptors/members; frozen
        # declarations stayed inline and were checked as ordinary exact refs.
        for key in ('historical_report_declarations', 'historical_bindings'):
            manifest.pop(key)
        manifest['files'] = [f for f in manifest['files'] if f['name'] not in old_files]
        for name in old_files:
            members.pop(name)
        selection = json.loads(members['selection.json'])
        selected = next(question for question in selection['questions'] if question['id'] == q['id'])
        selected['forensic'] = {'dependencies': [{'reference': q['claim_refs'][0], 'reason': 'Selected current claim'}]}
        members['selection.json'] = json.dumps(selection).encode()
        index = next(f for f in manifest['files'] if f['name'] == 'selection.json')
        index.update(bytes=len(members['selection.json']), sha256=hashlib.sha256(members['selection.json']).hexdigest())
        members['manifest.json'] = json.dumps(manifest).encode()
        old = self.write_members(members, 'old-format')
        self.assertEqual(self.verify_current(old)['snapshot_id'], state['meta']['snapshot_id'])
        self.assertEqual(self.verify(old)['snapshot_id'], state['meta']['snapshot_id'])
        def changed_old_ref(raw):
            value = json.loads(raw)
            selected = next(question for question in value['questions'] if question['id'] == q['id'])
            selected['forensic']['dependencies'][0]['reference']['version'] = 'wrong-legacy-version'
            return json.dumps(value).encode()
        tampered = self.tampered(old, 'selection.json', changed_old_ref, rehash=True)
        with self.assertRaisesRegex(ValueError, 'Selected reference version mismatch'):
            self.verify_current(tampered)

    def test_partial_extension_metadata_or_orphaned_members_cannot_downgrade_to_legacy(self):
        self.grounded(); path = self.output('extension-pair')
        export_evidence(self.case, host.current(self.case), path)
        original = self.members(path)
        for mode in ('one_descriptor', 'both_descriptors', 'one_member', 'one_pair'):
            with self.subTest(mode=mode):
                members = dict(original); manifest = json.loads(members['manifest.json'])
                if mode in ('one_descriptor', 'both_descriptors', 'one_pair'):
                    manifest.pop('historical_bindings')
                if mode == 'both_descriptors':
                    manifest.pop('historical_report_declarations')
                if mode in ('one_member', 'one_pair'):
                    members.pop('historical-bindings.json')
                    manifest['files'] = [f for f in manifest['files'] if f['name'] != 'historical-bindings.json']
                members['manifest.json'] = json.dumps(manifest).encode()
                partial = self.write_members(members, 'partial-' + mode)
                with self.assertRaisesRegex(ValueError, 'Incomplete historical declaration extension'):
                    self.verify_current(partial)

    def test_combined_report_and_forensic_frozen_bindings_survive_claim_replacement(self):
        q = self.grounded(forensic={}); original = deepcopy(q['forensic'])
        self.revise(q, 'replace-with-workbench', answer='Process creation leaves success uncertain.',
                    judgment=self.judgment('process.log'), reason='Use the separate process observation')
        state = host.current(self.case)
        rendered = bundle(self.case, state, question_ids=[q['id']])
        path = Path(rendered['manifest']).parent / 'selected-evidence.zip'
        members = self.members(path)
        bindings = json.loads(members['historical-bindings.json'])
        retained = next(b for b in bindings['bindings'] if b['field'] == 'forensic')
        self.assertEqual(retained['value'], original)
        self.assertTrue(retained['path'].endswith('/forensic'))
        self.assertEqual(bindings['role'], 'frozen_analyst_bindings_not_current_evidence')
        current = json.loads(members['selection.json'])
        self.assertTrue(all('forensic' not in row and 'forensic_context' not in row for row in current['questions']))
        self.assertEqual(self.verify(path)['snapshot_id'], state['meta']['snapshot_id'])
        tampered = self.tampered(path, 'historical-bindings.json', lambda raw: raw + b' ')
        with self.assertRaisesRegex(ValueError, 'Member hash mismatch'):
            self.verify(tampered)

    def test_temporal_source_version_change_preserves_frozen_bases_without_rewriting_them(self):
        read = self.call('forsic_read', {'path': 'request.log'})
        row = {'event_ref': 'TE-selected-request', 'description': 'A request-related event with unknown occurrence time',
            'evidence_ids': [read['evidence_id']], 'temporal': {
                'observed_or_inferred': 'inferred', 'timestamp_role': 'event_occurrence',
                'confidence_basis': 'The request text does not establish an occurrence date.',
                'basis_refs': [{'evidence_id': read['evidence_id'], 'pointer': '/lines/0/text',
                                'literal': 'job=maintenance', 'byte_start': 0}]}}
        q = self.grounded(timeline=[row]); state = host.current(self.case)
        original_time = next(t for t in state['timeline'] if t.get('question_id') == q['id'])['temporal']
        # Correct only returned metadata: exact retained scalar bytes are intact.
        changed = deepcopy(self.case.event(read['evidence_id'])['data'])
        changed['scope_note'] = 'Corrected acquisition metadata; retained body is unchanged'
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(changed), read['evidence_id']))
        state = host.current(self.case); host.checked(state)
        current_time = next(t for t in state['timeline'] if t.get('question_id') == q['id'])
        self.assertTrue(current_time['temporal_context']['temporal_requires_recheck'])
        rendered = bundle(self.case, state, question_ids=[q['id']])
        path = Path(rendered['manifest']).parent / 'selected-evidence.zip'
        bindings = json.loads(self.members(path)['historical-bindings.json'])['bindings']
        retained = next(b for b in bindings if b['field'] == 'temporal')
        self.assertEqual(retained['value'], original_time)
        self.assertEqual(self.verify(path)['snapshot_id'], state['meta']['snapshot_id'])


if __name__ == '__main__':
    unittest.main()
