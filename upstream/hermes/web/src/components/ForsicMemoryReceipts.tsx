import {useEffect,useState} from 'react'
import {Link} from 'react-router'
import {useTenant} from '@/contexts/tenant-context'
import {memoryRequest,memoryResultLabel,type MemoryChanges,type MemoryResult} from '@/lib/forsic-memory'
import {tenantPath} from '@/lib/forsic-tenant'
/** Notices come only from durable service receipts, never from assistant messages. */
export function ForsicMemoryReceipts(){
 const tenant=useTenant();const scope=`${tenant?.tenant_id}:${tenant?.profile_name}`;const [snapshot,setSnapshot]=useState<{scope:string;receipts:MemoryResult[]}>({scope:'',receipts:[]});const receipts=snapshot.scope===scope?snapshot.receipts:[]
 useEffect(()=>{if(!tenant)return;let alive=true;let revision:number|undefined;let running=false;async function poll(){if(running)return;running=true;try{if(revision===undefined){const snapshot=await memoryRequest<{sidecar_revision?:number;revision:number}>('tenant-memory/snapshot',tenant!);if(alive)revision=snapshot.sidecar_revision??snapshot.revision}else{const result=await memoryRequest<MemoryChanges>(`tenant-memory/changes?since_revision=${revision}`,tenant!);if(alive){revision=result.sidecar_revision;setSnapshot(previous=>({scope,receipts:[...(previous.scope===scope?previous.receipts:[]),...result.changes.filter(item=>item.status==='committed'&&item.durable===true&&item.item)].slice(-3)}))}}}catch{/* Polling failure must never become a save notification. */}finally{running=false}}void poll();const timer=setInterval(()=>void poll(),4000);return()=>{alive=false;clearInterval(timer)}},[tenant,scope])
 if(!tenant||!receipts.length)return null
 return <aside className="forsic-memory-receipts" aria-label="운영기억 저장 알림" aria-live="polite">{receipts.map((receipt,index)=><div key={receipt.operation_id||index}><span>{memoryResultLabel(receipt)}</span><Link to={tenantPath(`/memory?memory_id=${encodeURIComponent(receipt.item!.memory_id)}`,tenant)}>보기 · 수정 · 되돌리기</Link><button aria-label="저장 알림 닫기" onClick={()=>setSnapshot(previous=>({...previous,receipts:previous.receipts.filter(item=>item!==receipt)}))}>×</button></div>)}</aside>
}
