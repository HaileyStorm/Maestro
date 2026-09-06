import { useRef, useCallback, useState, useEffect, useId, useLayoutEffect, useMemo, type JSX, type ReactNode } from 'react'
import { Film, Play, Square, FolderOpen, Plus, Check, Loader2, X, BookMarked, Upload, Trash2, ListChecks, Eye, EyeOff, FolderInput, Lock, LockOpen, KeyRound, Pause, ArrowUp, ArrowDown, Sparkles, Share2 } from 'lucide-react'
import { TabFilter } from './TabFilter'
import { ThumbnailGallery } from './ThumbnailGallery'
import { MediaFeedItem } from './MediaFeedItem'
import { ProjectAccessPanel } from './ProjectAccessPanel'
import { LlmChat } from '../LlmChat'
import { H3DeliveryRecoveryStatus, OPEN_GALLERY_EVENT } from '../H3DeliveryRecoveryStatus'
import { useStore } from '../../stores/useStore'
import type { GenerationJob, ModelDef } from '../../types'
import * as api from '../../api/client'
import { modelDisplayName } from '../../lib/modelDisplay'
import {
  privatePreviewIdentity,
  privatePreviewWorkspaceHasRevealed,
  setPrivatePreviewsForWorkspaceRevealed,
  subscribePrivatePreviewChanges,
} from '../../lib/privatePreview'
import { boundedBackoffDelay, POLL_INTERVAL_MS, useVisibilityPolling } from '../../lib/useVisibilityPolling'
import { copyTextToClipboard } from '../../lib/clipboard'
import { subscribeQueueView } from '../../lib/mainViewNavigation'
import { isActiveLogicalQueueJob, projectLogicalQueue } from '../../lib/queueProjection'
import { formatApproximateDuration, formatMediaDuration } from '../../lib/format'

const QUEUE_REFRESH_EVENT = 'maestro:queue-refresh'
const REQUEST_WORKSPACE_UNLOCK_EVENT = 'maestro:request-workspace-unlock'
const RESOURCE_WAIT_TITLE = 'This generation is still in the queue. It will start when enough GPU resources are available, without interrupting a generation already running.'
const CPU_RESTART_WARNING = 'This text task is running on the CPU, so it may be slower. Maestro will restart it with GPU acceleration only if starting over is expected to finish sooner.'

type ProjectPermission =
  | 'project.open'
  | 'project.read'
  | 'project.mutate'
  | 'project.generate'
  | 'project.lifecycle'
  | 'project.membership.manage'
  | 'project.delete'

// A missing projection is the pre-cutover compatibility shape. Once the
// backend projects permissions, those exact values are the only UI authority.
function workspaceAllowsPermission(
  workspace: api.Workspace | undefined,
  permission: ProjectPermission,
): boolean {
  if (!workspace) return false
  return workspace.project_permissions === undefined
    ? true
    : workspace.project_permissions.includes(permission)
}

// Exported for executable role-projection regression coverage.
// eslint-disable-next-line react-refresh/only-export-components
export function projectActionVisibility(workspace: api.Workspace | undefined) {
  return {
    mutate: workspaceAllowsPermission(workspace, 'project.mutate'),
    generate: workspaceAllowsPermission(workspace, 'project.generate'),
    lifecycle: workspaceAllowsPermission(workspace, 'project.lifecycle'),
    delete: workspaceAllowsPermission(workspace, 'project.delete'),
  }
}

const H3_MODEL_FALLBACK_LABELS: Readonly<Record<string, string>> = {
  minimax_h3: 'FL2VA video model',
  minimax_h3_pinkcherry_fl2va: 'PinkCherry FL2VA video model',
  minimax_h3_w4a8_fl2va: 'Kijai W4A8 FL2VA video model',
  minimax_h3_ref2va: 'Ref2VA video model',
}

const H3_BOUNDARY_LABELS: Readonly<Record<string, string>> = {
  continuous: 'Continuous motion',
  precut: 'Continue into cut',
  cut: 'Hard camera or scene cut',
  transition: 'Smooth transition',
}

const JOB_STATUS_LABELS: Readonly<Record<string, string>> = {
  preparing: 'Preparing',
  waiting_for_plan_approval: 'Waiting for plan review',
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  held: 'Held',
  blocked: 'Needs attention',
  interrupted: 'Interrupted',
  restored: 'Restored',
  retrying: 'Retrying',
}

function visibleModelName(modelType: string | null | undefined, models: ModelDef[]): string {
  if (!modelType) return ''
  const catalogName = modelDisplayName(modelType, models)
  if (catalogName && catalogName !== modelType) return catalogName
  return H3_MODEL_FALLBACK_LABELS[modelType] || 'Model details unavailable'
}

function h3SegmentPurpose(modelType: string | null | undefined): string {
  if (modelType === 'minimax_h3_ref2va') return 'Uses reference images and recent motion'
  if (modelType?.startsWith('minimax_h3')) return 'Follows this segment’s frame anchors'
  return 'Selection details unavailable'
}

function visibleBoundaryName(boundary: string | null | undefined): string {
  if (!boundary) return 'Start'
  return H3_BOUNDARY_LABELS[boundary] || 'Boundary details unavailable'
}

function visibleJobStatus(status: string | null | undefined): string {
  if (!status) return 'Status update'
  return JOB_STATUS_LABELS[status] || 'Status update'
}

const MAIN_VIEWS = ['gallery', 'queue', 'chat'] as const
type MainView = typeof MAIN_VIEWS[number]

function nextMainViewFromKey(current: MainView, key: string): MainView | null {
  if (key === 'Home') return MAIN_VIEWS[0]
  if (key === 'End') return MAIN_VIEWS[MAIN_VIEWS.length - 1]
  if (key !== 'ArrowLeft' && key !== 'ArrowRight') return null
  const offset = key === 'ArrowRight' ? 1 : -1
  const currentIndex = MAIN_VIEWS.indexOf(current)
  return MAIN_VIEWS[(currentIndex + offset + MAIN_VIEWS.length) % MAIN_VIEWS.length]
}

