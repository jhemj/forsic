import {useTenant} from '@/contexts/tenant-context'
import {useCallback, useEffect, useLayoutEffect, useRef, useState} from 'react'
import {fetchJSON} from '@/lib/api'
import type {InvestigationSourcePage, InvestigationSourceRequest, InvestigationView} from '@/lib/forsic-investigation'

interface Options {sessionId?: string; profile?: string; caseId?: string; active: boolean}
interface Snapshot {scope: string; data: InvestigationView | null; loading: boolean; error: string | null}
const empty = (scope: string): Snapshot => ({scope, data: null, loading: false, error: null})

/** A real conversation authorizes a read-only, case-wide snapshot. */
export function useForsicInvestigation({sessionId, profile, caseId: expectedCaseId, active}: Options) {
  const tenant=useTenant()
  const scope = `${profile ?? ''}\0${tenant?.tenant_id??''}\0${sessionId ?? ''}`
  const [snapshot, setSnapshot] = useState<Snapshot>(() => empty(scope))
  const generation = useRef(0)
  const sourceEpoch = useRef(0)
  const selected = useRef({scope, active})
  const sameCase = useCallback((previous: Snapshot) => {
    if (expectedCaseId && previous.data && previous.data.case_id !== expectedCaseId) return false
    return previous.scope === scope || Boolean(expectedCaseId && previous.scope.split('\0').slice(0,2).join('\0') === `${profile??''}\0${tenant?.tenant_id??''}` &&
      previous.data?.case_id === expectedCaseId && previous.data.scope === 'case')
  }, [expectedCaseId, profile, scope, tenant?.tenant_id])
  useLayoutEffect(() => {
    selected.current = {scope, active}
    ++generation.current
    const epoch = ++sourceEpoch.current
    return () => {selected.current = {scope, active: false}; sourceEpoch.current = epoch + 1}
  }, [scope, active, expectedCaseId])
  const refresh = useCallback(async () => {
    if (!active || !sessionId || profile === undefined) return
    const current = ++generation.current
    setSnapshot(previous => ({...(sameCase(previous) ? previous : empty(scope)), loading: true, error: null}))
    const query = new URLSearchParams({session_id: sessionId, profile})
    try {
      const data = await fetchJSON<InvestigationView>(`/api/plugins/forsic/investigation-view?${query}`)
      if (current !== generation.current || selected.current.scope !== scope || !selected.current.active) return
      if (!data.case_id || !Array.isArray(data.questions) || !Array.isArray(data.observations) || !Array.isArray(data.sources) || !Array.isArray(data.timeline) ||
        (data.session_id !== undefined && data.session_id !== sessionId) || (data.profile !== undefined && data.profile !== profile) ||
        (expectedCaseId !== undefined && data.case_id !== expectedCaseId)) {
        setSnapshot({...empty(scope), error: 'Investigation identity mismatch'})
        return
      }
      setSnapshot({scope, data, loading: false, error: null})
    } catch {
      if (current !== generation.current || selected.current.scope !== scope || !selected.current.active) return
      setSnapshot(previous => ({...(sameCase(previous) ? previous : empty(scope)), loading: false, error: 'Investigation records unavailable'}))
    }
  }, [active, profile, scope, sessionId, expectedCaseId, sameCase])
  useEffect(() => {
    if (!active) return
    let cancelled = false
    void Promise.resolve().then(() => {if (!cancelled) void refresh()})
    const timer = setInterval(() => {void refresh()}, 10000)
    return () => {cancelled = true; clearInterval(timer)}
  }, [active, refresh])
  const visible = sameCase(snapshot) && active ? snapshot : empty(scope)
  const caseId = visible.data?.case_id
  const readSource = useCallback(async (request: InvestigationSourceRequest) => {
    if (!active || !sessionId || profile === undefined || !caseId || selected.current.scope !== scope || !selected.current.active) throw new Error('No selected investigation')
    const query = new URLSearchParams({session_id: sessionId, profile, case_id: caseId, source_id: request.source.id,
      source_version: request.source.version, pointer: request.pointer ?? '', offset: String(request.offset ?? 0), limit: '2048'})
    const epoch = sourceEpoch.current
    const page = await fetchJSON<InvestigationSourcePage>(`/api/plugins/forsic/source-page?${query}`)
    if (epoch !== sourceEpoch.current || selected.current.scope !== scope || !selected.current.active || page.source_ref?.id !== request.source.id || page.source_ref?.version !== request.source.version) throw new Error('Source identity mismatch')
    return page
  }, [active, caseId, profile, scope, sessionId])
  return {...visible, refresh, readSource}
}
