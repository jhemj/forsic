import {useEffect,useRef,useState,type ReactNode} from 'react'
import {useLocation,useNavigate} from 'react-router'
import {fetchJSON,setManagementProfile} from '@/lib/api'
import {requestUUID} from '@/lib/uuid'
import {PluginSlot} from '@/plugins/slots'
import {resolveTenant,setTenantRequestScope,tenantPath,verifiedTenant,type Tenant,type TenantDirectory,type TenantScope} from '@/lib/forsic-tenant'
import {TenantContext} from '@/contexts/tenant-context'
import {ProfileContext} from '@/contexts/profile-context'
import './forsic-tenant.css'

export function ForsicTenantSelector(){
 const navigate=useNavigate();const [tenants,setTenants]=useState<Tenant[]>([]);const [access,setAccess]=useState<TenantDirectory['access']|null>(null);const [chosen,setChosen]=useState('');const [name,setName]=useState('');const [description,setDescription]=useState('');const [auto,setAuto]=useState(false);const [loading,setLoading]=useState(true);const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [listError,setListError]=useState('');const [attempt,setAttempt]=useState(0);const operation=useRef<{key:string,id:string}|undefined>(undefined);const alive=useRef(true)
 useEffect(()=>{
  alive.current=true;const controller=new AbortController()
  void fetchJSON<TenantDirectory>('/api/plugins/forsic/tenants',{signal:controller.signal}).then(result=>{
   if(controller.signal.aborted)return
   if(!Array.isArray(result.tenants)||!result.tenants.every(verifiedTenant))throw new Error('조직 목록 형식을 확인하지 못했습니다.')
   if(typeof result.access?.can_create!=='boolean'||!['local','restricted'].includes(result.access.mode))throw new Error('조직 접속 권한을 확인하지 못했습니다.')
   setTenants(result.tenants);setAccess(result.access);setLoading(false)
  }).catch(cause=>{if(!controller.signal.aborted){setListError(cause instanceof Error?cause.message:'조직 목록을 불러오지 못했습니다.');setLoading(false)}})
  return()=>{alive.current=false;controller.abort()}
 },[attempt])
 function retry(){setLoading(true);setListError('');setAccess(null);setTenants([]);setChosen('');setAttempt(value=>value+1)}
 function enter(tenant:Tenant){if(!verifiedTenant(tenant))throw new Error('조직과 프로필 연결을 확인하지 못했습니다.');void navigate(tenant.start_url||tenantPath('/forsic?view=start',tenant))}
 const canCreate=access?.mode==='local'&&access.can_create===true
 async function create(){if(busy||!name.trim()||!canCreate)return;const payload={display_name:name.trim(),description:description.trim(),memory_mode:auto?'confirmed_auto':'manual'};const key=JSON.stringify(payload);if(operation.current?.key!==key)operation.current={key,id:requestUUID()};setBusy(true);setError('');try{const response=await fetchJSON<Tenant|{tenant:Tenant}>('/api/plugins/forsic/tenants',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,operation_id:operation.current.id})});if(alive.current)enter('tenant' in response?response.tenant:response)}catch(cause){if(alive.current)setError(cause instanceof Error?cause.message:'조직을 만들지 못했습니다. 같은 요청으로 다시 시도할 수 있습니다.')}finally{if(alive.current)setBusy(false)}}
 return <main className="forsic-tenant-screen"><section><header className="ft-brand"><div className="ft-brand-character"><PluginSlot name="tenant-entry" fallback={<img src="/assets/forsic-lens.svg" alt="포식이" width={104} height={104}/>}/></div><div><strong>포식이</strong><span>FORSIC</span></div></header><p>테넌트(조직)</p><h1>어느 조직의 조사인가요?</h1><p>같은 조직의 운영기억과 저장한 사례를 참고합니다. 증거와 판단은 사건별로 관리합니다.</p>
  {error&&<p role="alert">{error}</p>}
  {loading?<p role="status">조직 목록을 확인하고 있어요.</p>:listError?<section><p role="alert">{listError}</p><button onClick={retry}>조직 목록 다시 불러오기</button></section>:<>
   {access?.mode==='restricted'&&<p>이 접속에서 허용된 조직만 표시합니다.</p>}
   <div className="ft-columns" data-can-create={canCreate}><section><h2>기존 조직에서 시작</h2>{tenants.length?<><label>테넌트(조직)<select value={chosen} onChange={event=>setChosen(event.target.value)}><option value="">조직을 선택하세요</option>{tenants.map(tenant=><option key={tenant.tenant_id} value={tenant.tenant_id}>{tenant.display_name}</option>)}</select></label><button disabled={!chosen||busy} onClick={()=>{const tenant=tenants.find(item=>item.tenant_id===chosen);if(tenant)enter(tenant)}}>이 테넌트에서 시작</button></>:<p>{access?.mode==='restricted'?'현재 접속에서 사용할 수 있는 조직이 없습니다. 관리자에게 조직 접속 링크를 확인해주세요.':'등록된 조직이 없습니다.'}</p>}</section>
   <form aria-labelledby="ft-create-title" aria-describedby={!canCreate?'ft-create-restriction':undefined} onSubmit={event=>{event.preventDefault();void create()}}><h2 id="ft-create-title">새 조직 만들기</h2>{!canCreate&&<p id="ft-create-restriction" className="ft-create-restriction">새 조직은 호스트 PC에서만 만들 수 있습니다.</p>}<fieldset disabled={!canCreate||busy}><label>조직 이름<input required maxLength={120} value={name} onChange={event=>setName(event.target.value)}/></label><label>설명 (선택)<textarea maxLength={1000} value={description} onChange={event=>setDescription(event.target.value)}/></label><label className="ft-check"><input type="checkbox" checked={auto} onChange={event=>setAuto(event.target.checked)}/>확인된 운영 지식 자동 저장</label><p>기본은 수동 저장입니다. 새 조직은 빈 기억과 사건 목록으로 시작합니다.</p><button disabled={!canCreate||busy||!name.trim()}>{busy?'조직을 만들고 있어요…':'만들고 시작'}</button></fieldset></form></div>
  </>}
 </section></main>
}

