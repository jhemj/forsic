"""Presentation-only labels and exact retained timestamp excerpts; no evidence writes."""
import hashlib
import json
import re
from datetime import datetime, timezone

from .report_driven.host import content_version
from .report_driven.model_pages import child

MAX_SCAN_BYTES = 96 * 1024
MAX_FIELD_BYTES = 32 * 1024
MAX_CANDIDATES = 256
_BODY_FIELDS = ('stdout', 'stderr', 'content', 'text', 'lines', 'matches', 'entries',
                'results', 'metadata', 'stat', 'timestamps')
_SKIP_FIELDS = {'command', 'arguments', 'reason', 'purpose', 'started_at', 'finished_at',
                'collected_at', 'lookup_at', 'path', 'file_path', 'source_url', 'input'}
_DATE = re.compile(r'(?<![\w\d])(?P<date>\d{4}-\d{2}-\d{2})'
    r'(?:[T ](?P<clock>\d{2}:\d{2}:\d{2})(?P<fraction>\.\d{1,9})?'
    r'(?: ?(?P<zone>Z|[+-]\d{2}:?\d{2}))?)?(?!\d)')
_FILE_FIELDS = {'mtime': 'mtime', 'ctime': 'ctime', 'atime': 'atime', 'btime': 'btime',
                'birthtime': 'btime', 'modify': 'mtime', 'change': 'ctime',
                'access': 'atime', 'birth': 'btime'}
_EPOCH_FIELDS = {field+'_epoch':field for field in ('mtime','ctime','atime','btime')}


def _short(value, limit):
    return ' '.join(str(value or '').split())[:limit]


def _fields(data):
    """Only returned body/metadata scalars; command, acquisition and ledger clocks excluded."""
    remaining = MAX_SCAN_BYTES
    stack = [(child('', key), data[key], key) for key in reversed(_BODY_FIELDS) if key in data]
    # Some native tools return filesystem fields at the result root.
    stack += [(child('', key), value, key) for key, value in data.items()
              if key in _FILE_FIELDS or key in _EPOCH_FIELDS]
    visited = 0
    while stack and remaining > 0 and visited < 4096:
        pointer, value, name = stack.pop(); visited += 1
        if isinstance(value, str):
            raw = value.encode()[:min(MAX_FIELD_BYTES, remaining)]
            text = raw.decode('utf-8', errors='ignore'); remaining -= len(raw)
            yield pointer, text, name, len(raw) < len(value.encode()) or bool(stack and remaining <= 0)
        elif type(value) in (int,float) and name in _EPOCH_FIELDS:
            remaining -= len(str(value).encode())
            yield pointer, value, name, bool(stack and remaining <= 0)
        elif isinstance(value, dict):
            stack.extend((child(pointer, key), item, key) for key, item in reversed(list(value.items()))
                         if key not in _SKIP_FIELDS)
        elif isinstance(value, list):
            stack.extend((child(pointer, index), item, name) for index, item in reversed(list(enumerate(value))))


def source_display(source, data, input_metadata=None):
    """Deterministic fallback from this exact selected tool return and its matching input."""
    metadata = input_metadata or {}
    purpose = _short(metadata.get('reason') or data.get('purpose'), 160)
    target = _short(data.get('file_path') or data.get('path') or metadata.get('path'), 100)
    command = _short((metadata.get('arguments') or {}).get('command'), 100)
    preview = next((_short(text, 180) for _, text, _, _ in _fields(data)
                    if isinstance(text,str) and text.strip()), '')
    tool = data.get('tool', '')
    names = {'forsic_shell': '명령 출력', 'forsic_read': '파일 내용', 'forsic_search': '문자열 검색',
             'forsic_list': '파일 목록', 'forsic_image_read': '이미지 내 파일 내용',
             'forsic_image_files': '이미지 내 파일 조사', 'forsic_hash': '파일 해시'}
    title = purpose or ((names.get(tool, '보존 자료') + (' · ' + target if target else '')))
    summary = preview or command or target or '선택된 도구 반환입니다. 원문에서 검사 범위를 확인하세요.'
    if tool == 'forsic_shell':
        summary = '파생 명령 출력 · ' + summary
    return dict(display_title=title[:160], display_summary=summary[:240],
                display_origin='deterministic_retained')


