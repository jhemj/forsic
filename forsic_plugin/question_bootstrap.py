"""Persist only the requested question at an admitted native investigation turn.

The caller supplies execution intent and expected ownership; reads and compaction
never call this writer. Analytical conclusions remain entirely agent-owned.
"""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .evidence import Case
from .investigation_context import _native_path, resolve_context
from .report_driven import host


PROVISIONAL_ANSWER = '조사 진행 중입니다. 근거에 따른 현재 판단은 아직 저장되지 않았습니다.'


def is_native_investigation_turn(goal, user_message):
    """Recognize complete native goal inputs, without interpreting user prose."""
    if goal is None or goal.status != 'active' or not isinstance(user_message, str):
        return False
    from hermes_cli.goals import (
        CONTINUATION_PROMPT_TEMPLATE, CONTINUATION_PROMPT_WITH_CONTRACT_TEMPLATE,
        CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE, GOAL_ALREADY_SEEN_KICK,
        _render_extra_criteria,
    )
    if goal.has_contract():
        block = goal.contract.render_block()
        if goal.subgoals:
            block += '\n' + _render_extra_criteria(goal.subgoals)
        continuation = CONTINUATION_PROMPT_WITH_CONTRACT_TEMPLATE.format(goal=goal.goal, contract_block=block)
    elif goal.subgoals:
        continuation = CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
            goal=goal.goal, subgoals_block=goal.render_subgoals_block())
    else:
        continuation = CONTINUATION_PROMPT_TEMPLATE.format(goal=goal.goal)
    return user_message in (goal.goal, GOAL_ALREADY_SEEN_KICK, continuation)


def operation_id(case_id, goal_id, conversation_id):
    owner = json.dumps([case_id, goal_id, conversation_id], ensure_ascii=False,
                       separators=(',', ':')).encode()
    return 'initial-question-' + hashlib.sha256(owner).hexdigest()


def _owned_questions(state, goal_id, session_ids):
    return [q for q in state['questions'] if q.get('goal_id') == goal_id
            and q.get('session_id') in session_ids]


def existing_owned_receipt(case, host_args):
    """Call under the existing question writer's BEGIN IMMEDIATE transaction.

    This closes the race with an ordinary agent save. It only returns a current
    question receipt; it neither adopts evidence nor edits that question.
    """
    if host_args.get('_bootstrap_if_empty') is not True:
        return None
    goal_id, conversation_id = host_args.get('_goal_id'), host_args.get('_session_id')
    if not goal_id or not conversation_id:
        raise ValueError('Initial question requires an explicit native goal and conversation')
    binding = host_args.get('_bootstrap_binding')
    if not isinstance(binding, dict) or not all(isinstance(binding.get(k), str) and binding[k]
            for k in ('session_id', 'case_id', 'intake_root', 'native_db')):
        raise ValueError('Initial question requires its explicit native binding before atomic save')
    resolved = resolve_context(binding['session_id'], intake_root=binding['intake_root'],
                               native_db=binding['native_db'])
    if (resolved is None or resolved['goal'] is None or resolved['goal'].status != 'active'):
        raise ValueError('Initial question native goal is no longer active before atomic save')
    if (resolved['case_id'] != binding['case_id'] or resolved['case_id'] != case.config['case_id']
            or resolved['case'].manifest != case.manifest or resolved['goal_id'] != goal_id
            or resolved['conversation_id'] != conversation_id
            or resolved['requested_session_id'] != binding['session_id']
            or binding['session_id'] not in resolved['session_lineage']):
        raise ValueError('Initial question native ownership changed before atomic save')
    # This observation occurs after the case writer lock is acquired, closing
    # the lock-wait window. Native controls use their own store, so this is not
    # a claim of an atomic transaction across native state and the case ledger.
    lineage = host_args.get('_session_lineage') or [conversation_id]
    if conversation_id not in lineage:
        raise ValueError('Initial question conversation is outside its native lineage')
    questions = _owned_questions(host.current(case), goal_id, lineage)
    if not questions:
        return None
    q = questions[0]
    return {'bootstrap_status': 'existing_question', 'question': {
        'question_id': q['id'], 'revision': q['revision'],
        'read_call': {'tool': 'forsic_reporting', 'arguments': {
            'action': 'state', 'question_id': q['id']}}}}


