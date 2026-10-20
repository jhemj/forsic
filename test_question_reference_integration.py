"""Registered native callbacks and source/image reuse; synthetic, offline only."""
import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from forsic_plugin import register
from forsic_plugin.context_results import summarize
from forsic_plugin.loop_comparison import project
from forsic_plugin.report_driven import host
import test_question_bootstrap as question_fixture


class QuestionReferenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        from test_environment_support import offline_environment
        offline_environment(self)
        self.f = f = question_fixture.QuestionBootstrapTests()
        f.setUp(); self.addCleanup(f.doCleanups)
        binding = f.intake / (hashlib.sha256(f.sid.encode()).hexdigest() + '.json')
        binding.write_text(json.dumps({'manifest': str(f.manifest), 'stage': 'goal_active'}))
        ctx = Mock()
        ctx.get_config.side_effect = lambda name, default=None: str(f.intake) if name == 'intake_root' else default
        register(ctx)
        self.before = next(c.args[1] for c in ctx.register_hook.call_args_list if c.args[0] == 'pre_llm_call')
        self.handlers = {c.kwargs['name']: c.kwargs['handler'] for c in ctx.register_tool.call_args_list}

    def call(self, name, args):
        return json.loads(self.handlers[name](args, session_id=self.f.sid))

    def test_real_registered_turn_seeds_once_but_status_and_projection_do_not(self):
        f = self.f
        self.before(session_id=f.sid, user_message='진행 상황만 알려줘', platform='tui', turn_id='status')
        self.assertFalse([q for q in host.current(f.case)['questions'] if q['id'] != 'Q-intake'])
        with f.case.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='note'").fetchone()[0], 0)
        out = self.before(session_id=f.sid, user_message=f.goal.goal, platform='tui', turn_id='start')
        state = host.current(f.case)
        self.assertEqual(len(state['questions']), 1)
        q = state['questions'][0]
        self.assertEqual(q['answer'], question_fixture.bootstrap.PROVISIONAL_ANSWER)
        self.assertEqual(q['claim_refs'], [])
        self.assertIn(q['id'], out['context'])
        self.assertIn(q['id'], json.dumps(self.call('forsic_reporting', {'action': 'state'})))
        self.before(session_id=f.sid, user_message=f.goal.goal, platform='tui', turn_id='resume')
        self.assertEqual(host.current(f.case)['questions'], state['questions'])
        from forsic_plugin.investigation_context import post_context_compaction
        before = f.case.db.read_bytes()
        post_context_compaction(session_id=f.sid, intake_root=f.intake, native_db=f.native)
        self.assertEqual(f.case.db.read_bytes(), before)

    def test_registered_image_selection_then_source_handle_read_and_pruning(self):
        disk = self.f.case.root / 'sample.E01'; disk.write_bytes(b'synthetic image')
        listing = self.call('forsic_list', {'path': '.'})
        selected = next(row['image_ref'] for row in listing['files'] if row['path'] == disk.name)
        crumb = json.loads(summarize('forsic_list', {'path': '.'}, json.dumps(listing)))
        self.assertEqual(crumb['image_selections'][0]['image_ref'], selected)
        self.assertIn('source_handle', crumb)
        args = {'image_ref': selected, 'path': '\n', 'action': 'read', 'file_path': '/records/log'}
        with patch('forsic_plugin.image_process.run_parser', return_value={
                'lines': [{'line': 1, 'text': 'retained synthetic observation'}]}) as parser:
            read = self.call('forsic_image_read', args)
        self.assertNotIn('error', read, read); parser.assert_called_once()
        crumb = json.loads(summarize('forsic_image_read', args, json.dumps(read)))
        self.assertEqual(crumb['image_ref'], read['image_ref'])
        page = self.call('forsic_reporting', {'action': 'source', 'source_handle': read['source_handle'],
            'pointer': '/lines/0/text'})
        self.assertNotIn('error', page, page)
        self.assertIn('retained synthetic observation', json.dumps(page))
        self.assertEqual(page['source_ref'], read['source_ref'])
        self.assertIn('citation_ref', json.dumps(page))
        bad = self.call('forsic_reporting', {'action': 'source', 'source_handle': read['source_handle'],
            'source_version': read['evidence_id'], 'pointer': '/lines/0/text'})
        self.assertEqual(bad['error_code'], 'source_handle_rejected')

    def test_repeated_discovery_refs_are_not_new_progress_but_changed_files_are(self):
        (self.f.case.root / 'sample.E01').write_bytes(b'synthetic image')
        args = {'path': '.'}
        one = self.call('forsic_list', args); two = self.call('forsic_list', args)
        self.assertNotEqual(one['source_handle'], two['source_handle'])
        self.assertEqual(project('forsic_list', args, json.dumps(one))['result'],
                         project('forsic_list', args, json.dumps(two))['result'])
        (self.f.case.root / 'new.log').write_text('new record')
        changed = self.call('forsic_list', args)
        self.assertNotEqual(project('forsic_list', args, json.dumps(one))['result'],
                            project('forsic_list', args, json.dumps(changed))['result'])


if __name__ == '__main__':
    unittest.main()
