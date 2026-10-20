"""Public schema annotations stay compact; canonical validation remains intact."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from forsic_plugin import register
from forsic_plugin.report_driven.investigation_state import input_problems, validate_native_plan
from tools.schema_sanitizer import sanitize_tool_schemas


def registered():
    ctx = Mock()
    ctx.get_config.return_value = None
    register(ctx)
    return {call.kwargs['name']: call.kwargs['schema'] for call in ctx.register_tool.call_args_list}


class SchemaPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.after = registered()

    def test_sanitized_native_schema_keeps_typed_required_branches_without_refs(self):
        for name in ('forsic_question', 'forsic_mission'):
            original = deepcopy(self.after[name])
            wire = sanitize_tool_schemas([{'type': 'function', 'function': original}])[0]['function']
            self.assertEqual(wire['parameters']['required'], original['parameters']['required'])
            self.assertFalse(wire['parameters']['additionalProperties'])
            self.assertNotIn('$ref', json.dumps(wire))
        mission = self.after['forsic_mission']['parameters']
        branches = mission['properties']['tool_arguments']['anyOf']
        self.assertEqual(len({branch['title'] for branch in branches}), len(branches))
        for branch in branches:
            self.assertEqual(branch['type'], 'object')
            self.assertFalse(branch['additionalProperties'])
            self.assertIsInstance(branch['required'], list)
            self.assertTrue(set(branch['required']).issubset(branch['properties']))

    def test_actual_value_validation_accepts_supported_inputs_and_rejects_bad_inputs(self):
        samples = {
            'forsic_question': [
                {'operation_id': 'new', 'question': 'What happened?', 'scope': 'log', 'answer': 'Unknown'},
                {'operation_id': 'update', 'expected_revision': 1, 'judgment': {
                    'citation_refs': ['C1:synthetic'], 'assertion_kind': 'fact', 'reasoning_summary': 'Exact source'}},
                {'operation_id': False}, {'operation_id': 'bad', 'expected_revision': 0},
                {'operation_id': 'bad', 'judgment': {'citation_refs': [7]}},
                {'operation_id': 'bad', 'closure': {'retain_unresolved': False}},
                {'operation_id': 'bad', 'extra': 'not permitted'},
            ],
            'forsic_mission': [
                {'operation_id': 'plan', 'question_id': 'Q-1', 'expected_revision': 1, 'gap_id': 'G-1',
                 'why_it_matters': 'Distinguish', 'inconclusive_rule': 'No data', 'limits': [], 'reopen_conditions': [],
                 'tool_name': 'forsic_image_read', 'tool_arguments': {'path': 'disk.e01', 'action': 'read', 'file_path': '/log'}},
                {'operation_id': 'bad'},
                {'operation_id': 'bad', 'tool_arguments': {'path': 'disk.e01', 'action': 'read'}},
                {'operation_id': 'bad', 'tool_arguments': {'command': 'x', 'timeout_seconds': 1801}},
            ],
        }
        for name, values in samples.items():
            for index, value in enumerate(values):
                with self.subTest(tool=name, value=value):
                    new = input_problems(value, self.after[name]['parameters'])
                    if index >= (2 if name == 'forsic_question' else 1):
                        self.assertTrue(new)
                    else:
                        self.assertFalse(new)

    def test_json_schema_rejects_ambiguous_gap_selectors_and_malformed_citations(self):
        from jsonschema import Draft202012Validator
        question = Draft202012Validator(self.after['forsic_question']['parameters'])
        selected = {'operation_id': 'gap', 'gap_dispositions': [{'gap_id': 'G-1',
            'disposition': 'resolved', 'reason': 'Retained source', 'reopen_conditions': ['New source']}]}
        self.assertTrue(question.is_valid(selected))
        both = deepcopy(selected)
        both['gap_dispositions'][0]['gap_text'] = 'Ambiguous selector'
        self.assertFalse(question.is_valid(both))
        self.assertFalse(question.is_valid({'operation_id': 'bad', 'judgment': {
            'citation_refs': [7], 'assertion_kind': 'fact', 'reasoning_summary': 'Exact text'}}))
        self.assertFalse(question.is_valid({'operation_id': 'bad', 'expected_revision': False}))

    def test_public_image_menu_rejects_obsolete_fields_but_canonical_plans_keep_them(self):
        public = self.after['forsic_image_files']['parameters']
        for field in ('start_line', 'length', 'recursive'):
            self.assertNotIn(field, public['properties'])
            self.assertTrue(input_problems({'path': 'disk.e01', 'action': 'volumes', field: 1}, public))
        mission = self.after['forsic_mission']['parameters']
        branches = {branch['title']: branch for branch in mission['properties']['tool_arguments']['anyOf']}
        canonical = branches['forsic_image_files']
        for field in ('start_line', 'length', 'recursive'):
            self.assertIn(field, canonical['properties'])
        saved_read = {'tool_name': 'forsic_image_files', 'tool_arguments': {
            'path': 'disk.e01', 'action': 'read', 'file_path': '/log',
            'start_line': 2, 'length': 4, 'recursive': False}}
        self.assertFalse(input_problems(saved_read['tool_arguments'], canonical))
        validate_native_plan(saved_read, branches)
        invalid = deepcopy(saved_read)
        invalid['tool_arguments'].pop('file_path')
        with self.assertRaises(ValueError):
            validate_native_plan(invalid, branches)

