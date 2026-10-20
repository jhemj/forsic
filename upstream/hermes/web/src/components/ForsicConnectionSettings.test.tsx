// @vitest-environment jsdom
import * as React from 'react'
import {act, type ComponentType} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'

let host: HTMLDivElement, root: Root, Settings: ComponentType<{onClose: () => void}>
const fetchJSON=vi.fn()
beforeEach(() => {
  Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true})
  HTMLDialogElement.prototype.showModal=function() {this.open=true}
  fetchJSON.mockReset()
  const module={exports:{} as {ConnectionSettings:typeof Settings}}
  const sdkWindow={__HERMES_PLUGIN_SDK__:{React,fetchJSON},__HERMES_PLUGINS__:{register:vi.fn(),registerSlot:vi.fn()}}
  const source=readFileSync(resolve(process.cwd(),'../../../forsic_plugin/dashboard/dist/index.js'),'utf8')
  new Function('window','document','module',source)(sdkWindow,document,module)
  Settings=module.exports.ConnectionSettings
  host=document.createElement('div');document.body.append(host);root=createRoot(host)
})
afterEach(async () => {await act(async () => root.unmount());host.remove()})

it('loads safe remote status without rendering editors or requesting credentials',async () => {
  fetchJSON.mockResolvedValue({local_admin:false,llm_configured:true,evidence_configured:true,hosting_current:{mode:'private_lan'}})
  await act(async () => root.render(<Settings onClose={() => {}} />))
  expect(host.querySelector('dialog')?.open).toBe(true)
  expect(host.textContent).toContain('연결 상태')
  expect(host.textContent).toContain('내부 네트워크')
  expect(host.querySelectorAll('input,form')).toHaveLength(0)
  expect(host.querySelector('a')).toBeNull()
  expect(fetchJSON).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/setup')
})

it('loads masked existing connections locally and links to hosting setup',async () => {
  fetchJSON.mockResolvedValueOnce({local_admin:true}).mockResolvedValueOnce({llm:{base_url:'http://localhost:11434/v1',model:'synthetic',context_length:null},llm_current:{base_url:'http://localhost:11434/v1',model:'synthetic',context_length:null},jev:{base_url:''},gti:{enabled:false,key_configured:true,allow_public_network_indicators:false,base_url:'https://synthetic.test'},telegram:{group:'',assistant_key_configured:true,user_key_configured:false}})
  await act(async () => root.render(<Settings onClose={() => {}} />))
  expect(host.querySelector('a')?.getAttribute('href')).toBe('/forsic?view=setup')
  expect([...host.querySelectorAll<HTMLInputElement>('input[type=password]')].every(input => input.value === '')).toBe(true)
  expect(fetchJSON).toHaveBeenNthCalledWith(1,'/api/plugins/forsic/setup')
  expect(fetchJSON).toHaveBeenNthCalledWith(2,'/api/plugins/forsic/connections')
})
