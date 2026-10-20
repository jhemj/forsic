// @vitest-environment jsdom
import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import type {ServerRequest} from '@hermes/shared'
import {ForsicRequests} from './ForsicRequests'
let container: HTMLDivElement, root: Root
beforeEach(() => {Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true}); container = document.createElement('div'); document.body.append(container); root = createRoot(container)})
afterEach(async () => {await act(async () => root.unmount()); container.remove()})
function request(method: string, params: Record<string, unknown>, id = 'r'): ServerRequest {return {id, method, params, respond: vi.fn(), fail: vi.fn()}}
async function render(requests: ServerRequest[], extra: {connected?: boolean; pendingApproval?: Record<string, unknown>; onApproval?: (choice: string) => Promise<void>} = {}) {await act(async () => root.render(<ForsicRequests requests={requests} connected={true} onApproval={vi.fn()} {...extra} />))}
async function setValue(element: HTMLInputElement | HTMLTextAreaElement, value: string) {await act(async () => {const proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(element, value); element.dispatchEvent(new Event('input', {bubbles: true}))})}
async function submit() {await act(async () => {container.querySelector('form')!.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))})}
async function click(element: HTMLElement) {await act(async () => {element.click()})}

it('never chooses an implicit answer and sends multi-select as the native JSON string', async () => {
 const r = request('clarify', {questions: [{qid: 'q', question: '대상 선택', choices: ['A', 'B'], multi_select: true}]})
 await render([r]); const send = container.querySelector<HTMLButtonElement>('button[type=submit]')!
 expect(send.disabled).toBe(true); expect(r.respond).not.toHaveBeenCalled()
 const options = container.querySelectorAll<HTMLInputElement>('input[type=checkbox]')
 await click(options[0]); await click(options[1]); await submit()
 expect(r.respond).toHaveBeenCalledExactlyOnceWith({answers: {q: '["A","B"]'}})
 await submit(); expect(r.respond).toHaveBeenCalledTimes(1)
})
it('keeps replayed answer locks, requires freeform input, and preserves native null skips', async () => {
 const r = request('clarify', {questions: [{qid: 'locked', question: '이전 답'}, {qid: 'skip', question: '건너뛴 답'}, {qid: 'new', question: '새 질문'}], answers: {locked: '보존한 답', skip: null}})
 await render([r]); const fields = container.querySelectorAll('fieldset')
 expect(fields[0].disabled).toBe(true); expect(fields[1].disabled).toBe(true)
 expect(container.textContent).toContain('보존한 답'); expect(container.textContent).toContain('건너뛴 질문')
 await setValue(container.querySelector('textarea')!, '명시한 답'); await submit()
 expect(r.respond).toHaveBeenCalledExactlyOnceWith({answers: {locked: '보존한 답', skip: null, new: '명시한 답'}})
})
it('takes a selected radio value or explicit freeform without prefilling a choice', async () => {
 const r = request('clarify', {questions: [{qid: 'q', question: '선택', choices: ['A', 'B']}]})
 await render([r]); expect(container.querySelector<HTMLInputElement>('input')!.checked).toBe(false)
 await click(container.querySelector<HTMLInputElement>('input')!); await setValue(container.querySelector('textarea')!, '직접 쓴 답'); await submit()
 expect(r.respond).toHaveBeenCalledExactlyOnceWith({answers: {q: '직접 쓴 답'}})
})
it('honors supplied approval choices and session/permanent restrictions', async () => {
 const r = request('approval', {choices: ['once', 'session', 'always', 'deny'], allow_session: false, command: '<script>danger()</script>'})
 const onApproval = vi.fn().mockResolvedValue(undefined)
 await render([r], {onApproval}); expect(onApproval).not.toHaveBeenCalled(); expect([...container.querySelectorAll('button')].map(b => b.textContent)).toEqual(['이번만 허용', '거부'])
 expect(container.querySelector('script')).toBeNull()
 await click(container.querySelector('button')!); expect(onApproval).toHaveBeenCalledExactlyOnceWith('once'); expect(r.respond).not.toHaveBeenCalled()
})
it('sends fallback approval only on an explicit choice and hides duplicate mirrors', async () => {
 const onApproval = vi.fn().mockResolvedValue(undefined), pendingApproval = {request_id: 'p', choices: ['deny']}
 await render([], {pendingApproval, onApproval}); expect(onApproval).not.toHaveBeenCalled()
 await click(container.querySelector('button')!); expect(onApproval).toHaveBeenCalledExactlyOnceWith('deny')
 await render([request('approval', {choices: ['once']})], {pendingApproval, onApproval})
 expect(container.querySelectorAll('.fr-approval')).toHaveLength(1)
})
it.each(['sudo', 'secret', 'vault.unlock_prompt', 'vault.code'])('masks %s input and sends only explicit values', async method => {
 const r = request(method, {env_var: 'TEST_VALUE', prompt: '값을 입력하세요'})
 await render([r]); const input = container.querySelector('input')!
 expect(input.type).toBe('password'); expect(input.autocomplete).toBe('off'); expect(r.respond).not.toHaveBeenCalled()
 await setValue(input, 'synthetic-value'); await submit()
 expect(r.respond).toHaveBeenCalledExactlyOnceWith({value: 'synthetic-value'}); expect(input.value).toBe('')
})
it('requires an explicit skip and prevents responses while disconnected', async () => {
 const r = request('secret', {env_var: 'TEST'})
 await render([r], {connected: false}); expect(container.querySelector<HTMLInputElement>('input')?.disabled).toBe(true)
 await submit(); expect(r.respond).not.toHaveBeenCalled()
 await render([r]); await click(container.querySelector<HTMLButtonElement>('button[type=button]')!)
 expect(r.respond).toHaveBeenCalledExactlyOnceWith({value: ''})
})
it('does not fabricate complex or unknown native responses', async () => {
 const r = request('vault.save_login', {site: 'example.test'})
 await render([r]); expect(container.textContent).toContain('터미널에서 응답해 주세요.')
 expect(container.querySelector('input')).toBeNull(); expect(r.respond).not.toHaveBeenCalled(); expect(r.fail).not.toHaveBeenCalled()
})
