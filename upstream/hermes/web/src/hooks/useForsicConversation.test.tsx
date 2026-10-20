// @vitest-environment jsdom
import {act, useEffect} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {afterEach, beforeEach, expect, it, vi, type Mock} from 'vitest'
import type {ServerRequest} from '@hermes/shared'
import {useForsicConversation} from './useForsicConversation'
import {inspectorEventRuntime} from '@/lib/forsic-inspector'

const mocks = vi.hoisted(() => ({fetch: vi.fn(), instances: [] as unknown[]}))
vi.mock('@/lib/api', () => ({fetchJSON: mocks.fetch}))
vi.mock('@/lib/gatewayClient', () => ({GatewayClient: class {
  connectionState = 'idle'
  event?: (event: unknown) => void
  inbound?: (request: ServerRequest) => boolean | void
  state?: (state: string) => void
  constructor() {mocks.instances.push(this)}
  connect = vi.fn(async () => {this.connectionState = 'open'; this.state?.('open')})
  close = vi.fn(() => {this.connectionState = 'closed'})
  request = vi.fn(async (method: string, params: Record<string, unknown>) => {
    if (method === 'session.resume') return {session_id: 'runtime', session_key: 'tip', info: {profile_name: params.profile}, running: false}
    if (method === 'session.activate') return {session_id: 'runtime', info: {profile_name: params.profile, stored_session_id: 'tip'}, running: false}
    if (method === 'session.control.read') return {control: {goal: {status: 'paused'}}}
    if (method === 'session.control') return {control: {goal: {status: params.action === 'goal.pause' ? 'paused' : 'active'}}, dispatch: params.action === 'goal.resume' ? {type: 'send', message: 'NATIVE CONTINUATION', display: '/goal resume'} : {type: 'exec'}}
    return {status: 'streaming'}
  })
  onAny(handler: (event: unknown) => void) {this.event = handler; return () => {this.event = undefined}}
  onRequest(handler: (request: ServerRequest) => boolean) {this.inbound = handler; return () => {this.inbound = undefined}}
  onState(handler: (state: string) => void) {this.state = handler; handler('idle'); return () => {this.state = undefined}}
}}))
interface FakeGateway {
  connectionState: string; connect: ReturnType<typeof vi.fn>; close: ReturnType<typeof vi.fn>; request: Mock<(method: string, params: Record<string, unknown>) => Promise<unknown>>
  inbound: (request: ServerRequest) => boolean; event: (event: unknown) => void; state: (state: string) => void
}
const live = (available = false, status = 'paused', profile = 'case') => ({available, requested_session_id: 'root', stored_session_id: 'tip', runtime_session_id: available ? 'runtime' : null, profile_name: profile, running: false, control: {goal: {status}}})
let value: ReturnType<typeof useForsicConversation>, roots: Root[] = []
let available = false, goalStatus = 'paused'
const observer = vi.fn()
function Probe({profile = 'case', sessionId = 'root', active = true}: {profile?: string; sessionId?: string; active?: boolean}) {const snapshot = useForsicConversation({sessionId, profile, active, onEvent: observer}); useEffect(() => {value = snapshot}, [snapshot]); return null}
async function mount(profile = 'case') {const root = createRoot(document.createElement('div')); roots.push(root); await act(async () => {root.render(<Probe profile={profile} />)}); return root}
function gateway() {return mocks.instances.at(-1) as FakeGateway}
beforeEach(() => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true})
  available = false; goalStatus = 'paused'; mocks.instances.length = 0; observer.mockClear()
  mocks.fetch.mockImplementation(async (url: string) => url.includes('/review-chat') ? {status: 'ready', profile: new URLSearchParams(url.split('?')[1]).get('profile'), requested_session_id: 'root', case_id: 'synthetic-case', conversation_id: 'root', goal_id: 'goal', snapshot_id: 's', items: [], next_cursor: null, coverage: {scanned_receipts: 0, omitted_receipts: 0}} : url.includes('/messages') ? {session_id: 'tip', messages: [{row_id: 1, role: 'assistant', content: 'Saved'}]} : live(available, goalStatus, new URLSearchParams(url.split('?')[1]).get('profile') ?? 'case'))
})
afterEach(async () => {for (const root of roots) await act(async () => {root.unmount()}); roots = []; mocks.fetch.mockReset(); vi.useRealTimers()})

it('keeps persisted user pause across reload and resumes only through one explicit native dispatch', async () => {
  const root = await mount()
  expect(value.control.paused).toBe(true)
  expect(value.control.disabled).toBe(false)
  expect(gateway().connect).not.toHaveBeenCalled()
  expect(gateway().request).not.toHaveBeenCalled()
  await act(async () => {root.unmount()}); roots = []
  await mount()
  expect(gateway().request).not.toHaveBeenCalled()
  const gw = gateway()
  await act(async () => {value.control.onToggle(); value.control.onToggle()})
  expect(gw.request.mock.calls.filter(([method]) => method === 'session.resume')).toEqual([['session.resume', {session_id: 'tip', profile: 'case', omit_messages: true, inline_images: false, close_on_disconnect: false}]])
  expect(gw.request.mock.calls.filter(([method]) => method === 'session.control')).toEqual([['session.control', {session_id: 'runtime', profile: 'case', action: 'goal.resume'}]])
  expect(gw.request.mock.calls.filter(([method]) => method === 'prompt.submit')).toEqual([['prompt.submit', {session_id: 'runtime', profile: 'case', text: 'NATIVE CONTINUATION', display_kind: 'hidden'}]])
  expect(gw.request.mock.calls.some(([method]) => method === 'session.create' || method === 'session.close')).toBe(false)
})

