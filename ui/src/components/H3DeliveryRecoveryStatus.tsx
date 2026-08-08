import { useEffect, useMemo, useState } from 'react'
import * as api from '../api/client'
import { useStore } from '../stores/useStore'
import {
  announceH3DeliveryRecoveryChange,
  useH3DeliveryRecovery,
} from '../lib/useH3DeliveryRecovery'
import { recoveryGalleryNavigationVerified } from '../lib/h3DeliveryRecoveryContract'

export const OPEN_GALLERY_EVENT = 'maestro:open-gallery'

type RecoveryActionName = 'accept_native' | 'retry_delivery'

export function H3DeliveryRecoveryStatus({
  sourceJobId,
  workspace,
  compact = false,
}: {
  sourceJobId: string
  workspace: string
  compact?: boolean
}) {
  const reconnectJobs = useStore(state => state.reconnectJobs)
  const loadOutputs = useStore(state => state.loadOutputs)
  const switchWorkspace = useStore(state => state.switchWorkspace)
  const activeWorkspace = useStore(state => state.activeWorkspace)
  const jobs = useStore(state => state.jobs)
  const { recovery, loading, error: refreshError, refresh } = useH3DeliveryRecovery(
    sourceJobId,
    workspace,
    Boolean(sourceJobId && workspace),
  )
  const [submitting, setSubmitting] = useState<RecoveryActionName | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const actionCapabilities = useMemo(() => new Map(
    (recovery?.actions || [])
      .filter(action => Boolean(action.action && action.capability))
      .map(action => [action.action, action.capability]),
  ), [recovery?.actions])
  const acceptCapability = actionCapabilities.get('accept_native')
  const retryCapability = actionCapabilities.get('retry_delivery')
  const capabilityRevision = [...actionCapabilities.entries()]
    .map(([action, capability]) => `${action}:${capability}`)
    .join('|')
  const activeChild = recovery?.active_recovery_job_id
    ? jobs.find(job => job.id === recovery.active_recovery_job_id)
    : null
  const retryCount = recovery?.manual_retry_count ?? 0
  const retryLimit = recovery?.manual_retry_limit ?? 0

  useEffect(() => {
    if (recovery?.completed_recovery_job_id && activeWorkspace === workspace) void loadOutputs()
  }, [activeWorkspace, loadOutputs, recovery?.completed_recovery_job_id, workspace])

  useEffect(() => {
    if (recovery?.active_recovery_job_id && !activeChild) void reconnectJobs()
  }, [activeChild, reconnectJobs, recovery?.active_recovery_job_id])

  useEffect(() => {
    setActionError(null)
  }, [capabilityRevision])

  const schedule = async (action: RecoveryActionName, capability: string) => {
    setSubmitting(action)
    setActionError(null)
    try {
      await api.scheduleH3DeliveryRecovery(sourceJobId, action, workspace, capability)
      announceH3DeliveryRecoveryChange(sourceJobId)
      await reconnectJobs()
      await refresh()
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'Recovery could not be queued. Try again.')
      announceH3DeliveryRecoveryChange(sourceJobId)
      await refresh()
    } finally {
      setSubmitting(null)
    }
  }

  const viewInGallery = async () => {
    setActionError(null)
    const initialState = useStore.getState()
    const switchSucceeded = initialState.activeWorkspace === workspace
      && !initialState.browsingUploads
      ? true
      : await switchWorkspace(workspace)
    const switchedState = useStore.getState()
    const outputsLoaded = switchSucceeded
      ? await loadOutputs()
      : false
    const finalState = useStore.getState()
    if (recoveryGalleryNavigationVerified({
      expectedWorkspace: workspace,
      switchSucceeded,
      outputsLoaded,
      activeWorkspace: finalState.activeWorkspace,
      browsingUploads: finalState.browsingUploads,
    })) {
      window.dispatchEvent(new Event(OPEN_GALLERY_EVENT))
      return
    }
    setActionError(
      switchedState.activeWorkspace !== workspace || switchedState.browsingUploads
        ? 'The recovery completed, but its project could not be opened.'
        : 'The recovery completed, but Gallery could not load its outputs.',
    )
  }

  if (!recovery) {
    if (loading) return <p className="text-[10px] text-text-muted">Checking recovery options…</p>
    if (refreshError) return <p className="text-[10px] text-red-300">Recovery status could not be refreshed.</p>
    return null
  }

  return (
    <div className={`rounded-md border border-accent-green/30 bg-bg-primary/30 ${compact ? 'p-2' : 'p-2.5'} text-left`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-text-secondary">
        {recovery.native_available && (
          <span className="font-medium text-accent-green">Native result preserved privately</span>
        )}
        {recovery.recoverable && <span>Failed · recoverable</span>}
        {recovery.requested_target && <span>Target {recovery.requested_target}</span>}
        {recovery.manual_retry_count != null && recovery.manual_retry_limit != null && (
          <span>Delivery retries {retryCount}/{retryLimit}</span>
        )}
        {typeof recovery.restart_supported === 'boolean' && (
          <span>
            {recovery.restart_supported
              ? 'Recovery survives an app restart'
              : 'Recovery is limited to this app session'}
          </span>
        )}
      </div>

      {recovery.active_recovery_job_id && (
        <p className="mt-1.5 text-[10px] text-accent-blue">
          Recovery child is {activeChild?.status === 'running' ? 'running' : 'queued'} in the generation queue.
        </p>
      )}
      {recovery.completed_recovery_job_id && (
        <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2">
          <p className="text-[10px] text-accent-green">Recovery child completed; the result is available in Gallery.</p>
          <button
            type="button"
            onClick={() => void viewInGallery()}
            className="rounded border border-accent-green/30 bg-accent-green/10 px-2 py-1 text-[10px] text-accent-green hover:bg-accent-green/20"
          >
            {activeWorkspace === workspace ? 'View in Gallery' : 'Open project in Gallery'}
          </button>
        </div>
      )}

      {!recovery.active_recovery_job_id && !recovery.completed_recovery_job_id && (
        <>
          <p className="mt-1.5 text-[10px] text-text-muted">
            Retry delivery reuses the preserved native result. It does not rerun generation or denoise and does not change machine settings.
          </p>
          {(acceptCapability || retryCapability) && (
            <div className="mt-2 flex flex-wrap gap-2">
              {acceptCapability && (
                <button
                  type="button"
                  disabled={submitting !== null}
                  onClick={() => void schedule('accept_native', acceptCapability)}
                  className="rounded bg-accent-green px-3 py-1.5 text-[11px] font-medium text-white hover:opacity-90 disabled:cursor-wait disabled:opacity-50"
                >
                  {submitting === 'accept_native' ? 'Queuing native result…' : 'Use native result'}
                </button>
              )}
              {retryCapability && (
                <button
                  type="button"
                  disabled={submitting !== null}
                  onClick={() => void schedule('retry_delivery', retryCapability)}
                  className="rounded border border-accent-blue/40 bg-accent-blue/10 px-3 py-1.5 text-[11px] font-medium text-accent-blue hover:bg-accent-blue/20 disabled:cursor-wait disabled:opacity-50"
                >
                  {submitting === 'retry_delivery' ? 'Queuing delivery…' : 'Retry delivery only'}
                </button>
              )}
            </div>
          )}
          {!retryCapability && retryLimit > 0 && retryCount >= retryLimit && (
            <p className="mt-1.5 text-[10px] text-text-muted">Delivery retry limit reached. The preserved native result can still be used.</p>
          )}
        </>
      )}

      {(actionError || refreshError) && (
        <p className="mt-1.5 text-[10px] text-red-300">{actionError || refreshError}</p>
      )}
    </div>
  )
}
