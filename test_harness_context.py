"""Hermes project-context precedence; offline, no model or investigation calls."""
from pathlib import Path
import tempfile
import unittest

from agent.prompt_builder import build_context_files_prompt


class HarnessContextTests(unittest.TestCase):
    def test_developer_charter_is_not_in_investigator_context(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as folder:
            cwd = Path(folder)
            (cwd / 'AGENTS.md').write_text((root / 'AGENTS.md').read_text() + '\nDEVELOPER_ONLY_SENTINEL\n')
            (cwd / '.hermes.md').write_text((root / '.hermes.md').read_text() + '\nINVESTIGATOR_SENTINEL\n')
            context = build_context_files_prompt(cwd=str(cwd), skip_soul=True)
            self.assertIn('INVESTIGATOR_SENTINEL', context)
            self.assertNotIn('DEVELOPER_ONLY_SENTINEL', context)
            self.assertNotIn('# Forsic 하네스 개발 원칙', context)

    def test_missing_runtime_override_would_expose_development_context(self):
        with tempfile.TemporaryDirectory() as folder:
            cwd = Path(folder)
            (cwd / 'AGENTS.md').write_text('DEVELOPER_ONLY_SENTINEL\n')
            self.assertIn('DEVELOPER_ONLY_SENTINEL', build_context_files_prompt(cwd=str(cwd), skip_soul=True))


if __name__ == '__main__':
    unittest.main()
