// @vitest-environment jsdom
import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'

const mocks = vi.hoisted(() => ({fetchJSON: vi.fn()}))
vi.mock('@/contexts/tenant-context',()=>({useTenant:()=>({tenant_id:'synthetic-tenant',profile_name:new URLSearchParams(window.location.search).get('profile')||window.__HERMES_INITIAL_PROFILE__||'default'})}))
vi.mock('@/lib/api', () => ({fetchJSON: mocks.fetchJSON}))
vi.mock('./ForsicOnboarding', () => ({default: ({onConflict}: {onConflict: (message: string) => void}) => <div>자료 연결<button onClick={() => onConflict('진행 중인 조사가 생겼습니다.')}>등록 충돌</button></div>}))
import ForsicEntry from './ForsicEntry'

let root: Root, host: HTMLDivElement
const available = {new_investigation: {allowed: true, reason: null, active_case: null}}
const blocked = {new_investigation: {allowed: false, reason: '진행 중인 조사가 있어 새 조사를 시작할 수 없습니다.', active_case: {case_id: 'synthetic-case', label: '합성 조사', resume_session_id: 'synthetic-session', status: 'running'}}}
beforeEach(() => {
  mocks.fetchJSON.mockReset()
  window.__HERMES_INITIAL_PROFILE__ = 'default'
  window.history.replaceState(null, '', '/')
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true})
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => {await act(async () => root.unmount()); host.remove(); delete window.__HERMES_INITIAL_PROFILE__; window.history.replaceState(null, '', '/')})
async function render(mode: 'start' | 'new' = 'start') {await act(async () => root.render(<MemoryRouter><ForsicEntry mode={mode} /></MemoryRouter>))}
function newButton() {return [...host.querySelectorAll('button')].find(button => button.textContent === '새 조사')}
function link(label: string) {return [...host.querySelectorAll('a')].find(anchor => anchor.textContent?.startsWith(label))}

it('offers new and existing entry after a read-only availability check', async () => {
  mocks.fetchJSON.mockResolvedValue(available)
  await render()
  expect(link('새 조사')?.getAttribute('href')).toBe('/forsic?view=new&tenant_id=synthetic-tenant&profile=default')
  expect(link('기존 조사')?.getAttribute('href')).toBe('/forsic?view=history&tenant_id=synthetic-tenant&profile=default')
  expect(host.textContent).not.toContain('자료 연결')
  expect(mocks.fetchJSON).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/investigations')
})

it.each([1, 2])('shows capacity %i/2 and preserves explicit close while a slot remains', async count => {
  mocks.fetchJSON.mockResolvedValue({new_investigation: {...paused.new_investigation, allowed: count < 2,
    reason: count < 2 ? null : '동시 조사 한도에 도달했습니다.', active_count: count, limit: 2, available_slots: 2-count}})
  await render()
  expect(host.textContent).toContain(`동시 조사 ${count} / 2`)
  expect(host.textContent).toContain('일시정지·대기 중인 조사 포함')
  expect(button('종료')).toBeDefined()
  expect(!!link('새 조사')).toBe(count < 2)
  if (count === 2) expect(newButton()?.disabled).toBe(true)
})

it('does not display unknown capacity as zero', async () => {
  mocks.fetchJSON.mockResolvedValue({new_investigation: {...blocked.new_investigation, active_count: null, limit: 2, available_slots: null}})
  await render()
  expect(host.textContent).not.toContain('동시 조사')
  expect(newButton()?.disabled).toBe(true)
})

it('keeps new disabled while loading and when state fails, then supports retry', async () => {
  let reject!: (cause: Error) => void
  mocks.fetchJSON.mockReturnValue(new Promise((_, fail) => {reject = fail}))
  await render()
  expect(newButton()?.disabled).toBe(true)
  expect(link('새 조사')).toBeUndefined()
  expect(host.textContent).toContain('확인하고 있어요')
  await act(async () => reject(new Error('offline')))
  expect(newButton()?.disabled).toBe(true)
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('시작할 수 없습니다')
  expect(link('기존 조사')).toBeDefined()
  mocks.fetchJSON.mockResolvedValue(available)
  await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === '조사 상태 다시 확인')!.click())
  expect(link('새 조사')).toBeDefined()
})

it.each(['start', 'new'] as const)('blocks %s entry with an explicit reason and current conversation link', async mode => {
  mocks.fetchJSON.mockResolvedValue(blocked)
  await render(mode)
  expect(newButton()?.disabled).toBe(true)
  expect(host.textContent).toContain(blocked.new_investigation.reason)
  expect(link('진행 중인 조사 열기')?.getAttribute('href')).toBe('/chat?case=synthetic-case&resume=synthetic-session&tenant_id=synthetic-tenant&profile=default')
  expect(host.textContent).not.toContain('자료 연결')
})

it('fails closed for missing availability even if cases were returned', async () => {
  mocks.fetchJSON.mockResolvedValue({cases: []})
  await render('new')
  expect(newButton()?.disabled).toBe(true)
  expect(host.textContent).not.toContain('자료 연결')
})

