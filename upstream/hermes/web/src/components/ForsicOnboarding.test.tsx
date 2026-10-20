// @vitest-environment jsdom
import {act, StrictMode} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import ForsicOnboarding from './ForsicOnboarding'

const mocks = vi.hoisted(() => ({fetchJSON: vi.fn(), connection: vi.fn(), sources: vi.fn(), conversation: vi.fn(), navigate: vi.fn()}))
vi.mock('@/lib/api', () => ({fetchJSON: (url: string, ...args: unknown[]) => url.startsWith('/api/plugins/forsic/evidence-connection?') ? mocks.connection(url, ...args) : url === '/api/plugins/forsic/evidence-sources' ? mocks.sources(url, ...args) : mocks.fetchJSON(url, ...args)}))
vi.mock('@/hooks/useForsicConversation', () => ({useForsicConversation: mocks.conversation}))
vi.mock('react-router', async importOriginal => ({...await importOriginal<typeof import('react-router')>(), useNavigate: () => mocks.navigate}))
vi.mock('motion/react', async () => {
  const {createElement} = await import('react')
  return {
    AnimatePresence: ({children}: {children: React.ReactNode}) => children,
    useReducedMotion: () => true,
    motion: {div: ({children, ...props}: Record<string, unknown>) => createElement('div', Object.fromEntries(Object.entries(props).filter(([name]) => !['initial', 'animate', 'exit', 'transition'].includes(name))), children as React.ReactNode)},
  }
})

let host: HTMLDivElement, root: Root
let chat: {loading: boolean; storedId: string | null; inputDisabled: boolean; error: string | null; send: ReturnType<typeof vi.fn>}
const created = {tenant_id:'synthetic-tenant',profile_name:'default',resume_url:'/chat?profile=default&case=synthetic-case&resume=synthetic-session',case_id: 'synthetic-case', session_id: 'synthetic-session', onboarding: {purpose: 'focused', environment: 'linux', question: '계정 접근과 파일 변경의 관계', prompt: '서버가 검증한 합성 조사 요청'}}
beforeEach(() => {
  vi.clearAllMocks(); mocks.fetchJSON.mockReset(); mocks.connection.mockReset(); mocks.sources.mockReset()
  mocks.sources.mockResolvedValue({sources: []})
  mocks.connection.mockImplementation((url: string) => {
    const path = new URL(url, 'http://localhost').searchParams.get('path')!
    return Promise.resolve({path, name: path.split('/').filter(Boolean).at(-1), kind: 'directory', access: 'readable', scope: 'entry_only'})
  })
  chat = {loading: false, storedId: created.session_id, inputDisabled: false, error: null, send: vi.fn().mockResolvedValue(true)}
  mocks.conversation.mockImplementation(() => ({...chat}))
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true})
  if (!HTMLDialogElement.prototype.showModal) HTMLDialogElement.prototype.showModal = function () {this.open = true}
  if (!HTMLDialogElement.prototype.close) HTMLDialogElement.prototype.close = function () {this.open = false; this.dispatchEvent(new Event('close'))}
  host = document.createElement('div'); document.body.append(host); root = createRoot(host); localStorage.clear()
})
afterEach(async () => {await act(async () => root.unmount()); host.remove(); localStorage.clear()})
async function render(strict = false, entry = '/forsic') {
  const view = <MemoryRouter initialEntries={[entry]}><ForsicOnboarding /></MemoryRouter>
  await act(async () => root.render(strict ? <StrictMode>{view}</StrictMode> : view))
}
function button(label: string) {
  const found = [...host.querySelectorAll('button')].find(item => item.textContent === label || item.getAttribute('aria-label') === label)
  if (!found) throw new Error(`Missing button: ${label}`)
  return found
}
async function click(label: string) {await act(async () => button(label).click())}
async function choose(name: string, value: string) {await act(async () => host.querySelector<HTMLInputElement>(`input[name="${name}"][value="${value}"]`)!.click())}
async function type(selector: string, value: string) {
  const input = host.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector)!
  await act(async () => {
    const prototype = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', {bubbles: true}))
  })
}
async function toPath() {expect(host.querySelector('h1')!.textContent).toBe('자료 연결')}
async function connectPath(path = '  /synthetic/evidence  ') {await type('#fo-evidence-path', path); await click('연결 확인하고 다음')}
async function toReview() {await connectPath(); await choose('purpose', 'focused'); await type('textarea', `  ${created.onboarding.question}  `); await click('다음'); await choose('environment', 'linux'); await click('다음')}
function posted(index = 0) {return JSON.parse(mocks.fetchJSON.mock.calls[index][1].body as string)}
function deferred<T>() {let resolve!: (value: T) => void; const promise = new Promise<T>(done => {resolve = done}); return {promise, resolve}}

