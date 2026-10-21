/** Read-only projections of native history and profile capability catalogues. */
export interface InspectorToolCall {
  id: string
  name: string
  arguments: string
  result?: string
  status: 'completed' | 'error' | 'running' | 'unknown'
  source: 'saved' | 'live'
  argumentsTruncated?: boolean
  resultTruncated?: boolean
  durationSeconds?: number
  submittedRequest?: {name: string; arguments: string; argumentsTruncated?: boolean}
  requestedTools?: {name: string; arguments: string}[]
}
export interface InspectorCatalogueItem {
  name: string
  description: string
  group?: string
  groupDescription?: string
}
export interface InspectorEvent {
  type: string
  session_id?: string
  payload?: unknown
}
/** Resolve only the parent conversation runtime; channel delivery alone is insufficient. */
export function inspectorEventRuntime(event: InspectorEvent, storedIds: string[], profile: string | undefined, runtimeId?: string): string | null {
  if (!event.session_id) return null
  if (event.type !== 'session.info') return event.session_id === runtimeId ? runtimeId : null
  const info = record(event.payload)
  if (profile && info.profile_name !== profile) return null
  if (event.session_id === runtimeId) return runtimeId
  return typeof info.stored_session_id === 'string' && storedIds.includes(info.stored_session_id) ? event.session_id : null
}
export const INSPECTOR_HISTORY_LIMIT = 100
const TEXT_LIMIT = 12000
function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
function resultStatus(...values: unknown[]): InspectorToolCall['status'] {
  const data = values.map(value => {
    if (typeof value === 'string') { try { return record(JSON.parse(value)) } catch { /* Output text is not execution metadata. */ } }
    return record(value)
  })
  // Inspect only native result envelopes and the explicit execution contract, never
  // arbitrary evidence, stdout, or nested records that may describe other failures.
  const envelopes = data.flatMap(value => [value, record(value.execution)])
  const states = envelopes.flatMap(value => [value.status, value.state])
  if (envelopes.some(value => value.is_error === true || value.success === false || Boolean(value.error)
    || (typeof value.exit_code === 'number' && value.exit_code !== 0))
    || states.some(state => ['error', 'failed', 'failure', 'cancelled', 'timed_out'].includes(String(state)))) return 'error'
  if (states.some(state => ['completion_unknown', 'not_started', 'worker_unavailable'].includes(String(state)))) return 'unknown'
  if (states.some(state => ['queued', 'pending', 'accepted', 'running', 'in_progress'].includes(String(state)))) return 'running'
  return 'completed'
}
function truncated(value: unknown, limit = TEXT_LIMIT): boolean {
  return (typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 2)).length > limit
}
function finished(call: InspectorToolCall | undefined): boolean { return call?.result !== undefined || call?.status === 'completed' || call?.status === 'error' }
function text(value: unknown, limit = TEXT_LIMIT): string {
  const result = typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 2)
  return result.length > limit ? result.slice(0, limit) + '\n…' : result
}
/** Mirror native bridge shapes without inventing child execution IDs. */
function toolInput(name: string, input: unknown, limit: number) {
  let argumentsValue = input
  let resolvedName = name
  let requestedTools: InspectorToolCall['requestedTools']
  if (name === 'tool_call') {
    try {
      const args = record(typeof input === 'string' ? JSON.parse(input) : input)
      let rawEntries: unknown = args.calls ?? [args]
      if (typeof rawEntries === 'string') rawEntries = JSON.parse(rawEntries)
      const entries: unknown[] = Array.isArray(rawEntries) ? rawEntries : [rawEntries]
      const normalized = entries.map(raw => {
        const entry = record(raw), underlying = typeof entry.name === 'string' ? entry.name.trim() : ''
        let inner = entry.arguments ?? {}
        if (typeof inner === 'string') inner = inner.trim() ? JSON.parse(inner) : {}
        if (underlying && !['tool_call', 'tool_search', 'tool_describe'].includes(underlying) && inner && typeof inner === 'object' && !Array.isArray(inner)) {
          return {name: underlying, arguments: inner}
        }
        return null
      })
      if (normalized.length && normalized.every(entry => entry !== null)) {
        if (normalized.length === 1) {resolvedName = normalized[0].name; argumentsValue = normalized[0].arguments}
        else requestedTools = normalized.map(entry => ({name: entry.name, arguments: text(entry.arguments, limit)}))
      }
    } catch { /* A malformed request remains the original bridge request. */ }
  }
  return {name: resolvedName, arguments: text(argumentsValue, limit), argumentsTruncated: truncated(argumentsValue, limit),
    ...(requestedTools ? {requestedTools} : {}),
    ...(resolvedName !== name ? {submittedRequest: {name, arguments: text(input, limit), argumentsTruncated: truncated(input, limit)}} : {})}
}
export function savedToolCalls(messages: unknown[], textLimit = TEXT_LIMIT): InspectorToolCall[] {
  const calls = new Map<string, InspectorToolCall>()
  for (const value of messages) {
    const message = record(value)
    if (message.display_kind === 'hidden') continue
    if (Array.isArray(message.tool_calls)) {
      for (const value of message.tool_calls) {
        const call = record(value), fn = record(call.function)
        if (typeof call.id !== 'string' || typeof fn.name !== 'string') continue
        calls.set(call.id, {id: call.id, ...toolInput(fn.name, fn.arguments, textLimit), status: 'unknown', source: 'saved'})
      }
    }
    if (message.role === 'tool' && typeof message.tool_call_id === 'string') {
      const id = message.tool_call_id
      const call = calls.get(id)
      calls.set(id, {...(call ?? {id, name: String(message.tool_name || 'tool'), arguments: '', source: 'saved' as const}), result: text(message.content, textLimit), resultTruncated: truncated(message.content, textLimit), status: resultStatus(message, message.content)})
    }
  }
  return [...calls.values()].slice(-INSPECTOR_HISTORY_LIMIT)
}
export function applyInspectorEvent(calls: InspectorToolCall[], event: InspectorEvent, textLimit = TEXT_LIMIT): InspectorToolCall[] {
  if (event.type !== 'tool.start' && event.type !== 'tool.complete') return calls
  const payload = record(event.payload)
  if (typeof payload.tool_id !== 'string' || typeof payload.name !== 'string') return calls
  const prior = calls.find(call => call.id === payload.tool_id)
  // Late hydration/start delivery must not downgrade an already returned call.
  if (event.type === 'tool.start' && finished(prior)) return calls
  const call: InspectorToolCall = {
    ...prior, id: payload.tool_id,
    ...toolInput(payload.name === 'tool_call' && prior && payload.args_text === undefined && payload.args === undefined ? prior.name : payload.name,
      payload.args_text ?? payload.args ?? prior?.arguments ?? '', textLimit),
    source: 'live', status: event.type === 'tool.start' ? 'running' : resultStatus(payload, payload.result, payload.result_text),
    ...(event.type === 'tool.complete' ? {result: text(payload.result_text ?? payload.result ?? payload.summary, textLimit), resultTruncated: truncated(payload.result_text ?? payload.result ?? payload.summary, textLimit)} : {}),
    ...(typeof payload.duration_s === 'number' ? {durationSeconds: payload.duration_s} : {}),
  }
  return (prior ? calls.map(item => item.id === call.id ? call : item) : [...calls, call]).slice(-INSPECTOR_HISTORY_LIMIT)
}
export function mergeInspectorHistory(saved: InspectorToolCall[], live: InspectorToolCall[]): InspectorToolCall[] {
  const calls = new Map(saved.map(call => [call.id, call]))
  const lastShared = live.findLastIndex(call => calls.has(call.id))
  for (const [index, call] of live.entries()) {
    const persisted = calls.get(call.id)
    // A bounded history page must not reappend older live calls as new activity.
    if (!persisted && index < lastShared) continue
    const merged = {...persisted, ...call, name: call.name === 'tool_call' && persisted ? persisted.name : call.name,
      arguments: call.arguments || persisted?.arguments || '',
      argumentsTruncated: call.arguments ? call.argumentsTruncated : persisted?.argumentsTruncated ?? call.argumentsTruncated,
      ...(persisted?.submittedRequest && !call.submittedRequest ? {submittedRequest: persisted.submittedRequest} : {}),
      ...(persisted?.requestedTools && !call.requestedTools ? {requestedTools: persisted.requestedTools} : {}),
    }
    calls.set(call.id, finished(persisted) && !finished(call) ? {...persisted!, arguments: persisted!.arguments || call.arguments} : merged)
  }
  return [...calls.values()].slice(-INSPECTOR_HISTORY_LIMIT)
}
export function toolCatalogue(rows: unknown[]): InspectorCatalogueItem[] {
  const items = new Map<string, InspectorCatalogueItem>()
  for (const value of rows) {
    const row = record(value)
    if (row.enabled !== true || !Array.isArray(row.tools)) continue
    for (const name of row.tools) {
      if (typeof name === 'string' && !items.has(name)) items.set(name, {
        name, description: '', group: String(row.label || row.name || ''),
        groupDescription: typeof row.description === 'string' ? row.description : '',
      })
    }
  }
  return [...items.values()].sort((a, b) => a.name.localeCompare(b.name))
}
export function skillCatalogue(rows: unknown[]): InspectorCatalogueItem[] {
  return rows.map(record).filter(row => row.enabled !== false && typeof row.name === 'string').map(row => ({
    name: String(row.name), description: typeof row.description === 'string' ? row.description : '',
    group: typeof row.category === 'string' ? row.category : undefined,
  })).sort((a, b) => a.name.localeCompare(b.name))
}
