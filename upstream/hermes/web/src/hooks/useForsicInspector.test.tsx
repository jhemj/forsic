// @vitest-environment jsdom
import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {afterEach, expect, it, vi} from 'vitest'
import {useForsicInspector} from './useForsicInspector'

const fetchJSON = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api', () => ({fetchJSON}))
afterEach(() => {vi.useRealTimers(); fetchJSON.mockReset()})

it('rejects old profile reads, resets live state, and never polls transcript or catalogue history', async () => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true})
  vi.useFakeTimers()
  let resolveOld!: (value: unknown) => void
  fetchJSON.mockImplementation((url: string) => {
    if (url.includes('/live') && url.includes('profile=A')) return new Promise(resolve => {resolveOld = resolve})
    return Promise.resolve(url.includes('/live') ? {requested_session_id: 'conversation', stored_session_id: 'conversation', profile_name: new URL(url, 'http://localhost').searchParams.get('profile')} : [])
  })
  let value!: ReturnType<typeof useForsicInspector>
  function Probe({profile}: {profile: string}) {value = useForsicInspector({profile, sessionId: 'conversation', active: true}); return null}
  const container = document.createElement('div'), root = createRoot(container)
  try {
    await act(async () => {root.render(<Probe profile="A" />)})
    await act(async () => {value.onEvent({session_id: 'parent-runtime', type: 'session.info', payload: {stored_session_id: 'conversation', profile_name: 'A', running: false}})})
    await act(async () => {value.onEvent({session_id: 'parent-runtime', type: 'tool.start', payload: {tool_id: 'a', name: 'private-A'}})})
    await act(async () => {value.onEvent({session_id: 'child-runtime', type: 'tool.start', payload: {tool_id: 'foreign', name: 'child-tool'}})})
    await act(async () => {value.onEvent({session_id: 'child-runtime', type: 'message.complete', payload: {}})})
    expect(value.currentTools.some(call => call.id === 'foreign')).toBe(false)
    expect(value.currentTools).toHaveLength(1)
    expect(value.running).toBe(true)
    await act(async () => {value.onEvent({session_id: 'parent-runtime', type: 'session.info', payload: {stored_session_id: 'conversation', profile_name: 'A', running: true, tools: {forsic: ['session-only-tool']}, skills: {investigation: ['session-only-skill']}}})})
    expect(value.catalogueSource).toBe('profile configuration')
    expect(value.toolItems.map(item => item.name)).toEqual(['session-only-tool'])
    expect(value.skillItems).toEqual([])
    await act(async () => {root.render(<Probe profile="B" />)})
    expect(value.currentTools).toEqual([])
    expect(value.running).toBe(null)
    expect(value.catalogueSource).toBe('profile configuration')
    await act(async () => {resolveOld({requested_session_id: 'conversation', stored_session_id: 'private-A', profile_name: 'A'})})
    expect(value.currentTools).toEqual([])
    const readsBefore = fetchJSON.mock.calls.length
    await act(async () => {vi.advanceTimersByTime(10000)})
    expect(fetchJSON.mock.calls).toHaveLength(readsBefore)
    expect(fetchJSON.mock.calls.some(([url]) => String(url).includes('/messages'))).toBe(false)
    await act(async () => {value.onEvent({session_id: 'parent-runtime', type: 'session.info', payload: {stored_session_id: 'conversation', profile_name: 'B', running: false}})})
    await act(async () => {value.onEvent({session_id: 'parent-runtime', type: 'tool.start', payload: {tool_id: 'b', name: 'B'}})})
    await act(async () => {value.onReconnect()})
    expect(value.currentTools).toEqual([])
    expect(value.running).toBe(null)
    await act(async () => {value.onEvent({session_id: 'parent-runtime', type: 'session.info', payload: {stored_session_id: 'conversation', profile_name: 'B', running: true}})})
    await act(async () => {value.onEvent({session_id: 'parent-runtime', type: 'message.complete', payload: {}})})
    expect(value.running).toBe(false)
  } finally {await act(async () => {root.unmount()})}
})