it('starts with data connection, requires custom question and environment, and preserves choices on back', async () => {
  await render(); expect(button('연결 확인하고 다음').disabled).toBe(true)
  expect(host.querySelector('input[name="purpose"]')).toBeNull()
  await connectPath('/synthetic/original')
  expect(button('다음').disabled).toBe(true)
  await choose('purpose', 'custom'); await type('textarea', '   ')
  expect(button('다음').disabled).toBe(true)
  await type('textarea', '원본 로그에서 무엇이 바뀌었나요?'); await click('다음')
  expect(button('다음').disabled).toBe(true)
  await choose('environment', 'unknown'); await click('이전')
  expect(host.querySelector<HTMLInputElement>('input[value="custom"]')!.checked).toBe(true)
  expect(host.querySelector('textarea')!.value).toBe('원본 로그에서 무엇이 바뀌었나요?')
  await click('다음'); expect(host.querySelector<HTMLInputElement>('input[value="unknown"]')!.checked).toBe(true)
  await click('다음')
  expect(host.querySelector('.fo-review')?.textContent).toContain('original')
  expect(host.querySelector('.fo-review')?.textContent).toContain('하위 자료는 조사 중 확인')
  await click('자료 위치 수정')
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe('/synthetic/original')
  expect(host.querySelector('[role="status"]')?.textContent).toContain('폴더 접근 확인됨')
  expect(mocks.connection).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/evidence-connection?path=%2Fsynthetic%2Foriginal')
  expect(mocks.fetchJSON).not.toHaveBeenCalled(); expect(mocks.conversation).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
})

it('only posts the reviewed trimmed payload after the explicit final click and suppresses double clicks', async () => {
  const registration = deferred<typeof created>(); mocks.fetchJSON.mockReturnValue(registration.promise)
  await render(); await toReview()
  expect(mocks.fetchJSON).not.toHaveBeenCalled(); expect(mocks.conversation).not.toHaveBeenCalled()
  await act(async () => {const start = button('조사 시작하기'); start.click(); start.click()})
  expect(mocks.fetchJSON).toHaveBeenCalledTimes(1)
  expect([...host.querySelectorAll<HTMLButtonElement>('.fo-review button')].every(control => control.disabled)).toBe(true)
  expect(mocks.fetchJSON.mock.calls[0][0]).toBe('/api/plugins/forsic/investigations')
  expect(mocks.fetchJSON.mock.calls[0][1]).toMatchObject({method: 'POST', headers: {'Content-Type': 'application/json'}})
  expect(posted()).toEqual({path: '/synthetic/evidence', onboarding: {purpose: 'focused', environment: 'linux', question: created.onboarding.question}, request_id: expect.any(String)})
  expect(posted().request_id).not.toBe(''); expect(mocks.conversation).not.toHaveBeenCalled()
  await act(async () => registration.resolve(created))
  expect(chat.send).toHaveBeenCalledExactlyOnceWith(created.onboarding.prompt)
})

it('retains request identity across failed registration retries and changes it for a changed payload', async () => {
  mocks.fetchJSON.mockRejectedValue(new Error('등록 실패'))
  await render(); await toReview(); await click('조사 시작하기')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('등록 실패')
  expect(mocks.conversation).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
  await click('조사 시작하기'); expect(posted(1)).toEqual(posted(0))
  await click('질문 수정'); await type('textarea', '달라진 조사 질문'); await click('다음'); await click('다음'); await click('조사 시작하기')
  expect(posted(2).onboarding.question).toBe('달라진 조사 질문'); expect(posted(2).request_id).not.toBe(posted(0).request_id)
  expect(chat.send).not.toHaveBeenCalled()
})

