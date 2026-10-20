import type {InspectorToolCall} from './forsic-inspector'
import {forsicToolCopy} from './forsic-tool-copy'

type Data = Record<string, unknown>
function object(value: unknown): Data {return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}}
function parse(value?: string): Data {try {return object(JSON.parse(value ?? ''))} catch {return {}}}
function value(input: unknown): string {return typeof input === 'string' ? input : typeof input === 'number' ? String(input) : ''}
function short(input: string, limit = 180): string {const text = input.replace(/\s+/g, ' ').trim(); return text.length > limit ? text.slice(0, limit) + '…' : text}
const IMAGE_ACTIONS: Record<string, string> = {volumes: '이미지 볼륨 확인', list: '이미지 폴더 목록', stat: '이미지 파일 정보', search: '이미지 내용 검색', archive_list: '압축파일 목록 확인', archive_read: '압축파일 원문 읽기'}
const COMMAND_OUTCOMES = {
  returned: {label: '명령 반환', tone: 'neutral', caption: '명령의 반환을 확인했습니다. 내부 단계의 성공이나 전체 검사 범위를 뜻하지 않습니다.'},
  no_matches: {label: '검색 결과 없음', tone: 'neutral', caption: '선택한 검색 조건에서 일치 항목이 없었습니다. 사건 전체에서 해당 흔적이 없다는 뜻은 아닙니다.'},
  partial: {label: '부분 처리', tone: 'warning', caption: '반환 결과에 제한·생략 또는 경고가 있습니다. 실제 처리 범위와 제외 항목은 결과 원문과 생성물의 요약을 확인하세요.'},
  failed: {label: '실행 실패', tone: 'error', caption: '명령 실행이 실패했습니다. 앞서 출력된 내용만으로 뒤 단계의 실행이나 전체 검사를 확인할 수 없습니다.'},
  completion_unknown: {label: '완료 미확인', tone: 'unknown', caption: '종료가 확인되지 않았습니다. 기존 실행 기록을 확인하기 전 자동으로 다시 실행하지 않습니다.'},
  not_started: {label: '실행 안 됨', tone: 'unknown', caption: '명령 실행이 시작되지 않았습니다. 반환된 사유를 확인하세요.'},
  interrupted: {label: '실행 중단', tone: 'warning', caption: '명령이 중단되었습니다. 남은 출력은 전체 처리나 검사 완료를 뜻하지 않습니다.'},
} as const

/** Present only explicit shell metadata; native running and rejection states keep priority. */
export function shellCommandOutcome(call: InspectorToolCall) {
  if (call.name !== 'forsic_shell' || call.status === 'running') return undefined
  const result = parse(call.result)
  if (result.execution_rejected === true) return undefined
  const status = value(object(result.command_outcome).status)
  if (!Object.hasOwn(COMMAND_OUTCOMES, status)) return undefined
  if (result.error && ['returned', 'no_matches', 'partial'].includes(status)) return undefined
  return {status, ...COMMAND_OUTCOMES[status as keyof typeof COMMAND_OUTCOMES]}
}

function reportingAttention(result: Data): string {
  const fields = ['error', 'warning', 'warnings', 'execution_rejected', 'requires_user_action', 'user_action_required', 'needs_user_action', 'requires_approval', 'approval_required', 'blocked']
  const values = fields.map(key => result[key]).filter(field => Array.isArray(field) ? field.length > 0 : Boolean(field))
  if (!values.length) return ''
  const text = values.flatMap(field => Array.isArray(field) ? field : [field]).map(field => value(field) || value(object(field).message)).filter(Boolean).join(' · ')
  return text || '사용자 확인이 필요한 반환 사항이 있습니다.'
}

/** Fold routine internal reads only; errors and explicit attention requests stay visible. */
export function isRoutineReportingLookup(call: InspectorToolCall): boolean {
  return call.name === 'forsic_reporting' && ['source', 'state', 'execution', 'gaps', 'review_inbox'].includes(value(parse(call.arguments).action))
    && call.status !== 'error' && !reportingAttention(parse(call.result))
}

