import { useEffect, useRef, useState } from 'react'
import { ChevronUp, ChevronDown, Cpu, MemoryStick, Power, Zap } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import {
  AccountApiError,
  fetchResourceReleasePreview,
  releaseResources,
  type ResourceReleasePreview,
} from '../../api/client'
import { POLL_INTERVAL_MS, useVisibilityPolling } from '../../lib/useVisibilityPolling'

// Color a "fullness" bar (VRAM / RAM) by how close to full it is —
// green well below, amber as it tightens, red near the ceiling. This is
// the at-a-glance OOM-risk read that matters most in this app.
function fullnessColor(pct: number): string {
  if (pct >= 90) return 'bg-red-500'
  if (pct >= 75) return 'bg-indicator-warning'
  return 'bg-emerald-500'
}

// Same thresholds, applied to TEXT (used by the collapsed chips, which
// have no bars). Low load stays neutral so only pressure stands out.
function fullnessText(pct: number): string {
  if (pct >= 90) return 'text-chip-red'
  if (pct >= 75) return 'text-indicator-warning'
  return 'text-text-secondary'
}

function Gauge({
  label,
  percent,
  value,
  fill,
  title,
}: {
  label: string
  percent: number
  value: string
  fill: string
  title?: string
}) {
  const w = Math.max(0, Math.min(100, percent))
  return (
    <div className="flex items-center gap-2" title={title}>
      <span className="w-10 shrink-0 text-[10px] text-text-muted uppercase tracking-wide">{label}</span>
      <div className="flex-1 h-1.5 rounded-full bg-bg-tertiary overflow-hidden">
        <div
          className={`h-full rounded-full ${fill} transition-[width] duration-500`}
          style={{ width: `${w}%` }}
        />
      </div>
      <span className="w-[92px] shrink-0 text-right text-[10px] text-text-secondary tabular-nums">{value}</span>
    </div>
  )
}

const COLLAPSE_KEY = 'hwbar_collapsed'

