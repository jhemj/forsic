"""Synthetic atomic writer and frozen report bindings; no model/network calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / 'upstream' / 'hermes')]

import test_question_v2 as fixtures
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.current_judgment import current_judgment
from forsic_plugin.report_driven.report_slots import SLOTS, project_report_slots


class ReportSlotsTests(unittest.TestCase):
    setUp = fixtures.QuestionV2Tests.setUp
    call = fixtures.QuestionV2Tests.call
    save = fixtures.QuestionV2Tests.save
    judgment = fixtures.QuestionV2Tests.judgment
    revise = fixtures.QuestionV2Tests.revise

    def row(self, **changes):
        row = dict(slot='entry_route', state='inferred', brief='The acquired record describes a request; execution remains uncertain.',
                   claim_indices=[0])
        row.update(changes)
        return row

    def grounded(self, op='grounded', **changes):
        return self.save(op, judgment=self.judgment(), reason='Keep exact request record',
                         report_slots=[self.row()], **changes)

    def entries(self, state=None, key='entry_route'):
        return next(s for s in project_report_slots(state or host.current(self.case))['slots'] if s['slot'] == key)['entries']

    def counts(self):
        with self.case.connect() as db:
            return {kind: db.execute('SELECT count(*) FROM events WHERE kind=?', (kind,)).fetchone()[0]
                    for kind in ('note', 'report_change', 'mutation_receipt')}

    def test_same_transaction_binds_replacement_claim_indices_and_preserves_uncertainty(self):
        first = self.judgment(assumptions=['Record belongs to the selected job'],
                              limitations=['No completion receipt'], inference_strength='plausible')
        second = self.judgment('process.log', statement='Process creation is recorded.',
                               limitations=['Creation alone does not establish success'])
        first['statement'] = 'A job request was recorded.'
        q = self.save(judgment={'claims': [first, second]}, reason='Separate selected claims',
                      report_slots=[self.row(claim_indices=[1])])
        state = host.current(self.case); host.checked(state)
        entry = self.entries(state)[0]
        self.assertEqual(entry['claim_refs'], [q['claim_refs'][1]])
        self.assertEqual(entry['claim_details'][0]['statement'], second['statement'])
        self.assertIn(second['limitations'][0], entry['limitations'])
        self.assertFalse(entry['needs_recheck'])
        self.assertFalse(entry['semantic_approval'])
        q = self.revise(q, 'use-first', report_slots=[self.row(claim_indices=[0])])
        entry = self.entries()[0]
        self.assertEqual(entry['assumptions'], first['assumptions'])
        self.assertEqual(entry['limitations'], first['limitations'])
        self.assertEqual(entry['claim_details'][0]['inference_strength'], 'plausible')

    def test_counterevidence_and_declared_limits_are_retained_separately(self):
        counter = self.judgment('process.log')['citation_refs']
        q = self.save(judgment=self.judgment(counter_citation_refs=counter, assumptions=['Unverified event identity'],
                                             limitations=['Only two records acquired']),
                      reason='Keep strongest counterpoint', report_slots=[self.row(limitations=['Editorial caution'])])
        entry = self.entries()[0]
        self.assertTrue(entry['counterevidence_refs'])
        self.assertTrue(entry['evidence_refs'])
        self.assertEqual(entry['limitations'], ['Editorial caution', 'Only two records acquired'])
        self.assertEqual(entry['assumptions'], ['Unverified event identity'])
        self.assertTrue(q['claim_refs'])

    def test_invalid_selection_rolls_back_new_note_claims_and_operation_receipt(self):
        judgment = self.judgment(); before = host.current(self.case); counts = self.counts()
        result = self.call('forsic_question', dict(operation_id='bad-binding', question='Was the job executed?',
            scope='Acquired synthetic records', answer='Execution is unknown.', reason='Retain observed request',
            judgment=judgment, report_slots=[self.row(claim_indices=[1])]))
        self.assertIn('error', result)
        self.assertEqual(counts, self.counts())
        after = host.current(self.case)
        for field in ('questions', 'claims', 'observations'):
            self.assertEqual(after[field], before[field])

    def test_cas_ownership_and_operation_replay_share_existing_writer(self):
        q = self.grounded(); payload = dict(operation_id='edit-report', question_id=q['id'],
            expected_revision=q['revision'], report_slots=[self.row(brief='Request observed; completion remains unproved.')])
        first = self.call('forsic_question', payload); self.assertNotIn('error', first, first)
        counts = self.counts(); state = host.current(self.case)
        self.assertNotIn('error', self.call('forsic_question', deepcopy(payload)))
        self.assertEqual(counts, self.counts())
        conflicting = self.call('forsic_question', {**payload, 'report_slots': []})
        self.assertIn('error', conflicting)
        stale = self.call('forsic_question', {**payload, 'operation_id': 'stale-report'})
        self.assertIn('revision conflict', stale['error'].lower())
        foreign = json.loads(self.case.invoke('forsic_reporting', {'action': 'question', 'payload': {
            **payload, 'operation_id': 'foreign-report', 'expected_revision': q['revision'] + 1}}, 'foreign',
            request_context={'question_contract_version': 'v2', 'goal_id': 'foreign-goal', 'conversation_id': 'foreign'}))
        self.assertIn('another native', foreign['error'])
        self.assertEqual(state['questions'], host.current(self.case)['questions'])
        self.assertEqual(counts, self.counts())

    def test_answer_scope_and_claim_changes_keep_frozen_declaration_visibly_stale(self):
        for field, changes in (
            ('answer', {'answer': 'Completion is still not established by this record.'}),
            ('scope', {'scope': 'Only the selected request record'}),
            ('claim', {'answer': 'A request exists; success remains unknown.', 'judgment': self.judgment()})):
            with self.subTest(field=field):
                q = self.grounded('grounded-' + field)
                frozen = deepcopy(q['report_slots'])
                self.revise(q, 'changed-' + field, reason='Correct the judgment basis', **changes)
                state = host.current(self.case); host.checked(state)
                current = host.find(state, 'questions', q['id'])
                self.assertEqual(current['report_slots'], frozen)
                entry = next(e for e in self.entries(state) if e['question_id'] == q['id'])
                self.assertTrue(entry['needs_recheck'])
                self.assertEqual(entry['state'], 'inferred')

    def test_reporting_only_edit_preserves_current_judgment_and_review_state(self):
        q = self.grounded()
        # A historical disposition fixture verifies that report editing cannot
        # silently invalidate already recorded review handling.
        with self.case.connect() as db:
            raw = json.loads(db.execute("SELECT data FROM events WHERE kind='note' AND json_extract(data,'$.note_id')=? ORDER BY rowid DESC LIMIT 1", (q['id'][2:],)).fetchone()[0])
        raw['investigation']['review_responses'] = [dict(review_id='synthetic-review', finding_id='synthetic-finding',
            disposition='scope_limited', reason='The retained request does not establish completion',
            limitation='Only the acquired request scope is explained', citation_refs=[],
            question_definition_version=q['definition_version'], semantic_approval=False)]
        self.case.record('note', raw, 'synthetic-session')
        q = host.find(host.current(self.case), 'questions', q['id'])
        old = deepcopy(q); before = current_judgment(host.current(self.case), q)
        q = self.revise(q, 'report-only', report_slots=[self.row(state='not_confirmed', brief='The acquired record does not establish execution.')])
        after = current_judgment(host.current(self.case), q)
        before.pop('revision'); after.pop('revision')
        self.assertEqual(before, after)
        for key in ('assessment', 'judgment_review_required', 'claim_refs', 'review_responses'):
            self.assertEqual(old[key], q[key])
        self.assertFalse(self.entries()[0]['needs_recheck'])
        self.assertTrue(q['claim_refs'])

    def test_omitted_slots_survive_followup_bookkeeping_without_recheck(self):
        q = self.grounded(); original = deepcopy(q['report_slots'])
        q = self.revise(q, 'followup-bookkeeping', next_checks=['Compare an independent completion receipt'],
                        priority='decision_critical')
        self.assertEqual(q['report_slots'], original)
        self.assertFalse(self.entries()[0]['needs_recheck'])
        q = self.revise(q, 'clear-report-declaration', report_slots=[])
        self.assertEqual(q['report_slots'], [])
        self.assertEqual(self.entries(), [])
        self.assertTrue(q['claim_refs'])

    def test_missing_is_editorial_and_legacy_notes_have_all_seven_empty_axes(self):
        projection = project_report_slots(host.current(self.case))
        self.assertEqual([r['slot'] for r in projection['slots']], [key for key, _ in SLOTS])
        self.assertTrue(all(r['missing'] and not r['entries'] for r in projection['slots']))
        self.assertEqual(projection['snapshot_id'], host.current(self.case)['meta']['snapshot_id'])

    def test_explicit_states_never_derive_from_assessment_or_brief_keywords(self):
        q = self.grounded(); self.assertEqual(self.entries()[0]['state'], 'inferred')
        for index, chosen in enumerate(('confirmed', 'not_confirmed', 'unavailable', 'not_applicable')):
            q = self.revise(q, 'state-' + str(index), assessment='supported', report_slots=[
                self.row(state=chosen, basis='Analyst-selected limited source scope', brief='No record establishes an attacker.')])
            self.assertEqual(self.entries()[0]['state'], chosen)
            self.assertFalse(self.entries()[0]['semantic_approval'])

    def test_unknown_na_need_basis_but_no_claims_and_each_question_keeps_scope(self):
        unknown = self.row(state='unavailable', claim_indices=[], basis='No completion source was provided')
        unknown.pop('claim_indices')
        first = self.save('unknown', report_slots=[unknown])
        second = self.save('outside', scope='Separate excluded target', report_slots=[self.row(
            state='not_applicable', claim_indices=[], basis='This target is excluded by the agreed question scope')])
        entries = self.entries()
        self.assertEqual({e['question_id'] for e in entries}, {first['id'], second['id']})
        self.assertEqual({e['scope'] for e in entries}, {first['scope'], second['scope']})
        self.assertTrue(all(not e['claim_refs'] and not e['needs_recheck'] for e in entries))

    def test_unknown_duplicate_oversized_or_injected_fields_reject_atomically(self):
        q = self.grounded(); before = host.current(self.case); counts = self.counts()
        invalid = [
            [self.row(slot='invented')], [self.row(), self.row()],
            [self.row(brief='x' * 701)], [self.row(state='unavailable', basis='  ')],
            [self.row(state='not_applicable')], [self.row(claim_indices=[])],
            [self.row(claim_indices=[0, 0])], [self.row(claim_indices=[True])],
            [self.row(claim_refs=q['claim_refs'])], [self.row(scope='Injected scope')],
            [self.row(judgment_basis={})],
        ]
        for index, rows in enumerate(invalid):
            with self.subTest(index=index):
                out = self.call('forsic_question', dict(operation_id='bad-' + str(index), question_id=q['id'],
                    expected_revision=q['revision'], report_slots=rows))
                self.assertIn('error', out, out)
                self.assertEqual(host.current(self.case)['questions'], before['questions'])
                self.assertEqual(counts, self.counts())

    def test_source_change_requires_recheck_without_mutating_frozen_refs(self):
        self.grounded(); state = host.current(self.case); original = deepcopy(self.entries(state)[0])
        chosen = next(r for r in original['evidence_refs'] if r['kind'] == 'source')
        host.find(state, 'sources', chosen['id'])['version'] = 'changed-source'
        entry = self.entries(state)[0]
        self.assertTrue(entry['needs_recheck'])
        self.assertEqual(entry['evidence_refs'], original['evidence_refs'])

    def test_synthetic_retained_source_correction_survives_global_audit_as_local_recheck(self):
        q = self.grounded(); frozen = deepcopy(q['report_slots'])
        source = next(r for r in frozen[0]['evidence_refs'] if r['kind'] == 'source')
        with self.case.connect() as db:
            event = self.case.event(source['id'])
            revised = deepcopy(event['data'])
            revised['lines'][0]['text'] = 'job=maintenance requested target=/opt/revised-task'
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(revised), source['id']))
        state = host.current(self.case); host.checked(state)
        actual = host.find(state, 'questions', q['id'])
        self.assertEqual(actual['report_slots'], frozen)
        entry = self.entries(state)[0]
        self.assertTrue(entry['needs_recheck'])
        self.assertIn('Selected retained reference changed or is unavailable', entry['recheck_reasons'])


if __name__ == '__main__':
    unittest.main()
