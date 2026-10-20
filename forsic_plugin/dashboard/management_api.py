"""Local operator management, using native auth, stores and idle admission."""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from forsic_plugin import capability_runtime as runtime
from forsic_plugin import extension_hub as hub
from forsic_plugin.environments import EnvironmentManager
from .management_store import ManagementStore, digest
from .tenant_api import request_scope

router = APIRouter(dependencies=[Depends(request_scope)])


class ActionInput(BaseModel):
    model_config = ConfigDict(extra='allow')
    action: str = Field(min_length=1, max_length=80)
    expected_revision: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    case_id: str = Field(default='', max_length=256)


def scope(request, write=False):
    from forsic_plugin.setup import local_admin
    home = runtime.served_home(request)
    if write and not local_admin(request):
        raise HTTPException(403, '설치된 컴퓨터의 localhost 주소에서 관리해주세요.')
    from forsic_plugin.tenants import current_scope
    tenant=current_scope()
    manager=EnvironmentManager(root=tenant['management_root']) if tenant is not None else EnvironmentManager()
    return home, ManagementStore(home,root=Path(tenant['management_root'])/'ui' if tenant is not None else None), manager


def settings():
    from hermes_cli.config import load_config,require_readable_config_before_write
    from forsic_plugin.tenants import current_scope
    tenant=current_scope()
    # Only explicit Forsic settings are projected here. Native load_config's
    # cold-cache path writes a last-known-good backup even on GET.
    config=require_readable_config_before_write() if tenant is not None else load_config()
    values=config.get('plugins', {}).get('entries', {}).get('forsic', {}).get('settings', {})
    return {**values,'intake_root':str(tenant['intake_root'])} if tenant is not None else values


def case_records():
    """Enumerate intake manifests including dormant/archived cases, not only live sessions."""
    from forsic_plugin.intake import Intake
    configuration = settings()
    intake = Intake(configuration.get('intake_root')).root.resolve()
    manifests = set()
    case_root = intake / 'cases'
    if case_root.is_symlink(): raise ValueError('사건 등록 위치가 링크여서 참조를 확인할 수 없습니다.')
    if case_root.exists():
        for directory in case_root.iterdir():
            if directory.is_symlink(): raise ValueError('사건 등록 위치의 링크를 확인해주세요.')
            if directory.is_dir():
                path = directory / 'case.json'
                if not path.is_file() or path.is_symlink(): raise ValueError('사건 manifest가 누락되었거나 링크입니다.')
                manifests.add(path.resolve())
    if intake.exists():
        for binding in intake.glob('*.json'):
            if binding.is_symlink(): raise ValueError('사건 연결 기록의 링크를 확인해주세요.')
            item = json.loads(binding.read_text())
            if item.get('manifest'):
                path = Path(item['manifest'])
                if not path.resolve().is_relative_to(case_root.resolve()):
                    raise ValueError('등록 범위 밖 사건 참조를 확인할 수 없습니다.')
                manifests.add(path.resolve(strict=True))
    if configuration.get('case_file'):
        configured = Path(configuration['case_file'])
        if configured.is_symlink(): raise ValueError('설정 사건 manifest가 링크입니다.')
        manifests.add(configured.resolve(strict=True))
    records = []
    for path in sorted(manifests):
        from forsic_plugin.tenants import current_scope,validate_case
        tenant=current_scope()
        if tenant is not None:validate_case(tenant,path)
        item = json.loads(path.read_text())
        if not item.get('output_root'):
            raise ValueError('사건 출력 위치를 확인할 수 없습니다.')
        records.append((path, item))
    return records


def selected_case(case_id, home):
    from forsic_plugin.evidence import Case
    if not case_id: return None
    from forsic_plugin.intake import Intake
    from forsic_plugin.investigations import Investigations
    intake = Intake(settings().get('intake_root')).root
    owned = Investigations(home, intake).get(case_id=case_id)
    if owned is None: raise HTTPException(404, '현재 프로필에 연결된 사건을 찾을 수 없습니다.')
    matches = [path for path, item in case_records() if item.get('case_id') == case_id]
    if len(matches) != 1 or matches[0] != Path(owned['manifest']).resolve():
        raise HTTPException(409, '사건 소유권과 보존 manifest가 일치하지 않습니다.')
    return Case(matches[0], read_only=True)


