"""Real elapsed-time auxiliary evaluation, durable native settlement, control races."""
import hashlib
import threading
import time
from types import SimpleNamespace as NS
import httpx
import pytest
from openai import OpenAI
from hermes_cli import goals, goals_runtime as runtime, goals_completion as completion
from hermes_state import SessionDB
from agent import auxiliary_client as aux

REQUEST={"model":"offline","messages":[{"role":"user","content":"Synthetic goal judge"}]}
COMPLETE={"id":"synthetic","object":"chat.completion","created":0,"model":"offline",
          "choices":[{"index":0,"message":{"role":"assistant","content":"CONTINUE"},"finish_reason":"stop"}]}

@pytest.fixture
def setup(tmp_path,monkeypatch):
    db=SessionDB(tmp_path/'state.db');sid='synthetic-native-evaluation';db.create_session(sid,'test')
    monkeypatch.setattr(goals,'_get_session_db',lambda:db)
    monkeypatch.setattr(goals,'configured_runtime_budget',lambda:3600.)
    manager=goals.GoalManager(sid);state=manager.set('Synthetic report',max_turns=8)
    state.runtime_policy=completion.make_policy(3600.,{'required_formats':['html']})
    path=tmp_path/'draft.html';path.write_text('<p>Saved synthetic finding</p>')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(completion,'read_work_projection',lambda *a,**kw:{'status':'known','digest':'basis-1',
        'artifact_status':'draft_preserved','delivery_status':'unconfirmed','blockers':['independent_review'],
        'artifacts':[{'path':str(path),'sha256':digest}]})
    yield NS(db=db,sid=sid,manager=manager,state=state,path=path,digest=digest)
    db.close()


def write(setup, mode='strict', remaining=900.15):
    s=setup.state;s.runtime_policy['ready_target_mode']=mode
    s.runtime_started_at=time.time();s.runtime_elapsed_seconds=3600.-remaining
    setup.db.set_meta('goal:'+setup.sid,s.to_json())


def saved(setup):return goals.GoalState.from_json(setup.db.get_meta('goal:'+setup.sid))


def install_sdk_judge(monkeypatch, response_delay=.4, on_request=None):
    wires=[];warming=True;release=threading.Event()
    def handle(request):
        if not warming:
            wires.append(time.time())
            if on_request:on_request()
            release.wait(response_delay)
        return httpx.Response(200,json=COMPLETE)
    client=OpenAI(api_key='synthetic',base_url='https://synthetic.invalid/v1',
                  http_client=httpx.Client(transport=httpx.MockTransport(handle)),max_retries=0)
    client.chat.completions.create(**REQUEST);warming=False
    def evaluate(worker,*a,**kw):
        aux._relay_sync_completion(client,REQUEST)
        worker._assert_evaluation_current()
        return goals._decision('active',True,'continue','continue','Synthetic work remains','')
    monkeypatch.setattr(goals.GoalManager,'_evaluate_after_turn',evaluate)
    return client,wires,release


@pytest.mark.parametrize('mode,remaining',[('strict',900.15),('soft',.15)])
def test_auxiliary_call_crossing_native_boundary_settles_without_another_model(setup,monkeypatch,mode,remaining):
    client,wires,release=install_sdk_judge(monkeypatch)
    write(setup,mode,remaining);start=time.time()
    try:
        result=setup.manager.evaluate_after_turn('Synthetic current report')
        assert .10 < time.time()-start < .55
        assert result['status']=='paused' and not result['should_continue']
        assert result['verdict']=='runtime_settlement'
        state=saved(setup)
        assert state.paused_reason==('report_target_settlement' if mode=='strict' else runtime.RUNTIME_BUDGET_REASON)
        assert state.runtime_settlement['artifact_status']=='draft_preserved'
        assert state.runtime_settlement['artifact_paths'][0]['sha256']==setup.digest
        assert state.runtime_budget_seconds==3600. and state.max_turns==8
        assert len(wires)==1 and not client.is_closed()
    finally:release.set();time.sleep(.02);client.close()


def test_soft_ready_target_does_not_cancel_healthy_evaluation(setup,monkeypatch):
    client,wires,release=install_sdk_judge(monkeypatch,response_delay=.2)
    write(setup,'soft',900.05)
    try:
        result=setup.manager.evaluate_after_turn('Synthetic current report')
        assert result['status']=='active' and result['should_continue']
        assert not saved(setup).runtime_settlement and len(wires)==1
    finally:release.set();client.close()


@pytest.mark.parametrize('control',['manual_pause','replacement','clear'])
def test_manual_control_during_auxiliary_call_wins_without_receipt_or_new_turn(setup,monkeypatch,control):
    def win():
        state=saved(setup)
        if control=='manual_pause':state.status='paused';state.pause_requested=True;state.paused_reason='User stop'
        elif control=='replacement':state.goal='Replacement goal';state.created_at+=1
        else:
            setup.db.set_meta('goal:'+setup.sid,'');return
        setup.db.set_meta('goal:'+setup.sid,state.to_json())
    client,wires,release=install_sdk_judge(monkeypatch,on_request=win)
    write(setup,'strict',900.15)
    try:
        result=setup.manager.evaluate_after_turn('Synthetic report')
        assert not result['should_continue'] and result['verdict']=='stale_evaluation'
        raw=setup.db.get_meta('goal:'+setup.sid)
        if control=='clear':assert not raw
        else:
            state=goals.GoalState.from_json(raw)
            assert not state.runtime_settlement
            if control=='manual_pause':assert state.paused_reason=='User stop'
            else:assert state.goal=='Replacement goal'
        assert len(wires)==1
    finally:release.set();time.sleep(.02);client.close()