def bootstrap_question(session_id, *, intake_root, native_db=None,
                       investigation_turn=False, turn_id='', expected_case_id='',
                       expected_goal_id='', expected_conversation_id=''):
    """Save a neutral draft once, only for explicitly admitted investigation work.

    A status request with an active standing goal is insufficient authorization:
    investigation_turn must come from the native caller, not user-text guessing.
    Failures are returned independently of read-only context projection.
    """
    if investigation_turn is not True or not turn_id:
        return {'status': 'not_applicable', 'reason': 'not_an_admitted_investigation_turn'}
    expected = (session_id, expected_case_id, expected_goal_id, expected_conversation_id)
    if not all(isinstance(x, str) and x for x in expected):
        return {'status': 'unavailable', 'reason': 'explicit_native_ownership_required'}
    try:
        resolved = resolve_context(session_id, intake_root=intake_root, native_db=native_db)
        if resolved is None:
            return {'status': 'not_applicable', 'reason': 'no_explicit_case_binding'}
        actual = (resolved['requested_session_id'], resolved['case_id'],
                  resolved['goal_id'], resolved['conversation_id'])
        if actual != expected or session_id not in resolved['session_lineage']:
            return {'status': 'unavailable', 'reason': 'native_ownership_changed'}
        with closing(sqlite3.connect(_native_path(native_db).as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('SELECT id FROM sessions WHERE id=?', (session_id,)).fetchone() is None:
                return {'status': 'unavailable', 'reason': 'native_session_unavailable'}
        if resolved['goal'] is None or resolved['goal'].status != 'active':
            return {'status': 'not_applicable', 'reason': 'native_goal_not_active'}
        questions = _owned_questions(host.current(resolved['case']),
                                     expected_goal_id, resolved['session_lineage'])
        if questions:
            return {'status': 'existing_question', 'question_ids': [q['id'] for q in questions]}
        config = resolved['case'].config
        question, scope = config.get('question'), config.get('scope')
        if (not isinstance(question, str) or not question.strip()
                or question == '질문 선택 대기' or not isinstance(scope, str) or not scope.strip()):
            return {'status': 'unavailable', 'reason': 'original_question_or_scope_unavailable'}
        # Comprehensive intake retains the user's optional question separately.
        onboarding = config.get('onboarding') or {}
        extra = onboarding.get('question') if isinstance(onboarding, dict) else None
        if isinstance(extra, str) and extra and extra not in question:
            question += '\n' + extra
        request = {'action': 'question', 'payload': {
            'operation_id': operation_id(*expected[1:]),
            'question': question, 'scope': scope, 'answer': PROVISIONAL_ANSWER,
            'work_state': 'active', 'reason': '사용자가 요청한 질문의 범위를 보존하고 조사를 진행합니다.'},
            '_goal_id': expected_goal_id, '_session_id': expected_conversation_id,
            '_session_lineage': resolved['session_lineage'],
            '_question_contract': 'v2', '_bootstrap_if_empty': True,
            '_bootstrap_binding': {'session_id': session_id, 'case_id': expected_case_id,
                'intake_root': str(Path(intake_root).resolve()), 'native_db': str(_native_path(native_db))}}
        # Recheck the native boundary immediately before entering the sole writer.
        current = resolve_context(session_id, intake_root=intake_root, native_db=native_db)
        if (current is None or current['goal'] is None or current['goal'].status != 'active'
                or (current['requested_session_id'], current['case_id'], current['goal_id'],
                    current['conversation_id']) != expected):
            return {'status': 'unavailable', 'reason': 'native_ownership_changed_before_save'}
        saved = host.invoke(Case(resolved['case'].manifest), request)
        return {'status': saved.get('bootstrap_status', 'saved'),
                'operation_id': request['payload']['operation_id'], 'question': saved['question']}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        return {'status': 'unavailable', 'reason': 'initial_question_save_failed',
                'error_type': type(exc).__name__, 'error': str(exc)}
