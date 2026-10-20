"""Rejected native question drafts repair without a second store or model calls."""
from copy import deepcopy
import json
import unittest

from forsic_plugin.context_results import summarize
from forsic_plugin.report_driven import host
import test_question_v2 as fixtures


class QuestionRecoveryTests(unittest.TestCase):
    setUp=fixtures.QuestionV2Tests.setUp
    call=fixtures.QuestionV2Tests.call
    judgment=fixtures.QuestionV2Tests.judgment
    save=fixtures.QuestionV2Tests.save
    close_payload=fixtures.QuestionV2Tests.close_payload

    def draft(self, **changes):
        p=dict(operation_id='new-closed-draft',question='Did the requested job finish?',
            scope='Acquired synthetic records',answer='The request is recorded; completion remains unknown.',
            reason='Retain the acquisition limit',priority='material',work_state='scoped_closed',
            judgment=self.judgment(),critical_gaps=['Completion receipt unavailable'],
            reopen_conditions=['A completion receipt becomes available'])
        p.update(changes)
        return p

    def assert_no_write(self,before):
        after=host.current(self.case)
        for field in ('questions','gaps','claims','observations','missions','tests','assessments'):
            self.assertEqual(before[field],after[field],field)
        with self.case.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='mutation_receipt'").fetchone()[0],
                             self.receipt_count)

    def checkpoint(self):
        with self.case.connect() as db:
            self.receipt_count=db.execute("SELECT count(*) FROM events WHERE kind='mutation_receipt'").fetchone()[0]
        return host.current(self.case)

    def test_failed_new_draft_repairs_once_with_original_answer_and_exact_refs(self):
        p=self.draft();original=deepcopy(p);before=self.checkpoint()
        out=self.call('forsic_question',p)
        self.assertEqual(p,original)
        self.assertEqual(out['error_code'],'invalid_question_fields')
        self.assertEqual(out['invalid_fields'][0]['missing_gap_texts'],p['critical_gaps'])
        recovery=out['recovery']
        self.assertEqual(recovery['scope'],'new')
        self.assertNotIn('read_call',recovery)
        self.assertNotIn('question_id',recovery)
        self.assertNotIn('submitted_arguments',recovery)
        self.assertEqual(recovery['gap_choices'],[{'gap_text':p['critical_gaps'][0]}])
        self.assert_no_write(before)
        # The existing native start record already retains the complete draft.
        start=self.case.event(out['started_id'])['data']['arguments']['payload']
        for key,value in original.items():self.assertEqual(start[key],value)
        p['gap_dispositions']=[dict(**recovery['gap_choices'][0],disposition='assessed_unresolved',
            reason='The retained request cannot establish completion',reopen_conditions=['New receipt'])]
        saved=self.call('forsic_question',p)
        self.assertNotIn('error',saved,saved)
        self.assertEqual(saved['question'],self.call('forsic_question',p)['question'])
        after=host.current(self.case)
        self.assertEqual(len(after['questions']),len(before['questions'])+1)
        q=host.find(after,'questions',saved['question']['question_id'])
        self.assertEqual((q['revision'],q['work_state'],q['answer']),(1,'scoped_closed',original['answer']))
        self.assertEqual(len(q['claim_refs']),1)
        with self.case.connect() as db:
            notes=db.execute("SELECT count(*) FROM events WHERE kind='note' AND json_extract(data,'$.note_id')=?",
                             (q['id'].removeprefix('Q-'),)).fetchone()[0]
            receipts=db.execute("SELECT count(*) FROM events WHERE kind='mutation_receipt'").fetchone()[0]
        self.assertEqual((notes,receipts),(1,self.receipt_count+1))
        claim=host.find(after,'claims',q['claim_refs'][0]['id'])
        observation=host.find(after,'observations',claim['source_refs'][0]['id'])
        self.assertEqual(observation['source_ref']['id'],original['judgment']['citation_refs'][0].split(':')[1])
        self.assertEqual(after['missions'],before['missions'])

    def test_shape_identity_selector_and_coverage_errors_are_returned_together(self):
        p=self.draft(expected_revision=0,priority='unassessed',gaps=['Independent process linkage missing'],
            gap_dispositions=[dict(gap_id='invented',gap_text='Completion receipt unavailable',
                disposition='assessed_unresolved',reason='No receipt',reopen_conditions=[])])
        before=self.checkpoint();out=self.call('forsic_question',p)
        problems=out['invalid_fields']
        self.assertTrue(any(x.get('matched_branches')==2 for x in problems))
        self.assertTrue(any(x.get('minimum')==1 for x in problems))
        self.assertTrue(any('New questions omit' in x.get('error','') for x in problems))
        self.assertTrue(any(x['path']=='payload.priority' for x in problems))
        self.assertTrue(any(x['path']=='payload.gap_dispositions[0].gap_id' for x in problems))
        self.assertTrue(any(x.get('missing_gap_texts')==p['gaps'] for x in problems))
        self.assertEqual(out['recovery']['omit_fields'],['question_id','expected_revision'])
        self.assert_no_write(before)

    def test_stale_and_cross_owner_updates_keep_current_identity_and_reject(self):
        q=self.save(judgment=self.judgment(),reason='Retain the source',gaps=['Unknown completion'])
        p=dict(operation_id='stale-foreign',question_id=q['id'],expected_revision=q['revision']+2,
            work_state='scoped_closed',reason='Retain uncertainty',reopen_conditions=['New receipt'])
        before=self.checkpoint()
        out=json.loads(self.case.invoke('forsic_reporting',{'action':'question','payload':p},'foreign',
            request_context={'goal_id':'foreign','conversation_id':'foreign','question_contract_version':'v2'}))
        self.assertEqual(out['recovery']['question_id'],q['id'])
        self.assertEqual(out['recovery']['revision'],q['revision'])
        self.assertFalse(out['recovery']['editable_in_current_conversation'])
        for message in ('revision conflict','another native conversation','another native goal'):
            self.assertTrue(any(message in x.get('error','') for x in out['invalid_fields']))
        self.assert_no_write(before)
        p.update(operation_id='unfinished',expected_revision=q['revision'])
        out=self.call('forsic_question',p)
        self.assertIn('Account for remaining',out['error'])
        self.assert_no_write(before)

    def test_invalid_gap_and_exact_citation_still_reject_without_partial_records(self):
        p=self.draft(gap_dispositions=[dict(gap_text='Completion receipt unavailable',disposition='assessed_unresolved',
            reason='No receipt',reopen_conditions=['New receipt'])])
        before=self.checkpoint()
        bad=deepcopy(p);bad['gap_dispositions'][0]['gap_text']='Guessed gap'
        self.assertIn('error',self.call('forsic_question',bad));self.assert_no_write(before)
        bad=deepcopy(p);bad['judgment']['citation_refs'][0]+='invalid'
        self.assertIn('error',self.call('forsic_question',bad));self.assert_no_write(before)

    def test_pruning_preserves_structured_repair_and_never_truncates_selectors(self):
        p=self.draft();out=self.call('forsic_question',p)
        compact=json.loads(summarize('forsic_question',p,json.dumps(out)))
        self.assertEqual(compact['invalid_fields'],out['invalid_fields'])
        self.assertEqual(compact['recovery'],out['recovery'])
        self.assertEqual(compact['retained_result_id'],out['evidence_id'])
        p=self.draft(operation_id='long-gap',critical_gaps=['Exact gap '+('X'*5000)])
        out=self.call('forsic_question',p)
        raw=summarize('forsic_question',p,json.dumps(out));self.assertLessEqual(len(raw),2000)
        compact=json.loads(raw)
        self.assertEqual(compact['recovery']['omitted_gap_choices'],1)
        self.assertEqual(compact['omitted_invalid_fields'],1)
        self.assertNotIn('lookup',compact)  # Mutation results are not evidence-body sources.


if __name__=='__main__':unittest.main()
