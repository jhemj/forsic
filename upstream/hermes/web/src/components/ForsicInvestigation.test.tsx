// @vitest-environment jsdom
import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect, it, vi} from 'vitest'
import {ForsicInvestigation} from './ForsicInvestigation'
import {investigationTimeGroups, type InvestigationTime, type InvestigationView} from '@/lib/forsic-investigation'
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
const source = {kind: 'source', id: 'E-one', version: 'immutable-v1'}
const point = (id: string, value: string, extra: Partial<InvestigationTime> = {}): InvestigationTime => ({id, version: '1', time_kind: 'record', shape: 'point', raw_values: [value], normalized_values: [value], comparable: true, explanation: id, limitation: '', ...extra})
const rows = [point('later', '2026-10-02T00:00:00Z'), point('ctime', '2026-10-01T00:00:00Z', {time_kind: 'file_metadata', file_time_type: 'ctime'}), point('earlier', '2026-10-01T00:00:00Z'), point('inferred', '2026-09-01T00:00:00Z', {temporal: {observed_or_inferred: 'inferred', earliest: '2026-09-01', latest: '2026-09-03'}}), point('unknown', '10:01:00', {comparable: false})]
it('sorts explicit comparable instants only and separates inference, file metadata and unknown clocks', () => {
  const groups = investigationTimeGroups(rows)
  expect(groups.map(group => [group.key, group.rows.map(row => row.id)])).toEqual([
    ['comparable', ['earlier', 'later']], ['modeled', ['inferred']], ['metadata', ['ctime']], ['unknown', ['unknown']],
  ])
  expect(rows[0].id).toBe('later')
})
it('renders retained facts and opens only the selected immutable source at its exact coordinate', async () => {
  const data: InvestigationView = {case_id: 'case', counts: {questions: 1, observations: 2, timeline: 5}, partial: true, omitted_counts: {observations: 1},
    questions: [{id: 'Q-real', version: '3', question: '저장된 실제 질문', answer: '현재 답', assessment: 'undetermined', work_state: 'open', evidence_ids: ['E-one'], evidence_refs: [source]}],
    sources: [{...source, label: '정확한 반환', locator: 'retained'}], observations: [{id: 'O-real', version: '2', source_ref: source, statement: '실제 관측', field_pointer: '/text', literal: '원문 문자열', interpretation_limit: '실행 여부 미확인', byte_start: 12, byte_end: 30}],
    timeline: rows.map(row => ({...row, observation_ref: {kind: 'observation', id: 'O-real', version: '2'}}))}
  const readSource = vi.fn(async () => ({source_ref: source, text: '선택한 보존 구간', byte_start: 12, byte_end: 30, total_bytes: 90}))
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host)
  try {
    await act(async () => {root.render(<ForsicInvestigation data={data} loading={false} error={null} readSource={readSource}/>)})
    expect(readSource).not.toHaveBeenCalled()
    expect(host.textContent).toContain('저장된 실제 질문'); expect(host.textContent).toContain('관측 1건 생략')
    expect(host.querySelector('[data-kind=metadata]')?.textContent).toContain('ctime')
    const button = [...host.querySelectorAll('button')].find(item => item.textContent?.includes('보존 원문 보기'))!
    await act(async () => {button.click()})
    expect(readSource).toHaveBeenCalledExactlyOnceWith({source, pointer: '/text', offset: 12})
    expect(host.querySelector('.fi-source-preview')?.textContent).toContain('선택한 보존 구간')
    expect(host.querySelector('.fi-source-preview')?.textContent).toContain('UTF-8 바이트 12–30 / 전체 90')
    expect(document.activeElement).toBe(host.querySelector('.fi-source-preview'))
    await act(async () => {(host.querySelector('[aria-label="보존 원문 닫기"]') as HTMLButtonElement).click()})
    expect(document.activeElement).toBe(button)
  } finally {await act(async () => {root.unmount()}); host.remove()}
})

