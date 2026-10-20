"""Pro P1 contract lane: synthetic real writer/resolver; no model or network."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

import test_efficiency_contracts as fixtures
from forsic_plugin.evidence import Case
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.current_judgment import current_judgment
from forsic_plugin.report_driven.views import bundle, bundle_stale, selected_report_state


class QuestionV2Tests(unittest.TestCase):
    call=fixtures.EfficiencyContracts.call

    def setUp(self):
        fixtures.EfficiencyContracts.setUp(self)
        # Native tool ownership is already mocked by the shared fixture. Give
        # the report's read-only scope resolver that same explicit binding.
        self.resolved = dict(case=Case(self.case.manifest,read_only=True), goal=None, goal_id='synthetic-goal',
            session_id='synthetic-session', requested_session_id='synthetic-session',
            conversation_id='synthetic-session', session_lineage=['synthetic-session'],
            case_id=self.case.config['case_id'])
        resolver = patch('forsic_plugin.investigation_context.resolve_context',
            side_effect=lambda session_id, **kwargs: self.resolved if session_id == 'synthetic-session' else None)
        resolver.start();self.addCleanup(resolver.stop)

    def test_disposition_reports_independent_shape_errors_in_one_rejection_then_repairs(self):
        q = self.save(gaps=['Unobserved completion'])
        before = host.current(self.case)
        payload = dict(operation_id='invalid-close', question_id=q['id'], expected_revision=q['revision'],
            work_state='scoped_closed', reason='Retain the uncertainty',
            gap_dispositions=[dict(gap_text='Unobserved completion', disposition='assessed_unresolved',
                                   reason='No completion record', reopen_conditions='New records')])
        result = self.call('forsic_question', payload)
        self.assertIn('payload.gap_dispositions[0].reopen_conditions', result['error'])
        self.assertIn('"missing_fields": ["reopen_conditions"]', result['error'])
        self.assertNotIn('Example:', result['error'])
        self.assertEqual(host.current(self.case)['meta']['content_revision'], before['meta']['content_revision'])
        payload.update(operation_id='repair-disposition', reopen_conditions=['New records'],
                       judgment=self.judgment(), answer=q['answer'])
        payload['gap_dispositions'][0]['reopen_conditions'] = ['New records']
        repaired = self.call('forsic_question', payload)
        self.assertNotIn('error', repaired, repaired)
        self.assertEqual(host.find(host.current(self.case),'questions',q['id'])['work_state'], 'scoped_closed')

    def test_closure_conflicts_are_reported_together_without_weakening_snapshot_guards(self):
        q = self.save(judgment=self.judgment(), reason='Retain explicit source', gaps=['Unknown effect'])
        before = host.current(self.case)
        payload = self.close_payload(q, work_state='scoped_closed', reason='Different reason',
                                     reopen_conditions=['Different conditions'])
        result = self.call('forsic_question', payload)
        self.assertIn('"incompatible_fields": ["reopen_conditions", "work_state"]', result['error'])
        self.assertIn('"must_equal": "closure.reason"', result['error'])
        self.assertEqual(host.current(self.case)['meta']['content_revision'], before['meta']['content_revision'])
        for field in ('work_state','reason','reopen_conditions'): payload.pop(field)
        payload['closure']['basis_snapshot'] = 'stale-basis'
        self.assertIn('Closure basis changed', self.call('forsic_question', payload)['error'])
        self.assertEqual(host.current(self.case)['meta']['content_revision'], before['meta']['content_revision'])
        result = self.call('forsic_question', self.close_payload(q))
        self.assertNotIn('error', result, result)

    def test_closure_reports_shape_identity_owner_revision_and_basis_in_one_atomic_rejection(self):
        q=self.save(judgment=self.judgment(),reason='Retain source',next_checks=['Compare a completion record'])
        before=host.current(self.case)
        payload=self.close_payload(q,expected_revision=q['revision']+9,scope='Changed scope',
                                   work_state='scoped_closed',reason='Conflicting reason')
        payload['closure'].update(basis_snapshot='Human description of the evidence',reopen_conditions='New records')
        result=json.loads(self.case.invoke('forsic_reporting',{'action':'question','payload':payload},'foreign',
            request_context={'goal_id':'foreign-goal','conversation_id':'foreign-session','question_contract_version':'v2'}))
        error=result['error']
        for expected in ('payload.closure.reopen_conditions','incompatible_fields','must_equal',
                         'Question revision conflict','another native conversation','another native goal',
                         'Closure basis changed','Explicitly reassess next_checks'):
            self.assertIn(expected,error)
        recovery=json.loads(error.split(' Recovery: ',1)[1])
        self.assertEqual((recovery['question_id'],recovery['revision'],recovery['snapshot_id']),
                         (q['id'],q['revision'],before['meta']['snapshot_id']))
        self.assertFalse(recovery['editable_in_current_conversation'])
        self.assertEqual(recovery['read_call'],{'tool':'forsic_reporting','arguments':{'action':'state','question_id':q['id']}})
        after=host.current(self.case)
        self.assertEqual(after['meta']['content_revision'],before['meta']['content_revision'])
        self.assertEqual(after['questions'],before['questions'])
        self.assertEqual(after['gaps'],before['gaps'])
        repaired=self.close_payload(q,next_checks=[])
        result=self.call('forsic_question',repaired)
        self.assertNotIn('error',result,result)

    def test_new_question_with_closure_explains_identity_and_basis_without_saving_draft(self):
        before=host.current(self.case)
        payload=dict(operation_id='new-with-closure',question='A bounded question?',scope='Synthetic records',
            answer='Unknown',expected_revision=9,closure=dict(basis_snapshot='Evidence reviewed',reason='No further check',
                retain_unresolved=True,reopen_conditions=['New evidence']))
        result=self.call('forsic_question',payload)
        for expected in ('incompatible_fields','New questions omit expected_revision','closure selects an existing question',
                         'Closure basis changed'):
            self.assertIn(expected,result['error'])
        recovery=json.loads(result['error'].split(' Recovery: ',1)[1])
        self.assertNotIn('question_id',recovery)
        self.assertIn('Save a new provisional or grounded answer',recovery['meaning'])
        self.assertEqual(host.current(self.case)['questions'],before['questions'])
        payload.pop('expected_revision');payload.pop('closure')
        created=self.call('forsic_question',payload)
        self.assertNotIn('error',created,created)
        call=created['question']['read_call']
        self.assertNotIn('error',self.call(call['tool'],call['arguments']))

    def save(self, op='new', **changes):
        p=dict(operation_id=op,question='Did the requested job finish?',scope='Acquired synthetic records',
               answer='A request is known; completion is unknown.',priority='material')
        p.update(changes)
        result=self.call('forsic_question',p)
        self.assertNotIn('error',result,result)
        return host.find(host.current(self.case),'questions',result['question']['question_id'])

    def judgment(self, path='request.log', **changes):
        result=self.call('forsic_read',{'path':path})
        self.assertNotIn('error',result,result)
        p=dict(citation_refs=[result['presented_spans'][0]['citation_ref']],assertion_kind='interpretation',
               reasoning_summary='This record describes a request, not a completion receipt.')
        p.update(changes)
        return p

    def revise(self, q, op, **changes):
        result=self.call('forsic_question',dict(operation_id=op,question_id=q['id'],expected_revision=q['revision'],**changes))
        self.assertNotIn('error',result,result)
        return host.find(host.current(self.case),'questions',q['id'])

    def close_payload(self,q,**changes):
        page=self.call('forsic_reporting',{'action':'state','question_id':q['id']})
        self.assertNotIn('error',page,page)
        p=dict(operation_id='close-'+q['id'],question_id=q['id'],expected_revision=q['revision'],
            closure=dict(basis_snapshot=page['snapshot_id'],reason='Available records cannot distinguish completion; retain the limits.',
                         retain_unresolved=True,reopen_conditions=['An independent completion receipt is acquired']))
        p.update(changes)
        return p

    def test_provisional_and_direct_paths_create_only_declared_gaps(self):
        provisional=self.save()
        s=host.current(self.case)
        self.assertFalse([g for g in s['gaps'] if g['question_ref']['id']==provisional['id']])
        j=self.judgment()
        revised=self.revise(provisional,'adopt',answer=provisional['answer'],reason='Check retained request',judgment=j)
        direct=self.save('direct',judgment=j,reason='Check retained request')
        s=host.current(self.case);host.checked(s)
        for q in (revised,direct):
            self.assertEqual(q['remaining_gap_ids'],[])
            self.assertTrue(q['claim_refs'])
        for field in ('missions','tests','assessments'):self.assertEqual(s[field],[])

    def test_omitted_analysis_is_undeclared_not_reviewed_empty(self):
        q=self.save(judgment=self.judgment(),reason='Use exact read')
        s=host.current(self.case);c=host.find(s,'claims',q['claim_refs'][0]['id'])
        self.assertEqual(c['declared_analysis_fields'],[])
        self.assertEqual(c['inference_strength'],'unrated')
        j=current_judgment(s,q)['judgments'][0]
        self.assertEqual(set(j['undeclared_analysis_fields']),{'counter_citation_refs','assumptions','limitations','inference_strength'})
        q=self.revise(q,'declared',answer=q['answer'],reason='Explicitly compare selected evidence',
            judgment=self.judgment(counter_citation_refs=[],assumptions=[],limitations=['Completion unknown'],inference_strength='plausible'))
        self.assertFalse(current_judgment(host.current(self.case),q)['judgments'][0]['undeclared_analysis_fields'])

    def test_correction_closure_and_report_share_current_answer_and_retained_limits(self):
        q=self.save(judgment=self.judgment(),reason='Retain initial interpretation',gaps=['Completion receipt absent','Exit status unavailable'])
        old=q['claim_refs'][0]['id']
        q=self.revise(q,'correct',answer='Process creation is recorded; completion remains unknown.',reason='Additional record changes the current answer',
                      judgment=self.judgment('process.log',limitations=['Creation does not prove completion']))
        out=self.call('forsic_question',self.close_payload(q));self.assertNotIn('error',out,out)
        s=host.current(self.case);host.checked(s);q=host.find(s,'questions',q['id'])
        self.assertEqual(q['work_state'],'scoped_closed');self.assertEqual(host.find(s,'claims',old)['status'],'superseded')
        gs=[g for g in s['gaps'] if g['question_ref']['id']==q['id']]
        self.assertEqual(len(gs),2)
        self.assertTrue(all(g['disposition']=='assessed_unresolved' and not g['resolution_refs'] for g in gs))
        rendered=self.call('forsic_reporting',{'action':'render','allow_partial':True})
        self.assertNotIn('error',rendered,rendered)
        m=json.loads(Path(rendered['manifest']).read_text())
        self.assertEqual(m['question_ids'],[q['id']]);self.assertEqual(m['selected_scope_status'],'supported_scope_closed')
        self.assertEqual(m['case_inventory']['investigation'],'partial')
        for name in ('integrated.html',):
            body=(Path(rendered['manifest']).parent/name).read_text()
            self.assertIn(q['answer'],body);self.assertIn('Completion receipt absent',body)
            disclaimer='미선언 분석 항목'
            self.assertIn(disclaimer,body)
            self.assertIn('counter_citation_refs',body)
        self.assertFalse(bundle_stale(self.case,m))
        self.revise(q,'reopen',work_state='active',reason='New contrary record',answer='The completion claim needs review')
        self.assertTrue(bundle_stale(self.case,m))
        self.assertTrue(Path(rendered['manifest']).is_file())

    def test_closure_rejects_stale_snapshot_and_commit_race_atomically(self):
        q=self.save(judgment=self.judgment(),reason='Retain read',gaps=['Uncertain limit'])
        p=self.close_payload(q)
        self.save('other')
        before=host.current(self.case)
        result=self.call('forsic_question',p);self.assertIn('Closure basis changed',result.get('error',''))
        self.assertEqual(before['meta']['content_revision'],host.current(self.case)['meta']['content_revision'])
        p=self.close_payload(q)
        writer=host.write
        def race(*args,**kwargs):
            self.save('concurrent-other')
            return writer(*args,**kwargs)
        with patch.object(host,'write',side_effect=race):result=self.call('forsic_question',p)
        self.assertIn('before commit',result.get('error',''))
        now=host.find(host.current(self.case),'questions',q['id'])
        self.assertEqual(now,q)

    def test_selected_report_freshness_tracks_selected_answer_and_preserves_old_bytes(self):
        q=self.save(judgment=self.judgment(),reason='Retain read')
        other=self.save('other-report-question',judgment=self.judgment('process.log'),reason='Separate question')
        rendered=self.call('forsic_reporting',{'action':'render','allow_partial':True,'question_ids':[q['id']]})
        self.assertNotIn('error',rendered,rendered)
        manifest=json.loads(Path(rendered['manifest']).read_text())
        files={p:Path(p).read_bytes() for p in rendered['files']}
        self.revise(other,'other-scope',scope='A revised, unrelated scope',reason='Separate scope definition')
        self.assertFalse(bundle_stale(self.case,manifest))
        self.revise(q,'selected-correct',answer='Creation is now confirmed; completion is still unknown.',
                    reason='Adopt the additional source',judgment=self.judgment('process.log'))
        self.assertTrue(bundle_stale(self.case,manifest))
        for p,content in files.items():self.assertEqual(Path(p).read_bytes(),content)

    def test_report_keeps_native_observation_conversation_guard(self):
        self.save(judgment=self.judgment(),reason='Retain the scoped source')
        with patch('forsic_plugin.investigation_context.resolve_context',
                   return_value={**self.resolved,'conversation_id':'foreign-session'}):
            result=self.call('forsic_reporting',{'action':'render'})
        self.assertIn('Report observation scope does not match its native conversation',result.get('error',''))

    def test_existing_gap_dependency_survives_v2_judgment(self):
        s=host.current(self.case);q=host.find(s,'questions',self.qid)
        original_gap=q['remaining_gap_ids'][0]
        m=self.call('forsic_mission',dict(operation_id='historical-plan',question_id=q['id'],expected_revision=q['revision'],
            gap_id=original_gap,why_it_matters='Retained discriminator',tool_name='forsic_read',tool_arguments={'path':'process.log'},
            inconclusive_rule='No linkage',limits=['No completion receipt'],reopen_conditions=['Acquire new records']))
        self.assertNotIn('error',m,m)
        q=host.find(host.current(self.case),'questions',q['id'])
        self.revise(q,'old-note-new-judgment',answer='Request known; completion unknown.',
                    reason='Use the v2 judgment contract',judgment=self.judgment())
        s=host.current(self.case);host.checked(s)
        self.assertEqual(host.find(s,'missions',m['mission']['id'])['gap_ref']['id'],original_gap)
        self.assertEqual(host.find(s,'gaps',original_gap)['disposition'],'open')

    def test_bulk_closure_keeps_material_gap_and_requires_explicit_reassessment(self):
        q=self.save(judgment=self.judgment(),reason='Retain read',critical_gaps=['Important unreviewed source'])
        p=self.close_payload(q);s=host.current(self.case)
        out=self.call('forsic_question',p);self.assertIn('Material or planned',out.get('error',''))
        self.assertEqual(s['meta']['content_revision'],host.current(self.case)['meta']['content_revision'])
        p['gap_dispositions']=[dict(gap_text='Important unreviewed source',disposition='assessed_unresolved',
            reason='The source is not acquired; this limit remains important',reopen_conditions=['Acquire it'])]
        out=self.call('forsic_question',p);self.assertNotIn('error',out,out)
        g=host.find(host.current(self.case),'gaps',q['remaining_gap_ids'][0]);self.assertEqual(g['disposition'],'assessed_unresolved')

    def test_planned_running_and_unassessed_work_cannot_be_blanket_closed(self):
        q=self.save(judgment=self.judgment(),reason='Retain read',gaps=['Compare process record'])
        m=self.call('forsic_mission',dict(operation_id='plan-v2',question_id=q['id'],expected_revision=q['revision'],
            gap_id=q['remaining_gap_ids'][0],why_it_matters='A useful discriminator',tool_name='forsic_read',tool_arguments={'path':'process.log'},
            inconclusive_rule='Not the same task',limits=['No completion receipt'],reopen_conditions=['Acquire new records']))
        self.assertNotIn('error',m,m)
        out=self.call('forsic_question',self.close_payload(q));self.assertIn('Material or planned',out.get('error',''))
        call=m['next_call'];result=self.call(call['tool'],call['arguments']);self.assertNotIn('error',result,result)
        out=self.call('forsic_question',self.close_payload(q));self.assertIn('unassessed native work',out.get('error',''))
        q=host.find(host.current(self.case),'questions',q['id'])
        assessed=self.call('forsic_assess',dict(operation_id='assess-v2',mission_id=m['mission']['id'],result_id=result['evidence_id'],
            expected_revision=q['revision'],citation_refs=[result['presented_spans'][0]['citation_ref']],outcome='inconclusive',
            reasoning_summary='A creation record cannot establish request linkage.',limitations=['Completion unknown'],next_check='Acquire linkage'))
        self.assertNotIn('error',assessed,assessed)

    def test_next_checks_need_explicit_reassessment_and_definition_cannot_change_with_closure(self):
        q=self.save(judgment=self.judgment(),reason='Retain read',gaps=['Uncertainty'],next_checks=['Useful work'])
        p=self.close_payload(q)
        self.assertIn('next_checks',self.call('forsic_question',p).get('error',''))
        p['next_checks']=[];p['scope']='A different scope'
        before=host.current(self.case)
        self.assertIn('"incompatible_fields": ["scope"]',self.call('forsic_question',p).get('error',''))
        self.assertEqual(host.current(self.case)['meta']['content_revision'],before['meta']['content_revision'])
        p.pop('scope');self.assertNotIn('error',self.call('forsic_question',p))

    def test_replay_payload_conflict_stale_cas_and_foreign_owner_preserve_state(self):
        q=self.save(judgment=self.judgment(),reason='Retain read')
        p=self.close_payload(q);out=self.call('forsic_question',p);self.assertNotIn('error',out,out)
        self.assertEqual(out['question'],self.call('forsic_question',p)['question'])
        before=host.current(self.case)
        bad=deepcopy(p);bad['closure']['reason']='Different intent'
        self.assertIn('different mutation payload',self.call('forsic_question',bad).get('error',''))
        bad['operation_id']='fresh-op'
        self.assertIn('revision',self.call('forsic_question',bad).get('error','').lower())
        q=host.find(before,'questions',q['id'])
        bad=dict(operation_id='foreign',question_id=q['id'],expected_revision=q['revision'],answer='Unauthorized edit')
        out=json.loads(self.case.invoke('forsic_reporting',{'action':'question','payload':bad},'foreign',
            request_context={'goal_id':'other','conversation_id':'other','question_contract_version':'v2'}))
        self.assertIn('error',out)
        self.assertEqual(before['meta']['content_revision'],host.current(self.case)['meta']['content_revision'])

    def test_pin_matches_optional_contract_and_legacy_actions_are_not_model_writes(self):
        from tools.schema_sanitizer import sanitize_tool_schemas
        from jsonschema import Draft202012Validator
        wire=sanitize_tool_schemas([{'type':'function','function':self.schemas['forsic_question']}])[0]['function']['parameters']
        p=dict(operation_id='wire',judgment=self.judgment())
        self.assertFalse(list(Draft202012Validator(wire).iter_errors(p)))
        rp=self.schemas['forsic_reporting']['parameters']
        self.assertNotIn('$defs',rp)
        self.assertNotIn('review_response',rp['properties']['action']['enum'])
        response=self.schemas['forsic_review_response']['parameters']
        self.assertEqual(set(response['properties']),
            {'review_id','finding_id','disposition','reason','limitation','citation_refs',
             'operation_id','question_id','expected_revision'})
        self.assertNotIn('payload',response['properties'])
        for action in ('question','mission','assess','gap','requirement','publish'):
            self.assertNotIn(action,rp['properties']['action']['enum'])
            self.assertIn('error',self.call('forsic_reporting',{'action':action}))

    def test_write_rollback_keeps_v2_readable_and_rejects_reinterpretation(self):
        q=self.save(judgment=self.judgment(),reason='Retain read')
        from forsic_plugin import register
        ctx=Mock();ctx.get_config.side_effect=lambda k: 'v1' if k=='question_contract_version' else str(Path(self.temp.name)/'intake') if k=='intake_root' else None
        register(ctx)
        handlers={c.kwargs['name']:c.kwargs['handler'] for c in ctx.register_tool.call_args_list}
        schemas={c.kwargs['name']:c.kwargs['schema'] for c in ctx.register_tool.call_args_list}
        self.assertEqual(len(schemas['forsic_question']['parameters']['properties']['judgment']['anyOf'][0]['required']),7)
        self.assertNotIn('closure',schemas['forsic_question']['parameters']['properties'])
        out=json.loads(handlers['forsic_question'](dict(operation_id='rollback-edit',question_id=q['id'],expected_revision=q['revision'],answer='Do not reinterpret'),session_id='synthetic-session'))
        self.assertIn('v2 question writes are disabled',out.get('error',''))
        out=json.loads(handlers['forsic_reporting']({'action':'state','question_id':q['id']},session_id='synthetic-session'))
        self.assertNotIn('error',out,out)
        self.assertEqual(host.find(host.current(self.case),'questions',q['id']),q)


if __name__=='__main__':unittest.main()
