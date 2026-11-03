"""Bounded classic E01 reader for already validated, caller-owned read-only handles.

Uses Dissect's fixed EWF structures and Container interface, without constructing
its eagerly indexed Segment/Table objects. Format reference: libyal/libewf,
``documentation/Expert Witness Compression Format (EWF).asciidoc`` (table,
sectors and volume sections). EWF2, logical images and the faulty EnCase 6.7
31-bit offset overflow variant are outside this adapter's contract. Reads return
at most 64 MiB per call; larger extents must be consumed in chunks. A request
whose actual remaining extent exceeds that bound raises EWFError before I/O.
"""
from __future__ import annotations

import io
import operator
import struct
import threading
import zlib
from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass
from typing import BinaryIO

from dissect.evidence.ewf import c_ewf
from dissect.evidence.exception import EWFError
from dissect.target.container import Container

# Both retained metadata and payloads have explicit, per-reader ceilings.
CACHE_BYTES = 8 * 1024 * 1024
# Returned read buffers are also bounded; consumers must stream larger extents.
MAX_READ_BYTES = 64 * 1024 * 1024
MAX_CACHE_ENTRIES = 4096
MAX_SEGMENTS = 65535
MAX_SECTIONS = 262144
MAX_TABLES = 65536
MAX_TABLE_ENTRIES = 1024 * 1024
MAX_CHUNK_BYTES = 16 * 1024 * 1024
MAX_HEADER_BYTES = 1024 * 1024
INDEX_PAGE_BYTES = 4096
_DESCRIPTOR_BYTES = len(c_ewf.EWFSectionDescriptor)
_TABLE_HEADER_BYTES = 24


@dataclass(frozen=True, slots=True)
class _Table:
    segment: int
    first_chunk: int
    count: int
    index_offset: int
    base: int
    data_start: int
    data_end: int


