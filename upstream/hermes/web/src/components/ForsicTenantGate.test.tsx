// @vitest-environment jsdom
import {act,useEffect} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {MemoryRouter,useNavigate,useLocation} from 'react-router'
import {beforeEach,afterEach,it,expect,vi} from 'vitest'
const mocks=vi.hoisted(()=>({fetch:vi.fn(),profile:vi.fn()}))
vi.mock('@/lib/api',()=>({fetchJSON:mocks.fetch,setManagementProfile:mocks.profile}))
import {ForsicTenantGate} from './ForsicTenantGate'
import {useTenant} from '@/contexts/tenant-context'
const a={tenant_id:'tenant-a',display_name:'합성 A',profile_name:'profile-a',revision:1,memory_policy:{mode:'manual' as const},start_url:'/forsic?tenant_id=tenant-a&profile=profile-a'}
const b={...a,tenant_id:'tenant-b',display_name:'합성 B',profile_name:'profile-b'}
let host:HTMLDivElement,root:Root,navigate:ReturnType<typeof useNavigate>,mounts:number
function Workspace(){const tenant=useTenant();useEffect(()=>{mounts++},[]);return <div>작업공간 {tenant?.display_name}</div>}
function Location(){const next=useNavigate();const location=useLocation();useEffect(()=>{navigate=next},[next]);return <output>{location.pathname+location.search}</output>}
async function render(path='/'){await act(async()=>root.render(<MemoryRouter initialEntries={[path]}><Location/><ForsicTenantGate><Workspace/></ForsicTenantGate></MemoryRouter>))}
async function input(selector:string,value:string){const el=host.querySelector<HTMLInputElement>(selector)!;await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}))})}
function expectCreateDisabled(){const form=host.querySelector('form')!;expect(form).not.toBeNull();expect(form.getAttribute('aria-describedby')).toBe('ft-create-restriction');expect(host.querySelector('#ft-create-restriction')?.textContent).toBe('새 조직은 호스트 PC에서만 만들 수 있습니다.');for(const control of form.querySelectorAll('input,textarea,button'))expect(control.matches(':disabled')).toBe(true)}
function button(label:string){return [...host.querySelectorAll('button')].find(item=>item.textContent===label)!}
beforeEach(()=>{Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});mocks.fetch.mockReset();mocks.profile.mockReset();mounts=0;host=document.createElement('div');document.body.append(host);root=createRoot(host)})
afterEach(async()=>{await act(async()=>root.unmount());host.remove()})
it('T01 first entry only lists tenants without mounting a workspace or creating a session',async()=>{mocks.fetch.mockResolvedValue({tenants:[a],access:{can_create:true,mode:'local'}});await render();expect(host.textContent).toContain('어느 조직의 조사인가요?');expect(host.querySelector('.ft-brand strong')?.textContent).toBe('포식이');expect(host.querySelector('.ft-brand span')?.textContent).toBe('FORSIC');expect(host.querySelector('.ft-brand img')?.getAttribute('alt')).toBe('포식이');expect(host.querySelector('.ft-columns')?.children).toHaveLength(2);expect(mocks.fetch).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/tenants',{signal:expect.any(AbortSignal)});expect(mounts).toBe(0)})
it('T02 explicitly creates a fresh manual tenant and reuses operation id after a failed acknowledgement',async()=>{mocks.fetch.mockImplementation((url:string,init?:RequestInit)=>url.endsWith('/tenants')&&init?.method!=='POST'?Promise.resolve({tenants:[a],access:{can_create:true,mode:'local'}}):Promise.reject(new Error('연결 실패')));await render();expect(host.querySelector('fieldset')?.disabled).toBe(false);expect(host.querySelector('#ft-create-restriction')).toBeNull();await input('input:not([type=checkbox])','새 합성 조직');await act(async()=>button('만들고 시작').click());await act(async()=>button('만들고 시작').click());const writes=mocks.fetch.mock.calls.filter(call=>call[1]?.method==='POST');expect(writes).toHaveLength(2);const first=JSON.parse(writes[0][1].body);expect(first).toMatchObject({display_name:'새 합성 조직',memory_mode:'manual'});expect(JSON.parse(writes[1][1].body).operation_id).toBe(first.operation_id);expect(first).not.toHaveProperty('clone');expect(mounts).toBe(0)})
it('T08 resolves legacy case/resume links directly without forcing the selector',async()=>{mocks.fetch.mockResolvedValue({...a,case_id:'old-case',session_id:'old-session'});await render('/chat?case=old-case&resume=old-session');expect(host.textContent).toContain('작업공간 합성 A');expect(host.textContent).not.toContain('어느 조직');expect(mocks.fetch.mock.calls[0][0]).toContain('case_id=old-case&session_id=old-session');expect(mounts).toBe(1)})
it('T07 rejects delayed A resolution after switching to B and remounts tenant workspace',async()=>{let resolveA!:(value:typeof a)=>void;mocks.fetch.mockImplementation((url:string)=>url.includes('tenant_id=tenant-a')?new Promise(done=>{resolveA=done}):Promise.resolve(b));await render('/forsic?tenant_id=tenant-a&profile=profile-a');await act(async()=>navigate('/forsic?tenant_id=tenant-b&profile=profile-b'));expect(host.textContent).toContain('작업공간 합성 B');await act(async()=>resolveA(a));expect(host.textContent).not.toContain('합성 A');expect(host.textContent).toContain('작업공간 합성 B');expect(mocks.profile).toHaveBeenLastCalledWith('profile-b')})

it('remote first entry lists only server-authorized organizations and enters through normal ownership resolution',async()=>{
 mocks.fetch.mockImplementation((url:string)=>url==='/api/plugins/forsic/tenants'?Promise.resolve({tenants:[a],access:{can_create:false,mode:'restricted'}}):Promise.resolve(a))
 await render('/forsic')
 expectCreateDisabled();expect(host.querySelector('.ft-columns')?.children).toHaveLength(2);expect(host.querySelector('select')?.textContent).toContain('합성 A');expect(host.textContent).not.toContain('합성 B');expect(mounts).toBe(0)
 expect(mocks.fetch).toHaveBeenCalledTimes(1)
 await input('form input:not([type=checkbox])','원격 합성 조직')
 await act(async()=>{button('만들고 시작').click();host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))})
 expect(mocks.fetch).toHaveBeenCalledTimes(1)
 const select=host.querySelector('select')!
 await act(async()=>{select.value=a.tenant_id;select.dispatchEvent(new Event('change',{bubbles:true}))})
 await act(async()=>button('이 테넌트에서 시작').click())
 expect(mocks.fetch.mock.calls[1][0]).toBe('/api/plugins/forsic/tenants/resolve?tenant_id=tenant-a&profile=profile-a')
 expect(host.textContent).toContain('작업공간 합성 A');expect(mounts).toBe(1)
 expect(mocks.fetch.mock.calls.some(call=>call[1]?.method==='POST')).toBe(false)
})
it('a denied organization list is a retryable load failure, never an empty directory or create form',async()=>{
 mocks.fetch.mockRejectedValueOnce(new Error('허용된 조직 목록을 확인할 수 없습니다.')).mockResolvedValueOnce({tenants:[a],access:{can_create:false,mode:'restricted'}})
 await render()
 expect(host.querySelector('[role=alert]')?.textContent).toContain('확인할 수 없습니다');expect(host.textContent).not.toContain('등록된 조직이 없습니다');expect(host.querySelector('form')).toBeNull();expect(host.querySelector('select')).toBeNull();expect(mounts).toBe(0)
 await act(async()=>button('조직 목록 다시 불러오기').click())
 expect(host.querySelector('[role=alert]')).toBeNull();expect(host.querySelector('select')?.textContent).toContain('합성 A');expectCreateDisabled();expect(mounts).toBe(0)
})
it('an empty restricted directory explains access with organization creation disabled',async()=>{
 mocks.fetch.mockResolvedValue({tenants:[],access:{can_create:false,mode:'restricted'}})
 await render()
 expect(host.textContent).toContain('현재 접속에서 사용할 수 있는 조직이 없습니다');expectCreateDisabled();expect(host.textContent).not.toContain('등록된 조직이 없습니다');expect(mounts).toBe(0)
})
it('does not offer organization creation while access is unresolved or missing',async()=>{
 let done!:(value:unknown)=>void;mocks.fetch.mockImplementation(()=>new Promise(resolve=>{done=resolve}))
 await render();expect(host.querySelector('form')).toBeNull();expect(host.querySelector('[role=status]')).not.toBeNull()
 await act(async()=>done({tenants:[a]}))
 expect(host.querySelector('form')).toBeNull();expect(host.querySelector('[role=alert]')?.textContent).toContain('접속 권한');expect(mounts).toBe(0)
})

it('fails closed when a restricted connection reports creation permission',async()=>{
 mocks.fetch.mockResolvedValue({tenants:[a],access:{can_create:true,mode:'restricted'}})
 await render();expectCreateDisabled()
 await input('form input:not([type=checkbox])','합성 조직')
 await act(async()=>host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})))
 expect(mocks.fetch).toHaveBeenCalledTimes(1);expect(mounts).toBe(0)
})
