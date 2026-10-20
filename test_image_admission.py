"""Synthetic admission checks: invalid calls never open an image or start Docker."""
import builtins
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from forsic_plugin import image_reader
from forsic_plugin.report_driven.host import current, find
import test_efficiency_contracts as efficiency


class ImageAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.x=efficiency.EfficiencyContracts(); self.x.setUp(); self.addCleanup(self.x.doCleanups)

    def bad_calls(self):
        for action in ('read','read_bytes','stat','hash','archive_list','archive_read','search'):
            args={'path':'synthetic.e01','action':action,'member_index':0}
            yield args
            for value in ('',None,3,True):
                yield {**args,'file_path':value}
        for action in ('volumes','list','search'):
            for value in ('',None,3):
                yield {'path':value,'action':action,'text':'literal'}
        for value in (None,'',3,True):
            yield {'path':'synthetic.e01','action':'list','file_path':value}
        for value in (None,-1,True,1.5,'0'):
            yield {'path':'synthetic.e01','action':'archive_read','file_path':'archive.tar','member_index':value}
        yield {'path':'synthetic.e01','action':'archive_read','file_path':'archive.tar'}
        for value in ('',None,3,'two\nlines','x'*513):
            yield {'path':'synthetic.e01','action':'search','text':value}
        yield {'path':'synthetic.e01','action':'search'}
        yield {'path':'synthetic.e01','action':'invented'}

    def test_invalid_direct_calls_are_recorded_rejections_without_backend_or_state_progress(self):
        before=current(self.x.case)
        with patch.object(self.x.case,'image_files') as backend:
            for args in self.bad_calls():
                with self.subTest(args=args):
                    result=self.x.call('forsic_image_files',args)
                    self.assertEqual(result.get('error_code'),'invalid_arguments',result)
                    self.assertTrue(result['execution_rejected'])
                    self.assertFalse(result['backend_started'])
                    self.assertNotIn('outcome',result)
                    self.assertNotIn('lines',result)
                    self.assertEqual(len(result['schema_fingerprint']),64)
                    start=self.x.case.event(result['started_id'])['data']
                    self.assertTrue(start['execution_rejected']);self.assertFalse(start['backend_started'])
                    self.assertEqual(self.x.case.event(result['evidence_id'])['data']['error_code'],'invalid_arguments')
            backend.assert_not_called()
        after=current(self.x.case)
        for key in ('questions','missions','assessments'):
            self.assertEqual(after[key],before[key])

    def test_invalid_plans_and_legacy_plans_are_rejected_before_saving(self):
        before=current(self.x.case)
        for args in self.bad_calls():
            with self.subTest(args=args):
                payload={**self.x.plan(),'tool_name':'forsic_image_files','tool_arguments':args}
                result=self.x.call('forsic_mission',payload)
                self.assertIn('error',result)
                legacy=json.loads(self.x.case.invoke('forsic_reporting',{'action':'mission','payload':payload},'synthetic-session'))
                self.assertIn('error',legacy)
        self.assertEqual(current(self.x.case)['missions'],before['missions'])

    def test_root_defaults_exact_paths_and_unbound_exploration_remain_valid(self):
        calls=[{'action':'volumes'},{'action':'list'},{'action':'search','file_path':'/','text':'literal'},
               {'action':'stat','file_path':'/'},{'action':'read','file_path':' relative name '},
               {'action':'read_bytes','file_path':'body'}, {'action':'hash','file_path':'body'},
               {'action':'archive_list','file_path':'archive.tar'},
               {'action':'archive_read','file_path':'archive.tar','member_index':0}]
        with patch.object(self.x.case,'image_files',return_value={'synthetic_backend':True}) as backend:
            for args in calls:
                args={'path':'synthetic.e01',**args}; original=deepcopy(args)
                result=self.x.call('forsic_image_files',args)
                self.assertNotIn('error',result,result)
                self.assertNotIn('mission_context',result)
                self.assertEqual(backend.call_args.args[0],original)
                self.assertEqual(args,original)

    def test_supplied_empty_partial_or_wrong_type_mission_pair_never_becomes_unbound(self):
        values=('',None,False,0,[],{})
        pairs=[{'mission_id':v,'mission_version':v} for v in values]
        pairs += [{'mission_id':'M-example'},{'mission_version':'version'}]
        pairs += [{'mission_id':'M-example','mission_version':v} for v in values]
        pairs += [{'mission_id':v,'mission_version':'version'} for v in values]
        before=current(self.x.case)
        with patch.object(self.x.case,'read') as backend:
            for pair in pairs:
                result=self.x.call('forsic_read',{'path':'request.log',**pair})
                self.assertEqual(result.get('error_code'),'invalid_arguments',result)
                self.assertFalse(result['backend_started'])
            backend.assert_not_called()
        self.assertEqual(current(self.x.case)['missions'],before['missions'])

    def test_repair_uses_same_unsaved_plan_then_exact_execution_and_no_duplicate(self):
        payload={**self.x.plan(),'tool_name':'forsic_image_files',
                 'tool_arguments':{'path':'synthetic.e01','action':'read'}}
        rejected=self.x.call('forsic_mission',payload)
        self.assertIn('file_path',rejected['planned_tool_contract']['required_fields'])
        saved=self.x.call('forsic_mission',{**payload,'tool_arguments':{**payload['tool_arguments'],'file_path':'body'}})
        self.assertNotIn('error',saved,saved)
        call=saved['next_call']
        # The direct physical call still needs file_path; the reference resolves it
        # only from this exact saved plan, never from the latest mission.
        bad={**payload['tool_arguments'],'mission_id':saved['mission']['id'],
             'mission_version':saved['mission']['version']}
        with patch.object(self.x.case,'image_files',return_value={'lines':[{'line':1,'text':'synthetic'}]}) as backend:
            result=self.x.call('forsic_image_files',bad)
            self.assertTrue(result['execution_rejected']);backend.assert_not_called()
            self.assertEqual(find(current(self.x.case),'missions',saved['mission']['id'])['state'],'draft')
            good=self.x.call(call['tool'],call['arguments'])
            self.assertNotIn('error',good,good)
            backend.assert_called_once_with({**payload['tool_arguments'],'file_path':'body'})
            self.assertEqual(good['mission_context']['mission_id'],saved['mission']['id'])
            duplicate=self.x.call(call['tool'],call['arguments'])
            self.assertTrue(duplicate['execution_rejected']);backend.assert_called_once()

    def test_reader_rejects_before_parser_import_and_host_before_docker(self):
        original_import=builtins.__import__
        def guarded_import(name,*args,**kwargs):
            if name.startswith('dissect'):raise AssertionError('parser must not load')
            return original_import(name,*args,**kwargs)
        args={'path':'synthetic.e01','action':'read'}
        with patch('builtins.__import__',side_effect=guarded_import):
            with self.assertRaises(ValueError):image_reader.inspect('synthetic.e01',args)
        with patch.object(self.x.case,'path') as path,patch('forsic_plugin.evidence.subprocess.run') as docker:
            with self.assertRaises(ValueError):self.x.case.image_files(args)
            path.assert_not_called();docker.assert_not_called()

    def test_search_reports_all_invalid_limits_with_path_before_backend(self):
        args={'path':'','action':'search','file_path':'/sample.log','text':'sample',
              'max_seconds':60,'max_matches':80,'max_bytes':0,'max_files':True}
        before=deepcopy(args)
        with patch.object(self.x.case,'image_files',return_value={'matches':[]}) as backend:
            rejected=self.x.call('forsic_image_files',args)
            self.assertEqual({p['path'] for p in rejected['problems']},
                             {'arguments.'+k for k in ('path','max_seconds','max_matches','max_bytes','max_files')})
            self.assertFalse(rejected['backend_started']);backend.assert_not_called()
            self.assertEqual(args,before)
            self.assertEqual(self.x.case.event(rejected['started_id'])['data']['arguments'],before)
            corrected={**args,'path':'synthetic.e01','max_seconds':25,'max_matches':60,
                       'max_bytes':33554432,'max_files':500}
            recovered=self.x.call('forsic_image_files',corrected)
            self.assertNotIn('error',recovered,recovered)
            backend.assert_called_once_with(corrected)

    def test_search_limit_validation_precedes_parser_and_matches_host_admission(self):
        limits={'max_seconds':(0,60,True,'15',None,float('nan'),float('inf')),
                'max_files':(0,501,1.5), 'max_bytes':(0,33554433,1.5),
                'max_matches':(0,61,1.5)}
        original_import=builtins.__import__
        def guarded_import(name,*args,**kwargs):
            if name.startswith('dissect'):raise AssertionError('parser must not load')
            return original_import(name,*args,**kwargs)
        for field,values in limits.items():
            for value in values:
                args={'path':'synthetic.e01','action':'search','file_path':'/sample.log','text':'sample',field:value}
                with self.subTest(field=field,value=value), patch('builtins.__import__',side_effect=guarded_import):
                    with self.assertRaises(image_reader.InvalidArguments):
                        image_reader.inspect('synthetic.e01',args)
                with patch.object(self.x.case,'search') as backend:
                    result=self.x.call('forsic_search',{'path':'sample.log','text':'sample',field:value})
                    self.assertEqual(result.get('error_code'),'invalid_arguments',result)
                    self.assertFalse(result['backend_started']);backend.assert_not_called()
        for field,value in [('max_seconds',0.01),('max_seconds',25),('max_files',500),
                            ('max_bytes',33554432),('max_matches',60)]:
            args={'path':'synthetic.e01','action':'search','file_path':'/sample.log','text':'sample',field:value}
            image_reader.validate_image_action(args)

    def test_action_rules_match_sanitized_guidance_and_selected_plan_contract(self):
        from tools.schema_sanitizer import sanitize_tool_schemas
        from forsic_plugin.report_driven.investigation_state import native_plan_contract
        original=deepcopy(self.x.schemas['forsic_image_files'])
        wire=sanitize_tool_schemas([{'type':'function','function':original}])[0]['function']['parameters']
        schema=self.x.schemas['forsic_image_files']['parameters']
        self.assertEqual(original,self.x.schemas['forsic_image_files'])
        self.assertEqual(wire['properties']['action']['enum'],
            [action for action in image_reader.IMAGE_ACTION_REQUIRED if action not in ('read','read_bytes','archive_read','search')])
        self.assertEqual(wire['properties']['file_path']['minLength'],1)
        self.assertEqual(wire['required'],['action'])
        self.assertEqual(wire['properties']['image_ref']['required'], ['id','version','index'])
        branches=self.x.schemas['forsic_mission']['parameters']['properties']['tool_arguments']['anyOf']
        physical=next(branch for branch in branches if branch['title']=='forsic_image_files')
        self.assertNotIn('from_mission',physical['properties']['action']['enum'])
        for action in image_reader.IMAGE_ACTION_REQUIRED:
            selected=native_plan_contract('forsic_image_files',{'action':action},{'forsic_image_files':physical})
            contract=image_reader.image_action_contract(action)
            self.assertEqual(selected['required'],contract['required'])
            for field in contract['required']:
                expected=contract['properties'][field]
                self.assertEqual(selected['properties'][field],expected)
        self.assertEqual(schema['required'],['action'])
        # Native provider compatibility strips top-level unions. Conditional requirements
        # remain explicit guidance; shared admission (tested above) is authoritative.
        search=sanitize_tool_schemas([{'type':'function','function':deepcopy(self.x.schemas['forsic_image_search'])}])[0]['function']['parameters']
        self.assertEqual(search['required'],['file_path'])
        self.assertIn('omit text', search['properties']['text']['description'])
        self.assertIn('image_ref', search['properties'])
        self.assertEqual(search['properties']['max_matches']['maximum'],60)



if __name__=='__main__':unittest.main()
