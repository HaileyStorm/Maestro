import type { Yue2Track } from '../api/client'
import { currentAccountIdentityEpoch, useStore } from '../stores/useStore'
import { terminalJobScope } from './terminalJobMemory'

export type Yue2SubmissionIntent = Readonly<{
  accountEpoch: number
  workspace: string
  requestId: string
  payload: Record<string, unknown>
  accountScope: string | null
  phase: 'sending' | 'unconfirmed' | 'unavailable'
  ambiguous: boolean
}>

// Unresolved requests remain in this tab across reloads; never evict to make room.
const MAX_INTENTS = 16
const intents = new Map<string, Yue2SubmissionIntent>()
const listeners = new Set<() => void>()
let activeEpoch: number | null = null
let activeScope: string | null = null
const notify = () => { for (const listener of listeners) listener() }

type StoredIntent = { payload: Record<string, unknown> }
type Ledger = Record<string, StoredIntent>
const STORAGE_PREFIX = 'maestro:yue2-submissions-v1:'
const MAX_LEDGER_BYTES = 2 * 1024 * 1024
const MAX_PAYLOAD_BYTES = 128 * 1024

export function yue2SubmissionScope(): string | null {
  const state = useStore.getState()
  const context = state.accountContext ?? state.accessContext?.accounts
  return terminalJobScope(context) ?? (context?.enabled === true && context.authenticated === false
    && state.accessContext?.account_project_access_active === false ? 'legacy-project-access' : null)
}

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

function readLedger(): Ledger | null {
  if (!activeScope || activeScope !== yue2SubmissionScope()) return null
  try {
    const raw = sessionStorage.getItem(STORAGE_PREFIX + activeScope)
    if (raw === null) return Object.create(null) as Ledger
    if (raw.length > MAX_LEDGER_BYTES) return null
    const ledger = JSON.parse(raw)
    if (!ledger || typeof ledger !== 'object' || Array.isArray(ledger) || Object.keys(ledger).length > MAX_INTENTS) return null
    for (const [workspace, entry] of Object.entries(ledger)) {
      const payload = (entry as StoredIntent)?.payload
      if (!workspace || !payload || typeof payload !== 'object' || Array.isArray(payload)
        || payload.workspace !== workspace || typeof payload.requestId !== 'string'
        || !payload.requestId || payload.requestId.length > 128 || !boundedJson(payload) || JSON.stringify(payload).length > MAX_PAYLOAD_BYTES) return null
    }
    return ledger as Ledger
  } catch { return null }
}

function writeLedger(expected: Ledger, next: Ledger): boolean {
  try {
    const current = readLedger()
    if (!activeScope || !current || JSON.stringify(current) !== JSON.stringify(expected)) return false
    const raw = JSON.stringify(next)
    if (raw.length > MAX_LEDGER_BYTES) return false
    sessionStorage.setItem(STORAGE_PREFIX + activeScope, raw)
    const stored = readLedger()
    return stored !== null && JSON.stringify(stored) === raw
  } catch { return false }
}

export function pruneYue2SubmissionIntents(accountEpoch: number): void {
  const scope = yue2SubmissionScope()
  if (activeEpoch === accountEpoch && activeScope === scope) return
  activeEpoch = accountEpoch
  activeScope = scope
  intents.clear()
  notify()
}

// Account changes also prune submissions while no composer is mounted.
useStore.subscribe(() => pruneYue2SubmissionIntents(currentAccountIdentityEpoch()))

export function subscribeYue2SubmissionIntents(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function getYue2SubmissionIntent(accountEpoch: number, workspace: string): Yue2SubmissionIntent | null {
  if (activeEpoch !== accountEpoch) return null
  const ledger = readLedger()
  if (!ledger) return Object.freeze({ accountEpoch, accountScope: activeScope, workspace,
    requestId: '', payload: Object.freeze({}), phase: 'unavailable', ambiguous: true })
  const stored = Object.hasOwn(ledger, workspace) ? ledger[workspace] : null
  if (!stored) { intents.delete(workspace); return null }
  const current = intents.get(workspace)
  if (current && JSON.stringify(current.payload) === JSON.stringify(stored.payload)) return current
  const payload = JSON.parse(JSON.stringify(stored.payload)) as Record<string, unknown>
  freeze(payload)
  const restored: Yue2SubmissionIntent = Object.freeze({ accountEpoch, accountScope: activeScope, workspace,
    requestId: String(payload.requestId), payload, phase: 'unconfirmed', ambiguous: true })
  intents.set(workspace, restored)
  return restored
}

function freeze(value: unknown): void {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) freeze(child)
    Object.freeze(value)
  }
}

