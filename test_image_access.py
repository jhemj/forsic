"""Offline shared E01/CLI/export contracts with fake Dissect and synthetic bytes."""
import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forsic_plugin import image_access as access
from forsic_plugin.image_reader import image_segments, inspect, open_image


class Node:
    def __init__(self, path):
        self.local = path
        self.path = '/synthetic.bin'

    def lstat(self):
        return self.local.lstat()

    def stat(self):
        return self.local.stat()

    def open(self):
        return self.local.open('rb')


class ImageAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / 'synthetic.e01'
        self.segment(self.image, 1)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.payload = self.root / 'payload'
        self.payload.write_bytes(b'alpha\nbeta\n')
        self.node = Node(self.payload)
        self.volumes = [SimpleNamespace(offset=4096, size=12345, name='synthetic')]
        self.handles = []
        self.disk_closed = False
        target = SimpleNamespace(disks=SimpleNamespace(add=lambda _: None, apply=lambda: None), volumes=self.volumes)

        def container(handles):
            self.handles = list(handles)
            return SimpleNamespace(close=lambda: setattr(self, 'disk_closed', True))

        modules = {'dissect.target': SimpleNamespace(Target=lambda _: target),
                   'dissect.target.containers.ewf': SimpleNamespace(EwfContainer=container),
                   'dissect.target.filesystem': SimpleNamespace(open=lambda _: SimpleNamespace(get=lambda _: self.node))}
        stub = patch.dict(sys.modules, modules)
        stub.start()
        self.addCleanup(stub.stop)
        for name in ('socket.socket.connect', 'socket.create_connection'):
            guard = patch(name, side_effect=AssertionError('offline test forbids network'))
            guard.start()
            self.addCleanup(guard.stop)

    @staticmethod
    def segment(path, ordinal):
        path.write_bytes(b'EVF\x09\x0d\x0a\xff\x00' + b'\x00' + ordinal.to_bytes(2, 'little') + b'\x00\x00')

    def args(self, action='read', **extra):
        return {'path': str(self.image), 'action': action, 'file_path': self.node.path, **extra}

    def export(self, destination=None, **extra):
        return access.export_file(self.image, self.args('export', destination=str(destination or self.work/'new.bin'), **extra), work_root=self.work)

    def cli(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = access.main(arguments)
        return code, json.loads(output.getvalue())

    def test_segments_ignore_text_and_preserve_open_handles_then_close(self):
        (self.root/'synthetic.txt').write_text('not an image segment')
        self.segment(self.root/'synthetic.E02', 2)
        self.assertEqual([p.suffix.lower() for p in image_segments(self.image)], ['.e01', '.e02'])
        with open_image(self.image) as opened:
            self.assertEqual(len(opened.parts), 2)
            self.assertTrue(all(not h.closed and h.tell() == 0 for h in self.handles))
        self.assertTrue(self.disk_closed)
        self.assertTrue(all(h.closed for h in self.handles))

    def test_missing_reordered_or_invalid_segments_are_rejected(self):
        later = self.root/'synthetic.e03'
        for name, ordinal in (('synthetic.e03', 3), ('synthetic.e03', 2), ('synthetic.e02', 1)):
            with self.subTest(name=name, ordinal=ordinal):
                later.unlink(missing_ok=True)
                (self.root/'synthetic.e02').unlink(missing_ok=True)
                self.segment(self.root/name, ordinal)
                with self.assertRaisesRegex(ValueError, 'Missing, reordered'):
                    with open_image(self.image):
                        self.fail('invalid segments opened')
        (self.root/'synthetic.e02').unlink()
        self.image.write_bytes(b'not EWF data')
        with self.assertRaisesRegex(ValueError, 'Missing, reordered'):
            with open_image(self.image):
                pass

    def test_missing_first_segment_and_symlink_segment_are_rejected(self):
        self.segment(self.root/'synthetic.e02', 2)
        self.image.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing, reordered'):
            with open_image(self.image):
                pass
        self.image.symlink_to(self.root/'synthetic.e02')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            with open_image(self.image):
                pass

    def test_ambiguous_volume_requires_explicit_offset(self):
        self.volumes.append(SimpleNamespace(offset=8192, size=999, name='second'))
        with self.assertRaisesRegex(ValueError, 'Select volume_offset'):
            inspect(self.image, self.args())
        result = inspect(self.image, self.args(volume_offset=8192))
        self.assertEqual(result['volume_offset'], 8192)
        self.assertEqual(result['volume_selection'], 'explicit')
        with self.assertRaisesRegex(ValueError, 'Select volume_offset'):
            self.export()

    def test_existing_read_bytes_hash_and_stat_contracts(self):
        result = inspect(self.image, self.args())
        self.assertEqual(result['lines'], [{'line': 1, 'text': 'alpha'}, {'line': 2, 'text': 'beta'}])
        self.assertTrue(result['complete_file'])
        self.assertEqual(result['representation'], 'line_text')
        sliced = inspect(self.image, self.args('read_bytes', offset=2, length=4))
        self.assertEqual(base64.b64decode(sliced['base64']), b'pha\n')
        self.assertEqual((sliced['byte_start'], sliced['byte_end']), (2, 6))
        self.assertFalse(sliced['complete_file'])
        hashed = inspect(self.image, self.args('hash'))
        self.assertEqual(hashed['sha256'], hashlib.sha256(self.payload.read_bytes()).hexdigest())
        self.assertTrue(hashed['complete_file'])
        self.assertEqual(inspect(self.image, self.args('stat'))['bytes'], 11)

    def test_cli_existing_reads_use_inspect_unchanged(self):
        for action, options in (('volumes', []), ('read', ['--start-line', '2']),
                                ('read_bytes', ['--offset', '2', '--length', '4']), ('hash', []),
                                ('stat', []), ('search', ['--text', 'ALPHA', '--max-bytes', '50'])):
            with self.subTest(action=action), patch.object(access, 'evidence_image', return_value=self.image):
                code, result = self.cli(['/evidence/synthetic.e01', action, '--file-path', self.node.path, *options])
                self.assertEqual(code, 0, result)
                expected = self.args(action)
                if action == 'read': expected['start_line'] = 2
                if action == 'read_bytes': expected.update(offset=2, length=4)
                if action == 'search': expected.update(text='ALPHA', max_bytes=50)
                self.assertEqual(result, json.loads(json.dumps(inspect(self.image, expected))))

    def test_cli_argument_errors_are_json_with_nonzero_exit(self):
        for args in ([], ['x', 'invalid'], ['x', 'read_bytes', '--offset', 'oops']):
            code, result = self.cli(args)
            self.assertEqual(code, 1)
            self.assertIn('error', result)
            self.assertNotIn('Traceback', result['error'])
        with patch.object(access, 'evidence_image', return_value=self.image):
            code, result = self.cli(['/evidence/synthetic.e01', 'read'])
        self.assertEqual(code, 1)
        self.assertEqual(result['error_type'], 'InvalidArguments')

    def test_cli_source_boundary_rejects_outside_and_symlink_escape(self):
        evidence = self.root/'evidence'
        evidence.mkdir()
        inside = evidence/'disk.e01'
        self.segment(inside, 1)
        self.assertEqual(access.evidence_image(inside, evidence), inside)
        with self.assertRaisesRegex(ValueError, 'under /evidence'):
            access.evidence_image(self.image, evidence)
        link = evidence/'escape.e01'
        link.symlink_to(self.image)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            access.evidence_image(link, evidence)
        directory = evidence/'outside'
        directory.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'under /evidence'):
            access.evidence_image(directory/self.image.name, evidence)

    def test_export_preserves_exact_binary_and_source(self):
        raw = bytes(range(256))*1001
        self.payload.write_bytes(raw)
        original = self.image.read_bytes()
        result = self.export()
        self.assertEqual((self.work/'new.bin').read_bytes(), raw)
        self.assertEqual(result['bytes'], len(raw))
        self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertTrue(result['complete'])
        self.assertTrue(result['complete_file'])
        self.assertTrue(result['derived'])
        self.assertEqual(result['source'], {'image': str(self.image), 'volume_offset':4096, 'file_path':self.node.path})
        self.assertEqual(self.image.read_bytes(), original)
        self.assertEqual(stat.S_IMODE((self.work/'new.bin').stat().st_mode) & 0o111, 0)

    def test_relative_export_is_anchored_to_work_and_returns_absolute_destination(self):
        (self.work/'exports').mkdir()
        destination = './exports/export__etc_passwd'
        result = self.export(destination)
        expected = self.work/'exports'/'export__etc_passwd'
        self.assertEqual(result['destination'], str(expected))
        self.assertEqual(expected.read_bytes(), self.payload.read_bytes())
        self.assertEqual(result['sha256'], hashlib.sha256(self.payload.read_bytes()).hexdigest())
        self.assertTrue(result['complete_file'])

    def test_export_preserves_spaces_in_destination_filename(self):
        for destination in (' report file.bin ', 'nested folder/ report file.bin '):
            with self.subTest(destination=destination):
                (self.work/'nested folder').mkdir(exist_ok=True)
                result = self.export(destination)
                expected = self.work/destination
                self.assertEqual(result['destination'], str(expected))
                self.assertEqual(expected.read_bytes(), self.payload.read_bytes())

    def test_invalid_destination_form_is_rejected_before_opening_image(self):
        for destination in (' ', '\t\n', '.', './', '../escape', 'nested/../../escape',
                            str(self.work/'..'/'escape'), str(self.root/'escape'), 'bad\x00name'):
            with self.subTest(destination=destination), patch.object(access, 'open_image') as opened:
                with self.assertRaises(access.ExportError) as failed:
                    self.export(destination)
                opened.assert_not_called()
                self.assertFalse(failed.exception.details['complete_file'])
                self.assertEqual(failed.exception.details['bytes'], 0)
        self.assertEqual(list(self.work.iterdir()), [])

        with patch.object(access, 'open_image') as opened:
            with self.assertRaisesRegex(ValueError, 'requires --destination'):
                access.export_file(self.image, self.args('export', destination=''), work_root=self.work)
            opened.assert_not_called()

    def test_relative_export_keeps_exclusive_and_nofollow_creation(self):
        existing = self.work/'existing'
        existing.write_bytes(b'preserve')
        (self.work/'linked-file').symlink_to(existing)
        (self.work/'linked-parent').symlink_to(self.root, target_is_directory=True)
        for destination in ('existing', 'linked-file', 'linked-parent/escaped'):
            with self.subTest(destination=destination), self.assertRaises(access.ExportError):
                self.export(destination)
        self.assertEqual(existing.read_bytes(), b'preserve')
        self.assertFalse((self.root/'escaped').exists())

    def test_export_limit_before_creation_and_lower_limit(self):
        with self.assertRaisesRegex(access.ExportError, 'exceeds') as failed:
            self.export(max_bytes=3)
        self.assertFalse((self.work/'new.bin').exists())
        self.assertFalse(failed.exception.details['complete'])
        self.assertEqual(failed.exception.details['bytes'], 0)
        for invalid in (0, -1, True, access.EXPORT_LIMIT+1):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'max_bytes'):
                self.export(max_bytes=invalid)
        self.assertTrue(self.export(max_bytes=11)['complete'])

    def test_export_default_limit_is_256_mib(self):
        with self.payload.open('wb') as stream:
            stream.truncate(access.EXPORT_LIMIT + 1)
        with self.assertRaisesRegex(access.ExportError, 'exceeds'):
            self.export()
        self.assertFalse((self.work/'new.bin').exists())

    def test_export_existing_destination_is_preserved(self):
        destination = self.work/'new.bin'
        destination.write_bytes(b'preexisting')
        with self.assertRaises(access.ExportError):
            self.export(destination)
        self.assertEqual(destination.read_bytes(), b'preexisting')

    def test_export_rejects_destination_symlink_and_parent_escape(self):
        target = self.root/'outside'
        target.write_bytes(b'preserve')
        leaf = self.work/'leaf'
        leaf.symlink_to(target)
        directory = self.work/'linked'
        directory.symlink_to(self.root, target_is_directory=True)
        for destination in (leaf, directory/'new.bin', self.root/'forbidden', self.work/'..'/'forbidden'):
            with self.subTest(destination=destination), self.assertRaises(access.ExportError):
                self.export(destination)
        self.assertEqual(target.read_bytes(), b'preserve')
        self.assertFalse((self.root/'new.bin').exists())
        self.assertFalse((self.root/'forbidden').exists())

    def test_export_rejects_source_directory_and_symlink(self):
        original = self.payload
        for path in (self.root, self.root/'link'):
            if path.name == 'link': path.symlink_to(original)
            self.node.local = path
            with self.subTest(path=path), self.assertRaisesRegex(access.ExportError, 'regular file'):
                self.export()
        self.assertFalse((self.work/'new.bin').exists())

    def test_export_mid_read_failure_cleans_only_partial(self):
        class Broken(io.BytesIO):
            def read(self, size=-1):
                if self.tell(): raise OSError('synthetic mid-read failure')
                return super().read(4)
        with patch.object(self.node, 'open', return_value=Broken(self.payload.read_bytes())):
            with self.assertRaisesRegex(access.ExportError, 'mid-read') as failed:
                self.export()
        self.assertEqual(failed.exception.details['bytes'], 4)
        self.assertEqual(failed.exception.details['sha256'], hashlib.sha256(b'alph').hexdigest())
        self.assertFalse(failed.exception.details['complete'])
        self.assertFalse((self.work/'new.bin').exists())
        self.assertTrue(self.disk_closed)
        self.assertTrue(all(handle.closed for handle in self.handles))

    def test_export_short_read_or_growth_discards_partial(self):
        for data in (b'a', b'x'*12):
            with self.subTest(data=data), patch.object(self.node, 'open', return_value=io.BytesIO(data)):
                with self.assertRaises(access.ExportError):
                    self.export(max_bytes=11)
                self.assertFalse((self.work/'new.bin').exists())

    def test_export_changed_image_discards_partial(self):
        original = self.payload.read_bytes()
        segment = self.root/'synthetic.e02'
        outer = self
        class Changed(io.BytesIO):
            def close(self):
                outer.segment(segment, 2)
                super().close()
        with patch.object(self.node, 'open', return_value=Changed(original)):
            with self.assertRaisesRegex(access.ExportError, 'segments changed'):
                self.export()
        self.assertFalse((self.work/'new.bin').exists())

    def test_export_source_close_failure_discards_partial(self):
        def broken_close():
            raise OSError('synthetic source close failure')
        container = sys.modules['dissect.target.containers.ewf']
        with patch.object(container, 'EwfContainer', return_value=SimpleNamespace(close=broken_close)):
            with self.assertRaisesRegex(access.ExportError, 'source close failure') as failed:
                self.export()
        self.assertEqual(failed.exception.details['bytes'], len(self.payload.read_bytes()))
        self.assertFalse(failed.exception.details['complete'])
        self.assertFalse((self.work/'new.bin').exists())

    def test_cleanup_preserves_replacement_inode(self):
        destination = self.work/'new.bin'
        with self.assertRaisesRegex(OSError, 'synthetic'):
            with access.new_destination(destination, self.work) as output:
                output.write(b'partial')
                destination.unlink()
                destination.write_bytes(b'replacement')
                raise OSError('synthetic failure')
        self.assertEqual(destination.read_bytes(), b'replacement')

    def test_cli_export_failure_keeps_incomplete_metadata(self):
        with patch.object(access, 'evidence_image', return_value=self.image), patch.object(access, 'export_file', side_effect=access.ExportError('synthetic', {'bytes':4, 'complete':False})):
            code, result = self.cli(['/evidence/synthetic.e01', 'export', '--file-path', '/binary', '--destination', '/work/new'])
        self.assertEqual(code, 1)
        self.assertEqual(result['bytes'], 4)
        self.assertFalse(result['complete'])


if __name__ == '__main__':
    unittest.main()
