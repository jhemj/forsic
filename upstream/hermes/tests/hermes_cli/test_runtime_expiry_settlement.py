"""Actorless expiry and unavailable projection retain model-free native receipts."""
import hashlib
from types import SimpleNamespace as NS
import pytest
from hermes_cli import goals, goals_runtime as runtime, goals_completion as completion, plugins
from hermes_state import SessionDB

@pytest.fixture
def rt(tmp_path, monkeypatch):
    clock=NS(now=10000.)
    for module in (goals,runtime,completion):
        monkeypatch.setattr(module,'time',NS(time=lambda:clock.now))
    store=NS(path=tmp_path/'state.db',db=SessionDB(tmp_path/'state.db'))
    store.db.create_session('synthetic-expiry','test')
    monkeypatch.setattr(goals,'_get_session_db',lambda:store.db)
    monkeypatch.setattr(goals,'configured_runtime_budget',lambda:100.)
    manager=goals.GoalManager('synthetic-expiry')
    state=manager.set('Synthetic incomplete report',max_turns=32)
    state.runtime_policy=completion.make_policy(100.,{'required_formats':['html'],'layout_required':False})
    store.db.set_meta('goal:synthetic-expiry',state.to_json())
    store.db.append_message('synthetic-expiry','tool','Immutable synthetic observation',message_uid='evidence-uid')
    path=tmp_path/'draft.html';path.write_text('<p>Preserved synthetic finding</p>')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    work={'status':'known','identity':{'case':'synthetic'},'basis':{'revision':1},'digest':'basis-1',
          'artifact_status':'draft_preserved','delivery_status':'unconfirmed',
          'artifacts':[{'path':str(path),'sha256':digest}],'blockers':['review_incomplete']}
    calls=[]
    def hook(name,**kw):
        assert name=='goal_work_projection'
        calls.append(name)
        return [work]
    monkeypatch.setattr(plugins,'has_hook',lambda name:name=='goal_work_projection')
    monkeypatch.setattr(plugins,'invoke_hook',hook)
    def forbidden(*a,**kw):raise AssertionError('No model request belongs in local expiry settlement')
    monkeypatch.setattr(goals,'judge_goal',forbidden)
    yield NS(clock=clock,store=store,manager=manager,state=state,path=path,digest=digest,work=work,calls=calls)
    store.db.close()

def saved(rt):
    return goals.GoalState.from_json(rt.store.db.get_meta('goal:synthetic-expiry'))

@pytest.mark.parametrize('restore',[False,True])
def test_actorless_expired_wait_must_write_model_free_native_draft_receipt(rt,restore):
    rt.manager.wait_for_seconds(20,'bounded wait without executor')
    if restore:
        rt.store.db.close();rt.store.db=SessionDB(rt.store.path)
    rows=rt.store.db.get_messages('synthetic-expiry')
    rt.clock.now+=101
    state,expired=runtime.enforce_goal_runtime(rt.store.db,'synthetic-expiry',expected_goal_id=runtime.runtime_identity(rt.state))
    assert expired and state.status=='paused'
    current=saved(rt)
    assert current.runtime_budget_seconds==100. and current.turns_used==0
    assert rt.store.db.get_messages('synthetic-expiry')==rows
    assert hashlib.sha256(rt.path.read_bytes()).hexdigest()==rt.digest
    assert current.runtime_settlement, 'expiry paused actorless goal without native settlement'
    assert current.runtime_settlement['artifact_status']=='draft_preserved'
    assert current.runtime_settlement['artifact_paths'][0]['sha256']==rt.digest
    assert current.runtime_settlement['delivery_status']=='unconfirmed'


def test_projection_unavailable_must_not_erase_existing_verified_draft_receipt(rt,monkeypatch):
    receipt=completion.settle_native(rt.store.db,'synthetic-expiry',expected_identity=runtime.runtime_identity(rt.state),reason='checkpoint')
    assert receipt['artifact_paths'] and receipt['artifact_status']=='draft_preserved'
    old_paths=receipt['artifact_paths']
    monkeypatch.setattr(plugins,'has_hook',lambda name:False)
    rt.clock.now+=101
    receipt=completion.settle_native(rt.store.db,'synthetic-expiry',expected_identity=runtime.runtime_identity(rt.state),reason='deadline',pause=True)
    assert saved(rt).status=='paused'
    assert hashlib.sha256(rt.path.read_bytes()).hexdigest()==rt.digest
    assert receipt['artifact_paths']==old_paths, 'valid on-disk artifact disappeared only because projection was unavailable'
    assert receipt['artifact_status']=='draft_preserved'
    assert receipt['delivery_status']=='unconfirmed'

