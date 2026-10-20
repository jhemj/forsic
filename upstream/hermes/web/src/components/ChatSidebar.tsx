/**
 * ChatSidebar — structured-events panel that sits next to the xterm.js
 * terminal in the dashboard Chat tab.
 *
 * Two WebSockets, one per concern:
 *
 *   1. **JSON-RPC sidecar** (`GatewayClient` → /api/ws) — a lightweight
 *      session used only for side-panel connection state and
 *      credential warnings. Independent of the PTY pane's session by
 *      design. The model badge does NOT come from here: it reads the
 *      configured model and server from the same connection API as Forsic settings.
 *      The model card opens the Forsic plugin's existing connection settings;
 *      it never probes or changes a provider itself.
 *
 *   2. **Event subscriber** (/api/events?channel=…) — passive, receives
 *      every dispatcher emit from the PTY-side `tui_gateway.entry` that
 *      the dashboard fanned out.  The sidebar uses it for `session.info`
 *      (live chat title) and `dashboard.new_session_requested`.  The
 *      `channel` id ties this listener to the same chat tab's PTY child —
 *      see `ChatPage.tsx` for where the id is generated.  Transient drops
 *      (gateway restart, network blip) auto-reconnect with exponential
 *      backoff; auth rejections are terminal.  See `lib/events-reconnect`.
 *
 * Best-effort throughout: WS failures show in the badge / banner, the
 * terminal pane keeps working unimpaired.
 */

import { Button } from '@nous-research/ui/ui/components/button'
import { Badge } from '@nous-research/ui/ui/components/badge'
import { Card } from '@nous-research/ui/ui/components/card'

import { GatewayClient, type ConnectionState } from '@/lib/gatewayClient'
import { EventsFeedClient } from '@/lib/eventsFeedClient'
import { fetchJSON } from '@/lib/api'
import {
  EVENTS_MAX_RECONNECT_ATTEMPTS,
  eventsGaveUpMessage,
  eventsReconnectDelayMs,
  eventsReconnectingMessage,
  eventsRejectedMessage,
  isEventsAuthRejection,
  isEventsAuthRejectionMessage,
  isEventsFeedMessage,
  shouldRetryEventsClose
} from '@/lib/events-reconnect'
import { credentialWarning, sidecarErrorMessage } from '@/lib/chat-sidebar-banner'
import { titleFromSessionInfoPayload } from '@/lib/chat-title'
import { configuredConnection, type ConnectionSnapshot } from '@/lib/forsic-connection'
import type { InspectorEvent } from '@/lib/forsic-inspector'

