// @vitest-environment jsdom
import {act,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {afterEach,expect,it} from 'vitest';
import {ForsicEnvironmentPlan} from './ForsicEnvironmentPlan';
Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
let dispose=()=>{};
afterEach(()=>{dispose();document.body.innerHTML='';});
async function render(sources:unknown[],initial=''){
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 function Harness(){const [plan,setPlan]=useState(initial);return <><ForsicEnvironmentPlan sources={sources} environmentId="env-a" planText={plan} onPlanChange={setPlan} disabled={false}/><output>{plan}</output></>;}
 await act(async()=>root.render(<Harness/>));dispose=()=>act(()=>root.unmount());
 const click=async(name:string)=>act(async()=>{const node=[...host.querySelectorAll<HTMLButtonElement>('button')].find(button=>button.textContent===name);if(!node)throw new Error(name);node.click();});
 const check=async(name:string)=>act(async()=>host.querySelector<HTMLInputElement>(`[aria-label="${name}"]`)!.click());
 const type=async(name:string,value:string)=>act(async()=>{const node=host.querySelector<HTMLInputElement|HTMLTextAreaElement>(`[aria-label="${name}"]`)!;const prototype=node.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value')!.set!.call(node,value);node.dispatchEvent(new Event('input',{bubbles:true}));});
 const plan=()=>JSON.parse(host.querySelector('output')!.textContent||'{}');return {host,click,check,type,plan};
}
it('selects only the latest actual receipt for the chosen environment and preserves existing checks',async()=>{
 const sha='a'.repeat(64);const {host,click,check,plan}=await render([{environment_id:'env-other',checked_at:999,apt:{state:'checked',packages:{wrong:{installed:'1',candidate:'9'}}}},{environment_id:'env-a',checked_at:1,apt:{state:'checked',packages:{parser:{installed:'1',candidate:'2'}}}},{environment_id:'env-a',checked_at:2,apt:{state:'checked',packages:{parser:{installed:'1',candidate:'3',dependency_plan:'Inst lib-fixture 2'}}},python:{state:'checked',packages:{duckdb:{state:'checked',current:'1',candidate:'2',wheels:[{filename:'duckdb.whl',sha256:sha}],compatibility:'candidate only'}}}}],JSON.stringify({checks:[{name:'existing',argv:['parser','fixture'],expected_stdout:'rows'}],base_image:'kept'}));
 expect(host.textContent).not.toContain('wrong');expect(host.querySelector('[aria-label="parser 2 업데이트 선택"]')).toBeNull();await check('parser 3 업데이트 선택');await check('duckdb 2 업데이트 선택');await click('선택한 후보 2개를 변경 계획에 반영');expect(plan()).toMatchObject({apt:{parser:'3'},python:[{name:'duckdb',version:'2',sha256:[sha]}],checks:[{name:'existing',argv:['parser','fixture'],expected_stdout:'rows'}],base_image:'kept'});expect(host.textContent).toContain('전이 의존성');
});
it('keeps unavailable Python hashes and unaudited release assets unselectable without hiding offline failures',async()=>{
 const {host}=await render([{environment_id:'env-a',apt:{state:'failed_or_offline',packages:{}},python:{state:'checked',packages:{parser:{state:'checked',current:'1',candidate:'2',wheels:[]}}},release_binaries:{state:'checked',packages:{hayabusa:{state:'checked',candidate:'v5',assets:[{filename:'tool.zip',digest:'sha256:'+'a'.repeat(64)}]}}}}]);expect(host.textContent).toContain('확인 실패·오프라인');expect(host.textContent).not.toContain('업데이트 없음');expect(host.querySelector<HTMLInputElement>('[aria-label="parser 2 업데이트 선택"]')!.disabled).toBe(true);expect(host.querySelector<HTMLInputElement>('[aria-label="hayabusa v5 업데이트 선택"]')!.disabled).toBe(true);expect(host.textContent).toContain('binary_lock');
});
it('accepts readable functional-check fields as literal argv and exact expected output',async()=>{
 const {click,type,plan}=await render([]);await type('기능 검사 이름','합성 줄 수');await type('기능 검사 실행 파일','python');await type('기능 검사 인수','-c\nprint("synthetic result")');await type('기능 검사 기대 출력','synthetic result\n');await click('이 기능 검사를 계획에 추가');expect(plan().checks).toEqual([{name:'합성 줄 수',argv:['python','-c','print("synthetic result")'],expected_stdout:'synthetic result\n'}]);await click('검사 1 제거');expect(plan().checks).toEqual([]);
});
it('rejects help and version commands as functional checks without inventing replacement inputs',async()=>{
 const {host,click,type,plan}=await render([]);await type('기능 검사 이름','설치');await type('기능 검사 실행 파일','parser');await type('기능 검사 인수','--version');await type('기능 검사 기대 출력','2');await click('이 기능 검사를 계획에 추가');expect(host.querySelector('[role=alert]')?.textContent).toContain('실제 합성 입력');expect(plan()).toEqual({});
});

it('keeps the advanced functional-check editor collapsed until opened and shows the registered count',async()=>{
 const {host,type,click,plan}=await render([]);
 const editor=host.querySelector<HTMLDetailsElement>('.forsic-functional-check-editor')!;
 expect(editor.open).toBe(false);expect(editor.querySelector('summary')!.textContent).toContain('등록된 검사 0개');
 await act(async()=>editor.querySelector('summary')!.click());expect(editor.open).toBe(true);
 await type('기능 검사 이름','literal check');await type('기능 검사 실행 파일','parser');await type('기능 검사 인수','synthetic.txt');await type('기능 검사 기대 출력','expected rows');
 await click('이 기능 검사를 계획에 추가');expect(plan().checks).toEqual([{name:'literal check',argv:['parser','synthetic.txt'],expected_stdout:'expected rows'}]);expect(editor.querySelector('summary')!.textContent).toContain('등록된 검사 1개');
 await act(async()=>editor.querySelector('summary')!.click());expect(editor.open).toBe(false);expect(plan().checks).toHaveLength(1);
});