def references(manager):
    from types import SimpleNamespace
    result = []
    for _, item in case_records():
        state = manager.case_state(SimpleNamespace(output=Path(item['output_root'])))
        for record in [state.get('current'), state.get('pending'), *state.get('history', [])]:
            if record:
                selected = record.get('extensions', {})
                if selected and (not isinstance(selected, dict) or set(selected) != {'packs'}):
                    raise ValueError('과거 사건의 확장팩 참조 형식을 확인할 수 없습니다.')
                result.append(selected)
    return result


def builder_manager(home):
    from forsic_plugin.repository_builder import BuilderManager
    return BuilderManager(home)


def clean(value):
    """Paths stay on the server; public jobs carry logs and explicit identities."""
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items() if key not in ('path', 'log', 'destination', 'process')}
    if isinstance(value, list): return [clean(item) for item in value]
    return value


def public_job(record, store=None):
    result = clean(record)
    result.update(id=record.get('id') or record.get('job_id') or record.get('operation_id'),
                  status=record.get('status') or record.get('state') or 'not_observed')
    if record.get('kind') == 'repository_builder':
        result['native_session_id'] = record.get('stored_session_id') or record.get('runtime_session_id')
        result['fixture_test'] = record.get('tests')
        result['support'] = record.get('support') or {'status': 'not_observed', 'request': record.get('request')}
    result['can_retry'] = result['status'] in ('failed', 'interrupted', 'cancelled')
    if record.get('cancel_requested') and result['status'] in ('queued', 'running'):
        result['status'] = 'cancel_requested'
    if record.get('log'):
        path = Path(record['log'])
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 16000))
                result['log_tail'] = stream.read(16000).decode(errors='replace')
    destination = (record.get('inputs') or {}).get('destination')
    if store and destination and result['status'] == 'succeeded':
        path = Path(destination)
        if path.parent == store.root / 'artifacts' and path.is_file():
            try: store.transfer_path(path.name, 'artifacts')
            except ValueError: pass
            else: result['artifact_id'] = path.name
    return result


