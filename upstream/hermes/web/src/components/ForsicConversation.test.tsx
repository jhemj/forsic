// @vitest-environment jsdom
import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {ForsicConversation, type ForsicConversationProps} from './ForsicConversation'
let container: HTMLDivElement, root: Root
beforeEach(() => {Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true}); container = document.createElement('div'); document.body.append(container); root = createRoot(container)})
afterEach(async () => {await act(async () => root.unmount()); container.remove()})
const defaults = (): ForsicConversationProps => ({rows: [], running: false, connected: true, onSend: vi.fn().mockResolvedValue(true)})
async function render(props: Partial<ForsicConversationProps> = {}) {const all = {...defaults(), ...props}; await act(async () => root.render(<ForsicConversation {...all} />)); return all}
async function type(value: string) {const input = container.querySelector('textarea')!; await act(async () => {Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', {bubbles: true}))}); return input}
async function submit() {await act(async () => {container.querySelector('form')!.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))})}

it('renders distinct chat bubbles, visible tool summaries with folded raw details, and inline activity only while replying', async () => {
 const props = await render({running: true, rows: [{id: 'u', role: 'user', text: '질문'}, {id: 'a', role: 'assistant', text: '**답변**', streaming: true}, {id: 't', role: 'tools', calls: [{id: 'c', name: 'forsic_read', arguments: '<script>bad()</script>', result: '<img src=x>', status: 'completed', source: 'saved'}]}]})
 expect(container.querySelector('.fc-user')?.textContent).toContain('나질문')
 expect(container.querySelector('.fc-assistant strong')?.textContent).toBe('답변')
 expect(container.querySelector('.fc-tools')?.hasAttribute('open')).toBe(false)
 expect(container.textContent).toContain('성공')
 expect(container.querySelector('script')).toBeNull()
 await act(async () => {container.querySelector<HTMLDetailsElement>('.fc-tool-raw')!.open = true; container.querySelector('.fc-tool-raw')!.dispatchEvent(new Event('toggle'))})
 expect(container.querySelector('.fc-tool-call pre')?.textContent).toBe('<script>bad()</script>')
 expect(container.querySelectorAll('.fc-reply-activity')).toHaveLength(1)
 await render({...props, running: false})
 expect(container.querySelector('.fc-reply-activity')).toBeNull()
})
it('guards composing Enter and Shift+Enter while keeping composer usable during execution', async () => {
 const onSend = vi.fn().mockResolvedValue(true)
 await render({running: true, onSend})
 const input = await type('한글')
 expect(input.disabled).toBe(false)
 for (const event of [new KeyboardEvent('keydown', {key: 'Enter', isComposing: true, bubbles: true}), new KeyboardEvent('keydown', {key: 'Enter', keyCode: 229, bubbles: true}), new KeyboardEvent('keydown', {key: 'Enter', shiftKey: true, bubbles: true})]) await act(async () => {input.dispatchEvent(event)})
 expect(onSend).not.toHaveBeenCalled()
 await act(async () => {input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}))})
 expect(onSend).toHaveBeenCalledExactlyOnceWith('한글')
 expect(input.value).toBe('')
})
it('prevents duplicate sends and preserves edits made while a message is in flight', async () => {
 let resolve!: (value: boolean) => void
 const onSend = vi.fn(() => new Promise<boolean>(r => {resolve = r}))
 await render({onSend}); await type('보낼 내용'); await submit(); await submit()
 expect(onSend).toHaveBeenCalledTimes(1)
 const input = await type('새로 작성하는 내용')
 await act(async () => {resolve(true)})
 expect(input.value).toBe('새로 작성하는 내용')
})
it.each(['false', 'throw'])('preserves the draft and reports uncertainty on %s', async failure => {
 const onSend = failure === 'false' ? vi.fn().mockResolvedValue(false) : vi.fn().mockRejectedValue(new Error('offline'))
 await render({onSend}); const input = await type('유지할 내용'); await submit()
 expect(input.value).toBe('유지할 내용')
 expect(container.querySelector('[role=alert]')?.textContent).toBe('전송 결과를 확인해 주세요. 작성한 내용은 유지했습니다.')
})
it('shows one dynamic pause or resume control and obeys connectivity', async () => {
 const onToggle = vi.fn(), props = await render({control: {paused: false, pending: false, onToggle}})
 expect(container.querySelectorAll('.fc-control')).toHaveLength(1)
 await act(async () => {container.querySelector<HTMLButtonElement>('.fc-control')!.click()})
 expect(onToggle).toHaveBeenCalledTimes(1)
 await render({...props, control: {paused: true, pending: false, disabled: true, onToggle}, connected: false})
 expect(container.querySelector('.fc-control')?.textContent).toBe('재개')
 expect(container.querySelector<HTMLButtonElement>('.fc-control')?.disabled).toBe(true)
})
it('does not pull the viewport back to the bottom while reading older messages', async () => {
 const props = await render({rows: [{id: 'a', role: 'assistant', text: '첫 답변'}]})
 const scroll = container.querySelector<HTMLDivElement>('.fc-viewport')!
 Object.defineProperties(scroll, {scrollHeight: {value: 2000, configurable: true}, clientHeight: {value: 500, configurable: true}})
 scroll.scrollTop = 400
 await act(async () => {scroll.dispatchEvent(new Event('scroll', {bubbles: true}))})
 await render({...props, rows: [...props.rows, {id: 'b', role: 'assistant', text: '추가 답변'}]})
 expect(scroll.scrollTop).toBe(400)
 scroll.scrollTop = 1480
 await act(async () => {scroll.dispatchEvent(new Event('scroll', {bubbles: true}))})
 await render({...props, rows: [...props.rows, {id: 'c', role: 'assistant', text: '새 답변'}]})
 expect(scroll.scrollTop).toBe(2000)
})

