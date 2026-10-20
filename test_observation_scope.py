"""Offline observed-scope contracts: retained receipts only, no model/backend calls."""
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest

from forsic_plugin.evidence import Case
from forsic_plugin.observation_scope import observed_scope


class ObservationScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); evidence = root / 'evidence'; evidence.mkdir()
        manifest = root / 'case.json'
        manifest.write_text(json.dumps({'case_id': 'synthetic', 'evidence_root': str(evidence),
                                        'output_root': str(root / 'output')}))
        self.case = Case(manifest)
        self.resolved = {'case': Case(manifest, read_only=True), 'session_lineage': ['current', 'older']}

    def receipt(self, tool, *, args=None, session='current', **data):
        if args is not None:
            data['started_id'] = self.case.record('tool_start', {'tool': tool, 'arguments': args}, session)
        return self.case.record('tool_result', {'tool': tool, **data}, session)

    def targets(self, **kwargs):
        return observed_scope(self.resolved, **kwargs)['targets']

    def test_dynamic_inventory_historical_reads_and_foreign_sessions(self):
        for account in ('lunar-user', 'randomized-person-781'):
            with self.subTest(account=account):
                image = account + '.e01'
                job = '/settings/jobs/' + account
                listed = self.receipt('forsic_image_files', path=image, volume_offset=19, file_path='/settings/jobs',
                    representation='directory_inventory', content_not_measured=True, listing_complete=True,
                    entries=[{'path': job}, {'path': '/settings/jobs/ordinary'}])
                read = self.receipt('forsic_image_files', session='older', path=image, volume_offset=19,
                    file_path=job, complete_file=True, lines=[{'line': 1, 'text': 'synthetic record'}])
                for i in range(65):
                    self.receipt('forsic_read', path=account + str(i), lines=[], complete_file=True)
                self.receipt('forsic_image_files', session='foreign', path=image, volume_offset=19,
                    file_path='/settings/jobs/ordinary', lines=[], complete_file=True)
                rows = [t for t in self.targets(max_targets=300, max_chars=200000) if t['identity']['image'] == image]
                actual = {t['identity']['path']: t for t in rows}
                self.assertEqual(actual[job]['state'], 'body_read')
                self.assertEqual(actual['/settings/jobs/ordinary']['state'], 'listed')
                self.assertEqual(actual[job]['observed_states'], ['listed', 'body_read'])
                self.assertEqual({r['source_id'] for r in actual[job]['source_refs']}, {listed, read})

    def test_partial_search_errors_hashes_and_shell_are_not_completed_body_reads(self):
        self.receipt('forsic_read', path='partial', lines=[{'line': 2, 'text': 'fragment'}], complete_file=False)
        self.receipt('forsic_read_bytes', path='bytes', text='preview', base64='cHJldmlldw==',
                     complete_file=True, records_decoded=False)
        self.receipt('forsic_hash', path='hashed', complete_file=True, sha256='synthetic')
        self.receipt('forsic_search', args={'path': 'search-root'}, coverage_complete=True,
                     matches=[{'path': 'match', 'text': 'one matching line'}],
                     skips=[{'path': 'skip', 'reason': 'read_failed'}])
        failure = self.receipt('forsic_read', args={'path': 'failed'}, error='synthetic failure')
        start = self.case.event(failure)['data']['started_id']
        self.receipt('forsic_shell', stdout='fully read /shell-only', complete_file=True, path='shell-only')
        self.receipt('forsic_read', path='rejected', execution_rejected=True, error='bad argument')
        self.receipt('forsic_read', path='replayed', lines=[], complete_file=True, reused_from='foreign')
        view = observed_scope(self.resolved)
        actual = {t['identity']['path']: t for t in view['targets']}
        self.assertEqual(actual['partial']['state'], 'partial_body')
        self.assertEqual(actual['bytes']['state'], 'body_read')
        self.assertEqual(actual['hashed']['state'], 'listed')
        self.assertEqual(actual['match']['state'], 'partial_body')
        self.assertTrue(actual['match']['search_observed'])
        self.assertEqual(actual['search-root']['state'], 'listed')
        self.assertEqual(actual['skip']['state'], 'read_failed')
        self.assertEqual(actual['failed']['state'], 'read_failed')
        self.assertEqual(actual['failed']['source_refs'], [{'source_id': start, 'pointer': '/arguments/path'}])
        self.assertFalse({'shell-only', 'rejected', 'replayed'} & set(actual))
        self.assertEqual(view['excluded'], {'derived_shell_receipts': 1, 'reused_receipts': 1, 'rejected_receipts': 1})
        self.assertIn('not semantic analysis', ' '.join(view['limitations']))

    def test_image_volume_archive_ordinals_and_later_failure_preserve_history(self):
        for image, volume in (('first.e01', 0), ('first.e01', 50), ('second.e01', 0)):
            self.receipt('forsic_image_files', path=image, volume_offset=volume, file_path='/same',
                         lines=[], complete_file=(image == 'first.e01' and volume == 0))
        self.receipt('forsic_image_files', args={'path': 'first.e01', 'volume_offset': 0,
                     'file_path': '/same', 'action': 'read'}, error='later failure')
        self.receipt('forsic_image_files', path='first.e01', volume_offset=0, file_path='/bundle.tar',
                     archive_path='/bundle.tar', coverage_complete=False,
                     entries=[{'member_index': 0, 'name': 'duplicate'}, {'member_index': 1, 'name': 'duplicate'}])
        self.receipt('forsic_image_files', path='first.e01', volume_offset=0, file_path='/bundle.tar',
                     member={'member_index': 1, 'name': 'duplicate'}, complete_member=True, text='member bytes')
        self.receipt('forsic_image_files', args={'path': 'first.e01', 'volume_offset': 0,
                     'file_path': '/bundle.tar', 'action': 'archive_read', 'member_index': 1}, error='later archive failure')
        actual = self.targets()
        same = [t for t in actual if t['identity']['path'] == '/same']
        self.assertEqual(len(same), 3)
        full = next(t for t in same if t['identity']['image'] == 'first.e01' and t['identity']['volume_offset'] == 0)
        self.assertEqual(full['state'], 'body_read'); self.assertTrue(full['had_error'])
        self.assertIn('read_failed', full['observed_states'])
        members = {t['identity']['member_index']: t for t in actual if t['identity']['kind'] == 'archive_member'}
        self.assertEqual(len(members), 2)
        self.assertEqual(members[0]['state'], 'listed'); self.assertEqual(members[1]['state'], 'body_read')
        self.assertTrue(members[1]['had_error'])
        self.assertEqual(members[1]['identity']['member_name'], 'duplicate')

    def test_bounded_output_streams_large_history_and_preserves_counts(self):
        # Receipt bodies and target count substantially exceed the output budget.
        with self.case.connect() as db:
            for i in range(1500):
                data = json.dumps({'tool': 'forsic_read', 'path': 'target-' + str(i),
                                   'lines': [{'line': 1, 'text': 'x' * 12000}], 'complete_file': True})
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (str(i), i, 'tool_result', 'older', data))
        tracemalloc.start()
        try:
            view = observed_scope(self.resolved, max_targets=4, max_chars=2600)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(view['counts']['body_read'], 1500)
        self.assertEqual(view['omitted']['targets'], 1500 - len(view['targets']))
        self.assertLessEqual(len(view['targets']), 4)
        self.assertLessEqual(len(json.dumps(view, ensure_ascii=False, separators=(',', ':'))), 2600)
        self.assertLess(peak, 2 * 1024 * 1024)
        self.assertFalse(view['complete']); self.assertTrue(view['lineage_complete'])

    def test_ro_connection_no_mutation_and_limits(self):
        self.receipt('forsic_image_files', path='disk.e01', volumes=[{'offset': 0}, {'offset': 4096}])
        before = self.case.db.read_bytes()
        view = observed_scope(self.resolved)
        self.assertEqual(self.case.db.read_bytes(), before)
        volumes = [t['identity']['volume_offset'] for t in view['targets'] if t['identity']['kind'] == 'volume']
        self.assertEqual(volumes, [0, 4096])
        self.assertTrue(view['complete'])
        empty = observed_scope(self.resolved, max_targets=0)
        self.assertEqual(empty['targets'], []); self.assertEqual(empty['omitted']['targets'], 3)
        for kwargs in ({'max_targets': -1}, {'max_targets': True}, {'max_chars': 500}):
            with self.assertRaises(ValueError): observed_scope(self.resolved, **kwargs)
        with self.assertRaises(ValueError): observed_scope({'case': self.case, 'session_lineage': ['current']})

    def test_pages_oversized_identities_and_empty_complete_body(self):
        self.receipt('forsic_list', complete=False, files=[{'path': 'paged'}])
        self.receipt('forsic_read', path='paged', lines=[{'line': 1, 'text': 'first'}], complete_file=False, next_line=2)
        self.receipt('forsic_read', path='paged', lines=[{'line': 2, 'text': 'last'}], complete_file=False, next_line=None)
        self.receipt('forsic_read', path='empty', lines=[], complete_file=True)
        self.receipt('forsic_list', complete=True, files=[{'path': 'x' * 20000}])
        view = observed_scope(self.resolved, max_chars=6000)
        actual = {t['identity']['path']: t for t in view['targets']}
        self.assertEqual(actual['paged']['state'], 'partial_body')
        self.assertTrue(actual['paged']['partial_listing'])
        self.assertEqual(actual['empty']['state'], 'body_read')
        self.assertEqual(view['counts']['targets'], 3)
        self.assertEqual(view['omitted']['targets'], 1)
        self.assertFalse(view['complete'])


if __name__ == '__main__':
    unittest.main()