@pytest.mark.parametrize('control',['pause','replacement'])
def test_concurrent_manual_control_wins_expiry_CAS_without_settlement(rt,monkeypatch,control):
    original=rt.store.db.compare_and_swap_meta
    def win(key,old,new):
        state=goals.GoalState.from_json(old)
        if control=='pause':
            state.status='paused';state.paused_reason='explicit user pause';state.pause_requested=True
        else:
            state.goal='New explicit goal';state.created_at+=1
        rt.store.db.set_meta(key,state.to_json())
        return original(key,old,new)
    monkeypatch.setattr(rt.store.db,'compare_and_swap_meta',win)
    rt.clock.now+=101
    state,expired=runtime.enforce_goal_runtime(rt.store.db,'synthetic-expiry',expected_goal_id=runtime.runtime_identity(rt.state))
    assert not expired
    assert not state.runtime_settlement
    assert not rt.calls
    if control=='pause':assert state.paused_reason=='explicit user pause'
    else:assert state.goal=='New explicit goal'


@pytest.mark.parametrize('prior_status',['draft_preserved','final_ready','final_ready_with_limitations'])
@pytest.mark.parametrize('file_state',['valid','changed','missing'])
def test_prior_artifact_revalidation_never_borrows_final_or_delivery(rt,monkeypatch,prior_status,file_state):
    rt.work.update(artifact_status=prior_status,delivery_status='confirmed')
    previous=completion.settle_native(rt.store.db,'synthetic-expiry',expected_identity=runtime.runtime_identity(rt.state),reason='saved')
    assert previous['artifact_paths']
    if file_state=='changed':rt.path.write_text('Different bytes')
    elif file_state=='missing':rt.path.unlink()
    monkeypatch.setattr(plugins,'has_hook',lambda name:False)
    receipt=completion.settle_native(rt.store.db,'synthetic-expiry',expected_identity=runtime.runtime_identity(rt.state),reason='reader unavailable')
    assert receipt['delivery_status']=='unconfirmed'
    assert receipt['preservation_origin']=='previous_native_settlement'
    if file_state=='valid':
        assert receipt['artifact_status']=='draft_preserved'
        assert receipt['artifact_paths']==previous['artifact_paths']
    else:
        assert receipt['artifact_status']=='partial_checkpoint'
        assert receipt['artifact_paths']==[] and receipt['artifact_error']


def test_previous_foreign_goal_receipt_is_not_adopted_and_current_projection_wins(rt,monkeypatch):
    receipt=completion.settle_native(rt.store.db,'synthetic-expiry',expected_identity=runtime.runtime_identity(rt.state),reason='saved')
    state=saved(rt);state.runtime_settlement['goal_identity']=[999.,'Different goal']
    rt.store.db.set_meta('goal:synthetic-expiry',state.to_json())
    monkeypatch.setattr(plugins,'has_hook',lambda name:False)
    current=completion.settle_native(rt.store.db,'synthetic-expiry',expected_identity=runtime.runtime_identity(rt.state),reason='missing reader')
    assert not current['artifact_paths']
    # A present current projection can legitimately have no retained artifact.
    current=completion.settle_native(rt.store.db,'synthetic-expiry',expected_identity=runtime.runtime_identity(rt.state),reason='current read',work={'status':'known','artifact_status':'partial_checkpoint','artifacts':[]})
    assert current['artifact_status']=='partial_checkpoint' and not current['artifact_paths']


@pytest.mark.parametrize('failure',['exception','cas_false'])
def test_expiry_settlement_failure_is_explicit_and_prior_metadata_survives(rt,monkeypatch,failure):
    previous=completion.settle_native(rt.store.db,'synthetic-expiry',expected_identity=runtime.runtime_identity(rt.state),reason='saved')
    if failure=='exception':
        def unavailable(*a,**kw):raise OSError('Synthetic persistence failure')
        monkeypatch.setattr(completion,'settle_native',unavailable)
    else:
        monkeypatch.setattr(completion,'settle_native',lambda *a,**kw:{'status':'settlement_failed','reason':'state_conflict'})
    rt.clock.now+=101
    state,expired=runtime.enforce_goal_runtime(rt.store.db,'synthetic-expiry')
    assert expired and state.status=='paused'
    receipt=saved(rt).runtime_settlement
    assert receipt['status']=='settlement_failed'
    assert receipt['artifact_paths']==previous['artifact_paths']
    assert saved(rt).turns_used==0 and saved(rt).runtime_budget_seconds==100.


