import {fetchJSON} from './api'
export type MemoryMode = 'off' | 'manual' | 'confirmed_auto'
export interface Tenant {tenant_id:string;display_name:string;description?:string;profile_name:string;revision:number;memory_policy:{mode:MemoryMode};start_url?:string;new_url?:string;resume_url?:string}
export interface TenantDirectory {tenants:Tenant[];access:{can_create:boolean;mode:'local'|'restricted'}}
export interface TenantScope extends Tenant {case_id?:string;session_id?:string}
let selected: Pick<Tenant,'tenant_id'|'profile_name'> | null = null
const verifiedProfiles=new Map<string,string>()
export function setTenantRequestScope(scope:Pick<Tenant,'tenant_id'|'profile_name'>|null){selected=scope;if(scope)verifiedProfiles.set(scope.profile_name,scope.tenant_id)}
export function tenantRequestUrl(url:string){
  if(!selected || !url.startsWith('/api/plugins/forsic/') || /^\/api\/plugins\/forsic\/tenants(?:\/|\?|$)/.test(url))return url
  const parsed=new URL(url,'http://forsic.local')
  if(!parsed.searchParams.has('tenant_id'))parsed.searchParams.set('tenant_id',verifiedProfiles.get(parsed.searchParams.get('profile')||'')||selected.tenant_id)
  if(!parsed.searchParams.has('profile'))parsed.searchParams.set('profile',selected.profile_name)
  return parsed.pathname+parsed.search
}
export function tenantPath(path:string,tenant:Pick<Tenant,'tenant_id'|'profile_name'>){const url=new URL(path,'http://forsic.local');url.searchParams.set('tenant_id',tenant.tenant_id);url.searchParams.set('profile',tenant.profile_name);return url.pathname+url.search}
export function tenantUrl(path:string,tenant:Pick<Tenant,'tenant_id'|'profile_name'>){return tenantPath('/api/plugins/forsic/'+path,tenant)}
export function resolveTenant(params:URLSearchParams,signal?:AbortSignal){const query=new URLSearchParams();for(const [key,value] of [['tenant_id',params.get('tenant_id')],['profile',params.get('profile')],['case_id',params.get('case')],['session_id',params.get('resume')]])if(value)query.set(key!,value);return fetchJSON<TenantScope>(`/api/plugins/forsic/tenants/resolve?${query}`,{signal})}
export function verifiedTenant(value:Tenant):boolean{return Boolean(value?.tenant_id && value.profile_name && value.display_name && value.memory_policy && ['off','manual','confirmed_auto'].includes(value.memory_policy.mode))}
