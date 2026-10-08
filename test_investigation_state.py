"""Offline event/state invariants; only synthetic files, no model or network."""
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch

from test_report_driven import fixture, worked
from forsic_plugin.notes import note
from forsic_plugin.report_driven.host import current, invoke, find, checked
from forsic_plugin.report_driven.investigation_state import validate_execution_context


class InvestigationStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.case,self.results,self.original=fixture(self.tmp.name)
        self.qid='Q-'+self.original['note_id']
    def tearDown(self):self.tmp.cleanup()
    def q(self):return find(current(self.case),'questions',self.qid)
    def question(self,op,**fields):
        return invoke(self.case,{'action':'question','payload':dict(operation_id=op,question_id=self.qid,expected_revision=self.q()['revision'],**fields)})
    def mission(self,op='plan',**overrides):
        s=current(self.case);q=self.q()
        p=dict(operation_id=op,question_id=q['id'],expected_revision=q['revision'],
               gap_id=next(g['id'] for g in s['gaps'] if g['question_ref']['id']==q['id']),
               why_it_matters='Compare the actual request record',tool_name='forsic_read',
               tool_arguments={'path':'request.log'},inconclusive_rule='Unrelated identifiers cannot link the records',
               limits=['No conclusion about authorization'],reopen_conditions=['New matching identifier'])
        p.update(overrides)
        return invoke(self.case,{'action':'mission','payload':p})
    def linked_result(self,mission,data=None,args=None,tool=None):
        m=mission['mission'];tool=tool or m['proposed_tool_name'];args=args or m['tool_arguments']
        context=validate_execution_context(self.case,m['id'],m['version'],tool,args)
        sid=self.case.record('tool_start',{'tool':tool,'arguments':args,'mission_context':context})
        if data is None:data={'path':'request.log','lines':[{'line':1,'text':'job=maintenance requested target=/opt/task'}]}
        return self.case.record('tool_result',{'tool':tool,'started_id':sid,'mission_context':context,**data})
    def assess(self,mid,rid,op='assessment',**overrides):
        s=current(self.case);m=find(s,'missions',mid);src=find(s,'sources',rid)
        data=self.case.event(rid)['data'];citations=[]
        if data.get('lines'):
            citations=[{'source_id':rid,'source_version':src['version'],'pointer':'/lines/0/text','literal':data['lines'][0]['text'],'byte_start':0}]
        p=dict(operation_id=op,expected_revision=self.q()['revision'],mission_id=mid,mission_version=m['version'],result_id=rid,result_version=src['version'],
               outcome='found',reasoning_summary='The field records a request only',answer='A request is recorded; actual execution remains uncertain',
               citations=citations,limitations=['Execution and actor unknown'],next_check='Read a record with a matching identifier')
        p.update(overrides)
        return invoke(self.case,{'action':'assess','payload':p})
    def test_note_answer_keeps_mission_and_evaluation_definition(self):
        s=worked(self.case);ids=[a['id'] for a in s['assessments']];definition=self.q()['definition_version']
        saved=note(self.case,{'action':'get','note_id':self.original['note_id']})['note']
        note(self.case,{**saved,'action':'save','answer':'Revised wording, same proposition'})
        s=current(self.case)
        self.assertEqual([a['id'] for a in s['assessments']],ids)
        self.assertEqual(self.q()['definition_version'],definition)
        self.assertEqual(self.q()['answer'],'Revised wording, same proposition')
        self.assertEqual(note(self.case,{'action':'get','note_id':self.original['note_id']})['note']['answer'],self.q()['answer'])
        checked(s)
    def test_definition_change_invalidates_only_affected_question(self):
        worked(self.case)
        other=note(self.case,{'action':'save','question':'Other question','answer':'Unknown','scope':'Other path'})['note']
        before=find(current(self.case),'questions','Q-'+other['note_id'])
        self.question('definition',scope='A narrower question scope',reason='Explicit scope correction')
        s=current(self.case)
        self.assertFalse(s['assessments']);self.assertFalse(s['missions'])
        self.assertEqual(find(s,'questions',before['id'])['definition_version'],before['definition_version'])
        self.assertEqual(self.q()['scope'],'A narrower question scope')
        checked(s)
    def test_receipts_do_not_change_content_revision(self):
        before=current(self.case)
        for kind in ('turn_start','turn_complete','pre_auxiliary_call','post_auxiliary_call'):
            self.case.record(kind,{'status':'synthetic'})
        after=current(self.case)
        self.assertEqual(before['meta']['content_revision'],after['meta']['content_revision'])
        self.assertEqual(before['meta']['snapshot_id'],after['meta']['snapshot_id'])
    def test_compact_question_retry_is_idempotent_and_payload_change_rejected(self):
        args={'action':'question','payload':{'operation_id':'same','question_id':self.qid,'expected_revision':self.q()['revision'],'answer':'Updated answer'}}
        result=invoke(self.case,args);count=len(self.case.events(1000));retry=invoke(self.case,args)
        self.assertEqual(result['receipt'],retry['receipt']);self.assertEqual(len(self.case.events(1000)),count)
        args['payload']['answer']='Different answer'
        with self.assertRaisesRegex(ValueError,'different mutation'):invoke(self.case,args)
    def test_stale_question_rejected_but_unrelated_question_not_a_conflict(self):
        version=self.q()['revision']
        note(self.case,{'action':'save','question':'Other','answer':'Pending'})
        invoke(self.case,{'action':'question','payload':{'operation_id':'a','question_id':self.qid,'expected_revision':version,'answer':'Current'}})
        with self.assertRaisesRegex(ValueError,'revision conflict'):
            invoke(self.case,{'action':'question','payload':{'operation_id':'b','question_id':self.qid,'expected_revision':version,'answer':'Stale'}})
    def test_planned_native_result_to_completed_assessment(self):
        m=self.mission();rid=self.linked_result(m)
        self.assertEqual(find(current(self.case),'missions',m['mission']['id'])['state'],'unassessed')
        self.assess(m['mission']['id'],rid)
        s=current(self.case);self.assertEqual(find(s,'missions',m['mission']['id'])['state'],'completed')
        self.assertNotEqual(self.q()['work_state'],'scoped_closed')
        self.assertEqual(note(self.case,{'action':'get','note_id':self.original['note_id']})['note']['answer'],self.q()['answer'])
        checked(s)
    def test_exact_plan_and_version_admission(self):
        m=self.mission()['mission']
        with self.assertRaisesRegex(ValueError,'exact arguments'):
            validate_execution_context(self.case,m['id'],m['version'],'forsic_read',{'path':'process.log'})
        with self.assertRaisesRegex(ValueError,'Stale mission'):
            validate_execution_context(self.case,m['id'],'old','forsic_read',m['tool_arguments'])
        self.question('wording',answer='Answer wording changed')
        self.assertEqual(find(current(self.case),'missions',m['id'])['version'],m['version'])
        validate_execution_context(self.case,m['id'],m['version'],'forsic_read',m['tool_arguments'])
    def test_unbound_or_mismatched_result_cannot_complete_plan(self):
        m=self.mission()['mission']
        with self.assertRaisesRegex(ValueError,'no native execution receipt'):
            self.assess(m['id'],self.results[0]['evidence_id'])
    def test_queued_verification_not_completed_and_same_path_poll(self):
        m=self.mission(tool_name='forsic_verify',tool_arguments={'path':'image.E01','action':'start'})
        rid=self.linked_result(m,{'status':'queued','verification_complete':False,'job_id':'synthetic-job'})
        self.assertEqual(find(current(self.case),'missions',m['mission']['id'])['state'],'running')
        with self.assertRaisesRegex(ValueError,'Queued/running'):
            self.assess(m['mission']['id'],rid,outcome='inconclusive')
        ctx=validate_execution_context(self.case,m['mission']['id'],m['mission']['version'],'forsic_verify',{'path':'image.E01','action':'status'})
        self.assertEqual(ctx['mission_id'],m['mission']['id'])
        with self.assertRaises(ValueError):validate_execution_context(self.case,m['mission']['id'],m['mission']['version'],'forsic_verify',{'path':'different.E01','action':'status'})
    def test_stale_result_and_citation_versions_not_upgraded(self):
        m=self.mission();rid=self.linked_result(m)
        with self.assertRaisesRegex(ValueError,'Stale'):
            self.assess(m['mission']['id'],rid,result_version='stale')
        src=find(current(self.case),'sources',rid)
        with self.assertRaisesRegex(ValueError,'Stale'):
            self.assess(m['mission']['id'],rid,citations=[{'source_id':rid,'source_version':'stale','pointer':'/lines/0/text','literal':'job=maintenance requested target=/opt/task','byte_start':0}])
        self.assertFalse(current(self.case)['assessments'])
    def test_scope_close_defer_reopen_are_explicit_and_preserved(self):
        self.assertEqual(self.q()['work_state'],'open')
        self.question('defer',work_state='budget_deferred',reason='Available work exceeds the agreed budget',reopen_conditions=['Additional budget'])
        self.assertTrue(self.q()['deferred_reason'])
        self.question('reopen',work_state='open',reason='Budget renewed')
        s=current(self.case)
        dispositions=[{'gap_id':g['id'],'disposition':'assessed_unresolved','reason':'No discriminator remains in the retained scope','reopen_conditions':['New scoped record']} for g in s['gaps'] if g['question_ref']['id']==self.qid]
        self.question('close',work_state='scoped_closed',reason='Retained scope exhausted; answer remains uncertain',reopen_conditions=['New scoped record'],gap_dispositions=dispositions)
        self.assertEqual(self.q()['work_state'],'scoped_closed');self.assertEqual(self.q()['assessment'],'undetermined')
        saved=note(self.case,{'action':'get','note_id':self.original['note_id']})['note']
        note(self.case,{**saved,'action':'save','answer':'A wording correction'})
        self.assertEqual(self.q()['work_state'],'scoped_closed')
        self.question('new-evidence',work_state='active',reason='New evidence within scope')
        self.assertEqual(self.q()['work_state'],'active');self.assertIsNone(self.q()['closure_rationale'])
        checked(current(self.case))
    def test_goal_owner_cannot_be_reassigned(self):
        invoke(self.case,{'action':'question','_goal_id':'goal-a','_session_id':'session-a','payload':{'operation_id':'adopt','question_id':self.qid,'expected_revision':self.q()['revision'],'answer':'Adopt this question'}})
        self.assertEqual(self.q()['goal_id'],'goal-a')
        with self.assertRaisesRegex(ValueError,'another native goal'):
            invoke(self.case,{'action':'question','_goal_id':'goal-b','_session_id':'session-a','payload':{'operation_id':'wrong-owner','question_id':self.qid,'expected_revision':self.q()['revision'],'answer':'Overwrite'}})
    def test_note_idempotent_retry(self):
        saved=note(self.case,{'action':'get','note_id':self.original['note_id']})['note']
        args={**saved,'action':'save','operation_id':'note-retry','answer':'Retry answer'}
        first=note(self.case,args);again=note(self.case,args)
        self.assertEqual(first,again)
    def test_failed_verification_cannot_support_conclusion(self):
        m=self.mission(tool_name='forsic_verify',tool_arguments={'path':'image.E01','action':'start'},support_rule='Complete verification succeeds')
        rid=self.linked_result(m,{'status':'completed','verification_complete':False,'job_id':'synthetic-job','exit_code':0})
        with self.assertRaises(ValueError):self.assess(m['mission']['id'],rid,outcome='supports')
        self.assertEqual(find(current(self.case),'missions',m['mission']['id'])['state'],'failed')

    def test_mutation_replay_returns_identical_mission_receipt(self):
        s=current(self.case);q=self.q()
        payload={'operation_id':'mission-idempotency','question_id':q['id'],'expected_revision':q['revision'],
            'gap_id':s['gaps'][0]['id'],'why_it_matters':'A discrimination test','tool_name':'forsic_read','tool_arguments':{'path':'request.log'},
            'inconclusive_rule':'No linked identity','limits':['Only request record'],'reopen_conditions':['New identity']}
        args={'action':'mission','payload':payload}
        first=invoke(self.case,args);second=invoke(self.case,args)
        self.assertEqual(first,second)
        payload['why_it_matters']='Different test'
        with self.assertRaisesRegex(ValueError,'different mutation'):invoke(self.case,args)
    def test_linked_native_tool_call_and_duplicate_admission(self):
        m=self.mission()['mission']
        result=json.loads(self.case.invoke('forsic_read',{'path':'request.log','mission_id':m['id'],'mission_version':m['version']}))
        self.assertNotIn('error',result)
        self.assertEqual(result['mission_context']['mission_id'],m['id'])
        start=self.case.event(result['started_id'])
        self.assertEqual(start['data']['mission_context'],result['mission_context'])
        self.assertEqual(find(current(self.case),'missions',m['id'])['result_ids'],[result['evidence_id']])
        with patch.object(self.case,'read',side_effect=AssertionError('duplicate physical read')):
            duplicate=json.loads(self.case.invoke('forsic_read',{'path':'request.log','mission_id':m['id'],'mission_version':m['version']}))
        self.assertIn('already returned',duplicate['error'])
    def test_pending_native_call_cannot_be_duplicated(self):
        m=self.mission()['mission'];args=m['tool_arguments']
        ctx=validate_execution_context(self.case,m['id'],m['version'],'forsic_read',args)
        self.case.record('tool_start',{'tool':'forsic_read','arguments':args,'mission_context':ctx})
        with self.assertRaisesRegex(ValueError,'running native call'):
            validate_execution_context(self.case,m['id'],m['version'],'forsic_read',args)
    def test_changed_gap_invalidates_only_dependent_mission(self):
        m=self.mission()['mission'];before=len(self.case.events(1000))
        saved=note(self.case,{'action':'get','note_id':self.original['note_id']})['note']
        note(self.case,{**saved,'action':'save','critical_gaps':['New obligation'],'gaps':[]})
        s=current(self.case)
        self.assertFalse(s['missions']);self.assertGreater(len(self.case.events(1000)),before)
        checked(s)
    def test_strict_pointer_and_search_coverage_metadata(self):
        from forsic_plugin.report_driven.host import pointer, source_record
        for path in ('/lines/-1/text','/lines/01/text','/lines/+0/text','/bad~2escape'):
            with self.subTest(path=path), self.assertRaises(ValueError):pointer({'lines':[{'text':'record'}]},path)
        data={'tool':'forsic_search','path':'.','next_cursor':{'private':'large token'},'files_skipped':2,'scan_exhausted':False,
              'stop_reason':'budget','skips':[{'reason':'read_failed'},{'reason':'directory_limit'}]}
        coverage=json.loads(source_record({'id':'synthetic-source','data':data})['coverage'])
        self.assertTrue(coverage['has_next_cursor']);self.assertEqual(coverage['files_skipped'],2)
        self.assertEqual(coverage['failed_count'],1);self.assertFalse(coverage['scan_exhausted'])
        self.assertNotIn('large token',json.dumps(coverage))
    def test_definition_cannot_be_replaced_and_closed_in_one_step(self):
        with self.assertRaisesRegex(ValueError,'open question'):
            self.question('scope-widen',scope='A different scope',work_state='scoped_closed',reason='Cannot carry old work across scope',reopen_conditions=['New records'])
    def test_goal_owner_applies_to_legacy_assessment_path(self):
        from test_report_driven import mission
        self.question('priority',priority='material')
        invoke(self.case,{'action':'question','_goal_id':'goal-a','payload':{'operation_id':'owner','question_id':self.qid,'expected_revision':self.q()['revision'],'answer':'Owned answer'}})
        s=current(self.case)
        with self.assertRaisesRegex(ValueError,'another native goal'):
            invoke(self.case,{'action':'mission','_goal_id':'goal-b','snapshot_id':s['meta']['snapshot_id'],'payload':mission(s)})

    def test_alternative_and_gap_reorder_keeps_applicable_missions(self):
        saved=note(self.case,{'action':'get','note_id':self.original['note_id']})['note']
        saved=note(self.case,{**saved,'action':'save','alternatives':['Explanation one','Explanation two'],'critical_gaps':['Question A','Question B']})['note']
        m=self.mission()['mission'];before=m['version']
        note(self.case,{**saved,'action':'save','alternatives':list(reversed(saved['alternatives'])),'critical_gaps':list(reversed(saved['critical_gaps']))})
        after=find(current(self.case),'missions',m['id'])
        self.assertEqual(after['version'],before);checked(current(self.case))
    def test_changed_approved_case_scope_changes_content_revision(self):
        before=current(self.case)['meta']['content_revision']
        self.case.config['scope']='New approved evidence scope'
        self.assertNotEqual(current(self.case)['meta']['content_revision'],before)
    def test_question_concurrent_writers_cannot_overwrite(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier=Barrier(2);revision=self.q()['revision']
        def update(index):
            barrier.wait(timeout=5)
            try:return invoke(self.case,{'action':'question','payload':{'operation_id':'race-'+str(index),'question_id':self.qid,'expected_revision':revision,'answer':'Writer '+str(index)}})
            except ValueError as exc:return {'conflict':str(exc)}
        with ThreadPoolExecutor(max_workers=2) as workers:results=list(workers.map(update,[1,2]))
        self.assertEqual(sum('receipt' in r for r in results),1)
        self.assertEqual(sum('conflict' in r for r in results),1)
        self.assertEqual(self.q()['revision'],revision+1)

    def test_goalless_caller_cannot_change_or_execute_goal_owned_question(self):
        m=self.mission()['mission']
        invoke(self.case,{'action':'question','_goal_id':'goal-a','_session_id':'conversation-root','payload':{
            'operation_id':'bind-goal','question_id':self.qid,'expected_revision':self.q()['revision'],'answer':'Bound answer'}})
        with self.assertRaisesRegex(ValueError,'another native goal'):
            validate_execution_context(self.case,m['id'],m['version'],'forsic_read',m['tool_arguments'],goal_id='',session_id='conversation-root')
        with self.assertRaisesRegex(ValueError,'another native goal'):
            invoke(self.case,{'action':'question','_goal_id':'','_session_id':'conversation-root','payload':{
                'operation_id':'erase-goal','question_id':self.qid,'expected_revision':self.q()['revision'],'answer':'Bad replacement'}})
        saved=note(self.case,{'action':'get','note_id':self.original['note_id']})['note']
        with self.assertRaisesRegex(ValueError,'another native goal'):
            note(self.case,{**saved,'action':'save','_session_id':'conversation-root','_goal_id':'','answer':'Bad legacy replacement'})
    def test_focused_question_binds_session_without_goal(self):
        invoke(self.case,{'action':'question','_session_id':'focused-root','payload':{
            'operation_id':'focused','question_id':self.qid,'expected_revision':self.q()['revision'],'answer':'Focused answer'}})
        self.assertEqual(self.q()['session_id'],'focused-root');self.assertEqual(self.q()['goal_id'],'')
        with self.assertRaisesRegex(ValueError,'another native conversation'):
            invoke(self.case,{'action':'question','_session_id':'different-root','payload':{
                'operation_id':'focused-other','question_id':self.qid,'expected_revision':self.q()['revision'],'answer':'Wrong conversation'}})
        invoke(self.case,{'action':'question','_session_id':'focused-root','payload':{
            'operation_id':'focused-current','question_id':self.qid,'expected_revision':self.q()['revision'],'answer':'Same conversation'}})
        self.assertEqual(self.q()['answer'],'Same conversation')
    def test_assessment_and_note_update_roll_back_together(self):
        m=self.mission();rid=self.linked_result(m);before=len(self.case.events(1000));answer=self.q()['answer']
        with patch('forsic_plugin.report_driven.host.save_note_locked',side_effect=ValueError('synthetic note failure')):
            with self.assertRaisesRegex(ValueError,'synthetic note failure'):self.assess(m['mission']['id'],rid)
        self.assertEqual(len(self.case.events(1000)),before);self.assertEqual(self.q()['answer'],answer)
        self.assertFalse(current(self.case)['assessments'])
    def test_actual_test_tracks_plan_running_return_and_one_assessment(self):
        m=self.mission()['mission'];state=current(self.case)
        test=find(state,'tests','T-'+m['id']);self.assertEqual(test['execution_state'],'candidate')
        args=m['tool_arguments'];ctx=validate_execution_context(self.case,m['id'],m['version'],'forsic_read',args)
        sid=self.case.record('tool_start',{'tool':'forsic_read','arguments':args,'mission_context':ctx})
        test=find(current(self.case),'tests','T-'+m['id']);self.assertEqual(test['execution_state'],'running')
        rid=self.case.record('tool_result',{'tool':'forsic_read','started_id':sid,'mission_context':ctx,'lines':[{'line':1,'text':'A request only'}]})
        test=find(current(self.case),'tests','T-'+m['id']);self.assertEqual(test['execution_state'],'returned');self.assertEqual(test['assessment_state'],'unassessed')
        self.assess(m['id'],rid)
        state=current(self.case);self.assertEqual(len(state['tests']),1)
        self.assertEqual(state['tests'][0]['assessment_state'],'assessed');self.assertEqual(state['tests'][0]['result_ref']['id'],rid)

    def test_failed_native_result_requires_limitation_assessment_before_close(self):
        m=self.mission();rid=self.linked_result(m,{'error':'Synthetic unsupported read','error_type':'ValueError'})
        dispositions=[{'gap_id':g['id'],'disposition':'assessed_unresolved','reason':'No further supported check','reopen_conditions':['A supported reader']} for g in current(self.case)['gaps'] if g['question_ref']['id']==self.qid]
        with self.assertRaisesRegex(ValueError,'unassessed native work'):
            self.question('early-close',work_state='scoped_closed',reason='No further supported check',reopen_conditions=['A supported reader'],gap_dispositions=dispositions)
        self.assess(m['mission']['id'],rid,outcome='unavailable',citations=[],answer='This reader could not inspect the requested scope',limitations=['The requested scope remains unknown'])
        self.assertEqual(find(current(self.case),'missions',m['mission']['id'])['state'],'completed')
        # No forced retry: one explicit unavailable assessment is sufficient to
        # record the failed attempt; scope disposition remains analyst-owned.
        self.assertEqual(find(current(self.case),'tests','T-'+m['mission']['id'])['assessment_state'],'assessed')


if __name__=='__main__':unittest.main()
