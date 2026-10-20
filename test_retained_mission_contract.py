"""Native retained-result planning on synthetic evidence; no network or models."""
import json
import tempfile
import unittest
from unittest.mock import patch

import test_efficiency_contracts as efficiency
from test_report_driven import fixture
from forsic_plugin.notes import EVIDENCE_TOOLS
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.host import current, find


class RetainedMissionContractTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.create_connection'):
            blocker = patch(target, side_effect=AssertionError('retained mission tests forbid network'))
            blocker.start(); self.addCleanup(blocker.stop)
        self.x = efficiency.EfficiencyContracts()
        self.x.setUp(); self.addCleanup(self.x.doCleanups)
        self.case, self.qid = self.x.case, self.x.qid
        q = self.question()
        self.ok(self.x.call('forsic_question', {
            'operation_id': 'bind-retained-fixture', 'question_id': q['id'],
            'expected_revision': q['revision'], 'answer': q['answer'],
            'reason': 'Bind the synthetic question to this native session and goal'}))

    def ok(self, result):
        self.assertNotIn('error', result, result)
        return result

    def question(self):
        return find(current(self.case), 'questions', self.qid)

    def payload(self, **changes):
        q = self.question()
        return {'operation_id': 'retained-plan', 'question_id': q['id'],
            'expected_revision': q['revision'], 'gap_id': q['remaining_gap_ids'][0],
            'result_ids': [r['evidence_id'] for r in self.x.results],
            'why_it_matters': 'Compare the retained request and process records without collecting again',
            'support_rule': 'Both retained records explicitly share the same job identifier',
            'refute_rule': 'The retained records explicitly identify different jobs',
            'inconclusive_rule': 'Names without a shared identifier do not establish a connection',
            'limits': ['These records do not prove authorization or successful completion'],
            'reopen_conditions': ['An independent record identifies the request and process'], **changes}

    def call(self, payload):
        return self.x.call('forsic_mission_from_results', payload)

    def physical_events(self):
        return [e for e in host.ledger(self.case)
            if e['kind'] in ('tool_start', 'tool_result') and e['data'].get('tool') in EVIDENCE_TOOLS]

    def persisted(self):
        state = current(self.case)
        return {key: state[key] for key in ('questions', 'missions', 'tests', 'assessments', 'observations', 'sources')}

    def rejected_without_mutation_or_execution(self, payload):
        before, physical = self.persisted(), self.physical_events()
        result = self.call(payload)
        self.assertIn('error', result, result)
        self.assertEqual(self.persisted(), before)
        self.assertEqual(self.physical_events(), physical)
        return result

    def test_retained_schema_survives_native_sanitization_without_execution_fields(self):
        from tools.schema_sanitizer import sanitize_tool_schemas
        from forsic_plugin.report_driven.investigation_state import compact_input_schemas
        schema = {'parameters':compact_input_schemas()['mission_from_results']}
        self.assertNotIn('forsic_mission_from_results',self.x.schemas)
        wire = sanitize_tool_schemas([{'type': 'function', 'function': schema}])[0]['function']['parameters']
        self.assertIn('result_ids', wire['required'])
        self.assertIn('gap_id', wire['required'])
        self.assertFalse(wire['additionalProperties'])
        for key in ('tool_name', 'tool_arguments', 'path', 'mission_id', 'mission_version'):
            self.assertNotIn(key, wire['properties'])

    def test_missing_empty_invalid_and_duplicate_result_ids_cannot_plan_or_execute(self):
        absent = self.payload(); absent.pop('result_ids')
        self.rejected_without_mutation_or_execution(absent)
        for ids in ([], None, '', [''], ['   '], [None], [self.x.results[0]['evidence_id']] * 2):
            with self.subTest(ids=ids):
                self.rejected_without_mutation_or_execution(self.payload(result_ids=ids))

    def test_foreign_retained_result_is_not_resolved_from_another_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            other, results, _ = fixture(tmp)
            foreign_before = current(other)
            rejected = self.rejected_without_mutation_or_execution(
                self.payload(result_ids=[self.x.results[0]['evidence_id'], results[0]['evidence_id']]))
            self.assertIn(results[0]['evidence_id'], rejected['error'])
            self.assertEqual(current(other), foreign_before)

    def test_retained_ids_mixed_with_any_execution_fields_fail_closed(self):
        for changes in ({'tool_name': 'forsic_read'}, {'tool_arguments': {'path': 'request.log'}},
                        {'path': 'request.log'}, {'tool_name': 'forsic_read', 'tool_arguments': {'path': 'request.log'}}):
            with self.subTest(changes=changes):
                self.rejected_without_mutation_or_execution(self.payload(**changes))

    def test_session_and_goal_ownership_are_preserved(self):
        before, physical = self.persisted(), self.physical_events()
        for session, goal in (('different-session', 'synthetic-goal'),
                              ('synthetic-session', 'different-goal')):
            with self.subTest(session=session, goal=goal):
                # Supply the resolved native conversation, not just a child
                # session ID that belongs to the fixture's same conversation.
                result = json.loads(self.case.invoke('forsic_reporting',
                    {'action': 'mission_from_results', 'payload': self.payload()}, session,
                    request_context={'goal_id': goal, 'conversation_id': session}))
                self.assertIn('error', result, result)
        self.assertEqual(self.persisted(), before)
        self.assertEqual(self.physical_events(), physical)

    def test_multiple_explicit_results_are_after_result_with_matching_receipt(self):
        payload = self.payload(); before = self.persisted(); physical = self.physical_events()
        saved = self.ok(self.call(payload)); receipt = saved['mission']
        stored = find(current(self.case), 'missions', receipt['id'])
        self.assertEqual(stored['design_timing'], 'after_result')
        self.assertEqual(stored['mission_kind'], 'evaluate_result')
        self.assertIsNone(stored['proposed_tool_name'])
        self.assertEqual(stored['tool_arguments'], {})
        self.assertEqual(stored['execution_start_ids'], [])
        self.assertEqual(stored['result_ids'], [])
        self.assertEqual([r['id'] for r in stored['reuse_result_refs']], payload['result_ids'])
        for ref in stored['reuse_result_refs']:
            self.assertEqual(ref['version'], find(current(self.case), 'sources', ref['id'])['version'])
        self.assertEqual(receipt['design_timing'], 'after_result')
        self.assertEqual(receipt['retained_result_ids'], payload['result_ids'])
        self.assertEqual(receipt['execution_result_ids'], [])
        self.assertEqual(receipt['retained_result_count'], 2)
        self.assertEqual(receipt['execution_result_count'], 0)
        self.assertEqual(receipt['omitted_retained_result_count'], 0)
        self.assertEqual(receipt['omitted_execution_result_count'], 0)
        self.assertNotIn('result_ids', receipt)
        self.assertNotIn('next_call', saved)
        self.assertFalse(saved['execution_authorized'])
        self.assertEqual(saved['next_tool'], 'forsic_assess')
        rules = receipt['assessment_rules']
        self.assertEqual(rules['purpose'], 'discriminate')
        self.assertEqual(rules['target_proposition'], stored['target_proposition'])
        for key in ('support_rule', 'refute_rule', 'inconclusive_rule'):
            self.assertEqual(rules[key], payload[key])
        self.assertEqual(rules['clipped_fields'], [])
        self.assertEqual(current(self.case)['questions'], before['questions'])
        self.assertEqual(current(self.case)['sources'], before['sources'])
        self.assertEqual(self.physical_events(), physical)
        self.assertEqual(self.case.event(saved['evidence_id'])['data']['tool'], 'forsic_reporting')
        self.assertEqual(self.case.event(saved['started_id'])['data']['arguments']['action'], 'mission_from_results')
        host.checked(current(self.case))

    def test_receipt_marks_omitted_ids_and_clipped_rules_without_losing_originals(self):
        results = list(self.x.results)
        for _ in range(19):
            results.append(self.ok(self.x.call('forsic_read', {'path': 'request.log'})))
        selected = [result['evidence_id'] for result in results]
        rule = 'An exact synthetic discriminator with preserved qualifications. ' * 12
        physical = self.physical_events()
        saved = self.ok(self.call(self.payload(result_ids=selected, support_rule=rule)))
        receipt = saved['mission']; stored = find(current(self.case), 'missions', receipt['id'])
        self.assertEqual(receipt['retained_result_ids'], selected[:20])
        self.assertEqual(receipt['retained_result_count'], 21)
        self.assertEqual(receipt['omitted_retained_result_count'], 1)
        self.assertEqual([ref['id'] for ref in stored['reuse_result_refs']], selected)
        self.assertEqual(stored['support_rule'], rule)
        self.assertIn('support_rule', receipt['assessment_rules']['clipped_fields'])
        self.assertNotEqual(receipt['assessment_rules']['support_rule'], rule)
        details = saved['details_call']
        full = self.ok(self.x.call(details['tool'], {**details['arguments'],
            'pointer': details['arguments']['pointer'] + '/support_rule', 'offset': 0, 'limit': 4096}))
        self.assertEqual(full['text'], rule)
        self.assertTrue(full['full_field'])
        self.assertEqual(self.physical_events(), physical)

    def test_existing_before_result_mission_still_executes_only_on_native_call(self):
        physical = self.physical_events()
        saved = self.ok(self.x.call('forsic_mission', self.x.plan()))
        self.assertEqual(saved['mission']['design_timing'], 'before_result')
        self.assertEqual(saved['mission']['retained_result_ids'], [])
        self.assertEqual(saved['mission']['execution_result_ids'], [])
        self.assertEqual(self.physical_events(), physical)
        call = saved['next_call']; result = self.ok(self.x.call(call['tool'], call['arguments']))
        stored = find(current(self.case), 'missions', saved['mission']['id'])
        self.assertEqual(stored['result_ids'], [result['evidence_id']])
        self.assertEqual(stored['reuse_result_refs'], [])
        self.assertEqual(result['mission_context']['mission_id'], stored['id'])
        replay = self.ok(self.x.call('forsic_mission', self.x.plan()))
        self.assertEqual(replay['mission']['execution_result_ids'], [result['evidence_id']])
        self.assertEqual(replay['mission']['retained_result_ids'], [])
        self.assertNotIn('next_call', replay)

    def test_same_operation_replays_once_after_revision_change_and_changed_payload_conflicts(self):
        payload = self.payload(); saved = self.ok(self.call(payload))
        q = self.question()
        self.ok(self.x.call('forsic_question', {'operation_id': 'after-retained-plan',
            'question_id': q['id'], 'expected_revision': q['revision'],
            'answer': q['answer'] + ' The wording is clarified.', 'reason': 'Explicit wording correction'}))
        before, physical = self.persisted(), self.physical_events()
        replay = self.ok(self.call(payload))
        self.assertEqual(replay['receipt'], saved['receipt'])
        self.assertEqual(replay['mission']['id'], saved['mission']['id'])
        self.assertEqual(self.persisted(), before)
        self.assertEqual(self.physical_events(), physical)
        self.rejected_without_mutation_or_execution({**payload, 'inconclusive_rule': 'Changed interpretation criterion'})
        self.assertEqual(len(current(self.case)['missions']), 1)

    def test_question_revision_cas_rejects_change_between_expansion_and_save(self):
        payload = self.payload(); original_write = host.write; changed = False
        physical = self.physical_events()
        def change_before_write(case, state, change, **kwargs):
            nonlocal changed
            if not changed and change.get('records', {}).get('missions'):
                changed = True
                q = self.question()
                self.ok(self.x.call('forsic_question', {'operation_id': 'racing-wording-change',
                    'question_id': q['id'], 'expected_revision': q['revision'],
                    'answer': q['answer'] + ' A concurrent edit.', 'reason': 'Synthetic concurrent edit'}))
            return original_write(case, state, change, **kwargs)
        with patch.object(host, 'write', side_effect=change_before_write):
            result = self.call(payload)
        self.assertTrue(changed)
        self.assertIn('error', result, result)
        self.assertFalse(current(self.case)['missions'])
        self.assertEqual(self.question()['revision'], payload['expected_revision'] + 1)
        self.assertEqual(self.physical_events(), physical)
        repaired = {**payload, 'expected_revision': self.question()['revision']}
        self.ok(self.call(repaired))
        self.assertEqual(len(current(self.case)['missions']), 1)

    def test_blank_discriminating_conditions_and_purpose_are_not_saved(self):
        for key in ('support_rule', 'refute_rule', 'inconclusive_rule', 'why_it_matters'):
            for text in ('', ' \t\n '):
                with self.subTest(key=key, text=text):
                    self.rejected_without_mutation_or_execution(self.payload(**{key: text}))
        for key in ('limits', 'reopen_conditions'):
            for values in ([], [''], [' \t ']):
                with self.subTest(key=key, values=values):
                    self.rejected_without_mutation_or_execution(self.payload(**{key: values}))

    def test_absent_rule_stays_absent_and_supports_cannot_bypass_old_guards(self):
        payload = self.payload(); payload.pop('support_rule'); payload.pop('refute_rule')
        saved = self.ok(self.call(payload)); mission = find(current(self.case), 'missions', saved['mission']['id'])
        self.assertEqual(saved['mission']['assessment_rules']['purpose'], 'discover')
        self.assertIsNone(saved['mission']['assessment_rules']['support_rule'])
        self.assertIsNone(saved['mission']['assessment_rules']['refute_rule'])
        result = self.x.results[0]
        source = find(current(self.case), 'sources', result['evidence_id'])
        located = self.ok(self.x.call('forsic_reporting', {'action': 'source',
            'source_id': result['evidence_id'], 'query': 'requested'}))
        basis = self.ok(self.x.call('forsic_assessment_basis', {'mission_id': mission['id'],
            'result_id': result['evidence_id'], 'citation_refs': [located['matches'][0]['citation_ref']]}))
        result = self.x.call('forsic_assess', {'operation_id': 'unsupported-claim',
            'basis_receipt_id': basis['evidence_id'], 'outcome': 'supports',
            'reasoning_summary': 'An observation cannot invent a missing support criterion',
            'limitations': ['No support rule was planned'], 'next_check': 'Plan a discriminating criterion explicitly'})
        self.assertIn('error', result, result)
        citation = self.case.event(located['evidence_id'])['data']['matches'][0]['citation']
        legacy = {'operation_id': 'legacy-invalid', 'expected_revision': self.question()['revision'],
            'mission_id': mission['id'], 'mission_version': mission['version'],
            'result_id': source['id'], 'result_version': source['version'], 'outcome': 'inconclusive',
            'reasoning_summary': 'Only retained evidence can support this assessment',
            'limitations': ['No execution identity'], 'next_check': 'Compare independent records',
            'citations': [citation]}
        for changed in ({'mission_version': 'stale-version'},
                        {'citations': [{**citation, 'literal': 'invented retained text'}]}):
            with self.subTest(changed=changed):
                result = json.loads(self.case.invoke('forsic_reporting', {'action': 'assess',
                    'payload': {**legacy, **changed}}, 'synthetic-session',
                    request_context={'goal_id': 'synthetic-goal', 'conversation_id': 'synthetic-session'}))
                self.assertIn('error', result, result)
        self.assertFalse(current(self.case)['assessments'])
        self.assertFalse(current(self.case)['observations'])


if __name__ == '__main__':
    unittest.main()
