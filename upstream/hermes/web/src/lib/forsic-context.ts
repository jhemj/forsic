/** Native current occupancy only. Session throughput cannot substitute for context size. */
export interface ContextUsage {used: number; maximum: number; estimated: boolean}
export function readContextUsage(value: unknown): ContextUsage | null {
  if (!value || typeof value !== 'object') return null
  const usage = value as Record<string, unknown>
  const used = usage.context_used, maximum = usage.context_max
  if (typeof used !== 'number' || !Number.isFinite(used) || used < 0 || typeof maximum !== 'number' || !Number.isFinite(maximum) || maximum <= 0) return null
  return {used, maximum, estimated: usage.context_estimated !== false || usage.context_source !== 'provider_usage'}
}

/** Gateway average across up to ten recent completed API requests, including request wait. */
export function readRecentAvgTps(value: unknown): number | null {
  if (!value || typeof value !== 'object') return null
  const speed = (value as Record<string, unknown>).avg_tps
  return typeof speed === 'number' && Number.isFinite(speed) && speed > 0 ? speed : null
}
