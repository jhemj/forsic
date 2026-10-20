// @vitest-environment jsdom
import * as React from 'react'
import {act, type ComponentType} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'

let host: HTMLDivElement, root: Root, Identity: ComponentType
const fetchJSON=vi.fn()
const eventName='forsic:review-activity'
const requestName='forsic:review-activity-request'
function activity(overrides: Record<string, unknown>={}) {
  window.dispatchEvent(new CustomEvent(eventName,{detail:{
    profile:'default',sessionId:'synthetic-session',caseId:'synthetic-case',
    active:true,reviewId:'synthetic-review',startedAt:Date.now()-1000,
    observedAt:Date.now(),...overrides,
  }}))
}
function character() {
  return host.querySelector('.forsic-identity')?.getAttribute('data-character')
}
beforeEach(() => {
  vi.useFakeTimers()
  Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true})
  window.history.replaceState({},'', '/chat?profile=default&case=synthetic-case&resume=synthetic-session')
  fetchJSON.mockReset().mockResolvedValue({case:{case_id:'synthetic-case'},events:[]})
  Object.assign(window,{
    __HERMES_PLUGIN_SDK__:{React,fetchJSON},
    __HERMES_PLUGINS__:{register:vi.fn(),registerSlot:vi.fn()},
  })
  const module={exports:{} as {Identity:ComponentType}}
  const source=readFileSync(resolve(process.cwd(),'../../../forsic_plugin/dashboard/dist/index.js'),'utf8')
  new Function('window','document','module',source)(window,document,module)
  Identity=module.exports.Identity
  host=document.createElement('div');document.body.append(host);root=createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove();vi.useRealTimers()
})

it('switches both directions on the authenticated chat projection without fetching reviews again',async () => {
  await act(async () => root.render(<Identity />))
  expect(character()).toBe('forsic')
  await act(async () => activity())
  expect(character()).toBe('ren')
  expect(host.querySelector('.forsic-identity-copy')?.textContent).toBe('렌독립 질문 생성 중')
  expect(host.querySelector('.forsic-character-ren')?.getAttribute('aria-hidden')).toBe('false')
  expect(host.querySelector('.forsic-character-original')?.getAttribute('aria-hidden')).toBe('true')
  await act(async () => activity({active:false,reviewId:null,startedAt:null}))
  expect(character()).toBe('forsic')
  expect(host.querySelector('.forsic-character-ren')?.getAttribute('aria-hidden')).toBe('true')
  expect(fetchJSON.mock.calls.every(([url]) => String(url).includes('/status?'))).toBe(true)
})

it('requests a snapshot when mounted after the chat publisher',async () => {
  const answer=()=>activity()
  window.addEventListener(requestName,answer)
  try {
    await act(async () => root.render(<Identity />))
    expect(character()).toBe('ren')
  } finally {window.removeEventListener(requestName,answer)}
})

it('ignores another case, profile, session, and expired or invalid signals',async () => {
  await act(async () => root.render(<Identity />))
  for(const override of [
    {caseId:'other-case'},{profile:'other-profile'},{sessionId:'other-session'},
    {observedAt:Date.now()-30001},{observedAt:Date.now()+1000},{observedAt:null},{reviewId:null},
  ]) {
    await act(async () => activity(override))
    expect(character()).toBe('forsic')
  }
})

it('expires a lost publisher while allowing the existing chat polls to refresh it',async () => {
  await act(async () => root.render(<Identity />))
  await act(async () => activity())
  await act(async () => vi.advanceTimersByTimeAsync(24000))
  await act(async () => activity())
  await act(async () => vi.advanceTimersByTimeAsync(24000))
  expect(character()).toBe('ren')
  await act(async () => vi.advanceTimersByTimeAsync(6001))
  expect(character()).toBe('forsic')
})

it('clears Ren on route departure and does not infer work from an old case',async () => {
  await act(async () => root.render(<Identity />))
  await act(async () => activity())
  expect(character()).toBe('ren')
  await act(async () => {
    window.history.replaceState({},'', '/forsic?view=history')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  expect(character()).toBe('forsic')
  await act(async () => activity())
  expect(character()).toBe('forsic')
})

it('shows Ren for the review panel without claiming generation and restores Forsic on exit',async () => {
  await act(async () => root.render(<Identity />))
  const select=(selected: boolean, sessionId='synthetic-session') => window.dispatchEvent(new CustomEvent('forsic:review-panel',{
    detail:{profile:'default',sessionId,caseId:'synthetic-case',selected},
  }))
  await act(async () => select(true,'another-session'))
  expect(character()).toBe('forsic')
  await act(async () => select(true))
  expect(character()).toBe('ren')
  expect(host.querySelector('.forsic-identity-copy')?.textContent).toBe('렌독립 검토')
  await act(async () => select(false))
  expect(character()).toBe('forsic')
  await act(async () => {activity(); select(true); select(false)})
  expect(character()).toBe('ren')
  expect(host.textContent).toContain('독립 질문 생성 중')
  await act(async () => activity({active:false}))
  expect(character()).toBe('forsic')
})
