"""File hub contracts and native versioned dispatch, with synthetic inputs only."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from forsic_plugin import extension_hub as hub


def archive(directory, destination, version=None, extra=None):
    manifest = json.loads((directory / 'manifest.json').read_text())
    if version: manifest['version'] = version
    if extra: extra(manifest)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as out:
        out.writestr('manifest.json', json.dumps(manifest))
        for name in manifest['files']: out.write(directory / name, name)
    return destination


class ExtensionHubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.example = Path(__file__).parent / 'examples/extensions/tool'
        self.bundle = archive(self.example, self.root / 'tool.zip')

    def test_import_export_idempotency_conflict_and_exact_payloads(self):
        first = hub.import_pack(self.bundle, self.home)
        self.assertFalse(first['idempotent'])
        self.assertTrue(hub.import_pack(self.bundle, self.home)['idempotent'])
        exported = self.root / 'export.zip'
        hub.export_pack('local.tool-example', '1.0.0', exported, self.home)
        second = hub.import_pack(exported, self.root / 'other-home')
        self.assertEqual(second['sha256'], first['sha256'])
        self.assertEqual(second['manifest'], first['manifest'])
        altered = archive(self.example, self.root / 'changed.zip', extra=lambda m: m.update(description='changed'))
        with self.assertRaisesRegex(ValueError, 'different content'): hub.import_pack(altered, self.home)
        self.assertEqual(hub.list_packs(self.home)[0]['sha256'], first['sha256'])

    def test_selection_cas_operation_identity_disable_and_rollback(self):
        first = hub.import_pack(self.bundle, self.home)
        two = archive(self.example, self.root / 'v2.zip', '2.0.0')
        second = hub.import_pack(two, self.home)
        def select(record, enabled=True):
            return {'packs': {'local.tool-example': {'version': record['manifest']['version'], 'sha256': record['sha256'],
                    'enabled': enabled, 'tools': {'count-lines': True}, 'skills': {}}}}
        initial = hub.selection_snapshot(self.home)
        saved = hub.save_selection(select(first), initial['revision'], 'one', self.home)
        self.assertEqual(hub.save_selection(select(first), initial['revision'], 'one', self.home), saved)
        with self.assertRaisesRegex(ValueError, 'operation_id'): hub.save_selection(select(second), saved['revision'], 'one', self.home)
        with self.assertRaisesRegex(ValueError, 'conflict'): hub.save_selection(select(second), initial['revision'], 'two', self.home)
        updated = hub.save_selection(select(second), saved['revision'], 'two', self.home)
        rolled = hub.save_selection(select(first, False), updated['revision'], 'rollback', self.home)
        self.assertFalse(rolled['selection']['packs']['local.tool-example']['enabled'])
        with self.assertRaisesRegex(ValueError, 'referenced'): hub.delete_pack('local.tool-example', '1.0.0', self.home)
        with self.assertRaisesRegex(ValueError, 'referenced'): hub.delete_pack('local.tool-example', '2.0.0', self.home, references=[select(second)])
        self.assertTrue(hub.delete_pack('local.tool-example', '2.0.0', self.home)['deleted'])

    def test_reject_traversal_links_normalized_collisions_and_bombs_before_registration(self):
        cases = [('traversal', '../outside', b'x', 0), ('absolute', '/tmp/outside', b'x', 0),
                 ('backslash', 'a\\b', b'x', 0), ('link', 'link', b'../../outside', stat.S_IFLNK | 0o777),
                 ('special', 'fifo', b'', stat.S_IFIFO | 0o600)]
        for label, name, data, mode in cases:
            bad = self.root / (label + '.zip')
            with zipfile.ZipFile(bad, 'w') as out:
                entry = zipfile.ZipInfo(name); entry.external_attr = mode << 16
                out.writestr(entry, data)
            with self.subTest(label=label), self.assertRaises(ValueError): hub.import_pack(bad, self.home)
        for names in [('A.txt', 'a.txt'), ('é.txt', 'e\u0301.txt'), ('same', 'same'), ('parent', 'parent/child')]:
            bad = self.root / 'collision.zip'
            with zipfile.ZipFile(bad, 'w') as out:
                for name in names: out.writestr(name, 'x')
            with self.assertRaises(ValueError): hub.import_pack(bad, self.home)
        bomb = self.root / 'bomb.zip'
        with zipfile.ZipFile(bomb, 'w', zipfile.ZIP_DEFLATED) as out: out.writestr('bomb', b'0' * (2*1024*1024))
        with self.assertRaisesRegex(ValueError, 'expansion'): hub.import_pack(bomb, self.home)
        self.assertFalse((self.home / 'forsic/extensions/packs').exists())
        self.assertFalse((self.root / 'outside').exists())

    def test_tampered_installed_payload_and_unlisted_files_rejected(self):
        record = hub.import_pack(self.bundle, self.home)
        path = Path(record['path']) / 'adapter/count_lines.py'
        path.write_text('raise AssertionError("must not execute on import")')
        with self.assertRaisesRegex(ValueError, 'checksum'): hub.load_pack('local.tool-example', '1.0.0', self.home)
        bad = self.root / 'extra.zip'
        bad.write_bytes(self.bundle.read_bytes())
        with zipfile.ZipFile(bad, 'a') as out: out.writestr('unexpected.py', 'raise AssertionError')
        with self.assertRaisesRegex(ValueError, 'every payload'): hub.import_pack(bad, self.home)

    def test_platform_api_remote_schema_and_payload_preview_checks(self):
        mutations = [lambda m: m['compatibility'].update(extension_api=2),
                     lambda m: m['compatibility'].update(platforms=['windows/amd64']),
                     lambda m: m['tools'][0]['parameters'].update({'$ref': 'https://example.invalid/schema'}),
                     lambda m: m['tools'][0].update(network='host')]
        for index, mutation in enumerate(mutations):
            bad = archive(self.example, self.root / (str(index) + '.zip'), extra=mutation)
            with self.assertRaises(ValueError): hub.import_pack(bad, self.home)
        with self.assertRaisesRegex(ValueError, 'changed after preview'):
            hub.import_pack(self.bundle, self.home, expected_sha256='0' * 64)
        self.assertFalse(hub.list_packs(self.home))

    def test_declared_dependencies_require_measured_exact_versions_and_interpreter(self):
        from forsic_plugin.extension_runtime import dependency_status
        pack = hub.preview_pack(self.bundle)
        pack['manifest']['dependencies'] = [{'method':'apt','name':'jq','version':'1.7-1'},
            {'method':'python','name':'example_pkg','version':'2.0','sha256':['a'*64]}]
        environment={'plan':{'installed':{'apt':{'jq':'1.7-1'},'python':{'example-pkg':'1.0'},'isolated_python':{'example-pkg':'2.0'}}}}
        tool=pack['manifest']['tools'][0]
        self.assertFalse(dependency_status(pack,environment=environment,tool=tool)['available'])
        isolated=deepcopy(tool);isolated['argv'][0]='/opt/forsic-deps/bin/python'
        self.assertTrue(dependency_status(pack,environment=environment,tool=isolated)['available'])
        environment['plan']['installed']['apt']['jq']='1.6-1'
        missing=dependency_status(pack,environment=environment,tool=isolated)
        self.assertFalse(missing['available']);self.assertEqual(missing['missing'][0]['actual'],'1.6-1')
        for dependencies in ([{'method':'shell','command':'anything'}], [{'method':'python','name':'x','version':'1','sha256':[]}],
                             [{'method':'apt','name':'jq','version':'1'},{'method':'apt','name':'jq','version':'2'}]):
            with self.assertRaises(ValueError): hub.validate_dependencies(dependencies)

    def test_native_names_do_not_collide_when_logical_prefixes_are_identical(self):
        from forsic_plugin.extension_runtime import runtime_name
        pack = hub.preview_pack(self.bundle)
        first = runtime_name(pack, {'name':'a-very-long-identical-prefix-one'})
        second = runtime_name(pack, {'name':'a-very-long-identical-prefix-two'})
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 64)
        self.assertLessEqual(len(second), 64)

    def test_argv_values_are_schema_checked_and_shell_quoted(self):
        from forsic_plugin.extension_runtime import argv_for
        import shlex
        tool = hub.preview_pack(self.bundle)['manifest']['tools'][0]
        malicious = "/evidence/a ' $(touch /tmp/unwanted);\nfile.txt"
        args = argv_for(tool, {'path': malicious})
        self.assertEqual(shlex.split(shlex.join(args))[-1], malicious)
        with self.assertRaises(Exception): argv_for(tool, {'path': 12})
        with self.assertRaises(Exception): argv_for(tool, {'path': 'x', 'unexpected': True})


class ExtensionNativeTests(unittest.TestCase):
    def setUp(self):
        from hermes_cli import plugins
        from hermes_cli.config import save_config
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        docker_root = Path(__file__).parent / 'artifacts' if os.environ.get('FORSIC_TEST_DOCKER_ENVIRONMENTS') == '1' else None
        if docker_root is not None: docker_root.mkdir(exist_ok=True)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='hub-native-', dir=docker_root)))
        self.home = self.root / 'home'; self.home.mkdir()
        token = set_hermes_home_override(self.home)
        self.addCleanup(reset_hermes_home_override, token)
        self.stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': str(self.root / 'host')}))
        self.stack.enter_context(patch.object(plugins, '_plugin_manager', None))
        self.stack.enter_context(patch.object(plugins, '_plugin_managers_by_home', {}))
        self.stack.enter_context(patch.object(plugins, 'discover_plugins'))
        self.stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('offline')))
        example = Path(__file__).parent / 'examples/extensions/tool'
        self.one = hub.import_pack(archive(example, self.root / 'one.zip'), self.home)
        def v2(m):
            m['tools'][0]['parameters']['properties']['path']['minLength'] = 2
        self.two = hub.import_pack(archive(example, self.root / 'two.zip', '2.0.0', v2), self.home)
        skilldir = Path(__file__).parent / 'examples/extensions/skill'
        self.skill = hub.import_pack(archive(skilldir, self.root / 'skill.zip'), self.home)
        self.selections = {}
        for sid, pack in [('case-a', self.one), ('case-b', self.two)]:
            self.selections[sid] = {'packs': {p['manifest']['id']: {'version':p['manifest']['version'], 'sha256':p['sha256'],
                'enabled':True, 'tools':{}, 'skills':{}} for p in (pack, self.skill)}}
        save_config({'plugins': {'enabled':['forsic'], 'entries': {'forsic': {'settings': {'intake_root':str(self.home / 'intake')}}}}})
        self.manager = plugins.get_plugin_manager()
        manifest = plugins.parse_manifest_file(Path(__file__).parent / 'forsic_plugin/plugin.yaml', Path(__file__).parent / 'forsic_plugin', 'user', '')
        from forsic_plugin.evidence import Case
        self.cases = {}
        for sid in self.selections:
            evidence = self.root / sid; evidence.mkdir(); (evidence/'sample.txt').write_text('alpha\nbeta\n')
            config = self.root / (sid + '.json');config.write_text(json.dumps({'case_id':sid,'evidence_root':str(evidence),'output_root':str(self.root/(sid+'-out'))}))
            self.cases[sid] = Case(config)
        self.stack.enter_context(patch('forsic_plugin.intake.Intake.case', side_effect=lambda sid:self.cases.get(sid)))
        def snapshot(_, case):
            sid = next(s for s,c in self.cases.items() if c is case)
            return {'extensions':deepcopy(self.selections[sid]), 'environment_id':'synthetic-env'}
        from forsic_plugin.environments import EnvironmentManager
        self.original_snapshot = EnvironmentManager.execution_snapshot
        self.original_selection = EnvironmentManager.extension_selection
        self.stack.enter_context(patch.object(EnvironmentManager, 'extension_selection', lambda manager, case: snapshot(manager,case)['extensions']))
        self.stack.enter_context(patch.object(EnvironmentManager, 'execution_snapshot', snapshot))
        contexts = {sid: {'status':'ready','requested_session_id':sid,'conversation_id':sid,'case_id':case.config['case_id']}
                    for sid, case in self.cases.items()}
        self.stack.enter_context(patch('forsic_plugin.investigation_context.native_goal_binding', side_effect=lambda sid,**kw:contexts[sid]))
        from forsic_plugin import register
        register(plugins.PluginContext(manifest,self.manager))
        self.addCleanup(self.manager.unload,'forsic')

    def menu(self,sid):
        from model_tools import get_tool_definitions
        return {v['function']['name']:v['function'] for v in get_tool_definitions(enabled_toolsets=['forsic'], quiet_mode=True,
            skip_tool_search_assembly=True, session_id=sid)}

    def test_same_profile_case_v1_v2_v1_menu_and_actual_argv_execution(self):
        from tools.registry import registry
        from forsic_plugin.extension_runtime import runtime_name
        first, second = (runtime_name(p,p['manifest']['tools'][0]) for p in (self.one,self.two))
        for sid, yes, no in [('case-a',first,second),('case-b',second,first),('case-a',first,second)]:
            menu=self.menu(sid);self.assertIn(yes,menu);self.assertNotIn(no,menu)
        self.assertNotIn('minLength',self.menu('case-a')[first]['parameters']['properties']['path'])
        self.assertEqual(self.menu('case-b')[second]['parameters']['properties']['path']['minLength'],2)
        # Execute the real adapter process against only its synthetic input. The
        # Docker seam is replaced; native dispatch/admission/ledger remain real.
        contexts = {}
        for sid,case in self.cases.items(): contexts[sid]={'status':'ready','requested_session_id':sid,'conversation_id':sid,'case_id':case.config['case_id']}
        def run(case,sid,argv,pack_root,timeout_seconds,native_execution=None,extension=None,**kwargs):
            actual=[sys.executable if i==0 and a=='python' else a.replace('/extension/',pack_root+'/').replace('/evidence/',str(case.root)+'/') for i,a in enumerate(argv)]
            process=subprocess.run(actual,capture_output=True,timeout=timeout_seconds,check=False)
            return {'stdout':process.stdout.decode(),'stderr':process.stderr.decode(),'execution':{'state':'returned','exit_code':process.returncode,'termination_confirmed':True},'extension':extension}
        self.stack.enter_context(patch('forsic_plugin.shell_workspace.invoke_extension', side_effect=run))
        for sid,name,version in [('case-a',first,'1.0.0'),('case-b',second,'2.0.0')]:
            result=json.loads(registry.dispatch(name,{'path':'/extension/fixtures/sample.txt'},session_id=sid))
            self.assertNotIn('error',result,result);self.assertEqual(json.loads(result['stdout']),{'lines':2,'bytes':11})
            self.assertEqual(result['extension']['version'],version)
            self.assertTrue(result['evidence_id']);self.assertTrue(result['started_id'])
            with self.cases[sid].connect() as db:
                start=json.loads(db.execute("SELECT data FROM events WHERE kind='tool_start'").fetchone()[0])
                self.assertEqual(start['extension']['version'],version)
            wrong=json.loads(registry.dispatch(second if sid=='case-a' else first,{'path':'x'},session_id=sid))
            self.assertEqual(wrong['error_code'],'capability_disabled')
        self.selections['case-a']['packs']['local.tool-example']['tools']['count-lines']=False
        self.assertNotIn(first,self.menu('case-a'))
        denied=json.loads(registry.dispatch(first,{'path':'x'},session_id='case-a'))
        self.assertEqual(denied['error_code'],'capability_disabled')
        self.assertIn(second,self.menu('case-b'))

    def test_safe_registration_refresh_discovers_new_version_without_rebinding_old_case(self):
        from forsic_plugin.extension_runtime import refresh_extensions, runtime_name
        example = Path(__file__).parent / 'examples/extensions/tool'
        third = hub.import_pack(archive(example, self.root / 'three.zip', '3.0.0'), self.home)
        name = runtime_name(third, third['manifest']['tools'][0])
        self.selections['case-b']['packs']['local.tool-example'].update(version='3.0.0', sha256=third['sha256'])
        self.assertNotIn(name, self.menu('case-b'))
        self.assertTrue(refresh_extensions(self.home)['registered'])
        self.assertIn(name, self.menu('case-b'))
        first = runtime_name(self.one, self.one['manifest']['tools'][0])
        self.assertIn(first, self.menu('case-a'))
        self.assertNotIn(name, self.menu('case-a'))

    def test_builder_scope_refuses_all_investigation_tools_and_case_context(self):
        from tools.registry import registry
        import sys
        fake = SimpleNamespace(is_builder_session=lambda sid: sid == 'builder',
                               builder_context=lambda sid: {'context': 'Evidence-free builder'} if sid == 'builder' else None)
        with patch.dict(sys.modules, {'forsic_plugin.repository_builder': fake}):
            self.assertFalse({name for name in self.menu('builder') if name.startswith('forsic_')})
            for name, args in [('forsic_intake', {'path': '/secret'}), ('forsic_read', {'path':'secret'}),
                               ('forsic_skill', {})]:
                result = json.loads(registry.dispatch(name, args, session_id='builder'))
                self.assertEqual(result['error_code'], 'capability_disabled')
            rendered = self.manager.render_system_prompt_sections({'session_id':'builder'})
            self.assertFalse(rendered)
            normal = self.manager.render_system_prompt_sections({'session_id':'case-a'})
            self.assertTrue(normal)

    def test_builder_compaction_identity_accepts_only_native_same_profile_continuations(self):
        import sqlite3
        from forsic_plugin.repository_builder import BuilderManager, _binding
        manager = BuilderManager(self.home)
        job_id = 'build-' + 'a' * 24
        directory = manager.directory(job_id); (directory/'session').mkdir(parents=True)
        value = {'id':job_id,'status':'running','stored_session_id':'builder-root','runtime_session_id':'builder-runtime',
                 'profile':'default','input_sha256':'0'*64,'draft_revision':0,'cancel_requested':False}
        (directory/'job.json').write_text(json.dumps(value))
        with sqlite3.connect(self.home/'state.db') as db:
            db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,cwd TEXT,profile_name TEXT,source TEXT,parent_session_id TEXT,end_reason TEXT,model_config TEXT)')
            def insert(sid,parent=None,end=None,config=None,cwd=None,profile=None):
                db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',(sid,str(cwd or directory/'session'),profile,'forsic-builder',parent,end,json.dumps(config or {})))
            insert('builder-root',end='compression')
            insert('builder-child','builder-root','compression')
            insert('builder-tip','builder-child')
            insert('builder-branch','builder-root',config={'_branched_from':'builder-root'})
            insert('builder-foreign','builder-root',profile='foreign')
            insert('builder-other-cwd','builder-root',cwd=self.root/'case-a')
        for sid in ('builder-root','builder-child','builder-tip'):
            self.assertIsNotNone(_binding(sid,self.home),sid)
        for sid in ('builder-branch','builder-foreign','builder-other-cwd','unrelated'):
            self.assertIsNone(_binding(sid,self.home),sid)
        self.assertFalse({name for name in self.menu('builder-tip') if name.startswith('forsic_') and name != 'forsic_extension_builder'})
        self.assertIn('forsic_extension_builder',self.menu('builder-tip'))
        from model_tools import handle_function_call
        described = json.loads(handle_function_call('tool_describe', {'names':['forsic_extension_builder']},
            session_id='builder-tip', enabled_toolsets=['forsic']))
        self.assertIn('forsic_extension_builder', json.dumps(described))
        self.assertIn('inspect', json.dumps(described))
        blocked = json.loads(handle_function_call('tool_describe', {'names':['forsic_read']},
            session_id='builder-tip', enabled_toolsets=['forsic']))
        self.assertNotIn('Read evidence text with exact', json.dumps(blocked))
        from hermes_cli.plugins import invoke_hook
        projections=invoke_hook('post_context_compaction',session_id='builder-tip',max_bytes=2000)
        builder=next(item for item in projections if item.get('provider')=='forsic-builder')
        self.assertEqual(builder['status'],'ready')
        self.assertIn(job_id,builder['context'])

    @unittest.skipUnless(os.environ.get('FORSIC_TEST_DOCKER_ENVIRONMENTS') == '1', 'Opt-in native Docker integration with existing local images')
    def test_real_native_adapter_records_actual_container_output_and_preserves_original(self):
        from forsic_plugin.environments import EnvironmentManager
        from forsic_plugin.extension_runtime import runtime_name
        from tools.registry import registry
        self.stack.enter_context(patch.dict(os.environ, {'FORSIC_MANAGEMENT_ROOT': str(self.home/'forsic-management')}))
        self.stack.enter_context(patch.object(EnvironmentManager, 'execution_snapshot', self.original_snapshot))
        self.stack.enter_context(patch.object(EnvironmentManager, 'extension_selection', self.original_selection))
        manager = EnvironmentManager()
        registered = manager.register(operation_id='native-hub-environment', background=False)
        self.assertEqual(registered['state'], 'succeeded', registered)
        environment_id = registered['result']['environment_id']
        manager.set_default(environment_id, expected_revision=0, operation_id='native-hub-default')
        case = self.cases['case-a']
        manager.select_case(case, environment_id, extensions=self.selections['case-a'], expected_revision=0,
                            operation_id='native-hub-case', sessions_safe=lambda: True)
        original = (case.root/'sample.txt').read_bytes()
        name = runtime_name(self.one, self.one['manifest']['tools'][0])
        result = json.loads(registry.dispatch(name, {'path':'/evidence/sample.txt'}, session_id='case-a',
                            tool_call_id='call-native-hub', turn_id='turn-native-hub'))
        self.assertNotIn('error', result, result)
        self.assertEqual(result['parsed_output'], {'lines':2, 'bytes':11})
        self.assertEqual(result['extension']['sha256'], self.one['sha256'])
        self.assertEqual(result['execution_policy']['environment']['environment_id'], environment_id)
        self.assertEqual(result['execution']['exit_code'], 0)
        self.assertTrue(result['execution']['termination_confirmed'])
        self.assertEqual((case.root/'sample.txt').read_bytes(), original)
        with case.connect() as db:
            starts = [json.loads(row[0]) for row in db.execute("SELECT data FROM events WHERE kind='tool_start'")]
        self.assertEqual(starts[-1]['native_call']['tool_call_id'], 'call-native-hub')

    def test_native_skill_read_and_reference_disable_are_read_only(self):
        from tools.registry import registry
        from forsic_plugin.extension_runtime import runtime_name
        name=runtime_name(self.skill,self.skill['manifest']['skills'][0])
        with patch('tools.skills_tool_plugin._preprocess_skill',side_effect=AssertionError('read-only')):
            catalog=json.loads(registry.dispatch('forsic_skill',{},session_id='case-a'))
            self.assertIn(name,{item['name'] for item in catalog['skills']})
            body=json.loads(registry.dispatch('forsic_skill',{'name':name},session_id='case-a'))
            self.assertTrue(body['success'],body)
            reference=json.loads(registry.dispatch('forsic_skill',{'name':name,'file_path':'references/scope.md'},session_id='case-a'))
            self.assertTrue(reference['success'],reference)
            self.assertIn('synthetic sample',reference['content'])
        self.selections['case-a']['packs']['local.skill-example']['skills']['text-review']=False
        self.assertNotIn(name,self.menu('case-a')['forsic_skill']['parameters']['properties']['name']['enum'])
        for args in ({'name':name},{'name':name,'file_path':'references/scope.md'}):
            result=json.loads(registry.dispatch('forsic_skill',args,session_id='case-a'))
            self.assertEqual(result['error_code'],'capability_disabled')
        self.assertIn(name,self.menu('case-b')['forsic_skill']['parameters']['properties']['name']['enum'])


if __name__=='__main__': unittest.main()
