"""Verify static dashboard skill metadata without loading the plugin registry.

Run `python3 verify_dashboard_skills.py --write` after changing registered skills
or their frontmatter, then run without arguments to check the shipped catalogue.
"""
from __future__ import annotations

import argparse
import ast
import json
import hashlib
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'upstream' / 'hermes'))
from agent.skill_utils import parse_frontmatter


def registered_skill_names(plugin_root=None):
    """Resolve only the canonical literal declaration; never import plugin code."""
    plugin_root = plugin_root or ROOT / 'forsic_plugin'
    tree = ast.parse((plugin_root / '__init__.py').read_text(encoding='utf-8'))
    register = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'register')
    declarations = [node for node in register.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'skills' for target in node.targets)]
    if len(declarations) != 1:
        raise ValueError('Expected one registered skills declaration')
    value = declarations[0].value
    if isinstance(value, ast.Name):
        imports = [alias for node in register.body
                   if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == 'capability_policy'
                   for alias in node.names if alias.name == 'SKILLS' and (alias.asname or alias.name) == value.id]
        rebound = any(isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == value.id
                      for statement in register.body for node in ast.walk(statement))
        if len(imports) != 1 or rebound:
            raise ValueError('Registered skills must reference the canonical capability-policy tuple')
        policy = ast.parse((plugin_root / 'capability_policy.py').read_text(encoding='utf-8'))
        constants = [node.value for node in policy.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == 'SKILLS' for target in node.targets)]
        writes = [node for statement in policy.body for node in ast.walk(statement)
                  if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == 'SKILLS']
        if len(constants) != 1 or len(writes) != 1:
            raise ValueError('Expected one canonical SKILLS literal tuple')
        value = constants[0]
    try:
        names = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise ValueError('Registered skill names must be a unique literal tuple') from exc
    if (not isinstance(names, tuple) or any(not isinstance(name, str) or Path(name).name != name for name in names)
            or len(set(names)) != len(names)):
        raise ValueError('Registered skill names must be a unique literal tuple of valid names')
    return names


def registered_metadata():
    names = registered_skill_names()
    result = []
    for name in names:
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError('Invalid registered skill name')
        source = ROOT / 'forsic_plugin' / 'skills' / name / 'SKILL.md'
        frontmatter, _ = parse_frontmatter(source.read_text(encoding='utf-8'))
        if frontmatter.get('name') != name or not isinstance(frontmatter.get('description'), str):
            raise ValueError(f'Skill metadata does not match registration: {name}')
        result.append({'name': name, 'description': frontmatter['description']})
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate UI locale key: {key}')
        result[key] = value
    return result


def validate_locale(expected, path=None):
    path = path or ROOT / 'forsic_plugin/ui/skills.ko.json'
    locale = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    if locale.get('schema') != 'forsic.ui.skill-copy.v1' or locale.get('locale') != 'ko':
        raise ValueError('Unsupported Korean skill UI locale schema')
    skills = locale.get('skills')
    names = {item['name'] for item in expected}
    if not isinstance(skills, dict) or set(skills) != names:
        actual = set(skills) if isinstance(skills, dict) else set()
        raise ValueError(f'Korean skill coverage mismatch: missing={sorted(names - actual)}, extra={sorted(actual - names)}; edit forsic_plugin/ui/skills.ko.json')
    for item in expected:
        name, source = item['name'], item['description']
        copy = skills[name]
        if not isinstance(copy, dict):
            raise ValueError(f'Invalid Korean skill entry: {name}')
        for field, limit in (('display_name', 80), ('description', 600), ('category', 80)):
            value = copy.get(field)
            if (not isinstance(value, str) or not value.strip() or len(value) > limit
                    or not re.search('[가-힣]', value) or any(ord(char) < 32 for char in value)):
                raise ValueError(f'Invalid Korean skill {field}: {name}')
        terms = copy.get('search_terms')
        if (not isinstance(terms, list) or not terms or len(terms) > 20
                or any(not isinstance(term, str) or not term.strip() or len(term) > 80
                       or any(ord(char) < 32 for char in term) for term in terms)
                or len(set(terms)) != len(terms)):
            raise ValueError(f'Invalid Korean search terms: {name}')
        if copy.get('source_description_sha256') != hashlib.sha256(source.encode()).hexdigest():
            raise ValueError(f'Stale Korean skill translation: {name}; review translation and update source_description_sha256 in forsic_plugin/ui/skills.ko.json')
    return locale


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Regenerate the static catalogue from registered metadata')
    args = parser.parse_args()
    expected = registered_metadata()
    target = ROOT / 'forsic_plugin' / 'dashboard' / 'dist' / 'skills.json'
    validate_locale(expected)  # Never regenerate reviewed Korean copy from English.
    if args.write:
        target.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if json.loads(target.read_text(encoding='utf-8')) != expected:
        raise SystemExit('Dashboard skill metadata is stale; run python3 verify_dashboard_skills.py --write')
    print(f'Dashboard skill metadata verified: {len(expected)} registered skills; Korean UI coverage verified')


if __name__ == '__main__':
    main()