def snapshot(request, case_id=''):
    from forsic_plugin.setup import local_admin
    home, store, manager = scope(request)
    catalog = manager.catalog()
    selection = hub.selection_snapshot(home)
    case = selected_case(case_id, home)
    case_state = manager.case_state(case) if case else None
    refs = references(manager)
    builder_jobs = builder_manager(home).list_jobs()
    selected_environment_id = ((case_state or {}).get('current') or {}).get('environment_id') if case else catalog.get('default_environment_id')
    try: selected_environment = manager.manifest(selected_environment_id, verify=False) if selected_environment_id else {}
    except (OSError, ValueError): selected_environment = {}
    from forsic_plugin.extension_runtime import dependency_status
    packs = []
    for pack in hub.list_packs(home):
        manifest = pack['manifest']
        referenced = any(item.get('packs', {}).get(manifest['id'], {}).get('version') == manifest['version'] for item in refs)
        default_reference = selection['selection']['packs'].get(manifest['id'], {}).get('version') == manifest['version']
        receipts = [receipt for job in builder_jobs for receipt in job.get('tests', [])
                    if receipt.get('pack_sha256') == pack['sha256']]
        tested = bool(manifest.get('tools')) and all(any(receipt.get('tool') == tool['name']
                 and receipt.get('passed') is True for receipt in receipts) for tool in manifest.get('tools', []))
        functional = 'verified' if tested else 'not_observed'
        valid = pack.get('validation_status') != 'invalid' and bool(pack.get('sha256'))
        tools = [{**tool, 'availability': dependency_status(pack, environment=selected_environment, tool=tool)} for tool in manifest.get('tools', [])] if valid else []
        packs.append({**manifest, 'name': manifest['display_name'], 'sha256': pack['sha256'],
                      'status': 'registered' if valid else 'unavailable', 'functional_status': functional,
                      'tools': tools, 'availability': {'available': valid and all(tool['availability']['available'] for tool in tools)},
                      'validation': {'structure': 'verified' if valid else 'invalid', 'functional': functional,
                                     'receipts': receipts, 'error': pack.get('error')},
                      'builtin': False, 'referenced': referenced or default_reference})
    jobs = [public_job(item, store) for item in catalog['operations']]
    inventory = {}
    for item in catalog['operations']:
        if item.get('kind') == 'inventory' and item.get('state') == 'succeeded':
            inventory[item['inputs']['environment_id']] = item['result'].get('installed', {})
    environments = []
    for item in catalog['environments']:
        identity = item['environment_id']
        measured = inventory.get(identity) or item.get('plan', {}).get('installed', {})
        tools = [{'name': name, 'version': version, 'method': kind, 'update_supported': kind in ('apt', 'python')}
                 for kind in ('apt', 'python', 'isolated_python') for name, version in measured.get(kind, {}).items()]
        tools.extend({'name': name, 'version': entry.get('output') if entry.get('exit_code') == 0 else None, 'method': 'release_binary',
                      'update_supported': True if name in ('hayabusa', 'yr', 'gitleaks') else None,
                      'installation_status': entry.get('state')} for name, entry in measured.get('binaries', {}).items())
        environments.append({**item, 'id': identity, 'status': item.get('state', 'ready'),
            'platform': next((image.get('platform') for image in item.get('images', {}).values()), None),
            'validation': item.get('checks'), 'tools': tools, 'inventory': measured, 'inventory_status': 'measured' if measured else 'not_observed'})
    saved = store.read(store.root / 'runtime.json', {}) or {}
    identity = {'default_revision': catalog['revision'], 'default_environment_id': catalog['default_environment_id'],
                'packs_revision': selection['revision'], 'case_id': case_id, 'case_state': case_state,
                'packs': [(item['id'], item['version'], item['sha256']) for item in packs],
                'environments': [(item['environment_id'], item.get('state')) for item in catalog['environments']]}
    current = (case_state or {}).get('current') or {}
    from forsic_plugin.tenants import current_scope
    tenant=current_scope()
    return {**({'tenant_id':tenant['tenant_id'],'profile':tenant['profile_name']} if tenant is not None else {}),
        'revision': digest(identity), 'local_admin': local_admin(request),
        'runtime_pending': saved.get('pending', False),
        'environments': {'items': environments, 'default_environment_id': catalog['default_environment_id'],
            'default_revision': catalog['revision'], 'case': None if case_state is None else {
                'revision': case_state['revision'], 'environment_id': current.get('environment_id'),
                'extensions': current.get('extensions', {}).get('packs', {}), 'pending': case_state.get('pending'),
                'legacy_history': current.get('legacy_history', 'not retroactively pinned')},
            'updates': [clean(item['result']) for item in catalog['operations'] if item.get('kind') == 'check_updates' and item.get('result')]},
        'packs': {'items': packs, 'selection': {'revision': selection['revision'], 'packs': selection['selection']['packs']}},
        'builder': {'jobs': [public_job(item) for item in builder_jobs]}, 'jobs': jobs}


@contextmanager
def idle_scope(home):
    from hermes_cli.backend_retirement import retirement
    with retirement.idle_boundary() as idle:
        if not idle:
            yield False
            return
        try: safe = runtime._durable_idle(home)
        except (OSError, sqlite3.Error): safe = False
        if not safe:
            yield False
            return
        with ExitStack() as stack:
            for _, session in runtime._live_sessions(home):
                lock = session.get('history_lock')
                if lock is None or not lock.acquire(blocking=False):
                    yield False
                    return
                stack.callback(lock.release)
            yield True


