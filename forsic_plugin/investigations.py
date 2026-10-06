"""Case directory projected from native Hermes sessions and existing intake bindings."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time


@contextmanager
def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    try:
        yield db
    finally:
        db.close()


class Investigations:
    def __init__(self, home, intake):
        self.home, self.intake = Path(home), Path(intake)

    def list(self):
        if not (self.home / 'state.db').exists():
            return []
        with readonly(self.home / 'state.db') as db:
            sessions = [dict(r) for r in db.execute('SELECT id,parent_session_id,title,started_at,'
                       'ended_at,last_activity_at,archived FROM sessions ORDER BY started_at,id')]
            goals = {r['key'][5:]: json.loads(r['value']) for r in db.execute(
                "SELECT key,value FROM state_meta WHERE key LIKE 'goal:%'")}
        groups = {}
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
                    question=config.get('question', ''), manifest=str(manifest), output_root=config['output_root'],
                    session_id=session['id'], sessions=[], archived=bool(session['archived']),
                    created_at=state.get('created_at', session['started_at']), title=session['title'] or config['label'])
            item = groups[case_id]
            if item['manifest'] != str(manifest):
                raise ValueError('Duplicate case identity')
            item['sessions'].append(session['id'])
            item['resume_session_id'] = session['id']
            item['last_activity_at'] = max(item.get('last_activity_at', 0), session['last_activity_at'] or session['started_at'])
            goal = goals.get(session['id'], {})
            item['goal_status'] = goal.get('status')
            # A stored active goal alone is not proof the process is still investigating.
            recent = time.time() - item['last_activity_at'] < 600
            item['status'] = ('ended' if goal.get('status') == 'done' else
                              'investigating' if goal.get('status') == 'active' and not session['ended_at'] and recent else 'waiting')
        return sorted(groups.values(), key=lambda item: item['last_activity_at'], reverse=True)

    def get(self, case_id='', session_id=''):
        return next((item for item in self.list() if (case_id and item['case_id'] == case_id)
                     or (session_id and session_id in item['sessions'])), None)

    def public(self):
        # Routing paths remain local implementation details, not directory UI fields.
        return [{k: v for k, v in item.items() if k not in ('manifest', 'output_root', 'sessions')}
                for item in self.list()]
