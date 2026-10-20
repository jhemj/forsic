"""First-run preferences staged for the next explicit dashboard launch.

Uses native configuration, secret storage, basic login and LAN listeners. Reads
and saves never start an agent or contact a model endpoint.
"""
import ipaddress
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from . import connections

LLM_PENDING_KEY = 'FORSIC_PENDING_LLM_API_KEY'
LLM_KEY = 'FORSIC_LLM_API_KEY'
PASSWORD_PENDING = 'FORSIC_PENDING_DASHBOARD_PASSWORD_HASH'
PRIVATE = tuple(ipaddress.ip_network(v) for v in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))


def settings(cfg):
    return cfg.setdefault('plugins', {}).setdefault('entries', {}).setdefault('forsic', {}).setdefault('settings', {})


def local_admin(request):
    """Require direct loopback transport AND loopback browser authority.

    No forwarding header can elevate a reverse-proxy request into local
    administration, even when the actual proxy connects over loopback.
    """
    from .remote_policy import is_loopback
    if request is None or not request.client or not is_loopback(request.client.host):
        return False
    if any(k.lower() == 'forwarded' or k.lower().startswith('x-forwarded-') or k.lower() == 'x-real-ip' for k in request.headers):
        return False
    try:
        host = urlsplit('//'+request.headers.get('host', ''))
        if host.hostname not in ('localhost', '127.0.0.1', '::1') or host.username or host.password or host.path or host.query or host.fragment:
            return False
        host.port
        origin = request.headers.get('origin')
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ('http', 'https') or parsed.netloc.lower() != host.netloc.lower() or parsed.path or parsed.query or parsed.fragment:
                return False
    except ValueError:
        return False
    # Profile switching must not turn local administration into cross-profile
    # configuration writes on a multiplexed host.
    profiles = request.query_params.getlist('profile')
    served = getattr(getattr(request.scope.get('app'), 'state', None), 'initial_profile', 'default') or 'default'
    scope = None
    # request_scope explicitly admits the unselected host setup/tenant gate.
    # Do not reinterpret that validated None as an unknown active tenant.
    if not hasattr(request.state, 'forsic_tenant_scope') or request.state.forsic_tenant_scope is not None:
        from .tenants import current_scope
        scope = current_scope()
    if scope is not None:
        served = scope['profile_name']
    if any(profile and profile != served for profile in profiles):
        return False
    return True


def hosting(cfg):
    dash = cfg.get('dashboard') or {}
    mode = 'private_lan' if dash.get('trusted_private_lan_no_login') else 'online' if dash.get('public_url') else 'local'
    return {'mode': mode, 'private_lan_host': dash.get('private_lan_host', ''), 'public_url': dash.get('public_url', '')}


def public(request):
    cfg = connections.config()
    opts = settings(cfg)
    current = hosting(cfg)
    completed = opts.get('setup_completed', True) is not False
    if not local_admin(request):
        return {'local_admin': False, 'required': False, 'completed': True, 'pending': False, 'restart_required': False,
                'hosting_current': {'mode': current['mode']},
                'llm_configured': bool(cfg.get('model', {}).get('default')), 'evidence_configured': bool(opts.get('evidence_directory'))}
    pending = opts.get('setup_pending') or {}
    active_llm = connections.model_connection(cfg.get('model') or {})
    llm = {**active_llm, **(connections.preferences(cfg).get('llm_pending') or {})}
    values = {'llm_base_url': llm['base_url'], 'llm_model': llm['model'], 'llm_context_length': llm['context_length'],
              'evidence_directory': opts.get('evidence_directory', ''), 'evidence_directory_label': opts.get('evidence_directory_label', ''),
              'hosting_mode': current['mode'], 'private_lan_host': current['private_lan_host'], 'public_url': current['public_url'],
              'login_username': (cfg.get('dashboard', {}).get('basic_auth') or {}).get('username', '')}
    values.update({k: v for k, v in pending.items() if k in values})
    restart = bool(pending or connections.preferences(cfg).get('llm_pending'))
    key_action = pending.get('llm_key_action')
    return {'local_admin': True, 'required': not completed and not bool(pending), 'completed': completed,
            'pending': restart, 'restart_required': restart, 'values': values, 'hosting_current': current,
            'llm_key_configured': key_action == 'replace' or (key_action != 'clear' and connections.has_model_key(cfg)),
            'login_configured': bool(pending.get('password_pending') or login_configured(cfg))}


