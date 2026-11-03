"""Exact state/source navigation survives native pruning; no live/model traffic."""
from copy import deepcopy
from functools import partial
import json
import tempfile
import unittest

from agent.context_compressor import ContextCompressor
from tools.registry import registry
from forsic_plugin.context_results import summarize
from forsic_plugin.report_driven.gap_audit import audit
from forsic_plugin.report_driven.host import current, invoke, source_record
from forsic_plugin.report_driven.model_pages import MAX_BYTES, size, state_view, source_view, StateReadError
from test_report_driven import fixture


class RetainedNavigationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case, _, _ = fixture(self.tmp.name)

    def prune(self, args, body):
        name = 'synthetic_retained_navigation'
        registry.register(name, 'synthetic', {'parameters': {}}, lambda **kwargs: '',
                          result_summary=partial(summarize, 'forsic_reporting'))
        self.addCleanup(registry.deregister, name)
        messages = [{'role': 'tool', 'tool_call_id': 'read', 'message_uid': 'stable',
                     'content': json.dumps({**body, 'padding': 'x' * 10000})}]
        self.assertTrue(ContextCompressor._demote_tool_result_at(messages, 0,
            {'read': (name, json.dumps(args))}, 100))
        self.assertEqual(messages[0]['message_uid'], 'stable')
        self.assertLessEqual(len(messages[0]['content']), 2048)
        return json.loads(messages[0]['content'].split('\n', 1)[1])

    def test_full_inbox_prunes_to_exact_owned_single_finding_navigation(self):
        from test_review_actions import ReviewActionsTests
        fixture = ReviewActionsTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        inbox = fixture.start()
        before = deepcopy(current(fixture.case)['questions'])
        model_calls = len(fixture.calls)
        pruned = self.prune({'action': 'review_inbox'}, inbox)
        index = pruned['review_inbox']
        self.assertEqual(index['pending_count'], 5)
        self.assertEqual(index['write_scope']['question_ids'], inbox['write_scope']['question_ids'])
        self.assertEqual(index['questions'][0], {key: inbox['questions'][0][key] for key in ('id', 'revision')})
        self.assertEqual(len(index['findings']) + index['omitted_findings'], len(inbox['findings']))
        self.assertGreater(len(index['findings']), 0)
        call = index['findings'][0]['read_call']
        selected = invoke(fixture.case, fixture.args(**call['arguments']))
        self.assertEqual(len(selected['findings']), 1)
        finding = selected['findings'][0]
        self.assertEqual(finding['finding_id'], call['arguments']['finding_id'])
        self.assertEqual(finding['review_id'], call['arguments']['review_id'])
        guide = finding['response_input']
        self.assertEqual(guide['save_call']['arguments']['question_id'], inbox['questions'][0]['id'])
        self.assertEqual(guide['save_call']['arguments']['expected_revision'], inbox['questions'][0]['revision'])
        self.assertNotIn('reason', guide['save_call']['arguments'])
        source = invoke(fixture.case, fixture.args(**finding['source_lookup']['arguments']))
        self.assertEqual(source['source_ref']['id'], finding['source_reference']['source_id'])
        self.assertNotIn('quote', str(index['findings']))
        self.assertEqual(current(fixture.case)['questions'], before)
        self.assertEqual(len(fixture.calls), model_calls)

    def test_missing_state_pointer_recovers_to_verified_parent_without_mutation(self):
        state = current(self.case)
        before = deepcopy(state)
        args = {'action': 'state', 'snapshot_id': state['meta']['snapshot_id'], 'pointer': '/observed_scope'}
        failed = json.loads(self.case.invoke('forsic_reporting', args))
        self.assertEqual(failed['error_code'], 'state_pointer_absent')
        self.assertFalse(failed['write_applied'])
        pruned = self.prune(args, failed)
        self.assertEqual(pruned['failed_state'], args)
        self.assertEqual(pruned['next_call'], failed['next_call'])
        parent = invoke(self.case, pruned['next_call']['arguments'])
        self.assertEqual(parent['pointer'], '')
        child = next(item for item in parent['items'] if item['pointer'] == '/scope')
        selected = invoke(self.case, child['read_call']['arguments'])
        self.assertEqual(selected['pointer'], '/scope')
        self.assertEqual(current(self.case), before)

    def test_state_paging_and_child_calls_preserve_snapshot_and_native_pruning(self):
        state = current(self.case)
        overview = state_view(state, audit(state), {'action': 'state'})
        args = {**overview['sections']['questions']['read_call']['arguments'], 'limit': 1}
        first = state_view(state, audit(state), args)
        child = first['items'][0]['read_call']
        question = state_view(state, audit(state), child['arguments'])
        pruned = self.prune(child['arguments'], question)
        self.assertEqual(pruned['read_call'], question['read_call'])
        self.assertEqual(pruned['next_call'], question['next_call'])
        self.assertEqual(pruned['next_call']['arguments']['snapshot_id'], state['meta']['snapshot_id'])
        tail = state_view(state, audit(state), pruned['next_call']['arguments'])
        self.assertEqual(tail['offset'], question['next_offset'])
        stale = deepcopy(state); stale['meta']['snapshot_id'] = 'changed'
        with self.assertRaises(StateReadError) as error:
            state_view(stale, audit(stale), child['arguments'])
        self.assertEqual(error.exception.details['error_code'], 'state_snapshot_mismatch')
        self.assertEqual(error.exception.details['next_call']['arguments'], {'action': 'state'})
        for value in (overview, first, question, tail):
            self.assertLessEqual(size(value), MAX_BYTES)

    def test_selected_question_recovery_does_not_expand_scope(self):
        state = current(self.case); q = state['questions'][0]
        args = {'action': 'state', 'question_id': q['id'],
                'snapshot_id': state['meta']['snapshot_id'], 'pointer': '/sources'}
        with self.assertRaises(StateReadError) as error:
            state_view(state, audit(state), args)
        self.assertEqual(error.exception.details['next_call']['arguments'],
                         {'action': 'state', 'question_id': q['id']})
        recovered = state_view(state, audit(state), error.exception.details['next_call']['arguments'])
        self.assertTrue(all(item['read_call']['arguments']['question_id'] == q['id'] for item in recovered['items']))

    def test_invalid_state_offset_returns_explicit_reset_not_silent_clamp(self):
        state = current(self.case)
        args = {'action': 'state', 'snapshot_id': state['meta']['snapshot_id'], 'pointer': '/sources', 'offset': 10000}
        failed = json.loads(self.case.invoke('forsic_reporting', args))
        self.assertEqual(failed['error_code'], 'state_page_invalid')
        pruned = self.prune(args, failed)
        self.assertEqual(pruned['failed_state']['offset'], 10000)
        call = pruned['next_call']['arguments']
        self.assertEqual(call['offset'], 0)
        self.assertEqual(call['pointer'], '/sources')
        self.assertNotIn('items', failed)
        self.assertNotIn('error', invoke(self.case, call))

    def test_source_bad_offset_recovery_pins_exact_version_and_survives_pruning(self):
        source = current(self.case)['sources'][0]
        discovery = invoke(self.case, {'action': 'source', 'source_id': source['id']})
        args = {**discovery['read_call'], 'pointer': '/lines', 'offset': 10000}
        failed = json.loads(self.case.invoke('forsic_reporting', args))
        self.assertEqual(failed['error_code'], 'source_page_invalid')
        pruned = self.prune(args, failed)
        self.assertEqual(pruned['failed_source']['offset'], 10000)
        result = invoke(self.case, pruned['next_call'])
        self.assertEqual(result['source_ref'], discovery['source_ref'])
        self.assertEqual(result['pointer'], '/lines')
        self.assertEqual(result['offset'], 0)
        wrong = {**pruned['next_call'], 'source_version': 'changed'}
        with self.assertRaises(ValueError): invoke(self.case, wrong)

    def test_successful_source_page_keeps_exact_field_and_continuation(self):
        data = {'tool': 'forsic_read', 'lines': [{'text': 'source text' * 1500}]}
        source_id = self.case.record('tool_result', data, 'synthetic')
        source = source_record(self.case.event(source_id))
        args = {'action': 'source', 'source_id': source_id, 'source_version': source['version'],
                'pointer': '/lines/0/text', 'offset': 0, 'limit': 512}
        first = invoke(self.case, args)
        pruned = self.prune(args, first)
        self.assertEqual(pruned['read_call'], first['read_call'])
        self.assertEqual(pruned['next_call'], first['next_call'])
        second = invoke(self.case, pruned['next_call'])
        self.assertEqual(second['byte_start'], first['byte_end'])
        self.assertEqual(second['source_ref'], first['source_ref'])
        self.assertNotIn('text', pruned)


if __name__ == '__main__': unittest.main()
