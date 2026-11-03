// @vitest-environment jsdom
import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect,it,vi} from 'vitest'
import {ForsicReports} from './ForsicReports'
import type {InvestigationReport, InvestigationReportAuthoring} from '@/lib/forsic-investigation'
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
vi.mock('@/lib/api', () => ({authedFetch:vi.fn()}))
const report: InvestigationReport = {id:'report-immutable-1234',name:'report-internal-name',title:'합성 자료의 실행 흔적 검토',created_at:'2026-10-20T03:30:00Z',session_id:'session-internal-id',files:[{name:'report-1234.html',url:'/api/plugins/forsic/reports/1234/report.html'},{name:'report-1234.docx',url:'/api/plugins/forsic/reports/1234/report.docx'}]}
const groupLabels = ['종합 판단','주요 조사결과','사건 전개 개요','개인정보 영향','판단의 핵심 근거','종합 해석','사건 시간축','시각 미확정 중요 사항','판단 적용 범위']
const authoring: InvestigationReportAuthoring = {template_version:'forsic-integrated-v2',status:'drafting',reviewed:false,review_status:'required',groups:groupLabels.map((label,index)=>({id:'group-'+index,label,state: index === 0 ? 'saved' : index === 1 ? 'unavailable' : index === 2 ? 'recheck' : index === 3 ? 'ready' : index === 4 ? 'in_progress' : 'missing',saved_at:index < 5 ? Date.parse('2026-10-20T03:30:00Z')/1000 : null,preview:index < 3 ? [{node_id:'node-'+index,text:'보존된 합성 작성 내용 '+index,omitted_characters:37,status:index === 1 ? 'unavailable' : 'saved',unavailable_reason:index === 1 ? '선택 자료로 확인할 수 없는 범위' : '',omitted_reason_characters:index === 1 ? 9 : 0}] : [],omitted_entries:index === 0 ? 2 : 0}))}

it('shows nine ordered writing groups with distinct saved, unknown, recheck and reviewed states and KST previews',async()=>{
  const {authedFetch}=await import('@/lib/api');vi.mocked(authedFetch).mockClear()
  const host=document.createElement('div');const root=createRoot(host)
  try{
    await act(async()=>root.render(<ForsicReports authoring={authoring} reports={[report]} loading={false} error={null} hasCase/>))
    const groups=[...host.querySelectorAll('.frr-authoring-group')]
    expect(groups).toHaveLength(9)
    expect(groups.map(group=>group.querySelector('h3')?.textContent)).toEqual(groupLabels)
    expect(groups.map(group=>group.querySelector('.frr-authoring-badge')?.textContent)).toEqual(['저장됨','미확인 사항 기재','재확인 필요','검토됨','작성 중','미작성','미작성','미작성','미작성'])
    const time=groups[0].querySelector('time')!
    expect(time.dateTime).toBe('2026-10-20T03:30:00.000Z')
    expect(time.textContent).toContain('KST');expect(time.textContent).toContain('12:30')
    expect(groups[0].querySelector('details')?.open).toBe(false)
    expect(groups[0].querySelector('details')?.textContent).toContain('37자는 미리보기에서 생략')
    expect(groups[0].querySelector('details')?.textContent).toContain('추가 항목 2개')
    expect(groups[1].textContent).toContain('선택 자료로 확인할 수 없는 범위')
    expect(groups[1].textContent).toContain('미확인 사유 9자')
    expect(groups[2].textContent).toContain('이전 저장 내용입니다')
    expect(host.querySelectorAll('.frr-card')).toHaveLength(1)
    expect(host.textContent).not.toContain('검증 완료')
    expect(authedFetch).not.toHaveBeenCalled()
  }finally{await act(async()=>root.unmount())}
})

