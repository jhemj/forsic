import {FileText} from 'lucide-react'
import type {InvestigationQuestion, InvestigationReport, InvestigationReportAuthoring, InvestigationReportWorkspace} from '@/lib/forsic-investigation'
import {QuestionJudgment} from './ForsicRelationMap'
import {ForsicReportLink} from './ForsicReportLink'
import './ForsicReports.css'

const layoutLabels: Record<string, string> = {not_rendered:'렌더링 미실시',unverified:'검토 미확인',complete:'검토 기록 있음',passed:'통과 기록 있음',failed:'수정 필요',required:'검토 필요'}
const reviewLabels: Record<string, string> = {complete:'검토 기록 있음',required:'검토 필요',pending:'검토 대기',needs_revision:'수정 필요',unavailable:'조회 불가'}
interface Props {reportCount?: number; omittedReportCount?: number;workspace?: InvestigationReportWorkspace; lastCheckedAt?: string | null; questions?: InvestigationQuestion[]; reports?: InvestigationReport[]; authoring?: InvestigationReportAuthoring; loading: boolean; error: string | null; hasCase: boolean; profile?: string}
function fileSlot(files: InvestigationReport['files'], index: number) {
  const extension = files[index].name.toLowerCase().split('.').at(-1)
  return `${extension}:${files.slice(0, index).filter(file => file.name.toLowerCase().split('.').at(-1) === extension).length}`
}
function fileLabel(name: string) {
  const lower = name.toLowerCase()
  return lower.endsWith('.html') ? '웹에서 읽기' : lower.endsWith('.docx') ? 'Word 내려받기' : lower.endsWith('.pdf') ? 'PDF 내려받기' : lower.endsWith('.zip') ? '보고서 근거 ZIP 내려받기' : '보고서 파일 내려받기'
}
function reportTime(value: string) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value.replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$1년 $2월 $3일')
  if (!/T.*(?:Z|[+-]\d{2}:?\d{2})$/.test(value)) return value.replace('T',' ')
  const parsed = new Date(value)
  return Number.isFinite(parsed.getTime()) ? new Intl.DateTimeFormat('ko-KR', {year:'numeric', month:'long', day:'numeric', hour:'2-digit', minute:'2-digit',second:'2-digit',timeZone:'Asia/Seoul'}).format(parsed) + ' KST' : value
}
function ReportAuthoring({authoring}: {authoring: InvestigationReportAuthoring}) {
  const unavailable = authoring.status === 'unavailable'
  const hasDraft = authoring.groups.some(group => group.state !== 'missing')
  const stateLabel = {missing:'미작성',in_progress:'작성 중',saved:'저장됨',unavailable:'미확인 사항 기재',recheck:'재확인 필요',ready:'검토됨'}
  return <section className="frr-authoring" aria-label="보고서 작성 진행">
    <header className="frr-intro"><h2>보고서 작성 진행</h2><p>본문 순서에 따라 저장된 작성·검토 기록을 보여줍니다. 작성 상태는 조사 결론의 확실성을 뜻하지 않습니다.</p></header>
    <p className="frr-authoring-status" role="status">{unavailable ? '현재 작성·검토 상태를 확인할 수 없습니다. 저장된 본문과 저장 시각은 아래에 보존합니다.' : authoring.status === 'ready' ? '본문과 근거를 대조한 작성 검토가 기록되었습니다.' : authoring.status === 'recheck' ? '판단이나 근거가 달라져 작성 내용의 재확인이 필요합니다.' : authoring.review_status === 'needs_revision' ? '저장된 검토에서 수정·재확인할 내용이 남아 있습니다.' : authoring.status === 'review' ? '본문이 저장되었습니다. 문서 전체 검토가 남아 있습니다.' : hasDraft ? '항목별 본문을 작성하고 있습니다.' : '조사 결과를 정리한 뒤 본문을 작성합니다.'}</p>
    <ol className="frr-authoring-groups">{authoring.groups.map(group => {
      const instant = group.saved_at !== null && Number.isFinite(group.saved_at) ? new Date(group.saved_at * 1000) : null
      const savedAt = instant && Number.isFinite(instant.getTime()) ? instant.toISOString() : null
      return <li className="frr-authoring-group" key={group.id} data-state={unavailable && group.preview.length === 0 ? 'unknown' : group.state}>
        <div className="frr-authoring-heading"><h3>{group.label}</h3><span className="frr-authoring-badge">{unavailable ? group.preview.length > 0 ? '저장 내용 있음 · 현재성 미확인' : '조회 불가' : stateLabel[group.state]}</span></div>
        {savedAt && <p className="frr-time">저장 <time dateTime={savedAt}>{reportTime(savedAt)}</time></p>}
        {group.state === 'recheck' && <p className="frr-authoring-caution">이전 저장 내용입니다. 현재 판단·근거와 다시 대조해야 합니다.</p>}
        {group.preview.length > 0 && <><p className="frr-authoring-preview">{group.preview[0].text}</p><details className="frr-authoring-details"><summary>저장된 작성 내용 펼치기</summary>{group.preview.map(entry => <div key={entry.node_id}><p>{entry.text}</p>{entry.status === 'unavailable' && <small>확인할 수 없는 범위로 기재됨{entry.unavailable_reason ? ': ' + entry.unavailable_reason : ''}</small>}{Boolean(entry.omitted_reason_characters) && <small>미확인 사유 {entry.omitted_reason_characters}자는 미리보기에서 생략했습니다.</small>}{entry.omitted_characters > 0 && <small>이 항목의 {entry.omitted_characters}자는 미리보기에서 생략했습니다.</small>}</div>)}{group.omitted_entries > 0 && <p className="frr-empty-file">추가 항목 {group.omitted_entries}개는 이 미리보기에서 생략했습니다.</p>}</details></>}
      </li>
    })}</ol>
  </section>
}
function ReportCard({report, profile}: {report: InvestigationReport; profile?: string}) {
  return <article className="frr-card">
      <header><FileText size={22} aria-hidden="true"/><div><h3>{report.title || '제목이 기록되지 않은 보고서'}</h3>{report.created_at ? <p className="frr-time"><time dateTime={report.created_at}>{reportTime(report.created_at)}</time></p> : <p className="frr-time">저장 시각 미확인</p>}</div></header>
      {(report.editorial_status === 'authored_current' || report.editorial_status === 'technical_partial') && <span className="frr-editorial-status" title="저장 시점의 작성 형식을 나타냅니다.">{report.editorial_status === 'authored_current' ? '독자용 보고서' : '중간 기록'}</span>}
      <p className="frr-time">{report.draft_revision !== undefined ? `초안 v${report.draft_revision}` : '초안 버전 미확인'}{report.narrative_revision !== undefined && ` · 본문 v${report.narrative_revision}`} · {{same:'최신 초안과 동일',older:'최신 초안보다 이전',different_scope:'초안과 범위가 다름',unknown:'초안과의 관계 미확인'}[report.draft_relation || 'unknown']}{report.lifecycle === 'previous_final' && ' · 이전 최종 선택본'}</p>
      <p className="frr-version-status" data-current={report.current === true ? 'true' : report.current === false ? 'false' : 'unknown'}>{report.current === true ? '현재 조사와 일치하는 저장본' : report.current === false ? '현재 조사와 달라진 저장본' : '현재 조사와의 일치 여부 미확인'}</p>
      {report.changes?.length ? <details className="frr-changes" aria-label="저장 후 달라진 판단"><summary>저장 후 달라진 내용</summary><ul>{report.changes.map(change => <li key={change.question_id}><strong>{change.title}</strong><span>{change.kinds.map(kind => ({answer:'현재 답 변경',evidence:'연결 근거 변경',limits:'한계·반론 변경',added:'새 질문',removed:'현재 범위에서 제외',explanation:'판단 설명 변경'}[kind] || '판단 기록 변경')).join(' · ')}</span></li>)}</ul>{Boolean(report.omitted_changes) && <p className="frr-empty-file">추가 변경 {report.omitted_changes}건은 이 목록에서 생략했습니다.</p>}</details> : report.current === false ? <p className="frr-empty-file">현재 조사와 차이가 있습니다. 질문별 변경 내용을 비교할 수 없는 기록입니다.</p> : null}
      {report.files.length > 0 || report.word_export ? <div className="frr-files">{[...report.files, ...(!report.files.some(file => file.name.toLowerCase().endsWith('.docx')) && report.word_export ? [report.word_export] : []) ].map((file, index, files) => <ForsicReportLink key={fileSlot(files, index)} metadata={{version: report.id, status: report.lifecycle === 'final' ? '최종 선택본' : report.lifecycle === 'previous_final' ? '이전 최종 선택본' : '저장본', createdAt: report.created_at, draftRevision: report.draft_revision}} file={file} profile={profile} label={fileLabel(file.name)}/>)}</div> : <p className="frr-empty-file">열 수 있는 보고서 파일이 기록되지 않았어요.</p>}
      <details className="frr-details"><summary>보고서 식별 정보와 파일 이름</summary><dl><dt>기록 식별자</dt><dd>{report.id}</dd>{report.name && <><dt>저장 이름</dt><dd>{report.name}</dd></>}{report.session_id && <><dt>출처 대화</dt><dd>{report.session_id}</dd></>}{report.snapshot_id && <><dt>판단 스냅샷</dt><dd>{report.snapshot_id}</dd></>}{report.timestamp_basis && <><dt>시각 근거</dt><dd>{report.timestamp_basis === 'saved_bundle_directory' ? '보고서 저장 디렉터리의 기록 시각' : report.timestamp_basis}</dd></>}</dl>{report.files.length > 0 && <ul>{report.files.map(file => <li key={file.url}>{file.name}</li>)}</ul>}</details>
    </article>

}
export function ForsicReports({questions,reports,authoring,workspace,lastCheckedAt,reportCount,omittedReportCount,loading,error,hasCase,profile}: Props) {
  // The newest saved artifact is not necessarily a verified final report.
  const ordered = [...(reports || [])].sort((a, b) => {
    const aTime = Date.parse(a.created_at || ''), bTime = Date.parse(b.created_at || '')
    return Number.isFinite(aTime) && Number.isFinite(bTime) ? bTime - aTime : 0
  })
  const latest = ordered.find(report => report.id === workspace?.saved_report_id) || ordered[0]
  const previous = ordered.filter(report => report !== latest)
  const final = workspace?.final_report_id ? ordered.find(report => report.id === workspace.final_report_id) : ordered.find(report => report.lifecycle === 'final')
  const savedAt = workspace?.draft?.saved_at || authoring?.saved_at
  const completed = workspace?.completed_groups
  const total = workspace?.total_groups ?? authoring?.groups.length
  const excerpt = authoring?.groups.flatMap(group => group.preview).find(entry => entry.text.trim())
  const inventory = reportCount ?? ordered.length + (omittedReportCount || 0)
  const relation = (report?: InvestigationReport) => report?.draft_relation === 'same' ? '현재 초안과 동일' : report?.draft_relation === 'older' ? '현재 초안보다 이전' : report?.draft_relation === 'different_scope' ? '초안과 범위 다름' : '초안과 관계 미확인'
  const draftVersion = workspace?.draft?.draft_revision ?? authoring?.draft_revision
  const draftStatus = authoring?.status === 'unavailable' ? '저장된 초안 · 현재 작성 상태 미확인' : workspace?.stage_label || '작성 상태 미확인'
  return <section className="forsic-readable-reports" aria-label="사건 보고서 목록" aria-busy={loading}>
    <header className="frr-intro"><h2>보고서</h2><p>최신 초안에서 저장본, 최종 선택까지의 보존된 상태입니다.</p></header>
    <section className="frr-lifecycle" aria-label="보고서 현재 단계">
      <strong>{workspace?.stage_label || '보고서 단계 미확인'}</strong>
      <p>{workspace?.next_step || '작성·검토 기록을 조회해 다음 단계를 확인합니다.'}</p>
      <p>{completed !== undefined && total !== undefined ? `필수 작성 항목 ${completed} / ${total}개 완료` : total !== undefined ? `작성 항목 ${total}개 · 완료 수 미확인` : '작성 항목 수 미확인'}</p>
      {workspace?.independent_review && <p>독립 검토 · {reviewLabels[workspace.independent_review.status] || '상태 확인 필요'} · 남은 항목 {workspace.independent_review.pending_count}개</p>}
      <p className="frr-time">10초마다 상태 조회 · {loading ? '조회 중…' : '조회 대기'} · 마지막 성공 {lastCheckedAt ? <time dateTime={lastCheckedAt}>{reportTime(lastCheckedAt)}</time> : '아직 없음'}</p>
      {error && <p className="forsic-inspector-notice" role="status">상태 조회에 실패했습니다. {lastCheckedAt ? '마지막 성공 시점의 자료를 표시합니다.' : '아직 성공적으로 조회된 자료가 없습니다.'} 다음 조회에서 다시 확인합니다.</p>}
    </section>
    <div className="frr-overview" aria-label="보고서 세 상태 요약">
      <section><h3>최신 초안</h3><strong>{draftVersion != null ? `초안 v${draftVersion}` : '버전 미확인'}</strong><p>{savedAt ? <time dateTime={savedAt}>{reportTime(savedAt)}</time> : '저장 시각 미확인'}</p><small>{draftStatus}</small></section>
      <section><h3>최신 저장본</h3><strong>{latest ? latest.draft_revision !== undefined ? `초안 v${latest.draft_revision} 저장본` : '저장 버전 있음' : '아직 없음'}</strong><p>{latest?.created_at ? <time dateTime={latest.created_at}>{reportTime(latest.created_at)}</time> : '저장 시각 미확인'}</p><small>{latest ? relation(latest) : '파일 생성 기록 없음'}</small></section>
      <section><h3>최종 선택본</h3><strong>{final ? final.draft_revision !== undefined ? `초안 v${final.draft_revision} 선택본` : '선택 기록 있음' : workspace?.final_report_id ? '파일 조회 불가' : '선택된 최종본 없음'}</strong><p>{final?.finalization?.selected_at ? <time dateTime={final.finalization.selected_at}>{reportTime(final.finalization.selected_at)}</time> : '선택 시각 미확인'}</p><small>{final ? relation(final) : '최종 선택 여부와 검토 통과는 별개'}</small></section>
    </div>
    <p className="frr-inventory">{inventory > ordered.length ? `저장 기록 ${inventory}개 중 ${ordered.length}개 조회` : `저장 기록 ${ordered.length}개 조회`}</p>
    <section className="frr-draft" aria-label="작성 중인 최신 초안"><header className="frr-intro"><h2>작성 중인 최신 초안</h2><p>{workspace?.draft ? `초안 v${workspace.draft.draft_revision} · 기록 v${workspace.draft.revision}` : authoring?.draft_revision != null ? `초안 v${authoring.draft_revision}` : '초안 버전 미확인'}</p><p className="frr-time">마지막 초안 저장 {savedAt ? <time dateTime={savedAt}>{reportTime(savedAt)}</time> : '시각 미확인'}</p></header>
      <p className="frr-time">{draftStatus}</p>
      {excerpt ? <p className="frr-authoring-preview">{excerpt.text}</p> : <p className="frr-empty-file">최신 초안 미리보기가 없습니다.</p>}
      {authoring?.read_issue && <p role="status">{authoring.read_issue}</p>}
    </section>
    <section aria-label="저장된 보고서"><header className="frr-intro"><h2>저장된 보고서</h2><p>저장 당시의 고정된 파일입니다. 독자용 형식은 최종 선택 여부와 별개입니다.</p></header>
      {latest ? <ReportCard report={latest} profile={profile}/> : <p className="frr-empty-file">{loading ? '저장본을 조회하고 있습니다.' : hasCase ? '아직 저장된 보고서가 없어요.' : '사건에 연결된 보고서를 확인할 수 없습니다.'}</p>}
    </section>
    <section aria-label="최종본"><header className="frr-intro"><h2>최종본</h2></header>{final ? <><p className="frr-time">최종 선택 {final.finalization?.selected_at ? reportTime(final.finalization.selected_at) : '시각 미확인'}</p><p className="frr-time">{final.draft_relation === 'older' ? '현재 초안보다 이전 버전의 최종 선택본입니다.' : final.draft_relation === 'same' ? '현재 초안과 같은 버전입니다.' : '현재 초안과의 버전 관계는 확인되지 않았습니다.'}</p><p className="frr-time">독립 검토 {final.finalization?.independent_review_current === true ? '선택 시점에 유효한 기록' : '현재 유효성 미확인'} · 문서 배치 {layoutLabels[final.finalization?.layout_status || ''] || '검토 완료 미확인'} · 최종 선택은 검토 통과를 뜻하지 않습니다.</p><ReportCard report={final} profile={profile}/></> : <p className="frr-empty-file">{workspace?.final_report_id ? '최종 선택 기록은 있으나 해당 저장 파일을 이 목록에서 조회할 수 없습니다.' : '아직 최종본으로 선택된 보고서가 없습니다.'}</p>}</section>
    {authoring && <details className="frr-section frr-authoring-section"><summary>초안 항목별 작성·검토 내용 <span>{authoring.groups.length}개</span></summary><div className="frr-section-content"><ReportAuthoring authoring={authoring}/></div></details>}
    {previous.length > 0 && <details className="frr-section frr-history"><summary>저장본 이력 <span>{previous.length}개 · 조회된 저장 파일 버전 {ordered.length}개</span></summary><div className="frr-section-content">{previous.map(report => <ReportCard key={report.id} report={report} profile={profile}/>)}</div></details>}
    {Boolean(questions?.length) && <details className="frr-section frr-current-section"><summary>현재 질문별 판단 <span>{questions!.length}개</span></summary><section className="frr-current frr-section-content" aria-label="현재 조사 요약">{questions!.map((question,index) => <details className="frr-current-question" key={question.id}><summary><span className="frr-question-index">질문 {index + 1}</span><span className="frr-question-title">{question.question}</span></summary><p className="frr-full-question">{question.question}</p><QuestionJudgment question={question}/></details>)}</section></details>}
  </section>
}
