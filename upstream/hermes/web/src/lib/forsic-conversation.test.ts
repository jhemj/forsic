import {expect, it} from 'vitest'
import {applyConversationEvent, conversationEventStatus, emptyConversation, fullSavedCalls, hydrateConversation, reconcileBufferedEvents, reconcileConversation, savedConversation} from './forsic-conversation'
import {toolDetail, toolStatusLabel} from './forsic-tool-detail'

it('keeps native bridge purpose and raw provenance across live, reloaded and compressed history projections', () => {
  const args = {command: 'x'.repeat(13000), reason: 'Compare authentication records'}
  const wrapper = {calls: [{name: 'forsic_shell', arguments: args}]}
  const result = '{"error":"Command failed","command_outcome":{"status":"failed"},"execution":{"exit_code":1}}'
  const messages = [
    {row_id: 1, role: 'assistant', tool_calls: [{id: 'native-id', function: {name: 'tool_call', arguments: JSON.stringify(wrapper)}}]},
    {row_id: 2, role: 'tool', tool_call_id: 'native-id', tool_name: 'forsic_shell', content: result},
  ]
  let live = applyConversationEvent(emptyConversation(), {type: 'tool.start', payload: {tool_id: 'native-id', name: 'forsic_shell', args}})
  live = applyConversationEvent(live, {type: 'tool.complete', payload: {tool_id: 'native-id', name: 'forsic_shell', result_text: result}})
  const reloaded = reconcileConversation(savedConversation(messages), live)
  expect(reloaded.rows).toHaveLength(1)
  for (const call of [live.rows[0].calls![0], reloaded.rows[0].calls![0], fullSavedCalls(messages)[0]]) {
    expect(call).toMatchObject({id: 'native-id', name: 'forsic_shell', status: 'error', argumentsTruncated: false})
    expect(toolDetail(call).purpose).toBe(args.reason)
    expect(toolDetail(call).details).toContainEqual({label: '명령', value: args.command})
    expect(toolStatusLabel(call)).toBe('실행 실패')
  }
  expect(reloaded.rows[0].calls![0].submittedRequest?.arguments).toBe(JSON.stringify(wrapper))
  const compressed = fullSavedCalls([messages[0], {...messages[1], content: '{"pruned":true,"body_omitted":true}'}])[0]
  expect(toolDetail(compressed).purpose).toBe(args.reason)
  expect(compressed.status).toBe('unknown')
})

it('keeps a compressed native batch as one unknown parent record with requested operation labels', () => {
  const argumentsText = JSON.stringify({calls: [{name: 'forsic_skill', arguments: {}}, {name: 'forsic_shell', arguments: {reason: 'Compare selected records'}}]})
  const [row] = savedConversation([
    {role: 'assistant', tool_calls: [{id: 'batch-id', function: {name: 'tool_call', arguments: argumentsText}}]},
    {role: 'tool', tool_call_id: 'batch-id', tool_name: 'tool_call', content: '[tool_call] calls=[...] (270 chars result)'},
  ])
  expect(row.calls).toHaveLength(1)
  expect(row.calls![0]).toMatchObject({id: 'batch-id', name: 'tool_call', arguments: argumentsText, status: 'unknown'})
  expect(toolDetail(row.calls![0])).toMatchObject({label: '조사 스킬 읽기 · 분석 터미널', purpose: 'Compare selected records'})
})

