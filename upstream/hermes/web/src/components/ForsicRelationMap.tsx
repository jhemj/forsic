import {useState} from 'react'
import type {InvestigationObservation, InvestigationRef, InvestigationQuestion} from '@/lib/forsic-investigation'
import {displayTitle, displaySummary, exactQuestionRefs, recordKey, sameRef, sessionLabel, sessionOf, type MonitorView} from './forsic-monitor'

interface Props {data: MonitorView; onSource: (source: InvestigationRef, observation?: InvestigationObservation) => void}
const shorten = (value: string, max = 24) => value.length > max ? `${value.slice(0, max)}…` : value
export function QuestionJudgment({question}: {question: InvestigationQuestion}) {
  const explanation = question.reader_explanation
  const readable = explanation && ['current', 'provisional'].includes(explanation.status)
  const paragraphs = readable ? explanation.paragraphs.filter(paragraph => paragraph.text.trim() !== question.answer.trim()) : []
  const limits = [...new Set([...(question.limitations ?? []), ...(question.gaps ?? []), ...(explanation?.limitations ?? [])])]
  const objections = [...new Set([...(question.counterarguments ?? []), ...(explanation?.counterarguments ?? [])])]
  return <div className="fi-judgment fi-readable-judgment">
    <section className="fi-answer"><h4>현재 답</h4><p>{question.answer || '저장된 답이 없습니다.'}</p></section>
    {readable && paragraphs.length > 0 && <section className="fi-reader-explanation" aria-label="조사 판단 설명">
      <h4>{explanation.title || '판단을 이해하기 위한 설명'}</h4>
      {explanation.status === 'provisional' && <p className="fi-reader-status">잠정 설명 · 채택한 근거 판단이 아직 연결되지 않았습니다.</p>}
      {paragraphs.map((paragraph,index) => <div key={index}><p>{paragraph.text}</p>
        {(paragraph.claim_refs.length > 0 || paragraph.source_refs.length > 0 || paragraph.counterevidence_refs.length > 0) && <details className="fi-technical"><summary>이 설명에 연결된 판단과 근거</summary>
          {paragraph.claim_refs.map(ref => <code key={`claim-${ref.id}`}>판단 · {ref.id} · {ref.version}</code>)}
          {paragraph.source_refs.map(ref => <code key={`source-${ref.id}`}>근거 · {ref.kind} · {ref.id} · {ref.version}</code>)}
          {paragraph.counterevidence_refs.map(ref => <code key={`counter-${ref.id}`}>반대 근거 · {ref.kind} · {ref.id} · {ref.version}</code>)}
        </details>}
      </div>)}
    </section>}
    {explanation?.status === 'stale' && <p className="fi-reader-status" role="status">답·범위 또는 근거가 바뀌어 이전 설명의 갱신이 필요합니다. 위에는 현재 저장된 답을 표시합니다.</p>}
    <div className="fi-judgment-context">
      <section><h4>중요한 반론</h4>{objections.length ? <ul>{objections.map((item,index) => <li key={index}>{item}</li>)}</ul> : <p>별도로 저장된 반론이 없습니다.</p>}</section>
      <section><h4>남은 한계</h4>{limits.length ? <ul>{limits.map((item,index) => <li key={index}>{item}</li>)}</ul> : <p>별도로 저장된 한계가 없습니다. 원문과 관측의 확인 범위를 함께 확인하세요.</p>}</section>
    </div>
  </div>
}
export function ForsicRelationMap({data, onSource}: Props) {
  const [questionKey, setQuestionKey] = useState('')
  const [selected, setSelected] = useState('')
  const question = data.questions.find(item => recordKey(item) === questionKey) || data.questions[0]
  if (!question) return <div className="fi-empty"><p>연결 지도의 기준이 될 질문이 없어요.</p><small>저장된 질문의 정확한 근거 연결만 표시합니다.</small></div>
  const refs = exactQuestionRefs(question, data)
  const directObservations = data.observations.filter(observation => refs.some(ref => sameRef(ref, {kind: 'observation', ...observation})))
  const sourceRefs = [...refs.filter(ref => ref.kind === 'source'), ...directObservations.map(observation => observation.source_ref)].filter((ref, index, all) => all.findIndex(item => sameRef(item, ref)) === index)
  const sources = sourceRefs.slice(0, 8)
  const linked = data.observations.filter(observation => sources.some(ref => sameRef(ref, observation.source_ref)))
  const observations = linked.slice(0, 12)
  const selectedSource = data.sources.find(item => selected === `s:${recordKey(item)}`)
  const selectedObservation = data.observations.find(item => selected === `o:${recordKey(item)}`)
  const height = Math.max(220, Math.max(sources.length, observations.length) * 64 + 70)
  const sourceY = (index: number) => 58 + index * 64
  const observationY = (index: number) => 58 + index * 64
  return <div className="fi-map-workspace"><div className="fi-chart-toolbar"><div><span className="fi-eyebrow">판단과 근거</span><strong>질문을 중심으로 연결 보기</strong></div><span className="fi-map-count">출처 {sources.length} · 관측 {observations.length}</span></div><label className="fi-question-picker">기준 질문<select aria-label="연결 지도 기준 질문" value={recordKey(question)} onChange={event => {setQuestionKey(event.target.value); setSelected('')}}>{data.questions.map(item => <option key={recordKey(item)} value={recordKey(item)}>{item.question}</option>)}</select></label>
    <section aria-label="사건의 현재 답"><QuestionJudgment question={question}/></section>
    <p className="fi-caption">선은 저장된 근거 참조와 출처 연결입니다. 위치와 거리는 인과관계나 신뢰도를 뜻하지 않아요.</p>
    {sourceRefs.length > 0 && <section className="fi-evidence-chain" aria-label="판단에 연결된 근거"><h4>이 판단에 연결된 근거</h4>{sourceRefs.map((ref,index) => {
      const source = data.sources.find(item => sameRef(ref,{kind:'source',...item}))
      const evidence = data.observations.filter(item => sameRef(item.source_ref,ref))
      return <article className="fi-chain-card" key={`${ref.id}@${ref.version}`}><header><span className="fi-chain-number">{index+1}</span><h5>{source ? displayTitle(source,source.label || '보존된 출처') : '상세를 표시할 수 없는 출처'}</h5></header>
        {source && <>{source.display_summary && <p>{source.display_summary}</p>}<button type="button" className="fi-reference" onClick={() => onSource(ref)}>이 출처의 보존 원문 보기</button></>}
        {evidence.map(item => <details className="fi-chain-observation" key={recordKey(item)}><summary>{displayTitle(item,item.statement)}</summary>{item.display_summary && <p>{item.display_summary}</p>}<blockquote>{item.literal}</blockquote>{item.interpretation_limit && <p className="fi-limitation">해석의 한계 · {item.interpretation_limit}</p>}<button type="button" className="fi-reference" onClick={() => onSource(item.source_ref,item)}>이 관측의 보존 원문 보기</button></details>)}
        <details className="fi-technical"><summary>출처 식별 정보</summary><code>{ref.id} · {ref.version}</code>{source?.coverage && <p>{source.coverage}</p>}</details>
      </article>
    })}</section>}
    <details className="fi-optional-chart"><summary>연결 지도로 살펴보기 <span>출처와 관측의 관계</span></summary>
    {!sources.length ? <div className="fi-empty"><p>이 질문에 정확한 버전으로 연결된 출처가 없어요.</p><small>불명확한 참조를 추측해 연결하지 않습니다.</small></div> : <div className="fi-chart-scroll"><svg viewBox={`0 0 820 ${height}`} className="fi-relation-chart" role="group" aria-label="질문·출처·관측 연결 지도">
      <text className="fi-map-column" x={25} y={18}>질문</text><text className="fi-map-column" x={295} y={18}>채택한 출처 · 정확 버전</text><text className="fi-map-column" x={565} y={18}>출처에 연결된 관측</text>
      {sources.map((ref, index) => refs.some(item => sameRef(item, ref)) && <path key={`q-${recordKey(ref)}`} className="fi-map-edge" d={`M245,${height / 2} C270,${height / 2} 270,${sourceY(index)} 295,${sourceY(index)}`}/>)}
      {observations.map((observation, index) => directObservations.includes(observation) && <path key={`direct-${recordKey(observation)}`} className="fi-map-edge" d={`M245,${height / 2} C400,${height / 2} 410,${observationY(index)} 565,${observationY(index)}`}/>)}
      {observations.map((observation, index) => <path key={`o-${recordKey(observation)}`} className="fi-map-edge fi-map-observation-edge" d={`M515,${sourceY(sources.findIndex(ref => sameRef(ref, observation.source_ref)))} C540,${sourceY(sources.findIndex(ref => sameRef(ref, observation.source_ref)))} 540,${observationY(index)} 565,${observationY(index)}`}/>)}
      <g className="fi-map-question"><rect x={25} y={height / 2 - 25} width={220} height={50} rx={9}/><text x={39} y={height / 2 - 2}>{shorten(question.question, 22)}</text><text className="fi-map-node-meta" x={39} y={height / 2 + 15}>조사의 핵심 질문</text><title>{question.question}</title></g>
      {sources.map((ref, index) => {const source = data.sources.find(item => item.id === ref.id && item.version === ref.version); const key = `s:${recordKey(ref)}`; const choose = () => {setSelected(key); onSource(ref)}; return <g key={key} role="button" tabIndex={0} aria-label={`출처 선택 · ${source ? displayTitle(source, source.label || '보존된 출처') : '상세가 없는 출처'} · ${ref.version}`} aria-pressed={selected === key} className={`fi-map-node${selected === key ? ' is-selected' : ''}`} onClick={choose} onKeyDown={event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); choose()}}}><rect x={295} y={sourceY(index) - 24} width={220} height={48} rx={8}/><foreignObject x={309} y={sourceY(index) - 17} width={192} height={36}><div className="fi-map-node-copy"><strong>{source ? displayTitle(source, source.label || '보존된 출처') : '상세가 없는 출처'}</strong><small>{source ? displaySummary(source, source.coverage || '보존 원문에 연결됨') : '정확 버전 연결'}</small></div></foreignObject><title>{source ? displayTitle(source, source.label || '보존된 출처') : '상세가 없는 출처'} · {ref.version}</title></g>})}
      {observations.map((observation, index) => {const key = `o:${recordKey(observation)}`; const choose = () => {setSelected(key); onSource(observation.source_ref, observation)}; return <g key={key} role="button" tabIndex={0} aria-label={`관측 선택 · ${displayTitle(observation, observation.statement)}`} aria-pressed={selected === key} className={`fi-map-node fi-map-observation${selected === key ? ' is-selected' : ''}`} onClick={choose} onKeyDown={event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); choose()}}}><rect x={565} y={observationY(index) - 24} width={230} height={48} rx={8}/><foreignObject x={579} y={observationY(index) - 17} width={202} height={36}><div className="fi-map-node-copy"><strong>{displayTitle(observation, observation.statement)}</strong><small>{displaySummary(observation, observation.interpretation_limit)}</small></div></foreignObject><title>{observation.statement} · {observation.interpretation_limit}</title></g>})}
    </svg></div>}
    {(sourceRefs.length > sources.length || linked.length > observations.length) && <p className="fi-caption">집중 보기를 위해 출처 {sourceRefs.length - sources.length}건 · 관측 {linked.length - observations.length}건을 생략했어요. 기준 질문을 바꾸거나 아래 목록에서 확인하세요.</p>}
    {(selectedSource || selectedObservation) && <section className="fi-map-selection" aria-label="선택한 연결의 설명">{selectedObservation ? <><h4>{displayTitle(selectedObservation, selectedObservation.statement)}</h4><p>{displaySummary(selectedObservation, selectedObservation.statement)}</p>{selectedObservation.display_origin === 'model_summary' && <small className="fi-summary-origin">모델이 정리한 표시용 요약</small>}<blockquote>{selectedObservation.literal}</blockquote><p className="fi-limitation">{selectedObservation.interpretation_limit}</p><button type="button" className="fi-reference" onClick={() => onSource(selectedObservation.source_ref, selectedObservation)}>이 관측의 보존 원문 보기</button><details><summary>관측 식별자와 정확한 위치</summary><code>{selectedObservation.id} · {selectedObservation.version}<br/>{selectedObservation.source_ref.id} · {selectedObservation.source_ref.version}<br/>{selectedObservation.field_pointer} · {selectedObservation.byte_start !== undefined && selectedObservation.byte_end !== undefined ? `UTF-8 ${selectedObservation.byte_start}–${selectedObservation.byte_end}` : '보존된 필드 값 · 바이트 위치 미제공'}</code><p>{selectedObservation.statement}</p></details></> : selectedSource && <><h4>{displayTitle(selectedSource, selectedSource.label || '보존된 출처')}</h4><p>{displaySummary(selectedSource, selectedSource.coverage || '이 출처의 요약은 아직 없습니다. 보존 원문에서 확인하세요.')}</p>{selectedSource.display_origin === 'model_summary' && <small className="fi-summary-origin">모델이 정리한 표시용 요약</small>}<small>{sessionLabel(sessionOf(selectedSource), data)}</small><button type="button" className="fi-reference" onClick={() => onSource({kind: 'source', id: selectedSource.id, version: selectedSource.version})}>이 출처의 보존 원문 보기</button><details><summary>출처 식별자와 보존 범위</summary><code>{selectedSource.id} · {selectedSource.version}</code><p>{selectedSource.label}</p><p>{selectedSource.locator}</p><p>{selectedSource.coverage}</p></details></>}</section>}
    </details>
  </div>
}
