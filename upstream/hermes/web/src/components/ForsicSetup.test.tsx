// @vitest-environment jsdom
import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
const mocks = vi.hoisted(() => ({fetchJSON:vi.fn()}))
vi.mock('@/lib/api', () => ({fetchJSON:mocks.fetchJSON}))
import ForsicSetup, {ForsicSetupGate, type SetupStatus} from './ForsicSetup'
let root: Root, host: HTMLDivElement
const initial: SetupStatus = {local_admin:true,required:true,completed:false,pending:false,restart_required:false,llm_key_configured:true,login_configured:true,values:{llm_base_url:'http://localhost:11434/v1',llm_model:'synthetic-model',llm_context_length:null,evidence_directory:'/synthetic/evidence',evidence_directory_label:'시험 자료',hosting_mode:'local',private_lan_host:'',public_url:'',login_username:'admin'}}
beforeEach(() => {
  Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true})
  mocks.fetchJSON.mockReset().mockImplementation(async (url:string) => {
    if(url.endsWith('/evidence-sources')) return {sources:[{path:'/synthetic/evidence',name:'시험 자료'}],restricted:true,available:true}
    if(url.includes('/evidence-browser')) return {path:'/synthetic/evidence/folder',display_path:'시험 자료/folder',parent:'/synthetic/evidence',entries:[],has_more:false,next_offset:null}
    return initial
  })
  HTMLDialogElement.prototype.showModal = function() {this.open=true}
  HTMLDialogElement.prototype.close = function() {this.open=false}
  host=document.createElement('div');document.body.append(host);root=createRoot(host)
})
afterEach(async () => {await act(async () => root.unmount());host.remove()})
async function render(status=initial,onSaved=vi.fn()) {await act(async () => root.render(<MemoryRouter><ForsicSetup initial={status} onSaved={onSaved} /></MemoryRouter>));return onSaved}
function button(text:string) {return [...host.querySelectorAll('button')].find(item => item.textContent?.startsWith(text))!}
async function click(text:string) {await act(async () => button(text).click())}
async function input(element:HTMLInputElement,value:string) {await act(async () => {Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(element,value);element.dispatchEvent(new Event('input',{bubbles:true}))})}

it('navigates four steps, uses the existing confined picker, and saves only after review',async () => {
  const onSaved=await render()
  expect(mocks.fetchJSON).not.toHaveBeenCalled()
  expect(document.activeElement?.id).toBe('fs-title')
  await click('다음')
  expect(host.textContent).toContain('시험 자료')
  await click('폴더 찾아보기')
  expect(host.querySelector('dialog')?.open).toBe(true)
  await click('이 폴더 선택')
  await click('다음')
  expect(host.querySelector('input[value="local"]')?.getAttribute('checked')).not.toBeNull()
  await click('다음')
  expect(host.textContent).toContain('/synthetic/evidence/folder')
  expect(mocks.fetchJSON.mock.calls.every(call => call[1]?.method !== 'PUT')).toBe(true)
  await click('설정 저장')
  const save=mocks.fetchJSON.mock.calls.find(call => call[1]?.method === 'PUT')!
  expect(save[0]).toBe('/api/plugins/forsic/setup')
  expect(JSON.parse(save[1].body)).toMatchObject({evidence_directory:'/synthetic/evidence/folder',llm_api_key:'',clear_llm_key:false,login_password:'',llm_context_length:null})
  expect(onSaved).toHaveBeenCalledOnce()
})

it('retains unsaved inputs across back navigation, masks keys, and only checks model metadata on click',async () => {
  await render()
  const secret=host.querySelector<HTMLInputElement>('input[type=password]')!
  expect(secret.value).toBe('')
  await input(secret,'synthetic-secret')
  await input(host.querySelectorAll<HTMLInputElement>('input')[1],'synthetic-new-model')
  expect(mocks.fetchJSON).not.toHaveBeenCalled()
  mocks.fetchJSON.mockResolvedValueOnce({reachable:true,model_found:true,detail:'ok'})
  await click('모델 목록 확인')
  expect(JSON.parse(mocks.fetchJSON.mock.calls[0][1].body)).toMatchObject({llm_model:'synthetic-new-model',llm_api_key:'synthetic-secret'})
  expect(host.querySelector('[role=status]')?.textContent).toContain('실제 응답은 조사 시작 후')
  await click('다음');await click('이전')
  expect(host.querySelectorAll<HTMLInputElement>('input')[1].value).toBe('synthetic-new-model')
  expect(host.querySelector<HTMLInputElement>('input[type=password]')?.value).toBe('synthetic-secret')
})

