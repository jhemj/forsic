"""A fresh human objective after a runtime handoff uses one native bounded goal."""
from contextlib import nullcontext
import json
import logging
from types import SimpleNamespace
import threading
from unittest.mock import Mock

import pytest

from hermes_cli import goals, goals_runtime
from hermes_state import SessionDB
from tui_gateway import methods_prompt


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    db = SessionDB(tmp_path / 'state.db')
    db.create_session('dialogue', source='web')
    monkeypatch.setattr(goals, '_get_session_db', lambda: db)
    monkeypatch.setattr(goals, 'configured_runtime_budget', lambda: 120.0)
    monkeypatch.setattr(goals_runtime, 'configured_runtime_budget', lambda: 120.0)
    monkeypatch.setattr(goals, 'judge_goal', Mock(side_effect=AssertionError('no model calls')))
    handlers = {}
    server = SimpleNamespace(register_method=lambda name, fn: handlers.__setitem__(name, fn),
        _load_cfg=lambda: {'goals': {'max_turns': 8}},
        _session_profile_runtime_scope=lambda _: nullcontext(),
        _session_db=lambda _: nullcontext(db),
        _submit_row_owner_key=lambda staged, session: staged['_submit_row_session_id'],
        logger=logging.getLogger(__name__))
    methods_prompt.register(server)
    previous = goals.GoalState(goal='Original investigation', status='paused', created_at=1000.0,
        paused_reason=goals_runtime.RUNTIME_HANDOFF_REASON, last_verdict='runtime_budget_handoff',
        last_reason='Findings and limits handed to the user', runtime_budget_seconds=100.0,
        runtime_elapsed_seconds=95.0)
    db.set_meta('goal:dialogue', previous.to_json())
    db.append_message('dialogue', 'assistant', content='Previous report and limits')
    text = 'Please investigate the suspicious process further'
    row_id = db.append_message('dialogue', 'user', content=text)
    staged = {'_row_id': row_id, 'role': 'user', 'content': text,
              '_submit_row_session_id': 'dialogue'}
    session = {'source': 'web', 'session_key': 'dialogue', 'agent': SimpleNamespace(session_id='dialogue'),
               'history_lock': threading.RLock(), '_submit_user_row': staged, 'history': []}
    yield SimpleNamespace(server=server, db=db, previous=previous, text=text, row_id=row_id,
                          session=session, handlers=handlers)
    db.close()


def metadata(runtime):
    return runtime.db._execute_write(lambda conn: json.loads(conn.execute(
        'SELECT display_metadata FROM messages WHERE id=?', (runtime.row_id,)).fetchone()[0] or '{}'))


def test_fresh_human_message_starts_new_scope_and_preserves_handoff_receipt(runtime):
    rt = runtime
    previous_id = goals.goal_identity(rt.previous)
    receipt = rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=True)
    current = goals.load_goal('dialogue')
    assert current.status == 'active' and current.goal == rt.text
    assert goals.goal_identity(current) != previous_id
    assert current.runtime_budget_seconds == 120.0 and current.max_turns == 8
    assert current.runtime_followup_of == previous_id
    assert current.runtime_followup_user_row_id == rt.row_id
    assert current.runtime_elapsed_seconds < 2.0
    assert current.runtime_started_at is not None
    assert metadata(rt)['runtime_handoff_followup'] == receipt
    assert receipt['previous_runtime_budget_seconds'] == 100.0
    assert receipt['previous_runtime_elapsed_seconds'] == 95.0
    assert receipt['previous_paused_reason'] == goals_runtime.RUNTIME_HANDOFF_REASON
    assert receipt['previous_last_reason'] == rt.previous.last_reason
    assert receipt['goal_id'] == goals.goal_identity(current)
    assert receipt['accepted_user_row_id'] == rt.row_id
    rows = rt.db.get_messages('dialogue')
    assert receipt['accepted_user_message_uid'] == rows[-1]['message_uid']
    assert len(rows) == 2 and rows[0]['content'] == 'Previous report and limits'
    before = rt.db.get_meta('goal:dialogue')
    assert rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=True) is None
    assert rt.db.get_meta('goal:dialogue') == before


