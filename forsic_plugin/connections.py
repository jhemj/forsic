"""Connection preferences and explicit operator tests over native config/storage."""
from urllib.parse import urlsplit
import hashlib
import threading
import re
import secrets

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
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise ValueError('API 주소를 인증정보 없이 올바른 HTTP(S) 형식으로 입력해주세요.') from None
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


def _llm_key_pending(cfg):
    return cfg.get('plugins', {}).get('entries', {}).get('forsic', {}).get('settings', {}).get('setup_pending') or {}


def _key_choice(body, field='llm_api_key', clear_field='clear_llm_key', label='LLM'):
    value = body.get(field, '')
    if not isinstance(value, str):
        raise ValueError(f'{label} API 키 형식을 확인해주세요.')
    value = value.strip()
    if (len(value) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in value)
            or value and body.get(clear_field)):
        raise ValueError(f'{label} API 키 형식을 확인해주세요. 교체와 삭제는 동시에 할 수 없어요.')
    return ('replace' if value else 'clear') if field in body or body.get(clear_field) else None, value


def require_endpoint_key_choice(cfg, url, body):
    action, _ = _key_choice(body)
    pending = _llm_key_pending(cfg)
    selected_url = pending.get('llm_key_base_url') or (preferences(cfg).get('llm_pending') or {}).get('base_url')
    if action:
        return
    if pending.get('llm_key_action') == 'replace':
        if url != selected_url:
            raise ValueError('LLM 서버 주소를 바꾸려면 새 API 키를 입력하거나 키 없이 사용을 선택해주세요.')
        return
    if pending.get('llm_key_action') == 'clear' and url == selected_url:
        return
    if url != (cfg.get('model') or {}).get('base_url') and has_model_key(cfg):
        raise ValueError('LLM 서버 주소를 바꾸려면 새 API 키를 입력하거나 키 없이 사용을 선택해주세요.')


def stage_llm_key(cfg, url, body):
    """Use the existing setup pending record; never overwrite a live secret slot."""
    from hermes_cli.config import custom_endpoint_key_env, save_env_value, require_env_writable, get_env_path
    action, value = _key_choice(body)
    if not action:
        return
    opts = cfg.setdefault('plugins', {}).setdefault('entries', {}).setdefault('forsic', {}).setdefault('settings', {})
    pending = opts.setdefault('setup_pending', {})
    if action == 'replace':
        slot = custom_endpoint_key_env('forsic_llm_' + secrets.token_hex(16))
        require_env_writable(slot, 'set')
        save_env_value(slot, value)
        get_env_path().chmod(0o600)
        pending['llm_key_env'] = slot
    else:
        pending.pop('llm_key_env', None)
    pending.update(llm_key_action=action, llm_key_base_url=url)


def retire_llm_key(cfg, slot):
    """Remove only Forsic-owned slots, after the config stopped referring to them."""
    from .setup import LLM_KEY, LLM_PENDING_KEY
    from hermes_cli.config import remove_env_value
    if slot and (slot in (LLM_KEY, LLM_PENDING_KEY) or slot.startswith(('HERMES_CUSTOM_FORSIC_LLM_', 'HERMES_CUSTOM_FORSIC_REN_'))):
        def referenced(value):
            if isinstance(value, dict):
                return any(referenced(v) for v in value.values())
            if isinstance(value, list):
                return any(referenced(v) for v in value)
            return value == slot
        if not referenced(cfg):
            remove_env_value(slot)


def pending_llm_credential(cfg):
    from .setup import LLM_PENDING_KEY
    return key(_llm_key_pending(cfg).get('llm_key_env') or LLM_PENDING_KEY)


def model_credential(cfg):
    """Read only the credential explicitly bound to this configured main route."""
    model = cfg.get('model') or {}
    direct = model.get('api_key') or model.get('api')
    if direct:
        return direct
    slot = model.get('key_env') or model.get('api_key_env')
    if slot:
        value = key(slot)
        if not value:
            raise ValueError('주 모델의 API 키를 현재 프로필에서 확인할 수 없습니다.')
        return value
    return 'no-key-required'


def selected_key_configured(cfg):
    action = _llm_key_pending(cfg).get('llm_key_action')
    return action == 'replace' or (action != 'clear' and has_model_key(cfg))


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


def _ren_preferences(prefs):
    return prefs.get('ren_llm') or {}


def _ren_key_env(url, version=''):
    from hermes_cli.config import custom_endpoint_key_env
    identity = 'forsic_ren_' + hashlib.sha256(url.encode()).hexdigest()
    return custom_endpoint_key_env(identity + ('_' + version if version else ''))


