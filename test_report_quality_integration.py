"""Current report visibility and masking; synthetic ledger only."""
from copy import deepcopy
from pathlib import Path
import json
import unittest
from lxml import html
import test_report_narrative as fixtures
from forsic_plugin.report_driven.host import current
from forsic_plugin.report_driven.views import bundle, compile_views, selected_report_state
from forsic_plugin.report_driven.render_views import render_html, bookmark_id
from test_report_readability import synthetic_view


class ReportQualityIntegrationTests(unittest.TestCase):
    setUp=fixtures.ReportNarrativeTests.setUp
    tearDown=fixtures.ReportNarrativeTests.tearDown

    def test_inapplicable_limit_is_not_current_but_receipt_remains(self):
        state=current(self.case)
        gap=state['gaps'][0]
        gap.update(disposition='not_applicable', reason='Historical obligation does not apply')
        selected=selected_report_state(state,[q['id'] for q in state['questions']])
        self.assertNotIn(gap['reason'],selected['status']['explicit_limitations'])
        self.assertTrue(any(g['id']==gap['id'] for g in selected['gaps']))
        view=compile_views(selected)['integrated']
        self.assertFalse(any(n.get('text')==gap['reason'] for n in view['reader_notices']))

    def test_first_page_status_and_proposed_checks_do_not_claim_final_approval(self):
        state=current(self.case)
        state['questions'][0]['next_checks']=['Inspect the missing shared identifier before attribution']
        view=compile_views(state,independent_review={'final_review_current':False,
            'pending_finding_ids':['synthetic-finding']})['integrated']
        status=view['snapshot_status']
        self.assertIn('독립 검토 미확인',status['semantic'])
        self.assertIn('미처분 의견 1건',status['semantic'])
        self.assertIn('별개',status['integrity']['status'])
        self.assertEqual(view['publication_status'],'internal_partial_snapshot')
        self.assertTrue(any(n.get('region')=='reader_next_checks' and 'before attribution' in n['text']
                            for n in view['reader_notices']))

    def test_new_reader_notices_follow_existing_masking_contract(self):
        state=current(self.case)
        state['questions'][0]['next_checks']=['Inspect private-marker-731 before attribution']
        state['gaps'][0].update(impact='material',reason='private-marker-731 remains unavailable')
        report=bundle(self.case,state,redact=['private-marker-731'])
        manifest=json.loads(Path(report['manifest']).read_text())
        self.assertEqual(manifest['evidence_export']['status'],'not_exported_redacted')
        rendered=Path(next(p for p in report['files'] if p.endswith('.html'))).read_text()
        dom=html.fromstring(rendered)
        notices=dom.xpath('//*[@data-region="reader_limitation" or @data-region="reader_next_checks"]')
        self.assertTrue(notices)
        self.assertTrue(any('[비공개]' in n.text_content() for n in notices))
        self.assertFalse(any('private-marker-731' in n.text_content() for n in notices))

    def test_long_supplement_uses_prose_record_and_preserves_exact_text(self):
        view=synthetic_view()
        table=view['pages'][0]['blocks'][-1]
        table.update(rows=[['Finding','Qualified conclusion']],authored_hierarchy=True,
                     cell_details=[['','Long supplementary explanation. '*50]])
        before=deepcopy(view)
        dom=html.fromstring(render_html(view))
        record=dom.get_element_by_id(bookmark_id('long-finding'))
        self.assertEqual(record.tag,'article')
        self.assertNotIn('short-record',record.get('class'))
        self.assertIn(table['cell_details'][0][1],record.text_content())
        self.assertEqual(view,before)

    def test_metadata_provenance_is_disclosed_without_changing_qualified_value(self):
        view=synthetic_view()
        view['pages'][0]['blocks']=[{'kind':'table','region':'metadata','headers':['IP'],
            'rows':[['192.0.2.2 · supplied value']], 'cell_details':[['Exact supplied metadata; internal host identity not verified']],
            'authored_hierarchy':True}]
        dom=html.fromstring(render_html(view))
        cell=dom.xpath('//table[@data-region="metadata"]//td')[0]
        self.assertIn('supplied value',cell.xpath('./span')[0].text_content())
        detail=cell.xpath('./details')[0]
        self.assertNotIn('open',detail.attrib)
        self.assertIn('not verified',detail.text_content())


if __name__=='__main__':unittest.main()
