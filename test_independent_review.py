"""Hermetic native-checkpoint blind review; all model responses are local fakes."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forsic_plugin import independent_review as review
from forsic_plugin.investigation_context import resolve_context
from hermes_cli.goals import goal_identity
import test_investigation_context as fixtures


class IndependentReviewTests(unittest.TestCase):
    setUp=fixtures.InvestigationContextTests.setUp
    bind=fixtures.InvestigationContextTests.bind
    session=fixtures.InvestigationContextTests.session
    save_note=fixtures.InvestigationContextTests.save_note

    def setup_review(self,count=3):
        self.config={'model':{'provider':'custom','default':'synthetic-private-model','base_url':'http://127.0.0.1:9000/v1'}}
        self.stack.enter_context(patch('hermes_cli.config.load_config_readonly',return_value=self.config))
        for target in ('socket.socket.connect','socket.create_connection'):
            self.stack.enter_context(patch(target,side_effect=AssertionError('offline test forbids network')))
        self.calls=[]
        self.fake=self.stack.enter_context(patch('agent.auxiliary_client.call_llm',side_effect=self.response))
        self.inventory=self.case.record('tool_result',{'tool':'forsic_image_files','representation':'directory_inventory',
            'path':'synthetic.e01','volume_offset':0,'file_path':'/settings/user-jobs','listing_complete':True,'content_not_measured':True,
            'reason':'PRIVATE_TOOL_REASON','instruction':'PRIVATE_INSTRUCTION',
            'entries':[{'path':'/settings/user-jobs/custom-account','bytes':42,'mode':'-rw-------'},
                       {'path':'/settings/user-jobs/system-account','bytes':20,'mode':'-rw-r--r--'}]},self.sid)
        self.add_sources(count)
        self.save_note(answer='PRIVATE_MAIN_ANSWER',summary='PRIVATE_SUMMARY',last_response='PRIVATE_LAST_RESPONSE')
        self.case.record('message',{'text':'PRIVATE_HISTORY'},self.sid)

    def empty_response(self,**kwargs):
        self.calls.append(deepcopy(kwargs))
        return iter([SimpleNamespace(choices=[SimpleNamespace(index=0,
            delta=SimpleNamespace(content='{"findings":[]}'),finish_reason='stop')],usage=None)])

    def test_first_review_requires_owned_agent_answer_not_bootstrap_or_read_count(self):
        from forsic_plugin.question_bootstrap import PROVISIONAL_ANSWER
        self.setup_review()
        self.save_note(answer=PROVISIONAL_ANSWER)
        self.add_sources(20)
        review.goal_advisory_context(**self.kwargs(turns_used=20))
        self.fake.assert_not_called();self.assertFalse(self.request_paths())
        self.save_note(answer='A provisional interpretation, with unresolved alternatives')
        result=review.goal_advisory_context(**self.kwargs(turns_used=21))
        self.assertEqual(result['status'],'received')
        self.assertEqual(len(self.calls),1)
        self.assertNotIn('A provisional interpretation',self.calls[0]['messages'][1]['content'])
        self.assertTrue(result['requires_followup'])  # Saving an answer is no approval.

    def test_answer_lookup_failure_never_dispatches_auxiliary_call(self):
        self.setup_review()
        with patch.object(review,'_question_records',side_effect=ValueError('synthetic unavailable note')):
            result=review.goal_advisory_context(**self.kwargs())
        self.assertEqual(result['status'],'unavailable')
        self.fake.assert_not_called();self.assertFalse(self.request_paths())

    def test_answer_timing_gate_respects_ownership_and_does_not_require_four_reads(self):
        from forsic_plugin.question_bootstrap import PROVISIONAL_ANSWER
        self.setup_review(count=0)
        self.save_note(answer=PROVISIONAL_ANSWER)
        foreign=deepcopy(self.note);foreign.update(note_id='foreign-answer',session_id='foreign-session')
        foreign['investigation']['answer']='Foreign provisional answer'
        self.case.record('note',foreign,'foreign-session')
        review.goal_advisory_context(**self.kwargs())
        self.fake.assert_not_called()
        self.save_note(answer='Owned provisional assessment')
        self.assertEqual(review.goal_advisory_context(**self.kwargs())['status'],'received')
        self.assertEqual(len(self.calls),1)

    def test_reads_alone_and_pending_questions_do_not_start_more_reviews(self):
        self.setup_review();first=review.goal_advisory_context(**self.kwargs())
        self.add_sources(20)
        self.save_note(answer='A later provisional interpretation')
        result=review.goal_advisory_context(**self.kwargs(turns_used=20))
        self.assertTrue(result['requires_followup']);self.assertEqual(len(self.calls),1)
        self.assertEqual(result['pending_finding_ids'],first['pending_finding_ids'])
        # Even after a no-question receipt, evidence growth alone is insufficient.
        with patch.object(review,'review_state',return_value={'status':'received','pending_findings':[]}):
            self.save_note(answer='PRIVATE_MAIN_ANSWER')
            review.goal_advisory_context(**self.kwargs(turns_used=21))
        self.assertEqual(len(self.calls),1)

    def add_sources(self,count,sid=None):
        for i in range(count):
            self.case.record('tool_result',{'tool':'forsic_read','path':'synthetic-'+str(i)+'.log',
                'complete_file':True,'lines':[{'line':1,'text':'job request only '+str(i)}]},sid or self.sid)

    def kwargs(self,**changes):
        result=dict(session_id=self.sid,goal_id=goal_identity(self.goal),goal_text=self.goal.goal,
                    turns_used=1,intake_root=self.intake,native_db=self.native,enabled=True)
        result.update(changes);return result

    def response(self,**kwargs):
        self.assertTrue(self.request_paths())  # Intent receipt exists before physical dispatch.
        self.calls.append(deepcopy(kwargs))
        body=json.loads(kwargs['messages'][1]['content'])
        source=next(s for s in body['sources'] if s['source_id']==self.inventory)
        handle=next(f['review_ref'] for f in source['fields'] if f['pointer']=='/entries/0/path')
        text=json.dumps({'findings':[{'review_ref':handle,
            'question':'사용자별 설정 내용을 확인했는가?','why':'실제 목록에는 사용자별 파일이 있으나 내용 관측은 제시되지 않았습니다.',
            'next_check':'기존 도구로 /settings/user-jobs/custom-account 본문을 읽고 실행 여부는 따로 확인하세요.'}]},ensure_ascii=False)
        return iter([SimpleNamespace(choices=[SimpleNamespace(index=0,delta=SimpleNamespace(content=text,reasoning_content='PRIVATE_REASONING'),finish_reason='stop')],
                                     usage=SimpleNamespace(model_dump=lambda:{'prompt_tokens':10,'completion_tokens':20}))])

    def request_paths(self):
        return list(self.case.output.glob('independent-review-*-request.json'))

    def ledger(self):
        return self.case.db.read_bytes()

    def test_review_clock_rollover_preserves_packet_and_evidence_fingerprint(self):
        self.setup_review()
        raw = 'Dec 31 23:59 year missing; 2099-01-01 future clock; ctime is metadata'
        self.case.record('tool_result', {'tool': 'forsic_read', 'path': 'synthetic-time.log',
            'lines': [{'line': 1, 'text': raw}]}, self.sid)
        with patch('forsic_plugin.runtime_clock.datetime') as clock:
            clock.now.return_value.astimezone.return_value = datetime(2039, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
            real_response = self.response
            def crosses_year(**kwargs):
                clock.now.return_value.astimezone.return_value = datetime(2040, 1, 1, tzinfo=timezone.utc)
                return real_response(**kwargs)
            self.fake.side_effect = crosses_year
            before = self.ledger()
            result = review.goal_advisory_context(**self.kwargs())
            self.assertEqual(result['status'], 'received', result)
            self.assertEqual(len(self.calls), 1)
            packet = json.loads(self.calls[0]['messages'][1]['content'])
            self.assertEqual(packet['runtime_clock']['current_datetime'], '2039-12-31T23:59:59+00:00')
            self.assertIn(raw, self.calls[0]['messages'][1]['content'])
            self.assertEqual(self.ledger(), before)
            resolved = resolve_context(self.sid, intake_root=self.intake, native_db=self.native)
            later = review._collect(resolved)
            self.assertEqual(later['runtime_clock']['current_datetime'], '2040-01-01T00:00:00+00:00')
            replay = review._collect(resolved, runtime_clock=packet['runtime_clock'])
            self.assertEqual(later['fingerprint'], replay['fingerprint'])
            self.assertNotEqual(later['input_fingerprint'], replay['input_fingerprint'])

    def test_same_day_clock_reuses_legacy_review_without_ignoring_temporal_context(self):
        self.setup_review()
        self.fake.side_effect = self.empty_response
        resolved = resolve_context(self.sid, intake_root=self.intake, native_db=self.native)
        initial = {'current_datetime': '2040-01-01T00:00:00+00:00',
                   'timezone': 'UTC', 'basis': 'server_clock_at_context_refresh'}
        args = self.kwargs(stage='final', draft_response='Same bounded candidate.')
        with patch.object(review, 'current_context', return_value=initial) as clock:
            # Existing immutable receipts hashed the exact packet clock. Reuse
            # them from retained messages without rewriting their identity.
            with patch.object(review, '_input_fingerprint', side_effect=lambda system, body:
                    review._hash({'system': system, 'prompt': review._json(body)})):
                first = review.goal_advisory_context(**args)
            receipts = {p: p.read_bytes() for p in self.case.output.glob('independent-review-*.json')}
            original = review._collect(resolved, 'final', args['draft_response'])
            clock.return_value = {**initial, 'current_datetime': '2040-01-01T23:59:59+00:00'}
            later = review._collect(resolved, 'final', args['draft_response'])
            self.assertNotEqual(later['prompt'], original['prompt'])
            self.assertEqual(later['input_fingerprint'], original['input_fingerprint'])
            again = review.goal_advisory_context(**args)
            self.assertEqual(again['review_id'], first['review_id'])
            self.assertEqual(len(self.calls), 1)
            self.assertEqual({p: p.read_bytes() for p in receipts}, receipts)
            packet = json.loads(self.calls[0]['messages'][1]['content'])
            self.assertEqual(packet['runtime_clock'], initial)
            for changed in ({'current_datetime': '2040-01-02T00:00:00+00:00'},
                    {'current_datetime': '2040-01-01T23:59:59+09:00'},
                    {'timezone': 'Asia/Seoul'}, {'basis': 'different_clock_basis'}):
                with self.subTest(changed=changed):
                    selected = review._collect(resolved, 'final', args['draft_response'],
                                              runtime_clock={**initial, **changed})
                    self.assertNotEqual(selected['input_fingerprint'], original['input_fingerprint'])
            changed_draft = review._collect(resolved, 'final', 'A changed candidate.')
            self.assertNotEqual(changed_draft['input_fingerprint'], original['input_fingerprint'])
            with patch.object(review, 'SYSTEM', review.SYSTEM + '\nChanged review policy.'):
                changed_policy = review._collect(resolved, 'final', args['draft_response'])
            self.assertNotEqual(changed_policy['input_fingerprint'], original['input_fingerprint'])
            clock.return_value = {**initial, 'current_datetime': '2040-01-02T00:00:00+00:00'}
            review.goal_advisory_context(**args)
            self.assertEqual(len(self.calls), 2)
            self.assertEqual(json.loads(self.calls[-1]['messages'][1]['content'])['runtime_clock'], clock.return_value)

    def test_malformed_retained_packets_do_not_match_or_hide_usable_review(self):
        self.setup_review()
        self.fake.side_effect = self.empty_response
        args = self.kwargs(stage='final', draft_response='Same bounded candidate.')
        first = review.goal_advisory_context(**args)
        resolved = resolve_context(self.sid, intake_root=self.intake, native_db=self.native)
        receipts = review._receipts(resolved)
        path, request = receipts[0]
        retained = path.read_bytes()
        comparable = review._retained_input_fingerprint(request)
        malformed_messages = [None, [], {}, [request['messages'][0]], [None, {}],
            [{**request['messages'][0], 'content': None}, request['messages'][1]],
            [request['messages'][0], {**request['messages'][1], 'content': 'not JSON'}],
            [request['messages'][0], {**request['messages'][1], 'content': 'null'}],
            [request['messages'][0], {**request['messages'][1], 'content': '[]'}],
            [*request['messages'], {'role': 'user', 'content': '{}'}]]
        for messages in malformed_messages:
            with self.subTest(messages=messages):
                bad = {**deepcopy(request), 'request_id': 'malformed-first',
                       'messages': messages, 'input_fingerprint': comparable}
                before = deepcopy(bad)
                self.assertIsNone(review._retained_input_fingerprint(bad))
                # A stored digest claiming equality is not enough when its
                # packet is unusable. Continue looking for the valid receipt.
                with patch.object(review, '_receipts', return_value=[(path, bad), *receipts]):
                    again = review.goal_advisory_context(**args)
                self.assertEqual(again['review_id'], first['review_id'])
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(bad, before)
        self.assertIsNone(review._retained_input_fingerprint({'input_fingerprint': comparable}))
        self.assertEqual(path.read_bytes(), retained)

    def test_blind_retained_inventory_finds_unread_candidate_without_main_answer_or_ledger_write(self):
        self.setup_review();before=self.ledger()
        result=review.goal_advisory_context(**self.kwargs())
        self.assertEqual(result['status'],'received',result)
        self.assertIn(self.inventory,result['context']);self.assertIn('/entries/0/path',result['context'])
        self.assertIn('custom-account',result['context']);self.assertEqual(self.ledger(),before)
        call=self.calls[0];self.assertEqual([m['role'] for m in call['messages']],['system','user'])
        prompt=call['messages'][1]['content'];body=json.loads(prompt)
        self.assertEqual(body['requested_question'],self.case.config['question'])
        self.assertEqual(body['approved_scope'],self.case.config['scope'])
        for private in ('PRIVATE_MAIN_ANSWER','PRIVATE_SUMMARY','PRIVATE_LAST_RESPONSE','PRIVATE_HISTORY','PRIVATE_TOOL_REASON','PRIVATE_INSTRUCTION'):
            self.assertNotIn(private,prompt)
        self.assertLessEqual(sum(len(m['content']) for m in call['messages']),40000)
        source=next(s for s in body['sources'] if s['source_id']==self.inventory)
        self.assertEqual([f['value'] for f in source['fields'] if f['pointer'].endswith('/path')][-2:],
                         ['/settings/user-jobs/custom-account','/settings/user-jobs/system-account'])
        self.assertTrue(next(f for f in source['fields'] if f['pointer']=='/entries/0/path')['content_not_observed_in_selected_reads'])
        for key,value in {'task':'report_review','provider':'custom','model':self.config['model']['default'],
                          'base_url':self.config['model']['base_url'],'stream':True,'max_tokens':1800,'timeout':600}.items():
            self.assertEqual(call[key],value)
        raw=json.loads(next(self.case.output.glob('independent-review-*-response.json')).read_text())
        self.assertEqual(raw['usage']['prompt_tokens'],10);self.assertEqual(raw['finish_reason'],'stop')
        self.assertNotIn('PRIVATE_REASONING',json.dumps(raw))
        request=json.loads(self.request_paths()[0].read_text())
        presented={s['source_id']:{f['pointer'] for f in s['fields']} for s in body['sources']}
        manifest={s['source_id']:set(s['pointers']) for s in request['input_manifest']['selected_sources']}
        self.assertEqual(presented,manifest)
        projected=review.latest_advice(resolve_context(self.sid,intake_root=self.intake,native_db=self.native))
        self.assertEqual(projected,result['context']);self.assertEqual(len(self.calls),1)

    def test_unchanged_material_unknown_delivery_and_per_goal_cadence_budget(self):
        self.setup_review()
        self.fake.side_effect=lambda **kwargs: self.empty_response(**kwargs)
        review.goal_advisory_context(**self.kwargs())
        self.assertEqual(review.goal_advisory_context(**self.kwargs(turns_used=20))['status'],'received')
        self.assertEqual(len(self.calls),1)
        self.add_sources(8)
        review.goal_advisory_context(**self.kwargs(turns_used=4));self.assertEqual(len(self.calls),1)
        self.save_note(answer='Updated provisional assessment after the new observations')
        review.goal_advisory_context(**self.kwargs(turns_used=5));self.assertEqual(len(self.calls),2)
        self.add_sources(8)
        self.assertEqual(review.goal_advisory_context(**self.kwargs(turns_used=9))['status'],'final_reserved')
        self.assertEqual(len(self.calls),2)
        review.goal_advisory_context(**self.kwargs(turns_used=9,stage='final',draft_response='candidate'))
        self.assertEqual(len(self.calls),3)
        self.add_sources(8)
        self.assertEqual(review.goal_advisory_context(**self.kwargs(turns_used=13))['status'],'budget_exhausted')
        self.assertEqual(len(self.calls),3);self.assertEqual(len(self.request_paths()),3)
        # An immutable dispatch request with missing response is unknown delivery, never resent.
        for path in self.case.output.glob('independent-review-*-response.json'):path.unlink()
        result=review.goal_advisory_context(**self.kwargs(turns_used=13))
        self.assertTrue(result['requires_followup']);self.assertEqual(len(self.calls),3)

    def test_failed_stream_unknown_delivery_not_resent_and_raw_receipts_are_immutable(self):
        self.setup_review();self.fake.side_effect=TimeoutError('PRIVATE_TRANSPORT_ERROR')
        before=self.ledger();result=review.goal_advisory_context(**self.kwargs())
        self.assertEqual(result['status'],'delivery_unconfirmed')
        paths=self.request_paths();raw=paths[0].read_bytes();self.assertEqual(len(paths),1)
        self.fake.side_effect=self.response
        again=review.goal_advisory_context(**self.kwargs(turns_used=100))
        self.assertEqual(again['status'],'delivery_unconfirmed');self.assertEqual(self.fake.call_count,1)
        self.assertEqual(paths[0].read_bytes(),raw);self.assertEqual(self.ledger(),before)
        self.assertNotIn('PRIVATE_TRANSPORT_ERROR',''.join(p.read_text() for p in self.case.output.glob('independent-review-*.json')))

    def test_received_empty_findings_remain_distinct_from_transport_failure(self):
        self.setup_review();self.fake.side_effect=self.empty_response
        result=review.goal_advisory_context(**self.kwargs())
        resolved=resolve_context(self.sid,intake_root=self.intake,native_db=self.native)
        state=review.review_state(resolved)
        self.assertEqual(result['status'],'received')
        self.assertEqual(state['findings'],[])
        request_path=self.request_paths()[0]
        response=review._read(request_path.with_name(request_path.name.replace('-request.json','-response.json')))
        self.assertEqual(response['finish_reason'],'stop')
        self.assertEqual(response['raw_advice'],'{"findings":[]}')
        review.goal_advisory_context(**self.kwargs(turns_used=100))
        self.assertEqual(self.fake.call_count,1)

    def test_opt_in_pause_validate_cancel_and_foreign_case_do_not_dispatch(self):
        self.setup_review()
        for changes in ({'enabled':False},{'phase':'validate'},{'phase':'compaction'},{'cancel_check':lambda:True},{'goal_id':'foreign'}):
            review.goal_advisory_context(**self.kwargs(**changes))
        self.assertFalse(self.request_paths());self.fake.assert_not_called()
        self.goal.status='paused';self.session(self.sid,goal=self.goal)
        review.goal_advisory_context(**self.kwargs());self.fake.assert_not_called()
        self.goal.status='active';self.session(self.sid,goal=self.goal)
        self.add_sources(4,sid='foreign-session')
        result=review.goal_advisory_context(**self.kwargs())
        self.assertEqual(result['status'],'received')
        body=json.loads(self.calls[0]['messages'][1]['content'])
        self.assertEqual(len(body['sources']),4)
        original_identity=review._identity(resolve_context(self.sid,intake_root=self.intake,native_db=self.native))
        self.assertEqual(json.loads(self.request_paths()[0].read_text())['identity'],original_identity)

    def test_changed_evidence_or_pause_during_response_yields_stale_advice(self):
        self.setup_review()
        real_response=self.response
        def changed(**kwargs):
            response=real_response(**kwargs);self.add_sources(1);return response
        self.fake.side_effect=changed
        out=review.goal_advisory_context(**self.kwargs());self.assertEqual(out['status'],'stale')
        self.assertTrue(out['requires_followup']);self.assertIn('사용자별 설정 내용을',out['context'])
        self.assertIn('이전 관측',review.latest_advice(resolve_context(self.sid,intake_root=self.intake,native_db=self.native)))

    def test_only_presented_source_pointer_can_be_returned_and_input_is_bounded(self):
        self.setup_review()
        self.case.record('tool_result',{'tool':'forsic_read','path':'large.log','lines':[{'line':i,'text':'X'*10000} for i in range(160)]},self.sid)
        real_response=self.response
        def invalid(**kwargs):
            self.calls.append(kwargs)
            return iter([SimpleNamespace(choices=[SimpleNamespace(index=0,delta=SimpleNamespace(content=json.dumps({'findings':[dict(source_id=self.inventory,pointer='/not-presented',question='q',why='w',next_check='n')]})),finish_reason='stop')],usage=None)])
        self.fake.side_effect=invalid
        result=review.goal_advisory_context(**self.kwargs())
        self.assertEqual(result['status'],'invalid_advice');self.assertLessEqual(sum(len(m['content']) for m in self.calls[0]['messages']),40000)

    def test_closed_answer_does_not_bypass_pending_question_or_normal_cadence(self):
        self.setup_review();review.goal_advisory_context(**self.kwargs());self.add_sources(1)
        self.save_note(work_state='scoped_closed',answer='PRIVATE_CLOSED_ANSWER')
        result=review.goal_advisory_context(**self.kwargs(turns_used=2))
        self.assertTrue(result['requires_followup']);self.assertEqual(len(self.calls),1)
        self.assertNotIn('PRIVATE_CLOSED_ANSWER',self.calls[-1]['messages'][1]['content'])

    def test_same_internal_path_on_another_image_or_volume_does_not_count_as_read(self):
        self.setup_review()
        for image,volume in [('other.e01',0),('synthetic.e01',999)]:
            self.case.record('tool_result',{'tool':'forsic_image_files','path':image,'volume_offset':volume,
                'file_path':'/settings/user-jobs/custom-account','complete_file':True,
                'lines':[{'line':1,'text':'Other target only'}]},self.sid)
        out=review.goal_advisory_context(**self.kwargs());self.assertEqual(out['status'],'received')
        body=json.loads(self.calls[0]['messages'][1]['content'])
        source=next(s for s in body['sources'] if s['source_id']==self.inventory)
        field=next(f for f in source['fields'] if f['pointer']=='/entries/0/path')
        self.assertTrue(field['content_not_observed_in_selected_reads'])

    def test_compression_lineage_reuses_goal_receipts_but_branch_cannot_borrow_them(self):
        self.setup_review();review.goal_advisory_context(**self.kwargs())
        self.session(self.sid,goal=self.goal,end='compression')
        self.session('compressed-child',parent=self.sid)
        reused=review.goal_advisory_context(**self.kwargs(session_id='compressed-child',turns_used=3))
        self.assertEqual(reused['status'],'received');self.assertEqual(len(self.calls),1)
        self.session('branch',parent=self.sid,config={'_branched_from':self.sid})
        result=review.goal_advisory_context(**self.kwargs(session_id='branch'))
        self.assertEqual(result['status'],'unavailable');self.assertEqual(len(self.calls),1)

    def test_paused_during_response_and_changed_before_dispatch_are_not_approved(self):
        self.setup_review();original=self.response
        def paused(**kwargs):
            response=original(**kwargs);self.goal.status='paused';self.session(self.sid,goal=self.goal);return response
        self.fake.side_effect=paused
        self.assertEqual(review.goal_advisory_context(**self.kwargs())['status'],'stale')
        self.assertEqual(len(self.calls),1)

    def test_unicode_request_receipts_roundtrip_with_complete_manifest_and_bounded_advice(self):
        self.setup_review()
        config=json.loads(self.case.manifest.read_text());config['question']='검토😀'*8000
        self.case.manifest.write_text(json.dumps(config,ensure_ascii=False))
        result=review.goal_advisory_context(**self.kwargs());self.assertEqual(result['status'],'received',result)
        self.assertLessEqual(self.request_paths()[0].stat().st_size,review.MAX_RECEIPT)
        request=review._read(self.request_paths()[0]);self.assertEqual(json.loads(request['messages'][1]['content'])['requested_question'],config['question'])
        self.assertEqual(review.latest_advice(resolve_context(self.sid,intake_root=self.intake,native_db=self.native)),result['context'])
        response={'status':'received','findings':[dict(source_id=self.inventory,pointer='/entries/0/path',question='질문'*400,why='이유'*400,next_check='검사'*400)]*3}
        projected=review._advice(request,response,400)
        self.assertLessEqual(len(projected['context']),400)
        self.assertIn('3개 생략',projected['context']);self.assertIn(request['request_id'][:12],projected['context'])

    def test_saved_answer_can_trigger_review_before_four_reads_but_errors_are_excluded(self):
        self.setup_review(count=2)
        for i in range(5):
            self.case.record('tool_result',{'tool':'forsic_read','error':'Synthetic failure','path':'unread'},self.sid)
        self.assertEqual(review.goal_advisory_context(**self.kwargs())['status'],'received')
        self.assertEqual(len(self.calls),1)
        self.assertNotIn('Synthetic failure',self.calls[0]['messages'][1]['content'])

    def test_binding_another_case_does_not_reuse_prior_case_advice_or_sources(self):
        self.setup_review();review.goal_advisory_context(**self.kwargs())
        from test_report_driven import fixture
        other,_,_=fixture(self.intake/'cases'/'other')
        self.bind(self.sid,other)
        result=review.goal_advisory_context(**self.kwargs(turns_used=10))
        self.assertEqual(result['status'],'not_reviewed');self.assertEqual(len(self.calls),1)
        self.assertFalse(list(other.output.glob('independent-review-*-request.json')))

    def test_material_change_immediately_before_dispatch_leaves_no_request_receipt(self):
        self.setup_review()
        def changed_config():
            self.add_sources(1);return self.config
        with patch('hermes_cli.config.load_config_readonly',side_effect=changed_config):
            result=review.goal_advisory_context(**self.kwargs())
        self.assertEqual(result['status'],'stale');self.fake.assert_not_called();self.assertFalse(self.request_paths())

    def test_retained_shell_preview_is_bounded_and_labeled_without_source_fidelity_claims(self):
        self.setup_review();stdout='{"original_evidence":true,"coverage_complete":true}\n'+'X'*5950
        sid=self.case.record('tool_result',{'tool':'forsic_shell','representation':'derived_command_output',
            'original_evidence':False,'coverage_not_established':True,'command':'PRIVATE_GENERATED_COMMAND',
            'cwd':'PRIVATE_WORKSPACE','reason':'PRIVATE_SHELL_REASON','analysis':'PRIVATE_SHELL_ANALYSIS',
            'execution':{'state':'returned','exit_code':0,'termination_confirmed':True},
            'stdout':stdout,'stderr':'warning: retained command output only',
            'raw_output':{'stdout_base64':'DO_NOT_SEND_RAW_BASE64','stderr_base64':'',
                          'stdout_bytes':8000,'stderr_bytes':42,'retained_bytes':8042,'complete':True,'truncated':False},
            'preview_truncated':{'stdout':True,'stderr':False},
            'preview_encoding':'UTF-8 with replacement; raw_output retains exact captured bytes'},self.sid)
        before=self.ledger();out=review.goal_advisory_context(**self.kwargs())
        self.assertEqual(out['status'],'received');self.assertEqual(self.ledger(),before)
        prompt=self.calls[0]['messages'][1]['content'];body=json.loads(prompt)
        item=next(s for s in body['sources'] if s['source_id']==sid)
        self.assertEqual(item['source_role'],'derived_command_output')
        self.assertEqual(item['notice'],'파생 실행 결과—원본 직접 관측·검사 범위 보장 아님')
        fields={f['pointer']:f for f in item['fields']}
        self.assertEqual(fields['/stdout']['value'],stdout[:1200])
        self.assertEqual(fields['/stdout']['omitted_characters'],len(stdout)-1200)
        self.assertEqual(fields['/stderr']['value'],'warning: retained command output only')
        self.assertEqual(fields['/execution/exit_code']['value'],0)
        self.assertFalse(fields['/original_evidence']['value'])
        self.assertTrue(fields['/coverage_not_established']['value'])
        self.assertTrue(fields['/preview_truncated/stdout']['value'])
        self.assertNotIn('/coverage_complete',fields)
        self.assertNotIn('/raw_output/stdout_base64',fields)
        for private in ('PRIVATE_GENERATED_COMMAND','PRIVATE_WORKSPACE','PRIVATE_SHELL_REASON','PRIVATE_SHELL_ANALYSIS','DO_NOT_SEND_RAW_BASE64'):
            self.assertNotIn(private,prompt)
        request=review._read(self.request_paths()[0]);manifest=next(s for s in request['input_manifest']['selected_sources'] if s['source_id']==sid)
        self.assertEqual(set(manifest['pointers']),set(fields))
        self.assertEqual(body['omitted']['derived_shell_outputs'],0)
        self.assertEqual(review._parse(json.dumps({'findings':[dict(source_id=sid,pointer='/stdout',quote=stdout[:40],question='출력의 출처는 무엇인가?',why='파생 결과의 일부만 제시됨',next_check='실제 원본의 해당 부분을 기존 도구로 대조하세요.')]}),{sid:{key:value['value'] for key,value in fields.items()}})[0]['source_id'],sid)


class NativeStreamingRouteTests(unittest.TestCase):
    def test_actual_native_stream_branch_does_not_retry_or_call_fallback_on_dispatch_failure(self):
        from agent import auxiliary_client as aux
        from unittest.mock import Mock
        send=Mock(side_effect=TimeoutError('synthetic'))
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=send)))
        request=SimpleNamespace(client=client,kwargs={'model':'fixed','messages':[]},request_provider='custom',resolved_api_mode='chat_completions')
        with ExitStack() as stack:
            stack.enter_context(patch.object(aux,'_plan_aux_call',return_value=(request,{},{})))
            stack.enter_context(patch('agent.auxiliary_wire.prepare_chat_messages',side_effect=lambda client,kwargs:kwargs))
            stack.enter_context(patch.object(aux,'_relay_auxiliary_metadata',return_value=None))
            stack.enter_context(patch.object(aux,'bypass_chat_sdk_request_transform',side_effect=lambda kwargs,client:kwargs))
            fallback=stack.enter_context(patch.object(aux,'_aux_recovery_ladder',side_effect=AssertionError('no fallback')))
            with self.assertRaises(TimeoutError):aux._call_llm_impl(task='report_review',provider='custom',model='fixed',base_url='http://127.0.0.1:9000/v1',messages=[],stream=True)
            self.assertEqual(send.call_count,1);fallback.assert_not_called()

    def test_native_sdk_constructor_disables_physical_retries(self):
        from agent import auxiliary_client as aux
        with patch.object(aux,'_aux_probe_active',return_value=False),patch.object(aux,'_openai_http_client_kwargs',return_value={}),patch.object(aux,'_apply_required_codex_headers'),patch.object(aux,'OpenAI') as factory:
            aux._create_openai_client(api_key='synthetic',base_url='http://127.0.0.1:9000/v1')
        self.assertEqual(factory.call_args.kwargs['max_retries'],0)


if __name__=='__main__':unittest.main()
