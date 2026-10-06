"""Synthetic-only streaming search and allocated-image hash regressions."""
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forsic_plugin.evidence import Case
from forsic_plugin.image_reader import hash_node, search_node
from forsic_plugin import literal_search


class Node:
    """Minimal Dissect node: no container, image or external service needed."""
    def __init__(self, name, content=b'', children=None, mode=None):
        self.path, self.content, self.children = name, content, children
        self.mode = mode or ((stat.S_IFDIR | 0o555) if children is not None else (stat.S_IFREG | 0o444))
        self.inode = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
        self.mtime = 100

    def lstat(self):
        return SimpleNamespace(st_dev=1, st_ino=self.inode, st_mode=self.mode,
                               st_size=len(self.content), st_mtime=self.mtime, st_ctime=100)

    def open(self):
        return io.BytesIO(self.content)

    def scandir(self):
        return [SimpleNamespace(get=lambda n=node: n) for node in self.children]


class SearchResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'evidence'
        self.root.mkdir()
        manifest = self.base / 'case.json'
        manifest.write_text(json.dumps({'evidence_root': str(self.root),
                                       'output_root': str(self.base / 'results')}))
        self.case = Case(manifest)

    def pages(self, call, args):
        pages, matches = [], []
        for _ in range(1000):
            result = call(args)
            pages.append(result)
            matches.extend(result['matches'])
            if result['next_cursor'] is None:
                return pages, matches
            args = {**args, 'cursor': result['next_cursor']}
        self.fail('Search did not converge')

    def test_host_file_over_four_mib_is_not_skipped(self):
        raw = b'ordinary log line\n' * 300000 + b'UNIQUE NEEDLE\n'
        (self.root / 'large.log').write_bytes(raw)
        pages, hits = self.pages(self.case.search, {'path': 'large.log', 'text': 'unique needle',
                                                   'max_bytes': 1024 * 1024})
        self.assertGreater(len(raw), 4 * 1024 * 1024)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['line'], 300001)
        self.assertEqual(sum(p['bytes_scanned'] for p in pages), len(raw))
        self.assertTrue(all(p['files_skipped'] == 0 for p in pages))
        self.assertTrue(pages[-1]['scan_exhausted'])
        self.assertFalse(pages[-1]['coverage_complete'])  # A page is not the entire search.

    def test_same_file_match_cap_resumes_without_loss_or_duplicate(self):
        (self.root / 'many.log').write_text('hit\n' * 135)
        pages, hits = self.pages(self.case.search, {'path': 'many.log', 'text': 'hit'})
        self.assertEqual([len(p['matches']) for p in pages], [60, 60, 15])
        self.assertEqual([p['line'] for p in hits], list(range(1, 136)))
        self.assertEqual(pages[0]['next_cursor']['state']['byte_offset'], 240)
        self.assertEqual(pages[0]['next_cursor']['state']['line'], 61)

    def test_utf8_casefold_and_literal_across_byte_boundaries(self):
        raw = '시작 STRAẞE 끝\r\n다음 가나다 종료\n끝 가나다'.encode()
        for needle, lines in [('strasse', [1]), ('가나다', [2, 3])]:
            (self.root / 'unicode.log').write_bytes(raw)
            pages, hits = self.pages(self.case.search, {'path': 'unicode.log', 'text': needle, 'max_bytes': 1})
            self.assertEqual([h['line'] for h in hits], lines)
            self.assertEqual(sum(p['bytes_scanned'] for p in pages), len(raw))
            self.assertNotIn('\ufffd', ''.join(h['text'] for h in hits))

    def test_long_unfinished_line_carries_bounded_state_then_emits_once(self):
        raw = b'a' * 5000 + b'needle' + b'z' * 11000 + b'\n'
        (self.root / 'long.log').write_bytes(raw)
        pages, hits = self.pages(self.case.search, {'path': 'long.log', 'text': 'needle', 'max_bytes': 4096})
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['line'], 1)
        self.assertTrue(hits[0]['line_truncated'])
        self.assertEqual(hits[0]['byte_end'], len(raw))
        self.assertTrue(all(len(json.dumps(p['next_cursor'])) < 6000 for p in pages[:-1]))

    def test_changed_query_scope_file_or_cursor_is_rejected(self):
        path = self.root / 'a.log'
        path.write_text('hit\nhit\n')
        (self.root / 'b.log').write_text('hit\nhit\n')
        args = {'path': 'a.log', 'text': 'hit', 'max_matches': 1}
        cursor = self.case.search(args)['next_cursor']
        for changed in ({'text': 'other'}, {'path': 'b.log'}, {'offset': 1}):
            with self.assertRaisesRegex(ValueError, 'cursor'):
                self.case.search({**args, **changed, 'cursor': cursor})
        changed_cursor = {**cursor, 'file_path': 'b.log'}
        with self.assertRaisesRegex(ValueError, 'cursor'):
            self.case.search({**args, 'cursor': changed_cursor})
        path.write_text('changed\n')
        with self.assertRaisesRegex(ValueError, 'cursor'):
            self.case.search({**args, 'cursor': cursor})

    def test_directory_file_budget_and_skips_are_explicit(self):
        (self.root / 'a').write_text('hit\n')
        (self.root / 'b').write_bytes(b'\x00not text')
        (self.root / 'c').write_text('hit\n')
        (self.root / 'link').symlink_to(self.root / 'a')
        pages, hits = self.pages(self.case.search, {'text': 'hit', 'max_files': 1})
        self.assertEqual([h['path'] for h in hits], ['a', 'c'])
        self.assertEqual(sum(p['files_skipped'] for p in pages), 2)
        self.assertEqual({s['reason'] for p in pages for s in p['skips']}, {'binary_content', 'not_regular_file'})
        self.assertFalse(any(p['coverage_complete'] for p in pages))

    def test_image_search_uses_same_streaming_resume_and_old_file_offset(self):
        node = Node('/', children=[Node('/a', b'match\nmatch\n'), Node('/b', b'match\n')])
        pages, hits = self.pages(lambda args: search_node(node, args, {'image': 'fixture'}),
                                {'text': 'match', 'max_bytes': 3})
        self.assertEqual([(h['file_path'], h['line']) for h in hits], [('/a', 1), ('/a', 2), ('/b', 1)])
        legacy = search_node(node, {'text': 'match', 'offset': 1})
        self.assertEqual([h['file_path'] for h in legacy['matches']], ['/b'])
        self.assertFalse(legacy['coverage_complete'])
        with self.assertRaisesRegex(ValueError, 'cursor'):
            search_node(node, {'text': 'match', 'cursor': pages[0]['next_cursor']}, {'image': 'changed'})

    def test_recursive_scope_symlink_and_empty_files(self):
        node = Node('/', children=[Node('/a', b''),
                                  Node('/folder', children=[Node('/folder/b', b'hit')]),
                                  Node('/link', mode=stat.S_IFLNK | 0o777)])
        plain = search_node(node, {'text': 'hit'})
        self.assertEqual(plain['matches'], [])
        self.assertEqual(plain['files_completed'], 1)
        self.assertEqual(plain['files_skipped'], 1)
        pages, hits = self.pages(lambda args: search_node(node, args),
                                {'text': 'hit', 'recursive': True, 'max_files': 1})
        self.assertEqual([h['file_path'] for h in hits], ['/folder/b'])
        self.assertTrue(pages[-1]['scan_exhausted'])

    def test_search_short_read_is_not_complete(self):
        node = Node('/short', b'full content')
        with patch.object(node, 'open', return_value=io.BytesIO(b'partial')):
            result = search_node(node, {'text': 'absent'})
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(result['skips'][0]['reason'], 'unexpected_eof')

    def test_changed_image_file_identity_is_rejected(self):
        node = Node('/a', b'hit\nhit\n')
        result = search_node(node, {'text': 'hit', 'max_matches': 1})
        node.mtime += 1
        with self.assertRaisesRegex(ValueError, 'cursor'):
            search_node(node, {'text': 'hit', 'cursor': result['next_cursor']})

    def test_cursor_binds_current_file_in_directory(self):
        node = Node('/', children=[Node('/a', b'first\nhit\n')])
        result = search_node(node, {'text': 'hit', 'max_bytes': 3})
        node.children[0].mtime += 1
        with self.assertRaisesRegex(ValueError, 'changed'):
            search_node(node, {'text': 'hit', 'cursor': result['next_cursor']})

    def test_read_and_stat_failures_remain_skips_with_bounded_pages(self):
        def denied():
            raise PermissionError('synthetic')
        node = Node('/later', b'hit\n')
        def call(args):
            return literal_search.search_entries([
                ('/denied1', denied, None, None), ('/denied2', denied, None, None),
                ('/later', node.lstat, node.open, None)], args, {'synthetic': True})
        pages, hits = self.pages(call, {'text': 'hit', 'max_files': 1})
        self.assertEqual(sum(p['files_skipped'] for p in pages), 2)
        self.assertEqual(len(hits), 1)
        self.assertTrue(all(p['files_skipped'] <= 1 for p in pages))

    def test_limits_are_validated_and_small_complete_search_is_complete(self):
        node = Node('/small', b'hit\n')
        for name, value in [('max_bytes', 0), ('max_matches', 61), ('max_files', 1.5),
                            ('max_seconds', 26), ('max_bytes', True)]:
            with self.assertRaises(ValueError):
                search_node(node, {'text': 'hit', name: value})
        self.assertTrue(search_node(node, {'text': 'hit'})['coverage_complete'])

    def test_directory_enumeration_error_is_an_explicit_gap(self):
        node = Node('/', children=[])
        with patch.object(node, 'scandir', side_effect=PermissionError('synthetic')):
            result = search_node(node, {'text': 'hit'})
        self.assertFalse(result['coverage_complete'])
        self.assertTrue(result['scan_exhausted'])
        self.assertEqual(result['skips'][0]['reason'], 'directory_PermissionError')

    def test_image_hash_reads_complete_regular_file_not_slice(self):
        raw = b'prefix' + b'synthetic' * 600000 + b'final byte'
        result = hash_node(Node('/large', raw))
        self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['bytes'], len(raw))
        self.assertTrue(result['complete_file'])
        self.assertEqual(result['hash_scope'], 'whole_file')
        for node in (Node('/', children=[]), Node('/link', mode=stat.S_IFLNK | 0o777)):
            with self.assertRaisesRegex(ValueError, 'regular file'):
                hash_node(node)

    def test_image_hash_short_read_or_changed_metadata_never_returns_digest(self):
        node = Node('/truncated', b'full content')
        with patch.object(node, 'open', return_value=io.BytesIO(b'partial')):
            with self.assertRaisesRegex(ValueError, 'ended early'):
                hash_node(node)
        before = node.lstat()
        changed = SimpleNamespace(**{**vars(before), 'st_mtime': 101})
        with patch.object(node, 'lstat', side_effect=[before, changed]):
            with self.assertRaisesRegex(ValueError, 'changed'):
                hash_node(node)

    def test_time_limit_returns_same_file_continuation(self):
        node = Node('/a', b'first\nhit\n')
        # Start / file admission / first read / next read budget => stop.
        with patch.object(literal_search.time, 'monotonic', side_effect=[0, 0, 0, 2]):
            result = search_node(node, {'text': 'hit', 'max_seconds': 1})
        self.assertEqual(result['stop_reason'], 'time_limit')
        self.assertEqual(result['next_cursor']['state']['byte_offset'], 6)
        final = search_node(node, {'text': 'hit', 'cursor': result['next_cursor']})
        self.assertEqual([h['line'] for h in final['matches']], [2])


if __name__ == '__main__':
    unittest.main()
