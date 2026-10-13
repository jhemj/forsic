"""Synthetic native-handler recovery, identity and replay fences; no model/network."""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch
import test_mission_reference as references
import test_efficiency_contracts as efficiency
from forsic_plugin.report_driven.host import current, find
from forsic_plugin.loop_comparison import project

class RecoveryTests(unittest.TestCase):
    setUp=references.MissionReferenceTests.setUp
    plan=references.MissionReferenceTests.plan
    reference=staticmethod(references.MissionReferenceTests.reference)
    invoke=references.MissionReferenceTests.invoke
    rejected=references.MissionReferenceTests.rejected
    # Inherit the existing ownership, crash-window and concurrency contracts.
    def test_public_plan_alias_executes_canonical_reader(self):
        saved=self.plan(tool_name='forsic_image_read')
        self.assertEqual(find(current(self.x.case),'missions',saved['mission']['id'])['proposed_tool_name'],'forsic_image_files')
        with patch.object(self.x.case,'image_files',return_value=self.body) as backend:
            result=self.x.call(saved['next_call']['tool'],saved['next_call']['arguments'])
        backend.assert_called_once_with(self.effective)
        self.assertNotIn('error',result)
        receipt=self.x.case.event(saved['started_id'])['data']
        self.assertEqual(receipt['submitted_arguments']['payload']['tool_name'],'forsic_image_read')
        self.assertEqual(receipt['arguments']['payload']['tool_name'],'forsic_image_files')

    def test_confirmed_transient_failure_retry_preserves_mission_and_results(self):
        saved=self.plan();ref=self.reference(saved)
        with patch.object(self.x.case,'image_files',side_effect=TimeoutError('synthetic transient read')):
            failed=self.x.call('forsic_image_read_mission',ref)
        retry=failed['retry_call']['arguments'];self.assertEqual(retry['retry_of'],failed['evidence_id'])
        original=deepcopy(retry)
        # Without active native ownership a returned retry token grants no execution.
        self.rejected(retry)
        with patch.object(self.x.case,'image_files',return_value=self.body) as backend:
            ok=self.invoke(retry,goal_status='active')
            self.assertNotIn('error',ok,ok);backend.assert_called_once_with(self.effective)
        self.assertEqual(retry,original)
        mission=find(current(self.x.case),'missions',saved['mission']['id'])
        self.assertEqual(mission['state'],'unassessed')
        self.assertEqual(mission['result_ids'],[failed['evidence_id'],ok['evidence_id']])
        self.rejected(retry,goal_status='active')

    def test_retry_budget_and_changed_inputs_do_not_execute(self):
        saved=self.plan();ref=self.reference(saved)
        with patch.object(self.x.case,'image_files',side_effect=TimeoutError('synthetic timeout')):
            fail=self.x.call('forsic_image_read_mission',ref)
            second=self.invoke(fail['retry_call']['arguments'],goal_status='active')
        self.assertNotIn('retry_call',second)
        self.rejected({**ref,'retry_of':second['evidence_id']},goal_status='active')
        self.rejected({**ref,'retry_of':fail['evidence_id'],'file_path':'/different'},goal_status='active')

    def test_retry_admission_is_atomic_under_concurrent_calls(self):
        from concurrent.futures import ThreadPoolExecutor
        saved=self.plan();ref=self.reference(saved)
        with patch.object(self.x.case,'image_files',side_effect=TimeoutError('temporary')):
            failed=self.x.call('forsic_image_read_mission',ref)
        retry=failed['retry_call']['arguments']
        with patch.object(self.x.case,'image_files',return_value=self.body) as backend:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:self.invoke(retry,goal_status='active'),range(2)))
            backend.assert_called_once()
        self.assertEqual(sum(not x.get('error') for x in results),1)

    def test_parser_failure_cannot_forge_retry(self):
        saved=self.plan();ref=self.reference(saved)
        with patch.object(self.x.case,'image_files',side_effect=ValueError('bad format')):
            fail=self.x.call('forsic_image_read_mission',ref)
        self.assertNotIn('retry_call',fail)
        self.rejected({**ref,'retry_of':fail['evidence_id']},goal_status='active')

