"""Actual atomic question writer integration, synthetic evidence and no model calls."""
from copy import deepcopy
import json
import unittest

import test_question_v2 as fixtures
import test_investigation_context as goal_fixtures
from hermes_cli.goals import goal_identity
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.current_judgment import current_judgment


class ForensicWorkbenchWriterTests(unittest.TestCase):
    setUp=fixtures.QuestionV2Tests.setUp
    call=fixtures.QuestionV2Tests.call
    save=fixtures.QuestionV2Tests.save
    judgment=fixtures.QuestionV2Tests.judgment
    revise=fixtures.QuestionV2Tests.revise
    close_payload=fixtures.QuestionV2Tests.close_payload

    def counts(self):
        with self.case.connect() as db:
            return {kind:db.execute('SELECT count(*) FROM events WHERE kind=?',(kind,)).fetchone()[0]
                    for kind in ('note','report_change','mutation_receipt')}

    def grounded(self,op='grounded',**changes):
        return self.save(op,judgment=self.judgment(),reason='Select one observed request line',forensic={},**changes)

    def test_same_atomic_write_captures_new_adopted_ancestry_without_false_stale_hint(self):
        q=self.grounded();state=host.current(self.case);host.checked(state)
        stored=q['forensic'];self.assertTrue(stored['basis'])
        self.assertEqual({r['kind'] for r in stored['basis']},{'claim','observation','source'})
        self.assertEqual(stored['definition_basis'],q['definition_version'])
        self.assertEqual(q['forensic_context']['change_impacts'],[])
        self.assertFalse(q['forensic_context']['semantic_approval'])
        selected={r['id'] for r in q['claim_refs']}
        self.assertTrue(selected <= {r['id'] for r in stored['basis'] if r['kind']=='claim'})
        with self.case.connect() as db:
            raw=json.loads(db.execute("SELECT data FROM events WHERE kind='note' AND json_extract(data,'$.note_id')=? ORDER BY rowid DESC LIMIT 1",(q['id'][2:],)).fetchone()[0])
        self.assertEqual(raw['forensic'],stored)
        self.assertNotIn('forensic',raw.get('investigation',{}))
        again=host.current(self.case)
        self.assertEqual(again['meta']['snapshot_id'],state['meta']['snapshot_id'])
        self.assertEqual(host.find(again,'questions',q['id'])['version'],q['version'])

    def test_invalid_forensic_after_new_judgment_rolls_back_note_claim_and_operation(self):
        judgment=self.judgment();before=host.current(self.case);counts=self.counts()
        result=self.call('forsic_question',dict(operation_id='bad-new-workbench',question='Was it executed?',
            scope='Synthetic acquired records',answer='Request exists; execution unknown',priority='material',
            reason='Record exact source',judgment=judgment,
            forensic={'dependencies':[{'reference':{'kind':'source','id':'missing','version':'invented'},'reason':'Invalid selection'}]}))
        self.assertIn('error',result,result)
        after=host.current(self.case)
        self.assertEqual(self.counts(),counts)
        for field in ('questions','claims','observations'):self.assertEqual(after[field],before[field])
        self.assertEqual(after['meta']['content_revision'],before['meta']['content_revision'])

    def test_identical_operation_replay_does_not_recapture_basis_or_duplicate_records(self):
        judgment=self.judgment()
        payload=dict(operation_id='idempotent-workbench',question='Same request?',scope='Selected request record',
            answer='A maintenance request was recorded.',priority='material',reason='Retain exact source',judgment=judgment,forensic={})
        first=self.call('forsic_question',deepcopy(payload));self.assertNotIn('error',first,first)
        counts=self.counts();state=host.current(self.case)
        second=self.call('forsic_question',deepcopy(payload));self.assertNotIn('error',second,second)
        self.assertEqual(self.counts(),counts)
        self.assertEqual(host.current(self.case)['questions'],state['questions'])
        self.assertEqual(first['question']['question_id'],second['question']['question_id'])
        self.assertEqual(first['question']['revision'],second['question']['revision'])

    def test_unrelated_note_edit_preserves_frozen_basis_and_does_not_create_self_version_churn(self):
        q=self.grounded();basis=deepcopy(q['forensic'])
        q=self.revise(q,'add-next-check',next_checks=['If a completion receipt becomes available, compare the run identifier'])
        self.assertEqual(q['forensic'],basis)
        self.assertEqual(q['forensic_context']['change_impacts'],[])
        state=host.current(self.case);self.assertEqual(host.current(self.case)['meta']['snapshot_id'],state['meta']['snapshot_id'])

    def test_answer_replacement_retains_old_claim_in_history_and_flags_only_its_question(self):
        first=self.grounded('first');second=self.grounded('second')
        old=deepcopy(first['forensic']);old_claim=first['claim_refs'][0]['id']
        changed=self.revise(first,'correct-answer',answer='The recorded request does not establish completion.',
            reason='Correct the scope of the execution link')
        state=host.current(self.case);other=host.find(state,'questions',second['id'])
        self.assertEqual(changed['forensic'],old)
        self.assertTrue(any(r.get('selected_ref',{}).get('id')==old_claim and 'replaced' in r['recheck_reason']
                            for r in changed['forensic_context']['change_impacts']))
        self.assertTrue(any(c['id']==old_claim for c in state['claims']))
        self.assertEqual(other['forensic_context']['change_impacts'],[])
        self.assertFalse(any(r['automatic_reopening'] for r in changed['forensic_context']['change_impacts']))

    def test_definition_correction_signals_frozen_definition_for_related_question_only(self):
        first=self.grounded('first');second=self.grounded('second')
        before=deepcopy(first['forensic'])
        changed=self.revise(first,'narrow-scope',scope='Only maintenance requests in request.log',
            reason='Narrow the claimed investigation scope')
        self.assertEqual(changed['forensic'],before)
        self.assertTrue(any('scope changed' in r['recheck_reason'] for r in changed['forensic_context']['change_impacts']))
        self.assertEqual(host.find(host.current(self.case),'questions',second['id'])['forensic_context']['change_impacts'],[])

    def temporal_payload(self,op='timed',**changes):
        read=self.call('forsic_read',{'path':'request.log'})
        temporal={'observed_or_inferred':'inferred','timestamp_role':'event_occurrence',
            'confidence_basis':'Selected request text establishes the request; occurrence date is uncertain.',
            'basis_refs':[{'evidence_id':read['evidence_id'],'pointer':'/lines/0/text','literal':'job=maintenance','byte_start':0}]}
        row={'event_ref':'TE-selected','description':'Selected request-related event with uncertain occurrence date',
             'evidence_ids':[read['evidence_id']],'temporal':temporal}
        value=dict(operation_id=op,question='When could the selected request relate to execution?',scope='Selected request record',
            answer='The request is recorded; occurrence time remains uncertain.',priority='material',
            reason='Keep explicit source and temporal uncertainty',timeline=[row],forensic={},
            judgment={'citation_refs':[read['presented_spans'][0]['citation_ref']],'assertion_kind':'interpretation',
                      'reasoning_summary':'The request line does not establish execution or an exact occurrence time.'})
        value.update(changes);return value

    def test_same_atomic_write_captures_new_time_event_basis_after_new_time_records_exist(self):
        result=self.call('forsic_question',self.temporal_payload())
        self.assertNotIn('error',result,result)
        q=host.find(host.current(self.case),'questions',result['question']['question_id'])
        self.assertIn('time_event',{r['kind'] for r in q['forensic']['basis']})
        self.assertEqual(q['forensic_context']['change_impacts'],[])

    def test_time_correction_only_flags_exact_dependent_question_preserving_old_basis(self):
        payload=self.temporal_payload('time-first');payload.pop('forensic')
        result=self.call('forsic_question',payload);self.assertNotIn('error',result,result)
        q=host.find(host.current(self.case),'questions',result['question']['question_id'])
        q=self.revise(q,'capture-existing-time',forensic={},reason='Select the existing time dependency')
        self.assertIn('time_event',{r['kind'] for r in q['forensic']['basis']})
        other=self.grounded('unrelated-time-question')
        baseline=deepcopy(q['forensic'])
        row=deepcopy(payload['timeline'][0]);row['temporal'].update(
            earliest='2026-10-01T00:00:00Z',latest='2026-10-02T00:00:00Z',
            timezone_basis='Candidate bounds stated in UTC; clock offset remains unknown.',
            year_basis='Proposed 2026 candidate under the selected source-link assumption.',
            assumptions=['The selected request belongs to the proposed event interval.'])
        changed=self.revise(q,'correct-time-bounds',timeline=[row],reason='State a revised bounded event hypothesis')
        self.assertEqual(changed['forensic'],baseline)
        self.assertTrue(any(r.get('selected_ref',{}).get('kind')=='time_event' for r in changed['forensic_context']['change_impacts']))
        self.assertEqual(host.find(host.current(self.case),'questions',other['id'])['forensic_context']['change_impacts'],[])

    def test_relation_only_source_is_captured_by_actual_writer_and_selective_change_ancestry(self):
        from forsic_plugin.report_driven.forensic_workbench import change_impact
        payload=self.temporal_payload('relation-only-basis')
        process=self.call('forsic_read',{'path':'process.log'});process_id=process['evidence_id']
        origin=payload['timeline'][0];origin['evidence_ids'].append(process_id)
        origin['temporal']['relations']=[{'target_event_ref':'TE-target','relation':'before',
            'reason':'A selected process record is the separate basis for this proposed ordering.',
            'basis_refs':[{'evidence_id':process_id,'pointer':'/lines/0/text','literal':'process=task','byte_start':0}]}]
        target=deepcopy(origin);target['event_ref']='TE-target';target['description']='The explicitly selected second event'
        target['temporal'].pop('relations');target['evidence_ids']=[origin['temporal']['basis_refs'][0]['evidence_id']]
        payload['timeline'].append(target)
        result=self.call('forsic_question',payload);self.assertNotIn('error',result,result)
        state=host.current(self.case);q=host.find(state,'questions',result['question']['question_id'])
        self.assertNotIn(process_id,q['evidence_ids'])  # Not selected by the aggregate judgment.
        self.assertIn(process_id,{r['id'] for r in q['forensic']['basis'] if r['kind']=='source'})
        observed={o['id']:o for o in state['observations']}
        relation_observations=[r for row in state['timeline'] if row.get('question_id')==q['id']
            for r in row['temporal_context']['observation_refs']
            if observed[r['id']]['source_ref']['id']==process_id]
        self.assertTrue(relation_observations)
        self.assertTrue({r['id'] for r in relation_observations}<={r['id'] for r in q['forensic']['basis'] if r['kind']=='observation'})
        other=self.grounded('unrelated-to-relation');state=host.current(self.case)
        changed={'kind':'source','id':process_id,'version':'corrected-process-version'}
        impact=change_impact(state,[changed])
        self.assertEqual([r['question_ref']['id'] for r in impact['dependent_questions']],[q['id']])
        self.assertNotIn(other['id'],json.dumps(impact))
        # When a stale source cannot produce an observation, retained relation bases
        # still describe the dependency. Remove frozen workbench shortcuts as well.
        stale=deepcopy(state)
        stale['observations']=[o for o in stale['observations'] if o['source_ref']['id']!=process_id]
        for question in stale['questions']:question['forensic']={}
        for row in stale['timeline']:
            row['temporal_context']['observation_refs']=[r for r in row['temporal_context']['observation_refs']
                if r['id'] not in {o['id'] for o in relation_observations}]
        impact=change_impact(stale,[changed])
        self.assertEqual([r['question_ref']['id'] for r in impact['dependent_questions']],[q['id']])

    def test_malformed_long_or_cyclic_ancestry_does_not_exhaust_python_recursion(self):
        from forsic_plugin.report_driven.forensic_workbench import _automatic_refs,_roots
        claims=[{'id':'F-'+str(i),'version':'v','source_refs':[{'kind':'claim','id':'F-'+str(i+1),'version':'v'}]} for i in range(1200)]
        claims[-1]['source_refs']=[{'kind':'claim','id':'F-0','version':'v'}]
        rows=_automatic_refs({'claims':claims},{'id':'Q-test','claim_refs':[{'kind':'claim','id':'F-0','version':'v'}]})
        self.assertEqual(len(rows),1200)
        lineage={str(i):{'input_lineage':'observed_parent_receipts','observed_inputs':[{'id':str(i+1)}]} for i in range(1200)}
        roots,unknown=_roots('0',lineage)
        self.assertTrue(unknown);self.assertEqual(roots,set())
        lineage={'a':{'input_lineage':'observed_parent_receipts','observed_inputs':[{'id':'b'}]},
                 'b':{'input_lineage':'observed_parent_receipts','observed_inputs':[{'id':'a'}]}}
        self.assertTrue(_roots('a',lineage)[1])

    def test_frozen_source_change_propagates_through_explicit_question_dependency_without_version_churn(self):
        from forsic_plugin.report_driven.forensic_workbench import propagate_changes
        source_question=self.grounded('source-owner')
        linked=self.save('linked-question',forensic={'dependencies':[{'reference':host.ref('question',source_question),
            'reason':'This answer uses the source question reasoning'}]})
        unrelated=self.grounded('unrelated-question')
        before=host.current(self.case);source_before=host.find(before,'questions',source_question['id'])
        source_id=next(r['id'] for r in source_before['forensic']['basis'] if r['kind']=='source')
        record=self.case.event(source_id);record['data']['lines'][0]['text']='Corrected retained source content'
        with self.case.connect() as db,db:
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(record['data']),source_id))
        state=host.current(self.case);source_after=host.find(state,'questions',source_question['id'])
        self.assertEqual(source_before['version'],source_after['version'])
        original=deepcopy(state);notes=self.counts()
        propagation=propagate_changes(state)
        self.assertIn(linked['id'],propagation['contexts'])
        self.assertNotIn(unrelated['id'],propagation['contexts'])
        context=propagation['contexts'][linked['id']]
        self.assertTrue(any(row.get('inherited_dependency_change') and row['changed_ref']['id']==source_id
                            for row in context['change_impacts']))
        self.assertTrue(any(row['via_selected_ref']['id']==source_question['id'] for row in context['change_impacts']))
        self.assertEqual(state,original);self.assertEqual(self.counts(),notes)
        self.assertFalse(propagation['automatic_reopening'])
        limited=propagate_changes(state,max_chars=900,max_items=1)
        self.assertLessEqual(len(json.dumps(limited['contexts'][linked['id']],ensure_ascii=False,sort_keys=True,separators=(',',':'))),900)
        omitted=propagate_changes(state,max_changed_refs=0)
        self.assertGreater(omitted['omitted_changed_references'],0)
        self.assertTrue(omitted['contexts'][linked['id']]['partial'])

    def test_external_claim_dependency_is_not_misclassified_as_withdrawn_own_claim(self):
        from forsic_plugin.report_driven.forensic_workbench import schema
        owner=self.grounded('external-claim-owner');claim=owner['claim_refs'][0]
        dependent=self.save('external-claim-dependent',forensic={'dependencies':[{'reference':claim,
            'reason':'This question explicitly uses another question claim'}]})
        self.assertNotIn(claim['id'],dependent['forensic']['own_claim_ids'])
        self.assertIn(claim['id'],{r['id'] for r in dependent['forensic']['basis']})
        self.assertEqual(dependent['forensic_context']['change_impacts'],[])
        self.assertNotIn('own_claim_ids',schema()['properties'])
        rejected=self.call('forsic_question',{'operation_id':'fake-ownership','question_id':dependent['id'],
            'expected_revision':dependent['revision'],'forensic':{'own_claim_ids':[claim['id']]}})
        self.assertIn('error',rejected)
        changed=self.revise(owner,'withdraw-owned-claim',answer='The observed request has a narrower interpretation.',
            reason='Withdraw the prior aggregate statement')
        self.assertTrue(any(r.get('selected_ref',{}).get('id')==claim['id'] for r in changed['forensic_context']['change_impacts']))

    def test_propagation_recomputes_existing_generated_rows_and_clipped_counts_idempotently(self):
        from forsic_plugin.report_driven.forensic_workbench import propagate_changes
        owner=self.grounded('idempotent-owner')
        dependent=self.save('idempotent-dependent',forensic={'dependencies':[{'reference':host.ref('question',owner),
            'reason':'Retain selected upstream reasoning'}]})
        source_id=next(r['id'] for r in owner['forensic']['basis'] if r['kind']=='source')
        event=self.case.event(source_id);event['data']['lines'][0]['text']='A corrected retained source observation'
        with self.case.connect() as db,db:
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(event['data']),source_id))
        state=host.current(self.case)
        for cap in (7000,900,256):
            with self.subTest(cap=cap):
                first=propagate_changes(state,max_chars=cap,max_items=1)
                again_state=deepcopy(state)
                for question in again_state['questions']:
                    if question['id'] in first['contexts']:question['forensic_context']=first['contexts'][question['id']]
                second=propagate_changes(again_state,max_chars=cap,max_items=1)
                self.assertEqual(first['contexts'][dependent['id']],second['contexts'][dependent['id']])
                self.assertEqual(first['contexts'][dependent['id']]['counts']['change_impacts'],1)
                self.assertLessEqual(len(json.dumps(second['contexts'][dependent['id']],ensure_ascii=False,sort_keys=True,separators=(',',':'))),cap)

    def test_legacy_and_new_questions_do_not_require_workbench_or_all_file_coverage(self):
        legacy=host.current(self.case)['questions'][0]
        self.assertEqual(legacy.get('forensic',{}),{})
        q=self.save('optional')
        self.assertEqual(q.get('forensic',{}),{})
        grounded=self.save('no-workbench',judgment=self.judgment(),reason='Keep one local selected source')
        result=self.call('forsic_question',self.close_payload(grounded))
        self.assertNotIn('error',result,result)
        closed=host.find(host.current(self.case),'questions',grounded['id'])
        self.assertEqual(closed['work_state'],'scoped_closed')
        self.assertEqual(closed.get('forensic',{}),{})


