"""Read-only reconciliation of public progress and the existing question journal.

This is navigation, not a second finding store or a semantic classifier. Public
text is never copied into a judgment; the analyst writes through forsic_question.
"""
from contextlib import closing
import sqlite3


def answer_status(note):
    from .question_bootstrap import PROVISIONAL_ANSWER
    if note.get('answer') == PROVISIONAL_ANSWER:
        return 'not_saved'
    if note.get('investigation', {}).get('judgment_review_required'):
        return 'needs_recheck'
    return 'recorded'


def checkpoint(resolved, *, db=None):
    """Compare native public/tool boundaries with same-goal committed notes.

    A public explanation may be only progress or a plan. Pending means that its
    judgment has not been reconciled, never that a new finding is proven. Source
    text, model reasoning and evidence counts cannot choose an answer here.
    """
    if db is None:
        try:
            with closing(sqlite3.connect(resolved['case'].db.as_uri() + '?mode=ro', uri=True)) as connection:
                connection.execute('PRAGMA query_only=ON')
                connection.execute('BEGIN')
                return checkpoint(resolved, db=connection)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            return {'status': 'unavailable', 'pending_briefings': None,
                    'latest_briefing_at': None, 'latest_saved_at': None}
    goal = resolved.get('goal')
    lineage = resolved.get('session_lineage') or []
    if not goal or not lineage:
        return {'status': 'current', 'pending_briefings': 0,
                'latest_briefing_at': None, 'latest_saved_at': None}
    marks = ','.join('?' for _ in lineage)
    # Select latest revisions before checking ownership: old ownership must not
    # reappear after a correction or an authorized handoff.
    from .question_bootstrap import PROVISIONAL_ANSWER
    owned = """WITH owned AS (SELECT data FROM events
        WHERE rowid IN (SELECT max(rowid) FROM events WHERE kind='note'
            GROUP BY json_extract(data,'$.note_id'))
        AND json_extract(data,'$.goal_id')=?
        AND json_extract(data,'$.session_id') IN (""" + marks + ')) '
    params = [resolved['goal_id'], *lineage]
    count, latest_saved = db.execute(owned + """SELECT count(*), max(CASE WHEN
        json_extract(data,'$.answer') != ? THEN COALESCE(json_extract(data,'$.judgment_updated_at'),
            json_extract(data,'$.updated_at')) END) FROM owned""",
        [*params, PROVISIONAL_ANSWER]).fetchone()
    # Return only bounded navigation metadata, never load all historical note
    # text or a ledger-sized report projection on each native evidence return.
    notes = list(db.execute(owned + """SELECT json_extract(data,'$.note_id'),
        json_extract(data,'$.revision') FROM owned
        ORDER BY json_extract(data,'$.updated_at') DESC LIMIT 3""", params))
    # API receipt + actual native tool start proves that a public tool-round
    # boundary was reached. Deduplicate request IDs; retries are not discoveries.
    rows = db.execute("""SELECT max(e.time), count(DISTINCT json_extract(e.data,'$.api_request_id'))
        FROM events e WHERE e.kind='post_api_request'
        AND e.session IN (""" + marks + """) AND e.time>?
        AND json_extract(e.data,'$.assistant_content_chars')>0
        AND EXISTS (SELECT 1 FROM events t WHERE t.kind='tool_start' AND t.session=e.session
            AND json_extract(t.data,'$.native_call.api_request_id')=json_extract(e.data,'$.api_request_id'))""",
        [*lineage, max(float(getattr(goal, 'created_at', 0) or 0), latest_saved or 0)]).fetchone()
    latest_briefing, pending = rows
    result = {'status': 'pending' if pending else 'current', 'pending_briefings': pending,
            'latest_briefing_at': latest_briefing, 'latest_saved_at': latest_saved,
            'questions': [{'question_id': 'Q-' + note_id, 'expected_revision': revision,
                'read_call': {'tool': 'forsic_note', 'arguments': {'action': 'get', 'note_id': note_id}}}
                for note_id, revision in notes], 'omitted_questions': max(0, count-3)}
    if pending:
        from .notes import EVIDENCE_TOOLS
        tools = sorted(EVIDENCE_TOOLS)
        retained = db.execute("""SELECT id, json_extract(data,'$.tool') FROM events
            WHERE kind='tool_result' AND session IN (""" + marks + ") AND time>? "
            "AND json_extract(data,'$.tool') IN (" + ','.join('?' for _ in tools) + ") "
            "ORDER BY rowid DESC LIMIT 2",
            [*lineage, max(float(getattr(goal, 'created_at', 0) or 0), latest_saved or 0), *tools]).fetchall()
        result['retained_results'] = [{'source_id': source, 'tool': tool,
            'read_call': {'tool': 'forsic_reporting', 'arguments': {'action': 'source', 'source_id': source}}}
            for source, tool in retained]
    return result


def model_checkpoint(resolved):
    value = checkpoint(resolved)
    if value['status'] != 'pending':
        return value
    return {**value, 'meaning': 'Public investigation explanation since the last saved answer; not evidence or a confirmed finding.',
        'next_step': 'Before unrelated collection, reconcile material findings, changed scope or next checks through the SAME forsic_question. Use the current revision below, retained_results and an explicit provisional interpretation; do not wait for certainty. Confirm saved_judgment in the returned receipt. Progress-only commentary needs no duplicate judgment: report/tool errors and draft status belong to existing work_state/reason and report receipts, never the incident answer or analytical next_checks. Do not repeat collection to reconstruct known results.',
        'save_tool': 'forsic_question',
        'save_fields': ['operation_id', 'question_id', 'expected_revision', 'answer', 'judgment', 'next_checks', 'reason'],
        'optional_explanation': 'reader_explanation shares the saved interpretation with the UI and report.'}
