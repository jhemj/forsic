"""Offline reader references: exact native provenance, shared HTML/Word notes."""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
import tempfile
import json
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from forsic_plugin.report_driven.reader_projection import cite_pages, evidence_references, reference_appendix
from forsic_plugin.report_driven.render_views import render_html, render_docx, bookmark_id
from forsic_plugin.report_driven.views import _template_appendices
from test_report_template_fidelity import HTMLLinks, NS, W


def reference_state():
    sources=[dict(id='C'+str(i),version='native-version-'+str(i),label='Synthetic file '+str(i),
        locator='activity.sqlite3#C'+str(i),content_sha256=str(i)*64,
        source_generation_group='native-execution-'+str(i),provenance='forsic_read / native-start-'+str(i),
        source_role='tool_result',coverage='selected line only') for i in (1,2)]
    observation=dict(id='OBS-full-native-id',version='observation-version',
        source_ref=dict(kind='source',id='C1',version='native-version-1'),
        literal='C1 is literal evidence; notC1 [99] <script> remains an exact quote.',
        field_pointer='/lines/0/text',byte_start=5,byte_end=70,canonical_sha256='a'*64,
        presented_view='window',coordinate_basis='retained scalar UTF-8 bytes',
        interpretation_limit='Success is not established.',acquisition_context={'tool':'forsic_read','field_role':'body'})
    return dict(sources=sources,observations=[observation],questions=[],claims=[],scope={'id':'scope'})


def reader_view():
    state=reference_state()
    refs=[dict(kind='observation',id='OBS-full-native-id',version='observation-version'),
          dict(kind='source',id='C2',version='native-version-2')]
    pages=[dict(id='body',title='Synthetic report',section='body',blocks=[
        dict(kind='paragraph',text='C1 is prose, unknown-id [99] stays unchanged.',citation_refs=refs),
        dict(kind='paragraph',text='Same selected observation.',citation_refs=[refs[0]])])]
    before=deepcopy(pages);cited=cite_pages(pages,state)
    assert pages==before
    return dict(data_mode='synthetic',title='Synthetic report',security='Internal',
        snapshot_revision='synthetic-snapshot',pages=[*cited,reference_appendix(state)])


