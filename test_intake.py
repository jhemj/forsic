import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from forsic_plugin.intake import Intake, GOAL, OPTIONS, WELCOME


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.evidence = self.base / 'evidence'
        self.evidence.mkdir()
        (self.evidence / 'one.log').write_text('start\nexit=1\n')
        (self.evidence / 'unrelated.txt').write_text('unrelated\n')
        self.intake = Intake(self.base / 'state', synthetic_roots=[self.evidence])

    def tearDown(self):
        self.temp.cleanup()

    def select(self, session='one', path=None):
        path = path or self.evidence
        self.intake.before(session, str(path))
        return self.intake.inspect(session, {'path': str(path)})

    def test_first_turn_and_light_loading(self):
        self.assertIn(WELCOME, self.intake.before('one', '안녕'))
        with self.assertRaises(ValueError):
            self.intake.inspect('one', {'path': str(self.evidence)})
        with patch('forsic_plugin.evidence.subprocess.run') as runner:
            result = self.select()
        runner.assert_not_called()
        self.assertEqual(result['integrity'], 'not_checked')
        self.assertFalse(result['full_content_read'])
        self.assertEqual(result['options'][0], '종합 침해 분석')
        self.assertEqual(self.intake.state('one')['stage'], 'awaiting_question')
        self.assertTrue(self.intake.case('one').config['synthetic'])

    def test_single_file_scope_and_no_accidental_sibling_reads(self):
        result = self.select(path=self.evidence/'one.log')
        case = self.intake.case('one')
        self.assertEqual(result['selected_files'], ['one.log'])
        self.assertEqual([p.name for p in case._files()], ['one.log'])
        self.assertEqual([p.name for p in case._files('one.log')], ['one.log'])
        self.assertIn('error', json.loads(case.invoke('forsic_read', {'path':'unrelated.txt'})))
        self.assertNotIn('error', json.loads(case.invoke('forsic_read', {'path':'one.log'})))

    def test_sessions_independent_resume_and_compression(self):
        self.select()
        self.assertIsNone(self.intake.case('new'))
        self.select('other', self.evidence/'one.log')
        self.assertNotEqual(self.intake.case('one').manifest, self.intake.case('other').manifest)
        reloaded = Intake(self.base/'state')
        self.assertEqual(reloaded.case('one').root, self.evidence)
        reloaded.before('compressed', 'continue', parent='one')
        self.assertEqual(reloaded.case('one').manifest, reloaded.case('compressed').manifest)

    def test_repeated_intake_is_idempotent_but_scope_change_needs_new_session(self):
        first = self.select()
        second = self.select()
        self.assertEqual(first['case_id'], second['case_id'])
        self.assertTrue(second['reused'])
        with self.assertRaises(ValueError):
            self.select(path=self.evidence/'one.log')

    def test_goal_requires_choice_then_dispatches_native_once(self):
        self.intake.before('one', '1')
        self.assertEqual(self.intake.state('one')['stage'], 'awaiting_path')
        self.select()
        self.intake.before('one', '1번으로 진행')
        self.assertEqual(self.intake.state('one')['stage'], 'goal_pending')
        inject = Mock(return_value=True)
        self.assertTrue(self.intake.after('one', inject))
        inject.assert_called_once_with('/goal '+GOAL)
        self.assertIsNone(self.intake.after('one', inject))
        self.assertEqual(self.intake.case('one').config['question'], OPTIONS[0])
        self.assertEqual(self.intake.state('one')['stage'], 'goal_queued')

    def test_no_host_is_not_execution_and_free_question_not_full_goal(self):
        self.select()
        self.intake.before('one', '종합 침해 분석')
        self.assertFalse(self.intake.after('one', Mock(return_value=False)))
        self.assertIn('지원되지', self.intake.before('one', '상태'))
        self.select('other')
        self.intake.before('other', '작업이 실패한 이유는?')
        self.assertEqual(self.intake.case('other').config['question'], '작업이 실패한 이유는?')
        self.assertIsNone(self.intake.after('other', Mock()))

    def test_native_goal_has_checkable_report_contract_no_shell_gates(self):
        from hermes_cli.goals import parse_contract
        headline, contract = parse_contract(GOAL)
        self.assertIn('종합 침해 분석', headline)
        self.assertIn('네 파일', contract.verification)
        self.assertIn('부분 보고서', contract.stop_when)
        self.assertIn('읽기 전용', contract.constraints)

    def test_ewf_metadata_only_identifies_interior_gap(self):
        for suffix in ('E01', 'E03'):
            (self.evidence/f'disk.{suffix}').write_bytes(b'EVF\x09\x0d\x0a\xff\x00'+b'fixture')
        result = self.select(path=self.evidence/'disk.E01')
        self.assertEqual(result['format'], 'EWF')
        self.assertEqual(result['missing_numbered_segments'], [2])
        self.assertEqual(result['selected_files'], ['disk.E01','disk.E03'])

    def test_missing_path_creates_no_case_and_user_text_is_not_executed(self):
        self.intake.before('one', '/nonexistent/forsic-intake-test')
        with self.assertRaises(FileNotFoundError):
            self.intake.inspect('one', {'path':'/nonexistent/forsic-intake-test'})
        self.assertIsNone(self.intake.case('one'))

    def test_parent_prefix_is_not_user_selected_scope(self):
        self.intake.before('one', str(self.evidence/'one.log'))
        with self.assertRaises(ValueError):
            self.intake.inspect('one', {'path':str(self.evidence)})

    def test_native_goal_persistence_and_duplicate_start(self):
        from hermes_cli import goals
        meta = {}
        db = Mock()
        db.get_meta.side_effect = meta.get
        db.set_meta.side_effect = lambda key, value: meta.__setitem__(key, value)
        self.select()
        self.intake.before('one', '1')
        with patch.object(goals, '_get_session_db', return_value=db):
            self.assertTrue(self.intake.activate_native_goal('one'))
            saved = goals.load_goal('one')
            self.assertEqual(saved.status, 'active')
            self.assertIn('임원용', saved.contract.verification)
            self.assertFalse(self.intake.activate_native_goal('one'))
            self.assertEqual(saved.created_at, goals.load_goal('one').created_at)

    def test_registered_hooks_and_tools_use_session_bound_case(self):
        from forsic_plugin import register
        ctx = Mock()
        settings = {'intake_root':str(self.base/'plugin'), 'synthetic_roots':[str(self.evidence)]}
        ctx.get_config.side_effect = settings.get
        register(ctx)
        hooks = {call.args[0]:call.args[1] for call in ctx.register_hook.call_args_list}
        tools = {call.kwargs['name']:call.kwargs['handler'] for call in ctx.register_tool.call_args_list}
        hooks['pre_llm_call'](session_id='s', user_message=str(self.evidence))
        result = json.loads(tools['forsic_intake']({'path':str(self.evidence)}, session_id='s'))
        self.assertIn('case_id', result)
        self.assertIn('error', json.loads(tools['forsic_read']({'path':'one.log'}, session_id='different')))
        self.assertNotIn('error', json.loads(tools['forsic_read']({'path':'one.log'}, session_id='s')))


if __name__ == '__main__':
    unittest.main()
