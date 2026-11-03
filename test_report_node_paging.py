"""Complete authored units within the existing byte cap; no live model or data."""
from copy import deepcopy
import unittest

import test_report_narrative as fixtures
from forsic_plugin.report_driven import narrative
from forsic_plugin.report_driven.host import current, invoke
from forsic_plugin.report_driven.model_pages import MAX_BYTES, size, select


class ReportNodePagingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReportNarrativeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.case = self.fixture.case
        packet = invoke(self.case, {'action': 'compose', 'section': 'key_evidence'})
        self.entries = [dict(node_id='key_evidence.'+str(i),
            cells=['Observed request '+str(i), 'x'*1200, 'A completed request does not prove successful execution.'],
            cell_details=['', 'y'*1200, 'The selected source scope remains unchanged.'],
            basis_keys=['Q1'], status='saved') for i in range(3)]
        self.fixture.write('key_evidence', draft={'entries': self.entries})
        self.state = current(self.case)
        self.progress = narrative.status(self.state)
        self.snapshot = narrative._review_identity(self.progress)

    def read(self, pointer, **kw):
        return narrative.prepare(self.state, {'section':'review', 'snapshot_id':self.snapshot,
            'pointer':pointer, **kw}, self.case)

    def test_small_review_node_is_complete_even_with_small_child_limit(self):
        expected = self.progress['fields']['key_evidence']['entries'][0]
        result = self.read('/fields/key_evidence/entries/0', limit=2)
        self.assertTrue(result['complete_value'])
        self.assertEqual(result['value'], expected)
        self.assertIsNone(result['next_call'])
        self.assertLessEqual(size(result), MAX_BYTES)
        self.assertIn('edit_read_call', result)

    def test_entry_collection_packs_whole_nodes_and_exact_continuation(self):
        expected = self.progress['fields']['key_evidence']['entries']
        page = self.read('/fields/key_evidence/entries', limit=25)
        nodes = []
        calls = 0
        while True:
            calls += 1
            self.assertLessEqual(size(page), MAX_BYTES)
            self.assertTrue(all('value' in item for item in page['items']))
            nodes.extend(item['value'] for item in page['items'])
            for item in page['items']:
                exact = narrative.prepare(self.state, item['read_call']['arguments'], self.case)
                self.assertEqual(exact['value'], item['value'])
            if not page['next_call']:
                break
            page = narrative.prepare(self.state, page['next_call']['arguments'], self.case)
        self.assertEqual(nodes, expected)
        self.assertLessEqual(calls, len(expected))

    def test_oversize_node_falls_back_without_losing_content_or_mutating_state(self):
        state = deepcopy(self.state)
        state['report_narratives'][0]['fields']['key_evidence']['entries'][0]['cells'][1] = 'long '+ '가'*7000
        before = deepcopy(state)
        progress = narrative.status(state)
        page = narrative.prepare(state, {'section':'review',
            'snapshot_id':narrative._review_identity(progress), 'pointer':'/fields/key_evidence/entries/0'}, self.case)
        self.assertNotIn('value', page)
        self.assertFalse(page['complete_value'])
        self.assertLessEqual(size(page), MAX_BYTES)
        self.assertEqual(state, before)

    def test_offsets_and_stale_snapshot_still_rejected(self):
        with self.assertRaises(ValueError):
            self.read('/fields/key_evidence/entries', offset=100)
        with self.assertRaises(narrative.ReportAuthoringError):
            narrative.prepare(self.state, {'section':'review', 'snapshot_id':'rd2.stale',
                'pointer':'/fields/key_evidence/entries/0'}, self.case)


if __name__ == '__main__':
    unittest.main()
