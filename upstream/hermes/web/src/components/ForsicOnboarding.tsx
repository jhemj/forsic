import {useEffect, useRef, useState} from 'react'
import {Link, useNavigate} from 'react-router'
import {AnimatePresence, motion, useReducedMotion} from 'motion/react'
import {ArrowLeft, ArrowRight, Check, ChevronRight, File, FileText, Folder, FolderOpen, House, Layers3, Monitor, Search, ShieldCheck, Sparkles, Terminal, X} from 'lucide-react'
import {fetchJSON} from '@/lib/api'
import {useTenant} from '@/contexts/tenant-context'
import {tenantPath} from '@/lib/forsic-tenant'
import {requestUUID} from '@/lib/uuid'
import {useForsicConversation} from '@/hooks/useForsicConversation'
import './forsic-onboarding.css'

const purposes = [
  {id: 'comprehensive', label: '전체적으로 살펴보기', detail: '침해 흔적부터 경과와 영향까지 종합적으로 조사해요.', icon: ShieldCheck},
  {id: 'focused', label: '의심되는 단서 확인하기', detail: '특정 파일, 로그, 계정이나 행위를 집중적으로 확인해요.', icon: Search},
  {id: 'report', label: '보고서 작성하기', detail: '근거 모음과 기존 보고서를 검토해 템플릿에 맞춰 작성합니다.', icon: FileText},
  {id: 'custom', label: '직접 질문하기', detail: '알고 싶은 내용과 조사 범위를 자유롭게 알려주세요.', icon: Sparkles},
] as const
const environments = [
  {id: 'windows', label: 'Windows', detail: 'PC · Windows 서버', icon: Monitor},
  {id: 'linux', label: 'Linux', detail: '서버 · 시스템 로그', icon: Terminal},
  {id: 'macos', label: 'macOS', detail: 'Mac · 시스템 기록', icon: Monitor},
  {id: 'mixed', label: '여러 환경', detail: '여러 기기의 자료를 함께', icon: Layers3},
  {id: 'unknown', label: '아직 모르겠어요', detail: '자료를 확인하며 알아봐요', icon: Search},
] as const
const steps = ['자료 연결', '조사 목적', '대상 환경', '시작 준비']
interface Selection {purpose: string; environment: string; question: string}
interface CreatedConversation {resume_url:string;profile_name:string;tenant_id:string;case_id: string; session_id: string; onboarding: Selection & {prompt: string}}
interface EvidenceSource {path: string; name: string; display_path?: string}
interface EvidenceSources {sources: EvidenceSource[]; restricted?: boolean; default_path?: string; display_path?: string; available?: boolean}
interface EvidenceConnection {path: string; display_path?: string; name: string; kind: 'directory' | 'file'; access: 'readable'; scope: 'entry_only'}
interface BrowserPage {path: string; display_path?: string; parent: string | null; entries: {name: string; path: string; kind: 'directory' | 'file'}[]; has_more: boolean; next_offset: number | null}
const conversationURL = (item: CreatedConversation) => item.resume_url

/** Only mounted for an in-memory click intent, never for a URL or saved draft.
 * The shared native hook owns execution; a lost acknowledgement is never retried here. */
