// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'
import { ChatSidebar } from './ChatSidebar'

const fetchJSON = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api', () => ({ fetchJSON }))
vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'ko' }) }))
vi.mock('@/lib/gatewayClient', () => ({ GatewayClient: class {
  onState(fn: (state: string) => void) { fn('open'); return () => {} }
  on() { return () => {} }
  connect() { return Promise.resolve() }
  request() { return Promise.resolve({session_id:'sidecar'}) }
  close() {}
} }))
vi.mock('@/lib/eventsFeedClient', () => ({ EventsFeedClient: class {
  onClose() { return () => {} }
  onState() { return () => {} }
  on() { return () => {} }
  connect() { return Promise.resolve() }
  close() {}
} }))

it('opens Forsic settings from the configured model card and refreshes without model probes', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fetchJSON.mockResolvedValue({llm_current:{model:'provider/org/full-model',base_url:'http://name:secret@host.test:8000/v1?token=secret'}})
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const open = vi.fn()
  window.addEventListener('forsic:open-connection-settings', open)
  try {
    await act(async () => { root.render(<MemoryRouter><ChatSidebar channel="test" profile="case-profile" /></MemoryRouter>) })
    expect(fetchJSON).toHaveBeenCalledWith('/api/plugins/forsic/connections')
    expect(container.textContent).toContain('provider/org/full-model')
    expect(container.textContent).toContain('http://host.test:8000/v1')
    expect(container.textContent).not.toContain('secret')
    expect(container.textContent).toContain('화면 연결됨')
    await act(async () => { container.querySelector<HTMLButtonElement>('[aria-label="조사 모델 연결 설정 열기"]')!.click() })
    expect(open).toHaveBeenCalledOnce()
    fetchJSON.mockResolvedValue({llm_current:{model:'provider/org/updated-model',base_url:'https://host.test/v1'}})
    await act(async () => { window.dispatchEvent(new Event('forsic:connections-changed')) })
    expect(container.textContent).toContain('provider/org/updated-model')
  } finally {
    window.removeEventListener('forsic:open-connection-settings', open)
    await act(async () => { root.unmount() })
    container.remove()
  }
})
