// @vitest-environment jsdom
import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect,it,vi} from 'vitest'
import {ForsicReports} from './ForsicReports'
import type {InvestigationReport} from '@/lib/forsic-investigation'
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
vi.mock('@/lib/api', () => ({authedFetch:vi.fn()}))
const report: InvestigationReport = {id:'report-immutable-1234',name:'report-internal-name',title:'합성 자료의 실행 흔적 검토',created_at:'2026-10-20T03:30:00Z',session_id:'session-internal-id',files:[{name:'report-1234.html',url:'/api/plugins/forsic/reports/1234/report.html'},{name:'report-1234.docx',url:'/api/plugins/forsic/reports/1234/report.docx'}]}
it('shows the saved title and readable file actions while keeping identities secondary without fetching reports', async () => {
  const {authedFetch}=await import('@/lib/api')
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
  try {
    await act(async()=>root.render(<ForsicReports reports={[report]} loading={false} error={null} hasCase profile="synthetic"/>))
    expect(host.querySelector('h3')?.textContent).toBe(report.title)
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
  try{await act(async()=>root.render(<ForsicReports reports={[{...report,title:''}]} loading={false} error={null} hasCase/>));expect(host.querySelector('h3')?.textContent).toBe('제목이 기록되지 않은 보고서')}finally{await act(async()=>root.unmount())}
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
    await act(async()=>root.render(<QuestionJudgment question={{id:'Q-changed',version:'2',question:'현재 질문',answer:'정정된 현재 답',assessment:'undetermined',work_state:'active',evidence_ids:[],reader_explanation:{status:'stale',canonical_answer:'정정된 현재 답',title:'오래된 제목',paragraphs:[{text:'오래된 설명',claim_refs:[],source_refs:[],counterevidence_refs:[]}],counterarguments:['유지해야 할 반론'],limitations:['남은 한계'],semantic_approval:false}}}/>))
    expect(host.textContent).toContain('정정된 현재 답')
    expect(host.textContent).toContain('이전 설명의 갱신이 필요')
    expect(host.textContent).not.toContain('오래된 제목')
    expect(host.textContent).not.toContain('오래된 설명')
    expect(host.textContent).toContain('유지해야 할 반론')
    expect(host.textContent).toContain('남은 한계')
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
