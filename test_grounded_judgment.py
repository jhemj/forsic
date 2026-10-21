"""Offline native read -> selected evidence -> current judgment, without missions."""
import json
from copy import deepcopy
from pathlib import Path
import subprocess
from unittest.mock import patch
import unittest
import test_efficiency_contracts as fixtures
from forsic_plugin.report_driven.host import current,find,checked
from forsic_plugin.report_driven.current_judgment import current_judgment

class GroundedJudgmentTests(unittest.TestCase):
    setUp=fixtures.EfficiencyContracts.setUp
    call=fixtures.EfficiencyContracts.call
    def read(self,path='request.log'):
        r=self.call('forsic_read',{'path':path})
        self.assertNotIn('error',r,r)
        self.assertTrue(r['presented_spans'])
        return r
    def payload(self,refs,**changes):
        return dict(operation_id='grounded',question='Was the task executed?',scope='Two acquired records',
            answer='A request is recorded; successful execution is not established.',reason='Interpret the explicitly selected record',
            assessment='undetermined',priority='material',gaps=['Independent execution receipt not acquired'],
            alternatives=['A request without execution'],next_checks=['Compare independent process records'],
            judgment=dict(citation_refs=refs,counter_citation_refs=[],assertion_kind='interpretation',inference_strength='plausible',
                reasoning_summary='A request statement does not establish successful execution',assumptions=[],limitations=['Acquired record only']),**changes)
    def ref(self,r):return r['presented_spans'][0]['citation_ref']
    def test_read_create_judgment_report_no_mission_or_assessment(self):
        r=self.read();before=current(self.case);p=self.payload([self.ref(r)])
        saved=self.call('forsic_question',p);self.assertNotIn('error',saved,saved)
        s=current(self.case);checked(s)
        q=find(s,'questions',saved['question']['question_id']);j=current_judgment(s,q)
        self.assertFalse(j['review_required']);self.assertTrue(j['claim_refs'])
        claim=find(s,'claims',q['claim_refs'][0]['id']);ob=find(s,'observations',claim['source_refs'][0]['id'])
        self.assertEqual(ob['literal'],r['lines'][0]['text'])
        self.assertEqual(claim['semantic_review'],'not_reviewed')
        for key in ('missions','tests','assessments'):self.assertEqual(s[key],before[key])
        self.assertEqual(self.call('forsic_question',p)['question'],saved['question'])
        from forsic_plugin.report_driven.views import compile_views
        rendered=compile_views(s)
        self.assertIn(p['answer'],str(rendered))
        self.assertIn(p['judgment']['limitations'][0],str(rendered))
    def test_counterevidence_correction_and_retraction_preserve_history(self):
        r=self.read();r2=self.read('process.log');p=self.payload([self.ref(r)])
        saved=self.call('forsic_question',p);s=current(self.case);q=find(s,'questions',saved['question']['question_id'])
        previous=q['claim_refs'][0]['id']
        p2=self.payload([self.ref(r2)]);p2.update(operation_id='correction',question_id=q['id'],expected_revision=q['revision'],answer='A process was created; request linkage remains uncertain.')
        p2['judgment']['counter_citation_refs']=[self.ref(r)]
        fixed=self.call('forsic_question',p2);self.assertNotIn('error',fixed,fixed)
        s=current(self.case);checked(s);q=find(s,'questions',q['id'])
        self.assertEqual(find(s,'claims',previous)['status'],'superseded')
        self.assertTrue(find(s,'claims',q['claim_refs'][0]['id'])['counterevidence_refs'])
        ret=self.call('forsic_question',dict(operation_id='retract',question_id=q['id'],expected_revision=q['revision'],claim_action='retract',reason='Withdraw the interpretation'))
        self.assertNotIn('error',ret,ret);s=current(self.case);checked(s)
        self.assertFalse(find(s,'questions',q['id'])['claim_refs'])
    def test_invalid_ref_rolls_back_note_claim_and_mutation(self):
        r=self.read();before=current(self.case);p=self.payload([self.ref(r)[:-1]+'9'])
        result=self.call('forsic_question',p);self.assertIn('error',result)
        after=current(self.case)
        for key in ('questions','claims','observations','missions','tests','assessments'):self.assertEqual(before[key],after[key])
    def test_frozen_ref_rejects_changed_source(self):
        r=self.read();p=self.payload([self.ref(r)]);event=self.case.event(r['evidence_id']);event['data']['lines'][0]['text']='Changed'
        with self.case.connect() as db:db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(event['data']),r['evidence_id']))
        self.assertIn('error',self.call('forsic_question',p))
    def test_source_page_c1_remains_valid_without_mission(self):
        r=self.read();page=self.call('forsic_reporting',{'action':'source','source_id':r['evidence_id'],'query':'requested'})
        c=page['matches'][0]['citation_ref'];self.assertTrue(c.startswith('C1:'))
        self.assertNotIn('error',self.call('forsic_question',self.payload([c])))
    def test_failed_reads_and_metadata_do_not_offer_body_refs(self):
        failed=self.call('forsic_read',{'path':'missing.log'});self.assertNotIn('presented_spans',failed)
        metadata=self.call('forsic_list',{'path':'.'});self.assertNotIn('presented_spans',metadata)

    def failed_shell(self):
        from forsic_plugin import shell_workspace as shell
        work = Path(self.temp.name) / 'shell-work'
        work.mkdir(exist_ok=True)
        def run(argv, container, *_args, **_kwargs):
            process = subprocess.run(['/bin/bash', '-e', '-o', 'pipefail', '-c', argv[-1]],
                capture_output=True, cwd=work)
            return {'stdout': process.stdout, 'stderr': process.stderr,
                'execution': {'state': 'returned', 'backend': 'docker', 'container': container,
                    'exit_code': process.returncode, 'termination_confirmed': True}}
        with patch.object(shell, '_evidence_mounts', return_value=[]), \
                patch.object(shell, '_workspace', return_value=work), \
                patch.object(shell.os, 'getuid', return_value=1000), \
                patch.object(shell, 'run_container', side_effect=run) as executed:
            result = json.loads(self.case.invoke('forsic_shell',
                {'command': "printf 'observed partial output\\n'; exit 1"}, 'synthetic-session',
                request_context={'status': 'ready', 'goal_id': 'synthetic-goal', 'goal_status': 'active',
                    'requested_session_id': 'synthetic-session', 'conversation_id': 'synthetic-session',
                    'case_id': self.case.config['case_id']}))
        self.assertEqual(executed.call_count, 1, result)
        self.assertEqual(result['execution']['exit_code'], 1)
        self.assertIn('error', result)
        self.assertNotIn('presented_spans', result)
        return result

    def test_failed_output_citation_rejection_lookup_and_explicit_c1_retry(self):
        from forsic_plugin.report_driven.host import source_record
        valid = self.ref(self.read())
        failed = self.failed_shell()
        source = source_record(self.case.event(failed['evidence_id']))
        invented = 'C2:' + source['id'] + ':' + source['version'] + ':0'
        payload = self.payload([valid])
        claim = deepcopy(payload['judgment']);claim['statement'] = payload['answer']
        payload['judgment'] = {'claims': [deepcopy(claim), {**claim, 'citation_refs': [valid, invented]}]}
        before = current(self.case)
        rejected = self.call('forsic_question', payload)
        self.assertEqual(rejected['error_code'], 'invalid_citation_selection')
        self.assertEqual(rejected['failure_stage'], 'citation_validation')
        self.assertFalse(rejected['write_applied'])
        self.assertEqual(rejected['invalid_fields'][0]['path'], 'judgment.claims[1].citation_refs[1]')
        self.assertNotIn('observed partial output', json.dumps(rejected))
        for key in ('questions', 'claims', 'observations'):
            self.assertEqual(current(self.case)[key], before[key])
        lookup = rejected['recovery']['read_call']
        self.assertEqual(lookup, {'tool': 'forsic_reporting', 'arguments': {
            'action': 'source', 'source_id': source['id'], 'source_version': source['version'],
            'pointer': '/stdout', 'offset': 0}})
        page = self.call(lookup['tool'], lookup['arguments'])
        self.assertNotIn('error', page, page)
        self.assertTrue(page['citation_ref'].startswith('C1:'))
        context = page['acquisition_context']['derivation']
        self.assertEqual(context['exit_code'], 1)
        self.assertTrue(context['output_may_be_generated'])
        payload['judgment']['claims'][1]['citation_refs'][1] = page['citation_ref']
        saved = self.call('forsic_question', payload)
        self.assertNotIn('error', saved, saved)
        observations = current(self.case)['observations']
        selected = next(item for item in observations if item.get('literal') == 'observed partial output\n')
        self.assertEqual(selected['acquisition_context']['derivation']['exit_code'], 1)
        self.assertEqual(self.case.event(source['id'])['data']['execution']['exit_code'], 1)

    def test_unavailable_unreturned_or_stale_failed_source_has_no_output_recovery(self):
        from forsic_plugin.report_driven.host import source_record
        failed = self.failed_shell()
        original = self.case.event(failed['evidence_id'])['data']
        variants = [
            {'status': 'pending'},
            {'execution': {'state': 'completion_unknown', 'exit_code': 1, 'termination_confirmed': False}},
            {'execution': {'state': 'not_started', 'termination_confirmed': True}},
            {'stdout': '', 'stderr': ''},
            {'stdout': None, 'stderr': None},
            {'execution_rejected': True},
            {'started_id': '0' * 32},
        ]
        for index, changes in enumerate(variants):
            with self.subTest(changes=changes):
                data = {**deepcopy(original), **changes}
                eid = self.case.record('tool_result', data, 'synthetic-session')
                source = source_record(self.case.event(eid))
                ref = 'C2:' + eid + ':' + source['version'] + ':0'
                result = self.call('forsic_question', {**self.payload([ref]), 'operation_id': 'unavailable-' + str(index)})
                self.assertEqual(result['error_code'], 'invalid_citation_selection')
                self.assertNotIn('recovery', result)
        for index, ref in enumerate(('C2:' + '0'*32 + ':' + '0'*64 + ':0',
                'C2:' + failed['evidence_id'] + ':' + '0'*64 + ':0')):
            result = self.call('forsic_question', {**self.payload([ref]), 'operation_id': 'bad-source-' + str(index)})
            self.assertEqual(result['error_code'], 'invalid_citation_selection')
            self.assertNotIn('recovery', result)
    def test_revision_and_owner_fences_are_unchanged(self):
        r=self.read();saved=self.call('forsic_question',self.payload([self.ref(r)]));q=find(current(self.case),'questions',saved['question']['question_id'])
        bad=self.payload([self.ref(r)]);bad.update(operation_id='stale',question_id=q['id'],expected_revision=q['revision']+1)
        self.assertIn('error',self.call('forsic_question',bad))
        bad.update(operation_id='foreign',expected_revision=q['revision'])
        out=json.loads(self.case.invoke('forsic_reporting',{'action':'question','payload':bad},'foreign',request_context={'goal_id':'foreign','conversation_id':'foreign'}))
        self.assertIn('error',out)

    def test_optional_test_evaluates_in_one_call_without_preparation(self):
        plan=fixtures.EfficiencyContracts.plan(self)
        m=self.call('forsic_mission',plan)
        call=m['next_call'];result=self.call(call['tool'],call['arguments'])
        q=find(current(self.case),'questions',self.qid)
        p=dict(operation_id='single-assess',mission_id=m['mission']['id'],result_id=result['evidence_id'],
            expected_revision=q['revision'],citation_refs=[self.ref(result)],outcome='inconclusive',
            reasoning_summary='Request only',limitations=['No execution receipt'],next_check='Independent record')
        out=self.call('forsic_assess',p);self.assertNotIn('error',out,out)
        s=current(self.case);checked(s);self.assertEqual(len(s['assessments']),1)
        self.assertEqual(self.call('forsic_assess',p)['receipt'],out['receipt'])
        with self.case.connect() as db:
            starts=[json.loads(row[0]) for row in db.execute("SELECT data FROM events WHERE kind='tool_start'")]
        self.assertFalse(any(x.get('arguments',{}).get('action')=='assessment_basis' for x in starts))
        self.assertEqual(find(s,'questions',self.qid)['answer'],q['answer'])

    def test_native_surface_removes_duplicate_writers_and_preparation(self):
        for tool in ('forsic_question_create','forsic_report','forsic_assessment_basis','forsic_mission_from_results'):
            self.assertNotIn(tool,self.schemas)
        self.assertIn('judgment',self.schemas['forsic_question']['parameters']['properties'])
        self.assertNotIn('basis_receipt_id',self.schemas['forsic_assess']['parameters']['properties'])
        before=current(self.case)['questions']
        out=self.call('forsic_note',{'action':'save','question':'Bypass','answer':'Not allowed'})
        self.assertIn('error',out);self.assertEqual(before,current(self.case)['questions'])

    def test_presented_spans_preserve_native_repeat_detection(self):
        from forsic_plugin.loop_comparison import project
        a=self.read();b=self.read()
        pa=project('forsic_read',{'path':'request.log'},json.dumps(a))
        pb=project('forsic_read',{'path':'request.log'},json.dumps(b))
        self.assertEqual(pa['result'],pb['result'])

    def closure(self, **changes):
        p=self.payload([self.ref(self.read())])
        p.update(work_state='scoped_closed',reopen_conditions=['An independent execution receipt becomes available'],
            gap_dispositions=[dict(gap_text=p['gaps'][0],disposition='assessed_unresolved',
                reason='The retained request is informative but cannot establish execution',reopen_conditions=['New independent record'])])
        p.update(changes)
        return p

    def test_direct_read_to_scoped_answer_with_exact_text_disposition_in_one_transaction(self):
        before=current(self.case);p=self.closure();out=self.call('forsic_question',p)
        self.assertNotIn('error',out,out);s=current(self.case);checked(s)
        q=find(s,'questions',out['question']['question_id']);self.assertEqual(q['work_state'],'scoped_closed')
        self.assertEqual(q['revision'],1);self.assertTrue(q['claim_refs'])
        gs=[g for g in s['gaps'] if g['question_ref']['id']==q['id']]
        self.assertEqual(len(gs),1);self.assertEqual(gs[0]['original_obligation'],p['gaps'][0])
        self.assertEqual(gs[0]['disposition'],'assessed_unresolved');self.assertEqual(gs[0]['feasible_next_action'],'none')
        for field in ('missions','tests','assessments'):self.assertEqual(s[field],before[field])
        self.assertEqual(self.call('forsic_question',p)['question'],out['question'])

    def test_new_closure_rejects_invented_ambiguous_or_missing_gap_selection_atomically(self):
        for replacement in ([],[dict(gap_id='invented')],[dict(gap_text='different')],
                            [dict(gap_text='Independent execution receipt not acquired',gap_id='invented')]):
            with self.subTest(replacement=replacement):
                p=self.closure()
                p['gap_dispositions']=[{**p['gap_dispositions'][0],**v} for v in replacement]
                if replacement and 'gap_id' in replacement[0] and 'gap_text' not in replacement[0]:
                    p['gap_dispositions'][0].pop('gap_text')
                before=current(self.case);out=self.call('forsic_question',p);self.assertIn('error',out,out)
                after=current(self.case)
                for field in ('questions','claims','observations','gaps'):self.assertEqual(before[field],after[field])

    def test_gap_selector_contract_matches_sanitized_native_admission(self):
        from jsonschema import Draft202012Validator
        from tools.schema_sanitizer import sanitize_tool_schemas
        from forsic_plugin.report_driven.investigation_state import input_problems
        pin=sanitize_tool_schemas([{'type':'function','function':deepcopy(self.schemas['forsic_question'])}])[0]['function']['parameters']
        for selectors in ({}, {'gap_id':'G-explicit','gap_text':'Both selected'},
                          {'gap_id':''}, {'gap_text':''}, {'gap_id':'G-explicit'}, {'gap_text':'Exact gap text'}):
            p={'operation_id':'selector-contract','gap_dispositions':[dict(disposition='assessed_unresolved',
                reason='No supported discriminator',reopen_conditions=['New record'],**selectors)]}
            valid=len(selectors)==1 and bool(next(iter(selectors.values())))
            with self.subTest(selectors=selectors):
                self.assertEqual(not list(Draft202012Validator(pin).iter_errors(p)),valid)
                self.assertEqual(not input_problems(p,pin),valid)
        p=self.closure();p['gap_dispositions'][0].pop('gap_text');before=current(self.case)
        out=self.call('forsic_question',p)
        self.assertIn('error',out);self.assertIn('gap_dispositions[0]',out['error'])
        self.assertIn('gap_id',out['error']);self.assertIn('gap_text',out['error'])
        after=current(self.case)
        for field in ('questions','claims','observations','gaps'):self.assertEqual(before[field],after[field])
        p['gap_dispositions'][0]['gap_text']=p['gaps'][0]
        self.assertNotIn('error',self.call('forsic_question',p))

    def test_no_synthetic_gap_for_grounded_closed_answer_without_declared_gaps(self):
        p=self.closure(gaps=[],gap_dispositions=[]);out=self.call('forsic_question',p)
        self.assertNotIn('error',out,out);s=current(self.case);checked(s)
        self.assertFalse([g for g in s['gaps'] if g['question_ref']['id']==out['question']['question_id']])

    def test_update_cannot_add_unaccounted_gap_while_closing(self):
        out=self.call('forsic_question',self.payload([self.ref(self.read())]));q=find(current(self.case),'questions',out['question']['question_id'])
        p=self.closure(question_id=q['id'],expected_revision=q['revision'],operation_id='update-close')
        p['gaps'].append('A second material gap')
        self.assertIn('error',self.call('forsic_question',p))
        p['gap_dispositions'].append(dict(gap_text=p['gaps'][1],disposition='assessed_unresolved',reason='Explicit remaining limit',reopen_conditions=['New records']))
        out=self.call('forsic_question',p);self.assertNotIn('error',out,out)
        s=current(self.case);checked(s)
        self.assertTrue(all(g['disposition']=='assessed_unresolved' for g in s['gaps'] if g['question_ref']['id']==q['id']))

    def test_historical_generated_gap_is_preserved_after_grounded_update(self):
        q=find(current(self.case),'questions',self.qid);before=list(q['remaining_gap_ids'])
        p=self.payload([self.ref(self.read())]);p.update(question_id=q['id'],expected_revision=q['revision'],operation_id='legacy-update')
        p.pop('gaps');p.pop('question');p.pop('scope')
        out=self.call('forsic_question',p);self.assertNotIn('error',out,out)
        q=find(current(self.case),'questions',self.qid)
        self.assertEqual(q['remaining_gap_ids'],before)


