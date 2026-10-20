import {useRef, useState} from 'react'
import type {ServerRequest} from '@hermes/shared'
import './forsic-requests.css'

type Data = Record<string, unknown>
const object = (value: unknown): Data => value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}
const text = (value: unknown) => typeof value === 'string' ? value : ''
const approvalLabels: Record<string, string> = {once: '이번만 허용', session: '이 세션에서 허용', always: '항상 허용', deny: '거부'}
function approvalChoices(params: Data) {
  const choices = Array.isArray(params.choices) ? params.choices : ['once', ...(params.smart_denied || params.allow_session === false ? [] : ['session', ...(params.allow_permanent === false ? [] : ['always'])]), 'deny']
  return [...new Set(choices.filter((choice): choice is string => typeof choice === 'string' && choice in approvalLabels && !(choice === 'session' && params.allow_session === false) && !(choice === 'always' && (params.allow_permanent === false || params.allow_session === false || params.smart_denied === true))))]
}

function ApprovalCard({params, connected, send}: {params: Data; connected: boolean; send: (choice: string) => void | Promise<void>}) {
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState(false)
  const lock = useRef(false)
  async function choose(choice: string) {
    if (lock.current || !connected) return
    lock.current = true; setBusy(true); setError(false)
    try {await send(choice); setDone(true)} catch {lock.current = false; setError(true)} finally {setBusy(false)}
  }
  return <section className="fr-card fr-approval" aria-label="실행 승인 요청"><div className="fr-eyebrow">사용자 확인 필요</div><h3>이 작업을 허용할까요?</h3>{text(params.description) && <p>{text(params.description)}</p>}{text(params.command) && <pre>{text(params.command)}</pre>}
    <div className="fr-actions">{approvalChoices(params).map(choice => <button key={choice} disabled={!connected || busy || done} className={choice === 'deny' ? 'fr-secondary' : ''} onClick={() => {void choose(choice)}}>{approvalLabels[choice]}</button>)}</div>
    {done && <p role="status">선택을 전달했습니다.</p>}{error && <p role="alert">전송 결과를 확인해 주세요.</p>}
  </section>
}

interface Question {qid: string; question: string; choices: string[]; multi: boolean}
function ClarifyCard({request, connected}: {request: ServerRequest; connected: boolean}) {
  const questions: Question[] = (Array.isArray(request.params.questions) ? request.params.questions : []).map(object).filter(q => typeof q.qid === 'string' && typeof q.question === 'string').map(q => ({qid: text(q.qid), question: text(q.question), choices: Array.isArray(q.choices) ? q.choices.filter((c): c is string => typeof c === 'string') : [], multi: q.multi_select === true}))
  const locked = object(request.params.answers)
  const [values, setValues] = useState<Record<string, string>>({})
  const [selected, setSelected] = useState<Record<string, string[]>>({})
  const [done, setDone] = useState(false)
  const [error, setError] = useState(false)
  const lock = useRef(false)
  const isLocked = (qid: string) => Object.prototype.hasOwnProperty.call(locked, qid)
  const answer = (q: Question): string | null | undefined => {
    if (isLocked(q.qid)) return typeof locked[q.qid] === 'string' ? locked[q.qid] as string : locked[q.qid] === null ? null : undefined
    if (q.multi) {
      const items = [...(selected[q.qid] ?? []), ...(values[q.qid]?.trim() ? [values[q.qid].trim()] : [])]
      return items.length ? JSON.stringify(items) : undefined
    }
    return values[q.qid]?.trim() || undefined
  }
  const valid = questions.length > 0 && questions.every(q => answer(q) !== undefined)
  function submit() {
    if (!valid || lock.current || !connected) return
    lock.current = true; setError(false)
    try {request.respond({answers: Object.fromEntries(questions.map(q => [q.qid, answer(q)]))}); setDone(true)} catch {lock.current = false; setError(true)}
  }
  return <section className="fr-card" aria-label="추가 질문"><div className="fr-eyebrow">포식이의 질문</div><h3>조금 더 알려주세요.</h3><form onSubmit={event => {event.preventDefault(); submit()}}>
    {questions.map(q => <fieldset key={q.qid} disabled={!connected || done || isLocked(q.qid)}><legend>{q.question}</legend>
      {isLocked(q.qid) ? <p className="fr-locked">{locked[q.qid] === null ? '건너뛴 질문' : text(locked[q.qid])}<span> · 전달된 답변</span></p> : <>
        {q.multi && q.choices.length > 0 && <p className="fr-help">여러 개를 선택할 수 있어요.</p>}
        <div className="fr-options">{q.choices.map((choice, i) => <label key={`${choice}-${i}`}><input type={q.multi ? 'checkbox' : 'radio'} name={`${request.id}-${q.qid}`} checked={q.multi ? (selected[q.qid] ?? []).includes(choice) : values[q.qid] === choice} onChange={() => {
          if (q.multi) setSelected(current => ({...current, [q.qid]: (current[q.qid] ?? []).includes(choice) ? (current[q.qid] ?? []).filter(item => item !== choice) : [...(current[q.qid] ?? []), choice]}))
          else setValues(current => ({...current, [q.qid]: choice}))
        }} />{choice}</label>)}</div>
        <label className="fr-freeform">{q.choices.length ? '직접 입력' : '답변'}<textarea rows={2} value={q.multi ? values[q.qid] ?? '' : q.choices.includes(values[q.qid]) ? '' : values[q.qid] ?? ''} onChange={event => setValues(current => ({...current, [q.qid]: event.target.value}))} /></label>
      </>}
    </fieldset>)}<button type="submit" disabled={!connected || done || !valid}>{done ? '답변 전달됨' : '답변 보내기'}</button>
    {error && <p role="alert">전송 결과를 확인해 주세요. 작성한 내용은 유지했습니다.</p>}
  </form></section>
}

