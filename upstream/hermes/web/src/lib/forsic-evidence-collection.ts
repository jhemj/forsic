/** Display data for retained, explicitly adopted case evidence. Never model input. */
export interface EvidenceCollectionItem {
  evidence_item_id: string
  item_version: string
  title: string
  kind: string
  source_ref?: {id: string; version: string}
  retained_span?: {field_pointer?: string; byte_start?: number; byte_end?: number}
  original_locator?: unknown
  excerpt?: string
  linked_questions?: Array<{id: string; title?: string}>
  linked_claims?: Array<{id: string; statement?: string}>
  conversation_ids?: string[]
  currentness: string
  sensitive?: boolean | Record<string, unknown>
  downloadable: boolean
  download_reason?: string
  collected_at?: number | string | null
}
export interface EvidenceCollection {
  case_id: string
  profile?: string
  session_id?: string
  source_snapshot_id: string
  selection_digest: string
  items: EvidenceCollectionItem[]
  counts: {total: number; downloadable: number; restricted: number; stale: number; filtered?: number; errors?: number}
  next_cursor?: string | null
  offset?: number
  permissions?: {sensitive_originals: boolean; max_sensitive_entries: number; max_sensitive_bytes: number}
}
export interface EvidenceExport {
  export_id: string
  status: 'building' | 'ready' | 'failed' | 'cancelled' | 'expired' | 'queued'
  case_id?: string
  item_count?: number
  source_snapshot_id?: string
  includes_sensitive_originals?: boolean
  stale?: boolean
}
export const evidenceKindLabel: Record<string, string> = {
  retained_excerpt: '보존 발췌', derived_analysis: '파생 분석 결과', sensitive_detection: '민감정보 탐지',
  original_copy: '보존 원본 사본', excerpt: '보존 발췌', derived: '파생 분석 결과', sensitive: '민감정보 탐지',
}
export function evidenceFailure(status?: number): string {
  if (status === 401) return '인증이 만료됐어요. 화면을 새로고침해 주세요.'
  if (status === 403) return '이 자료에 접근할 수 없어요. 민감 원문은 서버의 로컬 관리 화면에서만 선택할 수 있습니다.'
  if (status === 404) return '선택한 증거 또는 보존 묶음을 찾을 수 없어요.'
  if (status === 409) return '기록이나 선택 버전이 변경됐어요. 목록을 새로고침하고 다시 선택해 주세요.'
  if (status === 413 || status === 429) return '묶음의 허용량을 넘었어요. 증거를 나누어 선택해 주세요.'
  return '요청 결과를 확인하지 못했어요. 기존 조사 기록은 유지됩니다.'
}