import { cn } from '@/lib/utils'
import { useI18n } from '@/i18n'
import { AlertCircle, ChevronDown, KeyRound, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

interface SessionInfo {
  cwd?: string
  model?: string
  provider?: string
  credential_warning?: string
  title?: string
}

// Auto-redial budget for the JSON-RPC sidecar (#95951). After this many
// bounded-backoff attempts the manual Reconnect affordance stays the only
// path, mirroring the events feed's give-up contract.
const SIDE_CAR_MAX_RECONNECT_ATTEMPTS = 5;

// Surfaced once when the redial budget is exhausted. Only this module may
// clear it (on the next successful open), matching how the events feed
// owns its own banner messages.
const SIDE_CAR_GAVE_UP_MESSAGE =
  "gateway sidecar disconnected — gave up after " +
  `${SIDE_CAR_MAX_RECONNECT_ATTEMPTS} attempts, use Reconnect`;

const STATE_LABEL: Record<ConnectionState, string> = {
  idle: 'idle',
  connecting: 'connecting',
  open: 'UI connected',
  closed: 'closed',
  error: 'error'
}

const STATE_TONE: Record<ConnectionState, 'secondary' | 'warning' | 'success' | 'destructive'> = {
  idle: 'secondary',
  connecting: 'warning',
  open: 'success',
  closed: 'secondary',
  error: 'destructive'
}

interface ChatSidebarProps {
  channel: string
  /** Chat profile from the dashboard switcher / URL scope. */
  profile?: string
  className?: string
  onDashboardNewSessionRequest?: () => void
  onSessionTitleChange?: (title: string | null) => void
  onInspectorEvent?: (event: InspectorEvent) => void
  onInspectorReconnect?: () => void
}

/** Build the ``session.create`` params for the sidecar session.
 *
 * Extracted from the effect below so the invariant — close_on_disconnect
 * is set, source is "tool", and the profile is forwarded when present —
 * can be tested without reading component source text. See
 * ``chat-sidebar-session-params.test.ts``.
 */
export function sidecarSessionCreateParams(profile?: string): Record<string, unknown> {
  return {
    close_on_disconnect: true,
    source: 'tool',
    ...(profile ? { profile } : {})
  }
}

export function ChatSidebar({
  channel,
  profile,
  className,
  onDashboardNewSessionRequest,
  onSessionTitleChange,
  onInspectorEvent,
  onInspectorReconnect
}: ChatSidebarProps) {
  const navigate = useNavigate()
  const { locale } = useI18n()
  const ko = locale === 'ko'
  // `version` bumps on reconnect (manual button, profile/channel switch) and
  // re-runs the socket effects. The clients themselves live for the whole
  // component: the shared client keeps per-session seq watermarks and asks
  // the gateway to replay the gap on the next `connect()`, which only works
  // when the SAME instance survives the drop.
  const [version, setVersion] = useState(0)
  const gw = useMemo(() => new GatewayClient(), [])
  const feed = useMemo(() => new EventsFeedClient(), [])
  // Sidecar auto-redial budget (#95951). A ref, NOT effect state: the counter
  // must survive the [gw, version] effect re-runs a redial triggers, or the
  // budget resets every attempt and never exhausts.
  // Reset on a successful open and on scope switches.
  const sidecarRedialAttemptRef = useRef(0)
  const sidecarGaveUpRef = useRef(false)

  const [state, setState] = useState<ConnectionState>('idle')
  const [info, setInfo] = useState<SessionInfo>({})
  const [error, setError] = useState<string | null>(null)
  const [connection, setConnection] = useState<ReturnType<typeof configuredConnection> | null>(null)
  const [connectionError, setConnectionError] = useState(false)

  // The plugin settings and this card share the serving workspace's connection
  // snapshot. These disk-only reads never probe the provider. llm_current is the
  // applied config; a pending save must not appear as the active connection.
  useEffect(() => {
    let live = true
    const refresh = () => {
      void fetchJSON<ConnectionSnapshot>('/api/plugins/forsic/connections').then(config => {
        if (!live) return
        setConnection(configuredConnection(config))
        setConnectionError(false)
      }).catch(() => {
        if (live) setConnectionError(true)
      })
    }
    refresh()
    window.addEventListener('forsic:connections-changed', refresh)
    return () => {
      live = false
      window.removeEventListener('forsic:connections-changed', refresh)
    }
  }, [version])

  const openConnectionSettings = () => {
    window.dispatchEvent(new Event('forsic:open-connection-settings'))
  }

  // Profile or PTY channel change tears down both WebSockets. Bump `version`
  // (same path as the manual Reconnect button) so the gateway client is
  // recreated and the events feed resubscribes — otherwise the old events
  // socket's close handler can leave a stale error banner after a switch.
  const scopeKey = `${channel}\0${profile ?? ''}`
  const prevScopeKey = useRef<string | null>(null)
  useEffect(() => {
    if (prevScopeKey.current === null) {
      prevScopeKey.current = scopeKey
      return
    }
    if (prevScopeKey.current === scopeKey) return
    prevScopeKey.current = scopeKey
    setError(null)
    // Fresh scope, fresh sidecar redial budget (#95951).
    sidecarRedialAttemptRef.current = 0
    sidecarGaveUpRef.current = false
    setVersion(v => v + 1)
  }, [scopeKey])

  useEffect(() => {
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setInfo({})
      setError(null)
    })
    const offState = gw.onState(setState)

    const offSessionInfo = gw.on('session.info', ev => {
      // session.info is surface-specific on the wire; narrow to the fields this sidebar reads.
      const payload = ev.payload as SessionInfo | undefined

      if (payload) {
        setInfo(prev => ({ ...prev, ...payload }))
      }
    })

    const offError = gw.on('error', ev => {
      const message = ev.payload?.message

      if (message) {
        console.warn(`[chat-sidebar] sidecar error: ${message}`)
        setError(sidecarErrorMessage(message))
      }
    })

    // Auto-redial after a transient drop (#95951): a dashboard service
    // restart closes the sidecar's WebSocket with 1012, and GatewayClient
    // deliberately delegates reconnect policy to this connection owner.
    // Bounded exponential backoff — the same shape the PTY pane uses —
    // capped at SIDE_CAR_MAX_RECONNECT_ATTEMPTS; after that the manual
    // Reconnect affordance stays the only path. A successful open resets
    // the counter; unmount or a scope switch (version bump) cancels the
    // pending timer because this effect tears down with the old client.
    let redialTimer: ReturnType<typeof setTimeout> | null = null;
    const offRedial = gw.onState((s) => {
      if (s === "open") {
        sidecarRedialAttemptRef.current = 0;
        if (sidecarGaveUpRef.current) {
          sidecarGaveUpRef.current = false;
          setError((current) =>
            current === SIDE_CAR_GAVE_UP_MESSAGE ? null : current,
          );
        }
        return;
      }
      if (s !== "closed" && s !== "error") {
        return;
      }
      if (cancelled || redialTimer) {
        return;
      }
      // The attempt counter lives in a ref: each redial rebuilds the client
      // and re-runs this effect, so a closure-local counter would reset and
      // the budget would never exhaust (#95951).
      if (sidecarRedialAttemptRef.current >= SIDE_CAR_MAX_RECONNECT_ATTEMPTS) {
        // Mirror the events feed's give-up contract: say so once, then the
        // manual Reconnect affordance stays the only path. Cleared again if
        // a later connection does open (manual reconnect followed by a
        // within-budget drop).
        if (!sidecarGaveUpRef.current) {
          sidecarGaveUpRef.current = true;
          setError((current) => current ?? SIDE_CAR_GAVE_UP_MESSAGE);
        }
        return;
      }
      const attempt = sidecarRedialAttemptRef.current;
      sidecarRedialAttemptRef.current += 1;
      const delayMs = Math.min(250 * 2 ** attempt, 3000);
      redialTimer = setTimeout(() => {
        redialTimer = null;
        if (!cancelled) {
          setVersion((v) => v + 1);
        }
      }, delayMs);
    });

    // Create the sidecar session so the gateway surfaces session-scoped
    // signals (connection state, credential warnings). It's independent of the
    // PTY pane's session by design; model settings do not use this session.
    gw.connect()
      .then(() => {
        if (cancelled) {
          return
        }
        // close_on_disconnect: the gateway reaps this sidecar session (and its
        // slash_worker subprocess) when the WS drops, instead of leaking it.
        return gw.request<{ session_id: string }>('session.create', sidecarSessionCreateParams(profile))
      })
      .catch((e: Error) => {
        if (!cancelled) {
          console.warn(`[chat-sidebar] sidecar connect failed: ${e.message}`)
          setError(sidecarErrorMessage(e.message))
        }
      })

    return () => {
      cancelled = true
      if (redialTimer) {
        clearTimeout(redialTimer)
        redialTimer = null
      }
      offRedial()
      offState()
      offSessionInfo()
      offError()
      gw.close()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gw, version])

  // Event subscriber WebSocket — receives the rebroadcast of every
  // dispatcher emit from the PTY child's gateway.  See /api/pub +
  // /api/events in hermes_cli/web_server.py for the broadcast hop.
  //
  // Framing, dispatch and connect timeout come from the shared JSON-RPC
  // client (`EventsFeedClient`); this effect owns only the retry policy and
  // the banner. Failures (auth/loopback rejection, server too old to expose
  // the endpoint, transient drops) surface in the same banner as the
  // JSON-RPC sidecar so the sidebar matches its documented best-effort UX
  // and the user always has a reconnect affordance.
  useEffect(() => {
    if (!channel) {
      return
    }
    let unmounting = false
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let attempt = 0

    // The banner is shared with `info.credential_warning` and the JSON-RPC
    // sidecar, and `error` is those messages' only home — the sidecar does
    // not re-emit. So the events feed may only write over an empty banner
    // or one of its own messages, and may only clear its own.
    const surface = (msg: string) =>
      !unmounting && setError(current => (isEventsFeedMessage(current) ? msg : (current ?? msg)))

    const clearEventsBanner = () => !unmounting && setError(current => (isEventsFeedMessage(current) ? null : current))

    // Single scheduling path: the client collapses `error` + `close` of one
    // socket generation into one `closed` transition, so only one timer is
    // ever queued per drop.
    const scheduleReconnect = () => {
      if (unmounting || reconnectTimer) {
        return
      }
      if (attempt >= EVENTS_MAX_RECONNECT_ATTEMPTS) {
        surface(eventsGaveUpMessage())
        return
      }

      const delay = eventsReconnectDelayMs(attempt)
      attempt += 1
      surface(eventsReconnectingMessage(delay))

      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        void connect()
      }, delay)
    }

    const connect = async () => {
      if (unmounting) {
        return
      }
      try {
        // Re-minted every attempt: tickets are single-use with a short TTL,
        // so a reconnect cannot replay the URL from the first connection.
        await feed.connect(channel)
      } catch {
        // Connect-phase failures (ticket mint, handshake timeout, close
        // during handshake) reach the reconnect ladder through the close
        // handler when a socket existed; a pre-socket failure has no close
        // event, so schedule here. `onClose` de-dupes via `reconnectTimer`.
        if (!unmounting && feed.lastCloseCode === null) {
          scheduleReconnect()
        }
      }
    }

    const offClose = feed.onClose(code => {
      if (unmounting) {
        return
      }
      onInspectorReconnect?.()
      console.warn(`[chat-sidebar] events feed closed code=${code ?? 'none'}`)
      if (code !== undefined && isEventsAuthRejection(code)) {
        surface(eventsRejectedMessage(code))
        return
      }
      // `undefined` = handshake timeout / error without a close frame.
      if (shouldRetryEventsClose(code)) {
        scheduleReconnect()
      }
    })

    const offState = feed.onState(state => {
      if (state === 'open') {
        attempt = 0
        clearEventsBanner()
        onInspectorReconnect?.()
      }
    })

    const offSessionInfo = feed.on('session.info', ev => {
      onInspectorEvent?.(ev)
      const title = titleFromSessionInfoPayload(ev.payload)
      if (title !== undefined) {
        onSessionTitleChange?.(title)
      }
    })
    const offNewSession = feed.on('dashboard.new_session_requested', () => {
      onDashboardNewSessionRequest?.()
    })
    // Reuse the chat's passive channel. Inspecting activity never creates a
    // session, submits a prompt or takes ownership of the running investigation.
    const offActivity = (['tool.start', 'tool.complete', 'message.complete'] as const)
      .map(type => feed.on(type, ev => onInspectorEvent?.(ev)))

    void connect()

    return () => {
      unmounting = true
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      offClose()
      offState()
      offSessionInfo()
      offNewSession()
      offActivity.forEach(off => off())
      feed.close()
    }
  }, [channel, feed, onDashboardNewSessionRequest, onSessionTitleChange, onInspectorEvent, onInspectorReconnect, version])

  const reconnect = useCallback(() => {
    setError(null)
    setVersion(v => v + 1)
  }, [])

  const modelName = connection?.model || (ko ? '모델 미설정' : 'Model not configured')
  const credential = credentialWarning(info.credential_warning)
  const banner = error ?? credential?.message ?? null
  const showReload = isEventsAuthRejectionMessage(error)

  return (
    <aside
      className={cn(
        'flex h-full w-full min-w-0 shrink-0 flex-col gap-3 overflow-y-auto overflow-x-hidden pr-1',
        className
      )}
    >
      <button
        type="button"
        onClick={openConnectionSettings}
        aria-label={ko ? '조사 모델 연결 설정 열기' : 'Open model connection settings'}
        className="flex w-full min-w-0 flex-col gap-1 rounded-lg border border-border bg-background px-3 py-2 text-left hover:bg-secondary/40 focus-visible:outline-2 focus-visible:outline-offset-2"
      >
        <span className="text-display text-xs tracking-wider text-text-tertiary">{ko ? '조사 모델' : 'model'}</span>
        {connectionError ? (
          <span className="text-xs text-destructive">{ko ? '연결 설정을 불러오지 못했어요.' : 'Could not read connection settings.'}</span>
        ) : !connection ? (
          <span className="text-xs text-text-secondary">{ko ? '연결 설정 불러오는 중…' : 'Loading connection settings…'}</span>
        ) : (
          <>
            <span className="flex min-w-0 max-w-full items-center gap-1 text-sm font-medium">
              <span className="min-w-0 whitespace-normal break-all">{modelName}</span>
              <ChevronDown className="size-3.5 shrink-0 text-text-secondary" />
            </span>
            <span className="break-all text-xs text-text-secondary">
              {connection.server || (ko ? '서버 주소 미설정' : 'Server not configured')}
            </span>
            {connection.pending && <span className="text-xs text-text-tertiary">{ko ? '변경 대기 · 다음 서버 시작 시 적용' : 'Changes pending · apply on next server start'}</span>}
          </>
        )}
      </button>
      <Badge tone={STATE_TONE[state]} className="self-start">
        {ko ? ({idle:'화면 대기',connecting:'화면 연결 중',open:'화면 연결됨',closed:'화면 연결 종료',error:'화면 연결 확인'}[state]) : STATE_LABEL[state]}
      </Badge>

      {banner && (
        <Card className="flex items-start gap-2 border-destructive/40 bg-destructive/5 px-3 py-2 text-xs">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />

          <div className="min-w-0 flex-1">
            <div className="wrap-break-word text-destructive">{banner}</div>

            {error && showReload && (
              <Button
                size="sm"
                outlined
                className="mt-1"
                onClick={() => window.location.reload()}
                prefix={<RefreshCw />}
              >
                {ko ? '페이지 새로고침' : 'Reload page'}
              </Button>
            )}
            {error && !showReload && (
              <Button size="sm" outlined className="mt-1" onClick={reconnect} prefix={<RefreshCw />}>
                {ko ? '상태 다시 연결' : 'Reconnect side panel'}
              </Button>
            )}
            {!error && credential && (
              <div className="mt-1 flex flex-wrap gap-2">
                <Button
                  size="sm"
                  outlined
                  prefix={<KeyRound />}
                  // Router navigation: a full page load would tear down the
                  // xterm scrollback and the chat sockets. (The mobile portal
                  // still lives under ChatPage, so router context is present.)
                  onClick={() => navigate('/env')}
                >
                  {ko ? '연결 키 설정' : 'Add key'}
                </Button>
                <Button size="sm" outlined onClick={openConnectionSettings}>
                  {ko ? '연결 설정' : 'Connection settings'}
                </Button>
              </div>
            )}
          </div>
        </Card>
      )}

    </aside>
  )
}
