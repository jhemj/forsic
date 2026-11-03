"""Source-preserving audience placement and shared review contract."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
import unittest
from lxml import html, etree

from test_report_reading_flow import fields
from forsic_plugin.report_driven.audience_projection import project, CONTRACT
from forsic_plugin.report_driven.reader_projection import project_authored, cite_pages
from forsic_plugin.report_driven.render_views import render_html, render_docx
from forsic_plugin.report_review_packet import EXECUTIVE_READING_PLAN, FOLLOWUP_CHECKS, REPORT_SYSTEM, compact, expand, COLUMNS
import json
from forsic_plugin.independent_review import SYSTEM, EXECUTIVE_SYSTEM


class ExecutiveSynthesisTests(unittest.TestCase):
    def test_sparse_input_is_lossless_and_smaller_without_conflating_missing_values(self):
        rows = [dict(node_id=f'row.{i}', status='saved', cells=['관측과 판단 의미'],
                     basis_keys=['C1']) for i in range(12)]
        rows[0]['cell_details'] = None
        rows[1]['cell_details'] = []
        rows[2]['cell_details'] = ['']
        rows[3]['assessment'] = {'level': None, 'rationale': '평가 보류'}
        view = {'fields': {'synthesis': {'entries': rows, 'complete': True}}}
        original = deepcopy(view); packed = compact(view)
        self.assertEqual(expand(packed), original); self.assertEqual(view, original)
        size = lambda obj: len(json.dumps(obj, ensure_ascii=False, separators=(',', ':')))
        self.assertLess(size(packed), size(view))
        for i in range(3): self.assertEqual(expand(packed)['fields']['synthesis']['entries'][i], rows[i])
        self.assertNotIn('cell_details', expand(packed)['fields']['synthesis']['entries'][4])

    def test_existing_dense_receipts_still_decode_and_empty_unmarked_fields_survive(self):
        row = dict(zip(COLUMNS, ['node', 'saved', ['fact'], ['scope'], ['C1'], [['C1']]]))
        view = {'fields': {'dense': {'entries': [row]}, 'empty': {'entries': []}}}
        packed = compact(view)
        self.assertEqual(expand(packed), view)
        packed['encoding'] = 'columnar-report-entries-v1'
        packed['fields']['dense']['entry_encoding'] = packed['encoding']
        self.assertEqual(expand(packed), view)

    def test_body_synthesis_keeps_distinct_support_and_appendix_details_in_html_word(self):
        value = fields()
        first = {'kind': 'source', 'id': 'synthetic-execution', 'version': 'v1'}
        second = {'kind': 'source', 'id': 'synthetic-maintenance', 'version': 'v2'}
        prose = ['실행 기록과 재실행 설정의 대상 일치로 반복 구동을 의도한 구성 유력.',
                 '관리 작업의 결과일 가능성 잔존. 실행 주체의 귀속은 별도 판단.']
        details = ['전체 읽기 범위와 원문 좌표의 기술 대조.', '관리 작업대장의 검사 범위와 제한.']
        for e, text, detail, ref in zip(value['synthesis']['entries'], prose, details, (first, second)):
            e.update(cells=[text], cell_details=[detail], citation_refs=[ref], cell_citation_refs=[[ref]])
        original = deepcopy(value)
        pages = project(project_authored({}, value), value, {})
        body_blocks = [b for p in pages if p['presentation_view'] == 'body' for b in p['blocks']]
        located = [b for b in body_blocks if b.get('region') == 'synthesis']
        self.assertEqual([b['text'] for b in located], prose)
        self.assertEqual([b['citation_refs'] for b in located], [[first], [second]])
        self.assertFalse(any(b.get('detail') for b in located))
        order = [b.get('region') for b in body_blocks]
        self.assertLess(order.index('overall_judgment'), order.index('synthesis'))
        self.assertLess(order.index('synthesis'), order.index('major_findings'))
        self.assertEqual(value, original)
        state = {'sources': [first, second]}
        view = dict(title='Synthetic executive synthesis', security='Synthetic', data_mode='synthetic',
                    snapshot_revision='synthetic', template_contract=CONTRACT, pages=cite_pages(pages, state))
        dom = html.fromstring(render_html(view))
        main = ' '.join(e.text_content() for e in dom.xpath('//article[@data-presentation-scope="body"]'))
        appendix = ' '.join(e.text_content() for e in dom.xpath('//article[@data-presentation-scope="technical"]'))
        for text, detail in zip(prose, details):
            self.assertIn(text, main); self.assertNotIn(detail, main); self.assertIn(detail, appendix)
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'report.docx'; render_docx(view, path)
            with ZipFile(path) as doc:
                document = etree.fromstring(doc.read('word/document.xml'))
            text = ''.join(document.itertext()); boundary = text.index('별첨 1.')
            for lead, detail in zip(prose, details):
                self.assertLess(text.index(lead), boundary)
                self.assertGreater(text.index(detail), boundary)

    def test_reviewer_body_order_matches_rendered_groups_and_all_modes_share_followup(self):
        value = fields()
        pages = project(project_authored({}, value), value, {})
        groups = []
        for p in pages:
            if p['presentation_view'] != 'body': continue
            for b in p['blocks']:
                group = b.get('region')
                if group and group not in groups: groups.append(group)
        groups[0] = 'report_identity'
        self.assertEqual(groups, [g for g in EXECUTIVE_READING_PLAN['order'] if g in value])
        for prompt in (REPORT_SYSTEM, SYSTEM, EXECUTIVE_SYSTEM):
            self.assertEqual(prompt.count(FOLLOWUP_CHECKS), 1)
        self.assertEqual(EXECUTIVE_READING_PLAN['copyedit_passes'], 1)


if __name__ == '__main__': unittest.main()
