"""Synthetic offline selective claim reuse through the atomic v2 writer."""
from copy import deepcopy
import json
import unittest
import test_question_v2 as fixtures
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.current_judgment import current_judgment
from forsic_plugin.report_driven.report_slots import project_report_slots


class ClaimRetentionTests(unittest.TestCase):
    setUp=fixtures.QuestionV2Tests.setUp
    call=fixtures.QuestionV2Tests.call
    save=fixtures.QuestionV2Tests.save
    judgment=fixtures.QuestionV2Tests.judgment
    revise=fixtures.QuestionV2Tests.revise

    def initial(self, op='initial', count=10, **changes):
        template=self.judgment()
        claims=[dict(template, statement='Selected request claim '+str(i)) for i in range(count)]
        claims[0].update(counter_citation_refs=self.judgment('process.log')['citation_refs'], limitations=[])
        return self.save(op, judgment={'claims':claims}, reason='Select independent statements', **changes)

    def retain(self, reference):
        return {'retain_claim_ref':deepcopy(reference)}

    def proposal(self, q, op='correct', **changes):
        p=dict(operation_id=op,question_id=q['id'],expected_revision=q['revision'],
            answer='A request was recorded; linkage remains uncertain.',reason='Correct one statement',
            judgment={'claims':[self.retain(r) for r in q['claim_refs']]})
        p.update(changes)
        return p

    def assertRejected(self, payload):
        before=host.current(self.case)
        with self.case.connect() as db:
            events=db.execute("SELECT id FROM events WHERE kind IN ('note','report_change','mutation_receipt') ORDER BY rowid").fetchall()
        result=self.call('forsic_question',payload)
        self.assertIn('error',result,result)
        self.assertEqual(before,host.current(self.case))
        with self.case.connect() as db:
            self.assertEqual(events,db.execute("SELECT id FROM events WHERE kind IN ('note','report_change','mutation_receipt') ORDER BY rowid").fetchall())
        return result

    def test_one_of_ten_keeps_exact_records_order_masks_ancestry_and_replay(self):
        q=self.initial();before=host.current(self.case)
        old={r['id']:deepcopy(host.find(before,'claims',r['id'])) for r in q['claim_refs']}
        p=self.proposal(q)
        new=self.judgment('process.log',statement='Process creation is recorded; success is unknown.')
        # Reordering is intentional; retained records previously occupied global state positions.
        order=list(reversed(q['claim_refs']))
        p['judgment']['claims']=[self.retain(r) for r in order]
        p['judgment']['claims'][4]=new
        result=self.call('forsic_question',p);self.assertNotIn('error',result,result)
        after=host.current(self.case);host.checked(after);now=host.find(after,'questions',q['id'])
        refs=now['claim_refs'];removed=order[4]['id']
        self.assertEqual(len(refs),10)
        for i, reference in enumerate(refs):
            record=host.find(after,'claims',reference['id'])
            if i==4:self.assertEqual(record['statement'],new['statement'])
            else:self.assertEqual(record,old[order[i]['id']])
        self.assertEqual(host.find(after,'claims',removed)['status'],'superseded')
        self.assertEqual([c['id'] for c in current_judgment(after,now)['judgments']],[r['id'] for r in refs])
        for observation in before['observations']:
            self.assertEqual(host.find(after,'observations',observation['id']),observation)
        self.assertTrue(set(q['evidence_ids']).issubset(now['evidence_ids']))
        replay=self.call('forsic_question',p)
        self.assertEqual(replay['receipt'],result['receipt'])
        self.assertEqual(replay['question'],result['question'])
        self.assertEqual(host.current(self.case),after)
        self.assertRejected(dict(p,answer='Conflicting reuse of operation'))

    def test_retain_only_keeps_all_claims_and_legacy_adapter_cannot_select(self):
        q=self.initial(count=2);before=host.current(self.case)
        p=self.proposal(q)
        result=self.call('forsic_question',p);self.assertNotIn('error',result,result)
        after=host.current(self.case);now=host.find(after,'questions',q['id'])
        self.assertEqual(now['claim_refs'],q['claim_refs'])
        for reference in q['claim_refs']:
            self.assertEqual(host.find(after,'claims',reference['id']),host.find(before,'claims',reference['id']))
        self.assertEqual(now['evidence_ids'],q['evidence_ids'])
        from forsic_plugin.report_driven.grounded_judgment import retained_claims, schema
        from forsic_plugin.report_driven.investigation_state import latest_note, input_problems
        self.assertTrue(input_problems(p['judgment'],schema('v1')))
        with self.case.connect() as db:note=latest_note(db,q['id'][2:])
        legacy=deepcopy(note);legacy.pop('question_contract_version')
        with self.assertRaisesRegex(ValueError,'existing v2 question'):
            retained_claims(self.case,after,legacy,note,p['judgment'],'v2')
        self.assertEqual(after,host.current(self.case))

    def test_legacy_absent_declaration_mask_is_preserved_without_defaulting(self):
        q=self.initial(count=2)
        ident=q['claim_refs'][0]['id']
        with self.case.connect() as db:
            rows=db.execute("SELECT id,data FROM events WHERE kind='report_change'").fetchall()
            for row in rows:
                data=json.loads(row['data']); changed=False
                for claim in data.get('records',{}).get('claims',[]):
                    if claim['id']==ident:
                        claim.pop('declared_analysis_fields',None);changed=True
                if changed:db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(data),row['id']))
        before=host.current(self.case);q=host.find(before,'questions',q['id'])
        p=self.proposal(q);p['judgment']['claims'][1]=self.judgment(statement='Corrected second claim')
        result=self.call('forsic_question',p);self.assertNotIn('error',result,result)
        after=host.current(self.case)
        self.assertEqual(host.find(after,'claims',ident),host.find(before,'claims',ident))
        view=current_judgment(after,host.find(after,'questions',q['id']))
        self.assertEqual(view['judgments'][0]['declaration_status'],'not_recorded')
        self.assertNotIn('declared_analysis_fields',host.find(after,'claims',ident))

    def test_retained_ancestry_rechecks_exact_literal_and_source_version(self):
        q=self.initial(count=2);state=host.current(self.case)
        from forsic_plugin.report_driven.grounded_judgment import retained_claims
        from forsic_plugin.report_driven.investigation_state import latest_note
        with self.case.connect() as db:note=latest_note(db,q['id'][2:])
        claim=host.find(state,'claims',q['claim_refs'][0]['id'])
        observation_id=claim['source_refs'][0]['id']
        for field,value in (('literal','Forged retained quotation'),('source_ref',dict(host.find(state,'observations',observation_id)['source_ref'],version='stale'))):
            changed=deepcopy(state);host.find(changed,'observations',observation_id)[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                retained_claims(self.case,changed,note,note,{'claims':[self.retain(q['claim_refs'][0])]},'v2')
        self.assertEqual(host.current(self.case),state)

    def test_rejections_are_atomic(self):
        q=self.initial(count=2);foreign=self.initial('foreign',count=1)
        invalid=[]
        base=self.proposal(q)
        invalid.append(dict(base,operation_id='duplicate',judgment={'claims':[self.retain(q['claim_refs'][0])]*2}))
        invalid.append(dict(base,operation_id='foreign',judgment={'claims':[self.retain(foreign['claim_refs'][0])]}))
        wrong=deepcopy(q['claim_refs'][0]);wrong['version']='stale'
        invalid.append(dict(base,operation_id='version',judgment={'claims':[self.retain(wrong)]}))
        invalid.append(dict(base,operation_id='scope',scope='Changed scope'))
        invalid.append(dict(base,operation_id='proposition',target_proposition='Different proposition'))
        mixed=self.retain(q['claim_refs'][0]);mixed['statement']='Sneaked change'
        invalid.append(dict(base,operation_id='mixed',judgment={'claims':[mixed]}))
        malformed=self.judgment(statement='Invalid replacement',citation_refs=['C1:'+'0'*32+':0'])
        invalid.append(dict(base,operation_id='invalid-citation',judgment={'claims':[self.retain(q['claim_refs'][0]),malformed]}))
        invalid.append(dict(base,operation_id='cas',expected_revision=q['revision']+1))
        new=dict(base,operation_id='new',question='New question',scope=q['scope'])
        new.pop('question_id');new.pop('expected_revision');invalid.append(new)
        for p in invalid:
            with self.subTest(op=p['operation_id']):self.assertRejected(p)

    def test_historical_retracted_and_legacy_claims_cannot_be_retained(self):
        q=self.initial(count=2);old=deepcopy(q['claim_refs'])
        q=self.revise(q,'replace-all',answer=q['answer'],reason='Full replacement remains supported',
            judgment={'claims':[self.judgment(statement='Replacement statement')]})
        self.assertRejected(dict(self.proposal(q),judgment={'claims':[self.retain(old[0])]}))
        current_ref=deepcopy(q['claim_refs'][0])
        q=self.revise(q,'withdraw',claim_action='retract',reason='Withdraw judgment')
        self.assertRejected(dict(self.proposal(q),judgment={'claims':[self.retain(current_ref)]}))
        legacy=self.save('legacy',reason='Ungrounded provisional note')
        state=host.current(self.case);candidate=next(c for c in state['claims'] if c['id']=='F-'+legacy['id'][2:])
        self.assertRejected(dict(self.proposal(legacy),judgment={'claims':[self.retain(host.ref('claim',candidate))]}))

    def test_source_version_change_and_native_ownership_reject(self):
        q=self.initial(count=2);p=self.proposal(q)
        original=host.current(self.case)
        source_id=q['evidence_ids'][0]
        event=self.case.event(source_id);event['data']['lines'][0]['text']='Mutated retained source'
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(event['data']),source_id))
        self.assertRejected(p)
        # Ownership is checked independently of citation retention.
        from forsic_plugin.report_driven.investigation_state import check_question_revision
        for kwargs in ({'session_id':'other','goal_id':'synthetic-goal'}, {'session_id':'synthetic-session','goal_id':'other'}):
            with self.assertRaises(ValueError):check_question_revision(host.find(original,'questions',q['id']),p,**kwargs)

    def test_answer_and_claim_change_leave_slots_stale_until_explicit_rebind(self):
        from forsic_plugin import independent_review as review
        q=self.initial(count=2,report_slots=[dict(slot='entry_route',state='inferred',brief='Selected request',claim_indices=[0])],
            reader_explanation={'paragraphs':[{'text':'Request evidence and uncertainty','claim_indices':[0]}]})
        fingerprint=review._review_evaluation(self.resolved,review._review_questions(self.resolved))
        self.assertEqual(q['reader_explanation']['status'],'current')
        p=self.proposal(q);p['judgment']['claims'][1]=self.judgment('process.log',statement='Creation recorded')
        result=self.call('forsic_question',p);self.assertNotIn('error',result,result)
        state=host.current(self.case);now=host.find(state,'questions',q['id'])
        self.assertEqual(now['report_slots'],q['report_slots'])
        entries=next(s['entries'] for s in project_report_slots(state)['slots'] if s['slot']=='entry_route')
        self.assertTrue(entries[0]['needs_recheck'])
        self.assertFalse(entries[0]['semantic_approval'])
        self.assertEqual(now['reader_explanation']['status'],'stale')
        self.assertNotEqual(fingerprint,review._review_evaluation(self.resolved,review._review_questions(self.resolved)))


if __name__=='__main__':unittest.main()
