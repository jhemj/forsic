import {authedFetch, fetchJSON} from '@/lib/api';

export interface ManagedTool {name: string; version?: string; method?: string; update_supported?: boolean}
export interface ManagedEnvironment {id: string; status?: string; platform?: string; created_at?: string | number; tools?: ManagedTool[]; image_digest?: string; validation?: unknown; inventory?: unknown}
export interface ManagedPackEntry {name: string; description?: string; requires_tools?: string[]; availability?: unknown}
export interface ManagedPack {id: string; version: string; sha256: string; name?: string; description?: string; kind?: string; tools?: ManagedPackEntry[]; skills?: ManagedPackEntry[]; dependencies?: unknown; validation?: unknown; builtin?: boolean; referenced?: boolean; source?: unknown; status?: string; functional_status?: string; environment_id?: string; availability?: unknown}
export interface PackChoice {version: string; sha256: string; enabled: boolean; tools?: Record<string, boolean>; skills?: Record<string, boolean>}
export interface WorkflowSkillDraft {pack_id: string; version: string; name: string; description: string; skill_md: string; requires_tools: string[]}
export interface WorkflowSkillProposal {
  id: string; revision: number; status: string; draft_sha256: string; draft: WorkflowSkillDraft;
  reason: string; applicability: string; limitations: string; diff: string;
  target: {builtin?: string; pack_id?: string; version?: string; sha256: string; skill_name?: string} | null;
  origin: {case_id: string; receipts: {evidence_id: string; sha256: string; tool: string}[]};
  validation: {structure: string; functional: string; lint?: {severity: string; rule: string; message: string}[]};
  publication: {status: string; limitation?: string; pack_id?: string; version?: string; sha256?: string};
  activation: {status: string; scope?: string};
}
export interface ManagementJob {id: string; kind?: string; status: string; stage?: string; error?: unknown; log_tail?: string | string[]; can_retry?: boolean; operation_id?: string; stages?: unknown; plan?: unknown; source?: unknown; support?: unknown; native_session_id?: string; fixture_test?: unknown; artifact_id?: string; [key: string]: unknown}
export interface ManagementSnapshot {
  revision: string; local_admin: boolean; runtime_pending?: boolean;
  environments: {items: ManagedEnvironment[]; default_environment_id?: string; default_revision?: string; case?: {revision?: string | number; environment_id?: string; extensions?: Record<string, PackChoice>; pending?: unknown}; updates?: unknown[]};
  packs: {items: ManagedPack[]; selection: {revision?: string; packs: Record<string, PackChoice>}};
  builder: {jobs: ManagementJob[]}; jobs: ManagementJob[];
  skill_proposals?: {items: WorkflowSkillProposal[]};
}
export interface ManagementResult {status?: string; snapshot?: ManagementSnapshot; preview?: unknown; artifact_id?: string; job?: ManagementJob; [key: string]: unknown}
export interface ManagementUpload {upload_id: string; sha256: string; bytes: number; name: string}
export function managementUrl(profile?: string, caseId?: string) {
  const query = new URLSearchParams({profile: profile || 'default'});
  if (caseId) query.set('case_id', caseId);
  return `/api/plugins/forsic/management?${query}`;
}
export async function uploadManagementFile(file: File, kind: 'pack' | 'environment' | 'source', profile?: string) {
  const form = new FormData(); form.set('file', file); form.set('kind', kind);
  return fetchJSON<ManagementUpload>(`/api/plugins/forsic/management/upload?profile=${encodeURIComponent(profile || 'default')}`, {method: 'POST', body: form});
}
interface SaveFileHandle {createWritable(): Promise<WritableStream<Uint8Array>>}
export async function downloadManagementFile(artifactId: string, profile?: string) {
  const picker = (window as Window & {showSaveFilePicker?: (options: {suggestedName: string}) => Promise<SaveFileHandle>}).showSaveFilePicker;
  let handle: SaveFileHandle | undefined;
  if (picker) {
    try {handle = await picker.call(window, {suggestedName: `forsic-${artifactId}.archive`});}
    catch (reason) {if (reason instanceof DOMException && reason.name === 'AbortError') return; throw reason;}
  }
  const response = await authedFetch(`/api/plugins/forsic/management/files/${encodeURIComponent(artifactId)}?profile=${encodeURIComponent(profile || 'default')}`);
  if (!response.ok) throw new Error(`내보낸 파일을 받지 못했어요 (${response.status}).`);
  if (handle && response.body) {await response.body.pipeTo(await handle.createWritable()); return;}
  const size = Number(response.headers.get('Content-Length'));
  if (!response.headers.has('Content-Length') || !Number.isFinite(size) || size > 256 * 1024**2) {
    await response.body?.cancel();
    throw new Error('큰 환경 파일은 파일 저장 대화상자를 지원하는 브라우저에서 다운로드해 주세요. 내보낸 파일은 보존되어 있어요.');
  }
  const blob = await response.blob(); const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `forsic-${artifactId}.archive`; anchor.click(); URL.revokeObjectURL(url);
}
export const activeManagementJob = (job: ManagementJob) => ['queued', 'pending', 'starting', 'completion_unknown', 'running', 'cancelling', 'cancel_requested', 'waiting', 'preparing'].includes(job.status);
