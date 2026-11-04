"""H28-H36,H51,H55,H62-H63 exercise native CAS and actual dispatch guards."""
import hashlib
import json
import random
from types import SimpleNamespace as NS

import pytest

from hermes_cli import goals, goals_runtime as runtime, goals_completion as completion, plugins
from hermes_state import SessionDB
from agent.deadline import native_operation_scope, native_operation_deadline


@pytest.fixture
def native(tmp_path, monkeypatch):
    now=NS(value=100000.)
    fake=NS(time=lambda:now.value)
    for module in (goals,runtime,completion):monkeypatch.setattr(module,'time',fake)
    db=SessionDB(tmp_path/'state.db');db.create_session('case-a','test');db.create_session('case-b','test')
    monkeypatch.setattr(goals,'_get_session_db',lambda:db)
    monkeypatch.setattr(goals,'configured_runtime_budget',lambda:86400.)
    manager=goals.GoalManager('case-a');state=manager.set('Synthetic report')
    state.runtime_policy=completion.make_policy(86400.,{'required_formats':['html']});db.set_meta('goal:case-a',state.to_json())
    work={'status':'known','identity':{'case':'synthetic'},'basis':{'revision':1},'digest':'basis-1',
          'blockers':['gap-a'],'estimate':{'known':False},'quota':{}}
    def hook(name,**kw):
        return [{'purpose':'report','max_seconds':30.}] if kw.get('mode')=='classify_action' and kw['action']['tool']=='synthetic_report' else [{'purpose':'discovery'}] if kw.get('mode')=='classify_action' else [work]
    monkeypatch.setattr(plugins,'has_hook',lambda _:True);monkeypatch.setattr(plugins,'invoke_hook',hook)
    agent=NS(_session_db=db,session_id='case-a',_runtime_turn_identity=runtime.runtime_identity(state),interrupt=lambda *a,**k:None)
    yield now,db,manager,agent,work
    db.close()


def test_H28_H30_closing_admits_real_report_action_but_not_self_labelled_shell(native):
    now,db,manager,agent,work=native
    state=goals.load_goal('case-a');state.runtime_elapsed_seconds=21.5*3600;db.set_meta('goal:case-a',state.to_json())
    assert not completion.admit_action(agent,purpose='discovery',max_seconds=80*60)['allowed']
    assert completion.admit_action(agent,purpose='report',max_seconds=30)['allowed']
    envelope={'purpose':'targeted_verification','blocker':'gap-a','scope':{'targets':['/small']},
              'max_seconds':10,'max_output_bytes':1024,'expected_result':'discriminate alternatives'}
    blocked,args=completion.admit_tool_request(agent,'terminal',{'command':'find / -type f','_native_action':envelope},'tool-1')
    assert blocked and json.loads(blocked)['error']=='native_action_scope_invalid'
    assert '_native_action' not in args
    blocked,_=completion.admit_tool_request(agent,'synthetic_report',{'action':'render'},'tool-2')
    assert blocked is None


@pytest.mark.parametrize('replacement',['legacy','v2','cleared','paused'])
def test_H07_H54_new_request_cannot_borrow_replacement_goal_authority(native,replacement):
    now,db,manager,agent,work=native
    completion.admit_model_request(agent,'old-request')
    if replacement=='paused':manager.pause('user stop')
    elif replacement=='cleared':manager.clear()
    else:
        now.value+=1;state=manager.set('New requested scope')
        if replacement=='v2':state.runtime_policy=completion.make_policy(86400.);db.set_meta('goal:case-a',state.to_json())
    with pytest.raises(completion.NativeAdmissionDenied):completion.admit_model_request(agent,'new-request')
    blocked,_=completion.admit_tool_request(agent,'synthetic_report',{},'new-tool')
    assert blocked


def test_H07_H39_retry_is_fenced_to_original_work_and_deadline(native):
    now,db,manager,agent,work=native
    completion.admit_model_request(agent,'same-request');original=agent._native_operation_deadline
    now.value+=10;completion.admit_model_request(agent,'same-request')
    assert agent._native_operation_deadline<=original
    work['digest']='new-basis'
    with pytest.raises(completion.NativeAdmissionDenied,match='basis'):completion.admit_model_request(agent,'same-request')


