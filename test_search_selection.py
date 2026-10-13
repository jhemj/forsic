"""Real synthetic search pages through native handlers, without a model or network."""
from copy import deepcopy
import json
from unittest.mock import Mock, patch
import unittest

import test_efficiency_contracts as fixtures
from forsic_plugin import register
from forsic_plugin.report_driven.host import source_record
from forsic_plugin.context_results import summarize
from forsic_plugin.loop_comparison import project


class SearchSelectionTests(unittest.TestCase):
    def setUp(self):
        self.x = fixtures.EfficiencyContracts(); self.x.setUp(); self.addCleanup(self.x.doCleanups)
        self.context = dict(status='ready', goal_id='synthetic-goal', goal_status='active',
            requested_session_id='synthetic-session', conversation_id='synthetic-session',
            case_id=self.x.case.config['case_id'])
        p = patch('forsic_plugin.investigation_context.native_goal_binding', return_value=self.context)
        p.start(); self.addCleanup(p.stop)
        ctx = Mock(); ctx.get_config.return_value = None; register(ctx)
        self.x.handlers = {c.kwargs['name']: c.kwargs['handler'] for c in ctx.register_tool.call_args_list}
        self.x.schemas = {c.kwargs['name']: c.kwargs['schema'] for c in ctx.register_tool.call_args_list}
        (self.x.case.root/'many.log').write_text('hit first\nhit second\nhit third\n')
        self.args = dict(path='many.log', text='hit', max_matches=1)

    def first(self):
        r = self.x.call('forsic_search', self.args)
        self.assertNotIn('error', r, r)
        return r

    def test_native_selection_preserves_page_cursor_exactly_and_does_not_claim_full_coverage(self):
        a = self.first(); self.assertNotIn('next_cursor', a); self.assertTrue(a['has_next_cursor'])
        retained = deepcopy(self.x.case.event(a['evidence_id']))
        c = a['next_call']; b = self.x.call(c['tool'], c['arguments'])
        self.assertNotIn('error', b, b); self.assertEqual(b['matches'][0]['line'], 2)
        start = self.x.case.event(b['started_id'])['data']
        self.assertEqual(start['submitted_arguments'], c['arguments'])
        self.assertEqual(start['arguments']['cursor'], retained['data']['next_cursor'])
        self.assertNotIn('cursor_ref', start['arguments'])
        self.assertEqual(self.x.case.event(a['evidence_id']), retained)
        c = b['next_call']; last = self.x.call(c['tool'], c['arguments'])
        self.assertEqual(last['matches'][0]['line'], 3); self.assertFalse(last['has_next_cursor'])
        self.assertFalse(last['coverage_complete']); self.assertIsNone(last['next_call'])
        self.assertTrue(last['presented_spans'])

    def test_wrong_selection_target_query_and_goal_rejected_before_reader(self):
        a = self.first(); args = a['next_call']['arguments']
        alternatives = [dict(cursor_ref={}), dict(cursor_ref={**args['cursor_ref'], 'version':'wrong'}),
            dict(cursor_ref={'id':'foreign-result','version':'wrong'}), dict(path='process.log'),
            dict(text='other'), dict(cursor={}), dict(offset=0), dict(recursive=True)]
        for change in alternatives:
            with self.subTest(change=change), patch.object(self.x.case,'search', side_effect=AssertionError('reader must not run')):
                r = self.x.call('forsic_search', {**args, **change})
                self.assertTrue(r.get('execution_rejected'), r); self.assertFalse(r['backend_started'])
                self.assertNotIn('outcome', r)
        for status in ('paused','done','unknown'):
            self.context['goal_status'] = status
            self.assertTrue(self.x.call('forsic_search', args)['execution_rejected'])

    def test_stale_retained_page_and_changed_evidence_rejected(self):
        a=self.first(); args=a['next_call']['arguments']
        event=self.x.case.event(a['evidence_id']); event['data']['matches'][0]['text']='Changed'
        with self.x.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(event['data']),event['id']))
        self.assertTrue(self.x.call('forsic_search',args)['execution_rejected'])
        a=self.first(); args=a['next_call']['arguments'];(self.x.case.root/'many.log').write_text('changed content\n')
        r=self.x.call('forsic_search',args);self.assertIn('error',r);self.assertNotIn('matches',r)

    def test_complete_or_failed_results_are_not_continuations(self):
        full=self.x.call('forsic_search',{**self.args,'max_matches':60})
        for r in (full,self.x.call('forsic_search',{'path':'missing','text':'hit'})):
            ref={k:source_record(self.x.case.event(r['evidence_id']))[k] for k in ('id','version')}
            out=self.x.call('forsic_search',{**self.args,'cursor_ref':ref})
            self.assertTrue(out['execution_rejected']);self.assertFalse(out['backend_started'])

    def test_image_selection_runs_same_reader_with_exact_target_and_volume(self):
        from test_search_resume import Node
        from forsic_plugin.image_reader import search_node
        node=Node('/logs/a', b'hit one\nhit two\nhit three\n')
        (self.x.case.root/'disk.E01').write_bytes(b'synthetic image placeholder')
        args=dict(path='disk.E01',action='search',file_path='/logs/a',volume_offset=4096,
                  text='hit',max_matches=1)
        def reader(a):
            return dict(path=a['path'],file_path=a['file_path'],volume_offset=a['volume_offset'],
                        **search_node(node,a,{'image':'synthetic'}))
        with patch.object(self.x.case,'image_files',side_effect=reader) as backend:
            a=self.x.call('forsic_image_files',args);self.assertNotIn('error',a,a)
            c=a['next_call'];b=self.x.call(c['tool'],c['arguments']);self.assertNotIn('error',b,b)
            self.assertEqual([a['matches'][0]['line'],b['matches'][0]['line']],[1,2])
            self.assertEqual(backend.call_args.args[0]['volume_offset'],4096)
            before=backend.call_count
            for change in ({'volume_offset':8192},{'file_path':'/'},{'action':'list'}):
                out=self.x.call(c['tool'],{**c['arguments'],**change})
                self.assertTrue(out['execution_rejected'],out)
            self.assertEqual(backend.call_count,before)

    def test_cancelled_or_ambiguous_execution_is_not_a_page_reference(self):
        a=self.first();args=a['next_call']['arguments'];event=self.x.case.event(a['evidence_id'])
        event['data']['status']='cancelled'
        with self.x.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(event['data']),event['id']))
        args['cursor_ref']={k:source_record(event)[k] for k in ('id','version')}
        with patch.object(self.x.case,'search',side_effect=AssertionError('reader must not run')):
            self.assertTrue(self.x.call('forsic_search',args)['execution_rejected'])
        a=self.first();args=a['next_call']['arguments'];event=self.x.case.event(a['evidence_id'])
        with self.x.case.connect() as db:
            db.execute('INSERT INTO events VALUES (?,?,?,?,?)',('synthetic-duplicate',event['time'],'tool_result',event['session'],json.dumps(event['data'])))
        self.assertTrue(self.x.call('forsic_search',args)['execution_rejected'])

    def test_native_sanitized_schemas_keep_explicit_selection_fields(self):
        from tools.schema_sanitizer import sanitize_tool_schemas
        schemas=sanitize_tool_schemas([{'type':'function','function':deepcopy(v)} for v in self.x.schemas.values()])
        pin={s['function']['name']:s['function']['parameters'] for s in schemas}
        for tool in ('forsic_search','forsic_image_files'):
            self.assertEqual(pin[tool]['properties']['cursor_ref']['required'],['id','version'])
            self.assertNotIn('cursor',pin[tool]['properties'])
        gap=pin['forsic_question']['properties']['gap_dispositions']['items']
        self.assertIn('gap_text',gap['properties']);self.assertNotIn('gap_id',gap['required'])
        plans=pin['forsic_mission']['properties']['tool_arguments']['anyOf']
        for plan in plans:self.assertNotIn('cursor_ref',plan['properties'])
        a=self.first();p=self.x.plan();p.update(tool_name='forsic_search',tool_arguments=a['next_call']['arguments'])
        out=self.x.call('forsic_mission',p);self.assertIn('cursor_ref',out['error'])
        self.assertTrue(out['execution_rejected']);self.assertNotIn('mission',out)

    def test_comparison_and_pruning_preserve_scope_without_receipt_churn(self):
        a=self.first();b=self.first()
        self.assertEqual(project('forsic_search',self.args,json.dumps(a))['result'],
                         project('forsic_search',self.args,json.dumps(b))['result'])
        crumb=json.loads(summarize('forsic_search',self.args,json.dumps(a)))
        self.assertFalse(crumb['returned_scope']['coverage_complete'])
        self.assertEqual(crumb['returned_scope']['search_text'],'hit')
        self.assertEqual(crumb['next_call'],a['next_call'])
        self.assertTrue(crumb['continuation_available']);self.assertNotIn('citation_ref',json.dumps(crumb))
        self.assertNotIn('cursor',self.x.schemas['forsic_search']['parameters']['properties'])
        self.assertEqual(self.x.schemas['forsic_search']['parameters']['properties']['cursor_ref']['required'],['id','version'])

    def test_source_lookup_keeps_original_query_and_partial_flags_even_for_long_scope(self):
        a=self.first();event=self.x.case.event(a['evidence_id']);event['data']['scope_note']='long description '*500
        with self.x.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(event['data']),event['id']))
        source=source_record(event)
        for extra in ({'query':'first'},{'pointer':'/matches/0/text','source_version':source['version']}):
            r=self.x.call('forsic_reporting',{'action':'source','source_id':event['id'],**extra})
            self.assertNotIn('error',r,r); coverage=json.loads(r['retained_coverage'])
            self.assertFalse(coverage['coverage_complete']);self.assertTrue(coverage['has_next_cursor'])
            self.assertEqual(coverage['search_text'],'hit');self.assertEqual(coverage['match_type'],'casefold_literal')
            self.assertIn('scope_note',coverage['omitted_fields'])


if __name__=='__main__':unittest.main()
