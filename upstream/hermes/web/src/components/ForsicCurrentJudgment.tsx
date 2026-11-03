import {useRef, useState} from 'react'
import {Link2} from 'lucide-react'
import type {InvestigationSourcePage, InvestigationSourceRequest, InvestigationView, InvestigationRef} from '@/lib/forsic-investigation'
import {QuestionJudgment} from './ForsicRelationMap'
import {ForsicInvestigation, SourcePreview} from './ForsicInvestigation'
import {ForsicMissionFocus} from './ForsicMissionFocus'
import {displayTitle, exactQuestionRefs, recordKey, sameRef} from './forsic-monitor'
import './forsic-current-judgment.css'

interface Props {
  data: InvestigationView | null
  loading: boolean
  error: string | null
  lastCheckedAt: string | null
  readSource: (request: InvestigationSourceRequest) => Promise<InvestigationSourcePage>
}
const assessment: Record<string, string> = {supported: '지지됨', refuted: '반박됨', conflicting: '상충', undetermined: '판단 유보', not_recorded: '평가 미기록'}
const savedTime = (value?: number | null) => value && Number.isFinite(value) ? `${new Date(value * 1000).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'})} KST` : '시각 미기록'

/** A read-only presentation of the existing note-owned answer, never a new judgment. */
export function ForsicCurrentJudgment({data, loading, error, lastCheckedAt, readSource}: Props) {
  const [questionId, setQuestionId] = useState('')
  const [selection, setSelection] = useState<{questionKey: string; request: InvestigationSourceRequest} | null>(null)
  const trigger = useRef<HTMLElement | null>(null)
  const question = data?.questions.find(item => item.id === questionId) || data?.questions[0]
  const references = question && data ? exactQuestionRefs(question, data) : []
  const selected = question && selection?.questionKey === recordKey(question) ? selection.request : null
  const openSource = (reference: InvestigationRef) => {
    if (!data || !question) return
    const observation = reference.kind === 'observation' ? data.observations.find(item => sameRef(reference, {kind: 'observation', ...item})) : undefined
    const source = observation?.source_ref ?? reference
    if (source.kind !== 'source' || !source.version) return
    trigger.current = document.activeElement as HTMLElement | null
    setSelection({questionKey: recordKey(question), request: {source, pointer: observation?.field_pointer || '', offset: observation?.byte_start ?? 0}})
  }
  const checkpoint = data?.judgment_checkpoint
  return <div className="forsic-investigation-panel fcj-panel" aria-label="저장된 현재 판단" aria-busy={loading}>
    <p className="fi-caption">저장된 판단·근거·다음 확인과 조사 진행을 함께 확인합니다.</p>
    {error && <p className="fi-notice" role="alert">현재 판단을 다시 확인하지 못했어요.{data ? ' 아래는 마지막으로 불러온 기록이며 최신 여부를 확인할 수 없습니다.' : ' 상태 다시 조회로 확인해 주세요.'}</p>}
    {!data && !error && <div className="fi-empty" role="status"><p>{loading ? '저장된 판단을 불러오고 있어요.' : '이 대화에 연결된 판단 기록을 기다리고 있어요.'}</p></div>}
    {data && <>
      {checkpoint?.status === 'pending' && <section className="fi-notice" aria-label="새 설명과 저장된 판단" role="status"><strong>새 설명 이후 판단 반영 여부 확인 필요</strong><p>마지막 판단 저장 이후 조사 설명 {checkpoint.pending_briefings}회가 있었습니다. 아래는 저장된 판단이며, 진행 안내만 있는 경우에는 수정이 필요하지 않습니다. 새 설명은 최종 결론을 뜻하지 않습니다.</p><small>최근 설명 · {savedTime(checkpoint.latest_briefing_at)}<br/>최근 판단 저장 · {savedTime(checkpoint.latest_saved_at)}</small></section>}
      {checkpoint?.status === 'unavailable' && <p className="fi-notice" role="status">새 설명이 저장된 판단에 반영되었는지 확인할 수 없어요.</p>}
      {!data.questions.length && <div className="fi-empty"><p>아직 저장된 질문과 답이 없어요.</p><small>대화 중 설명만으로 판단이 저장되었다고 표시하지 않습니다.</small></div>}
      {data.partial && <p className="fi-notice">일부 기록을 표시하고 있어요.{data.omitted_counts?.questions ? ` 질문 ${data.omitted_counts.questions}건이 생략되었습니다.` : ''}</p>}
      {question && <>
        {data.questions.length > 1 && <label className="fi-question-picker">살펴볼 질문<select aria-label="현재 판단 기준 질문" value={question.id} onChange={event => {setQuestionId(event.target.value); setSelection(null)}}>{data.questions.map(item => <option key={recordKey(item)} value={item.id}>{item.question}</option>)}</select></label>}
        <article className="fcj-card" aria-label="선택한 질문의 판단">
          <header><span className="fcj-eyebrow">{question.answer_status === 'not_saved' ? '답 미저장' : question.answer_status === 'needs_recheck' ? '재검토 필요' : question.answer_status === 'recorded' ? '저장된 답' : '저장 상태 미확인'} · {assessment[question.assessment] || question.assessment}</span><h3>현재 조사 판단</h3><p className="fi-caption">마지막 저장 · {savedTime(question.updated_at)}<br/>판단 버전 · {question.version || '미기록'}</p></header>
          {question.answer_status === 'not_saved' ? <p className="fi-notice">이 질문의 답은 아직 저장되지 않았어요.</p> : <><QuestionJudgment question={question} showReferences={false}/>{question.answer_status === 'needs_recheck' && <p className="fi-notice" role="status">저장된 답을 다시 검토해야 해요. 확정된 최신 판단으로 해석하지 마세요.</p>}</>}
          <section className="fcj-section" aria-label="판단의 근거"><h4>판단의 근거</h4>{!references.length && <p className="fi-caption">정확한 출처와 버전이 연결된 근거가 없어요.</p>}<div className="fi-source-links">{references.map(reference => {
            const observation = reference.kind === 'observation' ? data.observations.find(item => sameRef(reference, {kind: 'observation', ...item})) : undefined
            const sourceRef = observation?.source_ref ?? reference
            const source = sourceRef.kind === 'source' ? data.sources.find(item => item.id === sourceRef.id && item.version === sourceRef.version) : undefined
            return source ? <button type="button" className="fi-reference" key={`${reference.kind}:${recordKey(reference)}`} onClick={() => openSource(reference)} title={`${source.id} · ${source.version}`}><Link2 size={13} aria-hidden="true"/>{displayTitle(observation || source, observation?.statement || source.label || '보존 원문')} · 원문 보기</button> : <p className="fi-caption" key={`${reference.kind}:${recordKey(reference)}`}>근거 상세를 표시할 수 없어요. <code>{reference.id} · {reference.version || '버전 미확인'}</code></p>
          })}</div></section>
          {selected && <SourcePreview key={`${recordKey(selected.source)}:${selected.pointer}`} request={selected} source={data.sources.find(item => item.id === selected.source.id && item.version === selected.source.version)} readSource={readSource} onClose={() => {setSelection(null); trigger.current?.focus()}}/>}
          <section className="fcj-section" aria-label="다음 확인"><h4>다음 확인</h4>{question.next_checks?.length ? <ul>{question.next_checks.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p className="fi-caption">다음 확인이 기록되지 않았어요.</p>}</section>
          <ForsicMissionFocus focus={data.task_focus} questionId={question.id}/>
          <details className="fcj-history"><summary>조사 질문과 수정 이유</summary><h4>조사 질문</h4><p>{question.question}</p><h4>수정 이유</h4><p>{question.correction_reason || '이번 판단의 수정 이유가 기록되지 않았어요.'}</p><p className="fi-caption">현재 저장된 버전을 표시합니다. 이전 판단의 전체 변경 이력은 이 보기에 제공되지 않습니다.</p><code>{question.id} · {question.version}</code></details>
        </article>
      </>}
      <details className="fcj-records"><summary>조사 기록 자세히 보기<span>시간 흐름 · 연결 지도 · 보존 원문</span></summary><ForsicInvestigation data={data} loading={loading} error={error} readSource={readSource}/></details>
      {lastCheckedAt && <p className="fi-caption fcj-checked">화면 확인 · {new Date(lastCheckedAt).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'})} KST{loading ? ' · 갱신 중' : ''}</p>}
    </>}
  </div>
}