def refresh_runtime(home, store):
    from forsic_plugin.extension_runtime import refresh_extensions
    from tui_gateway import server
    result = refresh_extensions(home)
    if result.get('registered') is not True:
        store.write(store.root / 'runtime.json', {'pending': True, 'reason': result.get('reason', '등록 경로 미확인')})
        return False
    try:
        server._refresh_live_sessions(home)
        verified = runtime._applied(home, runtime._live_sessions(home))
    except Exception:
        verified = False
    store.write(store.root / 'runtime.json', {'pending': not verified})
    return verified


FIELDS = {
    'env.register': set(), 'env.inventory': {'environment_id'}, 'env.check_updates': {'environment_id'},
    'env.prepare': {'plan'}, 'env.plan_dependencies': {'base_environment_id', 'id', 'version', 'expected_sha256'}, 'env.set_default': {'environment_id'},
    'env.apply': {'environment_id', 'extensions', 'reason'}, 'env.rollback': {'environment_id', 'reason'},
    'env.apply_pending': set(), 'env.export': {'environment_id'}, 'env.import': {'upload_id'},
    'hub.preview': {'upload_id'}, 'hub.import': {'upload_id', 'expected_sha256'},
    'hub.select': {'scope', 'selection'}, 'hub.export': {'id', 'version', 'scope', 'environment_id'}, 'hub.delete': {'id', 'version'},
    'operation.cancel': {'target_operation_id'}, 'operation.retry': {'target_operation_id'},
    'builder.create': {'repository_url', 'source_upload_id', 'request'},
    **{f'builder.{name}': {'job_id'} for name in ('start', 'resume', 'cancel', 'export', 'import')},
}


