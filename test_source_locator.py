"""Retained-result navigation contracts; synthetic evidence, no models/network."""
import json
import tempfile
import unittest
from unittest.mock import patch
from test_report_driven import fixture
from forsic_plugin.report_driven.host import current, invoke, source_record
from forsic_plugin.report_driven.model_pages import MAX_BYTES, size, source_view
from forsic_plugin.report_driven.source_locator import MAX_CHARS


class SourceLocatorTests(unittest.TestCase):
    def view(self, data, **args):
        source = source_record({'id':'E-synthetic', 'data':data})
        return source, source_view(source, data, args)

    def test_utf8_tail_and_direct_next_call(self):
        data = {'tool':'forsic_read', 'text':'가🙂' * MAX_CHARS + '끝%_🙂'}
        source, result = self.view(data, query='끝%_🙂')
        self.assertEqual(result['returned_count'], 0)
        self.assertFalse(result['coverage_complete'])
        while result['next_call']:
            result = source_view(source, data, result['next_call'])
            self.assertLessEqual(size(result), MAX_BYTES)
            if result['matches']:
                break
        match = result['matches'][0]
        self.assertEqual(match['role'], 'body')
        exact = source_view(source, data, match['next_call'])
        self.assertEqual(exact['text'], '끝%_🙂')
        self.assertEqual(data['text'].encode()[match['byte_start']:match['byte_end']], exact['text'].encode())
        self.assertEqual(match['citation'],dict(source_id=source['id'],source_version=source['version'],
            pointer=match['pointer'],literal=exact['text'],byte_start=match['byte_start']))

    def test_literal_crosses_scan_boundary(self):
        data = {'tool':'forsic_read', 'text':'x'*(MAX_CHARS-3)+'가🙂end'}
        source, result = self.view(data, query='가🙂end')
        hits=[]
        while True:
            hits.extend(result['matches'])
            if not result['next_call']:break
            result=source_view(source,data,result['next_call'])
        self.assertEqual(len(hits),1)

    def test_overlap_continuation_has_no_duplicates_or_omissions(self):
        data={'tool':'forsic_read','text':'a'*40}
        source,result=self.view(data,query='aaa',limit=2)
        hits=[]
        while True:
            self.assertLessEqual(size(result),MAX_BYTES)
            hits.extend(result['matches'])
            if not result['next_call']:break
            result=source_view(source,data,result['next_call'])
        self.assertEqual([m['byte_start'] for m in hits],list(range(38)))
        self.assertTrue(result['scan_exhausted'])
        self.assertFalse(result['coverage_complete'])

    def test_roles_and_escaped_pointer_and_literal_wildcards(self):
        data={'tool':'forsic_search','search_text':'%_', 'matches':[{'text':'%_'}], 'a/b~c':'%_'}
        source,result=self.view(data,query='%_')
        self.assertTrue(result['coverage_complete'])
        self.assertEqual([m['role'] for m in result['matches']],['input','body','metadata'])
        self.assertEqual(result['matches'][-1]['pointer'],'/a~1b~0c')
        for m in result['matches']:
            self.assertEqual(source_view(source,data,m['next_call'])['text'],'%_')

    def test_zero_match_complete_vs_partial(self):
        _,small=self.view({'tool':'forsic_read','text':'empty'},query='needle')
        _,large=self.view({'tool':'forsic_read','text':'x'*(MAX_CHARS*2)},query='needle')
        self.assertTrue(small['coverage_complete']);self.assertTrue(small['scan_exhausted'])
        self.assertFalse(large['coverage_complete']);self.assertFalse(large['scan_exhausted'])
        self.assertEqual(small['returned_count'],0);self.assertEqual(large['returned_count'],0)

    def test_stale_discovery_and_query_rejected(self):
        data={'tool':'forsic_read','text':'abc'}
        source=source_record({'id':'E-synthetic','data':data})
        for args in ({'source_version':'old'},{'query':'abc','source_version':'old'}):
            with self.assertRaisesRegex(ValueError,'source_version'):
                source_view(source,data,args)

    def test_cursor_binding_and_invalid_arguments(self):
        data={'tool':'forsic_read','text':'a'*100}
        source,result=self.view(data,query='a',limit=1)
        for key,value in [('query','different'),('source_version','old')]:
            args={**result['next_call'],key:value}
            with self.assertRaises(ValueError):source_view(source,data,args)
        for args in ({'query':''},{'query':123},{'query':'🙂'*100},{'query':'a','pointer':'/text'},
                     {'query':'a','locator_cursor':{}},{'locator_cursor':{}}):
            with self.assertRaises(ValueError):source_view(source,data,args)

    def test_node_limit_and_nonstring_exclusion(self):
        data={'tool':'forsic_read','records':[0]*600+['target']}
        source,result=self.view(data,query='target')
        self.assertEqual(result['stop_reason'],'node_limit')
        result=source_view(source,data,result['next_call'])
        self.assertEqual(result['matches'][0]['pointer'],'/records/600')
        _,zero=self.view(data,query='0')
        self.assertFalse(zero['matches'])

    def test_actual_case_source_roundtrip_and_read_only_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            case,results,note=fixture(tmp)
            sid=results[0]['evidence_id'];before=current(case);raw=case.db.read_bytes()
            with patch('socket.socket',side_effect=AssertionError('network forbidden')):
                result=invoke(case,{'action':'source','source_id':sid,'query':'requested'})
                self.assertTrue(result['matches'])
                for match in result['matches']:
                    self.assertEqual(invoke(case,match['next_call'])['text'],match['literal'])
            self.assertEqual(case.db.read_bytes(),raw)
            self.assertEqual(current(case),before)
            native=json.loads(case.invoke('forsic_reporting',{'action':'source','source_id':sid,'query':'requested'}))
            self.assertNotIn('error',native)
            self.assertEqual(current(case)['questions'],before['questions'])
            self.assertEqual(current(case)['sources'],before['sources'])
            with self.assertRaises((ValueError,StopIteration)):
                invoke(case,{'action':'source','source_id':'other-case-id','query':'requested'})

    def test_registered_handler_accepts_query_and_exact_next_call(self):
        from pathlib import Path
        from unittest.mock import Mock
        from forsic_plugin import register
        with tempfile.TemporaryDirectory() as tmp:
            case, results, _ = fixture(tmp)
            ctx=Mock();ctx.get_config.side_effect=lambda k,d=None: str(Path(tmp)/'intake') if k=='intake_root' else d
            with patch('forsic_plugin.intake.Intake.case',return_value=case), patch('forsic_plugin.investigation_context.native_goal_binding',return_value={'goal_id':'test'}):
                register(ctx)
                tool=next(c.kwargs for c in ctx.register_tool.call_args_list if c.kwargs['name']=='forsic_reporting')
                self.assertIn('query',tool['schema']['parameters']['properties'])
                self.assertIn('locator_cursor',tool['schema']['parameters']['properties'])
                result=json.loads(tool['handler']({'action':'source','source_id':results[0]['evidence_id'],'query':'requested'},session_id='test'))
                self.assertTrue(result['matches'])
                page=json.loads(tool['handler'](result['matches'][0]['next_call'],session_id='test'))
                self.assertEqual(page['text'],'requested')

    def test_failed_or_skipped_original_search_stays_explicit(self):
        data={'tool':'forsic_search','search_text':'absent','matches':[], 'files_completed':0,
              'files_skipped':2,'coverage_complete':False,'error':'synthetic read failure'}
        _,result=self.view(data,query='unmatched-literal')
        self.assertTrue(result['coverage_complete'])  # retained strings only
        coverage=json.loads(result['retained_coverage'])
        self.assertFalse(coverage['coverage_complete']);self.assertEqual(coverage['files_skipped'],2)
        self.assertEqual(coverage['error'],'synthetic read failure')

    def test_identical_text_other_source_and_cursor_cannot_cross(self):
        data={'tool':'forsic_read','text':'a'*10}
        source,result=self.view(data,query='a',limit=1)
        other=source_record({'id':'E-other','data':data})
        with self.assertRaisesRegex(ValueError,'another source'):
            source_view(other,data,result['next_call'])

    def test_escaped_query_response_budget(self):
        data={'tool':'forsic_read','text':'\x00'*2000}
        source,result=self.view(data,query='\x00'*64,limit=10)
        self.assertTrue(result['matches']);self.assertLessEqual(size(result),MAX_BYTES)
        self.assertEqual(result['returned_count'],len(result['matches']))
        self.assertIsNotNone(result['next_call'])
