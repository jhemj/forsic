// @vitest-environment jsdom
import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {afterEach, expect, it, vi} from 'vitest'
import {useForsicReviewHistory} from './useForsicReviewHistory'
import type {ReviewHistory, ReviewHistoryItem} from '@/lib/forsic-review-history'
const fetchJSON = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api', () => ({fetchJSON}))
const entry = (id: string): ReviewHistoryItem => ({review_id: id, finding_id: id, question: id, stage: 'final', current_goal: false, currentness: 'not_revalidated', status: 'pending', responses: []})
const page = (caseId = 'synthetic', session = 'a'): ReviewHistory => ({case_id: caseId, session_id: session, profile: 'default', scope: 'case', snapshot: 'snapshot', offset: 0, next_offset: null, total: 1, items: [entry('old-round')], counts: {total: 1, answered: 0, pending: 1, historical_answer: 0, historical_goal: 1, receipts: 1}, issues: []})
let latest: ReturnType<typeof useForsicReviewHistory>
let dispose = () => {}
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
afterEach(() => {dispose(); document.body.innerHTML = ''; fetchJSON.mockReset()})
async function render(initial: {caseId: string; sessionId: string; profile?: string; active?: boolean}) {
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host)
  function Probe(props: typeof initial) {latest = useForsicReviewHistory({...props, profile: props.profile ?? 'default', active: props.active ?? true}); return <p>{latest.data?.case_id}:{latest.data?.items.map(item => item.question).join('|')}:{latest.error}</p>}
  const update = async (props: typeof initial) => {await act(async () => root.render(<Probe {...props}/>))}
  await update(initial); dispose = () => act(() => root.unmount())
  return {host, update}
}
it('reads every page with the original snapshot and never omits earliest rounds', async () => {
  fetchJSON.mockImplementation(async (url: string) => {
    const query = new URL(url, 'http://synthetic').searchParams
    if (query.get('offset') === '0') return {...page(), total: 2, next_offset: 1}
    expect(query.get('snapshot')).toBe('snapshot')
    return {...page(), offset: 1, total: 2, items: [entry('latest-final')]}
  })
  const {host} = await render({caseId: 'synthetic', sessionId: 'a'})
  expect(fetchJSON).toHaveBeenCalledTimes(2)
  expect(host.textContent).toContain('old-round|latest-final')
  expect(latest.data?.items).toHaveLength(2)
  expect(fetchJSON.mock.calls.every(([, options]) => !options.method || options.method === 'GET')).toBe(true)
})
it('suppresses an in-flight old case after native session switching and clears other profiles', async () => {
  let release!: (value: ReviewHistory) => void
  fetchJSON.mockImplementationOnce(() => new Promise(resolve => {release = resolve})).mockResolvedValue(page('other-case', 'b'))
  const {host, update} = await render({caseId: 'synthetic', sessionId: 'a'})
  await update({caseId: 'other-case', sessionId: 'b'})
  await act(async () => release(page()))
  expect(latest.data?.case_id).toBe('other-case')
  expect(host.textContent).not.toContain('synthetic')
  fetchJSON.mockImplementation(() => new Promise(() => {}))
  await update({caseId: 'other-case', sessionId: 'b', profile: 'another'})
  expect(latest.data).toBeNull()
  expect(host.textContent).not.toContain('old-round')
})
it('rejects cross-case identity and changed snapshots instead of combining records', async () => {
  fetchJSON.mockResolvedValueOnce({...page(), total: 2, next_offset: 1}).mockResolvedValueOnce({...page('foreign'), total: 2, offset: 1, items: [entry('foreign')]})
  await render({caseId: 'synthetic', sessionId: 'a'})
  expect(latest.data).toBeNull(); expect(latest.error).not.toBeNull()
})
it('keeps the last complete snapshot on a failed read-only refresh', async () => {
  fetchJSON.mockResolvedValueOnce(page()).mockRejectedValueOnce(new Error('unavailable'))
  await render({caseId: 'synthetic', sessionId: 'a'})
  await act(async () => latest.refresh())
  expect(latest.data?.items[0].question).toBe('old-round')
  expect(latest.error).not.toBeNull()
})