def login_configured(cfg):
    basic = cfg.get('dashboard', {}).get('basic_auth') or {}
    return bool((basic.get('username') or connections.key('HERMES_DASHBOARD_BASIC_AUTH_USERNAME')) and
                (basic.get('password_hash') or basic.get('password') or connections.key('HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH') or connections.key('HERMES_DASHBOARD_BASIC_AUTH_PASSWORD')))


def folder(value, cfg):
    from .onboarding import EvidenceBrowser
    from hermes_cli.config import get_hermes_home
    if not isinstance(value, str) or len(value) > 4096 or '\x00' in value:
        raise ValueError('자료 폴더의 전체 경로를 확인해주세요.')
    value = value.strip()
    if not value:
        return ''
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('자료 폴더의 전체 경로를 입력해주세요.')
    try:
        browser = EvidenceBrowser(get_hermes_home(), settings(cfg).get('intake_root') or Path(__file__).resolve().parents[1] / 'state/intake')
        info = browser.connection(str(path))
        if info['kind'] != 'directory' or not os.access(path, os.R_OK | os.X_OK):
            raise ValueError('접근 가능한 자료 폴더를 선택해주세요.')
    except (OSError, PermissionError):
        raise ValueError('접근 가능한 자료 폴더를 선택해주세요.') from None
    return str(path)


def validate(body, cfg):
    llm = {'base_url': connections.endpoint(body['llm_base_url']), 'model': body['llm_model'].strip(),
           'context_length': body.get('llm_context_length')}
    if not llm['model'] or len(llm['model']) > 200 or any(ord(c) < 32 for c in llm['model']):
        raise ValueError('사용할 LLM 모델 이름을 입력해주세요.')
    context = llm['context_length']
    if context is not None and (type(context) is not int or not 64000 <= context <= 2147483647):
        raise ValueError('문맥 길이는 64,000 이상의 정수 또는 모델 기본값으로 지정해주세요.')
    choice = dict(body)
    prior = settings(cfg).get('setup_pending') or {}
    prior_url = prior.get('llm_key_base_url') or (connections.preferences(cfg).get('llm_pending') or {}).get('base_url')
    if (prior.get('llm_key_action') == 'replace' and prior_url != llm['base_url']
            and not body.get('llm_api_key') and not body.get('clear_llm_key')):
        raise ValueError('LLM 서버 주소를 바꾸려면 새 API 키를 입력하거나 기존 키 삭제를 선택해주세요.')
    if llm['base_url'] == prior_url:
        if prior.get('llm_key_action') == 'replace' and connections.key(LLM_PENDING_KEY):
            choice['llm_api_key'] = connections.key(LLM_PENDING_KEY)
        elif prior.get('llm_key_action') == 'clear':
            choice['clear_llm_key'] = True
    connections.require_endpoint_key_choice(cfg, llm['base_url'], choice)
    evidence = folder(body['evidence_directory'], cfg)
    label = body.get('evidence_directory_label', '').strip()
    username = body.get('login_username', '').strip()
    for value, maximum in ((label, 256), (username, 128)):
        if len(value) > maximum or any(ord(c) < 32 for c in value):
            raise ValueError('폴더 표시 이름과 로그인 아이디를 확인해주세요.')
    mode = body['hosting_mode']
    lan, url = body.get('private_lan_host', '').strip(), body.get('public_url', '').strip().rstrip('/')
    if mode not in ('local', 'private_lan', 'online'):
        raise ValueError('접속 방식을 선택해주세요.')
    if mode != 'local' and not evidence:
        raise ValueError('공유할 자료 폴더를 먼저 선택해주세요.')
    if mode == 'private_lan':
        try:
            address = ipaddress.ip_address(lan)
            if '%' in lan or not any(address in network for network in PRIVATE):
                raise ValueError()
            lan = str(address)
        except ValueError:
            raise ValueError('사설망에서 사용할 이 컴퓨터의 IP 주소를 입력해주세요.') from None
    else:
        lan = ''
    if mode == 'online':
        url = connections.endpoint(url)
        parsed = urlsplit(url)
        try:
            is_public = ipaddress.ip_address(parsed.hostname).is_global
        except ValueError:
            is_public = parsed.hostname not in ('localhost',) and '.' in parsed.hostname
        if parsed.scheme != 'https' or parsed.path not in ('', '/') or not is_public:
            raise ValueError('HTTPS 공개 주소를 경로 없이 입력해주세요. TLS 프록시가 먼저 준비되어야 해요.')
        if not username or not (body.get('login_password') or login_configured(cfg) or settings(cfg).get('setup_pending', {}).get('password_pending')):
            raise ValueError('온라인 접속에 사용할 로그인 아이디와 비밀번호를 입력해주세요.')
    else:
        url = ''
    key = body.get('llm_api_key', '')
    password = body.get('login_password', '')
    if not isinstance(key, str) or len(key) > 8192 or any(ord(c) < 32 or ord(c) > 126 for c in key) or key and body.get('clear_llm_key'):
        raise ValueError('LLM API 키 형식을 확인해주세요. 교체와 삭제는 동시에 할 수 없어요.')
    if password and (len(password) < 12 or len(password) > 1024 or '\x00' in password):
        raise ValueError('로그인 비밀번호는 12자 이상으로 입력해주세요.')
    return llm, {'evidence_directory': evidence, 'evidence_directory_label': label, 'hosting_mode': mode,
                 'private_lan_host': lan, 'public_url': url, 'login_username': username}


