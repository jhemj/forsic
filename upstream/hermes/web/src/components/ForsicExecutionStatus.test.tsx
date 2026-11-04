import {renderToStaticMarkup} from 'react-dom/server'
import {describe, expect, it} from 'vitest'
import {ForsicExecutionStatus} from './ForsicExecutionStatus'

it('shows a paused goal with its frozen allowance and no invented deadline', () => {
  const view = renderToStaticMarkup(<ForsicExecutionStatus execution={{status:'paused',remaining_seconds:10800,policy_revision:'report-completion-v2',blocker_count:20,blockers:['one']}}/>)
  expect(view).toContain('<h3>일시정지</h3>')
  expect(view).toContain('3.0시간')
  expect(view).toContain('재개 시 남은 예산으로 계산')
  expect(view).toContain('보고서에서 확인할 필수 항목 20건')
})

describe('native phase is independent of artifact completion', () => {
  it('shows a scheduled wake and conservative reserve without calling it final', () => {
    const view = renderToStaticMarkup(<ForsicExecutionStatus execution={{status:'active',phase:'closing',remaining_seconds:18000,reserve_seconds:21600,estimate_source:'cold_start',next_due:1793900000,owner_status:'recovery_scheduled',policy_revision:'report-completion-v2',artifact_status:'draft_preserved'}}/>)
    expect(view).toContain('<h3>보고서 마무리</h3>')
    expect(view).toContain('6.0시간 · 초기 보수 추정')
    expect(view).toContain('자동 복구 담당 등록')
    expect(view).not.toContain('최종본 완료')
  })
  it('does not equate active state with an execution owner', () => {
    const view = renderToStaticMarkup(<ForsicExecutionStatus execution={{status:'active',phase:'closing',remaining_seconds:100,owner_status:'unowned',policy_revision:'report-completion-v2'}}/>)
    expect(view).toContain('실행 담당 없음 · 점검 필요')
    expect(view).not.toContain('자동 복구 담당 등록')
  })
  it('labels goal end as execution end', () => {
    const view = renderToStaticMarkup(<ForsicExecutionStatus execution={{status:'done',remaining_seconds:100,policy_revision:'report-completion-v2',artifact_status:'draft_preserved'}}/>)
    expect(view).toContain('<h3>목표 실행 종료</h3>')
  })
})
