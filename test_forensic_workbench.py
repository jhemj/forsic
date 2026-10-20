"""Synthetic retained cases exercise optional contracts without model/network calls."""
from copy import deepcopy
from contextlib import contextmanager
from unittest.mock import patch
import json
from pathlib import Path
import tempfile
import unittest

from forsic_plugin.evidence import Case
from forsic_plugin.report_driven import forensic_workbench as workbench
from forsic_plugin.report_driven.host import current, ref
from test_report_driven import fixture, worked


class ForensicWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.case,self.reads,self.note=fixture(self.temp.name)
        self.state=current(self.case);self.question=deepcopy(self.state['questions'][0])
        self.readonly=Case(self.case.manifest,read_only=True)

    def add(self,**data):
        sid=self.case.record('tool_result',data)
        self.state=current(self.case)
        return ref('source',next(s for s in self.state['sources'] if s['id']==sid))

    def absent(self,source,**changes):
        row=dict(declared_scope='All accounts during the requested interval',source_refs=[source],
            query_or_decoder='Literal search for the selected term',excluded_scope=['Uncollected account logs'],
            limitation='A zero match does not establish global absence')
        row.update(changes);return {'absence':[row]}

    def save(self,value):
        result=workbench.validate(self.readonly,self.state,self.question,value)
        self.question['forensic']=result
        return result

    def projection(self,**options):
        return workbench.project(self.readonly,self.state,self.question,**options)

    def test_absence_zero_matches_partial_decoder_scope_stays_distinct_from_declared_absence(self):
        source=self.add(tool='forsic_search',path='request.log',search_text='account=other',matches=[],
            bytes_scanned=40,coverage_complete=False,search_complete=False,files_skipped=1,
            records_decoded=False,representation='matched_text',scope='one selected log')
        self.save(self.absent(source));view=self.projection()
        row=view['absence_scopes'][0]
        self.assertEqual(row['declared_scope'],'All accounts during the requested interval')
        observed=row['acquired'][0]
        self.assertEqual(observed['measurement_status'],'partial')
        self.assertEqual(observed['acquired_scope']['returned_matches'],0)
        self.assertEqual(observed['acquired_scope']['scope'],'one selected log')
        self.assertFalse(observed['acquired_scope']['records_decoded'])
        self.assertFalse(observed['semantic_absence_validated']);self.assertFalse(row['all_files_gate'])
        self.assertEqual(row['semantic_scope_comparison'],'analyst_required')

    def test_complete_search_and_complete_listing_do_not_become_semantic_absence(self):
        search=self.add(tool='forsic_search',path='request.log',matches=[],search_complete=True,coverage_complete=True)
        listing=self.add(tool='forsic_list',path='.',files=[],complete=True)
        self.save({'absence':[self.absent(search)['absence'][0],self.absent(listing)['absence'][0]]})
        rows=self.projection()['absence_scopes']
        self.assertEqual(rows[0]['acquired'][0]['measurement_status'],'complete_for_returned_measurement')
        self.assertEqual(rows[1]['acquired'][0]['measurement_status'],'listing_only')
        self.assertFalse(any(r['acquired'][0]['semantic_absence_validated'] for r in rows))

    def test_failed_measurement_is_failed_not_zero_matches_or_no_activity(self):
        source=self.add(tool='forsic_read',path='missing.log',error='Synthetic read failed')
        self.save(self.absent(source));row=self.projection()['absence_scopes'][0]['acquired'][0]
        self.assertEqual(row['measurement_status'],'failed')
        self.assertNotIn('returned_matches',row['acquired_scope'])

    def test_declarations_reject_projection_fields_stale_refs_other_question_claim_and_writer_basis(self):
        source=ref('source',self.state['sources'][0]);before=self.case.db.read_bytes()
        for value in ({'basis':[source]},self.absent({**source,'version':'stale'}),
                      self.absent(source,acquired_scope='pretend full coverage')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):self.save(value)
        self.state['claims'].append({'id':'F-foreign','version':'1'})
        with self.assertRaisesRegex(ValueError,'another question'):
            self.save(self.absent(source,claim_ref={'kind':'claim','id':'F-foreign','version':'1'}))
        self.assertEqual(self.case.db.read_bytes(),before)

    def test_selected_check_is_optional_and_explicit_disposition_needs_actual_references(self):
        source=ref('source',self.state['sources'][0])
        check=dict(selected_link='Requested configuration to execution',competing_explanation='Request was never executed',
            discriminating_observation='Matching execution receipt',feasible_next_check='Read the selected process log',
            expected_decision_change='Revise the execution link if no matching run is observed')
        self.save({'checks':[check]});self.assertEqual(self.projection()['selected_checks'][0]['disposition'],'planned')
        with self.assertRaises(ValueError):self.save({'checks':[{**check,'disposition':'investigated','reason':'Checked'}]})
        self.save({'checks':[{**check,'disposition':'scope_limited','reason':'The execution log is unavailable'}]})
        self.assertEqual(self.projection()['selected_checks'][0]['disposition'],'scope_limited')
        self.save({'checks':[{**check,'disposition':'refuted','reason':'The cited record contradicts this link','evidence_refs':[source]}]})
        self.assertEqual(self.projection()['selected_checks'][0]['evidence_refs'],[source])
        self.assertFalse(self.projection()['semantic_approval'])

    def test_same_target_metadata_is_not_crypto_identity_and_separate_records_not_semantic_independence(self):
        first=ref('source',self.state['sources'][0])
        second=json.loads(self.case.invoke('forsic_read',{'path':'request.log'}))['evidence_id']
        self.state=current(self.case);second=ref('source',next(s for s in self.state['sources'] if s['id']==second))
        independent=ref('source',next(s for s in self.state['sources'] if s['id']==self.reads[1]['evidence_id']))
        self.question['evidence_ids']=[r['id'] for r in (first,second,independent)]
        self.save({});view=self.projection()
        pair=next(r for r in view['source_relations'] if {r['left'],r['right']}=={first['id'],second['id']})
        self.assertEqual(pair['relation'],'same_target_metadata')
        self.assertTrue(all(r['identity_check_level']=='metadata_only' for r in view['source_lineage']))
        self.assertTrue(any(r['relation']=='separate_recorded_acquisitions' for r in view['source_relations']))
        self.assertTrue(all(r['semantic_independence']=='not_determined' for r in view['source_relations']))

    def test_observed_reuse_has_shared_origin_but_declared_shell_parents_remain_unobserved(self):
        original=ref('source',self.state['sources'][0])
        copy=self.add(tool='forsic_read',path='copy.log',reused_from=original['id'],lines=[{'line':1,'text':'copy'}])
        shell=self.add(tool='forsic_shell',path='copy.log',started_id='synthetic-execution',
            stdout='PRIVATE_LARGE_STDOUT'*10000,command='PRIVATE_COMMAND',representation='derived_command_output')
        self.question['evidence_ids']=[original['id'],copy['id'],shell['id']]
        self.save({'declared_lineage':[{'source_ref':shell,'parent_source_refs':[original],'reason':'Analyst says this script read the original'}]})
        view=self.projection();lineages={r['source_ref']['id']:r for r in view['source_lineage']}
        self.assertEqual(lineages[copy['id']]['observed_inputs'],[original])
        self.assertEqual(lineages[shell['id']]['observed_inputs'],[])
        self.assertEqual(lineages[shell['id']]['analyst_declared_inputs'],[original])
        self.assertEqual(lineages[shell['id']]['input_lineage'],'unknown_arbitrary_command_inputs')
        self.assertTrue(any(r['relation']=='same_recorded_origin' for r in view['source_relations']))
        self.assertTrue(any(r['relation']=='unknown' for r in view['source_relations']))
        self.assertNotIn('PRIVATE_LARGE_STDOUT',json.dumps(view));self.assertNotIn('PRIVATE_COMMAND',json.dumps(view))

    def test_only_observed_full_original_hash_raises_identity_check_level(self):
        hashed=json.loads(self.case.invoke('forsic_hash',{'path':'request.log'}))['evidence_id']
        self.state=current(self.case);self.question['evidence_ids']=[hashed]
        self.save({});row=self.projection()['source_lineage'][0]
        self.assertEqual(row['identity_check_level'],'content_sha256')
        self.assertEqual(row['output_fingerprint']['scope'],'retained_tool_result')
        self.assertNotEqual(row['output_fingerprint']['sha256'],row['original_content_fingerprint'])
        self.assertEqual(row['semantic_independence'],'not_determined')

    def test_identical_full_original_content_hash_is_distinguished_from_different_result_receipt_hashes(self):
        (self.case.root/'duplicate.log').write_bytes((self.case.root/'request.log').read_bytes())
        hashes=[json.loads(self.case.invoke('forsic_hash',{'path':name}))['evidence_id']
                for name in ('request.log','duplicate.log')]
        self.state=current(self.case);self.question['evidence_ids']=hashes;self.save({})
        view=self.projection();pair=view['source_relations'][0]
        self.assertEqual(pair['relation'],'same_observed_content_sha256')
        self.assertEqual(pair['semantic_independence'],'not_determined')
        rows=view['source_lineage']
        self.assertNotEqual(rows[0]['output_fingerprint']['sha256'],rows[1]['output_fingerprint']['sha256'])
        self.assertEqual(rows[0]['original_content_fingerprint'],rows[1]['original_content_fingerprint'])

    def test_automatic_claim_observation_ancestry_and_only_dependent_question_changes(self):
        self.state=worked(self.case);self.question=deepcopy(self.state['questions'][0])
        # The fixture's adopted claim links observations from the two exact sources.
        self.save({});kinds={r['kind'] for r in self.question['forensic']['basis']}
        self.assertTrue({'source','observation','claim'}<=kinds)
        self.assertNotIn('question',kinds)
        unrelated_source=self.add(tool='forsic_read',path='unrelated.log',lines=[{'line':1,'text':'Independent record'}])
        other={**deepcopy(self.question),'id':'Q-other','version':'other-version','claim_refs':[],
               'evidence_ids':[unrelated_source['id']],'forensic':{}}
        self.state['questions']=[self.question,other]
        chosen=next(r for r in self.question['forensic']['basis'] if r['kind']=='source')
        source=next(s for s in self.state['sources'] if s['id']==chosen['id']);source['version']='corrected-version'
        impact=workbench.change_impact(self.state,[ref('source',source)])
        self.assertEqual([r['question_ref']['id'] for r in impact['dependent_questions']],[self.question['id']])
        self.assertNotIn('Q-other',json.dumps(impact))
        self.assertTrue(impact['dependent_claim_refs'])
        self.assertFalse(impact['automatic_reopening'])
        self.assertEqual(impact['report_snapshot_refs'][0]['affected_question_ids'],[self.question['id']])
        view=self.projection();self.assertTrue(view['change_impacts'])
        self.assertFalse(any(r['automatic_conclusion_change'] for r in view['change_impacts']))
        self.assertEqual(self.question['forensic']['basis'],self.question['forensic']['basis'])

    def test_exact_explicit_time_event_dependency_changes_only_its_question(self):
        self.state['timeline']=[{'id':'TM-record','event_ref':'TE-selected','version':'time-v1','temporal':{'basis_refs':[]}}]
        selected={'kind':'time_event','id':'TE-selected','version':'time-v1'}
        self.save({'dependencies':[{'reference':selected,'reason':'Answer depends on this inferred year'}]})
        other={**deepcopy(self.question),'id':'Q-other','version':'other','evidence_ids':[],'claim_refs':[],'forensic':{}}
        self.state['questions']=[self.question,other];self.state['timeline'][0]['version']='time-v2'
        impact=workbench.change_impact(self.state,[{**selected,'version':'time-v2'}])
        self.assertEqual([r['question_ref']['id'] for r in impact['dependent_questions']],[self.question['id']])
        self.assertEqual(self.projection()['change_impacts'][0]['selected_ref'],selected)
        with self.assertRaises(ValueError):self.save({'dependencies':[{'reference':{**selected,'id':'TE-unknown'},'reason':'Guess'}]})

    def test_scope_and_cross_question_dependencies_are_explicit_and_self_reference_is_rejected(self):
        scope=ref('scope',self.state['scope'])
        self.save({'dependencies':[{'reference':scope,'reason':'This selected answer depends on the admitted scope'}]})
        other={**deepcopy(self.question),'id':'Q-other','version':'other','claim_refs':[],'evidence_ids':[],'forensic':{}}
        self.state['questions']=[self.question,other]
        impact=workbench.change_impact(self.state,[{**scope,'version':'new-scope'}])
        self.assertEqual([r['question_ref']['id'] for r in impact['dependent_questions']],[self.question['id']])
        with self.assertRaisesRegex(ValueError,'owning question'):
            self.save({'dependencies':[{'reference':ref('question',self.question),'reason':'Self'}]})

    def test_scope_definition_and_removed_adopted_claim_remain_visible_without_self_version_churn(self):
        self.state=worked(self.case);self.question=deepcopy(self.state['questions'][0]);self.save({})
        original=deepcopy(self.question['forensic'])
        self.question['version']='changed-only-by-current-note-metadata'
        self.assertEqual(self.projection()['change_impacts'],[])
        old=self.question['claim_refs'][0]
        self.question['claim_refs']=[]
        # The old adopted record still exists with the same content version.
        hints=self.projection()['change_impacts']
        self.assertTrue(any(r.get('selected_ref',{}).get('id')==old['id'] and 'replaced' in r['recheck_reason'] for r in hints))
        self.question['definition_version']='corrected-proposition-or-scope'
        self.assertTrue(any('scope changed' in r['recheck_reason'] for r in self.projection()['change_impacts']))
        self.assertEqual(self.question['forensic'],original)
        self.assertNotIn('definition_basis',workbench.schema()['properties'])

    def test_time_basis_source_change_uses_exact_owned_event_and_affects_dependents_only(self):
        source=ref('source',self.state['sources'][0])
        self.state['timeline']=[{'id':'T-current','event_ref':'TE-owned','question_id':self.question['id'],'version':'v1',
            'temporal':{'basis_refs':[{'evidence_id':source['id'],'source_version':source['version'],
                                      'pointer':'/lines/0/text','literal':'time','byte_start':0}]}}]
        self.save({})
        self.assertIn('time_event',{r['kind'] for r in self.question['forensic']['basis']})
        self.state['questions']=[self.question,{**deepcopy(self.question),'id':'Q-other','version':'other','claim_refs':[],
            'evidence_ids':[],'forensic':{}}]
        impact=workbench.change_impact(self.state,[{**source,'version':'corrected-source'}])
        self.assertEqual([r['question_ref']['id'] for r in impact['dependent_questions']],[self.question['id']])

    def test_metadata_projection_batches_one_bounded_select_per_source_and_never_loads_body(self):
        self.save({});statements=[];original=self.readonly.connect
        @contextmanager
        def traced():
            with original() as db:
                db.set_trace_callback(statements.append)
                yield db
        with patch.object(self.readonly,'connect',traced),patch.object(self.readonly,'event',side_effect=AssertionError('No whole receipt loading')):
            view=self.projection()
        selects=[s for s in statements if s.startswith('SELECT')]
        self.assertEqual(len(selects),view['selected_source_count'])
        self.assertTrue(all('substr(json_extract' in statement for statement in selects))

    def test_note_shaped_writer_input_and_schema_do_not_expose_program_basis(self):
        note={**deepcopy(self.note),'investigation':{'claim_refs':[]}}
        parsed=workbench.validate(self.readonly,self.state,note,{})
        self.assertTrue(parsed['basis'])
        schema=workbench.schema()
        self.assertNotIn('basis',schema['properties']);self.assertNotIn('$defs',schema)
        self.assertLess(len(json.dumps(schema)),6000)

    def test_large_selected_ancestry_is_explicitly_partial_instead_of_blocking_optional_declarations(self):
        ids=[self.case.record('tool_result',{'tool':'forsic_read','path':'bounded-'+str(index)+'.log',
             'complete_file':True,'lines':[{'line':1,'text':'synthetic record'}]}) for index in range(70)]
        self.state=current(self.case);self.question['evidence_ids']=ids
        self.save({});basis=self.question['forensic']
        self.assertEqual(len(basis['basis']),64);self.assertEqual(basis['basis_omitted'],6)
        view=self.projection(max_chars=2000)
        self.assertEqual(view['tracked_dependency_count'],64)
        self.assertEqual(view['omitted_dependency_count'],6)
        self.assertEqual(view['selected_source_count'],70)
        self.assertEqual(view['omitted_source_metadata'],38)
        self.assertLessEqual(len(workbench._json(view)),2000)

    def test_projection_is_bounded_counts_before_omission_and_never_mutates_case_or_state(self):
        self.save({'absence':[self.absent(ref('source',self.state['sources'][0]))['absence'][0]]*4})
        before=self.case.db.read_bytes();question=deepcopy(self.question);state=deepcopy(self.state)
        view=self.projection(max_items=1,max_chars=900)
        self.assertLessEqual(len(workbench._json(view)),900)
        self.assertEqual(view['counts']['absence_scopes'],4)
        self.assertTrue(view['partial'])
        tiny=self.projection(max_chars=256)
        self.assertLessEqual(len(workbench._json(tiny)),256)
        self.assertEqual(self.case.db.read_bytes(),before);self.assertEqual(self.state,state);self.assertEqual(self.question,question)
        with self.assertRaises(ValueError):self.projection(max_chars=255)


if __name__=='__main__':unittest.main()
