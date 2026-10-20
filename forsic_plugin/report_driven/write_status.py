"""Bounded report notices from paired native question-write receipts; no writes."""
from contextlib import closing
import hashlib
import json
import sqlite3


MAX_NOTICES = 20


def projection(case, state, *, cutoff=None):
    """Freeze save status separately from the unchanged judgment snapshot.

    Only compact identity fields leave SQLite. Rejected answers and error text
    never enter report content. Unknown questions and new drafts are excluded.
    """
    notices = []
    affected = 0
    fingerprint = hashlib.sha256()
    with closing(sqlite3.connect(case.db.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if cutoff is None:
            row = db.execute('SELECT rowid,id FROM events ORDER BY rowid DESC LIMIT 1').fetchone()
            cutoff = dict(rowid=row[0], event_id=row[1]) if row else dict(rowid=0, event_id=None)
        for question in sorted(state['questions'], key=lambda q: q['id']):
            if question['id'] == 'Q-intake':
                continue
            committed = db.execute("SELECT rowid,json_extract(data,'$.revision') FROM events "
                "WHERE kind='note' AND json_extract(data,'$.note_id')=? AND rowid<=? "
                "ORDER BY rowid DESC LIMIT 1",
                (question['id'].removeprefix('Q-'), cutoff['rowid'])).fetchone()
            if not committed or committed[1] != question['revision']:
                raise ValueError('Question revision changed before report save-status projection')
            # A note committed after this attempt began already supersedes it.
            # A delayed delivery error must not label that saved revision unsaved.
            failed = db.execute("SELECT r.id,r.rowid FROM events r JOIN events s "
                "ON s.id=json_extract(r.data,'$.started_id') "
                "WHERE r.kind='tool_result' AND s.kind='tool_start' "
                "AND json_extract(r.data,'$.tool')='forsic_reporting' "
                "AND json_extract(s.data,'$.tool')='forsic_reporting' "
                "AND json_extract(s.data,'$.arguments.action')='question' "
                "AND json_extract(s.data,'$.arguments.payload.question_id')=? "
                "AND COALESCE(json_extract(r.data,'$.error'),'')<>'' "
                "AND s.rowid>? AND r.rowid<=? AND s.rowid<r.rowid "
                "ORDER BY r.rowid DESC LIMIT 1",
                (question['id'], committed[0], cutoff['rowid'])).fetchone()
            if not failed:
                continue
            affected += 1
            identity = [question['id'], question['revision'], failed[0]]
            fingerprint.update(json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode())
            if len(notices) < MAX_NOTICES:
                notices.append(dict(question_id=question['id'], committed_revision=question['revision'],
                    failed_result_id=failed[0],
                    message=f"질문 {question['id']}의 수정 저장 시도가 실패하여 변경 내용은 저장되지 않았습니다. "
                            f"이 보고서는 현재 저장된 revision {question['revision']}의 답을 사용합니다."))
    return dict(cutoff=cutoff, status=dict(schema='forsic-report-write-status-1',
        judgment_snapshot_id=state['meta']['snapshot_id'], affected_question_count=affected,
        omitted_notice_count=max(0, affected - len(notices)), notices=notices,
        projection_sha256=fingerprint.hexdigest()))