def test_handoff_receipt_resolves_native_compaction_generations_and_rewind_revokes(runtime):
    from hermes_state_identity import resolve_message_generation
    rt = runtime
    receipt = rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=True)
    for _ in range(2):
        messages = rt.db.get_messages_as_conversation('dialogue', include_row_ids=True)
        rt.db.archive_and_compact('dialogue', [dict(m) for m in messages],
            covered_ids=[m['_row_id'] for m in messages], carried_messages=messages)
    def generation(conn):
        return resolve_message_generation(conn, rt.row_id, ['dialogue'])
    origin, current = rt.db._execute_write(generation)
    assert not origin['active'] and not origin['compacted']
    assert current['id'] != rt.row_id and current['active']
    assert origin['message_uid'] == current['message_uid'] == receipt['accepted_user_message_uid']
    assert json.loads(current['display_metadata'])['runtime_handoff_followup'] == receipt
    rt.db._execute_write(lambda conn: conn.execute(
        'UPDATE messages SET active=0,compacted=0 WHERE id=?', (current['id'],)))
    assert rt.db._execute_write(generation) is None


@pytest.mark.parametrize('reason', ['user-paused', 'user-safe-paused',
    goals_runtime.RUNTIME_HISTORY_REASON, 'tool guardrail: halt'])
def test_other_pauses_are_never_replaced_or_renewed(runtime, reason):
    rt = runtime
    rt.previous.paused_reason = reason
    rt.db.set_meta('goal:dialogue', rt.previous.to_json())
    before = rt.db.get_meta('goal:dialogue')
    assert rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=True) is None
    assert rt.db.get_meta('goal:dialogue') == before
    assert metadata(rt) == {}


@pytest.mark.parametrize('source,fresh', [('web', False), ('bot_room', True), ('tool', True), ('cron', True)])
def test_background_and_nonhuman_sources_cannot_grant_allowance(runtime, source, fresh):
    rt = runtime
    rt.session['source'] = source
    before = rt.db.get_meta('goal:dialogue')
    assert rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=fresh) is None
    assert rt.db.get_meta('goal:dialogue') == before
    assert metadata(rt) == {}


def test_hard_runtime_exhaustion_needs_a_fresh_human_row_and_preserves_original_state(runtime):
    rt = runtime
    rt.previous.paused_reason = goals_runtime.RUNTIME_BUDGET_REASON
    rt.previous.runtime_elapsed_seconds = rt.previous.runtime_budget_seconds
    raw = rt.previous.to_json()
    rt.db.set_meta('goal:dialogue', raw)
    assert rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=False) is None
    assert rt.db.get_meta('goal:dialogue') == raw
    result = rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=True)
    current = goals.load_goal('dialogue')
    assert result['previous_goal_state'] == json.loads(raw)
    assert current.runtime_followup_of == goals.goal_identity(rt.previous)
    assert current.runtime_budget_seconds == 120.0
    assert current.goal == rt.text


def test_same_accepted_user_row_cannot_grant_next_allowance_after_replay(runtime):
    rt = runtime
    rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=True)
    current = goals.load_goal('dialogue')
    current.status, current.paused_reason = 'paused', goals_runtime.RUNTIME_HANDOFF_REASON
    current.runtime_started_at = None
    rt.db.set_meta('goal:dialogue', current.to_json())
    before = rt.db.get_meta('goal:dialogue')
    with pytest.raises(goals._StaleGoalEvaluation):
        rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=True)
    assert rt.db.get_meta('goal:dialogue') == before


@pytest.mark.parametrize('replacement', ['pause', 'new-goal'])
def test_concurrent_native_control_wins_exact_cas_without_retry(runtime, monkeypatch, replacement):
    rt = runtime
    original_save = goals.GoalManager._save
    concurrent = goals.GoalState.from_json(rt.previous.to_json())
    if replacement == 'pause':
        concurrent.paused_reason = 'user-paused'
    else:
        concurrent = goals.GoalState(goal='Different explicit objective', created_at=2000.0)
    concurrent_raw = concurrent.to_json()
    calls = []
    def raced(manager):
        calls.append(manager.session_id)
        rt.db.set_meta('goal:dialogue', concurrent_raw)
        return original_save(manager)
    monkeypatch.setattr(goals.GoalManager, '_save', raced)
    with pytest.raises(goals._StaleGoalEvaluation):
        rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=True)
    assert calls == ['dialogue']
    assert rt.db.get_meta('goal:dialogue') == concurrent_raw
    assert metadata(rt) == {}


def test_receipt_storage_failure_rolls_back_goal_and_metadata(runtime, monkeypatch):
    rt = runtime
    original = rt.db._execute_write
    before = rt.db.get_meta('goal:dialogue')
    class BrokenReceipt:
        def __init__(self, conn): self.conn = conn
        def execute(self, sql, parameters=()):
            if sql.startswith('UPDATE messages SET display_metadata='):
                raise OSError('synthetic receipt write failure')
            return self.conn.execute(sql, parameters)
    monkeypatch.setattr(rt.db, '_execute_write', lambda fn, **kw: original(lambda conn: fn(BrokenReceipt(conn)), **kw))
    with pytest.raises(OSError):
        rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=True)
    assert rt.db.get_meta('goal:dialogue') == before
    assert metadata(rt) == {}


