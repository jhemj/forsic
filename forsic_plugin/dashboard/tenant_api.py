"""Tenant selection and native operational-memory management under one scope."""
from typing import Literal

from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field


def api_error(exc):
    if isinstance(exc,HTTPException):raise exc
    if isinstance(exc,PermissionError):raise HTTPException(403,str(exc)) from None
    if isinstance(exc,LookupError):raise HTTPException(404,'테넌트 또는 기억 항목을 찾을 수 없습니다.') from None
    if isinstance(exc,FileNotFoundError):raise HTTPException(404,'보존된 항목을 찾을 수 없습니다.') from None
    if isinstance(exc,(ValueError,TypeError,OSError)):raise HTTPException(409,str(exc) if isinstance(exc,ValueError) else '저장 상태를 확인해주세요.') from None
    raise exc


async def request_scope(request:Request):
    """Bind in the async request task; sync endpoints inherit its contextvars."""
    from forsic_plugin.tenants import resolve_request,bind_scope
    unscoped={'tenants_list','tenant_create','setup_get','setup_save','setup_check_model','skills_ui_copy'}
    body={}
    if request.method in ('POST','PUT','PATCH') and 'application/json' in request.headers.get('content-type',''):
        try:body=await request.json()
        except ValueError:body={}
        if not isinstance(body,dict):body={}
    selected={}
    for field,aliases in (('tenant_id',('tenant_id',)),('profile_name',('profile','profile_name')),
                          ('session_id',('session_id',)),('case_id',('case_id',))):
        values=[]
        for key in aliases:
            values.extend(v for v in request.query_params.getlist(key) if v)
            values.extend(str(source[key]) for source in (body,request.path_params) if source.get(key))
        if len(set(values))>1:raise HTTPException(403,'요청의 테넌트·프로필·사건 범위가 일치하지 않습니다.')
        selected[field]=values[0] if values else ''
    remote_profile=getattr(request.state,'forsic_remote_profile',None)
    remote_policy=getattr(request.state,'forsic_remote_policy',None)
    if (getattr(request.scope.get('route'),'name','') in unscoped
            and selected['profile_name']=='default'
            and (getattr(request.app.state,'initial_profile','default') or 'default')=='default'
            and not any(selected[k] for k in ('tenant_id','session_id','case_id'))):
        # Old entry links may explicitly name the host profile. It is not a
        # tenant selector, and a clean install need not register a default tenant.
        selected['profile_name']=''
    try:
        if remote_policy is not None and any(selected.values()):
            allowed=remote_policy.select(profile=selected['profile_name'],tenant_id=selected['tenant_id'])
            selected['profile_name']=allowed.profile
        elif remote_profile is not None and any(selected.values()):
            if selected['profile_name'] not in ('',remote_profile):
                raise PermissionError('이 서버에서 제공하는 조직의 조사를 선택해주세요.')
            if selected['tenant_id']:
                from forsic_plugin.tenants import TenantRegistry
                # Check metadata ownership before opening any case/session data.
                TenantRegistry().resolve(tenant_id=selected['tenant_id'],profile_name=remote_profile)
            selected['profile_name']=remote_profile
        scope=resolve_request(**selected,request=request)
    except Exception as exc:api_error(exc)
    request.state.forsic_tenant_scope=scope
    if scope is None:
        from forsic_plugin.tenants import TenantRegistry
        # Only the tenant gate and host setup are meaningful without selection.
        # In particular, never follow the legacy global current.json implicitly.
        if TenantRegistry().path.exists() and getattr(request.scope.get('route'),'name','') not in unscoped:
            raise HTTPException(400,'테넌트를 선택해주세요.')
        yield
        return
    from hermes_constants import set_hermes_home_override,reset_hermes_home_override
    with bind_scope(scope):
        token=set_hermes_home_override(scope['profile_home'])
        try:yield
        finally:reset_hermes_home_override(token)


router=APIRouter(dependencies=[Depends(request_scope)])


def public_scope(scope):
    from forsic_plugin.tenants import TenantRegistry
    return {**TenantRegistry.public(scope),
            **{key:scope[key] for key in ('resume_url',) if key in scope},'profile':scope['profile_name']}


def required_scope():
    from forsic_plugin.tenants import current_scope
    scope=current_scope()
    if scope is None:raise HTTPException(400,'테넌트를 선택해주세요.')
    return scope


