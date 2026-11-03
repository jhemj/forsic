// @vitest-environment jsdom
import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {ForsicRuntimeBar} from './ForsicRuntimeBar'
import type {InvestigationGoal, RuntimeWork} from '../lib/forsic-runtime'

let container: HTMLDivElement, root: Root
const origin = Date.parse('2026-10-20T00:00:00Z') / 1000
const fresh = (): InvestigationGoal => ({status: 'active', created_at: origin, runtime_started_at: origin, runtime_elapsed_seconds: 0, runtime_budget_seconds: 86400, runtime_accounting_known: true})
async function render(goal: InvestigationGoal | null, connected = true, work?: RuntimeWork) {await act(async () => root.render(<ForsicRuntimeBar goal={goal} connected={connected} work={work}/>))}
const progress = () => container.querySelector('[role=progressbar]')!
beforeEach(() => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true})
  vi.useFakeTimers(); vi.setSystemTime(origin * 1000)
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
})
afterEach(async () => {await act(async () => root.unmount()); container.remove(); vi.useRealTimers()})

it('tracks native active time across 12/18/24h without inventing checkpoint decisions or completion', async () => {
  let goal = fresh()
  vi.setSystemTime((origin + 43200 - 1) * 1000)
  await render(goal)
  expect(container.textContent).toContain('11시간 59분 59초')
  expect(container.textContent).toContain('시작 2026-10-20 09:00 KST')
  expect(container.querySelectorAll('.fc-runtime-checkpoints strong')[0].textContent).toBe('대기')
  await act(async () => {vi.advanceTimersByTime(1000)})
  expect(progress().getAttribute('aria-valuenow')).toBe('43200')
  expect(container.querySelectorAll('.fc-runtime-checkpoints strong')[0].textContent).toBe('대기')
  goal = {...goal, runtime_phase: 'checkpoint', runtime_checkpoint_due_seconds: 43200}
  await render(goal)
  expect(container.querySelectorAll('.fc-runtime-checkpoints strong')[0].textContent).toBe('검토 예정')
  goal = {...goal, runtime_phase: 'investigation', runtime_checkpoint_due_seconds: null, runtime_checkpoints: [{threshold_seconds: 43200, decision: 'continue', reason: '확인할 핵심 로그가 남음', decided_at: origin + 43200}]}
  await render(goal)
  expect(container.textContent).toContain('계속 조사')
  expect(container.textContent).toContain('확인할 핵심 로그가 남음')
  vi.setSystemTime((origin + 64800) * 1000)
  await act(async () => {vi.advanceTimersByTime(1000)})
  expect(container.querySelectorAll('.fc-runtime-checkpoints strong')[1].textContent).toBe('대기')
  goal = {...goal, runtime_phase: 'closing', runtime_checkpoints: [...goal.runtime_checkpoints!, {threshold_seconds: 64800, decision: 'finish', reason: '남은 한계를 정리', decided_at: origin + 64800}]}
  await render(goal)
  expect(container.querySelectorAll('.fc-runtime-checkpoints strong')[1].textContent).toBe('마무리 선택')
  vi.setSystemTime((origin + 86410) * 1000)
  await act(async () => {vi.advanceTimersByTime(1000)})
  expect(progress().getAttribute('aria-valuenow')).toBe('86400')
  expect(container.querySelector<HTMLElement>('.fc-runtime-current')!.style.left).toBe('100%')
  expect(container.textContent).not.toContain('조사 종료')
  expect(container.textContent).not.toContain('결과 정리됨')
  await render({...goal, runtime_checkpoints: [{threshold_seconds: 43200, decision: 'finish', reason: '답변과 보고서 작성으로 전환', decided_at: origin + 43200}]})
  expect(container.querySelectorAll('.fc-runtime-checkpoints strong')[0].textContent).toBe('마무리 선택')
  expect(container.querySelectorAll('.fc-runtime-checkpoints strong')[1].textContent).toBe('마무리 전환')
  expect(container.textContent).not.toContain('결과 정리됨')
})

