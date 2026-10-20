"""Complete registered source/citation handoff on disposable synthetic ledgers."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from forsic_plugin.report_driven import assessment_basis, host
import test_efficiency_contracts as efficiency
import test_grounded_judgment as grounded
import test_review_recovery_contract as review_fixtures


class SourceCallPathTests(unittest.TestCase):
    setUp = efficiency.EfficiencyContracts.setUp
    call = efficiency.EfficiencyContracts.call

    def page(self, **args):
        result = self.call('forsic_reporting', {'action': 'source', **args})
        self.assertNotIn('error', result, result)
        self.assertNotIn('citation_ref_unavailable', result, result)
        return result

    def retained(self, page):
        return {**self.case.event(page['evidence_id'])['data'],
                'evidence_id': page['evidence_id']}

    def test_registered_wrapper_avoids_replay_and_has_constant_exact_event_reads(self):
        source_id = self.results[0]['evidence_id']
        counts = []
        for traffic in (0, 60):
            for index in range(traffic):
                self.case.record('pre_api_request', {'request': 'x' * 4096}, 'other-session')
                self.case.record('tool_result', {'tool': 'forsic_read',
                    'path': 'unrelated-' + str(index), 'lines': [{'text': 'y' * 4096}]}, 'other-session')
            with (patch.object(host, 'current', side_effect=AssertionError('No current replay')),
                  patch.object(host, 'ledger', side_effect=AssertionError('No ledger replay')),
                  patch.object(self.case, 'event', wraps=self.case.event) as reads):
                page = self.page(source_id=source_id, query='requested')
            self.assertTrue(page['matches'][0]['citation_ref'].startswith('C1:'))
            receipt = self.case.event(page['evidence_id'])
            allowed = {source_id, page['evidence_id'], receipt['data']['started_id']}
            self.assertEqual({call.args[0] for call in reads.call_args_list}, allowed)
            counts.append(reads.call_count)
            state = host.current(self.case)
            self.assertEqual(assessment_basis._source_choices(self.case, None, page['evidence_id']),
                             assessment_basis._source_choices(self.case, state, page['evidence_id']))
            raw = self.retained(page)
            original = assessment_basis._source_choices
            with patch.object(assessment_basis, '_source_choices',
                              side_effect=lambda case, unused, eid: original(case, state, eid)):
                self.assertEqual(page, assessment_basis.citation_choices(self.case, raw))
        self.assertEqual(counts[0], counts[1])

    def test_all_page_shapes_match_full_projection_citation_semantics(self):
        source = host.source_record(self.case.event(self.results[0]['evidence_id']))
        for args in ({'query': 'requested'}, {'query': 'absent synthetic literal'},
                     {'pointer': '/lines/0/text'}, {'pointer': '/lines'},
                     {'pointer': '/path'}, {}):
            with self.subTest(args=args):
                page = self.page(source_id=source['id'], source_version=source['version'], **args)
                self.assertEqual(assessment_basis._source_choices(self.case, None, page['evidence_id']),
                    assessment_basis._source_choices(self.case, host.current(self.case), page['evidence_id']))
                self.assertNotIn('citation_ref', self.case.event(page['evidence_id'])['data'])

    def test_historical_source_receipt_contracts_keep_exact_replay(self):
        from forsic_plugin.report_driven.model_pages import source_view
        source = host.source_record(self.case.event(self.results[0]['evidence_id']))
        data = self.case.event(source['id'])['data']
        args = {'action': 'source', 'source_id': source['id'], 'source_version': source['version'],
                'pointer': '/lines/0/text'}
        for acquisition in (False, True):
            for continuation in (False, True):
                for coordinates in (False, True):
                    with self.subTest(acquisition=acquisition, continuation=continuation, coordinates=coordinates):
                        shown = source_view(source, data, args, include_acquisition_context=acquisition,
                            include_continuation=continuation, include_coordinate_calls=coordinates)
                        start = self.case.record('tool_start',
                            {'tool': 'forsic_reporting', 'arguments': args}, 'synthetic-session')
                        eid = self.case.record('tool_result',
                            {**shown, 'tool': 'forsic_reporting', 'started_id': start}, 'synthetic-session')
                        exact = assessment_basis._source_choices(self.case, None, eid)
                        self.assertEqual(exact,
                            assessment_basis._source_choices(self.case, host.current(self.case), eid))
                        self.assertEqual(exact[0], shown)
                        self.assertEqual(exact[1][0]['literal'], shown['text'])

    def test_invalid_native_receipt_source_version_bytes_and_roles_keep_legacy_checks(self):
        page = self.page(source_id=self.results[0]['evidence_id'], query='requested')
        receipt = self.case.event(page['evidence_id'])
        start_id = receipt['data']['started_id']
        source_id = self.results[0]['evidence_id']
        corruptions = [
            (page['evidence_id'], lambda row: row['data'].update(tool='forsic_read')),
            (page['evidence_id'], lambda row: row['data'].update(error='failed')),
            (page['evidence_id'], lambda row: row['data'].update(execution_rejected=True)),
            (page['evidence_id'], lambda row: row['data'].update(started_id='missing')),
            (page['evidence_id'], lambda row: row['data'].update(presentation_contract='unknown')),
            (page['evidence_id'], lambda row: row['data'].update(paging_contract='unknown')),
            (page['evidence_id'], lambda row: row['data'].update(source_coordinate_contract='unknown')),
            (page['evidence_id'], lambda row: row['data']['matches'][0]['citation'].update(byte_start=1)),
            (page['evidence_id'], lambda row: row['data']['matches'][0].update(role='metadata')),
            (start_id, lambda row: row.update(session='foreign-session')),
            (start_id, lambda row: row.update(kind='tool_result')),
            (start_id, lambda row: row['data'].update(tool='forsic_read')),
            (start_id, lambda row: row['data'].update(execution_rejected=True)),
            (start_id, lambda row: row['data']['arguments'].update(action='state')),
            (start_id, lambda row: row['data']['arguments'].update(source_id='foreign-source')),
            (start_id, lambda row: row['data']['arguments'].update(source_id={'invalid': 'id'})),
            (start_id, lambda row: row['data']['arguments'].update(source_version='stale')),
            (start_id, lambda row: row['data']['arguments'].update(source_handle='damaged')),
            (source_id, lambda row: row.update(kind='tool_start')),
            (source_id, lambda row: row['data'].update(tool='forsic_reporting')),
            (source_id, lambda row: row['data']['lines'][0].update(text='changed original field')),
        ]

        def replace(row):
            with self.case.connect() as db:
                db.execute('UPDATE events SET kind=?,session=?,data=? WHERE id=?',
                    (row['kind'], row['session'], json.dumps(row['data']), row['id']))

        def outcome(state):
            try:
                return assessment_basis._source_choices(self.case, state, page['evidence_id'])
            except (ValueError, KeyError, TypeError) as exc:
                return type(exc), str(exc)

        for event_id, mutate in corruptions:
            with self.subTest(event_id=event_id, mutate=mutate):
                original = self.case.event(event_id)
                changed = deepcopy(original)
                mutate(changed)
                replace(changed)
                try:
                    state = host.current(self.case)
                    legacy = outcome(state)
                    self.assertEqual(outcome(None), legacy)
                    self.assertIsInstance(legacy[1], str)
                    raw = self.retained(page)
                    actual = assessment_basis.citation_choices(self.case, raw)
                    original_choices = assessment_basis._source_choices
                    with patch.object(assessment_basis, '_source_choices',
                                      side_effect=lambda case, unused, eid: original_choices(case, state, eid)):
                        self.assertEqual(actual, assessment_basis.citation_choices(self.case, raw))
                finally:
                    replace(original)

    def test_returned_reference_saves_provisional_question_without_mission(self):
        page = self.page(source_id=self.results[0]['evidence_id'], query='requested')
        reference = next(item['citation_ref'] for item in page['matches'] if item['role'] == 'body')
        payload = grounded.GroundedJudgmentTests.payload(self, [reference])
        result = self.call('forsic_question', payload)
        self.assertNotIn('error', result, result)
        state = host.current(self.case)
        question = host.find(state, 'questions', result['question']['question_id'])
        self.assertEqual(question['answer'], payload['answer'])
        self.assertEqual(question['assessment'], 'undetermined')
        claim = host.find(state, 'claims', question['claim_refs'][0]['id'])
        observation = host.find(state, 'observations', claim['source_refs'][0]['id'])
        self.assertEqual(observation['literal'], 'requested')
        self.assertEqual(observation['presented_view'], 'exact_excerpt')
        self.assertFalse(state['missions'])
        host.checked(state)


class SourceReviewCallPathTests(unittest.TestCase):
    # The existing fixture supplies retained, validated reviewer receipts with
    # a local fake response. No transport or real model is used.
    for _name in ('setUp', 'bind', 'session', 'save_note', 'setup_review', 'add_sources',
                  'kwargs', 'response', 'request_paths', 'resolved', 'prepare', 'call',
                  'args', 'start', 'cite', 'response_args', 'adapter_call'):
        locals()[_name] = getattr(review_fixtures.ReviewRecoveryContractTests, _name)
    del _name

    def test_registered_c1_page_saves_answer_and_native_review_response(self):
        finding = self.start()['findings'][0]
        fake_calls = len(self.calls)
        read = self.adapter_call({'path': 'request.log'}, 'forsic_read')
        self.assertNotIn('error', read, read)
        with (patch.object(host, 'current', side_effect=AssertionError('No source replay')),
              patch.object(host, 'ledger', side_effect=AssertionError('No source ledger replay'))):
            page = self.adapter_call({'action': 'source', 'source_id': read['evidence_id'],
                                      'query': 'requested'}, 'forsic_reporting')
        self.assertNotIn('error', page, page)
        reference = next(item['citation_ref'] for item in page['matches'] if item['role'] == 'body')
        payload = grounded.GroundedJudgmentTests.payload(self, [reference])
        saved = self.adapter_call(payload, 'forsic_question')
        self.assertNotIn('error', saved, saved)
        qid = saved['question']['question_id']
        question = host.find(host.current(self.case), 'questions', qid)
        response = self.adapter_call({'operation_id': 'answer-review-from-c1',
            'question_id': qid, 'expected_revision': question['revision'],
            'review_id': finding['review_id'], 'finding_id': finding['finding_id'],
            'disposition': 'investigated', 'reason': 'The retained request does not establish execution.',
            'citation_refs': [reference]})
        self.assertNotIn('error', response, response)
        state = host.current(self.case)
        question = host.find(state, 'questions', qid)
        self.assertEqual(question['answer'], payload['answer'])
        self.assertEqual(question['review_responses'][0]['citations'][0]['literal'], 'requested')
        self.assertEqual(question['review_responses'][0]['citation_refs'], [reference])
        self.assertEqual(len(self.calls), fake_calls)
        inbox = self.adapter_call({'action': 'review_inbox', 'review_id': finding['review_id'],
                                  'finding_id': finding['finding_id']}, 'forsic_reporting')
        self.assertEqual(inbox['findings'][0]['status'], 'answered')
        host.checked(state)


if __name__ == '__main__':
    unittest.main()
