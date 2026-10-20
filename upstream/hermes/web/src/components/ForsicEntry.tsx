import {lazy, Suspense, useEffect, useRef, useState} from 'react'
import {Link} from 'react-router'
import {ArrowRight, FolderOpen, Plus, Search} from 'lucide-react'
import {fetchJSON} from '@/lib/api'
import {requestUUID} from '@/lib/uuid'
import {useTenant} from '@/contexts/tenant-context'
import {tenantPath} from '@/lib/forsic-tenant'
import './forsic-entry.css'

const ForsicOnboarding = lazy(() => import('./ForsicOnboarding'))
interface Availability {
  allowed: boolean
  reason: string | null
  active_case: {case_id: string; label: string; resume_session_id: string | null; resume_url?: string; status: string; goal_id?: string | null; closable?: boolean} | null
}
interface EntryState {status: 'loading' | 'ready' | 'error'; availability: Availability | null}

/** Read-only entry: opening this screen never creates a case or connects an agent. */
export default function ForsicEntry({mode = 'start'}: {mode?: 'start' | 'new'}) {
  const tenant=useTenant()
  const link=(path:string)=>tenant?tenantPath(path,tenant):path
  const [state, setState] = useState<EntryState>({status: 'loading', availability: null})
  const [attempt, setAttempt] = useState(0)
  const [conflict, setConflict] = useState('')
  const [closing, setClosing] = useState(false)
  const closeRequest = useRef<{key: string; id: string} | null>(null)
  const pendingClose = useRef(false)
  useEffect(() => {
    let active = true
    void fetchJSON<{new_investigation: Availability}>('/api/plugins/forsic/investigations').then(result => {
      if (!result.new_investigation || typeof result.new_investigation.allowed !== 'boolean') throw new Error('Missing availability')
      if (active) setState({status: 'ready', availability: result.new_investigation})
    }).catch(() => {if (active) setState({status: 'error', availability: null})})
    return () => {active = false}
  }, [attempt])
  function refresh(message = '') {
    setConflict(message)
    setState({status: 'loading', availability: null})
    setAttempt(value => value + 1)
  }
  async function closeInvestigation() {
    const current = state.availability?.active_case
    if (pendingClose.current || !current?.closable || !current.goal_id || !current.resume_session_id) return
    if (!window.confirm(`‘${current.label || '진행 중인 조사'}’ 조사를 종료할까요?\n\n사용자 종료로 기록되며 조사 성공을 뜻하지 않습니다. 대화·증거·노트·보고서는 보존됩니다.`)) return
    const profile = tenant?.profile_name
    if(!profile)return
    const key = JSON.stringify([profile, current.case_id, current.resume_session_id, current.goal_id])
    pendingClose.current = true
    setClosing(true)
    setConflict('')
    try {
      if (closeRequest.current?.key !== key) closeRequest.current = {key, id: requestUUID()}
      const result = await fetchJSON<{status: string}>(`/api/plugins/forsic/investigations/${encodeURIComponent(current.case_id)}/close`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({profile, request_id: closeRequest.current.id, session_id: current.resume_session_id, goal_id: current.goal_id, confirmed: true}),
      })
      if (result.status !== 'closed') throw new Error('Close not acknowledged')
      refresh('조사를 사용자 종료로 기록했어요. 기존 기록은 보존됩니다.')
    } catch {
      setConflict('종료를 확인하지 못했어요. 실행 중인 작업이 있다면 멈춘 뒤 다시 시도하거나 조사 상태를 다시 확인해 주세요.')
    } finally {pendingClose.current = false; setClosing(false)}
  }
  const available = state.status === 'ready' && state.availability?.allowed === true
  if (mode === 'new' && available) return <Suspense fallback={<p className="forsic-shell-loading" role="status">새 조사 화면을 준비하고 있어요.</p>}><ForsicOnboarding onConflict={refresh} /></Suspense>
  const current = state.availability?.active_case
  const currentURL = current?.resume_session_id
    ? current.resume_url || link(`/chat?${new URLSearchParams({case: current.case_id, resume: current.resume_session_id})}`)
    : link('/forsic?view=history')
  return <main className="forsic-entry">
    <section className="fe-window" aria-labelledby="fe-title">
      <div className="fe-brand"><span><Search size={21} /></span>FORSIC</div>
      <header><p>조사 시작</p><h1 id="fe-title">어떤 조사를 진행할까요?</h1></header>
      <div className="fe-options">
        <section className="fe-option"><span className="fe-icon"><Plus size={24} /></span><h2>새 조사</h2><p>자료를 연결하고 조사 목적을 설정합니다.</p>
          {available ? <Link className="fe-primary" to={tenant?.new_url||link('/forsic?view=new')}>새 조사<ArrowRight size={16} /></Link> : <button className="fe-primary" type="button" disabled aria-describedby="fe-availability">새 조사<ArrowRight size={16} /></button>}
        </section>
        <section className="fe-option"><span className="fe-icon"><FolderOpen size={24} /></span><h2>기존 조사</h2><p>사건 목록에서 진행하던 대화를 엽니다.</p><Link className="fe-secondary" to={link('/forsic?view=history')}>기존 조사<ArrowRight size={16} /></Link></section>
      </div>
      <div id="fe-availability" className="fe-availability" aria-live="polite"><div key={state.status} className="fe-state">
        {conflict && <p role="alert">{conflict}</p>}
        {state.status === 'loading' && <p role="status">새 조사를 시작할 수 있는지 확인하고 있어요.</p>}
        {state.status === 'error' && <><p role="alert">조사 상태를 확인하지 못해 새 조사를 시작할 수 없습니다.</p><button type="button" onClick={() => refresh()}>조사 상태 다시 확인</button></>}
        {state.status === 'ready' && !available && <><p>{state.availability?.reason || '진행 중인 조사가 있어 새 조사를 시작할 수 없습니다.'}</p>{current && <Link to={currentURL}>진행 중인 조사 열기{current.label ? ` · ${current.label}` : ''}<ArrowRight size={15} /></Link>}{current?.closable && current.goal_id && current.resume_session_id && <button type="button" onClick={() => void closeInvestigation()} disabled={closing}>{closing ? '종료 중…' : '종료'}</button>}<button type="button" disabled={closing} onClick={() => refresh()}>상태 다시 확인</button></>}
      </div></div>
    </section>
  </main>
}
