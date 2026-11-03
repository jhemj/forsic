import {useCallback, useEffect, useRef, useState} from 'react'
import {Copy, RefreshCw, Search} from 'lucide-react'
import {fetchJSON} from '@/lib/api'
import {useTenant} from '@/contexts/tenant-context'
import './ForsicIndicators.css'

interface Source {source_id: string; pointer: string; literal: string; source_path?: string}
export interface Indicator {indicator_id: string; type: string; canonical_value: string; status: string; summary: string; limitations: string[]; source_refs: Source[]}
interface Page {case_id: string; indicators: Indicator[]; total: number; next_offset: number | null}
const TYPES: Record<string,string> = {domain:'도메인',ip:'IP',hash:'파일 해시',path:'경로',url:'URL',account:'계정'}
const STATES: Record<string,string> = {suspicious:'의심 지표',observed:'관측 단서',undetermined:'판단 보류',benign:'정상 설명',withdrawn:'철회'}

export function ForsicIndicators({caseId,profile}: {caseId?: string | null; profile?: string}) {
  const tenant=useTenant()
  const scope=`${tenant?.tenant_id || ''}\0${profile || ''}\0${caseId || ''}`
  const [snapshot,setSnapshot]=useState<{scope:string;page:Page | null}>({scope,page:null})
  const [query,setQuery]=useState(''),[status,setStatus]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[copied,setCopied]=useState('')
  const generation=useRef(0)
  const page=snapshot.scope===scope?snapshot.page:null
  const load=useCallback(async (offset=0) => {
    if (!caseId || profile===undefined) return
    const ticket=++generation.current
    setBusy(true);setError('')
    const params=new URLSearchParams({case_id:caseId,profile,query,status,offset:String(offset)})
    try {
      const next=await fetchJSON<Page>(`/api/plugins/forsic/indicators?${params}`)
      if (ticket!==generation.current) return
      if (next.case_id!==caseId || !Array.isArray(next.indicators) || !Number.isInteger(next.total) ||
        (next.next_offset!==null && (!Number.isInteger(next.next_offset) || next.next_offset<=offset))) throw new Error('Invalid indicator page')
      setSnapshot(previous=>({scope,page:{...next,indicators:offset && previous.scope===scope ? [...(previous.page?.indicators || []),...next.indicators].filter((row,index,rows)=>rows.findIndex(r=>r.indicator_id===row.indicator_id)===index):next.indicators}}))
    } catch {if(ticket===generation.current)setError('IOC 목록을 불러오지 못했습니다. 다시 확인해 주세요.')}
    finally {if(ticket===generation.current)setBusy(false)}
  },[caseId,profile,query,status,scope])
  useEffect(()=>{setSnapshot({scope,page:null});setCopied('');const timer=setTimeout(()=>void load(),150);return()=>{clearTimeout(timer);++generation.current}},[scope,load])
  async function copy(value:string) {try {await navigator.clipboard.writeText(value);setCopied(value)} catch {setError('복사할 수 없습니다. 표시된 값을 선택해 복사해 주세요.')}}
  return <section className="forsic-iocs" aria-label="확보된 IOC 목록" aria-busy={busy}>
    <header><div><h2>확보된 IOC</h2><p>출처와 연결된 조사 지표 · 의심 지표와 관측 단서 구분</p></div><button type="button" aria-label="IOC 새로고침" disabled={busy || !caseId} onClick={()=>void load()}><RefreshCw size={16}/></button></header>
    <div className="fioc-controls"><label><Search size={15} aria-hidden="true"/><input aria-label="IOC 검색" placeholder="주소·해시·경로 검색" value={query} onChange={e=>setQuery(e.target.value)}/></label><select aria-label="IOC 분류" value={status} onChange={e=>setStatus(e.target.value)}><option value="">전체 분류</option>{Object.entries(STATES).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></div>
    {error && <p role="alert">{error}</p>}
    {page && <p className="fioc-count">{page.total}개 확보 · {page.indicators.length}개 표시</p>}
    {!error && !page?.indicators.length && <p className="fioc-empty">{busy?'목록 확인 중…':caseId?'아직 출처와 연결해 등록한 IOC가 없습니다.':'사건을 선택하면 IOC 목록이 표시됩니다.'}</p>}
    <div className="fioc-list">{page?.indicators.map(row=><article className="fioc-card" key={row.indicator_id}>
      <div className="fioc-labels"><span>{TYPES[row.type] || row.type}</span><b data-status={row.status}>{STATES[row.status] || '분류 미확인'}</b></div>
      <div className="fioc-value"><code>{row.canonical_value}</code><button type="button" aria-label={`${row.canonical_value} 복사`} onClick={()=>void copy(row.canonical_value)}><Copy size={15}/></button></div>
      <p>{row.summary || '관측 값 등록 · 해석 미기재'}</p>
      <details><summary>근거·적용 범위 <span>{row.source_refs.length}건</span></summary>{row.limitations.length>0 && <ul>{row.limitations.map((line,i)=><li key={i}>{line}</li>)}</ul>}<ol>{row.source_refs.map((source,i)=><li key={i}><code>{source.source_path || '보존된 분석 결과'}</code><p>관측 값: <code>{source.literal}</code></p><small>출처 {source.source_id} · {source.pointer}</small></li>)}</ol></details>
    </article>)}</div>
    {page?.next_offset!==null && page?.next_offset!==undefined && <button className="fioc-more" disabled={busy} type="button" onClick={()=>void load(page.next_offset!)}>더 보기</button>}
    <span className="fioc-copy-status" role="status">{copied?'IOC 값을 복사했습니다.':''}</span>
  </section>
}