it('shows only canonical Forsic summaries while its loader is enabled, deduplicates metadata and rejects stale reads', async () => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true})
  let resolveStale!: (value: unknown) => void
  let staticReads = 0
  fetchJSON.mockImplementation((url: string) => {
    if (url.includes('/live')) return Promise.resolve({requested_session_id: 'conversation', stored_session_id: 'conversation', profile_name: new URL(url, 'http://localhost').searchParams.get('profile')})
    if (url.includes('/toolsets')) return Promise.resolve([{enabled: url.includes('profile=A'), name: 'forsic', tools: ['forsic_skill']}])
    if (url.startsWith('/api/skills?')) return Promise.resolve([{name: 'ordinary', description: 'Profile summary', enabled: true}])
    if (url === '/dashboard-plugins/forsic/dist/skills.json') {
      if (++staticReads === 2) return new Promise(resolve => {resolveStale = resolve})
      return Promise.resolve([{name: 'curated-procedure', description: '정확한 조사 스킬 설명'}, {name: 'curated-procedure', description: 'Duplicate'}])
    }
    throw new Error('Unexpected endpoint')
  })
  let value!: ReturnType<typeof useForsicInspector>
  function Probe({profile}: {profile: string}) {value = useForsicInspector({profile, sessionId: 'conversation', active: true}); return null}
  const root = createRoot(document.createElement('div'))
  try {
    await act(async () => {root.render(<Probe profile="A" />)})
    expect(value.skillItems).toEqual([
      {name: 'curated-procedure', description: '정확한 조사 스킬 설명', group: '포식이 조사 스킬'},
    ])
    expect(fetchJSON.mock.calls.some(([url]) => String(url).startsWith('/api/skills?'))).toBe(false)
    expect(value.catalogueSource).toBe('profile configuration')
    await act(async () => {value.onEvent({session_id: 'runtime', type: 'session.info', payload: {stored_session_id: 'conversation', profile_name: 'A', tools: {forsic: ['forsic_skill']}, skills: {profile: ['ordinary', 'unregistered']}}})})
    expect(value.skillItems.map(item => item.name)).toEqual(['curated-procedure'])
    let pending!: Promise<void>
    await act(async () => {pending = value.refresh()})
    await act(async () => {root.render(<Probe profile="B" />)})
    expect(value.skillItems).toEqual([])
    await act(async () => {resolveStale([{name: 'stale-A', description: 'Must not appear'}]); await pending})
    expect(value.skillItems).toEqual([])
  } finally {await act(async () => {root.unmount()})}
})

it.each(['rejected', 'malformed'])('clears stale canonical metadata after %s refresh while preserving tools', async failure => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true})
  let fail = false
  fetchJSON.mockImplementation((url: string) => {
    if (url.includes('/live')) return Promise.resolve({requested_session_id: 'conversation', stored_session_id: 'conversation', profile_name: 'A'})
    if (url.includes('/toolsets')) return Promise.resolve([{enabled: true, name: 'forsic', tools: ['forsic_skill']}])
    if (url.startsWith('/api/skills?')) return Promise.resolve([{name: 'ordinary', description: 'Profile summary', enabled: true}])
    if (!fail) return Promise.resolve([{name: 'canonical', description: 'Current procedure'}])
    return failure === 'rejected' ? Promise.reject(new Error('Static metadata unavailable')) : Promise.resolve({unexpected: 'metadata'})
  })
  let value!: ReturnType<typeof useForsicInspector>
  function Probe() {value = useForsicInspector({profile: 'A', sessionId: 'conversation', active: true}); return null}
  const root = createRoot(document.createElement('div'))
  try {
    await act(async () => {root.render(<Probe />)})
    expect(value.skillItems.map(item => item.name)).toEqual(['canonical'])
    fail = true
    await act(async () => {await value.refresh()})
    expect(value.toolItems[0].name).toBe('forsic_skill')
    expect(value.skillItems).toEqual([])
    expect(value.catalogueError).toBeTruthy()
    expect(value.running).toBe(null)
  } finally {await act(async () => {root.unmount()})}
})

