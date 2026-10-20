// @vitest-environment jsdom
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {Blob as NodeBlob} from 'node:buffer';
import {ForsicReportLink} from './ForsicReportLink';

const fetchMock = vi.fn();
const createBlob = vi.fn<(blob: Blob) => string>();
const revokeBlob = vi.fn();
let dispose = () => {};
const reportUrl = '/api/plugins/forsic/report-bundles/synthetic/executive.html?case_id=case-a';

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('fetch', fetchMock);
  createBlob.mockReturnValue('blob:synthetic-report');
  Object.defineProperty(URL, 'createObjectURL', {value: createBlob, configurable: true});
  Object.defineProperty(URL, 'revokeObjectURL', {value: revokeBlob, configurable: true});
  window.__HERMES_SESSION_TOKEN__ = 'synthetic-session';
  fetchMock.mockResolvedValue({ok: true, status: 200, headers: new Headers({'Content-Disposition': 'attachment; filename="report.html"'}), blob: async () => new NodeBlob(['<html><body>합성 보고서</body></html>'], {type: 'text/html'})});
});
afterEach(() => {
  dispose(); document.body.innerHTML = ''; delete window.__HERMES_SESSION_TOKEN__;
  vi.restoreAllMocks(); vi.unstubAllGlobals(); fetchMock.mockReset(); createBlob.mockReset(); revokeBlob.mockClear();
});

async function render(name = 'executive.html', url = reportUrl, method?: 'POST') {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<ForsicReportLink file={{name, url, method}} profile="analyst"/>));
  dispose = () => {act(() => root.unmount());};
  return {host, open: async () => {await act(async () => host.querySelector('button')!.click());}, rerender: async (profile: string) => {await act(async () => root.render(<ForsicReportLink file={{name,url,method}} profile={profile}/>));}};
}

it('opens HTML using native session auth and explicit profile inside a sandboxed reader', async () => {
  const {host, open} = await render(); await open();
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe(`${reportUrl}&profile=analyst`);
  expect(options.headers.get('X-Hermes-Session-Token')).toBe('synthetic-session');
  expect(options.credentials).toBe('include');
  expect(options.signal).toBeInstanceOf(AbortSignal);
  expect(host.querySelector('a')).toBeNull();
  const reader = document.querySelector('[role="dialog"]')!;
  const frame = reader.querySelector('iframe')!;
  expect(frame.hasAttribute('src')).toBe(false);
  expect(frame.getAttribute('srcdoc')).toContain('<html><body>합성 보고서</body></html>');
  expect(frame.getAttribute('sandbox')).toBe('');
  expect(reader.querySelector('a')?.getAttribute('download')).toBe('report.html');
  expect(reader.querySelector('a')?.getAttribute('href')).toBe('blob:synthetic-report');
  expect(document.activeElement?.getAttribute('aria-label')).toBe('보고서 닫기');
  await act(async () => (reader.querySelector('button') as HTMLButtonElement).click());
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(revokeBlob).toHaveBeenCalledExactlyOnceWith('blob:synthetic-report');
  expect(document.activeElement).toBe(host.querySelector('button'));
});

it('restricts preview resources before the original HTML while preserving exact download bytes', async () => {
  const original = '<!doctype html><html><head><link rel="stylesheet" href="https://example.invalid/style.css"></head><body><img src="https://example.invalid/image.png"><script>synthetic()</script>합성</body></html>';
  fetchMock.mockResolvedValue({ok: true, status: 200, headers: new Headers(), blob: async () => new NodeBlob([original], {type: 'text/html'})});
  const {open} = await render(); await open();
  const download = await createBlob.mock.calls[0][0].text();
  const frame = document.querySelector('iframe')!;
  const preview = frame.getAttribute('srcdoc');
  expect(download).toBe(original);
  const policy = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; base-uri 'none'; form-action 'none'">`;
  expect(preview).toBe(policy + original);
  expect(frame.getAttribute('sandbox')).toBe('');
  expect(frame.hasAttribute('src')).toBe(false);
  expect(createBlob).toHaveBeenCalledTimes(1);
  dispose(); dispose = () => {};
  expect(revokeBlob).toHaveBeenCalledExactlyOnceWith('blob:synthetic-report');
});

it('downloads a binary report with cookie auth and a safe server-provided UTF-8 filename', async () => {
  delete window.__HERMES_SESSION_TOKEN__;
  fetchMock.mockResolvedValue({ok: true, status: 200, headers: new Headers({'Content-Disposition': "attachment; filename*=UTF-8''%ED%95%A9%EC%84%B1%2F%EB%B3%B4%EA%B3%A0%EC%84%9C.docx"}), blob: async () => new Blob(['synthetic docx'])});
  const clicks: {href: string; name: string}[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {clicks.push({href: this.href, name: this.download});});
  const {open} = await render('executive.docx', reportUrl.replace('.html', '.docx'));
  await open();
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).not.toContain('token');
  expect(options.headers.has('X-Hermes-Session-Token')).toBe(false);
  expect(options.credentials).toBe('include');
  expect(clicks).toEqual([{href: 'blob:synthetic-report', name: '합성_보고서.docx'}]);
  expect(document.querySelector('iframe')).toBeNull();
  dispose(); dispose = () => {};
  expect(revokeBlob).toHaveBeenCalledExactlyOnceWith('blob:synthetic-report');
});

it.each([401, 404])('keeps a failed %s download in the current screen with a retryable Korean message', async status => {
  fetchMock.mockResolvedValue({ok: false, status});
  const {host, open} = await render(); await open();
  expect(host.querySelector('[role="status"]')?.textContent).toContain(status === 401 ? '인증이 만료' : '보고서를 열지 못했어요');
  expect(host.textContent).not.toContain('Unauthorized');
  expect(createBlob).not.toHaveBeenCalled();
  expect(host.querySelector('button')?.disabled).toBe(false);
});

