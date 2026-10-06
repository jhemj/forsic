import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { GatewayClient } from '../gatewayClient.js'
import { greetNewSession } from '../app/sessionGreeting.js'
import { getUiState, patchUiState, resetUiState } from '../app/uiStore.js'

describe('fresh-session generated greeting', () => {
  beforeEach(() => {
    resetUiState()
    patchUiState({ sid: 'fresh' })
  })

  it('submits a hidden native prompt and skips absent configuration or a superseded/busy session', async () => {
    let config = { config: { display: { new_session_prompt: '' } } }
    const request = vi.fn(async (method: string) => method === 'config.get' ? config : { status: 'streaming' })
    const gw = { request } as unknown as GatewayClient
    const sys = vi.fn()
    expect(await greetNewSession(gw, 'fresh', sys)).toBe(false)
    config = { config: { display: { new_session_prompt: 'Introduce yourself without tools.' } } }
    patchUiState({ busy: true })
    expect(await greetNewSession(gw, 'fresh', sys)).toBe(false)
    patchUiState({ busy: false })
    expect(await greetNewSession(gw, 'old-session', sys)).toBe(false)
    expect(await greetNewSession(gw, 'fresh', sys)).toBe(true)
    expect(request.mock.calls.filter(([method]) => method === 'prompt.submit')).toHaveLength(1)
    expect(request).toHaveBeenLastCalledWith('prompt.submit', {
      session_id: 'fresh', text: 'Introduce yourself without tools.', display_kind: 'hidden'
    })
    expect(getUiState().busy).toBe(true)
    expect(sys).not.toHaveBeenCalled()
  })

  it('does not retry an uncertain submission and permits ordinary chat after a failure', async () => {
    const request = vi.fn(async (method: string) => {
      if (method === 'config.get') return { config: { display: { new_session_prompt: 'Hello' } } }
      throw new Error('Connection closed')
    })
    const sys = vi.fn()
    expect(await greetNewSession({ request } as unknown as GatewayClient, 'fresh', sys)).toBe(false)
    expect(request.mock.calls.filter(([method]) => method === 'prompt.submit')).toHaveLength(1)
    expect(getUiState().busy).toBe(false)
    expect(sys).toHaveBeenCalledOnce()
  })
})
