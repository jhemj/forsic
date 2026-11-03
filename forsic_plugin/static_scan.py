"""Bounded static scans of selected workspace files; no execution or regex.

literal mode compares exact UTF-8 bytes. strings mode extracts maximal printable
ASCII runs (0x20..0x7e); previews are limited to 256 bytes, offsets cover the run.
Hashes describe captured bytes only unless full_file_sha256 is explicitly set.
"""
import hashlib
import json
import os
from pathlib import PurePosixPath
import stat
import sys
import time

MAX_FILES = 8
MAX_BYTES = 32 * 1024 * 1024
MAX_MATCHES = 100
MAX_SECONDS = 25


def properties():
    return {'paths': {'type': 'array', 'minItems': 1, 'maxItems': MAX_FILES,
                     'items': {'type': 'string'}, 'description': 'Selected regular files under /work; relative paths are workspace relative. No symlinks.'},
            'mode': {'type': 'string', 'enum': ['strings', 'literal']},
            'text': {'type': 'string', 'maxLength': 512, 'description': 'literal: exact UTF-8 bytes; strings: optional case-sensitive literal ASCII filter.'},
            'min_length': {'type': 'integer', 'minimum': 1, 'maximum': 256, 'default': 6},
            'max_bytes': {'type': 'integer', 'minimum': 1, 'maximum': MAX_BYTES, 'default': 8 * 1024 * 1024},
            'max_matches': {'type': 'integer', 'minimum': 1, 'maximum': MAX_MATCHES, 'default': 60},
            'max_seconds': {'type': 'integer', 'minimum': 1, 'maximum': MAX_SECONDS, 'default': 15}}


def validate(args):
    if not isinstance(args, dict) or set(args) - {'action', 'timeout_seconds', *properties()}:
        raise ValueError('static_scan accepts selected paths, mode, literal text and fixed bounds only')
    paths = args.get('paths')
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES:
        raise ValueError('Select one to eight workspace files')
    normalized = []
    for path in paths:
        if not isinstance(path, str) or not path or len(path) > 4096 or '\0' in path:
            raise ValueError('Invalid workspace path')
        p = PurePosixPath(path)
        if '..' in p.parts or (p.is_absolute() and p.parts[:2] != ('/', 'work')):
            raise ValueError('Paths must stay under /work without parent traversal')
        relative = p.relative_to('/work') if p.is_absolute() else p
        if not relative.parts or relative == PurePosixPath('.'):
            raise ValueError('Select a file, not the workspace root')
        normalized.append(relative.as_posix())
    if len(set(normalized)) != len(normalized):
        raise ValueError('Duplicate workspace paths are not allowed')
    mode = args.get('mode')
    if mode not in ('literal', 'strings'):
        raise ValueError('mode must be literal or strings')
    text = args.get('text', '')
    if not isinstance(text, str) or len(text) > 512 or (mode == 'literal' and not text):
        raise ValueError('literal mode requires nonempty text of at most 512 characters')
    text.encode('utf-8')
    if mode == 'strings' and any(not 32 <= ord(c) <= 126 for c in text):
        raise ValueError('strings filter must contain printable ASCII only')
    result = {'paths': normalized, 'mode': mode, 'text': text}
    for key, default, maximum in [('min_length', 6, 256), ('max_bytes', 8*1024*1024, MAX_BYTES),
                                  ('max_matches', 60, MAX_MATCHES), ('max_seconds', 15, MAX_SECONDS)]:
        value = args.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(key + ' exceeds its fixed positive integer bound')
        result[key] = value
    return result


def _identity(info):
    return {key: getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}


def _open(root, path):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = PurePosixPath(path).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        selected = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        if not stat.S_ISREG(os.fstat(selected).st_mode):
            os.close(selected)
            raise OSError('Selected input is not a regular file')
        return os.fdopen(selected, 'rb')
    finally:
        os.close(fd)