it('distinguishes unavailable retained progress from missing drafts and shows review findings without a score',async()=>{
  const host=document.createElement('div');const root=createRoot(host)
  try{
    const unknown: InvestigationReportAuthoring={...authoring,status:'unavailable',groups:authoring.groups.map(group=>({...group,state:'unavailable',saved_at:null,preview:[],omitted_entries:0}))}
    await act(async()=>root.render(<ForsicReports authoring={unknown} loading={false} error={null} hasCase/>))
    expect(host.querySelectorAll('.frr-authoring-group')).toHaveLength(9)
    expect([...host.querySelectorAll('.frr-authoring-badge')].every(node=>node.textContent==='조회 불가')).toBe(true)
    expect(host.querySelectorAll('.frr-authoring-preview')).toHaveLength(0)
    expect(host.querySelector('time')).toBeNull()
    await act(async()=>root.render(<ForsicReports authoring={{...authoring,review_status:'needs_revision'}} loading={false} error={null} hasCase/>))
    expect(host.querySelector('.frr-authoring-status')?.textContent).toContain('수정·재확인할 내용')
    expect(host.querySelector('[role="progressbar"]')).toBeNull()
  }finally{await act(async()=>root.unmount())}
})
it('does not claim writing has started when all nine groups are missing',async()=>{
  const host=document.createElement('div');const root=createRoot(host)
  const missing: InvestigationReportAuthoring={...authoring,groups:authoring.groups.map(group=>({...group,state:'missing',saved_at:null,preview:[],omitted_entries:0}))}
  try{
    await act(async()=>root.render(<ForsicReports authoring={missing} loading={false} error={null} hasCase/>))
    expect(host.querySelectorAll('.frr-authoring-group')).toHaveLength(9)
    expect(host.querySelector('.frr-authoring-status')?.textContent).toBe('조사 결과를 정리한 뒤 본문을 작성합니다.')
    for(const state of ['in_progress','saved'] as const){
      await act(async()=>root.render(<ForsicReports authoring={{...missing,groups:missing.groups.map((group,index)=>index===0?{...group,state}:group)}} loading={false} error={null} hasCase/>))
      expect(host.querySelector('.frr-authoring-status')?.textContent).toBe('항목별 본문을 작성하고 있습니다.')
    }
  }finally{await act(async()=>root.unmount())}
})
it('shows the saved title and readable file actions while keeping identities secondary without fetching reports', async () => {
  const {authedFetch}=await import('@/lib/api')
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
  try {
    await act(async()=>root.render(<ForsicReports reports={[report]} loading={false} error={null} hasCase profile="synthetic"/>))
    expect(host.querySelector('.frr-card h3')?.textContent).toBe(report.title)
    expect([...host.querySelectorAll('button')].map(button=>button.textContent)).toEqual(['웹에서 읽기','Word 내려받기'])
    const details=host.querySelector('details')!
    expect(details.open).toBe(false)
    expect(details.textContent).toContain(report.id)
    expect(details.textContent).toContain(report.files[0].name)
    expect(host.querySelector('time')?.dateTime).toBe(report.created_at)
    expect(host.querySelector('time')?.textContent).toContain('2026년 10월 20일')
    expect(authedFetch).not.toHaveBeenCalled()
  }finally{await act(async()=>root.unmount());host.remove()}
})
it('does not invent a report title from an internal filename',async()=>{
  const host=document.createElement('div');const root=createRoot(host)
  try{await act(async()=>root.render(<ForsicReports reports={[{...report,title:''}]} loading={false} error={null} hasCase/>));expect(host.querySelector('.frr-card h3')?.textContent).toBe('제목이 기록되지 않은 보고서')}finally{await act(async()=>root.unmount())}
})

it('uses the same current question explanation and preserves saved version distinctions without fetching or generating',async()=>{
  const {authedFetch}=await import('@/lib/api')
  const question = {id:'Q-shared',version:'1',question:'실행 여부는?',answer:'실행 성공은 아직 확인되지 않았습니다.',assessment:'undetermined',work_state:'active',evidence_ids:[],
    reader_explanation:{status:'provisional' as const,canonical_answer:'실행 성공은 아직 확인되지 않았습니다.',title:'요청과 실행의 구분',paragraphs:[{text:'요청 기록은 있으나 완료 기록이 없어 판단을 유보합니다.',claim_refs:[],source_refs:[],counterevidence_refs:[]}],counterarguments:['다른 작업일 수 있음'],limitations:['완료 기록 미수집'],semantic_approval:false as const}}
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
  try {
    await act(async()=>root.render(<ForsicReports questions={[question]} reports={[{...report,current:false,comparison_status:'changed',changes:[{question_id:'Q-shared',title:'실행 여부는?',kinds:['answer','evidence','limits']}]},{...report,id:'current-version',current:true,comparison_status:'current'}]} loading={false} error={null} hasCase/>))
    expect(host.querySelector('.frr-current')?.textContent).toContain(question.reader_explanation.paragraphs[0].text)
    expect(host.querySelector('.frr-current')?.textContent).toContain('잠정 설명')
    expect(host.querySelector('.frr-current')?.textContent).toContain('완료 기록 미수집')
    expect(host.querySelector('[data-current="false"]')?.textContent).toContain('현재 조사와 달라진 저장본')
    expect(host.querySelector('[data-current="true"]')?.textContent).toContain('현재 조사와 일치하는 저장본')
    expect(host.querySelector('.frr-changes')?.textContent).toContain('현재 답 변경 · 연결 근거 변경 · 한계·반론 변경')
    expect(authedFetch).not.toHaveBeenCalled()
  }finally{await act(async()=>root.unmount());host.remove()}
})