const valueMethods = new Set(['sudo', 'display.install.sudo', 'secret', 'vault.unlock_prompt', 'vault.code'])
function ValueCard({request, connected}: {request: ServerRequest; connected: boolean}) {
  const [value, setValue] = useState('')
  const [done, setDone] = useState(false)
  const [error, setError] = useState(false)
  const lock = useRef(false)
  const p = request.params
  const title = request.method === 'secret' ? '보안 값 입력' : request.method === 'vault.code' ? '인증 코드 입력' : request.method.startsWith('vault.') ? '비밀번호 관리자 잠금 해제' : '관리자 비밀번호 입력'
  function respond(result: string) {
    if (lock.current || !connected) return
    lock.current = true
    try {request.respond({value: result}); setValue(''); setDone(true)} catch {lock.current = false; setError(true)}
  }
  return <section className="fr-card" aria-label={title}><div className="fr-eyebrow">보안 입력</div><h3>{title}</h3>{text(p.prompt) && <p>{text(p.prompt)}</p>}{text(p.command) && <pre>{text(p.command)}</pre>}{text(p.display_name) && <p>{text(p.display_name)}</p>}{text(p.site) && <p>{text(p.site)}</p>}{text(p.hint) && <p>{text(p.hint)}</p>}
    <form onSubmit={event => {event.preventDefault(); if (value) respond(value)}}><label className="fr-freeform">{request.method === 'secret' ? text(p.env_var) || '보안 값' : request.method === 'vault.code' ? '인증 코드' : '비밀번호'}<input type="password" autoComplete="off" value={value} disabled={!connected || done} onChange={event => setValue(event.target.value)} /></label><div className="fr-actions"><button type="submit" disabled={!connected || done || !value}>전달하기</button><button type="button" className="fr-secondary" disabled={!connected || done} onClick={() => respond('')}>건너뛰기</button></div></form>
    {done && <p role="status">응답을 전달했습니다.</p>}{error && <p role="alert">전송 결과를 확인해 주세요.</p>}
  </section>
}

export function ForsicRequests({requests, pendingApproval, onApproval, connected}: {requests: ServerRequest[]; pendingApproval?: Data | null; onApproval: (choice: string) => Promise<void>; connected: boolean}) {
  return <div className="forsic-requests">
    {requests.map(request => request.method === 'approval' ? <ApprovalCard key={request.id} params={request.params} connected={connected} send={onApproval} /> : request.method === 'clarify' ? <ClarifyCard key={request.id} request={request} connected={connected} /> : valueMethods.has(request.method) ? <ValueCard key={request.id} request={request} connected={connected} /> : <section key={request.id} className="fr-card"><h3>터미널에서 확인이 필요해요.</h3><p>이 요청은 터미널에서 응답해 주세요.</p><code>{request.method}</code></section>)}
    {pendingApproval && !requests.some(request => request.method === 'approval') && <ApprovalCard key={text(pendingApproval.request_id) || 'pending-approval'} params={pendingApproval} connected={connected} send={onApproval} />}
  </div>
}
