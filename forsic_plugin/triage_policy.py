"""Bounded selected-file triage through the existing native container boundary.

Raw parser/scanner rows never enter the event ledger. This module produces
candidates only; adoption remains the existing explicit question-note operation.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from . import sensitive_policy as storage
from .execution_contract import ExecutionFailure

SCHEMA = 'forsic.triage.v1'
MAX_TARGETS = 8
MAX_BYTES = storage.MAX_JOB_BYTES
MAX_CANDIDATES = 20
MAX_INDEX_CANDIDATES = 20000
MAX_INDEX_BYTES = 8 * 1024**2
# Each 384-byte record has at most 324 string bytes (six JSON characters
# each), 768 raw-hex characters and bounded fixed/numeric fields: <4096
# serialized bytes. 2000 records leave room for source/summary below 8 MiB.
MAX_RECORDS = 2000
PRIVATE_NAME = '.triage-artifacts'
PERSISTENCE_FORMATS = ('cron_user', 'cron_system', 'systemd_unit')
PROFILES = {
    'linux_persistence': {
        'target_os': 'linux', 'purposes': ['quick', 'persistence'],
        'applicability': 'Explicitly selected cron_user, cron_system or systemd_unit UTF-8 configuration files.',
        'kind': 'configuration_candidates', 'input': 'Complete retained files/exports with caller-asserted triage_format per target.',
        'engine': 'forsic.triage_scan persistence structure version 1',
        'rules': 'Structural entries only; no maliciousness rules, command execution or environment expansion.',
        'return': 'Fixed entry labels, exact byte/line ranges, counts and source/hash recheck calls; no command/value text.',
        'limitations': 'Configured text only; effective configuration, enablement, execution, authorization and maliciousness are not established. Unsupported syntax is partial scope.',
        'cost_limits': {'selected_files': MAX_TARGETS, 'combined_input_bytes': MAX_BYTES,
            'timeout_seconds': 300, 'returned_candidates': MAX_CANDIDATES,
            'lines_per_file': 20000, 'execution': 'sequential'},
    },
    'windows_evtx': {
        'target_os': 'windows', 'purposes': ['quick', 'account', 'persistence'],
        'applicability': 'Explicitly selected Windows EVTX files; rule detection candidates.',
        'kind': 'rule_detection', 'input': 'Complete retained EVTX file or complete retained image export; binary bytes preserved.',
        'engine': 'hayabusa 4.2.0',
        'rules': 'Installed pinned release bundle; verify every installed rule/config hash. No updates or custom rules.',
        'return': 'Counts, hash-verified installed rule title/severity/SHA when available, UUID RuleID, numeric RecordID and source/hash recheck calls; no event text.',
        'limitations': 'Hayabusa diagnostics prevent complete coverage; event identity and rule conditions require source recheck.',
        'cost_limits': {'selected_files': MAX_TARGETS, 'combined_input_bytes': MAX_BYTES,
            'timeout_seconds': 300, 'returned_candidates': MAX_CANDIDATES,
            'output_bytes_per_engine_file': 8 * 1024**2, 'execution': 'sequential'},
    },
    'linux_login_parser': {
        'target_os': 'linux', 'purposes': ['quick', 'account'],
        'applicability': 'Explicit Linux/glibc x86 little-endian 384-byte utmp/wtmp/btmp ABI assertion; not an anomaly detector.',
        'kind': 'record_parser', 'input': 'Complete retained uncompressed login record file or complete retained image export.',
        'engine': 'forsic.login_records version 1', 'rules': 'No detection rules; numeric record types and parser warnings only.',
        'return': 'Counts, fixed record type labels, byte ranges and source/hash recheck calls; no users, hosts or raw records.',
        'limitations': 'ABI is caller asserted; no authentication outcome inference, rotation discovery or session correlation.',
        'cost_limits': {'selected_files': MAX_TARGETS, 'combined_input_bytes': MAX_BYTES,
            'timeout_seconds': 300, 'returned_candidates': MAX_CANDIDATES,
            'records_per_file': MAX_RECORDS, 'output_bytes_per_engine_file': 8 * 1024**2,
            'execution': 'sequential'},
    },
}


def catalog():
    return {'action': 'triage', 'profiles': {k: dict(v) for k, v in PROFILES.items()},
        'recommendation_guidance': {k: v for k, v in recommendations('unknown', 'quick').items()
            if k in ('input_requirement', 'availability', 'skip_guidance')},
        'initial_recommendations': {os_name: {purpose: {k: v for k, v in recommendations(os_name, purpose).items()
            if k in ('profiles', 'state')}
            for purpose in ('quick', 'account', 'persistence', 'data_movement')}
            for os_name in ('unknown', 'windows', 'linux', 'macos')},
        'unavailable_profiles': {'yara_x': 'not_implemented_requires_reviewed_rules_contract',
            'chainsaw': 'not_implemented', 'mac_apt': 'not_implemented'},
        'sensitive_data': {'action': 'sensitive_scan', 'meaning': 'Separate existing private masked profiles; never use generic triage for secrets/PII.'},
        'cost_limits': {'selected_files': MAX_TARGETS, 'combined_input_bytes': MAX_BYTES,
            'returned_candidates': MAX_CANDIDATES, 'login_records_per_file': MAX_RECORDS,
            'timeout_seconds_max': 300,
            'timeout_scope': 'container_runtime_only; input staging and hash validation are separate byte-bounded preprocessing',
            'processes': 'one engine at a time; conversation workspace lock',
            'engine_output_bytes_per_file': 8 * 1024**2, 'memory_bytes': 8 * 1024**3, 'cpus': 2}}


def recommendations(target_os, purpose):
    """Descriptive starting points, never evidence discovery or an execution gate."""
    if target_os not in ('unknown', 'windows', 'linux', 'macos') or purpose not in ('quick', 'account', 'persistence', 'data_movement'):
        raise ValueError('Choose an explicit supported OS label and investigation purpose')
    profiles = [name for name, profile in PROFILES.items()
        if profile['target_os'] == target_os and purpose in profile['purposes']]
    return {'target_os': target_os, 'purpose': purpose, 'profiles': profiles,
        'state': 'os_identification_required' if target_os == 'unknown' else
            'runtime_and_input_check_required' if profiles else 'no_registered_profile',
        'input_requirement': 'Explicit complete retained files/exports; confirm format and Linux ABI where applicable.',
        'availability': 'Declared support only; check installed engine and actual evidence before execution.',
        'skip_guidance': 'Agent may add, omit or defer checks. Record important omissions and reasons in existing question notes.',
        'unavailable_reason': None if profiles else 'Identify OS first; no Windows fallback.' if target_os == 'unknown' else
            'No registered initial profile for this OS/purpose; use existing specialist tools when available.'}


def properties():
    target = storage.properties()['targets']
    target = {**target, 'maxItems': MAX_TARGETS,
        'description': 'Explicit retained evidence_file or workspace_export targets with exact source version; no directory/image/default full scan.'}
    target['items'] = {**target['items'], 'properties': {**target['items']['properties'],
        'kind': {'type': 'string', 'enum': ['evidence_file', 'workspace_export']},
        'triage_format': {'type': 'string', 'enum': list(PERSISTENCE_FORMATS)}},
        'required': ['kind', 'pointer'],
        'description': 'triage requires kind=evidence_file or workspace_export, pointer and source_handle or source_id/source_version. Defaults format=text and encoding=auto preserve binary bytes, not a file-format assertion. linux_persistence also requires triage_format.'}
    return {'profile': {'type': 'string', 'enum': list(PROFILES)},
        'targets': target,
        'login_format': {'type': 'string', 'enum': ['linux-glibc-x86-le-384']},
        'candidate_limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_CANDIDATES},
        'record_limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_RECORDS},
        'resume_ref': {'type': 'string', 'maxLength': 32}, **query_properties()}


def validate(args):
    if set(args) - {'action', 'profile', 'targets', 'login_format', 'candidate_limit', 'record_limit', 'timeout_seconds', 'resume_ref'}:
        raise ValueError('triage accepts one profile, retained targets, explicit login_format and bounded limits only')
    profile = args.get('profile')
    if profile not in PROFILES:
        raise ValueError('Select a registered triage profile')
    submitted_targets = args.get('targets')
    if not isinstance(submitted_targets, list) or any(not isinstance(t, dict) for t in submitted_targets):
        raise ValueError('Select retained targets')
    for index, target in enumerate(submitted_targets):
        prefix = f'targets[{index}]'
        for field in ('kind', 'pointer'):
            if field not in target:
                raise ValueError(f'{prefix}.{field} is required for triage')
        if 'source_handle' not in target:
            for field in ('source_id', 'source_version'):
                if field not in target:
                    raise ValueError(f'{prefix}.{field} is required without source_handle')
        if target['kind'] not in ('evidence_file', 'workspace_export'):
            raise ValueError(f'{prefix}.kind must be evidence_file or workspace_export')
        if profile == 'linux_persistence' and target.get('triage_format') not in PERSISTENCE_FORMATS:
            raise ValueError(f'{prefix}.triage_format must be cron_user, cron_system or systemd_unit for linux_persistence')
    formats = [t.get('triage_format') for t in submitted_targets]
    if profile != 'linux_persistence' and any('triage_format' in t for t in submitted_targets):
        raise ValueError('triage_format applies only to linux_persistence')
    checked = storage.validate({**{k: v for k, v in args.items() if k in ('timeout_seconds', 'resume_ref')},
        'targets': [{k: v for k, v in t.items() if k != 'triage_format'} for t in submitted_targets]})
    if profile == 'linux_persistence':
        checked['targets'] = [{**t, 'triage_format': f} for t, f in zip(checked['targets'], formats)]
    if len(checked['targets']) > MAX_TARGETS or any(t['kind'] not in ('evidence_file', 'workspace_export') for t in checked['targets']):
        raise ValueError('Select 1 to 8 complete retained files or exports, never retained text or a full image')
    if any(t['encoding'] != 'auto' or t['format'] != 'text' for t in checked['targets']):
        raise ValueError('Triage stages exact binary bytes; encoding and structured text reinterpretation are unsupported')
    if profile == 'linux_login_parser':
        if args.get('login_format') != 'linux-glibc-x86-le-384':
            raise ValueError('Assert the supported Linux login ABI with login_format')
    elif 'login_format' in args or 'record_limit' in args:
        raise ValueError('login_format and record_limit apply only to linux_login_parser')
    result = {**args, 'targets': checked['targets'], 'timeout_seconds': checked['timeout_seconds']}
    for key, maximum in (('candidate_limit', MAX_CANDIDATES), ('record_limit', MAX_RECORDS)):
        value = args.get(key, maximum)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f'{key} must be an integer from 1 to {maximum}')
        if key != 'record_limit' or profile == 'linux_login_parser':
            result[key] = value
    return result


def _base(case, *, create=True):
    # Apply the same ownership, overlap and no-symlink checks as sensitive storage,
    # while keeping separate artifacts and avoiding its comparison key/group state.
    output = Path(case.config['output_root']).absolute()
    storage._no_symlinks(output)
    if output.resolve() != case.output or output.is_relative_to(case.root):
        raise ValueError('Triage storage overlaps evidence')
    base = output / PRIVATE_NAME
    if base.is_relative_to(case.root) or case.root.is_relative_to(base):
        raise ValueError('Triage storage overlaps evidence')
    storage._no_symlinks(base)
    if create:
        base.mkdir(mode=0o700, exist_ok=True)
    info = base.stat()
    if not base.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Triage storage requires an owned private directory')
    return base


def _execution(value):
    result = storage._execution(value)
    if value.get('state') == 'timed_out':
        result['state'] = 'timed_out'
    return result


def _failure(code, execution=None, *, stage='admission'):
    return {'schema': SCHEMA, 'action': 'triage', 'representation': 'derived_triage_candidates',
        'original_evidence': False, 'coverage_not_established': True,
        'adoption': 'candidates_only_explicit_source_recheck_and_note_adoption_required',
        'coverage': {'state': 'failed'}, 'candidates': [],
        'error': 'Selected triage did not complete; inspect retained execution before retrying.',
        'error_code': code, 'failure_stage': stage,
        'execution': _execution(execution or {'state': 'not_started', 'termination_confirmed': True})}


def _project(body, inputs, args, scan_id, *, candidate_limit=None):
    """No raw strings from an engine, diagnostics, event values or filenames."""
    if (not isinstance(body, dict) or body.get('schema') != SCHEMA or body.get('scan_id') != scan_id
            or body.get('profile') != args['profile']):
        raise ValueError('Invalid triage identity')
    if (not isinstance(body.get('inputs'), list) or not isinstance(body.get('candidates'), list)
            or any(not isinstance(row, dict) for row in (*body['inputs'], *body['candidates']))):
        raise ValueError('Invalid triage result shape')
    by_id = {item['input_id']: item for item in inputs}
    scopes = []
    counts = ('candidate_count', 'records_parsed', 'records_available', 'records_with_warnings')
    def numbers(value):
        return {k: value[k] for k in counts if type(value.get(k)) is int and 0 <= value[k] <= 10**12}
    reasons = {'record_limit', 'record_warnings', 'truncated_tail', 'engine_diagnostics', 'engine_timeout',
        'engine_failed', 'engine_unavailable', 'rules_unverified', 'unsupported_input', 'output_invalid', 'deadline_reached',
        'unsupported_syntax', 'line_limit', 'unsupported_encoding'}
    for value in body.get('inputs', []):
        key = value.get('input_id')
        if key not in by_id or key in {s['input_id'] for s in scopes}:
            raise ValueError('Invalid triage scope')
        state = value.get('state')
        if state not in ('complete', 'partial', 'failed', 'skipped'):
            raise ValueError('Invalid scope state')
        scopes.append({'input_id': key, 'state': state, **numbers(value),
            'counts_complete': value.get('counts_complete') is True,
            'reason_codes': sorted({r if r in reasons else 'output_invalid' for r in value.get('reason_codes', [])}),
            'source_recheck': {'source_ref': by_id[key]['source_ref'], 'sha256': by_id[key]['sha256'],
                'bytes': by_id[key]['bytes'], 'selection_kind': by_id[key]['selection_kind']}})
    if len(scopes) != len(inputs):
        raise ValueError('Missing selected scope')
    candidates, seen_candidates = [], set()
    scope_counts = {scope['input_id']: scope.get('candidate_count', 0) for scope in scopes}
    observed = sum(s.get('candidate_count', 0) for s in scopes)
    for row in body.get('candidates', [])[:candidate_limit or args['candidate_limit']]:
        key, ordinal = row.get('input_id'), row.get('ordinal')
        if (key not in by_id or type(ordinal) is not int or not 1 <= ordinal <= scope_counts[key]
                or (key, ordinal) in seen_candidates):
            raise ValueError('Invalid candidate locator')
        seen_candidates.add((key, ordinal))
        locator = {k: row[k] for k in ('byte_start', 'byte_end', 'record_index', 'event_record_id', 'record_type', 'line_start', 'line_end')
            if type(row.get(k)) is int and 0 <= row[k] <= 2**63-1}
        if args['profile'] == 'linux_login_parser' and not (
            locator.get('byte_start', -1) == locator.get('record_index', -2) * 384
            and locator.get('byte_end') == locator['byte_start'] + 384 <= by_id[key]['bytes']):
            raise ValueError('Invalid login byte coordinate')
        if args['profile'] == 'linux_persistence' and not (
                0 <= locator.get('byte_start', -1) < locator.get('byte_end', -1) <= by_id[key]['bytes']
                and 1 <= locator.get('line_start', 0) <= locator.get('line_end', 0) <= 20000
                and row.get('entry_kind') in ('cron_schedule', 'cron_macro', 'systemd_directive')
                and type(row.get('value_empty')) is bool):
            raise ValueError('Invalid persistence coordinates or metadata')
        candidate = {'candidate_id': f'C{len(candidates)+1:04d}', 'input_id': key, 'ordinal': ordinal,
            'status': 'candidate_not_adopted', 'source_recheck': {'source_ref': by_id[key]['source_ref'],
                'sha256': by_id[key]['sha256'], **locator},
            'restricted_row_ref': {'scan_id': scan_id, 'input_id': key, 'candidate_ordinal': ordinal, 'model_access': False}}
        if args['profile'] == 'linux_persistence':
            candidate.update(entry_kind=row['entry_kind'], value_empty=row['value_empty'],
                triage_format=by_id[key]['triage_format'],
                interpretation='configured_text_only_effective_state_and_execution_not_established')
        ref = by_id[key]['source_ref']
        candidate['source_call'] = {'tool': 'forsic_reporting', 'arguments': {
            'action': 'source', 'source_id': ref['id'], 'source_version': ref['version'],
            'pointer': ref['pointer']}}
        if (args['profile'] in ('linux_login_parser', 'linux_persistence') and by_id[key]['selection_kind'] == 'evidence_file'):
            location = by_id[key].get('original_location', {})
            if location.get('coordinate_space') == 'selected_file_bytes':
                candidate['original_byte_call'] = {'tool': 'forsic_read_bytes', 'arguments': {
                    'path': location['path'], 'offset': locator['byte_start'],
                    'length': locator['byte_end']-locator['byte_start']}}
        if args['profile'] == 'linux_login_parser':
            from .login_records import TYPES
            candidate['record_type_label'] = TYPES.get(locator.get('record_type'), 'UNKNOWN')
            candidate['interpretation'] = 'recorded_structure_only_authentication_outcome_not_established'
        rule = row.get('rule_id')
        if isinstance(rule, str) and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', rule):
            candidate['rule_id'] = rule
            rule_sha = row.get('rule_sha256')
            if isinstance(rule_sha, str) and storage.HASH.fullmatch(rule_sha):
                candidate['rule_sha256'] = rule_sha
                title = row.get('rule_title')
                if isinstance(title, str) and 1 <= len(title) <= 180 and not any(ord(c) < 32 for c in title):
                    candidate['rule_title'] = title
                if row.get('severity') in ('informational', 'low', 'medium', 'high', 'critical'):
                    candidate['severity'] = row['severity']
                candidate['rule_metadata_source'] = 'hash_verified_installed_rule'
        candidates.append(candidate)
    engines = {}
    for k, v in body.get('engine_identity', {}).items():
        if k in ('binary_sha256', 'rules_manifest_sha256', 'helper_sha256', 'lock_sha256') and isinstance(v, str) and storage.HASH.fullmatch(v):
            engines[k] = v
    states = [s['state'] for s in scopes]
    state = 'complete' if all(s == 'complete' for s in states) else 'skipped' if all(s == 'skipped' for s in states) else 'failed' if all(s in ('failed', 'skipped') for s in states) else 'partial'
    return {'schema': SCHEMA, 'action': 'triage', 'profile': args['profile'],
        'profile_contract': PROFILES[args['profile']], 'representation': 'derived_triage_candidates',
        'original_evidence': False, 'coverage_not_established': True,
        'adoption': 'candidates_only_explicit_source_recheck_and_note_adoption_required',
        'coverage': {'state': state, 'inputs': scopes, 'selected_files': len(inputs),
            'scope': 'selected_files_only', 'zero_candidates_meaning': 'no_observed_candidates_does_not_establish_absence'},
        'summary': {'candidate_count': observed, 'returned_candidates': len(candidates),
            'omitted_candidates': max(0, observed-len(candidates)),
            'counts_complete': all(s['counts_complete'] for s in scopes),
            'count_scope': 'observed_output_candidates_only'},
        'engine_identity': engines, 'candidates': candidates}


def invoke(case, submitted, session_id, native_execution=None):
    from . import shell_workspace as shell
    args = validate(submitted)
    safe = _failure('triage_unavailable')
    job = manifest = None
    try:
        if native_execution and native_execution.interrupted():
            return _failure('cancelled_before_start')
        base, workspace = _base(case), shell._workspace(case, session_id)
        with shell._workspace_lock(workspace), storage._lock(base):
            storage._quota(base)
            snapshot = shell._execution_snapshot(case)
            image = snapshot['images']['analysis']['image_id']
            job = base / uuid.uuid4().hex
            job.mkdir(mode=0o700)
            staged = job / 'inputs'
            staged.mkdir(mode=0o700)
            inputs = storage._stage(case, args, session_id, staged, native_execution)
            if args['profile'] == 'linux_persistence':
                for item, target in zip(inputs, args['targets']):
                    item['triage_format'] = target['triage_format']
            if args['profile'] == 'windows_evtx':
                # Hayabusa's extension filtering must not silently skip opaque
                # staging names; bytes and original retained locators stay exact.
                for item in inputs:
                    name = item['input_id'] + '.evtx'
                    (staged/item['input_id']).rename(staged/name)
                    item['path'] = '/scan-input/' + name
            helper = Path(snapshot['helpers']['triage_scan.py']['path'])
            parser = Path(snapshot['helpers']['login_records.py']['path'])
            identity = {'case_id': case.config['case_id'], 'session_id': session_id, 'inputs': inputs,
                'image_id': image, 'environment': snapshot, 'helper_sha256': storage._sha(helper.read_bytes()),
                'parser_sha256': storage._sha(parser.read_bytes()),
                'lock_sha256': snapshot['declared_lock_sha256'],
                'settings': {k: args.get(k) for k in ('profile', 'candidate_limit', 'record_limit', 'timeout_seconds')},
                'login_format': args.get('login_format'), 'schema': SCHEMA}
            fingerprint = storage._sha(storage._json(identity))
            old_jobs = [base / args['resume_ref']] if args.get('resume_ref') else sorted(base.iterdir())
            for old in old_jobs:
                if old == job or not old.is_dir() or not (old/'manifest.json').exists():
                    continue
                prior = json.loads(storage._read(old/'manifest.json'))
                if prior.get('request_fingerprint') != fingerprint:
                    if args.get('resume_ref'):
                        raise ValueError('Resume identity mismatch')
                    continue
                if prior['state'] != 'retained':
                    raise ValueError('Prior termination is unknown; do not restart')
                raw = storage._read(old/'safe.json')
                if storage._sha(raw) != prior['safe_sha256']:
                    raise ValueError('Changed retained result')
                shutil.rmtree(job)
                return {**json.loads(raw), 'reused': True}
            if args.get('resume_ref'):
                raise ValueError('Resume reference unavailable')
            manifest = {'request_fingerprint': fingerprint, 'identity': identity, 'state': 'prepared',
                'native_call': native_execution.record() if native_execution else None}
            storage._write(job/'manifest.json', storage._json(manifest))
            request = {'schema': SCHEMA, 'scan_id': job.name, 'inputs': inputs,
                **identity['settings'], 'login_format': args.get('login_format'),
                'candidate_index_limit': MAX_INDEX_CANDIDATES}
            storage._write(job/'request.json', storage._json(request))
            output = job/'output'
            output.mkdir(mode=0o700)
            storage._write(output/'request.json', b'')
            mounts = [shell._mount(staged, '/scan-input', True), shell._mount(output, '/private', False),
                shell._mount(job/'request.json', '/private/request.json', True),
                shell._mount(helper, '/opt/forsic/triage_scan.py', True),
                shell._mount(parser, '/opt/forsic/login_records.py', True)]
            container = 'forsic-triage-' + job.name
            command = shell._command({'cwd': '/work', 'command': ''}, image, mounts, container)
            command = command[:command.index(image)] + [image, 'python', '-B', '/opt/forsic/triage_scan.py', '--request', '/private/request.json']
            command[command.index('--workdir')+1] = '/private'
            pending_interrupt = None
            try:
                from .environments import EnvironmentManager
                with EnvironmentManager().execution(case,expected_snapshot=snapshot):
                    result = shell.run_container(command, container, native_execution,
                        timeout_seconds=args['timeout_seconds'], max_output_bytes=65536, cleanup_on_return=True)
                execution = _execution(result['execution'])
                # Execution already happened. Any subsequent I/O/validation
                # failure must retain that receipt, never claim not_started.
                safe = _failure('triage_result_processing_failed', execution, stage='result_processing')
                storage._write(job/'stdout.bin', result['stdout'])
                storage._write(job/'stderr.bin', result['stderr'])
                if execution.get('state') == 'returned' and execution.get('exit_code') == 0 and execution.get('termination_confirmed') is True:
                    for item in inputs:
                        if storage._sha(storage._read(staged/Path(item['path']).name, MAX_BYTES)) != item['sha256']:
                            raise ValueError('Changed staged input')
                    body = json.loads(storage._read(output/'result.json'))
                    if args['profile'] == 'windows_evtx' and body.get('engine_identity', {}).get('lock_sha256') != identity['lock_sha256']:
                        if any(s.get('state') != 'skipped' for s in body.get('inputs', [])):
                            raise ValueError('Installed tool lock differs from pinned package lock')
                    safe = _project(body, inputs, args, job.name)
                    # Only allow-listed candidates are retained for later model queries.
                    # The engine's complete rows and diagnostics remain restricted.
                    if 'candidate_index' in body:
                        projected = _project({**body, 'candidates': body['candidate_index']},
                            inputs, args, job.name, candidate_limit=MAX_INDEX_CANDIDATES)
                        retained, encoded_bytes = [], 1024
                        for candidate in projected['candidates']:
                            size = len(storage._json(candidate))+1
                            if encoded_bytes+size > MAX_INDEX_BYTES:
                                break
                            retained.append(candidate)
                            encoded_bytes += size
                        index = {'schema': SCHEMA, 'scan_id': job.name,
                            'request_fingerprint': fingerprint, 'candidates': retained}
                        encoded = storage._json(index)
                        storage._write(job/'candidate-index.json', encoded)
                        manifest['candidate_index_sha256'] = storage._sha(encoded)
                        safe['candidate_query'] = {'available': True, 'index_sha256': storage._sha(encoded),
                            'indexed_candidates': len(index['candidates']),
                            'index_truncated': safe['summary']['candidate_count'] > len(retained),
                            'next_call': {'tool': 'forsic_shell', 'arguments': {'action': 'triage_query',
                                'scan_id': job.name, 'index_sha256': storage._sha(encoded)}}}
                    else:
                        safe['candidate_query'] = {'available': False, 'reason': 'candidate_index_unavailable'}
                    safe['execution'] = execution
                else:
                    safe = _failure('triage_execution_incomplete', execution, stage='execution')
            except ExecutionFailure as exc:
                safe = _failure('triage_execution_failed', exc.details.get('execution'), stage='execution')
            except (OSError, ValueError, KeyError, TypeError):
                safe = _failure('triage_result_processing_failed', safe.get('execution'), stage='result_processing')
            except KeyboardInterrupt as exc:
                pending_interrupt = exc
                safe = _failure('cancel_requested', getattr(exc, 'forsic_execution', {}), stage='execution')
                exc.forsic_shell_result = safe
                exc.forsic_execution = safe['execution']
                raise
            finally:
                safe.update(scan_id=job.name, resume_ref=job.name, reused=False, limits=catalog()['cost_limits'],
                    provenance={k: v for k, v in identity.items() if k != 'inputs'},
                    restricted_artifact={'ref': job.name, 'role': 'restricted_triage_rows_and_diagnostics', 'model_access': False})
                # Provenance input fields contain retained source locators only, never input text.
                try:
                    raw = storage._json(safe)
                    storage._write(job/'safe.json', raw)
                    manifest.update(state='retained' if safe['execution'].get('termination_confirmed') is True else 'completion_unknown', safe_sha256=storage._sha(raw))
                    storage._write(job/'manifest.json', storage._json(manifest), replace=True)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    if pending_interrupt is not None:
                        # A failed receipt write must never convert native
                        # cancellation into an ordinary returned tool failure.
                        safe['retention_failure'] = {'error_code': 'triage_retention_failed',
                            'failure_stage': 'retention', 'state': 'failed'}
                        pending_interrupt.forsic_shell_result = safe
                        pending_interrupt.forsic_execution = safe['execution']
                        raise pending_interrupt from exc
                    raise
            return safe
    except (OSError, ValueError, KeyError, TypeError, ExecutionFailure) as exc:
        if job is not None and manifest is not None:
            # Retention itself can fail after execution. Keep the identity and
            # actual receipt so callers inspect this job instead of replaying.
            retained = _failure('triage_retention_failed', safe.get('execution'), stage='retention')
            retained.update(scan_id=job.name, resume_ref=job.name, reused=False,
                provenance={k: v for k, v in manifest['identity'].items() if k != 'inputs'},
                restricted_artifact={'ref': job.name, 'role': 'restricted_triage_rows_and_diagnostics', 'model_access': False})
            return retained
        details = getattr(exc, 'details', {})
        code = details.get('error_code')
        rejected = _failure(code if code in ('workspace_busy', 'cancelled_before_start') else 'triage_rejected', details.get('execution'))
        if isinstance(exc, storage.ScanRejected) and exc.args and exc.args[0] in storage.REMEDIES:
            code = exc.args[0]
            rejected.update(admission_failure_code=code.removeprefix('sensitive_'),
                next_step=storage.REMEDIES[code])
        return rejected


def query_properties():
    return {'scan_id': {'type': 'string', 'pattern': '^[0-9a-f]{32}$'},
        'index_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
        'offset': {'type': 'integer', 'minimum': 0, 'maximum': MAX_INDEX_CANDIDATES},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_CANDIDATES},
        'input_id': {'type': 'string', 'pattern': '^F[0-9]{4}$'},
        'record_type': {'type': 'integer', 'minimum': 0, 'maximum': 2**63-1},
        'record_index_min': {'type': 'integer', 'minimum': 0, 'maximum': 2**63-1},
        'record_index_max': {'type': 'integer', 'minimum': 0, 'maximum': 2**63-1},
        'event_record_id': {'type': 'integer', 'minimum': 0, 'maximum': 2**63-1},
        'rule_id': {'type': 'string', 'pattern': '^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$'}}


def validate_query(args):
    if not isinstance(args, dict) or set(args)-{'action', *query_properties()}:
        raise ValueError('triage_query accepts a retained scan/digest and bounded metadata filters only')
    if not isinstance(args.get('scan_id'), str) or not storage.IDENTIFIER.fullmatch(args['scan_id']):
        raise ValueError('Copy the retained scan_id')
    if 'index_sha256' in args and not (isinstance(args['index_sha256'], str) and storage.HASH.fullmatch(args['index_sha256'])):
        raise ValueError('Copy the retained index_sha256')
    result = {'action': 'triage_query', 'offset': 0, 'limit': MAX_CANDIDATES, **args}
    for key, maximum in (('offset', MAX_INDEX_CANDIDATES), ('limit', MAX_CANDIDATES),
            ('record_type', 2**63-1), ('record_index_min', 2**63-1),
            ('record_index_max', 2**63-1), ('event_record_id', 2**63-1)):
        if key in result and (type(result[key]) is not int or not (1 if key == 'limit' else 0) <= result[key] <= maximum):
            raise ValueError('Invalid bounded metadata filter')
    for key in ('input_id', 'rule_id'):
        if key in result and (not isinstance(result[key], str) or not re.fullmatch(query_properties()[key]['pattern'], result[key])):
            raise ValueError('Invalid candidate metadata identifier')
    if result.get('record_index_min', 0) > result.get('record_index_max', 2**63-1):
        raise ValueError('Invalid record index range')
    return result


def query(case, submitted, session_id):
    """Read only a digest-bound safe index; never execute a parser or read raw rows."""
    from .report_driven.host import source_record
    args = validate_query(submitted)
    failure = {'schema': SCHEMA, 'action': 'triage_query', 'scan_id': args['scan_id'],
        'representation': 'derived_triage_candidates', 'original_evidence': False,
        'coverage_not_established': True, 'candidates': [], 'query_state': 'rejected',
        'error': 'Retained safe candidates could not be queried.'}
    try:
        base = _base(case, create=False)
        with storage._lock(base):
            job = base/args['scan_id']
            manifest = json.loads(storage._read(job/'manifest.json'))
            identity = manifest['identity']
            if (identity['case_id'] != case.config['case_id'] or identity['session_id'] != session_id
                    or manifest['request_fingerprint'] != storage._sha(storage._json(identity))):
                raise ValueError('Scan identity mismatch')
            if manifest['state'] != 'retained':
                return {**failure, 'error_code': 'triage_query_completion_unknown'}
            raw = storage._read(job/'safe.json')
            if storage._sha(raw) != manifest['safe_sha256']:
                raise ValueError('Changed retained result')
            safe = json.loads(raw)
            if 'candidate_index_sha256' not in manifest:
                return {**failure, 'query_state': 'unavailable', 'error_code': 'candidate_index_unavailable',
                    'meaning': 'This retained scan has no queryable safe index; no parser was rerun.'}
            digest = manifest['candidate_index_sha256']
            if args.get('index_sha256') != digest or safe['candidate_query']['index_sha256'] != digest:
                raise ValueError('Index digest mismatch')
            raw = storage._read(job/'candidate-index.json', MAX_INDEX_BYTES)
            if storage._sha(raw) != digest:
                raise ValueError('Changed candidate index')
            index = json.loads(raw)
            if (index['schema'] != SCHEMA or index['scan_id'] != args['scan_id']
                    or index['request_fingerprint'] != manifest['request_fingerprint']):
                raise ValueError('Index identity mismatch')
            for item in identity['inputs']:
                ref = item['source_ref']
                event = case.event(ref['id'])
                if not event or source_record(event)['version'] != ref['version']:
                    raise ValueError('Stale source version')
                # The retained staged snapshot is the parse input; no new source
                # acquisition or parsing is performed while selecting candidates.
                staged = job/'inputs'/Path(item['path']).name
                if storage._sha(storage._read(staged, MAX_BYTES)) != item['sha256']:
                    raise ValueError('Changed retained parse input')
            candidates = index['candidates']
            if len(candidates) > MAX_INDEX_CANDIDATES:
                raise ValueError('Invalid candidate index')
            if 'input_id' in args and args['input_id'] not in {i['input_id'] for i in identity['inputs']}:
                raise ValueError('Unknown selected input')
            def matches(candidate):
                locator = candidate['source_recheck']
                return (('input_id' not in args or candidate['input_id'] == args['input_id'])
                    and all(key not in args or locator.get(key) == args[key] for key in ('record_type', 'event_record_id'))
                    and ('record_index_min' not in args or locator.get('record_index', -1) >= args['record_index_min'])
                    and ('record_index_max' not in args or locator.get('record_index', 2**63) <= args['record_index_max'])
                    and ('rule_id' not in args or candidate.get('rule_id', '').lower() == args['rule_id'].lower()))
            selected = [candidate for candidate in candidates if matches(candidate)]
            if args['offset'] > len(selected):
                raise ValueError('Offset beyond retained selection')
            page = selected[args['offset']:args['offset']+args['limit']]
            next_offset = args['offset']+len(page)
            more = next_offset < len(selected)
            return {**safe, 'action': 'triage_query', 'query_state': 'returned', 'reused': True,
                'execution_role': 'retained_parser_execution_not_query_execution',
                'candidates': page, 'summary': {**safe['summary'], 'returned_candidates': len(page),
                    'omitted_candidates': max(0, safe['summary']['candidate_count']-len(page))},
                'selection': {'index_sha256': digest, 'indexed_candidates': len(candidates),
                    'index_truncated': safe['candidate_query']['index_truncated'],
                    'matched_indexed_candidates': len(selected), 'offset': args['offset'],
                    'returned_candidates': len(page), 'page_truncated': more,
                    'filters': {k: v for k, v in args.items() if k not in ('action', 'scan_id', 'index_sha256', 'offset', 'limit')},
                    'count_scope': 'retained_safe_candidate_index_not_semantic_review',
                    'next_call': {'tool': 'forsic_shell', 'arguments': {**args, 'offset': next_offset}} if more else None}}
    except (OSError, ValueError, KeyError, TypeError):
        return {**failure, 'error_code': 'triage_query_rejected',
            'meaning': 'Retained scan identity, version or artifact integrity could not be verified; no parser was rerun.'}
