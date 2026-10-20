"""Offline loading/routing contracts; these checks do not evaluate model reasoning."""
from contextlib import ExitStack
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import test_harness_guidance as guidance


class ForensicSkillLoadingTests(unittest.TestCase):
    def fixture(self, folder, stack):
        registered, paths, manager = guidance.HarnessGuidanceTests().skill_fixture(folder)
        for target, options in (
            ('hermes_cli.plugins.discover_plugins', {}),
            ('hermes_cli.plugins.get_plugin_manager', {'return_value': manager}),
            ('hermes_cli.plugins._get_disabled_plugins', {'return_value': []}),
            ('tools.skills_tool._is_skill_disabled', {'return_value': False}),
            ('tools.skills_tool_plugin._preprocess_skill', {'side_effect': AssertionError('read only')}),
        ):
            stack.enter_context(patch(target, **options))
        return registered, paths

    def test_every_registered_procedure_and_advertised_reference_is_readable(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            registered, paths = self.fixture(folder, stack)
            disk = {p.parent.name for p in (Path(__file__).parent/'forsic_plugin/skills').glob('*/SKILL.md')}
            self.assertEqual(disk, {key.split(':', 1)[1] for key in paths})
            for key, skill_path in paths.items():
                name = key.split(':', 1)[1]
                with self.subTest(name=name):
                    body = json.loads(registered['handler']({'name': name}))
                    self.assertTrue(body['success'], body)
                    self.assertIn(skill_path.read_text(), body['content'])
                    refs = body['linked_files']['references']
                    actual_refs = {p.relative_to(skill_path.parent).as_posix()
                                   for p in (skill_path.parent/'references').glob('*.md')}
                    self.assertEqual(set(refs), actual_refs)
                    for reference in refs:
                        result = json.loads(registered['handler']({'name': name, 'file_path': reference}))
                        self.assertTrue(result['success'], result)
                        self.assertEqual(result['content'], (skill_path.parent/reference).read_text())
                        self.assertNotIn('linked_files', result)
            self.assertFalse((Path(folder)/'intake').exists())

    def test_explicit_cross_skill_routes_resolve_through_the_actual_reader(self):
        route = re.compile(r'forsic_skill\(name="([a-z0-9-]+)"(?:, file_path="([^"]+)")?\)')
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            registered, paths = self.fixture(folder, stack)
            seen = set()
            for skill_path in paths.values():
                resources = [skill_path, *(skill_path.parent/'references').glob('*.md')]
                for resource in resources:
                    for name, reference in route.findall(resource.read_text()):
                        args = {'name': name}
                        if reference:
                            args['file_path'] = reference
                        with self.subTest(resource=str(resource), args=args):
                            result = json.loads(registered['handler'](args))
                            self.assertTrue(result['success'], result)
                            seen.add((name, reference))
            self.assertTrue({('inspect-documents', 'references/formats.md'),
                             ('executive-followup', 'references/reader-questions.md'),
                             ('source-triage', 'references/archives-and-recovery.md')} <= seen)
            self.assertFalse((Path(folder)/'intake').exists())

    def test_new_procedures_cannot_load_other_skills_files_or_executable_resources(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            registered, _ = self.fixture(folder, stack)
            with patch('tools.skills_tool.skill_view', side_effect=AssertionError('reject before read')):
                for name in ('inspect-documents', 'inspect-scripts'):
                    for reference in ('../source-triage/SKILL.md', '/etc/passwd',
                                      'references/archives-and-recovery.md', 'scripts/inspect.py', ''):
                        result = json.loads(registered['handler']({'name': name, 'file_path': reference}))
                        self.assertIn('error', result, result)
            self.assertFalse((Path(folder)/'intake').exists())


if __name__ == '__main__':
    unittest.main()
