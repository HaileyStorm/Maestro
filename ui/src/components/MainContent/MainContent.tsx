import { useRef, useCallback, useState, useEffect, useId, useLayoutEffect, useMemo, type JSX } from 'react'
import { Film, Play, Square, FolderOpen, Plus, Check, Loader2, X, BookMarked, Upload, Trash2, ListChecks, Eye, EyeOff, FolderInput, Lock, LockOpen, KeyRound, Pause, ArrowUp, ArrowDown } from 'lucide-react'
import { TabFilter } from './TabFilter'
import { ThumbnailGallery } from './ThumbnailGallery'
import { MediaFeedItem } from './MediaFeedItem'
import { LlmChat } from '../LlmChat'
import { H3DeliveryRecoveryStatus, OPEN_GALLERY_EVENT } from '../H3DeliveryRecoveryStatus'
import { useStore } from '../../stores/useStore'
import type { GenerationJob } from '../../types'
import * as api from '../../api/client'
import {
  privatePreviewIdentity,
  privatePreviewWorkspaceHasRevealed,
  setPrivatePreviewsForWorkspaceRevealed,
  subscribePrivatePreviewChanges,
} from '../../lib/privatePreview'
import { boundedBackoffDelay, POLL_INTERVAL_MS, useVisibilityPolling } from '../../lib/useVisibilityPolling'

const QUEUE_REFRESH_EVENT = 'maestro:queue-refresh'
const REQUEST_WORKSPACE_UNLOCK_EVENT = 'maestro:request-workspace-unlock'
const RESOURCE_WAIT_TITLE = 'This job is durably queued and will start when the generation lane is available.'
const CPU_RESTART_WARNING = 'CPU text is slower and may be discarded and restarted with acceleration only when that is predicted to deliver sooner.'

type ResourcePresentation = {
  label: string
  title: string
  warning?: string
  tone: 'accelerated' | 'cpu' | 'transition' | 'neutral'
}

