export type H3PromptRewriteCandidateKind = 'deterministic' | 'base' | 'adapted'

export interface H3PromptRewriteBinding {
  readonly requestCommitment: string
  readonly originalPrompt: string
}

export interface H3PromptRewriteApplySelection {
  readonly selected_kind: H3PromptRewriteCandidateKind
  readonly request_commitment: string
  readonly preview_commitment: string
}

export interface H3PromptRewritePreview {
  readonly schema_version: 2
  readonly request_commitment: string
  readonly original_prompt: string
  readonly candidates: readonly Readonly<{
    kind: H3PromptRewriteCandidateKind
    text: string
    produced_by_runtime: boolean
  }>[]
  readonly selection: null
  readonly runtime_evidence: Readonly<{
    execution_available: true
    base_executed: true
    adapter_executed: true
    fallback_used: false
    execution_receipt_sha256: string
  }>
  readonly commitment: string
}

const validatedPreviews = new WeakSet<object>()
const kinds = ['deterministic', 'base', 'adapted'] as const
const hashPattern = /^[0-9a-f]{64}$/
const encoder = new TextEncoder()
const invalid = () => new Error('This prompt comparison is unavailable.')

function exactObject(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) throw invalid()
  const observed = Reflect.ownKeys(value)
  if (observed.length !== keys.length || observed.some(key => typeof key !== 'string' || !keys.includes(key))) throw invalid()
  return value as Record<string, unknown>
}

function text(value: unknown, nonempty = true): string {
  if (typeof value !== 'string' || (nonempty && !value) || value.includes('\0')
    || encoder.encode(value).length > 65536 || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) throw invalid()
  return value
}

function hash(value: unknown): string {
  if (typeof value !== 'string' || !hashPattern.test(value)) throw invalid()
  return value
}

// Matches the public Python document's sorted, UTF-8 canonical JSON.
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value !== null && typeof value === 'object') {
    const object = value as Record<string, unknown>
    return '{' + Object.keys(object).sort().map(key => JSON.stringify(key) + ':' + canonical(object[key])).join(',') + '}'
  }
  return JSON.stringify(value)
}

// HTTP LAN pages lack SubtleCrypto. Keep the same mandatory SHA-256 check
// with the standard 32-bit compression function and bounded public bytes.
function sha256WithoutSubtle(bytes: Uint8Array): string {
  const constants = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]
  const state = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
  const padded = new Uint8Array(Math.ceil((bytes.length + 9) / 64) * 64)
  padded.set(bytes)
  padded[bytes.length] = 0x80
  const view = new DataView(padded.buffer)
  view.setUint32(padded.length - 8, Math.floor(bytes.length / 0x20000000))
  view.setUint32(padded.length - 4, bytes.length * 8)
  const words = new Uint32Array(64)
  const rotate = (value: number, bits: number) => (value >>> bits) | (value << (32 - bits))
  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let i = 0; i < 16; i++) words[i] = view.getUint32(offset + i * 4)
    for (let i = 16; i < 64; i++) {
      const low = rotate(words[i - 15], 7) ^ rotate(words[i - 15], 18) ^ (words[i - 15] >>> 3)
      const high = rotate(words[i - 2], 17) ^ rotate(words[i - 2], 19) ^ (words[i - 2] >>> 10)
      words[i] = (words[i - 16] + low + words[i - 7] + high) >>> 0
    }
    let [a, b, c, d, e, f, g, h] = state
    for (let i = 0; i < 64; i++) {
      const first = (h + (rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25)) + ((e & f) ^ (~e & g)) + constants[i] + words[i]) >>> 0
      const second = ((rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0
      h = g; g = f; f = e; e = (d + first) >>> 0
      d = c; c = b; b = a; a = (first + second) >>> 0
    }
    for (const [index, value] of [a, b, c, d, e, f, g, h].entries()) state[index] = (state[index] + value) >>> 0
  }
  return state.map(value => value.toString(16).padStart(8, '0')).join('')
}

export async function parseH3PromptRewritePreview(value: unknown, binding: H3PromptRewriteBinding): Promise<H3PromptRewritePreview> {
  const requestCommitment = hash(binding.requestCommitment)
  const originalPrompt = text(binding.originalPrompt)
  const document = exactObject(value, ['schema_version', 'request_commitment', 'original_prompt', 'candidates', 'selection', 'runtime_evidence', 'commitment'])
  if (document.schema_version !== 2 || document.selection !== null
    || document.request_commitment !== requestCommitment || document.original_prompt !== originalPrompt
    || !Array.isArray(document.candidates) || document.candidates.length !== 3) throw invalid()
  const candidates = document.candidates.map((value, index) => {
    const candidate = exactObject(value, ['kind', 'text', 'produced_by_runtime'])
    if (candidate.kind !== kinds[index] || candidate.produced_by_runtime !== (index !== 0)) throw invalid()
    return Object.freeze({ kind: kinds[index], text: text(candidate.text), produced_by_runtime: index !== 0 })
  })
  if (candidates[0].text !== originalPrompt) throw invalid()
  const evidence = exactObject(document.runtime_evidence, ['execution_available', 'base_executed', 'adapter_executed', 'fallback_used', 'execution_receipt_sha256'])
  if (evidence.execution_available !== true || evidence.base_executed !== true
    || evidence.adapter_executed !== true || evidence.fallback_used !== false) throw invalid()
  const unsigned = {
    schema_version: 2 as const,
    request_commitment: requestCommitment,
    original_prompt: originalPrompt,
    candidates: Object.freeze(candidates),
    selection: null,
    runtime_evidence: Object.freeze({ execution_available: true as const, base_executed: true as const,
      adapter_executed: true as const, fallback_used: false as const,
      execution_receipt_sha256: hash(evidence.execution_receipt_sha256) }),
  }
  const commitment = hash(document.commitment)
  const bytes = encoder.encode(canonical(unsigned))
  if (bytes.length > 262144) throw invalid()
  const subtle = globalThis.crypto?.subtle
  const digest = typeof subtle?.digest === 'function'
    ? Array.from(new Uint8Array(await subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('')
    : sha256WithoutSubtle(bytes)
  if (digest !== commitment) throw invalid()
  const preview = Object.freeze({ ...unsigned, commitment })
  validatedPreviews.add(preview)
  return preview
}

export function isCurrentH3PromptRewritePreview(value: unknown, binding: H3PromptRewriteBinding): value is H3PromptRewritePreview {
  return !!value && typeof value === 'object' && validatedPreviews.has(value)
    && (value as H3PromptRewritePreview).request_commitment === binding.requestCommitment
    && (value as H3PromptRewritePreview).original_prompt === binding.originalPrompt
}
