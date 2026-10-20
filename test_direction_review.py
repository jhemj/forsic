"""Direction-review regressions using synthetic ledger calls, no model/network."""
import json
import unittest
import test_current_judgment as base
from forsic_plugin.report_driven.current_judgment import current_judgment,current_claims
from forsic_plugin.report_driven.views import compile_views
from forsic_plugin.report_driven.model_pages import select

class DirectionReviewTests(unittest.TestCase):
    def setUp(self):
        self.x=base.CurrentJudgmentTests();self.x.setUp();self.addCleanup(self.x.doCleanups)
    def second(self,patch):
        x=self.x;p=x.synthesize();p['mission_version']=x.state()['missions'][0]['version']
        p.update(operation_id='sparse-update',expected_revision=x.q()['revision'])
        p['question_update']={'answer':'Revised aggregate explanation','assessment':'conflicting',
                              'reason':'Revise only specified fields',**patch}
        x.call('assess',p)
        return current_judgment(x.state(),x.q())
    def test_sparse_update_preserves_aggregate_fields(self):
        j=self.second({});c=j['judgments'][0]
        for k,v in {'inference_strength':'favored','assumptions':['Independent record origin'],
                    'reasoning_summary':'Aggregate reason','limitations':['New aggregate limit']}.items():
            self.assertEqual(c[k],v)
        self.assertEqual(j['assessment'],'conflicting')
        self.assertIn('New aggregate next check',json.dumps(j))
    def test_explicit_empty_fields_clear_without_local_test_leakage(self):
        j=self.second({'assumptions':[],'limitations':[],'reasoning_summary':'','inference_strength':'unrated'})
        for k,v in {'assumptions':[],'limitations':[],'reasoning_summary':'','inference_strength':'unrated'}.items():
            self.assertEqual(j['judgments'][0][k],v)
        self.assertTrue(j['local_assessments'][-1]['limitations'])
    def test_retract_without_replacement_has_no_current_old_answer(self):
        x=self.x;x.synthesize();before=x.q();x.update(reason='Withdraw unsupported answer',claim_action='retract')
        state=x.state();self.assertTrue(x.q()['judgment_review_required'])
        self.assertEqual(x.q()['definition_version'],before['definition_version']);self.assertFalse(current_claims(state))
        for view in compile_views(state).values():
            text=json.dumps(view,ensure_ascii=False);self.assertNotIn('Aggregate explanation A',text);self.assertIn('재검토',text)
        self.assertNotIn('Aggregate explanation A',json.dumps(current_judgment(state,x.q())))
        self.assertTrue(any(e['data'].get('answer')=='Aggregate explanation A' for e in x.case.events(100) if e['kind']=='note'))
    def test_legacy_candidate_still_available(self):
        self.assertTrue(current_claims(self.x.state()))
    def test_measurement_pointers_roundtrip_real_source_handler(self):
        x=self.x;result=json.loads(x.case.invoke('forsic_search',{'path':'.','text':'missing'}))
        source=json.loads(x.case.invoke('forsic_reporting',{'action':'source','source_id':result['evidence_id']}))
        self.assertTrue(source['measurement']['scope_pointers'])
        for pointer in source['measurement']['scope_pointers']:
            page=json.loads(x.case.invoke('forsic_reporting',{'action':'source','source_id':result['evidence_id'],
                 'source_version':source['source_ref']['version'],'pointer':pointer}))
            self.assertNotIn('error',page);self.assertIsNotNone(select(x.case.event(result['evidence_id'])['data'],pointer))
    def test_state_overview_never_reintroduces_cut_answer(self):
        x=self.x;x.update(answer='affirmative prefix '*80+'BUT RETRACTED',reason='Preserve entire caveat')
        result=json.loads(x.case.invoke('forsic_reporting',{'action':'state'}))
        self.assertNotIn('affirmative prefix',json.dumps(result));self.assertNotIn('answer_preview',json.dumps(result))

class RuntimeProfileTests(unittest.TestCase):
    def test_initializer_and_example_have_same_wait_policy(self):
        from pathlib import Path
        from ruamel.yaml import YAML
        from deploy_forsic import initial_config
        example=YAML(typ='safe').load(Path('config/config.example.yaml').read_text())
        config=initial_config('http://127.0.0.1:11434/v1','synthetic-model')
        for key in ('providers','agent','compression'):
            self.assertEqual(config[key],example[key])
        for key in ('compression','goal_judge','report_review'):
            self.assertEqual(config['auxiliary'][key]['timeout'],example['auxiliary'][key]['timeout'])
        self.assertEqual(config['auxiliary']['title_generation'],example['auxiliary']['title_generation'])
    def test_native_compression_resolves_configured_budgets(self):
        from unittest.mock import patch
        from agent.conversation_compression import resolve_context_compression_timeouts
        from deploy_forsic import initial_config
        cfg=initial_config('http://127.0.0.1:11434/v1','synthetic-model')
        with patch('agent.auxiliary_client._effective_aux_timeout',return_value=1200):
            self.assertEqual(resolve_context_compression_timeouts(cfg['compression']),(1200,1800))

if __name__=='__main__':unittest.main()
