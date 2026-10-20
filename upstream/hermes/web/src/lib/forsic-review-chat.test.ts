import {expect, it, vi} from 'vitest'
import {readReviewChat, withReviewChat, type ReviewChatItem, type ReviewChatPage} from './forsic-conversation'

export const reviewItem = (changes: Partial<ReviewChatItem> = {}): ReviewChatItem => ({
  id: 'review:r:f', case_id: 'case', conversation_id: 'root', goal_id: 'goal', review_id: 'r', finding_id: 'f',
  speaker: 'ren', kind: 'review_question', stage: 'checkpoint', round: null, public_text: '실제 질문',
  public_reason: '공개 근거', next_check: '다음 확인', observed_at: null, time_basis: 'unknown', receipt_state: 'received',
  freshness: 'unknown', historical: false, semantic_approval: false, response_ref: null, disposition: null, ...changes,
})
const page = (items: ReviewChatItem[], changes: Partial<ReviewChatPage> = {}): ReviewChatPage => ({status: 'ready', profile: 'p', requested_session_id: 'tip', case_id: 'case', conversation_id: 'root', goal_id: 'goal', items, snapshot_id: 'snapshot', next_cursor: null, coverage: {scanned_receipts: 1, omitted_receipts: 0}, ...changes})

it('deduplicates by receipt identity while preserving separate equal questions and native ordering', () => {
  const native = [{id: 'n2', role: 'assistant' as const, text: 'later', timestamp: 20}, {id: 'n1', role: 'user' as const, text: 'earlier', timestamp: 10}]
  const item = reviewItem({observed_at: new Date(15000).toISOString(), time_basis: 'response_observed'})
  const rows = withReviewChat(native, [item, item, reviewItem({id: 'review:r:g', finding_id: 'g'})])
  expect(rows.filter(row => !row.review)).toEqual(native)
  expect(rows.filter(row => row.review)).toHaveLength(2)
  expect(native).toHaveLength(2)
  expect(rows[0].review).toEqual(item)
})
it('fetches independent pages without altering native cursors or dispatching work', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(page([reviewItem()], {next_cursor: 'next'})).mockResolvedValueOnce(page([reviewItem({id: 'review:r:g', finding_id: 'g'})]))
  const controller = new AbortController()
  const result = await readReviewChat(fetcher, 'p', 'tip', controller.signal)
  expect(result.items).toHaveLength(2)
  expect(fetcher.mock.calls[1][0]).toContain('cursor=next')
  expect(fetcher.mock.calls[0][1]).toEqual({signal: controller.signal, cache: 'no-store'})
})
it.each([
  {profile: 'foreign'}, {requested_session_id: 'foreign'},
  {items: [reviewItem({case_id: 'foreign'})]}, {items: [reviewItem({conversation_id: 'foreign'})]},
  {items: [reviewItem({goal_id: 'old', historical: false})]}, {status: 'unavailable'},
])('rejects mismatched or unavailable public scope %j', async changes => {
  await expect(readReviewChat(vi.fn().mockResolvedValue(page([reviewItem()], changes)), 'p', 'tip', new AbortController().signal)).rejects.toThrow()
})
it('rejects a changed snapshot across pages and aborted late responses', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(page([reviewItem()], {next_cursor: 'next'})).mockResolvedValueOnce(page([], {snapshot_id: 'new'}))
  await expect(readReviewChat(fetcher, 'p', 'tip', new AbortController().signal)).rejects.toThrow()
  const controller = new AbortController(); controller.abort()
  await expect(readReviewChat(vi.fn().mockResolvedValue(page([reviewItem()])), 'p', 'tip', controller.signal)).rejects.toThrow('cancelled')
})
