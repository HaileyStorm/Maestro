import { SubmissionLedger } from './submissionLedger'
import type { DirectorQueueState } from '../api/client'

export type DirectorQueueAdmissionView = {
  phase: 'preparing' | 'sending' | 'unconfirmed' | 'unavailable'
  checking: boolean
  message: string
}
export type DirectorQueueAdmission = Record<string, unknown> & {
  requestId: string
  workspace: string
  projectInstance: string
  params: Record<string, unknown>
}

/** One unresolved admission per project in this tab; no store import or auto retry. */
export class DirectorQueueAdmissionLedger extends SubmissionLedger {
  constructor(currentScope: () => string | null) {
    super('maestro:director-queue-admissions-v1:', currentScope)
  }

  current(scope: string | null, workspace: string): DirectorQueueAdmission | null | undefined {
    const ledger = this.read(scope)
    if (!ledger) return undefined
    if (!Object.hasOwn(ledger, workspace)) return null
    const payload = this.payload(ledger[workspace])
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(String(payload.requestId))
      || typeof payload.projectInstance !== 'string' || !/^project:v1:[a-f0-9]{64}$/.test(payload.projectInstance)
      || !payload.params || typeof payload.params !== 'object' || Array.isArray(payload.params)
      || (payload.params as Record<string, unknown>).workspace !== workspace) return undefined
    return payload as DirectorQueueAdmission
  }

  settle(scope: string | null, intent: DirectorQueueAdmission): boolean {
    const ledger = this.read(scope)
    if (!ledger || !Object.hasOwn(ledger, intent.workspace)
      || JSON.stringify(ledger[intent.workspace].payload) !== JSON.stringify(intent)) return false
    const next = Object.assign(Object.create(null), ledger)
    delete next[intent.workspace]
    return this.write(scope, ledger, next)
  }
}

/** Exact receipt and consistent entry presence are required, including removal tombstones. */
export function confirmedDirectorAdmission(
  intent: DirectorQueueAdmission, value: unknown, acknowledgement = false,
): { queue: DirectorQueueState; removed: boolean } | null {
  if (!value || typeof value !== 'object') return null
  const queue = value as DirectorQueueState
  if (queue.project_instance !== intent.projectInstance || !Array.isArray(queue.entries) || !Array.isArray(queue.admissions)) return null
  const matching = queue.admissions.filter(item => item?.request_id === intent.requestId)
  if (matching.length !== 1) return null
  const receipt = matching[0]
  if (!/^[a-f0-9]{8}$/.test(receipt.entry_id) || typeof receipt.removed !== 'boolean') return null
  const entries = queue.entries.filter(item => item?.id === receipt.entry_id)
  if (receipt.removed ? entries.length !== 0 : entries.length !== 1 || typeof entries[0].status !== 'string') return null
  if (acknowledgement) {
    const ack = queue.admission
    if (!ack || ack.request_id !== intent.requestId || ack.entry_id !== receipt.entry_id
      || ack.removed !== receipt.removed || typeof ack.reused !== 'boolean') return null
  }
  return { queue, removed: receipt.removed }
}
