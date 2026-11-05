"""Read-only completion work over the existing case/goal/report authorities.

No scheduler, provider dispatch, adopted judgment, or mutable ready ledger lives
here. Unreadable or unstructured requirements remain unknown, never zero work.
Native owns clock arithmetic, operation admission and the terminal decision.
"""
import hashlib
import json
import re
import sqlite3
import stat
import threading
from collections import OrderedDict
from contextlib import closing
from copy import deepcopy
from pathlib import Path


# Disposable optimization only. Final selection still runs the canonical hash
# gates. Unknown, changing or oversized dependency sets never enter this cache.
_PROJECTION_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
_CACHE_ENTRIES = 16
_CACHE_PATHS = 4096
_CACHE_VALUE_BYTES = 1024 * 1024


def _file_stamp(path):
    def values(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)
    try:
        info = path.lstat()
        return (str(path), values(info), values(path.stat()) if stat.S_ISLNK(info.st_mode) else None)
    except FileNotFoundError:
        return (str(path), None)


def _cache_identity(resolved, options):
    goal = resolved['goal']
    return _digest([str(resolved['case'].manifest), _digest(resolved['case'].config), str(resolved.get('native_db')),
        resolved['case_id'], resolved['goal_id'], resolved['conversation_id'], resolved['session_lineage'],
        [goal.created_at, goal.goal, goal.max_turns, goal.turns_used,
         getattr(goal, 'runtime_followup_of', None),
         getattr(goal, 'runtime_followup_user_row_id', None)], options])