it.each([{...created, case_id: ''}, {...created, session_id: ''}, {...created, onboarding: {...created.onboarding, prompt: ''}}])('does not execute incomplete registration results: %j', async response => {
  mocks.fetchJSON.mockResolvedValue(response)
  await render(); await toReview(); await click('조사 시작하기')
  expect(host.querySelector('[role="alert"]')).not.toBeNull(); expect(mocks.conversation).not.toHaveBeenCalled()
  expect(chat.send).not.toHaveBeenCalled(); expect(mocks.navigate).not.toHaveBeenCalled()
})

it('waits for native readiness, sends once under StrictMode, and navigates only after acceptance', async () => {
  const acceptance = deferred<boolean>()
  chat.loading = true; chat.storedId = null; chat.inputDisabled = true; chat.send.mockReturnValue(acceptance.promise)
  mocks.fetchJSON.mockResolvedValue(created)
  await render(true); await toReview(); await click('조사 시작하기')
  expect(mocks.conversation).toHaveBeenCalledWith({sessionId: created.session_id, profile: 'default', active: true})
  expect(chat.send).not.toHaveBeenCalled()
  chat.loading = false; await render(true); expect(chat.send).not.toHaveBeenCalled()
  chat.storedId = created.session_id; await render(true); expect(chat.send).not.toHaveBeenCalled()
  chat.inputDisabled = false; chat.error = '연결 대기'; await render(true); expect(chat.send).not.toHaveBeenCalled()
  chat.error = null; await render(true); expect(chat.send).toHaveBeenCalledExactlyOnceWith(created.onboarding.prompt)
  await render(true); expect(chat.send).toHaveBeenCalledTimes(1); expect(mocks.navigate).not.toHaveBeenCalled()
  await act(async () => acceptance.resolve(true))
  expect(mocks.navigate).toHaveBeenCalledExactlyOnceWith('/chat?profile=default&case=synthetic-case&resume=synthetic-session', {replace: true})
})

it.each(['unacknowledged', 'rejected'])('leaves an explicit link and never automatically retries an %s send or restores intent on remount', async failure => {
  if (failure === 'unacknowledged') chat.send.mockResolvedValue(false)
  else chat.send.mockRejectedValue(new Error('acknowledgement lost'))
  mocks.fetchJSON.mockResolvedValue(created)
  await render(true); await toReview(); await click('조사 시작하기')
  expect(chat.send).toHaveBeenCalledTimes(1); expect(mocks.navigate).not.toHaveBeenCalled()
  const link = [...host.querySelectorAll('a')].find(item => item.textContent?.includes('준비한 대화 열기'))
  expect(link?.getAttribute('href')).toBe('/chat?profile=default&case=synthetic-case&resume=synthetic-session')
  await render(true); chat.loading = true; await render(true); chat.loading = false; await render(true)
  expect(chat.send).toHaveBeenCalledTimes(1)
  await act(async () => root.unmount()); root = createRoot(host)
  localStorage.setItem('forsic-onboarding', JSON.stringify(created))
  await render(true, '/forsic?case=synthetic-case&resume=synthetic-session&start=1')
  expect(chat.send).toHaveBeenCalledTimes(1); expect(mocks.fetchJSON).toHaveBeenCalledTimes(1)
  expect(host.querySelector('.fo-handoff')).toBeNull()
})

