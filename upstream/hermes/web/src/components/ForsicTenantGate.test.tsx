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
function Location(){navigate=useNavigate();return <output>{useLocation().pathname+useLocation().search}</output>}
async function render(path='/'){await act(async()=>root.render(<MemoryRouter initialEntries={[path]}><Location/><ForsicTenantGate><Workspace/></ForsicTenantGate></MemoryRouter>))}
async function input(selector:string,value:string){const el=host.querySelector<HTMLInputElement>(selector)!;await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}))})}
function button(label:string){return [...host.querySelectorAll('button')].find(item=>item.textContent===label)!}
beforeEach(()=>{Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});mocks.fetch.mockReset();mocks.profile.mockReset();mounts=0;host=document.createElement('div');document.body.append(host);root=createRoot(host)})
afterEach(async()=>{await act(async()=>root.unmount());host.remove()})
it('T01 first entry only lists tenants without mounting a workspace or creating a session',async()=>{mocks.fetch.mockResolvedValue({tenants:[a]});await render();expect(host.textContent).toContain('어느 조직의 조사인가요?');expect(mocks.fetch).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/tenants');expect(mounts).toBe(0)})
it('T02 explicitly creates a fresh manual tenant and reuses operation id after a failed acknowledgement',async()=>{mocks.fetch.mockImplementation((url:string,init?:RequestInit)=>url.endsWith('/tenants')&&!init?Promise.resolve({tenants:[a]}):Promise.reject(new Error('연결 실패')));await render();await input('input:not([type=checkbox])','새 합성 조직');await act(async()=>button('만들고 시작').click());await act(async()=>button('만들고 시작').click());const writes=mocks.fetch.mock.calls.filter(call=>call[1]?.method==='POST');expect(writes).toHaveLength(2);const first=JSON.parse(writes[0][1].body);expect(first).toMatchObject({display_name:'새 합성 조직',memory_mode:'manual'});expect(JSON.parse(writes[1][1].body).operation_id).toBe(first.operation_id);expect(first).not.toHaveProperty('clone');expect(mounts).toBe(0)})
it('T08 resolves legacy case/resume links directly without forcing the selector',async()=>{mocks.fetch.mockResolvedValue({...a,case_id:'old-case',session_id:'old-session'});await render('/chat?case=old-case&resume=old-session');expect(host.textContent).toContain('작업공간 합성 A');expect(host.textContent).not.toContain('어느 조직');expect(mocks.fetch.mock.calls[0][0]).toContain('case_id=old-case&session_id=old-session');expect(mounts).toBe(1)})
it('T07 rejects delayed A resolution after switching to B and remounts tenant workspace',async()=>{let resolveA!:(value:typeof a)=>void;mocks.fetch.mockImplementation((url:string)=>url.includes('tenant_id=tenant-a')?new Promise(done=>{resolveA=done}):Promise.resolve(b));await render('/forsic?tenant_id=tenant-a&profile=profile-a');await act(async()=>navigate('/forsic?tenant_id=tenant-b&profile=profile-b'));expect(host.textContent).toContain('작업공간 합성 B');await act(async()=>resolveA(a));expect(host.textContent).not.toContain('합성 A');expect(host.textContent).toContain('작업공간 합성 B');expect(mocks.profile).toHaveBeenLastCalledWith('profile-b')})
