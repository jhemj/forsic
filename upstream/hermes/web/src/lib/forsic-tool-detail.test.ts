import {expect, it} from 'vitest'
import {isRoutineReportingLookup, shellCommandOutcome, toolDetail, toolStatusLabel} from './forsic-tool-detail'
import {savedToolCalls, type InspectorToolCall} from './forsic-inspector'
const call = (patch: Partial<InspectorToolCall> = {}): InspectorToolCall => ({id: 'a', name: 'forsic_image_files', arguments: '{}', status: 'completed', source: 'saved', ...patch})

it('describes the selected image operation and returned listing without claiming body coverage', () => {
  const detail = toolDetail(call({arguments: JSON.stringify({action: 'list', path: '/evidence/disk.e01', file_path: '/etc', limit: 100}), result: JSON.stringify({entries: [{name: 'a'}, {name: 'b'}], listing_complete: false, next_offset: 2})}))
  expect(detail.label).toBe('이미지 폴더 목록')
  expect(detail.target).toBe('/etc')
  expect(detail.summary).toBe('반환 목록 2개 · 일부 범위')
  expect(detail.details).toContainEqual({label: '이미지', value: '/evidence/disk.e01'})
  expect(detail.summary).not.toContain('100')
})
it('uses returned read ranges and keeps partial reads explicit', () => {
  const detail = toolDetail(call({name: 'forsic_image_read', arguments: '{"file_path":"/var/log/example.log","line_limit":500}', result: '{"line_start":1,"line_end":80,"complete_file":false,"next_line":81}'}))
  expect(detail.summary).toBe('1–80행 · 일부 범위')
  expect(detail.details).toContainEqual({label: '이어서 읽을 행', value: '81'})
})
it('shows a terminal failure and the actual exit instead of the cleanup result', () => {
  const [item] = savedToolCalls([{role: 'assistant', tool_calls: [{id: 'a', function: {name: 'forsic_shell', arguments: '{"command":"python inspect.py"}'}}]}, {role: 'tool', tool_call_id: 'a', content: JSON.stringify({execution: {state: 'returned', exit_code: 1, cleanup: {exit_code: 0}}, stderr: 'Missing input file'})}])
  expect(toolStatusLabel(item)).toBe('실패')
  expect(toolDetail(item)).toMatchObject({target: 'python inspect.py', summary: 'Missing input file'})
  expect(toolDetail(item).details).toContainEqual({label: '종료 코드', value: '1'})
})
it('does not turn job acceptance or missing return into success', () => {
  expect(toolStatusLabel(call({status: 'running', result: '{"status":"queued"}'}))).toBe('작업 접수')
  expect(toolStatusLabel(call({status: 'running'}))).toBe('진행 중')
  expect(toolStatusLabel(call({status: 'unknown'}))).toBe('결과 미확인')
})
it('keeps malformed, truncated and unrecognized payloads usable without guessing measurements', () => {
  expect(toolDetail(call({arguments: '{"file_path":', result: '{"lines":', resultTruncated: true})).summary).toBe('일부 범위')
  expect(toolDetail(call({arguments: '{}', result: 'plain reply'})).summary).toBe('plain reply')
  expect(toolDetail(call({arguments: JSON.stringify({path: '/evidence/' + 'x'.repeat(500)})})).target.length).toBeLessThan(185)
})
it('displays exact selected archive member and bytes without inferring full member coverage', () => {
  const detail = toolDetail(call({name: 'forsic_archive_read', arguments: '{"member_index":7}', result: '{"member":{"name":"logs/app.log"},"byte_start":0,"byte_end":255,"complete_member":false}'}))
  expect(detail.target).toBe('logs/app.log')
  expect(detail.summary).toBe('바이트 0–255 · 일부 범위')
})
it('separates total size from returned bytes and handles shell preview truncation', () => {
  const detail = toolDetail(call({result: '{"content_bytes":10000,"byte_start":0,"byte_end":99}'}))
  expect(detail.summary).toBe('바이트 0–99')
  expect(detail.details).toContainEqual({label: '전체 크기', value: '10,000바이트'})
  expect(toolDetail(call({name: 'forsic_shell', result: '{"execution":{"exit_code":0},"preview_truncated":{"stdout":true,"stderr":false}}'})).summary).toBe('종료 코드 0 · 일부 범위')
})
it('shows the actual image search text without treating unrelated note text as a search', () => {
  expect(toolDetail(call({arguments: '{"action":"search","text":"scheduled task"}'})).details).toContainEqual({label: '검색어', value: 'scheduled task'})
  expect(toolDetail(call({name: 'forsic_note', arguments: '{"text":"analysis"}'})).details).not.toContainEqual({label: '검색어', value: 'analysis'})
})

it('shows only the explicit public shell purpose with a short summary and full detail', () => {
  const reason = '  Check the selected image log for execution evidence.\n' + 'Keep the exact observation. '.repeat(20)
  const detail = toolDetail(call({name: 'forsic_shell', arguments: JSON.stringify({command: 'python inspect.py', reason})}))
  expect(detail.purpose.length).toBeLessThanOrEqual(181)
  expect(detail.purpose).not.toContain('\n')
  expect(detail.details[0]).toEqual({label: '목적', value: reason.trim()})
  expect(detail.target).toBe('python inspect.py')
})
it.each([undefined, null, 12, {}, '   '])('does not infer a purpose when the shell reason is %s', reason => {
  const detail = toolDetail(call({name: 'forsic_shell', arguments: JSON.stringify({command: 'python inspect.py', reason, reasoning: 'private', reasoning_content: 'private'}), result: '{"reason":"returned reason","description":"returned description"}'}))
  expect(detail.purpose).toBe('')
  expect(detail.details.some(item => item.label === '목적')).toBe(false)
  expect(JSON.stringify(detail)).not.toContain('private')
})
it('uses registered public purpose for evidence tools without repurposing judgment or result fields', () => {
  expect(toolDetail(call({arguments: '{"reason":"public reason"}'})).purpose).toBe('public reason')
  for (const name of ['forsic_question', 'forsic_review_response', 'forsic_mission', 'forsic_assess', 'forsic_archive_read', 'forsic_archive_relist', 'unrelated_tool']) {
    expect(toolDetail(call({name, arguments: '{"reason":"change justification"}'})).purpose).toBe('')
  }
})

