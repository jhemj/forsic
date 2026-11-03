import {useCallback, useEffect, useLayoutEffect, useRef, useState} from 'react'
import {useTenant} from '@/contexts/tenant-context'
import {fetchJSON} from '@/lib/api'
import type {ReviewHistory} from '@/lib/forsic-review-history'

interface Options {sessionId?: string; profile?: string; caseId?: string | null; active: boolean}
interface State {scope: string; data: ReviewHistory | null; loading: boolean; error: string | null}

/** Read every retained page, with identity and snapshot checks across native handoffs. */
export function useForsicReviewHistory({sessionId, profile, caseId, active}: Options) {
  const tenant = useTenant()
  const scope = `${tenant?.tenant_id ?? ''}\0${profile ?? ''}\0${sessionId ?? ''}\0${caseId ?? ''}`
  const empty = useCallback((): State => ({scope, data: null, loading: false, error: null}), [scope])
  const [state, setState] = useState<State>(empty)
  const epoch = useRef(0)
  const flight = useRef<{epoch: number; controller: AbortController; promise: Promise<void>} | null>(null)
  useLayoutEffect(() => {
    const current = ++epoch.current
    return () => {epoch.current = current + 1; flight.current?.controller.abort()}
  }, [scope, active])
  const refresh = useCallback((): Promise<void> => {
    if (!active || !sessionId || profile === undefined || !caseId) return Promise.resolve()
    if (flight.current?.epoch === epoch.current) return flight.current.promise
    const current = epoch.current
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 45000)
    const promise = (async () => {
      setState(previous => ({...(previous.scope === scope ? previous : empty()), loading: true, error: null}))
      try {
        let first: ReviewHistory | null = null
        let offset = 0
        const items: ReviewHistory['items'] = []
        do {
          const query: URLSearchParams = new URLSearchParams({session_id: sessionId, profile, case_id: caseId, offset: String(offset), limit: '30'})
          if (first) query.set('snapshot', first.snapshot)
          const page: ReviewHistory = await Promise.race([
            fetchJSON<ReviewHistory>(`/api/plugins/forsic/review-history?${query}`, {signal: controller.signal}),
            new Promise<never>((_, reject) => controller.signal.addEventListener('abort', () => reject(new Error('History read cancelled')), {once: true})),
          ])
          if (current !== epoch.current) return
          if (page.case_id !== caseId || page.session_id !== sessionId || page.profile !== profile || page.scope !== 'case' ||
            !Array.isArray(page.items) || !Array.isArray(page.issues) || !page.counts || page.offset !== offset ||
            typeof page.snapshot !== 'string' || typeof page.total !== 'number' ||
            (first && (page.snapshot !== first.snapshot || page.total !== first.total)) ||
            (page.next_offset !== null && (!Number.isInteger(page.next_offset) || page.next_offset <= offset || page.next_offset > page.total))) {
            throw new Error('Review history identity mismatch')
          }
          first ??= page
          items.push(...page.items)
          if (page.next_offset === null) break
          offset = page.next_offset
        } while (!controller.signal.aborted)
        if (!first || items.length !== first.total || controller.signal.aborted) throw new Error('Incomplete review history')
        if (current === epoch.current) setState({scope, data: {...first, items, next_offset: null}, loading: false, error: null})
      } catch {
        if (current === epoch.current) setState(previous => ({...(previous.scope === scope ? previous : empty()), loading: false, error: '검토 기록을 불러오지 못했어요. 다시 조회해 주세요.'}))
      } finally {
        clearTimeout(timer)
        if (flight.current?.epoch === current) flight.current = null
      }
    })()
    flight.current = {epoch: current, controller, promise}
    return promise
  }, [active, sessionId, profile, caseId, scope, empty])
  useEffect(() => {
    if (!active) return
    let cancelled = false
    void Promise.resolve().then(() => {if (!cancelled) void refresh()})
    const timer = setInterval(() => {void refresh()}, 10000)
    return () => {cancelled = true; clearInterval(timer)}
  }, [active, refresh])
  return {...(state.scope === scope ? state : empty()), loading: active && state.scope === scope && state.loading, refresh}
}