it('keeps explicit cold resume and idle drafting available when the hook permits them', async () => {
 const onToggle = vi.fn(), onSend = vi.fn().mockResolvedValue(true)
 await render({connected: false, inputDisabled: false, onSend, control: {paused: true, pending: false, disabled: false, onToggle}})
 const control = container.querySelector<HTMLButtonElement>('.fc-control')!
 expect(control.disabled).toBe(false); expect(control.textContent).toBe('재개')
 await act(async () => {control.click()})
 expect(onToggle).toHaveBeenCalledTimes(1)
 const input = await type('저장된 대화에 보낼 내용')
 expect(input.disabled).toBe(false)
 await submit()
 expect(onSend).toHaveBeenCalledExactlyOnceWith('저장된 대화에 보낼 내용')
})

it('lets welcome suggestions prepare an editable draft without sending a message', async () => {
 const onSend = vi.fn().mockResolvedValue(true)
 await render({onSend})
 const suggestion = container.querySelector<HTMLButtonElement>('.fc-starters button')!
 await act(async () => {suggestion.click()})
 const input = container.querySelector<HTMLTextAreaElement>('textarea')!
 expect(input.value).toBe('어떤 자료부터 확인하면 좋을까요?')
 expect(document.activeElement).toBe(input)
 expect(onSend).not.toHaveBeenCalled()
 await type('직접 고친 질문')
 await submit()
 expect(onSend).toHaveBeenCalledExactlyOnceWith('직접 고친 질문')
})

it('offers an explicit return to the latest conversation without interrupting earlier reading', async () => {
 await render({rows: [{id: 'a', role: 'assistant', text: '답변'}]})
 const scroll = container.querySelector<HTMLDivElement>('.fc-viewport')!
 Object.defineProperties(scroll, {scrollHeight: {value: 2000, configurable: true}, clientHeight: {value: 500, configurable: true}})
 scroll.scrollTop = 300
 await act(async () => {scroll.dispatchEvent(new Event('scroll', {bubbles: true}))})
 expect(scroll.scrollTop).toBe(300)
 const latest = container.querySelector<HTMLButtonElement>('.fc-latest')!
 expect(latest.textContent).toContain('최근 대화로')
 await act(async () => {latest.click()})
 expect(scroll.scrollTop).toBe(2000)
 expect(container.querySelector('.fc-latest')).toBeNull()
})

