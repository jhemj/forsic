"""Opt-in LAN product boundary, fixed at startup; local Hermes stays unchanged.

Remote users select new evidence only through the guarded Forsic picker. Generic
host administration, file/terminal access and unbound native agent creation are
not part of that product surface. This is not another authentication scheme.
"""
from copy import deepcopy
from contextlib import closing
import asyncio
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs

DENIED = '원격 접속에서는 조사 화면과 연결된 사건만 사용할 수 있습니다. 자료는 공유 폴더 선택 화면에서 접수해주세요.'


def is_loopback(peer):
    try:
        address = ipaddress.ip_address(peer)
        return (address.ipv4_mapped or address).is_loopback if isinstance(address, ipaddress.IPv6Address) else address.is_loopback
    except (TypeError, ValueError):
        return False


def require_bound_native_intake(intake, session_id, restricted_mode):
    """The native tool has no trusted HTTP peer. Opt-in deployments use UI intake.

    Existing bindings still permit the native tool's normal reused-scope result.
    Local UI intake remains unrestricted by the HTTP peer policy.
    """
    if restricted_mode and not intake.state(session_id).get('manifest'):
        raise PermissionError('공유 자료 제한 모드에서는 새 사건을 자료 선택 화면에서 접수해주세요.')


class ForsicRemotePolicy:
    def __init__(self, settings, home, profile='default'):
        settings = deepcopy(settings)
        self.enabled = settings.get('remote_evidence_directory_only', False)
        self.online = settings.get('online_hosting', False)
        self.setup_required = settings.get('setup_completed', True) is False
        if not isinstance(self.enabled, bool):
            raise ValueError('remote_evidence_directory_only must be a boolean')
        self.home = Path(home).resolve()
        self.profile = profile
        self.intake = Path(settings.get('intake_root') or Path(__file__).resolve().parents[1] / 'state/intake').resolve()

    def restricted(self, scope):
        peer = (scope.get('client') or ('', 0))[0]
        if self.online:
            from starlette.requests import Request
            from .setup import local_admin
            return self.enabled and not local_admin(Request({**scope, 'type': 'http'}))
        return self.enabled and not is_loopback(peer)

    def profile_allowed(self, value):
        return value is None or value in ('', self.profile)

    def bound(self, session_id, *, runtime=False):
        if not isinstance(session_id, str) or not session_id or len(session_id) > 4096:
            return False
        if runtime:
            from tui_gateway import server
            with server._sessions_lock:
                session = server._sessions.get(session_id)
                if not session:
                    return False
                home = session.get('profile_home')
                if home is None and session.get('profile_name', self.profile) != self.profile:
                    return False
                if home is not None and Path(home).resolve() != self.home:
                    return False
                session_id = session.get('session_key')
                if not isinstance(session_id, str) or not session_id:
                    return False
        try:
            from .investigation_context import _compression_link
            native = self.home / 'state.db'
            with closing(sqlite3.connect(native.as_uri()+'?mode=ro', uri=True)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                seen = set()
                for _ in range(128):
                    if session_id in seen:
                        return False
                    seen.add(session_id)
                    row = db.execute('SELECT id,parent_session_id,end_reason,source,model_config FROM sessions WHERE id=?',
                                     (session_id,)).fetchone()
                    if row is None:
                        return False  # Stale intake files are not native sessions.
                    binding = self.intake / (hashlib.sha256(session_id.encode()).hexdigest()+'.json')
                    if binding.is_file():
                        state = json.loads(binding.read_text())
                        if not isinstance(state, dict):
                            return False
                        manifest = Path(state['manifest']).resolve(strict=True)
                        if not manifest.is_relative_to(self.intake / 'cases'):
                            return False
                        config = json.loads(manifest.read_text())
                        return isinstance(config, dict) and isinstance(config.get('case_id'), str) and bool(config['case_id'])
                    parent_id = row['parent_session_id']
                    if not isinstance(parent_id, str) or not parent_id:
                        return False
                    parent = db.execute('SELECT id,parent_session_id,end_reason,source,model_config FROM sessions WHERE id=?',
                                        (parent_id,)).fetchone()
                    # Inherit only native compression continuation, never a
                    # branch/reset/delegation. Reuse the existing scope contract.
                    if parent is None or not _compression_link(dict(row), dict(parent)):
                        return False
                    session_id = parent_id
                return False  # Bound traversal, including corrupted long chains.
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            return False

    def http_allowed(self, scope):
        path, method = scope.get('path', ''), scope.get('method', 'GET')
        query = parse_qs(scope.get('query_string', b'').decode('utf-8', 'replace'), keep_blank_values=True)
        if any(not self.profile_allowed(profile) for profile in query.get('profile', [])):
            return False
        if method == 'GET' and re.fullmatch(
                r'/api/plugins/forsic/(?:skills-ui-copy|review-chat|evidence-collection(?:/exports/[^/]+(?:/download)?)?)', path):
            return True  # Ordinary authentication and endpoint ownership checks still run.
        if method in ('GET', 'HEAD'):
            if path in ('/login', '/auth/login', '/auth/callback', '/api/plugins/forsic/setup', '/api/plugins/forsic/capabilities'):
                return True
            if path in ('/', '/forsic', '/chat', '/sessions', '/favicon.ico') or path.startswith('/assets/') or re.fullmatch(r'/(?:fonts|fonts-terminal)/[^/]+\.(?:woff2?|ttf|otf)', path):
                return True
            if path in ('/api/auth/me', '/api/auth/providers', '/api/dashboard/plugins',
                        '/api/dashboard/themes', '/api/dashboard/font', '/api/tools/toolsets'):
                return True
            if path in ('/api/profiles', '/api/profiles/active'):
                return True  # Middleware supplies only the served profile, not host configuration.
            if re.fullmatch(r'/dashboard-plugins/forsic/dist/(?:index\.js|style\.css|rail\.css|skills\.json)', path):
                return True
            match = re.fullmatch(r'/api/sessions/([^/]+)/(?:live|messages|latest-descendant|timeline|messages/around)', path)
            if match:
                return self.bound(match[1])
            return bool(re.fullmatch(r'/api/plugins/forsic/(?:investigations(?:/[^/]+/conversations)?|evidence-sources|evidence-browser|evidence-connection|status|evidence/[^/]+|indicators|indicator-export/[^/]+|reports/[^/]+|report-text/[^/]+|download/[^/]+|report-driven|report-bundles/[^/]+/[^/]+|investigation-view|source-page|library(?:/[^/]+(?:/sources/[^/]+)?)?)', path))
        return method == 'POST' and (path in ('/api/auth/ws-ticket', '/auth/password-login', '/auth/logout') or bool(re.fullmatch(
            r'/api/plugins/forsic/(?:investigations(?:/[^/]+/(?:conversations|close))?|investigation-enrich|library|evidence-collection/exports|report-bundles/[a-f0-9]{64}/word)', path)))

    def rpc_allowed(self, request):
        if not isinstance(request, dict):
            return False
        method = request.get('method')
        if method is None:
            # Native server_requests.resolve_response still checks the owning transport.
            return isinstance(request.get('id'), str) and request['id'].startswith('srq-') and ('result' in request or 'error' in request)
        if not isinstance(method, str):
            return False
        params = request.get('params', {})
        if not isinstance(params, dict) or not self.profile_allowed(params.get('profile')):
            return False
        if method in ('gateway.ping', 'client.capabilities'):
            return True
        if method == 'session.resume':
            return set(params) <= {'session_id', 'profile', 'omit_messages', 'inline_images', 'close_on_disconnect'} and self.bound(params.get('session_id'))
        allowed = {'session.activate', 'session.control.read', 'session.control', 'session.interrupt',
                   'session.events.since', 'session.events.stats', 'prompt.submit', 'approval.respond',
                   'command.dispatch'}
        if method not in allowed or not self.bound(params.get('session_id'), runtime=True):
            return False
        if method == 'prompt.submit' and (not isinstance(params.get('text'), str) or set(params) - {'session_id', 'profile', 'text', 'display_kind'}):
            return False
        if method == 'command.dispatch' and params.get('name') != 'goal':
            return False
        if method == 'session.control' and params.get('action') not in ('goal.pause', 'goal.resume'):
            return False
        return True


class ForsicRemoteAccessMiddleware:
    def __init__(self, app, *, policy):
        self.app, self.policy = app, policy

    async def _json(self, send, status, body, headers=()):
        payload = json.dumps(body, ensure_ascii=False).encode()
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(key, value) for key, value in headers if key.lower() not in (b'content-type', b'content-length', b'etag')] + [(b'content-type', b'application/json; charset=utf-8'), (b'content-length', str(len(payload)).encode())]})
        await send({'type': 'http.response.body', 'body': payload})

    async def __call__(self, scope, receive, send):
        if self.policy.setup_required and scope.get('type') == 'http' and scope.get('method') not in ('GET', 'HEAD', 'OPTIONS') and not scope.get('path', '').startswith(('/api/plugins/forsic/setup', '/auth/', '/api/auth/')):
            return await self._json(send, 409, {'detail': '초기 설정을 저장한 뒤 포식이를 다시 시작해주세요.'})
        if self.policy.setup_required and scope.get('type') == 'websocket':
            return await send({'type': 'websocket.close', 'code': 4409, 'reason': 'Complete setup and restart Forsic first'})
        if scope.get('type') not in ('http', 'websocket') or not self.policy.restricted(scope):
            return await self.app(scope, receive, send)
        if scope['type'] == 'http':
            if not await asyncio.to_thread(self.policy.http_allowed, scope):
                return await self._json(send, 403, {'detail': DENIED})
            if scope['path'] in ('/api/profiles', '/api/profiles/active', '/api/dashboard/plugins'):
                # Run the ordinary auth/router first. Projection never turns an
                # existing 401/403 into an unauthenticated success response.
                messages = []
                async def capture(message):
                    messages.append(message)
                await self.app(scope, receive, capture)
                start = next((message for message in messages if message['type'] == 'http.response.start'), {})
                if start.get('status') == 200:
                    if scope['path'] == '/api/dashboard/plugins':
                        try:
                            values = json.loads(b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body'))
                            body = [p for p in values if isinstance(p, dict) and p.get('name') == 'forsic'] if isinstance(values, list) else []
                        except (ValueError, TypeError):
                            return await self._json(send, 503, {'detail': '조사 화면 구성 정보를 확인할 수 없습니다.'})
                    else:
                        body = {'profiles': [{'name': self.policy.profile}]} if scope['path'] == '/api/profiles' else {'active': self.policy.profile, 'current': self.policy.profile}
                    return await self._json(send, 200, body, start.get('headers', ()))
                for message in messages:
                    await send(message)
                return
            return await self.app(scope, receive, send)
        if scope.get('path') != '/api/ws':
            return await send({'type': 'websocket.close', 'code': 4403, 'reason': 'Forsic remote product access only'})

        async def guarded_receive():
            while True:
                event = await receive()
                if event['type'] != 'websocket.receive':
                    return event
                try:
                    request = json.loads(event.get('text') or event.get('bytes') or '')
                except (ValueError, TypeError, UnicodeError):
                    return event  # Native transport returns its ordinary parse error; no method runs.
                if await asyncio.to_thread(self.policy.rpc_allowed, request):
                    return event
                await send({'type': 'websocket.send', 'text': json.dumps({'jsonrpc': '2.0',
                            'id': request.get('id') if isinstance(request, dict) else None,
                            'error': {'code': 4030, 'message': DENIED}}, ensure_ascii=False)})
        return await self.app(scope, guarded_receive, send)


def install_remote_access(app, *, settings, home, profile='default'):
    """Call once at dashboard assembly, before serving; never reload per request."""
    policy = ForsicRemotePolicy(settings, home, profile)
    app.add_middleware(ForsicRemoteAccessMiddleware, policy=policy)
    return policy
