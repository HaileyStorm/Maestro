import type { QueueJobState, QueueState } from '../api/client'
import type { GenerationJob } from '../types'

const ACTIVE_STATUSES = new Set<GenerationJob['status']>([
  'preparing',
  'waiting_for_plan_approval',
  'queued',
  'running',
])

export interface LogicalQueueTarget {
  publicJob: GenerationJob
  schedulerJobId: string
  schedulerJob?: GenerationJob
  queueJob?: QueueJobState
}

export interface LogicalQueueProjection {
  visibleJobs: GenerationJob[]
  schedulerTargetByPublicJobId: Map<string, LogicalQueueTarget>
  summary: QueueState['summary']
  activeCount: number
}

export function isActiveLogicalQueueJob(job: GenerationJob): boolean {
  return ACTIVE_STATUSES.has(job.status)
}

function recoveryNeedsDirectAction(job: GenerationJob, queueJob?: QueueJobState): boolean {
  const states = [job.recoveryState, queueJob?.recovery_state]
  return job.status === 'waiting_for_plan_approval'
    || (queueJob?.resource_descriptor ?? job.resourceDescriptor)?.state === 'blocked'
    || job.recoveryBlocked === true
    || job.recoveryInterrupted === true
    || job.recoveryActionable === true
    || queueJob?.recovery_blocked === true
    || queueJob?.recovery_interrupted === true
    || queueJob?.recovery_actionable === true
    || (job.recoveryActions?.length ?? 0) > 0
    || (queueJob?.recovery_actions?.length ?? 0) > 0
    || states.some(state => state === 'interrupted' || state?.startsWith('blocked'))
}

function queueRecoveryNeedsDirectAction(queueJob: QueueJobState): boolean {
  return queueJob.status === 'waiting_for_plan_approval'
    || queueJob.resource_descriptor?.state === 'blocked'
    || queueJob.recovery_blocked === true
    || queueJob.recovery_interrupted === true
    || queueJob.recovery_actionable === true
    || (queueJob.recovery_actions?.length ?? 0) > 0
    || queueJob.recovery_state === 'interrupted'
    || queueJob.recovery_state?.startsWith('blocked') === true
}

function logicalJobKind(job?: GenerationJob, queueJob?: QueueJobState) {
  const publicKind = job?.logicalJobKind
  const queueKind = queueJob?.logical_job_kind
  if (publicKind !== undefined && queueKind !== undefined && publicKind !== queueKind) return undefined
  return queueKind ?? publicKind
}

function hasReferencePackRelation(
  parent: GenerationJob,
  child: GenerationJob,
  parentQueueJob?: QueueJobState,
  childQueueJob?: QueueJobState,
): boolean {
  return logicalJobKind(parent, parentQueueJob) === 'reference_pack_parent'
    && logicalJobKind(child, childQueueJob) === 'reference_pack_child'
}

function isReferenceChild(
  parent: GenerationJob,
  child: GenerationJob,
  parentQueueJob?: QueueJobState,
  childQueueJob?: QueueJobState,
): boolean {
  if (parent.failedChildJobId === child.id) return true
  if (child.status === 'failed') return false
  return hasReferencePackRelation(parent, child, parentQueueJob, childQueueJob)
}

function summarize(
  visibleJobs: readonly GenerationJob[],
  targets: ReadonlyMap<string, LogicalQueueTarget>,
): QueueState['summary'] {
  const summary: QueueState['summary'] = {
    running: 0,
    waiting: 0,
    held: 0,
    registering: 0,
    preparing: 0,
    approval_waiting: 0,
    active_total: 0,
  }
  for (const publicJob of visibleJobs) {
    if (!isActiveLogicalQueueJob(publicJob)) continue
    summary.active_total += 1
    const target = targets.get(publicJob.id)
    const queueJob = target?.queueJob
    const status = queueJob?.status ?? target?.schedulerJob?.status ?? publicJob.status
    if (queueJob?.held || publicJob.held) summary.held += 1
    else if (status === 'running') summary.running += 1
    else if (status === 'preparing') summary.preparing += 1
    else if (status === 'waiting_for_plan_approval') summary.approval_waiting += 1
    else if (queueJob && (queueJob.wait_reason === 'registering' || queueJob.position == null)) {
      summary.registering += 1
    } else summary.waiting += 1
  }
  return summary
}

/**
 * Convert authorized physical job rows into their user-facing logical queue.
 * Correlation uses only exact server-authored kind and parent metadata; display names
 * and prompt content never participate. Internal Reference children stay as the
 * scheduler target while their public parent remains the card/cancel target.
 */
export function projectLogicalQueue(
  jobs: readonly GenerationJob[],
  queueJobs: readonly QueueJobState[] = [],
): LogicalQueueProjection {
  const jobsById = new Map(jobs.map(job => [job.id, job]))
  const queueById = new Map(queueJobs.map(job => [job.job_id, job]))
  const foldedChildIds = new Set<string>()
  const schedulerTargetByPublicJobId = new Map<string, LogicalQueueTarget>()

  for (const job of jobs) {
    schedulerTargetByPublicJobId.set(job.id, {
      publicJob: job,
      schedulerJobId: job.id,
      schedulerJob: job,
      queueJob: queueById.get(job.id),
    })
  }

  // `/jobs` may already suppress an ordinary internal child while `/queue`
  // intentionally retains its authorized physical scheduler row. Join that
  // row directly to the public parent so controls never fall back to a stale
  // or guessed target ID.
  for (const childQueueJob of queueJobs) {
    const parentId = childQueueJob.parent_job_id
    if (!parentId || parentId === childQueueJob.job_id) continue
    const parent = jobsById.get(parentId)
    if (!parent) continue
    const parentQueueJob = queueById.get(parent.id)
    const child = jobsById.get(childQueueJob.job_id)
    if (child?.status === 'failed' && parent.failedChildJobId !== child.id) continue
    const referenceRelation = parent.failedChildJobId === childQueueJob.job_id
      || (logicalJobKind(parent, parentQueueJob) === 'reference_pack_parent'
        && logicalJobKind(child, childQueueJob) === 'reference_pack_child')
    if (!referenceRelation || queueRecoveryNeedsDirectAction(childQueueJob)) continue
    if (child && recoveryNeedsDirectAction(child, childQueueJob)) continue
    schedulerTargetByPublicJobId.set(parent.id, {
      publicJob: parent,
      schedulerJobId: childQueueJob.job_id,
      ...(child ? { schedulerJob: child } : {}),
      queueJob: childQueueJob,
    })
  }

  for (const child of jobs) {
    if (!child.parentJobId || child.parentJobId === child.id) continue
    const parent = jobsById.get(child.parentJobId)
    if (!parent) continue
    const parentQueueJob = queueById.get(parent.id)
    const childQueueJob = queueById.get(child.id)
    if (!isReferenceChild(parent, child, parentQueueJob, childQueueJob)) continue
    if (recoveryNeedsDirectAction(child, childQueueJob)) continue

    foldedChildIds.add(child.id)
    if (childQueueJob) {
      schedulerTargetByPublicJobId.set(parent.id, {
        publicJob: parent,
        schedulerJobId: child.id,
        schedulerJob: child,
        queueJob: childQueueJob,
      })
    }
  }

  const visibleJobs = jobs.filter(job => !foldedChildIds.has(job.id))
  const summary = summarize(visibleJobs, schedulerTargetByPublicJobId)
  return {
    visibleJobs,
    schedulerTargetByPublicJobId,
    summary,
    activeCount: summary.active_total,
  }
}