export function reserveYue2SubmissionIntent(accountEpoch: number, workspace: string, payload: Record<string, unknown>): Yue2SubmissionIntent {
  pruneYue2SubmissionIntents(accountEpoch)
  const ledger = readLedger()
  if (!ledger) throw new Error('Submission recovery could not be read in this tab. Check the song library before trying again.')
  if (Object.hasOwn(ledger, workspace)) throw new Error('This project already has a submission awaiting confirmation.')
  if (Object.keys(ledger).length >= MAX_INTENTS) throw new Error('Confirm an unresolved YuE2 submission in another project before starting another.')
  const frozen = JSON.parse(JSON.stringify(payload)) as Record<string, unknown>
  if (frozen.workspace !== workspace || typeof frozen.requestId !== 'string' || !frozen.requestId
    || frozen.requestId.length > 128 || !boundedJson(frozen) || JSON.stringify(frozen).length > MAX_PAYLOAD_BYTES) throw new Error('This submission is too large or invalid to retain safely.')
  const nextLedger = Object.assign(Object.create(null), ledger, { [workspace]: { payload: frozen } }) as Ledger
  if (!writeLedger(ledger, nextLedger)) throw new Error('This tab could not retain the submission safely. Check the song library before trying again.')
  freeze(frozen)
  const intent: Yue2SubmissionIntent = Object.freeze({ accountEpoch, accountScope: activeScope, workspace,
    requestId: String(frozen.requestId), payload: frozen, phase: 'sending', ambiguous: false })
  intents.set(workspace, intent)
  notify()
  return intent
}

export function retryYue2SubmissionIntent(intent: Yue2SubmissionIntent): Yue2SubmissionIntent | null {
  if (getYue2SubmissionIntent(intent.accountEpoch, intent.workspace) !== intent || intent.phase !== 'unconfirmed') return null
  const next: Yue2SubmissionIntent = Object.freeze({ ...intent, phase: 'sending' })
  intents.set(intent.workspace, next)
  notify()
  return next
}

export function markYue2SubmissionUnconfirmed(intent: Yue2SubmissionIntent): void {
  if (getYue2SubmissionIntent(intent.accountEpoch, intent.workspace) !== intent) return
  intents.set(intent.workspace, Object.freeze({ ...intent, phase: 'unconfirmed', ambiguous: true }))
  notify()
}

export function releaseYue2SubmissionIntent(intent: Yue2SubmissionIntent): void {
  if (getYue2SubmissionIntent(intent.accountEpoch, intent.workspace) !== intent) return
  const ledger = readLedger()
  if (!ledger) return
  const next = Object.assign(Object.create(null), ledger) as Ledger
  delete next[intent.workspace]
  if (!writeLedger(ledger, next)) { markYue2SubmissionUnconfirmed(intent); return }
  intents.delete(intent.workspace)
  notify()
}

export function matchingYue2SubmissionTracks(intent: Yue2SubmissionIntent, tracks: unknown): Yue2Track[] | null {
  if (intent.phase === 'unavailable' || !Array.isArray(tracks)) return null
  const matching = tracks.filter(track => track && typeof track === 'object' && track.requestId === intent.requestId)
  if (matching.length !== 1 || matching.some(track => track.project !== intent.workspace
    || typeof track.id !== 'string' || !track.id || typeof track.title !== 'string'
    || typeof track.status !== 'string' || !track.status || typeof track.stage !== 'string'
    || typeof track.duration !== 'number' || !Number.isFinite(track.duration) || track.duration < 0
    || typeof track.elapsed !== 'number' || !Number.isFinite(track.elapsed) || track.elapsed < 0)) return null
  return matching as Yue2Track[]
}

export function acknowledgedYue2SubmissionTracks(intent: Yue2SubmissionIntent, response: unknown): Yue2Track[] | null {
  if (!response || typeof response !== 'object') return null
  const value = response as { requestId?: unknown; reused?: unknown; tracks?: unknown }
  if (value.requestId !== intent.requestId || typeof value.reused !== 'boolean'
    || !Array.isArray(value.tracks) || value.tracks.length !== 1) return null
  return matchingYue2SubmissionTracks(intent, value.tracks)
}
