"""Case directory projected from native Hermes sessions and existing intake bindings."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid


@contextmanager
def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    try:
        yield db
    finally:
        db.close()


class RequestConflict(ValueError):
    """An idempotency key was reused for a different explicit UI action."""


class Investigations:
    def __init__(self, home, intake):
        self.home, self.intake = Path(home), Path(intake)

    @staticmethod
    def _conversations(sessions):
        """Collapse compression continuations, never separate branches or another case."""
        by_id = {s['id']: s for s in sessions}
        roots = {}
        for session in sessions:
            current, seen = session, {session['id']}
            while current.get('parent_session_id') in by_id:
                parent = by_id[current['parent_session_id']]
                if parent['id'] in seen or parent.get('end_reason') != 'compression':
                    break
                config = current.get('model_config') or {}
                if isinstance(config, str):
                    try:
                        config = json.loads(config)
                    except (ValueError, TypeError):
                        config = {}
                if not isinstance(config, dict):
                    config = {}
                if current.get('source') == 'tool' or parent['id'] in (
                        config.get('_branched_from'), config.get('_delegate_from'), config.get('_reset_from')):
                    break
                seen.add(parent['id'])
                current = parent
            roots.setdefault(current['id'], []).append(session)
        conversations = []
        for root, members in roots.items():
            # A compressed ancestor may get a late activity heartbeat; follow the
            # lineage to a leaf instead of accidentally resuming that ancestor.
            parents = {s.get('parent_session_id') for s in members}
            tips = [s for s in members if s['id'] not in parents] or members
            tip = max(tips, key=lambda s: (not bool(s['ended_at']), s['last_activity_at'] or s['started_at'], s['id']))
            named = next((s['title'] for s in reversed(members) if s.get('title')), None)
            conversations.append(dict(id=root, resume_session_id=tip['id'], title=named or '새 대화',
                last_activity_at=max(s['last_activity_at'] or s['started_at'] for s in members),
                session_ids=[s['id'] for s in members]))
        return sorted(conversations, key=lambda item: item['last_activity_at'], reverse=True)

    def list(self):
        if not (self.home / 'state.db').exists():
            return []
        with readonly(self.home / 'state.db') as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}
            extra = ','.join(name if name in columns else 'NULL AS ' + name
                             for name in ('end_reason', 'source', 'model_config'))
            sessions = [dict(r) for r in db.execute('SELECT id,parent_session_id,title,started_at,'
                       'ended_at,last_activity_at,archived,' + extra + ' FROM sessions ORDER BY started_at,id')]
            goals = {r['key'][5:]: json.loads(r['value']) for r in db.execute(
                "SELECT key,value FROM state_meta WHERE key LIKE 'goal:%'")}
        groups, rows = {}, {}
        for session in sessions:
            binding = self.intake / (hashlib.sha256(session['id'].encode()).hexdigest() + '.json')
            if not binding.exists():
                continue
            state = json.loads(binding.read_text())
            if not state.get('manifest'):
                continue
            manifest = Path(state['manifest']).resolve()
            if not manifest.is_relative_to((self.intake / 'cases').resolve()):
                continue
            config = json.loads(manifest.read_text())
            case_id = config['case_id']
            if case_id not in groups:
                groups[case_id] = dict(case_id=case_id, label=config['label'], synthetic=bool(config.get('synthetic')),
                    scope=config.get('scope') or state.get('selected_path') or config.get('evidence_root', ''),
                    question=config.get('question', ''), manifest=str(manifest), output_root=config['output_root'],
                    session_id=session['id'], sessions=[], archived=bool(session['archived']),
                    created_at=state.get('created_at', session['started_at']), title=session['title'] or config['label'])
                rows[case_id] = []
            item = groups[case_id]
            if item['manifest'] != str(manifest):
                raise ValueError('Duplicate case identity')
            item['sessions'].append(session['id'])
            rows[case_id].append(session)
        for case_id, item in groups.items():
            conversations = self._conversations(rows[case_id])
            item['conversations'] = conversations
            item['resume_session_id'] = conversations[0]['resume_session_id']
            item['last_activity_at'] = max(c['last_activity_at'] for c in conversations)
            # Case activity follows any live conversation, not the last-created row.
            tips = [next(s for s in rows[case_id] if s['id'] == c['resume_session_id']) for c in conversations]
            active = [s for s in tips if goals.get(s['id'], {}).get('status') == 'active'
                      and not s['ended_at'] and time.time() - (s['last_activity_at'] or s['started_at']) < 600]
            item['goal_status'] = ('active' if active else goals.get(item['resume_session_id'], {}).get('status'))
            item['status'] = ('investigating' if active else 'ended' if item['goal_status'] == 'done' else 'waiting')
        return sorted(groups.values(), key=lambda item: item['last_activity_at'], reverse=True)

    def get(self, case_id='', session_id=''):
        if not case_id and not session_id:
            return None
        return next((item for item in self.list()
                     if (not case_id or item['case_id'] == case_id)
                     and (not session_id or session_id in item['sessions'])), None)

    def public(self):
        # Evidence scope is a product concept; storage/manifest paths stay private.
        return [{**{k: v for k, v in item.items() if k not in ('manifest', 'output_root', 'sessions')},
                 'session_ids': item['sessions']} for item in self.list()]

    def create_conversation(self, request_id, *, case_id='', path=''):
        """Explicit UI intake, not a run: bind an empty native conversation only.

        Persist a receipt before mutation. Retries reuse the same session and intake
        binding after a partial failure; reusing the key for another scope fails.
        """
        import fcntl
        from hermes_state import SessionDB
        from forsic_plugin.intake import Intake, write_json

        request_id = str(uuid.UUID(str(request_id)))
        intent = {'case_id': case_id, 'path': path.strip()}
        if bool(case_id) == bool(intent['path']):
            raise ValueError('기존 사건 또는 새 증거 경로 중 하나를 선택해주세요.')
        receipts = self.intake / 'ui-requests'
        receipt_path = receipts / (request_id + '.json')
        if receipt_path.exists():
            recorded = json.loads(receipt_path.read_text())
            if recorded['intent'] != intent:
                raise RequestConflict('같은 요청 번호에 다른 사건이나 경로를 사용할 수 없습니다.')
            if recorded.get('result'):
                return recorded['result']
        if intent['path']:
            evidence = Path(intent['path'].strip('\"\'`')).expanduser().resolve(strict=True)
            if not evidence.is_file() and not evidence.is_dir():
                raise ValueError('일반 파일이나 증거 폴더를 선택해주세요.')
            evidence_root = evidence if evidence.is_dir() else evidence.parent
            # Validate before even writing the request receipt: the writable
            # database/intake store must never be inside selected evidence.
            if any(p.resolve().is_relative_to(evidence_root) for p in (self.intake, self.home)):
                raise ValueError('증거와 포식이 저장소는 분리된 경로여야 합니다.')
        receipts.mkdir(parents=True, exist_ok=True)
        with (receipts / (request_id + '.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
            if receipt and receipt['intent'] != intent:
                raise RequestConflict('같은 요청 번호에 다른 사건이나 경로를 사용할 수 없습니다.')
            if receipt and receipt.get('result'):
                return receipt['result']
            existing = self.get(case_id=case_id) if case_id else None
            if case_id and existing is None:
                raise LookupError('사건을 찾을 수 없습니다.')
            if receipt is None:
                receipt = {'intent': intent, 'session_id': 'forsic-' + uuid.uuid4().hex, 'created_at': time.time()}
                write_json(receipt_path, receipt)
            session_id = receipt['session_id']
            intake = Intake(self.intake)
            if existing:
                state = intake.state(session_id)
                if state.get('manifest') and state['manifest'] != existing['manifest']:
                    raise RequestConflict('이 대화는 다른 사건에 연결되어 있습니다.')
                if not state.get('manifest'):
                    # No parent, messages, goal or previous answer is inherited.
                    write_json(intake.path(session_id), {'stage': 'case_bound',
                        'manifest': existing['manifest'], 'selected_path': existing['scope'],
                        'created_at': receipt['created_at']})
            else:
                # The literal comes from the user's dedicated evidence-path form.
                # Use the same lightweight, read-only intake as native conversation.
                if not intake.state(session_id).get('manifest'):
                    intake.before(session_id, message=intent['path'])
                else:
                    intake.messages[session_id] = intent['path']
                intake.inspect(session_id, {'path': intent['path']})
            state = intake.state(session_id)
            manifest = json.loads(Path(state['manifest']).read_text())
            db = SessionDB(self.home / 'state.db')
            try:
                db.create_session(session_id=session_id, source='cli', profile_name='default')
            finally:
                db.close()
            result = {'case_id': manifest['case_id'], 'session_id': session_id}
            receipt['result'] = result
            write_json(receipt_path, receipt)
            return result
