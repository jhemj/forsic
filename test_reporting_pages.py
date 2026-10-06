"""Model-only reporting pages: offline, no live case writes or model/network."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_report_driven import fixture
from forsic_plugin.report_driven.host import current, invoke, source_record
from forsic_plugin.report_driven.gap_audit import audit
from forsic_plugin.report_driven.model_pages import MAX_BYTES, page, select, size, source_view, state_view


class ReportingPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.case, self.results, self.note = fixture(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def bounded(self, value):
        self.assertLessEqual(size(value), MAX_BYTES)
        # Existing evidence wrapper still fits below Hermes's 8k char floor.
        wrapped = json.dumps({'evidence_id':'E-test','tool':'forsic_reporting','started_id':'E-start',**value}, ensure_ascii=False)
        self.assertLess(len(wrapped), 8000)
        self.assertLess(len(wrapped.encode()), MAX_BYTES + 200)

    def test_overview_inventory_pages_keep_all_records_and_snapshot(self):
        s = current(self.case)
        s['sources'] = [{**s['sources'][0], 'id':'E-'+str(i)} for i in range(1000)]
        before = deepcopy(s)
        summary = state_view(s, audit(s), {'action':'state'})
        self.bounded(summary)
        self.assertTrue(summary['partial_view'])
        self.assertEqual(summary['omitted_counts']['sources'], 1000)
        self.assertNotIn('sources', summary['state'])
        found = []
        offset = 0
        while True:
            result = state_view(s, audit(s), {'action':'state','snapshot_id':summary['snapshot_id'], 'pointer':'/sources','offset':offset,'limit':25})
            self.bounded(result)
            found.extend(x['value'] for x in result['items'])
            if result['next_offset'] is None:
                break
            self.assertGreater(result['next_offset'], offset)
            offset = result['next_offset']
        self.assertEqual(found, s['sources'])
        self.assertEqual(s, before)

    def test_gaps_and_large_records_return_refs_not_hidden_truncation(self):
        s = current(self.case)
        s['gaps'][0]['reason'] = '긴 공백 조건🙂' * 10000
        result = state_view(s, audit(s), {'action':'gaps'})
        self.bounded(result)
        first = result['items'][0]
        self.assertFalse(first['presented'])
        self.assertEqual(first['ref']['id'], s['gaps'][0]['id'])
        record = state_view(s, audit(s), {'action':'state','snapshot_id':s['meta']['snapshot_id'],'pointer':first['pointer'],'limit':25})
        self.bounded(record)
        reason = next(x for x in record['items'] if x.get('key') == 'reason')
        self.assertFalse(reason['presented'])
        self.assertEqual(reason['pointer'], '/gaps/0/reason')

    def test_source_small_contract_and_case_reads_are_unchanged(self):
        s = current(self.case)
        source = s['sources'][0]
        before = self.case.db.read_bytes()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')):
            result = invoke(self.case, {'action':'source','source_id':source['id']})
            self.assertEqual(result['result'], self.case.event(source['id'])['data'])
            self.assertEqual(result['source'], source)
            self.bounded(result)
            invoke(self.case, {'action':'state'})
            invoke(self.case, {'action':'gaps'})
        self.assertEqual(self.case.db.read_bytes(), before)
        self.assertEqual(current(self.case), s)

    def test_long_single_line_unicode_source_roundtrip_exact_bytes(self):
        text = ('한글🙂\\\"\x00') * 4000
        data = {'tool':'forsic_read','started_id':'synthetic', 'lines':[{'line':1,'text':text}]}
        event = {'id':'E-long','data':data}
        source = source_record(event)
        first = source_view(source, data, {})
        self.bounded(first)
        self.assertEqual(first['source_ref']['version'], source['version'])
        offset = 0
        chunks = []
        while True:
            result = source_view(source, data, {'source_version':source['version'],'pointer':'/lines/0/text','offset':offset,'limit':4096})
            self.bounded(result)
            self.assertEqual(result['text'].encode(), text.encode()[result['byte_start']:result['byte_end']])
            self.assertFalse(result['full_field'])
            chunks.append(result['text'])
            if result['next_offset'] is None:
                break
            self.assertGreater(result['next_offset'], offset)
            offset = result['next_offset']
        self.assertEqual(''.join(chunks), text)

    def test_continuations_require_exact_snapshot_and_source_version(self):
        s = current(self.case)
        for extra in ({}, {'snapshot_id':'stale'}):
            with self.assertRaises(ValueError):
                state_view(s, audit(s), {'action':'state','pointer':'/sources',**extra})
        source = s['sources'][0]
        data = self.case.event(source['id'])['data']
        for extra in ({}, {'source_version':'stale'}):
            with self.assertRaises(ValueError):
                source_view(source, data, {'pointer':'/lines/0/text',**extra})
        self.assertTrue(source_view(source, data, {'pointer':'/lines/0/text','source_version':source['version']})['full_field'])

    def test_pointer_escapes_invalid_indices_and_byte_boundaries(self):
        self.assertEqual(select({'a/b':{'~value':['ok']}}, '/a~1b/~0value/0'), 'ok')
        for pointer in ('no-slash','/a~2b','/x/-1','/x/01','/x/999','/x/false','/x/０'):
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                select({'x':['a']}, pointer)
        for args in ({'offset':1}, {'offset':99}, {'offset':-1}, {'limit':0}, {'limit':1}, {'offset':True}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                page('🙂한글', '/text', args, {})

    def test_escaped_scalar_budget_and_exact_nonstring_value(self):
        result = page('\x00' * 20000, '/text', {'limit':4096}, {})
        self.bounded(result)
        self.assertGreater(result['next_offset'], 0)
        self.assertEqual(page(False, '/flag', {}, {})['value'], False)

    def test_registered_tool_has_paging_fields_no_new_tool(self):
        from unittest.mock import Mock
        from forsic_plugin import register
        ctx = Mock()
        ctx.get_config.side_effect = lambda k,d=None: str(Path(self.tmp.name)/'intake') if k == 'intake_root' else d
        register(ctx)
        schemas = {c.kwargs['name']:c.kwargs['schema'] for c in ctx.register_tool.call_args_list}
        for name in ('pointer','offset','limit','source_version'):
            self.assertIn(name, schemas['forsic_reporting']['parameters']['properties'])
        self.assertNotIn('read_file', schemas)


if __name__ == '__main__':
    unittest.main()
