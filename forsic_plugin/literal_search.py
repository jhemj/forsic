"""Bounded UTF-8 literal search shared by host files and the isolated image reader.

Cursors are continuation data, not evidence signatures. They bind the query,
scope and current file metadata and carry only a short, unfinished-line state.
"""
import codecs
import hashlib
import json
import stat
import time


CHUNK = 64 * 1024
PREVIEW = 2000


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def file_identity(info):
    return {key: getattr(info, key, None) for key in
            ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
             'st_mtime', 'st_ctime')}


def _limit(args, name, default, maximum):
    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= maximum:
        raise ValueError(f'{name} must be greater than 0 and at most {maximum}')
    if name != 'max_seconds' and int(value) != value:
        raise ValueError(f'{name} must be an integer')
    return value if name == 'max_seconds' else int(value)


def _fresh_line(offset=0, line=1):
    return {'byte_offset': offset, 'line': line, 'line_start': offset,
            'preview': '', 'characters': 0, 'tail': '', 'matched': False,
            'decoder_bytes': ''}


def search_entries(entries, args, scope, path_key='path'):
    """Entries yield (name, stat callable, binary open callable, skip reason).

    Enumeration must be deterministic. Rewalking to the saved entry is not an
    index, and does not claim to detect changes to already returned evidence.
    """
    needle = args.get('text', '')
    if not isinstance(needle, str) or not needle or len(needle) > 512 or '\n' in needle or '\r' in needle:
        raise ValueError('Provide a single-line nonempty literal search of at most 512 characters')
    limits = {name: _limit(args, name, default, maximum) for name, default, maximum in (
        ('max_files', 500, 500), ('max_bytes', 8 * 1024 * 1024, 32 * 1024 * 1024),
        ('max_matches', 60, 60), ('max_seconds', 15, 25))}
    binding = fingerprint({'scope': scope, 'text': needle, 'match_type': 'casefold_literal_v2'})
    legacy_offset = max(0, int(args.get('offset', 0)))
    cursor = args.get('cursor')
    if cursor is not None:
        if not isinstance(cursor, dict) or len(json.dumps(cursor)) > 24000:
            raise ValueError('Invalid search cursor; pass next_cursor unchanged')
        payload = {k: v for k, v in cursor.items() if k != 'checksum'}
        if (cursor.get('version') != 1 or cursor.get('binding') != binding
                or cursor.get('checksum') != fingerprint(payload) or legacy_offset):
            raise ValueError('Search cursor does not match this query/scope; pass it unchanged without offset')
        index = cursor['entry_index']
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError('Invalid search cursor position')
    else:
        index = 0
    start_time = time.monotonic()
    matches, skips = [], []
    scanned = completed = byte_count = visited = directories = regular_seen = 0
    next_cursor = None
    stop_reason = None
    folded = needle.casefold()
    reached_cursor = cursor is None

    def continuation(number, name, identity, state, reason):
        nonlocal next_cursor, stop_reason
        payload = {'version': 1, 'binding': binding, 'entry_index': number,
                   'file_path': name, 'file_identity': identity, 'state': state}
        next_cursor = {**payload, 'checksum': fingerprint(payload)}
        stop_reason = reason

    def budget_reason():
        if byte_count >= limits['max_bytes']:
            return 'byte_limit'
        if len(matches) >= limits['max_matches']:
            return 'match_limit'
        if time.monotonic() - start_time >= limits['max_seconds']:
            return 'time_limit'
        return None

    for number, (name, get_stat, opener, skip_reason) in enumerate(entries):
        if number < index:
            continue
        reached_cursor = True
        if skip_reason:
            identity = {'entry_kind': skip_reason, 'stat': file_identity(get_stat()) if get_stat else None}
            if cursor is not None and number == index and (cursor['file_path'] != name or cursor['file_identity'] != identity):
                raise ValueError('Evidence traversal changed since the search cursor')
            reason = budget_reason() or ('file_limit' if visited >= limits['max_files'] else None)
            reason = reason or ('directory_limit' if directories >= 500 else None)
            if reason:
                continuation(number, name, identity, _fresh_line(), reason)
                break
            if skip_reason == 'directory':
                directories += 1
            else:
                skips.append({path_key: name, 'reason': skip_reason})
                visited += 1
            continue
        try:
            info = get_stat()
        except (OSError, ValueError) as exc:
            identity = {'stat_error': type(exc).__name__}
            if cursor is not None and number == index and (cursor['file_path'] != name or cursor['file_identity'] != identity):
                raise ValueError('Search cursor file is no longer readable') from exc
            reason = budget_reason() or ('file_limit' if visited >= limits['max_files'] else None)
            if reason:
                continuation(number, name, identity, _fresh_line(), reason)
                break
            skips.append({path_key: name, 'reason': 'stat_failed', 'error_type': type(exc).__name__})
            visited += 1
            continue
        identity = file_identity(info)
        if cursor is not None and number == index:
            if cursor['file_path'] != name or cursor['file_identity'] != identity:
                raise ValueError('Evidence changed since the search cursor; start a new search')
            state = dict(cursor['state'])
        else:
            state = _fresh_line()
        reason = budget_reason() or ('file_limit' if visited >= limits['max_files'] else None)
        if reason:
            continuation(number, name, identity, state, reason)
            break
        if not stat.S_ISREG(info.st_mode):
            skips.append({path_key: name, 'reason': 'not_regular_file'})
            visited += 1
            continue
        regular_seen += 1
        if cursor is None and regular_seen <= legacy_offset:
            continue
        visited += 1
        scanned += 1
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        decoder.setstate((bytes.fromhex(state['decoder_bytes']), 0))
        initial_offset = state['byte_offset']
        if not 0 <= initial_offset <= info.st_size or state['line'] < 1:
            raise ValueError('Invalid search cursor byte/line position')
        try:
            with opener() as stream:
                stream.seek(initial_offset)
                while True:
                    reason = budget_reason()
                    if reason and state['byte_offset'] < info.st_size:
                        state['decoder_bytes'] = decoder.getstate()[0].hex()
                        continuation(number, name, identity, state, reason)
                        break
                    allowance = min(CHUNK, limits['max_bytes'] - byte_count)
                    raw = stream.readline(allowance) if allowance > 0 and state['byte_offset'] < info.st_size else b''
                    byte_count += len(raw)
                    state['byte_offset'] += len(raw)
                    if not raw and state['byte_offset'] < info.st_size:
                        skips.append({path_key: name, 'reason': 'unexpected_eof',
                                      'byte_offset': state['byte_offset'], 'expected_bytes': info.st_size})
                        break
                    if b'\x00' in raw:
                        skips.append({path_key: name, 'reason': 'binary_content',
                                      'byte_offset': state['byte_offset'] - len(raw)})
                        break
                    ended = not raw or raw.endswith(b'\n')
                    text = decoder.decode(raw, final=not raw)
                    if raw.endswith(b'\n'):
                        text = text[:-1]  # LF-delimited lines; CR remains searchable.
                    state['characters'] += len(text)
                    state['preview'] = (state['preview'] + text)[:PREVIEW]
                    combined = state['tail'] + text.casefold()
                    state['matched'] = state['matched'] or folded in combined
                    state['tail'] = combined[-(len(folded) - 1):] if len(folded) > 1 else ''
                    if ended:
                        if state['matched']:
                            matches.append({path_key: name, 'line': state['line'],
                                            'byte_start': state['line_start'], 'byte_end': state['byte_offset'],
                                            'text': state['preview'].rstrip('\r'),
                                            'line_truncated': state['characters'] > PREVIEW})
                        state = _fresh_line(state['byte_offset'], state['line'] + 1)
                        if not raw or state['byte_offset'] >= info.st_size:
                            completed += 1
                            break
                    if not raw:
                        break
            if file_identity(get_stat()) != identity:
                raise ValueError('Evidence changed during search; discard this page and start a new search')
        except OSError as exc:
            skips.append({path_key: name, 'reason': 'read_failed', 'error_type': type(exc).__name__})
            next_cursor = None
        if next_cursor is not None:
            break
    if not reached_cursor:
        raise ValueError('Search cursor file is no longer in this scope')
    exhausted = next_cursor is None
    return {'matches': matches, 'files_scanned': scanned, 'files_completed': completed,
            'files_skipped': len(skips), 'skips': skips, 'bytes_scanned': byte_count,
            'next_cursor': next_cursor, 'stop_reason': stop_reason, 'scan_exhausted': exhausted,
            'next_offset': None, 'search_text': needle, 'match_type': 'casefold_literal',
            'search_binding_sha256': binding, 'coverage_complete': exhausted and not skips and cursor is None and legacy_offset == 0,
            'encoding': 'UTF-8 replacement; LF-delimited lines',
            'limit_note': 'Time is checked between bounded reads and traversal steps, not a hard OS I/O deadline. '
                          'Directories have a 10,000-entry discovery cap; narrow any skipped directory.',
            'scope_note': 'This page covers only the bytes scanned. Continue with next_cursor, not next_offset. '
                          'A resumed page alone does not establish complete scope coverage. Skips remain unexamined; '
                          'no match is not absence of execution.'}