function monitorFixture(): InvestigationView {
  const observation = (id: string, version: string, session_id: string, reference = source) => ({id, version, session_id, source_ref: reference, statement: `관측-${id}`, field_pointer: '/text', literal: `합성-${id}`, byte_start: 12, byte_end: 30, interpretation_limit: '실행 성공 미확인'})
  return {case_id: 'synthetic-case', profile: 'synthetic-profile', sessions: [{id: 'a', label: '첫 조사', available: true}, {id: 'b', label: '추가 확인', available: false}],
    questions: [{id: 'Q-a', version: '1', question: '실행 흔적 확인?', answer: '명령 기록은 있으나 성공 미확인', assessment: 'undetermined', work_state: 'active', evidence_ids: ['E-one'], evidence_refs: [source], counterarguments: ['명령만으로 성공 입증 불가'], limitations: ['결과 로그 미확보'], gaps: ['독립 흔적 확인 필요'], session_id: 'a'}, {id: 'Q-b', version: '1', question: '추가 질문', answer: '다른 세션 답', assessment: 'supported', work_state: 'open', evidence_ids: ['E-two'], evidence_refs: [{kind: 'source', id: 'E-two', version: 'v2'}], session_id: 'b'}],
    sources: [{...source, label: '명령 반환', locator: 'retained', session_id: 'a'}, {id: 'E-two', version: 'v2', label: '추가 반환', locator: 'retained', session_id: 'b'}, {...source, version: 'old', label: '이전 반환', locator: 'retained'}],
    observations: [observation('O-a', '1', 'a'), observation('O-b', '2', 'b', {kind: 'source', id: 'E-two', version: 'v2'}), observation('O-old', '3', 'a', {...source, version: 'old'})],
    timeline: [point('early-a', '2026-10-01T00:00:00Z', {session_id: 'a', question_id: 'Q-a', observation_ref: {kind: 'observation', id: 'O-a', version: '1'}}), point('late-b', '2026-10-03T00:00:00Z', {session_id: 'b', question_id: 'Q-b', observation_ref: {kind: 'observation', id: 'O-b', version: '2'}}), point('range-a', '', {session_id: 'a', shape: 'interval', temporal: {earliest: '2026-10-01T06:00:00Z', latest: '2026-10-01T12:00:00Z'}}), point('candidates-a', '', {session_id: 'a', shape: 'candidates', temporal: {candidates: [{earliest: '2026-10-01T08:00:00Z', latest: '2026-10-01T09:00:00Z', basis: '후보 1'}, {earliest: '2026-10-02T08:00:00Z', latest: '2026-10-02T09:00:00Z', basis: '후보 2'}]}}), point('unknown-clock', '10:10', {comparable: false, session_id: 'a'}), point('incompatible-clock', '2026-10-01T09:00:00Z', {session_id: 'a', temporal: {clock_domain: 'incompatible'}}), point('file-ctime', '2026-10-01T09:00:00Z', {session_id: 'a', time_kind: 'file_metadata', file_time_type: 'ctime'})]}
}
async function withMonitor(run: (host: HTMLDivElement) => Promise<void>, data = monitorFixture()) {
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host)
  const readSource = vi.fn(async (request: {source: typeof source}) => ({source_ref: request.source, text: '합성 원문', byte_start: 12, byte_end: 30}))
  try {await act(async () => root.render(<ForsicInvestigation data={data} loading={false} error={null} readSource={readSource}/>)); await run(host)}
  finally {await act(async () => root.unmount()); host.remove()}
  return readSource
}
const clickText = async (host: HTMLElement, text: string) => {const button = [...host.querySelectorAll('button')].find(item => item.textContent === text)!; await act(async () => button.click())}
it('filters SVG and records together while unknown clocks stay outside and file timestamps use a separate lane', async () => {
  await withMonitor(async host => {
    expect(host.querySelectorAll('[data-time-id]').length).toBe(5)
    for (const id of ['unknown-clock', 'incompatible-clock']) expect(host.querySelector(`[data-time-id="${id}"]`)).toBeNull()
    expect(host.querySelector('[data-time-id="file-ctime"]')?.getAttribute('data-lane')).toBe('file_metadata')
    expect(host.querySelectorAll('[data-time-id="candidates-a"] [data-inferred-range]').length).toBe(2)
    expect(host.querySelector('[data-kind="unknown"]')?.textContent).toContain('incompatible-clock')
    await act(async () => (host.querySelector('[aria-label="시간축 확대"]') as HTMLButtonElement).click())
    expect(host.querySelector('[data-time-id="early-a"]')).toBeNull()
    expect(host.querySelector('[data-time-id="late-b"]')).toBeNull()
    expect(host.querySelector('[aria-label="단서의 시간 흐름"]')?.textContent).not.toContain('early-a')
    expect(host.querySelector('[data-kind="unknown"]')?.textContent).toContain('unknown-clock')
    expect(host.querySelector('[data-kind="metadata"]')).toBeNull()
    await act(async () => (host.querySelector('[aria-label="시간 필터 초기화"]') as HTMLButtonElement).click())
    expect(host.querySelector('[data-time-id="early-a"]')).not.toBeNull()
    await clickText(host, '추정 구간 · 후보')
    expect(host.querySelector('[data-time-id="range-a"]')).toBeNull()
    expect(host.querySelector('[aria-label="단서의 시간 흐름"]')?.textContent).not.toContain('range-a')
  })
})
it('opens exact immutable observation coordinates by keyboard and shows saved answer, limits and verified origin', async () => {
  const readSource = await withMonitor(async host => {
    await act(async () => {host.querySelector('[data-time-id="early-a"]')!.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}))})
    const selected = host.querySelector('[aria-label="선택한 시간 단서"]')!
    expect(selected.textContent).toContain('실행 성공 미확인')
    expect(selected.textContent).toContain('결과 로그 미확보')
    expect(selected.textContent).toContain('독립 흔적 확인 필요')
    expect(selected.querySelector('a')?.getAttribute('href')).toBe('/chat?case=synthetic-case&resume=a&profile=synthetic-profile')
    expect(host.querySelector('.fi-source-preview')?.textContent).toContain('immutable-v1')
    expect(host.querySelector('.fi-source-preview')?.textContent).toContain('합성 원문')
  })
  expect(readSource).toHaveBeenCalledExactlyOnceWith({source, pointer: '/text', offset: 12})
})
it('filters by origin session and maps only exact source versions without text-inferred relationships', async () => {
  const readSource = await withMonitor(async host => {
    await clickText(host, '연결 지도')
    const graph = host.querySelector('.fi-relation-chart')!
    expect(graph.textContent).toContain('관측-O-a')
    expect(graph.textContent).not.toContain('관측-O-old')
    expect(graph.textContent).not.toContain('관측-O-b')
    await act(async () => graph.querySelector('[aria-label="관측 선택 · 관측-O-a"]')!.dispatchEvent(new KeyboardEvent('keydown', {key: ' ', bubbles: true})))
    expect(host.querySelector('.fi-source-preview')?.textContent).toContain('/text · 오프셋 12')
    const filter = host.querySelector('[aria-label="조사 세션 필터"]') as HTMLSelectElement
    await act(async () => {filter.value = 'b'; filter.dispatchEvent(new Event('change', {bubbles: true}))})
    expect(host.querySelector('.fi-source-preview')).toBeNull()
    expect(host.querySelector('.fi-relation-chart')?.textContent).toContain('관측-O-b')
    expect(host.querySelector('.fi-relation-chart')?.textContent).not.toContain('관측-O-a')
    expect(host.querySelector('[aria-label="사건의 현재 답"]')?.textContent).toContain('다른 세션 답')
    expect(host.querySelector('[aria-label="사건의 현재 답"] a')).toBeNull()
    await clickText(host, '시간 흐름')
    expect(host.querySelector('[data-time-id="late-b"]')).not.toBeNull()
    expect(host.querySelector('[data-time-id="early-a"]')).toBeNull()
  })
  expect(readSource).toHaveBeenCalledExactlyOnceWith({source, pointer: '/text', offset: 12})
})
it('keeps different clock domains on independent axes and unparseable modeled bounds outside coordinates', async () => {
  const data = monitorFixture()
  data.timeline = [point('clock-a', '2026-10-01T00:00:00Z', {temporal: {clock_domain: 'host-a'}}), point('clock-b', '2026-10-01T00:00:00Z', {temporal: {clock_domain: 'host-b'}}), point('unknown-basis', '2026-10-01T00:00:00Z', {temporal: {timezone_basis: 'unknown'}}), point('partial-model', '', {shape: 'interval', temporal: {earliest: '2026-10-01T00:00:00', latest: '2026-10-02T00:00:00Z'}})]
  await withMonitor(async host => {
    expect(host.querySelectorAll('.fi-clock-domain').length).toBe(2)
    expect(host.querySelector('[data-time-id="unknown-basis"]')).toBeNull()
    expect(host.querySelector('[data-time-id="partial-model"]')).toBeNull()
    expect(host.querySelector('[data-kind="modeled"]')?.textContent).toContain('partial-model')
  }, data)
})

