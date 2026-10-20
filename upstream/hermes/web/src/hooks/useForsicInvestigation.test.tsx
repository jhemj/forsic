// @vitest-environment jsdom
import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {afterEach, expect, it, vi} from 'vitest'
import {useForsicInvestigation} from './useForsicInvestigation'
import type {InvestigationView} from '@/lib/forsic-investigation'

const fetchJSON = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api', () => ({fetchJSON}))
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
afterEach(() => {fetchJSON.mockReset(); vi.useRealTimers()})
const view = (case_id = 'case-A'): InvestigationView => ({case_id, questions: [], observations: [], sources: [], timeline: []})
const source = {kind: 'source', id: 'E-sample', version: 'frozen-v1'}
async function harness() {
  let value!: ReturnType<typeof useForsicInvestigation>
  function Probe({active, profile, sessionId, caseId}: {active: boolean; profile: string; sessionId?: string; caseId?: string}) {value = useForsicInvestigation({active, profile, sessionId, caseId}); return null}
  const root = createRoot(document.createElement('div'))
  return {get value() {return value}, render: async (active: boolean, profile = 'A', sessionId: string | undefined = 'selected', caseId?: string) => {await act(async () => {root.render(<Probe active={active} profile={profile} sessionId={sessionId} caseId={caseId}/>)})}, close: async () => {await act(async () => {root.unmount()})}}
}
it('reads and polls only while visible, with explicit selected session and profile and no mutation', async () => {
  vi.useFakeTimers(); fetchJSON.mockResolvedValue(view())
  const probe = await harness()
  try {
    await probe.render(false); expect(fetchJSON).not.toHaveBeenCalled()
    await probe.render(true); expect(fetchJSON).toHaveBeenCalledTimes(1)
    const query = new URL(fetchJSON.mock.calls[0][0], 'https://example.invalid').searchParams
    expect(query.get('session_id')).toBe('selected'); expect(query.get('profile')).toBe('A')
    await act(async () => {vi.advanceTimersByTime(10000)})
    expect(fetchJSON).toHaveBeenCalledTimes(2)
    await probe.render(false)
    await act(async () => {vi.advanceTimersByTime(30000)})
    expect(fetchJSON).toHaveBeenCalledTimes(2); expect(probe.value.data).toBeNull()
    expect(fetchJSON.mock.calls.every(call => call.length === 1)).toBe(true)
  } finally {await probe.close()}
})
it('rejects late results across profile changes and hiding the pane', async () => {
  let resolveOld!: (data: unknown) => void
  fetchJSON.mockImplementation((url: string) => url.includes('profile=A') ? new Promise(resolve => {resolveOld = resolve}) : Promise.resolve(view('case-B')))
  const probe = await harness()
  try {
    await probe.render(true); await probe.render(true, 'B')
    await act(async () => {resolveOld(view('private-A'))})
    expect(probe.value.data?.case_id).toBe('case-B')
    await probe.render(true, 'A'); await probe.render(false, 'A')
    await act(async () => {resolveOld(view('private-A'))})
    expect(probe.value.data).toBeNull()
    await probe.render(true, 'B'); expect(probe.value.data?.case_id).toBe('case-B')
  } finally {await probe.close()}
})
it('checks source versions and case binding, and rejects source replies after scope changes', async () => {
  let resolvePage!: (data: unknown) => void
  fetchJSON.mockImplementation((url: string) => url.includes('/source-page') ? new Promise(resolve => {resolvePage = resolve}) : Promise.resolve(view()))
  const probe = await harness()
  try {
    await probe.render(true)
    const read = probe.value.readSource({source, pointer: '/body', offset: 10})
    const query = new URL(fetchJSON.mock.calls.at(-1)![0], 'https://example.invalid').searchParams
    expect(Object.fromEntries(query)).toMatchObject({case_id: 'case-A', profile: 'A', session_id: 'selected', source_id: 'E-sample', source_version: 'frozen-v1', pointer: '/body', offset: '10'})
    resolvePage({source_ref: {...source, version: 'wrong-version'}, text: 'untrusted'})
    await expect(read).rejects.toThrow('Source identity mismatch')
    const late = probe.value.readSource({source}); await probe.render(true, 'B')
    resolvePage({source_ref: source, text: 'old-private-source'})
    await expect(late).rejects.toThrow('Source identity mismatch')
    const reopened = probe.value.readSource({source}); await probe.render(false, 'B'); await probe.render(true, 'B')
    resolvePage({source_ref: source, text: 'previous-drawer-request'})
    await expect(reopened).rejects.toThrow('Source identity mismatch')
  } finally {await probe.close()}
})
it('rejects a mismatched overview identity instead of displaying it', async () => {
  fetchJSON.mockResolvedValue({...view(), session_id: 'foreign', profile: 'A'})
  const probe = await harness()
  try {await probe.render(true); expect(probe.value.data).toBeNull(); expect(probe.value.error).toBeTruthy()} finally {await probe.close()}
})

it('preserves a verified case-wide snapshot across conversations but immediately hides it for another case', async () => {
  let finish!: (value: unknown) => void
  fetchJSON.mockResolvedValue({...view(), scope: 'case', profile: 'A', session_id: 'first'})
  const probe = await harness()
  try {
    await probe.render(true, 'A', 'first', 'case-A')
    fetchJSON.mockImplementation(() => new Promise(resolve => {finish = resolve}))
    await probe.render(true, 'A', 'second', 'case-A')
    expect(probe.value.data?.case_id).toBe('case-A')
    await act(async () => {finish({...view(), scope: 'case', profile: 'A', session_id: 'second'})})
    await probe.render(true, 'A', 'third', 'case-B')
    expect(probe.value.data).toBeNull()
    await act(async () => {finish({...view(), scope: 'case', profile: 'A', session_id: 'third'})})
    expect(probe.value.data).toBeNull()
    expect(probe.value.error).toBeTruthy()
  } finally {await probe.close()}
})
