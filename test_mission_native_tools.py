"""Exercise real plugin entry points with synthetic evidence; no model/network."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from forsic_plugin.evidence import Case
from forsic_plugin.intake import Intake
from forsic_plugin.report_driven.host import current, find, checked
from test_report_driven import fixture


class MissionNativeToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.case, self.results, self.original = fixture(self.root)
        self.qid = 'Q-' + self.original['note_id']

    def tearDown(self):
        self.temp.cleanup()

    def call(self, tool, args):
        result = json.loads(self.case.invoke(tool, args, 'synthetic-session', request_context={'goal_id':'synthetic-goal'}))
        self.assertNotIn('error', result, result)
        return result

    def plan(self, tool='forsic_read', arguments=None):
        q = find(current(self.case), 'questions', self.qid)
        return self.call('forsic_reporting', {'action':'mission', 'payload':{
            'operation_id':'plan-1','question_id':self.qid,'expected_revision':q['revision'],
            'gap_id':q['remaining_gap_ids'][0],
            'why_it_matters':'Distinguish recorded request from successful execution',
            'tool_name':tool,'tool_arguments':arguments or {'path':'request.log'},
            'inconclusive_rule':'A request alone cannot establish execution',
            'limits':['No actor attribution'], 'reopen_conditions':['New independent result']}})['mission']

    def test_actual_tool_start_return_assessment_and_retry(self):
        m = self.plan()
        result = self.call('forsic_read', {'path':'request.log','mission_id':m['id'],'mission_version':m['version']})
        event = self.case.event(result['evidence_id'])
        start = self.case.event(result['started_id'])
        self.assertEqual(start['data']['arguments'], {'path':'request.log'})
        self.assertEqual(event['data']['mission_context'],start['data']['mission_context'])
        s = current(self.case)
        self.assertEqual(find(s,'missions',m['id'])['state'],'unassessed')
        self.assertEqual(len(s['tests']),1)
        src = find(s,'sources',result['evidence_id'])
        payload = {'operation_id':'assess-1','expected_revision':find(s,'questions',self.qid)['revision'],
            'mission_id':m['id'],'mission_version':find(s,'missions',m['id'])['version'],
            'result_id':src['id'],'result_version':src['version'],
            'outcome':'found','reasoning_summary':'The returned line records a request only',
            'answer':'A request is recorded; execution is not established.',
            'citations':[{'source_id':src['id'],'source_version':src['version'],
                'pointer':'/lines/0/text','literal':result['lines'][0]['text'],'byte_start':0}],
            'limitations':['No execution receipt'],'next_check':'Find an independent execution record'}
        first = self.call('forsic_reporting',{'action':'assess','payload':payload})
        second = self.call('forsic_reporting',{'action':'assess','payload':payload})
        self.assertEqual(first['receipt'],second['receipt'])
        s = current(self.case)
        self.assertEqual(find(s,'missions',m['id'])['state'],'completed')
        self.assertEqual(len(s['assessments']),1)
        self.assertNotEqual(find(s,'questions',self.qid)['work_state'],'scoped_closed')
        checked(s)

    def test_assess_schema_error_is_actionable_and_preserves_state(self):
        before = current(self.case)
        cases = [({'assessment_kind':'evidence','question_revision':1}, 'Unsupported assess fields'),
                 ({'outcome':'inconclusive'}, 'Missing assess fields')]
        for payload, message in cases:
            state = current(self.case)
            out = json.loads(self.case.invoke('forsic_reporting',
                {'action':'assess','snapshot_id':state['meta']['snapshot_id'],'payload':payload}, 'synthetic-session'))
            self.assertEqual(out['error_type'], 'ValueError')
            self.assertIn(message, out['error'])
            self.assertIn('mission', out['error'])
            self.assertIn('No assessment was saved', out['error'])
            self.assertEqual(current(self.case)['questions'], before['questions'])
            self.assertEqual(current(self.case)['assessments'], before['assessments'])

    def test_source_requires_explicit_top_level_reference(self):
        out = json.loads(self.case.invoke('forsic_reporting',
            {'action':'source','payload':{'source_id':self.results[0]['evidence_id']}}, 'synthetic-session'))
        self.assertEqual(out['error_type'], 'ValueError')
        self.assertIn('top-level source_handle or source_id', out['error'])
        result = self.call('forsic_reporting', {'action':'source','source_id':self.results[0]['evidence_id']})
        self.assertEqual(result['source']['id'],self.results[0]['evidence_id'])

    def test_invalid_payload_returns_recorded_error_without_question_mutation(self):
        before = current(self.case)['questions']
        for payload in ['{"answer":"do not save"}', [], None, False]:
            out = json.loads(self.case.invoke('forsic_reporting',
                {'action':'question', 'payload':payload}, 'synthetic-session'))
            self.assertEqual(out['error_type'], 'ValueError')
            self.assertIn('payload must be a JSON object', out['error'])
            self.assertEqual(self.case.event(out['evidence_id'])['kind'], 'tool_result')
            self.assertEqual(current(self.case)['questions'], before)
        # A corrected object reaches the real question save contract.
        out = self.call('forsic_reporting', {'action':'question','payload':{
            'operation_id':'corrected-payload','question':'A bounded question',
            'scope':'Synthetic request log only','answer':'Undetermined'}})
        self.assertTrue(any(q['question']=='A bounded question' for q in current(self.case)['questions']))

    def test_empty_question_payload_recovers_through_registered_handler(self):
        from forsic_plugin import register
        ctx=Mock();ctx.get_config.side_effect=lambda key: str(self.root/'intake') if key=='intake_root' else None
        with patch('forsic_plugin.intake.Intake.case',return_value=self.case), \
             patch('forsic_plugin.investigation_context.native_goal_binding',return_value={'goal_id':'trusted'}):
            register(ctx)
            handler=next(c.kwargs['handler'] for c in ctx.register_tool.call_args_list if c.kwargs['name']=='forsic_question')
            before=current(self.case)['questions']
            for payload in ({}, {'operation_id':''}):
                out=json.loads(handler(payload,session_id='synthetic-session'))
                self.assertIn('operation_id',out['error'])
                self.assertEqual(current(self.case)['questions'],before)
            out=json.loads(handler({},session_id='synthetic-session'))
            self.assertNotIn('Example:',out['error'])
            self.assertEqual(out['input_contract']['tool'],'forsic_question')
            self.assertEqual(out['input_contract']['field_location'],'top_level')
            example={'operation_id':'repair-empty','question':'What was requested?',
                     'scope':'Acquired synthetic records','answer':'Undetermined pending source review'}
            self.assertTrue(set(example)<=set(out['input_contract']['allowed_fields']))
            saved=json.loads(handler(example,session_id='synthetic-session'))
            self.assertNotIn('error',saved,saved)
            again=json.loads(handler(example,session_id='synthetic-session'))
            self.assertEqual(saved['question'],again['question'])
            self.assertEqual(len(current(self.case)['questions']),len(before)+1)

    def test_action_fields_survive_native_schema_without_cross_action_properties(self):
        from forsic_plugin import register
        from tools.schema_sanitizer import sanitize_tool_schemas
        from forsic_plugin.report_driven.investigation_state import compact_input_schemas
        from forsic_plugin.report_driven.assessment_basis import schemas as basis_schemas
        ctx=Mock();ctx.get_config.side_effect=lambda key: str(self.root/'intake') if key=='intake_root' else None
        register(ctx)
        schemas={c.kwargs['name']:c.kwargs['schema'] for c in ctx.register_tool.call_args_list}
        for action,shape in compact_input_schemas().items():
            if action in ('question_create','mission_from_results'):continue
            if action=='assess':
                from forsic_plugin.report_driven.assessment_basis import selected_schema
                shape=selected_schema()
            schema=schemas['forsic_'+action]
            wire=sanitize_tool_schemas([{'type':'function','function':schema}])[0]['function']['parameters']
            self.assertEqual(set(wire['properties']),set(shape['properties']))
            self.assertEqual(wire['required'],shape['required'] + (['gap_id'] if action=='mission' else []))
            self.assertIn('operation_id',wire['required'])
            self.assertNotIn('payload',wire['properties'])
            self.assertFalse(wire['additionalProperties'])
        self.assertNotIn('mission_id',schemas['forsic_question']['parameters']['properties'])
        self.assertNotIn('question',schemas['forsic_assess']['parameters']['properties'])
        for field in ('mission_version','result_version','citations','basis_receipt_id'):
            self.assertNotIn(field,schemas['forsic_assess']['parameters']['properties'])
        from forsic_plugin.report_driven.assessment_basis import schemas as legacy_schemas
        prepared=legacy_schemas()['assessment_basis']
        self.assertNotIn('forsic_assessment_basis',schemas)
        self.assertEqual(prepared,basis_schemas()['assessment_basis'])
        for action in compact_input_schemas():
            self.assertNotIn(action,schemas['forsic_reporting']['parameters']['properties']['action']['enum'])

    def test_field_guidance_is_isolated_and_outcome_enum_survives_native_schema(self):
        from forsic_plugin import register
        from tools.schema_sanitizer import sanitize_tool_schemas
        from forsic_plugin.report_driven.contracts import Assessment
        ctx=Mock();ctx.get_config.side_effect=lambda key: str(self.root/'intake') if key=='intake_root' else None
        register(ctx)
        schemas={c.kwargs['name']:c.kwargs['schema'] for c in ctx.register_tool.call_args_list}
        wires={name:sanitize_tool_schemas([{'type':'function','function':schema}])[0]['function']['parameters']
               for name,schema in schemas.items()}
        hint=wires['forsic_mission']['properties']['gap_id']['description']
        for name in ('forsic_question','forsic_assess'):
            self.assertNotIn(hint,json.dumps(wires[name]))
        self.assertNotIn(hint,json.dumps(wires['forsic_mission']['properties']['why_it_matters']))
        outcome=wires['forsic_assess']['properties']['outcome']
        self.assertEqual(outcome['enum'],Assessment.model_json_schema()['properties']['outcome']['enum'])
        self.assertIn('supports',outcome['enum']);self.assertNotIn('supported',outcome['enum'])

    def test_invalid_outcome_returns_actual_allowed_values_without_saving(self):
        m=self.plan()
        result=self.call('forsic_read',{'path':'request.log','mission_id':m['id'],'mission_version':m['version']})
        before=current(self.case);src=find(before,'sources',result['evidence_id'])
        basis=self.call('forsic_reporting',{'action':'assessment_basis','payload':{
            'mission_id':m['id'],'result_id':src['id'],'citation_refs':[]}})
        p={'operation_id':'invalid-outcome','basis_receipt_id':basis['evidence_id'],'outcome':'supported',
           'reasoning_summary':'A local recorded request','limitations':['Not execution'],
           'next_check':'Execution receipt'}
        from forsic_plugin import register
        ctx=Mock();ctx.get_config.side_effect=lambda key: str(self.root/'intake') if key=='intake_root' else None
        with patch('forsic_plugin.intake.Intake.case',return_value=self.case), \
             patch('forsic_plugin.investigation_context.native_goal_binding',return_value={'goal_id':'synthetic-goal','conversation_id':'synthetic-session'}):
            register(ctx)
            handler=next(c.kwargs['handler'] for c in ctx.register_tool.call_args_list if c.kwargs['name']=='forsic_assess')
            out=json.loads(handler(p,session_id='synthetic-session'))
        self.assertIn('Invalid test evaluation',out['error'])
        self.assertIn('payload.outcome',out['error']);self.assertIn('allowed_values',out['error'])
        self.assertIn('supports',out['error']);self.assertIn('No assessment was saved',out['error'])
        self.assertEqual(current(self.case)['assessments'],before['assessments'])
        self.assertEqual(current(self.case)['questions'],before['questions'])

    def test_dedicated_handlers_use_existing_atomic_writer_and_native_execution(self):
        from forsic_plugin import register
        ctx=Mock();ctx.get_config.side_effect=lambda key: str(self.root/'intake') if key=='intake_root' else None
        binding={'goal_id':'synthetic-goal','conversation_id':'synthetic-session'}
        with patch('forsic_plugin.intake.Intake.case',return_value=self.case), \
             patch('forsic_plugin.investigation_context.native_goal_binding',return_value=binding):
            register(ctx)
            handlers={c.kwargs['name']:c.kwargs['handler'] for c in ctx.register_tool.call_args_list}
            def invoke(name,args):return json.loads(handlers[name](args,session_id='synthetic-session'))
            p={'operation_id':'new-q','question':'Is execution established?', 'scope':'Synthetic logs',
               'answer':'Undetermined','gaps':['Execution not yet tested']}
            first=invoke('forsic_question',p);self.assertNotIn('error',first,first)
            self.assertEqual(first['question'],invoke('forsic_question',p)['question'])
            self.assertIn('error',invoke('forsic_question',{**p,'answer':'Changed retry'}))
            q=find(current(self.case),'questions',first['question']['question_id'])
            self.assertEqual(q['goal_id'],'synthetic-goal')
            self.assertIn('error',invoke('forsic_question',{'operation_id':'wrong','question_id':q['id'],'expected_revision':0,'answer':'Stale'}))
            # A real failed shape: local-test fields cannot be saved as a question.
            self.assertIn('error',invoke('forsic_question',{**p,'operation_id':'mixed','support_rule':'Observed request','limits':[]}))
            self.assertIn('error',invoke('forsic_question',{'answer':'Unknown','critical_gaps':[],'gap_ids':[]}))
            planned=invoke('forsic_mission',{'operation_id':'plan','question_id':q['id'],'expected_revision':q['revision'],
                'why_it_matters':'Distinguish request from execution','tool_name':'forsic_read','tool_arguments':{'path':'request.log'},
                'inconclusive_rule':'No execution receipt','limits':['No attribution'],'reopen_conditions':['New log']})
            self.assertNotIn('error',planned,planned);m=planned['mission']
            result=invoke('forsic_read',{'path':'request.log','mission_id':m['id'],'mission_version':m['version']})
            self.assertNotIn('error',result,result);state=current(self.case);src=find(state,'sources',result['evidence_id'])
            located=invoke('forsic_reporting',{'action':'source','source_id':src['id'],
                'source_version':src['version'],'pointer':'/lines/0/text','offset':0,'limit':4096})
            self.assertNotIn('error',located,located)
            self.assertEqual(located['text'],result['lines'][0]['text'])
            assessed=invoke('forsic_assess',{'operation_id':'assessment',
                'mission_id':m['id'],'result_id':src['id'],'expected_revision':q['revision'],
                'citation_refs':[located['citation_ref']],'outcome':'inconclusive',
                'reasoning_summary':'A recorded request is not an execution receipt','limitations':['Execution unverified'],
                'next_check':'Independent execution record'})
            self.assertNotIn('error',assessed,assessed)
            state=current(self.case);self.assertEqual(find(state,'missions',m['id'])['state'],'completed')
            self.assertEqual(find(state,'questions',q['id'])['answer'],'Undetermined')
            checked(state)

    def test_legacy_missing_gap_recovers_using_current_dedicated_tool(self):
        from forsic_plugin import register
        ctx=Mock();ctx.get_config.side_effect=lambda key: str(self.root/'intake') if key=='intake_root' else None
        with patch('forsic_plugin.intake.Intake.case',return_value=self.case), \
             patch('forsic_plugin.investigation_context.native_goal_binding',return_value={'goal_id':'synthetic-goal'}):
            register(ctx)
            handlers={c.kwargs['name']:c.kwargs['handler'] for c in ctx.register_tool.call_args_list}
            before=current(self.case);q=find(before,'questions',self.qid)
            gaps=[g for g in before['gaps'] if g['question_ref']['id']==q['id']]
            self.assertGreater(len(gaps),1)
            p={'operation_id':'missing-gap','question_id':q['id'],'expected_revision':q['revision'],
               'why_it_matters':'Check request contents','tool_name':'forsic_read','tool_arguments':{'path':'request.log'},
               'inconclusive_rule':'A request does not prove execution','limits':['No actor attribution'],
               'reopen_conditions':['Independent execution receipt']}
            error=json.loads(handlers['forsic_reporting']({'action':'mission','payload':p},session_id='synthetic-session'))
            self.assertIn('Use forsic_mission',error['error'])
            self.assertEqual(current(self.case)['questions'],before['questions'])
            self.assertFalse(current(self.case)['missions'])
            error=json.loads(handlers['forsic_mission'](p,session_id='synthetic-session'))
            self.assertEqual(error['input_contract']['tool'],'forsic_mission')
            self.assertIn('gap_id',error['input_contract']['allowed_fields'])
            for g in gaps:self.assertIn(g['id'],error['error'])
            self.assertIn(before['meta']['snapshot_id'],error['error'])
            self.assertEqual(current(self.case)['questions'],before['questions'])
            self.assertFalse(current(self.case)['missions'])
            saved=json.loads(handlers[error['input_contract']['tool']]({**p,'operation_id':'corrected-gap','gap_id':gaps[0]['id']},session_id='synthetic-session'))
            self.assertNotIn('error',saved,saved);m=saved['mission']
            result=json.loads(handlers['forsic_read']({'path':'request.log','mission_id':m['id'],'mission_version':m['version']},session_id='synthetic-session'))
            self.assertNotIn('error',result,result)
            self.assertEqual(result['lines'][0]['text'],'job=maintenance requested target=/opt/task')
            wrong=json.loads(handlers['forsic_read']({'path':'process.log','mission_id':m['id'],'mission_version':m['version']},session_id='synthetic-session'))
            self.assertTrue(wrong['execution_rejected'])

    def test_mission_gap_recovery_does_not_select_another_question_gap(self):
        self.call('forsic_note',{'action':'save','question':'Other scope','answer':'Unknown','gaps':['Other gap']})
        state=current(self.case);q=find(state,'questions',self.qid)
        other=next(g for g in state['gaps'] if g['question_ref']['id']!=q['id'])
        out=json.loads(self.case.invoke('forsic_reporting',{'action':'mission','payload':{
            'operation_id':'foreign-gap','question_id':q['id'],'expected_revision':q['revision'],'gap_id':other['id'],
            'why_it_matters':'Check request','tool_name':'forsic_read','tool_arguments':{'path':'request.log'},
            'inconclusive_rule':'No execution receipt','limits':['Attribution'],'reopen_conditions':['Independent log']}},'synthetic-session'))
        self.assertIn('gap_candidates',out['error'])
        self.assertNotIn(other['id'],out['error'])
        self.assertFalse(current(self.case)['missions'])

    def test_planned_arguments_retain_native_fields_after_sanitization(self):
        from forsic_plugin import register
        from tools.schema_sanitizer import sanitize_tool_schemas
        ctx=Mock();ctx.get_config.side_effect=lambda key: str(self.root/'intake') if key=='intake_root' else None
        register(ctx)
        schemas={c.kwargs['name']:c.kwargs['schema'] for c in ctx.register_tool.call_args_list}
        wire=sanitize_tool_schemas([{'type':'function','function':schemas['forsic_mission']}])[0]['function']['parameters']
        args=wire['properties']['tool_arguments']
        branches={branch['title']:branch for branch in args['anyOf']}
        props=branches['forsic_image_files']['properties']
        for key in ('path','action','file_path','member_index','archive_scan_bytes','start_line','text'):
            self.assertIn(key,props)
        self.assertNotIn('cursor',props)
        self.assertNotIn('cursor_ref',props)  # Continue retained searches directly via next_call.
        self.assertIn('archive_list',props['action']['enum'])
        self.assertIn('archive_read',props['action']['enum'])
        self.assertNotIn('mission_id',props)
        self.assertNotIn('mission_version',props)
        self.assertEqual(branches['forsic_image_files']['required'],['path','action'])
        self.assertEqual(branches['forsic_list']['required'],[])

    def test_rejected_reference_never_executes_evidence_tool(self):
        m = self.plan()
        with patch.object(self.case,'read',side_effect=AssertionError('should not execute')):
            result=json.loads(self.case.invoke('forsic_read',{'path':'request.log','mission_id':m['id'],
                'mission_version':'stale'},'synthetic-session'))
        self.assertTrue(result['execution_rejected'])
        self.assertEqual(find(current(self.case),'missions',m['id'])['state'],'draft')

    def test_unavailable_binding_is_not_a_normal_goalless_write(self):
        before=len(current(self.case)['questions'])
        result=json.loads(self.case.invoke('forsic_note',{'action':'save','question':'Unbound','answer':'Unknown'},
            'synthetic-session',request_context={'status':'unavailable'}))
        self.assertTrue(result['execution_rejected'])
        self.assertEqual(len(current(self.case)['questions']),before)
        result=json.loads(self.case.invoke('forsic_note',{'action':'save','question':'Focused','answer':'Unknown'},
            'synthetic-session',request_context={'status':'ready','goal_id':''}))
        self.assertNotIn('error',result)

    def test_background_return_is_not_completion(self):
        m = self.plan('forsic_verify',{'path':'synthetic.E01','action':'start'})
        with patch.object(self.case,'verify',return_value={'status':'queued','job_id':'synthetic-job','verification_complete':False}):
            self.call('forsic_verify',{'path':'synthetic.E01','action':'start','mission_id':m['id'],'mission_version':m['version']})
        s=current(self.case)
        self.assertEqual(find(s,'missions',m['id'])['state'],'running')
        checked(s)

    def test_read_only_case_does_not_mutate_or_invoke(self):
        before=hashlib.sha256(self.case.db.read_bytes()).hexdigest()
        ro=Case(self.root/'case.json',read_only=True)
        current(ro)
        with self.assertRaises(ValueError):ro.invoke('forsic_read',{'path':'request.log'})
        with self.assertRaises(Exception):ro.record('invented',{})
        self.assertEqual(hashlib.sha256(self.case.db.read_bytes()).hexdigest(),before)

    def test_registered_schema_and_trusted_context(self):
        from forsic_plugin import register
        ctx=Mock();ctx.get_config.side_effect=lambda key,default=None: str(self.root/'intake') if key=='intake_root' else default
        register(ctx)
        tools={call.kwargs['name']:call.kwargs for call in ctx.register_tool.call_args_list}
        hooks={call.args[0]:call.args[1] for call in ctx.register_hook.call_args_list}
        self.assertIn('post_context_compaction',hooks)
        self.assertIn('goal_evaluation_context',hooks)
        reporting=tools['forsic_reporting']['schema']['parameters']
        self.assertNotIn('Mission',reporting.get('$defs',{}))
        self.assertIn('forsic_question',tools)
        image=tools['forsic_image_files']['schema']['parameters']['properties']
        self.assertIn('archive_list',image['action']['enum'])
        self.assertNotIn('archive_read',image['action']['enum'])
        self.assertNotIn('member_index',image)
        self.assertEqual(set(tools['forsic_archive_read']['schema']['parameters']['required']),{'listing_ref','member_index'})
        self.assertEqual(image['archive_scan_bytes']['maximum'],1073741824)
        # Judgment and review dispositions have separate typed native tools;
        # reporting keeps bounded reads and explicitly typed prose composition.
        self.assertNotIn('payload',reporting['properties'])
        self.assertIn('forsic_review_response',tools)
        self.assertIn('draft',reporting['properties'])
        self.assertEqual(set(reporting['properties']['action']['enum']),{'case','state','gaps','source','execution','review','review_inbox','compose','render','finalize'})
        self.assertNotIn('forsic_case',tools)
        self.assertIn('started_id', reporting['properties'])
        self.assertIn('mission_version',tools['forsic_read']['schema']['parameters']['properties'])
        with patch('forsic_plugin.intake.Intake.case',return_value=self.case), \
             patch('forsic_plugin.investigation_context.native_goal_binding',return_value={'goal_id':'trusted'}):
            # register captured the imported binding, so register once more inside the patch.
            ctx.reset_mock();register(ctx)
            handler=next(call.kwargs['handler'] for call in ctx.register_tool.call_args_list if call.kwargs['name']=='forsic_question')
            out=json.loads(handler({'operation_id':'trusted-context','question':'A new question','scope':'Synthetic logs','answer':'Unknown'},session_id='synthetic-session'))
        self.assertNotIn('error',out)
        q=next(q for q in current(self.case)['questions'] if q['question']=='A new question')
        self.assertEqual(q['goal_id'],'trusted')


if __name__=='__main__':unittest.main()
