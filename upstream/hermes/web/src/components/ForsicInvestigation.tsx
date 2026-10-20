import {useEffect, useLayoutEffect, useRef, useState} from 'react'
import {ArrowLeft, ChevronRight, Clock3, FileText, Link2, X, Network, ListChecks} from 'lucide-react'
import {timeContext, type InvestigationObservation, type InvestigationQuestion, type InvestigationSource, type InvestigationRef, type InvestigationSourcePage, type InvestigationSourceRequest, type InvestigationTime, type InvestigationView} from '@/lib/forsic-investigation'
import {fetchJSON} from '@/lib/api'
import {ForsicTimeline} from './ForsicTimeline'
import {ForsicRelationMap, QuestionJudgment} from './ForsicRelationMap'
import {displayTitle, displaySummary, filterSession, monitorTimeGroups, recordKey, sessionOf, timelineMark, type MonitorView} from './forsic-monitor'
import './forsic-investigation.css'

interface Props {
  data: InvestigationView | null
  loading: boolean
  error: string | null
  readSource: (request: InvestigationSourceRequest) => Promise<InvestigationSourcePage>
  onReviewTabChange?: (selected: boolean) => void
}
const ASSESSMENT: Record<string, string> = {supported: '지지됨', refuted: '반박됨', conflicting: '상충', undetermined: '판단 유보', not_recorded: '평가 미기록'}
const TIME_ROLE: Record<string, string> = {event_occurrence: '사건 발생 시각', source_timestamp: '출처에 적힌 시각', record_timestamp: '기록 시각', file_metadata: '파일 필드 시각', collection_time: '수집 시각', unknown: '의미 미확인'}
const QUOTE_VALIDATION: Record<string, string> = {exact_presented_field_handle: '제시된 원문 필드와 정확히 연결됨', exact_substring_only: '제시된 문자열 일치만 확인', not_applicable: '인용 검증 대상 없음'}
const REVIEW_DISPOSITION: Record<string, string> = {accepted: '반영', rejected: '채택하지 않음', deferred: '검토 보류', open: '검토 중', investigated: '조사함', refuted: '반박함', scope_limited: '범위 한정', not_recorded: '처리 상태 미기록'}
const WORK: Record<string, string> = {open: '열림', active: '조사 중', held: '보류', scoped_closed: '범위 종결', blocked_internal: '확인 필요', blocked_external: '외부 자료 필요', budget_deferred: '보류'}
function questionSources(question: InvestigationQuestion, sources: InvestigationSource[]): InvestigationRef[] {
  const refs = question.evidence_refs ?? question.evidence_ids.map(id => {
    const matches = sources.filter(item => item.id === id)
    return {kind: 'source', id, version: matches.length === 1 ? matches[0].version : ''}
  })
  return [...refs, ...question.evidence_ids.filter(id => !refs.some(ref => ref.id === id)).map(id => ({kind: 'source', id, version: ''}))]
}
const displayTime = (value: string) => value.replace('T', ' ')
const readableSession = (id: string | undefined, data: MonitorView) => data.sessions?.find(session => session.id === id)?.label || (id ? '제목이 없는 대화' : '출처 대화 미기록')

