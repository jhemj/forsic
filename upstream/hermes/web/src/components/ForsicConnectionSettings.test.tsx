// @vitest-environment jsdom
import * as React from 'react'
import {act, type ComponentType} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'

let host: HTMLDivElement, root: Root, Settings: ComponentType<{onClose?: () => void,embedded?:boolean,section?:'llm'|'gti'|'telegram'}>
let pluginLocation: {search:string}
const fetchJSON=vi.fn()
beforeEach(() => {
  Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true})
  HTMLDialogElement.prototype.showModal=function() {this.open=true}
  fetchJSON.mockReset()
  const module={exports:{} as {ConnectionSettings:typeof Settings}}
  pluginLocation={search:''}
  const sdkWindow={location:pluginLocation,dispatchEvent:vi.fn(),__HERMES_PLUGIN_SDK__:{React,fetchJSON},__HERMES_PLUGINS__:{register:vi.fn(),registerSlot:vi.fn()}}
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
  expect(host.querySelector('a')?.getAttribute('href')).toBe('/settings?section=hosting')
  expect([...host.querySelectorAll<HTMLInputElement>('input[type=password]')].every(input => input.value === '')).toBe(true)
  expect(fetchJSON).toHaveBeenNthCalledWith(1,'/api/plugins/forsic/setup')
  expect(fetchJSON).toHaveBeenNthCalledWith(2,'/api/plugins/forsic/connections')
})

function connection(ren?: {enabled:boolean,base_url:string,model:string,key_configured:boolean}) {
  return {llm:{base_url:'http://localhost:11434/v1',model:'forsic-main',context_length:null},llm_current:{base_url:'http://localhost:11434/v1',model:'forsic-main',context_length:null},jev:{base_url:''},gti:{enabled:false,key_configured:false,allow_public_network_indicators:false,base_url:'https://synthetic.test'},telegram:{group:'',assistant_key_configured:false,user_key_configured:false},...(ren ? {ren_llm:ren} : {})}
}
function inputFor(label:string) {
  const labelNode=[...host.querySelectorAll('label')].find(node=>node.textContent?.startsWith(label))
  if(!labelNode) throw new Error('Missing label: '+label)
  return labelNode.querySelector('input') as HTMLInputElement
}
async function change(label:string,value:string) {
  await act(async () => {
    const input=inputFor(label)
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,value)
    input.dispatchEvent(new Event('input',{bubbles:true}))
  })
}
async function toggle(label:string) {await act(async () => inputFor(label).click())}
async function keyAction(label:string) {await act(async () => host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!.click())}
async function submit() {await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})))}
async function load(ren?: {enabled:boolean,base_url:string,model:string,key_configured:boolean}) {
  fetchJSON.mockResolvedValueOnce({local_admin:true}).mockResolvedValueOnce(connection(ren))
  await act(async () => root.render(<Settings onClose={() => {}} />))
}

it('defaults old connections to one model and retains entered Ren details across toggles',async () => {
  await load()
  expect(inputFor('추가 모델 사용').checked).toBe(false)
  expect(host.textContent).not.toContain('렌 전용 URL')
  await toggle('추가 모델 사용')
  expect(inputFor('렌 전용 URL').value).toBe('')
  expect(inputFor('렌 전용 모델').value).toBe('')
  await change('렌 전용 URL','https://ren.synthetic.test/v1')
  await change('렌 전용 모델','ren-review')
  await toggle('추가 모델 사용')
  expect(host.textContent).toContain('현재 포식이 모델')
  await toggle('추가 모델 사용')
  expect(inputFor('렌 전용 URL').value).toBe('https://ren.synthetic.test/v1')
  expect(inputFor('렌 전용 모델').value).toBe('ren-review')
  expect(inputFor('모델 이름').value).toBe('forsic-main')
  expect(host.textContent).toContain('독립된 검토 문맥')
  expect(fetchJSON).toHaveBeenCalledTimes(2)
})

