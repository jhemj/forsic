"""Offline regressions: exact grouped counts and residual scope at delivery."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

import test_investigation_context as context_tests
from forsic_plugin import investigation_context as context
from forsic_plugin.report_driven.delivery import delivery_view
from forsic_plugin.report_driven.host import current
from forsic_plugin.report_driven.views import bundle, compile_views
from forsic_plugin.report_driven.finalization import finalize


class DeliveryFidelityTests(unittest.TestCase):
    setUp = context_tests.InvestigationContextTests.setUp
    bind = context_tests.InvestigationContextTests.bind
    session = context_tests.InvestigationContextTests.session
    save_note = context_tests.InvestigationContextTests.save_note
    kwargs = context_tests.InvestigationContextTests.kwargs
    prepare = context_tests.InvestigationContextTests.prepare
    validate = context_tests.InvestigationContextTests.validate

    def counted_answer(self):
        self.note['answer'] = ('Observed in the selected authentication logs only. ' * 7
            + 'Total 200: source-A 198, source-B 2. Successful records do not establish authorization.')
        self.note['scope'] = 'Selected log only; boot image and application logs not reviewed.'
        self.note['critical_gaps'] = ['Application logs remain unreviewed.']
        self.save_note(priority='material', work_state='scoped_closed',
            closure_rationale='Bounded findings with residual uncertainty', reopen_conditions=['Application logs inspected'])

    def test_completion_gets_whole_answer_that_navigation_omits(self):
        self.counted_answer()
        prepared = self.prepare(); body = json.loads(prepared['context'])
        self.assertEqual(body['closed_index'][0]['answer_is_model_assessment'],
                         '[omitted; read detail_pointer/current claim]')
        delivered = body['delivery']['answers'][0]
        self.assertEqual(delivered['answer'], self.note['answer'])
        self.assertEqual(delivered['scope'], self.note['scope'])
        self.assertIn('Application logs remain unreviewed', json.dumps(delivered))
        self.assertEqual(self.validate(prepared)['status'], 'ready')
        self.assertLess(len(json.dumps([{'context':prepared['context'],'revision':prepared['revision']}])) , 12000)

    def test_report_selection_and_final_delivery_share_exact_answer(self):
        self.counted_answer(); state = current(self.case)
        result = bundle(self.case, state)
        selected = finalize(self.case, state, result['bundle_id'])
        self.assertEqual(result['delivery'], selected['delivery'])
        self.assertEqual(result['delivery']['answers'][0]['answer'], self.note['answer'])
        for view in compile_views(state).values():
            self.assertIn(self.note['answer'], str(view))
            self.assertIn('Application logs remain unreviewed', str(view))

    def test_large_field_is_omitted_whole_not_rewritten_or_half_asserted(self):
        self.counted_answer(); state=current(self.case)
        state['questions'][0]['answer'] = 'source-A 198; source-B 2; ' * 1000
        view=delivery_view(state,max_chars=1800)
        self.assertEqual(view['answers'],[])
        self.assertEqual(view['omitted']['answers'],1)
        self.assertNotIn('source-A', json.dumps(view))
        self.assertIn('snapshot_id',view['retrieval']['arguments'])

    def test_report_discloses_observed_scope_without_claiming_analysis(self):
        self.counted_answer(); state=current(self.case)
        inventory={'counts':{'listed':2,'partial_body':1,'body_read':0,'read_failed':1},
            'targets':[{'identity':{'path':'application.log'},'state':'listed',
                        'source_refs':[{'source_id':'synthetic-source','pointer':'/entries/0'}]}],
            'omitted':{'targets':3}}
        result=bundle(self.case,state,scope_inventory=inventory)
        manifest=json.loads(Path(result['manifest']).read_text())
        self.assertEqual(manifest['scope_inventory'],inventory)
        for view in compile_views(state,scope_inventory=inventory).values():
            text=str(view)
            self.assertIn('application.log',text)
            self.assertIn('목록·메타데이터만 관측',text)
            self.assertIn('분석 완료가 아니며',text)
            self.assertIn('생략된 대상 3개',text)

    def test_redacted_reports_do_not_return_unredacted_delivery_copy(self):
        self.counted_answer(); state=current(self.case)
        inventory={'counts':{'listed':1,'partial_body':0,'body_read':0,'read_failed':0},
            'targets':[{'identity':{'path':'source-A/application.log','member_name':'source-A.txt'},
                        'state':'listed',
                        'source_refs':[{'source_id':'synthetic-source','pointer':'/entries/0'}]}],
            'omitted':{'targets':0}}
        result=bundle(self.case,state,redact=['source-A'],scope_inventory=inventory)
        self.assertNotIn('delivery',result)
        self.assertNotIn('delivery',finalize(self.case,state,result['bundle_id']))
        manifest_text=Path(result['manifest']).read_text()
        self.assertNotIn('source-A',manifest_text)
        self.assertNotIn('source-A',json.dumps(json.loads(manifest_text)['scope_inventory']))
        self.assertEqual(inventory['targets'][0]['identity']['path'],'source-A/application.log')

    def test_same_goal_answer_change_invalidates_completion_snapshot(self):
        self.counted_answer(); prepared=self.prepare()
        self.note['answer']='Corrected: source-A 197; source-B 2; one record ambiguous.'
        self.save_note()
        self.assertEqual(self.validate(prepared)['status'],'stale')
        self.assertIn('Corrected',self.prepare()['context'])


if __name__ == '__main__':
    unittest.main()
