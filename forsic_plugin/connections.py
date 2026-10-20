"""Small connection preferences over Hermes config/secret storage; no model calls."""
from urllib.parse import urlsplit
import threading
import re

VT_URL = 'https://www.virustotal.com/api/v3'
_lock = threading.RLock()


def config():
    from hermes_cli.config import require_readable_config_before_write
    return require_readable_config_before_write()


def preferences(cfg=None):
    cfg = config() if cfg is None else cfg
    return cfg.get('plugins', {}).get('entries', {}).get('forsic', {}).get('settings', {}).get('connections', {})


def key(name='VT_APIKEY'):
    # Read only this credential; never return it through UI, tool output, or logs.
    from hermes_cli.config import load_env
    return load_env().get(name, '')


def endpoint(value, optional=False):
    value = value.strip().rstrip('/')
    if not value and optional:
        return ''
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('API 주소는 인증정보·쿼리 없이 http:// 또는 https://로 입력해주세요.')
    try:
        parsed.port
    except ValueError:
        raise ValueError('API 주소의 포트를 확인해주세요.') from None
    return value


def has_model_key(cfg):
    model = cfg.get('model') or {}
    direct = model.get('api_key') or model.get('api')
    return bool((direct and direct not in ('ollama', 'no-key-required')) or
                model.get('key_env') or model.get('api_key_env'))


def require_endpoint_key_choice(cfg, url, body):
    if (url != (cfg.get('model') or {}).get('base_url') and has_model_key(cfg)
            and not body.get('llm_api_key') and not body.get('clear_llm_key')):
        raise ValueError('LLM 서버 주소를 바꾸려면 초기 설정에서 새 API 키를 입력하거나 기존 키 삭제를 선택해주세요.')


def telegram_group(value):
    """Normalize a destination without resolving it or sending any Telegram request."""
    value = str(value or '').strip()
    if not value:
        return ''
    if re.fullmatch(r'-[1-9][0-9]{0,19}', value):
        return value
    if value.startswith(('t.me/', 'telegram.me/')):
        value = 'https://' + value
    if '://' in value:
        parsed = urlsplit(value)
        if parsed.hostname in ('t.me', 'telegram.me') and (
                parsed.path.startswith('/+') or parsed.path.startswith('/joinchat/')):
            raise ValueError('비공개 초대 링크는 그룹 ID가 아니에요. -100으로 시작하는 그룹 ID를 입력해주세요.')
        if (parsed.scheme not in ('http', 'https') or parsed.netloc not in ('t.me', 'telegram.me')
                or parsed.query or parsed.fragment):
            raise ValueError('텔레그램 그룹 ID 또는 공개 그룹의 @아이디·t.me/아이디 주소를 입력해주세요.')
        value = '@' + parsed.path.strip('/')
    if re.fullmatch(r'@[A-Za-z][A-Za-z0-9_]{4,31}', value):
        return value.lower()
    raise ValueError('텔레그램 그룹 ID 또는 공개 그룹의 @아이디·t.me/아이디 주소를 입력해주세요. 초대·메시지 링크는 사용할 수 없어요.')


def model_connection(model):
    # ollama_num_ctx also represents an explicit runtime override.
    return {'base_url': model.get('base_url', ''), 'model': model.get('default', ''),
            'context_length': model.get('context_length') or model.get('ollama_num_ctx') or None}


def context_metadata(llm):
    """Read native metadata without a provider request or a fabricated default.

    Native caches merge runtime windows, training maxima and fallbacks. Their
    scalar value is useful as a slider hint, never evidence of a model default.
    """
    from agent.model_metadata import MINIMUM_CONTEXT_LENGTH, get_cached_context_length
    cached = get_cached_context_length(llm['model'], llm['base_url'])
    return {'model': llm['model'], 'base_url': llm['base_url'],
            'minimum_context_length': MINIMUM_CONTEXT_LENGTH,
            'default_context_length': None,
            'reported_context_length': cached if type(cached) is int and cached > 0 else None}


def public():
    cfg = config()
    prefs = preferences(cfg)
    model = cfg.get('model', {})
    active = model_connection(model)
    selected = {**active, **(prefs.get('llm_pending') or {})}
    return {'llm': selected, 'llm_current': active, 'llm_context': context_metadata(selected),
            'llm_pending': bool(prefs.get('llm_pending')),
            'jev': {'base_url': prefs.get('jev_base_url', ''), 'enabled': False},
            'telegram': {'assistant_key_configured': bool(key('TELEGRAM_BOT_TOKEN')),
                         'user_key_configured': bool(key('FORSIC_USER_TELEGRAM_BOT_TOKEN')),
                         'group': prefs.get('telegram_group', '')},
            'gti': {'base_url': VT_URL, 'enabled': bool(prefs.get('gti_enabled', False)),
                    'key_configured': bool(key()),
                    'allow_public_network_indicators': bool(prefs.get('gti_public_network', False))}}


