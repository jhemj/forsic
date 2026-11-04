"""Generated native transitions: real goals/CAS/receipts, synthetic work/provider seam.

This is a test driver, not an alternate scheduler or a model of production state.
Each event calls the existing native API; assertions observe the durable result.
Domain review/evidence gate semantics remain in the actual Forsic report suites.
No OS restart, live provider, external delivery, or wall-clock soak is claimed.
"""
from copy import deepcopy
import hashlib
import json
import random
from types import SimpleNamespace as NS

import pytest

from agent.iteration_budget import IterationBudget
from hermes_cli import goals, goals_completion as completion, goals_recovery as recovery
from hermes_cli import goals_runtime as runtime, plugins
from hermes_state import SessionDB


@pytest.fixture
def native(tmp_path, monkeypatch):
    clock = NS(now=100000.)
    for module in (goals, runtime, recovery, completion):
        monkeypatch.setattr(module, 'time', NS(time=lambda: clock.now))
    store = NS(db=SessionDB(tmp_path / 'state.db'), path=tmp_path / 'state.db')
    monkeypatch.setattr(goals, '_get_session_db', lambda: store.db)
    monkeypatch.setattr(goals, 'configured_runtime_budget', lambda: 86400.)
    work = {}
    agents = {}
    for sid in ('alpha', 'beta'):
        store.db.create_session(sid, 'test')
        state = goals.GoalManager(sid).set('Synthetic report ' + sid, max_turns=32)
        state.runtime_policy = completion.make_policy(86400., {'required_formats': ['html']})
        store.db.set_meta('goal:' + sid, state.to_json())
        store.db.append_message(sid, 'tool', 'Preserved result ' + sid, message_uid='source-' + sid)
        work[sid] = {'status': 'known', 'identity': {'case': sid}, 'basis': {'revision': 1},
            'digest': sid + '-basis-1', 'blockers': ['unresolved-' + sid], 'estimate': {'known': False},
            'review_work': {'remaining_rounds': 3, 'unanswered_findings': ['one'], 'final_review_needed': True},
            'artifact_work': {'render_needed': True, 'selection_needed': True},
            'quota': {}, 'artifact_status': 'partial_checkpoint', 'delivery_status': 'unconfirmed'}
        agents[sid] = NS(_session_db=store.db, session_id=sid,
            _runtime_turn_identity=runtime.runtime_identity(state), interrupt=lambda *a, **k: None,
            iteration_budget=IterationBudget(64))
    def hook(name, **kw):
        if kw.get('mode') == 'classify_action':
            return [{'purpose': 'report' if kw['action']['tool'] == 'saved_report' else 'discovery',
                     'max_seconds': 30.}]
        return [work[kw['session_id']]]
    monkeypatch.setattr(plugins, 'has_hook', lambda name: name == 'goal_work_projection')
    monkeypatch.setattr(plugins, 'invoke_hook', hook)
    # Only provider response is synthetic; turn accounting and CAS remain native.
    monkeypatch.setattr(goals, 'judge_goal', lambda *a, **kw:
                        ('continue', 'Synthetic bounded work remains', False, None, False))
    yield NS(clock=clock, store=store, agents=agents, work=work)
    store.db.close()


def reopen(rt):
    rt.store.db.close()
    rt.store.db = SessionDB(rt.store.path)
    for agent in rt.agents.values():
        agent._session_db = rt.store.db


def saved(rt, sid):
    return goals.GoalState.from_json(rt.store.db.get_meta('goal:' + sid))


