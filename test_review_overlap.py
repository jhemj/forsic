"""Offline overlap at native tool boundaries; fake auxiliary calls and barriers only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from forsic_plugin import independent_review as review
from forsic_plugin import independent_review_overlap as overlap
from forsic_plugin.investigation_context import resolve_context
import test_review_dispositions as fixtures


class ReviewOverlapTests(unittest.TestCase):
    bind=fixtures.ReviewDispositionTests.bind
    session=fixtures.ReviewDispositionTests.session
    save_note=fixtures.ReviewDispositionTests.save_note
    setup_review=fixtures.ReviewDispositionTests.setup_review
    add_sources=fixtures.ReviewDispositionTests.add_sources
    kwargs=fixtures.ReviewDispositionTests.kwargs
    request_paths=fixtures.ReviewDispositionTests.request_paths
    response=fixtures.ReviewDispositionTests.response
    ledger=fixtures.ReviewDispositionTests.ledger

    def setUp(self):
        fixtures.ReviewDispositionTests.setUp(self)
        self.setup_review()
        self.entered=threading.Event();self.release=threading.Event();self.jobs=[]
        original=overlap.start
        def start(*args,**kwargs):
            job=original(*args,**kwargs);self.jobs.append(job);return job
        self.stack.enter_context(patch.object(overlap,'start',side_effect=start))
        self.fake.side_effect=self.blocked
        # Finish before source/aux patches or temporary homes are released.
        self.addCleanup(self.finish_workers)

    def resolved(self):
        return resolve_context(self.sid,intake_root=self.intake,native_db=self.native)

    def blocked(self,**kwargs):
        self.calls.append(deepcopy(kwargs))
        self.entered.set()
        if not self.release.wait(5):raise AssertionError('test did not release auxiliary barrier')
        body=json.loads(kwargs['messages'][1]['content'])
        source=next((s for s in body['sources'] if s['source_id']==self.inventory),body['sources'][0])
        handle=next((f['review_ref'] for f in source['fields'] if f['pointer']=='/entries/0/path'),source['fields'][0]['review_ref'])
        text=json.dumps({'findings':[{'review_ref':handle,'question':'Inspect the user configuration',
            'why':'An inventory does not establish its contents','next_check':'Read the indicated source'}]})
        return iter([SimpleNamespace(choices=[SimpleNamespace(index=0,
            delta=SimpleNamespace(content=text),finish_reason='stop')],usage=None)])

    def finish_workers(self):
        self.release.set()
        for job in self.jobs:
            if not job.done.wait(5):
                job.cancel.set()
                raise AssertionError('bounded test worker did not finish')

    def launch(self,**changes):
        result=review.goal_advisory_context(**self.kwargs(stage='exploration',nonblocking=True,**changes))
        self.assertTrue(self.entered.wait(3))
        self.assertFalse(self.jobs[-1].done.is_set())
        return result

    def settle(self):
        self.release.set()
        self.assertTrue(self.jobs[-1].done.wait(5))
        return review.advisory_at_boundary(self.resolved())

    def handlers(self):
        from forsic_plugin import register
        self.stack.enter_context(patch('forsic_plugin.intake.Intake.case',return_value=self.case))
        ctx=Mock();ctx.get_config.side_effect=lambda key:str(self.intake) if key=='intake_root' else key=='independent_review'
        register(ctx)
        tools={call.kwargs['name']:call.kwargs['handler'] for call in ctx.register_tool.call_args_list}
        hooks={call.args[0]:call.args[1] for call in ctx.register_hook.call_args_list}
        return tools,hooks,ctx

    def test_main_collection_runs_while_same_native_model_reviews_fixed_input(self):
        result=self.launch()
        self.assertEqual(result['status'],'in_progress');self.assertTrue(result['requires_followup'])
        request_path=self.request_paths()[0];before=request_path.read_bytes()
        request=json.loads(before)
        collected=json.loads(self.case.invoke('forsic_read',{'path':'process.log'},self.sid))
        self.assertNotIn('error',collected)
        self.assertFalse(self.release.is_set())
        self.assertEqual(request_path.read_bytes(),before)
        sent=json.loads(self.calls[0]['messages'][1]['content'])
        self.assertNotIn(collected['evidence_id'],{source['source_id'] for source in sent['sources']})
        self.assertEqual(sent['stage'],'exploration')
        self.assertNotIn('PRIVATE_MAIN_ANSWER',str(self.calls[0]))
        self.assertEqual(self.calls[0]['model'],self.config['model']['default'])
        self.assertTrue(self.calls[0]['stream'])
        self.settle()
        response=review._read(request_path.with_name(request_path.name.replace('-request.json','-response.json')))
        self.assertEqual(response['request_sha256'],hashlib.sha256(before).hexdigest())
        self.assertEqual(response['response_version'],1)
        self.assertTrue(request['snapshot_revision'])
        self.assertEqual(request['execution_mode'],'overlap')

    def test_duplicate_invocations_and_final_boundary_do_not_start_a_second_call(self):
        self.launch()
        for stage in ('exploration','checkpoint'):
            value=review.goal_advisory_context(**self.kwargs(stage=stage))
            self.assertEqual(value['status'],'in_progress')
            self.assertTrue(value['requires_followup'])
        self.add_sources(9)
        waiting=threading.Event();finished=threading.Event();values=[]
        original=overlap.wait_existing
        def wait(*args,**kwargs):
            waiting.set();return original(*args,**kwargs)
        def final():
            try:values.append(review.goal_advisory_context(**self.kwargs(stage='final',draft_response='Candidate')))
            finally:finished.set()
        with patch.object(overlap,'wait_existing',side_effect=wait):
            from agent.memory_provider import spawn_context_thread
            thread=spawn_context_thread(final,name='offline-final-boundary');thread.start()
            self.assertTrue(waiting.wait(3));self.assertFalse(finished.is_set())
            self.settle();self.assertTrue(finished.wait(3));thread.join(3)
        self.assertTrue(values[0]['requires_followup'])
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.request_paths()),1)

    def test_live_request_cannot_be_dispositioned_away_at_final_boundary(self):
        self.launch()
        with patch.object(overlap,'wait_existing',return_value='in_progress'), patch.object(review,'review_state',return_value={'requires_followup':False,
                'review_id':'', 'pending_finding_ids':[], 'pending_findings':[]}):
            value=review.goal_advisory_context(**self.kwargs(stage='final',draft_response='Candidate'))
        self.assertEqual(value['status'],'in_progress');self.assertTrue(value['requires_followup'])
        self.assertEqual(len(self.calls),1)

    def test_new_evidence_marks_old_snapshot_stale_and_retains_material_findings(self):
        self.launch();self.add_sources(1)
        result=self.settle()
        self.assertEqual(result['status'],'stale');self.assertTrue(result['requires_followup'])
        state=review.review_state(self.resolved())
        self.assertEqual(len(state['pending_findings']),1)
        self.assertEqual(state['pending_findings'][0]['source_id'],self.inventory)
        self.assertEqual(state['pending_findings'][0]['quote_validation'],'exact_presented_field_handle')
        self.assertEqual(state['dispositions'],[])

    def test_session_disposal_and_interrupt_latch_native_aux_cancellation(self):
        for kind in ('dispose','interrupted','failed'):
            with self.subTest(kind=kind):
                # Each reason is tested with its own actual goal binding.
                if self.jobs:
                    self.sid+='-next';self.session(self.sid,goal=self.goal);self.bind(self.sid,self.case)
                    self.add_sources(4);self.save_note(answer='Provisional assessment for this native conversation');self.entered.clear();self.release.clear()
                self.launch()
                review.cancel_pending_reviews(native_db=self.native,session_id='other',dispose=True)
                self.assertFalse(self.jobs[-1].cancel.is_set())
                review.cancel_pending_reviews(native_db=self.native,session_id=self.sid)
                self.assertFalse(self.jobs[-1].cancel.is_set())
                review.cancel_pending_reviews(native_db=self.native,session_id=self.sid,**{kind:True})
                self.assertTrue(self.jobs[-1].cancel.is_set())
                self.settle()
                response=review._read(self.request_paths()[-1].with_name(self.request_paths()[-1].name.replace('-request.json','-response.json')))
                self.assertEqual(response['status'],'cancelled')

    def test_owner_thread_interrupt_is_observed_from_native_aux_scope(self):
        from tools.interrupt import set_interrupt,is_thread_interrupted
        from agent.auxiliary_client import _aux_interrupt_cancel_requested
        owner=threading.get_ident()
        checked=[]
        original=self.blocked
        def blocked(**kwargs):
            result=original(**kwargs)
            checked.append(_aux_interrupt_cancel_requested())
            return result
        self.fake.side_effect=blocked
        self.launch(cancel_check=lambda:is_thread_interrupted(owner))
        try:
            set_interrupt(True,owner)
            self.settle()
        finally:set_interrupt(False,owner)
        self.assertEqual(checked,[True])
        self.assertTrue(self.jobs[-1].cancel.is_set())

    def test_native_goal_pause_cancels_bound_request_without_adopting_results(self):
        self.launch()
        self.goal.status='paused';self.session(self.sid,goal=self.goal)
        self.settle()
        response=review._read(next(self.case.output.glob('*-response.json')))
        self.assertEqual(response['status'],'cancelled')
        self.assertEqual(response['findings'],[])

    def test_plugin_unload_and_real_session_hooks_cancel_only_their_owner(self):
        _,hooks,ctx=self.handlers()
        self.launch()
        hooks['on_session_end'](session_id=self.sid,completed=True,interrupted=False,failed=False)
        self.assertFalse(self.jobs[-1].cancel.is_set())
        hooks['on_session_finalize'](session_id='other')
        self.assertFalse(self.jobs[-1].cancel.is_set())
        for call in ctx.on_unload.call_args_list:call.args[0]()
        self.assertTrue(self.jobs[-1].cancel.is_set())
        self.settle()

    def test_later_real_tool_boundary_collects_once_without_altering_evidence_receipt(self):
        tools,_,_=self.handlers()
        first=json.loads(tools['forsic_read']({'path':'request.log'},session_id=self.sid))
        self.assertNotIn('independent_review',first)
        self.assertFalse(self.calls)
        self.launch()
        self.assertTrue(self.entered.wait(3))
        second=json.loads(tools['forsic_read']({'path':'process.log'},session_id=self.sid))
        self.assertNotIn('error',second)
        self.assertFalse(self.release.is_set());self.assertEqual(len(self.calls),1)
        self.settle()
        # Cached reads are still native tool boundaries and collect settled advice.
        third=json.loads(tools['forsic_read']({'path':'request.log'},session_id=self.sid))
        self.assertIn('independent_review',third)
        self.assertTrue(third['independent_review']['requires_followup'])
        self.assertNotEqual(third['independent_review']['status'],'in_progress')
        fourth=json.loads(tools['forsic_read']({'path':'request.log'},session_id=self.sid))
        self.assertNotIn('independent_review',fourth)
        retained=self.case.event(first['evidence_id'])['data']
        self.assertNotIn('independent_review',retained)
        self.assertEqual(len(self.calls),1)

    def test_successful_answer_save_starts_review_but_failed_save_does_not(self):
        from forsic_plugin.question_bootstrap import PROVISIONAL_ANSWER
        from forsic_plugin.report_driven.host import current
        for field in ('summary','last_response'):self.note['investigation'].pop(field,None)
        self.note['answer']=PROVISIONAL_ANSWER
        self.note['investigation'].pop('answer',None)
        self.save_note()
        tools,_,_=self.handlers()
        bad=json.loads(tools['forsic_question']({'operation_id':'missing-answer'},session_id=self.sid))
        self.assertIn('error',bad);self.assertFalse(self.calls)
        q=current(self.case)['questions'][0]
        saved=json.loads(tools['forsic_question']({'operation_id':'save-first-answer',
            'question_id':q['id'],'expected_revision':q['revision'],
            'answer':'Provisional record comparison; success remains unconfirmed',
            'reason':'Preserve the bounded provisional assessment'},session_id=self.sid))
        self.assertNotIn('error',saved,saved)
        self.assertTrue(saved['independent_review']['requires_followup'],saved)
        self.assertTrue(self.entered.wait(3));self.assertEqual(len(self.calls),1)
        self.assertEqual(len(self.request_paths()),1)
        # An ungrounded provisional answer may still require judgment review;
        # timing admission is not semantic acceptance of that answer.
        self.assertNotIn('Provisional record comparison',self.calls[0]['messages'][1]['content'])
        self.settle()

    def test_review_failure_does_not_reclassify_committed_answer_as_failed_save(self):
        from forsic_plugin.report_driven.host import current
        for field in ('summary','last_response'):self.note['investigation'].pop(field,None)
        self.note['investigation'].pop('answer',None)
        self.save_note()
        q=current(self.case)['questions'][0]
        with patch.object(review,'goal_advisory_context',side_effect=RuntimeError('synthetic review error')):
            # register captures the function, so create the real handlers under the patch.
            tools,_,_=self.handlers()
            saved=json.loads(tools['forsic_question']({'operation_id':'save-despite-review-error',
                'question_id':q['id'],'expected_revision':q['revision'],
                'answer':'Saved answer before review error', 'reason':'Save provisional assessment'},session_id=self.sid))
        self.assertNotIn('error',saved,saved)
        self.assertEqual(saved['independent_review']['status'],'unavailable')
        self.assertEqual(current(self.case)['questions'][0]['answer'],'Saved answer before review error')

    def test_finished_receipt_survives_worker_registry_retirement(self):
        self.launch();self.settle()
        self.assertEqual(review.inflight_review_ids(self.resolved()),[])
        self.assertTrue(review.advisory_at_boundary(self.resolved())['requires_followup'])
        self.assertEqual(len(self.calls),1)

    def test_restart_unknown_request_is_not_replayed_even_after_new_sources(self):
        self.launch();self.settle()
        response=next(self.case.output.glob('*-response.json'))
        response.unlink()  # Synthetic crash: immutable request survived, no response did.
        again=review.goal_advisory_context(**self.kwargs(stage='exploration',nonblocking=True))
        self.assertEqual(again['status'],'delivery_unconfirmed')
        self.assertTrue(again['requires_followup'])
        self.add_sources(8)
        final=review.goal_advisory_context(**self.kwargs(stage='final',draft_response='Candidate'))
        self.assertEqual(final['status'],'delivery_unconfirmed')
        self.assertTrue(final['requires_followup']);self.assertEqual(len(self.calls),1)
        self.assertEqual(len(self.request_paths()),1)

    def test_one_reserved_final_slot_and_three_request_budget_are_shared(self):
        self.launch();received=self.settle()
        fixtures.ReviewDispositionTests.dispose(self,received)
        self.fake.side_effect=lambda **kwargs: iter([SimpleNamespace(choices=[SimpleNamespace(index=0,
            delta=SimpleNamespace(content='{"findings":[]}'),finish_reason='stop')],usage=None)])
        self.add_sources(8)
        review.goal_advisory_context(**self.kwargs(stage='exploration',turns_used=5))
        self.assertEqual(len(self.request_paths()),2)
        self.add_sources(8)
        reserved=review.goal_advisory_context(**self.kwargs(stage='exploration',turns_used=9,nonblocking=True))
        self.assertEqual(reserved['status'],'final_reserved');self.assertEqual(len(self.request_paths()),2)
        final=review.goal_advisory_context(**self.kwargs(stage='final'))
        self.assertFalse(final['requires_followup'])  # Prior material finding has a saved response.
        self.assertEqual(len(self.request_paths()),3)
        capped=review.goal_advisory_context(**self.kwargs(stage='final',draft_response='Changed'))
        self.assertEqual(capped['status'],'budget_exhausted');self.assertFalse(capped['requires_followup'])


    def test_final_wait_obeys_existing_request_deadline_without_another_dispatch(self):
        self.launch()
        self.jobs[-1].deadline=0
        result=review.goal_advisory_context(**self.kwargs(stage='final',draft_response='Candidate'))
        self.assertEqual(result['status'],'in_progress');self.assertTrue(result['requires_followup'])
        self.assertTrue(self.jobs[-1].cancel.is_set());self.assertEqual(len(self.calls),1)

    def test_final_wait_cancels_without_waiting_for_an_unresponsive_fake_stream(self):
        self.launch()
        cancel=threading.Event();waiting=threading.Event();finished=threading.Event();values=[]
        original=overlap.wait_existing
        def wait(*args,**kwargs):
            waiting.set();return original(*args,**kwargs)
        def final():
            try:values.append(review.goal_advisory_context(**self.kwargs(stage='final',
                draft_response='Candidate',cancel_check=cancel.is_set)))
            finally:finished.set()
        from agent.memory_provider import spawn_context_thread
        with patch.object(overlap,'wait_existing',side_effect=wait):
            thread=spawn_context_thread(final,name='offline-cancelled-final');thread.start()
            self.assertTrue(waiting.wait(3));cancel.set()
            self.assertTrue(finished.wait(3));thread.join(3)
        self.assertEqual(values[0]['status'],'cancelled')
        self.assertTrue(self.jobs[-1].cancel.is_set());self.assertEqual(len(self.calls),1)

    def test_forensic_workbench_is_final_analyst_context_and_exploration_stays_blind(self):
        self.save_note(forensic={'temporal':{'interpretation':'ANALYST_TEMPORAL_SECRET'},
                                 'working_model':'Hypothesis, not a source observation'})
        self.launch();self.settle()
        self.assertNotIn('ANALYST_TEMPORAL_SECRET',str(self.calls[0]))
        snapshot=review._collect(self.resolved(),stage='final',draft_response='Current draft')
        body=json.loads(snapshot['prompt'])
        summaries=[q['forensic_summary'] for q in body['analyst_state']['questions'] if 'forensic_summary' in q]
        self.assertEqual(len(summaries),1)
        self.assertEqual(summaries[0]['role'],'analyst_workbench_not_source_evidence')
        self.assertIn('ANALYST_TEMPORAL_SECRET',summaries[0]['text'])
        self.assertLessEqual(len(summaries[0]['text']),1400)


    def test_replacing_goal_cancels_prior_request_before_starting_another_for_same_session(self):
        self.launch()
        self.goal.created_at+=1
        self.goal.goal='A different question in the same native conversation'
        self.session(self.sid,goal=self.goal)
        blocked=review.goal_advisory_context(**self.kwargs(stage='exploration',nonblocking=True))
        self.assertEqual(blocked['status'],'in_progress')
        self.assertTrue(self.jobs[0].cancel.is_set())
        self.assertEqual(len(self.jobs),1);self.assertEqual(len(self.request_paths()),1)
        self.settle()
        self.entered.clear();self.release.clear()
        self.save_note(answer='Provisional assessment for the replacement goal')
        self.launch()
        self.assertEqual(len(self.jobs),2);self.assertEqual(len(self.request_paths()),2)


    def test_current_top_level_workbench_and_selected_time_hash_are_final_only(self):
        self.note['forensic']={'checks':[{'reason':'TOP_LEVEL_ANALYST_STATE'}]}
        self.note['timeline']=[{'event_ref':'TE-'+str(index),'raw_time':'Oct 15 08:00:00',
            'time':'2026-10-15T08:00:00+09:00', 'description':'Model-maintained temporal scope',
            'temporal':{'observed_or_inferred':'inferred','timestamp_role':'event_occurrence',
                'clock_domain':'host-log','earliest':'2026-10-15T07:59:00+09:00',
                'latest':'2026-10-15T08:01:00+09:00','year_basis':'ANALYST_YEAR_BASIS'},
            'temporal_basis_refs':[{'evidence_id':self.inventory,'pointer':'/entries/0/path',
                                    'source_version':'a'*64,'source_role':'retained_tool_result'}]}
            for index in range(5)]
        self.save_note()
        blind=review._collect(self.resolved(),stage='exploration')
        self.assertNotIn('TOP_LEVEL_ANALYST_STATE',blind['prompt'])
        self.assertNotIn('ANALYST_YEAR_BASIS',blind['prompt'])
        final=review._collect(self.resolved(),stage='final')
        question=json.loads(final['prompt'])['analyst_state']['questions'][0]
        self.assertIn('TOP_LEVEL_ANALYST_STATE',question['forensic_summary']['text'])
        temporal=question['temporal_summary']
        self.assertEqual(len(temporal['events']),4);self.assertEqual(temporal['omitted_events'],1)
        self.assertEqual(temporal['events'][0]['raw_time'],'Oct 15 08:00:00')
        self.assertEqual(temporal['events'][0]['temporal']['observed_or_inferred'],'inferred')
        self.assertEqual(temporal['events'][0]['basis_refs'][0]['source_version'],'a'*64)
        # Even an omitted event participates in the exact final-state revision.
        self.note['timeline'][4]['temporal']['year_basis']='A changed unshown inference'
        self.case.record('note',self.note,self.sid)
        changed=review._collect(self.resolved(),stage='final')
        self.assertNotEqual(final['evaluation_fingerprint'],changed['evaluation_fingerprint'])


    def test_receipt_publication_is_atomic_and_cannot_replace_an_existing_receipt(self):
        target=self.case.output/'synthetic-immutable.json'
        ready=threading.Event();release=threading.Event();done=threading.Event();errors=[]
        original=review.os.link
        def link(source,destination,**kwargs):
            if Path(destination)==target:
                ready.set()
                if not release.wait(3):raise AssertionError('test publication barrier not released')
            return original(source,destination,**kwargs)
        def write():
            try:review._immutable(target,{'status':'complete'})
            except Exception as exc:errors.append(exc)
            finally:done.set()
        from agent.memory_provider import spawn_context_thread
        with patch.object(review.os,'link',side_effect=link):
            thread=spawn_context_thread(write,name='offline-receipt-publication');thread.start()
            try:
                self.assertTrue(ready.wait(3));self.assertFalse(target.exists())
            finally:release.set()
            self.assertTrue(done.wait(3));thread.join(3)
        self.assertEqual(errors,[]);self.assertEqual(review._read(target),{'status':'complete'})
        with self.assertRaises(FileExistsError):review._immutable(target,{'status':'replacement'})
        self.assertEqual(review._read(target),{'status':'complete'})

    def test_response_version_hash_is_verified_at_read_boundaries(self):
        self.launch();self.settle()
        request_path=self.request_paths()[0];request=review._read(request_path)
        response_path=request_path.with_name(request_path.name.replace('-request.json','-response.json'))
        response=review._response_for(request_path,request)
        response['request_sha256']='0'*64
        response_path.write_text(json.dumps(response))
        with self.assertRaises(ValueError):review._response_for(request_path,request)
        value=review.advisory_at_boundary(self.resolved())
        self.assertEqual(value['status'],'unavailable');self.assertTrue(value['requires_followup'])
        self.assertEqual(len(self.calls),1)


    def test_native_background_dispatch_keeps_profile_a_b_a_routes(self):
        from hermes_constants import (get_hermes_home,set_hermes_home_override,reset_hermes_home_override)
        second=fixtures.ReviewDispositionTests();second.setUp();second.setup_review()
        self.addCleanup(second.doCleanups);self.addCleanup(self.finish_workers)
        homes={self.home.resolve():'profile-model-a',second.home.resolve():'profile-model-b'}
        seen=[]
        def config():
            return {'model':{'provider':'custom','default':homes[get_hermes_home().resolve()],
                             'base_url':'http://127.0.0.1:9000/v1'}}
        def model(**kwargs):
            seen.append((get_hermes_home().resolve(),kwargs['model']))
            return iter([SimpleNamespace(choices=[SimpleNamespace(index=0,
                delta=SimpleNamespace(content='{"findings":[]}'),finish_reason='stop')],usage=None)])
        with patch('hermes_cli.config.load_config_readonly',side_effect=config), patch(
                'agent.auxiliary_client.call_llm',side_effect=model):
            for index,fixture in enumerate((self,second,self)):
                if index==2:
                    self.goal.created_at+=1;self.session(self.sid,goal=self.goal);self.save_note(answer='Provisional assessment for the next goal')
                token=set_hermes_home_override(fixture.home)
                try:
                    review.goal_advisory_context(**fixture.kwargs(stage='exploration',nonblocking=True))
                finally:reset_hermes_home_override(token)
                self.assertTrue(self.jobs[-1].done.wait(3))
        self.assertEqual(seen,[(self.home.resolve(),'profile-model-a'),
                              (second.home.resolve(),'profile-model-b'),
                              (self.home.resolve(),'profile-model-a')])


if __name__=='__main__':unittest.main()
