import {useTenant} from '@/contexts/tenant-context'
import {useCallback, useEffect, useRef, useState} from 'react'
import type {ServerRequest} from '@hermes/shared'
import {readContextUsage, type ContextUsage} from '@/lib/forsic-context'
import {fetchJSON} from '@/lib/api'
import {GatewayClient, type GatewayEvent} from '@/lib/gatewayClient'
import {type InspectorEvent} from '@/lib/forsic-inspector'
import {currentReviewActivity, applyConversationEvent, readReviewChat, withReviewChat, type ReviewChatItem, timedConversationStatus, asRecord, conversationEventStatus, emptyConversation, hydrateConversation, reconcileBufferedEvents, reconcileConversation, savedConversation, type PublicConversation, type ConversationRow} from '@/lib/forsic-conversation'
export type {ConversationRow} from '@/lib/forsic-conversation'
export type {ServerRequest} from '@hermes/shared'

interface Options {sessionId: string; profile: string; active: boolean; onEvent?: (event: InspectorEvent) => void}
interface Control {goal?: {status?: string; paused_reason?: string} | null; revision?: string}
interface Live {available: boolean; requested_session_id: string; stored_session_id: string; runtime_session_id: string | null; profile_name: string; running: boolean | null; control: Control}
interface History {session_id: string; messages: unknown[]}
interface Snapshot {
  scope: string; conversation: PublicConversation; running: boolean; connected: boolean; loading: boolean; reconnecting: boolean
  error: string | null; submissionNotice?: string; statusText: string; turnStartedAt?: number; turnTimeObserved?: boolean; hasMore: boolean; requests: ServerRequest[]; control: Control | null
  reviewItems: ReviewChatItem[]; reviewCaseId?: string; reviewFresh: boolean; reviewError: string | null; reviewOmitted: number
  contextUsage: ContextUsage | null; verified: boolean; pending: boolean; uncertain: boolean; runtimeId?: string; storedId?: string
}
const empty = (scope: string): Snapshot => ({scope, reviewFresh: false, reviewItems: [], reviewError: null, reviewOmitted: 0, conversation: emptyConversation(), running: false, connected: false, loading: false, reconnecting: false, error: null, statusText: '', hasMore: false, requests: [], control: null, contextUsage: null, verified: false, pending: false, uncertain: false})
interface Owner {send: (text: string) => Promise<boolean>; toggle: () => Promise<void>; refresh: () => Promise<void>; earlier: () => Promise<void>; approval: (choice: string) => Promise<void>}
const authFailure = (error: unknown) => /4401|4403|401|403|auth|token|ticket/i.test(error instanceof Error ? error.message : String(error))
const supportedRequests = new Set(['approval', 'clarify', 'sudo', 'secret', 'vault.unlock_prompt'])