it('opens home from a blank path without selecting it or starting an investigation', async () => {
  mocks.fetchJSON.mockResolvedValue({path: '/synthetic/home', parent: '/synthetic', entries: [], has_more: false, next_offset: null})
  await render(); await toPath(); await type('#fo-evidence-path', '  ')
  expect(button('폴더 찾아보기').disabled).toBe(false); expect(button('연결 확인하고 다음').disabled).toBe(true)
  await act(async () => {host.querySelector('input')!.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}))})
  expect(mocks.fetchJSON).not.toHaveBeenCalled()
  await click('폴더 찾아보기')
  expect(mocks.fetchJSON).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/evidence-browser?path=&offset=0')
  expect(host.querySelector<HTMLDialogElement>('dialog[aria-label="자료 폴더 탐색"]')?.open).toBe(true)
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe('  ')
  expect(button('연결 확인하고 다음').disabled).toBe(true)
  expect(mocks.conversation).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
  await click('이 폴더 선택')
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe('/synthetic/home')
  expect(host.querySelector<HTMLDialogElement>('dialog')?.open ?? false).toBe(false)
  expect(button('연결 확인하고 다음').disabled).toBe(false)
  expect(mocks.connection).not.toHaveBeenCalled()
  await click('연결 확인하고 다음'); await click('이전')
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe('/synthetic/home')
  expect(mocks.fetchJSON).toHaveBeenCalledTimes(1); expect(mocks.conversation).not.toHaveBeenCalled()
})

it('navigates folders separately from selection and selects a file on explicit click', async () => {
  const listing = {path: '/synthetic', parent: null, entries: [{name: 'logs', path: '/synthetic/logs', kind: 'directory'}, {name: 'sample.log', path: '/synthetic/sample.log', kind: 'file'}], has_more: false, next_offset: null}
  mocks.fetchJSON.mockResolvedValueOnce(listing)
    .mockResolvedValueOnce({path: '/synthetic/logs', parent: '/synthetic', entries: [], has_more: false, next_offset: null})
    .mockResolvedValueOnce(listing)
    .mockResolvedValueOnce({path: '/synthetic/home', parent: '/synthetic', entries: [], has_more: false, next_offset: null})
    .mockResolvedValueOnce(listing)
  await render(); await toPath(); await type('#fo-evidence-path', ' /synthetic ')
  await act(async () => {host.querySelector('input')!.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}))})
  expect(mocks.fetchJSON).toHaveBeenNthCalledWith(1, '/api/plugins/forsic/evidence-browser?path=%2Fsynthetic&offset=0')
  expect(button('상위 폴더 열기').disabled).toBe(true)
  await click('logs')
  expect(mocks.fetchJSON).toHaveBeenNthCalledWith(2, '/api/plugins/forsic/evidence-browser?path=%2Fsynthetic%2Flogs&offset=0')
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe(' /synthetic ')
  await click('상위 폴더 열기')
  expect(mocks.fetchJSON).toHaveBeenNthCalledWith(3, '/api/plugins/forsic/evidence-browser?path=%2Fsynthetic&offset=0')
  await click('홈 폴더')
  expect(mocks.fetchJSON).toHaveBeenNthCalledWith(4, '/api/plugins/forsic/evidence-browser?path=&offset=0')
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe(' /synthetic ')
  await click('상위 폴더 열기'); await click('sample.log선택')
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe('/synthetic/sample.log')
  expect(host.querySelector<HTMLDialogElement>('dialog')?.open ?? false).toBe(false)
  expect(mocks.fetchJSON.mock.calls.every(call => call.length === 1)).toBe(true)
  expect(mocks.conversation).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
})

it('keeps the typed path and reports browse errors without registering or executing', async () => {
  mocks.fetchJSON.mockRejectedValue(new Error('허용된 자료 경로 밖입니다'))
  await render(); await toPath(); await type('#fo-evidence-path', '/synthetic/denied'); await click('폴더 찾아보기')
  expect(host.querySelector('[role="alert"]')?.textContent).toBe('허용된 자료 경로 밖입니다')
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe('/synthetic/denied')
  expect(mocks.fetchJSON).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/evidence-browser?path=%2Fsynthetic%2Fdenied&offset=0')
  expect(mocks.conversation).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
  await click('폴더 목록 닫기')
  expect(host.querySelector<HTMLDialogElement>('dialog')?.open ?? false).toBe(false)
  expect(button('폴더 찾아보기').disabled).toBe(false)
})

