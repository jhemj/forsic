import {timeContext, type InvestigationTime, type InvestigationView, type InvestigationRef, type InvestigationQuestion, type InvestigationDisplay} from '@/lib/forsic-investigation'

export type MonitorView = InvestigationView
export const recordKey = (row: {id: string; version: string}) => `${row.id}@${row.version}`
export const sameRef = (a: InvestigationRef, b: InvestigationRef) => a.kind === b.kind && a.id === b.id && a.version === b.version
export function exactQuestionRefs(question: InvestigationQuestion, data: InvestigationView): InvestigationRef[] {
  if (question.evidence_refs) return question.evidence_refs
  return question.evidence_ids.flatMap(id => {
    const matches = data.sources.filter(source => source.id === id)
    return matches.length === 1 ? [{kind: 'source', id, version: matches[0].version}] : []
  })
}
export const displayTitle = (row: InvestigationDisplay, fallback: string) => row.display_title || fallback
export const displaySummary = (row: InvestigationDisplay, fallback: string) => row.display_summary || fallback
export interface TimelineMark {row: InvestigationTime; domain: string; domainLabel: string; lane: string; inferred: boolean; localClock: boolean; precision: string; spans: {start: number | null; end: number | null}[]}
export function explicitInstant(value?: string | null): number | null {
  if (!value || !/T.*(?:Z|[+-]\d{2}:?\d{2})$/.test(value)) return null
  const instant = Date.parse(value)
  return Number.isFinite(instant) ? instant : null
}
/** Calendar coordinates are display-only: their UTC arithmetic never asserts a timezone. */
function calendarCoordinate(value?: string | null): number | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$/.test(value)) return null
  const parsed = Date.parse(value.length === 10 ? `${value}T00:00:00Z` : `${value}Z`)
  return Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 10) === value.slice(0, 10) ? parsed : null
}
/** A coordinate is allowed only by the retained temporal contract, never by record order. */
export function timelineMark(row: InvestigationTime): TimelineMark | null {
  const context = timeContext(row)
  const localClock = (context.coordinate_basis || row.coordinate_basis) === 'source_local_calendar'
  const clock = context.clock_domain || (localClock ? '' : '정규화된 비교 시각')
  if (!['record', 'event', 'file_metadata'].includes(row.time_kind) || !clock || /unknown|incompatible|미상|불명/i.test(clock) || (!localClock && (!row.comparable || /^(unknown|미상|불명)$/i.test(context.timezone_basis || row.timezone_basis || '')))) return null
  const coordinate = localClock ? calendarCoordinate : explicitInstant
  const values = localClock ? row.local_values ?? [] : row.normalized_values
  const candidates = context.temporal_candidates ?? context.candidates ?? []
  const inferred = context.observed_or_inferred === 'inferred' || ['interval', 'candidates'].includes(row.shape) || candidates.length > 0
  const precision = context.precision || row.precision || (values[0]?.match(/^\d{4}-\d{2}-\d{2}$/) ? 'day' : 'second')
  let spans: TimelineMark['spans']
  if (inferred) {
    const ranges = candidates.length ? candidates : [{earliest: context.earliest, latest: context.latest}]
    spans = ranges.map(range => ({start: coordinate(range.earliest), end: coordinate(range.latest)}))
    if (ranges.some((range, index) => (range.earliest && spans[index].start === null) || (range.latest && spans[index].end === null)) || spans.some(span => (span.start === null && span.end === null) || (span.start !== null && span.end !== null && span.start > span.end))) return null
  } else {
    const instant = row.shape === 'point' && values.length === 1 ? coordinate(values[0]) : null
    if (instant === null || (context.observed_or_inferred && context.observed_or_inferred !== 'observed')) return null
    spans = [{start: instant, end: precision === 'day' ? instant + 86_400_000 : instant}]
  }
  return {row, domain: localClock ? `${clock} · 출처 내부 달력 · 시간대 미상` : clock, domainLabel: localClock ? `${context.clock_label || row.display_source_title || '출처 내 기록 시각'} · 출처 내부 달력 · 시간대 미상` : context.clock_label || clock, localClock, precision, inferred,
    lane: inferred ? '추정 구간 · 후보' : row.time_kind === 'file_metadata' ? '파일 메타데이터' : row.time_kind === 'event' ? '관측된 사건 시각' : '관측된 기록 시각', spans}
}
export function sessionOf(row: object): string | undefined {return (row as {session_id?: string}).session_id}
export function sessionLabel(id: string | undefined, data: MonitorView): string {return id ? data.sessions?.find(session => session.id === id)?.label || id : '세션 미기록'}
export function filterSession(data: MonitorView, id: string): MonitorView {
  if (!id) return data
  const belongs = (row: object) => sessionOf(row) === id
  return {...data, questions: data.questions.filter(belongs), observations: data.observations.filter(belongs), timeline: data.timeline.filter(belongs), sources: data.sources}
}

export function monitorTimeGroups(rows: InvestigationTime[]) {
  const marks = rows.map(timelineMark).filter((mark): mark is TimelineMark => mark !== null && !mark.inferred && mark.row.time_kind !== 'file_metadata')
  const domains = [...new Set(marks.map(mark => mark.domain))]
  const groups = domains.map(domain => ({key: `comparable:${domain}`, kind: 'comparable', label: `비교 가능한 기록 시각 · ${marks.find(mark => mark.domain === domain)!.domainLabel}`, rows: marks.filter(mark => mark.domain === domain).sort((a, b) => a.spans[0].start! - b.spans[0].start!).map(mark => mark.row)}))
  const metadata = rows.filter(row => row.time_kind === 'file_metadata')
  const modeled = rows.filter(row => row.time_kind !== 'file_metadata' && (timeContext(row).observed_or_inferred === 'inferred' || Boolean(timeContext(row).candidates?.length || timeContext(row).temporal_candidates?.length) || ['interval', 'candidates'].includes(row.shape)))
  const unknown = rows.filter(row => !marks.some(mark => mark.row === row) && !metadata.includes(row) && !modeled.includes(row))
  return [...groups, {key: 'modeled', kind: 'modeled', label: '추정 구간과 시간 후보', rows: modeled}, {key: 'metadata', kind: 'metadata', label: '파일 메타데이터 시각', rows: metadata}, {key: 'unknown', kind: 'unknown', label: '순서를 확인할 수 없는 시각', rows: unknown}].filter(group => group.rows.length)
}
