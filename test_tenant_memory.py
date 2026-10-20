"""Real native MEMORY writes with temporary tenant registry/profile roots only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from forsic_plugin.tenants import TenantRegistry, bind_scope
from forsic_plugin.tenant_memory import MemoryService, native_policy, trusted_ui_source, memory_notice
from tools.memory_tool_store import MemoryStore
from tools.memory_tool import memory_tool, apply_memory_pending
from hermes_state import SessionDB


class TenantMemoryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': str(self.root)}))
        (self.root/'config.yaml').write_text(json.dumps({'memory': {'memory_enabled':False}, 'plugins': {'enabled': []}}))
        self.registry = TenantRegistry(self.root)
        self.legacy = self.registry.bootstrap_legacy(intake_root=self.root/'legacy-intake', library_store=self.root/'legacy-library.sqlite3')
        self.a = self.registry.create('Synthetic A', memory_mode='confirmed_auto', operation_id=str(uuid.uuid4()))
        self.b = self.registry.create('Synthetic B', memory_mode='confirmed_auto', operation_id=str(uuid.uuid4()))
        self.service = MemoryService(self.a)

    def write(self, content='ITAM is the inventory agent cron.', **extra):
        with bind_scope(self.a):
            return self.service.mutate({'action':'add','operation_id':str(uuid.uuid4()),'content':content,**extra}, source=trusted_ui_source(self.a))

    def change(self, result, action, **extra):
        item=result['item']
        with bind_scope(self.a):
            return self.service.mutate({'action':action,'operation_id':str(uuid.uuid4()),'memory_id':item['memory_id'],
                'expected_revision':item['revision'],'expected_content_sha256':item['content_sha256'],**extra},source=trusted_ui_source(self.a))

    def native(self, text='ITAM is the inventory agent cron.', *, role='user', basis='direct_confirmation', request=None):
        with bind_scope(self.a):
            if not hasattr(self, '_native_db'):
                self._native_db=SessionDB(Path(self.a['profile_home'])/'state.db')
                self.addCleanup(self._native_db.close)
            db=self._native_db
            db.create_session('memory-native','tui',profile_name=self.a['profile_name'])
            mid=db.append_message('memory-native',role,text)
            store=MemoryStore(owner_policy=native_policy(self.a),session_id='memory-native')
            store.load_from_disk()
            payload={'action':'add','target':'memory','operation_id':str(uuid.uuid4()),'content':'ITAM is the inventory agent cron.',
                'source_message_id':mid,'source_excerpt':text,'basis_kind':basis,**(request or {})}
            ctx={'session_id':'memory-native','tool_call_id':'native-call-'+str(mid),'turn_id':'native-turn',
                 'agent':SimpleNamespace(_session_db=db,side_agent=False)}
            return json.loads(memory_tool(store=store,owner_request=payload,native_context=ctx)), store, payload, ctx

    def test_T03_same_tenant_fresh_store_snapshot_and_T04_other_tenant(self):
        result=self.write();self.assertTrue(result['durable'])
        with bind_scope(self.a):
            first=MemoryStore(owner_policy=native_policy(self.a),session_id='one');first.load_from_disk()
            second=MemoryStore(owner_policy=native_policy(self.a),session_id='two');second.load_from_disk()
            self.assertEqual(first.format_for_system_prompt('memory'),second.format_for_system_prompt('memory'))
            block=first.format_for_system_prompt('memory');self.assertIn(result['item']['memory_id'],block)
            self.assertIn('unknown',block);self.assertIsNone(first.format_for_system_prompt('user'))
        with bind_scope(self.b):
            self.assertFalse(MemoryService(self.b).snapshot()['entries'])
            with self.assertRaises((ValueError,PermissionError)):first.add('memory','foreign write')
            with self.assertRaises((ValueError,PermissionError)):self.service.snapshot()

    def test_T09_off_manual_auto_and_no_passive_file_creation(self):
        with bind_scope(self.a):
            self.assertEqual(self.service.list()['items'],[])
            self.assertFalse(self.service.root.exists())
        result,_,_,_=self.native();self.assertTrue(result['success'],result)
        self.registry.update_policy(self.a['tenant_id'],'off',expected_revision=1,operation_id=str(uuid.uuid4()))
        with bind_scope(self.a),patch.object(self.service,'_raw',side_effect=AssertionError('Off must not load body')):
            self.assertEqual(self.service.snapshot()['entries'],[])
        with bind_scope(self.a),self.assertRaises(ValueError):self.write('new')

    def test_T10_verified_native_user_and_T11_forged_role_or_uncertain(self):
        good,_,_,_=self.native();self.assertEqual(good['item']['source']['source_type'],'user_attested')
        bad,_,_,_=self.native(role='tool');self.assertFalse(bad['success']);self.assertIn('direct-user',bad['error'])
        bad,_,_,_=self.native(text='Possibly so.',basis='unconfirmed');self.assertFalse(bad['success'])
        bad,_,_,_=self.native(text='```\nITAM is the inventory agent cron.\n```', request={'source_excerpt':'ITAM is the inventory agent cron.'});self.assertFalse(bad['success'])
        bad,_,_,_=self.native(text='Yes');self.assertFalse(bad['success']);self.assertIn('question',bad['error'])

    def test_server_resolves_omitted_source_and_rejects_explicit_paraphrase_or_runtime_nudge(self):
        good,store,_,_=self.native(request={'source_excerpt':None})
        self.assertTrue(good['success']);self.assertEqual(good['item']['source']['excerpt'],'ITAM is the inventory agent cron.')
        bad,_,_,_=self.native(request={'source_excerpt':'Paraphrased operational fact'})
        self.assertFalse(bad['success']);self.assertIn('literal',bad['error'])
        from agent.context_compressor import MAX_ITERATIONS_SUMMARY_REQUEST
        bad,_,_,_=self.native(text=MAX_ITERATIONS_SUMMARY_REQUEST,request={'source_excerpt':None})
        self.assertFalse(bad['success']);self.assertIn('Generated',bad['error'])
        with bind_scope(self.a):
            invalid=json.loads(memory_tool(store=store,owner_request={'action':'get','target':'memory'}))
            self.assertEqual(invalid['status'],'invalid_arguments')
            self.assertEqual(invalid['next_call']['arguments'],{'target':'memory','action':'list'})

    def test_scheduled_native_user_role_is_not_direct_operator_attestation(self):
        _,store,payload,ctx=self.native()
        with bind_scope(self.a):
            db=ctx['agent']._session_db
            db.create_session('scheduled','cron',profile_name=self.a['profile_name'])
            message=db.append_message('scheduled','user','ITAM is the scheduled inventory task.')
            result=json.loads(memory_tool(store=store,owner_request={**payload,'source_message_id':message,'source_excerpt':None},native_context={**ctx,'session_id':'scheduled'}))
            self.assertFalse(result['success']);self.assertIn('Scheduled',result['error'])

    def test_T12_unknown_validity_and_explicit_timezone(self):
        r=self.write();self.assertIsNone(r['item']['valid_from']);self.assertEqual(r['item']['applicability'],'unknown')
        self.assertTrue(r['item']['asserted_at'].endswith('+00:00'))
        with self.assertRaises(ValueError):self.write('Other',valid_from='2026-10-01')
        r=self.write('Scoped',applicability='development hosts only',valid_from='2026-10-01T00:00:00+09:00')
        with bind_scope(self.a):self.assertIn('development hosts only','\n'.join(self.service.snapshot()['entries']))
        with self.assertRaises(ValueError):
            self.write('Backward interval',valid_from='2026-10-02T00:00:00+00:00',valid_until='2026-10-01T00:00:00+00:00')

    def test_native_compression_source_and_branch_denial(self):
        result,store,payload,ctx=self.native();self.assertTrue(result['success'])
        with bind_scope(self.a):
            db=ctx['agent']._session_db
            db.end_session('memory-native','compression')
            db.create_session('continuation','tui',profile_name=self.a['profile_name'],parent_session_id='memory-native')
            follow={**payload,'operation_id':str(uuid.uuid4()),'content':'ITAM is inventory on development hosts.'}
            continued={**ctx,'session_id':'continuation','tool_call_id':'continued-call'}
            self.assertTrue(json.loads(memory_tool(store=store,owner_request=follow,native_context=continued))['success'])
            db.create_session('branch','tui',profile_name=self.a['profile_name'],parent_session_id='memory-native',model_config={'_branched_from':'memory-native'})
            denied={**continued,'session_id':'branch','tool_call_id':'branch-call'}
            self.assertFalse(json.loads(memory_tool(store=store,owner_request={**follow,'operation_id':str(uuid.uuid4())},native_context=denied))['success'])

    def test_initial_native_menu_adds_only_memory_and_skips_builder(self):
        from agent.agent_init import _load_tools
        schema={'type':'function','function':{'name':'memory','parameters':{}}}
        for skip,expected in ((False,['forsic','memory']),(True,['forsic'])):
            agent=SimpleNamespace(session_id='synthetic-menu',quiet_mode=True,enabled_toolsets=['forsic'],disabled_toolsets=[],side_agent=False,_skip_memory=skip)
            with (bind_scope(self.a),patch('hermes_cli.plugins.discover_plugins'),
                  patch('tools.memory_tool.resolve_memory_policy',return_value=native_policy(self.a)),
                  patch('model_tools.get_tool_definitions',return_value=[schema]) as definitions,
                  patch('agent.oneshot_footprint.prune_oneshot_tools',side_effect=lambda x:x),
                  patch('tools.connectors.turn.side_agent_tool_drops',return_value=set())):
                _load_tools(agent,['forsic'],[])
                self.assertEqual(definitions.call_args.kwargs['enabled_toolsets'],expected)
                self.assertEqual(agent.enabled_toolsets,expected)

    def test_compaction_notice_uses_owned_ancestor_snapshot_without_body_reload(self):
        result,store,_,ctx=self.native()
        with bind_scope(self.a):
            # The original store loaded before the first save. Explicit native
            # reload here establishes revision1 as the frozen pre-compaction snapshot.
            store.load_from_disk();frozen=store.format_for_system_prompt('memory')
            db=ctx['agent']._session_db
            db.end_session('memory-native','compression')
            db.create_session('rotated','tui',profile_name=self.a['profile_name'],parent_session_id='memory-native')
        self.change(result,'exclude')
        self.registry.update_policy(self.a['tenant_id'],'off',1,str(uuid.uuid4()))
        with bind_scope(self.a),patch.object(MemoryService,'_raw',side_effect=AssertionError('notice must not load body')):
            value=memory_notice(self.a,'rotated')
            self.assertEqual(value['loaded_snapshot_revision'],1);self.assertEqual(value['current_revision'],2)
            self.assertEqual(value['mode'],'off');self.assertEqual(value['changed'][0]['status'],'retracted')
            self.assertIsNone(memory_notice(self.a,'rotated'))
        self.assertEqual(store._system_prompt_snapshot['memory'],frozen)
        with bind_scope(self.b),self.assertRaises((LookupError,PermissionError)):
            memory_notice(self.b,'rotated')

    def test_restore_restores_historical_scope_and_validity_with_new_provenance(self):
        first=self.write(applicability='development only')
        second=self.change(first,'replace',content='Inventory agent on both environments.',applicability='development and production',valid_from='2026-10-01T00:00:00+00:00')
        excluded=self.change(second,'exclude')
        restored=self.change(excluded,'restore',from_revision=1)
        self.assertEqual(restored['item']['revision'],4)
        self.assertEqual(restored['item']['applicability'],'development only')
        self.assertIsNone(restored['item']['valid_from']);self.assertIsNone(restored['item']['valid_until'])
        self.assertNotEqual(restored['item']['asserted_at'],first['item']['asserted_at'])
        with bind_scope(self.a):
            self.assertEqual(self.service.get(restored['item']['memory_id'])['content'],'ITAM is the inventory agent cron.')

    def test_T14_native_pending_is_not_committed_and_replay_revalidates(self):
        with (patch('tools.write_approval.evaluate_gate',return_value=SimpleNamespace(allow=False,blocked=False,message='Review required')),
              patch('tools.write_approval.stage_write',return_value={'id':'pending-one'}) as staged):
            result,store,payload,ctx=self.native(basis='explicit_request')
        self.assertEqual(result['status'],'pending');self.assertFalse(result['success']);self.assertFalse(self.service.memory_path.exists())
        pending=staged.call_args.args[1]
        with bind_scope(self.a):
            replay=apply_memory_pending(pending,store);self.assertTrue(replay['success'],replay)
            rejected=apply_memory_pending({'action':'add','target':'memory','content':'bypass'},store);self.assertFalse(rejected['success'])

    def test_T15_duplicate_same_id_revision_and_stale_exact_target(self):
        r=self.write();dup=self.write();self.assertEqual(dup['item']['memory_id'],r['item']['memory_id']);self.assertEqual(dup['item']['revision'],1)
        changed=self.change(r,'replace',content='ITAM is inventory for development hosts only.')
        self.assertEqual(changed['item']['revision'],2)
        with self.assertRaises(ValueError):self.change(r,'replace',content='stale')
        with bind_scope(self.a):
            request={'action':'add','operation_id':'stable','content':'Unique A'};source=trusted_ui_source(self.a)
            one=self.service.mutate(request,source=source);self.assertEqual(self.service.mutate(request,source=source),one)
            with self.assertRaises(ValueError):self.service.mutate({**request,'content':'Different'},source=source)

    def test_T15_concurrent_instances_do_not_lose_updates(self):
        def run(i):
            with bind_scope(self.a):
                service=MemoryService(self.a)
                return service.mutate({'action':'add','operation_id':'parallel-'+str(i),'content':'Operating term '+str(i)},source=trusted_ui_source(self.a))
        with ThreadPoolExecutor(max_workers=4)as pool:out=list(pool.map(run,range(4)))
        self.assertTrue(all(x['success']for x in out))
        with bind_scope(self.a):self.assertEqual(self.service.list()['total'],4)

    def test_T16_prepare_failure_does_not_touch_native(self):
        import forsic_plugin.tenant_memory as module
        real=module._write
        def fail(path,value):
            if path.name=='prepared.json':raise OSError('synthetic prepare failure')
            return real(path,value)
        with patch.object(module,'_write',side_effect=fail),self.assertRaises(OSError):self.write()
        self.assertFalse(self.service.memory_path.exists())

    def test_T16_after_native_commit_failure_recovers_before_snapshot(self):
        import forsic_plugin.tenant_memory as module
        real=module._write
        def fail(path,value):
            if path.name=='metadata.json':raise OSError('synthetic sidecar failure')
            return real(path,value)
        with patch.object(module,'_write',side_effect=fail):result=self.write()
        self.assertFalse(result['success']);self.assertEqual(result['status'],'recovery_required');self.assertTrue(result['body_changed'])
        with bind_scope(self.a):
            self.assertEqual(self.service.snapshot()['entries'],[])
            store=MemoryStore(owner_policy=native_policy(self.a));store.load_from_disk()
            self.assertIn('ITAM',store.format_for_system_prompt('memory'))
            self.assertFalse(self.service.journal_path.exists())

    def test_T16_ambiguous_external_edit_not_injected_or_invented_commit(self):
        import forsic_plugin.tenant_memory as module
        real=module._write
        def fail(path,value):
            if path.name=='metadata.json':raise OSError('synthetic sidecar failure')
            return real(path,value)
        with patch.object(module,'_write',side_effect=fail):self.write()
        self.service.memory_path.write_text('unrelated external content')
        with bind_scope(self.a):
            with self.assertRaisesRegex(ValueError,'ambiguous'):self.service.recover()
            self.assertEqual(self.service.snapshot()['entries'],[])

    def test_T17_external_drift_preserves_other_verified_entries(self):
        one=self.write();two=self.write('Other known operating term.')
        raw=self.service.memory_path.read_text();self.service.memory_path.write_text(raw.replace('ITAM is the inventory agent cron.','External unknown text.'))
        with bind_scope(self.a):
            snap=self.service.snapshot();self.assertEqual(len(snap['entries']),1);self.assertIn('Other known',snap['entries'][0])
            self.assertEqual(self.service.get(one['item']['memory_id'])['synchronization'],'review_required')
        with self.assertRaisesRegex(ValueError,'outside'):self.write('Third')

    def test_T18_last_exclusion_restore_new_revision_and_frozen_notice(self):
        r=self.write()
        with bind_scope(self.a):
            store=MemoryStore(owner_policy=native_policy(self.a),session_id='frozen');store.load_from_disk();old=store.format_for_system_prompt('memory')
        excluded=self.change(r,'exclude',reason='No longer current')
        self.assertEqual(self.service.memory_path.read_text(),'')
        with bind_scope(self.a):
            self.assertEqual(store.format_for_system_prompt('memory'),old)
            self.assertEqual(self.service.snapshot()['entries'],[])
            listed=self.service.list()['items'];self.assertEqual(listed[0]['status'],'retracted')
            notice=memory_notice(self.a,'frozen');self.assertEqual(notice['loaded_snapshot_revision'],1);self.assertEqual(notice['current_revision'],2)
            store.load_from_disk();self.assertIsNone(store.format_for_system_prompt('memory'))
        restored=self.change(excluded,'restore',from_revision=1)
        self.assertEqual(restored['item']['revision'],3)
        with bind_scope(self.a):self.assertIn('r3',self.service.snapshot()['entries'][0])

    def test_T19_file_and_final_snapshot_budget_no_silent_consolidation(self):
        r=self.write('A'*1850)
        self.assertTrue(r['success'],r)
        too_large=self.write('B'*300);self.assertFalse(too_large['success']);self.assertIn('capacity',too_large)
        with bind_scope(self.a):
            store=MemoryStore(owner_policy=native_policy(self.a));store.load_from_disk()
            self.assertLessEqual(len(store.format_for_system_prompt('memory')),2200)
            self.assertEqual(self.service.list()['total'],1)

    def test_unframed_native_store_writes_and_user_target_cannot_bypass(self):
        with bind_scope(self.a):
            store=MemoryStore(owner_policy=native_policy(self.a));store.load_from_disk()
            self.assertFalse(store.add('memory','unframed')['success'])
            self.assertFalse(store.add('user','personal')['success'])
            self.assertIsNone(store.format_for_system_prompt('user'))


if __name__=='__main__':unittest.main()
