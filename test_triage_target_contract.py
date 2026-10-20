"""Explicit retained-file selection without inferred paths, kinds or profiles."""
import json
import unittest
from forsic_plugin.sensitive_policy import export_receipt, export_receipt_lines, ScanRejected
from forsic_plugin.triage_policy import validate, properties
from forsic_plugin.shell_workspace import schema


class TriageTargetContractTests(unittest.TestCase):
    def args(self, **target):
        return {'action': 'triage', 'profile': 'windows_evtx', 'targets': [
            {'kind': 'workspace_export', 'source_id': 'a' * 32,
             'source_version': 'b' * 64, 'pointer': '/stdout', **target}]}

    def test_missing_fields_name_the_exact_target(self):
        for field in ('kind', 'pointer', 'source_id', 'source_version'):
            args = self.args()
            args['targets'].append(dict(args['targets'][0]))
            del args['targets'][1][field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, r'targets\[1\]\.' + field):
                validate(args)
        args = self.args()
        args['profile'] = 'linux_persistence'
        with self.assertRaisesRegex(ValueError, r'targets\[0\]\.triage_format'):
            validate(args)

    def test_defaults_preserve_exact_binary_and_handle_selection(self):
        checked = validate(self.args())
        self.assertEqual(checked['targets'][0]['encoding'], 'auto')
        self.assertEqual(checked['targets'][0]['format'], 'text')
        args = self.args(source_handle='synthetic handle')
        del args['targets'][0]['source_id']
        del args['targets'][0]['source_version']
        self.assertEqual(validate(args)['targets'][0]['kind'], 'workspace_export')
        for target in ({'kind': 'retained_text'}, {'format': 'csv'}, {'encoding': 'utf-16'}):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate(self.args(**target))

    def test_shared_schema_explains_action_specific_contract(self):
        self.assertIn('kind', properties()['targets']['items']['required'])
        self.assertIn('For triage, kind is required', schema()['properties']['targets']['description'])
        self.assertEqual(schema()['properties']['targets']['items']['required'], ['pointer'])

    def test_export_line_is_explicit_positive_integer_for_stdout_exports_only(self):
        checked = validate(self.args(export_line=4))
        self.assertEqual(checked['targets'][0]['export_line'], 4)
        for changes in ({'export_line': True}, {'export_line': 0}, {'export_line': -1},
                {'export_line': '4'}, {'export_line': 1.5},
                {'export_line': 2, 'kind': 'evidence_file'},
                {'export_line': 2, 'pointer': '/path'}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'export_line'):
                validate(self.args(**changes))
        self.assertIn('export_line', properties()['targets']['items']['properties'])

    def test_receipt_parser_preserves_explicit_selection_and_destination_boundary(self):
        receipt = {'derived': True, 'complete_file': True, 'hash_scope': 'exported_bytes',
            'sha256': 'a'*64, 'destination': '/work/export'}
        stdout = '== selected\n' + json.dumps(receipt) + '\n'
        self.assertEqual(list(export_receipt_lines(stdout)), [(2, receipt)])
        self.assertEqual(export_receipt(stdout, 2), receipt)
        self.assertEqual(export_receipt(json.dumps(receipt, indent=2)), receipt)
        with self.assertRaisesRegex(ScanRejected, 'sensitive_export_selection_required'):
            export_receipt(stdout)
        for changes in ({'complete_file': False}, {'derived': False}, {'sha256': 'invalid'},
                {'hash_scope': 'source_bytes'}, {'destination': '/work/../outside'},
                {'destination': '/outside'}, {'destination': '/work'},
                {'destination': None}, {'destination': '/work/nul\0file'}):
            with self.subTest(changes=changes):
                invalid = json.dumps({**receipt, **changes})
                self.assertEqual(list(export_receipt_lines(invalid)), [])
                with self.assertRaises(ScanRejected):
                    export_receipt(invalid)
