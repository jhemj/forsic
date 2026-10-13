"""Synthetic native receipt handoffs; no model, live evidence or network access."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

import test_efficiency_contracts as efficiency
from test_report_driven import fixture
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.host import current, find


class AssessmentBasisTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.create_connection'):
            blocker = patch(target, side_effect=AssertionError('assessment basis tests forbid network'))
            blocker.start(); self.addCleanup(blocker.stop)
        self.x = efficiency.EfficiencyContracts()
        self.x.setUp(); self.addCleanup(self.x.doCleanups)
        self.case, self.qid = self.x.case, self.x.qid
        self.serial = 0
        q = self.question()
        self.ok(self.x.call('forsic_question', {
            'operation_id': 'bind-basis-fixture', 'question_id': q['id'],
            'expected_revision': q['revision'], 'answer': q['answer'],
            'reason': 'Bind this synthetic question to its native session and goal'}))

    def ok(self, result):
        self.assertNotIn('error', result, result)
        return result

    def question(self):
        return find(current(self.case), 'questions', self.qid)

    def reporting(self, action, payload=None, *, case=None, goal='synthetic-goal',
                  session='synthetic-session', **fields):
        args = {'action': action, **fields}
        if payload is not None:
            args['payload'] = payload
        if action == 'source' and case is None and goal == 'synthetic-goal' and session == 'synthetic-session':
            # The registered entry decorates the already persisted Case.invoke
            # return with presentation-only citation refs, as it does at runtime.
            return self.x.call('forsic_reporting', args)
        return json.loads((case or self.case).invoke('forsic_reporting', args, session,
            request_context={'goal_id': goal, 'conversation_id': session}))

    def plan(self, **changes):
        self.serial += 1
        return self.ok(self.x.call('forsic_mission', {
            **self.x.plan(), 'operation_id': 'basis-plan-' + str(self.serial), **changes}))['mission']

    def execute(self, mission, **changes):
        m = find(current(self.case), 'missions', mission['id'])
        return self.ok(self.x.call(m['proposed_tool_name'], {
            **m['tool_arguments'], 'mission_id': m['id'], 'mission_version': m['version'], **changes}))

    def locate(self, result, query='requested'):
        return self.ok(self.reporting('source', source_id=result['evidence_id'], query=query))

    def refs(self, located, role='body'):
        return [match['citation_ref'] for match in located['matches'] if match['role'] == role]

    def prepare(self, mission, result, refs, **context):
        return self.reporting('assessment_basis', {
            'mission_id': mission['id'], 'result_id': result['evidence_id'],
            'citation_refs': refs}, **context)

    def proposal(self, basis, **changes):
        return {'operation_id': 'basis-assessment', 'basis_receipt_id': basis['evidence_id'],
            'outcome': 'inconclusive', 'reasoning_summary': 'This records a request, not proof of execution',
            'limitations': ['One acquired record; execution and attribution remain unknown'],
            'next_check': 'Compare an independent execution receipt', **changes}

    def ready(self, **plan_changes):
        mission = self.plan(**plan_changes)
        result = self.execute(mission)
        located = self.locate(result)
        basis = self.ok(self.prepare(mission, result, self.refs(located)))
        return mission, result, located, basis

    def counts(self):
        with self.case.connect() as db:
            return dict(db.execute('SELECT kind,COUNT(*) FROM events GROUP BY kind'))

    def replace_event_data(self, event_id, mutate):
        """Corrupt only a disposable synthetic fixture to exercise stale bindings."""
        data = deepcopy(self.case.event(event_id)['data'])
        mutate(data)
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?',
                       (json.dumps(data, ensure_ascii=False), event_id))

    def test_explicit_locator_choice_basis_and_actual_atomic_assessment(self):
        mission, result, located, basis = self.ready()
        before = current(self.case)
        self.assertFalse(before['assessments'])
        self.assertEqual(self.refs(located), ['C1:' + located['evidence_id'] + ':0'])
        self.assertEqual(self.case.event(located['evidence_id'])['data']['tool'], 'forsic_reporting')
        self.assertEqual(self.case.event(basis['evidence_id'])['data']['tool'], 'forsic_reporting')
        assessed = self.ok(self.reporting('assess_basis', self.proposal(basis)))
        state = current(self.case)
        self.assertEqual(len(state['assessments']), 1)
        assessment = find(state, 'assessments', assessed['assessment']['id'])
        self.assertEqual(assessment['result_ref']['id'], result['evidence_id'])
        self.assertEqual(find(state, 'missions', mission['id'])['state'], 'completed')
        observation = find(state, 'observations', assessment['observation_refs'][0]['id'])
        self.assertNotIn('citation',located['matches'][0])
        original = self.case.event(located['evidence_id'])['data']['matches'][0]['citation']
        self.assertEqual(observation['literal'], original['literal'])
        self.assertEqual(observation['field_pointer'], original['pointer'])
        self.assertEqual(observation['byte_start'], original['byte_start'])
        self.assertEqual(self.question(), find(before, 'questions', self.qid))
        self.assertEqual(state['sources'], before['sources'])
        host.checked(state)

    def test_prepared_judgment_contract_exposes_missing_rules_before_submission(self):
        mission, result, _, basis = self.ready()
        contract = basis['assessment_basis']['judgment_contract']
        self.assertIsNone(contract['support_rule'])
        self.assertNotIn('supports', contract['eligible_outcomes'])
        self.assertEqual(contract['unmet_prerequisites']['supports'], ['support_rule'])
        self.assertIn('found', contract['eligible_outcomes'])
        self.assertFalse(contract['semantic_approval'])
        from forsic_plugin.report_driven.assessment_basis import basis_view
        self.assertEqual(basis_view(basis)['judgment_contract'], contract)
        rejected = self.reporting('assess_basis', self.proposal(basis, outcome='supports'))
        self.assertIn('support_rule', rejected['error'])
        self.assertIn('eligible_outcomes', rejected['error'])
        self.assertFalse(current(self.case)['assessments'])
        # No automatic outcome change: the caller explicitly chooses and explains
        # the narrower observation, using the same still-valid selected basis.
        self.ok(self.reporting('assess_basis', self.proposal(basis, outcome='found')))
        self.assertEqual(current(self.case)['assessments'][0]['outcome'], 'found')

    def test_declared_rule_requires_selected_observation_and_stays_literal(self):
        rule = 'A request line supports only the existence of a recorded request'
        mission, result, located, basis = self.ready(support_rule=rule)
        contract = basis['assessment_basis']['judgment_contract']
        self.assertEqual(contract['support_rule'], rule)
        self.assertIn('supports', contract['eligible_outcomes'])
        empty = self.ok(self.prepare(mission, result, []))
        empty_contract = empty['assessment_basis']['judgment_contract']
        self.assertNotIn('supports', empty_contract['eligible_outcomes'])
        self.assertNotIn('found', empty_contract['eligible_outcomes'])
        self.assertIn('selected exact source observations', empty_contract['unmet_prerequisites']['supports'])
        self.assertIn('error', self.reporting('assess_basis', self.proposal(empty, outcome='supports')))
        self.ok(self.reporting('assess_basis', self.proposal(basis, outcome='supports')))

    def test_failed_result_prerequisites_do_not_turn_failure_into_absence(self):
        mission = self.plan(tool_arguments={'path': 'missing.log'})
        m = find(current(self.case), 'missions', mission['id'])
        failed = self.x.call('forsic_read', {**m['tool_arguments'], 'mission_id':m['id'], 'mission_version':m['version']})
        self.assertIn('error', failed)
        basis = self.ok(self.prepare(mission, failed, []))
        contract = basis['assessment_basis']['judgment_contract']
        self.assertTrue(contract['failed_result'])
        self.assertEqual(contract['eligible_outcomes'], ['unavailable'])
        self.assertIn('error', self.reporting('assess_basis', self.proposal(basis, outcome='no_match_in_scope')))
        self.assertFalse(current(self.case)['assessments'])
        self.ok(self.reporting('assess_basis', self.proposal(basis, outcome='unavailable')))

    def test_prior_basis_contract_requires_explicit_reprepare(self):
        mission, result, located, basis = self.ready()
        self.replace_event_data(basis['evidence_id'], lambda d: d['assessment_basis'].update(kind='forsic_assessment_basis_v1'))
        rejected = self.reporting('assess_basis', self.proposal(basis))
        self.assertIn('explicitly prepared again', rejected['error'])
        self.assertFalse(current(self.case)['assessments'])
        fresh = self.ok(self.prepare(mission, result, self.refs(located)))
        self.ok(self.reporting('assess_basis', self.proposal(fresh)))

    def test_utf8_duplicate_literal_locations_do_not_collapse_or_guess(self):
        text = '가🙂 needle 가🙂 needle 끝'
        (self.case.root / 'request.log').write_text(text + '\n')
        mission = self.plan(); result = self.execute(mission)
        located = self.locate(result, 'needle')
        matches = [m for m in located['matches'] if m['role'] == 'body']
        self.assertEqual(len(matches), 2)
        self.assertNotEqual(matches[0]['citation_ref'], matches[1]['citation_ref'])
        selected = matches[1]
        basis = self.ok(self.prepare(mission, result, [selected['citation_ref']]))
        self.ok(self.reporting('assess_basis', self.proposal(basis)))
        obs = current(self.case)['observations'][-1]
        expected = len(text[:text.rfind('needle')].encode('utf-8'))
        self.assertEqual(obs['byte_start'], expected)
        self.assertEqual(obs['byte_end'], expected + len(b'needle'))
        self.assertEqual(obs['literal'], 'needle')

    def test_exact_scalar_page_ref_retains_presented_span(self):
        mission = self.plan(required_view='full_field'); result = self.execute(mission)
        source = find(current(self.case), 'sources', result['evidence_id'])
        page = self.ok(self.reporting('source', source_id=source['id'], source_version=source['version'],
            pointer='/lines/0/text', offset=0, limit=4096))
        self.assertTrue(page['full_field'])
        basis = self.ok(self.prepare(mission, result, [page['citation_ref']]))
        self.ok(self.reporting('assess_basis', self.proposal(basis)))
        obs = current(self.case)['observations'][-1]
        self.assertEqual(obs['literal'], page['text'])
        self.assertEqual(obs['presented_view'], 'full_field')

    def test_stale_question_revision_is_not_replaced_with_current(self):
        _, _, _, basis = self.ready()
        q = self.question()
        self.ok(self.x.call('forsic_question', {'operation_id': 'wording-after-basis',
            'question_id': q['id'], 'expected_revision': q['revision'],
            'answer': q['answer'] + ' Wording correction', 'reason': 'Explicit correction'}))
        result = self.reporting('assess_basis', self.proposal(basis))
        self.assertIn('error', result)
        self.assertFalse(current(self.case)['assessments'])

    def test_changed_question_definition_invalidates_its_bound_mission(self):
        _, _, _, basis = self.ready()
        q = self.question()
        self.ok(self.x.call('forsic_question', {'operation_id': 'scope-after-basis',
            'question_id': q['id'], 'expected_revision': q['revision'],
            'scope': 'A materially different synthetic scope', 'reason': 'Explicit definition change'}))
        self.assertIn('error', self.reporting('assess_basis', self.proposal(basis)))
        self.assertFalse(current(self.case)['assessments'])

    def test_changed_retained_source_version_is_not_silently_refreshed(self):
        _, result, _, basis = self.ready()
        self.replace_event_data(result['evidence_id'], lambda d: d['lines'][0].update(text='changed synthetic field'))
        self.assertIn('error', self.reporting('assess_basis', self.proposal(basis)))
        self.assertFalse(current(self.case)['assessments'])

    def test_changed_saved_mission_contract_is_not_silently_refreshed(self):
        mission, _, _, basis = self.ready()
        event = next(event for event in host.ledger(self.case)
            if event['kind'] == 'report_change' and any(
                m['id'] == mission['id'] for m in event['data'].get('records', {}).get('missions', [])))
        def corrupt_contract(data):
            next(m for m in data['records']['missions'] if m['id'] == mission['id'])['why_it_matters'] += ' changed'
        self.replace_event_data(event['id'], corrupt_contract)
        self.assertNotEqual(find(current(self.case), 'missions', mission['id'])['version'], mission['version'])
        self.assertIn('error', self.reporting('assess_basis', self.proposal(basis)))
        self.assertFalse(current(self.case)['assessments'])

    def test_basis_cannot_cross_case_session_or_goal(self):
        mission, result, located, basis = self.ready()
        for context in ({'session': 'different-session'}, {'goal': 'different-goal'}):
            with self.subTest(context=context):
                self.assertIn('error', self.prepare(mission, result, self.refs(located), **context))
                self.assertIn('error', self.reporting('assess_basis', self.proposal(basis), **context))
        with tempfile.TemporaryDirectory() as tmp:
            other, _, _ = fixture(tmp)
            self.assertIn('error', self.reporting('assess_basis', self.proposal(basis), case=other))
            self.assertFalse(current(other)['assessments'])
        self.assertFalse(current(self.case)['assessments'])

    def test_malformed_forged_and_out_of_range_citation_refs_do_not_save(self):
        mission = self.plan(); result = self.execute(mission); located = self.locate(result)
        valid = self.refs(located)[0]
        for reference in ('', 'made-up', 'C1:missing:0', valid.rsplit(':', 1)[0] + ':-1',
                          valid.rsplit(':', 1)[0] + ':999', 'C1:' + result['evidence_id'] + ':0'):
            with self.subTest(reference=reference):
                self.assertIn('error', self.prepare(mission, result, [reference]))
        self.assertFalse(current(self.case)['assessments'])

    def test_zero_match_and_collection_preview_do_not_issue_citations(self):
        mission = self.plan(); result = self.execute(mission)
        located = self.locate(result, 'not-present-anywhere')
        self.assertEqual(located['matches'], [])
        self.assertNotIn('citation_ref', located)
        self.assertIn('error', self.prepare(mission, result, ['C1:' + located['evidence_id'] + ':0']))
        source = find(current(self.case), 'sources', result['evidence_id'])
        preview = self.ok(self.reporting('source', source_id=source['id'], source_version=source['version'], pointer='/lines'))
        self.assertNotIn('citation_ref', preview)
        self.assertIn('error', self.prepare(mission, result, ['C1:' + preview['evidence_id'] + ':0']))

    def test_duplicate_choice_and_modified_locator_receipt_are_rejected(self):
        mission = self.plan(); result = self.execute(mission); located = self.locate(result)
        ref = self.refs(located)[0]
        self.assertIn('error', self.prepare(mission, result, [ref, ref]))
        self.replace_event_data(located['evidence_id'],
            lambda d: d['matches'][0]['citation'].update(literal='invented literal'))
        self.assertIn('error', self.prepare(mission, result, [ref]))
        self.assertFalse(current(self.case)['assessments'])

    def test_metadata_and_partial_excerpt_do_not_become_required_full_body(self):
        mission = self.plan(required_view='full_field'); result = self.execute(mission)
        for query, role in (('request.log', 'metadata'), ('requested', 'body')):
            with self.subTest(query=query):
                located = self.locate(result, query)
                refs = self.refs(located, role)
                self.assertTrue(refs)
                basis = self.prepare(mission, result, refs)
                outcome = basis if 'error' in basis else self.reporting('assess_basis', self.proposal(basis))
                self.assertIn('error', outcome)
                self.assertFalse(current(self.case)['assessments'])

    def test_unbound_continuation_needs_explicit_retained_result_mission(self):
        (self.case.root / 'request.log').write_text('job=maintenance requested target=/one\njob=maintenance requested target=/two\n')
        mission = self.plan(tool_arguments={'path': 'request.log', 'line_count': 1}); first = self.execute(mission)
        continuation = self.ok(self.x.call('forsic_read', {'path': 'request.log', 'start_line': 2, 'line_count': 1}))
        self.assertEqual(first['lines'][0]['line'], 1)
        self.assertEqual(continuation['lines'][0]['line'], 2)
        located = self.locate(continuation)
        self.assertIn('error', self.prepare(mission, continuation, self.refs(located)))
        self.assertNotEqual(first['evidence_id'], continuation['evidence_id'])
        payload = {**self.x.plan(), 'operation_id': 'explicit-reuse',
                   'result_ids': [continuation['evidence_id']]}
        payload.pop('tool_name'); payload.pop('tool_arguments')
        reuse = self.ok(self.x.call('forsic_mission', payload))['mission']
        basis = self.ok(self.prepare(reuse, continuation, self.refs(located)))
        self.ok(self.reporting('assess_basis', self.proposal(basis)))
        state = current(self.case)
        self.assertEqual(find(state, 'missions', mission['id'])['state'], 'unassessed')
        self.assertEqual(find(state, 'missions', reuse['id'])['state'], 'completed')

    def test_identical_replay_survives_later_revision_changed_payload_rejects(self):
        _, _, _, basis = self.ready(); payload = self.proposal(basis)
        first = self.ok(self.reporting('assess_basis', payload))
        q = self.question()
        self.ok(self.x.call('forsic_question', {'operation_id': 'later-question', 'question_id': q['id'],
            'expected_revision': q['revision'], 'answer': q['answer'] + ' Later wording'}))
        second = self.ok(self.reporting('assess_basis', payload))
        self.assertEqual(first['receipt'], second['receipt'])
        self.assertIn('error', self.reporting('assess_basis', {**payload, 'reasoning_summary': 'Changed retry'}))
        self.assertEqual(len(current(self.case)['assessments']), 1)
        with self.case.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM events WHERE kind='mutation_receipt' AND json_extract(data,'$.operation_id')=?",
                               (payload['operation_id'],)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_unrelated_question_change_does_not_replace_or_stale_bound_revision(self):
        _, _, _, basis = self.ready()
        self.ok(self.x.call('forsic_question', {'operation_id': 'unrelated', 'question': 'Another synthetic question',
            'scope': 'Independent scope', 'answer': 'Unknown'}))
        self.ok(self.reporting('assess_basis', self.proposal(basis)))
        self.assertEqual(len(current(self.case)['assessments']), 1)

    def test_native_observation_events_and_unrelated_source_do_not_stale_basis(self):
        mission, result, _, basis = self.ready()
        before = current(self.case)
        for kind in ('pre_api_request', 'post_api_request', 'turn_start', 'turn_complete'):
            self.case.record(kind, {'synthetic': True}, 'synthetic-session')
        self.ok(self.reporting('state'))
        unrelated = self.ok(self.x.call('forsic_read', {'path': 'process.log'}))
        self.ok(self.locate(unrelated, 'created'))
        self.assertNotEqual(current(self.case)['meta']['snapshot_id'], before['meta']['snapshot_id'])
        self.assertEqual(find(current(self.case), 'missions', mission['id'])['version'], mission['version'])
        self.ok(self.reporting('assess_basis', self.proposal(basis)))
        assessment = current(self.case)['assessments'][0]
        self.assertEqual(assessment['result_ref']['id'], result['evidence_id'])

    def test_explicit_question_update_and_assessment_share_atomic_rollback_and_retry(self):
        _, _, _, basis = self.ready(); before = current(self.case); q = self.question()
        payload = self.proposal(basis, question_update={
            'answer': 'A request is recorded; execution remains unverified',
            'assessment': 'undetermined', 'reason': 'Explicit synthesis of this local test'})
        with patch.object(host, 'save_note_locked', side_effect=ValueError('synthetic note save failure')):
            rejected = self.reporting('assess_basis', payload)
        self.assertIn('error', rejected)
        after = current(self.case)
        for field in ('questions', 'assessments', 'observations', 'missions'):
            self.assertEqual(after[field], before[field])
        saved = self.ok(self.reporting('assess_basis', payload))
        self.assertEqual(self.question()['answer'], payload['question_update']['answer'])
        self.assertEqual(self.question()['revision'], q['revision'] + 1)
        self.assertEqual(self.ok(self.reporting('assess_basis', payload))['receipt'], saved['receipt'])
        self.assertEqual(len(current(self.case)['assessments']), 1)

    def test_concurrent_same_operation_has_one_atomic_assessment(self):
        _, _, _, basis = self.ready(); payload = self.proposal(basis)
        barrier = Barrier(2)
        def submit(_):
            barrier.wait(timeout=10)
            return self.reporting('assess_basis', deepcopy(payload))
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(submit, range(2)))
        for result in results: self.ok(result)
        self.assertEqual(results[0]['receipt'], results[1]['receipt'])
        self.assertEqual(len(current(self.case)['assessments']), 1)

    def test_concurrent_different_results_cannot_overwrite_one_mission_test(self):
        results = [self.ok(self.x.call('forsic_read', {'path': name})) for name in ('request.log', 'process.log')]
        payload = {**self.x.plan(), 'operation_id': 'multi-result-plan',
                   'result_ids': [r['evidence_id'] for r in results]}
        payload.pop('tool_name'); payload.pop('tool_arguments')
        mission = self.ok(self.x.call('forsic_mission', payload))['mission']
        bases = [self.ok(self.prepare(mission, result, self.refs(self.locate(result, query))))
                 for result, query in zip(results, ('requested', 'created'))]
        barrier = Barrier(2); original_write = host.write
        def synchronized_write(*args, **kwargs):
            if args[2].get('records', {}).get('assessments'):
                barrier.wait(timeout=10)
            return original_write(*args, **kwargs)
        def submit(index):
            return self.reporting('assess_basis', self.proposal(bases[index], operation_id='parallel-' + str(index)))
        with patch.object(host, 'write', side_effect=synchronized_write), ThreadPoolExecutor(max_workers=2) as workers:
            returned = list(workers.map(submit, range(2)))
        self.assertEqual(sum('error' not in r for r in returned), 1, returned)
        state = current(self.case)
        self.assertEqual(len(state['assessments']), 1)
        test = find(state, 'tests', state['assessments'][0]['test_ref']['id'])
        self.assertEqual(test['result_ref'], state['assessments'][0]['result_ref'])
        host.checked(state)


if __name__ == '__main__':
    unittest.main()