def admin(request):
    from forsic_plugin.setup import local_admin
    if not local_admin(request):raise HTTPException(403,'설치된 컴퓨터의 localhost 주소에서 관리해주세요.')


class CreateTenant(BaseModel):
    model_config=ConfigDict(extra='forbid')
    display_name:str=Field(min_length=1,max_length=120)
    description:str=Field(default='',max_length=1000)
    memory_mode:Literal['off','manual','confirmed_auto']='manual'
    operation_id:str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')


class MemoryPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:Literal['off','manual','confirmed_auto']
    expected_revision:int=Field(strict=True,ge=0)
    operation_id:str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')


class MemoryAction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['add','replace','exclude','remove','restore']
    operation_id:str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    memory_id:str|None=Field(default=None,max_length=128)
    expected_revision:int|None=Field(default=None,strict=True,ge=1)
    expected_content_sha256:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    content:str|None=Field(default=None,max_length=2200)
    applicability:str|None=Field(default=None,max_length=2000)
    valid_from:str|None=Field(default=None,max_length=80)
    valid_until:str|None=Field(default=None,max_length=80)
    reason:str=Field(default='',max_length=2000)
    from_revision:int|None=Field(default=None,strict=True,ge=1)


@router.get('/tenants')
def tenants_list(request:Request):
    from forsic_plugin.tenants import TenantRegistry
    from forsic_plugin.setup import local_admin
    try:
        tenants=TenantRegistry().list_public()
        remote_profile=getattr(request.state,'forsic_remote_profile',None)
        remote_policy=getattr(request.state,'forsic_remote_policy',None)
        if remote_policy is not None:
            tenants=remote_policy.tenants()
        elif remote_profile is not None:
            tenants=[item for item in tenants if item['profile_name']==remote_profile]
        can_create=remote_profile is None and local_admin(request)
        return {'tenants':tenants,'access':{'can_create':can_create,
                'mode':'local' if can_create else 'restricted'}}
    except Exception as exc:api_error(exc)


@router.post('/tenants')
def tenant_create(body:CreateTenant,request:Request):
    admin(request)
    from forsic_plugin.tenants import TenantRegistry
    try:
        record=TenantRegistry().create(**body.model_dump(),source_profile=getattr(request.app.state,'initial_profile','default') or 'default')
        return public_scope(record)
    except Exception as exc:api_error(exc)


@router.get('/tenants/resolve')
def tenant_resolve():return public_scope(required_scope())


@router.put('/tenants/{tenant_id}/memory-policy')
def memory_policy(tenant_id:str,body:MemoryPolicy,request:Request):
    admin(request);scope=required_scope()
    if scope['tenant_id']!=tenant_id:raise HTTPException(403,'선택한 테넌트와 정책 대상이 다릅니다.')
    from forsic_plugin.tenants import TenantRegistry
    try:return public_scope(TenantRegistry().update_policy(tenant_id,**body.model_dump()))
    except Exception as exc:api_error(exc)


def memory_service():
    from forsic_plugin.tenant_memory import MemoryService
    return MemoryService(required_scope())


@router.get('/tenant-memory')
def memory_list(query:str='',offset:int=0,limit:int=50):
    try:return memory_service().list(query=query,offset=offset,limit=limit)
    except Exception as exc:api_error(exc)


@router.get('/tenant-memory/snapshot')
def memory_snapshot():
    try:return memory_service().snapshot()
    except Exception as exc:api_error(exc)


@router.get('/tenant-memory/changes')
def memory_changes(since_revision:int=0):
    try:return memory_service().changes(since_revision)
    except Exception as exc:api_error(exc)


@router.get('/tenant-memory/{memory_id}')
@router.get('/tenant-memory/{memory_id}/history')
def memory_get(memory_id:str,revision:int|None=None):
    try:return memory_service().get(memory_id,revision=revision)
    except Exception as exc:api_error(exc)


@router.post('/tenant-memory/action')
def memory_mutate(body:MemoryAction,request:Request):
    admin(request)
    from forsic_plugin.tenant_memory import trusted_ui_source
    try:
        scope=required_scope()
        source=trusted_ui_source(scope,actor_id='authenticated-local-operator')
        return memory_service().mutate(body.model_dump(exclude_unset=True),source=source)
    except Exception as exc:api_error(exc)
