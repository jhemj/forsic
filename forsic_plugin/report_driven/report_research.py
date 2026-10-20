"""Read-only report review and exact native-work reuse. No execution or new ledger.

The analyst chooses meaning, value, the next discriminator and scope closure. This
module only exposes existing records and fences replay of identical retained work.
"""
from __future__ import annotations

import hashlib
import json


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _refs(values):
    # Order of a selected reference set is not a new investigative condition.
    return sorted({json.dumps(r, sort_keys=True, separators=(',', ':')) for r in values})


def work_identity(question, mission):
    """Revision/operation/mission IDs and editorial wording never reset work.

    Definition identity, versioned selected inputs and exact native arguments do.
    Native normalization removes only existing transport/context-only fields.
    """
    from .investigation_state import normalized_tool_arguments
    return _hash({
        'question_definition': mission.get('question_definition_version') or question.get('definition_version'),
        'scope': mission.get('exact_target_scope', question.get('scope')),
        'proposition': mission.get('target_proposition', question.get('target_proposition')),
        'tool': mission.get('proposed_tool_name'),
        'arguments': normalized_tool_arguments(mission.get('tool_arguments') or {}),
        'inputs': _refs(mission.get('input_refs', [])),
        'counterevidence': _refs(mission.get('preserved_counterevidence_refs', [])),
        'retained_results': _refs(mission.get('reuse_result_refs', [])),
        'discriminator': {k: mission.get(k) for k in
                          ('support_rule', 'refute_rule', 'inconclusive_rule', 'required_view')},
    })


def state_call(state, pointer):
    return {'tool': 'forsic_reporting', 'arguments': {'action': 'state',
            'snapshot_id': state['meta']['snapshot_id'], 'pointer': pointer}}


def source_call(source):
    return {'tool': 'forsic_reporting', 'arguments': {'action': 'source',
            'source_id': source['id'], 'source_version': source['version']}}


def duplicate_work(state, question, mission):
    """Return retained exact work, including running/unknown completion, or None.

    State comes from the host's paired native receipts. An unstarted draft is
    reusable planning, never evidence that a tool ran. Partial/failed/no-match
    records do not certify a source-wide negative or authorize automatic replay.
    """
    identity = work_identity(question, mission)
    questions = {q['id']: q for q in state.get('questions', [])}
    sources = {s['id']: s for s in state.get('sources', [])}
    matches = []
    for index, old in enumerate(state.get('missions', [])):
        if old.get('id') == mission.get('id'):
            continue
        owner = questions.get(old.get('question_ref', {}).get('id'))
        if not owner or work_identity(owner, old) != identity:
            continue
        # The scoped answer writer validates ownership; this helper never adopts
        # other questions' conclusions or silently transfers mission ownership.
        result_ids = list(dict.fromkeys([*old.get('result_ids', []),
                            *(r['id'] for r in old.get('reuse_result_refs', []))]))
        starts = old.get('execution_start_ids', [])
        state_name = old.get('state', 'unknown')
        receipt_state = ('running_or_unknown' if (starts or state_name in ('completed', 'unassessed')) and not result_ids or state_name in ('running', 'queued')
                         else 'partial' if state_name == 'partial'
                         else 'failed' if state_name == 'failed'
                         else 'returned' if result_ids
                         else 'unstarted_plan')
        entry = {'work_identity': identity, 'mission_id': old['id'],
                 'mission_version': old['version'], 'receipt_state': receipt_state,
                 'execution_start_ids': list(starts), 'result_ids': list(result_ids),
                 'read_call': state_call(state, '/missions/' + str(index)),
                 'source_calls': [source_call(sources[sid]) for sid in result_ids if sid in sources],
                 'reuse': 'inspect_existing_plan' if receipt_state == 'unstarted_plan' else
                         'query_existing_completion' if receipt_state == 'running_or_unknown' else 'inspect_retained_result',
                 'whole_source_success': False, 'automatic_replay_authorized': False}
        if owner.get('session_id') and owner.get('session_id') == question.get('session_id'):
            from ..execution_contract import lookup_call
            entry['execution_calls'] = [lookup_call(start) for start in starts[-2:]]
        matches.append(entry)
    if not matches:
        return None
    # Prefer actual receipts over an earlier unstarted duplicate plan.
    return next((m for m in matches if m['receipt_state'] != 'unstarted_plan'), matches[0])


