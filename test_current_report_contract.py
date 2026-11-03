"""Synthetic current-answer reconciliation and precise authoring dependencies."""
from copy import deepcopy
import unittest

import test_grounded_judgment as grounded
import test_report_narrative as authored
from forsic_plugin.report_driven.current_judgment import current_judgment
from forsic_plugin.report_driven.host import current, find, invoke
from forsic_plugin.report_driven.narrative import basis, material, status


class CurrentReportContractTests(unittest.TestCase):
    setUp = grounded.GroundedJudgmentTests.setUp
    call = grounded.GroundedJudgmentTests.call
    read = grounded.GroundedJudgmentTests.read
    ref = grounded.GroundedJudgmentTests.ref
    payload = grounded.GroundedJudgmentTests.payload
    failed_shell = grounded.GroundedJudgmentTests.failed_shell

    def reconcile(self, before, after, residual, *, failed=False):
        if failed:
            result = self.failed_shell()
            source = next(s for s in current(self.case)['sources'] if s['id'] == result['evidence_id'])
            page = self.call('forsic_reporting', {'action': 'source', 'source_id': source['id'],
                'source_version': source['version'], 'pointer': '/stdout'})
            first = page['citation_ref']
        else:
            (self.case.root/'initial.log').write_text(before+'\n')
            first = self.ref(self.read('initial.log'))
        old_gap = 'Earlier scoped result requires reconciliation'
        p = self.payload([first])
        p.update(gaps=[old_gap, residual], critical_gaps=[old_gap, residual], answer=before)
        p['judgment']['assumptions'] = ['Earlier scope only']
        p['judgment']['limitations'] = [old_gap, residual]
        saved = self.call('forsic_question', p)
        self.assertNotIn('error', saved, saved)
        qid = saved['question']['question_id']
        old = find(current(self.case), 'questions', qid)
        old_claim = old['claim_refs'][0]['id']
        old_scope = next(g for g in current(self.case)['gaps']
                         if g['question_ref']['id'] == qid and g['original_obligation'] == old_gap)
        (self.case.root/'later.log').write_text(after+'\n')
        later = self.read('later.log')
        # Merely reading a successful later result cannot decide its entailment
        # or whether it supersedes this question's earlier acquisition limit.
        self.assertEqual(find(current(self.case), 'questions', qid)['answer'], before)
        replacement = self.payload([self.ref(later)])
        replacement.update(operation_id='reconciled', question_id=qid,
            expected_revision=old['revision'], answer=after,
            gap_dispositions=[{'gap_id': old_scope['id'], 'disposition': 'resolved',
                'reason': 'Analyst compared both retained scopes and adopts the later result for this obligation',
                'reopen_conditions': ['Contradictory retained result']}])
        replacement.pop('gaps')  # old note text remains as historical compatibility data
        replacement['judgment']['assumptions'] = []
        replacement['judgment']['limitations'] = [residual]
        corrected = self.call('forsic_question', replacement)
        self.assertNotIn('error', corrected, corrected)
        state = current(self.case)
        q = find(state, 'questions', qid)
        projection = current_judgment(state, q)
        self.assertEqual(projection['answer'], after)
        self.assertEqual(projection['judgments'][0]['limitations'], [residual])
        self.assertEqual(projection['judgments'][0]['assumptions'], [])
        self.assertEqual([g['original_obligation'] for g in projection['remaining_gaps']], [residual])
        self.assertEqual(q['remaining_gap_ids'], [g['id'] for g in projection['remaining_gaps']])
        self.assertNotIn(old_gap, state['status']['explicit_limitations'])
        self.assertIn(residual, state['status']['explicit_limitations'])
        self.assertEqual(projection['resolved_gaps'][0]['id'], old_scope['id'])
        self.assertEqual(find(state, 'claims', old_claim)['status'], 'superseded')
        packet = material(basis(state, [qid])[1])
        context = next(i for i in packet if i['kind'] == 'question')
        self.assertNotIn(old_scope['id'], context['remaining_gap_ids'])
        self.assertEqual(context['current_judgment']['judgments'][0]['statement'], after)
        self.assertFalse(any(i['kind'] == 'claim' and i['id'] == old_claim for i in packet))
        self.assertTrue(any(g['id'] == old_scope['id'] for g in state['gaps']))
        from forsic_plugin.report_driven.investigation_state import latest_note
        with self.case.connect() as db:
            retained_note = latest_note(db, qid.removeprefix('Q-'))
        self.assertIn(old_gap, retained_note['gaps'])
        if failed:
            self.assertTrue(self.case.event(result['evidence_id'])['data']['error'])

    def test_failed_acquisition_then_success_retains_search_coverage_limit(self):
        self.reconcile('Extraction failed for the selected member',
            'The selected member was subsequently extracted',
            'Search coverage and communication absence remain unestablished', failed=True)

    def test_partial_then_full_keeps_unrelated_attribution_limit(self):
        self.reconcile('Only the initial byte range was scanned',
            'The analyst reviewed the full selected byte range',
            'Actor attribution remains unresolved')

    def test_previously_unknown_field_can_be_replaced_without_changing_other_limits(self):
        self.reconcile('The selected field value was unknown',
            'The selected field value is recorded as 73',
            'The field does not establish process identity or successful execution')

    def test_rereading_originals_does_not_change_current_judgment_or_add_support(self):
        first = self.read()
        saved = self.call('forsic_question', self.payload([self.ref(first)]))
        qid = saved['question']['question_id']
        before = current_judgment(current(self.case), find(current(self.case), 'questions', qid))
        self.read()
        after = current_judgment(current(self.case), find(current(self.case), 'questions', qid))
        self.assertEqual(before, after)
        self.assertEqual(len(after['judgments'][0]['source_refs']), 1)


