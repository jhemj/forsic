"""Synthetic immutable receipts + append-only answer revisions; no model calls."""
from copy import deepcopy
import json
import sqlite3
from contextlib import closing
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from forsic_plugin import independent_review as review
from forsic_plugin.independent_review_history import project_history
from forsic_plugin.dashboard import plugin_api
from forsic_plugin.evidence import Case
import test_investigation_view as fixtures


class ReviewHistoryTests(unittest.TestCase):
    setUp = fixtures.InvestigationViewTests.setUp
    append = fixtures.InvestigationViewTests.append
    saved_review_response = fixtures.InvestigationViewTests.saved_review_response
    review_receipt = fixtures.InvestigationViewTests.review_receipt
    native_profile = fixtures.InvestigationViewTests.native_profile

    def history(self, **kwargs):
        return project_history(self.resolved, profile='default', **kwargs)

    def receipt(self, rid, *, ordinal=0, goal='goal', index=0, count=1):
        self.review_receipt(review_id=rid, goal=goal)
        path = self.case.output / ('independent-review-' + rid + '-request.json')
        request = json.loads(path.read_text())
        request.update(stage='final', created_ns=index, executive_round=ordinal,
                       report_question_ids=['Q-n' if goal == 'goal' else 'Q-followup'])
        path.write_text(json.dumps(request))
        response_path = path.with_name(path.name.replace('-request.json', '-response.json'))
        response = json.loads(response_path.read_text())
        finding = json.loads(response['raw_advice'])['findings'][0]
        response['raw_advice'] = json.dumps({'findings': [dict(finding, question=f'Synthetic review {rid} question {i}?') for i in range(count)]})
        response_path.write_text(json.dumps(response))
        return review._parse(response['raw_advice'], {self.source_id: {'/lines/0/text': 'Oct 1 03:00 synthetic activity'}}, rid, executive_round=ordinal)

    def test_all_three_rounds_and_later_final_reviews_survive_handoff_and_pagination(self):
        answers = []
        for ordinal in range(1, 4):
            findings = self.receipt('round-' + str(ordinal), ordinal=ordinal, index=ordinal, count=5)
            answers.extend(self.saved_review_response(f, 'scope_limited') for f in findings)
            note = deepcopy(self.note)
            note.update(revision=ordinal + 1)
            note['investigation']['review_responses'] = deepcopy(answers)
            self.append('note', note)
        original = answers[0]['reason']
        answers[0]['reason'] = 'Later retained disposition with a changed explanation.'
        note['revision'] = 5
        note['investigation']['review_responses'] = deepcopy(answers)
        self.append('note', note)
        # More than both old receipt and finding display limits, across goals.
        for i in range(28):
            self.receipt('final-' + str(i), goal='new-goal', index=10 + i, count=3)
        self.resolved['goal_id'] = 'new-goal'
        before = self.case.db.read_bytes()
        with patch.object(Case, 'record', side_effect=AssertionError('read only')), \
             patch.object(review, 'goal_advisory_context', side_effect=AssertionError('no replay')), \
             patch.object(review, '_complete_review', side_effect=AssertionError('no model call')):
            first = self.history(limit=30)
            items = list(first['items'])
            page = first
            while page['next_offset'] is not None:
                page = self.history(offset=page['next_offset'], limit=30, snapshot=first['snapshot'])
                items.extend(page['items'])
        self.assertEqual(self.case.db.read_bytes(), before)
        self.assertEqual(first['total'], 99)
        self.assertEqual(len(items), 99)
        self.assertEqual(first['counts']['receipts'], 31)
        self.assertEqual(first['counts']['answered'], 15)
        self.assertEqual(first['counts']['pending'], 84)
        self.assertEqual([i['executive_round'] for i in items[:15]], [1]*5 + [2]*5 + [3]*5)
        self.assertFalse(items[0]['current_goal'])
        self.assertTrue(items[-1]['current_goal'])
        responses = items[0]['responses']
        self.assertEqual([a['reason'] for a in responses], [original, answers[0]['reason']])
        self.assertEqual([a['applicability'] for a in responses], ['historical_note', 'current_note'])
        self.assertEqual(len(items[1]['responses']), 1)  # copied forward, not a new answer
        self.assertEqual(items[-1]['responses'], [])
        self.assertEqual(items[-1]['status'], 'pending')

    def test_old_response_is_visible_but_cannot_answer_changed_question_definition(self):
        finding = self.receipt('old')[0]
        answer = self.saved_review_response(finding, 'scope_limited')
        note = deepcopy(self.note)
        note['investigation']['review_responses'] = [answer]
        self.append('note', note)
        changed = deepcopy(note)
        changed.update(revision=2, scope='A different scope')
        self.append('note', changed)
        item = self.history()['items'][0]
        self.assertEqual(item['status'], 'historical_answer')
        self.assertEqual(len(item['responses']), 1)
        self.assertEqual(item['responses'][0]['reason'], answer['reason'])
        self.assertEqual(item['responses'][0]['applicability'], 'historical_note')

    def test_exact_finding_goal_and_conversation_identity_required_and_foreign_case_excluded(self):
        finding = self.receipt('local')[0]
        answer = self.saved_review_response(finding, 'scope_limited')
        for key, value in [('goal_id', 'wrong-goal'), ('session_id', 'wrong-conversation')]:
            note = deepcopy(self.note)
            note.update(note_id=key, **{key: value})
            note['investigation']['review_responses'] = [answer]
            self.append('note', note)
        mismatched = deepcopy(answer)
        mismatched['finding_id'] = 'wrong-finding'
        note = deepcopy(self.note)
        note['investigation']['review_responses'] = [mismatched]
        self.append('note', note)
        self.receipt('foreign')
        foreign = self.case.output / 'independent-review-foreign-request.json'
        raw = json.loads(foreign.read_text())
        raw['identity']['case_id'] = 'another-case'
        foreign.write_text(json.dumps(raw))
        result = self.history()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['responses'], [])
        self.assertNotIn('foreign', json.dumps(result))

    def test_snapshot_change_rejects_page_and_incomplete_receipts_are_disclosed(self):
        self.receipt('one')
        first = self.history(limit=1)
        self.receipt('two', index=1)
        with self.assertRaises(ValueError):
            self.history(offset=1, limit=1, snapshot=first['snapshot'])
        path = self.case.output / 'independent-review-two-response.json'
        path.unlink()
        result = self.history()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['counts']['receipts'], 2)
        self.assertEqual(result['issues'][0]['status'], 'response_unavailable')

    def test_router_reuses_bound_session_case_and_rejects_mismatched_case(self):
        self.receipt('one')
        with patch.object(plugin_api, 'investigation_scope', return_value=self.resolved) as bound:
            with self.assertRaises(HTTPException) as rejected:
                plugin_api.review_history('session', 'default', 'other', 0, 30, None)
            self.assertEqual(rejected.exception.status_code, 409)
            result = plugin_api.review_history('session', 'default', 'synthetic', 0, 30, None)
            self.assertEqual(result['total'], 1)
            self.assertEqual(bound.call_args.args, ('session', 'default'))


    def test_compression_lineage_keeps_original_responses_and_rejects_native_branches(self):
        _, native = self.native_profile()
        with closing(sqlite3.connect(native)) as db, db:
            db.execute("UPDATE sessions SET end_reason='compression' WHERE id='session'")
            db.execute("INSERT INTO sessions VALUES ('compressed','session',NULL,'tui','{}','default')")
            db.execute("INSERT INTO sessions VALUES ('branch','session',NULL,'tui',?, 'default')", (json.dumps({'_branched_from': 'session'}),))
        self.resolved['_view_native_db'] = native
        finding = self.receipt('original')[0]
        answer = self.saved_review_response(finding, 'scope_limited')
        note = deepcopy(self.note)
        note.update(session_id='compressed', revision=2)
        note['investigation']['review_responses'] = [answer]
        self.append('note', note)
        branch = deepcopy(note)
        branch.update(note_id='branch-note', session_id='branch')
        branch['investigation']['review_responses'][0]['reason'] = 'Unrelated branch answer.'
        self.append('note', branch)
        result = self.history()
        self.assertEqual(len(result['items'][0]['responses']), 1)
        self.assertEqual(result['items'][0]['responses'][0]['reason'], answer['reason'])

    def test_missing_native_session_keeps_unlinked_original_answer_visible_without_adoption(self):
        _, native = self.native_profile()
        self.resolved['_view_native_db'] = native
        finding = self.receipt('original')[0]
        answer = self.saved_review_response(finding, 'scope_limited')
        note = deepcopy(self.note)
        note['investigation']['review_responses'] = [answer]
        self.append('note', note)
        with closing(sqlite3.connect(native)) as db, db:
            db.execute("DELETE FROM sessions")
        result = self.history()
        self.assertEqual(result['items'][0]['status'], 'pending')
        self.assertEqual(result['unlinked_responses'][0]['reason'], answer['reason'])
        self.assertEqual(result['unlinked_responses'][0]['validation'], 'unverified_saved_response')


    def test_changed_then_restored_response_is_retained_as_a_new_history_entry(self):
        finding = self.receipt('original')[0]
        answer = self.saved_review_response(finding, 'scope_limited')
        for revision, reason in enumerate([answer['reason'], 'Reconsidered answer.', answer['reason']], 2):
            note = deepcopy(self.note)
            note['revision'] = revision
            note['investigation']['review_responses'] = [dict(answer, reason=reason)]
            self.append('note', note)
        responses = self.history()['items'][0]['responses']
        self.assertEqual(len(responses), 3)
        self.assertEqual([r['reason'] for r in responses], [answer['reason'], 'Reconsidered answer.', answer['reason']])
        self.assertEqual([r['applicability'] for r in responses], ['historical_note', 'historical_note', 'current_note'])
