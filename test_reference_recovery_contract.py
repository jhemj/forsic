"""Synthetic exact-selector recovery through native pruning and API construction."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from agent.context_compressor import ContextCompressor
from forsic_plugin.context_results import summarize
from forsic_plugin.report_driven.host import invoke, source_record
import test_native_context_transport as native
from test_report_driven import fixture
from tools.registry import registry


class ReferenceRecoveryContractTests(unittest.TestCase):
    name = 'synthetic_exact_reference_transport'
    register = native.NativeContextTransportTests.register
    messages = native.NativeContextTransportTests.messages
    prune = native.NativeContextTransportTests.prune
    wire = native.NativeContextTransportTests.wire

    def setUp(self):
        native.NativeContextTransportTests.setUp(self)
        self.case, self.results, _ = fixture(str(Path(self.temp.name) / 'case'))
        self.sid = self.results[0]['evidence_id']
        self.source = source_record(self.case.event(self.sid))
        self.selected = invoke(self.case, {'action': 'source', 'source_id': self.sid})
        self.received.clear()
        registry.deregister(self.name, scope=self.scope)
        def callback(args, content):
            self.received.append((deepcopy(args), content))
            return summarize('forsic_reporting', args, content)
        registry.register(self.name, 'synthetic', {'parameters': {}},
            lambda **kwargs: self.fail('Transport tests must not dispatch tools'),
            scope=self.scope, result_summary=callback)

    def transported(self, body, bridge, lean):
        # Force native pruning eligibility; the diagnostic has no source semantics.
        messages = self.messages(bridge, {**body, 'synthetic_diagnostic': 'x' * 4000})
        if lean:
            for n in range(10):
                messages.extend([{'role': 'assistant', 'content': 'Later synthetic round'},
                                 {'role': 'tool', 'tool_call_id': str(n), 'content': 'Recent'}])
            compressor = object.__new__(ContextCompressor)
            compressor.quiet_mode = True
            compressor._session_id = ''
            original = deepcopy(messages)
            messages = compressor._demote_stale_tail_tools(messages, 0)
            self.assertEqual(original[1], messages[1])
        else:
            self.prune(messages)
        content = self.wire(messages)[2]['content']
        self.assertTrue(self.received, 'Actual native registered summary callback must run')
        return json.loads(content.split('\n', 1)[1] if content.startswith('[Tool result summary]') else content)

    def test_exact_recovery_survives_pressure_and_lean_direct_and_bridge_paths(self):
        for bridge in (False, True):
            for lean in (False, True):
                with self.subTest(bridge=bridge, lean=lean):
                    self.received.clear()
                    kept = self.transported(self.selected, bridge, lean)
                    self.assertEqual(kept['source_handle'], self.selected['source_handle'])
                    args = kept['lookup']['arguments']
                    self.assertEqual(args, {'action': 'source', 'source_id': self.sid,
                                            'source_version': self.source['version']})
                    by_id = invoke(self.case, args)
                    by_handle = invoke(self.case, {'action': 'source', 'source_handle': kept['source_handle']})
                    self.assertEqual(by_id, by_handle)
                    self.assertEqual(by_id['source_ref'], self.selected['source_ref'])

    def test_invalid_handle_stays_rejected_and_alternative_requires_explicit_selection(self):
        for bridge in (False, True):
            for lean in (False, True):
                with self.subTest(bridge=bridge, lean=lean):
                    self.received.clear()
                    damaged = self.selected['source_handle'].rsplit('.', 1)[0] + '.AAAAAAAAAAAAAAAAAAAAAA'
                    args = {'action': 'source', 'source_handle': damaged, 'pointer': '/lines/0/text'}
                    failed = json.loads(self.case.invoke('forsic_reporting', args))
                    kept = self.transported(failed, bridge, lean)
                    self.assertEqual(kept['error_code'], 'source_handle_rejected')
                    self.assertEqual(kept['failed_source']['source_handle'], damaged)
                    self.assertEqual(kept['next_call'], {'action': 'state'})
                    self.assertNotIn('text', kept)
                    alternative = invoke(self.case, {'action': 'source', 'source_id': self.sid,
                        'source_version': self.source['version'], 'pointer': '/lines/0/text'})
                    self.assertEqual(alternative['text'], self.case.event(self.sid)['data']['lines'][0]['text'])
                    self.assertEqual(alternative['source_ref'], self.selected['source_ref'])

    def test_page_error_recovery_keeps_pointer_version_and_requires_explicit_retry(self):
        for bridge in (False, True):
            for lean in (False, True):
                with self.subTest(bridge=bridge, lean=lean):
                    self.received.clear()
                    args = {'action': 'source', 'source_handle': self.selected['source_handle'],
                            'pointer': '/lines', 'offset': 5}
                    failed = json.loads(self.case.invoke('forsic_reporting', args))
                    kept = self.transported(failed, bridge, lean)
                    self.assertEqual(kept['error_code'], 'source_page_invalid')
                    self.assertEqual(kept['failed_source']['offset'], 5)
                    self.assertEqual(kept['next_call'], {**args, 'offset': 0})
                    recovered = invoke(self.case, kept['next_call'])
                    self.assertEqual(recovered['pointer'], '/lines')
                    self.assertEqual(recovered['source_ref'], self.selected['source_ref'])
                    self.assertTrue(recovered['complete_value'])
                    self.assertIsNone(recovered['next_call'])


if __name__ == '__main__':
    unittest.main()
