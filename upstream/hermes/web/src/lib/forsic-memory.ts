import {fetchJSON} from './api'
import {tenantUrl,type Tenant} from './forsic-tenant'
export interface MemorySource {source_type?:string;case_id?:string;session_id?:string;message_id?:string;asserted_at?:string;[key:string]:unknown}
export interface MemoryItem {memory_id:string;revision:number;content:string;content_sha256:string;status:string;synchronization?:string;history?:MemoryItem[];applicability?:string;valid_from?:string|null;valid_until?:string|null;asserted_at?:string;recorded_at?:string;source?:MemorySource;reason?:string}
export interface MemoryCapacity {used?:number;limit?:number;used_chars?:number;max_chars?:number;remaining?:number}
export interface MemoryList {items:MemoryItem[];capacity:MemoryCapacity;sidecar_revision:number;has_more?:boolean;next_offset?:number|null;integrity_status?:string;synchronization?:string}
export interface MemoryResult {status:'committed'|'pending'|'recovery_required'|'failed'|'noop';item?:MemoryItem;capacity?:MemoryCapacity;sidecar_revision?:number;operation_id?:string;durable?:boolean;error?:string;message?:string}
export interface MemoryChanges {changes:MemoryResult[];sidecar_revision:number}
export function memoryRequest<T>(path:string,tenant:Tenant,init?:RequestInit){return fetchJSON<T>(tenantUrl(path,tenant),init)}
export function memoryAction(tenant:Tenant,payload:Record<string,unknown>){return memoryRequest<MemoryResult>('tenant-memory/action',tenant,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})}
export function memoryResultLabel(result:MemoryResult){if(result.status==='committed'&&result.durable!==true)return '저장 영속성과 출처 기록을 확인하지 못했습니다.';return ({committed:'이 테넌트의 운영기억에 저장했어요.',noop:'같은 내용이 이미 기록되어 있어요.',pending:'승인 대기 중입니다. 저장 완료가 아닙니다.',recovery_required:'본문과 출처의 동기화 확인이 필요합니다.',failed:'저장하지 못했습니다.'})[result.status]||'저장 상태를 확인하지 못했습니다.'}
