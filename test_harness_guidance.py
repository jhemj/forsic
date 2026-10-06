"""Procedure/tool discovery contracts; synthetic fixtures, no live calls."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from forsic_plugin import register
from forsic_plugin.intake import GOAL
import test_workflow


class HarnessGuidanceTests(unittest.TestCase):
    def test_goal_and_tools_lead_to_one_current_report_path(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = Mock()
            ctx.get_config.side_effect = {'intake_root': directory}.get
            register(ctx)
        schemas = {c.kwargs['name']: c.kwargs['schema'] for c in ctx.register_tool.call_args_list}
        self.assertIn('forsic_reporting(render)', GOAL)
        self.assertIn('실제 결과 평가', GOAL)
        self.assertIn('Legacy report compatibility only', schemas['forsic_report']['description'])
        reporting = schemas['forsic_reporting']['parameters']['properties']
        self.assertIn('review', reporting['action']['enum'])
        self.assertIn('review_id', reporting)
        self.assertIn('question_ids', reporting)
        revision = schemas['forsic_note']['parameters']['properties']['revision']
        self.assertIn('CURRENT stored revision', revision['description'])
        self.assertIn('send 2 to save revision 3', revision['description'])
        self.assertIn('forsic_reporting(state)', schemas['forsic_read']['description'])

    def test_report_review_patience_uses_existing_native_setting(self):
        from ruamel.yaml import YAML
        config_path = Path(__file__).parent/'config/config.yaml'
        if not config_path.exists():
            config_path = config_path.with_name('config.example.yaml')
        config = YAML(typ='safe').load(config_path.read_text())
        self.assertEqual(config['auxiliary']['report_review']['timeout'], 900)
        self.assertEqual(config['model']['provider'], 'custom')
        self.assertFalse(config['fallback_model'])

    def test_output_read_error_guides_without_broadening_evidence_scope(self):
        fixture = test_workflow.WorkflowTests('test_exact_bytes_and_continuation')
        fixture.setUp()
        try:
            result = fixture.call('read', path=str(fixture.case.output/'report.html'))
            self.assertIn('outside the evidence root', result['error'])
            self.assertIn('forsic_reporting(action=state)', result['error'])
            self.assertNotIn('outcome', result)
            outside = fixture.call('read', path=str(fixture.base/'unrelated.txt'))
            self.assertEqual(outside['error'], 'Path is outside the selected evidence root')
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
