"""Offline failed broad save -> exact recovery -> individual retained dispositions."""
from copy import deepcopy
import json
import unittest
import uuid

from forsic_plugin.report_driven import host
from forsic_plugin.context_results import summarize
from forsic_plugin.report_driven.investigation_state import QuestionInputError
from forsic_plugin.report_driven.review_actions import response_schema
import test_review_recovery_contract as fixtures


class AgenticReviewSavesTests(unittest.TestCase):
    for _name in ('setUp', 'bind', 'session', 'save_note', 'setup_review', 'add_sources',
                  'kwargs', 'response', 'request_paths', 'resolved', 'prepare', 'call',
                  'args', 'start', 'cite', 'response_args', 'native_call', 'adapter_call',
                  'combined_update'):
        locals()[_name] = getattr(fixtures.ReviewRecoveryContractTests, _name)
    del _name

    def test_rejected_broad_draft_preserves_claims_and_returns_exact_owned_inbox(self):
        finding = self.start()['findings'][0]
        payload = self.combined_update(finding, reasoning_note='Do not silently reinterpret this inference.')
        before = deepcopy(host.current(self.case)['questions'])
        args = self.args('question', payload=payload)
        with self.assertRaises(QuestionInputError) as caught:
            host.invoke(self.case, args)
        details = caught.exception.details
        self.assertFalse(details['write_applied'])
        self.assertTrue(any(p['path'] == 'payload.reasoning_note' for p in details['invalid_fields']))
        self.assertEqual(host.current(self.case)['questions'], before)
        recovery = details['recovery']['review_response']
        self.assertIn('existing answer remains unchanged', recovery['when'])
        inbox = host.invoke(self.case, self.args(**recovery['read_call']['arguments']))
        self.assertEqual(inbox['pending_count'], 5)
        self.assertEqual(inbox['questions'][0]['id'], payload['question_id'])
        self.assertEqual(payload['reasoning_note'], 'Do not silently reinterpret this inference.')
        self.assertEqual(len(payload['judgment']['citation_refs']), 1)

    def test_citation_in_evidence_ids_has_exact_source_recovery_without_adoption(self):
        finding = self.start()['findings'][0]
        payload = self.combined_update(finding)
        citation = payload['judgment']['citation_refs'][0]
        payload['evidence_ids'] = [citation]
        before = deepcopy(host.current(self.case)['questions'])
        failed = self.adapter_call(payload, 'forsic_question')
        self.assertEqual(failed['error_code'], 'invalid_question_fields')
        self.assertFalse(failed['write_applied'])
        problem = next(p for p in failed['invalid_fields'] if p['path'] == 'payload.evidence_ids[0]')
        self.assertIn('judgment.citation_refs', problem['error'])
        read = problem['source_read_call']
        self.assertEqual(read['arguments']['source_id'], citation.split(':')[1])
        source = host.invoke(self.case, self.args(**read['arguments']))
        self.assertEqual(source['source_ref']['id'], citation.split(':')[1])
        self.assertEqual(host.current(self.case)['questions'], before)
        self.assertEqual(payload['evidence_ids'], [citation])
        self.assertEqual(host.invoke(self.case, self.args())['pending_count'], 5)
        compact = json.loads(summarize('forsic_question', payload,
            json.dumps({**failed, 'padding': 'x' * 10000})))
        self.assertEqual(compact['invalid_fields'][0]['source_read_call'], read)
        self.assertEqual(compact['recovery']['review_response']['tool'], 'forsic_review_response')
        self.assertLessEqual(len(json.dumps(compact, ensure_ascii=False, separators=(',', ':'))), 2000)
        # Analyst explicitly repairs the original selection; the intended new
        # answer and disposition are still committed together by the same writer.
        payload.pop('evidence_ids')
        payload['operation_id'] = uuid.uuid4().hex
        accepted = self.adapter_call(payload, 'forsic_question')
        self.assertNotIn('error', accepted, accepted)
        question = next(q for q in host.current(self.case)['questions'] if q['id'] == payload['question_id'])
        self.assertEqual(question['answer'], payload['answer'])
        self.assertEqual(question['evidence_ids'], [citation.split(':')[1]])
        self.assertEqual(len(question['review_responses']), 1)

    def test_five_individual_grounded_saves_preserve_answer_and_replay_each_receipt(self):
        result = self.start()
        original = deepcopy(host.current(self.case)['questions'][0])
        citation = self.cite()
        calls = len(self.calls)
        for index, finding in enumerate(result['findings']):
            current = host.invoke(self.case, self.args())
            item = next(f for f in current['findings'] if f['finding_id'] == finding['finding_id'])
            guide = item['response_input']
            payload = {**guide['identity_fields'], **guide['question_choices'][0],
                'operation_id': uuid.uuid4().hex, 'disposition': 'investigated',
                'reason': 'Retained configuration is observed; successful execution remains unconfirmed.',
                'citation_refs': [citation], 'limitation': ''}
            self.assertEqual(set(payload), set(response_schema(native=True)['required']))
            accepted = self.adapter_call(payload, guide['tool'])
            self.assertNotIn('error', accepted, accepted)
            after = deepcopy(host.current(self.case)['questions'])
            self.assertEqual(after[0]['answer'], original['answer'])
            self.assertEqual(after[0]['claim_refs'], original['claim_refs'])
            self.assertEqual(len(after[0]['review_responses']), index + 1)
            replay = self.adapter_call(payload, guide['tool'])
            self.assertEqual(replay['question'], accepted['question'])
            self.assertEqual(host.current(self.case)['questions'], after)
            remaining = host.invoke(self.case, self.args())
            self.assertEqual(remaining['pending_count'], 4 - index)
            self.assertEqual(len(self.calls), calls)
        self.assertTrue(remaining['executive_followup']['rounds'][0]['complete'])

    def test_invalid_individual_schema_and_reference_do_not_consume_pending_finding(self):
        finding = self.start()['findings'][0]
        valid = self.response_args(finding)['payload']
        for changes in ({'reasoning_note': 'unsupported'}, {'citation_refs': ['C2:invalid']},
                        {'finding_id': 'unknown'}, {'citation_refs': [self.cite('foreign-session')]}):
            before = deepcopy(host.current(self.case)['questions'])
            failed = self.adapter_call({**valid, **changes})
            self.assertIn('error', failed)
            if changes == {'citation_refs': ['C2:invalid']}:
                self.assertEqual(failed['error_code'], 'invalid_review_response')
                self.assertFalse(failed['write_applied'])
                self.assertEqual(failed['invalid_fields'][0]['path'], 'citation_refs[0]')
                self.assertIn('unchanged returned C1/C2', failed['invalid_fields'][0]['repair'])
                self.assertEqual(failed['recovery']['tool'], 'forsic_review_response')
            self.assertEqual(host.current(self.case)['questions'], before)
            self.assertEqual(host.invoke(self.case, self.args())['pending_count'], 5)


if __name__ == '__main__':
    unittest.main()
