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


    def test_all_independent_reference_and_state_errors_precede_atomic_repair(self):
        valid=self.judgment();q=self.save('reference-base',judgment=valid,reason='Compare retained evidence')
        ref=valid['citation_refs'][0]
        bad=ref.rsplit(':',1)[0]+':99'
        payload=dict(operation_id='reference-repair',question_id=q['id'],expected_revision=q['revision']+1,
            answer=q['answer'],reason='Compare retained evidence',claim_action='retain',evidence_ids=['0'*32,'1'*32],
            judgment={'claims':[dict(valid,statement='First observation',citation_refs=[bad],
                counter_citation_refs=[bad],assertion_kind='fact',inference_strength='favored'),
                dict(valid,statement='Second observation',citation_refs=['malformed'])]})
        original=deepcopy(payload);before=self.checkpoint()
        rejected=self.call('forsic_question',payload)
        self.assertEqual(rejected['error_code'],'invalid_question_fields',rejected)
        paths={p['path'] for p in rejected['invalid_fields']}
        self.assertTrue({'payload.evidence_ids[0]','payload.evidence_ids[1]',
            'payload.judgment.claims[0].citation_refs[0]',
            'payload.judgment.claims[0].counter_citation_refs[0]',
            'payload.judgment.claims[1].citation_refs[0]',
            'payload.claim_action','payload.judgment.claims[0].inference_strength',
            'payload.expected_revision'} <= paths, rejected)
        self.assertEqual(payload,original);self.assert_no_write(before)
        for problem in rejected['invalid_fields']:
            if problem['path'].startswith('payload.judgment') and 'recovery' in problem:
                call=problem['recovery']['read_call']
                self.assertNotIn('error',self.call(call['tool'],call['arguments']))
        payload.update(expected_revision=q['revision'],claim_action='replace',evidence_ids=[])
        for claim in payload['judgment']['claims']:
            claim.update(citation_refs=[ref],counter_citation_refs=[],inference_strength='unrated')
        saved=self.call('forsic_question',payload)
        self.assertNotIn('error',saved,saved)
        self.assertEqual(self.call('forsic_question',payload)['question'],saved['question'])

    def test_reference_preflight_is_read_only_and_final_writer_still_rejects(self):
        from unittest.mock import patch
        from forsic_plugin.report_driven.investigation_state import validate_input, QuestionInputError
        from forsic_plugin.report_driven.grounded_judgment import records, CitationSelectionError
        valid=self.judgment();bad=valid['citation_refs'][0].rsplit(':',1)[0]+':99'
        payload=dict(operation_id='read-only-preflight',question='A question',scope='Synthetic records',
            answer='A bounded answer',reason='Compare sources',evidence_ids=['0'*32],
            judgment=dict(valid,citation_refs=[bad]))
        state=host.current(self.case);before=self.case.db.read_bytes()
        with patch.object(self.case,'record',side_effect=AssertionError('Preflight must not record')):
            with self.assertRaises(QuestionInputError):
                validate_input('question',payload,state=state,case=self.case)
        self.assertEqual(before,self.case.db.read_bytes())
        # Bypassing preflight does not bypass the transaction's old final guard.
        with self.assertRaises(CitationSelectionError):
            records(self.case,state,{'note_id':'synthetic','revision':1,'answer':'A','scope':'S',
                'target_proposition':'Q'},payload['judgment'],'v2')
        self.assertEqual(before,self.case.db.read_bytes())

    def test_wrong_case_modified_version_and_shape_errors_never_rebind(self):
        from pathlib import Path
        from test_report_driven import fixture
        from forsic_plugin.report_driven.host import source_record
        foreign,results,_=fixture(str(Path(self.temp.name)/'foreign'))
        source=source_record(foreign.event(results[0]['evidence_id']))
        foreign_ref=f"C2:{source['id']}:{source['version']}:0"
        valid=self.judgment();ref=valid['citation_refs'][0]
        parts=ref.split(':');parts[2]='0'*64;modified=':'.join(parts)
        payload=dict(operation_id='independent-shapes',question='A question',scope='Synthetic records',
            answer='A bounded answer',reason='Compare sources',evidence_ids=[{},'0'*32],
            judgment={'claims':[None,dict(valid,statement='One observation',
                citation_refs=[False,modified],counter_citation_refs=[foreign_ref,'malformed'])]})
        original=deepcopy(payload);before=self.checkpoint()
        rejected=self.call('forsic_question',payload)
        self.assertEqual(rejected['error_code'],'invalid_question_fields',rejected)
        problems={p['path']:p for p in rejected['invalid_fields']}
        self.assertIn('payload.evidence_ids[1]',problems)
        prefix='payload.judgment.claims[1].'
        self.assertIn(prefix+'citation_refs[1]',problems)
        known=problems[prefix+'citation_refs[1]']['recovery']['read_call']['arguments']
        self.assertEqual(known['source_version'],ref.split(':')[2])
        for name in ('counter_citation_refs[0]','counter_citation_refs[1]'):
            self.assertEqual(problems[prefix+name]['recovery']['read_call']['arguments'],{'action':'state'})
        self.assertEqual(payload,original);self.assert_no_write(before)

    def test_large_new_and_update_reference_diagnostics_keep_budget_and_counts(self):
        valid=self.judgment();q=self.save('many-reference-base',judgment=valid,reason='Compare sources')
        for update in (False,True):
            with self.subTest(update=update):
                payload=dict(operation_id='many-refs-'+str(update),question='A question',scope='Synthetic records',
                    answer='A bounded answer',reason='Compare sources',
                    judgment=dict(valid,citation_refs=['invalid-reference-'+str(i) for i in range(200)]))
                if update:payload.update(question_id=q['id'],expected_revision=q['revision'])
                before=self.checkpoint();rejected=self.call('forsic_question',payload)
                self.assertEqual(rejected['error_code'],'invalid_question_fields',rejected)
                self.assertEqual(rejected['invalid_field_count'],200)
                self.assertEqual(len(rejected['invalid_fields'])+rejected['omitted_invalid_fields'],200)
                keys=('error','error_code','write_applied','invalid_fields','recovery','invalid_field_count',
                    'repair_presentation','omitted_invalid_fields')
                projected={key:rejected[key] for key in keys if key in rejected}
                self.assertLessEqual(len(json.dumps(projected,ensure_ascii=False).encode()),8192)
                self.assert_no_write(before)

    def test_aggregate_c1_and_closed_scope_errors_recover_without_adoption(self):
        valid=self.judgment()
        source_id=valid['citation_refs'][0].split(':')[1]
        page=self.call('forsic_reporting',{'action':'source','source_id':source_id,'query':'requested'})
        bad=page['matches'][0]['citation_ref'].rsplit(':',1)[0]+':99'
        payload=self.draft(operation_id='closed-reference-draft',evidence_ids=['0'*32],
            judgment=dict(valid,citation_refs=[bad],counter_citation_refs=['C1:'+'f'*32+':0']))
        before=self.checkpoint();rejected=self.call('forsic_question',payload)
        self.assertEqual(rejected['error_code'],'invalid_question_fields',rejected)
        problems={p['path']:p for p in rejected['invalid_fields']}
        self.assertIn('payload.evidence_ids[0]',problems)
        self.assertIn('payload.gap_dispositions',problems)
        for role in ('citation_refs','counter_citation_refs'):
            self.assertIn('payload.judgment.'+role+'[0]',problems)
        read=problems['payload.judgment.citation_refs[0]']['recovery']['read_call']
        self.assertEqual(read['arguments'],dict(action='source',source_id=source_id,
            source_version=page['source_ref']['version']))
        self.assertNotIn('error',self.call(read['tool'],read['arguments']))
        self.assertEqual(problems['payload.judgment.counter_citation_refs[0]']['recovery']['read_call']['arguments'],{'action':'state'})
        self.assert_no_write(before)

    def test_combined_failure_keeps_exact_failed_output_recovery(self):
        from test_grounded_judgment import GroundedJudgmentTests
        from forsic_plugin.report_driven.host import source_record
        failed=GroundedJudgmentTests.failed_shell(self)
        source=source_record(self.case.event(failed['evidence_id']))
        invented=f"C2:{source['id']}:{source['version']}:0"
        payload=dict(operation_id='failed-output-combined',question='A question',scope='Synthetic output',
            answer='Completion remains unknown',reason='Review returned partial output',evidence_ids=['0'*32],
            judgment=dict(citation_refs=[invented],assertion_kind='interpretation',
                reasoning_summary='Partial output does not establish completion'))
        before=self.checkpoint();rejected=self.call('forsic_question',payload)
        self.assertEqual(rejected['error_code'],'invalid_question_fields',rejected)
        problem=next(p for p in rejected['invalid_fields'] if p['path']=='payload.judgment.citation_refs[0]')
        self.assertEqual(problem['recovery']['read_call'],{'tool':'forsic_reporting','arguments':{
            'action':'source','source_id':source['id'],'source_version':source['version'],
            'pointer':'/stdout','offset':0}})
        self.assertIn('not a replacement citation',problem['recovery']['meaning'])
        self.assertNotIn('observed partial output',json.dumps(rejected))
        self.assert_no_write(before)

    def test_blank_strings_are_validated_unchanged_with_other_reference_errors(self):
        payload=dict(operation_id='blank-reference-draft',question='A question',scope='Synthetic records',
            answer='A bounded answer',reason='Compare sources',evidence_ids=['','   ','0'*32],
            judgment=dict(citation_refs=['','   '],counter_citation_refs=['malformed'],
                assertion_kind='interpretation',reasoning_summary='No selected span is yet valid'))
        original=deepcopy(payload);before=self.checkpoint()
        rejected=self.call('forsic_question',payload)
        self.assertEqual(rejected['error_code'],'invalid_question_fields',rejected)
        paths={p['path'] for p in rejected['invalid_fields']}
        self.assertEqual(paths,{'payload.evidence_ids[0]','payload.evidence_ids[1]',
            'payload.evidence_ids[2]','payload.judgment.citation_refs[0]',
            'payload.judgment.citation_refs[1]','payload.judgment.counter_citation_refs[0]'})
        self.assertEqual(payload,original);self.assert_no_write(before)

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
