import type {ConversationRow} from './forsic-conversation'
import {forsicToolCopy} from './forsic-tool-copy'

/** Native goal accounting: elapsed excludes the currently active segment. */
export interface InvestigationGoal {
  status?: string
  paused_reason?: string
  pause_requested?: boolean
  created_at?: number
  runtime_budget_seconds?: number | null
  runtime_elapsed_seconds?: number
  runtime_started_at?: number | null
  runtime_accounting_known?: boolean
  runtime_phase?: string | null
  runtime_checkpoint_due_seconds?: number | null
  runtime_checkpoints?: {threshold_seconds: number; decision: 'continue' | 'finish'; reason: string; decided_at: number}[]
}

export const finiteClock = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0
export function investigationElapsed(goal: InvestigationGoal, now: number): number | null {
  if (goal.runtime_accounting_known === false || !finiteClock(goal.runtime_elapsed_seconds)) return null
  if (goal.status === 'active') {
    if (!finiteClock(goal.runtime_started_at) || goal.runtime_started_at === 0) return null
    return goal.runtime_elapsed_seconds + Math.max(0, now / 1000 - goal.runtime_started_at)
  }
  return goal.runtime_elapsed_seconds
}

export function investigationDuration(seconds: number): string {
  const total = Math.floor(Math.max(0, seconds))
  return `${Math.floor(total / 3600)}시간 ${Math.floor(total % 3600 / 60)}분 ${total % 60}초`
}

export interface RuntimeWork {
  running: boolean
  reconnecting?: boolean
  pausing?: boolean
  reviewing?: boolean
  rows: ConversationRow[]
  statusText?: string
}

/** Describe observed work, never infer an investigation stage from elapsed time or prose. */
export function investigationWorkLabel(goal: InvestigationGoal, connected: boolean, work?: RuntimeWork): string {
  if (goal.status === 'paused') return goal.paused_reason === 'runtime_budget_handoff' ? '결과 정리됨' : '일시정지'
  if (goal.status === 'done') return '조사 종료'
  if (!connected) return work?.reconnecting ? '연결 확인 중' : '연결 끊김'
  if (work?.pausing || goal.pause_requested) return '일시정지 준비 중'
  if (goal.runtime_phase === 'checkpoint') return '중간 점검 예정'
  if (goal.runtime_phase === 'closing' || goal.runtime_phase === 'handoff') return '마무리 중'
  if (goal.runtime_phase === 'exhausted') return '최종 한도 도달'
  if (!work) return '작업 상태 확인 중'
  if (work.running) {
    const labels: string[] = []
    // Completed assistant turns and human messages bound the current public tool suffix.
    for (const row of [...work.rows].reverse()) {
      if (row.review || row.kind === 'notice') continue
      if (row.role === 'user' || row.role === 'assistant' && !row.streaming && row.kind !== 'briefing') break
      for (const call of row.calls ?? []) {
        if (call.source !== 'live' || call.status !== 'running' || call.result !== undefined) continue
        for (const tool of call.requestedTools ?? [call]) {
          let action: string | undefined
          try {const args: unknown = JSON.parse(tool.arguments); if (args && typeof args === 'object' && 'action' in args && typeof args.action === 'string') action = args.action} catch { /* Incomplete native arguments have no known action. */ }
          if (tool.name === 'forsic_reporting' && action === 'compose') {labels.push('보고서 정리 중'); continue}
          if (tool.name === 'forsic_reporting' && !['source', 'state', 'gaps', 'execution', 'review_inbox', 'render', 'finalize', 'review'].includes(action ?? '')) {labels.push('조사 작업 중'); continue}
          if (['forsic_case', 'forsic_intake', 'forsic_list', 'forsic_image_info', 'forsic_image_files'].includes(tool.name)) {labels.push('증거 파악 중'); continue}
          const copy = forsicToolCopy(tool.name, action)
          labels.push(copy.description && tool.name !== 'forsic_shell' ? `${copy.label} 중` : '조사 중')
        }
      }
    }
    if (labels.length) return labels.every(label => label === labels[0]) ? labels[0] : '조사 작업 중'
  }
  if (work.reviewing) return '독립 검토 중'
  if (!work.running) return '응답 대기 중'
  if (work.rows.at(-1)?.streaming) return '답변 작성 중'
  if (/압축|compacting/i.test(work.statusText ?? '')) return '문맥 정리 중'
  return '조사 중'
}
