// @vitest-environment jsdom
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {ForsicWorkbench} from './ForsicWorkbench';
import type {useForsicInspector} from '@/hooks/useForsicInspector';

const fetchJSON = vi.hoisted(() => vi.fn());
const authedFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({fetchJSON, authedFetch}));
const snapshot = (): ReturnType<typeof useForsicInspector> => ({
  scope: 'sample', sessionId: 'sample', profile: 'default', active: true, loading: false, catalogueError: null,

  currentTools: [], running: null, catalogueSource: 'profile configuration',
  toolItems: [{name: 'forsic_shell', description: '', group: 'forsic'}, {name: 'forsic_read', description: ''}],
  skillItems: [{name:'correlate-time',description:'Compare source timestamps',groupDescription:'Technical group details'},{name:'external:correlate-time',description:'External English description'}],
  refresh: vi.fn(async () => undefined), onEvent: vi.fn(), onReconnect: vi.fn(),
});
const emptyCase = {case_id: 'bound-case', session_id: 'sample', profile: 'default', questions: [], observations: [], sources: [], timeline: [], reports: [], activities: []};
const locale = {schema:'forsic.ui.skill-copy.v1',locale:'ko',skills:{'correlate-time':{display_name:'시각 대조',description:'원문 시각과 추정 시간 관계를 대조합니다.',category:'사건 재구성',search_terms:['타임라인']}}};
function mockInvestigation(value: unknown) {fetchJSON.mockImplementation(async (url: string) => url === '/api/plugins/forsic/skills-ui-copy' ? locale : url.startsWith('/api/plugins/forsic/capabilities') ? {revision:'r1',local_admin:true,settings:{target_os:'all',tools:{},skills:{}},tools:snapshot().toolItems.map(item=>({...item,required:false,enabled:true,default_enabled:true,target_os:['windows','macos','linux'],overridden:false})),skills:snapshot().skillItems.map(item=>({...item,required:false,enabled:true,default_enabled:true,target_os:['windows','macos','linux'],overridden:false}))} : value);}
beforeEach(() => {Object.defineProperty(window, 'innerWidth', {value: 1280, configurable: true}); mockInvestigation(emptyCase);});
let dispose = () => {};
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
afterEach(() => {dispose(); document.body.innerHTML = ''; fetchJSON.mockReset(); authedFetch.mockReset();});
async function render(inspector = snapshot(), options: {connected?: boolean; running?: boolean; statusText?: string} = {}) {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<ForsicWorkbench inspector={inspector} connected {...options}><div>기존 대화 화면</div></ForsicWorkbench>));
  dispose = () => {act(() => root.unmount());};
  const click = async (label: string) => act(async () => {
    const button = [...host.querySelectorAll('button')].find(item => item.textContent?.includes(label) || item.getAttribute('aria-label') === label);
    if (!button) throw new Error(`Missing button ${label}`);
    button.click();
  });
  return {host, click, rerender: async (next: ReturnType<typeof useForsicInspector>) => {await act(async () => root.render(<ForsicWorkbench inspector={next} connected {...options}><div>기존 대화 화면</div></ForsicWorkbench>));}};
}

it('opens real names and purpose descriptions without executing anything or replacing chat', async () => {
  const state = snapshot(); const {host, click} = await render(state);
  await click('도구');
  expect(host.querySelector('aside')?.textContent).toContain('분석 터미널');
  expect(host.querySelector('aside')?.textContent).toContain('forsic_shell');
  expect(host.querySelector('.forsic-capability')?.tagName).toBe('ARTICLE');
  expect(host.querySelector('.forsic-capability')?.textContent).toContain('원본은 읽기 전용');
  expect(host.querySelector('.forsic-capability details')?.hasAttribute('open')).toBe(false);
  await click('스킬');
  expect(host.querySelector('aside')?.textContent).toContain('시각 대조');
  expect(host.querySelector('.forsic-capability-body > p')?.textContent).toContain('원문 시각');
  expect(host.querySelector('.forsic-capability details')?.hasAttribute('open')).toBe(false);
  expect(host.querySelector('.forsic-capability details')?.textContent).toContain('Compare source timestamps');
  expect(host.querySelectorAll('.forsic-capability')[1].textContent).toContain('한국어 설명 미등록');
  expect(host.textContent).toContain('기존 대화 화면');
  expect(state.refresh).not.toHaveBeenCalled();
  expect(state.onEvent).not.toHaveBeenCalled();
});