it('separates native wait phases from actual deltas and tool execution without exposing callback text', () => {
  const current = {statusText: '조사 중', running: true}
  const notices = [
    ['60s waiting for provider response headers', '잠시만요, 응답을 기다리고 있어요'],
    ['60s waiting for the first stream chunk', '연결됐어요. 첫 응답을 기다리고 있어요'],
    ['stream open; 60s without stream output', '잠시만요, 다음 응답을 기다리고 있어요'],
  ]
  for (const [phase, expected] of notices) for (const type of ['thinking.delta', 'status.update']) {
    const text = `⏳ still waiting on PRIVATE_MODEL — ${phase} (auto-reconnect: stream stale watchdog in 15s)`
    expect(conversationEventStatus(current, {type, payload: {text}})).toBe(expected)
    expect(applyConversationEvent(emptyConversation(), {type, payload: {text}}).rows).toEqual([])
  }
  expect(conversationEventStatus(current, {type: 'reasoning.delta', payload: {text: 'PRIVATE_CONTENT'}})).toBe('생각을 정리하고 있어요')
  expect(conversationEventStatus(current, {type: 'message.delta', payload: {text: 'PRIVATE_CONTENT'}})).toBe('확인한 내용을 전하고 있어요')
  expect(conversationEventStatus(current, {type: 'tool.start', payload: {name: 'PRIVATE_TOOL'}})).toBe('조사 작업을 진행하고 있어요')
  expect(conversationEventStatus(current, {type: 'status.update', payload: {text: '문맥 정리 중'}})).toBe('문맥 정리 중')
  for (const text of ['PRIVATE_CONTENT', 'waiting for provider response headers', '⏳ waiting on private — 60s waiting for provider response headers\nPRIVATE_CONTENT']) {
    expect(conversationEventStatus(current, {type: 'thinking.delta', payload: {text}})).toBe(current.statusText)
  }
})

it('keeps pause, reconnect and completed states authoritative over delayed progress events', () => {
  const events = ['thinking.delta', 'reasoning.delta', 'message.delta', 'tool.start', 'status.update'].map(type => ({type, payload: {text: 'PRIVATE_CONTENT'}}))
  for (const current of [
    {statusText: '일시 중지됨', running: true, paused: true},
    {statusText: '연결이 끊어졌습니다.', running: true, reconnecting: true},
    {statusText: '', running: false},
  ]) for (const event of events) expect(conversationEventStatus(current, event)).toBe(current.statusText)
  expect(conversationEventStatus({statusText: '생각을 정리하고 있어요', running: true}, {type: 'message.complete', payload: {status: 'completed'}})).toBe('')
  expect(conversationEventStatus({statusText: '생각을 정리하고 있어요', running: true}, {type: 'message.complete', payload: {status: 'interrupted'}})).toBe('일시 중지됨')
})

it('projects public narrative and full native tool results while hiding private reasoning and hidden turns', () => {
  const result = 'a'.repeat(20000)
  const rows = savedConversation([
    {row_id: 1, role: 'system', content: 'private system'},
    {row_id: 2, role: 'user', content: 'native continuation', display_kind: 'hidden'},
    {row_id: 3, role: 'assistant', content: 'Briefing', reasoning: 'private chain', tool_calls: [{id: 't', function: {name: 'read', arguments: '{"path":"x"}'}}]},
    {row_id: 4, role: 'tool', tool_call_id: 't', content: result},
    {id: 5, role: 'assistant', content: 'physical compaction wrapper', display_content: [{type: 'thinking', thinking: 'private chain'}, {type: 'text', text: 'Answer'}]},
  ])
  expect(rows.map(row => row.role)).toEqual(['assistant', 'tools', 'assistant'])
  expect(rows[1].calls?.[0].result).toBe(result)
  expect(rows[2].text).toBe('Answer')
  expect(rows[2].id).toBe('saved:5')
  expect(JSON.stringify(rows)).not.toContain('private')
})

