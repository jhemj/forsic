// @vitest-environment jsdom
import {act} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router'
import {beforeEach,afterEach,it,expect,vi} from 'vitest'
const mocks=vi.hoisted(()=>({request:vi.fn()}))
vi.mock('@/lib/forsic-memory',async original=>({...await original<typeof import('@/lib/forsic-memory')>(),memoryRequest:mocks.request}))
import {ForsicMemoryReceipts} from './ForsicMemoryReceipts'
import {TenantContext} from '@/contexts/tenant-context'
const tenant={tenant_id:'tenant-a',display_name:'합성 A',profile_name:'profile-a',revision:1,memory_policy:{mode:'manual' as const}}
let host:HTMLDivElement,root:Root
async function render(scope=tenant){await act(async()=>root.render(<MemoryRouter><TenantContext.Provider value={scope}><ForsicMemoryReceipts/></TenantContext.Provider></MemoryRouter>))}
beforeEach(()=>{Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});vi.useFakeTimers();mocks.request.mockReset();host=document.createElement('div');document.body.append(host);root=createRoot(host)})
afterEach(async()=>{await act(async()=>root.unmount());host.remove();vi.useRealTimers()})
it('T14 only displays newly committed durable receipts, never pending/failed/model prose',async()=>{mocks.request.mockResolvedValueOnce({sidecar_revision:5}).mockResolvedValueOnce({sidecar_revision:8,changes:[{status:'committed',durable:false,item:{memory_id:'false-commit'}},{status:'pending',message:'기억했어요'},{status:'failed',message:'기억했어요'},{status:'recovery_required',item:{memory_id:'notcommitted'}},{status:'committed',durable:true,operation_id:'saved',item:{memory_id:'M-1',revision:1}}]});await render();expect(host.textContent).toBe('');await act(async()=>vi.advanceTimersByTimeAsync(4000));expect(host.textContent).toContain('이 테넌트의 운영기억에 저장했어요.');expect(host.querySelectorAll('a')).toHaveLength(1);expect(host.querySelector('a')?.getAttribute('href')).toContain('memory_id=M-1&tenant_id=tenant-a&profile=profile-a');expect(host.textContent).not.toContain('기억했어요')})
it('T07 drops a late A receipt when scope switches to B',async()=>{let resolve!:(value:unknown)=>void;mocks.request.mockResolvedValueOnce({sidecar_revision:0}).mockImplementationOnce(()=>new Promise(done=>{resolve=done})).mockResolvedValue({sidecar_revision:0});await render();await act(async()=>vi.advanceTimersByTimeAsync(4000));await render({...tenant,tenant_id:'tenant-b',profile_name:'profile-b'});await act(async()=>resolve({sidecar_revision:1,changes:[{status:'committed',durable:true,item:{memory_id:'A-only'}}]}));expect(host.textContent).toBe('')})