it.each(['close button', 'escape'])('ignores a late paginated response after %s and can reopen', async cancellation => {
  const late = deferred<{path: string; parent: null; entries: []; has_more: false; next_offset: null}>()
  mocks.fetchJSON.mockResolvedValueOnce({path: '/synthetic', parent: null, entries: [], has_more: true, next_offset: 50})
    .mockReturnValueOnce(late.promise)
    .mockResolvedValueOnce({path: '/synthetic/fresh', parent: null, entries: [], has_more: false, next_offset: null})
  await render(); await toPath(); await type('#fo-evidence-path', '/synthetic'); await click('폴더 찾아보기'); await click('다음 항목')
  expect(mocks.fetchJSON).toHaveBeenNthCalledWith(2, '/api/plugins/forsic/evidence-browser?path=%2Fsynthetic&offset=50')
  expect(button('폴더 찾아보기').disabled).toBe(true)
  if (cancellation === 'escape') await act(async () => {host.querySelector('dialog')!.dispatchEvent(new Event('cancel', {cancelable: true}))})
  else await click('폴더 목록 닫기')
  await click('폴더 찾아보기')
  await act(async () => late.resolve({path: '/synthetic/stale', parent: null, entries: [], has_more: false, next_offset: null}))
  expect(host.querySelector('dialog')?.textContent).toContain('/synthetic/fresh')
  expect(host.querySelector('dialog')?.textContent).not.toContain('/synthetic/stale')
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe('/synthetic')
  await click('폴더 목록 닫기')
  expect(host.querySelector<HTMLDialogElement>('dialog')?.open ?? false).toBe(false)
  expect(button('폴더 찾아보기').disabled).toBe(false)
  expect(mocks.conversation).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
})

it('hands report purpose and requirements to native conversation once after explicit start', async () => {
  const report = {...created, onboarding: {purpose: 'report', environment: 'linux', question: '경영진에게 전달할 근거 중심 보고서', prompt: '서버에서 검증한 보고서 작성 요청'}}
  mocks.fetchJSON.mockResolvedValue(report)
  await render(true); await connectPath('/synthetic/report-evidence'); await choose('purpose', 'report')
  expect(host.querySelector('label[for="fo-question"]')?.textContent).toContain('보고서 요구사항')
  expect(button('다음').disabled).toBe(false)
  await type('textarea', `  ${report.onboarding.question}  `); await click('다음'); await choose('environment', 'linux'); await click('다음')
  expect(host.querySelector('.fo-review')?.textContent).toContain('보고서 작성하기')
  expect(button('보고서 작성하기').disabled).toBe(false)
  expect(mocks.fetchJSON).not.toHaveBeenCalled(); expect(mocks.conversation).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
  await click('보고서 작성하기'); await render(true)
  expect(posted()).toEqual({path: '/synthetic/report-evidence', onboarding: {purpose: 'report', environment: 'linux', question: report.onboarding.question}, request_id: expect.any(String)})
  expect(mocks.fetchJSON).toHaveBeenCalledTimes(1)
  expect(chat.send).toHaveBeenCalledExactlyOnceWith(report.onboarding.prompt)
  expect(mocks.navigate).toHaveBeenCalledExactlyOnceWith('/chat?profile=default&case=synthetic-case&resume=synthetic-session', {replace: true})
})


it('does not navigate on a late native acknowledgement after the user leaves onboarding', async () => {
  const acceptance = deferred<boolean>()
  chat.send.mockReturnValue(acceptance.promise); mocks.fetchJSON.mockResolvedValue(created)
  await render(true); await toReview(); await click('조사 시작하기')
  expect(chat.send).toHaveBeenCalledExactlyOnceWith(created.onboarding.prompt)
  await act(async () => root.render(<div>다른 화면</div>))
  await act(async () => acceptance.resolve(true))
  expect(mocks.navigate).not.toHaveBeenCalled()
  expect(chat.send).toHaveBeenCalledTimes(1)
  expect(host.textContent).toBe('다른 화면')
})