class NumberedReferenceTests(unittest.TestCase):
    def test_bundle_quality_distinguishes_authored_prose_from_explicit_technical_snapshot(self):
        from test_report_narrative import ReportNarrativeTests
        from forsic_plugin.report_driven.host import invoke, current
        from forsic_plugin.report_driven.narrative import status
        author=ReportNarrativeTests('test_native_render_requests_authored_prose_not_blank_template')
        author.setUp();self.addCleanup(author.tearDown)
        partial=invoke(author.case,{'action':'render','allow_partial':True})
        metadata=json.loads(Path(partial['manifest']).read_text())
        self.assertEqual(partial['editorial_status'],'technical_partial')
        self.assertEqual(metadata['editorial_status'],'technical_partial')
        self.assertFalse(metadata['editorial']['semantic_approval'])
        self.assertIn('독자용 본문 미작성',Path(next(p for p in partial['files'] if p.endswith('.html'))).read_text())
        author.finish()
        authored=invoke(author.case,{'action':'render'})
        metadata=json.loads(Path(authored['manifest']).read_text())
        progress=status(current(author.case))
        self.assertEqual(authored['editorial_status'],'authored_current')
        self.assertEqual(metadata['editorial']['basis_id'],progress['basis_id'])
        self.assertEqual(metadata['editorial']['revision'],progress['revision'])
        self.assertFalse(metadata['editorial']['semantic_approval'])

    def test_empty_new_reader_projections_do_not_stale_unchanged_historic_reports(self):
        from test_report_driven import fixture
        from forsic_plugin.report_driven.host import current
        from forsic_plugin.report_driven.views import report_material
        with tempfile.TemporaryDirectory() as root:
            case,_,_=fixture(root);state=current(case);historic=deepcopy(state)
            historic.pop('report_narratives',None)
            for question in historic['questions']:question.pop('reader_explanation',None)
            self.assertEqual(report_material(state),report_material(historic))

    def test_large_report_history_is_unknown_before_any_full_state_projection(self):
        from test_report_driven import fixture
        from forsic_plugin.investigation_view import _report_current_basis
        with tempfile.TemporaryDirectory() as root:
            case,_,_=fixture(root)
            with closing(sqlite3.connect(case.db)) as db, db:
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)',('large-synthetic-source',1,'tool_result','',
                    json.dumps({'tool':'forsic_read','data':{'text':'x'*(4*1024*1024)}})))
            before=case.db.read_bytes()
            with patch('forsic_plugin.report_driven.host.current',side_effect=AssertionError('unbounded projection')):
                self.assertIsNone(_report_current_basis(case))
            self.assertEqual(case.db.read_bytes(),before)

    def test_report_history_uses_verified_snapshots_and_reports_changes_without_writes(self):
        from test_report_driven import fixture
        from forsic_plugin.report_driven.host import current
        from forsic_plugin.report_driven.views import bundle
        from forsic_plugin.investigation_view import _reports
        from forsic_plugin.notes import note
        with tempfile.TemporaryDirectory() as root:
            case,records,saved_note=fixture(root);state=current(case);result=bundle(case,state)
            before=case.db.read_bytes();manifest_path=Path(result['manifest']);manifest_bytes=manifest_path.read_bytes()
            listing={'reports':[]};_reports(case,state['meta']['case_id'],listing,{})
            self.assertTrue(listing['reports'][0]['current'])
            self.assertEqual(listing['reports'][0]['comparison_status'],'current')
            self.assertEqual(listing['reports'][0]['changes'],[])
            self.assertEqual(case.db.read_bytes(),before)
            note(case,dict(action='save',note_id=saved_note['note_id'],revision=saved_note['revision'],
                question=saved_note['question'],answer='Changed current canonical answer.',
                evidence_ids=[r['evidence_id'] for r in records]))
            listing={'reports':[]};_reports(case,state['meta']['case_id'],listing,{})
            self.assertFalse(listing['reports'][0]['current'])
            self.assertEqual(listing['reports'][0]['comparison_status'],'changed')
            self.assertIn('answer',listing['reports'][0]['changes'][0]['kinds'])
            self.assertEqual(manifest_path.read_bytes(),manifest_bytes)
            with patch('forsic_plugin.investigation_view._report_current_basis',return_value=None):
                listing={'reports':[]};_reports(case,state['meta']['case_id'],listing,{})
            self.assertEqual(listing['reports'][0]['comparison_status'],'unavailable')
            self.assertNotIn('current',listing['reports'][0])

    def test_shared_question_explanation_is_used_without_rewriting_and_canonical_answer_once(self):
        from test_report_driven import fixture
        from forsic_plugin.report_driven.host import current
        from forsic_plugin.report_driven.views import compile_views
        with tempfile.TemporaryDirectory() as root:
            case,_,_=fixture(root);state=current(case);question=state['questions'][0]
            source=state['sources'][0]
            question['reader_explanation']=dict(status='current',title='Shared readable title',
                paragraphs=[dict(text='Shared explanation unchanged.',source_refs=[dict(kind='source',id=source['id'],version=source['version'])])],
                counterarguments=['Shared counterargument.'],limitations=['Shared important limitation.'])
            before=deepcopy(state);view=compile_views(state)['integrated']
            visible=''.join(HTMLLinks(render_html(view)).text)
            self.assertEqual(visible.count('Shared explanation unchanged.'),1)
            self.assertEqual(visible.count(question['answer']),1)
            for text in ('Shared readable title','Shared counterargument.','Shared important limitation.'):
                self.assertIn(text,visible)
            self.assertEqual(state,before)
            question['reader_explanation']['status']='stale'
            stale=''.join(HTMLLinks(render_html(compile_views(state)['integrated'])).text)
            self.assertNotIn('Shared explanation unchanged.',stale)
            self.assertEqual(stale.count(question['answer']),1)
            question['reader_explanation'].update(status='current',paragraphs=[dict(text=question['answer'])])
            same=''.join(HTMLLinks(render_html(compile_views(state)['integrated'])).text)
            self.assertEqual(same.count(question['answer']),1)

    def test_report_library_fallback_is_human_readable_without_rewriting_manifest(self):
        from forsic_plugin.investigation_view import _reports
        with tempfile.TemporaryDirectory() as root:
            output=Path(root);ident='b'*64;directory=output/('report-bundle-'+ident);directory.mkdir()
            (directory/'integrated.html').write_text('Synthetic report')
            manifest=json.dumps(dict(case_id='synthetic',bundle_id=ident,
                files=[{'name':'integrated.html'},{'name':'manifest.json'}]))
            (directory/'manifest.json').write_text(manifest)
            result={'reports':[]};counts={}
            _reports(SimpleNamespace(output=output),'synthetic',result,counts)
            self.assertEqual(result['reports'][0]['title'],'정보시스템 침해사고 조사 결과')
            self.assertEqual([f['name'] for f in result['reports'][0]['files']],['integrated.html'])
            self.assertEqual((directory/'manifest.json').read_text(),manifest)

    def test_only_typed_exact_references_become_numbers_without_prose_regex(self):
        view=reader_view();paragraphs=view['pages'][0]['blocks']
        self.assertEqual(paragraphs[0]['text'],'C1 is prose, unknown-id [99] stays unchanged. [1], [3]')
        self.assertEqual(paragraphs[1]['text'],'Same selected observation. [1]')
        state=reference_state();pages=deepcopy(view['pages'][:1])
        pages[0]['blocks'][0]['citation_refs'][0]['version']='invented-version'
        with self.assertRaisesRegex(ValueError,'exact report citation'):cite_pages(pages,state)

    def test_html_and_docx_links_resolve_to_same_bottom_notes_and_native_chain(self):
        view=reader_view();before=deepcopy(view)
        markup=render_html(view);parsed=HTMLLinks(markup)
        self.assertEqual(len(parsed.ids),len(set(parsed.ids)))
        self.assertTrue(set(parsed.links)<=set(parsed.ids))
        for label in ('[1]','[3]'):
            self.assertIn(bookmark_id(label),parsed.links)
            self.assertIn(bookmark_id(label),parsed.ids)
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'report.docx'
            with patch('socket.socket',side_effect=AssertionError('no network')):render_docx(view,output)
            with ZipFile(output) as package:document=ET.fromstring(package.read('word/document.xml'))
            names=[x.get(W+'name') for x in document.findall('.//w:bookmarkStart',NS)]
            links=[x.get(W+'anchor') for x in document.findall('.//w:hyperlink',NS)]
            self.assertTrue(set(links)<=set(names));self.assertEqual(len(names),len(set(names)))
            visible=''.join(document.itertext())
        for value in ('C1','C2','OBS-full-native-id','native-version-1','observation-version',
                      'native-execution-1','native-start-1','/lines/0/text','UTF-8 [5,70)',
                      reference_state()['observations'][0]['literal'],'Success is not established.'):
            self.assertIn(value,''.join(parsed.text));self.assertIn(value,visible)
        self.assertNotIn('<script>',markup);self.assertEqual(view,before)

    def test_stale_native_version_has_separate_note_not_current_record_content(self):
        state=reference_state();old=dict(kind='source',id='C1',version='old-native-version')
        state['claims']=[{'source_refs':[old]}]
        references=evidence_references(state)
        current=next(r for r in references if r['id']=='C1' and r['version']=='native-version-1')
        previous=next(r for r in references if r['id']=='C1' and r['version']=='old-native-version')
        self.assertNotEqual(current['label'],previous['label']);self.assertIsNone(previous['record'])
        notes=reference_appendix(state)
        at=next(i for i,b in enumerate(notes['blocks']) if b.get('id')==previous['label'])
        old_rows=notes['blocks'][at+1]['rows']
        self.assertIn('old-native-version',str(old_rows));self.assertNotIn('Synthetic file 1',str(old_rows))
        self.assertIn('현재 버전으로 대체하지 않습니다',str(old_rows))
        state['report_narratives']=[{'citation_refs':[dict(kind='source',id='old-editorial-source',version='old')]}]
        self.assertNotIn('old-editorial-source',str(reference_appendix(state)))

    def test_identical_appendix_tables_have_one_copy_and_working_cross_links(self):
        table=dict(kind='table',headers=['Answer','Limits'],rows=[['Exact authored answer','Important limit']],widths=[50,50])
        empty=dict(kind='table',headers=['Tests','Results'],rows=[['기록 없음','']],widths=[50,50])
        pages=[dict(id=key,title=key,blocks=[deepcopy(table),deepcopy(empty)]) for key in ('A01','A04','A06')]
        before=deepcopy(pages);grouped=_template_appendices(pages)
        retained=[b for p in grouped for b in p['blocks'] if b['kind']=='table']
        self.assertEqual(len(retained),1);self.assertEqual(retained[0]['rows'],table['rows'])
        self.assertEqual(pages,before)
        view=dict(data_mode='synthetic',title='Report',security='Internal',snapshot_revision='snapshot',pages=grouped)
        parsed=HTMLLinks(render_html(view))
        self.assertTrue(set(parsed.links)<=set(parsed.ids))
        self.assertEqual(''.join(parsed.text).count('Exact authored answer'),1)
        self.assertEqual(''.join(parsed.text).count('별도로 작성된 기록 없음'),1)


if __name__=='__main__':unittest.main()
