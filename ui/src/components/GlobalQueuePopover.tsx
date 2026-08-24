import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  Check,
  Clock,
  ListVideo,
  Loader2,
  Pause,
  Pencil,
  Play,
  Square,
  Trash2,
  X,
} from 'lucide-react'
import { closeModalIfTop, installModalFocus } from '../lib/modalFocus'
import { isActiveLogicalQueueJob, projectLogicalQueue } from '../lib/queueProjection'
import { useStore } from '../stores/useStore'

const ACTIVE_DIRECTOR_STATUSES = new Set(['held', 'queued', 'running'])
const ACTIVE_PIPELINE_STATUSES = new Set(['running', 'paused'])

function compactStatus(value: string): string {
  return value.replace(/_/g, ' ').replace(/^./, letter => letter.toUpperCase())
}

function queueStatusLabel(value: string): string {
  return ({
    held: 'Held',
    preparing: 'Getting ready',
    waiting_for_plan_approval: 'Needs plan review',
    queued: 'Waiting',
    running: 'Running',
    paused: 'Paused',
    completed: 'Finished',
    failed: 'Needs attention',
    cancelled: 'Cancelled',
  } as Record<string, string>)[value] || compactStatus(value)
}

function progressPercent(step: number, totalSteps: number, progress: number): number {
  if (totalSteps > 0) return Math.max(0, Math.min(100, (step / totalSteps) * 100))
  return Math.max(0, Math.min(100, progress * 100))
}

function directorEntryParam(
  entry: { params?: Record<string, unknown> },
  key: string,
): string {
  const value = entry.params?.[key]
  return typeof value === 'string' ? value : ''
}

