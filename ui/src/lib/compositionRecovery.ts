import type { CompositionRecoveryRequest, CompositionRecoveryResult } from '../api/client'

export interface CompositionRecoveryScope {
  accountId: string
  workspace: string
  jobId: string
  createdAt: number
}

export interface CompositionRecoveryHandle {
  scope: CompositionRecoveryScope
  request: CompositionRecoveryRequest
}

interface RecoveryTransport {
  submit: (jobId: string, body: CompositionRecoveryRequest) => Promise<CompositionRecoveryResult>
  get: (jobId: string, requestId: string) => Promise<CompositionRecoveryResult>
}

const UUID4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

function storageKey(scope: CompositionRecoveryScope) {
  return `maestro:composition-recovery:v1:${JSON.stringify(scope)}`
}

export function readCompositionRecoveryHandle(
  scope: CompositionRecoveryScope,
  storage: Pick<Storage, 'getItem'>,
): CompositionRecoveryHandle | null {
  const raw = storage.getItem(storageKey(scope))
  if (raw === null) return null
  if (raw.length > 4096) throw new Error('Saved composition recovery could not be verified.')
  const handle = JSON.parse(raw) as CompositionRecoveryHandle
  if (JSON.stringify(handle.scope) !== JSON.stringify(scope)
    || !UUID4.test(handle.request?.recovery_request_id)
    || handle.request.expected_created_at !== scope.createdAt
    || !Number.isSafeInteger(handle.request.expected_execution_attempt)
    || handle.request.expected_execution_attempt < 1
    || handle.request.confirmed !== true) {
    throw new Error('Saved composition recovery could not be verified.')
  }
  return handle
}

/** Store the exact request before its first POST. An existing handle uses GET
 * only; a fresh confirmation can replace a positively rejected request. */
export async function startCompositionRecovery({
  scope, executionAttempt, storage, transport, current, rejectedRequestId, newRequestId,
}: {
  scope: CompositionRecoveryScope
  executionAttempt: number
  storage: Pick<Storage, 'getItem' | 'setItem'>
  transport: RecoveryTransport
  current: () => boolean
  rejectedRequestId?: string
  newRequestId: () => string
}): Promise<CompositionRecoveryResult> {
  if (!current() || !Number.isSafeInteger(executionAttempt) || executionAttempt < 1
    || !Number.isFinite(scope.createdAt) || scope.createdAt <= 0) {
    throw new Error('This composition changed. Refresh its queue status.')
  }
  const previous = readCompositionRecoveryHandle(scope, storage)
  if (previous) {
    if (previous.request.expected_execution_attempt > executionAttempt) {
      throw new Error('This composition changed. Refresh its queue status.')
    }
    if (previous.request.expected_execution_attempt === executionAttempt) {
      const receipt = await transport.get(scope.jobId, previous.request.recovery_request_id)
      if (!current()) throw new Error('The account or composition changed.')
      if (receipt.status !== 'rejected' || rejectedRequestId !== receipt.recovery_request_id) return receipt
    }
  }
  if (!current()) throw new Error('The account or composition changed.')
  const latest = readCompositionRecoveryHandle(scope, storage)
  if (latest && latest.request.recovery_request_id !== previous?.request.recovery_request_id) {
    // Another click may have saved its intent while the rejection GET was in
    // flight. Adopt that handle rather than overwriting it with a second POST.
    return transport.get(scope.jobId, latest.request.recovery_request_id)
  }
  const request: CompositionRecoveryRequest = {
    recovery_request_id: newRequestId(), expected_created_at: scope.createdAt,
    expected_execution_attempt: executionAttempt, confirmed: true,
  }
  if (!UUID4.test(request.recovery_request_id)) throw new Error('A recovery request could not be reserved.')
  const encoded = JSON.stringify({ scope, request })
  storage.setItem(storageKey(scope), encoded)
  if (storage.getItem(storageKey(scope)) !== encoded) throw new Error('A recovery request could not be saved. Nothing was submitted.')
  let result: CompositionRecoveryResult
  try {
    result = await transport.submit(scope.jobId, request)
  } catch {
    // The POST may have arrived. Reconcile the same logical request, without
    // silently submitting it again or creating another request identifier.
    if (!current()) throw new Error('The account or composition changed.')
    result = await transport.get(scope.jobId, request.recovery_request_id)
  }
  if (!current()) throw new Error('The account or composition changed.')
  return result
}