it('keeps the canonical current answer visible and hides stale explanation prose and title',async()=>{
  const {QuestionJudgment}=await import('./ForsicRelationMap')
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
  try {
    await act(async()=>root.render(<QuestionJudgment question={{id:'Q-changed',version:'2',question:'현재 질문',answer:'정정된 현재 답',assessment:'undetermined',work_state:'active',evidence_ids:[],counterarguments:['현재 반론'],limitations:['현재 한계'],reader_explanation:{status:'stale',canonical_answer:'정정된 현재 답',title:'오래된 제목',paragraphs:[{text:'오래된 설명',claim_refs:[],source_refs:[],counterevidence_refs:[]}],counterarguments:['유지해야 할 반론'],limitations:['오래된 설명에만 있는 한계'],semantic_approval:false}}}/>))
    expect(host.textContent).toContain('정정된 현재 답')
    expect(host.textContent).toContain('이전 설명의 갱신이 필요')
    expect(host.textContent).not.toContain('오래된 제목')
    expect(host.textContent).not.toContain('오래된 설명')
    expect(host.textContent).not.toContain('유지해야 할 반론')
    expect(host.textContent).not.toContain('오래된 설명에만 있는 한계')
    expect(host.textContent).toContain('현재 반론')
    expect(host.textContent).toContain('현재 한계')
    await act(async()=>root.render(<QuestionJudgment question={{id:'Q-same',version:'1',question:'동일한 설명',answer:'중복 없이 한 번만 표시하는 답',assessment:'undetermined',work_state:'open',evidence_ids:[],reader_explanation:{status:'provisional',canonical_answer:'중복 없이 한 번만 표시하는 답',paragraphs:[{text:'중복 없이 한 번만 표시하는 답',claim_refs:[],source_refs:[],counterevidence_refs:[]}],counterarguments:[],limitations:[],semantic_approval:false}}}/>))
    expect(host.textContent?.split('중복 없이 한 번만 표시하는 답')).toHaveLength(2)
  }finally{await act(async()=>root.unmount());host.remove()}
})

it('distinguishes authored reports from technical partial records and leaves legacy format unknown',async()=>{
  const host=document.createElement('div');const root=createRoot(host)
  try{
    await act(async()=>root.render(<ForsicReports reports={[{...report,editorial_status:'authored_current'},{...report,id:'partial-record',editorial_status:'technical_partial'},{...report,id:'legacy-record'}]} loading={false} error={null} hasCase/>))
    expect([...host.querySelectorAll('.frr-editorial-status')].map(item=>item.textContent)).toEqual(['독자용 보고서','중간 기록'])
    expect(host.querySelectorAll('.frr-card')[2].querySelector('.frr-editorial-status')).toBeNull()
    expect(host.textContent).not.toContain('검증 완료')
  }finally{await act(async()=>root.unmount())}
})