it('renders source-local calendar coordinates without asserting UTC, day precision, or missing years', async () => {
  const data = monitorFixture()
  const local = (domain: string, precision = 'second') => ({coordinate_basis: 'source_local_calendar', clock_domain: domain, clock_label: '합성 출처의 기록', timezone_basis: 'unknown retained local clock', observed_or_inferred: 'observed', precision})
  data.timeline = [point('local-a', '', {comparable: false, normalized_values: [], local_values: ['2026-10-01T12:34:56'], temporal_context: local('source-local:a')}), point('local-b', '', {comparable: false, normalized_values: [], local_values: ['2026-10-01T12:34:56'], temporal_context: local('source-local:b')}), point('date-metadata', '', {comparable: false, time_kind: 'file_metadata', file_time_type: 'mtime', local_values: ['2026-10-01'], normalized_values: [], temporal_context: local('source-local:a', 'day')}), point('year-unknown', '', {comparable: false, local_values: ['10-01T12:34:56'], normalized_values: [], temporal_context: local('source-local:a')})]
  await withMonitor(async host => {
    expect(host.querySelectorAll('.fi-clock-domain').length).toBe(2)
    expect(host.querySelector('[data-time-id="local-a"]')?.getAttribute('data-local-clock')).toBe('true')
    expect(host.querySelector('[data-time-id="date-metadata"] [data-day-precision]')).not.toBeNull()
    expect(host.querySelector('[data-time-id="date-metadata"] [data-inferred-range]')).toBeNull()
    expect(host.querySelector('[data-time-id="year-unknown"]')).toBeNull()
    for (const domain of host.querySelectorAll('.fi-clock-domain')) {
      expect(domain.textContent).toContain('시간대 미상')
      expect(domain.textContent).toContain('합성 출처의 기록')
      expect(domain.textContent).not.toContain('source-local:')
      for (const value of domain.querySelectorAll('footer time')) expect(value.textContent).not.toMatch(/Z$/)
    }
    expect(host.querySelector('[data-kind="unknown"]')?.textContent).toContain('year-unknown')
  }, data)
})
it('keeps semantic summaries separate from identifiers and enriches only after an explicit scoped request', async () => {
  const data = monitorFixture()
  data.session_id = 'a'; data.counts = {questions: 2, review_questions: 1}
  data.review_questions = [{id: 'R-one', question: '명령의 성공을 독립 확인했는가?', disposition: 'open', quote_validation: 'exact'}]
  data.sources[0] = {...data.sources[0], label: 'forsic_shell_E-one', display_title: '명령 실행 반환에서 확인한 로그', display_summary: '프로세스 실행 명령이 기록되어 있으나 실행 결과와 성공 여부는 확인되지 않았습니다.'}
  const enriched = {...data, sources: data.sources.map((item, index) => index ? item : {...item, display_title: '사용자가 요청한 정리된 요약', display_origin: 'model_summary'})}
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({status: 'ready', message: '표시용 요약 완료', view: enriched}), {status: 200, headers: {'Content-Type': 'application/json'}}))
  try {
    const readSource = await withMonitor(async host => {
      expect(fetch).not.toHaveBeenCalled()
      expect(host.textContent).not.toContain('모델이 정리한 표시용 요약')
      const overview = host.querySelector('.fi-overview')!
      expect(overview.textContent).toContain('2핵심 질문')
      expect(overview.textContent).toContain('1독립 검토 질문')
      await clickText(host, '연결 지도')
      const graph = host.querySelector('.fi-relation-chart')!
      expect(graph.querySelector('foreignObject strong')?.textContent).toBe(data.sources[0].display_title)
      expect(graph.textContent).not.toContain('forsic_shell_E-one')
      await act(async () => graph.querySelector('[aria-label^="출처 선택"]')!.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})))
      const detail = host.querySelector('[aria-label="선택한 연결의 설명"]')!
      expect(detail.textContent).toContain(data.sources[0].display_summary)
      expect(detail.querySelector('details')?.textContent).toContain('E-one · immutable-v1')
      await clickText(host, '렌 · 독립 검토')
      expect(host.querySelector('[aria-label="저장된 질문과 답"]')).toBeNull()
      expect(host.querySelector('[aria-label="사건의 현재 답"]')).toBeNull()
      expect(host.querySelector('[aria-label="출처에 연결된 관측"]')).toBeNull()
      expect(host.querySelector('.fi-review-prompt')?.textContent).toContain('렌 · 검토 질문')
      expect(host.querySelector('[aria-label="독립 검토 질문"]')?.textContent).toContain(data.review_questions![0].question)
      await clickText(host, '요약 정리')
      expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/investigation-enrich', expect.objectContaining({method: 'POST', body: JSON.stringify({session_id: 'a', profile: 'synthetic-profile'})}))
      expect(host.textContent).toContain('표시용 요약 완료')
      await clickText(host, '연결 지도')
      expect(host.querySelector('.fi-relation-chart')?.textContent).toContain('사용자가 요청한 정리된 요약')
      expect(host.querySelector('.fi-source-preview')).toBeNull()
    }, data)
    expect(readSource).toHaveBeenCalledExactlyOnceWith({source, pointer: '', offset: 0})
  } finally {fetch.mockRestore()}
})

