type StoredIntent = { payload: Record<string, unknown> }
export type Ledger = Record<string, StoredIntent>
const MAX_LEDGER_BYTES = 2 * 1024 * 1024
const MAX_PAYLOAD_BYTES = 128 * 1024

function boundedJson(value: unknown): boolean {
  const pending: { value: unknown; depth: number }[] = [{ value, depth: 0 }]
  let nodes = 0
  while (pending.length) {
    const item = pending.pop()!
    if (++nodes > 4096 || item.depth > 12 || (typeof item.value === 'number' && !Number.isFinite(item.value))) return false
    if (item.value && typeof item.value === 'object') {
      for (const child of Object.values(item.value)) pending.push({ value: child, depth: item.depth + 1 })
    }
  }
  return true
}

/** Shared bounded tab storage; operation namespaces never mix generation and training. */
export class SubmissionLedger {
  private prefix: string
  private currentScope: () => string | null
  private label: string
  constructor(prefix: string, currentScope: () => string | null, label = '') {
    this.prefix = prefix
    this.currentScope = currentScope
    this.label = label
  }

  read(scope: string | null): Ledger | null {
    if (!scope || scope !== this.currentScope()) return null
    try {
      const raw = sessionStorage.getItem(this.prefix + scope)
      if (raw === null) return Object.create(null) as Ledger
      if (raw.length > MAX_LEDGER_BYTES) return null
      const ledger = JSON.parse(raw)
      if (!ledger || typeof ledger !== 'object' || Array.isArray(ledger) || Object.keys(ledger).length > 16) return null
      for (const [workspace, entry] of Object.entries(ledger)) {
        const payload = (entry as StoredIntent)?.payload
        if (!workspace || !payload || typeof payload !== 'object' || Array.isArray(payload)
          || payload.workspace !== workspace || typeof payload.requestId !== 'string'
          || !payload.requestId || payload.requestId.length > 128 || !boundedJson(payload) || JSON.stringify(payload).length > MAX_PAYLOAD_BYTES) return null
      }
      return ledger as Ledger
    } catch { return null }
  }

  write(scope: string | null, expected: Ledger, next: Ledger): boolean {
    try {
      const current = this.read(scope)
      if (!scope || !current || JSON.stringify(current) !== JSON.stringify(expected)) return false
      const raw = JSON.stringify(next)
      if (raw.length > MAX_LEDGER_BYTES) return false
      sessionStorage.setItem(this.prefix + scope, raw)
      const stored = this.read(scope)
      return stored !== null && JSON.stringify(stored) === raw
    } catch { return false }
  }

  payload(entry: StoredIntent): Record<string, unknown> {
    const payload = JSON.parse(JSON.stringify(entry.payload)) as Record<string, unknown>
    freeze(payload)
    return payload
  }

  reserve(scope: string | null, workspace: string, payload: Record<string, unknown>): Record<string, unknown> {
    const ledger = this.read(scope)
    if (!ledger) throw new Error('Submission recovery could not be read in this tab. Check the jobs before trying again.')
    if (Object.hasOwn(ledger, workspace)) throw new Error('This project already has a submission awaiting confirmation.')
    if (Object.keys(ledger).length >= 16) throw new Error(`Confirm an unresolved ${this.label ? this.label + ' ' : ''}submission in another project before starting another.`)
    const frozen = JSON.parse(JSON.stringify(payload)) as Record<string, unknown>
    if (frozen.workspace !== workspace || typeof frozen.requestId !== 'string' || !frozen.requestId
      || frozen.requestId.length > 128 || !boundedJson(frozen) || JSON.stringify(frozen).length > MAX_PAYLOAD_BYTES) throw new Error('This submission is too large or invalid to retain safely.')
    const nextLedger = Object.assign(Object.create(null), ledger, { [workspace]: { payload: frozen } }) as Ledger
    if (!this.write(scope, ledger, nextLedger)) throw new Error('This tab could not retain the submission safely. Check the jobs before trying again.')
    freeze(frozen)
    return frozen
  }
}
export function freeze(value: unknown): void {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) freeze(child)
    Object.freeze(value)
  }
}

