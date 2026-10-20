"""Offline archive adapter contracts. Synthetic bytes, no image, model or network."""
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from forsic_plugin.image_reader import inspect_archive


class Node:
    def __init__(self,path):self.path=path
    def lstat(self):return self.path.lstat()
    def open(self):return self.path.open('rb')


class ArchiveReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'record.tar.gz'
    def archive(self, entries, mode='w:gz'):
        with tarfile.open(self.path,mode) as t:
            for name,content,kind in entries:
                info=tarfile.TarInfo(name);info.mtime=1
                if kind=='link':info.type=tarfile.SYMTYPE;info.linkname='/etc/passwd';t.addfile(info)
                else:info.size=len(content);t.addfile(info,io.BytesIO(content))
        return Node(self.path)
    def test_list_and_read_duplicate_names_by_ordinal_and_slice(self):
        n=self.archive([('x',b'first','file'),('x',b'second','file')])
        listing=inspect_archive(n,dict(action='archive_list'))
        self.assertEqual([v['member_index'] for v in listing['entries']],[0,1])
        self.assertTrue(listing['coverage_complete'])
        out=inspect_archive(n,dict(action='archive_read',member_index=1,offset=1,length=3))
        self.assertEqual(out['text'],'eco');self.assertEqual(out['next_offset'],4)
        self.assertFalse(out['complete_member']);self.assertFalse(out['coverage_complete'])
        out=inspect_archive(n,dict(action='archive_read',member_index=0))
        self.assertTrue(out['complete_member']);self.assertEqual(out['text'],'first')
    def test_member_names_are_data_never_extracted_and_links_not_followed(self):
        n=self.archive([('../../outside',b'no write','file'),('/absolute',b'no exec','file'),('link',b'','link')])
        before=set(Path(self.tmp.name).iterdir())
        listing=inspect_archive(n,dict(action='archive_list'))
        self.assertEqual(listing['entries'][0]['name'],'../../outside')
        self.assertEqual(inspect_archive(n,dict(action='archive_read',member_index=0))['text'],'no write')
        with self.assertRaisesRegex(ValueError,'not followed'):inspect_archive(n,dict(action='archive_read',member_index=2))
        self.assertEqual(set(Path(self.tmp.name).iterdir()),before)
    def test_page_end_is_not_full_archive_coverage(self):
        n=self.archive([(str(i),b'x','file') for i in range(101)])
        first=inspect_archive(n,dict(action='archive_list'))
        self.assertEqual(first['next_offset'],100);self.assertFalse(first['coverage_complete'])
        last=inspect_archive(n,dict(action='archive_list',offset=100))
        self.assertEqual(last['entries'][0]['member_index'],100)
        self.assertTrue(last['scan_exhausted']);self.assertFalse(last['coverage_complete'])
    def test_compressed_expansion_and_skipped_members_are_bounded(self):
        n=self.archive([('big',b'0'*100000,'file'),('tail',b'target','file')])
        for action in ('archive_list','archive_read'):
            out=inspect_archive(n,dict(action=action,member_index=1,archive_scan_bytes=10240))
            self.assertEqual(out['stop_reason'],'scan_budget');self.assertFalse(out['coverage_complete'])
            self.assertNotIn('text',out);self.assertLessEqual(out['decompressed_bytes_scanned'],10240)
    def test_plain_tar_and_binary_slice(self):
        n=self.archive([('binary',b'\x00\xff','file')],mode='w')
        out=inspect_archive(n,dict(action='archive_read',member_index=0))
        self.assertEqual(out['base64'],'AP8=');self.assertTrue(out['complete_member'])
    def test_bad_format_and_unknown_member_are_explicit_errors(self):
        n=self.archive([('x',b'a','file')])
        with self.assertRaisesRegex(ValueError,'member_index'):inspect_archive(n,dict(action='archive_read',member_index=3))
        with self.assertRaisesRegex(ValueError,'member_index'):inspect_archive(n,dict(action='archive_read'))
        self.path.write_bytes(b'not a tar')
        with self.assertRaises(tarfile.ReadError):inspect_archive(n,dict(action='archive_list'))
    def test_archive_symlink_is_rejected(self):
        self.path.symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError,'regular file'):inspect_archive(Node(self.path),dict(action='archive_list'))
    def test_member_byte_offset_cannot_exceed_length(self):
        n=self.archive([('x',b'a','file')])
        with self.assertRaisesRegex(ValueError,'offset exceeds'):
            inspect_archive(n,dict(action='archive_read',member_index=0,offset=2))

    def test_unknown_member_does_not_become_empty_success(self):
        n=self.archive([])
        self.assertTrue(inspect_archive(n,dict(action='archive_list'))['coverage_complete'])
        with self.assertRaisesRegex(ValueError,'not present'):inspect_archive(n,dict(action='archive_read',member_index=0))

if __name__=='__main__':unittest.main()