function resourceCount(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`
}

function activeWorkDescription(preview: ResourceReleasePreview): string {
  const parts = [
    resourceCount(preview.running, 'running job'),
    resourceCount(preview.queued, 'queued work item', 'queued work items'),
    resourceCount(preview.preparing, 'job being prepared', 'jobs being prepared'),
    resourceCount(preview.director_running, 'Director pipeline running', 'Director pipelines running'),
  ]
  return `Current work: ${parts.join(', ')}.`
}

function resourceReleaseError(error: unknown): string {
  if (error instanceof AccountApiError) {
    if (error.status === 401) return 'Sign in as the owner before freeing Maestro resources.'
    if (error.code === 'owner_reauthentication_required') {
      return 'Confirm your owner password in Account settings, then try again.'
    }
    if (error.status === 403) {
      return 'Sign in as the owner and confirm your password in Account settings before freeing Maestro resources.'
    }
    return error.message || 'Could not load the resource release preview.'
  }
  return error instanceof Error && error.message
    ? error.message
    : 'Could not load the resource release preview.'
}

function isStaleResourceReleasePreview(error: AccountApiError): boolean {
  return error.code === 'resource_activity_changed'
    || error.code === 'resource_selection_changed'
    || /(?:queue activity|loaded resources) changed/i.test(error.message)
}

function mustWaitForResourceRelease(error: AccountApiError): boolean {
  return error.code === 'paired_sample_active'
    || /paired sample is active/i.test(error.message)
}

function ResourceReleaseControl({
  enabled,
  refreshHardware,
  compact = false,
}: {
  enabled: boolean
  refreshHardware: () => void
  compact?: boolean
}) {
  const [preview, setPreview] = useState<ResourceReleasePreview | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [opening, setOpening] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [selection, setSelection] = useState<'all' | string[]>('all')
  const [confirmationNotice, setConfirmationNotice] = useState<string | null>(null)
  const [waitForActiveWork, setWaitForActiveWork] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [accountAction, setAccountAction] = useState(false)
  const statusTimer = useRef<number | undefined>(undefined)
  const setAccountDrawerOpen = useStore(state => state.setAccountDrawerOpen)

  useEffect(() => () => window.clearTimeout(statusTimer.current), [])

  if (!enabled) return null

  const hasActiveWork = preview !== null && (
    preview.running > 0
    || preview.queued > 0
    || preview.preparing > 0
    || preview.director_running > 0
  )
  const hasLoadedResources = (preview?.loaded.length ?? 0) > 0
  const needsToStopRunning = preview !== null && (
    preview.running > 0 || preview.preparing > 0 || preview.director_running > 0
  )

  const setStatusBriefly = (message: string) => {
    setStatus(message)
    setAccountAction(false)
    window.clearTimeout(statusTimer.current)
    statusTimer.current = window.setTimeout(() => setStatus(null), 8000)
  }

  const setStatusForError = (error: unknown) => {
    if (error instanceof AccountApiError && (error.status === 401 || error.status === 403)) {
      window.clearTimeout(statusTimer.current)
      setStatus(resourceReleaseError(error))
      setAccountAction(true)
    } else {
      setStatusBriefly(resourceReleaseError(error))
    }
  }

  const openPreview = async () => {
    setOpening(true)
    setStatus(null)
    setAccountAction(false)
    setConfirmationNotice(null)
    setWaitForActiveWork(false)
    try {
      const next = await fetchResourceReleasePreview()
      setPreview(next)
      setSelection('all')
      setConfirmOpen(true)
    } catch (error) {
      setStatusForError(error)
    } finally {
      setOpening(false)
    }
  }

  const submitRelease = async () => {
    if (!preview || submitting || (Array.isArray(selection) && selection.length === 0)) return
    setSubmitting(true)
    setConfirmationNotice(null)
    setWaitForActiveWork(false)
    try {
      const result = await releaseResources({
        activity_token: preview.activity_token,
        confirm_active: hasActiveWork,
        stop_running: needsToStopRunning,
        targets: selection === 'all' ? ['all'] : selection,
      })
      const details = [
        result.released.length
          ? `Freed ${result.released.join(', ')}`
          : result.failures.length > 0
          ? 'No selected Maestro resources were freed'
          : 'No loaded resources needed clearing',
        result.stopped_jobs > 0 ? `stopped ${resourceCount(result.stopped_jobs, 'job')}` : '',
        result.stopped_pipelines > 0 ? `stopped ${resourceCount(result.stopped_pipelines, 'Director pipeline', 'Director pipelines')}` : '',
        result.queue_paused ? 'main queue remains paused' : '',
        result.director_queue_paused ? 'Director queue remains paused' : '',
        result.failures.length > 0
          ? `${resourceCount(result.failures.length, 'cleanup step')} failed; try again`
          : '',
      ].filter(Boolean)
      setConfirmOpen(false)
      setPreview(null)
      setSelection('all')
      setStatusBriefly(details.join('; '))
      refreshHardware()
    } catch (error) {
      if (error instanceof AccountApiError && error.status === 409) {
        try {
          const updated = await fetchResourceReleasePreview()
          setPreview(updated)
          const stalePreview = isStaleResourceReleasePreview(error)
          if (error.code === 'resource_selection_changed' || /loaded resources changed/i.test(error.message)) {
            setSelection('all')
          }
          const pausedQueues = [
            updated.queue_paused ? 'main queue' : '',
            updated.director_queue_paused ? 'Director queue' : '',
          ].filter(Boolean)
          const queueNotice = pausedQueues.length > 0
            ? `${pausedQueues.join(' and ')} ${pausedQueues.length === 1 ? 'is' : 'are'} paused; canceling will not resume ${pausedQueues.length === 1 ? 'it' : 'them'}.`
            : ''
          const errorMessage = error.message || 'Resource release could not continue.'
          const waitForWork = mustWaitForResourceRelease(error)
          setWaitForActiveWork(waitForWork)
          setConfirmationNotice([
            stalePreview ? `Preview refreshed. ${errorMessage}` : errorMessage,
            waitForWork ? 'Cancel and check again after it finishes.' : '',
            queueNotice,
          ].filter(Boolean).join(' '))
        } catch (previewError) {
          setConfirmOpen(false)
          setPreview(null)
          setStatusForError(previewError)
        }
      } else {
        if (error instanceof AccountApiError && (error.status === 401 || error.status === 403)) {
          setConfirmOpen(false)
          setPreview(null)
          setStatusForError(error)
        } else {
          setConfirmationNotice(resourceReleaseError(error))
        }
      }
    } finally {
      setSubmitting(false)
    }
  }

  const toggleTarget = (target: string, checked: boolean) => {
    setSelection(current => {
      const selected = current === 'all' ? [...(preview?.loaded ?? [])] : current
      return checked
        ? Array.from(new Set([...selected, target]))
        : selected.filter(item => item !== target)
    })
  }

  const cancelPreview = () => {
    setConfirmOpen(false)
    setPreview(null)
    setSelection('all')
    setConfirmationNotice(null)
    setWaitForActiveWork(false)
  }

  return (
    <div className={compact ? 'relative shrink-0' : 'mt-1.5 border-t border-border/50 pt-1.5'}>
      {!confirmOpen && !opening && !submitting && (
        <button
          type="button"
          onClick={openPreview}
          title="Review and free loaded Maestro resources"
          aria-label="Review and free loaded Maestro resources"
          className={`flex items-center gap-1.5 rounded px-1.5 py-1 text-[10px] text-text-muted hover:bg-bg-hover hover:text-text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${compact ? '' : 'ml-auto'}`}
        >
          <Power aria-hidden="true" size={11} />
          <span>{compact ? 'Free' : 'Free resources'}</span>
        </button>
      )}
      {(opening || submitting) && (
        <div className="px-1.5 py-1 text-[10px] text-text-muted" role="status">
          {opening ? 'Checking loaded resources…' : 'Freeing selected resources…'}
        </div>
      )}
      {confirmOpen && preview && (
        <div
          className={`mt-1 rounded-md border border-border bg-bg-primary p-2 text-[10px] text-text-secondary ${compact ? 'absolute bottom-full right-0 z-50 mb-2 w-72 max-w-[calc(100vw-2rem)] shadow-lg' : ''}`}
          role="group"
          aria-label="Confirm resource release"
        >
          {preview.loaded.length > 0 ? (
            <div className="mb-1.5">
              <div className="text-text-muted">Loaded resources</div>
              <div className="break-words">{preview.loaded.join(', ')}</div>
            </div>
          ) : (
            <div className="mb-1.5">No loaded Maestro resources were reported. Nothing to free.</div>
          )}
          {preview.loaded.length > 1 && (
            <fieldset className="mb-1.5">
              <legend className="mb-1 text-text-muted">Choose what to free</legend>
              <label className="flex items-center gap-1.5 py-0.5">
                <input
                  type="radio"
                  name="resource-release-selection"
                  checked={selection === 'all'}
                  onChange={() => setSelection('all')}
                />
                <span>All</span>
              </label>
              <div className="max-h-24 overflow-y-auto">
                {preview.loaded.map((resource, index) => (
                  <label key={`${resource}-${index}`} className="flex items-center gap-1.5 py-0.5">
                    <input
                      type="checkbox"
                      checked={selection === 'all' || selection.includes(resource)}
                      onChange={event => toggleTarget(resource, event.currentTarget.checked)}
                    />
                    <span className="break-all">{resource}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          {hasLoadedResources && hasActiveWork ? (
            <div className="mb-1.5 rounded border border-indicator-warning/40 bg-indicator-warning/10 p-1.5 text-text-primary">
              <p>{activeWorkDescription(preview)}</p>
              <p className="mt-1">
                {needsToStopRunning && 'Continuing will stop active generation and Director work. '}
                {preview.queued > 0 && (preview.queue_paused || preview.director_queue_paused
                  ? 'Already-paused queues will stay paused; continuing will pause any remaining queued work. '
                  : 'Continuing will pause queued work. ')}
                {preview.queued === 0 && (preview.queue_paused || preview.director_queue_paused) && (
                  `${[
                    preview.queue_paused ? 'main queue' : '',
                    preview.director_queue_paused ? 'Director queue' : '',
                  ].filter(Boolean).join(' and ')} ${preview.queue_paused && preview.director_queue_paused ? 'are' : 'is'} already paused and will stay paused. `
                )}
                The selected resources will then be freed. Continue?
              </p>
            </div>
          ) : hasLoadedResources ? (
            <p className="mb-1.5">Free the selected resources now?</p>
          ) : null}
          {confirmationNotice && (
            <p className="mb-1.5 text-indicator-warning" role="status">{confirmationNotice}</p>
          )}
          <div className="flex items-center justify-end gap-1.5">
            <button
              type="button"
              onClick={cancelPreview}
              disabled={submitting}
              className="rounded px-2 py-1 text-text-muted hover:bg-bg-hover disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={submitRelease}
              disabled={!hasLoadedResources || submitting || waitForActiveWork || (Array.isArray(selection) && selection.length === 0)}
              className="rounded bg-red-500/15 px-2 py-1 font-medium text-chip-red hover:bg-red-500/25 disabled:opacity-50"
            >
              {submitting ? 'Freeing…' : !hasLoadedResources ? 'Nothing to free' : waitForActiveWork ? 'Wait for it to finish' : 'Confirm and free'}
            </button>
          </div>
        </div>
      )}
      {status && !confirmOpen && (
        <div className={`px-1.5 py-1 text-[10px] text-text-muted break-words ${compact ? 'absolute bottom-full right-0 z-50 mb-2 w-72 max-w-[calc(100vw-2rem)] rounded border border-border bg-bg-primary shadow-lg' : ''}`} role="status" aria-live="polite">
          {status}
          {accountAction && (
            <button
              type="button"
              onClick={() => {
                setAccountDrawerOpen(true)
                setStatus(null)
                setAccountAction(false)
              }}
              className="mt-1 block rounded px-1.5 py-1 font-medium text-accent-blue hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            >
              Open Account
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Live hardware status indicators docked at the bottom of the sidebar.
 * Two views, toggled by the chevron and remembered in localStorage:
 *   - Expanded: labeled mini-gauges (GPU util + VRAM, CPU, RAM) plus the
 *     resident model and loaded LLM.
 *   - Collapsed: a single one-line row of tiny status chips (~the height
 *     of the model line), for users who want the readout but not the bulk.
 * Polls GET /api/v1/system-stats while local machine telemetry is available;
 * remote owners get the resource release control without requesting that
 * owner-only telemetry route.
 */
export function HardwareStatusBar({
  canClearResources = true,
  showHardwareTelemetry = true,
}: {
  canClearResources?: boolean
  showHardwareTelemetry?: boolean
} = {}) {
  const stats = useStore(s => s.systemStats)
  const loadSystemStats = useStore(s => s.loadSystemStats)
  const llmStatus = useStore(s => s.llmStatus)

  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(COLLAPSE_KEY) === '1' } catch { return false }
  })
  const toggle = () => setCollapsed(c => {
    const next = !c
    try { localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0') } catch { /* ignore */ }
    return next
  })

  useVisibilityPolling(
    () => loadSystemStats(),
    POLL_INTERVAL_MS.hardwareVisible,
    { enabled: showHardwareTelemetry },
  )

  const resourceControl = (
    <ResourceReleaseControl
      enabled={canClearResources}
      refreshHardware={() => {
        if (showHardwareTelemetry) loadSystemStats()
      }}
    />
  )

  // Stable-share owners get the explicit release control without entering
  // the machine-telemetry surface, which is not available to remote users.
  if (!showHardwareTelemetry) {
    return (
      <div className="px-3 py-2 border-t border-border bg-bg-secondary shrink-0">
        <div className="text-[9px] uppercase tracking-wider text-text-muted">Maestro resources</div>
        <ResourceReleaseControl
          enabled={canClearResources}
          refreshHardware={() => { /* remote owners cannot read machine telemetry */ }}
        />
      </div>
    )
  }

  const gpu = stats?.gpu
  const ram = stats?.ram
  const cpu = stats?.cpu
  const model = stats?.model
  // Only treat a model as "current" when it is actually resident in VRAM.
  // On a fresh restart `transformer_type` is seeded from the config's
  // last_model_type (the model from the previous session), so without
  // this gate the bar would show a stale name that isn't loaded.
  const modelLoaded = !!model?.loaded

  const fmtGb = (used?: number, total?: number) =>
    used == null || total == null ? '—' : `${used.toFixed(1)} / ${total.toFixed(0)} GB`
  const fmtG = (v?: number) => (v == null ? '—' : `${v.toFixed(1)}G`)

  // ---- Collapsed: one row of tiny status chips ----------------------
  if (collapsed) {
    return (
      <div className="w-full border-t border-border bg-bg-secondary shrink-0">
        <div className="flex items-center gap-1.5 px-3 py-1">
          <button
            type="button"
            onClick={toggle}
            title="Show hardware status"
            aria-label="Show hardware status"
            className="flex min-w-0 flex-1 items-center gap-2.5 py-0.5 text-[10px] hover:bg-bg-hover transition-colors"
          >
            {gpu?.available && (
              <span
                className="flex items-center gap-1 shrink-0 text-text-secondary"
                title={`GPU activity ${gpu.percent.toFixed(0)}%${gpu.compute_percent != null ? ` · compute activity ${gpu.compute_percent.toFixed(0)}%` : ''} · GPU memory ${fmtGb(gpu.vram_used_gb, gpu.vram_total_gb)}`}
              >
                <Zap size={11} className="text-text-muted" />
                <span className="tabular-nums">{gpu.percent.toFixed(0)}%</span>
                <span className={`tabular-nums ${fullnessText(gpu.vram_percent)}`}>{fmtG(gpu.vram_used_gb)}</span>
              </span>
            )}
            <span className="flex items-center gap-1 shrink-0 text-text-secondary" title={`CPU ${(cpu?.percent ?? 0).toFixed(0)}%`}>
              <Cpu size={11} className="text-text-muted" />
              <span className="tabular-nums">{(cpu?.percent ?? 0).toFixed(0)}%</span>
            </span>
            <span className="flex items-center gap-1 shrink-0 text-text-secondary" title={`RAM ${fmtGb(ram?.used_gb, ram?.total_gb)}`}>
              <MemoryStick size={11} className="text-text-muted" />
              <span className={`tabular-nums ${fullnessText(ram?.percent ?? 0)}`}>{fmtG(ram?.used_gb)}</span>
            </span>
            <span
              className="flex items-center gap-1 min-w-0 ml-auto"
              title={modelLoaded ? `${model?.name || 'Unknown model'} — loaded` : 'No model loaded'}
            >
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${modelLoaded ? 'bg-emerald-500' : 'bg-text-muted/40'}`} />
              <span className="truncate text-text-muted">{modelLoaded ? (model?.name || '—') : 'No model'}</span>
            </span>
            <ChevronUp size={13} className="shrink-0 text-text-muted" />
          </button>
          <ResourceReleaseControl
            enabled={canClearResources}
            refreshHardware={() => loadSystemStats()}
            compact
          />
        </div>
      </div>
    )
  }

  // ---- Expanded: full gauges ----------------------------------------
  return (
    <div className="px-3 py-2 border-t border-border bg-bg-secondary shrink-0">
      <div className="flex items-center justify-between mb-1">
        <span className="text-[9px] uppercase tracking-wider text-text-muted">Computer usage</span>
        <button
          onClick={toggle}
          title="Collapse"
          className="p-0.5 rounded hover:bg-bg-hover text-text-muted hover:text-text-secondary transition-colors"
        >
          <ChevronDown size={13} />
        </button>
      </div>
      <div className="flex flex-col gap-1">
        {gpu?.available ? (
          <>
            <Gauge label="GPU" percent={gpu.percent} value={`${gpu.percent.toFixed(0)}%`} fill="bg-accent-blue"
              title={gpu.compute_percent != null ? `Overall GPU activity · compute activity ${gpu.compute_percent.toFixed(0)}%` : undefined} />
            <Gauge
              label="VRAM"
              percent={gpu.vram_percent}
              value={fmtGb(gpu.vram_used_gb, gpu.vram_total_gb)}
              fill={fullnessColor(gpu.vram_percent)}
            />
          </>
        ) : (
          <div className="text-[10px] text-text-muted">No compatible NVIDIA GPU found</div>
        )}
        <Gauge label="CPU" percent={cpu?.percent ?? 0} value={`${(cpu?.percent ?? 0).toFixed(0)}%`} fill="bg-accent-blue" />
        <Gauge label="RAM" percent={ram?.percent ?? 0} value={fmtGb(ram?.used_gb, ram?.total_gb)} fill={fullnessColor(ram?.percent ?? 0)} />
      </div>

      {/* Currently-loaded model(s) */}
      <div className="mt-1.5 pt-1.5 border-t border-border/50 flex flex-col gap-0.5">
        <div
          className="flex items-center gap-1.5 min-w-0"
          title={modelLoaded ? `${model?.name || 'Unknown model'} — ready in GPU memory` : 'No generation model loaded'}
        >
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${modelLoaded ? 'bg-emerald-500' : 'bg-text-muted/40'}`} />
          <span className="text-[11px] text-text-secondary truncate">
            {modelLoaded ? (model?.name || 'Unknown model') : 'No model loaded'}
          </span>
        </div>
        {llmStatus?.loaded && llmStatus.model_id && (
          <div className="flex items-center gap-1.5 min-w-0" title="Director assistant model">
            <span className="w-1.5 h-1.5 rounded-full shrink-0 bg-accent-blue" />
            <span className="text-[10px] text-text-muted truncate">Director assistant · {llmStatus.model_id}</span>
          </div>
        )}
      </div>
      {resourceControl}
    </div>
  )
}
