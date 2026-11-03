// @vitest-environment jsdom
import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {afterEach, expect, it, vi} from 'vitest'
import type {ReviewHistory, ReviewHistoryItem, ReviewHistoryResponse} from '@/lib/forsic-review-history'
import {ForsicReviewHistory} from './ForsicReviewHistory'
const answer: ReviewHistoryResponse = {question_id: 'Q-synthetic', revision: 2, note_event_id: 'note-2', recorded_at: 1, disposition: 'scope_limited', reason: 'Retained original answer.', limitation: 'Bounded synthetic scope.', citation_refs: [], applicability: 'historical_note', validation: 'validated_saved_response'}
const item: ReviewHistoryItem = {review_id: 'review-synthetic', finding_id: 'finding-synthetic', question: 'Was the bounded activity verified?', why: 'Synthetic reason.', next_check: 'Read the retained result.', stage: 'final', executive_round: 1, current_goal: false, currentness: 'not_revalidated', status: 'answered', responses: [answer, {...answer, note_event_id: 'note-3', revision: 3, reason: 'Later changed answer.', applicability: 'current_note'}]}
const data: ReviewHistory = {case_id: 'case-synthetic', session_id: 'session', profile: 'default', scope: 'case', snapshot: 'snapshot', offset: 0, next_offset: null, total: 3, counts: {total: 3, answered: 1, pending: 1, historical_answer: 1, historical_goal: 2, receipts: 3}, issues: [], items: [item, {...item, review_id: 'review-latest', finding_id: 'finding-latest', executive_round: undefined, question: 'Latest final-review question?', current_goal: true, status: 'pending', responses: []}, {...item, review_id: 'review-old', finding_id: 'finding-old', executive_round: 3, status: 'historical_answer', responses: [answer]}]}
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
let dispose = () => {}
afterEach(() => {dispose(); document.body.innerHTML = ''})
async function render(props: {data: ReviewHistory | null; loading: boolean; error: string | null}) {
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  await act(async () => root.render(<ForsicReviewHistory {...props}/>))
  dispose = () => act(() => root.unmount())
  return host
}
it('shows original and changed answers, historical goals, pending final findings and honest totals', async () => {
  const host = await render({data, loading: false, error: null})
  expect(host.querySelectorAll('.fi-review-question')).toHaveLength(3)
  expect(host.textContent).toContain('Retained original answer.')
  expect(host.textContent).toContain('Later changed answer.')
  expect(host.textContent).toContain('경영진 질문 1차')
  expect(host.textContent).toContain('경영진 질문 3차')
  const latest = host.querySelectorAll('.fi-review-question')[1]
  expect(latest.textContent).toContain('최종 검토 · 현재 조사')
  expect(latest.textContent).toContain('답변 대기 · 답변 미기록')
  expect(host.querySelectorAll('.fi-review-question')[2].textContent).toContain('현재 질문에 적용되는 답변은 확인되지 않았어요.')
  expect(host.querySelector('[aria-label="독립 검토 전체 기록 수"]')?.textContent).toContain('3전체 질문')
  expect(host.querySelector('.fi-review-metadata code')?.closest('details')?.hasAttribute('open')).toBe(false)
})
it.each([{loading: true, error: null, text: '전체 검토 기록을 불러오고 있어요.'}, {loading: false, error: '조회 실패', text: '조회 실패'}])('shows loading and error honestly: $text', async ({loading, error, text}) => {
  const host = await render({data: null, loading, error}); expect(host.textContent).toContain(text)
})
it('shows empty and unavailable retained records without inventing answers', async () => {
  const host = await render({data: {...data, items: [], total: 0, issues: [{status: 'response_unavailable', message: '완전한 응답이 없습니다.'}]}, loading: false, error: null})
  expect(host.textContent).toContain('저장된 렌의 검토 질문이 없어요.')
  expect(host.textContent).toContain('확인하지 못한 검토 기록 1건')
  expect(host.querySelectorAll('.fi-review-response')).toHaveLength(0)
})
