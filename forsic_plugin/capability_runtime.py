"""Operator-only capability updates at Hermes' existing idle admission fence."""
from contextlib import ExitStack, closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from fastapi import HTTPException

from . import capability_policy as preferences


def revision(settings):
    return hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def served_home(request):
    """Accept only the served profile, even when the host scoped a plugin request."""
    from hermes_constants import get_hermes_home, get_routing_process_hermes_home, hermes_home_key
    from .tenants import current_scope
    tenant=current_scope()
    if tenant is not None:
        if (any(value and value != tenant['profile_name'] for value in request.query_params.getlist('profile'))
                or hermes_home_key(get_hermes_home())!=hermes_home_key(tenant['profile_home'])):
            raise HTTPException(403,'선택한 테넌트와 현재 프로필이 일치하지 않습니다.')
        return Path(tenant['profile_home']).resolve()
    served = getattr(request.app.state, 'initial_profile', '') or 'default'
    if (any(value and value != served for value in request.query_params.getlist('profile'))
            or hermes_home_key(get_hermes_home()) != hermes_home_key(get_routing_process_hermes_home())):
        raise HTTPException(403, '현재 제공 중인 프로필에서 설정해주세요.')
    return Path(get_hermes_home()).resolve()


def _durable_idle(home):
    """Read native leases without creating a database or modifying its journals."""
    path = home / 'state.db'
    if not path.exists():
        return True
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0.1)) as db:
        db.execute('PRAGMA query_only=ON')
        return not any(db.execute('SELECT 1 FROM ' + table + ' WHERE expires_at >= ? LIMIT 1',
                                  (time.time(),)).fetchone()
                       for table in ('session_turn_leases', 'compression_locks'))


def _live_sessions(home):
    from hermes_constants import get_process_hermes_home, hermes_home_key
    from tui_gateway import server
    with server._sessions_lock:
        return [(sid, session) for sid, session in server._sessions.items()
                if hermes_home_key(session.get('profile_home') or get_process_hermes_home()) == hermes_home_key(home)]


def _busy(home):
    from hermes_cli.web_server_idle_proof import idle_proof
    try:
        return idle_proof()['idle'] is not True or not _durable_idle(home)
    except Exception:
        return True


def _pin(home, session_id):
    path = home / 'state.db'
    if not path.is_file() or not session_id:
        return None
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0.1)) as db:
        db.execute('PRAGMA query_only=ON')
        row = db.execute('SELECT COALESCE(p.prompt,s.tool_names) FROM sessions s '
                         'LEFT JOIN system_prompts p ON p.hash=s.tool_names WHERE s.id=?',
                         (session_id,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def _verify_session(home, session):
    """Compare native rebuilt schemas and the actual durable pin, not refresh's return."""
    agent = session.get('agent')
    if agent is None:
        return True  # Lazy native construction will read the saved policy.
    from model_tools import get_tool_definitions
    from tui_gateway import server
    with server._session_profile_runtime_scope(session):
        expected = get_tool_definitions(enabled_toolsets=server._load_enabled_toolsets(getattr(agent, 'platform', None)),
                                        disabled_toolsets=server._load_disabled_toolsets(), quiet_mode=True, session_id=getattr(agent, 'session_id', ''))
    current = list(getattr(agent, 'tools', None) or [])
    name = lambda tool: (tool.get('function') or {}).get('name', '')
    relevant = lambda tools: {name(tool): tool for tool in tools
                              if name(tool).startswith('forsic_') or name(tool) == 'skills'}
    if relevant(current) != relevant(expected):
        return False
    if set(getattr(agent, 'valid_tool_names', ())) != {name(tool) for tool in current}:
        return False
    pin = _pin(home, getattr(agent, 'session_id', None))
    return isinstance(pin, dict) and pin.get('tools') == current


def _applied(home, sessions):
    try:
        return all(_verify_session(home, session) for _, session in sessions)
    except Exception:
        return False


def snapshot(request, *, cfg=None, applied=None, work_active=None):
    from hermes_cli.config import require_readable_config_before_write
    from .setup import local_admin
    home = served_home(request)
    result = preferences.public(cfg if cfg is not None else require_readable_config_before_write())
    busy = _busy(home) if work_active is None else work_active
    if applied is None:
        # Reads never rebuild tools, touch session state, or claim an idle boundary.
        applied = None if busy else _applied(home, _live_sessions(home))
    result.update(local_admin=local_admin(request), native_work_active=busy,
                  revision=revision(result['settings']), applied=applied is True,
                  apply_status='unverified' if applied is None else ('applied' if applied else 'unapplied'),
                  pending_runtime_update=applied is not True)
    return result


def save_and_apply(values, request, expected_revision=None):
    from hermes_cli.backend_retirement import retirement
    from hermes_cli.config import require_readable_config_before_write
    from .setup import local_admin
    if not local_admin(request):
        raise HTTPException(403, '이 컴퓨터에서 localhost 주소로 접속해 설정해주세요.')
    home = served_home(request)
    normalized = preferences.normalize(values)
    # Reservation is native and process-wide: a turn cannot pass its admission
    # check between this idle proof and publication of its new tool snapshot.
    with retirement.idle_boundary() as idle:
        if not idle:
            raise HTTPException(409, '진행 중인 조사 작업이 끝난 뒤 설정을 적용해주세요.')
        try:
            durable_idle = _durable_idle(home)
        except Exception:
            durable_idle = False
        if not durable_idle:
            raise HTTPException(409, '대화의 실행·압축 상태를 확인한 뒤 다시 적용해주세요.')
        cfg = require_readable_config_before_write()
        current = preferences.policy_from_config(cfg).settings
        changed = normalized != current
        if (expected_revision is not None and expected_revision != revision(current)) or (changed and expected_revision is None):
            raise HTTPException(409, '설정이 변경되었어요. 새로고침 후 다시 적용해주세요.')
        sessions = _live_sessions(home)
        with ExitStack() as stack:
            for _, session in sessions:
                lock = session.get('history_lock')
                if lock is None or not lock.acquire(blocking=False):
                    raise HTTPException(409, '대화가 사용 중이에요. 잠시 뒤 다시 적용해주세요.')
                stack.callback(lock.release)
            if changed:
                preferences.save(normalized)
            # Retry a saved but unapplied policy even when preferences are unchanged.
            applied = _applied(home, sessions)
            if not applied:
                from tui_gateway import server
                try:
                    server._refresh_live_sessions(home)
                except Exception:
                    pass  # Saved preferences remain visible as pending until verified.
                applied = _applied(home, sessions)
            return snapshot(request, applied=applied, work_active=False)
