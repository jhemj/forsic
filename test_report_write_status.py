"""Native rejected updates remain distinct from committed answers in new reports."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from forsic_plugin.evidence import Case
from forsic_plugin.report_driven import host, views
from forsic_plugin.report_driven.write_status import projection
from forsic_plugin.report_driven.word_export import export_word


class ReportWriteStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        evidence = root / 'evidence'
        evidence.mkdir()
        (evidence / 'request.log').write_text('request recorded\n')
        manifest = root / 'case.json'
        manifest.write_text(json.dumps(dict(case_id='SYN-write-status', synthetic=True,
            evidence_root=str(evidence), output_root=str(root / 'results'),
            scope='Synthetic request record', question='Was the request completed?')))
        self.case = Case(manifest)
        self.source = json.loads(self.case.invoke('forsic_read', {'path': 'request.log'}))['evidence_id']
        self.q = self.save('initial', question='Was the request completed?', scope='Synthetic request record',
            answer='A request was recorded; completion remains unknown.', evidence_ids=[self.source])['question']

    def save(self, operation, **payload):
        return json.loads(self.case.invoke('forsic_reporting',
            {'action': 'question', 'payload': dict(operation_id=operation, **payload)}))

    def reject(self, operation='failed-update', question=None):
        q = question or self.q
        result = self.save(operation, question_id=q['question_id'], expected_revision=999,
            answer='UNSAVED_PRIVATE_DRAFT_DO_NOT_RENDER', reason='Attempted correction')
        self.assertIn('error', result)
        return result

    def read_bundle(self, result):
        original = Path(result['manifest']).read_bytes()
        manifest = json.loads(original)
        self.assertEqual({Path(name).suffix for name in result['files']}, {'.html'})
        word = export_word(self.case, result['bundle_id'])
        self.assertEqual(word['snapshot_id'], manifest['snapshot_id'])
        self.assertEqual(Path(result['manifest']).read_bytes(), original)
        documents = {}
        for name in [*result['files'], word['path']]:
            path = Path(name)
            if path.suffix == '.docx':
                with ZipFile(path) as archive:
                    documents[path.name] = archive.read('word/document.xml').decode()
            else:
                documents[path.name] = path.read_text()
        self.assertEqual({Path(name).suffix for name in documents}, {'.html','.docx'})
        return manifest, documents

    def test_actual_failed_save_bundle_and_later_saved_correction(self):
        before = host.current(self.case)
        historical = views.bundle(self.case, before)
        historical_bytes = {p: Path(p).read_bytes() for p in [historical['manifest'], *historical['files']]}
        failed = self.reject()
        after = host.current(self.case)
        self.assertEqual(after, before)
        frozen = deepcopy(after)
        result = views.bundle(self.case, after)
        manifest, documents = self.read_bundle(result)
        status = manifest['write_status']
        self.assertEqual(status['affected_question_count'], 1)
        self.assertEqual(status['notices'][0]['failed_result_id'], failed['evidence_id'])
        self.assertEqual(status['notices'][0]['committed_revision'], 1)
        self.assertEqual(manifest['snapshot_id'], historical['snapshot_id'])
        self.assertNotEqual(result['bundle_id'], historical['bundle_id'])
        self.assertNotEqual(manifest['write_status_cutoff']['event_id'], manifest['ledger_cutoff'])
        for text in documents.values():
            self.assertIn('수정 저장 상태', text)
            self.assertIn('변경 내용은 저장되지 않았습니다', text)
            self.assertIn('현재 저장된 revision 1', text)
            self.assertNotIn('UNSAVED_PRIVATE_DRAFT_DO_NOT_RENDER', text)
        self.assertNotIn('UNSAVED_PRIVATE_DRAFT_DO_NOT_RENDER', json.dumps(manifest))
        self.assertEqual(after, frozen)
        corrected = self.save('corrected', question_id=self.q['question_id'], expected_revision=1,
            answer='The saved correction still leaves completion unknown.', reason='Retain source limits')
        self.assertNotIn('error', corrected, corrected)
        now = host.current(self.case)
        fresh, rendered = self.read_bundle(views.bundle(self.case, now))
        self.assertEqual(fresh['write_status']['affected_question_count'], 0)
        self.assertEqual(host.find(now, 'questions', self.q['question_id'])['revision'], 2)
        self.assertTrue(all('변경 내용은 저장되지 않았습니다' not in t for t in rendered.values()))
        self.assertEqual({p: Path(p).read_bytes() for p in historical_bytes}, historical_bytes)

    def test_selected_questions_and_fixed_status_cutoff_exclude_unrelated_failures(self):
        other = self.save('other', question='Was authorization observed?', scope='Synthetic request record',
            answer='Authorization remains unknown.', evidence_ids=[self.source])['question']
        state = host.current(self.case)
        fixed = projection(self.case, state)
        json.loads(self.case.invoke('forsic_read', {'path': 'missing.log'}))
        draft = self.save('new-draft', answer='UNSAVED_PRIVATE_DRAFT_DO_NOT_RENDER')
        self.assertIn('error', draft)
        self.assertEqual(projection(self.case, state)['status'], fixed['status'])
        self.reject(question=other)
        self.assertEqual(projection(self.case, state, cutoff=fixed['cutoff'])['status'], fixed['status'])
        state = host.current(self.case)
        selected, _ = self.read_bundle(views.bundle(self.case, state, question_ids=[self.q['question_id']]))
        self.assertEqual(selected['write_status']['affected_question_count'], 0)
        selected_other, _ = self.read_bundle(views.bundle(self.case, state, question_ids=[other['question_id']]))
        self.assertEqual([n['question_id'] for n in selected_other['write_status']['notices']], [other['question_id']])

    def test_relevant_failure_during_render_refuses_registration_but_telemetry_does_not(self):
        state = host.current(self.case)
        render = views.render_html
        once = False

        def fail_during_render(value):
            nonlocal once
            if not once:
                once = True
                self.reject()
            return render(value)

        with patch.object(views, 'render_html', side_effect=fail_during_render):
            with self.assertRaisesRegex(ValueError, 'save status changed during render'):
                views.bundle(self.case, state)
        self.assertFalse(list(self.case.output.glob('report-bundle-*')))
        once = False

        def telemetry_during_render(value):
            nonlocal once
            if not once:
                once = True
                self.case.record('post_api_request', {'unused': 'synthetic telemetry'})
            return render(value)

        with patch.object(views, 'render_html', side_effect=telemetry_during_render):
            result = views.bundle(self.case, host.current(self.case))
        self.assertTrue(Path(result['manifest']).is_file())

    def test_saved_revision_after_attempt_start_is_not_an_unsaved_correction(self):
        original = host.invoke
        for scenario in ('saved_then_delivery_error', 'concurrent_saved_revision_then_late_error'):
            with self.subTest(scenario=scenario):
                question = host.find(host.current(self.case), 'questions', self.q['question_id'])
                answer = 'Committed correction for ' + scenario

                def fail_after_commit(case, args):
                    operation = args.get('payload', {}).get('operation_id')
                    if operation != scenario:
                        return original(case, args)
                    if scenario == 'saved_then_delivery_error':
                        original(case, args)
                    else:
                        # A second native call commits while the first attempt is
                        # still in flight, before its delayed error is returned.
                        saved = self.save('concurrent-success', question_id=question['id'],
                            expected_revision=question['revision'], answer=answer,
                            reason='Preserve the actual saved revision')
                        self.assertNotIn('error', saved, saved)
                    raise OSError('Synthetic response delivery failure after a committed revision')

                with patch.object(host, 'invoke', side_effect=fail_after_commit):
                    failed = self.save(scenario, question_id=question['id'],
                        expected_revision=question['revision'], answer=answer,
                        reason='Preserve the actual saved revision')
                self.assertIn('error', failed)
                state = host.current(self.case)
                current = host.find(state, 'questions', question['id'])
                self.assertEqual(current['answer'], answer)
                self.assertEqual(current['revision'], question['revision'] + 1)
                self.assertEqual(projection(self.case, state)['status']['affected_question_count'], 0)
                with self.case.connect() as db:
                    start = db.execute('SELECT rowid FROM events WHERE id=?', (failed['started_id'],)).fetchone()[0]
                    result = db.execute('SELECT rowid FROM events WHERE id=?', (failed['evidence_id'],)).fetchone()[0]
                    committed = db.execute("SELECT MAX(rowid) FROM events WHERE kind='note' "
                        "AND json_extract(data,'$.note_id')=?", (question['id'].removeprefix('Q-'),)).fetchone()[0]
                self.assertLess(start, committed)
                self.assertLess(committed, result)


if __name__ == '__main__':
    unittest.main()