function MainViewTabs({
  activeView,
  onSelect,
  queueTitle,
  queueStateColor,
  activeQueueCount,
  queueStateLabel,
  queueDetails,
}: {
  activeView: MainView
  onSelect: (view: MainView) => void
  queueTitle: string
  queueStateColor: string
  activeQueueCount: number
  queueStateLabel: string
  queueDetails?: JSX.Element
}) {
  const selectFromKeyboard = (event: React.KeyboardEvent<HTMLButtonElement>, current: MainView) => {
    const next = nextMainViewFromKey(current, event.key)
    if (!next) return
    event.preventDefault()
    onSelect(next)
    window.requestAnimationFrame(() => {
      document.getElementById(`main-${next}-tab`)?.focus()
    })
  }

  return (
    <div
      role="tablist"
      aria-label="Main views"
      aria-orientation="horizontal"
      className="flex max-w-full shrink-0 overflow-x-auto rounded-md border border-border bg-bg-tertiary p-0.5 text-[10px]"
    >
      <button
        type="button"
        id="main-gallery-tab"
        role="tab"
        aria-selected={activeView === 'gallery'}
        aria-controls="main-gallery-panel"
        tabIndex={activeView === 'gallery' ? 0 : -1}
        className={`min-h-11 min-w-11 shrink-0 rounded px-2 py-1 md:min-h-0 md:min-w-0 ${activeView === 'gallery' ? 'bg-bg-active text-text-primary' : 'text-text-muted'}`}
        onClick={() => onSelect('gallery')}
        onKeyDown={event => selectFromKeyboard(event, 'gallery')}
      >
        Gallery
      </button>
      <button
        type="button"
        id="main-queue-tab"
        role="tab"
        aria-selected={activeView === 'queue'}
        aria-controls="main-queue-panel"
        tabIndex={activeView === 'queue' ? 0 : -1}
        title={queueTitle}
        className={`flex min-h-11 min-w-11 shrink-0 items-center gap-1.5 rounded px-2 py-1 md:min-h-0 md:min-w-0 ${activeView === 'queue' ? 'bg-bg-active text-text-primary' : 'text-text-muted'}`}
        onClick={() => onSelect('queue')}
        onKeyDown={event => selectFromKeyboard(event, 'queue')}
      >
        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${queueStateColor}`} />
        <span>Queue</span>
        {activeQueueCount > 0 && <span className="rounded-full bg-bg-primary/70 px-1 text-[9px]">{activeQueueCount}</span>}
        <span className="hidden text-[9px] lg:inline">{queueStateLabel}</span>
        {queueDetails}
      </button>
      <button
        type="button"
        id="main-chat-tab"
        role="tab"
        aria-selected={activeView === 'chat'}
        aria-controls="main-chat-panel"
        tabIndex={activeView === 'chat' ? 0 : -1}
        className={`min-h-11 min-w-11 shrink-0 rounded px-2 py-1 md:min-h-0 md:min-w-0 ${activeView === 'chat' ? 'bg-bg-active text-text-primary' : 'text-text-muted'}`}
        onClick={() => onSelect('chat')}
        onKeyDown={event => selectFromKeyboard(event, 'chat')}
      >
        Chat
      </button>
    </div>
  )
}

function MainViewPanels({ activeView, children }: { activeView: MainView; children: ReactNode }) {
  return (
    <>
      {MAIN_VIEWS.filter(view => view !== activeView).map(view => (
        <div
          key={view}
          id={`main-${view}-panel`}
          role="tabpanel"
          aria-labelledby={`main-${view}-tab`}
          hidden
          className="hidden"
        />
      ))}
      <div
        id={`main-${activeView}-panel`}
        role="tabpanel"
        aria-labelledby={`main-${activeView}-tab`}
        tabIndex={0}
        className="flex min-h-0 flex-1 flex-col overflow-hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue"
      >
        {children}
      </div>
    </>
  )
}

type QueueTabSnapshot = {
  state: api.QueueState | null
  jobs: GenerationJob[]
  error: string | null
  lastSuccessAt: number | null
}

type QueueTabRefreshOutcome =
  | { kind: 'success'; state: api.QueueState; jobs: GenerationJob[]; receivedAt: number }
  | { kind: 'failure'; error: string }

function queueRefreshIsStale(
  sequence: number,
  currentSequence: number,
  aborted: boolean,
): boolean {
  return sequence !== currentSequence || aborted
}

function reduceQueueTabSnapshot(
  current: QueueTabSnapshot,
  outcome: QueueTabRefreshOutcome,
): QueueTabSnapshot {
  if (outcome.kind === 'failure') return { ...current, error: outcome.error }
  return {
    state: outcome.state,
    jobs: outcome.jobs,
    error: null,
    lastSuccessAt: outcome.receivedAt,
  }
}

function queueTabDisplayJobs(
  snapshot: QueueTabSnapshot,
  liveJobs: GenerationJob[],
): GenerationJob[] {
  return snapshot.error ? snapshot.jobs : liveJobs
}

type GalleryEmptyState = 'none' | 'onboarding' | 'uploads' | 'filtered' | 'project-required'

function galleryEmptyState({
  outputsLoading,
  outputCount,
  outputsTotal,
  browsingUploads,
  activeWorkspace,
  hasActiveFilters,
  hasProjectJobs,
}: {
  outputsLoading: boolean
  outputCount: number
  outputsTotal: number
  browsingUploads: boolean
  activeWorkspace: string
  hasActiveFilters: boolean
  hasProjectJobs: boolean
}): GalleryEmptyState {
  if (outputsLoading || outputCount > 0) return 'none'
  if (browsingUploads) return 'uploads'
  if (!activeWorkspace) return 'project-required'
  if (hasActiveFilters) return 'filtered'
  if (outputsTotal > 0 || hasProjectJobs) return 'none'
  return 'onboarding'
}

type QueuePanelEmptyState = 'pending' | 'unavailable' | 'cached-stale' | 'empty' | 'none'

function queuePanelEmptyState(
  queue: api.QueueState | null,
  queueError: string | null,
  queueLastSuccessAt: number | null,
  visibleJobCount: number,
): QueuePanelEmptyState {
  if (visibleJobCount > 0) return 'none'
  if (!queue) return queueError ? 'unavailable' : 'pending'
  if (queueError && queueLastSuccessAt !== null) return 'cached-stale'
  return 'empty'
}

type ResourcePresentation = {
  label: string
  title: string
  warning?: string
  tone: 'accelerated' | 'cpu' | 'transition' | 'neutral'
}

function CopyableJobId({ jobId, label = 'Job ID' }: { jobId: string; label?: string }) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const copy = async () => {
    const copied = await copyTextToClipboard(jobId)
    setCopyState(copied ? 'copied' : 'failed')
  }
  const feedback = copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy failed' : 'Copy'

  return (
    <span className="inline-flex flex-col items-center">
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={`Copy ${label.toLowerCase()} ${jobId}`}
        title={copyState === 'copied' ? `${label} copied` : copyState === 'failed' ? `Could not copy ${label.toLowerCase()}` : `Copy ${label.toLowerCase()}`}
        className="inline-flex max-w-full items-center gap-1 rounded border border-border bg-bg-secondary/70 px-1.5 py-0.5 text-[9px] text-text-muted hover:bg-bg-hover hover:text-text-primary"
      >
        <span>{label}</span>
        <code className="truncate font-mono text-text-secondary">{jobId}</code>
        <span className={copyState === 'failed' ? 'text-red-300' : copyState === 'copied' ? 'text-accent-green' : ''}>{feedback}</span>
      </button>
      <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {copyState === 'copied'
          ? `${label} ${jobId} copied`
          : copyState === 'failed'
            ? `${label} ${jobId} could not be copied`
            : ''}
      </span>
    </span>
  )
}

function describeResourceExecution(
  descriptor: api.ResourceDescriptor | null | undefined,
): ResourcePresentation | null {
  if (!descriptor) return null

  if (descriptor.intent === 'generation') {
    const label = descriptor.state === 'queued'
      ? 'Generation queued'
      : descriptor.state === 'admitted'
        ? 'Starting generation'
        : descriptor.state === 'blocked'
          ? 'Generation waiting'
          : descriptor.state === 'released'
            ? 'Generation no longer using the GPU'
            : 'Generation using the GPU'
    return {
      label,
      title: descriptor.state === 'blocked'
        ? 'This generation is waiting for enough compatible GPU resources.'
        : 'This generation is using the GPU.',
      tone: descriptor.state === 'blocked' || descriptor.state === 'released' ? 'neutral' : 'accelerated',
    }
  }

  if (descriptor.state === 'preemption_requested') {
    return {
      label: 'Faster restart requested',
      title: 'GPU acceleration may finish sooner. If Maestro can make the switch, this CPU text task will restart from the beginning and its current progress will not carry over.',
      warning: 'If the switch proceeds, this CPU text task will restart from the beginning with GPU acceleration.',
      tone: 'transition',
    }
  }
  if (descriptor.state === 'resources_releasing') {
    return {
      label: 'Preparing to restart faster',
      title: 'The CPU text task has stopped. Maestro is waiting for it to close fully before restarting with GPU acceleration.',
      warning: 'The CPU progress was discarded; the task will restart from the beginning.',
      tone: 'transition',
    }
  }
  if (descriptor.state === 'restarting_on_accelerator') {
    return {
      label: 'Restarting with GPU acceleration',
      title: 'This text task is restarting from the beginning with GPU acceleration. An ETA will appear after it starts.',
      warning: 'Restarting from the beginning; the ETA is not known yet.',
      tone: 'transition',
    }
  }

  if (descriptor.execution === 'cpu') {
    const label = descriptor.state === 'queued'
      ? 'Text queued for CPU'
      : descriptor.state === 'admitted'
        ? 'Starting text on CPU'
        : descriptor.state === 'blocked'
          ? 'Text waiting for CPU'
          : descriptor.state === 'released'
            ? 'Text task no longer using CPU'
            : 'Text using CPU · slower'
    return {
      label,
      title: descriptor.preemptible
        ? CPU_RESTART_WARNING
        : 'This text task is using the CPU, which is usually slower than GPU acceleration.',
      ...(descriptor.preemptible ? {
        warning: 'Maestro may restart this task from the beginning with GPU acceleration, but only when that is expected to finish sooner.',
      } : {}),
      tone: descriptor.state === 'blocked' || descriptor.state === 'released' ? 'neutral' : 'cpu',
    }
  }

  return {
    label: descriptor.state === 'queued'
      ? 'Accelerated text queued'
      : descriptor.state === 'admitted'
        ? 'Starting accelerated text'
        : descriptor.state === 'blocked'
          ? 'Accelerated text waiting'
          : descriptor.state === 'released'
            ? 'Text task no longer using GPU'
            : 'Text using GPU acceleration',
    title: 'This planning or review task is using GPU acceleration.',
    tone: descriptor.state === 'blocked' || descriptor.state === 'released' ? 'neutral' : 'accelerated',
  }
}

function resourcePresentationClass(tone: ResourcePresentation['tone']): string {
  if (tone === 'cpu') return 'border-amber-300/35 bg-amber-300/10 text-amber-200'
  if (tone === 'transition') return 'border-violet-300/35 bg-violet-300/10 text-violet-200'
  if (tone === 'accelerated') return 'border-accent-green/30 bg-accent-green/10 text-accent-green'
  return 'border-border bg-bg-secondary text-text-secondary'
}

function h3EstimatedRuntime(job: GenerationJob): number | null {
  const estimate = job.h3Estimate
  if (!estimate) return null
  const run = Number(estimate.seconds || 0)
  const load = estimate.model_load_state === 'resident'
    ? 0
    : Number(estimate.model_load_seconds || 0)
  const total = run + load
  return Number.isFinite(total) && total > 0 ? total : null
}

function h3QueuedRuntime(job: GenerationJob): number | null {
  const remaining = job.etaSeconds
  if (remaining != null && Number.isFinite(remaining) && remaining >= 0) return remaining
  return h3EstimatedRuntime(job)
}

function estimateRuntime(estimate: GenerationJob['estimateAfterResume']): number | null {
  if (!estimate) return null
  const total = Number(estimate.seconds || 0) + Number(estimate.model_load_seconds || 0)
  return Number.isFinite(total) && total > 0 ? total : null
}

function WorkspaceSelector() {
  const workspaces = useStore(s => s.workspaces ?? [])
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const browsingUploads = useStore(s => s.browsingUploads)
  const switchWorkspace = useStore(s => s.switchWorkspace)
  const createWorkspace = useStore(s => s.createWorkspace)
  const unlockWorkspace = useStore(s => s.unlockWorkspace)
  const lockWorkspace = useStore(s => s.lockWorkspace)
  const lockAllWorkspaces = useStore(s => s.lockAllWorkspaces)
  const deleteWorkspace = useStore(s => s.deleteWorkspace)
  const loadWorkspaces = useStore(s => s.loadWorkspaces)
  const reconnectJobs = useStore(s => s.reconnectJobs)
  const resumeJobRecovery = useStore(s => s.resumeJobRecovery)
  const accessContext = useStore(s => s.accessContext)
  const accountProjectMigration = useStore(s => s.accountProjectMigration)
  const remote = accessContext?.remote === true
  const accountProjectAccessActive = api.isAccountProjectAccessActive(accessContext, accountProjectMigration)
  const accountAuthenticated = accessContext?.accounts?.authenticated === true
  const accountContentAvailable = !accountProjectAccessActive || accountAuthenticated
  const legacyProjectPasswordAccess = !accountProjectAccessActive
  const canCreateProject = accessContext?.account_project_creation_requires_account !== true
    || accessContext?.accounts?.authenticated === true
  const [open, setOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [unlockTarget, setUnlockTarget] = useState<string | null>(null)
  const [unlockPassword, setUnlockPassword] = useState('')
  const [unlockRemember, setUnlockRemember] = useState<api.WorkspaceRememberPolicy>('device')
  const [unlockRecoveryJobId, setUnlockRecoveryJobId] = useState<string | null>(null)
  const [unlockSelectAfter, setUnlockSelectAfter] = useState(false)
  const [unlockingTarget, setUnlockingTarget] = useState<string | null>(null)
  const [lockingTarget, setLockingTarget] = useState<string | null>(null)
  const [lockingAll, setLockingAll] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)
  const [creatingProject, setCreatingProject] = useState(false)
  const [passwordTarget, setPasswordTarget] = useState<Pick<api.Workspace, 'name' | 'password_protected'> | null>(null)
  const [passwordValue, setPasswordValue] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [passwordSaving, setPasswordSaving] = useState(false)
  const [passwordError, setPasswordError] = useState<string | null>(null)
  const [passwordNotice, setPasswordNotice] = useState<string | null>(null)
  const [confirmRemovePassword, setConfirmRemovePassword] = useState(false)
  const createProjectInFlightRef = useRef(false)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const projectDialogRef = useRef<HTMLDivElement>(null)
  const projectDialogTitleId = useId()
  const requiredProject = remote
    && (!accountProjectAccessActive || accessContext?.accounts?.authenticated === true)
    && (
    !activeWorkspace
    || !workspaces.some(workspace => (
      workspace.name === activeWorkspace
      && (accountProjectAccessActive || workspace.unlocked !== false)
    ))
  )
  const projectTriggerLabel = !accountContentAvailable
    ? 'Sign in'
    : browsingUploads ? 'Uploads' : (activeWorkspace || 'Select project')
  const projectTriggerAccessibleLabel = !accountContentAvailable
    ? 'Sign in to view projects and uploads'
    : browsingUploads
      ? `Browsing uploads. Current project: ${activeWorkspace || 'none'}. Open project selector`
      : activeWorkspace
        ? `Current project: ${activeWorkspace}. Open project selector`
        : 'Select or create a project'
  const unlockedProtectedCount = legacyProjectPasswordAccess ? workspaces.filter(workspace => (
    workspace.password_protected && workspace.unlocked
  )).length : 0

  const resetPasswordEditor = useCallback(() => {
    setPasswordTarget(null)
    setPasswordValue('')
    setPasswordConfirm('')
    setPasswordError(null)
    setPasswordNotice(null)
    setConfirmRemovePassword(false)
  }, [])

  const resetUnlockEditor = useCallback(() => {
    setUnlockTarget(null)
    setUnlockPassword('')
    setUnlockRemember('device')
    setUnlockRecoveryJobId(null)
    setUnlockSelectAfter(false)
  }, [])

  useEffect(() => {
    if (!accountProjectAccessActive) return
    resetPasswordEditor()
    resetUnlockEditor()
  }, [accountProjectAccessActive, resetPasswordEditor, resetUnlockEditor])

  const beginUnlock = useCallback((
    workspace: string,
    recoveryJobId: string | null = null,
    selectAfter = false,
  ) => {
    resetPasswordEditor()
    setCreating(false)
    setUnlockTarget(workspace)
    setUnlockPassword('')
    setUnlockRemember('device')
    setUnlockRecoveryJobId(recoveryJobId)
    setUnlockSelectAfter(selectAfter)
  }, [resetPasswordEditor])

  const handleDelete = async (name: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!workspaceAllowsPermission(
      workspaces.find(workspace => workspace.name === name),
      'project.delete',
    )) return
    if (confirmDelete !== name) {
      setConfirmDelete(name)
      setTimeout(() => setConfirmDelete(c => (c === name ? null : c)), 4000)
      return
    }
    setConfirmDelete(null)
    setDeleting(name)
    setDeleteError(null)
    try {
      await deleteWorkspace(name)
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : String(err))
      setTimeout(() => setDeleteError(null), 6000)
    } finally {
      setDeleting(null)
    }
  }

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (requiredProject) return
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false)
        setCreating(false)
        resetPasswordEditor()
        resetUnlockEditor()
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open, requiredProject, resetPasswordEditor, resetUnlockEditor])

  // A new Cloudflare browser has no active project by design.  Put the
  // project gate in front of them immediately instead of leaving Generate
  // disabled with no obvious next action.
  useEffect(() => {
    if (!requiredProject || open) return
    setOpen(true)
    setCreating(workspaces.length === 0)
  }, [open, requiredProject, workspaces.length])

  useEffect(() => {
    if (accountProjectAccessActive) return
    const nowSeconds = Date.now() / 1000
    const expiries = workspaces.flatMap(workspace => {
      if (!workspace.password_protected || !workspace.unlocked) return []
      return [workspace.unlock_expires_at, workspace.unlock_idle_expires_at]
        .filter((value): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0)
    })
    if (expiries.length === 0) return
    const nextExpiry = Math.min(...expiries)
    let cancelled = false
    let timer = 0
    const refreshAtExpiry = async () => {
      const refreshed = await loadWorkspaces()
      if (!refreshed && !cancelled) {
        timer = window.setTimeout(() => void refreshAtExpiry(), 5000)
      }
    }
    timer = window.setTimeout(
      () => void refreshAtExpiry(),
      nextExpiry <= nowSeconds
        ? 5000
        : Math.min(2_147_000_000, Math.max(250, (nextExpiry - nowSeconds) * 1000 + 250)),
    )
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [accountProjectAccessActive, loadWorkspaces, workspaces])

  useEffect(() => {
    const requestUnlock = (event: Event) => {
      const detail = (event as CustomEvent<{ workspace?: string; jobId?: string }>).detail
      const workspace = detail?.workspace || ''
      if (!workspace) return
      if (accountProjectAccessActive) {
        setDeleteError(null)
        void (async () => {
          const switched = await switchWorkspace(workspace)
          if (!switched) {
            setDeleteError('Could not open this project. Try again.')
            return
          }
          if (detail?.jobId) await resumeJobRecovery(detail.jobId)
          window.dispatchEvent(new CustomEvent(QUEUE_REFRESH_EVENT))
          setOpen(false)
        })().catch(error => {
          setDeleteError(error instanceof Error ? error.message : 'Could not open this project. Try again.')
        })
        return
      }
      setOpen(true)
      beginUnlock(workspace, detail?.jobId || null, true)
    }
    window.addEventListener(REQUEST_WORKSPACE_UNLOCK_EVENT, requestUnlock)
    return () => window.removeEventListener(REQUEST_WORKSPACE_UNLOCK_EVENT, requestUnlock)
  }, [accountProjectAccessActive, beginUnlock, resumeJobRecovery, switchWorkspace])

  useEffect(() => {
    if (!open || !requiredProject) return
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    const frame = window.requestAnimationFrame(() => projectDialogRef.current?.focus())
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        return
      }
      if (event.key !== 'Tab' || !projectDialogRef.current) return
      const focusable = Array.from(projectDialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ))
      if (focusable.length === 0) {
        event.preventDefault()
        projectDialogRef.current.focus()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && (document.activeElement === first || document.activeElement === projectDialogRef.current)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      window.cancelAnimationFrame(frame)
      document.removeEventListener('keydown', handleKeyDown)
      previousFocus?.focus()
    }
  }, [open, requiredProject])

  const normalizedNewProjectName = newName.trim().replace(/\s+/g, '-')
  const createProjectDisabled = creatingProject || !normalizedNewProjectName || (
    legacyProjectPasswordAccess
    && ((newPassword.length > 0 && newPassword.length < 8) || (remote && !newPassword))
  )

  const handleCreate = async () => {
    const name = normalizedNewProjectName
    const legacyPasswordInvalid = legacyProjectPasswordAccess && (
      (newPassword.length > 0 && newPassword.length < 8) || (remote && !newPassword)
    )
    if (
      !canCreateProject
      || !name
      || legacyPasswordInvalid
      || creatingProject
      || createProjectInFlightRef.current
    ) return
    createProjectInFlightRef.current = true
    setCreateError(null)
    setCreatingProject(true)
    try {
      await createWorkspace(name, accountProjectAccessActive ? undefined : newPassword || undefined)
      setNewName('')
      setNewPassword('')
      setCreating(false)
      setOpen(false)
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : 'Project creation failed')
    } finally {
      createProjectInFlightRef.current = false
      setCreatingProject(false)
    }
  }

  const handleUnlock = async () => {
    if (accountProjectAccessActive || !unlockTarget || unlockingTarget || lockingTarget || lockingAll) return
    const target = unlockTarget
    const password = unlockPassword
    const remember = unlockRemember
    const recoveryJobId = unlockRecoveryJobId
    const selectAfter = unlockSelectAfter || recoveryJobId !== null
    setUnlockPassword('')
    setDeleteError(null)
    setUnlockingTarget(target)
    try {
      await unlockWorkspace(target, password, remember)
      await reconnectJobs()
      if (selectAfter) {
        const switched = await switchWorkspace(target)
        if (!switched) throw new Error('Could not open this project. Try again.')
      }
      if (recoveryJobId) {
        await resumeJobRecovery(recoveryJobId)
      }
      window.dispatchEvent(new CustomEvent(QUEUE_REFRESH_EVENT))
      resetUnlockEditor()
      if (selectAfter && useStore.getState().activeWorkspace === target) {
        setOpen(false)
      }
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : 'Unlock failed')
    } finally {
      setUnlockingTarget(null)
    }
  }

  const handleLock = async (name: string, event: React.MouseEvent) => {
    event.stopPropagation()
    if (accountProjectAccessActive || unlockingTarget || lockingTarget || lockingAll) return
    setLockingTarget(name)
    setDeleteError(null)
    try {
      await lockWorkspace(name)
      if (unlockTarget === name) resetUnlockEditor()
      window.dispatchEvent(new CustomEvent(QUEUE_REFRESH_EVENT))
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : 'Project could not be locked')
    } finally {
      setLockingTarget(null)
    }
  }

  const handleLockAll = async () => {
    if (accountProjectAccessActive || unlockingTarget || lockingAll || lockingTarget) return
    setLockingAll(true)
    setDeleteError(null)
    try {
      await lockAllWorkspaces()
      resetUnlockEditor()
      window.dispatchEvent(new CustomEvent(QUEUE_REFRESH_EVENT))
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : 'Projects could not be locked')
    } finally {
      setLockingAll(false)
    }
  }

  const openPasswordEditor = (workspace: api.Workspace, event: React.MouseEvent) => {
    event.stopPropagation()
    if (accountProjectAccessActive || !workspaceAllowsPermission(workspace, 'project.lifecycle')) return
    setPasswordTarget({ name: workspace.name, password_protected: workspace.password_protected })
    setPasswordValue('')
    setPasswordConfirm('')
    setPasswordError(null)
    setPasswordNotice(null)
    setConfirmRemovePassword(false)
    setCreating(false)
    resetUnlockEditor()
  }

  const handlePasswordUpdate = async (remove = false) => {
    if (accountProjectAccessActive || !passwordTarget || remote || passwordSaving) return
    if (!remove) {
      if (passwordValue.length < 8) {
        setPasswordError('Enter a password with at least 8 characters.')
        return
      }
      if (passwordValue !== passwordConfirm) {
        setPasswordError('The two passwords do not match.')
        return
      }
    }
    setPasswordSaving(true)
    setPasswordError(null)
    setPasswordNotice(null)
    try {
      const result = await api.setWorkspacePassword(passwordTarget.name, remove ? '' : passwordValue)
      await loadWorkspaces()
      setPasswordTarget({ name: passwordTarget.name, password_protected: result.password_protected })
      setPasswordValue('')
      setPasswordConfirm('')
      setConfirmRemovePassword(false)
      setPasswordNotice(remove
        ? 'Password removed. This project is no longer available through remote access.'
        : passwordTarget.password_protected
          ? 'Password changed. This browser remains unlocked.'
          : 'Password set. This project can now be unlocked remotely.')
    } catch (error) {
      setPasswordError(error instanceof Error ? error.message : 'Password update failed. Try again.')
    } finally {
      setPasswordSaving(false)
    }
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => {
          if (!accountContentAvailable) return
          if (requiredProject) {
            setOpen(true)
            return
          }
          if (open) {
            setCreating(false)
            resetPasswordEditor()
            resetUnlockEditor()
          }
          setOpen(!open)
        }}
        className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-colors border border-border"
        title={projectTriggerAccessibleLabel}
        aria-label={projectTriggerAccessibleLabel}
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={!accountContentAvailable}
      >
        <FolderOpen size={12} />
        <span className="max-w-[120px] truncate md:hidden lg:inline">{projectTriggerLabel}</span>
      </button>

      {open && (
        <>
        {requiredProject && <div className="fixed inset-0 z-[100] bg-black/70" aria-hidden="true" />}
        <div
          ref={projectDialogRef}
          role="dialog"
          aria-modal={requiredProject ? 'true' : undefined}
          aria-labelledby={projectDialogTitleId}
          tabIndex={requiredProject ? -1 : undefined}
          className={requiredProject
            ? 'fixed left-1/2 top-1/2 z-[110] max-h-[min(88vh,680px)] w-[min(92vw,380px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-border bg-bg-secondary shadow-2xl focus:outline-none'
            : 'fixed left-2 right-2 top-14 z-[70] max-h-[calc(100vh-4rem)] overflow-y-auto rounded-lg border border-border bg-bg-secondary shadow-2xl sm:absolute sm:left-auto sm:right-0 sm:top-full sm:mt-1 sm:w-64 sm:max-h-[min(78vh,620px)]'}
        >
          <div className="px-2 py-1.5 border-b border-border">
            <div className="flex items-center justify-between gap-2">
              <span id={projectDialogTitleId} className="text-[10px] text-text-muted uppercase tracking-wider">
                {requiredProject
                  ? 'Choose a project to enter Maestro'
                  : accountProjectAccessActive
                    ? 'Projects'
                    : remote ? 'Projects — unlock with password' : 'Workspaces'}
              </span>
              {unlockedProtectedCount > 0 && (
                <button
                  type="button"
                  onClick={() => void handleLockAll()}
                  disabled={unlockingTarget !== null || lockingAll || lockingTarget !== null}
                  className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[9px] text-text-muted transition-colors hover:bg-bg-hover hover:text-amber-300 focus-visible:text-amber-300 disabled:cursor-wait disabled:opacity-50"
                  title={`Lock all ${unlockedProtectedCount} unlocked projects in this browser`}
                  aria-label={`Lock all ${unlockedProtectedCount} unlocked projects`}
                >
                  {lockingAll ? <Loader2 size={10} className="animate-spin" /> : <Lock size={10} />}
                  Lock all
                </button>
              )}
            </div>
            {requiredProject && (
              <p className="mt-1 text-[10px] leading-relaxed text-text-secondary">
                {accountProjectAccessActive
                  ? 'Choose a project available to your account, or create a new project.'
                  : 'Unlock an available project, or create a password-protected project for this browser.'}
              </p>
            )}
          </div>
          <div className="max-h-[200px] overflow-y-auto">
            {workspaces.map(ws => {
              const absoluteExpiry = Number(ws.unlock_expires_at)
              const idleExpiry = Number(ws.unlock_idle_expires_at)
              const validExpiries = [absoluteExpiry, idleExpiry].filter(value => Number.isFinite(value) && value > 0)
              const effectiveExpiry = validExpiries.length > 0 ? Math.min(...validExpiries) : null
              const unlockedTitle = ws.remember_policy === 'device'
                ? `Lock ${ws.name}. Remembered on this device${effectiveExpiry ? ` until ${new Date(effectiveExpiry * 1000).toLocaleString()}` : ''}.`
                : `Lock ${ws.name}. Unlocked for this session${effectiveExpiry ? ` until ${new Date(effectiveExpiry * 1000).toLocaleString()}` : ''}.`
              return (
              <div key={ws.name} className="flex items-center group hover:bg-bg-hover transition-colors">
                <button
                  onClick={async () => {
                    if (lockingAll || lockingTarget) return
                    if (legacyProjectPasswordAccess && ws.password_protected && !ws.unlocked) {
                      beginUnlock(ws.name, null, true)
                    } else {
                      await switchWorkspace(ws.name)
                      if (useStore.getState().activeWorkspace === ws.name) {
                        setOpen(false)
                      } else {
                        setDeleteError('Could not open this project. Try again.')
                      }
                    }
                  }}
                  className={`flex-1 min-w-0 text-left px-3 py-2 text-xs flex items-center justify-between ${
                    ws.name === activeWorkspace && !browsingUploads ? 'text-accent-blue' : 'text-text-secondary'
                  }`}
                >
                  <span className="flex min-w-0 items-center gap-1.5 truncate">
                    <span className="truncate">{ws.name}</span>
                    {legacyProjectPasswordAccess && ws.password_protected && ws.unlocked && (
                      <span className="shrink-0 text-[8px] uppercase tracking-wide text-accent-green">
                        {ws.remember_policy === 'device' ? 'remembered' : 'session'}
                      </span>
                    )}
                  </span>
                  {ws.name === activeWorkspace && !browsingUploads && <Check size={12} className="shrink-0" />}
                </button>
                {legacyProjectPasswordAccess && ws.password_protected && (
                  <button
                    type="button"
                    onClick={event => {
                      event.stopPropagation()
                      if (ws.unlocked) {
                        void handleLock(ws.name, event)
                      } else {
                        beginUnlock(ws.name)
                      }
                    }}
                    disabled={lockingAll || lockingTarget !== null || unlockingTarget !== null}
                    className={`shrink-0 rounded px-2 py-2 transition-colors focus-visible:bg-bg-hover ${
                      ws.unlocked
                        ? 'text-accent-green hover:text-amber-300 focus-visible:text-amber-300'
                        : 'text-amber-400 hover:text-accent-blue focus-visible:text-accent-blue'
                    } disabled:cursor-wait disabled:opacity-50`}
                    title={ws.unlocked ? unlockedTitle : `Unlock ${ws.name}`}
                    aria-label={ws.unlocked ? `Lock ${ws.name}` : `Unlock ${ws.name}`}
                  >
                    {lockingTarget === ws.name || unlockingTarget === ws.name
                      ? <Loader2 size={12} className="animate-spin" />
                      : ws.unlocked ? <LockOpen size={12} /> : <Lock size={12} />}
                  </button>
                )}
                {legacyProjectPasswordAccess && !remote && (!ws.password_protected || ws.unlocked) && workspaceAllowsPermission(ws, 'project.lifecycle') && (
                  <button
                    onClick={event => openPasswordEditor(ws, event)}
                    disabled={passwordSaving && passwordTarget?.name === ws.name}
                    className={`shrink-0 px-2 py-2 transition-colors hover:text-accent-blue focus-visible:text-accent-blue ${
                      passwordTarget?.name === ws.name ? 'text-accent-blue bg-accent-blue/10' : 'text-text-muted'
                    }`}
                    title={ws.password_protected ? `Change password for ${ws.name}` : `Set a password for ${ws.name}`}
                    aria-label={ws.password_protected ? `Change password for ${ws.name}` : `Set a password for ${ws.name}`}
                  >
                    {passwordSaving && passwordTarget?.name === ws.name
                      ? <Loader2 size={12} className="animate-spin" />
                      : <KeyRound size={12} />}
                  </button>
                )}
                {/* default IS the outputs folder itself — not deletable */}
                {ws.name !== 'default' && (accountProjectAccessActive || !remote || ws.unlocked) && workspaceAllowsPermission(ws, 'project.delete') && (
                  <button
                    onClick={e => handleDelete(ws.name, e)}
                    disabled={deleting === ws.name}
                    className={`px-2 py-2 shrink-0 transition-colors ${
                      confirmDelete === ws.name
                        ? 'text-red-400 bg-red-500/15'
                        : deleting === ws.name
                          ? 'text-text-muted cursor-wait'
                          : 'text-text-muted opacity-100 hover:text-red-400 md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100'
                    }`}
                    title={confirmDelete === ws.name
                      ? `Click again to permanently delete "${ws.name}" and its ${ws.file_count ?? 0} files`
                      : `Delete workspace (${ws.file_count ?? 0} files)`}
                  >
                    {deleting === ws.name ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                  </button>
                )}
              </div>
              )
            })}
          </div>
          {legacyProjectPasswordAccess && !remote && passwordTarget && (
            <div className="border-t border-border p-2 space-y-2">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-[11px] font-medium text-text-primary">
                    {passwordTarget.password_protected ? 'Change project password' : 'Set project password'}
                  </p>
                  <p className="mt-0.5 text-[9px] leading-relaxed text-text-muted">
                    <span className="font-medium text-text-secondary">{passwordTarget.name}</span>: manage the password used for Cloudflare project access.
                  </p>
                </div>
                <button
                  onClick={resetPasswordEditor}
                  className="shrink-0 rounded p-1 text-text-muted hover:bg-bg-hover hover:text-text-primary"
                  title="Close password settings"
                  aria-label="Close password settings"
                >
                  <X size={12} />
                </button>
              </div>
              <input
                type="password"
                value={passwordValue}
                onChange={event => {
                  setPasswordValue(event.target.value)
                  setPasswordError(null)
                  setPasswordNotice(null)
                }}
                placeholder={passwordTarget.password_protected ? 'New password (8+ characters)' : 'Password (8+ characters)'}
                aria-label={passwordTarget.password_protected ? 'New project password' : 'Project password'}
                autoComplete="new-password"
                className="w-full rounded border border-border bg-bg-tertiary px-2 py-1 text-xs text-text-primary focus:border-accent-blue focus:outline-none"
                autoFocus
              />
              <input
                type="password"
                value={passwordConfirm}
                onChange={event => {
                  setPasswordConfirm(event.target.value)
                  setPasswordError(null)
                  setPasswordNotice(null)
                }}
                onKeyDown={event => event.key === 'Enter' && void handlePasswordUpdate()}
                placeholder="Confirm password"
                aria-label="Confirm project password"
                autoComplete="new-password"
                className="w-full rounded border border-border bg-bg-tertiary px-2 py-1 text-xs text-text-primary focus:border-accent-blue focus:outline-none"
              />
              {passwordValue.length > 0 && passwordValue.length < 8 && (
                <p className="text-[9px] text-amber-400">Use at least 8 characters.</p>
              )}
              {passwordConfirm.length > 0 && passwordValue !== passwordConfirm && (
                <p className="text-[9px] text-amber-400">Passwords do not match.</p>
              )}
              {passwordError && <p role="alert" className="text-[10px] leading-snug text-red-400">{passwordError}</p>}
              {passwordNotice && <p role="status" className="text-[10px] leading-snug text-accent-green">{passwordNotice}</p>}
              <button
                onClick={() => void handlePasswordUpdate()}
                disabled={passwordSaving || passwordValue.length < 8 || passwordValue !== passwordConfirm}
                className="flex w-full items-center justify-center gap-1 rounded bg-accent-blue px-2 py-1 text-xs text-white hover:bg-accent-blue-hover disabled:opacity-50"
              >
                {passwordSaving && <Loader2 size={11} className="animate-spin" />}
                {passwordTarget.password_protected ? 'Change password' : 'Set password'}
              </button>
              {passwordTarget.password_protected && (
                confirmRemovePassword ? (
                  <div className="rounded border border-red-500/40 bg-red-500/10 p-2">
                    <p className="text-[9px] leading-relaxed text-red-300">
                      Removing this password closes Cloudflare access to the project until a new password is set.
                    </p>
                    <div className="mt-1.5 flex gap-1.5">
                      <button
                        onClick={() => void handlePasswordUpdate(true)}
                        disabled={passwordSaving}
                        className="flex-1 rounded bg-red-500/20 px-2 py-1 text-[10px] text-red-300 hover:bg-red-500/30 disabled:opacity-50"
                      >
                        Confirm removal
                      </button>
                      <button
                        onClick={() => setConfirmRemovePassword(false)}
                        disabled={passwordSaving}
                        className="rounded border border-border px-2 py-1 text-[10px] text-text-secondary hover:bg-bg-hover disabled:opacity-50"
                      >
                        Keep password
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    onClick={() => {
                      setConfirmRemovePassword(true)
                      setPasswordError(null)
                      setPasswordNotice(null)
                    }}
                    disabled={passwordSaving}
                    className="w-full rounded border border-red-500/30 px-2 py-1 text-[10px] text-red-300 hover:bg-red-500/10 disabled:opacity-50"
                  >
                    Remove password…
                  </button>
                )
              )}
            </div>
          )}
          {legacyProjectPasswordAccess && unlockTarget && (
            <div className="border-t border-border p-2 space-y-1.5">
              <p className="text-[10px] text-text-muted">Enter the project password to unlock <span className="font-medium text-text-secondary">{unlockTarget}</span>.</p>
              <div className="flex gap-1.5">
                <input
                  type="password"
                  value={unlockPassword}
                  onChange={event => setUnlockPassword(event.target.value)}
                  onKeyDown={event => event.key === 'Enter' && void handleUnlock()}
                  placeholder="Project password"
                  aria-label={`Password for ${unlockTarget}`}
                  autoComplete="current-password"
                  className="min-w-0 flex-1 rounded border border-border bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
                  autoFocus
                />
                <button
                  onClick={() => void handleUnlock()}
                  disabled={unlockingTarget !== null || lockingTarget !== null || lockingAll}
                  className="flex items-center gap-1 rounded bg-accent-blue px-2 py-1 text-xs text-white disabled:cursor-wait disabled:opacity-50"
                >
                  {unlockingTarget && <Loader2 size={11} className="animate-spin" />}
                  Unlock
                </button>
              </div>
              <label className="flex min-h-11 cursor-pointer items-start gap-1.5 rounded px-1 py-0.5 text-[9px] leading-relaxed text-text-muted hover:bg-bg-hover md:min-h-0">
                <input
                  type="checkbox"
                  checked={unlockRemember === 'device'}
                  onChange={event => setUnlockRemember(event.target.checked ? 'device' : 'session')}
                  className="mt-0.5"
                />
                <span>
                  <span className="text-text-secondary">Remember this device</span>
                  {' '}— uncheck for a shorter session unlock. You can relock this project at any time.
                </span>
              </label>
            </div>
          )}
          {deleteError && (
            <div className="px-3 py-1.5 text-[10px] text-red-400 border-t border-border leading-snug">{deleteError}</div>
          )}
          {/* Virtual Uploads view — browse user-uploaded media (read-only;
              generations keep saving to the real active workspace). */}
          {accountContentAvailable && !requiredProject && <div className="border-t border-border">
            <button
              onClick={() => { switchWorkspace('__uploads__'); setOpen(false) }}
              className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between hover:bg-bg-hover transition-colors ${
                browsingUploads ? 'text-accent-blue' : 'text-text-secondary'
              }`}
              title="Browse media you've uploaded — reuse as inputs"
            >
              <span className="flex items-center gap-1.5"><Upload size={12} /> Uploads</span>
              {browsingUploads && <Check size={12} />}
            </button>
          </div>}
          {canCreateProject && <div className="border-t border-border p-2">
            {creating ? (
              <form
                className="space-y-1.5"
                onSubmit={event => {
                  event.preventDefault()
                  void handleCreate()
                }}
              >
                <input
                  type="text"
                  value={newName}
                  onInput={event => setNewName(event.currentTarget.value)}
                  placeholder="workspace-name"
                  aria-label="Project name"
                  className="w-full bg-bg-tertiary border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-accent-blue"
                  autoFocus
                />
                {legacyProjectPasswordAccess && <input
                    type="password"
                    value={newPassword}
                    onChange={event => setNewPassword(event.target.value)}
                    placeholder={remote ? 'Required password (8+ chars)' : 'Optional password (8+ chars)'}
                    className="w-full bg-bg-tertiary border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-accent-blue"
                  />}
                {createError && <p className="text-[10px] leading-snug text-red-400">{createError}</p>}
                <button type="submit" disabled={createProjectDisabled} className="flex w-full items-center justify-center gap-1 px-2 py-1 text-xs bg-accent-blue text-white rounded hover:bg-accent-blue-hover disabled:opacity-50">
                  {creatingProject && <Loader2 size={11} className="animate-spin" />} {creatingProject ? 'Creating…' : 'Create project'}
                </button>
              </form>
            ) : (
              <button
                onClick={() => setCreating(true)}
                className="w-full text-left px-1 py-1 text-xs text-accent-blue hover:text-accent-blue-hover flex items-center gap-1"
              >
                <Plus size={12} /> {remote || accountProjectAccessActive ? 'New project' : 'New Workspace'}
              </button>
            )}
          </div>}
          {!remote && accessContext && (
            <div className="border-t border-border px-3 py-2 text-[9px] leading-relaxed text-text-muted">
              <span className={accessContext.cloudflare_enabled ? 'text-accent-green' : 'text-text-muted'}>
                Cloudflare access {accessContext.cloudflare_enabled ? 'enabled' : 'disabled'}.
              </span>{' '}
              {accessContext.cloudflare_enabled
                ? <>Share {accessContext.share_url ? <button onClick={() => void navigator.clipboard?.writeText(accessContext.share_url)} className="text-accent-blue underline">the configured URL</button> : 'the Cloudflare URL shown by Pinokio'}, then have the user {accountProjectAccessActive ? 'sign in and choose a project available to their account.' : 'select a project and enter its password.'}</>
                : 'Enable it locally in Maestro’s Pinokio Configure/ENVIRONMENT settings; remote users never receive machine controls.'}
            </div>
          )}
        </div>
        </>
      )}
    </div>
  )
}

