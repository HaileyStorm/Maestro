import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { useStore } from '../stores/useStore'
import type { OomInfo } from '../types'
import { H3DeliveryRecoveryStatus } from './H3DeliveryRecoveryStatus'
import { selectRecoverySourceIndex } from '../lib/h3DeliveryRecoveryContract'

const safeViewportPadding = {
  paddingTop: 'max(1rem, env(safe-area-inset-top, 0px))',
  paddingRight: 'max(1rem, env(safe-area-inset-right, 0px))',
  paddingBottom: 'max(1rem, env(safe-area-inset-bottom, 0px))',
  paddingLeft: 'max(1rem, env(safe-area-inset-left, 0px))',
}

const APPLY_FAILURE_MESSAGE = 'System settings could not be updated. Check the connection and try again.'

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
  const [applyingKey, setApplyingKey] = useState<string | null>(null)
  const [applyError, setApplyError] = useState<{ key: string; message: string } | null>(null)
  const [appliedToast, setAppliedToast] = useState<{ key: string; message: string } | null>(null)

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
            ? 'The delivery retry failed.'
            : 'Generation finished, but creating the requested output failed.'
          : 'Generation failed.',
        sourceJobId: failedJob.oomInfo.manual_retry_count == null ? failedJob.id : undefined,
        workspace: failedJob.oomInfo.manual_retry_count == null ? failedJob.workspace : undefined,
      }
    }
    return null
  }, [jobs, pipelineStatus])
  const activeOomKey = activeOom?.key ?? null
  const applying = applyingKey === activeOomKey
  const mountedRef = useRef(false)
  const applySequenceRef = useRef(0)
  const applyingRef = useRef(false)
  const applyAbortControllerRef = useRef<AbortController | null>(null)
  const activeOomKeyRef = useRef<string | null>(activeOomKey)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      applyingRef.current = false
      applyAbortControllerRef.current?.abort()
      applyAbortControllerRef.current = null
      applySequenceRef.current += 1
    }
  }, [])

  useEffect(() => {
    activeOomKeyRef.current = activeOomKey
    applySequenceRef.current += 1
    applyingRef.current = false
    applyAbortControllerRef.current?.abort()
    applyAbortControllerRef.current = null
    setApplyingKey(current => current === activeOomKey ? current : null)
    setApplyError(current => current?.key === activeOomKey ? current : null)
    setAppliedToast(current => current?.key === activeOomKey ? current : null)
  }, [activeOomKey])

  // Auto-clear the toast after 3 seconds.
  useEffect(() => {
    if (!appliedToast) return
    const t = setTimeout(() => setAppliedToast(null), 3000)
    return () => clearTimeout(t)
  }, [appliedToast])

  const handleDismiss = useCallback(() => {
    if (activeOom) {
      applySequenceRef.current += 1
      applyingRef.current = false
      applyAbortControllerRef.current?.abort()
      applyAbortControllerRef.current = null
      setApplyingKey(current => current === activeOom.key ? null : current)
      setApplyError(current => current?.key === activeOom.key ? null : current)
      setAppliedToast(current => current?.key === activeOom.key ? null : current)
      setDismissed(d => new Set(d).add(activeOom.key))
    }
  }, [activeOom])

  const handleApply = useCallback(async () => {
    const suggestedCoefficient = activeOom?.oom.suggested_coefficient
    if (!activeOom || suggestedCoefficient == null || applyingRef.current) return
    const oomKey = activeOom.key
    const sequence = ++applySequenceRef.current
    const controller = new AbortController()
    applyAbortControllerRef.current?.abort()
    applyAbortControllerRef.current = controller
    applyingRef.current = true
    setApplyingKey(oomKey)
    const isCurrent = () => (
      mountedRef.current
      && applySequenceRef.current === sequence
      && activeOomKeyRef.current === oomKey
    )
    try {
      const result = await updateSystemConfig(
        { vram_safety_coefficient: suggestedCoefficient },
        controller.signal,
      )
      if (!isCurrent()) return
      if (!result.ok) {
        setApplyError({ key: oomKey, message: result.message })
        return
      }
      setApplyError(null)
      setAppliedToast({
        key: oomKey,
        message: `GPU memory setting changed to ${Math.round(suggestedCoefficient * 100)}% — try the generation again`,
      })
      setDismissed(d => new Set(d).add(oomKey))
    } catch {
      if (isCurrent()) setApplyError({ key: oomKey, message: APPLY_FAILURE_MESSAGE })
    } finally {
      if (isCurrent()) {
        applyingRef.current = false
        setApplyingKey(current => current === oomKey ? null : current)
      }
      if (applyAbortControllerRef.current === controller) {
        applyAbortControllerRef.current = null
      }
    }
  }, [activeOom, updateSystemConfig])

  const visibleAppliedToast = appliedToast?.key === activeOomKey ? appliedToast.message : null
  const visibleApplyError = applyError?.key === activeOomKey ? applyError.message : null

  // Toast stays visible for 3s after apply; banner hides as soon as
  // it's dismissed (the OOM key is in the dismissed set).
  if (visibleAppliedToast) {
    return (
      <div
        className="pointer-events-none fixed inset-0 z-[60] flex max-h-[100vh] items-start justify-center supports-[height:100dvh]:max-h-[100dvh]"
        style={safeViewportPadding}
      >
        <div
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className="pointer-events-auto max-h-full max-w-md overflow-y-auto overscroll-contain rounded-lg bg-green-500/90 px-4 py-2.5 text-sm text-white shadow-xl"
        >
          {visibleAppliedToast}
        </div>
      </div>
    )
  }

  if (!activeOom || dismissed.has(activeOom.key)) return null

  const { oom, context } = activeOom
  const isDeliveryOom = oom.stage === 'h3_delivery'
  const vramGb = systemDetect?.hardware?.gpu_vram_gb
  const vramHint = machineControls && vramGb ? `Your GPU has ${vramGb} GB of memory.` : ''
  const canLower = !isDeliveryOom && machineControls && oom.suggested_coefficient !== null
  const deliveryTarget = oom.requested_target ? `${oom.requested_target} output` : 'requested output'
  const retriedDelivery = (oom.retry_count ?? 0) > 0
  const alertSummary = `${isDeliveryOom ? 'Output processing' : 'Generation'} ran out of GPU memory. ${context}`

  return (
    <div
      className="pointer-events-none fixed inset-0 z-[60] flex max-h-[100vh] items-start justify-center supports-[height:100dvh]:max-h-[100dvh]"
      style={safeViewportPadding}
    >
      <div className="pointer-events-auto max-h-full w-full max-w-xl overflow-y-auto overscroll-contain rounded-lg border border-amber-500/40 bg-bg-secondary shadow-2xl">
        <div className="sr-only" role="alert" aria-live="assertive" aria-atomic="true">
          {alertSummary}
        </div>
        {/* Header strip */}
        <div className="flex items-start gap-2.5 px-4 py-3 bg-amber-500/10">
          <AlertTriangle size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-indicator-warning" />
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium text-text-primary">
              {isDeliveryOom ? 'Output processing ran out of GPU memory' : 'Generation ran out of GPU memory'}
            </div>
            <div className="text-[12px] text-text-secondary mt-0.5">
              {context} {vramHint}
            </div>
          </div>
          <button
            type="button"
            onClick={handleDismiss}
            className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded text-text-muted transition-colors hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-bg-secondary motion-reduce:transition-none"
            aria-label="Dismiss out-of-memory recovery notice"
          >
            <X size={16} aria-hidden="true" />
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
                  <span className="font-medium text-accent-green">Generation finished.</span>{' '}
                  Maestro saved the original result privately when creating the {deliveryTarget.toLowerCase()} ran out of GPU memory
                  {retriedDelivery ? ' after one automatic retry.' : '.'}
                </>
              ) : (
                <>Creating the {deliveryTarget.toLowerCase()} ran out of GPU memory, and the original result was not saved for recovery.</>
              )}
            </div>
          )}

          {isDeliveryOom && activeOom.sourceJobId && activeOom.workspace && (
            <div className="[&_button]:min-h-11 [&_button]:min-w-11 [&_button]:transition-colors [&_button]:focus-visible:outline-none [&_button]:focus-visible:ring-2 [&_button]:focus-visible:ring-accent-blue [&_button]:focus-visible:ring-offset-2 [&_button]:focus-visible:ring-offset-bg-secondary [&_button]:motion-reduce:transition-none">
              <H3DeliveryRecoveryStatus
                sourceJobId={activeOom.sourceJobId}
                workspace={activeOom.workspace}
              />
            </div>
          )}

          {canLower ? (
            <>
              <div className="text-[12px] text-text-secondary leading-snug">
                Use a safer GPU memory setting: change <span className="font-mono text-text-primary">{Math.round(oom.current_coefficient * 100)}%</span> to{' '}
                <span className="font-mono text-indicator-warning">{Math.round(oom.suggested_coefficient! * 100)}%</span>. This leaves more memory for long videos and final output processing, but generation may be about 5% slower.
              </div>
              {visibleApplyError && (
                <div
                  role="alert"
                  aria-live="assertive"
                  aria-atomic="true"
                  className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] leading-snug text-red-200"
                >
                  {visibleApplyError}
                </div>
              )}
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <button
                  type="button"
                  onClick={handleApply}
                  disabled={applying}
                  className="min-h-11 w-full flex-1 rounded-md bg-amber-500 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-amber-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-200 focus-visible:ring-offset-2 focus-visible:ring-offset-bg-secondary disabled:cursor-wait disabled:opacity-50 motion-reduce:transition-none sm:w-auto"
                >
                  {applying ? 'Applying...' : `Use safer setting ${Math.round(oom.suggested_coefficient! * 100)}%`}
                </button>
                <button
                  type="button"
                  onClick={handleDismiss}
                  className="min-h-11 min-w-11 w-full rounded-md px-3 py-2 text-sm text-text-secondary transition-colors hover:bg-bg-tertiary hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-bg-secondary motion-reduce:transition-none sm:w-auto"
                >
                  Dismiss
                </button>
              </div>
              <div className="text-[10px] text-text-muted">
                Try the generation again after applying. The new setting takes effect the next time the model loads.
              </div>
            </>
          ) : machineControls && !isDeliveryOom ? (
            <>
              <div className="text-[12px] text-text-secondary leading-snug">
                The GPU memory setting is already at its safest level (<span className="font-mono">{Math.round(oom.current_coefficient * 100)}%</span>).
                Try a smaller model, reduce the resolution, or shorten the video.
              </div>
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={handleDismiss}
                  className="min-h-11 min-w-11 rounded-md px-3 py-2 text-sm text-text-secondary transition-colors hover:bg-bg-tertiary hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-bg-secondary motion-reduce:transition-none"
                >
                  Dismiss
                </button>
              </div>
            </>
          ) : (
            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleDismiss}
                className="min-h-11 min-w-11 rounded-md px-3 py-2 text-sm text-text-secondary transition-colors hover:bg-bg-tertiary hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-bg-secondary motion-reduce:transition-none"
              >
                Dismiss
              </button>
            </div>
          )}

          {machineControls && (
            <details className="text-[10px] text-text-muted">
              <summary className="inline-flex min-h-11 cursor-pointer items-center rounded hover:text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-bg-secondary">Show error details</summary>
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