function StartHandoff({created}: {created: CreatedConversation}) {
  const navigate = useNavigate()
  const chat = useForsicConversation({sessionId: created.session_id, profile: created.profile_name, active: true})
  const attempted = useRef(false)
  const mounted = useRef(true)
  const [finished, setFinished] = useState(false)
  useEffect(() => {mounted.current = true; return () => {mounted.current = false}}, [])
  useEffect(() => {
    if (attempted.current || chat.loading || !chat.storedId || chat.inputDisabled || chat.error) return
    attempted.current = true
    void chat.send(created.onboarding.prompt).then(accepted => {
      if (!mounted.current) return
      if (accepted) void navigate(conversationURL(created), {replace: true})
      else setFinished(true)
    }).catch(() => {if (mounted.current) setFinished(true)})
  }, [chat, created, navigate])
  return <div className="fo-handoff" role="status">
    <div className={`fo-start-symbol${!finished && !chat.error ? ' is-active' : ''}`}><ShieldCheck size={34} /></div>
    <h2>{finished || chat.error ? '대화에서 연결 상태를 확인해 주세요' : '포식이와 조사를 연결하고 있어요'}</h2>
    <p>{chat.error || (finished ? '같은 요청을 다시 보내지 않았어요. 준비한 대화에서 이어갈 수 있습니다.' : '선택한 자료와 질문을 전달하고 대화 화면으로 이동합니다.')}</p>
    {(finished || chat.error) && <Link className="fo-primary" to={conversationURL(created)}>준비한 대화 열기<ArrowRight size={16} /></Link>}
  </div>
}

