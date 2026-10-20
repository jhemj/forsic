"""Synthetic receipt-to-chat contracts; no real models or case data."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from forsic_plugin import independent_review as review
from forsic_plugin import review_chat as chat
from forsic_plugin.investigation_context import resolve_context
import test_review_dispositions as fixtures


class ReviewChatTests(unittest.TestCase):
    setUp = fixtures.ReviewDispositionTests.setUp
    bind = fixtures.ReviewDispositionTests.bind
    session = fixtures.ReviewDispositionTests.session
    save_note = fixtures.ReviewDispositionTests.save_note
    setup_review = fixtures.ReviewDispositionTests.setup_review
    add_sources = fixtures.ReviewDispositionTests.add_sources
    kwargs = fixtures.ReviewDispositionTests.kwargs
    response = fixtures.ReviewDispositionTests.response
    request_paths = fixtures.ReviewDispositionTests.request_paths
    ledger = fixtures.ReviewDispositionTests.ledger
    dispose = fixtures.ReviewDispositionTests.dispose

    def resolved(self):
        return resolve_context(self.sid, intake_root=self.intake, native_db=self.native)

    def project(self, **kwargs):
        return chat.review_chat(self.resolved(), profile='synthetic', session_id=self.sid, **kwargs)

    def copy_receipt(self, *, goal=None, conversation=None, round=None, rid='historical-review'):
        original = self.request_paths()[0]
        request = deepcopy(review._read(original))
        response = deepcopy(review._response_for(original, request))
        request['request_id'] = rid
        if goal is not None: request['identity']['goal_id'] = goal
        if conversation is not None: request['identity']['conversation_id'] = conversation
        if round is not None: request['executive_round'] = round
        path = original.with_name('independent-review-synthetic-'+rid+'-request.json')
        review._immutable(path, request)
        response['request_id'] = rid
        response['request_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        review._immutable(path.with_name(path.name.replace('-request.json', '-response.json')), response)
        return path

    def test_real_receipt_read_only_stable_identity_public_whitelist_and_cache(self):
        self.setup_review()
        review.goal_advisory_context(**self.kwargs())
        before = self.ledger()
        with patch.object(review, 'freshness', wraps=review.freshness) as fresh:
            first = self.project()
            second = self.project()
        self.assertEqual(fresh.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.ledger(), before)
        card = first['items'][0]
        self.assertEqual(card['speaker'], 'ren')
        self.assertEqual(card['kind'], 'review_question')
        self.assertEqual(card['time_basis'], 'response_observed')
        self.assertEqual(card['freshness'], 'current')
        self.assertFalse(card['semantic_approval'])
        text = json.dumps(first)
        for secret in ('PRIVATE_', 'raw_advice', 'messages', 'reasoning_content', 'quote', 'case_manifest'):
            self.assertNotIn(secret, text)

    def test_no_receipts_produce_no_fake_ren_or_source_hash(self):
        with patch.object(review, 'freshness', side_effect=AssertionError('no evidence read')):
            result = self.project()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['items'], [])

    def test_legacy_time_and_unknown_delivery_are_system_status(self):
        self.setup_review()
        review.goal_advisory_context(**self.kwargs())
        path = self.request_paths()[0]
        response_path = path.with_name(path.name.replace('-request.json', '-response.json'))
        response_path.unlink()  # synthetic historical intent without delivery receipt
        item = self.project()['items'][0]
        self.assertEqual(item['speaker'], 'system')
        self.assertEqual(item['receipt_state'], 'delivery_unconfirmed')
        self.assertEqual(item['time_basis'], 'request_recorded')
        self.assertEqual(len(self.calls), 1)

    def test_other_conversation_excluded_and_old_goal_is_history(self):
        self.setup_review()
        review.goal_advisory_context(**self.kwargs())
        self.copy_receipt(goal='old-goal')
        self.copy_receipt(conversation='other-conversation', rid='foreign-review')
        cards = self.project()['items']
        self.assertEqual(len(cards), 2)
        history = next(c for c in cards if c['goal_id'] == 'old-goal')
        self.assertTrue(history['historical'])
        self.assertEqual(history['freshness'], 'stale')
        self.assertFalse(any(c['review_id'] == 'foreign-review' for c in cards))

    def test_disposition_is_validated_saved_record_not_native_message(self):
        self.setup_review()
        result = review.goal_advisory_context(**self.kwargs(stage='final'))
        self.dispose(result)
        card = self.project()['items'][0]
        self.assertEqual(card['disposition']['disposition'], 'investigated')
        self.assertTrue(card['response_ref']['question_id'].startswith('Q-'))
        self.assertNotIn('native_message_id', card)
        self.save_note(question='Changed proposition', review_responses=[])
        card = self.project()['items'][0]
        self.assertIsNone(card['disposition'])
        self.assertEqual(card['freshness'], 'stale')

    def test_response_tampering_is_unavailable_not_empty_success(self):
        self.setup_review()
        review.goal_advisory_context(**self.kwargs())
        path = self.request_paths()[0]
        target = path.with_name(path.name.replace('-request.json', '-response.json'))
        response = json.loads(target.read_text())
        response['request_sha256'] = 'wrong'
        target.write_text(json.dumps(response))
        self.assertEqual(self.project()['status'], 'unavailable')

    def test_valid_empty_and_incomplete_are_distinct_without_approval(self):
        self.setup_review()
        review.goal_advisory_context(**self.kwargs())
        path = self.request_paths()[0]
        target = path.with_name(path.name.replace('-request.json', '-response.json'))
        response = json.loads(target.read_text())
        response['raw_advice'] = '{"findings":[]}'
        target.write_text(json.dumps(response))
        empty = self.project()['items'][0]
        self.assertEqual(empty['public_text'], '추가 질문 없음')
        self.assertFalse(empty['semantic_approval'])
        response['status'] = 'incomplete'
        response['finish_reason'] = 'length'
        target.write_text(json.dumps(response))
        incomplete = self.project()['items'][0]
        self.assertEqual(incomplete['receipt_state'], 'incomplete')
        self.assertEqual(incomplete['speaker'], 'system')

    def test_three_executive_rounds_project_fifteen_real_questions_without_another_call(self):
        self.setup_review()
        review.goal_advisory_context(**self.kwargs())
        for number in range(1, 4):
            original = self.request_paths()[0]
            request = deepcopy(review._read(original))
            response = deepcopy(review._response_for(original, request))
            rid = 'executive-round-'+str(number)
            request.update(request_id=rid, stage='final', executive_round=number,
                           created_ns=request['created_ns']+number*1000000000)
            finding = json.loads(response['raw_advice'])['findings'][0]
            findings = [dict(finding, question=f'라운드 {number} 질문 {index}') for index in range(5)]
            path = original.with_name('independent-review-synthetic-'+rid+'-request.json')
            review._immutable(path, request)
            response.update(request_id=rid, raw_advice=json.dumps({'findings':findings}),
                            request_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            review._immutable(path.with_name(path.name.replace('-request.json','-response.json')), response)
        cards = [item for item in self.project()['items'] if item['kind']=='executive_question']
        self.assertEqual(len(cards), 15)
        self.assertEqual([item['round'] for item in cards], [1]*5+[2]*5+[3]*5)
        self.assertEqual(len(self.calls), 1)

    def test_compressed_native_lineage_preserves_receipt_and_branch_excludes_it(self):
        self.setup_review()
        review.goal_advisory_context(**self.kwargs())
        self.session(self.sid, goal=self.goal, end='compression')
        self.session('compressed', parent=self.sid)
        resolved = resolve_context('compressed', intake_root=self.intake, native_db=self.native)
        result = chat.review_chat(resolved, profile='synthetic', session_id='compressed')
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['conversation_id'], self.sid)
        self.session('branch', parent=self.sid, config={'_branched_from':self.sid})
        resolved = resolve_context('branch', intake_root=self.intake, native_db=self.native)
        self.assertEqual(chat.review_chat(resolved, profile='synthetic', session_id='branch')['items'], [])

    def test_review_pages_are_snapshot_bound_and_question_correction_is_stale(self):
        self.setup_review()
        review.goal_advisory_context(**self.kwargs())
        self.copy_receipt(goal='old-goal')
        with patch.object(chat, 'PAGE_SIZE', 1):
            first = self.project()
            second = self.project(cursor=first['next_cursor'])
            self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])
            self.assertIsNone(second['next_cursor'])
            self.note['target_proposition'] = 'A different proposition'
            self.save_note()
            with self.assertRaises(ValueError):
                self.project(cursor=first['next_cursor'])
            current = next(item for item in self.project()['items'] if not item['historical'])
            self.assertEqual(current['freshness'], 'stale')

    def test_unrelated_newer_receipts_do_not_starve_owned_conversation(self):
        self.setup_review()
        review.goal_advisory_context(**self.kwargs())
        for number in range(26):
            self.copy_receipt(conversation='another-conversation', rid='unrelated-'+str(number))
        result = self.project()
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['coverage']['owned_receipts'], 1)
        self.assertEqual(result['coverage']['omitted_receipts'], 0)
        self.assertEqual(result['coverage']['unscanned_receipts'], 0)

    def test_cursor_and_selected_scope_cannot_be_substituted(self):
        with self.assertRaises(ValueError):
            chat.review_chat(self.resolved(), profile='synthetic', session_id='other')
        with self.assertRaises(ValueError):
            self.project(cursor='forged:40')


if __name__ == '__main__':
    unittest.main()
