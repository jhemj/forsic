// @vitest-environment jsdom
import {act, useEffect, useState, type ReactNode} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter, useNavigate, type NavigateFunction} from 'react-router'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import type {ForsicConversationProps} from '@/components/ForsicConversation'
import ChatPage, {type ForsicReviewActivityDetail} from './ChatPage'

const mocks = vi.hoisted(() => ({
  profile: 'default',
  conversation: vi.fn(), inspector: vi.fn(), view: vi.fn(), requests: vi.fn(),
  toggle: vi.fn(), send: vi.fn(), reconnect: vi.fn(), title: vi.fn(), onEvent: vi.fn(), onApproval: vi.fn(),
  state: {connected: false, paused: true, pending: false, disabled: false, reviewActivity: null as {reviewId: string; startedAt?: number} | null, reviewCaseId: null as string | null},
}))
vi.mock('@/hooks/useForsicConversation', () => ({useForsicConversation: (options: unknown) => {
  mocks.conversation(options)
  return {rows: [{id: 'answer', role: 'assistant', text: '저장된 답변'}], running: false, connected: mocks.state.connected, loading: false,
    reviewActivity: mocks.state.reviewActivity ? {...mocks.state.reviewActivity} : null, reviewCaseId: mocks.state.reviewCaseId, statusText: '일시정지됨', error: null, hasMore: false, onLoadEarlier: vi.fn(), send: mocks.send, inputDisabled: false,
    pendingRequests: [], pendingApproval: null, onApproval: mocks.onApproval, reconnect: mocks.reconnect,
    control: {paused: mocks.state.paused, pending: mocks.state.pending, disabled: mocks.state.disabled, onToggle: mocks.toggle}}
}}))
vi.mock('@/hooks/useForsicInspector', () => ({useForsicInspector: (options: unknown) => {mocks.inspector(options); return {onEvent: mocks.onEvent}}}))
vi.mock('@/contexts/useProfileScope', () => ({useProfileScope: () => ({profile: mocks.profile})}))
vi.mock('@/contexts/usePageHeader', () => ({usePageHeader: () => ({setTitle: mocks.title})}))
vi.mock('@/plugins', () => ({PluginSlot: () => null}))
vi.mock('@/components/ForsicConnectionCard', () => ({ForsicConnectionCard: () => <span>연결 설정</span>}))
vi.mock('@/components/ForsicWorkbench', () => ({ForsicWorkbench: ({children}: {children: ReactNode}) => <main>{children}</main>}))
vi.mock('@/components/ForsicRequests', () => ({ForsicRequests: (props: unknown) => {mocks.requests(props); return null}}))
vi.mock('@/components/ForsicConversation', () => ({ForsicConversation: function View(props: ForsicConversationProps) {
  mocks.view(props)
  // The real view owns its draft. Keep state here so tests exercise the page's
  // scope-key remount contract rather than coupling to composer internals.
  const [draft, setDraft] = useState('')
  return <section aria-label="React 대화"><p>{props.rows[0]?.text}</p><textarea aria-label="대화 초안" value={draft} onChange={event => setDraft(event.target.value)} />
    {props.control && <button onClick={props.control.onToggle} disabled={props.control.pending || props.control.disabled}>{props.control.paused ? '재개' : '일시정지'}</button>}
    {props.pendingContent}{props.children}</section>
}}))

let root: Root, container: HTMLDivElement, navigate: NavigateFunction
function NavigationProbe() {const currentNavigate = useNavigate(); useEffect(() => {navigate = currentNavigate}, [currentNavigate]); return null}
function view(active = true) {return <MemoryRouter initialEntries={['/chat?resume=session-a']}><NavigationProbe /><ChatPage isActive={active} /></MemoryRouter>}
async function render(active = true) {await act(async () => {root.render(view(active))})}
async function typeDraft(value: string) {const input = container.querySelector('textarea')!; await act(async () => {Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', {bubbles: true}))})}
beforeEach(() => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true})
  for (const mock of [mocks.conversation, mocks.inspector, mocks.view, mocks.requests, mocks.toggle, mocks.send, mocks.reconnect, mocks.title]) mock.mockClear()
  mocks.profile = 'default'; Object.assign(mocks.state, {connected: false, paused: true, pending: false, disabled: false, reviewActivity: null, reviewCaseId: null})
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
})
afterEach(async () => {await act(async () => {root.unmount()}); container.remove()})

