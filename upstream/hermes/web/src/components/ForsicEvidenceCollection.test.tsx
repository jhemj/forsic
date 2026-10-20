// @vitest-environment jsdom
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {ForsicEvidenceCollection} from './ForsicEvidenceCollection';
import type {EvidenceCollection, EvidenceCollectionItem} from '@/lib/forsic-evidence-collection';
const authedFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({authedFetch}));
const item = (id = 'ev-1', extra = {}): EvidenceCollectionItem => ({evidence_item_id:id, item_version:`version-${id}`, title:`증거 ${id}`, kind:'retained_excerpt', excerpt:'보존 발췌', downloadable:true, currentness:'current', ...extra});
const collection = (extra = {}): EvidenceCollection => ({case_id:'case-a', profile:'analyst', session_id:'session-a', source_snapshot_id:'snapshot-a', selection_digest:'selection-a', items:[item(), item('ev-2', {downloadable:false})], counts:{total:180, downloadable:150, restricted:1, stale:3, filtered:2}, ...extra});
const response = (body: unknown, status = 200) => ({ok:status < 400, status, json:async () => body});
let dispose = () => {};
const revoke = vi.fn(), createBlob = vi.fn(() => 'blob:evidence-local');
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  authedFetch.mockResolvedValue(response(collection()));
  Object.defineProperty(URL, 'createObjectURL', {value:createBlob, configurable:true});
  Object.defineProperty(URL, 'revokeObjectURL', {value:revoke, configurable:true});
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
});
afterEach(() => {dispose(); dispose = () => {}; document.body.innerHTML = ''; authedFetch.mockReset(); revoke.mockClear(); createBlob.mockClear(); vi.restoreAllMocks();});
async function render(active = true) {
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  const draw = async (props = {}) => act(async () => root.render(<ForsicEvidenceCollection key="case-a" sessionId="session-a" profile="analyst" caseId="case-a" active={active} {...props}/>));
  await draw(); dispose = () => {act(() => root.unmount());};
  const click = async (label: string) => act(async () => {
    const target = [...host.querySelectorAll<HTMLButtonElement | HTMLInputElement>('button,input')].find(node => node.getAttribute('aria-label') === label || node.textContent?.includes(label));
    if (!target) throw Error(`missing ${label}`); target.click();
  });
  const change = async (label: string, value: string) => act(async () => {
    const target = host.querySelector<HTMLSelectElement | HTMLInputElement>(`[aria-label="${label}"]`)!;
    Object.getOwnPropertyDescriptor(target.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(target, value);
    target.dispatchEvent(new Event(target.tagName === 'SELECT' ? 'change' : 'input', {bubbles:true}));
  });
  return {host, click, change, draw};
}
const postCalls = () => authedFetch.mock.calls.filter(([, options]) => options?.method === 'POST');
const posted = () => JSON.parse(postCalls().at(-1)![1].body);

it('fetches only when active and retains explicit case/profile/session ownership', async () => {
  const {draw, host} = await render(false);
  expect(authedFetch).not.toHaveBeenCalled();
  await draw({active:true});
  const query = new URL(authedFetch.mock.calls[0][0], 'http://local').searchParams;
  expect(Object.fromEntries(query)).toEqual({profile:'analyst',session_id:'session-a',case_id:'case-a',scope:'case',status:'current'});
  expect(host.textContent).toContain('채증 180건');
  expect(host.querySelectorAll('.fec-item')).toHaveLength(2);
  expect(host.querySelector<HTMLInputElement>('[aria-label="증거 ev-2 선택"]')?.disabled).toBe(true);
});

it('filters server-side, paginates the frozen snapshot and exports exact versions across pages', async () => {
  authedFetch.mockResolvedValueOnce(response(collection({next_cursor:'cursor-2'})));
  const {host, click} = await render();
  await click('현재 페이지 증거 선택');
  authedFetch.mockResolvedValueOnce(response(collection({items:[item('ev-3')], next_cursor:null})));
  await click('다음 페이지');
  const query = new URL(authedFetch.mock.calls.at(-1)![0], 'http://local').searchParams;
  expect(query.get('cursor')).toBe('cursor-2'); expect(query.get('source_snapshot_id')).toBe('snapshot-a');
  expect(host.textContent).toContain('2페이지'); expect(host.textContent).toContain('선택 1건');
  await click('현재 페이지 증거 선택');
  authedFetch.mockResolvedValueOnce(response({export_id:'export-a',status:'building',case_id:'case-a'}));
  await click('선택 증거 ZIP');
  expect(posted()).toMatchObject({source_snapshot_id:'snapshot-a',selection:'selected',include_sensitive_originals:false,profile:'analyst',session_id:'session-a',case_id:'case-a',items:[{evidence_item_id:'ev-1',item_version:'version-ev-1'},{evidence_item_id:'ev-3',item_version:'version-ev-3'}]});
});

it('searches and changes scope/type/status without exporting automatically; all means whole case', async () => {
  const {host, click, change} = await render();
  await change('증거 대화 범위','conversation'); await change('증거 유형','derived_analysis'); await change('증거 상태','all');
  await change('증거 이름·내용 검색','  특정 발췌  ');
  const calls = authedFetch.mock.calls.length;
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  expect(authedFetch.mock.calls.length).toBe(calls + 1);
  const query = new URL(authedFetch.mock.calls.at(-1)![0], 'http://local').searchParams;
  expect(query.get('search')).toBe('특정 발췌'); expect(query.get('scope')).toBe('conversation'); expect(query.get('kind')).toBe('derived_analysis'); expect(query.get('status')).toBe('all');
  expect(postCalls()).toHaveLength(0);
  authedFetch.mockResolvedValueOnce(response({export_id:'export-all',status:'building'}));
  await click('전체 채증 ZIP');
  expect(posted()).toMatchObject({selection:'all_eligible',items:[],source_snapshot_id:'snapshot-a'});
  expect(posted()).not.toHaveProperty('search');
});

it('requires explicit confirmation for sensitive originals and keeps ordinary defaults safe', async () => {
  authedFetch.mockResolvedValueOnce(response(collection({permissions:{sensitive_originals:true,max_sensitive_entries:10,max_sensitive_bytes:1024}})));
  const {host, click} = await render();
  await act(async () => host.querySelector<HTMLInputElement>('.fec-raw input')!.click());
  await click('전체 채증 ZIP');
  expect(postCalls()).toHaveLength(0); expect(host.querySelector('[role="dialog"]')).not.toBeNull();
  await click('취소'); expect(postCalls()).toHaveLength(0);
  await click('전체 채증 ZIP');
  authedFetch.mockResolvedValueOnce(response({export_id:'private-a',status:'building'}));
  await click('확인하고 생성');
  expect(posted().include_sensitive_originals).toBe(true);
});

it('uses the authenticated download path, enforces ZIP MIME and revokes the object URL', async () => {
  const {click, host} = await render();
  authedFetch.mockResolvedValueOnce(response({export_id:'export-a',status:'ready',case_id:'case-a',item_count:1}));
  authedFetch.mockResolvedValueOnce({ok:true,headers:new Headers({'Content-Type':'application/zip'}),blob:async()=>new Blob(['zip'])});
  await click('전체 채증 ZIP');
  expect(authedFetch.mock.calls.at(-1)![0]).toBe('/api/plugins/forsic/evidence-collection/exports/export-a/download?profile=analyst&session_id=session-a&case_id=case-a');
  expect(createBlob).toHaveBeenCalledOnce(); expect(host.querySelector('a')).toBeNull();
  authedFetch.mockResolvedValueOnce(response({export_id:'export-a',status:'ready'}));
  authedFetch.mockResolvedValueOnce({ok:true,headers:new Headers({'Content-Type':'text/html'}),blob:async()=>new Blob(['bad'])});
  await click('다시 내려받기');
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('묶음 형식'); expect(createBlob).toHaveBeenCalledOnce();
  dispose(); dispose=()=>{}; expect(revoke).toHaveBeenCalledWith('blob:evidence-local');
});

it.each([401,403,409,413])('shows safe actionable errors for HTTP %i and does not create a download', async status => {
  authedFetch.mockResolvedValueOnce(response({},status));
  const {host} = await render();
  expect(host.querySelector('[role="alert"]')).not.toBeNull(); expect(createBlob).not.toHaveBeenCalled();
});

it('retries unknown creation using the identical idempotency key and blocks duplicate clicks while busy', async () => {
  const {click,host} = await render();
  let resolve!: (value: unknown) => void;
  authedFetch.mockImplementationOnce(()=>new Promise(done=>{resolve=done}));
  await click('전체 채증 ZIP'); await click('전체 채증 ZIP');
  expect(postCalls()).toHaveLength(1); const first = posted();
  expect([...host.querySelectorAll<HTMLSelectElement | HTMLInputElement>('.fec-filters select,.fec-filters input')].every(node=>node.disabled)).toBe(true);
  expect(host.querySelector<HTMLButtonElement>('.fec-filters button')?.disabled).toBe(true);
  await act(async()=>resolve(response({},500)));
  expect(host.textContent).toContain('같은 요청 식별자');
  authedFetch.mockResolvedValueOnce(response({export_id:'original-export',status:'building'}));
  await click('기존 생성 요청 확인');
  expect(postCalls()).toHaveLength(2); expect(posted()).toEqual(first);
});

it('rejects scope-mismatched responses and renders evidence text without interpreting HTML', async () => {
  const payload='<img src=x onerror="window.bad=true"><script>alert(1)</script>';
  authedFetch.mockResolvedValueOnce(response(collection({items:[item('xss',{title:payload,excerpt:payload})]})));
  const {host,click}=await render();
  expect(host.querySelector('.fec-reading blockquote')?.textContent).toBe(payload); expect(host.querySelector('img,script')).toBeNull();
  authedFetch.mockResolvedValueOnce(response(collection({case_id:'other-case'})));
  await click('증거 목록 새로고침');
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('버전이 변경');
});

it('aborts unmounted requests and prevents late old-case data from entering a new case', async () => {
  let resolve!: (value:unknown)=>void;
  authedFetch.mockImplementationOnce(()=>new Promise(done=>{resolve=done}));
  const {host,draw}=await render(); const signal=authedFetch.mock.calls[0][1].signal;
  authedFetch.mockResolvedValueOnce(response(collection({case_id:'case-b',session_id:'session-b',items:[item('new')]})));
  await draw({key:'case-b',caseId:'case-b',sessionId:'session-b'});
  expect(signal.aborted).toBe(true);
  await act(async()=>resolve(response(collection({items:[item('old')]}))));
  expect(host.textContent).toContain('증거 new'); expect(host.textContent).not.toContain('증거 old');
});

it('clears a selection after a new listing snapshot instead of silently exporting the new versions', async () => {
  const {host,click}=await render();
  await click('현재 페이지 증거 선택'); expect(host.textContent).toContain('선택 1건');
  authedFetch.mockResolvedValueOnce(response(collection({source_snapshot_id:'new-snapshot',items:[item('ev-1',{item_version:'changed'})]})));
  await click('증거 목록 새로고침');
  expect(host.textContent).toContain('선택 0건');
  expect([...host.querySelectorAll('button')].find(node=>node.textContent?.includes('선택 증거 ZIP'))?.disabled).toBe(true);
  expect(postCalls()).toHaveLength(0);
});

it('polls an existing building export without submitting another creation and aborts on close', async () => {
  const {host,click}=await render();
  authedFetch.mockResolvedValueOnce(response({export_id:'building-a',status:'building'}));
  await click('전체 채증 ZIP');
  expect(host.textContent).toContain('생성 중');
  let resolve!:(value:unknown)=>void;
  authedFetch.mockImplementationOnce(()=>new Promise(done=>{resolve=done}));
  await click('생성 상태 확인');
  expect(postCalls()).toHaveLength(1);
  const last=authedFetch.mock.calls.at(-1)!;
  expect(last[0]).toContain('/exports/building-a?profile=analyst&session_id=session-a&case_id=case-a');
  const signal=last[1].signal;
  dispose(); dispose=()=>{};
  expect(signal.aborted).toBe(true);
  await act(async()=>resolve(response({export_id:'building-a',status:'ready'})));
  expect(createBlob).not.toHaveBeenCalled();
});

it('aborts an unknown pending creation on case change before it can trigger a download', async () => {
  const {host,click,draw}=await render();
  let resolve!:(value:unknown)=>void;
  authedFetch.mockImplementationOnce(()=>new Promise(done=>{resolve=done}));
  await click('전체 채증 ZIP');
  const signal=postCalls()[0][1].signal;
  authedFetch.mockResolvedValueOnce(response(collection({case_id:'case-b',session_id:'session-b'})));
  await draw({key:'case-b',caseId:'case-b',sessionId:'session-b'});
  expect(signal.aborted).toBe(true);
  await act(async()=>resolve(response({export_id:'old-case-export',status:'ready',case_id:'case-a'})));
  expect(createBlob).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain('묶음 준비됨');
  expect(authedFetch.mock.calls.filter(([url])=>url.includes('/download'))).toHaveLength(0);
});

it('sends real native session authentication and cookies for list, POST and ZIP transport', async () => {
  const native = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  const transport=vi.spyOn(globalThis,'fetch');
  const makeResponse=(body:unknown,zip=false)=>new Response(zip ? 'synthetic ZIP' : JSON.stringify(body),{status:200,headers:{'Content-Type':zip ? 'application/zip':'application/json'}});
  transport.mockResolvedValueOnce(makeResponse(collection()));
  transport.mockResolvedValueOnce(makeResponse({export_id:'authenticated',status:'ready',case_id:'case-a'}));
  transport.mockResolvedValueOnce(makeResponse(null,true));
  authedFetch.mockImplementation(native.authedFetch);
  window.__HERMES_SESSION_TOKEN__='synthetic-token';
  try {
    const {click}=await render(); await click('전체 채증 ZIP');
    expect(transport).toHaveBeenCalledTimes(3);
    for (const [,options] of transport.mock.calls) {
      expect(new Headers(options?.headers).get('X-Hermes-Session-Token')).toBe('synthetic-token');
      expect(options?.credentials).toBe('include');
    }
    expect(createBlob).toHaveBeenCalledOnce();
  } finally {delete window.__HERMES_SESSION_TOKEN__;}
});

it('preserves exact selected evidence across filters and searches within the same snapshot even when hidden', async () => {
  const {host,click,change}=await render();
  await click('현재 페이지 증거 선택');
  authedFetch.mockResolvedValueOnce(response(collection({items:[item('other')]})));
  await change('증거 유형','derived_analysis');
  expect(host.textContent).toContain('선택 1건');
  expect(host.querySelector('[aria-label="증거 ev-1 선택"]')).toBeNull();
  await change('증거 이름·내용 검색','없는 발췌');
  authedFetch.mockResolvedValueOnce(response(collection({items:[],counts:{total:180,downloadable:150,restricted:1,stale:3,filtered:0}})));
  await act(async()=>host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  expect(host.textContent).toContain('선택 1건');
  expect(host.querySelectorAll('.fec-item')).toHaveLength(0);
  authedFetch.mockResolvedValueOnce(response({export_id:'hidden-selection',status:'building'}));
  await click('선택 증거 ZIP');
  expect(posted()).toMatchObject({source_snapshot_id:'snapshot-a',selection:'selected',items:[{evidence_item_id:'ev-1',item_version:'version-ev-1'}]});
});

it('exports selected evidence and downloads on HTTP LAN without crypto.randomUUID', async () => {
  const original=Object.getOwnPropertyDescriptor(globalThis.crypto,'randomUUID');
  Object.defineProperty(globalThis.crypto,'randomUUID',{value:undefined,configurable:true});
  const randomBytes=vi.spyOn(globalThis.crypto,'getRandomValues');
  try {
    expect(globalThis.crypto.randomUUID).toBeUndefined();
    const {host,click}=await render();
    await click('현재 페이지 증거 선택');
    authedFetch.mockResolvedValueOnce(response({export_id:'lan-export',status:'ready',case_id:'case-a',item_count:1}));
    authedFetch.mockResolvedValueOnce({ok:true,headers:new Headers({'Content-Type':'application/zip'}),blob:async()=>new Blob(['synthetic LAN ZIP'])});
    await click('선택 증거 ZIP');
    expect(postCalls()).toHaveLength(1);
    expect(posted()).toMatchObject({selection:'selected',source_snapshot_id:'snapshot-a',items:[{evidence_item_id:'ev-1',item_version:'version-ev-1'}]});
    expect(posted().idempotency_key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(randomBytes).toHaveBeenCalledOnce();
    expect(authedFetch.mock.calls.at(-1)![0]).toContain('/exports/lan-export/download?');
    expect(createBlob).toHaveBeenCalledOnce();
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(host.textContent).toContain('묶음 준비됨');
  } finally {
    if (original) Object.defineProperty(globalThis.crypto,'randomUUID',original);
    else Reflect.deleteProperty(globalThis.crypto,'randomUUID');
  }
});
