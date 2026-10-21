"""Offline tenant API, archive and native-recall ownership boundaries."""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi.testclient import TestClient

from forsic_plugin.case_library import CaseLibrary
from forsic_plugin.dashboard import plugin_api
from forsic_plugin.dashboard.management_store import ManagementStore
from forsic_plugin.evidence import Case
from forsic_plugin.tenants import TenantRegistry, bind_scope


class TenantBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'HERMES_HOME': str(self.root)}).start()
        # Real native profile resolution activates a process-wide multiplexer
        # and launch-home pin. Keep those runtime globals inside this fixture.
        import hermes_constants
        from agent import secret_scope
        from tui_gateway import launch_profile_policy,server
        for module,name in ((hermes_constants,'_PINNED_PROCESS_HERMES_HOME'),
                            (secret_scope,'_MULTIPLEX_ACTIVE'),(secret_scope,'_AUTO_PINNED_HOME'),
                            (launch_profile_policy,'_snapshot')):
            patch.object(module,name,getattr(module,name)).start()
        patch.object(server,'_served_profile_homes',set(server._served_profile_homes)).start()
        (self.root / 'config.yaml').write_text('model:\n  default: synthetic-no-model\n')
        self.registry = TenantRegistry(self.root)
        self.legacy = self.registry.bootstrap_legacy(intake_root=self.root/'legacy-intake',library_store=self.root/'legacy-library.sqlite3')
        self.a = self.registry.create('Tenant A', operation_id=str(uuid.uuid4()))
        self.b = self.registry.create('Tenant B', operation_id=str(uuid.uuid4()))
        self.app = FastAPI()
        self.app.state.initial_profile = 'default'
        self.app.include_router(plugin_api.router, prefix='/api/plugins/forsic')
        self.client = TestClient(self.app, base_url='http://localhost:8860', client=('127.0.0.1', 1234))
        self.prefix = '/api/plugins/forsic'

    def params(self, scope):
        return {'tenant_id': scope['tenant_id'], 'profile': scope['profile_name']}

    def files(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()}

    def case(self, scope, name='same-case'):
        folder = Path(scope['case_root']) / name
        evidence = folder / 'evidence'
        evidence.mkdir(parents=True)
        (evidence / 'trace.log').write_text('Synthetic ' + scope['display_name'] + ' permission_denied\n')
        manifest = folder / 'case.json'
        manifest.write_text(json.dumps({'case_id': name, 'tenant_id': scope['tenant_id'],
            'profile_name': scope['profile_name'], 'label': scope['display_name'], 'synthetic': True,
            'evidence_root': str(evidence), 'output_root': str(folder / 'results'),
            'library_path': scope['library_store']}))
        with bind_scope(scope):
            case = Case(manifest)
            source = json.loads(case.invoke('forsic_read', {'path': 'trace.log'}))['evidence_id']
            result = json.loads(case.invoke('forsic_note', {'action': 'save', 'question': '공통 질문',
                'answer': scope['display_name'] + ' 관측', 'evidence_ids': [source]}))
            self.assertNotIn('error', result)
            saved = CaseLibrary(case).save({})
        return case, source, saved

    def test_first_entry_reads_create_no_case_session_store_or_model(self):
        from hermes_cli import config,config_backups
        config._LOAD_CONFIG_CACHE.clear()
        before = self.files()
        with patch.object(plugin_api, 'Case', side_effect=AssertionError('entry opened a case')), \
             patch.object(config_backups,'backup_config',wraps=config_backups.backup_config) as backups, \
             patch('forsic_plugin.dashboard.management_api.builder_manager', return_value=Mock(list_jobs=lambda: [])):
            for path in ('/tenants', '/tenants/resolve', '/tenant-memory', '/tenant-memory/snapshot', '/library', '/management'):
                response = self.client.get(self.prefix + path, params=self.params(self.a))
                self.assertEqual(response.status_code, 200, (path, response.text))
        backups.assert_not_called()
        self.assertEqual(self.files(), before)
        for scope in (self.a, self.b):
            self.assertFalse(Path(scope['native_db']).exists())
            self.assertFalse(Path(scope['case_root']).exists())
            self.assertFalse(Path(scope['library_store']).exists())

    def test_empty_completed_registry_allows_host_setup_list_and_local_create(self):
        from forsic_plugin.tenants import _atomic, current_scope
        _atomic(self.registry.path, {'schema': 1, 'tenants': [], 'operations': {},
                                     'legacy_migration_complete': True})
        before = self.files()
        with patch('forsic_plugin.dashboard.plugin_api.Case', side_effect=AssertionError('opened case')), \
             patch('socket.create_connection', side_effect=AssertionError('network request')), \
             patch('socket.socket.connect', side_effect=AssertionError('network request')):
            setup = self.client.get(self.prefix + '/setup')
            self.assertEqual(setup.status_code, 200, setup.text)
            self.assertTrue(setup.json()['local_admin'])
            listed = self.client.get(self.prefix + '/tenants')
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual(listed.json(), {'tenants': [], 'access': {'can_create': True, 'mode': 'local'}})
            self.assertEqual(self.files(), before)
            # The host entry exception does not relax tenant ownership elsewhere.
            with self.assertRaises(PermissionError):
                current_scope()
            self.assertEqual(self.client.get(self.prefix + '/library').status_code, 400)
            intent = {'display_name': 'Fresh tenant', 'memory_mode': 'manual', 'operation_id': str(uuid.uuid4())}
            created = self.client.post(self.prefix + '/tenants', json=intent)
            self.assertEqual(created.status_code, 200, created.text)
            self.assertEqual(len(self.registry.list_public()), 1)
            self.assertTrue(self.registry._load()['legacy_migration_complete'])

    def test_empty_registry_host_administration_keeps_transport_and_profile_fences(self):
        from forsic_plugin.tenants import _atomic
        _atomic(self.registry.path, {'schema': 1, 'tenants': [], 'operations': {},
                                     'legacy_migration_complete': True})
        intent = {'display_name': 'Denied tenant', 'operation_id': str(uuid.uuid4())}
        remote = TestClient(self.app, base_url='http://localhost:8860', client=('192.0.2.20', 1234))
        self.assertEqual(remote.post(self.prefix + '/tenants', json=intent).status_code, 403)
        for headers in ({'Origin': 'https://evil.example'}, {'X-Forwarded-For': '127.0.0.1'},
                        {'Host': 'evil.example'}):
            denied = self.client.post(self.prefix + '/tenants', json=intent, headers=headers)
            self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(self.client.get(self.prefix + '/setup', params={'profile': 'other'}).status_code, 404)
        self.assertEqual(self.registry.list_public(), [])

    def test_mismatched_selectors_and_direct_foreign_case_session_denied(self):
        _, _, saved = self.case(self.b)
        from hermes_state import SessionDB
        db = SessionDB(Path(self.a['native_db']))
        try:
            # Shared SQLite file does not establish ownership of its rows.
            db.create_session('foreign-session', 'web', profile_name=self.a['profile_name'])
        finally:
            db.close()
        with closing(sqlite3.connect(self.a['native_db'])) as shared, shared:
            shared.execute('UPDATE sessions SET profile_name=?', (self.b['profile_name'],))
        requests = [
            ('/tenants/resolve', {'tenant_id': self.a['tenant_id'], 'profile': self.b['profile_name']}),
            ('/library', {**self.params(self.a), 'case_id': 'same-case'}),
            ('/tenants/resolve', {**self.params(self.a), 'session_id': 'foreign-session'}),
            ('/library/' + saved['archive_id'], {**self.params(self.a), 'revision': saved['revision']}),
        ]
        for path, params in requests:
            response = self.client.get(self.prefix + path, params=params)
            self.assertIn(response.status_code, (400, 403, 404, 409), (path, response.text))
        response = self.client.get(self.prefix + '/tenants/resolve', params=[
            ('tenant_id', self.a['tenant_id']), ('tenant_id', self.b['tenant_id'])])
        self.assertEqual(response.status_code, 403)

    def test_same_query_uses_only_own_archive_and_pinned_sources(self):
        ca, ea, sa = self.case(self.a)
        _, eb, sb = self.case(self.b)
        original = Path(self.a['library_store']).read_bytes()
        with bind_scope(self.a):
            library = CaseLibrary()
            result = library.search({'query': '공통'})
            self.assertEqual([x['archive_id'] for x in result['cases']], [sa['archive_id']])
            self.assertTrue(result['reference_only']); self.assertFalse(result['current_case_evidence'])
            for action in ({'archive_id': sb['archive_id']}, {'action': 'source', 'archive_id': sa['archive_id'],
                           'revision': sa['revision'], 'source_id': eb},
                           {'archive_id': sa['archive_id'], 'revision': sb['revision']}):
                with self.assertRaises(ValueError): library.get(action)
            source = library.get({'action': 'source', **sa, 'source_id': ea})
            self.assertEqual(source['source']['id'], ea)
            ca.db.rename(ca.db.with_suffix('.saved'))
            self.assertEqual(library.get(sa)['source_state'], 'unavailable')
        self.assertEqual(Path(self.a['library_store']).read_bytes(), original)

    def test_foreign_archive_source_is_denied_before_source_database_open(self):
        _, _, saved = self.case(self.a)
        foreign, _, _ = self.case(self.b)
        with closing(sqlite3.connect(self.a['library_store'])) as db, db:
            db.execute('UPDATE cases SET source_db=?', (str(foreign.db),))
        real_connect = sqlite3.connect
        def checked(path, *args, **kwargs):
            self.assertNotIn(str(foreign.db), str(path), 'foreign source opened before ownership check')
            return real_connect(path, *args, **kwargs)
        with bind_scope(self.a), patch('forsic_plugin.case_library.sqlite3.connect', side_effect=checked):
            with self.assertRaises(PermissionError): CaseLibrary().get(saved)

    def test_management_tokens_and_retained_objects_cannot_cross_tenant(self):
        token = uuid.uuid4().hex
        with bind_scope(self.a):
            store = ManagementStore(self.a['profile_home'])
            destination = store.artifact_destination(token)
            destination.write_bytes(b'synthetic artifact')
            store.register_artifact(token)
        with bind_scope(self.b):
            with self.assertRaises(PermissionError): store.transfer_path(token, 'artifacts')
            with self.assertRaises(PermissionError): ManagementStore(self.a['profile_home'])
            other = ManagementStore(self.b['profile_home'])
            self.assertFalse(other.transfer_path(token, 'artifacts').exists())

    def test_registered_routes_without_selection_never_follow_global_pointer(self):
        for path in ('/library','/management','/investigations','/status','/tenant-memory'):
            response=self.client.get(self.prefix+path)
            self.assertEqual(response.status_code,400,(path,response.text))

    def test_remote_entry_lists_registered_tenants_before_cases_or_sessions_exist(self):
        from forsic_plugin.remote_policy import ForsicRemoteAccessMiddleware,ForsicRemotePolicy
        app=FastAPI();app.state.initial_profile='default'
        app.include_router(plugin_api.router,prefix=self.prefix)
        app.add_middleware(ForsicRemoteAccessMiddleware,policy=ForsicRemotePolicy(
            {'remote_evidence_directory_only':True,'intake_root':self.legacy['intake_root']},self.root))
        remote=TestClient(app,base_url='http://192.0.2.50:8860',client=('192.0.2.20',1234))
        before=self.files()
        listed=remote.get(self.prefix+'/tenants')
        self.assertEqual(listed.status_code,200,listed.text)
        self.assertEqual([t['tenant_id'] for t in listed.json()['tenants']],
                         [t['tenant_id'] for t in (self.legacy,self.a,self.b)])
        self.assertEqual(listed.json()['access'],{'can_create':False,'mode':'restricted'})
        selected=remote.get(self.prefix+'/tenants/resolve',params={'tenant_id':self.legacy['tenant_id']})
        self.assertEqual(selected.status_code,200,selected.text)
        self.assertEqual(selected.json()['profile'],'default')
        for tenant in (self.a,self.b):
            selected=remote.get(self.prefix+'/tenants/resolve',params=self.params(tenant))
            self.assertEqual(selected.status_code,200,selected.text)
            self.assertEqual(selected.json()['profile'],tenant['profile_name'])
            self.assertFalse(Path(tenant['native_db']).exists())
            self.assertFalse(Path(tenant['case_root']).exists())
        # A known tenant does not authorize a different profile or a native
        # profile that was never registered; reject before opening its data.
        with patch('forsic_plugin.tenants.assert_session_scope',side_effect=AssertionError('foreign data opened')):
            for query in ({'tenant_id':self.a['tenant_id'],'profile':self.b['profile_name']},
                          {'tenant_id':self.a['tenant_id'],'profile':'unregistered','session_id':'foreign'},
                          [('profile',self.a['profile_name']),('profile',self.b['profile_name'])]):
                denied=remote.get(self.prefix+'/tenants/resolve',params=query)
                self.assertIn(denied.status_code,(403,404))
        self.assertEqual(remote.post(self.prefix+'/tenants',json={}).status_code,403)
        self.assertEqual(remote.get(self.prefix+'/tenant-memory',params={'tenant_id':self.legacy['tenant_id']}).status_code,403)
        self.assertEqual(remote.get(self.prefix+'/investigations').status_code,400)
        self.assertEqual(self.files(),before)
        local=self.client.get(self.prefix+'/tenants').json()
        self.assertEqual(len(local['tenants']),3)
        self.assertEqual(local['access'],{'can_create':True,'mode':'local'})

    def test_named_remote_host_still_serves_only_its_registered_tenant(self):
        from forsic_plugin.remote_policy import ForsicRemoteAccessMiddleware,ForsicRemotePolicy
        app=FastAPI();app.state.initial_profile=self.a['profile_name']
        app.include_router(plugin_api.router,prefix=self.prefix)
        app.add_middleware(ForsicRemoteAccessMiddleware,policy=ForsicRemotePolicy(
            {'remote_evidence_directory_only':True,'intake_root':self.a['intake_root']},
            self.a['profile_home'],profile=self.a['profile_name']))
        remote=TestClient(app,base_url='http://192.0.2.50:8860',client=('100.64.0.20',1234))
        self.assertEqual([t['tenant_id'] for t in remote.get(self.prefix+'/tenants').json()['tenants']],
                         [self.a['tenant_id']])
        self.assertEqual(remote.get(self.prefix+'/tenants/resolve',params=self.params(self.a)).status_code,200)
        for tenant in (self.b,self.legacy):
            response=remote.get(self.prefix+'/tenants/resolve',params={'tenant_id':tenant['tenant_id']})
            self.assertIn(response.status_code,(403,404))

    def test_remote_default_host_selects_first_fresh_tenant_without_legacy_workspace(self):
        from forsic_plugin.remote_policy import ForsicRemoteAccessMiddleware,ForsicRemotePolicy
        from forsic_plugin.tenants import _atomic
        _atomic(self.registry.path, {'schema':1,'tenants':[],'operations':{},'legacy_migration_complete':True})
        fresh=self.registry.create('First fresh workspace',operation_id=str(uuid.uuid4()))
        app=FastAPI();app.state.initial_profile='default'
        app.include_router(plugin_api.router,prefix=self.prefix)
        app.add_middleware(ForsicRemoteAccessMiddleware,policy=ForsicRemotePolicy(
            {'remote_evidence_directory_only':True},self.root))
        remote=TestClient(app,base_url='http://192.0.2.50:8860',client=('100.64.0.20',1234))
        before=self.files()
        with patch('forsic_plugin.tenants.assert_session_scope',side_effect=AssertionError('entry opened a session')):
            for query in ({},{'profile':'default'}):
                listed=remote.get(self.prefix+'/tenants',params=query)
                self.assertEqual(listed.status_code,200,listed.text)
                self.assertEqual([t['tenant_id'] for t in listed.json()['tenants']],[fresh['tenant_id']])
            for query in ({'tenant_id':fresh['tenant_id']},self.params(fresh)):
                resolved=remote.get(self.prefix+'/tenants/resolve',params=query)
                self.assertEqual(resolved.status_code,200,resolved.text)
                self.assertEqual(resolved.json()['profile'],fresh['profile_name'])
            denied=remote.get(self.prefix+'/investigations',params={'profile':'default'})
            self.assertIn(denied.status_code,(400,403,404),denied.text)
        self.assertFalse(Path(fresh['native_db']).exists())
        self.assertFalse(Path(fresh['case_root']).exists())
        self.assertEqual(self.files(),before)

    def bound_native_session(self, tenant, session_id, case_id):
        from hermes_state import SessionDB
        from forsic_plugin.intake import write_json
        case=Path(tenant['case_root'])/case_id
        manifest=case/'case.json'
        (case/'evidence').mkdir(parents=True)
        write_json(manifest,{'case_id':case_id,'tenant_id':tenant['tenant_id'],
            'profile_name':tenant['profile_name'],'output_root':str(case/'results'),
            'evidence_root':str(case/'evidence'),'synthetic':True})
        binding=Path(tenant['intake_root'])/(hashlib.sha256(session_id.encode()).hexdigest()+'.json')
        write_json(binding,{'manifest':str(manifest)})
        with bind_scope(tenant):
            Case(manifest)
            db=SessionDB(Path(tenant['native_db']))
            try:db.create_session(session_id,'web',profile_name=tenant['profile_name'])
            finally:db.close()
        return binding,manifest

    def test_remote_same_native_session_id_stays_with_selected_tenant_and_case(self):
        from forsic_plugin.remote_policy import ForsicRemoteAccessMiddleware,ForsicRemotePolicy
        policy=ForsicRemotePolicy({'remote_evidence_directory_only':True},self.root)
        a_binding,_=self.bound_native_session(self.a,'shared-session-id','case-a')
        _,b_manifest=self.bound_native_session(self.b,'shared-session-id','case-b')
        app=FastAPI();app.state.initial_profile='default'
        app.include_router(plugin_api.router,prefix=self.prefix)
        app.add_middleware(ForsicRemoteAccessMiddleware,policy=policy)
        remote=TestClient(app,base_url='http://192.0.2.50:8860',client=('192.0.2.20',1234))
        for tenant,case_id,foreign_case in ((self.a,'case-a','case-b'),(self.b,'case-b','case-a')):
            selected=policy.select(profile=tenant['profile_name'])
            self.assertTrue(selected.bound('shared-session-id'))
            query={**self.params(tenant),'session_id':'shared-session-id','case_id':case_id}
            resolved=remote.get(self.prefix+'/tenants/resolve',params=query)
            self.assertEqual(resolved.status_code,200,resolved.text)
            self.assertEqual(resolved.json()['tenant_id'],tenant['tenant_id'])
            wrong=remote.get(self.prefix+'/tenants/resolve',params={**query,'case_id':foreign_case})
            self.assertIn(wrong.status_code,(403,404),wrong.text)
            scope={'path':'/api/sessions/shared-session-id/messages','method':'GET',
                   'query_string':urlencode(self.params(tenant)).encode()}
            self.assertTrue(policy.http_allowed(scope))
        # The same row id in both databases cannot authorize another tenant's
        # manifest, even if the binding itself is in the selected intake root.
        a_binding.write_text(json.dumps({'manifest':str(b_manifest)}))
        self.assertFalse(policy.select(profile=self.a['profile_name']).bound('shared-session-id'))
        self.assertTrue(policy.select(profile=self.b['profile_name']).bound('shared-session-id'))

    def test_remote_native_resume_and_controls_follow_runtime_home_and_reject_forged_ownership(self):
        from forsic_plugin.remote_policy import ForsicRemotePolicy
        from tui_gateway import server
        policy=ForsicRemotePolicy({'remote_evidence_directory_only':True},self.root)
        for tenant in (self.legacy,self.a,self.b):
            self.bound_native_session(tenant,'shared-session-id','native-case')
            self.assertTrue(policy.rpc_allowed({'method':'session.resume','params':{
                'session_id':'shared-session-id','profile':tenant['profile_name']}}))
        # Native deferred runtime records carry profile_home, not profile_name.
        sessions={
            'a-live':{'session_key':'shared-session-id','profile_home':self.a['profile_home']},
            'b-live':{'session_key':'shared-session-id','profile_home':self.b['profile_home']},
            'default-live':{'session_key':'shared-session-id','profile_home':None},
            'forged-home':{'session_key':'shared-session-id','profile_home':str(self.root/'unregistered'),
                           'profile_name':self.a['profile_name']},
            'mismatched-name':{'session_key':'shared-session-id','profile_home':self.a['profile_home'],
                               'profile_name':self.b['profile_name']},
            'mismatched-default':{'session_key':'shared-session-id','profile_home':None,
                                  'profile_name':self.a['profile_name']},
        }
        with patch.dict(server._sessions,sessions,clear=True):
            for runtime,tenant in (('a-live',self.a),('b-live',self.b),('default-live',self.legacy)):
                for selection in ({'profile':tenant['profile_name']},{}):
                    for method,extra in (('session.control.read',{}),('session.activate',{}),
                                         ('session.control',{'action':'goal.pause'}),
                                         ('prompt.submit',{'text':'Continue this synthetic case'})):
                        params={'session_id':runtime,**selection,**extra}
                        self.assertTrue(policy.rpc_allowed({'method':method,'params':params}),(method,params))
            for runtime,selection in (('a-live',{'profile':self.b['profile_name']}),
                                      ('b-live',{'profile':self.a['profile_name']}),
                                      ('forged-home',{}),('mismatched-name',{}),('mismatched-default',{}),
                                      ('forged-home',{'profile':self.a['profile_name']}),
                                      ('mismatched-name',{'profile':self.a['profile_name']})):
                params={'session_id':runtime,**selection}
                self.assertFalse(policy.rpc_allowed({'method':'session.control.read','params':params}),params)
            self.assertTrue(policy.rpc_allowed({'method':'session.resume','params':{'session_id':'shared-session-id'}}))

    def test_remote_native_http_requires_the_same_canonical_profile_as_the_handler(self):
        from forsic_plugin.remote_policy import ForsicRemotePolicy
        policy=ForsicRemotePolicy({'remote_evidence_directory_only':True},self.root)
        root_binding,_=self.bound_native_session(self.legacy,'duplicate-id','default-case')
        self.bound_native_session(self.a,'duplicate-id','tenant-case')
        def allowed(query):
            return policy.http_allowed({'path':'/api/sessions/duplicate-id/messages','method':'GET',
                'query_string':urlencode(query).encode()})
        # The native handler reads only profile=; aliases must not authorize a
        # tenant store while the handler silently opens the launch store.
        for query in (
            {'tenant_id':self.a['tenant_id']},
            {'profile_name':self.a['profile_name']},
            {'tenant_id':self.a['tenant_id'],'profile':''},
            {'profile_name':self.a['profile_name'],'profile':''},
            {'profile_name':self.a['profile_name'],'profile':'default'},
            [('profile',self.a['profile_name']),('profile','')],
            [('profile',''),('profile',self.a['profile_name'])],
            {'tenant_id':self.b['tenant_id'],'profile':self.a['profile_name']},
        ):
            with self.subTest(query=query):
                self.assertFalse(allowed(query))
        self.assertTrue(allowed(self.params(self.a)))
        self.assertTrue(allowed({'profile':self.a['profile_name']}))
        self.assertTrue(allowed({'profile':'default'}))
        self.assertTrue(allowed({}))
        root_binding.unlink()
        self.assertFalse(allowed({}))
        self.assertFalse(allowed({'tenant_id':self.a['tenant_id']}))
        self.assertTrue(allowed(self.params(self.a)))

    def test_unregistered_native_profile_cannot_fall_back_to_global_intake_or_library(self):
        from forsic_plugin.tenants import current_scope
        from forsic_plugin.intake import Intake
        from hermes_cli.profiles import create_profile
        from hermes_constants import set_hermes_home_override,reset_hermes_home_override
        foreign=create_profile('unregistered',no_alias=True,no_skills=True)
        token=set_hermes_home_override(foreign)
        try:
            for operation in (current_scope,Intake,CaseLibrary):
                with self.assertRaises(PermissionError):operation()
        finally:reset_hermes_home_override(token)

    def test_native_recall_is_unavailable_even_with_explicit_db_profile_and_cursor(self):
        from tools import session_search_tool as recall
        with bind_scope(self.a), patch.object(recall, '_dispatch', side_effect=AssertionError('recall dispatched')):
            self.assertFalse(recall.check_session_search_requirements())
            for args in ({'query': 'secret'}, {'session_id': 'foreign'},
                         {'session_id': 'foreign', 'around_message_id': 12, 'window': 3},
                         {'query': 'secret', 'profile': self.b['profile_name'], 'exclude_session_ids': ['own']}):
                result = json.loads(recall.session_search(db=object(), **args))
                self.assertIn('disabled', result['error'])

    def test_native_live_history_and_prompt_reject_mismatched_explicit_profile(self):
        from tui_gateway import server
        record={'profile_home':self.b['profile_home'],'history':[{'role':'user','content':'B_PRIVATE_SYNTHETIC'}],
                'session_key':'','agent':None}
        with patch.dict(server._sessions,{'b-live':record},clear=True), \
             patch.object(server,'_hermes_home',str(self.root)), \
             patch.object(server,'_ensure_active_session_slot',side_effect=AssertionError('foreign prompt admitted')):
            for method,extra in (('session.history',{}),('prompt.submit',{'text':'A-selected request'})):
                response=server.handle_request({'id':1,'method':method,'params':{
                    'session_id':'b-live','profile':self.a['profile_name'],**extra}})
                self.assertEqual(response['error']['code'],4001)
                self.assertNotIn('B_PRIVATE_SYNTHETIC',json.dumps(response))
            for params in ({'session_id':'b-live','profile':self.b['profile_name']},{'session_id':'b-live'}):
                result=server.handle_request({'id':2,'method':'session.history','params':params})
                self.assertEqual(result['result']['messages'][0]['text'],'B_PRIVATE_SYNTHETIC')
            foreign_path=server.handle_request({'id':3,'method':'session.history','params':{
                'session_id':'b-live','profile':str(Path(self.b['profile_home']))}})
            self.assertEqual(foreign_path['error']['code'],4064)
            config_handler=Mock(return_value={'must_not_run':True})
            refused=server._profile_scoped(config_handler)(4,{
                'session_id':'b-live','profile':self.a['profile_name']})
            self.assertEqual(refused['error']['code'],4001)
            config_handler.assert_not_called()

    def test_strict_native_resume_never_adopts_foreign_donor_or_mints_missing_key(self):
        from hermes_state import SessionDB
        from tui_gateway import server
        legacy=self.registry.bootstrap_legacy(intake_root=self.root/'old-intake',library_store=self.root/'old-library.sqlite3')
        donor=SessionDB(Path(legacy['native_db']))
        self.addCleanup(donor.close)
        donor.create_session('default-owned','web',profile_name='default')
        donor.append_message('default-owned','user','DEFAULT_PRIVATE_SYNTHETIC')
        before=donor.export_session('default-owned')
        with patch.object(server,'_hermes_home',str(self.root)), \
             patch.object(server,'_get_db',side_effect=AssertionError('foreign donor opened')), \
             patch.object(server,'_schedule_agent_build',side_effect=AssertionError('resume built an agent')):
            targets=['default-owned',server._new_session_key()]
            for target in targets:
                response=server.handle_request({'id':1,'method':'session.resume','params':{
                    'session_id':target,'profile':self.a['profile_name'],'defer_history':True,'omit_messages':True}})
                self.assertEqual(response['error']['code'],4007,response)
        self.assertEqual(donor.export_session('default-owned'),before)
        recipient=SessionDB(Path(self.a['native_db']))
        try:
            self.assertEqual(recipient.list_sessions_rich(),[])
            self.assertIsNone(recipient.get_session('default-owned'))
            with self.assertRaisesRegex(ValueError,'owner'):
                recipient.import_sessions([donor.export_session('default-owned')])
            self.assertEqual(recipient.list_sessions_rich(),[])
            own={'id':'own-import','source':'import','profile_name':self.a['profile_name'],
                 'messages':[{'role':'user','content':'Synthetic own restore'}]}
            result=recipient.import_sessions([own])
            self.assertTrue(result['ok'])
            self.assertEqual(recipient.get_session('own-import')['profile_name'],self.a['profile_name'])
        finally:recipient.close()
        self.assertEqual(donor.export_session('default-owned'),before)
        # The opt-in guard does not change unmanaged native portability.
        unmanaged=SessionDB(self.root/'unmanaged/state.db')
        try:
            result=unmanaged.import_sessions([donor.export_session('default-owned')])
            self.assertTrue(result['ok'])
            self.assertEqual(unmanaged.get_messages('default-owned')[0]['content'],'DEFAULT_PRIVATE_SYNTHETIC')
        finally:unmanaged.close()

    def test_memory_ui_source_is_server_attested_and_cross_tenant_ids_fail(self):
        base = self.prefix + '/tenant-memory'
        response = self.client.post(base + '/action', params=self.params(self.a), json={
            'action': 'add', 'operation_id': str(uuid.uuid4()), 'content': 'Synthetic system timezone is UTC.',
            'applicability': 'Synthetic tenant A systems only'})
        self.assertEqual(response.status_code, 200, response.text)
        receipt = response.json()
        self.assertEqual(receipt['status'], 'committed', receipt)
        mid = receipt['item']['memory_id']
        item = self.client.get(base + '/' + mid, params=self.params(self.a)).json()
        self.assertEqual(item['source']['source_type'], 'user_attested')
        self.assertTrue(item['source']['explicit_request'])
        self.assertFalse(item['current_case_evidence'])
        response = self.client.get(base + '/' + mid, params=self.params(self.b))
        self.assertIn(response.status_code, (400, 404, 409))
        self.assertEqual(self.client.get(base, params=self.params(self.b)).json()['items'], [])
        forged = self.client.post(base + '/action', params=self.params(self.a), json={
            'action': 'add', 'operation_id': str(uuid.uuid4()), 'content': 'forged', 'source': {'confirmed': True}})
        self.assertEqual(forged.status_code, 422)
        remote = TestClient(self.app, base_url='http://localhost:8860', client=('198.51.100.1', 1234))
        denied = remote.post(base + '/action', params=self.params(self.a), json={
            'action': 'add', 'operation_id': str(uuid.uuid4()), 'content': 'not local'})
        self.assertEqual(denied.status_code, 403)

    def test_http_create_policy_cas_and_off_mode(self):
        intent={'display_name':'Tenant C','memory_mode':'manual','operation_id':str(uuid.uuid4())}
        first=self.client.post(self.prefix+'/tenants',json=intent)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(self.client.post(self.prefix+'/tenants',json=intent).json(),first.json())
        self.assertIn('tenant_id=',first.json()['start_url'])
        self.assertNotIn('profile_home',first.json())
        params=self.params(self.a)
        path=self.prefix+'/tenants/'+self.a['tenant_id']+'/memory-policy'
        change={'mode':'off','expected_revision':self.a['revision'],'operation_id':str(uuid.uuid4())}
        updated=self.client.put(path,params=params,json=change)
        self.assertEqual(updated.status_code,200,updated.text)
        self.assertEqual(updated.json()['memory_policy']['mode'],'off')
        stale=self.client.put(path,params=params,json={**change,'mode':'confirmed_auto','operation_id':str(uuid.uuid4())})
        self.assertEqual(stale.status_code,409)
        blocked=self.client.post(self.prefix+'/tenant-memory/action',params=params,json={
            'action':'add','operation_id':str(uuid.uuid4()),'content':'off must not save'})
        self.assertEqual(blocked.status_code,409,blocked.text)
        self.assertEqual(self.client.get(self.prefix+'/tenant-memory/snapshot',params=params).json()['entries'],[])

    def test_memory_http_edit_exclude_restore_history_and_committed_polling(self):
        base=self.prefix+'/tenant-memory';params=self.params(self.a)
        initial=self.client.get(base+'/snapshot',params=params).json()['revision']
        def action(**body):
            result=self.client.post(base+'/action',params=params,json={'operation_id':str(uuid.uuid4()),**body})
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()['status'],'committed')
            return result.json()['item']
        item=action(action='add',content='Synthetic UTC service.',applicability='Synthetic A only')
        first=dict(item)
        def identity(item):
            return {'memory_id':item['memory_id'],'expected_revision':item['revision'],
                    'expected_content_sha256':item['content_sha256']}
        item=action(action='replace',**identity(item),content='Synthetic UTC service corrected.',reason='Analyst correction')
        stale=self.client.post(base+'/action',params=params,json={'action':'replace',**identity(first),
            'content':'stale edit','operation_id':str(uuid.uuid4())})
        self.assertEqual(stale.status_code,409)
        item=action(action='exclude',**identity(item),reason='No longer applicable')
        self.assertEqual(item['status'],'retracted')
        self.assertEqual(self.client.get(base+'/snapshot',params=params).json()['entries'],[])
        item=action(action='restore',**identity(item),from_revision=1,reason='Restore explicit historical value')
        detail=self.client.get(base+'/'+item['memory_id']+'/history',params=params).json()
        self.assertEqual(detail['content'],'Synthetic UTC service.')
        self.assertEqual(detail['revision'],4)
        old=self.client.get(base+'/'+item['memory_id'],params={**params,'revision':1}).json()
        self.assertTrue(old['historical_context'])
        updates=self.client.get(base+'/changes',params={**params,'since_revision':initial}).json()
        self.assertEqual(len(updates['changes']),4)
        self.assertTrue(all(x['status']=='committed' and x['durable'] for x in updates['changes']))

    def test_legacy_mapping_preserves_archive_ids_bodies_and_missing_source(self):
        folder=self.root/'legacy-intake/cases/original'
        (folder/'evidence').mkdir(parents=True)
        (folder/'evidence/log').write_text('Synthetic pre-migration original\n')
        manifest=folder/'case.json'
        library_path=self.root/'legacy-library.sqlite3'
        manifest.write_text(json.dumps({'case_id':'original','evidence_root':str(folder/'evidence'),
            'output_root':str(folder/'results'),'library_path':str(library_path),'synthetic':True}))
        case=Case(manifest)
        source=json.loads(case.invoke('forsic_read',{'path':'log'}))['evidence_id']
        json.loads(case.invoke('forsic_note',{'action':'save','question':'Legacy question','answer':'Legacy answer','evidence_ids':[source]}))
        saved=CaseLibrary(case).save({})
        before=library_path.read_bytes();manifest_before=manifest.read_bytes()
        legacy=self.registry.bootstrap_legacy(intake_root=self.root/'legacy-intake',library_store=library_path)
        with bind_scope(legacy):
            library=CaseLibrary()
            detail=library.get(saved)
            self.assertEqual(detail['archive_id'],saved['archive_id'])
            self.assertEqual(detail['revision'],saved['revision'])
            self.assertEqual(detail['source_state'],'unchanged')
            case.db.rename(case.db.with_suffix('.preserved'))
            self.assertEqual(library.get(saved)['source_state'],'unavailable')
            original=library.get({'action':'source',**saved,'source_id':source})
            self.assertEqual(original['source']['data']['lines'][0]['text'],'Synthetic pre-migration original')
        self.assertEqual(library_path.read_bytes(),before)
        self.assertEqual(manifest.read_bytes(),manifest_before)


if __name__ == '__main__': unittest.main()