it('seals streamed briefing once and uses committed final identity without leaking reasoning events', () => {
  let state = emptyConversation()
  for (const event of [
    {type: 'message.delta', seq: 1, payload: {text: 'Brief'}},
    {type: 'reasoning.delta', seq: 2, payload: {text: 'private'}},
    {type: 'message.interim', seq: 3, payload: {text: 'Brief', already_streamed: true}},
    {type: 'tool.start', seq: 4, payload: {tool_id: 't', name: 'read', args: {path: 'x'}}},
    {type: 'message.delta', seq: 5, payload: {text: 'partial'}},
    {type: 'message.complete', seq: 6, payload: {text: 'Final', persisted_turn: {final_assistant_row_id: 9}}},
  ]) state = applyConversationEvent(state, event)
  expect(state.rows.map(row => row.text).filter(Boolean)).toEqual(['Brief', 'Final'])
  expect(state.rows.at(-1)?.id).toBe('saved:9')
  expect(state.rows.some(row => row.streaming)).toBe(false)
  expect(applyConversationEvent(state, {type: 'message.complete', seq: 6, payload: {text: 'Final'}})).toBe(state)
  const saved = savedConversation([{row_id: 9, role: 'assistant', content: 'Final'}])
  expect(reconcileConversation(saved, state).rows.filter(row => row.text === 'Final')).toHaveLength(1)
})

it('restores in-flight public text and removes buffered overlap at a non-atomic snapshot boundary', () => {
  const state = hydrateConversation(emptyConversation(), {user: 'Question', user_row_id: 3, assistant: 'Hello world', streaming: true, reasoning: 'private'})
  const reconciled = reconcileBufferedEvents(state, [
    {type: 'message.delta', seq: 1, payload: {text: 'Hello '}},
    {type: 'message.delta', seq: 2, payload: {text: 'world, next'}},
    {type: 'message.interim', seq: 3, payload: {text: 'Hello world, next', already_streamed: true}},
  ])
  expect(reconciled.rows.map(row => row.text)).toEqual(['Question', 'Hello world, next'])
  expect(JSON.stringify(reconciled)).not.toContain('private')
})

it('does not overwrite a later streamed turn when a committed completion arrives late', () => {
  let state = applyConversationEvent(emptyConversation(), {type: 'message.complete', seq: 1, payload: {text: 'First', persisted_turn: {final_assistant_row_id: 1}}})
  state = applyConversationEvent(state, {type: 'message.delta', seq: 2, payload: {text: 'Second'}})
  state = applyConversationEvent(state, {type: 'message.complete', seq: 3, payload: {text: 'First', persisted_turn: {final_assistant_row_id: 1}}})
  expect(state.rows.map(row => row.text)).toEqual(['First', 'Second'])
  expect(state.rows.at(-1)?.streaming).toBe(true)
})

it('restores only server-projected public commentary before its tools and deduplicates a live briefing on reconnect', () => {
  const briefing = '확인한 기록의 범위를 조정했습니다. 다음으로 결과를 대조합니다.'
  const next = '실행 결과의 범위는 아직 확인하지 못했습니다.'
  const messages = [
    {row_id: 1, role: 'assistant', content: '', display_commentary: [briefing, next],
      codex_message_items: [{type: 'message', phase: 'commentary', content: [{type: 'output_text', text: 'UNREDACTED_PRIVATE'}]}],
      reasoning: 'PRIVATE_REASONING', tool_calls: [{id: 'read', function: {name: 'forsic_read', arguments: '{}'}}]},
    {row_id: 2, role: 'tool', tool_call_id: 'read', content: '{}'},
    {row_id: 3, role: 'assistant', content: '공개 최종 답변', display_commentary: []},
    {row_id: 4, role: 'assistant', content: '', display_commentary: [], codex_message_items: [{text: 'DISABLED_COMMENTARY'}]},
    {row_id: 5, role: 'assistant', display_kind: 'hidden', display_commentary: ['HIDDEN_COMMENTARY']},
  ]
  const saved = savedConversation(messages)
  expect(saved.map(row => [row.id, row.role, row.kind])).toEqual([
    ['saved:1:commentary:0', 'assistant', 'briefing'], ['saved:1:commentary:1', 'assistant', 'briefing'], ['tools:read', 'tools', undefined], ['saved:3', 'assistant', 'answer'],
  ])
  expect(JSON.stringify(saved)).not.toMatch(/PRIVATE|DISABLED|HIDDEN/)
  const live = applyConversationEvent(emptyConversation(), {type: 'message.interim', seq: 1, payload: {text: `${briefing}\n\n${next}`}})
  const once = reconcileConversation(saved, live)
  const twice = reconcileConversation(savedConversation(messages), once)
  expect(twice.rows).toEqual(saved)
  expect(twice.rows.filter(row => row.text === briefing)).toHaveLength(1)
})