it('offers lazy Word only by an explicit descriptor and keeps HTML usable during conversion',async()=>{
  const {authedFetch}=await import('@/lib/api');vi.mocked(authedFetch).mockClear();
  vi.mocked(authedFetch).mockReturnValue(new Promise(()=>{}));
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
  const descriptor={name:'integrated.docx',url:`/api/plugins/forsic/report-bundles/${'a'.repeat(64)}/word?case_id=synthetic`,method:'POST' as const};
  try{
    await act(async()=>root.render(<ForsicReports reports={[{...report,files:[report.files[0]],word_export:descriptor}]} loading={false} error={null} hasCase profile="synthetic"/>));
    expect(authedFetch).not.toHaveBeenCalled();
    const buttons=[...host.querySelectorAll<HTMLButtonElement>('.frr-files button')];
    expect(buttons.map(button=>button.textContent)).toEqual(['웹에서 읽기','Word 내려받기']);
    await act(async()=>buttons[1].click());
    expect(authedFetch).toHaveBeenCalledTimes(1);expect(vi.mocked(authedFetch).mock.calls[0][1]?.method).toBe('POST');
    expect(buttons[1].textContent).toBe('Word 변환 중…');expect(buttons[1].disabled).toBe(true);expect(buttons[0].disabled).toBe(false);
    await act(async()=>root.render(<ForsicReports reports={[{...report,word_export:descriptor}]} loading={false} error={null} hasCase profile="synthetic"/>));
    expect([...host.querySelectorAll('.frr-files button')].map(button=>button.textContent)).toEqual(['웹에서 읽기','Word 내려받기']);
    expect(authedFetch).toHaveBeenCalledTimes(1);
  }finally{await act(async()=>root.unmount());host.remove();vi.mocked(authedFetch).mockReset();}
});

it('shares an exact versioned judgment card with freshness and original-source action', async () => {
  const {QuestionJudgment} = await import('./ForsicRelationMap')
  const reference = {kind:'source',id:'source-synthetic',version:'old-version'}
  const question = {id:'Q-synthetic',version:'question-v3',question:'Synthetic question',answer:'Current retained answer',assessment:'undetermined',work_state:'active',evidence_ids:[],evidence_refs:[reference],limitations:['Important remaining limit'],reader_explanation:{status:'stale' as const,canonical_answer:'Old answer',paragraphs:[{text:'Outdated prose',claim_refs:[],source_refs:[],counterevidence_refs:[]}],counterarguments:[],limitations:[],semantic_approval:false as const}}
  const before=JSON.stringify(question)
  const onSource=vi.fn()
  const host=document.createElement('div');const root=createRoot(host)
  try {
    await act(async()=>root.render(<QuestionJudgment question={question} onSource={onSource}/>))
    expect(host.querySelector('.fi-readable-judgment')?.getAttribute('data-freshness')).toBe('stale')
    expect(host.textContent?.split('Current retained answer')).toHaveLength(2)
    expect(host.textContent).not.toContain('Outdated prose')
    expect(host.textContent).toContain('Important remaining limit')
    expect(host.textContent).toContain('question-v3')
    expect(host.textContent).toContain('old-version')
    await act(async()=>host.querySelector<HTMLButtonElement>('.fi-reference')!.click())
    expect(onSource).toHaveBeenCalledWith(reference)
    expect(JSON.stringify(question)).toBe(before)
  } finally {await act(async()=>root.unmount())}
})

it('keeps the latest authored draft visible beside old saved files and requires explicit final selection', async () => {
  const {authedFetch} = await import('@/lib/api'); vi.mocked(authedFetch).mockClear()
  const host = document.createElement('div'); const root = createRoot(host)
  const workspace = {phase:'review' as const,stage_label:'본문 검토 대기',next_step:'저장된 본문을 검토합니다.',completed_groups:5,total_groups:9,saved_report_id:report.id,final_report_id:null,draft:{revision:8,draft_revision:7,saved_at:'2026-10-21T01:00:00Z',current:true,review_status:'required',content_complete:true,question_ids:[]}}
  try {
    await act(async () => root.render(<ForsicReports workspace={workspace} authoring={authoring} reports={[{...report,editorial_status:'authored_current',draft_revision:3,draft_relation:'older'}]} loading={false} error={null} hasCase lastCheckedAt="2026-10-21T01:10:00Z"/>))
    const draft = host.querySelector('[aria-label="작성 중인 최신 초안"]')!
    expect(draft.closest('details')).toBeNull()
    expect(draft.textContent).toContain('초안 v7')
    expect(draft.textContent).toContain('보존된 합성 작성 내용 0')
    expect(host.querySelector('[aria-label="보고서 현재 단계"]')?.textContent).toContain('작성 항목 5 / 9개 완료')
    expect(host.querySelector('[aria-label="보고서 현재 단계"]')?.textContent).toContain('마지막 성공')
    expect(host.querySelector('[aria-label="저장된 보고서"]')?.textContent).toContain('최신 초안보다 이전')
    expect(host.querySelector('[aria-label="최종본"]')?.textContent).toContain('아직 최종본으로 선택된 보고서가 없습니다.')
    await act(async () => root.render(<ForsicReports workspace={{...workspace,phase:'final_selected',final_report_id:report.id}} reports={[{...report,lifecycle:'final',finalization:{receipt_id:'receipt-synthetic',selected_at:'2026-10-21T01:20:00Z',independent_review_current:true,layout_status:'complete'}}]} loading={false} error={null} hasCase/>))
    expect(host.querySelector('[aria-label="최종본"] .frr-card')?.textContent).toContain(report.title)
    expect(host.querySelector('[aria-label="최종본"]')?.textContent).not.toContain('아직 최종본')
    expect(authedFetch).not.toHaveBeenCalled()
  } finally {await act(async () => root.unmount())}
})