it('attaches exact live runtime and persists goal pause before native interrupt', async () => {
  available = true; goalStatus = 'active'
  await mount()
  const gw = gateway()
  gw.request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
    if (method === 'session.control.read') return {control: {goal: {status: goalStatus}}}
    if (method === 'session.control') {goalStatus = 'paused'; return {control: {goal: {status: 'paused'}}}}
    return {session_id: 'runtime', running: false, info: {stored_session_id: 'tip', profile_name: params.profile}}
  })
  await act(async () => {gw.event({type: 'session.control.update', session_id: 'runtime', payload: {control: {goal: {status: 'active'}}}})})
  await act(async () => {value.control.onToggle()})
  const methods = gw.request.mock.calls.map(([method]) => method)
  expect(methods.indexOf('session.control')).toBeLessThan(methods.indexOf('session.interrupt'))
  expect(gw.request.mock.calls.find(([method]) => method === 'session.activate')?.[1]).toMatchObject({session_id: 'runtime', profile: 'case'})
  expect(methods).not.toContain('session.resume')
  expect(value.control.paused).toBe(true)
})

it('never implicitly answers inbound requests and removes canceled cards', async () => {
  available = true
  await mount()
  const gw = gateway(), respond = vi.fn(), fail = vi.fn(), decline = vi.fn()
  const request: ServerRequest = {id: 'q', method: 'approval', params: {session_id: 'runtime', choices: ['once', 'deny']}, respond, fail, decline}
  await act(async () => {expect(gw.inbound(request)).toBe(true)})
  expect(value.pendingRequests).toHaveLength(1)
  expect(respond).not.toHaveBeenCalled(); expect(fail).not.toHaveBeenCalled()
  await act(async () => {await value.onApproval('not-offered')})
  expect(respond).not.toHaveBeenCalled()
  await act(async () => {gw.event({type: 'request.cancel', session_id: 'runtime', payload: {id: 'q'}})})
  expect(value.pendingRequests).toHaveLength(0)
  await act(async () => {expect(gw.inbound({...request, id: 'other', method: 'unsupported'})).toBe(true)})
  expect(fail).not.toHaveBeenCalled(); expect(respond).not.toHaveBeenCalled(); expect(decline).toHaveBeenCalledOnce()
  expect(value.pendingRequests).toHaveLength(0)
  expect(value.error).toBe('이 요청은 대화 화면에서 지원하지 않습니다.')
})

it('blocks duplicate and uncertain submissions and reconnects same client without resuming', async () => {
  available = true; goalStatus = 'active'
  await mount()
  const gw = gateway()
  await act(async () => {gw.event({type: 'session.control.update', session_id: 'runtime', payload: {control: {goal: null}}})})
  gw.request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
    if (method === 'prompt.submit') throw new Error('socket lost after send')
    if (method === 'session.control.read') return {control: {goal: null}}
    return {session_id: 'runtime', info: {profile_name: params.profile, stored_session_id: 'tip'}, running: false}
  })
  let results!: boolean[]
  await act(async () => {results = await Promise.all([value.send('hello'), value.send('hello')])})
  expect(results).toEqual([false, false])
  expect(value.inputDisabled).toBe(true)
  await act(async () => {expect(await value.send('hello')).toBe(false); value.reconnect()})
  expect(mocks.instances).toHaveLength(1)
  expect(gw.request.mock.calls.filter(([method]) => method === 'prompt.submit')).toHaveLength(1)
  expect(gw.request.mock.calls.some(([method]) => method === 'session.resume')).toBe(false)
  expect(value.inputDisabled).toBe(true)
})

it('keeps a lost acknowledgement uncertain even when a new same-text user row appears', async () => {
  available = true; goalStatus = 'active'
  let messages = [{row_id: 1, role: 'user', content: 'hello'}]
  mocks.fetch.mockImplementation(async (url: string) => url.includes('/review-chat') ? {status: 'ready', profile: new URLSearchParams(url.split('?')[1]).get('profile'), requested_session_id: 'root', case_id: 'synthetic-case', conversation_id: 'root', goal_id: 'goal', snapshot_id: 's', items: [], next_cursor: null, coverage: {scanned_receipts: 0, omitted_receipts: 0}} : url.includes('/messages') ? {session_id: 'tip', messages} : live(true, 'active'))
  await mount()
  const gw = gateway()
  gw.request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
    if (method === 'prompt.submit') throw new Error('socket lost after send')
    if (method === 'session.control.read') return {control: {goal: {status: 'active'}}}
    return {session_id: 'runtime', info: {profile_name: params.profile, stored_session_id: 'tip'}, running: false}
  })
  await act(async () => {gw.event({type: 'session.control.update', session_id: 'runtime', payload: {control: {goal: {status: 'active'}}}})})
  await act(async () => {expect(await value.send('hello')).toBe(false)})
  expect(value.inputDisabled).toBe(true)
  const uncertain = value.error
  await act(async () => {value.reconnect()})
  expect(value.error).toBe(uncertain)
  expect(value.inputDisabled).toBe(true)
  messages = [...messages, {row_id: 2, role: 'user', content: 'hello'}]
  await act(async () => {value.reconnect()})
  expect(value.error).toBe(uncertain)
  expect(value.inputDisabled).toBe(true)
  expect(gw.request.mock.calls.filter(([method]) => method === 'prompt.submit')).toHaveLength(1)
})

