import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from forsic_plugin.evidence import Case
from forsic_plugin.notes import timeline
from forsic_plugin.image_reader import search_node, select_volume
from types import SimpleNamespace


class Node:
    def __init__(self, path):
        self.path = Path(path)
    def lstat(self): return self.path.lstat()
    def open(self): return self.path.open('rb')
    def get(self): return self
    def scandir(self): return (Node(p) for p in self.path.iterdir())


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'evidence'
        self.root.mkdir()
        (self.root / 'cron.log').write_text('2026-09-30T10:00:00Z job started\n2026-09-30T10:00:01Z permission denied\n')
        self.manifest = self.base / 'case.json'
        self.manifest.write_text(json.dumps({'case_id': 'synthetic-test', 'evidence_root': str(self.root), 'output_root': str(self.base / 'out'), 'scope': '합성 예약 작업 로그', 'synthetic': True}))
        self.case = Case(self.manifest)
        self.source = self.call('read', path='cron.log')['evidence_id']

    def tearDown(self): self.temp.cleanup()
    def call(self, suffix, **args): return json.loads(self.case.invoke('forsic_' + suffix, args))

    def test_exact_bytes_and_continuation(self):
        value = self.call('read_bytes', path='cron.log', offset=3, length=10)
        import base64
        self.assertEqual(base64.b64decode(value['base64']), (self.root / 'cron.log').read_bytes()[3:13])
        self.assertEqual(value['next_offset'], 13)

    def test_note_revision_and_conflict(self):
        first = self.call('note', action='save', question='성공했나?', answer='실패 기록이다.', evidence_ids=[self.source])['note']
        args = dict(action='save', question='성공했나?', answer='실패 기록이며 시도는 있었다.', evidence_ids=[self.source], note_id=first['note_id'], revision=1, correction_reason='시도와 결과를 구별')
        second = self.call('note', **args)['note']
        self.assertEqual(second['revision'], 2)
        self.assertIn('error', self.call('note', **args))
        self.assertEqual(len(self.call('note', action='get', note_id=first['note_id'])['history']), 2)
        self.assertEqual(len(self.call('note', action='list')['notes']), 1)

    def test_unlinked_note_not_evidence(self):
        self.assertIn('error', self.call('note', action='save', question='q', answer='a', evidence_ids=['invented']))
        note = self.call('note', action='save', question='q', answer='a', evidence_ids=[self.source])
        self.assertIn('error', self.call('note', action='save', question='q', answer='a', evidence_ids=[note['evidence_id']]))

    def test_note_search_and_pagination(self):
        for i in range(3):
            self.call('note', action='save', question=f'실패 {i}', answer='미확인')
        page = self.call('note', action='list', query='실패', limit=2)
        self.assertEqual(page['total'], 3)
        self.assertEqual(page['next_offset'], 2)
        self.assertEqual(len(self.call('note', action='list', query='실패', offset=2)['notes']), 1)
        self.assertEqual(self.call('note', action='list', query='없는말')['total'], 0)

    def test_unlinked_note_remains_gap_in_report(self):
        self.call('note', action='save', question='원인은?', answer='권한 설정 확인이 필요하다.')
        result = self.call('report', audience='both')
        self.assertNotIn('error', result)
        snapshot = json.loads((self.case.output / result['source_appendix']).read_text())
        self.assertFalse(snapshot['findings'])
        self.assertIn('근거 미연결', snapshot['gaps'][0])

    def test_timeline_timezone_and_dedup(self):
        item = {'time': '2026-09-30T10:00:00.123Z', 'description': '실행 시도', 'evidence_ids': [self.source]}
        result = timeline([item, item, {**item, 'time': 'Sep 30 10:00:00'}])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['display_time'], '2026-09-30 19:00:00 KST')
        self.assertEqual(result[0]['raw_time'], item['time'])
        self.assertIn('시간대 미확인', result[1]['display_time'])

    def test_image_search_reports_skips(self):
        (self.root / 'binary').write_bytes(b'\0data')
        result = search_node(Node(self.root), {'text': 'permission'})
        self.assertEqual(len(result['matches']), 1)
        self.assertEqual(result['matches'][0]['line'], 2)
        self.assertFalse(result['coverage_complete'])

    def test_single_volume_missing_selector_is_unambiguous(self):
        for offset in (0, 1048576):
            volume = SimpleNamespace(offset=offset)
            self.assertIs(select_volume([volume]), volume)
            self.assertIs(select_volume([volume], offset), volume)

    def test_explicit_wrong_selector_does_not_fall_back(self):
        with self.assertRaisesRegex(ValueError, 'discovered offsets: 1048576'):
            select_volume([SimpleNamespace(offset=1048576)], 0)

    def test_multiple_volumes_require_selection_and_allow_zero(self):
        volumes = [SimpleNamespace(offset=0), SimpleNamespace(offset=2048)]
        with self.assertRaisesRegex(ValueError, '0, 2048'):
            select_volume(volumes)
        self.assertIs(select_volume(volumes, 0), volumes[0])
        self.assertIs(select_volume(volumes, 2048), volumes[1])

    def test_empty_volume_discovery_is_not_auto_selected(self):
        with self.assertRaisesRegex(ValueError, 'No volumes'):
            select_volume([])

    def test_image_search_recursive_opt_in(self):
        (self.root / 'nested').mkdir()
        (self.root / 'nested' / 'file').write_text('needle')
        self.assertFalse(search_node(Node(self.root), {'text': 'needle'})['matches'])
        self.assertEqual(len(search_node(Node(self.root), {'text': 'needle', 'recursive': True})['matches']), 1)

    def test_source_request_is_preserved(self):
        result = self.case.event(self.source)['data']
        start = self.case.event(result['started_id'])
        self.assertEqual(start['data']['arguments']['path'], 'cron.log')

    def test_two_reports_same_snapshot_and_escape(self):
        snapshot = {'title': '예약 작업 조사', 'summary': '실행은 시도했으나 성공하지 못했습니다.',
                    'findings': [{'title': '권한 오류', 'detail': '<script>no execution</script> 권한 오류로 실패했습니다.', 'evidence_ids': [self.source]}],
                    'alternatives': ['승인된 작업일 가능성은 별도 확인이 필요합니다.'], 'gaps': ['승인 기록 미검토'], 'actions': ['승인 기록 확인']}
        result = self.call('report', snapshot=snapshot, audience='both')
        self.assertNotIn('error', result)
        self.assertEqual(len(result['reports']), 2)
        snapshot = json.loads((self.case.output / result['source_appendix']).read_text())
        self.assertEqual(snapshot['sources'][0]['request']['data']['arguments']['path'], 'cron.log')
        from docx import Document
        for report in result['reports']:
            html = (self.case.output / report['html']).read_text()
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)
            self.assertEqual(Document(self.case.output / report['docx']).core_properties.subject, 'snapshot:' + result['snapshot_id'])
        again = self.call('report', snapshot_id=result['snapshot_id'], audience='analyst')
        self.assertEqual(again['snapshot_id'], result['snapshot_id'])
        self.assertEqual(again['reports'][0]['html'], result['reports'][1]['html'])

    def test_report_cannot_invent_sources_or_snapshot_path(self):
        self.assertIn('error', self.call('report', snapshot={'summary': 'x', 'findings': [{'title': 'x', 'detail': 'y', 'evidence_ids': ['invented']}]}))
        self.assertIn('error', self.call('report', snapshot_id='../../case.json'))

    def test_report_preserves_linked_notes_gaps_and_alternatives(self):
        self.call('note', action='save', question='성공했나?', answer='미확인', evidence_ids=[self.source],
                  alternatives=['정상 운영 실패일 수 있다.'], gaps=['승인 미검토'], next_checks=['승인 확인'])
        other = self.call('hash', path='cron.log')['evidence_id']
        self.call('note', action='save', question='별도 질문', answer='미확인', evidence_ids=[other], gaps=['별도 범위'])
        result = self.call('report', snapshot={'summary':'실행 실패 기록', 'findings':[{'title':'실패','detail':'실패 기록','evidence_ids':[self.source]}], 'gaps':[]})
        body = json.loads((self.case.output / result['source_appendix']).read_text())
        self.assertEqual(body['gaps'], ['승인 미검토'])
        self.assertEqual(body['alternatives'], ['정상 운영 실패일 수 있다.'])
        self.assertEqual(body['actions'], ['승인 확인'])

    def test_partial_report_without_model_or_findings(self):
        result = self.call('report', audience='both')
        self.assertNotIn('error', result)
        self.assertEqual(result['analysis_status'], 'partial')
        self.assertIn('아직 검토한 질문', (self.case.output / result['reports'][0]['html']).read_text())

    def test_generated_report_listing_is_not_evidence_or_rewrite(self):
        self.call('report', audience='both')
        self.assertIn('error', self.call('list', path=str(self.case.output)))
        before = {p.name: p.read_bytes() for p in self.case.output.iterdir() if p.name.startswith(('report-', 'snapshot-'))}
        self.assertEqual(len(before), 5)
        listed = self.call('report', action='list')
        self.assertEqual({x['name'] for x in listed['files']}, set(before))
        self.assertTrue(all(x['bytes'] > 0 for x in listed['files']))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.case.output.iterdir() if p.name in before})
        self.assertIn('error', self.call('note', action='save', question='q', answer='a', evidence_ids=[listed['evidence_id']]))


if __name__ == '__main__': unittest.main()
