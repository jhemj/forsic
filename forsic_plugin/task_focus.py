"""Read-only task cards derived from current note judgments and exact check records."""
import json
import sqlite3
from contextlib import closing

from .report_driven.current_judgment import navigation_view, current_judgment


def check_records(state, question):
    """A successful return is not an assessment or a resolved question."""
    records = []
    for mission in state['missions']:
        if mission['question_ref']['id'] != question['id']:
            continue
        test = next((t for t in state['tests'] if t['id'] == 'T-'+mission['id']), None)
        result = (test or {}).get('result_ref')
        assessment = next((a for a in reversed(state['assessments']) if test and
            a['test_ref'] == {'kind':'test','id':test['id'],'version':test['version']} and
            a.get('result_ref') == result), None)
        applicable = mission.get('question_definition_version') == question['definition_version']
        complete = bool(applicable and mission['state'] in ('completed','partial') and
            not question.get('judgment_review_required') and
            test and test['execution_state'] == 'returned' and test['assessment_state'] == 'assessed' and
            result and assessment and assessment['outcome'] != 'unavailable')
        records.append({'mission_id':mission['id'], 'mission_version':mission['version'],
            'state': 'assessed' if complete else 'recheck' if not applicable or question.get('judgment_review_required')
                     else (test or {}).get('execution_state', mission['state']),
            'complete':complete, 'tool':mission.get('proposed_tool_name',''),
            'why':mission.get('why_it_matters',''), 'result_ref':result,
            'assessment_ref': {'kind':'assessment','id':assessment['id'],'version':assessment['version']} if assessment else None,
            'outcome':assessment['outcome'] if assessment else None})
    return records


def project_tasks(state, questions, *, max_bytes=8000, include_judgment=True):
    """Omit whole cards/fields; UI check progress can use its separate canonical answer."""
    body = {'status':'ready', 'role':'analyst_judgment_not_source_evidence', 'tasks':[],
            'omitted_tasks':len(questions), 'details_call':{'tool':'forsic_reporting','arguments':{'action':'state'}}}
    def size():
        return len(json.dumps(body,ensure_ascii=False,separators=(',',':')).encode())
    for question in questions:
        card = navigation_view(state, question)
        if include_judgment:
            # A context card carries the whole qualified answer or is omitted.
            # Navigation placeholders must not become UI answers.
            full = current_judgment(state, question)
            card['answer_is_model_assessment'] = full['answer']
            card['scope'] = full['scope']
            card['judgments'] = [{**judgment,
                'counterevidence_ref_count':len(judgment['counterevidence_refs']),
                'source_ref_count':len(judgment['source_refs'])} for judgment in full['judgments']]
            card['question'] = question['question']
        else:
            # The unified UI already carries the exact answer, evidence and
            # next checks. Duplicating them can consume the progress budget
            # and hide an otherwise small check card after a meaningful save.
            card = {key: card[key] for key in (
                'id', 'revision', 'definition_version', 'review_required',
                'remaining_gap_count', 'remaining_gap_examples', 'details_call', 'omitted_details')}
        card['work_state'] = question['work_state']
        card['checks'] = []
        checks = check_records(state, question)
        card['check_counts'] = {'completed':sum(c['complete'] for c in checks),
                                'pending_or_recheck':sum(not c['complete'] for c in checks)}
        card['omitted_checks'] = len(checks)
        body['tasks'].append(card); body['omitted_tasks'] -= 1
        if size() > max_bytes:
            body['tasks'].pop(); body['omitted_tasks'] += 1
            continue
        # Keep outstanding checks visible before completed history.
        for check in sorted(checks,key=lambda c:c['complete']):
            card['checks'].append(check); card['omitted_checks'] -= 1
            if size() > max_bytes:
                card['checks'].pop(); card['omitted_checks'] += 1
    return body


def _selected_task_state(case):
    """Replay analytical records and their exact dependencies, not report prose.

    Note history is retained for definition changes and legacy reference replay.
    Evidence bodies enter only by a retained reference or a paired mission
    receipt. This is navigation state, never a full report/write snapshot.
    """
    from .report_driven.host import current
    from .notes import EVIDENCE_TOOLS

    events = {}
    read_bytes = 0
    max_bytes = 4 * 1024 * 1024
    max_records = 20000
    source_ids = set()
    mission_ids = set()

    def references(value):
        if isinstance(value, dict):
            if value.get('kind') == 'source' and isinstance(value.get('id'), str):
                source_ids.add(value['id'])
            for key, item in value.items():
                if key in ('evidence_ids', 'result_ids', 'execution_start_ids') and isinstance(item, list):
                    source_ids.update(x for x in item if isinstance(x, str))
                elif key == 'evidence_id' and isinstance(item, str):
                    source_ids.add(item)
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)

    with closing(sqlite3.connect(case.db.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')

        def read(where, parameters=()):
            nonlocal read_bytes
            # Oversized bodies never cross the SQL/Python boundary. The shared
            # budget includes all selected records, not a separate cap per pass.
            rows = db.execute('SELECT rowid,id,time,kind,session, '
                'length(cast(data AS BLOB)) AS bytes, '
                'CASE WHEN length(cast(data AS BLOB))<=? THEN data END AS data '
                'FROM events WHERE ' + where + ' ORDER BY rowid',
                (max_bytes - read_bytes, *parameters))
            for row in rows:
                if row['id'] in events:
                    continue
                read_bytes += row['bytes']
                if row['data'] is None or read_bytes > max_bytes or len(events) >= max_records:
                    raise ValueError('Selected task dependencies exceed the bounded read budget')
                event = {key: row[key] for key in ('rowid', 'id', 'time', 'kind', 'session')}
                event['data'] = json.loads(row['data'])
                events[event['id']] = event

        read("kind IN ('note','report_change')")
        for event in events.values():
            references(event['data'])
            if event['kind'] == 'report_change':
                mission_ids.update(m['id'] for m in event['data'].get('records', {}).get('missions', []))
        # Preserve actual start/result pairing, including running and failed
        # checks. Absence of an assessment never becomes a completed task.
        read("kind IN ('tool_start','tool_result') AND json_valid(data) AND "
             "json_extract(data,'$.mission_context.mission_id') IN (SELECT value FROM json_each(?))",
             (json.dumps(sorted(mission_ids)),))
        read("kind IN ('tool_start','tool_result') AND id IN (SELECT value FROM json_each(?))",
             (json.dumps(sorted(source_ids)),))
        # This existing scope flag depends on presence, even when the external
        # source is not selected by a question. Preserve its canonical meaning.
        read("rowid=(SELECT min(rowid) FROM events WHERE kind='tool_result' AND json_valid(data) "
             "AND json_extract(data,'$.source_kind')='external_intelligence' "
             "AND json_extract(data,'$.tool') IN (SELECT value FROM json_each(?)))",
             (json.dumps(sorted(EVIDENCE_TOOLS)),))

    # Reuse the canonical projector after releasing the read lock; citation and
    # execution revalidation may open their own short-lived connections.
    state = current(case, events=sorted(events.values(), key=lambda e: e['rowid']))
    state['_task_focus_only'] = True
    state['_task_focus_read'] = {'records': len(events), 'bytes': read_bytes, 'max_bytes': max_bytes}
    return state


def retained_task_state(case):
    """Reuse a full bounded snapshot when available, otherwise select task inputs."""
    from .investigation_view import _report_current_basis
    try:
        state = _report_current_basis(case)
        return state if state is not None else _selected_task_state(case)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error):
        return None