it('rejects wrong-profile activation and hides previous-case rows before reads finish', async () => {
  available = true
  const root = await mount()
  expect(value.rows[0].text).toBe('Saved')
  let resolve!: (value: unknown) => void
  mocks.fetch.mockImplementation((url: string) => url.includes('/messages') ? new Promise(done => {resolve = done}) : Promise.resolve(live(true, 'paused', 'other')))
  await act(async () => {root.render(<Probe profile="other" />)})
  expect(value.rows).toEqual([])
  const gw = gateway()
  gw.request.mockResolvedValue({session_id: 'runtime', info: {profile_name: 'case', stored_session_id: 'tip'}})
  await act(async () => {resolve({session_id: 'tip', messages: []})})
  expect(value.error).toContain('일치하지')
  expect(value.runtimeId).toBeUndefined()
  expect(gw.request.mock.calls.some(([method]) => method === 'prompt.submit')).toBe(false)
})


it('revalidates a compacted continuation tip against the stored root and preserves the same runtime', async () => {
  available = true
  await mount()
  const gw = gateway()
  mocks.fetch.mockImplementation(async (url: string) => url.includes('/review-chat') ? {status: 'ready', profile: new URLSearchParams(url.split('?')[1]).get('profile'), requested_session_id: 'root', case_id: 'synthetic-case', conversation_id: 'root', goal_id: 'goal', snapshot_id: 's', items: [], next_cursor: null, coverage: {scanned_receipts: 0, omitted_receipts: 0}} : url.includes('/messages') ? {session_id: 'next-tip', messages: [{id: 8, role: 'assistant', content: 'After compaction'}]} : {...live(true), stored_session_id: 'next-tip'})
  gw.request.mockImplementation(async (method: string) => method === 'session.control.read' ? {control: {goal: {status: 'paused'}}} : {session_id: 'runtime', session_key: 'next-tip', info: {profile_name: 'case', stored_session_id: 'next-tip'}})
  await act(async () => {gw.event({type: 'session.info', session_id: 'runtime', payload: {profile_name: 'case', stored_session_id: 'next-tip'}})})
  expect(value.storedId).toBe('next-tip')
  expect(value.runtimeId).toBe('runtime')
  expect(value.error).toBe(null)
  expect(value.rows.some(row => row.id === 'saved:8')).toBe(true)
  expect(gw.request.mock.calls.some(([method]) => method === 'session.resume')).toBe(false)
})


it('binds inspector tools from native cold resume envelope and accepts partial info without a refresh loop', async () => {
  await mount()
  available = true
  await act(async () => {value.control.onToggle()})
  const gw = gateway()
  const initial = observer.mock.calls.find(([event]) => event.type === 'session.info')?.[0]
  expect(initial).toMatchObject({session_id: 'runtime', payload: {stored_session_id: 'tip', profile_name: 'case', running: false}})
  expect(inspectorEventRuntime(initial, ['root', 'tip'], 'case')).toBe('runtime')
  const reads = mocks.fetch.mock.calls.length
  // _cwd_info's lazy shape omits both stored identity and profile. It belongs to the already verified runtime.
  await act(async () => {gw.event({type: 'session.info', session_id: 'runtime', payload: {cwd: '/workspace', branch: null, project: null, lazy: true, desktop_contract: 1}})})
  expect(mocks.fetch.mock.calls).toHaveLength(reads)
  expect(observer.mock.calls.at(-1)?.[0]).toMatchObject({type: 'session.info', payload: {stored_session_id: 'tip', profile_name: 'case'}})
  await act(async () => {gw.event({type: 'tool.start', session_id: 'runtime', payload: {tool_id: 'read', name: 'read', args: {path: 'file'}}})})
  expect(observer.mock.calls.at(-1)?.[0].type).toBe('tool.start')
  expect(value.rows.some(row => row.calls?.some(call => call.id === 'read'))).toBe(true)
  expect(value.error).toBe(null)
})

