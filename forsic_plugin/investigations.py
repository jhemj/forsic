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


MAX_CONCURRENT_INVESTIGATIONS = 2
_CAPACITY_REASON = (f'동시에 접수할 수 있는 조사 {MAX_CONCURRENT_INVESTIGATIONS}개가 모두 사용 중입니다. '
                    '기존 조사를 완료하거나 종료한 뒤 새 조사를 시작해주세요. 일시 중지된 조사도 포함됩니다.')


class InvestigationUnavailable(RequestConflict):
    """A new case cannot be admitted without a free investigation reservation."""


def conversation_usage(session_ids, usage_rows):
    """Physical-call deltas; never add inherited cumulative session counters."""
    if usage_rows is None:
        return None
    rows=[r for r in usage_rows if r['session_id'] in session_ids]
    if not rows:
        return None  # Missing accounting is not a measured zero.
    fields=('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','api_call_count')
    result={k:sum(r[k] or 0 for r in rows) for k in fields}
    result['prompt_tokens']=result['input_tokens']+result['cache_read_tokens']+result['cache_write_tokens']
    result['total_tokens']=result['prompt_tokens']+result['output_tokens']
    result['auxiliary_tokens']=sum(sum(r[k] or 0 for k in fields[:4]) for r in rows if r['task'])
    result['basis']='recorded_native_api_usage'
    return result


