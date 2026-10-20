"""CP05 representative data and scripted persistence contracts; no model behavior trial."""
import hashlib
import json
from pathlib import Path
from shutil import copyfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from forsic_plugin import independent_review as review
from forsic_plugin.investigation_context import resolve_context
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.word_export import export_word
import test_specialist_contracts as fixtures

ROOT = Path(__file__).parent / 'tests' / 'acceptance'


class AcceptanceDatasetTests(unittest.TestCase):
    def test_manifest_integrity_separation_and_deliberate_partial_data(self):
        manifest = json.loads((ROOT / 'manifest.json').read_text())
        self.assertEqual(manifest['model_behavior'], 'not_run')
        self.assertEqual(len(manifest['datasets']), 3)
        paths = set()
        for dataset in manifest['datasets']:
            self.assertEqual(dataset['license'], 'CC0-1.0')
            self.assertTrue(dataset['source'])
            for item in dataset['files']:
                path = ROOT / item['path']
                self.assertNotIn(item['path'], paths)
                paths.add(item['path'])
                raw = path.read_bytes()
                self.assertEqual(len(raw), item['bytes'])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), item['sha256'])
                self.assertEqual(path.parts[-3] == 'inputs', item['role'] == 'model_input')
        tracked = {str(p.relative_to(ROOT)) for parent in ('inputs', 'truth') for p in (ROOT / parent).rglob('*') if p.is_file()}
        self.assertEqual(paths, tracked)
        partial = (ROOT / 'inputs/partial_range/records.jsonl').read_text().splitlines()
        self.assertEqual(len(partial), 3)
        json.loads(partial[0]); json.loads(partial[2])
        with self.assertRaises(json.JSONDecodeError): json.loads(partial[1])