function SourcePreview({request: initial, source, readSource, onClose}: {source?: InvestigationSource; request: InvestigationSourceRequest; readSource: Props['readSource']; onClose: () => void}) {
  const [request, setRequest] = useState(initial)
  const [page, setPage] = useState<InvestigationSourcePage | null>(null)
  const [error, setError] = useState(false)
  const panel = useRef<HTMLElement>(null)
  useEffect(() => {panel.current?.focus()}, [])
  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => {
      if (cancelled) return
      setPage(null); setError(false)
      return readSource(request).then(value => {if (!cancelled) setPage(value)}, () => {if (!cancelled) setError(true)})
    })
    return () => {cancelled = true}
  }, [readSource, request])
  return <section className="fi-source-preview" aria-label="선택한 보존 원문" onKeyDown={event => {if (event.key === 'Escape') {event.stopPropagation(); onClose()}}} tabIndex={-1} ref={panel}>
    <header><strong><FileText size={14} aria-hidden="true"/>보존 원문</strong><button type="button" onClick={onClose} aria-label="보존 원문 닫기"><X size={16} aria-hidden="true"/></button></header>
    <h4>{source ? displayTitle(source, source.label || '선택한 보존 원문') : '선택한 보존 원문'}</h4>{source?.display_summary && <p>{source.display_summary}</p>}{source?.display_origin === 'model_summary' && <small className="fi-summary-origin">모델이 정리한 표시용 요약 · 판단 근거는 보존 원문에서 확인</small>}<details className="fi-source-identity"><summary>출처 식별자와 버전</summary><code>{request.source.id}</code><small>버전 · {request.source.version}</small>{source?.label && <p>{source.label}</p>}</details>
    <details className="fi-technical"><summary>선택한 원문 위치</summary><p>{request.pointer || '보존된 도구 반환'} · 오프셋 {request.offset ?? 0}</p></details>
    {error && <p className="fi-notice" role="alert">이 출처와 버전의 원문을 열지 못했어요. 목록을 새로고침한 뒤 근거를 다시 선택해 주세요.</p>}
    {!page && !error && <p className="fi-caption" role="status">선택한 구간을 불러오고 있어요.</p>}
    {page && <><p className="fi-caption">보존된 반환 기록의 일부입니다. 원본 파일 전체를 뜻하지 않아요.</p>{page.byte_start !== undefined && page.byte_end !== undefined && <p className="fi-caption">보존 필드 UTF-8 바이트 {page.byte_start}–{page.byte_end}{page.total_bytes !== undefined ? ` / 전체 ${page.total_bytes}` : ''} · 원본 이미지의 바이트 위치와 다릅니다.</p>}<pre>{page.text ?? JSON.stringify(page.value ?? page.items, null, 2)}</pre>
      {Array.isArray(page.items) && <div className="fi-source-fields">{page.items.map(item => <button type="button" key={item.pointer} onClick={() => setRequest({...request, pointer: item.pointer, offset: 0})}>{item.key || item.pointer}<ChevronRight size={12} aria-hidden="true"/></button>)}</div>}
      {(page.retained_coverage || page.acquisition_context) && <details className="fi-source-scope"><summary>보존된 반환 범위와 수집 맥락</summary><p className="fi-caption">반환된 범위와 제약을 함께 확인하세요. 검사하지 않은 범위의 부재를 뜻하지 않습니다.</p><pre>{JSON.stringify({retained_coverage: page.retained_coverage, acquisition_context: page.acquisition_context}, null, 2)}</pre></details>}
      <div className="fi-source-paging">{Boolean(request.offset) && <button type="button" onClick={() => setRequest({...request, offset: 0})}><ArrowLeft size={12} aria-hidden="true"/>처음 구간</button>}{page.next_offset != null && <button type="button" onClick={() => setRequest({...request, offset: page.next_offset!})}>다음 구간<ChevronRight size={12} aria-hidden="true"/></button>}</div>
    </>}
  </section>
}

