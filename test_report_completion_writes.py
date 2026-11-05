"""Closing admission reaches existing scoped writers; no provider or live-case calls."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from forsic_plugin import report_work, independent_review
from forsic_plugin.report_driven import host
from hermes_cli import goals, goals_completion, goals_runtime
import test_runtime_completion_integration as fixture


class ReportCompletionWriteTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.RuntimeCompletionIntegrationTests('test_H59_deferred_repair_continue_current_review_real_artifact_and_selection')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.item=self.f.cases['alpha'];self.case=self.item.case
        self.agent=NS(_session_db=self.f.db,session_id=self.item.sid,
            _runtime_turn_identity=goals_runtime.runtime_identity(self.item.goal))
        self.context={'_session_id':self.item.sid,'_goal_id':goals.goal_identity(self.item.goal),
            '_independent_review_options':{'enabled':True,'intake_root':str(self.f.intake),
                'native_db':str(self.f.db_path),'session_id':self.item.sid}}
        goals_completion.technical_closing(self.f.db,self.item.sid,
            goals_runtime.runtime_identity(self.item.goal),'finish-current-report')

    def question(self):return host.current(self.case)['questions'][0]

    def classify(self,tool,args):
        return report_work.goal_work_projection(session_id=self.item.sid,
            goal_identity=list(goals_runtime.runtime_identity(self.item.goal)),mode='classify_action',
            action={'tool':tool,'arguments':args},intake_root=self.f.intake,native_db=self.f.db_path,
            completion_contract=fixture.CONTRACT,enabled=True)

    def require_judgment(self):
        saved=deepcopy(self.item.note);saved['revision']+=1
        saved.setdefault('investigation',{})['judgment_review_required']=True
        self.case.record('note',saved,self.item.sid)

    def test_required_current_judgment_passes_native_closing_and_actual_writer(self):
        self.require_judgment();q=self.question()
        payload={'operation_id':'current-answer-review','question_id':q['id'],'expected_revision':q['revision'],
            'assessment':'undetermined','reason':'Retain the qualified result after checking the selected records'}
        self.assertEqual(self.classify('forsic_question',payload)['purpose'],'report')
        denied,args=goals_completion.admit_tool_request(self.agent,'forsic_question',payload,'same-question')
        self.assertIsNone(denied)
        saved=host.invoke(self.case,{'action':'question','payload':args,**self.context})
        self.assertEqual(self.question()['revision'],q['revision']+1)
        self.assertFalse(self.question()['judgment_review_required'])
        self.assertEqual(self.question()['id'],q['id'])
        self.assertEqual(self.f.provider_calls,[]);self.assertEqual(self.f.review_calls,[])
        self.assertEqual(self.classify('forsic_question',{**payload,'expected_revision':self.question()['revision']})['purpose'],'discovery')

    def test_new_foreign_stale_and_widened_scope_remain_discovery(self):
        self.require_judgment();q=self.question()
        base={'operation_id':'current-answer-review','question_id':q['id'],'expected_revision':q['revision'],
            'assessment':'undetermined','reason':'Current scoped interpretation'}
        cases=[('forsic_question',{'operation_id':'new','question':'New','scope':'Different','answer':'New'}),
            ('forsic_question',{**base,'expected_revision':q['revision']-1}),
            ('forsic_question',{**base,'scope':'A different investigation'}),
            ('forsic_question',{**base,'question_id':host.current(self.f.cases['beta'].case)['questions'][0]['id']}),
            ('forsic_question',{**base,'closure':{'basis_snapshot':'wrong','reason':'retain',
                'retain_unresolved':True,'reopen_conditions':['New evidence']}}),
            ('forsic_mission',{'operation_id':'new-mission','question_id':q['id'],'expected_revision':q['revision']}),
            ('forsic_shell',{'action':'run','command':'arbitrary'})]
        before=self.case.db.read_bytes()
        for number,(tool,args) in enumerate(cases):
            with self.subTest(tool=tool,number=number):
                self.assertEqual(self.classify(tool,args)['purpose'],'discovery')
                denied,_=goals_completion.admit_tool_request(self.agent,tool,args,'denied-'+str(number))
                self.assertEqual(json.loads(denied)['error'],'report_reserve_enforced')
        self.assertEqual(self.case.db.read_bytes(),before)

    def test_returned_critical_test_evaluation_reaches_existing_atomic_writer_once(self):
        from pathlib import Path
        import tempfile
        from forsic_plugin.report_driven.investigation_state import validate_execution_context
        from forsic_plugin.static_scan import scan
        self.require_judgment();q=self.question();state=host.current(self.case)
        gap=next(g for g in state['gaps'] if g['id'] in q['remaining_gap_ids']
                 and g['impact'] in ('material','decision_critical'))
        toolargs={'action':'static_scan','paths':['/work/selected.log'],'mode':'literal','text':'marker',
                  'max_bytes':12,'max_matches':1,'max_seconds':2,'timeout_seconds':30}
        mission=host.invoke(self.case,{'action':'mission','payload':{
            'operation_id':'saved-check','question_id':q['id'],'expected_revision':q['revision'],
            'gap_id':gap['id'],'tool_name':'forsic_shell','tool_arguments':toolargs,
            'support_rule':'A match supports marker presence only','why_it_matters':'Resolve the material uncertainty',
            'inconclusive_rule':'No match in selected bytes cannot resolve unscanned data',
            'limits':['No actor attribution'],'reopen_conditions':['New retained data']},**self.context})['mission']
        action={**toolargs,'mission_id':mission['id'],'mission_version':mission['version']}
        self.assertEqual(self.classify('forsic_shell',action)['purpose'],'targeted_verification')
        context=validate_execution_context(self.case,mission['id'],mission['version'],'forsic_shell',action,
            goal_id=self.context['_goal_id'],session_id=self.item.sid)
        start=self.case.record('tool_start',{'tool':'forsic_shell','arguments':action,'mission_context':context},self.item.sid)
        with tempfile.TemporaryDirectory() as root:
            Path(root,'selected.log').write_text('unrelated\n')
            result=scan({k:v for k,v in toolargs.items() if k!='timeout_seconds'},root=root)
        result_id=self.case.record('tool_result',{'tool':'forsic_shell','started_id':start,
            'mission_context':context,**result},self.item.sid)
        payload={'operation_id':'evaluate-returned-check','mission_id':mission['id'],'result_id':result_id,
            'citation_refs':[],'expected_revision':self.question()['revision'],'outcome':'inconclusive',
            'reasoning_summary':'The bounded test does not resolve the broader question',
            'limitations':['No body evidence selected; this is not a finding of absence'],
            'next_check':'Retain the unresolved qualified answer'}
        before=self.case.db.read_bytes()
        for changed in ({'mission_id':'foreign'}, {'result_id':self.item.sources[0]['evidence_id']},
                        {'expected_revision':q['revision']-1}, {'citation_refs':['forged']}):
            self.assertEqual(self.classify('forsic_assess',{**payload,**changed})['purpose'],'discovery')
        self.assertEqual(self.case.db.read_bytes(),before)
        self.assertEqual(self.classify('forsic_assess',payload)['purpose'],'report')
        denied,args=goals_completion.admit_tool_request(self.agent,'forsic_assess',payload,'evaluate-current-test')
        self.assertIsNone(denied)
        saved=host.invoke(self.case,{'action':'assess_selected','payload':args,**self.context})
        self.assertEqual(saved['assessment']['result_ref']['id'],result_id)
        self.assertEqual(saved['assessment']['outcome'],'inconclusive')
        self.assertEqual(len(host.current(self.case)['assessments']),1)
        self.assertEqual(self.classify('forsic_assess',payload)['purpose'],'discovery')
        self.assertEqual(self.f.provider_calls,[]);self.assertEqual(self.f.review_calls,[])

    def author(self):
        helper=fixture.report_fixture.ReportCompletionWorkTests('test_saved_final_is_verified_and_reused_without_claiming_remote_delivery_or_layout')
        helper.case=self.case;helper.native=self.context
        helper.author()

    def test_reviewed_body_reads_remain_available_without_opening_cosmetic_writes(self):
        from forsic_plugin.report_driven import narrative
        self.author()
        self.assertTrue(narrative.status(host.current(self.case))['ready'])
        before=self.case.db.read_bytes()
        request={'action':'compose','section':'review'}
        self.assertEqual(self.classify('forsic_reporting',request)['purpose'],'read')
        denied,args=goals_completion.admit_tool_request(self.agent,'forsic_reporting',request,'read-reviewed-body')
        self.assertIsNone(denied)
        packet=host.invoke(self.case,{**args,**self.context})
        read_call=packet['review_index_call']['arguments']
        self.assertEqual(self.classify('forsic_reporting',read_call)['purpose'],'read')
        denied,args=goals_completion.admit_tool_request(self.agent,'forsic_reporting',read_call,'read-reviewed-index')
        self.assertIsNone(denied)
        result=host.invoke(self.case,{**args,**self.context})
        self.assertTrue(result)
        self.assertEqual(self.case.db.read_bytes(),before)
        edit=host.invoke(self.case,{'action':'compose','section':'overall_judgment',**self.context})
        self.assertEqual(self.classify('forsic_reporting',{'action':'compose','section':'overall_judgment',
            'operation_id':'cosmetic','edit_ref':edit['save_contract']['edit_ref'],'draft':{'entries':[]}})['purpose'],'discovery')

    def test_material_finding_reopens_only_current_scope_authenticated_body_write(self):
        from forsic_plugin.report_driven import narrative
        self.author();result,_=self.material_review()
        self.assertTrue(narrative.status(host.current(self.case))['ready'])
        packet=host.invoke(self.case,{'action':'compose','section':'overall_judgment',**self.context})
        existing=deepcopy(narrative.status(host.current(self.case))['fields']['overall_judgment'])
        allowed=narrative.schema()['properties']['entries']['items']['properties']
        existing['entries']=[{k:v for k,v in e.items() if k in allowed} for e in existing['entries']]
        entry=existing['entries'][0]
        entry['cells'][0]='별도 요청 및 생성 관측의 존재 확인; 공통 작업 식별은 미확정'
        request={'action':'compose','section':'overall_judgment','operation_id':'material-body-correction',
            'question_ids':packet['question_ids'],'edit_ref':packet['save_contract']['edit_ref'],
            'draft':{'entries':existing['entries']}}
        before=self.case.db.read_bytes()
        for changed in ({'edit_ref':'forged'}, {'expected_revision':packet['revision']-1},
                        {'basis_id':'stale'}, {'question_ids':['foreign-question']}):
            self.assertEqual(self.classify('forsic_reporting',{**request,**changed})['purpose'],'discovery')
        self.assertEqual(self.case.db.read_bytes(),before)
        self.assertEqual(self.classify('forsic_reporting',request)['blocker'],'review_findings_pending')
        denied,args=goals_completion.admit_tool_request(self.agent,'forsic_reporting',request,'material-body-correction')
        self.assertIsNone(denied)
        host.invoke(self.case,{**args,**self.context})
        after=narrative.status(host.current(self.case))
        self.assertEqual(after['fields']['overall_judgment']['entries'][0]['cells'][0],entry['cells'][0])
        self.assertNotEqual(after['review_status'],'complete')
        self.assertGreater(after['draft_revision'],packet['draft_revision'])
        self.assertEqual(len(self.f.review_calls),1);self.assertEqual(self.f.provider_calls,[])

    def material_review(self):
        def response(**kwargs):
            self.f.review_calls.append(deepcopy(kwargs))
            body=json.loads(kwargs['messages'][1]['content'])
            field=next(field for source in body['sources'] for field in source['fields'])
            text=json.dumps({'findings':[{'review_ref':field['review_ref'],
                'question':'Do these supplied records establish a shared job identity?',
                'why':'A common name alone does not connect the two observations',
                'next_check':'Clarify the same-scope conclusion using the retained records','impact':'material'}]})
            return iter([NS(choices=[NS(index=0,delta=NS(content=text),finish_reason='stop')],usage=None)])
        with patch('agent.auxiliary_client.call_llm',side_effect=response):
            result=independent_review.goal_advisory_context(session_id=self.item.sid,
                goal_id=goals.goal_identity(self.item.goal),goal_text=self.item.goal.goal,
                turns_used=1,stage='exploration',intake_root=self.f.intake,native_db=self.f.db_path,enabled=True)
        self.assertTrue(result['pending_finding_ids'],result)
        inbox=host.invoke(self.case,{'action':'review_inbox',**self.context})
        return result,inbox

    def test_retained_material_inbox_finding_allows_atomic_answer_and_disposition(self):
        result,inbox=self.material_review();q=self.question()
        self.assertFalse(q.get('judgment_review_required'))
        response={'review_id':result['review_id'],'finding_id':result['pending_finding_ids'][0],
            'disposition':'scope_limited','reason':'The supplied records lack a shared job identifier',
            'limitation':'Independent matching identifiers are not present in the supplied records', 'citation_refs':[]}
        payload={'operation_id':'material-answer-correction','question_id':q['id'],'expected_revision':q['revision'],
            'answer':'The request and creation are separate observations; shared job identity remains unproven',
            'assessment':'undetermined','reason':'Clarify the current answer in response to the retained material finding',
            'review_responses':[response]}
        self.assertIn(result['pending_finding_ids'][0],json.dumps(inbox))
        malformed={**payload,'review_responses':[{**response,'review_id':'foreign-review'}]}
        self.assertEqual(self.classify('forsic_question',malformed)['purpose'],'discovery')
        selected=self.classify('forsic_question',payload)
        self.assertEqual(selected['blocker'],'review_findings_pending')
        denied,args=goals_completion.admit_tool_request(self.agent,'forsic_question',payload,'answer-finding')
        self.assertIsNone(denied)
        host.invoke(self.case,{'action':'question','payload':args,**self.context})
        after=self.question()
        self.assertEqual(after['revision'],q['revision']+1)
        self.assertEqual(after['answer'],payload['answer'])
        self.assertEqual(after['review_responses'][0]['finding_id'],response['finding_id'])
        resolved=__import__('forsic_plugin.investigation_context',fromlist=['resolve_context']).resolve_context(
            self.item.sid,intake_root=self.f.intake,native_db=self.f.db_path)
        self.assertEqual(independent_review.review_state(resolved)['pending_finding_ids'],[])
        self.assertEqual(self.classify('forsic_question',{**payload,'expected_revision':after['revision']})['purpose'],'discovery')
        self.assertEqual(len(self.f.review_calls),1);self.assertEqual(self.f.provider_calls,[])


if __name__=='__main__':unittest.main()
