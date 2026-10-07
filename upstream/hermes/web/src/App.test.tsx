// @vitest-environment jsdom
import { act, useEffect, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useNavigate } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ loading: true, mounts: 0, unmounts: 0 }));
vi.mock('@/lib/api', () => ({ fetchJSON: async () => ({cases:[{case_id:'A',label:'Case A',session_ids:['one']},{case_id:'B',label:'Case B',session_ids:['two']}]}) }));
vi.mock('@/plugins', () => ({
  usePlugins: () => ({ loading: state.loading }),
  PluginSlot: ({ fallback }: { fallback: ReactNode }) => fallback,
  PluginPage: () => <div>investigation board</div>,
}));
vi.mock('@/contexts/ProfileProvider', () => ({ ProfileProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock('@/contexts/useProfileScope', () => ({ useProfileScope: () => ({ profile: 'default' }) }));
vi.mock('@/contexts/PageHeaderProvider', () => ({ PageHeaderProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock('@/hooks/useSidebarStatus', () => ({ useSidebarStatus: () => null }));
vi.mock('@/components/MemoryPressureBanner', () => ({ MemoryPressureBanner: () => null }));
vi.mock('@/components/AuthWidget', () => ({ AuthWidget: () => <div>session authentication</div> }));
vi.mock('@/pages/SessionsPage', () => ({ default: () => <div>session history</div> }));
vi.mock('@/pages/ChatPage', () => ({ default: ({ isActive }: { isActive: boolean }) => {
  useEffect(() => { state.mounts++; return () => { state.unmounts++; }; }, []);
  return <div data-terminal-active={isActive}>native terminal</div>;
} }));
import App from './App';

let root: Root;
let container: HTMLDivElement;
let navigate: ReturnType<typeof useNavigate>;
function Navigation() { navigate = useNavigate(); return null; }
function view(path: string) {
  return <MemoryRouter initialEntries={[path]}><Navigation /><App key={String(state.loading)} /></MemoryRouter>;
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  state.loading = true; state.mounts = 0; state.unmounts = 0;
  container = document.createElement('div'); document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

it('keeps loading and plugin-failure navigation product-only without starting a terminal', async () => {
  await act(async () => root.render(view('/forsic')));
  expect(container.textContent).toContain('조사 화면을 불러오고 있어요.');
  expect(container.querySelectorAll('nav a')).toHaveLength(0);
  state.loading = false;
  await act(async () => root.render(view('/forsic')));
  expect(Array.from(container.querySelectorAll('nav a')).map(a => a.getAttribute('href')))
    .toEqual(['/forsic?view=new', '/forsic?view=history']);
  expect(container.textContent).toContain('session authentication');
  expect(state.mounts).toBe(0);
});

it('keeps the same native terminal mounted across board/history navigation and rejects obsolete routes', async () => {
  state.loading = false;
  await act(async () => root.render(view('/forsic')));
  expect(state.mounts).toBe(0);
  await act(async () => { await navigate('/chat?case=A&resume=one'); });
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
it('opens evidence intake for a new case without starting an agent', async () => {
  state.loading=false;
  await act(async () => root.render(view('/chat?fresh=1')));
  expect(state.mounts).toBe(0);
  expect(container.textContent).toContain('investigation board');
});
