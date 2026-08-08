import { useState, useEffect, useMemo, useCallback } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { useStore } from '../stores/useStore'
import type { OomInfo } from '../types'
import { H3DeliveryRecoveryStatus } from './H3DeliveryRecoveryStatus'
import { selectRecoverySourceIndex } from '../lib/h3DeliveryRecoveryContract'

/** Surface structured recovery state for generation and H3 delivery OOMs. */
export function OomRecoveryBanner() {
  const jobs = useStore(s => s.jobs)
  const pipelineStatus = useStore(s => s.pipelineStatus)
  const systemDetect = useStore(s => s.systemDetect)
  const updateSystemConfig = useStore(s => s.updateSystemConfig)
  const machineControls = useStore(s => s.accessContext?.machine_controls === true)
  // Once user dismisses a specific failure, suppress the banner for that
  // failure key until this component is remounted. Failed cards themselves
  // may be restored by the normal job reconnection flow.
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const [applying, setApplying] = useState(false)
  const [appliedToast, setAppliedToast] = useState<string | null>(null)

  // Find the most relevant OOM failure to show. Studio jobs and the
  // pipeline are independent surfaces — if both have OOM failures
  // (rare), prefer the most recent source failure using its server creation
  // time with stable newest-first list order as fallback. Pipeline takes
  // precedence because Director runs are typically
  // longer (more lost work, more user attention), so it's more likely
  // to be the active concern.
  const activeOom: {
    key: string
    oom: OomInfo
    context: string
    sourceJobId?: string
    workspace?: string
  } | null = useMemo(() => {
    if (pipelineStatus?.status === 'failed' && pipelineStatus.oom_info) {
      const failedClipIdx = pipelineStatus.clip_images?.length ?? 0
      const totalClips = pipelineStatus.clip_plans?.length ?? 0
      const context = totalClips > 0
        ? `Director pipeline failed on clip ${failedClipIdx + 1} of ${totalClips}.`
        : 'Director pipeline failed.'
      return { key: `pipeline:${pipelineStatus.id}`, oom: pipelineStatus.oom_info, context }
    }
    // Latest failed studio job with oom_info
    const failedOomJobs = jobs.filter(j => j.status === 'failed' && j.oomInfo)
    const sourceIndex = selectRecoverySourceIndex(failedOomJobs.map(job => ({
      createdAt: job.createdAt,
      manualRetryCount: job.oomInfo?.stage === 'h3_delivery'
        ? job.oomInfo.manual_retry_count
        : undefined,
    })))
    const failedJob = sourceIndex >= 0 ? failedOomJobs[sourceIndex] : undefined
    if (failedJob && failedJob.oomInfo) {
      return {
        key: `job:${failedJob.id}`,
        oom: failedJob.oomInfo,
        context: failedJob.oomInfo.stage === 'h3_delivery'
          ? failedJob.oomInfo.manual_retry_count != null
            ? 'A delivery-only recovery attempt failed.'
            : 'Native generation completed; exact delivery failed.'
          : 'Generation failed.',
        sourceJobId: failedJob.oomInfo.manual_retry_count == null ? failedJob.id : undefined,
        workspace: failedJob.oomInfo.manual_retry_count == null ? failedJob.workspace : undefined,
      }
    }
    return null
  }, [jobs, pipelineStatus])

  // Auto-clear the toast after 3 seconds.
  useEffect(() => {
    if (!appliedToast) return
    const t = setTimeout(() => setAppliedToast(null), 3000)
    return () => clearTimeout(t)
  }, [appliedToast])

  const handleDismiss = useCallback(() => {
    if (activeOom) {
      setDismissed(d => new Set(d).add(activeOom.key))
    }
  }, [activeOom])

  const handleApply = useCallback(async () => {
    if (!activeOom?.oom.suggested_coefficient) return
    setApplying(true)
    try {
      await updateSystemConfig({ vram_safety_coefficient: activeOom.oom.suggested_coefficient })
      setAppliedToast(`VRAM headroom lowered to ${activeOom.oom.suggested_coefficient.toFixed(2)} — try the generation again`)
      setDismissed(d => new Set(d).add(activeOom.key))
    } catch (e) {
      console.error('apply coefficient failed:', e)
    } finally {
      setApplying(false)
    }
  }, [activeOom, updateSystemConfig])

  // Toast stays visible for 3s after apply; banner hides as soon as
  // it's dismissed (the OOM key is in the dismissed set).
  if (appliedToast) {
    return (
      <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 max-w-md">
        <div className="bg-green-500/90 text-white text-sm rounded-lg shadow-xl px-4 py-2.5">
          {appliedToast}
        </div>
      </div>
    )
  }

  if (!activeOom || dismissed.has(activeOom.key)) return null

  const { oom, context } = activeOom
  const isDeliveryOom = oom.stage === 'h3_delivery'
  const vramGb = systemDetect?.hardware?.gpu_vram_gb
  const vramHint = machineControls && vramGb ? `Your GPU has ${vramGb} GB VRAM.` : ''
  const canLower = !isDeliveryOom && machineControls && oom.suggested_coefficient !== null
  const deliveryTarget = oom.requested_target ? `Exact ${oom.requested_target} delivery` : 'Exact delivery'
  const retriedDelivery = (oom.retry_count ?? 0) > 0

  return (
    <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 max-w-xl w-[calc(100%-2rem)]">
      <div className="bg-bg-secondary border border-amber-500/40 rounded-lg shadow-2xl overflow-hidden">
        {/* Header strip */}
        <div className="flex items-start gap-2.5 px-4 py-3 bg-amber-500/10">
          <AlertTriangle size={18} className="text-indicator-warning shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium text-text-primary">
              {isDeliveryOom ? 'Delivery ran out of VRAM' : 'Generation ran out of VRAM'}
            </div>
            <div className="text-[12px] text-text-secondary mt-0.5">
              {context} {vramHint}
            </div>
          </div>
          <button
            onClick={handleDismiss}
            className="text-text-muted hover:text-text-primary p-0.5 rounded transition-colors shrink-0"
            title="Dismiss"
          >
            <X size={14} />
          </button>
        </div>

        {/* Body */}
        <div className="px-4 py-3 space-y-2.5">
          {isDeliveryOom && (
            <div className={`rounded-md border px-3 py-2 text-[12px] leading-snug ${
              oom.native_available
                ? 'border-accent-green/30 bg-accent-green/10 text-text-secondary'
                : 'border-red-500/30 bg-red-500/10 text-text-secondary'
            }`}>
              {oom.native_available ? (
                <>
                  <span className="font-medium text-accent-green">Native generation succeeded.</span>{' '}
                  Maestro preserved the native result privately when {deliveryTarget.toLowerCase()} ran out of VRAM
                  {retriedDelivery ? ' again after one automatic identical retry.' : '.'}
                </>
              ) : (
                <>{deliveryTarget} ran out of VRAM, and no preserved native result is available.</>
              )}
            </div>
          )}

          {isDeliveryOom && activeOom.sourceJobId && activeOom.workspace && (
            <H3DeliveryRecoveryStatus
              sourceJobId={activeOom.sourceJobId}
              workspace={activeOom.workspace}
            />
          )}

          {canLower ? (
            <>
              <div className="text-[12px] text-text-secondary leading-snug">
                Lower VRAM headroom from <span className="font-mono text-text-primary">{oom.current_coefficient.toFixed(2)}</span> to{' '}
                <span className="font-mono text-indicator-warning">{oom.suggested_coefficient!.toFixed(2)}</span> to reserve more memory for generation spikes
                (long videos, VAE decode). About ~5% slower per generation.
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleApply}
                  disabled={applying}
                  className="flex-1 px-3 py-2 rounded-md bg-amber-500 hover:bg-amber-400 text-white text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-wait"
                >
                  {applying ? 'Applying...' : `Lower headroom to ${oom.suggested_coefficient!.toFixed(2)}`}
                </button>
                <button
                  onClick={handleDismiss}
                  className="px-3 py-2 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-tertiary text-sm transition-colors"
                >
                  Dismiss
                </button>
              </div>
              <div className="text-[10px] text-text-muted">
                After applying, re-run the generation — it'll use the new headroom on next model load.
              </div>
            </>
          ) : machineControls && !isDeliveryOom ? (
            <>
              <div className="text-[12px] text-text-secondary leading-snug">
                VRAM headroom is already at <span className="font-mono">{oom.current_coefficient.toFixed(2)}</span> (the safe minimum).
                Lowering it further won't help. Try a smaller model variant (e.g. INT8 or GGUF), reduce resolution, or shorten video length.
              </div>
              <div className="flex justify-end">
                <button
                  onClick={handleDismiss}
                  className="px-3 py-2 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-tertiary text-sm transition-colors"
                >
                  Dismiss
                </button>
              </div>
            </>
          ) : (
            <div className="flex justify-end">
              <button
                onClick={handleDismiss}
                className="px-3 py-2 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-tertiary text-sm transition-colors"
              >
                Dismiss
              </button>
            </div>
          )}

          {machineControls && (
            <details className="text-[10px] text-text-muted">
              <summary className="cursor-pointer hover:text-text-secondary">Show error details</summary>
              <div className="mt-1 font-mono text-[10px] bg-bg-primary/40 rounded px-2 py-1.5 break-all">
                {oom.message}
              </div>
            </details>
          )}
        </div>
      </div>
    </div>
  )
}