class CitationSelectionViewTests(unittest.TestCase):
    setUp = GroundedJudgmentTests.setUp
    call = GroundedJudgmentTests.call
    read = GroundedJudgmentTests.read
    payload = GroundedJudgmentTests.payload
    ref = GroundedJudgmentTests.ref
    def test_preview_is_exact_selected_field_not_whole_report_or_semantic_approval(self):
        (self.case.root/'banner.log').write_text('==== HEADER ====\nRequested '+('detail '*40)+'\nOutcome not recorded\n')
        r=self.read('banner.log')
        first,second=r['presented_spans'][:2]
        self.assertEqual(first['text_preview'],'==== HEADER ====')
        self.assertTrue(first['preview_complete'])
        self.assertFalse(second['preview_complete'])
        from forsic_plugin.report_driven.grounded_judgment import selection
        selected=selection(self.case,current(self.case),second['citation_ref'])
        self.assertTrue(selected['literal'].startswith(second['text_preview']))
        self.assertGreater(len(selected['literal']),len(second['text_preview']))
        out=self.call('forsic_question',self.payload([second['citation_ref']]))
        lookup=out['question']['gap_lookup']
        self.assertEqual(lookup['arguments']['question_id'],out['question']['question_id'])
        self.assertFalse(out['question']['semantic_approval'])
        gaps=self.call(lookup['tool'],lookup['arguments']);self.assertNotIn('error',gaps,gaps)
        self.assertEqual(gaps['gap_scope']['mode'],'explicit_question')

if __name__=='__main__':unittest.main()