it('aborts an outstanding download on unmount without publishing a stale preview', async () => {
  let complete!: (value: unknown) => void;
  fetchMock.mockReturnValue(new Promise(resolve => {complete = resolve;}));
  const {open} = await render(); await open();
  const signal = fetchMock.mock.calls[0][1].signal as AbortSignal;
  dispose(); dispose = () => {};
  expect(signal.aborted).toBe(true);
  await act(async () => complete({ok: true, status: 200, headers: new Headers(), blob: async () => new Blob(['stale'])}));
  expect(createBlob).not.toHaveBeenCalled();
  expect(document.querySelector('iframe')).toBeNull();
});

it('does not publish or retain a preview when unmounted while reading authenticated HTML', async () => {
  let complete!: (text: string) => void;
  fetchMock.mockResolvedValue({ok: true, status: 200, headers: new Headers(), blob: async () => ({text: () => new Promise<string>(resolve => {complete = resolve;})})});
  const {open} = await render(); await open();
  dispose(); dispose = () => {};
  await act(async () => complete('<html>stale report</html>'));
  expect(createBlob).not.toHaveBeenCalled();
  expect(document.querySelector('iframe')).toBeNull();
});

it('refuses to send session authentication outside the report API', async () => {
  const {host, open} = await render('executive.html', 'https://example.invalid/report.html'); await open();
  expect(fetchMock).not.toHaveBeenCalled();
  expect(host.querySelector('[role="status"]')?.textContent).toContain('다시 시도');
});

const wordUrl = `/api/plugins/forsic/report-bundles/${'a'.repeat(64)}/word?case_id=case-a`;
const wordResponse = () => ({ok:true,status:200,headers:new Headers({'Content-Type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','Content-Disposition':"attachment; filename*=UTF-8''%ED%95%A9%EC%84%B1%2F%EB%B3%B4%EA%B3%A0%EC%84%9C.docx"}),blob:async()=>new NodeBlob(['synthetic word bytes'])});

it('converts Word only on an explicit click, once while pending, with native auth and exact version descriptor',async()=>{
  let complete!: (value:unknown)=>void;
  fetchMock.mockReturnValue(new Promise(resolve=>{complete=resolve;}));
  const downloads:string[]=[];
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(function(this:HTMLAnchorElement){downloads.push(this.download);});
  const {host,open}=await render('integrated.docx',wordUrl,'POST');
  expect(fetchMock).not.toHaveBeenCalled();
  await open();await open();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0][0]).toBe(wordUrl+'&profile=analyst');
  const options=fetchMock.mock.calls[0][1];
  expect(options.method).toBe('POST');
  expect(options.headers.get('X-Hermes-Session-Token')).toBe('synthetic-session');
  expect(options.credentials).toBe('include');
  expect(host.textContent).toContain('Word 변환 중…');
  expect(host.querySelector('button')?.disabled).toBe(true);
  expect(downloads).toEqual([]);
  await act(async()=>complete(wordResponse()));
  expect(downloads).toEqual(['합성_보고서.docx']);
  expect(document.querySelector('iframe')).toBeNull();
  expect(host.querySelector('button')?.disabled).toBe(false);
});

it.each([401,500,200])('keeps failed Word conversion %s retryable without a false download',async status=>{
  const downloads=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
  fetchMock.mockResolvedValue({ok:status===200,status,headers:new Headers({'Content-Type':'application/json'}),blob:async()=>new NodeBlob(['{"error":"conversion failed"}'])});
  const {host,open}=await render('integrated.docx',wordUrl,'POST');await open();
  expect(host.textContent).toContain(status===401?'인증이 만료':'Word 변환에 실패');
  expect(downloads).not.toHaveBeenCalled();expect(createBlob).not.toHaveBeenCalled();
  expect(host.querySelector('button')?.disabled).toBe(false);
  fetchMock.mockResolvedValue(wordResponse());await open();
  expect(downloads).toHaveBeenCalledTimes(1);expect(fetchMock).toHaveBeenCalledTimes(2);
});

it('aborts Word conversion on profile changes and does not let the stale response download',async()=>{
  let complete!:(value:unknown)=>void;
  fetchMock.mockReturnValue(new Promise(resolve=>{complete=resolve;}));
  const downloads=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
  const {host,open,rerender}=await render('integrated.docx',wordUrl,'POST');await open();
  const signal=fetchMock.mock.calls[0][1].signal;
  await rerender('different-profile');expect(signal.aborted).toBe(true);
  expect(host.querySelector('button')?.disabled).toBe(false);
  await act(async()=>complete(wordResponse()));
  expect(downloads).not.toHaveBeenCalled();expect(createBlob).not.toHaveBeenCalled();
  fetchMock.mockResolvedValue(wordResponse());await open();
  expect(fetchMock.mock.calls[1][0]).toContain('profile=different-profile');expect(downloads).toHaveBeenCalledTimes(1);
});

it.each([wordUrl.replace('/word?','/integrated.docx?'),wordUrl.replace('a'.repeat(64),'synthetic'),wordUrl+'#fragment','https://example.invalid'+wordUrl])('rejects an unsupported POST export URL before sending authentication: %s',async url=>{
  const {host,open}=await render('integrated.docx',url,'POST');await open();
  expect(fetchMock).not.toHaveBeenCalled();expect(host.textContent).toContain('Word 변환에 실패');
});