@pytest.mark.parametrize('resource,remaining,required', [
    ('ordinary', 0, 1), ('ordinary', 1, 2), ('ordinary', 2, 1),
    ('main_calls', 0, 1), ('main_calls', 3, 4), ('native_turns', 0, 1),
    ('native_turns', 1, 1),
])
def test_H34_H35_resource_shortage_closes_discovery_without_reset(native, resource, remaining, required):
    rt = native
    state = saved(rt, 'alpha')
    before = (state.runtime_budget_seconds, state.turns_used, state.max_turns)
    rt.work['alpha']['quota'] = {resource: {'remaining': remaining, 'required_reserved': required}}
    control = completion.control_projection(state, rt.work['alpha'])
    assert control['quota_infeasible'] == (remaining < required)
    if remaining < required:
        assert control['phase'] == 'closing'
        assert not completion.admit_action(rt.agents['alpha'], purpose='discovery', max_seconds=1)['allowed']
    state = saved(rt, 'alpha')
    assert (state.runtime_budget_seconds, state.turns_used, state.max_turns) == before


def test_H34_native_turn_cap_used_projection_is_not_silently_ignored(native):
    rt = native
    # This is the actual domain native_turns shape, not a fabricated remaining field.
    rt.work['alpha']['quota'] = {'native_turns': {'cap': 4, 'used': 4,
                                                 'required_reserved': 1, 'unit': 'turns'}}
    control = completion.control_projection(saved(rt, 'alpha'), rt.work['alpha'])
    assert control['quota_infeasible']
    assert control['phase'] == 'closing'


@pytest.mark.parametrize('resource', [
    {'remaining': None, 'required_reserved': 1},
    {'cap': 4, 'used': None, 'required_reserved': 1},
    {'cap': 4, 'used': 1, 'required_reserved': None},
    {'cap': 4, 'used': 1},
    {'remaining': float('nan'), 'required_reserved': 1},
    {'remaining': True, 'required_reserved': 1},
])
def test_H34_unknown_resource_is_not_silently_zero_or_known(native, resource):
    rt = native
    rt.work['alpha']['quota'] = {'ordinary': resource}
    control = completion.control_projection(saved(rt, 'alpha'), rt.work['alpha'])
    assert control['unknown_quota'] == ['ordinary']
    assert not control['quota_infeasible']
    assert control['phase'] == 'investigation'


@pytest.mark.parametrize('remaining', [1, 3, 9, 10, 20])
def test_H35_existing_iteration_slots_reserve_completion_without_minting_calls(native, remaining):
    rt = native
    agent = rt.agents['alpha']
    agent.iteration_budget = IterationBudget(20)
    for _ in range(20 - remaining):
        assert agent.iteration_budget.consume()
    before = agent.iteration_budget.used
    # 3 rounds*2 + pending answer + final + render + selection = 10 existing calls.
    completion.admit_model_request(agent, 'accepted-current-request')
    assert agent.iteration_budget.used == before
    state = saved(rt, 'alpha')
    if remaining <= 10:
        assert state.runtime_control['phase'] == 'closing'
        assert state.runtime_control['reason'] == 'main_request_slots_reserved'
        assert not completion.admit_action(agent, purpose='discovery', max_seconds=1)['allowed']
    else:
        assert not state.runtime_control
    assert agent.iteration_budget.max_total == 20


