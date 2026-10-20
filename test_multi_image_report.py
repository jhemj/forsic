"""Synthetic image identities through native read, question, report and ZIP.

Uses real temporary intake bindings and native session/goal SQLite state;
the registration configuration is a Mock and the image parser is mocked.
No E01 parsing, container, network or model is run.
Matching hostname/path/text deliberately do not identify the originating image.
"""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile

import test_specialist_contracts as fixtures
from forsic_plugin.report_driven.host import checked, current, find


class MultiImageReportTests(unittest.TestCase):
    def setUp(self):
        fixtures.SpecialistContracts.setUp(self)
        from test_environment_support import offline_environment
        offline_environment(self)
    call = fixtures.SpecialistContracts.call
    bind = fixtures.SpecialistContracts.bind
    session = fixtures.SpecialistContracts.session
    save_note = fixtures.SpecialistContracts.save_note

    def exercise_origins(self, images, volumes):
        for image in set(images):
            (self.case.root / image).write_bytes(('synthetic image ' + image).encode())
        inventory = self.call('forsic_list', {'path': '.'})
        refs = {row['path']: row['image_ref'] for row in inventory['files'] if 'image_ref' in row}
        internal_path = '/var/log/shared.log'
        literal = 'hostname=SAME-HOST user=alice action=login'
        results = []
        for image, volume in zip(images, volumes):
            with patch('forsic_plugin.image_process.run_parser', return_value={
                'lines': [{'line': 1, 'text': literal}], 'volume_offset': volume,
                'volume_selection': 'explicit', 'complete_file': True}) as parser:
                result = self.call('forsic_image_read', {'image_ref': refs[image], 'action': 'read',
                    'file_path': internal_path, 'volume_offset': volume})
            self.assertNotIn('error', result, result)
            parser.assert_called_once()
            self.assertEqual(result['path'], image)
            self.assertEqual(result['volume_offset'], volume)
            results.append(result)
        self.assertNotEqual(results[0]['evidence_id'], results[1]['evidence_id'])
        if images[0] != images[1]:
            self.assertNotEqual(refs[images[0]], refs[images[1]])
        citations = []
        for result in results:
            # Existing content lookup must keep each identical text tied to its
            # requested retained source, rather than selecting by hostname/path.
            page = self.call('forsic_reporting', {'action': 'source',
                'source_id': result['evidence_id'], 'query': literal})
            self.assertNotIn('error', page, page)
            body = next(m for m in page['matches'] if m['role'] == 'body' and m['pointer'] == '/lines/0/text')
            citations.append(body['citation_ref'])
            retained = self.case.event(page['evidence_id'])['data']
            match = next(m for m in retained['matches'] if m['pointer'] == '/lines/0/text')
            self.assertEqual(match['citation']['source_id'], result['evidence_id'])
        self.assertNotEqual(citations[0], citations[1])
        answer = 'The selected image/volume records contain the same hostname and login text; host identity and causal linkage remain unknown.'
        saved = self.call('forsic_question', {'operation_id': 'same-host-two-origins',
            'question': 'What do the two selected image records show?',
            'scope': 'Two explicitly selected images/volumes and one internal log record each',
            'answer': answer, 'reason': 'Preserve both original locations without merging by hostname.',
            'priority': 'material', 'judgment': {'citation_refs': citations,
                'assertion_kind': 'fact', 'reasoning_summary': 'Both exact records contain the selected text; equal hostname and path do not prove one host or independent events.',
                'limitations': ['Synthetic selected records only; no cross-host compromise or whole-image coverage established.']}})
        self.assertNotIn('error', saved, saved)
        state = current(self.case)
        checked(state)
        question = find(state, 'questions', saved['question']['question_id'])
        self.assertEqual(question['session_id'], self.sid)
        self.assertTrue(question['goal_id'])
        claim = find(state, 'claims', question['claim_refs'][0]['id'])
        observations = [find(state, 'observations', reference['id']) for reference in claim['source_refs']]
        self.assertEqual(len(observations), 2)
        expected_ids = {result['evidence_id'] for result in results}
        self.assertEqual({o['source_ref']['id'] for o in observations}, expected_ids)
        report = self.call('forsic_reporting', {'action': 'render', 'allow_partial': True, 'question_ids': [question['id']]})
        self.assertNotIn('error', report, report)
        directory = Path(report['manifest']).parent
        html = (directory / 'integrated.html').read_text()
        self.assertIn(answer, html)
        for observation in observations:
            self.assertIn(observation['id'], html)
        evidence = directory / 'selected-evidence.zip'
        with zipfile.ZipFile(evidence) as archive:
            cards = json.loads(archive.read('cards.json'))
            selection = json.loads(archive.read('selection.json'))
            verifier = archive.read('verify.py')
        self.assertEqual(len(cards), 2)
        self.assertEqual({c['source']['source_id'] for c in cards}, expected_ids)
        locators = {(c['original_locator']['image_path'], c['original_locator']['volume_offset'],
                     c['original_locator']['path']) for c in cards}
        self.assertEqual(locators, {(image, volume, internal_path) for image, volume in zip(images, volumes)})
        self.assertEqual(len(locators), 2)
        self.assertEqual({c['retained_span']['literal'] for c in cards}, {literal})
        self.assertEqual({c['retained_span']['field_pointer'] for c in cards}, {'/lines/0/text'})
        exported_question = next(q for q in selection['questions'] if q['id'] == question['id'])
        self.assertEqual(exported_question['answer'], answer)
        self.assertEqual(exported_question['claim_refs'], question['claim_refs'])
        script = self.root / 'verify_multi_image.py'
        script.write_bytes(verifier)
        verified = subprocess.run([sys.executable, str(script), str(evidence)], capture_output=True, text=True)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)['verified_cards'], 2)
        self.assertEqual(current(self.case), state)

    def test_same_hostname_path_and_text_keep_two_image_origins(self):
        self.exercise_origins(['snapshot-a.E01', 'snapshot-b.E01'], [4096, 4096])

    def test_same_hostname_path_and_text_keep_two_partition_origins(self):
        self.exercise_origins(['snapshot.E01', 'snapshot.E01'], [4096, 8192])


if __name__ == '__main__':
    unittest.main()