def save(body, request):
    from hermes_cli.config import save_config, save_env_value, get_env_path
    with connections._lock:
        cfg = connections.config()
        llm, pending = validate(body, cfg)
        old = settings(cfg).get('setup_pending') or {}
        for field in ('llm_key_action', 'llm_key_base_url', 'password_pending'):
            if field in old:
                pending[field] = old[field]
        # Secrets have separate pending slots: editing settings never rotates a
        # key used by a live turn or the native login provider.
        if body.get('llm_api_key'):
            save_env_value(LLM_PENDING_KEY, body['llm_api_key'])
            pending['llm_key_action'] = 'replace'
            pending['llm_key_base_url'] = llm['base_url']
        elif body.get('clear_llm_key'):
            pending['llm_key_action'] = 'clear'
            pending.pop('llm_key_base_url', None)
        if body.get('login_password'):
            from plugins.dashboard_auth.basic import hash_password
            save_env_value(PASSWORD_PENDING, hash_password(body['login_password']))
            pending['password_pending'] = True
        if body.get('llm_api_key') or body.get('login_password'):
            get_env_path().chmod(0o600)
        settings(cfg)['setup_pending'] = pending
        prefs = settings(cfg).setdefault('connections', {})
        prefs['llm_pending'] = llm
        save_config(cfg)
    return public(request)


def validate_pending_setup():
    cfg = connections.config()
    pending = settings(cfg).get('setup_pending')
    if not pending:
        return
    llm = {**connections.model_connection(cfg.get('model', {})), **(connections.preferences(cfg).get('llm_pending') or {})}
    validate({**pending, 'llm_base_url': llm['base_url'], 'llm_model': llm['model'],
              'llm_context_length': llm['context_length'],
              'clear_llm_key': pending.get('llm_key_action') == 'clear',
              'llm_api_key': connections.key(LLM_PENDING_KEY) if pending.get('llm_key_action') == 'replace' else ''}, cfg)
    if pending.get('llm_key_action') == 'replace' and pending.get('llm_key_base_url') != llm['base_url']:
        raise ValueError('대기 중인 LLM API 키와 서버 주소가 일치하지 않습니다.')
    if pending.get('llm_key_action') == 'replace' and not connections.key(LLM_PENDING_KEY):
        raise ValueError('대기 중인 LLM API 키를 확인할 수 없습니다.')
    if pending.get('password_pending') and not connections.key(PASSWORD_PENDING):
        raise ValueError('대기 중인 로그인 비밀번호를 확인할 수 없습니다.')


