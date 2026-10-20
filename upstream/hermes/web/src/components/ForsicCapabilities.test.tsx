// @vitest-environment jsdom
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {ForsicCapabilities, type CapabilitySnapshot} from './ForsicCapabilities';
const fetchJSON = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({fetchJSON}));
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
const fixture = (): CapabilitySnapshot => ({revision:'rev-1',local_admin:true,native_work_active:false,settings:{target_os:'all',tools:{},skills:{}},tools:[{name:'forsic_read',required:true,enabled:true,default_enabled:true,target_os:['windows','macos','linux'],overridden:false},{name:'forsic_shell',required:false,enabled:true,default_enabled:true,target_os:['windows','macos','linux'],overridden:false}],skills:[{name:'scan-windows-events',required:false,enabled:true,default_enabled:true,target_os:['windows'],overridden:false}]});
let dispose=()=>{};
beforeEach(()=>{fetchJSON.mockImplementation(async(url:string)=>url.includes('skills-ui-copy')?{schema:'forsic.ui.skill-copy.v1',locale:'ko',skills:{}}:fixture());});
afterEach(()=>{dispose();document.body.innerHTML='';fetchJSON.mockReset();});
async function render(kind:'tools'|'skills'='tools',running=false) {
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 await act(async()=>root.render(<ForsicCapabilities kind={kind} profile="sample" running={running}/>));dispose=()=>{act(()=>root.unmount());};
 const click=async(text:string)=>act(async()=>{const button=[...host.querySelectorAll('button')].find(node=>node.textContent===text||node.getAttribute('aria-label')===text);if(!button)throw new Error(text);button.click();});
 const preset=async(value:string)=>act(async()=>{const select=host.querySelector('select')!;select.value=value;select.dispatchEvent(new Event('change',{bubbles:true}));});
 return {host,click,preset,rerender:async(profile:string)=>act(async()=>root.render(<ForsicCapabilities kind={kind} profile={profile} running={running}/>))};
}
it('previews presets, clears overrides, and saves explicitly with revision',async()=>{
 const {host,click,preset}=await render('skills');expect(fetchJSON).toHaveBeenCalledWith('/api/plugins/forsic/capabilities?profile=sample');
 await preset('macos');expect(host.querySelector('[role=switch]')?.getAttribute('aria-checked')).toBe('false');expect(fetchJSON.mock.calls.some(call=>call[1]?.method==='PUT')).toBe(false);
 await click('scan-windows-events 사용');expect(host.querySelector('[role=switch]')?.getAttribute('aria-checked')).toBe('true');
 await preset('linux');expect(host.querySelector('[role=switch]')?.getAttribute('aria-checked')).toBe('false');await click('저장');
 expect(JSON.parse(fetchJSON.mock.calls.at(-1)![1].body)).toEqual({target_os:'linux',tools:{},skills:{},expected_revision:'rev-1'});
});
it('locks core entries and keeps disabled entries manageable',async()=>{
 const {host,click}=await render();expect(host.querySelectorAll<HTMLButtonElement>('[role=switch]')[0].disabled).toBe(true);
 await act(async()=>host.querySelectorAll<HTMLButtonElement>('[role=switch]')[1].click());expect(host.querySelectorAll('[role=switch]')[1].getAttribute('aria-checked')).toBe('false');expect(host.querySelectorAll('.forsic-capability')).toHaveLength(2);
 await click('저장');expect(JSON.parse(fetchJSON.mock.calls.at(-1)![1].body).tools).toEqual({forsic_shell:false});
});
it('renders remote snapshots read-only',async()=>{
 const value=fixture();value.local_admin=false;fetchJSON.mockResolvedValue(value);const {host}=await render();expect(host.textContent).toContain('읽기 전용');expect(host.querySelector<HTMLSelectElement>('select')!.disabled).toBe(true);expect([...host.querySelectorAll<HTMLButtonElement>('[role=switch]')].every(node=>node.disabled)).toBe(true);
});
it('retains a draft after save failure and retries without pausing',async()=>{
 const {host,click,preset}=await render('skills');await preset('macos');fetchJSON.mockRejectedValueOnce({status:409});await click('저장');expect(host.querySelector('[role=alert]')?.textContent).toContain('일시정지 후 적용');expect(host.querySelector<HTMLSelectElement>('select')!.value).toBe('macos');expect(host.textContent).toContain('저장하지 않은 변경');expect(fetchJSON.mock.calls.every(call=>!String(call[0]).includes('pause'))).toBe(true);await click('저장');expect(host.textContent).toContain('설정을 저장했어요');
});
it('allows drafting but blocks saving during native work',async()=>{
 const {host,preset}=await render('skills',true);await preset('macos');expect(host.textContent).toContain('일시정지 후 적용');expect([...host.querySelectorAll<HTMLButtonElement>('button')].find(node=>node.textContent==='저장')!.disabled).toBe(true);
});
it('can retry a failed load',async()=>{
 fetchJSON.mockRejectedValueOnce(new Error('offline'));const {host,click}=await render();expect(host.querySelector('[role=alert]')).not.toBeNull();await click('설정 다시 불러오기');expect(host.querySelectorAll('[role=switch]')).toHaveLength(2);
});
it('loads saved OFF overrides, switches across every OS and returns to all',async()=>{
 const value=fixture();value.settings.skills={'scan-windows-events':false};value.skills[0].enabled=false;value.skills[0].overridden=true;fetchJSON.mockImplementation(async(url:string)=>url.includes('skills-ui-copy')?{schema:'forsic.ui.skill-copy.v1',locale:'ko',skills:{}}:value);
 const {host,preset}=await render('skills');expect(host.querySelector('[role=switch]')?.getAttribute('aria-checked')).toBe('false');
 await preset('windows');expect(host.querySelector('[role=switch]')?.getAttribute('aria-checked')).toBe('true');await preset('macos');expect(host.querySelector('[role=switch]')?.getAttribute('aria-checked')).toBe('false');await preset('all');expect(host.querySelector('[role=switch]')?.getAttribute('aria-checked')).toBe('true');
});
it('distinguishes saved policy from pending native application',async()=>{
 const {host,preset,click}=await render('skills');await preset('linux');const value=fixture();value.settings.target_os='linux';value.pending_runtime_update=true;value.apply_status='unapplied';fetchJSON.mockResolvedValueOnce(value);await click('저장');expect(host.textContent).toContain('조사 도구 구성 반영을 기다리고');expect(host.textContent).not.toContain('조사 도구 구성에 반영했어요');
});
it('retries applying unchanged saved settings after an unapplied reply',async()=>{
 const {host,preset,click}=await render('skills');await preset('linux');const value=fixture();value.settings.target_os='linux';value.pending_runtime_update=true;value.apply_status='unapplied';fetchJSON.mockResolvedValueOnce(value);await click('저장');
 expect(host.textContent).toContain('적용 다시 시도');fetchJSON.mockResolvedValueOnce({...value,apply_status:'applied',pending_runtime_update:false});await click('적용 다시 시도');expect(host.textContent).toContain('조사 도구 구성에 반영했어요');expect(host.textContent).not.toContain('적용 다시 시도');
});
it('describes unverified busy state without claiming a pending configuration',async()=>{
 fetchJSON.mockResolvedValue({...fixture(),native_work_active:true,apply_status:'unverified',pending_runtime_update:true});const {host}=await render();expect(host.textContent).toContain('조사 중에는 현재 적용 상태를 확인할 수 없어요');expect(host.textContent).not.toContain('반영을 기다리고');
});
it.each(['refresh','save'])('ignores late %s results after profile scope changes',async(operation)=>{
 const {host,preset,click,rerender}=await render('skills');let resolve!:(value:CapabilitySnapshot)=>void;const pending=new Promise<CapabilitySnapshot>(done=>{resolve=done;});
 if(operation==='save') await preset('linux');fetchJSON.mockReturnValueOnce(pending);await click(operation==='save'?'저장':'설정 다시 불러오기');await rerender('second');
 const old=fixture();old.settings.target_os='windows';old.local_admin=false;await act(async()=>resolve(old));expect(host.querySelector<HTMLSelectElement>('select')!.value).toBe('all');expect(host.textContent).not.toContain('읽기 전용');expect(host.textContent).not.toContain('설정을 저장했어요');
});
it.each(['refresh','save'])('ignores late %s failures after profile scope changes',async(operation)=>{
 const {host,preset,click,rerender}=await render('skills');let reject!:(value:unknown)=>void;const pending=new Promise<CapabilitySnapshot>((_,fail)=>{reject=fail;});
 if(operation==='save') await preset('linux');fetchJSON.mockReturnValueOnce(pending);await click(operation==='save'?'저장':'설정 다시 불러오기');await rerender('second');await act(async()=>reject({status:409}));expect(host.querySelector('[role=alert]')).toBeNull();
});