it('checks only the selected entry on explicit continue and lets a failed check retry', async () => {
  mocks.connection.mockRejectedValueOnce(new Error('자료 읽기 권한이 없습니다'))
  await render(); await type('#fo-evidence-path', ' /synthetic/denied ')
  expect(mocks.connection).not.toHaveBeenCalled()
  await click('연결 확인하고 다음')
  expect(host.querySelector('h1')!.textContent).toBe('자료 연결')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('자료 읽기 권한이 없습니다')
  expect(mocks.fetchJSON).not.toHaveBeenCalled(); expect(mocks.conversation).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
  await click('연결 확인하고 다음')
  expect(mocks.connection).toHaveBeenNthCalledWith(1, '/api/plugins/forsic/evidence-connection?path=%2Fsynthetic%2Fdenied')
  expect(mocks.connection).toHaveBeenNthCalledWith(2, '/api/plugins/forsic/evidence-connection?path=%2Fsynthetic%2Fdenied')
  expect(host.querySelector('h1')!.textContent).toBe('조사 목적')
  expect(mocks.fetchJSON).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
})

it.each(['edit', 'cancel', 'browse'])('ignores a late connection check after %s and requires a fresh check', async action => {
  const late = deferred<{path: string; name: string; kind: string; access: string; scope: string}>()
  mocks.connection.mockReturnValueOnce(late.promise)
  mocks.fetchJSON.mockResolvedValue({path: '/synthetic/original', parent: null, entries: [], has_more: false, next_offset: null})
  await render(); await type('#fo-evidence-path', '/synthetic/original'); await click('연결 확인하고 다음')
  expect(button('접근 확인 중').disabled).toBe(true)
  if (action === 'edit') await type('#fo-evidence-path', '/synthetic/changed')
  else if (action === 'cancel') await click('확인 취소')
  else {await click('폴더 찾아보기'); await click('폴더 목록 닫기')}
  await act(async () => late.resolve({path: '/synthetic/original', name: 'original', kind: 'directory', access: 'readable', scope: 'entry_only'}))
  expect(host.querySelector('h1')!.textContent).toBe('자료 연결')
  expect(host.querySelector('.fo-connection-state')).toBeNull()
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe(action === 'edit' ? '/synthetic/changed' : '/synthetic/original')
  expect(button('연결 확인하고 다음').disabled).toBe(false)
  expect(mocks.fetchJSON.mock.calls.every(call => !call[1])).toBe(true)
  expect(mocks.conversation).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
  await click('연결 확인하고 다음')
  expect(host.querySelector('h1')!.textContent).toBe('조사 목적')
  expect(mocks.connection).toHaveBeenCalledTimes(2)
})

it('offers the installation folder without selecting or checking it until the user acts', async () => {
  mocks.sources.mockResolvedValue({sources: [{path: '/synthetic/configured', name: 'configured'}]})
  mocks.fetchJSON.mockResolvedValue({path: '/synthetic/configured', parent: '/synthetic', entries: [], has_more: false, next_offset: null})
  await render()
  expect(mocks.sources).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/evidence-sources')
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe('')
  expect(button('연결 확인하고 다음').disabled).toBe(true)
  expect(host.textContent).toContain('설치 때 지정한 폴더')
  expect(mocks.connection).not.toHaveBeenCalled(); expect(mocks.fetchJSON).not.toHaveBeenCalled()
  await click('폴더 찾아보기')
  expect(mocks.fetchJSON).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/evidence-browser?path=%2Fsynthetic%2Fconfigured&offset=0')
  await click('폴더 목록 닫기'); await click('연결된 자료에서 선택')
  expect(mocks.fetchJSON).toHaveBeenNthCalledWith(2, '/api/plugins/forsic/evidence-browser?path=%2Fsynthetic%2Fconfigured&offset=0')
  expect(mocks.connection).not.toHaveBeenCalled()
  await click('이 폴더 선택'); await click('연결 확인하고 다음')
  expect(mocks.connection).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/evidence-connection?path=%2Fsynthetic%2Fconfigured')
  expect(mocks.conversation).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
})

