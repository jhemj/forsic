// @vitest-environment jsdom
import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect, it, vi} from 'vitest'
import {ForsicConnectionCard} from './ForsicConnectionCard'
const fetchJSON = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api', () => ({fetchJSON}))
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
it('places live connection status in the configured model card without probing or starting a session', async () => {
  fetchJSON.mockReset().mockResolvedValueOnce({local_admin:true}).mockResolvedValue({llm_current: {model: 'synthetic/model', base_url: 'https://model.test/v1'}})
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host)
  try {
    await act(async () => root.render(<ForsicConnectionCard connected={true} connectionText="연결됨 · 조사 중"/>))
    expect(host.textContent).toContain('synthetic/model')
    expect(host.querySelector('[role="status"]')?.textContent).toBe('연결됨')
    expect(host.textContent).not.toContain('조사 중')
    expect(fetchJSON).toHaveBeenNthCalledWith(1,'/api/plugins/forsic/setup')
    expect(fetchJSON).toHaveBeenNthCalledWith(2,'/api/plugins/forsic/connections')
    await act(async () => root.render(<ForsicConnectionCard connected={false} reconnecting={true} connectionText="저장된 대화를 표시하며 연결을 다시 확인합니다."/>))
    expect(host.querySelector('[role="status"]')?.textContent).toBe('재연결 중')
    expect(host.querySelector('[role="status"]')?.getAttribute('title')).toContain('채팅 화면 연결 상태')
    expect(host.querySelector('[role="status"]')?.getAttribute('title')).toContain('저장된 대화')
    await act(async () => root.render(<ForsicConnectionCard connected={false} reconnecting={false}/>))
    expect(host.querySelector('[role="status"]')?.textContent).toBe('연결 확인 필요')
    expect(fetchJSON).toHaveBeenCalledTimes(2)
  } finally {await act(async () => root.unmount()); host.remove()}
})

it('shows safe remote status without requesting the local connections editor', async () => {
  fetchJSON.mockReset().mockResolvedValue({local_admin:false,llm_configured:true})
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
  try {
    await act(async () => root.render(<ForsicConnectionCard connected={true} />))
    expect(host.textContent).toContain('모델 설정됨')
    expect(host.textContent).toContain('설치된 컴퓨터에서 확인')
    expect(fetchJSON).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/setup')
  } finally {await act(async () => root.unmount());host.remove()}
})