it('places compact draft, saved and final states before expandable writing groups and preserves fallback prose', async () => {
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<ForsicReports authoring={{...authoring,status:'unavailable',draft_revision:9}} reports={[{...report,lifecycle:'final',draft_revision:3,draft_relation:'older',finalization:{receipt_id:'synthetic',selected_at:'2026-10-21T01:00:00Z',independent_review_current:false,layout_status:'required'}}]} loading={false} error={null} hasCase/>))
    const outer = host.querySelector('.forsic-readable-reports')!
    const sections = [...outer.children]
    const draft = host.querySelector('[aria-label="작성 중인 최신 초안"]')!
    const saved = host.querySelector('[aria-label="저장된 보고서"]')!
    const final = host.querySelector('[aria-label="최종본"]')!
    const groups = host.querySelector<HTMLDetailsElement>('.frr-authoring-section')!
    expect(sections.indexOf(draft)).toBeLessThan(sections.indexOf(saved))
    expect(sections.indexOf(saved)).toBeLessThan(sections.indexOf(final))
    expect(sections.indexOf(final)).toBeLessThan(sections.indexOf(groups))
    expect(groups.open).toBe(false)
    expect(draft.textContent).toContain('보존된 합성 작성 내용 0')
    expect(draft.textContent).toContain('현재 작성 상태 미확인')
    expect(groups.querySelector('.frr-authoring-badge')?.textContent).toContain('저장 내용 있음')
    expect(final.textContent).toContain('현재 초안보다 이전 버전')
    expect(final.textContent).toContain('독립 검토 현재 유효성 미확인')
    expect(final.textContent).toContain('문서 배치 검토 필요')
    expect(final.textContent).toContain('최종 선택은 검토 통과를 뜻하지 않습니다')
  } finally {await act(async () => root.unmount())}
})

it('shows the three summary tiles above details with accurate partial inventory and seconds on check time', async () => {
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<ForsicReports reports={[{...report,draft_revision:3,draft_relation:'older'}]} authoring={{...authoring,draft_revision:9,saved_at:'2026-10-21T01:00:00Z'}} reportCount={25} omittedReportCount={24} lastCheckedAt="2026-10-21T01:10:20Z" loading={false} error={null} hasCase/>))
    const tiles = [...host.querySelectorAll('.frr-overview > section')]
    expect(tiles).toHaveLength(3)
    expect(tiles[0].textContent).toContain('초안 v9')
    expect(tiles[1].textContent).toContain('초안 v3 저장본')
    expect(tiles[1].textContent).toContain('현재 초안보다 이전')
    expect(tiles[2].textContent).toContain('선택된 최종본 없음')
    expect(host.querySelector('.frr-inventory')?.textContent).toBe('저장 기록 25개 중 1개 조회')
    expect(host.querySelector('.frr-lifecycle time')?.textContent).toContain('10:10:20')
    const children = [...host.querySelector('.forsic-readable-reports')!.children]
    expect(children.indexOf(host.querySelector('.frr-overview')!)).toBeLessThan(children.indexOf(host.querySelector('.frr-draft')!))
  } finally {await act(async () => root.unmount())}
})