function describeResourceExecution(
  descriptor: api.ResourceDescriptor | null | undefined,
): ResourcePresentation | null {
  if (!descriptor) return null

  if (descriptor.intent === 'generation') {
    const label = descriptor.state === 'queued'
      ? 'GPU generation queued'
      : descriptor.state === 'admitted'
        ? 'Starting GPU generation'
        : descriptor.state === 'blocked'
          ? 'GPU generation waiting'
          : descriptor.state === 'released'
            ? 'Generation resources released'
            : 'GPU generation'
    return {
      label,
      title: descriptor.state === 'blocked'
        ? 'Standard GPU generation is waiting for compatible generation resources.'
        : 'Standard generation uses the GPU generation lane.',
      tone: descriptor.state === 'blocked' || descriptor.state === 'released' ? 'neutral' : 'accelerated',
    }
  }

  if (descriptor.state === 'preemption_requested') {
    return {
      label: 'Acceleration restart requested',
      title: 'Acceleration is predicted to deliver sooner. CPU text will be discarded and restarted from zero; its progress will not carry over.',
      warning: 'Acceleration is predicted to deliver sooner; CPU progress will be discarded before restart.',
      tone: 'transition',
    }
  }
  if (descriptor.state === 'resources_releasing') {
    return {
      label: 'Releasing CPU resources',
      title: 'CPU text has stopped and its progress is discarded. Maestro is confirming the resources are released before restarting.',
      warning: 'CPU progress is discarded; waiting for resources to be fully released.',
      tone: 'transition',
    }
  }
  if (descriptor.state === 'restarting_on_accelerator') {
    return {
      label: 'Restarting with acceleration',
      title: 'CPU progress was discarded. This text step is restarting from zero with acceleration; ETA remains unknown until measured.',
      warning: 'Restarting from zero with acceleration; ETA remains unknown until measured.',
      tone: 'transition',
    }
  }

  if (descriptor.execution === 'cpu') {
    const label = descriptor.state === 'queued'
      ? 'CPU-only text queued'
      : descriptor.state === 'admitted'
        ? 'Starting CPU-only text'
        : descriptor.state === 'blocked'
          ? 'CPU text waiting'
          : descriptor.state === 'released'
            ? 'CPU resources released'
            : 'CPU-only text · slower'
    return {
      label,
      title: descriptor.preemptible
        ? CPU_RESTART_WARNING
        : 'This text step is using CPU-only execution, which is slower than acceleration.',
      ...(descriptor.preemptible ? {
        warning: 'Slower CPU work may be discarded and restarted only when acceleration is predicted to deliver sooner.',
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
            ? 'Text resources released'
            : 'Accelerated text',
    title: 'This text step is using accelerated execution.',
    tone: descriptor.state === 'blocked' || descriptor.state === 'released' ? 'neutral' : 'accelerated',
  }
}

function resourcePresentationClass(tone: ResourcePresentation['tone']): string {
  if (tone === 'cpu') return 'border-amber-300/35 bg-amber-300/10 text-amber-200'
  if (tone === 'transition') return 'border-violet-300/35 bg-violet-300/10 text-violet-200'
  if (tone === 'accelerated') return 'border-accent-green/30 bg-accent-green/10 text-accent-green'
  return 'border-border bg-bg-secondary text-text-secondary'
}

function compactEta(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return 'unknown'
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`
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
  const workspaces = useStore(s => s.workspaces)
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
  const remote = accessContext?.remote === true
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
  const dropdownRef = useRef<HTMLDivElement>(null)
  const projectDialogRef = useRef<HTMLDivElement>(null)
  const projectDialogTitleId = useId()
  const requiredProject = remote && (
    !activeWorkspace
    || !workspaces.some(workspace => (
      workspace.name === activeWorkspace && workspace.unlocked !== false
    ))
  )
  const projectTriggerLabel = browsingUploads ? 'Uploads' : (activeWorkspace || 'Select project')
  const projectTriggerAccessibleLabel = browsingUploads
    ? `Browsing uploads. Current project: ${activeWorkspace || 'none'}. Open project selector`
    : activeWorkspace
      ? `Current project: ${activeWorkspace}. Open project selector`
      : 'Select or create a project'
  const unlockedProtectedCount = workspaces.filter(workspace => (
    workspace.password_protected && workspace.unlocked
  )).length

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
  }, [loadWorkspaces, workspaces])

  useEffect(() => {
    const requestUnlock = (event: Event) => {
      const detail = (event as CustomEvent<{ workspace?: string; jobId?: string }>).detail
      const workspace = detail?.workspace || ''
      if (!workspace) return
      setOpen(true)
      beginUnlock(workspace, detail?.jobId || null, true)
    }
    window.addEventListener(REQUEST_WORKSPACE_UNLOCK_EVENT, requestUnlock)
    return () => window.removeEventListener(REQUEST_WORKSPACE_UNLOCK_EVENT, requestUnlock)
  }, [beginUnlock])

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

  const handleCreate = async () => {
    const name = newName.trim().replace(/\s+/g, '-')
    if (!name || (newPassword.length > 0 && newPassword.length < 8) || (remote && !newPassword) || creatingProject) return
    setCreateError(null)
    setCreatingProject(true)
    try {
      await createWorkspace(name, newPassword || undefined)
      setNewName('')
      setNewPassword('')
      setCreating(false)
      setOpen(false)
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : 'Project creation failed')
    } finally {
      setCreatingProject(false)
    }
  }

  const handleUnlock = async () => {
    if (!unlockTarget || unlockingTarget || lockingTarget || lockingAll) return
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
    if (unlockingTarget || lockingTarget || lockingAll) return
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
    if (unlockingTarget || lockingAll || lockingTarget) return
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
    if (!passwordTarget || remote || passwordSaving) return
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
      >
        <FolderOpen size={12} />
        <span className="max-w-[120px] truncate">{projectTriggerLabel}</span>
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
                {requiredProject ? 'Choose a project to enter Maestro' : remote ? 'Projects — unlock with password' : 'Workspaces'}
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
                Unlock an available project, or create a password-protected project for this browser.
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
                    if (ws.password_protected && !ws.unlocked) {
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
                    {ws.password_protected && ws.unlocked && (
                      <span className="shrink-0 text-[8px] uppercase tracking-wide text-accent-green">
                        {ws.remember_policy === 'device' ? 'remembered' : 'session'}
                      </span>
                    )}
                  </span>
                  {ws.name === activeWorkspace && !browsingUploads && <Check size={12} className="shrink-0" />}
                </button>
                {ws.password_protected && (
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
                {!remote && (!ws.password_protected || ws.unlocked) && (
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
                {ws.name !== 'default' && (!remote || ws.unlocked) && (
                  <button
                    onClick={e => handleDelete(ws.name, e)}
                    disabled={deleting === ws.name}
                    className={`px-2 py-2 shrink-0 transition-colors ${
                      confirmDelete === ws.name
                        ? 'text-red-400 bg-red-500/15'
                        : deleting === ws.name
                          ? 'text-text-muted cursor-wait'
                          : 'text-text-muted opacity-0 group-hover:opacity-100 focus-visible:opacity-100 hover:text-red-400'
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
          {!remote && passwordTarget && (
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
          {unlockTarget && (
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
              <label className="flex cursor-pointer items-start gap-1.5 rounded px-1 py-0.5 text-[9px] leading-relaxed text-text-muted hover:bg-bg-hover">
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
          {!requiredProject && <div className="border-t border-border">
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
          <div className="border-t border-border p-2">
            {creating ? (
              <div className="space-y-1.5">
                <input
                  type="text"
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleCreate()}
                  placeholder="workspace-name"
                  className="w-full bg-bg-tertiary border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-accent-blue"
                  autoFocus
                />
                <input
                  type="password"
                  value={newPassword}
                  onChange={event => setNewPassword(event.target.value)}
                  onKeyDown={event => event.key === 'Enter' && void handleCreate()}
                  placeholder={remote ? 'Required password (8+ chars)' : 'Optional password (8+ chars)'}
                  className="w-full bg-bg-tertiary border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-accent-blue"
                />
                {createError && <p className="text-[10px] leading-snug text-red-400">{createError}</p>}
                <button onClick={() => void handleCreate()} disabled={creatingProject || !newName.trim() || (newPassword.length > 0 && newPassword.length < 8) || (remote && !newPassword)} className="flex w-full items-center justify-center gap-1 px-2 py-1 text-xs bg-accent-blue text-white rounded hover:bg-accent-blue-hover disabled:opacity-50">
                  {creatingProject && <Loader2 size={11} className="animate-spin" />} {creatingProject ? 'Creating…' : 'Create project'}
                </button>
              </div>
            ) : (
              <button
                onClick={() => setCreating(true)}
                className="w-full text-left px-1 py-1 text-xs text-accent-blue hover:text-accent-blue-hover flex items-center gap-1"
              >
                <Plus size={12} /> {remote ? 'New project' : 'New Workspace'}
              </button>
            )}
          </div>
          {!remote && accessContext && (
            <div className="border-t border-border px-3 py-2 text-[9px] leading-relaxed text-text-muted">
              <span className={accessContext.cloudflare_enabled ? 'text-accent-green' : 'text-text-muted'}>
                Cloudflare access {accessContext.cloudflare_enabled ? 'enabled' : 'disabled'}.
              </span>{' '}
              {accessContext.cloudflare_enabled
                ? <>Share {accessContext.share_url ? <button onClick={() => void navigator.clipboard?.writeText(accessContext.share_url)} className="text-accent-blue underline">the configured URL</button> : 'the Cloudflare URL shown by Pinokio'}, then have the user select a project and enter its password.</>
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
  onStop: () => void
  onDismiss: () => void
  onToggleLog?: () => void
  onRecoveryAction?: (action: api.QueueRecoveryAction) => void
  onReviewPlan?: () => void
  logOpen?: boolean
  logEvents?: api.JobLogEvent[]
  logError?: string | null
}) {
  const machineControls = useStore(s => s.accessContext?.machine_controls === true)
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
    ? `Exact ${job.oomInfo.requested_target} delivery`
    : 'Exact delivery'
  const hasLocalEvents = (job.logEvents?.length || 0) > 0
  const canOpenLog = (!job.oomInfo || machineControls) && (hasLocalEvents || api.isBackendJobId(job.id))
  const errorText = job.error || job.message || (job.status === 'cancelled' ? 'Cancelled' : 'Generation failed')
  const resourcePresentation = describeResourceExecution(job.resourceDescriptor)
  const resourceWaitTitle = job.queueWaitReason === 'resource_wait' ? RESOURCE_WAIT_TITLE : undefined
  const queueWaitLabel = job.status !== 'running' && !isFailed && !recoveryBlocked ? ({
    held: 'Held — use Start next or Resume when ready',
    queue_paused: 'Queue paused — use Start next or Resume queue',
    registering: 'Registering with the scheduler',
    preparing: 'Preparing the generation',
    waiting_for_plan_approval: 'Generation plan ready for review',
    waiting_for_plan_terms: 'Waiting for required model terms',
    waiting_for_turn: 'Waiting for earlier queued work',
    waiting_for_active_generation: 'Waiting for another generation on this host',
    waiting_for_other_user: 'Waiting for another generation on this host',
    resource_wait: 'Waiting for generation resources',
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
            onClick={onDismiss}
            className="absolute top-2 right-2 p-1.5 rounded-full bg-bg-active text-text-secondary hover:bg-red-600 hover:text-white transition-colors z-10"
            title="Dismiss"
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
                    : isDeliveryOom
                      ? isDeliveryRecoveryChild
                        ? 'Delivery Retry Failed'
                        : nativeRecoveryAvailable
                          ? 'Delivery Failed After Native Generation'
                          : 'Delivery Failed'
                      : 'Generation Failed')
                : recoveryBlocked
                  ? 'Recovery Needed'
                  : recoveryState === 'restored'
                    ? 'Generation Restored'
                    : recoveryState === 'interrupted' || job.recoveryInterrupted
                      ? 'Generation Interrupted — Recovery Preserved'
                      : recoveryState === 'retrying'
                        ? 'Recovery Queued'
                        : job.status === 'preparing'
                          ? job.phase === 'planning_generation' ? 'Planning generation' : 'Enhancing prompt'
                          : job.status === 'waiting_for_plan_approval'
                            ? 'Plan ready for review'
                            : job.status === 'queued' ? 'Queued...' : 'Generating...'}
            </p>
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
                {job.h3SegmentPlan && (
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
                      ? `Server auto-accepts its frozen plan in ${Math.ceil(planReviewSeconds)}s`
                      : 'Server is auto-accepting its frozen plan…'}
                </p>
              </>
            )}
            {!isFailed && !recoveryBlocked && (
              <p className="mt-1 text-[10px] text-text-secondary">
                {queuedH3Runtime != null
                  ? job.h3SegmentPlan?.segments.length
                    ? `Planned time ${compactEta(queuedH3Runtime)} after start`
                    : `Estimated time ${compactEta(queuedH3Runtime)} after start`
                  : `Overall ETA ${compactEta(job.etaSeconds)}`}
                {job.status === 'running' && hasWindows && job.modelType?.startsWith('minimax_h3')
                    ? ` · Current segment ETA ${compactEta(job.subtaskEtaSeconds)}`
                    : ''}
              </p>
            )}
            {recoveryBlocked && (
              <div className="mt-2 rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-left text-[10px] text-text-secondary">
                <p className="font-medium text-amber-200">
                  {job.recoveryReasonText || 'This generation is waiting for a safe recovery decision.'}
                </p>
                <p className="mt-1">
                  Recovery attempt {job.recoveryAttempt ?? 0} of {job.recoveryAttemptLimit ?? 0}.
                </p>
                {job.recoveryRerunsDenoise && (
                  <p className="mt-1">
                    The current safe unit restarts its denoise work; completed units are retained.
                  </p>
                )}
                {estimateRuntime(job.estimateAfterResume) != null && (
                  <p className="mt-1">
                    Estimated work after resume: {compactEta(estimateRuntime(job.estimateAfterResume))}.
                  </p>
                )}
                {!!job.recoveryActions?.length && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {job.recoveryActions.map(action => (
                      <button
                        key={action}
                        type="button"
                        onClick={() => onRecoveryAction?.(action)}
                        className="rounded bg-amber-300/15 px-2.5 py-1 text-[10px] font-medium text-amber-200 hover:bg-amber-300/25"
                      >
                        {action === 'resume'
                          ? recoveryState === 'blocked_remote_reauth' ? 'Unlock project and resume' : 'Resume recovery'
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
            {(job.modelType || job.workspace) && (
              <p className="mt-1.5 text-[9px] text-text-muted truncate">
                {[job.modelType, job.workspace && `Project: ${job.workspace}`].filter(Boolean).join(' · ')}
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
                    const generatedFrames = segment.generated_frames ?? segment.frames
                    const publishedFrames = segment.published_frames ?? generatedFrames
                    const generatedSeconds = segment.generated_duration_seconds ?? segment.duration_seconds
                    const publishedSeconds = segment.published_duration_seconds ?? generatedSeconds
                    return (
                      <div
                        key={segment.index}
                        title={`Segment ${segment.index}: ${ref2va ? 'Ref2VA' : 'FL2VA'} · ${publishedSeconds.toFixed(2)}s published (${publishedFrames}f)${generatedFrames !== publishedFrames ? ` · ${generatedSeconds.toFixed(2)}s generated (${generatedFrames}f)` : ''} · ${segment.model_reason}${boundary ? ` · ${boundary}` : ''}`}
                        className={`min-w-[44px] rounded border px-1.5 py-1 text-center transition-colors ${
                          active ? 'border-white/70 ring-1 ring-white/30' : 'border-transparent'
                        } ${ref2va ? 'bg-violet-500/25 text-violet-200' : 'bg-sky-500/25 text-sky-200'}`}
                      >
                        <div className="text-[9px] font-semibold">{segment.index} · {ref2va ? 'REF' : 'FL'}</div>
                        <div className="text-[8px] opacity-75">{boundary === 'precut' ? 'pre-cut' : boundary || 'start'}</div>
                      </div>
                    )
                  })}
                </div>
                {job.currentSegmentReason && (
                  <p className="mt-1.5 truncate text-[9px] text-text-muted">
                    {job.currentSegmentModel?.endsWith('ref2va') ? 'Ref2VA' : 'FL2VA'}: {job.currentSegmentReason}
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
                      ? `Native generation succeeded. Maestro preserved it privately when ${deliveryTarget.toLowerCase()} ran out of VRAM${(job.oomInfo?.retry_count ?? 0) > 0 ? ' after one automatic retry' : ''}. Recovery status is shown below.`
                      : isDeliveryRecoveryChild
                        ? 'A delivery-only recovery attempt failed. Refreshed options remain on the original failed generation card.'
                      : isDeliveryOom
                        ? `${deliveryTarget} ran out of VRAM after native generation, but no preserved native result is available.`
                    : canOpenLog
                      ? 'Generation failed. Open technical details or event history for more information.'
                      : 'Generation failed before a server job was created. The technical details below contain the available error.'}
                </p>
                {isDeliveryOom && !isDeliveryRecoveryChild && job.workspace && api.isBackendJobId(job.id) && (
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
            ? 'Recovery blocked · live queue position and ETA resume after recovery'
            : isFailed
            ? nativeRecoveryAvailable
              ? 'Source delivery failed · recovery state shown above'
              : isDeliveryRecoveryChild
                ? 'Delivery recovery child failed · return to the original failed card'
              : 'Click × to dismiss — the tile stays so you can see what failed'
            : queueWaitLabel || phase || 'Preparing...'}
        </div>
        {!isFailed && (
          <button
            onClick={onStop}
            className="flex items-center gap-1 text-xs text-red-400 hover:text-red-300 transition-colors shrink-0 ml-2"
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
              <span>{event.status} · {event.progress}%</span>{' '}
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
  return `${summary.running} running · ${summary.preparing ?? 0} preparing · ${summary.approval_waiting ?? 0} awaiting review · ${summary.waiting} waiting · ${summary.held} held · ${summary.registering} registering`
}

function queuePositionLabel(position: number | null, waiting: number): string {
  if (position == null) return 'Registering with the scheduler'
  if (position === 1) return waiting > 1 ? `Next in line · 1 of ${waiting}` : 'Next in line'
  const ahead = position - 1
  return `${ahead} ${ahead === 1 ? 'job' : 'jobs'} ahead · ${position} of ${waiting}`
}

function QueuePanel({
  jobs,
  onStop,
  onDismiss,
  queue,
  queueError,
  refreshQueue,
}: {
  jobs: GenerationJob[]
  onStop: (jobId: string) => void
  onDismiss: (jobId: string) => void
  queue: api.QueueState | null
  queueError: string | null
  refreshQueue: () => Promise<void>
}) {
  const machineControls = useStore(s => s.accessContext?.machine_controls === true)
  const resumeJobRecovery = useStore(s => s.resumeJobRecovery)
  const retryJobRecovery = useStore(s => s.retryJobRecovery)
  const openH3PlanReview = useStore(s => s.openH3PlanReview)
  const planReviewError = useStore(s => s.h3PlanReviewError)
  const [error, setError] = useState<string | null>(null)
  const [countDrafts, setCountDrafts] = useState<Record<string, number>>({})
  const [logJobId, setLogJobId] = useState<string | null>(null)
  const [logEvents, setLogEvents] = useState<api.JobLogEvent[]>([])
  const [logError, setLogError] = useState<string | null>(null)

  const queueInfo = useMemo(
    () => new Map((queue?.jobs || []).map(item => [item.job_id, item])),
    [queue],
  )
  const projectedChildByParent = useMemo(() => {
    const jobsById = new Map(jobs.map(job => [job.id, job]))
    const projected = new Map<string, GenerationJob>()
    const liveStatuses = new Set<GenerationJob['status']>([
      'preparing', 'waiting_for_plan_approval', 'queued', 'running',
    ])
    for (const child of jobs) {
      if (!child.parentJobId || child.parentJobId === child.id) continue
      const parent = jobsById.get(child.parentJobId)
      const childInfo = queueInfo.get(child.id)
      const descriptor = childInfo?.resource_descriptor ?? child.resourceDescriptor
      if (!parent || !childInfo || !liveStatuses.has(parent.status) || !liveStatuses.has(child.status)) continue
      if (childInfo.held || childInfo.hold_after_output || descriptor?.state === 'blocked') continue
      const recoveryStates = [child.recoveryState, childInfo.recovery_state]
      if (child.recoveryBlocked || child.recoveryActionable || child.recoveryInterrupted
        || childInfo.recovery_blocked || childInfo.recovery_actionable || childInfo.recovery_interrupted
        || recoveryStates.some(state => state === 'interrupted' || state?.startsWith('blocked'))
        || (child.recoveryActions?.length ?? 0) > 0
        || (childInfo.recovery_actions?.length ?? 0) > 0) continue
      projected.set(parent.id, child)
    }
    return projected
  }, [jobs, queueInfo])
  const visibleJobs = useMemo(() => {
    const projectedChildIds = new Set(
      [...projectedChildByParent.values()].map(child => child.id),
    )
    return jobs.filter(job => !projectedChildIds.has(job.id))
  }, [jobs, projectedChildByParent])

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
    <div className="flex-1 overflow-y-auto p-3 md:p-4">
      <div className="mx-auto max-w-4xl space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-bg-tertiary px-3 py-2">
          <div>
            <p className="text-xs font-medium text-text-primary">Generation queue</p>
            <p className="text-[10px] text-text-muted">
              {queue?.paused ? 'Paused — queued jobs will not start.' : queue?.pause_after_current ? 'Will pause after the current output.' : 'Running in priority order.'}
            </p>
            {queue?.summary && (
              <p className="mt-0.5 text-[10px] text-text-secondary">{queueSummaryLabel(queue.summary)}</p>
            )}
            <details className="group mt-1.5 max-w-2xl text-[10px] text-text-muted">
              <summary className="cursor-pointer text-text-secondary hover:text-text-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-blue">
                How queue priority works
              </summary>
              <div className="mt-1.5 space-y-1 rounded-md border border-border bg-bg-primary/40 px-2.5 py-2 leading-relaxed">
                <p>User-set priority is applied before queue-order and residency choices; work waits until compatible resources are available.</p>
                <p>Reusing the exact loaded model can reorder otherwise eligible work.</p>
                <p>A starvation guard can preserve queue order to prevent excessive delay. Recent-service fair share is planned but is not active in this build; each row's live reason is authoritative.</p>
                <p>Only CPU work explicitly marked preemptible may be discarded and restarted from zero, and only when acceleration is predicted to deliver sooner.</p>
              </div>
            </details>
          </div>
          {machineControls && <div className="flex items-center gap-2">
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
        {(error || queueError || planReviewError) && <div className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error || queueError || planReviewError}</div>}
        <PipelinePlaceholder />
        {visibleJobs.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-text-muted">
            {queue?.summary.active_total
              ? `No jobs from this session. ${queue.summary.active_total} active globally.`
              : 'No queued, running, or failed generations.'}
          </div>
        ) : visibleJobs.map((job, index) => {
          const projectedChild = projectedChildByParent.get(job.id)
          const effectiveJob = projectedChild ?? job
          const schedulerJobId = effectiveJob.id
          const info = queueInfo.get(schedulerJobId)
          const effectiveResourceDescriptor = projectedChild
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
              : queuePositionLabel(info?.position ?? null, queue?.summary.waiting ?? 0)
          const waitDetail = info?.wait_reason === 'queue_paused'
            ? 'Queue paused'
            : info?.wait_reason === 'waiting_for_plan_terms'
              ? 'Waiting for required model terms'
            : info?.wait_reason === 'resource_wait'
              ? 'Waiting for generation resources'
            : info?.wait_reason === 'waiting_for_active_generation'
              || info?.wait_reason === 'waiting_for_other_user'
              ? 'Waiting for another generation on this host'
              : null
          const resourceWaitTitle = info?.wait_reason === 'resource_wait' ? RESOURCE_WAIT_TITLE : undefined
          const resourcePresentation = describeResourceExecution(effectiveResourceDescriptor)
          const residencyMessage = info?.status === 'running' && (
            info?.queue_reorder_reason === 'resident_base'
            || info?.queue_reorder_reason === 'resident_affinity'
          )
            ? 'Reordered to reuse the loaded model'
            : info?.status === 'running' && info.queue_reorder_reason === 'starvation_guard'
              ? 'Kept queue order to prevent long delays'
              : null
          return (
            <div key={job.id || `pending-${index}`} className="space-y-1.5">
              {info && (job.status === 'preparing' || job.status === 'waiting_for_plan_approval' || job.status === 'queued' || job.status === 'running') && (
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-bg-secondary px-2.5 py-1.5 text-[10px] text-text-muted">
                  {info.recovery_blocked ? (
                    <>
                      <span className="text-amber-300">
                        Recovery blocked · {info.recovery_reason_text || 'safe recovery input required'}
                      </span>
                      <button className="rounded border border-border px-1.5 py-0.5 hover:bg-bg-hover" onClick={() => void toggleLog(job)}>Log</button>
                    </>
                  ) : (
                    <>
                      <span title={resourceWaitTitle}>{queueRowLabel}{waitDetail ? ` · ${waitDetail}` : ''} · Priority {info.priority} · Outputs {info.produced_outputs}/{info.requested_outputs}</span>
                      <span className="text-[9px] text-text-secondary">
                        ETA {compactEta(info.eta_seconds)}
                        {info.subtask_eta_seconds != null ? ` · current task ${compactEta(info.subtask_eta_seconds)}` : ''}
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
                        <button className="rounded border border-border px-1.5 py-0.5 hover:bg-bg-hover" onClick={() => void toggleLog(effectiveJob)}>Log</button>
                        {info.status === 'queued' && <>
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
                        {info.status === 'running' && (
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
  const workspaces = useStore(s => s.workspaces)
  const activeWorkspace = useStore(s => s.activeWorkspace)
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

  const run = async (operation: () => Promise<string[]>) => {
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
    <div className="border-b border-border bg-bg-tertiary/70 px-2 py-2 md:px-6">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-text-primary">{selected.length} selected</span>
        <button onClick={selectAll} disabled={!outputs.length || busy} className="rounded-md border border-border px-2 py-1 text-[10px] text-text-secondary hover:text-text-primary disabled:opacity-40">
          Select loaded
        </button>
        <button onClick={clear} disabled={busy} className="rounded-md border border-border px-2 py-1 text-[10px] text-text-secondary hover:text-text-primary">Clear</button>
        <div className="mx-1 hidden h-5 w-px bg-border sm:block" />
        <select
          value={target}
          onChange={event => setTarget(event.target.value)}
          disabled={busy}
          className="rounded-md border border-border bg-bg-secondary px-2 py-1 text-[10px] text-text-primary"
        >
          <option value="">Move to project…</option>
          {workspaces.filter(workspace => workspace.name !== activeWorkspace && workspace.unlocked !== false).map(workspace => (
            <option key={workspace.name} value={workspace.name}>{workspace.name}</option>
          ))}
        </select>
        <button
          onClick={() => void run(() => moveSelected(target))}
          disabled={!selected.length || !target || busy}
          className="flex items-center gap-1 rounded-md bg-accent-blue px-2 py-1 text-[10px] text-white disabled:opacity-40"
        >
          <FolderInput size={11} /> Move lineage
        </button>
        <button title="Blur selected gallery previews by default; project access is unchanged" onClick={() => void run(() => setPrivacy(true))} disabled={!selected.length || busy} className="flex items-center gap-1 rounded-md border border-violet-500/40 px-2 py-1 text-[10px] text-violet-200 disabled:opacity-40">
          <EyeOff size={11} /> Blur previews
        </button>
        <button title="Show selected gallery previews normally; project access is unchanged" onClick={() => void run(() => setPrivacy(false))} disabled={!selected.length || busy} className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[10px] text-text-secondary disabled:opacity-40">
          <Eye size={11} /> Show previews
        </button>
        <button
          onClick={() => confirmDelete ? void run(deleteSelected) : setConfirmDelete(true)}
          disabled={!selected.length || busy}
          className="flex items-center gap-1 rounded-md border border-red-500/40 px-2 py-1 text-[10px] text-red-300 disabled:opacity-40"
          title="Deletes selected finals and every sidecar-linked component, window, and temporary artifact"
        >
          <Trash2 size={11} /> {confirmDelete ? `Confirm delete ${selected.length}` : 'Delete + linked'}
        </button>
        <button onClick={() => setSelectionMode(false)} disabled={busy} className="ml-auto p-1 text-text-muted hover:text-text-primary"><X size={14} /></button>
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
        <button
          onClick={() => stopPipeline()}
          className="flex items-center gap-1 text-xs text-red-400 hover:text-red-300 transition-colors shrink-0 ml-2"
        >
          <Square size={11} />
          Stop
        </button>
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
  const browsingUploads = useStore(s => s.browsingUploads)
  const mediaFilter = useStore(s => s.mediaFilter)
  const outputArtifactScope = useStore(s => s.outputArtifactScope)
  const outputSearchQuery = useStore(s => s.outputSearchQuery)
  const gallerySelectionMode = useStore(s => s.gallerySelectionMode)
  const setGallerySelectionMode = useStore(s => s.setGallerySelectionMode)
  const openQueueAfterSubmit = useStore(s => s.openQueueAfterSubmit)
  const accessContext = useStore(s => s.accessContext)
  const loadAccessContext = useStore(s => s.loadAccessContext)
  const reconcileQueueState = useStore(s => s.reconcileQueueState)
  const [shareCopied, setShareCopied] = useState(false)
  const [mainView, setMainView] = useState<'gallery' | 'queue' | 'chat'>('gallery')
  const [queueTabState, setQueueTabState] = useState<api.QueueState | null>(null)
  const [queueTabError, setQueueTabError] = useState<string | null>(null)
  const [accessPollAttempt, setAccessPollAttempt] = useState(0)
  const [privatePreviewVersion, setPrivatePreviewVersion] = useState(0)
  const queuePollSequence = useRef(0)
  const queuePollAbort = useRef<AbortController | null>(null)
  const seenJobIds = useRef(new Set(jobs.map(job => job.id).filter(Boolean)))

  useEffect(() => subscribePrivatePreviewChanges(() => {
    setPrivatePreviewVersion(version => version + 1)
  }), [])

  const anyProjectPrivatePreviewRevealed = useMemo(() => {
    void privatePreviewVersion
    return Boolean(activeWorkspace) && privatePreviewWorkspaceHasRevealed(activeWorkspace)
  }, [activeWorkspace, privatePreviewVersion])

  useEffect(() => {
    const openGallery = () => setMainView('gallery')
    window.addEventListener(OPEN_GALLERY_EVENT, openGallery)
    return () => window.removeEventListener(OPEN_GALLERY_EVENT, openGallery)
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
    const sequence = ++queuePollSequence.current
    queuePollAbort.current?.abort()
    const controller = new AbortController()
    queuePollAbort.current = controller
    const relayAbort = () => controller.abort()
    pollSignal?.addEventListener('abort', relayAbort, { once: true })
    try {
      const next = await api.fetchQueueState(controller.signal)
      if (sequence !== queuePollSequence.current || controller.signal.aborted) return
      reconcileQueueState(next)
      setQueueTabState(next)
      setQueueTabError(null)
    } catch (reason) {
      if (sequence !== queuePollSequence.current || controller.signal.aborted) return
      setQueueTabState(null)
      setQueueTabError(
        reason instanceof Error ? reason.message : 'Queue update failed',
      )
      throw reason
    } finally {
      pollSignal?.removeEventListener('abort', relayAbort)
      if (queuePollAbort.current === controller) queuePollAbort.current = null
    }
  }, [reconcileQueueState])

  useEffect(() => {
    const refresh = () => {
      if (!document.hidden) void refreshQueue().catch(() => {})
    }
    window.addEventListener(QUEUE_REFRESH_EVENT, refresh)
    return () => window.removeEventListener(QUEUE_REFRESH_EVENT, refresh)
  }, [refreshQueue])

  const activeQueueJobs = jobs.filter(job => (
    job.status === 'preparing'
    || job.status === 'waiting_for_plan_approval'
    || job.status === 'queued'
    || job.status === 'running'
  ))
  const queueActiveTotal = queueTabState?.summary.active_total ?? 0
  const queueActivity = activeQueueJobs.length > 0 || queueActiveTotal > 0

  useVisibilityPolling(
    refreshQueue,
    queueActivity
      ? POLL_INTERVAL_MS.queueActiveVisible
      : POLL_INTERVAL_MS.queueIdleVisible,
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

  const currentJob = activeQueueJobs.find(job => job.status === 'running')
  const queueStateLabel = (queueTabState?.summary.running ?? (currentJob ? 1 : 0)) > 0
    ? (queueTabState?.pause_after_current ? 'running · pause next' : 'running')
    : (queueTabState?.summary.approval_waiting ?? 0) > 0
      ? 'review needed'
      : (queueTabState?.summary.preparing ?? 0) > 0
        ? 'preparing'
    : queueTabState?.paused
      ? 'paused'
      : (queueTabState?.summary.held ?? 0) > 0
        && (queueTabState?.summary.waiting ?? 0) === 0
        && (queueTabState?.summary.registering ?? 0) === 0
        ? 'held'
        : queueActiveTotal > 0
          ? 'waiting'
          : jobs.some(job => job.status === 'failed')
            ? 'attention'
            : 'idle'
  const queueStateColor = (queueTabState?.summary.running ?? (currentJob ? 1 : 0)) > 0
    ? 'bg-accent-green'
    : (queueTabState?.summary.approval_waiting ?? 0) > 0 || queueTabState?.paused || (queueTabState?.summary.held ?? 0) > 0
      ? 'bg-amber-400'
      : jobs.some(job => job.status === 'failed')
        ? 'bg-red-400'
        : queueActiveTotal > 0 ? 'bg-accent-blue' : 'bg-text-muted'
  const queueTooltip = queueTabState
    ? `Queue: ${queueTabState.summary.active_total} active · ${queueSummaryLabel(queueTabState.summary)}${queueTabState.paused ? ' · paused' : queueTabState.pause_after_current ? ' · pauses after current output' : ''}`
    : 'Queue status loading'
  const ownedJobEtaTooltip = currentJob
    ? ` · Your job: overall ETA ${compactEta(currentJob.etaSeconds)}${currentJob.subtaskEtaSeconds != null ? ` · current task ${compactEta(currentJob.subtaskEtaSeconds)}` : ''}`
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
      <div className="relative z-40 flex flex-wrap items-start justify-between gap-2 border-b border-border px-2 py-2 md:px-6 md:py-3">
        {mainView === 'gallery' ? <TabFilter /> : <div className="text-xs font-medium text-text-primary">{mainView === 'queue' ? 'Queue' : 'LLM Chat'}</div>}
        <div className="ml-auto flex min-w-0 max-w-full flex-wrap items-center justify-end gap-1.5 sm:gap-2">
          <div className="flex rounded-md border border-border bg-bg-tertiary p-0.5 text-[10px]">
            <button className={`rounded px-2 py-1 ${mainView === 'gallery' ? 'bg-bg-active text-text-primary' : 'text-text-muted'}`} onClick={() => setMainView('gallery')}>Gallery</button>
            <button
              title={`${queueTooltip}${ownedJobEtaTooltip}`}
              className={`flex items-center gap-1.5 rounded px-2 py-1 ${mainView === 'queue' ? 'bg-bg-active text-text-primary' : 'text-text-muted'}`}
              onClick={() => setMainView('queue')}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${queueStateColor}`} />
              <span>Queue</span>
              {queueActiveTotal > 0 && <span className="rounded-full bg-bg-primary/70 px-1 text-[9px]">{queueActiveTotal}</span>}
              <span className="hidden lg:inline text-[9px]">{queueStateLabel}</span>
              {currentJob && (
                <span className="hidden xl:inline text-[9px]">
                  · {Math.round(currentJob.overallProgress ?? currentJob.progress * 100)}% · ETA {compactEta(currentJob.etaSeconds)}
                  {currentJob.subtaskEtaSeconds != null ? ` · task ${compactEta(currentJob.subtaskEtaSeconds)}` : ''}
                </span>
              )}
            </button>
            <button className={`rounded px-2 py-1 ${mainView === 'chat' ? 'bg-bg-active text-text-primary' : 'text-text-muted'}`} onClick={() => setMainView('chat')}>Chat</button>
          </div>
          <div className="text-[10px] md:text-xs text-text-muted hidden md:block">
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
          {mainView === 'gallery' && activeWorkspace && !browsingUploads && <>
            <span id="private-preview-session-note" className="hidden text-[9px] text-text-muted xl:inline">
              Browser-session preview only; project access unchanged.
            </span>
            <button
              type="button"
              aria-pressed={anyProjectPrivatePreviewRevealed}
              aria-describedby="private-preview-session-note"
              aria-label={`${anyProjectPrivatePreviewRevealed ? 'Blur' : 'Reveal'} all private previews for project ${activeWorkspace}`}
              onClick={() => setPrivatePreviewsForWorkspaceRevealed(
                activeWorkspace,
                !anyProjectPrivatePreviewRevealed,
              )}
              title={`${anyProjectPrivatePreviewRevealed ? 'Blur' : 'Reveal'} all private previews for this project in this browser session; project access is unchanged`}
              className="flex items-center gap-1 rounded-md border border-violet-500/40 px-2 py-1 text-[10px] text-violet-200 transition-colors hover:bg-violet-500/10"
            >
              {anyProjectPrivatePreviewRevealed ? <EyeOff size={12} /> : <Eye size={12} />}
              {anyProjectPrivatePreviewRevealed ? 'Blur all' : 'Reveal all'}
            </button>
          </>}
          {mainView === 'gallery' && <button
            type="button"
            onClick={() => setGallerySelectionMode(!gallerySelectionMode)}
            className={`flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] transition-colors ${gallerySelectionMode ? 'border-accent-blue bg-accent-blue/15 text-accent-blue' : 'border-border text-text-secondary hover:text-text-primary'}`}
          >
            <ListChecks size={12} /> Select
          </button>}
          <WorkspaceSelector />
        </div>
      </div>
      {mainView === 'gallery' && gallerySelectionMode && <GalleryBulkToolbar />}

      {/* Content area: feed + thumbnails */}
      {mainView === 'chat' ? (
        <LlmChat />
      ) : mainView === 'queue' ? (
        <QueuePanel
          jobs={jobs}
          onStop={stopGeneration}
          onDismiss={dismissJob}
          queue={queueTabState}
          queueError={queueTabError}
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
          {!outputsLoading && outputs.length === 0 && jobs.length === 0 && (() => {
            const noun = generationMode === 'image' ? 'images'
              : generationMode === 'audio' ? 'audio' : 'videos'
            const example = generationMode === 'image'
              ? 'a neon city street at night, cinematic'
              : generationMode === 'audio'
              ? 'a dreamy synthwave track about the ocean'
              : 'a golden retriever surfing a big wave, slow motion'
            return (
              <div className="flex items-center justify-center min-h-[300px] px-6">
                <div className="flex flex-col items-center gap-4 text-center max-w-sm">
                  <div className="w-16 h-16 rounded-2xl bg-bg-active flex items-center justify-center text-text-muted">
                    <Play size={24} />
                  </div>
                  <p className="text-sm text-text-secondary">Your generated {noun} will appear here.</p>
                  <ol className="text-xs text-text-muted space-y-1.5 text-left">
                    <li><span className="text-accent-blue font-medium">1.</span> Pick a model in the sidebar (a good default is already selected).</li>
                    <li><span className="text-accent-blue font-medium">2.</span> Type a prompt — e.g. <span className="text-text-secondary italic">“{example}”</span></li>
                    <li><span className="text-accent-blue font-medium">3.</span> Hit Generate.</li>
                  </ol>
                  <p className="text-[11px] text-text-muted leading-snug">
                    Heads up: if needed, this Maestro host downloads and prepares
                    model files before generation starts. The shared host cache is
                    reused when possible; loading into RAM/VRAM is a separate step.
                    Follow preparation status on the generation card.
                  </p>
                  <button
                    type="button"
                    onClick={() => useStore.getState().setRecipesOpen(true)}
                    className="mt-1 flex items-center gap-1.5 px-3 py-1.5 text-xs bg-accent-blue/10 border border-accent-blue/30 rounded-lg text-accent-blue hover:bg-accent-blue/20 transition-colors"
                    aria-label="Browse recipes"
                  >
                    <BookMarked size={13} /> Browse recipes
                  </button>
                </div>
              </div>
            )
          })()}
        </div>

        {/* Thumbnail sidebar */}
        <ThumbnailGallery
          activeIndex={activeIndex}
          onThumbnailClick={handleThumbnailClick}
        />
      </div>
      )}
    </main>
  )
}