// How many items to render beyond the viewport in each direction
const OVERSCAN = 5
// Info bar height + border/padding
const INFO_BAR_HEIGHT = 48
// aspect-video = 56.25% of width (16:9)
const ASPECT_RATIO = 0.5625
// Gap between items (tailwind space-y-3 = 12px)
const GAP = 12

function stripTimeSuffix(msg: string): string {
  return msg.replace(
    /\s*\|\s*(?:\d+:\d+(?::\d+)?|(?:\d+h\s+)?(?:\d+m\s+)?\d+(?:\.\d+)?s)\s*$/,
    '',
  ).trim()
}

function JobPlaceholder({
  job,
  canManageGeneration,
  referenceQuality = null,
  onStop,
  onDismiss,
  onToggleLog,
  onRecoveryAction,
  onReviewPlan,
  logOpen = false,
  logEvents = [],
  logError = null,
}: {
  job: GenerationJob
  canManageGeneration: boolean
  referenceQuality?: api.ProjectReferenceJobQualitySummary | null
  onStop: () => void
  onDismiss: () => void
  onToggleLog?: () => void
  onRecoveryAction?: (action: api.QueueRecoveryAction) => void
  onReviewPlan?: () => void
  logOpen?: boolean
  logEvents?: api.JobLogEvent[]
  logError?: string | null
}) {
  const models = useStore(s => s.models ?? [])
  const machineControls = useStore(s => s.accessContext?.machine_controls === true)
  const accessContext = useStore(s => s.accessContext)
  const accountProjectMigration = useStore(s => s.accountProjectMigration)
  const accountProjectAccessActive = api.isAccountProjectAccessActive(accessContext, accountProjectMigration)
  const ref2vaTermsAccepted = useStore(s => s.hostTerms?.minimax_h3_ref2va.accepted === true)
  const [reviewNowMs, setReviewNowMs] = useState(() => Date.now())
  useEffect(() => {
    if (job.status !== 'waiting_for_plan_approval' || job.planReviewDeadline == null) return
    const immediate = window.setTimeout(() => setReviewNowMs(Date.now()), 0)
    const timer = window.setInterval(() => setReviewNowMs(Date.now()), 250)
    return () => {
      window.clearTimeout(immediate)
      window.clearInterval(timer)
    }
  }, [job.planReviewDeadline, job.status])
  const planReviewSeconds = job.planReviewDeadline == null
    ? null
    : Math.max(0, job.planReviewDeadline - reviewNowMs / 1000)
  const planNeedsRef2VATerms = !ref2vaTermsAccepted && (
    job.h3SegmentPlan?.segments.some(
      segment => segment.model_type === 'minimax_h3_ref2va',
    ) === true
  )
  const planNeedsModelTerms = job.planReviewTermsRequired === true || planNeedsRef2VATerms
  const hasSteps = job.totalSteps > 0
  const progressPct = hasSteps ? (job.step / job.totalSteps) * 100 : job.progress * 100
  const hasWindows = (job.windowTotal ?? 0) > 1
  const currentStep = (job.windowTotalSteps ?? 0) > 0 ? (job.windowStep ?? 0) : job.step
  const currentTotalSteps = (job.windowTotalSteps ?? 0) > 0 ? (job.windowTotalSteps ?? 0) : job.totalSteps
  const hasExactCurrentSteps = currentTotalSteps > 0
  const progressUnit = job.modelType?.startsWith('minimax_h3') ? 'Segment' : 'Window'
  const overallPct = hasWindows
    ? Math.max(0, Math.min(100, job.overallProgress ?? job.progress * 100))
    : progressPct
  const windowPct = hasWindows
    ? Math.max(0, Math.min(100, hasExactCurrentSteps
        ? (currentStep / currentTotalSteps) * 100
        : (job.windowProgress ?? progressPct)))
    : progressPct
  const currentProgressIndeterminate = job.progressIndeterminate === true || !hasExactCurrentSteps
  const queuedH3Runtime = (job.status === 'queued' || job.status === 'waiting_for_plan_approval') && job.modelType?.startsWith('minimax_h3')
    ? h3QueuedRuntime(job)
    : null
  const phase = stripTimeSuffix(job.phase || job.message)
  const recoveryState = job.recoveryState
  const recoveryBlocked = job.recoveryBlocked === true
  const recoveryNotice = recoveryBlocked
    || job.recoveryInterrupted === true
    || recoveryState === 'interrupted'
    || recoveryState === 'restored'
    || recoveryState === 'retrying'
  const isFailed = (job.status === 'failed' || job.status === 'cancelled') && !recoveryNotice
  const isDeliveryOom = job.status === 'failed' && job.oomInfo?.stage === 'h3_delivery'
  const isDeliveryRecoveryChild = isDeliveryOom && job.oomInfo?.manual_retry_count != null
  const nativeRecoveryAvailable = isDeliveryOom
    && !isDeliveryRecoveryChild
    && job.oomInfo?.native_available === true
    && job.oomInfo.recoverable === true
  const deliveryTarget = job.oomInfo?.requested_target
    ? `${job.oomInfo.requested_target} output`
    : 'Requested output'
  const hasLocalEvents = (job.logEvents?.length || 0) > 0
  const canOpenLog = (!job.oomInfo || machineControls) && (hasLocalEvents || api.isBackendJobId(job.id))
  const errorText = job.error || job.message || (job.status === 'cancelled' ? 'Cancelled' : 'Generation failed')
  const failedChildJobId = job.status === 'failed' ? job.failedChildJobId : null
  const hasFailedChild = !!failedChildJobId
  const failedChildDetail = job.failureDetails?.detail || null
  const failedChildCode = job.failureDetails?.code || null
  const jobModelLabel = visibleModelName(job.modelType, models)
  const resourcePresentation = describeResourceExecution(job.resourceDescriptor)
  const recoveryAttemptLabel = Number.isInteger(job.recoveryAttempt)
    && Number.isInteger(job.recoveryAttemptLimit)
    && (job.recoveryAttemptLimit ?? 0) > 0
    ? `Recovery attempt ${job.recoveryAttempt} of ${job.recoveryAttemptLimit}.`
    : null
  const resourceWaitTitle = job.queueWaitReason === 'resource_wait' ? RESOURCE_WAIT_TITLE : undefined
  const queueWaitLabel = job.status !== 'running' && !isFailed && !recoveryBlocked ? ({
    held: 'Held — use Start next or Resume when ready',
    queue_paused: 'Queue paused — use Start next or Resume queue',
    registering: 'Adding to the queue',
    preparing: 'Preparing the generation',
    waiting_for_plan_approval: 'Generation plan ready for review',
    waiting_for_plan_terms: 'Waiting for required model terms',
    waiting_for_turn: 'Waiting for earlier queued work',
    waiting_for_active_generation: 'Waiting for another generation on this host',
    waiting_for_other_user: 'Waiting for another generation on this host',
    resource_wait: 'Waiting for available GPU resources',
    ready: 'Ready to start',
    running: 'Starting',
  } as const)[job.queueWaitReason || 'registering'] : null

  return (
    <div className={`rounded-xl border overflow-hidden ${
      isFailed
        ? 'border-red-500/30 bg-bg-tertiary'
        : recoveryNotice
          ? 'border-amber-400/40 bg-bg-tertiary'
          : 'border-accent-blue/30 bg-bg-tertiary'
    }`}>
      <div className="w-full aspect-video flex items-center justify-center relative">
        {/* Dismiss button (top-right, failed only) */}
        {isFailed && (
          <button
            type="button"
            onClick={onDismiss}
            className="absolute top-2 right-2 p-1.5 rounded-full bg-bg-active text-text-secondary hover:bg-red-600 hover:text-white transition-colors z-10"
            title="Dismiss generation"
            aria-label="Dismiss generation"
          >
            <X size={14} />
          </button>
        )}
        <div className="flex flex-col items-center gap-3 text-text-muted w-full max-w-md px-4">
          <Film size={40} className={isFailed ? 'text-red-400' : recoveryNotice ? 'text-amber-300' : 'animate-pulse'} />

          <div className="text-center w-full">
            <p className={`text-sm font-medium ${isFailed ? 'text-red-400' : recoveryNotice ? 'text-amber-300' : 'text-text-secondary'}`}>
              {isFailed
                ? (job.status === 'cancelled'
                    ? 'Cancelled'
                    : hasFailedChild
                      ? 'Reference Generation Failed'
                    : isDeliveryOom
                      ? isDeliveryRecoveryChild
                        ? 'Delivery Retry Failed'
                        : nativeRecoveryAvailable
                          ? 'Output Processing Failed After Generation'
                          : 'Delivery Failed'
                      : 'Generation Failed')
                : recoveryBlocked
                  ? 'Recovery Needed'
                  : recoveryState === 'restored'
                    ? 'Generation Restored'
                    : recoveryState === 'interrupted' || job.recoveryInterrupted
                      ? 'Generation Interrupted — Completed Parts Saved'
                      : recoveryState === 'retrying'
                        ? job.status === 'running' ? 'Recovery Running' : 'Recovery Queued'
                        : job.status === 'completed'
                          ? job.logicalJobKind === 'reference_pack_parent' ? 'Reference packs ready' : 'Generation complete'
                        : job.status === 'preparing'
                          ? job.phase === 'planning_generation' ? 'Planning generation' : 'Enhancing prompt'
                          : job.status === 'waiting_for_plan_approval'
                            ? 'Plan ready for review'
                            : job.status === 'queued' ? 'Queued...' : 'Generating...'}
            </p>
            {api.isBackendJobId(job.id) && (
              <div className="mt-1.5 flex justify-center">
                <CopyableJobId jobId={job.id} />
              </div>
            )}
            {referenceQuality && (
              <div className={`mx-auto mt-1.5 max-w-sm rounded border px-2 py-1 text-[9px] leading-relaxed ${referenceQuality.presentation.tone === 'pass' ? 'border-accent-green/30 bg-accent-green/10 text-accent-green' : referenceQuality.presentation.tone === 'residual' ? 'border-amber-400/30 bg-amber-400/10 text-amber-200' : 'border-border bg-bg-secondary/70 text-text-muted'}`} data-reference-fidelity={referenceQuality.presentation.tone}>
                <p className="font-medium">
                  Recommended · {referenceQuality.presentation.stateLabel}
                  {referenceQuality.presentation.gradeLabel ? ` · ${referenceQuality.presentation.gradeLabel}` : ''}
                  {referenceQuality.presentation.scoreLabel ? ` · ${referenceQuality.presentation.scoreLabel}` : ''}
                </p>
                {referenceQuality.presentation.preliminary && <p>Early recommendation · not graded yet</p>}
                {referenceQuality.presentation.residualSummary && <p>{referenceQuality.presentation.residualSummary}</p>}
                {referenceQuality.presentation.correctionAvailable && <p>Suggestions for improving the result are available.</p>}
                {referenceQuality.presentation.notice && <p>{referenceQuality.presentation.notice}</p>}
                {referenceQuality.candidateCount > 1 && <p>{referenceQuality.candidateCount} candidates remain available in Reference.</p>}
              </div>
            )}
            {!isFailed && (queueWaitLabel || phase) && (
              <p className="text-xs mt-1 truncate" title={resourceWaitTitle}>{queueWaitLabel || phase}</p>
            )}
            {!isFailed && resourcePresentation && (
              <div className="mt-1.5 flex flex-col items-center gap-1" data-resource-state={job.resourceDescriptor?.state}>
                <span
                  className={`rounded-full border px-2 py-0.5 text-[9px] font-medium ${resourcePresentationClass(resourcePresentation.tone)}`}
                  title={resourcePresentation.title}
                >
                  {resourcePresentation.label}
                </span>
                {resourcePresentation.warning && (
                  <p className="max-w-sm text-[9px] leading-relaxed text-text-muted">
                    {resourcePresentation.warning}
                  </p>
                )}
              </div>
            )}
            {job.status === 'waiting_for_plan_approval' && (
              <>
                {job.h3SegmentPlan && canManageGeneration && (
                  <button
                    type="button"
                    onClick={onReviewPlan}
                    className="mt-2 inline-flex items-center gap-1.5 rounded bg-accent-blue px-3 py-1.5 text-xs font-medium text-white hover:brightness-110"
                  >
                    <ListChecks size={13} /> Review plan
                  </button>
                )}
                <p className="mt-1 text-[10px] text-amber-200">
                  {planReviewSeconds == null
                    ? planNeedsRef2VATerms
                      ? 'Approval required to accept Ref2VA terms'
                      : planNeedsModelTerms
                        ? 'Approval required to accept model terms'
                        : 'Explicit plan approval required'
                    : planReviewSeconds > 0
                      ? `Maestro will accept this plan in ${Math.ceil(planReviewSeconds)}s unless you review it first`
                      : 'Maestro is accepting this plan…'}
                </p>
              </>
            )}
            {!isFailed && !recoveryBlocked && job.status !== 'completed' && (
              <p className="mt-1 text-[10px] text-text-secondary">
                {queuedH3Runtime != null
                  ? job.h3SegmentPlan?.segments.length
                    ? `Planned time ${formatApproximateDuration(queuedH3Runtime)} after start`
                    : `Estimated time ${formatApproximateDuration(queuedH3Runtime)} after start`
                  : `Overall ETA ${formatApproximateDuration(job.etaSeconds)}`}
                {job.status === 'running' && hasWindows && job.modelType?.startsWith('minimax_h3')
                    ? ` · Current segment ETA ${formatApproximateDuration(job.subtaskEtaSeconds)}`
                    : ''}
              </p>
            )}
            {recoveryBlocked && (
              <div className="mt-2 rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-left text-[10px] text-text-secondary">
                <p className="font-medium text-amber-200">
                  {job.recoveryReasonText || 'This generation needs your choice before it can continue.'}
                </p>
                {recoveryAttemptLabel && <p className="mt-1">{recoveryAttemptLabel}</p>}
                {job.recoveryRerunsDenoise && (
                  <p className="mt-1">
                    The current part will restart from the beginning; completed parts will stay saved.
                  </p>
                )}
                {estimateRuntime(job.estimateAfterResume) != null && (
                  <p className="mt-1">
                    Estimated work after resume: {formatApproximateDuration(estimateRuntime(job.estimateAfterResume))}.
                  </p>
                )}
                {!!job.recoveryActions?.length && canManageGeneration && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {job.recoveryActions.map(action => (
                      <button
                        key={action}
                        type="button"
                        onClick={() => onRecoveryAction?.(action)}
                        className="rounded bg-amber-300/15 px-2.5 py-1 text-[10px] font-medium text-amber-200 hover:bg-amber-300/25"
                      >
                        {action === 'resume'
                          ? recoveryState === 'blocked_remote_reauth'
                            ? accountProjectAccessActive ? 'Open project and resume' : 'Unlock project and resume'
                            : 'Resume recovery'
                          : 'Retry recovery'}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {hasWindows && !isFailed && !recoveryBlocked && (
              <div className="mt-2 space-y-1.5 text-left">
                <div className="flex items-center justify-between text-[10px] text-text-secondary">
                  <span>Overall</span>
                  <span>{progressUnit} {job.windowCurrent || 1}/{job.windowTotal}</span>
                </div>
                <div className="w-full bg-bg-active rounded-full h-1.5 overflow-hidden">
                  <div className="h-full bg-accent-blue rounded-full transition-all duration-300" style={{ width: `${overallPct}%` }} />
                </div>
                <div className="flex items-center justify-between text-[10px] text-text-secondary">
                  <span>Current {progressUnit.toLowerCase()}</span>
                  <span>{hasExactCurrentSteps ? `Step ${currentStep}/${currentTotalSteps}` : 'Preparing'}</span>
                </div>
                <div className="w-full bg-bg-active rounded-full h-1.5 overflow-hidden">
                  {currentProgressIndeterminate ? (
                    <div className="h-full w-full animate-pulse rounded-full bg-accent-green/60" />
                  ) : (
                    <div className="h-full bg-accent-green rounded-full transition-all duration-300" style={{ width: `${windowPct}%` }} />
                  )}
                </div>
              </div>
            )}
            {hasSteps && !hasWindows && !isFailed && !recoveryBlocked && (
              <p className="text-[10px] text-text-muted mt-0.5">
                Step {job.step}/{job.totalSteps}
              </p>
            )}
            {!job.modelType?.startsWith('minimax_h3') && (job.activeWindowPrompt || job.promptPreview) && (
              <div className="mt-2 rounded-md border border-border bg-bg-secondary/80 px-2.5 py-2 text-left">
                <p className="text-[9px] uppercase tracking-wide text-text-muted mb-1">
                  {job.activeWindowPrompt && hasWindows ? `Active ${progressUnit.toLowerCase()} prompt` : 'Prompt'}
                </p>
                <p className="text-[11px] text-text-secondary line-clamp-3 whitespace-pre-wrap break-words">
                  {job.activeWindowPrompt || job.promptPreview}
                </p>
              </div>
            )}
            {(jobModelLabel || job.workspace) && (
              <p className="mt-1.5 text-[9px] text-text-muted truncate">
                {[jobModelLabel, job.workspace && `Project: ${job.workspace}`].filter(Boolean).join(' · ')}
              </p>
            )}
            {!!job.h3SegmentPlan?.segments.length && !isFailed && (
              <div className="mt-2 rounded-md border border-border bg-bg-secondary/80 px-2.5 py-2 text-left">
                <div className="mb-1.5 flex items-center justify-between text-[9px] uppercase tracking-wide text-text-muted">
                  <span>Planned segments {job.h3SegmentPlan.clip_count}</span>
                  <span>{job.h3SegmentPlan.checkpoint_switches} model switch{job.h3SegmentPlan.checkpoint_switches === 1 ? '' : 'es'}</span>
                </div>
                <div className="flex gap-1 overflow-x-auto pb-0.5">
                  {job.h3SegmentPlan.segments.map(segment => {
                    const active = (job.windowCurrent || 0) === segment.index
                    const ref2va = segment.model_type === 'minimax_h3_ref2va'
                    const boundary = segment.boundary_from_previous?.type
                    const boundaryLabel = visibleBoundaryName(boundary)
                    const segmentModelLabel = visibleModelName(segment.model_type, models)
                    const segmentPurpose = h3SegmentPurpose(segment.model_type)
                    const generatedFrames = segment.generated_frames ?? segment.frames
                    const publishedFrames = segment.published_frames ?? generatedFrames
                    const generatedSeconds = segment.generated_duration_seconds ?? segment.duration_seconds
                    const publishedSeconds = segment.published_duration_seconds ?? generatedSeconds
                    return (
                      <div
                        key={segment.index}
                        title={`Segment ${segment.index}: ${segmentModelLabel} · ${formatMediaDuration(publishedSeconds)} published (${publishedFrames}f)${generatedFrames !== publishedFrames ? ` · ${formatMediaDuration(generatedSeconds)} generated (${generatedFrames}f)` : ''} · ${segmentPurpose}${boundary ? ` · ${boundaryLabel}` : ''}`}
                        className={`min-w-[44px] rounded border px-1.5 py-1 text-center transition-colors ${
                          active ? 'border-white/70 ring-1 ring-white/30' : 'border-transparent'
                        } ${ref2va ? 'bg-violet-500/25 text-violet-200' : 'bg-sky-500/25 text-sky-200'}`}
                      >
                        <div className="text-[9px] font-semibold">{segment.index} · {ref2va ? 'REF' : 'FL'}</div>
                        <div className="text-[8px] opacity-75">{boundaryLabel}</div>
                      </div>
                    )
                  })}
                </div>
                {job.currentSegmentReason && (
                  <p className="mt-1.5 truncate text-[9px] text-text-muted">
                    {visibleModelName(job.currentSegmentModel, models) || 'Current model'}: {h3SegmentPurpose(job.currentSegmentModel)}
                  </p>
                )}
              </div>
            )}
            {isFailed && (
              <div className="mt-2 text-left">
                <p className="text-center text-[11px] text-text-secondary">
                  {job.status === 'cancelled'
                    ? 'This generation was cancelled.'
                    : nativeRecoveryAvailable
                      ? `Generation finished, but creating the ${deliveryTarget.toLowerCase()} ran out of GPU memory${(job.oomInfo?.retry_count ?? 0) > 0 ? ' after one automatic retry' : ''}. Maestro saved the original result privately; recovery options are below.`
                      : isDeliveryRecoveryChild
                        ? 'The delivery retry failed. Updated recovery options are on the original failed generation.'
                      : isDeliveryOom
                        ? `Creating the ${deliveryTarget.toLowerCase()} ran out of GPU memory after generation, and the original result was not saved for recovery.`
                    : canOpenLog
                      ? 'Generation failed. Open technical details or event history for more information.'
                      : 'Generation failed before a server job was created. The technical details below contain the available error.'}
                </p>
                {failedChildJobId && (
                  <div className="mt-2 space-y-1 rounded border border-red-500/25 bg-red-500/10 px-2.5 py-2 text-[10px] text-text-secondary" data-reference-child-failure>
                    <div className="flex justify-center">
                      <CopyableJobId jobId={failedChildJobId} label="Child job ID" />
                    </div>
                    {job.failedChildStatus && (
                      <p><span className="text-text-muted">Child status:</span> {visibleJobStatus(job.failedChildStatus)}</p>
                    )}
                    {job.failedChildReason && (
                      <p><span className="text-text-muted">Reason:</span> <code className="font-mono">{job.failedChildReason}</code></p>
                    )}
                    {failedChildCode && (
                      <p><span className="text-text-muted">Code:</span> <code className="font-mono">{failedChildCode}</code></p>
                    )}
                    {failedChildDetail && (
                      <p className="whitespace-pre-wrap break-words"><span className="text-text-muted">Detail:</span> {failedChildDetail}</p>
                    )}
                  </div>
                )}
                {isDeliveryOom && !isDeliveryRecoveryChild && job.workspace && api.isBackendJobId(job.id) && canManageGeneration && (
                  <div className="mt-2">
                    <H3DeliveryRecoveryStatus
                      sourceJobId={job.id}
                      workspace={job.workspace}
                      compact
                    />
                  </div>
                )}
                {job.status !== 'cancelled' && (!job.oomInfo || machineControls) && (
                  <details className="mt-2 rounded border border-border bg-bg-secondary/70 px-2 py-1.5 text-[10px] text-text-muted">
                    <summary className="cursor-pointer text-text-secondary">Technical details</summary>
                    <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words font-mono">{errorText}</pre>
                  </details>
                )}
              </div>
            )}
          </div>

          {/* Progress bar — hidden when failed */}
          {!isFailed && !hasWindows && !recoveryBlocked && (
            <div className="w-full bg-bg-active rounded-full h-1.5 overflow-hidden">
              {progressPct > 0 ? (
                <div
                  className="h-full bg-accent-green rounded-full transition-all duration-300"
                  style={{ width: `${progressPct}%` }}
                />
              ) : (
                <div className="h-full bg-accent-green/60 rounded-full animate-pulse w-full" />
              )}
            </div>
          )}
        </div>
      </div>

      {/* Bottom bar */}
      <div className="px-3 py-2 min-h-[40px] flex items-center justify-between">
        <div className="text-[11px] text-text-muted truncate flex-1">
          {recoveryBlocked
            ? 'Recovery needs your choice · queue position and ETA will return afterward'
            : isFailed
            ? nativeRecoveryAvailable
              ? 'Output processing failed · recovery options are shown above'
              : isDeliveryRecoveryChild
                ? 'Delivery retry failed · return to the original failed generation'
              : 'Click × to dismiss — the tile stays so you can see what failed'
            : queueWaitLabel || phase || 'Preparing...'}
        </div>
        {!isFailed && canManageGeneration && (
          <button
            type="button"
            onClick={onStop}
            className="ml-2 flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1 text-xs text-red-400 transition-colors hover:text-red-300 md:min-h-0 md:min-w-0"
          >
            <Square size={11} />
            Stop
          </button>
        )}
        {isFailed && canOpenLog && onToggleLog && (
          <button
            onClick={onToggleLog}
            className="ml-2 shrink-0 rounded border border-border px-2 py-1 text-[10px] text-text-secondary hover:bg-bg-hover hover:text-text-primary"
          >
            {logOpen ? 'Hide event history' : hasLocalEvents ? 'Show recorded events' : 'Load job event history'}
          </button>
        )}
      </div>
      {logOpen && (
        <div className="max-h-48 overflow-y-auto border-t border-border bg-bg-primary p-2 font-mono text-[9px] text-text-muted">
          {logError ? (
            <div className="rounded border border-red-500/30 bg-red-500/10 px-2 py-1.5 font-sans text-[10px] text-red-300">
              {logError}
            </div>
          ) : logEvents.length === 0 ? 'No recorded events.' : logEvents.map((event, eventIndex) => (
            <div key={`${event.at}-${eventIndex}`} className="border-b border-border/40 py-1 last:border-0">
              <span className="text-text-secondary">{new Date(event.at * 1000).toLocaleTimeString()}</span>{' '}
              <span>{visibleJobStatus(event.status)} · {event.progress}%</span>{' '}
              <span>{event.message || event.phase}</span>
              {event.total_steps > 0 && <span> · {event.step}/{event.total_steps}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function queueSummaryLabel(summary: api.QueueState['summary']): string {
  return `${summary.running} running · ${summary.preparing ?? 0} preparing · ${summary.approval_waiting ?? 0} awaiting review · ${summary.waiting} waiting · ${summary.held} held · ${summary.registering} being added`
}

function queuePositionLabel(position: number | null, waiting: number): string {
  if (position == null) return 'Adding to the queue'
  if (waiting < 1 || position > waiting) return 'Waiting in queue'
  if (position === 1) return waiting > 1 ? `Next in line · 1 of ${waiting}` : 'Next in line'
  const ahead = position - 1
  return `${ahead} ${ahead === 1 ? 'job' : 'jobs'} ahead · ${position} of ${waiting}`
}

function sampleInterventionLabel(value: string): string {
  const words = value
    .replace(/^(?:maestro|comparison|control)[.:_+-]+/i, '')
    .replace(/[.:_+~-]+/g, ' ')
    .trim()
  if (!words) return 'Maestro workflow changes'
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function samplePairStateCopy(state: api.SampleCampaignQueueState): string {
  if (state === 'held') return 'Waiting for spare GPU time.'
  if (state === 'running_arm') return 'One side is running or ready to start.'
  if (state === 'outputs_unbound') {
    return 'Both runs finished, but their outputs are not yet linked as review evidence.'
  }
  return 'This comparison stopped before both sides were ready.'
}

function sampleArmStatusCopy(arm: api.SampleCampaignQueueArm): string {
  if (arm.status === 'queued') {
    return arm.queue_held ? 'Waiting for spare GPU time' : 'Queued to start'
  }
  if (arm.status === 'running') return `Generating · ${Math.round(arm.progress)}%`
  if (arm.status === 'completed') {
    const noun = arm.output_count === 1 ? 'output' : 'outputs'
    return `${arm.output_count} ${noun} ready · not linked for review yet`
  }
  return 'Stopped before this side was ready'
}

function sampleInterventionCopy(maestroChanges: string[], controlChanges: string[]): string {
  if (maestroChanges.length > 0 && controlChanges.length > 0) {
    return `Maestro adds ${maestroChanges.join(', ')}; the comparison adds ${controlChanges.join(', ')}.`
  }
  if (maestroChanges.length > 0) {
    return `Maestro adds ${maestroChanges.join(', ')}; the comparison runs without those changes.`
  }
  return `The comparison adds ${controlChanges.join(', ')}; Maestro runs without those changes.`
}

function SampleCampaignQueueSection({ pairs }: { pairs: api.SampleCampaignQueuePair[] }) {
  if (pairs.length === 0) return null
  return (
    <section
      aria-labelledby="sample-campaign-queue-title"
      className="space-y-2 rounded-lg border border-violet-400/25 bg-violet-400/5 px-3 py-3"
    >
      <div>
        <h2 id="sample-campaign-queue-title" className="text-xs font-medium text-text-primary">
          Comparative samples
        </h2>
        <p className="mt-0.5 text-[10px] leading-relaxed text-text-muted">
          Matched runs compare two workflow variants using the same generation setup.
        </p>
      </div>
      <div className="space-y-2">
        {pairs.map((entry, index) => {
          const maestroChanges = entry.pair.intervention_delta.maestro_only.map(sampleInterventionLabel)
          const controlChanges = entry.pair.intervention_delta.control_only.map(sampleInterventionLabel)
          return (
            <article
              key={entry.pair.pair_id}
              className="min-w-0 rounded-md border border-border bg-bg-secondary px-2.5 py-2"
              aria-label={`Matched sample ${index + 1}`}
            >
              <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
                <div className="min-w-0">
                  <p className="text-[11px] font-medium text-text-primary">Matched sample {index + 1}</p>
                  <p className="mt-0.5 text-[10px] leading-relaxed text-text-muted">
                    {samplePairStateCopy(entry.queue_state)}
                  </p>
                </div>
                <span className="rounded-full border border-violet-400/25 bg-violet-400/10 px-2 py-0.5 text-[9px] text-violet-200">
                  Paired comparison
                </span>
              </div>
              <p className="mt-1.5 break-words text-[10px] leading-relaxed text-text-secondary">
                {sampleInterventionCopy(maestroChanges, controlChanges)}
              </p>
              <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                {entry.arms.map(arm => (
                  <div key={arm.arm} className="min-w-0 rounded border border-border/70 bg-bg-primary/45 px-2 py-1.5">
                    <p className="text-[10px] font-medium text-text-primary">
                      {arm.arm === 'maestro' ? 'Maestro workflow' : 'Matched comparison workflow'}
                    </p>
                    <p className="mt-0.5 text-[10px] text-text-muted">{sampleArmStatusCopy(arm)}</p>
                  </div>
                ))}
              </div>
              <p className="mt-2 text-[10px] leading-relaxed text-text-muted">
                No visual-model review or owner review has been recorded yet.
              </p>
            </article>
          )
        })}
      </div>
    </section>
  )
}

function PromptEnhanceQueueCard() {
  const card = useStore(state => state.enhanceQueueCard)
  const activeWorkspace = useStore(state => state.activeWorkspace)
  const cancel = useStore(state => state.cancelEnhancePrompt)
  const handleUseAndGenerate = useStore(state => state.useCompletedEnhanceAndGenerate)
  if (!card || card.workspace !== activeWorkspace) return null

  const operation = card.status && 'request_id' in card.status ? card.status : null
  const text = card.result?.enhanced || operation?.partial_text || ''
  const stage = operation?.stage || operation?.phase || card.status?.phase || card.phase
  const stateLabel = card.phase === 'preparing'
    ? 'Preparing'
    : card.phase === 'queued'
      ? 'Queued'
    : card.phase === 'running'
      ? 'Writing'
      : card.phase === 'completed' ? 'Completed' : 'Needs attention'
  const isActive = card.phase === 'preparing' || card.phase === 'queued' || card.phase === 'running'

  return (
    <article
      data-prompt-enhance-queue-card
      className="rounded-lg border border-accent-blue/30 bg-accent-blue/5 px-3 py-3"
      aria-labelledby="prompt-enhance-queue-title"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Sparkles size={14} className="shrink-0 text-accent-blue" aria-hidden="true" />
            <h2 id="prompt-enhance-queue-title" className="text-xs font-medium text-text-primary">Prompt Enhance</h2>
          </div>
          <p className="mt-1 text-[10px] text-text-muted">
            {stateLabel} · {stage.replaceAll('_', ' ')} · {card.workspace}
          </p>
        </div>
        {isActive && (
          <button
            type="button"
            onClick={() => void cancel()}
            className="rounded-md border border-border px-2.5 py-1 text-[10px] text-text-secondary hover:bg-bg-hover hover:text-text-primary"
          >
            Cancel
          </button>
        )}
      </div>
      {text && (
        <div
          role="status"
          aria-live={isActive ? 'polite' : 'off'}
          className="mt-2 whitespace-pre-wrap break-words rounded-md border border-border bg-bg-primary/45 px-3 py-2 text-xs leading-relaxed text-text-secondary"
        >
          {text}
        </div>
      )}
      {card.error && <p className="mt-2 text-[10px] leading-relaxed text-red-300">{card.error}</p>}
      {card.phase === 'completed' && card.result && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-[10px] leading-relaxed text-text-muted">
            {card.resultApplied
              ? 'Applied to the open Generate prompt. No generation was started.'
              : 'Ready for Generate. Opening Generate applies this exact scoped result; it will not start by itself.'}
          </p>
          <button
            type="button"
            onClick={() => void handleUseAndGenerate()}
            className="rounded-md bg-accent-blue px-3 py-1.5 text-[10px] font-medium text-white hover:bg-accent-blue-hover"
          >
            Use &amp; Generate
          </button>
        </div>
      )}
    </article>
  )
}

function QueuePanel({
  jobs,
  sampleCampaignPairs,
  onStop,
  onDismiss,
  queue,
  queueError,
  queueLastSuccessAt,
  refreshQueue,
}: {
  jobs: GenerationJob[]
  sampleCampaignPairs: api.SampleCampaignQueuePair[]
  onStop: (jobId: string) => void
  onDismiss: (jobId: string) => void
  queue: api.QueueState | null
  queueError: string | null
  queueLastSuccessAt: number | null
  refreshQueue: () => Promise<void>
}) {
  const machineControls = useStore(s => s.accessContext?.machine_controls === true)
  const workspaces = useStore(s => s.workspaces ?? [])
  const resumeJobRecovery = useStore(s => s.resumeJobRecovery)
  const retryJobRecovery = useStore(s => s.retryJobRecovery)
  const openH3PlanReview = useStore(s => s.openH3PlanReview)
  const planReviewError = useStore(s => s.h3PlanReviewError)
  const enhanceQueueCard = useStore(s => s.enhanceQueueCard)
  const queueActiveWorkspace = useStore(s => s.activeWorkspace)
  const projectPermissionsProjected = workspaces.some(
    workspace => workspace.project_permissions !== undefined,
  )
  const [error, setError] = useState<string | null>(null)
  const [countDrafts, setCountDrafts] = useState<Record<string, number>>({})
  const [logJobId, setLogJobId] = useState<string | null>(null)
  const [logEvents, setLogEvents] = useState<api.JobLogEvent[]>([])
  const [logError, setLogError] = useState<string | null>(null)

  const projection = useMemo(
    () => projectLogicalQueue(jobs, queue?.jobs),
    [jobs, queue?.jobs],
  )
  const { visibleJobs } = projection
  const emptyState = queuePanelEmptyState(
    queue,
    queueError,
    queueLastSuccessAt,
    visibleJobs.length,
  )
  const hasEnhanceCard = enhanceQueueCard?.workspace === queueActiveWorkspace
  const panelEmptyState = hasEnhanceCard && emptyState === 'empty' ? 'none' : emptyState
  const referenceQualityTargetKey = JSON.stringify(visibleJobs.flatMap(job => (
    job.logicalJobKind === 'reference_pack_parent'
      && job.status === 'completed'
      && job.workspace
      ? [{ jobId: job.id, project: job.workspace }]
      : []
  )).sort((left, right) => left.jobId.localeCompare(right.jobId)))
  const [referenceQualityByJobId, setReferenceQualityByJobId] = useState<
    Record<string, api.ProjectReferenceJobQualitySummary>
  >({})

  useEffect(() => {
    const targets = JSON.parse(referenceQualityTargetKey) as Array<{ jobId: string; project: string }>
    if (targets.length === 0) return
    let current = true
    const projects = [...new Set(targets.map(target => target.project))]
    void Promise.all(projects.map(async project => {
      try {
        return [project, await api.fetchProjectAssets(project)] as const
      } catch {
        return [project, [] as api.ProjectAsset[]] as const
      }
    })).then(results => {
      if (!current) return
      const assetsByProject = new Map(results)
      const next: Record<string, api.ProjectReferenceJobQualitySummary> = {}
      for (const target of targets) {
        const summary = api.projectReferenceJobQualitySummary(
          assetsByProject.get(target.project) ?? [],
          target.jobId,
        )
        if (summary) next[target.jobId] = summary
      }
      setReferenceQualityByJobId(next)
    })
    return () => { current = false }
  }, [referenceQualityTargetKey])

  const act = async (action: () => Promise<unknown>) => {
    try {
      await action()
      await refreshQueue()
      setError(null)
    } catch (reason) {
      await refreshQueue().catch(() => {})
      setError(reason instanceof Error ? reason.message : 'Queue action failed')
    }
  }

  const toggleLog = async (job: GenerationJob) => {
    if (logJobId === job.id) {
      setLogJobId(null)
      setLogError(null)
      return
    }
    if (job.logEvents && job.logEvents.length > 0) {
      setLogEvents(job.logEvents)
      setLogJobId(job.id)
      setError(null)
      setLogError(null)
      return
    }
    if (!api.isBackendJobId(job.id)) return
    try {
      const result = await api.fetchJobLog(job.id)
      setLogEvents(result.events)
      setLogJobId(job.id)
      setError(null)
      setLogError(null)
    } catch (reason) {
      setLogEvents([])
      setLogJobId(job.id)
      setLogError(reason instanceof Error ? reason.message : 'Job event history is unavailable')
    }
  }

  const recover = (job: GenerationJob, action: api.QueueRecoveryAction) => {
    if (!job.recoveryActions?.includes(action)) return
    if (action === 'resume' && job.recoveryState === 'blocked_remote_reauth') {
      if (!job.workspace) {
        setError('Select the recovery project before resuming.')
        return
      }
      window.dispatchEvent(new CustomEvent(REQUEST_WORKSPACE_UNLOCK_EVENT, {
        detail: { workspace: job.workspace, jobId: job.id },
      }))
      return
    }
    void act(() => action === 'resume'
      ? resumeJobRecovery(job.id)
      : retryJobRecovery(job.id))
  }

  return (
    <div className="flex-1 overflow-y-auto p-3 [&_button]:min-h-11 [&_button]:min-w-11 [&_input:not([type=checkbox])]:min-h-11 [&_summary]:min-h-11 md:p-4 md:[&_button]:min-h-0 md:[&_button]:min-w-0 md:[&_input:not([type=checkbox])]:min-h-0 md:[&_summary]:min-h-0">
      <div className="mx-auto max-w-4xl space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-bg-tertiary px-3 py-2">
          <div>
            <p className="text-xs font-medium text-text-primary">Generation queue</p>
            <p className="text-[10px] text-text-muted">
              {emptyState === 'pending'
                ? 'Loading queue status.'
                : queueError && !queue
                ? 'Queue status unavailable.'
                : queue?.paused
                  ? 'Paused — queued jobs will not start.'
                  : queue?.pause_after_current
                    ? 'Will pause after the current output.'
                    : 'Ready jobs start by priority.'}
            </p>
            {queue && (
              <p className="mt-0.5 text-[10px] text-text-secondary">{queueSummaryLabel(projection.summary)}</p>
            )}
            <details className="group mt-1.5 max-w-2xl text-[10px] text-text-muted">
              <summary className="flex min-h-11 cursor-pointer items-center text-text-secondary hover:text-text-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-blue md:min-h-0">
                How queue priority works
              </summary>
              <div className="mt-1.5 space-y-1 rounded-md border border-border bg-bg-primary/40 px-2.5 py-2 leading-relaxed">
                <p>Ready jobs start by priority. When priorities match, Maestro usually keeps their queue order.</p>
                <p>A job may start sooner when it can reuse a model that is already loaded.</p>
                <p>Jobs that have waited a long time keep their place so they are not repeatedly delayed.</p>
                <p>Queue order and time estimates can change as work starts, finishes, or becomes ready.</p>
                <p>Queued generations do not interrupt work already running. Only a restartable CPU text task may start over with GPU acceleration, and only when that is expected to finish sooner.</p>
              </div>
            </details>
          </div>
          {machineControls && queue && <div className="flex items-center gap-2">
            {queue?.paused ? (
              <button className="rounded-md bg-accent-green/15 px-2.5 py-1 text-[10px] text-accent-green" onClick={() => void act(api.resumeQueue)}>
                Resume queue
              </button>
            ) : (
              <button className="flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-[10px] text-text-secondary" onClick={() => void act(() => api.pauseQueueAfterOutput(!(queue?.pause_after_current)))}>
                <Pause size={11} /> {queue?.pause_after_current ? 'Cancel pause' : 'Pause after output'}
              </button>
            )}
          </div>}
        </div>
        <SampleCampaignQueueSection pairs={sampleCampaignPairs} />
        <PromptEnhanceQueueCard />
        {queueError && (
          <div
            className={`rounded-md border px-3 py-2 text-xs ${queue ? 'border-amber-500/30 bg-amber-500/10 text-amber-200' : 'border-red-500/30 bg-red-500/10 text-red-300'}`}
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            {queue
              ? `Queue refresh failed. Showing the last successful update${queueLastSuccessAt == null ? '' : ` from ${new Date(queueLastSuccessAt).toLocaleTimeString()}`}; retrying automatically. ${queueError}`
              : `Queue unavailable; retrying automatically. ${queueError}`}
          </div>
        )}
        {(error || planReviewError) && <div className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error || planReviewError}</div>}
        <PipelinePlaceholder />
        {panelEmptyState === 'pending' ? (
          <div className="flex min-h-56 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-bg-secondary/60 px-6 py-10 text-center">
            <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-bg-tertiary text-text-muted" aria-hidden="true">
              <Loader2 size={22} className="animate-spin" />
            </div>
            <h3 className="text-sm font-medium text-text-primary">Loading queue</h3>
            <p className="mt-1 max-w-sm text-xs leading-relaxed text-text-muted">
              Checking queued and running generations.
            </p>
          </div>
        ) : panelEmptyState === 'unavailable' ? (
          <div className="flex min-h-56 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-bg-secondary/60 px-6 py-10 text-center">
            <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-bg-tertiary text-text-muted" aria-hidden="true">
              <Loader2 size={22} className="animate-spin" />
            </div>
            <h3 className="text-sm font-medium text-text-primary">Queue unavailable</h3>
            <p className="mt-1 max-w-sm text-xs leading-relaxed text-text-muted">
              The queue is unavailable. Maestro is retrying automatically.
            </p>
          </div>
        ) : panelEmptyState === 'cached-stale' ? (
          <div className="flex min-h-56 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-bg-secondary/60 px-6 py-10 text-center">
            <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-bg-tertiary text-text-muted" aria-hidden="true">
              <ListChecks size={22} />
            </div>
            <h3 className="text-sm font-medium text-text-primary">Last known queue is clear</h3>
            <p className="mt-1 max-w-sm text-xs leading-relaxed text-text-muted">
              The latest refresh failed. Maestro is retrying automatically.
            </p>
          </div>
        ) : panelEmptyState === 'empty' ? (
          <div className="flex min-h-56 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-bg-secondary/60 px-6 py-10 text-center">
            <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-accent-blue/30 bg-accent-blue/10 text-accent-blue" aria-hidden="true">
              <ListChecks size={22} />
            </div>
            <h3 className="text-sm font-medium text-text-primary">Queue is clear</h3>
            <p className="mt-1 max-w-sm text-xs leading-relaxed text-text-muted">
              No queued, running, or failed generations.
            </p>
          </div>
        ) : visibleJobs.map((job, index) => {
          const target = projection.schedulerTargetByPublicJobId.get(job.id)
          const effectiveJob = target?.schedulerJob ?? job
          const schedulerJobId = target?.schedulerJobId ?? job.id
          const info = target?.queueJob
          const effectiveResourceDescriptor = schedulerJobId !== job.id
            ? info?.resource_descriptor ?? effectiveJob.resourceDescriptor
            : effectiveJob.resourceDescriptor ?? info?.resource_descriptor
          const queueRowLabel = info?.status === 'running'
            ? (info.hold_after_output ? 'Holding after this output' : 'Running')
            : info?.status === 'preparing'
              ? 'Preparing generation'
              : info?.status === 'waiting_for_plan_approval'
                ? 'Awaiting plan review'
            : info?.held
              ? 'Held'
              : queuePositionLabel(info?.position ?? null, projection.summary.waiting)
          const waitDetail = info?.wait_reason === 'queue_paused'
            ? 'Queue paused'
            : info?.wait_reason === 'waiting_for_plan_terms'
              ? 'Waiting for required model terms'
            : info?.wait_reason === 'resource_wait'
              ? 'Waiting for available GPU resources'
            : info?.wait_reason === 'waiting_for_active_generation'
              || info?.wait_reason === 'waiting_for_other_user'
              ? 'Waiting for another generation on this host'
              : null
          const resourceWaitTitle = info?.wait_reason === 'resource_wait' ? RESOURCE_WAIT_TITLE : undefined
          const resourcePresentation = describeResourceExecution(effectiveResourceDescriptor)
          const canManageGeneration = !projectPermissionsProjected || workspaceAllowsPermission(
            workspaces.find(workspace => workspace.name === job.workspace),
            'project.generate',
          )
          const residencyMessage = info?.status === 'running' && (
            info?.queue_reorder_reason === 'resident_base'
            || info?.queue_reorder_reason === 'resident_affinity'
          )
            ? 'Started sooner by reusing the loaded model'
            : info?.status === 'running' && info.queue_reorder_reason === 'starvation_guard'
              ? 'Kept its place after a long wait'
              : null
          return (
            <div key={job.id || `pending-${index}`} className="space-y-1.5">
              {info && (job.status === 'preparing' || job.status === 'waiting_for_plan_approval' || job.status === 'queued' || job.status === 'running') && (
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-bg-secondary px-2.5 py-1.5 text-[10px] text-text-muted">
                  {info.recovery_blocked ? (
                    <>
                      <span className="text-amber-300">
                        Recovery needs your choice · {info.recovery_reason_text || 'choose how to continue'}
                      </span>
                      <button className="rounded border border-border px-1.5 py-0.5 hover:bg-bg-hover" onClick={() => void toggleLog(job)}>Log</button>
                    </>
                  ) : (
                    <>
                      <span title={resourceWaitTitle}>{queueRowLabel}{waitDetail ? ` · ${waitDetail}` : ''} · Priority {info.priority} · Outputs {info.produced_outputs}/{info.requested_outputs}</span>
                      <span className="text-[9px] text-text-secondary">
                        ETA {formatApproximateDuration(info.eta_seconds)}
                        {info.subtask_eta_seconds != null ? ` · current task ${formatApproximateDuration(info.subtask_eta_seconds)}` : ''}
                      </span>
                      {residencyMessage && (
                        <span
                          className="rounded-full border border-accent-green/30 bg-accent-green/10 px-2 py-0.5 text-[9px] text-accent-green"
                          title={residencyMessage}
                        >
                          {residencyMessage}
                        </span>
                      )}
                      {resourcePresentation && (
                        <span
                          className={`rounded-full border px-2 py-0.5 text-[9px] ${resourcePresentationClass(resourcePresentation.tone)}`}
                          title={resourcePresentation.title}
                          data-resource-state={effectiveResourceDescriptor?.state}
                        >
                          {resourcePresentation.label}
                        </span>
                      )}
                      <div className="flex flex-wrap items-center gap-1">
                        {canManageGeneration && <>
                          <span className="text-[9px]">Total</span>
                          <input
                            type="number"
                            min={Math.max(1, info.produced_outputs)}
                            max={25}
                            value={countDrafts[schedulerJobId] ?? info.requested_outputs}
                            onChange={event => setCountDrafts(values => ({ ...values, [schedulerJobId]: Number(event.target.value) }))}
                            className="w-12 rounded border border-border bg-bg-primary px-1 py-0.5 text-center text-[10px] text-text-primary"
                            aria-label="Requested output count"
                          />
                          <button className="rounded border border-border px-1.5 py-0.5 hover:bg-bg-hover" onClick={() => void act(() => api.setQueueOutputCount(schedulerJobId, countDrafts[schedulerJobId] ?? info.requested_outputs))}>Set</button>
                        </>}
                        <button className="rounded border border-border px-1.5 py-0.5 hover:bg-bg-hover" onClick={() => void toggleLog(effectiveJob)}>Log</button>
                        {canManageGeneration && info.status === 'queued' && <>
                          {machineControls && <>
                            <button className="rounded bg-accent-green/15 px-2 py-0.5 text-accent-green hover:bg-accent-green/25" onClick={() => void act(() => api.startQueueJobNext(schedulerJobId))}>
                              Start next
                            </button>
                            <button title="Lower priority" className="rounded p-1 hover:bg-bg-hover" onClick={() => void act(() => api.setQueuePriority(schedulerJobId, info.priority - 1))}><ArrowDown size={12} /></button>
                            <button title="Raise priority" className="rounded p-1 hover:bg-bg-hover" onClick={() => void act(() => api.setQueuePriority(schedulerJobId, info.priority + 1))}><ArrowUp size={12} /></button>
                          </>}
                          <button className="rounded border border-border px-2 py-0.5 text-text-secondary hover:text-text-primary" onClick={() => void act(() => info.held ? api.resumeQueueJob(schedulerJobId) : api.holdQueueJob(schedulerJobId))}>
                            {info.held ? 'Resume' : 'Hold'}
                          </button>
                        </>}
                        {canManageGeneration && info.status === 'running' && (
                          <button
                            className="rounded border border-border px-2 py-0.5 text-text-secondary hover:text-text-primary"
                            onClick={() => void act(() => info.hold_after_output ? api.resumeQueueJob(schedulerJobId) : api.holdQueueJob(schedulerJobId))}
                          >
                            {info.hold_after_output ? 'Cancel hold' : 'Hold after output'}
                          </button>
                        )}
                      </div>
                    </>
                  )}
                </div>
              )}
              <JobPlaceholder
                job={job}
                canManageGeneration={canManageGeneration}
                referenceQuality={referenceQualityByJobId[job.id]}
                onStop={() => onStop(job.id)}
                onDismiss={() => onDismiss(job.id)}
                onToggleLog={() => void toggleLog(effectiveJob)}
                onRecoveryAction={action => recover(job, action)}
                onReviewPlan={() => void openH3PlanReview(job.id)}
                logOpen={logJobId === effectiveJob.id}
                logEvents={logJobId === effectiveJob.id && effectiveJob.logEvents?.length ? effectiveJob.logEvents : logEvents}
                logError={logJobId === effectiveJob.id ? logError : null}
              />
              {job.status !== 'failed' && job.status !== 'cancelled' && !info && api.isBackendJobId(job.id) && (
                <button className="ml-2 text-[10px] text-text-muted hover:text-text-primary" onClick={() => void toggleLog(job)}>View job log</button>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function GalleryBulkToolbar() {
  const selected = useStore(s => s.selectedOutputKeys)
  const outputs = useStore(s => s.filteredOutputs())
  const workspaces = useStore(s => s.workspaces) ?? []
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const accessContext = useStore(s => s.accessContext)
  const accountProjectMigration = useStore(s => s.accountProjectMigration)
  const selectAll = useStore(s => s.selectAllLoadedOutputs)
  const clear = useStore(s => s.clearOutputSelection)
  const setSelectionMode = useStore(s => s.setGallerySelectionMode)
  const moveSelected = useStore(s => s.bulkMoveSelectedOutputs)
  const setPrivacy = useStore(s => s.bulkSetSelectedPrivacy)
  const deleteSelected = useStore(s => s.bulkDeleteSelectedOutputs)
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const accountProjectAccessActive = api.isAccountProjectAccessActive(accessContext, accountProjectMigration)
  const workspaceByName = new Map(workspaces.map(workspace => [workspace.name, workspace]))
  const selectedOutputs = outputs.filter(output => selected.includes(`${output.workspace}\0${output.name}`))
  const canMutateSelection = selected.length > 0
    && selectedOutputs.length === selected.length
    && selectedOutputs.every(output => workspaceAllowsPermission(
      workspaceByName.get(output.workspace),
      'project.mutate',
    ))
  const mutableTargets = workspaces.filter(workspace => (
    workspace.name !== activeWorkspace
    && (accountProjectAccessActive || workspace.unlocked !== false)
    && workspaceAllowsPermission(workspace, 'project.mutate')
  ))
  const canMoveSelection = canMutateSelection
    && Boolean(target)
    && workspaceAllowsPermission(workspaceByName.get(target), 'project.mutate')

  const run = async (allowed: boolean, operation: () => Promise<string[]>) => {
    if (!allowed) return
    setBusy(true)
    setErrors([])
    try {
      setErrors(await operation())
    } catch (error) {
      setErrors([error instanceof Error ? error.message : 'Bulk operation failed'])
    } finally {
      setBusy(false)
      setConfirmDelete(false)
    }
  }

  return (
    <div className="border-b border-border bg-bg-tertiary/70 px-2 py-2 [&_button]:min-h-11 [&_button]:min-w-11 [&_select]:min-h-11 md:px-6 md:[&_button]:min-h-0 md:[&_button]:min-w-0 md:[&_select]:min-h-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-text-primary">{selected.length} selected</span>
        <button onClick={selectAll} disabled={!outputs.length || busy} className="rounded-md border border-border px-2 py-1 text-[10px] text-text-secondary hover:text-text-primary disabled:opacity-40">
          Select loaded
        </button>
        <button onClick={clear} disabled={busy} className="rounded-md border border-border px-2 py-1 text-[10px] text-text-secondary hover:text-text-primary">Clear</button>
        <div className="mx-1 hidden h-5 w-px bg-border sm:block" />
        {canMutateSelection && <>
          <select
            value={target}
            onChange={event => setTarget(event.target.value)}
            disabled={busy}
            className="rounded-md border border-border bg-bg-secondary px-2 py-1 text-[10px] text-text-primary"
          >
            <option value="">Move to project…</option>
            {mutableTargets.map(workspace => (
              <option key={workspace.name} value={workspace.name}>{workspace.name}</option>
            ))}
          </select>
          <button
            onClick={() => void run(canMoveSelection, () => moveSelected(target))}
            disabled={!canMoveSelection || busy}
            className="flex items-center gap-1 rounded-md bg-accent-blue px-2 py-1 text-[10px] text-white disabled:opacity-40"
            title="Move the selected outputs. Finished outputs bring their related parts, generation steps, and temporary files."
          >
            <FolderInput size={11} /> Move selected
          </button>
          <button title="Blur the selected previews by default and revoke their existing share links. This does not change who can open the project." onClick={() => void run(canMutateSelection, () => setPrivacy(true))} disabled={busy} className="flex items-center gap-1 rounded-md border border-violet-500/40 px-2 py-1 text-[10px] text-violet-200 disabled:opacity-40">
            <EyeOff size={11} /> Blur previews
          </button>
          <button title="Show the selected previews by default and revoke their existing share links. This does not change who can open the project." onClick={() => void run(canMutateSelection, () => setPrivacy(false))} disabled={busy} className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[10px] text-text-secondary disabled:opacity-40">
            <Eye size={11} /> Show previews
          </button>
          <button
            onClick={() => confirmDelete ? void run(canMutateSelection, deleteSelected) : setConfirmDelete(true)}
            disabled={busy}
            className="flex items-center gap-1 rounded-md border border-red-500/40 px-2 py-1 text-[10px] text-red-300 disabled:opacity-40"
            title="Delete the selected outputs. For each finished output, also delete its related parts, generation steps, and temporary files."
          >
            <Trash2 size={11} /> {confirmDelete ? `Delete ${selected.length} outputs and related files?` : 'Delete selected'}
          </button>
        </>}
        <button type="button" aria-label="Close selection tools" title="Close selection tools" onClick={() => setSelectionMode(false)} disabled={busy} className="ml-auto p-1 text-text-muted hover:text-text-primary"><X size={14} /></button>
        {busy && <Loader2 size={13} className="animate-spin text-accent-blue" />}
      </div>
      {errors.length > 0 && <p className="mt-1 text-[10px] text-red-400">{errors.join(' · ')}</p>}
    </div>
  )
}

function PipelinePlaceholder() {
  const pipelineStatus = useStore(s => s.pipelineStatus)
  const pipelineId = useStore(s => s.pipelineId)
  const stopPipeline = useStore(s => s.stopPipeline)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const activeProject = useStore(s => (s.workspaces ?? []).find(workspace => workspace.name === s.activeWorkspace))
  const canStopPipeline = Boolean(activeWorkspace) && projectActionVisibility(activeProject).generate

  if (!pipelineId || !pipelineStatus) return null
  if (pipelineStatus.status === 'completed' || pipelineStatus.status === 'failed' || pipelineStatus.status === 'cancelled') return null

  const phase = pipelineStatus.phase || 'planning'
  const progress = pipelineStatus.progress
  const message = progress?.message || phase

  const isVideoGeneration = phase === 'generating_video'
  const currentStep = progress?.window_step ?? progress?.step ?? 0
  const currentTotalSteps = progress?.window_total_steps ?? progress?.total_steps ?? 0
  const hasSteps = currentTotalSteps > 0
  const hasLegacyTotal = (progress?.total ?? 0) > 0
  const currentProgressPct = hasSteps
    ? Math.max(0, Math.min(100, (currentStep / currentTotalSteps) * 100))
    : !isVideoGeneration && hasLegacyTotal
      ? Math.max(0, Math.min(100, ((progress?.current ?? 0) / progress!.total) * 100))
    : Math.max(0, Math.min(100, progress?.window_progress ?? 0))
  const overallProgressPct = isVideoGeneration
    ? Math.max(0, Math.min(100, progress?.overall_progress ?? 0))
    : hasSteps
      ? currentProgressPct
    : progress && progress.total > 0
      ? Math.max(0, Math.min(100, (progress.current / progress.total) * 100))
      : 0
  const currentIndeterminate = Boolean(progress?.indeterminate)
    || (!hasSteps && (isVideoGeneration || !hasLegacyTotal))
  const phaseLabel = stripTimeSuffix(message)

  return (
    <div className="rounded-xl overflow-hidden border border-accent-blue/30 bg-bg-tertiary">
      <div className="w-full aspect-video flex items-center justify-center">
        <div className="flex flex-col items-center gap-3 text-text-muted w-full max-w-xs px-4">
          <Film size={40} className="animate-pulse" />

          <div className="text-center w-full">
            <p className="text-sm font-medium text-text-secondary">
              {pipelineStatus?.status === 'paused' ? 'Paused — Review' : 'Director'}
            </p>
            <p className="text-xs mt-1 truncate">{phaseLabel}</p>
            {isVideoGeneration && (progress?.window_total ?? 0) > 1 ? (
              <p className="text-[10px] text-text-muted mt-0.5">
                Segment {progress?.window_current || 1}/{progress?.window_total}
                {hasSteps ? ` · Step ${currentStep}/${currentTotalSteps}` : ''}
              </p>
            ) : hasSteps && (
              <p className="text-[10px] text-text-muted mt-0.5">
                Step {currentStep}/{currentTotalSteps}
              </p>
            )}
          </div>

          <div className="w-full space-y-1.5">
            {isVideoGeneration && (
              <div className="space-y-1">
                <div className="flex items-center justify-between text-[10px] text-text-muted">
                  <span>Overall</span>
                  <span>{Math.round(overallProgressPct)}%</span>
                </div>
                <div className="w-full bg-bg-active rounded-full h-1.5 overflow-hidden">
                  <div
                    className="h-full bg-accent-blue rounded-full transition-all duration-300"
                    style={{ width: `${overallProgressPct}%` }}
                  />
                </div>
              </div>
            )}
            <div className="space-y-1">
              {isVideoGeneration && (
                <div className="text-[10px] text-text-muted">Current segment</div>
              )}
              <div className="w-full bg-bg-active rounded-full h-1.5 overflow-hidden">
                {currentIndeterminate ? (
                  <div className="h-full bg-accent-green/60 rounded-full animate-pulse w-full" />
                ) : (
                  <div
                    className="h-full bg-accent-green rounded-full transition-all duration-300"
                    style={{ width: `${currentProgressPct}%` }}
                  />
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Bottom bar with stop button */}
      <div className="px-3 py-2 min-h-[40px] flex items-center justify-between">
        <div className="text-[11px] text-text-muted truncate flex-1">
          {phaseLabel || 'Preparing...'}
        </div>
        {canStopPipeline && <button
          onClick={() => {
            if (canStopPipeline) void stopPipeline()
          }}
          className="flex items-center gap-1 text-xs text-red-400 hover:text-red-300 transition-colors shrink-0 ml-2"
        >
          <Square size={11} />
          Stop
        </button>}
      </div>
    </div>
  )
}

export function MainContent() {
  const outputs = useStore(s => s.filteredOutputs())
  const outputsTotal = useStore(s => s.outputsTotal)
  const outputsLoading = useStore(s => s.outputsLoading)
  const jobs = useStore(s => s.jobs)
  const generationMode = useStore(s => s.generationMode)
  const stopGeneration = useStore(s => s.stopGeneration)
  const dismissJob = useStore(s => s.dismissJob)
  const setSelectedOutput = useStore(s => s.setSelectedOutput)
  const activeIndex = useStore(s => s.selectedOutput)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const activeProject = useStore(s => (s.workspaces ?? []).find(workspace => workspace.name === s.activeWorkspace))
  const canMutateActiveProject = projectActionVisibility(activeProject).mutate
  const canManageActiveProjectMembers = activeProject?.project_permissions?.includes('project.membership.manage') === true
  const browsingUploads = useStore(s => s.browsingUploads)
  const mediaFilter = useStore(s => s.mediaFilter)
  const outputArtifactScope = useStore(s => s.outputArtifactScope)
  const outputSearchQuery = useStore(s => s.outputSearchQuery)
  const gallerySelectionMode = useStore(s => s.gallerySelectionMode)
  const setGallerySelectionMode = useStore(s => s.setGallerySelectionMode)
  const openQueueAfterSubmit = useStore(s => s.openQueueAfterSubmit)
  const enhanceQueueCard = useStore(s => s.enhanceQueueCard)
  const accessContext = useStore(s => s.accessContext)
  const accountContext = useStore(s => s.accountContext)
  const accountProjectMigration = useStore(s => s.accountProjectMigration)
  const loadAccessContext = useStore(s => s.loadAccessContext)
  const reconcileQueueState = useStore(s => s.reconcileQueueState)
  const sampleCampaignPairs = useStore(s => s.sampleCampaignPairs)
  const refreshSampleCampaignQueue = useStore(s => s.refreshSampleCampaignQueue)
  const [shareCopied, setShareCopied] = useState(false)
  const [projectAccessOpen, setProjectAccessOpen] = useState(false)
  const projectShareTriggerRef = useRef<HTMLButtonElement>(null)
  const closeProjectAccess = useCallback(() => setProjectAccessOpen(false), [])
  const [mainView, setMainView] = useState<MainView>('gallery')
  const [queueTabSnapshot, setQueueTabSnapshot] = useState<QueueTabSnapshot>({
    state: null,
    jobs: [],
    error: null,
    lastSuccessAt: null,
  })
  const queueTabState = queueTabSnapshot.state
  const queueTabError = queueTabSnapshot.error
  const [accessPollAttempt, setAccessPollAttempt] = useState(0)
  const [privatePreviewVersion, setPrivatePreviewVersion] = useState(0)
  const queuePollSequence = useRef(0)
  const queuePollAbort = useRef<AbortController | null>(null)
  const seenJobIds = useRef(new Set(jobs.map(job => job.id).filter(Boolean)))
  const queuePollingReady = api.protectedProjectReadsReady(
    accessContext,
    accountContext,
    activeProject ? [activeProject] : [],
    activeWorkspace,
    accountProjectMigration,
  )
  const browserStudioUrl = typeof window === 'undefined'
    ? ''
    : `${window.location.origin}${window.location.pathname || '/'}`
  const hasActiveGalleryFilters = mediaFilter !== 'all'
    || outputArtifactScope !== 'final'
    || outputSearchQuery.trim().length > 0
  const activeWorkspaceHasJobs = !!activeWorkspace && jobs.some(job => job.workspace === activeWorkspace)
  const galleryState = galleryEmptyState({
    outputsLoading,
    outputCount: outputs.length,
    outputsTotal,
    browsingUploads,
    activeWorkspace,
    hasActiveFilters: hasActiveGalleryFilters,
    hasProjectJobs: activeWorkspaceHasJobs,
  })

  useEffect(() => subscribePrivatePreviewChanges(() => {
    setPrivatePreviewVersion(version => version + 1)
  }), [])

  const privatePreviewRevealState = useMemo<'none' | 'some' | 'all'>(() => {
    void privatePreviewVersion
    if (!activeWorkspace) return 'none'
    if (privatePreviewWorkspaceHasRevealed(activeWorkspace, 'all')) return 'all'
    return privatePreviewWorkspaceHasRevealed(activeWorkspace) ? 'some' : 'none'
  }, [activeWorkspace, privatePreviewVersion])
  const privatePreviewActionLabel = privatePreviewRevealState === 'all'
    ? 'Blur all'
    : privatePreviewRevealState === 'some' ? 'Reveal all remaining' : 'Reveal all'
  const privatePreviewActionPressed = privatePreviewRevealState === 'some'
    ? 'mixed' as const
    : privatePreviewRevealState === 'all'
  const togglePrivatePreviews = useCallback(() => {
    if (!activeWorkspace) return
    // Read current session state at activation time so repeated clicks/taps do
    // not depend on a React render completing between events.
    setPrivatePreviewsForWorkspaceRevealed(
      activeWorkspace,
      !privatePreviewWorkspaceHasRevealed(activeWorkspace, 'all'),
    )
  }, [activeWorkspace])

  useEffect(() => {
    const openGallery = () => setMainView('gallery')
    window.addEventListener(OPEN_GALLERY_EVENT, openGallery)
    return () => window.removeEventListener(OPEN_GALLERY_EVENT, openGallery)
  }, [])

  useEffect(() => {
    const openQueue = () => setMainView('queue')
    return subscribeQueueView(openQueue)
  }, [])

  useEffect(() => {
    const newActiveJob = jobs.some(job => (
      !!job.id
      && !seenJobIds.current.has(job.id)
      && (job.status === 'preparing' || job.status === 'waiting_for_plan_approval' || job.status === 'queued' || job.status === 'running')
    ))
    for (const job of jobs) if (job.id) seenJobIds.current.add(job.id)
    if (newActiveJob && openQueueAfterSubmit) setMainView('queue')
  }, [jobs, openQueueAfterSubmit])

  const refreshQueue = useCallback(async (pollSignal?: AbortSignal) => {
    if (!queuePollingReady) return
    const sequence = ++queuePollSequence.current
    queuePollAbort.current?.abort()
    const controller = new AbortController()
    queuePollAbort.current = controller
    const relayAbort = () => controller.abort()
    pollSignal?.addEventListener('abort', relayAbort, { once: true })
    try {
      const [queueState] = await Promise.all([
        api.fetchQueueState(controller.signal),
        refreshSampleCampaignQueue(controller.signal),
      ])
      if (queueRefreshIsStale(sequence, queuePollSequence.current, controller.signal.aborted)) return
      const sampleJobIds = new Set(
        useStore.getState().sampleCampaignPairs.flatMap(entry => entry.arms.map(arm => arm.job_id)),
      )
      const next = {
        ...queueState,
        jobs: queueState.jobs.filter(job => !sampleJobIds.has(job.job_id)),
      }
      reconcileQueueState(next)
      setQueueTabSnapshot(current => reduceQueueTabSnapshot(current, {
        kind: 'success',
        state: next,
        jobs: useStore.getState().jobs,
        receivedAt: Date.now(),
      }))
    } catch (reason) {
      if (queueRefreshIsStale(sequence, queuePollSequence.current, controller.signal.aborted)) return
      const recoveryStatus = api.queueAccessRecoveryStatus(reason)
      if (recoveryStatus !== null) {
        setQueueTabSnapshot({
          state: null,
          jobs: [],
          error: null,
          lastSuccessAt: null,
        })
        useStore.setState({
          jobs: [],
          sampleCampaignPairs: [],
          isGenerating: false,
        })
        api.requestAccessRecovery(recoveryStatus)
        return
      }
      setQueueTabSnapshot(current => reduceQueueTabSnapshot(current, {
        kind: 'failure',
        error: reason instanceof Error ? reason.message : 'Queue update failed',
      }))
      throw reason
    } finally {
      pollSignal?.removeEventListener('abort', relayAbort)
      if (queuePollAbort.current === controller) queuePollAbort.current = null
    }
  }, [queuePollingReady, reconcileQueueState, refreshSampleCampaignQueue])

  useEffect(() => {
    if (queuePollingReady) return
    queuePollSequence.current += 1
    queuePollAbort.current?.abort()
    queuePollAbort.current = null
    setQueueTabSnapshot({
      state: null,
      jobs: [],
      error: null,
      lastSuccessAt: null,
    })
    useStore.setState(state => ({
      jobs: state.jobs.filter(job => job.status === 'failed' || job.status === 'cancelled'),
      sampleCampaignPairs: [],
      isGenerating: false,
    }))
  }, [queuePollingReady])

  useEffect(() => {
    const refresh = () => {
      if (queuePollingReady && !document.hidden) void refreshQueue().catch(() => {})
    }
    window.addEventListener(QUEUE_REFRESH_EVENT, refresh)
    return () => window.removeEventListener(QUEUE_REFRESH_EVENT, refresh)
  }, [queuePollingReady, refreshQueue])

  const sampleCampaignJobIds = useMemo(
    () => new Set(sampleCampaignPairs.flatMap(entry => entry.arms.map(arm => arm.job_id))),
    [sampleCampaignPairs],
  )
  const enhanceOrdinaryJobId = (
    enhanceQueueCard?.scope?.requestId || enhanceQueueCard?.requestId || ''
  ).replaceAll('-', '')
  const queueDisplayJobs = queueTabDisplayJobs(queueTabSnapshot, jobs)
    .filter(job => (
      !sampleCampaignJobIds.has(job.id)
      && !(
        enhanceOrdinaryJobId
        && job.logicalJobKind === 'prompt_enhancement'
        && job.id.replaceAll('-', '') === enhanceOrdinaryJobId
      )
    ))
  const ordinaryQueueState = useMemo(() => queueTabState ? {
    ...queueTabState,
    jobs: queueTabState.jobs.filter(job => (
      !sampleCampaignJobIds.has(job.job_id)
      && !(
        enhanceOrdinaryJobId
        && job.logical_job_kind === 'prompt_enhancement'
        && job.job_id.replaceAll('-', '') === enhanceOrdinaryJobId
      )
    )),
  } : null, [queueTabState, sampleCampaignJobIds, enhanceOrdinaryJobId])
  const logicalQueue = useMemo(
    () => projectLogicalQueue(queueDisplayJobs, ordinaryQueueState?.jobs),
    [queueDisplayJobs, ordinaryQueueState?.jobs],
  )
  const activeQueueJobs = logicalQueue.visibleJobs.filter(isActiveLogicalQueueJob)
  const enhanceQueueActive = enhanceQueueCard?.workspace === activeWorkspace
    && (
      enhanceQueueCard.phase === 'preparing'
      || enhanceQueueCard.phase === 'queued'
      || enhanceQueueCard.phase === 'running'
    )
  const activeQueueCount = logicalQueue.activeCount + (enhanceQueueActive ? 1 : 0)
  const queueActivity = logicalQueue.activeCount > 0
    || enhanceQueueActive
    || sampleCampaignPairs.some(entry => entry.queue_state === 'running_arm')

  useVisibilityPolling(
    refreshQueue,
    queueActivity
      ? POLL_INTERVAL_MS.queueActiveVisible
      : POLL_INTERVAL_MS.queueIdleVisible,
    { enabled: queuePollingReady },
  )

  useEffect(() => () => {
      queuePollSequence.current += 1
      queuePollAbort.current?.abort()
      queuePollAbort.current = null
  }, [])

  const accessContextPending = !accessContext?.remote
    && accessContext?.cloudflare_enabled === true
    && !accessContext.share_url
  const refreshAccessContext = useCallback(async () => {
    try {
      await loadAccessContext()
    } finally {
      setAccessPollAttempt(attempt => Math.min(attempt + 1, 16))
    }
  }, [loadAccessContext])

  useEffect(() => {
    if (!accessContextPending) setAccessPollAttempt(0)
  }, [accessContextPending])

  useVisibilityPolling(
    refreshAccessContext,
    boundedBackoffDelay(accessPollAttempt),
    { enabled: accessContextPending, immediate: false },
  )

  const currentTarget = activeQueueJobs
    .map(job => logicalQueue.schedulerTargetByPublicJobId.get(job.id))
    .find(target => target?.queueJob?.status === 'running' || target?.schedulerJob?.status === 'running')
  const currentJob = currentTarget?.schedulerJob ?? currentTarget?.publicJob
  const currentEtaSeconds = currentTarget?.queueJob?.eta_seconds ?? currentJob?.etaSeconds
  const currentSubtaskEtaSeconds = currentTarget?.queueJob?.subtask_eta_seconds ?? currentJob?.subtaskEtaSeconds
  const queueSummary = logicalQueue.summary
  const queueStateLabel = queueSummary.running > 0
    ? (ordinaryQueueState?.pause_after_current ? 'running · pause next' : 'running')
    : queueSummary.approval_waiting > 0
      ? 'review needed'
      : queueSummary.preparing > 0
        ? 'preparing'
    : enhanceQueueActive
      ? 'prompt enhance'
    : ordinaryQueueState?.paused
      ? 'paused'
      : queueSummary.held > 0
        && queueSummary.waiting === 0
        && queueSummary.registering === 0
        ? 'held'
        : logicalQueue.activeCount > 0
          ? 'waiting'
          : logicalQueue.visibleJobs.some(job => job.status === 'failed')
            ? 'attention'
            : 'idle'
  const queueStateColor = queueSummary.running > 0
    ? 'bg-accent-green'
    : queueSummary.approval_waiting > 0 || queueTabState?.paused || queueSummary.held > 0
      ? 'bg-amber-400'
      : enhanceQueueActive
        ? 'bg-accent-blue'
      : logicalQueue.visibleJobs.some(job => job.status === 'failed')
        ? 'bg-red-400'
        : logicalQueue.activeCount > 0 ? 'bg-accent-blue' : 'bg-text-muted'
  const queueTooltip = ordinaryQueueState
    ? `Queue: ${activeQueueCount} active · ${queueSummaryLabel(queueSummary)}${enhanceQueueActive ? ' · Prompt Enhance active' : ''}${ordinaryQueueState.paused ? ' · paused' : ordinaryQueueState.pause_after_current ? ' · pauses after current output' : ''}`
    : 'Queue status loading'
  const ownedJobEtaTooltip = currentJob
    ? ` · Your job: overall ETA ${formatApproximateDuration(currentEtaSeconds)}${currentSubtaskEtaSeconds != null ? ` · current task ${formatApproximateDuration(currentSubtaskEtaSeconds)}` : ''}`
    : ''

  const feedRef = useRef<HTMLDivElement>(null)
  const isUserScrolling = useRef(false)
  const scrollTarget = useRef<{ identity: string; listGeneration: number; scopeGeneration: number } | null>(null)
  const viewportAnchor = useRef<{ identity: string; intraItemOffset: number } | null>(null)
  const selectedOutputIdentity = useRef<string | null>(null)
  const galleryScopeKey = JSON.stringify([
    browsingUploads ? '__uploads__' : activeWorkspace,
    mediaFilter,
    outputArtifactScope,
    outputSearchQuery,
  ])
  const outputIdentities = useMemo(() => outputs.map(file => (
    privatePreviewIdentity(file.workspace, file.name, file.revision)
  )), [outputs])
  const outputIdentitySignature = JSON.stringify(outputIdentities)
  const listFence = useRef({ signature: '', generation: 0 })
  if (listFence.current.signature !== outputIdentitySignature) {
    listFence.current = {
      signature: outputIdentitySignature,
      generation: listFence.current.generation + 1,
    }
  }
  const listGeneration = listFence.current.generation
  const scopeFence = useRef({ key: galleryScopeKey, generation: 0 })
  if (scopeFence.current.key !== galleryScopeKey) {
    scopeFence.current = { key: galleryScopeKey, generation: scopeFence.current.generation + 1 }
  }
  const scopeGeneration = scopeFence.current.generation
  const currentOutputIdentities = useRef(new Set(outputIdentities))
  currentOutputIdentities.current = new Set(outputIdentities)

  // Virtualization state
  const [scrollTop, setScrollTop] = useState(0)
  const [containerHeight, setContainerHeight] = useState(800)
  const [containerWidth, setContainerWidth] = useState(800)
  const lastMeasuredContainerWidth = useRef(0)
  const measuredHeights = useRef<Map<string, { height: number; epoch: number }>>(new Map())
  const [measurementEpoch, setMeasurementEpoch] = useState(0)
  const [measurementVersion, setMeasurementVersion] = useState(0)

  // The feed DOM unmounts for Queue/Chat. Restore its controlled virtual
  // viewport before paint when returning so the old index window cannot be
  // rendered against a new element still sitting at scrollTop 0.
  useLayoutEffect(() => {
    if (mainView === 'gallery' && feedRef.current) feedRef.current.scrollTop = scrollTop
  }, [mainView, scrollTop])

  // Dynamic estimated item height based on actual container width
  const estimatedItemHeight = Math.round(containerWidth * ASPECT_RATIO) + INFO_BAR_HEIGHT

  // Queue cards have their own view and never participate in gallery offsets.
  const placeholderTotalHeight = 0

  // Measure container on mount and resize; clear stale heights on width change
  useEffect(() => {
    const el = feedRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      const rect = entries[0].contentRect
      setContainerHeight(rect.height)
      const newWidth = rect.width
      setContainerWidth(newWidth)
      if (
        lastMeasuredContainerWidth.current
        && Math.abs(newWidth - lastMeasuredContainerWidth.current) > 2
      ) {
        measuredHeights.current.clear()
        setMeasurementEpoch(epoch => epoch + 1)
      }
      lastMeasuredContainerWidth.current = newWidth
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [mainView])

  const getItemHeight = useCallback((index: number) => {
    const file = outputs[index]
    if (!file) return estimatedItemHeight
    const identity = privatePreviewIdentity(file.workspace, file.name, file.revision)
    const measurement = measuredHeights.current.get(identity)
    return measurement?.epoch === measurementEpoch ? measurement.height : estimatedItemHeight
  }, [estimatedItemHeight, measurementEpoch, outputs])

  const { startIndex, endIndex, totalHeight, itemOffsets } = useMemo(() => {
    // ResizeObserver writes into a ref; reading this version makes each new
    // measurement participate in the memoized offset calculation.
    void measurementVersion
    const count = outputs.length
    const offsets: number[] = new Array(count)
    let cumulative = placeholderTotalHeight

    for (let i = 0; i < count; i++) {
      offsets[i] = cumulative
      cumulative += getItemHeight(i) + GAP
    }
    const total = cumulative - (count > 0 ? GAP : 0)

    let lo = 0, hi = count - 1
    const viewStart = scrollTop - OVERSCAN * estimatedItemHeight
    while (lo < hi) {
      const mid = (lo + hi) >>> 1
      if (offsets[mid] + getItemHeight(mid) < viewStart) lo = mid + 1
      else hi = mid
    }
    const start = Math.max(0, lo)

    const viewEnd = scrollTop + containerHeight + OVERSCAN * estimatedItemHeight
    let end = start
    while (end < count && offsets[end] < viewEnd) end++

    return {
      startIndex: start,
      endIndex: Math.min(end, count),
      totalHeight: Math.max(total, placeholderTotalHeight),
      itemOffsets: offsets,
    }
  }, [outputs.length, scrollTop, containerHeight, getItemHeight, placeholderTotalHeight, estimatedItemHeight, measurementVersion])

  const handleItemMeasured = useCallback((identity: string, epoch: number, height: number) => {
    if (epoch !== measurementEpoch) return
    if (!currentOutputIdentities.current.has(identity)) return
    const previous = measuredHeights.current.get(identity)
    if (previous?.height !== height || previous.epoch !== epoch) {
      measuredHeights.current.set(identity, { height, epoch })
      setMeasurementVersion(version => version + 1)
    }
  }, [measurementEpoch])

  const anchoredScope = useRef(galleryScopeKey)
  useLayoutEffect(() => {
    const feedEl = feedRef.current
    if (anchoredScope.current !== galleryScopeKey) {
      anchoredScope.current = galleryScopeKey
      scrollTarget.current = null
      viewportAnchor.current = null
      selectedOutputIdentity.current = outputIdentities[0] ?? null
      measuredHeights.current.clear()
      setMeasurementEpoch(epoch => epoch + 1)
      setMeasurementVersion(version => version + 1)
      if (feedEl) {
        feedEl.scrollTo({ top: 0, behavior: 'auto' })
        setScrollTop(0)
      }
      return
    }

    const selectedIdentity = selectedOutputIdentity.current
    if (selectedIdentity) {
      const selectedIndex = outputIdentities.indexOf(selectedIdentity)
      if (selectedIndex >= 0 && selectedIndex !== activeIndex) setSelectedOutput(selectedIndex)
      else if (selectedIndex < 0) {
        selectedOutputIdentity.current = outputIdentities[Math.min(activeIndex, outputIdentities.length - 1)] ?? null
      }
    } else if (outputIdentities.length > 0) {
      selectedOutputIdentity.current = outputIdentities[Math.min(activeIndex, outputIdentities.length - 1)]
    }

    const anchor = viewportAnchor.current
    if (!feedEl || !anchor) return
    const anchorIndex = outputIdentities.indexOf(anchor.identity)
    if (anchorIndex < 0) return
    const anchoredTop = Math.max(0, (itemOffsets[anchorIndex] ?? 0) + anchor.intraItemOffset)
    if (Math.abs(feedEl.scrollTop - anchoredTop) > 0.5) {
      feedEl.scrollTop = anchoredTop
      setScrollTop(anchoredTop)
    }
  }, [activeIndex, galleryScopeKey, itemOffsets, measurementVersion, outputIdentities, outputIdentitySignature, setSelectedOutput])

  const captureViewportAnchor = useCallback((nextScrollTop: number) => {
    if (outputIdentities.length === 0) {
      viewportAnchor.current = null
      return
    }
    let anchorIndex = 0
    while (
      anchorIndex + 1 < outputIdentities.length
      && (itemOffsets[anchorIndex] + getItemHeight(anchorIndex)) <= nextScrollTop
    ) anchorIndex++
    viewportAnchor.current = {
      identity: outputIdentities[anchorIndex],
      intraItemOffset: nextScrollTop - itemOffsets[anchorIndex],
    }
  }, [getItemHeight, itemOffsets, outputIdentities])

  const handleItemVisible = useCallback((index: number) => {
    if (scrollTarget.current !== null) return
    if (isUserScrolling.current) {
      selectedOutputIdentity.current = outputIdentities[index] ?? null
      setSelectedOutput(index)
    }
  }, [outputIdentities, setSelectedOutput])

  const handleThumbnailClick = useCallback((index: number) => {
    const identity = outputIdentities[index]
    if (!identity) return
    selectedOutputIdentity.current = identity
    viewportAnchor.current = { identity, intraItemOffset: 0 }
    setSelectedOutput(index)
    const targetAtStart = { identity, listGeneration, scopeGeneration }
    scrollTarget.current = targetAtStart
    isUserScrolling.current = false
    const feedEl = feedRef.current
    if (!feedEl) {
      scrollTarget.current = null
      return
    }

    // ── Why this is two phases ──
    // The virtualizer only renders items inside [startIndex, endIndex].
    // Items outside that window have NEVER been measured — their height
    // is an estimate. Summing the estimates to compute an offset for a
    // distant target accumulates error linearly with distance: a click
    // 200 items away can land hundreds of px off.
    //
    // The previous implementation did a single smooth scrollTo to the
    // estimated offset. As items entered the viewport mid-animation,
    // they got measured and the total height shifted under the
    // animation, so the smooth scroll landed on the wrong item. The
    // 800ms guard then expired and the IntersectionObserver picked up
    // a wrong-active item → thumbnail strip auto-scrolled away from
    // what the user clicked → infinite oscillation.
    //
    // The fix:
    //   Phase 1: INSTANT jump to the estimated offset. This is allowed
    //            to be slightly wrong; its only job is to bring the
    //            target item into the virtualizer's render window so
    //            it actually mounts in the DOM.
    //   Phase 2: requestAnimationFrame wait until the DOM contains an
    //            element with the target's full output identity, then call
    //            scrollIntoView on it for pixel-precise alignment.
    //            By the time the element exists, its height has been
    //            measured, so this final align is accurate.
    //   Guard:   scrollTarget.current is held until phase 2
    //            finishes (not a fixed timeout). handleItemVisible
    //            ignores intersection events while this is non-null,
    //            so no wrong-active leak through.
    //   Re-entrancy: a stale align loop checks scrollTarget
    //            against its captured target on every frame and bails
    //            if a newer click overrode it.

    const estimatedOffset = itemOffsets[index] ?? placeholderTotalHeight
    feedEl.scrollTo({ top: estimatedOffset, behavior: 'auto' })

    let attempts = 0
    const MAX_ATTEMPTS = 30 // ~500ms at 60fps
    const align = () => {
      // Newer selection, scope, or list generation overrode this target.
      if (scrollTarget.current !== targetAtStart) return
      if (
        scopeFence.current.generation !== targetAtStart.scopeGeneration
        || listFence.current.generation !== targetAtStart.listGeneration
      ) {
        scrollTarget.current = null
        return
      }
      attempts++
      const targetEl = feedEl.querySelector(
        `[data-feed-identity="${encodeURIComponent(targetAtStart.identity)}"]`,
      ) as HTMLElement | null
      if (targetEl) {
        targetEl.scrollIntoView({ behavior: 'auto', block: 'start' })
        // One more frame so any post-mount measurement settles
        // before we release the guard.
        requestAnimationFrame(() => {
          if (scrollTarget.current === targetAtStart) {
            scrollTarget.current = null
          }
        })
      } else if (attempts < MAX_ATTEMPTS) {
        requestAnimationFrame(align)
      } else {
        // Item didn't mount within the budget — release the guard so
        // the user isn't stuck. Rare; happens if outputs.length changed
        // mid-flight or the index is out of range.
        if (scrollTarget.current === targetAtStart) {
          scrollTarget.current = null
        }
      }
    }
    // The first frame mounts the estimated window; the second aligns only if
    // the exact list and scope generations are still current.
    requestAnimationFrame(() => requestAnimationFrame(align))
  }, [itemOffsets, listGeneration, outputIdentities, placeholderTotalHeight, scopeGeneration, setSelectedOutput])

  // Infinite scroll: load more when near the bottom
  const loadingMore = useRef(false)
  const handleFeedScroll = useCallback(() => {
    const el = feedRef.current
    if (!el) return
    setScrollTop(el.scrollTop)
    captureViewportAnchor(el.scrollTop)
    if (scrollTarget.current === null) {
      isUserScrolling.current = true
    }
    // Trigger load-more when within 2 screens of the bottom
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    if (distanceToBottom < el.clientHeight * 2 && !loadingMore.current) {
      const store = useStore.getState()
      if (store.outputs.length < store.outputsTotal) {
        loadingMore.current = true
        store.loadMoreOutputs().finally(() => { loadingMore.current = false })
      }
    }
  }, [captureViewportAnchor])

  useEffect(() => {
    const visibleIdentities = new Set(outputs.map(file => (
      privatePreviewIdentity(file.workspace, file.name, file.revision)
    )))
    let pruned = false
    for (const identity of measuredHeights.current.keys()) {
      if (visibleIdentities.has(identity)) continue
      measuredHeights.current.delete(identity)
      pruned = true
    }
    if (pruned) setMeasurementVersion(version => version + 1)
  }, [outputs])

  const visibleItems = useMemo(() => {
    const items: JSX.Element[] = []
    for (let i = startIndex; i < endIndex; i++) {
      const file = outputs[i]
      if (!file) continue
      const identity = privatePreviewIdentity(file.workspace, file.name, file.revision)
      items.push(
        <MediaFeedItem
          key={identity}
          file={file}
          index={i}
          isActive={activeIndex === i}
          onVisible={handleItemVisible}
          measurementEpoch={measurementEpoch}
          onMeasured={handleItemMeasured}
          style={{
            position: 'absolute',
            top: itemOffsets[i],
            left: 0,
            right: 0,
          }}
        />
      )
    }
    return items
  }, [startIndex, endIndex, outputs, activeIndex, handleItemVisible, measurementEpoch, handleItemMeasured, itemOffsets])

  return (
    <main className="min-w-0 flex-1 flex flex-col h-full overflow-hidden">
      {/* Top bar */}
      <div
        data-main-toolbar
        className="relative z-40 grid min-w-0 grid-rows-[auto_auto] gap-2 border-b border-border px-2 py-2 [&_button]:min-h-11 [&_button]:min-w-11 [&_input:not([type=checkbox])]:min-h-11 [&_label]:min-h-11 [&_select]:min-h-11 md:px-6 md:py-3 md:[&_button]:min-h-0 md:[&_button]:min-w-0 md:[&_input:not([type=checkbox])]:min-h-0 md:[&_label]:min-h-0 md:[&_select]:min-h-0"
      >
        <div
          data-main-toolbar-primary
          className="flex min-w-0 flex-nowrap items-center gap-1.5 sm:gap-2"
        >
          <div
            data-main-toolbar-navigation
            className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto sm:gap-2"
          >
            <MainViewTabs
              activeView={mainView}
              onSelect={setMainView}
              queueTitle={`${queueTooltip}${ownedJobEtaTooltip}`}
              queueStateColor={queueStateColor}
              activeQueueCount={activeQueueCount}
              queueStateLabel={queueStateLabel}
              queueDetails={currentJob ? (
                <span className="hidden text-[9px] xl:inline">
                  · {Math.round(currentJob.overallProgress ?? currentJob.progress * 100)}% · ETA {formatApproximateDuration(currentEtaSeconds)}
                  {currentSubtaskEtaSeconds != null ? ` · task ${formatApproximateDuration(currentSubtaskEtaSeconds)}` : ''}
                </span>
              ) : undefined}
            />
            <div className="hidden text-[10px] text-text-muted md:block md:text-xs">
              {outputsTotal > outputs.length
                ? `${outputs.length} / ${outputsTotal} items`
                : `${outputs.length} ${outputs.length === 1 ? 'item' : 'items'}`}
            </div>
            {!accessContext?.remote && accessContext?.cloudflare_enabled && (
              <button
                type="button"
                disabled={!accessContext.share_url}
                title={accessContext.share_url || 'Pinokio is establishing the Cloudflare tunnel'}
                onClick={async () => {
                  if (!accessContext.share_url) return
                  await navigator.clipboard?.writeText(accessContext.share_url)
                  setShareCopied(true)
                  window.setTimeout(() => setShareCopied(false), 1800)
                }}
                className="max-w-[180px] truncate rounded-md border border-accent-blue/40 bg-accent-blue/10 px-2 py-1 text-[10px] text-accent-blue disabled:cursor-wait disabled:opacity-70"
              >
                {accessContext.share_url ? (shareCopied ? '✓ Cloudflare link copied' : 'Cloudflare · Copy link') : 'Cloudflare · starting…'}
              </button>
            )}
            {activeWorkspace && canManageActiveProjectMembers && (
              <button
                ref={projectShareTriggerRef}
                type="button"
                data-project-share-trigger
                onClick={() => setProjectAccessOpen(true)}
                className="flex min-h-11 shrink-0 items-center gap-1.5 rounded-md border border-accent-blue/40 bg-accent-blue/10 px-3 text-[10px] font-medium text-accent-blue transition-colors hover:bg-accent-blue/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0 md:px-2 md:py-1"
              >
                <Share2 size={12} aria-hidden="true" />
                Share project
              </button>
            )}
          </div>
          <div className="shrink-0 md:mr-28"><WorkspaceSelector /></div>
        </div>
        <div
          data-main-toolbar-view
          className="flex min-h-11 min-w-0 flex-nowrap items-center gap-1.5 overflow-x-auto sm:gap-2 md:min-h-8 lg:overflow-visible"
        >
          {mainView === 'gallery' ? <>
            <TabFilter />
            <div className="ml-auto flex shrink-0 items-center gap-1.5 sm:gap-2">
              {activeWorkspace && !browsingUploads && <>
                <span id="private-preview-session-note" className="hidden text-[9px] text-text-muted xl:inline">
                  This only changes previews in this browser. Project access does not change.
                </span>
                <button
                  type="button"
                  aria-pressed={privatePreviewActionPressed}
                  aria-describedby="private-preview-session-note"
                  aria-label={`${privatePreviewActionLabel} blurred previews for project ${activeWorkspace}`}
                  onClick={togglePrivatePreviews}
                  title={`${privatePreviewActionLabel} blurred previews in this browser. This does not change who can open the project.`}
                  className="flex min-h-11 items-center gap-1 rounded-md border border-violet-500/40 px-3 text-[10px] text-violet-200 transition-colors hover:bg-violet-500/10 md:min-h-0 md:px-2 md:py-1"
                >
                  {privatePreviewRevealState === 'all' ? <EyeOff size={12} /> : <Eye size={12} />}
                  {privatePreviewActionLabel}
                </button>
              </>}
              {canMutateActiveProject && <button
                type="button"
                onClick={() => setGallerySelectionMode(!gallerySelectionMode)}
                className={`flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] transition-colors ${gallerySelectionMode ? 'border-accent-blue bg-accent-blue/15 text-accent-blue' : 'border-border text-text-secondary hover:text-text-primary'}`}
              >
                <ListChecks size={12} /> Select
              </button>}
            </div>
          </> : (
            <h2 className="shrink-0 whitespace-nowrap text-xs font-medium text-text-primary">
              {mainView === 'queue' ? 'Queue' : 'LLM Chat'}
            </h2>
          )}
        </div>
      </div>
      {/* Content area: feed + thumbnails */}
      <MainViewPanels activeView={mainView}>
        {mainView === 'gallery' && gallerySelectionMode && <GalleryBulkToolbar />}
        {mainView === 'chat' ? (
          <LlmChat />
        ) : mainView === 'queue' ? (
          <QueuePanel
            jobs={queueDisplayJobs}
            sampleCampaignPairs={sampleCampaignPairs}
            onStop={stopGeneration}
            onDismiss={dismissJob}
            queue={ordinaryQueueState}
            queueError={queueTabError}
            queueLastSuccessAt={queueTabSnapshot.lastSuccessAt}
            refreshQueue={refreshQueue}
          />
        ) : (
        <div className="flex-1 flex flex-row gap-0 overflow-hidden relative">
        {/* Scrollable media feed */}
        <div
          ref={feedRef}
          className="flex-1 overflow-y-auto p-3 md:p-4"
          onScroll={handleFeedScroll}
        >
          {/* Position container for virtualized output items */}
          <div className="relative" style={{ height: totalHeight - placeholderTotalHeight }}>
            {visibleItems.map(item => {
              // Adjust top positions to be relative to this container (subtract placeholder height)
              const adjustedStyle = {
                ...item.props.style,
                top: (item.props.style?.top as number) - placeholderTotalHeight,
              }
              return { ...item, props: { ...item.props, style: adjustedStyle } }
            })}
          </div>

          {/* Loading state */}
          {outputsLoading && outputs.length === 0 && (
            <div className="flex items-center justify-center min-h-[300px]">
              <div className="flex flex-col items-center gap-3 text-text-muted">
                <Loader2 size={24} className="animate-spin text-accent-blue" />
                <p className="text-sm">Indexing workspace...</p>
              </div>
            </div>
          )}

          {/* Empty-state quick start. Teaches the three steps to a generation
              and explains host-shared model preparation without implying that
              every user's first request triggers a separate download. */}
          {galleryState === 'onboarding' && (() => {
            const noun = generationMode === 'image' ? 'images'
              : generationMode === 'audio' ? 'audio' : 'videos'
            const example = generationMode === 'image'
              ? 'a neon city street at night, cinematic'
              : generationMode === 'audio'
              ? 'a dreamy synthwave track about the ocean'
              : 'a golden retriever surfing a big wave, slow motion'
            return (
              <div className="flex items-center justify-center min-h-[300px] px-6">
                <div className="flex max-w-md flex-col items-center text-center">
                  <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-accent-blue/30 bg-accent-blue/10 text-accent-blue" aria-hidden="true">
                    <Play size={24} />
                  </div>
                  <h2 className="text-base font-semibold text-text-primary">No finished {noun} yet</h2>
                  <p className="mt-1 text-sm text-text-secondary">Your generated {noun} will appear here.</p>
                  <ol className="mt-5 w-full space-y-2 rounded-xl border border-border bg-bg-secondary/60 p-4 text-left text-xs text-text-muted">
                    <li><span className="text-accent-blue font-medium">1.</span> Pick a model in the sidebar (a good default is already selected).</li>
                    <li><span className="text-accent-blue font-medium">2.</span> Type a prompt — e.g. <span className="text-text-secondary italic">“{example}”</span></li>
                    <li><span className="text-accent-blue font-medium">3.</span> Hit Generate.</li>
                  </ol>
                  <p className="mt-4 text-[11px] leading-relaxed text-text-muted">
                    Heads up: if needed, this Maestro host downloads and prepares
                    model files before generation starts. The shared host cache is
                    reused when possible; loading into RAM/VRAM is a separate step.
                    Follow preparation status on the generation card.
                  </p>
                  <button
                    type="button"
                    onClick={() => useStore.getState().setRecipesOpen(true)}
                    className="mt-5 flex min-h-11 min-w-11 items-center gap-1.5 rounded-lg border border-accent-blue/30 bg-accent-blue/10 px-3 py-2 text-xs text-accent-blue transition-colors hover:bg-accent-blue/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0"
                    aria-label="Browse recipes"
                  >
                    <BookMarked size={13} /> Browse recipes
                  </button>
                </div>
              </div>
            )
          })()}

          {galleryState === 'uploads' && (
            <div className="flex min-h-[300px] items-center justify-center px-6">
              <div className="flex max-w-sm flex-col items-center text-center">
                <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-border bg-bg-secondary/60 text-text-muted" aria-hidden="true">
                  <Upload size={24} />
                </div>
                <h2 className="text-base font-semibold text-text-primary">No uploads yet</h2>
                <p className="mt-1 text-sm text-text-muted">Uploaded media will appear in this view.</p>
              </div>
            </div>
          )}

          {galleryState === 'filtered' && (
            <div className="flex min-h-[300px] items-center justify-center px-6">
              <div className="flex max-w-sm flex-col items-center text-center">
                <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-border bg-bg-secondary/60 text-text-muted" aria-hidden="true">
                  <Film size={24} />
                </div>
                <h2 className="text-base font-semibold text-text-primary">No matching items</h2>
                <p className="mt-1 text-sm text-text-muted">Try changing the Gallery filters or search.</p>
              </div>
            </div>
          )}

          {galleryState === 'project-required' && (
            <div className="flex min-h-[300px] items-center justify-center px-6">
              <div className="flex max-w-sm flex-col items-center text-center">
                <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-border bg-bg-secondary/60 text-text-muted" aria-hidden="true">
                  <FolderOpen size={24} />
                </div>
                <h2 className="text-base font-semibold text-text-primary">Select a project</h2>
                <p className="mt-1 text-sm text-text-muted">Choose a project to view its Gallery.</p>
              </div>
            </div>
          )}
        </div>

        {/* Thumbnail sidebar */}
        <ThumbnailGallery
          activeIndex={activeIndex}
          onThumbnailClick={handleThumbnailClick}
          privatePreviewControl={activeWorkspace && !browsingUploads ? {
            workspace: activeWorkspace,
            state: privatePreviewRevealState,
            onToggle: togglePrivatePreviews,
          } : undefined}
        />
        </div>
        )}
      </MainViewPanels>
      <ProjectAccessPanel
        open={projectAccessOpen && activeWorkspace !== '' && canManageActiveProjectMembers}
        workspace={activeWorkspace}
        recentlyReauthenticated={accountContext?.reauthenticated === true}
        configuredStudioUrl={accessContext?.share_url || ''}
        browserStudioUrl={browserStudioUrl}
        restoreFocus={projectShareTriggerRef.current}
        onClose={closeProjectAccess}
      />
    </main>
  )
}
