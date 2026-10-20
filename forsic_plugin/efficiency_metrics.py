"""Offline, read-only physical-call accounting from existing case receipts.

No model calls, pricing lookup, cumulative session counters or case writes. Latency
is summed *call duration*, which may overlap; it is never exclusive wall time.
Unknown usage stays unknown. Provider output tokens may include hidden reasoning;
visible summary characters are a separate observation, not a token estimate.
"""
import math
from pathlib import Path
import sqlite3


KINDS = ('pre_api_request', 'post_api_request', 'api_request_error',
         'pre_auxiliary_call', 'post_auxiliary_call')
GROUPS = ('main', 'compression', 'review', 'other_auxiliary')
TOKENS = ('input_tokens', 'fresh_input_tokens', 'cached_input_tokens',
          'cache_write_tokens', 'provider_output_tokens', 'reasoning_tokens')


def _number(value):
    return value if type(value) in (int, float) and 0 <= value <= 2**53-1 and math.isfinite(value) else None


def _integer(value):
    return value if type(value) is int and _number(value) is not None else None


def _usage(usage, api_mode='', provider=''):
    """Native canonical receipts, plus raw usage from Forsic display receipts."""
    if not isinstance(usage, dict) or not usage:
        return {}
    def first(*paths):
        for path in paths:
            item = usage
            for key in path.split('.'):
                item = item.get(key) if isinstance(item, dict) else None
            number = _integer(item)
            if number is not None:
                return number
        return None
    # CanonicalUsage input_tokens excludes both cache buckets. request_count is
    # persisted by asdict(); raw Responses input_tokens instead includes cache.
    canonical = 'request_count' in usage or 'cache_read_tokens' in usage
    if canonical:
        fresh = first('input_tokens')
        cached = first('cache_read_tokens')
        written = first('cache_write_tokens')
        total = fresh + cached + written if None not in (fresh, cached, written) else None
    else:
        cached = first('prompt_tokens_details.cached_tokens', 'input_tokens_details.cached_tokens',
                       'cache_read_input_tokens', 'prompt_cache_hit_tokens', 'cached_tokens')
        written = first('prompt_tokens_details.cache_write_tokens',
                        'input_tokens_details.cache_write_tokens', 'input_tokens_details.cache_creation_tokens',
                        'prompt_tokens_details.cache_creation_input_tokens',
                        'cache_creation_input_tokens', 'cache_write_tokens')
        raw_input = first('prompt_tokens', 'input_tokens')
        anthropic = api_mode == 'anthropic_messages' or provider == 'anthropic'
        # Absent cache details are unknown; do not manufacture a fresh/cache split.
        if anthropic:
            fresh = raw_input
            total = fresh + cached + written if None not in (fresh, cached, written) else None
        else:
            total = raw_input
            fresh = total-cached-written if None not in (total, cached, written) and total >= cached+written else None
    return dict(input_tokens=total, fresh_input_tokens=fresh, cached_input_tokens=cached,
                cache_write_tokens=written, provider_output_tokens=first('output_tokens', 'completion_tokens'),
                reasoning_tokens=first('reasoning_tokens', 'output_tokens_details.reasoning_tokens',
                                       'completion_tokens_details.reasoning_tokens'))


def aggregate_efficiency_events(events):
    """Aggregate ordered ``{kind, session, data}`` receipts; return numbers only.

    Identity is scoped by session, channel, native request id and attempt start
    (or call counter for old receipts). Repeated copies of the same hook do not
    count twice. Receipts without request identity cannot safely be paired and
    remain separate observations, exposed by identity_unavailable. ``inflight``
    means a pre receipt has no terminal receipt, not proof a process is still
    alive. ``completed`` means the API returned, not summary adoption/success.
    """
    calls = {}
    for index, event in enumerate(events):
        kind = event.get('kind')
        if kind not in KINDS:
            continue
        data = event.get('data') or {}
        channel = 'auxiliary' if 'auxiliary' in kind else 'main'
        request = data.get('api_request_id')
        request = request if isinstance(request, str) and request else None
        start = _number(data.get('started_at'))
        counter = _integer(data.get('api_call_count'))
        if counter is None:
            counter = _integer(data.get('retry_count'))
        attempt = ('start', start) if start is not None else ('counter', counter)
        key = (event.get('session'), channel, request, attempt) if request else ('unidentified', index)
        call = calls.setdefault(key, {'channel': channel, 'pre': None, 'post': None, 'error': None,
                                      'identified': bool(request)})
        phase = 'pre' if kind.startswith('pre_') else 'error' if kind == 'api_request_error' else 'post'
        call[phase] = data
    result = {}
    for group in GROUPS:
        result[group] = dict(calls=0, completed=0, errors=0, inflight=0, returned_unconfirmed=0,
            identity_unavailable=0, pre_unavailable=0, usage_available=0, usage_unavailable=0,
            call_seconds=0.0, duration_available=0, duration_unavailable=0,
            visible_output_chars=0, visible_output_chars_available=0,
            **{field: None for field in TOKENS}, token_calls={field: 0 for field in TOKENS},
            latency_by_status={status: dict(call_seconds=0.0, duration_available=0, duration_unavailable=0)
                for status in ('completed', 'errors', 'inflight', 'returned_unconfirmed')})
    for call in calls.values():
        pre = call['pre'] or {}
        terminal = call['post'] or call['error']
        observation = {**pre, **(terminal or {})}
        task = observation.get('aux_task')
        group = 'main' if call['channel'] == 'main' else (
            'compression' if task == 'compression' else 'review' if task == 'report_review' else 'other_auxiliary')
        target = result[group]
        target['calls'] += 1
        target['identity_unavailable'] += not call['identified']
        target['pre_unavailable'] += call['pre'] is None
        if terminal is None:
            status = 'inflight'
        elif call['error'] is not None or terminal.get('error'):
            status = 'errors'
        elif call['channel'] == 'auxiliary' and not terminal.get('finish_reason'):
            # Native streaming post means the stream was handed to its consumer,
            # not that generation completed. Historical hooks omit streaming.
            status = 'returned_unconfirmed'
        else:
            status = 'completed'
        target[status] += 1
        usage = _usage((terminal or {}).get('usage'), observation.get('api_mode'), observation.get('provider'))
        available = any(value is not None for value in usage.values())
        target['usage_available' if available else 'usage_unavailable'] += 1
        for field, value in usage.items():
            if value is not None:
                target[field] = (target[field] or 0) + value
                target['token_calls'][field] += 1
        duration = _number((terminal or {}).get('api_duration'))
        end = _number((terminal or {}).get('ended_at'))
        start = _number(observation.get('started_at'))
        if duration is None and end is not None and start is not None and end >= start:
            duration = end-start
        duration_field = 'duration_available' if duration is not None else 'duration_unavailable'
        target[duration_field] += 1
        target['latency_by_status'][status][duration_field] += 1
        if duration is not None:
            target['call_seconds'] += duration
            target['latency_by_status'][status]['call_seconds'] += duration
        chars = _integer((terminal or {}).get('assistant_content_chars'))
        # A stream handoff's forced zero is not an observation of its output.
        if chars is not None and (call['channel'] == 'main' or (terminal or {}).get('finish_reason')):
            target['visible_output_chars'] += chars
            target['visible_output_chars_available'] += 1
    return result


