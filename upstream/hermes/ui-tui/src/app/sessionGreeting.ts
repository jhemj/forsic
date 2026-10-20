import type { GatewayClient } from '../gatewayClient.js'
import { getUiState, patchUiState } from './uiStore.js'

interface GreetingConfig {
  config?: { display?: { new_session_prompt?: string } }
}

// Only the fresh-session path calls this, never resume/activate/reconnect.
// Use the existing prompt transport and hidden input support: the generated reply
// is an ordinary persisted assistant message, not a second greeting/model client.
export async function greetNewSession(gw: GatewayClient, sid: string, sys: (message: string) => void): Promise<boolean> {
  let submitted = false
  try {
    const cfg = await gw.request<GreetingConfig>('config.get', { key: 'full' })
    const text = cfg?.config?.display?.new_session_prompt?.trim()
    if (!text || getUiState().sid !== sid || getUiState().busy) return false
    patchUiState({ busy: true, status: 'running…' })
    submitted = true
    await gw.request('prompt.submit', { session_id: sid, text, display_kind: 'hidden' })
    return true
  } catch (error) {
    if (getUiState().sid === sid) {
      if (submitted) patchUiState({ busy: false, status: 'ready' })
      sys(`인사를 불러오지 못했어요. 바로 메시지를 보내셔도 돼요. (${error instanceof Error ? error.message : String(error)})`)
    }
    return false
  }
}