it('ignores foreign global reclamation and refreshes an exact reclaimed owner before explicit cold resume', async () => {
  available = true
  await mount()
  const gw = gateway()
  const reads = mocks.fetch.mock.calls.length
  gw.request.mockClear()
  for (const payload of [
    {session_id: 'other-runtime', stored_session_id: 'tip', reason: 'idle_timeout'},
    {session_id: 'runtime', stored_session_id: 'another-tip', reason: 'lru_evict'},
  ]) await act(async () => {gw.event({type: 'session.reclaimed', payload})})
  expect(value.runtimeId).toBe('runtime')
  expect(mocks.fetch.mock.calls).toHaveLength(reads)
  available = false
  await act(async () => {gw.event({type: 'session.reclaimed', payload: {session_id: 'runtime', stored_session_id: 'tip', reason: 'idle_timeout'}})})
  expect(value.runtimeId).toBeUndefined()
  expect(value.control.paused).toBe(true)
  expect(value.control.disabled).toBe(false)
  expect(mocks.fetch.mock.calls).toHaveLength(reads + 3)
  expect(gw.request).not.toHaveBeenCalled()
  await act(async () => {value.control.onToggle()})
  expect(gw.request.mock.calls.filter(([method]) => method === 'session.resume')).toEqual([['session.resume', {session_id: 'tip', profile: 'case', omit_messages: true, inline_images: false, close_on_disconnect: false}]])
  expect(gw.request.mock.calls.filter(([method]) => method === 'prompt.submit')).toHaveLength(1)
})

it('updates current context from the exact live session without using lifetime totals or new requests', async () => {
  available = true
  const root = await mount(), gw = gateway()
  const requests = gw.request.mock.calls.length
  await act(async () => {gw.event({type: 'session.usage', session_id: 'runtime', payload: {usage: {input_tokens: 1600000, context_used: 58000, context_max: 131072, context_source: 'provider_usage', context_estimated: false}}})})
  expect(value.contextUsage).toEqual({used: 58000, maximum: 131072, estimated: false})
  await act(async () => {gw.event({type: 'session.usage', session_id: 'foreign', payload: {usage: {context_used: 1, context_max: 8}}})})
  expect(value.contextUsage?.used).toBe(58000)
  await act(async () => {gw.event({type: 'session.usage', session_id: 'runtime', payload: {usage: {context_used: 21000, context_max: 131072, context_source: 'local_estimate', context_estimated: true}}})})
  expect(value.contextUsage).toEqual({used: 21000, maximum: 131072, estimated: true})
  await act(async () => {gw.event({type: 'session.usage', session_id: 'runtime', payload: {usage: {input_tokens: 1700000, total_tokens: 1800000}}})})
  expect(value.contextUsage).toBeNull()
  expect(gw.request.mock.calls).toHaveLength(requests)
  await act(async () => {root.render(<Probe profile="other" />)})
  expect(value.contextUsage).toBeNull()
})

it('retries a temporarily unavailable mapping and attaches once without starting or submitting a turn', async () => {
  vi.useFakeTimers(); goalStatus = 'active'
  await mount()
  const gw = gateway()
  expect(value.connected).toBe(false)
  expect(value.reconnecting).toBe(true)
  expect(value.connectionText).toContain('다시 확인')
  expect(value.rows.filter(row => !row.speaker).map(row => row.text)).toEqual(['Saved'])
  expect(gw.request).not.toHaveBeenCalled()
  gw.request.mockImplementation(async (method: string) => method === 'session.control.read' ? {control: {goal: {status: 'active'}}}
    : {session_id: 'runtime', info: {profile_name: 'case', stored_session_id: 'tip'}, running: true})
  available = true
  await act(async () => {await vi.advanceTimersByTimeAsync(1000)})
  expect(value.connected).toBe(true)
  expect(value.running).toBe(true)
  expect(value.connectionText).toBe('연결됨 · 조사 중')
  expect(value.reconnecting).toBe(false)
  expect(value.rows.filter(row => !row.speaker).map(row => row.text)).toEqual(['Saved'])
  expect(gw.request.mock.calls.map(([method]) => method)).toEqual(['session.activate', 'session.control.read'])
  expect(vi.getTimerCount()).toBe(1)
})

it('bounds unavailable retries and never mistakes an open socket or stale running snapshot for a live connection', async () => {
  vi.useFakeTimers(); goalStatus = 'active'
  available = true
  await mount()
  const gw = gateway()
  mocks.fetch.mockImplementation(async (url: string) => url.includes('/review-chat') ? {status: 'ready', profile: new URLSearchParams(url.split('?')[1]).get('profile'), requested_session_id: 'root', case_id: 'synthetic-case', conversation_id: 'root', goal_id: 'goal', snapshot_id: 's', items: [], next_cursor: null, coverage: {scanned_receipts: 0, omitted_receipts: 0}} : url.includes('/messages') ? {session_id: 'tip', messages: []} : {...live(false, 'active'), running: true})
  gw.request.mockClear()
  await act(async () => {value.reconnect()})
  expect(gw.connectionState).toBe('open')
  expect(value.connected).toBe(false)
  expect(value.running).toBe(false)
  const reads = mocks.fetch.mock.calls.filter(([url]) => !url.includes('/review-chat')).length
  await act(async () => {await vi.advanceTimersByTimeAsync(65000)})
  expect(mocks.fetch.mock.calls.filter(([url]) => !url.includes('/review-chat'))).toHaveLength(reads + 10)
  expect(vi.getTimerCount()).toBe(1)
  expect(value.reconnecting).toBe(false)
  expect(value.connectionText).toContain('실행 상태는 확인되지')
  expect(gw.request).not.toHaveBeenCalled()
})

