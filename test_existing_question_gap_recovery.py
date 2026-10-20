"""Synthetic registered-tool recovery for rewritten unsaved closure obligations."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.investigation_state import note_gap_records
import test_question_v2 as fixtures


class ExistingQuestionGapRecoveryTests(unittest.TestCase):
    setUp=fixtures.QuestionV2Tests.setUp
    call=fixtures.QuestionV2Tests.call
    judgment=fixtures.QuestionV2Tests.judgment
    save=fixtures.QuestionV2Tests.save

    def draft(self):
        self.saved_texts=['Saved obligation '+str(i) for i in range(6)]
        q=self.save(gaps=self.saved_texts,judgment=self.judgment(),reason='Retain the request record')
        return q,dict(operation_id='correct-current-answer',question_id=q['id'],expected_revision=q['revision'],
            answer='A request exists; completion and authorization remain uncertain.',
            reason='Preserve remaining uncertainty after the source review',judgment=self.judgment(),
            work_state='scoped_closed',reopen_conditions=['New independent receipt'],
            critical_gaps=['Proposed obligation '+str(i) for i in range(3)])

    def records(self):
        with self.case.connect() as db:
            return db.execute("SELECT kind,data FROM events WHERE kind IN ('note','report_change','mutation_receipt') ORDER BY rowid").fetchall()

    def disposition(self, selector):
        return dict(**selector,disposition='assessed_unresolved',reason='The available record leaves this obligation uncertain',
                    reopen_conditions=['New independent receipt'])

    def repair(self,p,recovery):
        p['gap_dispositions']=[self.disposition({'gap_id':g['gap_id']}) for g in recovery['committed_gaps']]
        p['gap_dispositions'] += [self.disposition(g) for g in recovery['proposed_gaps']]
        return p

    def test_rewritten_proposal_reports_all_errors_and_repairs_in_one_atomic_save(self):
        q,p=self.draft();before=host.current(self.case);records=self.records()
        first=self.call('forsic_question',p)
        self.assertEqual(first['error_code'],'invalid_question_fields')
        recovery=first['recovery']
        self.assertEqual(len(recovery['committed_gaps']),6)
        self.assertEqual(recovery['proposed_gaps'],[{'gap_text':t} for t in p['critical_gaps']])
        self.assertTrue(all(set(g)=={'gap_text'} for g in recovery['proposed_gaps']))
        missing=next(e for e in first['invalid_fields'] if e.get('missing_gap_count'))
        self.assertEqual((missing['missing_gap_count'],len(missing['missing_gap_ids'])),(9,6))
        self.assertEqual(self.records(),records)
        # Reproduce a client retaining old proposal IDs while rewriting its text.
        transient=note_gap_records({'revision':q['revision'],'critical_gaps':p['critical_gaps'],
                                   'declared_gaps_only':True},q,[])
        p['critical_gaps']=['Rewritten obligation '+str(i) for i in range(5)]
        p['gap_dispositions']=[self.disposition({'gap_id':g['gap_id']}) for g in recovery['committed_gaps']]
        p['gap_dispositions'] += [self.disposition({'gap_id':g['id']}) for g in transient]
        p['gap_dispositions'][1]['reopen_conditions']=[]
        p['gap_dispositions'][2]['reopen_conditions']=[]
        rejected=self.call('forsic_question',p)
        fields=rejected['invalid_fields']
        self.assertEqual(len([e for e in fields if 'Unknown gaps ID' in e.get('error','')]),3)
        self.assertTrue(any(e['path']=='payload.gap_dispositions[1].reopen_conditions' for e in fields))
        self.assertTrue(any(e['path']=='payload.gap_dispositions[2].reopen_conditions' for e in fields))
        missing=next(e for e in fields if e.get('missing_gap_count'))
        self.assertEqual(missing['missing_gap_count'],5)
        self.assertEqual(missing['missing_gap_ids'],[])
        self.assertEqual(missing['missing_gap_texts'],p['critical_gaps'])
        self.assertNotEqual(recovery['draft_fingerprint'],rejected['recovery']['draft_fingerprint'])
        self.assertEqual(self.records(),records)
        self.assertEqual(host.current(self.case)['questions'],before['questions'])
        self.repair(p,rejected['recovery']);original=deepcopy(p)
        saved=self.call('forsic_question',p)
        self.assertNotIn('error',saved,saved)
        self.assertEqual(p,original)
        after=host.current(self.case);now=host.find(after,'questions',q['id'])
        self.assertEqual((now['revision'],now['answer'],now['work_state']),
                         (q['revision']+1,p['answer'],'scoped_closed'))
        gaps=[g for g in after['gaps'] if g['question_ref']['id']==q['id']]
        self.assertEqual(len(gaps),11)
        self.assertTrue(all(g['disposition']=='assessed_unresolved' for g in gaps))
        self.assertFalse({g['id'] for g in transient}&{g['id'] for g in gaps})
        after_records=self.records()
        for kind in ('note','mutation_receipt'):
            self.assertEqual(sum(row['kind']==kind for row in after_records),
                             sum(row['kind']==kind for row in records)+1)
        self.assertEqual(saved['question'],self.call('forsic_question',p)['question'])
        self.assertEqual(self.records(),after_records)
        changed=deepcopy(p);changed['answer']='A different committed payload'
        self.assertIn('operation_id already belongs',self.call('forsic_question',changed)['error'])
        self.assertEqual(self.records(),after_records)
        self.assertEqual(after['missions'],before['missions'])

    def test_stale_identity_and_invalid_exact_citation_preserve_all_records(self):
        q,p=self.draft();out=self.call('forsic_question',p);self.repair(p,out['recovery'])
        before=self.records();p['expected_revision']+=1
        bad=self.call('forsic_question',p)
        self.assertTrue(any('revision conflict' in e.get('error','') for e in bad['invalid_fields']))
        self.assertEqual(self.records(),before)
        p['expected_revision']=q['revision'];p['judgment']['citation_refs'][0]+='invalid'
        self.assertIn('error',self.call('forsic_question',p))
        self.assertEqual(self.records(),before)
        foreign=json.loads(self.case.invoke('forsic_reporting',{'action':'question','payload':p},'foreign',
            request_context={'goal_id':'foreign','conversation_id':'foreign','question_contract_version':'v2'}))
        self.assertFalse(foreign['recovery']['editable_in_current_conversation'])
        self.assertTrue(any('another native conversation' in e.get('error','') for e in foreign['invalid_fields']))
        self.assertEqual(self.records(),before)

    def test_commit_cas_rejects_revision_changed_after_draft_projection(self):
        q,p=self.draft();out=self.call('forsic_question',p);self.repair(p,out['recovery'])
        write=host.write;intervened=False
        def competing_write(*args,**kwargs):
            nonlocal intervened
            if not intervened:
                intervened=True
                changed=self.call('forsic_question',dict(operation_id='concurrent-edit',question_id=q['id'],
                    expected_revision=q['revision'],next_checks=['Review a new independent receipt']))
                self.assertNotIn('error',changed,changed)
            return write(*args,**kwargs)
        with patch.object(host,'write',side_effect=competing_write):
            rejected=self.call('forsic_question',p)
        self.assertIn('Question changed',rejected['error'])
        state=host.current(self.case);now=host.find(state,'questions',q['id'])
        self.assertEqual(now['revision'],q['revision']+1)
        self.assertEqual(now['answer'],q['answer'])
        self.assertEqual(len([g for g in state['gaps'] if g['question_ref']['id']==q['id']]),6)
        with self.case.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='mutation_receipt' AND json_extract(data,'$.operation_id')=?",(p['operation_id'],)).fetchone()[0],0)

    def test_malformed_gap_patch_and_duplicate_selectors_never_write(self):
        q,p=self.draft();before=self.records();p['critical_gaps']={'invalid':'shape'}
        self.assertIn('error',self.call('forsic_question',p));self.assertEqual(self.records(),before)
        p['critical_gaps']=[]
        first=self.call('forsic_question',p);self.repair(p,first['recovery'])
        p['gap_dispositions'].append(deepcopy(p['gap_dispositions'][0]))
        rejected=self.call('forsic_question',p)
        self.assertTrue(any('Duplicate gap disposition' in e.get('error','') for e in rejected['invalid_fields']))
        self.assertEqual(self.records(),before)

    def test_two_hundred_unicode_gaps_bound_first_rejection_and_keep_exact_query_and_full_commit(self):
        texts=[str(i)+' 확인하지 못한 범위 '+('한글🙂'*45) for i in range(200)]
        q=self.save(gaps=texts,judgment=self.judgment(),reason='Preserve the selected source')
        before=host.current(self.case);records=self.records()
        committed=[g for g in before['gaps'] if g['question_ref']['id']==q['id']]
        proposed=[str(i)+' 새로 제안한 범위 '+('자료🙂'*45) for i in range(200)]
        p=dict(operation_id='large-unicode-draft',question_id=q['id'],expected_revision=q['revision'],
            work_state='scoped_closed',reason='Available evidence leaves these obligations uncertain',
            reopen_conditions=['An independent record arrives'],critical_gaps=proposed,
            gap_dispositions=[self.disposition({'gap_id':'G-not-committed'}),
                              {**self.disposition({'gap_id':committed[0]['id']}),'reopen_conditions':[]}])
        rejected=self.call('forsic_question',p)
        self.assertEqual(rejected['error_code'],'invalid_question_fields')
        self.assertLessEqual(len(json.dumps(rejected,ensure_ascii=False).encode('utf-8')),12288)
        recovery=rejected['recovery']
        self.assertEqual((recovery['question_id'],recovery['revision']),(q['id'],q['revision']))
        self.assertEqual(len(recovery['draft_fingerprint']),64)
        self.assertTrue(any('Unknown gaps ID' in e.get('error','') for e in rejected['invalid_fields']))
        self.assertTrue(any(e['path']=='payload.gap_dispositions[1].reopen_conditions' for e in rejected['invalid_fields']))
        for key,total in (('committed_gaps',200),('proposed_gaps',200)):
            self.assertEqual(len(recovery[key])+recovery.get('omitted_'+key,0),total)
        self.assertTrue(recovery['omitted_committed_gaps']);self.assertTrue(recovery['omitted_proposed_gaps'])
        original={g['id']:g['original_obligation'] for g in committed}
        for item in recovery['committed_gaps']:self.assertEqual(item['gap_text'],original[item['gap_id']])
        self.assertTrue(all(item['gap_text'] in proposed for item in recovery['proposed_gaps']))
        self.assertEqual(self.records(),records)
        # The preserved call is an actual registered reader, not a proposed ID.
        call=recovery['read_call'];page=self.call(call['tool'],call['arguments'])
        self.assertNotIn('error',page,page)
        revision=self.call(call['tool'],{**call['arguments'],'snapshot_id':page['snapshot_id'],
            'pointer':page['pointer']+'/revision'})
        self.assertEqual(revision['value'],q['revision'])
        lookup=page['gap_lookup'];gaps=self.call(lookup['tool'],lookup['arguments'])
        self.assertEqual(gaps['gap_scope']['selected_count'],200)
        next_call=gaps['next_call'];self.assertIsNotNone(next_call)
        following=self.call(next_call['tool'],next_call['arguments'])
        self.assertNotIn('error',following,following)
        self.assertEqual(following['offset'],gaps['next_offset'])
        # Bounded presentation does not weaken validation of omitted obligations.
        self.repair(p,recovery)
        incomplete=self.call('forsic_question',p)
        self.assertIn('error',incomplete);self.assertEqual(self.records(),records)
        p['gap_dispositions']=[self.disposition({'gap_id':g['id']}) for g in committed]
        p['gap_dispositions'] += [self.disposition({'gap_text':text}) for text in proposed]
        saved=self.call('forsic_question',p);self.assertNotIn('error',saved,saved)
        after=host.current(self.case);now=host.find(after,'questions',q['id'])
        self.assertEqual(now['revision'],q['revision']+1)
        final=[g for g in after['gaps'] if g['question_ref']['id']==q['id']]
        self.assertEqual(len(final),400)
        self.assertTrue(all(g['disposition']=='assessed_unresolved' for g in final))

    def test_oversized_validation_items_are_omitted_whole_with_aggregate_counts(self):
        from forsic_plugin.report_driven.investigation_state import QuestionInputError
        problems=[{'path':'payload.'+('잘못된필드🙂'*500)+str(i),'expected_type':'string'} for i in range(200)]
        recovery={'scope':'update','question_id':'Q-synthetic','revision':7,'draft_fingerprint':'f'*64,
            'read_call':{'tool':'forsic_reporting','arguments':{'action':'state','question_id':'Q-synthetic'}}}
        for legacy_preview in (False,True):
            error=QuestionInputError(problems,recovery,include_recovery=legacy_preview)
            view={'error':str(error),**error.details}
            self.assertLessEqual(len(json.dumps(view,ensure_ascii=False).encode('utf-8')),8192)
            self.assertEqual(error.details['invalid_field_count'],200)
            self.assertEqual(len(error.details['invalid_fields'])+error.details['omitted_invalid_fields'],200)
            self.assertTrue(all(item in problems for item in error.details['invalid_fields']))
            self.assertEqual(error.details['recovery'],recovery)


if __name__=='__main__':unittest.main()
