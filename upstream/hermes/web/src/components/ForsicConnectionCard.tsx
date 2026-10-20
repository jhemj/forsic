import {useEffect, useState} from 'react'
import {Settings2} from 'lucide-react'
import type {SetupStatus} from './ForsicSetup'
import {fetchJSON} from '@/lib/api'
import {configuredConnection, type ConnectionSnapshot} from '@/lib/forsic-connection'

/** Saved settings only; this card never creates a session or probes a provider. */
export function ForsicConnectionCard({connected, reconnecting, connectionText}: {connected?: boolean; reconnecting?: boolean; connectionText?: string}) {
  const [connection, setConnection] = useState<ReturnType<typeof configuredConnection> | null>(null)
  useEffect(() => {
    let alive = true
    const refresh = () => {void fetchJSON<SetupStatus>('/api/plugins/forsic/setup').then(async status => {
      if (!status.local_admin) {
        if (alive) setConnection({model: status.llm_configured ? '모델 설정됨' : '모델 설정 필요', server: '상세 설정은 설치된 컴퓨터에서 확인', pending:false})
        return
      }
      const value = await fetchJSON<ConnectionSnapshot>('/api/plugins/forsic/connections')
      if (alive) setConnection(configuredConnection(value))
    }).catch(() => {if (alive) setConnection(null)})}
    refresh()
    window.addEventListener('focus', refresh)
    window.addEventListener('forsic:connections-changed', refresh)
    return () => {alive = false; window.removeEventListener('focus', refresh); window.removeEventListener('forsic:connections-changed', refresh)}
  }, [])
  return <button type="button" className="forsic-connection-card" aria-label="조사 모델 설정 열기" onClick={() => window.dispatchEvent(new CustomEvent('forsic:open-connection-settings'))}>
    <span className="forsic-connection-label"><span>조사 모델</span>{connected !== undefined && <span className="forsic-connection-state" role="status" data-connected={connected} title={`채팅 화면 연결 상태${connectionText ? ` · ${connectionText}` : ''}`}><i aria-hidden="true"/>{connected ? '연결됨' : reconnecting ? '재연결 중' : '연결 확인 필요'}</span>}<Settings2 size={14} /></span>
    <strong>{connection?.model || '설정 확인하기'}</strong>
    <span className="forsic-connection-url">{connection?.server || '서버 주소를 확인할 수 없어요.'}</span>
    {connection?.pending && <small>설정 변경 적용 대기 중</small>}
  </button>
}