class BoundedEwfContainer(Container):
    __type__ = 'ewf'

    def __init__(self, handles: list[BinaryIO]):
        super().__init__([], 0)
        self._handles = []
        self._tables = []
        self._table_starts = []
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._position = 0
        self._lock = threading.RLock()
        self._section_count = 0
        self.chunk_size = 0
        self.sector_size = 0
        self._chunk_count = 0
        self._geometry = None
        try:
            if not 1 <= len(handles) <= MAX_SEGMENTS:
                raise EWFError('EWF segment count exceeds reader contract')
            self._handles = list(handles)
            self.fh = self._handles
            header_seen = False
            chunks = 0
            for ordinal, handle in enumerate(self._handles, 1):
                chunks, seen = self._parse_segment(handle, ordinal, chunks)
                header_seen |= seen
            if not header_seen or self._geometry is None or not self._tables:
                raise EWFError('EWF header, volume or chunk tables missing')
            declared_chunks, sectors_per_chunk, sector_size, sectors = self._geometry
            if chunks != declared_chunks:
                raise EWFError('EWF declared chunk count does not match supplied segments')
            self._chunk_count = chunks
            self.sector_size = sector_size
            self.chunk_size = sectors_per_chunk * sector_size
            self.size = sectors * sector_size
            if (self.size + self.chunk_size - 1) // self.chunk_size != chunks:
                raise EWFError('EWF sector and chunk geometry disagree')
            self._table_starts = [table.first_chunk for table in self._tables]
            # Validate final extent now, including a genuinely partial final chunk.
            self._read_chunk(chunks - 1)
        except BaseException:
            self.close()
            raise

    @property
    def cache_bytes(self) -> int:
        return self._cache_bytes

    def _read_at(self, segment: int, offset: int, size: int) -> bytes:
        handle = self._handles[segment]
        handle.seek(offset)
        data = handle.read(size)
        if len(data) != size:
            raise EWFError('Truncated EWF structure or chunk')
        return data

    @staticmethod
    def _checksum(data: bytes) -> None:
        if len(data) < 4 or zlib.adler32(data[:-4]) != struct.unpack('<I', data[-4:])[0]:
            raise EWFError('EWF checksum mismatch')

    @staticmethod
    def _decompress(data: bytes, limit: int) -> bytes:
        try:
            decoder = zlib.decompressobj()
            result = decoder.decompress(data, limit + 1)
            if len(result) > limit or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
                raise EWFError('EWF compressed data exceeds bounds or is incomplete')
            return result
        except zlib.error as error:
            raise EWFError('Corrupt EWF compressed data') from error

    def _parse_segment(self, handle: BinaryIO, ordinal: int, chunks: int) -> tuple[int, bool]:
        segment = ordinal - 1
        if not handle.readable() or not handle.seekable():
            raise EWFError('EWF requires readable seekable handles')
        handle.seek(0, io.SEEK_END)
        file_size = handle.tell()
        header = c_ewf.EWFHeader(self._read_at(segment, 0, len(c_ewf.EWFHeader)))
        if header.signature != b'EVF\x09\x0d\x0a\xff\x00' or header.segment_number != ordinal:
            raise EWFError('Unsupported, missing or reordered classic E01 segment')
        offset = len(c_ewf.EWFHeader)
        sectors_range = None
        header_seen = False
        while True:
            self._section_count += 1
            if self._section_count > MAX_SECTIONS:
                raise EWFError('EWF section metadata exceeds reader contract')
            if offset < len(c_ewf.EWFHeader) or offset + _DESCRIPTOR_BYTES > file_size:
                raise EWFError('EWF section outside segment')
            raw = self._read_at(segment, offset, _DESCRIPTOR_BYTES)
            self._checksum(raw)
            descriptor = c_ewf.EWFSectionDescriptor(raw)
            kind = descriptor.type.rstrip(b'\x00')
            start = offset + _DESCRIPTOR_BYTES
            if kind in (b'next', b'done'):
                expected = b'done' if ordinal == len(self._handles) else b'next'
                if (kind != expected or descriptor.next != offset or descriptor.size not in (0, _DESCRIPTOR_BYTES)
                        or start != file_size):
                    raise EWFError('Invalid EWF segment terminator or missing segments')
                return chunks, header_seen
            end = offset + descriptor.size
            if descriptor.size < _DESCRIPTOR_BYTES or end > file_size or descriptor.next != end:
                raise EWFError('Invalid EWF section bounds or cyclic section chain')
            if kind in (b'header', b'header2'):
                if end - start > MAX_HEADER_BYTES:
                    raise EWFError('EWF header metadata exceeds reader contract')
                self._decompress(self._read_at(segment, start, end - start), MAX_HEADER_BYTES)
                header_seen = True
            elif kind in (b'volume', b'disk', b'data'):
                # 'data' repeats the volume on later E01 segments.
                size = end - start
                if size not in (len(c_ewf.EWFVolumeSection), len(c_ewf.EWFVolumeSectionSpec)):
                    raise EWFError('Unsupported EWF volume geometry structure')
                raw_volume = self._read_at(segment, start, size)
                self._checksum(raw_volume)
                volume_type = c_ewf.EWFVolumeSection if size == len(c_ewf.EWFVolumeSection) else c_ewf.EWFVolumeSectionSpec
                volume = volume_type(raw_volume)
                geometry = (volume.chunk_count, volume.sector_count, volume.sector_size, volume.total_sector_count)
                if (not 1 <= volume.sector_size <= 65536 or not 1 <= volume.sector_count <= 32768
                        or not 1 <= volume.chunk_count <= 0xffffffff or volume.total_sector_count < 1
                        or volume.sector_count * volume.sector_size > MAX_CHUNK_BYTES):
                    raise EWFError('EWF chunk geometry exceeds reader contract')
                if self._geometry is not None and geometry != self._geometry:
                    raise EWFError('Conflicting EWF volume geometry')
                self._geometry = geometry
            elif kind == b'sectors':
                sectors_range = (start, end)
            elif kind == b'table':
                if self._geometry is None or len(self._tables) >= MAX_TABLES:
                    raise EWFError('EWF table metadata exceeds contract or precedes volume')
                if end - start < _TABLE_HEADER_BYTES + 8:
                    raise EWFError('Truncated EWF table header')
                raw_table = self._read_at(segment, start, _TABLE_HEADER_BYTES)
                self._checksum(raw_table)
                count, _, base, _, _ = struct.unpack('<IIQII', raw_table)
                if not 1 <= count <= MAX_TABLE_ENTRIES or chunks + count > self._geometry[0]:
                    raise EWFError('EWF table entry count exceeds reader contract')
                index_offset = start + _TABLE_HEADER_BYTES
                footer_offset = index_offset + count * 4
                if footer_offset + 4 > end:
                    raise EWFError('EWF chunk index extends outside table')
                # Verify the index without retaining a Python integer per entry.
                checksum = 1
                for position in range(index_offset, footer_offset, 65536):
                    checksum = zlib.adler32(self._read_at(segment, position, min(65536, footer_offset - position)), checksum)
                if checksum != struct.unpack('<I', self._read_at(segment, footer_offset, 4))[0]:
                    raise EWFError('EWF chunk index checksum mismatch')
                first = base + (struct.unpack('<I', self._read_at(segment, index_offset, 4))[0] & 0x7fffffff)
                if footer_offset + 4 <= first < end:
                    data_start, data_end = footer_offset + 4, end
                elif sectors_range is not None and sectors_range[0] <= first < sectors_range[1]:
                    data_start, data_end = sectors_range
                else:
                    raise EWFError('EWF chunk index points outside its data section')
                self._tables.append(_Table(segment, chunks, count, index_offset, base, data_start, data_end))
                chunks += count
            # Hashes, acquisition metadata and table2 mirror are not disk bytes.
            elif kind not in (b'table2', b'hash', b'digest', b'error2', b'session', b'ltree', b'analytical_data'):
                raise EWFError(f'Unsupported classic E01 section type: {kind!r}')
            offset = descriptor.next

    def _cache_put(self, key: tuple, value: bytes) -> bytes:
        if len(value) <= CACHE_BYTES:
            while self._cache and (self._cache_bytes + len(value) > CACHE_BYTES or len(self._cache) >= MAX_CACHE_ENTRIES):
                _, expired = self._cache.popitem(last=False)
                self._cache_bytes -= len(expired)
            self._cache[key] = value
            self._cache_bytes += len(value)
        return value

    def _entry(self, table: _Table, index: int) -> int:
        page = index * 4 // INDEX_PAGE_BYTES
        key = ('index', table.segment, table.index_offset, page)
        data = self._cache.get(key)
        if data is None:
            start = page * INDEX_PAGE_BYTES
            data = self._cache_put(key, self._read_at(table.segment, table.index_offset + start,
                                                   min(INDEX_PAGE_BYTES, table.count * 4 - start)))
        else:
            self._cache.move_to_end(key)
        return struct.unpack_from('<I', data, index * 4 % INDEX_PAGE_BYTES)[0]

    def _read_chunk(self, number: int) -> bytes:
        key = ('chunk', number)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        table = self._tables[bisect_right(self._table_starts, number) - 1]
        index = number - table.first_chunk
        entry = self._entry(table, index)
        start = table.base + (entry & 0x7fffffff)
        end = table.base + (self._entry(table, index + 1) & 0x7fffffff) if index + 1 < table.count else table.data_end
        if (not table.data_start <= start < end <= table.data_end
                or end - start > self.chunk_size + 65536):
            raise EWFError('EWF chunk extent exceeds data section or size bound')
        if index and table.base + (self._entry(table, index - 1) & 0x7fffffff) >= start:
            raise EWFError('EWF chunk offsets are not increasing')
        raw = self._read_at(table.segment, start, end - start)
        if entry & 0x80000000:
            data = self._decompress(raw, self.chunk_size)
        else:
            self._checksum(raw)
            data = raw[:-4]
        required = self.chunk_size if number + 1 < self._chunk_count else self.size - number * self.chunk_size
        if not required <= len(data) <= self.chunk_size:
            raise EWFError('EWF decoded chunk length disagrees with disk geometry')
        return self._cache_put(key, data)

    def read(self, length: int = -1) -> bytes:
        with self._lock:
            self._checkClosed()
            length = -1 if length is None else operator.index(length)
            if length < -1:
                raise ValueError('invalid read length')
            remaining = max(0, self.size - self._position)
            length = remaining if length == -1 else min(length, remaining)
            if length > MAX_READ_BYTES:
                raise EWFError(f'EWF read exceeds per-call limit of {MAX_READ_BYTES} bytes; read in chunks')
            result = bytearray()
            try:
                while length:
                    number, within = divmod(self._position, self.chunk_size)
                    count = min(length, self.chunk_size - within)
                    result.extend(self._read_chunk(number)[within:within + count])
                    self._position += count
                    length -= count
            except BaseException:
                self.close()
                raise
            return bytes(result)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        with self._lock:
            self._checkClosed()
            offset = operator.index(offset)
            if whence == io.SEEK_CUR:
                offset += self._position
            elif whence == io.SEEK_END:
                offset += self.size
            elif whence != io.SEEK_SET:
                raise ValueError('invalid whence')
            if offset < 0:
                raise ValueError('negative seek position')
            self._position = offset
            return offset

    def tell(self) -> int:
        self._checkClosed()
        return self._position

    def readable(self) -> bool:
        return not self.closed

    def close(self) -> None:
        # Input handles belong to image_reader's ExitStack, including on errors.
        with self._lock:
            self._cache.clear()
            self._cache_bytes = 0
            self._tables.clear()
            self._table_starts.clear()
            self._handles.clear()
            self.fh = []
            io.IOBase.close(self)


def open_ewf_container(handles: list[BinaryIO]) -> Container:
    """Open classic E01 segments; caller retains ownership of every input handle."""
    return BoundedEwfContainer(handles)
