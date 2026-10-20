import {useCallback, useEffect, useLayoutEffect, useRef, useState} from 'react'
import {Download, FolderArchive, RefreshCw, Search} from 'lucide-react'
import {authedFetch} from '@/lib/api'
import {requestUUID} from '@/lib/uuid'
import {evidenceFailure, evidenceKindLabel, type EvidenceCollection, type EvidenceCollectionItem, type EvidenceExport} from '@/lib/forsic-evidence-collection'
import './forsic-evidence-collection.css'

interface Props {sessionId?: string; profile?: string; caseId?: string; active: boolean}
type Selection = {evidence_item_id: string; item_version: string}
type ExportRequest = {source_snapshot_id: string; selection: 'selected' | 'all_eligible'; items: Selection[]; include_sensitive_originals: boolean; idempotency_key: string}
const API = '/api/plugins/forsic/evidence-collection'
const selectedKey = (item: Selection) => `${item.evidence_item_id}:${item.item_version}`

function EvidenceRow({item, checked, onChange}: {item: EvidenceCollectionItem; checked: boolean; onChange: () => void}) {
  const span = item.retained_span
  const sensitive = Boolean(item.sensitive)
  return <article className="fec-item">
    <div className="fec-item-heading">
      <input type="checkbox" aria-label={`${item.title} 선택`} checked={checked} disabled={!item.downloadable} onChange={onChange}/>
      <div><strong>{item.title || '보존된 증거'}</strong><div className="fec-badges">
        <span>{evidenceKindLabel[item.kind] || '보존 자료'}</span>
        <span>{item.currentness === 'current' ? '현재 연결' : '정정 전 이력'}</span>
        {sensitive && <span>기본 묶음은 가림 결과</span>}
      </div></div>
    </div>
    {item.excerpt !== undefined && <div className="fec-reading"><span className="fec-reading-label">보존된 내용</span><blockquote>{item.excerpt.length > 320 ? `${item.excerpt.slice(0,320)}…` : item.excerpt}</blockquote>{item.excerpt.length > 320 && <details><summary>발췌 전체 읽기</summary><pre>{item.excerpt}</pre></details>}</div>}
    {item.linked_questions?.length ? <div className="fec-links"><strong>연결된 판단</strong><ul>{item.linked_questions.map(q => <li key={q.id}>{q.title || '제목이 기록되지 않은 판단'}</li>)}</ul></div> : null}
    {item.collected_at && <p className="fec-meta">보존 반환 시각 · {new Date(typeof item.collected_at === 'number' ? item.collected_at * 1000 : item.collected_at).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'})} KST</p>}
    {!item.downloadable && <p className="fec-warning">{item.currentness === 'stale' ? '정정 전 이력입니다. 현재 채증 묶음에서는 제외됩니다.' : '원문 연결 또는 발췌를 검증하지 못해 내보낼 수 없습니다.'}</p>}
    <details className="fec-technical"><summary>근거·위치 보기</summary>
      {item.linked_questions?.length ? <p>판단 식별자 · {item.linked_questions.map(q => q.id).join(' · ')}</p> : null}
      <dl>
        <dt>출처</dt><dd>{item.source_ref?.id || '연결 미확인'}</dd>
        <dt>버전</dt><dd>{item.source_ref?.version || item.item_version}</dd>
        {span && <><dt>보존 반환 위치</dt><dd>{span.field_pointer || '/'} · UTF-8 {span.byte_start ?? '?'}–{span.byte_end ?? '?'} 바이트</dd></>}
        <dt>원본 위치</dt><dd>{item.original_locator ? '기록된 계보는 내려받은 묶음에서 확인할 수 있습니다.' : '원본 파일 내 위치 미확보'}</dd>
        <dt>자료 식별자</dt><dd>{item.evidence_item_id}</dd>
      </dl>
      <p className="fec-meta">보존 반환의 위치는 원본 이미지 내 위치와 다릅니다.</p>
    </details>
  </article>
}

/** Selected-scope requests only. Changing scope unmounts this component in the workbench. */
export function ForsicEvidenceCollection({sessionId, profile, caseId, active}: Props) {
  const [data, setData] = useState<EvidenceCollection | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [scope, setScope] = useState('case')
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('current')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [selection, setSelection] = useState<Map<string, Selection>>(new Map())
  const [includeRaw, setIncludeRaw] = useState(false)
  const [confirm, setConfirm] = useState<'selected' | 'all_eligible' | null>(null)
  const [pending, setPending] = useState<ExportRequest | null>(null)
  const [created, setCreated] = useState<EvidenceExport | null>(null)
  const [busy, setBusy] = useState(false)
  const [page, setPage] = useState(1)
  const sequence = useRef(0)
  const snapshotId = useRef('')
  const listRequest = useRef<AbortController | null>(null)
  const exportRequest = useRef<AbortController | null>(null)
  const blobUrl = useRef('')
  const activeRef = useRef(active)
  useLayoutEffect(() => {
    activeRef.current = active
    return () => {activeRef.current = false}
  }, [active])
  const cancelRequests = useCallback(() => {
    ++sequence.current
    listRequest.current?.abort(); exportRequest.current?.abort()
  }, [])
  const owner = useCallback(() => new URLSearchParams({profile: profile || '', session_id: sessionId || '', ...(caseId ? {case_id: caseId} : {})}), [profile, sessionId, caseId])
  const refresh = useCallback(async (cursor?: string, snapshot?: string) => {
    if (!active || !sessionId || !profile || !caseId) return
    const current = ++sequence.current
    listRequest.current?.abort()
    const controller = new AbortController(); listRequest.current = controller
    setLoading(true); setError('')
    const params = owner()
    params.set('scope', scope); params.set('status', status)
    if (kind) params.set('kind', kind)
    if (query) params.set('search', query)
    if (cursor) params.set('cursor', cursor)
    if (snapshot) params.set('source_snapshot_id', snapshot)
    try {
      const response = await authedFetch(`${API}?${params}`, {signal: controller.signal})
      if (!response.ok) throw new Error(evidenceFailure(response.status))
      const value: EvidenceCollection = await response.json()
      if (controller.signal.aborted || current !== sequence.current || !activeRef.current) return
      if (!value.case_id || (caseId && value.case_id !== caseId) || (value.profile && value.profile !== profile) ||
          (value.session_id && value.session_id !== sessionId) || !value.source_snapshot_id || !Array.isArray(value.items) || !value.counts) throw new Error(evidenceFailure(409))
      if (snapshotId.current !== value.source_snapshot_id) setSelection(new Map())
      snapshotId.current = value.source_snapshot_id
      setData(value)
      setPage(previous => cursor ? previous + 1 : 1)
    } catch (failure) {
      if (!controller.signal.aborted && current === sequence.current) setError(failure instanceof Error ? failure.message : evidenceFailure())
    } finally {if (!controller.signal.aborted && current === sequence.current) setLoading(false)}
  }, [active, sessionId, profile, owner, scope, status, kind, query, caseId])
  useEffect(() => {
    if (!active) return
    let cancelled = false
    void Promise.resolve().then(() => {if (!cancelled) void refresh()})
    return () => {cancelled = true; cancelRequests()}
  }, [active, refresh, cancelRequests])
  useEffect(() => () => {
    listRequest.current?.abort(); exportRequest.current?.abort()
    if (blobUrl.current) URL.revokeObjectURL(blobUrl.current)
  }, [])

  function choose(item: EvidenceCollectionItem) {
    setSelection(previous => {
      const next = new Map(previous), key = selectedKey(item)
      if (next.has(key)) next.delete(key)
      else next.set(key, {evidence_item_id: item.evidence_item_id, item_version: item.item_version})
      return next
    })
  }
  async function download(value: EvidenceExport) {
    if (!/^[a-zA-Z0-9_-]{1,100}$/.test(value.export_id)) throw new Error('잘못된 묶음 식별자입니다.')
    const controller = new AbortController(); exportRequest.current = controller
    const response = await authedFetch(`${API}/exports/${encodeURIComponent(value.export_id)}/download?${owner()}`, {signal: controller.signal})
    if (!response.ok) throw new Error(evidenceFailure(response.status))
    if (!response.headers.get('Content-Type')?.includes('application/zip')) throw new Error('묶음 형식을 확인하지 못했어요.')
    const blob = await response.blob()
    if (controller.signal.aborted || !activeRef.current) return
    if (blobUrl.current) URL.revokeObjectURL(blobUrl.current)
    blobUrl.current = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = blobUrl.current; link.download = `forsic-evidence-${value.export_id}.zip`
    document.body.append(link); link.click(); link.remove()
  }
  async function createExport(request: ExportRequest) {
    if (!active || !data || busy) return
    const controller = new AbortController(); exportRequest.current = controller
    setBusy(true); setError(''); setPending(request); setConfirm(null)
    try {
      const response = await authedFetch(`${API}/exports`, {method: 'POST', signal: controller.signal,
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...request, profile, session_id: sessionId, case_id: data.case_id})})
      if (!response.ok) {
        if (response.status >= 400 && response.status < 500) setPending(null)
        throw new Error(evidenceFailure(response.status))
      }
      const value: EvidenceExport = await response.json()
      if (controller.signal.aborted || !activeRef.current) return
      if (!value.export_id || (value.case_id && value.case_id !== data.case_id)) throw new Error('생성된 묶음의 사건을 확인하지 못했어요.')
      setCreated(value); setPending(null)
      if (value.status === 'ready') await download(value)
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : evidenceFailure())
    } finally {if (!controller.signal.aborted) setBusy(false)}
  }
  function start(selectionType: 'selected' | 'all_eligible') {
    if (!data || busy || loading) return
    if (includeRaw && confirm !== selectionType) {setConfirm(selectionType); return}
    void createExport({source_snapshot_id: data.source_snapshot_id, selection: selectionType, items: selectionType === 'selected' ? [...selection.values()] : [],
      include_sensitive_originals: includeRaw, idempotency_key: requestUUID()})
  }
  async function retryDownload() {
    if (!created || busy) return
    setBusy(true); setError('')
    try {
      const controller = new AbortController(); exportRequest.current = controller
      const response = await authedFetch(`${API}/exports/${encodeURIComponent(created.export_id)}?${owner()}`, {signal: controller.signal})
      if (!response.ok) throw new Error(evidenceFailure(response.status))
      const value: EvidenceExport = await response.json()
      if (controller.signal.aborted || !activeRef.current) return
      setCreated(value)
      if (value.status === 'ready') await download(value)
    } catch (failure) {if (activeRef.current) setError(failure instanceof Error ? failure.message : evidenceFailure())}
    finally {if (activeRef.current) setBusy(false)}
  }
  const eligible = data?.items.filter(item => item.downloadable) || []
  return <section className="fec" aria-label="사건 증거 모음">
    <div className="fec-summary"><FolderArchive size={25}/><div><strong>{data ? `채증 ${data.counts.total}건` : '증거 모음'}</strong>
      <p>{data ? `내보내기 가능 ${data.counts.downloadable}건 · 민감 자료 ${data.counts.restricted}건 · 정정 전 ${data.counts.stale}건` : '채택한 근거와 보존 발췌를 모아 봅니다.'}</p></div>
      <button type="button" aria-label="증거 목록 새로고침" disabled={loading || busy} onClick={() => void refresh()}><RefreshCw size={16}/></button>
    </div>
    <form className="fec-filters" onSubmit={event => {event.preventDefault(); setQuery(search.trim())}}>
      <select aria-label="증거 대화 범위" value={scope} disabled={busy} onChange={event => setScope(event.target.value)}><option value="case">전체 대화</option><option value="conversation">현재 대화</option></select>
      <select aria-label="증거 유형" value={kind} disabled={busy} onChange={event => setKind(event.target.value)}><option value="">모든 유형</option><option value="retained_excerpt">보존 발췌</option><option value="derived_analysis">파생 결과</option><option value="sensitive_detection">민감 탐지</option></select>
      <select aria-label="증거 상태" value={status} disabled={busy} onChange={event => setStatus(event.target.value)}><option value="current">현재 연결</option><option value="stale">정정 전 이력</option><option value="all">전체 이력</option></select>
      <label><Search size={15}/><input aria-label="증거 이름·내용 검색" value={search} disabled={busy} onChange={event => setSearch(event.target.value)} placeholder="이름·내용 검색"/></label><button type="submit" disabled={busy}>검색</button>
    </form>
    {error && <p role="alert" className="fec-warning">{error}</p>}
    {Boolean(data?.counts.errors) && <p className="fec-warning">원문 연결을 확인하지 못한 항목 {data?.counts.errors}건은 내보내기에서 제외됩니다.</p>}
    <div className="fec-actions">
      <label><input type="checkbox" aria-label="현재 페이지 증거 선택" checked={eligible.length > 0 && eligible.every(item => selection.has(selectedKey(item)))} disabled={!eligible.length || busy} onChange={event => {const checked = event.target.checked; setSelection(previous => {const next = new Map(previous); for (const item of eligible) {if (checked) next.set(selectedKey(item), {evidence_item_id: item.evidence_item_id, item_version: item.item_version}); else next.delete(selectedKey(item))} return next})}}/>현재 페이지</label>
      <span>선택 {selection.size}건</span>
      <button type="button" disabled={!selection.size || busy || loading || Boolean(pending)} onClick={() => start('selected')}><Download size={14}/>선택 증거 ZIP</button>
      <button type="button" disabled={!data?.counts.downloadable || busy || loading || Boolean(pending)} onClick={() => start('all_eligible')}>전체 채증 ZIP</button>
    </div>
    <p className="fec-meta">전체 채증 ZIP은 검색·대화 필터와 관계없이 선택 시점의 사건 전체 채증을 포함합니다. 원본 이미지 전체는 포함하지 않습니다.</p>
    {data?.permissions?.sensitive_originals && <label className="fec-raw"><input type="checkbox" checked={includeRaw} disabled={busy} onChange={event => {setIncludeRaw(event.target.checked); setConfirm(null)}}/>민감값 원문 발췌 포함 <small>서버 로컬 전용 · 최대 {data.permissions.max_sensitive_entries}건 / {Math.round(data.permissions.max_sensitive_bytes / 1024)} KiB</small></label>}
    {confirm && <div role="dialog" aria-label="민감 원문 포함 확인" className="fec-confirm"><strong>민감값 원문 발췌를 포함할까요?</strong>
      <p>{confirm === 'selected' ? `선택한 ${selection.size}건` : '사건 전체 채증'} 중 보존·검증 가능한 민감 원문이 포함됩니다. 이 ZIP은 로컬에서 보호하여 보관해 주세요.</p>
      <button type="button" onClick={() => start(confirm)}>확인하고 생성</button><button type="button" onClick={() => setConfirm(null)}>취소</button>
    </div>}
    {pending && !busy && <div className="fec-warning"><p>생성 결과가 확인되지 않았어요. 같은 요청 식별자로 확인하며, 새 묶음을 중복 요청하지 않습니다.</p><button type="button" onClick={() => void createExport(pending)}>기존 생성 요청 확인</button></div>}
    {created && <div className="fec-export"><span>{created.status === 'ready' ? `묶음 준비됨${created.item_count !== undefined ? ` · ${created.item_count}건` : ''}` : `묶음 상태: ${({'building':'생성 중','queued':'대기 중','failed':'실패','cancelled':'취소','expired':'보존 만료'} as Record<string,string>)[created.status] || '확인 필요'}`}{created.stale ? ' · 이전 스냅샷' : ''}</span><button type="button" disabled={busy} onClick={() => void retryDownload()}>{created.status === 'ready' ? '다시 내려받기' : '생성 상태 확인'}</button></div>}
    {busy && <p role="status">📦 증거 묶음을 확인하고 있어요…</p>}
    <div aria-busy={loading}>{data?.items.map(item => <EvidenceRow key={selectedKey(item)} item={item} checked={selection.has(selectedKey(item))} onChange={() => choose(item)}/>)}</div>
    {!loading && !error && data && !data.items.length && <p className="fec-empty">이 범위에 채택된 증거가 아직 없습니다. 도구 호출 이력과 채증 항목은 구분됩니다.</p>}
    {!caseId && <p className="fec-empty">사건에 연결된 대화를 선택해 주세요.</p>}
    {loading && <p role="status">증거 목록을 불러오고 있어요…</p>}
    {data && <footer className="fec-pagination"><span>{page}페이지 · 검색 결과 {data.counts.filtered ?? data.counts.total}건 중 {data.items.length}건 표시</span><button type="button" disabled={!data.next_cursor || loading || busy} onClick={() => void refresh(data.next_cursor || undefined, data.source_snapshot_id)}>다음 페이지</button></footer>}
  </section>
}