class RepresentativeWriterContracts(unittest.TestCase):
    setUp = fixtures.SpecialistContracts.setUp
    bind = fixtures.SpecialistContracts.bind
    session = fixtures.SpecialistContracts.session
    save_note = fixtures.SpecialistContracts.save_note
    call = fixtures.SpecialistContracts.call

    def test_representative_sources_provisional_review_correction_and_report(self):
        # A model or network dispatch would be an error, including through final rendering.
        with patch('agent.auxiliary_client.call_llm', side_effect=AssertionError('No model calls in lane C')), \
             patch('socket.create_connection', side_effect=AssertionError('No network in lane C')):
            for dataset in json.loads((ROOT / 'manifest.json').read_text())['datasets']:
                with self.subTest(dataset=dataset['id']):
                    self.roundtrip(dataset)

    def roundtrip(self, dataset):
        name = dataset['id']
        source = ROOT / 'inputs' / name
        truth = json.loads((ROOT / 'truth' / (name + '.json')).read_text())
        path = self.case.root / (name + '.jsonl')
        copyfile(source / 'records.jsonl', path)
        original = path.read_bytes()
        observed = self.call('forsic_read', {'path': path.name, 'line_start': 1,
                                           'line_count': dataset['limits']['line_count']})
        self.assertNotIn('error', observed, observed)
        cites = [span['citation_ref'] for span in observed['presented_spans']]
        self.assertEqual(len(observed['lines']), min(3, dataset['limits']['line_count']))
        if name == 'partial_range':
            self.assertNotIn('exit_code', '\n'.join(line['text'] for line in observed['lines']))
        question = (source / 'question.txt').read_text().splitlines()[0]
        provisional = 'The selected records have been read; their explanation remains provisional.'
        saved = self.call('forsic_question', {'operation_id': name + '-provisional',
            'question': question, 'scope': name + ': selected initial read only', 'answer': provisional,
            'reason': 'Preserve a provisional answer before reviewing its limits.',
            'judgment': {'citation_refs': cites, 'assertion_kind': 'interpretation',
                         'reasoning_summary': 'Scripted contract payload; no semantic approval.'}})
        self.assertNotIn('error', saved, saved)
        qid = saved['question'].get('question_id') or saved['question']['id']
        before = host.find(host.current(self.case), 'questions', qid)
        self.assertEqual(before['answer'], provisional)
        resolved = resolve_context(self.sid, intake_root=self.intake, native_db=self.native)
        packet = review._collect(resolved, 'exploration')
        body = json.loads(packet['prompt'])
        presented = next(s for s in body['sources'] if s['source_id'] == observed['evidence_id'])
        field = next(f for f in presented['fields'] if f['pointer'] == '/lines/0/text')
        rid = 'scripted-contract-' + name
        request = {'request_id': rid, 'identity': review._identity(resolved), 'created_ns': 1,
            'schema_version': 3, 'stage': 'exploration', 'fixture_only': True,
            'material_fingerprint': packet['fingerprint'], 'evaluation_fingerprint': packet['evaluation_fingerprint'],
            'messages': [{'role': 'system', 'content': review.SYSTEM}, {'role': 'user', 'content': packet['prompt']}]}
        prefix = 'independent-review-' + review._goal_key(resolved) + '-' + rid
        request_path = self.case.output / (prefix + '-request.json')
        request_path.write_text(json.dumps(request))
        raw = json.dumps({'findings': [{'review_ref': field['review_ref'], 'question': truth['review_question'],
            'why': 'Scripted contract advice: check the retained scope.', 'next_check': 'Compare the supplied records and limits.'}]})
        (self.case.output / (prefix + '-response.json')).write_text(json.dumps({
            'request_id': rid, 'response_version': 1,
            'request_sha256': hashlib.sha256(request_path.read_bytes()).hexdigest(),
            'status': 'received', 'finish_reason': 'stop', 'raw_advice': raw, 'fixture_only': True}))
        finding = next(f for f in review.review_state(resolved)['findings'] if f['review_id'] == rid)
        final_answer = truth['scripted_contract_answer']
        response = {'review_id': rid, 'finding_id': finding['finding_id'], 'disposition': 'investigated',
                    'reason': truth['scripted_response_reason'], 'citation_refs': cites}
        corrected = self.call('forsic_question', {'operation_id': name + '-corrected', 'question_id': qid,
            'expected_revision': before['revision'], 'answer': final_answer, 'reason': 'Scripted bounded correction',
            'judgment': {'citation_refs': cites, 'assertion_kind': 'interpretation',
                         'reasoning_summary': 'Contract persistence only; human semantic review remains required.'},
            'review_responses': [response]})
        self.assertNotIn('error', corrected, corrected)
        current = host.find(host.current(self.case), 'questions', qid)
        self.assertEqual(current['answer'], final_answer)
        self.assertEqual(current['revision'], before['revision'] + 1)
        accepted = current['review_responses'][0]
        self.assertEqual(accepted['review_id'], rid)
        self.assertFalse(accepted['semantic_approval'])
        self.assertEqual([c['literal'] for c in accepted['citations']],
                         [line['text'] for line in observed['lines']])
        self.assertTrue(any(d['review_id'] == rid for d in review.review_state(resolved)['dispositions']))
        history = self.call('forsic_note', {'action': 'get', 'note_id': qid.removeprefix('Q-'), 'include_history': True, 'limit': 10})
        self.assertTrue(any(n.get('answer') == provisional for n in history['history']))
        report = self.call('forsic_reporting', {'action': 'render', 'allow_partial': True, 'question_ids': [qid]})
        self.assertNotIn('error', report, report)
        self.assertEqual({Path(file).suffix for file in report['files']}, {'.html'})
        before_manifest = Path(report['manifest']).read_bytes()
        word = export_word(self.case, report['bundle_id'])
        self.assertEqual(word['snapshot_id'], report['snapshot_id'])
        self.assertEqual(Path(report['manifest']).read_bytes(), before_manifest)
        formats = set()
        for file in [*report['files'], word['path']]:
            file = Path(file); formats.add(file.suffix)
            if file.suffix == '.html': text = file.read_text()
            else:
                with ZipFile(file) as doc: text = doc.read('word/document.xml').decode()
            self.assertIn(final_answer, text)
            self.assertNotIn(provisional, text)
            self.assertIn(truth['scripted_response_reason'], text)
        self.assertEqual(formats, {'.html', '.docx'})
        self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__': unittest.main()