@pytest.mark.parametrize('control',['manual_pause','replacement'])
def test_control_immediately_after_settlement_is_not_adopted_as_original_result(setup,monkeypatch,control):
    real=completion.settle_native
    def settle_then_control(db,sid,**kw):
        receipt=real(db,sid,**kw)
        state=saved(setup)
        if control=='manual_pause':state.pause_requested=True;state.paused_reason='User stop after settlement'
        else:state.goal='New goal after settlement';state.created_at+=1;state.status='active'
        db.set_meta('goal:'+sid,state.to_json())
        return receipt
    monkeypatch.setattr(completion,'settle_native',settle_then_control)
    write(setup,'strict',899.)
    result=setup.manager.evaluate_after_turn('Synthetic report')
    assert result['verdict']=='stale_evaluation' and not result['should_continue']
    if control=='manual_pause':assert saved(setup).paused_reason=='User stop after settlement'
    else:assert saved(setup).goal=='New goal after settlement'


@pytest.mark.parametrize('failure',['exception','same_goal_CAS_conflict','storage_unavailable'])
def test_settlement_failure_has_one_same_goal_stop_marker_or_explicit_unconfirmed(setup,monkeypatch,failure):
    write(setup,'strict',899.)
    before=saved(setup);before.runtime_settlement={'artifact_paths':[{'path':str(setup.path),'sha256':setup.digest}],
                                                 'artifact_status':'draft_preserved'}
    setup.db.set_meta('goal:'+setup.sid,before.to_json())
    real=setup.db.compare_and_swap_meta;calls=[]
    def cas(key,old,new):
        calls.append(key)
        if failure=='storage_unavailable':return False
        if failure=='same_goal_CAS_conflict' and len(calls)==1:
            current=goals.GoalState.from_json(old);current.last_reason='Concurrent same-goal metadata'
            setup.db.set_meta(key,current.to_json());return False
        return real(key,old,new)
    monkeypatch.setattr(setup.db,'compare_and_swap_meta',cas)
    if failure=='exception':
        def unavailable(*a,**kw):raise OSError('Synthetic reader failure')
        monkeypatch.setattr(completion,'settle_native',unavailable)
    result=setup.manager.evaluate_after_turn('Synthetic report')
    assert not result['should_continue'] and result['verdict']=='settlement_failed'
    state=saved(setup)
    assert state.runtime_settlement['artifact_paths'][0]['sha256']==setup.digest
    if failure=='storage_unavailable':
        assert state.status=='active' and result['reason']=='persistence_unconfirmed'
    else:
        assert state.status=='paused' and state.runtime_settlement['status']=='settlement_failed'
    assert len(calls)==(1 if failure=='exception' else 2)


@pytest.mark.parametrize('control',['manual_pause','replacement'])
def test_user_control_wins_failed_settlement_marker_CAS(setup,monkeypatch,control):
    write(setup,'strict',899.)
    monkeypatch.setattr(completion,'settle_native',lambda *a,**kw:{'status':'settlement_failed','reason':'state_conflict'})
    real=setup.db.compare_and_swap_meta
    def cas(key,old,new):
        current=goals.GoalState.from_json(old)
        if control=='manual_pause':current.status='paused';current.pause_requested=True;current.paused_reason='User stop'
        else:current.goal='Replacement during failure';current.created_at+=1
        setup.db.set_meta(key,current.to_json());return real(key,old,new)
    monkeypatch.setattr(setup.db,'compare_and_swap_meta',cas)
    result=setup.manager.evaluate_after_turn('Synthetic report')
    assert not result['should_continue'] and result['verdict']=='stale_evaluation'
    current=saved(setup)
    assert not current.runtime_settlement
    if control=='manual_pause':assert current.paused_reason=='User stop'
    else:assert current.goal=='Replacement during failure'


@pytest.mark.parametrize('control',['same_goal','manual_pause','replacement'])
def test_expired_request_typed_failure_uses_existing_gateway_pause_boundary(setup,monkeypatch,control):
    from tui_gateway import prompt_turn
    from agent.turn_failure_copy import stamp_failure
    from agent.turn_outcome import turn_outcome
    write(setup,'soft',3500.)
    identity=goals.goal_identity(saved(setup))
    result={'failed':True,'interrupted':False,'completed':False,'turn_exit_reason':'native_operation_deadline_expired',
            'error':'Accepted model request deadline expired; saved work preserved.'}
    stamp_failure(result,'native_operation_deadline',False)
    if control=='manual_pause':setup.manager.pause(reason='Explicit user pause')
    elif control=='replacement':setup.manager.set('Replacement after request')
    assert turn_outcome(result).kind=='terminal_error'
    prompt_turn._pause_failed_turn_goal({'session_key':setup.sid},identity,result)
    state=saved(setup)
    if control=='same_goal':
        assert state.status=='paused' and 'native_operation_deadline' in state.paused_reason
        assert state.turns_used==0 and state.max_turns==8
    elif control=='manual_pause':assert state.paused_reason=='Explicit user pause'
    else:assert state.status=='active' and state.goal=='Replacement after request'
