"""Retained facts and correction navigation survive context reduction, offline."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.context_results import summarize
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.current_judgment import navigation_view
from forsic_plugin.investigation_context import recent_observed_scope, _report_context
from test_report_driven import fixture


class ProjectionRecoveryTests(unittest.TestCase):
    def test_report_for_selected_question_does_not_cover_other_current_questions(self):
        from forsic_plugin.report_driven.views import bundle
        from forsic_plugin.notes import note
        with tempfile.TemporaryDirectory() as root:
            case,records,_=fixture(root)
            note(case,{'action':'save','question':'Did the process finish?',
                'answer':'Creation does not establish completion.',
                'evidence_ids':[records[1]['evidence_id']],
                'critical_gaps':['No completion record reviewed']})
            state=host.current(case)
            selected=state['questions'][0]['id']
            bundle(case,state,question_ids=[selected])
            view=_report_context({'case':case,'case_id':state['meta']['case_id']},state,state['questions'])
            self.assertEqual(view['current_questions_in_report'],1)
            self.assertEqual(view['current_questions_omitted'],len(state['questions'])-1)
            self.assertGreater(view['current_questions_omitted'],0)
            self.assertFalse(view['artifact_is_completion'])

    def test_uncertain_commit_and_execution_lookup_preserve_exact_recovery(self):
        call={'tool':'forsic_reporting','arguments':{'action':'execution','started_id':'start-A'}}
        bodies=[
            {'error':'unconfirmed commit '+'detail '*600, 'error_code':'result_commit_unknown',
             'execution_status':'unknown','execution_call':call},
            {'evidence_id':'result-A','error':'timeout','execution_call':call,
             'execution':{'state':'completion_unknown','termination_confirmed':False,
                          'cleanup':{'confirmed':False,'error_type':'TimeoutExpired'}}},
            {'execution_started_id':'start-A','completion':'unknown','result_commit':'not_observed',
             'delivery':'not_observed','process_liveness':'not_observed','results':[]},
        ]
        for body in bodies:
            with self.subTest(body=body):
                text=summarize('forsic_reporting',{'path':'x'*2000},json.dumps(body))
                self.assertIsNotNone(text)
                result=json.loads(text)
                self.assertEqual(result['execution_lookup'],call)
                self.assertLessEqual(len(text),2000)
                if 'execution' in body:
                    self.assertFalse(result['execution']['termination_confirmed'])
                    self.assertEqual(result['execution']['state'],'completion_unknown')
                if 'completion' in body:
                    self.assertEqual(result['completion'],'unknown')
                    self.assertEqual(result['process_liveness'],'not_observed')

    def test_retained_source_lookup_uses_exact_record_without_full_replay(self):
        with tempfile.TemporaryDirectory() as root:
            case,results,_=fixture(root)
            source=host.current(case)['sources'][0]
            args={'action':'source','source_id':source['id'],'source_version':source['version']}
            before=host.invoke(case,args)
            original=deepcopy(case.event(source['id']))
            with patch.object(host,'current',side_effect=AssertionError('unrelated full replay')):
                self.assertEqual(host.invoke(case,args),before)
                with self.assertRaisesRegex(ValueError,'version'):
                    host.invoke(case,{**args,'source_version':'stale','pointer':'/lines'})
                wrong=case.record('note',{'note_id':'not-a-source'},'other')
                with self.assertRaises(ValueError):
                    host.invoke(case,{'action':'source','source_id':wrong})
            self.assertEqual(case.event(source['id']),original)

    def test_compacted_scope_includes_verified_lineage_not_unrelated_session(self):
        with tempfile.TemporaryDirectory() as root:
            case,_,_=fixture(root)
            wanted=[]
            for sid in ('parent','compressed-child','unrelated'):
                eid=case.record('tool_result',{'tool':'forsic_read_bytes','path':'fixture',
                    'representation':'byte_preview','records_decoded':False,'byte_start':128,
                    'byte_end':191,'complete_file':False},sid)
                if sid!='unrelated':wanted.append(eid)
            facts=recent_observed_scope({'case':case,'conversation_id':'parent',
                'session_lineage':['compressed-child','parent']},host.current(case))
            self.assertEqual({f['source_id'] for f in facts},set(wanted))
            self.assertTrue(all(f['observed']['records_decoded'] is False for f in facts))

    def test_current_navigation_keeps_counterevidence_and_stable_question_lookup(self):
        with tempfile.TemporaryDirectory() as root:
            case,_,_=fixture(root)
            state=host.current(case);question=state['questions'][1] if state['questions'][0]['id']=='Q-intake' else state['questions'][0]
            claim=next(c for c in state['claims'] if c['id']=='F-'+question['id'][2:])
            claim.update(counterevidence_refs=[host.ref('source',state['sources'][1])])
            before=deepcopy(state)
            view=navigation_view(state,question)
            self.assertEqual(view['judgments'][0]['counterevidence_refs'],claim['counterevidence_refs'])
            self.assertEqual(view['details_call'],{'tool':'forsic_reporting','arguments':{'action':'state','question_id':question['id']}})
            self.assertEqual(state,before)


if __name__=='__main__':unittest.main()