it('requires online address and login and keeps edits after failed save for an explicit retry',async () => {
  await render({...initial,login_configured:false})
  await click('다음');await click('다음')
  await act(async () => host.querySelector<HTMLInputElement>('input[value=online]')!.click())
  expect(button('다음').disabled).toBe(true)
  await input(host.querySelector<HTMLInputElement>('input[type=url]')!,'https://synthetic.example')
  await input(host.querySelector<HTMLInputElement>('input[type=password]')!,'synthetic-passphrase')
  expect(button('다음').disabled).toBe(false)
  await click('다음')
  mocks.fetchJSON.mockRejectedValueOnce(new Error('합성 저장 실패'))
  await click('설정 저장')
  expect(host.querySelector('[role=alert]')?.textContent).toContain('합성 저장 실패')
  expect(host.textContent).toContain('https://synthetic.example')
  expect(button('설정 저장').disabled).toBe(false)
})

it('shows remote status without editors, secrets, evidence browsing or writes',async () => {
  await render({local_admin:false,required:false,completed:true,pending:false,restart_required:false,hosting_current:{mode:'private_lan'},llm_configured:true,evidence_configured:true})
  expect(host.textContent).toContain('내부 네트워크')
  expect(host.textContent).toContain('설치된 컴퓨터에서 변경')
  expect(host.querySelectorAll('input,button')).toHaveLength(0)
  expect(mocks.fetchJSON).not.toHaveBeenCalled()
})

it.each([false,true])('only gates blank bootstrap required=%s without starting investigation',async required => {
  mocks.fetchJSON.mockResolvedValue({...initial,required})
  await act(async () => root.render(<MemoryRouter><ForsicSetupGate><p>기존 조사 화면</p></ForsicSetupGate></MemoryRouter>))
  expect(host.textContent?.includes('기존 조사 화면')).toBe(!required)
  expect(mocks.fetchJSON).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/setup')
})

it('offers retry without assuming first-run configuration when status is unavailable',async () => {
  mocks.fetchJSON.mockRejectedValue(new Error('offline'))
  await act(async () => root.render(<MemoryRouter><ForsicSetupGate><p>기존 조사 화면</p></ForsicSetupGate></MemoryRouter>))
  expect(host.textContent).not.toContain('기존 조사 화면')
  expect(host.querySelector('[role=alert]')?.textContent).toContain('설정을 불러오지 못했어요')
})

it('allows retry on explicit setup load error',async () => {
  mocks.fetchJSON.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(initial)
  await act(async () => root.render(<MemoryRouter initialEntries={['/forsic?view=setup']}><ForsicSetupGate><p>기존 조사 화면</p></ForsicSetupGate></MemoryRouter>))
  expect(host.querySelector('[role=alert]')?.textContent).toContain('불러오지 못했어요')
  await click('다시 확인')
  expect(host.querySelector('#fs-title')?.textContent).toBe('모델 연결')
})

it('allows private LAN without login while explaining shared network access',async () => {
  await render({...initial,login_configured:false})
  await click('다음');await click('다음')
  await act(async () => host.querySelector<HTMLInputElement>('input[value=private_lan]')!.click())
  expect(host.querySelector('input[type=password]')).toBeNull()
  expect(host.textContent).toContain('로그인 없이 사용')
  await input(host.querySelector<HTMLInputElement>('input:not([type=radio])')!,'192.168.1.10')
  expect(button('다음').disabled).toBe(false)
})

it('keeps first setup pending until restart and permits reviewing its settings',async () => {
  mocks.fetchJSON.mockResolvedValue({...initial,required:false,pending:true,restart_required:true})
  await act(async () => root.render(<MemoryRouter><ForsicSetupGate><p>기존 조사 화면</p></ForsicSetupGate></MemoryRouter>))
  expect(host.textContent).toContain('다시 시작한 뒤 완료')
  expect(host.textContent).not.toContain('기존 조사 화면')
  expect([...host.querySelectorAll('button')].some(item => item.textContent === '조사 화면으로')).toBe(false)
  await click('설정 다시 확인')
  expect(host.querySelector('#fs-title')?.textContent).toBe('모델 연결')
})

it('fetches a catalog on explicit request with URL only and selects an available model',async () => {
  await render({...initial,values:{...initial.values!,llm_model:''}})
  expect(button('다음').disabled).toBe(true)
  expect(button('모델 목록 확인').disabled).toBe(false)
  expect(mocks.fetchJSON).not.toHaveBeenCalled()
  mocks.fetchJSON.mockResolvedValueOnce({reachable:true,model_found:false,models:['synthetic-a','synthetic-b']})
  await click('모델 목록 확인')
  expect(JSON.parse(mocks.fetchJSON.mock.calls[0][1].body).llm_model).toBe('')
  const select=host.querySelector('select')!
  await act(async () => {select.value='synthetic-b';select.dispatchEvent(new Event('change',{bubbles:true}))})
  expect(host.querySelectorAll<HTMLInputElement>('input')[1].value).toBe('synthetic-b')
  expect(button('다음').disabled).toBe(false)
  expect(mocks.fetchJSON).toHaveBeenCalledTimes(1)
})
