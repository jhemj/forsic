"""Offline image target reuse and guard checks; no Docker or evidence parser."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.evidence import Case
from forsic_plugin.image_reference import resolve


class ImageReferenceTests(unittest.TestCase):
    def setUp(self):
        from test_environment_support import offline_environment
        offline_environment(self)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name); root = base / 'evidence'; root.mkdir()
        self.disk = root / 'disk name.E01'; self.disk.write_bytes(b'synthetic image')
        (root / 'disk name.E02').write_bytes(b'synthetic second segment')
        (root / 'other.E01').write_bytes(b'other image')
        self.manifest = base / 'case.json'
        self.manifest.write_text(json.dumps({'case_id': 'synthetic', 'evidence_root': str(root), 'output_root': str(base / 'output')}))
        self.case = Case(self.manifest)

    def call(self, tool, args):
        return json.loads(self.case.invoke(tool, args, 'test-session'))

    def selected(self):
        result = self.call('forsic_list', {'path': '.'})
        self.assertNotIn('_image_targets', result)
        row = next(row for row in result['files'] if row['path'] == self.disk.name)
        return row['image_ref']

    def test_listing_ref_reuse_blank_path_and_submitted_effective_provenance(self):
        ref = self.selected()
        for path in (None, '', '\n', ' \t\r\n'):
            args = {'image_ref': ref, 'action': 'read', 'file_path': '/home/user/log'}
            if path is not None:args['path'] = path
            with self.subTest(path=path), patch('forsic_plugin.image_process.run_parser', return_value={'lines': [{'line': 1, 'text': 'synthetic'}]}) as parser:
                result = self.call('forsic_image_read', args)
                self.assertNotIn('error', result, result)
                self.assertEqual(result['path'], self.disk.name)
                start = self.case.event(result['started_id'])['data']
                self.assertEqual(start['submitted_arguments'], args)
                self.assertEqual(start['arguments']['path'], self.disk.name)
                self.assertNotIn('image_ref', start['arguments'])
                self.assertEqual(start['arguments']['file_path'], '/home/user/log')
                self.assertEqual(result['argument_resolution']['identity_strength'], 'metadata_checked')
                self.assertIn('image_ref', result)
                parser.assert_called_once()
        effective, _ = resolve(self.case, {'image_ref': result['image_ref'], 'action': 'volumes'})
        self.assertEqual(effective['path'], self.disk.name)

    def test_ref_search_and_normal_explicit_path_share_executor(self):
        ref = self.selected()
        for tool, args in [('forsic_image_files', {'path': self.disk.name, 'action': 'list'}),
                           ('forsic_image_search', {'image_ref': ref, 'file_path': '/', 'text': 'literal'})]:
            with patch('forsic_plugin.image_process.run_parser', return_value={'matches': []}) as parser:
                result = self.call(tool, args)
            self.assertNotIn('error', result, result); parser.assert_called_once()
            self.assertEqual(self.case.event(result['started_id'])['data']['arguments']['path'], self.disk.name)

    def test_bad_ref_conflicts_cross_case_stale_and_tampered_reject_without_backend(self):
        ref = self.selected()
        bad = [None, {}, {**ref, 'version': 'wrong'}, {**ref, 'id': 'other-case'},
               {**ref, 'index': 100}, {**ref, 'index': -1}, {**ref, 'index': True}, {**ref, 'extra': 1}]
        with patch.object(self.case, 'image_files') as backend:
            for value in bad:
                result = self.call('forsic_image_files', {'image_ref': value, 'action': 'list'})
                self.assertTrue(result['execution_rejected'], result)
            for path in (None, True, 'other.E01', 'n' + self.disk.name, '/home/user', ' \n' + self.disk.name + '\n'):
                result = self.call('forsic_image_files', {'image_ref': ref, 'path': path, 'action': 'list'})
                self.assertEqual(result['error_code'], 'image_reference_path_conflict', result)
            backend.assert_not_called()
        other_manifest = Path(self.temp.name) / 'other.json'
        config = json.loads(self.manifest.read_text()); config.update(case_id='other', output_root=str(Path(self.temp.name) / 'other-output'))
        other_manifest.write_text(json.dumps(config))
        other = Case(other_manifest)
        with self.assertRaises(ValueError):resolve(other, {'image_ref': ref})
        with self.case.connect() as db:
            row = db.execute('SELECT data FROM events WHERE id=?', (ref['id'],)).fetchone()
            data = json.loads(row['data']); data['complete'] = False
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(data), ref['id']))
        with self.assertRaises(ValueError):resolve(self.case, {'image_ref': ref})

    def test_metadata_segment_root_and_scope_changes_invalidate_reference(self):
        ref = self.selected()
        (self.disk.parent / 'disk name.E02').write_bytes(b'changed second segment')
        result = self.call('forsic_image_files', {'image_ref': ref, 'action': 'list'})
        self.assertEqual(result['error_code'], 'image_reference_identity_conflict')
        ref = self.selected(); self.disk.write_bytes(b'changed first segment')
        with self.assertRaises(ValueError):resolve(self.case, {'image_ref': ref})
        ref = self.selected(); self.case.config['selected_files'] = ['other.E01']
        with self.assertRaises(ValueError):resolve(self.case, {'image_ref': ref})
        self.case.config['selected_files'] = []
        ref = self.selected(); other = self.disk.parent / 'sub'; other.mkdir(); self.case.root = other
        with self.assertRaises(ValueError):resolve(self.case, {'image_ref': ref})

    def test_ambiguous_or_mismatched_retained_origin_is_rejected(self):
        ref = self.selected(); event = self.case.event(ref['id'])
        self.case.record('tool_result', event['data'], 'test-session')
        with self.assertRaises(ValueError):resolve(self.case, {'image_ref': ref})
        ref = self.selected(); event = self.case.event(ref['id'])
        with self.case.connect() as db:
            db.execute('UPDATE events SET session=? WHERE id=?', ('wrong-session', event['data']['started_id']))
        with self.assertRaises(ValueError):resolve(self.case, {'image_ref': ref})

    def test_ascii_trim_only_when_exact_missing_and_preserve_real_spaces(self):
        args = {'path': ' \t' + self.disk.name + '\n', 'action': 'list'}
        with patch('forsic_plugin.image_process.run_parser', return_value={'entries': []}):
            result = self.call('forsic_image_files', args)
        self.assertNotIn('error', result, result)
        start = self.case.event(result['started_id'])['data']
        self.assertEqual(start['submitted_arguments'], args)
        self.assertEqual(start['arguments']['path'], self.disk.name)
        self.assertEqual(result['argument_resolution']['kind'], 'image_path_ascii_whitespace')
        spaced = self.disk.parent / (' ' + self.disk.name + ' '); spaced.write_bytes(b'exact spaced name')
        exact, resolution = resolve(self.case, {'path': spaced.name})
        self.assertEqual(exact['path'], spaced.name); self.assertIsNone(resolution)
        for path in ('n' + self.disk.name, 'x' + self.disk.name, '\u00a0' + self.disk.name):
            exact, resolution = resolve(self.case, {'path': path})
            self.assertEqual(exact['path'], path); self.assertIsNone(resolution)
        exact, resolution = resolve(self.case, args, allow_trim=False)
        self.assertEqual(exact, args); self.assertIsNone(resolution)
        self.case.config['selected_files'] = [self.disk.name]
        exact, resolution = resolve(self.case, args)
        self.assertEqual(exact['path'], self.disk.name)
        self.case.config['selected_files'] = []
        leading = self.disk.parent / (' ' + self.disk.name); leading.write_bytes(b'leading-space image')
        with patch('forsic_plugin.image_process.run_parser', return_value={'entries': []}):
            result = self.call('forsic_image_files', {'path': leading.name, 'action': 'list'})
        self.assertNotIn('error', result, result)
        self.assertEqual(result['path'], leading.name)

    def test_blank_or_internal_image_target_rejected_before_docker_and_errors_have_no_ref(self):
        with patch('forsic_plugin.image_process.run_parser') as parser:
            for path in ('', '\n', ' \r\n', '/home/user', '.', 'n' + self.disk.name):
                result = self.call('forsic_image_files', {'path': path, 'action': 'list'})
                self.assertIn('error', result, result); self.assertNotIn('image_ref', result)
            parser.assert_not_called()
        with patch('forsic_plugin.image_process.run_parser', return_value={'error': 'missing internal file', 'error_type': 'FileNotFoundError'}):
            result = self.call('forsic_image_files', {'path': self.disk.name, 'action': 'stat', 'file_path': '/missing'})
        self.assertNotIn('image_ref', result)
        with patch.object(self.case, 'image_files', return_value={'lines': []}):
            result = self.call('forsic_image_files', {'path': 'nonexistent.E01', 'action': 'list'})
        self.assertNotIn('image_ref', result)

    def test_host_binding_overwrites_parser_claims_and_discards_mid_call_changes(self):
        with patch('forsic_plugin.image_process.run_parser', return_value={'entries': [], '_image_targets': [{'path': 'fake.E01'}]}):
            result = self.call('forsic_image_files', {'path': self.disk.name, 'action': 'list'})
        effective, _ = resolve(self.case, {'image_ref': result['image_ref']})
        self.assertEqual(effective['path'], self.disk.name)
        def changed(*args, **kwargs):
            self.disk.write_bytes(b'mid-call change')
            return {'entries': []}
        with patch('forsic_plugin.image_process.run_parser', side_effect=changed):
            result = self.call('forsic_image_files', {'path': self.disk.name, 'action': 'list'})
        self.assertIn('changed during inspection', result['error']); self.assertNotIn('image_ref', result)
        second = self.disk.parent / 'disk name.E02'; second.unlink(); second.symlink_to(self.disk)
        listed = self.call('forsic_list', {'path': '.'})
        self.assertNotIn('error', listed, listed)
        row = next(row for row in listed['files'] if row['path'] == self.disk.name)
        self.assertIn('image_selection_error', row); self.assertNotIn('image_ref', row)

    def test_canonical_plans_still_require_exact_path(self):
        from forsic_plugin.image_reader import image_read_entry_schema, image_search_entry_schema
        for contract in (image_read_entry_schema(), image_search_entry_schema()):
            self.assertIn('path', contract['required']); self.assertNotIn('image_ref', contract['properties'])
        import test_efficiency_contracts as fixtures
        x = fixtures.EfficiencyContracts(); x.setUp(); self.addCleanup(x.doCleanups)
        from jsonschema import Draft202012Validator
        ref = self.selected()
        for tool, args in [('forsic_image_files', {'action':'list'}),
                           ('forsic_image_read', {'action':'read','file_path':'/body'}),
                           ('forsic_image_search', {'file_path':'/body','text':'literal'})]:
            validator = Draft202012Validator(x.schemas[tool]['parameters'])
            self.assertTrue(list(validator.iter_errors(args)), tool)
            self.assertFalse(list(validator.iter_errors({**args,'path':self.disk.name})), tool)
            self.assertFalse(list(validator.iter_errors({**args,'image_ref':ref})), tool)
            self.assertTrue(list(validator.iter_errors({'image_ref':ref})), tool)
        raw = ' \nmissing.E01\n'
        planned = x.call('forsic_mission', {**x.plan(), 'tool_name': 'forsic_image_read',
            'tool_arguments': {'path': raw, 'action': 'read', 'file_path': '/body'}})
        self.assertNotIn('error', planned, planned)
        with patch.object(x.case, 'image_files', return_value={'lines': []}) as backend:
            result = x.call(planned['next_call']['tool'], planned['next_call']['arguments'])
        self.assertNotIn('error', result, result)
        self.assertEqual(backend.call_args.args[0]['path'], raw)


if __name__ == '__main__':unittest.main()