it.each(['auth', 'identity'])('stops automatic attach retries after %s rejection', async failure => {
  vi.useFakeTimers(); goalStatus = 'active'
  await mount()
  mocks.fetch.mockImplementation(async (url: string) => {
    if (failure === 'auth') throw new Error('401 unauthorized')
    return url.includes('/messages') ? {session_id: 'foreign-tip', messages: []} : live(true, 'active')
  })
  await act(async () => {await vi.advanceTimersByTimeAsync(1000)})
  const reads = mocks.fetch.mock.calls.length
  await act(async () => {await vi.advanceTimersByTimeAsync(65000)})
  expect(mocks.fetch.mock.calls).toHaveLength(reads)
  expect(value.reconnecting).toBe(false)
  expect(value.connected).toBe(false)
  expect(gateway().request).not.toHaveBeenCalled()
})

it('cancels scheduled retries when changing sessions, deactivating or unmounting', async () => {
  vi.useFakeTimers(); goalStatus = 'active'
  const root = await mount(), previous = gateway()
  mocks.fetch.mockImplementation(async (url: string) => url.includes('/review-chat') ? {status: 'ready', profile: new URLSearchParams(url.split('?')[1]).get('profile'), requested_session_id: 'root', case_id: 'synthetic-case', conversation_id: 'root', goal_id: 'goal', snapshot_id: 's', items: [], next_cursor: null, coverage: {scanned_receipts: 0, omitted_receipts: 0}} : url.includes('/messages') ? {session_id: 'other-tip', messages: []}
    : {...live(false, 'active'), requested_session_id: 'other', stored_session_id: 'other-tip'})
  await act(async () => {root.render(<Probe sessionId="other" />)})
  const reads = mocks.fetch.mock.calls.length
  expect(previous.close).toHaveBeenCalledOnce()
  await act(async () => {root.render(<Probe sessionId="other" active={false} />)})
  await act(async () => {await vi.advanceTimersByTimeAsync(65000)})
  expect(mocks.fetch.mock.calls).toHaveLength(reads)
  await act(async () => {root.render(<Probe sessionId="other" />)})
  const mountedReads = mocks.fetch.mock.calls.length
  await act(async () => {root.unmount()}); roots = []
  await act(async () => {await vi.advanceTimersByTimeAsync(65000)})
  expect(mocks.fetch.mock.calls).toHaveLength(mountedReads)
  expect(mocks.instances.every(instance => !(instance as FakeGateway).request.mock.calls.length)).toBe(true)
})

it('does not activate an old conversation when a pending socket connection finishes after unmount', async () => {
  vi.useFakeTimers(); goalStatus = 'active'
  const root = await mount(), gw = gateway()
  let connected!: () => void
  gw.connect.mockImplementation(() => new Promise<void>(resolve => {connected = resolve}))
  available = true
  await act(async () => {await vi.advanceTimersByTimeAsync(1000)})
  await act(async () => {root.unmount()}); roots = []
  await act(async () => {connected()})
  expect(gw.request).not.toHaveBeenCalled()
})

it('shows native continuation as running after a failed turn without resuming from the UI', async () => {
  available = true; goalStatus = 'active'
  await mount()
  const gw = gateway()
  await act(async () => {gw.event({type: 'session.control.update', session_id: 'runtime', payload: {control: {goal: {status: 'active'}}}})})
  await act(async () => {gw.event({type: 'message.complete', session_id: 'runtime', payload: {status: 'error', text: '이번 요청을 완료하지 못했습니다.'}})})
  const calls = gw.request.mock.calls.length
  await act(async () => {gw.event({type: 'message.start', session_id: 'runtime'}); gw.event({type: 'tool.start', session_id: 'runtime', payload: {tool_id: 'after-failure', name: 'read', args: {}}})})
  expect(value.running).toBe(true)
  expect(value.connectionText).toBe('연결됨 · 조사 중')
  expect(value.statusText).toBe('조사 작업을 진행하고 있어요')
  expect(value.rows.some(row => row.text === '이번 요청을 완료하지 못했습니다.')).toBe(true)
  expect(gw.request.mock.calls).toHaveLength(calls)
})

