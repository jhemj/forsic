"""Normal reviewed v2 writing fills retained controls without rebuilding body."""
from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from test_report_driven import fixture, worked
from forsic_plugin.report_driven.host import current, invoke
from forsic_plugin.report_driven.narrative import status
from forsic_plugin.report_driven.template_contract import GROUPS, REVIEW_CHECKS
from forsic_plugin.report_driven.reader_projection import project_authored, cite_pages
from forsic_plugin.report_driven.views import compile_views
from forsic_plugin.report_driven import render_views as renderer

W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
NS={'w':W[1:-1]}


def synthetic_fields():
    values={
      'report_identity':['미확인: 호스트 기록 미제공','미확인: 주소 기록 미제공','미확인: 이미지 채증 기록 미제공'],
      'overall_judgment':['두 보존 기록에 요청과 프로세스 생성이 남아 있으나 동일 작업이라는 연결은 미확인입니다. 요청 승인과 최종 실행 성공, 정보 반출은 이 자료로 판단할 수 없습니다.'],
      'major_findings.timing':['기록의 공통 시각 기준은 확인되지 않았습니다.','미확인'],
      'major_findings.entry':['최초 진입을 설명하는 인증 기록은 제공되지 않았습니다.','자료 부족'],
      'major_findings.activity':['요청 문자열과 프로세스 생성은 각각 관측되며 같은 실행인지는 미확인입니다.','관측 및 연결 미확인'],
      'major_findings.personal_data':['자료가 작업 기록에 한정되어 개인정보 보유를 판단할 수 없습니다.','자료 부족'],
      'major_findings.external_effect':['외부 전송의 내용과 성공을 보여 주는 기록이 없습니다.','미확인'],
      'event_overview.entry':['제공 범위에 최초 접근 기록이 없어 유입은 미확인입니다.'],
      'event_overview.activity':['서로 다른 반환에서 요청 및 프로세스 생성 기록을 관측했습니다.'],
      'event_overview.information_contact':['정보 접촉과 전송은 수집 범위에서 판단할 수 없습니다.'],
      'information_impact.presence':['파일과 데이터베이스의 내용은 검사 범위 밖입니다.','개인정보 보유 미확인'],
      'information_impact.exposure':['공유 설정과 외부 열람 이력은 제공되지 않았습니다.','외부 노출 미확인'],
      'information_impact.transfer':['전송 대상과 데이터 내용을 대조하지 못했습니다.','실제 반출 미확인'],
      'key_evidence.row1':['요청 반환에는 requested, 프로세스 반환에는 created가 관측됩니다.','각 레코드의 요청 및 생성 상태를 직접 보여 줍니다.','공통 작업 식별자가 없어 동일 작업, 승인 또는 최종 성공을 입증하지 않습니다.'],
      'synthesis.likely':['요청과 생성은 별개 관측으로 해석하는 것이 현재 자료의 범위에 맞습니다.'],
      'synthesis.alternative':['같은 작업일 가능성은 남지만 공통 식별자가 없으므로 연결을 확정하지 않습니다.'],
      'timeline.row1':['시각 비교 불가','요청과 프로세스 생성 문자열을 각각 관측했습니다.','정렬 가능한 시각이 없어 최초 사건이나 선후 관계로 배열하지 않습니다.'],
      'undated_events':['두 레코드의 발생 시각과 실제 실행의 선후 관계는 확인되지 않았습니다.'],
      'scope':['이 판단은 제공된 요청·프로세스 기록에 한하며 다른 자산, 승인 여부와 최종 실행 성공에는 적용하지 않습니다.']}
    fields={}
    for group,_,nodes,columns,_ in GROUPS:
        fields[group]={'entries':[dict(node_id=node,cells=values[node],basis_keys=[],status='saved')
                                  for node in nodes or (group+'.row1',)]}
    return fields


