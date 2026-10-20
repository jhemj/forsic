"""Offline transport-boundary regression checks; no case execution or model calls."""
import asyncio
from contextlib import closing
import hashlib
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forsic_plugin.intake import Intake, write_json
from forsic_plugin.remote_policy import ForsicRemoteAccessMiddleware, ForsicRemotePolicy, require_bound_native_intake


class RemotePolicyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = {'remote_evidence_directory_only': True, 'intake_root': str(self.root/'intake')}
        self.policy = ForsicRemotePolicy(self.settings, self.root/'home')
        self.manifest = self.root/'intake/cases/synthetic/manifest.json'
        write_json(self.manifest, {'case_id': 'synthetic-case'})
        write_json(self.root/'intake'/ (hashlib.sha256(b'stored').hexdigest()+'.json'), {'manifest': str(self.manifest)})
        self.native = self.root/'home/state.db'
        self.native.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT, end_reason TEXT, source TEXT, model_config TEXT)')
            db.execute('INSERT INTO sessions VALUES (?,NULL,NULL,?,?)', ('stored', 'cli', '{}'))

    def native_session(self, session_id, parent=None, *, end_reason=None, source='cli', config=None):
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                       (session_id, parent, end_reason, source, json.dumps(config or {})))

    def scope(self, path, *, method='GET', peer='192.0.2.20', query=b''):
        return {'type':'http', 'path':path, 'method':method, 'client':(peer,123), 'query_string':query, 'headers':[]}

    async def http(self, scope, *, status=200):
        reached, sent = [], []
        async def app(scope, receive, send):
            reached.append(scope)
            await send({'type':'http.response.start','status':status,'headers':[]})
            await send({'type':'http.response.body','body':b'{"operator_data":"private"}'})
        async def send(message):
            sent.append(message)
        await ForsicRemoteAccessMiddleware(app, policy=self.policy)(scope, None, send)
        return reached, sent

    async def test_remote_admin_and_host_files_never_reach_handlers(self):
        for method, path in [('PUT','/api/config'),('GET','/api/config'),('GET','/api/fs/list'),
                             ('POST','/api/fs/write'),('GET','/api/files/read'),('POST','/api/actions'),
                             ('GET','/api/plugins/forsic/connections'),('PUT','/api/plugins/forsic/connections'),
                             ('POST','/api/plugins/forsic/connections/gti-check'),('GET','/api/status'),
                             ('GET','/api/media'),('POST','/api/sessions/import'),('POST','/api/profiles')]:
            with self.subTest(method=method,path=path):
                reached,sent = await self.http(self.scope(path,method=method))
                self.assertEqual(reached,[])
                self.assertEqual(sent[0]['status'],403)

    async def test_product_routes_and_bound_existing_case_reads_remain_available(self):
        for method,path in [('GET','/'),('GET','/forsic'),('GET','/assets/product.js'),
                            ('GET','/api/dashboard/plugins'),('GET','/fonts-terminal/JetBrainsMono-Regular.woff2'),('GET','/api/tools/toolsets'),
                            ('GET','/api/plugins/forsic/investigations'),('GET','/api/plugins/forsic/evidence-sources'),
                            ('GET','/api/plugins/forsic/investigation-view'),('GET','/api/plugins/forsic/report-bundles/bundle/report.docx'),
                            ('POST','/api/plugins/forsic/report-bundles/'+'a'*64+'/word'),
                            ('POST','/api/plugins/forsic/investigations'),('POST','/api/plugins/forsic/investigation-enrich'),('POST','/api/plugins/forsic/investigations/case/conversations'),
                            ('GET','/api/sessions/stored/live'),('GET','/api/sessions/stored/messages')]:
            with self.subTest(path=path):
                reached,sent=await self.http(self.scope(path,method=method,query=b'profile=default'))
                self.assertEqual(len(reached),1)
                self.assertEqual(sent[0]['status'],200)
        reached,sent=await self.http(self.scope('/api/sessions/unbound/messages'))
        self.assertEqual(reached,[])
        self.assertEqual(sent[0]['status'],403)

    async def test_actual_peer_and_fixed_startup_policy_ignore_forged_forwarding(self):
        self.settings['remote_evidence_directory_only']=False
        scope=self.scope('/api/config',method='PUT')
        scope['headers']=[(b'x-forwarded-for',b'127.0.0.1'),(b'host',b'localhost')]
        reached,sent=await self.http(scope)
        self.assertFalse(reached)
        self.assertEqual(sent[0]['status'],403)
        for peer in ('127.0.0.1','::1','::ffff:127.0.0.1'):
            reached,sent=await self.http(self.scope('/api/config',method='PUT',peer=peer))
            self.assertEqual(len(reached),1)
        self.policy=ForsicRemotePolicy({},self.root/'home')
        reached,_=await self.http(scope)
        self.assertEqual(len(reached),1)

    async def test_profile_projection_preserves_auth_failure_and_rejects_profile_switches(self):
        reached,sent=await self.http(self.scope('/api/profiles'),status=401)
        self.assertEqual(sent[0]['status'],401)
        reached,sent=await self.http(self.scope('/api/profiles'))
        self.assertEqual(json.loads(sent[1]['body']),{'profiles':[{'name':'default'}]})
        for query in (b'profile=other',b'profile=default&profile=other',b'profile=%6fther'):
            reached,sent=await self.http(self.scope('/api/plugins/forsic/investigations',query=query))
            self.assertFalse(reached)
            self.assertEqual(sent[0]['status'],403)

    async def test_remote_plugin_projection_loads_only_product_ui(self):
        sent=[]
        async def app(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':json.dumps([{'name':'forsic','version':'current'},{'name':'unrelated-admin'}]).encode()})
        async def send(message):sent.append(message)
        await ForsicRemoteAccessMiddleware(app,policy=self.policy)(self.scope('/api/dashboard/plugins'),None,send)
        self.assertEqual(json.loads(sent[1]['body']),[{'name':'forsic','version':'current'}])

    def test_unbound_native_intake_requires_picker_but_existing_binding_and_default_mode_remain(self):
        intake=Intake(self.root/'intake')
        with self.assertRaises(PermissionError):
            require_bound_native_intake(intake,'unbound',True)
        require_bound_native_intake(intake,'stored',True)
        require_bound_native_intake(intake,'unbound',False)

    def test_runtime_rpc_requires_real_binding_and_blocks_alternate_host_entrypoints(self):
        sessions={'runtime':{'session_key':'stored','profile_home':self.root/'home'},
                  'foreign':{'session_key':'stored','profile_home':self.root/'other'}}
        server=SimpleNamespace(_sessions=sessions,_sessions_lock=threading.Lock())
        with patch.dict(sys.modules,{'tui_gateway':SimpleNamespace(server=server)}):
            for method in ('session.activate','session.control.read','session.interrupt','session.events.since','approval.respond'):
                self.assertTrue(self.policy.rpc_allowed({'method':method,'params':{'session_id':'runtime','profile':'default'}}))
            self.assertTrue(self.policy.rpc_allowed({'method':'session.resume','params':{'session_id':'stored','profile':'default'}}))
            self.assertTrue(self.policy.rpc_allowed({'method':'prompt.submit','params':{'session_id':'runtime','text':'continue'}}))
            for method in ('config.set','config.get','cli.exec','file.attach','image.attach','pdf.attach','complete.path','session.create','session.branch_stored','session.cwd.set','browser.manage','tool.execute'):
                self.assertFalse(self.policy.rpc_allowed({'method':method,'params':{'session_id':'runtime'}}),method)
            for params in ({'session_id':'unbound','text':'start'},{'session_id':'foreign','text':'start'},
                           {'session_id':'runtime','profile':'other','text':'start'},
                           {'session_id':'runtime','text':'start','attachments':['/outside']}):
                self.assertFalse(self.policy.rpc_allowed({'method':'prompt.submit','params':params}))
            self.assertFalse(self.policy.rpc_allowed({'method':[], 'params':{}}))
            self.assertFalse(self.policy.rpc_allowed({'method':'prompt.submit','params':{'session_id':'runtime','text':[{'type':'file','path':'/outside'}]}}))
            self.assertFalse(self.policy.rpc_allowed({'method':'session.resume','params':{'session_id':'stored','cwd':'/outside'}}))
            self.assertFalse(self.policy.rpc_allowed({'method':'command.dispatch','params':{'session_id':'runtime','name':'config'}}))
            self.assertFalse(self.policy.rpc_allowed({'method':'session.control','params':{'session_id':'runtime','action':'goal.clear'}}))
            self.assertTrue(self.policy.rpc_allowed({'method':'session.control','params':{'session_id':'runtime','action':'goal.pause'}}))
            self.assertTrue(self.policy.rpc_allowed({'method':'session.control','params':{'session_id':'runtime','action':'goal.resume'}}))

    def test_binding_manifest_cannot_escape_the_managed_case_directory(self):
        self.native_session('escape')
        self.native_session('link')
        outside=self.root/'outside.json';write_json(outside,{'case_id':'other'})
        write_json(self.root/'intake'/(hashlib.sha256(b'escape').hexdigest()+'.json'),{'manifest':str(outside)})
        self.assertFalse(self.policy.bound('escape'))
        (self.root/'intake/cases/link.json').symlink_to(outside)
        write_json(self.root/'intake'/(hashlib.sha256(b'link').hexdigest()+'.json'),{'manifest':str(self.root/'intake/cases/link.json')})
        self.assertFalse(self.policy.bound('link'))

    def test_compression_descendant_inherits_existing_scope_for_http_and_native_rpc(self):
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute("UPDATE sessions SET end_reason='compression' WHERE id='stored'")
        self.native_session('compressed', 'stored', end_reason='compression')
        self.native_session('tip', 'compressed')
        self.assertTrue(self.policy.bound('tip'))
        self.assertTrue(self.policy.http_allowed(self.scope('/api/sessions/tip/messages')))
        self.assertTrue(self.policy.rpc_allowed({'method': 'session.resume', 'params': {'session_id': 'tip'}}))
        server = SimpleNamespace(_sessions={'runtime-tip': {'session_key': 'tip', 'profile_home': self.root/'home'}},
                                 _sessions_lock=threading.Lock())
        with patch.dict(sys.modules, {'tui_gateway': SimpleNamespace(server=server)}):
            self.assertTrue(self.policy.rpc_allowed({'method': 'session.activate', 'params': {'session_id': 'runtime-tip'}}))
            self.assertFalse(self.policy.rpc_allowed({'method': 'session.activate', 'params': {'session_id': 'runtime-tip', 'profile': 'other'}}))

    def test_inherited_scope_rejects_branch_reset_delegation_and_noncompression_edges(self):
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute("UPDATE sessions SET end_reason='compression' WHERE id='stored'")
        for key in ('_branched_from', '_delegate_from', '_reset_from'):
            self.native_session(key, 'stored', config={key: 'stored'})
            self.assertFalse(self.policy.bound(key))
        self.native_session('tool-child', 'stored', source='tool')
        self.assertFalse(self.policy.bound('tool-child'))
        self.native_session('ordinary-parent')
        self.native_session('ordinary-child', 'ordinary-parent')
        write_json(self.root/'intake'/(hashlib.sha256(b'ordinary-parent').hexdigest()+'.json'), {'manifest': str(self.manifest)})
        self.assertFalse(self.policy.bound('ordinary-child'))

    def test_missing_native_rows_orphaned_bindings_cycles_and_overlong_lineage_fail_closed(self):
        write_json(self.root/'intake'/(hashlib.sha256(b'orphan').hexdigest()+'.json'), {'manifest': str(self.manifest)})
        self.assertFalse(self.policy.bound('orphan'))
        self.native_session('missing-parent-child', 'does-not-exist')
        self.assertFalse(self.policy.bound('missing-parent-child'))
        self.native_session('cycle-one', 'cycle-two', end_reason='compression')
        self.native_session('cycle-two', 'cycle-one', end_reason='compression')
        self.assertFalse(self.policy.bound('cycle-one'))
        for index in range(129):
            self.native_session('long-'+str(index), 'long-'+str(index+1) if index < 128 else 'stored',
                                end_reason='compression')
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute("UPDATE sessions SET end_reason='compression' WHERE id='stored'")
        self.assertFalse(self.policy.bound('long-0'))

    async def test_websocket_rejects_terminal_and_filters_rpc_before_native_dispatch(self):
        received,sent=[],[]
        events=iter([{'type':'websocket.connect'},
                     {'type':'websocket.receive','text':json.dumps({'id':1,'method':'config.set','params':{}})},
                     {'type':'websocket.receive','text':json.dumps({'id':2,'method':'gateway.ping','params':{}})},
                     {'type':'websocket.disconnect'}])
        async def receive():return next(events)
        async def send(message):sent.append(message)
        async def app(scope,receive,send):
            await receive();await send({'type':'websocket.accept'})
            while True:
                event=await receive()
                if event['type']=='websocket.disconnect':break
                received.append(json.loads(event['text']))
        scope={**self.scope('/api/ws'),'type':'websocket'}
        await ForsicRemoteAccessMiddleware(app,policy=self.policy)(scope,receive,send)
        self.assertEqual([r['method'] for r in received],['gateway.ping'])
        self.assertEqual(json.loads(sent[1]['text'])['error']['code'],4030)
        received.clear();sent.clear();scope['path']='/api/pty'
        await ForsicRemoteAccessMiddleware(app,policy=self.policy)(scope,receive,send)
        self.assertFalse(received)
        self.assertEqual(sent[0]['type'],'websocket.close')


if __name__=='__main__':unittest.main()
