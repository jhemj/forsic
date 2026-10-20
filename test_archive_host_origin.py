"""Synthetic host/container identity boundaries; no Docker, model or network."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_archive_selection as fixtures
from forsic_plugin.evidence import Case
from forsic_plugin.image_reader import ARCHIVE_ORIGIN_VERSION, archive_origin, image_segments, inspect


class ArchiveHostOriginTests(unittest.TestCase):
    setUp = fixtures.ImageOriginTests.setUp
    segment = staticmethod(fixtures.ImageOriginTests.segment)

    def call(self, args=None, selection=None):
        case = SimpleNamespace(root=self.root, path=lambda name: self.root/name)
        return Case.image_files(case, args or self.args, archive_selection=selection)

    def parser(self, command, container, native_execution):
        # Model independent container namespaces while preserving every local
        # before/after field. The real reader and synthetic TAR path still run.
        def local_origin(parts, volume):
            origin = archive_origin(parts, volume)
            for segment in origin['segments']:
                segment['metadata']['st_dev'] = self.container_device
            return origin
        with patch('forsic_plugin.image_reader.archive_origin', side_effect=local_origin):
            result = inspect(str(self.image), json.loads(command[-1]))
        self.parser_origin = deepcopy(result['archive_origin'])
        result['archive_origin_version'] = -100  # Never authoritative.
        return result

    def listed(self):
        self.container_device = 101
        listing = self.call()
        self.selection = {key: deepcopy(listing[key]) for key in
                          ('archive_origin', 'archive_origin_version', 'archive_identity')}
        self.selection['member'] = deepcopy(listing['entries'][0])
        self.read = {**self.args, 'action': 'archive_read', 'member_index': 0}
        return listing

    def test_namespace_change_uses_full_host_binding_and_overwrites_parser_origin(self):
        with patch('forsic_plugin.image_process.run_parser', side_effect=self.parser):
            listing = self.listed()
            self.assertNotEqual(listing['archive_origin'], self.parser_origin)
            self.assertEqual(listing['archive_origin_version'], ARCHIVE_ORIGIN_VERSION)
            self.container_device = 202
            result = self.call(self.read, self.selection)
        self.assertEqual(result['text'], 'body')
        self.assertEqual(result['archive_origin'], listing['archive_origin'])
        self.assertNotEqual(result['archive_origin'], self.parser_origin)
        self.assertEqual(result['archive_origin_version'], ARCHIVE_ORIGIN_VERSION)
        self.assertEqual(result['archive_origin'], archive_origin(image_segments(self.image), 8192))
        self.assertEqual(set(result['archive_origin']['segments'][0]['metadata']),
                         {'st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns',
                          'st_ctime_ns', 'st_mtime', 'st_ctime'})

    def test_every_changed_host_field_rejects_before_parser_launch(self):
        with patch('forsic_plugin.image_process.run_parser', side_effect=self.parser):
            self.listed()
        for field in self.selection['archive_origin']['segments'][0]['metadata']:
            changed = deepcopy(self.selection['archive_origin'])
            changed['segments'][0]['metadata'][field] += 1
            with self.subTest(field=field), \
                    patch('forsic_plugin.image_reader.archive_origin', return_value=changed), \
                    patch('forsic_plugin.image_process.run_parser') as parser:
                with self.assertRaisesRegex(ValueError, 'changed since listing'):
                    self.call(self.read, self.selection)
                parser.assert_not_called()

    def test_implicit_single_volume_listing_binds_discovered_volume(self):
        self.volumes[:] = [SimpleNamespace(offset=8192)]
        self.args.pop('volume_offset')
        with patch('forsic_plugin.image_process.run_parser', side_effect=self.parser):
            listing = self.listed()
            self.assertEqual(listing['archive_origin']['volume_offset'], 8192)
            self.read['volume_offset'] = 8192  # As supplied by retained-listing resolution.
            self.container_device = 202
            self.assertEqual(self.call(self.read, self.selection)['text'], 'body')

    def test_added_removed_and_symlinked_segments_reject_before_launch(self):
        with patch('forsic_plugin.image_process.run_parser', side_effect=self.parser):
            self.listed()
        second = self.root/'synthetic.e02'
        self.segment(second, 2)
        with patch('forsic_plugin.image_process.run_parser') as parser:
            with self.assertRaisesRegex(ValueError, 'changed since listing'):
                self.call(self.read, self.selection)
            parser.assert_not_called()
        second.unlink()
        saved = self.root/'saved.bin'
        self.image.rename(saved)
        with patch('forsic_plugin.image_process.run_parser') as parser:
            with self.assertRaisesRegex(ValueError, 'changed since listing'):
                self.call(self.read, self.selection)
            parser.assert_not_called()
            self.image.symlink_to(saved)
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                self.call(self.read, self.selection)
            parser.assert_not_called()

    def test_every_mid_call_host_field_change_discards_parser_output(self):
        before = archive_origin(image_segments(self.image), 8192)
        for field in before['segments'][0]['metadata']:
            after = deepcopy(before)
            after['segments'][0]['metadata'][field] += 1
            with self.subTest(field=field), \
                    patch('forsic_plugin.image_reader.archive_origin', side_effect=[before, after]), \
                    patch('forsic_plugin.image_process.run_parser', return_value={
                        'volume_offset':8192, 'text':'untrusted body', 'archive_origin':before,
                        'archive_origin_version':ARCHIVE_ORIGIN_VERSION}) as parser:
                with self.assertRaisesRegex(ValueError, 'changed during archive inspection'):
                    self.call()
                parser.assert_called_once()

    def test_container_local_change_still_discards_output(self):
        from forsic_plugin.image_reader import inspect_archive
        with patch('forsic_plugin.image_process.run_parser', side_effect=self.parser):
            self.listed()
            def changed(node, args, selection):
                result = inspect_archive(node, args, selection)
                self.image.write_bytes(self.image.read_bytes()+b'changed')
                return result
            with patch('forsic_plugin.image_reader.inspect_archive', side_effect=changed):
                with self.assertRaisesRegex(ValueError, 'Image segments changed during'):
                    self.call(self.read, self.selection)

    def test_volume_archive_and_header_mismatches_remain_rejected(self):
        with patch('forsic_plugin.image_process.run_parser', side_effect=self.parser) as parser:
            self.listed()
            with self.assertRaisesRegex(ValueError, 'selected volume changed'):
                self.call({**self.read, 'volume_offset':4096}, self.selection)
            with patch('forsic_plugin.image_reader.select_volume', return_value=SimpleNamespace(offset=4096)):
                with self.assertRaisesRegex(ValueError, 'Selected volume differs'):
                    self.call(self.read, self.selection)
            for field, value, message in [('archive_identity', 'st_size', 'Archive metadata changed'),
                                           ('member', 'bytes', 'ordinal header changed')]:
                selection = deepcopy(self.selection)
                selection[field][value] += 1
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                    self.call(self.read, selection)

    def test_legacy_binding_and_spoofed_origin_cannot_authorize_read(self):
        with patch('forsic_plugin.image_process.run_parser', side_effect=self.parser):
            self.listed()
        for version in (None, 1):
            selection = deepcopy(self.selection)
            selection['archive_origin_version'] = version
            with self.subTest(version=version), patch('forsic_plugin.image_process.run_parser') as parser:
                with self.assertRaisesRegex(ValueError, 're-list'):
                    self.call(self.read, selection)
                parser.assert_not_called()
        selection = deepcopy(self.selection)
        selection['archive_origin']['segments'][0]['metadata']['st_dev'] += 1
        forged = {'volume_offset':8192, 'text':'forged', 'archive_origin':selection['archive_origin'],
                  'archive_origin_version':ARCHIVE_ORIGIN_VERSION}
        with patch('forsic_plugin.image_process.run_parser', return_value=forged) as parser:
            with self.assertRaisesRegex(ValueError, 'changed since listing'):
                self.call(self.read, selection)
            parser.assert_not_called()


if __name__ == '__main__':
    unittest.main()