it('distinguishes validated reviewer responses from missing and unverified dispositions', async () => {
  const data = monitorFixture()
  const response = {question_id: 'Q-a', revision: 2, disposition: 'scope_limited' as const, reason: '독립 실행 기록을 확인했으나 성공 범위는 제한됩니다.', limitation: '결과 로그 미확보', citation_refs: ['obs:synthetic/record']}
  data.review_questions = [
    {id: 'R-saved', question: '독립 기록은 확인했는가?', disposition: 'scope_limited', disposition_validation: 'validated_saved_response', response},
    {id: 'R-missing', question: '결과 로그는 있는가?', disposition: 'not_recorded', disposition_validation: 'unverified_or_not_recorded'},
    {id: 'R-unverified', question: '이전 처분은 검증되었는가?', disposition: 'accepted', response},
  ]
  await withMonitor(async host => {
    await clickText(host, '렌 · 독립 검토')
    const cards = host.querySelectorAll('.fi-review-question')
    expect(cards).toHaveLength(3)
    expect(cards[0].querySelector('.fi-review-response')?.textContent).toContain(response.reason)
    expect(cards[0].textContent).toContain('범위 한정')
    expect(cards[0].textContent).toContain(response.limitation)
    expect(cards[0].textContent).toContain(response.citation_refs[0])
    expect(cards[0].textContent).not.toContain('답변 미기록')
    expect(cards[1].querySelector('.fi-review-status')?.textContent).toBe('답변 미기록')
    expect(cards[2].querySelector('.fi-review-response')?.textContent).toContain('미검증')
    expect(cards[2].textContent).not.toContain(response.reason)
    await clickText(host, '시간 흐름')
    expect(host.querySelector('[aria-label="저장된 질문과 답"]')?.textContent).toContain(data.questions[0].answer)
  }, data)
})

