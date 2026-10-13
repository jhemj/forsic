"""Offline direct-image-read entry contracts; no image parser, model, or network."""
import base64
from copy import deepcopy
import json
import socket
import unittest
from unittest.mock import patch

import test_efficiency_contracts as efficiency
from forsic_plugin.report_driven.host import current, find


class ImageReadEntryTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'socket.create_connection'):
            blocker = patch(target, side_effect=AssertionError('offline read-entry test forbids network'))
            blocker.start(); self.addCleanup(blocker.stop)
        self.x = efficiency.EfficiencyContracts()
        self.x.setUp()
        self.addCleanup(self.x.doCleanups)
        self.args = {'path': 'synthetic.e01', 'action': 'read',
                     'file_path': '/records/processes.txt', 'start_line': 81}
        self.lines = ['synthetic process row ' + str(number) for number in range(1, 201)]

    def call(self, args):
        return self.x.call('forsic_image_read', args)

    def body(self, args):
        # A bounded synthetic result for dispatch/source tests, not an E01 parser.
        first = args.get('start_line', 1)
        lines = [{'line': number, 'text': self.lines[number - 1]}
                 for number in range(first, min(first + 80, len(self.lines) + 1))]
        return {'path': args['path'], 'file_path': args['file_path'], 'lines': lines,
                'line_start': lines[0]['line'] if lines else None,
                'line_end': lines[-1]['line'] if lines else None,
                'next_line': first + len(lines) if first + len(lines) <= len(self.lines) else None,
                'complete_file': first == 1 and first + len(lines) > len(self.lines)}

    def plan(self):
        saved = self.x.call('forsic_mission', {**self.x.plan(),
            'tool_name': 'forsic_image_files', 'tool_arguments': deepcopy(self.args)})
        self.assertNotIn('error', saved, saved)
        return saved

    def assert_rejected(self, args):
        with patch.object(self.x.case, 'image_files') as backend:
            result = self.call(args)
            self.assertIn('error', result, result)
            self.assertEqual(result['error_code'], 'invalid_arguments')
            self.assertTrue(result['execution_rejected'])
            self.assertFalse(result['backend_started'])
            self.assertNotIn('아직 증거가 접수되지', result['error'])
            self.assertNotIn('outcome', result)
            start = self.x.case.event(result['started_id'])['data']
            self.assertEqual(start['tool'], 'forsic_image_files')
            self.assertEqual(start['submitted_tool'], 'forsic_image_read')
            self.assertEqual(start['submitted_arguments'], args)
            self.assertTrue(start['execution_rejected'])
            backend.assert_not_called()
            return result

    def test_explicit_pages_81_and_161_keep_canonical_source_and_submitted_arguments(self):
        with patch.object(self.x.case, 'image_files', side_effect=self.body) as backend:
            for first, last in ((81, 160), (161, 200)):
                args = {**self.args, 'start_line': first, 'reason': 'Read the selected continuation page'}
                original = deepcopy(args)
                result = self.call(args)
                self.assertNotIn('error', result, result)
                self.assertEqual(args, original)
                self.assertEqual(backend.call_args.args[0], args)
                self.assertEqual(result['lines'][0]['line'], first)
                self.assertEqual(result['lines'][-1]['line'], last)
                self.assertEqual(result['tool'], 'forsic_image_files')
                self.assertFalse(result['complete_file'])
                self.assertNotIn('mission_context', result)
                start = self.x.case.event(result['started_id'])['data']
                self.assertEqual(start['submitted_tool'], 'forsic_image_read')
                self.assertEqual(start['submitted_arguments'], args)
                self.assertEqual(start['arguments'], args)
                located = self.x.call('forsic_reporting', {'action': 'source',
                    'source_id': result['evidence_id'], 'query': self.lines[first - 1]})
                match = next(item for item in located['matches'] if item['role'] == 'body')
                self.assertNotIn('citation',match)
                retained_match = next(m for m in self.x.case.event(located['evidence_id'])['data']['matches'] if m['pointer']==match['pointer'] and m['byte_start']==match['byte_start'])
                self.assertEqual(retained_match['citation']['source_id'], result['evidence_id'])
                self.assertEqual(retained_match['citation']['literal'], self.lines[first - 1])
                source = find(current(self.x.case), 'sources', result['evidence_id'])
                self.assertIn('forsic_image_files', source['provenance'])
            self.assertEqual(backend.call_count, 2)

    def test_three_missing_file_paths_never_start_backend_or_borrow_a_draft(self):
        saved = self.plan()
        before = find(current(self.x.case), 'missions', saved['mission']['id'])
        for first in (81, 161, 241):
            result = self.assert_rejected({'path': 'synthetic.e01', 'action': 'read',
                'start_line': first, 'reason': 'Request page ' + str(first)})
            self.assertIn('file_path', result['missing_fields'])
        after = find(current(self.x.case), 'missions', saved['mission']['id'])
        self.assertEqual(after, before)
        self.assertEqual(after['state'], 'draft')

    def test_read_bytes_keeps_raw_source_and_compact_model_view(self):
        raw = b'one synthetic byte range\n'
        args = {'path': 'synthetic.e01', 'action': 'read_bytes',
                'file_path': '/records/processes.txt', 'offset': 27, 'length': len(raw), 'volume_offset': 4096}
        body = {'path': args['path'], 'file_path': args['file_path'],
                'text': raw.decode(), 'base64': base64.b64encode(raw).decode(),
                'byte_start': 27, 'byte_end': 27 + len(raw), 'next_offset': 27 + len(raw)}
        with patch.object(self.x.case, 'image_files', return_value=body) as backend:
            result = self.call(args)
            self.assertNotIn('error', result, result)
            backend.assert_called_once_with(args)
        self.assertEqual(result['tool'], 'forsic_image_files')
        self.assertNotIn('base64', result)
        self.assertEqual(result['omitted_fields'], ['base64'])
        retained = self.x.case.event(result['evidence_id'])['data']
        self.assertEqual(retained['base64'], body['base64'])
        self.assertEqual(result['source_ref']['id'], result['evidence_id'])
        self.assertEqual(find(current(self.x.case), 'sources', result['evidence_id'])['version'], result['source_ref']['version'])

    def test_explicit_mission_pair_uses_canonical_exact_plan_and_blocks_changed_scope(self):
        saved = self.plan()
        pair = {'mission_id': saved['mission']['id'], 'mission_version': saved['mission']['version']}
        wrong = {**self.args, **pair, 'start_line': 161}
        with patch.object(self.x.case, 'image_files') as backend:
            result = self.call(wrong)
            self.assertTrue(result['execution_rejected'], result)
            self.assertFalse(result['backend_started'])
            backend.assert_not_called()
        with patch.object(self.x.case, 'image_files', side_effect=self.body) as backend:
            result = self.call({**self.args, **pair})
            self.assertNotIn('error', result, result)
            backend.assert_called_once_with(self.args)
        self.assertEqual(result['mission_context']['tool_name'], 'forsic_image_files')
        mission = find(current(self.x.case), 'missions', saved['mission']['id'])
        self.assertEqual(mission['state'], 'unassessed')
        self.assertEqual(mission['result_ids'], [result['evidence_id']])

    def test_unbound_read_never_completes_matching_draft(self):
        saved = self.plan()
        with patch.object(self.x.case, 'image_files', side_effect=self.body):
            out = self.call(self.args)
        self.assertNotIn('error', out, out)
        self.assertNotIn('mission_context', out)
        self.assertNotIn('mission_context', self.x.case.event(out['started_id'])['data'])
        self.assertEqual(find(current(self.x.case), 'missions', saved['mission']['id'])['state'], 'draft')

    def test_required_values_types_actions_and_extra_fields_reject_with_receipts(self):
        bad = [{k: v for k, v in self.args.items() if k != field} for field in ('path', 'action', 'file_path')]
        bad += [{**self.args, field: value} for field in ('path', 'file_path') for value in ('', None, 3, True)]
        bad += [{**self.args, 'action': value} for value in ('list', 'volumes', 'stat', 'search', 'archive_read', 'from_mission', None)]
        bad += [{**self.args, field: value} for field, value in (
            ('length', True), ('start_line', '161'), ('offset', 1.5), ('volume_offset', False),
            ('member_index', 0), ('text', 'unrelated'), ('cursor', {}), ('_invented', 1))]
        for args in bad:
            with self.subTest(args=args): self.assert_rejected(args)
        for pair in ({'mission_id': 'M-unknown'}, {'mission_version': 'v1'},
                     {'mission_id': '', 'mission_version': ''}, {'mission_id': None, 'mission_version': 'v1'}):
            with self.subTest(pair=pair): self.assert_rejected({**self.args, **pair})

    def test_generic_list_volumes_and_legacy_direct_read_contracts_remain(self):
        with patch.object(self.x.case, 'image_files', return_value={'synthetic_backend': True}) as backend:
            for action in ('list', 'volumes'):
                args = {'path': 'synthetic.e01', 'action': action}
                result = self.x.call('forsic_image_files', args)
                self.assertNotIn('error', result, result)
                self.assertEqual(backend.call_args.args[0], args)
            # Old native transcripts remain executable through the canonical
            # handler, though read is no longer advertised on the generic entry.
            result = self.x.call('forsic_image_files', self.args)
            self.assertNotIn('error', result, result)
            self.assertEqual(backend.call_args.args[0], self.args)

    def test_visible_schemas_require_file_path_without_changing_physical_mission_identity(self):
        from tools.schema_sanitizer import sanitize_tool_schemas
        from forsic_plugin.notes import EVIDENCE_TOOLS
        def sanitized(name):
            return sanitize_tool_schemas([{'type': 'function', 'function':
                deepcopy(self.x.schemas[name])}])[0]['function']['parameters']
        entry = sanitized('forsic_image_read')
        self.assertEqual(set(entry['required']), {'path', 'action', 'file_path'})
        self.assertEqual(entry['properties']['action']['enum'], ['read', 'read_bytes'])
        self.assertFalse(entry['additionalProperties'])
        self.assertEqual(entry['properties']['file_path']['minLength'], 1)
        generic = sanitized('forsic_image_files')
        self.assertEqual(generic['required'], ['path', 'action'])
        self.assertNotIn('read', generic['properties']['action']['enum'])
        self.assertNotIn('read_bytes', generic['properties']['action']['enum'])
        self.assertTrue({'list', 'volumes'} <= set(generic['properties']['action']['enum']))
        mission = sanitized('forsic_mission')
        self.assertIn('forsic_image_read', mission['properties']['tool_name']['enum'])
        self.assertNotIn('forsic_image_read', EVIDENCE_TOOLS)
        branch = next(item for item in mission['properties']['tool_arguments']['anyOf'] if item['title'] == 'forsic_image_files')
        self.assertTrue({'read', 'read_bytes'} <= set(branch['properties']['action']['enum']))
        result = self.x.call('forsic_mission', {**self.x.plan(), 'tool_name': 'forsic_image_read', 'tool_arguments': self.args})
        self.assertNotIn('error', result)


if __name__ == '__main__':
    unittest.main()
