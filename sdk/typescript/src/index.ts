/** Continuum HTTP client. Server contract owns settings; no automatic mutation retries. */
export { operationReference } from './operations.js'
export type { OperationId } from './operations.js'
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
export type CallInputs = { path?: Record<string, string | number>; query?: Record<string, string | number | boolean>; body?: Json | FormData; maximumResponseBytes?: number }
type Operation = { operationId: string; method: string; path: string; summary?: string; [key: string]: unknown }
type Contract = { paths: Record<string, Record<string, Operation>> }

export class MaestroError extends Error {
  constructor(public status: number | null, public detail: unknown,
              public operation: string, public uncertain: boolean) {
    super(`${operation}: ${typeof detail === 'string' ? detail : 'request refused'}`)
    this.name = 'MaestroError'
  }
  get nextAction() { return this.uncertain ? 'Reconcile the same identity; do not blindly retry or cancel.' : 'Read discovery and the operation error contract.' }
}

export class MaestroClient {
  private schema?: Contract
  readonly baseUrl: string
  readonly origin: string
  constructor(baseUrl: string, private options: { origin?: string; fetch?: typeof fetch; timeoutMs?: number } = {}) {
    const url = new URL(baseUrl)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('Use the configured Maestro HTTP(S) URL without credentials or query')
    this.baseUrl = baseUrl.replace(/\/$/, '')
    this.origin = options.origin ?? url.origin
  }
  async request(method: string, path: string, inputs: Omit<CallInputs, 'path'> = {}): Promise<unknown> {
    if (!path.startsWith('/api/v1/') && path !== '/openapi.json' && path !== '/health' && path !== '/ready') throw new Error('Only Continuum HTTP paths are supported')
    if (path.includes('?') || path.includes('#')) throw new Error('Pass query parameters separately')
    const url = new URL(this.baseUrl + path)
    if (url.origin !== new URL(this.baseUrl).origin || (!url.pathname.startsWith('/api/v1/') && !['/openapi.json', '/health', '/ready'].includes(url.pathname))) throw new Error('Path must remain inside the public Continuum API')
    for (const [key, value] of Object.entries(inputs.query ?? {})) url.searchParams.set(key, String(value))
    method = method.toUpperCase()
    if (inputs.maximumResponseBytes !== undefined && (!Number.isSafeInteger(inputs.maximumResponseBytes) || inputs.maximumResponseBytes <= 0)) throw new Error('A response byte limit must be a positive integer')
    const mutation = !['GET', 'HEAD'].includes(method)
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.options.timeoutMs ?? 60000)
    const form = inputs.body instanceof FormData
    // Browsers own Origin and their session cookie. Node callers provide an
    // operator-authorized fetch adapter/cookie jar; this client exports neither.
    const headers: Record<string, string> = typeof window === 'undefined' ? { Origin: this.origin } : {}
    if (inputs.body !== undefined && !form) headers['Content-Type'] = 'application/json'
    try {
      let response: Response
      try {
        response = await (this.options.fetch ?? fetch)(url, { method, headers, credentials: 'include', redirect: 'error', signal: controller.signal,
          body: form ? inputs.body as FormData : inputs.body === undefined ? undefined : JSON.stringify(inputs.body) })
      } catch {
        throw new MaestroError(null, 'Transport interrupted; server state is unknown', `${method} ${path}`, mutation)
      }
      if (!response.ok) {
        let detail: unknown = 'HTTP request refused'
        try { detail = (await response.json()).detail ?? detail } catch { /* Keep redacted transport detail. */ }
        throw new MaestroError(response.status, detail, `${method} ${path}`, mutation && response.status >= 500)
      }
      if (response.status === 204) return null
      const contentType = response.headers.get('content-type') ?? ''
      if (inputs.maximumResponseBytes !== undefined) {
        const limit = inputs.maximumResponseBytes
        const declared = Number(response.headers.get('content-length'))
        if (declared > limit) {
          await response.body?.cancel()
          throw new MaestroError(413, 'Media exceeds the bounded adapter limit; use the explicit download/upload workflow', `${method} ${path}`, mutation)
        }
        const reader = response.body?.getReader()
        if (!reader) throw new Error('Response body missing')
        const chunks: Uint8Array[] = []
        let length = 0
        while (true) {
          const { value, done } = await reader.read()
          if (done) break
          length += value.byteLength
          if (length > limit) {
            await reader.cancel()
            throw new MaestroError(413, 'Media exceeds the bounded adapter limit', `${method} ${path}`, mutation)
          }
          chunks.push(value)
        }
        const bytes = new Uint8Array(length)
        let offset = 0
        for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
        return contentType.includes('json') ? JSON.parse(new TextDecoder().decode(bytes)) : contentType.startsWith('text/') ? new TextDecoder().decode(bytes) : bytes.buffer
      }
      return contentType.includes('json') ? await response.json() : contentType.startsWith('text/') ? await response.text() : await response.arrayBuffer()
    } catch (error) {
      if (error instanceof MaestroError) throw error
      throw new MaestroError(null, 'Response interrupted or invalid; reconcile server state', `${method} ${path}`, mutation)
    } finally { clearTimeout(timer) }
  }
  async contract(refresh = false): Promise<Contract> {
    if (!this.schema || refresh) this.schema = await this.request('GET', '/openapi.json') as Contract
    return this.schema
  }
  async operations(feature?: string): Promise<Record<string, Operation>> {
    const operations: Record<string, Operation> = {}
    for (const [path, methods] of Object.entries((await this.contract()).paths)) {
      if (!path.startsWith('/api/v1/')) continue
      for (const [method, operation] of Object.entries(methods)) {
        const declaredFeature = String((operation['x-maestro'] as { feature?: string } | undefined)?.feature ?? '')
        if (feature && declaredFeature !== feature && !declaredFeature.startsWith(feature + '.')) continue
        if (['get', 'post', 'put', 'patch', 'delete', 'head'].includes(method)) operations[operation.operationId] = { ...operation, method: method.toUpperCase(), path }
      }
    }
    return operations
  }
  async call(operationId: string, inputs: CallInputs = {}): Promise<unknown> {
    const op = (await this.operations())[operationId]
    if (!op) throw new Error('Unknown operation; discover the current contract')
    const keys = [...op.path.matchAll(/\{([^}]+)\}/g)].map(match => match[1])
    if (keys.length !== Object.keys(inputs.path ?? {}).length || keys.some(key => !(key in (inputs.path ?? {})))) throw new Error(`Path fields must be exactly ${keys.join(', ')}`)
    let path = op.path
    for (const key of keys) path = path.replace(`{${key}}`, encodeURIComponent(String(inputs.path![key])))
    return this.request(op.method, path, inputs)
  }
  discover() { return this.request('GET', '/api/v1/api/discovery') }
  /** Reuse one project-authorized saved reference. Creates a private upload,
   * never a generation or trained model, and never retries an uncertain upload. */
  async prepareVoiceReference(project: string, assetId: string, variantId: string) {
    type Reference = { id: string; variant_type: string; status: string; outputs: { filename: string; media_type: string; relative_path: string }[]; metadata?: { wav_sha256?: string; transcript?: string } }
    const prefix = `/api/v1/projects/${encodeURIComponent(project)}/assets`
    const listing = await this.request('GET', prefix, { query: { asset_type: 'voice' } }) as { assets: { id: string; asset_type?: string; variants?: Reference[] }[] }
    const asset = listing.assets.find(value => value.id === assetId)
    if (asset?.asset_type !== 'voice') throw new Error('Select an authorized voice asset')
    const variant = asset.variants?.find(value => value.id === variantId)
    if (!variant || variant.variant_type !== 'voice_reference' || variant.status !== 'kept' || variant.outputs.length !== 1) throw new Error('Select one kept voice-reference variant containing one recording')
    const output = variant.outputs[0]
    if (!/^[^/\\\x00-\x1f]{1,200}\.(wav|flac)$/i.test(output.filename) || !output.media_type.startsWith('audio/') || output.relative_path !== `media/${assetId}/${variantId}/${output.filename}`) throw new Error('Expected a saved WAV or FLAC reference, not a path or URL')
    const raw = await this.request('GET', `${prefix}/media/${output.relative_path.split('/').map(encodeURIComponent).join('/')}`, { maximumResponseBytes: 16 * 1024 * 1024 })
    if (!(raw instanceof ArrayBuffer) || !raw.byteLength) throw new Error('Expected authorized audio bytes')
    const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', raw))].map(value => value.toString(16).padStart(2, '0')).join('')
    if (variant.metadata?.wav_sha256 && variant.metadata.wav_sha256 !== digest) throw new Error('Saved reference differs from its recorded content hash')
    const transcript = variant.metadata?.transcript ?? ''
    if (typeof transcript !== 'string' || transcript.length > 20000) throw new Error('Reference transcript must be bounded text')
    const form = new FormData()
    form.append('file', new Blob([raw], { type: output.media_type }), output.filename)
    const upload = await this.request('POST', '/api/v1/upload-audio', { query: { private: true }, body: form }) as Record<string, unknown>
    const { path: _hostPath, ...publicUpload } = upload
    return { source: { project, asset_id: assetId, variant_id: variantId, filename: output.filename, sha256: digest }, upload: publicUpload, transcript, bytes: raw.byteLength, generation_submitted: false }
  }
  async wait(operationId: string, inputs: CallInputs, timeoutMs = 60000, intervalMs = 2000): Promise<unknown> {
    const op = (await this.operations())[operationId]
    if (!op || op.method !== 'GET') throw new Error('Waiting requires a read-only status operation')
    const deadline = Date.now() + timeoutMs
    while (true) {
      const state = await this.call(operationId, inputs) as { status?: string; recovery_actionable?: boolean; recovery_blocked?: boolean }
      // Yield actionable recovery without retrying or cancelling. An
      // interruption flag alone can mean active automatic recovery.
      if (['completed', 'failed', 'cancelled', 'blocked', 'paused'].includes(state?.status ?? '') || state?.recovery_actionable === true || state?.recovery_blocked === true) return state
      const remaining = deadline - Date.now()
      if (remaining <= 0) throw new MaestroError(null, 'Observation timed out; reconcile server state. No cancellation or retry was requested', operationId, false)
      await new Promise(resolve => setTimeout(resolve, Math.min(Math.max(intervalMs, 100), remaining)))
    }
  }
}
