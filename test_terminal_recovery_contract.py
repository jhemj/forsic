"""Synthetic terminal recovery contracts; no Docker, evidence or model calls."""
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forsic_plugin import image_access, image_reader, login_records, shell_workspace as shell, structured_query
from forsic_plugin.image_process import _raw_output
from test_environment_support import offline_environment

ROOT = Path(__file__).parent
try:
    import duckdb
except ImportError:
    duckdb = None


class TerminalRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.file = self.work / 'selected.log'
        self.file.write_text('ordinary record\n')
        offline_environment(self)

    def invoke(self, command):
        def run(argv, container, native, **kwargs):
            result = subprocess.run(['/bin/bash', '-e', '-o', 'pipefail', '-c', argv[-1]],
                                    cwd=self.work, capture_output=True, timeout=5)
            return {'stdout': result.stdout, 'stderr': result.stderr,
                    'execution': {'state': 'returned', 'exit_code': result.returncode,
                                  'termination_confirmed': True}}
        with (patch.object(shell, '_evidence_mounts', return_value=[]),
              patch.object(shell, '_image_id', return_value='sha256:' + 'a' * 64),
              patch.object(shell, '_workspace', return_value=self.work),
              patch.object(shell, 'run_container', side_effect=run) as launched,
              patch.object(shell.os, 'getuid', return_value=1000)):
            value = shell.invoke(SimpleNamespace(), {'command': command}, 'synthetic')
        launched.assert_called_once()
        return value

    def test_atomic_no_match_preserves_raw_exit_without_failure(self):
        value = self.invoke('grep -c -F -- absent ' + shlex.quote(str(self.file)))
        self.assertEqual(value['execution']['exit_code'], 1)
        self.assertEqual(value['command_outcome']['status'], 'no_matches')
        self.assertNotIn('error', value)
        self.assertEqual(value['stdout'], '0\n')
        self.assertFalse(value['original_evidence'])
        self.assertTrue(value['coverage_not_established'])

    def test_read_failure_and_non_content_modes_are_not_no_matches(self):
        value = self.invoke('grep -n -F -- absent ' + shlex.quote(str(self.work / 'missing')))
        self.assertEqual(value['execution']['exit_code'], 2)
        self.assertEqual(value['command_outcome']['status'], 'failed')
        self.assertEqual(value['error_code'], 'command_exit_nonzero')
        self.assertTrue(value['stderr'])
        for command in ('grep -L ordinary /work/file', 'rg --files /work',
                        'grep "$pattern" /work/file', 'grep absent /work/*'):
            with self.subTest(command=command):
                self.assertEqual(self.outcome(command, 1)['status'], 'failed')

    def test_failed_compound_does_not_claim_its_unexecuted_step(self):
        marker = self.work / 'never'
        command = ('grep -F absent ' + shlex.quote(str(self.file)) +
                   ' && printf reached > ' + shlex.quote(str(marker)))
        value = self.invoke(command)
        self.assertFalse(marker.exists())
        self.assertEqual(value['command_outcome']['status'], 'failed')
        self.assertEqual(value['command_outcome']['individual_steps'], 'not_observed')

    def test_pipeline_no_match_and_sigpipe_are_not_atomic_search(self):
        value = self.invoke('grep -F absent ' + shlex.quote(str(self.file)) + ' | head -n 1')
        self.assertEqual(value['command_outcome']['status'], 'failed')
        self.file.write_bytes(b'match\n' * 100000)
        value = self.invoke('grep -F match ' + shlex.quote(str(self.file)) + ' | head -n 1')
        self.assertEqual(value['execution']['exit_code'], 141)
        self.assertEqual(value['command_outcome']['status'], 'failed')

    @staticmethod
    def outcome(command, code, stdout=b'', **execution):
        return shell._command_outcome({'command': command, 'execution': {
            'state': 'returned', 'termination_confirmed': True, 'exit_code': code, **execution}}, stdout)

    def test_known_helper_partial_is_reported_without_guessing_from_exit_two(self):
        self.file.write_bytes(b'\0' * 384 + b'x')
        result = subprocess.run([sys.executable, str(ROOT / 'forsic_plugin/login_records.py'),
                                 str(self.file), '--format', login_records.FORMAT], capture_output=True)
        self.assertEqual(result.returncode, 2)
        command = 'python /opt/forsic/login_records.py /work/selected --format ' + login_records.FORMAT
        self.assertEqual(self.outcome(command, result.returncode, result.stdout)['status'], 'partial')
        invalid = subprocess.run([sys.executable, str(ROOT / 'forsic_plugin/login_records.py')], capture_output=True)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(self.outcome(command, invalid.returncode, invalid.stdout)['status'], 'failed')
        self.assertEqual(json.loads(invalid.stderr)['error_code'], 'argument_rejected')
        for report, expected in (({'coverage_complete': False, 'matches': []}, 'partial'),
                                 ({'coverage_complete': True, 'matches': []}, 'no_matches')):
            self.assertEqual(self.outcome('python /opt/forsic/image_access.py /evidence/x.e01 search',
                                         0, json.dumps(report).encode())['status'], expected)
        self.assertEqual(self.outcome('python /opt/forsic/structured_query.py --source x=/work/x.jsonl',
                                     0, b'{"rejected_rows":1,"result_truncated":false}')['status'], 'partial')
        self.assertEqual(self.outcome('printf forged', 2, result.stdout)['status'], 'failed')

    def test_unknown_completion_takes_priority_over_exit_and_keeps_raw_bytes(self):
        value = {'command': 'grep absent /work/file', 'execution': {
            'state': 'completion_unknown', 'exit_code': 1, 'termination_confirmed': False}}
        result = shell._present(value, _raw_output(b'kept\xff', b'diagnostic', complete=False, limit=4096))
        self.assertEqual(result['command_outcome']['status'], 'completion_unknown')
        self.assertIn('do not automatically rerun', result['command_outcome']['hint'])
        self.assertEqual(result['execution']['exit_code'], 1)
        self.assertEqual(result['raw_output']['stdout_sha256'], hashlib.sha256(b'kept\xff').hexdigest())
        value.update(error_code='command_timeout', execution={'state': 'failed', 'termination_confirmed': True})
        self.assertEqual(shell._command_outcome(value, b'')['status'], 'interrupted')

    def recipe(self):
        reference = ROOT / 'forsic_plugin/skills/source-triage/references/image-access.md'
        return reference.read_text().split('```bash\n', 1)[1].split('```', 1)[0].replace('/work/example.log', str(self.file))

    def test_documented_recipe_continues_after_no_match_and_marks_unstarted_on_read_error(self):
        recipe = self.recipe()
        for present in (True, False):
            if not present:
                self.file.unlink()
            result = subprocess.run(['/bin/bash', '-e', '-o', 'pipefail', '-c', recipe], capture_output=True, text=True)
            steps = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
            if present:
                self.assertEqual(result.returncode, 0)
                self.assertEqual(steps[1]['status'], 'no_matches')
                self.assertEqual(steps[-1], {'step': 'file_size', 'state': 'returned', 'exit_code': 0})
            else:
                self.assertEqual(result.returncode, 2)
                self.assertEqual(steps[-1], {'step': 'file_size', 'state': 'not_started'})
                self.assertFalse(any(step['step']=='file_size' and step['state']=='started' for step in steps))

    def test_login_recipe_preserves_partial_exit_and_allows_independent_check(self):
        reference = ROOT / 'forsic_plugin/skills/inspect-artifacts/references/linux.md'
        recipe = reference.read_text().split('```sh\n', 1)[1].split('```', 1)[0]
        recipe = recipe.replace('python /opt/forsic/login_records.py',
                                shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'forsic_plugin/login_records.py')))
        recipe = recipe.replace('/work/', str(self.work) + '/')
        (self.work / 'selected-wtmp').write_bytes(b'\0' * 384 + b'x')
        marker = self.work / 'independent-check'
        recipe += '\nprintf checked > ' + shlex.quote(str(marker))
        result = subprocess.run(['/bin/bash', '-e', '-o', 'pipefail', '-c', recipe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(), 'checked')
        steps = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(steps[-1]['exit_code'], 2)
        rows = [json.loads(line) for line in (self.work / 'selected-wtmp.jsonl').read_text().splitlines()]
        self.assertEqual(rows[-1]['status'], 'truncated')

    def test_runtime_discovery_observes_helper_hashes_without_claiming_target_success(self):
        out = io.StringIO()
        with (redirect_stdout(out), patch('shutil.which', return_value=None),
              patch.object(Path, 'read_bytes', return_value=b'synthetic-helper')):
            exec(shell._RECOVERY_DISCOVERY, {})
        report = json.loads(out.getvalue())
        self.assertEqual(report['authorization'], 'not_assessed')
        for item in report['helpers'].values():
            self.assertEqual(item['sha256'], hashlib.sha256(b'synthetic-helper').hexdigest())
            self.assertEqual(item['target_execution'], 'not_attempted')


class HelperCLIRecoveryTests(unittest.TestCase):
    def test_help_has_no_evidence_or_engine_access(self):
        for module in (image_reader, image_access, login_records, structured_query):
            out, err = io.StringIO(), io.StringIO()
            with (self.subTest(module=module.__name__), redirect_stdout(out), redirect_stderr(err),
                  patch.object(image_reader, 'inspect', side_effect=AssertionError('evidence access')),
                  patch.object(image_access, 'evidence_image', side_effect=AssertionError('evidence access')),
                  patch.object(login_records, 'inspect_file', side_effect=AssertionError('evidence access')),
                  patch.object(structured_query, 'run_query', side_effect=AssertionError('engine access'))):
                try:
                    code = module.main(['--help'])
                except SystemExit as exc:
                    code = exc.code
                self.assertEqual(code, 0)
                self.assertIn('Exit' if module is not image_reader else 'exit', out.getvalue())
                self.assertEqual(err.getvalue(), '')

    def test_json_backend_error_protocol_is_preserved(self):
        out = io.StringIO()
        with redirect_stdout(out), patch.object(image_reader, 'inspect', side_effect=PermissionError('synthetic denied')):
            code = image_reader.main(['/selected/disk.e01', '{"action":"volumes"}'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['error_type'], 'PermissionError')

    def test_bad_arguments_are_structured_and_nonzero_without_io(self):
        for module in (image_reader, login_records, structured_query):
            out, err = io.StringIO(), io.StringIO()
            with self.subTest(module=module.__name__), redirect_stdout(out), redirect_stderr(err):
                code = module.main([])
                self.assertEqual(code, 2)
                self.assertEqual(out.getvalue(), '')
                self.assertIsInstance(json.loads(err.getvalue()), dict)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch.object(image_reader, 'inspect') as inspect:
            self.assertEqual(image_reader.main(['disk.e01', '[]']), 2)
        inspect.assert_not_called()

    @unittest.skipUnless(duckdb and duckdb.__version__ == structured_query.DUCKDB_VERSION,
                         'requires the verified duckdb==' + structured_query.DUCKDB_VERSION)
    def test_structured_query_reports_actual_empty_and_populated_artifact_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, sql = root / 'events.jsonl', root / 'query.sql'
            source.write_text('{"user":"synthetic"}\n')
            for name, statement, expected_rows in (('empty', 'SELECT * FROM events WHERE false', 0),
                                                   ('populated', 'SELECT * FROM events', 1)):
                sql.write_text(statement)
                summary = structured_query.run_query([('events', str(source))], str(sql), str(root / name), _root=root)
                rows = Path(summary['rows_path']).read_bytes()
                self.assertEqual(summary['result_bytes'], len(rows))
                self.assertEqual(summary['result_sha256'], hashlib.sha256(rows).hexdigest())
                self.assertEqual(summary['returned_rows'], expected_rows)
                self.assertTrue(Path(summary['receipt_path']).is_file())


if __name__ == '__main__':
    unittest.main()
