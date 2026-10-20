import {applyInspectorEvent, savedToolCalls, type InspectorEvent, type InspectorToolCall} from './forsic-inspector'

export interface ConversationRow {
  id: string
  role: 'user' | 'assistant' | 'tools'
  text?: string
  calls?: InspectorToolCall[]
  streaming?: boolean
  timestamp?: number
  kind?: 'briefing' | 'answer' | 'notice'
  speaker?: 'ren' | 'forsic' | 'system'
  turnUserId?: string
  positionUnknown?: boolean
  replyTo?: string
  review?: ReviewChatItem
}
export interface PublicConversation {rows: ConversationRow[]; nextId: number; lastSeq: number; epoch?: string; turn: number}
export const emptyConversation = (): PublicConversation => ({rows: [], nextId: 0, lastSeq: 0, turn: 0})
export const asRecord = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
interface ConversationStatus {statusText: string; running: boolean; paused?: boolean; reconnecting?: boolean}
/** Status chrome reports native progress only; callback text never becomes public reasoning. */
export function conversationEventStatus(current: ConversationStatus, event: InspectorEvent): string {
  if (event.type === 'message.complete') return asRecord(event.payload).status === 'interrupted' ? '일시 중지됨' : ''
  if (current.paused || current.reconnecting) return current.statusText
  if (event.type === 'message.start') return '조사를 준비하고 있어요'
  if (!current.running) return current.statusText
  const text = asRecord(event.payload).text
  if (event.type === 'reasoning.delta' && typeof text === 'string' && text.length) return '생각을 정리하고 있어요'
  if (event.type === 'message.delta' && typeof text === 'string' && text.length) return '확인한 내용을 전하고 있어요'
  if (event.type === 'tool.start') return '조사 작업을 진행하고 있어요'
  if ((event.type === 'thinking.delta' || event.type === 'status.update') && typeof text === 'string' && text.length <= 1024) {
    const wait = /^⏳ (?:still )?waiting on [^\r\n]{1,256} — (?:\d{1,9}s (waiting for provider response headers|waiting for the first stream chunk)|stream open; \d{1,9}s (without stream output))(?: \(auto-reconnect: [A-Za-z -]{1,64} watchdog in \d{1,9}s\))?$/.exec(text)
    if (wait?.[1] === 'waiting for provider response headers') return '잠시만요, 응답을 기다리고 있어요'
    if (wait?.[1] === 'waiting for the first stream chunk') return '연결됐어요. 첫 응답을 기다리고 있어요'
    if (wait?.[2] === 'without stream output') return '잠시만요, 다음 응답을 기다리고 있어요'
  }
  // Other status.update messages already belong to the public status channel.
  if (event.type === 'status.update' && typeof text === 'string') return text
  return current.statusText
}
const string = (value: unknown): string => typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 2)
function publicText(value: unknown): string {
  if (typeof value === 'string') return value
  if (!Array.isArray(value)) return ''
  return value.map(asRecord).filter(part => part.type === 'text').map(part => typeof part.text === 'string' ? part.text : '').join('\n')
}
// append_toolguard_guidance appends this native trailer outside the JSON result.
// Do not scan arbitrary prose, nested evidence or stdout for error-like content.
const nativeToolGuidance = /\n\n\[Tool loop (?:warning: (?:same_tool_failure_warning|repeated_exact_failure_warning|idempotent_no_progress_warning)|hard stop: (?:same_tool_failure_halt|identical_call_streak_halt|identical_cycle_halt)); count=[1-9]\d*; [\s\S]+\]$/
function resultForStatus(value: unknown, allowUnknown = true): unknown {
  let parsed = value
  if (typeof value === 'string') {
    const trailer = nativeToolGuidance.exec(value)
    try {parsed = JSON.parse(trailer ? value.slice(0, trailer.index) : value)} catch {
      return allowUnknown && /^\s*[{[]/.test(value) ? {status: 'completion_unknown'} : value
    }
  }
  const result = asRecord(parsed)
  return allowUnknown && (result.pruned === true || result.body_omitted === true || result.truncated === true) ? {...result, status: result.status ?? 'completion_unknown'} : parsed
}
/** Inspector helpers own native result status; the transcript keeps full expandable payloads. */
export function fullSavedCalls(messages: unknown[]): InspectorToolCall[] {
  const calls = savedToolCalls(messages.map(raw => {
    const message = asRecord(raw)
    return message.role === 'tool' ? {...message, content: resultForStatus(message.content)} : raw
  }))
  for (const raw of messages) {
    const message = asRecord(raw)
    if (message.display_kind === 'hidden') continue
    for (const rawCall of Array.isArray(message.tool_calls) ? message.tool_calls : []) {
      const call = asRecord(rawCall), fn = asRecord(call.function), match = calls.find(item => item.id === call.id)
      if (match) {match.arguments = string(fn.arguments); match.argumentsTruncated = false}
    }
    const match = calls.find(item => item.id === message.tool_call_id)
    if (message.role === 'tool' && match) {match.result = string(message.content); match.resultTruncated = false}
  }
  return calls
}
export function savedConversation(messages: unknown[]): ConversationRow[] {
  const calls = fullSavedCalls(messages), shown = new Set<string>(), rows: ConversationRow[] = []
  for (const [index, raw] of messages.entries()) {
    const message = asRecord(raw)
    if (['hidden', 'auto_continue'].includes(String(message.display_kind)) || message.role === 'system' || message.role === 'user' && message._compressed_summary === true) continue
    const rowId = message.row_id ?? message.id ?? message._row_id
    const id = typeof rowId === 'number' ? `saved:${rowId}` : `history:${index}`
    // The server owns commentary policy, redaction and think-block stripping.
    // Raw provider sidecars and reasoning are never public transcript sources.
    if (message.role === 'assistant' && Array.isArray(message.display_commentary)) {
      message.display_commentary.forEach((part, index) => {
        if (typeof part === 'string' && part.trim()) rows.push({id: `${id}:commentary:${index}`, role: 'assistant', text: part, kind: 'briefing'})
      })
    }
    const text = publicText(message.display_content ?? message.content)
    if ((message.role === 'assistant' || message.role === 'user') && text.trim()) rows.push({id, role: message.role, text,
      ...(message.role === 'assistant' ? {kind: message.display_kind === 'failed_turn' ? 'notice' as const : Array.isArray(message.tool_calls) && message.tool_calls.length ? 'briefing' as const : 'answer' as const} : {}),
      ...(typeof message.timestamp === 'number' ? {timestamp: message.timestamp} : {})})
    const ids = Array.isArray(message.tool_calls) ? message.tool_calls.map(asRecord).map(call => call.id) : message.role === 'tool' ? [message.tool_call_id] : []
    const group = calls.filter(call => ids.includes(call.id) && !shown.has(call.id))
    if (group.length) {group.forEach(call => shown.add(call.id)); rows.push({id: `tools:${group[0].id}`, role: 'tools', calls: group, ...(message.role === 'assistant' && typeof message.timestamp === 'number' ? {timestamp: message.timestamp} : {})})}
  }
  return rows
}
function append(state: PublicConversation, row: Omit<ConversationRow, 'id'>, id?: string): void {
  state.rows.push({...row, id: id ?? `live:${++state.nextId}`})
}
export function applyConversationEvent(previous: PublicConversation, event: InspectorEvent & {seq?: number; replayEpoch?: string}): PublicConversation {
  const epochChanged = Boolean(event.replayEpoch && previous.epoch && event.replayEpoch !== previous.epoch)
  if (!epochChanged && typeof event.seq === 'number' && event.seq <= previous.lastSeq) return previous
  const state: PublicConversation = {...previous, rows: previous.rows.map(row => ({...row})), epoch: event.replayEpoch ?? previous.epoch, lastSeq: typeof event.seq === 'number' ? event.seq : epochChanged ? 0 : previous.lastSeq}
  const payload = asRecord(event.payload)
  if (event.type === 'message.start') {state.turn++; return state}
  if (event.type === 'message.delta') {
    if (typeof payload.text !== 'string' || !payload.text) return state
    const row = state.rows.findLast(row => row.streaming)
    if (row) row.text = (row.text ?? '') + payload.text
    else append(state, {role: 'assistant', text: payload.text, streaming: true})
  } else if (event.type === 'message.interim') {
    const text = publicText(payload.text)
    if (!text.trim()) return state
    const row = state.rows.findLast(row => row.streaming)
    if (row) {row.text = text; row.streaming = false; row.kind = 'briefing'}
    else if (!(payload.already_streamed === true && state.rows.at(-1)?.text === text)) append(state, {role: 'assistant', text, kind: 'briefing'})
  } else if (event.type === 'message.complete') {
    const text = publicText(payload.text)
    const kind = payload.status === 'error' ? 'notice' : 'answer'
    const persisted = asRecord(payload.persisted_turn), finalId = typeof persisted.final_assistant_row_id === 'number' ? `saved:${persisted.final_assistant_row_id}` : undefined
    const replyTo = payload.status !== 'error' && persisted.complete === true && Number.isSafeInteger(persisted.user_row_id) && Number(persisted.user_row_id) > 0 && finalId ? `saved:${persisted.user_row_id}` : undefined
    const streaming = state.rows.findLast(row => row.streaming)
    const precedingUser = streaming ? state.rows.slice(0, state.rows.indexOf(streaming)).findLast(row => row.role === 'user') : undefined
    const row = (finalId && state.rows.find(row => row.id === finalId)) || (replyTo && (streaming?.turnUserId ?? precedingUser?.id) && (streaming?.turnUserId ?? precedingUser?.id) !== replyTo ? undefined : streaming)
    // Complete's public text is authoritative only for its own structurally identified reply.
    if (row) {row.text = text || row.text; row.streaming = false; row.kind = kind; if (finalId) row.id = finalId; if (replyTo) row.replyTo = replyTo}
    else if (text.trim() && !state.rows.some(item => (finalId && item.id === finalId) || item === state.rows.at(-1) && item.role === 'assistant' && item.text === text)) append(state, {role: 'assistant', text, kind, ...(replyTo ? {replyTo} : {})}, finalId)
  } else if (event.type === 'tool.start' || event.type === 'tool.complete') {
    const toolId = payload.tool_id
    if (typeof toolId !== 'string') return state
    const row = state.rows.find(row => row.calls?.some(call => call.id === toolId))
    const statusEvent = event.type === 'tool.complete' ? {...event, payload: {...payload,
      result: resultForStatus(payload.result), result_text: resultForStatus(payload.result_text, payload.result == null),
      ...(payload.result == null && payload.result_text == null ? {status: payload.status ?? 'completion_unknown', result: resultForStatus(payload.summary)} : {}),
    }} : event
    const calls = applyInspectorEvent(row?.calls ?? [], statusEvent).map(call => call.id !== toolId ? call : {...call,
      arguments: payload.args_text !== undefined || payload.args !== undefined ? string(payload.args_text ?? payload.args) : call.arguments,
      argumentsTruncated: false,
      ...(event.type === 'tool.complete' ? {result: string(payload.result_text ?? payload.result ?? payload.summary), resultTruncated: false} : {}),
    })
    if (row) row.calls = calls
    else {const group = state.rows.at(-1); if (group?.role === 'tools') group.calls = [...(group.calls ?? []), ...calls]; else append(state, {role: 'tools', calls})}
  }
  return state
}
/** A REST snapshot has no event cursor. Match committed identity first, then ordered public content. */
export function reconcileConversation(saved: ConversationRow[], current: PublicConversation): PublicConversation {
  const matched = new Set<string>(), savedIds = new Set(saved.map(row => row.id))
  let cursor = 0
  for (const row of current.rows) {
    const at = saved.findIndex((item, index) => index >= cursor && item.role === row.role && (item.id === row.id || (row.role === 'tools' ? Boolean(row.calls?.every(call => item.calls?.some(savedCall => savedCall.id === call.id))) : item.text === row.text)))
    if (at >= 0) {matched.add(row.id); cursor = at + 1; continue}
    // Native interim delivery may join several public commentary items, while
    // history retains each server-projected item with its own stable identity.
    if (row.kind === 'briefing') {
      for (let start = cursor; start < saved.length; start++) {
        let text = ''
        for (let end = start; end < saved.length && saved[end].kind === 'briefing'; end++) {
          text += (end === start ? '' : '\n\n') + (saved[end].text ?? '')
          if (text === row.text) {matched.add(row.id); cursor = end + 1; break}
          if (!row.text?.startsWith(text)) break
        }
        if (matched.has(row.id)) break
      }
    }
  }
  const tail = current.rows.filter(row => !savedIds.has(row.id) && !matched.has(row.id))
  return {...current, rows: [...saved.map(row => {const prior = current.rows.find(item => item.id === row.id); return prior?.replyTo ? {...row, replyTo: prior.replyTo} : row}), ...tail]}
}

/** Hydrate the public in-flight suffix, never the private reasoning snapshot. */
export function hydrateConversation(current: PublicConversation, inflightValue: unknown): PublicConversation {
  const inflight = asRecord(inflightValue), rows = current.rows.map(row => ({...row}))
  const publicTurn = !['hidden', 'auto_continue'].includes(String(inflight.display_kind)) && asRecord(inflight.display_metadata)._compressed_summary !== true
  const user = typeof inflight.user === 'string' && publicTurn ? inflight.user : ''
  const userId = typeof inflight.user_row_id === 'number' ? `saved:${inflight.user_row_id}` : undefined
  const state = {...current, rows}
  if (user && !rows.some(row => userId ? row.id === userId : row.role === 'user' && row.text === user)) append(state, {role: 'user', text: user}, userId)
  const assistant = typeof inflight.assistant === 'string' ? inflight.assistant : ''
  const assistantCharacters = [...assistant] // Native Python offsets count Unicode code points.
  const rawCorrections = Array.isArray(inflight.corrections) ? inflight.corrections : []
  const rawOffsets = Array.isArray(inflight.correction_offsets) ? inflight.correction_offsets : []
  const visibility = Array.isArray(inflight.correction_display_kinds) ? inflight.correction_display_kinds : []
  const included = rawCorrections.map((_, index) => index).filter(index => visibility[index] === 'visible' || publicTurn && visibility[index] == null)
  const corrections = included.filter(index => typeof rawCorrections[index] === 'string' && rawCorrections[index].trim()).map(index => ({index, text: rawCorrections[index] as string, offset: rawOffsets[index]}))
  if (corrections.length) {
    // These ids belong only to the replayed native snapshot, never to persisted history.
    const prefix = `inflight:${userId ?? user}:`
    const generated = rows.filter(row => row.id.startsWith(prefix))
    const streaming = rows.findLast(row => row.streaming)
    const insertion = generated.length ? rows.indexOf(generated[0]) : streaming ? rows.indexOf(streaming) : rows.length
    state.rows = rows.filter(row => !row.id.startsWith(prefix) && row !== streaming)
    const suffix: ConversationRow[] = []
    let start = 0
    const unknown: typeof corrections = []
    let segment = 0
    for (const correction of corrections) {
      const end = correction.offset
      if (!Number.isSafeInteger(end) || end < start || end > assistantCharacters.length) {unknown.push(correction); continue}
      if (end > start) suffix.push({id: `${prefix}assistant:${segment++}`, role: 'assistant', text: assistantCharacters.slice(start, end).join(''), turnUserId: userId, kind: 'briefing'})
      suffix.push({id: `${prefix}correction:${correction.index}`, role: 'user', text: correction.text})
      start = end
    }
    if (assistantCharacters.length > start) suffix.push({id: `${prefix}assistant:${segment}`, role: 'assistant', text: assistantCharacters.slice(start).join(''), turnUserId: userId, streaming: inflight.streaming === true})
    // Acceptance is known even when native replay cannot prove the position within the reply.
    for (const correction of unknown) suffix.push({id: `${prefix}correction:${correction.index}`, role: 'user', text: correction.text, positionUnknown: true})
    const anchor = userId ? state.rows.findIndex(row => row.id === userId) : -1
    let cursor = anchor >= 0 ? anchor + 1 : Math.min(insertion, state.rows.length)
    for (const row of suffix) {
      const at = state.rows.findIndex((candidate, index) => index >= cursor && candidate.role === row.role && candidate.text === row.text)
      if (at >= 0) cursor = at + 1
      else {state.rows.splice(cursor, 0, row); cursor++}
    }
  } else if (assistant) {
    const row = rows.findLast(row => row.streaming)
    if (row) {row.text = assistant; if (userId) row.turnUserId = userId}
    else if (rows.at(-1)?.role !== 'assistant' || rows.at(-1)?.text !== assistant) append(state, {role: 'assistant', text: assistant, turnUserId: userId, streaming: inflight.streaming === true})
  }
  return state
}
/** Snapshot/event boundaries are not atomic. Drop only proven text overlap in buffered public deltas. */
export function reconcileBufferedEvents(current: PublicConversation, events: (InspectorEvent & {seq?: number; replayEpoch?: string})[]): PublicConversation {
  let state = current
  let offset = 0
  while (offset < events.length) {
    const event = events[offset]
    if (event.type !== 'message.delta') {state = applyConversationEvent(state, event); offset++; continue}
    let end = offset, text = ''
    while (end < events.length && events[end].type === 'message.delta') {text += publicText(asRecord(events[end].payload).text); end++}
    const tail = state.rows.at(-1)
    const known = tail?.role === 'assistant' ? tail.text ?? '' : ''
    let overlap = Math.min(known.length, text.length)
    while (overlap > 0 && !known.endsWith(text.slice(0, overlap))) overlap--
    for (; offset < end; offset++) {
      const candidate = events[offset], payload = asRecord(candidate.payload), delta = publicText(payload.text)
      const skip = Math.min(overlap, delta.length)
      overlap -= skip
      state = applyConversationEvent(state, {...candidate, payload: {...payload, text: delta.slice(skip)}})
    }
  }
  return state
}

/** Native turn time is authoritative; observed time is labelled separately. */
export function timedConversationStatus(text: string, startedAt: number | undefined, observed: boolean, now: number): string {
  const kind = /압축|compacting/i.test(text) ? '🗜️' : /생각|thinking/i.test(text) ? '💭' : null
  if (!kind) return text
  const label = `${kind} ${text.replace(/^(?:💭|🤔|🧠|🗜️?)\s*/u, '')}`
  if (startedAt === undefined || !Number.isFinite(startedAt) || startedAt <= 0) return label
  const elapsed = Math.max(0, Math.floor(now / 1000 - startedAt))
  return `${label} · ${observed ? '관측' : '이번 응답'} 경과 ${Math.floor(elapsed / 60)}m ${elapsed % 60}s`
}

export interface ReviewChatItem {
  id: string; case_id: string; conversation_id: string; goal_id: string; review_id: string; finding_id: string | null
  speaker: 'ren' | 'system'; kind: 'review_question' | 'executive_question' | 'review_status'
  stage: string; round: number | null; public_text: string; public_reason: string | null; next_check: string | null
  observed_at: string | null; time_basis: 'response_observed' | 'request_recorded' | 'unknown'
  receipt_state: string; freshness: 'current' | 'stale' | 'unknown'; historical: boolean; semantic_approval: false
  response_ref: {question_id: string; revision: number; finding_id: string} | null
  disposition: {question_id: string; revision: number; disposition: string; reason: string; limitation: string | null; citation_refs: string[]} | null
}
export interface ReviewActivity {reviewId: string; startedAt?: number}
/** Only authenticated native in-flight receipts indicate an active independent review. */
export function currentReviewActivity(items: ReviewChatItem[]): ReviewActivity | null {
  const item = items.find(item => item.kind === 'review_status' && item.receipt_state === 'in_progress' && !item.historical)
  if (!item) return null
  const startedAt = item.time_basis === 'request_recorded' && item.observed_at ? Date.parse(item.observed_at) / 1000 : NaN
  return {reviewId: item.review_id, ...(Number.isFinite(startedAt) && startedAt > 0 ? {startedAt} : {})}
}
export interface ReviewChatPage {
  status: string; profile: string; requested_session_id: string; case_id: string; conversation_id: string; goal_id: string
  items: ReviewChatItem[]; snapshot_id: string; next_cursor: string | null
  coverage: {scanned_receipts: number; omitted_receipts: number; unscanned_receipts?: number; unreadable_receipts?: number}
}
/** Review cards are derived display rows; native history and event cursors remain untouched. */
export function withReviewChat(native: ConversationRow[], items: ReviewChatItem[]): ConversationRow[] {
  const rows = [...native], seen = new Set<string>()
  for (const item of items) {
    if (seen.has(item.id)) continue
    seen.add(item.id)
    const time = item.observed_at ? Date.parse(item.observed_at) / 1000 : NaN
    const row: ConversationRow = {id: item.id, role: 'assistant', text: item.public_text, speaker: item.speaker, review: item}
    const at = Number.isFinite(time) ? rows.findIndex(candidate => !candidate.review && candidate.timestamp !== undefined && candidate.timestamp > time) : -1
    if (at < 0) rows.push(row)
    else rows.splice(at, 0, row)
  }
  return rows
}
/** All pages must belong to one selected authenticated conversation and one retained snapshot. */
export async function readReviewChat(
  fetcher: (url: string, init?: RequestInit) => Promise<ReviewChatPage>,
  profile: string, sessionId: string, signal: AbortSignal,
): Promise<{items: ReviewChatItem[]; omitted: number; caseId: string}> {
  let cursor = '', first: ReviewChatPage | undefined
  const items: ReviewChatItem[] = [], cursors = new Set<string>()
  for (let page = 0; page < 8; page++) {
    const query = new URLSearchParams({profile, session_id: sessionId, ...(cursor ? {cursor} : {})})
    const data = await fetcher(`/api/plugins/forsic/review-chat?${query}`, {signal, cache: 'no-store'})
    if (signal.aborted) throw new Error('Review chat request cancelled')
    if (data.profile !== profile || data.requested_session_id !== sessionId || !data.case_id || !data.conversation_id || !Array.isArray(data.items) ||
      first && (data.case_id !== first.case_id || data.conversation_id !== first.conversation_id || data.goal_id !== first.goal_id || data.snapshot_id !== first.snapshot_id)) throw new Error('검토 기록의 대화 연결을 확인하지 못했습니다.')
    if (data.status !== 'ready') throw new Error('검토 기록을 확인할 수 없습니다.')
    for (const item of data.items) {
      if (item.case_id !== data.case_id || item.conversation_id !== data.conversation_id || typeof item.id !== 'string' ||
        item.semantic_approval !== false || !['ren', 'system'].includes(item.speaker) ||
        !['review_question', 'executive_question', 'review_status'].includes(item.kind) ||
        typeof item.public_text !== 'string' || item.historical !== (item.goal_id !== data.goal_id)) throw new Error('검토 기록의 소유권을 확인하지 못했습니다.')
      items.push(item)
    }
    first ??= data
    if (!data.next_cursor) return {items, caseId: data.case_id, omitted: (data.coverage?.omitted_receipts ?? 0) + (data.coverage?.unscanned_receipts ?? 0) + (data.coverage?.unreadable_receipts ?? 0)}
    if (cursors.has(data.next_cursor)) throw new Error('검토 이력 페이지를 확인하지 못했습니다.')
    cursor = data.next_cursor; cursors.add(cursor)
  }
  throw new Error('검토 이력이 표시 한도를 초과했습니다.')
}