/** One native transport, bound to an existing stored conversation. Mount and reconnect only attach. */
export function useForsicConversation({sessionId, profile, active, onEvent}: Options) {
  const tenant=useTenant()
  const scope = `${tenant?.tenant_id??''}\0${profile}\0${sessionId}`
  const [snapshot, setSnapshot] = useState<Snapshot>(() => empty(scope))
  const owner = useRef<Owner | null>(null), callback = useRef(onEvent)
  useEffect(() => {callback.current = onEvent}, [onEvent])
  useEffect(() => {
    if (!active || !sessionId.trim() || !profile.trim()) {owner.current = null; return}
    const gateway = new GatewayClient()
    let disposed = false, state = empty(scope), live: Live | null = null, runtimeId: string | undefined
    let earlierOffset = 100, canonical: ConversationRow[] = [], refreshSerial = 0, buffered: GatewayEvent[] = [], attaching = false, connecting: Promise<void> | null = null
    let retryTimer: ReturnType<typeof setTimeout> | undefined, retries = 0, terminalAuth = false, identityRejected = false, operation = false, pendingText: string | null = null
    let reviewTimer: ReturnType<typeof setTimeout> | undefined, reviewAbort: AbortController | undefined, reviewSerial = 0, reviewGeneration = 0
    const cancelReview = () => {reviewSerial++; clearTimeout(reviewTimer); reviewTimer = undefined; reviewAbort?.abort(); reviewAbort = undefined}
    let pendingSavedIds: Set<string> | null = null, pendingUserRowId: string | null = null
    const publish = (patch: Partial<Snapshot>) => {
      if (disposed) return
      const paused = (patch.control ?? state.control)?.goal?.status === 'paused'
      const pauseChanged = patch.control !== undefined && paused !== (state.control?.goal?.status === 'paused')
      if (pauseChanged || patch.connected === false || patch.verified === false) {
        reviewGeneration++
        patch = {...patch, reviewFresh: false}
      }
      if (patch.reviewFresh && (paused || !(patch.connected ?? state.connected) || !(patch.verified ?? state.verified))) patch = {...patch, reviewFresh: false}
      state = {...state, ...patch}; setSnapshot(state)
    }
    const readReviews = async () => {
      cancelReview()
      if (disposed || terminalAuth || identityRejected || !live || document.hidden) return
      const serial = reviewSerial, generation = reviewGeneration, tip = live.stored_session_id
      const controller = new AbortController()
      reviewAbort = controller
      const timeout = setTimeout(() => controller.abort(), 10000)
      try {
        const result = await readReviewChat(fetchJSON, profile, sessionId, controller.signal)
        if (!disposed && !controller.signal.aborted && serial === reviewSerial && tip === live?.stored_session_id) publish({reviewFresh: generation === reviewGeneration, reviewCaseId: result.caseId, reviewItems: result.items, reviewOmitted: result.omitted, reviewError: null})
      } catch (error) {
        if (!disposed && serial === reviewSerial && !document.hidden) {
          if (authFailure(error)) {terminalAuth = true; cancelReview(); clearTimeout(retryTimer); retryTimer = undefined; publish({reviewItems: [], reviewError: '검토 기록 인증을 다시 확인해 주세요.'})}
          else publish({reviewFresh: false, reviewError: '검토 기록을 확인하지 못했습니다. 이전에 표시한 기록은 최신 상태가 아닐 수 있습니다.'})
        }
      } finally {
        clearTimeout(timeout)
        if (reviewAbort === controller) reviewAbort = undefined
        if (!disposed && serial === reviewSerial && !terminalAuth && !identityRejected && !document.hidden) reviewTimer = setTimeout(() => {void readReviews()}, 8000)
      }
    }
    const reviewVisibility = () => {if (document.hidden) {cancelReview(); publish({reviewFresh: false})} else void readReviews()}
    document.addEventListener('visibilitychange', reviewVisibility)
    const nativeParams = () => ({session_id: runtimeId!, profile})
    const acceptIdentity = (result: unknown, expectedRuntime?: string): string => {
      const response = asRecord(result), info = asRecord(response.info)
      const stored = info.session_key ?? response.session_key ?? info.stored_session_id ?? response.stored_session_id ?? response.resumed
      if (!live || response.session_id === undefined || typeof response.session_id !== 'string' || expectedRuntime && response.session_id !== expectedRuntime || info.profile_name !== profile || stored !== live.stored_session_id) {identityRejected = true; cancelReview(); publish({reviewItems: []}); publish({verified: false, control: null, requests: []}); throw new Error('대화 연결 정보가 선택한 사건과 일치하지 않습니다.')}
      runtimeId = response.session_id
      retries = 0; clearTimeout(retryTimer); retryTimer = undefined
      const startedAt = response.turn_started_at ?? info.turn_started_at ?? asRecord(response.inflight).started_at
      publish({...(typeof startedAt === 'number' && Number.isFinite(startedAt) && startedAt > 0 ? {turnStartedAt: startedAt, turnTimeObserved: false} : {}), runtimeId, storedId: live.stored_session_id, contextUsage: readContextUsage(info.usage), verified: true, reconnecting: false, running: response.running === true || info.running === true, statusText: '', conversation: hydrateConversation(state.conversation, response.inflight)})
      // Cold resume's lightweight info omits stored identity; the verified response envelope owns it.
      callback.current?.({type: 'session.info', session_id: runtimeId, payload: {...info, stored_session_id: stored, profile_name: profile, running: state.running}})
      return runtimeId
    }
    const readControl = async () => {
      const result = await gateway.request<{control: Control}>('session.control.read', nativeParams())
      if (!disposed) publish({control: result.control})
      return result.control
    }
    const applyEvent = (event: GatewayEvent, project = true) => {
      if (disposed) return
      // Reclamation is broadcast globally; its payload owns the runtime identity.
      if (event.type === 'session.reclaimed') {
        const payload = asRecord(event.payload)
        if (!runtimeId || payload.session_id !== runtimeId || payload.stored_session_id !== live?.stored_session_id) return
        runtimeId = undefined
        publish({runtimeId: undefined, verified: false, running: false, requests: []})
        void refresh(false, 0, false).catch(() => {})
        return
      }
      if (event.session_id !== runtimeId || !state.verified) return
      if (event.type === 'session.info') {
        const info = asRecord(event.payload)
        if (info.profile_name !== undefined && info.profile_name !== profile) {identityRejected = true; cancelReview(); publish({reviewItems: []}); publish({verified: false, control: null, requests: [], error: '대화 식별 정보가 선택한 사건과 일치하지 않습니다.'}); return}
        const eventStored = info.session_key ?? info.stored_session_id
        if (eventStored !== undefined && eventStored !== live?.stored_session_id) {
          // Compaction rotates the persisted tip. Resolve it through the authenticated root mapping again.
          publish({verified: false, control: null})
          void refresh().catch(() => {})
          return
        }
      }
      const forwarded = event.type === 'session.info' ? {...event, payload: {...asRecord(event.payload), stored_session_id: live?.stored_session_id, profile_name: profile, running: typeof asRecord(event.payload).running === 'boolean' ? asRecord(event.payload).running : state.running}} : event
      callback.current?.(forwarded)
      const payload = asRecord(event.payload)
      const patch: Partial<Snapshot> = {conversation: project ? applyConversationEvent(state.conversation, event) : state.conversation}
      if (['session.info', 'session.usage', 'message.complete'].includes(event.type) && payload.usage !== undefined) patch.contextUsage = readContextUsage(payload.usage)
      if (event.type === 'message.start' && state.turnStartedAt === undefined && !state.reconnecting && !event.replayed) {patch.turnStartedAt = Date.now() / 1000; patch.turnTimeObserved = true}
      if (event.type === 'session.info' && typeof payload.turn_started_at === 'number' && Number.isFinite(payload.turn_started_at) && payload.turn_started_at > 0) {patch.turnStartedAt = payload.turn_started_at; patch.turnTimeObserved = false}
      if (event.type === 'message.start' || event.type === 'message.delta' || event.type === 'tool.start') patch.running = true
      patch.statusText = conversationEventStatus({...state, paused: state.control?.goal?.status === 'paused'}, event)
      if (event.type === 'session.info' && typeof payload.running === 'boolean') patch.running = payload.running
      if (event.type === 'session.control.update') patch.control = asRecord(payload.control) as Control
      if (event.type === 'request.cancel') patch.requests = state.requests.filter(request => request.id !== payload.id)
      if (event.type === 'message.complete') {patch.turnStartedAt = undefined; patch.turnTimeObserved = false; patch.running = false; patch.statusText = payload.status === 'interrupted' ? '일시 중지됨' : ''}
      publish(patch)
      if (event.type === 'message.complete') void refresh(false).catch(() => {})
    }
    // Requests must be owned before activate/resume replays open_requests. Never implicitly settle another client's prompt.
    const stopRequests = gateway.onRequest(request => {
      if (disposed || request.params.session_id !== runtimeId) {request.decline?.('This conversation does not display this request'); return true}
      if (!supportedRequests.has(request.method)) {publish({error: '이 요청은 대화 화면에서 지원하지 않습니다.'}); request.decline?.('This conversation does not display this request'); return true}
      publish({requests: [...state.requests.filter(item => item.id !== request.id && !(request.method === 'approval' && item.id.startsWith('legacy:'))), request]})
      return true
    })
    const stopEvents = gateway.onAny(event => {if (attaching && event.type !== 'session.reclaimed') buffered.push(event); else applyEvent(event)})
    const refresh = async (attach = true, offset = 0, attachChangedTip = true): Promise<void> => {
      const serial = ++refreshSerial
      cancelReview()
      publish({loading: true, reviewFresh: false})
      attaching = true
      try {
        const query = new URLSearchParams({profile})
        const [history, resolved] = await Promise.all([
          fetchJSON<History>(`/api/sessions/${encodeURIComponent(sessionId)}/messages?${query}&limit=100&offset=${offset}&order=latest&include_compacted=true&inline_images=false`),
          fetchJSON<Live>(`/api/sessions/${encodeURIComponent(sessionId)}/live?${query}`),
        ])
        if (disposed || serial !== refreshSerial) return
        if (resolved.profile_name !== profile || resolved.requested_session_id !== sessionId || !resolved.stored_session_id || history.session_id !== resolved.stored_session_id) {identityRejected = true; cancelReview(); publish({reviewItems: []}); throw new Error('저장된 대화와 실행 중인 대화의 연결을 확인할 수 없습니다.')}
        const previousTip = live?.stored_session_id
        live = resolved
        void readReviews()
        const page = savedConversation(history.messages), pageIds = new Set(page.map(row => row.id))
        canonical = offset ? [...page, ...canonical.filter(row => !pageIds.has(row.id))] : [...canonical.filter(row => !pageIds.has(row.id)), ...page]
        const saved = canonical
        const acceptedPending = pendingText !== null && pendingUserRowId !== null && pendingSavedIds !== null && saved.some(row => row.role === 'user' && row.id === pendingUserRowId && !pendingSavedIds!.has(row.id))
        publish({conversation: reconcileConversation(saved, state.conversation), loading: false, hasMore: offset || earlierOffset === 100 ? history.messages.length === 100 : state.hasMore, storedId: resolved.stored_session_id,
          control: resolved.control, running: resolved.running === true, error: state.uncertain && !acceptedPending ? state.error : null,
          ...(acceptedPending ? {uncertain: false} : {})})
        if (acceptedPending) {pendingText = null; pendingSavedIds = null; pendingUserRowId = null}
        if ((attach || attachChangedTip && previousTip !== resolved.stored_session_id) && resolved.available && resolved.runtime_session_id) {
          runtimeId = resolved.runtime_session_id
          await connect()
          if (disposed || serial !== refreshSerial) return
          const result = await gateway.request('session.activate', {...nativeParams(), omit_messages: true})
          if (disposed || serial !== refreshSerial) return
          acceptIdentity(result, resolved.runtime_session_id)
          await readControl()
          const pending = asRecord(asRecord(result).pending_approval)
          if (typeof pending.request_id === 'string' && !state.requests.some(request => request.method === 'approval')) {
            const id = `legacy:${pending.request_id}`
            const request: ServerRequest = {id, method: 'approval', params: {...pending, session_id: runtimeId}, respond: () => {}, fail: () => {}}
            publish({requests: [...state.requests, request]})
          }
        } else if (!resolved.available) {
          runtimeId = undefined; publish({runtimeId: undefined, verified: false, requests: []})
          scheduleReconnect()
        }
      } catch (error) {
        if (!disposed && serial === refreshSerial) {
          if (authFailure(error)) {terminalAuth = true; cancelReview(); publish({reviewItems: []})}
          publish({loading: false, verified: false, error: error instanceof Error ? error.message : '대화를 불러오지 못했습니다.'})
          if (attach || terminalAuth || identityRejected) scheduleReconnect()
        }
        throw error
      } finally {
        if (!disposed && serial === refreshSerial) {attaching = false; const events = buffered; buffered = []; const accepted = state.verified ? events.filter(event => event.session_id === runtimeId) : []; publish({conversation: reconcileBufferedEvents(state.conversation, accepted)}); for (const event of accepted) applyEvent(event, false)}
      }
    }
    const scheduleReconnect = () => {
      if (disposed) return
      if (terminalAuth || identityRejected || retries >= 5) {clearTimeout(retryTimer); retryTimer = undefined; publish({reconnecting: false}); return}
      if (retryTimer) return
      publish({reconnecting: true})
      retryTimer = setTimeout(() => {retryTimer = undefined; retries++; void refresh().catch(() => {})}, Math.min(1000 * 2 ** retries, 15000))
    }
    const connect = (): Promise<void> => {
      if (connecting) return connecting
      connecting = gateway.connect().then(() => {if (!disposed) {publish({connected: true})}}).catch(error => {if (authFailure(error)) {terminalAuth = true; cancelReview(); publish({reviewItems: []}); clearTimeout(retryTimer); retryTimer = undefined} throw error}).finally(() => {connecting = null})
      return connecting
    }
    const stopState = gateway.onState(connection => {
      if (disposed) return
      if (connection === 'closed' || connection === 'error') {publish({connected: false, verified: false, requests: [], statusText: '연결이 끊어졌습니다.'}); scheduleReconnect()}
    })
    const ensureRuntime = async () => {
      if (runtimeId && state.verified && gateway.connectionState === 'open') return
      if (identityRejected) throw new Error('대화 연결 정보를 다시 확인해야 합니다.')
      if (!live) throw new Error('저장된 대화를 먼저 확인해야 합니다.')
      await connect()
      // This is called only by an explicit user action. No mount/reconnect cold resume.
      const result = await gateway.request('session.resume', {session_id: live.stored_session_id, profile, omit_messages: true, inline_images: false, close_on_disconnect: false})
      if (disposed) throw new Error('대화가 변경되었습니다.')
      acceptIdentity(result)
      await readControl()
    }
    const send = async (text: string): Promise<boolean> => {
      if (disposed || operation || state.loading || state.uncertain || state.requests.length || state.control?.goal?.status === 'paused' || !text.trim()) return false
      operation = true; publish({pending: true, error: null, submissionNotice: undefined})
      let submitted = false, acknowledged = false
      try {
        await ensureRuntime()
        if (state.control?.goal?.status === 'paused') throw new Error('일시 중지된 조사는 재개 버튼으로 먼저 재개해 주세요.')
        let prompt = text, hidden = false
        if (text.startsWith('/')) {
          const [, name, arg = ''] = text.match(/^\/(\S+)\s*([\s\S]*)$/) ?? []
          if (!name) return false
          submitted = true
          const dispatch = await gateway.request<{type: string; message?: string; display?: string; output?: string; notice?: string}>('command.dispatch', {...nativeParams(), name, arg})
          if (dispatch.type !== 'send' && dispatch.type !== 'skill') {
            publish({statusText: dispatch.output ?? dispatch.notice ?? ''})
            await readControl()
            await refresh(false)
            return true
          }
          if (!dispatch.message?.trim()) throw new Error('명령이 보낼 내용을 반환하지 않았습니다.')
          prompt = dispatch.message; hidden = Boolean(dispatch.display)
        }
        pendingUserRowId = null
        pendingText = hidden ? null : prompt
        pendingSavedIds = pendingText === null ? null : new Set(canonical.map(row => row.id))
        submitted = true
        const receipt = await gateway.request<{status?: string; user_row_id?: number}>('prompt.submit', {...nativeParams(), text: prompt, ...(hidden ? {display_kind: 'hidden'} : {})})
        acknowledged = true
        if (!hidden && Number.isSafeInteger(receipt.user_row_id) && Number(receipt.user_row_id) > 0) pendingUserRowId = `saved:${receipt.user_row_id}`
        const notices: Record<string, string> = {streaming: '입력 접수 · 새 턴을 시작했습니다.', queued: '입력 접수 · 다음 턴 대기열에 등록했습니다.', steered: '입력 접수 · 현재 작업 뒤 조사에 반영하도록 전달했습니다.', redirected: '입력 접수 · 진행 중인 조사의 방향 전환을 요청했습니다.'}
        if (!disposed) {publish({running: true, submissionNotice: hidden ? undefined : notices[receipt.status ?? ''] ?? '입력을 접수했습니다. 처리 상태를 확인하고 있습니다.'}); await refresh()}
        return true
      } catch (error) {
        if (!disposed) publish({error: submitted ? '전송 결과를 확인 중입니다. 연결을 새로 확인해 주세요. 같은 메시지를 자동으로 다시 보내지 않습니다.' : error instanceof Error ? error.message : '메시지를 보내지 못했습니다.', uncertain: submitted && !acknowledged})
        return acknowledged
      } finally {operation = false; publish({pending: false})}
    }
    const toggle = async () => {
      if (disposed || operation || state.loading || state.uncertain || !state.control?.goal) return
      operation = true; publish({pending: true, error: null})
      try {
        const paused = state.control.goal.status === 'paused'
        await ensureRuntime()
        const result = await gateway.request<{control: Control; dispatch?: {type?: string; message?: string; display?: string}}>('session.control', {...nativeParams(), action: paused ? 'goal.resume' : 'goal.pause'})
        publish({control: result.control})
        if (!paused) await gateway.request('session.interrupt', nativeParams())
        else if (result.dispatch?.type === 'send' && result.dispatch.message?.trim()) {
          // The control returns the native continuation; the TUI submits this same dispatch.
          // A lost acknowledgement must never cause a second submit.
          publish({uncertain: true})
          await gateway.request('prompt.submit', {...nativeParams(), text: result.dispatch.message, display_kind: 'hidden'})
          publish({uncertain: false, running: true})
        }
        await readControl()
        await refresh(false)
      } catch (error) {publish({error: error instanceof Error ? error.message : '조사 상태 변경을 확인하지 못했습니다.'})}
      finally {operation = false; publish({pending: false})}
    }
    const approval = async (choice: string) => {
      const request = state.requests.find(request => request.method === 'approval')
      if (disposed || !request || !state.verified) return
      const choices = request.params.choices
      if (Array.isArray(choices) && !choices.includes(choice)) return
      if (request.id.startsWith('legacy:')) {
        await gateway.request('approval.respond', {...nativeParams(), choice, request_id: request.params.request_id})
      } else request.respond({choice})
      publish({requests: state.requests.filter(item => item.id !== request.id)})
    }
    const actions: Owner = {send, toggle, approval, refresh: async () => {terminalAuth = false; retries = 0; clearTimeout(retryTimer); retryTimer = undefined; identityRejected = false; await refresh()}, earlier: async () => {if (state.loading) return; await refresh(false, earlierOffset); earlierOffset += 100}}
    owner.current = actions
    publish({loading: true})
    void refresh().catch(() => {})
    return () => {disposed = true; cancelReview(); document.removeEventListener('visibilitychange', reviewVisibility); refreshSerial++; clearTimeout(retryTimer); stopRequests(); stopEvents(); stopState(); gateway.close(); if (owner.current === actions) owner.current = null}
  }, [active, profile, scope, sessionId])
  const visible = snapshot.scope === scope && active ? snapshot : empty(scope)
  const [clock, setClock] = useState(Date.now)
  const timing = visible.connected && visible.verified && visible.running && visible.control?.goal?.status !== 'paused' && /생각|thinking|압축|compacting/i.test(visible.statusText)
  useEffect(() => {
    if (!timing) return
    const timer = setInterval(() => setClock(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [timing])
  const statusText = timedConversationStatus(visible.statusText, visible.turnStartedAt, Boolean(visible.turnTimeObserved), clock)
  const pendingApproval = visible.requests.find(request => request.method === 'approval') ?? null
  const onApproval = useCallback((choice: string) => owner.current?.approval(choice) ?? Promise.resolve(), [])
  const connected = visible.connected && visible.verified
  const connectionText = visible.control?.goal?.status === 'paused' ? '조사가 일시 중지되어 있어요. 재개하면 같은 대화를 이어갑니다.'
    : visible.reconnecting ? '저장된 대화를 표시하고 있어요. 현재 연결을 다시 확인하고 있어요.'
    : visible.loading ? '저장된 대화와 현재 연결을 확인하고 있어요.'
    : !connected ? '저장된 대화를 표시하고 있어요. 현재 조사 실행 상태는 확인되지 않았습니다.'
    : visible.running ? '연결됨 · 조사 중' : '연결됨 · 응답 대기 중'
  const reviewActivity = connected && !visible.loading && !visible.error && !visible.reviewError && visible.reviewFresh && visible.control?.goal?.status !== 'paused' ? currentReviewActivity(visible.reviewItems) : null
  const displayed = withReviewChat(visible.conversation.rows, visible.reviewItems)
  if (visible.submissionNotice) displayed.push({id: 'native:submission-status', role: 'assistant', speaker: 'system', kind: 'notice', text: visible.submissionNotice})
  if (visible.reviewError || visible.reviewOmitted) displayed.push({id: 'review:coverage', role: 'assistant', speaker: 'system', kind: 'notice', text: visible.reviewError ?? `검토 이력 표시 범위: 영수증 ${visible.reviewOmitted}건은 표시되지 않았거나 확인하지 못했습니다.`})
  return {rows: displayed, reviewActivity, reviewCaseId: visible.reviewCaseId, running: connected && visible.running, connected, connectionText, reconnecting: visible.reconnecting, loading: visible.loading,
    contextUsage: visible.contextUsage, statusText, error: visible.error, inputDisabled: !active || !sessionId.trim() || !profile.trim() || !visible.storedId || visible.loading || visible.pending || visible.uncertain || visible.requests.length > 0 || visible.control?.goal?.status === 'paused',
    hasMore: visible.hasMore, onLoadEarlier: useCallback(() => {void owner.current?.earlier().catch(() => {})}, []),
    send: useCallback((text: string) => owner.current?.send(text) ?? Promise.resolve(false), []), pendingRequests: visible.requests, pendingApproval, onApproval,
    reconnect: useCallback(() => {void owner.current?.refresh().catch(() => {})}, []), runtimeId: visible.runtimeId, storedId: visible.storedId,
    control: {paused: visible.control?.goal?.status === 'paused', pending: visible.pending, disabled: !active || visible.loading || visible.uncertain || !visible.control?.goal,
      onToggle: useCallback(() => {void owner.current?.toggle()}, [])}}
}