class DuplicateWorkError(ValueError):
    def __init__(self, retained):
        self.details = {'error_code': 'identical_native_work_exists', 'retained_work': retained,
                        'execution_authorized': False,
                        'next_step': 'Inspect retained work. Query unknown completion; do not replay it. A retained failure can use only its explicitly eligible original native retry contract; a new mission is not a retry. Changed evidence, scope, exact inputs or discriminator can justify a different plan.'}
        super().__init__('Identical native work already exists; reuse its plan or retained receipts. '
                         'A new operation_id, mission_id or unrelated case revision does not change the work.')


def validate_no_duplicate_work(state, question, mission):
    """Admission hook for a new expanded mission, before any ledger mutation.

    Ordinary unbound reads are untouched. A deliberately classified transient
    failure still uses its existing native retry contract on the original mission.
    """
    retained = duplicate_work(state, question, mission)
    if retained and (retained['receipt_state'] in ('returned', 'running_or_unknown') or
                     retained['receipt_state'] in ('failed', 'partial') and
                     (retained['execution_start_ids'] or retained['result_ids'])):
        # Retained failure/partial output is still the same attempted work. A new
        # mission ID must not bypass its original native retry/completion guard.
        raise DuplicateWorkError(retained)


def report_research_review(state, host_args=None, *, max_items=4, paused=False):
    """Bounded navigation for the analyst; never tools, notes, missions or resume.

    Missing declarations are editorial review, not evidence gaps. Existing gaps,
    analyst priority and dispositions are the only action-selection inputs.
    """
    from .report_slots import project_report_slots
    args = host_args or {}
    paused = bool(paused or args.get('_report_paused') or
                  state.get('status', {}).get('execution') in ('stopped', 'user_stopped', 'budget_exhausted'))
    limit = max(0, min(int(max_items), 6))
    projection = project_report_slots(state)
    goal, session = args.get('_goal_id'), args.get('_session_id')
    bound = bool(goal and session)
    questions = state.get('questions', [])
    indexed = {q['id']: (i, q) for i, q in enumerate(questions)}
    def owned(q):
        return bound and q.get('goal_id') == goal and q.get('session_id') == session
    requested = args.get('question_id')
    requested_ids = args.get('question_ids')
    subset = None
    if requested_ids is not None:
        from .host import selected_question_records
        _, subset = selected_question_records(state, requested_ids)
        scope = {key: value for key, value in (('goal_id', goal), ('session_id', session)) if value}
        if any(q['id'] in subset and any(q.get(key, '') != value for key, value in scope.items())
               for q in questions):
            raise ValueError('Review only questions from the current conversation and goal')
    # A singular question remains an additional narrowing filter. An explicit
    # report subset never replaces ownership or widens a singular selection.
    eligible = [q for q in questions if owned(q) and (not requested or q['id'] == requested)
                and (subset is None or q['id'] in subset)]
    selected = {q['id'] for q in eligible}
    slots = projection.get('slots', [])
    if isinstance(slots, dict):
        slots = [dict(value, slot=key) for key, value in slots.items()]
    slot_status = []
    for slot in slots:
        entries = [entry for entry in slot.get('entries', [])
                   if entry.get('question_id') in selected]
        slot_status.append({'slot': slot['slot'], 'declared_count': len(entries),
                            'missing_declaration': not bool(entries),
                            'needs_recheck_count': sum(bool(e.get('needs_recheck')) for e in entries)})
    rechecks = []
    for q in eligible:
        names = [slot['slot'] for slot in slots if any(e.get('question_id') == q['id'] and e.get('needs_recheck') for e in slot.get('entries', []))]
        if names:
            rechecks.append({'question_id': q['id'], 'slots': names,
                'question_call': state_call(state, '/questions/' + str(indexed[q['id']][0]))})
    result = {'schema': 'forsic-report-research-1', 'snapshot_id': state['meta']['snapshot_id'],
              'execution_authorized': False, 'paused': bool(paused),
              'meaning': 'Read-only review inventory. Missing slots are undeclared presentation, not missing evidence. The analyst chooses material uncertainty and valuable permitted checks; unknown answers and scope closure remain valid.',
              'slot_status': slot_status, 'declaration_rechecks': rechecks[:2],
              'omitted_declaration_recheck_count': max(0, len(rechecks) - 2), 'items': [],
              'conversation_scope': {'binding_available': bound, 'editable_question_count': len(eligible)},
              'limits': {'item_limit': limit, 'native_execution_budgets_unchanged': True,
                         'automatic_retries': 0, 'resume_authorized': False},
              'lookup': {'questions': state_call(state, '/questions'), 'gaps': state_call(state, '/gaps'),
                         'missions': state_call(state, '/missions')},
              'next_step': 'Read the selected question and actual gap. Revise that owned question with forsic_question and current expected_revision; investigate through existing native tools when permitted, then revise and render the new snapshot. No slot-completion or reviewer gate.'}
    candidates = [(i, g) for i, g in enumerate(state.get('gaps', []))
                  if g.get('question_ref', {}).get('id') in selected
                  and g.get('disposition') not in ('resolved', 'not_applicable')
                  and g.get('kind') not in ('editorial_only', 'publication_issue')]
    rank = {'decision_critical': 0, 'material': 1, 'unassessed': 2, 'contextual': 3}
    candidates.sort(key=lambda pair: rank.get(pair[1].get('impact'), 2))
    for gi, gap in candidates[:limit]:
        qi, question = indexed[gap['question_ref']['id']]
        item = {'question_id': question['id'], 'expected_revision': question.get('revision'),
                'gap_id': gap['id'], 'impact': gap.get('impact'),
                'reason_preview': gap.get('reason', '')[:160],
                'editable_in_current_conversation': True,
                'question_call': state_call(state, '/questions/' + str(qi)),
                'gap_call': state_call(state, '/gaps/' + str(gi)),
                'gap_lookup': {'tool': 'forsic_reporting', 'arguments': {'action': 'gaps', 'question_id': question['id']}},
                'question_writer': {'tool': 'forsic_question', 'required_current_fields': ['operation_id', 'question_id', 'expected_revision']},
                'feasible_next_action': gap.get('feasible_next_action'),
                'preconditions_declared': bool(gap.get('missing_preconditions')),
                'source_calls': []}
        sources = {s['id']: s for s in state.get('sources', [])}
        item['source_calls'] = [source_call(r) for r in gap.get('basis_refs', [])
                                if r.get('kind') == 'source' and r['id'] in sources][:2]
        for mi, mission in enumerate(state.get('missions', [])):
            if mission.get('gap_ref', {}).get('id') != gap['id']:
                continue
            item['mission_call'] = state_call(state, '/missions/' + str(mi))
            if mission.get('result_ids') or mission.get('execution_start_ids'):
                from ..execution_contract import lookup_call
                item['retained_work'] = {'state': mission['state'],
                    'execution_calls': [lookup_call(start) for start in mission.get('execution_start_ids', [])[-2:]],
                    'result_ids': mission.get('result_ids', []), 'replay_authorized': False,
                    'next_step': 'Read retained result or query existing completion; no automatic replay.'}
            elif (not paused and question.get('work_state') in ('open', 'active')
                  and not gap.get('missing_preconditions') and mission.get('state') in ('draft', 'candidate', 'ready')):
                from .investigation_state import image_read_reference_call
                call = image_read_reference_call(mission)
                if call:
                    item['candidate_native_call'] = call
                elif mission.get('proposed_tool_name') and mission.get('tool_arguments'):
                    item['candidate_native_call'] = {'tool': mission['proposed_tool_name'],
                        'arguments': {**mission['tool_arguments'], 'mission_id': mission['id'], 'mission_version': mission['version']}}
            break
        trial = {**result, 'items': result['items'] + [item]}
        if len(json.dumps(trial, ensure_ascii=False).encode()) > 5600:
            break
        result['items'].append(item)
    result['omitted_gap_count'] = len(candidates) - len(result['items'])
    return result
