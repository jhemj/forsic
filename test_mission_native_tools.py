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
        self.assertIn('top-level source_id', out['error'])
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
        self.assertIn('question',reporting['properties']['action']['enum'])
        self.assertEqual(reporting['properties']['payload']['type'], 'object')
        self.assertIn('mission_version',tools['forsic_read']['schema']['parameters']['properties'])
        with patch('forsic_plugin.intake.Intake.case',return_value=self.case), \
             patch('forsic_plugin.investigation_context.native_goal_binding',return_value={'goal_id':'trusted'}):
            # register captured the imported binding, so register once more inside the patch.
            ctx.reset_mock();register(ctx)
            handler=next(call.kwargs['handler'] for call in ctx.register_tool.call_args_list if call.kwargs['name']=='forsic_note')
            out=json.loads(handler({'action':'save','question':'A new question','answer':'Unknown',
                '_goal_id':'untrusted'},session_id='synthetic-session'))
        self.assertNotIn('error',out)
        q=next(q for q in current(self.case)['questions'] if q['question']=='A new question')
        self.assertEqual(q['goal_id'],'trusted')


if __name__=='__main__':unittest.main()
