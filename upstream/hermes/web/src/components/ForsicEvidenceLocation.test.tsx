// @vitest-environment jsdom
import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {ForsicEvidenceLocation} from './ForsicEvidenceLocation'
const fetchJSON = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api', () => ({fetchJSON}))
let host: HTMLDivElement, root: Root
beforeEach(() => {Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});fetchJSON.mockReset();host=document.createElement('div');document.body.append(host);root=createRoot(host)})
afterEach(async () => {await act(async () => root.unmount());host.remove()})
it('shows the public share label instead of the server transport path', async () => {
  fetchJSON.mockResolvedValue({display_path:'smb://files.example.invalid/archive',default_path:'/synthetic/private-mount',restricted:true,available:true})
  await act(async () => root.render(<ForsicEvidenceLocation />))
  expect(host.textContent).toContain('smb://files.example.invalid/archive')
  expect(host.textContent).toContain('이 공유 폴더 안의 자료')
  expect(host.textContent).not.toContain('/synthetic/private-mount')
})
it('reports unavailable configured storage without implying an empty share', async () => {
  fetchJSON.mockResolvedValue({display_path:'smb://files.example.invalid/archive',available:false,restricted:true})
  await act(async () => root.render(<ForsicEvidenceLocation />))
  expect(host.querySelector('[role="status"]')?.textContent).toContain('연결할 수 없습니다')
})
