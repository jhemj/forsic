import {FileText} from 'lucide-react'
import type {InvestigationQuestion, InvestigationReport} from '@/lib/forsic-investigation'
import {QuestionJudgment} from './ForsicRelationMap'
import {ForsicReportLink} from './ForsicReportLink'
import './ForsicReports.css'

interface Props {questions?: InvestigationQuestion[]; reports?: InvestigationReport[]; loading: boolean; error: string | null; hasCase: boolean; profile?: string}
function fileLabel(name: string) {
  const lower = name.toLowerCase()
  return lower.endsWith('.html') ? '웹에서 읽기' : lower.endsWith('.docx') ? 'Word 내려받기' : lower.endsWith('.pdf') ? 'PDF 내려받기' : lower.endsWith('.zip') ? '보고서 근거 ZIP 내려받기' : '보고서 파일 내려받기'
}
function reportTime(value: string) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value.replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$1년 $2월 $3일')
  if (!/T.*(?:Z|[+-]\d{2}:?\d{2})$/.test(value)) return value.replace('T',' ')
  const parsed = new Date(value)
  return Number.isFinite(parsed.getTime()) ? new Intl.DateTimeFormat('ko-KR', {year:'numeric', month:'long', day:'numeric', hour:'2-digit', minute:'2-digit',timeZone:'Asia/Seoul'}).format(parsed) + ' KST' : value
}
export function ForsicReports({questions,reports,loading,error,hasCase,profile}: Props) {
  return <section className="forsic-readable-reports" aria-label="사건 보고서 목록" aria-busy={loading}>
    <header className="frr-intro"><h2>현재 조사 요약</h2><p>현재 저장된 질문의 답과 설명입니다. 조사 모니터와 같은 기록을 읽으며, 조회만으로 보고서를 새로 만들지 않습니다.</p></header>
    {questions?.length ? <section className="frr-current" aria-label="현재 조사 요약">{questions.map((question,index) => <details className="frr-current-question" key={question.id} open={index === 0}><summary>{question.question}</summary><QuestionJudgment question={question}/></details>)}</section> : <p className="frr-empty-file">{loading ? '현재 조사 요약을 불러오고 있어요.' : '아직 저장된 질문의 답이 없습니다.'}</p>}
    <header className="frr-intro"><h2>저장된 보고서 버전</h2><p>각 보고서는 저장 시점의 판단과 근거를 보존합니다. 현재 요약이 바뀌어도 이전 보고서 파일은 그대로 남습니다.</p></header>
    {error && <p className="forsic-inspector-notice" role="status">보고서 목록을 불러오지 못했어요. 새로고침으로 다시 확인해 주세요.</p>}
    {reports?.map(report => <article className="frr-card" key={report.id}>
      <header><FileText size={22} aria-hidden="true"/><div><h3>{report.title || '제목이 기록되지 않은 보고서'}</h3>{report.created_at ? <p className="frr-time"><time dateTime={report.created_at}>{reportTime(report.created_at)}</time></p> : <p className="frr-time">저장 시각 미확인</p>}</div></header>
      {(report.editorial_status === 'authored_current' || report.editorial_status === 'technical_partial') && <span className="frr-editorial-status" title="저장 시점의 작성 형식을 나타냅니다.">{report.editorial_status === 'authored_current' ? '독자용 보고서' : '중간 기록'}</span>}
      <p className="frr-version-status" data-current={report.current === true ? 'true' : report.current === false ? 'false' : 'unknown'}>{report.current === true ? '현재 조사와 일치하는 저장본' : report.current === false ? '현재 조사와 달라진 저장본' : '현재 조사와의 일치 여부 미확인'}</p>
      {report.changes?.length ? <section className="frr-changes" aria-label="저장 후 달라진 판단"><h4>저장 후 달라진 내용</h4><ul>{report.changes.map(change => <li key={change.question_id}><strong>{change.title}</strong><span>{change.kinds.map(kind => ({answer:'현재 답 변경',evidence:'연결 근거 변경',limits:'한계·반론 변경',added:'새 질문',removed:'현재 범위에서 제외',explanation:'판단 설명 변경'}[kind] || '판단 기록 변경')).join(' · ')}</span></li>)}</ul>{Boolean(report.omitted_changes) && <p className="frr-empty-file">추가 변경 {report.omitted_changes}건은 이 목록에서 생략했습니다.</p>}</section> : report.current === false ? <p className="frr-empty-file">현재 조사와 차이가 있습니다. 질문별 변경 내용을 비교할 수 없는 기록입니다.</p> : null}
      {report.files.length > 0 || report.word_export ? <div className="frr-files">{[...report.files, ...(!report.files.some(file => file.name.toLowerCase().endsWith('.docx')) && report.word_export ? [report.word_export] : [])].map(file => <ForsicReportLink key={`${profile}:${file.url}:${file.method || 'GET'}`} file={file} profile={profile} label={fileLabel(file.name)}/>)}</div> : <p className="frr-empty-file">열 수 있는 보고서 파일이 기록되지 않았어요.</p>}
      <details className="frr-details"><summary>보고서 식별 정보와 파일 이름</summary><dl><dt>기록 식별자</dt><dd>{report.id}</dd>{report.name && <><dt>저장 이름</dt><dd>{report.name}</dd></>}{report.session_id && <><dt>출처 대화</dt><dd>{report.session_id}</dd></>}{report.snapshot_id && <><dt>판단 스냅샷</dt><dd>{report.snapshot_id}</dd></>}{report.timestamp_basis && <><dt>시각 근거</dt><dd>{report.timestamp_basis === 'saved_bundle_directory' ? '보고서 저장 디렉터리의 기록 시각' : report.timestamp_basis}</dd></>}</dl>{report.files.length > 0 && <ul>{report.files.map(file => <li key={file.url}>{file.name}</li>)}</ul>}</details>
    </article>)}
    {!error && !reports?.length && <div className="forsic-inspector-empty"><FileText size={24} aria-hidden="true"/><p>{loading ? '보고서를 불러오고 있어요.' : hasCase ? '아직 저장된 보고서가 없어요.' : '사건에 연결된 보고서를 확인할 수 없어요.'}</p><small>포식이가 저장한 보고서가 여기에 모입니다.</small></div>}
  </section>
}
