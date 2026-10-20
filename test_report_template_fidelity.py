"""Offline regression contracts for retained-template rendering and lossless details."""
from collections import Counter
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from forsic_plugin.report_driven import render_views as renderer
from forsic_plugin.report_driven.host import current
from forsic_plugin.report_driven.views import _template_appendices, bundle
from test_report_driven import fixture

NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
W='{'+NS['w']+'}'


class HTMLLinks(HTMLParser):
    def __init__(self, markup):
        super().__init__();self.ids=[];self.links=[];self.text=[];self.feed(markup)
    def handle_data(self, data):self.text.append(data)
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.append(attrs['id'])
        if attrs.get('href','').startswith('#'):self.links.append(attrs['href'][1:])


def view_fixture():
    table={'kind':'table','variant':'summary','headers':['구분','핵심 확인내용','판단','근거'],
           'rows':[['주요 행위','명령 관측; 실행 성공 미입증','추정','근거 1']],
           'widths':[25]*4,'row_ids':['근거 1']}
    return dict(reader='integrated',data_mode='synthetic',title='정보시스템 침해사고 조사 결과',
                security='내부 검토용',report_id='synthetic-report',snapshot_revision='synthetic-snapshot',
                pages=[dict(id='I01',title='정보시스템 침해사고 조사 결과',section='body',blocks=[
                    {'kind':'note','text':'중요 유보: 명령 문자열은 실행 성공의 증거가 아님'},
                    {'kind':'callout','label':'종합 판단','text':'근거 1에 관한 관측; 행위자 귀속 미확인'},
                    table,{'kind':'paragraph','text':'반론은 근거 1과 함께 보존'}]),
                    dict(id='appendix-A',title='부록 A. 판단축별 조사 상세',section='appendix',blocks=[
                        {'kind':'heading','id':'old-detail','text':'과거 상세','level':2},
                        deepcopy(table),{'kind':'note','text':'표 한계: 수집 이전 구간 미검토'}])])


class ReportTemplateFidelityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'report.docx'
        self.view=view_fixture();self.before=deepcopy(self.view)
        with patch('socket.socket',side_effect=AssertionError('network forbidden')):
            renderer.render_docx(self.view,self.path)
        self.package=ZipFile(self.path);self.addCleanup(self.package.close)
        self.document=ET.fromstring(self.package.read('word/document.xml'))

    def test_template_geometry_styles_and_every_opaque_part_are_preserved(self):
        changed={'word/document.xml','word/header1.xml','word/footer1.xml','docProps/core.xml'}
        with ZipFile(renderer.TEMPLATE_PATH) as template:
            self.assertEqual(set(template.namelist()),set(self.package.namelist()))
            for name in template.namelist():
                if name not in changed:
                    with self.subTest(part=name):self.assertEqual(template.read(name),self.package.read(name))
            source=ET.fromstring(template.read('word/document.xml'))
            self.assertEqual(ET.tostring(source.find('w:body/w:sectPr',NS)),
                             ET.tostring(self.document.find('w:body/w:sectPr',NS)))
        self.assertEqual(self.view,self.before)

    def test_title_rule_page_field_and_white_on_dark_grayscale_headers(self):
        styles=ET.fromstring(self.package.read('word/styles.xml'))
        title=next(s for s in styles.findall('w:style',NS) if s.get(W+'styleId')=='Title')
        border=title.find('w:pPr/w:pBdr/w:bottom',NS)
        self.assertIsNotNone(border);self.assertEqual(border.get(W+'val'),'single')
        footer=ET.fromstring(self.package.read('word/footer1.xml'))
        self.assertTrue(any(e.get(W+'instr','').strip()=='PAGE' for e in footer.findall('.//w:fldSimple',NS)))
        header_rows=[r for r in self.document.findall('.//w:tr',NS) if r.find('w:trPr/w:tblHeader',NS) is not None]
        self.assertTrue(header_rows)
        for row in header_rows:
            for cell in row.findall('w:tc',NS):
                fill=cell.find('w:tcPr/w:shd',NS).get(W+'fill')
                self.assertEqual(len(set((fill[:2],fill[2:4],fill[4:]))),1)
                self.assertLess(int(fill[:2],16),128)
                for run in cell.findall('.//w:r',NS):
                    if run.find('w:t',NS) is not None:
                        self.assertEqual(run.find('w:rPr/w:color',NS).get(W+'val'),'FFFFFF')

    def test_populated_output_has_no_template_prompts_and_preserves_all_qualifications(self):
        markup=renderer.render_html(self.view)
        text='\n'.join(''.join(p.itertext()) for p in self.document.findall('.//w:p',NS))
        visible_html=''.join(HTMLLinks(markup).text)
        for prompt in ('[사건명·관리번호]','[직접 확인한 기록]','[EV-]','미기입 표준양식','원본 증거 없는 단정 금지'):
            self.assertNotIn(prompt,text);self.assertNotIn(prompt,markup)
        for value in ('중요 유보: 명령 문자열은 실행 성공의 증거가 아님','명령 관측; 실행 성공 미입증',
                      '근거 1에 관한 관측; 행위자 귀속 미확인','반론은 근거 1과 함께 보존','표 한계: 수집 이전 구간 미검토'):
            self.assertIn(value,text);self.assertIn(value,visible_html)

    def test_internal_links_resolve_once_even_for_repeated_evidence_rows(self):
        bookmarks=self.document.findall('.//w:bookmarkStart',NS)
        names=[b.get(W+'name') for b in bookmarks];ids=[b.get(W+'id') for b in bookmarks]
        self.assertEqual(len(names),len(set(names)));self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(Counter(ids),Counter(b.get(W+'id') for b in self.document.findall('.//w:bookmarkEnd',NS)))
        anchors=[b.get(W+'anchor') for b in self.document.findall('.//w:hyperlink',NS)]
        self.assertTrue(anchors);self.assertTrue(set(anchors)<=set(names))
        self.assertEqual(names.count(renderer.bookmark_id('근거 1')),1)
        html=HTMLLinks(renderer.render_html(self.view))
        self.assertEqual(len(html.ids),len(set(html.ids)));self.assertTrue(set(html.links)<=set(html.ids))

    def test_template_hash_is_actual_package_hash_and_bound_to_bundle_manifest(self):
        case,_,_=fixture(Path(self.tmp.name)/'case')
        with patch('socket.socket',side_effect=AssertionError('network forbidden')):
            result=bundle(case,current(case))
        manifest=json.loads(Path(result['manifest']).read_text())
        expected=hashlib.sha256(renderer.TEMPLATE_PATH.read_bytes()).hexdigest()
        self.assertEqual(manifest['style_profile']['template_sha256'],expected)
        self.assertEqual(manifest['style_profile'],renderer.render_style_identity())
        alternative=Path(self.tmp.name)/'alternative.docx'
        with ZipFile(renderer.TEMPLATE_PATH) as source,ZipFile(alternative,'w') as target:
            for entry in source.infolist():
                payload=source.read(entry.filename)
                if entry.filename=='docProps/core.xml':payload=payload.replace(b'</cp:coreProperties>',b'<!-- identity probe --></cp:coreProperties>')
                target.writestr(entry,payload)
        with patch.object(renderer,'TEMPLATE_PATH',alternative):
            self.assertNotEqual(renderer.render_style_identity()['template_sha256'],expected)

    def test_appendix_grouping_preserves_every_original_block_row_and_reference(self):
        ids=['IR01','EX01','EX02','A03','A08','A02','A01','A12','future-detail']
        pages=[]
        for index,key in enumerate(ids):
            pages.append(dict(id=key,title='부록 상세 '+key,blocks=[
                {'kind':'note','text':'중요 한계 '+key},
                {'kind':'table','headers':['원문','참조'],
                 'rows':[['원문 '+key,'근거 '+str(index)],['반론 '+key,'REF-'+key]],
                 'widths':[50,50],'row_ids':['row-'+key,'counter-'+key]}]))
        before=deepcopy(pages);grouped=_template_appendices(pages)
        self.assertEqual(pages,before)
        self.assertEqual([p['id'] for p in grouped],['appendix-'+x for x in 'ABCDEFG'])
        retained=[b for p in grouped for b in p['blocks'] if not (b['kind']=='heading' and b.get('id') in ids)]
        encode=lambda b:json.dumps({k:v for k,v in b.items() if not (k=='id' and b.get('kind')=='table')},ensure_ascii=False,sort_keys=True)
        self.assertEqual(Counter(map(encode,retained)),Counter(encode(b) for p in before for b in p['blocks']))
        headings=[b['id'] for p in grouped for b in p['blocks'] if b.get('id') in ids]
        self.assertEqual(Counter(headings),Counter(ids))
        self.assertEqual(_template_appendices([]),[])
        future=_template_appendices([pages[-1]])
        self.assertEqual(future[0]['id'],'appendix-G')
        self.assertEqual(list(map(encode,future[0]['blocks'][1:])),list(map(encode,pages[-1]['blocks'])))


if __name__=='__main__':unittest.main()
