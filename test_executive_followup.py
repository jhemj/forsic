"""Bounded independent executive questions; native model streams are local fakes."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from forsic_plugin import independent_review as review
from forsic_plugin.investigation_context import resolve_context
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.grounded_judgment import read_refs
import test_independent_review as fixtures


class ExecutiveFollowupTests(unittest.TestCase):
    setUp=fixtures.IndependentReviewTests.setUp
    bind=fixtures.IndependentReviewTests.bind
    session=fixtures.IndependentReviewTests.session
    save_note=fixtures.IndependentReviewTests.save_note
    setup_review=fixtures.IndependentReviewTests.setup_review
    add_sources=fixtures.IndependentReviewTests.add_sources
    kwargs=fixtures.IndependentReviewTests.kwargs
    request_paths=fixtures.IndependentReviewTests.request_paths

    def resolved(self):
        return resolve_context(self.sid,intake_root=self.intake,native_db=self.native)

    def prepare(self):
        self.setup_review()
        for field in ('answer','summary','last_response'):
            self.note['investigation'].pop(field,None)
        self.save_note()

    def response(self,**kwargs):
        body=json.loads(kwargs['messages'][1]['content'])
        executive=body.get('executive_followup')
        if not executive:return fixtures.IndependentReviewTests.response(self,**kwargs)
        self.calls.append(deepcopy(kwargs))
        anchor=body['sources'][0]['fields'][0]['review_ref']
        number=executive['round']
        findings=[dict(review_ref=anchor,question=f'임원 {number}회 질문 {index}: 현재 판단의 미확인 범위는 무엇인가?',
            why='제시된 관측의 범위와 결론을 구별해 확인한다.',next_check=f'현재 자료에서 {number}회 {index}번의 판별 근거를 확인한다.')
            for index in range(1,6)]
        text=json.dumps({'findings':findings},ensure_ascii=False)
        return iter([SimpleNamespace(choices=[SimpleNamespace(index=0,delta=SimpleNamespace(content=text),finish_reason='stop')],usage=None)])

    def call(self,**changes):
        return review.goal_advisory_context(**self.kwargs(stage='final',executive_followup=True,**changes))

    def answer(self,findings,*,limited=False):
        """Use the real question writer, with current-case retained citations."""
        start=self.case.record('tool_start',{'tool':'forsic_read','arguments':{'path':'followup.log'}},self.sid)
        data={'tool':'forsic_read','started_id':start,'path':'followup.log',
              'lines':[{'line':1,'text':'The supplied record observes configuration only, not successful execution.'}]}
        source=self.case.record('tool_result',data,self.sid)
        cite=read_refs(self.case,{**data,'evidence_id':source})['presented_spans'][0]['citation_ref']
        saved=[]
        for index,finding in enumerate(findings):
            response=dict(review_id=finding['review_id'],finding_id=finding['finding_id'],
                disposition='scope_limited' if limited else 'investigated',reason='현재 자료에서 확인한 범위만 답한다.',
                citation_refs=[] if limited else [cite])
            if limited:response['limitation']='선택 자료에는 요청한 내용이 없어 확인하지 못했다. 새 취득 자료가 필요하다.'
            payload=dict(operation_id=uuid.uuid4().hex,question=finding['question'],scope='Acquired synthetic records',
                answer=f"주 조사 답 {finding.get('executive_round',0)}회 {index+1}: 설정 관측만 있으며 실행 성공은 미확인이다.",
                reason='실제 보존 원문과 남은 한계를 비교했다.',review_responses=[response])
            if not limited:payload['judgment']=dict(citation_refs=[cite],assertion_kind='interpretation',
                reasoning_summary='설정 기록은 실행 성공을 입증하지 않는다.')
            result=host.invoke(self.case,{'action':'question','payload':payload,
                '_goal_id':self.resolved()['goal_id'],'_session_id':self.sid,
                '_session_lineage':[self.sid],'_question_contract':'v2'})
            self.assertNotIn('error',result,result)
            saved.append(result)
        return saved

    def test_three_rounds_have_five_questions_and_receive_all_previous_main_answers(self):
        from forsic_plugin.report_driven.finalization import _review_gate, independent_review_projection
        self.prepare()
        for number in range(1,4):
            before=self.case.db.read_bytes()
            result=self.call(draft_response='현재 보고서의 초안')
            self.assertEqual(self.case.db.read_bytes(),before)  # Advisory never writes case judgments.
            self.assertEqual(result['status'],'received',result)
            self.assertEqual(len(self.calls),number)
            projected=review.review_state(self.resolved())
            current=[f for f in projected['findings'] if f.get('executive_round')==number]
            self.assertEqual(len(current),5)
            self.assertEqual(len({f['question'] for f in projected['findings']}),number*5)
            self.assertFalse(projected['final_review_current'])
            _,blocked=_review_gate(self.case,host.current(self.case),{'bundle_id':'synthetic'},
                {'enabled':True,'resolved':self.resolved()})
            self.assertFalse(blocked['finalized'])
            for finding in current:self.assertIn(finding['question'],result['context'])
            self.assertLessEqual(len(result['context']),3500)
            self.assertEqual(self.call()['status'],'executive_waiting')
            self.assertEqual(len(self.calls),number)
            body=json.loads(self.calls[-1]['messages'][1]['content'])
            previous=body['executive_followup']['previous_round']
            self.assertEqual(len(previous['questions']),0 if number==1 else 5)
            self.assertIn('not_source_evidence',previous['role'])
            if number>1:
                self.assertEqual(len(previous['main_answers']),5)
                for item in previous['questions']:
                    self.assertTrue(item['main_response']['citations'])
                    self.assertEqual(item['main_response']['disposition'],'investigated')
                for answer in previous['main_answers']:
                    self.assertIn(f'주 조사 답 {number-1}회',answer['answer'])
            self.answer(current)
        complete=self.call()
        self.assertEqual(len(self.calls),3)
        projected=review.review_state(self.resolved())
        self.assertTrue(projected['executive_followup']['complete'])
        self.assertTrue(projected['final_review_current'])
        self.assertFalse(complete['requires_followup'])
        review.goal_advisory_context(**self.kwargs(stage='exploration',executive_followup=True,turns_used=100))
        self.assertEqual(len(self.calls),3)
        latest=review.advisory_at_boundary(self.resolved())
        self.assertEqual(latest['status'],'received')
        self.assertIn('3회·15개',latest['context'])
        self.assertEqual(len(projected['dispositions']),15)
        self.assertFalse(projected['executive_followup']['semantic_approval'])
        context={'enabled':True,'resolved':self.resolved()}
        disclosure=independent_review_projection(context,host.current(self.case))
        self.assertTrue(disclosure['executive_followup']['complete'])
        _,blocked=_review_gate(self.case,host.current(self.case),
            {'bundle_id':'synthetic','independent_review':disclosure},context)
        self.assertIsNone(blocked)
        self.assertEqual([review._read(p)['executive_round'] for p in sorted(self.request_paths(),key=lambda p:review._read(p)['created_ns'])],[1,2,3])
        for call in self.calls:
            self.assertEqual(call['model'],self.config['model']['default'])
            self.assertEqual(call['max_tokens'],4000)
            self.assertLessEqual(sum(len(m['content']) for m in call['messages']),review.MAX_INPUT)
        # Retracting today's verdict requires a new grounded judgment; the
        # completed investigation responses remain separate and reusable.
        state=host.current(self.case)
        question=next(q for q in state['questions'] if q.get('review_responses'))
        host.invoke(self.case,{'action':'question','payload':dict(operation_id=uuid.uuid4().hex,
            question_id=question['id'],expected_revision=question['revision'],
            answer='정정된 현재 답의 근거 범위는 다시 검토해야 한다.',claim_action='retract',reason='현재 답의 해석을 정정한다.'),
            '_goal_id':self.resolved()['goal_id'],'_session_id':self.sid,
            '_session_lineage':[self.sid],'_question_contract':'v2'})
        self.assertFalse(review.review_state(self.resolved())['final_review_current'])
        self.assertEqual(self.call()['status'],'executive_waiting')
        self.assertEqual(len(self.calls),3)
        pending=review.review_state(self.resolved())
        self.assertTrue(pending['executive_followup']['complete'])
        self.assertEqual(len(pending['dispositions']),15)
        self.assertEqual(pending['pending_findings'],[])
        question=next(q for q in host.current(self.case)['questions'] if q['id']==question['id'])
        citation=question['review_responses'][0]['citation_refs'][0]
        host.invoke(self.case,{'action':'question','payload':dict(operation_id=uuid.uuid4().hex,
            question_id=question['id'],expected_revision=question['revision'],
            answer='정정된 답은 현재 원문에서 관측한 설정 범위로 한정한다.',reason='현재 원문과 판단의 범위를 다시 비교했다.',
            judgment={'citation_refs':[citation],'assertion_kind':'interpretation',
                      'reasoning_summary':'현재 관측은 설정에 한정되며 실행 성공을 입증하지 않는다.'}),
            '_goal_id':self.resolved()['goal_id'],'_session_id':self.sid,
            '_session_lineage':[self.sid],'_question_contract':'v2'})
        restored=review.review_state(self.resolved())
        self.assertTrue(restored['final_review_current'])
        self.assertFalse(restored['requires_followup'])
        self.assertEqual(len(restored['dispositions']),15)
        self.call()
        self.assertEqual(len(self.calls),3)

    def test_large_expanded_citations_cannot_prevent_the_next_mandatory_round(self):
        self.prepare()
        for _ in range(2):
            self.call()
            self.answer(review.review_state(self.resolved())['pending_findings'])
        resolved = self.resolved()
        projection = review.review_state(resolved)
        snapshot = review._collect(resolved, 'final', 'Current report draft')
        expected = {}
        for response in projection['dispositions'][-5:]:
            expected[response['finding_id']] = {key: deepcopy(value) for key, value in response.items() if key != 'citations'}
            # Native expanded coordinates can be much larger than exact saved
            # reference handles. They are metadata copies, not five new answers.
            response['citations'] = [{'literal': 'retained coordinate metadata ' * 350}]
        before = deepcopy(projection)
        packet = review._executive_snapshot(resolved, snapshot, 3, projection)
        self.assertEqual(projection, before)  # Projection remains read-only.
        self.assertLessEqual(len(packet['prompt']) + len(packet['system']), review.MAX_INPUT)
        previous = json.loads(packet['prompt'])['executive_followup']['previous_round']
        self.assertEqual(len(previous['questions']), 5)
        self.assertEqual(previous['citation_projection']['expanded_coordinates_omitted'], 5)
        for item in previous['questions']:
            self.assertEqual(item['main_response'], expected[item['finding_id']])
            self.assertTrue(item['main_response']['citation_refs'])
        # The native final boundary still dispatches the required third round.
        self.assertEqual(self.call()['status'], 'received')
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(review.review_state(resolved)['executive_followup']['rounds'][-1]['round'], 3)

    def test_irreducible_packet_failure_is_explicit_nonactionable_and_does_not_dispatch(self):
        self.prepare()
        self.call()
        self.answer(review.review_state(self.resolved())['pending_findings'])
        error = ValueError('Complete executive question/answer history exceeds the review input bound')
        with patch.object(review, '_executive_snapshot', side_effect=error):
            result = self.call()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result['status'], 'unavailable')
        self.assertTrue(result['requires_followup'])
        self.assertFalse(result['followup_actionable'])
        self.assertEqual(result['diagnostic']['code'], 'executive_history_budget')
        self.assertEqual(result['diagnostic']['phase'], 'final_packet')
        self.assertEqual(result['diagnostic']['recovery']['arguments']['action'], 'review_inbox')
        self.assertNotIn('provider', result['diagnostic']['reason'])

    def test_review_progress_requires_question_changes_not_unrelated_reads_or_prose(self):
        self.prepare()
        self.call(draft_response='First draft')
        before = review.review_state(self.resolved())
        again = self.call(draft_response='Another unsupported completion claim')
        self.assertEqual(again['progress_fingerprint'], before['progress_fingerprint'])
        self.assertEqual(again['followup_action'], 'respond')
        self.add_sources(1)
        changed = review.review_state(self.resolved())
        self.assertEqual(changed['progress_fingerprint'], before['progress_fingerprint'])
        self.assertEqual(changed['pending_finding_ids'], before['pending_finding_ids'])
        self.save_note(answer='Updated current answer with an explicit remaining limitation.')
        changed = review.review_state(self.resolved())
        self.assertNotEqual(changed['progress_fingerprint'], before['progress_fingerprint'])
        self.assertEqual(changed['pending_finding_ids'], before['pending_finding_ids'])

    def test_honest_unavailable_answers_advance_but_partial_answers_do_not(self):
        self.prepare();first=self.call()
        findings=review.review_state(self.resolved())['findings']
        self.answer(findings[:4],limited=True)
        waiting=self.call()
        self.assertEqual(waiting['status'],'executive_waiting')
        self.assertEqual(len(self.calls),1)
        self.assertIn(findings[4]['question'],waiting['context'])
        self.answer(findings[4:],limited=True)
        self.call()
        self.assertEqual(len(self.calls),2)
        previous=json.loads(self.calls[-1]['messages'][1]['content'])['executive_followup']['previous_round']
        self.assertEqual(len(previous['questions']),5)
        for item in previous['questions']:
            self.assertEqual(item['main_response']['disposition'],'scope_limited')
            self.assertTrue(item['main_response']['limitation'])
            self.assertEqual(item['main_response']['citations'],[])

    def test_ordinary_final_receipts_do_not_consume_executive_rounds(self):
        self.prepare()
        for index in range(3):
            review.goal_advisory_context(**self.kwargs(stage='final',draft_response=str(index)))
        self.assertEqual(len(self.calls),3)
        old=review.review_state(self.resolved())['pending_findings']
        self.assertEqual(self.call()['status'],'executive_waiting')
        self.answer(old,limited=True)
        result=self.call()
        self.assertEqual(len(self.calls),4)
        self.assertEqual(result['executive_followup']['rounds'][0]['round'],1)

    def test_completed_generic_rounds_require_current_report_review_without_restarting_dialogue(self):
        from test_report_narrative import ReportNarrativeTests, answerability_checks
        from forsic_plugin.report_driven.narrative import material, status
        from forsic_plugin.report_driven.template_contract import WRITING_ORDER, REVIEW_CHECKS
        self.prepare()
        for number in range(1,4):
            self.call()
            findings=[f for f in review.review_state(self.resolved())['findings']
                      if f.get('executive_round')==number]
            self.answer(findings)
        self.assertTrue(review.review_state(self.resolved())['executive_followup']['complete'])
        self.assertEqual(len(self.calls),3)
        native={'_session_id':self.resolved()['conversation_id'],'_goal_id':self.resolved()['goal_id'],
            '_independent_review_options':{'enabled':True,'executive_followup':True,'session_id':self.sid,
                'intake_root':str(self.intake),'native_db':str(self.native)}}
        # Main-agent document writing/review cannot certify a newer report for
        # Ren. Its ordinary final call preserves the completed three rounds.
        author=ReportNarrativeTests('test_native_render_requests_authored_prose_not_blank_template')
        author.case=self.case
        keys=[row['key'] for row in material(host.current(self.case))
              if row['kind']=='question' or row['kind']=='gap' and row.get('impact') in ('material','decision_critical')]
        for group in WRITING_ORDER:
            packet=host.invoke(self.case,{'action':'compose','section':group,**native})
            host.invoke(self.case,{'action':'compose','section':group,'operation_id':'native-author-'+group,
                'edit_ref':packet['save_contract']['edit_ref'],'question_ids':packet['question_ids'],
                'draft':author.draft(group,packet,keys=keys),**native})
        packet=host.invoke(self.case,{'action':'compose','section':'review',**native})
        host.invoke(self.case,{'action':'compose','section':'review','operation_id':'native-document-review',
            'edit_ref':packet['save_contract']['edit_ref'],'question_ids':packet['question_ids'],
            'review':{'summary':'Synthetic main-agent whole-document comparison against preserved evidence.',
                'reviewed_node_ids':packet['reviewed_node_ids'],
                'answerability_checks':answerability_checks(),
                'checks':[{'check':check,'result':'satisfied','reason':'Synthetic scope and uncertainty comparison.'}
                          for check in REVIEW_CHECKS], 'comparisons':[{
                    'node_ids':[packet['reviewed_node_ids'][0]], 'basis_keys':[],
                    'statement':'The synthetic draft preserves an unconfirmed connection.',
                    'source_support':'No common task identity establishes the connection.',
                    'limits':'Authorization and final execution success remain unknown.',
                    'assessment':'Retain the qualified account within the selected scope.'}], 'findings':[]},**native})
        self.assertTrue(status(host.current(self.case))['ready'])
        self.fake.side_effect=lambda **kw: fixtures.IndependentReviewTests.empty_response(self,**kw)
        report=host.invoke(self.case,{'action':'render',**native})
        result=host.invoke(self.case,{'action':'finalize','bundle_id':report['bundle_id'],**native})
        self.assertEqual(result['delivery_status'],'independent_review_rerender_required')
        self.assertIsNone(result['review']['availability_status'])
        self.assertTrue(result['review']['final_review_current'])
        self.assertTrue(result['review']['executive_followup']['complete'])
        report=host.invoke(self.case,{'action':'render',**native})
        result=host.invoke(self.case,{'action':'finalize','bundle_id':report['bundle_id'],**native})
        self.assertTrue(result['finalized'])
        self.assertEqual(len(self.calls),4)

    def test_invalid_question_count_and_duplicate_or_unpresented_anchor_never_advance(self):
        self.prepare()
        source=next(iter(review._collect(self.resolved(),stage='final')['review_refs']))
        good=dict(review_ref=source,question='새 질문',why='현재 범위 확인',next_check='보존 원문 확인')
        for findings in ([good]*4,[dict(good,question=str(i)) for i in range(6)],
                         [good]*5,[dict(good,question=str(i),review_ref='R1:invented') for i in range(5)]):
            with self.assertRaises(ValueError):
                review._parse(json.dumps({'findings':findings}),{},review_refs={source:{'source_id':'x','source_version':'v','pointer':'/x','quote':'x'}},executive_round=1)
        text=json.dumps({'findings':[dict(good,question=str(i)) for i in range(5)]})
        with self.assertRaises(ValueError):
            review._parse(text,{},review_refs={source:{'source_id':'x','source_version':'v','pointer':'/x','quote':'x'}},executive_round=2,previous_questions=['0'])
        self.fake.side_effect=lambda **_:iter([SimpleNamespace(choices=[SimpleNamespace(index=0,
            delta=SimpleNamespace(content=json.dumps({'findings':[]})),finish_reason='stop')],usage=None)])
        result=self.call()
        self.assertEqual(result['status'],'invalid_advice')
        state=review.review_state(self.resolved())
        self.assertFalse(state['executive_followup']['complete'])
        self.assertEqual(state['executive_followup']['rounds'][0]['question_count'],0)
        self.answer(state['pending_findings'],limited=True)
        self.call()
        self.assertEqual(self.fake.call_count,1)
        self.assertFalse(review.review_state(self.resolved())['final_review_current'])

    def test_pause_cancel_and_failed_delivery_never_start_or_repeat_round(self):
        from forsic_plugin.report_driven.finalization import _review_gate, independent_review_projection
        self.prepare()
        self.goal.status='paused';self.session(self.sid,goal=self.goal)
        self.call();self.assertFalse(self.calls)
        self.goal.status='active';self.session(self.sid,goal=self.goal)
        self.call(cancel_check=lambda:True);self.assertFalse(self.calls)
        self.fake.side_effect=TimeoutError('synthetic failure')
        result=self.call()
        self.assertEqual(result['status'],'delivery_unconfirmed')
        state=review.review_state(self.resolved())
        self.answer(state['pending_findings'],limited=True)
        partial=self.call()
        self.assertEqual(self.fake.call_count,1)
        self.assertEqual(len(self.request_paths()),1)
        state=review.review_state(self.resolved())
        self.assertFalse(state['executive_followup']['complete'])
        self.assertEqual(state['executive_followup']['status'],'blocked')
        self.assertEqual(state['executive_followup']['blocked_status'],'delivery_unconfirmed')
        self.assertEqual(partial['status'],'delivery_unconfirmed')
        self.assertTrue(partial['requires_followup'])  # Still incomplete, but no model retry loop.
        self.assertFalse(partial['followup_actionable'])
        self.assertIn('미완료',partial['context'])
        context={'enabled':True,'resolved':self.resolved()}
        disclosure=independent_review_projection(context,host.current(self.case))
        _,blocked=_review_gate(self.case,host.current(self.case),
            {'bundle_id':'synthetic','independent_review':disclosure},context)
        self.assertEqual(blocked['delivery_status'],'independent_review_incomplete')
        self.assertTrue(blocked['draft_preserved'])

    def test_cancellation_during_executive_response_is_retained_without_second_call(self):
        self.prepare();cancelled=[False]
        response=self.response
        def interrupted(**kwargs):
            stream=response(**kwargs);cancelled[0]=True
            return stream
        self.fake.side_effect=interrupted
        result=self.call(cancel_check=lambda:cancelled[0])
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(len(self.calls),1)
        self.assertFalse(review.review_state(self.resolved())['executive_followup']['complete'])
        self.call()
        self.assertEqual(len(self.calls),1)

    def test_executive_question_anchor_is_not_a_current_evidence_citation(self):
        self.prepare();self.call()
        finding=review.review_state(self.resolved())['findings'][0]
        before=host.current(self.case)
        with self.assertRaises(ValueError):
            host.invoke(self.case,{'action':'question','payload':dict(operation_id=uuid.uuid4().hex,
                question='독립 질문을 관측으로 채택하는가?',scope='현재 자료',answer='질문 자체는 관측이 아니다.',
                reason='반론은 증거가 아니다.',judgment=dict(citation_refs=[finding['review_ref']],
                    assertion_kind='fact',reasoning_summary='원문 R1은 조언 좌표이며 C1/C2가 아니다.')),
                '_goal_id':self.resolved()['goal_id'],'_session_id':self.sid,
                '_session_lineage':[self.sid],'_question_contract':'v2'})
        self.assertEqual(host.current(self.case)['questions'],before['questions'])


if __name__=='__main__':unittest.main()
