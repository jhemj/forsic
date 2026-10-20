"""Read-only task cards derived from current note judgments and exact check records."""
import json
import sqlite3

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


def project_tasks(state, questions, *, max_bytes=8000):
    """Omit whole cards/fields with counts; never shorten a qualified answer."""
    body = {'status':'ready', 'role':'analyst_judgment_not_source_evidence', 'tasks':[],
            'omitted_tasks':len(questions), 'details_call':{'tool':'forsic_reporting','arguments':{'action':'state'}}}
    def size():
        return len(json.dumps(body,ensure_ascii=False,separators=(',',':')).encode())
    for question in questions:
        card = navigation_view(state, question)
        # A card either carries the whole qualified answer or is omitted with
        # an exact lookup. Navigation placeholders must not become UI answers.
        full = current_judgment(state, question)
        card['answer_is_model_assessment'] = full['answer']
        card['scope'] = full['scope']
        card['judgments'] = [{**judgment,
            'counterevidence_ref_count':len(judgment['counterevidence_refs']),
            'source_ref_count':len(judgment['source_refs'])} for judgment in full['judgments']]
        card['question'] = question['question']
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


def retained_task_state(case):
    """Reuse the existing bounded ledger reader; failed reads mean unknown."""
    from .investigation_view import _report_current_basis
    try:
        return _report_current_basis(case)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error):
        return None