it('ticks native turn elapsed time through status changes and reconnect, then stops at completion', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-20T00:00:00Z'))
  available = true; goalStatus = 'active'
  await mount()
  const gw = gateway(), started = Date.now() / 1000 - 61
  gw.request.mockImplementation(async (method: string) => method === 'session.control.read' ? {control: {goal: {status: 'active'}}} : {session_id: 'runtime', running: true, turn_started_at: started, info: {profile_name: 'case', stored_session_id: 'tip'}})
  await act(async () => {gw.event({type: 'session.control.update', session_id: 'runtime', payload: {control: {goal: {status: 'active'}}}}); gw.event({type: 'session.info', session_id: 'runtime', payload: {running: true, turn_started_at: started}}); gw.event({type: 'reasoning.delta', session_id: 'runtime', payload: {text: 'private'}})})
  await act(async () => {vi.advanceTimersByTime(1000)})
  expect(value.statusText).toBe('💭 생각을 정리하고 있어요 · 이번 응답 경과 1m 2s')
  await act(async () => {gw.event({type: 'status.update', session_id: 'runtime', payload: {text: '🗜️ 대화 맥락 압축 중'}}); vi.advanceTimersByTime(1000)})
  expect(value.statusText).toBe('🗜️ 대화 맥락 압축 중 · 이번 응답 경과 1m 3s')
  await act(async () => {gw.state('closed'); vi.advanceTimersByTime(1000)})
  await act(async () => {gw.event({type: 'reasoning.delta', session_id: 'runtime', payload: {text: 'private'}})})
  await act(async () => {vi.advanceTimersByTime(1000)})
  expect(value.statusText).toContain('이번 응답 경과 1m 5s')
  await act(async () => {gw.event({type: 'message.complete', session_id: 'runtime', payload: {status: 'completed', text: 'done'}})})
  await act(async () => {vi.advanceTimersByTime(3000)})
  expect(value.statusText).toBe('')
})

const publicReview = (text: string) => ({id: 'review:r:f', case_id: 'synthetic-case', conversation_id: 'root', goal_id: 'goal', review_id: 'r', finding_id: 'f', speaker: 'ren', kind: 'review_question', stage: 'checkpoint', round: null, public_text: text, public_reason: null, next_check: null, observed_at: null, time_basis: 'unknown', receipt_state: 'received', freshness: 'unknown', historical: false, semantic_approval: false, response_ref: null, disposition: null})
const publicPage = (profile: string, text: string, sessionId = 'root') => ({status: 'ready', profile, requested_session_id: sessionId, case_id: 'synthetic-case', conversation_id: 'root', goal_id: 'goal', snapshot_id: 'snapshot', items: [publicReview(text)], next_cursor: null, coverage: {scanned_receipts: 1, omitted_receipts: 0}})

it('cancels a previous profile request and rejects its late receipt without touching native control', async () => {
  const original = mocks.fetch.getMockImplementation()!
  let resolve!: (value: unknown) => void, oldSignal!: AbortSignal
  mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
    if (!url.includes('/review-chat')) return original(url, init)
    if (url.includes('profile=case')) {oldSignal = init!.signal as AbortSignal; return new Promise(r => {resolve = r})}
    return Promise.resolve(publicPage('other', '다른 프로필의 검토'))
  })
  const root = await mount()
  expect(oldSignal.aborted).toBe(false)
  await act(async () => {root.render(<Probe profile="other" />)})
  expect(oldSignal.aborted).toBe(true)
  await act(async () => {resolve(publicPage('case', '늦게 도착한 검토'))})
  expect(value.rows.filter(row => row.review).map(row => row.text)).toEqual(['다른 프로필의 검토'])
  expect(gateway().request).not.toHaveBeenCalled()
})

it('cancels selected-session receipt requests when hidden or inactive and makes no second websocket', async () => {
  vi.useFakeTimers()
  const original = mocks.fetch.getMockImplementation()!
  const signals: AbortSignal[] = []
  mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
    if (!url.includes('/review-chat')) return original(url, init)
    signals.push(init!.signal as AbortSignal)
    return new Promise(() => {})
  })
  const root = await mount()
  const reads = mocks.fetch.mock.calls.filter(([url]) => url.includes('/review-chat')).length
  await act(async () => {Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange')); await vi.advanceTimersByTimeAsync(30000)})
  expect(signals[0].aborted).toBe(true)
  expect(mocks.fetch.mock.calls.filter(([url]) => url.includes('/review-chat'))).toHaveLength(reads)
  await act(async () => {Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange'))})
  expect(signals).toHaveLength(2)
  await act(async () => {root.render(<Probe active={false}/>)})
  expect(signals[1].aborted).toBe(true)
  expect(value.rows).toEqual([])
  expect(mocks.instances).toHaveLength(1)
  expect(gateway().request).not.toHaveBeenCalled()
})

it('stops read-only polling on authentication failure while preserving manual pause', async () => {
  vi.useFakeTimers()
  const original = mocks.fetch.getMockImplementation()!
  mocks.fetch.mockImplementation((url: string, init?: RequestInit) => url.includes('/review-chat') ? Promise.reject(new Error('401 auth expired')) : original(url, init))
  await mount()
  const reads = mocks.fetch.mock.calls.length
  await act(async () => {await vi.advanceTimersByTimeAsync(30000)})
  expect(mocks.fetch.mock.calls).toHaveLength(reads)
  expect(value.control.paused).toBe(true)
  expect(value.rows.some(row => row.speaker === 'system' && row.text?.includes('인증'))).toBe(true)
  expect(gateway().request).not.toHaveBeenCalled()
})

it.each([
 ['streaming', '입력 접수 · 새 턴을 시작했습니다.'],
 ['queued', '입력 접수 · 다음 턴 대기열에 등록했습니다.'],
 ['steered', '입력 접수 · 현재 작업 뒤 조사에 반영하도록 전달했습니다.'],
 ['redirected', '입력 접수 · 진행 중인 조사의 방향 전환을 요청했습니다.'],
])('shows the actual native %s acceptance without claiming the question was answered', async (status, notice) => {
 available = true; goalStatus = 'active'
 await mount()
 const gw = gateway()
 gw.request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
  if (method === 'prompt.submit') return {status, ...(status === 'streaming' ? {user_row_id: 10} : {})}
  if (method === 'session.control.read') return {control: {goal: {status: 'active'}}}
  return {session_id: 'runtime', running: true, info: {profile_name: params.profile, stored_session_id: 'tip'}, inflight: {user: 'question', user_row_id: 10, assistant: '', streaming: true}}
 })
 await act(async () => {gw.event({type: 'session.control.update', session_id: 'runtime', payload: {control: {goal: {status: 'active'}}}})})
 await act(async () => {expect(await value.send('question')).toBe(true)})
 expect(value.rows.find(row => row.id === 'native:submission-status')?.text).toBe(notice)
 expect(value.rows.filter(row => row.id === 'saved:10')).toHaveLength(1)
 expect(value.rows.some(row => row.text?.includes('답변 완료'))).toBe(false)
 expect(gw.request.mock.calls.filter(([method]) => method === 'prompt.submit')).toHaveLength(1)
})

