"""Synthetic card views preserve exact refs and decline invented coordinates."""
from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from forsic_plugin.report_driven.reader_cards import reader_cards
from forsic_plugin.report_driven import render_views
from test_report_driven import fixture, worked
from test_report_template_fidelity import HTMLLinks


class ReaderCardsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.case,_,_=fixture(Path(self.tmp.name)/'case')
        self.state=worked(self.case)

    def event(self, key, value, **updates):
        return dict(id=key,version='v1',explanation='Synthetic observation '+key,
            shape='point',comparable=True,normalized_values=[value],raw_values=[],
            time_kind='record',observation_ref=None,claim_refs=[],limitation='Returned scope only',
            temporal={'observed_or_inferred':'observed','clock_domain':'clock-A'},**updates)

    def test_only_structured_observed_current_instants_receive_coordinates(self):
        rows=[self.event('later','2026-01-02T00:00:00Z'),self.event('earlier','2026-01-01T00:00:00Z'),
              self.event('prose','before January'),self.event('invalid','2026-02-30T00:00:00Z'),
              self.event('inferred','2026-01-01T00:00:00Z'),self.event('changed','2026-01-01T00:00:00Z')]
        rows[4]['temporal']['observed_or_inferred']='inferred'
        rows[5]['temporal_context']={'temporal_requires_recheck':True}
        self.state['timeline']=rows;before=deepcopy(self.state)
        cards=reader_cards(self.state)
        self.assertGreater(cards['timeline'][0]['stamp'],cards['timeline'][1]['stamp'])
        self.assertTrue(all(row['stamp'] is None for row in cards['timeline'][2:]))
        self.assertEqual(cards['timeline'][-1]['freshness'],'recheck')
        self.assertEqual(self.state,before)
        html=render_views.render_reader_cards(cards,set())
        self.assertEqual(html.count('class="axis-mark"'),2)
        self.assertIn('추정·시각 미확정·재검토 사항',html)
        links=HTMLLinks(html);self.assertTrue(set(links.links)<=set(links.ids))

    def test_current_judgment_keeps_exact_versions_limits_and_freshness(self):
        self.state['questions'][0]['judgment_review_required']=True
        claim=self.state['claims'][0]
        old_ref=dict(kind='source',id=self.state['sources'][0]['id'],version='retained-old-version')
        claim['source_refs']=[old_ref];claim['limitations']=['Synthetic material limitation']
        self.state['questions'][0]['claim_refs']=[dict(kind='claim',id=claim['id'],version=claim['version'])]
        before=deepcopy(self.state);cards=reader_cards(self.state)
        question=cards['judgments'][0]
        self.assertEqual(question['answer'],self.state['questions'][0]['answer'])
        self.assertTrue(question['review_required'])
        self.assertEqual(question['revision'],self.state['questions'][0]['revision'])
        self.assertEqual(question['judgments'][0]['source_refs'][0]['version'],'retained-old-version')
        self.assertEqual(question['judgments'][0]['limitations'],['Synthetic material limitation'])
        for judgment in question['judgments']:
            for ref in judgment['source_refs']:
                self.assertTrue(ref['version']);self.assertTrue(ref['label'])
        self.assertEqual(self.state,before)

    def test_font_profile_changes_filled_runs_but_preserves_package_styles(self):
        from test_report_template_fidelity import view_fixture
        path=Path(self.tmp.name)/'reader.docx'
        render_views.render_docx(view_fixture(),path)
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        with ZipFile(path) as result,ZipFile(render_views.TEMPLATE_PATH) as template:
            self.assertEqual(result.read('word/styles.xml'),template.read('word/styles.xml'))
            doc=ET.fromstring(result.read('word/document.xml'))
        for cell in doc.findall('.//w:tc',ns):
            for run in cell.findall('.//w:r',ns):
                if run.find('w:t',ns) is not None:
                    self.assertEqual(run.find('w:rPr/w:sz',ns).get('{'+ns['w']+'}val'),'20')
        self.assertIn('font-size:11pt',render_views.CSS)
        self.assertIn('font-size:10pt',render_views.CSS)


if __name__=='__main__':unittest.main()
