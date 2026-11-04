"""Durable native context recovery against SessionDB; no model calls or alternate store."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from agent.turn_outcome import compression_outcome, turn_outcome
from hermes_cli import goals, goals_recovery as recovery, goals_runtime as runtime
from hermes_state import SessionDB

@pytest.fixture
def rt(tmp_path, monkeypatch):
    clock = NS(now=1000.)
    for module in (goals, recovery, runtime): monkeypatch.setattr(module, "time", NS(time=lambda: clock.now))
    db = SessionDB(tmp_path / "state.db")
    db.create_session("recovery-task", "tui")
    monkeypatch.setattr(goals, "_get_session_db", lambda: db)
    monkeypatch.setattr(goals, "configured_runtime_budget", lambda: 86400.)
    monkeypatch.setattr("hermes_cli.goals_completion.read_work_projection", lambda *a: {"status": "unknown"})
    manager = goals.GoalManager("recovery-task")
    manager.set("Complete the synthetic report", max_turns=32)
    yield NS(db=db, clock=clock, manager=manager, sid=manager.session_id,
             scope=goals.goal_identity(manager.state), home=tmp_path)
    db.close()

def prepare(rt, **kwargs):
    return recovery.prepare_context_recovery(rt.db, rt.sid, expected_scope=rt.scope,
        cause=kwargs.pop("cause", "compression_lock_busy"), source_turn=kwargs.pop("source_turn", "turn-1"),
        owner=kwargs.pop("owner", "owner-a"), **kwargs)

def current(rt): return recovery.read_context(rt.db, rt.sid)

@pytest.mark.parametrize("text", ["Context compression temporarily deferred", "SSE disconnected", "arbitrary error prose"])
def test_legacy_deferred_description_does_not_become_terminal_error(text):
    outcome = turn_outcome({"failed": False, "completed": False, "compression_deferred": True, "error": text})
    assert outcome.kind == "yielded" and outcome.display_status == "yielded"
    assert turn_outcome({"failed": True, "error": text}).kind == "terminal_error"

def test_unknown_lock_storage_is_not_confirmed_busy():
    assert compression_outcome("compression_lock_state_unknown").kind == "terminal_error"
    assert compression_outcome("compression_lock_busy").kind == "yielded"
    assert turn_outcome({"compression_deferred": True, "interrupted": True}).kind == "interrupted"

def test_original_deadline_and_attempt_survive_manager_reload_and_successful_claim(rt):
    assert prepare(rt) == "pending"
    original = current(rt)[2]["original_deadline"]
    rt.clock.now += 11
    claim = recovery.claim_context_recovery(rt.db, rt.sid, owner="owner-a")
    assert claim["attempts"] == 1
    restored = goals.GoalManager(rt.sid).state.runtime_recovery["context"]
    assert restored["original_deadline"] == original
    assert recovery.context_turn_started(rt.db, rt.sid, claim["claim_id"])
    assert current(rt)[2]["receipts"][-1]["claim_id"] == claim["claim_id"]
    assert prepare(rt, source_turn="turn-2") == "pending"
    assert current(rt)[2]["original_deadline"] == original
    assert current(rt)[2]["attempts"] == 1
    assert goals.load_goal(rt.sid).turns_used == 0

def test_restart_pending_without_ui_can_change_owner_after_lease_not_deadline(rt):
    prepare(rt);deadline = current(rt)[2]["original_deadline"]
    rt.clock.now += 11
    assert recovery.claim_context_recovery(rt.db, rt.sid, owner="new-process") is None
    rt.clock.now += 20
    reopened = SessionDB(rt.home / "state.db")
    claim = recovery.claim_context_recovery(reopened, rt.sid, owner="new-process")
    reopened.close()
    assert claim["original_deadline"] == deadline and claim["owner"] == "new-process"

@pytest.mark.parametrize("control", ["pause", "safe_pause", "replace", "clear"])
def test_user_control_and_replacement_win_between_due_and_claim(rt, control):
    prepare(rt); rt.clock.now += 40
    if control == "pause": rt.manager.pause("user stop")
    elif control == "safe_pause": rt.manager.request_pause()
    elif control == "replace": rt.manager.set("A different scope")
    else: rt.manager.clear()
    before = rt.db.get_meta("goal:" + rt.sid)
    assert recovery.claim_context_recovery(rt.db, rt.sid, owner="new-process") is None
    assert rt.db.get_meta("goal:" + rt.sid) == before

def test_goal_replacement_wins_claim_cas(rt, monkeypatch):
    prepare(rt);rt.clock.now += 11
    original = rt.db.compare_and_swap_meta
    def replace(key, expected, value):
        newer = goals.GoalState.from_json(expected); newer.goal = "Replacement"
        rt.db.set_meta(key, newer.to_json())
        return original(key, expected, value)
    monkeypatch.setattr(rt.db, "compare_and_swap_meta", replace)
    assert recovery.claim_context_recovery(rt.db, rt.sid, owner="owner-a") is None
    assert goals.load_goal(rt.sid).goal == "Replacement"

def test_two_owners_cannot_consume_same_intent(rt):
    prepare(rt);rt.clock.now += 40
    assert recovery.claim_context_recovery(rt.db, rt.sid, owner="first")
    assert recovery.claim_context_recovery(rt.db, rt.sid, owner="second") is None
    assert current(rt)[2]["attempts"] == 1

@pytest.mark.parametrize("started", [False, True])
def test_crash_after_claim_never_blindly_replays_unknown_dispatch(rt, started):
    prepare(rt);rt.clock.now += 11
    claim = recovery.claim_context_recovery(rt.db, rt.sid, owner="owner-a")
    if started: assert recovery.context_turn_started(rt.db, rt.sid, claim["claim_id"])
    rt.clock.now = current(rt)[2]["owner_lease_until"] + 1
    assert recovery.claim_context_recovery(rt.db, rt.sid, owner="new-process") is None
    assert current(rt)[1].status == "paused" and current(rt)[2]["status"] == "terminal"
    assert current(rt)[1].runtime_settlement["artifact_status"] == "partial_checkpoint"

def test_due_at_hard_cap_settles_without_new_attempt(rt):
    rt.manager.state.runtime_budget_seconds = 5.;rt.manager._save()
    prepare(rt);rt.clock.now += 5
    assert recovery.claim_context_recovery(rt.db, rt.sid, owner="owner-a") is None
    assert current(rt)[1].status == "paused" and current(rt)[2]["attempts"] == 0

def test_repeated_deferral_has_finite_shared_attempt_budget(rt):
    prepare(rt)
    for number in range(3):
        rt.clock.now += 11
        claim = recovery.claim_context_recovery(rt.db, rt.sid, owner="owner-a")
        assert claim and recovery.context_turn_started(rt.db, rt.sid, claim["claim_id"])
        disposition = prepare(rt, source_turn=f"turn-{number+2}")
    assert disposition == "terminal" and current(rt)[2]["attempts"] == 3

def test_exhaustion_counter_survives_success_and_process_reload(rt):
    prepare(rt, cause="compression_exhausted");rt.clock.now += 11
    claim = recovery.claim_context_recovery(rt.db, rt.sid, owner="owner-a")
    assert recovery.context_turn_started(rt.db, rt.sid, claim["claim_id"])
    assert recovery.settle_context_recovery(rt.db, rt.sid, claim_id=claim["claim_id"])
    rt.manager = goals.GoalManager(rt.sid)
    assert prepare(rt, cause="compression_exhausted", source_turn="second") == "terminal"
    assert current(rt)[2]["exhaustion_attempts"] == 1

def test_scope_update_does_not_renew_existing_episode_deadline(rt):
    prepare(rt);deadline = current(rt)[2]["original_deadline"]
    rt.manager.add_subgoal("Synthetic extra explanation")
    rt.scope = goals.goal_identity(rt.manager.state);rt.clock.now += 20
    assert prepare(rt, source_turn="next") == "pending"
    assert current(rt)[2]["original_deadline"] == deadline

def test_unavailable_storage_never_creates_wait_or_spends_attempt(rt, monkeypatch):
    before = current(rt)[0]
    with monkeypatch.context() as patch:
        patch.setattr(rt.db, "get_meta", Mock(side_effect=OSError("synthetic storage unavailable")))
        with pytest.raises(OSError): prepare(rt)
    assert rt.db.get_meta("goal:" + rt.sid) == before

def test_sse_word_boundary_does_not_misclassify_compression_description():
    from agent.error_surface import _looks_like_stream_drop
    assert not _looks_like_stream_drop("compressed session could not be processed")
    assert _looks_like_stream_drop("SSE connection interrupted")

def test_known_cooldown_is_waited_without_burning_three_early_retries(rt):
    assert prepare(rt, cause="compression_transient_guard", retry_at=rt.clock.now+600) == "pending"
    initial=current(rt)[2]
    assert initial["next_due"]==1600 and initial["original_deadline"]==1900
    rt.clock.now += 500
    assert recovery.claim_context_recovery(rt.db,rt.sid,owner="owner-a") is None
    assert current(rt)[2]["attempts"]==0
    rt.clock.now += 101
    assert recovery.claim_context_recovery(rt.db,rt.sid,owner="owner-a")["attempts"]==1

def test_live_compression_lock_is_wait_without_provider_attempt(rt,monkeypatch):
    prepare(rt);rt.clock.now += 11
    monkeypatch.setattr(rt.db,"get_compression_lock_holder",lambda sid: "confirmed-live-holder")
    assert recovery.claim_context_recovery(rt.db,rt.sid,owner="owner-a") is None
    assert current(rt)[2]["attempts"]==0 and current(rt)[2]["next_due"]==1021

def test_manual_resume_does_not_renew_terminal_episode(rt):
    prepare(rt);rt.clock.now += 901
    recovery.claim_context_recovery(rt.db,rt.sid,owner="owner-a")
    terminal=current(rt)[2]
    rt.manager=goals.GoalManager(rt.sid);rt.manager.resume()
    assert prepare(rt,source_turn="after-explicit-resume")=="terminal"
    assert current(rt)[2]["original_deadline"]==terminal["original_deadline"]

def test_sqlite_acquisition_error_is_not_live_lock_contention(rt,monkeypatch):
    import sqlite3
    monkeypatch.setattr(rt.db,"_execute_write",Mock(side_effect=sqlite3.OperationalError("synthetic unavailable")))
    with pytest.raises(sqlite3.OperationalError):
        rt.db.try_acquire_compression_lock(rt.sid,"test-holder")

def test_compression_producer_carries_distinct_yield_and_unknown_failure(monkeypatch):
    from agent.conversation_loop import _compression_deferred_result
    import agent.conversation_loop as loop
    monkeypatch.setattr(loop,"time",NS(time=lambda:1000.,monotonic=lambda:100.))
    agent=NS(session_id="synthetic-compression",_flush_status_buffer=lambda:None,
        context_compressor=NS(_summary_failure_cooldown_until=700.,_structural_no_op_backoff_until=0.))
    result=_compression_deferred_result(agent,[],0,"transient_block")
    assert not result["failed"] and turn_outcome(result).kind=="yielded"
    assert result["turn_outcome"]["retry_at"]==1600.
    agent._compression_lock_state_unknown=True
    assert turn_outcome(_compression_deferred_result(agent,[],0)).kind=="terminal_error"

@pytest.mark.parametrize("version", ["legacy", "v2"])
def test_context_episode_leaves_existing_settlement_reserve_untouched(rt, version):
    from hermes_cli.goals_completion import make_policy
    rt.manager.state.runtime_budget_seconds=24000.
    rt.manager.state.runtime_elapsed_seconds=22000.
    rt.manager.state.runtime_policy=make_policy(24000.) if version=="v2" else {}
    rt.manager._save()
    assert prepare(rt)=="pending"
    state=current(rt)[1]
    reserve=state.runtime_policy["settlement_seconds"] if version=="v2" else 1200.
    assert current(rt)[2]["original_deadline"]==min(1900.,3000.-reserve)
    assert bool(state.runtime_policy)==(version=="v2")

@pytest.mark.parametrize("change", [{"failed":True},{"completed":True},{"turn_outcome":{"kind":"complete","cause":"compression_lock_busy","progress_saved":True,"next_owner":"native_goal_recovery"}}])
def test_contradictory_deferred_outcomes_are_not_accepted_as_yield(change):
    result={"failed":False,"completed":False,"compression_deferred":True,
            "turn_outcome":compression_outcome("compression_lock_busy").to_dict()}
    result.update(change)
    assert turn_outcome(result).kind=="terminal_error"
    assert turn_outcome(result).cause=="invalid_turn_outcome"

def test_persisted_wait_accounting_survives_two_deferred_turns(rt):
    prepare(rt);rt.clock.now += 20
    claim=recovery.claim_context_recovery(rt.db,rt.sid,owner="owner-a")
    assert recovery.context_turn_started(rt.db,rt.sid,claim["claim_id"])
    rt.clock.now += 5
    prepare(rt,source_turn="second-deferred")
    assert current(rt)[2]["episode_started_at"]==1000.
    rt.clock.now += 11
    recovery.claim_context_recovery(rt.db,rt.sid,owner="owner-a")
    assert current(rt)[2]["wait_elapsed_seconds"]==31.
