import type { Yue2TrainingJob, Yue2TrainingRequest } from '../../api/client'
import { currentAccountIdentityEpoch, useStore } from '../../stores/useStore'

/** Retain exact uncertain inputs only within the current account and project. */
export class Yue2TrainingAttempts {
  private pending = new Map<string, { request: Yue2TrainingRequest; ambiguous: boolean }>()
  private epoch: number
  private readEpoch: () => number

  constructor(readEpoch: () => number = currentAccountIdentityEpoch) {
    this.readEpoch = readEpoch
    this.epoch = readEpoch()
  }

  prune(): void {
    const current = this.readEpoch()
    if (current === this.epoch) return
    this.pending.clear()
    this.epoch = current
  }

  current(accountEpoch: number, workspace: string): Yue2TrainingRequest | undefined {
    if (accountEpoch !== this.readEpoch()) return
    this.prune()
    return this.pending.get(workspace)?.request
  }

  next(accountEpoch: number, workspace: string, create: () => Yue2TrainingRequest): Yue2TrainingRequest | undefined {
    if (accountEpoch !== this.readEpoch()) return
    const previous = this.current(accountEpoch, workspace)
    if (previous) {
      // A repeat can overlap a still-pending POST retained through SPA navigation.
      this.pending.get(workspace)!.ambiguous = true
      return previous
    }
    const request = JSON.parse(JSON.stringify(create())) as Yue2TrainingRequest
    request.tracks.forEach(Object.freeze)
    Object.freeze(request.tracks)
    Object.freeze(request)
    this.pending.set(workspace, { request, ambiguous: false })
    return request
  }

  ambiguous(accountEpoch: number, workspace: string, requestId: string): boolean {
    if (!this.current(accountEpoch, workspace)) return false
    const pending = this.pending.get(workspace)
    return pending?.request.requestId === requestId && pending.ambiguous === true
  }

  markAmbiguous(accountEpoch: number, workspace: string, requestId: string): void {
    if (this.current(accountEpoch, workspace)?.requestId === requestId) {
      this.pending.get(workspace)!.ambiguous = true
    }
  }

  clear(accountEpoch: number, workspace: string, requestId: string): void {
    if (this.current(accountEpoch, workspace)?.requestId === requestId) this.pending.delete(workspace)
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

// Account changes must discard private attempts even while the controls are unmounted.
export const yue2TrainingAttempts = new Yue2TrainingAttempts()
useStore.subscribe(() => yue2TrainingAttempts.prune())