it('allows direct new only after availability and refreshes after registration conflict', async () => {
  mocks.fetchJSON.mockResolvedValueOnce(available).mockResolvedValueOnce(blocked)
  await render('new')
  expect(host.textContent).toContain('자료 연결')
  await act(async () => host.querySelector<HTMLButtonElement>('button')!.click())
  expect(mocks.fetchJSON).toHaveBeenCalledTimes(2)
  expect(host.querySelector('[role="alert"]')?.textContent).toBe('진행 중인 조사가 생겼습니다.')
  expect(newButton()?.disabled).toBe(true)
  expect(link('진행 중인 조사 열기')).toBeDefined()
})

it.each(['start', 'new'] as const)('rechecks blocked %s entry explicitly after the current investigation finishes', async mode => {
  let resolve!: (value: typeof available) => void
  mocks.fetchJSON.mockResolvedValueOnce(blocked).mockReturnValueOnce(new Promise(done => {resolve = done}))
  await render(mode)
  expect(mocks.fetchJSON).toHaveBeenCalledTimes(1)
  await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === '상태 다시 확인')!.click())
  expect(newButton()?.disabled).toBe(true)
  expect(host.textContent).toContain('확인하고 있어요')
  await act(async () => resolve(available))
  expect(mocks.fetchJSON).toHaveBeenCalledTimes(2)
  if (mode === 'new') expect(host.textContent).toContain('자료 연결')
  else expect(link('새 조사')).toBeDefined()
})

const paused = {new_investigation: {...blocked.new_investigation, active_case: {...blocked.new_investigation.active_case, status: 'waiting', goal_id: 'synthetic-goal', closable: true}}}
function button(text: string) {return [...host.querySelectorAll('button')].find(item => item.textContent === text)!}
it('asks for confirmation and sends nothing when ending is cancelled', async () => {
  mocks.fetchJSON.mockResolvedValue(paused)
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  await render()
  await act(async () => button('종료').click())
  expect(confirm).toHaveBeenCalledOnce()
  expect(confirm.mock.calls[0][0]).toContain('사용자 종료')
  expect(confirm.mock.calls[0][0]).toContain('보존')
  expect(mocks.fetchJSON).toHaveBeenCalledTimes(1)
  expect(newButton()?.disabled).toBe(true)
  confirm.mockRestore()
})
it('ends only the confirmed goal, prevents repeat clicks, and unlocks after acknowledgement and refresh', async () => {
  let resolve!: (value: {status: string}) => void
  mocks.fetchJSON.mockResolvedValueOnce(paused).mockReturnValueOnce(new Promise(done => {resolve = done})).mockResolvedValueOnce(available)
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  await render('new')
  await act(async () => {button('종료').click(); button('종료').click()})
  expect(mocks.fetchJSON).toHaveBeenCalledTimes(2)
  expect(button('종료 중…').disabled).toBe(true)
  expect(newButton()?.disabled).toBe(true)
  const [url, options] = mocks.fetchJSON.mock.calls[1]
  expect(url).toBe('/api/plugins/forsic/investigations/synthetic-case/close')
  expect(options.method).toBe('POST')
  expect(JSON.parse(options.body)).toEqual({profile: 'default', request_id: expect.stringMatching(/^[0-9a-f-]{36}$/), session_id: 'synthetic-session', goal_id: 'synthetic-goal', confirmed: true})
  await act(async () => resolve({status: 'closed'}))
  expect(mocks.fetchJSON).toHaveBeenCalledTimes(3)
  expect(host.textContent).toContain('자료 연결')
  confirm.mockRestore()
})
it('keeps creation blocked after errors and retries the same request id', async () => {
  mocks.fetchJSON.mockResolvedValueOnce(paused).mockRejectedValueOnce(new Error('busy')).mockResolvedValueOnce({status: 'unknown'})
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  await render()
  await act(async () => button('종료').click())
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('다시 시도')
  expect(newButton()?.disabled).toBe(true)
  await act(async () => button('종료').click())
  expect(JSON.parse(mocks.fetchJSON.mock.calls[1][1].body).request_id).toBe(JSON.parse(mocks.fetchJSON.mock.calls[2][1].body).request_id)
  expect(mocks.fetchJSON).toHaveBeenCalledTimes(3)
  expect(newButton()?.disabled).toBe(true)
  confirm.mockRestore()
})
it('does not offer goal closure for a blocker without a closable native goal', async () => {
  mocks.fetchJSON.mockResolvedValue(blocked)
  await render()
  expect(button('종료')).toBeUndefined()
})

it.each(['?profile=selected-investigator', ''] )('closes using the explicit or server-provided profile for %s', async search => {
  window.__HERMES_INITIAL_PROFILE__ = 'served-investigator'
  window.history.replaceState(null, '', '/forsic'+search)
  mocks.fetchJSON.mockResolvedValueOnce(paused).mockResolvedValueOnce({status: 'unknown'})
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  await render()
  await act(async () => button('종료').click())
  expect(JSON.parse(mocks.fetchJSON.mock.calls[1][1].body).profile).toBe(search ? 'selected-investigator' : 'served-investigator')
  confirm.mockRestore()
})