def execute(body, request, home, store, manager, fields):
    action, op = body.action, body.operation_id
    case = selected_case(body.case_id, home)
    if action == 'env.plan_dependencies':
        pack = hub.load_pack(fields['id'], fields['version'], home, fields['expected_sha256'])
        plan = manager.plan_dependencies(fields['base_environment_id'], pack['manifest'].get('dependencies', []))
        return {'status': 'preview', 'plan': plan, 'functional_checks_required': True}
    if action in ('env.register', 'env.inventory', 'env.check_updates', 'env.prepare', 'env.import', 'operation.cancel', 'operation.retry'):
        if action == 'env.register': job = manager.register(operation_id=op)
        elif action == 'env.inventory': job = manager.inventory(fields['environment_id'], operation_id=op)
        elif action == 'env.check_updates': job = manager.check_updates(fields['environment_id'], operation_id=op)
        elif action == 'env.prepare': job = manager.prepare(fields['plan'], operation_id=op)
        elif action == 'env.import': job = manager.import_environment(store.upload_path(fields['upload_id'], 'environment'), operation_id=op)
        elif action == 'operation.cancel': job = manager.cancel(fields['target_operation_id'])
        else: job = manager.retry(fields['target_operation_id'], new_operation_id=op)
        return {'status': job['state'], 'job': public_job(job, store)}
    if action in ('env.set_default', 'env.apply', 'env.rollback', 'env.apply_pending', 'hub.select', 'hub.import', 'hub.delete', 'builder.import'):
        with idle_scope(home) as safe:
            extra = {}
            if action == 'env.set_default':
                manager.set_default(fields['environment_id'], expected_revision=manager.catalog()['revision'], operation_id=op)
            elif action in ('env.apply', 'env.rollback'):
                if case is None: raise ValueError('변경할 사건을 선택해주세요.')
                extensions = fields.get('extensions')
                if extensions is not None: extensions = hub.normalize_selection({'packs': extensions}, home)
                manager.select_case(case, fields['environment_id'], extensions=extensions,
                    expected_revision=manager.case_state(case)['revision'], operation_id=op,
                    reason=fields.get('reason', ''), sessions_safe=lambda: safe)
            elif action == 'env.apply_pending':
                if case is not None: manager.apply_pending(case, sessions_safe=lambda: safe)
            elif action == 'hub.select':
                selected = hub.normalize_selection({'packs': fields['selection']}, home)
                if fields['scope'] == 'default':
                    hub.save_selection(selected, hub.selection_snapshot(home)['revision'], op, home)
                elif fields['scope'] == 'case' and case is not None:
                    manager.select_case(case, extensions=selected, expected_revision=manager.case_state(case)['revision'],
                        operation_id=op, reason='사용자가 사건 확장팩 선택을 변경함', sessions_safe=lambda: safe)
                else: raise ValueError('올바른 선택 범위와 사건을 지정해주세요.')
            elif action == 'hub.import':
                hub.import_pack(store.upload_path(fields['upload_id'], 'pack'), home, fields['expected_sha256'])
            elif action == 'builder.import':
                extra['result'] = clean(builder_manager(home).import_pack(fields['job_id']))
            elif action == 'hub.delete':
                # No deleting versions while sessions may still retain their schema.
                if not safe: raise HTTPException(409, '조사 작업이 끝난 뒤 미참조 버전을 정리해주세요.')
                hub.delete_pack(fields['id'], fields['version'], home, references=references(manager))
            applied = refresh_runtime(home, store) if safe else False
            if not safe: store.write(store.root / 'runtime.json', {'pending': True})
            return {'status': 'applied' if applied else 'pending', **extra}
    if action == 'hub.preview':
        return {'status': 'preview', 'preview': hub.preview_pack(store.upload_path(fields['upload_id'], 'pack'))}
    if action in ('hub.export', 'env.export', 'builder.export'):
        token = uuid.uuid4().hex
        destination = store.artifact_destination(token)
        if action == 'hub.export' and fields.get('scope', 'pack') == 'pack':
            hub.export_pack(fields['id'], fields['version'], destination, home)
            store.register_artifact(token)
            return {'status': 'completed', 'artifact_id': token}
        if action == 'env.export' or action == 'hub.export' and fields.get('scope') == 'offline':
            extensions = {}
            choices = hub.selection_snapshot(home)['selection']['packs']
            if case is not None:
                state = manager.case_state(case)
                choices = ((state.get('current') or {}).get('extensions') or {}).get('packs', {})
            if action == 'hub.export':
                pack = hub.load_pack(fields['id'], fields['version'], home)
                choices = {fields['id']: {'version': fields['version'], 'sha256': pack['sha256'], 'enabled': True}}
            stage = store.root / 'export-packs' / token
            stage.mkdir(parents=True, mode=0o700)
            for index, (pack_id, choice) in enumerate(choices.items()):
                hub.load_pack(pack_id, choice['version'], home, choice['sha256'])
                path = stage / f'pack-{index}.zip'
                hub.export_pack(pack_id, choice['version'], path, home)
                extensions[path.name] = path
            job = manager.export_environment(fields['environment_id'], destination, operation_id=op, extensions=extensions)
            store.write(destination.with_suffix('.json'), {'operation_id': op, 'pending': True})
            return {'status': job['state'], 'job': public_job(job, store)}
        if action != 'builder.export': raise ValueError('팩 파일 또는 오프라인 묶음 범위를 선택해주세요.')
        result = builder_manager(home).export(fields['job_id'], destination)
        store.register_artifact(token)
        return {'status': 'completed', 'artifact_id': token, 'result': clean(result)}
    builder = builder_manager(home)
    if action == 'builder.create':
        if bool(fields.get('repository_url')) == bool(fields.get('source_upload_id')):
            raise ValueError('저장소 URL 또는 소스 압축파일 하나를 지정해주세요.')
        source = store.upload_path(fields['source_upload_id'], 'source') if fields.get('source_upload_id') else None
        result = builder.create(repository_url=fields.get('repository_url'), source_archive=source,
                                request=fields['request'], operation_id=op)
    elif action in ('builder.start', 'builder.resume'):
        function = builder.start if action == 'builder.start' else builder.resume
        result = function(fields['job_id'], profile=getattr(request.app.state, 'initial_profile', 'default') or 'default')
    elif action == 'builder.cancel': result = builder.cancel(fields['job_id'])
    elif action == 'builder.import': result = builder.import_pack(fields['job_id'])
    else: raise ValueError('지원하지 않는 관리 작업입니다.')
    return {'status': result.get('status') or result.get('state') or 'not_observed', 'result': clean(result)}


