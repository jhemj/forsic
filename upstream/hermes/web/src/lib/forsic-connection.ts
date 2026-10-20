/** Display saved configuration only; never treat a sidecar socket as provider health. */
export interface ConnectionSnapshot {
  llm_current?: { model?: string; base_url?: string }
  llm_pending?: boolean
}

export function configuredConnection(config: ConnectionSnapshot) {
  const fields = config.llm_current ?? {}
  let server = ''
  if (typeof fields.base_url === 'string' && fields.base_url.trim()) {
    try {
      const url = new URL(fields.base_url)
      if (url.protocol === 'http:' || url.protocol === 'https:') {
        // Never render credentials or query/fragment tokens, including in titles.
        url.username = ''
        url.password = ''
        url.search = ''
        url.hash = ''
        server = url.toString().replace(/\/$/, '')
      }
    } catch {
      // Invalid URLs may contain secrets too; do not fall back to the raw value.
    }
  }
  return { model: typeof fields.model === 'string' ? fields.model : '', server, pending: !!config.llm_pending }
}
