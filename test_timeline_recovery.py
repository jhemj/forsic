"""Offline regressions for exact listing timestamps and native local-model patience."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_report_driven import fixture
from forsic_plugin.notes import timeline, note
from forsic_plugin.report_driven.host import current
import tempfile


class TimelineRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.case, _, self.original = fixture(self.tmp.name)
        self.entries = [
            {'path': '/var/log/other', 'mtime_epoch': 0},
            {'path': '/var/log/test.log', 'mtime_epoch': 1770856431.0163112},
        ]
        self.eid = self.case.record('tool_result', {
            'tool': 'forsic_image_files', 'file_path': '/var/log',
            'entries': self.entries, 'complete': False, 'next_offset': 100})
        self.item = {'description': '파일 수정 시각 (행위 발생시각 아님)', 'evidence_ids': [self.eid],
                     'time_source': {'evidence_id': self.eid, 'field': 'mtime', 'entry_path': '/var/log/test.log'}}

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, item):
        return note(self.case, {'action': 'save', 'question': '언제 수정됐나?', 'answer': '파일 메타데이터만 확인',
                              'evidence_ids': [self.eid], 'timeline': [item], 'critical_gaps': ['수정 주체 미확인']})

    def test_exact_entry_is_used_not_first_row_or_description(self):
        item = {**self.item, 'description': 'description misleadingly mentions /var/log/other'}
        row = timeline([item], self.case)[0]
        self.assertEqual(row['raw_time'], self.entries[1]['mtime_epoch'])
        self.assertEqual(row['display_time'], '2026-02-12 09:33:51 KST')

    def test_missing_path_explains_repair_without_silent_note_loss(self):
        item = deepcopy(self.item); del item['time_source']['entry_path']
        with self.assertRaisesRegex(ValueError, 'entry_path.*stat'):
            self.save(item)
        self.assertEqual(len(current(self.case)['questions']), 1)

    def test_wrong_ambiguous_and_unavailable_entry_rejected(self):
        for path in ('/var/log/missing', '/var/log/TEST.log', 'test.log'):
            item = deepcopy(self.item); item['time_source']['entry_path'] = path
            with self.assertRaisesRegex(ValueError, 'exactly one entry'):
                timeline([item], self.case)
        duplicate = self.case.record('tool_result', {'tool': 'forsic_image_files', 'entries': self.entries * 2})
        item = deepcopy(self.item); item['evidence_ids'] = [duplicate]; item['time_source']['evidence_id'] = duplicate
        with self.assertRaisesRegex(ValueError, 'exactly one entry'):
            timeline([item], self.case)

    def test_unavailable_field_requires_stat_not_mtime_substitution(self):
        item = deepcopy(self.item); item['time_source']['field'] = 'ctime'
        with self.assertRaisesRegex(ValueError, 'no ctime.*stat'):
            timeline([item], self.case)

    def test_new_and_legacy_reports_share_exact_pointer(self):
        self.save(self.item)
        state = current(self.case)
        obs = state['observations'][0]
        self.assertEqual(obs['field_pointer'], '/entries/1/mtime_epoch')
        self.assertEqual(obs['literal'], str(self.entries[1]['mtime_epoch']))
        self.assertEqual(state['timeline'][0]['normalized_values'], ['2026-02-12T09:33:51.016311+09:00'])
        result = json.loads(self.case.invoke('forsic_report', {'snapshot': {
            'summary': '메타데이터 확인', 'timeline': [self.item]}}))
        self.assertNotIn('error', result)
        for report in result['reports']:
            self.assertIn('2026-02-12 09:33:51 KST', (self.case.output / report['html']).read_text())

    def test_historical_bad_binding_stays_visible_as_gap_not_crash(self):
        saved = self.save(self.item)['note']
        invalid = deepcopy(saved); invalid['revision'] = 2
        invalid['timeline'][0]['time_source'].pop('entry_path')
        self.case.record('note', invalid)
        state = current(self.case)
        self.assertEqual(state['timeline'], [])
        self.assertIn('정확한 파일·시각 필드 연결 필요', str(state['status']['explicit_limitations']))

    def test_wrong_stat_path_and_nonfinite_epoch_rejected(self):
        for value in (True, float('nan'), float('inf'), 1e100):
            eid = self.case.record('tool_result', {'tool': 'forsic_image_files', 'times': {'mtime': value}})
            item = {'description': 'bad', 'evidence_ids': [eid], 'time_source': {'evidence_id': eid, 'field': 'mtime'}}
            with self.assertRaises(ValueError):
                timeline([item], self.case)
        eid = self.case.record('tool_result', {'tool': 'forsic_image_files', 'file_path': '/one', 'times': {'mtime': 0}})
        item = {'description': 'bad', 'evidence_ids': [eid], 'time_source': {'evidence_id': eid, 'field': 'mtime', 'entry_path': '/two'}}
        with self.assertRaisesRegex(ValueError, 'does not match'):
            timeline([item], self.case)


class LocalPatienceTests(unittest.TestCase):
    def test_native_timeouts_match_local_prefill_budget(self):
        from ruamel.yaml import YAML
        from hermes_cli.timeouts import get_provider_request_timeout, get_provider_stale_timeout
        from agent.chat_completion_helpers import _StreamingCall, _local_stream_stale_timeout_default
        config_path = Path(__file__).parent / 'config/config.yaml'
        if not config_path.exists():
            config_path = config_path.with_name('config.example.yaml')
        config = YAML(typ='safe').load(config_path.read_text())
        with patch('hermes_cli.config.load_config_readonly', return_value=config):
            self.assertEqual(get_provider_request_timeout('custom', 'qwen3.8:latest'), 900)
            self.assertEqual(get_provider_stale_timeout('custom', 'qwen3.8:latest'), 900)
            self.assertEqual(_local_stream_stale_timeout_default(), 900)
            monitor = object.__new__(_StreamingCall)
            monitor.agent = SimpleNamespace(provider='custom', model='qwen3.8:latest',
                                            base_url=config['model']['base_url'])
            monitor.api_kwargs = {'model': 'qwen3.8:latest', 'messages': []}
            monitor._resolve_stale_timeout()
            self.assertEqual(monitor._stream_stale_timeout, 900)
        self.assertEqual(config['agent']['run_budget_seconds'], 3600)
        self.assertEqual(config['agent']['max_turns'], 20)
        self.assertEqual(config['agent']['api_max_retries'], 1)
        self.assertEqual(config['fallback_model'], {})


if __name__ == '__main__':
    unittest.main()
