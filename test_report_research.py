"""Synthetic offline report handoff: native contracts/receipts, no model/network."""
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.report_driven.host import current, find
from forsic_plugin.report_driven.investigation_state import expand_mission
from forsic_plugin.report_driven.report_research import (
    DuplicateWorkError, duplicate_work, report_research_review,
    validate_no_duplicate_work, work_identity,
)
from forsic_plugin.report_driven.report_slots import bind, project_report_slots
from forsic_plugin.report_driven.gap_audit import audit
from test_report_driven import fixture


class ReportResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.case, self.results, _ = fixture(self.temp.name)
        self.binding = {'_goal_id': 'report-goal', '_session_id': 'report-session'}
        result = self.call('forsic_reporting', {'action': 'question', 'payload': {
            'operation_id': 'report-question', 'question': 'Does the request have an independent execution record?',
            'scope': 'The synthetic request and process records only',
            'answer': 'Undetermined; the two records have not been linked.',
            'critical_gaps': ['A shared execution identity remains unconfirmed'],
            'next_checks': ['Compare the independent execution identity'], 'priority': 'material'}})
        self.qid = result['question']['question_id']

    def tearDown(self):
        self.temp.cleanup()

    def call(self, tool, arguments, *, session='report-session', goal='report-goal'):
        result = json.loads(self.case.invoke(tool, arguments, session,
                            request_context={'goal_id': goal}))
        self.assertNotIn('error', result, result)
        return result

    def question(self, state=None):
        return find(state or current(self.case), 'questions', self.qid)

    def payload(self, state=None):
        state = state or current(self.case); q = self.question(state)
        return {'operation_id': 'native-plan', 'question_id': q['id'], 'expected_revision': q['revision'],
                'gap_id': q['remaining_gap_ids'][0], 'why_it_matters': 'Identify execution independently',
                'tool_name': 'forsic_read', 'tool_arguments': {'path': 'process.log'},
                'input_ids': [self.results[0]['evidence_id']],
                'support_rule': 'A shared identity is present', 'refute_rule': 'An explicit different identity is present',
                'inconclusive_rule': 'Names alone do not link execution',
                'limits': ['No actor attribution'], 'reopen_conditions': ['New independent execution record']}

    def plan(self):
        return self.call('forsic_reporting', {'action': 'mission', 'payload': self.payload()})['mission']

    def test_T12_real_native_handoff_and_question_writer(self):
        # Inventory exposes a real gap and editable existing native question,
        # not a new report-only question or an automatic investigative loop.
        s = current(self.case); before = deepcopy(s)
        review = report_research_review(s, self.binding)
        self.assertEqual(s, before)
        item = review['items'][0]
        self.assertEqual(item['question_writer']['tool'], 'forsic_question')
        self.assertEqual(item['gap_id'], self.question(s)['remaining_gap_ids'][0])
        self.call('forsic_reporting', {'action': 'question', 'payload': {
            'operation_id': 'clarify-next-discriminator', 'question_id': self.qid,
            'expected_revision': item['expected_revision'],
            'answer': 'Identity linkage remains undetermined; inspect the independent process record.',
            'next_checks': ['Compare the independent execution identity']}})
        m = self.plan(); s = current(self.case)
        review = report_research_review(s, self.binding)
        native = review['items'][0]['candidate_native_call']
        self.assertEqual(native['tool'], 'forsic_read')
        self.assertEqual(native['arguments']['mission_id'], m['id'])
        returned = self.call(native['tool'], native['arguments'])
        self.assertIn('run_id=R-77', returned['lines'][0]['text'])
        s = current(self.case); planned = find(s, 'missions', m['id'])
        self.assertIn(returned['evidence_id'], planned['result_ids'])
        self.assertEqual(self.case.event(returned['evidence_id'])['data']['mission_context'],
                         self.case.event(returned['started_id'])['data']['mission_context'])
        review = report_research_review(s, self.binding)
        self.assertNotIn('candidate_native_call', review['items'][0])
        self.assertFalse(review['items'][0]['retained_work']['replay_authorized'])

    def test_absent_and_stale_slots_are_editorial_only(self):
        s = current(self.case); q = self.question(s)
        before_gaps = deepcopy(s['gaps'])
        review = report_research_review(s, self.binding)
        self.assertTrue(all(x['missing_declaration'] for x in review['slot_status']))
        q['report_slots'] = bind(s, q, [{'slot': 'entry_route', 'state': 'unavailable',
            'brief': 'No direct discriminator is available in this scope.', 'claim_indices': [],
            'basis': 'The retained scope lacks an entry-route record.'}])
        self.assertFalse(next(x for x in project_report_slots(s)['slots'] if x['slot']=='entry_route')['entries'][0]['needs_recheck'])
        q['answer'] = 'The current answer changed; recheck the declaration.'
        review = report_research_review(s, self.binding)
        self.assertEqual(next(x for x in review['slot_status'] if x['slot']=='entry_route')['needs_recheck_count'], 1)
        self.assertEqual(s['gaps'], before_gaps)
        self.assertFalse(review['execution_authorized'])
        self.assertNotIn('report_slots_incomplete', audit(s)['scope_final_blockers'])

    def test_other_owner_cannot_edit_or_receive_native_candidates(self):
        self.plan(); s = current(self.case)
        review = report_research_review(s, {'_goal_id': 'other', '_session_id': 'other'})
        self.assertEqual(review['items'], [])
        result = json.loads(self.case.invoke('forsic_reporting', {'action': 'question', 'payload': {
            'operation_id': 'wrong-owner', 'question_id': self.qid,
            'expected_revision': self.question(s)['revision'], 'answer': 'Replace another owner answer'}},
            'other-session', request_context={'goal_id': 'other-goal'}))
        self.assertIn('error', result)
        self.assertEqual(current(self.case)['questions'], s['questions'])

    def test_exact_work_survives_unrelated_revision_and_has_source_recovery(self):
        m = self.plan()
        self.call('forsic_read', {'path': 'process.log', 'mission_id': m['id'], 'mission_version': m['version']})
        s = current(self.case); q = self.question(s)
        new = expand_mission(s, self.payload(s), goal_id='report-goal', session_id='report-session')
        old_key = work_identity(q, new)
        s['meta']['snapshot_id'] = 'unrelated-case-revision'
        q['revision'] += 10; q['version'] = 'unrelated-note-revision'
        self.assertEqual(work_identity(q, new), old_key)
        match = duplicate_work(s, q, new)
        self.assertEqual(match['receipt_state'], 'returned')
        self.assertTrue(match['source_calls'])
        self.assertFalse(match['whole_source_success'])
        with self.assertRaises(DuplicateWorkError) as caught:
            validate_no_duplicate_work(s, q, new)
        self.assertEqual(caught.exception.details['retained_work']['read_call']['arguments']['snapshot_id'], s['meta']['snapshot_id'])

    def test_actual_new_mission_cannot_repeat_returned_work_with_new_rationale(self):
        m = self.plan()
        returned = self.call('forsic_read', {'path': 'process.log', 'mission_id': m['id'], 'mission_version': m['version']})
        self.call('forsic_reporting', {'action': 'question', 'payload': {
            'operation_id': 'unrelated-case-revision', 'question': 'An unrelated display question?',
            'scope': 'Separate synthetic question only', 'answer': 'Undetermined'}})
        payload = self.payload()
        payload.update(operation_id='new-plan-id', why_it_matters='Same discriminator with editorial rationale change')
        result = json.loads(self.case.invoke('forsic_reporting', {'action': 'mission', 'payload': payload},
                        'report-session', request_context={'goal_id': 'report-goal'}))
        self.assertEqual(result['error_code'], 'identical_native_work_exists')
        self.assertIn(returned['evidence_id'], result['retained_work']['result_ids'])
        self.assertEqual(result['retained_work']['source_calls'][0]['arguments']['source_id'], returned['evidence_id'])
        self.assertEqual(len(current(self.case)['missions']), 1)
        # A real change of exact selected input is admissible through the same
        # native mission contract; no general read or changed-work prohibition.
        payload.update(operation_id='changed-exact-input')
        payload['tool_arguments']['max_lines'] = 5
        changed = self.call('forsic_reporting', {'action': 'mission', 'payload': payload})
        self.assertNotEqual(changed['mission']['id'], m['id'])

    def test_changed_selected_inputs_arguments_or_discriminator_allow_new_work(self):
        self.plan(); s = current(self.case); q = self.question(s)
        new = expand_mission(s, self.payload(s), goal_id='report-goal', session_id='report-session')
        for change in ('input_version', 'arguments', 'discriminator', 'scope'):
            other = deepcopy(new)
            if change == 'input_version': other['input_refs'][0]['version'] += '-new'
            if change == 'arguments': other['tool_arguments']['max_lines'] = 5
            if change == 'discriminator': other['support_rule'] = 'A different explicit identity test'
            if change == 'scope': other['exact_target_scope'] += ' extended'
            self.assertIsNone(duplicate_work(s, q, other), change)
            validate_no_duplicate_work(s, q, other)

    def test_running_unknown_partial_and_failed_do_not_authorize_replay(self):
        m = self.plan(); s = current(self.case); q = self.question(s)
        old = find(s, 'missions', m['id'])
        new = expand_mission(s, self.payload(s), goal_id='report-goal', session_id='report-session')
        old.update(state='running', execution_start_ids=['actual-start'], result_ids=[])
        match = duplicate_work(s, q, new)
        self.assertEqual(match['reuse'], 'query_existing_completion')
        with self.assertRaises(DuplicateWorkError): validate_no_duplicate_work(s, q, new)
        for status in ('failed', 'partial'):
            old.update(state=status, result_ids=[self.results[0]['evidence_id']])
            match = duplicate_work(s, q, new)
            self.assertEqual(match['receipt_state'], status)
            self.assertFalse(match['automatic_replay_authorized'])
            self.assertFalse(match['whole_source_success'])
            with self.assertRaises(DuplicateWorkError):
                validate_no_duplicate_work(s, q, new)
            # The existing mission itself remains outside new-plan admission.
            validate_no_duplicate_work(s, q, old)

    def test_actual_partial_success_cannot_be_replanned_or_replayed(self):
        state = current(self.case)
        legacy = expand_mission(state, self.payload(state), goal_id='report-goal', session_id='report-session')
        legacy['compact_contract'] = False
        legacy['mission_kind'] = find(state, 'gaps', legacy['gap_ref']['id'])['feasible_next_action']
        m = self.call('forsic_reporting', {'action': 'mission',
            'snapshot_id': state['meta']['snapshot_id'], 'payload': legacy})['mission']
        returned = self.call('forsic_read', {'path': 'process.log', 'mission_id': m['id'], 'mission_version': m['version']})
        state = current(self.case); source = find(state, 'sources', returned['evidence_id'])
        self.call('forsic_reporting', {'action': 'assess', 'payload': {
            'operation_id': 'inconclusive-native-success', 'expected_revision': self.question(state)['revision'],
            'mission_id': m['id'], 'mission_version': m['version'],
            'result_id': source['id'], 'result_version': source['version'], 'outcome': 'inconclusive',
            'reasoning_summary': 'The process record lacks the shared request identity.',
            'answer': 'The recorded process cannot yet be linked to the request.', 'citations': [],
            'limitations': ['One synthetic process record'], 'next_check': 'Read an independent identity record'}})
        self.assertEqual(find(current(self.case), 'missions', m['id'])['state'], 'partial')
        payload = self.payload()
        payload.update(operation_id='partial-new-id', why_it_matters='Editorial change after an inconclusive assessment')
        duplicate = json.loads(self.case.invoke('forsic_reporting', {'action': 'mission', 'payload': payload},
            'report-session', request_context={'goal_id': 'report-goal'}))
        self.assertEqual(duplicate['error_code'], 'identical_native_work_exists')
        self.assertEqual(duplicate['retained_work']['receipt_state'], 'partial')
        self.assertEqual(len(current(self.case)['missions']), 1)
        # Native execution still owns the existing mission's retry decision.
        with patch.object(self.case, 'read') as backend:
            replay = json.loads(self.case.invoke('forsic_read', {'path': 'process.log',
                'mission_id': m['id'], 'mission_version': m['version']}, 'report-session',
                request_context={'goal_id': 'report-goal'}))
        self.assertIn('error', replay)
        backend.assert_not_called()
        after = find(current(self.case), 'missions', m['id'])
        self.assertEqual(after['execution_start_ids'], [returned['started_id']])
        self.assertEqual(after['result_ids'], [returned['evidence_id']])

    def test_reporting_research_pointer_is_opt_in_paged_and_snapshot_bound(self):
        with patch('forsic_plugin.report_driven.report_research.report_research_review',
                   side_effect=AssertionError('Default overview must not compute research')):
            overview = self.call('forsic_reporting', {'action': 'state'})
        lookup = overview['conversation_scope']['report_research_call']
        self.assertEqual(lookup['arguments']['pointer'], '/report_research')
        response = self.call(lookup['tool'], lookup['arguments'])
        self.assertEqual(response['view_kind'], 'report_research')
        self.assertFalse(response['execution_authorized'])
        self.assertLessEqual(len(json.dumps(response, ensure_ascii=False).encode()), 6000)
        items = self.call('forsic_reporting', {'action': 'gaps', 'snapshot_id': response['snapshot_id'],
                          'question_id': self.qid, 'pointer': '/report_research/items'})
        self.assertEqual(items['items'][0]['value']['question_id'], self.qid)
        stale = json.loads(self.case.invoke('forsic_reporting', {'action': 'state',
                    'snapshot_id': 'stale', 'pointer': '/report_research'}, 'report-session',
                    request_context={'goal_id': 'report-goal'}))
        self.assertIn('State changed', stale['error'])

    def test_explicit_report_subset_preserves_inventory_scope_and_paging(self):
        other = self.call('forsic_reporting', {'action': 'question', 'payload': {
            'operation_id': 'second-report-question', 'question': 'A separate bounded impact question?',
            'scope': 'Independent synthetic impact scope', 'answer': 'Undetermined',
            'critical_gaps': ['First impact discriminator absent', 'Second impact discriminator absent'],
            'priority': 'material'}})['question']['question_id']
        state = current(self.case)
        first = self.question(state)
        first['report_slots'] = bind(state, first, [{'slot': 'entry_route', 'state': 'unavailable',
            'brief': 'Entry-route scope is unavailable.', 'claim_indices': [], 'basis': 'No entry-route record'}])
        first['answer'] = 'The first question changed and has a stale declaration.'
        selected = report_research_review(state, {**self.binding, 'question_ids': [other]})
        self.assertTrue(selected['items'])
        self.assertTrue(all(item['question_id'] == other for item in selected['items']))
        self.assertEqual(selected['conversation_scope']['editable_question_count'], 1)
        self.assertEqual(selected['declaration_rechecks'], [])
        self.assertEqual(next(row for row in selected['slot_status'] if row['slot']=='entry_route')['declared_count'], 0)
        overview = self.call('forsic_reporting', {'action': 'state', 'question_ids': [other]})
        lookup = overview['conversation_scope']['report_research_call']
        self.assertEqual(lookup['arguments']['question_ids'], [other])
        response = self.call('forsic_reporting', {**lookup['arguments'],
            'pointer': '/report_research/items', 'question_id': other, 'limit': 1})
        seen = []
        while True:
            seen.extend(item['value']['question_id'] for item in response['items'])
            self.assertEqual(response['read_call']['arguments']['question_ids'], [other])
            self.assertEqual(response['read_call']['arguments']['question_id'], other)
            if not response['next_call']:
                break
            self.assertEqual(response['next_call']['arguments']['question_ids'], [other])
            self.assertEqual(response['next_call']['arguments']['question_id'], other)
            response = self.call(response['next_call']['tool'], response['next_call']['arguments'])
        self.assertEqual(seen, [other, other])
        # A singular selection remains an additional filter, never an override.
        narrowed = report_research_review(state, {**self.binding,
            'question_ids': [self.qid, other], 'question_id': other})
        self.assertTrue(all(item['question_id'] == other for item in narrowed['items']))
        empty = report_research_review(state, {**self.binding,
            'question_ids': [other], 'question_id': self.qid})
        self.assertEqual(empty['items'], [])

    def test_render_subset_handoff_retains_exact_question_ids(self):
        other = self.call('forsic_reporting', {'action': 'question', 'payload': {
            'operation_id': 'render-subset-question', 'question': 'A bounded rendered impact question?',
            'scope': 'Only synthetic impact records', 'answer': 'Impact remains undetermined.',
            'critical_gaps': ['The independent impact discriminator is unavailable'],
            'priority': 'material'}})['question']['question_id']
        rendered = self.call('forsic_reporting', {'action': 'render', 'allow_partial': True, 'question_ids': [other]})
        handle = rendered['research_review_call']
        self.assertEqual(handle['arguments']['question_ids'], [other])
        self.assertEqual(handle['arguments']['snapshot_id'], rendered['snapshot_id'])
        response = self.call(handle['tool'], {**handle['arguments'], 'pointer': '/report_research/items'})
        self.assertTrue(response['items'])
        self.assertTrue(all(row['value']['question_id'] == other for row in response['items']))
        self.assertNotIn(self.qid, [row['value']['question_id'] for row in response['items']])
        self.assertEqual(response['read_call']['arguments']['question_ids'], [other])
        self.assertFalse(response['execution_authorized'])

    def test_report_subset_rejects_unknown_empty_and_other_owner_ids(self):
        state = current(self.case)
        foreign = next(q['id'] for q in state['questions'] if q['id'] != self.qid)
        for ids in ([], ['not-a-current-question'], [self.qid, 'not-a-current-question']):
            response = json.loads(self.case.invoke('forsic_reporting', {'action': 'state',
                'snapshot_id': state['meta']['snapshot_id'], 'pointer': '/report_research/items',
                'question_ids': ids}, 'report-session', request_context={'goal_id': 'report-goal'}))
            self.assertIn('Select current question_ids', response['error'])
        response = json.loads(self.case.invoke('forsic_reporting', {'action': 'state',
            'snapshot_id': state['meta']['snapshot_id'], 'pointer': '/report_research',
            'question_ids': [self.qid, foreign]}, 'report-session', request_context={'goal_id': 'report-goal'}))
        self.assertIn('current conversation and goal', response['error'])
        # Historical singular lookup retains its established read-only behavior.
        old = report_research_review(state, {**self.binding, 'question_id': foreign})
        self.assertEqual(old['items'], [])
        self.assertFalse(old['execution_authorized'])

    def test_paused_read_only_bounded_inventory_never_calls_native_tools(self):
        self.plan(); s = current(self.case); before = deepcopy(s)
        with patch.object(self.case, 'invoke', side_effect=AssertionError('No native call permitted')):
            review = report_research_review(s, self.binding, paused=True)
        self.assertEqual(s, before)
        self.assertTrue(review['paused'])
        self.assertFalse(review['execution_authorized'])
        self.assertFalse(review['limits']['resume_authorized'])
        self.assertFalse(any('candidate_native_call' in item for item in review['items']))
        self.assertLessEqual(len(json.dumps(review, ensure_ascii=False).encode()), 6000)