@pytest.mark.parametrize('seed', [7, 19, 41, 73, 109, 211])
def test_H62_two_case_generated_controls_recovery_and_receipts_preserve_native_invariants(native, seed):
    rt = native
    rng = random.Random(seed)
    ledger = {}
    original_rows = {sid: rt.store.db.get_messages(sid) for sid in rt.agents}
    stale_agents = []
    native_turns_evaluated = 0
    # Every seed includes each event; order and case interleaving vary.
    events = ['tick', 'turn', 'wait', 'defer', 'claim', 'start', 'retry', 'reopen',
              'pause', 'resume', 'replace', 'basis', 'checkpoint', 'settle'] * 5
    rng.shuffle(events)
    # A valid normal turn establishes nonzero usage before random pauses/WAITs.
    events.insert(0, 'turn')
    for index, event in enumerate(events):
        sid = rng.choice(tuple(rt.agents))
        other = 'beta' if sid == 'alpha' else 'alpha'
        untouched = rt.store.db.get_meta('goal:' + other)
        rt.clock.now += rng.randint(1, 12)
        state = saved(rt, sid)
        manager = goals.GoalManager(sid)
        agent = rt.agents[sid]
        if event == 'reopen':
            reopen(rt)
        elif event == 'pause' and state.status == 'active':
            manager.pause('explicit test user pause')
        elif event == 'resume' and state.status == 'paused':
            # Explicit test user event, never performed by the production recovery driver.
            manager.resume(reset_budget=False)
        elif event == 'replace':
            stale_agents.append(deepcopy(agent._runtime_turn_identity))
            new = manager.set('New explicit scope ' + sid + str(index), max_turns=32)
            new.runtime_policy = completion.make_policy(86400., {'required_formats': ['html']})
            rt.store.db.set_meta('goal:' + sid, new.to_json())
            blocked, _ = completion.admit_tool_request(agent, 'saved_report', {}, 'late-old-' + str(index))
            assert blocked  # INV-02/07: replacement never adopts old work.
            rt.agents[sid] = NS(_session_db=rt.store.db, session_id=sid,
                _runtime_turn_identity=runtime.runtime_identity(new), interrupt=lambda *a, **k: None,
                iteration_budget=IterationBudget(64))
        elif event == 'wait' and state.status == 'active':
            manager.wait_for_seconds(rng.randint(1, 20), 'bounded native wait')
        elif event == 'turn' and state.status == 'active':
            if not manager.is_waiting():
                assert agent.iteration_budget.consume()
                before_turns = state.turns_used
                manager.evaluate_after_turn('Synthetic result retained in the existing source')
                assert saved(rt, sid).turns_used == before_turns + 1
                native_turns_evaluated += 1
        elif event in {'defer', 'retry'} and state.status == 'active':
            recovery.prepare_context_recovery(rt.store.db, sid,
                expected_scope=goals.goal_identity(state), cause='compression_lock_busy',
                source_turn=f'{seed}-{index}', owner='native-test-owner')
        elif event == 'claim':
            claim = recovery.claim_context_recovery(rt.store.db, sid, owner='native-test-owner')
            if claim:
                assert recovery.claim_context_recovery(rt.store.db, sid, owner='competitor') is None
        elif event == 'start':
            context = state.runtime_recovery.get('context', {})
            if context.get('claim_id'):
                started = recovery.context_turn_started(rt.store.db, sid, context['claim_id'])
                if started:
                    assert not recovery.context_turn_started(rt.store.db, sid, context['claim_id'])
        elif event == 'basis':
            rt.work[sid]['digest'] += '-changed'
        elif event == 'checkpoint' and state.status == 'active':
            completion.claim_recovery(rt.store.db, sid, 'checkpoint',
                expected_identity=runtime.runtime_identity(state), deadline=rt.clock.now + 40,
                max_attempts=2, threshold=43200)
        elif event == 'settle':
            completion.settle_native(rt.store.db, sid, expected_identity=runtime.runtime_identity(state),
                                     reason='synthetic partial observation')
        assert rt.store.db.get_meta('goal:' + other) == untouched  # case isolation
        for owner in rt.agents:
            current = saved(rt, owner)
            identity = (owner, *runtime.runtime_identity(current))
            prior = ledger.get(identity)
            elapsed = runtime.runtime_elapsed(current)
            recovery_state = current.runtime_recovery
            counts = {kind: rec.get('total_attempts', rec.get('attempts', 0))
                      for kind, rec in recovery_state.items()}
            if prior:
                assert elapsed >= prior['elapsed']  # INV-01/08
                assert current.turns_used >= prior['turns']
                assert rt.agents[owner].iteration_budget.used >= prior['calls']
                for kind, count in prior['counts'].items():
                    assert counts.get(kind, 0) >= count
            ledger[identity] = {'elapsed': elapsed, 'turns': current.turns_used, 'counts': counts,
                                'calls': rt.agents[owner].iteration_budget.used}
            assert current.runtime_budget_seconds == 86400.
            assert current.runtime_checkpoints == []  # claims do not forge decisions (INV-05)
            assert rt.store.db.get_messages(owner) == original_rows[owner]  # INV-06
            context = recovery_state.get('context') or {}
            if context.get('status') == 'pending' and current.status == 'active':
                assert context['next_owner'] and context['owner']
                assert context['next_due'] <= context['original_deadline']
            if current.status != 'active':
                with pytest.raises(completion.NativeAdmissionDenied):
                    completion.admit_model_request(rt.agents[owner], f'{seed}-{index}-late')
            if current.runtime_settlement:
                assert current.runtime_settlement['artifact_status'] == 'partial_checkpoint'
                assert current.runtime_settlement['delivery_status'] != 'confirmed'
    assert stale_agents and ledger and native_turns_evaluated > 0
    # Fair clock advancement must settle every pending recovery, never infinite WAIT.
    for sid, agent in rt.agents.items():
        state = saved(rt, sid)
        if state.status == 'paused':
            goals.GoalManager(sid).resume(reset_budget=False)
        state = saved(rt, sid)
        recovery.prepare_context_recovery(rt.store.db, sid, expected_scope=goals.goal_identity(state),
            cause='compression_lock_busy', source_turn='final-fairness', owner='native-test-owner')
        rt.clock.now += 901
        recovery.claim_context_recovery(rt.store.db, sid, owner='native-test-owner')
        state = saved(rt, sid)
        assert state.status == 'paused'
        assert state.runtime_recovery['context']['status'] == 'terminal'


