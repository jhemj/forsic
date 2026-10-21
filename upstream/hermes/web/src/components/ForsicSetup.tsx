import {useEffect, useRef, useState, type ReactNode} from 'react'
import {Link, useLocation, useNavigate} from 'react-router'
import {ArrowLeft, ArrowRight, Check, Search, ShieldCheck} from 'lucide-react'
import {fetchJSON} from '@/lib/api'
import {EvidencePicker} from './ForsicOnboarding'
import './forsic-onboarding.css'
import './forsic-setup.css'

type HostingMode = 'local' | 'private_lan' | 'online'
interface SetupValues {
  llm_base_url: string; llm_model: string; llm_context_length: number | null
  evidence_directory: string; evidence_directory_label: string
  hosting_mode: HostingMode; private_lan_host: string; public_url: string; login_username: string
}
export interface SetupStatus {
  local_admin: boolean; required: boolean; completed: boolean; pending: boolean; restart_required: boolean
  values?: SetupValues; llm_key_configured?: boolean; login_configured?: boolean
  hosting_current?: {mode: HostingMode}; llm_configured?: boolean; evidence_configured?: boolean
}
const steps = ['모델 연결', '자료 폴더', '접속 범위', '설정 확인']
const hosting = [
  {id:'local', label:'이 컴퓨터에서만', detail:'설치한 컴퓨터의 브라우저에서 사용합니다.'},
  {id:'private_lan', label:'내부 네트워크', detail:'같은 내부망의 기기에서 로그인 없이 접속합니다.'},
  {id:'online', label:'보안 온라인 접속', detail:'HTTPS 주소와 로그인을 갖춘 환경에서 접속합니다.'},
] as const

export function SetupReadOnly({status}: {status: SetupStatus}) {
  return <section className="fs-readonly" aria-labelledby="fs-readonly-title"><ShieldCheck size={28} aria-hidden="true" />
    <h1 id="fs-readonly-title">포식이 연결 상태</h1>
    {status.hosting_current && <p>접속 범위 · {hosting.find(item => item.id === status.hosting_current?.mode)?.label}</p>}
    {typeof status.llm_configured === 'boolean' && <p>조사 모델 · {status.llm_configured ? '설정됨' : '설정 필요'}</p>}
    {typeof status.evidence_configured === 'boolean' && <p>자료 폴더 · {status.evidence_configured ? '설정됨' : '설정 필요'}</p>}
    <p>연결과 공개 범위는 포식이가 설치된 컴퓨터에서 변경할 수 있습니다.</p>
    <Link to="/forsic?view=start">조사 화면으로</Link>
  </section>
}

