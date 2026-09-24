import type { Yue2TrainingRequest } from '../../api/client'

/** Keep an uncertain POST's exact inputs and ID across retries and project switches. */
export class Yue2TrainingAttempts {
  private pending = new Map<string, Yue2TrainingRequest>()

  current(workspace: string): Yue2TrainingRequest | undefined {
    return this.pending.get(workspace)
  }

  next(workspace: string, create: () => Yue2TrainingRequest): Yue2TrainingRequest {
    const previous = this.pending.get(workspace)
    if (previous) return previous
    const request = create()
    this.pending.set(workspace, request)
    return request
  }

  clear(workspace: string, requestId: string): void {
    if (this.pending.get(workspace)?.requestId === requestId) this.pending.delete(workspace)
  }
}

// YuE2 controls can unmount when the user changes projects or music engines.
export const yue2TrainingAttempts = new Yue2TrainingAttempts()
