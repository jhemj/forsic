"""Read-only work typing for an authenticated report handoff.

An empty automatically seeded follow-up is an editorial task, not an incident
proposition. Never manufacture source evidence to close it. Real investigation
questions and their work keep the existing gates and ownership.
"""
import json


def classify(resolved, state):
    from .investigation_context import report_followup_selection
    from .question_bootstrap import operation_id
    selected = report_followup_selection(resolved)
    if not selected:
        return None
    op = operation_id(resolved['case_id'], resolved['goal_id'], resolved['conversation_id'])
    with resolved['case'].connect() as db:
        row = db.execute("SELECT data FROM events WHERE kind='mutation_receipt' "
                         "AND json_extract(data,'$.operation_id')=? ORDER BY rowid DESC LIMIT 1", (op,)).fetchone()
    receipt = json.loads(row[0]).get('response', {}).get('question', {}) if row else {}
    trackers = []
    for q in state['questions']:
        if (q['id'] != receipt.get('question_id') or q.get('definition_version') != receipt.get('definition_version')
                or q.get('goal_id') != resolved['goal_id'] or q.get('session_id') not in resolved['session_lineage']):
            continue
        # Any adopted analytical basis or declared investigation work makes this
        # a real question. Its priority/answer text is never used as a heuristic.
        if any(q.get(k) for k in ('claim_refs', 'evidence_ids', 'citation_refs', 'next_checks')):
            continue
        if any(r.get('question_ref', {}).get('id') == q['id']
               for kind in ('claims', 'gaps', 'missions', 'tests', 'hypotheses') for r in state.get(kind, [])):
            continue
        trackers.append(q['id'])
    return {'kind': 'report_followup_task', 'source_goal_id': selected['source_goal_id'],
            'question_ids': selected['question_ids'], 'legacy_tracker_ids': trackers,
            'meaning': 'Editorial work only. Existing investigation answers and gaps stay read-only; '
                       'new substantive investigation requires its own explicit question.'}


def selected_version(resolved, state, task, review):
    """A selected current report ends this administrative task, never the goal.

The native goal evaluator still checks delivery, requested UI/print validation
and any unfulfilled user scope. Availability is not a fabricated passed review.
"""
    from .report_driven import narrative
    from .report_driven.report_reference import resolve_report
    authored = [r for r in state.get('report_narratives', []) if r.get('goal_id') == resolved['goal_id']
                and r.get('session_id') in resolved['session_lineage']]
    progress = narrative.status({**state, 'report_narratives': authored}, task['question_ids'])
    if not progress['ready']:
        return {'stage': 'author_current_version', 'pending': 'Current report fields and document review remain incomplete.'}
    if (not review.get('final_review_current') or review.get('requires_followup')
            or review.get('pending_finding_ids')):
        return {'stage': 'review_current_version', 'pending': 'Current independent review or finding disposition remains incomplete.'}
    scope = dict(session_id=resolved['conversation_id'], goal_id=resolved['goal_id'],
                 source_goal_id=task['source_goal_id'], source_question_ids=task['question_ids'])
    with resolved['case'].connect() as db:
        rows = list(db.execute("SELECT id,data FROM events WHERE kind='report_finalized' ORDER BY rowid DESC LIMIT 32"))
    for row in rows:
        record = json.loads(row[1])
        if record.get('case_id') != resolved['case_id'] or record.get('selection') != 'final_delivery_copy':
            continue
        try:
            manifest, _, _ = resolve_report(resolved['case'], state, record['bundle_id'],
                                           snapshot_id=record['snapshot_id'], report_scope=scope)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        # Require an explicit current-goal scope, not legacy scope inference.
        if manifest.get('report_scope') != scope or sorted(manifest['question_ids']) != sorted(task['question_ids']):
            continue
        selected_review = record.get('independent_review') or {}
        if (not selected_review.get('final_review_current') or
                selected_review.get('latest_final_review_id') != review.get('latest_final_review_id')):
            continue
        return {'stage': 'deliver_selected_version', 'pending': None, 'bundle_id': record['bundle_id'],
                'selection_receipt': row[0], 'layout': manifest.get('layout_review', {'status': 'unverified'}),
                'meaning': 'No forensic scope-closure call for the empty report tracker. Deliver the selected '
                           'version; the native goal evaluator must still assess the user objective and disclosed validation limits.'}
    return {'stage': 'select_current_version', 'pending': 'No intact current-goal report selection for this reviewed draft.'}