it('leads with the saved answer, keeps technical diagrams optional, and leaves Ren outside reading-mode styles', async () => {
  await withMonitor(async host => {
    const answer=host.querySelector('[aria-label="사건의 현재 답"]')!
    const diagram=host.querySelector('details.fi-optional-chart') as HTMLDetailsElement
    expect(Boolean(answer.compareDocumentPosition(diagram) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true)
    expect(diagram.open).toBe(false)
    expect(answer.textContent).toContain('명령 기록은 있으나 성공 미확인')
    expect(answer.querySelectorAll('.fi-judgment-context li').length).toBe(3)
    expect(host.querySelector('.fi-question code')?.closest('details.fi-technical')?.hasAttribute('open')).toBe(false)
    await clickText(host,'연결 지도')
    const chain=host.querySelector('[aria-label="판단에 연결된 근거"]')!
    expect(chain.textContent).toContain('명령 반환')
    expect(chain.textContent).toContain('관측-O-a')
    expect(chain.textContent).not.toContain('관측-O-old')
    expect((host.querySelector('.fi-map-workspace > details.fi-optional-chart') as HTMLDetailsElement).open).toBe(false)
    await clickText(host,'렌 · 독립 검토')
    expect(host.querySelector('.fi-human-panel')).toBeNull()
    expect(host.querySelector('.fi-review-section')).not.toBeNull()
  })
})
