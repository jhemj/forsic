"""Explicit per-claim evidence through the real atomic writer; synthetic/offline."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

import test_efficiency_contracts as fixtures
import test_grounded_judgment as grounded
from test_report_driven import fixture
from forsic_plugin.report_driven.host import current, find, checked
from forsic_plugin.report_driven.current_judgment import current_judgment
from forsic_plugin.report_driven.grounded_judgment import schema
from forsic_plugin.report_driven.investigation_state import input_problems


class ClaimEvidenceBindingTests(unittest.TestCase):
    setUp = fixtures.EfficiencyContracts.setUp
    call = fixtures.EfficiencyContracts.call
    read = grounded.GroundedJudgmentTests.read
    ref = grounded.GroundedJudgmentTests.ref
    payload = grounded.GroundedJudgmentTests.payload

    def granular(self, **changes):
        request = self.read()
        process = self.read('process.log')
        claims = [dict(title='Request', statement='The retained record describes a maintenance request.',
                       citation_refs=[self.ref(request)], assertion_kind='fact',
                       reasoning_summary='The selected request field contains the requested target.'),
                  dict(statement='The request and process may be linked; successful completion is unknown.',
                       citation_refs=[self.ref(process)], counter_citation_refs=[self.ref(request)],
                       assertion_kind='interpretation', inference_strength='plausible',
                       reasoning_summary='Process creation is recorded, but the request has no shared run identifier.',
                       assumptions=['The similarly named task could be related'],
                       limitations=['No shared identifier or completion receipt'])]
        p = self.payload([])
        p.update(judgment={'claims': claims}, **changes)
        return p, request, process

    def saved_question(self, out):
        self.assertNotIn('error', out, out)
        state = current(self.case)
        checked(state)
        return state, find(state, 'questions', out['question']['question_id'])

    def selected(self, state, question):
        return [find(state, 'claims', r['id']) for r in question['claim_refs']]

    def assert_unchanged(self, before):
        after = current(self.case)
        for field in ('questions', 'claims', 'observations', 'gaps', 'missions', 'tests', 'assessments'):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(after['meta']['content_revision'], before['meta']['content_revision'])

    def test_each_authored_claim_keeps_its_exact_support_counter_and_declarations(self):
        p, request, process = self.granular()
        before = current(self.case)
        out = self.call('forsic_question', p)
        state, q = self.saved_question(out)
        claims = self.selected(state, q)
        self.assertEqual(len(claims), 2)
        self.assertEqual(q['answer'], p['answer'])
        self.assertEqual(q['alternatives'], p['alternatives'])
        self.assertEqual(q['next_checks'], p['next_checks'])
        self.assertEqual(q['evidence_ids'], [request['evidence_id'], process['evidence_id']])
        for actual, submitted in zip(claims, p['judgment']['claims']):
            self.assertEqual(actual['statement'], submitted['statement'])
            self.assertEqual(actual['semantic_review'], 'not_reviewed')
            self.assertEqual(actual['reasoning_summary'], submitted['reasoning_summary'])
            self.assertEqual(actual['scope'], q['scope'])
        self.assertEqual(claims[0]['title'], 'Request')
        self.assertEqual(claims[1]['title'], q['target_proposition'])
        self.assertEqual(claims[0]['declared_analysis_fields'], [])
        self.assertEqual(claims[1]['declared_analysis_fields'],
                         ['counter_citation_refs', 'inference_strength', 'assumptions', 'limitations'])
        for claim, result in zip(claims, (request, process)):
            ob = find(state, 'observations', claim['source_refs'][0]['id'])
            self.assertEqual(ob['source_ref']['id'], result['evidence_id'])
            self.assertEqual(ob['source_ref']['version'], find(state, 'sources', result['evidence_id'])['version'])
            self.assertEqual(ob['literal'], result['lines'][0]['text'])
            self.assertEqual(ob['byte_end'], len(ob['literal'].encode('utf-8')))
            self.assertIn('not original image offset', ob['coordinate_basis'])
            self.assertTrue(ob['acquisition_context'])
        self.assertEqual(claims[1]['counterevidence_refs'], claims[0]['source_refs'])
        self.assertEqual(claims[1]['limitations'], p['judgment']['claims'][1]['limitations'])
        self.assertEqual(current_judgment(state, q)['answer'], p['answer'])
        self.assertEqual(len(current_judgment(state, q)['judgments']), 2)
        for field in ('missions', 'tests', 'assessments'):
            self.assertEqual(state[field], before[field])
        self.assertEqual(self.call('forsic_question', p)['question'], out['question'])
        self.assertEqual(current(self.case)['meta']['content_revision'], state['meta']['content_revision'])

    def test_granular_writer_report_and_portable_evidence_preserve_each_claim_basis(self):
        from forsic_plugin.report_driven.views import bundle, selected_report_state
        p, request, process = self.granular()
        state, question = self.saved_question(self.call('forsic_question', p))
        authored = self.selected(state, question)
        report = bundle(self.case, state, question_ids=[question['id']])
        manifest = json.loads(Path(report['manifest']).read_text())
        self.assertEqual(manifest['evidence_export']['status'], 'complete')
        self.assertEqual(manifest['evidence_export']['snapshot_id'], state['meta']['snapshot_id'])
        evidence = Path(report['manifest']).parent / 'selected-evidence.zip'
        with zipfile.ZipFile(evidence) as archive:
            exported = json.loads(archive.read('selection.json'))
            evidence_manifest = json.loads(archive.read('manifest.json'))
            sources = json.loads(archive.read('sources.json'))
            cards = {c['card_id']: c for c in json.loads(archive.read('cards.json'))}
            verifier = archive.read('verify.py')
        self.assertEqual(evidence_manifest['question_ids'], [question['id']])
        self.assertEqual(evidence_manifest['snapshot_id'], report['snapshot_id'])
        actual_claims = {c['id']: c for c in exported['claims']}
        for claim, submitted, result in zip(authored, p['judgment']['claims'], (request, process)):
            actual = actual_claims[claim['id']]
            self.assertEqual(actual['statement'], submitted['statement'])
            for field in ('source_refs', 'counterevidence_refs', 'limitations', 'assumptions'):
                self.assertEqual(actual[field], claim[field])
            for reference in actual['source_refs'] + actual['counterevidence_refs']:
                original = find(state, 'observations', reference['id'])
                card = cards[reference['id']]
                self.assertEqual(card['observation_version'], reference['version'])
                self.assertEqual(card['source_ref'], original['source_ref'])
                self.assertEqual(card['retained_span']['literal'], original['literal'])
                self.assertEqual(card['retained_span']['byte_start'], original['byte_start'])
                self.assertEqual(card['retained_span']['byte_end'], original['byte_end'])
            self.assertEqual(cards[actual['source_refs'][0]['id']]['source']['source_id'], result['evidence_id'])
        self.assertEqual(actual_claims[authored[1]['id']]['limitations'],
                         p['judgment']['claims'][1]['limitations'])
        expected_versions = {s['id']: s['version'] for s in selected_report_state(state,[question['id']])['sources']}
        self.assertEqual(evidence_manifest['selection_index']['source'], expected_versions)
        self.assertEqual({s['source']['source_id']: s['source']['source_version'] for s in sources}, expected_versions)
        practitioner = (Path(report['manifest']).parent/'integrated.html').read_text()
        for submitted in p['judgment']['claims']:
            self.assertIn(submitted['statement'], practitioner)
            for limitation in submitted.get('limitations', []):
                self.assertIn(limitation, practitioner)
        verifier_path = Path(self.temp.name)/'verify_selected_evidence.py'
        verifier_path.write_bytes(verifier)
        verified = subprocess.run([sys.executable,str(verifier_path),str(evidence)], capture_output=True,text=True)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)['verified_cards'], len(cards))
        self.assertEqual(current(self.case), state)

    def test_legacy_compound_answer_remains_one_claim_without_splitting_prose(self):
        p = self.payload([self.ref(self.read())])
        p['answer'] = 'A request exists. Execution remains unknown. Attribution is also unknown.'
        state, q = self.saved_question(self.call('forsic_question', p))
        claims = self.selected(state, q)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['statement'], p['answer'])
        self.assertEqual(claims[0]['limitations'], p['judgment']['limitations'])

    def test_all_claims_supersede_and_retract_together_preserving_history(self):
        p, _, _ = self.granular()
        state, q = self.saved_question(self.call('forsic_question', p))
        previous = [c['id'] for c in self.selected(state, q)]
        update = deepcopy(p)
        update.update(operation_id='correct', question_id=q['id'], expected_revision=q['revision'],
                      answer='A revised answer preserves the linkage uncertainty.')
        update['judgment']['claims'][0]['statement'] = 'The request names an explicit target.'
        state, q = self.saved_question(self.call('forsic_question', update))
        replacement = [c['id'] for c in self.selected(state, q)]
        self.assertEqual(q['revision'], 2)
        self.assertFalse(set(previous) & set(replacement))
        for cid in previous:
            self.assertEqual(find(state, 'claims', cid)['status'], 'superseded')
        out = self.call('forsic_question', dict(operation_id='retract-all', question_id=q['id'],
            expected_revision=q['revision'], claim_action='retract', reason='Withdraw both retained claims'))
        state, q = self.saved_question(out)
        self.assertEqual(q['claim_refs'], [])
        for cid in replacement:
            self.assertEqual(find(state, 'claims', cid)['status'], 'retracted')
        from forsic_plugin.notes import note
        history = note(self.case, {'action':'get', 'note_id':q['id'][2:]})['history']
        self.assertEqual([n['revision'] for n in history], [3, 2, 1])
        self.assertEqual(history[-1]['answer'], p['answer'])
        self.assertEqual(len(history[-1]['investigation']['claim_refs']), 2)

    def test_one_invalid_claim_rolls_back_retirement_observations_note_and_operation(self):
        p, _, _ = self.granular()
        state, q = self.saved_question(self.call('forsic_question', p))
        update = deepcopy(p)
        update.update(operation_id='rollback-then-retry', question_id=q['id'], expected_revision=q['revision'],
                      answer='A deliberately revised answer.')
        good_ref = update['judgment']['claims'][1]['counter_citation_refs'][0]
        update['judgment']['claims'][1]['counter_citation_refs'] = [good_ref[:-1]+'9']
        before = current(self.case)
        self.assertIn('error', self.call('forsic_question', update))
        self.assert_unchanged(before)
        with self.case.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='mutation_receipt' AND json_extract(data,'$.operation_id')=?",
                                        (update['operation_id'],)).fetchone()[0], 0)
        update['judgment']['claims'][1]['counter_citation_refs'] = [good_ref]
        state, q = self.saved_question(self.call('forsic_question', update))
        self.assertEqual(q['revision'], 2)
        self.assertEqual(len(q['claim_refs']), 2)

    def test_repeated_explicit_claims_are_distinct_but_share_the_exact_observation(self):
        p, _, _ = self.granular()
        p['judgment']['claims'] = [p['judgment']['claims'][0], deepcopy(p['judgment']['claims'][0])]
        state, q = self.saved_question(self.call('forsic_question', p))
        a, b = self.selected(state, q)
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual(a['source_refs'], b['source_refs'])
        self.assertEqual(sum(o['id'] == a['source_refs'][0]['id'] for o in state['observations']), 1)

    def test_granular_claims_keep_v1_full_analysis_and_accept_exact_c1_selections(self):
        p, request, _ = self.granular()
        page = self.call('forsic_reporting', {'action':'source','source_id':request['evidence_id'],'query':'requested'})
        citation = page['matches'][0]['citation_ref']
        self.assertTrue(citation.startswith('C1:'))
        p['judgment']['claims'][0]['citation_refs'] = [citation]
        for claim in p['judgment']['claims']:
            for key, default in (('counter_citation_refs',[]),('inference_strength','unrated'),('assumptions',[]),('limitations',[])):
                claim.setdefault(key, default)
        out = json.loads(self.case.invoke('forsic_reporting', {'action':'question','payload':p}, 'synthetic-session',
            request_context={'goal_id':'synthetic-goal','conversation_id':'synthetic-session','question_contract_version':'v1'}))
        state, q = self.saved_question(out)
        self.assertEqual(len(q['claim_refs']), 2)
        self.assertEqual(q['answer'], p['answer'])
        for claim in self.selected(state, q):
            self.assertNotIn('declared_analysis_fields', claim)

    def test_new_scope_closure_accepts_granular_citations_without_aggregate_fields(self):
        p, _, _ = self.granular()
        p.update(work_state='scoped_closed', reopen_conditions=['New independent record'],
            gap_dispositions=[dict(gap_text=p['gaps'][0], disposition='assessed_unresolved',
                                  reason='No retained completion record', reopen_conditions=['New completion record'])])
        state, q = self.saved_question(self.call('forsic_question', p))
        self.assertEqual(q['work_state'], 'scoped_closed')
        self.assertEqual(len(q['claim_refs']), 2)

    def test_revision_owner_and_operation_guards_apply_to_the_entire_claim_set(self):
        p, _, _ = self.granular()
        state, q = self.saved_question(self.call('forsic_question', p))
        update = deepcopy(p)
        update.update(operation_id='stale', question_id=q['id'], expected_revision=q['revision']+1)
        self.assertIn('error', self.call('forsic_question', update))
        self.assert_unchanged(state)
        update.update(operation_id='foreign', expected_revision=q['revision'])
        out = json.loads(self.case.invoke('forsic_reporting', {'action':'question','payload':update}, 'foreign',
            request_context={'goal_id':'foreign','conversation_id':'foreign','question_contract_version':'v2'}))
        self.assertIn('error', out)
        self.assert_unchanged(state)
        conflicting = deepcopy(p)
        conflicting['judgment']['claims'][0]['statement'] += ' Changed.'
        self.assertIn('different mutation payload', self.call('forsic_question', conflicting)['error'])
        self.assert_unchanged(state)

    def test_claim_rejects_foreign_case_and_changed_source_without_partial_write(self):
        p, request, _ = self.granular()
        with tempfile.TemporaryDirectory() as other_root:
            other, _, _ = fixture(other_root)
            foreign = json.loads(other.invoke('forsic_read', {'path':'request.log'}))
            from forsic_plugin.report_driven.grounded_judgment import read_refs
            foreign_ref = read_refs(other, foreign)['presented_spans'][0]['citation_ref']
            p['judgment']['claims'][1]['citation_refs'] = [foreign_ref]
            before = current(self.case)
            self.assertIn('error', self.call('forsic_question', p))
            self.assert_unchanged(before)
        p['judgment']['claims'][1]['citation_refs'] = [self.ref(request)]
        event = self.case.event(request['evidence_id'])
        event['data']['lines'][0]['text'] = 'Changed retained source field'
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?', (json.dumps(event['data']), request['evidence_id']))
        before = current(self.case)
        self.assertIn('error', self.call('forsic_question', p))
        self.assert_unchanged(before)

    def test_sanitized_schema_preserves_alternatives_and_rejects_ambiguous_or_invalid_claims(self):
        from jsonschema import Draft202012Validator
        from tools.schema_sanitizer import sanitize_tool_schemas
        p, _, _ = self.granular()
        minimal = deepcopy(p['judgment'])
        legacy = self.payload([self.ref(self.read())])['judgment']
        invalid = []
        for change in ({'statement':' '}, {'title':''}, {'citation_refs':[]},
                       {'citation_refs':[7]}, {'reasoning_summary':'\n'}, {'extra':'invented'}):
            value = deepcopy(minimal)
            value['claims'][1].update(change)
            invalid.append(value)
        for version in ('v1','v2'):
            granular = deepcopy(minimal)
            if version == 'v1':
                for claim in granular['claims']:
                    for key, default in (('counter_citation_refs',[]),('inference_strength','unrated'),('assumptions',[]),('limitations',[])):
                        claim.setdefault(key, default)
            function = {'name':'synthetic', 'parameters':{'type':'object', 'properties':{'judgment':schema(version)},
                'required':['judgment'], 'additionalProperties':False}}
            wire = sanitize_tool_schemas([{'type':'function','function':function}])[0]['function']['parameters']
            self.assertNotIn('$ref', json.dumps(wire))
            for value, valid in [(granular,True), (legacy,True), ({'claims':[]},False),
                                  ({**legacy, **granular},False), *[(v,False) for v in invalid]]:
                with self.subTest(version=version, value=value):
                    self.assertEqual(Draft202012Validator(wire).is_valid({'judgment':value}), valid)
                    self.assertEqual(not input_problems({'judgment':value},wire), valid)
        bad = deepcopy(p)
        bad['judgment']['claims'][1]['inference_strength'] = 'favored'
        bad['judgment']['claims'][1]['assertion_kind'] = 'fact'
        before = current(self.case)
        self.assertIn('Inference strength', self.call('forsic_question', bad)['error'])
        self.assert_unchanged(before)


if __name__ == '__main__':
    unittest.main()
