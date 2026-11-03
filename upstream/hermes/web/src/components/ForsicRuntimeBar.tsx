import {useEffect, useState} from 'react'
import {finiteClock, investigationDuration, investigationElapsed, investigationWorkLabel, type InvestigationGoal, type RuntimeWork} from '../lib/forsic-runtime'
import './forsic-runtime-bar.css'

const kst = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'})

/** A read-only view of the native goal clock; crossing a marker never makes a decision. */
export function ForsicRuntimeBar({goal, connected, work}: {goal?: InvestigationGoal | null; connected: boolean; work?: RuntimeWork}) {
  const [sample, setSample] = useState(() => ({goal: connected ? goal : undefined, clock: Date.now()}))
  const ticking = connected && goal?.status === 'active' && goal.runtime_accounting_known !== false
  useEffect(() => {
    if (!connected) return
    setSample({goal, clock: Date.now()})
    if (!ticking) return
    const timer = setInterval(() => setSample({goal, clock: Date.now()}), 1000)
    return () => clearInterval(timer)
  }, [goal, connected, ticking])
  if (!goal) return null
  const clock = sample.clock
  const budget = finiteClock(goal.runtime_budget_seconds) && goal.runtime_budget_seconds > 0 ? goal.runtime_budget_seconds : null
  const elapsed = !connected && goal.status === 'active' && sample.goal !== goal ? null : investigationElapsed(goal, clock)
  const percent = elapsed !== null && budget !== null ? Math.min(100, elapsed / budget * 100) : null
  const activity = investigationWorkLabel(goal, connected, work)
  const checkpoints = budget === null ? [] : [43200, 64800].filter(value => value < budget)
  const paused = goal.status === 'paused'
  const phase = paused ? goal.paused_reason === 'runtime_budget_handoff' ? '결과 정리됨' : '일시정지'
    : goal.runtime_phase === 'checkpoint' ? '체크포인트 검토 예정'
    : goal.runtime_phase === 'closing' || goal.runtime_phase === 'handoff' ? '마무리 중'
    : goal.runtime_phase === 'exhausted' ? '최종 한도 도달' : goal.status === 'done' ? '조사 종료' : '조사 중'
  const knownStart = finiteClock(goal.created_at) && goal.created_at > 0 && goal.created_at < 8640000000000
  const remaining = budget !== null && elapsed !== null ? Math.max(0, budget - elapsed) : null
  const deadline = connected && !paused && goal.status === 'active' && remaining !== null && elapsed !== null
    ? clock + remaining * 1000 : null
  return <section className="fc-runtime" aria-label="조사 시간" data-stale={!connected}>
    <div className="fc-runtime-heading"><strong>조사 시간 <span>{elapsed === null ? '확인 필요' : `현재 ${investigationDuration(elapsed)}`}</span></strong><span>{connected ? phase : activity}{!connected && elapsed !== null && ' · 마지막 확인'}</span></div>
    {budget !== null && <>
      <div className="fc-runtime-track" role="progressbar" aria-label="조사 시간 경과" aria-valuemin={0} aria-valuemax={budget} aria-valuenow={elapsed === null ? undefined : Math.min(budget, Math.floor(elapsed))} aria-valuetext={elapsed === null ? '조사 시간 미확인' : `${investigationDuration(elapsed)} 경과, 최종 한도 ${budget / 3600}시간${!connected ? ', 마지막 확인값' : ''}`}>
        {percent !== null && <><div className="fc-runtime-fill" style={{width: `${percent}%`}}/><span className="fc-runtime-current" style={{left: `${percent}%`}} data-edge={percent < 30 ? 'start' : percent > 70 ? 'end' : 'middle'} aria-hidden="true"><span className="fc-runtime-bubble">{activity}</span></span></>}
        {checkpoints.map(value => <span key={value} className="fc-runtime-tick" style={{left: `${value / budget * 100}%`}} aria-hidden="true"/>)}
      </div>
      <div className="fc-runtime-labels" aria-hidden="true"><span>시작</span>{checkpoints.map(value => <span key={value} style={{left: `${value / budget * 100}%`}}>{value / 3600}h</span>)}<span>{budget / 3600}h <small>최종 한도</small></span></div>
    </>}
    <span className="fc-sr-only">현재 작업: {activity}</span>
    <div className="fc-runtime-details">
      {knownStart && <span>시작 <time dateTime={new Date(goal.created_at! * 1000).toISOString()}>{kst.format(goal.created_at! * 1000)} KST</time></span>}
      {budget === null ? <span>시간 한도 미확인</span> : <span>최종 한도 {budget / 3600}시간{deadline !== null && ` · 예상 ${kst.format(deadline)} KST`}</span>}
      <span>일시정지 시간 제외{paused ? ' · 재개 시 예상 시각 갱신' : ''}</span>
    </div>
    {checkpoints.length > 0 && <ul className="fc-runtime-checkpoints" aria-label="조사 체크포인트">{checkpoints.map(value => {
      const receipt = goal.runtime_checkpoints?.find(item => item.threshold_seconds === value && ['continue', 'finish'].includes(item.decision))
      const closingSelected = goal.runtime_checkpoints?.some(item => item.decision === 'finish' && item.threshold_seconds < value)
      const due = !receipt && goal.runtime_checkpoint_due_seconds === value && goal.status === 'active'
      const label = receipt ? receipt.decision === 'continue' ? '계속 조사' : '마무리 선택' : closingSelected ? '마무리 전환' : due ? '검토 예정' : '대기'
      return <li key={value}><span>{value / 3600}h 체크포인트</span><strong>{label}</strong>{receipt?.reason && <p>{receipt.reason}</p>}</li>
    })}</ul>}
  </section>
}
