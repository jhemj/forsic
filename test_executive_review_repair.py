"""Offline malformed reviewer -> bounded same-round repair -> completion contracts."""
from copy import deepcopy
import json
import unittest
from types import SimpleNamespace

from forsic_plugin import independent_review as review
from forsic_plugin.independent_review_repair import ordinary_receipts
import test_executive_followup as fixtures


class ExecutiveRepairTests(unittest.TestCase):
    setUp=fixtures.ExecutiveFollowupTests.setUp
    bind=fixtures.ExecutiveFollowupTests.bind
    session=fixtures.ExecutiveFollowupTests.session
    save_note=fixtures.ExecutiveFollowupTests.save_note
    setup_review=fixtures.ExecutiveFollowupTests.setup_review
    add_sources=fixtures.ExecutiveFollowupTests.add_sources
    kwargs=fixtures.ExecutiveFollowupTests.kwargs
    request_paths=fixtures.ExecutiveFollowupTests.request_paths
    resolved=fixtures.ExecutiveFollowupTests.resolved
    prepare=fixtures.ExecutiveFollowupTests.prepare
    response=fixtures.ExecutiveFollowupTests.response
    call=fixtures.ExecutiveFollowupTests.call
    answer=fixtures.ExecutiveFollowupTests.answer
    def repair_model(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        body=json.loads(kwargs['messages'][1]['content'])
        context=body['executive_followup']
        count=context['questions_required']
        anchor=body['sources'][0]['fields'][0]['review_ref']
        repair=bool(context.get('validation_repair'))
        findings=[{'review_ref':anchor,'question':f"Round {context['round']} {'repair' if repair else 'initial'} {i}?",
                   'why':'Compare the retained observation with its interpretation.',
                   'next_check':'Read the retained synthetic record.'} for i in range(count)]
        if context['round']==1 and (not repair or self.repair_fails):
            findings[-1]['review_ref']='R1:invalid-whole-source'
        raw=json.dumps({'findings':findings})
        return iter([SimpleNamespace(choices=[SimpleNamespace(index=0,
            delta=SimpleNamespace(content=raw),finish_reason='stop')],usage=None)])

    def begin_partial(self):
        self.prepare();self.repair_fails=False;self.fake.side_effect=self.repair_model
        self.call()
        state=review.review_state(self.resolved())
        self.assertEqual(len(state['pending_findings']),4)
        self.assertEqual(state['executive_followup']['rounds'][0]['question_count'],4)
        self.assertFalse(state['executive_followup']['rounds'][0]['complete'])
        self.assertEqual(state['followup_action'],'respond')
        self.assertFalse(state['final_review_current'])
        self.answer(state['pending_findings'],limited=True)
        return self.request_paths()[0]

    def test_partial_findings_one_item_repair_then_exact_three_rounds(self):
        original=self.begin_partial();before=original.read_bytes()
        response_path=original.with_name(original.name.replace('-request.json','-response.json'))
        response_before=response_path.read_bytes()
        accepted_ids={f['finding_id'] for f in review.review_state(self.resolved())['findings']}
        pending=review.advisory_at_boundary(self.resolved())
        self.assertEqual(pending['followup_action'],'repair_review')
        self.assertTrue(pending['followup_actionable'])
        self.assertEqual(len(self.calls),1)
        self.call()
        state=review.review_state(self.resolved())
        self.assertEqual(len(self.calls),2)
        repair_body=json.loads(self.calls[-1]['messages'][1]['content'])
        self.assertEqual(repair_body['executive_followup']['questions_required'],1)
        self.assertEqual(len(repair_body['executive_followup']['validation_repair']['accepted_questions']),4)
        self.assertEqual(state['executive_followup']['rounds'][0]['question_count'],5)
        self.assertEqual(state['executive_followup']['rounds'][0]['answered'],4)
        self.assertFalse(state['executive_followup']['rounds'][0]['complete'])
        self.assertEqual(len(state['pending_findings']),1)
        self.assertTrue(accepted_ids <= {f['finding_id'] for f in state['findings']})
        self.call();self.assertEqual(len(self.calls),2)  # A reread cannot repeat repair.
        self.answer(state['pending_findings'],limited=True)
        for number in (2,3):
            self.call();state=review.review_state(self.resolved())
            self.assertEqual(len(state['pending_findings']),5)
            self.assertEqual(state['executive_followup']['rounds'][-1]['round'],number)
            self.answer(state['pending_findings'],limited=True)
        complete=self.call();state=review.review_state(self.resolved())
        self.assertEqual(len(self.calls),4)
        self.assertTrue(state['executive_followup']['complete'])
        self.assertTrue(state['final_review_current'])
        self.assertFalse(complete['requires_followup'])
        self.assertEqual(len(state['dispositions']),15)
        self.assertEqual(len(ordinary_receipts(review._receipts(self.resolved()))),1)
        self.assertEqual(original.read_bytes(),before)
        self.assertEqual(response_path.read_bytes(),response_before)

    def test_failed_repair_is_bounded_and_never_completes_round(self):
        self.begin_partial();self.repair_fails=True;self.call()
        state=review.review_state(self.resolved())
        # Failed repair gets an explicit availability disposition, not a fake fifth question.
        self.answer(state['pending_findings'],limited=True)
        for _ in range(3):self.call()
        state=review.review_state(self.resolved())
        self.assertEqual(len(self.calls),2)
        self.assertEqual(state['executive_followup']['rounds'][0]['question_count'],4)
        self.assertFalse(state['executive_followup']['complete'])
        self.assertFalse(state['followup_actionable'])
        self.assertNotIn('review_repair',state)

    def test_repaired_dialogue_current_report_review_and_actual_final_selection(self):
        self.test_partial_findings_one_item_repair_then_exact_three_rounds()
        fixtures.ExecutiveFollowupTests.compose_report_and_finalize(self)
        self.assertEqual(len(self.calls),5)  # Three rounds, one repair, one current-report review.
        with self.case.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='report_finalized'").fetchone()[0],1)

    def test_repair_respects_existing_budget_and_user_stop(self):
        self.begin_partial()
        self.config['forsic']={'independent_review':{'call_budgets':{review._goal_key(self.resolved()):0}}}
        self.call();self.assertEqual(len(self.calls),1)
        self.assertFalse(review.review_state(self.resolved())['followup_actionable'])
        self.config['forsic']['independent_review']['call_budgets'][review._goal_key(self.resolved())]=1
        self.goal.status='paused';self.session(self.sid,goal=self.goal)
        self.call();self.assertEqual(len(self.calls),1)
        self.goal.status='active';self.session(self.sid,goal=self.goal)
        self.call();self.assertEqual(len(self.calls),2)

    def test_legacy_invalid_receipt_preserves_four_valid_items_without_rewriting(self):
        original=self.begin_partial()
        path=original.with_name(original.name.replace('-request.json','-response.json'))
        old=json.loads(path.read_text());old['findings']=[];old['error_type']='ValueError'
        path.write_text(json.dumps(old));before=path.read_bytes()
        state=review.review_state(self.resolved())
        self.assertEqual(state['executive_followup']['rounds'][0]['question_count'],4)
        self.assertEqual(state['followup_action'],'repair_review')
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(len(self.calls),1)

    def test_workflow_revision_reuses_review_basis_but_changed_answer_and_prose_do_not(self):
        self.prepare();resolved=self.resolved()
        question=review._question_records(resolved)[0]
        draft={'schema':'forsic-report-narrative-2','question_ids':[question['id']],
               'goal_id':resolved['goal_id'],'basis_id':'cas-before','draft_revision':1,
               'draft_hash':'prose-one','sections':{'scope':{'text':'An explicitly bounded finding.'}},'review':None}
        self.case.record('report_narrative',draft,self.sid)
        resolved=review.report_review_scope(resolved,[question['id']])
        first=review.freshness(resolved)
        self.save_note(work_state='scoped_closed',priority='contextual',judgment_review_required=False)
        self.case.record('report_narrative',{**draft,'basis_id':'cas-after','draft_revision':2},self.sid)
        scoped=lambda:review.report_review_scope(self.resolved(),[question['id']])
        second=review.freshness(scoped())
        self.assertEqual(first['evaluation_fingerprint'],second['evaluation_fingerprint'])
        self.assertEqual(first['fingerprint'],second['fingerprint'])
        self.assertEqual(first['report_draft_fingerprint'],second['report_draft_fingerprint'])
        self.save_note(answer='A materially changed interpretation.')
        third=review.freshness(scoped())
        self.assertNotEqual(second['evaluation_fingerprint'],third['evaluation_fingerprint'])
        self.assertNotEqual(second['fingerprint'],third['fingerprint'])
        self.case.record('report_narrative',{**draft,'draft_hash':'prose-two',
            'sections':{'scope':{'text':'The main finding changed.'}}},self.sid)
        self.assertNotEqual(third['evaluation_fingerprint'],review.freshness(scoped())['evaluation_fingerprint'])

    def test_draft_semantics_preserve_layout_citations_and_text_not_save_bookkeeping(self):
        draft={'draft_hash':'retained','basis_id':'before','draft_revision':1,'fields':{'timeline':{
            'saved_at':1,'dependencies':{'Q:a':'old-workflow-digest'},'judgment_dependencies':{'Q:a':'old'},
            'entries':[{'node_id':'event-1','cells':['Event meaning'], 'citation_refs':['C2:exact-before'],
                        'basis_keys':['Q:a'],'cell_details':['qualification'],'assessment':{'level':3}}],
            'complete':True}}}
        after=deepcopy(draft);after.update(basis_id='after',draft_revision=2)
        after['fields']['timeline'].update(saved_at=2,dependencies={'Q:a':'new-workflow-digest'},judgment_dependencies={'Q:a':'new'})
        self.assertEqual(review._draft_identity(draft),review._draft_identity(after))
        for key,value in [('cells',['Changed interpretation']),('citation_refs',['C2:exact-after']),
                          ('cell_details',['Different qualification']),('assessment',{'level':5})]:
            changed=deepcopy(after);changed['fields']['timeline']['entries'][0][key]=value
            self.assertNotEqual(review._draft_identity(draft),review._draft_identity(changed))

    def test_review_prompt_keeps_static_sources_before_volatile_context_losslessly(self):
        body={'goal':'synthetic goal','sources':[{'field':'retained source'}],
              'active_task':{'round':1},'runtime_clock':{'current_datetime':'synthetic first'}}
        original=review._prompt_json(body)
        changed=review._prompt_json({**body,'active_task':{'round':2},
                                     'runtime_clock':{'current_datetime':'synthetic second'}})
        self.assertEqual(json.loads(original),body)
        self.assertEqual(len(original),len(review._json(body)))
        self.assertEqual(original.split(',"active_task":')[0],changed.split(',"active_task":')[0])
        self.assertLess(original.index('"sources"'),original.index('"runtime_clock"'))