it('keeps returned asynchronous jobs out of active tool indicators through completion and reconnect', async () => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true})
  fetchJSON.mockImplementation((url: string) => Promise.resolve(url.includes('/live') ? {requested_session_id: 'conversation', stored_session_id: 'conversation', profile_name: new URL(url, 'http://localhost').searchParams.get('profile')} : []))
  let value!: ReturnType<typeof useForsicInspector>
  function Probe() {value = useForsicInspector({profile: 'A', sessionId: 'conversation', active: true}); return null}
  const root = createRoot(document.createElement('div'))
  try {
    await act(async () => {root.render(<Probe />)})
    await act(async () => {value.onEvent({session_id: 'parent-runtime', type: 'session.info', payload: {stored_session_id: 'conversation', profile_name: 'A', running: true}})})
    await act(async () => {value.onEvent({session_id: 'parent-runtime', type: 'tool.start', payload: {tool_id: 'job', name: 'forsic_verification'}})})
    expect(value.currentTools).toHaveLength(1)
    await act(async () => {value.onEvent({session_id: 'parent-runtime', type: 'tool.complete', payload: {tool_id: 'job', name: 'forsic_verification', result: {status: 'queued'}}})})
    expect(value.currentTools).toHaveLength(0)
    await act(async () => {value.onEvent({session_id: 'parent-runtime', type: 'message.complete', payload: {}})})
    await act(async () => {value.onReconnect()})
    expect(value.currentTools).toHaveLength(0)
  } finally {await act(async () => {root.unmount()})}
})

it.each([true, false])('accepts a compacted stored identity only after its exact read-only mapping is verified: %s', async valid => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true})
  let resolveMapping!: (value: unknown) => void
  fetchJSON.mockImplementation((url: string) => url.includes('/live') ? new Promise(resolve => {resolveMapping = resolve}) : Promise.resolve([]))
  let value!: ReturnType<typeof useForsicInspector>
  function Probe() {value = useForsicInspector({profile: 'A', sessionId: 'conversation', active: true}); return null}
  const root = createRoot(document.createElement('div'))
  try {
    await act(async () => {root.render(<Probe />)})
    await act(async () => {value.onEvent({session_id: 'compacted-runtime', type: 'session.info', payload: {stored_session_id: 'compacted-tip', profile_name: 'A', running: false}})})
    await act(async () => {resolveMapping({requested_session_id: 'conversation', stored_session_id: 'compacted-tip', profile_name: valid ? 'A' : 'other-profile'})})
    await act(async () => {value.onEvent({session_id: 'compacted-runtime', type: 'tool.start', payload: {tool_id: 'current', name: 'forsic_read'}})})
    expect(value.currentTools).toHaveLength(valid ? 1 : 0)
    await act(async () => {value.onEvent({session_id: 'foreign-runtime', type: 'tool.start', payload: {tool_id: 'foreign', name: 'foreign'}})})
    expect(value.currentTools.some(call => call.id === 'foreign')).toBe(false)
    if (valid) {
      await act(async () => {await value.refresh()})
      expect(fetchJSON.mock.calls.filter(([url]) => String(url).includes('/live'))).toHaveLength(1)
      await act(async () => {value.onEvent({session_id: 'compacted-runtime', type: 'tool.complete', payload: {tool_id: 'current', name: 'forsic_read', result: 'done'}})})
      expect(value.currentTools).toEqual([])
    }
    expect(fetchJSON.mock.calls.some(([url]) => String(url).includes('/messages'))).toBe(false)
  } finally {await act(async () => {root.unmount()})}
})
