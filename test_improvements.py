import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import patch

from forsic_plugin.evidence import Case
from forsic_plugin.intake import write_json
from forsic_plugin.notes import timeline
from forsic_plugin import verification
from telegram_mirror import Mirror


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        evidence = self.root / 'evidence'
        evidence.mkdir()
        (evidence / 'events.log').write_text('scheduled\npermission denied\n')
        manifest = self.root / 'case.json'
        write_json(manifest, {'case_id': 'synthetic', 'label': 'synthetic', 'synthetic': True,
                             'evidence_root': str(evidence), 'output_root': str(self.root / 'results')})
        self.case = Case(manifest)
        self.source = self.call('read', path='events.log')['evidence_id']

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, tool, **args):
        return json.loads(self.case.invoke('forsic_' + tool, args))

    def draft(self):
        return {'summary': '실행 성공은 아직 미확인입니다.', 'findings': [
            {'title': '실행 실패 가능성', 'kind': 'hypothesis', 'detail': '권한 오류 기록이 있습니다.',
             'evidence_ids': [self.source]}]}

    def test_citable_not_found_does_not_claim_deletion(self):
        missing = self.call('read', path='missing')
        self.assertEqual(missing['outcome'], 'not_found')
        self.assertIn('not established', missing['meaning'])
        saved = self.call('note', action='save', question='존재?', answer='선택 범위에서 찾지 못함',
                          evidence_ids=[missing['evidence_id']])
        self.assertNotIn('error', saved)
        self.assertNotIn('outcome', self.call('read', path='../outside'))

    def test_search_scope_and_read_pages(self):
        result = self.call('search', path='events.log', text='success')
        self.assertEqual(result['search_text'], 'success')
        self.assertEqual(result['search_path'], 'events.log')
        self.assertEqual(result['matches'], [])
        page = self.call('read', path='events.log', line_count=1)
        self.assertEqual((page['line_start'], page['line_end'], page['next_line']), (1, 1, 2))

    def test_stat_time_comes_from_cited_epoch(self):
        eid = self.case.record('tool_result', {'tool': 'forsic_image_files', 'times': {'mtime': 1770856431.0163112}})
        item = {'description': '수정 시각', 'evidence_ids': [eid],
                'time_source': {'evidence_id': eid, 'field': 'mtime'}}
        result = timeline([item], self.case)[0]
        self.assertEqual(result['display_time'], '2026-02-12 09:33:51 KST')
        self.assertEqual(result['raw_time'], 1770856431.0163112)
        with self.assertRaises(ValueError):
            timeline([{**item, 'evidence_ids': [self.source]}], self.case)
        report = self.call('report', snapshot={**self.draft(), 'timeline': [item]})
        self.assertNotIn('error', report)
        self.assertIn('2026-02-12 09:33:51 KST', (self.case.output / report['reports'][0]['html']).read_text())

    def test_critical_gap_preserved_in_both_reports(self):
        self.call('note', action='save', question='성공?', answer='미확인', status='needs_input',
                  critical_gaps=['실행 결과가 필요'], evidence_ids=[self.source])
        result = self.call('report', snapshot=self.draft(), analysis_status='complete')
        self.assertEqual(result['analysis_status'], 'partial')
        for report in result['reports']:
            html = (self.case.output / report['html']).read_text()
            self.assertIn('실행 결과가 필요', html)
            self.assertIn('가설 · ', html)
            self.assertIn('부분 조사', html)

    def test_unlinked_critical_gap_in_note_derived_report(self):
        self.call('note', action='save', question='범위?', answer='추가 자료 필요', critical_gaps=['원본 누락'])
        result = self.call('report', analysis_status='complete')
        self.assertEqual(result['analysis_status'], 'partial')

    def test_background_verification_is_reused_and_status_never_starts(self):
        (self.case.root / 'disk.E01').write_bytes(b'synthetic, not EWF')
        with patch.object(verification.subprocess, 'Popen') as launch:
            self.assertEqual(self.call('verify', path='disk.E01', action='status')['status'], 'not_started')
            launch.assert_not_called()
            first = self.call('verify', path='disk.E01')
            second = self.call('verify', path='disk.E01')
            self.assertEqual(launch.call_count, 1)
            self.assertEqual(first['job_id'], second['job_id'])
            self.assertEqual(first['timeout_seconds'], 21600)
            self.assertTrue(second['reused'])
            self.assertFalse(second['verification_complete'])

    def test_verification_timeout_remains_unverified_without_rerun(self):
        (self.case.root / 'disk.E01').write_bytes(b'synthetic')
        with patch.object(verification.subprocess, 'Popen'):
            first = self.call('verify', path='disk.E01')
        with patch.object(verification.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ewfverify', 21600)) as run:
            verification.run(first['receipt'])
            verification.run(first['receipt'])
            self.assertEqual(run.call_count, 1)
        result = self.call('verify', path='disk.E01', action='status')
        self.assertEqual(result['status'], 'timed_out')
        self.assertFalse(result['verification_complete'])

    def test_changed_segments_do_not_pass_verification(self):
        source = self.case.root / 'disk.E01'
        source.write_bytes(b'synthetic')
        with patch.object(verification.subprocess, 'Popen'):
            first = self.call('verify', path='disk.E01')
        source.write_bytes(b'changed content')
        with patch.object(verification.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            verification.run(first['receipt'])
        result = json.loads(Path(first['receipt']).read_text())
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['verification_complete'])

    def test_missing_verify_worker_not_reported_as_running_or_restarted(self):
        (self.case.root / 'disk.E01').write_bytes(b'synthetic')
        with patch.object(verification.subprocess, 'Popen'):
            first = self.call('verify', path='disk.E01')
        write_json(first['receipt'], {**first, 'status': 'running', 'pid': 12345})
        with patch.object(verification.os, 'kill', side_effect=ProcessLookupError), \
             patch.object(verification.subprocess, 'Popen') as launch:
            result = self.call('verify', path='disk.E01')
            self.assertEqual(result['status'], 'worker_unavailable')
            self.assertFalse(result['verification_complete'])
            launch.assert_not_called()

    def test_native_review_separate_context_cached_no_private_reasoning(self):
        chunk = Obj(model='qwen3.8:latest', usage=None, choices=[Obj(
            delta=Obj(content='실행 성공 단정을 피하세요.', reasoning_content='private text'), finish_reason='stop')])
        config = {'model': {'provider': 'custom', 'default': 'qwen3.8:latest', 'base_url': 'http://localhost:11434/v1'}}
        with patch('hermes_cli.config.load_config_readonly', return_value=config), \
             patch('agent.auxiliary_client.call_llm', return_value=iter([chunk])) as model:
            first = self.call('report', action='review', snapshot=self.draft())
            second = self.call('report', action='review', snapshot=self.draft())
            self.assertEqual(model.call_count, 1)
            self.assertTrue(model.call_args.kwargs['stream'])
            messages = model.call_args.kwargs['messages']
            self.assertEqual(len(messages), 2)
            self.assertIn('permission denied', messages[1]['content'])
            self.assertNotIn('private text', json.dumps(first))
            self.assertEqual(first['review_status'], 'received')
            self.assertTrue(second['reused'])
            self.assertNotIn('error', self.call('report', snapshot_id=second['snapshot_id']))

    def test_review_failure_retained_and_partial_report_still_saved(self):
        config = {'model': {'provider': 'custom', 'default': 'qwen3.8:latest', 'base_url': 'http://localhost:11434/v1'}}
        with patch('hermes_cli.config.load_config_readonly', return_value=config), \
             patch('agent.auxiliary_client.call_llm', side_effect=TimeoutError('secret error body')) as model:
            first = self.call('report', action='review', snapshot=self.draft())
            second = self.call('report', action='review', snapshot=self.draft())
            self.assertEqual(model.call_count, 1)
            self.assertTrue(second['reused'])
            self.assertEqual(first['review_status'], 'delivery_unconfirmed')
            self.assertNotIn('secret error', json.dumps(first))
            self.assertEqual(self.call('report', snapshot_id=first['snapshot_id'])['analysis_status'], 'partial')

    def test_large_review_not_silently_cut_or_sent(self):
        config = {'model': {'provider': 'custom', 'default': 'qwen3.8:latest', 'base_url': 'http://localhost:11434/v1'}}
        with patch('hermes_cli.config.load_config_readonly', return_value=config), \
             patch('agent.auxiliary_client.call_llm') as model:
            result = self.call('report', action='review', snapshot={**self.draft(), 'summary': 'x' * 81000})
            self.assertEqual(result['review_status'], 'not_sent')
            model.assert_not_called()

    def test_native_skill_frontmatter_and_complete_body(self):
        from tools.skills_tool import _parse_frontmatter
        folders = list((Path(__file__).parent / 'forsic_plugin/skills').iterdir())
        self.assertEqual(len([p for p in folders if p.is_dir()]), 17)
        self.assertTrue({'organize-indicators','personal-data-scope','initial-access','data-movement'} <= {p.name for p in folders})
        for folder in folders:
            if folder.is_dir():
                metadata, body = _parse_frontmatter((folder / 'SKILL.md').read_text())
                self.assertEqual(metadata['name'], folder.name)
                self.assertTrue(metadata['description'])
                self.assertTrue(body.strip())

    def mirror(self):
        mirror = Mirror(self.root, self.root / 'intake', self.root / 'cursor.json', 'synthetic', -123)
        mirror.state = {'session': 'synthetic', 'chat_id': '-123', 'since': 0, 'pending': None,
                        'status': 'ready', 'delivered': [], 'progress_message_id': 33}
        return mirror

    def test_progress_failure_does_not_block_final_chat(self):
        mirror = self.mirror()
        sent = []
        async def send(text):
            sent.append(text)
            return 42
        async def progress(mid, text):
            raise TimeoutError('private detail')
        with patch.object(mirror, 'batch', return_value=[('event:one', '작업 중'), ('message:two', '포식이\n부분 조사 결과')]):
            asyncio.run(mirror.tick(send, progress))
        self.assertEqual(sent, ['포식이\n부분 조사 결과'])
        self.assertTrue(mirror.state['progress_paused'])
        self.assertEqual(mirror.state['status'], 'ready')
        self.assertNotIn('private detail', mirror.path.read_text())
        self.assertIn('event:one', mirror.state['delivered'])

    def test_legacy_progress_uncertain_preserved_not_resent(self):
        mirror = self.mirror()
        mirror.state.update(pending={'key': 'event:old', 'chunk': 0}, status='delivery_unconfirmed', error_type='BadRequest')
        mirror.isolate_progress_failure()
        self.assertEqual(mirror.state['progress_failure']['key'], 'event:old')
        self.assertIsNone(mirror.state['pending'])
        self.assertTrue(mirror.state['progress_paused'])
        mirror.state.update(pending={'key': 'message:uncertain'}, status='delivery_unconfirmed')
        mirror.isolate_progress_failure()
        self.assertIsNotNone(mirror.state['pending'])


if __name__ == '__main__':
    unittest.main()
