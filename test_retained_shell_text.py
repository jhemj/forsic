"""Offline readable projections of retained shell bytes; no command execution."""
import base64
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.assessment_basis import _source_choices
from forsic_plugin.report_driven.model_pages import MAX_BYTES, size
from forsic_plugin.report_driven.source_handles import SourceHandleError, source_handle
import test_shell_integration as integration


class RetainedShellTextTests(unittest.TestCase):
    call = integration.ShellIntegrationTests.call
    output = integration.ShellIntegrationTests.output

    def setUp(self):
        isolated = tempfile.TemporaryDirectory()
        self.addCleanup(isolated.cleanup)
        for name in ('get_hermes_home', 'get_default_hermes_root'):
            mock = patch('hermes_constants.' + name, return_value=Path(isolated.name))
            mock.start()
            self.addCleanup(mock.stop)
        integration.ShellIntegrationTests.setUp(self)

    def retain(self, raw, stderr=b'', **changes):
        output = self.output(raw[:100].decode('utf-8', errors='replace'), **changes)
        output['raw_output'].update(
            stdout_base64=base64.b64encode(raw).decode(),
            stderr_base64=base64.b64encode(stderr).decode(),
            stdout_bytes=len(raw), stderr_bytes=len(stderr), retained_bytes=len(raw) + len(stderr),
            stdout_sha256=hashlib.sha256(raw).hexdigest())
        output['preview_truncated'] = {'stdout': len(raw) > 100, 'stderr': bool(stderr)}
        with patch('forsic_plugin.shell_workspace.invoke', return_value=output):
            result = self.call('forsic_shell', {'command': 'synthetic retained output'})
        self.assertNotIn('error', result, result)
        return result

    def read(self, call):
        with patch('forsic_plugin.shell_workspace.invoke', side_effect=AssertionError('No reexecution')):
            result = self.call('forsic_reporting', call)
        self.assertNotIn('error', result, result)
        self.assertNotIn('citation_ref_unavailable', result, result)
        self.assertLessEqual(size(result), MAX_BYTES)
        return result

    def test_large_output_continues_as_text_with_exact_decoded_offsets(self):
        raw = b'prefix\n' + b'A' * 8185 + b'READABLE TAIL\n' + b'B' * 9000
        shown = self.retain(raw)
        saved = deepcopy(self.case.event(shown['evidence_id']))
        call = {**shown['readable_output_calls']['stdout']['arguments'], 'offset': 8192, 'limit': 301}
        chunks, offset = [], 8192
        while call:
            page = self.read(call)
            self.assertEqual(page['status'], 'readable')
            self.assertEqual(page['decoded_byte_start'], offset)
            self.assertEqual(page['decoded_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(page['decoded_text'].encode(), raw[offset:page['decoded_byte_end']])
            self.assertTrue(page['derived'])
            self.assertFalse(page['original_evidence'])
            self.assertNotIn('citation_ref', page)
            self.assertNotIn('text', page)
            self.assertNotIn('byte_start', page)
            self.assertNotIn('full_field', page)
            chunks.append(page['decoded_text'])
            offset = page['decoded_byte_end']
            call = page['next_call']
            if call:
                self.assertEqual(call['representation'], 'shell_utf8')
                self.assertEqual(call['source_handle'], shown['source_handle'])
                self.assertEqual(call['offset'], offset)
                self.assertEqual(call['limit'], 301)
        self.assertEqual(''.join(chunks).encode(), raw[8192:])
        self.assertTrue(chunks[0].startswith('READABLE TAIL'))
        self.assertEqual(self.case.event(shown['evidence_id']), saved)

    def test_multibyte_boundaries_escape_budget_and_empty_stream(self):
        raw = ('가🙂\n"\\\t' * 1900).encode()
        shown = self.retain(raw)
        call = {**shown['readable_output_calls']['stdout']['arguments'], 'limit': 139}
        chunks = []
        while call:
            page = self.read(call)
            self.assertGreater(page['decoded_byte_end'], page['decoded_byte_start'])
            self.assertLessEqual(page['decoded_byte_end'] - page['decoded_byte_start'], 139)
            chunks.append(page['decoded_text'])
            call = page['next_call']
        self.assertEqual(''.join(chunks).encode(), raw)
        empty = self.read(shown['readable_output_calls']['stderr']['arguments'])
        self.assertEqual(empty['decoded_text'], '')
        self.assertTrue(empty['full_decoded_stream'])
        self.assertIsNone(empty['next_call'])
        # JSON escaping can exceed the input byte count; the serialized page stays bounded.
        escaped = self.retain(b'"\\' * 10000)
        call = escaped['readable_output_calls']['stdout']['arguments']
        parts = []
        while call:
            page = self.read(call)
            parts.append(page['decoded_text'])
            call = page['next_call']
        self.assertEqual(''.join(parts).encode(), b'"\\' * 10000)

    def test_utf8_boundary_small_limit_and_range_errors(self):
        shown = self.retain('가🙂'.encode())
        call = shown['readable_output_calls']['stdout']['arguments']
        for changes in ({'offset': 1}, {'offset': 8}, {'limit': 2}, {'offset': -1},
                        {'offset': True}, {'limit': 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                host.invoke(self.case, {**call, **changes})
        end = self.read({**call, 'offset': 7})
        self.assertEqual(end['decoded_text'], '')
        self.assertIsNone(end['next_call'])

    def test_invalid_utf8_and_binary_controls_are_explicit_and_preserve_bytes(self):
        for raw, status in ((b'valid prefix\xfftail', 'not_utf8'),
                            (b'valid prefix\x00tail', 'binary_controls'),
                            (b'\x1b[31mred', 'binary_controls')):
            with self.subTest(raw=raw):
                shown = self.retain(raw)
                page = self.read(shown['readable_output_calls']['stdout']['arguments'])
                self.assertEqual(page['status'], status)
                self.assertNotIn('decoded_text', page)
                self.assertNotIn('citation_ref', page)
                self.assertIsNone(page['next_call'])
                exact = self.read(page['raw_read_call'])
                self.assertEqual(base64.b64decode(exact['text']), raw)
                self.assertEqual(page['decoded_sha256'], hashlib.sha256(raw).hexdigest())
                if status == 'not_utf8':
                    self.assertEqual(page['invalid_byte_start'], 12)

    def test_legacy_base64_and_preview_scalar_semantics_remain_exact(self):
        raw = ('same preview\n' * 1000).encode()
        shown = self.retain(raw)
        legacy_call = shown['raw_output_calls']['stdout']['arguments']
        self.assertNotIn('representation', legacy_call)
        page = self.read({**legacy_call, 'offset': 8192, 'limit': 173})
        encoded = base64.b64encode(raw).decode()
        self.assertEqual(page['text'], encoded[8192:page['byte_end']])
        self.assertEqual(page['byte_start'], 8192)
        self.assertEqual(page['coordinate_basis'], 'UTF-8 bytes of the retained scalar field')
        self.assertIn('citation_ref', page)
        preview = self.read({'action': 'source', 'source_handle': shown['source_handle'], 'pointer': '/stdout'})
        self.assertEqual(preview['text'], shown['stdout'])
        self.assertIn('citation_ref', preview)

    def test_version_case_binding_and_selected_source_restrictions(self):
        shown = self.retain(b'original')
        call = shown['readable_output_calls']['stdout']['arguments']
        source = host.source_record(self.case.event(shown['evidence_id']))
        for changes in ({'source_version': 'stale'}, {'source_id': 'different'}):
            with self.subTest(changes=changes), self.assertRaises(SourceHandleError):
                host.invoke(self.case, {**call, **changes})
        legacy = {key: value for key, value in call.items() if key != 'source_handle'}
        for version in (None, 'stale'):
            args = {**legacy, 'source_id': source['id']}
            if version is not None:
                args['source_version'] = version
            with self.assertRaises(ValueError):
                host.invoke(self.case, args)
        exact = self.read({**legacy, 'source_id': source['id'], 'source_version': source['version']})
        self.assertEqual(exact['decoded_text'], 'original')
        with patch.object(self.case, 'config', {**self.case.config, 'case_id': 'other-case'}):
            with self.assertRaises(SourceHandleError):
                host.invoke(self.case, call)
        with tempfile.TemporaryDirectory() as other_root:
            other, _, _ = integration.fixture(other_root)
            with self.assertRaises(SourceHandleError):
                host.invoke(other, call)
        for changes in ({'pointer': '/stdout'}, {'query': 'original'}, {'representation': 'unknown'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                host.invoke(self.case, {**call, **changes})
        changed = deepcopy(self.case.event(source['id']))
        changed['data']['raw_output']['stdout_base64'] = base64.b64encode(b'changed').decode()
        with patch.object(self.case, 'event', return_value=changed), self.assertRaises(ValueError):
            host.invoke(self.case, call)

    def test_stderr_and_limit_cap_are_independent_of_stdout_preview(self):
        shown = self.retain(b'stdout only', stderr=('오류 detail\n' * 1000).encode())
        call = {**shown['readable_output_calls']['stderr']['arguments'], 'limit': 100000}
        page = self.read(call)
        self.assertEqual(page['pointer'], '/raw_output/stderr_base64')
        self.assertTrue(page['decoded_text'].startswith('오류 detail'))
        self.assertLessEqual(page['decoded_byte_end'], 4096)
        self.assertEqual(page['next_call']['limit'], 4096)
        self.assertNotIn('stdout only', page['decoded_text'])
        ascii_shown = self.retain(b'A' * 100000)
        ascii_page = self.read(ascii_shown['readable_output_calls']['stdout']['arguments'])
        self.assertEqual(ascii_page['decoded_text'], 'A' * ascii_page['decoded_byte_end'])

    def test_registered_schema_and_non_source_action_guard(self):
        from jsonschema import Draft202012Validator
        schema = self.schemas['forsic_reporting']['parameters']
        shown = self.retain(b'valid text')
        Draft202012Validator(schema).validate(shown['readable_output_calls']['stdout']['arguments'])
        for action in ('state', 'gaps', 'case'):
            with self.subTest(action=action):
                rejected = self.call('forsic_reporting', {'action': action, 'representation': 'shell_utf8'})
                self.assertIn('error', rejected)
                self.assertIn('representation', rejected['error'])

    def test_invalid_base64_and_non_shell_sources_are_rejected(self):
        for tool, encoded in (('forsic_shell', 'not valid base64!'),
                              ('forsic_read', base64.b64encode(b'read').decode())):
            data = {'tool': tool, 'raw_output': {'stdout_base64': encoded}}
            sid = self.case.record('tool_result', data, 'synthetic')
            source = host.source_record(self.case.event(sid))
            call = {'action': 'source', 'source_handle': source_handle(self.case, source),
                    'pointer': '/raw_output/stdout_base64', 'representation': 'shell_utf8'}
            with self.subTest(tool=tool), self.assertRaises(ValueError):
                host.invoke(self.case, call)

    def test_read_only_projection_capture_limits_and_receipt_does_not_forge_citation(self):
        shown = self.retain(b'partial retained output', raw_output={
            'complete': False, 'truncated': True, 'limit_bytes': 23})
        call = shown['readable_output_calls']['stdout']['arguments']
        before = self.case.db.read_bytes()
        with patch('socket.socket', side_effect=AssertionError('No network')):
            page = host.invoke(self.case, call)
        self.assertEqual(self.case.db.read_bytes(), before)
        self.assertEqual(page['capture'], {'complete': False, 'truncated': True, 'limit_bytes': 23})
        self.assertTrue(page['full_decoded_stream'])
        result = self.read(call)
        expected, choices = _source_choices(self.case, None, result['evidence_id'])
        self.assertEqual(choices, [])
        self.assertEqual(expected['decoded_text'], 'partial retained output')
        self.assertNotIn('citation_ref', result)
        # A decoded projection cannot be silently relabelled as a scalar page.
        receipt = self.case.event(result['evidence_id'])
        receipt['data'].update(text=expected['decoded_text'], byte_start=0)
        actual_event = self.case.event
        with patch.object(self.case, 'event', side_effect=lambda eid:
                          receipt if eid == result['evidence_id'] else actual_event(eid)):
            with self.assertRaises(ValueError):
                _source_choices(self.case, None, result['evidence_id'])


if __name__ == '__main__':
    unittest.main()
