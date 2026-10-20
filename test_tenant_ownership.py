"""Ownership and native-store tests use only temporary synthetic profiles."""
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

import pytest

from forsic_plugin.tenants import (TenantRegistry, bind_scope, current_scope,
    resolve_request, validate_case, assert_session_scope)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('FORSIC_MANAGEMENT_ROOT', raising=False)
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'config.yaml').write_text(json.dumps({
        'model': {'provider': 'custom', 'default': 'synthetic', 'api_key': 'PRIVATE_SENTINEL'},
        'memory': {'memory_enabled': False},
        'platform_toolsets': {'web': ['forsic']}, 'goals': {'max_turns': 32},
        'plugins': {'enabled': ['forsic'], 'entries': {'forsic': {'settings': {
            'case_file': '/private/tenant-case.json', 'evidence_directory': '/private/data',
            'independent_review': True}}}}}))
    r = TenantRegistry()
    return r


def create(registry, name):
    return registry.create(name, operation_id=str(uuid.uuid4()))


def session(scope, sid, *, parent=None, ended=None):
    from hermes_state import SessionDB
    with bind_scope(scope):
        db = SessionDB(Path(scope['native_db']))
        db.create_session(sid, source='cli', profile_name=scope['profile_name'], parent_session_id=parent)
        if ended:
            db.end_session(sid, ended)
        db.close()


def test_first_list_is_read_only_and_fresh_native_create(registry):
    before = {p.relative_to(registry.home) for p in registry.home.rglob('*')}
    assert registry.list_public() == []
    assert before == {p.relative_to(registry.home) for p in registry.home.rglob('*')}
    operation = str(uuid.uuid4())
    a = registry.create('A', operation_id=operation)
    assert registry.create('A', operation_id=operation) == a
    import hermes_yaml as yaml
    config = yaml.safe_load((Path(a['profile_home']) / 'config.yaml').read_text())
    assert 'PRIVATE_SENTINEL' not in json.dumps(config)
    assert '/private/' not in json.dumps(config)
    assert config['goals']['max_turns'] == 32
    assert config['memory']['user_profile_enabled'] is False
    assert not Path(a['native_db']).exists()
    assert not Path(a['library_store']).exists()
    assert not list((Path(a['profile_home']) / 'memories').glob('*.md'))
    assert not (Path(a['profile_home']) / 'USER.md').exists()
    with pytest.raises(ValueError):
        registry.create('B', operation_id=operation)


def test_native_create_retry_after_publication_failure(registry, monkeypatch):
    import forsic_plugin.tenants as tenants
    original = tenants._atomic
    attempts = 0
    def fail_commit(path, body):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise OSError('synthetic interrupted registry commit')
        return original(path, body)
    operation = str(uuid.uuid4())
    monkeypatch.setattr(tenants, '_atomic', fail_commit)
    with pytest.raises(OSError):
        registry.create('A', operation_id=operation)
    assert registry.list_public() == []
    monkeypatch.setattr(tenants, '_atomic', original)
    a = registry.create('A', operation_id=operation)
    assert len(registry.list_public()) == 1
    assert not Path(a['native_db']).exists()


def test_profile_case_session_cross_scope_and_old_link(registry, tmp_path):
    from forsic_plugin.intake import Intake
    a, b = create(registry, 'A'), create(registry, 'B')
    evidence = tmp_path / 'synthetic-evidence'
    evidence.mkdir()
    (evidence / 'example.log').write_text('synthetic record')
    session(a, 'a'); session(b, 'b')
    with bind_scope(a):
        intake = Intake()
        intake.before('a', str(evidence))
        intake.inspect('a', {'path': str(evidence)})
        manifest = intake.state('a')['manifest']
        case_id = json.loads(Path(manifest).read_text())['case_id']
    assert resolve_request(session_id='a')['tenant_id'] == a['tenant_id']
    assert resolve_request(profile_name=a['profile_name'], case_id=case_id)['tenant_id'] == a['tenant_id']
    with pytest.raises(LookupError):
        resolve_request(tenant_id=b['tenant_id'], session_id='a')
    with pytest.raises(PermissionError):
        validate_case(b, manifest)
    with bind_scope(a):
        with pytest.raises(PermissionError):
            Intake(b['intake_root'])
        with bind_scope(b):
            assert current_scope()['tenant_id'] == b['tenant_id']
        assert current_scope()['tenant_id'] == a['tenant_id']
    from hermes_state import SessionDB
    with bind_scope(a):
        db = SessionDB(Path(a['native_db']))
        with pytest.raises(PermissionError):
            db.create_session('foreign', source='cli', profile_name=b['profile_name'])
        with pytest.raises(PermissionError):
            db.create_session('foreign-parent', source='cli', parent_session_id='b')
        db.close()