def observation_display(observation, source=None):
    literal = _short(observation.get('literal'), 180)
    return dict(display_title=literal[:100] or _short(observation.get('statement'), 100),
                display_summary=literal or _short(observation.get('statement'), 240),
                display_origin='deterministic_retained')


def timestamp_projection(source, data, *, max_rows=6):
    """Observed literal clocks only. Unknown zones stay on a source-local calendar."""
    candidates = []
    scanned_bytes = 0
    truncated = False
    for pointer, text, name, cut in _fields(data):
        if type(text) in (int,float):
            scanned_bytes += len(str(text).encode()); truncated |= cut
            try:
                normalized = datetime.fromtimestamp(text,timezone.utc).isoformat().replace('+00:00','Z')
            except (ValueError,OverflowError,OSError):
                continue
            candidates.append(dict(pointer=pointer,literal=str(text),value=text,local=None,
                normalized=normalized,field=_EPOCH_FIELDS[name],precision='second',domain='utc',
                year_basis='explicit Unix epoch seconds field',
                timezone_basis='Unix epoch seconds (UTC), explicit '+name+' field',
                context=name+' = '+str(text)))
            if len(candidates) >= MAX_CANDIDATES:
                truncated = True; break
            continue
        scanned_bytes += len(text.encode()); truncated |= cut
        for match in _DATE.finditer(text):
            if len(candidates) >= MAX_CANDIDATES:
                truncated = True; break
            raw = match.group(); clock = match['clock']; zone = match['zone']
            local = match['date'] + ('T' + clock + (match['fraction'] or '') if clock else '')
            try:
                # Validate dates and zones without supplying any missing timezone/year.
                parsed = datetime.fromisoformat(local + ((zone if zone != 'Z' else '+00:00') if zone else ''))
            except ValueError:
                continue
            line_start = text.rfind('\n', 0, match.start()) + 1
            prefix = text[line_start:match.start()].strip()
            field = _FILE_FIELDS.get(name.lower())
            label = re.fullmatch(r'(?i)(?:st_)?(mtime|ctime|atime|btime|birthtime|modify|change|access|birth)\s*[:=]\s*', prefix)
            if label:
                field = _FILE_FIELDS[label[1].lower()]
            start = len(text[:match.start()].encode())
            candidates.append(dict(pointer=pointer, literal=raw, byte_start=start,
                byte_end=start+len(raw.encode()), local=local,
                context=_short(text[line_start:text.find('\n',match.end()) if '\n' in text[match.end():] else len(text)],120),
                normalized=parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if zone else None,
                field=field, precision='second' if clock else 'day',
                domain='utc' if zone else 'source-local:' + source['id']))
        if len(candidates) >= MAX_CANDIDATES:
            break
    # Return extrema for each clock/file field, bounded across the selected excerpt.
    groups = {}
    for item in candidates:
        groups.setdefault((item['domain'],item['field'],item['precision']), []).append(item)
    selected = []
    for key in sorted(groups, key=str):
        rows = sorted(groups[key],key=lambda item:item['normalized'] or item['local'])
        first, last = rows[0], rows[-1]
        selected.append((first, 'earliest_latest' if first is last else 'earliest'))
        if first is not last:
            selected.append((last, 'latest'))
    observations, timeline = [], []
    for item, boundary in selected[:max(0,max_rows)]:
        source_ref = dict(kind='source', id=source['id'], version=source['version'])
        identity = json.dumps([source_ref,item['pointer'],item.get('byte_start'),item['literal']],sort_keys=True)
        ident = hashlib.sha256(identity.encode()).hexdigest()[:24]
        field = item['field']; local_clock = item['domain'] != 'utc'
        limitation = ('반환된 발췌의 제한된 스캔에서 관측한 시각 문자열입니다. 사건 발생·최초 침해 시각이 아닙니다. '
                      '원문 전체의 최솟값·최댓값을 보장하지 않습니다.')
        if field:
            limitation += ' ' + field + '는 파일 메타데이터이며 사건 행위 시각·생성 시각을 확정하지 않습니다.'
        if data.get('tool') == 'forsic_shell':
            limitation += ' 파생 명령 출력이며 원본 직접 관측이나 입력 범위의 보장이 아닙니다.'
        zone_basis = item.get('timezone_basis') or ('unknown; retained literal has no timezone'
            if local_clock else 'explicit retained literal offset')
        year_basis = item.get('year_basis','explicit retained literal')
        clock_label = source.get('display_title') or source.get('label') or '보존 자료 시계'
        if clock_label.startswith('forsic_'):
            clock_label = '보존 자료 시계'
        ob = dict(id='O-display-time-'+ident, source_ref=source_ref,
            statement='보존된 반환 발췌에서 관측한 시각 문자열', field_pointer=item['pointer'],
            literal=item['literal'],
            coordinate_basis='UTF-8 bytes of the retained scalar field',
            canonical_sha256=hashlib.sha256(item['literal'].encode()).hexdigest(),
            presented_view='exact_excerpt', interpretation_limit=limitation)
        if 'value' in item:
            ob.update(value=item['value'],presented_view='metadata',
                      coordinate_basis='retained numeric scalar field',
                      statement='보존된 숫자 필드에서 관측한 파일 시각 값')
        else:
            ob.update(byte_start=item['byte_start'],byte_end=item['byte_end'])
        ob['version'] = content_version(ob)
        ob.update(observation_display(ob,source))
        observations.append(ob)
        row = dict(id='TM-display-time-'+ident, version=ob['version'],
            observation_ref=dict(kind='observation',id=ob['id'],version=ob['version']),
            source_ref=source_ref, time_kind='file_metadata' if field else 'record',
            time_role='file_metadata' if field else 'source_record_timestamp',
            observed_or_inferred='observed', inferred=False, file_time_type=field,
            shape='point', raw_values=[item['literal']],
            normalized_values=[item['normalized']] if item['normalized'] else [],
            local_values=[item['local']] if local_clock else [], comparable=not local_clock,
            clock_domain=item['domain'], timezone_basis=zone_basis,
            year_basis=year_basis, precision=item['precision'],
            excerpt_boundary=boundary, projection_origin='retained_timestamp_scan',
            explanation=('파일 '+field+' · ' if field else '관측 기록 · ') +
                ('발췌 내 최초·최종' if boundary=='earliest_latest' else '발췌 내 최초' if boundary=='earliest' else '발췌 내 최종') +
                ' · ' + item['context'],
            limitation=limitation,
            temporal=dict(observed_or_inferred='observed', timestamp_role='source_timestamp',
                clock_domain=item['domain'],timezone_basis=zone_basis,year_basis=year_basis),
            temporal_context=dict(coordinate_basis='source_local_calendar' if local_clock else 'utc_instant',
                precision=item['precision'],
                clock_label=clock_label,
                source_basis_current=True, scan_bytes=scanned_bytes, scan_truncated=truncated,
                total_matches_scanned=len(candidates), boundary_scope='bounded returned excerpt only'))
        if source.get('session_id'):
            ob['session_id'] = source['session_id']; row['session_id'] = source['session_id']
        timeline.append(row)
    return observations, timeline, dict(scanned_bytes=scanned_bytes, scan_truncated=truncated,
        matched_timestamps=len(candidates), omitted_boundaries=max(0,len(selected)-len(timeline)))
