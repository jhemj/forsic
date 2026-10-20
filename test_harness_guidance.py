"""Procedure/tool discovery contracts; synthetic fixtures, no live calls."""
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from forsic_plugin import register
from forsic_plugin.intake import GOAL
import test_workflow


class HarnessGuidanceTests(unittest.TestCase):
    def skill_fixture(self, directory):
        ctx = Mock()
        ctx.get_config.side_effect = {'intake_root': str(Path(directory)/'intake')}.get
        register(ctx)
        registration = next(c.kwargs for c in ctx.register_tool.call_args_list if c.kwargs['name']=='forsic_skill')
        paths = {'forsic:'+c.kwargs['name']: c.kwargs['path'] for c in ctx.register_skill.call_args_list}
        manager = SimpleNamespace(find_plugin_skill=paths.get,
            is_plugin_skill_available=lambda _: True,
            list_plugin_skills=lambda _: [n.split(':',1)[1] for n in paths])
        return registration, paths, manager

    def test_skill_catalog_is_metadata_only_and_does_not_start_intake(self):
        with tempfile.TemporaryDirectory() as folder:
            registered, paths, _ = self.skill_fixture(folder)
            with patch('tools.skills_tool.skill_view', side_effect=AssertionError('catalog must not load bodies')):
                result = json.loads(registered['handler']({}))
            self.assertEqual({'forsic:'+s['name'] for s in result['skills']}, set(paths))
            self.assertTrue(all(set(s)=={'name','description'} and s['description'] for s in result['skills']))
            self.assertFalse((Path(folder)/'intake').exists())

    def test_selected_skill_and_reference_use_native_reader_without_preprocessing(self):
        with tempfile.TemporaryDirectory() as folder:
            registered, paths, manager = self.skill_fixture(folder)
            name = 'forsic-report-driven'
            with patch('hermes_cli.plugins.discover_plugins'), \
                 patch('hermes_cli.plugins.get_plugin_manager', return_value=manager), \
                 patch('hermes_cli.plugins._get_disabled_plugins', return_value=[]), \
                 patch('tools.skills_tool._is_skill_disabled', return_value=False), \
                 patch('tools.skills_tool_plugin._preprocess_skill', side_effect=AssertionError('read only')):
                body = json.loads(registered['handler']({'name': name}, task_id='synthetic-skill'))
                self.assertTrue(body['success'], body)
                self.assertIn(paths['forsic:'+name].read_text(), body['content'])
                refs = body['linked_files']['references']
                self.assertIn('references/report_contract.md', refs)
                for ref in refs:
                    result = json.loads(registered['handler']({'name':name, 'file_path':ref}))
                    self.assertTrue(result['success'], result)
                    self.assertEqual(result['file'], ref)
                    self.assertEqual(result['content'], (paths['forsic:'+name].parent/ref).read_text())
                    self.assertNotIn(result['content'], body['content'])

    def test_skill_reference_selection_cannot_read_arbitrary_paths(self):
        from jsonschema import Draft202012Validator
        from tools.schema_sanitizer import sanitize_tool_schemas
        with tempfile.TemporaryDirectory() as folder:
            registered, _, _ = self.skill_fixture(folder)
            schema = sanitize_tool_schemas([{'type':'function','function':registered['schema']}])[0]['function']['parameters']
            validator = Draft202012Validator(schema)
            for args in ({}, {'name':'source-triage'}, {'name':'forsic-report-driven','file_path':'references/report_contract.md'}):
                self.assertFalse(list(validator.iter_errors(args)))
            self.assertTrue(list(validator.iter_errors({'name':'unregistered'})))
            self.assertTrue(list(validator.iter_errors({'name':'source-triage','file_path':42})))
            invalid = [{'name':'forsic-report-driven','file_path':p} for p in
                ('../config/AGENTS.md','/etc/passwd','references/../SKILL.md','scripts/run.sh','references/section_catalog.yaml','')]
            invalid += [{'file_path':'references/report_contract.md'},
                        {'name':'source-triage','file_path':'references/report_contract.md'}]
            with patch('tools.skills_tool.skill_view', side_effect=AssertionError('must reject before native lookup')):
                for args in invalid:
                    with self.subTest(args=args):
                        self.assertIn('error', json.loads(registered['handler'](args)))

    def test_native_reference_reader_rejects_changed_symlink_target(self):
        with tempfile.TemporaryDirectory() as folder:
            registered, paths, manager = self.skill_fixture(folder)
            root = Path(folder)/'skill'; (root/'references').mkdir(parents=True)
            name = 'forsic-report-driven'; original = paths['forsic:'+name]
            (root/'SKILL.md').write_text(original.read_text())
            outside = Path(folder)/'private.txt'; outside.write_text('UNRELATED_PRIVATE_CONTENT')
            (root/'references/report_contract.md').symlink_to(outside)
            paths['forsic:'+name] = root/'SKILL.md'
            with patch('hermes_cli.plugins.discover_plugins'), \
                 patch('hermes_cli.plugins.get_plugin_manager', return_value=manager), \
                 patch('hermes_cli.plugins._get_disabled_plugins', return_value=[]), \
                 patch('tools.skills_tool._is_skill_disabled', return_value=False):
                result = json.loads(registered['handler']({'name':name,'file_path':'references/report_contract.md'}))
            self.assertFalse(result['success'])
            self.assertNotIn(outside.read_text(), json.dumps(result))

    def test_goal_and_tools_lead_to_one_current_report_path(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = Mock()
            ctx.get_config.side_effect = {'intake_root': directory}.get
            register(ctx)
        schemas = {c.kwargs['name']: c.kwargs['schema'] for c in ctx.register_tool.call_args_list}
        self.assertIn('forsic_reporting(render)', GOAL)
        self.assertIn('직접 읽은 근거', GOAL)
        self.assertNotIn('forsic_report', schemas)
        reporting = schemas['forsic_reporting']['parameters']['properties']
        self.assertIn('review', reporting['action']['enum'])
        self.assertIn('review_id', reporting)
        self.assertIn('question_ids', reporting)
        self.assertEqual(schemas['forsic_note']['parameters']['properties']['action']['enum'],['list','get'])
        self.assertIn('forsic_reporting(state)', schemas['forsic_read']['description'])

    def test_report_review_patience_uses_existing_native_setting(self):
        from ruamel.yaml import YAML
        config_path = Path(__file__).parent/'config/config.yaml'
        if not config_path.exists():
            config_path = config_path.with_name('config.example.yaml')
        config = YAML(typ='safe').load(config_path.read_text())
        # Operator-approved slower models may need a longer wait.
        self.assertGreaterEqual(config['auxiliary']['report_review']['timeout'], 900)
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
