import type {ReviewHistory} from '@/lib/forsic-review-history'
import './forsic-investigation.css'
import './forsic-review-history.css'

const DISPOSITION: Record<string, string> = {investigated: '조사함', refuted: '반박함', scope_limited: '범위 한정', accepted: '반영', rejected: '채택하지 않음', deferred: '보류'}
interface Props {data: ReviewHistory | null; loading: boolean; error: string | null}
export function ForsicReviewHistory({data, loading, error}: Props) {
  return <section className="forsic-review-history fi-review-section" aria-label="렌의 독립 검토 기록" aria-busy={loading}>
    <header><h3>렌의 질문과 포식이의 답변</h3><p className="fi-caption">사건 전체의 보존된 검토와 답변입니다. 이전 목표와 보고서 버전의 기록도 함께 표시합니다.</p></header>
    {error && <p role="status" className="fi-notice">{error}{data ? ' 아래는 마지막으로 조회한 기록입니다.' : ''}</p>}
    {!data && !error && <p role="status">{loading ? '전체 검토 기록을 불러오고 있어요.' : '대화에 연결된 검토 기록을 기다리고 있어요.'}</p>}
    {data && <>
      <div className="fi-overview" aria-label="독립 검토 전체 기록 수"><span><strong>{data.counts.total}</strong>전체 질문</span><span><strong>{data.counts.answered}</strong>답변 연결</span><span><strong>{data.counts.pending}</strong>답변 대기</span><span><strong>{data.counts.historical_answer}</strong>과거 답변만 있음</span></div>
      <p className="fi-caption">이전 조사 질문 {data.counts.historical_goal}건 · 보존된 검토 {data.counts.receipts}회. 기록 조회는 검토를 다시 실행하지 않습니다.</p>
      {data.items.length === 0 && <p className="fi-caption">저장된 렌의 검토 질문이 없어요.</p>}
      {data.items.map((item, index) => <article className="fi-review-question" key={`${item.review_id}:${item.finding_id}`}>
        <div className="fi-review-prompt"><small>질문 {index + 1} · 렌{item.review_number ? ` · 검토 ${item.review_number}회` : ''} · {item.executive_round ? `경영진 질문 ${item.executive_round}차` : item.stage === 'final' ? '최종 검토' : '중간 검토'} · {item.current_goal ? '현재 조사' : '이전 조사'}{item.report_draft_revision ? ` · 보고서 초안 ${item.report_draft_revision}판` : ''}</small><h4>{item.question}</h4>{item.why && <p>{item.why}</p>}{item.next_check && <p className="fi-caption">확인 요청 · {item.next_check}</p>}</div>
        <div className="fi-review-response"><small>포식이 · 답변과 처리 상태</small>
          {item.responses.length === 0 && <><p className="fi-review-status">답변 대기 · 답변 미기록</p><p className="fi-caption">이 검토 의견에 정확히 연결된 저장 답변이 없어요.</p></>}
          {item.status === 'historical_answer' && <p className="fi-notice">과거 답변은 보존되어 있습니다. 현재 질문에 적용되는 답변은 확인되지 않았어요.</p>}
          {item.responses.map((answer, answerIndex) => <div className="frh-answer" key={`${answer.note_event_id}:${answer.question_id}:${answerIndex}`}>
            <p className="fi-review-status">{DISPOSITION[answer.disposition] || answer.disposition} · {answer.applicability === 'current_note' ? item.current_goal ? '현재 질문에 연결된 답변' : '이전 조사에 저장된 답변' : '과거 답변'}</p>
            <p>{answer.reason}</p>{answer.limitation && <p className="fi-caption">남은 한계 · {answer.limitation}</p>}
            {answer.validation !== 'validated_saved_response' && <p className="fi-notice">보존된 답변의 근거 연결은 현재 검증되지 않았어요.</p>}
            <details className="fi-technical"><summary>답변 기록과 근거</summary><code>{answer.question_id} · revision {answer.revision} · {answer.note_event_id}</code>{answer.citation_refs?.length ? <p>근거 참조 · {answer.citation_refs.join(', ')}</p> : null}</details>
          </div>)}
        </div>
        <details className="fi-review-metadata"><summary>검토 기록 상세</summary><p className="fi-caption">보존된 조언과 답변입니다. 현재 보고서의 재검증이나 의미상 승인을 뜻하지 않습니다.</p><code>{item.review_id}<br/>{item.finding_id}<br/>{item.goal_id}<br/>{item.session_id}</code>{item.report_version && <p>검토한 보고서 버전 · <code>{item.report_version}</code></p>}{item.report_question_ids?.length ? <p>검토 대상 질문 · {item.report_question_ids.join(', ')}</p> : null}</details>
      </article>)}
      {Boolean(data.unlinked_responses?.length) && <section aria-label="검토 연결을 확인하지 못한 답변"><h4>연결을 확인하지 못한 저장 답변</h4><p className="fi-caption">원래 대화의 연결을 확인하지 못해 검토 의견에 적용하지 않았습니다.</p>{data.unlinked_responses?.map((answer, index) => <article className="fi-review-question" key={`${answer.note_event_id}:${index}`}><div className="fi-review-response"><h4>{answer.question || '보존된 질문'}</h4><p>{answer.reason}</p>{answer.limitation && <p>{answer.limitation}</p>}<details><summary>답변 식별 정보</summary><code>{answer.review_id}<br/>{answer.finding_id}<br/>{answer.question_id}<br/>{answer.session_id}</code></details></div></article>)}</section>}
      {data.issues.length > 0 && <section aria-label="검토 기록 조회 한계"><h4>확인하지 못한 검토 기록 {data.issues.length}건</h4>{data.issues.map((issue, index) => <details key={`${issue.review_id ?? ''}:${index}`}><summary>{issue.question || issue.message}</summary><p>{issue.message}</p>{issue.reason && <p>{issue.reason}</p>}{issue.review_id && <code>{issue.review_id}</code>}</details>)}</section>}
    </>}
  </section>
}
