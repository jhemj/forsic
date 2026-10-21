"""HTTP management contracts with real local stores; Docker/native model calls excluded."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid
import zipfile

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from forsic_plugin import extension_hub as hub
from forsic_plugin.dashboard import management_api as api
from forsic_plugin.dashboard.management_store import ManagementStore
from forsic_plugin.evidence import Case
from hermes_cli.backend_retirement import RetirementFence
from test_extension_hub import archive


class ManagementAPITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(patch.stopall)
        self.home = Path(self.temporary.name)
        self.intake = self.home / 'intake'
        patch.dict(os.environ, {'HERMES_HOME': str(self.home), 'FORSIC_MANAGEMENT_ROOT': str(self.home / 'forsic-management')}).start()
        from hermes_cli.config import save_config
        save_config({'plugins': {'entries': {'forsic': {'settings': {'intake_root': str(self.intake)}}}}})
        self.builder = Mock()
        self.builder.list_jobs.return_value = []
        patch.object(api, 'builder_manager', return_value=self.builder).start()
        self.refreshed = patch('forsic_plugin.extension_runtime.refresh_extensions', return_value={'registered': True}).start()
        self.live_refresh = patch('tui_gateway.server._refresh_live_sessions').start()
        patch.object(api.runtime, '_live_sessions', return_value=[]).start()
        patch.object(api.runtime, '_durable_idle', return_value=True).start()
        self.idle = patch('hermes_cli.web_server_idle_proof.idle_proof', return_value={'idle': True}).start()
        patch('hermes_cli.backend_retirement.retirement', RetirementFence()).start()
        self.app = FastAPI()
        self.app.state.initial_profile = 'default'
        self.app.include_router(api.router, prefix='/api/plugins/forsic')
        self.client = TestClient(self.app, base_url='http://localhost:8860', client=('127.0.0.1', 1234))
        self.manager = api.EnvironmentManager()
        self.bundle = archive(Path(__file__).parent/'examples/extensions/tool', self.home/'tool.zip')

    def get(self, case_id=''):
        response = self.client.get('/api/plugins/forsic/management', params={'profile':'default','case_id':case_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def action(self, action, *, case_id='', operation_id=None, revision=None, **fields):
        return self.client.post('/api/plugins/forsic/management/action?profile=default', json={
            'action':action,'case_id':case_id,'operation_id':operation_id or uuid.uuid4().hex,
            'expected_revision':revision or self.get(case_id)['revision'],**fields})

    def upload(self, content=None, kind='pack'):
        response = self.client.post('/api/plugins/forsic/management/upload?profile=default',
            files={'file':('upload.zip',content if content is not None else self.bundle.read_bytes())},data={'kind':kind})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def register(self, operation_id='register'):
        image = {'image_id':'sha256:'+'1'*64,'repo_digests':[], 'os':'linux','architecture':'amd64','platform':'linux/amd64'}
        with patch.object(self.manager,'_inspect',return_value=image), patch.object(self.manager,'_smoke',return_value={'synthetic':True}), patch.object(self.manager,'_mount_smoke',return_value={'synthetic':True}), patch.object(self.manager,'_run',return_value=b'{"apt":{},"python":{},"binaries":{},"rules":{}}'):
            record = self.manager.register(operation_id=operation_id,background=False)
        self.assertEqual(record['state'],'succeeded',record)
        return record['result']['environment_id']

    def case(self):
        evidence=self.home/'evidence';evidence.mkdir(exist_ok=True)
        manifest=self.intake/'cases'/'case-a'/'case.json';manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({'case_id':'case-a','evidence_root':str(evidence),'output_root':str(manifest.parent/'results'),'label':'합성'}))
        from hermes_state import SessionDB
        db=SessionDB(self.home/'state.db')
        try: db.create_session('case-session','web')
        finally: db.close()
        (self.intake/(hashlib.sha256(b'case-session').hexdigest()+'.json')).write_text(json.dumps({'manifest':str(manifest),'stage':'ready'}))
        return Case(manifest)

    def test_get_is_read_only_and_no_management_or_builder_execution(self):
        first=self.get();self.get()
        self.assertFalse((self.home/'forsic-management').exists())
        self.assertFalse(first['environments']['items'])
        self.builder.create.assert_not_called();self.builder.start.assert_not_called();self.refreshed.assert_not_called()

    def test_pack_upload_preview_canonical_hash_import_and_exact_selection(self):
        uploaded=self.upload()
        self.assertEqual(uploaded['sha256'],hashlib.sha256(self.bundle.read_bytes()).hexdigest())
        preview=self.action('hub.preview',upload_id=uploaded['upload_id']).json()['preview']
        self.assertNotEqual(preview['sha256'],uploaded['sha256'])
        imported=self.action('hub.import',upload_id=uploaded['upload_id'],expected_sha256=preview['sha256'])
        self.assertEqual(imported.status_code,200,imported.text)
        selected={'local.tool-example':{'version':'1.0.0','sha256':preview['sha256'],'enabled':False,'tools':{'count-lines':False},'skills':{}}}
        response=self.action('hub.select',scope='default',selection=selected)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(hub.selection_snapshot(self.home)['selection']['packs'],selected)
        self.assertTrue(response.json()['snapshot']['packs']['items'][0]['referenced'])
        self.assertTrue(self.refreshed.called);self.assertTrue(self.live_refresh.called)

    def test_import_rejects_upload_tamper_and_unreviewed_content(self):
        uploaded=self.upload();store=ManagementStore(self.home)
        path=store.transfer_path(uploaded['upload_id']);path.chmod(0o600);path.write_bytes(b'changed')
        response=self.action('hub.preview',upload_id=uploaded['upload_id'])
        self.assertEqual(response.status_code,409,response.text)
        self.assertFalse(hub.list_packs(self.home))
        uploaded=self.upload()
        response=self.action('hub.import',upload_id=uploaded['upload_id'],expected_sha256='0'*64)
        self.assertEqual(response.status_code,409,response.text)
        self.assertFalse(hub.list_packs(self.home))

    def test_upload_type_bounds_and_no_path_tokens(self):
        from forsic_plugin.dashboard import management_store
        with patch.dict(management_store.LIMITS,{'pack':2}):
            response=self.client.post('/api/plugins/forsic/management/upload',files={'file':('x.zip',b'123')},data={'kind':'pack'})
            self.assertEqual(response.status_code,413,response.text)
        for token in ('../../config','x','a'*33):
            response=self.action('hub.preview',upload_id=token)
            self.assertEqual(response.status_code,409,response.text)
        response=self.client.post('/api/plugins/forsic/management/upload',files={'file':('x',b'123')},data={'kind':'unknown'})
        self.assertEqual(response.status_code,409,response.text)

    def test_zip_escape_is_rejected_without_registration_or_execution(self):
        bad=self.home/'bad.zip'
        with zipfile.ZipFile(bad,'w') as out:out.writestr('../escaped',b'no')
        uploaded=self.upload(bad.read_bytes())
        response=self.action('hub.preview',upload_id=uploaded['upload_id'])
        self.assertEqual(response.status_code,409,response.text)
        self.assertFalse((self.home/'escaped').exists());self.assertFalse(hub.list_packs(self.home))

    def test_idempotent_replay_conflict_and_stale_revision(self):
        uploaded=self.upload();old=self.get()['revision']
        body={'upload_id':uploaded['upload_id']}
        first=self.action('hub.preview',operation_id='preview-one',revision=old,**body)
        again=self.action('hub.preview',operation_id='preview-one',revision='stale',**body)
        self.assertEqual(first.json()['preview'],again.json()['preview'])
        conflict=self.action('hub.preview',operation_id='preview-one',upload_id='0'*32)
        self.assertEqual(conflict.status_code,409)
        stale=self.action('env.register',revision='stale')
        self.assertEqual(stale.status_code,409);self.assertFalse((self.manager.root/'operations').exists())

    def test_default_environment_does_not_change_case_pin_and_busy_change_is_pending(self):
        case=self.case();first=self.register();self.manager.set_default(first,expected_revision=0,operation_id='default-one')
        self.manager.select_case(case,first,extensions={'packs':{}},expected_revision=0,operation_id='pin-one',sessions_safe=lambda:True)
        second=self.register('register-two')
        response=self.action('env.set_default',case_id='case-a',environment_id=second)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.manager.case_state(case)['current']['environment_id'],first)
        self.idle.return_value={'idle':False}
        response=self.action('env.apply',case_id='case-a',environment_id=second,reason='합성 전환')
        self.assertEqual(response.status_code,200,response.text)
        state=self.manager.case_state(case)
        self.assertEqual(state['current']['environment_id'],first);self.assertEqual(state['pending']['environment_id'],second)
        self.get('case-a');self.assertEqual(self.manager.case_state(case),state)
        self.idle.return_value={'idle':True}
        response=self.action('env.apply_pending',case_id='case-a')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.manager.case_state(case)['current']['environment_id'],second)
        self.assertEqual(len(self.manager.case_state(case)['history']),2)

    def test_delete_rejects_archived_history_and_unknown_reference_state(self):
        case=self.case();record=hub.import_pack(self.bundle,self.home);identity=self.register()
        selected={'packs':{'local.tool-example':{'version':'1.0.0','sha256':record['sha256'],'enabled':False}}}
        self.manager.select_case(case,identity,extensions=selected,expected_revision=0,operation_id='pin-one',sessions_safe=lambda:True)
        self.manager.select_case(case,identity,extensions={'packs':{}},expected_revision=1,operation_id='pin-two',sessions_safe=lambda:True)
        response=self.action('hub.delete',id='local.tool-example',version='1.0.0')
        self.assertEqual(response.status_code,409,response.text);self.assertTrue(hub.list_packs(self.home))
        (self.intake/'broken.json').write_text('{')
        response=self.client.get('/api/plugins/forsic/management')
        self.assertEqual(response.status_code,409,response.text)

    def test_export_download_is_opaque_and_rejects_modified_or_linked_file(self):
        hub.import_pack(self.bundle,self.home)
        response=self.action('hub.export',id='local.tool-example',version='1.0.0')
        self.assertEqual(response.status_code,200,response.text)
        token=response.json()['artifact_id'];url='/api/plugins/forsic/management/files/'+token
        downloaded=self.client.get(url)
        self.assertEqual(downloaded.status_code,200,downloaded.text)
        self.assertTrue(zipfile.is_zipfile(__import__('io').BytesIO(downloaded.content)))
        path=ManagementStore(self.home).artifact_destination(token);path.chmod(0o600);path.write_bytes(b'changed')
        self.assertEqual(self.client.get(url).status_code,409)
        path.unlink();path.symlink_to(self.bundle)
        self.assertEqual(self.client.get(url).status_code,409)

    def test_native_auth_and_local_profile_origin_boundaries(self):
        from hermes_cli import web_server as native
        protected=FastAPI();protected.state.auth_required=False;protected.state.initial_profile='default'
        protected.include_router(api.router,prefix='/api/plugins/forsic',dependencies=[Depends(native._require_token)])
        unauthorized=TestClient(protected,base_url='http://localhost:8860',client=('127.0.0.1',1234))
        self.assertEqual(unauthorized.get('/api/plugins/forsic/management').status_code,401)
        authorized=unauthorized.get('/api/plugins/forsic/management',headers={'X-Hermes-Session-Token':native._SESSION_TOKEN})
        self.assertEqual(authorized.status_code,200,authorized.text)
        self.assertEqual(unauthorized.post('/api/plugins/forsic/management/upload',files={'file':('x',b'x')},data={'kind':'pack'}).status_code,401)
        for headers in ({'origin':'https://evil.invalid'},{'x-forwarded-for':'127.0.0.1'}):
            response=self.client.post('/api/plugins/forsic/management/upload',files={'file':('x',b'x')},data={'kind':'pack'},headers=headers)
            self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(self.client.get('/api/plugins/forsic/management?profile=other').status_code,403)
        remote=TestClient(self.app,base_url='http://localhost:8860',client=('192.0.2.1',1234))
        self.assertFalse(remote.get('/api/plugins/forsic/management').json()['local_admin'])
        self.assertEqual(remote.post('/api/plugins/forsic/management/upload',files={'file':('x',b'x')},data={'kind':'pack'}).status_code,403)

    def test_builder_request_start_cancel_are_separate_facade_calls(self):
        self.builder.create.return_value={'job_id':'b1','state':'draft'}
        self.builder.start.return_value={'job_id':'b1','state':'running'}
        self.builder.cancel.return_value={'job_id':'b1','state':'cancel_requested'}
        response=self.action('builder.create',repository_url='https://example.invalid/tool.git',request='합성 파일 변환')
        self.assertEqual(response.status_code,200,response.text);self.builder.start.assert_not_called()
        response=self.action('builder.start',job_id='b1')
        self.assertEqual(response.status_code,200,response.text);self.builder.start.assert_called_once_with('b1',profile='default')
        response=self.action('builder.cancel',job_id='b1')
        self.assertEqual(response.json()['status'],'cancel_requested');self.builder.cancel.assert_called_once_with('b1')

    def test_prepare_and_cancel_delegate_actual_management_operation(self):
        plan={'base_environment_id':'env-fixed','apt':{'parser':'1.2'},'checks':[]}
        record={'operation_id':'op-prepare','kind':'prepare','state':'running','stage':'build','inputs':{'plan':plan}}
        with patch.object(api.EnvironmentManager,'prepare',return_value=record) as prepare:
            response=self.action('env.prepare',plan=plan,operation_id='op-prepare')
            self.assertEqual(response.status_code,200,response.text);prepare.assert_called_once_with(plan,operation_id='op-prepare')
        with patch.object(api.EnvironmentManager,'cancel',return_value={**record,'cancel_requested':True}) as cancel:
            response=self.action('operation.cancel',target_operation_id='op-prepare')
            self.assertEqual(response.status_code,200,response.text);cancel.assert_called_once_with('op-prepare')
            self.assertEqual(response.json()['job']['status'],'cancel_requested')

    def test_unverified_native_schema_is_pending_and_explicit_apply_retries(self):
        hub.import_pack(self.bundle,self.home)
        record=hub.list_packs(self.home)[0]
        chosen={'local.tool-example':{'version':'1.0.0','sha256':record['sha256'],'enabled':False}}
        with patch.object(api.runtime,'_applied',return_value=False):
            response=self.action('hub.select',scope='default',selection=chosen)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'pending')
        self.assertTrue(response.json()['snapshot']['runtime_pending'])
        response=self.action('env.apply_pending')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'applied')
        self.assertFalse(response.json()['snapshot']['runtime_pending'])

    def test_async_environment_export_artifact_is_bound_to_completed_operation(self):
        identity=self.register()
        payload=b'synthetic-image-bundle-only-no-docker'
        def exported(_manager, environment_id, destination, *, operation_id, extensions=None):
            def work(_operation):
                destination.write_bytes(payload)
                return {'path':str(destination),'sha256':hashlib.sha256(payload).hexdigest()}
            return self.manager._start('export',{'environment_id':environment_id,'destination':str(destination)},operation_id,work,background=False)
        with patch.object(api.EnvironmentManager,'export_environment',exported):
            response=self.action('env.export',environment_id=identity,operation_id='offline-export')
        self.assertEqual(response.status_code,200,response.text)
        token=response.json()['job']['artifact_id']
        download=self.client.get('/api/plugins/forsic/management/files/'+token)
        self.assertEqual(download.status_code,200,download.text)
        self.assertEqual(download.content,payload)

    def test_pack_dependencies_make_reviewable_environment_plan_without_execution(self):
        identity=self.register()
        dependency={'method':'apt','name':'testdisk','version':'7.2-1'}
        bundle=archive(Path(__file__).parent/'examples/extensions/tool',self.home/'deps.zip',extra=lambda manifest:manifest.update(dependencies=[dependency]))
        pack=hub.import_pack(bundle,self.home)
        with patch('forsic_plugin.environments.subprocess.Popen',side_effect=AssertionError('Pure plan must not launch a process')):
            response=self.action('env.plan_dependencies',base_environment_id=identity,id=pack['manifest']['id'],version=pack['manifest']['version'],expected_sha256=pack['sha256'])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['plan']['apt'],{'testdisk':'7.2-1'})
        self.assertEqual(response.json()['plan']['checks'],[])
        self.assertTrue(response.json()['functional_checks_required'])
        self.assertEqual(len(self.manager.catalog()['environments']),1)

    def test_offline_pack_export_uses_the_exact_chosen_pack_archive(self):
        identity=self.register();pack=hub.import_pack(self.bundle,self.home)
        captured=[]
        def exported(_manager, environment_id, destination, *, operation_id, extensions=None):
            captured.extend(hub.preview_pack(path)['sha256'] for path in extensions.values())
            def work(_operation):
                destination.write_bytes(b'synthetic-offline-bundle')
                return {'path':str(destination),'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}
            return self.manager._start('export',{'environment_id':environment_id,'destination':str(destination)},operation_id,work,background=False)
        with patch.object(api.EnvironmentManager,'export_environment',exported):
            response=self.action('hub.export',scope='offline',environment_id=identity,id=pack['manifest']['id'],version=pack['manifest']['version'])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(captured,[pack['sha256']])
        self.assertEqual(response.json()['job']['status'],'succeeded')

    def test_case_mutations_require_native_ownership_in_the_served_profile(self):
        case=self.case()
        (self.intake/(hashlib.sha256(b'case-session').hexdigest()+'.json')).unlink()
        response=self.client.get('/api/plugins/forsic/management?case_id=case-a')
        self.assertEqual(response.status_code,404,response.text)
        self.assertFalse((case.output/'environment.json').exists())

    def test_native_dynamic_dashboard_loader_can_import_management_router(self):
        import importlib.util
        import sys
        name='hermes_dashboard_plugin_forsic_smoke'
        spec=importlib.util.spec_from_file_location(name,Path(__file__).parent/'forsic_plugin/dashboard/plugin_api.py')
        module=importlib.util.module_from_spec(spec)
        sys.modules[name]=module
        try:
            spec.loader.exec_module(module)
            app=FastAPI();app.state.initial_profile='default';app.include_router(module.router,prefix='/api/plugins/forsic')
            client=TestClient(app,base_url='http://localhost:8860',client=('127.0.0.1',1234))
            response=client.get('/api/plugins/forsic/management')
            self.assertEqual(response.status_code,200,response.text)
        finally:sys.modules.pop(name,None)
