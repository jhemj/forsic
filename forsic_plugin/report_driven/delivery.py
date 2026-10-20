"""One lossless answer/scope view for reports and the native completion judge.

These are retained analyst judgments, not newly verified conclusions. Bounds omit
whole records and disclose omissions; they never shorten a number's grouping,
qualifier, source attribution, or a scope limitation into a different assertion.
"""
from copy import deepcopy
import json

from .current_judgment import current_judgment


def answer_record(state, question):
    judgment = current_judgment(state, question)
    return {
        'question_id': question['id'], 'revision': question['revision'],
        'question': question['question'], 'answer': judgment['answer'],
        'scope': judgment['scope'], 'priority': question['priority'],
        'work_state': question['work_state'], 'review_required': judgment['review_required'],
        'closure_rationale': question.get('closure_rationale'),
        'remaining_gaps': judgment['remaining_gaps'],
        'judgments': judgment['judgments'], 'alternatives': judgment['alternatives'],
        'reopen_conditions': judgment['reopen_conditions'],
        'review_responses': deepcopy(question.get('review_responses', [])),
    }


def delivery_view(state, questions=None, *, max_chars=7000, scope_inventory=None):
    """Bounded exact records; no new model call, adoption, count or inference."""
    questions = state['questions'] if questions is None else questions
    refs = {r['id'] for q in questions for c in current_judgment(state, q)['judgments']
            for role in ('source_refs', 'counterevidence_refs') for r in c[role]
            if r['kind'] == 'observation'}
    observations = [o for o in state['observations'] if o['id'] in refs]
    body = {
        'schema': 'forsic-delivery-1', 'snapshot_id': state['meta']['snapshot_id'],
        'meaning': '저장된 답과 한계를 그대로 제시한 대조 자료입니다. 수치의 대상·집계 구성과 미검토 범위를 함께 비교하세요. 의미 정확성 승인이 아닙니다.',
        'answers': [], 'evidence_excerpts': [], 'scope_inventory': None,
        'omitted': {'answers': len(questions), 'evidence_excerpts': len(observations), 'scope_inventory': 0},
        'retrieval': {'tool': 'forsic_reporting', 'arguments': {'action': 'state',
            'snapshot_id': state['meta']['snapshot_id']}},
    }
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(',', ':')))
    if max_chars < size(body):
        raise ValueError('Delivery budget cannot preserve identity and omission metadata')
    # Keep current answers/limits before optional source excerpts; never take
    # only the first N characters of a large answer or multiline source field.
    rank = {'decision_critical': 0, 'material': 1, 'unassessed': 2, 'contextual': 3}
    for question in sorted(questions, key=lambda q: (rank[q['priority']], q['id'])):
        item = answer_record(state, question)
        body['answers'].append(item); body['omitted']['answers'] -= 1
        if size(body) > max_chars:
            body['answers'].pop(); body['omitted']['answers'] += 1
    if scope_inventory is not None:
        body['scope_inventory'] = deepcopy(scope_inventory)
        if size(body) > max_chars:
            body['scope_inventory'] = None; body['omitted']['scope_inventory'] = 1
    for observation in observations:
        item = {k: deepcopy(observation[k]) for k in ('id', 'source_ref', 'field_pointer',
                'literal', 'byte_start', 'byte_end', 'acquisition_context') if k in observation}
        body['evidence_excerpts'].append(item); body['omitted']['evidence_excerpts'] -= 1
        if size(body) > max_chars:
            body['evidence_excerpts'].pop(); body['omitted']['evidence_excerpts'] += 1
    return body


def report_scope_inventory(case, args):
    """Use trusted native binding, never a model-selected session or directory."""
    options = args.get('_independent_review_options') or {}
    if not options.get('session_id'):
        return None
    from ..investigation_context import resolve_context
    from ..observation_scope import observed_scope
    resolved = resolve_context(options['session_id'], intake_root=options['intake_root'],
                               native_db=options.get('native_db'))
    if (not resolved or resolved['case'].manifest != case.manifest
            or resolved['conversation_id'] != args.get('_session_id')):
        raise ValueError('Report observation scope does not match its native conversation')
    return observed_scope(resolved, max_targets=16, max_chars=5000)
