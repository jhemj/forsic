import {useEffect, useRef, useState, type ReactNode} from 'react'
import type {ContextUsage} from '../lib/forsic-context'
import {Markdown} from './Markdown'
import type {InspectorToolCall} from '../lib/forsic-inspector'
import {ForsicToolCall} from './ForsicToolCall'
import {isRoutineReportingLookup} from '../lib/forsic-tool-detail'
import './forsic-conversation.css'

import type {ConversationRow, ReviewChatItem, ReviewActivity} from '../lib/forsic-conversation'
export type {ConversationRow} from '../lib/forsic-conversation'
export interface ForsicConversationProps {
  rows: ConversationRow[]
  running: boolean
  connected: boolean
  connectionText?: string
  loading?: boolean
  statusText?: string
  error?: string | null
  hasMore?: boolean
  onLoadEarlier?: () => void
  onSend: (text: string) => Promise<boolean>
  inputDisabled?: boolean
  pendingContent?: ReactNode
  children?: ReactNode
  control?: {paused: boolean; pausing?: boolean; pending: boolean; disabled?: boolean; onToggle: () => void}
  reviewActivity?: ReviewActivity | null
  recentAvgTps?: number | null
  contextUsage?: ContextUsage | null
}

function ContextMeter({usage, connected}: {usage?: ContextUsage | null; connected: boolean}) {
  const percent = usage ? Math.round(usage.used / usage.maximum * 100) : null
  const count = (value: number) => new Intl.NumberFormat('en', {notation: 'compact', maximumFractionDigits: 1}).format(value)
  const label = usage ? `${usage.estimated ? '추정 문맥' : '문맥'} ${count(usage.used)} / ${count(usage.maximum)} · ${percent}%` : '문맥 확인 중'
  const detail = usage ? `${usage.used.toLocaleString('ko-KR')} / ${usage.maximum.toLocaleString('ko-KR')} 토큰 · ${usage.estimated ? '추정값' : '최근 모델 입력 기준'}${connected ? '' : ' · 연결 끊김, 마지막 확인값'}` : '현재 문맥 사용량을 아직 받지 못했습니다.'
  return <div className="fc-context" title={detail} aria-label={detail} data-stale={!connected}>
    <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7"/><circle cx="10" cy="10" r="7" pathLength="100" strokeDasharray={`${Math.min(100, percent ?? 0)} 100`}/></svg>
    <span>{label}{usage && !connected && <small> · 마지막 확인</small>}</span>
  </div>
}

