"""Offline registered discovery and legacy reader routing; no investigation calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_harness_guidance as guidance


class SkillConsolidationTests(unittest.TestCase):
    def fixture(self, folder):
        return guidance.HarnessGuidanceTests().skill_fixture(folder)

    def test_catalog_has_canonical_procedures_and_no_alias_registrations(self):
        with tempfile.TemporaryDirectory() as folder:
            registered,paths,_=self.fixture(folder)
            with patch('tools.skills_tool.skill_view',side_effect=AssertionError('metadata only')):
                result=json.loads(registered['handler']({}))
            names={item['name'] for item in result['skills']}
            self.assertEqual(len(names),24)
            self.assertIn('scan-sensitive-data',names)
            self.assertTrue({'inspect-artifacts','investigation-recipes','query-structured-logs',
                             'scan-windows-events','scan-file-rules','import-timeline','consult-previous-cases'} <= names)
            self.assertFalse({'report-executive','report-analyst','recover-content'} & names)
            self.assertTrue({'inspect-documents','inspect-scripts','source-triage'} <= names)
            self.assertEqual(set(paths),{'forsic:'+name for name in names})
            enum=registered['schema']['parameters']['properties']['name']['enum']
            self.assertEqual(set(enum),names)
            self.assertFalse((Path(folder)/'intake').exists())

    def test_legacy_calls_read_the_same_central_reference_without_preprocessing(self):
        with tempfile.TemporaryDirectory() as folder:
            registered,paths,manager=self.fixture(folder)
            reference='references/audiences.md'
            expected=(paths['forsic:forsic-report-driven'].parent/reference).read_text()
            with patch('hermes_cli.plugins.discover_plugins'), \
                 patch('hermes_cli.plugins.get_plugin_manager',return_value=manager), \
                 patch('hermes_cli.plugins._get_disabled_plugins',return_value=[]), \
                 patch('tools.skills_tool._is_skill_disabled',return_value=False), \
                 patch('tools.skills_tool_plugin._preprocess_skill',side_effect=AssertionError('read only')):
                central=json.loads(registered['handler']({'name':'forsic-report-driven','file_path':reference}))
                self.assertTrue(central['success'],central)
                self.assertEqual(central['content'],expected)
                for name in ('report-executive','report-analyst'):
                    legacy=json.loads(registered['handler']({'name':name},task_id='synthetic-old-call'))
                    self.assertTrue(legacy['success'],legacy)
                    self.assertEqual(legacy['file'],reference)
                    self.assertEqual(legacy['content'],central['content'])
                    self.assertNotIn('linked_files',legacy)
            self.assertFalse((Path(folder)/'intake').exists())

    def test_retired_recovery_name_reads_the_actual_archive_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            registered,paths,manager=self.fixture(folder)
            reference='references/archives-and-recovery.md'
            expected=(paths['forsic:source-triage'].parent/reference).read_text()
            with patch('hermes_cli.plugins.discover_plugins'), \
                 patch('hermes_cli.plugins.get_plugin_manager',return_value=manager), \
                 patch('hermes_cli.plugins._get_disabled_plugins',return_value=[]), \
                 patch('tools.skills_tool._is_skill_disabled',return_value=False), \
                 patch('tools.skills_tool_plugin._preprocess_skill',side_effect=AssertionError('read only')):
                canonical=json.loads(registered['handler']({'name':'source-triage','file_path':reference}))
                legacy=json.loads(registered['handler']({'name':'recover-content'},task_id='synthetic-recovery'))
            self.assertTrue(legacy['success'],legacy)
            self.assertEqual(legacy['file'],reference)
            self.assertEqual(legacy['content'],expected)
            self.assertEqual(legacy['content'],canonical['content'])
            self.assertNotIn('linked_files',legacy)
            self.assertFalse((Path(folder)/'intake').exists())

    def test_legacy_names_do_not_allow_arbitrary_reference_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            registered,_,_=self.fixture(folder)
            with patch('tools.skills_tool.skill_view',side_effect=AssertionError('invalid selection must not load')):
                for name in ('report-executive','report-analyst','recover-content'):
                    for path in ('/etc/passwd','../SKILL.md','references/report_contract.md',''):
                        result=json.loads(registered['handler']({'name':name,'file_path':path}))
                        self.assertIn('error',result,result)
            self.assertFalse((Path(folder)/'intake').exists())


if __name__=='__main__':unittest.main()
