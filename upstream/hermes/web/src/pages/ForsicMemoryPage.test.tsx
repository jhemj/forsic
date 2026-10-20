// @vitest-environment jsdom
import {act} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router'
import {beforeEach,afterEach,it,expect,vi} from 'vitest'
const mocks=vi.hoisted(()=>({request:vi.fn(),action:vi.fn(),fetch:vi.fn()}))
vi.mock('@/contexts/usePageHeader',()=>({usePageHeader:()=>({setTitle:()=>{}})}))
vi.mock('@/lib/api',()=>({fetchJSON:mocks.fetch}))
vi.mock('@/lib/forsic-memory',async original=>({...await original<typeof import('@/lib/forsic-memory')>(),memoryRequest:mocks.request,memoryAction:mocks.action}))
import ForsicMemoryPage from './ForsicMemoryPage'
import {TenantContext} from '@/contexts/tenant-context'
const tenant={tenant_id:'tenant-a',display_name:'합성 A',profile_name:'profile-a',revision:3,memory_policy:{mode:'manual' as const}}
const item={memory_id:'M-1',revision:2,content:'개발 서버군의 자산 점검 에이전트',content_sha256:'a'.repeat(64),status:'active',applicability:'개발 서버',source:{source_type:'user_attested',message_id:'message-one'},asserted_at:'2026-10-20T10:00:00+09:00',valid_from:null,valid_until:null,history:[{memory_id:'M-1',revision:1,content:'개발 서버의 자산 점검 에이전트',content_sha256:'b'.repeat(64),status:'superseded'}]}
let host:HTMLDivElement,root:Root
function button(text:string){return [...host.querySelectorAll('button')].find(el=>el.textContent===text)!}
async function render(){await act(async()=>root.render(<MemoryRouter><TenantContext.Provider value={tenant}><ForsicMemoryPage/></TenantContext.Provider></MemoryRouter>))}
async function select(value:string){const el=host.querySelector('select')!;await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(el,value);el.dispatchEvent(new Event('change',{bubbles:true}))})}
beforeEach(()=>{Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});mocks.fetch.mockReset();mocks.action.mockReset();mocks.request.mockReset();mocks.request.mockImplementation((path:string)=>Promise.resolve(path.includes('/M-1')?item:{items:[item],sidecar_revision:2,capacity:{used:100,limit:2200},next_offset:null}));host=document.createElement('div');document.body.append(host);root=createRoot(host)})
afterEach(async()=>{await act(async()=>root.unmount());host.remove()})
it('shows scope/provenance/unknown validity/capacity and sends exact CAS for exclusion then restore',async()=>{mocks.action.mockResolvedValue({status:'pending'});await render();expect(host.textContent).toContain('100 / 2,200자');await act(async()=>host.querySelector<HTMLButtonElement>('.fm-item')!.click());expect(host.textContent).toContain('분석가가 알려준 내용');expect(host.textContent).toContain('개발 서버');expect(host.textContent).toContain('미확인 ~ 미확인');await act(async()=>button('현재 기억에서 제외').click());expect(mocks.action.mock.calls[0][1]).toMatchObject({action:'exclude',memory_id:'M-1',expected_revision:2,expected_content_sha256:'a'.repeat(64),operation_id:expect.any(String)});expect(host.textContent).toContain('승인 대기 중입니다. 저장 완료가 아닙니다.');expect(host.textContent).not.toContain('이 테넌트의 운영기억에 저장했어요.');await act(async()=>button('이전 내용으로 되돌리기 · r1').click());expect(mocks.action.mock.calls[1][1]).toMatchObject({action:'restore',from_revision:1,expected_revision:2,expected_content_sha256:'a'.repeat(64)})})
it('persists off/manual/confirmed_auto only on explicit CAS policy save',async()=>{mocks.fetch.mockResolvedValue({...tenant,revision:4,memory_policy:{mode:'off'}});await render();expect(mocks.fetch).not.toHaveBeenCalled();await select('off');expect(mocks.fetch).not.toHaveBeenCalled();await act(async()=>button('설정 저장').click());expect(mocks.fetch.mock.calls[0][0]).toContain('/tenants/tenant-a/memory-policy?tenant_id=tenant-a&profile=profile-a');expect(JSON.parse(mocks.fetch.mock.calls[0][1].body)).toMatchObject({mode:'off',expected_revision:3,operation_id:expect.any(String)});expect(button('내용 추가').disabled).toBe(true);expect(host.textContent).toContain('현재 설정: 사용 안 함')})