def scan(args, root='/work'):
    args = validate(args)
    deadline = time.monotonic() + args['max_seconds']
    files, used, returned = [], 0, 0
    needle = args['text'].encode('utf-8')
    for path in args['paths']:
        item = {'path': '/work/' + path, 'matches': [], 'state': 'partial',
                'capture_complete': False, 'scan_complete': False, 'full_file_sha256': None}
        files.append(item)
        if used >= args['max_bytes'] or returned >= args['max_matches'] or time.monotonic() >= deadline:
            item['stop_reason'] = 'budget_exhausted'
            continue
        data = bytearray()
        try:
            with _open(root, path) as stream:
                before = _identity(os.fstat(stream.fileno()))
                item['identity_before'] = before
                while len(data) < before['st_size'] and used < args['max_bytes'] and time.monotonic() < deadline:
                    block = stream.read(min(65536, before['st_size']-len(data), args['max_bytes']-used))
                    if not block:
                        break
                    data.extend(block)
                    used += len(block)
                after = _identity(os.fstat(stream.fileno()))
                item['identity_after'] = after
                with _open(root, path) as current:
                    path_after = _identity(os.fstat(current.fileno()))
                item['path_identity_after'] = path_after
                item['identity_unchanged'] = before == after == path_after
                complete = len(data) == before['st_size'] and item['identity_unchanged']
                item.update(capture_complete=complete, captured_bytes=len(data),
                            captured_sha256=hashlib.sha256(data).hexdigest())
                if complete:
                    item['full_file_sha256'] = item['captured_sha256']
                position, scanned = 0, True
                while position < len(data):
                    if returned >= args['max_matches'] or time.monotonic() >= deadline:
                        scanned = False
                        break
                    if args['mode'] == 'literal':
                        # Bounded windows preserve cancellation/time checks for large no-match files.
                        end = min(len(data), position + 65536 + len(needle)-1)
                        found = data.find(needle, position, end)
                        if found < 0:
                            position += 65536
                            continue
                        start, end = found, found + len(needle)
                        position = start + 1
                    else:
                        if not 32 <= data[position] <= 126:
                            position += 1
                            continue
                        start = position
                        while position < len(data) and 32 <= data[position] <= 126:
                            position += 1
                            if position % 4096 == 0 and time.monotonic() >= deadline:
                                scanned = False
                                break
                        if not scanned:
                            break
                        end = position
                        if end == len(data) and not complete:
                            scanned = False  # trailing printable run may continue in uncaptured bytes
                            break
                        if end-start < args['min_length'] or (needle and needle not in data[start:end]):
                            continue
                    item['matches'].append({'byte_start': start, 'byte_end': end,
                        'preview': bytes(data[start:min(end, start+256)]).decode('ascii' if args['mode']=='strings' else 'utf-8', 'replace'),
                        'preview_truncated': end-start > 256})
                    returned += 1
                item['scan_complete'] = complete and scanned
                item['state'] = ('matches' if item['matches'] else 'no_matches') if item['scan_complete'] else 'partial'
                if not item['scan_complete']:
                    item['stop_reason'] = 'identity_changed' if not item['identity_unchanged'] else 'scan_or_capture_budget'
        except FileNotFoundError:
            item.update(state='missing',captured_bytes=len(data),captured_sha256=hashlib.sha256(data).hexdigest())
        except OSError as exc:
            item.update(state='read_failed', error_type=type(exc).__name__,
                        captured_bytes=len(data),captured_sha256=hashlib.sha256(data).hexdigest())
    failed = any(f['state'] in ('missing', 'read_failed') for f in files)
    return {'schema': 'forsic.static-scan.v1', 'files': files, 'success': not failed,
            'coverage_complete': all(f['scan_complete'] for f in files), 'bytes_captured': used,
            'matches_returned': returned, 'representation': 'derived_static_scan', 'original_evidence': False,
            'meaning': 'Only selected retained workspace bytes; no execution, maliciousness or original-evidence attestation.',
            'limits': {'max_files': MAX_FILES, **{key: args[key] for key in ('max_bytes', 'max_matches', 'max_seconds')}}}


def main():
    try:
        result = scan(json.loads(sys.argv[1]))
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result['success'] else 2
    except (ValueError, OSError, IndexError) as exc:
        print(json.dumps({'error': str(exc), 'error_type': type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