export function GlobalQueuePopover({
  iconSize = 16,
  panelAlign = 'icon',
}: {
  iconSize?: number
  panelAlign?: 'icon' | 'header-edge'
}) {
  const [open, setOpen] = useState(false)
  const [startingAll, setStartingAll] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  const jobs = useStore(state => state.jobs)
  const stopGeneration = useStore(state => state.stopGeneration)
  const startStudioQueue = useStore(state => state.startStudioQueue)
  const directorQueue = useStore(state => state.directorQueue)
  const directorQueueLoading = useStore(state => state.directorQueueLoading)
  const loadDirectorQueue = useStore(state => state.loadDirectorQueue)
  const loadDirectorQueueEntry = useStore(state => state.loadDirectorQueueEntry)
  const startDirectorQueue = useStore(state => state.startDirectorQueue)
  const pauseDirectorQueue = useStore(state => state.pauseDirectorQueue)
  const removeDirectorQueueEntry = useStore(state => state.removeDirectorQueueEntry)
  const moveDirectorQueueEntry = useStore(state => state.moveDirectorQueueEntry)
  const pipelineId = useStore(state => state.pipelineId)
  const pipelineStatus = useStore(state => state.pipelineStatus)
  const stopPipeline = useStore(state => state.stopPipeline)
  const setSidebarOpen = useStore(state => state.setSidebarOpen)
  const setSettingsOpen = useStore(state => state.setSettingsOpen)

  const studioProjection = useMemo(() => projectLogicalQueue(jobs), [jobs])
  const studioJobs = useMemo(() => studioProjection.visibleJobs.filter(
    job => isActiveLogicalQueueJob(job) || job.held,
  ), [studioProjection.visibleJobs])
  const studioHeldCount = studioJobs.filter(job => job.held).length
  const directorEntries = directorQueue?.entries || []
  const pendingDirectorCount = directorEntries.filter(entry => (
    ACTIVE_DIRECTOR_STATUSES.has(entry.status)
  )).length
  const runningDirectorCount = directorEntries.filter(entry => entry.status === 'running').length
  const waitingDirectorCount = directorEntries.filter(entry => (
    entry.status === 'held' || entry.status === 'queued'
  )).length
  const startableDirectorCount = directorEntries.filter(entry => (
    entry.status === 'held' || entry.status === 'queued'
  )).length
  const canStartDirector = (
    startableDirectorCount > 0
    && (!directorQueue?.running || directorQueue.paused)
  )
  const canStartHeldWork = studioHeldCount > 0 || canStartDirector
  const startActionLabel = studioHeldCount > 0 && canStartDirector
    ? 'Start held Studio work and resume Director queue'
    : studioHeldCount > 0
      ? 'Start held Studio work'
      : directorQueue?.paused
        ? 'Resume Director queue'
        : 'Start Director queue'
  const activePipeline = Boolean(
    pipelineId && pipelineStatus && ACTIVE_PIPELINE_STATUSES.has(pipelineStatus.status),
  )
  const activePipelineIsQueued = Boolean(
    activePipeline && directorEntries.some(entry => (
      entry.status === 'running'
      && (!entry.pipeline_id || entry.pipeline_id === pipelineId)
    )),
  )
  const totalCount = studioJobs.length
    + pendingDirectorCount
    + (activePipeline && !activePipelineIsQueued ? 1 : 0)

  useEffect(() => {
    void loadDirectorQueue()
  }, [loadDirectorQueue])

  useEffect(() => {
    if (!directorQueue?.running) return
    const timer = window.setInterval(() => void loadDirectorQueue(), 2500)
    return () => window.clearInterval(timer)
  }, [directorQueue?.running, loadDirectorQueue])

  useEffect(() => {
    if (!open) return
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        closeModalIfTop(document, dialogRef.current, () => setOpen(false))
      }
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick)
    }
  }, [open])

  useEffect(() => {
    if (!open || !dialogRef.current || !closeRef.current) return
    return installModalFocus({
      document,
      dialog: dialogRef.current,
      initialFocus: closeRef.current,
      restoreFocus: triggerRef.current,
      appRoot: null,
      onClose: () => setOpen(false),
      priority: 70,
    })
  }, [open])

  const toggleOpen = () => {
    const next = !open
    setOpen(next)
    if (next) {
      setSettingsOpen(false)
      void loadDirectorQueue()
    }
  }

  const openDirectorEntry = async (entryId: string) => {
    await loadDirectorQueueEntry(entryId)
    setOpen(false)
    setSidebarOpen(true)
  }

  const startAllQueues = async () => {
    if (startingAll) return
    setStartingAll(true)
    try {
      // Release Studio first. Director's existing GPU gate sees those jobs
      // as queued/running and waits, so one click safely starts both systems.
      if (studioHeldCount > 0) await startStudioQueue()
      if (
        startableDirectorCount > 0
        && (!directorQueue?.running || directorQueue.paused)
      ) {
        await startDirectorQueue()
      }
    } finally {
      setStartingAll(false)
    }
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={toggleOpen}
        className={`relative flex shrink-0 items-center justify-center rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${iconSize >= 20 ? 'h-11 w-11 p-0' : 'min-h-9 min-w-9 p-2'} ${
          open
            ? 'bg-bg-active text-text-primary'
            : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'
        }`}
        title="Generation queue"
        aria-label={`Generation queue, ${totalCount} ${totalCount === 1 ? 'item' : 'items'}`}
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <ListVideo size={iconSize} />
        {totalCount > 0 && (
          <span className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent-blue px-1 text-[9px] font-semibold leading-none text-white shadow-sm">
            {totalCount > 99 ? '99+' : totalCount}
          </span>
        )}
      </button>

      {open && (
        <div
          ref={dialogRef}
          role="dialog"
          aria-label="Generation queue"
          className={`absolute ${panelAlign === 'header-edge' ? '-right-12' : 'right-0'} top-full z-[80] mt-2 flex max-h-[min(70vh,640px)] w-[min(390px,calc(100vw-1rem))] flex-col overflow-hidden rounded-xl border border-border bg-bg-secondary shadow-2xl`}
        >
          <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <ListVideo size={14} className="text-accent-blue" />
                <h2 className="text-xs font-semibold text-text-primary">Generation Queue</h2>
                <span className="rounded-full bg-accent-blue/15 px-1.5 py-0.5 text-[9px] text-accent-blue">
                  {totalCount} {totalCount === 1 ? 'item' : 'items'}
                </span>
              </div>
              <p className="mt-0.5 text-[10px] text-text-muted">Running, waiting, and held work · timing is estimated</p>
            </div>
            <button
              ref={closeRef}
              type="button"
              onClick={() => closeModalIfTop(document, dialogRef.current, () => setOpen(false))}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-text-muted hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:h-9 md:w-9"
              aria-label="Close queue"
            >
              <X size={14} />
            </button>
          </div>

          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-2.5">
            {canStartHeldWork && (
              <button
                type="button"
                onClick={() => void startAllQueues()}
                disabled={startingAll || directorQueueLoading}
                className="flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-green-500/30 bg-green-500/10 px-3 py-2 text-[11px] font-semibold text-indicator-success hover:bg-green-500/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-40"
                aria-label={startActionLabel}
              >
                {startingAll
                  ? <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                  : <Play size={13} aria-hidden="true" />}
                {startActionLabel}
              </button>
            )}
            {activePipeline && !activePipelineIsQueued && pipelineStatus && (
              <section className="space-y-1.5">
                <div className="flex items-center justify-between px-1">
                  <span className="text-[10px] font-medium uppercase tracking-wider text-text-muted">Director now</span>
                  <span className="text-[9px] text-accent-blue">1 active</span>
                </div>
                <div className="rounded-lg border border-accent-blue/25 bg-bg-tertiary p-2">
                  <div className="flex items-center gap-2">
                    <Loader2 size={11} className="shrink-0 animate-spin text-accent-blue" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[10px] text-text-secondary">
                        {pipelineStatus.progress?.message || 'Director is working'}
                      </div>
                      <div className="text-[10px] text-text-muted">Director · {queueStatusLabel(pipelineStatus.status)}</div>
                    </div>
                    <button
                      type="button"
                      onClick={() => void stopPipeline()}
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-red-400 hover:bg-red-500/10 hover:text-red-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:h-9 md:w-9"
                      aria-label="Stop Director generation"
                    >
                      <Square size={10} />
                    </button>
                  </div>
                  {pipelineStatus.progress && pipelineStatus.progress.total > 0 && (
                    <div className="mt-2 h-1 overflow-hidden rounded-full bg-bg-active">
                      <div
                        className="h-full rounded-full bg-accent-blue transition-all"
                        style={{
                          width: `${Math.max(0, Math.min(100, (pipelineStatus.progress.current / pipelineStatus.progress.total) * 100))}%`,
                        }}
                      />
                    </div>
                  )}
                </div>
              </section>
            )}

            {studioJobs.length > 0 && (
              <section className="space-y-1.5">
                <div className="flex items-center justify-between px-1">
                  <span className="text-[10px] font-medium uppercase tracking-wider text-text-muted">Studio</span>
                  <span className="text-[10px] text-text-muted">
                    {studioHeldCount > 0 ? `${studioHeldCount} held` : `${studioJobs.length} active`}
                  </span>
                </div>
                <div className="space-y-1">
                  {studioJobs.map((job, index) => {
                    const percent = progressPercent(job.step, job.totalSteps, job.progress)
                    const label = job.held
                      ? (job.message && job.message !== 'Ready - waiting for Start Queue'
                          ? job.message
                          : 'Held — starts when you choose the queue action')
                      : job.phase || job.message || (
                        job.status === 'queued' ? 'Waiting to start' : 'Generating'
                      )
                    return (
                      <div key={job.id || `pending-${index}`} className="rounded-lg border border-border bg-bg-tertiary p-2">
                        <div className="flex items-center gap-2">
                          {job.status === 'running'
                            ? <Loader2 size={11} className="shrink-0 animate-spin text-accent-blue" />
                            : <Clock size={11} className="shrink-0 text-text-muted" />}
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-[10px] text-text-secondary">{label}</div>
                            <div className="text-[9px] text-text-muted">
                              Studio · {queueStatusLabel(job.held ? 'held' : job.status)}
                              {job.totalSteps > 0 ? ` · Step ${job.step}/${job.totalSteps}` : ''}
                            </div>
                          </div>
                          <button
                            type="button"
                            onClick={() => {
                              if (job.id) stopGeneration(job.id)
                            }}
                            disabled={!job.id}
                            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-red-400 hover:bg-red-500/10 hover:text-red-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-wait disabled:opacity-30 md:h-9 md:w-9"
                            aria-label={!job.id
                              ? 'Waiting for the server to accept this job'
                              : job.held
                                ? 'Remove held generation'
                                : job.status === 'queued'
                                  ? 'Cancel queued generation'
                                : 'Stop generation'}
                          >
                            {job.held || job.status === 'queued'
                              ? <X size={11} />
                              : <Square size={10} />}
                          </button>
                        </div>
                        <div className="mt-2 h-1 overflow-hidden rounded-full bg-bg-active">
                          <div
                            className={`h-full rounded-full bg-accent-blue transition-all ${percent === 0 && job.status === 'running' ? 'w-full animate-pulse opacity-60' : ''}`}
                            style={percent > 0 ? { width: `${percent}%` } : undefined}
                          />
                        </div>
                      </div>
                    )
                  })}
                </div>
              </section>
            )}

            {directorEntries.length > 0 && (
              <section className="space-y-1.5">
                <div className="flex items-center justify-between gap-2 px-1">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] font-medium uppercase tracking-wider text-text-muted">Director</span>
                    <span className="text-[10px] text-text-muted">
                      {runningDirectorCount > 0 ? `${runningDirectorCount} running` : ''}
                      {runningDirectorCount > 0 && waitingDirectorCount > 0 ? ' · ' : ''}
                      {waitingDirectorCount > 0 ? `${waitingDirectorCount} waiting` : ''}
                    </span>
                  </div>
                  {directorQueue?.running && !directorQueue.paused ? (
                    <button
                      type="button"
                      onClick={() => void pauseDirectorQueue()}
                      disabled={directorQueueLoading}
                      className="flex min-h-11 items-center gap-1 rounded-lg border border-orange-500/30 bg-orange-500/10 px-2.5 py-2 text-[10px] text-chip-orange focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-40 md:min-h-9"
                      aria-label="Pause Director queue after the current project"
                    >
                      <Pause size={9} /> Pause after current
                    </button>
                  ) : null}
                </div>
                <div className="space-y-1">
                  {directorEntries.map((entry, index) => (
                    <div key={entry.id} className="flex items-center gap-1.5 rounded-lg border border-border bg-bg-tertiary px-2 py-1.5">
                      {entry.status === 'running'
                        ? <Loader2 size={10} className="shrink-0 animate-spin text-accent-blue" />
                        : entry.status === 'completed'
                          ? <Check size={10} className="shrink-0 text-indicator-success" />
                          : <Clock size={10} className="shrink-0 text-text-muted" />}
                      <button
                        type="button"
                        onClick={() => void openDirectorEntry(entry.id)}
                        disabled={directorQueueLoading}
                        className="min-h-11 min-w-0 flex-1 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-9"
                        title={entry.message || entry.scene_description}
                      >
                        <div className="truncate text-[10px] text-text-secondary">
                          {entry.scene_description || `${directorEntryParam(entry, 'pipeline_type').replace(/_/g, ' ') || 'Director'} project`}
                        </div>
                        <div className={`truncate text-[8px] ${entry.status === 'failed' ? 'text-red-400' : 'text-text-muted'}`}>
                          {queueStatusLabel(entry.status)} · {entry.message || directorEntryParam(entry, 'video_model') || 'Director project'}
                        </div>
                      </button>
                      <button
                        type="button"
                        onClick={() => void openDirectorEntry(entry.id)}
                        disabled={directorQueueLoading}
                        aria-label="Open Director project"
                        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-text-muted hover:bg-bg-hover hover:text-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-40 md:h-9 md:w-9"
                      >
                        <Pencil size={9} />
                      </button>
                      {entry.status !== 'running' && (
                        <>
                          <button
                            type="button"
                            onClick={() => void moveDirectorQueueEntry(entry.id, -1)}
                            disabled={index === 0 || directorQueueLoading}
                            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-text-muted hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-20 md:h-9 md:w-9"
                            aria-label="Move Director project up"
                          >
                            <ArrowUp size={9} />
                          </button>
                          <button
                            type="button"
                            onClick={() => void moveDirectorQueueEntry(entry.id, 1)}
                            disabled={index === directorEntries.length - 1 || directorQueueLoading}
                            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-text-muted hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-20 md:h-9 md:w-9"
                            aria-label="Move Director project down"
                          >
                            <ArrowDown size={9} />
                          </button>
                          <button
                            type="button"
                            onClick={() => void removeDirectorQueueEntry(entry.id)}
                            disabled={directorQueueLoading}
                            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-text-muted hover:bg-red-500/10 hover:text-red-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-40 md:h-9 md:w-9"
                            aria-label="Remove Director project from queue"
                          >
                            <Trash2 size={9} />
                          </button>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {!activePipeline && studioJobs.length === 0 && directorEntries.length === 0 && (
              <div className="flex min-h-36 flex-col items-center justify-center gap-2 px-6 text-center">
                <ListVideo size={28} className="text-text-muted/60" />
                <div className="text-xs font-medium text-text-secondary">Queue is empty</div>
                <p className="text-[10px] leading-relaxed text-text-muted">
                  Queued Studio generations and held Director projects will appear here.
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
