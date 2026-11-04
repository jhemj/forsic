"""Restart/poller wakeup reaches native admission with a real goal DB and fake provider."""
import contextlib
import logging
from pathlib import Path
import threading
import time
import uuid
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from hermes_cli import goals, goals_recovery as recovery, goals_runtime as runtime
from hermes_state import SessionDB
from tui_gateway import goal_recovery, prompt_turn, session_lifecycle


@pytest.fixture
def rt(tmp_path, monkeypatch):
    clock = NS(now=1000.)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    fake_time = NS(time=lambda: clock.now)
    for module in (goals, recovery, runtime): monkeypatch.setattr(module, "time", fake_time)
    db = SessionDB(tmp_path / "state.db");db.create_session("wake-task", "desktop", cwd=str(tmp_path))
    monkeypatch.setattr(goals, "_get_session_db", lambda: db)
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: 86400.)
    monkeypatch.setattr("hermes_cli.goals_completion.read_work_projection", lambda *a: {"status": "unknown"})
    monkeypatch.setattr("hermes_cli.profiles.profiles_to_serve", lambda **kw: [("default", tmp_path)])
    manager = goals.GoalManager("wake-task");manager.set("Finish synthetic analysis", max_turns=32)
    manager.state.runtime_policy = {};manager._save()
    live = {}; calls = []
    server = NS(Path=Path, contextlib=contextlib, time=fake_time, threading=threading, uuid=uuid,
        logger=logging.getLogger(__name__), _session_turn_admission=session_lifecycle._session_turn_admission,
        _session_home=lambda session: tmp_path, _launch_home=lambda: tmp_path,
        _session_profile_runtime_scope=lambda session,**kw: contextlib.nullcontext(),
        _session_goal_paused=lambda session: bool((s:=goals.load_goal(session["session_key"])) and (s.status!="active" or s.pause_requested)),
        _find_live_session_by_key=lambda key, profile: next(((sid,s) for sid,s in live.items() if s["session_key"]==key), None),
        _stored_session_runtime_overrides=lambda row: {"provider_override": "stored-provider"},
        _ensure_active_session_slot=lambda *a: None,
        _claim_active_session_slot=lambda *a,**kw:(NS(release=lambda:None),None),
        _detached_ws_transport=object(), _start_agent_build=lambda sid,s: s.update(agent=NS(session_id=s["session_key"])),
        _wait_agent=lambda *a,**kw: None)
    goal_recovery.register(server);prompt_turn.register(server)
    server._GOAL_RECOVERY_OWNER="new-process"
    server._load_cfg=lambda: {};server._emit=Mock()
    def record(key, **kw):
        assert kw["resume_runtime_overrides"]["provider_override"] == "stored-provider"
        return {"session_key":key,"history_lock":threading.RLock(),"running":False,
                "agent":None,"profile_home":kw["profile_home"],"source":kw["source"]}
    server._deferred_session_record=record
    server._claim_or_reuse_live=lambda sid,key,record,lease: live.__setitem__(sid,record)
    def fake_provider(rid,sid,session,text,**kw):
        assert kw["respect_goal_pause"] and kw["display_kind"] == "hidden"
        assert server._ack_goal_recovery_start(session,kw["display_metadata"])
        operation=kw["display_metadata"]["goal_recovery"]["claim_id"]
        calls.append(operation)
        db.append_message(session["session_key"], "assistant", "Synthetic result", message_uid=operation)
        return True
    server._run_prompt_submit=fake_provider
    recovery.prepare_context_recovery(db,manager.session_id,expected_scope=goals.goal_identity(manager.state),
        cause="compression_lock_busy",source_turn="first-turn",owner="old-process")
    clock.now += 31
    yield NS(server=server,db=db,clock=clock,manager=manager,live=live,calls=calls,home=tmp_path)
    db.close()


