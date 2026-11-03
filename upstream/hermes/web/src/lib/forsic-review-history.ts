/** Retained advice and note revisions; historical answers never certify today's report. */
export interface ReviewHistoryResponse {
  question_id: string; revision: number; note_event_id: string; recorded_at: number
  disposition: string; reason: string; limitation?: string; citation_refs?: string[]
  validation: 'validated_saved_response' | 'unverified_saved_response'
  applicability: 'current_note' | 'historical_note'
}
export interface ReviewHistoryItem {
  review_id: string; finding_id: string; question: string; why?: string; next_check?: string
  review_number?: number; report_draft_revision?: number; report_version?: string; stage: string; executive_round?: number; created_ns?: number; current_goal: boolean
  goal_id?: string; session_id?: string; report_question_ids?: string[]; quote_validation?: string
  currentness: 'not_revalidated'; status: 'answered' | 'historical_answer' | 'pending'
  responses: ReviewHistoryResponse[]
}
export interface ReviewHistory {
  case_id: string; session_id: string; profile: string; scope: 'case'; snapshot: string
  items: ReviewHistoryItem[]; total: number; offset: number; next_offset: number | null
  counts: {total: number; answered: number; pending: number; historical_answer: number; historical_goal: number; receipts: number}
  unlinked_responses?: (ReviewHistoryResponse & {review_id: string; finding_id: string; question?: string; session_id?: string})[]
  issues: {review_id?: string; status: string; message: string; question?: string; reason?: string}[]
}
