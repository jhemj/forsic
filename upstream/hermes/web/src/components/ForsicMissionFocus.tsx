import type {InvestigationCheck, InvestigationTaskFocus} from '@/lib/forsic-investigation';
import './forsic-mission-focus.css';

const WORK_STATE: Record<string, string> = {open:'조사 중', active:'조사 중', held:'보류', blocked_internal:'검사 문제로 대기', blocked_external:'자료·권한 대기', budget_deferred:'조사 자원에 따라 보류'};

function Check({check}: {check: InvestigationCheck}) {
  return <li data-complete={check.complete}><strong>{check.why || check.tool || '저장된 검사'}</strong><span>{check.complete ? '검사 평가됨' : check.state === 'recheck' ? '재검토 필요' : check.state === 'failed' ? '실행 실패' : check.state === 'returned' ? '반환됨 · 평가 대기' : '검사 대기·진행 중'}</span><details><summary>검사 근거와 식별자</summary><pre>{JSON.stringify(check, null, 2)}</pre></details></li>;
}
export function ForsicMissionFocus({focus, questionId}: {focus?: InvestigationTaskFocus; questionId: string}) {
  const task = focus?.tasks.find(item => item.id === questionId);
  return <section className="forsic-mission-focus" aria-label="조사 진행">
    <h4>조사 진행</h4>
    {!focus || focus.status === 'unavailable' ? <p>저장된 검사 진행을 확인하지 못했어요.</p> : !task ? <p>선택한 질문의 검사 진행이 이 보기에 포함되지 않았어요.</p> : null}
    {task && <>
      <p>{task.review_required ? '현재 판단 재검토 필요' : task.work_state === 'scoped_closed' ? '명시한 범위 종결' : WORK_STATE[task.work_state] || '현재 조사 판단'}</p>
      <div className="forsic-check-counts"><span>평가한 검사 <strong>{task.check_counts.completed}개</strong></span><span>대기·재확인 <strong>{task.check_counts.pending_or_recheck}개</strong></span><span>남은 공백 <strong>{task.remaining_gap_count}개</strong></span></div>
      {!task.checks.length && !task.omitted_checks && <p className="fi-caption">별도로 등록된 검사는 없어요. 일반 도구 실행은 대화에서 확인할 수 있습니다.</p>}
      {task.remaining_gap_examples.length > 0 && <details><summary>남은 공백</summary><ul>{task.remaining_gap_examples.map(gap => <li key={gap.id}>{gap.reason}</li>)}</ul></details>}
      <ul>{task.checks.filter(check => !check.complete).map(check => <Check key={check.mission_id} check={check}/>)}</ul>
      {task.check_counts.completed > 0 && <details className="forsic-completed-checks"><summary>평가한 검사 {task.check_counts.completed}개</summary><ul>{task.checks.filter(check => check.complete).map(check => <Check key={check.mission_id} check={check}/>)}</ul><p>개별 검사 평가이며 질문 전체의 해결을 뜻하지 않아요.</p></details>}
      <details><summary>질문 상세와 보존 위치{task.omitted_details || task.omitted_checks ? ` · 생략된 상세 ${task.omitted_details}개, 검사 ${task.omitted_checks}개` : ''}</summary><pre>{JSON.stringify({question_id: task.id, revision: task.revision, definition_version: task.definition_version, lookup: task.details_call, check_counts: task.check_counts}, null, 2)}</pre></details>
    </>}
  </section>;
}