export function EvidencePicker({path, connection, checking, onChange, onCancelCheck, directoryOnly = false}: {path: string; connection: EvidenceConnection | null; checking: boolean; onChange: (path: string) => void; onCancelCheck: () => void; directoryOnly?: boolean}) {
  const [sourcePolicy, setSourcePolicy] = useState<EvidenceSources | null>(null)
  const [sources, setSources] = useState<EvidenceSource[]>([]), [sourceError, setSourceError] = useState(false), [sourceAttempt, setSourceAttempt] = useState(0)
  const [page, setPage] = useState<BrowserPage | null>(null)
  const [open, setOpen] = useState(false), [loading, setLoading] = useState(false)
  const [error, setError] = useState(''), [offset, setOffset] = useState(0)
  const serial = useRef(0), dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => () => {serial.current++}, [])
  useEffect(() => {
    let active = true
    setSourceError(false)
    void fetchJSON<EvidenceSources>('/api/plugins/forsic/evidence-sources').then(result => {
      if (active) {setSources(result.sources);setSourcePolicy(result)}
    }).catch(() => {if (active) setSourceError(true)})
    return () => {active = false}
  }, [sourceAttempt])
  useEffect(() => {
    if (open) dialog.current?.showModal()
    else dialog.current?.close()
  }, [open])
  function close() {serial.current++; setOpen(false); setLoading(false); setError(''); setPage(null)}
  function select(target: string) {onChange(target); close()}
  async function browse(target: string, nextOffset = 0) {
    onCancelCheck()
    const request = ++serial.current
    setOpen(true); setLoading(true); setError('')
    try {
      const result = await fetchJSON<BrowserPage>(`/api/plugins/forsic/evidence-browser?${new URLSearchParams({path: target.trim(), offset: String(nextOffset)})}`)
      if (request !== serial.current) return
      setPage(result); setOffset(nextOffset)
    } catch (cause) {if (request === serial.current) setError(cause instanceof Error ? cause.message : '폴더를 열지 못했습니다. 경로와 접근 권한을 확인하세요.')}
    finally {if (request === serial.current) setLoading(false)}
  }
  const parts = page?.path.split('/').filter(Boolean) ?? []
  function displayPath(target: string) {
    const source = sources.find(item => target === item.path || target.startsWith(item.path + '/'))
    return source?.display_path ? source.display_path + target.slice(source.path.length) : target
  }
  const browserDisplayPath = page?.display_path || (page ? displayPath(page.path) : '')
  return <div className="fo-evidence">
    <section className="fo-source-card" aria-label="선택한 자료">
      <div className="fo-source-icon"><FolderOpen size={28} /></div>
      <div className="fo-source-copy"><strong>{connection?.name || path.trim().split('/').filter(Boolean).at(-1) || '조사할 자료를 선택하세요'}</strong><p>{connection?.display_path || (path.trim() ? displayPath(path.trim()) : sourcePolicy?.display_path || '포식이가 실행 중인 컴퓨터의 자료')}</p></div>
      <button type="button" className="fo-primary" disabled={loading || sourcePolicy?.available === false} onClick={() => void browse(path.trim() || sources[0]?.path || '')}>폴더 찾아보기<ChevronRight size={16} /></button>
    </section>
    {sources.length > 0 && <div className="fo-configured-sources"><p>설치 때 지정한 폴더</p>{sources.map(source => <div key={source.path}><Folder size={17} /><span><strong>{source.name}</strong><small>{source.display_path || source.path}</small></span><button type="button" disabled={loading} onClick={() => void browse(source.path)}>연결된 자료에서 선택</button></div>)}</div>}
    {sourceError && <div className="fo-source-load-error" role="status"><span>연결된 자료 위치를 불러오지 못했어요. 폴더를 직접 선택할 수 있습니다.</span><button type="button" onClick={() => setSourceAttempt(sourceAttempt + 1)}>자료 위치 다시 불러오기</button></div>}
    {!sourcePolicy?.restricted && <details className="fo-manual-path"><summary>경로 직접 입력</summary><label htmlFor="fo-evidence-path">{directoryOnly ? '폴더 경로' : '폴더 또는 파일 경로'}</label>
      <div className="fo-path-field"><FolderOpen size={19} /><input id="fo-evidence-path" autoComplete="off" spellCheck={false} value={path} placeholder="/자료가/있는/폴더" onChange={event => onChange(event.target.value)} onKeyDown={event => {if (event.key === 'Enter') {event.preventDefault(); if (path.trim()) void browse(path)}}} /></div>
    </details>}
    {checking && <div className="fo-connection-state" role="status"><svg className="fo-access-activity" width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="4" y="5" width="16" height="14" rx="3" /><path d="M8 10h8M8 14h5" /><path className="fo-access-scan" d="M2 12h20" /></svg><span>선택한 자료의 접근 권한을 확인하고 있어요.</span><button type="button" onClick={onCancelCheck}>확인 취소</button></div>}
    {connection && <p className="fo-connection-state" role="status"><Check size={16} />선택한 {connection.kind === 'directory' ? '폴더' : '파일'} 접근 확인됨</p>}
    <details className="fo-source-help"><summary>자료 폴더가 보이지 않나요?</summary><p>자료는 포식이가 실행 중인 컴퓨터에 있어야 합니다. 다른 컴퓨터에서 접속한 경우에도 포식이 서버에 준비된 자료를 선택합니다.</p><p>웹 화면까지 컨테이너에서 실행하는 설치 환경에서는 호스트의 자료 폴더를 먼저 연결해야 합니다. 설치 관리자에게 연결을 요청하세요.</p></details>
    <dialog ref={dialog} className="fo-browser" aria-label="자료 폴더 탐색" onCancel={event => {event.preventDefault(); close()}}>
      {open && <>
        <header className="fo-browser-heading"><div><FolderOpen size={21} /><h2>폴더 찾아보기</h2></div><button type="button" aria-label="폴더 목록 닫기" onClick={close}><X size={19} /></button></header>
        <div className="fo-browser-bar">
          <button type="button" aria-label="홈 폴더" title="홈 폴더" disabled={loading} onClick={() => void browse('')}><House size={17} /></button>
          <button type="button" aria-label="상위 폴더 열기" title="상위 폴더" disabled={!page?.parent || loading} onClick={() => void browse(page!.parent!)}><ArrowLeft size={17} /></button>
          <nav aria-label="현재 폴더 경로">{sourcePolicy?.restricted ? <span>{browserDisplayPath || sourcePolicy.display_path}</span> : <><button type="button" disabled={loading} onClick={() => void browse('/')}>/</button>{parts.map((part, index) => <span key={index}><ChevronRight size={13} /><button type="button" disabled={loading} onClick={() => void browse('/' + parts.slice(0, index + 1).join('/'))}>{part}</button></span>)}</>}</nav>
        </div>
        {error && <p className="fo-error" role="alert">{error}</p>}
        <div className="fo-browser-list" aria-busy={loading}>
          {loading && <p role="status">폴더 목록 불러오는 중…</p>}
          {!loading && page?.entries.filter(entry => !directoryOnly || entry.kind === 'directory').map(entry => <button key={entry.path} type="button" onClick={() => entry.kind === 'directory' ? void browse(entry.path) : select(entry.path)}>
            {entry.kind === 'directory' ? <Folder size={20} /> : <File size={20} />}<span>{entry.name}</span>{entry.kind === 'directory' ? <ChevronRight size={15} /> : <small>선택</small>}
          </button>)}
          {!loading && page?.entries.length === 0 && <p>선택할 수 있는 항목이 없습니다.</p>}
        </div>
        {page && <div className="fo-browser-pages"><span>목록 {offset + (page.entries.length ? 1 : 0)}–{offset + page.entries.length}</span><div>{offset > 0 && <button type="button" disabled={loading} onClick={() => void browse(page.path, Math.max(0, offset - 100))}><ArrowLeft size={14} />이전 항목</button>}{page.has_more && <button type="button" disabled={loading} onClick={() => void browse(page.path, page.next_offset ?? 0)}>다음 항목<ArrowRight size={14} /></button>}</div></div>}
        <footer className="fo-browser-footer"><span title={browserDisplayPath}>{browserDisplayPath || '폴더를 선택하세요'}</span><button type="button" onClick={close}>취소</button><button type="button" className="fo-primary" disabled={!page || loading} onClick={() => select(page!.path)}>이 폴더 선택</button></footer>
      </>}
    </dialog>
    <div className="fo-preservation"><ShieldCheck size={20} /><div><strong>조사를 시작할 때 읽기 전용으로 연결합니다</strong><p>지금은 선택한 폴더나 파일의 접근만 확인합니다. 하위 파일과 내용은 조사 중에 확인합니다.</p></div></div>
  </div>
}