def test_followup_uses_current_compression_tip_and_leaves_parent_unchanged(runtime):
    rt = runtime
    rt.db.create_session('parent', source='web')
    rt.db.set_meta('goal:parent', rt.previous.to_json())
    rt.session['session_key'] = 'parent'
    receipt = rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=True)
    assert receipt['accepted_user_row_id'] == rt.row_id
    assert goals.load_goal('dialogue').goal == rt.text
    assert rt.db.get_meta('goal:parent') == rt.previous.to_json()


@pytest.mark.parametrize('defect', ['missing', 'different-text', 'hidden', 'foreign-row'])
def test_followup_requires_exact_fresh_visible_accepted_row(runtime, defect):
    rt = runtime
    staged = rt.session['_submit_user_row']
    if defect == 'missing': rt.session.pop('_submit_user_row')
    elif defect == 'different-text': staged['content'] = 'other text'
    elif defect == 'hidden': staged['display_kind'] = 'clarification_result'
    else: staged['_submit_row_session_id'] = 'another-session'
    before = rt.db.get_meta('goal:dialogue')
    with pytest.raises(goals._StaleGoalEvaluation):
        rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=True)
    assert rt.db.get_meta('goal:dialogue') == before


def test_missing_configured_ceiling_cannot_create_unbounded_followup(runtime, monkeypatch):
    rt = runtime
    monkeypatch.setattr(goals, 'configured_runtime_budget', lambda: None)
    before = rt.db.get_meta('goal:dialogue')
    with pytest.raises(ValueError):
        rt.server._start_runtime_handoff_followup(rt.session, rt.text, fresh_human=True)
    assert rt.db.get_meta('goal:dialogue') == before


def configure_submit(rt):
    server = rt.server
    rt.session['running'] = False
    rt.session['agent_ready'] = threading.Event()
    server.threading = SimpleNamespace(Thread=lambda **kw: SimpleNamespace(start=lambda: None))
    server._typed_stop_phrase_response = lambda *args: None
    server._sess_nowait = lambda *args: (rt.session, None)
    server._legacy_group_fence_error = lambda *args: None
    server._ensure_active_session_slot = lambda *args: None
    server._load_dashboard_process_isolation_config = lambda: {}
    server._session_uses_compute_host = lambda *args: False
    server.current_transport = lambda: None
    server._session_resume_lock = threading.RLock()
    server._reattach_refusal = lambda *args: None
    server._lock_in_submit_turn = lambda *args: (None, {})
    server._persist_session_row_for_submit = lambda *args: None
    server._restart_completed_failed_agent_build = lambda *args: True
    server._emit = Mock()
    server._ok = lambda rid, value: {'result': value}
    server._err = lambda rid, code, text, *args: {'error': {'code': code, 'message': text}}
    return server


@pytest.mark.parametrize('params,source,expected', [({}, 'web', True),
    ({'display_kind': 'hidden'}, 'web', False), ({'queued': True}, 'web', False),
    ({}, 'bot_room', False)])
def test_real_prompt_submit_admits_only_ordinary_fresh_human_input(runtime, params, source, expected):
    rt = runtime
    server = configure_submit(rt)
    rt.session['source'] = source
    response = rt.handlers['prompt.submit']('human-rpc', {'session_id': 'ui', 'text': rt.text, **params})
    assert response['result']['status'] == 'streaming'
    current = goals.load_goal('dialogue')
    assert (current.status == 'active') is expected
    assert bool(metadata(rt)) is expected


@pytest.mark.parametrize('language', ['en', 'ko'])
def test_followup_status_uses_native_locale_and_failed_emit_cannot_undo_admission(runtime, monkeypatch, language):
    rt = runtime
    server = configure_submit(rt)
    # The dispatch thread is an offline seam; every goal and message write is native.
    from agent import i18n
    monkeypatch.setattr(i18n, '_resolve_language', lambda *args, **kw: language)
    server._emit = Mock(side_effect=RuntimeError('disconnected status transport'))
    response = rt.handlers['prompt.submit']('second-human', {'session_id': 'ui', 'text': rt.text})
    assert response['result']['status'] == 'streaming'
    assert goals.load_goal('dialogue').runtime_followup_user_row_id == rt.row_id
    message = server._emit.call_args.args[2]['text']
    assert ('추가 조사를 시작' in message) is (language == 'ko')
    assert 'runtime_handoff_followup_started' not in message
