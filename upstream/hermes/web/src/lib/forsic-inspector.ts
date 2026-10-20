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
function truncated(value: unknown): boolean {
  return (typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 2)).length > TEXT_LIMIT
}
function finished(call: InspectorToolCall | undefined): boolean { return call?.result !== undefined || call?.status === 'completed' || call?.status === 'error' }
function text(value: unknown): string {
  const result = typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 2)
  return result.length > TEXT_LIMIT ? result.slice(0, TEXT_LIMIT) + '\n…' : result
}
export function savedToolCalls(messages: unknown[]): InspectorToolCall[] {
  const calls = new Map<string, InspectorToolCall>()
  for (const value of messages) {
    const message = record(value)
    if (message.display_kind === 'hidden') continue
    if (Array.isArray(message.tool_calls)) {
      for (const value of message.tool_calls) {
        const call = record(value), fn = record(call.function)
        if (typeof call.id !== 'string' || typeof fn.name !== 'string') continue
        calls.set(call.id, {id: call.id, name: fn.name, arguments: text(fn.arguments), argumentsTruncated: truncated(fn.arguments), status: 'unknown', source: 'saved'})
      }
    }
    if (message.role === 'tool' && typeof message.tool_call_id === 'string') {
      const id = message.tool_call_id
      const call = calls.get(id)
      calls.set(id, {...(call ?? {id, name: String(message.tool_name || 'tool'), arguments: '', source: 'saved' as const}), result: text(message.content), resultTruncated: truncated(message.content), status: resultStatus(message, message.content)})
    }
  }
  return [...calls.values()].slice(-INSPECTOR_HISTORY_LIMIT)
}
export function applyInspectorEvent(calls: InspectorToolCall[], event: InspectorEvent): InspectorToolCall[] {
  if (event.type !== 'tool.start' && event.type !== 'tool.complete') return calls
  const payload = record(event.payload)
  if (typeof payload.tool_id !== 'string' || typeof payload.name !== 'string') return calls
  const prior = calls.find(call => call.id === payload.tool_id)
  // Late hydration/start delivery must not downgrade an already returned call.
  if (event.type === 'tool.start' && finished(prior)) return calls
  const call: InspectorToolCall = {
    ...prior, id: payload.tool_id, name: payload.name,
    arguments: payload.args_text !== undefined ? text(payload.args_text) : payload.args !== undefined ? text(payload.args) : prior?.arguments ?? '',
    argumentsTruncated: truncated(payload.args_text ?? payload.args ?? prior?.arguments),
    source: 'live', status: event.type === 'tool.start' ? 'running' : resultStatus(payload, payload.result, payload.result_text),
    ...(event.type === 'tool.complete' ? {result: text(payload.result_text ?? payload.result ?? payload.summary), resultTruncated: truncated(payload.result_text ?? payload.result ?? payload.summary)} : {}),
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
    calls.set(call.id, finished(persisted) && !finished(call) ? {...persisted!, arguments: persisted!.arguments || call.arguments} : call)
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