export default function ForsicSetup({initial, onSaved, mode='initial'}: {initial: SetupStatus; onSaved: (status: SetupStatus) => void; mode?: 'initial'|'hosting'}) {
  const embedded = mode === 'hosting';
  const firstStep = embedded ? 1 : 0;
  const [value, setValue] = useState(initial.values)
  const [step, setStep] = useState(firstStep), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [apiKey, setApiKey] = useState<string | null>(null), [password, setPassword] = useState('')
  const [checkMessage, setCheckMessage] = useState('')
  const [models, setModels] = useState<string[]>([])
  const heading = useRef<HTMLHeadingElement>(null), lock = useRef(false)
  useEffect(() => {if(!embedded)heading.current?.focus({preventScroll:true})}, [step,embedded])
  if (!initial.local_admin) return <SetupReadOnly status={initial} />
  if (!value) return <p role="alert">설정 내용을 불러오지 못했습니다. 화면을 다시 열어 주세요.</p>
  const editingKey = apiKey !== null || !initial.llm_key_configured
  const keyFields = editingKey ? {llm_api_key:apiKey ?? ''} : {}
  function update<K extends keyof SetupValues>(key: K, next: SetupValues[K]) {
    setValue(previous => previous && ({...previous, [key]: next})); setError(''); setCheckMessage('')
    if (key === 'llm_base_url') setModels([])
  }
  function move(next: number) {setStep(next); setError(''); setCheckMessage('')}
  async function checkModel() {
    if (lock.current || !value) return
    lock.current = true; setBusy(true); setError(''); setCheckMessage('')
    try {
      const result = await fetchJSON<{reachable:boolean;model_found:boolean;models?:string[];detail:string}>('/api/plugins/forsic/setup/check-model', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({llm_base_url:value.llm_base_url,llm_model:value.llm_model,...keyFields})})
      setModels(result.models || [])
      setCheckMessage(result.reachable ? result.model_found ? '모델 목록에서 선택한 모델을 확인했어요. 실제 응답은 조사 시작 후 확인합니다.' : '서버에 연결됐어요. 아래 모델 목록에서 선택하거나 모델 이름을 직접 입력하세요.' : '모델 서버에 연결하지 못했어요. 주소와 키를 확인해 주세요.')
    } catch {setError('모델 목록을 확인하지 못했어요. 주소와 연결 상태를 확인해 주세요.')}
    finally {lock.current=false;setBusy(false)}
  }
  async function save() {
    if (lock.current) return
    lock.current=true; setBusy(true);setError('')
    try {
      // This editor changes folders/hosting only. Read the latest saved model so
      // a model change in the sibling category cannot be overwritten by this draft.
      let modelFields = {};
      if(embedded){
        const latest=await fetchJSON<SetupStatus>('/api/plugins/forsic/setup');
        if(!latest.local_admin||!latest.values)throw new Error('설정 권한과 현재 연결을 확인해 주세요.');
        modelFields={llm_base_url:latest.values.llm_base_url,llm_model:latest.values.llm_model,llm_context_length:latest.values.llm_context_length};
      }
      const saved = await fetchJSON<SetupStatus>('/api/plugins/forsic/setup',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({...value,...(embedded?modelFields:keyFields),login_password:password})})
      setApiKey(null);setPassword('');onSaved(saved)
      window.dispatchEvent(new Event('forsic:connections-changed'))
    } catch (cause) {setError(cause instanceof Error ? cause.message : '설정을 저장하지 못했어요. 입력 내용을 확인해 주세요.')}
    finally {lock.current=false;setBusy(false)}
  }
  const ready = step === 0 ? Boolean(value.llm_base_url.trim() && value.llm_model.trim()) : step === 1 ? Boolean(value.evidence_directory.trim())
    : step === 2 ? (value.hosting_mode === 'local' || Boolean(value.evidence_directory.trim() && (value.hosting_mode === 'private_lan' ? value.private_lan_host.trim() : value.public_url.trim() && value.login_username.trim() && (password ? password.length >= 12 : initial.login_configured)))) : true
  const Container=embedded?'div':'main';
  const Heading=embedded?'h3':'h1';
  return <Container className={`forsic-setup${embedded?' fs-embedded':''}`}><section className="fs-window" aria-labelledby="fs-title">
    {!embedded&&<header className="fs-header"><span className="fs-brand"><Search size={20} aria-hidden="true" /> FORSIC</span><span>{initial.required ? '처음 설정' : '연결 및 호스팅 설정'}</span></header>}
    <nav aria-label="설정 단계"><ol className="fs-steps">{steps.map((label,index) => index>=firstStep&&<li key={label} aria-current={index === step ? 'step' : undefined}><span>{index < step ? <Check size={14} /> : index + 1-firstStep}</span>{label}</li>)}</ol></nav>
    <div className="fs-body"><Heading id="fs-title" tabIndex={-1} ref={heading}>{steps[step]}</Heading>
      {step === 0 && <><p>조사에 사용할 모델 서버를 연결하세요.</p>
        <label className="fs-field">API 주소 · OpenAI 호환<input type="url" autoComplete="off" spellCheck={false} value={value.llm_base_url} onChange={event => update('llm_base_url',event.target.value)} placeholder="http://localhost:11434/v1" /></label>
        <label className="fs-field">모델 이름<input list="fs-model-options" value={value.llm_model} autoComplete="off" spellCheck={false} onChange={event => update('llm_model',event.target.value)} placeholder="서버에 설치된 모델 이름" /><datalist id="fs-model-options">{models.map(model => <option key={model} value={model} />)}</datalist></label>
        {editingKey ? <><label className="fs-field">API 키 · 선택<input type="password" autoComplete="new-password" disabled={busy} value={apiKey ?? ''} onChange={event => setApiKey(event.target.value)} placeholder="빈칸이면 API 키 없이 연결" /></label><p>빈칸으로 저장하면 API 키 없이 연결해요. {initial.llm_key_configured && <button type="button" disabled={busy} onClick={() => setApiKey(null)}>기존 키 유지</button>}</p></>
          : <div className="fs-field"><span>API 키 · 등록됨</span><div><button type="button" disabled={busy} onClick={() => setApiKey('')}>API 키 변경</button> <button type="button" disabled={busy} onClick={() => setApiKey('')}>키 없이 사용</button></div></div>}
        <details><summary>문맥 길이 직접 지정</summary><label className="fs-field">토큰 수 · 비우면 모델 기본값<input type="number" min={64000} max={2147483647} value={value.llm_context_length ?? ''} onChange={event => update('llm_context_length',event.target.value ? Number(event.target.value) : null)} /></label></details>
        <div className="fs-check-model"><button type="button" disabled={busy || !value.llm_base_url.trim()} onClick={() => void checkModel()}>모델 목록 확인 · 선택</button><small>누르면 입력한 서버에 모델 목록만 요청합니다.</small></div>{models.length > 0 && <label className="fs-field">확인된 모델<select value={models.includes(value.llm_model) ? value.llm_model : ''} onChange={event => update('llm_model',event.target.value)}><option value="" disabled>사용할 모델 선택</option>{models.map(model => <option key={model} value={model}>{model}</option>)}</select></label>}
      </>}
      {step === 1 && <><p>조사 자료를 보관할 폴더를 선택하세요. 원본은 읽기 전용으로 연결합니다.</p><EvidencePicker directoryOnly path={value.evidence_directory} connection={null} checking={false} onCancelCheck={() => {}} onChange={path => update('evidence_directory',path)} /><label className="fs-field">화면에 표시할 폴더 이름<input value={value.evidence_directory_label} onChange={event => update('evidence_directory_label',event.target.value)} placeholder="조사 자료" /></label></>}
      {step === 2 && <><p>어디에서 포식이에 접속할지 선택하세요.</p><fieldset className="fs-hosting"><legend className="sr-only">접속 범위</legend>{hosting.map(item => <label key={item.id} data-selected={value.hosting_mode === item.id}><input type="radio" name="hosting" value={item.id} checked={value.hosting_mode === item.id} onChange={() => update('hosting_mode',item.id)} /><span><strong>{item.label}</strong><small>{item.detail}</small></span></label>)}</fieldset>
        {value.hosting_mode === 'private_lan' && <p className="fs-notice">내부망에서는 로그인 없이 사용합니다. 같은 네트워크에서 이 주소에 접속할 수 있는 사람이 조사 자료와 대화에 접근할 수 있으니 신뢰하는 내부망에서 사용하세요.</p>}
        {value.hosting_mode !== 'local' && <>{value.hosting_mode === 'private_lan' ? <label className="fs-field">이 컴퓨터의 내부망 IP<input value={value.private_lan_host} onChange={event => update('private_lan_host',event.target.value)} placeholder="192.168.1.10" /></label> : <><label className="fs-field">HTTPS 접속 주소<input type="url" value={value.public_url} onChange={event => update('public_url',event.target.value)} placeholder="https://forsic.example.com" /></label><p className="fs-notice">인증서를 갖춘 HTTPS 프록시를 먼저 준비하세요. 저장 후 재시작하면 해당 주소를 사용하는 설정이 적용됩니다.</p></>}
          {value.hosting_mode === 'online' && <><label className="fs-field">로그인 이름<input autoComplete="username" value={value.login_username} onChange={event => update('login_username',event.target.value)} /></label><label className="fs-field">로그인 비밀번호<input type="password" autoComplete="new-password" minLength={12} value={password} onChange={event => setPassword(event.target.value)} placeholder={initial.login_configured ? '등록됨 · 바꿀 때만 입력' : '접속할 때 사용할 비밀번호'} /><small>새 비밀번호는 12자 이상 입력하세요.</small></label></>}</>}
      </>}
      {step === 3 && <><p>저장할 내용을 확인하세요.</p><dl className="fs-review">{!embedded&&<div><dt>조사 모델</dt><dd>{value.llm_model}<small>{value.llm_base_url}</small></dd></div>}<div><dt>자료 폴더</dt><dd>{value.evidence_directory_label || '조사 자료'}<small>{value.evidence_directory}</small></dd></div><div><dt>접속 범위</dt><dd>{hosting.find(item => item.id === value.hosting_mode)?.label}<small>{value.hosting_mode === 'online' ? value.public_url : value.hosting_mode === 'private_lan' ? value.private_lan_host : '이 컴퓨터에서만 접속'}</small></dd></div></dl><p className="fs-notice">저장 후 실행 도구로 포식이를 다시 시작하면 적용됩니다. 모델 요청이나 조사 시작은 하지 않습니다.</p></>}
      {checkMessage && <p role="status" className="fs-notice">{checkMessage}</p>}{error && <p role="alert" className="fs-error">{error}</p>}
    </div>
    <footer className="fs-footer">{step > firstStep ? <button type="button" disabled={busy} onClick={() => move(step-1)}><ArrowLeft size={16} />이전</button> : !embedded&&!initial.required ? <Link to="/forsic?view=start">조사 화면으로</Link> : <span />}
      <button type="button" className="fs-primary" disabled={busy || !ready} onClick={() => step === 3 ? void save() : move(step+1)}>{busy ? '처리 중…' : step === 3 ? '설정 저장' : '다음'}{!busy && <ArrowRight size={16} />}</button></footer>
  </section></Container>
}

