/** Continuum HTTP client. Server contract owns settings; no automatic mutation retries. */
export { operationReference } from './operations.js'
export type { OperationId } from './operations.js'
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
export type CallInputs = { path?: Record<string, string | number>; query?: Record<string, string | number | boolean>; body?: Json | FormData }
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
  async wait(operationId: string, inputs: CallInputs, timeoutMs = 60000, intervalMs = 2000): Promise<unknown> {
    const op = (await this.operations())[operationId]
    if (!op || op.method !== 'GET') throw new Error('Waiting requires a read-only status operation')
    const deadline = Date.now() + timeoutMs
    while (true) {
      const state = await this.call(operationId, inputs) as { status?: string }
      if (['completed', 'failed', 'cancelled', 'blocked', 'paused'].includes(state?.status ?? '')) return state
      const remaining = deadline - Date.now()
      if (remaining <= 0) throw new MaestroError(null, 'Observation timed out; work continues on the server', operationId, false)
      await new Promise(resolve => setTimeout(resolve, Math.min(Math.max(intervalMs, 100), remaining)))
    }
  }
}
