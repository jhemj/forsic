"""Machine-issued review reads and save bindings, with no model/network calls."""
from copy import deepcopy
import json
import unittest
import uuid
from unittest.mock import patch

from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.review_actions import _retained_lookup
import test_review_actions as fixtures


class ReviewInboxNavigationTests(unittest.TestCase):
    for _name in ('setUp', 'bind', 'session', 'save_note', 'setup_review', 'add_sources',
                  'kwargs', 'response', 'request_paths', 'resolved', 'prepare', 'call',
                  'args', 'start', 'cite', 'response_args'):
        locals()[_name] = getattr(fixtures.ReviewActionsTests, _name)
    del _name

    def test_inbox_read_calls_are_version_bound_and_do_not_adopt_citations(self):
        result = self.start()
        before = deepcopy(host.current(self.case)['questions'])
        calls = len(self.calls)
        for finding in result['findings']:
            lookup = finding['source_lookup']['arguments']
            self.assertIn('source_handle', lookup)
            self.assertNotIn('source_version', lookup)
            source = host.invoke(self.case, self.args(**lookup))
            self.assertEqual(source['source_ref']['id'], finding['source_reference']['source_id'])
            self.assertEqual(source['pointer'], finding['source_reference']['pointer'])
            self.assertNotEqual(source['source_ref']['version'], finding['source_reference']['source_version'])
            # Old review-event hashes never become aliases for source versions.
            with self.assertRaises(ValueError):
                host.invoke(self.case, self.args(**{**lookup,
                    'source_version': finding['source_reference']['source_version']}))
        self.assertEqual(host.current(self.case)['questions'], before)
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(host.invoke(self.case, self.args())['pending_count'], 5)

    def test_retained_tail_read_preserves_utf8_coordinates_and_case_binding(self):
        self.start()
        prefix, tail = '앞부분\n' * 1000, '검증한 마지막 기록'
        started = self.case.record('tool_start', {'tool': 'forsic_shell', 'arguments': {}}, self.sid)
        source_id = self.case.record('tool_result', {'tool': 'forsic_shell',
            'started_id': started, 'stdout': prefix + tail, 'exit_code': 0}, self.sid)
        from forsic_plugin.independent_review import _retained_version
        finding = {'source_id': source_id, 'pointer': '/stdout',
            'byte_start': len(prefix.encode('utf-8')),
            'source_version': _retained_version(self.case, source_id)}
        lookup = _retained_lookup(self.case, finding)['arguments']
        self.assertEqual(lookup['offset'], len(prefix.encode('utf-8')))
        read = host.invoke(self.case, self.args(**lookup))
        self.assertEqual(read['text'], tail)
        from test_report_driven import fixture
        other, _, _ = fixture(self.intake / 'cases' / 'different-ledger')
        with self.assertRaises(ValueError):
            host.invoke(other, self.args(**lookup))

    def test_changed_raw_review_source_never_mints_a_current_version_handle(self):
        from forsic_plugin.independent_review import _retained_version
        original = {'tool': 'forsic_read', 'text': 'Retained observation'}
        changed = {**original, 'text': 'A different observation'}
        variants = [json.dumps(changed), json.dumps(original, indent=2), 'corrupt retained JSON']
        for raw in variants:
            with self.subTest(raw=raw):
                sid = self.case.record('tool_result', original, self.sid)
                expected = _retained_version(self.case, sid)
                finding = {'source_id': sid, 'source_version': expected, 'pointer': '/text'}
                self.assertIn('source_handle', _retained_lookup(self.case, finding)['arguments'])
                with self.case.connect() as db:
                    db.execute('UPDATE events SET data=? WHERE id=?', (raw, sid))
                lookup = _retained_lookup(self.case, finding)
                self.assertEqual(lookup['status'], 'review_source_changed')
                self.assertEqual(lookup['review_source_version'], expected)
                self.assertEqual(lookup['retained_event_version'], _retained_version(self.case, sid))
                self.assertNotEqual(lookup['retained_event_version'], expected)
                self.assertEqual(lookup['arguments'], {'action': 'source', 'source_id': sid})
                self.assertNotIn('citation_ref', lookup)
                self.assertNotIn('source_handle', lookup['arguments'])

    def test_legacy_review_without_raw_version_requires_discovery(self):
        sid = self.case.record('tool_result', {'tool': 'forsic_read', 'text': 'retained'}, self.sid)
        lookup = _retained_lookup(self.case, {'source_id': sid, 'pointer': '/text'})
        self.assertEqual(lookup['status'], 'review_version_unavailable')
        self.assertEqual(lookup['arguments'], {'action': 'source', 'source_id': sid})

    def test_inbox_navigation_does_not_load_large_retained_body(self):
        self.start()
        from forsic_plugin.independent_review import MAX_RECEIPT
        source_id = self.case.record('tool_result', {'tool': 'forsic_shell',
            'stdout': 'x' * (MAX_RECEIPT + 1)}, self.sid)
        with patch.object(self.case, 'event', side_effect=AssertionError('No whole large source read')):
            lookup = _retained_lookup(self.case, {'source_id': source_id, 'pointer': '/stdout'})
        self.assertEqual(lookup['arguments'], {'action': 'source', 'source_id': source_id})

    def test_exact_current_goal_scaffold_saves_explicit_limit_and_refreshes_revision(self):
        result = self.start()
        owned = result['questions'][0]
        # Historical report notes stay readable, but cannot be response targets.
        old = deepcopy(self.note)
        old.update(note_id=uuid.uuid4().hex, goal_id='prior-goal', question='Historical report subject')
        self.case.record('note', old, self.sid)
        result = host.invoke(self.case, self.args())
        self.assertEqual(result['write_scope']['question_ids'], [owned['id']])
        finding = result['findings'][0]
        guide = finding['response_input']
        args = guide['save_call']['arguments']
        self.assertEqual(args['question_id'], owned['id'])
        self.assertEqual(args['expected_revision'], owned['revision'])
        for field in guide['save_call_incomplete']:
            self.assertNotIn(field, args)
        read = host.invoke(self.case, self.args(**guide['question_read_call']['arguments']))
        self.assertTrue(read)
        payload = {**args, 'operation_id': 'explicit-limited-response',
            'disposition': 'scope_limited', 'reason': 'Retained records do not answer the proposed new examination.',
            'citation_refs': [], 'limitation': 'No new collection is authorized in this report-only task; the missing execution history remains unknown.'}
        before = deepcopy(host.current(self.case)['questions'])
        with self.assertRaises(ValueError):
            host.invoke(self.case, self.args('review_response', payload={**payload,
                'question_id': 'Q-' + old['note_id']}))
        self.assertEqual(host.current(self.case)['questions'], before)
        accepted = host.invoke(self.case, self.args('review_response', payload=payload))
        after = deepcopy(host.current(self.case)['questions'])
        self.assertEqual(host.invoke(self.case, self.args('review_response', payload=payload)), accepted)
        self.assertEqual(host.current(self.case)['questions'], after)
        now = next(q for q in after if q['id'] == owned['id'])
        self.assertEqual(now['answer'], next(q['answer'] for q in before if q['id'] == owned['id']))
        self.assertEqual(now['review_responses'][0]['limitation'], payload['limitation'])
        self.assertEqual(now['review_responses'][0]['citations'], [])
        refresh = host.invoke(self.case, self.args(**guide['refresh_call']['arguments']))
        self.assertEqual(refresh['pending_count'], 4)
        self.assertEqual(refresh['findings'][0]['response_input']['save_call']['arguments']['expected_revision'],
                         owned['revision'] + 1)
        self.assertFalse(refresh['executive_followup']['complete'])

    def test_multiple_questions_require_explicit_target_selection(self):
        self.start()
        other = deepcopy(self.note)
        other.update(note_id=uuid.uuid4().hex, question='Second current question')
        self.case.record('note', other, self.sid)
        result = host.invoke(self.case, self.args())
        guide = result['findings'][0]['response_input']
        self.assertEqual(len(guide['question_choices']), 2)
        self.assertNotIn('save_call', guide)
        selected = host.invoke(self.case, self.args(question_id='Q-' + other['note_id']))
        self.assertEqual(selected['findings'][0]['response_input']['save_call']['arguments']['question_id'],
                         'Q-' + other['note_id'])


if __name__ == '__main__':
    unittest.main()
