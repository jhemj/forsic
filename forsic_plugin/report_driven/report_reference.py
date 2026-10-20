"""Resolve an exact rendered report, with recoverable failures and no writes."""
import hashlib
import json
import re


class ReportReferenceError(ValueError):
    def __init__(self, message, status, state, *, bundle_id=None, manifest=None, options=None,
                 report_scope=None):
        super().__init__(message)
        selected = (manifest or {}).get('question_ids')
        if not isinstance(selected,list) or any(not isinstance(key,str) for key in selected):
            selected=None
        allowed = [q['id'] for q in state['questions'] if not report_scope or
                   all(q.get(key, '') == value for key, value in report_scope.items())]
        arguments = {'action': 'render'}
        if selected and set(selected) <= set(allowed):
            arguments['question_ids'] = selected
        elif report_scope:
            arguments['question_ids'] = allowed
        if options:
            arguments.update({key: options[key] for key in ('redact', 'review_id') if options.get(key)})
        self.result = {'error': message, 'error_code': status, 'finalized': False,
            'delivery_status': status, 'draft_preserved': True, 'published': False,
            'report_reference': {'bundle_id': bundle_id,
                'snapshot_id': (manifest or {}).get('snapshot_id')},
            'current_snapshot_id': state['meta']['snapshot_id'],
            'recovery': {'tool': 'forsic_reporting', 'arguments': arguments},
            'next_step': 'Run the recovery render call. If it requests composition, follow that native writing packet and render again. Finalize only the bundle_id returned by a completed render. Do not pair an old bundle with a new snapshot or repeat state lookup.'}
        # Legacy masked bundles did not retain masking terms. Claiming an
        # unmasked render is safe recovery would expose material unexpectedly.
        if (manifest or {}).get('redacted') and options is None:
            self.result['recovery'] = None
            self.result['next_step'] = 'Re-render with the original literal redact terms and current question scope, then finalize its returned bundle_id. Legacy masking terms were not retained.'
        elif status == 'invalid_report_reference' and options is None:
            self.result['recovery'] = None
            self.result['next_step'] = 'Use the last trusted render arguments, including literal masking terms and question scope, to create a new report. This invalid reference cannot supply trusted rendering options.'


def scope_from_args(args):
    return {key: args.get('_' + key, '') for key in ('session_id', 'goal_id')}


def rendering_options(case, manifest):
    if 'render_options_sha256' not in manifest:
        return None
    path = case.output / ('report-bundle-' + manifest['bundle_id']) / '.render-options.json'
    if path.is_symlink():
        raise ValueError('Invalid report options path')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['render_options_sha256']:
        raise ValueError('Report rendering options changed')
    return json.loads(raw)


def resolve_report(case, state, bundle_id, *, snapshot_id=None, report_scope=None):
    """Validate files, immutable snapshot, case/goal scope and current content.

    An explicit snapshot is an assertion about this exact bundle. It cannot
    update the bundle's identity or bypass its current-content comparison.
    """
    from .finalization import report_files
    from .views import bundle_stale
    manifest = None
    options = None
    def fail(message, status):
        raise ReportReferenceError(message, status, state, bundle_id=bundle_id,
            manifest=manifest, options=options, report_scope=report_scope)
    try:
        manifest, files = report_files(case.output, bundle_id, case.config['case_id'])
        sid = manifest.get('snapshot_id', '')
        if not isinstance(sid, str) or not re.fullmatch('[a-f0-9]{64}', sid):
            raise ValueError('Invalid report snapshot')
        private = case.output / ('report-state-' + sid + '.json')
        if private.is_symlink():
            raise ValueError('Invalid report state path')
        raw = private.read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest.get('state_sha256'):
            raise ValueError('Report state changed')
        saved = json.loads(raw)
        if saved['meta']['case_id'] != case.config['case_id'] or saved['meta']['snapshot_id'] != sid:
            raise ValueError('Report state case or snapshot does not match')
        ids = manifest['question_ids']
        saved_ids = {q['id'] for q in saved['questions']}
        if not isinstance(ids, list) or not ids or not set(ids) <= saved_ids or len(ids) != len(set(ids)):
            raise ValueError('Invalid report question selection')
        if manifest.get('selection_mode') != 'selected_questions' and set(ids) != saved_ids:
            raise ValueError('Report scope differs from its immutable snapshot')
        options = rendering_options(case, manifest)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        fail('Report reference is invalid: ' + str(exc), 'invalid_report_reference')
    if snapshot_id is not None and snapshot_id != sid:
        fail('Explicit snapshot_id does not identify this rendered bundle; use its bundle_id alone.',
             'report_snapshot_mismatch')
    if report_scope and any(report_scope.values()):
        if 'report_scope' in manifest and manifest['report_scope'] != report_scope:
            fail('Rendered report belongs to another conversation or goal.', 'report_scope_mismatch')
        selected = [q for q in saved['questions'] if q['id'] in ids]
        if any(any(q.get(key, '') != value for key, value in report_scope.items()) for q in selected):
            fail('Rendered questions do not belong to the current conversation and goal.',
                 'report_scope_mismatch')
    try:
        stale = bundle_stale(case, manifest, state)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        fail('Report reference is invalid: ' + str(exc), 'invalid_report_reference')
    if stale:
        fail('Case changed; rendered answers, evidence or scope are stale.', 'stale_report_reference')
    return manifest, files, saved