def _cache_stamp(resolved):
    """Cheap conservative stamp; no report text/evidence contents are read."""
    from hermes_cli.config import get_config_path
    case = resolved['case']
    paths = {case.manifest, case.db, Path(str(case.db) + '-wal'),
             case.output, get_config_path()}
    with closing(sqlite3.connect(case.db.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        # All immutable event revisions, conservatively including housekeeping.
        ledger = db.execute('SELECT max(rowid),count(*) FROM events').fetchone()
    for child in case.output.iterdir():
        if child.suffix == '.json':
            paths.add(child)  # request/response, attempt and retained snapshots
        elif child.name.startswith('report-bundle-') or child.name == '.word-exports':
            paths.add(child)
            # Renderer directories are small; never follow an unexpected link.
            if child.is_dir() and not child.is_symlink():
                for item in child.rglob('*'):
                    paths.add(item)
                    if len(paths) > _CACHE_PATHS:
                        return None
        if len(paths) > _CACHE_PATHS:
            return None
    # Accepted old-report handoffs also read immutable native message receipts.
    if getattr(resolved['goal'], 'runtime_followup_user_row_id', None):
        native = resolved.get('native_db')
        if native:
            paths.update((Path(native), Path(str(native) + '-wal')))
    return _digest([ledger, [_file_stamp(path) for path in sorted(paths)]])


def cached_project(resolved, **options):
    """Reuse only a fully calculated projection with identical dependencies."""
    key = _cache_identity(resolved, options)
    try:
        before = _cache_stamp(resolved)
    except (OSError, ValueError, sqlite3.Error):
        before = None
    if before is not None:
        with _CACHE_LOCK:
            cached = _PROJECTION_CACHE.get(key)
            if cached and cached[0] == before:
                _PROJECTION_CACHE.move_to_end(key)
                return deepcopy(cached[1])
    result = project(resolved, **options)
    try:
        after = _cache_stamp(resolved) if before is not None else None
    except (OSError, ValueError, sqlite3.Error):
        after = None
    review_work = result.get('review_work', {})
    if (before is not None and after == before and result.get('status') == 'ready'
            and not review_work.get('inflight_review_ids')
            and review_work.get('status') not in ('in_progress', 'delivery_unconfirmed', 'unavailable')
            and len(json.dumps(result, ensure_ascii=False).encode()) <= _CACHE_VALUE_BYTES):
        with _CACHE_LOCK:
            _PROJECTION_CACHE[key] = (after, deepcopy(result))
            _PROJECTION_CACHE.move_to_end(key)
            while len(_PROJECTION_CACHE) > _CACHE_ENTRIES:
                _PROJECTION_CACHE.popitem(last=False)
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _unknown(code, identity=None):
    return {'status': 'unknown', 'identity': identity, 'basis': None,
            'policy': {}, 'blockers': [code], 'review_work': {}, 'artifact_work': {},
            'quota': {}, 'estimate': {'remaining_seconds': None, 'known': False,
                'queue_seconds': None, 'uncertainty_seconds': None},
            'next_action': None, 'digest': _digest([identity, code]),
            'checkpoint_context': {'status': 'unknown', 'blockers': [code]},
            'artifact_status': 'unknown', 'delivery_status': 'unknown'}


def _word_status(case, manifest):
    """Return an already verified DOCX identity, without invoking the exporter."""
    from .report_driven.finalization import report_files
    from .report_driven import word_export as word
    _, files = report_files(case.output, manifest['bundle_id'], case.config['case_id'])
    root = case.output / ('report-bundle-' + manifest['bundle_id'])
    for item in files:
        if item['name'].endswith('.docx'):
            return {'path': str(root / item['name']), 'sha256': item['sha256'], 'bytes': item['bytes']}
    if manifest.get('document_formats') != word.FORMATS:
        return None
    inputs = word.validate_inputs(root, manifest.get('lazy_word'))
    descriptor = manifest['lazy_word']
    binding = {'schema': word.SCHEMA, 'case_id': case.config['case_id'],
               'bundle_id': manifest['bundle_id'], 'snapshot_id': manifest['snapshot_id'],
               'renderer_contract': descriptor['renderer_contract'],
               'inputs_sha256': inputs['inputs_sha256']}
    cache = case.output / '.word-exports' / word._sha(word._encoded(binding))
    if not cache.exists():
        return None
    word._directory(cache.parent)
    word._directory(cache)
    receipt = json.loads(word._read(cache / 'receipt.json', 64 * 1024))
    if (any(receipt.get(key) != value for key, value in binding.items()) or
            receipt.get('original_renderer_sha256') != descriptor['renderer_sha256'] or
            not re.fullmatch('[a-f0-9]{64}', str(receipt.get('actual_renderer_sha256', ''))) or
            receipt.get('receipt_sha256') != word._sha(word._encoded(
                {key: value for key, value in receipt.items() if key != 'receipt_sha256'}))):
        raise ValueError('Word receipt changed')
    raw = word._read(cache / 'integrated.docx', 128 * 1024 * 1024)
    if len(raw) != receipt.get('bytes') or word._sha(raw) != receipt.get('sha256'):
        raise ValueError('Word artifact changed')
    return {'path': str(cache / 'integrated.docx'), 'sha256': receipt['sha256'], 'bytes': receipt['bytes']}


def _artifact(resolved, state, ids, scope, review_context, formats, layout_required):
    from .report_driven.finalization import _review_gate
    from .report_driven.report_reference import resolve_report
    case = resolved['case']
    with case.connect() as db:
        # Metadata only; this is not the bounded recent compose-navigation window.
        selections = list(db.execute("SELECT id,json_extract(data,'$.bundle_id'),"
            "json_extract(data,'$.snapshot_id') FROM events WHERE kind='report_finalized' "
            "AND json_extract(data,'$.case_id')=? AND json_extract(data,'$.selection')='final_delivery_copy' "
            "ORDER BY rowid DESC", (resolved['case_id'],)))
    paths = sorted(case.output.glob('report-bundle-*/manifest.json'))
    selected, selection_order = {}, {}
    for index, row in enumerate(selections):
        selected.setdefault(row[1], row)
        selection_order.setdefault(row[1], index)
    # Canonical receipt order, never a bundle hash's arbitrary lexical position.
    paths.sort(key=lambda path: selection_order.get(
        path.parent.name.removeprefix('report-bundle-'), len(selection_order)))
    failures = []
    complete_state = None
    for path in paths:
        try:
            raw = json.loads(path.read_text())
            if raw.get('case_id') != resolved['case_id'] or sorted(raw.get('question_ids', [])) != ids:
                continue
            # A foreign explicit scope is not a broken artifact in this goal.
            if raw.get('report_scope') and raw['report_scope'] != scope:
                continue
            # Older whole-case bundles bind all canonical content, whereas the
            # bounded workspace replay deliberately selects referenced inputs.
            # Never substitute that selected UI view for whole-case freshness.
            validation_state = state
            if raw.get('selection_mode') != 'selected_questions':
                if complete_state is None:
                    from .report_driven.host import current
                    complete_state = current(case)
                validation_state = complete_state
            manifest, files, _ = resolve_report(case, validation_state, raw['bundle_id'], report_scope=scope)
            gate_review, blocked = _review_gate(case, validation_state, manifest, review_context)
            # resolve_report permits only the canonical generated basenames and
            # has already checked these exact files, hashes and symlink policy.
            root = case.output / ('report-bundle-' + manifest['bundle_id'])
            artifacts = [{'path': str(root / item['name']), 'sha256': item['sha256'], 'bytes': item['bytes']}
                         for item in files]
            found = ['html'] if any(item['name'].endswith('.html') for item in files) else []
            if 'docx' in formats:
                word = _word_status(case, manifest)
                if word:
                    found.append('docx')
                    if word not in artifacts:artifacts.append(word)
            row = selected.get(manifest['bundle_id'])
            chosen = bool(row and row[2] == manifest['snapshot_id'] and not blocked)
            layout = manifest.get('layout_review', {}).get('status', 'unverified')
            # The existing renderer records not_rendered but has no verified
            # screen/print receipt writer. A status string is not such proof.
            layout_pending = layout_required
            return {'bundle_id': manifest['bundle_id'], 'snapshot_id': manifest['snapshot_id'],
                    'selection_receipt': row[0] if chosen else None,
                    'files': files, 'artifacts': artifacts, 'formats_present': found, 'layout': layout,
                    'render_needed': bool(blocked and blocked['delivery_status'] == 'independent_review_rerender_required'),
                    'selection_needed': not chosen, 'export_needed': [f for f in formats if f not in found],
                    'layout_needed': bool(layout_pending),
                    'review_blocker': blocked['delivery_status'] if blocked else None,
                    'review_satisfied': blocked is None,
                    'availability_status': (gate_review or {}).get('availability_status'),
                    'availability_limitation': (gate_review or {}).get('availability_limitation', ''),
                    'integrity': 'passed', 'prior_invalid_bundles': failures}
        except (OSError, ValueError, KeyError, TypeError):
            failures.append(path.parent.name)
    return {'render_needed': True, 'selection_needed': True, 'export_needed': list(formats),
            'layout_needed': bool(layout_required), 'integrity': 'unavailable' if failures else 'not_created',
            'prior_invalid_bundles': failures, 'files': [], 'artifacts': []}


def project(resolved, *, completion_contract=None, enabled=False, executive_followup=False):
    from . import independent_review as review
    from .independent_review_repair import completion_quota, reservation_blocker
    from .report_workspace import selected_basis, clean_basis
    from .report_driven import narrative
    from .report_driven.independent_gate import retained_attempt
    from .investigation_context import report_followup_selection
    goal = resolved['goal']
    identity = {'case_id': resolved['case_id'], 'goal_id': resolved['goal_id'],
                'goal_identity': [goal.created_at, goal.goal],
                'conversation_id': resolved['conversation_id'],
                'session_lineage': resolved['session_lineage']}
    state = clean_basis(selected_basis(resolved['case']))
    resolved = review.native_report_review_scope(resolved)
    owned = review._question_records(resolved, include_report_source=True)
    ids = sorted(resolved.get('_report_question_ids', [q['id'] for q in owned]))
    if not ids:
        return _unknown('question_scope_not_saved', identity)
    identity['question_scope'] = ids
    # Scope narrative just as the existing report handoff does, not by newest file.
    state = {**state, 'report_narratives': [n for n in state.get('report_narratives', [])
             if n.get('goal_id') == resolved['goal_id'] and n.get('session_id') in resolved['session_lineage']]}
    progress = narrative.status(state, ids)
    scoped = review.report_review_scope(resolved, ids)
    current_review = review.review_state(scoped, state) if enabled else {}
    context = {'enabled': enabled, 'resolved': scoped,
               'executive_followup_required': executive_followup}
    attempt_metadata = {}
    if enabled:
        attempt = retained_attempt(scoped)
        if attempt:
            context.update(attempt=attempt['attempt'], attempt_basis=attempt['basis'])
            attempt_metadata = attempt['attempt']
    contract = completion_contract if isinstance(completion_contract, dict) else {}
    formats = contract.get('required_formats')
    format_known = (isinstance(formats, list) and bool(formats) and
                    all(fmt in ('html', 'docx') for fmt in formats))
    formats = sorted(set(formats)) if format_known else []
    policy = {'required_formats': formats if format_known else None,
              'required_reviews': {'independent': enabled, 'executive': executive_followup},
              'layout_required': contract.get('layout_required'),
              'completion_target': contract.get('completion_target'),
              'policy_revision': _digest([contract, enabled, executive_followup])}
    blockers = []
    pinned_review = contract.get('required_review')
    policy['review_configuration_matches'] = pinned_review is None or pinned_review is enabled
    if not policy['review_configuration_matches']:blockers.append('review_policy_configuration_mismatch')
    if not format_known:blockers.append('required_formats_unknown')
    if type(contract.get('layout_required')) is not bool:blockers.append('layout_requirement_unknown')
    if contract.get('layout_required') is True:blockers.append('layout_verification_receipt_required')
    if contract.get('completion_target') != 'final_ready':blockers.append('completion_target_unknown')
    if not progress['content_complete']:blockers.append('authoring_incomplete')
    if progress['judgment_review_questions']:blockers.append('current_judgment_review_required')
    if progress['review_status'] != 'complete':blockers.append('document_review_required')
    if attempt_metadata.get('diagnostic') and attempt_metadata.get('followup_action') == 'repair':
        blockers.append('review_attempt:' + attempt_metadata['diagnostic'].get('code', 'unknown'))
    rounds = current_review.get('executive_followup', {}).get('rounds', [])
    remaining_rounds = max(0, review.EXECUTIVE_ROUNDS - sum(bool(r['complete']) for r in rounds)) if executive_followup else 0
    if remaining_rounds:blockers.append('executive_dialogue_incomplete')
    pending = current_review.get('pending_finding_ids', [])
    if pending:blockers.append('review_findings_pending')
    quota = completion_quota(scoped, review._receipts(scoped),
                             final_current=current_review.get('final_review_current', False)) if enabled else {
        'cap': 0, 'used': 0, 'remaining': 0, 'required_reserved': 0,
        'available_for_optional_work': 0, 'contract_infeasible': False}
    repair_blocker = reservation_blocker(scoped, review._receipts(scoped), rounds) if enabled and rounds else None
    if repair_blocker:blockers.append('review_final_slot_reserved')
    scope = {'session_id': resolved['conversation_id'], 'goal_id': resolved['goal_id']}
    followup = report_followup_selection(resolved)
    if followup:scope.update(source_goal_id=followup['source_goal_id'], source_question_ids=followup['question_ids'])
    artifact = _artifact(scoped, state, ids, scope, context, formats, contract.get('layout_required') is True)
    if current_review.get('status') == 'unavailable' and not artifact.get('review_satisfied'):
        blockers.append('review_state_unknown')
    if 'docx' in artifact['export_needed'] and artifact.get('bundle_id'):
        from urllib.parse import quote
        artifact['export_action'] = {'method': 'POST', 'url': '/api/plugins/forsic/report-bundles/' +
            artifact['bundle_id'] + '/word?case_id=' + quote(resolved['case_id'], safe=''),
            'meaning': 'Existing explicit saved-version Word export; projection does not invoke it.'}
    if artifact.get('review_blocker'):blockers.append(artifact['review_blocker'])
    if artifact['integrity'] == 'unavailable':blockers.append('artifact_integrity_unknown')
    # An actual selected bundle may use the existing disclosed-availability
    # policy. It is deliverable, but is never relabelled a passed review.
    final_needed = (enabled and not current_review.get('final_review_current', False)
                    and not artifact.get('review_satisfied'))
    if not final_needed:
        quota.update(required_reserved=0, contract_infeasible=False,
                     available_for_optional_work=quota['remaining'])
    if quota['contract_infeasible']:blockers.append('ordinary_review_contract_infeasible')
    if final_needed and not artifact.get('selection_receipt'):blockers.append('current_final_review_needed')
    review_work = {'status': current_review.get('status', 'disabled'),
        'inflight_review_ids': current_review.get('inflight_review_ids', []),
        'remaining_rounds': remaining_rounds, 'unanswered_findings': pending,
        'repair_options': current_review.get('review_repair'), 'final_review_needed': final_needed,
        'repair_blocker': repair_blocker,
        'progress_fingerprint': current_review.get('progress_fingerprint'), 'last_attempt': attempt_metadata}
    artifact['delivery_needed'] = None  # Selection is not a remote delivery receipt.
    ready = (progress['ready'] and not blockers and not artifact['selection_needed']
             and not artifact['export_needed'] and not artifact['layout_needed'])
    limitations = ([{'kind': 'independent_review_availability',
                     'status': artifact.get('availability_status'),
                     'description': artifact['availability_limitation']}]
                   if artifact.get('availability_limitation') else [])
    action = ('compose' if not progress['ready'] else 'review_inbox' if pending else
              'render' if artifact['render_needed'] else 'finalize' if artifact['selection_needed'] else None)
    arguments = {'action': action, 'question_ids': ids} if action else None
    if action == 'finalize':arguments.update(bundle_id=artifact['bundle_id'], snapshot_id=artifact['snapshot_id'])
    basis = {key: progress[key] for key in ('basis_id', 'semantic_basis_id', 'draft_revision', 'draft_hash')}
    basis['judgment_revision'] = {q['id']: q.get('revision') for q in owned if q['id'] in ids}
    basis['judgment_review_question_ids'] = [q['question_id'] for q in progress['judgment_review_questions']]
    basis['evidence_snapshot'] = state['meta']['snapshot_id']
    basis['source_ids'] = sorted(source['id'] for source in state['sources'])
    basis['missing_groups'] = progress['missing_groups']
    basis['stale_groups'] = progress['stale_fields']
    work = []
    for key, complete, dependencies in (
        ('judgment', not progress['judgment_review_questions'], []),
        ('compose', progress['content_complete'], ['judgment']),
        ('document_review', progress['ready'], ['compose']),
        ('executive', not remaining_rounds and not pending, ['document_review']),
        ('final_review', not final_needed, ['executive']),
        ('render', not artifact['render_needed'], ['final_review']),
        ('formats', format_known and not artifact['export_needed'] and not artifact['layout_needed'], ['render']),
        ('selection', not artifact['selection_needed'], ['formats']),
    ):
        work.append({'id': key, 'status': 'complete' if complete else 'pending',
                     'depends_on': dependencies})
    result = {'status': 'ready', 'identity': identity, 'basis': basis, 'policy': policy,
        'blockers': sorted(set(blockers)), 'review_work': review_work, 'artifact_work': artifact,
        'quota': {'ordinary': quota, 'native_turns': {'cap': goal.max_turns, 'used': goal.turns_used,
                  'required_reserved': 0 if ready else 1, 'unit': 'turns'},
                  'main_calls': {'status': 'unknown', 'authority': 'native_per_turn_iteration_budget'}}, 'work': work,
        'estimate': {'remaining_seconds': None, 'known': False, 'queue_seconds': None,
                     'uncertainty_seconds': None, 'source': 'unknown'},
        'next_action': {'tool': 'forsic_reporting', 'arguments': arguments} if action else None,
        'artifacts': artifact['artifacts'], 'limitations': limitations,
        'artifact_status': ('final_ready_with_limitations' if limitations else 'final_ready') if ready else
                           'draft_preserved' if progress['draft_revision'] else 'partial_checkpoint',
        'delivery_status': 'local_ready' if ready else 'not_delivered'}
    result['digest'] = _digest(result)
    result['checkpoint_context'] = {key: result[key] for key in
        ('identity', 'basis', 'blockers', 'review_work', 'artifact_status', 'limitations', 'delivery_status', 'next_action')}
    return result


def _verification_binding(projection, action, resolved):
    """Reuse a saved discriminating mission and a genuinely bounded tool path.

    A fixed arbitrary shell command still has no enforceable target selection.
    Only the existing selected-file static scanner supplies all of target,
    byte, match, runtime and output bounds; native owns the remaining deadline.
    """
    from .report_driven.host import current, find
    from .report_driven.investigation_state import validate_execution_context, normalized_tool_arguments
    from .shell_workspace import validate, MAX_OUTPUT_BYTES
    args = action['arguments']
    if (not resolved or action.get('tool') != 'forsic_shell' or args.get('action') != 'static_scan'
            or 'current_judgment_review_required' not in projection['blockers']):
        return None
    try:
        # Same read-only identity/input validator as the actual atomic admission.
        context = validate_execution_context(resolved['case'], args['mission_id'], args['mission_version'],
            'forsic_shell', args, goal_id=resolved['goal_id'], session_id=resolved['conversation_id'])
        state = current(resolved['case'])
        mission = find(state, 'missions', context['mission_id'])
        question = find(state, 'questions', context['question_id'])
        gap = find(state, 'gaps', mission['gap_ref']['id'])
        if (question['id'] not in projection['basis']['judgment_review_question_ids']
                or not question.get('judgment_review_required')
                or gap['question_ref']['id'] != question['id']
                or gap['id'] not in question['remaining_gap_ids']
                or gap['version'] != mission['gap_ref']['version']
                or gap['disposition'] in ('resolved', 'not_applicable')
                or gap['impact'] not in ('material', 'decision_critical')
                or mission['design_timing'] != 'before_result'
                or not (mission.get('support_rule') or mission.get('refute_rule'))):
            return None
        checked = validate(normalized_tool_arguments(args))
        if len(checked['paths']) > 4:
            return None
        return {'arguments_sha256': hashlib.sha256(json.dumps(args, sort_keys=True,
                    ensure_ascii=False).encode()).hexdigest(),
                'blocker': 'current_judgment_review_required',
                'scope': {'targets': ['/work/' + path for path in checked['paths']],
                    'question_id': question['id'], 'gap_id': gap['id'],
                    'mission_id': mission['id'], 'mission_version': mission['version']},
                'max_seconds': checked['timeout_seconds'], 'max_output_bytes': MAX_OUTPUT_BYTES,
                'max_input_bytes': checked['max_bytes'], 'max_matches': checked['max_matches'],
                'scan_seconds': checked['max_seconds'],
                'authority': 'saved_mission_and_existing_static_scan_execution_contract'}
    except (KeyError, TypeError, ValueError):
        return None


def _current_judgment_write(projection, action, resolved):
    """Admit completion of a required owned judgment, not new investigation scope."""
    if not resolved or action.get('tool') != 'forsic_question':return None
    payload=action.get('arguments')
    if not isinstance(payload,dict) or not payload.get('question_id'):return None
    try:
        from .report_driven.host import current,find
        from .report_driven.investigation_state import check_question_revision,question_state_problems
        state=current(resolved['case'])
        question=find(state,'questions',payload['question_id'])
        if question['id'] not in projection['identity']['question_scope']:return None
        scope={'_goal_id':resolved['goal_id'],'_session_id':resolved['conversation_id']}
        check_question_revision(question,payload,goal_id=scope['_goal_id'],session_id=scope['_session_id'])
        if question_state_problems(payload,state,scope)[0]:return None
        if any(key in payload and payload[key]!=question[key] for key in ('question','target_proposition','scope')):
            return None
        blocker=None
        if ('current_judgment_review_required' in projection.get('blockers',[])
                and question['id'] in projection['basis']['judgment_review_question_ids']
                and question.get('judgment_review_required')):
            blocker='current_judgment_review_required'
        elif 'review_findings_pending' in projection.get('blockers',[]) and payload.get('review_responses'):
            from .independent_review import review_state
            pending={(f['review_id'],f['finding_id']) for f in review_state(resolved,state)['pending_findings']
                     if f.get('impact')=='material'}
            if any(isinstance(response,dict) and (response.get('review_id'),response.get('finding_id')) in pending
                   for response in payload['review_responses']):
                blocker='review_findings_pending'
        if blocker is None:return None
        return {'blocker':blocker,'scope':{'question_id':question['id'],
                'expected_revision':question['revision'],'snapshot_id':state['meta']['snapshot_id']}}
    except (KeyError,TypeError,ValueError):return None


def _current_test_assessment(projection, action, resolved):
    """Finish a returned, pre-existing discriminating test without new collection."""
    if (not resolved or action.get('tool') != 'forsic_assess'
            or 'current_judgment_review_required' not in projection.get('blockers', [])):
        return None
    try:
        from .report_driven.host import current, find
        from .report_driven.assessment_basis import expand_selected
        state = current(resolved['case'])
        payload = action['arguments']
        selected = expand_selected(resolved['case'], state, payload,
            goal_id=resolved['goal_id'], session_id=resolved['conversation_id'])
        mission = find(state, 'missions', selected['mission_id'])
        question = find(state, 'questions', mission['question_ref']['id'])
        gap = find(state, 'gaps', mission['gap_ref']['id'])
        if (question['id'] not in projection['basis']['judgment_review_question_ids']
                or not question.get('judgment_review_required')
                or gap['id'] not in question['remaining_gap_ids']
                or gap['question_ref']['id'] != question['id']
                or gap['version'] != mission['gap_ref']['version']
                or gap['impact'] not in ('material', 'decision_critical')
                or gap['disposition'] in ('resolved', 'not_applicable')
                or mission['design_timing'] != 'before_result'
                or selected['result_id'] not in mission['result_ids']
                or not (mission.get('support_rule') or mission.get('refute_rule'))):
            return None
        test = find(state, 'tests', 'T-' + mission['id'])
        if any(a['test_ref']['id'] == test['id'] and a['test_ref']['version'] == test['version']
               and a['result_ref']['id'] == selected['result_id'] for a in state['assessments']):
            return None
        return {'blocker': 'current_judgment_review_required', 'scope': {
            'question_id': question['id'], 'expected_revision': question['revision'],
            'mission_id': mission['id'], 'mission_version': mission['version'],
            'result_id': selected['result_id'], 'result_version': selected['result_version']}}
    except (KeyError, TypeError, ValueError):
        return None


def _current_report_correction(projection, args, resolved):
    """A retained material finding can require editing an already reviewed body."""
    if (not resolved or 'draft' not in args or 'review' in args
            or 'review_findings_pending' not in projection.get('blockers', [])):
        return None
    try:
        import hmac
        from .independent_review import review_state
        from .report_driven import narrative
        from .report_driven.host import current, find
        from .report_driven.investigation_state import check_question_revision
        state = current(resolved['case'])
        ids = projection['identity']['question_scope']
        if narrative.selection(state, {**args, '_goal_id': resolved['goal_id'],
                                       '_session_id': resolved['conversation_id']}) != ids:
            return None
        for qid in ids:
            question = find(state, 'questions', qid)
            check_question_revision(question, {'expected_revision': question['revision']},
                goal_id=resolved['goal_id'], session_id=resolved['conversation_id'])
        if not any(f.get('impact') == 'material' for f in review_state(resolved, state)['pending_findings']):
            return None
        progress = narrative.status(state, ids)
        group = args.get('section')
        if (group not in narrative.GROUP_IDS or not isinstance(args.get('edit_ref'), str)
                or not (resolved['case'].output / '.report-authoring-key').is_file()
                or 'basis_id' in args and args['basis_id'] != progress['basis_id']
                or 'expected_revision' in args and args['expected_revision'] != progress['revision']):
            return None
        expected = narrative._edit_reference(resolved['case'], progress, group,
            {'_goal_id': resolved['goal_id'], '_session_id': resolved['conversation_id']})
        if not hmac.compare_digest(args['edit_ref'], expected):
            return None
        return {'blocker': 'review_findings_pending', 'scope': {'question_ids': ids,
                'basis_id': progress['basis_id'], 'expected_revision': progress['revision'], 'section': group}}
    except (KeyError, TypeError, ValueError, OSError):
        return None


def classify_action(projection, action, *, resolved=None):
    """Only canonical operations in the current scope get report/read purpose.

    Arbitrary shell/execute_code and model-provided labels are not trusted here.
    Native still enforces the actual tool bound, budget, owner and deadline.
    """
    identity = projection.get('identity') or {}
    result = {'purpose': 'discovery', 'scope': identity,
              'blocker': None, 'max_seconds': None,
              'projection_digest': projection.get('digest')}
    if projection.get('status') != 'ready' or not isinstance(action, dict):return result
    tool, args = action.get('tool'), action.get('arguments')
    if not isinstance(args, dict):return result
    ids = identity.get('question_scope', [])
    selected = args.get('question_ids', ids)
    if (not isinstance(selected, list) or sorted(selected) != ids or
            args.get('question_id', ids[0] if ids else None) not in ids):return result
    work = {node['id']: node['status'] for node in projection.get('work', [])}
    verb = args.get('action')
    if tool == 'forsic_reporting':
        if verb in ('state', 'gaps', 'review_inbox'):
            result.update(purpose='read', blocker='current_report_state')
        elif verb == 'source' and args.get('source_id') in projection['basis']['source_ids']:
            result.update(purpose='read', blocker='selected_evidence')
        elif verb == 'compose':
            if 'draft' not in args and 'review' not in args:
                result.update(purpose='read', blocker='current_report_body')
            elif work.get('document_review') == 'pending':
                result.update(purpose='report', blocker='document_review' if work.get('compose') == 'complete' else 'compose')
            else:
                correction = _current_report_correction(projection, args, resolved)
                if correction:
                    result.update(purpose='report', **correction)
        elif verb == 'render' and projection['artifact_work']['render_needed']:
            result.update(purpose='report', blocker='render')
        elif (verb == 'finalize' and projection['artifact_work']['selection_needed'] and
              args.get('bundle_id') == projection['artifact_work'].get('bundle_id') and
              args.get('bundle_id') and args.get('snapshot_id', projection['artifact_work'].get('snapshot_id')) ==
              projection['artifact_work'].get('snapshot_id')):
            result.update(purpose='report', blocker='selection')
    payload = args if tool == 'forsic_review_response' else args.get('payload', {}) if verb == 'review_response' else {}
    if (tool in ('forsic_review_response', 'forsic_reporting') and isinstance(payload, dict)
            and payload.get('question_id') in ids and
            payload.get('finding_id') in projection['review_work']['unanswered_findings']):
        result.update(purpose='report', blocker=payload['finding_id'])
    write_scope = _current_judgment_write(projection, action, resolved)
    if write_scope:
        result.update(purpose='report', **write_scope)
    assessment_scope = _current_test_assessment(projection, action, resolved)
    if assessment_scope:
        result.update(purpose='report', **assessment_scope)
    binding = _verification_binding(projection, action, resolved)
    if binding:
        result.update(purpose='targeted_verification', blocker=binding['blocker'],
                      scope=binding['scope'], max_seconds=binding['max_seconds'],
                      verification_binding=binding)
    return result


def goal_work_projection(session_id='', goal_identity=None, mode='read', *, intake_root,
                         native_db=None, completion_contract=None, enabled=False,
                         executive_followup=False, action=None, **_):
    from .investigation_context import resolve_context
    if mode not in ('read', 'classify_action'):return _unknown('unsupported_projection_mode')
    try:
        resolved = resolve_context(session_id, intake_root=intake_root, native_db=native_db)
        if not resolved or not resolved.get('goal'):return {'status': 'not_applicable'}
        actual = [resolved['goal'].created_at, resolved['goal'].goal]
        if goal_identity != actual:return _unknown('goal_generation_mismatch', actual)
        result = cached_project(resolved, completion_contract=completion_contract, enabled=enabled,
                                executive_followup=executive_followup)
        return classify_action(result, action, resolved=resolved) if mode == 'classify_action' else result
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return _unknown('canonical_report_work_unavailable', goal_identity)
