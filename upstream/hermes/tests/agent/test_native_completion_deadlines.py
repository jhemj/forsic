"""H23-H35: deadlines derive from the durable native clock, including explicit calls."""
from types import SimpleNamespace as NS
import concurrent.futures
import time as real_time

import pytest

from agent import chat_completion_helpers as calls
from hermes_cli import goals, goals_runtime as runtime, goals_completion as completion
from hermes_state import SessionDB


@pytest.fixture
def task(tmp_path, monkeypatch):
    clock = NS(now=100000.)
    fake = NS(time=lambda: clock.now)
    for module in (goals, runtime, completion, calls):
        monkeypatch.setattr(module, 'time', fake)
    db = SessionDB(tmp_path / 'state.db');db.create_session('task', 'test')
    monkeypatch.setattr(goals, '_get_session_db', lambda: db)
    monkeypatch.setattr(goals, 'configured_runtime_budget', lambda: 86400.)
    manager = goals.GoalManager('task');manager.set('Synthetic report')
    agent = NS(_session_db=db, session_id='task', run_budget_seconds=86400., _run_budget_started_at=clock.now)
    yield clock, db, manager, agent
    db.close()


def test_H29_native_clock_caps_fresh_turn_timeout(task):
    clock, db, manager, agent = task
    state = goals.load_goal('task');state.runtime_elapsed_seconds = 86400. - 36
    db.set_meta('goal:task', state.to_json())
    agent._run_budget_started_at = clock.now - 5
    assert calls.cap_to_run_budget(agent, 600.) <= 36.


@pytest.mark.parametrize('hours', [12, 18, 24])
def test_H23_H32_cold_reserve_boundaries_and_settlement_are_distinct(task, hours):
    clock, db, manager, agent = task
    state = goals.load_goal('task');state.runtime_budget_seconds = hours*3600.
    state.runtime_policy = completion.make_policy(state.runtime_budget_seconds, {'required_formats':['html']})
    state.runtime_elapsed_seconds = hours*3600.*.75 - 1
    unknown = {'status':'unknown'}
    assert completion.control_projection(state, unknown)['phase'] == 'investigation'
    state.runtime_elapsed_seconds += 1
    c = completion.control_projection(state, unknown)
    assert c['phase'] == 'closing' and c['reserve_seconds'] == hours*3600.*.25
    assert c['ready_deadline'] < c['deadline']
    state.runtime_elapsed_seconds = state.runtime_budget_seconds - c['settlement_seconds']
    assert completion.control_projection(state, unknown)['phase'] == 'settlement'
    # Reopening without older checkpoint receipts must still enter model-free
    # settlement, rather than requesting an overdue decision after its deadline.
    assert runtime.runtime_phase(state, work=unknown) == 'settlement'
    state.runtime_elapsed_seconds = state.runtime_budget_seconds
    assert completion.control_projection(state, unknown)['phase'] == 'exhausted'


def test_H32_measured_work_adds_settlement_once(task):
    clock, db, manager, agent = task
    state = goals.load_goal('task');state.runtime_policy = completion.make_policy(86400.)
    work={'status':'known','estimate':{'known':True,'remaining_seconds':6*3600.,'queue_seconds':0.,'uncertainty_seconds':0.}}
    c=completion.control_projection(state, work)
    assert c['reserve_seconds'] == 6.5*3600
    assert c['latest_safe_close'] == c['deadline'] - 6.5*3600
    work['estimate'].update(remaining_seconds=5.5*3600.,queue_seconds=1800.,queue_included=True)
    assert completion.control_projection(state, work)['reserve_seconds'] == 6*3600


def test_H25_pause_freezes_active_clock_but_not_wall_submission(task):
    clock, db, manager, agent = task
    state=goals.load_goal('task');state.wall_deadline=clock.now+20
    db.set_meta('goal:task',state.to_json());manager.pause(reason='user stop')
    before=goals.load_goal('task').runtime_elapsed_seconds;clock.now+=21
    state=goals.load_goal('task')
    assert state.runtime_elapsed_seconds == before
    assert runtime.runtime_remaining(state) == 0
    assert state.status == 'paused' and state.paused_reason == 'user stop'


def test_H27_H31_explicit_operation_deadline_survives_new_attempt(task):
    clock, db, manager, agent=task
    agent._runtime_checkpoint_deadline=clock.now+7
    assert completion.operation_timeout(agent,600.) == 7
    clock.now+=3
    assert completion.operation_timeout(agent,600.) == 4
    clock.now+=4
    assert completion.operation_timeout(agent,600.) <= .001


def test_H28_H29_sequential_wait_honors_worker_grant_despite_approval_exclusion():
    from agent.tool_executor import _poll_sequential_future
    agent = NS(_native_tool_grants={'one':{'deadline':real_time.time()+.03}},
               _interrupt_requested=False)
    gate = NS(excluded_seconds=lambda:100.)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(real_time.sleep, .12)
        started = real_time.monotonic()
        state, _ = _poll_sequential_future(agent, future, 'bounded_test', None, started, gate, 'one')
        assert state == 'timeout'
        assert not future.done()
        assert real_time.monotonic()-started < .11