it('freezes paused and disconnected clocks, uses resumed native segments, and preserves unknown or other budgets', async () => {
  vi.setSystemTime((origin + 3600) * 1000)
  await render(fresh())
  expect(progress().getAttribute('aria-valuenow')).toBe('3600')
  const paused = {...fresh(), status: 'paused', runtime_elapsed_seconds: 3600, runtime_started_at: null}
  await render(paused)
  expect(container.textContent).toContain('일시정지')
  expect(container.textContent).not.toContain(' · 예상')
  await act(async () => {vi.advanceTimersByTime(3600 * 1000)})
  expect(progress().getAttribute('aria-valuenow')).toBe('3600')
  const resumed = {...paused, status: 'active', runtime_started_at: origin + 7200}
  await render(resumed)
  expect(container.textContent).toContain('예상 2026-10-21 10:00 KST')
  await act(async () => {vi.advanceTimersByTime(1000)})
  expect(progress().getAttribute('aria-valuenow')).toBe('3601')
  await render(resumed, false)
  await act(async () => {vi.advanceTimersByTime(60000)})
  expect(progress().getAttribute('aria-valuenow')).toBe('3601')
  expect(container.textContent).toContain('마지막 확인')
  await render({...resumed, runtime_elapsed_seconds: 3661, runtime_started_at: origin + 7261})
  expect(progress().getAttribute('aria-valuenow')).toBe('3661')
  await render({...fresh(), runtime_accounting_known: false})
  expect(progress().hasAttribute('aria-valuenow')).toBe(false)
  expect(container.querySelector('.fc-runtime-current')).toBeNull()
  expect(container.textContent).toContain('조사 시간 확인 필요')
  await render({...fresh(), runtime_budget_seconds: 3600})
  expect(container.textContent).toContain('1h 최종 한도')
  expect(container.textContent).not.toContain('12h')
  expect(container.textContent).not.toContain('24h')
  await render({status: 'active'})
  expect(container.querySelector('[role=progressbar]')).toBeNull()
  expect(container.textContent).toContain('시간 한도 미확인')
  await render(null)
  expect(container.textContent).toBe('')
  await render(fresh(), false)
  expect(progress().hasAttribute('aria-valuenow')).toBe(false)
  expect(container.textContent).toContain('조사 시간 확인 필요')
  await render({...paused, status: 'done'})
  expect(container.textContent).toContain('조사 종료')
})

it('updates the work label from native tool and response state', async () => {
 const investigationGoal = {status: 'active', runtime_budget_seconds: 86400, runtime_elapsed_seconds: 0, runtime_started_at: Date.now() / 1000 - 3600, runtime_accounting_known: true}
 const work = {running: true, rows: [{id: 'tool', role: 'tools', calls: [{id: 'report', name: 'forsic_reporting', arguments: '{"action":"render"}', status: 'running', source: 'live' as const}]}] as RuntimeWork['rows']}
 await render(investigationGoal, true, work)
 expect(container.querySelector('.fc-runtime-bubble')?.textContent).toBe('보고서 작성 중')
 expect(container.querySelector('.fc-runtime-current')?.getAttribute('data-edge')).toBe('start')
 await render(investigationGoal, true, {...work, rows: [{id: 'reply', role: 'assistant', streaming: true, text: '답변'}]})
 expect(container.querySelector('.fc-runtime-bubble')?.textContent).toBe('답변 작성 중')
 const closingGoal = {...investigationGoal, runtime_phase: 'closing'}
 await render(closingGoal, true, work)
 expect(container.querySelector('.fc-runtime-bubble')?.textContent).toBe('마무리 중')
 await render(closingGoal, false, {...work, reconnecting: true})
 expect(container.querySelector('.fc-runtime-bubble')?.textContent).toBe('연결 확인 중')
})