export default function ForsicOnboarding({onConflict}: {onConflict?: (message: string) => void} = {}) {
  const tenant=useTenant()
  const [step, setStep] = useState(0), [direction, setDirection] = useState(1)
  const [selection, setSelection] = useState<Selection>({purpose: '', environment: '', question: ''})
  const [path, setPath] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [connection, setConnection] = useState<EvidenceConnection | null>(null), [checking, setChecking] = useState(false)
  const connectionSerial = useRef(0), checkingRef = useRef(false)
  const [created, setCreated] = useState<CreatedConversation | null>(null)
  const request = useRef<{key: string; id: string} | null>(null), submitting = useRef(false)
  const heading = useRef<HTMLHeadingElement>(null)
  const reduceMotion = useReducedMotion()
  const selectedPurpose = purposes.find(item => item.id === selection.purpose)
  const selectedEnvironment = environments.find(item => item.id === selection.environment)
  const canContinue = step === 0 ? !!path.trim() : step === 1 ? Boolean(selection.purpose) && (selection.purpose !== 'custom' || !!selection.question.trim()) : step === 2 ? Boolean(selection.environment) : !!connection
  const titles = ['자료 연결', '조사 목적', '대상 환경', '선택 내용 확인']
  const descriptions = ['포식이가 실행 중인 컴퓨터의 폴더나 파일을 선택하세요.', '', '환경을 모르면 ‘아직 모르겠어요’를 선택하세요.', '']
  function move(next: number) {setDirection(next > step ? 1 : -1); setStep(next); setError('')}
  useEffect(() => {heading.current?.focus({preventScroll: true})}, [step])
  useEffect(() => () => {connectionSerial.current++}, [])
  function cancelCheck() {connectionSerial.current++; checkingRef.current = false; setChecking(false)}
  function changePath(next: string) {cancelCheck(); setPath(next); setConnection(null); setError('')}
  async function connect() {
    const target = path.trim()
    if (!target || checkingRef.current) return
    const serial = ++connectionSerial.current
    checkingRef.current = true; setChecking(true); setConnection(null); setError('')
    try {
      const result = await fetchJSON<EvidenceConnection>(`/api/plugins/forsic/evidence-connection?${new URLSearchParams({path: target})}`)
      if (serial !== connectionSerial.current) return
      if (!result.path || !result.name || !['directory', 'file'].includes(result.kind) || result.access !== 'readable' || result.scope !== 'entry_only') throw new Error('자료 접근 확인 결과를 확인하지 못했어요. 다시 확인해 주세요.')
      setPath(result.path); setConnection(result); move(1)
    } catch (cause) {if (serial === connectionSerial.current) setError(cause instanceof Error ? cause.message : '자료에 접근할 수 없어요. 위치와 읽기 권한을 확인해 주세요.')}
    finally {if (serial === connectionSerial.current) {checkingRef.current = false; setChecking(false)}}
  }
  async function start() {
    if (submitting.current) return
    submitting.current = true; setBusy(true); setError('')
    const payload = {path: path.trim(), onboarding: {...selection, question: selection.question.trim()}}
    const key = JSON.stringify(payload)
    if (request.current?.key !== key) request.current = {key, id: requestUUID()}
    try {
      const result = await fetchJSON<CreatedConversation>('/api/plugins/forsic/investigations', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...payload, request_id: request.current.id})})
      if (!result.case_id || !result.session_id || !result.onboarding?.prompt || !result.resume_url || !result.profile_name || !result.tenant_id) throw new Error('대화 준비 결과를 확인하지 못했어요. 다시 확인해 주세요.')
      setCreated(result)
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : '자료를 연결하지 못했어요. 경로와 접근 권한을 확인해 주세요.'
      setError(message)
      if (cause && typeof cause === 'object' && 'status' in cause && cause.status === 409) onConflict?.(message)
    }
    finally {submitting.current = false; setBusy(false)}
  }
  return <main className="forsic-onboarding">
    <div className="fo-topline"><span>새로운 조사</span><Link to={tenant?tenantPath('/forsic?view=start',tenant):'/forsic?view=start'}>나중에 시작하기<X size={15} /></Link></div>
    <section className="fo-window" aria-label="새 조사 시작 안내">
      <aside className="fo-journey">
        <div className="fo-wordmark"><span className="fo-brand-symbol"><Search size={21} /></span><span>FORSIC</span></div>
        <ol aria-label="시작 단계">{steps.map((label, index) => <li key={label} aria-current={!created && step === index ? 'step' : undefined} data-complete={index < step || !!created}><span>{index < step || created ? <Check size={15} /> : index + 1}</span><div>{label}</div></li>)}</ol>
      </aside>
      <div className="fo-stage">
        {created ? <StartHandoff created={created} /> : <>
          <div className="fo-step-meta"><span>시작하기</span><span>0{step + 1}<i> / 04</i></span></div>
          <div className="fo-progress" aria-hidden="true">{steps.map((label, index) => <span key={label} data-active={index <= step} />)}</div>
          <AnimatePresence mode="popLayout" initial={false} custom={direction}>
            <motion.div key={step} className="fo-step-body" initial={{opacity: 0, x: reduceMotion ? 0 : direction * 18}} animate={{opacity: 1, x: 0}} exit={{opacity: 0, x: reduceMotion ? 0 : -direction * 12}} transition={{duration: reduceMotion ? 0 : .22, ease: [.22, 1, .36, 1]}}>
              <header><h1 ref={heading} tabIndex={-1}>{titles[step]}</h1>{descriptions[step] && <p>{descriptions[step]}</p>}</header>
              {step === 1 && <>
                <fieldset className="fo-choices"><legend className="fo-sr-only">조사 목적</legend>{purposes.map(item => <label key={item.id} className="fo-choice" data-selected={selection.purpose === item.id}>
                  <input type="radio" name="purpose" value={item.id} checked={selection.purpose === item.id} onChange={() => setSelection({...selection, purpose: item.id})} /><span className="fo-choice-icon"><item.icon size={21} /></span><span><strong>{item.label}</strong><small>{item.detail}</small></span><span className="fo-choice-check" aria-hidden="true">{selection.purpose === item.id && <Check size={13} />}</span>
                </label>)}</fieldset>
                {selection.purpose && <div className="fo-note"><label htmlFor="fo-question">{selection.purpose === 'report' ? '보고서 요구사항' : selection.purpose === 'custom' ? '조사 질문' : '추가 질문'}<span>{selection.purpose !== 'custom' && '선택 사항'}</span></label><textarea id="fo-question" value={selection.question} maxLength={4000} rows={2} placeholder={selection.purpose === 'report' ? '보고서 독자, 사용할 템플릿, 포함할 범위를 입력하세요.' : '확인할 대상이나 조사 범위를 입력하세요.'} onChange={event => setSelection({...selection, question: event.target.value})} /></div>}
              </>}
              {step === 2 && <fieldset className="fo-choices fo-environments"><legend className="fo-sr-only">대상 환경</legend>{environments.map(item => <label key={item.id} className="fo-choice" data-selected={selection.environment === item.id}>
                <input type="radio" name="environment" value={item.id} checked={selection.environment === item.id} onChange={() => setSelection({...selection, environment: item.id})} /><span className="fo-choice-icon"><item.icon size={23} /></span><span><strong>{item.label}</strong><small>{item.detail}</small></span><span className="fo-choice-check" aria-hidden="true">{selection.environment === item.id && <Check size={13} />}</span>
              </label>)}</fieldset>}
              {step === 0 && <EvidencePicker path={path} connection={connection} checking={checking} onChange={changePath} onCancelCheck={cancelCheck} />}
              {step === 3 && <div className="fo-review">
                <dl><div><dt>조사 목적</dt><dd>{selectedPurpose?.label}</dd><button type="button" disabled={busy} aria-label="조사 목적 수정" onClick={() => move(1)}>수정</button></div><div><dt>대상 환경</dt><dd>{selectedEnvironment?.label}</dd><button type="button" disabled={busy} aria-label="대상 환경 수정" onClick={() => move(2)}>수정</button></div><div><dt>연결 자료</dt><dd className="fo-path-review"><strong>{connection?.name}</strong><br />{connection?.display_path || path.trim()}<small className="fo-review-access">선택한 {connection?.kind === 'directory' ? '폴더' : '파일'} 접근 확인됨 · 하위 자료는 조사 중 확인</small></dd><button type="button" disabled={busy} aria-label="자료 위치 수정" onClick={() => move(0)}>수정</button></div>{selection.question.trim() && <div><dt>{selection.purpose === 'report' ? '보고서 요구사항' : '추가 질문'}</dt><dd>{selection.question.trim()}</dd><button type="button" disabled={busy} aria-label="질문 수정" onClick={() => move(1)}>수정</button></div>}</dl>
                <p className="fo-help">시작하면 선택한 자료를 읽기 전용으로 연결하고 현재 설정된 조사 모델이 살펴봅니다. 진행 중에도 대화로 요청하거나 일시정지할 수 있어요.</p>
              </div>}
            </motion.div>
          </AnimatePresence>
          {error && <p className="fo-error" role="alert">{error}</p>}
          <footer className="fo-actions"><button type="button" className="fo-back" disabled={step === 0 || busy} onClick={() => move(step - 1)}><ArrowLeft size={16} />이전</button><button type="button" className="fo-primary" disabled={!canContinue || busy || checking} onClick={() => step === 0 ? void connect() : step === 3 ? void start() : move(step + 1)}>{checking ? '접근 확인 중' : busy ? '대화를 준비하고 있어요' : step === 3 ? (selection.purpose === 'report' ? '보고서 작성하기' : '조사 시작하기') : step === 0 ? '연결 확인하고 다음' : '다음'}<ArrowRight size={17} /></button></footer>
        </>}
      </div>
    </section>
  </main>
}
