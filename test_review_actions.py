"""Offline review inbox -> retained investigation -> atomic response contracts."""
from copy import deepcopy
import json
import unittest
import uuid
from unittest.mock import patch

from forsic_plugin import independent_review as review
from forsic_plugin.investigation_context import post_context_compaction, resolve_context
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.grounded_judgment import read_refs
from forsic_plugin.report_driven.review_actions import inbox
import test_executive_followup as fixtures
from test_report_driven import fixture


class ReviewActionsTests(unittest.TestCase):
    setUp = fixtures.ExecutiveFollowupTests.setUp
    bind = fixtures.ExecutiveFollowupTests.bind
    session = fixtures.ExecutiveFollowupTests.session
    save_note = fixtures.ExecutiveFollowupTests.save_note
    setup_review = fixtures.ExecutiveFollowupTests.setup_review
    add_sources = fixtures.ExecutiveFollowupTests.add_sources
    kwargs = fixtures.ExecutiveFollowupTests.kwargs
    response = fixtures.ExecutiveFollowupTests.response
    request_paths = fixtures.ExecutiveFollowupTests.request_paths
    resolved = fixtures.ExecutiveFollowupTests.resolved
    prepare = fixtures.ExecutiveFollowupTests.prepare
    call = fixtures.ExecutiveFollowupTests.call

    def args(self, action='review_inbox', **changes):
        resolved = self.resolved()
        return {'action': action, '_goal_id': resolved['goal_id'], '_session_id': self.sid,
            '_session_lineage': [self.sid], '_question_contract': 'v2',
            '_independent_review_options': {'session_id': self.sid,
                'intake_root': str(self.intake), 'native_db': str(self.native)}, **changes}

    def start(self):
        self.prepare()
        self.call()
        return host.invoke(self.case, self.args())

    def cite(self, session=None):
        data = {'tool': 'forsic_read', 'path': 'followup.log', 'lines': [
            {'line': 1, 'text': 'Observed configuration only; successful execution is unconfirmed.'}]}
        data['started_id'] = self.case.record('tool_start', {'tool': 'forsic_read',
            'arguments': {'path': data['path']}}, session or self.sid)
        sid = self.case.record('tool_result', data, session or self.sid)
        return read_refs(self.case, {**data, 'evidence_id': sid})['presented_spans'][0]['citation_ref']

    def response_args(self, finding, **changes):
        question = host.invoke(self.case, self.args())['questions'][0]
        payload = {'operation_id': uuid.uuid4().hex, 'question_id': question['id'],
            'expected_revision': question['revision'], 'review_id': finding['review_id'],
            'finding_id': finding['finding_id'], 'disposition': 'investigated',
            'reason': 'The followup separates observed configuration from execution.',
            'citation_refs': [self.cite()], **changes}
        return self.args('review_response', payload=payload)

    def test_inbox_is_snapshot_independent_read_only_and_never_calls_model(self):
        initial = self.start()
        before = self.case.db.read_bytes()
        with (patch.object(host, 'current', side_effect=AssertionError('No report snapshot replay')),
                patch.object(review, '_collect', side_effect=AssertionError('No model payload')),
                patch('agent.auxiliary_client.call_llm', side_effect=AssertionError('No model call'))):
            result = host.invoke(self.case, self.args(snapshot_id='unrelated-old-snapshot'))
        self.assertEqual(self.case.db.read_bytes(), before)
        self.assertEqual(result, initial)
        self.assertEqual(result['pending_count'], 5)
        self.assertEqual(len(result['findings']), 5)
        self.assertEqual(result['questions'][0]['revision'], self.note['revision'])
        self.assertFalse(result['semantic_approval'])
        for f in result['findings']:
            self.assertTrue(f['question']); self.assertTrue(f['next_check'])
            origin = f['source_reference']
            self.assertTrue(origin['source_version'].startswith('event-sha256:'))
            self.assertTrue(origin['pointer'].startswith('/'))
            source = host.invoke(self.case, self.args(**f['source_lookup']['arguments']))
            self.assertEqual(source['source_ref']['id'], origin['source_id'])
            from forsic_plugin.report_driven.model_pages import select
            actual = select(source['result'], origin['pointer'])
            self.assertEqual(origin['quote'], actual if isinstance(actual, str) else review._json(actual))
            self.assertEqual(origin['source_version'], review._retained_version(self.case, origin['source_id']))

    def test_finding_lookup_requires_owned_exact_ids_and_current_question(self):
        item = self.start()['findings'][0]
        result = host.invoke(self.case, self.args(review_id=item['review_id'], finding_id=item['finding_id'],
                                                question_id='Q-'+self.note['note_id']))
        self.assertEqual(len(result['findings']), 1)
        for extra in ({'finding_id': item['finding_id']}, {'review_id': 'foreign'},
                      {'review_id': item['review_id'], 'finding_id': 'invented'}, {'question_id': 'Q-foreign'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                host.invoke(self.case, self.args(**extra))

    def test_cross_case_session_and_goal_cannot_read_or_answer_receipts(self):
        item = self.start()['findings'][0]
        other, _, _ = fixture(self.intake/'cases'/'other')
        with self.assertRaises(ValueError): inbox(other, self.args(review_id=item['review_id']))
        for key, value in (('_goal_id', 'other-goal'), ('_session_id', 'other-conversation')):
            with self.assertRaises(ValueError): host.invoke(self.case, self.args(**{key: value}))
        self.session('other-session', goal=self.goal); self.bind('other-session', self.case)
        foreign = self.args(review_id=item['review_id'], _session_id='other-session')
        foreign['_independent_review_options']['session_id'] = 'other-session'
        with self.assertRaises(ValueError): host.invoke(self.case, foreign)
        bad = self.response_args(item); bad['_session_id'] = 'other-session'
        before = self.case.db.read_bytes()
        with self.assertRaises(ValueError): host.invoke(self.case, bad)
        self.assertEqual(self.case.db.read_bytes(), before)

    def test_actual_investigation_persists_response_revision_and_idempotent_receipt(self):
        item = self.start()['findings'][0]
        args = self.response_args(item)
        result = host.invoke(self.case, args)
        state = host.current(self.case)
        question = next(q for q in state['questions'] if q['id'] == args['payload']['question_id'])
        self.assertEqual(question['revision'], args['payload']['expected_revision']+1)
        self.assertEqual(question['review_responses'][0]['citations'][0]['literal'],
                         'Observed configuration only; successful execution is unconfirmed.')
        before = self.case.db.read_bytes()
        self.assertEqual(host.invoke(self.case, args), result)
        self.assertEqual(self.case.db.read_bytes(), before)
        viewed = host.invoke(self.case, self.args(review_id=item['review_id'], finding_id=item['finding_id']))
        self.assertEqual(viewed['findings'][0]['status'], 'answered')
        self.assertEqual(viewed['pending_count'], 4)
        self.assertFalse(viewed['executive_followup']['rounds'][0]['complete'])
        changed = deepcopy(args); changed['payload']['reason'] = 'Changed operation payload'
        with self.assertRaises(ValueError): host.invoke(self.case, changed)
        stale = deepcopy(args); stale['payload']['operation_id'] = uuid.uuid4().hex
        with self.assertRaises(ValueError): host.invoke(self.case, stale)

    def test_lost_response_history_is_lazy_owned_and_requires_explicit_resubmission(self):
        item=self.start()['findings'][0]
        args=self.response_args(item)
        host.invoke(self.case,args)
        with self.case.connect() as db:
            note=json.loads(db.execute("SELECT data FROM events WHERE kind='note' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        note['revision']+=1
        note['investigation'].pop('review_responses')
        self.case.record('note',note,self.sid)  # Reproduce a historical writer losing dispositions.
        current=host.invoke(self.case,self.args())
        pending=next(f for f in current['findings'] if f['finding_id']==item['finding_id'])
        self.assertNotIn('historical_response',pending)
        detail=host.invoke(self.case,self.args(**pending['historical_response_lookup']['arguments']))
        prior=detail['findings'][0]['historical_response']
        self.assertEqual(prior['status'],'historical_not_current_accepted')
        self.assertEqual(prior['response']['citation_refs'],args['payload']['citation_refs'])
        self.assertEqual(prior['response']['reason'],args['payload']['reason'])
        self.assertEqual(detail['pending_count'],5)
        # Exact native note history coordinates recover the original saved note.
        read=prior['read_call']['arguments']
        from forsic_plugin import notes
        history=notes.model_read(self.case,read)
        self.assertEqual(history['history'][0]['investigation']['review_responses'][0]['reason'],args['payload']['reason'])
        args['payload'].update(operation_id=uuid.uuid4().hex,expected_revision=note['revision'])
        host.invoke(self.case,args)
        self.assertEqual(host.invoke(self.case,self.args())['pending_count'],4)

    def test_invalid_disposition_reason_evidence_and_limitation_never_write(self):
        item = self.start()['findings'][0]
        args = self.response_args(item)
        cases = [{'citation_refs': []}, {'citation_refs': ['R1:advice-is-not-evidence']},
                 {'citation_refs': ['C2:invalid']}, {'disposition': 'accepted'}, {'reason': ''},
                 {'disposition': 'scope_limited', 'citation_refs': []},
                 {'citation_refs': [self.cite('foreign-session')]}, {'operation_id': ''},
                 {'expected_revision': True}, {'finding_id': 'invented'}]
        for changes in cases:
            bad = deepcopy(args); bad['payload'].update(changes)
            before = self.case.db.read_bytes()
            with self.subTest(changes=changes), self.assertRaises(ValueError): host.invoke(self.case, bad)
            self.assertEqual(self.case.db.read_bytes(), before)
        self.assertEqual(host.invoke(self.case, self.args())['pending_count'], 5)

    def test_five_explicit_answers_per_round_complete_three_rounds_without_fourth_call(self):
        result = self.start()
        for number in range(1, 4):
            self.assertEqual(result['pending_count'], 5)
            self.assertTrue(all(f['executive_round'] == number for f in result['findings']))
            for index, item in enumerate(result['findings']):
                args = self.response_args(item, disposition='scope_limited', citation_refs=[],
                    reason='The acquired scope lacks the required history.',
                    limitation='The execution history is unavailable in the acquired records.')
                host.invoke(self.case, args)
                current = host.invoke(self.case, self.args())
                self.assertEqual(current['executive_followup']['rounds'][number-1]['answered'], index+1)
            self.assertEqual(current['executive_followup']['complete'], number == 3)
            self.assertEqual(len(self.calls), number)
            self.call()
            result = host.invoke(self.case, self.args())
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(result['pending_count'], 0)
        self.assertTrue(result['executive_followup']['complete'])

    def test_pending_ids_survive_native_compaction_and_lookup_without_reviewer_call(self):
        initial = self.start()
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('compressed-child', parent=self.sid)
        args = self.args()
        args['_independent_review_options']['session_id'] = 'compressed-child'
        with patch('agent.auxiliary_client.call_llm', side_effect=AssertionError('No model call')):
            post_context_compaction(session_id='compressed-child', intake_root=self.intake, native_db=self.native)
            result = host.invoke(self.case, args)
        self.assertEqual(result['pending_finding_ids'], initial['pending_finding_ids'])
        self.assertEqual([f['question'] for f in result['findings']], [f['question'] for f in initial['findings']])
        self.assertEqual(result['executive_followup']['rounds'][0]['answered'], 0)


if __name__ == '__main__': unittest.main()
