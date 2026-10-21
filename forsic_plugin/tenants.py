"""Tenant ownership over native Hermes profiles; no second conversation store.

The registry records immutable ownership and policy, not memories or case content.
Reads never bootstrap profiles. Migration is an explicit, repeatable startup step.
"""
from contextlib import closing, contextmanager
from contextvars import ContextVar
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from urllib.parse import urlencode
import uuid

_scope = ContextVar('forsic_tenant_scope', default=None)
MODES = {'off', 'manual', 'confirmed_auto'}
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.tenant-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return deepcopy(default)


def _config(home):
    import hermes_yaml as yaml
    path = Path(home) / 'config.yaml'
    value = yaml.safe_load(path.read_text()) if path.is_file() else {}
    if not isinstance(value or {}, dict):
        raise ValueError('프로필 설정 형식이 올바르지 않습니다.')
    return value or {}


def _write_config(home, value):
    import hermes_yaml as yaml
    path = Path(home) / 'config.yaml'
    fd, name = tempfile.mkstemp(prefix='.tenant-config-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _settings(config):
    return config.get('plugins', {}).get('entries', {}).get('forsic', {}).get('settings', {}) or {}


def _reference_config(value):
    """Copy connection references, never embedded credentials or provider namespaces."""
    if isinstance(value, dict):
        return {k: _reference_config(v) for k, v in value.items()
                if not re.search(r'(^|_)(api_key|token|password|secret|credential)s?$', k, re.I)}
    if isinstance(value, list):
        return [_reference_config(v) for v in value]
    return deepcopy(value)


def _policy_config(config, scope):
    config = deepcopy(config)
    memory = config.setdefault('memory', {})
    memory.update(tenant_managed=True, memory_enabled=scope['memory_policy']['mode'] != 'off',
                  user_profile_enabled=False, provider='builtin', memory_char_limit=2200)
    config['curator'] = {'enabled': False}
    config['background_review'] = {'enabled': False}
    # Native recall remains explicitly unavailable until it can prove ownership
    # for every shared-DB search/detail/cursor path. Same-tenant case recall is available.
    config.setdefault('tools', {}).setdefault('session_search', {})['enabled'] = False
    config.setdefault('sessions', {}).update(require_profile_ownership=True,
        allow_legacy_unowned=bool(scope.get('legacy') and scope['profile_name'] == 'default'))
    settings = config.setdefault('plugins', {}).setdefault('entries', {}).setdefault('forsic', {})
    settings['enabled'] = True
    settings = settings.setdefault('settings', {})
    settings.update(tenant_id=scope['tenant_id'], intake_root=scope['intake_root'],
                    library_path=scope['library_store'])
    return config


class TenantRegistry:
    def __init__(self, root=None):
        from hermes_constants import get_default_hermes_root
        self.home = Path(root or get_default_hermes_root()).expanduser().resolve()
        self.root = self.home / 'forsic-tenants'
        self.path = self.root / 'registry.json'

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.root / 'registry.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _load(self):
        data = _read(self.path, {'schema': 1, 'tenants': [], 'operations': {}})
        if (data.get('schema') != 1 or not isinstance(data.get('tenants'), list)
                or ('legacy_migration_complete' in data and type(data['legacy_migration_complete']) is not bool)):
            raise ValueError('테넌트 등록 정보를 확인할 수 없습니다.')
        identities, homes, profiles = set(), set(), set()
        for entry in data['tenants']:
            home = str(Path(entry['profile_home']).resolve())
            if (entry['tenant_id'] in identities or home in homes
                    or entry['profile_name'] in profiles):
                raise ValueError('테넌트 소유 범위가 중복됩니다.')
            expected = self.home if entry['profile_name'] == 'default' else self.home / 'profiles' / entry['profile_name']
            if home != str(expected.absolute()) or expected.is_symlink():
                raise PermissionError('프로필의 고정 소유 경로가 변경되었습니다.')
            identities.add(entry['tenant_id'])
            homes.add(home)
            profiles.add(entry['profile_name'])
        return data

    def list_public(self):
        return [self.public(t) for t in self._load()['tenants'] if t.get('status') == 'ready']

    @staticmethod
    def public(scope):
        public = {k: deepcopy(scope[k]) for k in
                  ('tenant_id', 'profile_name', 'display_name', 'description', 'memory_policy',
                   'revision', 'legacy', 'created_at') if k in scope}
        query = urlencode({'tenant_id': scope['tenant_id'], 'profile': scope['profile_name']})
        public.update(start_url='/?' + query, new_url='/forsic?view=new&' + query)
        return public

    def resolve(self, tenant_id='', profile_name='', profile_home=None):
        if not tenant_id and not profile_name and profile_home is None:
            raise ValueError('테넌트를 선택해주세요.')
        if profile_name == 'current':
            raise ValueError('명시적인 테넌트 프로필을 선택해주세요.')
        candidates = [t for t in self._load()['tenants'] if t.get('status') == 'ready'
                      and (not tenant_id or t['tenant_id'] == tenant_id)
                      and (not profile_name or t['profile_name'] == profile_name)
                      and (profile_home is None or Path(t['profile_home']) == Path(profile_home).resolve())]
        if len(candidates) != 1:
            raise LookupError('선택한 테넌트와 프로필이 일치하지 않습니다.')
        return deepcopy(candidates[0])

    def bootstrap_legacy(self, *, profile_name='default', profile_home=None,
                         intake_root=None, library_store=None, management_root=None, native_path=None):
        """Register existing paths in place. No archive/body/session or memory rewrite."""
        if not _IDENTIFIER.fullmatch(profile_name) or profile_name == 'current':
            raise ValueError('프로필 이름을 확인해주세요.')
        home = Path(profile_home or (self.home if profile_name == 'default'
                                    else self.home / 'profiles' / profile_name)).resolve()
        expected = self.home if profile_name == 'default' else self.home / 'profiles' / profile_name
        if home != expected or expected.is_symlink():
            raise PermissionError('기존 프로필 경로가 일치하지 않습니다.')
        with self._locked():
            data = self._load()
            existing = next((t for t in data['tenants'] if t['profile_home'] == str(home)), None)
            if existing:
                return deepcopy(existing)
            config = _config(home)
            settings = _settings(config)
            repository = Path(__file__).resolve().parents[1]
            intake = Path(intake_root or settings.get('intake_root') or repository / 'state/intake').resolve()
            library = Path(library_store or settings.get('library_path') or repository / 'state/library/cases.sqlite3').resolve()
            # A second legacy profile with unspecified/shared roots is ambiguous.
            if any(t['intake_root'] == str(intake) or t['library_store'] == str(library)
                   for t in data['tenants']):
                raise ValueError('기존 프로필 자료의 소유 범위를 먼저 지정해주세요.')
            entry = dict(tenant_id='tenant-legacy-' + hashlib.sha256(str(home).encode()).hexdigest()[:12],
                         profile_name=profile_name, profile_home=str(home),
                         display_name='기존 작업공간' if profile_name == 'default' else profile_name,
                         description='', intake_root=str(intake), case_root=str(intake / 'cases'),
                         library_store=str(library), management_root=str(Path(management_root or
                             os.environ.get('FORSIC_MANAGEMENT_ROOT', home / 'forsic-management')).resolve()),
                         native_db=str(Path(native_path or home / 'state.db').resolve()),
                         memory_policy={'mode': 'off' if config.get('memory', {}).get('memory_enabled') is False else 'manual'},
                         revision=1, legacy=True, status='ready', created_at=time.time())
            # Preserve unknown legacy contents as unverified. Do not enable automatic writes.
            _write_config(home, _policy_config(config, entry))
            data['tenants'].append(entry)
            _atomic(self.path, data)
            return deepcopy(entry)

    def create(self, display_name, description='', memory_mode='manual', *,
               operation_id, source_profile='default'):
        name = str(display_name).strip()
        if not name or len(name) > 120 or len(str(description)) > 1000 or memory_mode not in MODES:
            raise ValueError('테넌트 이름과 기억 정책을 확인해주세요.')
        operation_id = str(uuid.UUID(str(operation_id)))
        intent = dict(display_name=name, description=str(description).strip(),
                      memory_mode=memory_mode, source_profile=source_profile)
        with self._locked():
            data = self._load()
            old = data['operations'].get(operation_id)
            if old and old['intent'] != intent:
                raise ValueError('같은 요청 번호에 다른 테넌트를 생성할 수 없습니다.')
            if old and old.get('tenant_id'):
                return self.resolve(tenant_id=old['tenant_id'])
            if any(t['display_name'].casefold() == name.casefold() for t in data['tenants']):
                raise ValueError('같은 이름의 테넌트가 있습니다.')
            if not old:
                ident = uuid.uuid4().hex
                old = dict(intent=intent, profile_name='tenant-' + ident[:20],
                           pending_tenant_id='tenant-' + ident, status='prepared')
                data['operations'][operation_id] = old
                _atomic(self.path, data)
            home = self.home / 'profiles' / old['profile_name']
            from hermes_cli.profiles import create_profile, get_profile_dir
            if Path(get_profile_dir(old['profile_name'])).resolve() != home:
                raise ValueError('native 프로필 루트가 테넌트 등록 루트와 다릅니다.')
            if not home.exists():
                create_profile(old['profile_name'], no_alias=True, no_skills=True)
            elif home.is_symlink():
                raise PermissionError('프로필 경로를 확인해주세요.')
            # Only this prepared operation may finish this fresh profile. A foreign
            # file or session created meanwhile makes recovery fail closed.
            if ((home / 'state.db').exists() or any((home / 'memories').glob('*.md'))
                    or (home / 'USER.md').exists()):
                raise ValueError('신규 프로필에 기존 자료가 있어 자동 복구를 중단했습니다.')
            source_home = self.home if source_profile == 'default' else self.home / 'profiles' / source_profile
            if source_profile != 'default' and not any(t['profile_name'] == source_profile for t in data['tenants']):
                raise LookupError('기본 설정을 제공할 프로필을 찾을 수 없습니다.')
            source = _config(source_home)
            # No cloned soul, skill, channel, MCP, credentials, histories, or memory provider.
            config = {k: _reference_config(source[k]) for k in
                      ('model', 'agent', 'compression', 'context_compression', 'auxiliary',
                       'reasoning_effort', 'reasoning', 'toolsets', 'platform_toolsets', 'display',
                       'goals', 'tool_loop_guardrails', 'providers', 'fallback_model')
                      if k in source}
            config['security'] = {'allow_lazy_installs': False}
            config['updates'] = {'check': False}
            config['terminal'] = {'cwd': str(Path(__file__).resolve().parents[1])}
            config['dashboard'] = {'theme': 'forsic'}
            plugin = {'enabled': ['forsic']}
            allowed_settings = ('independent_review', 'executive_followup', 'question_contract_version')
            plugin['entries'] = {'forsic': {'enabled': True, 'settings': {
                k: deepcopy(_settings(source)[k]) for k in allowed_settings if k in _settings(source)}}}
            ren = (_settings(source).get('connections') or {}).get('ren_llm')
            if ren:
                # Native key_env resolves only in the new profile's secret scope.
                # Retain the connection reference; never clone the source .env.
                plugin['entries']['forsic']['settings']['connections'] = {'ren_llm': {
                    k: deepcopy(ren[k]) for k in ('enabled', 'base_url', 'model', 'key_env') if k in ren}}
            config['plugins'] = plugin
            old_plugin = source.get('plugins', {}).get('entries', {}).get('forsic', {})
            for key in ('allow_gateway_injection', 'allow_llm'):
                if key in old_plugin:
                    plugin['entries']['forsic'][key] = deepcopy(old_plugin[key])
            for key in ('intake_root', 'library_path', 'tenant_id', 'synthetic_roots'):
                config['plugins']['entries']['forsic']['settings'].pop(key, None)
            entry = dict(tenant_id=old['pending_tenant_id'], profile_name=old['profile_name'],
                         profile_home=str(home), display_name=name, description=intent['description'],
                         intake_root=str(home / 'forsic/intake'), case_root=str(home / 'forsic/intake/cases'),
                         library_store=str(home / 'forsic/library/cases.sqlite3'),
                         management_root=str(home / 'forsic-management'), native_db=str(home / 'state.db'),
                         memory_policy={'mode': memory_mode}, revision=1, legacy=False,
                         status='ready', created_at=time.time())
            _write_config(home, _policy_config(config, entry))
            plugin_link = home / 'plugins' / 'forsic'
            plugin_link.parent.mkdir(exist_ok=True)
            if not plugin_link.exists():
                plugin_link.symlink_to(Path(__file__).resolve().parent, target_is_directory=True)
            elif plugin_link.resolve() != Path(__file__).resolve().parent:
                raise PermissionError('신규 프로필의 제품 플러그인 경로가 일치하지 않습니다.')
            # Product instructions, not the source profile's personal files.
            for filename in ('AGENTS.md', 'SOUL.md'):
                product = Path(__file__).resolve().parents[1] / 'config' / filename
                if product.is_file():
                    (home / filename).write_bytes(product.read_bytes())
            data['tenants'].append(entry)
            old.update(status='committed', tenant_id=entry['tenant_id'])
            _atomic(self.path, data)
            return deepcopy(entry)

    def update_policy(self, tenant_id, mode, expected_revision, operation_id):
        if mode not in MODES:
            raise ValueError('기억 정책을 확인해주세요.')
        operation_id = str(uuid.UUID(str(operation_id)))
        intent = dict(tenant_id=tenant_id, mode=mode, expected_revision=expected_revision)
        with self._locked():
            data = self._load()
            key = 'policy:' + operation_id
            old = data['operations'].get(key)
            if old:
                if old['intent'] != intent:
                    raise ValueError('같은 요청 번호가 다른 설정에 사용되었습니다.')
                return self.resolve(tenant_id=tenant_id)
            entry = next((t for t in data['tenants'] if t['tenant_id'] == tenant_id), None)
            if not entry:
                raise LookupError('테넌트를 찾을 수 없습니다.')
            if entry['revision'] != expected_revision:
                raise ValueError('설정이 변경되었습니다. 최신 상태를 다시 확인해주세요.')
            entry['memory_policy'] = {'mode': mode}
            entry['revision'] += 1
            # Runtime rechecks registry mode, so a stale agent never gains write authority.
            _write_config(entry['profile_home'], _policy_config(_config(entry['profile_home']), entry))
            data['operations'][key] = {'intent': intent, 'status': 'committed'}
            _atomic(self.path, data)
            return deepcopy(entry)


@contextmanager
def bind_scope(scope):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    token = _scope.set(deepcopy(scope))
    native_token = set_hermes_home_override(scope['profile_home'])
    try:
        yield scope
    finally:
        reset_hermes_home_override(native_token)
        _scope.reset(token)


def current_scope(required=False):
    from hermes_constants import get_hermes_home
    home = Path(get_hermes_home()).resolve()
    bound = _scope.get()
    if bound and Path(bound['profile_home']) != home:
        raise PermissionError('실행 중 테넌트 프로필이 변경되었습니다.')
    registry = TenantRegistry()
    try:
        return registry.resolve(tenant_id=bound['tenant_id'] if bound else '', profile_home=home)
    except LookupError:
        if required or bound or registry.path.exists() or _settings(_config(home)).get('tenant_id'):
            raise PermissionError('실행 테넌트의 소유 범위를 확인할 수 없습니다.')
        return None


def native_db(scope):
    return Path(scope.get('native_db') or Path(scope['profile_home']) / 'state.db').resolve()


def assert_session_scope(scope, session_id, native_db=None):
    path = Path(native_db or globals()['native_db'](scope)).resolve()
    if path != globals()['native_db'](scope) or not path.is_file():
        raise LookupError('이 테넌트의 저장된 대화를 찾을 수 없습니다.')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        row = db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
        if not row:
            raise LookupError('이 테넌트의 저장된 대화를 찾을 수 없습니다.')
        row = dict(row)
        owner = row.get('profile_name')
        if owner != scope['profile_name'] and not (scope.get('legacy') and not owner
                                                   and scope['profile_name'] == 'default'):
            raise PermissionError('다른 테넌트의 대화에 접근할 수 없습니다.')
        return row


validate_session = assert_session_scope


def validate_case(scope, manifest):
    if hasattr(manifest, 'config'):
        config = manifest.config
        path = getattr(manifest, 'manifest', None)
    elif isinstance(manifest, dict):
        config, path = manifest, None
    else:
        path = Path(manifest).resolve(strict=True)
        if not path.is_relative_to(Path(scope['case_root']).resolve()):
            raise PermissionError('다른 테넌트의 사건에 접근할 수 없습니다.')
        config = _read(path)
    if config.get('tenant_id') not in (scope['tenant_id'], None if scope.get('legacy') else ''):
        raise PermissionError('사건과 테넌트의 소유 범위가 다릅니다.')
    if config.get('profile_name') and config['profile_name'] != scope['profile_name']:
        raise PermissionError('사건과 프로필이 일치하지 않습니다.')
    output = Path(config['output_root']).resolve()
    if not output.is_relative_to(Path(scope['case_root']).resolve()):
        raise PermissionError('사건 결과가 테넌트 저장 범위를 벗어났습니다.')
    return config


def owns_case_source(scope, manifest=None, source_db=None):
    """Archived body/source identity must be checked by the caller before this path guard."""
    if manifest is not None:
        config = validate_case(scope, manifest)
        if source_db is not None and Path(source_db).resolve() != Path(config['output_root']).resolve() / 'activity.sqlite3':
            raise PermissionError('사례의 출처 경로가 원래 사건과 다릅니다.')
    if source_db is not None:
        path = Path(source_db).resolve()
        if path.name != 'activity.sqlite3' or not path.is_relative_to(Path(scope['case_root']).resolve()):
            raise PermissionError('다른 테넌트의 사례 출처에 접근할 수 없습니다.')
    return True


def resolve_request(*, tenant_id='', profile_name='', session_id='', case_id='', request=None, required=False):
    registry = TenantRegistry()
    if not registry.path.exists() and not tenant_id and not required:
        # Unmigrated native hosts retain their established boundary. Once the
        # registry exists, unknown ownership never falls back to default.
        return None
    if not tenant_id and not profile_name and not session_id and not case_id:
        if required:
            raise ValueError('테넌트를 선택해주세요.')
        return None
    if tenant_id or profile_name:
        candidates = [registry.resolve(tenant_id=tenant_id, profile_name=profile_name)]
    else:
        candidates = [registry.resolve(tenant_id=t['tenant_id']) for t in registry.list_public()]
    matches = []
    for scope in candidates:
        try:
            if session_id:
                assert_session_scope(scope, session_id)
            if case_id:
                if not _IDENTIFIER.fullmatch(case_id):
                    raise ValueError('사건 번호를 확인해주세요.')
                validate_case(scope, Path(scope['case_root']) / case_id / 'case.json')
            if session_id and case_id:
                from .investigation_context import resolve_context
                with bind_scope(scope):
                    context = resolve_context(session_id, intake_root=scope['intake_root'], native_db=native_db(scope))
                if context is None or context['case_id'] != case_id:
                    raise PermissionError('대화와 사건이 일치하지 않습니다.')
            matches.append(scope)
        except (LookupError, PermissionError, FileNotFoundError):
            continue
    if len(matches) != 1:
        raise LookupError('요청한 테넌트·사건·대화의 소유 범위를 확인할 수 없습니다.')
    return matches[0]


def migrate_workspace():
    """One-time startup migration; explicit bootstrap_legacy admits later profiles.

    An absent/false completion marker resumes interrupted migration in place.
    A completed, even empty, registry never rediscovers unregistered profiles.
    """
    registry = TenantRegistry()
    with registry._locked():
        data = registry._load()
        if data.get('legacy_migration_complete') is True:
            return {'tenant_ids': [t['tenant_id'] for t in data['tenants'] if t.get('status') == 'ready'],
                    'data_moved': False, 'model_requests': 0}
    result = []
    homes = [('default', registry.home)]
    profiles = registry.home / 'profiles'
    if profiles.is_dir():
        homes.extend((p.name, p) for p in sorted(profiles.iterdir())
                     if p.is_dir() and not p.is_symlink() and (p / 'config.yaml').is_file())
    for name, home in homes:
        config = _config(home)
        entry = config.get('plugins', {}).get('entries', {}).get('forsic')
        if name != 'default' and not entry:
            continue
        result.append(registry.bootstrap_legacy(profile_name=name, profile_home=home)['tenant_id'])
    with registry._locked():
        # Reload after independently locked bootstrap/create operations so their
        # mappings and operation receipts cannot be lost at completion.
        data = registry._load()
        data['legacy_migration_complete'] = True
        _atomic(registry.path, data)
    return {'tenant_ids': result, 'data_moved': False, 'model_requests': 0}