def apply_pending_setup():
    """Explicit launcher only; no server route invokes this function."""
    from hermes_cli.config import save_config, save_env_value, remove_env_value, get_env_path
    with connections._lock:
        cfg = connections.config()
        opts = settings(cfg)
        pending = opts.get('setup_pending')
        if not pending:
            return
        # Recheck directories/IP/TLS after time has passed before exposing them.
        values = {**pending, 'llm_base_url': cfg.get('model', {}).get('base_url', ''),
                  'llm_model': cfg.get('model', {}).get('default', ''),
                  'llm_context_length': cfg.get('model', {}).get('context_length')}
        _, verified = validate(values, cfg)
        action = pending.get('llm_key_action')
        if action == 'replace':
            if pending.get('llm_key_base_url') != cfg.get('model', {}).get('base_url'):
                raise ValueError('대기 중인 LLM API 키와 서버 주소가 일치하지 않습니다.')
            value = connections.key(LLM_PENDING_KEY)
            if not value:
                raise ValueError('대기 중인 LLM API 키를 확인할 수 없습니다.')
            save_env_value(LLM_KEY, value)
            cfg.setdefault('model', {})['key_env'] = LLM_KEY
            cfg['model'].pop('api_key', None)
            cfg['model'].pop('api', None)
            cfg['model'].pop('api_key_env', None)
        elif action == 'clear':
            remove_env_value(LLM_KEY)
            cfg.setdefault('model', {}).pop('key_env', None)
            cfg['model'].pop('api_key', None)
            cfg['model'].pop('api', None)
            cfg['model'].pop('api_key_env', None)
        if action in ('replace', 'clear'):
            for role in cfg.get('auxiliary', {}).values():
                if isinstance(role, dict) and role.get('provider') == 'custom' and role.get('base_url') == cfg.get('model', {}).get('base_url'):
                    role.pop('api_key', None)
                    role.pop('api_key_env', None)
                    if action == 'replace':
                        role['key_env'] = LLM_KEY
                    else:
                        role.pop('key_env', None)
        if pending.get('password_pending'):
            value = connections.key(PASSWORD_PENDING)
            if not value:
                raise ValueError('대기 중인 로그인 비밀번호를 확인할 수 없습니다.')
            save_env_value('HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH', value)
            remove_env_value('HERMES_DASHBOARD_BASIC_AUTH_PASSWORD')
        dash = cfg.setdefault('dashboard', {})
        if verified['hosting_mode'] == 'online':
            dash.setdefault('basic_auth', {})['username'] = verified['login_username']
            from hermes_cli.plugins_cmd import ensure_basic_auth_plugin_enabled_in_config
            ensure_basic_auth_plugin_enabled_in_config(cfg)
            save_env_value('HERMES_DASHBOARD_BASIC_AUTH_USERNAME', verified['login_username'])
            if not connections.key('HERMES_DASHBOARD_BASIC_AUTH_SECRET'):
                save_env_value('HERMES_DASHBOARD_BASIC_AUTH_SECRET', secrets.token_urlsafe(32))
        dash.update(trusted_private_lan_no_login=verified['hosting_mode'] == 'private_lan',
                    private_lan_host=verified['private_lan_host'], public_url=verified['public_url'])
        for field in ('evidence_directory', 'evidence_directory_label'):
            opts[field] = verified[field]
        opts['remote_evidence_directory_only'] = verified['hosting_mode'] != 'local'
        # Online traffic is restricted even through a local reverse proxy.
        opts['online_hosting'] = verified['hosting_mode'] == 'online'
        opts['setup_completed'] = True
        opts.pop('setup_pending')
        save_config(cfg)
        for name in (LLM_PENDING_KEY, PASSWORD_PENDING):
            remove_env_value(name)
        if get_env_path().exists():
            get_env_path().chmod(0o600)


def check_model(body):
    """Explicit, bounded catalog request; no prompt, evidence or generation."""
    import httpx
    url = connections.endpoint(body['llm_base_url'])
    model = body['llm_model'].strip()
    key = body.get('llm_api_key', '')
    if len(key) > 8192 or any(ord(c) < 32 or ord(c) > 126 for c in key):
        raise ValueError('LLM API 키 형식을 확인해주세요.')
    if not key:
        cfg = connections.config()
        pending = settings(cfg).get('setup_pending') or {}
        selected = connections.preferences(cfg).get('llm_pending') or {}
        # Keys are bound to their exact endpoint; a staged URL without a new
        # key must never receive the active connection's existing secret.
        if pending.get('llm_key_action') == 'replace' and url == selected.get('base_url') and url == pending.get('llm_key_base_url'):
            key = connections.key(LLM_PENDING_KEY)
        elif pending.get('llm_key_action') != 'clear' and url == cfg.get('model', {}).get('base_url'):
            key = connections.key(LLM_KEY)
    headers = {'Authorization': 'Bearer '+key} if key else {}
    try:
        deadline = time.monotonic() + 6
        with httpx.Client(timeout=httpx.Timeout(3), follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', url+'/models', headers=headers) as response:
                if response.status_code != 200:
                    return {'reachable': False, 'model_found': False, 'models': [], 'detail': '모델 목록을 읽지 못했어요. 주소와 인증 정보를 확인해주세요.'}
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 262144 or time.monotonic() > deadline:
                        raise ValueError()
        catalog = json.loads(raw)
        models = [entry['id'] for entry in catalog.get('data', []) if isinstance(entry, dict) and isinstance(entry.get('id'), str) and len(entry['id']) <= 200 and (not key or key not in entry['id']) and not any(ord(c) < 32 for c in entry['id'])][:100]
        return {'reachable': True, 'model_found': model in models, 'models': models, 'detail': '모델 목록을 확인했어요. 실제 추론은 실행하지 않았어요.'}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return {'reachable': False, 'model_found': False, 'models': [], 'detail': '모델 목록을 확인하지 못했어요. 주소와 서버 상태를 확인해주세요.'}