it('shows each action, target, returned range and outcome without expanding a generic group', async () => {
 await render({rows: [{id: 't', role: 'tools', calls: [
  {id: 'a', name: 'forsic_image_read', arguments: '{"file_path":"/var/log/example.log"}', result: '{"line_start":1,"line_end":80,"complete_file":false}', status: 'completed', source: 'saved'},
  {id: 'b', name: 'forsic_shell', arguments: '{"command":"python inspect.py"}', result: '{"stderr":"Missing input file","execution":{"exit_code":1}}', status: 'error', source: 'saved'},
 ]}]})
 const summaries = [...container.querySelectorAll('.fc-tool-call > summary')]
 expect(summaries).toHaveLength(2)
 expect(summaries[0].textContent).toContain('/var/log/example.log')
 expect(summaries[0].textContent).toContain('1–80행 · 일부 범위')
 expect(summaries[0].textContent).toContain('성공')
 expect(summaries[1].textContent).toContain('python inspect.py')
 expect(summaries[1].textContent).toContain('실패')
 expect(summaries[1].textContent).toContain('Missing input file')
 expect(container.querySelector('pre')).toBeNull()
 expect(container.textContent).not.toContain('도구 사용 내역')
})

it('does not animate an accepted asynchronous job as an active tool call', async () => {
 await render({rows: [{id: 't', role: 'tools', calls: [{id: 'q', name: 'forsic_verify', arguments: '{}', result: '{"status":"queued"}', status: 'running', source: 'saved'}]}]})
 expect(container.querySelector('.fc-tool-badge')?.textContent).toBe('작업 접수')
 expect(container.querySelector('.fc-tool-call')?.getAttribute('data-pending')).toBe('false')
})

it('replaces the analysis-screen entry with current context occupancy and distinguishes estimates and stale readings', async () => {
 await render({contextUsage: {used: 65536, maximum: 131072, estimated: false}})
 expect(container.textContent).not.toContain('분석 화면')
 expect(container.querySelector('.fc-context')?.textContent).toContain('문맥 65.5K / 131.1K · 50%')
 expect(container.querySelector('.fc-context')?.getAttribute('title')).toContain('최근 모델 입력 기준')
 await render({connected: false, contextUsage: {used: 20000, maximum: 131072, estimated: true}})
 expect(container.querySelector('.fc-context')?.textContent).toContain('추정 문맥')
 expect(container.querySelector('.fc-context')?.textContent).toContain('마지막 확인')
 await render({contextUsage: null})
 expect(container.querySelector('.fc-context')?.textContent).toBe('문맥 확인 중')
 expect(container.querySelector('.fc-context')?.textContent).not.toContain('0%')
})

it('keeps context footer free of redundant connection text while preserving notices and reply progress', async () => {
 const props = {rows: [
  {id: 'briefing', role: 'assistant' as const, kind: 'briefing' as const, text: '새 기록을 확인했습니다. 결과 로그를 대조합니다.'},
  {id: 'failed', role: 'assistant' as const, kind: 'notice' as const, text: '이전 요청을 완료하지 못했습니다.'},
 ]}
 await render({...props, connected: false, connectionText: '저장된 대화를 표시하고 있어요. 현재 연결을 다시 확인하고 있어요.'})
 expect(container.querySelector('.fc-message-kind')?.textContent).toBe('진행 안내')
 expect(container.querySelector('.fc-notice')?.textContent).toContain('이전 요청')
 expect(container.querySelector('.fc-connection')).toBeNull()
 expect(container.querySelector('.fc-reply-activity')).toBeNull()
 await render({...props, running: true, connectionText: '연결됨 · 조사 중'})
 expect(container.querySelector('.fc-connection')).toBeNull()
 expect(container.querySelector('.fc-reply-activity')).not.toBeNull()
})

it('shows one KST group timestamp only when the source timestamp is known', async () => {
  const calls = ['one', 'two'].map(id => ({id, name: 'forsic_read', arguments: '{}', status: 'completed' as const, source: 'saved' as const}))
  await render({rows: [{id: 'known', role: 'tools', calls, timestamp: Date.parse('2026-10-19T15:04:05Z') / 1000}, {id: 'unknown', role: 'tools', calls}]})
  expect(container.querySelectorAll('.fc-tool-group-heading time')).toHaveLength(1)
  expect(container.querySelector('.fc-tool-group-heading time')?.textContent).toBe('2026-10-20 00:04:05 KST')
})