# Project only accounting fields, never raw prompts, responses, evidence or errors.
_SCALARS = ('api_request_id', 'aux_task', 'api_mode', 'provider', 'api_call_count',
            'retry_count', 'started_at', 'ended_at', 'api_duration', 'assistant_content_chars')
_USAGE_PATHS = ('request_count', 'input_tokens', 'output_tokens', 'cache_read_tokens',
    'cache_write_tokens', 'reasoning_tokens', 'prompt_tokens', 'completion_tokens',
    'cache_read_input_tokens', 'cache_creation_input_tokens', 'prompt_cache_hit_tokens', 'cached_tokens',
    'prompt_tokens_details.cached_tokens', 'prompt_tokens_details.cache_write_tokens',
    'prompt_tokens_details.cache_creation_input_tokens', 'input_tokens_details.cached_tokens',
    'input_tokens_details.cache_write_tokens', 'input_tokens_details.cache_creation_tokens',
    'output_tokens_details.reasoning_tokens', 'completion_tokens_details.reasoning_tokens')


def case_efficiency_metrics(database, session_ids=None):
    """Read a case events SQLite database with mode=ro and query_only.

    Pass all continuation session ids to measure a conversation. No automatic
    lineage lookup or native session-counter addition occurs. Missing DB/table
    errors propagate so unavailable accounting cannot look like measured zero.
    Token sums cover only token_calls[field] calls; compare against calls before
    interpreting them as a full total. No monetary cost is inferred.
    """
    columns = ['kind', 'session'] + ["json_extract(data,'$."+field+"')" for field in _SCALARS]
    columns += ["CASE WHEN json_extract(data,'$.error') IS NOT NULL AND json_extract(data,'$.error') != '' THEN 1 ELSE 0 END",
                "CASE WHEN json_extract(data,'$.finish_reason') IS NOT NULL AND json_extract(data,'$.finish_reason') != '' THEN 1 ELSE 0 END"]
    columns += ["json_extract(data,'$.usage."+field+"')" for field in _USAGE_PATHS]
    query = 'SELECT '+','.join(columns)+' FROM events WHERE kind IN ('+','.join('?' for _ in KINDS)+')'
    params = list(KINDS)
    if session_ids is not None:
        sessions = tuple(session_ids)
        query += ' AND session IN ('+','.join('?' for _ in sessions)+')'
        params.extend(sessions)
    query += ' ORDER BY rowid'
    db = sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        def receipts():
            for row in db.execute(query, params):
                data = dict(zip(_SCALARS, row[2:2+len(_SCALARS)]))
                offset = 2+len(_SCALARS)
                data.update(error=bool(row[offset]), finish_reason=bool(row[offset+1]))
                usage = {}
                for path, value in zip(_USAGE_PATHS, row[offset+2:]):
                    if value is not None:
                        parent = usage
                        parts = path.split('.')
                        for part in parts[:-1]:
                            parent = parent.setdefault(part, {})
                        parent[parts[-1]] = value
                data['usage'] = usage
                yield dict(kind=row[0], session=row[1], data=data)
        return aggregate_efficiency_events(receipts())
    finally:
        db.close()


def main():
    """Offline operator entry point; never invokes an engine or updates a case."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--session', action='append', dest='sessions',
                        help='Exact native session ID; repeat for known compression lineage.')
    args = parser.parse_args()
    print(json.dumps({
        'basis': 'retained_physical_call_receipts',
        'duration_basis': 'sum_of_call_durations_may_overlap_not_exclusive_wall_time',
        'coverage': 'token_calls counts available measurements; missing values are not zero',
        'calls': case_efficiency_metrics(args.database, args.sessions),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
