"""Native dispatch, passive UI and report IOC integration: synthetic offline only."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_report_driven import fixture
from forsic_plugin.report_driven.host import current, review_material
from forsic_plugin.report_driven.views import bundle, bundle_stale, compile_views


class IndicatorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.case,_,_=fixture(self.temp.name)
        (self.case.root/'ioc.log').write_text('peer=192.0.2.4\n')
        self.source=json.loads(self.case.invoke('forsic_read',{'path':'ioc.log'}))['evidence_id']
        self.args=dict(action='upsert',type='ip',value='192.0.2.4',source_refs=[dict(source_id=self.source,pointer='/lines/0/text',literal='192.0.2.4',byte_start=5)],summary='관측한 접속 대상; 성공 여부 미확인')

    def tearDown(self): self.temp.cleanup()

    def save(self, **extra):
        out=json.loads(self.case.invoke('forsic_indicators',{**self.args,**extra}))
        self.assertNotIn('error',out)
        return out['indicator']

    def test_native_register_and_reports_share_material(self):
        row=self.save();s=current(self.case)
        self.assertEqual(s['indicators'][0]['indicator_id'],row['indicator_id'])
        self.assertIn('192.0.2.4',json.dumps(compile_views(s),ensure_ascii=False))
        review=review_material(self.case,s)
        self.assertIn('192.0.2.4',json.dumps(review,ensure_ascii=False))
        result=bundle(self.case,s)
        old=json.loads(Path(result['manifest']).read_text())
        self.save(revision=row['revision'],status='withdrawn',summary='다른 업무 접속으로 정정')
        self.assertTrue(bundle_stale(self.case,old))
        self.assertNotEqual(json.dumps(review,sort_keys=True),json.dumps(review_material(self.case,current(self.case)),sort_keys=True))

    def test_passive_api_list_export_no_events_and_no_model(self):
        from forsic_plugin.dashboard import plugin_api
        self.save();before=self.case.db.read_bytes()
        with patch.object(plugin_api,'selected_case',return_value=self.case):
            page=plugin_api.indicator_list(self.case.config['case_id'])
            self.assertEqual(page['total'],1)
            for fmt in ('json','csv'):
                response=plugin_api.indicator_export(fmt,self.case.config['case_id'])
                self.assertIn(b'192.0.2.4',response.body)
                self.assertIn('attachment',response.headers['content-disposition'])
        self.assertEqual(before,self.case.db.read_bytes())

    def test_native_cursor_rejects_model_injected_state(self):
        from forsic_plugin.literal_search import fingerprint
        (self.case.root/'search.log').write_text('ordinary\nneedle\n')
        args=dict(path='search.log',text='needle',max_bytes=3)
        page=json.loads(self.case.invoke('forsic_search',args));cursor=page['next_cursor']
        cursor['state']['matched']=True;cursor['state']['preview']='fabricated needle'
        cursor['checksum']=fingerprint({k:v for k,v in cursor.items() if k!='checksum'})
        bad=json.loads(self.case.invoke('forsic_search',{**args,'cursor':cursor}))
        self.assertIn('unchanged next_cursor',bad['error'])
        good=json.loads(self.case.invoke('forsic_search',{**args,'cursor':page['next_cursor']}))
        # The in-memory page was deliberately changed too; recover the exact saved cursor.
        saved=self.case.event(page['evidence_id'])['data']['next_cursor']
        good=json.loads(self.case.invoke('forsic_search',{**args,'cursor':saved}))
        self.assertNotIn('error',good)


if __name__=='__main__': unittest.main()
