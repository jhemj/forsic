/** Forsic presents one native conversation through the shared gateway. */
import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {useSearchParams} from 'react-router'
import {RefreshCw} from 'lucide-react'
import {ForsicConversation} from '@/components/ForsicConversation'
import {ForsicRequests} from '@/components/ForsicRequests'
import {ForsicWorkbench} from '@/components/ForsicWorkbench'
import {ForsicConnectionCard} from '@/components/ForsicConnectionCard'
import {useForsicConversation} from '@/hooks/useForsicConversation'
import {useForsicInspector} from '@/hooks/useForsicInspector'
import {useProfileScope} from '@/contexts/useProfileScope'
import {usePageHeader} from '@/contexts/usePageHeader'
import {PluginSlot} from '@/plugins'
import './forsic-chat-page.css'

/** Read-only UI projection; it never controls or starts native review work. */
export interface ForsicReviewActivityDetail {
  profile: string; sessionId: string; caseId: string | null; active: boolean
  reviewId: string | null; startedAt: number | null; observedAt: number
}
export interface ChatPageProps {isActive?: boolean; navigationTarget?: HTMLElement | null; caseLabel?: string; conversationTitle?: string}

function ConversationPage({isActive = true, navigationTarget, caseLabel, conversationTitle, sessionId, profile, expectedCaseId}: ChatPageProps & {
  sessionId: string; profile: string; expectedCaseId?: string
}) {
  const {setTitle} = usePageHeader()
  const inspector = useForsicInspector({sessionId, profile, active: isActive})
  const chat = useForsicConversation({sessionId, profile, active: isActive, onEvent: inspector.onEvent})
  useEffect(() => {if (isActive) setTitle([caseLabel, conversationTitle || '포식이와 대화'].filter(Boolean).join(' / '))}, [caseLabel, conversationTitle, isActive, setTitle])
  const reviewSignal = useRef<ForsicReviewActivityDetail>({profile, sessionId, caseId: null, active: false, reviewId: null, startedAt: null, observedAt: Date.now()})
  useEffect(() => {
    const caseId = chat.reviewCaseId ?? null
    const activity = isActive && caseId ? chat.reviewActivity : null
    const detail: ForsicReviewActivityDetail = {profile, sessionId, caseId, active: Boolean(activity), reviewId: activity?.reviewId ?? null, startedAt: activity?.startedAt !== undefined ? activity.startedAt * 1000 : null, observedAt: Date.now()}
    reviewSignal.current = detail
    window.dispatchEvent(new CustomEvent('forsic:review-activity', {detail}))
  }, [profile, sessionId, isActive, chat.reviewCaseId, chat.reviewActivity])
  useEffect(() => {
    const publishCurrent = () => window.dispatchEvent(new CustomEvent('forsic:review-activity', {detail: {...reviewSignal.current, observedAt: Date.now()}}))
    window.addEventListener('forsic:review-activity-request', publishCurrent)
    return () => {
      window.removeEventListener('forsic:review-activity-request', publishCurrent)
      window.dispatchEvent(new CustomEvent('forsic:review-activity', {detail: {...reviewSignal.current, profile, sessionId, active: false, reviewId: null, startedAt: null, observedAt: Date.now()}}))
    }
  }, [profile, sessionId])
  return <div className="forsic-chat-page">
    <PluginSlot name="chat:top" />
    {isActive && navigationTarget && createPortal(<ForsicConnectionCard connected={chat.connected} reconnecting={chat.reconnecting} connectionText={chat.connectionText}/>, navigationTarget)}
    <ForsicWorkbench inspector={inspector} expectedCaseId={expectedCaseId} connected={chat.connected} running={chat.running} statusText={chat.control.paused ? '일시정지됨' : chat.statusText}>
      <ForsicConversation key={`${profile}:${sessionId}`} rows={chat.rows} running={chat.running} connected={chat.connected} loading={chat.loading}
        statusText={chat.control.paused ? '일시정지됨' : chat.statusText} error={chat.error} hasMore={chat.hasMore} onLoadEarlier={chat.onLoadEarlier}
        onSend={chat.send} inputDisabled={chat.inputDisabled} control={chat.control} contextUsage={chat.contextUsage} recentAvgTps={chat.recentAvgTps} reviewActivity={chat.reviewActivity} connectionText={chat.connectionText}
        pendingContent={<ForsicRequests requests={chat.pendingRequests} pendingApproval={chat.pendingApproval?.params}
          onApproval={chat.onApproval} connected={chat.connected} />}>
        {!chat.connected && !chat.control.paused && <div className="forsic-chat-recovery"><span>{chat.reconnecting ? '연결을 자동으로 다시 확인하고 있어요.' : '현재 연결을 다시 확인할 수 있어요.'}</span><button type="button" disabled={chat.loading} onClick={chat.reconnect}><RefreshCw size={13} />다시 연결</button></div>}
      </ForsicConversation>
    </ForsicWorkbench>
    <PluginSlot name="chat:bottom" />
  </div>
}

export default function ChatPage(props: ChatPageProps) {
  const [params] = useSearchParams()
  const {profile} = useProfileScope()
  const active = props.isActive !== false
  const requestedId = params.get('resume') || ''
  const requestedProfile = profile || 'default'
  const expectedCaseId = params.get('case') || undefined
  const [last, setLast] = useState({sessionId: requestedId, profile: requestedProfile, expectedCaseId})
  useEffect(() => {if (active && requestedId) setLast({sessionId: requestedId, profile: requestedProfile, expectedCaseId})}, [active, requestedId, requestedProfile, expectedCaseId])
  const selected = active ? {sessionId: requestedId, profile: requestedProfile, expectedCaseId} : last
  return <ConversationPage {...props} key={`${selected.profile}:${selected.expectedCaseId || selected.sessionId}`} {...selected} />
}
