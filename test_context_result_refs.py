"""Synthetic retained lookup survives native pruning without becoming evidence."""
import json
from copy import deepcopy
from functools import partial
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.context_compressor import ContextCompressor, _summarize_tool_result
from forsic_plugin.context_results import summarize
from hermes_constants import hermes_home_key, set_hermes_home_override, reset_hermes_home_override
from hermes_cli.plugins import PluginContext
from tools.registry import registry
import test_efficiency_contracts as efficiency


class ContextResultRefsTests(unittest.TestCase):
    def test_real_read_prunes_to_exact_retrievable_source_with_scope_and_no_body_mutation(self):
        from agent.context_compressor import MAX_ITERATIONS_SUMMARY_REQUEST, _LEGACY_MAX_ITERATIONS_SUMMARY_REQUEST
        for marker in (MAX_ITERATIONS_SUMMARY_REQUEST, _LEGACY_MAX_ITERATIONS_SUMMARY_REQUEST):
            self.assertTrue(ContextCompressor._is_synthetic_compression_user_turn({'role': 'user', 'content': marker}))
        x = efficiency.EfficiencyContracts(); x.setUp(); self.addCleanup(x.doCleanups)
        result = x.call('forsic_read', {'path': 'request.log', 'start_line': 1})
        source = result['evidence_id']
        retained = deepcopy(x.case.event(source))
        # This body is long enough for native pruning; the ledger retains its original record.
        body = json.dumps({**result, 'padding': 'large unneeded detail ' * 300})
        registry.register('synthetic_retained_read', 'synthetic', {'parameters': {}}, lambda **k: '',
                          result_summary=partial(summarize, 'forsic_read'))
        self.addCleanup(registry.deregister, 'synthetic_retained_read')
        messages = [{'role': 'tool', 'tool_call_id': 'native-call', 'content': body, 'message_uid': 'stable'}]
        self.assertTrue(ContextCompressor._demote_tool_result_at(messages, 0,
            {'native-call': ('synthetic_retained_read', json.dumps({'path': 'request.log', 'start_line': 1}))}, 100))
        pruned = json.loads(messages[0]['content'].split('\n', 1)[1])
        self.assertFalse(ContextCompressor._demote_tool_result_at(messages, 0,
            {'native-call': ('synthetic_retained_read', '{}')}, 100))
        self.assertTrue(pruned['body_omitted'])
        self.assertEqual(pruned['requested_scope']['start_line'], 1)
        self.assertNotIn('padding', pruned)
        self.assertNotIn('citation_ref', pruned)
        self.assertEqual(messages[0]['message_uid'], 'stable')
        history = [{'role': 'assistant', 'tool_calls': [{'id': 'native-call', 'function': {
            'name': 'synthetic_retained_read', 'arguments': '{"path":"request.log"}'}}]},
            {'role': 'tool', 'tool_call_id': 'native-call', 'content': body, 'message_uid': 'old-tool'}]
        for i in range(7):
            history.extend([{'role': 'assistant', 'content': 'next'},
                            {'role': 'tool', 'tool_call_id': str(i), 'content': 'recent'}])
        original = deepcopy(history)
        compressor = object.__new__(ContextCompressor); compressor.quiet_mode = True
        demoted = compressor._demote_stale_tail_tools(history, 0)
        self.assertIn(source, demoted[1]['content'])
        self.assertEqual(history, original)
        self.assertEqual(demoted, compressor._demote_stale_tail_tools(demoted, 0))
        call = pruned['lookup']
        self.assertEqual(call['arguments']['source_id'], source)
        exact = x.call(call['tool'], call['arguments'])
        self.assertNotIn('error', exact, exact)
        self.assertEqual(x.case.event(source), retained)
        partial_result = {'evidence_id': 'synthetic-source', 'matches': [], 'coverage_complete': False,
                          'scan_exhausted': False, 'stop_reason': 'match_limit', 'bytes_scanned': 82,
                          'next_cursor': {'opaque': 'cursor'}, 'search_path': '/', 'error': 'read failed'}
        crumb = json.loads(summarize('forsic_image_files', {'action': 'search', 'file_path': '/', 'text': 'x'},
                                     json.dumps(partial_result)))
        self.assertFalse(crumb['returned_scope']['coverage_complete'])
        self.assertTrue(crumb['continuation_available'])
        self.assertEqual(crumb['returned_matches_count'], 0)
        self.assertTrue(crumb['error_present'])
        for wrapper in ('forsic_image_read', 'forsic_archive_read'):
            wrapped = json.loads(summarize(wrapper, {}, json.dumps({
                'evidence_id': source, 'byte_start': 128, 'byte_end': 255,
                'member_index': 3, 'complete_member': False, 'identity_strength': 'metadata_checked'})))
            self.assertEqual(wrapped['lookup']['arguments']['source_id'], source)
            self.assertEqual(wrapped['returned_scope']['byte_start'], 128)
            self.assertFalse(wrapped['returned_scope']['complete_member'])
            self.assertEqual(wrapped['returned_scope']['identity_strength'], 'metadata_checked')

    def test_real_plugin_registration_is_profile_scoped_and_bounded_with_native_fallback(self):
        name = 'synthetic_scope_summary'
        body = json.dumps({'evidence_id': 'source-a', 'complete_file': False, 'text': 'x' * 5000})
        with tempfile.TemporaryDirectory() as root:
            a, b = [hermes_home_key(str(Path(root) / suffix)) for suffix in ('a', 'b')]
            ctx = object.__new__(PluginContext)
            ctx.manifest = SimpleNamespace(name='synthetic')
            ctx._manager = SimpleNamespace(scope_key=a, _plugin_tool_names=set(),
                _track_scoped_registration=lambda *args, **kwargs: None)
            ctx.register_tool(name, 'synthetic', {'parameters': {}}, lambda **kwargs: '',
                              result_summary=partial(summarize, 'forsic_read'))
            self.addCleanup(registry.deregister, name, scope=a)
            for scope, has_lookup in ((a, True), (b, False), (a, True)):
                token = set_hermes_home_override(scope)
                try:
                    text = _summarize_tool_result(name, '{"path":"fixture"}', body)
                    self.assertEqual('"lookup"' in text, has_lookup)
                    self.assertLessEqual(len(text), 2048)
                finally:
                    reset_hermes_home_override(token)
            token = set_hermes_home_override(a)
            try:
                entry = registry.get_entry(name)
                for callback in (lambda *a: 'x' * 3000, lambda *a: {}, lambda *a: 1/0):
                    with patch.object(entry, 'result_summary', callback):
                        fallback = _summarize_tool_result(name, '{}', body)
                        self.assertNotIn('"lookup"', fallback)
                        self.assertIn(name, fallback)
            finally:
                reset_hermes_home_override(token)



