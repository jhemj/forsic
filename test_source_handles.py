"""Exact retained-source handles; offline synthetic ledgers, no model/network."""
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.context_results import source_recovery, summarize
from forsic_plugin.report_driven.assessment_basis import _source_choices
from forsic_plugin.report_driven.host import current, invoke, source_record
from forsic_plugin.report_driven.model_pages import MAX_BYTES, SourceVersionMismatch, size, source_view
from forsic_plugin.report_driven.source_handles import SourceHandleError, source_handle
from test_report_driven import fixture


class SourceHandleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case, self.results, _ = fixture(self.tmp.name)
        self.sid = self.results[0]['evidence_id']
        self.source = source_record(self.case.event(self.sid))
        self.handle = source_handle(self.case, self.source)

    def read(self, **args):
        return invoke(self.case, {'action': 'source', **args})

    def test_discovery_mints_deterministic_handle_and_preserves_exact_reference(self):
        selected = self.read(source_id=self.sid)
        self.assertEqual(selected['source_handle'], self.handle)
        self.assertEqual(selected['source_ref'], {'id': self.sid, 'version': self.source['version']})
        self.assertEqual(selected['read_call'], {'action': 'source', 'source_handle': self.handle,
                                               'pointer': '', 'offset': 0})
        root = invoke(self.case, selected['read_call'])
        self.assertEqual(root['pointer'], '')
        self.assertEqual(root['source_ref'], selected['source_ref'])
        self.assertLessEqual(size(root), MAX_BYTES)
        self.assertEqual(self.read(source_handle=self.handle)['source_ref'], selected['source_ref'])

    def test_explicit_selection_must_match_and_legacy_wrong_versions_still_fail(self):
        self.read(source_handle=self.handle, source_id=self.sid, source_version=self.source['version'])
        for args in ({'source_id': 'other'}, {'source_version': self.sid}, {'source_version': 'old'},
                     {'source_version': None}):
            with self.subTest(args=args), self.assertRaises(SourceHandleError):
                self.read(source_handle=self.handle, **args)
        for args in ({'pointer': ''}, {'source_version': 'old'}, {'source_version': self.sid}):
            with self.subTest(args=args), self.assertRaises(SourceVersionMismatch):
                self.read(source_id=self.sid, **args)
        page = self.read(source_id=self.sid, source_version=self.source['version'], pointer='/lines/0/text')
        self.assertIn('requested', page['text'])

    def test_corrupted_unsupported_and_cross_case_handles_fail_before_lookup(self):
        parts = self.handle.split('.')
        damaged = '.'.join([parts[0], parts[1], ('A' if parts[2][0] != 'A' else 'B') + parts[2][1:]])
        for handle in (damaged, 'sh2.' + '.'.join(parts[1:]), self.handle + '=', '', None, {}, 'x'*2049):
            with self.subTest(handle=handle), patch.object(self.case, 'event', side_effect=AssertionError('must not lookup')):
                with self.assertRaises(SourceHandleError):
                    self.read(source_handle=handle)
        with tempfile.TemporaryDirectory() as tmp:
            other, _, _ = fixture(tmp)  # Same public case_id, separate ledger.
            with patch.object(other, 'event', side_effect=AssertionError('must not lookup')):
                with self.assertRaises(SourceHandleError):
                    invoke(other, {'action': 'source', 'source_handle': self.handle})

    def test_stale_handle_rejects_changed_content_without_new_handle_or_page(self):
        event = deepcopy(self.case.event(self.sid))
        event['data']['lines'][0]['text'] = 'changed retained bytes'
        changed = source_record(event)
        self.assertNotEqual(changed['version'], self.source['version'])
        self.assertNotEqual(source_handle(self.case, changed), self.handle)
        for args in ({}, {'pointer': '/lines/0/text'}, {'query': 'changed'}):
            with self.subTest(args=args), patch.object(self.case, 'event', return_value=event):
                with self.assertRaises(SourceVersionMismatch) as caught:
                    self.read(source_handle=self.handle, **args)
                details = caught.exception.details
                self.assertEqual(details['failed_source']['source_handle'], self.handle)
                self.assertEqual(details['next_call'], {'action': 'source', 'source_id': self.sid})
                self.assertNotIn('source_handle', details)
                self.assertNotIn('result', details)

    def test_hash_covers_full_retained_result_and_ignores_key_order_only(self):
        event = self.case.event(self.sid)
        reordered = {**event, 'data': dict(reversed(list(event['data'].items())))}
        self.assertEqual(source_record(reordered)['version'], self.source['version'])
        changed = deepcopy(event)
        changed['data']['coverage_complete'] = 'different metadata'
        self.assertNotEqual(source_record(changed)['version'], self.source['version'])

    def test_pointer_and_utf8_paging_reuses_only_handle_and_preserves_limits(self):
        data = {'tool': 'forsic_read', 'text': '한글🙂' * 1600}
        sid = self.case.record('tool_result', data, 'synthetic')
        discovery = self.read(source_id=sid)
        root = invoke(self.case, discovery['read_call'])
        call = next(item['read_call'] for item in root['items'] if item['pointer'] == '/text')
        call['limit'] = 137
        parts = []
        while call:
            self.assertIn('source_handle', call)
            self.assertNotIn('source_id', call)
            self.assertNotIn('source_version', call)
            page = invoke(self.case, call)
            self.assertLessEqual(size(page), MAX_BYTES)
            self.assertLessEqual(page['byte_end']-page['byte_start'], 137)
            parts.append(page['text'])
            call = page['next_call']
            if call:
                self.assertEqual(call['limit'], 137)
        self.assertEqual(''.join(parts), data['text'])

    def test_query_continuations_and_exact_matches_preserve_citation_versions(self):
        data = {'tool': 'forsic_read', 'text': '가🙂' * 20}
        sid = self.case.record('tool_result', data, 'synthetic')
        discovery = self.read(source_id=sid)
        call = {'action': 'source', 'source_handle': discovery['source_handle'], 'query': '가🙂', 'limit': 2}
        hits = []
        while call:
            self.assertIn('source_handle', call)
            result = invoke(self.case, call)
            self.assertLessEqual(size(result), MAX_BYTES)
            for match in result['matches']:
                exact = invoke(self.case, match['next_call'])
                self.assertEqual(exact['text'], match['literal'])
                self.assertEqual(match['citation']['source_id'], sid)
                self.assertEqual(match['citation']['source_version'], result['source_ref']['version'])
                hits.append(match['byte_start'])
            call = result['next_call']
        self.assertEqual(hits, list(range(0, 140, 7)))

    def test_collection_and_escaped_query_budgets_with_handle(self):
        data = {'tool': 'forsic_read', **{str(i): 'x' * 3500 for i in range(35)}}
        sid = self.case.record('tool_result', data, 'synthetic')
        discovery = self.read(source_id=sid)
        self.assertLessEqual(size(discovery), MAX_BYTES)
        for item in discovery['items']:
            self.assertIn('source_handle', item['read_call'])
            self.assertLessEqual(size(invoke(self.case, item['read_call'])), MAX_BYTES)
        data = {'tool': 'forsic_read', 'text': '\x00' * 2000}
        sid = self.case.record('tool_result', data, 'synthetic')
        selected = self.read(source_id=sid)
        result = self.read(source_handle=selected['source_handle'], query='\x00' * 64, limit=10)
        self.assertTrue(result['matches'])
        self.assertLessEqual(size(result), MAX_BYTES)
        self.assertLessEqual(size(invoke(self.case, result['next_call'])), MAX_BYTES)

    def test_query_cursor_must_keep_exact_identity_and_query(self):
        data = {'tool': 'forsic_read', 'text': 'a' * 20}
        sid = self.case.record('tool_result', data, 'synthetic')
        result = self.read(source_id=sid, query='a', limit=1)
        for changes in ({'query': 'different'}, {'source_version': 'wrong'},
                        {'source_id': self.sid}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                invoke(self.case, {**result['next_call'], **changes})
        tampered = deepcopy(result['next_call'])
        tampered['locator_cursor']['source_version'] = 'wrong'
        with self.assertRaises(ValueError):
            invoke(self.case, tampered)

    def test_read_only_and_receipt_replay_for_new_handle_and_historical_inputs(self):
        before = self.case.db.read_bytes()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')):
            page = self.read(source_handle=self.handle, pointer='/lines/0/text')
        self.assertEqual(self.case.db.read_bytes(), before)
        args = {'action': 'source', 'source_handle': self.handle, 'pointer': '/lines/0/text'}
        for historical in (False, True):
            retained_args = ({'action': 'source', 'source_id': self.sid, 'source_version': self.source['version'],
                              'pointer': '/lines/0/text'} if historical else args)
            retained = (source_view(self.source, self.case.event(self.sid)['data'], retained_args)
                        if historical else page)
            started = self.case.record('tool_start', {'tool': 'forsic_reporting', 'arguments': retained_args}, 'synthetic')
            eid = self.case.record('tool_result', {**retained, 'tool': 'forsic_reporting', 'started_id': started}, 'synthetic')
            _, choices = _source_choices(self.case, current(self.case), eid)
            self.assertEqual(choices[0]['source_version'], self.source['version'])

    def test_loop_comparison_ignores_only_self_receipt_identity(self):
        from forsic_plugin.loop_comparison import project
        common = {'text': 'same retained output', 'nested': {'source_handle': self.handle}}
        first = {**common, 'evidence_id': self.sid, 'source_handle': self.handle,
                 'source_ref': {'id': self.sid, 'version': self.source['version']}}
        other_source = {**self.source, 'id': 'another-receipt'}
        other_handle = source_handle(self.case, other_source)
        second = {**common, 'evidence_id': other_source['id'], 'source_handle': other_handle,
                  'source_ref': {'id': other_source['id'], 'version': other_source['version']}}
        one = project('forsic_read', {'path': 'synthetic'}, json.dumps(first))
        two = project('forsic_read', {'path': 'synthetic'}, json.dumps(second))
        self.assertEqual(one, two)
        self.assertEqual(one['result']['nested']['source_handle'], self.handle)
        unrelated = project('forsic_read', {'path': 'synthetic'},
                            json.dumps({**second, 'source_handle': self.handle}))
        self.assertEqual(unrelated['result']['source_handle'], self.handle)
        self.assertNotEqual(one, unrelated)
        external = project('forsic_read', {'path': 'synthetic'},
                           json.dumps({**first, 'evidence_id': 'different-result'}))
        self.assertEqual(external['result']['source_handle'], self.handle)
        self.assertIn('source_ref', external['result'])

    def test_pruning_retains_only_bounded_handle_for_the_presented_source(self):
        selected = self.read(source_handle=self.handle)
        view = json.loads(summarize('forsic_reporting', {}, json.dumps(selected)))
        self.assertEqual(view['source_handle'], self.handle)
        for handle in ('damaged', 'x' * 513, source_handle(self.case, {**self.source, 'id': 'another'})):
            wrong = json.loads(summarize('forsic_reporting', {}, json.dumps({**selected, 'source_handle': handle})))
            self.assertNotIn('source_handle', wrong)

    def test_pointer_recovery_and_pruning_keep_executable_handle_call(self):
        failed = json.loads(self.case.invoke('forsic_reporting', {'action': 'source', 'source_handle': self.handle,
                                                                'pointer': '/result/absent'}))
        self.assertEqual(failed['error_code'], 'source_pointer_absent')
        self.assertEqual(failed['next_call']['source_handle'], self.handle)
        self.assertNotIn('source_version', failed['next_call'])
        kept = source_recovery(failed)
        self.assertEqual(kept['next_call'], failed['next_call'])
        self.assertEqual(kept['failed_source']['source_handle'], self.handle)
        invoke(self.case, kept['next_call'])
        selected = self.read(source_handle=self.handle)
        view = json.loads(summarize('forsic_reporting', {}, json.dumps(selected)))
        self.assertEqual(view['source_handle'], self.handle)
        self.assertEqual(view['lookup']['arguments'], {'action': 'source', 'source_id': self.sid,
                                                     'source_version': self.source['version']})
        rejected = json.loads(self.case.invoke('forsic_reporting', {'action': 'source', 'source_handle': 'damaged'}))
        self.assertEqual(rejected['error_code'], 'source_handle_rejected')
        self.assertEqual(source_recovery(rejected)['next_call'], {'action': 'state'})


if __name__ == '__main__':
    unittest.main()