def test_H30_tool_revalidates_saved_verification_after_blocker_changes(native, monkeypatch):
    now, db, manager, agent, work = native
    state = goals.load_goal('case-a');state.runtime_elapsed_seconds = 21.5*3600
    db.set_meta('goal:case-a', state.to_json())
    args = {'action':'static_scan', 'paths':['/small']}
    binding = {'scope':{'targets':['/small']}, 'blocker':'gap-a', 'max_seconds':10.,
               'max_output_bytes':1024,
               'arguments_sha256':hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    action = {'purpose':'targeted_verification', 'verification_binding':binding}
    monkeypatch.setattr(plugins, 'invoke_hook', lambda name, **kw:
        [action] if kw.get('mode') == 'classify_action' else [work])
    assert completion.admit_tool_request(agent, 'forsic_shell', args, 'scan')[0] is None
    # Another question can keep the same broad blocker while this saved mission
    # has become stale. A previous grant cannot override fresh classification.
    action = {'purpose':'discovery'}
    blocked, _ = completion.admit_tool_request(agent, 'forsic_shell', args, 'scan')
    assert json.loads(blocked)['tool_not_executed']


def test_H39_tool_retry_is_fenced_to_current_work_basis(native):
    now, db, manager, agent, work = native
    assert completion.admit_tool_request(agent, 'synthetic_report', {}, 'render')[0] is None
    work['digest'] = 'changed-current-judgment'
    blocked, _ = completion.admit_tool_request(agent, 'synthetic_report', {}, 'render')
    assert json.loads(blocked)['error'] == 'accepted_work_basis_changed'


def test_H31_H51_recovery_attempts_and_deadline_do_not_reset_after_restore(native):
    now,db,manager,agent,work=native;identity=agent._runtime_turn_identity
    first=completion.claim_recovery(db,'case-a','checkpoint',expected_identity=identity,max_attempts=2,deadline=now.value+30,threshold=43200)
    now.value+=5
    restored=goals.GoalManager('case-a')
    second=completion.claim_recovery(db,'case-a','checkpoint',expected_identity=identity,max_attempts=99,deadline=now.value+600,threshold=43200)
    assert second['deadline']==first['deadline'] and second['max_attempts']==2
    assert restored.state.runtime_recovery['checkpoint']['attempts_used']==1
    denied=completion.claim_recovery(db,'case-a','checkpoint',expected_identity=identity,max_attempts=99,deadline=now.value+600,threshold=43200)
    assert not denied['allowed'] and denied['attempts_used']==2


def test_H29_H31_child_scope_is_bounded_and_does_not_leak_to_next_operation(native):
    now,db,manager,agent,work=native
    assert native_operation_deadline() is None
    with native_operation_scope(now.value+5):
        with native_operation_scope(now.value+30):
            assert completion.operation_timeout(agent,600)==5
        now.value+=6
        with pytest.raises(completion.NativeAdmissionDenied):completion.admit_model_request(agent,'expired-child')
    assert native_operation_deadline() is None
    completion.admit_model_request(agent,'independent-next')


def test_H33_H34_unknown_and_infeasible_are_not_free_resources(native):
    now,db,manager,agent,work=native;state=goals.load_goal('case-a')
    unknown=completion.control_projection(state,{'status':'unknown'})
    assert unknown['reserve_seconds']>=state.runtime_budget_seconds*.25
    work['quota']={'ordinary':{'remaining':0,'required_reserved':1}}
    projection=completion.control_projection(state,work)
    assert projection['quota_infeasible'] and projection['phase']=='closing'


def test_H55_H56_settlement_verifies_existing_artifact_and_never_promotes_missing_final(native,tmp_path):
    now,db,manager,agent,work=native
    p=tmp_path/'report.html';p.write_text('Synthetic report')
    work.update(artifact_status='final_ready',delivery_status='unconfirmed',artifacts=[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}])
    saved=completion.settle_native(db,'case-a',expected_identity=agent._runtime_turn_identity,reason='deadline',work=work,pause=True)
    assert saved['artifact_status']=='final_ready' and saved['delivery_status']=='unconfirmed'
    assert goals.load_goal('case-a').status=='paused'
    p.unlink();saved=completion.settle_native(db,'case-a',expected_identity=agent._runtime_turn_identity,reason='deadline',work=work)
    assert saved['artifact_status']=='draft_preserved' and saved.get('artifact_error')


def test_H62_H63_generated_controls_preserve_usage_and_reject_all_late_dispatch(native):
    now,db,manager,agent,work=native;previous=0.;rng=random.Random(63847)
    for step in range(80):
        now.value+=rng.randint(0,120)
        manager=goals.GoalManager('case-a')
        if rng.getrandbits(1):manager.pause('user stop')
        else:manager.resume(reset_budget=False)
        state=goals.load_goal('case-a');elapsed=runtime.runtime_elapsed(state)
        assert elapsed>=previous;previous=elapsed
        if state.status=='paused':
            with pytest.raises(completion.NativeAdmissionDenied):completion.admit_model_request(agent,f'request-{step}')
        assert state.runtime_budget_seconds==86400.
