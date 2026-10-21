// @vitest-environment jsdom
import {act,useState} from 'react';
import {createRoot,type Root} from 'react-dom/client';
import {MemoryRouter} from 'react-router';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
const state=vi.hoisted(()=>({connections:0,management:0,hosting:0}));
vi.mock('@/contexts/tenant-context',()=>({useTenant:()=>({tenant_id:'synthetic-tenant',display_name:'시험 조직'})}));
vi.mock('@/contexts/useProfileScope',()=>({useProfileScope:()=>({profile:'synthetic-profile'})}));
vi.mock('@/contexts/usePageHeader',()=>({usePageHeader:()=>({setTitle:()=>{}})}));
vi.mock('@/plugins',()=>({PluginSlot:({componentProps}:{componentProps:{section:string}})=>{const [draft,setDraft]=useState('');state.connections++;return <div data-connection-section={componentProps.section}><input aria-label="연결 초안" value={draft} onChange={event=>setDraft(event.target.value)}/></div>;}}));
vi.mock('@/components/ForsicManagement',()=>({ForsicManagement:({section,onSectionChange}:{section:string;onSectionChange:(section:string)=>void})=>{state.management++;return <div data-management-section={section}><button onClick={()=>onSectionChange('environments')}>환경으로 전환</button></div>;}}));
vi.mock('@/components/ForsicHostingSettings',()=>({ForsicHostingSettings:()=>{state.hosting++;return <p>폴더 접속 편집</p>;}}));
import Page from './ForsicManagementPage';
let host:HTMLDivElement,root:Root;
beforeEach(()=>{Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});state.connections=state.management=state.hosting=0;host=document.createElement('div');document.body.append(host);root=createRoot(host);});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
async function select(name:string){await act(async()=>[...host.querySelectorAll<HTMLButtonElement>('.forsic-settings-nav button')].find(button=>button.textContent===name)!.click());}
it('lazily opens categories, retains connection drafts and preserves return scope',async()=>{
 await act(async()=>root.render(<MemoryRouter initialEntries={['/settings?case=synthetic-case&resume=synthetic-session']}><Page/></MemoryRouter>));
 expect(state.management).toBe(0);expect(state.hosting).toBe(0);
 const input=host.querySelector<HTMLInputElement>('input')!;
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,'unsaved-model');input.dispatchEvent(new Event('input',{bubbles:true}));});
 await select('텔레그램 알림');expect(host.querySelector('[data-connection-section]')?.getAttribute('data-connection-section')).toBe('telegram');
 await select('확장팩');expect(host.querySelector('[data-management-section]')?.getAttribute('data-management-section')).toBe('packs');expect(input.closest('[hidden]')).not.toBeNull();
 await select('조사·검토 모델');expect(host.querySelector('input')).toBe(input);expect(input.value).toBe('unsaved-model');
 const href=host.querySelector('header a')!.getAttribute('href')!;expect(href).toContain('/chat?');expect(href).toContain('tenant_id=synthetic-tenant');expect(href).toContain('case=synthetic-case');expect(href).toContain('resume=synthetic-session');expect(state.hosting).toBe(0);
});
it('opens deep linked management category and honors child requested category changes',async()=>{
 await act(async()=>root.render(<MemoryRouter initialEntries={['/settings?section=packs']}><Page/></MemoryRouter>));
 expect(state.connections).toBe(0);expect(host.querySelector('[data-management-section]')?.getAttribute('data-management-section')).toBe('packs');
 await act(async()=>host.querySelector<HTMLButtonElement>('[data-management-section] button')!.click());
 expect(host.querySelector('[data-management-section]')?.getAttribute('data-management-section')).toBe('environments');expect(host.querySelector('.forsic-settings-nav [aria-current=page]')?.textContent).toBe('분석 환경');
});
