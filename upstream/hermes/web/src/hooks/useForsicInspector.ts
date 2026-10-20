import {useCallback, useEffect, useRef, useState} from 'react'
import {fetchJSON} from '@/lib/api'
import {inspectorEventRuntime, applyInspectorEvent, skillCatalogue, toolCatalogue,
  type InspectorCatalogueItem, type InspectorEvent, type InspectorToolCall} from '@/lib/forsic-inspector'

interface Options {sessionId?: string; profile?: string; active: boolean}
interface Snapshot {
  scope: string; currentTools: InspectorToolCall[]; toolItems: InspectorCatalogueItem[]; skillItems: InspectorCatalogueItem[]
  loading: boolean; catalogueError: string | null
}
const empty = (scope: string): Snapshot => ({scope, currentTools: [], toolItems: [], skillItems: [], loading: false, catalogueError: null})
export function useForsicInspector({sessionId, profile, active}: Options) {
  const scope = `${profile ?? ''}\0${sessionId ?? ''}`
  const [snapshot, setSnapshot] = useState<Snapshot>(() => empty(scope))
  const [runtime, setRuntime] = useState<{scope: string; running: boolean | null; tools?: Record<string, string[]>}>({scope, running: null})
  const identity = useRef<{scope: string; resolvedId?: string; runtimeId?: string; pending: InspectorEvent[]}>({scope, pending: []})
  const eventHandler = useRef<(event: InspectorEvent) => void>(() => {})
  const generation = useRef(0)
  const refresh = useCallback(async () => {
    if (!active) return
    const current = ++generation.current
    setSnapshot(previous => ({...(previous.scope === scope ? previous : empty(scope)), loading: true}))
    // Explicit profile prevents management-switcher changes from redirecting an in-flight read.
    const query = new URLSearchParams({profile: profile ?? ''})
    const results = await Promise.allSettled([
      sessionId && !identity.current.resolvedId ? fetchJSON<{requested_session_id: string; stored_session_id: string; profile_name: string}>(`/api/sessions/${encodeURIComponent(sessionId)}/live?${query}`) : Promise.resolve(null),
      fetchJSON<unknown[]>(`/api/tools/toolsets?${query}`),
      fetchJSON<unknown[]>('/dashboard-plugins/forsic/dist/skills.json'),
    ])
    if (current !== generation.current) return
    const [mapping, tools, forsicSkills] = results
    if (mapping.status === 'fulfilled' && mapping.value && identity.current.scope === scope &&
      mapping.value.requested_session_id === sessionId && mapping.value.profile_name === (profile ?? '') && mapping.value.stored_session_id) {
      identity.current.resolvedId = mapping.value.stored_session_id
      const pending = identity.current.pending
      identity.current.pending = []
      for (const candidate of pending) eventHandler.current(candidate)
    }
    setSnapshot(previous => ({...(previous.scope === scope ? previous : empty(scope)), scope, loading: false,
      toolItems: tools.status === 'fulfilled' ? toolCatalogue(tools.value) : [],
      // Generated from the same canonical registrations as forsic_skill. The
      // generic profile library is not reachable through that loader.
      skillItems: forsicSkills.status === 'fulfilled' && Array.isArray(forsicSkills.value)
        ? skillCatalogue(forsicSkills.value).filter((item, index, items) => items.findIndex(candidate => candidate.name === item.name) === index).map(item => ({...item, group: '포식이 조사 스킬'}))
        : [],
      catalogueError: tools.status === 'rejected' || forsicSkills.status === 'rejected' || (forsicSkills.status === 'fulfilled' && !Array.isArray(forsicSkills.value)) ? 'Some profile capabilities are unavailable.' : null,
    }))
  }, [active, profile, scope, sessionId])
  useEffect(() => {
    identity.current = {scope, pending: []}
    let cancelled = false
    void Promise.resolve().then(() => {if (!cancelled) {setRuntime({scope, running: null}); setSnapshot(value => ({...(value.scope === scope ? value : empty(scope)), currentTools: []})); void refresh()}})
    const invalidate = () => {++generation.current}
    return () => {cancelled = true; invalidate()}
  }, [active, refresh, scope])
  const onEvent = useCallback((event: InspectorEvent) => {
    if (!active) return
    if (identity.current.scope !== scope) return
    const owner = identity.current
    const runtimeId = inspectorEventRuntime(event, [sessionId, owner.resolvedId].filter((id): id is string => Boolean(id)), profile, owner.runtimeId)
    if (!runtimeId) {
      if (event.type === 'session.info') owner.pending = [...owner.pending.filter(candidate => candidate.session_id !== event.session_id), event].slice(-8)
      return
    }
    owner.runtimeId = runtimeId
    const payload = event.payload && typeof event.payload === 'object' ? event.payload as Record<string, unknown> : {}
    if (event.type === 'session.info') {
      const names = (value: unknown): Record<string, string[]> | undefined => {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
        const entries = Object.entries(value).filter((entry): entry is [string, string[]] => Array.isArray(entry[1]) && entry[1].every(name => typeof name === 'string'))
        return Object.fromEntries(entries)
      }
      setRuntime({scope, running: typeof payload.running === 'boolean' ? payload.running : null, tools: names(payload.tools)})
    } else if (event.type === 'tool.start') {
      setRuntime(value => ({...(value.scope === scope ? value : {scope}), running: true}))
    } else if (event.type === 'message.complete') {
      setRuntime(value => ({...(value.scope === scope ? value : {scope}), running: false}))
    }
    // Retain only calls currently executing in this verified conversation.
    setSnapshot(value => {
      const previous = value.scope === scope ? value : empty(scope)
      return {...previous, currentTools: event.type === 'message.complete' ? [] : applyInspectorEvent(previous.currentTools, event).filter(call => call.status === 'running' && call.result === undefined)}
    })
  }, [active, profile, scope, sessionId])
  useEffect(() => {eventHandler.current = onEvent}, [onEvent])
  const onReconnect = useCallback(() => {
    // The notification feed has no replay: a missed completion cannot remain 'running'.
    identity.current = {scope, pending: []}
    setRuntime({scope, running: null})
    setSnapshot(value => value.scope === scope ? {...value, currentTools: []} : empty(scope))
    void refresh()
  }, [refresh, scope])
  const visible = snapshot.scope === scope ? snapshot : empty(scope)
  const metadata = runtime.scope === scope ? runtime : {scope, running: null}
  const scopedItems = (groups: Record<string, string[]> | undefined, catalogue: InspectorCatalogueItem[]) => groups === undefined ? catalogue : Object.entries(groups).flatMap(([group, names]) => names.map(name => ({...(catalogue.find(item => item.name === name) ?? {name, description: ''}), group})))
  const effectiveTools = scopedItems(metadata.tools, visible.toolItems)
  const investigationSkills = effectiveTools.some(item => item.name === 'forsic_skill') ? visible.skillItems : []
  return {...visible, sessionId, profile, active, running: metadata.running, toolItems: effectiveTools, skillItems: investigationSkills, refresh, onEvent, onReconnect,
    catalogueSource: 'profile configuration' as const}
}
