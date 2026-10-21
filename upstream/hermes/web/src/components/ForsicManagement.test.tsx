// @vitest-environment jsdom
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {ForsicManagement,type ManagementSection} from './ForsicManagement';
import type {ManagementSnapshot} from '@/lib/forsic-management';
const fetchJSON=vi.hoisted(()=>vi.fn());
vi.mock('@/lib/api',()=>({fetchJSON,authedFetch:vi.fn()}));
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);
const fixture=():ManagementSnapshot=>({revision:'r1',local_admin:true,environments:{default_environment_id:'env-new',case:{revision:'case-r2',environment_id:'env-old',extensions:{},pending:false},items:[{id:'env-new',status:'ready',tools:[{name:'parser',version:'1.2',method:'apt',update_supported:true}]},{id:'env-old',status:'ready'}],updates:[]},packs:{items:[{id:'local/parser',name:'분석 팩',version:'1',sha256:'hash1',kind:'tool',tools:[{name:'parse'}],skills:[{name:'guide',requires_tools:['parse']}],dependencies:['parser'],validation:{installation:'installed',fixture:'not_observed'}}],selection:{revision:'sel1',packs:{'local/parser':{version:'1',sha256:'hash1',enabled:true}}}},builder:{jobs:[{id:'build-1',status:'failed',stage:'fixture',source:{commit:'abc'},error:'기능 출력 불일치',native_session_id:'session-build',fixture_test:{status:'failed'}}]},jobs:[]});
let dispose=()=>{};
beforeEach(()=>{fetchJSON.mockImplementation(async(_url:string,init?:RequestInit)=>init?.method==='POST'?{status:'pending',snapshot:fixture()}:fixture());});
afterEach(()=>{dispose();document.body.innerHTML='';fetchJSON.mockReset();vi.useRealTimers();});
async function render(profile='sample',section?:ManagementSection,onSectionChange=vi.fn()){
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 await act(async()=>root.render(<ForsicManagement profile={profile} caseId="case-a" section={section} onSectionChange={onSectionChange}/>));dispose=()=>act(()=>root.unmount());
 const click=async(text:string)=>act(async()=>{const node=[...host.querySelectorAll<HTMLButtonElement>('button')].find(node=>node.textContent===text);if(!node)throw new Error(text);node.click();});
 const type=async(selector:string,value:string)=>act(async()=>{const node=host.querySelector<HTMLInputElement|HTMLTextAreaElement>(selector)!;const prototype=node instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value')!.set!.call(node,value);node.dispatchEvent(new Event('input',{bubbles:true}));});
 return {host,click,type,onSectionChange,rerender:async(profile:string)=>act(async()=>root.render(<ForsicManagement profile={profile} caseId="case-a" section={section} onSectionChange={onSectionChange}/>)),selectSection:async(next:ManagementSection)=>act(async()=>root.render(<ForsicManagement profile={profile} caseId="case-a" section={next} onSectionChange={onSectionChange}/>))};
}
function posted(){return fetchJSON.mock.calls.filter(call=>call[1]?.method==='POST'&&typeof call[1]?.body==='string').map(call=>JSON.parse(call[1].body));}
const proposalFixture=()=>({id:'proposal-a',revision:3,status:'review_pending',draft_sha256:'reviewed-hash',draft:{pack_id:'local.workflow',version:'1.0.0',name:'workflow',description:'반복 절차',skill_md:'General procedure',requires_tools:['forsic_shell']},reason:'반복 시행착오 감소',applicability:'선택 입력',limitations:'전체 범위 아님',diff:'changed',target:null,origin:{case_id:'case-a',receipts:[{evidence_id:'receipt-a',sha256:'source-hash',tool:'forsic_shell'}]},validation:{structure:'draft_checked',functional:'not_observed'},publication:{status:'not_saved'},activation:{status:'not_requested'}});
it('publishes only the reviewed exact skill revision and never auto-activates',async()=>{
 const value=fixture();value.skill_proposals={items:[proposalFixture()]};fetchJSON.mockResolvedValue(value);
 const {host,click}=await render();await click('스킬 제안');expect(posted()).toHaveLength(0);expect(host.textContent).toContain('실제 효과 미확인');await click('검토한 스킬 저장');expect(posted()).toHaveLength(0);await click('검토한 변경 실행');expect(posted()[0]).toMatchObject({action:'skill.publish',proposal_id:'proposal-a',proposal_revision:3,reviewed_sha256:'reviewed-hash'});expect(posted().some(body=>body.action==='skill.activate')).toBe(false);
});
it('requires a changed skill draft to be saved and reviewed before publication',async()=>{
 const value=fixture();value.skill_proposals={items:[proposalFixture()]};fetchJSON.mockResolvedValue(value);
 const {host,click,type}=await render();await click('스킬 제안');await type('[aria-label="workflow 절차 내용"]','Changed procedure');expect([...host.querySelectorAll<HTMLButtonElement>('button')].find(node=>node.textContent==='검토한 스킬 저장')?.disabled).toBe(true);await click('수정한 초안 저장 검토');await click('검토한 변경 실행');expect(posted()[0]).toMatchObject({action:'skill.draft',proposal_revision:3,draft:{skill_md:'Changed procedure'}});
});
it('shows builtin proposals as review-only and enforces remote read-only management',async()=>{
 const value=fixture();value.local_admin=false;value.skill_proposals={items:[{...proposalFixture(),target:{builtin:'source-triage',sha256:'builtin-hash'}}]};fetchJSON.mockResolvedValue(value);
 const {host,click}=await render();await click('스킬 제안');expect(host.textContent).toContain('배포된 스킬을 자동 교체하지 않습니다');expect([...host.querySelectorAll<HTMLButtonElement>('button')].find(node=>node.textContent==='검토한 스킬 저장')?.disabled).toBe(true);expect(host.querySelector<HTMLTextAreaElement>('[aria-label="workflow 절차 내용"]')?.disabled).toBe(true);expect(posted()).toHaveLength(0);
});
it('separates case pin from default and applies only the reviewed change',async()=>{
 const {host,click}=await render();expect(fetchJSON).toHaveBeenCalledWith('/api/plugins/forsic/management?profile=sample&case_id=case-a');
 expect(host.textContent).toContain('env-new');expect(host.textContent).toContain('env-old');
 await click('새 사건 기본값 검토');expect(posted()).toHaveLength(0);await click('검토한 변경 실행');
 expect(posted()[0]).toMatchObject({action:'env.set_default',environment_id:'env-new',expected_revision:'r1',case_id:'case-a'});expect(posted()[0].operation_id).toBeTruthy();expect(host.textContent).toContain('적용 대기');
 expect(posted().every(body=>!String(body.action).includes('pause'))).toBe(true);
});
it('reviews exact environment plans and does not build on editing',async()=>{
 const {host,click,type}=await render();await type('textarea[aria-label="환경 변경 계획"]','{"apt":{"parser":"1.3"},"checks":[{"name":"synthetic","argv":["parser","fixture.txt"],"expected_stdout":"result"}]}');
 await click('새 환경 준비 계획 검토');expect(posted()).toHaveLength(0);expect(host.querySelector('[aria-label="변경 검토"]')?.textContent).toContain('1.3');await click('검토한 변경 실행');expect(posted()[0]).toMatchObject({action:'env.prepare',plan:{base_environment_id:'env-new',apt:{parser:'1.3'}}});
});
it('retains reviewed input on conflict and suppresses duplicate clicks',async()=>{
 const {host,click}=await render();await click('현재 사건 적용 검토');let reject!:(value:unknown)=>void;fetchJSON.mockReturnValueOnce(new Promise((_,fail)=>{reject=fail;}));await click('검토한 변경 실행');await click('검토한 변경 실행');expect(posted()).toHaveLength(1);await act(async()=>reject({status:409}));expect(host.querySelector('[role=alert]')?.textContent).toContain('구성이 바뀌었거나');expect(host.querySelector('[aria-label="변경 검토"]')).not.toBeNull();
});
it('toggles an exact pack version within the selected scope without changing other packs',async()=>{
 const {host,click}=await render();await click('파일 허브');await click('이 버전 끄기 검토');await click('검토한 변경 실행');expect(posted()[0]).toMatchObject({action:'hub.select',scope:'default',selection:{'local/parser':{version:'1',sha256:'hash1',enabled:false}}});expect(host.textContent).toContain('실행 검증 상태');
});
it('creates a builder request and requires a separate explicit native start',async()=>{
 const {host,click,type}=await render();await click('저장소 제작기');await type('[aria-label="공개 저장소 URL"]','https://example.com/tool.git');await type('[aria-label="필요한 기능"]','파일을 JSON으로 변환');await click('제작 요청 저장');expect(posted()[0]).toMatchObject({action:'builder.create',repository_url:'https://example.com/tool.git',request:'파일을 JSON으로 변환'});expect(posted()).toHaveLength(1);expect(host.textContent).toContain('기능 출력 불일치');expect(host.textContent).toContain('session-build');await click('제작 시작');expect(posted()[1]).toMatchObject({action:'builder.start',job_id:'build-1'});
});
it('shows offline update failures instead of claiming no updates',async()=>{
 const {host,click}=await render();fetchJSON.mockRejectedValueOnce(new Error('후보 확인 실패: 오프라인'));await click('업데이트 확인');expect(host.querySelector('[role=alert]')?.textContent).toContain('오프라인');expect(host.textContent).not.toContain('업데이트 없음');
});
it('rejects late results when the management profile changes',async()=>{
 const {host,click,rerender}=await render();let resolve!:(value:unknown)=>void;fetchJSON.mockReturnValueOnce(new Promise(done=>{resolve=done;}));await click('업데이트 확인');await rerender('second');await act(async()=>resolve({status:'completed',snapshot:{...fixture(),local_admin:false}}));expect(host.textContent).not.toContain('읽기 전용');expect(host.textContent).not.toContain('요청 결과');
});
it('renders remote management read-only',async()=>{
 fetchJSON.mockResolvedValue({...fixture(),local_admin:false});const {host}=await render();expect(host.textContent).toContain('읽기 전용');expect([...host.querySelectorAll<HTMLButtonElement>('button')].filter(node=>['업데이트 확인','새 사건 기본값 검토'].includes(node.textContent||'')).every(node=>node.disabled)).toBe(true);expect(posted()).toHaveLength(0);
});

it('imports only after preview and binds registration to the canonical reviewed hash',async()=>{
 const {host,click}=await render();await click('파일 허브');
 const input=host.querySelector<HTMLInputElement>('[aria-label="확장팩 파일"]')!;Object.defineProperty(input,'files',{value:[new File(['pack'],'pack.zip')]});await act(async()=>input.dispatchEvent(new Event('change',{bubbles:true})));
 fetchJSON.mockResolvedValueOnce({upload_id:'u1',sha256:'archive-hash',bytes:4,name:'pack.zip'}).mockResolvedValueOnce({preview:{sha256:'canonical-hash',manifest:{id:'local/parser',version:'1'}}});
 await click('내용·호환성 미리보기');expect(posted().some(body=>body.action==='hub.import')).toBe(false);expect(host.textContent).toContain('canonical-hash');await click('검증·등록 검토');await click('검토한 변경 실행');expect(posted().at(-1)).toMatchObject({action:'hub.import',upload_id:'u1',expected_sha256:'canonical-hash'});
});
it('reviews a logical tool toggle while preserving exact pack identity',async()=>{
 const {host,click}=await render();await click('파일 허브');await act(async()=>host.querySelector<HTMLInputElement>('[aria-label="분석 팩 parse 사용"]')!.click());await click('검토한 변경 실행');expect(posted()[0]).toMatchObject({action:'hub.select',selection:{'local/parser':{version:'1',sha256:'hash1',enabled:true,tools:{parse:false}}}});
});
it('loads actual checked apt candidates into a plan without preparing an environment',async()=>{
 const value=fixture();value.environments.updates=[{environment_id:'env-new',apt:{state:'checked',packages:{parser:{installed:'1.2',candidate:'1.3'}}}}];fetchJSON.mockResolvedValue(value);
 const {host,click}=await render();await act(async()=>host.querySelector<HTMLInputElement>('[aria-label="parser 1.3 업데이트 선택"]')!.click());await click('선택한 후보 1개를 변경 계획에 반영');expect(JSON.parse(host.querySelector<HTMLTextAreaElement>('[aria-label="환경 변경 계획"]')!.value)).toMatchObject({apt:{parser:'1.3'},checks:[]});expect(posted()).toHaveLength(0);
});
it('offers an explicit retry for pending application without interrupting a native turn',async()=>{
 const value=fixture();value.runtime_pending=true;value.environments.case!.pending={environment_id:'env-new'};fetchJSON.mockResolvedValue(value);
 const {click}=await render();await click('대기 중인 구성 적용 다시 시도');expect(posted()[0]).toMatchObject({action:'env.apply_pending'});expect(posted().every(body=>!body.action.includes('pause')&&!body.action.includes('interrupt'))).toBe(true);
});
it('preserves the real backend cause for a rejected plan while keeping reviewable inputs',async()=>{
 const {host,click,type}=await render();await type('[aria-label="환경 변경 계획"]','{"apt":{"bad name":"1"},"checks":[{"name":"synthetic","argv":["parser","fixture"],"expected_stdout":"result"}]}');await click('새 환경 준비 계획 검토');fetchJSON.mockRejectedValueOnce(Object.assign(new Error('apt dependencies need exact safe names and versions'),{status:409}));await click('검토한 변경 실행');expect(host.querySelector('[role=alert]')?.textContent).toContain('apt dependencies need exact safe names and versions');expect(host.querySelector('[aria-label="변경 검토"]')).not.toBeNull();
});

it('lets the settings page own categories while retaining builder and environment drafts',async()=>{
 const {host,type,selectSection}=await render('sample','builder');expect(host.querySelector('nav[aria-label="관리 화면"]')).toBeNull();
 await type('[aria-label="공개 저장소 URL"]','https://example.com/draft.git');await type('[aria-label="필요한 기능"]','검토할 제작 요청');
 await selectSection('environments');await type('[aria-label="환경 변경 계획"]','{"checks":[]}');await selectSection('builder');
 expect(host.querySelector<HTMLInputElement>('[aria-label="공개 저장소 URL"]')!.value).toBe('https://example.com/draft.git');expect(host.querySelector<HTMLTextAreaElement>('[aria-label="필요한 기능"]')!.value).toBe('검토할 제작 요청');
 await selectSection('environments');expect(host.querySelector<HTMLTextAreaElement>('[aria-label="환경 변경 계획"]')!.value).toBe('{"checks":[]}');expect(posted()).toHaveLength(0);
});
it('keeps a pending review in its originating category and executes the exact reviewed payload',async()=>{
 const {host,click,selectSection,onSectionChange}=await render('sample','environments');await click('현재 사건 적용 검토');
 const panel=host.querySelector('[aria-label="변경 검토"]')!;expect(document.activeElement).toBe(panel);expect(panel.querySelector('details')!.open).toBe(false);expect(panel.textContent).toContain('현재 사건에 환경 env-new');
 await selectSection('packs');expect(host.querySelector('[aria-label="변경 검토"]')).toBeNull();expect(host.textContent).toContain('분석 환경에 실행 전 검토가 남아');expect([...host.querySelectorAll('button')].some(button=>button.textContent==='검토한 변경 실행')).toBe(false);expect(posted()).toHaveLength(0);
 await click('검토로 돌아가기');expect(onSectionChange).toHaveBeenCalledWith('environments');await selectSection('environments');await click('검토한 변경 실행');expect(posted()[0]).toMatchObject({action:'env.apply',environment_id:'env-new',expected_revision:'r1',case_id:'case-a',reason:'관리 화면에서 환경 전환 요청'});
});
it('keeps pack preview identity separate from environment uploads across category changes',async()=>{
 const {host,click,selectSection}=await render('sample','packs');const input=host.querySelector<HTMLInputElement>('[aria-label="확장팩 파일"]')!;Object.defineProperty(input,'files',{value:[new File(['pack'],'pack.zip')]});await act(async()=>input.dispatchEvent(new Event('change',{bubbles:true})));
 fetchJSON.mockResolvedValueOnce({upload_id:'pack-upload',sha256:'archive-hash',bytes:4,name:'pack.zip'}).mockResolvedValueOnce({preview:{sha256:'reviewed-pack-hash'}});await click('내용·호환성 미리보기');
 await selectSection('environments');expect([...host.querySelectorAll<HTMLButtonElement>('button')].find(button=>button.textContent==='환경 파일 업로드')!.disabled).toBe(true);expect(host.textContent).not.toContain('reviewed-pack-hash');
 await selectSection('packs');expect(host.textContent).toContain('reviewed-pack-hash');await click('검증·등록 검토');await click('검토한 변경 실행');expect(posted().at(-1)).toMatchObject({action:'hub.import',upload_id:'pack-upload',expected_sha256:'reviewed-pack-hash'});
});
it('preserves an edited skill draft when another settings category is selected',async()=>{
 const value=fixture();value.skill_proposals={items:[proposalFixture()]};fetchJSON.mockResolvedValue(value);const {host,type,click,selectSection}=await render('sample','skills');
 await type('[aria-label="workflow 절차 내용"]','Retained draft');await selectSection('builder');await selectSection('skills');expect(host.querySelector<HTMLTextAreaElement>('[aria-label="workflow 절차 내용"]')!.value).toBe('Retained draft');await click('수정한 초안 저장 검토');await click('검토한 변경 실행');expect(posted()[0]).toMatchObject({action:'skill.draft',proposal_revision:3,draft:{skill_md:'Retained draft'}});
});
it('asks the owning settings page to open a generated dependency plan without preparing it',async()=>{
 const {host,click,selectSection,onSectionChange}=await render('sample','packs');fetchJSON.mockResolvedValueOnce({status:'preview',plan:{apt:{parser:'1.3'},checks:[]}});await click('의존성으로 환경 계획 만들기');expect(onSectionChange).toHaveBeenCalledWith('environments');await selectSection('environments');expect(JSON.parse(host.querySelector<HTMLTextAreaElement>('[aria-label="환경 변경 계획"]')!.value)).toMatchObject({apt:{parser:'1.3'}});expect(posted()).toHaveLength(1);expect(posted()[0]).toMatchObject({action:'env.plan_dependencies',id:'local/parser',version:'1',expected_sha256:'hash1'});
});

it('uses ordinary standalone category buttons and retains reviews when navigating away',async()=>{
 const {host,click}=await render();expect(host.querySelector('[role="tablist"]')).toBeNull();const navigation=host.querySelector('nav[aria-label="관리 화면"]')!;expect(navigation.querySelector('button[aria-pressed=true]')!.textContent).toBe('분석 환경');
 await click('새 사건 기본값 검토');await click('파일 허브');expect(host.querySelector('[aria-label="변경 검토"]')).toBeNull();expect(posted()).toHaveLength(0);await click('분석 환경');expect(host.querySelector('[aria-label="변경 검토"]')!.textContent).toContain('이미 생성된 사건은 바뀌지 않습니다');await click('검토한 변경 실행');expect(posted()[0]).toMatchObject({action:'env.set_default',environment_id:'env-new'});
});

it('preserves unsaved environment functional-check fields and candidate selection across categories',async()=>{
 const value=fixture();value.environments.updates=[{environment_id:'env-new',checked_at:1,apt:{state:'checked',packages:{parser:{installed:'1.2',candidate:'1.3'}}}}];fetchJSON.mockResolvedValue(value);
 const {host,type,click,selectSection}=await render('sample','environments');
 const editor=host.querySelector<HTMLDetailsElement>('.forsic-functional-check-editor')!;
 await act(async()=>editor.querySelector('summary')!.click());
 await type('[aria-label="기능 검사 이름"]','Retained check');await type('[aria-label="기능 검사 실행 파일"]','parser');await type('[aria-label="기능 검사 인수"]','fixture.txt');await type('[aria-label="기능 검사 기대 출력"]','retained output');
 const candidate=host.querySelector<HTMLInputElement>('[aria-label="parser 1.3 업데이트 선택"]')!;await act(async()=>candidate.click());
 const calls=fetchJSON.mock.calls.length;await selectSection('packs');expect(editor.closest('[hidden]')).not.toBeNull();await selectSection('environments');
 expect(host.querySelector('.forsic-functional-check-editor')).toBe(editor);expect(editor.open).toBe(true);expect(candidate.checked).toBe(true);
 expect(host.querySelector<HTMLInputElement>('[aria-label="기능 검사 이름"]')!.value).toBe('Retained check');expect(host.querySelector<HTMLInputElement>('[aria-label="기능 검사 실행 파일"]')!.value).toBe('parser');expect(host.querySelector<HTMLTextAreaElement>('[aria-label="기능 검사 인수"]')!.value).toBe('fixture.txt');expect(host.querySelector<HTMLTextAreaElement>('[aria-label="기능 검사 기대 출력"]')!.value).toBe('retained output');expect(fetchJSON).toHaveBeenCalledTimes(calls);
 await click('이 기능 검사를 계획에 추가');await click('선택한 후보 1개를 변경 계획에 반영');await click('새 환경 준비 계획 검토');await click('검토한 변경 실행');
 expect(posted()[0]).toMatchObject({action:'env.prepare',plan:{apt:{parser:'1.3'},checks:[{name:'Retained check',argv:['parser','fixture.txt'],expected_stdout:'retained output'}]}});
});