function TimeDetail({row, observation, onSource, onSelect, selected}: {row: InvestigationTime; observation?: InvestigationObservation; onSelect?: (row: InvestigationTime) => void; selected?: boolean; onSource: (reference: InvestigationRef, observation?: InvestigationObservation, trigger?: HTMLButtonElement) => void}) {
  const context = timeContext(row)
  const candidates = context.temporal_candidates ?? context.candidates ?? []
  const modeled = context.observed_or_inferred === 'inferred' || ['interval', 'candidates'].includes(row.shape)
  const timeLabel = candidates.length ? `시간 후보 ${candidates.length}개` : modeled ? `${context.earliest ? displayTime(context.earliest) : '하한 미상'} ~ ${context.latest ? displayTime(context.latest) : '상한 미상'}` : (row.local_values?.[0] || row.normalized_values[0] || row.raw_values[0] || '시각 미확인')
  const kind = row.time_kind === 'file_metadata' ? `파일 메타데이터 · ${row.file_time_type || '필드 미확인'}` : row.time_kind === 'record' ? '기록에 적힌 시각' : row.time_kind === 'event' ? '사건 시각의 해석' : '시각 의미 미확인'
  return <details className={`fi-time-event${selected ? ' is-selected' : ''}`} data-shape={modeled ? 'interval' : 'point'} data-recheck={context.temporal_requires_recheck || undefined}>
    <summary onClick={() => onSelect?.(row)}><span className="fi-time-marker" aria-hidden="true"/><span className="fi-time-heading"><small>{kind}{context.timestamp_role ? ` · ${TIME_ROLE[context.timestamp_role] || context.timestamp_role}` : ''}{context.observed_or_inferred === 'inferred' ? ' · 추정' : ''}</small><strong>{displayTime(timeLabel)}</strong><span>{displayTitle(row, row.explanation)}</span>{row.display_summary && <span className="fi-caption">{row.display_summary}</span>}{context.temporal_requires_recheck && <em>시간 관계 재검토 필요</em>}</span><ChevronRight size={14} className="forsic-disclosure" aria-hidden="true"/></summary>
    <div className="fi-time-body">{row.display_origin === 'model_summary' && <small className="fi-summary-origin">모델이 정리한 표시용 요약</small>}
      {(context.coordinate_basis === 'source_local_calendar' || row.coordinate_basis === 'source_local_calendar') && <p className="fi-limitation">출처 내부 달력 좌표입니다. 시간대는 미상이며 다른 출처의 시계와 직접 비교하지 않습니다.</p>}
      {(context.precision || row.precision) === 'day' && <p className="fi-caption">날짜 단위 기록입니다. 시·분·초는 확인되지 않았어요.</p>}
      {row.local_values?.length ? <p><b>출처 내부 시각</b>{row.local_values.map(displayTime).join(' / ')}</p> : null}
      {row.raw_values.length > 0 && <p><b>원문 시각</b>{row.raw_values.join(' / ')}</p>}
      {row.normalized_values.length > 0 && <p><b>정규화된 시각</b>{row.normalized_values.map(displayTime).join(' / ')}</p>}
      {candidates.map((candidate, index) => <p key={index}><b>후보 {index + 1}</b>{candidate.earliest || '하한 미상'} ~ {candidate.latest || '상한 미상'}<br/>{candidate.basis}</p>)}
      <p><b>시간대 근거</b>{context.timezone_basis || row.timezone_basis || '확인되지 않음'}</p>
      <p><b>연도 근거</b>{context.year_basis || row.year_basis || '확인되지 않음'}</p>
      {context.clock_domain && <p><b>시계 기준</b>{context.clock_domain}</p>}
      {context.confidence_basis && <p><b>해석 근거</b>{context.confidence_basis}</p>}
      {context.assumptions?.map((item, index) => <p key={`a-${index}`}><b>가정</b>{item}</p>)}
      {[row.limitation, ...(context.temporal_limitations ?? context.limitations ?? [])].filter(Boolean).map((item, index) => <p className="fi-limitation" key={`l-${index}`}>{item}</p>)}
      {row.time_kind === 'file_metadata' && <p className="fi-limitation">파일 시각은 사건 발생·실행 시각과 다릅니다. ctime의 의미는 파일시스템과 파서에 따라 달라요.</p>}
      {observation ? <button type="button" className="fi-reference" onClick={event => onSource(observation.source_ref, observation, event.currentTarget)}><Link2 size={12} aria-hidden="true"/>연결된 원문<ChevronRight size={12} aria-hidden="true"/></button> : <p className="fi-caption">이 목록에 연결된 원문 관측의 상세가 없습니다.</p>}
      <details className="fi-technical"><summary>시간 기록 식별 정보</summary><code className="fi-record-id">{row.id} · {row.version}{sessionOf(row) ? ` · 세션 ${sessionOf(row)}` : ''}</code></details>
    </div>
  </details>
}

