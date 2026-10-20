"""Offline selected-file triage helper; executed only by the native container.

Only result.json is safe for the host allow-list projector. All other outputs
are private, including diagnostics and complete parser/detector rows.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import time

SCHEMA = 'forsic.triage.v1'
OUTPUT_LIMIT = 8 * 1024**2
MAX_INDEX_CANDIDATES = 20000
TOOL_ROOT = Path('/opt/forensic-tools')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)


def child_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_LIMIT, OUTPUT_LIMIT))


def run(argv, stem, deadline):
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        return None, 'deadline_reached'
    with open(str(stem)+'.stdout', 'xb') as out, open(str(stem)+'.stderr', 'xb') as err:
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                timeout=remaining, check=False, preexec_fn=child_limits)
            return result.returncode, None
        except subprocess.TimeoutExpired:
            return None, 'engine_timeout'
        except OSError:
            return None, 'engine_unavailable'


def rule_metadata(path):
    # Deliberately a small scalar reader, not a YAML interpreter. Omit complex
    # YAML/quoted escapes rather than guessing. These bytes were hash verified.
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        match = re.fullmatch(r'(title|id|level):[ \t]+([^\r\n]{1,240})', line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if value[:1] in ('"', "'") and value[-1:] == value[:1]:
            value = value[1:-1]
        if value in ('|', '>') or '\\' in value or len(value) > 180:
            continue
        values[key] = value
    ident = values.get('id', '')
    if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', ident):
        return None
    result = {'rule_id': ident, 'rule_sha256': sha(path)}
    if 'title' in values:
        result['rule_title'] = values['title']
    if values.get('level') in ('informational', 'low', 'medium', 'high', 'critical'):
        result['severity'] = values['level']
    return result


def engine(profile):
    identity = {'helper_sha256': sha(Path(__file__))}
    if profile == 'linux_persistence':
        return identity, None
    if profile == 'linux_login_parser':
        parser = Path('/opt/forsic/login_records.py')
        if not parser.is_file():
            return identity, 'engine_unavailable'
        identity['binary_sha256'] = sha(parser)
        return identity, None
    binary = shutil.which('hayabusa')
    manifest = TOOL_ROOT/'rule-config-sha256.json'
    lock = TOOL_ROOT/'forensic-tools.lock.json'
    if not binary or not manifest.is_file() or not lock.is_file():
        return identity, 'engine_unavailable'
    identity.update(binary_sha256=sha(Path(binary)), rules_manifest_sha256=sha(manifest), lock_sha256=sha(lock))
    try:
        hashes = json.loads(manifest.read_bytes())
        pinned = json.loads(lock.read_bytes())
        if pinned['packages']['hayabusa']['version'] != '4.2.0':
            return identity, 'rules_unverified'
        selected = {k: v for k, v in hashes.items() if k.startswith(('hayabusa/rules/', 'hayabusa/config/'))}
        actual = {str(p.relative_to(TOOL_ROOT)) for directory in ('hayabusa/rules', 'hayabusa/config')
            for p in (TOOL_ROOT/directory).rglob('*') if p.is_file()}
        if not selected or actual != set(selected):
            return identity, 'rules_unverified'
        for name, expected in selected.items():
            path = TOOL_ROOT/name
            if path.is_symlink() or not path.is_relative_to(TOOL_ROOT) or sha(path) != expected:
                return identity, 'rules_unverified'
        probe = subprocess.run([binary, 'dfir-timeline', '--help'], capture_output=True, timeout=3, check=False)
        if probe.returncode or not re.search(rb'Hayabusa v4\.2\.0(?:\s|$)', probe.stdout+probe.stderr):
            return identity, 'engine_unavailable'
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return identity, 'rules_unverified'
    metadata = {}
    for name in selected:
        if name.endswith(('.yml', '.yaml')):
            entry = rule_metadata(TOOL_ROOT/name)
            if entry is not None:
                ident = entry['rule_id'].lower()
                # Duplicate rule IDs do not identify an authoritative rule.
                metadata[ident] = None if ident in metadata else entry
    identity['_rule_catalog'] = {k: v for k, v in metadata.items() if v is not None}
    return identity, None


def linux(item, request, directory, deadline):
    stem = directory/item['input_id']
    code, reason = run(['python', '-B', '/opt/forsic/login_records.py', item['path'],
        '--format', request['login_format'], '--limit', str(request['record_limit'])], stem, deadline)
    scope = {'input_id': item['input_id'], 'state': 'failed', 'candidate_count': 0,
        'counts_complete': False, 'reason_codes': [reason] if reason else []}
    candidates = []
    if reason:
        return scope, candidates
    try:
        source = summary = None
        with open(str(stem)+'.stdout', 'rb') as stream:
            for raw in stream:
                row = json.loads(raw)
                if row.get('kind') == 'source':
                    source = row
                elif row.get('kind') == 'summary':
                    summary = row
                elif row.get('kind') == 'record':
                    scope['candidate_count'] += 1
                    if len(candidates) < request['candidate_limit']:
                        candidates.append({'input_id': item['input_id'], 'ordinal': scope['candidate_count'],
                            'record_index': row['record_index'], 'record_type': row['ut_type_raw'],
                            'byte_start': row['locator']['byte_start'], 'byte_end': row['locator']['byte_end']})
        if (not source or not summary or source['source_sha256'] != item['sha256']
                or summary['records_emitted'] != scope['candidate_count'] or code not in (0, 2, 3)):
            raise ValueError('Incomplete parser output')
        scope.update(records_parsed=summary['records_emitted'], records_available=summary['complete_records_available'],
            records_with_warnings=summary['records_with_warnings'], state='complete', counts_complete=True)
        if summary['limited']:
            scope['reason_codes'].append('record_limit')
            scope['counts_complete'] = False
        if summary['records_with_warnings']:
            scope['reason_codes'].append('record_warnings')
        if summary['truncated_tail']:
            scope['reason_codes'].append('truncated_tail')
        if scope['reason_codes']:
            scope['state'] = 'partial'
    except (OSError, ValueError, KeyError, TypeError):
        scope.update(state='failed', counts_complete=False, reason_codes=['output_invalid'])
    return scope, candidates


# Configuration structure only. Values remain opaque and never become code.
MAX_PERSISTENCE_LINES = 20000
CRON_MACROS = frozenset(('@reboot', '@yearly', '@annually', '@monthly', '@weekly', '@daily', '@midnight', '@hourly'))


def persistence(item, request, directory, deadline):
    scope = {'input_id': item['input_id'], 'state': 'complete', 'candidate_count': 0,
        'counts_complete': True, 'reason_codes': []}
    candidates = []
    raw = Path(item['path']).read_bytes()
    try:
        if b'\0' in raw:
            raise UnicodeError()
        raw.decode('utf-8-sig')
    except UnicodeError:
        scope.update(state='skipped', counts_complete=False, reason_codes=['unsupported_encoding'])
        return scope, candidates
    fmt = item.get('triage_format')
    if fmt not in ('cron_user', 'cron_system', 'systemd_unit'):
        scope.update(state='skipped', counts_complete=False, reason_codes=['unsupported_input'])
        return scope, candidates
    offset, section, pending = 0, False, None
    def partial(reason):
        scope.update(state='partial', counts_complete=False)
        if reason not in scope['reason_codes']:
            scope['reason_codes'].append(reason)
    def emit(kind, start, end, first, last, empty=False):
        scope['candidate_count'] += 1
        if len(candidates) < request['candidate_limit']:
            candidates.append({'input_id': item['input_id'], 'ordinal': scope['candidate_count'],
                'entry_kind': kind, 'value_empty': empty, 'byte_start': start, 'byte_end': end,
                'line_start': first, 'line_end': last})
    # splitlines() recognizes vertical controls as lines; only LF is a source line.
    lines = raw.split(b'\n', MAX_PERSISTENCE_LINES)
    for index, physical in enumerate(lines):
        if index == len(lines)-1 and not physical:
            break
        line = physical + (b'\n' if offset+len(physical) < len(raw) else b'')
        start, offset = offset, offset+len(line)
        number = index+1
        if number > MAX_PERSISTENCE_LINES:
            partial('line_limit'); break
        if time.monotonic() >= deadline:
            partial('deadline_reached'); break
        text = line.decode('utf-8-sig' if index == 0 else 'utf-8').strip()
        if any(ord(c) < 32 and c not in '\t\r\n' for c in text):
            partial('unsupported_syntax'); continue
        if not text or text.startswith(('#', ';') if fmt == 'systemd_unit' else ('#',)):
            continue
        if fmt != 'systemd_unit':
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*\s*=.*', text):
                continue
            fields = text.split(None, 1 if fmt == 'cron_user' else 2) if text.startswith('@') else text.split(None, 5 if fmt == 'cron_user' else 6)
            required = (2 if fmt == 'cron_user' else 3) if text.startswith('@') else (6 if fmt == 'cron_user' else 7)
            valid = len(fields) == required
            if text.startswith('@'):
                valid = valid and fields[0] in CRON_MACROS
            else:
                # Syntax subset, not schedule evaluation or dialect inference.
                valid = valid and all(re.fullmatch(r'[A-Za-z0-9*/?,#~L-]+', field) for field in fields[:5])
            if valid:
                emit('cron_macro' if text.startswith('@') else 'cron_schedule', start, offset, number, number)
            else:
                partial('unsupported_syntax')
            continue
        if pending is not None:
            # Comments/blank lines above do not terminate a continuation.
            if text.endswith('\\'):
                continue
            emit('systemd_directive', pending[0], offset, pending[1], number, False)
            pending = None
            continue
        if re.fullmatch(r'\[[A-Za-z][A-Za-z0-9]*\]', text):
            section = True
            continue
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9]*)\s*=(.*)', text)
        if not section or not match:
            partial('unsupported_syntax'); continue
        value = match[2].strip()
        if value.endswith('\\'):
            pending = (start, number)
        else:
            emit('systemd_directive', start, offset, number, number, not value)
    if pending is not None:
        partial('unsupported_syntax')
    return scope, candidates


def windows(item, request, directory, deadline):
    stem = directory/item['input_id']
    scope = {'input_id': item['input_id'], 'state': 'failed', 'candidate_count': 0,
        'counts_complete': False, 'reason_codes': []}
    candidates = []
    with open(item['path'], 'rb') as stream:
        if stream.read(8) != b'ElfFile\x00':
            scope.update(state='skipped', reason_codes=['unsupported_input'])
            return scope, candidates
    output = Path(str(stem)+'.jsonl')
    code, reason = run(['hayabusa', 'dfir-timeline', '-f', item['path'],
        '-r', str(TOOL_ROOT/'hayabusa/rules'), '-c', str(TOOL_ROOT/'hayabusa/rules/config'),
        '-w', '-t', 'jsonl', '-p', 'super-verbose', '-O', '--threads', '2', '-K', '-b', '-F', '-o', str(output)], stem, deadline)
    if reason:
        scope['reason_codes'] = [reason]
        return scope, candidates
    try:
        if code != 0 or not output.is_file() or output.stat().st_size > OUTPUT_LIMIT:
            scope['reason_codes'] = ['engine_failed']
            return scope, candidates
        with output.open('rb') as stream:
            for raw in stream:
                row = json.loads(raw)
                if (not isinstance(row, dict) or not isinstance(row.get('RuleID'), str)
                        or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', row['RuleID'])
                        or not (type(row.get('RecordID')) is int and row['RecordID'] >= 0
                            or isinstance(row.get('RecordID'), str) and re.fullmatch(r'\d{1,18}', row['RecordID']))):
                    raise ValueError('Invalid row')
                scope['candidate_count'] += 1
                if len(candidates) >= request['candidate_limit']:
                    continue
                candidate = {'input_id': item['input_id'], 'ordinal': scope['candidate_count']}
                record_id = row.get('RecordID')
                if type(record_id) is int and record_id >= 0 or isinstance(record_id, str) and re.fullmatch(r'\d{1,18}', record_id):
                    candidate['event_record_id'] = int(record_id)
                if isinstance(row.get('RuleID'), str):
                    candidate['rule_id'] = row['RuleID']
                    trusted = request.get('_rule_catalog', {}).get(row['RuleID'].lower())
                    if trusted:
                        candidate.update(trusted)
                candidates.append(candidate)
        # Hayabusa may exit successfully while skipping records/rules. Until its
        # unstructured diagnostics can be verified, report selected EVTX scope
        # as partial even with zero candidates. Never infer absence/completeness.
        scope.update(state='partial', counts_complete=True, reason_codes=['engine_diagnostics'])
    except (OSError, ValueError, KeyError, TypeError):
        scope.update(state='failed', counts_complete=False, reason_codes=['output_invalid'])
    return scope, candidates


def execute(request, directory):
    deadline = time.monotonic()+max(1, request['timeout_seconds']-1)
    identity, unavailable = engine(request['profile'])
    request = {**request, '_rule_catalog': identity.pop('_rule_catalog', {})}
    body = {'schema': SCHEMA, 'scan_id': request['scan_id'], 'profile': request['profile'],
        'engine_identity': identity, 'inputs': [], 'candidates': [], 'candidate_index': []}
    index_limit = min(MAX_INDEX_CANDIDATES, request.get('candidate_index_limit', request['candidate_limit']))
    parser_request = {**request, 'candidate_limit': index_limit}
    for item in request['inputs']:  # Deliberately sequential: no second executor or scheduler.
        if unavailable:
            scope, candidates = {'input_id': item['input_id'], 'state': 'skipped', 'candidate_count': 0,
                'counts_complete': False, 'reason_codes': [unavailable]}, []
        else:
            try:
                if sha(Path(item['path'])) != item['sha256']:
                    raise ValueError('Changed staged bytes')
                profile = {'linux_login_parser': linux, 'linux_persistence': persistence, 'windows_evtx': windows}[request['profile']]
                scope, candidates = profile(item, parser_request, directory, deadline)
            except (OSError, ValueError, KeyError, TypeError):
                scope, candidates = {'input_id': item['input_id'], 'state': 'failed', 'candidate_count': 0,
                    'counts_complete': False, 'reason_codes': ['output_invalid']}, []
        body['inputs'].append(scope)
        remaining = request['candidate_limit']-len(body['candidates'])
        body['candidates'].extend(candidates[:remaining])
        body['candidate_index'].extend(candidates[:max(0, index_limit-len(body['candidate_index']))])
    return body


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    request = json.loads(Path(args.request).read_bytes())
    directory = Path(args.request).parent
    body = execute(request, directory)
    write(directory/'result.json', body)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
