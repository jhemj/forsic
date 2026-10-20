"""Display summaries use one explicit native call; no investigation mutations."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.evidence import Case
from forsic_plugin import investigation_enrichment as display


class DisplayEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); (root/'original').mkdir()
        manifest = root/'case.json'
        manifest.write_text(json.dumps(dict(case_id='synthetic', evidence_root=str(root/'original'),
            output_root=str(root/'results'))))
        self.case = Case(manifest)
        self.native = root/'state.db'
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('CREATE TABLE state_meta(key TEXT PRIMARY KEY,value TEXT)')
            db.execute('INSERT INTO state_meta VALUES (?,?)', ('goal:session', json.dumps({'status':'active'})))
        self.resolved = dict(case=self.case, case_id='synthetic', requested_session_id='session', session_lineage=['session'])
        self.route = dict(provider='custom', default='approved-model', base_url='https://model.invalid/v1')
        self.view = dict(case_id='synthetic', questions=[], sources=[dict(id='s1',version='v1',label='forsic_shell',
            display_title='서비스 설정 확인',coverage='selected stdout')],
            observations=[dict(id='o1',version='v1',source_ref=dict(kind='source',id='s1',version='v1'),
                field_pointer='/stdout',literal='service enabled',byte_start=0,byte_end=15,
                interpretation_limit='configuration, not execution')],timeline=[])
        self.calls = []

    def generate(self, request, cancel):
        self.assertFalse(cancel())
        self.calls.append(deepcopy(request))
        items=json.loads(request['messages'][1]['content'])
        return dict(finish_reason='stop',usage={'prompt_tokens':100,'completion_tokens':30},raw=json.dumps({'items':[
            {'handle':item['handle'],'title':'서비스 설정 내용','summary':'서비스 활성화 설정이 기록되어 있습니다. 실행 성공은 확인하지 않았습니다.'}
            for item in items]},ensure_ascii=False))

    def run_enrich(self, generate=None):
        with patch.object(display,'_route',return_value=(self.route,self.native)), \
             patch('forsic_plugin.investigation_view.project_view',side_effect=lambda *a,**k:deepcopy(self.view)):
            return display.enrich(self.resolved,profile='default',generate=generate or self.generate)

    def test_explicit_call_cached_by_exact_material_preserves_evidence_and_usage(self):
        original = deepcopy(self.view)
        first = self.run_enrich()
        self.assertEqual(first['status'],'ready')
        self.assertEqual(first['view']['sources'][0]['label'],'forsic_shell')
        self.assertEqual(first['view']['sources'][0]['version'],'v1')
        self.assertEqual(first['view']['sources'][0]['display_origin'],'model_summary')
        self.assertEqual(self.run_enrich()['status'],'cached')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.view,original)
        events=self.case.events()
        self.assertEqual({e['kind'] for e in events},{'pre_auxiliary_call','post_auxiliary_call'})
        self.assertEqual(next(e for e in events if e['kind']=='post_auxiliary_call')['data']['usage']['prompt_tokens'],100)
        changed=deepcopy(self.view);changed['sources'][0]['version']='v2'
        self.assertNotIn('display_origin',display.apply_cached_display(self.resolved,changed)['sources'][0])
        # A GET cannot call the model or append a ledger event.
        with patch.object(display,'_generate',side_effect=AssertionError('GET called model')), \
             patch.object(Case,'record',side_effect=AssertionError('GET wrote ledger')):
            self.assertEqual(display.apply_cached_display(self.resolved,deepcopy(self.view))['display_enrichment']['cached_items'],2)

    def test_forged_reference_rejected_and_unknown_completion_never_resent(self):
        def forged(request,cancel):
            self.calls.append(request)
            return dict(finish_reason='stop',raw=json.dumps({'items':[dict(handle='foreign',title='x',summary='x')]}))
        self.assertEqual(self.run_enrich(forged)['status'],'failed')
        self.assertEqual(self.run_enrich()['status'],'failed')
        self.assertEqual(len(self.calls),1)
        self.assertFalse((self.case.output/'investigation-display-cache.json').exists())
        # Interrupted receipt write is observable; no fresh call repairs it implicitly.
        for path in self.case.output.glob('*-response.json'): path.unlink()
        self.assertEqual(self.run_enrich()['status'],'unconfirmed')
        self.assertEqual(len(self.calls),1)

    def test_new_user_pause_cancels_and_input_budget_is_bounded(self):
        initial=display._interrupted(self.native,['session'],None)
        with closing(sqlite3.connect(self.native)) as db, db:
            db.execute('UPDATE state_meta SET value=?',(json.dumps({'status':'paused'}),))
        self.assertTrue(display._interrupted(self.native,['session'],initial))
        self.view['sources'] *= 200
        result=self.run_enrich()
        # Repeated material is a single target, not permission to multiply calls.
        self.assertEqual(result['status'],'ready')
        self.assertEqual(len(json.loads(self.calls[0]['messages'][1]['content'])),2)

    def test_native_explicit_cancellation_is_recorded_and_never_resent(self):
        from agent.auxiliary_client import AuxiliaryExplicitCancellation
        def stopped(request,cancel):
            self.calls.append(request)
            raise AuxiliaryExplicitCancellation()
        result = self.run_enrich(stopped)
        self.assertEqual(result['status'],'cancelled')
        response = json.loads(next(self.case.output.glob('*-response.json')).read_text())
        self.assertEqual(response['status'],'cancelled')
        self.assertEqual(response['error_type'],'AuxiliaryExplicitCancellation')
        post = next(event for event in self.case.events() if event['kind']=='post_auxiliary_call')
        self.assertEqual(post['data']['error'],'AuxiliaryExplicitCancellation')
        self.assertEqual(self.run_enrich()['status'],'cancelled')
        self.assertEqual(len(self.calls),1)
        self.assertFalse((self.case.output/'investigation-display-cache.json').exists())

    def test_successful_receipt_restores_pruned_overlapping_selection_without_new_call(self):
        original = deepcopy(self.view)
        self.assertEqual(self.run_enrich()['status'],'ready')
        # B preserves A's observation but replaces its selected source. Thus the
        # missing subset when A returns differs from A's original request batch.
        self.view['sources'] = [dict(self.view['sources'][0],id='s2',version='v2')]
        self.assertEqual(self.run_enrich()['status'],'ready')
        cache = json.loads((self.case.output/'investigation-display-cache.json').read_text())
        self.assertNotIn(display._items(original)[0]['key'],cache['entries'])
        self.view = original
        before = self.case.db.read_bytes()
        result = self.run_enrich()
        self.assertEqual(result['status'],'cached')
        self.assertEqual(result['view']['display_enrichment']['cached_items'],2)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.case.db.read_bytes(),before)
        self.assertEqual(len(self.case.events()),4)

    def test_recovery_rejects_changed_full_request_snapshot_and_foreign_response(self):
        self.assertEqual(self.run_enrich()['status'],'ready')
        request_path = next(self.case.output.glob('*-request.json'))
        response_path = next(self.case.output.glob('*-response.json'))
        request = json.loads(request_path.read_text()); response = json.loads(response_path.read_text())
        for changed in ('request_material','request_messages','response_identity'):
            with self.subTest(changed=changed):
                (self.case.output/'investigation-display-cache.json').unlink(missing_ok=True)
                request_path.write_text(json.dumps(request)); response_path.write_text(json.dumps(response))
                if changed.startswith('request'):
                    forged = deepcopy(request)
                    if changed == 'request_material':
                        forged['items'][0]['version'] = 'unselected-version'
                    else:
                        forged['messages'][1]['content'] = 'different presented material'
                    request_path.write_text(json.dumps(forged))
                else:
                    response_path.write_text(json.dumps(dict(response,request_id='foreign-request')))
                result = self.run_enrich()
                self.assertEqual(result['status'],'failed')
                self.assertEqual(result['view']['display_enrichment']['cached_items'],0)
                self.assertEqual(len(self.calls),1)

    def test_receipt_recovery_respects_count_and_byte_budgets(self):
        self.assertEqual(self.run_enrich()['status'],'ready')
        needed = {item['key'] for item in display._items(self.view)}
        with patch.object(display,'MAX_RECOVERY_RECEIPTS',0):
            self.assertEqual(display._recover(self.case,'synthetic',self.route,needed),{})
        with patch.object(display,'MAX_RECOVERY_BYTES',1):
            self.assertEqual(display._recover(self.case,'synthetic',self.route,needed),{})


if __name__=='__main__': unittest.main()
