"""Offline executable discovery regression: no Docker, evidence scans or model calls."""
import contextlib
import io
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from forsic_plugin import shell_workspace as shell
from forsic_plugin.execution_contract import ExecutionFailure
from test_environment_support import offline_environment


class RecoveryCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        offline_environment(self)

    def discover(self, available, run):
        output=io.StringIO()
        with patch('shutil.which',side_effect=lambda name: '/usr/bin/'+name if name in available else None), \
             patch('subprocess.run',side_effect=run) as probe, contextlib.redirect_stdout(output):
            exec(shell._RECOVERY_DISCOVERY,{})
        return json.loads(output.getvalue()),probe

    def test_runtime_observation_separates_presence_support_and_execution(self):
        def run(argv,**kwargs):
            self.assertEqual(kwargs,{'capture_output':True,'timeout':3,'check':False})
            # A format-list request may return nonzero; retain it without claiming
            # unsupported target format, missing program or approval requirement.
            if argv[0].endswith('fsstat'):
                return SimpleNamespace(returncode=1,stdout=b'',stderr=b'Supported: ext4 ntfs')
            return SimpleNamespace(returncode=0,stdout=b'Synthetic version 1',stderr=b'')
        value,probe=self.discover({'photorec','fsstat','fls'},run)
        self.assertTrue(value['runtime_verified'])
        self.assertEqual(value['executables']['photorec']['state'],'available')
        self.assertEqual(value['executables']['tsk_recover']['state'],'missing_executable')
        formats=value['executables']['fsstat']['probes'][0]
        self.assertEqual(formats['exit_code'],1)
        self.assertEqual(formats['stderr'],'Supported: ext4 ntfs')
        self.assertEqual(value['target_format_support'],'not_checked')
        self.assertEqual(value['recovery_execution'],'not_attempted')
        self.assertEqual(value['authorization'],'not_assessed')
        self.assertEqual(probe.call_count,2)
        for call in probe.call_args_list:
            self.assertFalse(any('/evidence' in arg or '/work' in arg for arg in call.args[0]))

    def test_failed_probe_is_not_missing_executable_and_output_is_bounded(self):
        def run(argv,**kwargs):
            if argv[0].endswith('photorec'):
                raise subprocess.TimeoutExpired(argv,3)
            return SimpleNamespace(returncode=0,stdout=b'x'*2000,stderr=b'')
        value,_=self.discover({'photorec','ewfexport'},run)
        photorec=value['executables']['photorec']
        self.assertEqual(photorec['state'],'available')
        self.assertEqual(photorec['probes'][0]['state'],'probe_failed')
        export=value['executables']['ewfexport']['probes'][0]
        self.assertEqual(len(export['stdout']),1600)
        self.assertTrue(export['output_truncated'])

    def test_binary_strings_presence_probe_and_missing_are_distinct(self):
        def run(argv, **kwargs):
            self.assertEqual(argv, ['/usr/bin/strings', '--version'])
            self.assertEqual(kwargs, {'capture_output':True, 'timeout':3, 'check':False})
            return SimpleNamespace(returncode=0, stdout=b'GNU strings synthetic', stderr=b'')
        missing, probe = self.discover(set(), run)
        probe.assert_not_called()
        self.assertEqual(missing['executables']['strings']['state'], 'missing_executable')
        observed, probe = self.discover({'strings'}, run)
        self.assertEqual(observed['executables']['strings']['state'], 'available')
        self.assertEqual(observed['executables']['strings']['probes'][0]['stdout'], 'GNU strings synthetic')
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(observed['target_format_support'], 'not_checked')
        self.assertEqual(observed['recovery_execution'], 'not_attempted')
        declared = shell.workspace_info()['declared_capabilities']['binary_inspection']
        self.assertFalse(declared['runtime_verified'])
        self.assertEqual(declared['input_limit_bytes'], 1048576)
        self.assertEqual(shlex.split(declared['bounded_command']),
                         ['head', '-c', '1048576', '--', '/work/selected-binary', '|',
                          'strings', '-a', '-n', '8', '-t', 'x'])

    def test_declared_binary_recipe_limits_bytes_before_strings(self):
        # A synthetic executable observes the actual pipe input/arguments, so
        # the recipe's bound is exercised without relying on installed binutils
        # or claiming that any real binary has been inspected.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'selected binary'
            binary.write_bytes(b'A' * 1048576 + b'UNINSPECTED-TAIL')
            executable = root / 'strings'
            executable.write_text('#!' + sys.executable + '\n'
                'import sys\n'
                'assert sys.argv[1:] == ["-a", "-n", "8", "-t", "x"]\n'
                'data = sys.stdin.buffer.read()\n'
                'print(len(data), data[-2:].decode())\n')
            executable.chmod(0o700)
            command = shell.workspace_info()['declared_capabilities']['binary_inspection']['bounded_command']
            command = command.replace('/work/selected-binary', shlex.quote(str(binary)))
            result = subprocess.run(['/bin/bash', '-e', '-o', 'pipefail', '-c', command],
                env={'PATH': str(root) + ':/usr/bin:/bin'}, capture_output=True, timeout=5, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b'1048576 AA\n')
            self.assertEqual(binary.stat().st_size, 1048576 + len(b'UNINSPECTED-TAIL'))

    def test_binary_strings_probe_failure_preserves_executable_presence(self):
        def run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 3)
        value, probe = self.discover({'strings'}, run)
        item = value['executables']['strings']
        self.assertEqual(item['state'], 'available')
        self.assertEqual(item['probes'][0]['state'], 'probe_failed')
        self.assertEqual(value['target_format_support'], 'not_checked')
        self.assertEqual(value['recovery_execution'], 'not_attempted')
        self.assertEqual(probe.call_count, 1)

    def invoke(self, payload, *, state='returned', code=0, image_error=None):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_failure=(patch.object(shell,'_execution_snapshot',side_effect=image_error)
                              if image_error else contextlib.nullcontext())
            with snapshot_failure, patch.object(shell,'_evidence_mounts',return_value=[]), \
                 patch.object(shell,'_workspace',return_value=Path(directory)), \
                 patch.object(shell,'_workspace_lock',return_value=contextlib.nullcontext()), \
                 patch.object(shell.os,'getuid',return_value=1000), \
                 patch.object(shell,'run_container',return_value={
                     'stdout':payload,'stderr':b'',
                     'execution':{'state':state,'exit_code':code,'termination_confirmed':state=='returned'}}) as run:
                value=shell.invoke(SimpleNamespace(),{'action':'capabilities'},'synthetic-conversation')
        return value,run

    def test_builtin_action_uses_existing_isolated_executor_and_receipt(self):
        metadata,_=self.discover(set(),lambda *args,**kwargs: self.fail('missing executable was run'))
        value,run=self.invoke(json.dumps(metadata).encode())
        self.assertEqual(value['runtime_capabilities']['state'],'observed')
        argv=run.call_args.args[0]
        self.assertEqual(shlex.split(argv[-1]),['python','-c',shell._RECOVERY_DISCOVERY])
        self.assertIn('--read-only',argv)
        self.assertEqual(argv[argv.index('--network')+1],'none')
        self.assertEqual(run.call_args.kwargs['timeout_seconds'],30)
        self.assertEqual(value['execution']['state'],'returned')
        self.assertEqual(value['execution_policy']['image_id'],'sha256:'+'a'*64)
        self.assertFalse(value['original_evidence'])
        self.assertTrue(value['coverage_not_established'])

    def test_unavailable_image_is_not_unsupported_or_recovery_attempt(self):
        error=ExecutionFailure('Synthetic unavailable image',error_code='analysis_image_unavailable',
            execution={'state':'not_started','termination_confirmed':True})
        value,run=self.invoke(b'',image_error=error)
        run.assert_not_called()
        self.assertFalse(value['runtime_capabilities']['runtime_verified'])
        self.assertEqual(value['runtime_capabilities']['state'],'not_observed')
        self.assertEqual(value['runtime_capabilities']['recovery_execution'],'not_attempted')
        self.assertEqual(value['error_code'],'analysis_image_unavailable')

    def test_incomplete_failed_or_invalid_discovery_is_not_verified(self):
        for payload,state,code in ((b'{}','completion_unknown',0),(b'{}','returned',1),
                                   (b'not JSON','returned',0),(b'{}','returned',0)):
            with self.subTest(state=state,code=code,payload=payload):
                value,_=self.invoke(payload,state=state,code=code)
                self.assertFalse(value['runtime_capabilities']['runtime_verified'])
                self.assertIn('error_code',value)

    def test_capabilities_action_cannot_accept_targets_or_commands(self):
        self.assertEqual(shell.validate({'action':'capabilities'}),{'action':'capabilities'})
        self.assertEqual(shell.validate({'action':'run','command':'printf ok'})['command'],'printf ok')
        for args in ({},{'action':'other'},{'action':'capabilities','command':'anything'},
                     {'action':'capabilities','cwd':'/work'},{'action':'capabilities','path':'/evidence/image.e01'}):
            with self.subTest(args=args),self.assertRaises(ValueError):
                shell.validate(args)


if __name__=='__main__':
    unittest.main()