it('validates enabled Ren configuration before saving without any model request',async () => {
  await load()
  await toggle('추가 모델 사용')
  await submit()
  expect(host.querySelector('[role=alert]')?.textContent).toContain('모두 입력')
  await change('렌 전용 URL','file:///synthetic')
  await change('렌 전용 모델','ren-review')
  await submit()
  expect(fetchJSON).toHaveBeenCalledTimes(2)
  await change('렌 전용 URL','https://key@ren.synthetic.test/v1')
  await submit()
  expect(fetchJSON).toHaveBeenCalledTimes(2)
})

it('saves independent Ren fields and clears the entered secret after save',async () => {
  await load()
  await toggle('추가 모델 사용')
  await change('렌 전용 URL','https://ren.synthetic.test/v1')
  await change('렌 전용 모델','ren-review')
  await change('렌 전용 API 키','synthetic-ren-key')
  fetchJSON.mockResolvedValueOnce(connection({enabled:true,base_url:'https://ren.synthetic.test/v1',model:'ren-review',key_configured:true}))
  await submit()
  const [url,options]=fetchJSON.mock.calls[2]
  expect(url).toBe('/api/plugins/forsic/connections')
  expect(options.method).toBe('PUT')
  expect(JSON.parse(options.body)).toMatchObject({llm_model:'forsic-main',ren_llm_enabled:true,ren_llm_base_url:'https://ren.synthetic.test/v1',ren_llm_model:'ren-review',ren_llm_api_key:'synthetic-ren-key'})
  expect(host.querySelector('button[aria-label="렌 전용 API 키 변경"]')).not.toBeNull()
  expect([...host.querySelectorAll('input')].some(input=>input.value==='synthetic-ren-key')).toBe(false)
  expect(fetchJSON).toHaveBeenCalledTimes(3)
})

it('loads a masked Ren key, supports independent deletion and preserves details while disabled',async () => {
  const ren={enabled:true,base_url:'https://ren.synthetic.test/v1',model:'ren-review',key_configured:true}
  await load(ren)
  await keyAction('렌 전용 API 키 변경')
  await change('렌 전용 API 키','replacement')
  await keyAction('렌 전용 API 키 유지')
  await keyAction('렌 전용 API 키 없이 사용')
  expect(inputFor('렌 전용 API 키').value).toBe('')
  await toggle('추가 모델 사용')
  fetchJSON.mockResolvedValueOnce(connection({...ren,enabled:false,key_configured:false}))
  await submit()
  expect(JSON.parse(fetchJSON.mock.calls[2][1].body)).toMatchObject({ren_llm_enabled:false,ren_llm_base_url:ren.base_url,ren_llm_model:ren.model,ren_llm_api_key:''})
  await toggle('추가 모델 사용')
  expect(inputFor('렌 전용 URL').value).toBe(ren.base_url)
  expect(inputFor('렌 전용 API 키').disabled).toBe(false)
})

it('saves optional main and Ren API keys independently and clears blanks explicitly',async () => {
  await load({enabled:true,base_url:'https://ren.synthetic.test/v1',model:'ren-review',key_configured:false})
  expect(inputFor('조사 모델 API 키').type).toBe('password')
  expect(inputFor('렌 전용 API 키').placeholder).toContain('빈칸')
  await change('조사 모델 API 키','synthetic-main-key')
  await change('렌 전용 API 키','')
  const saved=connection({enabled:true,base_url:'https://ren.synthetic.test/v1',model:'ren-review',key_configured:false})
  fetchJSON.mockResolvedValueOnce({...saved,llm:{...saved.llm,key_configured:true},llm_pending:true})
  await submit()
  expect(JSON.parse(fetchJSON.mock.calls[2][1].body)).toMatchObject({llm_api_key:'synthetic-main-key',ren_llm_api_key:''})
  expect(host.querySelector('button[aria-label="조사 모델 API 키 변경"]')).not.toBeNull()
  expect(host.textContent).toContain('서버를 다시 시작할 필요 없어요')
  expect(host.innerHTML).not.toContain('synthetic-main-key')
  await keyAction('조사 모델 API 키 없이 사용')
  expect(inputFor('조사 모델 API 키').value).toBe('')
  fetchJSON.mockResolvedValueOnce(saved)
  await submit()
  expect(JSON.parse(fetchJSON.mock.calls[3][1].body).llm_api_key).toBe('')
})