it('keeps a typed location when installation sources arrive late', async () => {
  const late = deferred<{sources: {path: string; name: string}[]}>()
  mocks.sources.mockReturnValue(late.promise)
  mocks.fetchJSON.mockResolvedValue({path: '/synthetic/manual', parent: null, entries: [], has_more: false, next_offset: null})
  await render(); await type('#fo-evidence-path', '/synthetic/manual')
  await act(async () => late.resolve({sources: [{path: '/synthetic/configured', name: 'configured'}]}))
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe('/synthetic/manual')
  await click('폴더 찾아보기')
  expect(mocks.fetchJSON).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/evidence-browser?path=%2Fsynthetic%2Fmanual&offset=0')
  expect(mocks.connection).not.toHaveBeenCalled()
})

it('recovers from a source-list error while keeping manual connection available', async () => {
  mocks.sources.mockRejectedValueOnce(new Error('temporary error'))
  await render()
  expect(host.querySelector('[role="status"]')?.textContent).toContain('자료 위치를 불러오지 못했어요')
  expect(button('폴더 찾아보기').disabled).toBe(false)
  await type('#fo-evidence-path', '/synthetic/manual'); await click('자료 위치 다시 불러오기')
  expect(mocks.sources).toHaveBeenCalledTimes(2)
  expect(host.querySelector('.fo-source-load-error')).toBeNull()
  expect(host.querySelector<HTMLInputElement>('#fo-evidence-path')!.value).toBe('/synthetic/manual')
  await click('연결 확인하고 다음')
  expect(host.querySelector('h1')!.textContent).toBe('조사 목적')
  expect(mocks.fetchJSON).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
})

it('surfaces a registration conflict and requests a fresh availability check without native execution', async () => {
  const onConflict = vi.fn()
  mocks.fetchJSON.mockRejectedValue(Object.assign(new Error('다른 조사가 진행 중입니다.'), {status: 409}))
  await act(async () => root.render(<MemoryRouter><ForsicOnboarding onConflict={onConflict} /></MemoryRouter>))
  await toReview(); await click('조사 시작하기')
  expect(onConflict).toHaveBeenCalledExactlyOnceWith('다른 조사가 진행 중입니다.')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('다른 조사가 진행 중입니다.')
  expect(mocks.conversation).not.toHaveBeenCalled(); expect(chat.send).not.toHaveBeenCalled()
})

it('limits a remote picker to the configured share and presents public paths through review', async () => {
  const path = '/synthetic/private-mount'
  const display = 'smb://files.example.invalid/archive'
  mocks.sources.mockResolvedValue({sources:[{path,name:'공유 자료',display_path:display}],restricted:true,default_path:path,display_path:display,available:true})
  mocks.fetchJSON.mockResolvedValue({path,display_path:display,parent:null,entries:[],has_more:false,next_offset:null})
  mocks.connection.mockResolvedValue({path,name:'공유 자료',display_path:display,kind:'directory',access:'readable',scope:'entry_only'})
  await render()
  expect(host.querySelector('#fo-evidence-path')).toBeNull()
  expect(host.textContent).toContain(display)
  expect(host.textContent).not.toContain(path)
  await click('폴더 찾아보기')
  expect(mocks.fetchJSON).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/evidence-browser?path=%2Fsynthetic%2Fprivate-mount&offset=0')
  expect(host.querySelector('[aria-label="현재 폴더 경로"]')?.textContent).toBe(display)
  expect(host.querySelector('[aria-label="현재 폴더 경로"] button')).toBeNull()
  expect(button('상위 폴더 열기').disabled).toBe(true)
  await click('이 폴더 선택');await click('연결 확인하고 다음');await choose('purpose','focused');await click('다음');await choose('environment','linux');await click('다음')
  expect(host.querySelector('.fo-review')?.textContent).toContain(display)
  expect(host.textContent).not.toContain(path)
  expect(mocks.conversation).not.toHaveBeenCalled()
})

it('registers the explicit wizard action over HTTP LAN without crypto.randomUUID', async () => {
  const getRandomValues = globalThis.crypto.getRandomValues.bind(globalThis.crypto)
  vi.stubGlobal('crypto', {getRandomValues})
  try {
    mocks.fetchJSON.mockResolvedValue(created)
    await render();await toReview();await click('조사 시작하기')
    expect(posted().request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(chat.send).toHaveBeenCalledExactlyOnceWith(created.onboarding.prompt)
  } finally {vi.unstubAllGlobals()}
})