class ExecutionOutcomePruningTests(unittest.TestCase):
    def setUp(self):
        self.x = efficiency.EfficiencyContracts(); self.x.setUp(); self.addCleanup(self.x.doCleanups)

    def prune(self, tool, args, body, *, tail=False):
        name = 'synthetic_outcome_' + tool
        registry.register(name, 'synthetic', {'parameters': {}}, lambda **k: '',
                          result_summary=partial(summarize, tool))
        self.addCleanup(registry.deregister, name)
        history = [{'role':'assistant', 'tool_calls':[{'id':'call', 'function':{
            'name':name, 'arguments':json.dumps(args)}}]},
            {'role':'tool','tool_call_id':'call','content':body,'message_uid':'unchanged'}]
        original = deepcopy(history)
        compressor = object.__new__(ContextCompressor); compressor.quiet_mode = True
        if tail:
            for i in range(8):
                history.extend([{'role':'assistant','content':'continue'},
                    {'role':'tool','tool_call_id':str(i),'content':'recent'}])
            result = compressor._demote_stale_tail_tools(history, 0)
        else:
            result, _ = compressor._prune_old_tool_results(history, 0)
        self.assertEqual(result[0], original[0])
        self.assertEqual(history[:2], original)
        self.assertEqual(result[1]['message_uid'], 'unchanged')
        rendered = compressor._serialize_for_summary(result)
        content = result[1]['content']
        decoded = json.loads(content.split('\n',1)[1] if content.startswith('[Tool result summary]\n') else content)
        return decoded, rendered, content

    def test_failed_mutation_and_read_keep_actual_error_through_pruning_and_summary_input(self):
        x=self.x
        bad={'operation_id':'failed','mission_id':'','result_id':'','expected_revision':0,
            'outcome':'found','citation_refs':[],'reasoning_summary':'Unverified intent',
            'limitations':['No execution receipt'],'next_check':'Inspect current state'}
        before=efficiency.current(x.case)
        failed=x.call('forsic_assess',bad)
        self.assertIn('No assessment was saved',failed['error'])
        variants=[('forsic_assess',bad,failed),
            ('forsic_question',{'operation_id':'failed','answer':'Requested answer'},
             x.call('forsic_question',{'operation_id':'failed'})),
            ('forsic_read',{'path':'missing.log'},x.call('forsic_read',{'path':'missing.log'}))]
        for tool,args,body in variants:
            self.assertTrue(body.get('error'),body)
            for tail in (False,True):
                with self.subTest(tool=tool,tail=tail):
                    raw=json.dumps({**body,'unneeded_detail':'x'*4000})
                    view,text,content=self.prune(tool,args,raw,tail=tail)
                    self.assertTrue(view['error_present'])
                    self.assertEqual(view['error'],body['error'])
                    self.assertIn(body['error'],text)
                    self.assertNotIn('returned_records',view)
                    self.assertLess(len(content),len(raw))
                    self.assertEqual(view['retained_result_id'],body['evidence_id'])
                    self.assertEqual(x.case.event(body['evidence_id'])['data']['error'],body['error'])
                    if tool=='forsic_read':
                        lookup=view['lookup'];self.assertEqual(lookup['arguments']['source_id'],body['evidence_id'])
                        exact=x.call(lookup['tool'],lookup['arguments']);self.assertNotIn('error',exact,exact)
                    else:
                        # Mutation results are execution records, not selectable body sources.
                        self.assertNotIn('lookup',view)
        self.assertEqual(efficiency.current(x.case)['assessments'],before['assessments'])
        unknown={'error':'Commit status unknown','failure_stage':'presentation','execution_status':'unknown'}
        view,_,_=self.prune('forsic_assess',bad,json.dumps({**unknown,'detail':'x'*3000}))
        self.assertEqual(view['execution_status'],'unknown')
        self.assertNotIn('lookup',view)
        self.assertNotIn('saved',view)
        # A compact original must survive if a safe registered projection would grow it.
        raw=json.dumps({**unknown,'detail':'x'*140})
        self.assertGreater(len(raw),200)
        view,_,content=self.prune('forsic_assess',bad,raw)
        self.assertEqual(view,json.loads(raw))
        self.assertEqual(content,raw)

    def test_success_acknowledgements_are_from_results_and_partial_reads_stay_partial(self):
        x=self.x
        planned=x.call('forsic_mission',x.plan());self.assertNotIn('error',planned,planned)
        call=planned['next_call'];read=x.call(call['tool'],call['arguments'])
        q=efficiency.find(efficiency.current(x.case),'questions',x.qid)
        args={'operation_id':'saved','mission_id':planned['mission']['id'],'result_id':read['evidence_id'],
              'expected_revision':q['revision'],'citation_refs':[read['presented_spans'][0]['citation_ref']],
              'outcome':'inconclusive','reasoning_summary':'Request only','limitations':['No execution receipt'],
              'next_check':'Find an independent record'}
        assessed=x.call('forsic_assess',args);self.assertNotIn('error',assessed,assessed)
        saved_question=x.call('forsic_question',dict(operation_id='question-edit',question_id=q['id'],
            expected_revision=q['revision'],reason='Keep investigating',work_state='active'))
        self.assertNotIn('error',saved_question,saved_question)
        for tool,body,key in [('forsic_mission',planned,'mission'),('forsic_assess',assessed,'assessment'),
                              ('forsic_question',saved_question,'question')]:
            view,_,_=self.prune(tool,{'outcome':'supports','id':'invented'},json.dumps({**body,'padding':'x'*4000}))
            self.assertEqual(view['receipt'],body['receipt'])
            identity='question_id' if 'question_id' in body[key] else 'id'
            self.assertEqual(view['returned_records'][key][identity],body[key][identity])
            self.assertNotIn('lookup',view)
            if key=='assessment':self.assertEqual(view['returned_records'][key]['outcome'],'inconclusive')
            self.assertNotIn('invented',json.dumps(view))
        partial={'evidence_id':'retained','error':'x'*5000+' end of failure','coverage_complete':False,
                 'matches':[],'execution_rejected':True,'backend_started':False}
        view,_,content=self.prune('forsic_image_read',{},json.dumps(partial))
        self.assertTrue(view['error_present']);self.assertTrue(view['error_truncated'])
        self.assertTrue(view['error'].endswith('end of failure'))
        self.assertFalse(view['returned_scope']['coverage_complete'])
        self.assertFalse(view['returned_scope']['backend_started'])
        self.assertEqual(view['returned_matches_count'],0)
        self.assertLessEqual(len(content),2048)
        crowded={**partial, **{k:'p'*240 for k in ('file_path','search_path','search_text')},
                 'status':'running','verified':False}
        view,_,_=self.prune('forsic_image_read',{'path':'p'*240,'file_path':'p'*240},json.dumps(crowded))
        self.assertTrue(view['error_present'])
        self.assertFalse(view['returned_scope']['coverage_complete'])
        self.assertFalse(view['returned_scope']['verified'])
        self.assertEqual(view['returned_scope']['status'],'running')


if __name__ == "__main__": unittest.main()
