import type { Yue2Track } from '../api/client'
import { currentAccountIdentityEpoch, useStore } from '../stores/useStore'

export type Yue2SubmissionIntent = Readonly<{
  accountEpoch: number
  workspace: string
  requestId: string
  payload: Record<string, unknown>
  phase: 'sending' | 'unconfirmed'
  ambiguous: boolean
}>

// SPA memory only. Unresolved submissions are never evicted to make room.
const MAX_INTENTS = 16
const intents = new Map<string, Yue2SubmissionIntent>()
const listeners = new Set<() => void>()
let activeEpoch: number | null = null
const notify = () => { for (const listener of listeners) listener() }

export function pruneYue2SubmissionIntents(accountEpoch: number): void {
  if (activeEpoch === accountEpoch) return
  activeEpoch = accountEpoch
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
  return activeEpoch === accountEpoch ? intents.get(workspace) ?? null : null
}

function freeze(value: unknown): void {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) freeze(child)
    Object.freeze(value)
  }
}

export function reserveYue2SubmissionIntent(accountEpoch: number, workspace: string, payload: Record<string, unknown>): Yue2SubmissionIntent {
  pruneYue2SubmissionIntents(accountEpoch)
  if (intents.has(workspace)) throw new Error('This project already has a submission awaiting confirmation.')
  if (intents.size >= MAX_INTENTS) throw new Error('Confirm an unresolved YuE2 submission in another project before starting another.')
  const frozen = JSON.parse(JSON.stringify(payload)) as Record<string, unknown>
  freeze(frozen)
  const intent: Yue2SubmissionIntent = Object.freeze({ accountEpoch, workspace,
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
  intents.delete(intent.workspace)
  notify()
}

export function matchingYue2SubmissionTracks(intent: Yue2SubmissionIntent, tracks: unknown): Yue2Track[] | null {
  if (!Array.isArray(tracks)) return null
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
