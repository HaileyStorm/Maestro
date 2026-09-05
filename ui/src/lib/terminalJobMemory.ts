import type { GenerationJob } from '../types'

const STORAGE_KEY = 'maestro.terminal-jobs.v1'
const MAX_JOBS = 30

function canUseStorage(): boolean {
  try {
    return typeof sessionStorage !== 'undefined'
  } catch {
    // Browsers can deny access to the storage property itself.
    return false
  }
}

export function compactTerminalJob(job: GenerationJob): GenerationJob | null {
  if ((job.status !== 'failed' && job.status !== 'cancelled') || !job.id) return null
  return {
    id: job.id,
    createdAt: job.createdAt,
    status: job.status,
    progress: job.progress,
    step: job.step,
    totalSteps: job.totalSteps,
    phase: job.phase || '',
    message: job.message || '',
    outputFiles: Array.isArray(job.outputFiles) ? job.outputFiles.slice(0, 8) : [],
    error: job.error ?? null,
    failureDetails: job.failureDetails ?? null,
    oomInfo: job.oomInfo ?? null,
    modelType: job.modelType,
    generationMode: job.generationMode,
    workspace: job.workspace,
    windowCurrent: job.windowCurrent,
    windowTotal: job.windowTotal,
    overallProgress: job.overallProgress,
    recoveryState: job.recoveryState,
    recoveryBlocked: job.recoveryBlocked,
    recoveryAttempt: job.recoveryAttempt,
    recoveryAttemptLimit: job.recoveryAttemptLimit,
    recoveryRerunsDenoise: job.recoveryRerunsDenoise,
    recoveryReason: job.recoveryReason,
    recoveryReasonText: job.recoveryReasonText,
    recoveryActionable: job.recoveryActionable,
    recoveryActions: job.recoveryActions,
  }
}

export function loadTerminalJobs(): GenerationJob[] {
  if (!canUseStorage()) return []
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .map(item => compactTerminalJob(item as GenerationJob))
      .filter((item): item is GenerationJob => item != null)
      .slice(0, MAX_JOBS)
  } catch {
    return []
  }
}

export function persistTerminalJobs(jobs: readonly GenerationJob[]): void {
  if (!canUseStorage()) return
  const terminal = jobs
    .map(compactTerminalJob)
    .filter((item): item is GenerationJob => item != null)
    .slice(0, MAX_JOBS)
  try {
    if (terminal.length === 0) sessionStorage.removeItem(STORAGE_KEY)
    else sessionStorage.setItem(STORAGE_KEY, JSON.stringify(terminal))
  } catch {
    /* private mode or quota */
  }
}

export function mergeTerminalJobs(
  current: readonly GenerationJob[],
  remembered: readonly GenerationJob[],
): GenerationJob[] {
  const have = new Set(current.map(job => job.id).filter(Boolean))
  const extras = remembered.filter(job => job.id && !have.has(job.id))
  return extras.length === 0 ? [...current] : [...current, ...extras]
}
