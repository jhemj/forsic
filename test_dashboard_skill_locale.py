"""Offline UI locale contracts; canonical English metadata remains model-facing."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import verify_dashboard_skills as verifier


class SkillLocaleTests(unittest.TestCase):
    def setUp(self):
        self.expected = verifier.registered_metadata()
        self.path = verifier.ROOT / 'forsic_plugin/ui/skills.ko.json'
        self.locale = json.loads(self.path.read_text())

    def validate(self, locale):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'locale.json'
            path.write_text(json.dumps(locale, ensure_ascii=False))
            return verifier.validate_locale(self.expected, path)

    def test_exact_canonical_ids_and_current_source_hashes(self):
        self.assertTrue(self.expected)
        self.assertEqual(set(self.locale['skills']), {item['name'] for item in self.expected})
        self.validate(self.locale)

    def test_static_canonical_tuple_resolution_never_executes_plugin_or_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'__init__.py').write_text("raise RuntimeError('must not import')\ndef register(ctx):\n    from .capability_policy import SKILLS\n    skills = SKILLS\n")
            (root/'capability_policy.py').write_text("raise RuntimeError('must not import')\nSKILLS = ('first-skill', 'second-skill')\n")
            self.assertEqual(verifier.registered_skill_names(root),('first-skill','second-skill'))
            (root/'capability_policy.py').write_text("SKILLS = make_skills()\n")
            with self.assertRaisesRegex(ValueError,'literal tuple'):
                verifier.registered_skill_names(root)
            (root/'__init__.py').write_text("def register(ctx):\n    from .unknown import SKILLS\n    skills = SKILLS\n")
            with self.assertRaisesRegex(ValueError,'canonical'):
                verifier.registered_skill_names(root)

    def test_missing_extra_stale_and_invalid_translation_fail(self):
        for change in ('missing', 'extra', 'stale', 'english', 'terms'):
            with self.subTest(change=change):
                locale = copy.deepcopy(self.locale)
                first = next(iter(locale['skills']))
                if change == 'missing': del locale['skills'][first]
                if change == 'extra': locale['skills']['new-skill'] = locale['skills'][first]
                if change == 'stale': locale['skills'][first]['source_description_sha256'] = '0' * 64
                if change == 'english': locale['skills'][first]['description'] = 'English only'
                if change == 'terms': locale['skills'][first]['search_terms'] = ['x', 'x']
                with self.assertRaises(ValueError): self.validate(locale)

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            json.loads('{"skills": {}, "skills": {}}', object_pairs_hook=verifier.unique_object)

    def test_write_leaves_english_catalogue_locale_and_model_skills_byte_identical(self):
        paths = [self.path, verifier.ROOT / 'forsic_plugin/dashboard/dist/skills.json']
        paths += list((verifier.ROOT / 'forsic_plugin/skills').rglob('SKILL.md'))
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        subprocess.run([sys.executable, str(verifier.ROOT / 'verify_dashboard_skills.py'), '--write'], check=True, capture_output=True)
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})


if __name__ == '__main__': unittest.main()
