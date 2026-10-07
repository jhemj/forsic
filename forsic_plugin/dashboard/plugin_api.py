from pathlib import Path
from uuid import UUID
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, FileResponse, Response
from pydantic import BaseModel, Field, SecretStr
from hermes_cli.config import load_config
from forsic_plugin.evidence import Case

router = APIRouter()


class ConnectionInput(BaseModel):
    llm_base_url: str = Field(max_length=2048)
    llm_model: str = Field(max_length=200)
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


@router.get('/connections')
def connections_get():
    from forsic_plugin.connections import public
    return public()


@router.put('/connections')
def connections_save(body: ConnectionInput):
    from forsic_plugin.connections import save
    values = body.model_dump()
    for field in ('gti_api_key','telegram_assistant_key','telegram_user_key'):
        values[field] = getattr(body,field).get_secret_value()
    try:
        return save(values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


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
        return CaseLibrary(selected_case(case_id)).invoke(args)
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
    settings = load_config()['plugins']['entries']['forsic']['settings']
    return Investigations(get_hermes_home(), Intake(settings.get('intake_root')).root)


@router.get('/investigations')
def investigations():
    return {'cases': directory().public()}


class ConversationInput(BaseModel):
    request_id: UUID


class InvestigationInput(ConversationInput):
    path: str = Field(min_length=1, max_length=4096)


def create_ui_conversation(body, *, case_id='', path=''):
    from forsic_plugin.investigations import RequestConflict
    try:
        return directory().create_conversation(body.request_id, case_id=case_id, path=path)
    except RequestConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except (ValueError, FileNotFoundError, NotADirectoryError):
        raise HTTPException(400, '접근할 수 있는 증거 파일이나 폴더의 전체 경로를 확인해주세요.') from None
    except PermissionError:
        raise HTTPException(403, '선택한 경로에 접근할 권한이 없습니다.') from None


@router.post('/investigations')
def investigation_create(body: InvestigationInput):
    return create_ui_conversation(body, path=body.path)


@router.get('/investigations/{case_id}/conversations')
def investigation_conversations(case_id: str):
    item = directory().get(case_id=case_id)
    if item is None:
        raise HTTPException(404, '사건을 찾을 수 없습니다.')
    return {'case_id': case_id, 'conversations': item['conversations']}


@router.post('/investigations/{case_id}/conversations')
def investigation_conversation_create(case_id: str, body: ConversationInput):
    return create_ui_conversation(body, case_id=case_id)


def selected_case(case_id='', session_id=''):
    if not case_id and not session_id:
        return current_case()
    item = directory().get(case_id=case_id, session_id=session_id)
    if item is None:
        raise HTTPException(404, '이 대화에 연결된 사건이 없습니다.')
    return Case(item['manifest'])


def current_case():
    import json
    settings = load_config()["plugins"]["entries"]["forsic"]["settings"]
    from forsic_plugin.intake import Intake
    pointer = Intake(settings.get('intake_root')).root / 'current.json'
    manifest = json.loads(pointer.read_text())['manifest'] if pointer.exists() else settings['case_file']
    return Case(manifest)


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
    case = selected_case(case_id)
    state = current(case)
    return {'state': state, 'audit': audit(state), 'bundles': bundles(case)}


@router.get('/report-bundles/{bundle_id}/{name}')
def report_artifact(bundle_id: str, name: str, case_id: str = ''):
    from forsic_plugin.report_driven.views import artifact
    try:
        path = artifact(selected_case(case_id), bundle_id, name)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(404, '보고서 묶음 또는 파일을 확인하지 못했습니다.') from exc
    return FileResponse(path, filename=path.name, headers={'X-Content-Type-Options':'nosniff', 'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; sandbox"})
