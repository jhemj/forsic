"""Native handler round trips with synthetic retained evidence; no model calls."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from forsic_plugin import register
from forsic_plugin.report_driven.host import current, find
from test_report_driven import fixture


class EfficiencyContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.case,self.results,note=fixture(self.temp.name)
        self.qid='Q-'+note['note_id']
        for target,value in [('forsic_plugin.intake.Intake.case',self.case),
            ('forsic_plugin.investigation_context.native_goal_binding',{'goal_id':'synthetic-goal','conversation_id':'synthetic-session'})]:
            mock=patch(target,return_value=value);mock.start();self.addCleanup(mock.stop)
        ctx=Mock();ctx.get_config.side_effect=lambda k: str(Path(self.temp.name)/'intake') if k=='intake_root' else None
        register(ctx)
        self.handlers={c.kwargs['name']:c.kwargs['handler'] for c in ctx.register_tool.call_args_list}
        self.schemas={c.kwargs['name']:c.kwargs['schema'] for c in ctx.register_tool.call_args_list}

    def call(self,tool,args):
        legacy={'forsic_mission_from_results':'mission_from_results','forsic_assessment_basis':'assessment_basis'}
        action=legacy.get(tool)
        if tool=='forsic_assess' and 'basis_receipt_id' in args:action='assess_basis'
        if action:
            result=json.loads(self.case.invoke('forsic_reporting',{'action':action,'payload':args},'synthetic-session',
                request_context={'goal_id':'synthetic-goal','conversation_id':'synthetic-session'}))
            if action=='assessment_basis':
                from forsic_plugin.report_driven.assessment_basis import basis_view
                return basis_view(result)
            from forsic_plugin.model_results import mutation_view
            return mutation_view(result,self.case)
        return json.loads(self.handlers[tool](args,session_id='synthetic-session'))

    def test_api_boundary_receipt_keeps_metadata_without_request_contents(self):
        ctx=Mock();ctx.get_config.return_value=None
        register(ctx)
        callback=next(c.args[1] for c in ctx.register_hook.call_args_list if c.args[0]=='pre_api_request')
        with patch.object(self.case, 'record') as record:
            callback(session_id='synthetic-session', platform='tui', api_request_id='fixture',
                     request={'body':{'messages':[{'content':'PRIVATE'}]},
                              'boundary':{'stage':'pre_sdk_merged_body','tool_count':9}})
        payload=record.call_args.args[1]
        self.assertEqual(payload['request_boundary']['tool_count'],9)
        self.assertEqual(payload['platform'],'tui')
        self.assertNotIn('PRIVATE',json.dumps(payload))

    def plan(self,**changes):
        q=find(current(self.case),'questions',self.qid)
        return dict(operation_id='plan',question_id=self.qid,expected_revision=q['revision'],gap_id=q['remaining_gap_ids'][0],
            why_it_matters='Distinguish request from execution',tool_name='forsic_read',tool_arguments={'path':'request.log'},
            inconclusive_rule='No execution receipt',limits=['No attribution'],reopen_conditions=['New independent receipt'],**changes)

    def test_invalid_native_plan_has_no_mutation_and_recovers_once(self):
        p=self.plan();before=current(self.case)
        for args in ({},{'path':None},{'path':'request.log','line_count':True},{'path':'request.log','invented':1}):
            out=self.call('forsic_mission',{**p,'tool_arguments':args})
            self.assertIn('tool_arguments',out['error'])
            self.assertEqual(current(self.case)['questions'],before['questions'])
            self.assertEqual(current(self.case)['missions'],before['missions'])
            self.assertTrue(self.case.event(out['evidence_id'])['data']['execution_rejected'])
        saved=self.call('forsic_mission',p)
        self.assertNotIn('error',saved,saved)
        self.assertNotIn('current_answer',saved['mission'])
        retry=self.call('forsic_mission',p)
        self.assertEqual(saved['receipt'],retry['receipt'])
        self.assertEqual(saved['mission'],retry['mission'])
        call=saved['next_call'];result=self.call(call['tool'],call['arguments'])
        self.assertNotIn('error',result,result)
        self.assertEqual(result['mission_context']['mission_id'],saved['mission']['id'])
        self.assertEqual(find(current(self.case),'missions',saved['mission']['id'])['state'],'unassessed')
        again=self.call('forsic_mission',p)
        self.assertNotIn('next_call',again)
        self.assertEqual(again['current_mission_state'],'unassessed')

    def test_locator_citation_copies_into_real_assessment_and_details_roundtrip(self):
        saved=self.call('forsic_mission',self.plan());call=saved['next_call']
        result=self.call(call['tool'],call['arguments'])
        located=self.call('forsic_reporting',{'action':'source','source_id':result['evidence_id'],'query':'requested'})
        citation=self.case.event(located['evidence_id'])['data']['matches'][0]['citation']
        self.assertNotIn('citation',located['matches'][0]);self.assertEqual(located['matches'][0]['role'],'body')
        q=find(current(self.case),'questions',self.qid)
        basis=self.call('forsic_assessment_basis',{'mission_id':saved['mission']['id'],
            'result_id':result['evidence_id'],'citation_refs':[located['matches'][0]['citation_ref']]})
        self.assertNotIn('error',basis,basis)
        payload=dict(operation_id='assessment',basis_receipt_id=basis['evidence_id'],
            outcome='inconclusive',reasoning_summary='A request is recorded, not an execution receipt',
            limitations=['No execution observed'],next_check='Find independent execution evidence')
        assessed=self.call('forsic_assess',payload)
        self.assertNotIn('error',assessed,assessed)
        self.assertNotIn('reasoning_summary',assessed['assessment'])
        detail=assessed['details_call'];page=self.call(detail['tool'],detail['arguments'])
        self.assertNotIn('error',page,page)
        self.assertIn(payload['reasoning_summary'],json.dumps(page))
        observation=find(current(self.case),'observations',find(current(self.case),'assessments',assessed['assessment']['id'])['observation_refs'][0]['id'])
        self.assertEqual(observation['literal'],citation['literal'])
        self.assertEqual(observation['byte_start'],citation['byte_start'])
        self.assertEqual(find(current(self.case),'questions',self.qid)['answer'],q['answer'])
        self.assertEqual(self.call('forsic_assess',payload)['receipt'],assessed['receipt'])

    def test_missing_native_path_exposes_nested_contract_and_repaired_plan(self):
        args={'action':'read','file_path':'/var/log/synthetic.log'}
        p={**self.plan(),'tool_name':'forsic_image_files','tool_arguments':args}
        before=current(self.case)
        out=self.call('forsic_mission',p)
        self.assertIn('error',out)
        contract=out['planned_tool_contract']
        self.assertEqual(contract['field_location'],'tool_arguments')
        self.assertEqual(contract['required_fields'],['path','action','file_path'])
        self.assertEqual(contract['submitted_arguments'],args)
        self.assertNotIn('path',contract['submitted_arguments'])
        native=self.schemas['forsic_image_files']['parameters']['properties']
        self.assertEqual(contract['field_schemas']['path'],native['path'])
        self.assertEqual(contract['problems'][0]['missing_fields'],['path'])
        self.assertEqual(current(self.case)['missions'],before['missions'])
        # The caller supplies an inventory path; the handler never guesses it.
        fixed={**contract['submitted_arguments'],'path':'synthetic.e01'}
        saved=self.call('forsic_mission',{**p,'tool_arguments':fixed})
        self.assertNotIn('error',saved,saved)
        self.assertEqual(saved['next_call']['tool'],'forsic_image_read_mission')
        self.assertEqual(saved['next_call']['arguments']['path'],'synthetic.e01')
        self.assertEqual(saved['next_call']['arguments'],{'path':'synthetic.e01',
            'mission_id':saved['mission']['id'],'mission_version':saved['mission']['version']})
        stored=find(current(self.case),'missions',saved['mission']['id'])
        self.assertEqual(stored['tool_arguments'],fixed)

    def test_planning_schema_preserves_native_path_meaning_and_optional_inputs(self):
        from tools.schema_sanitizer import sanitize_tool_schemas
        wire=sanitize_tool_schemas([{'type':'function','function':self.schemas['forsic_mission']}])[0]['function']['parameters']
        args=wire['properties']['tool_arguments']
        branches={branch['title']:branch for branch in args['anyOf']}
        path=branches['forsic_image_files']['properties']['path']['description']
        native=self.schemas['forsic_image_files']['parameters']['properties']['path']['description']
        self.assertEqual(native,path)
        self.assertEqual(branches['forsic_image_files']['required'],['path','action'])
        self.assertNotIn('path',args.get('required',[]))
        self.assertEqual(branches['forsic_list']['required'],[])
        # Native list permits no path; tool-specific requirements must not become global.
        saved=self.call('forsic_mission',{**self.plan(),'tool_name':'forsic_list','tool_arguments':{}})
        self.assertNotIn('error',saved,saved)
        out=self.call('forsic_mission',{**self.plan(),'operation_id':'wrong-type','tool_name':'forsic_image_files','tool_arguments':None})
        self.assertEqual(out['planned_tool_contract']['problems'][0]['expected_type'],'object')
        out=self.call('forsic_mission',{**self.plan(),'operation_id':'wrong-tool-type','tool_name':['forsic_image_files']})
        self.assertIn('error',out)
        self.assertNotIn('planned_tool_contract',out)

    def test_planning_branches_preserve_each_native_admission_contract(self):
        from forsic_plugin.notes import EVIDENCE_TOOLS
        from forsic_plugin.report_driven.investigation_state import input_problems
        from tools.schema_sanitizer import sanitize_tool_schemas
        wire=sanitize_tool_schemas([{'type':'function','function':self.schemas['forsic_mission']}])[0]['function']['parameters']
        branches={branch['title']:branch for branch in wire['properties']['tool_arguments']['anyOf']}
        self.assertEqual(set(branches),EVIDENCE_TOOLS | {'forsic_image_read'})
        self.assertEqual(set(wire['properties']['tool_name']['enum']),EVIDENCE_TOOLS | {'forsic_image_read'})
        for name,branch in branches.items():
            native=sanitize_tool_schemas([{'type':'function','function':self.schemas[name]}])[0]['function']['parameters']
            self.assertEqual(branch['required'],native['required'])
            self.assertEqual(set(branch['properties']),set(native['properties'])-{'mission_id','mission_version'})
            self.assertFalse(branch['additionalProperties'])
            for key,field in branch['properties'].items():
                if name=='forsic_image_files' and key=='action':
                    self.assertEqual(native['properties'][key],{**field,
                        'enum':[action for action in field['enum'] if action not in ('read','read_bytes')]})
                elif key!='reason':self.assertEqual(field,native['properties'][key])
        args={'action':'read','file_path':'/var/log/synthetic.log','member_index':0}
        self.assertTrue(all(input_problems(args,branch) for branch in branches.values()))
        fixed={**args,'path':'synthetic.e01'}
        self.assertFalse(input_problems(fixed,branches['forsic_image_files']))
        self.assertFalse(input_problems({},branches['forsic_list']))
        # Shape compatibility with another branch cannot bypass the chosen tool.
        before=current(self.case)
        rejected=self.call('forsic_mission',{**self.plan(),'tool_name':'forsic_image_files','tool_arguments':{}})
        self.assertIn('error',rejected)
        self.assertEqual(current(self.case)['missions'],before['missions'])
        # Retained results need no new tool name or arguments and still plan.
        retained=self.plan(result_ids=[self.results[0]['evidence_id']])
        retained.pop('tool_name');retained.pop('tool_arguments')
        saved=self.call('forsic_mission',retained)
        self.assertNotIn('error',saved,saved)

    def test_text_view_keeps_exact_retained_bytes_and_version(self):
        for raw in ('한글🙂'.encode(),b'bad\xffutf8'):
            original={'text':raw.decode('utf-8','replace'),'base64':base64.b64encode(raw).decode(),
                'byte_start':0,'byte_end':len(raw),'complete_member':True,'next_offset':None}
            with patch.object(self.case,'image_files',return_value=original):
                out=self.call('forsic_image_files',{'path':'synthetic.e01','action':'archive_read','file_path':'a.tar','member_index':0})
            self.assertNotIn('base64',out)
            self.assertEqual(out['text'],original['text'])
            self.assertEqual(out['text_matches_raw_utf8'],out['text'].encode()==raw)
            event=self.case.event(out['evidence_id'])
            self.assertEqual(base64.b64decode(event['data']['base64']),raw)
            call=out['raw_bytes_call'];page=self.call(call['tool'],call['arguments'])
            self.assertEqual(page['text'],original['base64'])
            self.assertEqual(page['source_ref'],out['source_ref'])
            self.assertTrue(page['full_field'])
        with patch.object(self.case,'image_files',return_value={'base64':'AA=='}):
            self.assertEqual(self.call('forsic_image_files',{'path':'synthetic.e01','action':'read_bytes','file_path':'body'})['base64'],'AA==')

    def test_failure_can_be_assessed_without_fabricated_body(self):
        saved=self.call('forsic_mission',{**self.plan(),'tool_arguments':{'path':'missing.log'}})
        call=saved['next_call'];result=self.call(call['tool'],call['arguments'])
        self.assertIn('error',result)
        s=current(self.case);source=find(s,'sources',result['evidence_id'])
        basis=self.call('forsic_assessment_basis',{'mission_id':saved['mission']['id'],
            'result_id':source['id'],'citation_refs':[]})
        self.assertNotIn('error',basis,basis)
        out=self.call('forsic_assess',dict(operation_id='unavailable',basis_receipt_id=basis['evidence_id'],outcome='unavailable',
            reasoning_summary='The requested check failed',limitations=['No body inspected'],next_check='Inspect available alternate evidence'))
        self.assertNotIn('error',out,out)
        s=current(self.case)
        self.assertEqual(find(s,'missions',saved['mission']['id'])['state'],'completed')
        self.assertNotEqual(find(s,'questions',self.qid)['work_state'],'scoped_closed')
        self.assertEqual(s['tests'][-1]['result_ref']['id'],source['id'])


if __name__=='__main__': unittest.main()