def _ren_update(prefs, body):
    """Validate the optional second connection before writing any preferences/secrets."""
    previous = _ren_preferences(prefs)
    selected = dict(previous)
    for field in ('enabled', 'base_url', 'model'):
        name = 'ren_llm_' + field
        if name in body:
            value = body[name]
            if field == 'enabled':
                if type(value) is not bool:
                    raise ValueError('Ren 별도 모델 사용 여부를 확인해주세요.')
            else:
                if not isinstance(value, str):
                    raise ValueError('Ren 서버 주소와 모델 이름을 확인해주세요.')
                value = endpoint(value, optional=True) if field == 'base_url' else value.strip()
                if field == 'model' and (len(value) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in value)
                                          or value.lower() == 'auto'):
                    raise ValueError('Ren에 사용할 명시적 모델 이름을 입력해주세요.')
            selected[field] = value
    if selected.get('enabled') and (not selected.get('base_url') or not selected.get('model')):
        raise ValueError('Ren 별도 모델을 사용하려면 서버 주소와 모델 이름을 입력해주세요.')
    action, supplied = _key_choice(body, 'ren_llm_api_key', 'clear_ren_llm_key', 'Ren')
    if selected.get('base_url') != previous.get('base_url') and previous.get('key_env') and not action:
        raise ValueError('Ren 서버 주소를 바꾸려면 새 API 키를 입력하거나 키 없이 사용을 선택해주세요.')
    if action == 'replace':
        if not selected.get('base_url'):
            raise ValueError('Ren API 키를 저장하려면 서버 주소를 입력해주세요.')
        selected['key_env'] = _ren_key_env(selected['base_url'], secrets.token_hex(16))
    elif action == 'clear':
        selected.pop('key_env', None)
    return selected, supplied, previous.get('key_env') if action else None