def test_restart_with_no_ui_restores_stored_runtime_and_one_actual_turn_receipt(rt):
    assert not rt.live
    assert rt.server._sweep_goal_recovery()
    assert len(rt.calls)==1 and len(rt.live)==1
    saved=goals.load_goal(rt.manager.session_id)
    assert saved.runtime_recovery["context"]["status"]=="dispatched"
    assert saved.runtime_recovery["context"]["receipts"][-1]["claim_id"]==rt.calls[0]
    assert rt.db.get_messages(rt.manager.session_id)[-1]["message_uid"]==rt.calls[0]
    rt.server._sweep_goal_recovery()
    assert len(rt.calls)==1


def test_restart_honors_user_pause_and_never_constructs_owner(rt):
    rt.manager.pause("user stop")
    rt.server._sweep_goal_recovery()
    assert not rt.live and not rt.calls
    assert goals.load_goal(rt.manager.session_id).paused_reason=="user stop"


def test_explicit_close_is_not_reopened_by_unattended_recovery(rt):
    rt.db.end_session(rt.manager.session_id,"tui_close")
    rt.server._sweep_goal_recovery()
    assert not rt.live and not rt.calls
    assert goals.load_goal(rt.manager.session_id).runtime_recovery["context"]["status"]=="terminal"


def test_missing_transport_runtime_settles_without_active_no_owner(rt):
    rt.server._restore_goal_recovery_owner=lambda *a: None
    rt.server._sweep_goal_recovery()
    saved=goals.load_goal(rt.manager.session_id)
    assert saved.status=="paused" and saved.runtime_settlement["artifact_status"]=="partial_checkpoint"
    assert not rt.calls


def test_restart_at_deadline_has_no_new_provider_call(rt):
    rt.clock.now = goals.load_goal(rt.manager.session_id).runtime_recovery["context"]["original_deadline"] + 1
    rt.server._sweep_goal_recovery()
    assert not rt.calls
    assert goals.load_goal(rt.manager.session_id).status=="paused"


def test_original_failure_text_is_not_judged_and_deferred_stays_active(rt):
    session={"session_key":rt.manager.session_id}
    rt.manager.state.runtime_recovery={};rt.manager._save()
    rt.server._start_goal_recovery=Mock(return_value="test-owner")
    rt.server._pause_failed_turn_goal=Mock(side_effect=AssertionError("defer is not terminal"))
    result={"compression_deferred":True,"failed":False,"completed":False,"error":"temporary defer"}
    raw,status,_=rt.server._turn_outcome(result)
    assert status=="yielded"
    assert rt.server._goal_followup_after_turn("ui",session,result,status,raw,
        expected_goal_identity=goals.goal_identity(rt.manager.state)) is None
    saved=goals.load_goal(rt.manager.session_id)
    assert saved.status=="active"
    assert saved.runtime_recovery["context"]["status"]=="pending"
    assert saved.runtime_recovery["context"]["next_due"]>rt.clock.now
    assert saved.runtime_recovery["context"]["next_owner"]=="native_goal_recovery"


def test_same_scheduler_handle_is_registered_once(rt,monkeypatch):
    fake=NS(cancelled=False)
    schedule=Mock(return_value=fake)
    monkeypatch.setattr("agent.periodic_scheduler.schedule",schedule)
    rt.server._GOAL_RECOVERY_HANDLE=None
    assert rt.server._start_goal_recovery()==rt.server._start_goal_recovery()
    schedule.assert_called_once_with(rt.server._sweep_goal_recovery,5.)


def test_compression_parent_and_child_do_not_both_wake(rt):
    rt.db.create_session("wake-child","desktop",parent_session_id=rt.manager.session_id)
    rt.db.end_session(rt.manager.session_id,"compression")
    assert goals.migrate_goal_to_session(rt.manager.session_id,"wake-child")
    rt.server._sweep_goal_recovery()
    assert len(rt.calls)==1
    assert next(iter(rt.live.values()))["session_key"]=="wake-child"
    assert goals.load_goal(rt.manager.session_id).status=="cleared"