class ReportResearchRetryAdmissionTests(unittest.TestCase):
    def setUp(self):
        import test_mission_reference as references
        self.reference_fixture = references.MissionReferenceTests()
        self.reference_fixture.setUp()
        self.addCleanup(self.reference_fixture.doCleanups)

    def test_failed_new_mission_is_rejected_and_original_classified_retry_succeeds(self):
        from forsic_plugin.execution_contract import ExecutionFailure
        x = self.reference_fixture
        saved = x.plan(); mission_id = saved['mission']['id']
        failure = ExecutionFailure('synthetic confirmed transient timeout', error_code='parser_timeout',
            execution={'state': 'failed', 'termination_confirmed': True})
        with patch.object(x.x.case, 'image_files', side_effect=failure):
            failed = x.x.call('forsic_image_read_mission', x.reference(saved))
        self.assertEqual(find(current(x.x.case), 'missions', mission_id)['state'], 'failed')
        payload = {**x.x.plan(), 'operation_id': 'failed-work-new-mission-id',
            'why_it_matters': 'Same check with changed editorial rationale',
            'tool_name': 'forsic_image_files', 'tool_arguments': deepcopy(x.effective)}
        refused = x.x.call('forsic_mission', payload)
        self.assertEqual(refused['error_code'], 'identical_native_work_exists')
        self.assertEqual(refused['retained_work']['receipt_state'], 'failed')
        self.assertEqual(refused['retained_work']['result_ids'], [failed['evidence_id']])
        self.assertEqual(len(current(x.x.case)['missions']), 1)
        retry = failed['retry_call']
        self.assertEqual(retry['arguments']['mission_id'], mission_id)
        with patch.object(x.x.case, 'image_files', return_value=x.body) as backend:
            returned = x.invoke(retry['arguments'], goal_status='active')
        self.assertNotIn('error', returned, returned)
        backend.assert_called_once_with(x.effective)
        retained = find(current(x.x.case), 'missions', mission_id)
        self.assertEqual(retained['result_ids'], [failed['evidence_id'], returned['evidence_id']])
        self.assertEqual(len(current(x.x.case)['missions']), 1)
        x.rejected(retry['arguments'], goal_status='active')
        changed = {**payload, 'operation_id': 'changed-range-after-recovery',
            'tool_arguments': {**x.effective, 'start_line': 2}}
        admitted = x.x.call('forsic_mission', changed)
        self.assertNotIn('error', admitted, admitted)
        self.assertNotEqual(admitted['mission']['id'], mission_id)
        self.assertEqual(len(current(x.x.case)['missions']), 2)


if __name__ == '__main__':
    unittest.main()
