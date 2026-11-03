"""Synthetic authored flow and appendix placement; no evidence or model calls."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile
import unittest
from lxml import html, etree

import test_report_retained_regions as fixture
from forsic_plugin.report_driven import authoring_form
from forsic_plugin.report_driven.audience_projection import project, CONTRACT
from forsic_plugin.report_driven.reader_projection import project_authored, cite_pages
from forsic_plugin.report_driven.render_views import render_html, render_docx
from forsic_plugin.report_driven.template_contract import validate_group, OPTIONAL_GROUPS


def fields():
    with patch.object(fixture, 'GROUPS', tuple(g for g in fixture.GROUPS if g[0] not in
                      ('executive_context', 'key_indicators', 'response_actions', 'timeline_summary'))):
        value = fixture.synthetic_fields()
    value['report_identity']['entries'][0]['cells'] = ['host.example', '192.0.2.1', '미확인']
    return value


class ReadingFlowTests(unittest.TestCase):
    def test_named_summary_compiles_without_inventing_connections_or_dropping_support(self):
        payload = authoring_form.template('timeline_summary')
        payload['entries'][0]['values']['summary'] = {
            'text': '계정 인증과 후속 권한 사용 기록으로 확인되는 시스템 활동.',
            'detail': '두 행의 공통 작업 식별자는 없어 동일 실행의 입증과는 구분.',
            'basis_keys': ['S1', 'S2']}
        saved, _ = authoring_form.normalize('timeline_summary', payload)
        validate_group('timeline_summary', saved)
        self.assertEqual(authoring_form.to_form('timeline_summary', saved['entries'][0])['values'],
                         payload['entries'][0]['values'])
        self.assertEqual(saved['entries'][0]['cell_basis_keys'], [['S1', 'S2']])
        self.assertIn('timeline_summary', OPTIONAL_GROUPS)

    def test_flow_precedes_events_actions_only_in_appendix_html_and_word(self):
        value = fields()
        ref = {'kind': 'source', 'id': 'synthetic-flow-source', 'version': 'v1'}
        summary = '계정 인증에 이어 관리자 권한 대상 명령 사용이 기록된 흐름.'
        detail = '동일 작업 여부의 상세 비교 자료는 별도 확인 대상.'
        action = '보존된 로그를 별도 사본으로 보관'
        value['timeline_summary'] = {'entries': [dict(node_id='timeline_summary', cells=[summary],
            cell_details=[detail], cell_citation_refs=[[ref]], basis_keys=['S1'], status='saved')]}
        value['response_actions'] = {'entries': [dict(node_id='response_actions.row1',
            cells=['우선', action, '후속 대조에 사용할 기록 보존'],
            cell_details=['근거 훼손 방지', '원본에 쓰기 없이 별도 저장', '대조 가능성 유지'],
            cell_citation_refs=[[ref], [ref], [ref]], basis_keys=['S1'], status='saved')]}
        original = deepcopy(value)
        state = {'sources': [ref]}
        pages = cite_pages(project(project_authored({}, value), value, state), state)
        self.assertEqual(value, original)
        third = next(p for p in pages if p['id'] == 'I03')
        self.assertEqual([b.get('region') for b in third['blocks']], [None, 'timeline_summary', 'timeline'])
        self.assertTrue(third['blocks'][1]['text'].startswith(summary))
        self.assertEqual(third['blocks'][1]['citation_refs'], [ref])
        view = dict(title='Synthetic reading flow', security='Synthetic', data_mode='synthetic',
                    snapshot_revision='synthetic-flow', template_contract=CONTRACT, pages=pages)
        dom = html.fromstring(render_html(view))
        body = ' '.join(e.text_content() for e in dom.xpath('//article[@data-presentation-scope="body"]'))
        technical = ' '.join(e.text_content() for e in dom.xpath('//article[@data-presentation-scope="technical"]'))
        self.assertIn(summary, body); self.assertNotIn(detail, body)
        self.assertNotIn(action, body); self.assertIn(action, technical)
        self.assertIn(detail, technical)
        self.assertEqual(dom.xpath('//input[@checked]/@id'), ['audit-view'])
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'flow.docx'; render_docx(view, path)
            with ZipFile(path) as document:
                xml = etree.fromstring(document.read('word/document.xml'))
            text = ''.join(xml.itertext())
            self.assertLess(text.index(summary), text.index('별첨 1.'))
            self.assertGreater(text.index(action), text.index('별첨 1.'))
            self.assertIn(detail, text)

    def test_missing_optional_summary_preserves_existing_saved_timeline(self):
        value = fields(); original = deepcopy(value)
        pages = project(project_authored({}, value), value, {})
        third = next(p for p in pages if p['id'] == 'I03')
        self.assertEqual([b.get('region') for b in third['blocks']], [None, 'timeline'])
        self.assertEqual(value, original)


if __name__ == '__main__':
    unittest.main()