def test_scheduler_start_failure_records_owner_unavailable_not_pending(rt):
    rt.server._start_goal_recovery=Mock(side_effect=RuntimeError("synthetic timer unavailable"))
    result={"compression_deferred":True,"failed":False,"completed":False,"error":"temporary"}
    assert rt.server._register_goal_recovery({"session_key":rt.manager.session_id},result,
        goals.goal_identity(rt.manager.state))=="terminal"
    saved=goals.load_goal(rt.manager.session_id)
    assert saved.status=="paused" and saved.runtime_recovery["context"]["next_owner"] is None

def test_user_pause_after_claim_before_turn_start_wins(rt):
    def paused(rid,sid,session,text,**kw):
        rt.manager.pause("user stop after claim")
        assert not rt.server._ack_goal_recovery_start(session,kw["display_metadata"])
        return False
    rt.server._run_prompt_submit=paused
    rt.server._sweep_goal_recovery()
    saved=goals.load_goal(rt.manager.session_id)
    assert saved.paused_reason=="user stop after claim" and not rt.calls

def test_unavailable_stored_provider_is_not_replaced_by_current_default(rt):
    rt.server._start_agent_build=lambda sid,session: None
    rt.server._wait_agent=lambda *a,**kw: {"error":"stored provider unavailable"}
    rt.server._sweep_goal_recovery()
    saved=goals.load_goal(rt.manager.session_id)
    assert saved.status=="paused" and not rt.calls

def test_foreign_live_executor_prevents_even_expired_context_settlement(rt):
    rt.clock.now = goals.load_goal(rt.manager.session_id).runtime_recovery["context"]["original_deadline"] + 1
    rt.server._ensure_active_session_slot=lambda *a: "another live writer owns this session"
    before=rt.db.get_meta("goal:"+rt.manager.session_id)
    rt.server._sweep_goal_recovery()
    assert rt.db.get_meta("goal:"+rt.manager.session_id)==before and not rt.calls

def test_missing_runtime_does_not_settle_foreign_live_executor(rt):
    rt.server._restore_goal_recovery_owner=lambda *a: None
    rt.server._claim_active_session_slot=lambda *a,**kw:(None,"foreign live owner")
    before=rt.db.get_meta("goal:"+rt.manager.session_id)
    rt.server._sweep_goal_recovery()
    assert rt.db.get_meta("goal:"+rt.manager.session_id)==before

def test_execution_deadline_comes_from_claim_not_client_metadata(rt):
    rt.server._sweep_goal_recovery()
    session=next(iter(rt.live.values()))
    context=goals.load_goal(rt.manager.session_id).runtime_recovery["context"]
    metadata={"goal_recovery":{"claim_id":context["claim_id"],"original_deadline":99999999}}
    assert rt.server._goal_recovery_execution_deadline(session,metadata)==context["original_deadline"]
    rt.manager=goals.GoalManager(rt.manager.session_id);rt.manager.set("Replacement")
    with pytest.raises(InterruptedError): rt.server._goal_recovery_execution_deadline(session,metadata)

def test_idle_dispatched_intent_is_settled_after_native_lease_is_reclaimed(rt):
    rt.server._sweep_goal_recovery()
    session=next(iter(rt.live.values()))
    # Simulate loss of the executor after its dispatched receipt. The normal
    # admission/lease gates above prove no sibling live executor owns this row.
    session["running"]=False
    rt.server._sweep_goal_recovery()
    assert len(rt.calls)==1
    saved=goals.load_goal(rt.manager.session_id)
    assert saved.status=="paused" and saved.paused_reason=="context_recovery_delivery_unknown"

