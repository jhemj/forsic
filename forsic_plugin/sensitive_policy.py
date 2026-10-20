"""Selected local scans through the native terminal boundary.

Only the masked result enters the event ledger. Detector artifacts and selected
input copies stay outside /work. The report renderer may read bounded original
excerpts for the user's local appendix; model/source/export paths never do so.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
import tomllib
import uuid

from .execution_contract import ExecutionFailure

SCHEMA = 'forsic.sensitive.scan.v1'
MAX_FILE = 256 * 1024 * 1024
MAX_JOB_BYTES = 32 * 1024 * 1024
MAX_PRIVATE_BYTES = 2 * 1024**3
MAX_PRIVATE_FILES = 10000
MAX_RESULT_BYTES = 32 * 1024 * 1024
JOB_RESERVATION = 256 * 1024 * 1024
PRIVATE_NAME = '.sensitive-artifacts'
IDENTIFIER = re.compile(r'^[0-9a-f]{32}$')
HASH = re.compile(r'^[0-9a-f]{64}$')
ENTITIES = frozenset(('SECRET', 'KR_RRN', 'KR_FRN', 'KR_PASSPORT',
    'KR_DRIVER_LICENSE', 'KR_BRN', 'PHONE_NUMBER', 'EMAIL_ADDRESS', 'CREDIT_CARD'))
CONTEXT_SIGNALS = frozenset(('korean_rrn_label_nearby','korean_frn_label_nearby',
    'korean_phone_label_nearby','korean_email_label_nearby','korean_passport_label_nearby',
    'korean_driver_license_label_nearby','korean_card_label_nearby','korean_business_identifier_label_nearby'))
FORMAT_LIMITATIONS = ('no_cell_or_json_pointer_mapping','no_json_escape_decoding',
    'no_nlp_context_or_free_text_person_recognition')
REASONS = frozenset(('not_regular_file', 'unsupported_encoding', 'utf16_bom_required',
    'decode_error', 'unsupported_binary_text', 'job_limit', 'input_byte_limit',
    'pii_engine_not_supported', 'deadline_reached', 'finding_limit', 'recognizer_failed',
    'secret_coordinate_unresolved', 'engine_not_installed', 'engine_timeout',
    'engine_failed', 'engine_report_invalid', 'engine_report_limit', 'engine_diagnostic_error',
    'input_processing_failed', 'csv_record_parse_failed', 'jsonl_record_parse_failed',
    'structured_record_invalid', 'structured_parse_error', 'unsupported_structured_format',
    'private_output_budget', 'private_output_limit', 'source_changed', 'output_limit'))
NUMBER_FIELDS = frozenset(('selected_files', 'fully_processed_files', 'partial_files',
    'failed_files', 'excluded_files', 'bytes_total', 'bytes_read', 'record_number',
    'secret_occurrences_observed', 'pii_occurrences_observed',
    'organization_identifier_occurrences_observed', 'distinct_values_observed',
    'distinct_people', 'records_with_findings', 'files_with_findings'))

REMEDIES = {
    'sensitive_storage_overlap': 'Private scan storage overlaps evidence. An operator must select nonoverlapping storage; do not move originals.',
    'sensitive_artifact_quota': 'The private artifact budget is full. Retain or remove completed artifacts explicitly; do not delete automatically.',
    'sensitive_input_limit': 'Select a smaller retained scalar or explicit derived range. The combined staging limit is 32 MiB.',
    'sensitive_source_version_changed': 'Look up the retained source and use its actual current version. Do not invent a version.',
    'sensitive_export_changed': 'The workspace file differs from its retained export receipt. Compare or obtain a new explicit export.',
    'sensitive_unsupported_format': 'Use an existing supported extractor, then select its text or record derivative. Binary/container data was not scanned.',
    'sensitive_resume_mismatch': 'This resume_ref belongs to different inputs or settings. Inspect the retained scan; request a new scan explicitly only for the changed scope.',
}


class ScanRejected(ValueError):
    """Actionable public code, never a source value or detector diagnostic."""


def properties():
    return {
        'targets': {'type': 'array', 'minItems': 1, 'maxItems': 64,
            'description': 'Select retained references, never new paths. retained_text scans one body scalar; evidence_file selects a returned original file path; workspace_export selects a complete export receipt in /stdout.',
            'items': {'type': 'object', 'properties': {
                'source_handle': {'type': 'string', 'maxLength': 2048},
                'source_id': {'type': 'string', 'maxLength': 64},
                'source_version': {'type': 'string', 'maxLength': 64},
                'pointer': {'type': 'string', 'maxLength': 512},
                'kind': {'type': 'string', 'enum': ['retained_text', 'evidence_file', 'workspace_export']},
                'encoding': {'type': 'string', 'enum': ['auto', 'utf-8', 'utf-16', 'cp949']},
                'format': {'type': 'string', 'enum': ['text', 'csv', 'jsonl']}},
                'required': ['pointer'], 'additionalProperties': False}},
        'profiles': {'type': 'array', 'minItems': 1, 'maxItems': 2,
            'items': {'type': 'string', 'enum': ['secrets', 'pii_kr']}},
        'mode': {'type': 'string', 'enum': ['triage', 'scope_count']},
        'resume_ref': {'type': 'string', 'maxLength': 32,
            'description': 'Replay an exact retained scan with the same targets/profiles/mode; never restart uncertain execution.'},
    }


def validate(args):
    if set(args) - {'action', 'targets', 'profiles', 'mode', 'resume_ref', 'timeout_seconds'}:
        raise ValueError('sensitive_scan accepts retained targets, profiles, mode, resume_ref and timeout_seconds only')
    targets = args.get('targets')
    if not isinstance(targets, list) or not 1 <= len(targets) <= 64:
        raise ValueError('Select 1 to 64 retained targets')
    result = []
    for target in targets:
        allowed = {'source_id', 'source_version', 'source_handle', 'pointer', 'kind', 'encoding', 'format'}
        if not isinstance(target, dict) or set(target) - allowed:
            raise ValueError('Select a retained source and pointer, not a new path')
        if not isinstance(target.get('pointer'), str) or not re.fullmatch(r'/[A-Za-z0-9_~/.-]{1,510}', target['pointer']):
            raise ValueError('Use a returned body or file-location pointer')
        if 'source_handle' in target:
            if not isinstance(target['source_handle'], str) or len(target['source_handle']) > 2048:
                raise ValueError('Invalid source handle')
        elif not (isinstance(target.get('source_id'), str) and IDENTIFIER.fullmatch(target['source_id'])
                  and isinstance(target.get('source_version'), str) and HASH.fullmatch(target['source_version'])):
            raise ValueError('Copy the exact source_handle or source_id and source_version')
        value = {'kind': 'retained_text', 'encoding': 'auto', 'format': 'text', **target}
        if (value['kind'] not in ('retained_text', 'evidence_file', 'workspace_export')
                or value['encoding'] not in ('auto', 'utf-8', 'utf-16', 'cp949')
                or value['format'] not in ('text', 'csv', 'jsonl')):
            raise ValueError('Unsupported target kind, encoding or format')
        result.append(value)
    profiles = args.get('profiles', ['secrets', 'pii_kr'])
    if (not isinstance(profiles, list) or not profiles or len(profiles) > 2
            or any(p not in ('secrets', 'pii_kr') for p in profiles)):
        raise ValueError('Select secrets and/or pii_kr')
    mode, timeout = args.get('mode', 'triage'), args.get('timeout_seconds', 300)
    if mode not in ('triage', 'scope_count') or type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError('Use triage or scope_count and a timeout of 1 to 300 seconds')
    if 'resume_ref' in args and not (isinstance(args['resume_ref'], str) and IDENTIFIER.fullmatch(args['resume_ref'])):
        raise ValueError('Copy the retained resume_ref')
    return {**args, 'targets': result, 'profiles': sorted(set(profiles)), 'mode': mode, 'timeout_seconds': timeout}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _no_symlinks(path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('Sensitive artifact paths must not contain symlinks')


def _base(case, *, create=True):
    output = Path(case.config['output_root']).absolute()
    _no_symlinks(output)
    if output.resolve() != case.output or output.is_relative_to(case.root):
        raise ScanRejected('sensitive_storage_overlap')
    base = output / PRIVATE_NAME
    if base == case.root or base.is_relative_to(case.root) or case.root.is_relative_to(base):
        raise ScanRejected('sensitive_storage_overlap')
    if create:
        base.mkdir(mode=0o700, exist_ok=True)
    _no_symlinks(base)
    info = base.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Sensitive artifact directory requires owner-only access')
    return base


def _write(path, raw, *, replace=False):
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(name, path)
        else:
            os.link(name, path, follow_symlinks=False)
    finally:
        Path(name).unlink(missing_ok=True)


def _read(path, limit=MAX_RESULT_BYTES):
    _no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > limit:
            raise ValueError('Invalid restricted artifact')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Restricted artifact exceeds its byte limit')
    return raw


@contextmanager
def _lock(base):
    fd = os.open(base / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('A sensitive scan owns this case artifact boundary') from None
        yield
    finally:
        os.close(fd)


def _quota(base):
    count = total = 0
    for root, dirs, files in os.walk(base, followlinks=False):
        for name in dirs + files:
            info = (Path(root) / name).lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError('Unexpected object in restricted artifacts')
            count += 1
            total += info.st_size
            if count > MAX_PRIVATE_FILES or total > MAX_PRIVATE_BYTES - JOB_RESERVATION:
                raise ScanRejected('sensitive_artifact_quota')
    if shutil.disk_usage(base).free < JOB_RESERVATION:
        raise ValueError('Insufficient space for a bounded sensitive scan')


def _copy_regular(path, destination, remaining, native):
    """Pin each parent and the regular-file inode; never follow evidence links."""
    _no_symlinks(path)
    parents = []
    try:
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parents.append(fd)
        for part in path.parts[1:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            parents.append(fd)
        source = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
                raise ValueError('Select a regular file no larger than 256 MiB')
            if before.st_size > remaining:
                raise ScanRejected('sensitive_input_limit')
            digest, used = hashlib.sha256(), 0
            out_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(out_fd, 'wb') as output:
                while block := stream.read(min(1024 * 1024, remaining - used + 1)):
                    if native and native.interrupted():
                        raise ExecutionFailure('Native cancellation before scan launch', error_code='cancelled_before_start')
                    used += len(block)
                    if used > remaining:
                        raise ValueError('Input grew beyond the staging budget')
                    digest.update(block)
                    output.write(block)
            after = os.fstat(stream.fileno())
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            current = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            if identity(before) != identity(after) or identity(after) != identity(current) or used != before.st_size:
                raise ValueError('Selected source changed while staging')
            return used, digest.hexdigest()
    finally:
        for fd in reversed(parents):
            os.close(fd)


def _stage(case, args, session_id, directory, native):
    from .report_driven.source_handles import resolve_source_handle
    from .report_driven.host import source_record
    from .report_driven.model_pages import select
    from .report_driven.measurement import field_role
    from .notes import EVIDENCE_TOOLS
    from .shell_workspace import _workspace
    inputs, used, selections = [], 0, set()
    for i, submitted in enumerate(args['targets'], 1):
        target = resolve_source_handle(case, submitted)
        event = case.event(target['source_id'])
        if not event or event['kind'] != 'tool_result' or event['data'].get('tool') not in EVIDENCE_TOOLS:
            raise ValueError('Select a retained evidence result from this case')
        data = event['data']
        if source_record(event)['version'] != target.get('source_version') or data.get('error'):
            raise ScanRejected('sensitive_source_version_changed')
        value = select(data, target['pointer'])
        if not isinstance(value, str):
            raise ValueError('Select one body scalar or returned file path')
        key = f'F{i:04d}'
        item = {'input_id': key, 'path': '/scan-input/' + key,
            'encoding': target['encoding'], 'format': target['format'],
            'source_ref': {'id': event['id'], 'version': target['source_version'], 'pointer': target['pointer']},
            'selection_kind': target['kind']}
        destination = directory / key
        if target['kind'] == 'retained_text':
            identity = ('text', event['id'], target['pointer'])
            if field_role(data, target['pointer']) != 'body' or data.get('schema') == SCHEMA:
                raise ValueError('Select retained body text, not metadata or a masked scan')
            if target['encoding'] not in ('auto', 'utf-8'):
                raise ValueError('Retained text is already UTF-8; do not reinterpret its encoding')
            raw = value.encode('utf-8')
            if len(raw) + used > MAX_JOB_BYTES:
                raise ScanRejected('sensitive_input_limit')
            _write(destination, raw)
            item.update(source_role='retained_tool_text', encoding='utf-8',
                bytes=len(raw), sha256=_sha(raw),
                original_location={'coordinate_space': 'retained_scalar_utf8', 'pointer': target['pointer']},
                coverage={'original_scope': 'retained_scalar_only', 'whole_original_file': False})
        else:
            if target['kind'] == 'evidence_file':
                tool = data['tool']
                allowed = ((tool == 'forsic_list' and re.fullmatch(r'/files/\d+/path', target['pointer']))
                    or (tool in ('forsic_read', 'forsic_read_bytes', 'forsic_hash') and target['pointer'] == '/path')
                    or (tool == 'forsic_search' and re.fullmatch(r'/matches/\d+/path', target['pointer'])))
                if not allowed:
                    raise ValueError('Use a retained selected-file path, not an image-internal or arbitrary path')
                # Reject a symlink even though Case.path normally resolves paths.
                _no_symlinks(case.root / value)
                path = case.path(value)
                if re.search(r'\.(?:e\d\d|ex01|aff4?|zip|7z|rar|gz|tar|pdf|docx?|xlsx?)$', path.name, re.I):
                    raise ScanRejected('sensitive_unsupported_format')
                item.update(source_role='selected_file',
                    original_location={'coordinate_space': 'selected_file_bytes', 'path': value},
                    coverage={'original_scope': 'selected_regular_file'})
            else:
                if (data['tool'] != 'forsic_shell' or event['session'] != session_id
                        or target['pointer'] != '/stdout'
                        or data.get('execution', {}).get('termination_confirmed') is not True
                        or data.get('execution', {}).get('exit_code') != 0
                        or data.get('preview_truncated', {}).get('stdout')):
                    raise ValueError('Select a complete export receipt from this conversation')
                receipt = json.loads(value)
                if (not isinstance(receipt, dict) or receipt.get('derived') is not True
                        or receipt.get('complete_file') is not True or receipt.get('hash_scope') != 'exported_bytes'
                        or not isinstance(receipt.get('sha256'), str) or not HASH.fullmatch(receipt['sha256'])):
                    raise ValueError('Select a complete image_access export receipt')
                logical = PurePosixPath(receipt.get('destination', ''))
                if logical.parts[:2] != ('/', 'work') or '..' in logical.parts or len(logical.parts) < 3:
                    raise ValueError('Export destination is outside the conversation workspace')
                path = _workspace(case, session_id).joinpath(*logical.parts[2:])
                item.update(source_role='derived_workspace_export',
                    original_location={'coordinate_space': 'derived_export_bytes', 'destination': str(logical),
                        'declared_source': receipt.get('source'), 'physical_image_offset': None},
                    coverage={'original_scope': 'declared_export', 'original_linkage': 'retained_command_declaration'},
                    expected_sha256=receipt['sha256'])
            _no_symlinks(path)
            info = path.stat()
            identity = ('file', info.st_dev, info.st_ino)
            if identity in selections:
                raise ValueError('The same selected file occurs more than once')
            count, digest = _copy_regular(path, destination, MAX_JOB_BYTES - used, native)
            if item.get('expected_sha256', digest) != digest:
                raise ScanRejected('sensitive_export_changed')
            item.update(bytes=count, sha256=digest)
        if identity in selections:
            raise ValueError('The same retained input occurs more than once')
        selections.add(identity)
        used += item['bytes']
        inputs.append(item)
    return inputs


def _execution(data):
    result = {k: data[k] for k in ('state', 'exit_code', 'termination_confirmed')
              if k in data and (data[k] is None or type(data[k]) in (bool, int)
                               or data[k] in ('not_started', 'returned', 'failed', 'cancelled', 'completion_unknown'))}
    result['backend'] = 'docker'
    if isinstance(data.get('cleanup'), dict):
        result['cleanup'] = {k: data['cleanup'][k] for k in ('confirmed', 'requested', 'client_reaped', 'exit_code')
                             if type(data['cleanup'].get(k)) in (bool, int)}
    return result


def _safe_helper(body, scan_id):
    """Allow-listed projection: untrusted diagnostics/raw fields cannot escape."""
    if not isinstance(body, dict) or body.get('schema') != SCHEMA or body.get('scan_id') != scan_id:
        raise ValueError('Invalid scan result identity')
    def numbers(obj):
        return {k: v for k, v in obj.items() if k in NUMBER_FIELDS
                and (v is None or type(v) is int and 0 <= v <= 10**15)}
    def state(value):
        return value if value in ('complete', 'partial', 'failed', 'excluded', 'unsupported',
            'not_supported', 'completed', 'not_started', 'returned', 'unavailable', 'available',
            'cancelled', 'missing', 'invalid', 'observed') else 'unknown'
    coverage = numbers(body.get('coverage', {}))
    coverage.update(state=state(body.get('coverage', {}).get('state')), count_scope='observed_ranges_only', inputs=[])
    for source in body.get('coverage', {}).get('inputs', [])[:64]:
        input_id = source.get('input_id')
        if not isinstance(input_id, str) or not re.fullmatch(r'F\d{4}', input_id):
            raise ValueError('Invalid scan input identity')
        ranges = [{k: x[k] for k in ('start', 'end') if type(x.get(k)) is int and x[k] >= 0}
                  for x in source.get('ranges', [])[:128]]
        coverage['inputs'].append({**numbers(source), 'input_id': input_id,
            'state': state(source.get('state')), 'ranges': ranges,
            'reason_codes': [reason if reason in REASONS else 'input_processing_failed'
                             for reason in source.get('reason_codes', [])[:16]],
            'profile_states': {k: state(v) for k, v in source.get('profile_states', {}).items()
                               if k in ('secrets', 'pii_kr')}})
        mapping = source.get('record_mapping_state')
        if mapping in ('not_requested','complete','failed'):
            coverage['inputs'][-1]['record_mapping_state'] = mapping
    summary = numbers(body.get('summary', {}))
    summary.update(distinct_people=None, credential_live_validity='not_tested',
                   external_disclosure='not_assessed', real_person_data='not_assessed',
                   count_method='retained_unique_source_span_and_value',
                   counts_are_lower_bounds=body.get('summary',{}).get('counts_are_lower_bounds') is True)
    rules = tomllib.loads((Path(__file__).parents[1] / 'tools/sensitive/gitleaks.toml').read_text())['rules']
    trusted_rules = {rule['id'] for rule in rules} | ENTITIES
    findings = []
    for finding in body.get('findings', []):
        ident, input_id = finding.get('finding_id'), finding.get('input_id')
        if (not isinstance(ident, str) or not re.fullmatch(r'[DSP]\d{1,6}', ident)
                or not isinstance(input_id, str) or not re.fullmatch(r'F\d{4}', input_id)):
            raise ValueError('Invalid finding identity')
        loc = finding.get('source_locator', {})
        locator = {k: loc[k] for k in ('byte_start', 'byte_end', 'line_start', 'line_end', 'column_start', 'column_end')
                   if type(loc.get(k)) is int and loc[k] >= 0}
        if 'byte_start' not in locator or locator.get('byte_end', -1) <= locator['byte_start']:
            raise ValueError('Invalid finding coordinates')
        locator['coordinate_space'] = 'scanned_input_bytes_and_unicode_codepoints'
        if isinstance(loc.get('structured_record'), dict):
            locator['structured_record'] = numbers(loc['structured_record'])
            if loc['structured_record'].get('format') in ('csv','jsonl'):
                locator['structured_record']['format'] = loc['structured_record']['format']
        entity = finding.get('entity_type')
        observations = []
        for obs in finding.get('observations', []):
            if obs.get('engine') in ('gitleaks', 'presidio_local'):
                validation = obs.get('format_validation')
                observations.append({'engine': obs['engine'],
                    'rule_id': obs.get('rule_id') if obs.get('rule_id') in trusted_rules else 'opaque_rule',
                    'format_validation': validation if type(validation) is bool else None,
                    'provenance': obs.get('provenance') if obs.get('provenance') in
                        ('presidio_upstream','adapter_local_pattern','trusted_rules') else 'unknown'})
        findings.append({'finding_id': ident, 'input_id': input_id,
            'entity_type': entity if entity in ENTITIES else 'PII_CANDIDATE',
            'category': 'secret_candidate' if entity == 'SECRET' else
                        'organization_identifier' if entity == 'KR_BRN' else 'pii_candidate',
            'masked_excerpt': '<' + ('SECRET:' if entity == 'SECRET' else 'PII:') + ident + '>',
            'source_locator': locator, 'safe_excerpt_coordinate_space': 'derived_masked_utf8',
            'context_signals': [x for x in finding.get('context_signals',[]) if x in CONTEXT_SIGNALS],
            'observations': observations, 'real_person_data': 'not_assessed'})
    engines = []
    for engine in body.get('engines', []):
        if engine.get('name') not in ('gitleaks', 'presidio_local'):
            continue
        value = {'name': engine['name'], 'state': state(engine.get('state'))}
        if isinstance(engine.get('version'), str) and re.fullmatch(r'[0-9.]{1,32}', engine['version']):
            value['version'] = engine['version']
        for key in ('config_sha256','adapter_sha256','binary_sha256'):
            if isinstance(engine.get(key), str) and HASH.fullmatch(engine[key]):
                value[key] = engine[key]
        if engine['name']=='presidio_local':
            value.update(nlp='not_loaded',recognizers=[x for x in engine.get('recognizers',[]) if x in ENTITIES])
        engines.append(value)
    return {'schema': SCHEMA, 'action': 'sensitive_scan', 'scan_id': scan_id,
        'representation': 'derived_masked_detection_result', 'original_evidence': False,
        'coverage_not_established': True, 'coverage': coverage, 'summary': summary,
        'engines': engines, 'findings': findings,
        'context_policy': {'method':'bounded_same_line_literal_alias','alias_version':'korean_labels_v1',
            'window_codepoints_each_side':48,'nlp':'not_loaded',
            'interpretation':'lexical_proximity_only_no_field_binding_or_person_verification'},
        'limitations': list(FORMAT_LIMITATIONS),
        'privacy': {'masking_policy':'whole_value_opaque_v1','normalization_version':'exact_values_v1',
                    'raw_hashes_private':True},
        'findings_omitted': max(0, body.get('findings_omitted', 0)) if type(body.get('findings_omitted')) is int else 0}


def _groups(base, safe, private):
    path = base / 'groups.json'
    groups = json.loads(_read(path)) if path.exists() else {}
    by_id = {x.get('finding_id'): x for x in private.get('findings', [])}
    for finding in safe['findings']:
        token = by_id.get(finding['finding_id'], {}).get('value_hmac')
        if isinstance(token, str) and HASH.fullmatch(token):
            if token not in groups:
                groups[token] = 'G' + str(len(groups) + 1).zfill(6)
            finding['group_id'] = groups[token]
    _write(path, _json(groups), replace=True)


def _failure(code, execution=None):
    return {'schema': SCHEMA, 'action': 'sensitive_scan',
        'representation': 'derived_masked_detection_result', 'original_evidence': False,
        'error': 'Sensitive scan did not complete. Inspect its retained execution and selected scope.',
        'error_code': code, 'execution': _execution(execution or {'state': 'not_started', 'termination_confirmed': True}),
        **({'next_step':REMEDIES[code]} if code in REMEDIES else {}),
        'coverage': {'state': 'unknown'}, 'summary': {'distinct_people': None,
            'credential_live_validity': 'not_tested', 'external_disclosure': 'not_assessed'}}


def invoke(case, submitted, session_id, native_execution=None):
    from . import shell_workspace as shell
    args = validate(submitted)
    job = None
    safe = _failure('sensitive_scan_unavailable')
    manifest = None
    try:
        if native_execution and native_execution.interrupted():
            return _failure('cancelled_before_start')
        base, workspace = _base(case), shell._workspace(case, session_id)
        with shell._workspace_lock(workspace), _lock(base):
            _quota(base)
            key_path = base / 'hmac.key'
            if not key_path.exists():
                if any(p.is_dir() for p in base.iterdir()):
                    raise ValueError('Case comparison key is unavailable; do not silently create a replacement')
                _write(key_path, os.urandom(32))
            key = _read(key_path, 32)
            if len(key) != 32:
                raise ValueError('Invalid comparison key')
            key_identity = base / 'key-identity.json'
            if key_identity.exists():
                if json.loads(_read(key_identity, 512)) != {'key_id': _sha(key)}:
                    return _failure('sensitive_comparison_key_changed')
            else:
                if (base / 'groups.json').exists():
                    return _failure('sensitive_comparison_key_identity_missing')
                _write(key_identity, _json({'key_id': _sha(key)}))
            snapshot = shell._execution_snapshot(case)
            image = snapshot['images']['analysis']['image_id']
            job = base / uuid.uuid4().hex
            job.mkdir(mode=0o700)
            inputs_dir = job / 'inputs'
            inputs_dir.mkdir(mode=0o700)
            inputs = _stage(case, args, session_id, inputs_dir, native_execution)
            code_hash = _sha(Path(snapshot['helpers']['sensitive_scan.py']['path']).read_bytes())
            lock_hash = snapshot['declared_lock_sha256']
            identity = {'case_id': case.config['case_id'], 'session_id': session_id,
                'inputs': inputs, 'profiles': args['profiles'], 'mode': args['mode'],
                'image_id': image, 'environment': snapshot, 'helper_sha256': code_hash, 'package_lock_sha256': lock_hash,
                'key_id': _sha(key), 'schema': SCHEMA}
            fingerprint = _sha(_json(identity))
            candidates = [base / args['resume_ref']] if args.get('resume_ref') else sorted(base.iterdir())
            for old in candidates:
                manifest_path = old / 'manifest.json'
                if old == job or not old.is_dir() or not manifest_path.exists():
                    continue
                prior = json.loads(_read(manifest_path))
                if prior.get('request_fingerprint') != fingerprint:
                    if args.get('resume_ref'):
                        raise ScanRejected('sensitive_resume_mismatch')
                    continue
                if prior.get('state') != 'retained':
                    raise ValueError('Prior scan completion is unconfirmed; inspect its execution')
                raw = _read(old / 'safe-retained.json')
                if _sha(raw) != prior['safe_sha256']:
                    raise ValueError('Retained scan integrity check failed')
                safe = json.loads(raw)
                shutil.rmtree(job)
                job = None
                safe.update(reused=True, reused_from_scan=old.name)
                return safe
            if args.get('resume_ref'):
                raise ValueError('The retained resume reference is unavailable')
            manifest = {'schema': SCHEMA, 'scan_id': job.name, 'request_fingerprint': fingerprint,
                'identity': identity, 'state': 'prepared', 'created_at': time.time(),
                'native_call': native_execution.record() if native_execution else None}
            _write(job / 'manifest.json', _json(manifest))
            _write(job / 'hmac.key', key)
            request = {'schema': SCHEMA, 'scan_id': job.name, 'profiles': args['profiles'],
                'mode': args['mode'], 'inputs': inputs, 'key_path': '/private/hmac.key',
                'output_dir': '/private', 'deadline_seconds': args['timeout_seconds']}
            _write(job / 'request.json', _json(request))
            output_dir = job / 'output'
            output_dir.mkdir(mode=0o700)
            # Nested bind targets must exist on shared/virtiofs hosts. These
            # empty placeholders are covered by the read-only file mounts.
            for name in ('request.json', 'hmac.key'):
                _write(output_dir / name, b'')
            mounts = [shell._mount(inputs_dir, '/scan-input', True), shell._mount(output_dir, '/private', False),
                shell._mount(job / 'request.json', '/private/request.json', True),
                shell._mount(job / 'hmac.key', '/private/hmac.key', True),
                shell._mount(Path(snapshot['helpers']['sensitive_scan.py']['path']), '/opt/forsic/sensitive_scan.py', True)]
            container = 'forsic-sensitive-' + job.name
            command = shell._command({'cwd': '/work', 'command': ''}, image, mounts, container)
            image_pos = command.index(image)
            command = command[:image_pos] + [image, 'python', '-B', '/opt/forsic/sensitive_scan.py',
                                             '--request', '/private/request.json']
            # The generic executor's environment is fixed, but no /work or
            # original evidence tree is mounted into this typed operation.
            command[command.index('--workdir') + 1] = '/private'
            started = time.monotonic()
            try:
                from .environments import EnvironmentManager
                with EnvironmentManager().execution(case,expected_snapshot=snapshot):
                    result = shell.run_container(command, container, native_execution,
                        timeout_seconds=args['timeout_seconds'], max_output_bytes=65536, cleanup_on_return=True)
                execution = _execution(result['execution'])
                safe['execution'] = execution
                _write(job / 'stdout.bin', result['stdout'])
                _write(job / 'stderr.bin', result['stderr'])
                if execution.get('state') != 'returned' or execution.get('termination_confirmed') is not True or execution.get('exit_code') != 0:
                    safe = _failure('sensitive_execution_incomplete', execution)
                else:
                    private_raw = _read(output_dir / 'result.json')
                    private = json.loads(private_raw)
                    for item in inputs:
                        if _sha(_read(inputs_dir / item['input_id'],MAX_JOB_BYTES)) != item['sha256']:
                            raise ValueError('Staged input changed during execution')
                    safe = _safe_helper(json.loads(_read(output_dir / 'safe.json')), job.name)
                    safe['execution'] = execution
                    _groups(base, safe, private)
                    safe.update(execution=execution,
                        input_refs=[{'input_id': x['input_id'], 'source_ref': x['source_ref'],
                                     'source_role': x['source_role'], 'selection_kind': x['selection_kind']}
                                    for x in inputs])
                    safe['summary_text'] = (
                        f"Detector candidates: secrets={safe['summary'].get('secret_occurrences_observed',0)}, "
                        f"PII={safe['summary'].get('pii_occurrences_observed',0)}, "
                        f"organization identifiers={safe['summary'].get('organization_identifier_occurrences_observed',0)}. "
                        f"Coverage={safe['coverage']['state']}; counts are "
                        + ('lower bounds' if safe['summary']['counts_are_lower_bounds'] else 'observed candidates only')
                        + ". People, credential validity and exfiltration are not established.")
                    manifest['private_sha256'] = _sha(private_raw)
                manifest['elapsed_seconds'] = round(time.monotonic() - started, 3)
            except ExecutionFailure as exc:
                safe = _failure('sensitive_execution_failed', exc.details.get('execution'))
                _write(job / 'failure.json', _json(exc.details))
            except KeyboardInterrupt as exc:
                safe = _failure('cancel_requested', getattr(exc, 'forsic_execution', {}))
                exc.forsic_shell_result = safe
                exc.forsic_execution = safe['execution']
                try:
                    _write(job / 'interrupt.json', _json(getattr(exc, 'forsic_raw_output', {})))
                except OSError:
                    pass  # Cancellation and its actual execution state take priority.
                raise
            safe.update(scan_id=job.name, resume_ref=job.name,
                restricted_artifact={'ref': job.name, 'role': 'restricted_detection_artifacts', 'model_access': False},
                reused=False, limits={'selected_files': 64, 'staging_bytes': MAX_JOB_BYTES,
                    'private_artifact_bytes': MAX_PRIVATE_BYTES, 'private_artifact_files': MAX_PRIVATE_FILES,
                    'timeout_seconds': args['timeout_seconds'], 'aggregate_workspace_quota_enforced': False})
            raw = _json(safe)
            _write(job / 'safe-retained.json', raw)
            manifest.update(safe_sha256=_sha(raw), state='retained' if
                safe['execution'].get('termination_confirmed') is True else 'completion_unknown')
            _write(job / 'manifest.json', _json(manifest), replace=True)
            return safe
    except (OSError, ValueError, KeyError, TypeError) as exc:
        code = exc.args[0] if isinstance(exc,ScanRejected) and exc.args[0] in REMEDIES else 'sensitive_scan_rejected'
        safe = _failure(code, safe.get('execution'))
        if job is not None:
            try:
                _write(job / 'host-failure.json', _json({'error_type': type(exc).__name__, 'detail': str(exc)}))
                if manifest is not None:
                    safe.update(scan_id=job.name, resume_ref=job.name)
                    raw = _json(safe)
                    _write(job / 'safe-retained.json',raw,replace=True)
                    manifest.update(safe_sha256=_sha(raw),state='retained' if
                        safe['execution'].get('termination_confirmed') is True else 'completion_unknown')
                    _write(job / 'manifest.json',_json(manifest),replace=True)
            except (OSError, ValueError):
                pass
        # Never echo paths, diagnostics, input scalars or exception messages.
        return safe


def model_view(result):
    """Full safe findings stay pageable; omit whole records, not their limits."""
    body = deepcopy(result)
    findings = body.pop('findings', [])
    coverage = body.get('coverage', {})
    for container, key, omitted in ((coverage,'inputs','display_omitted_inputs'),
                                    (body,'input_refs','display_omitted_input_refs')):
        if isinstance(container.get(key),list):
            container[omitted] = max(0,len(container[key])-5)
            container[key] = container[key][:5]
    if result.get('source_ref'):
        body['findings_call'] = {'tool': 'forsic_reporting', 'arguments': {
            'action': 'source', 'source_id': result['source_ref']['id'],
            'source_version': result['source_ref']['version'], 'pointer': '/findings'}}
        if coverage.get('display_omitted_inputs'):
            body['coverage_call'] = {'tool':'forsic_reporting','arguments':{
                **body['findings_call']['arguments'],'pointer':'/coverage/inputs'}}
        if body.get('display_omitted_input_refs'):
            body['input_refs_call'] = {'tool':'forsic_reporting','arguments':{
                **body['findings_call']['arguments'],'pointer':'/input_refs'}}
    body['findings'] = []
    body['display_omitted_findings'] = len(findings)
    for finding in findings[:10]:
        body['findings'].append(finding)
        body['display_omitted_findings'] -= 1
        if len(_json(body)) > 8000:
            body['findings'].pop()
            body['display_omitted_findings'] += 1
            break
    return body


def report_appendix(case, source_ids, *, max_findings=40, max_bytes=32768):
    """Only the local report renderer calls this; never add it to model state."""
    result = {'schema': 'forsic-sensitive-appendix-1', 'entries': [], 'total_findings': 0,
        'returned_findings': 0, 'omitted_findings': 0, 'omitted_bytes': 0,
        'counts_are_lower_bounds': False, 'unretained_findings': 0,
        'contains_sensitive_originals': False, 'limitations': []}
    seen, used = set(), 0
    for source_id in dict.fromkeys(source_ids):
        event = case.event(source_id)
        if not event or event['kind'] != 'tool_result' or event['data'].get('schema') != SCHEMA:
            continue
        scan_id = event['data'].get('scan_id')
        if not isinstance(scan_id, str) or not IDENTIFIER.fullmatch(scan_id) or scan_id in seen:
            continue
        seen.add(scan_id)
        try:
            job = _base(case, create=False) / scan_id
            manifest = json.loads(_read(job / 'manifest.json'))
            if manifest['identity']['case_id'] != case.config['case_id']:
                raise ValueError('Case mismatch')
            raw = _read(job / 'output' / 'result.json')
            if _sha(raw) != manifest.get('private_sha256'):
                raise ValueError('Changed private result')
            private = json.loads(raw)
            safe_raw = _read(job / 'safe-retained.json')
            if _sha(safe_raw) != manifest.get('safe_sha256'):
                raise ValueError('Changed safe result')
            retained = json.loads(safe_raw)
            lower_bound = retained.get('summary',{}).get('counts_are_lower_bounds') is True
            result['counts_are_lower_bounds'] |= lower_bound
            if lower_bound:
                result['unretained_findings'] = None
            elif result['unretained_findings'] is not None:
                result['unretained_findings'] += retained.get('findings_omitted',0)
            inputs = {x['input_id']: x for x in manifest['identity']['inputs']}
            staged_inputs = {}
            findings = private.get('findings', [])
            result['total_findings'] += len(findings)
            for finding in findings:
                item = inputs[finding['input_id']]
                excerpt = finding['excerpt']
                start, end = finding['excerpt_byte_start'], finding['excerpt_byte_end']
                if item['input_id'] not in staged_inputs:
                    staged_inputs[item['input_id']] = _read(job / 'inputs' / item['input_id'], MAX_JOB_BYTES)
                    if _sha(staged_inputs[item['input_id']]) != item['sha256']:
                        raise ValueError('Changed staged input')
                staged = staged_inputs[item['input_id']]
                if not (0 <= start <= end <= len(staged)):
                    raise ValueError('Changed staged input or invalid excerpt')
                codec = finding.get('encoding', 'utf-8')
                if codec not in ('utf-8', 'utf-8-sig', 'utf-16-le', 'utf-16-be', 'cp949'):
                    raise ValueError('Unsupported excerpt codec')
                if staged[start:end].decode(codec, errors='strict') != excerpt:
                    raise ValueError('Excerpt does not match retained input bytes')
                size = len(excerpt.encode('utf-8'))
                if len(result['entries']) >= max_findings or used + size > max_bytes:
                    result['omitted_findings'] += 1
                    result['omitted_bytes'] += end-start
                    continue
                result['entries'].append({k: finding[k] for k in ('finding_id', 'input_id', 'excerpt',
                    'excerpt_byte_start', 'excerpt_byte_end', 'match_byte_start', 'match_byte_end')} | {
                    'encoding': codec, 'source_ref': item['source_ref'],
                    'original_location': item['original_location'], 'source_role': item['source_role']})
                used += size
        except (OSError, ValueError, KeyError, TypeError):
            result['limitations'].append('선택한 탐지 결과의 원문 별첨을 검증하지 못했습니다. 원문이나 무검출로 대체하지 않았습니다.')
    result['returned_findings'] = len(result['entries'])
    result['unavailable_findings'] = result['total_findings'] - result['returned_findings'] - result['omitted_findings']
    result['contains_sensitive_originals'] = bool(result['entries'])
    return result
