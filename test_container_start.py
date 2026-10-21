"""Container startup is daemon metadata, not an interpretation of command output."""
import base64
import io
import json
import os
import subprocess
import unittest
import uuid
from unittest.mock import Mock, patch

from forsic_plugin import image_process as process
from forsic_plugin.execution_contract import ExecutionFailure
from forsic_plugin.shell_workspace import _command_outcome


class ContainerStartTests(unittest.TestCase):
    def setUp(self):
        self.name = 'forsic-test-container'
        self.command = ['docker', 'run', '--name', self.name, 'synthetic-image', 'synthetic-command']
        self.child = Mock(returncode=125)
        self.child.poll.return_value = 125
        self.child.communicate.return_value = (b'', b'synthetic daemon failure')
        self.child.stdout = io.BytesIO()
        self.child.stderr = io.BytesIO()

    def invoke(self, startup, *, cleanup=True, bounded=False):
        capture = Mock(exceeded=False)
        capture.step.return_value = True
        capture.output.return_value = (b'raw\xff', b'daemon\x00error')
        with patch.object(process.subprocess, 'Popen', return_value=self.child) as launch, \
             patch.object(process, '_container_start', return_value=startup), \
             patch.object(process, '_cleanup', return_value={'confirmed':cleanup}) as remove, \
             patch.object(process, '_BoundedCapture', return_value=capture):
            try:
                return process.run_container(self.command, self.name, cleanup_on_return=True, retained_container=True,
                    max_output_bytes=4096 if bounded else None)
            finally:
                launch.assert_called_once()
                remove.assert_called_once()

    def test_missing_bind_is_not_an_executed_script_and_keeps_exact_bytes(self):
        with self.assertRaises(ExecutionFailure) as caught:
            self.invoke({'started':False, 'basis':'retained_container_absent'}, bounded=True)
        details = caught.exception.details
        self.assertEqual(details['error_code'], 'container_start_failed')
        self.assertEqual(details['execution']['state'], 'not_started')
        self.assertFalse(details['backend_started'])
        self.assertEqual(base64.b64decode(details['raw_output']['stdout_base64']), b'raw\xff')
        outcome = _command_outcome({'command':'echo x; echo y', **details}, b'')
        self.assertEqual(outcome['status'], 'not_started')
        self.assertNotIn('Bash', outcome.get('hint', ''))

    def test_inspected_absence_confirms_no_container_even_if_remove_failed(self):
        with self.assertRaises(ExecutionFailure) as caught:
            self.invoke({'started':False, 'basis':'retained_container_absent'}, cleanup=False)
        self.assertEqual(caught.exception.details['execution']['state'], 'not_started')
        self.assertTrue(caught.exception.details['execution']['termination_confirmed'])

    def test_created_container_cleanup_failure_preserves_uncertainty(self):
        with self.assertRaises(ExecutionFailure) as caught:
            self.invoke({'started':False, 'basis':'container_never_started'}, cleanup=False)
        self.assertEqual(caught.exception.details['execution']['state'], 'completion_unknown')

    def test_unavailable_inspection_never_claims_not_started(self):
        for cleanup, state in ((True, 'failed'), (False, 'completion_unknown')):
            with self.subTest(cleanup=cleanup), self.assertRaises(ExecutionFailure) as caught:
                self.invoke({'started':None, 'basis':'container_state_unavailable'}, cleanup=cleanup)
            self.assertEqual(caught.exception.details['execution']['state'], state)
            self.assertEqual(caught.exception.details['error_code'], 'container_start_unverified')
            self.assertIsNone(caught.exception.details['backend_started'])

    def test_real_program_exit_125_is_not_startup_failure(self):
        self.child.communicate.return_value = (b'', b'docker: Error response from daemon: invalid mount config')
        with self.assertRaises(ExecutionFailure) as caught:
            self.invoke({'started':True, 'basis':'container_started_at'})
        self.assertEqual(caught.exception.details['error_code'], 'parser_process_failed')
        result = self.invoke({'started':True, 'basis':'container_started_at'}, bounded=True)
        self.assertEqual(result['execution']['state'], 'returned')
        self.assertEqual(result['execution']['exit_code'], 125)

    def test_inspection_uses_trusted_state_and_strict_absence(self):
        zero = '0001-01-01T00:00:00Z'
        cases = [
            (0, json.dumps({'Status':'created','Running':False,'Pid':0,'StartedAt':zero}).encode(), b'', False),
            (0, json.dumps({'Status':'exited','StartedAt':'2026-01-01T00:00:00Z'}).encode(), b'', True),
            (1, b'', b'Error: No such object: '+self.name.encode(), False),
            (1, b'', b'Error response from daemon: No such container: '+self.name.encode(), False),
            (1, b'', b'Cannot connect to the Docker daemon', None),
            (1, b'', b'Error: No such object: different-container', None),
            (0, b'{}', b'', None),
            (0, b'[]', b'', None),
        ]
        for rc, out, err, started in cases:
            with self.subTest(out=out, err=err), patch.object(process.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([],rc,out,err)) as inspect:
                self.assertIs(process._container_start(self.name)['started'], started)
                self.assertEqual(inspect.call_args.args[0][-1], self.name)
        with patch.object(process.subprocess, 'run', side_effect=subprocess.TimeoutExpired('inspect',15)):
            self.assertIsNone(process._container_start(self.name)['started'])

    def test_successful_parser_is_cleaned_and_cleanup_failure_preserves_output(self):
        self.child.returncode = 0
        self.child.poll.return_value = 0
        self.child.communicate.return_value = (b'{"lines":[]}', b'')
        for confirmed in (True, False):
            with self.subTest(confirmed=confirmed), patch.object(process.subprocess, 'Popen', return_value=self.child), \
                 patch.object(process, '_cleanup', return_value={'confirmed':confirmed}) as cleanup, \
                 patch.object(process, '_container_start') as inspect:
                if confirmed:
                    self.assertEqual(process.run_parser(self.command,self.name,cleanup_on_return=True)['lines'], [])
                else:
                    with self.assertRaises(ExecutionFailure) as caught:
                        process.run_parser(self.command,self.name,cleanup_on_return=True)
                    self.assertEqual(caught.exception.details['error_code'], 'parser_cleanup_unknown')
                    self.assertEqual(base64.b64decode(caught.exception.details['raw_output']['base64']), b'{"lines":[]}')
                cleanup.assert_called_once()
                inspect.assert_not_called()

    def test_cleanup_does_not_imply_container_retention(self):
        capture = Mock(exceeded=False)
        capture.step.return_value = True
        capture.output.return_value = (b'', b'program exited 125')
        with patch.object(process.subprocess,'Popen',return_value=self.child), \
             patch.object(process,'_BoundedCapture',return_value=capture), \
             patch.object(process,'_cleanup',return_value={'confirmed':True}), \
             patch.object(process,'_container_start') as inspect:
            result = process.run_container(self.command,self.name,max_output_bytes=4096,cleanup_on_return=True)
        self.assertEqual(result['execution']['state'],'returned')
        inspect.assert_not_called()


