"""Environment contracts; optional real Docker checks use only synthetic files."""
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import patch

from forsic_plugin import environments as env


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='forsic-environment-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manager = env.EnvironmentManager(self.root/'management')
        self.case = SimpleNamespace(output=self.root/'case')
        self.image = {'image_id':'sha256:'+'1'*64,'repo_digests':[],
                      'os':'linux','architecture':'amd64','platform':'linux/amd64'}
        self.inspect = patch.object(self.manager,'_inspect',return_value=self.image).start()
        self.smoke = patch.object(self.manager,'_smoke',return_value={'analysis':{'ok':True},'parser':{'ok':True}}).start()
        self.mount_smoke = patch.object(self.manager,'_mount_smoke',return_value={'analysis':{'ok':True},'parser':{'ok':True}}).start()
        self.runner=patch.object(self.manager,'_run',return_value=b'{"apt":{},"python":{},"rules":{}}')
        self.runner.start();self.addCleanup(self.runner.stop)
        self.host=patch.object(env,'_host_utilities',return_value={});self.host.start();self.addCleanup(self.host.stop)
        self.addCleanup(patch.stopall)

    def register(self, name='register'):
        operation = self.manager.register(operation_id=name,background=False)
        self.assertEqual(operation['state'],'succeeded',operation)
        return operation['result']['environment_id']

    def default(self, identity, revision=0):
        return self.manager.set_default(identity,expected_revision=revision,operation_id='default-'+str(revision))

    def test_default_changes_only_new_cases_and_snapshot_helpers_are_immutable(self):
        first = self.register()
        self.default(first)
        snapshot = self.manager.execution_snapshot(self.case)
        second = self.register('register-two')
        self.default(second,1)
        self.assertEqual(self.manager.execution_snapshot(self.case)['environment_id'],first)
        other = SimpleNamespace(output=self.root/'other-case')
        self.assertEqual(self.manager.execution_snapshot(other)['environment_id'],second)
        self.assertTrue(all(Path(v['path']).parent.parent.name==first for v in snapshot['helpers'].values()))
        self.assertEqual(snapshot['legacy_history'],'not retroactively pinned')
        self.assertEqual(self.manager.case_state(self.case)['revision'],1)

    def test_new_helper_revision_preserves_prior_case_bytes(self):
        import shutil
        first=self.register();self.default(first)
        old=self.manager.execution_snapshot(self.case)
        helpers=self.root/'candidate-helpers';helpers.mkdir()
        sources={}
        for name in env.HELPERS:
            target=helpers/name;shutil.copyfile(env.SOURCE/'forsic_plugin'/name,target);sources[name]=target
        changed=helpers/'image_access.py'
        changed.write_bytes(changed.read_bytes()+b'\n# synthetic next helper revision\n')
        operation=self.manager._start('helper-revision',{'sha256':env._file_sha(changed)},'helpers',
            lambda op:self.manager._register({'analysis':self.image,'parser':self.image},op,source_helpers=sources),background=False)
        self.assertEqual(operation['state'],'succeeded',operation)
        second=operation['result']['environment_id'];self.default(second,1)
        new=self.manager.execution_snapshot(SimpleNamespace(output=self.root/'new-case'))
        self.assertNotEqual(old['helpers']['image_access.py']['sha256'],new['helpers']['image_access.py']['sha256'])
        self.assertEqual(env._file_sha(old['helpers']['image_access.py']['path']),old['helpers']['image_access.py']['sha256'])
        self.assertEqual(self.manager.execution_snapshot(self.case)['environment_id'],first)

    def test_pending_requires_safety_callable_and_execution_lease_defers_apply(self):
        first, second = self.register(),self.register('second')
        self.default(first)
        self.manager.execution_snapshot(self.case)
        pending=self.manager.select_case(self.case,second,expected_revision=1,
            operation_id='select',reason='synthetic test',sessions_safe=True)
        self.assertEqual(pending['current']['environment_id'],first)
        self.assertIsNotNone(pending['pending'])
        self.assertEqual(self.manager.apply_pending(self.case,sessions_safe=lambda:False)['revision'],1)
        with self.manager.execution(self.case):
            self.assertEqual(self.manager.apply_pending(self.case,sessions_safe=lambda:True)['revision'],1)
        applied=self.manager.apply_pending(self.case,sessions_safe=lambda:True)
        self.assertEqual(applied['revision'],2)
        self.assertEqual([x['environment_id'] for x in applied['history']],[first,second])
        self.assertEqual(self.manager.select_case(self.case,second,expected_revision=1,
            operation_id='select',reason='synthetic test',sessions_safe=lambda:True)['revision'],2)
        with self.assertRaisesRegex(ValueError,'conflict'):
            self.manager.select_case(self.case,first,expected_revision=1,operation_id='stale')
        rollback=self.manager.select_case(self.case,first,expected_revision=2,operation_id='rollback',sessions_safe=lambda:True)
        self.assertEqual(rollback['revision'],3)
        self.assertEqual(rollback['current']['environment_id'],first)

    def test_selection_and_turn_bind_keep_pack_version_and_schema_identity(self):
        from forsic_plugin import extension_hub as hub
        archive=self.root/'pack.zip'
        directory=env.SOURCE/'examples/extensions/tool'
        with zipfile.ZipFile(archive,'w') as bundle:
            for path in directory.rglob('*'):
                if path.is_file():bundle.write(path,str(path.relative_to(directory)))
        pack=hub.import_pack(archive,home=self.root)
        selection={'packs':{'local.tool-example':{'version':'1.0.0','sha256':pack['sha256'],
            'enabled':True,'tools':{'count-lines':True},'skills':{}}}}
        defaults=hub.selection_snapshot(home=self.root)
        hub.save_selection(selection,defaults['revision'],'defaults',home=self.root)
        identity=self.register();self.default(identity)
        old=self.manager.execution_snapshot(self.case)
        self.assertEqual(old['extensions'],selection)
        # Profile defaults use the manager root's owning home and do not mutate
        # a prior case. An explicit case change has a separate revision.
        hub.save_selection({'packs':{}},hub.selection_snapshot(home=self.root)['revision'],'defaults-two',home=self.root)
        self.assertEqual(self.manager.execution_snapshot(self.case)['extensions'],selection)
        with env.bind_snapshot(self.case,old):
            self.manager.select_case(self.case,extensions={'packs':{}},expected_revision=1,
                operation_id='pack-two',sessions_safe=lambda:True)
            self.assertEqual(self.manager.execution_snapshot(self.case)['extensions'],selection)
        self.assertEqual(self.manager.execution_snapshot(self.case)['extensions'],{'packs':{}})

    def test_missing_image_and_modified_helper_do_not_fallback_to_tags(self):
        identity=self.register();self.default(identity)
        snapshot=self.manager.execution_snapshot(self.case)
        self.inspect.side_effect=ValueError('missing pinned image')
        with self.assertRaisesRegex(ValueError,'missing pinned'):
            self.manager.execution_snapshot(self.case)
        self.inspect.side_effect=None
        helper=Path(snapshot['helpers']['image_access.py']['path']);helper.chmod(0o600);helper.write_text('changed')
        with self.assertRaisesRegex(ValueError,'integrity'):
            self.manager.execution_snapshot(self.case)

    def test_failed_verification_does_not_register_or_change_default(self):
        identity=self.register();self.default(identity)
        self.smoke.side_effect=ValueError('feature failure')
        operation=self.manager.register(operation_id='failure',background=False)
        self.assertEqual(operation['state'],'failed')
        self.assertEqual(operation['retry_stage'],'verification')
        self.assertEqual(self.manager.catalog()['default_environment_id'],identity)
        self.assertEqual(len(self.manager.catalog()['environments']),1)

    def test_failed_real_bind_check_does_not_publish_or_change_default(self):
        identity=self.register();self.default(identity)
        for failure in ('helper bind source unavailable', 'workspace write denied'):
            self.mount_smoke.side_effect=ValueError(failure)
            operation=self.manager.register(operation_id='bind-failure-'+str(len(failure)),background=False)
            self.assertEqual(operation['state'],'failed',operation)
            self.assertEqual(operation['retry_stage'],'mount_verification')
            self.assertEqual(self.manager.catalog()['default_environment_id'],identity)
            self.assertEqual([p.name for p in (self.manager.root/'environments').iterdir()],[identity])

    def test_first_bind_failure_cannot_bootstrap_default_or_case_snapshot(self):
        self.mount_smoke.side_effect=ValueError('helper bind source unavailable')
        with self.assertRaisesRegex(ValueError,'Cannot preserve the installed runtime'):
            self.manager.execution_snapshot(self.case)
        self.assertIsNone(self.manager.catalog()['default_environment_id'])
        self.assertEqual(self.manager.catalog()['environments'],[])
        self.assertIsNone(self.manager.case_state(self.case)['current'])

    def test_registration_checks_final_helper_paths_before_publication(self):
        def check(images,operation,directory):
            self.assertEqual(directory.parent.parent,self.manager.root/'environments')
            self.assertFalse((directory.parent/'manifest.json').exists())
            self.assertEqual(set(p.name for p in directory.iterdir()),set(env.HELPERS))
            self.assertTrue(all(env._file_sha(directory/name)==env._file_sha(env.SOURCE/'forsic_plugin'/name)
                                for name in env.HELPERS))
            return {'analysis':{'synthetic':True},'parser':{'synthetic':True}}
        self.mount_smoke.side_effect=check
        identity=self.register()
        operation=self.manager.operation('register')
        self.assertEqual(operation['mount_checks']['analysis'],{'synthetic':True})
        self.assertIn('future case workspace paths are not certified',operation['checks_scope'])
        # Existing manifests have no new required field and keep their identity.
        self.assertNotIn('mount_checks',self.manager.manifest(identity))

    def test_cancel_after_bind_check_does_not_publish(self):
        def cancel(images,operation,directory):
            self.manager._change_operation(operation,cancel_requested=True)
            return {}
        self.mount_smoke.side_effect=cancel
        operation=self.manager.register(operation_id='cancel-mounts',background=False)
        self.assertEqual(operation['state'],'cancelled')
        self.assertEqual(self.manager.catalog()['environments'],[])

    def test_mount_check_uses_only_exact_helpers_and_disposable_owned_workspace(self):
        patch.stopall()
        helper_directory=self.root/'helpers';helper_directory.mkdir()
        for name in env.HELPERS:(helper_directory/name).write_text('synthetic helper '+name)
        self.manager.root.mkdir()
        seen=[]
        def run(operation,argv,**limits):
            import csv
            mounts=[next(csv.reader([argv[i+1]])) for i,value in enumerate(argv) if value=='--mount']
            self.assertEqual(len(mounts),len(env.HELPERS)+1)
            for name,fields in zip(env.HELPERS,mounts[:-1]):
                self.assertEqual(fields,['type=bind','src='+str(helper_directory/name),
                    'dst=/opt/forsic/'+name,'readonly'])
            work=Path(mounts[-1][1].removeprefix('src='))
            self.assertEqual(work.parent,self.manager.root)
            self.assertEqual(mounts[-1][2],'dst=/work')
            self.assertNotIn('readonly',mounts[-1])
            self.assertTrue(work.is_dir())
            self.assertEqual(argv[argv.index('--user')+1],f'{os.getuid()}:{os.getgid()}')
            for flag in ('--pull=never','--network=none','--read-only','--cap-drop=ALL'):
                self.assertIn(flag,argv)
            self.assertEqual(limits,{'timeout':120,'output_limit':16384})
            seen.append(work)
            return json.dumps({'helper_sha256':json.loads(argv[-2]),
                'helpers_readonly':True,'workspace_write_read':True}).encode()
        with patch.object(self.manager,'_run',side_effect=run):
            receipt=self.manager._mount_smoke({'analysis':self.image,'parser':self.image},'mounts',helper_directory)
        self.assertEqual(set(receipt),{'analysis','parser'})
        self.assertEqual(len(seen),2)
        self.assertTrue(all(not path.exists() for path in seen))
        with patch.object(self.manager,'_run',return_value=b'{"helpers_readonly":true}'):
            with self.assertRaisesRegex(ValueError,'bind verification failed'):
                self.manager._mount_smoke({'analysis':self.image},'bad-mounts',helper_directory)
        self.assertEqual(list(self.manager.root.iterdir()),[])

    def test_registration_retry_preserves_resolved_ids_after_tag_changes(self):
        self.smoke.side_effect=ValueError('synthetic validation failure')
        failed=self.manager.register(operation_id='failed-register',background=False)
        self.assertEqual(failed['state'],'failed')
        self.smoke.side_effect=None
        retried=self.manager.retry('failed-register',new_operation_id='retry-register',background=False)
        self.assertEqual(retried['state'],'succeeded',retried)
        self.assertEqual(retried['inputs'],{'analysis':self.image['image_id'],'parser':self.image['image_id']})

    def test_idempotence_conflicts_and_no_unverified_plan(self):
        first=self.manager.register(operation_id='idem',background=False)
        self.assertEqual(self.manager.register(operation_id='idem',background=False),first)
        with self.assertRaisesRegex(ValueError,'different inputs'):
            self.manager.register({'analysis':'other','parser':'other'},operation_id='idem')
        identity=first['result']['environment_id']
        for plan in ({'base_environment_id':identity,'apt':{'testdisk':'7.3'}},
                     {'base_environment_id':identity,'python':[{'name':'thing','version':'latest','sha256':[]}]},
                     {'base_environment_id':identity,'apt':{'testdisk':'x;touch /host'}}):
            with self.assertRaises(ValueError):self.manager.prepare(plan,operation_id='bad',background=False)

    def test_cancel_bounded_process_and_recovery_do_not_trust_pid_reuse(self):
        self.runner.stop()
        def worker(op):
            return self.manager._run(op,['/usr/bin/python3','-c','import time;print("started",flush=True);time.sleep(60)'])
        operation=self.manager._start('cancel-test',{},'cancel',worker)
        for _ in range(100):
            record=self.manager.operation('cancel')
            if record.get('process'):break
            time.sleep(.01)
        identity=record['process']
        self.assertIn('start_ticks',identity)
        self.assertIn('boot_id',identity)
        self.manager.cancel('cancel')
        env._threads[(str(self.manager.root),'cancel')].join(5)
        self.assertEqual(self.manager.operation('cancel')['state'],'cancelled')
        self.assertIsNone(env._process_identity(identity['pid']))
        env._write(self.manager.root/'operations/stale.json',{'state':'running','stage':'build',
            'operation_id':'stale','created_at':time.time()-10,'process':identity})
        self.assertEqual(self.manager.operation('stale')['state'],'interrupted')

    def test_orphan_cancel_waits_for_actual_process_and_container_termination(self):
        import subprocess
        identity={'pid':987654,'start_ticks':'123','boot_id':'synthetic'}
        env._write(self.manager.root/'operations/orphan.json',{'operation_id':'orphan',
            'state':'completion_unknown','created_at':time.time()-10,'stage':'functional_fixture',
            'process':identity,'container':'forsic-fixture-orphan'})
        with patch.object(env,'_process_identity',return_value=identity),patch.object(env.os,'killpg') as signal_process, \
                patch.object(env.subprocess,'run',return_value=subprocess.CompletedProcess([],1,b'',b'daemon unavailable')):
            first=self.manager.cancel('orphan')
        signal_process.assert_called_once()
        self.assertEqual(first['state'],'completion_unknown')
        self.assertFalse(first['termination_confirmed'])
        self.assertEqual(first['process'],identity)
        with patch.object(env,'_process_identity',return_value=None), \
                patch.object(env.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')):
            second=self.manager.cancel('orphan')
        self.assertEqual(second['state'],'cancelled')
        self.assertTrue(second['termination_confirmed'])
        self.assertIsNone(second['process'])
        env._write(self.manager.root/'operations/reused.json',{'operation_id':'reused',
            'state':'completion_unknown','created_at':time.time()-10,'stage':'build','process':identity})
        with patch.object(env,'_process_identity',return_value={**identity,'start_ticks':'456'}), \
                patch.object(env.os,'killpg') as reused_signal:
            reused=self.manager.cancel('reused')
        reused_signal.assert_not_called()
        self.assertEqual(reused['state'],'cancelled')
        self.assertTrue(reused['termination_confirmed'])

    def test_offline_archive_rejects_traversal_duplicates_links_and_oversize(self):
        for mode in ('traversal','duplicate','link','oversize'):
            archive=self.root/(mode+'.tar')
            with tarfile.open(archive,'w') as bundle:
                entry=tarfile.TarInfo('../escape' if mode=='traversal' else 'entry')
                entry.size=1
                if mode=='link':entry.type=tarfile.SYMTYPE;entry.linkname='/etc/passwd';entry.size=0
                bundle.addfile(entry,io.BytesIO(b'x'))
                if mode=='duplicate':bundle.addfile(entry,io.BytesIO(b'x'))
            destination=self.root/(mode+'-out');destination.mkdir()
            with patch.object(env,'MAX_BUNDLE_BYTES',0 if mode=='oversize' else env.MAX_BUNDLE_BYTES):
                with self.assertRaises(ValueError):env._extract_bundle(archive,destination)
        self.assertFalse((self.root/'escape').exists())

    def test_pack_dependency_plan_is_shared_hash_locked_and_requires_functional_checks(self):
        identity=self.register()
        deps=[{'method':'apt','name':'testdisk','version':'7.2'},
            {'method':'python','name':'duckdb','version':'1.5.6','sha256':['a'*64]}]
        plan=self.manager.plan_dependencies(identity,deps)
        self.assertEqual(plan['apt'],{'testdisk':'7.2'})
        self.assertEqual(plan['python'][0]['sha256'],['a'*64])
        with self.assertRaisesRegex(ValueError,'functional check'):
            self.manager.prepare(plan,operation_id='dependencies',background=False)
        for bad in ([{'method':'host','name':'x','version':'1'}],[{'method':'apt','name':['x'],'version':'1'}],
                    [{'method':'python','name':'x','version':'1','sha256':[]}],deps+deps):
            with self.assertRaises(ValueError):self.manager.plan_dependencies(identity,bad)

    def test_revision_change_before_container_admission_rejects_old_command_snapshot(self):
        from forsic_plugin.execution_contract import ExecutionFailure
        first=self.register();second=self.register('second');self.default(first)
        snapshot=self.manager.execution_snapshot(self.case)
        self.manager.select_case(self.case,second,expected_revision=1,operation_id='switch',sessions_safe=lambda:True)
        with self.assertRaises(ExecutionFailure) as rejected:
            with self.manager.execution(self.case,expected_snapshot=snapshot):pass
        self.assertEqual(rejected.exception.details['error_code'],'environment_revision_changed')
        self.assertEqual(rejected.exception.details['execution']['state'],'not_started')

    def test_unqualified_architecture_is_not_registered(self):
        self.inspect.return_value={**self.image,'architecture':'arm64','platform':'linux/arm64'}
        result=self.manager.register(operation_id='arm',background=False)
        self.assertEqual(result['state'],'failed')
        self.assertIn('Platform not functionally qualified',result['error'])
        self.assertIsNone(self.manager.catalog()['default_environment_id'])
        self.assertEqual(self.manager.catalog()['environments'],[])

    def test_empty_expected_results_are_rejected_before_container_start(self):
        identity=self.register()
        pack=self.root/'pack';pack.mkdir()
        fixture=self.root/'fixture';fixture.mkdir()
        for expected in ({'contains':''},{'json_fields':{}},{}):
            with self.assertRaises(ValueError):
                env.run_fixture(identity,pack,['python','-c','print(1)'],fixture,expected,
                    operation_id='bad-fixture',manager=self.manager,background=False)

    def test_deleted_helper_and_reused_mutation_ids_fail_closed(self):
        identity=self.register();self.default(identity)
        other=self.register('other')
        with self.assertRaisesRegex(ValueError,'different inputs'):
            self.manager.set_default(other,expected_revision=1,operation_id='default-0')
        snapshot=self.manager.execution_snapshot(self.case)
        with self.assertRaisesRegex(ValueError,'different inputs'):
            self.manager.select_case(self.case,other,expected_revision=1,operation_id='default-0')
        helper=Path(snapshot['helpers']['image_access.py']['path'])
        helper.unlink()
        with self.assertRaises(FileNotFoundError):self.manager.execution_snapshot(self.case)

    def test_management_commands_never_mount_evidence_or_enable_host_privileges(self):
        argv=env._management_run(self.image['image_id'],['python','-c','print(1)'])
        self.assertIn('--rm',argv)
        self.assertIn('--network=none',argv)
        self.assertIn('--read-only',argv)
        self.assertNotIn('--mount',argv)
        self.assertNotIn('--privileged',argv)
        self.assertNotIn('/var/run/docker.sock',argv)

    def test_fixture_retains_container_until_explicit_cleanup(self):
        identity=self.register()
        pack=self.root/'pack';pack.mkdir()
        fixture=self.root/'fixture';fixture.mkdir()
        returned={'stdout':b'passed\n','stderr':b'',
            'execution':{'state':'returned','exit_code':0,'termination_confirmed':True}}
        with patch('forsic_plugin.image_process.run_container',return_value=returned) as execute:
            operation=env.run_fixture(identity,pack,['python','-c','print("passed")'],fixture,
                {'contains':'passed'},operation_id='retained-fixture',manager=self.manager,background=False)
        self.assertEqual(operation['state'],'succeeded',operation)
        command=execute.call_args.args[0]
        self.assertNotIn('--rm',command[:command.index(self.image['image_id'])])
        self.assertTrue(execute.call_args.kwargs['cleanup_on_return'])
        self.assertTrue(execute.call_args.kwargs['retained_container'])
        self.assertEqual(command[command.index('--name')+1],execute.call_args.args[1])


@unittest.skipUnless(os.environ.get('FORSIC_TEST_DOCKER_ENVIRONMENTS')=='1','Opt-in existing local image Docker integration')
class DockerEnvironmentTests(unittest.TestCase):
    def test_real_preserved_helper_and_workspace_binds_before_registration(self):
        (env.SOURCE/'artifacts').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='forsic-bind-docker-',dir=env.SOURCE/'artifacts') as temporary:
            manager=env.EnvironmentManager(Path(temporary)/'management')
            images={'analysis':os.environ.get('FORSIC_TEST_ANALYSIS_IMAGE','forsic-analysis:1'),
                    'parser':os.environ.get('FORSIC_TEST_PARSER_IMAGE','forsic-dissect:3.25.1')}
            operation=manager.register(images,operation_id='register-binds',background=False)
            self.assertEqual(operation['state'],'succeeded',operation)
            self.assertIsNone(manager.catalog()['default_environment_id'])
            for role,receipt in operation['mount_checks'].items():
                self.assertTrue(receipt['helpers_readonly'],role)
                self.assertTrue(receipt['workspace_write_read'],role)
                self.assertEqual(receipt['helper_sha256'],{name:entry['sha256']
                    for name,entry in operation['result']['helpers'].items()})
            self.assertFalse(list(manager.root.glob('.mount-check-*')))
            from forsic_plugin.image_process import bind_mount
            def unavailable(source,destination,readonly,**kwargs):
                if Path(source).name=='image_reader.py':
                    source=str(source)+'.synthetic-missing'
                return bind_mount(source,destination,readonly,**kwargs)
            with patch('forsic_plugin.image_process.bind_mount',side_effect=unavailable):
                failed=manager.register(images,operation_id='unavailable-bind',background=False)
            self.assertEqual(failed['state'],'failed',failed)
            self.assertEqual(failed['retry_stage'],'mount_verification')
            self.assertIsNone(manager.catalog()['default_environment_id'])
            self.assertEqual([p.name for p in (manager.root/'environments').iterdir()],
                [operation['result']['environment_id']])
            self.assertFalse(list(manager.root.glob('.mount-check-*')))

    def test_real_prepare_pin_helper_rollback_fixture_and_offline_roundtrip(self):
        (env.SOURCE/'artifacts').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='forsic-env-docker-',dir=env.SOURCE/'artifacts') as temporary:
            root=Path(temporary)
            manager=env.EnvironmentManager(root/'manager')
            op=manager.register({'analysis':'forsic-dissect:3.25.1','parser':'forsic-dissect:3.25.1'},operation_id='register',background=False)
            self.assertEqual(op['state'],'succeeded',op)
            original=op['result']['environment_id']
            manager.set_default(original,expected_revision=0,operation_id='default')
            from forsic_plugin.evidence import Case
            from forsic_plugin.shell_workspace import invoke
            evidence=root/'evidence';evidence.mkdir();(evidence/'synthetic.txt').write_text('synthetic only\n')
            case_file=root/'case.json';case_file.write_text(json.dumps({'case_id':'synthetic-environment-case',
                'evidence_root':str(evidence),'output_root':str(root/'case'),'synthetic':True}))
            case=Case(case_file)
            first=manager.execution_snapshot(case)
            check={'name':'synthetic-json','argv':['python','-c','import json;print(json.dumps({"count":len([1,2,3])}))'],'expected_stdout':'"count": 3'}
            prepared=manager.prepare({'base_environment_id':original,'checks':[check]},operation_id='prepare',background=False)
            self.assertEqual(prepared['state'],'succeeded',prepared)
            candidate=prepared['result']['environment_id']
            self.assertTrue(prepared['result']['plan']['installed']['apt'])
            manager.set_default(candidate,expected_revision=1,operation_id='default-two')
            self.assertEqual(manager.execution_snapshot(case)['environment_id'],original)
            with patch.dict(os.environ,{'FORSIC_MANAGEMENT_ROOT':str(manager.root)}):
                old_execution=invoke(case,{'command':'cat /evidence/synthetic.txt'},'synthetic-env-session')
            self.assertNotIn('error',old_execution,old_execution)
            self.assertEqual(old_execution['execution_policy']['image_id'],first['images']['analysis']['image_id'])
            manager.select_case(case,candidate,expected_revision=1,operation_id='switch',sessions_safe=lambda:True)
            self.assertEqual(manager.execution_snapshot(case)['environment_id'],candidate)
            with patch.dict(os.environ,{'FORSIC_MANAGEMENT_ROOT':str(manager.root)}):
                new_execution=invoke(case,{'command':'cat /evidence/synthetic.txt'},'synthetic-env-session')
            self.assertNotIn('error',new_execution,new_execution)
            self.assertEqual(new_execution['execution_policy']['image_id'],prepared['result']['images']['analysis']['image_id'])
            manager.select_case(case,original,expected_revision=2,operation_id='rollback',sessions_safe=lambda:True)
            self.assertEqual(manager.execution_snapshot(case)['images'],first['images'])
            pack=root/'pack';pack.mkdir();(pack/'adapter.py').write_text('import json,pathlib;print(json.dumps({"count":len(pathlib.Path("/inputs/test.txt").read_text().splitlines())}))')
            fixture=root/'fixture';fixture.mkdir();(fixture/'test.txt').write_text('one\ntwo\n')
            checked=env.run_fixture(original,pack,['python','/extension/adapter.py'],fixture,{'json_fields':{'count':2}},operation_id='fixture',manager=manager,background=False)
            self.assertEqual(checked['state'],'succeeded',checked)
            self.assertTrue(checked['result']['passed'])
            import base64
            self.assertEqual(base64.b64decode(checked['result']['raw_output']['stdout_base64']),b'{"count": 2}\n')
            self.assertEqual(checked['result']['raw_output']['limit_bytes'],1024*1024)
            archive=root/'offline.tar'
            exported=manager.export_environment(original,archive,operation_id='export',background=False)
            self.assertEqual(exported['state'],'succeeded',exported)
            other=env.EnvironmentManager(root/'offline-manager')
            imported=other.import_environment(archive,operation_id='import',background=False)
            self.assertEqual(imported['state'],'succeeded',imported)
            self.assertEqual(other.manifest(original),manager.manifest(original))
            self.assertIsNone(other.catalog()['default_environment_id'])
            imported_fixture=env.run_fixture(original,pack,['python','/extension/adapter.py'],fixture,{'json_fields':{'count':2}},operation_id='fixture',manager=other,background=False)
            self.assertEqual(imported_fixture['state'],'succeeded',imported_fixture)
            if os.environ.get('FORSIC_ENV_TEST_RECEIPT'):
                receipt={'test':'real Docker environment preservation and offline roundtrip',
                    'source_image_id':first['images']['analysis']['image_id'],
                    'candidate_image_id':prepared['result']['images']['analysis']['image_id'],
                    'original_environment_id':original,'candidate_environment_id':candidate,
                    'fixture':checked['result'],'offline_fixture':imported_fixture['result'],
                    'old_case_execution':{'image_id':old_execution['execution_policy']['image_id'],'stdout':old_execution['stdout'],'execution':old_execution['execution']},
                    'new_case_execution':{'image_id':new_execution['execution_policy']['image_id'],'stdout':new_execution['stdout'],'execution':new_execution['execution']},
                    'archive_sha256':exported['result']['sha256'],
                    'network_downloads':False,'model_calls':False,'real_evidence':False}
                path=Path(os.environ['FORSIC_ENV_TEST_RECEIPT']);path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(receipt,indent=2)+'\n')



if __name__=='__main__':unittest.main()
