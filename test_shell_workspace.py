"""Synthetic shell boundary tests: no evidence, model, network or Docker launch."""
import base64
import csv
import json
import os
from pathlib import Path
import shlex
import subprocess
import socket
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from forsic_plugin import shell_workspace as shell
from forsic_plugin.evidence import Case
from forsic_plugin.execution_contract import ExecutionFailure
from forsic_plugin.image_process import run_container, _cleanup, _raw_output, bind_mount


class ShellWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        evidence=self.root/'evidence'
        evidence.mkdir()
        (evidence/'approved').mkdir()
        (evidence/'approved'/'one.e01').write_bytes(b'synthetic selected')
        (evidence/'excluded.e01').write_bytes(b'synthetic excluded')
        manifest=self.root/'case.json'
        manifest.write_text(json.dumps({'evidence_root':str(evidence),
            'output_root':str(self.root/'output'),'selected_files':['approved/one.e01']}))
        self.case=Case(manifest)
        self.image='sha256:'+'a'*64
        from test_environment_support import offline_environment
        offline_environment(self,self.image)

    def test_workspace_capabilities_match_build_declarations_without_runtime_probe(self):
        dockerfile=(Path(__file__).parent/'Dockerfile.analysis').read_text()
        parserfile=(Path(__file__).parent/'Dockerfile.tools').read_text()
        query_requirements=(Path(__file__).parent/'requirements-analysis.txt').read_text()
        with (patch.object(shell.subprocess,'run',side_effect=AssertionError('runtime probe')),
              patch.object(shell.subprocess,'Popen',side_effect=AssertionError('runtime launch')),
              patch.object(shell,'_image_id',side_effect=AssertionError('image probe')),
              patch.object(shell,'_workspace',side_effect=AssertionError('workspace creation')),
              patch.object(Path,'read_text',side_effect=AssertionError('runtime file read'))):
            info=shell.workspace_info()
        declared=info['declared_capabilities']
        self.assertIn('FROM '+declared['python_base_image']+' AS parser',dockerfile.splitlines())
        self.assertIn('FROM '+declared['base_image'],dockerfile.splitlines())
        packages={item.split('==')[0]:item.split('==')[1]
                  for line in (parserfile+'\n'+dockerfile).splitlines() if line.startswith('RUN pip install ')
                  for item in shlex.split(line) if '==' in item}
        packages.update({line.split('==')[0]:line.split('==')[1].split()[0]
                         for line in query_requirements.splitlines() if '==' in line})
        self.assertEqual(declared['python_packages'],packages)
        self.assertEqual(declared['image'],shell.IMAGE)
        self.assertEqual(declared['source'],'Dockerfile.analysis')
        self.assertFalse(declared['runtime_verified'])
        self.assertTrue(info['evidence_readonly']);self.assertFalse(info['network'])
        self.assertFalse((self.case.output/'shell-workspaces').exists())
        declared['python_packages'].clear()
        self.assertEqual(shell.workspace_info()['declared_capabilities']['python_packages'],packages)

    def test_recovery_is_declared_separately_from_reader_coverage(self):
        info=self.case.info({})
        self.assertTrue(info['capabilities']['carving'])
        self.assertTrue(info['capabilities']['deleted_recovery'])
        declared=info['shell_workspace']['declared_capabilities']
        self.assertFalse(declared['runtime_verified'])
        self.assertIn('XFS is not supported',declared['deleted_recovery']['filesystem'])
        self.assertIn('raw copy',declared['deleted_recovery']['ewf'])
        self.assertEqual(info['shell_workspace']['file_size_limit_bytes'],256*1024*1024)
        dockerfile=(Path(__file__).parent/'Dockerfile.analysis').read_text()
        for pin in ('sleuthkit=4.12.1+dfsg-3build1','testdisk=7.2-0.1build1','ewf-tools=20140816-2build1'):
            self.assertIn(pin,dockerfile)

    def test_capability_discovery_command_uses_installed_metadata_without_importing_package(self):
        command=shell.workspace_info()['declared_capabilities']['discovery']['package_metadata_command']
        argv=shlex.split(command)
        self.assertEqual(argv[:2],['python','-c'])
        fake=SimpleNamespace(metadata={'Name':'synthetic-package'},version='test-version',entry_points=[
            SimpleNamespace(name='synthetic-cli',group='console_scripts'),
            SimpleNamespace(name='not-a-cli',group='other')])
        # Execute only the advertised stdlib discovery snippet against fake metadata.
        # No Dissect import, installed-image inspection or container launch occurs.
        with (patch('importlib.metadata.distribution',return_value=fake) as distribution,
              patch('builtins.print') as output):
            exec(argv[2],{})
        distribution.assert_called_once_with('dissect.target')
        self.assertEqual(output.call_args_list[0].args,('synthetic-package','test-version'))
        self.assertEqual(output.call_args_list[1].args,('synthetic-cli',))

    def test_validation_rejects_authority_arguments_and_byte_or_cwd_overflow(self):
        self.assertEqual(shell.validate({'command':'printf ok'}),
            {'command':'printf ok','cwd':'/work','timeout_seconds':300})
        bad=({'command':''},{'command':' '},{'command':'x\0y'},
             {'command':'é'*(shell.MAX_COMMAND_BYTES//2+1)},
             {'command':'x','timeout_seconds':True},{'command':'x','timeout_seconds':1801},
             {'command':'x','cwd':'/evidence'},{'command':'x','cwd':'/work/../evidence'},
             {'command':'x','cwd':'/work-other'},{'command':'x','image':'host'},
             {'command':'x','docker_args':['--privileged']})
        for args in bad:
            with self.subTest(args=str(args)[:100]),self.assertRaises(ValueError):
                shell.validate(args)

    def test_subset_mounts_only_approved_file_and_never_parent(self):
        mounts=shell._evidence_mounts(self.case)
        self.assertEqual(len(mounts),1)
        self.assertEqual(mounts[0],{'source':str(self.case.root/'approved/one.e01'),
            'destination':'/evidence/approved/one.e01','readonly':True})
        self.case.config['selected_files']=['excluded.e01','../case.json']
        with self.assertRaises(ValueError):shell._evidence_mounts(self.case)

    def test_bind_mount_csv_preserves_commas_quotes_and_option_like_path_text(self):
        for source in ('/tmp/with,comma','/tmp/with"quote',
                       '/tmp/both",dst=/wrong,readonly=false',
                       '/tmp/smb-share:server=192.0.2.7,share=synthetic/archive'):
            with self.subTest(source=source):
                encoded=bind_mount(source,'/evidence',True,recursive_readonly=True)
                self.assertEqual(next(csv.reader([encoded],strict=True)),
                    ['type=bind','src='+source,'dst=/evidence','readonly',
                     'bind-propagation=rprivate','bind-recursive=readonly'])
        self.assertEqual(next(csv.reader([bind_mount('/tmp/with,comma','/work',False)],strict=True)),
                         ['type=bind','src=/tmp/with,comma','dst=/work'])

    def test_shell_command_encodes_actual_quoted_comma_paths_as_one_mount(self):
        evidence=self.root/'smb-share:server=192.0.2.7,share=synthetic'
        evidence.mkdir()
        selected='one",readonly=false.e01'
        (evidence/selected).write_bytes(b'synthetic selected file')
        manifest=self.root/'quoted-case.json'
        manifest.write_text(json.dumps({'evidence_root':str(evidence),
            'output_root':str(self.root/'quoted-output'),'selected_files':[selected]}))
        case=Case(manifest)
        mounts=shell._evidence_mounts(case)
        with patch.object(shell.os,'getuid',return_value=1000):
            command=shell._command(shell.validate({'command':'true'}),self.image,mounts,'synthetic-container')
        encoded=[command[index+1] for index,item in enumerate(command) if item=='--mount']
        self.assertEqual(len(encoded),1)
        self.assertEqual(next(csv.reader(encoded,strict=True)),
            ['type=bind','src='+str(evidence/selected),'dst=/evidence/'+selected,
             'readonly','bind-propagation=rprivate','bind-recursive=readonly'])

    def test_bind_mount_rejects_nul_and_linebreaks_before_building_commands(self):
        for character in ('\0','\n','\r'):
            for source,destination in (('/tmp/a'+character+'b','/evidence'),('/tmp/a','/evidence/a'+character+'b')):
                with self.subTest(character=repr(character),source=source):
                    with self.assertRaises(ValueError):bind_mount(source,destination,True)
                    with self.assertRaises(ValueError):shell._mount(source,destination,True)
                    with patch.object(shell.os,'getuid',return_value=1000),self.assertRaises(ValueError):
                        shell._command(shell.validate({'command':'true'}),self.image,
                            [{'source':source,'destination':destination,'readonly':True}],'synthetic-container')

    def test_whole_case_root_only_without_selection(self):
        self.case.config['selected_files']=[]
        self.assertEqual(shell._evidence_mounts(self.case),
            [{'source':str(self.case.root),'destination':'/evidence','readonly':True}])

    def test_full_root_refuses_live_socket_and_fifo_before_any_backend(self):
        self.case.config['selected_files']=[]
        channel=socket.socket(socket.AF_UNIX)
        self.addCleanup(channel.close)
        special=self.case.root/'live.sock'
        channel.bind(str(special))
        with patch.object(shell,'_image_id') as image,patch.object(shell,'run_container') as backend:
            with self.assertRaises(ValueError):shell.invoke(self.case,{'command':'anything'},'s')
        image.assert_not_called();backend.assert_not_called()
        special.unlink()
        os.mkfifo(special)
        with self.assertRaises(ValueError):shell._evidence_mounts(self.case)

    def test_selected_socket_fifo_and_simulated_device_are_not_regular_files(self):
        special=self.case.root/'special'
        os.mkfifo(special)
        self.case.config['selected_files']=['special']
        with self.assertRaises(ValueError):shell._evidence_mounts(self.case)
        with patch.object(Path,'lstat',return_value=SimpleNamespace(st_mode=stat.S_IFCHR)):
            with self.assertRaises(ValueError):shell._check_evidence_tree(self.case.root)

    def test_directory_scan_never_follows_symlinks_or_reads_file_contents(self):
        self.case.config['selected_files']=[]
        outside=self.root/'outside'
        outside.mkdir()
        os.mkfifo(outside/'special')
        (self.case.root/'external-link').symlink_to(outside,target_is_directory=True)
        with (patch.object(Path,'read_bytes',side_effect=AssertionError('content read')),
              patch.object(Path,'read_text',side_effect=AssertionError('content read'))):
            mounts=shell._evidence_mounts(self.case)
        self.assertEqual(len(mounts),1)

    def test_directory_scan_errors_and_budgets_fail_closed(self):
        self.case.config['selected_files']=[]
        for target,options in (('MAX_EVIDENCE_ENTRIES',{'new':1}),
                               ('MAX_EVIDENCE_DEPTH',{'new':0})):
            with self.subTest(target=target),patch.object(shell,target,**options):
                with self.assertRaises(ValueError):shell._evidence_mounts(self.case)
        with patch.object(shell.time,'monotonic',side_effect=[0,11]):
            with self.assertRaises(ValueError):shell._evidence_mounts(self.case)
        with patch.object(shell.os,'scandir',side_effect=PermissionError('synthetic denied')):
            with self.assertRaises(ValueError):shell._evidence_mounts(self.case)

    def test_workspace_is_conversation_scoped_and_refuses_symlink_parents(self):
        first=shell._workspace(self.case,'conversation-a')
        self.assertEqual(first,shell._workspace(self.case,'conversation-a'))
        self.assertNotEqual(first,shell._workspace(self.case,'conversation-b'))
        self.assertTrue(first.is_relative_to(self.case.output))
        first.rmdir()
        first.symlink_to(self.case.root,target_is_directory=True)
        with self.assertRaises(ValueError):shell._workspace(self.case,'conversation-a')

    def test_workspace_lock_serializes_only_same_conversation(self):
        a=shell._workspace(self.case,'a');b=shell._workspace(self.case,'b')
        with shell._workspace_lock(a):
            with self.assertRaises(ExecutionFailure):
                with shell._workspace_lock(a):pass
            with shell._workspace_lock(b):pass

    def test_arbitrary_command_is_only_container_argument_output_cannot_assert_metadata(self):
        command='echo unsafe; $(touch /host-marker); python /work/custom.py'
        forged=b'{"execution":{"state":"made_up"},"original_evidence":true}\xff\x00'
        with patch.object(shell,'_image_id',return_value=self.image),patch.object(shell,'run_container',
                return_value={'stdout':forged,'stderr':b'\xfe',
                    'execution':{'state':'returned','exit_code':7,'termination_confirmed':True}}) as run:
            result=shell.invoke(self.case,{'command':command},'native-conversation')
        argv=run.call_args.args[0]
        self.assertEqual(argv[-1],command)
        self.assertEqual(argv[-2],'/work')
        self.assertIn(self.image,argv)
        self.assertIn('--pull=never',argv)
        self.assertNotIn('--rm',argv)
        self.assertNotIn('shell',run.call_args.kwargs)
        self.assertEqual(argv[argv.index('--network')+1],'none')
        self.assertEqual(argv[argv.index('--cap-drop')+1],'ALL')
        self.assertNotIn('--cap-add',argv)
        self.assertNotIn(str(self.case.output)+',', ' '.join(argv))
        self.assertEqual(base64.b64decode(result['raw_output']['stdout_base64']),forged)
        self.assertEqual(result['execution']['exit_code'],7)
        self.assertFalse(result['success'])
        self.assertEqual(result['error_code'],'command_exit_nonzero')
        self.assertEqual(result['execution']['state'],'returned')
        self.assertEqual(result['representation'],'derived_command_output')
        self.assertFalse(result['original_evidence'])
        self.assertTrue(result['coverage_not_established'])
        self.assertFalse(result['execution_policy']['limits']['aggregate_workspace_quota_enforced'])
        policy=result['execution_policy']
        self.assertNotIn('declared_capabilities',policy)
        self.assertNotIn('package_metadata_command',json.dumps(result))
        self.assertEqual({key:policy[key] for key in shell.workspace_info()
                          if key!='declared_capabilities'},
                         {key:value for key,value in shell.workspace_info().items()
                          if key!='declared_capabilities'})
        self.assertIn('package_metadata_command',
                      shell.workspace_info()['declared_capabilities']['discovery'])
        trusted=[m for m in policy['mounts'] if m['destination'].startswith('/opt/forsic/')]
        self.assertEqual({Path(m['destination']).name for m in trusted},
                         {'image_reader.py','literal_search.py','image_access.py','structured_query.py','login_records.py'})
        self.assertTrue(all(m['readonly'] and Path(m['source']).parent==Path(shell.__file__).parent for m in trusted))
        for m in trusted:
            self.assertIn('src='+m['source']+',dst='+m['destination']+',readonly', ' '.join(argv))

    def test_cleanup_uncertainty_takes_priority_over_nonzero_exit(self):
        with patch.object(shell,'_image_id',return_value=self.image),patch.object(shell,'run_container',
                return_value={'stdout':b'kept','stderr':b'failure',
                    'execution':{'state':'completion_unknown','exit_code':7,'termination_confirmed':False}}):
            result=shell.invoke(self.case,{'command':'synthetic'},'s')
        self.assertEqual(result['error_code'],'command_cleanup_unknown')
        self.assertEqual(result['execution']['exit_code'],7)
        self.assertEqual(result['stdout'],'kept')

    def test_zero_exit_does_not_infer_failure_from_output_text(self):
        with patch.object(shell,'_image_id',return_value=self.image),patch.object(shell,'run_container',
                return_value={'stdout':b'error or failed are ordinary output','stderr':b'diagnostic',
                    'execution':{'state':'returned','exit_code':0,'termination_confirmed':True}}):
            result=shell.invoke(self.case,{'command':'synthetic'},'s')
        self.assertNotIn('error',result)
        self.assertNotIn('success',result)

    def test_cancel_before_admission_probes_nothing(self):
        class Cancel:
            def interrupted(self):return True
        with patch.object(shell,'_image_id') as probe,patch.object(shell,'run_container') as run:
            result=shell.invoke(self.case,{'command':'anything'},'s',Cancel())
        probe.assert_not_called();run.assert_not_called()
        self.assertEqual(result['error_code'],'cancelled_before_start')
        self.assertFalse((self.case.output/'shell-workspaces').exists())

    def test_missing_image_fails_closed_without_pull_or_runtime(self):
        from forsic_plugin.environments import EnvironmentManager
        with (patch.object(EnvironmentManager,'execution_snapshot',side_effect=ValueError('Pinned image missing')),
              patch.object(shell,'run_container') as run):
            result=shell.invoke(self.case,{'command':'anything'},'s')
        run.assert_not_called()
        self.assertEqual(result['error_code'],'environment_unavailable')

    def test_preview_is_separate_from_raw_bytes_and_bounded_even_invalid_utf8(self):
        raw=b'\xff'*12000
        self.assertLessEqual(len(shell._preview(raw).encode('utf-8')),shell.PREVIEW_BYTES)

    def test_keyboard_interrupt_keeps_derived_bytes_and_propagates(self):
        interrupted=KeyboardInterrupt()
        interrupted.forsic_execution={'state':'cancelled','termination_confirmed':True}
        interrupted.forsic_raw_output=_raw_output(b'kept\xff',b'err',complete=False,limit=shell.MAX_OUTPUT_BYTES)
        with (patch.object(shell,'_image_id',return_value=self.image),
              patch.object(shell,'run_container',side_effect=interrupted),self.assertRaises(KeyboardInterrupt) as caught):
            shell.invoke(self.case,{'command':'synthetic'},'s')
        value=caught.exception.forsic_shell_result
        self.assertEqual(base64.b64decode(value['raw_output']['stdout_base64']),b'kept\xff')
        self.assertEqual(value['execution']['state'],'cancelled')
        self.assertFalse(value['original_evidence'])


class BoundedForegroundTests(unittest.TestCase):
    @staticmethod
    def cleanup(container,process):
        if process.poll() is None:process.kill()
        process.communicate(timeout=3)
        process.stdout.close();process.stderr.close()
        return {'requested':True,'confirmed':True,'client_reaped':True}

    def run_synthetic(self,program,**kwargs):
        return run_container([sys.executable,'-c',program],'synthetic-no-docker',
            max_output_bytes=4096,cleanup_on_return=True,**kwargs)

    def test_real_binary_subprocess_preserves_streams_and_nonzero_exit_once(self):
        with patch('forsic_plugin.image_process._cleanup',side_effect=self.cleanup) as cleanup:
            result=self.run_synthetic("import os,sys;os.write(1,b'\\xff\\x00');os.write(2,b'\\xfe');sys.exit(7)")
        cleanup.assert_called_once()
        self.assertEqual(result['stdout'],b'\xff\x00');self.assertEqual(result['stderr'],b'\xfe')
        self.assertEqual(result['execution']['state'],'returned')
        self.assertEqual(result['execution']['exit_code'],7)

    def test_real_subprocess_output_ceiling_stops_and_retains_prefix(self):
        with patch('forsic_plugin.image_process._cleanup',side_effect=self.cleanup),self.assertRaises(ExecutionFailure) as caught:
            self.run_synthetic("import os;os.write(1,b'x'*100000)")
        data=caught.exception.details
        self.assertEqual(data['error_code'],'command_output_limit')
        self.assertEqual(base64.b64decode(data['raw_output']['stdout_base64']),b'x'*4096)
        self.assertTrue(data['raw_output']['truncated'])
        self.assertTrue(data['execution']['termination_confirmed'])

    def test_timeout_and_native_cancel_do_not_retry_or_clear_interrupt(self):
        class Interrupt:
            calls=0
            def interrupted(self):
                self.calls+=1
                return self.calls>1
        for kwargs,code in (({'timeout_seconds':.02},'command_timeout'),
                            ({'native_execution':Interrupt()},'cancel_requested')):
            with (self.subTest(code=code),patch('forsic_plugin.image_process._cleanup',side_effect=self.cleanup) as cleanup,
                  self.assertRaises(ExecutionFailure) as caught):
                self.run_synthetic('import time;time.sleep(10)',**kwargs)
            cleanup.assert_called_once()
            self.assertEqual(caught.exception.details['error_code'],code)

    def test_cleanup_uncertainty_preserves_returned_output(self):
        with patch('forsic_plugin.image_process._cleanup',return_value={'confirmed':False}):
            result=self.run_synthetic("print('kept')")
        self.assertEqual(result['stdout'],b'kept\n')
        self.assertEqual(result['execution']['state'],'completion_unknown')
        self.assertFalse(result['execution']['termination_confirmed'])

    def test_owned_attach_client_is_reaped_before_container_removal(self):
        calls=[]
        class Process:
            stdout=None;stderr=None
            def poll(self):return None
            def kill(self):calls.append('kill-client')
            def communicate(self,timeout):calls.append('reap-client')
        def remove(argv,**kwargs):
            calls.append('remove-container')
            self.assertEqual(argv,['docker','rm','-f','exact-owned-container'])
            return subprocess.CompletedProcess(argv,0,b'',b'')
        with patch('forsic_plugin.image_process.subprocess.run',side_effect=remove):
            result=_cleanup('exact-owned-container',Process())
        self.assertEqual(calls,['kill-client','reap-client','remove-container'])
        self.assertTrue(result['confirmed'])


if __name__=='__main__':unittest.main()