@pytest.mark.parametrize('unknown_started', [False, True])
def test_H57_recovery_unknown_delivery_reopen_never_replays_effect(native, unknown_started):
    rt = native
    sid = 'alpha'
    state = saved(rt, sid)
    recovery.prepare_context_recovery(rt.store.db, sid, expected_scope=goals.goal_identity(state),
        cause='compression_lock_busy', source_turn='unknown-effect', owner='one')
    rt.clock.now += 11
    claim = recovery.claim_context_recovery(rt.store.db, sid, owner='one')
    assert claim
    if unknown_started:
        assert recovery.context_turn_started(rt.store.db, sid, claim['claim_id'])
    before = deepcopy(saved(rt, sid).runtime_recovery['context'])
    reopen(rt)
    rt.clock.now += 31
    assert recovery.claim_context_recovery(rt.store.db, sid, owner='restored', reconcile_abandoned=True) is None
    after = saved(rt, sid)
    assert after.status == 'paused'
    assert after.runtime_recovery['context']['attempts'] == before['attempts'] == 1
    assert after.runtime_recovery['context']['receipts'] == before['receipts']
    assert after.runtime_settlement['artifact_status'] == 'partial_checkpoint'
    assert after.runtime_settlement['delivery_status'] == 'unconfirmed'


def test_H62_hard_cap_rejects_model_tool_and_recovery_then_allows_local_partial(native):
    rt = native
    state = saved(rt, 'alpha')
    rt.clock.now = runtime.runtime_wall_deadline(state) + 1
    with pytest.raises(completion.NativeAdmissionDenied):
        completion.admit_model_request(rt.agents['alpha'], 'too-late')
    blocked, _ = completion.admit_tool_request(rt.agents['alpha'], 'saved_report', {}, 'too-late')
    assert json.loads(blocked)['tool_not_executed']
    claim = completion.claim_recovery(rt.store.db, 'alpha', 'checkpoint',
        expected_identity=runtime.runtime_identity(state), deadline=rt.clock.now + 600)
    assert not claim['allowed']
    receipt = completion.settle_native(rt.store.db, 'alpha', expected_identity=runtime.runtime_identity(state),
                                      reason='deadline', pause=True)
    assert receipt['artifact_status'] == 'partial_checkpoint'
    assert receipt['delivery_status'] == 'unconfirmed'
    assert saved(rt, 'alpha').status == 'paused'


