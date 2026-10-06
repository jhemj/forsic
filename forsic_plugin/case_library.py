"""On-demand local case snapshots. Not shared prompt memory or a new agent loop."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from .notes import current_notes, sources


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class CaseLibrary:
    def __init__(self, case):
        self.case = case
        self.path = Path(case.config.get('library_path') or
                         Path(__file__).resolve().parents[1] / 'state/library/cases.sqlite3')
        self.archive_id = hashlib.sha256((str(case.manifest) + '\n' +
                                          str(case.config.get('case_id', ''))).encode()).hexdigest()[:32]

    def connect(self, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=15)
            db.execute('CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, revision TEXT, synthetic INTEGER, search_text TEXT, source_db TEXT, saved_at REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS versions (id TEXT, revision TEXT, body TEXT, saved_at REAL, PRIMARY KEY(id, revision))')
        else:
            db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
            db.execute('PRAGMA query_only=ON')
        db.row_factory = sqlite3.Row
        return db

    def save(self, args):
        notes = sorted(current_notes(self.case), key=lambda n: n['note_id'])
        if not notes:
            raise ValueError('조사 노트를 먼저 저장해 주세요. 빈 사건은 사례로 저장하지 않습니다.')
        state = args.get('analysis_status', 'partial')
        if state not in ('partial', 'requested_scope_answered'):
            raise ValueError('analysis_status must be partial or requested_scope_answered')
        tags = args.get('tags', [])
        if not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(t, str) or len(t) > 100 for t in tags):
            raise ValueError('Use at most 20 short text tags')
        ids = [eid for n in notes for eid in n.get('evidence_ids', [])]
        ids += [eid for n in notes for t in n.get('timeline', []) for eid in t.get('evidence_ids', [])]
        records = sources(self.case, ids)
        for record in records:
            record['request'] = self.case.event(record['data'].get('started_id', ''))
        body = {'case': self.case.info({}), 'title': str(args.get('title') or self.case.config.get('label') or self.case.config['case_id'])[:240],
                'tags': list(dict.fromkeys(t.strip() for t in tags if t.strip())),
                'analysis_status': state, 'notes': notes, 'sources': records}
        revision = digest(body)
        # Index summaries, not raw evidence or chat; literal terms have inspectable meaning.
        text = [body['title'], *body['tags'], str(body['case'].get('question') or ''), str(body['case'].get('scope') or '')]
        for n in notes:
            text.extend([n['question'], n['answer']])
            for field in ('alternatives', 'gaps', 'next_checks'):
                text.extend(n.get(field, []))
            text.extend(t.get('description', '') for t in n.get('timeline', []))
        index = '\n'.join(str(v) for v in text).casefold()
        now = time.time()
        with closing(self.connect(write=True)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT revision FROM cases WHERE id=?', (self.archive_id,)).fetchone()
            reused = bool(old and old['revision'] == revision)
            db.execute('INSERT OR IGNORE INTO versions VALUES (?,?,?,?)', (self.archive_id, revision, encoded(body), now))
            if not reused:
                db.execute('INSERT OR REPLACE INTO cases VALUES (?,?,?,?,?,?)',
                           (self.archive_id, revision, bool(body['case'].get('synthetic')), index, str(self.case.db), now))
        return {'archive_id': self.archive_id, 'revision': revision, 'reused': reused,
                'saved_notes': len(notes), 'saved_sources': len(records), 'analysis_status': state}

    @staticmethod
    def body(row):
        value = json.loads(row['body'])
        if digest(value) != row['revision']:
            raise ValueError('보관본 내용이 변경되어 검증할 수 없습니다.')
        return value

    @staticmethod
    def source_state(row, body):
        path = Path(row['source_db'])
        if not path.is_file():
            return 'unavailable'
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                db.execute('PRAGMA query_only=ON')
                values = db.execute("SELECT data FROM events WHERE kind='note' ORDER BY time DESC,rowid DESC").fetchall()
            latest = {}
            for value, in values:
                n = json.loads(value)
                latest.setdefault(n['note_id'], n)
            return 'unchanged' if sorted(latest.values(), key=lambda n: n['note_id']) == body['notes'] else 'changed'
        except (OSError, sqlite3.Error, ValueError, KeyError):
            return 'unavailable'

    def card(self, row, body):
        return {'archive_id': row['id'], 'revision': row['revision'], 'title': body['title'],
                'case_id': body['case']['case_id'], 'question': body['case'].get('question'),
                'scope': body['case'].get('scope'), 'synthetic': bool(body['case'].get('synthetic')),
                'tags': body['tags'], 'analysis_status': body['analysis_status'], 'saved_at': row['saved_at'],
                'source_state': self.source_state(row, body), 'note_count': len(body['notes']),
                'summary': '\n'.join(n['answer'] for n in body['notes'])[:600]}

    def search(self, args):
        query = str(args.get('query') or '').strip()
        if len(query) > 512:
            raise ValueError('Search query must be at most 512 characters')
        terms = list(dict.fromkeys(re.findall(r'[\w./:-]+', query.casefold())))[:12]
        offset, limit = max(0, int(args.get('offset', 0))), max(1, min(20, int(args.get('limit', 10))))
        if not self.path.is_file():
            return {'cases': [], 'total': 0, 'next_offset': None, 'query_terms': terms}
        clauses = ['c.synthetic=?']
        params = [bool(self.case.config.get('synthetic'))]
        if args.get('exclude_current', True):
            clauses.append('c.id!=?')
            params.append(self.archive_id)
        if terms:
            clauses.append('(' + ' OR '.join('instr(c.search_text,?)>0' for _ in terms) + ')')
            params += terms
        where = ' AND '.join(clauses)
        rank = '+'.join('(instr(c.search_text,?)>0)' for _ in terms) or '0'
        with closing(self.connect()) as db:
            total = db.execute('SELECT count(*) FROM cases c WHERE ' + where, params).fetchone()[0]
            rows = db.execute('SELECT c.*,v.body,(' + rank + ') AS matched_count FROM cases c JOIN versions v ON c.id=v.id AND c.revision=v.revision WHERE ' + where + ' ORDER BY matched_count DESC,c.saved_at DESC,c.id LIMIT ? OFFSET ?', terms + params + [limit, offset]).fetchall()
        cards = []
        for row in rows:
            body = self.body(row)
            cards.append({**self.card(row, body), 'matched_terms': [t for t in terms if t in row['search_text']]})
        return {'cases': cards, 'total': total, 'next_offset': offset + limit if offset + limit < total else None,
                'query_terms': terms, 'search_method': 'literal_keywords',
                'meaning': 'Keyword matches in archived notes, not semantic similarity or evidence of a link between incidents.'}

    def get(self, args):
        if not self.path.is_file():
            raise ValueError('아직 보관된 사례가 없습니다.')
        with closing(self.connect()) as db:
            head = db.execute('SELECT * FROM cases WHERE id=? AND synthetic=?',
                              (args.get('archive_id', ''), bool(self.case.config.get('synthetic')))).fetchone()
            if not head:
                raise ValueError('이 사례 창고에서 찾을 수 없는 사건입니다.')
            revision = args.get('revision') or head['revision']
            row = db.execute('SELECT * FROM versions WHERE id=? AND revision=?', (head['id'], revision)).fetchone()
            if not row:
                raise ValueError('Unknown archived revision')
            versions = [dict(r) for r in db.execute('SELECT revision,saved_at FROM versions WHERE id=? ORDER BY saved_at DESC', (head['id'],))]
        body = self.body(row)
        if args.get('action') == 'source':
            source = next((s for s in body['sources'] if s['id'] == args.get('source_id')), None)
            if not source:
                raise ValueError('Unknown source in this archived revision')
            return {'archive_id': head['id'], 'revision': revision, 'case_id': body['case']['case_id'],
                    'reference_only': True, 'source': source}
        offset, limit = max(0, int(args.get('offset', 0))), max(1, min(20, int(args.get('limit', 10))))
        selected = body['notes'][offset:offset+limit]
        return {**self.card({**dict(head), 'revision': revision, 'saved_at': row['saved_at']}, body),
                'is_latest_archive': revision == head['revision'], 'reference_only': True,
                'notes': selected, 'next_offset': offset + limit if offset + limit < len(body['notes']) else None,
                'versions': versions,
                'sources': [{'id': s['id'], 'tool': s['data'].get('tool'), 'path': s['data'].get('file_path') or s['data'].get('path')}
                            for s in body['sources'] if any(s['id'] in n.get('evidence_ids', []) or any(s['id'] in t.get('evidence_ids', []) for t in n.get('timeline', [])) for n in selected)]}

    def invoke(self, args):
        action = args.get('action', 'search')
        if action == 'save':
            return self.save(args)
        if action == 'search':
            return self.search(args)
        if action in ('get', 'source'):
            return self.get(args)
        raise ValueError('Supported actions: save, search, get, source')
