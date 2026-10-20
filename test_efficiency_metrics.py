"""Synthetic native receipt shapes; no model, network or actual case writes."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from forsic_plugin.efficiency_metrics import aggregate_efficiency_events, case_efficiency_metrics


def event(kind, request='r', session='s', **data):
    return dict(kind=kind, session=session, data=dict(api_request_id=request, **data))


def canonical(fresh=80, cached=20, output=12):
    return dict(input_tokens=fresh, output_tokens=output, cache_read_tokens=cached,
                cache_write_tokens=0, reasoning_tokens=9, request_count=1,
                prompt_tokens=fresh+cached, total_tokens=fresh+cached+output)


class EfficiencyMetricsTests(unittest.TestCase):
    def test_compression_is_independent_and_duplicates_do_not_add_usage(self):
        pre = event('pre_auxiliary_call', aux_task='compression', started_at=10, retry_count=0, api_call_count=1)
        post = event('post_auxiliary_call', aux_task='compression', started_at=10, ended_at=16,
                     api_duration=6, finish_reason='stop', usage=canonical(), assistant_content_chars=3)
        events = [pre, pre, post, post,
            event('post_api_request', usage=canonical(900, 100, 30), api_duration=8),
            event('post_auxiliary_call', request='review', aux_task='report_review',
                  finish_reason='stop', usage=canonical(40, 10, 10), api_duration=5)]
        result = aggregate_efficiency_events(events)
        compression = result['compression']
        self.assertEqual(compression['calls'], 1)
        self.assertEqual(compression['input_tokens'], 100)
        self.assertEqual(compression['fresh_input_tokens'], 80)
        self.assertEqual(compression['cached_input_tokens'], 20)
        self.assertEqual(compression['provider_output_tokens'], 12)
        self.assertEqual(compression['reasoning_tokens'], 9)
        self.assertEqual(compression['visible_output_chars'], 3)
        self.assertEqual(compression['call_seconds'], 6)
        self.assertEqual(result['main']['input_tokens'], 1000)
        self.assertEqual(result['review']['input_tokens'], 50)

    def test_error_retry_and_inflight_missing_usage_are_not_zero(self):
        events = [event('pre_auxiliary_call', aux_task='compression', started_at=10, retry_count=0),
            event('post_auxiliary_call', aux_task='compression', started_at=10, retry_count=0,
                  error='TimeoutError', api_duration=4, usage=None),
            event('pre_auxiliary_call', aux_task='compression', started_at=15, retry_count=1),
            event('post_auxiliary_call', aux_task='compression', started_at=15, retry_count=1,
                  finish_reason='length', api_duration=2, usage=canonical()),
            event('pre_auxiliary_call', request='pending', aux_task='compression', started_at=18),
            event('api_request_error', request='main-error', api_duration=20, error={'type': 'TimeoutError'})]
        result = aggregate_efficiency_events(events)
        comp = result['compression']
        self.assertEqual((comp['calls'], comp['completed'], comp['errors'], comp['inflight']), (3, 1, 1, 1))
        self.assertEqual((comp['usage_available'], comp['usage_unavailable']), (1, 2))
        self.assertEqual(comp['token_calls']['input_tokens'], 1)
        self.assertEqual(comp['call_seconds'], 6)
        self.assertEqual(comp['latency_by_status']['errors']['call_seconds'], 4)
        self.assertEqual(comp['latency_by_status']['completed']['call_seconds'], 2)
        self.assertEqual(comp['latency_by_status']['inflight']['duration_unavailable'], 1)
        self.assertEqual(comp['duration_unavailable'], 1)
        self.assertIsNone(result['main']['input_tokens'])
        self.assertEqual(result['main']['errors'], 1)

    def test_stream_handoff_cannot_claim_complete_or_visible_zero(self):
        result = aggregate_efficiency_events([event('post_auxiliary_call', aux_task='report_review',
            usage=None, finish_reason=None, assistant_content_chars=0, api_duration=0.01)])['review']
        self.assertEqual(result['returned_unconfirmed'], 1)
        self.assertEqual(result['completed'], 0)
        self.assertEqual(result['usage_unavailable'], 1)
        self.assertEqual(result['visible_output_chars_available'], 0)

    def test_identity_boundaries_include_sessions_channel_and_attempt(self):
        result = aggregate_efficiency_events([
            event('post_api_request', session='a', started_at=1, usage=canonical()),
            event('post_api_request', session='b', started_at=1, usage=canonical()),
            event('post_auxiliary_call', session='a', started_at=1, aux_task='compression', usage=canonical(), finish_reason='stop'),
            event('api_request_error', session='a', started_at=2, error='timeout'),
            event('pre_api_request', request='', started_at=3),
            event('post_api_request', request='', started_at=3)])
        self.assertEqual(result['main']['calls'], 5)
        self.assertEqual(result['main']['identity_unavailable'], 2)
        self.assertEqual(result['main']['inflight'], 1)
        self.assertEqual(result['compression']['calls'], 1)

    def test_raw_display_usage_does_not_invent_cache_split(self):
        result = aggregate_efficiency_events([event('post_auxiliary_call', aux_task='investigation_display',
            usage={'prompt_tokens': 100, 'completion_tokens': 7}, finish_reason='stop')])['other_auxiliary']
        self.assertEqual(result['input_tokens'], 100)
        self.assertIsNone(result['fresh_input_tokens'])
        self.assertIsNone(result['cached_input_tokens'])
        self.assertEqual(result['provider_output_tokens'], 7)
        result = aggregate_efficiency_events([event('post_auxiliary_call', aux_task='investigation_display',
            usage={'prompt_tokens': 100, 'completion_tokens': 7,
                   'prompt_tokens_details': {'cached_tokens': 30, 'cache_write_tokens': 0}}, finish_reason='stop')])['other_auxiliary']
        self.assertEqual(result['fresh_input_tokens'], 70)

    def test_invalid_values_and_empty_usage_remain_unavailable(self):
        result = aggregate_efficiency_events([event('post_api_request', usage={}, api_duration=float('nan')),
            event('post_api_request', request='x', usage={'input_tokens': True,
                'output_tokens': 2**2048, 'cache_read_tokens': '10'}, assistant_content_chars=True)])['main']
        self.assertEqual(result['usage_unavailable'], 2)
        self.assertEqual(result['duration_unavailable'], 2)
        self.assertIsNone(result['provider_output_tokens'])
        self.assertEqual(result['visible_output_chars_available'], 0)

    def test_sql_projection_matches_in_memory_and_never_changes_database(self):
        rows = [event('pre_auxiliary_call', aux_task='compression', started_at=10),
            event('post_auxiliary_call', aux_task='compression', started_at=10, ended_at=14,
                  usage=canonical(), finish_reason='stop', assistant_content_chars=3,
                  response={'content': 'PRIVATE_EVIDENCE'}),
            event('post_api_request', session='excluded', usage=canonical())]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'case.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE events (id TEXT, time REAL, kind TEXT, session TEXT, data TEXT)')
                for index, row in enumerate(rows):
                    db.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?)',
                        (str(index), index, row['kind'], row['session'], json.dumps(row['data'])))
            before = path.read_bytes()
            result = case_efficiency_metrics(path, ['s'])
            self.assertEqual(result, aggregate_efficiency_events(rows[:2]))
            self.assertNotIn('PRIVATE', json.dumps(result))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(case_efficiency_metrics(path, [])['compression']['calls'], 0)
            with self.assertRaises(sqlite3.OperationalError):
                case_efficiency_metrics(Path(temporary)/'missing.sqlite')
            self.assertFalse((Path(temporary)/'missing.sqlite').exists())

    def test_overlapping_calls_sum_durations_without_claiming_wall_time(self):
        result = aggregate_efficiency_events([
            event('post_auxiliary_call', request='a', aux_task='compression',
                  started_at=10, ended_at=20, finish_reason='stop', usage=canonical()),
            event('post_auxiliary_call', request='b', aux_task='compression',
                  started_at=15, ended_at=25, finish_reason='stop', usage=canonical())])['compression']
        self.assertEqual(result['call_seconds'], 20)
        self.assertNotIn('wall_seconds', result)

    def test_main_pre_post_without_post_retry_count_pair_by_start(self):
        result = aggregate_efficiency_events([
            event('pre_api_request', started_at=10, retry_count=1, api_call_count=2),
            event('post_api_request', started_at=10, api_call_count=2, usage=canonical(), api_duration=9)])['main']
        self.assertEqual(result['calls'], 1)
        self.assertEqual(result['completed'], 1)
        self.assertEqual(result['pre_unavailable'], 0)


if __name__ == '__main__':
    unittest.main()