def test_H58_native_artifact_receipt_never_promotes_unconfirmed_delivery(native, tmp_path):
    rt = native
    path = tmp_path / 'report.html'
    path.write_text('Synthetic report')
    work = rt.work['alpha']
    work.update(artifact_status='final_ready_with_limitations', artifacts=[
        {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}])
    state = saved(rt, 'alpha')
    for _ in range(2):
        receipt = completion.settle_native(rt.store.db, 'alpha', expected_identity=runtime.runtime_identity(state),
                                          reason='local provided')
        assert receipt['artifact_status'] == 'final_ready_with_limitations'
        assert receipt['delivery_status'] == 'unconfirmed'
        reopen(rt)
    path.write_text('Changed artifact')
    receipt = completion.settle_native(rt.store.db, 'alpha', expected_identity=runtime.runtime_identity(state),
                                      reason='recheck')
    assert receipt['artifact_status'] == 'draft_preserved'
    assert receipt['delivery_status'] == 'unconfirmed'


@pytest.mark.parametrize('work_status,artifact_status,blockers,complete', [
    ('known', 'draft_preserved', [], False),
    ('known', 'partial_checkpoint', [], False),
    ('unknown', 'final_ready', [], False),
    ('known', 'final_ready', ['required_format_docx'], False),
    ('known', 'final_ready', ['required_layout_verification'], False),
    ('known', 'final_ready', [], True),
    ('known', 'final_ready_with_limitations', [], True),
])
def test_H59_native_done_rechecks_pinned_completion_contract_after_legacy_ready(
        native, monkeypatch, tmp_path, work_status, artifact_status, blockers, complete):
    rt = native
    sid = 'alpha'
    contract = {'required_formats': ['html', 'docx'], 'layout_required': True}
    state = saved(rt, sid)
    state.runtime_policy = completion.make_policy(86400., contract)
    rt.store.db.set_meta('goal:' + sid, state.to_json())
    original_rows = rt.store.db.get_messages(sid)
    original_identity = runtime.runtime_identity(state)
    work = rt.work[sid]
    path = tmp_path / 'selected.html'
    path.write_text('Synthetic selected artifact')
    work.update(status=work_status, artifact_status=artifact_status, blockers=blockers,
                artifacts=[{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}],
                next_action={'tool': 'synthetic_report', 'arguments': {'action': 'complete_required_artifacts'}})
    projection_contracts = []
    legacy_phases = []
    callback = lambda **kw: None
    monkeypatch.setattr(plugins, 'has_hook', lambda name:
                        name in {'goal_evaluation_context', 'goal_work_projection'})
    monkeypatch.setattr(plugins, 'iter_hook_callbacks', lambda name: [callback])
    def hook(name, **kw):
        if name == 'goal_evaluation_context':
            legacy_phases.append(kw['phase'])
            return [{'provider': 'synthetic-domain', 'status': 'ready', 'revision': 'unchanged',
                     'context': 'Legacy domain selection and review are ready'}]
        projection_contracts.append(deepcopy(kw['completion_contract']))
        return [work]
    monkeypatch.setattr(plugins, 'invoke_hook', hook)
    monkeypatch.setattr(goals, 'judge_goal', lambda *a, **kw:
                        ('done', 'Synthetic model believes the report complete', False, None, False))
    decision = goals.GoalManager(sid).evaluate_after_turn('Synthetic completed prose')
    current = saved(rt, sid)
    assert legacy_phases == ['prepare', 'validate']
    assert runtime.runtime_identity(current) == original_identity
    assert rt.store.db.get_messages(sid) == original_rows
    assert current.runtime_budget_seconds == 86400.
    assert current.max_turns == 32 and current.turns_used == 1
    if complete:
        assert current.status == 'done' and not decision['should_continue']
    else:
        assert current.status == 'active' and decision['should_continue']
        assert decision['continuation_prompt']
        assert current.last_verdict != 'done'
    assert projection_contracts and all(value == contract for value in projection_contracts)