it('folds adjacent routine lookup records while retaining live states, source time and every raw call', async () => {
  const calls = [
    {id: 'read-1', name: 'forsic_reporting', arguments: '{"action":"source","source_id":"E-example"}', result: '{"content":"retained excerpt"}', status: 'completed' as const, source: 'saved' as const},
    {id: 'read-2', name: 'forsic_reporting', arguments: '{"action":"execution"}', status: 'running' as const, source: 'live' as const},
    {id: 'read-3', name: 'forsic_reporting', arguments: '{"action":"state"}', status: 'unknown' as const, source: 'saved' as const},
  ]
  const props = {running: true, rows: calls.map((call, index) => ({id: call.id, role: 'tools' as const, calls: [call], ...(index === 0 ? {timestamp: Date.parse('2026-10-19T15:04:05Z') / 1000} : {})}))}
  await render(props)
  const history = container.querySelector<HTMLDetailsElement>('.fc-lookup-history')!
  expect(container.querySelectorAll('.fc-lookup-history')).toHaveLength(1)
  expect(history.open).toBe(false)
  expect(history.querySelector('summary')?.textContent).toContain('내부 조회 3건진행 중 1건결과 미확인 1건')
  expect(container.querySelectorAll('.fc-tools > .fc-tool-list > .fc-tool-call')).toHaveLength(0)
  expect(container.querySelector('time')?.textContent).toBe('2026-10-20 00:04:05 KST')
  await act(async () => {history.open = true; history.dispatchEvent(new Event('toggle'))})
  expect(history.querySelectorAll('.fc-tool-call')).toHaveLength(3)
  const raw = history.querySelector<HTMLDetailsElement>('.fc-tool-raw')!
  await act(async () => {raw.open = true; raw.dispatchEvent(new Event('toggle'))})
  expect(raw.textContent).toContain('retained excerpt')
  expect(raw.textContent).toContain('E-example')
  await render({...props, rows: [{...props.rows[0], calls: [calls[0], {...calls[1], result: '{"status":"queued"}'}, calls[2]]}]})
  expect(container.querySelector<HTMLDetailsElement>('.fc-lookup-history')?.open).toBe(true)
  expect(history.querySelector('summary')?.textContent).toContain('작업 접수 1건')
})

it('keeps failed lookups, explicit warnings, report writes and user notices visible outside folded reads', async () => {
  const read = (id: string, action: string, extra = {}): import('../lib/forsic-inspector').InspectorToolCall => ({id, name: 'forsic_reporting', arguments: JSON.stringify({action}), status: 'completed', source: 'saved', ...extra})
  await render({rows: [{id: 'mixed', role: 'tools', calls: [read('lookup', 'state'), read('failed', 'source', {status: 'error', result: '{"error":"Selected source unavailable"}'}), read('warning', 'execution', {result: '{"warning":"User approval required","requires_user_action":true}'}), read('report', 'render')]}, {id: 'notice', role: 'assistant', kind: 'notice', text: '사용자 확인이 필요합니다.'}], error: '연결을 확인해 주세요.'})
  expect(container.querySelector('.fc-tool-group-failures')?.textContent).toBe('실패 1건')
  const visible = [...container.querySelectorAll('.fc-tools > .fc-tool-list > .fc-tool-call > summary')]
  expect(visible).toHaveLength(3)
  expect(visible[0].textContent).toContain('실패')
  expect(visible[0].textContent).toContain('Selected source unavailable')
  expect(visible[1].textContent).toContain('User approval required')
  expect(visible[2].textContent).toContain('보고서 작성')
  expect(container.querySelector('.fc-notice')?.textContent).toContain('사용자 확인')
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('연결을 확인')
})

