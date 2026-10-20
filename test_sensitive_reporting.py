"""Synthetic local report originals and remote-delivery boundaries; no model/network."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from docx import Document
from test_report_driven import fixture, worked
from forsic_plugin.report_driven.host import current, invoke
from forsic_plugin.report_driven.views import bundle, artifact
from telegram_reports import ReportDelivery


class SensitiveReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.case,_,_=fixture(self.root)
        self.state=worked(self.case)
        source=self.state['sources'][0]
        self.original='<script>synthetic-secret&한글</script>'
        self.appendix={'schema':'forsic-sensitive-appendix-1',
            'entries':[{'finding_id':'SF-synthetic','input_id':'SI-synthetic',
                'source_ref':{'id':source['id'],'version':source['version'],'pointer':'/lines/0/text'},
                'original_location':{'path':'synthetic.txt','coordinate_space':'original_file_bytes'},
                'excerpt':self.original,'excerpt_byte_start':20,'excerpt_byte_end':66,
                'match_byte_start':28,'match_byte_end':44,'encoding':'utf-8','source_role':'original_evidence'}],
            'total_findings':50,'returned_findings':1,'omitted_findings':49,'omitted_bytes':9000,
            'limitations':['Synthetic bounded sample.'],'contains_sensitive_originals':True}

    def tearDown(self):self.tmp.cleanup()

    def render(self,**options):
        def hydrate(case,source_ids):
            saved=case.output/('report-state-'+self.state['meta']['snapshot_id']+'.json')
            self.assertTrue(saved.is_file(),'Safe state must precede original hydration')
            self.assertNotIn(self.original,saved.read_text())
            self.assertEqual(set(source_ids),{s['id'] for s in self.state['sources']})
            return deepcopy(self.appendix)
        with patch('forsic_plugin.sensitive_policy.report_appendix',side_effect=hydrate):
            return bundle(self.case,self.state,**options)

    def test_integrated_html_word_originals_escaped_with_omissions_safe_state(self):
        before=deepcopy(current(self.case))
        rendered=self.render()
        manifest=json.loads(Path(rendered['manifest']).read_text())
        self.assertTrue(rendered['local_only'])
        self.assertTrue(manifest['local_only'])
        self.assertNotIn(self.original,json.dumps(rendered,ensure_ascii=False))
        self.assertNotIn(self.original,json.dumps(manifest,ensure_ascii=False))
        html=artifact(self.case,rendered['bundle_id'],'integrated.html').read_text()
        self.assertIn('&lt;script&gt;synthetic-secret&amp;한글&lt;/script&gt;',html)
        self.assertNotIn('<script>synthetic-secret',html)
        self.assertIn('생략 49개',html)
        self.assertIn('9000바이트',html)
        self.assertIn('발췌 [20,66)',html)
        self.assertIn('일치 [28,44)',html)
        self.assertIn('원래 위치',html)
        self.assertLess(html.index('부록 D.'),html.index('synthetic-secret'))
        from forsic_plugin.report_driven.word_export import export_word
        document=Document(export_word(self.case,rendered['bundle_id'])['path'])
        text='\n'.join(p.text for p in document.paragraphs)+'\n'+'\n'.join(c.text for t in document.tables for r in t.rows for c in r.cells)
        self.assertIn(self.original,text)
        self.assertIn('생략 49개',text)
        self.assertEqual(current(self.case),before)
        saved=self.case.output/('report-state-'+self.state['meta']['snapshot_id']+'.json')
        self.assertNotIn('synthetic-secret',saved.read_text())

    def test_all_omitted_and_invalid_raw_are_visible_without_leaking_originals(self):
        self.appendix.update(entries=[],returned_findings=0,omitted_findings=50,
            omitted_bytes=9046,contains_sensitive_originals=False,counts_are_lower_bounds=True,
            unretained_findings=None,limitations=['원문 무결성을 검증하지 못했습니다.'])
        result=self.render()
        self.assertNotIn('local_only',result)
        html=Path(result['files'][0]).read_text()
        self.assertIn('보존·관측 후보 50개',html)
        self.assertIn('생략 50개',html)
        self.assertIn('원문 미수록',html)
        self.assertIn('후보 수는 관측한 하한',html)
        self.assertIn('원문 미보존 후보: 미상',html)
        self.assertIn('원문 무결성을 검증하지 못했습니다.',html)
        self.assertNotIn('synthetic-secret',html)
        self.assertNotIn('synthetic-secret',json.dumps(result,ensure_ascii=False))

    def test_no_selected_originals_keeps_regular_delivery_policy(self):
        self.appendix.update(entries=[],total_findings=0,returned_findings=0,omitted_findings=0,
                             omitted_bytes=0,limitations=[],contains_sensitive_originals=False)
        result=self.render()
        self.assertNotIn('local_only',result)
        self.assertNotIn('민감정보 후보 원문 별첨',Path(result['files'][0]).read_text())

    def test_local_finalization_does_not_queue_telegram_and_upload_guard_blocks(self):
        result=self.render();since=time.time()
        delivery=ReportDelivery(self.root/'delivery.json',self.case.output,
            self.case.config['case_id'],-123,8,since)
        finalized=invoke(self.case,{'action':'finalize','snapshot_id':self.state['meta']['snapshot_id'],
                                   'bundle_id':result['bundle_id']})
        self.assertTrue(finalized['finalized'])
        send=AsyncMock()
        asyncio.run(delivery.tick(send));send.assert_not_awaited()
        self.assertFalse(delivery.state['jobs'])
        self.assertTrue(delivery.state['rejected_events'])
        # Also protect jobs queued by an earlier process, before discovery policy.
        manifest=json.loads(Path(result['manifest']).read_text())
        file=next(f for f in manifest['files'] if f['name']=='integrated.html')
        delivery.state['jobs']['old']={**file,'bundle_id':result['bundle_id'],
            'snapshot_id':self.state['meta']['snapshot_id'],'status':'queued'}
        delivery.save()
        asyncio.run(delivery.tick(send));send.assert_not_awaited()
        self.assertEqual(delivery.state['jobs']['old']['status'],'blocked')


if __name__=='__main__':unittest.main()
