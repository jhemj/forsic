import {expect, it} from 'vitest'
import {investigationWorkLabel, type InvestigationGoal, type RuntimeWork} from './forsic-runtime'
import {applyConversationEvent, emptyConversation, type ConversationRow} from './forsic-conversation'

it('classifies only current observed work and exact reporting actions, preserving pause, connection and native phase priority', () => {
  const goal: InvestigationGoal = {status: 'active'}
  const work: RuntimeWork = {running: true, rows: []}
  const label = (patch: Partial<RuntimeWork> = {}, state = goal, connected = true) => investigationWorkLabel(state, connected, {...work, ...patch})
  const toolRows = (name: string, action?: string): ConversationRow[] => applyConversationEvent(emptyConversation(), {type: 'tool.start', payload: {tool_id: 'current', name, args: action ? {action} : {}}}).rows
  for (const [action, expected] of [['compose', '보고서 정리 중'], ['render', '보고서 작성 중'], ['finalize', '보고서 최종본 확인 중'], ['review', '보고서 검토 중'], ['source', '보존 근거 조회 중'], ['state', '조사 상태 조회 중'], ['gaps', '남은 확인 조회 중'], ['execution', '실행 기록 조회 중'], ['review_inbox', '검토 의견 조회 중'], ['unknown', '조사 작업 중']]) {
    expect(label({rows: toolRows('forsic_reporting', action)})).toBe(expected)
  }
  expect(label({rows: toolRows('forsic_image_info')})).toBe('증거 파악 중')
  expect(label({rows: toolRows('forsic_shell')})).toBe('조사 중')
  const requested = toolRows('tool_call')
  requested[0].calls![0].requestedTools = [{name: 'forsic_reporting', arguments: '{"action":"render"}'}]
  expect(label({rows: requested})).toBe('보고서 작성 중')
  requested[0].calls![0].requestedTools!.push({name: 'forsic_read', arguments: '{}'})
  expect(label({rows: requested})).toBe('조사 작업 중')
  const rows = toolRows('forsic_reporting', 'render')
  for (const patch of [{source: 'saved' as const}, {result: '{"status":"accepted"}'}, {status: 'completed' as const}, {status: 'unknown' as const}]) {
    expect(label({rows: [{...rows[0], calls: [{...rows[0].calls![0], ...patch}]}]})).toBe('조사 중')
  }
  expect(label({rows: [...rows, {id: 'finished', role: 'assistant', kind: 'answer', text: '저장된 응답'}]})).toBe('조사 중')
  expect(label({rows: [{id: 'reply', role: 'assistant', streaming: true}]})).toBe('답변 작성 중')
  expect(label({statusText: '🗜️ compacting'})).toBe('문맥 정리 중')
  expect(label({reviewing: true})).toBe('독립 검토 중')
  expect(label({running: false})).toBe('응답 대기 중')
  expect(label({rows}, {...goal, runtime_phase: 'checkpoint'})).toBe('중간 점검 예정')
  expect(label({rows}, {...goal, runtime_phase: 'closing'})).toBe('마무리 중')
  expect(label({rows, pausing: true})).toBe('일시정지 준비 중')
  expect(label({rows}, {...goal, status: 'paused'})).toBe('일시정지')
  expect(label({rows}, {...goal, status: 'done'})).toBe('조사 종료')
  expect(label({rows}, goal, false)).toBe('연결 끊김')
  expect(label({rows, reconnecting: true}, goal, false)).toBe('연결 확인 중')
  expect(investigationWorkLabel(goal, true)).toBe('작업 상태 확인 중')
  expect(label({}, {...goal, runtime_elapsed_seconds: 80000})).toBe('조사 중')
})
