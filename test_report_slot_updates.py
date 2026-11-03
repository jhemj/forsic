"""Synthetic partial report declarations through the existing atomic writer."""
from copy import deepcopy
import json
import unittest

import test_report_slots as fixtures
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.investigation_state import compact_input_schemas


class ReportSlotUpdatesTests(unittest.TestCase):
    setUp = fixtures.ReportSlotsTests.setUp
    call = fixtures.ReportSlotsTests.call
    save = fixtures.ReportSlotsTests.save
    judgment = fixtures.ReportSlotsTests.judgment
    revise = fixtures.ReportSlotsTests.revise
    row = fixtures.ReportSlotsTests.row
    counts = fixtures.ReportSlotsTests.counts
    entries = fixtures.ReportSlotsTests.entries

    def grounded(self):
        return self.save(judgment=self.judgment(assumptions=['Selected request identity'],
            limitations=['No completion record']), reason='Retain exact request', report_slots=[
                self.row(), self.row(slot='attacker_actions', limitations=['Editorial uncertainty']),
                self.row(slot='decisive_evidence')])

    def test_partial_preserves_unmentioned_rows_exactly_and_upserts_missing_slot(self):
        q = self.grounded(); frozen = deepcopy(q['report_slots'])
        q = self.revise(q, 'partial', report_slot_updates=[self.row(brief='Corrected route scope')])
        self.assertEqual(q['report_slots'][1:], frozen[1:])
        self.assertEqual(q['report_slots'][0]['brief'], 'Corrected route scope')
        self.assertNotIn('_report_slot_updates', q)
        self.assertNotIn('report_slot_updates', q)
        q = self.revise(q, 'upsert', report_slot_updates=[self.row(slot='compromise_time')])
        self.assertEqual(len(q['report_slots']), 4)
        self.assertEqual(q['report_slots'][1:3], frozen[1:])
        self.assertTrue(all(not self.entries(key=r['slot'])[0]['needs_recheck'] for r in q['report_slots']))
        host.checked(host.current(self.case))

    def test_invalid_partial_or_mixed_payloads_leave_all_events_unchanged(self):
        q = self.grounded(); before = host.current(self.case); counts = self.counts()
        cases = [dict(report_slot_updates=[]), dict(report_slot_updates=[self.row()]*8),
            dict(report_slot_updates=[self.row(), self.row()]),
            dict(report_slot_updates=[self.row(slot='unknown')]),
            dict(report_slot_updates=[self.row(claim_indices=[1])]),
            dict(report_slot_updates=[self.row(claim_indices=[True])]),
            dict(report_slot_updates=[self.row(claim_indices=[0, 0])]),
            dict(report_slot_updates=[self.row(claim_indices=[])]),
            dict(report_slot_updates=[self.row(judgment_basis={})]),
            dict(report_slot_updates=[self.row(state='unavailable', basis=' ')]),
            dict(report_slot_updates=[self.row()], report_slots=[]),
            dict(report_slot_updates=[self.row()], injected=True),
            # Failure after provisional replacement claims must roll them back too.
            dict(report_slot_updates=[self.row(claim_indices=[9])],
                 answer='Corrected answer', judgment=self.judgment(), reason='Replace judgment')]
        for i, changes in enumerate(cases):
            with self.subTest(i=i):
                result = self.call('forsic_question', dict(operation_id='invalid-partial-'+str(i),
                    question_id=q['id'], expected_revision=q['revision'], **changes))
                self.assertIn('error', result, result)
                self.assertEqual(self.counts(), counts)
                for field in ('questions', 'claims', 'observations'):
                    self.assertEqual(host.current(self.case)[field], before[field])

    def test_cas_foreign_owner_and_identical_retry_use_existing_transaction(self):
        q = self.grounded()
        payload = dict(operation_id='partial-once', question_id=q['id'], expected_revision=q['revision'],
                       report_slot_updates=[self.row(brief='Revised route')])
        first = self.call('forsic_question', payload); self.assertNotIn('error', first, first)
        counts = self.counts(); state = host.current(self.case)
        replay = self.call('forsic_question', deepcopy(payload))
        self.assertNotIn('error', replay, replay)
        self.assertEqual(replay['question'], first['question'])
        stale = self.call('forsic_question', {**payload, 'operation_id': 'partial-stale'})
        self.assertIn('revision conflict', stale['error'].lower())
        conflict = self.call('forsic_question', {**payload, 'report_slot_updates': [self.row(brief='Different')]})
        self.assertIn('error', conflict)
        for context in (dict(conversation_id='foreign', goal_id='synthetic-goal'),
                        dict(conversation_id='synthetic-session', goal_id='foreign-goal')):
            result = json.loads(self.case.invoke('forsic_reporting', {'action': 'question', 'payload': {
                **payload, 'operation_id': 'foreign-'+context['goal_id']+context['conversation_id'],
                'expected_revision': q['revision']+1}}, context['conversation_id'],
                request_context={'question_contract_version': 'v2', **context}))
            self.assertIn('another native', result['error'])
        self.assertEqual(self.counts(), counts)
        self.assertEqual(host.current(self.case)['questions'], state['questions'])

    def test_replacement_judgment_binds_only_changed_slot_and_leaves_others_stale(self):
        q = self.grounded(); frozen = deepcopy(q['report_slots'])
        q = self.revise(q, 'replace-and-partial', answer='A request exists; execution is uncertain.',
            judgment={'claims': [self.judgment(statement='A request is recorded, with execution unproved.')]},
            reason='Correct current claim', report_slot_updates=[self.row(brief='Updated route')])
        self.assertEqual(q['report_slots'][1:], frozen[1:])
        self.assertEqual(q['report_slots'][0]['claim_refs'], q['claim_refs'])
        self.assertNotEqual(q['report_slots'][0]['claim_refs'], frozen[0]['claim_refs'])
        self.assertFalse(self.entries()[0]['needs_recheck'])
        self.assertTrue(self.entries(key='attacker_actions')[0]['needs_recheck'])
        self.assertTrue(self.entries(key='decisive_evidence')[0]['needs_recheck'])
        host.checked(host.current(self.case))

    def test_answer_only_edit_rebinds_explicit_unavailable_slot_leaving_others_stale(self):
        q = self.grounded(); frozen = deepcopy(q['report_slots'])
        q = self.revise(q, 'answer-partial', answer='Execution remains unknown in this scope.',
            reason='Narrow answer', report_slot_updates=[self.row(state='unavailable', claim_indices=[],
                basis='No execution record acquired')])
        self.assertEqual(q['report_slots'][1:], frozen[1:])
        self.assertFalse(self.entries()[0]['needs_recheck'])
        self.assertTrue(self.entries(key='attacker_actions')[0]['needs_recheck'])

    def test_full_replace_and_clear_semantics_remain_available(self):
        q = self.grounded()
        q = self.revise(q, 'full-replace', report_slots=[self.row(slot='compromise_time')])
        self.assertEqual([r['slot'] for r in q['report_slots']], ['compromise_time'])
        q = self.revise(q, 'clear', report_slots=[])
        self.assertEqual(q['report_slots'], [])
        q = self.revise(q, 'upsert-empty', report_slot_updates=[self.row()])
        self.assertEqual([r['slot'] for r in q['report_slots']], ['entry_route'])

    def test_new_question_and_v1_cannot_use_partial_updates(self):
        counts = self.counts()
        result = self.call('forsic_question', dict(operation_id='new-partial', question='Question?',
            scope='Synthetic source', answer='Unknown', report_slot_updates=[self.row()]))
        self.assertIn('existing question', result['error'])
        self.assertEqual(self.counts(), counts)
        self.assertNotIn('report_slot_updates', compact_input_schemas('v1')['question']['properties'])
        result = json.loads(self.case.invoke('forsic_reporting', {'action': 'question', 'payload': {
            'operation_id': 'v1-partial', 'question': 'Question?', 'scope': 'Synthetic source',
            'answer': 'Unknown', 'report_slot_updates': [self.row()]}}, 'synthetic-session',
            request_context={'question_contract_version': 'v1', 'goal_id': 'synthetic-goal',
                             'conversation_id': 'synthetic-session'}))
        self.assertIn('error', result)
        self.assertEqual(self.counts(), counts)


if __name__ == '__main__':
    unittest.main()