it('omits both registered credentials unless the user edits them',async () => {
  const saved=connection({enabled:true,base_url:'https://ren.synthetic.test/v1',model:'ren-review',key_configured:true})
  const configured={...saved,llm:{...saved.llm,key_configured:true}}
  fetchJSON.mockResolvedValueOnce({local_admin:true}).mockResolvedValueOnce(configured)
  await act(async () => root.render(<Settings onClose={() => {}} />))
  await keyAction('조사 모델 API 키 변경')
  await change('조사 모델 API 키','unsaved-replacement')
  await keyAction('조사 모델 API 키 유지')
  await toggle('추가 모델 사용')
  fetchJSON.mockResolvedValueOnce({...configured,ren_llm:{...configured.ren_llm!,enabled:false}})
  await submit()
  const body=JSON.parse(fetchJSON.mock.calls[2][1].body)
  expect(body).not.toHaveProperty('llm_api_key')
  expect(body).not.toHaveProperty('ren_llm_api_key')
  expect(body).not.toHaveProperty('clear_llm_key')
  expect(body).not.toHaveProperty('clear_ren_llm_key')
})


it('embeds the controlled category without a modal, header or inner navigation',async () => {
  fetchJSON.mockResolvedValueOnce({local_admin:true}).mockResolvedValueOnce(connection())
  await act(async () => root.render(<Settings embedded section="llm" />))
  expect(host.querySelector('dialog,header,nav')).toBeNull()
  expect(host.querySelector('form')).not.toBeNull()
  expect(host.querySelector('button[type=submit]')?.textContent).toBe('설정 저장')
  expect(host.textContent).toContain('저장된 기본 모델')
  expect(host.textContent).toContain('다음 대화 차례')
  expect(fetchJSON).toHaveBeenCalledTimes(2)
})

it('preserves unsaved fields across controlled categories',async () => {
  fetchJSON.mockResolvedValueOnce({local_admin:true}).mockResolvedValueOnce(connection())
  await act(async () => root.render(<Settings embedded section="llm" />))
  await change('모델 이름','unsaved-model')
  await change('조사 모델 API 키','unsaved-key')
  await act(async () => root.render(<Settings embedded section="telegram" />))
  expect(inputFor('모델 이름').closest('section')?.hidden).toBe(true)
  await change('그룹 ID 또는 공개 그룹 주소','@synthetic_group')
  await change('포식이 봇 토큰','unsaved-token')
  await act(async () => root.render(<Settings embedded section="gti" />))
  await act(async () => root.render(<Settings embedded section="llm" />))
  expect(inputFor('모델 이름').value).toBe('unsaved-model')
  expect(inputFor('조사 모델 API 키').value).toBe('unsaved-key')
  await act(async () => root.render(<Settings embedded section="telegram" />))
  expect(inputFor('그룹 ID 또는 공개 그룹 주소').value).toBe('@synthetic_group')
  expect(inputFor('포식이 봇 토큰').value).toBe('unsaved-token')
  expect(fetchJSON).toHaveBeenCalledTimes(2)
})

it('embeds remote status without requesting or showing credentials',async () => {
  fetchJSON.mockResolvedValue({local_admin:false,llm_configured:true,evidence_configured:true,hosting_current:{mode:'private_lan'}})
  await act(async () => root.render(<Settings embedded section="telegram" />))
  expect(host.querySelector('dialog,form,input,nav')).toBeNull()
  expect(host.textContent).toContain('연결 상태')
  expect(fetchJSON).toHaveBeenCalledExactlyOnceWith('/api/plugins/forsic/setup')
})


