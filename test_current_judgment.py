"""Pro-review reproductions through the real case contract, synthetic and offline."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from test_report_driven import fixture, mission, proposal, worked
from forsic_plugin.report_driven.host import current, invoke, find, checked, definition
from forsic_plugin.report_driven.views import compile_views
from forsic_plugin.report_driven.current_judgment import current_judgment, navigation_view
from forsic_plugin.report_driven.measurement import field_role
from forsic_plugin.notes import note


class CurrentJudgmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.case,self.sources,self.original=fixture(self.tmp.name)
        self.qid='Q-'+self.original['note_id']
    def state(self):return current(self.case)
    def q(self):return find(self.state(),'questions',self.qid)
    def call(self,action,payload):
        result=json.loads(self.case.invoke('forsic_reporting',{'action':action,'snapshot_id':self.state()['meta']['snapshot_id'],'payload':payload}))
        self.assertNotIn('error',result,result)
        return result
    def plan(self,view='full_field'):
        m=mission(self.state());m.update(required_view=view,target_proposition='Only a request string exists')
        self.call('mission',m)
        return proposal(self.state(),self.case)
    def synthesize(self):
        p=self.plan();p.pop('answer')
        p['question_update']={'answer':'Aggregate explanation A','assessment':'conflicting','reason':'Compare the records',
            'assertion_kind':'interpretation','inference_strength':'favored','assumptions':['Independent record origin'],
            'reasoning_summary':'Aggregate reason','limitations':['New aggregate limit'],
            'alternatives':['Strong alternative'],'next_checks':['New aggregate next check'],
            'reopen_conditions':['New contradicting material']}
        self.call('assess',p);return p
    def update(self,**kwargs):
        return self.call('question',dict(operation_id='correction',question_id=self.qid,expected_revision=self.q()['revision'],**kwargs))
    def test_local_support_does_not_promote_or_rewrite_aggregate(self):
        p=self.plan();p.pop('answer');p['outcome']='supports';before=self.q()
        out=self.call('assess',p);after=self.q()
        self.assertEqual(out['assessment']['outcome'],'supports');self.assertEqual(out['question_update'],{})
        for field in ('answer','assessment','revision','claim_refs','remaining_gap_ids'):
            self.assertEqual(before[field],after[field],field)
        self.assertEqual(self.state()['tests'][0]['assessment_state'],'assessed')
    def test_legacy_answer_does_not_map_local_outcome(self):
        p=self.plan();p['outcome']='supports';self.call('assess',p)
        self.assertEqual(self.q()['assessment'],'undetermined');self.assertTrue(self.q()['judgment_review_required'])
    def test_explicit_synthesis_current_view(self):
        self.synthesize();q=self.q();j=current_judgment(self.state(),q)
        self.assertEqual(q['assessment'],'conflicting');self.assertFalse(q['judgment_review_required'])
        self.assertEqual(j['judgments'][0]['inference_strength'],'favored')
        self.assertEqual(j['judgments'][0]['reasoning_summary'],'Aggregate reason')
        self.assertNotEqual(j['local_assessments'][0]['reasoning_summary'],'Aggregate reason')
        projected=json.dumps(navigation_view(self.state(),q))
        for value in ('New aggregate next check','New aggregate limit','Aggregate reason','Independent record origin','Strong alternative'):
            self.assertIn(value,projected)
        self.assertEqual(q['next_checks'],['New aggregate next check'])
    def test_retraction_preserves_definition_and_test_history_not_current_claim(self):
        self.synthesize();before=self.state();old=self.q()['claim_refs'][0]['id']
        self.update(answer='The prior link is withdrawn',reason='Counterevidence',claim_action='retract',assessment='undetermined')
        after=self.state();self.assertEqual(self.q()['definition_version'],before['questions'][0]['definition_version'])
        self.assertEqual(find(after,'claims',old)['status'],'retracted')
        self.assertEqual(definition(after['tests']),definition(before['tests']));self.assertEqual(definition(after['assessments']),definition(before['assessments']))
        self.assertEqual(self.q()['claim_refs'],[])
        for view in compile_views(after).values():
            text=json.dumps(view);self.assertNotIn('Aggregate explanation A',text);self.assertIn('The prior link is withdrawn',text)
        checked(after)
    def test_legacy_correction_clears_current_adoption_and_requires_review(self):
        self.synthesize();old=self.q()['claim_refs'][0]['id']
        note(self.case,dict(action='save',note_id=self.original['note_id'],revision=self.q()['revision'],
                            question=self.q()['question'],answer='Corrected but not assessed',correction_reason='Contradiction'))
        self.assertEqual(find(self.state(),'claims',old)['status'],'superseded')
        self.assertTrue(self.q()['judgment_review_required']);self.assertEqual(self.q()['claim_refs'],[])
        self.assertNotIn('Aggregate explanation A',json.dumps(compile_views(self.state())))
    def test_explicit_retain_preserves_selected_claim_and_tests(self):
        self.synthesize();before=self.q();tests=self.state()['tests']
        self.update(answer='Aggregate explanation A (same meaning)',reason='Wording only',claim_action='retain')
        self.assertEqual(self.q()['claim_refs'],before['claim_refs']);self.assertEqual(self.q()['assessment'],before['assessment'])
        self.assertEqual(definition(self.state()['tests']),definition(tests))
    def test_second_synthesis_replaces_only_current_claim(self):
        p=self.synthesize();old=self.q()['claim_refs'][0]['id']
        p['mission_version']=self.state()['missions'][0]['version']
        p['question_update']['answer']='Aggregate explanation B';p['question_update']['reason']='New comparison'
        self.call('assess',p)
        self.assertEqual(find(self.state(),'claims',old)['status'],'superseded')
        self.assertEqual(len(self.state()['assessments']),2)
        self.assertNotIn('Aggregate explanation A',json.dumps(compile_views(self.state())))
    def test_actual_compact_errors_have_fields_and_do_not_mutate(self):
        p=self.plan();p.update(operation_id='bad-assess',expected_revision=self.q()['revision'],assessment_kind='invented')
        before=self.q();out=json.loads(self.case.invoke('forsic_reporting',{'action':'assess','payload':p}))
        for text in ('unknown_fields','assessment_kind','payload','allowed_fields','No assessment was saved'):
            self.assertIn(text,out['error'])
        self.assertEqual(self.q(),before);self.assertFalse(self.state()['assessments'])
        p.pop('assessment_kind');self.call('assess',p);self.assertEqual(len(self.state()['assessments']),1)
    def test_compact_reason_not_outer_or_legacy_field(self):
        p=dict(operation_id='reason-test',question_id=self.qid,expected_revision=self.q()['revision'],work_state='active')
        before=self.q()
        for extra in ({},{'correction_reason':'wrong API'}):
            out=json.loads(self.case.invoke('forsic_reporting',{'action':'question','reason':'outer only','payload':{**p,**extra}}))
            self.assertIn('error',out);self.assertEqual(self.q(),before)
        p['reason']='Resume this scope';self.call('question',p);self.assertEqual(self.q()['work_state'],'active')
    def test_required_view_matrix(self):
        for required,ptr,fragment,allowed in [('metadata','/path',False,True),('exact_excerpt','/path',False,False),
                ('full_field','/path',False,False),('exact_excerpt','/lines/0/text',True,True),
                ('full_field','/lines/0/text',True,False),('full_field','/lines/0/text',False,True)]:
            with self.subTest(required=required,pointer=ptr,fragment=fragment),tempfile.TemporaryDirectory() as t:
                c,_,_=fixture(t);s=current(c);m=mission(s);m['required_view']=required
                invoke(c,{'action':'mission','snapshot_id':s['meta']['snapshot_id'],'payload':m})
                s=current(c);p=proposal(s,c)
                for cit in p['citations']:
                    data=c.event(cit['source_id'])['data'];cit['pointer']=ptr
                    cit['literal']=data['path'] if ptr=='/path' else data['lines'][0]['text']
                    if fragment:cit['literal']=cit['literal'][:5]
                args={'action':'assess','snapshot_id':s['meta']['snapshot_id'],'payload':p}
                if allowed:invoke(c,args);self.assertTrue(current(c)['assessments'])
                else:
                    with self.assertRaisesRegex(ValueError,'not compatible'):invoke(c,args)
                    self.assertFalse(current(c)['assessments'])
    def test_input_echo_is_not_body_and_real_image_body_is_supported(self):
        for pointer in ('/search_text','/arguments/text','/context/text','/path'):
            self.assertEqual(field_role({'tool':'forsic_search'},pointer),'metadata')
        self.assertEqual(field_role({'tool':'forsic_image_files'},'/lines/0/text'),'body')
    def test_search_presentation_preserves_retained_version_and_skips(self):
        root=Path(self.tmp.name)/'evidence';(root/'binary').write_bytes(b'\x00\xff');(root/'link').symlink_to(root/'binary')
        result=json.loads(self.case.invoke('forsic_search',{'path':'.','text':'absent'}))
        self.assertFalse(result['measurement']['page']['coverage_complete'])
        self.assertGreaterEqual(result['measurement']['page']['files_skipped'],2)
        saved=self.case.event(result['evidence_id'])['data'];self.assertNotIn('measurement',saved)
        source=json.loads(self.case.invoke('forsic_reporting',{'action':'source','source_id':result['evidence_id']}))
        self.assertEqual(source['measurement'],result['measurement'])
    def test_long_judgment_is_omitted_with_pointer_not_cut_into_assertion(self):
        self.update(answer='affirmative prefix '*80+'BUT RETRACTED',reason='full caveat')
        view=navigation_view(self.state(),self.q())
        self.assertGreater(view['omitted_details'],0);self.assertIn('omitted',view['answer_is_model_assessment'])
        self.assertTrue(view['detail_pointer'])

class JudgmentHookTests(unittest.TestCase):
    def test_latest_synthesis_is_shared_by_goal_compaction_and_closed_index(self):
        import test_investigation_context as fixture_module
        from forsic_plugin import investigation_context as context
        from hermes_cli.goals import goal_identity
        x=fixture_module.InvestigationContextTests();x.setUp();self.addCleanup(x.doCleanups)
        case=x.case;s=current(case);qid=s['questions'][0]['id']
        owner={'_goal_id':goal_identity(x.goal),'_session_id':x.sid}
        m=mission(s);invoke(case,dict(action='mission',snapshot_id=s['meta']['snapshot_id'],payload=m,**owner))
        s=current(case);p=proposal(s,case);p.pop('answer')
        p['question_update']={'answer':'Bounded uncertain answer','assessment':'undetermined','reason':'Aggregate update',
            'limitations':['Current limitation'],'next_checks':['Current next check'],
            'reasoning_summary':'Current reason','assumptions':['Current assumption']}
        invoke(case,dict(action='assess',snapshot_id=s['meta']['snapshot_id'],payload=p,**owner))
        goal=x.prepare();compact=context.post_context_compaction(**x.kwargs())
        self.assertNotEqual(goal['status'],'unavailable');self.assertEqual(compact['status'],'ready')
        for packet in (goal,compact):
            text=packet['context'];body=json.loads(text)
            self.assertTrue(body['active_questions'])
            for val in ('Current limitation','Current next check','Current reason','Current assumption'):
                self.assertIn(val,text)
            self.assertLessEqual(len(text.encode()),5000)
        s=current(case);q=find(s,'questions',qid)
        payload={'operation_id':'close-after-review','question_id':qid,'expected_revision':q['revision'],
                 'work_state':'scoped_closed','reason':'No valuable available discrimination remains',
                 'reopen_conditions':['New distinguishing data'],
                 'gap_dispositions':[{'gap_id':g['id'],'disposition':'assessed_unresolved','reason':'No available discriminator',
                                      'reopen_conditions':['New distinguishing data']} for g in s['gaps']]}
        invoke(case,dict(action='question',payload=payload,**owner))
        prepared=x.prepare();self.assertEqual(prepared['status'],'ready',prepared)
        row=json.loads(prepared['context'])['closed_index'][0]
        self.assertEqual(row['assessment'],'undetermined');self.assertEqual(row['answer_is_model_assessment'],'Bounded uncertain answer')
        self.assertEqual(row['revision'],find(current(case),'questions',qid)['revision'])
        self.assertTrue(row['judgments']);self.assertTrue(row['reopen'])
        self.assertEqual(current(case)['status']['investigation'],'supported_scope_closed')