it('does not clear a lost submission acknowledgement when an unrelated in-flight response completes', async () => {
 available = true; goalStatus = 'active'
 await mount()
 const gw = gateway()
 gw.request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
  if (method === 'prompt.submit') throw new Error('lost ack')
  if (method === 'session.control.read') return {control: {goal: {status: 'active'}}}
  return {session_id: 'runtime', running: false, info: {profile_name: params.profile, stored_session_id: 'tip'}}
 })
 await act(async () => {gw.event({type: 'session.control.update', session_id: 'runtime', payload: {control: {goal: {status: 'active'}}}})})
 await act(async () => {expect(await value.send('new question')).toBe(false)})
 await act(async () => {gw.event({type: 'message.complete', session_id: 'runtime', payload: {text: 'prior answer', status: 'complete', persisted_turn: {complete: true, user_row_id: 1, final_assistant_row_id: 2, row_ids: [1, 2]}}})})
 expect(value.inputDisabled).toBe(true)
 expect(value.rows.find(row => row.id === 'native:submission-status')).toBeUndefined()
 await act(async () => {expect(await value.send('new question')).toBe(false)})
 expect(gw.request.mock.calls.filter(([method]) => method === 'prompt.submit')).toHaveLength(1)
})

it.each([[0, 6], [null, null], undefined])('restores explicit public corrections on reattach with offsets %s without sending a prompt', async correction_offsets => {
 available = true; goalStatus = 'active'
 await mount()
 const gw = gateway()
 gw.request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
  if (method === 'session.control.read') return {control: {goal: {status: 'active'}}}
  return {session_id: 'runtime', running: true, info: {profile_name: params.profile, stored_session_id: 'tip'}, inflight: {user: 'HIDDEN_CONTINUATION', display_kind: 'hidden', user_row_id: 9, assistant: 'beforeafter', streaming: true, corrections: ['HIDDEN_CORRECTION', '내 질문'], correction_offsets, correction_display_kinds: ['hidden', 'visible']}}
 })
 await act(async () => {value.reconnect()})
 expect(value.rows.filter(row => row.role === 'user').map(row => row.text)).toEqual(['내 질문'])
 await act(async () => {value.reconnect()})
 expect(value.rows.filter(row => row.text === '내 질문')).toHaveLength(1)
 expect(JSON.stringify(value.rows)).not.toMatch(/HIDDEN_CONTINUATION|HIDDEN_CORRECTION/)
 expect(gw.request.mock.calls.filter(([method]) => method === 'prompt.submit')).toHaveLength(0)
})

it('keeps hidden command input and its acknowledgement out of the public transcript', async () => {
 available = true; goalStatus = 'active'
 await mount()
 const gw = gateway()
 gw.request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
  if (method === 'command.dispatch') return {type: 'send', message: 'PRIVATE_COMMAND', display: '/native'}
  if (method === 'prompt.submit') return {status: 'steered'}
  if (method === 'session.control.read') return {control: {goal: {status: 'active'}}}
  return {session_id: 'runtime', running: true, info: {profile_name: params.profile, stored_session_id: 'tip'}, inflight: {user: 'PRIVATE_COMMAND', display_kind: 'hidden', assistant: '공개 답변', corrections: ['PRIVATE_CORRECTION'], correction_offsets: [0], correction_display_kinds: ['hidden']}}
 })
 await act(async () => {gw.event({type: 'session.control.update', session_id: 'runtime', payload: {control: {goal: {status: 'active'}}}})})
 await act(async () => {expect(await value.send('/native')).toBe(true)})
 expect(value.rows.find(row => row.id === 'native:submission-status')).toBeUndefined()
 expect(JSON.stringify(value.rows)).not.toMatch(/PRIVATE_COMMAND|PRIVATE_CORRECTION/)
 expect(gw.request.mock.calls.filter(([method]) => method === 'prompt.submit')).toEqual([['prompt.submit', {session_id: 'runtime', profile: 'case', text: 'PRIVATE_COMMAND', display_kind: 'hidden'}]])
})

