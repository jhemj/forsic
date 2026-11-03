"""Read-only report workspace: authored draft, immutable files and final selection.

This is a selected UI projection, never a write snapshot or another report ledger.
Old prose and unrelated tool returns do not decide whether a draft can be read.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3

MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_INPUT_RECORDS = 20000


def timestamp(value):
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return value if isinstance(value, str) else None


def selected_basis(case):
    """Replay current analytical state with a bounded metadata working set.

    Historical note definitions/alternatives remain for canonical migration and
    invalidation. Replaced answer/prose/forensic views are not replay inputs.
    Current notes, changes and latest prose remain verbatim. Each source is read
    separately under the same per-record cap and fingerprinted by the canonical
    projector, then its body is released. Evidence text is not UI state.
    """
    from .report_driven.host import current, source_record, encoded
    from .notes import EVIDENCE_TOOLS
    events, source_ids, mission_ids, source_records = {}, set(), set(), {}
    read_bytes = raw_bytes = 0

    def references(value):
        if isinstance(value, dict):
            if value.get('kind') == 'source' and isinstance(value.get('id'), str):
                source_ids.add(value['id'])
            for key, item in value.items():
                if key in ('evidence_ids', 'result_ids', 'execution_start_ids') and isinstance(item, list):
                    source_ids.update(x for x in item if isinstance(x, str))
                elif key in ('evidence_id', 'source_id', 'started_id') and isinstance(item, str):
                    source_ids.add(item)
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)

    with closing(sqlite3.connect(case.db.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')

        def read(where, params=(), projection='data'):
            nonlocal read_bytes, raw_bytes
            query = ('SELECT rowid,id,time,kind,session,length(cast(body AS BLOB)) AS bytes, '
                     'CASE WHEN length(cast(body AS BLOB))<=? THEN body END AS data FROM '
                     '(SELECT rowid,id,time,kind,session,' + projection + ' AS body FROM events WHERE ' + where + ') ORDER BY rowid')
            for row in db.execute(query, (MAX_INPUT_BYTES, *params)):
                if row['id'] in events:
                    continue
                if row['data'] is None or len(events) >= MAX_INPUT_RECORDS:
                    raise ValueError('선택된 보고서 근거의 조회 범위 초과. 저장된 초안은 표시하며 근거 일치 판정은 보류합니다.')
                event = {key: row[key] for key in ('rowid', 'id', 'time', 'kind', 'session')}
                event['data'] = json.loads(row['data'])
                raw_bytes += row['bytes'] or 0
                references(event['data'])
                if event['kind'] == 'tool_result' and event['data'].get('tool') in EVIDENCE_TOOLS:
                    # Keep exact full-body identity/coverage, not a summary hash.
                    # Native execution projection only needs these receipt fields.
                    source_records[event['id']] = source_record(event)
                    event['data'] = {k: event['data'][k] for k in (
                        'tool', 'source_kind', 'started_id', 'job_id', 'mission_context',
                        'status', 'state', 'error', 'exit_code', 'verification_complete') if k in event['data']}
                    event['data']['_report_source_version'] = source_records[event['id']]['version']
                    read_bytes += len(encoded(source_records[event['id']]))
                read_bytes += len(encoded(event['data']))
                if read_bytes > MAX_INPUT_BYTES:
                    raise ValueError('선택된 보고서 상태의 조회 범위 초과. 저장된 초안은 표시하며 근거 일치 판정은 보류합니다.')
                events[event['id']] = event
                if event['kind'] == 'report_change':
                    mission_ids.update(m['id'] for m in event['data'].get('records', {}).get('missions', []))

        # Retain all note identities and definition history. Only the last note
        # per ID contributes its answer/forensic view to host.current().
        old_note = "json_remove(data,'$.answer','$.investigation','$.forensic','$.report_slots','$.reader_explanation','$.timeline','$.next_checks','$.correction_reason')"
        read("kind='note'", projection="CASE WHEN rowid IN (SELECT max(rowid) FROM events WHERE kind='note' GROUP BY json_extract(data,'$.note_id')) THEN data ELSE " + old_note + ' END')
        read("kind='report_change'")
        read("rowid IN (SELECT max(rowid) FROM events WHERE kind='indicator' GROUP BY json_extract(data,'$.indicator_id'))")
        read("rowid IN (SELECT max(rowid) FROM events WHERE kind='report_narrative' GROUP BY json_extract(data,'$.question_ids'))")
        read("kind IN ('tool_start','tool_result') AND json_valid(data) AND json_extract(data,'$.mission_context.mission_id') IN (SELECT value FROM json_each(?))", (json.dumps(sorted(mission_ids)),))
        # Explicit starts and any referenced result's paired start are transitive
        # dependencies. Fetch each record once; unrelated results stay in SQL.
        requested = set()
        while source_ids - requested:
            selected = source_ids - requested
            requested.update(selected)
            read("kind IN ('tool_start','tool_result') AND id IN (SELECT value FROM json_each(?))", (json.dumps(sorted(selected)),))
        read("rowid=(SELECT min(rowid) FROM events WHERE kind='tool_result' AND json_valid(data) AND json_extract(data,'$.source_kind')='external_intelligence' AND json_extract(data,'$.tool') IN (SELECT value FROM json_each(?)))", (json.dumps(sorted(EVIDENCE_TOOLS)),))
        read("rowid=(SELECT min(rowid) FROM events WHERE kind='goal_started')", projection="'{}'")
    # Nested citation revalidation must not run under this read transaction.
    state = current(case, events=sorted(events.values(), key=lambda e: e['rowid']),
                    _source_records=source_records)
    state['_report_selection_only'] = True
    state['_report_read'] = {'records': len(events), 'bytes': read_bytes, 'max_bytes': MAX_INPUT_BYTES,
                             'raw_bytes': raw_bytes, 'max_record_bytes': MAX_INPUT_BYTES}
    return state


def clean_basis(state):
    if state is None:
        return None
    return {key: value for key, value in state.items() if not key.startswith('_report_')}


def latest_draft(case):
    """Draft readability is independent of evidence projection availability."""
    with closing(sqlite3.connect(case.db.as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute("SELECT length(cast(data AS BLOB)),CASE WHEN length(cast(data AS BLOB))<=? THEN data END FROM events WHERE kind='report_narrative' ORDER BY rowid DESC LIMIT 1", (MAX_INPUT_BYTES,)).fetchone()
        if row is None:
            return None
        if row[1] is None:
            raise ValueError('저장된 초안 자체의 조회 범위 초과')
        return json.loads(row[1])


def authoring(case, state):
    from .report_driven.narrative import progress_view
    from .report_driven.template_contract import GROUPS
    issue = None
    draft = max((state or {}).get('report_narratives', []),
                key=lambda r: (r.get('saved_at', 0), r.get('revision', 0)), default=None)
    if state is None:
        try:
            draft = latest_draft(case)
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            issue = str(exc)
    if state is not None:
        try:
            result = progress_view(clean_basis(state))
        except (ValueError, KeyError, TypeError) as exc:
            issue = str(exc)
            result = progress_view(None)
    else:
        result = progress_view(None)
        issue = issue or '현재 근거와의 일치 여부를 조회하지 못했습니다. 저장된 초안과 생성 파일은 별도로 확인할 수 있습니다.'
    if draft:
        result.update(saved_at=timestamp(draft.get('saved_at')), revision=draft.get('revision'),
                      draft_revision=draft.get('draft_revision'), question_ids=draft.get('question_ids', []))
        if result['status'] == 'unavailable':
            # Preserve actual saved prose while keeping currentness unknown.
            groups = []
            for key, label, *_ in GROUPS:
                field = draft.get('fields', {}).get(key)
                entries = (field or {}).get('entries', [])
                previews = []
                for entry in entries[:5]:
                    text = ' · '.join(entry['cells'])
                    previews.append({'node_id': entry['node_id'], 'text': text[:300],
                        'omitted_characters': max(0, len(text)-300), 'status': entry['status']})
                groups.append({'id': key, 'label': label,
                    'state': 'saved' if field and field.get('complete', True) else 'in_progress' if field else 'missing',
                    'saved_at': (field or {}).get('saved_at'), 'preview': previews,
                    'omitted_entries': max(0, len(entries)-5)})
            result['groups'] = groups
    if issue:
        result['read_issue'] = issue[:280]
    result['content_complete'] = bool(result['status'] in ('ready', 'review'))
    return result, draft


def final_selections(case, case_id):
    """Small receipt metadata only; final selection is not blanket verification."""
    with closing(sqlite3.connect(case.db.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        # Select scalar fields in SQL, not the potentially large review packet.
        rows = db.execute("""SELECT id,time,json_extract(data,'$.bundle_id') AS bundle_id,
            json_extract(data,'$.snapshot_id') AS snapshot_id,
            json_extract(data,'$.independent_review.final_review_current') AS reviewed
            FROM events WHERE kind='report_finalized' AND json_valid(data)
            AND json_extract(data,'$.case_id')=? AND json_extract(data,'$.selection')='final_delivery_copy'
            ORDER BY rowid DESC LIMIT 100""", (case_id,))
        return [dict(row) for row in rows]


def describe_report(item, manifest, saved, draft, selections):
    item['lifecycle'] = 'saved'
    item['draft_relation'] = 'unknown'
    item['verification'] = {'semantic': manifest.get('status', {}).get('semantic_gate', 'not_reviewed'),
                            'layout': (manifest.get('layout_review') or {}).get('status', 'unverified')}
    if saved:
        rows = saved.get('report_narratives', [])
        ids = sorted(manifest.get('question_ids', []))
        row = max((r for r in rows if r.get('question_ids') == ids),
                  key=lambda r: (r.get('saved_at', 0), r.get('revision', 0)), default=None)
        if row:
            item.update(draft_revision=row.get('draft_revision'), narrative_revision=row.get('revision'),
                        draft_saved_at=timestamp(row.get('saved_at')))
            if draft:
                same_scope = row['question_ids'] == draft.get('question_ids')
                item['draft_relation'] = ('different_scope' if not same_scope else
                    'same' if row.get('draft_hash') and row.get('draft_hash') == draft.get('draft_hash') else
                    'older' if row.get('draft_revision', 0) < draft.get('draft_revision', 0) else 'unknown')
    receipt = next((r for r in selections if r['bundle_id'] == item['id'] and r['snapshot_id'] == item.get('snapshot_id')), None)
    if receipt and saved:
        item['lifecycle'] = 'final' if receipt == selections[0] else 'previous_final'
        item['finalization'] = {'receipt_id': receipt['id'], 'selected_at': timestamp(receipt['time']),
            'independent_review_current': bool(receipt['reviewed']), 'layout_status': item['verification']['layout']}


def workspace(progress, reports):
    latest = next(iter(sorted(reports, key=lambda r: r.get('created_at', ''), reverse=True)), None)
    final = next((r for r in reports if r.get('lifecycle') == 'final'), None)
    draft = None
    if progress.get('revision'):
        draft = {key: progress.get(key) for key in ('revision','draft_revision','saved_at','current','review_status','content_complete','question_ids')}
    from .report_driven.template_contract import OPTIONAL_GROUPS
    groups = [g for g in progress['groups'] if g['id'] not in OPTIONAL_GROUPS]
    completed = sum(g['state'] in ('saved','ready','unavailable') and bool(g.get('saved_at')) for g in groups)
    if final and final.get('current') is True and final.get('draft_relation') == 'same':
        phase, label, step = 'final_selected', '최종본 선택 완료', '선택된 최종본의 검증 범위와 파일 확인'
    elif progress['status'] == 'unavailable':
        phase, label, step = 'unavailable', '현재 상태 일부 확인 필요', '저장된 초안 확인 · 근거 일치 여부 조회 필요'
    elif progress['status'] == 'recheck':
        phase, label, step = 'needs_revision', '변경 내용 확인 중', '달라진 판단에 맞춰 영향받은 항목 확인'
    elif progress['status'] == 'drafting':
        phase, label, step = 'drafting', '초안 작성 중', '남은 항목 작성 후 본문·근거 검토'
    elif progress['status'] == 'review':
        phase, label, step = 'review', '본문 검토 단계', '현재 초안의 본문·근거 대조'
    elif latest and latest.get('current') is True and latest.get('draft_relation') == 'same':
        phase, label, step = 'ready_to_finalize', '생성 파일의 최종 검토 단계', '현재판 독립 검토와 의견 처분 후 최종본 선택'
    else:
        phase, label, step = 'ready_to_generate', '초안 검토 완료 · 파일 반영 대기', '현재 초안으로 보고서 파일 생성'
    return {'phase': phase, 'stage_label': label, 'next_step': step, 'draft': draft,
            'saved_report_id': (latest or {}).get('id'), 'final_report_id': (final or {}).get('id'),
            'completed_groups': completed, 'total_groups': len(groups)}


# Immutable saved files are checked once per exact filesystem identity. Keep only
# comparison metadata, not 24 full snapshots or original evidence bodies.
from functools import lru_cache


@lru_cache(maxsize=24)
def _snapshot_summary(path, file_identity, expected_hash, case_id, snapshot_id, question_ids):
    import hashlib
    from .report_driven.views import selected_report_identity, report_material
    raw = path.read_bytes()
    if len(raw) > MAX_INPUT_BYTES or hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('Unverified report snapshot')
    saved = json.loads(raw)
    if saved['meta']['case_id'] != case_id or saved['meta']['snapshot_id'] != snapshot_id:
        raise ValueError('Foreign report snapshot')
    identity = selected_report_identity(saved, list(question_ids)) if question_ids is not None else report_material(saved)
    keys = {'questions': ('id','question','answer','scope','assessment','claim_refs','evidence_ids','deferred_reason','remaining_gap_ids','reader_explanation'),
            'claims': ('id','statement','assertion_kind','inference_strength','reasoning_summary','source_refs','counterevidence_refs','limitations','assumptions'),
            'sources': ('id','version'),
            'report_narratives': ('question_ids','revision','draft_revision','draft_hash','saved_at')}
    summary = {field: [{key: row[key] for key in fields if key in row} for row in saved.get(field, [])]
               for field, fields in keys.items()}
    summary['meta'] = {key: saved['meta'][key] for key in ('case_id','snapshot_id')}
    return summary, identity


def saved_summary(case, manifest):
    path = case.output / ('report-state-' + manifest['snapshot_id'] + '.json')
    if path.is_symlink():
        raise ValueError('Invalid report snapshot path')
    stat = path.stat()
    if stat.st_size > MAX_INPUT_BYTES:
        raise ValueError('Unbounded report snapshot')
    ids = tuple(sorted(manifest['question_ids'])) if manifest.get('selection_mode') == 'selected_questions' else None
    result = _snapshot_summary(path, (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns),
        manifest.get('state_sha256'), manifest['case_id'], manifest['snapshot_id'], ids)
    if ids is not None and result[1] != manifest.get('selected_content_revision'):
        raise ValueError('Selected report identity disagrees with its immutable snapshot')
    return result