class AuthoringDependencyContractTests(unittest.TestCase):
    # Reuse the existing offline authoring fixture, not its inherited test suite.
    setUp = authored.ReportNarrativeTests.setUp
    tearDown = authored.ReportNarrativeTests.tearDown
    draft = authored.ReportNarrativeTests.draft
    write = authored.ReportNarrativeTests.write
    review = authored.ReportNarrativeTests.review
    def test_direct_source_only_entry_is_stale_after_own_answer_or_limits_change(self):
        state = current(self.case)
        items = material(basis(state, [state['questions'][0]['id']])[1])
        source = next(item for item in items if item['kind'] == 'source')
        self.write('scope', keys=[source['key']])
        for mutation in ('answer', 'limit', 'gap'):
            changed = deepcopy(current(self.case))
            if mutation == 'answer':
                changed['questions'][0]['answer'] = 'A materially revised answer'
            elif mutation == 'limit':
                claim_id = changed['questions'][0]['claim_refs'][0]['id']
                find(changed, 'claims', claim_id)['limitations'] = ['A newly applicable limit']
            else:
                changed['gaps'][0]['disposition'] = 'resolved'
            with self.subTest(mutation=mutation):
                self.assertIn('scope', status(changed)['stale_fields'])

    def test_exact_header_values_and_unrelated_questions_keep_their_dependencies(self):
        state = current(self.case)
        items = material(basis(state, [state['questions'][0]['id']])[1])
        source = next(item for item in items if item['kind'] == 'source')
        self.write('report_identity', keys=[source['key']])
        self.write('scope', keys=[source['key']])
        changed = deepcopy(current(self.case))
        changed['questions'][0]['answer'] += ' A materially changed answer'
        progress = status(changed)
        self.assertIn('report_identity', progress['fields'])
        self.assertIn('scope', progress['stale_fields'])
        unrelated = deepcopy(current(self.case))
        other = deepcopy(unrelated['questions'][0])
        other.update(id='Q-unrelated', answer='Unrelated answer', claim_refs=[], remaining_gap_ids=[])
        unrelated['questions'].append(other)
        self.assertIn('scope', status(unrelated, [state['questions'][0]['id']])['fields'])

    def test_receipts_and_question_write_revision_are_not_semantic_dependencies(self):
        self.write('scope', keys=['Q1'])
        changed = deepcopy(current(self.case))
        question = changed['questions'][0]
        question['revision'] += 1
        question['version'] = 'another-write-version'
        question['review_responses'] = [{'response': 'Retained after review'}]
        question['work_state'] = 'active'
        question['closure_rationale'] = 'A work disposition receipt'
        self.assertIn('scope', status(changed)['fields'])

    def test_new_local_assessment_is_exposed_without_automatically_changing_the_answer(self):
        from test_report_driven import proposal
        self.write('scope', keys=['Q1'])
        before = current(self.case)
        p = proposal(before, self.case)
        p.pop('answer', None)
        p['reasoning_summary'] = 'A new scoped local comparison; aggregate entailment requires analyst reconciliation'
        invoke(self.case, {'action':'assess', 'snapshot_id':before['meta']['snapshot_id'], 'payload':p})
        after = current(self.case)
        self.assertEqual(before['questions'][0]['answer'], after['questions'][0]['answer'])
        self.assertIn('scope', status(after)['stale_fields'])
        item = next(i for i in material(basis(after, [after['questions'][0]['id']])[1]) if i['kind']=='question')
        self.assertIn(p['reasoning_summary'], str(item['current_judgment']['local_assessments']))
        self.assertIn('result_scope', item['current_judgment']['local_assessments'][0])

    def test_large_authoring_material_pages_the_same_current_projection(self):
        from forsic_plugin.report_driven.narrative import prepare
        state = current(self.case)
        state['questions'][0]['answer'] = 'A long qualified answer. ' * 1000
        packet = prepare(state, {'section':'scope'}, self.case)
        question = next(i for i in packet['items'] if i['kind']=='question')
        self.assertTrue(question['body_omitted'])
        self.assertEqual(question['read_call']['arguments']['pointer'], '/material/0')
        page = prepare(state, question['read_call']['arguments'], self.case)
        fields = list(page['items'])
        while page.get('next_call'):
            page = prepare(state, page['next_call']['arguments'], self.case)
            fields.extend(page['items'])
        projection = next(i for i in fields if i['key']=='current_judgment')
        detail = prepare(state, projection['read_call']['arguments'], self.case)
        self.assertEqual(detail['pointer'], '/material/0/current_judgment')

    def test_exact_cell_support_is_not_pooled_across_distinct_cells(self):
        state = current(self.case)
        items = material(basis(state, [state['questions'][0]['id']])[1])
        observations = [i for i in items if i['kind'] == 'observation']
        a, b = observations[:2]
        packet = invoke(self.case, {'action': 'compose', 'section': 'key_evidence'})
        draft = {'entries': [{'node_id': 'key_evidence.selected', 'cells': ['Record A', 'Record B', 'Link unresolved'],
            'basis_keys': ['Q1', a['key'], b['key']],
            'cell_basis_keys': [[a['key']], [b['key']], []], 'status': 'saved'}]}
        before = self.case.db.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Each saved value requires its own selected support'):
            self.write('key_evidence', draft=draft)
        self.assertEqual(before, self.case.db.read_bytes())
        gap = next(item for item in items if item['kind'] == 'gap')
        draft['entries'][0]['basis_keys'].append(gap['key'])
        draft['entries'][0]['cell_basis_keys'][2] = [gap['key']]
        self.write('key_evidence', draft=draft)
        entry = status(current(self.case))['fields']['key_evidence']['entries'][0]
        self.assertEqual(entry['cell_citation_refs'], [a['citation_refs'], b['citation_refs'], gap['citation_refs']])
        for group in authored.WRITING_ORDER:
            if group != 'key_evidence':
                self.write(group)
        self.review()
        from forsic_plugin.report_driven.narrative import project_body
        pages = project_body(current(self.case))
        table = next(block for page in pages for block in page['blocks'] if block.get('region') == 'key_evidence')
        self.assertEqual(table['cell_citation_refs'][0], [[], a['citation_refs'], b['citation_refs'], gap['citation_refs']])
        self.assertTrue(packet['save_contract'])

    def test_cell_selection_rejects_foreign_support_or_wrong_cell_count_atomically(self):
        packet = invoke(self.case, {'action': 'compose', 'section': 'key_evidence'})
        draft = self.draft('key_evidence', packet, keys=['Q1'])
        before = self.case.db.read_bytes()
        for selections in ([['Q1']], [['Q1'], ['C1'], []]):
            bad = deepcopy(draft)
            bad['entries'][0]['cell_basis_keys'] = selections
            with self.assertRaisesRegex(ValueError, 'cell_basis_keys'):
                self.write('key_evidence', draft=bad)
            self.assertEqual(self.case.db.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