it('keeps tool-associated public text as a briefing and failed-turn boundaries as notices', () => {
  const rows = savedConversation([
    {row_id: 1, role: 'assistant', content: '새 기록이 확인되어 범위를 좁힙니다.', tool_calls: [{id: 'a', function: {name: 'read', arguments: '{}'}}]},
    {row_id: 2, role: 'assistant', content: '이번 요청을 완료하지 못했습니다.', display_kind: 'failed_turn'},
  ])
  expect(rows[0].kind).toBe('briefing')
  expect(rows[2].kind).toBe('notice')
})

const nativeWarning = '\n\n[Tool loop warning: same_tool_failure_warning; count=4; forsic_question has failed 4 times this turn. Inspect the latest output before retrying.]'
it.each([
  ['scope limitation failure', JSON.stringify({evidence_id: 'synthetic-result', error: 'scope_limited에는 답변과 보고서에 남길 limitation이 필요합니다.', error_type: 'ValueError', failure_stage: 'execution'}) + nativeWarning, 'error'],
  ['invalid fields failure with JSON brackets in its error', JSON.stringify({evidence_id: 'synthetic-result', error: 'Invalid question fields: [{"path":"payload.answer","error":"Required"}]', error_type: 'ValueError'}) + nativeWarning, 'error'],
  ['native hard stop suffix', '{"error":"Question was not changed"}\n\n[Tool loop hard stop: same_tool_failure_halt; count=7; Diagnose before retrying.]', 'error'],
  ['successful result with repetition warning', '{"success":true,"question":{"answer":"A warning is not a tool failure"}}\n\n[Tool loop warning: idempotent_no_progress_warning; count=4; Use the earlier result.]', 'completed'],
  ['plain evidence text mentioning error', 'The source contains the word error and the text "error": "historical failure".', 'completed'],
  ['nested evidence error fields', JSON.stringify({content: {error: 'A historical recorded error'}, stdout: '{"error":"Recorded evidence"}'}) + nativeWarning, 'completed'],
  ['unknown trailing source prose', '{"error":"Quoted source data"}\n\nA source document continues here.', 'unknown'],
  ['unrecognized trailer code', '{"error":"Unverified body"}\n\n[Tool loop warning: invented_warning; count=4; Source text.]', 'unknown'],
  ['unclosed native trailer', '{"error":"Unverified body"}\n\n[Tool loop warning: same_tool_failure_warning; count=4; Cut off', 'unknown'],
  ['truncated JSON', '{"error":"Cut off', 'unknown'],
  ['compressed result without outcome', '{"pruned":true,"body_omitted":true,"tool":"forsic_question"}', 'unknown'],
  ['compressed result retaining explicit error', '{"pruned":true,"body_omitted":true,"error":"Question was not changed"}', 'error'],
  ['explicitly truncated result', '{"truncated":true,"question":{"answer":"Only a preview"}}', 'unknown'],
  ['verbose tail without complete result', '[showing verbose tail; omitted 42 chars]\n"error":"Unverified tail"}', 'unknown'],
])('projects %s consistently from saved and live native tool results while preserving raw text', (_name, result, status) => {
  const saved = fullSavedCalls([{role: 'tool', tool_call_id: 'question', tool_name: 'forsic_question', content: result}])[0]
  const fromHistory = savedConversation([{role: 'tool', tool_call_id: 'question', tool_name: 'forsic_question', content: result}])[0].calls?.[0]
  expect(saved.status).toBe(status)
  expect(fromHistory?.status).toBe(status)
  expect(saved.result).toBe(result)
  for (const field of ['result', 'result_text']) {
    const live = applyConversationEvent(emptyConversation(), {type: 'tool.complete', payload: {tool_id: 'question', name: 'forsic_question', [field]: result}}).rows[0].calls?.[0]
    expect(live?.status).toBe(status)
    expect(live?.result).toBe(result)
  }
})