def test_compaction_owner_and_case_inheritance(registry, tmp_path):
    from forsic_plugin.intake import Intake
    from forsic_plugin.investigation_context import resolve_context
    a = create(registry, 'A')
    evidence = tmp_path / 'evidence' / 'synthetic.log'
    evidence.parent.mkdir()
    evidence.write_text('synthetic')
    session(a, 'root')
    with bind_scope(a):
        intake = Intake()
        intake.before('root', str(evidence))
        intake.inspect('root', {'path': str(evidence)})
    with sqlite3.connect(a['native_db']) as db:
        db.execute("UPDATE sessions SET end_reason='compression' WHERE id='root'")
    session(a, 'child', parent='root')
    with bind_scope(a):
        result = resolve_context('child', intake_root=a['intake_root'], native_db=a['native_db'])
    assert result['conversation_id'] == 'root'
    assert result['session_lineage'] == ['child', 'root']
    assert assert_session_scope(a, 'child')['profile_name'] == a['profile_name']


def test_shared_native_store_refuses_all_unfiltered_paths(registry):
    from hermes_state import SessionDB
    a, b = create(registry, 'A'), create(registry, 'B')
    session(a, 'a')
    with sqlite3.connect(a['native_db']) as db:
        db.execute("INSERT INTO sessions(id,source,started_at,profile_name) VALUES('b','cli',0,?)", (b['profile_name'],))
    for read_only in (False, True):
        with pytest.raises(PermissionError, match='Mixed-profile'):
            SessionDB(Path(a['native_db']), read_only=read_only)
    # Bounded row ownership checks do not infer ownership from the database path.
    assert assert_session_scope(a, 'a')['id'] == 'a'
    with pytest.raises(PermissionError):
        assert_session_scope(a, 'b')


def test_legacy_mapping_preserves_bytes_and_repeats(registry):
    home = registry.home
    (home / 'memories').mkdir()
    memory = home / 'memories/MEMORY.md'
    memory.write_text('unverified legacy fact')
    legacy = home / 'old-intake'
    legacy.mkdir()
    archive = home / 'old-library.sqlite3'
    archive.write_bytes(b'preserved archive fixture')
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (memory, archive)}
    a = registry.bootstrap_legacy(intake_root=legacy, library_store=archive)
    assert registry.bootstrap_legacy(intake_root=legacy, library_store=archive) == a
    assert a['memory_policy']['mode'] == 'off'
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (memory, archive)}
    b = create(registry, 'New')
    assert b['library_store'] != a['library_store']
    assert b['intake_root'] != a['intake_root']
    with pytest.raises(LookupError):
        resolve_request(profile_name='unknown')


def legacy_profile(registry, name):
    home = registry.home / 'profiles' / name
    home.mkdir(parents=True)
    (home / 'config.yaml').write_text(json.dumps({'plugins': {'entries': {'forsic': {
        'enabled': True, 'settings': {'intake_root': str(home / 'intake'),
                                     'library_path': str(home / 'library.sqlite3')}}}}}))
    return home