def test_cold_restore_binds_real_profile_scope_a_b_a_without_environment_leak(rt,monkeypatch):
    import os
    from agent.secret_scope import (build_profile_secret_scope,get_secret,set_secret_scope,reset_secret_scope)
    from hermes_constants import (get_hermes_home,set_hermes_home_override,reset_hermes_home_override)
    from tui_gateway import model_switch
    from tui_gateway.method_ctx import rebind
    a=rt.home/"profiles"/"a";b=rt.home/"profiles"/"b"
    for home,value in ((a,"synthetic-a"),(b,"synthetic-b")):
        home.mkdir(parents=True)
        (home/"config.yaml").write_text("terminal:\n  backend: local\n")
        (home/".env").write_text("RECOVERY_SCOPE_API_KEY="+value+"\n")
    names=vars(rt.server)
    names.update(_TurnScopes=prompt_turn._TurnScopes,build_profile_secret_scope=build_profile_secret_scope,
        set_secret_scope=set_secret_scope,reset_secret_scope=reset_secret_scope,
        set_hermes_home_override=set_hermes_home_override,reset_hermes_home_override=reset_hermes_home_override)
    for name in ("_profile_runtime_scope_tokens","_release_profile_runtime_scope_tokens","_session_profile_runtime_scope"):
        names[name]=rebind(getattr(model_switch,name),names)
    seen=[]
    rt.server._find_live_session_by_key=lambda *args: None
    def record(key,**kwargs):
        seen.append((get_hermes_home(),get_secret("RECOVERY_SCOPE_API_KEY")))
        return {"session_key":key,"profile_home":kwargs["profile_home"]}
    rt.server._deferred_session_record=record
    before=dict(os.environ)
    for home in (a,b,a):
        with contextlib.closing(SessionDB(home/"state.db")) as db:
            db.create_session("same-id-in-separate-profiles","desktop",cwd=str(home))
            assert rt.server._restore_goal_recovery_owner(db,"same-id-in-separate-profiles",home)
    assert seen==[(a,"synthetic-a"),(b,"synthetic-b"),(a,"synthetic-a")]
    assert dict(os.environ)==before and get_hermes_home()==rt.home

def test_recovery_registration_failure_never_leaves_active_without_owner(rt):
    rt.manager.state.runtime_recovery={};rt.manager._save()
    rt.server._register_goal_recovery=Mock(side_effect=OSError("synthetic intent write failure"))
    rt.server._hook_failure=Mock()
    session={"session_key":rt.manager.session_id,"profile_home":None,"agent":None}
    result={"completed":False,"failed":False,"compression_deferred":True,"error":"temporary description"}
    raw,status,_=rt.server._turn_outcome(result)
    assert rt.server._goal_followup_after_turn("ui",session,result,status,raw,
        expected_goal_identity=goals.goal_identity(rt.manager.state)) is None
    saved=goals.load_goal(rt.manager.session_id)
    assert saved.status=="paused" and saved.turns_used==0 and not rt.calls

@pytest.mark.parametrize("release",["released","expired","dead"])
def test_existing_lock_rules_preserve_live_owner_and_resume_after_reclaim(rt,monkeypatch,release):
    import hermes_state_compression
    monkeypatch.setattr(hermes_state_compression,"time",rt.server.time)
    dead=NS(value=False)
    monkeypatch.setattr("hermes_state._compression_lock_holder_process_is_dead",lambda holder:dead.value)
    rt.db.append_message(rt.manager.session_id,"tool","Preserved synthetic completed result",message_uid="preserved-result")
    before=rt.db.get_messages(rt.manager.session_id)
    assert rt.db.try_acquire_compression_lock(rt.manager.session_id,"synthetic-existing-owner",ttl_seconds=60)
    rt.server._sweep_goal_recovery()
    assert not rt.calls and rt.db.get_compression_lock_holder(rt.manager.session_id)=="synthetic-existing-owner"
    assert goals.load_goal(rt.manager.session_id).runtime_recovery["context"]["attempts"]==0
    if release=="released":rt.db.release_compression_lock(rt.manager.session_id,"synthetic-existing-owner")
    elif release=="dead":dead.value=True
    rt.clock.now += 61 if release=="expired" else 11
    original=rt.server._run_prompt_submit
    def resumed(*args,**kwargs):
        assert rt.db.try_acquire_compression_lock(rt.manager.session_id,"resumed-compression")
        return original(*args,**kwargs)
    rt.server._run_prompt_submit=resumed
    rt.server._sweep_goal_recovery()
    assert len(rt.calls)==1 and rt.db.get_messages(rt.manager.session_id)[0]==before[0]
