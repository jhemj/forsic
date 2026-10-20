import type {InvestigationCheck, InvestigationTaskFocus} from '@/lib/forsic-investigation';
import './forsic-mission-focus.css';

const WORK_STATE: Record<string, string> = {open:'조사 중', active:'조사 중', held:'보류', blocked_internal:'검사 문제로 대기', blocked_external:'자료·권한 대기', budget_deferred:'조사 자원에 따라 보류'};

function Check({check}: {check: InvestigationCheck}) {
  return <li data-complete={check.complete}><strong>{check.why || check.tool || '저장된 검사'}</strong><span>{check.complete ? '검사 평가됨' : check.state === 'recheck' ? '재검토 필요' : check.state === 'failed' ? '실행 실패' : check.state === 'returned' ? '반환됨 · 평가 대기' : '검사 대기·진행 중'}</span><details><summary>검사 근거와 식별자</summary><pre>{JSON.stringify(check, null, 2)}</pre></details></li>;
}
export function ForsicMissionFocus({focus}: {focus?: InvestigationTaskFocus}) {
  if (!focus) return null;
  return <section className="forsic-mission-focus" aria-label="현재 질문과 검사">
    <h3>현재 질문과 검사</h3>
    {focus.status === 'unavailable' ? <p>현재 질문과 검사 상태를 확인하지 못했어요.</p> : !focus.tasks.length && !focus.omitted_tasks ? <p>저장된 질문이 없어요. 대화하며 자유롭게 조사를 이어갈 수 있어요.</p> : null}
    {focus.tasks.map(task => <article key={`${task.id}:${task.revision}`}>
      <strong>{task.question}</strong><small>질문 개정 {task.revision} · {task.review_required ? '현재 판단 재검토 필요' : task.work_state === 'scoped_closed' ? '명시한 범위 종결' : WORK_STATE[task.work_state] || '현재 조사 판단'}</small>
      <p>{task.answer_is_model_assessment || '아직 답을 정리하지 않았어요.'}</p>
      {task.scope && <p><b>판단 범위</b> {task.scope}</p>}
      {task.judgments.map((judgment, index) => <div key={index}><small>판단의 강도 · {judgment.inference_strength}</small>{[...judgment.assumptions, ...judgment.limitations].map((text, i) => <p key={i}>{text}</p>)}{judgment.counterevidence_ref_count > 0 && <p>반증 근거 {judgment.counterevidence_ref_count}개 · 상세에서 위치 확인</p>}</div>)}
      {task.alternatives.map((text, index) => <p key={index}><b>경쟁 설명</b> {text}</p>)}
      {task.remaining_gap_count > 0 && <p><b>남은 공백 {task.remaining_gap_count}개</b> {task.remaining_gap_examples.map(gap => gap.reason).join(' · ')}</p>}
      {task.next_checks.length > 0 && <p><b>포식이가 정한 다음 확인</b> {task.next_checks.join(' · ')}</p>}
      <ul>{task.checks.filter(check => !check.complete).map(check => <Check key={check.mission_id} check={check}/>)}</ul>
      {task.check_counts.completed > 0 && <details className="forsic-completed-checks"><summary>평가한 검사 {task.check_counts.completed}개</summary><ul>{task.checks.filter(check => check.complete).map(check => <Check key={check.mission_id} check={check}/>)}</ul><p>개별 검사 평가이며 질문 전체의 해결을 뜻하지 않아요.</p></details>}
      <details><summary>질문 상세와 보존 위치{task.omitted_details || task.omitted_checks ? ` · 생략된 상세 ${task.omitted_details}개, 검사 ${task.omitted_checks}개` : ''}</summary><pre>{JSON.stringify({question_id: task.id, revision: task.revision, definition_version: task.definition_version, lookup: task.details_call, judgments: task.judgments, check_counts: task.check_counts}, null, 2)}</pre></details>
    </article>)}
    {Boolean(focus.omitted_tasks) && <p>다른 질문 {focus.omitted_tasks}개는 사건 기록에 보존되어 있어요.</p>}
  </section>;
}