/** Only a server-resolved ownership tuple may mount a workspace or native session. */
export function ForsicTenantGate({children}:{children:ReactNode}){
 const {pathname,search}=useLocation();const navigate=useNavigate();const params=new URLSearchParams(search);const bare=!params.has('tenant_id')&&!params.has('profile')&&!params.has('case')&&!params.has('resume')
 const identity=JSON.stringify([params.get('tenant_id'),params.get('profile'),params.get('case'),params.get('resume')]);const [state,setState]=useState<{identity:string;tenant:TenantScope|null;error:string}>({identity:'',tenant:null,error:''});const selector=pathname==='/tenants'||(bare&&!state.tenant)
 useEffect(()=>{if(selector)return;if(bare&&state.tenant){void navigate(tenantPath(pathname+search,state.tenant),{replace:true});return}if(state.identity===identity)return;const controller=new AbortController();void resolveTenant(new URLSearchParams(search),controller.signal).then(result=>{if(!verifiedTenant(result))throw new Error('조직과 프로필 연결을 확인하지 못했습니다.');if(!controller.signal.aborted)setState({identity,tenant:result,error:''})}).catch(cause=>{if(!controller.signal.aborted)setState({identity,tenant:null,error:cause instanceof Error?cause.message:'조직 소유관계를 확인하지 못했습니다.'})});return()=>controller.abort()},[identity,selector,search,bare,navigate,pathname,state.tenant,state.identity])
 const matches=state.tenant && (bare||params.has('tenant_id')||params.has('profile')) && (!params.has('tenant_id')||params.get('tenant_id')===state.tenant.tenant_id) && (!params.has('profile')||params.get('profile')===state.tenant.profile_name);const tenant=!selector&&(state.identity===identity||matches)?state.tenant:null
 setTenantRequestScope(tenant);if(tenant)setManagementProfile(tenant.profile_name)
 if(selector)return <ForsicTenantSelector/>
 if(!tenant)return <main className="forsic-tenant-screen">{state.identity===identity&&state.error?<><p role="alert">{state.error}</p><button onClick={()=>void navigate('/tenants')}>조직 다시 선택</button></>:<p role="status">조직과 조사 소유관계를 확인하고 있어요.</p>}</main>
 return <TenantContext.Provider value={tenant}><ProfileContext.Provider value={{profile:tenant.profile_name,currentProfile:tenant.profile_name,profiles:[tenant.profile_name],setProfile:()=>{void navigate('/tenants')}}}><div className="contents" key={`${tenant.tenant_id}:${tenant.profile_name}`}>{children}</div></ProfileContext.Provider></TenantContext.Provider>
}
