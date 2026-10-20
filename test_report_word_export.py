"""Lazy Word exports use only immutable reader inputs; no model or live case."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from forsic_plugin.notes import note
from forsic_plugin.report_driven.host import current,digest
from forsic_plugin.report_driven.views import bundle
from forsic_plugin.report_driven.finalization import report_files
from forsic_plugin.report_driven import render_views
from forsic_plugin.report_driven.word_export import export_word,validate_inputs,INPUT_NAMES,FORMATS
from test_report_driven import fixture,worked


def word_text(path):
    from xml.etree import ElementTree as ET
    with ZipFile(path) as archive:return ''.join(ET.fromstring(archive.read('word/document.xml')).itertext())


class ReportWordExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.case,self.reads,self.note=fixture(Path(self.tmp.name)/'case')
        self.state=worked(self.case)
        self.network=patch('socket.socket',side_effect=AssertionError('No model or network'))
        self.network.start();self.addCleanup(self.network.stop)

    def render(self,**kwargs):
        result=bundle(self.case,self.state,**kwargs)
        self.root=Path(result['manifest']).parent
        self.manifest=json.loads(Path(result['manifest']).read_text())
        return result

    def test_default_is_html_only_and_inputs_are_private_manifest_bound(self):
        with patch.object(render_views,'render_docx',side_effect=AssertionError('Default render must not generate Word')):
            result=self.render()
        self.assertEqual([Path(p).name for p in result['files']],['integrated.html'])
        self.assertFalse((self.root/'integrated.docx').exists())
        self.assertEqual(self.manifest['document_formats'],FORMATS)
        self.assertEqual({x['name'] for x in self.manifest['files']},{'integrated.html','selected-evidence.zip'})
        for kind,name in INPUT_NAMES.items():
            record=self.manifest['lazy_word']['inputs'][kind];raw=(self.root/name).read_bytes()
            self.assertEqual(record['sha256'],hashlib.sha256(raw).hexdigest())
            self.assertEqual((self.root/name).stat().st_mode&0o777,0o600)
        _,files=report_files(self.case.output,result['bundle_id'],self.case.config['case_id'])
        self.assertEqual([record['name'] for record in files],['integrated.html'])

    def test_saved_words_template_and_style_survive_current_judgment_and_global_style_changes(self):
        result=self.render();before={p:p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        original_template=(self.root/INPUT_NAMES['template']).read_bytes()
        saved=note(self.case,dict(action='get',note_id=self.note['note_id']))['note']
        note(self.case,dict(action='save',note_id=saved['note_id'],revision=saved['revision'],
             question=saved['question'],answer='A later answer must not rewrite this Word document.',
             evidence_ids=saved['evidence_ids']))
        changed_style=deepcopy(render_views.STYLE);changed_style['components']={'invalid':99999}
        with (patch('forsic_plugin.report_driven.host.current',side_effect=AssertionError('No current state read')),
             patch('forsic_plugin.report_driven.views.compile_views',side_effect=AssertionError('No new reader projection')),
             patch.object(render_views,'TEMPLATE_PATH',Path(self.tmp.name)/'missing-template.docx'),
             patch.object(render_views,'STYLE',changed_style)):
            exported=export_word(self.case,result['bundle_id'])
        visible=word_text(exported['path'])
        self.assertIn(self.state['questions'][0]['answer'],visible)
        self.assertNotIn('A later answer must not rewrite',visible)
        with ZipFile(exported['path']) as package,ZipFile(__import__('io').BytesIO(original_template)) as template:
            self.assertEqual(package.read('word/styles.xml'),template.read('word/styles.xml'))
        self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_repeated_and_concurrent_downloads_render_once_and_reuse_verified_cache(self):
        result=self.render();original=render_views.render_docx;counter=[];guard=threading.Lock()
        def render(*args,**kwargs):
            with guard:counter.append(1)
            return original(*args,**kwargs)
        with patch.object(render_views,'render_docx',side_effect=render):
            with ThreadPoolExecutor(max_workers=4) as workers:
                exported=list(workers.map(lambda _:export_word(self.case,result['bundle_id']),range(4)))
        self.assertEqual(len(counter),1)
        self.assertEqual(sum(not item['cached'] for item in exported),1)
        self.assertEqual(len({item['sha256'] for item in exported}),1)
        # Compatible renderer changes do not replace an already generated file.
        with (patch('forsic_plugin.report_driven.word_export._renderer_hash',return_value='b'*64),
             patch.object(render_views,'render_docx',side_effect=AssertionError('Reuse cache'))):
            cached=export_word(self.case,result['bundle_id'])
        self.assertTrue(cached['cached']);self.assertEqual(cached['sha256'],exported[0]['sha256'])

    def test_supported_contract_allows_first_export_after_compatible_renderer_upgrade(self):
        result=self.render()
        with patch('forsic_plugin.report_driven.word_export._renderer_hash',return_value='b'*64):
            exported=export_word(self.case,result['bundle_id'])
        self.assertEqual(exported['receipt']['actual_renderer_sha256'],'b'*64)
        self.assertEqual(exported['receipt']['original_renderer_sha256'],self.manifest['lazy_word']['renderer_sha256'])
        unsupported=deepcopy(self.manifest['lazy_word']);unsupported['renderer_contract']='unknown-contract'
        with self.assertRaisesRegex(ValueError,'renderer contract'):validate_inputs(self.root,unsupported)

    def test_snapshot_tampering_and_unknown_public_files_are_not_accepted(self):
        result=self.render();manifest=deepcopy(self.manifest)
        manifest['files'].append(dict(name='unexpected.txt',sha256=hashlib.sha256(b'extra').hexdigest(),bytes=5))
        manifest.pop('bundle_id');ident=digest(manifest);manifest['bundle_id']=ident
        derived=self.case.output/('report-bundle-'+ident);shutil.copytree(self.root,derived)
        (derived/'unexpected.txt').write_bytes(b'extra');(derived/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'Invalid report artifacts'):export_word(self.case,ident)
        snapshot=self.case.output/('report-state-'+result['snapshot_id']+'.json');snapshot.write_text('{}')
        with self.assertRaisesRegex(ValueError,'snapshot changed'):export_word(self.case,result['bundle_id'])

    def test_redacted_saved_view_and_local_sensitive_appendix_are_used_exactly(self):
        appendix={'total_findings':1,'returned_findings':1,'omitted_findings':0,'omitted_bytes':0,
            'entries':[dict(finding_id='synthetic-sensitive',input_id='input',
                source_ref=dict(id=self.state['sources'][0]['id'],version=self.state['sources'][0]['version'],pointer='/text'),
                original_location={},source_role='tool_result',excerpt='LOCAL-ONLY-ORIGINAL',encoding='UTF-8',
                excerpt_byte_start=0,excerpt_byte_end=19,match_byte_start=0,match_byte_end=19)],
            'limitations':[],'contains_sensitive_originals':True}
        with patch('forsic_plugin.sensitive_policy.report_appendix',return_value=appendix):
            result=self.render(redact=['maintenance'])
        exported=export_word(self.case,result['bundle_id']);visible=word_text(exported['path'])
        self.assertNotIn('maintenance',visible);self.assertIn('[비공개]',visible)
        self.assertIn('LOCAL-ONLY-ORIGINAL',visible);self.assertTrue(exported['local_only'])
        self.assertEqual(exported['delivery_policy'],'local_only_sensitive_originals')

    def test_changed_inputs_cache_and_receipt_are_refused(self):
        result=self.render();exported=export_word(self.case,result['bundle_id'])
        word=exported['path'];original=word.read_bytes();word.write_bytes(b'changed Word')
        with self.assertRaisesRegex(ValueError,'Cached Word'):export_word(self.case,result['bundle_id'])
        word.write_bytes(original)
        receipt=word.parent/'receipt.json';saved=receipt.read_bytes();value=json.loads(saved)
        value['actual_renderer_sha256']='c'*64;receipt.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'receipt changed'):export_word(self.case,result['bundle_id'])
        receipt.write_bytes(saved)
        view=self.root/INPUT_NAMES['view'];view.write_bytes(view.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'input changed'):export_word(self.case,result['bundle_id'])

    def test_symlink_inputs_cache_and_foreign_case_fail(self):
        result=self.render();view=self.root/INPUT_NAMES['view'];raw=view.read_bytes()
        outside=Path(self.tmp.name)/'outside';outside.write_bytes(raw);view.unlink();view.symlink_to(outside)
        with self.assertRaises((OSError,ValueError)):export_word(self.case,result['bundle_id'])
        view.unlink();view.write_bytes(raw)
        cache=self.case.output/'.word-exports';cache.symlink_to(Path(self.tmp.name))
        with self.assertRaises(ValueError):export_word(self.case,result['bundle_id'])
        cache.unlink()
        foreign,_,_=fixture(Path(self.tmp.name)/'foreign');foreign.config['case_id']='FOREIGN'
        shutil.copytree(self.root,foreign.output/self.root.name)
        with self.assertRaisesRegex(ValueError,'case, bundle or snapshot'):export_word(foreign,result['bundle_id'])

    def test_interrupted_generation_leaves_no_half_cache_and_can_retry(self):
        result=self.render()
        def interrupt(view,path,**kwargs):path.write_bytes(b'partial');raise RuntimeError('synthetic interruption')
        with patch.object(render_views,'render_docx',side_effect=interrupt),self.assertRaisesRegex(RuntimeError,'interruption'):
            export_word(self.case,result['bundle_id'])
        cache=self.case.output/'.word-exports'
        self.assertFalse(any(p.is_dir() for p in cache.iterdir()))
        self.assertFalse(export_word(self.case,result['bundle_id'])['cached'])

    def test_legacy_existing_docx_remains_supported_without_generation(self):
        result=self.render();exported=export_word(self.case,result['bundle_id'])
        manifest=deepcopy(self.manifest);manifest.pop('bundle_id');manifest.pop('document_formats');manifest.pop('lazy_word')
        raw=exported['path'].read_bytes();manifest['files'].append(dict(name='integrated.docx',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
        ident=digest(manifest);manifest['bundle_id']=ident
        legacy=self.case.output/('report-bundle-'+ident);shutil.copytree(self.root,legacy)
        (legacy/'integrated.docx').write_bytes(raw);(legacy/'manifest.json').write_text(json.dumps(manifest))
        with patch.object(render_views,'render_docx',side_effect=AssertionError('Legacy Word already exists')):
            existing=export_word(self.case,ident)
        self.assertTrue(existing['legacy']);self.assertEqual(existing['path'],legacy/'integrated.docx')


if __name__=='__main__':unittest.main()