class Investigations:
    def __init__(self, home, intake):
        self.home, self.intake = Path(home), Path(intake)
        from .tenants import TenantRegistry, native_db
        registry = TenantRegistry()
        self.tenant_scope = registry.resolve(profile_home=self.home) if registry.path.exists() else None
        if self.tenant_scope and self.intake.resolve() != Path(self.tenant_scope['intake_root']).resolve():
            raise PermissionError('조사 접수 저장소와 테넌트가 일치하지 않습니다.')
        self.native_path = native_db(self.tenant_scope) if self.tenant_scope else self.home / 'state.db'

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
        if not self.native_path.exists():
            return []
        with readonly(self.native_path) as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}
            extra = ','.join(name if name in columns else 'NULL AS ' + name
                             for name in ('end_reason', 'source', 'model_config', 'profile_name'))
            sessions = [dict(r) for r in db.execute('SELECT id,parent_session_id,title,started_at,'
                       'ended_at,last_activity_at,archived,' + extra + ' FROM sessions ORDER BY started_at,id')]
            if self.tenant_scope:
                owner = self.tenant_scope['profile_name']
                sessions = [s for s in sessions if s.get('profile_name') == owner or
                            (self.tenant_scope.get('legacy') and owner == 'default' and not s.get('profile_name'))]
            goals = {r['key'][5:]: json.loads(r['value']) for r in db.execute(
                "SELECT key,value FROM state_meta WHERE key LIKE 'goal:%'")}
            has_usage=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_model_usage'").fetchone()
            usage_rows=[dict(r) for r in db.execute('SELECT session_id,task,input_tokens,output_tokens,'
                'cache_read_tokens,cache_write_tokens,api_call_count FROM session_model_usage')] if has_usage else None
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
            if self.tenant_scope:
                from .tenants import validate_case
                validate_case(self.tenant_scope, manifest)
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
            for conversation in conversations:
                conversation['usage']=conversation_usage(conversation['session_ids'],usage_rows)
            item['conversations'] = conversations
            item['resume_session_id'] = conversations[0]['resume_session_id']
            item['last_activity_at'] = max(c['last_activity_at'] for c in conversations)
            # Case activity follows any live conversation, not the last-created row.
            tips = [next(s for s in rows[case_id] if s['id'] == c['resume_session_id']) for c in conversations]
            active = [s for s in tips if goals.get(s['id'], {}).get('status') == 'active'
                      and not s['ended_at'] and time.time() - (s['last_activity_at'] or s['started_at']) < 600]
            item['goal_status'] = ('active' if active else goals.get(item['resume_session_id'], {}).get('status'))
            closed = item['goal_status'] == 'cleared' and all(
                goals.get(s['id'], {}).get('status') in ('done', 'cleared') for s in tips)
            item['status'] = ('investigating' if active else 'closed' if closed
                              else 'ended' if item['goal_status'] == 'done' else 'waiting')
        return sorted(groups.values(), key=lambda item: item['last_activity_at'], reverse=True)

    def get(self, case_id='', session_id=''):
        if not case_id and not session_id:
            return None
        return next((item for item in self.list()
                     if (not case_id or item['case_id'] == case_id)
                     and (not session_id or session_id in item['sessions'])), None)

    def public(self):
        # Evidence scope is a product concept; storage/manifest paths stay private.
        result = [{**{k: v for k, v in item.items() if k not in ('manifest', 'output_root', 'sessions')},
                   'session_ids': item['sessions']} for item in self.list()]
        if self.tenant_scope:
            from urllib.parse import urlencode
            for item in result:
                item.update(tenant_id=self.tenant_scope['tenant_id'], profile_name=self.tenant_scope['profile_name'])
                def url(session_id):
                    return '/chat?' + urlencode({'tenant_id': item['tenant_id'], 'profile': item['profile_name'],
                                                 'case': item['case_id'], 'resume': session_id})
                item['resume_url'] = url(item['resume_session_id'])
                item['conversations'] = [{**c, 'resume_url': url(c['resume_session_id'])} for c in item['conversations']]
        return result

    def new_investigation(self, *, exclude_session_id='', _local=False):
        """Read persisted native lifecycle; stale heartbeats never release a case.

        Intake/receipts also reserve creation until a native goal exists. These
        are the existing recovery records, not a second investigation ledger.
        Capacity is shared across tenants; only local case identities are returned.
        """
        unavailable = {'allowed': False, 'reason': '기존 조사 상태를 확인할 수 없어 새 조사를 시작할 수 없습니다. 기존 조사와 저장 상태를 확인해주세요.',
                       'active_case': None, 'active_count': None, 'limit': MAX_CONCURRENT_INVESTIGATIONS, 'available_slots': None}
        try:
            if self.tenant_scope and not _local:
                from .tenants import TenantRegistry
                registry = TenantRegistry()
                local = self.new_investigation(exclude_session_id=exclude_session_id, _local=True)
                total = local['active_count']
                if total is None:
                    return local
                for tenant in registry.list_public():
                    scope = registry.resolve(tenant_id=tenant['tenant_id'])
                    if scope['tenant_id'] == self.tenant_scope['tenant_id']:
                        continue
                    other = Investigations(scope['profile_home'], scope['intake_root']).new_investigation(_local=True)
                    if other['active_count'] is None:
                        return {**unavailable, 'reason': '다른 작업공간의 조사 상태를 확인할 수 없어 새 조사를 시작할 수 없습니다.'}
                    total += other['active_count']
                allowed = total < MAX_CONCURRENT_INVESTIGATIONS
                return {**local, 'allowed': allowed,
                        'reason': None if allowed else _CAPACITY_REASON,
                        'active_count': total, 'available_slots': max(0, MAX_CONCURRENT_INVESTIGATIONS-total)}
            cases = self.list()
            goals, sessions, leases = {}, {}, None
            has_native = self.native_path.exists()
            if has_native:
                with readonly(self.native_path) as db:
                    sessions = {hashlib.sha256(r['id'].encode()).hexdigest(): dict(r)
                                for r in db.execute('SELECT id FROM sessions')}
                    for row in db.execute("SELECT key,value FROM state_meta WHERE key LIKE 'goal:%'"):
                        goal = json.loads(row['value'])
                        if not isinstance(goal, dict) or goal.get('status') not in ('active', 'paused', 'done', 'cleared'):
                            raise ValueError('Unknown native goal status')
                        goals[row['key'][5:]] = goal
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_turn_leases'").fetchone():
                        leases = {row['conversation_id'] for row in db.execute(
                            'SELECT conversation_id FROM session_turn_leases WHERE expires_at > ?', (time.time(),))}
            states = {}
            # A live native lease with no session row is inconsistent; it must
            # not be mistaken for deliberately deleted, idle history.
            uncertain = bool(leases and leases - {row['id'] for row in sessions.values()})
            pending = set()
            for path in self.intake.glob('*.json'):
                if len(path.stem) != 64 or any(c not in '0123456789abcdef' for c in path.stem):
                    continue
                state = json.loads(path.read_text())
                if not isinstance(state, dict) or not isinstance(state.get('stage'), str):
                    raise ValueError('Invalid intake state')
                if not state.get('manifest'):
                    if state['stage'] != 'awaiting_path':
                        raise ValueError('Missing case binding')
                    continue
                manifest = Path(state['manifest']).resolve(strict=True)
                if not manifest.is_relative_to((self.intake / 'cases').resolve()):
                    raise ValueError('Invalid case binding')
                config = json.loads(manifest.read_text())
                if not isinstance(config, dict) or not config.get('case_id') or not config.get('label'):
                    raise ValueError('Invalid case manifest')
                if path.stem not in sessions:
                    # Native deletion retains Forsic bindings and ledgers. Such
                    # history is not a live investigation; an unfinished UI
                    # receipt below still reserves a partially created session.
                    # A missing native store is unknown, rather than deletion.
                    if not has_native and path.stem != hashlib.sha256(exclude_session_id.encode()).hexdigest():
                        uncertain = True
                else:
                    states[sessions[path.stem]['id']] = state
            for receipt_path in (self.intake / 'ui-requests').glob('*.json'):
                receipt = json.loads(receipt_path.read_text())
                if not isinstance(receipt, dict) or not isinstance(receipt.get('intent'), dict) or not receipt.get('session_id'):
                    raise ValueError('Invalid request receipt')
                if (receipt['intent'].get('path') and not receipt.get('result')
                        and receipt['session_id'] != exclude_session_id):
                    pending.add(receipt['session_id'])
            blockers, reserved_sessions = [], set()
            for case in cases:
                for conversation in case['conversations']:
                    sid = conversation['resume_session_id']
                    if sid == exclude_session_id:
                        continue
                    goal = goals.get(sid)
                    native = goal.get('status') if goal else None
                    running = bool(leases and set(conversation['session_ids']) & leases)
                    if native in ('done', 'cleared') and not running:
                        continue
                    state = states[sid]
                    if native is None and state['stage'] == 'case_bound' and not running:
                        continue  # An empty conversation in an existing case.
                    if native is None and state['stage'] in ('focused_question', 'goal_manual') and not running and leases is not None:
                        # These intake stages persist after an ordinary answer.
                        # An explicit completed turn releases manual work; absence
                        # of a goal/lease alone never releases fresh intake.
                        with readonly(Path(case['output_root']) / 'activity.sqlite3') as db:
                            members = conversation['session_ids']
                            last = db.execute('SELECT kind FROM events WHERE session IN (' + ','.join('?' for _ in members) +
                                ") AND kind IN ('turn_start','turn_complete') ORDER BY time DESC,rowid DESC LIMIT 1", members).fetchone()
                        if last and last['kind'] == 'turn_complete':
                            continue
                    if native is None and state['stage'] not in ('awaiting_question', 'focused_question', 'goal_pending',
                                                                  'goal_queued', 'goal_manual', 'goal_active', 'case_bound'):
                        raise ValueError('Unknown investigation lifecycle')
                    status = 'paused' if native == 'paused' else 'investigating' if native == 'active' or running else 'waiting'
                    reserved_sessions.update(conversation['session_ids'])
                    blockers.append({'case_id': case['case_id'], 'label': case['label'],
                                     'resume_session_id': sid, 'status': status})
                    from hermes_cli.goals import GoalState, goal_identity
                    valid_goal = GoalState.from_json(json.dumps(goal)) if goal else None
                    blockers[-1].update(goal_id=goal_identity(valid_goal) if valid_goal and valid_goal.goal.strip() else '',
                                        closable=bool(valid_goal and valid_goal.goal.strip() and native in ('active', 'paused')))
            if uncertain:
                return unavailable
            active = min(blockers, key=lambda item: ('investigating', 'paused', 'waiting').index(item['status'])) if blockers else None
            # A partially committed receipt and its native conversation occupy
            # the same reservation; replay excludes only its own pending session.
            count = len(blockers) + len(pending-reserved_sessions)
            allowed = count < MAX_CONCURRENT_INVESTIGATIONS
            reason = None if allowed else _CAPACITY_REASON
            return {'allowed': allowed, 'reason': reason, 'active_case': active,
                    'active_count': count, 'limit': MAX_CONCURRENT_INVESTIGATIONS,
                    'available_slots': max(0, MAX_CONCURRENT_INVESTIGATIONS-count)}
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError):
            return unavailable

    def close_investigation(self, request_id, *, case_id, session_id, profile, goal_id, confirmed):
        """Explicit user stop through native controls; never resume/build an agent or mark success."""
        import fcntl
        import sys
        from contextlib import ExitStack
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        from hermes_cli import goals
        from .intake import write_json

        if confirmed is not True or not goal_id:
            raise RequestConflict('종료할 조사를 확인해주세요.')
        request_id = str(uuid.UUID(str(request_id)))
        intent = dict(case_id=case_id, session_id=session_id, profile=profile, goal_id=goal_id)
        receipts = self.intake / 'ui-close-requests'
        receipts.mkdir(parents=True, exist_ok=True)
        receipt_path = receipts / (request_id + '.json')
        with (receipts / 'close.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
            if receipt and receipt.get('intent') != intent:
                raise RequestConflict('같은 요청 번호에 다른 조사를 종료할 수 없습니다.')
            case = self.get(case_id=case_id, session_id=session_id)
            conversation = next((c for c in (case or {}).get('conversations', [])
                                 if c['resume_session_id'] == session_id), None)
            if conversation is None:
                raise RequestConflict('선택한 조사 대화가 변경됐습니다. 목록을 새로고침해주세요.')
            members = set(conversation['session_ids'])
            token = set_hermes_home_override(self.home)
            try:
                native_db = goals._get_session_db()
                if native_db is None:
                    raise RequestConflict('저장된 조사 상태를 확인할 수 없습니다.')
                native_session = native_db.get_session(session_id)
                if not native_session or native_session.get('profile_name') not in (None, '', profile):
                    raise RequestConflict('이 프로필에 연결된 조사 대화가 아닙니다.')
                key = 'goal:' + session_id
                raw = native_db.get_meta(key)
                current = goals.GoalState.from_json(raw) if raw else None
                if current is None or goals.goal_identity(current) != goal_id:
                    raise RequestConflict('조사 목표가 변경됐습니다. 목록을 새로고침해주세요.')
                if receipt and receipt.get('result'):
                    if current.status != 'cleared':
                        raise RequestConflict('종료 후 조사 상태가 변경됐습니다. 목록을 새로고침해주세요.')
                    return {**receipt['result'], 'new_investigation': self.new_investigation()}
                if current.status not in ('active', 'paused') and not (receipt and current.status == 'cleared'):
                    raise RequestConflict('종료할 진행 중 조사가 없습니다.')
                if receipt is None:
                    write_json(receipt_path, {'intent': intent})

                class CloseGoalManager(goals.GoalManager):
                    def _save(manager):
                        goals.settle_runtime(manager.state)
                        manager.state.evaluation_receipt = None
                        # Retain native pause/clear semantics while fencing the confirmed
                        # identity and idle guards in the SAME native write transaction.
                        def save(conn):
                            stored = conn.execute('SELECT value FROM state_meta WHERE key=?', (key,)).fetchone()
                            state = goals.GoalState.from_json(stored[0]) if stored else None
                            if state is None or goals.goal_identity(state) != goal_id or stored[0] != manager._control_expected:
                                raise RequestConflict('조사 목표가 변경됐습니다. 다시 확인해주세요.')
                            if state.status not in ('active', 'paused', 'cleared') or (
                                    manager.state.status == 'paused' and state.status == 'cleared'):
                                raise RequestConflict('조사 종료 상태가 변경됐습니다. 다시 확인해주세요.')
                            if manager.state.status == 'cleared':
                                if state.status != 'paused' and not (current.status == 'cleared' and state.status == 'cleared'):
                                    raise RequestConflict('조사 종료 상태가 변경됐습니다. 목록을 새로고침해주세요.')
                                for table, column in (('session_turn_leases', 'conversation_id'), ('compression_locks', 'session_id')):
                                    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                                        raise RequestConflict('조사 실행 상태를 확인할 수 없습니다.')
                                    if conn.execute('SELECT 1 FROM ' + table + ' WHERE ' + column + ' IN (' +
                                                    ','.join('?' for _ in members) + ') AND expires_at > ? LIMIT 1',
                                                    [*members, time.time()]).fetchone():
                                        raise InvestigationUnavailable('조사 작업을 종료 중입니다. 잠시 후 다시 시도해주세요.')
                            value = manager.state.to_json()
                            conn.execute('UPDATE state_meta SET value=? WHERE key=? AND value=?',
                                         (value, key, manager._control_expected))
                            manager._control_expected = value
                        native_db._execute_write(save)
                        return manager.state

                manager = CloseGoalManager(session_id=session_id)
                if current.status != 'cleared':
                    manager.pause(reason='user-requested-close')
                gateway = sys.modules.get('tui_gateway.server')
                runtimes = []
                if gateway is not None:
                    with gateway._sessions_lock:
                        runtimes = [(sid, runtime) for sid, runtime in gateway._sessions.items()
                                    if runtime.get('session_key') in members and
                                    Path(runtime.get('profile_home') or gateway._hermes_home).resolve() == self.home.resolve()]
                    for sid, runtime in runtimes if current.status != 'cleared' else []:
                        latest = goals.load_goal(session_id)
                        if latest is None or goals.goal_identity(latest) != goal_id or latest.status != 'paused':
                            raise RequestConflict('조사 목표가 변경됐습니다. 다시 확인해주세요.')
                        # Native history_lock is non-reentrant; interruption owns
                        # its short critical sections and must run outside it.
                        gateway._interrupt_session_turn(sid, runtime)
                # No cold session.resume: it can create an agent and schedule crash recovery.
                deadline = time.monotonic() + 2
                while True:
                    with ExitStack() as held:
                        if gateway is not None:
                            held.enter_context(gateway._sessions_lock)
                            matching = [(sid, runtime) for sid, runtime in gateway._sessions.items()
                                        if runtime.get('session_key') in members and
                                        Path(runtime.get('profile_home') or gateway._hermes_home).resolve() == self.home.resolve()]
                            if {(sid, id(runtime)) for sid, runtime in matching} != {(sid, id(runtime)) for sid, runtime in runtimes}:
                                raise InvestigationUnavailable('조사 대화 상태가 변경됐습니다. 잠시 후 다시 시도해주세요.')
                        for _, runtime in runtimes:
                            held.enter_context(runtime['history_lock'])
                        from hermes_cli.web_server_idle_exit import _session_work_in_flight
                        from tools.async_delegation import has_live_for_session
                        busy = any(_session_work_in_flight(runtime) or runtime.get('_compute_host_turn_id') or
                                   getattr(runtime.get('agent'), '_background_review_run', None) is not None or
                                   getattr(runtime.get('agent'), '_background_review_agent', None) is not None or
                                   bool(getattr(runtime.get('agent'), '_active_children', []))
                                   for _, runtime in runtimes)
                        busy = busy or any(has_live_for_session(session_key=member) for member in members)
                        if not busy:
                            from tui_gateway.turn_marker import clear_turn_marker, _marker_path
                            for member in members:
                                clear_turn_marker(self.home, member)
                            marker_path = _marker_path(self.home)
                            markers = json.loads(marker_path.read_text()) if marker_path.exists() else {}
                            if not isinstance(markers, dict) or members.intersection(markers):
                                raise RequestConflict('중단된 조사 재개 상태를 해제하지 못했습니다. 다시 시도해주세요.')
                            manager.clear()
                            break
                    if time.monotonic() >= deadline:
                        raise InvestigationUnavailable('조사 작업을 종료 중입니다. 잠시 후 다시 시도해주세요.')
                    time.sleep(.05)
                result = dict(status='closed', case_id=case_id, session_id=session_id, goal_id=goal_id)
                write_json(receipt_path, {'intent': intent, 'result': result})
                return {**result, 'new_investigation': self.new_investigation()}
            finally:
                reset_hermes_home_override(token)

    def create_conversation(self, request_id, *, case_id='', path='', onboarding=None, evidence_root_limit=None):
        """Explicit UI intake, not a run: bind an empty native conversation only.

        Persist a receipt before mutation. Retries reuse the same session and intake
        binding after a partial failure; reusing the key for another scope fails.
        """
        import fcntl
        from hermes_state import SessionDB
        from forsic_plugin.intake import Intake, write_json
        from forsic_plugin.onboarding import choices

        request_id = str(uuid.UUID(str(request_id)))
        intent = {'case_id': case_id, 'path': path.strip()}
        selected = choices(onboarding) if onboarding is not None else None
        if selected is not None:
            if case_id:
                raise ValueError('새 사건에서 조사 목적을 선택해주세요.')
            intent['onboarding'] = {k: selected[k] for k in ('purpose', 'environment', 'question')}
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
            if evidence_root_limit is not None and not evidence.is_relative_to(Path(evidence_root_limit)):
                raise PermissionError('원격 접속에서는 설정된 공유 자료 폴더 안에서 선택해주세요.')
            if not evidence.is_file() and not evidence.is_dir():
                raise ValueError('일반 파일이나 증거 폴더를 선택해주세요.')
            evidence_root = evidence if evidence.is_dir() else evidence.parent
            # Validate before even writing the request receipt: the writable
            # database/intake store must never be inside selected evidence.
            if any(p.resolve().is_relative_to(evidence_root) for p in (self.intake, self.home)):
                raise ValueError('증거와 포식이 저장소는 분리된 경로여야 합니다.')
        receipts.mkdir(parents=True, exist_ok=True)
        from .tenants import TenantRegistry
        admission_path = TenantRegistry().root / 'admission.lock' if self.tenant_scope else receipts / 'admission.lock'
        with (receipts / (request_id + '.lock')).open('a') as lock, admission_path.open('a') as admission:
            fcntl.flock(lock, fcntl.LOCK_EX)
            # Different request IDs must share admission through the native-session
            # write and receipt commit, including the creation-to-first-goal gap.
            fcntl.flock(admission, fcntl.LOCK_EX)
            receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
            if receipt and receipt['intent'] != intent:
                raise RequestConflict('같은 요청 번호에 다른 사건이나 경로를 사용할 수 없습니다.')
            if receipt and receipt.get('result'):
                return receipt['result']
            if not case_id:
                availability = self.new_investigation(exclude_session_id=receipt['session_id'] if receipt else '')
                if not availability['allowed']:
                    raise InvestigationUnavailable(availability['reason'])
            existing = self.get(case_id=case_id) if case_id else None
            if case_id and existing is None:
                raise LookupError('사건을 찾을 수 없습니다.')
            if existing and evidence_root_limit is not None:
                selected_scope = Path(existing['scope']).resolve(strict=True)
                if not selected_scope.is_relative_to(Path(evidence_root_limit)):
                    raise PermissionError('원격 접속에서는 설정된 공유 자료 폴더 안에서 선택해주세요.')
            if receipt is None:
                receipt = {'intent': intent, 'session_id': 'forsic-' + uuid.uuid4().hex, 'created_at': time.time()}
                write_json(receipt_path, receipt)
            session_id = receipt['session_id']
            intake = Intake(self.intake, tenant_scope=self.tenant_scope)
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
                intake.inspect(session_id, {'path': intent['path']}, allowed_root=evidence_root_limit)
            state = intake.state(session_id)
            manifest = json.loads(Path(state['manifest']).read_text())
            if selected is not None:
                # Repeatable writes recover either half of a partially persisted
                # choice. Registration keeps awaiting_question: only the next
                # native user message is permitted to dispatch an investigation.
                if manifest.get('onboarding') != selected:
                    manifest['onboarding'] = selected
                    write_json(state['manifest'], manifest)
                if state.get('onboarding') != selected:
                    state['onboarding'] = selected
                    write_json(intake.path(session_id), state)
            db = SessionDB(self.native_path)
            try:
                db.create_session(session_id=session_id, source='cli',
                                  model_config={'follow_profile_config': True},
                                  profile_name=self.tenant_scope['profile_name'] if self.tenant_scope else 'default')
            finally:
                db.close()
            result = {'case_id': manifest['case_id'], 'session_id': session_id}
            if self.tenant_scope:
                from urllib.parse import urlencode
                result.update(tenant_id=self.tenant_scope['tenant_id'], profile_name=self.tenant_scope['profile_name'])
                result['resume_url'] = '/chat?' + urlencode({'tenant_id': result['tenant_id'],
                    'profile': result['profile_name'], 'case': result['case_id'], 'resume': session_id})
            if selected is not None:
                result['onboarding'] = selected
            receipt['result'] = result
            write_json(receipt_path, receipt)
            return result