function OriginLink({sessionId, data}: {sessionId?: string; data: MonitorView}) {
  if (!sessionId || !data.sessions?.some(session => session.id === sessionId && session.available === true)) return null
  const query = new URLSearchParams({case: data.case_id, resume: sessionId})
  if (data.profile) query.set('profile', data.profile)
  return <a className="fi-origin-link" href={`/chat?${query.toString()}`}>출처 대화 열기 · {readableSession(sessionId, data)}<ChevronRight size={12} aria-hidden="true"/></a>
}

export function ForsicInvestigation({data: originalData, loading, error, readSource, onReviewTabChange}: Props) {
  const [sessionId, setSessionId] = useState('')
  const [summaryKey, setSummaryKey] = useState('')
  const [view, setView] = useState<'time' | 'map' | 'questions'>('time')
  const reviewTabSelected = view === 'questions' && Boolean(originalData)
  useEffect(() => {
    onReviewTabChange?.(reviewTabSelected)
    return () => onReviewTabChange?.(false)
  }, [onReviewTabChange, reviewTabSelected])
  const [visibleKeys, setVisibleKeys] = useState<string[] | null>(null)
  const [originSession, setOriginSession] = useState<string | undefined>(undefined)
  const [selectedTime, setSelectedTime] = useState<InvestigationTime | null>(null)
  const fingerprint = originalData ? JSON.stringify([originalData.case_id, originalData.session_id, originalData.profile, originalData.review_questions, originalData.counts, originalData.omitted_counts, ...[originalData.sources, originalData.observations, originalData.questions, originalData.timeline].map(rows => rows.map(recordKey))]) : ''
  const currentFingerprint = useRef(fingerprint)
  useLayoutEffect(() => {currentFingerprint.current = fingerprint; return () => {currentFingerprint.current = ''}}, [fingerprint])
  const [enrichment, setEnrichment] = useState<{fingerprint: string; pending: boolean; message: string; data?: InvestigationView} | null>(null)
  const currentEnrichment = enrichment?.fingerprint === fingerprint ? enrichment : null
  const monitor = (currentEnrichment?.data ?? originalData) as MonitorView | null
  const enrich = async () => {
    if (!originalData?.session_id || originalData.profile === undefined || currentEnrichment?.pending) return
    setEnrichment({fingerprint, pending: true, message: '보존된 근거를 바탕으로 표시용 요약을 정리하고 있어요.'})
    try {
      const result = await fetchJSON<{status: string; message: string; view?: InvestigationView}>('/api/plugins/forsic/investigation-enrich', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session_id: originalData.session_id, profile: originalData.profile})})
      if (currentFingerprint.current !== fingerprint) return
      const view = result.view
      const valid = view?.case_id === originalData.case_id && (view?.session_id === undefined || view.session_id === originalData.session_id) && (view?.profile === undefined || view.profile === originalData.profile) && Array.isArray(view?.questions) && Array.isArray(view?.observations) && Array.isArray(view?.sources) && Array.isArray(view?.timeline)
      setEnrichment({fingerprint, pending: false, message: result.message || (['ready', 'cached'].includes(result.status) ? '표시용 요약을 정리했어요.' : '요약 정리를 완료하지 못했어요.'), data: valid && ['ready', 'cached'].includes(result.status) ? view : undefined})
    } catch {
      if (currentFingerprint.current === fingerprint) setEnrichment({fingerprint, pending: false, message: '요약을 정리하지 못했어요. 다시 시도할 수 있어요.'})
    }
  }
  const data = monitor ? filterSession(monitor, sessionId) : null
  const summary = data?.questions.find(question => recordKey(question) === summaryKey) || data?.questions[0]
  const timeline = data?.timeline.filter(row => !visibleKeys || visibleKeys.includes(recordKey(row)) || !timelineMark(row)) ?? []

  const [selection, setSelection] = useState<InvestigationSourceRequest | null>(null)
  const trigger = useRef<HTMLElement | SVGElement | null>(null)
  const openSource = (source: InvestigationRef, observation?: InvestigationObservation, button?: HTMLButtonElement) => {
    trigger.current = button ?? document.activeElement as HTMLElement | SVGElement | null
    setOriginSession(observation ? sessionOf(observation) : monitor?.sources.find(item => item.id === source.id && item.version === source.version)?.session_id)
    setSelection({source, pointer: observation?.field_pointer || '', offset: observation?.byte_start ?? 0})
  }
  const closeSource = () => {setSelection(null); trigger.current?.focus()}
  const selectTime = (row: InvestigationTime) => {
    setSelectedTime(row)
    const observation = data?.observations.find(item => row.observation_ref?.kind === 'observation' && item.id === row.observation_ref.id && item.version === row.observation_ref.version)
    if (observation) openSource(observation.source_ref, observation)
    else setSelection(null)
  }
  return <div className={`forsic-investigation-panel${view !== 'questions' ? ' fi-human-panel' : ''}`} aria-busy={loading && !data}>
    {error && <p className="fi-notice" role="status">조사 기록을 불러오지 못했어요. 새로고침으로 다시 확인해 주세요.{data ? ' 아래는 마지막으로 불러온 기록입니다.' : ''}</p>}
    {!data && !error && <div className="fi-empty"><Clock3 size={24} aria-hidden="true"/><p>{loading ? '단서와 시간 기록을 불러오고 있어요.' : '대화에 연결된 기록을 기다리고 있어요.'}</p></div>}
    {data && monitor && <>
      <header className="fi-case-header"><div><h3>사건 기록</h3>{view === 'questions' ? <p>{monitor.case_id}</p> : <details className="fi-technical"><summary>사건 식별 정보</summary><code>{monitor.case_id}</code></details>}</div><div className="fi-header-actions"><span className="fi-live-label">{loading ? '기록 갱신 중' : '저장된 기록'}</span><button type="button" className="fi-reference" disabled={!originalData?.session_id || originalData.profile === undefined || currentEnrichment?.pending} onClick={() => void enrich()}>{currentEnrichment?.pending ? '요약 정리 중…' : '요약 정리'}</button></div></header>{currentEnrichment && <p className="fi-caption" role="status">{currentEnrichment.message}</p>}
      <div className="fi-session-filter"><label htmlFor="fi-session-scope">기록 범위</label><select id="fi-session-scope" aria-label="조사 세션 필터" value={sessionId} onChange={event => {setSessionId(event.target.value); setSelectedTime(null); setSelection(null); setVisibleKeys(null)}}><option value="">사건 전체 · 모든 세션</option>{(monitor.sessions ?? []).map((session,index) => <option key={session.id} value={session.id}>{session.label || (view === 'questions' ? session.id : `대화 ${index+1} · 제목 미기록`)}</option>)}</select></div>
      <div className="fi-overview" aria-label="연결된 전체 기록 수" aria-live="polite" aria-atomic="true"><span><strong>{monitor.counts?.main_questions ?? monitor.counts?.questions ?? monitor.questions.length}</strong>핵심 질문</span><span><strong>{monitor.counts?.review_questions ?? monitor.review_questions?.length ?? 0}</strong>독립 검토 질문<small> · 사건 전체</small></span><span><strong>{monitor.counts?.observations ?? monitor.observations.length}</strong>관측</span><span><strong>{monitor.counts?.timeline ?? monitor.timeline.length}</strong>시간 기록</span></div>
      {data.partial && <p className="fi-notice">일부 기록을 표시하고 있어요.{data.omitted_counts && Object.entries(data.omitted_counts).filter(([, count]) => count > 0).map(([kind, count]) => ` ${{questions: '질문', sources: '출처', observations: '관측', timeline: '시간 기록', unavailable_references: '확인할 수 없는 근거 연결'}[kind] || kind} ${count}건 생략.`).join('')}</p>}
      <nav className="fi-view-tabs" aria-label="조사 모니터 보기"><button type="button" aria-pressed={view === 'time'} onClick={() => setView('time')}><Clock3 size={14}/>시간 흐름</button><button type="button" aria-pressed={view === 'map'} onClick={() => setView('map')}><Network size={14}/>연결 지도</button><button type="button" aria-pressed={view === 'questions'} onClick={() => {setView('questions'); setSelection(null)}}><ListChecks size={14}/>렌 · 독립 검토</button></nav>
      {view === 'time' && summary && <section className="fi-case-summary" aria-label="사건의 현재 답"><div className="fi-summary-heading"><h3 className="fi-readable-heading">현재 판단</h3><span>{WORK[summary.work_state] || summary.work_state} · {ASSESSMENT[summary.assessment] || summary.assessment}</span></div>{data.questions.length > 1 ? <select aria-label="현재 답의 기준 질문" value={recordKey(summary)} onChange={event => setSummaryKey(event.target.value)}>{data.questions.map(question => <option key={recordKey(question)} value={recordKey(question)}>{question.question}</option>)}</select> : <h4>{summary.question}</h4>}<QuestionJudgment question={summary}/><small className="fi-origin">기록 출처 · {readableSession(sessionOf(summary), monitor)}</small> <OriginLink sessionId={sessionOf(summary)} data={monitor}/></section>}
      {view === 'time' && <details className="fi-optional-chart"><summary>시간축으로 비교하기 <span>확대·범위 선택</span></summary><ForsicTimeline rows={data.timeline} selectedKey={selectedTime ? recordKey(selectedTime) : undefined} onSelect={selectTime} onVisibleRows={setVisibleKeys}/></details>}
      {view === 'map' && <ForsicRelationMap data={data} onSource={openSource}/>}
      {selectedTime && view === 'time' && <section className="fi-selected-clue" aria-label="선택한 시간 단서"><header><strong>선택한 단서</strong><button type="button" aria-label="시간 단서 선택 해제" onClick={() => {setSelectedTime(null); setSelection(null)}}><X size={14}/></button></header><h4>{displayTitle(selectedTime, selectedTime.explanation)}</h4><p>{displaySummary(selectedTime, selectedTime.explanation)}</p>{selectedTime.display_origin === 'model_summary' && <small className="fi-summary-origin">모델이 정리한 표시용 요약</small>}<small>기록 출처 · {readableSession(sessionOf(selectedTime), monitor)}</small><OriginLink sessionId={sessionOf(selectedTime)} data={monitor}/>{data.observations.filter(item => selectedTime.observation_ref?.kind === 'observation' && item.id === selectedTime.observation_ref.id && item.version === selectedTime.observation_ref.version).map(observation => <blockquote key={recordKey(observation)}>{observation.literal}<small>{observation.interpretation_limit}</small></blockquote>)}{data.questions.filter(question => question.id === selectedTime.question_id).map(question => <div key={recordKey(question)}><h4>{question.question}</h4><QuestionJudgment question={question}/></div>)}</section>}
      <p className="fi-caption">{selection && <>기록 출처 · {readableSession(originSession, monitor)} <OriginLink sessionId={originSession} data={monitor}/></>}</p>
      {selection && <SourcePreview key={`${selection.source.id}@${selection.source.version}:${selection.pointer}`} request={selection} source={monitor.sources.find(source => source.id === selection.source.id && source.version === selection.source.version)} readSource={readSource} onClose={closeSource}/>}
      {view === 'time' && <section className="fi-section" aria-label="단서의 시간 흐름"><h3><Clock3 size={15} aria-hidden="true"/>시간 흐름</h3><p className="fi-caption">선택한 범위와 레인의 기록을 표시합니다. 시계 기준이 있는 기록과 파일 필드를 구분하며, 시간 미상은 축 밖에 보존합니다.</p>
        {!data.timeline.length && <div className="fi-empty"><p>아직 연결된 시간 기록이 없어요.</p><small>{data.timeline_empty_reason || '저장된 시간 해석이나 정확한 원문 시각 연결이 없습니다. 자료에 시각이 전혀 없다는 뜻은 아니에요.'}</small></div>}
        {monitorTimeGroups(timeline).map(group => <section className="fi-time-group" key={group.key} data-kind={group.kind} aria-label={group.label}><h4>{group.label}</h4>{group.kind === 'metadata' && <p className="fi-caption">ctime·mtime 등 파일 필드의 시각입니다. 같은 시계 기준 안에서 필드 시각을 비교할 수 있지만 사건 발생이나 실행을 입증하지 않습니다.</p>}{group.kind !== 'comparable' && group.kind !== 'metadata' && <p className="fi-caption">아래 항목의 배치 순서는 사건의 발생 순서를 뜻하지 않아요.</p>}
          {group.rows.map(row => <TimeDetail key={`${row.id}@${row.version}`} row={row} selected={selectedTime ? recordKey(selectedTime) === recordKey(row) : false} onSelect={selectTime} observation={data.observations.find(item => row.observation_ref?.kind === 'observation' && item.id === row.observation_ref.id && item.version === row.observation_ref.version)} onSource={openSource}/>)}
        </section>)}
      </section>}
      {view !== 'questions' && <section className="fi-section" aria-label="저장된 질문과 답"><h3>핵심 질문과 현재 답</h3>
        {!data.questions.length && <p className="fi-caption">아직 저장된 질문과 답이 없어요.</p>}
        {data.questions.map(question => <details className="fi-question" key={`${question.id}@${question.version}`}><summary><span><small>{WORK[question.work_state] || question.work_state} · {ASSESSMENT[question.assessment] || question.assessment} · {readableSession(sessionOf(question), monitor)}</small><strong>{question.question}</strong></span><ChevronRight size={14} className="forsic-disclosure" aria-hidden="true"/></summary><div><QuestionJudgment question={question}/><div className="fi-source-links">{questionSources(question, data.sources).map(reference => {const id = reference.id; const source = reference.kind === 'source' ? data.sources.find(item => item.id === id && item.version === reference.version) : undefined; return source ? <button type="button" className="fi-reference" key={`${id}@${reference.version}`} title={`${source.id} · ${source.version}`} onClick={event => openSource({kind: 'source', id: source.id, version: source.version}, undefined, event.currentTarget)}><Link2 size={12} aria-hidden="true"/>{displayTitle(source, source.label || '보존된 출처')}</button> : <details className="fi-technical" key={`${id}@${reference.version}`}><summary>상세를 표시할 수 없는 근거</summary><code>{id} · {reference.version || '버전 미확인'}</code></details>})}</div><details className="fi-technical"><summary>판단 기록 식별 정보</summary><code className="fi-record-id">{question.id} · {question.version}</code></details></div></details>)}
      </section>}
      {view === 'questions' && <section className="fi-section fi-review-section" aria-label="독립 검토 질문">
        <h3>렌의 독립 검토</h3><p className="fi-caption">렌이 남긴 질문과 포식이의 답변·처리 상태입니다.</p>
        {data.review_question_coverage && <p className="fi-caption">{typeof data.review_question_coverage.current_goal_questions === 'number' && <>현재 조사 {data.review_question_coverage.current_goal_questions}건</>}{typeof data.review_question_coverage.historical_goal_questions === 'number' && <> · 이전 조사 {data.review_question_coverage.historical_goal_questions}건</>}</p>}
        {data.review_questions?.length ? data.review_questions.map(question => <article className="fi-review-question" key={`${question.review_id || question.session_id || ''}:${question.id}`}>
          <div className="fi-review-prompt"><small>렌 · 검토 질문{question.current_goal !== undefined ? question.current_goal ? ' · 현재 조사' : ' · 이전 조사' : ''}</small><h4>{question.question}</h4></div>
          <div className="fi-review-response"><small>포식이 · 답변과 처리 상태</small>{question.response && question.disposition_validation === 'validated_saved_response' ? <><p className="fi-review-status">{REVIEW_DISPOSITION[question.response.disposition] || question.response.disposition}</p><p>{question.response.reason}</p>{question.response.limitation && <p className="fi-caption">남은 한계 · {question.response.limitation}</p>}{question.response.citation_refs.length > 0 && <p className="fi-caption">근거 참조 · {question.response.citation_refs.join(', ')}</p>}</> : <><p className="fi-review-status">답변 미기록</p><p className="fi-caption">{question.disposition_validation === 'unverified_or_not_recorded' ? '저장 기록과 연결이 확인된 답변이 없어요.' : `처리 상태 · ${REVIEW_DISPOSITION[question.disposition || 'not_recorded'] || question.disposition} · 미검증`}</p></>}</div>
          <details className="fi-review-metadata"><summary>검토 기록 상세<ChevronRight size={14} className="forsic-disclosure" aria-hidden="true"/></summary>{question.currentness === 'not_revalidated' && <p className="fi-caption">보존된 검토 조언입니다. 현재 조사 상태에 대한 재검증은 확인되지 않았어요.</p>}<p>인용 검증 · {QUOTE_VALIDATION[question.quote_validation || ''] || question.quote_validation || '검증 상태 미기록'}</p><code className="fi-record-id">{question.id}</code></details>
        </article>) : <p className="fi-caption">저장된 렌의 검토 질문이 없어요.</p>}
      </section>}
      {view !== 'questions' && <section className="fi-section" aria-label="출처에 연결된 관측"><h3><Link2 size={15} aria-hidden="true"/>연결된 관측</h3>
        {!data.observations.length && <p className="fi-caption">아직 원문 위치에 연결된 관측이 없어요.</p>}
        {data.observations.filter(observation => !visibleKeys || view !== 'time' || timeline.some(row => row.observation_ref?.id === observation.id && row.observation_ref.version === observation.version)).map(observation => <details className="fi-observation" key={`${observation.id}@${observation.version}`}><summary><span><strong>{displayTitle(observation, observation.statement)}</strong>{observation.display_summary && <span className="fi-observation-summary">{observation.display_summary}</span>}<small className="fi-origin">{readableSession(sessionOf(observation), monitor)}</small></span><ChevronRight size={14} className="forsic-disclosure" aria-hidden="true"/></summary><div><OriginLink sessionId={sessionOf(observation)} data={monitor}/>{observation.display_origin === 'model_summary' && <small className="fi-summary-origin">모델이 정리한 표시용 요약</small>}{observation.display_title && <p><b>저장된 관측</b> {observation.statement}</p>}<blockquote>{observation.literal}</blockquote><p>{observation.interpretation_limit}</p><details className="fi-technical"><summary>관측의 식별 정보와 정확한 위치</summary><code className="fi-record-id">{observation.id} · {observation.version}<br/>{observation.field_pointer} · {observation.byte_start !== undefined && observation.byte_end !== undefined ? `보존 필드 UTF-8 바이트 ${observation.byte_start}–${observation.byte_end}` : '보존된 필드 값 · 바이트 위치 미제공'}</code></details><button type="button" className="fi-reference" onClick={event => openSource(observation.source_ref, observation, event.currentTarget)}><FileText size={12} aria-hidden="true"/>보존 원문 보기<ChevronRight size={12} aria-hidden="true"/></button></div></details>)}
      </section>}
    </>}
  </div>
}