def authored_case(root):
    case,_,_=fixture(root);state=worked(case);question=state['questions'][0]
    invoke(case,{'action':'question','payload':{'operation_id':'retained-assessment','question_id':question['id'],
        'expected_revision':question['revision'],'assessment':'undetermined','reason':'합성 자료의 연결 미확인을 보존'}})
    fields=synthetic_fields()
    for group,_,_,_,_ in GROUPS:
        packet=invoke(case,{'action':'compose','section':group})
        keys=[item['key'] for item in packet['items'] if item['kind'] in ('question','claim','gap')]
        draft=deepcopy(fields[group])
        for entry in draft['entries']:entry['basis_keys']=keys
        invoke(case,{'action':'compose','section':group,'edit_ref':packet['save_contract']['edit_ref'],
                     'operation_id':'retained-'+group,'draft':draft})
    packet=invoke(case,{'action':'compose','section':'review'})
    invoke(case,{'action':'compose','section':'review','edit_ref':packet['save_contract']['edit_ref'],
       'operation_id':'retained-review','review':{'summary':'합성 예제의 구조와 불확실성을 대조했습니다.',
       'reviewed_node_ids':packet['reviewed_node_ids'],'checks':[{'check':key,'result':'satisfied',
       'reason':'해당 합성 자료의 범위와 작성 항목을 대조했습니다.'} for key in REVIEW_CHECKS],
       'comparisons':[{'node_ids':[packet['reviewed_node_ids'][0]],'basis_keys':[],
          'statement':'합성 결과는 관측의 범위에 한정됩니다.', 'source_support':'선택 관측은 전체 성공을 입증하지 않습니다.',
          'limits':'최종 성공과 행위자 연결은 미확인입니다.', 'assessment':'명시된 미확정을 유지합니다.'}],'findings':[]}})
    return case,current(case)


def document_regions(path):
    with ZipFile(path) as source:root=ET.fromstring(source.read('word/document.xml'))
    return root,{control.find('w:sdtPr/w:tag',NS).get(W+'val'):control.find('w:sdtContent',NS)
                 for control in root.findall('w:body/w:sdt',NS)}


class RetainedRegionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.case,self.state=authored_case(Path(self.tmp.name)/'case')
        self.view=compile_views(self.state)['integrated'];self.path=Path(self.tmp.name)/'report.docx'
        renderer.render_docx(self.view,self.path)
        self.root,self.regions=document_regions(self.path)

    def test_actual_authored_ready_path_has_fixed_page_and_table_contract(self):
        self.assertTrue(status(self.state)['ready'])
        self.assertEqual(self.view['template_contract'],'forsic-integrated-v2')
        self.assertEqual([p['id'] for p in self.view['pages'][:3]],['I01','I02','I03'])
        order=[b['region'] for p in self.view['pages'][:3] for b in p['blocks'] if b.get('region')]
        self.assertEqual(order,['intro','metadata','overall_judgment','major_findings','event_overview','certainty_note',
            'information_impact','key_evidence','synthesis','evidence_note','timeline','undated_events','scope','time_note'])
        expected={'major_findings':6,'event_overview':2,'information_impact':4,'key_evidence':2,'timeline':2}
        for key,count in expected.items():
            self.assertEqual(len(self.regions['report.'+key].findall('w:tbl/w:tr',NS)),count)
        findings=self.regions['report.major_findings'].findall('w:tbl/w:tr',NS)
        self.assertEqual([''.join(r.find('w:tc',NS).itertext()) for r in findings[1:]],
                         ['사고 시점','최초 진입','주요 행위','개인정보','외부 영향'])
        text=''.join(self.regions['report.information_impact'].itertext())
        for value in ('개인정보 보유','외부 노출','외부 반출'):self.assertIn(value,text)

    def test_retained_nodes_and_table_geometry_survive_with_equal_html_docx_content(self):
        template,original=document_regions(renderer.TEMPLATE_PATH)
        self.assertEqual([node.tag for node in template.find('w:body',NS)],
                         [node.tag for node in self.root.find('w:body',NS)])
        markup=renderer.render_html(self.view)
        from test_report_template_fidelity import HTMLLinks
        html_text=''.join(HTMLLinks(markup).text)
        for page in self.view['pages'][:3]:
            for block in page['blocks']:
                if not block.get('region'):continue
                content=self.regions['report.'+block['region']]
                visible=''.join(content.itertext())
                if block['kind']=='table':
                    self.assertEqual(ET.tostring(content.find('w:tbl/w:tblGrid',NS)),
                                     ET.tostring(original['report.'+block['region']].find('w:tbl/w:tblGrid',NS)))
                    for row in block['rows']:
                        for value in row:self.assertIn(value.replace('\n',''),visible);self.assertIn(value,html_text)
                else:self.assertIn(block['text'].replace('\n',''),visible);self.assertIn(block['text'],html_text)
        text=''.join(self.root.itertext())
        for token in ('[EV-]','[사건명·관리번호]','[직접 확인한 기록]'):self.assertNotIn(token,text)
        body=''.join(''.join(self.regions['report.'+key].itertext()) for key in
                     ('metadata','overall_judgment','major_findings','event_overview','information_impact','key_evidence','synthesis','timeline','undated_events','scope'))
        for value in ('SRC-','OBS-','Q-','CLAIM-','SHA-256'):self.assertNotIn(value,body)
        self.assertIn('[1]',body)
        for letter in 'ABCDEFG':self.assertIn('report.appendix-'+letter,self.regions)
        for source in self.state['sources']:self.assertIn(source['id'],text)

    def test_explicit_unknowns_and_long_rows_keep_full_text_without_font_shrink(self):
        fields=synthetic_fields();e=fields['information_impact']['entries'][2]
        e.update(status='unavailable',unavailable_reason='네트워크 내용과 전송 성공 기록이 제공되지 않았습니다.')
        long='정확 관측을 보존하며 부분 검색의 0건을 전체 부재로 해석하지 않습니다. '*55
        fields['key_evidence']['entries'][0]['cells'][0]=long
        view=deepcopy(self.view);pages=project_authored(self.state,fields)
        view['pages']=cite_pages(pages,self.state)+view['pages'][3:]
        path=Path(self.tmp.name)/'long.docx';renderer.render_docx(view,path)
        _,regions=document_regions(path)
        evidence=regions['report.key_evidence']
        self.assertIn(long,''.join(evidence.itertext()))
        self.assertIsNone(evidence.findall('w:tbl/w:tr',NS)[1].find('w:trPr/w:cantSplit',NS))
        self.assertIn(e['unavailable_reason'],''.join(regions['report.information_impact'].itertext()))
        self.assertIn(e['unavailable_reason'],renderer.render_html(view))


    def test_numbered_notes_and_shared_appendix_table_links_resolve(self):
        from test_report_template_fidelity import HTMLLinks
        names=[b.get(W+'name') for b in self.root.findall('.//w:bookmarkStart',NS)]
        links=[b.get(W+'anchor') for b in self.root.findall('.//w:hyperlink',NS)]
        self.assertEqual(len(names),len(set(names)))
        self.assertTrue(links);self.assertTrue(set(links)<=set(names),set(links)-set(names))
        html=HTMLLinks(renderer.render_html(self.view))
        self.assertEqual(len(html.ids),len(set(html.ids)))
        self.assertTrue(set(html.links)<=set(html.ids))

    def test_missing_named_region_fails_instead_of_generic_rebuild(self):
        original=renderer.TEMPLATE_PATH.read_bytes()
        import io
        output=io.BytesIO()
        with ZipFile(io.BytesIO(original)) as source,ZipFile(output,'w') as target:
            for item in source.infolist():
                raw=source.read(item.filename)
                if item.filename=='word/document.xml':raw=raw.replace(b'report.major_findings',b'report.wrong_region')
                target.writestr(item,raw)
        with self.assertRaisesRegex(ValueError,'Missing retained template region major_findings'):
            renderer.render_docx(self.view,Path(self.tmp.name)/'bad.docx',template_bytes=output.getvalue())


if __name__=='__main__':unittest.main()
