"""Lossless reader/audit presentation contracts; synthetic data only."""
from copy import deepcopy
from pathlib import Path
import unittest
import tempfile
from zipfile import ZipFile
from lxml import html
from forsic_plugin.report_driven.render_views import render_html, render_docx, bookmark_id, _source_excerpt


def synthetic_view():
    refs=['['+str(i)+']' for i in range(1,7)]
    excerpt='first <script> & retained\nsecond exact line\nthird line'
    return dict(title='합성 가독성 보고서',security='합성 예시',data_mode='synthetic',
        snapshot_revision='synthetic-readability-1',publication_status='internal_partial_snapshot',
        snapshot_status={'investigation':'범위 내 확인 중','semantic':'미검토','layout':'합성 검증',
                         'integrity':{'subject':'보고 묶음 파일 지문','status':'미확인'}},
        reader_notices=[{'kind':'callout','label':'현재 자료 한계','text':'자료 일부만 반환됨. 실행 성공 미입증.'}],
        pages=[{'id':'main','title':'요약과 주요 판단','section':'body','presentation_view':'body','blocks':[
            {'kind':'callout','label':'현재 판단','text':'명령 관측 · 실행 성공 미입증.'},
            {'kind':'paragraph','text':'정확한 근거를 보존합니다. '+', '.join(refs)},
            {'kind':'paragraph','text':'audit-item의 전체 실행 기록 참조.'},
            {'kind':'table','headers':['판단','설명'],'rows':[['관측','줄 <tag> & 상세. '*100]],'row_ids':['long-finding']}]},
            {'id':'appendix-B','title':'감사·재현 기록','section':'appendix','presentation_view':'audit','blocks':[
                {'kind':'heading','id':'audit-item','text':'실행·검토 원문'},
                {'kind':'paragraph','text':'원문 UUID synthetic-id / 전체 기록 보존'}]},
            {'id':'EX02','title':'기술 근거와 원문','section':'appendix','presentation_view':'technical','blocks':sum(([
                {'kind':'heading','id':label,'level':2,'text':label+' 보존 근거'},
                {'kind':'table','literal':True,'headers':['항목','보존 값'],
                 'rows':[['보존 인용',excerpt],['보존 필드 내 위치','UTF-8 [20,80)'],['해석 한계','이 명령의 실행 성공은 확인되지 않음.']],
                 'cell_line_starts':{'0:1':41},'cell_highlight_lines':{'0:1':[42]}}
                ] for label in refs),[])}])