function RecentSpeed({value, connected}: {value?: number | null; connected: boolean}) {
  if (!connected || typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null
  const label = `최근 평균 ${value.toLocaleString('ko-KR', {minimumFractionDigits: 1, maximumFractionDigits: 1})} 토큰/초`
  const detail = '사용량이 확인된 최근 완료 API 요청 최대 10회의 출력 토큰 합계를 요청 시간 합계로 나눈 값입니다. 입력 처리와 대기 시간을 포함하며, 순수 생성 속도나 세션 전체 평균이 아닙니다.'
  return <span className="fc-recent-speed" tabIndex={0} title={detail} aria-label={`${label}. ${detail}`}>{label}</span>
}

/** Abstract activity belongs to the pending reply, never the application's mascot. */
function ReplyActivity() {
  return <svg className="fc-reply-activity" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M3 6h18M3 18h18" fill="none" stroke="currentColor" opacity=".2" />
    {[6, 12, 18].map((cx, i) => <circle key={cx} cx={cx} cy="12" r="2" style={{animationDelay: `${i * 160}ms`}} />)}
  </svg>
}

const toolTime = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'})
function ToolStartTime({timestamp}: {timestamp?: number}) {
  if (typeof timestamp !== 'number' || !Number.isFinite(timestamp)) return null
  return <time dateTime={new Date(timestamp * 1000).toISOString()} title="작업 시작 · 한국 표준시">{toolTime.format(timestamp * 1000)} KST</time>
}
function LookupHistory({calls, timestamp}: {calls: InspectorToolCall[]; timestamp?: number}) {
  const active = calls.filter(call => call.status === 'running' && call.result === undefined).length
  const accepted = calls.filter(call => call.status === 'running' && call.result !== undefined).length
  const unknown = calls.filter(call => call.status === 'unknown').length
  return <details className="fc-lookup-history">
    <summary><span>내부 조회 {calls.length}건</span>{active > 0 && <span>진행 중 {active}건</span>}{accepted > 0 && <span>작업 접수 {accepted}건</span>}{unknown > 0 && <span>결과 미확인 {unknown}건</span>}<ToolStartTime timestamp={timestamp}/><span className="fc-lookup-hint">내역 보기</span></summary>
    <div className="fc-tool-list">{calls.map(call => <ForsicToolCall key={call.id} call={call}/>)}</div>
  </details>
}

function ToolGroup({calls, timestamp}: {calls: InspectorToolCall[]; timestamp?: number}) {
  const failed = calls.filter(call => call.status === 'error').length
  const groups: {lookup: boolean; calls: InspectorToolCall[]}[] = []
  for (const call of calls) {
    const lookup = isRoutineReportingLookup(call), previous = groups.at(-1)
    if (lookup && previous?.lookup) previous.calls.push(call)
    else groups.push({lookup, calls: [call]})
  }
  const onlyLookups = groups.length === 1 && groups[0].lookup
  return <section className="fc-tools" aria-label="도구 실행 기록">
    {calls.length > 1 && !onlyLookups && <div className="fc-tool-group-heading"><span>작업 {calls.length}건</span><ToolStartTime timestamp={timestamp}/>{failed > 0 && <span className="fc-tool-group-failures">실패 {failed}건</span>}</div>}
    <div className="fc-tool-list">{groups.map(group => group.lookup ? <LookupHistory key={group.calls[0].id} calls={group.calls} timestamp={onlyLookups ? timestamp : undefined}/> : <ForsicToolCall key={group.calls[0].id} call={group.calls[0]}/>)}</div>
  </section>
}

const receiptLabels: Record<string, string> = {received: '응답 수신', in_progress: '검토 중', delivery_unconfirmed: '수신 미확인', invalid_advice: '응답 확인 실패', incomplete: '불완전 응답', cancelled: '취소됨', stale: '이전 자료 기준', unavailable: '기록 확인 불가'}
function ReviewCard({item}: {item: ReviewChatItem}) {
  const time = item.observed_at ? new Date(item.observed_at) : null
  const answer = item.disposition
  const dispositionLabels: Record<string, string> = {investigated: '조사 기록', refuted: '반증 기록', scope_limited: '범위 제한 기록'}
  return <div className="fc-review-exchange" data-historical={item.historical}>
  <article className={`fc-message fc-review fc-${item.speaker}`}>
    <div className="fc-author">{item.speaker === 'ren' && <img className="fc-ren-avatar" src="/assets/ren-reviewer.svg" alt="" aria-hidden="true"/>}{item.speaker === 'ren' ? '렌' : '시스템'}<span className="fc-message-kind">{item.kind === 'executive_question' ? `임원 관점 질문 · ${item.round}/3회` : item.kind === 'review_status' ? '검토 상태' : '독립 검토'}</span></div>
    <div className="fc-bubble">
      <p className="fc-review-text">{item.public_text}</p>
      <div className="fc-review-meta"><span>{receiptLabels[item.receipt_state] ?? '상태 미상'}</span>{item.historical && <span>과거 목표의 검토 이력</span>}{item.freshness === 'stale' && <span>이전 자료 기준</span>}{item.freshness === 'unknown' && <span>현재성 미확인</span>}<span>{time && Number.isFinite(time.valueOf()) ? <>{item.time_basis === 'request_recorded' ? '요청 시각' : '응답 관측 시각'} <time dateTime={item.observed_at!}>{time.toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'})} KST</time></> : '표시 시각 미상'}</span></div>
      {(item.public_reason || item.next_check) && <details><summary>검토 근거 펼치기</summary>{item.public_reason && <p className="fc-review-text">{item.public_reason}</p>}{item.next_check && <p className="fc-review-text">다음 확인: {item.next_check}</p>}<p>렌은 별도 문맥의 조언자입니다. 원본 재분석·도구 실행·조사 승인 기록이 아닙니다.</p></details>}
      {!answer && item.speaker === 'ren' && <p className="fc-review-waiting">{item.historical ? '이 검토 이력에 연결된 답변 미기록' : '포식이 답변 미기록'}</p>}
    </div>
  </article>
  {answer && <article className="fc-message fc-assistant fc-review-answer" aria-label="포식이 검토 답변 기록">
    <div className="fc-author">포식이<span className="fc-message-kind">{item.historical ? '과거 검토 답변 기록' : '검토 답변 기록'}</span></div>
    <div className="fc-bubble">
      <blockquote className="fc-reply-quote"><span>렌의 질문에 답변</span><p>{item.public_text}</p></blockquote>
      <p className="fc-review-text">{answer.reason}</p>
      {answer.limitation && <p className="fc-review-text">한계: {answer.limitation}</p>}
      <div className="fc-review-meta"><span>{dispositionLabels[answer.disposition] ?? '처분 기록'}</span><span>저장된 질문 처분 · {answer.question_id} · 수정 {answer.revision}</span>{item.historical && <span>과거 목표 기준</span>}{item.freshness !== 'current' && <span>{item.freshness === 'stale' ? '이전 자료 기준' : '현재성 미확인'}</span>}</div>
      <details><summary>근거 참조 {answer.citation_refs.length}개 · 출처 보기</summary><p>검토 {item.review_id}{item.finding_id ? ` · 의견 ${item.finding_id}` : ''} · 목표 {item.goal_id}</p>{answer.citation_refs.length ? <ul>{answer.citation_refs.map((ref, index) => <li key={index}>{ref}</li>)}</ul> : <p>연결된 근거 참조 없음</p>}<p>저장된 답변 기록입니다. 렌의 조언 자체는 조사 승인이나 근거가 아닙니다.</p></details>
    </div>
  </article>}
  </div>
}

export function ForsicConversation({rows, running, connected, loading, statusText, error, hasMore, onLoadEarlier, onSend, inputDisabled, pendingContent, children, control, contextUsage, recentAvgTps, reviewActivity}: ForsicConversationProps) {
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState(false)
  const [showLatest, setShowLatest] = useState(false)
  const [highlightedQuestion, setHighlightedQuestion] = useState<string | null>(null)
  const [reviewClock, setReviewClock] = useState(Date.now)
  useEffect(() => {
    if (!reviewActivity?.startedAt) return
    setReviewClock(Date.now())
    const timer = setInterval(() => setReviewClock(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [reviewActivity?.reviewId, reviewActivity?.startedAt])
  const sendingRef = useRef(false)
  const composing = useRef(false)
  const draftRef = useRef(draft)
  const viewport = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const nearBottom = useRef(true)
  const disabled = Boolean(inputDisabled)
  function scrollToLatest() {
    const element = viewport.current
    if (element) element.scrollTop = element.scrollHeight
    nearBottom.current = true
    setShowLatest(false)
  }
  useEffect(() => {
    const element = viewport.current
    if (element && nearBottom.current) element.scrollTop = element.scrollHeight
  }, [rows, running, pendingContent])
  async function submit() {
    const text = draftRef.current.trim()
    if (!text || disabled || sendingRef.current) return
    const submittedDraft = draftRef.current
    sendingRef.current = true; setSending(true); setSendError(false)
    try {
      if (await onSend(text)) {
        if (draftRef.current === submittedDraft) {draftRef.current = ''; setDraft('')}
      } else setSendError(true)
    } catch {setSendError(true)} finally {sendingRef.current = false; setSending(false)}
  }
  const shownRows: ConversationRow[] = []
  for (const row of rows) {
    const previous = shownRows.at(-1)
    if (row.role === 'tools' && row.calls?.length && row.calls.every(isRoutineReportingLookup) && previous?.role === 'tools' && previous.calls?.length && previous.calls.every(isRoutineReportingLookup)) {
      shownRows[shownRows.length - 1] = {...previous, calls: [...previous.calls, ...row.calls]}
    } else shownRows.push(row)
  }
  // A chronological context quote is not an assertion that a question was resolved.
  const replyContexts = new Map<string, ConversationRow>()
  let precedingQuestion: ConversationRow | undefined
  for (const row of shownRows) {
    if (row.review) continue
    if (row.replyTo) {const question = shownRows.find(candidate => candidate.id === row.replyTo && candidate.role === 'user'); if (question) replyContexts.set(row.id, question); precedingQuestion = undefined; continue}
    if (row.role === 'user') precedingQuestion = row.positionUnknown ? undefined : row
    else if (precedingQuestion && row.role === 'assistant' && row.kind !== 'notice' && row.text?.trim()) {replyContexts.set(row.id, precedingQuestion); precedingQuestion = undefined}
  }
  const messageId = (id: string) => `forsic-message-${encodeURIComponent(id)}`
  function showQuestion(id: string) {
    const element = document.getElementById(messageId(id))
    if (!element) return
    setHighlightedQuestion(id)
    element.focus({preventScroll: true})
    element.scrollIntoView?.({block: 'center', behavior: 'auto'})
  }
  const inlineProgress = running && !rows.some(row => row.role === 'assistant' && row.streaming)
  return <section className="forsic-conversation" aria-label="포식이와 대화">
    <div className="fc-viewport" ref={viewport} onScroll={() => {const el = viewport.current; if (el) {nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96; setShowLatest(!nearBottom.current)}}}>
      <div className="fc-thread" role="log" aria-label="대화 내용" aria-busy={Boolean(loading)}>
        {hasMore && <button className="fc-earlier" disabled={loading} onClick={onLoadEarlier}>이전 대화 불러오기</button>}
        {loading && <p className="fc-empty" role="status">대화를 불러오고 있어요.</p>}
        {!loading && rows.length === 0 && <div className="fc-welcome"><h2>새 대화</h2><div className="fc-starters" aria-label="질문 작성 도움말">{['어떤 자료부터 확인하면 좋을까요?', '지금까지 확인한 내용을 정리해 주세요.'].map(text => <button key={text} type="button" disabled={disabled} onClick={() => {draftRef.current = text; setDraft(text); input.current?.focus()}}>{text}<span aria-hidden="true">↗</span></button>)}</div></div>}
        {shownRows.map(row => row.review ? <ReviewCard key={row.id} item={row.review}/> : row.role === 'tools' ? <ToolGroup key={row.id} calls={row.calls ?? []} timestamp={row.timestamp} /> : <article key={row.id} id={messageId(row.id)} tabIndex={row.role === 'user' ? -1 : undefined} className={`fc-message fc-${row.role}${row.kind === 'notice' ? ' fc-notice' : ''}${highlightedQuestion === row.id ? ' fc-question-highlight' : ''}`}>
          <div className="fc-author">{row.role === 'user' ? '나' : row.speaker === 'system' ? '시스템' : '포식이'}{row.positionUnknown && <span className="fc-message-kind">수신 위치 미확인</span>}{row.role === 'assistant' && row.kind === 'briefing' && <span className="fc-message-kind">진행 안내</span>}</div>
          <div className="fc-bubble">{replyContexts.has(row.id) && <button type="button" className="fc-reply-quote" onClick={() => showQuestion(replyContexts.get(row.id)!.id)} aria-label="앞선 내 질문으로 이동"><span>{row.replyTo ? '이 질문에 대한 답변 · 나' : '이 질문에 이어서 · 나'}</span><p>{replyContexts.get(row.id)!.text}</p></button>}{row.role === 'user' ? <p className="fc-user-text">{row.text}</p> : <Markdown content={row.text ?? ''} />}
            {row.streaming && running && <div className="fc-progress" role="status"><ReplyActivity /><span>답변을 작성하고 있어요</span></div>}
          </div>
        </article>)}
        {(inlineProgress || reviewActivity) && <article className={`fc-message fc-pending fc-${reviewActivity ? 'ren' : 'assistant'}`} aria-label={reviewActivity ? '렌 독립 검토 진행' : '포식이 응답 진행'}>
          <div className="fc-pending-identity" key={reviewActivity ? `ren:${reviewActivity.reviewId}` : 'forsic'}>
            <div className="fc-author">{reviewActivity && <img className="fc-ren-avatar" src="/assets/ren-reviewer.svg" alt="" aria-hidden="true"/>}{reviewActivity ? '렌' : '포식이'}</div>
            <div className="fc-progress" role="status">{!reviewActivity && <ReplyActivity/>}<span>{reviewActivity ? '독립 질문을 정리하고 있어요' : statusText || '살펴보고 있어요'}{reviewActivity?.startedAt !== undefined && <small className="fc-review-elapsed"> · 경과 {Math.floor(Math.max(0, reviewClock / 1000 - reviewActivity.startedAt) / 60)}m {Math.floor(Math.max(0, reviewClock / 1000 - reviewActivity.startedAt) % 60)}s</small>}</span></div>
          </div>
        </article>}
        {pendingContent}
        {error && <p className="fc-error" role="alert">{error}</p>}
        {children}
      </div>
    </div>
    <div className="fc-compose-wrap">{showLatest && <button type="button" className="fc-latest" onClick={scrollToLatest}><span aria-hidden="true">↓</span>최근 대화로</button>}<form className="fc-composer" onSubmit={event => {event.preventDefault(); void submit()}}>
      <label className="fc-sr-only" htmlFor="forsic-chat-draft">포식이에게 메시지</label>
      <textarea ref={input} id="forsic-chat-draft" value={draft} rows={2} disabled={disabled} placeholder={control?.paused ? '재개하면 이 대화를 이어갈 수 있어요…' : '포식이에게 물어보세요…'} onChange={event => {draftRef.current = event.target.value; setDraft(event.target.value)}} onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && !composing.current && event.keyCode !== 229) {event.preventDefault(); void submit()}
      }} />
      <div className="fc-compose-actions"><div className="fc-compose-meta"><ContextMeter usage={contextUsage} connected={connected}/><RecentSpeed value={recentAvgTps} connected={connected}/></div><div>

        {control && <button type="button" className="fc-control" onClick={control.onToggle} disabled={control.pending || control.pausing || control.disabled} aria-label={control.paused ? '조사 재개' : control.pausing ? '안전하게 일시정지 중' : '안전하게 일시정지'}><svg viewBox="0 0 16 16" aria-hidden="true">{control.paused ? <path d="m5 3 8 5-8 5Z" /> : <path d="M4 3h3v10H4zm5 0h3v10H9z" />}</svg>{control.pending ? '처리 중' : control.pausing ? '작업 마무리 중' : control.paused ? '재개' : '안전하게 일시정지'}</button>}
        <button type="submit" className="fc-send" disabled={disabled || sending || !draft.trim()} aria-label="메시지 보내기"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m10 15 0-10m-4 4 4-4 4 4" /></svg></button>
      </div></div>
    </form>{sendError && <p className="fc-error" role="alert">전송 결과를 확인해 주세요. 작성한 내용은 유지했습니다.</p>}</div>
  </section>
}