it('shows observed activity and labels truncated or unavailable results honestly', async () => {
  const state = snapshot(); state.running = true;
  const {host, click} = await render(state);
  expect(host.querySelector('.forsic-workbench-presence')?.getAttribute('data-running')).toBe('true');
  expect(host.querySelector('.forsic-activity-mark')).toBeNull();
  expect(host.querySelector('.forsic-call')).toBeNull();
  await click('조사 패널 닫기');
  expect(host.querySelector('aside')?.hidden).toBe(true);
});

it.each([{connected: false}, {running: false}])('keeps the activity indicator inactive when live state overrides stored activity: %j', async options => {
  const state = snapshot(); state.running = true;
  const {host} = await render(state, options);
  expect(host.querySelector('.forsic-workbench-presence')?.getAttribute('data-running')).toBe('false');
});

it('returns keyboard focus to the selected control after closing its panel with Escape', async () => {
  const {host, click} = await render();
  await click('도구');
  await act(async () => {host.querySelector('aside')!.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));});
  expect(host.querySelector('aside')?.hidden).toBe(true);
  expect(document.activeElement?.textContent).toContain('도구');
});


it('opens the shared investigation monitor on desktop and returns focus when closed', async () => {
  mockInvestigation({case_id: 'bound-case', session_id: 'sample', profile: 'default', questions: [], observations: [], sources: [], timeline: []});
  const state = snapshot(); const {host, click} = await render(state);
  expect(fetchJSON).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/investigation-view?session_id=sample&profile=default');
  expect(host.querySelector('aside')?.getAttribute('aria-label')).toBe('조사 모니터');
  expect(document.activeElement).toBe(host.querySelector('aside'));
  expect(host.textContent).toContain('아직 연결된 시간 기록이 없어요.');
  expect(host.textContent).toContain('기존 대화 화면');
  expect(state.refresh).not.toHaveBeenCalled();
  await click('조사 패널 닫기');
  expect(host.querySelector('aside')?.hidden).toBe(true);
  expect(document.activeElement?.textContent).toContain('조사 모니터');
});


it('labels a saved conversation without a live runtime without implying an ongoing connection attempt', async () => {
  const state = snapshot();
  const {host} = await render(state, {connected: false, running: false});
  expect(host.querySelector('[role="status"]')?.textContent).toBe('저장된 대화');
  expect(host.textContent).toContain('기존 대화 화면');
  expect(host.querySelector('.forsic-workbench-presence')?.getAttribute('data-running')).toBe('false');
  expect(fetchJSON).toHaveBeenCalledWith('/api/plugins/forsic/investigation-view?session_id=sample&profile=default');
  expect(state.refresh).not.toHaveBeenCalled();
  expect(state.onReconnect).not.toHaveBeenCalled();
});

it.each(['연결이 끊어졌습니다.', '일시정지됨'])('preserves explicit native transport or pause status for a disconnected conversation: %s', async statusText => {
  const {host} = await render(snapshot(), {connected: false, running: false, statusText});
  expect(host.querySelector('[role="status"]')?.textContent).toBe(statusText);
  expect(fetchJSON).toHaveBeenCalledWith('/api/plugins/forsic/investigation-view?session_id=sample&profile=default');
});


it('starts closed on mobile while leaving the monitor available', async () => {
  Object.defineProperty(window, 'innerWidth', {value: 390, configurable: true});
  const {host, click} = await render();
  expect(host.querySelector('aside')?.hidden).toBe(true);
  await click('조사 모니터');
  expect(host.querySelector('aside')?.hidden).toBe(false);
});

it('keeps the selected report tab across conversations of the same case', async () => {
  mockInvestigation({...emptyCase, reports: [{id: 'report-1', name: 'summary', title: '사건 판단 보고서', files: [{name: 'executive.html', url: '/api/plugins/forsic/report-bundles/report-1/executive.html?case_id=bound-case'}]}]});
  const {host, click, rerender} = await render();
  await click('보고서');
  expect(host.querySelector('aside')?.getAttribute('aria-label')).toBe('보고서');
  expect(host.querySelector('.frr-card .forsic-report-file')?.textContent).toContain('웹에서 읽기');
  expect(host.querySelector('.frr-card a')).toBeNull();
  mockInvestigation({...emptyCase, session_id: 'second', reports: [{id: 'report-1', name: 'summary', title: '사건 판단 보고서', files: [{name: 'executive.html', url: '/api/plugins/forsic/report-bundles/report-1/executive.html?case_id=bound-case'}]}]});
  await rerender({...snapshot(), sessionId: 'second', scope: 'second'});
  expect(host.querySelector('aside')?.getAttribute('aria-label')).toBe('보고서');
  expect(host.querySelector('.frr-card')?.textContent).toContain('사건 판단 보고서');
});

