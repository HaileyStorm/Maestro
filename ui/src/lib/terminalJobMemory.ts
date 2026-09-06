import type { AccountContext, GenerationJob } from '../types'

const STORAGE_KEY = 'maestro.terminal-jobs.v2'
const LEGACY_STORAGE_KEY = 'maestro.terminal-jobs.v1'
const MAX_JOBS = 30

export function terminalJobScope(context: AccountContext | null | undefined): string | null {
  if (context?.enabled === false) return 'local'
  if (context?.enabled === true && context.authenticated === true && context.account?.id) {
    return `account:${context.account.id}`
  }
  return null
}

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

export function loadTerminalJobs(scope: string | null = null): GenerationJob[] {
  if (!scope || !canUseStorage()) return []
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (parsed?.scope !== scope || !Array.isArray(parsed.jobs)) return []
    return (parsed.jobs as unknown[])
      .map(item => compactTerminalJob(item as GenerationJob))
      .filter((item): item is GenerationJob => item != null)
      .slice(0, MAX_JOBS)
  } catch {
    return []
  }
}

export function clearTerminalJobs(): void {
  if (!canUseStorage()) return
  try {
    sessionStorage.removeItem(STORAGE_KEY)
    sessionStorage.removeItem(LEGACY_STORAGE_KEY)
  } catch {
    /* private mode */
  }
}

export function persistTerminalJobs(jobs: readonly GenerationJob[], scope: string | null = null): void {
  // Do not erase the previous page's record while account bootstrap is pending.
  if (!scope || !canUseStorage()) return
  const terminal = jobs
    .map(compactTerminalJob)
    .filter((item): item is GenerationJob => item != null)
    .slice(0, MAX_JOBS)
  try {
    sessionStorage.removeItem(LEGACY_STORAGE_KEY)
    if (terminal.length === 0) sessionStorage.removeItem(STORAGE_KEY)
    else sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ scope, jobs: terminal }))
  } catch {
    /* private mode or quota */
  }
}
