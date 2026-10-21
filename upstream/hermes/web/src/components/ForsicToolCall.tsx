import {useState} from 'react'
import {Check, X, LoaderCircle, CircleHelp, ChevronDown, Clock3, AlertTriangle, Search, Terminal} from 'lucide-react'
import type {InspectorToolCall} from '../lib/forsic-inspector'
import {shellCommandOutcome, toolDetail, toolStatusLabel} from '../lib/forsic-tool-detail'
import './forsic-tool-call.css'

function pretty(text: string) {try {return JSON.stringify(JSON.parse(text), null, 2)} catch {return text}}

export function ForsicToolCall({call}: {call: InspectorToolCall}) {
  const [rawOpen, setRawOpen] = useState(false)
  const detail = toolDetail(call), pending = call.status === 'running' && call.result === undefined
  const outcome = shellCommandOutcome(call)
  const Icon = outcome ? {neutral: outcome.status === 'no_matches' ? Search : Terminal, warning: AlertTriangle, error: X, unknown: CircleHelp}[outcome.tone]
    : {completed: Check, error: X, running: pending ? LoaderCircle : Clock3, unknown: CircleHelp}[call.status]
  const duration = typeof call.durationSeconds === 'number' && Number.isFinite(call.durationSeconds) && call.durationSeconds >= 0 ? `${call.durationSeconds.toFixed(1)}초` : null
  return <details className="forsic-call fc-tool-call" data-status={call.status} data-pending={pending} data-command-outcome={outcome?.status} data-outcome-tone={outcome?.tone}>
    <summary>
      <span className="fc-tool-symbol" aria-hidden="true"><Icon size={15}/></span>
      <span className="fc-tool-heading">
        <strong className={detail.purpose ? 'fc-tool-purpose' : undefined}>{detail.purpose || detail.label}</strong>
        {detail.purpose && <span className="fc-tool-kind">{detail.label}</span>}
        {detail.target && <span className="fc-tool-target" data-command={call.name === 'forsic_shell'}>{detail.target}</span>}
        {detail.summary && <span className="fc-tool-summary">{detail.summary}</span>}
      </span>
      <span className="fc-tool-meta"><span className="fc-tool-badge">{toolStatusLabel(call)}</span>{duration && <small>{duration}</small>}</span>
      <ChevronDown className="fc-tool-chevron" size={13} aria-hidden="true"/>
    </summary>
    <div className="fc-tool-body">
      {detail.description && <p>{detail.description}</p>}
      {outcome && <p className="fc-tool-caption">반환된 명령 상태이며 조사 결론이나 원본 증거의 직접 관측을 뜻하지 않습니다.</p>}
      {!outcome && call.status === 'completed' && <p className="fc-tool-caption">도구 실행 성공을 뜻하며, 조사 결론이나 전체 범위 확인을 뜻하지 않습니다.</p>}
      {call.status === 'running' && !pending && <p className="fc-tool-caption">별도 작업이 접수되었습니다. 완료 결과는 아직 확인되지 않았습니다.</p>}
      {detail.details.length > 0 && <dl>{detail.details.map((item, i) => <div key={`${item.label}:${i}`}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>}
      <details className="fc-tool-raw" onToggle={event => setRawOpen(event.currentTarget.open)}><summary>입력·결과 원문</summary>
        {rawOpen && <div><p><code>{call.name}</code> · {call.source === 'live' ? '실시간 기록' : '저장된 기록'}</p><h4>입력</h4><pre>{pretty(call.arguments || '(입력 기록 없음)')}</pre>
          {call.submittedRequest && <><h4>제출된 요청 · {call.submittedRequest.name}</h4><pre>{pretty(call.submittedRequest.arguments)}</pre></>}
          {(call.argumentsTruncated || call.submittedRequest?.argumentsTruncated) && <p>긴 입력의 앞부분만 표시합니다.</p>}
          <h4>반환 결과</h4><pre>{call.result === undefined ? pending ? '결과를 기다리고 있습니다.' : '반환 기록이 없습니다.' : pretty(call.result)}</pre>
        </div>}
      </details>
      {call.resultTruncated && <p className="fc-tool-caption">긴 결과의 앞부분만 표시합니다. 전체 반환 기록은 보존되어 있습니다.</p>}
    </div>
  </details>
}