@unittest.skipUnless(os.environ.get('FORSIC_TEST_CONTAINER_IMAGE'), 'opt-in immutable Docker image required')
class RealContainerStartTests(unittest.TestCase):
    def test_missing_mount_and_program_125_have_different_boundaries(self):
        image = os.environ['FORSIC_TEST_CONTAINER_IMAGE']
        self.assertTrue(image.startswith('sha256:'))
        name = 'forsic-start-test-'+uuid.uuid4().hex
        command = ['docker','run','--pull=never','--name',name,'--network','none','--read-only',
                   '--cap-drop','ALL','--security-opt','no-new-privileges',
                   '--user',f'{os.getuid()}:{os.getgid()}', '--memory','128m','--pids-limit','16']
        missing = command+['--mount',process.bind_mount('/nonexistent-'+uuid.uuid4().hex,'/probe',True),
                            image,'python','-c','print("should not run")']
        with self.assertRaises(ExecutionFailure) as caught:
            process.run_container(missing,name,max_output_bytes=8192,cleanup_on_return=True,retained_container=True)
        self.assertEqual(caught.exception.details['execution']['state'], 'not_started')
        self.assertEqual(caught.exception.details['error_code'], 'container_start_failed')
        program = 'import sys; print("docker: Error response from daemon: invalid mount config",file=sys.stderr); sys.exit(125)'
        result = process.run_container(command+[image,'python','-c',program],name,
                                       max_output_bytes=8192,cleanup_on_return=True,retained_container=True)
        self.assertEqual(result['execution']['state'], 'returned')
        self.assertEqual(result['execution']['exit_code'], 125)
        self.assertTrue(result['execution']['cleanup']['confirmed'])


if __name__ == '__main__':
    unittest.main()