it('keeps the tenant, profile, case and conversation in the hosting settings link',async () => {
  pluginLocation.search='?tenant_id=synthetic-tenant&profile=review&case=CASE-synthetic&resume=session-synthetic'
  await load()
  const link=new URL(host.querySelector('a')!.getAttribute('href')!,'https://synthetic.test')
  expect(link.pathname).toBe('/settings')
  expect(Object.fromEntries(link.searchParams)).toEqual({section:'hosting',tenant_id:'synthetic-tenant',profile:'review',case:'CASE-synthetic',resume:'session-synthetic'})
})

async function testModel(name:'포식이'|'렌') {
  await act(async()=>host.querySelector<HTMLButtonElement>(`button[aria-label="${name} 모델 테스트"]`)!.click())
}
it('tests unsaved main draft without saving and shows actual response latency',async()=>{
  await load()
  await change('모델 이름','draft-model')
  await change('조사 모델 API 키','synthetic-draft-key')
  fetchJSON.mockResolvedValueOnce({status:'ok',model_responded:true,latency_ms:1234})
  await testModel('포식이')
  expect(fetchJSON.mock.calls[2][0]).toBe('/api/plugins/forsic/connections/test')
  expect(JSON.parse(fetchJSON.mock.calls[2][1].body)).toEqual({connection:'main',base_url:'http://localhost:11434/v1',model:'draft-model',api_key:'synthetic-draft-key'})
  expect(host.textContent).toContain('모델 응답 확인 · 1.2초')
  expect(fetchJSON.mock.calls.every(([,options])=>options?.method!=='PUT')).toBe(true)
  await change('모델 이름','changed-again')
  expect(host.textContent).not.toContain('모델 응답 확인')
})
it('tests Ren independently with the saved key omitted or explicit keyless draft',async()=>{
  await load({enabled:true,base_url:'https://ren.synthetic.test/v1',model:'ren-test',key_configured:true})
  fetchJSON.mockResolvedValueOnce({status:'authentication_error',model_responded:false,latency_ms:12,detail:'untrusted-provider-text'})
  await testModel('렌')
  expect(JSON.parse(fetchJSON.mock.calls[2][1].body)).toEqual({connection:'ren',base_url:'https://ren.synthetic.test/v1',model:'ren-test'})
  expect(host.textContent).toContain('API 키 또는 모델 접근 권한')
  expect(host.textContent).not.toContain('untrusted-provider-text')
  await keyAction('렌 전용 API 키 없이 사용')
  fetchJSON.mockResolvedValueOnce({status:'ok',model_responded:true,latency_ms:20})
  await testModel('렌')
  expect(JSON.parse(fetchJSON.mock.calls[3][1].body).api_key).toBe('')
})
it('deduplicates pending tests and discards a result for an edited draft',async()=>{
  await load()
  let finish!:(value:unknown)=>void
  fetchJSON.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}))
  await testModel('포식이')
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="포식이 모델 테스트"]')!.disabled).toBe(true)
  await testModel('포식이')
  expect(fetchJSON).toHaveBeenCalledTimes(3)
  await change('모델 이름','new-draft')
  await act(async()=>finish({status:'ok',model_responded:true,latency_ms:500}))
  expect(host.textContent).not.toContain('모델 응답 확인')
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="포식이 모델 테스트"]')!.disabled).toBe(false)
})
it('does not call a response-free connection success a model response',async()=>{
  await load()
  fetchJSON.mockResolvedValueOnce({status:'ok',reachable:true,model_responded:false,latency_ms:10})
  await testModel('포식이')
  expect(host.textContent).not.toContain('모델 응답 확인')
  expect(host.querySelector('.forsic-model-test [role=alert]')).not.toBeNull()
})
