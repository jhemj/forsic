"""Native exact-execution recovery; synthetic receipts and mocked Docker only."""
from copy import deepcopy
import json
import subprocess
import unittest
from unittest.mock import patch

from forsic_plugin.capabilities import capability
import test_shell_integration as fixtures


class ExecutionReconciliationTests(unittest.TestCase):
    setUp = fixtures.ShellIntegrationTests.setUp
    call = fixtures.ShellIntegrationTests.call
    output = fixtures.ShellIntegrationTests.output

    def uncertain(self, **changes):
        execution = dict(state='completion_unknown', backend='docker',
            container='forsic-shell-' + 'a' * 32, exit_code=0, termination_confirmed=False,
            cleanup={'confirmed':False, 'client_reaped':True, 'error_type':'TimeoutExpired'})
        execution.update(changes)
        with patch('forsic_plugin.shell_workspace.invoke', return_value=self.output(
                execution=execution, error='Cleanup uncertain', error_code='command_cleanup_unknown')):
            return self.call('forsic_shell', {'command':'synthetic command'})

    def reconcile(self, out):
        return self.call('forsic_reporting', {'action':'execution_reconcile', 'started_id':out['started_id']})

    def absent(self):
        return subprocess.CompletedProcess([], 1, b'', b'Error: No such object: forsic-shell-' + b'a' * 32 + b'\n')

    def test_exact_absence_appends_once_preserves_original_and_releases_future_work(self):
        out = self.uncertain()
        original = deepcopy(self.case.event(out['evidence_id']))
        with patch('forsic_plugin.execution_contract.subprocess.run', return_value=self.absent()) as inspect:
            view = self.call('forsic_reporting', {'action':'execution', 'started_id':out['started_id']})
            inspect.assert_not_called()  # ordinary ledger lookup remains read-only
            self.assertEqual(view['reconcile_call']['arguments']['action'], 'execution_reconcile')
            receipt = self.reconcile(out)
            self.assertNotIn('error', receipt, receipt)
            self.assertTrue(receipt['termination_confirmed'])
            self.assertFalse(receipt['command_rerun'])
            self.assertEqual(self.reconcile(out)['receipt_id'], receipt['receipt_id'])
            inspect.assert_called_once()
            self.assertEqual(inspect.call_args.args[0][-1], 'forsic-shell-' + 'a' * 32)
        self.assertEqual(self.case.event(out['evidence_id']), original)
        with self.case.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='execution_reconciled'").fetchone()[0], 1)
        with patch('forsic_plugin.shell_workspace.invoke', return_value=self.output()) as backend:
            next_result = self.call('forsic_shell', {'command':'different authorized work'})
        self.assertNotIn('error', next_result, next_result)
        backend.assert_called_once()

    def test_running_missing_permissions_and_timeout_cannot_clear_fence(self):
        outcomes = [subprocess.CompletedProcess([], 0, b'{"Running":true}', b''),
            subprocess.CompletedProcess([], 0, b'{"Running":false,"Status":"exited"}', b''),
            subprocess.CompletedProcess([], 1, b'', b'Cannot connect to Docker daemon'),
            subprocess.CompletedProcess([], 1, b'', b'No such unrelated resource'),
            subprocess.TimeoutExpired('inspect', 10)]
        for index, outcome in enumerate(outcomes):
            with self.subTest(index=index):
                self.binding['conversation_id'] = 'uncertain-' + str(index)
                out = self.uncertain()
                options = {'side_effect':outcome} if isinstance(outcome, Exception) else {'return_value':outcome}
                with patch('forsic_plugin.execution_contract.subprocess.run', **options):
                    self.assertIn('error', self.reconcile(out))
                with patch('forsic_plugin.shell_workspace.invoke') as backend:
                    self.assertEqual(self.call('forsic_shell', {'command':'next'})['error_code'], 'execution_completion_unknown')
                    backend.assert_not_called()
        with self.case.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='execution_reconciled'").fetchone()[0], 0)

    def test_foreign_session_invalid_container_and_unreaped_client_are_not_inspected(self):
        out = self.uncertain()
        self.binding['conversation_id'] = 'foreign-conversation'
        with patch('forsic_plugin.execution_contract.subprocess.run') as inspect:
            self.assertIn('error', self.reconcile(out))
            inspect.assert_not_called()
        for index, changes in enumerate([
                {'container':'--help'}, {'container':'someone-elses-container'},
                {'cleanup':{'client_reaped':False}}, {'backend':'other'}, {'state':'returned'}]):
            with self.subTest(changes=changes):
                self.binding['conversation_id'] = 'invalid-' + str(index)
                out = self.uncertain(**changes)
                with patch('forsic_plugin.execution_contract.subprocess.run') as inspect:
                    self.assertIn('error', self.reconcile(out))
                    inspect.assert_not_called()

    def test_effects_do_not_claim_readonly_or_evidence_execution(self):
        effects = capability('forsic_reporting', {'action':'execution_reconcile'})
        self.assertTrue(effects['subprocess'])
        self.assertTrue(effects['case_ledger_write'])
        for key in ('comparison_readonly','target_write','workspace_write','evidence_read','network','target_code_execution'):
            self.assertFalse(effects[key], key)