def test_projection_exception_still_preserves_verified_prior_draft(rt,monkeypatch):
    previous=completion.settle_native(rt.store.db,'synthetic-expiry',expected_identity=runtime.runtime_identity(rt.state),reason='saved')
    def unavailable(*a,**kw):raise OSError('Synthetic reader failure')
    monkeypatch.setattr(completion,'read_work_projection',unavailable)
    receipt=completion.settle_native(rt.store.db,'synthetic-expiry',expected_identity=runtime.runtime_identity(rt.state),reason='reader failed')
    assert receipt['artifact_status']=='draft_preserved'
    assert receipt['artifact_paths']==previous['artifact_paths']


@pytest.mark.parametrize('control',['pause','replacement'])
def test_control_after_expiry_CAS_still_wins(rt,monkeypatch,control):
    real=completion.settle_native
    def concurrent(db,sid,**kw):
        if control=='pause':
            current=saved(rt);current.paused_reason='explicit user pause after expiry';current.pause_requested=True
            rt.store.db.set_meta('goal:synthetic-expiry',current.to_json())
        else:
            current=saved(rt);current.goal='Replacement after expiry';current.created_at+=1
            rt.store.db.set_meta('goal:synthetic-expiry',current.to_json())
        return real(db,sid,**kw)
    monkeypatch.setattr(completion,'settle_native',concurrent)
    rt.clock.now+=101
    state,expired=runtime.enforce_goal_runtime(rt.store.db,'synthetic-expiry')
    assert not expired
    if control=='pause':assert state.paused_reason=='explicit user pause after expiry'
    else:assert state.goal=='Replacement after expiry' and not state.runtime_settlement


def test_duplicate_expiry_never_repeats_settlement_or_resets_budget(rt):
    rt.clock.now+=101
    state,expired=runtime.enforce_goal_runtime(rt.store.db,'synthetic-expiry')
    assert expired and state.runtime_settlement
    raw=rt.store.db.get_meta('goal:synthetic-expiry');calls=list(rt.calls)
    restored,expired=runtime.enforce_goal_runtime(rt.store.db,'synthetic-expiry')
    assert not expired
    assert rt.store.db.get_meta('goal:synthetic-expiry')==raw and rt.calls==calls


@pytest.mark.parametrize('store_failure',['cas_false','cas_error','read_error'])
def test_unpersistable_failure_is_returned_honestly_without_erasing_saved_paths(rt,monkeypatch,store_failure):
    previous=completion.settle_native(rt.store.db,'synthetic-expiry',expected_identity=runtime.runtime_identity(rt.state),reason='saved')
    original_cas=rt.store.db.compare_and_swap_meta
    original_get=rt.store.db.get_meta
    wrote_expiry=[]
    def cas(key,old,new):
        if not wrote_expiry:
            wrote_expiry.append(True)
            return original_cas(key,old,new)
        if store_failure=='cas_false':return False
        raise OSError('Synthetic unavailable write')
    def get(key):
        if wrote_expiry and store_failure=='read_error':raise OSError('Synthetic unavailable read')
        return original_get(key)
    def unavailable(*a,**kw):raise OSError('Synthetic settlement failure')
    monkeypatch.setattr(completion,'settle_native',unavailable)
    monkeypatch.setattr(rt.store.db,'compare_and_swap_meta',cas)
    monkeypatch.setattr(rt.store.db,'get_meta',get)
    rt.clock.now+=101
    state,expired=runtime.enforce_goal_runtime(rt.store.db,'synthetic-expiry')
    assert expired and state.status=='paused'
    assert state.runtime_settlement['status']=='settlement_failed'
    assert state.runtime_settlement['persistence']=='unconfirmed'
    assert state.runtime_settlement['artifact_paths']==previous['artifact_paths']
    actual=goals.GoalState.from_json(original_get('goal:synthetic-expiry'))
    assert actual.status=='paused' and actual.runtime_settlement['artifact_paths']==previous['artifact_paths']


def test_successful_expiry_settlement_replaces_only_an_old_failure_marker(rt):
    completion.settle_native(rt.store.db,'synthetic-expiry',expected_identity=runtime.runtime_identity(rt.state),reason='saved')
    state=saved(rt);state.runtime_settlement.update(status='settlement_failed',persistence='unconfirmed')
    rt.store.db.set_meta('goal:synthetic-expiry',state.to_json())
    rt.clock.now+=101
    state,expired=runtime.enforce_goal_runtime(rt.store.db,'synthetic-expiry')
    assert expired and state.runtime_settlement['artifact_status']=='draft_preserved'
    assert state.runtime_settlement.get('status')!='settlement_failed'
    assert 'persistence' not in state.runtime_settlement