def test_completed_empty_migration_does_not_resurrect_profiles(registry):
    from forsic_plugin.tenants import _atomic, migrate_workspace
    profile = legacy_profile(registry, 'old-profile')
    _atomic(registry.path, {'schema': 1, 'tenants': [], 'operations': {},
                            'legacy_migration_complete': True})
    before = {p: p.read_bytes() for p in (registry.path, registry.home / 'config.yaml', profile / 'config.yaml')}
    for _ in range(2):
        assert migrate_workspace() == {'tenant_ids': [], 'data_moved': False, 'model_requests': 0}
        assert registry.list_public() == []
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize('completion_marker', [None, False])
def test_interrupted_migration_resumes_and_preserves_registered_ownership(registry, monkeypatch, completion_marker):
    from forsic_plugin.tenants import _atomic, migrate_workspace
    if completion_marker is not None:
        _atomic(registry.path, {'schema': 1, 'tenants': [], 'operations': {},
                                'legacy_migration_complete': completion_marker})
    profile = legacy_profile(registry, 'old-profile')
    memory = profile / 'memories' / 'MEMORY.md'
    memory.parent.mkdir()
    memory.write_bytes(b'synthetic historical memory')
    original = TenantRegistry.bootstrap_legacy

    def interrupt_named(self, **kwargs):
        if kwargs.get('profile_name') == 'old-profile':
            raise OSError('synthetic interrupted migration')
        return original(self, **kwargs)

    monkeypatch.setattr(TenantRegistry, 'bootstrap_legacy', interrupt_named)
    with pytest.raises(OSError, match='interrupted migration'):
        migrate_workspace()
    partial = registry._load()
    assert partial.get('legacy_migration_complete') is not True
    existing = registry.resolve(profile_name='default')
    bookmark = registry.public(existing)['start_url']
    monkeypatch.setattr(TenantRegistry, 'bootstrap_legacy', original)
    result = migrate_workspace()
    assert result['tenant_ids'] == [existing['tenant_id'], registry.resolve(profile_name='old-profile')['tenant_id']]
    assert registry._load()['legacy_migration_complete'] is True
    assert registry.resolve(profile_name='default') == existing
    assert registry.public(registry.resolve(profile_name='default'))['start_url'] == bookmark
    assert memory.read_bytes() == b'synthetic historical memory'
    saved = registry.path.read_bytes()
    assert migrate_workspace() == result
    assert registry.path.read_bytes() == saved

    # Completion disables automatic discovery, while explicit import remains possible.
    later = legacy_profile(registry, 'later-profile')
    assert migrate_workspace() == result
    with pytest.raises(LookupError):
        registry.resolve(profile_name='later-profile')
    imported = registry.bootstrap_legacy(profile_name='later-profile', profile_home=later)
    assert imported['tenant_id'] in migrate_workspace()['tenant_ids']


def test_migration_completion_preserves_interleaved_create_operation(registry, monkeypatch):
    from forsic_plugin.tenants import migrate_workspace
    original = TenantRegistry.bootstrap_legacy
    created = []

    def create_after_bootstrap(self, **kwargs):
        result = original(self, **kwargs)
        created.append(create(self, 'Created during migration'))
        return result

    monkeypatch.setattr(TenantRegistry, 'bootstrap_legacy', create_after_bootstrap)
    migrate_workspace()
    stored = registry._load()
    assert stored['legacy_migration_complete'] is True
    assert registry.resolve(tenant_id=created[0]['tenant_id']) == created[0]
    assert any(op.get('tenant_id') == created[0]['tenant_id'] for op in stored['operations'].values())


def test_global_admission_does_not_expose_other_tenant_details(registry, tmp_path):
    from forsic_plugin.investigations import Investigations, InvestigationUnavailable
    a, b = create(registry, 'A'), create(registry, 'B')
    evidence = tmp_path / 'evidence' / 'synthetic.log'
    evidence.parent.mkdir()
    evidence.write_text('synthetic')
    with bind_scope(a):
        result = Investigations(a['profile_home'], a['intake_root']).create_conversation(
            str(uuid.uuid4()), path=str(evidence))
    assert result['profile_name'] == a['profile_name']
    assert a['tenant_id'] in result['resume_url']
    with bind_scope(b):
        other = Investigations(b['profile_home'], b['intake_root'])
        assert other.list() == []
        assert other.new_investigation()['active_case'] is None
        with pytest.raises(InvestigationUnavailable):
            other.create_conversation(str(uuid.uuid4()), path=str(evidence))
