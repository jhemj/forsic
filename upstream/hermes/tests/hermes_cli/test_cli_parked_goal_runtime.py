"""A cached CLI owner enforces the native cap before respecting a WAIT barrier."""
import hashlib
import os
import queue
import sys
import time
from types import SimpleNamespace as NS

import pytest

from hermes_cli import goals, goals_completion as completion, goals_runtime as runtime
from hermes_cli.cli_loops_mixin import CLILoopsMixin
from hermes_state import SessionDB


class IdleCLI(CLILoopsMixin):
    def __init__(self, db, manager):
        self._session_db = db
        self.session_id = manager.session_id
        self._goal_manager = manager
        self._pending_input = queue.Queue()


@pytest.fixture
def native(tmp_path, monkeypatch):
    db = SessionDB(tmp_path / 'state.db')
    sid = 'synthetic-cli-wait'
    db.create_session(sid, 'test')
    monkeypatch.setattr(goals, '_get_session_db', lambda: db)
    monkeypatch.setattr(goals, 'configured_runtime_budget', lambda: 3600.)
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: {})
    manager = goals.GoalManager(sid)
    state = manager.set('Preserve the synthetic report', max_turns=8)
    state.runtime_policy = completion.make_policy(3600., {'required_formats': ['html']})
    state.runtime_policy['ready_target_mode'] = 'soft'
    state.turns_used = 3
    state.runtime_recovery = {'checkpoint': {'attempts_used': 1, 'deadline': time.time() + 100}}
    manager._save()
    manager.wait_for_seconds(7200, 'Synthetic timed wait')
    artifact = tmp_path / 'report.html'
    artifact.write_text('<p>Synthetic saved report</p>')
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    work_calls = []
    def work(*args, **kwargs):
        work_calls.append(args)
        return {'status': 'known', 'digest': 'synthetic-basis',
                'artifact_status': 'draft_preserved', 'delivery_status': 'unconfirmed',
                'blockers': ['independent_review'],
                'artifacts': [{'path': str(artifact), 'sha256': digest}]}
    monkeypatch.setattr(completion, 'read_work_projection', work)
    monkeypatch.setattr(goals, 'judge_goal', lambda *a, **kw: pytest.fail('Idle boundary must not call a model'))
    monkeypatch.setattr(goals, '_session_waiting', lambda sid: True)
    monkeypatch.setitem(sys.modules, 'cli', NS(_DIM='', _RST='', _cprint=lambda *a: None))
    fixture = NS(db=db, sid=sid, manager=manager, cli=IdleCLI(db, manager),
                 path=artifact, digest=digest, work_calls=work_calls)
    yield fixture
    db.close()


def read(native):
    raw = native.db.get_meta('goal:' + native.sid)
    return goals.GoalState.from_json(raw) if raw else None


def write(native, state):
    native.db.set_meta('goal:' + native.sid, state.to_json())


@pytest.mark.parametrize('barrier', ['timer', 'live_pid', 'live_session'])
def test_cached_idle_owner_settles_expired_goal_before_live_wait(native, barrier):
    state = read(native)
    if barrier != 'timer':
        state.waiting_until = 0.0
        if barrier == 'live_pid': state.waiting_on_pid = os.getpid()
        else: state.waiting_on_session = 'synthetic-live-process'
    state.runtime_started_at = time.time() - state.runtime_budget_seconds - 1
    write(native, state)
    assert native.cli._get_goal_manager() is native.manager
    assert native.manager.state.status == 'active'
    before = (state.turns_used, state.max_turns, state.runtime_budget_seconds, state.runtime_recovery)
    native.cli._maybe_resume_parked_goal()
    result = read(native)
    assert result.status == 'paused'
    assert result.paused_reason == runtime.RUNTIME_BUDGET_REASON
    assert result.runtime_settlement['artifact_status'] == 'draft_preserved'
    assert result.runtime_settlement['artifact_paths'] == [
        {'path': str(native.path), 'sha256': native.digest, 'bytes': native.path.stat().st_size}]
    assert (result.turns_used, result.max_turns, result.runtime_budget_seconds, result.runtime_recovery) == before
    assert not result.waiting_until and result.waiting_on_pid is None and result.waiting_on_session is None
    assert native.cli._pending_input.empty() and not native.db.get_messages(native.sid)
    assert len(native.work_calls) == 1
    receipt = result.runtime_settlement
    native.cli._last_goal_barrier_check = 0
    native.cli._maybe_resume_parked_goal()
    assert read(native).runtime_settlement == receipt and len(native.work_calls) == 1


