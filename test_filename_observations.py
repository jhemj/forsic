"""Offline bounded name observations; no original files, images or models."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.evidence import Case

from forsic_plugin.filename_observations import observe_listing


class FilenameObservationTests(unittest.TestCase):
    def setUp(self):
        from test_environment_support import offline_environment
        offline_environment(self)

    def test_controls_keep_original_coordinates_and_safe_display_without_verdict(self):
        raw = '/home/보고서\u202eexe.txt'
        listing = {'path': 'disk.E01', 'file_path': '/home', 'volume_offset': 4096,
                   'entries': [{'path': raw, 'inode': 42}]}
        before = deepcopy(listing)
        result = observe_listing(listing, source_ref={'id': 'retained-source', 'version': 'v1'})
        self.assertEqual(listing, before)
        observation = result['observations'][0]
        self.assertEqual(observation['raw_name'], raw)
        self.assertEqual(json.loads(observation['escaped_display']), raw)
        self.assertNotIn('\u202e', observation['escaped_display'])
        self.assertIn('\\u202e', observation['escaped_display'])
        self.assertEqual(observation['controls'][0]['index'], raw.index('\u202e'))
        self.assertEqual(observation['controls'][0]['codepoint'], 'U+202E')
        self.assertEqual(observation['field_pointer'], '/entries/0/path')
        self.assertEqual(observation['locator']['volume_offset'], 4096)
        self.assertEqual(observation['locator']['image_path'], 'disk.E01')
        self.assertEqual(observation['locator']['path'], raw)
        self.assertEqual(observation['source_ref']['id'], 'retained-source')
        self.assertNotIn('confidence', observation)
        self.assertFalse(result['scope']['whole_image_inspected'])

    def test_ordinary_scripts_accents_and_joiners_are_not_observations(self):
        names = ['한국어.txt', 'العربية.txt', 'עברית.txt', 'cafe\u0301.txt', '👩\u200d💻.png', 'نامه\u200cها.txt']
        result = observe_listing({'files': [{'path': name} for name in names]})
        self.assertEqual(result['observations'], [])
        self.assertEqual(result['scope']['inspected_entries'], len(names))
        self.assertEqual(result['status'], 'inspected_returned_names')
        self.assertEqual(result['scope']['unreturned_entries'], 'unknown')

    def test_archive_members_keep_duplicate_name_identity_and_stable_dedup(self):
        listing = {'path': 'disk.E01', 'volume_offset': 10, 'file_path': '/a.tar',
                   'entries': [{'name': 'a\u200b.txt', 'member_index': index, 'header_offset': index * 512}
                               for index in (0, 1, 1)]}
        first = observe_listing(listing)
        self.assertEqual(len(first['observations']), 2)
        self.assertEqual(first, observe_listing(deepcopy(listing)))
        ids = [o['id'] for o in first['observations']]
        other = observe_listing({**listing, 'path': 'other.E01'})
        self.assertTrue(set(ids).isdisjoint(o['id'] for o in other['observations']))
        self.assertEqual(first['observations'][1]['locator']['member_index'], 1)
        self.assertEqual(ids, [o['id'] for o in observe_listing(listing, source_ref={'id': 'another-receipt'})['observations']])

    def test_entry_and_character_limits_are_explicit_and_do_not_scan_suffix(self):
        listing = {'entries': [{'path': 'normal'}, {'path': 'long\u202ename'}, {'path': 'a\u202e'}]}
        result = observe_listing(listing, max_entries=2, max_characters=6)
        self.assertEqual(result['observations'], [])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['scope']['inspected_entries'], 1)
        self.assertEqual(result['scope']['uninspected_entries'], 2)
        self.assertEqual(result['scope']['character_budget_skipped_entries'], 1)
        self.assertEqual(result['scope']['entry_budget_skipped_entries'], 1)
        self.assertEqual(listing['entries'][1]['path'], 'long\u202ename')

    def test_missing_disabled_malformed_and_empty_results_do_not_claim_clean(self):
        self.assertEqual(observe_listing({})['status'], 'not_run')
        self.assertEqual(observe_listing({'files': [{'path': '\u202e'}]}, max_entries=0)['status'], 'not_run')
        result = observe_listing({'entries': [None, {'path': 42}, {'name': '\u2066x\u2069'}]})
        self.assertEqual(result['scope']['invalid_name_entries'], 2)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['observations'][0]['control_count'], 2)
        self.assertEqual(observe_listing({'files': []})['status'], 'inspected_returned_names')
        disabled = observe_listing({'files': [{'path': ''}]}, max_characters=0)
        self.assertEqual(disabled['status'], 'not_run')
        self.assertEqual(disabled['scope']['inspected_entries'], 0)

    def test_lro_marks_isolates_and_invisibles_remain_observations_only(self):
        controls = '\u202d\u202c\u200e\u061c\u2067\u2069\u2060\ufeff\u00ad\n'
        result = observe_listing({'files': [{'path': 'name' + controls + '.txt'}]})
        observation = result['observations'][0]
        self.assertEqual(observation['control_count'], len(controls))
        self.assertEqual({c['codepoint'] for c in observation['controls']},
                         {f'U+{ord(c):04X}' for c in controls})
        self.assertNotIn('malicious', observation)
        self.assertIn('not established', observation['meaning'])

    def test_control_position_output_and_parameters_are_bounded(self):
        result = observe_listing({'entries': [{'path': '\u202e' * 100}]})
        observation = result['observations'][0]
        self.assertEqual(len(observation['controls']), 64)
        self.assertEqual(observation['control_count'], 100)
        self.assertEqual(observation['omitted_control_positions'], 36)
        for kwargs in ({'max_entries': True}, {'max_entries': 101}, {'max_characters': 32769}, {'max_characters': -1}):
            with self.assertRaises(ValueError):
                observe_listing({}, **kwargs)


    def test_large_origin_and_unselected_name_are_bounded_without_identity_collision(self):
        origin = 'x' * 100000
        listing = {'path': origin, 'entries': [{'path': 'a\u202e', 'name': origin, 'inode': i} for i in range(100)]}
        original = deepcopy(listing)
        result = observe_listing(listing, max_characters=200)
        self.assertLessEqual(len(json.dumps(result).encode()), 65536)
        self.assertEqual(result['scope']['inspected_characters'], 200)
        self.assertGreater(result['scope']['omitted_observations'], 0)
        observation = result['observations'][0]
        self.assertEqual(observation['locator']['listing_path']['field_pointer'], '/path')
        self.assertEqual(observation['locator']['name']['field_pointer'], '/entries/0/name')
        self.assertEqual(observation['locator']['name']['characters'], len(origin))
        self.assertTrue(observation['locator']['name']['omitted'])
        self.assertEqual(listing, original)
        changed = observe_listing({**listing, 'path': origin + 'different'})
        self.assertNotEqual(observation['id'], changed['observations'][0]['id'])
        self.assertEqual(observation['id'], observe_listing(listing, max_entries=1)['observations'][0]['id'])
        row_changed = {'path': origin, 'entries': [{'path': 'a\u202e', 'name': origin + 'different', 'inode': 0}]}
        self.assertNotEqual(observation['id'], observe_listing(row_changed)['observations'][0]['id'])

    def test_large_raw_name_and_escaped_controls_cannot_exceed_added_json_budget(self):
        listing = {'entries': [{'path': '\u202e' * 32768}]}
        result = observe_listing(listing)
        self.assertLessEqual(len(json.dumps(result).encode()), 65536)
        self.assertEqual(result['observations'], [])
        self.assertEqual(result['scope']['inspected_characters'], 32768)
        self.assertEqual(result['scope']['omitted_observations'], 1)
        self.assertEqual(result['scope']['observation_omission_reasons'], {'output_byte_budget': 1})
        self.assertEqual(result['status'], 'partial')
        result = observe_listing({'entries': [{'path': 'a\u202e'}]}, source_ref={'id': 's' * 100000, 'version': 'v1'})
        self.assertLessEqual(len(json.dumps(result).encode()), 65536)
        self.assertTrue(result['observations'][0]['source_ref']['id']['omitted'])
        with self.assertRaises(ValueError):
            observe_listing({}, source_ref={'id': {'nested': 'unbounded'}})

    def test_case_listing_retains_original_observation_coordinates_and_read_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); evidence = base / 'evidence'; evidence.mkdir()
            name = 'report\u202eexe.txt'
            (evidence / name).write_text('ordinary body\n')
            manifest = base / 'case.json'
            manifest.write_text(json.dumps({'evidence_root': str(evidence), 'output_root': str(base / 'results')}))
            case = Case(manifest)
            listing = json.loads(case.invoke('forsic_list', {'path': '.'}, 'synthetic'))
            self.assertNotIn('error', listing, listing)
            retained = case.event(listing['evidence_id'])['data']
            observation = retained['filename_observations']['observations'][0]
            self.assertEqual(retained['files'][0]['path'], name)
            self.assertEqual(observation['raw_name'], name)
            self.assertEqual(observation['field_pointer'], '/files/0/path')
            self.assertEqual(listing['filename_observations'], retained['filename_observations'])
            # The guard must attach only to listing returns, even for a read of
            # the same path containing a display control.
            with patch('forsic_plugin.filename_observations.observe_listing', side_effect=AssertionError('read must not inspect names')):
                read = json.loads(case.invoke('forsic_read', {'path': name}, 'synthetic'))
            self.assertNotIn('error', read, read)
            self.assertNotIn('filename_observations', read)
            self.assertNotIn('filename_observations', case.event(read['evidence_id'])['data'])
            self.assertEqual(read['lines'][0]['text'], 'ordinary body')
            (evidence / 'synthetic.E01').write_bytes(b'synthetic-image-only')
            for action, row in (('list', {'path': '/dir/' + name, 'inode': 7}),
                                ('archive_list', {'name': name, 'member_index': 4, 'header_offset': 2048})):
                with self.subTest(action=action), patch('forsic_plugin.image_process.run_parser', return_value={
                        'entries': [row], 'volume_offset': 4096, 'volume_selection': 'explicit', 'listing_complete': True}):
                    result = json.loads(case.invoke('forsic_image_files', {
                        'path': 'synthetic.E01', 'action': action, 'file_path': '/dir' if action == 'list' else '/bundle.tar',
                        'volume_offset': 4096}, 'synthetic'))
                self.assertNotIn('error', result, result)
                retained = case.event(result['evidence_id'])['data']
                observation = retained['filename_observations']['observations'][0]
                key = 'path' if action == 'list' else 'name'
                self.assertEqual(observation['field_pointer'], '/entries/0/' + key)
                self.assertEqual(observation['raw_name'], retained['entries'][0][key])
                self.assertEqual(observation['locator']['image_path'], 'synthetic.E01')
                self.assertEqual(observation['locator']['volume_offset'], 4096)
                self.assertEqual(result['filename_observations'], retained['filename_observations'])
            huge_name = '\u202e' * 32768
            with patch('forsic_plugin.image_process.run_parser', return_value={
                    'entries': [{'name': huge_name, 'member_index': 5, 'header_offset': 2560}],
                    'volume_offset': 4096, 'volume_selection': 'explicit'}):
                result = json.loads(case.invoke('forsic_image_files', {'path': 'synthetic.E01',
                    'action': 'archive_list', 'file_path': '/bundle.tar', 'volume_offset': 4096}, 'synthetic'))
            self.assertNotIn('error', result, result)
            retained = case.event(result['evidence_id'])['data']
            self.assertEqual(retained['entries'][0]['name'], huge_name)
            added = retained['filename_observations']
            self.assertEqual(added['observations'], [])
            self.assertEqual(added['scope']['omitted_observations'], 1)
            self.assertLessEqual(len(json.dumps(added).encode()), 65536)


if __name__ == '__main__':
    unittest.main()
