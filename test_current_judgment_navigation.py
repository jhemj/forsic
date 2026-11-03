"""Synthetic, offline selected-judgment navigation through returned calls."""
from copy import deepcopy
import tempfile
import unittest

from test_report_driven import fixture
from forsic_plugin.report_driven.host import current
from forsic_plugin.report_driven.current_judgment import current_judgment, navigation_view
from forsic_plugin.report_driven.model_pages import MAX_BYTES, StateReadError, size, state_view


class CurrentJudgmentNavigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case, _, _ = fixture(self.tmp.name)
        self.state = current(self.case)
        q = self.state['questions'][0]
        self.state['questions'] = [
            {**deepcopy(q), 'id':'Q-history', 'goal_id':'old', 'session_id':'old'},
            {**deepcopy(q), 'id':'Q-selected', 'goal_id':'goal', 'session_id':'chat'}]
        self.binding = {'_goal_id':'goal', '_session_id':'chat'}
        self.q = self.state['questions'][1]
        self.state['claims'] = [dict(
            id='F-'+str(i), version='1', status='superseded' if i == 35 else 'adopted',
            statement='Synthetic claim '+str(i), scope='Synthetic scope',
            reasoning_summary=('긴 근거🙂' * 450 if i == 0 else 'Synthetic reason'),
            assumptions=['Synthetic assumption'], limitations=['Synthetic limit'])
            for i in range(36)]
        self.q['claim_refs'] = [{'id':c['id'], 'version':c['version']} for c in self.state['claims']]
        self.state['questions'][0]['claim_refs'] = [{'id':'F-history', 'version':'1'}]
        self.state['claims'].append(dict(id='F-history',version='1',status='adopted',
            statement='History only',scope='Historical scope'))
        gap = self.state['gaps'][0]
        self.state['gaps'] = [{**deepcopy(gap), 'id':'G-'+str(i),
            'question_ref':{'id':'Q-selected','version':'1'},
            'disposition':'resolved' if i % 2 else 'assessed_unresolved'} for i in range(34)]
        self.state['gaps'].append({**deepcopy(gap), 'id':'G-history',
            'question_ref':{'id':'Q-history','version':'1'}})
        self.before = deepcopy(self.state)
        self.db_before = self.case.db.read_bytes()
        self.calls = []

    def read(self, call):
        args = call['arguments']
        self.assertEqual(call['tool'], 'forsic_reporting')
        self.assertEqual(args['snapshot_id'], self.state['meta']['snapshot_id'])
        self.assertEqual(args['question_id'], self.selected_id)
        self.assertTrue(args['pointer'].startswith(self.root))
        self.calls.append(deepcopy(call))
        result = state_view(self.state, {}, {**args, **self.binding})
        self.assertLessEqual(size(result), MAX_BYTES)
        self.assertEqual(result['editable_in_current_conversation'], self.selected_id == 'Q-selected')
        self.assertTrue(result['derived'])
        self.assertFalse(result['original_evidence'])
        return result

    def materialize(self, call, expected):
        result = self.read(call)
        if 'text' in result:
            chunks = [result['text']]
            while result['next_call']:
                result = self.read(result['next_call'])
                chunks.append(result['text'])
            return ''.join(chunks)
        if 'items' not in result:
            return result['value']
        items = []
        while True:
            # Execute each returned child call, including fields already inline.
            for item in result['items']:
                key = item.get('key')
                value = expected[key] if key is not None else expected[len(items)]
                items.append((key, self.materialize(item['read_call'], value)))
            if result['next_call'] is None:
                break
            result = self.read(result['next_call'])
        return dict(items) if isinstance(expected, dict) else [v for _, v in items]

    def test_complete_selected_projection_and_history_use_returned_navigation(self):
        for index, question in enumerate(self.state['questions']):
            self.selected_id = question['id']
            self.root = '/questions/'+str(index)+'/current_judgment'
            raw = state_view(self.state, {}, {'action':'state','question_id':self.selected_id,**self.binding})
            call = raw['judgment_read_call']
            self.assertEqual(call, navigation_view(self.state, question)['details_call'])
            self.assertEqual(call['arguments']['pointer'], self.root)
            expected = current_judgment(self.state, question)
            self.assertEqual(self.materialize(call, expected), expected)
        self.assertTrue(any(c['arguments']['offset'] > 0 for c in self.calls))
        selected = current_judgment(self.state, self.q)
        self.assertEqual(len(selected['judgments']), 35)
        self.assertNotIn('F-35', [c['id'] for c in selected['judgments']])
        self.assertNotIn('F-history', [c['id'] for c in selected['judgments']])
        self.assertEqual(len(selected['remaining_gaps']), 17)
        self.assertEqual(len(selected['resolved_gaps']), 17)
        self.assertEqual(self.state, self.before)
        self.assertEqual(self.case.db.read_bytes(), self.db_before)

    def test_snapshot_and_invalid_paths_return_executable_scoped_recovery(self):
        self.selected_id = self.q['id']
        self.root = '/questions/1/current_judgment'
        cases = [dict(pointer=self.root), dict(pointer=self.root,snapshot_id='stale')]
        cases += [dict(pointer=p,snapshot_id=self.state['meta']['snapshot_id']) for p in
                  ('/claims','/questions/1/claims','/questions/1/judgments',
                   '/questions/0/current_judgment','/questions/01/current_judgment',
                   self.root+'/absent')]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(StateReadError) as error:
                state_view(self.state, {}, {'action':'state','question_id':self.selected_id,**extra})
            details = error.exception.details
            self.assertFalse(details['write_applied'])
            self.assertNotIn('judgments', details)
            recovered = self.read(details['judgment_read_call'])
            self.assertEqual(recovered['pointer'], self.root)
        # The global claims collection still exists only without a selected question.
        with self.assertRaises(StateReadError):
            state_view(self.state, {}, {'action':'state','question_id':self.selected_id,
                'pointer':'/claims','snapshot_id':self.state['meta']['snapshot_id']})
        global_claims = state_view(self.state, {}, {'action':'state','pointer':'/claims',
            'snapshot_id':self.state['meta']['snapshot_id']})
        self.assertEqual(global_claims['total'], len(self.state['claims']))
        self.assertEqual(self.state, self.before)


if __name__ == '__main__':
    unittest.main()
