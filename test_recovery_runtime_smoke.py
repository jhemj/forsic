"""Offline failure/cleanup contracts for the synthetic recovery smoke launcher."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('recovery_smoke', Path(__file__).parent / 'tools/smoke_recovery_runtime.py')
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class RecoverySmokeTest(unittest.TestCase):
    def test_client_failure_always_removes_the_unique_container_and_retains_receipt(self):
        for failure, exit_code in ((subprocess.TimeoutExpired('docker', 240), 124), (OSError('synthetic launcher failure'), 1)):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / 'output'
                image = json.dumps([{'Id': 'sha256:synthetic-image', 'Size': 1}]).encode()
                with patch.object(smoke.os, 'getuid', return_value=1000), patch.object(smoke.os, 'getgid', return_value=1000), \
                     patch.object(smoke.sys, 'argv', ['smoke', '--image', 'synthetic:candidate', '--output', str(output)]), \
                     patch.object(smoke.subprocess, 'check_output', return_value=image), \
                     patch.object(smoke.subprocess, 'run', side_effect=[failure, subprocess.CompletedProcess([], 0, 'removed', '')]) as run:
                    with self.assertRaises(SystemExit) as stopped:
                        smoke.main()
                self.assertEqual(stopped.exception.code, exit_code)
                command = run.call_args_list[0].args[0]
                name = command[command.index('--name') + 1]
                self.assertTrue(name.startswith('forsic-recovery-smoke-'))
                self.assertEqual(run.call_args_list[1].args[0], ['docker', 'rm', '-f', name])
                receipt = json.loads((output / 'runtime-smoke.json').read_text())
                self.assertEqual(receipt['exit_code'], exit_code)
                self.assertEqual(receipt['cleanup']['exit_code'], 0)
                self.assertEqual(receipt['image_id'], 'sha256:synthetic-image')

    def test_nonempty_output_is_rejected_before_running_docker(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / 'existing.txt'
            marker.write_text('retain me')
            with patch.object(smoke.os, 'getuid', return_value=1000), \
                 patch.object(smoke.sys, 'argv', ['smoke', '--image', 'synthetic:candidate', '--output', temporary]), \
                 patch.object(smoke.subprocess, 'check_output') as inspect:
                with self.assertRaises(SystemExit) as stopped:
                    smoke.main()
            self.assertEqual(stopped.exception.code, 2)
            inspect.assert_not_called()
            self.assertEqual(marker.read_text(), 'retain me')


if __name__ == '__main__':
    unittest.main()
