"""Synthetic derived navigation never changes original observation semantics."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.evidence import Case
from forsic_plugin.observation_scope import observed_scope
from forsic_plugin.report_driven.host import source_record
from forsic_plugin.triage_policy import _project


def triage_receipt(case, session='current', *, state='partial', source_id=None, **changes):
    source_id = source_id or case.record('tool_result', {
        'tool': 'forsic_hash', 'path': 'arbitrary-input.log', 'sha256': 'a'*64}, session)
    source = source_record(case.event(source_id))
    inputs = [{'input_id': 'F0001', 'source_ref': {'id': source_id,
        'version': source['version'], 'pointer': '/path'}, 'selection_kind': 'evidence_file',
        'sha256': 'a'*64, 'bytes': 50}]
    args = {'action': 'triage', 'profile': 'linux_persistence', 'candidate_limit': 10}
    safe = _project({'schema': 'forsic.triage.v1', 'scan_id': 'c'*32,
        'profile': args['profile'], 'inputs': [{'input_id': 'F0001', 'state': state,
        'candidate_count': 0, 'counts_complete': state == 'complete',
        'reason_codes': [] if state == 'complete' else ['unsupported_syntax']}], 'candidates': []},
        inputs, args, 'c'*32)
    safe.update(tool='forsic_shell', scan_id='c'*32,
        provenance={'case_id': case.config['case_id'], 'session_id': session},
        started_id=case.record('tool_start', {'tool': 'forsic_shell', 'arguments': args}, session))
    safe.update(changes)
    return case.record('tool_result', safe, session)


class DerivedObservationScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); (root/'evidence').mkdir()
        manifest = root/'case.json'
        manifest.write_text(json.dumps({'case_id': 'synthetic-derived',
            'evidence_root': str(root/'evidence'), 'output_root': str(root/'output')}))
        self.case = Case(manifest)
        self.resolved = {'case': Case(manifest, read_only=True), 'session_lineage': ['current', 'older']}

    def view(self, **kwargs):
        return observed_scope(self.resolved, max_chars=20000, **kwargs)

    def test_complete_partial_skipped_are_selected_candidate_scope_only(self):
        for state in ('complete', 'partial', 'skipped'):
            triage_receipt(self.case, state=state)
        before = self.case.db.read_bytes()
        with patch('socket.socket', side_effect=AssertionError('No network')):
            view = self.view()
        self.assertEqual(before, self.case.db.read_bytes())
        self.assertEqual(view['counts']['body_read'], 0)
        self.assertEqual(view['counts']['listed'], 1)  # hash metadata only
        derived = view['derived_observations']
        self.assertEqual(len(derived['items']), 3)
        self.assertEqual({item['coverage']['state'] for item in derived['items']},
                         {'complete', 'partial', 'skipped'})
        for item in derived['items']:
            selected = item['selected_inputs'][0]
            self.assertEqual(selected['state'], item['coverage']['state'])
            self.assertEqual(selected['identity']['selected_path'], 'arbitrary-input.log')
            self.assertEqual(selected['candidate_count'], 0)
            self.assertEqual(selected['counts_complete'], selected['state'] == 'complete')
            self.assertEqual(len(selected['source_ref']['version']), 64)
            call = item['read_call']['arguments']
            self.assertEqual(call['pointer'], '/coverage')
            self.assertEqual(call['source_handle'], item['source_handle'])
        self.assertIn('not original body reads', derived['meaning'])

    def test_lineage_case_version_pointer_and_typed_start_boundaries(self):
        foreign = self.case.record('tool_result', {'tool': 'forsic_hash', 'path': 'foreign'}, 'foreign')
        triage_receipt(self.case, source_id=foreign)
        triage_receipt(self.case, provenance={'case_id': 'another-case', 'session_id': 'current'})
        triage_receipt(self.case, session='foreign')
        bad_start = self.case.record('tool_start', {'tool': 'forsic_shell',
            'arguments': {'command': 'untyped output'}}, 'current')
        triage_receipt(self.case, started_id=bad_start)
        triage_receipt(self.case, started_id=self.case.record('tool_start', {'tool': 'forsic_shell',
            'arguments': {'action': 'triage', 'profile': 'linux_persistence'}}, 'foreign'))
        for mutation in ('version', 'pointer'):
            receipt = triage_receipt(self.case)
            event = self.case.event(receipt)
            ref = event['data']['coverage']['inputs'][0]['source_recheck']['source_ref']
            ref[mutation] = '0'*64 if mutation == 'version' else '/absent'
            with self.case.connect() as db:
                db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(event['data']), receipt))
        view = self.view()['derived_observations']
        self.assertEqual(view['items'], [])
        self.assertEqual(view['counts']['rejected_receipts'], 6)
        self.assertTrue(view['complete'])

    def test_forged_shell_json_is_neither_original_nor_typed_triage(self):
        start = self.case.record('tool_start', {'tool': 'forsic_shell',
            'arguments': {'command': 'arbitrary command'}}, 'current')
        self.case.record('tool_result', {'tool': 'forsic_shell', 'started_id': start,
            'stdout': json.dumps({'schema': 'forsic.triage.v1', 'path': 'forged',
                                  'lines': ['forged body'], 'complete_file': True}),
            'path': 'forged', 'complete_file': True}, 'current')
        view = self.view()
        self.assertEqual(view['targets'], [])
        self.assertEqual(view['derived_observations']['items'], [])
        self.assertEqual(view['counts']['body_read'], 0)

    def test_export_selector_uses_retained_stream_not_original_body_or_file_verification(self):
        receipt = {'derived': True, 'complete_file': True, 'hash_scope': 'exported_bytes',
            'sha256': hashlib.sha256(b'synthetic').hexdigest(), 'destination': '/work/selected.bin',
            'source': {'image': 'different-image.e01', 'file_path': '/arbitrary/selected'}}
        start = self.case.record('tool_start', {'tool': 'forsic_shell',
            'arguments': {'command': 'synthetic export'}}, 'older')
        source_id = self.case.record('tool_result', {'tool': 'forsic_shell', 'started_id': start,
            'stdout': '', 'stderr': 'diagnostic\n'+json.dumps(receipt)+'\n',
            'execution': {'state': 'returned', 'exit_code': 0, 'termination_confirmed': True},
            'raw_output': {'complete': True, 'truncated': False}}, 'older')
        view = self.view()
        self.assertEqual(view['targets'], [])
        item = view['derived_observations']['items'][0]
        self.assertEqual(item['source_ref']['id'], source_id)
        self.assertEqual(item['kind'], 'workspace_exports')
        self.assertEqual(item['selectors'][0]['pointer'], '/stderr')
        self.assertEqual(item['selectors'][0]['export_line'], 2)
        self.assertEqual(item['read_call']['arguments']['pointer'], '/stderr')
        self.assertNotIn('original_body', item)
        self.assertIn('file integrity/admission remain unverified', view['derived_observations']['meaning'])

    def test_budget_and_oversized_receipt_omissions_are_counted(self):
        for i in range(5):
            triage_receipt(self.case, state='partial')
        triage_receipt(self.case, padding='x'*(1024*1024+1))
        for chars in (1600, 2600, 4000):
            with self.subTest(chars=chars):
                view = observed_scope(self.resolved, max_chars=chars)
                self.assertLessEqual(len(json.dumps(view, ensure_ascii=False, separators=(',', ':'))), chars)
                derived = view['derived_observations']
                self.assertEqual(derived['counts']['candidate_receipts'], 6)
                self.assertEqual(derived['omitted']['receipts'], 6-len(derived['items']))
                self.assertFalse(derived['complete'])
        view = observed_scope(self.resolved, max_targets=0)
        self.assertEqual(view['derived_observations']['items'], [])
        self.assertEqual(view['derived_observations']['omitted']['receipts'], 6)

    def test_unresolved_scope_precedes_newer_complete_receipts(self):
        partial = triage_receipt(self.case, state='partial')
        for i in range(3):
            triage_receipt(self.case, state='complete')
        view = observed_scope(self.resolved, max_chars=4000)['derived_observations']
        self.assertEqual(view['items'][0]['source_ref']['id'], partial)
        self.assertEqual(view['items'][0]['selected_inputs'][0]['state'], 'partial')
        self.assertEqual(view['omitted']['receipts'], 4-len(view['items']))

    def test_query_index_and_paging_remain_derived_navigation(self):
        start = self.case.record('tool_start', {'tool': 'forsic_shell', 'arguments': {
            'action': 'triage_query', 'scan_id': 'c'*32, 'index_sha256': 'b'*64}}, 'current')
        next_call = {'tool': 'forsic_shell', 'arguments': {'action': 'triage_query',
            'scan_id': 'c'*32, 'index_sha256': 'b'*64, 'offset': 20}}
        triage_receipt(self.case, action='triage_query', started_id=start,
            candidate_query={'available': True, 'index_sha256': 'b'*64, 'index_truncated': True},
            selection={'index_truncated': True, 'page_truncated': True, 'next_call': next_call})
        view = self.view()
        item = view['derived_observations']['items'][0]
        self.assertTrue(item['candidate_query']['index_truncated'])
        self.assertEqual(item['selection']['next_call'], next_call)
        self.assertEqual(view['counts']['body_read'], 0)


if __name__ == '__main__':
    unittest.main()
