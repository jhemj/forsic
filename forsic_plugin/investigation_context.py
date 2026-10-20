"""Read-only, goal-bound projections for native evaluation and compaction.

No current-case pointer, model call, writable Case, session creation or implicit adoption.
The projection preserves analyst judgments as judgments; it is not source evidence.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from .evidence import Case
from .report_driven import host

PROVIDER = 'forsic'
MAX_CONTEXT_BYTES = 5000  # leave envelope/marker space within the native 6 KiB handoff


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _hash(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _clip(value, limit=240):
    raw = str(value or '').encode('utf-8')
    return raw.decode() if len(raw) <= limit else raw[:limit-3].decode('utf-8', errors='ignore') + '…'


def _native_path(native_db):
    if native_db is not None:
        return Path(native_db).resolve()
    from hermes_cli.config import get_hermes_home
    return (get_hermes_home() / 'state.db').resolve()


def _binding_path(root, session_id):
    return root / (hashlib.sha256(session_id.encode()).hexdigest() + '.json')


def _compression_link(child, parent):
    config = child.get('model_config') or {}
    if isinstance(config, str):
        config = json.loads(config)
    if not isinstance(config, dict):
        raise ValueError('Native session lineage metadata is not an object')
    return (parent.get('end_reason') == 'compression' and child.get('source') != 'tool'
            and parent['id'] not in [config.get(k) for k in ('_branched_from', '_delegate_from', '_reset_from')])


def resolve_context(session_id, *, intake_root, native_db=None):
    """Resolve only this explicit binding/native parent chain; never choose a case globally.

    The native goal can be inherited only through a compression continuation. A branch,
    delegate or reset without its own goal does not borrow the parent's standing goal.
    """
    if not isinstance(session_id, str) or not session_id:
        return None
    root, native = Path(intake_root).resolve(), _native_path(native_db)
    from .tenants import current_scope, assert_session_scope, validate_case
    tenant_scope = current_scope()
    if tenant_scope and root != Path(tenant_scope['intake_root']).resolve():
        raise PermissionError('조사 문맥과 테넌트 접수 저장소가 다릅니다.')
    direct = _binding_path(root, session_id)
    if not native.is_file():
        if direct.is_file():
            raise ValueError('Bound case has no readable native session store')
        return None
    with closing(sqlite3.connect(native.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        rows, seen, current_id, manifest = [], set(), session_id, None
        while current_id:
            if current_id in seen:
                raise ValueError('Native session parent lineage contains a cycle')
            seen.add(current_id)
            if tenant_scope:
                assert_session_scope(tenant_scope, current_id, native_db=native)
            row = db.execute('SELECT id,parent_session_id,end_reason,source,model_config FROM sessions WHERE id=?', (current_id,)).fetchone()
            entry = dict(row) if row else {'id': current_id, 'parent_session_id': None}
            rows.append(entry)
            binding = _binding_path(root, current_id)
            if manifest is None and binding.is_file():
                body = json.loads(binding.read_text())
                if not isinstance(body, dict):
                    raise ValueError('Explicit case binding is not an object')
                if body.get('manifest'):
                    manifest = Path(body['manifest']).resolve(strict=True)
                    if not manifest.is_relative_to(root / 'cases'):
                        raise ValueError('Explicit case binding is outside the intake case directory')
                    if tenant_scope:
                        validate_case(tenant_scope, manifest)
            current_id = entry.get('parent_session_id')
        if manifest is None:
            return None
        # Scope lineage for goal ownership stops at actual branch/reset/delegation edges.
        conversation = [rows[0]]
        for parent in rows[1:]:
            if not _compression_link(conversation[-1], parent):
                break
            conversation.append(parent)
        for entry in conversation:
            path = _binding_path(root, entry['id'])
            if path.is_file():
                own_binding = json.loads(path.read_text())
                if not isinstance(own_binding, dict):
                    raise ValueError('Compression case binding is not an object')
                own_manifest = own_binding.get('manifest')
                if own_manifest and Path(own_manifest).resolve(strict=True) != manifest:
                    raise ValueError('Compression lineage has conflicting explicit case bindings')
        goal_state, owner = None, None
        from hermes_cli.goals import GoalState, goal_identity
        for entry in conversation:
            raw = db.execute('SELECT value FROM state_meta WHERE key=?', ('goal:' + entry['id'],)).fetchone()
            if raw:
                goal_state, owner = GoalState.from_json(raw[0]), entry['id']
                break
        if goal_state is not None and (not goal_state.goal.strip() or goal_state.status == 'cleared'):
            goal_state = None
        if goal_state is None:
            owner = conversation[-1]['id']
    case = Case(manifest, read_only=True)
    if not case.output.resolve().is_relative_to(manifest.parent):
        raise ValueError('Case ledger output is outside its explicitly bound case directory')
    return {'case': case, 'goal': goal_state, 'goal_id': goal_identity(goal_state) if goal_state else '',
            'session_id': owner, 'requested_session_id': session_id,
            'conversation_id': conversation[-1]['id'],
            'session_lineage': [entry['id'] for entry in conversation], 'case_id': case.config['case_id']}


def native_goal_binding(session_id, *, intake_root, native_db=None):
    """Metadata for trusted tool-host binding. Failure is explicit and never selects a fallback."""
    try:
        resolved = resolve_context(session_id, intake_root=intake_root, native_db=native_db)
        if resolved is None:
            return {'status': 'not_applicable'}
        return {'status': 'ready', 'goal_status': resolved['goal'].status if resolved['goal'] else None, **{key: resolved[key] for key in
                ('goal_id', 'session_id', 'requested_session_id', 'conversation_id', 'case_id', 'session_lineage')}}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        return {'status': 'unavailable', 'reason': _clip(str(exc), 300)}


def _owned_state(resolved, state):
    goal_id, lineage = resolved['goal_id'], set(resolved['session_lineage'])
    questions = [q for q in state['questions'] if q.get('goal_id') == goal_id and q.get('session_id') in lineage]
    ids = {q['id'] for q in questions}
    missions = [m for m in state['missions'] if m['question_ref']['id'] in ids]
    tests = [t for t in state['tests'] if t['question_ref']['id'] in ids]
    test_ids = {t['id'] for t in tests}
    assessments = [a for a in state['assessments'] if a['test_ref']['id'] in test_ids]
    return questions, missions, tests, assessments


def completion_state(resolved, state, *, review=None):
    """Structural/declared-work sufficiency, not a verdict about analytical truth.

    Contextual open questions may remain. A scoped closure may explicitly retain uncertainty.
    No report-wide section, semantic/layout-review, all-open or severity-count gate is used.
    """
    questions, missions, tests, assessments = _owned_state(resolved, state)
    pending = []
    if review is None:
        from .independent_review import review_state
        review = review_state(resolved, state)
    if review.get('requires_followup'):
        pending.extend('독립 검토 의견 처분 필요: '+f for f in review.get('pending_finding_ids', []))
        if not review.get('pending_finding_ids'):
            pending.append('독립 검토 기록의 상태를 확인하지 못했습니다.')
    if not questions:
        pending.append('No question is explicitly bound to this native goal; record its requested scope before declaring completion.')
    for question in questions:
        # Closure does not supply a missing priority judgment. Check it before
        # branching on work_state, including old records that bypassed the writer.
        if question['priority'] == 'unassessed':
            pending.append(question['id'] + ': importance has not yet been assessed')
        if question.get('judgment_review_required') and question['work_state']=='scoped_closed':
            pending.append(question['id']+': current judgment correction requires explicit assessment')
        if question['work_state'] == 'scoped_closed':
            if not question.get('closure_rationale') or not question.get('reopen_conditions'):
                pending.append(question['id'] + ': scope closure lacks its reason or reopening conditions')
        elif question['priority'] in ('decision_critical', 'material'):
            pending.append(question['id'] + ': important scope remains ' + question['work_state'])
    pending_results = set()
    for mission in missions:
        if mission['state'] in ('running', 'queued', 'unassessed'):
            pending.append(mission['id'] + ': native work/results remain ' + mission['state'])
            pending_results.update((mission['question_ref']['id'], rid) for rid in mission.get('result_ids', []))
    for test in tests:
        result = test.get('result_ref')
        key = (test['question_ref']['id'], result['id']) if result else None
        if result and test['assessment_state'] in ('unassessed', 'stale') and key not in pending_results:
            pending.append(test['id'] + ': returned result remains ' + test['assessment_state']
                           + ' (execution ' + test['execution_state'] + ')')
            pending_results.add(key)
    try:
        host.validate_refs(state, [questions, missions, tests, assessments])
    except ValueError:
        pending.append('Goal-bound investigation state contains missing or stale source references')
    return pending


def _report_context(resolved, state, questions):
    """Inspect existing derived artifacts only; their existence is not investigation completion."""
    question_ids = {q['id'] for q in questions}
    matches = []
    for directory in resolved['case'].output.glob('report-bundle-*'):
        if not directory.is_dir() or directory.is_symlink():
            continue
        manifest_path = directory / 'manifest.json'
        if manifest_path.is_symlink():
            raise ValueError('Report manifest is a symbolic link')
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('case_id') != resolved['case_id']:
            raise ValueError('Derived report case identity does not match its bound ledger')
        if not question_ids.intersection(manifest.get('question_ids', [])):
            continue
        matches.append((manifest_path.stat().st_mtime_ns, directory.name, directory, manifest))
    if not matches:
        return {'status': 'none_for_this_scope', 'artifact_is_completion': False}
    _, _, directory, manifest = max(matches, key=lambda row: row[:2])
    for item in manifest.get('files', []):
        if Path(item['name']).name != item['name']:
            raise ValueError('Report manifest contains an invalid artifact name')
    from .report_driven.views import listing, bundle_stale
    listing(resolved['case'], directory, manifest['snapshot_id'], include_delivery=False)
    stale = bundle_stale(resolved['case'], manifest, state)
    saved = json.loads((resolved['case'].output / ('report-state-' + manifest['snapshot_id'] + '.json')).read_text())
    saved_revision = saved['meta'].get('content_revision', '')
    if manifest.get('content_revision', saved_revision) != saved_revision:
        raise ValueError('Report manifest content revision disagrees with its immutable snapshot')
    return {'status': 'derived_artifact_verified', 'bundle_id': manifest['bundle_id'],
            'snapshot_id': manifest['snapshot_id'], 'saved_content_revision': saved_revision,
            'current_content_revision': state['meta']['content_revision'], 'stale': stale,
            'current_question_count':len(question_ids),
            'current_questions_in_report':len(question_ids.intersection(manifest.get('question_ids',[]))),
            'current_questions_omitted':len(question_ids.difference(manifest.get('question_ids',[]))),
            'files_verified': len(manifest['files']), 'artifact_is_completion': False,
            'note': 'File existence/integrity and snapshot freshness do not certify the requested investigation.'}


def work_inventory(questions, missions, tests):
    """Counts of agent-declared state before clipping, never a feasibility verdict."""
    states = ('open', 'active', 'held', 'scoped_closed', 'blocked_internal', 'blocked_external', 'budget_deferred')
    important = [q for q in questions if q['priority'] in ('decision_critical', 'material')]
    return {
        'question_states': {state: sum(q['work_state'] == state for q in questions) for state in states},
        'important_question_states': {state: sum(q['work_state'] == state for q in important) for state in states},
        'priority_unassessed': sum(q['priority'] == 'unassessed' for q in questions),
        'planned_missions': sum(m['state'] in ('draft', 'candidate', 'ready') for m in missions),
        'running_missions': sum(m['state'] in ('queued', 'running') for m in missions),
        'unevaluated_results': sum(bool(t.get('result_ref')) and t['assessment_state'] in ('unassessed', 'stale') for t in tests),
        'meaning': 'Agent-declared states for this goal, including omitted items; not verified feasibility or a global blocked verdict.'}


def recent_observed_scope(resolved, state):
    """Execution metadata from this conversation; not a parallel judgment ledger."""
    # Only the lineage already verified by resolve_context; never arbitrary
    # descendants or another conversation in the same case.
    sessions=list(dict.fromkeys(resolved.get('session_lineage') or [resolved['conversation_id']]))
    with resolved['case'].connect() as db:
        ids = [row[0] for row in db.execute(
            "SELECT id FROM events WHERE session IN ("+','.join('?' for _ in sessions)+") AND kind='tool_result' ORDER BY rowid DESC LIMIT 64",
            sessions)]
    sources = {s['id']: s for s in state['sources']}
    facts = []
    for eid in ids:
        if eid not in sources:
            continue
        data = json.loads(sources[eid]['coverage'])
        if not any(k in data for k in ('coverage_complete', 'complete_file', 'complete_member', 'line_start', 'byte_start', 'listing_complete')):
            continue
        from .report_driven.measurement import scope_view
        shown = scope_view(data,('file_path','path','has_next_cursor','returned_entries_count'),text_limit=180)
        facts.append({'source_id': eid, 'observed': shown,
                      'omitted_fields': shown.pop('omitted_fields',[])})
    return sorted(facts, key=lambda f: not (f['observed'].get('has_next_cursor') or
        any(f['observed'].get(k) is False for k in ('coverage_complete','complete_file','complete_member','listing_complete'))))


def project_context(resolved, state, *, max_bytes=MAX_CONTEXT_BYTES):
    """Small navigation projection. Truncated strings and omitted records are explicit."""
    budget = min(MAX_CONTEXT_BYTES, int(max_bytes))
    if budget < 1400:
        raise ValueError('Projection budget is too small to preserve scope and omission metadata')
    questions, missions, tests, assessments = _owned_state(resolved, state)
    rank = {'decision_critical': 0, 'material': 1, 'unassessed': 2, 'contextual': 3}
    questions = sorted(questions, key=lambda q: (rank[q['priority']], q['id']))
    from .independent_review import review_state
    review = review_state(resolved, state)
    pending = completion_state(resolved, state, review=review)
    from .observation_scope import observed_scope
    inventory = observed_scope(resolved, max_targets=12, max_chars=4000)
    review_binding = {k: review.get(k) for k in ('status','review_id','pending_finding_ids',
        'dispositions','stale_review_ids','latest_final_review_id','final_review_current')}
    reports = _report_context(resolved, state, questions)
    revision = _hash({'case_id': resolved['case_id'], 'goal_id': resolved['goal_id'],
                      'scope_version': state['scope']['version'], 'content_revision': state['meta']['content_revision'],
                      'reports': reports, 'independent_review': review_binding})
    body = {'kind': 'investigation_state_projection',
            'notice': 'MODEL-MAINTAINED; NOT original evidence or validation. Retrieve originals by ID; … marks clipping.',
            'case_id': resolved['case_id'], 'goal_id': resolved['goal_id'],
            'conversation_id': resolved['conversation_id'], 'content_revision': revision,
            'requested_scope': _clip(resolved['goal'].goal, 360) if resolved['goal'] else
                               'No standing native goal. Only explicitly session-owned questions are restored.',
            'completion': {'pending_count': len(pending), 'examples': [_clip(x, 170) for x in pending[:2]],
                           'truth_approval': False},
            'work_inventory': work_inventory(questions, missions, tests),
            'answer_updates': {'tool': 'forsic_question',
                'required_update_fields': ['operation_id', 'question_id', 'expected_revision'],
                'grounded_answer_fields': ['answer', 'reason', 'judgment'],
                'review_response_field': 'review_responses',
                'questions': [{'question_id': q['id'], 'expected_revision': q['revision'],
                    'adopted_claim_count': len(q['claim_refs']),
                    'judgment_review_required': q.get('judgment_review_required', False)} for q in questions[:2]],
                'omitted_questions': max(0, len(questions)-2)},
            'work_decision': 'Compare changed evidence/answer and next-check value.',
            'active_questions': [], 'active_missions': [], 'unassessed_results': [], 'closed_index': [], 'observed_scope': [],
            'observed_targets': [],
            'scope_inventory': {'counts': inventory['counts'], 'omitted': inventory['omitted'],
                                'meaning': '전체 대화 계보의 관측 상태이며 분석 완료를 뜻하지 않습니다.'},
            'independent_review': {'status': review['status'],
                'pending_count': None if review['status'] in ('unavailable', 'unknown') else len(review['pending_finding_ids']),
                'pending_scope': 'material',
                'lookup': {'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox'}},
                'response_tool': 'forsic_review_response'},
            'observed_scope_note': 'Latest 64; partial first; window-local omissions. Metadata only. Search: exact term/target.',
            'reports': reports,
            'omitted': {'active_questions': 0, 'active_missions': 0, 'unassessed_results': 0, 'closed_index': 0,
                        'other_goal_or_unbound_questions': len(state['questions']) - len(questions)},
            'preserved_history': {'excluded_records':state['meta'].get('excluded_history_records',0), 'meaning':'Old definitions retained; note(get, include_history=true).'},
            'retrieval': 'forsic_reporting(state/source), forsic_note(get).'}
    entries = {'active_questions': [], 'closed_index': [], 'active_missions': [], 'unassessed_results': [],
               'observed_scope': recent_observed_scope(resolved, state),
               'observed_targets': inventory['targets']}
    from .report_driven.current_judgment import navigation_view
    for q in questions:
        item=navigation_view(state,q)
        item.update(priority=q['priority'],work_state=q['work_state'])
        if q['work_state']=='scoped_closed':
            item['reason']=_clip(q.get('closure_rationale'),180)
            entries['closed_index'].append(item)
        else:
            item['deferred_reason']=_clip(q.get('deferred_reason'),160)
            entries['active_questions'].append(item)
    priorities = {q['id']: rank[q['priority']] for q in questions}
    for m in sorted(missions, key=lambda item: (priorities[item['question_ref']['id']], item['id'])):
        if m['state'] in ('draft', 'candidate', 'ready', 'queued', 'running', 'blocked'):
            entries['active_missions'].append({'mission_id': m['id'], 'mission_version': m['version'],
                'question_id': m['question_ref']['id'], 'state': m['state'], 'tool': m.get('proposed_tool_name'),
                'target': _clip(m.get('exact_target_scope'), 160), 'why': _clip(m.get('why_it_matters'), 160),
                'arguments_preview': _clip(json.dumps(m.get('tool_arguments', {}), ensure_ascii=False), 200)})
        if m['state'] in ('running', 'queued', 'unassessed', 'failed'):
            entries['unassessed_results'].append({'mission_id': m['id'], 'question_id': m['question_ref']['id'],
                                                'state': m['state'], 'result_ids': m.get('result_ids', [])[:3],
                                                'omitted_results': max(0, len(m.get('result_ids', []))-3)})
    represented = {(item['question_id'], rid) for item in entries['unassessed_results'] for rid in item['result_ids']}
    for test in tests:
        result = test.get('result_ref')
        key = (test['question_ref']['id'], result['id']) if result else None
        if result and test['assessment_state'] in ('unassessed', 'stale') and key not in represented:
            entries['unassessed_results'].append({'test_id': test['id'], 'question_id': test['question_ref']['id'],
                'state': test['assessment_state'], 'execution_state': test['execution_state'], 'result_ids': [result['id']]})
            represented.add(key)
    for section, values in entries.items():
        body['omitted'][section] = len(values)
    # Restore concrete unanswered questions before historical observation detail.
    # This read-only view never chooses a disposition or writes a provisional answer.
    from .review_navigation import pending_items
    review_items = pending_items(review, max_bytes=min(1000, max(0, budget-len(_encoded(body)))))
    body['independent_review'].update(review_items)
    if len(_encoded(body)) > budget:
        body['independent_review'].pop('pending_items')
        body['independent_review'].pop('omitted_pending_items')
    # Current judgments and their retrieval handles precede historical observation detail.
    # A full navigation record can exceed the remaining budget even for one question;
    # retain its current answer/status and exact lookup rather than silently losing it.
    for index in range(max([len(values) for values in entries.values()] or [0])):
        for section, values in entries.items():
            if index >= len(values):
                continue
            body[section].append(values[index]); body['omitted'][section] -= 1
            if len(_encoded(body)) > budget:
                body[section].pop(); body['omitted'][section] += 1
                if section in ('active_questions', 'closed_index'):
                    keys = ('id', 'revision', 'definition_version', 'priority', 'work_state',
                            'assessment', 'review_required', 'answer_is_model_assessment',
                            'next_checks', 'remaining_gap_count', 'reason', 'deferred_reason',
                            'detail_pointer', 'details_call')
                    compact = {key: values[index][key] for key in keys if key in values[index]}
                    # Preserve the qualification of an answer before historical
                    # assessment details. An unqualified answer plus a lookup
                    # is not an equivalent compaction of a bounded judgment.
                    compact['judgments'] = [
                        {key: judgment[key] for key in (
                            'id', 'assertion_kind', 'inference_strength',
                            'reasoning_summary', 'assumptions', 'limitations',
                            'declaration_status', 'undeclared_analysis_fields')
                         if key in judgment}
                        for judgment in values[index].get('judgments', [])
                    ]
                    compact['omitted_fields'] = [key for key in values[index] if key not in compact]
                    body[section].append(compact); body['omitted'][section] -= 1
                    if len(_encoded(body)) > budget:
                        # At the smallest navigation budget, keep the exact
                        # lookup and explicitly qualify the partial answer.
                        compact.pop('judgments')
                        compact['omitted_fields'].append('judgments')
                        compact['answer_qualifications_omitted'] = True
                    if len(_encoded(body)) > budget:
                        body[section].pop(); body['omitted'][section] += 1
    context = _encoded(body).decode('utf-8')
    if len(context.encode('utf-8')) > budget:
        raise ValueError('Projection identity/omission metadata exceeds its budget')
    return context, revision, pending


def _projection(session_id, *, intake_root, native_db=None, max_bytes=MAX_CONTEXT_BYTES, goal_id='', delivery_max_chars=0):
    resolved = resolve_context(session_id, intake_root=intake_root, native_db=native_db)
    if resolved is None:
        return {'provider': PROVIDER, 'status': 'not_applicable'}
    if goal_id and resolved['goal_id'] != goal_id:
        return {'provider': PROVIDER, 'status': 'unavailable', 'reason': 'Native goal identity changed; no other goal context was substituted'}
    state = host.current(resolved['case'])
    context, revision, pending = project_context(resolved, state, max_bytes=max_bytes)
    if delivery_max_chars:
        from .report_driven.delivery import delivery_view
        base = json.loads(context)
        questions, _, _, _ = _owned_state(resolved, state)
        # Completion gets full answer/limit records in its existing judge budget;
        # compaction keeps the smaller navigation view. Count nested JSON escaping.
        budget = delivery_max_chars - 1100
        remaining = budget - len(json.dumps(context, ensure_ascii=False))
        while remaining >= 900:
            base['delivery'] = delivery_view(state, questions, max_chars=remaining)
            candidate = json.dumps(base, ensure_ascii=False, separators=(',', ':'))
            if len(json.dumps(candidate, ensure_ascii=False)) <= budget:
                context = candidate
                break
            remaining -= 400
        revision = _hash({'navigation': revision, 'delivery_snapshot': state['meta']['snapshot_id']})
    return {'provider': PROVIDER, 'status': 'ready', 'revision': revision, 'context': context,
            'pending': pending}


def goal_evaluation_context(session_id='', conversation_id='', goal_id='', goal_text='', contract=None,
                            phase='prepare', expected_revision=None, max_chars=12000, *, intake_root, native_db=None, **_):
    """Native hook callback: prepare one snapshot, validate that exact revision at DONE."""
    try:
        result = _projection(session_id, intake_root=intake_root, native_db=native_db,
                             max_bytes=min(MAX_CONTEXT_BYTES, int(max_chars)), goal_id=goal_id,
                             delivery_max_chars=int(max_chars))
        pending = result.pop('pending', [])
        if result['status'] == 'ready':
            if phase == 'validate' and (expected_revision or {}).get(PROVIDER) != result['revision']:
                result.update(status='stale', reason='Investigation content or goal scope changed while the judge was running')
            elif pending:
                result.update(status='incomplete', reason=_clip('; '.join(pending[:3]), 700))
        return result
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        return {'provider': PROVIDER, 'status': 'unavailable', 'reason': _clip(str(exc), 300)}


def post_context_compaction(session_id='', max_bytes=6000, *, intake_root, native_db=None, include_runtime_clock=True, **_):
    """Native compaction hook. No before-turn hook replay or state mutation."""
    try:
        from .runtime_clock import current_context
        clock = current_context() if include_runtime_clock else None
        # Native compaction retains only provider/context, so the clock belongs
        # inside that context. Reserve its bytes without clipping current state.
        clock_bytes = len(_encoded({'runtime_clock': clock})) if clock else 0
        result = _projection(session_id, intake_root=intake_root, native_db=native_db,
                             max_bytes=min(MAX_CONTEXT_BYTES, int(max_bytes) - 500) - clock_bytes)
        result.pop('pending', None)
        if result['status'] == 'ready' and clock:
            body = json.loads(result['context'])
            body['runtime_clock'] = clock
            result['context'] = _encoded(body).decode('utf-8')
        return result
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        return {'provider': PROVIDER, 'status': 'unavailable', 'reason': _clip(str(exc), 300)}
