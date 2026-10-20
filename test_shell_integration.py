"""Native shell admission/ledger/provenance with a mocked container backend only."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from forsic_plugin import register
from forsic_plugin.capabilities import capability
from forsic_plugin.execution_contract import ExecutionFailure
from forsic_plugin.report_driven import host
from test_report_driven import fixture


class ShellIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.case,_,_=fixture(self.temp.name)
        from test_environment_support import offline_environment
        if self._testMethodName != 'test_real_container_through_registered_native_tool':
            offline_environment(self)
        self.binding=dict(status='ready',goal_id='shell-goal',goal_status='active',
            requested_session_id='shell-native',session_id='shell-native',conversation_id='shell-conversation',
            case_id=self.case.config['case_id'])
        for target,value in (('forsic_plugin.intake.Intake.case',self.case),
                             ('forsic_plugin.investigation_context.native_goal_binding',self.binding)):
            p=patch(target,return_value=value);p.start();self.addCleanup(p.stop)
        ctx=Mock();ctx.get_config.side_effect=lambda key: str(Path(self.temp.name)/'intake') if key=='intake_root' else None
        register(ctx)
        self.handlers={c.kwargs['name']:c.kwargs['handler'] for c in ctx.register_tool.call_args_list}
        self.schemas={c.kwargs['name']:c.kwargs['schema'] for c in ctx.register_tool.call_args_list}

    def call(self,tool,args,**identity):
        return json.loads(self.handlers[tool](args,session_id='shell-native',**identity))

    def output(self,text='chosen marker\n',code=0,**changes):
        raw=text.encode()
        value=dict(command="printf 'chosen marker\\n'",cwd='/work',stdout=text,stderr='',
            representation='derived_command_output',original_evidence=False,coverage_not_established=True,
            raw_output=dict(stdout_base64=base64.b64encode(raw).decode(),stderr_base64='',stdout_bytes=len(raw),
                stderr_bytes=0,retained_bytes=len(raw),complete=True,truncated=False,limit_bytes=12000),
            execution=dict(state='returned',backend='docker',container='synthetic-container',exit_code=code,
                termination_confirmed=True,cleanup={'confirmed':True}),
            execution_policy={'network':'none','workspace_scope':'conversation','originals_read_only':True})
        value.update(changes)
        return value

    def test_registered_handler_keeps_native_identity_actual_args_and_derived_source(self):
        args=dict(command="printf 'chosen marker\\n'",cwd='/work',timeout_seconds=30,
                  reason='Inspect a composed command result')
        original=deepcopy(args)
        with patch('forsic_plugin.shell_workspace.invoke',return_value=self.output()) as backend:
            out=self.call('forsic_shell',args,turn_id='native-turn',tool_call_id='native-call',api_request_id='native-request')
        self.assertNotIn('error',out,out);self.assertEqual(args,original)
        backend.assert_called_once()
        self.assertEqual(backend.call_args.args[:3],(self.case,{k:v for k,v in args.items() if k!='reason'},'shell-conversation'))
        native=backend.call_args.kwargs['native_execution']
        self.assertEqual(native.record(),dict(session_id='shell-native',turn_id='native-turn',tool_call_id='native-call',api_request_id='native-request'))
        start=self.case.event(out['started_id']);retained=self.case.event(out['evidence_id'])
        self.assertEqual(start['session'],'shell-conversation')
        self.assertEqual(retained['session'],start['session'])
        self.assertEqual(start['data']['arguments'],args)
        self.assertEqual(start['data']['native_call'],native.record())
        effects=start['data']['capability_effects']
        for field in ('evidence_read','workspace_write','subprocess','target_code_execution'):self.assertTrue(effects[field])
        for field in ('target_write','network','comparison_readonly'):self.assertFalse(effects[field])
        self.assertIn('/stdout',[p['pointer'] for p in out['presented_spans']])
        source=host.find(host.current(self.case),'sources',out['evidence_id'])
        coverage=json.loads(source['coverage'])
        self.assertEqual(source['source_role'],'tool_result')
        self.assertEqual(coverage['representation'],'derived_command_output')
        self.assertFalse(coverage['original_evidence']);self.assertTrue(coverage['coverage_not_established'])

    def test_stdout_exact_c1_c2_references_keep_derivation_in_adopted_claim(self):
        with patch('forsic_plugin.shell_workspace.invoke',return_value=self.output()):
            out=self.call('forsic_shell',{'command':"printf 'chosen marker\\n'"})
        c2=next(p['citation_ref'] for p in out['presented_spans'] if p['pointer']=='/stdout')
        source=host.find(host.current(self.case),'sources',out['evidence_id'])
        page=self.call('forsic_reporting',dict(action='source',source_id=out['evidence_id'],source_version=source['version'],pointer='/stdout'))
        self.assertNotIn('error',page,page)
        self.assertFalse(page['acquisition_context']['returned_scope']['original_evidence'])
        self.assertTrue(page['acquisition_context']['derivation']['output_may_be_generated'])
        c1=page['citation_ref']
        self.assertTrue(c1.startswith('C1:'))
        for index,ref in enumerate((c2,c1)):
            payload=dict(operation_id='shell-claim-'+str(index),question='What did the command print?',scope='Synthetic container stdout',
                answer='The command printed a chosen marker; original evidence content remains unestablished.',
                reason='Retain exactly the returned derived output',priority='contextual',
                judgment=dict(citation_refs=[ref],assertion_kind='fact',reasoning_summary='This is command output, including model-authored printf text.'))
            saved=self.call('forsic_question',payload);self.assertNotIn('error',saved,saved)
            state=host.current(self.case);q=host.find(state,'questions',saved['question']['question_id'])
            claim=host.find(state,'claims',q['claim_refs'][0]['id'])
            observation=host.find(state,'observations',claim['source_refs'][0]['id'])
            self.assertEqual(observation['literal'],out['stdout'])
            context=observation['acquisition_context']
            self.assertEqual(context['returned_scope']['representation'],'derived_command_output')
            self.assertFalse(context['returned_scope']['original_evidence'])
            self.assertTrue(context['returned_scope']['coverage_not_established'])
            self.assertTrue(context['derivation']['output_may_be_generated'])

    def test_invalid_arguments_do_not_launch_or_start_workspace_work(self):
        variants=({}, {'command':None}, {'command':'  '}, {'command':'pwd','timeout_seconds':True},
                  {'command':'pwd','timeout_seconds':1801}, {'command':'pwd','cwd':'/host'},
                  {'command':'pwd','_host_override':'anything'}, {'command':'pwd','reason':[]})
        with patch('forsic_plugin.shell_workspace.invoke') as backend:
            for args in variants:
                with self.subTest(args=args):
                    out=self.call('forsic_shell',args)
                    self.assertEqual(out['error_code'],'invalid_arguments',out)
                    self.assertTrue(out['execution_rejected']);self.assertFalse(out['backend_started'])
                    self.assertTrue(out['problems'])
            backend.assert_not_called()

    def test_unavailable_foreign_or_paused_binding_cannot_launch(self):
        variants=({'status':'unavailable'},{'requested_session_id':'another-native'},
                  {'case_id':'another-case'},{'conversation_id':''},{'goal_status':'paused'},
                  {'goal_status':None})
        with patch('forsic_plugin.shell_workspace.invoke') as backend:
            for changes in variants:
                original=deepcopy(self.binding);self.binding.update(changes)
                out=self.call('forsic_shell',{'command':'pwd'})
                self.assertTrue(out['execution_rejected'],out)
                self.assertIn(out['error_code'],('shell_binding_unavailable','shell_goal_inactive'))
                self.binding.clear();self.binding.update(original)
            backend.assert_not_called()

    def test_unknown_completion_fences_different_commands_and_lookup_never_executes(self):
        result=self.output(error='Synthetic cleanup uncertainty',error_code='shell_cleanup_unknown',
                           execution=dict(state='completion_unknown',termination_confirmed=False,exit_code=None))
        with patch('forsic_plugin.shell_workspace.invoke',return_value=result) as backend:
            first=self.call('forsic_shell',{'command':'printf one > /work/result'})
            second=self.call('forsic_shell',{'command':'printf two > /work/different'})
            self.assertEqual(second['error_code'],'execution_completion_unknown')
            self.assertTrue(second['execution_rejected'])
            self.assertEqual(second['pending_started_id'],first['started_id'])
            view=self.call(second['execution_call']['tool'],second['execution_call']['arguments'])
            self.assertEqual(view['completion'],'completion_unknown')
            self.assertEqual(view['results'][0]['evidence_id'],first['evidence_id'])
            backend.assert_called_once()
        self.assertNotIn('retry_call',first);self.assertNotIn('presented_spans',first)

    def test_commit_loss_retains_exact_attempt_without_backend_retry(self):
        record=self.case.record
        for persisted in (False,True):
            # Use separate conversation workspaces so the earlier unknown start
            # remains fenced while both commit acknowledgement cases are tested.
            self.binding['conversation_id']='commit-'+str(persisted)
            def lose_ack(*args,**kwargs):
                if persisted:record(*args,**kwargs)
                raise sqlite3.OperationalError('Synthetic acknowledgement loss')
            with patch('forsic_plugin.shell_workspace.invoke',return_value=self.output()) as backend, \
                 patch.object(self.case,'record',side_effect=lose_ack):
                out=self.call('forsic_shell',{'command':'printf one > /work/result'})
                backend.assert_called_once()
            self.assertEqual(out['error_code'],'result_commit_unknown')
            with patch('forsic_plugin.shell_workspace.invoke') as backend:
                view=self.call(out['execution_call']['tool'],out['execution_call']['arguments'])
                self.assertEqual(view['result_commit'],'retained' if persisted else 'not_observed')
                if not persisted:
                    blocked=self.call('forsic_shell',{'command':'printf two > /work/result'})
                    self.assertEqual(blocked['error_code'],'execution_completion_unknown')
                backend.assert_not_called()

    def test_nonzero_exit_and_structured_backend_failure_are_retained(self):
        with patch('forsic_plugin.shell_workspace.invoke',return_value=self.output(code=17,stderr='Synthetic diagnostic')):
            out=self.call('forsic_shell',{'command':'printf diagnostic >&2; exit 17'})
        retained=self.case.event(out['evidence_id'])['data']
        self.assertEqual(retained['execution']['exit_code'],17)
        self.assertEqual(retained['stderr'],'Synthetic diagnostic')
        with patch('forsic_plugin.shell_workspace.invoke',side_effect=ExecutionFailure('Synthetic launch failure',
                   error_code='shell_launch_failed',execution={'state':'not_started','termination_confirmed':True})):
            out=self.call('forsic_shell',{'command':'pwd'})
        self.assertEqual(out['error_code'],'shell_launch_failed')
        self.assertEqual(self.case.event(out['evidence_id'])['data']['execution']['state'],'not_started')

    def test_nonzero_backend_exit_reaches_native_failure_classifier_and_keeps_bytes(self):
        from agent.display import _detect_tool_failure
        from forsic_plugin import shell_workspace as shell
        backend=dict(stdout=b'partial output',stderr=b'precise diagnostic',
            execution=dict(state='returned',backend='docker',container='synthetic-shell',
                exit_code=17,termination_confirmed=True,cleanup={'confirmed':True}))
        with patch.object(shell,'_image_id',return_value='sha256:'+'a'*64),patch.object(shell,'run_container',return_value=backend):
            out=self.call('forsic_shell',{'command':'synthetic command'})
        self.assertFalse(out['success'])
        self.assertEqual(out['error_code'],'command_exit_nonzero')
        self.assertEqual(out['execution']['state'],'returned')
        self.assertTrue(_detect_tool_failure('forsic_shell',json.dumps(out))[0])
        retained=self.case.event(out['evidence_id'])['data']
        self.assertEqual(base64.b64decode(retained['raw_output']['stdout_base64']),b'partial output')
        self.assertEqual(base64.b64decode(retained['raw_output']['stderr_base64']),b'precise diagnostic')
        self.assertEqual(retained['error_code'],out['error_code'])

    def test_shell_model_presentations_compare_across_fresh_receipts_and_containers(self):
        from forsic_plugin import shell_workspace as shell
        from forsic_plugin.loop_comparison import project
        args={'command':'synthetic command'}
        def backend(index):
            return dict(stdout=b'same partial',stderr=b'same diagnostic',
                execution=dict(state='returned',backend='docker',container='synthetic-'+str(index),
                    exit_code=17,termination_confirmed=True,cleanup={'confirmed':True}))
        with patch.object(shell,'_image_id',return_value='sha256:'+'a'*64),patch.object(shell,'run_container',side_effect=[backend(1),backend(2)]):
            one=self.call('forsic_shell',args)
            two=self.call('forsic_shell',args)
        self.assertNotEqual(one['evidence_id'],two['evidence_id'])
        self.assertNotEqual(one['source_ref'],two['source_ref'])
        self.assertEqual(project('forsic_shell',args,json.dumps(one))['result'],
                         project('forsic_shell',args,json.dumps(two))['result'])

    def test_native_keyboard_interrupt_retains_partial_bytes_and_propagates(self):
        from forsic_plugin.image_process import _raw_output
        interrupted=KeyboardInterrupt()
        execution=dict(state='cancelled',backend='docker',termination_confirmed=True,
                       cleanup={'confirmed':True},exit_code=-9)
        interrupted.forsic_execution=execution
        interrupted.forsic_raw_output=_raw_output(b'partial stdout\xff',b'partial stderr',complete=False,limit=12000)
        with patch('forsic_plugin.shell_workspace._image_id',return_value='sha256:'+'a'*64), \
             patch('forsic_plugin.shell_workspace._command',return_value=['synthetic-container-command']), \
             patch('forsic_plugin.shell_workspace.run_container',side_effect=interrupted) as backend, \
             self.assertRaises(KeyboardInterrupt) as caught:
            self.call('forsic_shell',{'command':'printf partial; sleep 1'},
                      turn_id='interrupt-turn',tool_call_id='interrupt-call')
        self.assertIs(caught.exception,interrupted)
        backend.assert_called_once()
        event=self.case.events(1)[0]
        self.assertEqual((event['kind'],event['session']),('tool_result','shell-conversation'))
        retained=event['data']
        self.assertEqual(retained['tool'],'forsic_shell')
        self.assertEqual(retained['error_type'],'KeyboardInterrupt')
        self.assertEqual(retained['error_code'],'cancel_requested')
        self.assertEqual(retained['execution'],execution)
        self.assertEqual(base64.b64decode(retained['raw_output']['stdout_base64']),b'partial stdout\xff')
        self.assertEqual(base64.b64decode(retained['raw_output']['stderr_base64']),b'partial stderr')
        self.assertEqual(retained['stdout'],'partial stdout\ufffd')
        self.assertEqual(retained['representation'],'derived_command_output')
        self.assertFalse(retained['original_evidence']);self.assertTrue(retained['coverage_not_established'])
        from forsic_plugin.report_driven.measurement import acquisition_context
        context=acquisition_context(retained,'/stdout')
        self.assertFalse(context['derivation']['capture_complete'])
        self.assertEqual(context['derivation']['execution_state'],'cancelled')
        start=self.case.event(retained['started_id'])
        self.assertEqual(start['data']['native_call']['tool_call_id'],'interrupt-call')

    def test_large_raw_output_stays_in_ledger_with_bounded_preview_and_exact_paging(self):
        result=self.output(text='A'*6000)
        raw_bytes=b'A'*(256*1024)
        encoded=base64.b64encode(raw_bytes).decode()
        result['raw_output'].update(stdout_base64=encoded,stdout_bytes=len(raw_bytes),retained_bytes=len(raw_bytes),
                                    stdout_sha256=hashlib.sha256(raw_bytes).hexdigest(),truncated=False)
        result['preview_truncated']={'stdout':True,'stderr':False}
        with patch('forsic_plugin.shell_workspace.invoke',return_value=result):
            out=self.call('forsic_shell',{'command':'python -c "print(1)"'})
        self.assertNotIn('raw_output',out)
        self.assertLess(len(json.dumps(out)),20000)
        self.assertEqual(out['stdout'],result['stdout'])
        self.assertEqual(out['raw_output_metadata']['stdout_bytes'],len(raw_bytes))
        self.assertEqual(out['raw_output_metadata']['stdout_sha256'],hashlib.sha256(raw_bytes).hexdigest())
        self.assertTrue(out['preview_truncated']['stdout'])
        self.assertEqual(self.case.event(out['evidence_id'])['data']['raw_output']['stdout_base64'],encoded)
        ref=next(span['citation_ref'] for span in out['presented_spans'] if span['pointer']=='/stdout')
        from forsic_plugin.report_driven.grounded_judgment import selection
        selected=selection(self.case,host.current(self.case),ref)
        self.assertEqual(selected['literal'],result['stdout'])
        call=out['raw_output_calls']['stdout']
        page=self.call(call['tool'],call['arguments'])
        self.assertNotIn('error',page,page)
        self.assertEqual(page['text'],encoded[:len(page['text'])])
        self.assertIsNotNone(page['next_call'])
        next_page=self.call('forsic_reporting',page['next_call'])
        self.assertNotIn('error',next_page,next_page)
        self.assertEqual(next_page['text'],encoded[next_page['byte_start']:next_page['byte_end']])

    def test_case_info_is_static_and_shell_is_not_readonly_comparison(self):
        with patch('forsic_plugin.shell_workspace.invoke') as backend:
            info=self.call('forsic_reporting',{'action':'case'})
        backend.assert_not_called()
        self.assertTrue(info['capabilities']['container_shell'])
        self.assertIn('/work',json.dumps(info['shell_workspace']))
        self.assertFalse(capability('forsic_shell',{'command':'cat /evidence/request.log'})['comparison_readonly'])
        self.assertTrue(capability('forsic_read',{'path':'request.log'})['comparison_readonly'])


if __name__=='__main__':unittest.main()
