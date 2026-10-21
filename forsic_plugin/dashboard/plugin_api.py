from pathlib import Path
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, FileResponse, Response, JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, SecretStr, ConfigDict, StrictBool
from hermes_cli.config import load_config
from forsic_plugin.evidence import Case

from forsic_plugin.dashboard.tenant_api import request_scope

router = APIRouter(dependencies=[Depends(request_scope)])


class SetupRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def private_validation(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Pydantic's default response echoes invalid input values,
                # including malformed secrets or caller-supplied extra field names.
                return JSONResponse({'detail': [{'loc': error['loc'][:1], 'msg': '입력값을 확인해주세요.', 'type': error['type']} for error in exc.errors()]}, status_code=422)
        return private_validation


setup_router = APIRouter(route_class=SetupRoute)


class SetupInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    llm_base_url: str = Field(max_length=2048)
    llm_model: str = Field(max_length=200)
    llm_context_length: int | None = Field(default=None, strict=True, ge=64000, le=2147483647)
    llm_api_key: SecretStr = SecretStr('')
    clear_llm_key: bool = Field(default=False, strict=True)
    evidence_directory: str = Field(max_length=4096)
    evidence_directory_label: str = Field(default='', max_length=256)
    hosting_mode: Literal['local', 'private_lan', 'online']
    private_lan_host: str = Field(default='', max_length=128)
    public_url: str = Field(default='', max_length=2048)
    login_username: str = Field(default='', max_length=128)
    login_password: SecretStr = SecretStr('')


class ModelCheckInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    llm_base_url: str = Field(max_length=2048)
    llm_model: str = Field(max_length=200)
    llm_api_key: SecretStr = SecretStr('')


def require_setup_admin(request):
    from forsic_plugin.setup import local_admin
    if not local_admin(request):
        raise HTTPException(403, '이 컴퓨터에서 localhost 주소로 접속해 설정해주세요.')


@setup_router.get('/setup')
def setup_get(request: Request):
    from forsic_plugin.setup import public
    return public(request)


@setup_router.put('/setup')
def setup_save(body: SetupInput, request: Request):
    from forsic_plugin.setup import save
    require_setup_admin(request)
    values = body.model_dump(exclude_unset=True)
    for field in ('llm_api_key', 'login_password'):
        if field in values:
            values[field] = getattr(body, field).get_secret_value()
    try:
        return save(values, request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@setup_router.post('/setup/check-model')
def setup_check_model(body: ModelCheckInput, request: Request):
    from forsic_plugin.setup import check_model
    require_setup_admin(request)
    values = body.model_dump(exclude_unset=True)
    if 'llm_api_key' in values:
        values['llm_api_key'] = body.llm_api_key.get_secret_value()
    try:
        return check_model(values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


router.include_router(setup_router)


class CapabilitiesInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_os: Literal['all', 'windows', 'macos', 'linux'] = 'all'
    tools: dict[str, StrictBool] = Field(default_factory=dict)
    skills: dict[str, StrictBool] = Field(default_factory=dict)
    expected_revision: str | None = Field(default=None, max_length=64, strict=True)


@router.get('/capabilities')
def capabilities_get(request: Request):
    from forsic_plugin.capability_runtime import snapshot
    try:
        return snapshot(request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.put('/capabilities')
def capabilities_save(body: CapabilitiesInput, request: Request):
    from forsic_plugin.capability_runtime import save_and_apply
    require_setup_admin(request)
    try:
        return save_and_apply(body.model_dump(exclude={'expected_revision'}), request, body.expected_revision)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except PermissionError:
        raise HTTPException(403, '관리자가 관리하는 설정은 변경할 수 없습니다.') from None


class ConnectionInput(BaseModel):
    llm_api_key: SecretStr = SecretStr('')
    clear_llm_key: StrictBool = False
    llm_base_url: str = Field(max_length=2048)
    llm_model: str = Field(max_length=200)
    llm_context_length: int | None = Field(default=None, strict=True, ge=64000, le=2147483647)
    ren_llm_enabled: StrictBool | None = None
    ren_llm_base_url: str | None = Field(default=None, max_length=2048)
    ren_llm_model: str | None = Field(default=None, max_length=200)
    ren_llm_api_key: SecretStr = SecretStr('')
    clear_ren_llm_key: StrictBool = False
    jev_base_url: str = Field(default='', max_length=2048)
    gti_enabled: bool = False
    gti_public_network: bool = False
    gti_api_key: SecretStr = SecretStr('')
    clear_gti_key: bool = False
    telegram_assistant_key: SecretStr = SecretStr('')
    telegram_user_key: SecretStr = SecretStr('')
    clear_telegram_assistant_key: bool = False
    clear_telegram_user_key: bool = False
    telegram_group: str | None = Field(default=None, max_length=256)


connection_router = APIRouter(route_class=SetupRoute)


@connection_router.get('/connections')
def connections_get():
    from forsic_plugin.connections import public
    return public()


@connection_router.put('/connections')
def connections_save(body: ConnectionInput):
    from forsic_plugin.connections import save
    values = body.model_dump(exclude_unset=True)
    for field in ('llm_api_key','gti_api_key','telegram_assistant_key','telegram_user_key','ren_llm_api_key'):
        if field in values:
            values[field] = getattr(body,field).get_secret_value()
    try:
        return save(values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


class ConnectionTestInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    connection: Literal['main', 'ren']
    base_url: str = Field(max_length=2048)
    model: str = Field(max_length=200)
    api_key: SecretStr = SecretStr('')


@connection_router.post('/connections/test')
def connections_test(body: ConnectionTestInput, request: Request):
    from forsic_plugin.connections import test_connection
    require_setup_admin(request)
    values = body.model_dump(exclude_unset=True)
    if 'api_key' in values:
        values['api_key'] = body.api_key.get_secret_value()
    try:
        return test_connection(values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


router.include_router(connection_router)


@router.post('/connections/gti-check')
def connections_check():
    from forsic_plugin.intelligence import check_connection
    return check_connection()


class ArchiveInput(BaseModel):
    title: str = Field(default='', max_length=240)
    tags: list[str] = Field(default_factory=list, max_length=20)


def library_call(args, case_id=''):
    from forsic_plugin.case_library import CaseLibrary
    try:
        from forsic_plugin.tenants import current_scope
        scope=current_scope()
        case=selected_case(case_id) if case_id or args.get('action')=='save' or scope is None else None
        return CaseLibrary(case,scope=scope).invoke(args)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/library')
def library_search(query: str = '', offset: int = 0, exclude_current: bool = False, case_id: str = ''):
    return library_call({'action': 'search', 'query': query, 'offset': offset, 'exclude_current': exclude_current}, case_id)


@router.post('/library')
def library_save(body: ArchiveInput, case_id: str = ''):
    # Explicit UI action, no model call and no automatic completion claim.
    return library_call({'action': 'save', 'title': body.title, 'tags': body.tags}, case_id)


@router.get('/library/{archive_id}')
def library_get(archive_id: str, revision: str = '', offset: int = 0, case_id: str = ''):
    return library_call({'action': 'get', 'archive_id': archive_id, 'revision': revision, 'offset': offset}, case_id)


@router.get('/library/{archive_id}/sources/{source_id}')
def library_source(archive_id: str, source_id: str, revision: str, case_id: str = ''):
    return library_call({'action': 'source', 'archive_id': archive_id, 'source_id': source_id, 'revision': revision}, case_id)


def directory():
    from hermes_cli.config import get_hermes_home
    from forsic_plugin.intake import Intake
    from forsic_plugin.investigations import Investigations
    from forsic_plugin.tenants import current_scope
    scope=current_scope()
    if scope is not None:return Investigations(scope['profile_home'],scope['intake_root'])
    settings = load_config()['plugins']['entries']['forsic']['settings']
    return Investigations(get_hermes_home(), Intake(settings.get('intake_root')).root)


@router.get('/investigations')
def investigations():
    import sqlite3
    catalog = directory()
    availability = catalog.new_investigation()
    try:
        cases = catalog.public()
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError):
        # Keep the admission failure visible even when the directory itself is
        # unreadable; an empty list must never imply that new intake is safe.
        cases = []
        availability = {'allowed': False, 'reason': '기존 조사 목록을 읽을 수 없어 새 조사를 시작할 수 없습니다. 저장 상태를 확인해주세요.',
                        'active_case': None}
    return {'cases': cases, 'new_investigation': availability}


class ConversationInput(BaseModel):
    request_id: UUID


class InvestigationCloseInput(ConversationInput):
    model_config = ConfigDict(extra='forbid')
    profile: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=256)
    goal_id: str = Field(min_length=1, max_length=128)
    confirmed: bool = Field(strict=True)


@router.post('/investigations/{case_id}/close')
def investigation_close(case_id: str, body: InvestigationCloseInput, request: Request):
    import sqlite3
    from hermes_cli.profiles import get_profile_dir
    from forsic_plugin.investigations import RequestConflict
    from urllib.parse import urlsplit
    from forsic_plugin.tenants import current_scope
    tenant=current_scope()
    served = tenant['profile_name'] if tenant is not None else getattr(request.app.state, 'initial_profile', 'default') or 'default'
    if body.profile != served:
        raise HTTPException(403, '이 화면에 연결된 프로필만 종료할 수 있습니다.')
    if body.confirmed is not True:
        raise HTTPException(409, '조사 종료를 확인해주세요.')
    origin = request.headers.get('origin')
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            raise HTTPException(403, '같은 조사 화면에서 종료를 요청해주세요.') from None
        if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get('host') or parsed.path or parsed.query or parsed.fragment:
            raise HTTPException(403, '같은 조사 화면에서 종료를 요청해주세요.')
    try:
        catalog = directory()
        if get_profile_dir(body.profile).resolve() != catalog.home.resolve():
            raise RequestConflict('프로필과 조사 저장소가 일치하지 않습니다.')
        return catalog.close_investigation(body.request_id, case_id=case_id,
            session_id=body.session_id, profile=body.profile, goal_id=body.goal_id, confirmed=body.confirmed)
    except (RequestConflict, ValueError, OSError, KeyError, TypeError, AttributeError, RuntimeError, sqlite3.Error):
        raise HTTPException(409, '조사 종료 상태를 확인하지 못했습니다. 잠시 후 목록을 새로고침하고 같은 요청을 다시 시도해주세요.') from None


class OnboardingInput(BaseModel):
    purpose: Literal['comprehensive', 'focused', 'report', 'timeline', 'custom']
    environment: Literal['windows', 'linux', 'macos', 'mixed', 'unknown']
    question: str = Field(default='', max_length=4000)


class InvestigationInput(ConversationInput):
    path: str = Field(min_length=1, max_length=4096)
    onboarding: OnboardingInput | None = None


def evidence_access(request=None):
    from forsic_plugin.onboarding import EvidenceAccess
    # Restricted transport policy belongs to the server, independently of the
    # selected tenant's fresh profile and its optional evidence preferences.
    settings = getattr(request.state, 'forsic_remote_evidence_settings', None) if request is not None else None
    force_remote = settings is not None
    if settings is None:
        settings = load_config().get('plugins', {}).get('entries', {}).get('forsic', {}).get('settings', {})
    if not isinstance(settings, dict):
        raise ValueError('설정된 자료 위치를 확인해주세요.')
    peer = request.client.host if request is not None and request.client else ''
    if not force_remote and settings.get('online_hosting'):
        from forsic_plugin.setup import local_admin
        force_remote = not local_admin(request)
    return EvidenceAccess(settings, peer, force_remote=force_remote)


def create_ui_conversation(body, *, case_id='', path='', request=None):
    initial = load_config().get('plugins', {}).get('entries', {}).get('forsic', {}).get('settings', {})
    if initial.get('setup_completed', True) is False:
        raise HTTPException(409, '초기 설정을 저장한 뒤 포식이를 다시 시작해주세요.')
    from forsic_plugin.investigations import RequestConflict
    try:
        catalog = directory()
        access = evidence_access(request)
        root_limit = None
        if access.restricted:
            from forsic_plugin.onboarding import EvidenceBrowser
            selected = catalog.get(case_id=case_id) if case_id else None
            if case_id and selected is None:
                raise LookupError('사건을 찾을 수 없습니다.')
            EvidenceBrowser(catalog.home, catalog.intake, access).connection(selected['scope'] if selected else path)
            root_limit = access.root()
        onboarding = getattr(body, 'onboarding', None)
        options = {'evidence_root_limit': root_limit} if root_limit is not None else {}
        return catalog.create_conversation(body.request_id, case_id=case_id, path=path,
            onboarding=onboarding.model_dump() if onboarding is not None else None, **options)
    except RequestConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except (ValueError, FileNotFoundError, NotADirectoryError):
        raise HTTPException(400, '접근할 수 있는 증거 파일이나 폴더의 전체 경로를 확인해주세요.') from None
    except PermissionError:
        raise HTTPException(403, '선택한 경로에 접근할 권한이 없습니다.') from None
    except OSError:
        raise HTTPException(400, '접근할 수 있는 증거 파일이나 폴더의 전체 경로를 확인해주세요.') from None


@router.post('/investigations')
def investigation_create(body: InvestigationInput, request: Request = None):
    return create_ui_conversation(body, path=body.path, request=request)


@router.get('/evidence-browser')
def evidence_browser(request: Request = None, path: str = Query('', max_length=4096), offset: int = Query(0, ge=0, lt=10000)):
    from forsic_plugin.onboarding import EvidenceBrowser
    catalog = directory()
    try:
        return EvidenceBrowser(catalog.home, catalog.intake, evidence_access(request)).browse(path, offset)
    except PermissionError:
        raise HTTPException(403, '이 폴더에는 접근할 수 없습니다. 증거 폴더를 선택해주세요.') from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except OSError:
        raise HTTPException(400, '접근할 수 있는 증거 폴더의 전체 경로를 확인해주세요.') from None


@router.get('/evidence-connection')
def evidence_connection(request: Request = None, path: str = Query(..., min_length=1, max_length=4096)):
    from forsic_plugin.onboarding import EvidenceBrowser
    catalog = directory()
    try:
        return EvidenceBrowser(catalog.home, catalog.intake, evidence_access(request)).connection(path)
    except PermissionError:
        raise HTTPException(403, '이 자료에는 접근할 수 없습니다. 다른 파일이나 폴더를 선택해주세요.') from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except OSError:
        raise HTTPException(400, '접근할 수 있는 자료 파일이나 폴더를 선택해주세요.') from None


@router.get('/evidence-sources')
def evidence_sources(request: Request = None):
    from forsic_plugin.onboarding import configured_sources
    try:
        access = evidence_access(request)
        result = configured_sources(access.path)
        if access.described:
            result.update(access.metadata())
            if access.restricted and not result['available']:
                result['sources'] = []
            for source in result['sources']:
                source['display_path'] = access.display(source['path'])
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/investigations/{case_id}/conversations')
def investigation_conversations(case_id: str):
    item = directory().get(case_id=case_id)
    if item is None:
        raise HTTPException(404, '사건을 찾을 수 없습니다.')
    return {'case_id': case_id, 'conversations': item['conversations']}


@router.post('/investigations/{case_id}/conversations')
def investigation_conversation_create(case_id: str, body: ConversationInput, request: Request = None):
    return create_ui_conversation(body, case_id=case_id, request=request)


def selected_case(case_id='', session_id='', *, read_only=False):
    if not case_id and not session_id:
        return current_case(read_only=read_only)
    item = directory().get(case_id=case_id, session_id=session_id)
    if item is None:
        raise HTTPException(404, '이 대화에 연결된 사건이 없습니다.')
    from forsic_plugin.tenants import current_scope,validate_case
    scope=current_scope()
    if scope is not None:validate_case(scope,item['manifest'])
    return Case(item['manifest'], read_only=read_only)


def current_case(*, read_only=False):
    import json
    from forsic_plugin.tenants import current_scope,TenantRegistry
    if current_scope() is not None or TenantRegistry().path.exists():raise HTTPException(400,'선택한 테넌트의 사건 또는 대화를 명시해주세요.')
    settings = load_config()["plugins"]["entries"]["forsic"]["settings"]
    from forsic_plugin.intake import Intake
    pointer = Intake(settings.get('intake_root')).root / 'current.json'
    manifest = json.loads(pointer.read_text())['manifest'] if pointer.exists() else settings['case_file']
    return Case(manifest, read_only=read_only)


@router.get("/status")
def status(case_id: str = '', session_id: str = ''):
    from forsic_plugin.notes import current_notes
    case = selected_case(case_id, session_id)
    files = [*case.output.glob('report-*.html'), *case.output.glob('report-*.md')]
    return {"case": case.info({}), "events": case.events(), 'notes': current_notes(case), "reports": [p.name for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]}


@router.get("/evidence/{eid}")
def evidence(eid: str, case_id: str = ''):
    result = selected_case(case_id).event(eid)
    if result is None:
        raise HTTPException(404, "Unknown evidence result")
    return result


@router.get('/indicators')
def indicator_list(case_id: str, query: str = '', status: str = '', offset: int = 0):
    from forsic_plugin.indicators import invoke
    try:
        return invoke(selected_case(case_id), {'action':'list','query':query,'status':status,'offset':offset,'limit':25})
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/indicator-export/{format}')
def indicator_export(format: str, case_id: str):
    from forsic_plugin.indicators import export_content
    if format not in ('json','csv'):
        raise HTTPException(400, 'JSON 또는 CSV를 선택해주세요.')
    content = export_content(selected_case(case_id), format)
    return Response(content=content, media_type='application/json' if format=='json' else 'text/csv; charset=utf-8',
                    headers={'Content-Disposition':f'attachment; filename="forsic-indicators.{format}"',
                             'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})


@router.get("/reports/{name}", response_class=PlainTextResponse)
def report(name: str, case_id: str = ''):
    case = selected_case(case_id)
    if Path(name).name != name or not name.startswith("report-") or not name.endswith(".md"):
        raise HTTPException(400, "Invalid report name")
    path = case.output / name
    if not path.is_file():
        raise HTTPException(404, "Report not found")
    return path.read_text(encoding="utf-8")


@router.get("/report-text/{name}")
def report_text(name: str, case_id: str = ''):
    import json
    case = selected_case(case_id)
    if name.endswith('.html'):
        path = output_path(name, case_id)
        candidates = list(case.output.glob('snapshot-' + name.split('-')[1] + '*.json'))
        body = json.loads(candidates[0].read_text()) if len(candidates) == 1 else {}
        return {'name': name, 'html': path.read_text(), 'docx': name[:-5] + '.docx',
                'source_appendix': candidates[0].name if len(candidates) == 1 else None,
                'evidence_ids': [r['id'] for r in body.get('sources', [])]}
    with case.connect() as db:
        row = db.execute("SELECT data FROM events WHERE kind='tool_result' AND json_extract(data, '$.report')=? ORDER BY time DESC LIMIT 1", (name,)).fetchone()
    return {"name": name, "markdown": report(name, case_id), "evidence_ids": json.loads(row['data']).get('evidence_ids', []) if row else []}


def output_path(name, case_id=''):
    import re
    if not re.fullmatch(r'(report-[a-f0-9]{16}-(executive|analyst)\.(html|docx)|snapshot-[a-f0-9]{64}\.json)', name):
        raise HTTPException(400, 'Invalid output name')
    case = selected_case(case_id)
    path = case.output / name
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, 'Output not found')
    return path


@router.get('/download/{name}')
def download(name: str, case_id: str = ''):
    path = output_path(name, case_id)
    return FileResponse(path, filename=path.name, headers={'X-Content-Type-Options': 'nosniff'})


@router.get('/report-driven')
def report_driven(case_id: str = ''):
    from forsic_plugin.report_driven.host import current
    from forsic_plugin.report_driven.gap_audit import audit
    from forsic_plugin.report_driven.views import bundles
    from forsic_plugin.report_driven.current_judgment import current_judgment
    case = selected_case(case_id, read_only=True)
    state = current(case)
    return {'state': state, 'audit': audit(state), 'bundles': bundles(case),
            'judgments': {q['id']: current_judgment(state, q) for q in state['questions']}}


@router.get('/report-bundles/{bundle_id}/{name}')
def report_artifact(bundle_id: str, name: str, case_id: str = ''):
    from forsic_plugin.report_driven.views import artifact
    try:
        path = artifact(selected_case(case_id), bundle_id, name)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(404, '보고서 묶음 또는 파일을 확인하지 못했습니다.') from exc
    return FileResponse(path, filename=path.name, headers={'X-Content-Type-Options':'nosniff', 'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; sandbox"})


@router.post('/report-bundles/{bundle_id}/word')
def report_word(bundle_id: str, case_id: str = ''):
    """Explicit format conversion of one saved version; never a model request."""
    import hashlib
    import os
    from forsic_plugin.report_driven.word_export import export_word
    try:
        exported = export_word(selected_case(case_id, read_only=True), bundle_id)
        descriptor = os.open(exported['path'], os.O_RDONLY | os.O_NOFOLLOW)
        stream = os.fdopen(descriptor, 'rb')
        try:
            if (os.fstat(descriptor).st_size != exported['bytes'] or
                    hashlib.file_digest(stream, 'sha256').hexdigest() != exported['sha256']):
                raise ValueError('Word output changed')
            stream.seek(0)
        except BaseException:
            stream.close()
            raise
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(409, '저장된 보고서를 Word로 변환하지 못했습니다. 보고서 버전과 보존 파일을 확인해주세요.') from exc
    def chunks():
        try:
            while chunk := stream.read(1024 * 1024):
                yield chunk
        finally:
            stream.close()
    return StreamingResponse(chunks(),
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        headers={'Content-Disposition': 'attachment; filename="integrated.docx"',
                 'Content-Length': str(exported['bytes']), 'Cache-Control': 'no-store',
                 'X-Content-Type-Options': 'nosniff', 'X-Forsic-Report-Version': bundle_id})


def investigation_scope(session_id, profile):
    from forsic_plugin.tenants import current_scope,validate_session
    tenant=current_scope()
    if tenant is not None:
        if profile!=tenant['profile_name']:raise HTTPException(403,'선택한 테넌트와 대화 프로필이 다릅니다.')
        try:validate_session(tenant,session_id)
        except (ValueError,PermissionError,LookupError):raise HTTPException(403,'선택한 테넌트의 대화가 아닙니다.') from None
    import sqlite3
    from forsic_plugin.investigation_view import resolve_view
    try:
        return resolve_view(session_id, profile)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        raise HTTPException(409, '대화와 사건 연결을 확인하지 못했습니다. 선택한 대화를 새로고침해주세요.') from exc


@router.get('/investigation-view')
def investigation_view(session_id: str = Query(..., min_length=1, max_length=256),
                       profile: str = Query(..., min_length=1, max_length=128)):
    import sqlite3
    from forsic_plugin.investigation_view import project_view
    resolved = investigation_scope(session_id, profile)
    try:
        from forsic_plugin.investigation_enrichment import apply_cached_display
        return apply_cached_display(resolved, project_view(resolved, profile=profile))
    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        raise HTTPException(409, '보존된 조사 기록을 확인하지 못했습니다. 다시 조회해주세요.') from exc


class InvestigationEnrichmentInput(BaseModel):
    session_id: str = Field(min_length=1, max_length=256)
    profile: str = Field(min_length=1, max_length=128)


@router.post('/investigation-enrich')
def investigation_enrich(body: InvestigationEnrichmentInput):
    import sqlite3
    from hermes_cli.config import get_hermes_home
    from hermes_cli.profiles import get_profile_dir
    from forsic_plugin.investigation_enrichment import enrich
    resolved = investigation_scope(body.session_id, body.profile)
    # This explicit action uses only the served profile's approved model route.
    if get_profile_dir(body.profile).resolve() != get_hermes_home().resolve():
        raise HTTPException(403, '현재 서비스에 연결된 조사 프로필을 선택해주세요.')
    try:
        return enrich(resolved, profile=body.profile)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        raise HTTPException(409, '요약 상태를 확인하지 못했습니다. 기존 기록은 유지됩니다.') from exc


@router.get('/source-page')
def source_page(case_id: str, source_id: str, source_version: str,
                pointer: str = '', offset: int = Query(0, ge=0), limit: int = Query(2048, ge=1, le=4096),
                session_id: str = '', profile: str = ''):
    """Exact case-bound retained source read; no tool invocation or ledger writes."""
    from forsic_plugin.report_driven.host import invoke
    if not case_id.strip():
        raise HTTPException(400, '사건을 명시해서 원문을 조회하세요.')
    try:
        if session_id or profile:
            from forsic_plugin.investigation_view import selected_source
            resolved = investigation_scope(session_id, profile)
            if resolved['case_id'] != case_id:
                raise HTTPException(409, '선택한 대화와 사건이 일치하지 않습니다.')
            return selected_source(resolved, source_id, source_version, pointer=pointer, offset=offset, limit=limit)
        return invoke(selected_case(case_id, read_only=True), {
            'action': 'source', 'source_id': source_id, 'source_version': source_version,
            'pointer': pointer, 'offset': offset, 'limit': limit})
    except (ValueError, KeyError, StopIteration) as exc:
        raise HTTPException(409, '출처 또는 버전이 일치하지 않습니다. 조사 관계를 새로고침하고 근거를 다시 선택하세요.') from exc


# These projections share the existing plugin authentication middleware and
# explicit native ownership resolver. Loading them cannot start an agent.
@router.get('/skills-ui-copy')
def skills_ui_copy():
    import json
    path = Path(__file__).resolve().parents[1] / 'ui' / 'skills.ko.json'
    return JSONResponse(json.loads(path.read_text(encoding='utf-8')),
                        headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


@router.get('/review-chat')
def review_chat(session_id: str = Query(..., min_length=1, max_length=256),
                profile: str = Query(..., min_length=1, max_length=128),
                case_id: str = Query('', max_length=256),
                cursor: str = Query('', max_length=2048)):
    from forsic_plugin.review_chat import review_chat as project
    resolved = investigation_scope(session_id, profile)
    if case_id and case_id != resolved['case_id']:
        raise HTTPException(409, '선택한 대화와 사건이 일치하지 않습니다.')
    try:
        result = project(resolved, profile=profile, session_id=session_id, cursor=cursor)
    except (ValueError, OSError, KeyError, TypeError):
        raise HTTPException(409, '검토 기록이 변경됐거나 확인되지 않습니다. 다시 조회해 주세요.') from None
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


class EvidenceSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evidence_item_id: str = Field(min_length=1, max_length=128)
    item_version: str = Field(min_length=1, max_length=128)


class EvidenceExportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(min_length=1, max_length=256)
    profile: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=256)
    source_snapshot_id: str = Field(min_length=1, max_length=128)
    selection: Literal['selected', 'all_eligible']
    items: list[EvidenceSelection] = Field(default_factory=list, max_length=250)
    include_sensitive_originals: bool = Field(default=False, strict=True)
    idempotency_key: str = Field(min_length=8, max_length=128)


def collection_scope(session_id, profile, case_id):
    resolved = investigation_scope(session_id, profile)
    if not case_id or case_id != resolved['case_id']:
        raise HTTPException(409, '선택한 대화와 사건이 일치하지 않습니다.')
    return resolved['case'], dict(case_id=case_id, profile_id=profile, session_id=session_id,
        conversation_id=resolved['conversation_id'], session_ids=resolved['session_lineage'])


def collection_call(function, *args, **kwargs):
    import sqlite3
    from forsic_plugin.evidence_collection import CollectionError
    try:
        return function(*args, **kwargs)
    except CollectionError as exc:
        messages = {403: '선택한 사건 또는 민감 원문에 접근할 수 없습니다.',
                    404: '선택한 증거 묶음을 찾지 못했습니다.',
                    409: '보존 기록이나 버전이 일치하지 않습니다. 목록을 새로고침해 주세요.',
                    413: '내보내기 허용량을 넘었습니다. 증거를 나누어 선택해 주세요.'}
        raise HTTPException(exc.status_code, messages.get(exc.status_code, '증거 선택과 요청값을 확인해 주세요.')) from None
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error):
        raise HTTPException(409, '보존 자료를 검증하지 못했습니다. 기존 조사 기록은 유지됩니다.') from None


def collection_local_admin(request, profile):
    from forsic_plugin.setup import local_admin
    from forsic_plugin.tenants import current_scope
    tenant=current_scope()
    if tenant is not None:return profile==tenant['profile_name'] and local_admin(request)
    served = getattr(request.app.state, 'initial_profile', 'default') or 'default'
    return profile == served and local_admin(request)


@router.get('/evidence-collection')
def evidence_collection(request: Request, session_id: str = Query(..., min_length=1, max_length=256),
                        profile: str = Query(..., min_length=1, max_length=128),
                        case_id: str = Query(..., min_length=1, max_length=256),
                        scope: Literal['case', 'conversation'] = 'case',
                        kind: str = Query('', max_length=64),
                        status: Literal['current', 'stale', 'all'] = 'current',
                        search: str = Query('', max_length=200), cursor: str = Query('', max_length=2048),
                        source_snapshot_id: str = Query('', max_length=128),
                        page_size: int = Query(25, ge=1, le=50)):
    from forsic_plugin.evidence_collection import list_collection
    case, context = collection_scope(session_id, profile, case_id)
    result = collection_call(list_collection, case, context=context, scope=scope, kind=kind or None,
        status=status, search=search, cursor=cursor or None, page_size=page_size,
        source_snapshot_id=source_snapshot_id or None)
    result.update(profile=profile, session_id=session_id, permissions={
        'sensitive_originals': collection_local_admin(request, profile),
        'max_sensitive_entries': 40, 'max_sensitive_bytes': 32768})
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@router.post('/evidence-collection/exports')
def evidence_export_create(body: EvidenceExportInput, request: Request):
    from urllib.parse import urlsplit
    from forsic_plugin.evidence_collection_export import create_export
    # The native middleware owns credentials. This additional write check also
    # protects direct router mounts against a supplied foreign browser origin.
    origin = request.headers.get('origin')
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            raise HTTPException(403, '같은 조사 화면에서 내보내기를 요청해 주세요.') from None
        if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get('host') or parsed.path or parsed.query or parsed.fragment:
            raise HTTPException(403, '같은 조사 화면에서 내보내기를 요청해 주세요.')
    case, context = collection_scope(body.session_id, body.profile, body.case_id)
    result = collection_call(create_export, case, context=context,
        source_snapshot_id=body.source_snapshot_id, selection=body.selection,
        items=[item.model_dump() for item in body.items],
        include_sensitive_originals=body.include_sensitive_originals,
        local_admin=collection_local_admin(request, body.profile), idempotency_key=body.idempotency_key)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@router.get('/evidence-collection/exports/{export_id}')
def evidence_export_status(export_id: str, request: Request,
                           session_id: str = Query(..., min_length=1, max_length=256),
                           profile: str = Query(..., min_length=1, max_length=128),
                           case_id: str = Query(..., min_length=1, max_length=256)):
    from forsic_plugin.evidence_collection_export import export_status
    case, context = collection_scope(session_id, profile, case_id)
    result = collection_call(export_status, case, export_id, context=context,
                             local_admin=collection_local_admin(request, profile))
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@router.get('/evidence-collection/exports/{export_id}/download')
def evidence_export_download(export_id: str, request: Request,
                             session_id: str = Query(..., min_length=1, max_length=256),
                             profile: str = Query(..., min_length=1, max_length=128),
                             case_id: str = Query(..., min_length=1, max_length=256)):
    from urllib.parse import quote
    from forsic_plugin.evidence_collection_export import export_download
    case, context = collection_scope(session_id, profile, case_id)
    content, metadata = collection_call(export_download, case, export_id, context=context,
                                        local_admin=collection_local_admin(request, profile))
    # The helper returns already verified bounded bytes, not a path that could
    # be replaced between verification and FileResponse opening the file.
    safe = 'forsic-evidence-' + metadata['export_id'] + '.zip'
    filename = quote('증거-모음-' + metadata['export_id'][:12] + '.zip', safe='')
    return Response(content.getvalue(), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="{safe}"; filename*=UTF-8\'\'{filename}',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

# Management remains behind the native plugin router's authentication dependency.
from forsic_plugin.dashboard.management_api import router as management_router
router.include_router(management_router)
from forsic_plugin.dashboard.tenant_api import router as tenant_router
router.include_router(tenant_router)