class ErrorBoundaryTests(unittest.TestCase):
    setUp=efficiency.EfficiencyContracts.setUp
    call=efficiency.EfficiencyContracts.call
    plan=efficiency.EfficiencyContracts.plan
    def test_presentation_error_returns_committed_receipt_without_reexecution(self):
        with patch('forsic_plugin.model_results.mutation_view',side_effect=ValueError('bad view')):
            result=self.call('forsic_mission',self.plan())
        self.assertNotIn('error',result,result)
        self.assertEqual(result['presentation_warning']['failure_stage'],'presentation')
        self.assertEqual(len(current(self.case)['missions']),1)
        self.assertEqual(self.case.event(result['evidence_id'])['data']['mission']['id'],result['mission']['id'])

    def test_inner_measurement_failure_preserves_saved_result(self):
        with patch('forsic_plugin.report_driven.measurement.measurement_view',side_effect=ValueError('bad presentation')):
            result=self.call('forsic_read',{'path':'request.log'})
        self.assertNotIn('error',result,result)
        self.assertEqual(result['presentation_warning']['failure_stage'],'presentation')
        self.assertEqual(result['lines'],self.case.event(result['evidence_id'])['data']['lines'])

    def test_case_absence_and_corrupt_binding_are_different(self):
        with patch('forsic_plugin.intake.Intake.case',return_value=None):
            result=self.call('forsic_case',{})
        self.assertEqual(result['error_code'],'case_not_bound')
        with patch('forsic_plugin.intake.Intake.case',side_effect=ValueError('invalid binding')):
            result=self.call('forsic_case',{})
        self.assertEqual(result['error_code'],'tool_boundary_failure')
        self.assertNotIn('아직 증거',result['error'])

class LoopIntegrationTests(unittest.TestCase):
    def setUp(self):
        from tools.registry import registry
        self.registry=registry
        # Real registry adapter, no replacement hashing implementation.
        self.name='synthetic_forensic_read'
        self.registry.register(self.name,'synthetic',{'name':self.name,'parameters':{}},lambda **k:'',
            loop_comparison=lambda args,result:project('forsic_image_read',args,result))
        self.addCleanup(self.registry.deregister,self.name)

    def test_receipt_churn_and_reason_do_not_hide_repeated_body(self):
        from agent.tool_guardrails import ToolCallGuardrailController, ToolCallGuardrailConfig
        guard=ToolCallGuardrailController(ToolCallGuardrailConfig(hard_stop_enabled=True,no_progress_block_after=3))
        base={'path':'fixture.e01','action':'read','file_path':'/log','start_line':1}
        for i in range(3):
            args={**base,'reason':str(i)};result=json.dumps({'evidence_id':str(i),'started_id':str(i),'text':'x'*1000})
            guard.after_call(self.name,args,result)
            observed=guard.observe_call(self.name,args,result,tool_call_id=str(i))
            self.assertIsNone(observed.stub) # Every current receipt remains available for citation.
        self.assertTrue(guard.before_call(self.name,base).should_halt)
        self.assertFalse(guard.before_call(self.name,{**base,'start_line':81}).should_halt)

    def test_content_changes_and_nested_evidence_ids_remain_distinct(self):
        from agent.tool_guardrails import _result_hash, ToolCallSignature
        args={'path':'fixture','action':'read','file_path':'/log'}
        one=json.dumps({'evidence_id':'receipt1','rows':[{'evidence_id':'actual-evidence-A'}]})
        two=json.dumps({'evidence_id':'receipt2','rows':[{'evidence_id':'actual-evidence-B'}]})
        self.assertNotEqual(_result_hash(one,self.name,args),_result_hash(two,self.name,args))
        self.assertEqual(ToolCallSignature.from_call(self.name,{**args,'reason':'a'}),ToolCallSignature.from_call(self.name,{**args,'reason':'b'}))

    def test_plugin_registration_and_profile_scoped_comparison(self):
        from types import SimpleNamespace
        from hermes_cli.plugins import PluginContext
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override, hermes_home_key
        from agent.tool_loop_comparison import comparison
        import tempfile
        name='synthetic_profile_read'
        with tempfile.TemporaryDirectory() as root:
            a,b=[hermes_home_key(root+'/'+suffix) for suffix in ('a','b')]
            ctx=object.__new__(PluginContext)
            ctx.manifest=SimpleNamespace(name='synthetic')
            ctx._manager=SimpleNamespace(scope_key=a,_plugin_tool_names=set(),
                _track_scoped_registration=lambda *args,**kwargs:None)
            ctx.register_tool(name,'synthetic',{'name':name,'parameters':{}},lambda **kwargs:'',
                loop_comparison=lambda args,result:{'arguments':{'profile':'a'}})
            for home,expected in ((a,{'profile':'a'}),(b,None),(a,{'profile':'a'})):
                token=set_hermes_home_override(home)
                try:self.assertEqual(comparison(name,{}).get('arguments'),expected)
                finally:reset_hermes_home_override(token)
            self.registry.deregister(name,scope=a)

    def test_polling_does_not_halt_and_ordinary_tools_keep_default_identity(self):
        from agent.tool_guardrails import ToolCallGuardrailController, ToolCallGuardrailConfig, ToolCallSignature
        name='synthetic_poll'
        self.registry.register(name,'synthetic',{'name':name,'parameters':{}},lambda **k:'',
            loop_comparison=lambda args,result:project('forsic_verify',args,result))
        self.addCleanup(self.registry.deregister,name)
        guard=ToolCallGuardrailController(ToolCallGuardrailConfig(hard_stop_enabled=True,no_progress_block_after=3))
        for i in range(8):
            args={'action':'status','path':'fixture'};body=json.dumps({'evidence_id':str(i),'status':'running'})
            self.assertFalse(guard.before_call(name,args).should_halt)
            guard.after_call(name,args,body);guard.observe_call(name,args,body)
        self.assertIsNone(guard.halt_decision)
        self.assertNotEqual(ToolCallSignature.from_call('unknown',{'reason':'a'}),ToolCallSignature.from_call('unknown',{'reason':'b'}))

    def test_writes_and_polling_have_separate_policies(self):
        self.assertNotIn('idempotent',project('forsic_note',{'action':'save'}))
        self.assertNotIn('idempotent',project('forsic_reporting',{'action':'render'}))
        self.assertTrue(project('forsic_verify',{'action':'status'})['repeatable'])
        self.assertFalse(project('forsic_verify',{'action':'status'})['idempotent'])