it('clears actual independent-review activity on completion, read failure, pause, hidden visibility and disconnect while allowing native in-progress stale materials', async () => {
 vi.useFakeTimers()
 available = true; goalStatus = 'active'
 const original = mocks.fetch.getMockImplementation()!
 let receiptState = 'in_progress', freshness = 'current', fail = false
 mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
  if (!url.includes('/review-chat')) return original(url, init)
  if (fail) return Promise.reject(new Error('read failed'))
  return Promise.resolve({...publicPage('case', '검토 중'), items: [{...publicReview('검토 중'), id: 'review:r:status', finding_id: null, kind: 'review_status', speaker: 'system', receipt_state: receiptState, freshness, time_basis: 'request_recorded', observed_at: '2026-10-20T12:00:00Z'}]})
 })
 const root = await mount(), gw = gateway()
 const control = async (status: string) => {await act(async () => {gw.event({type: 'session.control.update', session_id: 'runtime', payload: {control: {goal: {status}}}})})}
 const poll = async () => {await act(async () => {await vi.advanceTimersByTimeAsync(8000)})}
 await control('active')
 expect(value.reviewActivity).toBeNull()
 await poll(); expect(value.reviewActivity?.reviewId).toBe('r')
 receiptState = 'received'; await poll(); expect(value.reviewActivity).toBeNull()
 receiptState = 'in_progress'; freshness = 'stale'; await poll(); expect(value.reviewActivity?.reviewId).toBe('r')
 freshness = 'current'; await poll(); expect(value.reviewActivity?.reviewId).toBe('r')
 await control('paused'); expect(value.reviewActivity).toBeNull()
 await control('active'); expect(value.reviewActivity).toBeNull()
 await poll(); expect(value.reviewActivity?.reviewId).toBe('r')
 fail = true; await poll(); expect(value.reviewActivity).toBeNull()
 fail = false; await poll(); expect(value.reviewActivity?.reviewId).toBe('r')
 await act(async () => {Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange'))})
 expect(value.reviewActivity).toBeNull()
 await act(async () => {Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange'))})
 expect(value.reviewActivity?.reviewId).toBe('r')
 await act(async () => {gw.state('closed')}); expect(value.reviewActivity).toBeNull()
 await act(async () => {root.render(<Probe active={false}/>)}); expect(value.reviewActivity).toBeNull()
 expect(gw.request.mock.calls.some(([method]) => method === 'prompt.submit')).toBe(false)
})

it('requires a new successful review observation after pause/resume and disconnect/reconnect even if an old read settles late', async () => {
 vi.useFakeTimers()
 available = true; goalStatus = 'active'
 const original = mocks.fetch.getMockImplementation()!
 const page = {...publicPage('case', '검토 중'), items: [{...publicReview('검토 중'), id: 'review:r:status', kind: 'review_status', speaker: 'system', finding_id: null, receipt_state: 'in_progress', freshness: 'current'}]}
 let hold = false, finish: ((page: unknown) => void) | undefined
 mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
  if (!url.includes('/review-chat')) return original(url, init)
  return hold ? new Promise(resolve => {finish = resolve}) : Promise.resolve(page)
 })
 await mount()
 const gw = gateway(), previousRequest = gw.request.getMockImplementation()!
 gw.request.mockImplementation(async (method: string, params: Record<string, unknown>) => method === 'session.control.read' ? {control: {goal: {status: 'active'}}} : previousRequest(method, params))
 const control = async (status: string) => {await act(async () => {gw.event({type: 'session.control.update', session_id: 'runtime', payload: {control: {goal: {status}}}})})}
 await control('active')
 await act(async () => {await vi.advanceTimersByTimeAsync(8000)})
 expect(value.reviewActivity?.reviewId).toBe('r')
 hold = true
 await act(async () => {await vi.advanceTimersByTimeAsync(8000)})
 await control('paused'); await control('active')
 expect(value.reviewActivity).toBeNull()
 await act(async () => {finish!(page)})
 expect(value.reviewActivity).toBeNull()
 hold = false
 await act(async () => {await vi.advanceTimersByTimeAsync(8000)})
 expect(value.reviewActivity?.reviewId).toBe('r')
 await control('paused'); hold = true
 await act(async () => {await vi.advanceTimersByTimeAsync(8000)})
 await control('active')
 await act(async () => {finish!(page)})
 expect(value.reviewActivity).toBeNull()
 hold = false
 await act(async () => {await vi.advanceTimersByTimeAsync(8000)})
 expect(value.reviewActivity?.reviewId).toBe('r')
 hold = true
 await act(async () => {gw.state('closed'); await vi.advanceTimersByTimeAsync(1000)})
 expect(value.connected).toBe(true)
 expect(value.reviewActivity).toBeNull()
 await act(async () => {finish!(page)})
 expect(value.reviewActivity?.reviewId).toBe('r')
 expect(gw.request.mock.calls.some(([method]) => method === 'prompt.submit')).toBe(false)
})
