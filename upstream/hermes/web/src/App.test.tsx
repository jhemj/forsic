// @vitest-environment jsdom
import { act, useEffect, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { BrowserRouter, MemoryRouter, useNavigate } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ loading: true, mounts: 0, unmounts: 0, title: vi.fn() }));
vi.mock('@/lib/api', () => ({ setManagementProfile: vi.fn(), api: {getProfiles: async () => ({profiles:[{name:'default'}]}), getActiveProfile: async () => ({current:'default',active:'default'})}, fetchJSON: async () => ({new_investigation:{allowed:true,reason:null,active_case:null},cases:[{case_id:'A',label:'Case A',session_ids:['one']},{case_id:'B',label:'Case B',session_ids:['two']}]}) }));
vi.mock('@/plugins', () => ({
  usePlugins: () => ({ loading: state.loading }),
  PluginSlot: ({ fallback }: { fallback: ReactNode }) => fallback,
  PluginPage: () => <div>investigation board</div>,
}));
vi.mock('@/contexts/usePageHeader', () => ({usePageHeader: () => ({setTitle: state.title})}));
vi.mock('@/contexts/PageHeaderProvider', () => ({ PageHeaderProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock('@/hooks/useSidebarStatus', () => ({ useSidebarStatus: () => null }));
vi.mock('@/components/MemoryPressureBanner', () => ({ MemoryPressureBanner: () => null }));
vi.mock('@/components/AuthWidget', () => ({ AuthWidget: () => <div>session authentication</div> }));
vi.mock('@/pages/ForsicManagementPage',()=>({default:()=> <div>dedicated management workspace</div>}));
vi.mock('@/pages/SessionsPage', () => ({ default: () => <div>session history</div> }));
vi.mock('@/components/ForsicOnboarding', () => ({ default: () => <div>새 조사 시작 안내</div> }));
vi.mock('@/pages/ChatPage', () => ({ default: ({ isActive }: { isActive: boolean }) => {
  useEffect(() => { state.mounts++; return () => { state.unmounts++; }; }, []);
  return <div data-terminal-active={isActive}>native terminal</div>;
} }));

vi.mock('@/components/ForsicTenantGate',async()=>{const {ProfileProvider}=await import('@/contexts/ProfileProvider');const {TenantContext}=await import('@/contexts/tenant-context');return {ForsicTenantGate:({children}:{children:ReactNode})=><TenantContext.Provider value={{tenant_id:'test-tenant',display_name:'합성 조직',profile_name:'default',revision:1,memory_policy:{mode:'manual'}}}><ProfileProvider>{children}</ProfileProvider></TenantContext.Provider>}});
vi.mock('@/components/ForsicMemoryReceipts',()=>({ForsicMemoryReceipts:()=>null}));
import App from './App';

let root: Root;
let container: HTMLDivElement;
let navigate: ReturnType<typeof useNavigate>;
function Navigation() { navigate = useNavigate(); return null; }
async function settleLazyRoutes() {
  await act(async () => { await vi.dynamicImportSettled(); });
}
function view(path: string) {
  return <MemoryRouter initialEntries={[path]}><Navigation /><App key={String(state.loading)} /></MemoryRouter>;
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  state.loading = true; state.mounts = 0; state.unmounts = 0;
  window.__HERMES_DASHBOARD_PROFILE__ = "default";
  state.title.mockClear();
  container = document.createElement('div'); document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); delete window.__HERMES_DASHBOARD_PROFILE__; window.history.replaceState(null, "", "/"); });

it('keeps loading and plugin-failure navigation product-only without starting a terminal', async () => {
  await act(async () => root.render(view('/forsic')));
  expect(container.textContent).toContain('조사 화면을 불러오고 있어요.');
  expect(container.querySelectorAll('nav a')).toHaveLength(0);
  state.loading = false;
  await act(async () => root.render(view('/forsic')));
  expect(Array.from(container.querySelectorAll('nav a')).map(a => a.getAttribute('href')))
    .toEqual(['/forsic?view=new&tenant_id=test-tenant&profile=default', '/forsic?view=history&tenant_id=test-tenant&profile=default']);
  expect(container.textContent).toContain('session authentication');
  expect(state.mounts).toBe(0);
});

it('keeps the same native terminal mounted across board/history navigation and rejects obsolete routes', async () => {
  state.loading = false;
  await act(async () => root.render(view('/forsic')));
  expect(state.mounts).toBe(0);
  await act(async () => { await navigate('/chat?case=A&resume=one'); });
  await settleLazyRoutes();
  expect(state.mounts).toBe(1);
  await act(async () => { await navigate('/forsic?view=history'); });
  expect(container.querySelector('[data-chat-active]')?.getAttribute('aria-hidden')).toBe('true');
  await act(async () => { await navigate('/chat?case=A&resume=one'); });
  expect(state.mounts).toBe(1);
  expect(state.unmounts).toBe(0);
  await act(async () => { await navigate('/models'); });
  expect(container.textContent).toContain('investigation board');
  expect(state.mounts).toBe(1);
});

it('closes the mobile navigation when switching board views', async () => {
  state.loading = false;
  await act(async () => root.render(view('/forsic?view=board')));
  await act(async () => (container.querySelector('[aria-label="메뉴 열기"]') as HTMLButtonElement).click());
  expect(container.querySelector('#app-sidebar')?.getAttribute('data-open')).toBe('true');
  await act(async () => { await navigate('/forsic?view=history'); });
  expect(container.querySelector('#app-sidebar')?.getAttribute('data-open')).toBe('false');
  expect(state.mounts).toBe(0);
});

it('does not open a native terminal when a session belongs to another case', async () => {
  state.loading=false;
  await act(async () => root.render(view('/chat?case=A&resume=two')));
  expect(state.mounts).toBe(0);
  expect(container.textContent).toContain('이 대화와 사건의 연결이 일치하지 않습니다.');
});
it('opens evidence intake through explicit new view without starting an agent', async () => {
  state.loading=false;
  await act(async () => root.render(view('/forsic?view=new')));
  await settleLazyRoutes();
  expect(state.mounts).toBe(0);
  expect(container.textContent).toContain('새 조사 시작 안내');
});

it.each(['/', '/forsic', '/forsic?view=start', '/chat', '/chat?fresh=1'])('shows the investigation entry at %s without connecting a native chat', async path => {
  state.loading = false;
  await act(async () => root.render(view(path)));
  await settleLazyRoutes();
  expect(container.textContent).toContain('어떤 조사를 진행할까요?');
  expect(container.textContent).toContain('기존 조사');
  expect(state.mounts).toBe(0);
});

it.each(['/', '/chat'])('renders entry at %s while the real provider synchronizes the browser profile', async path => {
  state.loading = false;
  window.history.replaceState(null, '', path);
  await act(async () => root.render(<BrowserRouter><App /></BrowserRouter>));
  await settleLazyRoutes();
  expect(new URLSearchParams(window.location.search).get('profile')).toBe('default');
  expect(container.textContent).toContain('어떤 조사를 진행할까요?');
  expect(state.title).toHaveBeenLastCalledWith('조사 시작');
  expect(state.mounts).toBe(0);
});

it('opens management from the left footer in a dedicated route without mounting a native conversation',async()=>{
 state.loading=false;await act(async()=>root.render(view('/forsic?view=history&profile=default')));const entry=container.querySelector<HTMLAnchorElement>('.forsic-management-link')!;expect(entry.closest('#app-sidebar')).not.toBeNull();expect(entry.getAttribute('href')).toBe('/management?profile=default&tenant_id=test-tenant');await act(async()=>entry.click());await settleLazyRoutes();expect(container.textContent).toContain('dedicated management workspace');expect(state.mounts).toBe(0);expect(container.querySelector('.forsic-management-link')?.getAttribute('aria-current')).toBe('page');
});
it('preserves case and resume context when leaving chat for management without a second conversation',async()=>{
 state.loading=false;await act(async()=>root.render(view('/chat?case=A&resume=one&profile=default')));await settleLazyRoutes();expect(state.mounts).toBe(1);expect(container.querySelector('.forsic-management-link')?.getAttribute('href')).toBe('/management?profile=default&tenant_id=test-tenant&case=A&resume=one');await act(async()=>container.querySelector<HTMLAnchorElement>('.forsic-management-link')!.click());await settleLazyRoutes();expect(container.textContent).toContain('dedicated management workspace');expect(state.mounts).toBe(1);expect(container.querySelector('[data-chat-active]')?.getAttribute('aria-hidden')).toBe('true');
});