@pytest.mark.parametrize('control', [
    'manual_pause', 'pending_pause', 'common_pending_pause', 'replacement', 'clear', 'queued_user',
    'live_timer', 'live_pid', 'live_session', 'normal_wake', 'throttled',
    'cas_manual_pause', 'cas_pending_pause', 'cas_replacement', 'cas_clear',
    'barrier_manual_pause', 'barrier_pending_pause', 'barrier_replacement', 'barrier_clear',
    'prompt_manual_pause', 'prompt_pending_pause', 'prompt_replacement', 'prompt_clear',
    'prompt_queued_user',
])
def test_idle_owner_preserves_control_barriers_and_normal_wake(native, monkeypatch, control):
    stage, _, action = control.partition('_')
    raced = stage in {'cas', 'barrier', 'prompt'}
    state = read(native)
    if stage == 'cas' or control in {'manual_pause', 'pending_pause', 'common_pending_pause',
                                      'replacement', 'clear', 'queued_user', 'throttled'}:
        state.runtime_started_at = time.time() - state.runtime_budget_seconds - 1
    if stage in {'barrier', 'prompt'} or control == 'normal_wake':
        state.waiting_until = time.time() - 1
    if control in {'live_pid', 'live_session'}:
        state.waiting_until = 0.0
        if control == 'live_pid': state.waiting_on_pid = os.getpid()
        else: state.waiting_on_session = 'synthetic-live-process'
    write(native, state)
    expected = [None]
    def apply_control(action):
        current = read(native)
        if action == 'manual_pause':
            current.status = 'paused'; current.paused_reason = 'user-paused'; current.clear_wait()
        elif action in {'pending_pause', 'common_pending_pause'}: current.pause_requested = True
        elif action == 'replacement': current.goal = 'Replacement scope'; current.created_at += 1
        if action == 'clear': native.db.set_meta('goal:' + native.sid, '')
        else: write(native, current)
        if action == 'queued_user': native.cli._pending_input.put('User request first')
        expected[0] = native.db.get_meta('goal:' + native.sid)
    if raced:
        if stage == 'cas':
            original = native.db.compare_and_swap_meta
            def race(*args, **kwargs):
                apply_control(action)
                return original(*args, **kwargs)
            monkeypatch.setattr(native.db, 'compare_and_swap_meta', race)
        else:
            name = 'is_waiting' if stage == 'barrier' else 'next_continuation_prompt'
            original = getattr(native.manager, name)
            def race(*args, **kwargs):
                result = original(*args, **kwargs)
                apply_control(action)
                return result
            monkeypatch.setattr(native.manager, name, race)
    else:
        apply_control(control)
    if control == 'throttled': native.cli._last_goal_barrier_check = time.time()
    if control == 'common_pending_pause':
        result, paused = runtime.enforce_goal_runtime(native.db, native.sid,
                                                     expected_goal_id=runtime.runtime_identity(state))
        assert not paused and result.pause_requested
    else:
        native.cli._maybe_resume_parked_goal()
    if control == 'normal_wake':
        result = read(native)
        assert result.status == 'active' and not result.waiting_until
        assert native.cli._pending_input.qsize() == 1
        assert result.goal in native.cli._pending_input.get_nowait()
        native.cli._last_goal_barrier_check = 0
        native.cli._maybe_resume_parked_goal()
        assert native.cli._pending_input.empty()
    else:
        assert expected[0] is not None, 'The requested control race was not exercised'
        assert native.db.get_meta('goal:' + native.sid) == expected[0]
        assert native.cli._pending_input.qsize() == (1 if control.endswith('queued_user') else 0)
    assert not native.work_calls and not native.db.get_messages(native.sid)