def api_error(exc):
    if isinstance(exc, HTTPException): raise exc
    if isinstance(exc, OverflowError): raise HTTPException(413, str(exc)) from None
    if isinstance(exc, FileNotFoundError): raise HTTPException(404, '등록된 관리 파일을 찾을 수 없습니다.') from None
    if isinstance(exc, PermissionError): raise HTTPException(403, '관리 파일 접근 권한을 확인해주세요.') from None
    if isinstance(exc, (ValueError, KeyError, TypeError, AttributeError, OSError, sqlite3.Error)):
        raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else '관리 자료나 입력을 검증하지 못했습니다.') from None
    raise exc


@router.get('/management')
def management_get(request: Request, case_id: str = ''):
    try: return JSONResponse(snapshot(request, case_id), headers={'Cache-Control': 'no-store'})
    except Exception as exc: api_error(exc)


@router.post('/management/action')
def management_action(body: ActionInput, request: Request):
    try:
        home, store, manager = scope(request, write=True)
        fields = body.model_extra or {}
        if body.action not in FIELDS or set(fields) - FIELDS[body.action]:
            raise ValueError('지원하지 않는 작업 또는 입력 필드입니다.')
        fingerprint = digest({'action': body.action, 'case_id': body.case_id, 'fields': fields})
        with store.lock():
            previous = store.operation(body.operation_id)
            if previous:
                if previous['fingerprint'] != fingerprint: raise HTTPException(409, '요청 식별자를 다른 변경에 재사용할 수 없습니다.')
                result = previous['response']
            else:
                if body.expected_revision != snapshot(request, body.case_id)['revision']:
                    raise HTTPException(409, '관리 구성이 바뀌었습니다. 새로고침 후 다시 검토해주세요.')
                result = execute(body, request, home, store, manager, fields)
                store.save_operation(body.operation_id, fingerprint, result)
        return {**result, 'snapshot': snapshot(request, body.case_id)}
    except Exception as exc: api_error(exc)


@router.post('/management/upload')
async def management_upload(request: Request, file: UploadFile = File(...), kind: str = Form(...)):
    try:
        _, store, _ = scope(request, write=True)
        return await store.upload(file, kind)
    except Exception as exc: api_error(exc)
    finally: await file.close()


@router.get('/management/files/{artifact_id}')
def management_download(artifact_id: str, request: Request):
    try:
        _, store, manager = scope(request, write=True)
        path = store.transfer_path(artifact_id, 'artifacts')
        metadata = store.read(path.with_suffix('.json'))
        if metadata and metadata.get('pending'):
            job = manager.operation(metadata['operation_id'])
            if job['state'] != 'succeeded': raise HTTPException(409, '내보내기 작업이 아직 완료되지 않았습니다.')
            raw = job['result']
            if Path(raw['path']) != path: raise ValueError('내보내기 파일 정체성이 일치하지 않습니다.')
            metadata = {'bytes': path.stat().st_size, 'sha256': raw['sha256']}
            store.verify(path, metadata, 32 * 1024**3)
        else:
            path, metadata = store.artifact(artifact_id)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        stream = os.fdopen(descriptor, 'rb')
        # Verify the same open inode used by the response, not a reopened path.
        if hashlib.file_digest(stream, 'sha256').hexdigest() != metadata['sha256']:
            stream.close()
            raise ValueError('내보내기 파일 내용이 변경되었습니다.')
        stream.seek(0)
        def chunks():
            try:
                while chunk := stream.read(1024 * 1024): yield chunk
            finally: stream.close()
        return StreamingResponse(chunks(), media_type='application/octet-stream', headers={
            'Content-Disposition': f'attachment; filename="forsic-{artifact_id}.archive"',
            'Content-Length': str(metadata['bytes']),
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    except Exception as exc: api_error(exc)
