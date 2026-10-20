"""Actual atomic question writer and persisted independent-review integration."""
from copy import deepcopy
import uuid
import unittest
import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from forsic_plugin import independent_review as review
from forsic_plugin.investigation_context import resolve_context
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.grounded_judgment import read_refs
import test_review_dispositions as fixtures


class ReviewResponseWriterTests(unittest.TestCase):
    setUp = fixtures.ReviewDispositionTests.setUp
    bind = fixtures.ReviewDispositionTests.bind
    session = fixtures.ReviewDispositionTests.session
    save_note = fixtures.ReviewDispositionTests.save_note
    setup_review = fixtures.ReviewDispositionTests.setup_review
    add_sources = fixtures.ReviewDispositionTests.add_sources
    kwargs = fixtures.ReviewDispositionTests.kwargs
    response = fixtures.ReviewDispositionTests.response
    request_paths = fixtures.ReviewDispositionTests.request_paths
    ledger = fixtures.ReviewDispositionTests.ledger

    def prepare(self):
        self.setup_review()
        for field in ('summary', 'last_response', 'answer'):
            self.note['investigation'].pop(field, None)
        self.save_note()
        result = review.goal_advisory_context(**self.kwargs(stage='exploration'))
        self.resolved = resolve_context(self.sid, intake_root=self.intake, native_db=self.native)
        self.response_item = {'review_id': result['review_id'], 'finding_id': result['pending_finding_ids'][0],
            'disposition':'scope_limited', 'reason':'현재 조사에서 실행 여부를 확인하지 못했다.',
            'limitation':'목록에서 발견한 항목의 본문과 실행 여부는 확인하지 못했다.', 'citation_refs':[]}
        data = {'tool':'forsic_read', 'path':'dynamic-user-config',
                'lines':[{'line':1,'text':'A benign scheduled backup is configured; execution is not observed.'}]}
        data['started_id'] = self.case.record('tool_start', {'tool':'forsic_read',
            'arguments':{'path':'dynamic-user-config'}}, self.sid)
        eid = self.case.record('tool_result', data, self.sid)
        self.cite = read_refs(self.case, {**data,'evidence_id':eid})['presented_spans'][0]['citation_ref']
        return result

    def write(self, item=None, **changes):
        state = host.current(self.case)
        q = next(q for q in state['questions'] if q['id'] == 'Q-'+self.note['note_id'])
        payload = {'operation_id':uuid.uuid4().hex, 'question_id':q['id'], 'expected_revision':q['revision'],
                   **changes}
        if item is not None: payload['review_responses'] = [item]
        args = {'action':'question', 'payload':payload, '_goal_id':self.resolved['goal_id'],
                '_session_id':self.sid, '_session_lineage':[self.sid], '_question_contract':'v2'}
        return host.invoke(self.case,args), args

    def test_scoped_limitation_uses_real_writer_and_survives_projection(self):
        result = self.prepare()
        saved, args = self.write(self.response_item)
        current = review.review_state(self.resolved)
        self.assertFalse(current['requires_followup'])
        self.assertEqual(current['dispositions'][0]['limitation'], self.response_item['limitation'])
        self.assertEqual(current['findings'][0]['finding_id'], result['pending_finding_ids'][0])
        before = self.ledger()
        self.assertEqual(host.invoke(self.case,args), saved)
        self.assertEqual(self.ledger(), before)

    def test_investigation_and_refutation_require_real_citations(self):
        self.prepare()
        for disposition in ('investigated','refuted'):
            item = {**self.response_item, 'disposition':disposition, 'citation_refs':[self.cite]}
            self.write(item)
            q = next(q for q in host.current(self.case)['questions'] if q['id']=='Q-'+self.note['note_id'])
            citation = q['review_responses'][0]['citations'][0]
            self.assertEqual(citation['literal'], 'A benign scheduled backup is configured; execution is not observed.')
            self.assertFalse(q['review_responses'][0]['semantic_approval'])
            self.assertFalse(review.review_state(self.resolved)['requires_followup'])

    def test_invalid_identity_and_citation_fail_without_note_change(self):
        self.prepare()
        bad = [dict(self.response_item, review_id='foreign-review'),
               dict(self.response_item, finding_id='invented-finding'),
               dict(self.response_item, limitation=''),
               dict(self.response_item, disposition='refuted'),
               dict(self.response_item, disposition='investigated', citation_refs=['C2:invalid'])]
        for item in bad:
            with self.subTest(item=item):
                before = self.ledger()
                with self.assertRaises(ValueError): self.write(item)
                self.assertEqual(self.ledger(), before)

    def test_substantive_answer_edit_preserves_investigation_response(self):
        self.prepare(); self.write(self.response_item)
        self.write(answer='새 답은 이전 범위를 넘어설 수 있으므로 다시 판단한다.', reason='답변 정정')
        state=review.review_state(self.resolved)
        self.assertTrue(state['requires_followup'])
        self.assertEqual(state['pending_findings'],[])
        self.assertTrue(state['judgment_review_questions'])
        self.assertEqual(state['dispositions'][0]['reason'],self.response_item['reason'])

    def test_foreign_conversation_and_stale_revision_do_not_dispose(self):
        self.prepare(); _, args = self.write(self.response_item)
        for changes in ({'_session_id':'other-conversation'}, {}):
            bad = deepcopy(args); bad.update(changes); bad['payload']['operation_id']=uuid.uuid4().hex
            before = self.ledger()
            with self.assertRaises(ValueError): host.invoke(self.case,bad)
            self.assertEqual(self.ledger(),before)

    def test_same_answer_with_replaced_verdict_grounding_preserves_separate_response(self):
        self.prepare(); self.write(self.response_item)
        q = next(q for q in host.current(self.case)['questions'] if q['id']=='Q-'+self.note['note_id'])
        self.write(answer=q['answer'], reason='인용 근거를 정정한다.', judgment={
            'citation_refs':[self.cite], 'assertion_kind':'interpretation',
            'reasoning_summary':'실행 관측과 설정 내용을 구별하여 한정한다.'})
        self.assertFalse(review.review_state(self.resolved)['requires_followup'])

    def test_changed_definition_keeps_history_pending_until_explicit_revalidation(self):
        self.prepare(); self.write(self.response_item)
        self.write(scope='A different explicitly selected time window')
        state=review.review_state(self.resolved)
        self.assertTrue(state['requires_followup'])
        self.assertEqual(state['dispositions'],[])
        self.assertIn('earlier question',state['invalid_dispositions'][0]['reason'])
        q=next(q for q in host.current(self.case)['questions'] if q['id']=='Q-'+self.note['note_id'])
        self.assertEqual(q['review_responses'][0]['reason'],self.response_item['reason'])
        self.write(self.response_item)
        self.assertFalse(review.review_state(self.resolved)['requires_followup'])

    def test_response_revision_does_not_change_substantive_review_identity(self):
        self.prepare()
        before=review.freshness(self.resolved)['evaluation_fingerprint']
        self.write(self.response_item)
        after=review.freshness(self.resolved)['evaluation_fingerprint']
        self.assertEqual(before,after)
        self.write(answer='New substantive answer with a different finding.',reason='New judgment')
        self.assertNotEqual(after,review.freshness(self.resolved)['evaluation_fingerprint'])
        self.assertEqual(len(review.review_state(self.resolved)['dispositions']),1)

    def test_unrelated_question_edit_does_not_reopen_answered_finding(self):
        self.prepare(); self.write(self.response_item)
        args={'action':'question','payload':{'operation_id':uuid.uuid4().hex,
            'question':'A separate question','scope':'Independent scope','answer':'Provisional separate answer'},
            '_goal_id':self.resolved['goal_id'],'_session_id':self.sid,
            '_session_lineage':[self.sid],'_question_contract':'v2'}
        created=host.invoke(self.case,args)
        q=next(q for q in host.current(self.case)['questions'] if q['question']=='A separate question')
        args['payload']={'operation_id':uuid.uuid4().hex,'question_id':q['id'],
            'expected_revision':q['revision'],'scope':'Changed separate scope','answer':'Changed separate answer'}
        host.invoke(self.case,args)
        projected=review.review_state(self.resolved)
        self.assertEqual(projected['pending_findings'],[])
        self.assertEqual(len(projected['dispositions']),1)

    def test_legacy_response_original_definition_is_not_rebound_to_changed_scope(self):
        self.prepare(); self.write(self.response_item)
        with self.case.connect() as db:
            raw=db.execute("SELECT data FROM events WHERE kind='note' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        original=json.loads(raw)
        original['investigation']['review_responses'][0].pop('question_definition_version')
        self.case.record('note',original,self.sid)
        changed=deepcopy(original);changed['revision']+=1;changed['scope']='A changed legacy scope'
        from forsic_plugin.report_driven.investigation_state import definition_version
        changed['definition_version']=definition_version(changed)
        self.case.record('note',changed,self.sid)
        projected=review.review_state(self.resolved)
        self.assertTrue(projected['requires_followup'])
        self.assertEqual(projected['dispositions'],[])
        self.assertIn('earlier question',projected['invalid_dispositions'][0]['reason'])

    def test_unknown_legacy_applicability_does_not_halt_unrelated_note_update(self):
        from unittest.mock import patch
        self.prepare();self.write(self.response_item)
        with self.case.connect() as db:
            note=json.loads(db.execute("SELECT data FROM events WHERE kind='note' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        note['investigation']['review_responses'][0].pop('question_definition_version')
        self.case.record('note',note,self.sid)
        with patch('forsic_plugin.report_driven.review_responses.origin_definition',
                   side_effect=ValueError('Original legacy applicability is unavailable')):
            self.write(answer='An independently corrected answer',reason='Keep uncertainty visible')
            projected=review.review_state(self.resolved)
            self.assertTrue(projected['pending_findings'])
            self.assertIn('unavailable',projected['invalid_dispositions'][0]['reason'])
            q=next(q for q in host.current(self.case)['questions'] if q['id']=='Q-'+self.note['note_id'])
            self.assertEqual(q['answer'],'An independently corrected answer')
            self.assertEqual(len(q['review_responses']),1)
            self.write(self.response_item)
            self.assertEqual(len(review.review_state(self.resolved)['dispositions']),1)

    def test_changed_followup_source_reopens_disposition_without_changing_review_history(self):
        self.prepare()
        item={**self.response_item,'disposition':'investigated','citation_refs':[self.cite]}
        self.write(item)
        self.assertFalse(review.review_state(self.resolved)['requires_followup'])
        sid=self.cite.split(':')[1];event=self.case.event(sid)
        event['data']['lines'][0]['text']='Corrected retained record; the prior selection is stale.'
        with self.case.connect() as db,db:
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(event['data']),sid))
        state=review.review_state(self.resolved)
        self.assertTrue(state['requires_followup']);self.assertEqual(state['dispositions'],[])
        self.assertEqual(len(state['findings']),1)
        self.assertIn('Stale',state['invalid_dispositions'][0]['reason'])

    def test_foreign_followup_evidence_is_rejected_even_in_the_same_case(self):
        self.prepare()
        data={'tool':'forsic_read','path':'foreign.log','lines':[{'line':1,'text':'Foreign conversation observation'}]}
        data['started_id']=self.case.record('tool_start',{'tool':'forsic_read','arguments':{'path':'foreign.log'}},'foreign')
        sid=self.case.record('tool_result',data,'foreign')
        cite=read_refs(self.case,{**data,'evidence_id':sid})['presented_spans'][0]['citation_ref']
        before=self.ledger()
        with self.assertRaisesRegex(ValueError,'another conversation'):
            self.write({**self.response_item,'disposition':'refuted','citation_refs':[cite]})
        self.assertEqual(self.ledger(),before)
        self.assertTrue(review.review_state(self.resolved)['requires_followup'])

    def test_actual_host_final_review_rerender_and_delivery_without_duplicate_call(self):
        self.prepare(); self.write(self.response_item)
        clock = {'current_datetime': '2040-01-01T00:00:00+00:00', 'timezone': 'UTC'}
        self.stack.enter_context(patch.object(review, 'current_context', side_effect=lambda: deepcopy(clock)))
        def no_findings(**kwargs):
            self.calls.append(deepcopy(kwargs))
            return iter([SimpleNamespace(choices=[SimpleNamespace(index=0,
                delta=SimpleNamespace(content=json.dumps({'findings':[]})), finish_reason='stop')],usage=None)])
        self.fake.side_effect = no_findings
        base = {'_goal_id':self.resolved['goal_id'], '_session_id':self.sid,
                '_independent_review_options':{'enabled':True,'session_id':self.sid,
                    'intake_root':str(self.intake),'native_db':str(self.native)}}
        draft = host.invoke(self.case, {**base,'action':'render','allow_partial':True})
        final = host.invoke(self.case,{**base,'action':'finalize','bundle_id':draft['bundle_id'],
            'snapshot_id':host.current(self.case)['meta']['snapshot_id']})
        self.assertFalse(final['finalized'])
        self.assertEqual(final['delivery_status'],'independent_review_rerender_required')
        clock['current_datetime'] = '2040-01-01T00:00:01+00:00'
        updated = host.invoke(self.case, {**base,'action':'render','allow_partial':True})
        for f in updated['files']:
            if f.endswith('.html'): self.assertIn(self.response_item['limitation'], Path(f).read_text())
        calls = len(self.calls)
        final = host.invoke(self.case,{**base,'action':'finalize','bundle_id':updated['bundle_id'],
            'snapshot_id':host.current(self.case)['meta']['snapshot_id']})
        self.assertTrue(final['finalized'])
        self.assertEqual(len(self.calls),calls)
        self.assertEqual(json.loads(self.calls[-1]['messages'][1]['content'])['runtime_clock']['current_datetime'],
                         '2040-01-01T00:00:00+00:00')


if __name__ == '__main__': unittest.main()