it('uses complete native result metadata when verbose text is truncated and treats summary-only completion as unknown', () => {
  const state = applyConversationEvent(emptyConversation(), {type: 'tool.complete', payload: {
    tool_id: 'q', name: 'forsic_question', result: {success: true}, result_text: '[showing verbose tail; omitted 42 chars]\npartial output',
  }})
  expect(state.rows[0].calls?.[0].status).toBe('completed')
  const summaryOnly = applyConversationEvent(emptyConversation(), {type: 'tool.complete', payload: {tool_id: 'q', name: 'forsic_question', summary: 'Finished'}})
  expect(summaryOnly.rows[0].calls?.[0].status).toBe('unknown')
  const errorSummary = applyConversationEvent(emptyConversation(), {type: 'tool.complete', payload: {tool_id: 'q', name: 'forsic_question', summary: '{"error":"Question was not changed"}' + nativeWarning}})
  expect(errorSummary.rows[0].calls?.[0].status).toBe('error')
})

it('preserves an explicit native error status when only a summary is returned', () => {
  const state = applyConversationEvent(emptyConversation(), {type: 'tool.complete', payload: {
    tool_id: 'q', name: 'forsic_question', status: 'error', summary: 'Question was not changed',
  }})
  expect(state.rows[0].calls?.[0].status).toBe('error')
})

it('uses the saved tool request timestamp and never substitutes a tool completion time', () => {
  const rows = savedConversation([{role: 'assistant', timestamp: 1729393200, tool_calls: [{id: 'a', function: {name: 'forsic_read', arguments: '{}'}}]}, {role: 'tool', tool_call_id: 'a', timestamp: 1729393260, content: 'done'}, {role: 'tool', tool_call_id: 'orphan', timestamp: 1729393270, content: 'done'}])
  expect(rows[0].timestamp).toBe(1729393200)
  expect(rows[1].timestamp).toBeUndefined()
})

it('restores ordered native corrections at public assistant offsets without duplicating snapshots or canonical rows', () => {
 const inflight = {user: 'original', user_row_id: 1, assistant: 'beforeafter', streaming: true, corrections: ['same', 'same'], correction_offsets: [6, 6]}
 const once = hydrateConversation(emptyConversation(), inflight)
 expect(once.rows.map(row => [row.role, row.text])).toEqual([['user', 'original'], ['assistant', 'before'], ['user', 'same'], ['user', 'same'], ['assistant', 'after']])
 expect(hydrateConversation(once, inflight).rows).toEqual(once.rows)
 const saved = savedConversation([{row_id: 1, role: 'user', content: 'original'}, {row_id: 2, role: 'assistant', content: 'before'}, {row_id: 3, role: 'user', content: 'same'}, {row_id: 4, role: 'user', content: 'same'}])
 const restored = hydrateConversation(reconcileConversation(saved, once), inflight)
 expect(restored.rows.filter(row => row.role === 'user' && row.text === 'same')).toHaveLength(2)
 expect(restored.rows.filter(row => row.text === 'before')).toHaveLength(1)
})

it.each(['hidden', 'auto_continue'])('never exposes %s control input or its untyped corrections', display_kind => {
 const state = hydrateConversation(emptyConversation(), {display_kind, user: 'PRIVATE_CONTROL', user_row_id: 1, assistant: 'public', streaming: true, corrections: ['PRIVATE_CORRECTION'], correction_offsets: [0]})
 expect(state.rows.map(row => row.text)).toEqual(['public'])
 expect(savedConversation([{row_id: 1, role: 'user', content: 'PRIVATE_CONTROL', display_kind}, {row_id: 2, role: 'user', content: 'PRIVATE_SUMMARY', _compressed_summary: true}])).toEqual([])
})

