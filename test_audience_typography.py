"""One audience scale across emitted HTML and retained Word, with literal sources."""
from copy import deepcopy
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from docx.oxml import parse_xml
from docx.text.run import Run

from forsic_plugin.report_driven import render_views as renderer, typography

LITERAL = '  first\t[12]\n<literal> & second  '


def synthetic_view():
    return dict(title='Synthetic typography', security='Synthetic', data_mode='synthetic',
        snapshot_revision='synthetic', template_contract='forsic-integrated-audience-1',
        pages=[dict(id='body', title='Synthetic title', blocks=[
            dict(kind='heading', text='Section heading'),
            dict(kind='paragraph',text='References [1] [2] [3] [4] [5]'),
            dict(kind='callout', region='overall_judgment', label='Summary label',
                 assessment=dict(level=3,labels=['One','Two','Three','Four','Five'],tone='risk',rationale='Assessment explanation',time_range=dict(subject='Observed time',qualifier='Estimate',relation='by',start=None,end='Synthetic bound')),
                 text='Long overall prose [1]\n  Nested overall explanation', detail='Secondary explanation [1]', authored_hierarchy=True),
            dict(kind='table', region='metadata', variant='metadata', headers=['Key', 'Value'], rows=[['Host', 'Synthetic host']], authored_hierarchy=True),
            dict(kind='table', region='timeline', headers=['Time','Event','Meaning','Evidence'],
                 rows=[['Unknown time','Timeline title\n  Nested event prose','Timeline body','[1]']],
                 authored_hierarchy=True, event_kinds=['unknown'], row_ids=['event']),
        ]),dict(id='appendix', title='Appendix title', section='appendix', blocks=[
            *[dict(kind='heading',text='Reference '+str(i),id='['+str(i)+']',level=2) for i in range(1,6)],
            dict(kind='table', headers=['Label','Text'], rows=[['Detail label','Appendix prose\n  Nested appendix']], authored_hierarchy=True),
            dict(kind='table', id='source', headers=['Field','Value'], literal=True,
                 rows=[['자료','Synthetic source'],['보존 인용',LITERAL]], row_ids=['source-meta','source-quote']),
        ])])


