import type {InvestigationExecution} from '@/lib/forsic-investigation'

interface Props {execution?: InvestigationExecution | null}
const phases: Record<string, string> = {investigation: '조사', checkpoint: '중간 점검', closing: '보고서 마무리', settlement: '산출물 확인·정리', exhausted: '실행 한도 종료'}
const owners: Record<string, string> = {inactive: '실행 대기', native_lease: 'native 실행 임대 확인', recovery_scheduled: '자동 복구 담당 등록', native_wait: 'native 대기 예약', unowned: '실행 담당 없음 · 점검 필요', unknown: '실행 담당 확인 불가'}
const reasons: Record<string, string> = {runtime_budget_checkpoint_failed: '중간 점검 결과 저장 실패', 'goal runtime budget exhausted': '전체 실행 한도 소진', runtime_budget_handoff: '남은 산출물 저장 후 종료', context_recovery_exhausted: '문맥 복구 한도 소진', context_recovery_delivery_unknown: '이전 복구 요청의 전달 상태 확인 필요', context_recovery_owner_unavailable: '자동 복구 담당 확보 실패'}
const duration = (value?: number | null) => value == null ? '확인 불가' : `${Math.max(0, value / 3600).toFixed(1)}시간`
const at = (value: number) => `${new Date(value * 1000).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'})} KST`

/** Native goal controls and canonical report projection; this component only reads. */
export function ForsicExecutionStatus({execution: e}: Props) {
  if (!e) return null
  const title = e.status === 'paused' ? '일시정지' : e.status === 'done' ? '목표 실행 종료' : phases[e.phase || ''] || '실행 상태 확인 중'
  return <section className="fcj-card fcj-runtime" aria-label="실행 및 보고서 마감 상태">
    <h3>{title}</h3>
    <dl><div><dt>남은 실행 시간</dt><dd>{duration(e.remaining_seconds)}</dd></div>
      <div><dt>보고서·정리 예약</dt><dd>{duration(e.reserve_seconds)}{e.estimate_source === 'cold_start' ? ' · 초기 보수 추정' : ''}</dd></div>
      <div><dt>보고서 준비 목표</dt><dd>{e.ready_deadline ? at(e.ready_deadline) : '재개 시 남은 예산으로 계산'}</dd></div>
      <div><dt>실행 담당·재개</dt><dd>{owners[e.owner_status || 'unknown']}{e.next_due ? ` · ${at(e.next_due)}` : ''}</dd></div></dl>
    {e.reason && e.status === 'paused' && <p className="fi-caption">중지 사유 · {reasons[e.reason] || (e.reason.startsWith('turn failed:') ? '모델 응답 또는 실행 실패 · 진단 기록 확인 필요' : '저장된 중지 사유 확인 필요')}</p>}
    {Boolean(e.blocker_count || e.blockers?.length) && <p className="fi-caption">보고서에서 확인할 필수 항목 {e.blocker_count ?? e.blockers?.length}건</p>}
    {e.wall_deadline && <p className="fi-caption">별도 제출 시한 · {at(e.wall_deadline)}</p>}
  </section>
}
