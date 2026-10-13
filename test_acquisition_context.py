"""Read -> citation -> observation/report/pruning, offline synthetic data only."""
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch

from forsic_plugin.image_reader import byte_preview, inspect_archive
from forsic_plugin.report_driven.host import current,find,source_record,checked
from forsic_plugin.report_driven.measurement import acquisition_context
from forsic_plugin.report_driven.model_pages import source_view
from forsic_plugin.report_driven.source_locator import retained_coverage
from forsic_plugin.context_results import summarize
import test_grounded_judgment as grounded
from test_archive_reader import Node

class AcquisitionContextTests(unittest.TestCase):
    def setUp(self):
        self.x=grounded.GroundedJudgmentTests();self.x.setUp();self.addCleanup(self.x.doCleanups)

    def binary(self):
        path=self.x.case.root/'binary.dat';path.write_bytes(b'\x00\xffuser\x00tail')
        out=self.x.call('forsic_read_bytes',{'path':'binary.dat','offset':1,'length':5})
        self.assertNotIn('error',out,out)
        return out

    def test_local_byte_read_preserves_exact_bytes_but_not_record_decode_or_whole_coverage(self):
        out=self.binary()
        self.assertEqual(base64.b64decode(self.x.case.event(out['evidence_id'])['data']['base64']),b'\xffuser')
        self.assertTrue(out['decode_lossy']);self.assertFalse(out['records_decoded'])
        self.assertEqual(out['content_bytes'],11);self.assertFalse(out['complete_file'])
        end=byte_preview(b'tail',7,11)
        self.assertIsNone(end['next_offset']);self.assertFalse(end['complete_content'])
        self.assertFalse(end['decode_lossy']);self.assertFalse(end['records_decoded'])
        past=byte_preview(b'',12,11);self.assertFalse(past['complete_content'])
        full=byte_preview(b'abc',0,3);self.assertTrue(full['complete_content'])
        self.assertFalse(full['records_decoded'])

    def test_archive_preview_reports_member_scope_without_decoding_or_execution(self):
        path=Path(self.x.temp.name)/'bundle.tar'
        with tarfile.open(path,'w') as archive:
            t=tarfile.TarInfo('not-a-command');t.size=5;archive.addfile(t,io.BytesIO(b'\x00\xffabc'))
        before=path.read_bytes()
        out=inspect_archive(Node(path),dict(action='archive_read',member_index=0,length=2))
        self.assertEqual(out['representation'],'byte_preview');self.assertFalse(out['records_decoded'])
        self.assertTrue(out['decode_lossy']);self.assertEqual(out['nul_bytes'],1)
        self.assertEqual(out['content_bytes'],5);self.assertFalse(out['complete_member'])
        self.assertEqual(path.read_bytes(),before)

    def test_direct_and_paged_citations_store_scope_and_render_without_extra_work(self):
        x=self.x;r=self.binary();orig=deepcopy(x.case.event(r['evidence_id']))
        direct=r['presented_spans'][0]['citation_ref']
        source=x.call('forsic_reporting',{'action':'source','source_id':r['evidence_id']})
        pg=x.call('forsic_reporting',dict(action='source',source_id=r['evidence_id'],
            source_version=source['source_ref']['version'],pointer='/text',offset=0,limit=4))
        self.assertNotIn('error',pg,pg)
        self.assertFalse(pg['acquisition_context']['returned_scope']['records_decoded'])
        self.assertTrue(pg['acquisition_context']['returned_scope']['decode_lossy'])
        self.assertFalse(pg['full_field'])
        for i,reference in enumerate((direct,pg['citation_ref'])):
            p=x.payload([reference])
            p['operation_id']='selection-'+str(i)
            out=x.call('forsic_question',p);self.assertNotIn('error',out,out)
            state=current(x.case);checked(state);q=find(state,'questions',out['question']['question_id'])
            claim=find(state,'claims',q['claim_refs'][0]['id']);ob=find(state,'observations',claim['source_refs'][0]['id'])
            self.assertEqual(ob['acquisition_context']['returned_scope']['content_bytes'],11)
            self.assertFalse(ob['acquisition_context']['returned_scope']['complete_file'])
            self.assertEqual(claim['semantic_review'],'not_reviewed')
            from forsic_plugin.report_driven.views import compile_views
            rendered=str(compile_views(state));self.assertIn('byte_preview',rendered);self.assertIn('records_decoded',rendered)
        self.assertEqual(x.case.event(r['evidence_id']),orig)
        for key in ('missions','tests','assessments'):self.assertEqual(state[key],[])

    def test_legacy_c1_receipt_still_valid_and_tampering_rejected(self):
        x=self.x;r=x.read();args={'action':'source','source_id':r['evidence_id']}
        src=source_record(x.case.event(r['evidence_id']));args.update(source_version=src['version'],pointer='/lines/0/text')
        legacy=source_view(src,x.case.event(r['evidence_id'])['data'],args,include_acquisition_context=False)
        from forsic_plugin.report_driven import model_pages
        with patch.object(model_pages,'source_view',return_value=legacy):
            old=x.call('forsic_reporting',args)
        from forsic_plugin.report_driven.assessment_basis import _selection
        chosen=_selection(x.case,current(x.case),'C1:'+old['evidence_id']+':0')
        self.assertEqual(chosen['literal'],r['lines'][0]['text'])
        event=x.case.event(old['evidence_id']);event['data']['text']='changed'
        with x.case.connect() as db:db.execute('update events set data=? where id=?',(json.dumps(event['data']),old['evidence_id']))
        with self.assertRaisesRegex(ValueError,'does not match'):_selection(x.case,current(x.case),'C1:'+old['evidence_id']+':0')

    def test_partial_search_context_survives_observation_and_oversized_coverage(self):
        data={'tool':'forsic_image_files','matches':[{'text':'file /synthetic/object'}],
              'coverage_complete':False,'files_skipped':2,'scan_exhausted':True,'next_cursor':None,
              'search_text':'object','scope':'x'*5000}
        context=acquisition_context(data,'/matches/0/text')
        self.assertFalse(context['returned_scope']['coverage_complete']);self.assertEqual(context['returned_scope']['files_skipped'],2)
        self.assertEqual(context['returned_scope']['representation'],'matched_text')
        self.assertNotIn('execution_success',context)
        src={'coverage':json.dumps({**data,**byte_preview(b'\xff',4,20)})}
        small=json.loads(retained_coverage(src));self.assertFalse(small['records_decoded']);self.assertTrue(small['decode_lossy'])
        self.assertFalse(small['complete_content'])

    def test_compaction_body_omission_does_not_erase_prior_presentation_or_decode_limits(self):
        r=self.binary();raw=json.dumps(r);crumb=json.loads(summarize('forsic_read_bytes',{'path':'binary.dat'},raw))
        self.assertTrue(crumb['text_fields_present_in_original_message']);self.assertTrue(crumb['body_omitted'])
        self.assertFalse(crumb['returned_scope']['records_decoded']);self.assertFalse(crumb['returned_scope']['complete_file'])
        self.assertLessEqual(len(json.dumps(crumb,ensure_ascii=False,separators=(',',':'))),2000)
        for extra in ({'lines':None},{'matches':3},{'entries':[],'lines':[]}):
            view=json.loads(summarize('forsic_read',{},json.dumps({'evidence_id':'fixture',**extra})))
            self.assertFalse(view['text_fields_present_in_original_message'])
        nested={'evidence_id':'fixture','source_ref':{'id':'original'},'page':{'items':[{'value':{'text':'observed'}}]}}
        view=json.loads(summarize('forsic_reporting',{},json.dumps(nested)))
        self.assertTrue(view['text_fields_present_in_original_message'])
        failure=json.loads(summarize('forsic_question',{},json.dumps({'error':'No mutation saved'})))
        self.assertTrue(failure['error_present']);self.assertFalse(failure['text_fields_present_in_original_message'])

if __name__=='__main__':unittest.main()
