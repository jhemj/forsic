"""Offline staged-review and persisted-disposition contracts, with fake model streams."""
from copy import deepcopy
import json
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from forsic_plugin import independent_review as review
from forsic_plugin.investigation_context import resolve_context
import test_independent_review as fixtures


class ReviewDispositionTests(unittest.TestCase):
    setUp=fixtures.IndependentReviewTests.setUp
    bind=fixtures.IndependentReviewTests.bind
    session=fixtures.IndependentReviewTests.session
    save_note=fixtures.IndependentReviewTests.save_note
    setup_review=fixtures.IndependentReviewTests.setup_review
    add_sources=fixtures.IndependentReviewTests.add_sources
    kwargs=fixtures.IndependentReviewTests.kwargs
    response=fixtures.IndependentReviewTests.response
    request_paths=fixtures.IndependentReviewTests.request_paths
    ledger=fixtures.IndependentReviewTests.ledger

    def resolved(self):
        return resolve_context(self.sid,intake_root=self.intake,native_db=self.native)

    def model_findings(self,findings):
        def response(**kwargs):
            self.calls.append(deepcopy(kwargs))
            body=json.loads(kwargs['messages'][1]['content']);selected=[]
            for finding in findings:
                if not {'source_id','pointer','quote'}<=set(finding):selected.append(finding);continue
                source=next((s for s in body['sources'] if s['source_id']==finding['source_id']),{})
                field=next((f for f in source.get('fields',[]) if f['pointer']==finding['pointer'] and
                            finding['quote'] in str(f['value'])),{})
                selected.append({k:v for k,v in finding.items() if k not in ('source_id','pointer','quote')} |
                                {'review_ref':field.get('review_ref','R1:not-presented')})
            text=json.dumps({'findings':selected},ensure_ascii=False)
            return iter([SimpleNamespace(choices=[SimpleNamespace(index=0,
                delta=SimpleNamespace(content=text),finish_reason='stop')],usage=None)])
        self.fake.side_effect=response

    def finding(self,**changes):
        item=dict(source_id=self.inventory,pointer='/entries/0/path',quote='/settings/user-jobs/custom-account',
                  question='Inspect the user job',why='The inventory shows this candidate',next_check='Read this candidate',impact='material')
        item.update(changes);return item

    def dispose(self,result,disposition='investigated',reason='Checked the cited source and retained the bounded current answer.',citation_refs=None,*,current_answer='Current answer after the review'):
        from forsic_plugin.report_driven.grounded_judgment import read_refs,selection
        if citation_refs is None:
            start=self.case.record('tool_start',{'tool':'forsic_read','arguments':{'path':'followup.log'}},self.sid)
            data={'tool':'forsic_read','started_id':start,'path':'followup.log','lines':[{'line':1,'text':'The retained followup observes configuration only.'}]}
            source=self.case.record('tool_result',data,self.sid)
            citation_refs=[read_refs(self.case,{**data,'evidence_id':source})['presented_spans'][0]['citation_ref']]
        answer=dict(review_id=result['review_id'],finding_id=result['pending_finding_ids'][0],
                    disposition=disposition,reason=reason,citation_refs=citation_refs,citations=[])
        for reference in citation_refs:
            selected=selection(self.case,{},reference)
            answer['citations'].append({**selected,'byte_end':selected['byte_start']+len(selected['literal'].encode()),
                'source_ref':{'kind':'source','id':selected['source_id'],'version':selected['source_version']}})
        if disposition=='scope_limited':answer['limitation']=reason
        self.save_note(review_responses=[answer],answer=current_answer)
        return answer

    def test_exploration_blind_final_sees_current_answer_limits_and_draft_in_separate_roles(self):
        self.setup_review()
        self.save_note(answer='CURRENT_ANALYST_ANSWER',limitations=['CURRENT_LIMIT'])
        initial=review.goal_advisory_context(**self.kwargs(stage='exploration',draft_response='PRIVATE_IGNORED_DRAFT'))
        blind=self.calls[-1]['messages'][1]['content']
        for secret in ('CURRENT_ANALYST_ANSWER','CURRENT_LIMIT','PRIVATE_IGNORED_DRAFT'):
            self.assertNotIn(secret,blind)
        final=review.goal_advisory_context(**self.kwargs(stage='final',draft_response='CANDIDATE_FINAL'))
        body=json.loads(self.calls[-1]['messages'][1]['content'])
        self.assertEqual(body['analyst_state']['role'],'analyst_judgment_not_source_evidence')
        self.assertEqual(body['analyst_state']['questions'][0]['answer'],'CURRENT_ANALYST_ANSWER')
        self.assertEqual(body['analyst_state']['questions'][0]['limitations'],['CURRENT_LIMIT'])
        self.assertEqual(body['candidate_response']['text'],'CANDIDATE_FINAL')
        self.assertNotEqual(initial['review_id'],final['review_id'])
        self.assertTrue(final['requires_followup'])
        self.assertEqual(len(review.review_state(self.resolved())['pending_findings']),2)
        self.assertEqual(review.goal_advisory_context(**self.kwargs(stage='final',draft_response='CANDIDATE_FINAL'))['review_id'],final['review_id'])
        self.assertEqual(len(self.calls),2)

    def test_packet_preserves_boolean_types_and_separates_presentation_omissions(self):
        self.setup_review()
        result=review.goal_advisory_context(**self.kwargs())
        request=review._read(self.request_paths()[0])
        body=json.loads(request['messages'][1]['content'])
        inventory=next(s for s in body['sources'] if s['source_id']==self.inventory)
        complete=next(f for f in inventory['fields'] if f['pointer']=='/listing_complete')
        self.assertIs(complete['value'],True)
        from forsic_plugin.independent_review_state import _presented_handles
        coordinate=_presented_handles(request)[complete['review_ref']]
        self.assertEqual(coordinate['quote'],'true')
        self.assertEqual(inventory['omission_scope'],'review_packet_fields_only')
        self.assertEqual(body['omission_scope'],'review_packet_presentation_only')
        self.assertEqual(body['observed_scope']['omission_scope'],'projection_targets_only')
        actual=self.case.event(self.inventory)['data']['listing_complete']
        self.assertIs(actual,True)
        self.assertEqual(inventory['source_version'],review._retained_version(self.case,self.inventory))
        # An exact coordinate does not validate the reviewer's semantic claim.
        raw={'review_ref':complete['review_ref'],'question':'Is this inventory complete?',
             'why':'The reviewer incorrectly says false.','next_check':'Inspect original coverage.'}
        parsed=review._parse(json.dumps({'findings':[raw]}),{},review_refs=_presented_handles(request))
        self.assertEqual(parsed[0]['quote'],'true')
        self.assertEqual(parsed[0]['why'],raw['why'])

    def test_wrong_source_quote_rejected_per_item_and_valid_advice_survives(self):
        self.setup_review()
        modern=self.case.record('tool_result',{'tool':'forsic_read','path':'corrected.log',
            'lines':[{'line':1,'text':'2026-10-15 corrected source'}]},self.sid)
        self.model_findings([self.finding(source_id=modern,pointer='/lines/0/text',quote='1970'),
                             self.finding(source_id=modern,pointer='/lines/0/text',quote='2026-10-15')])
        result=review.goal_advisory_context(**self.kwargs(stage='final'))
        self.assertEqual(result['status'],'received')
        state=review.review_state(self.resolved())
        self.assertEqual(len(state['findings']),1)
        self.assertEqual(state['findings'][0]['quote'],'2026-10-15 corrected source')
        self.assertEqual(state['findings'][0]['quote_validation'],'exact_presented_field_handle')
        response=review._read(next(self.case.output.glob('*-response.json')))
        self.assertEqual(len(response['rejected_findings']),1)
        self.assertIn('review_ref',response['rejected_findings'][0]['reason'])
        self.assertNotIn('1970',result['context'])

    def test_analyst_claim_is_not_a_citable_source_and_malformed_item_does_not_drop_valid(self):
        self.setup_review()
        self.model_findings([{'question':'bad'},self.finding(source_id='analyst_state',pointer='/questions/0/answer',quote='PRIVATE_MAIN_ANSWER'),self.finding()])
        result=review.goal_advisory_context(**self.kwargs(stage='final'))
        self.assertEqual(result['status'],'received')
        self.assertEqual(len(review.review_state(self.resolved())['findings']),1)
        response=review._read(next(self.case.output.glob('*-response.json')))
        self.assertEqual(len(response['rejected_findings']),2)

    def test_received_does_not_mean_disposed_and_more_turns_or_new_source_do_not_clear_pending(self):
        self.setup_review()
        result=review.goal_advisory_context(**self.kwargs(stage='exploration'))
        self.assertEqual(result['status'],'received');self.assertTrue(result['requires_followup'])
        fid=result['pending_finding_ids'][0]
        again=review.goal_advisory_context(**self.kwargs(stage='exploration',turns_used=100))
        self.assertEqual(again['pending_finding_ids'],[fid]);self.assertEqual(len(self.calls),1)
        self.add_sources(70)  # Original source is beyond the recent-result selection window.
        state=review.review_state(self.resolved())
        self.assertEqual(state['status'],'stale');self.assertIn(result['review_id'],state['stale_review_ids'])
        self.assertIn(fid,state['pending_finding_ids'])
        before=self.ledger();review.latest_advice(self.resolved());self.assertEqual(self.ledger(),before)
        self.assertEqual(review.review_finding(self.resolved(),result['review_id'],fid)['finding_id'],fid)
        self.assertEqual(len(self.calls),1)

    def test_field_handle_resolves_exact_version_pointer_and_value_without_model_transcription(self):
        self.setup_review()
        result=review.goal_advisory_context(**self.kwargs())
        request=review._read(self.request_paths()[0]);self.assertEqual(request['schema_version'],3)
        body=json.loads(request['messages'][1]['content'])
        source=next(s for s in body['sources'] if s['source_id']==self.inventory)
        field=next(f for f in source['fields'] if f['pointer']=='/entries/0/path')
        finding=review.review_state(self.resolved())['findings'][0]
        self.assertEqual(finding['review_ref'],field['review_ref'])
        self.assertEqual(finding['quote'],field['value'])
        self.assertEqual(finding['source_version'],source['source_version'])
        self.assertEqual(finding['retrieval'],{'action':'source','source_id':self.inventory,'pointer':field['pointer'],'offset':0})
        from forsic_plugin.independent_review_state import _presented_handles
        snapshot=review._collect(self.resolved())
        self.assertEqual(_presented_handles(request),snapshot['review_refs'])
        raw={'review_ref':field['review_ref'],'question':'Inspect','why':'Inventory','next_check':'Read source'}
        valid=review._parse(json.dumps({'findings':[raw]}),snapshot['refs'],review_refs=snapshot['review_refs'])
        self.assertEqual(valid[0]['quote'],field['value'])
        for changed in ({**raw,'quote':'invented'},{**raw,'source_version':'invented'},
                        {**raw,'pointer':'/invented'},{**raw,'review_ref':'R1:not-presented'}):
            rejected=[]
            self.assertEqual(review._parse(json.dumps({'findings':[changed]}),snapshot['refs'],rejected=rejected,
                                           review_refs=snapshot['review_refs']),[])
            self.assertEqual(len(rejected),1)
        self.assertTrue(result['requires_followup'])

    def test_rejected_items_return_same_native_recovery_without_model_retry(self):
        self.setup_review()
        self.model_findings([self.finding(),self.finding(pointer='/not-presented')])
        result=review.goal_advisory_context(**self.kwargs())
        self.assertEqual(result['status'],'received')
        self.assertEqual(len(result['rejected_findings']),1)
        rejected=result['rejected_findings'][0]
        self.assertEqual(rejected['review_id'],result['review_id'])
        self.assertEqual(rejected['role'],'rejected_advice_not_evidence')
        self.assertIn('do not resubmit',rejected['recovery'])
        self.assertIn('거절된 검토 의견',result['context'])
        review.goal_advisory_context(**self.kwargs(turns_used=100))
        self.assertEqual(len(self.calls),1)

    def test_forged_reference_without_retained_citations_does_not_clear_finding(self):
        self.setup_review();result=review.goal_advisory_context(**self.kwargs())
        self.save_note(review_responses=[{'review_id':result['review_id'],
            'finding_id':result['pending_finding_ids'][0],'disposition':'investigated',
            'reason':'Claimed followup without evidence','citation_refs':[{'kind':'source','id':self.inventory,'version':'invented'}]}])
        state=review.review_state(self.resolved())
        self.assertTrue(state['requires_followup']);self.assertEqual(state['dispositions'],[])
        self.assertEqual(len(state['invalid_dispositions']),1)

    def test_each_explicit_disposition_preserves_review_history_and_clears_pending(self):
        for disposition in ('investigated','refuted','scope_limited'):
            with self.subTest(disposition=disposition):
                # Reuse one bounded review, replacing only the current note's response.
                if not hasattr(self,'calls'):
                    self.setup_review();result=review.goal_advisory_context(**self.kwargs())
                    self.review_result=result
                answer=self.dispose(self.review_result,disposition)
                state=review.review_state(self.resolved())
                self.assertFalse(state['requires_followup']);self.assertEqual(state['pending_finding_ids'],[])
                self.assertEqual(len(state['findings']),1)
                self.assertEqual(state['dispositions'][0]['disposition'],disposition)
                self.assertEqual(state['dispositions'][0]['question_revision'],self.note['revision'])
                self.assertEqual(state['dispositions'][0]['reason'],answer['reason'])
                self.assertEqual(len(self.calls),1)

    def test_source_correction_requires_grounded_refutation_not_automatic_erasure(self):
        self.setup_review()
        result=review.goal_advisory_context(**self.kwargs())
        from forsic_plugin.report_driven.grounded_judgment import read_refs
        start=self.case.record('tool_start',{'tool':'forsic_read','arguments':{'path':'job.log'}},self.sid)
        data={'tool':'forsic_read','started_id':start,'path':'job.log',
            'lines':[{'line':1,'text':'2026-10-15: configuration was not executed'}]}
        correction=self.case.record('tool_result',data,self.sid)
        cite=read_refs(self.case,{**data,'evidence_id':correction})['presented_spans'][0]['citation_ref']
        self.assertTrue(review.review_state(self.resolved())['requires_followup'])
        self.dispose(result,'refuted',reason='The new 2026 execution record contradicts the proposed execution link.',
                     citation_refs=[cite])
        state=review.review_state(self.resolved())
        self.assertFalse(state['requires_followup'])
        self.assertEqual(state['findings'][0]['source_id'],self.inventory)
        self.assertEqual(state['dispositions'][0]['citations'][0]['source_id'],correction)

    def test_contextual_advice_does_not_block_and_empty_advice_is_not_coverage_approval(self):
        self.setup_review();self.model_findings([self.finding(impact='contextual')])
        result=review.goal_advisory_context(**self.kwargs(stage='final'))
        state=review.review_state(self.resolved())
        self.assertFalse(result['requires_followup']);self.assertEqual(len(state['findings']),1)
        self.assertEqual(state['findings'][0]['impact'],'contextual')

    def test_unknown_dispatch_availability_disposition_stops_followup_without_resend(self):
        self.setup_review();self.fake.side_effect=TimeoutError('synthetic')
        current_answer=review._question_records(self.resolved())[0]['answer']
        clock={**review.current_context(),'current_datetime':'2032-01-01T00:00:00+00:00','timezone':'UTC'}
        with patch.object(review,'current_context',return_value=clock) as controlled_clock:
            result=review.goal_advisory_context(**self.kwargs())
            self.assertEqual(result['status'],'delivery_unconfirmed');self.assertTrue(result['requires_followup'])
            finding=review.review_finding(self.resolved(),result['review_id'],result['pending_finding_ids'][0])
            self.assertEqual(finding['kind'],'availability')
            # A disposition-only save must not introduce a new analyst answer.
            self.dispose(result,'scope_limited',reason='Independent delivery is unknown; current answer retains this review limitation.',
                         citation_refs=[],current_answer=current_answer)
            controlled_clock.return_value={**clock,'current_datetime':'2032-01-01T00:00:01+00:00'}
            again=review.goal_advisory_context(**self.kwargs(turns_used=100))
        self.assertEqual(again['status'],'delivery_unconfirmed');self.assertFalse(again['requires_followup'])
        self.assertEqual(review._question_records(self.resolved())[0]['answer'],current_answer)
        self.assertEqual(self.fake.call_count,1)
        self.assertEqual(len(self.request_paths()),1)

    def adopted_link(self,source_id,pointer,literal,*,start=0,source_version=None):
        from forsic_plugin.report_driven.host import source_record
        event=self.case.event(source_id);version=source_version or source_record(event)['version']
        observation=dict(id='O-old-selected',version='1',source_ref={'kind':'source','id':source_id,'version':version},
            field_pointer=pointer,literal=literal,byte_start=start,byte_end=start+len(literal.encode()),
            canonical_sha256=hashlib.sha256(literal.encode()).hexdigest(),presented_view='exact_excerpt',
            coordinate_basis='retained UTF-8 field bytes')
        claim=dict(id='F-selected',version='1',status='adopted',statement='Maintenance was requested. No unrelated account activity exists.',
            assertion_kind='interpretation',reasoning_summary='Analyst selected one maintenance request line.',
            limitations=['One local request line does not establish complete account coverage.'],scope='Requested synthetic scope',
            source_refs=[{'kind':'observation','id':observation['id'],'version':observation['version']}],counterevidence_refs=[],semantic_review='not_reviewed')
        self.case.record('report_change',{'records':{'claims':[claim],'observations':[observation]}})
        self.note['evidence_ids']=[source_id]
        self.save_note(answer=claim['statement'],claim_refs=[{'kind':'claim','id':claim['id'],'version':claim['version']}])
        return claim,observation

    def test_final_selected_old_source_exposes_exact_claim_observation_mapping_without_semantic_approval(self):
        self.setup_review();old=self.sources[0]['evidence_id']
        literal=self.case.event(old)['data']['lines'][0]['text']
        claim,observation=self.adopted_link(old,'/lines/0/text',literal)
        self.add_sources(60)
        blind=review._collect(self.resolved(),stage='exploration')
        self.assertNotIn(old,blind['refs']);self.assertNotIn('grounding',json.loads(blind['prompt']))
        final=review._collect(self.resolved(),stage='final',draft_response=claim['statement'])
        body=json.loads(final['prompt']);ground=body['analyst_state']['grounding']
        self.assertEqual(body['analyst_state']['questions'][0]['evidence_ids'],[old])
        self.assertEqual(ground['claims'][0]['statement'],claim['statement'])
        self.assertEqual(ground['claims'][0]['role'],'analyst_claim_not_source_evidence')
        selected=ground['observations'][0]
        self.assertEqual(selected['literal'],literal);self.assertEqual(selected['byte_start'],0)
        self.assertEqual(selected['source_ref'],observation['source_ref'])
        self.assertEqual(selected['acquisition_context']['field_role'],'body')
        self.assertFalse(selected['semantic_validation']);self.assertFalse(ground['claims'][0]['semantic_validation'])
        self.assertIn(literal,final['refs'][old]['/lines/0/text'])
        self.assertNotIn('No unrelated account',final['refs'][old]['/lines/0/text'])
        self.assertLessEqual(len(final['prompt'])+len(review.SYSTEM),review.MAX_INPUT)
        self.model_findings([self.finding(source_id=old,pointer='/lines/0/text',quote=literal[:20])])
        result=review.goal_advisory_context(**self.kwargs(stage='final',draft_response=claim['statement']))
        self.assertEqual(result['status'],'received');self.assertTrue(result['requires_followup'])
        self.assertEqual(review.review_state(self.resolved())['findings'][0]['source_id'],old)

    def test_selected_observation_wrong_version_or_literal_is_visible_unverified_and_not_quote_ref(self):
        self.setup_review();old=self.sources[0]['evidence_id'];self.add_sources(60)
        self.adopted_link(old,'/lines/0/text','invented literal')
        final=review._collect(self.resolved(),stage='final')
        observations=json.loads(final['prompt'])['analyst_state']['grounding']['observations']
        self.assertEqual(observations[0]['coordinate_validation'],'unverified')
        self.assertEqual(observations[0]['claimed_literal'],'invented literal');self.assertNotIn(old,final['refs'])
        self.adopted_link(old,'/lines/0/text','job=maintenance',source_version='0'*64)
        final=review._collect(self.resolved(),stage='final')
        self.assertNotIn(old,final['refs'])
        self.assertIn('version',json.loads(final['prompt'])['analyst_state']['grounding']['observations'][0]['unmet_expectation'])

    def test_selected_old_source_version_change_invalidates_final_currency(self):
        self.setup_review();old=self.sources[0]['evidence_id']
        literal=self.case.event(old)['data']['lines'][0]['text']
        self.adopted_link(old,'/lines/0/text',literal);self.add_sources(60);self.model_findings([])
        review.goal_advisory_context(**self.kwargs(stage='final'))
        self.assertTrue(review.review_state(self.resolved())['final_review_current'])
        event=self.case.event(old);event['data']['lines'][0]['text']='Corrected retained content'
        with self.case.connect() as db,db:
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(event['data']),old))
        state=review.review_state(self.resolved())
        self.assertFalse(state['final_review_current']);self.assertEqual(state['status'],'stale')
        observations=json.loads(review._collect(self.resolved(),stage='final')['prompt'])['analyst_state']['grounding']['observations']
        self.assertEqual(observations[0]['coordinate_validation'],'unverified')

    def test_multiple_exact_excerpts_at_one_pointer_do_not_create_a_synthetic_quote(self):
        self.setup_review();old=self.sources[0]['evidence_id']
        actual=self.case.event(old)['data']['lines'][0]['text'];first='maintenance';second='target=/opt/task'
        claim,observation=self.adopted_link(old,'/lines/0/text',first,start=actual.index(first))
        other={**observation,'id':'O-second','literal':second,'byte_start':actual.index(second),
               'byte_end':actual.index(second)+len(second),'canonical_sha256':hashlib.sha256(second.encode()).hexdigest()}
        claim['source_refs'].append({'kind':'observation','id':other['id'],'version':other['version']})
        self.case.record('report_change',{'records':{'claims':[claim],'observations':[other]}})
        self.add_sources(60);snapshot=review._collect(self.resolved(),stage='final')
        self.assertEqual(snapshot['refs'][old]['/lines/0/text'],[first,second])
        handles={ref:c for ref,c in snapshot['review_refs'].items() if c['source_id']==old and c['pointer']=='/lines/0/text'}
        self.assertEqual({c['quote'] for c in handles.values()},{first,second})
        selected=next(ref for ref,c in handles.items() if c['quote']==second)
        exact=review._parse(json.dumps({'findings':[{'review_ref':selected,'question':'Inspect',
            'why':'Selected fragment','next_check':'Read source'}]}),snapshot['refs'],review_refs=snapshot['review_refs'])
        self.assertEqual(exact[0]['quote'],second)
        from forsic_plugin.independent_review_state import _presented
        archived={'messages':[{'role':'user','content':snapshot['prompt']}]}
        self.assertEqual(_presented(archived)[old]['/lines/0/text'],[first,second])
        valid=self.finding(source_id=old,pointer='/lines/0/text',quote=second)
        invalid=self.finding(source_id=old,pointer='/lines/0/text',quote=first+second)
        self.assertEqual(len(review._parse(json.dumps({'findings':[valid,invalid]}),snapshot['refs'])),1)

    def test_large_shell_grounding_keeps_retrieval_coordinate_without_loading_body(self):
        self.setup_review()
        shell=self.case.record('tool_result',{'tool':'forsic_shell','stdout':'SHELL_SECRET_BODY'*30000,
            'representation':'derived_command_output','original_evidence':False},self.sid)
        self.adopted_link(shell,'/stdout','SHELL_SECRET_BODY')
        self.add_sources(60)
        final=review._collect(self.resolved(),stage='final')
        item=json.loads(final['prompt'])['analyst_state']['grounding']['observations'][0]
        self.assertEqual(item['coordinate_validation'],'unverified')
        self.assertEqual(item['retrieval']['source_id'],shell)
        self.assertNotIn(shell,final['refs'])
        self.assertNotIn('SHELL_SECRET_BODY'*2,final['prompt'])

    def handlers(self,enabled=True):
        from forsic_plugin import register
        self.stack.enter_context(patch('forsic_plugin.intake.Intake.case',return_value=self.case))
        ctx=Mock();ctx.get_config.side_effect=lambda key: str(self.intake) if key=='intake_root' else enabled if key=='independent_review' else None
        register(ctx)
        return {call.kwargs['name']:call.kwargs['handler'] for call in ctx.register_tool.call_args_list}

    def test_read_collects_checkpoint_advice_once_and_keeps_evidence_receipt_clean(self):
        self.setup_review();handlers=self.handlers()
        from forsic_plugin import independent_review_overlap as overlap
        jobs=[];start=overlap.start
        def capture(*args,**kwargs):
            job=start(*args,**kwargs);jobs.append(job);return job
        def finish():
            for job in jobs:
                self.assertTrue(job.done.wait(5),'Offline review must finish before fixture cleanup')
        self.stack.enter_context(patch.object(overlap,'start',side_effect=capture))
        self.addCleanup(finish)
        result=json.loads(handlers['forsic_read']({'path':'request.log'},session_id=self.sid))
        self.assertNotIn('error',result,result)
        self.assertNotIn('independent_review',result);self.fake.assert_not_called()
        started=review.goal_advisory_context(**self.kwargs(stage='exploration',nonblocking=True))
        self.assertEqual(started['status'],'in_progress')
        self.assertTrue(started['requires_followup'])
        original=self.case.event(result['evidence_id'])['data']
        self.assertNotIn('independent_review',original)
        self.assertEqual(original['lines'][0]['text'],'job=maintenance requested target=/opt/task')
        finish()
        self.assertEqual(json.loads(self.calls[0]['messages'][1]['content'])['stage'],'exploration')
        again=json.loads(handlers['forsic_read']({'path':'process.log'},session_id=self.sid))
        collected=again['independent_review']
        self.assertEqual(collected['role'],'independent_advice_not_source_evidence')
        self.assertIn(collected['status'],('received','stale'));self.assertEqual(len(self.calls),1)
        repeated=json.loads(handlers['forsic_read']({'path':'process.log'},session_id=self.sid))
        self.assertNotIn('independent_review',repeated);self.assertEqual(len(self.calls),1)
        projection=json.loads(handlers['forsic_reporting']({'action':'state'},session_id=self.sid))
        pending=projection['independent_review']
        self.assertTrue(pending['requires_followup'])
        self.assertEqual(pending['pending_finding_ids'],collected['pending_finding_ids'])
        self.assertIn('quote',pending['findings'][0])
        self.assertIn('review_inbox',pending['response_instruction'])
        self.assertIn('review_response',pending['response_instruction'])

    def test_reads_never_dispatch_after_cancel_failure_disabled_or_unavailable_checkpoint(self):
        self.setup_review();handlers=self.handlers()
        with patch('tools.interrupt.is_thread_interrupted',return_value=True):
            result=json.loads(handlers['forsic_read']({'path':'request.log'},session_id=self.sid))
        self.assertNotIn('error',result);self.assertNotIn('independent_review',result);self.fake.assert_not_called()
        result=json.loads(handlers['forsic_read']({'path':'missing.log'},session_id=self.sid))
        self.assertIn('error',result);self.fake.assert_not_called()
        self.config['model']['provider']='unsupported'
        result=json.loads(handlers['forsic_read']({'path':'request.log'},session_id=self.sid))
        self.assertNotIn('independent_review',result);self.fake.assert_not_called()
        unavailable=review.goal_advisory_context(**self.kwargs(stage='exploration'))
        self.assertEqual(unavailable['status'],'unavailable');self.fake.assert_not_called()
        self.config['model']['provider']='custom'
        result=json.loads(handlers['forsic_read']({'path':'process.log'},session_id=self.sid))
        self.assertNotIn('independent_review',result);self.fake.assert_not_called()
        disabled=self.handlers(enabled=False)
        result=json.loads(disabled['forsic_read']({'path':'request.log'},session_id=self.sid))
        self.assertNotIn('independent_review',result);self.fake.assert_not_called()

    def test_final_currency_includes_current_question_evaluation_version(self):
        self.setup_review();self.model_findings([])
        result=review.goal_advisory_context(**self.kwargs(stage='final',draft_response='Candidate'))
        state=review.review_state(self.resolved())
        self.assertTrue(state['final_review_current'])
        self.assertEqual(state['latest_final_review_id'],result['review_id'])
        self.assertEqual(state['latest_final_status'],'received');self.assertEqual(state['latest_stage'],'final')
        self.save_note(answer='Corrected answer after final review')
        state=review.review_state(self.resolved())
        self.assertFalse(state['final_review_current']);self.assertEqual(state['latest_final_status'],'received')
        self.assertEqual(review._latest_result(self.resolved())['status'],'stale')

    def test_canonical_note_owner_is_json_session_even_when_writer_event_session_is_blank(self):
        self.setup_review();result=review.goal_advisory_context(**self.kwargs())
        self.dispose(result)
        # Native atomic note writer uses an empty event session; JSON retains its owner.
        with self.case.connect() as db,db:
            db.execute("UPDATE events SET session='' WHERE kind='note' AND json_extract(data,'$.revision')=?",(self.note['revision'],))
        state=review.review_state(self.resolved())
        self.assertFalse(state['requires_followup'])
        self.assertEqual(state['dispositions'][0]['question_revision'],self.note['revision'])

    def test_writer_can_resolve_original_findings_with_minimal_bound_context(self):
        self.setup_review();result=review.goal_advisory_context(**self.kwargs())
        resolved=self.resolved();resolved.pop('goal')
        projected=review.review_state(resolved,{'questions':review._question_records(resolved)})
        self.assertNotEqual(projected['status'],'unavailable')
        self.assertIn(result['pending_finding_ids'][0],projected['pending_finding_ids'])
        self.assertFalse(projected['final_review_current'])

    def test_quote_from_unpresented_truncated_suffix_is_rejected(self):
        self.setup_review()
        source=self.case.record('tool_result',{'tool':'forsic_read','path':'large.log',
            'lines':[{'line':1,'text':'A'*1500+'SECRET_UNPRESENTED_SUFFIX'}]},self.sid)
        snapshot=review._collect(self.resolved())
        self.assertEqual(len(snapshot['refs'][source]['/lines/0/text']),1200)
        invalid=self.finding(source_id=source,pointer='/lines/0/text',quote='SECRET_UNPRESENTED_SUFFIX')
        self.assertEqual(review._parse(json.dumps({'findings':[invalid]}),snapshot['refs']),[])

    def test_disposed_failed_final_retains_availability_status_for_report_limit(self):
        self.setup_review();self.fake.side_effect=TimeoutError('synthetic')
        result=review.goal_advisory_context(**self.kwargs(stage='final'))
        self.dispose(result,'scope_limited',reason='Final review delivery is unknown; retain this limit.',citation_refs=[])
        state=review.review_state(self.resolved())
        self.assertFalse(state['requires_followup']);self.assertFalse(state['final_review_current'])
        self.assertEqual(state['latest_final_status'],'delivery_unconfirmed')

    def test_legacy_receipt_without_quote_is_historical_and_not_a_current_validated_finding(self):
        self.setup_review();review.goal_advisory_context(**self.kwargs())
        path=self.request_paths()[0];request=review._read(path);request.pop('schema_version')
        path.write_text(json.dumps(request))
        response_path=path.with_name(path.name.replace('-request.json','-response.json'))
        response=review._read(response_path)
        response.pop('response_version');response.pop('request_sha256')
        response_path.write_text(json.dumps(response))
        state=review.review_state(self.resolved())
        self.assertEqual(state['status'],'historical_unvalidated')
        self.assertFalse(state['requires_followup']);self.assertEqual(state['findings'],[])
        self.assertEqual(review._latest_result(self.resolved())['status'],'historical_unvalidated')

    def test_schema_two_receipts_keep_exact_substring_validation(self):
        self.setup_review();review.goal_advisory_context(**self.kwargs())
        path=self.request_paths()[0];request=review._read(path);request['schema_version']=2
        path.write_text(json.dumps(request))
        response_path=path.with_name(path.name.replace('-request.json','-response.json'))
        response=review._read(response_path)
        response['request_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        response['raw_advice']=json.dumps({'findings':[self.finding()]})
        response_path.write_text(json.dumps(response))
        projected=review.review_state(self.resolved())
        self.assertEqual(projected['status'],'received')
        self.assertEqual(projected['findings'][0]['quote_validation'],'exact_substring_only')
        self.assertTrue(projected['requires_followup'])

    def test_source_version_change_invalidates_old_field_handle_even_for_an_unchanged_field(self):
        self.setup_review();before=review._collect(self.resolved())
        coordinate=next(c for c in before['review_refs'].values() if c['source_id']==self.inventory and c['pointer']=='/entries/0/path')
        handle=next(ref for ref,c in before['review_refs'].items() if c==coordinate)
        event=self.case.event(self.inventory);event['data']['entries'][1]['bytes']=21
        with self.case.connect() as db,db:
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(event['data']),self.inventory))
        after=review._collect(self.resolved())
        current=next(c for c in after['review_refs'].values() if c['source_id']==self.inventory and c['pointer']==coordinate['pointer'])
        self.assertEqual(coordinate['quote'],current['quote'])
        self.assertNotEqual(coordinate['source_version'],current['source_version'])
        self.assertNotIn(handle,after['review_refs'])

    def test_invalid_raw_advice_cannot_be_promoted_by_a_received_status(self):
        self.setup_review();self.model_findings([])
        review.goal_advisory_context(**self.kwargs(stage='final'))
        path=next(self.case.output.glob('*-response.json'));response=review._read(path)
        response['raw_advice']=json.dumps({'findings':[{'review_ref':'R1:invented',
            'question':'Check','why':'Claimed','next_check':'Read'}]})
        response['status']='received';path.write_text(json.dumps(response))
        projected=review.review_state(self.resolved())
        self.assertFalse(projected['final_review_current'])
        self.assertTrue(projected['requires_followup'])
        self.assertEqual(projected['latest_final_status'],'invalid_advice')
        self.assertEqual(projected['findings'][0]['kind'],'availability')

    def test_final_evaluation_revision_changes_review_input_but_blind_answer_edit_does_not(self):
        self.setup_review();review.goal_advisory_context(**self.kwargs(stage='exploration'))
        self.save_note(answer='Revised analyst answer')
        review.goal_advisory_context(**self.kwargs(stage='exploration',turns_used=100))
        self.assertEqual(len(self.calls),1)
        final=review.goal_advisory_context(**self.kwargs(stage='final',draft_response='Fixed candidate'))
        self.save_note(answer='Corrected current answer')
        changed=review.goal_advisory_context(**self.kwargs(stage='final',draft_response='Fixed candidate'))
        self.assertEqual(len(self.calls),3)
        self.assertNotEqual(final['review_id'],changed['review_id'])
        stopped=review.goal_advisory_context(**self.kwargs(stage='final',draft_response='Another candidate'))
        self.assertEqual(stopped['status'],'budget_exhausted');self.assertTrue(stopped['requires_followup'])


if __name__=='__main__':unittest.main()
