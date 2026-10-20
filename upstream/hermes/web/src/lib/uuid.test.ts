import {expect, it, vi} from 'vitest'
import {requestUUID} from './uuid'

it('uses the native secure-context UUID API when available', () => {
  const randomUUID = vi.fn(() => 'native-uuid')
  const getRandomValues = vi.fn()
  expect(requestUUID({randomUUID,getRandomValues})).toBe('native-uuid')
  expect(randomUUID).toHaveBeenCalledOnce()
  expect(getRandomValues).not.toHaveBeenCalled()
})
it('creates an RFC UUIDv4 from cryptographic bytes when HTTP LAN has no randomUUID', () => {
  const getRandomValues = vi.fn((bytes: Uint8Array) => {bytes.fill(255);return bytes})
  expect(requestUUID({getRandomValues})).toBe('ffffffff-ffff-4fff-bfff-ffffffffffff')
  expect(getRandomValues.mock.calls[0][0]).toHaveLength(16)
  expect(requestUUID({getRandomValues: bytes => {bytes.fill(0);return bytes}})).toBe('00000000-0000-4000-8000-000000000000')
})
it('fails explicitly if cryptographic randomness is unavailable', () => {
  expect(() => requestUUID({} as Crypto)).toThrow('Secure random generation')
})