def review_connection_snapshot(cfg):
    """Freeze route and scoped credential together; the credential stays in memory."""
    ren = _ren_preferences(preferences(cfg))
    if ren.get('enabled'):
        selected, _, _ = _ren_update(preferences(cfg), {
            'ren_llm_enabled': True, 'ren_llm_base_url': ren.get('base_url', ''),
            'ren_llm_model': ren.get('model', '')})
        route = {'provider': 'custom', 'default': selected['model'],
                 'base_url': endpoint(selected['base_url']), 'connection': 'ren'}
        expected = re.escape(_ren_key_env(route['base_url']).removesuffix('_API_KEY'))
        if selected.get('key_env') and not re.fullmatch(expected + r'(?:_[0-9A-F]{32})?_API_KEY', selected['key_env']):
            raise ValueError('Ren 서버에 연결된 별도 API 키 참조를 확인해주세요.')
        credential = key(selected['key_env']) if selected.get('key_env') else 'no-key-required'
        if not credential:
            raise ValueError('Ren 별도 모델의 API 키를 현재 프로필에서 확인할 수 없습니다.')
        return route, credential
    model = cfg.get('model') or {}
    if (model.get('provider') != 'custom' or not isinstance(model.get('default'), str)
            or not model['default'] or not model.get('base_url')):
        raise ValueError('Independent review requires an explicit custom model connection')
    return {'provider': model['provider'], 'default': model['default'],
            'base_url': endpoint(model['base_url'])}, model_credential(cfg)


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
    selected = {**active, **(prefs.get('llm_pending') or {}), 'key_configured': selected_key_configured(cfg)}
    ren = _ren_preferences(prefs)
    return {'llm': selected, 'llm_current': active, 'llm_saved_default': active,
            'llm_apply_policy': 'next_turn', 'llm_context': context_metadata(selected),
            'ren_llm': {'enabled': bool(ren.get('enabled')), 'base_url': ren.get('base_url', ''),
                        'model': ren.get('model', ''),
                        'key_configured': bool(key(ren['key_env'])) if ren.get('key_env') else False},
            'llm_pending': bool(prefs.get('llm_pending') or _llm_key_pending(cfg).get('llm_key_action')),
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
        ren, ren_key, _ = _ren_update(preferences(cfg), body)
        from hermes_cli.config import require_env_writable
        if ren_key:
            require_env_writable(ren['key_env'], 'set')
        prefs = cfg.setdefault('plugins', {}).setdefault('entries', {}).setdefault('forsic', {}).setdefault('settings', {}).setdefault('connections', {})
        require_endpoint_key_choice(cfg, llm['base_url'], body)
        old_pending_slot = _llm_key_pending(cfg).get('llm_key_env')
        if ren or any(k.startswith('ren_llm_') or k == 'clear_ren_llm_key' for k in body):
            prefs['ren_llm'] = ren
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
        if ren_key:
            save_env_value(ren['key_env'], ren_key)
            get_env_path().chmod(0o600)
        stage_llm_key(cfg, llm['base_url'], body)
        # Publish once: existing turns keep their bound client and immutable key.
        _promote_pending_llm(cfg)
        save_config(cfg)
        retire_llm_key(cfg, old_pending_slot)
        # Previously live main/Ren slots may still be bound to an in-flight or
        # pinned native runtime. Only never-live staging slots are retired here.
    return public()


def _promote_pending_llm(cfg):
    """Publish a staged choice in memory; caller performs the single atomic write."""
    prefs = preferences(cfg)
    key_pending = _llm_key_pending(cfg)
    action = key_pending.get('llm_key_action')
    pending = prefs.get('llm_pending')
    if not pending and not action:
        return
    pending = pending or model_connection(cfg.get('model') or {})
    new_url = endpoint(pending['base_url'])
    require_endpoint_key_choice(cfg, new_url, {})
    old = cfg.setdefault('model', {}).copy()
    credential_fields = ('key_env', 'api_key_env', 'api_key', 'api')
    replacement = {}
    legacy_pending = False
    if action:
        if key_pending.get('llm_key_base_url') and key_pending['llm_key_base_url'] != new_url:
            raise ValueError('대기 중인 LLM API 키와 서버 주소가 일치하지 않습니다.')
        if action == 'replace':
            if not pending_llm_credential(cfg):
                raise ValueError('대기 중인 LLM API 키를 확인할 수 없습니다.')
            slot = key_pending.get('llm_key_env')
            if not slot:
                # Migrate the fixed legacy pending slot without rotating it.
                stage_llm_key(cfg, new_url, {'llm_api_key': pending_llm_credential(cfg)})
                slot = key_pending['llm_key_env']
                legacy_pending = True
            replacement = {'key_env': slot}
        else:
            replacement = {'api_key': 'no-key-required'}
    elif new_url != old.get('base_url'):
        replacement = {'api_key': 'no-key-required'}
    if action or new_url != old.get('base_url'):
        for field in credential_fields:
            cfg['model'].pop(field, None)
        cfg['model'].update(replacement)
    cfg['model'].update(provider='custom', base_url=new_url, default=pending['model'])
    if 'context_length' in pending:
        context = pending['context_length']
        if context is None:
            cfg['model'].pop('context_length', None)
            cfg['model'].pop('ollama_num_ctx', None)
        else:
            cfg['model']['context_length'] = context
            cfg['model']['ollama_num_ctx'] = context
    for role in cfg.get('auxiliary', {}).values():
        if isinstance(role, dict) and role.get('provider') == 'custom' and role.get('base_url') == old.get('base_url'):
            if action or new_url != old.get('base_url'):
                for field in credential_fields:
                    role.pop(field, None)
                role.update(replacement)
            role.update(base_url=new_url, model=pending['model'])
    prefs.pop('llm_pending', None)
    for field in ('llm_key_action', 'llm_key_base_url', 'llm_key_env'):
        key_pending.pop(field, None)
    opts = cfg.get('plugins', {}).get('entries', {}).get('forsic', {}).get('settings', {})
    if not key_pending:
        opts.pop('setup_pending', None)
    return old, action, legacy_pending


def apply_pending_llm(*, retire_active=True):
    """Launcher/migration compatibility; live saves retain previously active slots."""
    from hermes_cli.config import save_config, remove_env_value
    from .setup import LLM_PENDING_KEY
    with _lock:
        cfg = config()
        promoted = _promote_pending_llm(cfg)
        if promoted is None:
            return
        old, action, legacy_pending = promoted
        save_config(cfg)
        if legacy_pending:
            remove_env_value(LLM_PENDING_KEY)
        if action and retire_active:
            retire_llm_key(cfg, old.get('key_env') or old.get('api_key_env'))


def test_connection(body):
    """Explicit bounded inference with a fixed prompt, never case data or persistence."""
    import time
    import httpx
    from openai import OpenAI, APIConnectionError, APITimeoutError, APIStatusError
    url = endpoint(body['base_url'])
    model = body['model'].strip()
    if not model or model.lower() == 'auto' or any(ord(c) < 32 or ord(c) == 127 for c in model):
        raise ValueError('테스트할 명시적 모델 이름을 입력해주세요.')
    action, credential = _key_choice(body, 'api_key', label='테스트')
    cfg = config()
    if action is None:
        if body['connection'] == 'main':
            selected_url = (preferences(cfg).get('llm_pending') or {}).get('base_url') or (cfg.get('model') or {}).get('base_url')
            if url != selected_url:
                raise ValueError('서버 주소를 바꾸려면 새 API 키를 입력하거나 키 없이 사용을 선택해주세요.')
            require_endpoint_key_choice(cfg, url, {})
            pending = _llm_key_pending(cfg)
            selected = {**model_connection(cfg.get('model') or {}), **(preferences(cfg).get('llm_pending') or {})}
            if pending.get('llm_key_action') == 'replace' and url == selected.get('base_url') and url == pending.get('llm_key_base_url'):
                credential = pending_llm_credential(cfg)
                if not credential:
                    raise ValueError('저장된 API 키를 현재 프로필에서 확인할 수 없습니다.')
            elif pending.get('llm_key_action') == 'clear' and url == selected.get('base_url'):
                credential = ''
            elif url == (cfg.get('model') or {}).get('base_url'):
                credential = model_credential(cfg)
        else:
            saved = _ren_preferences(preferences(cfg))
            if url != saved.get('base_url'):
                raise ValueError('Ren 서버 주소를 바꾸려면 새 API 키를 입력하거나 키 없이 사용을 선택해주세요.')
            elif saved.get('key_env'):
                credential = key(saved['key_env'])
                if not credential:
                    raise ValueError('저장된 Ren API 키를 현재 프로필에서 확인할 수 없습니다.')
    credential = credential if credential not in ('ollama', 'no-key-required') else ''
    started = time.monotonic()
    status, reachable, responded = 'invalid_response', False, False
    details = {'ok': '모델이 테스트 요청에 응답했어요.',
               'authentication_error': '인증 정보를 확인해주세요.',
               'model_error': '모델 이름과 API 지원 여부를 확인해주세요.',
               'timeout': '테스트 요청 대기 시간(30초)이 초과됐어요.',
               'connection_error': '서버에 연결하지 못했어요.',
               'provider_error': '서버가 테스트 요청을 처리하지 못했어요.',
               'invalid_response': '모델의 유효한 응답을 확인하지 못했어요.'}
    class BoundedStream(httpx.SyncByteStream):
        def __init__(self, source):
            self.source = source

        def __iter__(self):
            size = 0
            for data in self.source:
                size += len(data)
                if time.monotonic() - started > 30:
                    raise httpx.ReadTimeout('Connection test deadline exceeded')
                if size > 65536:
                    raise ValueError('Connection test response limit exceeded')
                yield data

        def close(self):
            self.source.close()

    def bound_response(response):
        response.stream = BoundedStream(response.stream)

    try:
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False,
                          event_hooks={'response': [bound_response]}) as transport:
            with OpenAI(base_url=url, api_key=credential or 'no-key-required',
                        timeout=30, max_retries=0, http_client=transport) as client:
                with client.chat.completions.create(model=model, messages=[{'role': 'user', 'content': 'Reply with OK.'}],
                                                    max_tokens=32, stream=True) as stream:
                    reachable = True
                    for count, chunk in enumerate(stream):
                        if time.monotonic() - started > 30:
                            status = 'timeout'
                            break
                        choices = getattr(chunk, 'choices', None) or []
                        for choice in choices:
                            delta = getattr(choice, 'delta', None)
                            content = getattr(delta, 'content', None) if delta else None
                            reasoning = getattr(delta, 'reasoning_content', None) if delta else None
                            for value in (content, reasoning):
                                if isinstance(value, str) and value:
                                    responded = True
                        if responded:
                            status = 'ok'
                            break
                        if count >= 127:
                            break
    except (APITimeoutError, httpx.TimeoutException):
        status = 'timeout'
    except APIConnectionError:
        status = 'connection_error'
    except APIStatusError as exc:
        reachable = True
        status = ('authentication_error' if exc.status_code in (401, 403) else
                  'model_error' if exc.status_code in (400, 404, 422) else 'provider_error')
    except Exception:
        # SDK/parser failures may embed a provider body or credential. Return only
        # our fixed status text; never echo or log the exception.
        status = 'invalid_response'
    return {'reachable': reachable, 'model_responded': responded, 'status': status,
            'latency_ms': round((time.monotonic() - started) * 1000), 'detail': details[status]}