/** Only returned fields describe results; requested limits are never treated as measured coverage. */
export function toolDetail(call: InspectorToolCall) {
  const args = parse(call.arguments), result = parse(call.result), execution = object(result.execution), copy = forsicToolCopy(call.name, value(args.action))
  const outcome = shellCommandOutcome(call)
  const details: {label: string; value: string}[] = []
  const add = (label: string, input: unknown) => {const text = value(input); if (text) details.push({label, value: text})}
  const path = value(args.path || result.path), file = value(args.file_path || result.file_path)
  const member = value(object(result.member).name || args.member_path)
  const command = value(args.command || result.command)
  const purpose = copy.publicReason && typeof args.reason === 'string' ? args.reason.trim() : ''
  const label = call.name === 'forsic_image_files' ? IMAGE_ACTIONS[value(args.action)] || copy.label : copy.label
  add('목적', purpose)
  add(file ? '이미지' : '대상', path)
  add('파일', file)
  add('압축파일 내부 대상', member)
  if (args.member_index !== undefined) add('멤버 번호', args.member_index)
  add('명령', command)
  add('작업 폴더', args.cwd || result.cwd)
  add('검색어', args.pattern || args.query || args.needle || (args.action === 'search' ? args.text : undefined))
  if (call.name === 'forsic_skill') add('스킬', args.name || result.name)
  if (args.action) add('작업', args.action)
  const facts: string[] = []
  if (typeof result.line_start === 'number' && typeof result.line_end === 'number') facts.push(`${result.line_start}–${result.line_end}행`)
  else if (typeof result.byte_start === 'number' && typeof result.byte_end === 'number') facts.push(`바이트 ${result.byte_start}–${result.byte_end}`)
  if (Array.isArray(result.entries)) facts.push(`반환 목록 ${result.entries.length}개`)
  else if (Array.isArray(result.files)) facts.push(`반환 목록 ${result.files.length}개`)
  else if (Array.isArray(result.volumes)) facts.push(`볼륨 ${result.volumes.length}개`)
  if (Array.isArray(result.matches)) facts.push(`반환 일치 항목 ${result.matches.length}개`)
  if (typeof result.content_bytes === 'number') add('전체 크기', `${result.content_bytes.toLocaleString('ko-KR')}바이트`)
  const exitCode = execution.exit_code ?? result.exit_code
  if (typeof exitCode === 'number') {add('종료 코드', exitCode); if (call.name === 'forsic_shell') facts.push(`종료 코드 ${exitCode}`)}
  if (outcome) {add('실행 상태', outcome.label); add('범위·해석', outcome.caption)}
  const preview = object(result.preview_truncated)
  const partial = outcome?.status === 'partial' || call.resultTruncated || result.preview_truncated === true || preview.stdout === true || preview.stderr === true || result.complete_file === false || result.complete_content === false || result.complete_member === false || result.listing_complete === false || result.coverage_complete === false || result.complete === false || result.truncated === true
  if (partial) facts.push('일부 범위')
  if (result.next_line != null) add('이어서 읽을 행', result.next_line)
  if (result.next_offset != null) add('이어서 읽을 위치', result.next_offset)
  if (facts.length) add('반환 범위', facts.join(' · '))
  let summary = facts.join(' · ')
  const attention = call.name === 'forsic_reporting' ? reportingAttention(result) : ''
  if (attention) {add('확인할 사항', attention); summary = [summary, short(attention)].filter(Boolean).join(' · ')}
  if (call.status === 'error') {
    const error = value(result.error) || value(object(result.error).message) || value(result.stderr)
    summary = short(error || summary || '실행을 완료하지 못했습니다.')
  }
  if (!summary && call.status === 'completed') {
    if (call.name === 'forsic_skill' && typeof result.content === 'string') summary = '조사 절차를 불러왔습니다.'
    else if (!Object.keys(result).length && call.result) summary = short(call.result)
  }
  const target = short(command || member || file || path || (call.name === 'forsic_skill' ? value(args.name || result.name) : ''), command ? 80 : 180)
  return {label, description: copy.description, purpose: short(purpose), target, summary, details}
}

export function toolStatusLabel(call: InspectorToolCall): string {
  if (call.status === 'running' && call.result !== undefined) return '작업 접수'
  const outcome = shellCommandOutcome(call)
  if (outcome) return outcome.label
  return {completed: '성공', error: '실패', running: '진행 중', unknown: '결과 미확인'}[call.status]
}