it('does not invent report links for unavailable or empty reports', async () => {
  const {host, click} = await render();
  await click('보고서');
  expect(host.textContent).toContain('아직 저장된 보고서가 없어요.');
  expect(host.querySelector('.frr-card a')).toBeNull();
});

it('keeps tool call history in the conversation instead of duplicating it in the monitor', async () => {
  const {host} = await render();
  expect(host.querySelector('.forsic-call')).toBeNull();
  expect(host.querySelector('.forsic-monitor-live')).toBeNull();
  expect([...host.querySelectorAll('nav button')].map(button => button.textContent).join(' ')).not.toContain('활동 기록');
});

it('names the active reporting action in the toolbar without adding a second invocation list', async () => {
  const state = snapshot()
  state.currentTools = [{id: 'report-active', name: 'forsic_reporting', arguments: '{"action":"render"}', status: 'running', source: 'live'}]
  const {host} = await render(state, {running: true})
  expect(host.querySelector('.forsic-workbench-status [role="status"]')?.textContent).toBe('보고서 작성')
  expect(host.querySelector('.fc-tool-call')).toBeNull()
})

it('loads evidence only when its tab opens and preserves the bound request scope', async () => {
  authedFetch.mockResolvedValue({ok:true,json:async()=>({case_id:'bound-case',session_id:'sample',profile:'default',source_snapshot_id:'snapshot-1',selection_digest:'digest',items:[],counts:{total:0,downloadable:0,restricted:0,stale:0}})});
  const {host,click}=await render();
  expect(authedFetch).not.toHaveBeenCalled();
  await click('도구'); await click('스킬');
  expect(fetchJSON).toHaveBeenCalledWith('/api/plugins/forsic/skills-ui-copy');
  expect(authedFetch).not.toHaveBeenCalled();
  await click('증거 모음');
  expect(host.querySelector('aside')?.getAttribute('aria-label')).toBe('증거 모음');
  expect(authedFetch).toHaveBeenCalledOnce();
  const params=new URL(authedFetch.mock.calls[0][0],'http://local').searchParams;
  expect(params.get('case_id')).toBe('bound-case'); expect(params.get('session_id')).toBe('sample'); expect(params.get('profile')).toBe('default');
  expect(host.textContent).toContain('기존 대화 화면');
});

it('searches canonical IDs, English metadata and Korean aliases while keeping English in details', async () => {
  const {host,click}=await render(); await click('스킬');
  const input=host.querySelector<HTMLInputElement>('[aria-label="스킬 검색"]')!;
  for (const term of ['타임라인','Compare source timestamps','시각 대조']) {
    await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,term); input.dispatchEvent(new Event('input',{bubbles:true}));});
    expect(host.querySelectorAll('.forsic-capability')).toHaveLength(1);
    expect(host.querySelector('.forsic-capability code')?.textContent).toBe('correlate-time');
    expect(host.querySelector('.forsic-capability-body > p')?.textContent).not.toContain('Compare');
  }
});

it('publishes Ren panel selection only while the independent-review tab is visible', async () => {
  const signals: Array<{selected: boolean; sessionId: string; caseId: string}> = [];
  const receive = (event: Event) => signals.push((event as CustomEvent).detail);
  window.addEventListener('forsic:review-panel', receive);
  try {
    const {click} = await render();
    expect(signals.at(-1)).toMatchObject({selected: false, sessionId: 'sample', caseId: 'bound-case'});
    await click('렌 · 독립 검토');
    expect(signals.at(-1)?.selected).toBe(true);
    await click('시간 흐름');
    expect(signals.at(-1)?.selected).toBe(false);
    await click('렌 · 독립 검토');
    await click('도구');
    expect(signals.at(-1)?.selected).toBe(false);
    await click('조사 모니터');
    expect(signals.at(-1)?.selected).toBe(true);
    await click('조사 패널 닫기');
    expect(signals.at(-1)?.selected).toBe(false);
  } finally {window.removeEventListener('forsic:review-panel', receive);}
});
