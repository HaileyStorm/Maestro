import type { Yue2TrainingJob, Yue2TrainingRequest } from '../../api/client'
import { currentAccountIdentityEpoch, useStore } from '../../stores/useStore'
import { Yue2RequestLedger, yue2SubmissionScope } from '../../lib/yue2SubmissionIntent'

/** Retain exact uncertain training inputs in this tab, isolated by account and project. */
export class Yue2TrainingAttempts {
  private pending = new Map<string, { request: Yue2TrainingRequest; ambiguous: boolean }>()
  private ledger = new Yue2RequestLedger('training')
  private epoch: number
  private scope: string | null
  private readEpoch: () => number

  constructor(readEpoch: () => number = currentAccountIdentityEpoch) {
    this.readEpoch = readEpoch
    this.epoch = readEpoch()
    this.scope = yue2SubmissionScope()
  }

  prune(): void {
    const current = this.readEpoch()
    const scope = yue2SubmissionScope()
    if (current === this.epoch && scope === this.scope) return
    this.pending.clear()
    this.epoch = current
    this.scope = scope
  }

  unavailable(accountEpoch: number): boolean {
    if (accountEpoch !== this.readEpoch()) return true
    this.prune()
    return this.ledger.read(this.scope) === null
  }

  current(accountEpoch: number, workspace: string): Yue2TrainingRequest | undefined {
    if (accountEpoch !== this.readEpoch()) return
    this.prune()
    const ledger = this.ledger.read(this.scope)
    if (!ledger) { this.pending.clear(); return }
    const entry = Object.hasOwn(ledger, workspace) ? ledger[workspace] : null
    if (!entry) { this.pending.delete(workspace); return }
    const previous = this.pending.get(workspace)
    if (previous && JSON.stringify(previous.request) === JSON.stringify(entry.payload)) return previous.request
    const request = this.ledger.payload(entry) as unknown as Yue2TrainingRequest
    this.pending.set(workspace, { request, ambiguous: true })
    return request
  }

  next(accountEpoch: number, workspace: string, create: () => Yue2TrainingRequest): Yue2TrainingRequest | undefined {
    if (accountEpoch !== this.readEpoch()) return
    const previous = this.current(accountEpoch, workspace)
    if (previous) {
      // A repeat can overlap a still-pending POST retained through navigation or reload.
      this.pending.get(workspace)!.ambiguous = true
      return previous
    }
    const request = this.ledger.reserve(this.scope, workspace, create() as unknown as Record<string, unknown>) as unknown as Yue2TrainingRequest
    this.pending.set(workspace, { request, ambiguous: false })
    return request
  }

  ambiguous(accountEpoch: number, workspace: string, requestId: string): boolean {
    if (!this.current(accountEpoch, workspace)) return false
    const pending = this.pending.get(workspace)
    return pending?.request.requestId === requestId && pending.ambiguous === true
  }

  markAmbiguous(accountEpoch: number, workspace: string, requestId: string): void {
    if (this.current(accountEpoch, workspace)?.requestId === requestId) this.pending.get(workspace)!.ambiguous = true
  }

  clear(accountEpoch: number, workspace: string, requestId: string): void {
    if (this.current(accountEpoch, workspace)?.requestId !== requestId) return
    const ledger = this.ledger.read(this.scope)
    if (!ledger) return
    const next = Object.assign(Object.create(null), ledger)
    delete next[workspace]
    if (!this.ledger.write(this.scope, ledger, next)) { this.markAmbiguous(accountEpoch, workspace, requestId); return }
    this.pending.delete(workspace)
  }
}

export function matchingYue2TrainingJob(request: Yue2TrainingRequest, value: unknown): Yue2TrainingJob | null {
  if (!value || typeof value !== 'object') return null
  const job = value as Yue2TrainingJob
  if (job.id !== request.requestId || job.project !== request.workspace
    || typeof job.name !== 'string' || !['artist', 'style'].includes(job.kind)
    || typeof job.trigger !== 'string' || typeof job.stage !== 'string'
    || !['queued', 'preparing', 'waiting-for-resource', 'running', 'succeeded', 'failed', 'cancelled'].includes(job.state)
    || !Array.isArray(job.sourceTakeIds) || !job.sourceTakeIds.every(id => typeof id === 'string')
    || ![0, 1].includes(job.cancel_requested)) return null
  return job
}

// Account changes retire visible inputs; returning to that account restores its unresolved receipts.
export const yue2TrainingAttempts = new Yue2TrainingAttempts()
useStore.subscribe(() => yue2TrainingAttempts.prune())
