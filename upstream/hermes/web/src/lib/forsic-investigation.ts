/** Bounded read-only views of retained question, source and time records. */
export interface InvestigationRef {kind: string; id: string; version: string}
export interface InvestigationDisplay {display_title?: string; display_summary?: string; display_origin?: string}
export interface InvestigationSource extends InvestigationDisplay {id: string; version: string; label: string; locator: string; source_role?: string; coverage?: string; provenance?: string; session_id?: string}
export interface InvestigationObservation extends InvestigationDisplay {
  id: string; version: string; source_ref: InvestigationRef; statement: string; field_pointer: string; literal: string
  byte_start?: number; byte_end?: number; value?: unknown; presented_view?: string; coordinate_basis?: string; interpretation_limit: string; session_id?: string
}
export interface ReaderExplanation {
  status: 'current' | 'provisional' | 'stale' | 'missing'; canonical_answer: string; title?: string
  paragraphs: {text: string; claim_refs: InvestigationRef[]; source_refs: InvestigationRef[]; counterevidence_refs: InvestigationRef[]}[]
  counterarguments: string[]; limitations: string[]; semantic_approval: false
}
export interface InvestigationQuestion {
  id: string; version: string; question: string; answer: string; assessment: string; work_state: string; evidence_ids: string[]; evidence_refs?: InvestigationRef[]
  session_id?: string; counterarguments?: string[]; limitations?: string[]; gaps?: string[]; reader_explanation?: ReaderExplanation
}
interface TemporalCandidate {earliest?: string | null; latest?: string | null; basis: string}
export interface InvestigationTemporal {
  observed_or_inferred?: string; timestamp_role?: string; clock_domain?: string; earliest?: string | null; latest?: string | null
  timezone_basis?: string | null; year_basis?: string | null; confidence_basis?: string; assumptions?: string[]
  candidates?: TemporalCandidate[]; temporal_candidates?: TemporalCandidate[]; limitations?: string[]; temporal_limitations?: string[]
  temporal_requires_recheck?: boolean; coordinate_basis?: string; precision?: string; clock_label?: string
}
export interface InvestigationTime extends InvestigationDisplay {
  id: string; version: string; question_id?: string; event_ref?: string; observation_ref?: InvestigationRef | null; session_id?: string
  time_kind: string; shape: string; raw_values: string[]; normalized_values: string[]; comparable: boolean
  explanation: string; limitation: string; file_time_type?: string | null; timezone_basis?: string | null; year_basis?: string | null
  local_values?: string[]; precision?: string; coordinate_basis?: string; display_source_title?: string
  temporal?: InvestigationTemporal; temporal_context?: InvestigationTemporal
}
export interface InvestigationReviewQuestion {id: string; question: string; disposition?: string; semantic_approval?: boolean; disposition_validation?: 'validated_saved_response' | 'unverified_or_not_recorded'; response?: {question_id: string; revision: number; disposition: 'investigated' | 'refuted' | 'scope_limited'; reason: string; limitation: string | null; citation_refs: string[]}; quote_validation?: string; review_id?: string; currentness?: string; current_goal?: boolean; session_id?: string}
export interface InvestigationView {
  case_id: string; session_id?: string; profile?: string; questions: InvestigationQuestion[]; sources: InvestigationSource[]; observations: InvestigationObservation[]; timeline: InvestigationTime[]
  review_questions?: InvestigationReviewQuestion[]; review_question_coverage?: {current_goal_questions?: number; historical_goal_questions?: number}; timeline_empty_reason?: string
  task_focus?: InvestigationTaskFocus
  scope?: 'case'; sessions?: InvestigationSession[]; reports?: InvestigationReport[]
  counts?: Record<string, number>; omitted_counts?: Record<string, number>; partial?: boolean
}
export interface InvestigationSession {id: string; label: string; available?: boolean}
export interface InvestigationReportChange {question_id: string; title: string; kinds: string[]}
export interface InvestigationReportFile {name: string; url: string; method?: 'POST'}
export interface InvestigationReport {word_export?: InvestigationReportFile & {method: 'POST'}; editorial_status?: 'authored_current' | 'technical_partial'; timestamp_basis?: string; omitted_changes?: number; snapshot_id?: string; current?: boolean; comparison_status?: 'current' | 'changed' | 'unavailable'; changes?: InvestigationReportChange[]; id: string; name: string; title: string; created_at?: string; session_id?: string; scope?: string; files: InvestigationReportFile[]}
export interface InvestigationSourcePage {source_ref: InvestigationRef; text?: string; value?: unknown; retained_coverage?: unknown; acquisition_context?: unknown; items?: {pointer: string; key?: string}[]; byte_start?: number; byte_end?: number; total_bytes?: number; has_more?: boolean; next_offset?: number | null; total?: number; offset?: number}
export interface InvestigationSourceRequest {source: InvestigationRef; pointer?: string; offset?: number}
export function timeContext(row: InvestigationTime): InvestigationTemporal {return {...row.temporal, ...row.temporal_context}}
function absolutePoint(row: InvestigationTime): number | null {
  const value = row.normalized_values[0]
  const context = timeContext(row)
  if (!['record', 'event'].includes(row.time_kind) || !row.comparable || row.normalized_values.length !== 1 || row.shape !== 'point' || (context.observed_or_inferred !== undefined && context.observed_or_inferred !== 'observed') || Boolean(context.candidates?.length || context.temporal_candidates?.length) || !value || !/T.*(?:Z|[+-]\d{2}:?\d{2})$/.test(value)) return null
  const instant = Date.parse(value)
  return Number.isFinite(instant) ? instant : null
}
/** Only comparable explicit instants are sorted; delivery and ledger time are unused. */
export function investigationTimeGroups(rows: InvestigationTime[]) {
  const comparable = rows.filter(row => absolutePoint(row) !== null).sort((a, b) => absolutePoint(a)! - absolutePoint(b)!)
  const metadata = rows.filter(row => row.time_kind === 'file_metadata')
  const modeled = rows.filter(row => row.time_kind !== 'file_metadata' && absolutePoint(row) === null && (timeContext(row).observed_or_inferred === 'inferred' || Boolean(timeContext(row).candidates?.length || timeContext(row).temporal_candidates?.length) || ['interval', 'candidates'].includes(row.shape)))
  const unknown = rows.filter(row => !comparable.includes(row) && !modeled.includes(row) && !metadata.includes(row))
  return [{key: 'comparable', label: '비교 가능한 기록 시각', rows: comparable}, {key: 'modeled', label: '추정 구간과 시간 후보', rows: modeled}, {key: 'metadata', label: '파일 메타데이터 시각', rows: metadata}, {key: 'unknown', label: '순서를 확인할 수 없는 시각', rows: unknown}].filter(group => group.rows.length)
}

export interface InvestigationCheck {
  mission_id: string; mission_version: string; state: string; complete: boolean; tool: string; why: string
  result_ref: InvestigationRef | null; assessment_ref: InvestigationRef | null; outcome: string | null
}
export interface InvestigationTask {
  id: string; revision: number; definition_version: string; question: string; work_state: string
  assessment: string; review_required: boolean; answer_is_model_assessment: string; scope: string
  judgments: {inference_strength: string; assumptions: string[]; limitations: string[]; counterevidence_refs: InvestigationRef[]; counterevidence_ref_count: number}[]
  alternatives: string[]; next_checks: string[]; remaining_gap_count: number
  remaining_gap_examples: {id: string; reason: string; disposition: string}[]
  checks: InvestigationCheck[]; check_counts: {completed: number; pending_or_recheck: number}; omitted_checks: number; omitted_details: number
  details_call: {tool: string; arguments: Record<string, string>}
}
export interface InvestigationTaskFocus {status: 'ready' | 'unavailable'; tasks: InvestigationTask[]; omitted_tasks: number | null}
