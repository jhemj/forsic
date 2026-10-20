"""Offline exact report references; no model dispatch or external delivery."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from test_report_driven import fixture
from forsic_plugin.report_driven.host import current, invoke, ledger
from forsic_plugin.report_driven.finalization import finalize
from forsic_plugin.report_driven.report_reference import ReportReferenceError
from forsic_plugin.report_driven.views import bundle


class ReportReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.case,_,_=fixture(self.tmp.name)
        self.state=current(self.case)
        self.network=patch('socket.socket',side_effect=AssertionError('Offline test attempted network'))
        self.network.start();self.addCleanup(self.network.stop)

    def selected(self):
        return [e for e in ledger(self.case) if e['kind']=='report_finalized']

    def correct(self,**fields):
        value=deepcopy(next(e['data'] for e in reversed(ledger(self.case)) if e['kind']=='note'))
        value['revision']+=1
        value.update(fields)
        self.case.record('note',value)
        return current(self.case)

    def test_render_supplies_one_exact_ready_action_and_never_selects_delivery(self):
        report=invoke(self.case,{'action':'render','allow_partial':True})
        self.assertEqual(self.selected(),[])
        self.assertFalse(report['published'])
        call=report['actions']['finalize']
        self.assertEqual(call['tool'],'forsic_reporting')
        self.assertEqual(call['arguments'],{'action':'finalize','bundle_id':report['bundle_id']})
        result=invoke(self.case,call['arguments'])
        self.assertTrue(result['finalized']);self.assertFalse(result['published'])
        self.assertEqual(result['snapshot_id'],report['snapshot_id'])
        self.assertEqual(self.selected()[0]['data']['snapshot_id'],report['snapshot_id'])
        self.assertTrue(invoke(self.case,call['arguments'])['reused'])

    def test_current_review_does_not_require_snapshot_bookkeeping(self):
        with patch('forsic_plugin.review.review_payload',return_value={'synthetic':True}) as review:
            self.assertEqual(invoke(self.case,{'action':'review'}),{'synthetic':True})
            self.assertEqual(review.call_args.args[2],self.state['meta']['snapshot_id'])

    def test_stale_bundle_and_mixed_explicit_snapshot_fail_before_review_dispatch(self):
        report=bundle(self.case,self.state)
        now=self.correct(answer='A retained correction changes the current answer.')
        for extra,status in (({},'stale_report_reference'),
                             ({'snapshot_id':now['meta']['snapshot_id']},'report_snapshot_mismatch')):
            with self.subTest(extra=extra), patch('forsic_plugin.report_driven.independent_gate.context') as review:
                with self.assertRaises(ReportReferenceError) as caught:
                    invoke(self.case,{'action':'finalize','bundle_id':report['bundle_id'],**extra})
                review.assert_not_called()
                result=caught.exception.result
                self.assertEqual(result['delivery_status'],status)
                self.assertEqual(result['recovery']['arguments'],
                    {'action':'render','question_ids':[q['id'] for q in self.state['questions']]})
                self.assertFalse(result['published']);self.assertEqual(self.selected(),[])
        replacement=invoke(self.case,{**result['recovery']['arguments'],'allow_partial':True})
        self.assertNotEqual(replacement['bundle_id'],report['bundle_id'])
        self.assertEqual(self.selected(),[])
        self.assertTrue(invoke(self.case,replacement['actions']['finalize']['arguments'])['finalized'])

    def test_explicit_snapshot_asserts_the_bundle_and_cannot_be_ignored(self):
        report=bundle(self.case,self.state)
        with self.assertRaises(ReportReferenceError) as caught:
            invoke(self.case,{'action':'finalize','bundle_id':report['bundle_id'],'snapshot_id':'0'*64})
        self.assertEqual(caught.exception.result['error_code'],'report_snapshot_mismatch')
        self.assertEqual(self.selected(),[])

    def test_nonmaterial_native_projection_change_keeps_exact_bundle_snapshot(self):
        report=bundle(self.case,self.state)
        now=deepcopy(self.state)
        now['meta'].update(snapshot_id='f'*64,generated_at='2026-10-19T00:00:00+00:00')
        now['status']['execution']='running'
        now['status']['explicit_limitations'][0]='Synthetic native goal bookkeeping changed.'
        with patch('forsic_plugin.report_driven.host.current',return_value=now):
            result=finalize(self.case,now,report['bundle_id'])
        self.assertTrue(result['finalized'])
        self.assertEqual(result['snapshot_id'],report['snapshot_id'])
        self.assertNotEqual(result['snapshot_id'],now['meta']['snapshot_id'])

    def test_manifest_tampering_cannot_change_scope_or_hide_changed_artifacts(self):
        for field in ('case_id','question_ids','files','manifest_identity'):
            with self.subTest(field=field):
                report=bundle(self.case,current(self.case))
                path=Path(report['manifest']);original=path.read_bytes()
                manifest=json.loads(original)
                if field=='case_id':manifest[field]='OTHER-CASE'
                elif field=='question_ids':manifest[field]=[]
                elif field=='manifest_identity':manifest.pop(field)
                else:
                    artifact=path.parent/'integrated.html';artifact.write_text('tampered')
                    record=next(f for f in manifest['files'] if f['name']==artifact.name)
                    record.update(bytes=artifact.stat().st_size,sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ReportReferenceError,'Report reference is invalid'):
                    invoke(self.case,{'action':'finalize','bundle_id':report['bundle_id']})
                self.assertEqual(self.selected(),[])
                # Restore/delete only synthetic artifacts before the next case.
                shutil.rmtree(path.parent)

    def test_missing_or_changed_private_snapshot_is_not_rebound_to_current(self):
        report=bundle(self.case,self.state)
        path=self.case.output/('report-state-'+report['snapshot_id']+'.json')
        path.write_text('{}')
        with self.assertRaises(ReportReferenceError):
            invoke(self.case,{'action':'finalize','bundle_id':report['bundle_id']})
        self.assertEqual(self.selected(),[])

    def test_bundle_from_another_case_is_refused_even_with_shared_output_copy(self):
        report=bundle(self.case,self.state)
        with tempfile.TemporaryDirectory() as other:
            foreign,_,_=fixture(other)
            foreign.config['case_id']='SYN-foreign'
            shutil.copytree(Path(report['manifest']).parent,foreign.output/Path(report['manifest']).parent.name)
            with self.assertRaises(ReportReferenceError):
                invoke(foreign,{'action':'finalize','bundle_id':report['bundle_id']})
            self.assertFalse(any(e['kind']=='report_finalized' for e in ledger(foreign)))

    def test_goal_and_conversation_scope_are_checked_before_review(self):
        state=self.correct(goal_id='goal-one',session_id='conversation-one')
        args={'_goal_id':'goal-one','_session_id':'conversation-one'}
        report=invoke(self.case,{'action':'render','allow_partial':True,**args})
        for key in ('_goal_id','_session_id'):
            with self.subTest(key=key), patch('forsic_plugin.report_driven.independent_gate.context') as review:
                with self.assertRaises(ReportReferenceError) as caught:
                    invoke(self.case,{'action':'finalize','bundle_id':report['bundle_id'],**args,key:'other'})
                self.assertEqual(caught.exception.result['delivery_status'],'report_scope_mismatch')
                review.assert_not_called()
        self.assertEqual(self.selected(),[])
        self.assertTrue(invoke(self.case,{'action':'finalize','bundle_id':report['bundle_id'],**args})['finalized'])

    def test_stale_masked_report_recovery_preserves_exact_masking_terms(self):
        report=bundle(self.case,self.state,redact=['maintenance','process'])
        self.correct(answer='A new answer requires another masked rendering.')
        with self.assertRaises(ReportReferenceError) as caught:
            invoke(self.case,{'action':'finalize','bundle_id':report['bundle_id']})
        call=caught.exception.result['recovery']['arguments']
        self.assertEqual(call['redact'],['maintenance','process'])
        recovered=invoke(self.case,{**call,'allow_partial':True})
        self.assertTrue(json.loads(Path(recovered['manifest']).read_text())['redacted'])
        self.assertNotIn('delivery',recovered)
        self.assertEqual(self.selected(),[])

    def test_legacy_bundle_without_new_manifest_identity_remains_supported(self):
        report=bundle(self.case,self.state)
        path=Path(report['manifest']);manifest=json.loads(path.read_text())
        for key in ('manifest_identity','render_options_sha256','report_scope'):
            manifest.pop(key,None)
        (path.parent/'.render-options.json').unlink()
        path.write_text(json.dumps(manifest))
        self.assertTrue(invoke(self.case,{'action':'finalize','bundle_id':report['bundle_id'],
            'snapshot_id':report['snapshot_id']})['finalized'])


if __name__=='__main__':
    unittest.main()