class ReadabilityTests(unittest.TestCase):
    def setUp(self):
        self.view=synthetic_view();self.before=deepcopy(self.view)
        self.markup=render_html(self.view);self.dom=html.fromstring(self.markup)

    def test_reader_audit_native_controls_and_lossless_sections(self):
        radio=self.dom.get_element_by_id('audit-view')
        self.assertIn('checked',radio.attrib)
        self.assertEqual(radio.get('name'),self.dom.get_element_by_id('audit-view').get('name'))
        controls=self.dom.xpath('//*[@role="group"]')[0]
        self.assertEqual(controls.get('aria-label'),'인쇄 범위')
        self.assertIn('웹에서는 항상 전체 보고서 표시',controls.text_content())
        self.assertEqual({x.get('for') for x in controls.xpath('.//label')},{'body-view','reader-view','audit-view'})
        audit=self.dom.get_element_by_id(bookmark_id('appendix-B'))
        self.assertIn('audit-only',audit.get('class'))
        self.assertEqual(audit.get('data-presentation-scope'),'audit')
        technical=self.dom.get_element_by_id(bookmark_id('EX02'))
        self.assertNotIn('audit-only',technical.get('class'))
        self.assertEqual(technical.get('data-presentation-scope'),'technical')
        self.assertEqual(self.dom.get_element_by_id(bookmark_id('main')).get('data-presentation-scope'),'body')
        self.assertIn('미입증',self.dom.xpath('//main/aside')[0].getparent().text_content())
        self.assertIn('전체 기록 보존',audit.text_content())
        self.assertEqual(self.view,self.before)

    def test_citations_disclose_full_set_and_all_return_links_resolve(self):
        identifiers=self.dom.xpath('//@id');self.assertEqual(len(identifiers),len(set(identifiers)))
        for anchor in self.dom.xpath('//a[starts-with(@href,"#")]'):
            self.assertIn(anchor.get('href')[1:],identifiers)
        groups=self.dom.xpath('//span[@class="citation-group"]');self.assertEqual(len(groups),1)
        group=groups[0];toggle=group.xpath('./input')[0]
        self.assertEqual(group.xpath('./label')[0].get('for'),toggle.get('id'))
        self.assertEqual(len(group.xpath('.//a[@class="citation"]')),6)
        immediate=group.xpath('./span[@class="citation-representatives"]/a')
        folded=group.xpath('./span[@class="citation-list"]/a')
        self.assertEqual([x.text for x in immediate],['[1]','[2]'])
        self.assertEqual([x.text for x in folded],['[3]','[4]','[5]','[6]'])
        self.assertNotIn('checked',toggle.attrib)
        for citation in group.xpath('.//a[@class="citation"]'):
            back=self.dom.xpath('//nav[@class="citation-returns"]/a[@href="#'+citation.get('id')+'"]')
            self.assertEqual(len(back),1)
        self.assertFalse(self.dom.xpath('//script|//tag'))

    def test_prose_records_exact_excerpt_and_escaped_content(self):
        record=self.dom.get_element_by_id(bookmark_id('long-finding'))
        self.assertEqual(record.tag,'article');self.assertTrue(record.xpath('./dl/dt'))
        self.assertIn('줄 <tag> & 상세. '*100,record.text_content())
        pre=self.dom.xpath('//pre[@class="source-excerpt"]')[0]
        self.assertEqual([x.text for x in pre.xpath('.//span[@class="line-number"]')],['41','42','43'])
        self.assertEqual(pre.xpath('.//mark')[0].text,'second exact line')
        self.assertIn('<script> & retained',pre.text_content())
        self.assertNotIn('line-number',_source_excerpt('retained\ntext'))
        self.assertIn('UTF-8 [20,80)',self.dom.text_content())
        support=self.dom.xpath('//section[@class="source-support"]')[0]
        summary=support.xpath('./dl[@class="source-summary"]')[0]
        self.assertIn('UTF-8 [20,80)',summary.text_content())
        self.assertIn('실행 성공은 확인되지 않음',summary.text_content())
        self.assertFalse(summary.xpath('.//pre'))
        self.assertFalse(support.xpath('./details[@open]'))

    def test_html_only_notices_leave_word_snapshot_content_identical(self):
        plain=deepcopy(self.view);plain.pop('reader_notices')
        with tempfile.TemporaryDirectory() as directory:
            first=Path(directory)/'with-notices.docx';second=Path(directory)/'without-notices.docx'
            render_docx(self.view,first);render_docx(plain,second)
            with ZipFile(first) as a,ZipFile(second) as b:
                self.assertEqual(a.read('word/document.xml'),b.read('word/document.xml'))

    def test_status_subject_and_print_contract_are_explicit(self):
        status=self.dom.xpath('//aside[@class="document-status"]')[0].text_content()
        self.assertIn('초안',status);self.assertIn('보고 묶음 파일 지문 · 미확인',status)
        self.assertNotIn('E01',status)
        unknown=deepcopy(self.view);unknown.pop('publication_status')
        self.assertIn('발행 상태 미확인',html.fromstring(render_html(unknown)).xpath('//aside[@class="document-status"]')[0].text_content())
        css=self.dom.xpath('//style')[0].text
        # Print scopes remain selectable; screen visibility is tested with
        # Chromium against the synthetic body/technical/audit fixture.
        self.assertIn('#body-view:checked~main [data-presentation-scope="technical"]',css)
        self.assertIn('#reader-view:checked~main [data-presentation-scope="audit"]{display:none!important}',css)
        self.assertIn('main article.sheet details.exact-original>:not(summary){display:block!important}',css)
        self.assertIn('#audit-view:checked~main article[data-presentation-scope]{display:block!important}',css)
        self.assertIn('#audit-view:checked~main details>:not(summary){display:block!important}',css)
        self.assertIn('#audit-view:checked~main .citation-group .citation-list{display:inline!important}',css)
        self.assertNotIn('@media print{details> :not(summary){display:block!important}',css)
        self.assertIn('vertical-align:top',css)


if __name__=='__main__':
    unittest.main()
