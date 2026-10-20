// @vitest-environment jsdom
import * as React from 'react';
import {act, type ComponentType, type ReactNode} from 'react';
import {createRoot} from 'react-dom/client';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {runInNewContext} from 'node:vm';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {authedFetch} from '@/lib/api';

const fetchMock = vi.fn();
const revoke = vi.fn();
let dispose = () => {};
let ReportDownload: ComponentType<{url: string; name: string; children: ReactNode}>;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('fetch', fetchMock);
  Object.defineProperty(URL, 'createObjectURL', {value: vi.fn(() => 'blob:legacy-report'), configurable: true});
  Object.defineProperty(URL, 'revokeObjectURL', {value: revoke, configurable: true});
  window.__HERMES_SESSION_TOKEN__ = 'synthetic-session';
  const module = {exports: {}};
  runInNewContext(readFileSync(resolve(process.cwd(), '../../../forsic_plugin/dashboard/dist/index.js'), 'utf8'), {
    module, document, URL, URLSearchParams, AbortController,
    window: {location: {search: '?profile=analyst'}, __HERMES_PLUGIN_SDK__: {React, authedFetch}, __HERMES_PLUGINS__: {register: vi.fn(), registerSlot: vi.fn()}},
  });
  ReportDownload = (module.exports as {ReportDownload: typeof ReportDownload}).ReportDownload;
});
afterEach(() => {
  dispose(); document.body.innerHTML = ''; delete window.__HERMES_SESSION_TOKEN__;
  vi.restoreAllMocks(); vi.unstubAllGlobals(); fetchMock.mockReset(); revoke.mockClear();
});
async function render() {
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  await act(async () => root.render(<ReportDownload url="/api/plugins/forsic/report-bundles/synthetic/executive.docx?case_id=case-a" name="executive.docx">Word 내려받기</ReportDownload>));
  dispose = () => {act(() => root.unmount());};
  await act(async () => host.querySelector('button')!.click());
  return host;
}

it('downloads legacy bundle files through the same SDK auth while retaining case and profile', async () => {
  fetchMock.mockResolvedValue({ok: true, status: 200, headers: new Headers({'Content-Disposition': 'attachment; filename="saved.docx"'}), blob: async () => new Blob(['synthetic report'])});
  const downloads: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {downloads.push(`${this.href}|${this.download}`);});
  const host = await render();
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe('/api/plugins/forsic/report-bundles/synthetic/executive.docx?case_id=case-a&profile=analyst');
  expect(options.headers.get('X-Hermes-Session-Token')).toBe('synthetic-session');
  expect(options.credentials).toBe('include');
  expect(downloads).toEqual(['blob:legacy-report|saved.docx']);
  expect(host.querySelector('a')).toBeNull();
  dispose(); dispose = () => {};
  expect(revoke).toHaveBeenCalledExactlyOnceWith('blob:legacy-report');
});

it('shows a Korean authentication error instead of navigating to Unauthorized JSON', async () => {
  fetchMock.mockResolvedValue({ok: false, status: 401});
  const host = await render();
  expect(host.querySelector('[role="status"]')?.textContent).toContain('인증이 만료');
  expect(host.textContent).not.toContain('Unauthorized');
  expect(host.querySelector('button')?.disabled).toBe(false);
});
