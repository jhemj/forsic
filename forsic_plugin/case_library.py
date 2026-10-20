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
    def __init__(self, case=None, *, scope=None):
        from .tenants import current_scope,validate_case
        self.scope=scope if scope is not None else current_scope()
        self.case = case
        if self.scope is not None:
            if case is not None:
                validate_case(self.scope,case)
                if case.config.get('library_path') and Path(case.config['library_path']).resolve()!=Path(self.scope['library_store']).resolve():
                    raise PermissionError('사건의 사례 저장소가 테넌트 소유 범위와 다릅니다.')
            self.path=Path(self.scope['library_store'])
        elif case is not None:
            self.path=Path(case.config.get('library_path') or Path(__file__).resolve().parents[1]/'state/library/cases.sqlite3')
        else:raise ValueError('사례를 조회할 테넌트를 선택해주세요.')
        self.archive_id=(hashlib.sha256((str(case.manifest)+'\n'+str(case.config.get('case_id',''))).encode()).hexdigest()[:32]
                         if case is not None else None)

    def _scope_check(self):
        from .tenants import current_scope
        scope=current_scope()
        if self.scope is None and scope is not None:
            raise PermissionError('등록된 테넌트에서 공용 사례 저장소를 사용할 수 없습니다.')
        if self.scope is not None and (scope is None or scope['tenant_id']!=self.scope['tenant_id'] or
            Path(scope['library_store']).resolve()!=self.path.resolve()):
            raise PermissionError('사례 조회 중 테넌트 소유 범위가 변경되었습니다.')
        if self.path.is_symlink():raise PermissionError('사례 저장소 링크는 허용하지 않습니다.')

    def _archive_owned(self,row,body):
        if self.scope is None:return
        from .tenants import owns_case_source
        source=Path(row['source_db'])
        case=body['case'];case_id=case['case_id']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',case_id):raise PermissionError('사례 사건의 소유 범위를 확인할 수 없습니다.')
        manifest=(Path(self.scope['case_root'])/case_id/'case.json').resolve()
        expected=hashlib.sha256((str(manifest)+'\n'+case_id).encode()).hexdigest()[:32]
        if (expected!=row['id'] or source.is_symlink() or source.parent.resolve()!=Path(case['output_root']).resolve()):
            raise PermissionError('사례와 원래 사건 출처의 소유 범위가 다릅니다.')
        owns_case_source(self.scope,manifest if manifest.is_file() else None,source_db=source)

    def connect(self, write=False):
        self._scope_check()
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
        self._scope_check()
        if self.case is None:raise ValueError('저장할 사건을 명시해주세요.')
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

    def source_state(self,row, body):
        self._scope_check()
        self._archive_owned(row,body)
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
                'reference_only': True, 'current_case_evidence': False,
                'case_id': body['case']['case_id'], 'question': body['case'].get('question'),
                'scope': body['case'].get('scope'), 'synthetic': bool(body['case'].get('synthetic')),
                'tags': body['tags'], 'analysis_status': body['analysis_status'], 'saved_at': row['saved_at'],
                'source_state': self.source_state(row, body), 'note_count': len(body['notes']),
                'summary': '\n'.join(n['answer'] for n in body['notes'])[:600],
                'read_call': self.read_call('get', row['id'], row['revision'])}

    @staticmethod
    def read_call(action, archive_id, revision, **arguments):
        return {'tool': 'forsic_cases', 'arguments': {'action': action, 'archive_id': archive_id,
                                                    'revision': revision, **arguments}}

    def search(self, args):
        self._scope_check()
        query = str(args.get('query') or '').strip()
        if len(query) > 512:
            raise ValueError('Search query must be at most 512 characters')
        terms = list(dict.fromkeys(re.findall(r'[\w./:-]+', query.casefold())))[:12]
        offset, limit = max(0, int(args.get('offset', 0))), max(1, min(20, int(args.get('limit', 10))))
        reference = {'reference_only': True, 'current_case_evidence': False, 'search_method': 'literal_keywords',
                     'meaning': 'Keyword matches in archived notes, not semantic similarity or evidence of a link between incidents.'}
        if not self.path.is_file():
            return {**reference, 'cases': [], 'total': 0, 'next_offset': None, 'query_terms': terms}
        self._scope_check()
        clauses = ['1=1']
        params = []
        if self.case is not None:
            clauses.append('c.synthetic=?');params.append(bool(self.case.config.get('synthetic')))
        if args.get('exclude_current', True) and self.archive_id is not None:
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
            self._archive_owned(row,body)
            cards.append({**self.card(row, body), 'matched_terms': [t for t in terms if t in row['search_text']]})
        return {**reference, 'cases': cards, 'total': total,
                'next_offset': offset + limit if offset + limit < total else None, 'query_terms': terms}

    def get(self, args):
        self._scope_check()
        if not self.path.is_file():
            raise ValueError('아직 보관된 사례가 없습니다.')
        with closing(self.connect()) as db:
            filters='id=?'+(' AND synthetic=?' if self.case is not None else '')
            params=[args.get('archive_id','')]+([bool(self.case.config.get('synthetic'))] if self.case is not None else [])
            head = db.execute('SELECT * FROM cases WHERE '+filters,params).fetchone()
            if not head:
                raise ValueError('이 사례 창고에서 찾을 수 없는 사건입니다.')
            revision = args.get('revision') or head['revision']
            row = db.execute('SELECT * FROM versions WHERE id=? AND revision=?', (head['id'], revision)).fetchone()
            if not row:
                raise ValueError('Unknown archived revision')
            versions = [dict(r) for r in db.execute('SELECT revision,saved_at FROM versions WHERE id=? ORDER BY saved_at DESC', (head['id'],))]
        body = self.body(row)
        self._archive_owned(head,body)
        if args.get('action') == 'source':
            source = next((s for s in body['sources'] if s['id'] == args.get('source_id')), None)
            if not source:
                raise ValueError('Unknown source in this archived revision')
            return {'archive_id': head['id'], 'revision': revision, 'case_id': body['case']['case_id'],
                    'reference_only': True, 'current_case_evidence': False,
                    'source_state': self.source_state(head, body), 'is_latest_archive': revision == head['revision'],
                    'source': source}
        offset, limit = max(0, int(args.get('offset', 0))), max(1, min(20, int(args.get('limit', 10))))
        selected = body['notes'][offset:offset+limit]
        next_offset = offset + limit if offset + limit < len(body['notes']) else None
        return {**self.card({**dict(head), 'revision': revision, 'saved_at': row['saved_at']}, body),
                'is_latest_archive': revision == head['revision'],
                'notes': selected, 'next_offset': next_offset,
                **({'next_call': self.read_call('get', head['id'], revision, offset=next_offset, limit=limit)}
                   if next_offset is not None else {}),
                'versions': versions,
                'sources': [{'id': s['id'], 'tool': s['data'].get('tool'), 'path': s['data'].get('file_path') or s['data'].get('path'),
                             'source_call': self.read_call('source', head['id'], revision, source_id=s['id'])}
                            for s in body['sources'] if any(s['id'] in n.get('evidence_ids', []) or any(s['id'] in t.get('evidence_ids', []) for t in n.get('timeline', [])) for n in selected)]}

    def invoke(self, args):
        self._scope_check()
        action = args.get('action', 'search')
        if action == 'save':
            return self.save(args)
        if action == 'search':
            return self.search(args)
        if action in ('get', 'source'):
            return self.get(args)
        raise ValueError('Supported actions: save, search, get, source')