it('retains corrections with unknown position when offset metadata is incomplete or invalid', () => {
 for (const correction_offsets of [undefined, [null], [99], [-1]]) {
  const state = hydrateConversation(emptyConversation(), {user: 'question', assistant: 'answer', corrections: ['correction'], correction_offsets})
  expect(state.rows.map(row => row.text)).toEqual(['question', 'answer', 'correction'])
  expect(state.rows.at(-1)?.positionUnknown).toBe(true)
 }
})

it('links only proven complete native final responses and preserves linkage through history refresh', () => {
 const rows = savedConversation([{row_id: 1, role: 'user', content: 'first question'}, {row_id: 2, role: 'user', content: 'new question'}])
 const original = {...emptyConversation(), rows}
 const event = {type: 'message.complete', seq: 1, payload: {text: 'first answer', persisted_turn: {complete: true, user_row_id: 1, final_assistant_row_id: 3, row_ids: [1, 3]}}}
 const proven = applyConversationEvent(original, event)
 expect(proven.rows.at(-1)?.replyTo).toBe('saved:1')
 const saved = savedConversation([{row_id: 1, role: 'user', content: 'first question'}, {row_id: 2, role: 'user', content: 'new question'}, {row_id: 3, role: 'assistant', content: 'first answer'}])
 expect(reconcileConversation(saved, proven).rows.at(-1)?.replyTo).toBe('saved:1')
 for (const complete of [false, undefined]) expect(applyConversationEvent(original, {...event, payload: {...event.payload, persisted_turn: {...event.payload.persisted_turn, complete}}}).rows.at(-1)?.replyTo).toBeUndefined()
})

it('restores explicitly visible corrections during hidden goal continuations while excluding private and legacy unknown inputs', () => {
 const inflight = {user: 'HIDDEN_GOAL', display_kind: 'hidden', user_row_id: 8, assistant: 'beforeafter', streaming: true, corrections: ['PRIVATE', '사용자 질문', 'UNKNOWN'], correction_offsets: [0, 6, 6], correction_display_kinds: ['hidden', 'visible', null]}
 const once = hydrateConversation(emptyConversation(), inflight)
 expect(once.rows.map(row => row.text)).toEqual(['before', '사용자 질문', 'after'])
 expect(hydrateConversation(once, inflight).rows).toEqual(once.rows)
 expect(JSON.stringify(once)).not.toMatch(/HIDDEN_GOAL|PRIVATE|UNKNOWN/)
})

it('never overwrites a newer question stream with a late proven reply to another user row', () => {
 const state = {...emptyConversation(), rows: savedConversation([{row_id: 1, role: 'user', content: 'old'}, {row_id: 2, role: 'user', content: 'new'}])}
 const streaming = applyConversationEvent(state, {type: 'message.delta', seq: 1, payload: {text: 'new partial'}})
 const complete = applyConversationEvent(streaming, {type: 'message.complete', seq: 2, payload: {text: 'old answer', status: 'complete', persisted_turn: {complete: true, user_row_id: 1, final_assistant_row_id: 3, row_ids: [1, 3]}}})
 expect(complete.rows.find(row => row.text === 'new partial')?.streaming).toBe(true)
 expect(complete.rows.find(row => row.id === 'saved:3')?.replyTo).toBe('saved:1')
})

it('uses native Unicode character offsets when restoring correction boundaries', () => {
 const state = hydrateConversation(emptyConversation(), {user: 'question', assistant: '🔎확인후속', corrections: ['추가 질문'], correction_offsets: [3]})
 expect(state.rows.map(row => row.text)).toEqual(['question', '🔎확인', '추가 질문', '후속'])
})

