"""Synthetic native-handler review context bounds; all model calls are local fakes."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from forsic_plugin import independent_review as review
from forsic_plugin.report_driven import host
from forsic_plugin.review_delivery import automatic_review_context
import test_review_actions as fixtures


class ReviewContextDeliveryTests(unittest.TestCase):
    for _name in ('setUp','bind','session','save_note','setup_review','add_sources','kwargs',
                  'response','request_paths','resolved','prepare','call','args','start','cite','response_args'):
        locals()[_name]=getattr(fixtures.ReviewActionsTests,_name)
    del _name

    def handlers(self,enabled=True):
        from forsic_plugin import register
        ctx=Mock()
        ctx.get_config.side_effect=lambda key: str(self.intake) if key=='intake_root' else enabled if key=='independent_review' else None
        register(ctx)
        return {call.kwargs['name']:call.kwargs['handler'] for call in ctx.register_tool.call_args_list}

    def large_four_findings(self):
        self.start()
        state=deepcopy(review.review_state(self.resolved()))
        state['findings']=state['findings'][:4]
        state['pending_findings']=deepcopy(state['findings'])
        state['pending_finding_ids']=[f['finding_id'] for f in state['pending_findings']]
        # Expanded coordinates in a validated read projection used to survive
        # top-level clipping and were repeated on every unrelated state error.
        for finding in state['findings']:
            finding['citations']=[{'literal':'PRIVATE_REVIEW_COORDINATE '*400}]*4
        return state

    def test_repeated_stale_state_errors_preserve_repair_but_deliver_only_small_review_navigation(self):
        state=self.large_four_findings()
        original=deepcopy(state)
        self.assertGreater(len(json.dumps(state,ensure_ascii=False)),50000)
        plain=self.handlers(enabled=False)
        old=host.current(self.case)['meta']['snapshot_id']
        self.save_note(answer='A revised current answer with explicit limits.')
        question_id='Q-'+self.note['note_id']
        args={'action':'state','snapshot_id':old,'question_id':question_id,'pointer':'/questions/0'}
        expected=json.loads(plain['forsic_reporting'](args,session_id=self.sid))
        self.assertEqual(expected['error_code'],'state_snapshot_mismatch')
        self.assertIn('next_call',expected)
        before=deepcopy(host.current(self.case)['questions']);calls=len(self.calls)
        with patch.object(review,'review_state',return_value=state):
            handlers=self.handlers()
            for _ in range(5):
                result=json.loads(handlers['forsic_reporting'](args,session_id=self.sid))
                attached=result.pop('independent_review')
                # Each real native call has distinct receipt IDs; error and
                # every repair selector remain byte-for-byte equivalent.
                receipt_keys={'evidence_id','started_id','reused_from'}
                self.assertEqual({k:v for k,v in result.items() if k not in receipt_keys},
                                 {k:v for k,v in expected.items() if k not in receipt_keys})
                self.assertEqual(attached['pending_count'],4)
                self.assertEqual(attached['pending_scope'],'material')
                self.assertEqual(attached['lookup'],{'tool':'forsic_reporting','arguments':{'action':'review_inbox'}})
                self.assertTrue(attached['requires_followup'])
                self.assertLess(len(json.dumps(attached,ensure_ascii=False)),650)
                self.assertLess(len(json.dumps({**result,'independent_review':attached},ensure_ascii=False)),2500)
                self.assertNotIn('PRIVATE_REVIEW_COORDINATE',json.dumps(attached))
            # Explicit lookup still supplies whole finding/source/save inputs.
            inbox=json.loads(handlers['forsic_reporting'](attached['lookup']['arguments'],session_id=self.sid))
            self.assertEqual(len(inbox['findings']),4)
            for item,finding in zip(inbox['findings'],state['findings']):
                self.assertEqual(item['question'],finding['question'])
                self.assertEqual(item['next_check'],finding['next_check'])
                self.assertEqual(item['source_reference']['quote'],finding['quote'])
                self.assertEqual(item['source_reference']['source_version'],finding['source_version'])
                self.assertEqual(item['response_input']['save_call']['tool'],'forsic_review_response')
                self.assertEqual(item['response_input']['save_call']['arguments']['finding_id'],finding['finding_id'])
            self.assertFalse(inbox['semantic_approval'])
        self.assertEqual(host.current(self.case)['questions'],before)
        self.assertEqual(state,original);self.assertEqual(len(self.calls),calls)

    def test_successful_state_and_gap_reads_use_the_same_navigation_contract(self):
        state=self.large_four_findings()
        with patch.object(review,'review_state',return_value=state):
            handlers=self.handlers()
            for action in ('state','gaps'):
                result=json.loads(handlers['forsic_reporting']({'action':action},session_id=self.sid))
                self.assertNotIn('error',result,result)
                self.assertEqual(result['independent_review'],automatic_review_context(state))
                self.assertNotIn('findings',result['independent_review'])
                self.assertNotIn('dispositions',result['independent_review'])

    def test_automatic_navigation_survives_repeat_projection_without_losing_pending_count(self):
        from forsic_plugin.context_results import review_navigation
        first=automatic_review_context({'status':'received','pending_finding_ids':['a','b','c','d'],
            'requires_followup':True,'review_id':'review-current'})
        second=review_navigation({'independent_review':first})['independent_review']
        self.assertEqual(second['pending_count'],4)
        self.assertEqual(second['lookup'],first['lookup'])
        self.assertEqual(second['role'],first['role'])
        self.assertTrue(second['requires_followup'])

    def test_bare_or_invalid_count_is_not_promoted_into_known_review_state(self):
        from forsic_plugin.context_results import review_navigation
        self.assertIsNone(review_navigation({'independent_review':{
            'status':'received','pending_count':4}})['independent_review']['pending_count'])
        for count in (None,True,-1,2**53):
            first=automatic_review_context({'status':'received','pending_finding_ids':[]})
            first['pending_count']=count
            self.assertIsNone(review_navigation({'independent_review':first})['independent_review']['pending_count'])
        zero=automatic_review_context({'status':'received','pending_finding_ids':[]})
        self.assertEqual(review_navigation({'independent_review':zero})['independent_review']['pending_count'],0)
        zero['status']='unavailable'
        self.assertIsNone(review_navigation({'independent_review':zero})['independent_review']['pending_count'])

    def test_automatic_projection_preserves_unknown_and_never_mutates_review_advice(self):
        for status in ('received','stale','unavailable','unknown'):
            advice={'status':status,'review_id':'review-current','requires_followup':True,
                'pending_finding_ids':['a','b','c','d'],'context':'PRIVATE_PROPOSED_TEST '*1000,
                'pending_items':[{'question':'PRIVATE_REVIEW_QUESTION'}],
                'followup_action':'respond','findings':[{'why':'PRIVATE_REVIEW_REASON'}]}
            before=deepcopy(advice);view=automatic_review_context(advice)
            self.assertEqual(advice,before)
            self.assertEqual(view['status'],status)
            self.assertEqual(view['pending_count'],None if status in ('unavailable','unknown') else 4)
            self.assertTrue(view['advice_body_omitted'])
            self.assertNotIn('PRIVATE_',json.dumps(view))
            self.assertLess(len(json.dumps(view)),650)


if __name__=='__main__':unittest.main()