if __name__=='__main__': unittest.main()

class NewConversationContractTests(unittest.TestCase):
    def setUp(self):
        self.x=efficiency.EfficiencyContracts();self.x.setUp();self.addCleanup(self.x.doCleanups)
    def call(self,tool,args,owner='new'):
        from unittest.mock import Mock
        from forsic_plugin import register
        with patch('forsic_plugin.investigation_context.native_goal_binding',return_value={'goal_id':'goal-'+owner,'conversation_id':'session-'+owner}):
            ctx=Mock();ctx.get_config.return_value=None;register(ctx)
            handler=next(c.kwargs['handler'] for c in ctx.register_tool.call_args_list if c.kwargs['name']==tool)
            return json.loads(handler(args,session_id='session-'+owner))
    def create(self,owner='new'):
        return self.call('forsic_question',dict(operation_id='create-'+owner,question='Which explanation fits '+owner+'?',scope='Synthetic acquisition',answer='Undetermined; retained notes need source verification',priority='material',gaps=['Actual body unreviewed']),owner)
    def test_create_uses_current_owner_and_replay_keeps_single_question(self):
        from tools.schema_sanitizer import sanitize_tool_schemas
        self.assertNotIn('forsic_question_create',self.x.schemas)
        self.assertIn('judgment',self.x.schemas['forsic_question']['parameters']['properties'])
        before=current(self.x.case);a=self.create();b=self.create();self.assertNotIn('error',a,a);self.assertEqual(a['question'],b['question'])
        q=find(current(self.x.case),'questions',a['question']['question_id'])
        self.assertEqual((q['goal_id'],q['session_id'],q['work_state'],q['assessment']),('goal-new','session-new','open','undetermined'))
        self.assertEqual(len(current(self.x.case)['questions']),len(before['questions'])+1)
    def test_create_rejects_update_fields_without_ledger_mutation(self):
        before=current(self.x.case)['questions']
        base=dict(operation_id='bad',question='A new scope?',scope='Synthetic',answer='Unknown')
        for addition in ({'expected_revision':0},{'question_id':self.x.qid},{'work_state':'active'}):
            out=self.call('forsic_question',{**base,**addition});self.assertIn('error',out)
            self.assertEqual(current(self.x.case)['questions'],before)
        self.assertNotIn('error',self.create())
    def test_foreign_question_is_readable_but_not_editable_and_new_question_is_prioritized(self):
        a=self.create('old');oldid=a['question']['question_id'];before=find(current(self.x.case),'questions',oldid)
        view=self.call('forsic_reporting',{'action':'state'})
        self.assertEqual(view['conversation_scope']['owned_question_count'],0)
        self.assertTrue(all(not q['editable_in_current_conversation'] for q in view['state']['questions']))
        rejected=self.call('forsic_question',{'operation_id':'wrong-owner','question_id':oldid,'expected_revision':before['revision'],'answer':'Do not adopt'})
        self.assertIn('another native conversation',rejected['error']);self.assertEqual(find(current(self.x.case),'questions',oldid),before)
        saved=self.create();qid=saved['question']['question_id'];view=self.call('forsic_reporting',{'action':'state'})
        self.assertEqual(view['conversation_scope']['owned_question_count'],1)
        self.assertEqual(view['state']['questions'][0]['id'],qid)
        pointer=view['state']['questions'][0]['pointer'];state=current(self.x.case)
        self.assertEqual(state['questions'][int(pointer.rsplit('/',1)[1])]['id'],qid)
        self.assertEqual(find(state,'questions',oldid),before)