it.each([
  ['no_matches', '검색 결과 없음', 1], ['partial', '부분 처리', 2],
  ['failed', '실행 실패', 7], ['completion_unknown', '완료 미확인', 0],
  ['not_started', '실행 안 됨', undefined], ['interrupted', '실행 중단', undefined],
  ['returned', '명령 반환', 0],
])('presents the explicit shell outcome %s and keeps the raw exit and scope', (status, label, exitCode) => {
  const [item] = savedToolCalls([{role: 'assistant', tool_calls: [{id: 'a', function: {name: 'forsic_shell', arguments: '{}'}}]}, {role: 'tool', tool_call_id: 'a', content: JSON.stringify({execution: {state: status === 'completion_unknown' ? status : 'returned', exit_code: exitCode}, command_outcome: {status}})}])
  expect(toolStatusLabel(item)).toBe(label)
  const detail = toolDetail(item)
  expect(detail.details).toContainEqual({label: '실행 상태', value: label})
  expect(detail.details.some(entry => entry.label === '범위·해석' && entry.value.length > 10)).toBe(true)
  if (exitCode !== undefined) expect(detail.details).toContainEqual({label: '종료 코드', value: String(exitCode)})
  if (status === 'partial') expect(detail.summary).toContain('일부 범위')
})
it('does not infer shell outcomes from stdout, other tools, running calls, or rejected calls', () => {
  const result = JSON.stringify({command_outcome: {status: 'partial'}, execution: {exit_code: 2}})
  expect(toolStatusLabel(call({name: 'forsic_shell', status: 'running', result}))).toBe('작업 접수')
  expect(shellCommandOutcome(call({name: 'forsic_shell', status: 'running', result}))).toBeUndefined()
  expect(toolStatusLabel(call({name: 'forsic_shell', status: 'error', result: JSON.stringify({execution_rejected: true, command_outcome: {status: 'returned'}})}))).toBe('실패')
  expect(toolStatusLabel(call({name: 'forsic_image_read', result}))).toBe('성공')
  expect(toolStatusLabel(call({name: 'forsic_shell', result: '{"stdout":"no_matches", "execution":{"exit_code":0}}'}))).toBe('성공')
  expect(toolStatusLabel(call({name: 'forsic_shell', result: '{"command_outcome":{"status":"unrecognized"}}'}))).toBe('성공')
})

it.each(['returned', 'no_matches', 'partial'])('keeps an explicit call error ahead of the shell outcome %s', status => {
  const result = JSON.stringify({error: 'Capability discovery returned invalid metadata.', error_code: 'capability_discovery_invalid', execution: {state: 'returned', exit_code: 0}, command_outcome: {status}})
  const [item] = savedToolCalls([{role: 'assistant', tool_calls: [{id: 'a', function: {name: 'forsic_shell', arguments: '{}'}}]}, {role: 'tool', tool_call_id: 'a', content: result}])
  expect(shellCommandOutcome(item)).toBeUndefined()
  expect(toolStatusLabel(item)).toBe('실패')
  expect(toolDetail(item).summary).toBe('Capability discovery returned invalid metadata.')
})

it('labels review responses in Korean without presenting their judgment reason as execution purpose', () => {
  const detail = toolDetail(call({name: 'forsic_review_response', arguments: JSON.stringify({reason: '판단을 유지한 근거'})}))
  expect(detail.label).toBe('검토 답변 기록')
  expect(detail.description).toBe('검토 의견의 반영 여부와 판단 이유를 해당 질문에 기록합니다.')
  expect(detail.purpose).toBe('')
  expect(detail.details.some(item => item.label === '목적')).toBe(false)
  expect(JSON.stringify(detail)).not.toContain('판단을 유지한 근거')
})

it('distinguishes report authoring from each routine reporting read without changing execution status', () => {
  for (const action of ['source', 'state', 'execution', 'gaps', 'review_inbox']) {
    const lookup = call({name: 'forsic_reporting', arguments: JSON.stringify({action}), status: 'running'})
    expect(isRoutineReportingLookup(lookup)).toBe(true)
    expect(toolDetail(lookup).label).toContain('조회')
    expect(toolStatusLabel(lookup)).toBe('진행 중')
  }
  const report = call({name: 'forsic_reporting', arguments: '{"action":"render"}'})
  expect(toolDetail(report).label).toBe('보고서 작성')
  expect(isRoutineReportingLookup(report)).toBe(false)
  for (const patch of [{status: 'error' as const}, {result: '{"warning":"Check selected scope"}'}, {result: '{"requires_user_action":true}'}, {result: '{"blocked":"Approval required"}'}]) {
    expect(isRoutineReportingLookup(call({name: 'forsic_reporting', arguments: '{"action":"source"}', ...patch}))).toBe(false)
  }
  expect(isRoutineReportingLookup(call({name: 'forsic_reporting', arguments: '{broken'}))).toBe(false)
})