it('opens the React conversation and shared native hook', async () => {
  await render()
  expect(container.querySelector('[aria-label="React 대화"]')).not.toBeNull()
  expect(container.textContent).toContain('저장된 답변')
  expect(mocks.conversation).toHaveBeenLastCalledWith({sessionId: 'session-a', profile: 'default', active: true, onEvent: mocks.onEvent})
  expect(mocks.inspector).toHaveBeenLastCalledWith({sessionId: 'session-a', profile: 'default', active: true})
  expect(mocks.requests).toHaveBeenLastCalledWith({requests: [], pendingApproval: undefined, onApproval: mocks.onApproval, connected: false})
})
it('preserves the cold paused control and delegates one dynamic pause/resume action', async () => {
  await render()
  let control = [...container.querySelectorAll('button')].filter(button => ['재개', '일시정지'].includes(button.textContent ?? ''))
  expect(control).toHaveLength(1); expect(control[0].textContent).toBe('재개')
  expect(mocks.view.mock.calls.at(-1)?.[0].control).toMatchObject({paused: true, disabled: false, onToggle: mocks.toggle})
  // The page must preserve the hook's cold-resume contract even before a
  // runtime connects; the actual view decides when the action is clickable.
  await act(async () => {control[0].click()})
  expect(mocks.toggle).toHaveBeenCalledTimes(1)
  Object.assign(mocks.state, {connected: true, paused: false})
  await render()
  control = [...container.querySelectorAll('button')].filter(button => ['재개', '일시정지'].includes(button.textContent ?? ''))
  expect(control).toHaveLength(1); expect(control[0].textContent).toBe('일시정지')
  await act(async () => {control[0].click()})
  expect(mocks.toggle).toHaveBeenCalledTimes(2)
  expect(container.textContent).not.toContain('다시 연결')
})
it('clears the view-owned draft when a valid session or profile scope changes', async () => {
  await render(); await typeDraft('이 세션의 초안')
  await act(async () => {navigate('/chat?resume=session-b')})
  expect(container.querySelector('textarea')?.value).toBe('')
  expect(mocks.conversation).toHaveBeenLastCalledWith(expect.objectContaining({sessionId: 'session-b', profile: 'default'}))
  await typeDraft('이 프로필의 초안'); mocks.profile = 'another-profile'; await render()
  expect(container.querySelector('textarea')?.value).toBe('')
  expect(mocks.conversation).toHaveBeenLastCalledWith(expect.objectContaining({sessionId: 'session-b', profile: 'another-profile'}))
})
it('preserves the last active scope and draft when another route changes query or profile', async () => {
  await render(); await typeDraft('돌아와서 이어 쓸 초안')
  await render(false)
  mocks.profile = 'settings-profile'
  await act(async () => {navigate('/settings?resume=unrelated')})
  await render(false)
  expect(mocks.conversation).toHaveBeenLastCalledWith(expect.objectContaining({sessionId: 'session-a', profile: 'default', active: false}))
  expect(mocks.inspector).toHaveBeenLastCalledWith({sessionId: 'session-a', profile: 'default', active: false})
  expect(container.querySelector('textarea')?.value).toBe('돌아와서 이어 쓸 초안')
  mocks.profile = 'default'
  await act(async () => {navigate('/chat?resume=session-a')})
  await render()
  expect(container.querySelector('textarea')?.value).toBe('돌아와서 이어 쓸 초안')
})

it('publishes scoped review activity in milliseconds, answers a header handshake and refreshes the observation each poll render', async () => {
 const events: ForsicReviewActivityDetail[] = []
 const record = (event: Event) => events.push((event as CustomEvent<ForsicReviewActivityDetail>).detail)
 window.addEventListener('forsic:review-activity', record)
 let now = 100_000
 const clock = vi.spyOn(Date, 'now').mockImplementation(() => now)
 try {
  Object.assign(mocks.state, {connected: true, paused: false, reviewCaseId: 'verified-case', reviewActivity: {reviewId: 'native-review', startedAt: 50}})
  await render()
  expect(events.at(-1)).toEqual({profile: 'default', sessionId: 'session-a', caseId: 'verified-case', active: true, reviewId: 'native-review', startedAt: 50_000, observedAt: 100_000})
  expect(mocks.view.mock.calls.at(-1)?.[0].reviewActivity).toEqual({reviewId: 'native-review', startedAt: 50})
  now += 8000
  window.dispatchEvent(new Event('forsic:review-activity-request'))
  expect(events.at(-1)?.observedAt).toBe(108_000)
  const count = events.length
  now += 8000; await render()
  expect(events.length).toBeGreaterThan(count)
  expect(events.at(-1)?.observedAt).toBe(116_000)
  await render(false)
  expect(events.at(-1)).toMatchObject({profile: 'default', sessionId: 'session-a', caseId: 'verified-case', active: false, reviewId: null, startedAt: null})
 } finally {clock.mockRestore(); window.removeEventListener('forsic:review-activity', record)}
})

it('requires a verified case for active header state and clears the old scope on navigation and unmount', async () => {
 const events: ForsicReviewActivityDetail[] = []
 const record = (event: Event) => events.push((event as CustomEvent<ForsicReviewActivityDetail>).detail)
 window.addEventListener('forsic:review-activity', record)
 try {
  Object.assign(mocks.state, {connected: true, paused: false, reviewActivity: {reviewId: 'review'}})
  await render()
  expect(events.at(-1)).toMatchObject({active: false, caseId: null, reviewId: null})
  mocks.state.reviewCaseId = 'case-a'; await render()
  expect(events.at(-1)).toMatchObject({active: true, caseId: 'case-a', sessionId: 'session-a'})
  mocks.state.reviewActivity = null; mocks.state.reviewCaseId = null
  await act(async () => {navigate('/chat?resume=session-b')})
  expect(events.some(event => event.sessionId === 'session-a' && event.caseId === 'case-a' && !event.active && event.reviewId === null)).toBe(true)
  expect(events.at(-1)).toMatchObject({sessionId: 'session-b', active: false})
  const count = events.length
  await act(async () => {root.unmount()})
  expect(events.at(-1)).toMatchObject({sessionId: 'session-b', active: false, reviewId: null, startedAt: null})
  expect(events.length).toBeGreaterThan(count)
  const afterUnmount = events.length
  window.dispatchEvent(new Event('forsic:review-activity-request'))
  expect(events).toHaveLength(afterUnmount)
 } finally {window.removeEventListener('forsic:review-activity', record)}
})