class ForensicGoalProjectionTests(unittest.TestCase):
    setUp=goal_fixtures.InvestigationContextTests.setUp
    bind=goal_fixtures.InvestigationContextTests.bind
    session=goal_fixtures.InvestigationContextTests.session
    save_note=goal_fixtures.InvestigationContextTests.save_note
    kwargs=goal_fixtures.InvestigationContextTests.kwargs

    def test_actual_atomic_writer_workbench_reaches_bounded_native_goal_navigation(self):
        from forsic_plugin import investigation_context as context
        state=host.current(self.case);question=state['questions'][0];source=host.ref('source',state['sources'][0])
        declaration={'declared_scope':'Selected acquired records and requested interval',
            'source_refs':[source],'query_or_decoder':'Only returned decoded fields',
            'excluded_scope':['Uncollected account records'],'limitation':'The selected observation does not establish system-wide absence'}
        payload={'operation_id':'native-workbench','question_id':question['id'],'expected_revision':question['revision'],
            'forensic':{'absence':[declaration]*3}}
        result=json.loads(self.case.invoke('forsic_reporting',{'action':'question','payload':payload},self.sid,
            request_context={'goal_id':goal_identity(self.goal),'conversation_id':self.sid,'question_contract_version':'v2'}))
        self.assertNotIn('error',result,result)
        before=self.case.db.read_bytes()
        result=context.goal_evaluation_context(goal_id=goal_identity(self.goal),**self.kwargs())
        self.assertEqual(result['status'],'incomplete',result)
        # The judge receives the current answer/limits within its separate
        # 12k-character budget. Only navigation/compaction has the 5KiB budget.
        self.assertLessEqual(len(json.dumps(result['context'],ensure_ascii=False)),12000-1100)
        body=json.loads(result['context']);active=next(row for row in body['active_questions'] if row['id']==question['id'])
        self.assertEqual(active['forensic']['counts']['absence_scopes'],3)
        self.assertEqual(active['forensic']['selected_dependency_changes'],0)
        self.assertEqual(active['forensic']['detail_call']['arguments']['question_id'],question['id'])
        self.assertNotIn('system-wide absence',json.dumps(active['forensic']))
        self.assertEqual(self.case.db.read_bytes(),before)


if __name__=='__main__':unittest.main()