def save(body):
    from hermes_cli.config import save_config, save_env_value, remove_env_value, get_env_path
    llm = {'base_url': endpoint(body['llm_base_url']), 'model': body['llm_model'].strip()}
    if not llm['model'] or len(llm['model']) > 200 or any(c in llm['model'] for c in '\r\n'):
        raise ValueError('사용할 LLM 모델 이름을 입력해주세요.')
    if 'llm_context_length' in body:
        from agent.model_metadata import MINIMUM_CONTEXT_LENGTH
        context = body['llm_context_length']
        if context is not None and (type(context) is not int or not MINIMUM_CONTEXT_LENGTH <= context <= 2147483647):
            raise ValueError(f'문맥 길이는 {MINIMUM_CONTEXT_LENGTH:,} 이상의 정수 또는 모델 기본값으로 지정해주세요.')
        llm['context_length'] = context
    jev = endpoint(body.get('jev_base_url', ''), optional=True)
    group = telegram_group(body['telegram_group']) if body.get('telegram_group') is not None else None
    supplied_key = (body.get('gti_api_key') or '').strip()
    if supplied_key and (not supplied_key.isascii() or not supplied_key.isalnum() or len(supplied_key) != 64):
        raise ValueError('VT/GTI API 키 형식을 확인해주세요. 기존 키는 변경하지 않았어요.')
    if supplied_key and body.get('clear_gti_key'):
        raise ValueError('키 교체와 삭제는 동시에 할 수 없어요.')
    telegram = []
    for field, env_name in [('telegram_assistant_key','TELEGRAM_BOT_TOKEN'), ('telegram_user_key','FORSIC_USER_TELEGRAM_BOT_TOKEN')]:
        value = (body.get(field) or '').strip()
        clear = bool(body.get('clear_' + field))
        if value and (not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}', value) or clear):
            raise ValueError('텔레그램 봇 토큰 형식을 확인해주세요. 교체와 삭제는 동시에 할 수 없어요.')
        telegram.append((env_name,value,clear))
    with _lock:
        cfg = config()
        prefs = cfg.setdefault('plugins', {}).setdefault('entries', {}).setdefault('forsic', {}).setdefault('settings', {}).setdefault('connections', {})
        require_endpoint_key_choice(cfg, llm['base_url'], body)
        current = cfg.get('model', {})
        active = model_connection(current)
        # Omission preserves both legacy clients and an already staged choice.
        selected = {**active, **(prefs.get('llm_pending') or {}), **llm}
        if selected == active:
            prefs.pop('llm_pending', None)
        else:
            prefs['llm_pending'] = selected
        prefs.update(jev_base_url=jev, gti_enabled=bool(body.get('gti_enabled')),
                     gti_public_network=bool(body.get('gti_public_network')))
        if group is not None:
            prefs['telegram_group'] = group
        if supplied_key:
            save_env_value('VT_APIKEY', supplied_key)
            get_env_path().chmod(0o600)
        elif body.get('clear_gti_key'):
            remove_env_value('VT_APIKEY')
            prefs['gti_enabled'] = False
        for env_name,value,clear in telegram:
            if value:
                save_env_value(env_name,value)
                get_env_path().chmod(0o600)
            elif clear:
                remove_env_value(env_name)
        save_config(cfg)
    return public()


def apply_pending_llm():
    """Called only by the operator's dashboard launcher, never during a live turn."""
    from hermes_cli.config import save_config
    with _lock:
        cfg = config()
        prefs = preferences(cfg)
        pending = prefs.get('llm_pending')
        if not pending:
            return
        new_url = endpoint(pending['base_url'])
        setup_pending = cfg.get('plugins', {}).get('entries', {}).get('forsic', {}).get('settings', {}).get('setup_pending') or {}
        require_endpoint_key_choice(cfg, new_url, {'llm_api_key': 'pending' if setup_pending.get('llm_key_action') == 'replace' else '', 'clear_llm_key': setup_pending.get('llm_key_action') == 'clear'})
        old = cfg.setdefault('model', {}).copy()
        if new_url != old.get('base_url'):
            # URL and credential references are changed in the same config
            # write; interruption before setup applies cannot forward an old key.
            for field in ('key_env', 'api_key_env', 'api_key', 'api'):
                cfg['model'].pop(field, None)
        cfg['model'].update(provider='custom', base_url=new_url, default=pending['model'])
        if 'context_length' in pending:
            context = pending['context_length']
            if context is None:
                cfg['model'].pop('context_length', None)
                cfg['model'].pop('ollama_num_ctx', None)
            else:
                cfg['model']['context_length'] = context
                # Keep Ollama's native allocation in sync with the chosen window.
                cfg['model']['ollama_num_ctx'] = context
        # Follow only auxiliary roles that used this same existing local connection.
        for role in cfg.get('auxiliary', {}).values():
            if isinstance(role, dict) and role.get('provider') == 'custom' and role.get('base_url') == old.get('base_url'):
                if new_url != old.get('base_url'):
                    for field in ('key_env', 'api_key_env', 'api_key', 'api'):
                        role.pop(field, None)
                role.update(base_url=new_url, model=pending['model'])
        prefs.pop('llm_pending')
        save_config(cfg)
