// @vitest-environment jsdom
import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {afterEach, beforeEach, expect, it} from 'vitest'
import {ForsicToolCall} from './ForsicToolCall'
import type {InspectorToolCall} from '../lib/forsic-inspector'

let container: HTMLDivElement, root: Root
beforeEach(() => {Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true}); container = document.createElement('div'); document.body.append(container); root = createRoot(container)})
afterEach(async () => {await act(async () => root.unmount()); container.remove()})
const shell = (patch: Partial<InspectorToolCall> = {}): InspectorToolCall => ({id: 'shell', name: 'forsic_shell', arguments: '{}', status: 'completed', source: 'saved', ...patch})
async function render(call: InspectorToolCall) {await act(async () => root.render(<ForsicToolCall call={call} />))}

it.each(['live', 'saved'] as const)('shows the explicit purpose before the command for %s shell calls and keeps failure details', async source => {
  const reason = '선택한 로그에서 실행 흔적을 확인합니다. ' + '확인 범위를 구분합니다. '.repeat(20)
  await render(shell({source, status: 'error', arguments: JSON.stringify({command: 'python inspect.py', reason}), result: '{"execution":{"exit_code":1},"stderr":"Missing input","preview_truncated":{"stdout":true}}'}))
  const heading = container.querySelector('.fc-tool-heading')!
  const purpose = heading.querySelector('.fc-tool-purpose')!
  const command = heading.querySelector('.fc-tool-target')!
  expect(purpose.textContent!.length).toBeLessThanOrEqual(181)
  expect(purpose.compareDocumentPosition(command) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  const detail = Array.from(container.querySelectorAll('dl > div')).find(row => row.querySelector('dt')?.textContent === '목적')
  expect(detail?.querySelector('dd')?.textContent).toBe(reason.trim())
  expect(command.textContent).toBe('python inspect.py')
  expect(container.querySelector('.fc-tool-badge')?.textContent).toBe('실패')
  expect(container.querySelector('.fc-tool-summary')?.textContent).toBe('Missing input')
  expect(container.querySelector('dl')?.textContent).toContain('일부 범위')
})
it('omits missing purpose and does not promote reasoning fields or returned descriptions', async () => {
  await render(shell({arguments: '{"command":"printf ok","reasoning":"private thought","reasoning_content":"private content"}', result: '{"reason":"returned purpose","description":"returned explanation"}'}))
  expect(container.querySelector('.fc-tool-purpose')).toBeNull()
  expect(container.querySelector('dl')?.textContent).not.toContain('목적')
  expect(container.textContent).not.toContain('private')
  expect(container.textContent).not.toContain('returned purpose')
})
it('renders public purpose as text without interpreting markup', async () => {
  await render(shell({arguments: JSON.stringify({command: 'printf ok', reason: '<img src=x onerror=bad()>Inspect the file'})}))
  expect(container.querySelector('.fc-tool-purpose')?.textContent).toBe('<img src=x onerror=bad()>Inspect the file')
  expect(container.querySelector('img')).toBeNull()
})

it.each(['live', 'saved'] as const)('prioritizes purpose in a collapsed %s card while preserving the complete command inside', async source => {
  const command = 'python inspect.py --input "' + 'long directory/'.repeat(30) + 'log.txt"'
  const reason = '로그의 인증 성공과 실패 기록을 비교합니다.'
  await render(shell({source, arguments: JSON.stringify({command, reason})}))
  const card = container.querySelector('details')!
  const summary = card.querySelector('summary')!
  expect(card.open).toBe(false)
  expect(summary.querySelector('.fc-tool-heading > :first-child')?.textContent).toBe(reason)
  expect(summary.querySelector('.fc-tool-kind')?.textContent).toBe('분석 터미널')
  expect(summary.querySelector('.fc-tool-target')?.textContent!.length).toBeLessThanOrEqual(81)
  expect(summary.textContent).not.toContain(command)
  const detail = Array.from(card.querySelectorAll('dl > div')).find(row => row.querySelector('dt')?.textContent === '명령')
  expect(detail?.querySelector('dd')?.textContent).toBe(command)
})

it.each([
  ['no_matches', '검색 결과 없음', 'neutral', 1], ['partial', '부분 처리', 'warning', 2],
  ['failed', '실행 실패', 'error', 7], ['completion_unknown', '완료 미확인', 'unknown', 0],
])('renders %s from returned metadata without success styling or conclusions', async (status, label, tone, exitCode) => {
  await render(shell({result: JSON.stringify({command_outcome: {status}, execution: {exit_code: exitCode}})}))
  const card = container.querySelector('.fc-tool-call')!
  expect(card.getAttribute('data-status')).toBe('completed')
  expect(card.getAttribute('data-command-outcome')).toBe(status)
  expect(card.getAttribute('data-outcome-tone')).toBe(tone)
  expect(container.querySelector('.fc-tool-badge')?.textContent).toBe(label)
  expect(container.querySelector('dl')?.textContent).toContain(`종료 코드${exitCode}`)
  expect(container.querySelector('dl')?.textContent).toContain('범위·해석')
  expect(container.textContent).not.toContain('도구 실행 성공')
  expect(container.textContent).toContain('원본 증거의 직접 관측을 뜻하지 않습니다')
  if (status === 'partial') expect(container.querySelector('.fc-tool-summary')?.textContent).toContain('일부 범위')
})
it('keeps running, rejected and legacy presentation independent from new outcome metadata', async () => {
  const result = '{"command_outcome":{"status":"partial"}}'
  await render(shell({status: 'running', result}))
  expect(container.querySelector('.fc-tool-badge')?.textContent).toBe('작업 접수')
  expect(container.querySelector('.fc-tool-call')?.hasAttribute('data-command-outcome')).toBe(false)
  await render(shell({status: 'running'}))
  expect(container.querySelector('.fc-tool-badge')?.textContent).toBe('진행 중')
  expect(container.querySelector('.fc-tool-call')?.getAttribute('data-pending')).toBe('true')
  await render(shell({status: 'error', result: '{"execution_rejected":true,"error":"Guardrail rejected","command_outcome":{"status":"returned"}}'}))
  expect(container.querySelector('.fc-tool-badge')?.textContent).toBe('실패')
  expect(container.querySelector('.fc-tool-summary')?.textContent).toBe('Guardrail rejected')
  await render(shell({result: '{"execution":{"exit_code":0}}'}))
  expect(container.querySelector('.fc-tool-badge')?.textContent).toBe('성공')
  expect(container.querySelector('.fc-tool-call')?.hasAttribute('data-command-outcome')).toBe(false)
})

it.each(['returned', 'no_matches', 'partial'])('keeps error styling and diagnostics when %s coexists with a call error', async status => {
  await render(shell({status: 'error', result: JSON.stringify({error: 'Capability discovery returned invalid metadata.', error_code: 'capability_discovery_invalid', execution: {state: 'returned', exit_code: 0}, command_outcome: {status}})}))
  expect(container.querySelector('.fc-tool-badge')?.textContent).toBe('실패')
  expect(container.querySelector('.fc-tool-call')?.hasAttribute('data-command-outcome')).toBe(false)
  expect(container.querySelector('.fc-tool-summary')?.textContent).toBe('Capability discovery returned invalid metadata.')
  expect(container.querySelector('dl')?.textContent).toContain('종료 코드0')
})
