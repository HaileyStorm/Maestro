import { useEffect, useRef, useState } from 'react'
import * as api from '../api/client'
import { currentAccountIdentityEpoch, useStore } from '../stores/useStore'
import { readCompositionRecoveryHandle, startCompositionRecovery, type CompositionRecoveryScope } from '../lib/compositionRecovery'

export function CompositionRecoveryStatus({ jobId, workspace, createdAt }: {
  jobId: string
  workspace: string
  createdAt?: number
}) {
  const accountId = useStore(state => {
    const context = state.accountContext ?? state.accessContext?.accounts
    return context?.authenticated && context.account ? context.account.id
      : context?.enabled === false ? 'local-owner' : ''
  })
  const reconnectJobs = useStore(state => state.reconnectJobs)
  const identity = JSON.stringify([accountId, workspace, jobId, createdAt])
  const lifecycle = useRef({ identity, generation: 0, mounted: false })
  if (lifecycle.current.identity !== identity) {
    lifecycle.current = { identity, generation: lifecycle.current.generation + 1, mounted: false }
  }
  const [confirmAttempt, setConfirmAttempt] = useState<number | null>(null)
  const [receipt, setReceipt] = useState<api.CompositionRecoveryResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hasRequest, setHasRequest] = useState(false)

  const scope: CompositionRecoveryScope | null = accountId && Number.isFinite(createdAt) && Number(createdAt) > 0
    ? { accountId, workspace, jobId, createdAt: Number(createdAt) } : null
  const current = (epoch: number, generation: number) => lifecycle.current.mounted
    && lifecycle.current.identity === identity && lifecycle.current.generation === generation
    && currentAccountIdentityEpoch() === epoch

  useEffect(() => {
    lifecycle.current.mounted = true
    const generation = ++lifecycle.current.generation
    const epoch = currentAccountIdentityEpoch()
    setConfirmAttempt(null)
    setReceipt(null)
    setError(null)
    setBusy(false)
    setHasRequest(false)
    if (scope) try {
      const handle = readCompositionRecoveryHandle(scope, sessionStorage)
      if (handle) {
        setHasRequest(true)
        void api.fetchCompositionRecovery(jobId, handle.request.recovery_request_id).then(value => {
          if (current(epoch, generation)) setReceipt(value)
        }).catch(() => {
          if (current(epoch, generation)) setError('The earlier request is still unverified. Check its status before starting another attempt.')
        })
      }
    } catch {
      setHasRequest(true)
      setError('Saved recovery could not be verified. No new attempt will be submitted.')
    }
    return () => {
      lifecycle.current.mounted = false
      lifecycle.current.generation++
    }
  // The scope contains only the fields encoded in identity.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity])

  const check = async () => {
    if (!scope || busy) return
    const epoch = currentAccountIdentityEpoch()
    const generation = lifecycle.current.generation
    setBusy(true)
    setError(null)
    try {
      const handle = readCompositionRecoveryHandle(scope, sessionStorage)
      if (!handle) throw new Error('No saved recovery request is available.')
      const value = await api.fetchCompositionRecovery(jobId, handle.request.recovery_request_id)
      if (!current(epoch, generation)) return
      setReceipt(value)
      if (value.status === 'accepted') await reconnectJobs(epoch)
    } catch (reason) {
      if (current(epoch, generation)) setError(reason instanceof Error ? reason.message : 'Recovery could not be checked.')
    } finally {
      if (current(epoch, generation)) setBusy(false)
    }
  }

  const review = async () => {
    if (!scope || busy || (hasRequest && receipt?.status !== 'rejected' && receipt?.status !== 'accepted')) return
    const epoch = currentAccountIdentityEpoch()
    const generation = lifecycle.current.generation
    setBusy(true)
    setError(null)
    try {
      const job = await api.fetchJobStatus(jobId)
      if (!current(epoch, generation)) return
      const attempt = job.composition_recovery_execution_attempt
      if (job.created_at !== createdAt || job.workspace !== workspace
        || !job.recovery_actions?.includes('recover_composition')
        || !Number.isSafeInteger(attempt) || Number(attempt) < 1) {
        throw new Error('Recovery is no longer available for this composition. Refresh the queue.')
      }
      const previous = readCompositionRecoveryHandle(scope, sessionStorage)
      if (previous && receipt?.status === 'accepted' && Number(attempt) <= previous.request.expected_execution_attempt) {
        throw new Error('The earlier recovery was accepted. Check its status and refresh the queue before starting another attempt.')
      }
      setConfirmAttempt(Number(attempt))
    } catch (reason) {
      if (current(epoch, generation)) setError(reason instanceof Error ? reason.message : 'Recovery could not be reviewed.')
    } finally {
      if (current(epoch, generation)) setBusy(false)
    }
  }

  const confirm = async () => {
    if (!scope || confirmAttempt === null || busy) return
    const epoch = currentAccountIdentityEpoch()
    const generation = lifecycle.current.generation
    setBusy(true)
    setError(null)
    setConfirmAttempt(null)
    try {
      const value = await startCompositionRecovery({
        scope, executionAttempt: confirmAttempt, storage: sessionStorage,
        transport: { submit: api.submitCompositionRecovery, get: api.fetchCompositionRecovery },
        current: () => current(epoch, generation), rejectedRequestId: receipt?.status === 'rejected' ? receipt.recovery_request_id : undefined,
        newRequestId: api.createLlmRequestId,
      })
      if (!current(epoch, generation)) return
      setHasRequest(true)
      setReceipt(value)
      if (value.status === 'accepted') await reconnectJobs(epoch)
    } catch (reason) {
      if (!current(epoch, generation)) return
      try { setHasRequest(Boolean(readCompositionRecoveryHandle(scope, sessionStorage))) } catch { setHasRequest(true) }
      setError(reason instanceof Error ? reason.message : 'Recovery is unverified. Check its status before trying again.')
    } finally {
      if (current(epoch, generation)) setBusy(false)
    }
  }

  return (
    <div className="mt-2 rounded border border-amber-300/25 bg-bg-primary/30 p-2 text-left text-[10px]" data-composition-recovery>
      {confirmAttempt !== null ? <>
        <p className="text-text-secondary">Render the unfinished segments again? Completed segments will be verified and reused. The interrupted attempt will stay saved privately.</p>
        <p className="mt-1 text-text-muted">Maestro will first check that the previous render has stopped. Recovery stays held if that cannot be verified.</p>
        <div className="mt-2 flex gap-2">
          <button type="button" onClick={() => void confirm()} disabled={busy} className="rounded bg-amber-300/15 px-2.5 py-1 font-medium text-amber-200">Confirm recovery</button>
          <button type="button" onClick={() => setConfirmAttempt(null)} disabled={busy} className="rounded border border-border px-2.5 py-1 text-text-secondary">Cancel</button>
        </div>
      </> : <>
        {receipt && <p className="text-text-secondary">{receipt.message}</p>}
        {hasRequest && <button type="button" onClick={() => void check()} disabled={busy || !scope} className="mt-1 rounded bg-amber-300/15 px-2.5 py-1 text-amber-200">{busy ? 'Checking recovery…' : 'Check recovery status'}</button>}
        {(!hasRequest || receipt?.status === 'rejected' || receipt?.status === 'accepted') && <button type="button" onClick={() => void review()} disabled={busy || !scope} className="mt-1 rounded bg-amber-300/15 px-2.5 py-1 text-amber-200">{busy ? 'Checking composition…' : 'Review composition recovery'}</button>}
      </>}
      {error && <p role="alert" className="mt-1 text-red-300">{error}</p>}
    </div>
  )
}
