"""Explicit author-selected detail roles, without model/network/runtime calls."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from test_report_retained_regions import synthetic_fields, authored_case, document_regions, W, NS
from forsic_plugin.report_driven import render_views as renderer
from forsic_plugin.report_driven.narrative import _draft_hash, status, _review_document, ReportAuthoringError
from forsic_plugin.report_driven.template_contract import validate_group, TEMPLATE_VERSION
from forsic_plugin.report_driven.reader_projection import project_authored, cite_pages, evidence_references, reference_appendix
from forsic_plugin.report_driven.host import invoke, current
from forsic_plugin.report_driven.views import redact_view


def sample_fields():
    fields=synthetic_fields()
    for group,field in fields.items():
        for entry in field['entries']:
            entry['cell_details']=['설명 <tag> & 근거: 공통 식별자는 제공되지 않음']*len(entry['cells'])
    fields['report_identity']['entries'][0]['cells']=['미확인','192.0.2.10 (합성 예시)','미확인']
    fields['report_identity']['entries'][0]['cell_details']=['호스트 식별 기록 미제공','문서용 예약 주소·실제 증거 아님','이미지 채증 기록 미제공']
    fields['overall_judgment']['entries'][0]['cells']=['요청과 생성 각각 관측·동일 작업 연결 미확인']
    return fields


def sample_view(fields):
    return dict(title='합성 보고서',security='합성 예시',data_mode='synthetic',snapshot_revision='synthetic-1',
                template_contract='forsic-integrated-v2',pages=project_authored({},fields))


class HierarchyTests(unittest.TestCase):
    def test_detail_shape_hash_and_legacy_entries(self):
        field=synthetic_fields()['major_findings']
        for e in field['entries']:e['basis_keys']=['Q1']
        validate_group('major_findings',field)
        original=_draft_hash({'major_findings':field})
        field['entries'][0]['cell_details']=['','설명']
        validate_group('major_findings',field)
        self.assertNotEqual(original,_draft_hash({'major_findings':field}))
        for bad in ([],['only one'],['a','b','c'],['a',None]):
            field['entries'][0]['cell_details']=bad
            with self.assertRaises(ValueError):validate_group('major_findings',field)
        self.assertEqual(TEMPLATE_VERSION,'forsic-integrated-v2-reader4')

    def test_projection_escape_mask_and_unavailable_reason(self):
        fields=sample_fields();entry=fields['scope']['entries'][0]
        entry.update(status='unavailable',unavailable_reason='중요 상태 설명',cells=['미확인'])
        v=sample_view(fields);pages=v['pages']
        blocks=[b for p in pages for b in p['blocks']]
        metadata=next(b for b in blocks if b.get('region')=='metadata')
        self.assertEqual(metadata['rows'][0][1],'192.0.2.10 (합성 예시)')
        self.assertIn('문서용',metadata['cell_details'][0][1])
        scope=next(b for b in blocks if b.get('region')=='scope')
        self.assertIn('중요 상태 설명',scope['text'])
        rendered=renderer.render_html(v)
        self.assertIn('&lt;tag&gt; &amp;',rendered);self.assertNotIn('<tag>',rendered)
        self.assertIn('authored-overall',rendered)
        self.assertIn('@media screen{table[data-region="major_findings"] td>.authored-lead',rendered)
        self.assertIn('td>.authored-lead{display:inline}',rendered)
        self.assertIn('td>.authored-detail{margin-top:2pt}',rendered)
        masked=redact_view(v,['공통 식별자'])
        self.assertNotIn('공통 식별자',str(masked))
        entry['cells']=['미확인: 중요 상태 설명']
        scope=next(b for p in sample_view(fields)['pages'] for b in p['blocks'] if b.get('region')=='scope')
        self.assertEqual(scope['text'].count('중요 상태 설명'),1)
        entry['cells']=['미확인'];entry['cell_details']=['중요 상태 설명']
        scope=next(b for p in sample_view(fields)['pages'] for b in p['blocks'] if b.get('region')=='scope')
        self.assertEqual(scope['text'],'미확인')
        self.assertEqual(scope['detail'],'중요 상태 설명')

    def test_word_roles_package_and_historical_profile(self):
        v=sample_view(sample_fields())
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'new.docx';renderer.render_docx(v,path)
            root,regions=document_regions(path)
            def sizes(region,index):
                p=regions['report.'+region].findall('.//w:p',NS)[index]
                return [(r.find('w:rPr/w:sz',NS).get(W+'val'),r.find('w:rPr/w:b',NS).get(W+'val','1')) for r in p.findall('.//w:r',NS)]
            self.assertEqual(sizes('overall_judgment',1),[('28','1')])
            self.assertEqual(sizes('overall_judgment',2),[('20','0')])
            detail=regions['report.overall_judgment'].findall('.//w:p',NS)[2]
            self.assertEqual(detail.find('.//w:color',NS).get(W+'val'),'5F6368')
            # Metadata: header row first, then value and visible detail paragraphs.
            ps=regions['report.metadata'].findall('.//w:tr',NS)[1].findall('w:tc',NS)[1].findall('w:p',NS)
            self.assertEqual(ps[0].find('.//w:sz',NS).get(W+'val'),'22')
            self.assertEqual(ps[1].find('.//w:sz',NS).get(W+'val'),'20')
            with ZipFile(renderer.TEMPLATE_PATH) as source,ZipFile(path) as output:
                for name in source.namelist():
                    if name not in {'word/document.xml','word/header1.xml','word/footer1.xml','docProps/core.xml'}:
                        self.assertEqual(source.read(name),output.read(name),name)
            old=deepcopy(renderer.STYLE);old['readability'].pop('authored_hierarchy')
            historical=sample_view(synthetic_fields());a=Path(td)/'a.docx';b=Path(td)/'b.docx'
            renderer.render_docx(historical,a,style_profile=old)
            for page in historical['pages']:
                for block in page['blocks']:block.pop('authored_hierarchy',None)
            renderer.render_docx(historical,b,style_profile=old)
            with ZipFile(a) as za,ZipFile(b) as zb:self.assertEqual(za.read('word/document.xml'),zb.read('word/document.xml'))

    def test_details_in_cas_review_packets_and_citations(self):
        with tempfile.TemporaryDirectory() as td:
            case,state=authored_case(td)
            packet=invoke(case,dict(action='compose',section='overall_judgment'))
            old_ref=packet['save_contract']['edit_ref']
            draft={'entries':deepcopy(status(state,None)['fields']['overall_judgment']['entries'])}
            draft['entries'][0].pop('citation_refs',None)
            draft['entries'][0]['cell_details']=['새 설명 <tag> & 완전 보존']
            invoke(case,dict(action='compose',section='overall_judgment',edit_ref=old_ref,operation_id='detail-change',draft=draft))
            after=status(current(case),None)
            self.assertNotEqual(after['review_status'],'complete')
            self.assertIn('새 설명',str(_review_document(after,current(case))))
            with self.assertRaises(ReportAuthoringError):
                invoke(case,dict(action='compose',section='overall_judgment',edit_ref=old_ref,operation_id='detail-stale',draft=draft))
            refs=evidence_references(state)
            ref=next(r for r in refs if r['record'] is not None)
            citation={k:ref[k] for k in ('kind','id','version')}
            fields=sample_fields()
            fields['report_identity']['entries'][0]['citation_refs']=[citation]
            pages=cite_pages(project_authored(state,fields),state)
            metadata=next(b for p in pages for b in p['blocks'] if b.get('region')=='metadata')
            self.assertIn(ref['label'],metadata['cell_details'][0][0])
            self.assertEqual(str(metadata).count(ref['label']),1)
            self.assertEqual(metadata['rows'][0][1],'192.0.2.10 (합성 예시)')
            self.assertNotIn(ref['label'],metadata['cell_details'][0][1])
            fields['report_identity']['entries'][0]['cell_details'][0]=''
            fields['overall_judgment']['entries'][0]['citation_refs']=[citation]
            pages=cite_pages(project_authored(state,fields),state)
            metadata=next(b for p in pages for b in p['blocks'] if b.get('region')=='metadata')
            self.assertEqual(metadata['cell_details'][0][0].strip(),ref['label'])
            self.assertNotIn(ref['label'],str(metadata['rows']))
            overall=next(b for p in pages for b in p['blocks'] if b.get('region')=='overall_judgment')
            self.assertEqual(str(overall).count(ref['label']),1)
            self.assertIn(ref['label'],overall['detail'])
            self.assertNotIn(ref['label'],overall['text'])
            v=sample_view(fields);v['pages']=[*pages,reference_appendix(state)]
            markup=renderer.render_html(v)
            self.assertIn('href="#'+renderer.bookmark_id(ref['label'])+'"',markup)


if __name__=='__main__':unittest.main()