it('shows verified Ren receipts and saved Forsic dispositions as text records with explicit time basis', async () => {
 const item: import('../lib/forsic-conversation').ReviewChatItem = {id: 'review:r:f', case_id: 'case', conversation_id: 'root', goal_id: 'old-goal', review_id: 'r', finding_id: 'f', speaker: 'ren', stage: 'final', public_reason: '공개 근거', next_check: '다음 검사', receipt_state: 'received', semantic_approval: false, response_ref: null, kind: 'executive_question', round: 2, public_text: '<script>attack()</script> [link](javascript:bad())',
   observed_at: '2025-01-01T00:00:00Z', time_basis: 'request_recorded', historical: true, freshness: 'stale',
   disposition: {question_id: 'Q-1', revision: 4, disposition: 'scope_limited', reason: '저장된 처분', limitation: '미확인 범위', citation_refs: []}}
 await render({rows: [{id: 'ordinary', role: 'assistant', text: '렌 review-conclusions 임원 반론'}, {id: item.id, role: 'assistant', review: item}]})
 expect(container.querySelector('.fc-assistant .fc-author')?.textContent).toBe('포식이')
 expect(container.querySelector('.fc-ren .fc-author')?.textContent).toContain('렌임원 관점 질문 · 2/3회')
 expect(container.textContent).toContain('과거 목표의 검토 이력')
 expect(container.textContent).toContain('요청 시각')
 expect(container.textContent).toContain('포식이과거 검토 답변 기록')
 expect(container.textContent).toContain('저장된 처분')
 expect(container.querySelector('script')).toBeNull()
 expect(container.querySelector('.fc-review a')).toBeNull()
})


it('renders a saved response as a separate reply and leaves unanswered review questions explicit', async () => {
 const item: import('../lib/forsic-conversation').ReviewChatItem = {id: 'review:r:f', case_id: 'case', conversation_id: 'root', goal_id: 'goal', review_id: 'review-1', finding_id: 'finding-1', speaker: 'ren', stage: 'final', public_reason: null, next_check: null, receipt_state: 'received', semantic_approval: false, response_ref: null, kind: 'review_question', round: null, public_text: '실행 주체를 확인했나요?', observed_at: null, time_basis: 'unknown', historical: false, freshness: 'current', disposition: {question_id: 'Q-1', revision: 3, disposition: 'investigated', reason: '독립 자료로 확인했습니다.', limitation: '일부 시각은 미확인', citation_refs: ['note:observation-1', '<script>ref</script>']}}
 await render({rows: [{id: item.id, role: 'assistant', review: item}]})
 const question = container.querySelector('.fc-ren')!, reply = container.querySelector('.fc-review-answer')!
 expect(question.contains(reply)).toBe(false)
 expect(reply.previousElementSibling).toBe(question)
 expect(reply.querySelector('.fc-reply-quote p')?.textContent).toBe(item.public_text)
 expect(reply.textContent).toContain(item.disposition!.reason)
 expect(reply.textContent).toContain('일부 시각은 미확인')
 expect(reply.textContent).toContain('Q-1 · 수정 3')
 expect(reply.textContent).toContain('review-1 · 의견 finding-1 · 목표 goal')
 expect(Array.from(reply.querySelectorAll('li')).map(el => el.textContent)).toEqual(item.disposition!.citation_refs)
 expect(container.querySelector('script')).toBeNull()
 await render({rows: [{id: item.id, role: 'assistant', review: {...item, disposition: null}}]})
 expect(container.querySelector('.fc-review-answer')).toBeNull()
 expect(container.querySelector('.fc-review-waiting')?.textContent).toBe('포식이 답변 미기록')
})

it('quotes public user context once, skips tools and notices, and jumps to the exact user message', async () => {
 const {savedConversation} = await import('../lib/forsic-conversation')
 const rows = savedConversation([
  {row_id: 1, role: 'user', display_kind: 'hidden', content: 'private continuation'},
  {row_id: 2, role: 'assistant', content: '앞선 내용'},
  {row_id: 3, role: 'user', content: 'raw instruction', display_content: '공개 질문\n<script>text</script>'},
  {row_id: 4, role: 'assistant', tool_calls: [{id: 'tool', function: {name: 'lookup', arguments: '{}'}}], content: '자료를 확인합니다.'},
  {row_id: 5, role: 'assistant', content: '결과입니다.'},
  {row_id: 6, role: 'user', content: '새 질문'},
  {row_id: 7, role: 'assistant', display_kind: 'failed_turn', content: '중단 안내'},
  {row_id: 8, role: 'assistant', content: '새 답변'},
 ])
 await render({rows})
 const quotes = Array.from(container.querySelectorAll<HTMLButtonElement>('button.fc-reply-quote'))
 expect(quotes.map(el => el.querySelector('p')?.textContent)).toEqual(['공개 질문\n<script>text</script>', '새 질문'])
 expect(container.textContent).not.toContain('private continuation')
 expect(container.textContent).not.toContain('raw instruction')
 expect(container.querySelector('script')).toBeNull()
 await act(async () => quotes[0].click())
 const user = container.querySelector('.fc-question-highlight')!
 expect(user.querySelector('.fc-user-text')?.textContent).toBe('공개 질문\n<script>text</script>')
 expect(document.activeElement).toBe(user)
 expect(container.querySelector('.fc-tools .fc-reply-quote')).toBeNull()
 expect(container.querySelector('.fc-notice .fc-reply-quote')).toBeNull()
})

