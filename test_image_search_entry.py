"""Explicit image search inputs share the existing reader, cursor and ledger."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
import test_search_selection as fixtures
from test_search_resume import Node
from forsic_plugin.image_reader import search_node
from forsic_plugin.report_driven.host import current, find
from forsic_plugin.loop_comparison import project
from forsic_plugin.context_results import summarize


class ImageSearchEntryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SearchSelectionTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups); self.x = self.fixture.x
        (self.x.case.root / 'disk.E01').write_bytes(b'synthetic placeholder')
        self.node = Node('/logs/a', b'hit one\nhit two\nhit three\n')
        self.args = dict(path='disk.E01', file_path='/logs/a', volume_offset=4096, text='hit', max_matches=1)

    def reader(self, args):
        self.assertEqual(args['action'], 'search')
        return dict(path=args['path'], file_path=args['file_path'], volume_offset=args['volume_offset'],
                    **search_node(self.node, args, {'image': 'synthetic'}))

    def call(self, args): return self.x.call('forsic_image_search', args)

    def assert_submission(self, result, args):
        start = self.x.case.event(result['started_id'])['data']
        self.assertEqual(start['tool'], 'forsic_image_files')
        self.assertEqual(start['submitted_tool'], 'forsic_image_search')
        self.assertEqual(start['submitted_arguments'], args)

    def test_pin_requires_internal_path_query_exposes_image_selection_and_excludes_other_action_fields(self):
        from tools.schema_sanitizer import sanitize_tool_schemas
        from jsonschema import Draft202012Validator
        self.assertIn('forsic_image_search', self.x.schemas)
        wire = sanitize_tool_schemas([{'type': 'function', 'function': self.x.schemas['forsic_image_search']}])[0]['function']['parameters']
        self.assertEqual(set(wire['required']), {'file_path'})
        self.assertIn('omit text', wire['properties']['text']['description'])
        direct = self.x.schemas['forsic_image_search']['parameters']
        initial_missing = {k:v for k,v in self.args.items() if k != 'text'}
        self.assertTrue(list(Draft202012Validator(direct).iter_errors(initial_missing)))
        selected = {**initial_missing, 'cursor_ref':{'id':'synthetic-page','version':'synthetic-version'}}
        self.assertFalse(list(Draft202012Validator(direct).iter_errors(selected)))
        self.assertFalse(list(Draft202012Validator(wire).iter_errors(selected)))
        self.assertFalse(list(Draft202012Validator(wire).iter_errors({**selected, 'text':''})))
        self.assertEqual(wire['properties']['image_ref']['required'], ['id','version','index'])
        self.assertNotIn('action', wire['properties'])
        self.assertNotIn('search', self.x.schemas['forsic_image_files']['parameters']['properties']['action']['enum'])
        self.assertFalse(list(Draft202012Validator(wire).iter_errors(self.args)))
        for change in ({'file_path': ''}, {'max_matches': 100}, {'action': 'list'}):
            self.assertTrue(list(Draft202012Validator(wire).iter_errors({**self.args, **change})))

    def test_bad_inputs_reject_without_backend_or_inferred_target_then_repair(self):
        for change in ({'path': ''}, {'file_path': ''}, {'max_matches': 100}, {'action': 'search'}):
            with patch.object(self.x.case, 'image_files') as backend:
                submitted = {**self.args, **change}
                result = self.call(submitted)
                self.assertTrue(result['execution_rejected'], result); self.assertFalse(result['backend_started'])
                self.assert_submission(result, submitted)
                backend.assert_not_called()
        for field in ('path', 'file_path', 'text'):
            with patch.object(self.x.case, 'image_files') as backend:
                submitted = {k:v for k,v in self.args.items() if k != field}
                result = self.call(submitted)
                self.assertIn(field, result['missing_fields']); backend.assert_not_called()
                self.assert_submission(result, submitted)
        with patch.object(self.x.case, 'image_files', side_effect=self.reader) as backend:
            result = self.call(self.args)
            self.assertNotIn('error', result, result)
            backend.assert_called_once_with({**self.args, 'action': 'search'})
        self.assertEqual(current(self.x.case)['missions'], [])

    def test_exact_continuation_canonical_arguments_and_context_source_survive(self):
        original = deepcopy(self.args)
        with patch.object(self.x.case, 'image_files', side_effect=self.reader) as backend:
            first = self.call(self.args); self.assertNotIn('error', first, first)
            self.assert_submission(first, original)
            self.assertEqual(self.x.case.event(first['started_id'])['data']['arguments'],
                             {**original, 'action': 'search'})
            retained = deepcopy(self.x.case.event(first['evidence_id']))
            call = first['next_call']
            self.assertEqual(call['tool'], 'forsic_image_search'); self.assertNotIn('action', call['arguments'])
            second = self.x.call(call['tool'], call['arguments']); self.assertNotIn('error', second, second)
            self.assertEqual([first['matches'][0]['line'], second['matches'][0]['line']], [1, 2])
            self.assertEqual(backend.call_args.args[0]['cursor'], retained['data']['next_cursor'])
        self.assertEqual(self.args, original)
        self.assert_submission(second, call['arguments'])
        self.assertEqual(self.x.case.event(first['evidence_id']), retained)
        self.assertTrue(second['presented_spans']); self.assertFalse(second['coverage_complete'])
        summary = summarize('forsic_image_search', call['arguments'], json.dumps(second))
        self.assertIn(second['evidence_id'], json.dumps(summary))
        comparison = project('forsic_image_search', self.args, json.dumps(first))
        self.assertEqual(comparison['failure_group'], 'forsic_image_files')

    def test_selected_page_restores_only_omitted_query_with_exact_literal_scope_and_provenance(self):
        literal = '  literal.[x]  '
        self.node = Node('/logs/a', (literal + 'first\n' + literal + 'second\n' + literal + 'third\n').encode())
        self.args.update(text=literal, recursive=True)
        with patch.object(self.x.case, 'image_files', side_effect=self.reader) as backend:
            first = self.call(self.args)
            first_event = deepcopy(self.x.case.event(first['evidence_id']))
            for missing in (True, False):
                with self.subTest(missing=missing):
                    submitted = deepcopy(first['next_call']['arguments'])
                    if missing:
                        submitted.pop('text')
                    else:
                        submitted['text'] = ''
                    before = deepcopy(submitted)
                    result = self.call(submitted)
                    self.assertNotIn('error', result, result)
                    self.assertEqual(result['matches'][0]['line'], 2)
                    self.assertEqual(submitted, before)
                    self.assert_submission(result, before)
                    start = self.x.case.event(result['started_id'])['data']
                    self.assertEqual(start['arguments']['text'], literal)
                    self.assertTrue(start['arguments']['recursive'])
                    self.assertEqual(start['arguments']['cursor'], first_event['data']['next_cursor'])
                    self.assertEqual(start['argument_resolution']['restored_fields'], ['text'])
                    self.assertEqual(start['argument_resolution']['source_ref'], first['source_ref'])
                    self.assertEqual(backend.call_args.args[0]['text'], literal)
                    self.assertEqual(result['next_call']['arguments']['text'], literal)
                    crumb = json.loads(summarize('forsic_image_search', before, json.dumps(result)))
                    self.assertEqual(crumb['next_call'], result['next_call'])
            self.assertEqual(self.x.case.event(first['evidence_id']), first_event)
            last_args = deepcopy(result['next_call']['arguments']); last_args.pop('text')
            last = self.call(last_args)
            self.assertEqual(last['matches'][0]['line'], 3)
            self.assertFalse(last['has_next_cursor'])
            self.assertIsNone(last['next_call'])

    def test_query_restoration_never_repairs_conflicts_stale_foreign_or_changed_scope(self):
        with patch.object(self.x.case, 'image_files', side_effect=self.reader):
            first = self.call(self.args)
        original = first['next_call']['arguments']
        bad = [dict(text='different'), dict(text=' '), dict(text=None), dict(text=5),
               dict(text='', recursive=True), dict(text='', file_path='/'),
               dict(text='', volume_offset=8192), dict(text='', path='other.E01'),
               dict(text='', cursor_ref={**original['cursor_ref'], 'version':'stale'}),
               dict(text='', cursor_ref={'id':'foreign-case-source', 'version':'foreign-version'})]
        with patch.object(self.x.case, 'image_files') as backend:
            for change in bad:
                with self.subTest(change=change):
                    result = self.call({**original, **change})
                    self.assertTrue(result['execution_rejected'], result)
                    self.assertFalse(result['backend_started'])
            self.fixture.context['goal_status'] = 'paused'
            result = self.call({**original, 'text':''})
            self.assertTrue(result['execution_rejected'])
            backend.assert_not_called()

    def test_unbound_empty_query_has_structured_recovery_without_a_backend_or_guessed_query(self):
        for tool in ('forsic_image_search', 'forsic_search'):
            for missing in (True, False):
                with self.subTest(tool=tool, missing=missing), \
                        patch.object(self.x.case, 'image_files') as image_backend, \
                        patch.object(self.x.case, 'search') as file_backend:
                    args = deepcopy(self.args) if tool == 'forsic_image_search' else {'path':'many.log','text':''}
                    if missing:
                        args.pop('text', None)
                    else:
                        args['text'] = ''
                    result = self.x.call(tool, args)
                    self.assertTrue(result['execution_rejected'], result)
                    self.assertFalse(result['backend_started'])
                    self.assertEqual(result['query_recovery']['field'], 'text')
                    self.assertFalse(result['query_recovery']['automatic_retry'])
                    self.assertNotIn('next_call', result)
                    image_backend.assert_not_called(); file_backend.assert_not_called()

    def test_changed_cursor_target_or_paused_goal_never_launches(self):
        with patch.object(self.x.case, 'image_files', side_effect=self.reader): first = self.call(self.args)
        call = first['next_call']
        with patch.object(self.x.case, 'image_files') as backend:
            for change in ({'file_path': '/'}, {'volume_offset': 8192}, {'text': 'different'}):
                result = self.call({**call['arguments'], **change})
                self.assertTrue(result['execution_rejected']); self.assertFalse(result['backend_started'])
            self.fixture.context['goal_status'] = 'paused'
            self.assertTrue(self.call(call['arguments'])['execution_rejected'])
            backend.assert_not_called()

    def test_optional_plan_uses_same_canonical_reader_and_exact_mission_once(self):
        payload = {**self.x.plan(), 'tool_name': 'forsic_image_search', 'tool_arguments': self.args}
        bad = self.x.call('forsic_mission', {**payload, 'tool_arguments': {k:v for k,v in self.args.items() if k != 'file_path'}})
        self.assertIn('error', bad); self.assertEqual(current(self.x.case)['missions'], [])
        result = self.x.call('forsic_mission', payload); self.assertNotIn('error', result, result)
        mission = find(current(self.x.case), 'missions', result['mission']['id'])
        self.assertEqual(mission['proposed_tool_name'], 'forsic_image_files')
        self.assertEqual(mission['tool_arguments'], {**self.args, 'action': 'search'})
        call = result['next_call']; self.assertEqual(call['tool'], 'forsic_image_search')
        with patch.object(self.x.case, 'image_files', side_effect=self.reader) as backend:
            out = self.x.call(call['tool'], call['arguments']); self.assertNotIn('error', out, out)
            self.assertEqual(out['mission_context']['mission_id'], mission['id'])
            self.assertTrue(self.x.call(call['tool'], call['arguments'])['execution_rejected'])
            backend.assert_called_once()


if __name__ == '__main__': unittest.main()