/** Read-only bootstrap check. Existing configured installations keep their normal entry. */
export function ForsicSetupGate({children}: {children: ReactNode}) {
  const location = useLocation(), navigate = useNavigate()
  const explicit = location.pathname === '/forsic' && new URLSearchParams(location.search).get('view') === 'setup'
  const [status, setStatus] = useState<SetupStatus | null>(null), [failed,setFailed] = useState(false), [attempt,setAttempt] = useState(0)
  const [saved,setSaved] = useState<SetupStatus | null>(null)
  useEffect(() => {
    let active=true
    void fetchJSON<SetupStatus>('/api/plugins/forsic/setup').then(result => {if(active) {setStatus(result);setSaved(null);setFailed(false)}}).catch(() => {if(active)setFailed(true)})
    return () => {active=false}
  }, [attempt, explicit])
  const pendingBootstrap = status && !status.completed && status.pending && !explicit
  const notice = saved || (pendingBootstrap ? status : null)
  if (notice) return <main className="forsic-setup"><section className="fs-readonly"><Check size={30} /><h1>설정을 저장했어요</h1><p>{notice.restart_required ? '실행 도구로 포식이를 다시 시작하면 새 연결과 접속 범위가 적용됩니다.' : '설정이 준비됐어요.'}</p>
    {!notice.completed && <p role="status">처음 설정은 다시 시작한 뒤 완료됩니다.</p>}
    {notice.completed && <button type="button" className="fs-primary" onClick={() => {setSaved(null);void navigate('/forsic?view=start')}}>조사 화면으로</button>}
    <button type="button" onClick={() => {setSaved(null);void navigate('/forsic?view=setup')}}>설정 다시 확인</button></section></main>
  if (failed) return <main className="forsic-setup"><section className="fs-readonly"><p role="alert">설정을 불러오지 못했어요. 연결 상태를 확인하고 다시 시도해 주세요.</p><button type="button" onClick={() => {setFailed(false);setAttempt(value => value+1)}}>다시 확인</button></section></main>
  if (!status && !failed) return <p className="forsic-shell-loading" role="status">포식이 설정을 확인하고 있어요.</p>
  if (status && (status.required || explicit)) return <ForsicSetup key={String(explicit)} initial={status} onSaved={result => {setStatus(result);setSaved(result)}} />
  return children
}
