"""Offline unified artifacts and immutable historical-reader compatibility."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.report_driven.finalization import report_files
from forsic_plugin.report_driven.host import current, digest
from forsic_plugin.report_driven.word_export import export_word
from forsic_plugin.report_driven.views import artifact, bundle, compile_views, report_material
from test_report_driven import fixture
from test_reader_report_projection import visible_blocks


class IntegratedReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.case,_,_=fixture(self.tmp.name);self.state=current(self.case)

    def test_one_view_with_seven_axes_and_lossless_shared_appendix(self):
        before=deepcopy(self.state)
        views=compile_views(self.state)
        self.assertEqual(set(views),{'integrated'})
        view=views['integrated'];body=[p for p in view['pages'] if p['section']=='body']
        self.assertEqual(len(body),3)
        text=visible_blocks(body)
        for label in ('사고 시점','최초 진입','주요 행위','개인정보','외부 영향','판단의 핵심 근거','주요 사건 경과'):
            self.assertIn(label,text)
        self.assertIn('보고용 판단 선언',text)
        self.assertNotIn(self.state['questions'][0]['answer'],text)
        self.assertIn(self.state['questions'][0]['answer'],visible_blocks(view['pages'][3:]))
        for title in ('남은 조사와 재개 조건','판단의 근거와 중요한 한계'):
            self.assertNotIn(title,[p['title'] for p in body])
        self.assertEqual(self.state,before)

    def test_real_bundle_defaults_to_html_with_explicit_same_snapshot_word_export(self):
        with patch('socket.socket',side_effect=AssertionError('network forbidden')):
            result=bundle(self.case,self.state)
        manifest,documents=report_files(self.case.output,result['bundle_id'],self.case.config['case_id'])
        self.assertEqual({f['name'] for f in documents},{'integrated.html'})
        self.assertEqual({f['name'] for f in manifest['files']},{'integrated.html','selected-evidence.zip'})
        self.assertFalse((Path(result['manifest']).parent/'integrated.docx').exists())
        self.assertFalse((self.case.output/'.word-exports').exists())
        manifest_bytes = Path(result['manifest']).read_bytes()
        word = export_word(self.case, result['bundle_id'])
        self.assertTrue(word['path'].is_file())
        self.assertEqual(word['snapshot_id'], manifest['snapshot_id'])
        self.assertEqual(hashlib.sha256(word['path'].read_bytes()).hexdigest(), word['sha256'])
        self.assertEqual(Path(result['manifest']).read_bytes(), manifest_bytes)
        self.assertEqual(manifest['snapshot_id'],manifest['evidence_export']['snapshot_id'])
        self.assertEqual(artifact(self.case,result['bundle_id'],'integrated.html').name,'integrated.html')
        with self.assertRaises(ValueError):artifact(self.case,result['bundle_id'],'executive.html')
        before={p:Path(p).read_bytes() for p in result['files']}
        self.assertEqual(bundle(self.case,self.state)['bundle_id'],result['bundle_id'])
        self.assertEqual(before,{p:Path(p).read_bytes() for p in before})

    def historical(self,names):
        # Real old shape, independently hashed artifacts; never regenerated.
        files=[]
        payloads={name:('historical '+name).encode() for name in names}
        for name,value in payloads.items():files.append(dict(name=name,sha256=hashlib.sha256(value).hexdigest(),bytes=len(value)))
        manifest=dict(schema='forsic-report-bundle-1',case_id=self.case.config['case_id'],
                      snapshot_id=self.state['meta']['snapshot_id'],files=files,manifest_identity='sha256-v1')
        ident=digest(manifest);manifest['bundle_id']=ident
        root=self.case.output/('report-bundle-'+ident);root.mkdir()
        for name,value in payloads.items():(root/name).write_bytes(value)
        (root/'manifest.json').write_text(json.dumps(manifest))
        return root,ident,payloads

    def test_historical_integrated_pair_remains_immutable_and_readable(self):
        root,ident,payloads=self.historical(('integrated.html','integrated.docx'))
        _,documents=report_files(self.case.output,ident,self.case.config['case_id'])
        self.assertEqual({f['name'] for f in documents},set(payloads))
        self.assertEqual(payloads,{name:(root/name).read_bytes() for name in payloads})

    def test_historical_four_artifacts_remain_immutable_and_readable(self):
        root,ident,payloads=self.historical(('executive.html','executive.docx','practitioner.html','practitioner.docx'))
        _,documents=report_files(self.case.output,ident,self.case.config['case_id'])
        self.assertEqual({f['name'] for f in documents},set(payloads))
        self.assertEqual(payloads,{name:(root/name).read_bytes() for name in payloads})

    def test_mixed_reader_generations_are_rejected(self):
        _,ident,_=self.historical(('integrated.html','integrated.docx','executive.html','executive.docx'))
        with self.assertRaisesRegex(ValueError,'Invalid report artifacts'):
            report_files(self.case.output,ident,self.case.config['case_id'])

    def test_optional_empty_report_contract_keeps_historical_content_identity(self):
        old=deepcopy(self.state)
        for question in old['questions']:question.pop('report_slots',None)
        self.assertEqual(report_material(old),report_material(self.state))


if __name__=='__main__':unittest.main()