it('does not absorb a new visible correction into an identical old user question during a hidden turn', () => {
 const current = {...emptyConversation(), rows: savedConversation([{row_id: 1, role: 'user', content: '반복 질문'}, {row_id: 2, role: 'assistant', content: '옛 답변'}])}
 const state = hydrateConversation(current, {user: 'PRIVATE', user_row_id: 5, display_kind: 'hidden', assistant: '진행', streaming: true, corrections: ['반복 질문'], correction_offsets: [2], correction_display_kinds: ['visible']})
 expect(state.rows.filter(row => row.role === 'user' && row.text === '반복 질문')).toHaveLength(2)
 expect(state.rows.at(-1)?.text).toBe('반복 질문')
})

it('keeps visible questions with unknown offsets beside positioned corrections without invented reply links', () => {
 const inflight = {display_kind: 'hidden', user_row_id: 10, user: 'PRIVATE', assistant: 'beforeafter', corrections: ['legacy', '질문 A', '질문 B', 'PRIVATE correction', '질문 C'], correction_offsets: [null, null, 6, 0, 2], correction_display_kinds: [null, 'visible', 'visible', 'hidden', 'visible'], streaming: true}
 const once = hydrateConversation(emptyConversation(), inflight)
 expect(once.rows.map(row => row.text)).toEqual(['before', '질문 B', 'after', '질문 A', '질문 C'])
 expect(once.rows.filter(row => row.positionUnknown).map(row => row.text)).toEqual(['질문 A', '질문 C'])
 expect(hydrateConversation(once, inflight).rows).toEqual(once.rows)
 expect(once.rows.some(row => row.replyTo)).toBe(false)
 expect(JSON.stringify(once)).not.toMatch(/PRIVATE|legacy/)
})

it('settles a proven completion of the original native turn after restored corrections without leaving a duplicate stream', () => {
 const inflight = {user: 'question', user_row_id: 1, assistant: 'beforeafter', streaming: true, corrections: ['correction'], correction_offsets: [6], correction_display_kinds: ['visible']}
 const state = hydrateConversation(emptyConversation(), inflight)
 const done = applyConversationEvent(state, {type: 'message.complete', seq: 1, payload: {text: 'final', status: 'complete', persisted_turn: {complete: true, user_row_id: 1, final_assistant_row_id: 3, row_ids: [1, 3]}}})
 expect(done.rows.some(row => row.streaming)).toBe(false)
 expect(done.rows.filter(row => row.text === 'final')).toHaveLength(1)
 expect(done.rows.find(row => row.id === 'saved:3')?.replyTo).toBe('saved:1')
})

it('derives review activity only from current native in-progress receipts and keeps real request time separate', async () => {
 const {currentReviewActivity} = await import('./forsic-conversation')
 const item: import('./forsic-conversation').ReviewChatItem = {id: 'review:r:status', case_id: 'case', conversation_id: 'root', goal_id: 'goal', review_id: 'r', finding_id: null, speaker: 'system', kind: 'review_status', stage: 'checkpoint', round: null, public_text: '검토 중', public_reason: null, next_check: null, observed_at: '2026-10-20T12:00:00Z', time_basis: 'request_recorded', receipt_state: 'in_progress', freshness: 'current', historical: false, semantic_approval: false, response_ref: null, disposition: null}
 expect(currentReviewActivity([item])).toEqual({reviewId: 'r', startedAt: Date.parse(item.observed_at!) / 1000})
 expect(currentReviewActivity([{...item, freshness: 'stale'}])?.reviewId).toBe('r')
 expect(currentReviewActivity([{...item, observed_at: null}])).toEqual({reviewId: 'r'})
 expect(currentReviewActivity([{...item, time_basis: 'response_observed'}])).toEqual({reviewId: 'r'})
 for (const patch of [{historical: true}, {kind: 'review_question' as const}, {receipt_state: 'received'}, {receipt_state: 'cancelled'}, {receipt_state: 'delivery_unconfirmed'}]) expect(currentReviewActivity([{...item, ...patch}])).toBeNull()
})
