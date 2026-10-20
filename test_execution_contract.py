"""Actual wrapper/ledger fault boundaries with synthetic I/O; no model or Docker launch."""
import base64
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from forsic_plugin.execution_contract import NativeExecution, ExecutionFailure, execution_view
from forsic_plugin.image_process import run_parser
from forsic_plugin.loop_comparison import project
from forsic_plugin.report_driven.host import current, find
import test_mission_reference as references


class Process:
    def __init__(self, stdout=b'{}', returncode=0, error=None):
        self.stdout=stdout;self.returncode=returncode;self.error=error;self.killed=False
    def communicate(self, timeout=None):
        if self.error:
            error,self.error=self.error,None
            raise error
        return self.stdout,b''
    def poll(self):return self.returncode
    def kill(self):self.killed=True;self.returncode=-9


class ExecutionContractTests(unittest.TestCase):
    def setUp(self):
        self.x=references.MissionReferenceTests();self.x.setUp();self.addCleanup(self.x.doCleanups)
        self.case=self.x.x.case
        (self.case.root/'synthetic.e01').write_bytes(b'synthetic-not-an-image')
        self.args={**self.x.effective}
        self.clock=None

    def call(self,tool,args):return self.x.x.call(tool,args)
    def parser(self, process, cleanup=None, clock=None):
        patches=[patch('forsic_plugin.image_process.subprocess.Popen',return_value=process)]
        if cleanup is not None:
            patches.append(patch('forsic_plugin.image_process.subprocess.run',
                                 **({'side_effect':cleanup} if isinstance(cleanup,BaseException) else {'return_value':cleanup})))
        if clock is not None:patches.append(patch('forsic_plugin.image_process.time.monotonic',side_effect=clock))
        for p in patches:
            result=p.start();self.addCleanup(p.stop)
        return patches[0].target.Popen

    def timeout(self, cleanup, args=None, tool='forsic_image_read'):
        process=Process(error=subprocess.TimeoutExpired('synthetic read',300))
        self.parser(process,cleanup,clock=[0,0,301])
        return self.call(tool,self.args if args is None else args)

    def test_actual_wrapper_timeout_confirmed_once_only_retry_and_exact_arguments(self):
        saved=self.x.plan(); ref=self.x.reference(saved)
        out=self.timeout(subprocess.CompletedProcess([],0,b'',b''),ref,'forsic_image_read_mission')
        self.assertEqual(out['error_type'],'TimeoutExpired')
        self.assertEqual(out['execution']['state'],'failed')
        self.assertTrue(out['execution']['termination_confirmed'])
        self.assertEqual(out['retry_call']['arguments']['retry_of'],out['evidence_id'])
        self.assertTrue(self.case.event(out['evidence_id'])['data']['retryable_read_failure'])
        # One explicit native-owned retry. Retrying does not change the target/range.
        with patch.object(self.case,'image_files',return_value=self.x.body) as backend:
            result=self.x.invoke(out['retry_call']['arguments'],goal_status='active')
            self.assertNotIn('error',result,result);backend.assert_called_once_with(self.args)
        self.x.rejected(out['retry_call']['arguments'],goal_status='active')

    def test_cleanup_failure_preserves_timeout_and_fences_uncertain_aliases(self):
        out=self.timeout(subprocess.TimeoutExpired('cleanup',15))
        self.assertEqual(out['error_code'],'parser_timeout')
        self.assertEqual(out['execution']['state'],'completion_unknown')
        self.assertEqual(out['execution']['cleanup']['error_type'],'TimeoutExpired')
        self.assertNotIn('retry_call',out)
        self.assertIn('image parser timed out',out['error'])
        # reason and an unused read length cannot disguise the same physical work.
        with patch.object(self.case,'image_files') as backend:
            duplicate=self.call('forsic_image_files',{**self.args,'reason':'try again','length':9000})
            self.assertTrue(duplicate['execution_rejected']);backend.assert_not_called()
        self.assertEqual(duplicate['pending_started_id'],out['started_id'])
        lookup=duplicate['execution_call']
        view=self.call(lookup['tool'],lookup['arguments'])
        self.assertEqual(view['results'][0]['evidence_id'],out['evidence_id'])
        self.assertEqual(view['results'][0]['execution']['state'],'completion_unknown')
        self.assertEqual(view['process_liveness'],'not_observed')
        # A genuinely different range remains a possible next investigation action.
        with patch.object(self.case,'image_files',return_value=self.x.body) as backend:
            alternate=self.call('forsic_image_read',{**self.args,'start_line':81})
            self.assertNotIn('error',alternate,alternate);backend.assert_called_once()

    def test_malformed_output_is_retained_failure_never_a_reading_or_retry(self):
        for n,raw in enumerate((b'[]',b'{broken',b'\xff')):
            with self.subTest(raw=raw):
                self.parser(Process(raw))
                out=self.call('forsic_image_read',{**self.args,'start_line':1+n})
                self.assertEqual(out['error_code'],'parser_output_invalid')
                self.assertEqual(out['failure_stage'],'decode')
                self.assertEqual(out['execution']['state'],'failed')
                retained=self.case.event(out['evidence_id'])['data']
                self.assertEqual(base64.b64decode(retained['raw_output']['base64']),raw)
                self.assertNotIn('retry_call',out)
                self.assertNotIn('presented_spans',out)

    def test_json_parser_error_keeps_type_scope_and_cannot_self_authorize_retry(self):
        for n,kind in enumerate(('FileNotFoundError','PermissionError','TimeoutError')):
            body={'error':'synthetic parser error','error_type':kind,
                  'execution':{'state':'returned','termination_confirmed':False},'retryable_read_failure':True}
            self.parser(Process(json.dumps(body).encode()))
            out=self.call('forsic_image_read',{**self.args,'start_line':n+1})
            self.assertEqual(out['failure_stage'],'parser');self.assertEqual(out['error_type'],kind)
            self.assertEqual(out['execution']['state'],'failed');self.assertTrue(out['execution']['termination_confirmed'])
            self.assertNotIn('retryable_read_failure',out);self.assertNotIn('retry_call',out)
            if kind=='FileNotFoundError':
                self.assertEqual(out['outcome'],'not_found');self.assertIn('allocated',out['meaning'])
            else:self.assertNotIn('outcome',out)

    def test_native_cancel_before_start_and_during_process_does_not_resume(self):
        from tools.interrupt import set_interrupt,is_interrupted
        context=NativeExecution('synthetic-session','synthetic-turn','native-call','request')
        self.addCleanup(set_interrupt,False)
        set_interrupt(True)
        with patch.object(self.case,'image_files') as backend:
            out=json.loads(self.case.invoke('forsic_image_read',self.args,'synthetic-session',native_execution=context))
            self.assertEqual(out['error_code'],'cancelled_before_start');backend.assert_not_called()
        self.assertTrue(is_interrupted())
        self.assertEqual(self.case.event(out['started_id'])['data']['native_call'],context.record())
        set_interrupt(False)
        process=Process()
        def launch(*a,**k):set_interrupt(True);return process
        with patch('forsic_plugin.image_process.subprocess.Popen',side_effect=launch),patch('forsic_plugin.image_process.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'',b'')):
            cancelled=json.loads(self.case.invoke('forsic_image_read',self.args,'synthetic-session',native_execution=context))
        self.assertEqual(cancelled['execution']['state'],'cancelled')
        self.assertTrue(cancelled['execution']['termination_confirmed']);self.assertTrue(is_interrupted())
        self.assertNotIn('retry_call',cancelled)

    def test_launch_process_and_communication_failures_preserve_distinct_boundaries(self):
        with patch('forsic_plugin.image_process.subprocess.Popen',side_effect=FileNotFoundError('synthetic unavailable backend')):
            out=self.call('forsic_image_read',self.args)
        self.assertEqual(out['failure_stage'],'launch')
        self.assertEqual(out['execution']['state'],'not_started')
        self.assertNotIn('retry_call',out)
        for line,process,code in ((2,Process(returncode=125),'parser_process_failed'),
                                  (3,Process(error=OSError('synthetic pipe failure')),'parser_communication_failed')):
            with self.subTest(code=code):
                self.parser(process,subprocess.CompletedProcess([],1,b'',b'synthetic cleanup uncertainty'))
                out=self.call('forsic_image_read',{**self.args,'start_line':line})
                self.assertEqual(out['error_code'],code)
                self.assertEqual(out['failure_stage'],'execution')
                self.assertEqual(out['execution']['state'],'completion_unknown')
                self.assertFalse(out['execution']['termination_confirmed'])
                self.assertNotIn('retry_call',out)

    def test_keyboard_interrupt_persists_cleanup_outcome_and_propagates(self):
        self.parser(Process(error=KeyboardInterrupt()),subprocess.CompletedProcess([],0,b'',b''))
        with self.assertRaises(KeyboardInterrupt):
            self.case.invoke('forsic_image_read',self.args,'synthetic-session')
        result=self.case.events(1)[0]
        self.assertEqual(result['kind'],'tool_result')
        self.assertEqual(result['data']['execution']['state'],'cancelled')
        self.assertTrue(result['data']['execution']['termination_confirmed'])
        self.assertNotIn('retryable_read_failure',result['data'])

    def test_result_commit_failure_lookup_and_response_loss_never_relaunch(self):
        original=self.case.record
        for persisted in (False,True):
            args={**self.args,'start_line':81 if persisted else 1}
            def record(*a,**kw):
                if persisted:original(*a,**kw)
                raise sqlite3.OperationalError('synthetic commit acknowledgement loss')
            with patch.object(self.case,'image_files',return_value=self.x.body) as backend,patch.object(self.case,'record',side_effect=record):
                out=self.call('forsic_image_read',args)
                backend.assert_called_once()
            self.assertEqual(out['failure_stage'],'result_commit')
            self.assertEqual(out['error_code'],'result_commit_unknown')
            with patch.object(self.case,'image_files') as backend:
                view=self.call('forsic_reporting',out['execution_call']['arguments'])
                backend.assert_not_called()
            self.assertEqual(view['result_commit'],'retained' if persisted else 'not_observed')
            if persisted:
                self.assertEqual(len(view['results']),1)
                self.assertEqual(self.case.event(view['results'][0]['evidence_id'])['data']['lines'],self.x.body['lines'])
            else:
                with patch.object(self.case,'image_files') as backend:
                    duplicate=self.call('forsic_image_read',args)
                    self.assertEqual(duplicate['error_code'],'execution_completion_unknown');backend.assert_not_called()

    def test_abrupt_exit_unknown_lookup_ownership_and_range_defaults(self):
        class Abrupt(BaseException):pass
        with patch.object(self.case,'image_files',side_effect=Abrupt):
            with self.assertRaises(Abrupt):self.call('forsic_image_read',self.args)
        with patch.object(self.case,'image_files') as backend:
            args={k:v for k,v in self.args.items() if k not in ('start_line','length')}
            out=self.call('forsic_image_read',args)
            self.assertEqual(out['error_code'],'execution_completion_unknown');backend.assert_not_called()
        with self.assertRaises(ValueError):execution_view(self.case,out['pending_started_id'],'other-session')
        view=execution_view(self.case,out['pending_started_id'],'synthetic-session')
        self.assertEqual(view['completion'],'unknown');self.assertEqual(view['process_liveness'],'not_observed')

    def test_successful_real_subprocess_and_comparison_keep_semantic_boundaries(self):
        # Safe synthetic helper, not evidence execution or a model call.
        body=run_parser([sys.executable,'-c','import json; print(json.dumps({"lines": [{"line": 1, "text": "synthetic"}]}))'],'unused-success-container')
        self.assertEqual(body['lines'][0]['text'],'synthetic');self.assertEqual(body['execution']['state'],'returned')
        a={'evidence_id':'a',**body}; b={'evidence_id':'b',**body,'execution':{**body['execution'],'container':'other'}}
        self.assertEqual(project('forsic_image_read',self.args,json.dumps(a))['result'],project('forsic_image_read',self.args,json.dumps(b))['result'])
        b['execution']['state']='completion_unknown';b['execution']['termination_confirmed']=False
        self.assertNotEqual(project('forsic_image_read',self.args,json.dumps(a))['result'],project('forsic_image_read',self.args,json.dumps(b))['result'])
        self.assertTrue(project('forsic_reporting',{'action':'execution'})['repeatable'])
        self.assertNotIn('idempotent',project('forsic_question',{}))

if __name__=='__main__':unittest.main()