class AudienceTypographyTests(unittest.TestCase):
    def test_scale_and_emitted_html(self):
        view=synthetic_view();before=deepcopy(view);html=renderer.render_html(view)
        self.assertEqual(view,before)
        self.assertIn('data-typography="audience"',html)
        self.assertIn('class="hierarchy-line nested"',html)
        self.assertIn('class="citation-group"',html)
        self.assertIn('class="citation"',html)
        self.assertIn('first\t[12]',html)
        self.assertIn('&lt;literal&gt; &amp; second  ',html)
        css=re.search(r'<style>(.*?)</style>',html,re.S).group(1)
        self.assertEqual(css,renderer.CSS)
        self.assertNotRegex(renderer.LAYOUT_CSS,r'(?:font-size|font-weight|font-family|line-height|letter-spacing)\s*:')
        self.assertIn('.sheet .source-field>dd',css)
        self.assertIn('.citation-group a',css)
        self.assertIn('.hierarchy-line{font:inherit;color:inherit;line-height:inherit}',css)
        roles=typography.resolved_roles(renderer.STYLE)
        self.assertEqual(roles['overall'],roles['body'])
        self.assertEqual((roles['body']['screen_px'],roles['body']['weight']),(16,400))
        self.assertEqual(roles['heading']['screen_px'],22)
        for role in typography.SELECTORS:
            self.assertIn('before_px',roles[role]);self.assertIn('after_pt',roles[role])
        legacy=deepcopy(view);legacy['template_contract']='forsic-integrated-v2'
        self.assertIn('<style>'+renderer.CSS+'</style>',renderer.render_html(legacy))

    def test_word_role_runs_literal_and_preserved_parts(self):
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'synthetic.docx'
            renderer.render_docx(synthetic_view(),target)
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            with ZipFile(target) as out:
                xml=parse_xml(out.read('word/document.xml'))
                for paragraph in xml.findall('.//w:p',ns):
                    self.assertEqual(paragraph.find('w:pPr/w:snapToGrid',ns).get('{'+ns['w']+'}val'),'0')
                def runs(text):
                    return [p for p in xml.findall('.//w:p',ns) if text in ''.join(p.itertext())][0].findall('.//w:r',ns)
                overall=runs('Long overall prose')
                overall_p=next(p for p in xml.findall('.//w:p',ns) if 'Long overall prose' in ''.join(p.itertext()))
                spacing=overall_p.find('w:pPr/w:spacing',ns)
                self.assertEqual(spacing.get('{'+ns['w']+'}lineRule'),'exact')
                self.assertAlmostEqual(int(spacing.get('{'+ns['w']+'}line'))/20,16.275,delta=0.05)
                self.assertEqual([(r.find('w:rPr/w:sz',ns).get('{'+ns['w']+'}val'),r.find('w:rPr/w:b',ns).get('{'+ns['w']+'}val')) for r in overall],[('21','0'),('16','0'),('21','0')])
                source=runs('<literal>')
                self.assertEqual(''.join(Run(r,None).text for r in source),LITERAL)
                for run in source:
                    self.assertEqual(run.find('w:rPr/w:sz',ns).get('{'+ns['w']+'}val'),'19')
                    self.assertEqual(run.find('w:rPr/w:rFonts',ns).get('{'+ns['w']+'}ascii'),'Noto Sans Mono')
                with ZipFile(renderer.TEMPLATE_PATH) as original:
                    for name in original.namelist():
                        if name not in {'word/document.xml','word/header1.xml','word/footer1.xml','docProps/core.xml'}:
                            self.assertEqual(original.read(name),out.read(name),name)

    def test_retained_content_layout_uses_same_word_roles(self):
        import test_report_retained_regions as fixture
        from forsic_plugin.report_driven.reader_projection import project_authored
        # This older shared fixture predates additional writing groups. Use its
        # original input scope to exercise the retained content layout.
        with patch.object(fixture,'GROUPS',tuple(g for g in fixture.GROUPS if g[0] not in ('executive_context','key_indicators','response_actions','timeline_summary'))):
            fields=fixture.synthetic_fields()
        pages=project_authored({},fields)
        view=dict(title='Synthetic retained report',security='Synthetic',data_mode='synthetic',
                  snapshot_revision='synthetic',template_contract='forsic-integrated-v2',pages=pages)
        fields['overall_judgment']['entries'][0]['cell_details']=['Secondary retained explanation']
        view['pages']=project_authored({},fields)
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'retained.docx';renderer.render_docx(view,target)
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            with ZipFile(target) as output:
                xml=parse_xml(output.read('word/document.xml'))
            paragraphs=xml.findall('.//w:p',ns)
            for text,size in [('Secondary retained explanation','19'),(fields['overall_judgment']['entries'][0]['cells'][0],'21')]:
                paragraph=next(p for p in paragraphs if text in ''.join(p.itertext()))
                self.assertEqual(paragraph.find('.//w:sz',ns).get('{'+ns['w']+'}val'),size)
                self.assertEqual(paragraph.find('.//w:b',ns).get('{'+ns['w']+'}val'),'0')
            self.assertIn('<style>'+renderer.CSS+'</style>',renderer.render_html(view))

    def test_metadata_values_only_projection_preserves_appendix_details_and_refs(self):
        import test_report_retained_regions as fixture
        from forsic_plugin.report_driven.reader_projection import project_authored, cite_pages
        from forsic_plugin.report_driven.audience_projection import project
        with patch.object(fixture,'GROUPS',tuple(g for g in fixture.GROUPS if g[0] not in ('executive_context','key_indicators','response_actions','timeline_summary'))):
            fields=fixture.synthetic_fields()
        refs=[dict(kind='source',id='synthetic-source-'+str(i),version='synthetic-v1') for i in range(3)]
        entry=fields['report_identity']['entries'][0]
        entry.update(cells=['host.example','192.0.2.1','미확인'],
                     cell_details=['Hostname source and qualification','IP source and qualification','Acquisition-time scope and unknown reason'],
                     cell_citation_refs=[[ref] for ref in refs])
        before=deepcopy(fields)
        state=dict(sources=[dict(id=ref['id'],version=ref['version']) for ref in refs])
        pages=cite_pages(project(project_authored({},fields),fields,state),state)
        self.assertEqual(fields,before)
        metadata=next(b for p in pages for b in p['blocks'] if b.get('region')=='metadata')
        self.assertEqual(metadata['rows'],[entry['cells']])
        self.assertEqual(metadata['cell_details'],[['','','']])
        self.assertEqual(metadata['cell_citation_refs'],[[[],[],[]]])
        explanation=next(p for p in pages if p['id']=='T01')
        identity=explanation['blocks'][1]
        self.assertEqual(identity['cell_citation_refs'],[[[],[ref]] for ref in refs])
        for i,(value,detail) in enumerate(zip(entry['cells'],entry['cell_details']),1):
            self.assertEqual(identity['rows'][i-1][1],value+'\n'+detail+' ['+str(i)+']')
        view=dict(title='Synthetic metadata',security='Synthetic',data_mode='synthetic',
                  snapshot_revision='synthetic',template_contract='forsic-integrated-audience-1',pages=pages)
        html=renderer.render_html(view)
        header=html.split('data-region="metadata"',1)[1].split('</table>',1)[0]
        for detail in entry['cell_details']:self.assertNotIn(detail,header);self.assertIn(detail,html)
        self.assertNotIn('class="citation"',header)
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'metadata.docx';renderer.render_docx(view,path)
            with ZipFile(path) as output:xml=parse_xml(output.read('word/document.xml'))
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            header=next(t for t in xml.findall('.//w:tbl',ns) if 'Hostname' in ''.join(t.itertext()) and 'host.example' in ''.join(t.itertext()))
            for detail in entry['cell_details']:self.assertNotIn(detail,''.join(header.itertext()));self.assertIn(detail,''.join(xml.itertext()))
            value=header.findall('w:tr',ns)[1].findall('w:tc',ns)[0]
            self.assertEqual(value.find('.//w:b',ns).get('{'+ns['w']+'}val'),'0')

    def test_metadata_format_validation_and_named_schema(self):
        from forsic_plugin.report_driven.template_contract import validate_group
        from forsic_plugin.report_driven.authoring_form import form_schema
        from forsic_plugin.report_driven.narrative import schema
        entry=dict(node_id='report_identity',cells=['host.example','192.0.2.1','미확인'],
                   cell_details=['Source note','Address scope','Unknown acquisition reason'],basis_keys=['S1'],status='saved')
        validate_group('report_identity',dict(entries=[entry]))
        entry['cells'][0]='long-hostname-'*80
        validate_group('report_identity',dict(entries=[entry]))
        for index, bad in [(0,'host.example\nSource explanation'),
                           (0,'Host observed in retained records; its service role is unknown.'),
                           (1,'192.0.2.1 was observed, but the network scope remains uncertain.'),
                           (2,'The acquisition date is not confirmed because the retained conversion record describes a different event.'),
                           (2,'이미지 채증 시점: 2020-01-02')]:
            entry['cells']=['host.example','192.0.2.1','2020-01-02']
            entry['cells'][index]=bad
            with self.assertRaisesRegex(ValueError,'report_identity.values.*update only report_identity'):
                validate_group('report_identity',dict(entries=[entry]))
        entry['cells']=['host.example','192.0.2.1','2020-01-02 (완료 기록)']
        with self.assertRaisesRegex(ValueError,'primary value only'):
            validate_group('report_identity',dict(entries=[entry]))
        for date in ['2020-01-02','2020.01.02 10:30 KST','2020-01-02T10:30:00+09:00','2020-01-02 ~ 2020-01-03','미확인']:
            entry['cells']=['a'*253,'2001:db8::1',date]
            validate_group('report_identity',dict(entries=[entry]))
        slots=form_schema('report_identity',schema())['properties']['entries']['items']['properties']['values']['properties']
        self.assertEqual(set(slots),{'hostname','representative_ip','acquisition_time'})
        self.assertEqual(slots['hostname']['properties']['text']['examples'],['host.example'])
        self.assertFalse(re.fullmatch(slots['hostname']['properties']['text']['pattern'],'host.example\nSource'))


if __name__=='__main__':unittest.main()