it('uses a proven native reply target instead of the latest unrelated user question', async () => {
 await render({rows: [{id: 'saved:1', role: 'user', text: '원 질문'}, {id: 'saved:2', role: 'user', text: '새 질문'}, {id: 'saved:3', role: 'assistant', text: '원 질문 답변', replyTo: 'saved:1'}]})
 const quote = container.querySelector<HTMLButtonElement>('button.fc-reply-quote')!
 expect(quote.querySelector('span')?.textContent).toBe('이 질문에 대한 답변 · 나')
 expect(quote.querySelector('p')?.textContent).toBe('원 질문')
 await act(async () => quote.click())
 expect(container.querySelector('.fc-question-highlight .fc-user-text')?.textContent).toBe('원 질문')
 expect(container.textContent).not.toContain('답변 완료')
})

it('labels unknown-position accepted input and does not pair a later answer with it', async () => {
 await render({rows: [{id: 'q', role: 'user', text: '위치 미상 질문', positionUnknown: true}, {id: 'answer', role: 'assistant', text: '독립 응답'}]})
 expect(container.querySelector('.fc-user .fc-message-kind')?.textContent).toBe('수신 위치 미확인')
 expect(container.querySelector('.fc-user-text')?.textContent).toBe('위치 미상 질문')
 expect(container.querySelector('button.fc-reply-quote')).toBeNull()
})

it('switches pending identity to Ren only during actual review and restores Forsic without renaming its transcript', async () => {
 const rows: import('../lib/forsic-conversation').ConversationRow[] = [{id: 'a', role: 'assistant', text: '포식이의 기존 답변'}]
 await render({rows, running: true, reviewActivity: {reviewId: 'r'}})
 expect(container.querySelector('.fc-pending .fc-author')?.textContent).toBe('렌')
 expect(container.querySelector('.fc-pending img')?.getAttribute('src')).toBe('/assets/ren-reviewer.svg')
 expect(container.querySelector('.fc-pending [role=status]')?.textContent).toBe('독립 질문을 정리하고 있어요')
 expect(container.querySelector('.fc-pending .fc-reply-activity')).toBeNull()
 expect(container.querySelector('.fc-assistant .fc-author')?.textContent).toBe('포식이')
 await render({rows, running: true, reviewActivity: null})
 expect(container.querySelector('.fc-pending .fc-author')?.textContent).toBe('포식이')
 expect(container.querySelector('.fc-pending img')).toBeNull()
 await render({rows, running: false, reviewActivity: null})
 expect(container.querySelector('.fc-pending')).toBeNull()
})

it('shows review elapsed time only from a real recorded start', async () => {
 vi.useFakeTimers()
 try {
  vi.setSystemTime(new Date('2026-10-20T12:01:02Z'))
  await render({reviewActivity: {reviewId: 'r', startedAt: Date.parse('2026-10-20T12:00:00Z') / 1000}})
  expect(container.querySelector('.fc-review-elapsed')?.textContent).toContain('1m 2s')
  await act(async () => {vi.advanceTimersByTime(1000)})
  expect(container.querySelector('.fc-review-elapsed')?.textContent).toContain('1m 3s')
  await render({reviewActivity: {reviewId: 'r2'}})
  expect(container.querySelector('.fc-review-elapsed')).toBeNull()
 } finally {vi.useRealTimers()}
})
