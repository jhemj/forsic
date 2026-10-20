"""Explicit image selection from retained same-case discovery, never a last-used path."""
from copy import deepcopy

ASCII_WHITESPACE = ' \t\n\r\v\f'


def reference_schema():
    return {'type': 'object', 'required': ['id', 'version', 'index'],
        'properties': {**{key: {'type': 'string', 'minLength': 1} for key in ('id', 'version')},
            'index': {'type': 'integer', 'minimum': 0}}, 'additionalProperties': False,
        'description': 'Copy image_ref from the chosen current-case E01 inventory row or successful image result. It selects only the original image; file_path still selects a path inside it.'}


class ImageSelectionError(ValueError):
    def __init__(self, message, code='invalid_image_reference'):
        self.details = {'error_code': code,
            'recovery': {'tool': 'forsic_list', 'arguments': {'path': '.'}},
            'next_step': 'Select image_ref from the current evidence inventory or a retained successful image result. Keep file_path as the internal image path; do not infer an image target.'}
        super().__init__(message)


def _successful(data):
    execution = data.get('execution')
    return not (data.get('error') or data.get('execution_rejected') or data.get('pending')
        or data.get('status') in ('pending', 'running', 'cancelled', 'failed', 'error', 'unknown')
        or isinstance(execution, dict) and (execution.get('state') not in ('returned', 'completed')
            or execution.get('termination_confirmed') is False or execution.get('exit_code', 0) != 0))


def binding(case, path):
    from .image_reader import image_segments
    from .literal_search import file_identity
    # case.path rechecks selected-files scope; no extra scope comes from a reference.
    path = case.path(str(path))
    if not path.is_file() or path.suffix.lower() != '.e01':
        return None
    parts = image_segments(path)
    if any(part.is_symlink() for part in parts):
        raise ValueError('Image segment symlinks are not supported')
    return {'root': str(case.root), 'path': str(path.relative_to(case.root)),
        'identity': file_identity(path.stat()),
        'segments': [{'name': part.name, 'metadata': file_identity(part.stat())} for part in parts],
        'identity_strength': 'metadata_checked'}


def result_view(result, case):
    """References are a projection; their exact version hashes the retained original."""
    from .report_driven.host import source_record
    view = {key: value for key, value in result.items() if key != '_image_targets'}
    targets = result.get('_image_targets')
    if result.get('error') or not targets or not result.get('evidence_id'):
        return view
    event = case.event(result['evidence_id'])
    source = source_record(event)
    refs = [{key: source[key] for key in ('id', 'version')} | {'index': index}
            for index in range(len(targets))]
    if event['data']['tool'] == 'forsic_list':
        by_path = {target['path']: ref for target, ref in zip(targets, refs)}
        view['files'] = [{**row, **({'image_ref': by_path[row['path']]} if row['path'] in by_path else {})}
                         for row in view.get('files', [])]
    elif len(refs) == 1:
        view['image_ref'] = refs[0]
    return view


def resolve(case, args, *, allow_trim=True):
    """Resolve the explicit selection before canonical admission; preserve submitted args."""
    effective = {key: deepcopy(value) for key, value in args.items() if key != 'image_ref'}
    path = args.get('path')
    if 'image_ref' in args:
        from .report_driven.host import source_record
        from .report_driven.investigation_state import input_problems
        ref = args['image_ref']
        if (input_problems(ref, reference_schema(), 'arguments.image_ref')
                or any(not isinstance(ref.get(key), str) or not ref[key] for key in ('id', 'version'))):
            raise ImageSelectionError('image_ref must be copied exactly from a retained image selection.')
        event = case.event(ref['id'])
        if (not event or event['kind'] != 'tool_result' or not _successful(event['data'])
                or event['data'].get('tool') not in ('forsic_list', 'forsic_image_files')):
            raise ImageSelectionError('image_ref does not select successful discovery in this case.')
        if source_record(event)['version'] != ref['version']:
            raise ImageSelectionError('Selected image result version changed.', 'image_reference_version_conflict')
        targets = event['data'].get('_image_targets', [])
        if ref['index'] >= len(targets):
            raise ImageSelectionError('image_ref index was not returned by this result.')
        start = case.event(event['data'].get('started_id', ''))
        with case.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM events WHERE kind='tool_result' AND json_extract(data,'$.started_id')=?",
                               (event['data'].get('started_id'),)).fetchone()[0]
        if (not start or start['kind'] != 'tool_start' or start['session'] != event['session']
                or start['time'] > event['time'] or count != 1
                or start['data'].get('tool') != event['data']['tool']
                or not _successful(start['data'])):
            raise ImageSelectionError('Image discovery has no unambiguous matching execution.')
        selected = targets[ref['index']]
        try:
            current = binding(case, selected['path'])
        except (OSError, ValueError):
            current = None
        if current != selected:
            raise ImageSelectionError('Image scope, file identity or segment metadata changed. Re-list the selected evidence.', 'image_reference_identity_conflict')
        if event['data']['tool'] == 'forsic_list':
            matches = [row for row in event['data'].get('files', []) if row.get('path') == selected['path']]
            agrees = len(matches) == 1
        else:
            original = start['data'].get('arguments', {})
            try:
                agrees = (case.path(original['path']) == case.path(selected['path'])
                    and case.path(event['data']['path']) == case.path(selected['path']))
            except (OSError, ValueError, KeyError):
                agrees = False
        if not agrees:
            raise ImageSelectionError('Retained image target does not match its original discovery execution.')
        if 'path' in args and (not isinstance(path, str) or path.strip(ASCII_WHITESPACE)):
            try:
                agrees = isinstance(path, str) and case.path(path) == case.path(selected['path'])
            except (OSError, ValueError):
                agrees = False
            if not agrees:
                raise ImageSelectionError('Explicit path conflicts with selected image_ref; choose one exact target.', 'image_reference_path_conflict')
        effective['path'] = path if isinstance(path, str) and path.strip(ASCII_WHITESPACE) else selected['path']
        return effective, {'kind': 'retained_image', 'image_ref': deepcopy(ref),
            'identity_strength': 'metadata_checked', 'identity_limitation': 'File and segment metadata comparison; not cryptographic content equality.'}
    if isinstance(path, str) and not path.strip(ASCII_WHITESPACE):
        raise ImageSelectionError('Image path is blank. Copy an image_ref or the E01 path from the current evidence inventory.', 'image_path_required')
    if allow_trim and isinstance(path, str) and path != path.strip(ASCII_WHITESPACE):
        # An existing filename containing surrounding spaces always wins.
        try:
            case.path(path)
        except (FileNotFoundError, ValueError):
            original = (case.root / path).resolve()
            if not original.is_relative_to(case.root) or original.exists():
                return effective, None
            trimmed = path.strip(ASCII_WHITESPACE)
            try:
                candidate = case.path(trimmed)
                valid = candidate.is_file() and candidate.suffix.lower() == '.e01'
            except (OSError, ValueError):
                valid = False
            if valid:
                effective['path'] = str(candidate.relative_to(case.root))
                return effective, {'kind': 'image_path_ascii_whitespace', 'submitted_path': path,
                    'effective_path': effective['path']}
        except (OSError, ValueError):
            pass
    return effective, None
