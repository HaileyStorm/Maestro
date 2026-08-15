import type {
  ArtifactClass,
  DirectorImageRoleLoraSelection,
  DirectorRecoveryMetadata,
  OutputArtifactScope,
  OutputSearchFilters,
  ProjectReferenceAnchorBasis,
  ProjectReferenceAnchorPrivacy,
  ProjectReferenceAdditionalLora,
  ProjectReferencePrivateAdditionalLora,
  ProjectReferenceAdditionalLoraSummary,
  ProjectReferenceAssetType,
  ProjectReferenceCharacterAnatomy,
  ProjectReferenceCharacterGender,
  ProjectReferenceCharacterProfileInput,
  ProjectReferenceDepth,
  ProjectReferenceDetailCallout,
  ProjectReferenceDetailKind,
  ProjectReferenceDetailOperation,
  ProjectReferenceIntent,
  ProjectReferenceLegacyAssetType,
  ProjectReferenceLegacyAnchorPrivacy,
  ProjectReferenceLoraScope,
  LoraParameterSchema,
  LoraParameterDefinition,
  LoraParameterValue,
  ModelManualInstallation,
  ProjectReferenceManagedLayoutAssistMode,
  ProjectReferencePackPlan,
  ProjectReferencePreset,
  ProjectReferenceOperationRouting,
  ProjectReferenceTypeFields,
  ProjectReferenceTypeFieldItem,
  ScailResolutionProfile,
  LoraInfo,
  LogicalJobKind,
  AccountAuthResult,
  AccountContext,
  AccountProjectMigrationStatus,
  AccountNoncePurpose,
  AccountSession,
  AccountSummary,
  ResponsibleUseProjection,
  ResponsibleUseStatus,
  SupportAccountSummary,
  SupportFulfillmentMutationInput,
  SupportManualContributionInput,
  SupportAdminProjection,
  SupportPublicProjection,
  SupportSelfProjection,
} from '../types'

export type {
  ProjectReferenceAssetType,
  ProjectReferenceCharacterAnatomy,
  ProjectReferenceCharacterGender,
  ProjectReferenceCharacterProfileInput,
  ProjectReferenceAnchorBasis,
  ProjectReferenceAnchorPrivacy,
  ProjectReferenceAdditionalLora,
  ProjectReferenceAdditionalLoraSummary,
  ProjectReferenceDepth,
  ProjectReferenceDetailCallout,
  ProjectReferenceDetailKind,
  ProjectReferenceDetailOperation,
  ProjectReferenceIntent,
  ProjectReferenceLegacyAssetType,
  ProjectReferenceLegacyAnchorPrivacy,
  ProjectReferenceLoraScope,
  ProjectReferenceManagedLayoutAssistMode,
  ProjectReferencePackPlan,
  ProjectReferencePreset,
  ProjectReferenceOperationRouting,
  ProjectReferenceTypeFields,
  ProjectReferenceTypeFieldItem,
} from '../types'

const BASE = ''  // same origin in production; Vite proxy handles /api in dev

export interface ApiModel {
  model_type: string
  name: string
  description?: string
  selector_help?: string
  lora_compatibility_note?: string
  family: string
  architecture: string
  is_i2v: boolean
  is_t2v: boolean
  guidance_max_phases: number
  fps: number
  supports_end_frame?: boolean
  /** Legacy broad flag: accepts input audio OR generates output audio. */
  supports_audio?: boolean
  supports_audio_input?: boolean
  generates_audio?: boolean
  supports_ref_images?: boolean
  image_outputs?: boolean
  director?: import('../types').DirectorModelCompatibility
  is_downloaded?: boolean
  downloadable?: boolean
  manual_installation_ready?: boolean
  availability_status?: string
  manual_checkpoint_verification_required?: boolean
  manual_checkpoint_verified?: boolean
  manual_installation?: ModelManualInstallation
  supported_operations?: string[]
  automatic_routing?: boolean
  verified?: boolean
  default_for_operations?: string[]
  revenue_eligible?: boolean | null
  fine_tuning_eligible?: boolean | null
  derivative_tooling?: boolean | null
  // Upstream catalog metadata; it does not control Maestro visibility.
  nsfw_only?: boolean
  update_status?: string
  required_host_terms?: import('../types').ModelHostTermRequirement[]
}

export interface ApiFamily {
  id: string
  label: string
  order: number
}

export interface ApiResolution {
  label: string
  value: string
}

export interface ApiOutput {
  name: string
  type: 'video' | 'image' | 'audio'
  mode: string | null
  favorite?: boolean
  size: number
  created_at: number
  /** Changes when the media or its sidecar is replaced/written in place. */
  revision: string
  workspace: string
  private: boolean
  explicit: boolean
  url: string
  /** Edit-mode sub-classification (retake, inpaint, outpaint, or restyle). */
  edit_sub_mode?: string | null
  artifact_class: ArtifactClass
  linked_component_count: number
}

export type QueueRecoveryState =
  | 'blocked'
  | 'blocked_preparation'
  | 'blocked_remote_reauth'
  | 'cancelled'
  | 'interrupted'
  | 'restored'
  | 'retrying'
  | 'terminal'

export type QueueRecoveryReason =
  | 'project_missing_or_recreated'
  | 'input_missing_or_changed'
  | 'attempt_limit_reached'
  | 'preparation_must_resubmit'
  | 'worker_start_failed'
  | 'owner_reauthentication_required'

export type QueueRecoveryAction = 'resume' | 'retry'

export type ResourceIntent = 'generation' | 'text'
export type ResourceExecution = 'standard' | 'cpu'
export type ResourcePreemptionMode = 'none' | 'discard_restart'
export type ResourceExecutionState =
  | 'queued'
  | 'admitted'
  | 'running'
  | 'preemption_requested'
  | 'resources_releasing'
  | 'restarting_on_accelerator'
  | 'blocked'
  | 'released'

export interface ResourceDescriptor {
  intent: ResourceIntent
  execution: ResourceExecution
  preemptible: boolean
  preemption_mode: ResourcePreemptionMode
  state: ResourceExecutionState
  execution_attempt: number
}

export interface QueueRecoveryMetadata {
  recovery_state?: QueueRecoveryState | null
  recovery_interrupted?: boolean
  recovery_blocked?: boolean
  recovery_attempt?: number
  recovery_attempt_limit?: number
  recovery_reruns_denoise?: boolean
  recovery_reason?: QueueRecoveryReason | null
  recovery_reason_text?: string | null
  recovery_actionable?: boolean
  recovery_actions?: QueueRecoveryAction[]
  estimate_after_resume?: import('../types').H3PerformanceEstimate | null
}

export interface ApiJobStatus extends QueueRecoveryMetadata {
  job_id: string
  created_at: number
  status: 'preparing' | 'waiting_for_plan_approval' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number
  step: number
  total_steps: number
  phase: string
  message: string
  output_files: string[]
  error: string | null
  error_code?: string | null
  failure_details?: Record<string, unknown> | null
  /** Bounded, owner-safe correlation for a terminal Reference child. */
  failed_child_job_id?: string | null
  failed_child_status?: 'failed' | 'cancelled' | 'blocked' | null
  failed_child_reason?: string | null
  /** Present only on failed jobs that look like CUDA OOMs.
   *  See `OomInfo` in types/index.ts. */
  oom_info?: import('../types').OomInfo | null
  prompt_preview: string
  active_window_prompt: string
  model_type: string
  generation_mode: string
  workspace: string
  window_current: number
  window_total: number
  window_step: number
  window_total_steps: number
  window_progress: number
  overall_progress: number
  progress_indeterminate?: boolean
  queue_priority: number
  queue_held: boolean
  hold_after_output: boolean
  queue_position: number | null
  queue_wait_reason: QueueWaitReason | null
  resource_descriptor?: ResourceDescriptor | null
  parent_job_id?: string | null
  logical_job_kind?: LogicalJobKind
  queue_reorder_reason: QueueReorderReason | null
  queue_residency_bypass_count: number
  queue_residency_bypassed_waiters: number
  requested_outputs: number
  produced_outputs: number
  eta_seconds?: number | null
  subtask_eta_seconds?: number | null
  h3_estimate?: import('../types').H3PerformanceEstimate | null
  queue: { paused: boolean; pause_after_current: boolean }
  h3_segment_plan?: import('../types').H3SegmentPlan | null
  plan_review_required?: boolean
  /** True when the frozen plan cannot auto-accept until Ref2VA terms are accepted. */
  plan_review_terms_required?: boolean
  /** Server-authored absolute Unix epoch seconds; null outside plan review. */
  plan_review_deadline?: number | null
  current_segment_model?: string
  current_segment_reason?: string
  current_segment_boundary?: import('../types').H3SegmentBoundary | null
  events?: JobLogEvent[]
}

export interface QueueJobState extends QueueRecoveryMetadata {
  job_id: string
  status: 'preparing' | 'waiting_for_plan_approval' | 'queued' | 'running'
  priority: number
  held: boolean
  hold_after_output: boolean
  position: number | null
  wait_reason: QueueWaitReason | null
  resource_descriptor?: ResourceDescriptor | null
  parent_job_id?: string | null
  logical_job_kind?: LogicalJobKind
  plan_review_terms_required?: boolean
  /** Server-authored absolute Unix epoch seconds; null outside plan review. */
  plan_review_deadline?: number | null
  queue_reorder_reason: QueueReorderReason | null
  queue_residency_bypass_count: number
  queue_residency_bypassed_waiters: number
  requested_outputs: number
  produced_outputs: number
  eta_seconds?: number | null
  subtask_eta_seconds?: number | null
}

export interface QueueState {
  paused: boolean
  pause_after_current: boolean
  summary: {
    running: number
    waiting: number
    held: number
    registering: number
    preparing: number
    approval_waiting: number
    active_total: number
  }
  jobs: QueueJobState[]
}

export type SampleCampaignQueueState = 'held' | 'running_arm' | 'outputs_unbound' | 'blocked'
export type SampleCampaignArmName = 'maestro' | 'control'
export type SampleCampaignArmStatus = 'queued' | 'running' | 'completed' | 'failed'
export type SampleCampaignRecoveryState =
  | 'sample_campaign_held'
  | 'sample_campaign_released'
  | 'terminal'
  | 'blocked'
  | null
export type SampleCampaignResourceState =
  | 'queued'
  | 'running'
  | 'preemption_requested'
  | 'released'
  | 'blocked'

export interface SampleCampaignPublicPairProjection {
  schema_version: 1
  pair_id: string
  case_id: string
  arms: ['maestro', 'control']
  shared_generation: {
    same_normalized_prompt: true
    same_normalized_inputs: true
    same_model_revision: true
    same_settings: true
    same_seed: true
    same_output_index: true
    model_revision: string
    seed: string
    output_index: number
    input_count: number
  }
  intervention_delta: {
    maestro_only: string[]
    control_only: string[]
  }
  evaluation: {
    evidence_class: 'manifest_only'
    vlm_verdict: 'not_reviewed'
    human_verdict: 'not_reviewed'
  }
}

export interface SampleCampaignQueueArm {
  job_id: string
  arm: SampleCampaignArmName
  status: SampleCampaignArmStatus
  queue_held: boolean
  recovery_state: SampleCampaignRecoveryState
  resource_state: SampleCampaignResourceState
  progress: number
  output_available: boolean
  output_count: number
}

export interface SampleCampaignQueuePair {
  pair: SampleCampaignPublicPairProjection
  queue_state: SampleCampaignQueueState
  arms: [SampleCampaignQueueArm, SampleCampaignQueueArm]
}

export interface SampleCampaignQueueProjection {
  schema_version: 1
  pairs: SampleCampaignQueuePair[]
}

const SAMPLE_CAMPAIGN_MAX_PAIRS = 100
const SAMPLE_CAMPAIGN_MAX_OUTPUTS = 1_000
const SAMPLE_CAMPAIGN_ID = /^[A-Za-z0-9][A-Za-z0-9_.+@~-]{0,255}$/
const SAMPLE_CAMPAIGN_JOB_ID = /^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$/
const SAMPLE_CAMPAIGN_INTERVENTION = /^[A-Za-z0-9][A-Za-z0-9_.:+~-]{0,127}$/
const SAMPLE_CAMPAIGN_UINT64_MAX = '18446744073709551615'

function _sampleCampaignRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function _sampleCampaignHasExactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(key => Object.hasOwn(value, key))
}

function _sampleCampaignInteger(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === 'number'
    && Number.isFinite(value)
    && Number.isInteger(value)
    && value >= minimum
    && value <= maximum
}

function _sampleCampaignUint64(value: unknown): value is string {
  if (typeof value !== 'string' || !/^(?:0|[1-9][0-9]{0,19})$/.test(value)) return false
  return value.length < SAMPLE_CAMPAIGN_UINT64_MAX.length
    || value.length === SAMPLE_CAMPAIGN_UINT64_MAX.length
      && value <= SAMPLE_CAMPAIGN_UINT64_MAX
}

function _sampleCampaignStringList(
  value: unknown,
  pattern: RegExp,
  maximum: number,
): string[] | null {
  if (!Array.isArray(value) || value.length > maximum) return null
  const result: string[] = []
  for (const item of value) {
    if (typeof item !== 'string' || !pattern.test(item)) return null
    result.push(item)
  }
  if (new Set(result).size !== result.length) return null
  if (result.some((item, index) => index > 0 && result[index - 1] >= item)) return null
  return result
}

function _decodeSampleCampaignPair(value: unknown): SampleCampaignPublicPairProjection | null {
  const pair = _sampleCampaignRecord(value)
  if (!pair || !_sampleCampaignHasExactKeys(pair, [
    'schema_version', 'pair_id', 'case_id', 'arms', 'shared_generation',
    'intervention_delta', 'evaluation',
  ])) return null
  if (
    pair.schema_version !== 1
    || typeof pair.pair_id !== 'string'
    || !SAMPLE_CAMPAIGN_ID.test(pair.pair_id)
    || typeof pair.case_id !== 'string'
    || !SAMPLE_CAMPAIGN_ID.test(pair.case_id)
    || !Array.isArray(pair.arms)
    || pair.arms.length !== 2
    || pair.arms[0] !== 'maestro'
    || pair.arms[1] !== 'control'
  ) return null

  const shared = _sampleCampaignRecord(pair.shared_generation)
  if (!shared || !_sampleCampaignHasExactKeys(shared, [
    'same_normalized_prompt', 'same_normalized_inputs', 'same_model_revision',
    'same_settings', 'same_seed', 'same_output_index', 'model_revision',
    'seed', 'output_index', 'input_count',
  ])) return null
  if (
    shared.same_normalized_prompt !== true
    || shared.same_normalized_inputs !== true
    || shared.same_model_revision !== true
    || shared.same_settings !== true
    || shared.same_seed !== true
    || shared.same_output_index !== true
    || typeof shared.model_revision !== 'string'
    || !SAMPLE_CAMPAIGN_ID.test(shared.model_revision)
    || !_sampleCampaignUint64(shared.seed)
    || !_sampleCampaignInteger(shared.output_index, 0, Number.MAX_SAFE_INTEGER)
    || !_sampleCampaignInteger(shared.input_count, 0, 10_000)
  ) return null

  const delta = _sampleCampaignRecord(pair.intervention_delta)
  if (!delta || !_sampleCampaignHasExactKeys(delta, ['maestro_only', 'control_only'])) return null
  const maestroOnly = _sampleCampaignStringList(delta.maestro_only, SAMPLE_CAMPAIGN_INTERVENTION, 100)
  const controlOnly = _sampleCampaignStringList(delta.control_only, SAMPLE_CAMPAIGN_INTERVENTION, 100)
  if (!maestroOnly || !controlOnly || maestroOnly.length + controlOnly.length === 0) return null
  if (maestroOnly.some(item => controlOnly.includes(item))) return null

  const evaluation = _sampleCampaignRecord(pair.evaluation)
  if (!evaluation || !_sampleCampaignHasExactKeys(
    evaluation,
    ['evidence_class', 'vlm_verdict', 'human_verdict'],
  )) return null
  if (
    evaluation.evidence_class !== 'manifest_only'
    || evaluation.vlm_verdict !== 'not_reviewed'
    || evaluation.human_verdict !== 'not_reviewed'
  ) return null

  return {
    schema_version: 1,
    pair_id: pair.pair_id,
    case_id: pair.case_id,
    arms: ['maestro', 'control'],
    shared_generation: {
      same_normalized_prompt: true,
      same_normalized_inputs: true,
      same_model_revision: true,
      same_settings: true,
      same_seed: true,
      same_output_index: true,
      model_revision: shared.model_revision,
      seed: shared.seed,
      output_index: shared.output_index,
      input_count: shared.input_count,
    },
    intervention_delta: {
      maestro_only: maestroOnly,
      control_only: controlOnly,
    },
    evaluation: {
      evidence_class: 'manifest_only',
      vlm_verdict: 'not_reviewed',
      human_verdict: 'not_reviewed',
    },
  }
}

function _decodeSampleCampaignArm(
  value: unknown,
  expectedArm: SampleCampaignArmName,
): SampleCampaignQueueArm | null {
  const arm = _sampleCampaignRecord(value)
  if (!arm || !_sampleCampaignHasExactKeys(arm, [
    'job_id', 'arm', 'status', 'queue_held', 'recovery_state', 'resource_state',
    'progress', 'output_available', 'output_count',
  ])) return null
  const statuses: readonly SampleCampaignArmStatus[] = ['queued', 'running', 'completed', 'failed']
  const recoveryStates: readonly Exclude<SampleCampaignRecoveryState, null>[] = [
    'sample_campaign_held', 'sample_campaign_released', 'terminal', 'blocked',
  ]
  const resourceStates: readonly SampleCampaignResourceState[] = [
    'queued', 'running', 'preemption_requested', 'released', 'blocked',
  ]
  if (
    typeof arm.job_id !== 'string'
    || !SAMPLE_CAMPAIGN_JOB_ID.test(arm.job_id)
    || arm.arm !== expectedArm
    || !statuses.includes(arm.status as SampleCampaignArmStatus)
    || typeof arm.queue_held !== 'boolean'
    || !(arm.recovery_state === null || recoveryStates.includes(arm.recovery_state as Exclude<SampleCampaignRecoveryState, null>))
    || !resourceStates.includes(arm.resource_state as SampleCampaignResourceState)
    || typeof arm.progress !== 'number'
    || !Number.isFinite(arm.progress)
    || arm.progress < 0
    || arm.progress > 100
    || typeof arm.output_available !== 'boolean'
    || !_sampleCampaignInteger(arm.output_count, 0, SAMPLE_CAMPAIGN_MAX_OUTPUTS)
    || arm.output_available !== (arm.output_count > 0)
  ) return null
  const status = arm.status as SampleCampaignArmStatus
  const recoveryState = arm.recovery_state as SampleCampaignRecoveryState
  const resourceState = arm.resource_state as SampleCampaignResourceState
  if (status === 'queued' && (
    resourceState !== 'queued'
    || !(
      arm.queue_held === true
        ? recoveryState === null
          || recoveryState === 'sample_campaign_held'
          || recoveryState === 'sample_campaign_released'
        : recoveryState === 'sample_campaign_released'
    )
  )) return null
  if (status === 'completed' && (
    arm.queue_held || recoveryState !== 'terminal' || resourceState !== 'released' || arm.output_count < 1
  )) return null
  if (status === 'failed' && (
    !['terminal', 'blocked'].includes(recoveryState ?? '')
    || !['released', 'blocked'].includes(resourceState)
  )) return null
  if (status === 'running' && (
    arm.queue_held
    || recoveryState !== 'sample_campaign_released'
    || !['running', 'preemption_requested'].includes(resourceState)
  )) return null

  return {
    job_id: arm.job_id,
    arm: expectedArm,
    status,
    queue_held: arm.queue_held,
    recovery_state: recoveryState,
    resource_state: resourceState,
    progress: arm.progress,
    output_available: arm.output_available,
    output_count: arm.output_count,
  }
}

function _decodeSampleCampaignQueuePair(value: unknown): SampleCampaignQueuePair | null {
  const entry = _sampleCampaignRecord(value)
  if (!entry || !_sampleCampaignHasExactKeys(entry, ['pair', 'queue_state', 'arms'])) return null
  const pair = _decodeSampleCampaignPair(entry.pair)
  const states: readonly SampleCampaignQueueState[] = ['held', 'running_arm', 'outputs_unbound', 'blocked']
  if (!pair || !states.includes(entry.queue_state as SampleCampaignQueueState)) return null
  if (!Array.isArray(entry.arms) || entry.arms.length !== 2) return null
  const maestro = _decodeSampleCampaignArm(entry.arms[0], 'maestro')
  const control = _decodeSampleCampaignArm(entry.arms[1], 'control')
  if (!maestro || !control || maestro.job_id === control.job_id) return null
  const queueState = entry.queue_state as SampleCampaignQueueState
  const expectedQueueState: SampleCampaignQueueState = [maestro, control].some(arm => arm.status === 'failed')
    ? 'blocked'
    : maestro.status === 'completed' && control.status === 'completed'
      ? 'outputs_unbound'
      : maestro.status === 'queued' && control.status === 'queued' && maestro.queue_held
        ? 'held'
        : maestro.status === 'completed' && control.status === 'queued' && control.queue_held
          ? 'held'
          : 'running_arm'
  if (queueState !== expectedQueueState) return null

  return { pair, queue_state: queueState, arms: [maestro, control] }
}

export function decodeSampleCampaignQueue(value: unknown): SampleCampaignQueueProjection {
  const root = _sampleCampaignRecord(value)
  if (
    !root
    || !_sampleCampaignHasExactKeys(root, ['schema_version', 'pairs'])
    || root.schema_version !== 1
    || !Array.isArray(root.pairs)
    || root.pairs.length > SAMPLE_CAMPAIGN_MAX_PAIRS
  ) throw new Error('Sample campaign queue response is invalid')

  const pairs: SampleCampaignQueuePair[] = []
  const pairIds = new Set<string>()
  const jobIds = new Set<string>()
  let previousSortKey = ''
  for (const value of root.pairs) {
    const decoded = _decodeSampleCampaignQueuePair(value)
    if (!decoded) throw new Error('Sample campaign queue response is invalid')
    const sortKey = `${decoded.pair.case_id}\u0000${decoded.pair.pair_id}`
    if (
      pairIds.has(decoded.pair.pair_id)
      || (previousSortKey && previousSortKey >= sortKey)
      || decoded.arms.some(arm => jobIds.has(arm.job_id))
    ) throw new Error('Sample campaign queue response is invalid')
    pairIds.add(decoded.pair.pair_id)
    decoded.arms.forEach(arm => jobIds.add(arm.job_id))
    previousSortKey = sortKey
    pairs.push(decoded)
  }
  return { schema_version: 1, pairs }
}

export async function fetchSampleCampaignQueue(
  signal?: AbortSignal,
): Promise<SampleCampaignQueueProjection | null> {
  const res = await fetch(`${BASE}/api/v1/sample-campaign/queue`, {
    signal,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  })
  if (res.status === 403 || res.status === 404) return null
  if (!res.ok) throw new Error('Sample campaign queue is unavailable')
  return decodeSampleCampaignQueue(await res.json())
}

export type QueueWaitReason =
  | 'running'
  | 'held'
  | 'queue_paused'
  | 'registering'
  | 'preparing'
  | 'waiting_for_plan_approval'
  | 'waiting_for_plan_terms'
  | 'waiting_for_turn'
  | 'waiting_for_active_generation'
  | 'waiting_for_other_user'
  | 'resource_wait'
  | 'ready'

export type QueueReorderReason =
  | 'queue_order'
  | 'resident_base'
  | 'resident_affinity'
  | 'starvation_guard'

export async function fetchQueueState(signal?: AbortSignal): Promise<QueueState> {
  const res = await fetch(`${BASE}/api/v1/queue`, { signal })
  if (!res.ok) throw new Error('Failed to load queue')
  return res.json()
}

export async function setQueuePriority(jobId: string, priority: number) {
  const res = await fetch(`${BASE}/api/v1/queue/${encodeURIComponent(jobId)}/priority`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ priority }),
  })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Reprioritize failed')
  return res.json()
}

export async function holdQueueJob(jobId: string) {
  const res = await fetch(`${BASE}/api/v1/queue/${encodeURIComponent(jobId)}/hold`, { method: 'POST' })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Hold failed')
  return res.json()
}

export async function resumeQueueJob(jobId: string) {
  const res = await fetch(`${BASE}/api/v1/queue/${encodeURIComponent(jobId)}/resume`, { method: 'POST' })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Resume failed')
  return res.json()
}

export async function startQueueJobNext(jobId: string) {
  const res = await fetch(`${BASE}/api/v1/queue/${encodeURIComponent(jobId)}/start-next`, { method: 'POST' })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Start next failed')
  return res.json()
}

export async function setQueueOutputCount(jobId: string, count: number): Promise<{
  job_id: string
  requested_outputs: number
  produced_outputs: number
}> {
  const res = await fetch(`${BASE}/api/v1/queue/${encodeURIComponent(jobId)}/output-count`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ count }),
  })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Output-count update failed')
  return res.json()
}

export interface QueueRecoveryResult {
  job_id: string
  status: 'queued'
  recovery_attempt: number
  recovery_state: 'retrying'
  reruns_denoise: boolean
}

async function queueRecoveryRequest(
  jobId: string,
  action: QueueRecoveryAction,
): Promise<QueueRecoveryResult> {
  const endpoint = action === 'resume' ? 'recovery-resume' : 'recovery-retry'
  const res = await fetch(
    `${BASE}/api/v1/queue/${encodeURIComponent(jobId)}/${endpoint}`,
    { method: 'POST', cache: 'no-store' },
  )
  if (!res.ok) {
    const payload = await res.json().catch(() => ({})) as { detail?: unknown }
    const detail = typeof payload.detail === 'string' ? payload.detail : ''
    if (res.status === 404) throw new Error('That recovery is no longer available.')
    if (res.status === 409) throw new Error(detail || 'Recovery state changed. Refresh and try again.')
    if (res.status === 503) throw new Error(detail || 'The recovery worker could not be started. Try again.')
    throw new Error(detail || 'Recovery could not be started.')
  }
  return res.json()
}

export const resumeQueueRecovery = (jobId: string) => queueRecoveryRequest(jobId, 'resume')
export const retryQueueRecovery = (jobId: string) => queueRecoveryRequest(jobId, 'retry')

export interface JobLogEvent {
  at: number
  status: string
  message: string
  phase: string
  progress: number
  step: number
  total_steps: number
}

export function isBackendJobId(jobId: string): boolean {
  return /^[0-9a-f]{8}$/i.test(jobId)
}

export async function fetchJobLog(jobId: string, limit = 100): Promise<{ job_id: string; events: JobLogEvent[] }> {
  if (!isBackendJobId(jobId)) {
    throw new Error('This failure happened before Maestro created a server job. Use the technical details on the card.')
  }
  const query = new URLSearchParams({ limit: String(Math.max(1, Math.min(250, limit))) })
  const res = await fetch(`${BASE}/api/v1/jobs/${encodeURIComponent(jobId)}/log?${query}`)
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Job log unavailable')
  return res.json()
}

export async function pauseQueueAfterOutput(enabled = true) {
  const res = await fetch(`${BASE}/api/v1/queue/pause-after-output`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error('Queue pause request failed')
  return res.json()
}

export async function resumeQueue() {
  const res = await fetch(`${BASE}/api/v1/queue/resume`, { method: 'POST' })
  if (!res.ok) throw new Error('Queue resume failed')
  return res.json()
}

// --- Models & Families ---

export async function fetchModels(): Promise<{ families: ApiFamily[]; models: ApiModel[] }> {
  const res = await fetch(`${BASE}/api/v1/models`)
  if (!res.ok) throw new Error('Failed to fetch models')
  return res.json()
}

export interface ModelVisibilitySettings {
  configured: boolean
  enabled_models: string[]
  defaults_version: number
}

export async function fetchModelVisibility(): Promise<ModelVisibilitySettings> {
  const res = await fetch(`${BASE}/api/v1/model-visibility`)
  if (!res.ok) throw new Error('Failed to fetch model visibility')
  return res.json()
}

export async function updateModelVisibility(params: {
  enabled_models: string[]
  defaults_version: number
}): Promise<ModelVisibilitySettings> {
  const res = await fetch(`${BASE}/api/v1/model-visibility`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) throw new Error('Failed to save model visibility')
  return res.json()
}

// Re-scan defaults/ + finetunes/ on the server so a newly-imported checkpoint
// appears in the model list without a restart. Returns model_types that appeared.
export async function reloadModels(): Promise<{ status: string; model_count: number; added: string[] }> {
  const res = await fetch(`${BASE}/api/v1/models/reload`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to reload models')
  return res.json()
}

export async function deleteModel(modelType: string): Promise<{ deleted: string[]; model_type: string }> {
  const res = await fetch(`${BASE}/api/v1/models/${encodeURIComponent(modelType)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete model')
  return res.json()
}

export type ModelDownloadStatus = 'downloading' | 'completed' | 'failed'

export async function downloadModel(modelType: string, workspace: string): Promise<{ status: ModelDownloadStatus; model_type: string }> {
  const query = new URLSearchParams({ workspace })
  const res = await fetch(`${BASE}/api/v1/models/${encodeURIComponent(modelType)}/download?${query}`, { method: 'POST' })
  if (!res.ok) {
    const message = res.status === 409
      ? 'Review this model recipe\'s terms and supported installation method.'
      : res.status === 423
        ? 'Unlock the selected project before downloading models.'
        : res.status === 403
          ? 'This project is not authorized to download models.'
          : res.status === 404
            ? 'This model is unavailable.'
            : 'Failed to start model download.'
    throw new Error(message)
  }
  return res.json()
}

export async function verifyManualCheckpoint(modelType: string): Promise<{
  status: 'verified'
  model_type: string
  manual_checkpoint_verified: true
  is_downloaded: boolean
}> {
  const res = await fetch(
    `${BASE}/api/v1/models/${encodeURIComponent(modelType)}/verify-manual-checkpoint`,
    { method: 'POST' },
  )
  if (!res.ok) {
    const message = res.status === 403
      ? 'Manual checkpoint verification is available only on the local host.'
      : res.status === 404
        ? 'This model is unavailable.'
        : 'Verification failed. Confirm the exact local filename, byte size, and SHA-256.'
    throw new Error(message)
  }
  return res.json()
}

export async function fetchModelDownloads(): Promise<{ downloads: Record<string, { status: ModelDownloadStatus; error: string | null }> }> {
  const res = await fetch(`${BASE}/api/v1/models/downloads/status`)
  if (!res.ok) throw new Error('Failed to fetch model download status')
  return res.json()
}

export async function waitForModelDownloadTerminal(
  modelType: string,
  options: {
    isCurrent: () => boolean
    pollIntervalMs?: number
    wait?: (milliseconds: number) => Promise<void>
    onStatus?: (status: ModelDownloadStatus) => void
  },
): Promise<{ status: 'completed' | 'failed' | 'cancelled'; error: string | null }> {
  const wait = options.wait ?? (milliseconds => new Promise(resolve => {
    globalThis.setTimeout(resolve, milliseconds)
  }))
  while (options.isCurrent()) {
    let snapshot: Awaited<ReturnType<typeof fetchModelDownloads>>
    try {
      snapshot = await fetchModelDownloads()
    } catch {
      if (!options.isCurrent()) return { status: 'cancelled', error: null }
      await wait(options.pollIntervalMs ?? 1000)
      continue
    }
    if (!options.isCurrent()) return { status: 'cancelled', error: null }
    const current = snapshot.downloads[modelType]
    if (current) {
      options.onStatus?.(current.status)
      if (current.status === 'completed' || current.status === 'failed') {
        return { status: current.status, error: current.error }
      }
    }
    await wait(options.pollIntervalMs ?? 1000)
  }
  return { status: 'cancelled', error: null }
}

// --- Resolutions ---

export async function fetchResolutions(): Promise<ApiResolution[]> {
  const res = await fetch(`${BASE}/api/v1/resolutions`)
  if (!res.ok) throw new Error('Failed to fetch resolutions')
  const data = await res.json()
  return data.resolutions
}

// --- Model Defaults ---

export async function fetchDefaults(modelType: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE}/api/v1/defaults/${encodeURIComponent(modelType)}`)
  if (!res.ok) throw new Error(`Failed to fetch defaults for ${modelType}`)
  return res.json()
}

// --- Generation ---

export async function submitGeneration(params: Record<string, unknown>): Promise<{
  job_id: string
  status?: 'preparing' | 'queued'
  h3_estimate?: import('../types').H3PerformanceEstimate | null
}> {
  const res = await fetch(`${BASE}/api/v1/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Generation failed' }))
    throw new Error(err.detail || 'Generation failed')
  }
  return res.json()
}

export interface GenerationPlanApprovalRequest {
  workspace: string
  segment_overrides: NonNullable<import('../types').GenerateParams['h3_segment_overrides']>
  boundary_overrides: NonNullable<import('../types').GenerateParams['h3_boundary_overrides']>
  h3_ref2va_terms_accepted?: boolean
  plan_revision?: string
  duration_snap_mode?: 'manual' | 'nearest' | 'down'
  segment_duration_edits?: Array<{ segment_index: number; published_frames: number }>
  duration_redistribution?: 'none' | 'next' | 'future'
}

export async function approveGenerationPlan(
  jobId: string,
  params: GenerationPlanApprovalRequest,
): Promise<{
  job_id: string
  status: 'queued'
  h3_segment_plan: import('../types').H3SegmentPlan
  h3_estimate: import('../types').H3PerformanceEstimate | null
}> {
  const res = await fetch(`${BASE}/api/v1/generate/${encodeURIComponent(jobId)}/plan/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    if (res.status === 404) throw new Error('That plan review is no longer available.')
    if (res.status === 409) throw new Error('The plan review state changed. Refresh and try again.')
    if (res.status === 423) throw new Error('Unlock the plan\'s project and try again.')
    throw new Error('The generation plan could not be approved.')
  }
  return res.json()
}

export async function previewGenerationPlan(params: Record<string, unknown>): Promise<{
  requires_review: boolean
  plan: import('../types').H3SegmentPlan | null
  effective_model_type: string
  requirements: import('../types').H3GenerationRequirements
  h3_estimate: import('../types').H3PerformanceEstimate | null
  segment_count_estimate: import('../types').H3SegmentCountEstimate | null
}> {
  const res = await fetch(`${BASE}/api/v1/generate/plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Could not build generation plan' }))
    throw new Error(err.detail || 'Could not build generation plan')
  }
  return res.json()
}

export interface H3EvaluationProfile {
  id: string
  label: string
  component_role: string
  model_type?: string | null
  experimental: boolean
  enabled_by_default: boolean
  revision?: string
  notes?: string[]
}

export async function fetchH3EvaluationCatalog(): Promise<{
  pinned_as_of: string
  profiles: Record<string, H3EvaluationProfile>
}> {
  const res = await fetch(`${BASE}/api/v1/h3/evaluation/catalog`)
  if (!res.ok) throw new Error('Failed to load H3 evaluation profiles')
  return res.json()
}

export interface H3StyleWorkflowProvenance {
  workflow_identity_source: 'official_minimax_h3_skill'
  workflow_source: string
  prompt_brief_provenance: 'maestro_adapted'
  surface: 'huggingface_hub_canvas'
  supported_prompt_schemas: string[]
  supported_h3_modes: string[]
  supported_model_types: string[]
}

export interface H3StyleWorkflowCatalogStyle {
  id: string
  label: string
  description: string
  /** Display-only server metadata. Never submit this client-authored. */
  prompt_brief: string
  workflow_identity_source: 'official_minimax_h3_skill'
  workflow_source: string
  prompt_brief_provenance: 'maestro_adapted'
  surface: 'huggingface_hub_canvas'
  supported_prompt_schemas: string[]
  supported_h3_modes: string[]
}

export interface H3StyleWorkflowCatalog {
  source: string
  revision: string
  source_revision: string
  checked_at: number | null
  update_status: 'updated' | 'cached' | 'bundled_fallback' | 'offline_fallback'
  update_error?: string
  supported_model_types: string[]
  provenance: H3StyleWorkflowProvenance
  styles: H3StyleWorkflowCatalogStyle[]
}

export async function fetchH3StyleWorkflows(): Promise<H3StyleWorkflowCatalog> {
  const res = await fetch(`${BASE}/api/v1/h3/style-workflows`)
  if (!res.ok) throw new Error('H3 style catalog is unavailable')
  return res.json()
}

export interface H3AccelerationStatus {
  dense_sdpa: { available: boolean; default: boolean; quality: string }
  sol_attn: {
    available: boolean
    default: boolean
    approximate: boolean
    repository: string
    required_revision: string
    installed_revision: string | null
    hardware_ok: boolean
    error: string | null
  }
  sage2: {
    available: boolean
    default: false
    approximate: true
    validated: boolean
    repository: string
    version: string
    required_revision: string
    installed_revision: string | null
    hardware_ok: boolean
    reason: string
    validation_reason: string | null
    validation_record_sha256: string | null
    validated_profiles: string[]
    validated_model_types: string[]
    last_unavailable_reason: string | null
    model_status: Record<string, string>
    turbo_status: string
  }
  w4a8: {
    available: boolean
    default: boolean
    experimental: boolean
    repository: string
    revision: string
    runtime_revision: string
    compatible_models: string[]
    conditioning_mode: string
    reason: string
  }
  stats: Record<string, number>
}

export async function fetchH3AccelerationStatus(probe = false): Promise<H3AccelerationStatus> {
  const res = await fetch(`${BASE}/api/v1/h3/acceleration?probe=${probe ? 'true' : 'false'}`)
  if (!res.ok) throw new Error('Failed to inspect H3 acceleration support')
  return res.json()
}

export interface H3BenchmarkRecord {
  cache_key: string
  measured_local: true
  measured_at_unix: number
  generation_wall_time_seconds: number
  effective_output_fps: number
  peak_gpu_memory_bytes: number | null
  normalized_speed_index: number | null
  reference_overhead_percent: number | null
  spec: {
    case_id: 'text_only' | 'first_frame' | 'first_last' | 'ref2va'
    model: { id: string }
    engine: { id: string; effective_id?: string }
    encoder: { id: string }
    task: { profile: string; width: number; height: number; frame_count: number; sampling_steps: number }
  }
}

export interface H3BenchmarkReport {
  quick_task: { width: number; height: number; frame_count: number; fps: number; sampling_steps: number }
  normalization: string
  records: H3BenchmarkRecord[]
  published_external: Array<{
    source: string
    url: string
    reported_speedup: string
    comparable_to_maestro_quick_task: false
  }>
}

export async function fetchH3BenchmarkReport(): Promise<H3BenchmarkReport> {
  const res = await fetch(`${BASE}/api/v1/h3/benchmark`)
  if (!res.ok) throw new Error('Failed to load local H3 benchmark results')
  return res.json()
}

export async function fetchJobStatus(jobId: string): Promise<ApiJobStatus> {
  const res = await fetch(`${BASE}/api/v1/status/${encodeURIComponent(jobId)}`)
  if (!res.ok) throw new Error('Failed to fetch job status')
  return res.json()
}

export interface H3DeliveryRecoveryAction {
  action: string
  capability: string
  method: string
  endpoint: string
}

export interface H3DeliveryRecoveryState {
  source_job_id: string
  recoverable: boolean
  stage?: 'h3_delivery'
  requested_target?: string
  native_available?: boolean
  manual_retry_count?: number
  manual_retry_limit?: number
  active_recovery_job_id?: string | null
  completed_recovery_job_id?: string | null
  restart_supported?: boolean
  unsupported_after_restart_reason?: string | null
  actions: H3DeliveryRecoveryAction[]
}

export interface H3DeliveryRecoveryJob {
  source_job_id: string
  job_id: string
  status: 'queued'
  action: string
  reruns_denoise: false
  mutates_machine_settings: false
}

export async function fetchH3DeliveryRecovery(
  jobId: string,
  workspace: string,
): Promise<H3DeliveryRecoveryState | null> {
  const query = new URLSearchParams({ workspace })
  const res = await fetch(
    `${BASE}/api/v1/jobs/${encodeURIComponent(jobId)}/delivery-recovery?${query}`,
    { cache: 'no-store' },
  )
  if (res.status === 404) return null
  if (!res.ok) throw new Error('Delivery recovery status is unavailable')
  return res.json()
}

export async function scheduleH3DeliveryRecovery(
  jobId: string,
  action: 'accept_native' | 'retry_delivery',
  workspace: string,
  capability: string,
): Promise<H3DeliveryRecoveryJob> {
  const suffix = action === 'accept_native' ? 'accept-native' : 'retry'
  const res = await fetch(
    `${BASE}/api/v1/jobs/${encodeURIComponent(jobId)}/delivery-recovery/${suffix}`,
    {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace, capability }),
    },
  )
  if (!res.ok) {
    throw new Error(
      res.status === 404
        ? 'That recovery option changed. Refreshed options are shown below.'
        : 'Recovery could not be queued. Try again.',
    )
  }
  return res.json()
}

// --- Music: LLM song writer (Music mode Simple) ---

export async function writeSong(params: {
  workspace: string
  description: string
  instrumental?: boolean
  seed?: number
  reference_image_path?: string
}, options?: LlmRequestOptions): Promise<{ style: string; lyrics: string; raw: string }> {
  return withLlmPreparation(
    { workspace: params.workspace, purpose: 'configured' },
    options,
    async () => {
      const res = await fetch(`${BASE}/api/v1/llm/write-song`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
        signal: options?.signal,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Song writing failed' }))
        throw new Error(err.detail || 'Song writing failed')
      }
      return res.json()
    },
  )
}

// Director Music Video: generate a music track (writes the song first if only
// a description is given) and return the authorized audio reference so it can
// flow straight into the existing analyze → plan-structure → pipeline chain.
export interface DirectorMusicRequest {
  description?: string
  style?: string
  lyrics?: string
  instrumental?: boolean
  duration_seconds?: number
  reference_image_path?: string
  model_type?: string
  seed?: number
  workspace?: string
  private_output?: boolean
  explicit_output?: boolean
}

export interface DirectorPreparationStatus {
  director_request_id: string
  status: string
  phase: string
  interrupted?: boolean
  next_action: 'generate_music' | 'analyze_audio' | 'classify_or_structure' | null
  actions: Array<'generate_music' | 'analyze_audio' | 'classify_or_structure'>
}

export async function startDirectorPreparation(
  params: DirectorMusicRequest,
): Promise<DirectorPreparationStatus> {
  const res = await fetch(`${BASE}/api/v1/director/preparation`, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Director preparation failed' }))
    throw new Error(err.detail || 'Director preparation failed')
  }
  return res.json()
}

export async function fetchDirectorPreparation(
  requestId: string,
  workspace: string,
): Promise<DirectorPreparationStatus> {
  const query = new URLSearchParams({ workspace })
  const res = await fetch(
    `${BASE}/api/v1/director/preparation/${encodeURIComponent(requestId)}?${query}`,
    { cache: 'no-store' },
  )
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Director preparation is unavailable' }))
    throw new Error(err.detail || 'Director preparation is unavailable')
  }
  return res.json()
}

export async function generateMusic(
  params: DirectorMusicRequest & { director_request_id: string },
): Promise<{ director_request_id: string; audio_path: string; filename: string; style: string; lyrics: string }> {
  const res = await fetch(`${BASE}/api/v1/director/generate-music`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Music generation failed' }))
    throw new Error(err.detail || 'Music generation failed')
  }
  return res.json()
}

// --- Tools: standalone post-processing on an existing clip ---

export async function submitToolUpscale(params: {
  video_path: string
  method?: string
  seed?: number
  workspace?: string
}): Promise<{ job_id: string }> {
  const res = await fetch(`${BASE}/api/v1/tools/upscale`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Upscale failed' }))
    throw new Error(err.detail || 'Upscale failed')
  }
  return res.json()
}

export async function submitToolRevoice(params: {
  video_path: string
  voice_ref_paths: string[]
  mode?: 'single' | 'two'
  diffusion_steps?: number
  cfg_rate?: number
  workspace?: string
}): Promise<{ job_id: string }> {
  const res = await fetch(`${BASE}/api/v1/tools/revoice`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Revoice failed' }))
    throw new Error(err.detail || 'Revoice failed')
  }
  return res.json()
}

// --- Workspaces ---

export interface AccessContext {
  remote: boolean
  /** Explicit server-authored membership cutover state. Missing means fail closed. */
  account_project_access_active?: boolean
  /** New projects require a signed-in owner while cutover is active or recovering. */
  account_project_creation_requires_account?: boolean
  project_password_required: boolean
  project_names_visible: boolean
  machine_controls: boolean
  custom_model_sources: boolean
  catalog_model_downloads: boolean
  classic_ui: boolean
  cloudflare_enabled: boolean
  share_url: string
  share_flow: string
  /** Optional for compatibility with hosts predating the account layer. */
  accounts?: AccountContext
}

/**
 * Whether project membership, rather than the legacy browser password grant,
 * is the server-authored access boundary. An explicit migration projection is
 * authoritative when available; other surfaces use the explicit access-context
 * cutover flag so authenticated members do not have to fetch the owner-only
 * migration API. Older hosts without that flag fail closed into legacy access.
 */
export function isAccountProjectAccessActive(
  context: AccessContext | null,
  migration: AccountProjectMigrationStatus | null = null,
): boolean {
  if (context?.accounts?.enabled !== true) return false
  if (migration !== null) return migration.state === 'active' && migration.enforced === true
  return context.account_project_access_active === true
}

export function getDirectorHostActionAccessState(
  context: AccessContext | null,
): 'loading' | 'local' | 'lan' {
  if (context === null) return 'loading'
  return context.machine_controls ? 'local' : 'lan'
}

export function isDirectLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.trim().toLowerCase().replace(/^\[|\]$/g, '')
  return normalized === 'localhost'
    || normalized === '::1'
    || /^127(?:\.\d{1,3}){3}$/.test(normalized)
}

let accessContextRequest: Promise<AccessContext> | null = null

export async function fetchAccessContext(): Promise<AccessContext> {
  // React StrictMode and independent capability refreshes may ask for this at
  // the same time. Share one in-flight response so the server creates exactly
  // one initial session cookie; later calls remain fresh for share-URL updates.
  if (accessContextRequest) return accessContextRequest
  const pending = (async () => {
    const res = await fetch(`${BASE}/api/v1/access-context`, {
      credentials: 'same-origin',
    })
    if (!res.ok) throw new Error('Failed to determine access capabilities')
    return res.json() as Promise<AccessContext>
  })()
  accessContextRequest = pending
  try {
    return await pending
  } finally {
    if (accessContextRequest === pending) accessContextRequest = null
  }
}

// --- Optional account authority ---

export class AccountApiError extends Error {
  readonly code: string
  readonly status: number
  readonly retryAfter: number

  constructor(message: string, { code = 'account_request_failed', status = 0, retryAfter = 0 } = {}) {
    super(message)
    this.name = 'AccountApiError'
    this.code = code
    this.status = status
    this.retryAfter = retryAfter
  }
}

async function accountRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    credentials: 'same-origin',
    cache: 'no-store',
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as {
      detail?: string | { code?: unknown; message?: unknown }
    }
    const detail = payload.detail
    const message = typeof detail === 'string'
      ? detail
      : typeof detail?.message === 'string'
        ? detail.message
        : 'The account request could not be completed.'
    const code = typeof detail === 'object' && typeof detail?.code === 'string'
      ? detail.code
      : 'account_request_failed'
    const retryAfter = Number.parseInt(response.headers.get('Retry-After') || '0', 10)
    throw new AccountApiError(message, {
      code,
      status: response.status,
      retryAfter: Number.isFinite(retryAfter) ? retryAfter : 0,
    })
  }
  return response.json() as Promise<T>
}

export async function fetchAccountContext(): Promise<AccountContext> {
  return accountRequest<AccountContext>('/api/v1/account/context')
}

export async function fetchAccountProjectMigration(): Promise<AccountProjectMigrationStatus> {
  return accountRequest<AccountProjectMigrationStatus>('/api/v1/account/projects/migration')
}

export async function migrateAccountProjects(): Promise<AccountProjectMigrationStatus> {
  return accountRequest<AccountProjectMigrationStatus>('/api/v1/account/projects/migration', {
    method: 'POST',
  })
}

export async function issueAccountNonce(purpose: AccountNoncePurpose): Promise<string> {
  const result = await accountRequest<{ nonce: string; purpose: AccountNoncePurpose; expires_in: number }>(
    '/api/v1/account/nonce',
    { method: 'POST', body: JSON.stringify({ purpose }) },
  )
  return result.nonce
}

async function accountNonceMutation<T>(
  purpose: AccountNoncePurpose,
  path: string,
  body: Record<string, unknown>,
  method: 'POST' | 'PUT' | 'DELETE' = 'POST',
): Promise<T> {
  const nonce = await issueAccountNonce(purpose)
  return accountRequest<T>(path, {
    method,
    body: JSON.stringify({ ...body, nonce }),
  })
}

export async function bootstrapAccount(input: {
  username: string
  password: string
  email?: string
  deviceLabel?: string
}): Promise<AccountAuthResult> {
  return accountNonceMutation<AccountAuthResult>('bootstrap', '/api/v1/account/bootstrap', {
    username: input.username,
    password: input.password,
    email: input.email || '',
    device_label: input.deviceLabel || 'Browser',
  })
}

export async function loginAccount(input: {
  username: string
  password: string
  deviceLabel?: string
}): Promise<AccountAuthResult> {
  return accountNonceMutation<AccountAuthResult>('login', '/api/v1/account/login', {
    username: input.username,
    password: input.password,
    device_label: input.deviceLabel || 'Browser',
  })
}

export async function logoutAccount(): Promise<{ status: 'logged_out' }> {
  return accountNonceMutation('revoke_session', '/api/v1/account/logout', {})
}

export async function reauthenticateAccount(password: string): Promise<{
  account: AccountSummary
  reauthenticated_until: number
}> {
  return accountNonceMutation('reauth', '/api/v1/account/reauth', { password })
}

export async function recoverAccount(input: {
  username: string
  recoveryCode: string
  newPassword: string
  deviceLabel?: string
}): Promise<AccountAuthResult> {
  return accountNonceMutation<AccountAuthResult>('recover', '/api/v1/account/recover', {
    username: input.username,
    recovery_code: input.recoveryCode,
    new_password: input.newPassword,
    device_label: input.deviceLabel || 'Browser',
  })
}

export async function changeAccountPassword(newPassword: string): Promise<{
  status: 'password_changed'
  other_sessions_revoked: true
}> {
  return accountNonceMutation(
    'change_password', '/api/v1/account/password', { new_password: newPassword }, 'PUT',
  )
}

export async function rotateAccountRecoveryCodes(): Promise<{ recovery_codes: string[] }> {
  return accountNonceMutation('rotate_recovery_codes', '/api/v1/account/recovery-codes', {})
}

export async function fetchAccountSessions(): Promise<{ sessions: AccountSession[] }> {
  return accountRequest('/api/v1/account/sessions')
}

export async function revokeAccountSession(sessionHandle: string): Promise<{
  revoked: true
  current: boolean
}> {
  return accountNonceMutation(
    'revoke_session',
    `/api/v1/account/sessions/${encodeURIComponent(sessionHandle)}`,
    {},
    'DELETE',
  )
}

export async function revokeAllAccountSessions(retainCurrent: boolean): Promise<{
  revoked: number
  current_revoked: boolean
}> {
  return accountNonceMutation(
    'revoke_all_sessions',
    '/api/v1/account/sessions/revoke-all',
    { retain_current: retainCurrent },
  )
}

export async function fetchServerAccounts(): Promise<{ accounts: AccountSummary[] }> {
  return accountRequest('/api/v1/account/users')
}

export async function createServerAccount(input: {
  username: string
  password: string
  email?: string
}): Promise<AccountAuthResult> {
  return accountNonceMutation<AccountAuthResult>('create_account', '/api/v1/account/users', {
    username: input.username,
    password: input.password,
    email: input.email || '',
    role: 'user',
  })
}

export async function setServerAccountDisabled(accountId: string, disabled: boolean): Promise<{
  status: 'updated'
}> {
  return accountNonceMutation(
    'disable_account',
    `/api/v1/account/users/${encodeURIComponent(accountId)}`,
    { disabled },
    'PUT',
  )
}

// --- Provider-neutral Support ---

interface RawSupportAccountProjection {
  recorded?: Record<string, unknown>
  benefits?: Record<string, unknown>
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

const SUPPORT_ALLOWANCE_SOURCES = new Set(['free', 'one_time_support', 'recurring_support'])
const SUPPORT_ALLOWANCE_STATUSES = new Set(['active', 'inactive', 'refunded', 'expired', 'capped', 'canceled'])
const SUPPORT_ALLOWANCE_REFUND_STATES = new Set(['not_applicable', 'none', 'partial', 'full', 'excess'])
const SUPPORT_ADMIN_EVENT_KINDS = new Set([
  'one_time_contribution',
  'recurring_started',
  'recurring_renewed',
  'refund',
  'chargeback',
  'recurring_canceled',
  'fulfillment_set',
  'account_link_verified',
  'account_link_revoked',
])
const SUPPORT_FUNDING_EVENT_KINDS = new Set(['one_time_contribution', 'recurring_started', 'recurring_renewed'])
const SUPPORT_ADJUSTMENT_EVENT_KINDS = new Set(['refund', 'chargeback'])
const SUPPORT_RECURRING_EVENT_KINDS = new Set(['recurring_started', 'recurring_renewed', 'recurring_canceled'])
const SUPPORT_ACCOUNT_LINK_EVENT_KINDS = new Set(['account_link_verified', 'account_link_revoked'])
const SUPPORT_MANUAL_PROVIDERS = new Set([
  'manual_buy_me_a_coffee',
  'manual_patreon',
  'manual_direct_compute_sponsorship',
])
const SUPPORT_FULFILLMENT_STATUSES = new Set([
  'pending',
  'in_progress',
  'fulfilled',
  'declined',
  'reversed',
  'complete',
])
const SUPPORT_DISCREPANCY_REASONS = new Set([
  'unresolved_or_mismatched_adjustment',
  'adjustments_exceed_contribution',
])

function safeAllowanceNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null
}

function safeAllowanceTimestamp(value: unknown): string | null {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(value)) return null
  return Number.isNaN(Date.parse(value)) ? null : value
}

function safeOpaqueSupportReference(value: unknown): string | null {
  return typeof value === 'string' && /^key_[0-9a-f]{64}$/.test(value) ? value : null
}

function safeSupportEventId(value: unknown): string | null {
  return typeof value === 'string' && /^evt_[0-9a-f]{32}$/.test(value) ? value : null
}

function safeSupportAuditTimestamp(value: unknown): string | null {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) return null
  const parsed = new Date(value)
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().replace('.000Z', 'Z') === value
    ? value
    : null
}

function supportFulfillmentStatus(value: unknown): SupportAdminProjection['audit']['fulfillment'][number]['status'] | undefined {
  if (typeof value !== 'string' || !SUPPORT_FULFILLMENT_STATUSES.has(value)) return undefined
  return value === 'complete' ? 'fulfilled' : value as SupportAdminProjection['audit']['fulfillment'][number]['status']
}

function supportAdminEventContractIsValid(input: {
  provider: string
  kind: string
  amount: number
  contractReference: string | null
  relatedReference: string | null
  fulfillmentItem: string | null
  fulfillmentStatus: string | null
  actorReference: string | null
}): boolean {
  const hasFulfillmentFields = input.fulfillmentItem !== null
    || input.fulfillmentStatus !== null
  const actorMatchesSource = SUPPORT_MANUAL_PROVIDERS.has(input.provider)
    ? input.actorReference !== null
    : input.actorReference === null
  if (SUPPORT_FUNDING_EVENT_KINDS.has(input.kind)) {
    return input.amount > 0 && !hasFulfillmentFields && actorMatchesSource
      && (!SUPPORT_RECURRING_EVENT_KINDS.has(input.kind) || input.contractReference !== null)
  }
  if (SUPPORT_ADJUSTMENT_EVENT_KINDS.has(input.kind)) {
    return input.amount > 0 && input.relatedReference !== null
      && !hasFulfillmentFields && actorMatchesSource
  }
  if (SUPPORT_RECURRING_EVENT_KINDS.has(input.kind) && input.contractReference === null) return false
  if (input.kind === 'recurring_canceled') {
    return input.amount === 0 && !hasFulfillmentFields && actorMatchesSource
  }
  if (input.kind === 'fulfillment_set') {
    return input.amount === 0 && input.relatedReference !== null && input.fulfillmentItem !== null
      && input.fulfillmentStatus !== null && input.actorReference !== null
  }
  if (SUPPORT_ACCOUNT_LINK_EVENT_KINDS.has(input.kind)) {
    return input.amount === 0 && input.contractReference !== null
      && input.relatedReference !== null && !hasFulfillmentFields
      && input.actorReference === null
  }
  return !hasFulfillmentFields && input.actorReference === null
}

function supportAdminAudit(recorded: Record<string, unknown>): SupportAdminProjection['audit'] {
  let incomplete = false
  const totals = recorded.currency_totals_minor
  const currency_totals_minor: Record<string, number> = {}
  if (totals && typeof totals === 'object' && !Array.isArray(totals)) {
    for (const [currency, amount] of Object.entries(totals)) {
      if (/^[A-Z]{3}$/.test(currency) && safeAllowanceNumber(amount) !== null) {
        currency_totals_minor[currency] = amount as number
      } else {
        incomplete = true
      }
    }
  } else {
    incomplete = true
  }

  if (!Array.isArray(recorded.audit)) incomplete = true
  const events = Array.isArray(recorded.audit) ? recorded.audit.flatMap(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      incomplete = true
      return []
    }
    const event = item as Record<string, unknown>
    const eventId = safeSupportEventId(event.event_id)
    const sourceReference = safeOpaqueSupportReference(event.source_event_key)
    const occurredAt = safeSupportAuditTimestamp(event.occurred_at)
    const receivedAt = safeSupportAuditTimestamp(event.received_at)
    const contractReference = event.contract_key === null ? null : safeOpaqueSupportReference(event.contract_key)
    const relatedReference = event.related_event_key === null ? null : safeOpaqueSupportReference(event.related_event_key)
    const actorReference = event.actor_key === null ? null : safeOpaqueSupportReference(event.actor_key)
    const fulfillmentItem = event.fulfillment_item === null
      ? null
      : typeof event.fulfillment_item === 'string' && /^[a-z][a-z0-9_]{1,63}$/.test(event.fulfillment_item)
        ? event.fulfillment_item
        : undefined
    const fulfillmentStatus = event.fulfillment_status === null
      ? null
      : supportFulfillmentStatus(event.fulfillment_status)
    const amount = safeAllowanceNumber(event.amount_minor)
    const kind = typeof event.kind === 'string' && SUPPORT_ADMIN_EVENT_KINDS.has(event.kind)
      ? event.kind
      : null
    if (
      !Number.isSafeInteger(event.sequence)
      || (event.sequence as number) < 1
      || (event.sequence as number) > 50_000
      || eventId === null
      || typeof event.provider !== 'string'
      || !/^[a-z][a-z0-9_]{1,47}$/.test(event.provider)
      || sourceReference === null
      || kind === null
      || occurredAt === null
      || receivedAt === null
      || amount === null
      || amount > 10_000_000_000
      || typeof event.currency !== 'string'
      || !/^[A-Z]{3}$/.test(event.currency)
      || (event.contract_key !== null && contractReference === null)
      || (event.related_event_key !== null && relatedReference === null)
      || fulfillmentItem === undefined
      || fulfillmentStatus === undefined
      || (event.actor_key !== null && actorReference === null)
      || !supportAdminEventContractIsValid({
        provider: event.provider as string,
        kind: kind || '',
        amount: amount ?? 0,
        contractReference,
        relatedReference,
        fulfillmentItem: fulfillmentItem ?? null,
        fulfillmentStatus: fulfillmentStatus ?? null,
        actorReference,
      })
    ) {
      incomplete = true
      return []
    }
    return [{
      sequence: event.sequence as number,
      event_id: eventId,
      provider: event.provider,
      source_reference: sourceReference,
      kind: kind as SupportAdminProjection['audit']['events'][number]['kind'],
      occurred_at: occurredAt,
      received_at: receivedAt,
      amount_minor: amount as number,
      currency: event.currency,
      contract_reference: contractReference,
      related_reference: relatedReference,
      fulfillment_item: fulfillmentItem,
      fulfillment_status: fulfillmentStatus as SupportAdminProjection['audit']['events'][number]['fulfillment_status'],
      actor_reference: actorReference,
    }]
  }) : []

  if (!Array.isArray(recorded.fulfillment)) incomplete = true
  const fulfillment = Array.isArray(recorded.fulfillment) ? recorded.fulfillment.flatMap(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      incomplete = true
      return []
    }
    const row = item as Record<string, unknown>
    const targetEventId = row.target_event_id === null ? null : safeSupportEventId(row.target_event_id)
    const auditEventId = safeSupportEventId(row.audit_event_id)
    const actorReference = safeOpaqueSupportReference(row.actor_key)
    const status = supportFulfillmentStatus(row.status)
    const proofReference = row.proof_reference === undefined || row.proof_reference === null
      ? null
      : safeOpaqueSupportReference(row.proof_reference)
    const changedAt = safeSupportAuditTimestamp(row.changed_at)
    if (
      (row.target_event_id !== null && targetEventId === null)
      || typeof row.item !== 'string'
      || !/^[a-z][a-z0-9_]{1,63}$/.test(row.item)
      || status === undefined
      || auditEventId === null
      || actorReference === null
      || (row.proof_reference !== undefined && row.proof_reference !== null && proofReference === null)
      || changedAt === null
    ) {
      incomplete = true
      return []
    }
    return [{
      target_event_id: targetEventId,
      item: row.item,
      status,
      audit_event_id: auditEventId,
      actor_reference: actorReference,
      proof_reference: proofReference,
      changed_at: changedAt,
    }]
  }) : []

  if (!Array.isArray(recorded.unresolved)) incomplete = true
  const discrepancies = Array.isArray(recorded.unresolved) ? recorded.unresolved.flatMap(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      incomplete = true
      return []
    }
    const row = item as Record<string, unknown>
    const eventId = safeSupportEventId(row.event_id)
    if (
      eventId === null
      || typeof row.reason !== 'string'
      || !SUPPORT_DISCREPANCY_REASONS.has(row.reason)
    ) {
      incomplete = true
      return []
    }
    return [{
      event_id: eventId,
      reason: row.reason as SupportAdminProjection['audit']['discrepancies'][number]['reason'],
    }]
  }) : []

  return { currency_totals_minor, events, fulfillment, discrepancies, incomplete }
}

function supportRecordedAllowance(value: unknown): SupportAccountSummary['recorded_allowance'] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const raw = value as Record<string, unknown>
  const effectiveAllowance = safeAllowanceNumber(raw.effective_allowance)
  const asOf = safeAllowanceTimestamp(raw.as_of)
  const active = raw.state === 'active' && raw.enforcement_enabled === true
  const recordedOnly = raw.state === 'recorded_not_enforced' && raw.enforcement_enabled === false
  if (
    (!active && !recordedOnly)
    || typeof raw.unit !== 'string'
    || !/^[a-z][a-z0-9_]{1,63}$/.test(raw.unit)
    || effectiveAllowance === null
    || asOf === null
    || !Array.isArray(raw.sources)
  ) return undefined

  const sources = raw.sources.flatMap(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const source = item as Record<string, unknown>
    const grantedAllowance = safeAllowanceNumber(source.granted_allowance)
    const sourceEffectiveAllowance = safeAllowanceNumber(source.effective_allowance)
    const expiresAt = source.expires_at === null ? null : safeAllowanceTimestamp(source.expires_at)
    if (
      typeof source.source !== 'string'
      || !SUPPORT_ALLOWANCE_SOURCES.has(source.source)
      || typeof source.status !== 'string'
      || !SUPPORT_ALLOWANCE_STATUSES.has(source.status)
      || typeof source.refund_state !== 'string'
      || !SUPPORT_ALLOWANCE_REFUND_STATES.has(source.refund_state)
      || grantedAllowance === null
      || sourceEffectiveAllowance === null
      || (source.expires_at !== null && expiresAt === null)
    ) return []
    return [{
      source: source.source as NonNullable<SupportAccountSummary['recorded_allowance']>['sources'][number]['source'],
      granted_allowance: grantedAllowance,
      effective_allowance: sourceEffectiveAllowance,
      expires_at: expiresAt,
      status: source.status as NonNullable<SupportAccountSummary['recorded_allowance']>['sources'][number]['status'],
      refund_state: source.refund_state as NonNullable<SupportAccountSummary['recorded_allowance']>['sources'][number]['refund_state'],
    }]
  })
  const sourceTotal = sources.reduce((total, source) => total + source.effective_allowance, 0)
  if (
    sources.length !== raw.sources.length
    || !Number.isSafeInteger(sourceTotal)
    || sourceTotal !== effectiveAllowance
  ) return undefined
  return {
    state: active ? 'active' : 'recorded_not_enforced',
    enforcement_enabled: active,
    unit: raw.unit,
    as_of: asOf,
    effective_allowance: effectiveAllowance,
    sources,
  }
}

function supportAccountSummary(value: RawSupportAccountProjection | undefined): SupportAccountSummary {
  const recorded = value?.recorded || {}
  const benefits = value?.benefits || {}
  const recordedAllowance = supportRecordedAllowance(recorded.recorded_allowance)
  const benefitState = typeof benefits.state === 'string' ? benefits.state : ''
  const schedulerEnforcementEnabled = benefits.scheduler_enforcement_enabled === true
  const effectiveBenefits = stringList(benefits.effective_benefits)
  const recordedEligibility = stringList(benefits.recorded_eligibility)
  const benefitsCoherent = (
    benefitState === 'active'
      ? schedulerEnforcementEnabled
        && effectiveBenefits.length === 1
        && effectiveBenefits[0] === 'bounded_queue_priority'
        && recordedAllowance?.state === 'active'
        && recordedAllowance.enforcement_enabled === true
        && recordedAllowance.effective_allowance > 0
      : benefitState === 'hosted_priority_available'
        ? schedulerEnforcementEnabled
          && effectiveBenefits.length === 0
          && recordedAllowance?.enforcement_enabled !== true
          && (recordedAllowance?.effective_allowance ?? 0) === 0
        : benefitState === 'owner_exempt'
          ? schedulerEnforcementEnabled
            && effectiveBenefits.length === 0
            && recordedAllowance?.enforcement_enabled !== true
        : benefitState === 'recorded_not_enforced'
          && !schedulerEnforcementEnabled
          && effectiveBenefits.length === 0
          && recordedAllowance?.enforcement_enabled !== true
  )
  return {
    event_count: typeof recorded.event_count === 'number' ? recorded.event_count : 0,
    one_time_tier: typeof recorded.one_time_tier === 'string' ? recorded.one_time_tier : null,
    recurring_tier: typeof recorded.recurring_tier === 'string' ? recorded.recurring_tier : null,
    active_recurring_count: typeof recorded.active_recurring_count === 'number'
      ? recorded.active_recurring_count
      : 0,
    ...(benefitsCoherent && recordedAllowance ? { recorded_allowance: recordedAllowance } : {}),
    benefits: benefitsCoherent
      ? {
          state: benefitState,
          scheduler_enforcement_enabled: schedulerEnforcementEnabled,
          effective_benefits: effectiveBenefits,
          recorded_eligibility: recordedEligibility,
        }
      : {
          state: 'recorded_not_enforced',
          scheduler_enforcement_enabled: false,
          effective_benefits: [],
          recorded_eligibility: recordedEligibility,
        },
  }
}

export async function fetchSupportCatalog(): Promise<SupportPublicProjection> {
  return accountRequest<SupportPublicProjection>('/api/v1/support/catalog')
}

export async function fetchSupportSelf(): Promise<SupportSelfProjection> {
  const raw = await accountRequest<SupportPublicProjection & {
    account_support?: RawSupportAccountProjection
    responsible_use: ResponsibleUseProjection
  }>('/api/v1/support/self')
  return {
    public: {
      schema_version: raw.schema_version,
      provider_catalog: raw.provider_catalog,
      benefit_availability: raw.benefit_availability,
      support_priority: raw.support_priority,
    },
    account: supportAccountSummary(raw.account_support),
    responsible_use: raw.responsible_use,
  }
}

export async function fetchResponsibleUse(): Promise<ResponsibleUseProjection> {
  return accountRequest<ResponsibleUseProjection>('/api/v1/support/responsible-use')
}

export async function acceptResponsibleUse(input: {
  documentVersion: number
  contentSha256: string
}): Promise<{ status: ResponsibleUseStatus }> {
  return accountRequest('/api/v1/support/responsible-use/accept', {
    method: 'POST',
    body: JSON.stringify({
      document_version: input.documentVersion,
      content_sha256: input.contentSha256,
    }),
  })
}

export async function fetchAdminAccountSupport(accountId: string): Promise<SupportAdminProjection> {
  const raw = await accountRequest<{
    account_support?: RawSupportAccountProjection
    responsible_use: ResponsibleUseStatus
    support_priority: SupportAdminProjection['support_priority']
  }>(`/api/v1/support/admin/accounts/${encodeURIComponent(accountId)}`)
  return supportAdminProjection(raw)
}

function supportAdminProjection(raw: {
  account_support?: RawSupportAccountProjection
  responsible_use: ResponsibleUseStatus
  support_priority: SupportAdminProjection['support_priority']
}): SupportAdminProjection {
  const recorded = raw.account_support?.recorded || {}
  return {
    account: supportAccountSummary(raw.account_support),
    audit: supportAdminAudit(recorded),
    responsible_use: raw.responsible_use,
    support_priority: raw.support_priority,
  }
}

export async function transitionAdminAccountFulfillment(
  accountId: string,
  input: SupportFulfillmentMutationInput,
): Promise<SupportAdminProjection> {
  const raw = await accountRequest<{
    account_support?: RawSupportAccountProjection
    responsible_use: ResponsibleUseStatus
    support_priority: SupportAdminProjection['support_priority']
  }>(`/api/v1/support/admin/accounts/${encodeURIComponent(accountId)}/fulfillment`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
  return supportAdminProjection(raw)
}

export async function recordAdminAccountContribution(
  accountId: string,
  input: SupportManualContributionInput,
): Promise<SupportAdminProjection> {
  const raw = await accountRequest<{
    account_support?: RawSupportAccountProjection
    responsible_use: ResponsibleUseStatus
    support_priority: SupportAdminProjection['support_priority']
  }>(`/api/v1/support/admin/accounts/${encodeURIComponent(accountId)}/contributions`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
  return supportAdminProjection(raw)
}

export interface Workspace {
  name: string
  path?: string
  file_count?: number
  password_protected?: boolean
  unlocked?: boolean
  remember_policy?: WorkspaceRememberPolicy | null
  unlock_expires_at?: number | null
  unlock_idle_expires_at?: number | null
  project_role?: 'owner' | 'editor' | 'viewer'
  project_permissions?: string[]
}

export type WorkspaceRememberPolicy = 'session' | 'device'

export interface WorkspaceUnlockResult {
  unlocked: boolean
  remember_policy: WorkspaceRememberPolicy
  unlock_expires_at: number
  unlock_idle_expires_at: number
}

export interface WorkspaceLockResult {
  unlocked: false
  locked_count: number
}

export interface WorkspacePasswordResult {
  password_protected: boolean
  unlocked: boolean
  remember_policy: WorkspaceRememberPolicy | null
  unlock_expires_at: number | null
  unlock_idle_expires_at: number | null
}

export async function fetchWorkspaces(): Promise<{ workspaces: Workspace[]; active: string }> {
  const res = await fetch(`${BASE}/api/v1/workspaces`, {
    credentials: 'same-origin',
    cache: 'no-store',
  })
  if (!res.ok) throw new Error('Failed to fetch workspaces')
  return res.json()
}

export async function setActiveWorkspace(name: string): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/workspaces/active`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!res.ok) throw new Error('Failed to switch workspace')
}

export async function createWorkspace(
  name: string,
  password?: string,
  remember: WorkspaceRememberPolicy = 'device',
): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/workspaces`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, password: password || undefined, remember }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to create workspace' }))
    throw new Error(err.detail || 'Failed to create workspace')
  }
}

export async function unlockWorkspace(
  name: string,
  password: string,
  remember: WorkspaceRememberPolicy,
): Promise<WorkspaceUnlockResult> {
  const res = await fetch(`${BASE}/api/v1/workspaces/${encodeURIComponent(name)}/unlock`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password, remember }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unlock failed' }))
    throw new Error(err.detail || 'Unlock failed')
  }
  return res.json()
}

export async function lockWorkspace(name: string): Promise<WorkspaceLockResult> {
  const res = await fetch(`${BASE}/api/v1/workspaces/${encodeURIComponent(name)}/lock`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error('Project could not be locked')
  return res.json()
}

export async function lockAllWorkspaces(): Promise<WorkspaceLockResult> {
  const res = await fetch(`${BASE}/api/v1/workspaces/lock-all`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error('Projects could not be locked')
  return res.json()
}

export async function setWorkspacePassword(
  name: string,
  password: string,
  remember: WorkspaceRememberPolicy = 'device',
): Promise<WorkspacePasswordResult> {
  const res = await fetch(`${BASE}/api/v1/workspaces/${encodeURIComponent(name)}/password`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password, remember }),
  })
  const payload = await res.json().catch(() => null) as ({
    detail?: unknown
    password_protected?: unknown
    unlocked?: unknown
    remember_policy?: unknown
    unlock_expires_at?: unknown
    unlock_idle_expires_at?: unknown
  } | null)
  if (!res.ok) {
    throw new Error(typeof payload?.detail === 'string' ? payload.detail : 'Password update failed')
  }
  return {
    password_protected: payload?.password_protected === true,
    unlocked: payload?.unlocked === true,
    remember_policy: payload?.remember_policy === 'device' || payload?.remember_policy === 'session'
      ? payload.remember_policy
      : null,
    unlock_expires_at: typeof payload?.unlock_expires_at === 'number' ? payload.unlock_expires_at : null,
    unlock_idle_expires_at: typeof payload?.unlock_idle_expires_at === 'number' ? payload.unlock_idle_expires_at : null,
  }
}

// --- Project reference assets ---

export interface ProjectAssetOutput {
  id: string
  filename: string
  relative_path: string
  media_type: string
  label: string
  metadata: ProjectAssetOutputMetadata
}

export type ProjectReferenceSheetMode = 'production' | 'hybrid' | 'draft'
export type ProjectReferenceReviewStatus = 'pass' | 'fail' | 'review_unavailable'

const PROJECT_REFERENCE_ASSET_TYPE_ALIASES: Record<string, ProjectReferenceAssetType> = {
  character: 'character',
  location: 'location',
  setting: 'location',
  prop: 'prop',
  item: 'prop',
  vehicle: 'vehicle',
  machine: 'vehicle',
  creature: 'creature',
  wardrobe: 'wardrobe',
  accessory: 'wardrobe',
  world: 'world',
  style: 'world',
}

export function normalizeProjectReferenceAssetType(value: unknown): ProjectReferenceAssetType | null {
  return typeof value === 'string' ? PROJECT_REFERENCE_ASSET_TYPE_ALIASES[value] ?? null : null
}

export function getDirectorProjectReferenceKind(
  value: unknown,
): 'character' | 'location' | null {
  const normalized = normalizeProjectReferenceAssetType(value)
  return normalized === 'character' || normalized === 'location' ? normalized : null
}

const PROJECT_REFERENCE_ANCHOR_PRIVACY_VALUES: readonly ProjectReferenceAnchorPrivacy[] = [
  'private_blurred', 'private_visible', 'project_blurred', 'project_visible',
]

export function normalizeProjectReferenceAnchorPrivacy(
  value: unknown,
  schemaVersion?: number,
): ProjectReferenceAnchorPrivacy | null {
  if (typeof value === 'string' && PROJECT_REFERENCE_ANCHOR_PRIVACY_VALUES.includes(
    value as ProjectReferenceAnchorPrivacy,
  )) return value as ProjectReferenceAnchorPrivacy
  if (schemaVersion !== 2 && value === 'standard') return 'project_visible'
  return null
}

export interface ProjectReferenceSheetArtifactMetadata {
  role?: string
  model?: string
  provenance?: Record<string, unknown>
  reason_codes?: string[]
}

export interface ProjectReferencePackArtifactMetadata {
  schema_version: 2
  planner_version: string
  role: string
  index: number
  model?: string
  provenance?: {
    strategy?: string
    version?: string
    anchor_role?: string | null
  }
  reason_codes?: string[]
  private_output?: boolean
  anchor_privacy?: ProjectReferenceAnchorPrivacy
  detail?: {
    managed: true
    source_digest?: string
    normalized_crop?: [number, number, number, number]
    requested_operation?: ProjectReferenceDetailOperation
    resolved_operation?: ProjectReferenceDetailOperation
    editor_model?: string | null
    commitment?: string
  } | {
    custom_id: string
    kind: ProjectReferenceDetailKind
    source_role: string
    source_digest: string
    normalized_crop: [number, number, number, number]
    requested_operation: ProjectReferenceDetailOperation
    resolved_operation: ProjectReferenceDetailOperation
    editor_model: string | null
    label_digest: string
    seal: string
  }
}

export interface ProjectAssetOutputMetadata extends Record<string, unknown> {
  private?: boolean
  explicit?: boolean
  initial_blur?: boolean
  reference_sheet?: ProjectReferenceSheetArtifactMetadata
  reference_pack?: ProjectReferencePackArtifactMetadata
  lineage?: {
    parent_job_id?: string
    candidate_index?: number
    candidate_count?: number
    parent_asset_id?: string
    parent_variant_id?: string | null
  }
}

export function projectAssetOutputNeedsInitialBlur(output: ProjectAssetOutput): boolean {
  return output.metadata?.private === true || output.metadata?.initial_blur === true
}

export interface ProjectReferenceSheetVariantMetadata {
  schema_version?: number
  planner_version?: string
  mode?: ProjectReferenceSheetMode
  asset_type?: ProjectReferenceAssetType | ProjectReferenceLegacyAssetType
  model?: string
  generation_model?: string
  editor_model?: string
  roles?: {
    sheet?: string
    panels?: string[]
    repaired?: string[]
  }
  reason_codes?: string[]
  review_status?: ProjectReferenceReviewStatus
  max_repair_attempts?: number
  repair_attempts_used?: number
  anchor_privacy?: ProjectReferenceLegacyAnchorPrivacy
}

export type ProjectReferenceQualityStatus = 'pass' | 'residual' | 'review_unavailable'
export type ProjectReferenceAssessmentClass = 'exact' | 'minor_residual' | 'material_residual'
export type ProjectReferenceAssessmentSeverity = ProjectReferenceAssessmentClass | 'not_applicable'
export type ProjectReferenceRecommendationBasis =
  | 'accepted_assessment'
  | 'preliminary_ungraded'
  | 'residual_assessment'

export interface ProjectReferenceFidelityAssessment {
  version: 'fidelity_assessment_v2' | string
  assessment_class: ProjectReferenceAssessmentClass
  worst_severity: ProjectReferenceAssessmentSeverity
  residual_count: number
  score_basis_points: number | null
  status: 'pass' | 'fail'
  dimension_checks: Record<string, boolean>
  failed_roles: string[]
  reason_codes: string[]
}

export interface ProjectReferencePublicQuality {
  status: ProjectReferenceQualityStatus
  warning: string | null
  review_deferred: boolean
  assessment: ProjectReferenceFidelityAssessment | null
  recommended: boolean
  recommendation_basis: ProjectReferenceRecommendationBasis | null
}

export interface ProjectReferenceQualityPresentation {
  stateLabel: string
  gradeLabel: string | null
  scoreLabel: string | null
  residualSummary: string | null
  correctionAvailable: boolean
  recommended: boolean
  preliminary: boolean
  notice: string | null
  tone: 'pass' | 'residual' | 'deferred'
}

export interface ProjectReferencePackVariantMetadata extends Partial<ProjectReferencePackPlan> {
  schema_version: 2
  planner_version: string
  plan_seal?: string
  reference_type?: ProjectReferenceAssetType
  anchor_role?: string | null
  generation_model?: string
  editor_model?: string | null
  user_loras?: { count: number; preserved: boolean }
  additional_loras?: ProjectReferenceAdditionalLoraSummary
  private_output?: boolean
  operation_routing?: ProjectReferenceOperationRouting
  review_status?: ProjectReferenceReviewStatus
  max_repair_attempts?: number
  repair_attempts_used?: number
  quality?: ProjectReferencePublicQuality
  roles?: {
    sheets?: string[]
    repaired?: string[]
  }
}

const PROJECT_REFERENCE_RESIDUAL_LABELS: Readonly<Record<string, string>> = {
  identity_mismatch: 'identity',
  request_mismatch: 'authored request',
  view_mismatch: 'view or pose',
  accessory_mismatch: 'accessories',
  style_mismatch: 'style',
  overall_fidelity_mismatch: 'overall fidelity',
  mature_register_mismatch: 'anatomy or mature register',
  violent_register_mismatch: 'violent register',
  detail_register_mismatch: 'authored details',
}

const PROJECT_REFERENCE_GRADE_LABELS: Readonly<Record<ProjectReferenceAssessmentClass, string>> = {
  exact: 'Exact',
  minor_residual: 'Minor residuals',
  material_residual: 'Material residuals',
}

export function projectReferenceQualityPresentation(
  metadata: ProjectReferencePackVariantMetadata | null | undefined,
): ProjectReferenceQualityPresentation | null {
  const quality = metadata?.quality
  if (!quality) return null
  if (quality.status === 'review_unavailable') {
    return {
      stateLabel: 'Fidelity review deferred',
      gradeLabel: 'Ungraded',
      scoreLabel: null,
      residualSummary: null,
      correctionAvailable: false,
      recommended: quality.recommended,
      preliminary: quality.recommended && quality.recommendation_basis === 'preliminary_ungraded',
      notice: 'This candidate remains usable; compare it yourself until fidelity review is available.',
      tone: 'deferred',
    }
  }
  const assessment = quality.assessment
  if (!assessment) return null
  const reasonLabels = [...new Set(assessment.reason_codes.flatMap(code => {
    const label = PROJECT_REFERENCE_RESIDUAL_LABELS[code]
    return label ? [label] : []
  }))]
  const residualSummary = quality.status === 'residual'
    ? reasonLabels.length > 0
      ? `Differences: ${reasonLabels.slice(0, 3).join(', ')}${reasonLabels.length > 3 ? ` +${reasonLabels.length - 3}` : ''}`
      : 'Residual differences were recorded.'
    : null
  return {
    stateLabel: quality.status === 'pass' ? 'Fidelity passed' : 'Fidelity reviewed',
    gradeLabel: PROJECT_REFERENCE_GRADE_LABELS[assessment.assessment_class],
    scoreLabel: assessment.score_basis_points == null
      ? null
      : `${(assessment.score_basis_points / 100).toFixed(assessment.score_basis_points % 100 === 0 ? 0 : 1)}%`,
    residualSummary,
    correctionAvailable: quality.status === 'residual'
      && metadata.review?.final_correction?.template_id === 'reference-residual-correction',
    recommended: quality.recommended,
    preliminary: false,
    notice: quality.status === 'residual'
      ? 'This candidate remains usable; review the noted differences before keeping it.'
      : null,
    tone: quality.status,
  }
}

export interface ProjectReferenceJobQualitySummary {
  candidateCount: number
  variantLabel: string
  presentation: ProjectReferenceQualityPresentation
}

export function projectReferenceJobQualitySummary(
  assets: readonly ProjectAsset[],
  jobId: string,
): ProjectReferenceJobQualitySummary | null {
  const candidates = assets.flatMap(asset => asset.variants).filter(variant => (
    variant.variant_type === 'reference_pack' && variant.metadata.job?.id === jobId
  ))
  const recommended = candidates.filter(variant => (
    variant.metadata.reference_pack?.quality?.recommended === true
  ))
  if (recommended.length !== 1) return null
  const presentation = projectReferenceQualityPresentation(
    recommended[0].metadata.reference_pack,
  )
  return presentation ? {
    candidateCount: candidates.length,
    variantLabel: recommended[0].label,
    presentation,
  } : null
}

export interface ProjectAssetVariant {
  id: string
  variant_type: string
  label: string
  status: 'candidate' | 'kept' | 'rejected'
  outputs: ProjectAssetOutput[]
  metadata: Record<string, unknown> & {
    reference_sheet?: ProjectReferenceSheetVariantMetadata
    reference_pack?: ProjectReferencePackVariantMetadata
    job?: {
      id?: string
      model?: string
      generation_model?: string
      editor_model?: string | null
      candidate_index?: number
      candidate_count?: number
      max_repair_attempts_per_candidate?: number
      repair_attempts_used_per_candidate?: number
      retry?: {
        parent_variant_id?: string | null
        instruction_present?: boolean
        plan_seal?: string
      }
    }
    parent?: {
      asset_id?: string
      variant_id?: string | null
    }
  }
}

export interface ProjectAsset {
  id: string
  asset_type: ProjectReferenceAssetType | ProjectReferenceLegacyAssetType | string
  name: string
  description: string
  tags: string[]
  variants: ProjectAssetVariant[]
  metadata: Record<string, unknown>
}

export interface ProjectReferenceGenerationSettings {
  /** URL-safe idempotency key. Optional only for legacy callers/hosts. */
  request_id?: string
  schema_version?: 2
  mode?: ProjectReferenceSheetMode
  intent?: ProjectReferenceIntent
  depth?: ProjectReferenceDepth
  /** Accepted only with Custom depth; all presets resolve their own sheet count. */
  sheet_count?: number
  type_fields?: ProjectReferenceTypeFields
  managed_layout_assist?: ProjectReferenceManagedLayoutAssistMode
  preset?: ProjectReferencePreset
  anchor_basis?: ProjectReferenceAnchorBasis
  detail_callouts?: ProjectReferenceDetailCallout[]
  planning_model?: string
  planning_provider?: string
  review_model?: string
  review_provider?: string
  model_type?: string
  editor_model_type?: string
  candidate_count?: number
  panel_size?: [number, number]
  draft_size?: [number, number]
  resolution?: string | [number, number]
  columns?: number
  palette_swatches?: number
  review?: boolean
  max_repair_attempts?: number
  num_inference_steps?: number
  guidance_scale?: number
  seed?: number
  negative_prompt?: string
  activated_loras?: string[]
  loras_multipliers?: string
  additional_loras?: ProjectReferenceAdditionalLora[]
  private_output?: boolean
  explicit_output?: boolean
  /** Character-only server-managed callout convenience; independent of output handling. */
  explicit_convenience?: boolean
  content_capability?: 'standard' | 'unrestricted_local'
  initial_blur?: boolean
  intelligence_policy?: 'standard_auto' | 'uncensored_auto'
  /** Exact authored style; owner-private replay restores this on Retry/Edit. */
  style?: string
}

export interface FreshProjectReferenceGenerationRequest extends ProjectReferenceGenerationSettings {
  asset_id?: never
  parent_variant_id?: never
  edit_instruction?: never
  character_profile?: ProjectReferenceCharacterProfileInput
  name: string
  asset_type: ProjectReferenceAssetType
  description?: string
  tags?: string[]
  poses?: string
  outfits?: string
  genre?: string
}

export interface ExistingProjectReferenceGenerationRequest extends ProjectReferenceGenerationSettings {
  asset_id: string
  parent_variant_id?: string
  edit_instruction?: string
  asset_type?: ProjectReferenceAssetType
  character_profile?: ProjectReferenceCharacterProfileInput
}

export type ProjectReferenceGenerationRequest =
  | FreshProjectReferenceGenerationRequest
  | ExistingProjectReferenceGenerationRequest

export interface ProjectReferenceGenerationResponse {
  job_id: string
  asset: ProjectAsset
  /** Present for v2 requests; absent on v1-compatible hosts/responses. */
  plan?: ProjectReferencePackPlan
}

export interface ProjectReferenceCapabilities {
  schema_version: 2
  planner_version: string
  intents: ProjectReferenceIntent[]
  depths: Record<ProjectReferenceDepth, {
    sheet_count?: number
    minimum?: number
    maximum?: number
    default?: number
  }>
  reference_types: Array<{
    id: ProjectReferenceAssetType
    presets: Array<{
      id: ProjectReferencePreset
      label: string
      ordered_roles: string[]
      valid_source_roles: string[]
      detail_operations: ProjectReferenceDetailOperation[]
    }>
    type_fields: Array<{
      id: string
      groups: Array<{
        id: string
        label: string
        options: Array<{ id: string; label: string }>
      }>
    }>
    detail_kinds: Array<{ id: Exclude<ProjectReferenceDetailKind, 'custom'>; label: string }>
    supports_custom_details: boolean
  }>
  detail_operations: ProjectReferenceDetailOperation[]
  lora_scopes: ProjectReferenceLoraScope[]
  content_capabilities: Array<'standard' | 'unrestricted_local'>
  intelligence_policies: Array<'standard_auto' | 'uncensored_auto'>
  uncensored_auto_review: {
    requested_model: 'auto_local'
    resolved_model: string
    resolved_provider: 'local'
    vision_required: true
    required_projector: string
    installed: boolean
    projector_available: boolean
    vision_capable: boolean
    resident: boolean
    vision_available: boolean | null
    loading: boolean
    loading_phase: string | null
    setup_state:
      | 'missing_model'
      | 'missing_projector'
      | 'loading'
      | 'loaded_without_vision'
      | 'ready_unloaded'
      | 'ready_resident'
    /** Server-authoritative worker-time readiness; residency is not required. */
    queue_ready: boolean
  }
  explicit_generation_model: {
    preferred_order: string[]
    resolved_model: string
    fallback_model: string
    selection_source: 'verified_manual_preference' | 'fallback'
    candidates: Array<{
      model_type: string
      enabled: boolean
      manual_checkpoint_verified: boolean
      terms_accepted: boolean
      downloaded: boolean
      ready: boolean
    }>
  }
  review_policy: {
    mandatory_for_content_capabilities: Array<'unrestricted_local'>
    mandatory_when_explicit_output: true
    off_allowed_for_content_capabilities: Array<'standard'>
    mandatory_contract: 'explicit_unrestricted_fidelity_v1'
  }
  character_profile: {
    schema_version: 1
    genders: ProjectReferenceCharacterGender[]
    age: { optional: true; minimum: 0; maximum: 999 }
    explicit_anatomy: ProjectReferenceCharacterAnatomy[]
    explicit_convenience: {
      supported: boolean
      requires_explicit_output: boolean
    }
  }
  max_candidate_count: number
  max_repair_attempts: number
  default_models: {
    generation_model: string
    editor_model: string
  }
}

export interface ProjectReferenceAuthoringSnapshot {
  schema_version: 2
  asset_id: string
  variant_id: string
  authored_settings: {
    seal: string
    /** Absent only for legacy snapshots created before exact style replay. */
    style?: string
    type_fields: ProjectReferenceTypeFields
    detail_callouts: ProjectReferenceDetailCallout[]
    character_profile?: ProjectReferenceCharacterProfileInput
    explicit_convenience: boolean
  }
  /** Owner-private, no-store replay values. Public pack metadata contains digests only. */
  additional_loras?: ProjectReferencePrivateAdditionalLora[]
}

export interface ProjectReferenceModelCatalogEntry {
  model_type: string
  name: string
  image_outputs?: boolean
  supports_ref_images?: boolean
  is_downloaded?: boolean
  downloadable?: boolean
  manual_checkpoint_verification_required?: boolean
  manual_checkpoint_verified?: boolean
  manual_installation?: ModelManualInstallation
}

export const PROJECT_REFERENCE_EXPLICIT_CONVENIENCE_AGE_BLOCKER =
  'Explicit convenience requires an omitted age or an authored age of at least 18.'
export const PROJECT_REFERENCE_CHARACTER_AGE_BLOCKER =
  'Character age must be blank or a whole number from 0 through 999.'

const PROJECT_REFERENCE_CHARACTER_ANATOMY_ORDER: readonly ProjectReferenceCharacterAnatomy[] = [
  'breasts', 'vulva', 'penis',
]

export interface ProjectReferenceCharacterProfileDraft {
  gender: ProjectReferenceCharacterGender
  ageInput: string
  explicitAnatomy: readonly ProjectReferenceCharacterAnatomy[]
}

export interface ProjectReferenceCharacterProfileSerialization {
  profile?: ProjectReferenceCharacterProfileInput
  age: number | null
  blocker: string | null
}

/** Serialize only explicitly authored Character facts; blank/default state stays absent. */
export function serializeProjectReferenceCharacterProfile(
  draft: ProjectReferenceCharacterProfileDraft,
  explicitConvenience: boolean,
): ProjectReferenceCharacterProfileSerialization {
  const trimmedAge = draft.ageInput.trim()
  const age = trimmedAge === '' ? null : Number(trimmedAge)
  if (trimmedAge !== '' && (!/^\d{1,3}$/.test(trimmedAge)
    || age === null || !Number.isInteger(age) || age < 0 || age > 999)) {
    return { age: null, blocker: PROJECT_REFERENCE_CHARACTER_AGE_BLOCKER }
  }
  if (explicitConvenience && age !== null && age < 18) {
    return { age, blocker: PROJECT_REFERENCE_EXPLICIT_CONVENIENCE_AGE_BLOCKER }
  }
  const selected = new Set(draft.explicitAnatomy)
  const explicitAnatomy = PROJECT_REFERENCE_CHARACTER_ANATOMY_ORDER.filter(item => selected.has(item))
  const hasAuthoredFacts = draft.gender !== 'unspecified' || age !== null || explicitAnatomy.length > 0
  return {
    age,
    blocker: null,
    ...(hasAuthoredFacts ? {
      profile: {
        gender: draft.gender,
        ...(age !== null ? { age } : {}),
        explicit_anatomy: explicitAnatomy,
      },
    } : {}),
  }
}

export function getLoraParameterDefaults(
  schema: LoraParameterSchema | undefined,
): Record<string, LoraParameterValue> {
  if (!schema) return {}
  return Object.fromEntries(schema.parameters.flatMap(parameter => (
    parameter.default === undefined ? [] : [[parameter.id, parameter.default]]
  )))
}

export function getLoraParameterValue(
  parameter: LoraParameterDefinition,
  values: Record<string, LoraParameterValue> | undefined,
): LoraParameterValue | undefined {
  return values && Object.prototype.hasOwnProperty.call(values, parameter.id)
    ? values[parameter.id]
    : parameter.default
}

export function getLoraParameterOptionToken(value: LoraParameterValue): string {
  return JSON.stringify([typeof value, value])
}

export function validateLoraParameterValues(
  schema: LoraParameterSchema | undefined,
  values: Record<string, LoraParameterValue> | undefined,
): string[] {
  if (!schema) return values && Object.keys(values).length > 0
    ? ['This LoRA no longer publishes a parameter schema.']
    : []
  const current = values ?? {}
  const definitions = new Map(schema.parameters.map(parameter => [parameter.id, parameter]))
  const errors: string[] = []
  for (const id of Object.keys(current)) {
    if (!definitions.has(id)) errors.push(`Unknown parameter: ${id}.`)
  }
  for (const parameter of schema.parameters) {
    if (parameter.type === 'enum') {
      const optionTokens = (parameter.options ?? []).map(option => (
        getLoraParameterOptionToken(option.value)
      ))
      if (new Set(optionTokens).size !== optionTokens.length) {
        errors.push(`${parameter.label} publishes ambiguous duplicate choices.`)
      }
    }
    const value = getLoraParameterValue(parameter, current)
    if (value === undefined) {
      if (parameter.required) errors.push(`${parameter.label} is required.`)
      continue
    }
    if (parameter.type === 'boolean') {
      if (typeof value !== 'boolean') errors.push(`${parameter.label} must be Yes or No.`)
      continue
    }
    if (parameter.type === 'text') {
      if (typeof value !== 'string') errors.push(`${parameter.label} must be text.`)
      else if ([...value].some(character => {
        const code = character.charCodeAt(0)
        return code < 32 || code === 127
      })) {
        errors.push(`${parameter.label} cannot contain control characters.`)
      } else if (parameter.min_length !== undefined && [...value].length < parameter.min_length) {
        errors.push(`${parameter.label} must be at least ${parameter.min_length} characters.`)
      } else if (parameter.max_length !== undefined && [...value].length > parameter.max_length) {
        errors.push(`${parameter.label} must be at most ${parameter.max_length} characters.`)
      }
      continue
    }
    if (parameter.type === 'enum') {
      const matches = parameter.options?.some(option => (
        typeof option.value === typeof value && option.value === value
      )) === true
      if (!matches) errors.push(`${parameter.label} must use one of the published choices.`)
      continue
    }
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      errors.push(`${parameter.label} must be a finite number.`)
      continue
    }
    if (parameter.type === 'integer' && !Number.isInteger(value)) {
      errors.push(`${parameter.label} must be a whole number.`)
    }
    if (parameter.minimum !== undefined && value < parameter.minimum) {
      errors.push(`${parameter.label} must be at least ${parameter.minimum}.`)
    }
    if (parameter.maximum !== undefined && value > parameter.maximum) {
      errors.push(`${parameter.label} must be at most ${parameter.maximum}.`)
    }
    if (parameter.minimum !== undefined && parameter.step !== undefined) {
      const offset = (value - parameter.minimum) / parameter.step
      if (!Number.isFinite(offset) || Math.abs(offset - Math.round(offset)) > 1e-9) {
        errors.push(`${parameter.label} must follow the published step of ${parameter.step}.`)
      }
    }
  }
  return errors
}

/** Build Director's exact role-specific wire row from one server catalog row. */
export function createDirectorImageRoleLoraSelection(
  lora: LoraInfo,
  multiplier = lora.recommended_weights?.default ?? 1,
): DirectorImageRoleLoraSelection {
  const selection: DirectorImageRoleLoraSelection = {
    id: lora.filename,
    multiplier: Math.max(-10, Math.min(10, multiplier)),
  }
  if (lora.parameter_schema) {
    selection.parameter_schema_digest = lora.parameter_schema.schema_digest
    selection.parameter_values = getLoraParameterDefaults(lora.parameter_schema)
  }
  return selection
}

/** Strip client-only or stale object keys before crossing the strict role wire. */
export function toDirectorImageRoleLoraWire(
  selections: readonly DirectorImageRoleLoraSelection[],
): DirectorImageRoleLoraSelection[] {
  return selections.map(selection => ({
    id: selection.id,
    multiplier: selection.multiplier,
    ...(selection.parameter_schema_digest !== undefined ? {
      parameter_schema_digest: selection.parameter_schema_digest,
      parameter_values: { ...(selection.parameter_values ?? {}) },
    } : {}),
  }))
}

/**
 * Mirror the server's content-free Director LoRA shape checks so invalid or
 * stale schema selections are caught before a pipeline request is sent.
 */
export function validateDirectorImageRoleLoraSelections(
  selections: readonly DirectorImageRoleLoraSelection[],
  catalog: readonly LoraInfo[],
): string[] {
  const errors: string[] = []
  if (selections.length > 64) errors.push('Select at most 64 LoRAs for one Director image role.')
  const byFilename = new Map(catalog.map(lora => [lora.filename, lora]))
  const seen = new Set<string>()
  for (const selection of selections) {
    const prefix = selection.id ? `${selection.id}: ` : ''
    if (!selection.id || selection.id.length > 512
      || selection.id !== selection.id.split(/[\\/]/).pop()) {
      errors.push(`${prefix}LoRA id must be one catalog filename.`)
      continue
    }
    if (seen.has(selection.id)) {
      errors.push(`${prefix}Select each LoRA only once per image role.`)
      continue
    }
    seen.add(selection.id)
    if (typeof selection.multiplier !== 'number' || !Number.isFinite(selection.multiplier)
      || selection.multiplier < -10 || selection.multiplier > 10) {
      errors.push(`${prefix}Multiplier must be between -10 and 10.`)
    }
    const current = byFilename.get(selection.id)
    if (!current) {
      errors.push(`${prefix}This LoRA is no longer available for the selected model.`)
      continue
    }
    const schema = current.parameter_schema
    if (!schema) {
      if (selection.parameter_schema_digest !== undefined
        || selection.parameter_values !== undefined) {
        errors.push(`${prefix}This LoRA no longer publishes a parameter schema.`)
      }
      continue
    }
    if (!/^[0-9a-f]{64}$/.test(selection.parameter_schema_digest || '')
      || selection.parameter_schema_digest !== schema.schema_digest) {
      errors.push(`${prefix}Its published input schema changed. Remove and add it again.`)
      continue
    }
    if (selection.parameter_values === undefined) {
      errors.push(`${prefix}Published parameter values are required.`)
      continue
    }
    errors.push(...validateLoraParameterValues(schema, selection.parameter_values)
      .map(error => `${prefix}${error}`))
  }
  return errors
}

export function loraParameterSchemasConflict(
  generationSchema: LoraParameterSchema | undefined,
  editingSchema: LoraParameterSchema | undefined,
  scope: ProjectReferenceLoraScope,
  generationCompatible: boolean,
  editingCompatible: boolean,
): boolean {
  if (scope !== 'auto' || !generationCompatible || !editingCompatible) return false
  if (Boolean(generationSchema) !== Boolean(editingSchema)) return true
  return Boolean(generationSchema && editingSchema)
    && generationSchema?.schema_digest !== editingSchema?.schema_digest
}

export function hasProjectReferenceLoraParameterSummary(
  item: { parameters?: unknown },
): boolean {
  return item.parameters !== undefined
}

export function getProjectReferenceModelAvailabilityCopy(
  model: ProjectReferenceModelCatalogEntry,
): string {
  if (model.downloadable === false) {
    return model.manual_checkpoint_verified
      ? ' (manual checkpoint verified)'
      : ' (manual install and verification required)'
  }
  return model.is_downloaded === false ? ' (download required)' : ''
}

export function getProjectReferenceGenerationModels<T extends ProjectReferenceModelCatalogEntry>(
  models: readonly T[],
): T[] {
  return models.filter(model => model.image_outputs === true)
}

export function getProjectReferenceEditorModels<T extends ProjectReferenceModelCatalogEntry>(
  models: readonly T[],
): T[] {
  return models.filter(model => (
    model.image_outputs === true && model.supports_ref_images === true
  ))
}

export interface ProjectReferenceQueueBlockerState {
  submitting: boolean
  project_locked: boolean
  loading: boolean
  name_missing: boolean
  capabilities_unavailable: boolean
  deliverables_unavailable: boolean
  generation_model_missing: boolean
  editor_model_missing: boolean
  terms_pending: boolean
  manual_verification_pending: boolean
  incompatible_lora: boolean
  invalid_lora_multiplier: boolean
  invalid_lora_parameters: boolean
  invalid_authored_settings: boolean
  invalid_character_age: boolean
  explicit_convenience_age: boolean
  too_many_detail_callouts: boolean
  review_unavailable: boolean
}

export interface ProjectReferenceQueueBlocker {
  id: keyof ProjectReferenceQueueBlockerState
  message: string
}

const PROJECT_REFERENCE_QUEUE_BLOCKER_COPY: Record<
  keyof ProjectReferenceQueueBlockerState,
  string
> = {
  submitting: 'A reference pack is already being submitted.',
  project_locked: 'Unlock this project before queueing a reference pack.',
  loading: 'Wait for Reference project data to finish loading.',
  name_missing: 'Enter a reference name.',
  capabilities_unavailable: 'Reference capabilities are unavailable.',
  deliverables_unavailable: 'The authoritative pack layout is unavailable.',
  generation_model_missing: 'Select an available generation model.',
  editor_model_missing: 'Select an available reference-image editor for this mode.',
  terms_pending: 'Review and accept every selected model notice.',
  manual_verification_pending: 'Install and locally verify every selected manual checkpoint.',
  incompatible_lora: 'Change or remove LoRAs that are incompatible with their selected operation.',
  invalid_lora_multiplier: 'Set every LoRA multiplier between -10 and 10.',
  invalid_lora_parameters: 'Complete every required LoRA parameter with a valid published value.',
  invalid_authored_settings: 'Fix invalid or unavailable authored reference details.',
  invalid_character_age: PROJECT_REFERENCE_CHARACTER_AGE_BLOCKER,
  explicit_convenience_age: PROJECT_REFERENCE_EXPLICIT_CONVENIENCE_AGE_BLOCKER,
  too_many_detail_callouts: 'Select at most eight combined authored and managed detail callouts.',
  review_unavailable: 'Prepare the required local fidelity reviewer and MMProj shown above.',
}

export function getProjectReferenceQueueBlockers(
  state: ProjectReferenceQueueBlockerState,
): ProjectReferenceQueueBlocker[] {
  return (Object.keys(PROJECT_REFERENCE_QUEUE_BLOCKER_COPY) as Array<keyof ProjectReferenceQueueBlockerState>)
    .filter(id => state[id])
    .map(id => ({ id, message: PROJECT_REFERENCE_QUEUE_BLOCKER_COPY[id] }))
}

export function getProjectReferenceVisibilityHints(
  modelTypes: readonly string[],
  enabledModels: ReadonlySet<string>,
  catalog: readonly ProjectReferenceModelCatalogEntry[],
  loaded: boolean,
): { disabled: string[]; enabled_missing: string[] } {
  if (!loaded) return { disabled: [], enabled_missing: [] }
  const catalogTypes = new Set(catalog.map(model => model.model_type))
  return {
    disabled: modelTypes.filter(modelType => !enabledModels.has(modelType)),
    enabled_missing: modelTypes.filter(modelType => (
      enabledModels.has(modelType) && !catalogTypes.has(modelType)
    )),
  }
}

export function selectProjectReferenceModel(
  models: readonly ProjectReferenceModelCatalogEntry[],
  current: string,
  preferred = '',
): string {
  if (models.some(model => model.model_type === current)) return current
  if (preferred && models.some(model => model.model_type === preferred)) return preferred
  return models[0]?.model_type ?? ''
}

export function getProjectReferencePreferredGenerationModel(
  mode: ProjectReferenceSheetMode,
  explicitOutput: boolean,
  contentCapability: 'standard' | 'unrestricted_local',
  capabilities: ProjectReferenceCapabilities | null,
): string {
  if (mode === 'draft') return 'flux2_klein_9b'
  if (explicitOutput || contentCapability === 'unrestricted_local') {
    return capabilities?.explicit_generation_model?.resolved_model
      || capabilities?.default_models.generation_model
      || ''
  }
  return capabilities?.default_models.generation_model ?? ''
}

export function getEffectiveProjectReferenceRepairAttempts(
  mode: ProjectReferenceSheetMode,
  review: boolean,
  requested: number,
): number {
  if (!review || mode === 'draft') return 0
  if (!Number.isInteger(requested)) return 1
  return Math.max(1, Math.min(5, requested))
}

export function getProjectReferenceRepairCopy(
  metadata?: {
    repair_attempts_used?: number
    roles?: { repaired?: string[] }
  },
): string {
  const repaired = Array.isArray(metadata?.roles?.repaired)
    ? metadata.roles.repaired.filter(role => typeof role === 'string')
    : []
  const recordedAttempts = metadata?.repair_attempts_used
  const attempts = Number.isInteger(recordedAttempts) && Number(recordedAttempts) >= 0
    ? Number(recordedAttempts)
    : repaired.length
  if (attempts === 0) return 'No repair was needed.'
  const targets = repaired.length > 0
    ? repaired.map(role => (
      role.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase())
    )).join(', ')
    : 'the requested panels'
  return `${attempts} bounded repair ${attempts === 1 ? 'attempt' : 'attempts'} regenerated ${targets}.`
}

const PROJECT_ASSET_STATUS_MESSAGES: Partial<Record<number, string>> = {
  401: 'Project reference access was denied',
  403: 'Project reference access was denied',
  404: 'Project references are unavailable for this project',
  409: 'Project reference request conflicts with the current state',
  423: 'Project reference access is locked',
  500: 'Project reference service failed',
  503: 'Project reference storage is unavailable',
}

export class ProjectAssetRequestError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ProjectAssetRequestError'
    this.status = status
  }
}

/** Build a fixed, status-aware error without reflecting response content. */
export function projectAssetRequestError(
  status: number,
  fallback: string,
): ProjectAssetRequestError {
  const safeStatus = Number.isInteger(status) && status >= 100 && status <= 599
    ? status
    : 0
  const message = PROJECT_ASSET_STATUS_MESSAGES[safeStatus] ?? fallback
  return new ProjectAssetRequestError(
    safeStatus,
    safeStatus > 0 ? `${message} (HTTP ${safeStatus})` : message,
  )
}

/** Render only fixed client copy; arbitrary exceptions may contain private details. */
export function projectReferenceSafeErrorMessage(reason: unknown, fallback: string): string {
  return reason instanceof ProjectAssetRequestError ? reason.message : fallback
}

/** Select the sole semantic reference represented by a reference-sheet variant. */
export function selectProjectAssetApplyOutput(
  variant: ProjectAssetVariant,
): ProjectAssetOutput | undefined {
  if (variant.variant_type === 'reference_pack') {
    return getProjectAssetApplyOutputs(variant)[0]
  }
  if (variant.variant_type !== 'reference_sheet') return variant.outputs[0]
  return variant.outputs.find(output => output.metadata?.reference_sheet?.role === 'sheet')
    // Compatibility with reference-sheet records written before artifact roles.
    ?? variant.outputs[0]
}

/** Whole-pack application order is authored by public, prompt-free indices. */
export function getProjectAssetApplyOutputs(
  variant: ProjectAssetVariant,
): ProjectAssetOutput[] {
  if (variant.variant_type !== 'reference_pack') {
    const output = selectProjectAssetApplyOutput(variant)
    return output ? [output] : []
  }
  return variant.outputs
    .map((output, originalIndex) => ({ output, originalIndex }))
    .sort((left, right) => {
      const leftIndex = left.output.metadata.reference_pack?.index
      const rightIndex = right.output.metadata.reference_pack?.index
      const safeLeft = Number.isInteger(leftIndex) ? Number(leftIndex) : left.originalIndex
      const safeRight = Number.isInteger(rightIndex) ? Number(rightIndex) : right.originalIndex
      return safeLeft - safeRight || left.originalIndex - right.originalIndex
    })
    .map(({ output }) => output)
}

/** Return display-only components; these must never be applied separately. */
export function getProjectAssetComponentOutputs(
  variant: ProjectAssetVariant,
): ProjectAssetOutput[] {
  if (variant.variant_type === 'reference_pack') return []
  if (variant.variant_type !== 'reference_sheet') return []
  const sheet = selectProjectAssetApplyOutput(variant)
  return variant.outputs.filter(output => (
    output !== sheet && output.metadata?.reference_sheet?.role !== 'sheet'
  ))
}

export function projectAssetVariantOperationKey(
  project: string,
  assetId: string,
  variantId: string,
): string {
  return JSON.stringify([project, assetId, variantId])
}

/** Acquire a synchronous per-variant guard before React can rerender. */
export function lockProjectAssetVariantOperation(
  locks: Set<string>,
  project: string,
  assetId: string,
  variantId: string,
): string | null {
  const key = projectAssetVariantOperationKey(project, assetId, variantId)
  if (locks.has(key)) return null
  locks.add(key)
  return key
}

export function isProjectAssetOperationCurrent(
  submittedProject: string,
  submittedEpoch: number,
  currentProject: string,
  currentEpoch: number,
): boolean {
  return submittedProject === currentProject && submittedEpoch === currentEpoch
}

export interface ProjectReferenceRetrySettings {
  mode: ProjectReferenceSheetMode
  model_type: string
  editor_model_type?: string
  private_output: boolean
  explicit_output: boolean
  explicit_convenience?: boolean
  character_profile?: ProjectReferenceCharacterProfileInput
  content_capability?: 'standard' | 'unrestricted_local'
  initial_blur?: boolean
  intelligence_policy?: 'standard_auto' | 'uncensored_auto'
  style?: string
  additional_loras?: ProjectReferenceAdditionalLora[]
  review: boolean
  max_repair_attempts: number
  schema_version?: 2
  asset_type?: ProjectReferenceAssetType
  intent?: ProjectReferenceIntent
  depth?: ProjectReferenceDepth
  sheet_count?: number
  type_fields?: ProjectReferenceTypeFields
  managed_layout_assist?: ProjectReferenceManagedLayoutAssistMode
  preset?: ProjectReferencePreset
  anchor_basis?: ProjectReferenceAnchorBasis
  detail_callouts?: ProjectReferenceDetailCallout[]
  planning_model?: string
  planning_provider?: string
  review_model?: string
  review_provider?: string
  /** Session-only proof that private labels came from this exact public summary. */
  authored_settings_seal?: string
}

export function isProjectReferenceReviewMandatory(
  contentCapability: 'standard' | 'unrestricted_local' | undefined,
  explicitOutput: boolean,
  policy: ProjectReferenceCapabilities['review_policy'] | undefined,
): boolean {
  return (policy
    ? policy.mandatory_for_content_capabilities.some(capability => capability === contentCapability)
    : contentCapability === 'unrestricted_local')
    || (explicitOutput && (policy?.mandatory_when_explicit_output ?? true))
}

export function isProjectReferenceReviewerEligible(
  intelligencePolicy: 'standard_auto' | 'uncensored_auto',
  modelId: string | undefined,
  provider: string | undefined,
  reviewModels: ReadonlyArray<{ id: string; provider?: string }>,
  capabilities: ProjectReferenceCapabilities | null,
): boolean {
  if (!modelId || modelId === 'off') return false
  if (intelligencePolicy === 'uncensored_auto') {
    const contract = capabilities?.uncensored_auto_review
    if (!contract?.queue_ready) return false
    return modelId === 'auto_local'
      || (modelId === contract.resolved_model
        && (!provider || provider === contract.resolved_provider))
  }
  if (modelId === 'auto_local') {
    return reviewModels.some(model => (model.provider ?? 'local') === 'local')
  }
  return reviewModels.some(model => (
    model.id === modelId && (!provider || (model.provider ?? 'local') === provider)
  ))
}

export function getProjectReferenceReviewerSetupCopy(
  contract: ProjectReferenceCapabilities['uncensored_auto_review'] | undefined,
): string {
  if (!contract) {
    return 'The required local fidelity-review setup is unavailable. Refresh Reference and try again.'
  }
  switch (contract.setup_state) {
    case 'ready_resident':
      return 'Paperscarecrow is loaded with its MMProj and ready for local fidelity review.'
    case 'ready_unloaded':
      return 'Paperscarecrow and its MMProj are installed. They will load automatically when local fidelity review starts.'
    case 'loading':
      return `Paperscarecrow is loading${contract.loading_phase ? ` (${contract.loading_phase})` : ''}. Queueing will unlock when its MMProj setup is confirmed.`
    case 'missing_model':
      return 'The required Paperscarecrow reviewer checkpoint is not installed. Install and verify that exact local model before queueing.'
    case 'missing_projector':
      return 'Paperscarecrow is installed, but its required MMProj is missing. Install the listed MMProj before queueing.'
    case 'loaded_without_vision':
      return 'Paperscarecrow is loaded, but vision is unavailable because its MMProj did not initialize. Reload or repair the local reviewer before queueing.'
  }
}

export function getProjectReferenceReviewerAction(
  setupState: ProjectReferenceCapabilities['uncensored_auto_review']['setup_state'] | undefined,
): { kind: 'load' | 'reload'; label: string } | null {
  if (setupState === 'missing_model' || setupState === 'missing_projector') {
    return { kind: 'load', label: 'Install / load required reviewer' }
  }
  if (setupState === 'loaded_without_vision') {
    return { kind: 'reload', label: 'Reload required reviewer' }
  }
  return null
}

export interface ProjectReferenceRetryReviewDecision {
  ready: boolean
  use_current_reviewer: boolean
  intelligence_policy: 'standard_auto' | 'uncensored_auto'
}

export function resolveProjectReferenceRetryReview(
  source: Pick<ProjectReferenceRetrySettings,
    'content_capability' | 'explicit_output' | 'intelligence_policy'
    | 'review' | 'review_model' | 'review_provider'>,
  current: { review_model: string; review_provider?: string },
  reviewModels: ReadonlyArray<{ id: string; provider?: string }>,
  capabilities: ProjectReferenceCapabilities | null,
): ProjectReferenceRetryReviewDecision {
  const mandatory = isProjectReferenceReviewMandatory(
    source.content_capability, source.explicit_output, capabilities?.review_policy,
  )
  const intelligencePolicy = source.intelligence_policy
    ?? (mandatory ? 'uncensored_auto' : 'standard_auto')
  if (!mandatory) {
    return { ready: true, use_current_reviewer: false, intelligence_policy: intelligencePolicy }
  }
  if (source.review && isProjectReferenceReviewerEligible(
    intelligencePolicy, source.review_model, source.review_provider,
    reviewModels, capabilities,
  )) {
    return { ready: true, use_current_reviewer: false, intelligence_policy: intelligencePolicy }
  }
  const currentEligible = isProjectReferenceReviewerEligible(
    intelligencePolicy, current.review_model, current.review_provider,
    reviewModels, capabilities,
  )
  return {
    ready: currentEligible,
    use_current_reviewer: currentEligible,
    intelligence_policy: intelligencePolicy,
  }
}

/** Private style/labels are required to replay any opaque authored identity. */
export function projectReferenceRetryNeedsPrivateAuthoring(
  variant: ProjectAssetVariant,
): boolean {
  const authored = variant.metadata.reference_pack?.authored_settings
  return authored?.style_present === true
    || authored?.type_fields.some(field => field.items.some(item => item.custom)) === true
    || authored?.detail_callouts.some(callout => 'kind' in callout && callout.kind === 'custom') === true
    || authored?.character_profile !== undefined
    || authored?.managed_character_callouts !== undefined
}

export function isProjectReferenceStyleReplayReady(
  authored: { style_present?: boolean; style_commitment?: string } | undefined,
  style: unknown,
): boolean {
  const declaresPresence = authored != null
    && Object.prototype.hasOwnProperty.call(authored, 'style_present')
  const declaresCommitment = authored != null
    && Object.prototype.hasOwnProperty.call(authored, 'style_commitment')
  // Legacy plans declare neither field. A current contract is atomic: partial
  // summaries fail closed even when they claim the authored style was empty.
  if (!declaresPresence && !declaresCommitment) return true
  if (typeof authored?.style_present !== 'boolean'
    || typeof authored.style_commitment !== 'string'
    || !/^[0-9a-f]{64}$/.test(authored.style_commitment)) return false
  return authored.style_present === false
    || (typeof style === 'string' && style.trim().length > 0)
}

function hasCommitment(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

/**
 * The no-store authoring route validates the full authored-settings seal. This
 * additional shape/count check prevents a partial or stale private snapshot
 * from being replayed when a public Character summary is present.
 */
export function isProjectReferenceCharacterReplayReady(
  packMetadata: Pick<ProjectReferencePackPlan,
    'authored_settings' | 'explicit_convenience'> | undefined,
  snapshot: Pick<ProjectReferenceAuthoringSnapshot['authored_settings'],
    'character_profile' | 'explicit_convenience'> | undefined,
): boolean {
  const authored = packMetadata?.authored_settings
  const publicProfile = authored?.character_profile
  const publicManaged = authored?.managed_character_callouts
  if (!publicProfile && !publicManaged) {
    return snapshot?.character_profile === undefined
  }
  if (!publicProfile || !publicManaged
    || typeof packMetadata?.explicit_convenience !== 'boolean'
    || snapshot?.explicit_convenience !== packMetadata.explicit_convenience) return false
  const profile = snapshot?.character_profile
  if (!profile
    || Object.keys(profile).some(key => !['gender', 'age', 'explicit_anatomy'].includes(key))
    || !['woman', 'man', 'non_binary', 'unspecified'].includes(profile.gender)
    || !Array.isArray(profile.explicit_anatomy)
    || new Set(profile.explicit_anatomy).size !== profile.explicit_anatomy.length
    || profile.explicit_anatomy.some(item => !PROJECT_REFERENCE_CHARACTER_ANATOMY_ORDER.includes(item))
    || profile.explicit_anatomy.some((item, index) => (
      PROJECT_REFERENCE_CHARACTER_ANATOMY_ORDER.filter(candidate => (
        profile.explicit_anatomy.includes(candidate)
      ))[index] !== item
    ))) return false
  const agePresent = profile.age !== undefined && profile.age !== null
  if (agePresent && (!Number.isInteger(profile.age) || Number(profile.age) < 0 || Number(profile.age) > 999)) return false
  if (publicProfile.schema_version !== 1
    || publicProfile.gender.present !== (profile.gender !== 'unspecified')
    || publicProfile.age.present !== agePresent
    || publicProfile.explicit_anatomy.count !== profile.explicit_anatomy.length
    || publicProfile.explicit_anatomy.commitments.length !== profile.explicit_anatomy.length
    || (publicProfile.gender.present ? !hasCommitment(publicProfile.gender.commitment) : publicProfile.gender.commitment !== null)
    || (publicProfile.age.present ? !hasCommitment(publicProfile.age.commitment) : publicProfile.age.commitment !== null)
    || publicProfile.explicit_anatomy.commitments.some(commitment => !hasCommitment(commitment))) return false
  const expectedManagedCount = publicManaged.active_count + publicManaged.tombstone_count
  if (publicManaged.schema_version !== 1
    || !Number.isInteger(publicManaged.active_count) || publicManaged.active_count < 0
    || !Number.isInteger(publicManaged.tombstone_count) || publicManaged.tombstone_count < 0
    || !Number.isInteger(publicManaged.rename_count) || publicManaged.rename_count < 0
    || publicManaged.rename_count > publicManaged.active_count
    || publicManaged.commitments.length !== expectedManagedCount
    || publicManaged.commitments.some(commitment => !hasCommitment(commitment))) return false
  return true
}

function resolveProjectReferenceRetryAuthoredSettings(
  packMetadata: ProjectReferencePackVariantMetadata,
  fallback: ProjectReferenceRetrySettings,
  capabilities?: ProjectReferenceCapabilities,
): Pick<ProjectReferenceRetrySettings,
  'style' | 'type_fields' | 'detail_callouts' | 'character_profile' | 'explicit_convenience'> {
  const summary = packMetadata.authored_settings
  if (!summary) return {}
  const referenceType = packMetadata.reference_type ?? fallback.asset_type
  const hasExactPrivateSnapshot = fallback.authored_settings_seal === summary.seal
  const typeCapability = capabilities?.reference_types.find(item => item.id === referenceType)
  const fallbackFields = (fallback.type_fields ?? {}) as Record<string, ProjectReferenceTypeFieldItem[] | undefined>
  const resolvedFields: Record<string, ProjectReferenceTypeFieldItem[]> = {}
  for (const field of summary.type_fields) {
    const resolvedItems: ProjectReferenceTypeFieldItem[] = []
    for (const publicItem of field.items) {
      const localItem = hasExactPrivateSnapshot && publicItem.custom
        ? fallbackFields[field.field]?.find(item => (
        item.id === publicItem.id
        && item.custom === publicItem.custom
        && item.group === publicItem.group
        ))
        : undefined
      if (localItem) {
        resolvedItems.push(localItem)
        continue
      }
      if (publicItem.custom) return {}
      const group = typeCapability?.type_fields.find(item => item.id === field.field)
        ?.groups.find(item => item.id === publicItem.group)
      const option = group?.options.find(item => item.id === publicItem.id)
      if (!option) return {}
      resolvedItems.push({ ...publicItem, label: option.label })
    }
    resolvedFields[field.field] = resolvedItems
  }

  const fallbackCallouts = fallback.detail_callouts ?? []
  const characterReplayReady = isProjectReferenceCharacterReplayReady(
    packMetadata,
    hasExactPrivateSnapshot && typeof fallback.explicit_convenience === 'boolean'
      ? {
          character_profile: fallback.character_profile,
          explicit_convenience: fallback.explicit_convenience,
        }
      : undefined,
  )
  if ((summary.character_profile || summary.managed_character_callouts) && !characterReplayReady) return {}
  const publicAuthoredCallouts = summary.detail_callouts.flatMap(
    callout => 'managed' in callout ? [] : [callout],
  )
  if (hasExactPrivateSnapshot && fallbackCallouts.length !== publicAuthoredCallouts.length) return {}
  const resolvedCallouts: ProjectReferenceDetailCallout[] = []
  for (const publicCallout of publicAuthoredCallouts) {
    const localCallout = hasExactPrivateSnapshot
      ? fallbackCallouts.find(item => item.custom_id === publicCallout.custom_id)
      : undefined
    if (localCallout) {
      resolvedCallouts.push({
        ...localCallout,
        kind: publicCallout.kind,
        operation: publicCallout.requested_operation,
        source_role: publicCallout.source_role,
      })
      continue
    }
    if (publicCallout.kind === 'custom') return {}
    const kind = typeCapability?.detail_kinds.find(item => item.id === publicCallout.kind)
    if (!kind) return {}
    resolvedCallouts.push({
      custom_id: publicCallout.custom_id,
      label: kind.label,
      kind: publicCallout.kind,
      operation: publicCallout.requested_operation,
      source_role: publicCallout.source_role,
    })
  }
  return {
    ...(summary.style_present === true
      && hasExactPrivateSnapshot
      && typeof fallback.style === 'string'
      && fallback.style.length > 0
      ? { style: fallback.style }
      : summary.style_present === false ? { style: '' } : {}),
    type_fields: resolvedFields as ProjectReferenceTypeFields,
    detail_callouts: resolvedCallouts,
    ...(summary.character_profile ? {
      character_profile: fallback.character_profile,
    } : {}),
    explicit_convenience: fallback.explicit_convenience ?? false,
  }
}

/** Preserve available source settings; layout controls are deliberately current. */
export function getProjectReferenceRetrySettings(
  variant: ProjectAssetVariant,
  fallback: ProjectReferenceRetrySettings,
  capabilities?: ProjectReferenceCapabilities,
): ProjectReferenceRetrySettings {
  const metadata = variant.metadata.reference_sheet
  const packMetadata = variant.metadata.reference_pack
  const output = selectProjectAssetApplyOutput(variant)
  const mode = packMetadata?.mode ?? metadata?.mode
  const selectedMode = mode === 'production' || mode === 'hybrid' || mode === 'draft'
    ? mode
    : fallback.mode
  const model = packMetadata?.generation_model ?? metadata?.generation_model ?? metadata?.model
  const editorModel = packMetadata?.editor_model ?? metadata?.editor_model
  const fallbackEditorModel = fallback.editor_model_type?.trim()
  const requestedRepairs = typeof packMetadata?.max_repair_attempts === 'number'
    ? packMetadata.max_repair_attempts
    : typeof metadata?.max_repair_attempts === 'number'
      ? metadata.max_repair_attempts
    : fallback.max_repair_attempts
  const settings: ProjectReferenceRetrySettings = {
    mode: selectedMode,
    model_type: typeof model === 'string' && model.length > 0 ? model : fallback.model_type,
    editor_model_type: typeof editorModel === 'string' && editorModel.length > 0
      ? editorModel
      : fallbackEditorModel || undefined,
    private_output: typeof packMetadata?.private_output === 'boolean'
      ? packMetadata.private_output
      : typeof output?.metadata.private === 'boolean'
      ? output.metadata.private
      : fallback.private_output,
    explicit_output: typeof packMetadata?.explicit_output === 'boolean'
      ? packMetadata.explicit_output
      : typeof output?.metadata.explicit === 'boolean'
      ? output.metadata.explicit
      : fallback.explicit_output,
    // `review_unavailable` is also the no-review representation in v1
    // metadata, so current user intent is the only unambiguous source here.
    review: fallback.review,
    max_repair_attempts: getEffectiveProjectReferenceRepairAttempts(
      selectedMode,
      fallback.review,
      requestedRepairs,
    ),
  }
  if (fallback.asset_type) settings.asset_type = fallback.asset_type
  if (fallback.preset) settings.preset = fallback.preset
  if (fallback.anchor_basis) settings.anchor_basis = fallback.anchor_basis
  if (packMetadata?.schema_version === 2) {
    settings.schema_version = 2
    settings.asset_type = packMetadata.reference_type ?? fallback.asset_type
    settings.intent = packMetadata.intent ?? fallback.intent ?? 'generic'
    settings.depth = packMetadata.depth ?? fallback.depth ?? 'standard'
    settings.sheet_count = settings.depth === 'custom'
      ? packMetadata.sheet_count ?? fallback.sheet_count
      : undefined
    const authoredSettings = resolveProjectReferenceRetryAuthoredSettings(
      packMetadata,
      fallback,
      capabilities,
    )
    if (authoredSettings.style !== undefined) {
      settings.style = authoredSettings.style
    }
    if (authoredSettings.type_fields !== undefined) {
      settings.type_fields = authoredSettings.type_fields
    }
    settings.managed_layout_assist = 'off'
    settings.preset = packMetadata.preset ?? fallback.preset
    settings.anchor_basis = packMetadata.anchor_basis ?? fallback.anchor_basis
    if (authoredSettings.detail_callouts !== undefined) {
      settings.detail_callouts = authoredSettings.detail_callouts
    }
    if (authoredSettings.character_profile !== undefined) {
      settings.character_profile = authoredSettings.character_profile
    }
    if (authoredSettings.explicit_convenience !== undefined) {
      settings.explicit_convenience = authoredSettings.explicit_convenience
    }
    settings.content_capability = packMetadata.content_capability ?? fallback.content_capability
    settings.initial_blur = packMetadata.initial_blur ?? fallback.initial_blur
    settings.intelligence_policy = packMetadata.intelligence_policy ?? fallback.intelligence_policy
    const summarizedLoras = [
      ...(packMetadata.additional_loras?.applied ?? []),
      ...(packMetadata.additional_loras?.skipped ?? []),
    ]
    settings.additional_loras = packMetadata.additional_loras
      ? [...new Map(summarizedLoras.map(lora => [lora.id, {
          id: lora.id,
          multiplier: lora.weight,
          scope: lora.requested_scope,
        }])).values()]
      : fallback.additional_loras
    settings.planning_model = packMetadata.planning?.resolved_model
      ?? packMetadata.planning?.requested_model
      ?? fallback.planning_model
    settings.planning_provider = settings.planning_model === 'auto' || settings.planning_model === 'deterministic'
      ? undefined
      : packMetadata.planning?.resolved_provider ?? fallback.planning_provider
    settings.review_model = packMetadata.review?.resolved_model
      ?? packMetadata.review?.requested_model
      ?? fallback.review_model
    settings.review_provider = settings.review_model === 'auto_local' || settings.review_model === 'off'
      ? undefined
      : packMetadata.review?.resolved_provider ?? fallback.review_provider
    settings.review = settings.review_model !== 'off'
    settings.max_repair_attempts = getEffectiveProjectReferenceRepairAttempts(
      selectedMode,
      settings.review,
      requestedRepairs,
    )
  }
  return settings
}

export async function fetchProjectAssets(project: string): Promise<ProjectAsset[]> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets`)
  if (!res.ok) throw projectAssetRequestError(res.status, 'Failed to load project references')
  const data = await res.json()
  return data.assets || []
}

export async function fetchProjectReferenceCapabilities(
  project: string,
): Promise<ProjectReferenceCapabilities> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets/reference-capabilities`)
  if (!res.ok) throw projectAssetRequestError(res.status, 'Failed to load Reference capabilities')
  return res.json()
}

export async function fetchProjectReferenceAuthoring(
  project: string,
  assetId: string,
  variantId: string,
  signal?: AbortSignal,
): Promise<ProjectReferenceAuthoringSnapshot> {
  const res = await fetch(
    `${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets/${encodeURIComponent(assetId)}/variants/${encodeURIComponent(variantId)}/reference-authoring`,
    { signal, cache: 'no-store' },
  )
  if (!res.ok) throw projectAssetRequestError(res.status, 'Exact reference authoring is unavailable')
  return res.json()
}

export async function createProjectAsset(project: string, body: Record<string, unknown>): Promise<ProjectAsset> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) throw projectAssetRequestError(res.status, 'Failed to create reference card')
  return res.json()
}

export const PROJECT_REFERENCE_REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._~-]{7,127}$/
const PROJECT_REFERENCE_REQUEST_RANDOM_BYTES = 18
const PROJECT_REFERENCE_REQUEST_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-'
const PROJECT_REFERENCE_AMBIGUOUS_TRANSPORT_ATTEMPTS = 2

export function projectReferenceRequestIdFromRandomBytes(bytes: Uint8Array): string {
  if (bytes.length !== PROJECT_REFERENCE_REQUEST_RANDOM_BYTES) {
    throw new Error(`Reference request IDs require ${PROJECT_REFERENCE_REQUEST_RANDOM_BYTES} random bytes.`)
  }
  return `ref_${Array.from(bytes, byte => PROJECT_REFERENCE_REQUEST_ALPHABET[byte & 63]).join('')}`
}

export function createProjectReferenceRequestId(): string {
  return projectReferenceRequestIdFromRandomBytes(
    globalThis.crypto.getRandomValues(new Uint8Array(PROJECT_REFERENCE_REQUEST_RANDOM_BYTES)),
  )
}

class ProjectReferenceAmbiguousTransportError extends Error {
  constructor() {
    super('Reference submission transport ended before acceptance could be confirmed.')
    this.name = 'ProjectReferenceAmbiguousTransportError'
  }
}

async function postProjectAssetReferences(
  project: string,
  encodedBody: string,
): Promise<ProjectReferenceGenerationResponse> {
  let response: Response
  try {
    response = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets/generate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: encodedBody,
    })
  } catch {
    throw new ProjectReferenceAmbiguousTransportError()
  }
  if (!response.ok) {
    throw projectAssetRequestError(response.status, 'Failed to start reference generation')
  }
  try {
    return await response.json()
  } catch {
    throw new ProjectReferenceAmbiguousTransportError()
  }
}

export async function generateProjectAssetReferences(
  project: string,
  body: ProjectReferenceGenerationRequest,
): Promise<ProjectReferenceGenerationResponse> {
  if (body.request_id !== undefined && !PROJECT_REFERENCE_REQUEST_ID_PATTERN.test(body.request_id)) {
    throw new Error('Invalid Reference request ID.')
  }
  const encodedBody = JSON.stringify(body)
  const attempts = body.request_id === undefined ? 1 : PROJECT_REFERENCE_AMBIGUOUS_TRANSPORT_ATTEMPTS
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await postProjectAssetReferences(project, encodedBody)
    } catch (reason) {
      if (!(reason instanceof ProjectReferenceAmbiguousTransportError) || attempt + 1 >= attempts) throw reason
    }
  }
  throw new ProjectReferenceAmbiguousTransportError()
}

export async function addProjectAssetVariant(
  project: string, assetId: string, body: Record<string, unknown>,
): Promise<ProjectAssetVariant> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets/${encodeURIComponent(assetId)}/variants`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) throw projectAssetRequestError(res.status, 'Failed to import reference media')
  return res.json()
}

export async function setProjectAssetVariantStatus(
  project: string, assetId: string, variantId: string, status: 'candidate' | 'kept' | 'rejected',
): Promise<ProjectAssetVariant> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets/${encodeURIComponent(assetId)}/variants/${encodeURIComponent(variantId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
  })
  if (!res.ok) throw projectAssetRequestError(res.status, 'Failed to update reference candidate')
  return res.json()
}

export async function deleteProjectAssetVariant(
  project: string, assetId: string, variantId: string,
): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets/${encodeURIComponent(assetId)}/variants/${encodeURIComponent(variantId)}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw projectAssetRequestError(res.status, 'Failed to delete reference candidate')
}

export function getProjectAssetMediaUrl(project: string, relativePath: string): string {
  return `${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets/media/${relativePath.split('/').map(encodeURIComponent).join('/')}`
}

export interface BlenderStatus {
  installed: boolean
  ready: boolean
  mcp_attested: boolean
  runtime_attested: boolean
  bridge_ready: boolean
  recovery_action: string
  workspace: string
  bridge: string
  blender_min_version: string
  blender_version?: string | null
  arbitrary_code: false
  max_total_frames: number
}

export interface BlenderSemanticLegendEntry {
  object_name: string
  primitive: string
  color: [number, number, number, number]
  subject: string
  action: string
}

export interface BlenderSemanticMapping {
  legend: BlenderSemanticLegendEntry[]
  conditioned_prompt: string
}

async function blenderRequest<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${BASE}/api/v1/blender/${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Blender operation failed' }))
    throw new Error(err.detail || 'Blender operation failed')
  }
  return res.json()
}

export async function fetchBlenderStatus(workspace: string): Promise<BlenderStatus> {
  const res = await fetch(`${BASE}/api/v1/blender/status?workspace=${encodeURIComponent(workspace)}`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Blender MCP is unavailable' }))
    throw new Error(err.detail || 'Blender MCP is unavailable')
  }
  return res.json()
}

export const createBlenderScene = (body: Record<string, unknown>) => blenderRequest<Record<string, unknown>>('scene', body)
export interface BlenderDirectorPlan {
  workspace: string
  director_prompt: string
  scene: Record<string, unknown>
  animation: Record<string, unknown>
  semantic_mapping: BlenderSemanticMapping
  review_frames: number[]
  notes: string
  duration_seconds: number
  frame_count: number
  fps: number
  llm_model: string
  confirmation_required: boolean
  review_strategy: string
}
export interface BlenderDirectorFinal {
  status: 'awaiting_user_review'
  workspace: string
  video: {
    filename: string
    url: string
    fps: number
    frame_start: number
    frame_end: number
    frame_count: number
    duration_seconds: number
    recommended_model_type: string
    recommended_video_prompt_type: string
    stage2_controlled: boolean
  }
  asset_id: string
  variant_id: string
  director_model: string
  director_reviews: Array<{
    attempt: number
    verdict: string
    analysis: string
    review_frames: number[]
  }>
  semantic_mapping: BlenderSemanticMapping
  final_plan: BlenderDirectorPlan
}
export const planBlenderScene = (body: Record<string, unknown>) => blenderRequest<BlenderDirectorPlan>('director-plan', body)
export const finalizeBlenderScene = (body: Record<string, unknown>) => blenderRequest<BlenderDirectorFinal>('director-finalize', body)
export const animateBlenderScene = (body: Record<string, unknown>) => blenderRequest<Record<string, unknown>>('animate', body)
export const inspectBlenderScene = (body: Record<string, unknown>) => blenderRequest<Record<string, unknown>>('inspect', body)
export const renderBlenderPreviews = (body: Record<string, unknown>) => blenderRequest<{ previews: { filename: string; url: string }[] }>('render', body)

export async function deleteWorkspace(name: string): Promise<{ switched_to_default: boolean; files_deleted: number }> {
  const res = await fetch(`${BASE}/api/v1/workspaces/${encodeURIComponent(name)}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to delete workspace' }))
    throw new Error(err.detail || 'Failed to delete workspace')
  }
  return res.json()
}

// --- Job Management ---

export async function cancelJob(jobId: string): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/cancel/${encodeURIComponent(jobId)}`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to cancel job')
}

export async function fetchActiveJobs(): Promise<{ jobs: ApiJobStatus[] }> {
  const res = await fetch(`${BASE}/api/v1/jobs`)
  if (!res.ok) throw new Error('Failed to fetch jobs')
  return res.json()
}

// --- Move to Workspace ---

export interface OutputSelection {
  name: string
  workspace: string
  revision?: string
}

export interface BulkOutputResult {
  name: string
  workspace: string
  ok: boolean
  moved?: string[]
  changed?: string[]
  deleted?: string[]
  failed?: string[]
  error?: string
}

async function _bulkOutputRequest(
  action: 'move' | 'privacy' | 'delete',
  body: Record<string, unknown>,
): Promise<{ results: BulkOutputResult[] }> {
  const res = await fetch(`${BASE}/api/v1/outputs/bulk/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Bulk ${action} failed` }))
    throw new Error(err.detail || `Bulk ${action} failed`)
  }
  return res.json()
}

export function bulkMoveOutputs(items: OutputSelection[], targetWorkspace: string) {
  return _bulkOutputRequest('move', { items, target_workspace: targetWorkspace })
}

export function bulkSetOutputPrivacy(items: OutputSelection[], privateOutput: boolean) {
  return _bulkOutputRequest('privacy', { items, private: privateOutput })
}

export function bulkDeleteOutputs(items: OutputSelection[], cascade = true) {
  return _bulkOutputRequest('delete', { items, cascade })
}

export async function moveOutput(name: string, workspace: string, sourceWorkspace?: string): Promise<void> {
  const result = await bulkMoveOutputs(
    [{ name, workspace: sourceWorkspace || 'default' }], workspace,
  )
  const failure = result.results.find(item => !item.ok)
  if (failure) throw new Error(failure.error || 'Move failed')
}

// --- Favorites ---

export async function toggleFavorite(name: string, workspace: string): Promise<{ name: string; favorite: boolean }> {
  const res = await fetch(`${BASE}/api/v1/favorites/${encodeURIComponent(name)}?workspace=${encodeURIComponent(workspace)}`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to toggle favorite')
  return res.json()
}

// --- Outputs ---

const OUTPUT_SEARCH_FIELDS = ['model', 'lora', 'seed', 'reference', 'after', 'before'] as const

export function buildOutputSearchQuery(text: string, filters: OutputSearchFilters = {}): string {
  const parts = [text.trim()]
  for (const field of OUTPUT_SEARCH_FIELDS) {
    const value = filters[field]?.trim()
    if (value) parts.push(`${field}:${JSON.stringify(value)}`)
  }
  return parts.filter(Boolean).join(' ')
}

export function splitOutputSearchQuery(query: string): { text: string; filters: OutputSearchFilters } {
  const filters: OutputSearchFilters = {}
  const selector = /(?:^|\s)(model|lora|seed|reference|after|before):(?:"((?:\\.|[^"\\])*)"|(\S+))/gi
  const text = query.replace(selector, (_match, rawField: string, quoted: string | undefined, bare: string | undefined) => {
    const field = rawField.toLowerCase() as keyof OutputSearchFilters
    let value = bare || ''
    if (quoted !== undefined) {
      try {
        value = JSON.parse(`"${quoted}"`) as string
      } catch {
        value = quoted
      }
    }
    if (field === 'reference') {
      filters.reference = value === 'with' || value === 'without' ? value : ''
    } else {
      filters[field] = value
    }
    return ' '
  }).replace(/\s+/g, ' ').trim()
  return { text, filters }
}

export async function fetchOutputs(limit = 0, offset = 0, opts?: { favoritesOnly?: boolean; multiclipOnly?: boolean; search?: string; filters?: OutputSearchFilters; workspace?: string; artifactScope?: OutputArtifactScope; mediaType?: string }): Promise<{ outputs: ApiOutput[]; total: number }> {
  const params = new URLSearchParams()
  params.set('artifact_scope', opts?.artifactScope ?? 'final')
  if (limit > 0) params.set('limit', String(limit))
  if (offset > 0) params.set('offset', String(offset))
  if (opts?.favoritesOnly) params.set('favorites_only', 'true')
  if (opts?.multiclipOnly) params.set('multiclip_only', 'true')
  const outputSearch = buildOutputSearchQuery(opts?.search || '', opts?.filters)
  if (outputSearch) params.set('search', outputSearch)
  // "__uploads__" browses the uploads folder (virtual Uploads view)
  if (opts?.workspace) params.set('workspace', opts.workspace)
  if (opts?.mediaType && opts.mediaType !== 'all') params.set('media_type', opts.mediaType)
  const qs = params.toString()
  const res = await fetch(`${BASE}/api/v1/outputs${qs ? '?' + qs : ''}`)
  if (!res.ok) throw new Error('Failed to fetch outputs')
  const data = await res.json()
  return { outputs: data.outputs, total: data.total ?? data.outputs.length }
}

export function getFileUrl(filename: string, workspace?: string): string {
  const query = workspace ? `?workspace=${encodeURIComponent(workspace)}` : ''
  return `${BASE}/api/v1/file/${encodeURIComponent(filename)}${query}`
}

export interface OutputShareLink {
  share_path: string
  public_url: string
  configured_public_origin: boolean
  created_at: number
  explicit: boolean
}

export async function createOutputShare(
  name: string, workspace: string, revision: string,
): Promise<OutputShareLink> {
  const res = await fetch(`${BASE}/api/v1/output-shares`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, workspace, revision }),
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Could not create share link' }))
    throw new Error(error.detail || 'Could not create share link')
  }
  return res.json()
}

export async function revokeOutputShare(name: string, workspace: string): Promise<number> {
  const res = await fetch(`${BASE}/api/v1/output-shares`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, workspace }),
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Could not revoke share link' }))
    throw new Error(error.detail || 'Could not revoke share link')
  }
  const data = await res.json()
  return Number(data.revoked || 0)
}

export function getUploadUrl(filename: string): string {
  return `${BASE}/api/v1/uploads/${encodeURIComponent(filename)}`
}

export async function fetchOutputMetadata(name: string, workspace?: string): Promise<import('../types').OutputMetadata> {
  // Retry with a per-attempt timeout. On a slow/high-latency link (e.g. the user
  // is remote over VPN) the request can stall long enough that a single attempt
  // hangs or is dropped by an intermediary; the old single-shot fetch then left
  // the caller with no metadata and the "Load Settings" button a silent no-op.
  const query = workspace ? `?workspace=${encodeURIComponent(workspace)}` : ''
  const url = `${BASE}/api/v1/outputs/${encodeURIComponent(name)}/metadata${query}`
  const ATTEMPTS = 3
  const PER_ATTEMPT_MS = 30000  // generous: the server may read embedded video metadata to recover a seed
  let lastErr: unknown = null
  for (let attempt = 0; attempt < ATTEMPTS; attempt++) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), PER_ATTEMPT_MS)
    try {
      const res = await fetch(url, { signal: controller.signal })
      if (!res.ok) return { source: 'none', params: null }
      return await res.json()
    } catch (e) {
      lastErr = e
      // Diagnostic: AbortError = our per-attempt timeout fired (link too slow);
      // TypeError = network failure / dropped connection. Helps pinpoint a
      // "Load Settings does nothing over VPN" report.
      console.warn(`[LoadSettings] fetchOutputMetadata attempt ${attempt + 1}/${ATTEMPTS} failed:`,
                   (e as { name?: string })?.name || e)
      if (attempt < ATTEMPTS - 1) {
        await new Promise(r => setTimeout(r, 400 * (attempt + 1)))  // brief backoff before retry
      }
    } finally {
      clearTimeout(timer)
    }
  }
  throw lastErr  // all attempts failed — loadOutputMetadata's catch sets meta null
}

export interface OutputCleanupResult {
  final: string
  deleted: string[]
  deferred: string[]
  failed: string[]
}

export async function deleteOutput(name: string, deleteComponents = false, workspace?: string): Promise<{
  deleted: string
  components?: OutputCleanupResult | null
}> {
  const params = new URLSearchParams()
  if (deleteComponents) params.set('delete_components', 'true')
  if (workspace) params.set('workspace', workspace)
  const suffix = params.size ? `?${params}` : ''
  const res = await fetch(`${BASE}/api/v1/outputs/${encodeURIComponent(name)}${suffix}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to delete output' }))
    throw new Error(err.detail || 'Failed to delete output')
  }
  return res.json()
}

export async function deleteOutputComponents(name: string, workspace?: string): Promise<OutputCleanupResult> {
  const suffix = workspace ? `?workspace=${encodeURIComponent(workspace)}` : ''
  const res = await fetch(`${BASE}/api/v1/outputs/${encodeURIComponent(name)}/components${suffix}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to clean linked components' }))
    throw new Error(err.detail || 'Failed to clean linked components')
  }
  const result = await res.json()
  return result
}

export async function rejoinClips(groupId: string, workspace: string, audioFile?: string): Promise<{ filename: string; clip_count: number }> {
  const res = await fetch(`${BASE}/api/v1/outputs/rejoin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ group_id: groupId, workspace, audio_file: audioFile }),
  })
  if (!res.ok) throw new Error('Failed to rejoin clips')
  return res.json()
}

export async function fetchGroupClips(groupId: string, workspace: string): Promise<{ group_id: string; clips: Array<{ filename: string; index: number; total: number; prompt: string }> }> {
  const res = await fetch(`${BASE}/api/v1/outputs/group/${encodeURIComponent(groupId)}?workspace=${encodeURIComponent(workspace)}`)
  if (!res.ok) throw new Error('Failed to fetch group clips')
  return res.json()
}

// --- Director Pipeline ---

export interface PipelineLlmProgress {
  phase: string
  pass: string
  activity: string
  partial_text: string
  attempt: number
  attempt_limit: number
  generated_tokens_approx: number
  elapsed_seconds: number
  live_tps: number | null
  average_tps: number | null
  done: boolean
}

export interface PipelineStatus extends DirectorRecoveryMetadata {
  id: string
  workspace: string
  status: 'queued' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled' | 'blocked'
  phase: 'registered' | 'planning' | 'polishing_prompts' | 'generating_images' | 'generating_video' | 'post_processing' | 'completed' | 'paused' | 'resuming' | 'blocked_remote_reauth' | 'blocked_input_changed'
  auto_mode: boolean
  progress: {
    current: number
    total: number
    message: string
    step: number
    total_steps: number
    indeterminate?: boolean
    window_current?: number
    window_total?: number
    window_step?: number
    window_total_steps?: number
    window_progress?: number
    overall_progress?: number
  }
  clip_plans: Array<{ video_prompt: string; image_prompt: string }>
  clip_images: string[]
  output_files: string[]
  error: string | null
  /** Present only on failed pipelines that look like CUDA OOMs.
   *  See `OomInfo` in types/index.ts. */
  oom_info?: import('../types').OomInfo | null
  pause_reason: string | null
  /** Process-memory-only telemetry for this exact live Director pipeline. */
  llm_progress: PipelineLlmProgress | null
  /** Content-free planning duration; retained after the transient stream ends. */
  llm_planning_time_sec?: number | null
  /** Non-fatal warnings raised during the run — currently used for
   *  architecture-mismatch advisories when image LoRAs are dropped
   *  because they were trained for a different Flux variant than the
   *  active model (e.g. Flux 2 Dev LoRA on Klein 9B). The chat renders
   *  these inline so users see why some selected LoRAs weren't applied. */
  lora_warnings?: string[]
}

export type DirectorFailureCode =
  | 'director_model_unavailable'
  | 'director_model_not_ready'
  | 'director_model_terms_required'
  | 'director_role_lora_unavailable'
  | 'director_reference_unavailable'

export type DirectorFailureComponent =
  | 'video_model'
  | 'image_creator_model'
  | 'continuity_editor_model'
  | 'image_creator_lora'
  | 'continuity_editor_lora'
  | 'character_reference'
  | 'location_reference'
  | 'starting_image'

export interface DirectorComponentFailure {
  code: DirectorFailureCode
  component: DirectorFailureComponent
  message: string
  /** Zero-based selection index for a client-local multi-reference upload failure. */
  reference_index?: number
}

const DIRECTOR_FAILURE_CODES = new Set<DirectorFailureCode>([
  'director_model_unavailable',
  'director_model_not_ready',
  'director_model_terms_required',
  'director_role_lora_unavailable',
  'director_reference_unavailable',
])

const DIRECTOR_FAILURE_COMPONENTS = new Set<DirectorFailureComponent>([
  'video_model',
  'image_creator_model',
  'continuity_editor_model',
  'image_creator_lora',
  'continuity_editor_lora',
  'character_reference',
  'location_reference',
  'starting_image',
])

const DIRECTOR_COMPONENT_LABELS: Record<DirectorFailureComponent, string> = {
  video_model: 'Video model',
  image_creator_model: 'Image creator',
  continuity_editor_model: 'Continuity editor',
  image_creator_lora: 'Creator LoRA',
  continuity_editor_lora: 'Editor LoRA',
  character_reference: 'Character reference',
  location_reference: 'Location reference',
  starting_image: 'Starting image',
}

function directorFailureMessage(
  code: DirectorFailureCode,
  component: DirectorFailureComponent,
  referenceIndex?: number,
): string {
  const label = referenceIndex !== undefined
    && (component === 'character_reference' || component === 'location_reference')
    ? `${DIRECTOR_COMPONENT_LABELS[component]} ${referenceIndex + 1}`
    : DIRECTOR_COMPONENT_LABELS[component]
  if (code === 'director_model_unavailable') {
    return `${label} is unavailable in this session. Select an authorized exact model or use Maestro locally.`
  }
  if (code === 'director_model_not_ready') {
    return `${label} is not ready on this host. Complete its setup and try again.`
  }
  if (code === 'director_model_terms_required') {
    return `Review and accept the terms required for ${label.toLowerCase()}, then try again.`
  }
  if (code === 'director_role_lora_unavailable') {
    return `${label} is unavailable or no longer matches the selected role model. Review the exact LoRA selection and try again.`
  }
  return `${label} could not be accessed. Remove or replace that reference and try again.`
}

export class DirectorRequestError extends Error {
  readonly code: DirectorFailureCode
  readonly component: DirectorFailureComponent
  readonly reference_index?: number

  constructor(code: DirectorFailureCode, component: DirectorFailureComponent, referenceIndex?: number) {
    const normalizedReferenceIndex = Number.isInteger(referenceIndex) && Number(referenceIndex) >= 0
      ? Number(referenceIndex)
      : undefined
    super(directorFailureMessage(code, component, normalizedReferenceIndex))
    this.name = 'DirectorRequestError'
    this.code = code
    this.component = component
    this.reference_index = normalizedReferenceIndex
  }
}

function directorStructuredFailure(payload: unknown): DirectorRequestError | null {
  if (!payload || typeof payload !== 'object') return null
  const candidate = payload as { code?: unknown; component?: unknown; message?: unknown }
  if (
    typeof candidate.code !== 'string'
    || !DIRECTOR_FAILURE_CODES.has(candidate.code as DirectorFailureCode)
    || typeof candidate.component !== 'string'
    || !DIRECTOR_FAILURE_COMPONENTS.has(candidate.component as DirectorFailureComponent)
    || typeof candidate.message !== 'string'
  ) return null
  // The closed code/component pair authors the visible copy. In particular,
  // model-unavailable responses deliberately do not reveal whether an exact
  // catalog ID is unknown or hidden from this session.
  return new DirectorRequestError(
    candidate.code as DirectorFailureCode,
    candidate.component as DirectorFailureComponent,
  )
}

async function throwDirectorRequestFailure(
  res: Response,
  fallback: string,
): Promise<never> {
  const payload = await res.json().catch(() => ({})) as { detail?: unknown; error?: unknown }
  const structured = directorStructuredFailure(payload)
  if (structured) throw structured
  const detail = typeof payload.detail === 'string'
    ? payload.detail
    : typeof payload.error === 'string'
      ? payload.error
      : ''
  if (res.status === 404 && (!detail || detail === 'Not Found')) {
    throw new Error('Director is not available in the running Maestro backend. Restart Maestro and try again.')
  }
  if (res.status === 401 || res.status === 403) {
    throw new Error(`Director access was denied${detail ? `: ${detail}` : '.'}`)
  }
  if (res.status === 423) {
    throw new Error(`Unlock the selected Director project first${detail ? `: ${detail}` : '.'}`)
  }
  if (res.status === 404 && /unauthorized media|(?:character|location|starting image|reference|media).*(?:not found|unavailable)|(?:not found|unavailable).*(?:reference|media)/i.test(detail)) {
    throw new Error(`Director could not access a selected reference${detail ? `: ${detail}` : '.'}`)
  }
  if (res.status === 404 && /\bmodel\b.*\bnot found\b|\bnot found\b.*\bmodel\b/i.test(detail)) {
    throw new Error('A selected Director model is unavailable in this session. Review the exact Video, Creator, and Editor selections or use Maestro locally.')
  }
  throw new Error(detail || `${fallback} (HTTP ${res.status})`)
}

export type DirectorPipelineType = 'music_video' | 'short_film_story' | 'short_film_audio'

export interface DirectorPreflightRequest {
  pipeline_type: DirectorPipelineType
  explicit_output: boolean
  video_model: string
  image_creator_model: string | null
  image_creator_loras?: DirectorImageRoleLoraSelection[]
  continuity_editor_model: string
  continuity_editor_loras?: DirectorImageRoleLoraSelection[]
  director_resolution_preset: import('../types').ResolutionPreset
  director_aspect_ratio: import('../types').AspectRatio
  reference_presence: {
    starting_image: boolean
    character: boolean
    location: boolean
  }
}

export interface DirectorPreflightResponse {
  status: 'ready'
  resolved: {
    pipeline_type: DirectorPipelineType
    video_model: string
    image_creator_model: string
    continuity_editor_model: string
    director_resolution_preset: import('../types').ResolutionPreset
    director_aspect_ratio: import('../types').AspectRatio
    video_resolution: string
    image_resolution: string | null
  }
  components: Array<{
    component: DirectorFailureComponent
    status: 'ready' | 'not_required'
  }>
}

export async function preflightDirectorPipeline(
  params: DirectorPreflightRequest,
): Promise<DirectorPreflightResponse> {
  const res = await fetch(`${BASE}/api/v1/director/preflight`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
    cache: 'no-store',
    credentials: 'same-origin',
  })
  if (!res.ok) await throwDirectorRequestFailure(res, 'Director preflight failed')
  return res.json()
}

export async function startPipeline(params: Record<string, unknown>): Promise<{ pipeline_id: string }> {
  // Internal child-job linkage is server-authored. Only the public durable
  // preparation id may cross this client boundary.
  const publicParams = { ...params }
  delete publicParams._director_request_id
  const res = await fetch(`${BASE}/api/v1/director/pipeline/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(publicParams),
  })
  if (!res.ok) {
    await throwDirectorRequestFailure(res, 'Failed to start Director pipeline')
  }
  return res.json()
}

export async function fetchPipelineStatus(pid: string): Promise<PipelineStatus> {
  const res = await fetch(`${BASE}/api/v1/director/pipeline/${encodeURIComponent(pid)}`)
  if (!res.ok) throw new Error('Failed to fetch pipeline status')
  return res.json()
}

export async function continuePipeline(pid: string, updates?: { clip_plans?: Array<{ video_prompt: string; image_prompt: string }> }): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/director/pipeline/${encodeURIComponent(pid)}/continue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates || {}),
  })
  if (!res.ok) throw new Error('Failed to continue pipeline')
}

export async function stopPipeline(pid: string): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/director/pipeline/${encodeURIComponent(pid)}/stop`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error('Failed to stop pipeline')
}

export interface ResumePipelineResult {
  status: 'resumed' | 'paused'
  pipeline_id: string
  next_action?: 'continue'
  actions?: Array<'continue'>
}

export async function resumePipeline(pid: string, workspace: string): Promise<ResumePipelineResult> {
  const res = await fetch(`${BASE}/api/v1/director/pipeline/${encodeURIComponent(pid)}/resume?workspace=${encodeURIComponent(workspace)}`, {
    method: 'POST',
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: 'Failed to resume pipeline' }))
    throw new Error(body.detail || 'Failed to resume pipeline')
  }
  return res.json()
}

// ── Recipes ──────────────────────────────────────────────────────────────

export interface RecipeLora {
  filename: string
  multiplier: string | number
  source_url?: string
  size_mb?: number
}

export interface RecipeCard {
  id: string
  name: string
  description: string
  mode: string
  model_type: string
  lora_count: number
  prompt_example: string
  nsfw: boolean
  source: 'bundled' | 'user'
  thumbnail_url: string | null
}

export interface Recipe extends RecipeCard {
  loras: RecipeLora[]
  params: Record<string, unknown>
}

function recipeUrl(path: string, workspace: string): string {
  return `${BASE}${path}?workspace=${encodeURIComponent(workspace)}`
}

async function recipeRequestError(res: Response, fallback: string): Promise<Error> {
  const body = await res.json().catch(() => ({ detail: fallback }))
  return new Error(body.detail || fallback)
}

export async function fetchRecipes(workspace: string): Promise<{ recipes: RecipeCard[] }> {
  const res = await fetch(recipeUrl('/api/v1/recipes', workspace), { cache: 'no-store' })
  if (!res.ok) throw await recipeRequestError(res, 'Failed to load recipes')
  return res.json()
}

export async function fetchRecipe(id: string, workspace: string): Promise<Recipe> {
  const res = await fetch(recipeUrl(`/api/v1/recipes/${encodeURIComponent(id)}`, workspace), { cache: 'no-store' })
  if (!res.ok) throw await recipeRequestError(res, 'Recipe not found')
  return res.json()
}

export async function saveRecipeFromOutput(body: {
  output_name: string; workspace: string; name: string; description?: string; nsfw?: boolean
}): Promise<RecipeCard> {
  const res = await fetch(`${BASE}/api/v1/recipes/save-from-output`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Save failed' }))
    throw new Error(err.detail || 'Save failed')
  }
  return res.json()
}

export async function importRecipe(recipe: Record<string, unknown>): Promise<RecipeCard> {
  const res = await fetch(`${BASE}/api/v1/recipes/import`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(recipe),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Import failed' }))
    throw new Error(err.detail || 'Import failed')
  }
  return res.json()
}

export async function deleteRecipe(id: string): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/recipes/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Delete failed' }))
    throw new Error(err.detail || 'Delete failed')
  }
}

// ── System preflight ─────────────────────────────────────────────────────

export interface PreflightCheck {
  id: string
  level: 'error' | 'warn'
  message: string
}

export async function fetchPreflight(): Promise<{ ok: boolean; checks: PreflightCheck[] }> {
  const res = await fetch(`${BASE}/api/v1/system/preflight`)
  if (!res.ok) throw new Error('preflight failed')
  return res.json()
}

// ── Director Pipeline Dashboard ──────────────────────────────────────────

export async function fetchPipelineList(workspace: string): Promise<{ pipelines: import('../types').PipelineListItem[] }> {
  const res = await fetch(`${BASE}/api/v1/director/pipelines?workspace=${encodeURIComponent(workspace)}`)
  if (!res.ok) throw new Error('Failed to fetch pipelines')
  return res.json()
}

export async function fetchSavedPipeline(pid: string, workspace: string): Promise<import('../types').SavedPipelineState> {
  const res = await fetch(`${BASE}/api/v1/director/pipelines/${encodeURIComponent(pid)}?workspace=${encodeURIComponent(workspace)}`, {
    cache: 'no-store',
  })
  if (!res.ok) throw new Error('Pipeline not found')
  return res.json()
}

export async function tagPipelineClip(pid: string, clipIndex: number, tag: string | null, workspace: string): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/director/pipelines/${encodeURIComponent(pid)}/clips/${clipIndex}/tag`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tag, workspace }),
  })
  if (!res.ok) throw new Error('Failed to tag clip')
}

export async function startPipelineRepair(pid: string, workspace: string): Promise<{
  pipeline_id: string
  repair: import('../types').PipelineRepairState
}> {
  const res = await fetch(`${BASE}/api/v1/director/pipelines/${encodeURIComponent(pid)}/repair?workspace=${encodeURIComponent(workspace)}`, {
    method: 'POST',
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Repair failed to start' }))
    throw new Error(err.error || err.detail || 'Repair failed to start')
  }
  return res.json()
}

export async function cancelPipelineRepair(pid: string, workspace: string): Promise<{
  pipeline_id: string
  repair: import('../types').PipelineRepairState
}> {
  const res = await fetch(`${BASE}/api/v1/director/pipelines/${encodeURIComponent(pid)}/repair/cancel?workspace=${encodeURIComponent(workspace)}`, {
    method: 'POST',
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Repair cancel failed' }))
    throw new Error(err.error || err.detail || 'Repair cancel failed')
  }
  return res.json()
}

export async function rerunClipImage(pid: string, clipIndex: number, workspace: string, prompt?: string): Promise<{ filename: string; clip_index: number }> {
  const res = await fetch(`${BASE}/api/v1/director/pipelines/${encodeURIComponent(pid)}/clips/${clipIndex}/rerun-image`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: prompt || undefined, workspace }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Re-run failed' }))
    throw new Error(err.error || 'Re-run image failed')
  }
  return res.json()
}

export async function rerunClipVideo(pid: string, clipIndex: number, workspace: string, prompt?: string): Promise<{ filename: string; clip_index: number }> {
  const res = await fetch(`${BASE}/api/v1/director/pipelines/${encodeURIComponent(pid)}/clips/${clipIndex}/rerun-video`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: prompt || undefined, workspace }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Re-run failed' }))
    throw new Error(err.error || 'Re-run video failed')
  }
  return res.json()
}

export async function rejoinPipeline(pid: string, workspace: string): Promise<{ filename: string }> {
  const res = await fetch(`${BASE}/api/v1/director/pipelines/${encodeURIComponent(pid)}/rejoin?workspace=${encodeURIComponent(workspace)}`, {
    method: 'POST',
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Rejoin failed' }))
    throw new Error(err.error || 'Rejoin failed')
  }
  return res.json()
}

export async function deletePipeline(pid: string, workspace: string): Promise<{ media_deleted: number; media_deferred: number }> {
  const res = await fetch(`${BASE}/api/v1/director/pipelines/${encodeURIComponent(pid)}?workspace=${encodeURIComponent(workspace)}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Delete failed' }))
    throw new Error(err.detail || 'Delete failed')
  }
  return res.json()
}

// --- Director v2 ---

export interface DirectorV2PlanRequest {
  request_id: string
  project_instance: string
  workspace: string
  skill_type: string
  video_model?: string
  /** Null is the explicit new-role automatic-creator sentinel. */
  image_creator_model?: string | null
  image_editor_model?: string
  image_creator_loras?: DirectorImageRoleLoraSelection[]
  image_editor_loras?: DirectorImageRoleLoraSelection[]
  /** Legacy combined image wire; never mix with the role fields above. */
  image_model?: string
  explicit_output?: boolean
  scene_description?: string
  story_description?: string
  clips?: unknown[]
  lyrics?: unknown[]
  bpm?: number
  reference_image_path?: string
  character_ref_paths?: string[]
  character_ref_labels?: string[]
  location_ref_paths?: string[]
  location_ref_labels?: string[]
  speaker_mappings?: Record<string, unknown>
  characters?: Array<{ name: string; description: string }>
  audio_path?: string
  target_duration?: number
  target_scenes?: number
  narrative_mode?: boolean
  fps?: number
  frames_steps?: number
  frames_minimum?: number
  concept?: string
  visual_style?: string
  /** Exact server-catalog ID only; workflow metadata remains server-owned. */
  h3_style_workflow?: string
  platform?: string
  style?: string
  prompt_type?: string
  director_flags?: Record<string, boolean>
}

export type DirectorReadinessReason =
  | 'director_incompatible'
  | 'manual_verification_required'
  | 'model_disabled'
  | 'model_not_downloaded'
  | 'model_terms_required'
  | 'model_unavailable'

export type DirectorReadinessAction =
  | 'accept_terms'
  | 'download_model'
  | 'enable_model'
  | 'select_model'
  | 'verify_manual_checkpoint'

export interface DirectorImageRoleCandidate {
  model_type: string
  compatible: boolean
  ready: boolean
  reasons: DirectorReadinessReason[]
  actions: DirectorReadinessAction[]
  enabled: boolean
  downloaded: boolean
}

export interface DirectorImageRoleCapability {
  /** Null when a remote allowlist intentionally hides the server default ID. */
  resolved_model: string | null
  selection_source:
    | 'verified_manual_preference'
    | 'safe_fallback'
    | 'fixed_default'
  candidates: DirectorImageRoleCandidate[]
  lora_catalog_endpoint: '/api/v1/loras/{model_type}/details'
}

export interface DirectorCapabilities {
  schema_version: 1
  readiness_reason_values: DirectorReadinessReason[]
  readiness_action_values: DirectorReadinessAction[]
  image_roles: {
    creator: DirectorImageRoleCapability
    editor: DirectorImageRoleCapability
  }
}

export async function fetchDirectorCapabilities(
  explicitOutput = false,
): Promise<DirectorCapabilities> {
  const query = new URLSearchParams({ explicit_output: String(explicitOutput) })
  const res = await fetch(`${BASE}/api/v1/director/capabilities?${query}`, {
    cache: 'no-store',
    credentials: 'same-origin',
  })
  if (!res.ok) {
    const payload = await res.json().catch(() => ({})) as { detail?: unknown }
    const detail = typeof payload.detail === 'string' ? payload.detail : ''
    if (res.status === 401 || res.status === 403) {
      throw new Error(detail || 'Director image capabilities are not authorized for this session.')
    }
    throw new Error(detail || 'Director image capabilities are unavailable.')
  }
  return res.json()
}

export interface DirectorV2PlanResponse {
  clip_plans: Array<{ video_prompt: string; image_prompt: string }>
  production_plan: Record<string, unknown>
  skill_type: string
}

export interface DirectorV2OperationScope {
  requestId: string
  workspace: string
  projectInstance: string
}

export interface DirectorV2OperationStatus {
  request_id: string
  operation_kind: 'director_preview'
  status: 'running' | 'completed' | 'failed' | 'cancelled'
  phase: string
  stage: string
  pass: number
  pass_limit: number
  attempt: number
  attempt_limit: number
  partial_text: string
  generated_tokens_approx: number
  elapsed_seconds: number
  live_tps: number | null
  average_tps: number | null
  result_available: boolean
  retryable: boolean
  error?: { code: string; message: string; retryable: boolean } | null
}

export interface DirectorV2RequestOptions extends LlmRequestOptions {
  projectInstance: string
  onOperationStatus?: (status: DirectorV2OperationStatus) => void
  onSubmissionAttempted?: () => void | Promise<void>
  onAdmissionConfirmed?: (status: DirectorV2OperationStatus) => void | Promise<void>
}

export class DirectorV2WaitError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'DirectorV2WaitError'
  }
}

export class DirectorV2ScopeError extends Error {
  constructor(message = 'The Director preview no longer matches this project') {
    super(message)
    this.name = 'DirectorV2ScopeError'
  }
}

const DIRECTOR_V2_CANCEL_ADMISSION_WAIT_MS = 15_000

function assertDirectorV2StatusScope(
  status: DirectorV2OperationStatus,
  scope: DirectorV2OperationScope,
): void {
  if (
    canonicalLlmRequestId(status.request_id) !== canonicalLlmRequestId(scope.requestId)
    || status.operation_kind !== 'director_preview'
  ) {
    throw new DirectorV2ScopeError()
  }
}

async function assertDirectorV2ProjectScope(
  scope: DirectorV2OperationScope,
  signal?: AbortSignal,
): Promise<void> {
  throwIfAborted(signal)
  const current = await fetchLlmModels(scope.workspace, signal)
  if (!current.project_instance || current.project_instance !== scope.projectInstance) {
    throw new DirectorV2ScopeError()
  }
}

async function submitDirectorV2Plan(
  request: DirectorV2PlanRequest,
  signal?: AbortSignal,
): Promise<DirectorV2OperationStatus> {
  const res = await fetch(`${BASE}/api/v1/director/v2/plan`, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  })
  if (isTransientHttpStatus(res.status)) throw new TransientHttpError()
  if (!res.ok || res.status !== 202) {
    const err = await res.json().catch(() => ({ detail: 'Plan failed' }))
    throw new Error(err.detail || 'Director v2 plan failed')
  }
  return res.json()
}

export async function fetchDirectorV2Operation(
  scope: DirectorV2OperationScope,
  signal?: AbortSignal,
): Promise<DirectorV2OperationStatus | null> {
  throwIfAborted(signal)
  const query = new URLSearchParams({ workspace: scope.workspace })
  const res = await fetch(
    `${BASE}/api/v1/llm/operations/director_preview/${encodeURIComponent(scope.requestId)}?${query}`,
    { cache: 'no-store', signal },
  )
  if (res.status === 404) return null
  if (isTransientHttpStatus(res.status)) throw new TransientHttpError()
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Director preview status is unavailable' }))
    throw new Error(err.detail || 'Director preview status is unavailable')
  }
  const status = await res.json() as DirectorV2OperationStatus
  assertDirectorV2StatusScope(status, scope)
  return status
}

async function fetchDirectorV2Result(
  scope: DirectorV2OperationScope,
  signal?: AbortSignal,
): Promise<DirectorV2PlanResponse> {
  await assertDirectorV2ProjectScope(scope, signal)
  const query = new URLSearchParams({ workspace: scope.workspace })
  const res = await fetch(
    `${BASE}/api/v1/llm/operations/director_preview/${encodeURIComponent(scope.requestId)}/result?${query}`,
    { cache: 'no-store', signal },
  )
  if (res.status === 404) {
    throw new DirectorV2WaitError('This Director preview result is no longer available.')
  }
  if (isTransientHttpStatus(res.status)) throw new TransientHttpError()
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Director preview result is unavailable' }))
    throw new Error(err.detail || 'Director preview result is unavailable')
  }
  const result = await res.json() as Partial<DirectorV2PlanResponse>
  await assertDirectorV2ProjectScope(scope, signal)
  if (
    !Array.isArray(result.clip_plans)
    || !result.clip_plans.every(plan => (
      Boolean(plan)
      && typeof plan === 'object'
      && !Array.isArray(plan)
      && (plan.video_prompt === undefined || typeof plan.video_prompt === 'string')
      && (plan.image_prompt === undefined || typeof plan.image_prompt === 'string')
    ))
    || !result.production_plan
    || typeof result.production_plan !== 'object'
    || Array.isArray(result.production_plan)
    || typeof result.skill_type !== 'string'
  ) {
    throw new DirectorV2ScopeError('The Director preview result did not match its request')
  }
  return result as DirectorV2PlanResponse
}

async function recoverDirectorV2Submission(
  request: DirectorV2PlanRequest,
  scope: DirectorV2OperationScope,
  signal?: AbortSignal,
): Promise<DirectorV2OperationStatus> {
  const startedAt = Date.now()
  while (Date.now() - startedAt < LLM_PREPARATION_MAX_WAIT_MS) {
    throwIfAborted(signal)
    try {
      const existing = await fetchDirectorV2Operation(scope, signal)
      if (existing) return existing
      await assertDirectorV2ProjectScope(scope, signal)
      const submitted = await submitDirectorV2Plan(request, signal)
      assertDirectorV2StatusScope(submitted, scope)
      return submitted
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
      await waitForPreparationPoll(signal)
    }
  }
  throw new DirectorV2WaitError(
    'Director preview status is still unavailable. Reload to resume waiting.',
  )
}

export async function waitForDirectorV2Operation(
  scope: DirectorV2OperationScope,
  signal?: AbortSignal,
  initial?: DirectorV2OperationStatus,
  onStatus?: (status: DirectorV2OperationStatus) => void,
): Promise<DirectorV2PlanResponse> {
  const startedAt = Date.now()
  let operation: DirectorV2OperationStatus | null | undefined = initial
  while (!operation) {
    if (Date.now() - startedAt >= LLM_PREPARATION_MAX_WAIT_MS) {
      throw new DirectorV2WaitError(
        'Director preview is still running. Reload to resume waiting.',
      )
    }
    try {
      operation = await fetchDirectorV2Operation(scope, signal)
      if (!operation) {
        throw new DirectorV2WaitError('This Director preview request is no longer available.')
      }
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
      await waitForPreparationPoll(signal)
    }
  }
  assertDirectorV2StatusScope(operation, scope)
  onStatus?.(operation)
  while (operation.status === 'running') {
    if (Date.now() - startedAt >= LLM_PREPARATION_MAX_WAIT_MS) {
      throw new DirectorV2WaitError(
        'Director preview is still running. Reload to resume waiting.',
      )
    }
    await waitForPreparationPoll(signal)
    try {
      const next = await fetchDirectorV2Operation(scope, signal)
      if (!next) {
        throw new DirectorV2WaitError('This Director preview request is no longer available.')
      }
      operation = next
      onStatus?.(operation)
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
    }
  }
  if (operation.status !== 'completed' || !operation.result_available) {
    throw new Error(
      operation.error?.message
      || (operation.status === 'cancelled'
        ? 'Director preview was cancelled'
        : 'Director preview failed'),
    )
  }
  while (Date.now() - startedAt < LLM_PREPARATION_MAX_WAIT_MS) {
    try {
      return await fetchDirectorV2Result(scope, signal)
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
      await waitForPreparationPoll(signal)
    }
  }
  throw new DirectorV2WaitError(
    'Director preview finished, but its result is still unreachable. Reload to resume waiting.',
  )
}

export async function resumeDirectorV2Plan(
  scope: DirectorV2OperationScope,
  options?: Pick<DirectorV2RequestOptions, 'signal' | 'onOperationStatus'>,
): Promise<DirectorV2PlanResponse> {
  await assertDirectorV2ProjectScope(scope, options?.signal)
  return waitForDirectorV2Operation(
    scope,
    options?.signal,
    undefined,
    options?.onOperationStatus,
  )
}

export async function cancelDirectorV2Plan(
  scope: DirectorV2OperationScope,
  signal?: AbortSignal,
): Promise<DirectorV2OperationStatus> {
  await assertDirectorV2ProjectScope(scope, signal)
  const query = new URLSearchParams({ workspace: scope.workspace })
  const operationUrl = `${BASE}/api/v1/llm/operations/director_preview/${encodeURIComponent(scope.requestId)}?${query}`
  const startedAt = Date.now()
  while (true) {
    throwIfAborted(signal)
    const res = await fetch(operationUrl, {
      method: 'DELETE', cache: 'no-store', signal,
    })
    if (res.status !== 404) {
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Director preview could not be cancelled' }))
        throw new Error(err.detail || 'Director preview could not be cancelled')
      }
      const status = await res.json() as DirectorV2OperationStatus
      assertDirectorV2StatusScope(status, scope)
      return status
    }
    let admitted: DirectorV2OperationStatus | null = null
    try {
      admitted = await fetchDirectorV2Operation(scope, signal)
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
    }
    if (admitted && admitted.status !== 'running') return admitted
    if (Date.now() - startedAt >= DIRECTOR_V2_CANCEL_ADMISSION_WAIT_MS) {
      throw new DirectorV2WaitError(
        'Director preview cancellation is still confirming. Reload to resume or cancel again.',
      )
    }
    await waitForLlmMutationRetry(signal)
  }
}

export async function directorV2Plan(
  params: DirectorV2PlanRequest,
  options: DirectorV2RequestOptions,
): Promise<DirectorV2PlanResponse> {
  const scope: DirectorV2OperationScope = {
    requestId: params.request_id,
    workspace: params.workspace,
    projectInstance: params.project_instance,
  }
  if (options.projectInstance !== params.project_instance) {
    throw new DirectorV2ScopeError('The Director preview project fence did not match its request')
  }
  await prepareLlmForRequest(
    { workspace: params.workspace, purpose: 'configured' },
    options,
  )
  await assertDirectorV2ProjectScope(scope, options.signal)
  throwIfAborted(options.signal)
  await options.onSubmissionAttempted?.()
  throwIfAborted(options.signal)
  let operation: DirectorV2OperationStatus
  try {
    operation = await submitDirectorV2Plan(params, options.signal)
    assertDirectorV2StatusScope(operation, scope)
  } catch (error) {
    throwIfAborted(options.signal)
    if (!isTransientRequestError(error)) throw error
    operation = await recoverDirectorV2Submission(params, scope, options.signal)
  }
  await options.onAdmissionConfirmed?.(operation)
  throwIfAborted(options.signal)
  return waitForDirectorV2Operation(
    scope,
    options.signal,
    operation,
    options.onOperationStatus,
  )
}

// --- Presets ---

export interface GenerationPreset {
  id: string
  name: string
  mode: string
  model_type: string
  prompt: string
  activated_loras: string[]
  loras_multipliers: string
  lora_weights: Record<string, number[]>
  params: Record<string, unknown>
  created_at: number
}

export async function fetchPresets(): Promise<{ presets: GenerationPreset[] }> {
  const res = await fetch(`${BASE}/api/v1/presets`)
  if (!res.ok) throw new Error('Failed to fetch presets')
  return res.json()
}

export async function createPreset(preset: Omit<GenerationPreset, 'id' | 'created_at'>): Promise<GenerationPreset> {
  const res = await fetch(`${BASE}/api/v1/presets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(preset),
  })
  if (!res.ok) throw new Error('Failed to create preset')
  return res.json()
}

export async function deletePreset(id: string): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/presets/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete preset')
}

// --- LoRAs ---

export async function fetchLoras(modelType: string): Promise<{ loras: string[]; guidance_max_phases: number }> {
  const res = await fetch(`${BASE}/api/v1/loras/${encodeURIComponent(modelType)}`)
  if (!res.ok) throw new Error('Failed to fetch loras')
  return res.json()
}

// --- Model Options ---

export async function fetchModelOptions(modelType: string): Promise<import('../types').ModelOptions> {
  const res = await fetch(`${BASE}/api/v1/model-options/${encodeURIComponent(modelType)}`)
  if (!res.ok) throw new Error('Failed to fetch model options')
  return res.json()
}

export async function estimateH3Performance(
  params: import('../types').H3EstimateRequest,
): Promise<import('../types').H3EstimateResponse> {
  const res = await fetch(`${BASE}/api/v1/h3/estimate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Could not estimate H3 performance' }))
    throw new Error(error.detail || 'Could not estimate H3 performance')
  }
  return res.json()
}

// --- Retake ---

export async function submitRetake(params: {
  video_path: string; start_time: number; end_time: number;
  prompt: string; model_type: string;
  negative_prompt?: string; seed?: number; guidance_scale?: number;
  num_inference_steps?: number; retake_strength?: number; workspace?: string;
  retake_engine?: string; regenerate_audio?: boolean; resolution?: string;
  activated_loras?: string[]; loras_multipliers?: string;
  private_output?: boolean; explicit_output?: boolean;
}): Promise<{ job_id: string; status: string; retake_frames: string }> {
  const res = await fetch(`${BASE}/api/v1/retake`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Retake failed' }))
    throw new Error(err.detail || 'Retake failed')
  }
  return res.json()
}

// --- Inpaint ---

export async function segmentPreview(params: {
  video_path: string; text: string; frame_index?: number;
  start_time?: number; end_time?: number;
  full_video?: boolean; invert_mask?: boolean;
}): Promise<{ mask_preview: string; target: string; frame_index: number; masks_path?: string; prompt?: string; negative_prompt?: string }> {
  const res = await fetch(`${BASE}/api/v1/segment/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Segmentation failed' }))
    throw new Error(err.detail || 'Segmentation failed')
  }
  return res.json()
}

export async function submitInpaint(params: {
  video_path: string; description: string;
  sam_target?: string; invert_mask?: boolean;
  start_time?: number; end_time?: number;
  model_type: string; retake_strength?: number; resolution?: string;
  activated_loras?: string[]; loras_multipliers?: string;
  seed?: number; guidance_scale?: number;
  num_inference_steps?: number; negative_prompt?: string;
  mask_padding?: number; workspace?: string;
  masks_path?: string; stage2_steps?: number;
  private_output?: boolean; explicit_output?: boolean;
}): Promise<{ job_id: string; status: string }> {
  const res = await fetch(`${BASE}/api/v1/inpaint`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Inpaint failed' }))
    throw new Error(err.detail || 'Inpaint failed')
  }
  return res.json()
}

// --- Edit Anything ---
//
// Prompt-driven video edit using the Alissonerdx Edit Anything LoRA
// (https://huggingface.co/Alissonerdx/LTX-LoRAs). No mask required —
// the LoRA interprets Add/Remove/Replace/Style prompts directly.

export async function submitEditAnything(params: {
  video_path: string;
  prompt: string;
  model_type: string;
  start_time?: number;
  end_time?: number;
  /** LoRA strength (default 1.0, try 1.2 if edit is too weak). */
  lora_strength?: number;
  /** Retake strength — how much of the source latent structure is kept.
   *  Default 1.0 (full regen). Lower (0.5-0.8) preserves more of the
   *  original composition. */
  retake_strength?: number;
  negative_prompt?: string;
  seed?: number;
  guidance_scale?: number;
  num_inference_steps?: number;
  activated_loras?: string[];
  loras_multipliers?: string;
  workspace?: string;
  private_output?: boolean;
  explicit_output?: boolean;
}): Promise<{
  job_id: string;
  status: string;
  edit_range?: string;
  lora_filename?: string;
}> {
  const res = await fetch(`${BASE}/api/v1/edit-anything`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Edit Anything failed' }))
    throw new Error(err.detail || 'Edit Anything failed')
  }
  return res.json()
}

// --- Repaint (SCAIL-2 Animate: edited first frame + source motion) ---

export interface RepaintRegionRequest {
  id?: string;
  /** Person/object phrase to track through the source video. */
  source: string;
  /** Corresponding person/object phrase in the edited first frame. */
  target: string;
}

export async function submitRepaint(params: {
  video_path: string;
  target_frame_path: string;
  region_mappings?: RepaintRegionRequest[];
  prompt?: string;
  start_time?: number;
  end_time?: number;
  model_type?: string;
  negative_prompt?: string;
  seed?: number;
  num_inference_steps?: number;
  /** SCAIL-2 HQ only. Fast is CFG-distilled and stays at 1. */
  guidance_scale?: number;
  resolution_profile?: ScailResolutionProfile;
  activated_loras?: string[];
  loras_multipliers?: string;
  workspace?: string;
  private_output?: boolean;
  explicit_output?: boolean;
}): Promise<{
  job_id: string;
  status: string;
  frames?: number;
  region_count?: number;
  resolution_profile?: ScailResolutionProfile;
  resolution?: string;
  sliding_window_size?: number;
  num_inference_steps?: number;
  guidance_scale?: number;
}> {
  const res = await fetch(`${BASE}/api/v1/repaint`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Repaint failed' }))
    throw new Error(err.detail || 'Repaint failed')
  }
  return res.json()
}

export async function repaintPreview(params: {
  video_path: string;
  target_frame_path: string;
  region_mappings: RepaintRegionRequest[];
  time?: number;
  workspace?: string;
}): Promise<{
  found: boolean;
  frame_index: number;
  source_preview: string;
  target_preview: string;
  mapping_results: Array<{
    mapping_index: number;
    source: string;
    target: string;
    source_found: boolean;
    target_found: boolean;
    color: number[];
  }>;
}> {
  const res = await fetch(`${BASE}/api/v1/repaint/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Repaint preview failed' }))
    throw new Error(err.detail || 'Repaint preview failed')
  }
  return res.json()
}

// --- Recast (SCAIL-2 Replace: swap a person for a reference character) ---

export async function submitRecast(params: {
  video_path: string;
  ref_image_path?: string;
  /** Same-character views for the legacy single-mapping request. */
  additional_ref_image_paths?: string[];
  /** Deterministic source-person → replacement-reference assignments. */
  character_mappings?: Array<{
    id?: string;
    target: string;
    ref_image_path: string;
    additional_ref_image_paths?: string[];
    reference_aligned_to_source?: boolean;
  }>;
  /** Who to replace, as a SAM3 keyword ("woman", "man in red"). */
  target?: string;
  /** Number of matching people to track and replace (1-5). */
  person_count?: number;
  /** The reference is an edited copy of the selected source first frame. */
  reference_aligned_to_source?: boolean;
  /** Preserve original subject identity while neutralizing reference scenery. */
  isolate_reference?: boolean;
  /** Derive a tighter same-character identity view when none is supplied. */
  auto_face_detail?: boolean;
  /** Rewrite and append Maestro's identity/scene continuity guidance. */
  enhance_prompt?: boolean;
  /** Strict post-composite fallback; may create visible lighting/color seams. */
  protect_bystanders?: boolean;
  /** Experimental: preserve other visible identities with native SCAIL-2 color correspondence. */
  preserve_bystanders?: boolean;
  /** Apply the official SCAIL-2 replacement Relighting LoRA. */
  use_relighting?: boolean;
  /** Spatial quality only; does not select a model or change its step schedule. */
  resolution_profile?: ScailResolutionProfile;
  /** Optional scene/character description — a good one helps identity. */
  prompt?: string;
  start_time?: number;
  end_time?: number;
  model_type?: string;
  negative_prompt?: string;
  seed?: number;
  num_inference_steps?: number;
  guidance_scale?: number;
  activated_loras?: string[];
  loras_multipliers?: string;
  workspace?: string;
  private_output?: boolean;
  explicit_output?: boolean;
}): Promise<{
  job_id: string;
  status: string;
  frames?: number;
  target?: string;
  person_count?: number;
  resolution_profile?: ScailResolutionProfile;
  resolution?: string;
  sliding_window_size?: number;
  num_inference_steps?: number;
  guidance_scale?: number;
}> {
  const res = await fetch(`${BASE}/api/v1/recast`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Recast failed' }))
    throw new Error(err.detail || 'Recast failed')
  }
  return res.json()
}

export async function recastPreview(params: {
  video_path: string;
  target?: string;
  person_count?: number;
  ref_image_path?: string;
  additional_ref_image_paths?: string[];
  character_mappings?: Array<{
    id?: string;
    target: string;
    ref_image_path?: string;
    additional_ref_image_paths?: string[];
    reference_aligned_to_source?: boolean;
  }>;
  isolate_reference?: boolean;
  auto_face_detail?: boolean;
  resolution_profile?: ScailResolutionProfile;
  time?: number;
  end_time?: number;
  workspace?: string;
}): Promise<{
  found: boolean;
  matched_people: number;
  requested_people: number;
  frame_index: number;
  time_seconds?: number;
  timeline_start_seconds?: number;
  timeline_end_seconds?: number;
  sampled_frame_count?: number;
  preview: string;
  resolution_profile?: ScailResolutionProfile;
  output_resolution?: number[];
  mapping_results?: Array<{
    mapping_index: number;
    target: string;
    found: boolean;
    color: number[];
    overlap_fraction: number;
    first_frame_index?: number | null;
    first_time_seconds?: number | null;
    anchor_frame_index?: number | null;
    anchor_time_seconds?: number | null;
  }>;
  reference_previews?: Array<{
    mapping_index: number;
    view_index: number;
    kind: 'primary' | 'additional' | 'auto_face_detail';
    mask_source: string;
    source_size: number[];
    prepared_size: number[];
    crop_box?: number[];
    detail_size?: number[];
    detail_source?: string;
    prepared_image: string;
    clip_identity_image?: string;
    semantic_mask: string;
  }>;
}> {
  const res = await fetch(`${BASE}/api/v1/recast/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Preview failed' }))
    throw new Error(err.detail || 'Preview failed')
  }
  return res.json()
}

// --- Outpaint ---

export async function submitOutpaint(params: {
  video_path: string; prompt: string; model_type: string;
  pad_top?: number; pad_bottom?: number; pad_left?: number; pad_right?: number;
  outpaint_aspect?: 'source' | '16:9' | '9:16' | '1:1' | '4:3' | '3:4';
  resolution_preset?: 'auto' | '480p' | '540p' | '720p' | '1080p';
  source_preservation?: number;
  outpaint_lora_strength?: number;
  mask_preserving_outpaint?: boolean;
  num_inference_steps?: number;
  guidance_scale?: number;
  negative_prompt?: string;
  seed?: number;
  activated_loras?: string[]; loras_multipliers?: string;
  workspace?: string;
  private_output?: boolean; explicit_output?: boolean;
  // Optional outpaint refinement controls.
  preserve_source_audio?: boolean;
  lock_source_pixels?: boolean;
  trim_window_smear?: boolean;
  sliding_window_size?: number;
  sliding_window_overlap?: number;
  start_time?: number;
  end_time?: number;
}): Promise<{ job_id: string; status: string }> {
  const res = await fetch(`${BASE}/api/v1/outpaint`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Outpaint failed' }))
    throw new Error(err.detail || 'Outpaint failed')
  }
  return res.json()
}

// --- Blend ---

export async function submitBlend(params: {
  clip_a_path: string; clip_b_path: string;
  prompt?: string;
  model_type: string;
  blend_mode?: 'insert' | 'overlap'; overlap_sec?: number;
  seed?: number; activated_loras?: string[]; loras_multipliers?: string;
  workspace?: string;
  private_output?: boolean; explicit_output?: boolean;
  // Studio params inherited by the blend (progressive_pipeline,
  // num_inference_steps, guidance_scale, negative_prompt, etc.). Blend-
  // specific fields are overridden server-side.
  base_params?: Record<string, unknown>;
  // Blend-specific tuning overrides (take precedence over base_params)
  /** Seconds of A's overlap-zone start used as video_source for motion
   *  continuity (VE mode). 0 = pure SE. Default 1.0. */
  motion_prefix_sec?: number;
  /** Seconds of B's overlap-zone end used as video_end for motion continuity
   *  on the B side (via _append_suffix_entries in ltx2.py). 0 = single
   *  image_end anchor. Default 1.0. */
  motion_suffix_sec?: number;
  /** Strength of the VE anchor locks (video_source + image_end).
   *  1.0 = hard lock → averaging → crossfade. 0.5-0.8 = model invents
   *  motion between anchors. Default 1.0 server-side. */
  input_video_strength?: number;
  anchor_frames?: number;
  injection_strength?: number;
  num_inference_steps?: number;
  guidance_scale?: number;
  negative_prompt?: string;
  /** @deprecated no longer used; kept for back-compat with existing call sites */
  transition_sec?: number;
  /** @deprecated bell-curve weighting is applied automatically */
  strength_a?: number;
  /** @deprecated bell-curve weighting is applied automatically */
  strength_b?: number;
  /** @deprecated superseded by anchor_frames; kept for back-compat */
  denoise_strength?: number;
}): Promise<{ job_id: string; status: string; overlap_sec?: number; frames?: number }> {
  const res = await fetch(`${BASE}/api/v1/blend`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Blend failed' }))
    throw new Error(err.detail || 'Blend failed')
  }
  return res.json()
}

/** SAM (Inpaint) service status. Status values:
 *   ready / available — service running, model loaded or loading
 *   installed         — env installed but service not started; will
 *                        auto-start on demand
 *   not_installed     — SAM env doesn't exist; user must run
 *                        "Install Inpaint Support (SAM 3.1)" from the
 *                        Pinokio menu before Inpaint will work
 *   unavailable       — generic failure (service unhealthy, network)
 */
export async function samServiceStatus(): Promise<{
  status: string
  model_loaded: boolean
  error?: string
}> {
  const res = await fetch(`${BASE}/api/v1/sam/status`)
  if (!res.ok) return { status: 'unavailable', model_loaded: false }
  return res.json()
}

// --- Audio Mix ---

export async function mixAudio(tracks: { path: string; start_time: number; volume: number }[], workspace?: string): Promise<{ filename: string; path: string }> {
  const res = await fetch(`${BASE}/api/v1/audio/mix`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tracks, workspace }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Mix failed' }))
    throw new Error(err.detail || 'Mix failed')
  }
  return res.json()
}

// --- Upload ---

export async function uploadImage(file: File): Promise<{
  filename: string
  path: string
  url: string
  fps?: number
  frame_count?: number
  duration_seconds?: number
  has_audio?: boolean
}> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/api/v1/upload`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) throw new Error('Upload failed')
  return res.json()
}

// --- Scheduled public research ---

export interface ResearchSuggestion {
  finding_id: string
  title: string
  decision: string
  summary: string
}

export interface ResearchCycleStatus {
  run_id: string | null
  status: 'completed' | 'failed'
  completed_at: string | null
  discovered: number
  analyzed: number
  provider_failures: number
  source_failures: number
  ready_for_review: number
  batch_size: number
  deepseek_disabled_reason: string | null
}

export interface ResearchImplementationStatus {
  active: boolean
  run_id: string | null
  packet_id: string | null
  started_at: string | null
  completed_at: string | null
  status: 'never_run' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted_requires_review'
  summary: string
}

export interface ResearchStatus {
  schema_version: number
  schedule_enabled: boolean
  configured_batch_size: number
  cadence: 'every_6_hours' | 'daily' | 'weekly' | null
  last_cycle_at: string | null
  last_cycle: ResearchCycleStatus | null
  next_due_at: string | null
  queued_candidate_count: number
  research_active: boolean
  research_phase: string | null
  implementation_active: boolean
  implementation_chunk_count: number
  implementation_ready: boolean
  readiness_threshold: number
  readiness_reason: string
  recent_pending: ResearchSuggestion[]
  last_implementation_run: ResearchImplementationStatus
  runtime_error: string | null
  disclosure: string
}

async function researchResponse<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: fallback }))
    throw new Error(error.detail || fallback)
  }
  return res.json()
}

export async function fetchResearchStatus(signal?: AbortSignal): Promise<ResearchStatus> {
  const res = await fetch(`${BASE}/api/v1/research/status`, {
    cache: 'no-store',
    signal,
  })
  return researchResponse<ResearchStatus>(res, 'Research status is unavailable')
}

export async function runResearchNow(): Promise<{ status: string; force: boolean }> {
  const res = await fetch(`${BASE}/api/v1/research/run`, {
    method: 'POST',
    cache: 'no-store',
  })
  return researchResponse(res, 'Research could not start')
}

export async function startResearchImplementation(
  force: boolean,
): Promise<{ status: string; force: boolean }> {
  const nonceResponse = await fetch(`${BASE}/api/v1/research/implementation/nonce`, {
    method: 'POST',
    cache: 'no-store',
  })
  const capability = await researchResponse<{ nonce: string }>(
    nonceResponse,
    'Implementation authorization could not be created',
  )
  const res = await fetch(`${BASE}/api/v1/research/implementation/run`, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ nonce: capability.nonce, force }),
  })
  return researchResponse(res, 'Implementation could not start')
}

// --- System Config ---

export async function fetchSystemConfig(): Promise<import('../types').SystemConfig> {
  const res = await fetch(`${BASE}/api/v1/system-config`)
  if (!res.ok) throw new Error('Failed to fetch system config')
  return res.json()
}

export async function scanModelFolders(): Promise<{ candidates: import('../types').ModelFolderCandidate[] }> {
  const res = await fetch(`${BASE}/api/v1/model-folders/scan`)
  if (!res.ok) throw new Error('Failed to scan for model folders')
  return res.json()
}

export async function updateSystemConfig(
  partial: Partial<import('../types').SystemConfig>,
  signal?: AbortSignal,
): Promise<{ status: string; updated: Record<string, unknown> }> {
  const res = await fetch(`${BASE}/api/v1/system-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(partial),
    signal,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Update failed' }))
    throw new Error(err.detail || 'Update failed')
  }
  return res.json()
}

// --- Performance Auto-Tune ---

/** Read the user's current hardware + the auto-tune recommendation
 *  for it. Backs the AutoPerformanceCard readout. Always succeeds —
 *  on systems without CUDA, the response includes a "no GPU detected"
 *  recommendation rather than a 500. */
export async function fetchSystemDetect(): Promise<import('../types').SystemDetectResponse> {
  const res = await fetch(`${BASE}/api/v1/system-detect`)
  if (!res.ok) throw new Error('Failed to fetch hardware detection')
  return res.json()
}

/** Live CPU / RAM / GPU + loaded-model telemetry for the hardware
 *  status indicators. Cheap enough to poll every ~2s. */
export async function fetchSystemStats(): Promise<import('../types').SystemStats> {
  const res = await fetch(`${BASE}/api/v1/system-stats`)
  if (!res.ok) throw new Error('Failed to fetch system stats')
  return res.json()
}

/** Manually unload the resident generation model (and LLM) to free
 *  VRAM/RAM. Models stay loaded between generations by design; this is
 *  the explicit opt-out. 409s when a generation or Director run is
 *  active. Returns which models were released. */
export async function releaseModels(): Promise<{ released: string[] }> {
  const res = await fetch(`${BASE}/api/v1/system/release-model`, { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unload failed' }))
    throw new Error(err.detail || 'Unload failed')
  }
  return res.json()
}

/** Apply the recommended settings to wgp_config.json. Used by both
 *  the "Re-detect" button (refreshes after hardware change) and the
 *  auto-tune toggle going from off → on. Server-side this is a single
 *  call: re-runs detection, writes recommendation, sets
 *  services.auto_performance=true, applies runtime side effects. */
export async function applySystemDetect(): Promise<import('../types').SystemDetectApplyResponse> {
  const res = await fetch(`${BASE}/api/v1/system-detect/apply`, {
    method: 'POST',
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Apply failed' }))
    throw new Error(err.detail || 'Apply failed')
  }
  return res.json()
}

// --- Services Config ---

export async function fetchServicesConfig(): Promise<import('../types').ServicesConfig> {
  const res = await fetch(`${BASE}/api/v1/services-config`)
  if (!res.ok) throw new Error('Failed to fetch services config')
  return res.json()
}

export async function updateServicesConfig(
  partial: Partial<import('../types').ServicesConfig>
): Promise<{ status: string; updated: Record<string, unknown> }> {
  const res = await fetch(`${BASE}/api/v1/services-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(partial),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Update failed' }))
    throw new Error(err.detail || 'Update failed')
  }
  return res.json()
}

export async function fetchHostTerms(
  workspace: string,
): Promise<{ terms: import('../types').HostTermsStatus }> {
  const query = new URLSearchParams({ workspace })
  const res = await fetch(`${BASE}/api/v1/host-terms?${query}`)
  if (!res.ok) {
    const message = res.status === 423
      ? 'Unlock the selected project to review host notices.'
      : res.status === 403
        ? 'This project is not authorized to review host notices.'
        : 'Failed to load host notice status.'
    throw new Error(message)
  }
  return res.json()
}

export async function acceptHostTerm(
  term: import('../types').HostTermId,
  version: number,
  workspace: string,
): Promise<{ status: string; terms: import('../types').HostTermsStatus }> {
  const res = await fetch(`${BASE}/api/v1/host-terms/accept`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ term, version, workspace }),
  })
  if (!res.ok) {
    const message = res.status === 409
      ? 'The notice changed. Reload Maestro and review the current version.'
      : res.status === 423
        ? 'Unlock the selected project before accepting this notice.'
        : res.status === 403
          ? 'This project is not authorized to accept host notices.'
          : 'Host notice acceptance failed.'
    throw new Error(message)
  }
  return res.json()
}

// --- LLM Service ---

export type LlmPreparationPurpose = 'chat' | 'enhance' | 'configured'
export type LlmPreparationPhase = 'queued' | 'loading' | 'ready' | 'failed'

export interface LlmPreparationStatus {
  operation_id: string
  status: 'preparing' | 'ready' | 'failed'
  phase: LlmPreparationPhase
  retryable: boolean
  error?: {
    code: string
    message: string
    retryable: boolean
  } | null
}

export interface LlmPreparationRequest {
  workspace: string
  purpose: LlmPreparationPurpose
  model_id?: string
  model_type?: string
  vision_required?: boolean
}

export interface LlmRequestOptions {
  signal?: AbortSignal
  onPreparationStatus?: (status: LlmPreparationStatus) => void
}

export class LlmPreparationError extends Error {
  readonly code: string
  readonly retryable: boolean

  constructor(code: string, message: string, retryable: boolean) {
    super(message)
    this.name = 'LlmPreparationError'
    this.code = code
    this.retryable = retryable
  }
}

export class LlmChatWaitError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'LlmChatWaitError'
  }
}

export class LlmEnhanceWaitError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'LlmEnhanceWaitError'
  }
}

export class LlmEnhanceScopeError extends Error {
  constructor(message = 'The Prompt Enhance project changed') {
    super(message)
    this.name = 'LlmEnhanceScopeError'
  }
}

// A 31B local model can take substantially longer to download/load than an
// HTTP proxy will keep one inference request open. Preparation uses short
// requests and permits a bounded 45-minute wait without placing creative
// content in the prepare payload.
const LLM_PREPARATION_MAX_WAIT_MS = 45 * 60 * 1000
const LLM_PREPARATION_VISIBLE_POLL_MS = 1_000
const LLM_ENHANCE_CANCEL_ADMISSION_WAIT_MS = 15_000
const LLM_ENHANCE_CANCEL_RETRY_MS = 250

class TransientHttpError extends Error {}

function isTransientHttpStatus(status: number): boolean {
  return status === 408 || (status >= 500 && status <= 599)
}

function isTransientRequestError(error: unknown): boolean {
  return error instanceof TypeError || error instanceof TransientHttpError
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException('The browser stopped waiting', 'AbortError')
}

function waitForPreparationPoll(signal?: AbortSignal): Promise<void> {
  throwIfAborted(signal)
  return new Promise((resolve, reject) => {
    let settled = false
    let timer: number | null = null
    const finish = (error?: unknown) => {
      if (settled) return
      settled = true
      if (timer !== null) window.clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibilityChange)
      }
      if (error) reject(error)
      else resolve()
    }
    const onAbort = () => finish(new DOMException('The browser stopped waiting', 'AbortError'))
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        if (timer !== null) window.clearTimeout(timer)
        timer = null
      } else {
        finish()
      }
    }
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibilityChange)
    }
    if (typeof document === 'undefined' || document.visibilityState !== 'hidden') {
      timer = window.setTimeout(() => finish(), LLM_PREPARATION_VISIBLE_POLL_MS)
    }
    signal?.addEventListener('abort', onAbort, { once: true })
    if (signal?.aborted) {
      onAbort()
    }
  })
}

function waitForLlmMutationRetry(signal?: AbortSignal): Promise<void> {
  throwIfAborted(signal)
  return new Promise((resolve, reject) => {
    const timer = globalThis.setTimeout(finish, LLM_ENHANCE_CANCEL_RETRY_MS)
    function finish(): void {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }
    function onAbort(): void {
      globalThis.clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      reject(new DOMException('The browser stopped waiting', 'AbortError'))
    }
    signal?.addEventListener('abort', onAbort, { once: true })
    if (signal?.aborted) onAbort()
  })
}

async function llmPreparationError(
  res: Response,
  fallback = 'LLM preparation failed',
): Promise<LlmPreparationError> {
  const body = await res.json().catch(() => null) as {
    detail?: string | { code?: string; message?: string; retryable?: boolean }
    error?: { code?: string; message?: string; retryable?: boolean }
    code?: string
    message?: string
    retryable?: boolean
  } | null
  const detail = body?.error || (typeof body?.detail === 'object' ? body.detail : body)
  const message = detail?.message
    || (typeof body?.detail === 'string' ? body.detail : '')
    || fallback
  return new LlmPreparationError(
    detail?.code || 'preparation_failed',
    message,
    detail?.retryable !== false,
  )
}

export async function startLlmPreparation(
  request: LlmPreparationRequest,
  signal?: AbortSignal,
): Promise<LlmPreparationStatus> {
  throwIfAborted(signal)
  const payload: LlmPreparationRequest = {
    workspace: request.workspace,
    purpose: request.purpose,
    ...(request.purpose === 'chat' && request.model_id
      ? { model_id: request.model_id }
      : {}),
    ...(request.purpose === 'enhance' && request.model_type
      ? { model_type: request.model_type }
      : {}),
    ...(request.purpose === 'enhance'
      ? { vision_required: request.vision_required === true }
      : {}),
  }
  const res = await fetch(`${BASE}/api/v1/llm/prepare`, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
  if (isTransientHttpStatus(res.status)) throw new TransientHttpError()
  if (!res.ok) throw await llmPreparationError(res)
  return res.json()
}

export async function fetchLlmPreparation(
  operationId: string,
  workspace: string,
  signal?: AbortSignal,
): Promise<LlmPreparationStatus> {
  throwIfAborted(signal)
  const query = new URLSearchParams({ workspace })
  const res = await fetch(
    `${BASE}/api/v1/llm/prepare/${encodeURIComponent(operationId)}?${query}`,
    { cache: 'no-store', signal },
  )
  if (res.status === 404) {
    throw new LlmPreparationError(
      'preparation_not_found',
      'LLM preparation expired. Preparing again.',
      true,
    )
  }
  if (isTransientHttpStatus(res.status)) throw new TransientHttpError()
  if (!res.ok) throw await llmPreparationError(res, 'LLM preparation is unavailable')
  return res.json()
}

export async function prepareLlmForRequest(
  request: LlmPreparationRequest,
  options?: LlmRequestOptions,
): Promise<LlmPreparationStatus> {
  const startedAt = Date.now()
  let status: LlmPreparationStatus | null = null
  while (true) {
    while (!status) {
      try {
        status = await startLlmPreparation(request, options?.signal)
      } catch (error) {
        throwIfAborted(options?.signal)
        if (!isTransientRequestError(error)) throw error
        if (Date.now() - startedAt >= LLM_PREPARATION_MAX_WAIT_MS) {
          throw new LlmPreparationError(
            'preparation_unavailable',
            'LLM preparation is still unreachable. Try again to resume waiting.',
            true,
          )
        }
        await waitForPreparationPoll(options?.signal)
      }
    }
    options?.onPreparationStatus?.(status)
    if (status.status === 'ready') return status
    if (status.status === 'failed') {
      throw new LlmPreparationError(
        status.error?.code || 'preparation_failed',
        status.error?.message || 'LLM preparation failed',
        status.error?.retryable ?? status.retryable,
      )
    }
    if (Date.now() - startedAt >= LLM_PREPARATION_MAX_WAIT_MS) {
      throw new LlmPreparationError(
        'preparation_timeout',
        'The LLM is still preparing. Try again to resume waiting.',
        true,
      )
    }
    await waitForPreparationPoll(options?.signal)
    try {
      status = await fetchLlmPreparation(
        status.operation_id,
        request.workspace,
        options?.signal,
      )
    } catch (error) {
      throwIfAborted(options?.signal)
      if (
        error instanceof LlmPreparationError
        && error.code === 'preparation_not_found'
      ) {
        // Ready preparations may expire or their model may unload while a tab
        // is hidden. Re-submit the same content-free selector on return.
        status = null
        continue
      }
      if (!isTransientRequestError(error)) throw error
    }
  }
}

async function withLlmPreparation<T>(
  request: LlmPreparationRequest,
  options: LlmRequestOptions | undefined,
  inference: () => Promise<T>,
): Promise<T> {
  await prepareLlmForRequest(request, options)
  throwIfAborted(options?.signal)
  return inference()
}

async function preparedConfiguredPost<T>(
  path: string,
  params: { workspace: string },
  options: LlmRequestOptions | undefined,
  fallback: string,
): Promise<T> {
  return withLlmPreparation(
    { workspace: params.workspace, purpose: 'configured' },
    options,
    async () => {
      const res = await fetch(`${BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
        signal: options?.signal,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: fallback }))
        throw new Error(err.detail || fallback)
      }
      return res.json()
    },
  )
}

export async function fetchLlmStatus(signal?: AbortSignal): Promise<import('../types').LlmStatus> {
  const res = await fetch(`${BASE}/api/v1/llm/status`, { signal })
  if (!res.ok) throw new Error('Failed to fetch LLM status')
  return res.json()
}

export async function loadLlm(
  params?: { model_id?: string; device?: string; provider?: 'local' },
  signal?: AbortSignal,
): Promise<import('../types').LlmStatus & { status: string }> {
  const res = await fetch(`${BASE}/api/v1/llm/load`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params || {}),
    signal,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Load failed' }))
    throw new Error(err.detail || 'Load failed')
  }
  return res.json()
}

export async function unloadLlm(): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/llm/unload`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to unload LLM')
}

export async function fetchLlmModels(workspace?: string, signal?: AbortSignal): Promise<{
  models: import('../types').LlmModelOption[]
  guides: import('../types').LlmPromptGuideOption[]
  project_instance?: string
}> {
  const query = workspace
    ? `?${new URLSearchParams({ workspace })}`
    : ''
  const res = await fetch(`${BASE}/api/v1/llm/models${query}`, {
    cache: 'no-store',
    signal,
  })
  if (!res.ok) throw new Error('Failed to fetch LLM models')
  const data = await res.json()
  return {
    models: data.models || [],
    guides: data.guides || [],
    project_instance: typeof data.project_instance === 'string'
      ? data.project_instance
      : undefined,
  }
}

export async function uploadLlmChatImage(
  workspace: string,
  file: File,
  signal?: AbortSignal,
): Promise<{ filename: string; url: string }> {
  const form = new FormData()
  form.append('file', file)
  const query = new URLSearchParams({ workspace })
  const res = await fetch(`${BASE}/api/v1/llm/chat-upload?${query}`, {
    method: 'POST',
    body: form,
    signal,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Image upload failed' }))
    throw new Error(err.detail || 'Image upload failed')
  }
  return res.json()
}

export async function deleteLlmChatImage(
  workspace: string,
  filename: string,
): Promise<void> {
  const query = new URLSearchParams({ workspace })
  const res = await fetch(
    `${BASE}/api/v1/llm/chat-upload/${encodeURIComponent(filename)}?${query}`,
    { method: 'DELETE' },
  )
  if (!res.ok && res.status !== 404) {
    const err = await res.json().catch(() => ({ detail: 'Image cleanup failed' }))
    throw new Error(err.detail || 'Image cleanup failed')
  }
}

export interface LlmRefusalLiteralResult {
  added: boolean
  count: number
  revision: string | number
}

export const LLM_REFUSAL_LITERAL_MAX_CODE_POINTS = 256

function isLlmRefusalWhitespace(codePoint: number): boolean {
  return codePoint === 0x09
    || codePoint === 0x0a
    || codePoint === 0x0d
    || codePoint === 0x20
    || codePoint === 0xa0
    || codePoint === 0x1680
    || (codePoint >= 0x2000 && codePoint <= 0x200a)
    || codePoint === 0x2028
    || codePoint === 0x2029
    || codePoint === 0x202f
    || codePoint === 0x205f
    || codePoint === 0x3000
}

export function validateLlmRefusalLiteral(literal: string): string | null {
  const characters = Array.from(literal)
  if (characters.length === 0) return 'Select refusal wording before continuing.'
  if (characters.length > LLM_REFUSAL_LITERAL_MAX_CODE_POINTS) {
    return `Keep refusal wording to ${LLM_REFUSAL_LITERAL_MAX_CODE_POINTS} characters or fewer.`
  }
  const codePoints = characters.map(character => character.codePointAt(0) ?? 0)
  if (codePoints.some(codePoint => (
    (codePoint <= 0x1f && codePoint !== 0x09 && codePoint !== 0x0a && codePoint !== 0x0d)
    || (codePoint >= 0x7f && codePoint <= 0x9f)
    || (codePoint >= 0xd800 && codePoint <= 0xdfff)
  ))) {
    return 'Refusal wording contains an unsupported control character.'
  }
  if (codePoints.every(isLlmRefusalWhitespace)) {
    return 'Select refusal wording before continuing.'
  }
  return null
}

export async function addLlmRefusalLiteral(
  literal: string,
  signal?: AbortSignal,
): Promise<LlmRefusalLiteralResult> {
  const validationError = validateLlmRefusalLiteral(literal)
  if (validationError) throw new Error(validationError)
  const res = await fetch(`${BASE}/api/v1/llm/refusal-literals`, {
    method: 'POST',
    cache: 'no-store',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ literal }),
    signal,
  })
  if (!res.ok) {
    // The selected wording must not be reflected through an error envelope.
    throw new Error('Could not add the selected refusal wording')
  }
  const body = await res.json() as Partial<LlmRefusalLiteralResult>
  if (
    typeof body.added !== 'boolean'
    || typeof body.count !== 'number'
    || !Number.isInteger(body.count)
    || body.count < 0
    || (typeof body.revision !== 'string' && typeof body.revision !== 'number')
  ) {
    throw new Error('The host returned an invalid refusal-wording status')
  }
  return {
    added: body.added,
    count: body.count,
    revision: body.revision,
  }
}

export interface LlmChatOperationStatus {
  request_id: string
  status: 'running' | 'completed' | 'failed'
  phase: string
  retryable: boolean
  partial_text?: string
  attempt?: number
  attempt_limit?: number
  generated_tokens_approx?: number
  elapsed_seconds?: number
  live_tps?: number | null
  average_tps?: number | null
  result?: {
    text: string
    model_id: string
    guide_ids: string[]
    generated_tokens_approx?: number
    elapsed_seconds?: number
    average_tps?: number | null
  } | null
  error?: { code: string; message: string; retryable: boolean } | null
}

export interface LlmChatResult {
  text: string
  model_id: string
  guide_ids: string[]
  generated_tokens_approx?: number
  elapsed_seconds?: number
  average_tps?: number | null
}

export function createLlmRequestId(): string {
  if (typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0'))
  return [
    hex.slice(0, 4).join(''),
    hex.slice(4, 6).join(''),
    hex.slice(6, 8).join(''),
    hex.slice(8, 10).join(''),
    hex.slice(10).join(''),
  ].join('-')
}

async function submitLlmChat(
  request: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<LlmChatOperationStatus> {
  const res = await fetch(`${BASE}/api/v1/llm/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  })
  if (isTransientHttpStatus(res.status)) throw new TransientHttpError()
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Chat request failed' }))
    throw new Error(err.detail || 'Chat request failed')
  }
  return res.json()
}

export async function fetchLlmChatOperation(
  requestId: string,
  workspace: string,
  signal?: AbortSignal,
): Promise<LlmChatOperationStatus | null> {
  const query = new URLSearchParams({ workspace })
  const res = await fetch(
    `${BASE}/api/v1/llm/chat/${encodeURIComponent(requestId)}?${query}`,
    { cache: 'no-store', signal },
  )
  if (res.status === 404) return null
  if (isTransientHttpStatus(res.status)) throw new TransientHttpError()
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Chat status is unavailable' }))
    throw new Error(err.detail || 'Chat status is unavailable')
  }
  return res.json()
}

async function recoverLlmChatSubmission(
  request: Record<string, unknown>,
  requestId: string,
  workspace: string,
  signal?: AbortSignal,
): Promise<LlmChatOperationStatus> {
  const startedAt = Date.now()
  while (Date.now() - startedAt < LLM_PREPARATION_MAX_WAIT_MS) {
    throwIfAborted(signal)
    try {
      const existing = await fetchLlmChatOperation(requestId, workspace, signal)
      if (existing) return existing
      // If the first POST never reached the host, the same UUID and exact
      // request can be re-submitted safely. A running operation coalesces.
      return await submitLlmChat(request, signal)
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
      await waitForPreparationPoll(signal)
    }
  }
  throw new LlmChatWaitError('Chat status is still unavailable. Try again to resume waiting.')
}

export async function waitForLlmChatOperation(
  requestId: string,
  workspace: string,
  signal?: AbortSignal,
  initial?: LlmChatOperationStatus,
  onStatus?: (status: LlmChatOperationStatus) => void,
): Promise<LlmChatResult> {
  const startedAt = Date.now()
  let operation: LlmChatOperationStatus | null | undefined = initial
  while (!operation) {
    if (Date.now() - startedAt >= LLM_PREPARATION_MAX_WAIT_MS) {
      throw new LlmChatWaitError('Chat status is still unavailable. Resume waiting to retrieve the result.')
    }
    try {
      operation = await fetchLlmChatOperation(requestId, workspace, signal)
      if (!operation) {
        throw new Error('This Chat result is no longer available. Retry the turn.')
      }
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
      await waitForPreparationPoll(signal)
    }
  }
  onStatus?.(operation)
  while (operation.status === 'running') {
    if (Date.now() - startedAt >= LLM_PREPARATION_MAX_WAIT_MS) {
      throw new LlmChatWaitError('Chat is still running. Resume waiting to retrieve the result.')
    }
    await waitForPreparationPoll(signal)
    try {
      const next = await fetchLlmChatOperation(requestId, workspace, signal)
      if (!next) {
        throw new Error('This Chat result is no longer available. Retry the turn.')
      }
      operation = next
      onStatus?.(operation)
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
      // A retryable proxy/status failure does not end the durable operation.
    }
  }
  if (operation.status === 'completed' && operation.result) {
    return {
      ...operation.result,
      generated_tokens_approx: operation.result.generated_tokens_approx
        ?? operation.generated_tokens_approx,
      elapsed_seconds: operation.result.elapsed_seconds ?? operation.elapsed_seconds,
      average_tps: operation.result.average_tps ?? operation.average_tps,
    }
  }
  throw new Error(operation.error?.message || 'Chat generation failed')
}

export async function llmChat(params: {
  workspace: string
  request_id: string
  model_id: string
  messages: import('../types').LlmChatMessage[]
  guide_ids: string[]
  explicit_output?: boolean
  image_paths?: string[]
  max_new_tokens?: number
}, signal?: AbortSignal, onPreparationStatus?: LlmRequestOptions['onPreparationStatus'], onOperationStatus?: (status: LlmChatOperationStatus) => void, onSubmissionAttempted?: () => void): Promise<LlmChatResult> {
  const request = {
    ...params,
    // Attachment display metadata is browser-local. Only role/content and
    // one-use upload references cross the API boundary.
    messages: params.messages.map(({ role, content }) => ({ role, content })),
  }
  await prepareLlmForRequest(
    {
      workspace: params.workspace,
      purpose: 'chat',
      model_id: params.model_id,
    },
    { signal, onPreparationStatus },
  )
  throwIfAborted(signal)
  // From this point a disconnect can hide an accepted 202, so the browser
  // must retain the request id even before it observes operation status.
  onSubmissionAttempted?.()

  let operation: LlmChatOperationStatus | null = null
  try {
    operation = await submitLlmChat(request, signal)
  } catch (error) {
    throwIfAborted(signal)
    if (!isTransientRequestError(error)) throw error
    // A transient disconnect may hide a successful 202. Query the durable
    // request id first; if it never arrived, re-submit the exact same request.
    operation = await recoverLlmChatSubmission(
      request,
      params.request_id,
      params.workspace,
      signal,
    )
  }
  return waitForLlmChatOperation(
    params.request_id,
    params.workspace,
    signal,
    operation,
    onOperationStatus,
  )
}

export interface LlmEnhanceOperationScope {
  requestId: string
  workspace: string
  projectInstance: string
}

export interface LlmEnhanceOperationStatus {
  request_id: string
  operation_kind: 'enhance'
  status: 'running' | 'completed' | 'failed' | 'cancelled'
  phase: string
  stage: string
  pass: number
  pass_limit: number
  attempt: number
  attempt_limit: number
  partial_text: string
  generated_tokens_approx: number
  elapsed_seconds: number
  live_tps: number | null
  average_tps: number | null
  result_available: boolean
  retryable: boolean
  error?: { code: string; message: string; retryable: boolean } | null
}

export interface LlmEnhanceResult {
  original: string
  enhanced: string
}

export interface LlmEnhanceRequestOptions extends LlmRequestOptions {
  projectInstance: string
  onOperationStatus?: (status: LlmEnhanceOperationStatus) => void
  onSubmissionAttempted?: () => void | Promise<void>
}

function canonicalLlmRequestId(requestId: string): string {
  return requestId.replaceAll('-', '').toLowerCase()
}

function assertLlmEnhanceStatusScope(
  status: LlmEnhanceOperationStatus,
  scope: LlmEnhanceOperationScope,
): void {
  if (
    canonicalLlmRequestId(status.request_id) !== canonicalLlmRequestId(scope.requestId)
    || status.operation_kind !== 'enhance'
  ) {
    throw new LlmEnhanceScopeError()
  }
}

async function assertLlmEnhanceProjectScope(
  scope: LlmEnhanceOperationScope,
  signal?: AbortSignal,
): Promise<void> {
  throwIfAborted(signal)
  const current = await fetchLlmModels(scope.workspace, signal)
  if (
    !current.project_instance
    || current.project_instance !== scope.projectInstance
  ) {
    throw new LlmEnhanceScopeError()
  }
}

async function submitLlmEnhance(
  request: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<LlmEnhanceOperationStatus> {
  const res = await fetch(`${BASE}/api/v1/llm/enhance-prompt`, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  })
  if (isTransientHttpStatus(res.status)) throw new TransientHttpError()
  if (!res.ok || res.status !== 202) {
    const err = await res.json().catch(() => ({ detail: 'Enhancement failed' }))
    throw new Error(err.detail || 'Enhancement failed')
  }
  return res.json()
}

export async function fetchLlmEnhanceOperation(
  scope: LlmEnhanceOperationScope,
  signal?: AbortSignal,
): Promise<LlmEnhanceOperationStatus | null> {
  throwIfAborted(signal)
  const query = new URLSearchParams({ workspace: scope.workspace })
  const res = await fetch(
    `${BASE}/api/v1/llm/operations/enhance/${encodeURIComponent(scope.requestId)}?${query}`,
    { cache: 'no-store', signal },
  )
  if (res.status === 404) return null
  if (isTransientHttpStatus(res.status)) throw new TransientHttpError()
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Prompt Enhance status is unavailable' }))
    throw new Error(err.detail || 'Prompt Enhance status is unavailable')
  }
  const status = await res.json() as LlmEnhanceOperationStatus
  assertLlmEnhanceStatusScope(status, scope)
  return status
}

async function fetchLlmEnhanceResult(
  scope: LlmEnhanceOperationScope,
  signal?: AbortSignal,
): Promise<LlmEnhanceResult> {
  await assertLlmEnhanceProjectScope(scope, signal)
  const query = new URLSearchParams({ workspace: scope.workspace })
  const res = await fetch(
    `${BASE}/api/v1/llm/operations/enhance/${encodeURIComponent(scope.requestId)}/result?${query}`,
    { cache: 'no-store', signal },
  )
  if (res.status === 404) {
    throw new LlmEnhanceWaitError('This Prompt Enhance result is no longer available.')
  }
  if (isTransientHttpStatus(res.status)) throw new TransientHttpError()
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Prompt Enhance result is unavailable' }))
    throw new Error(err.detail || 'Prompt Enhance result is unavailable')
  }
  const result = await res.json() as Partial<LlmEnhanceResult>
  await assertLlmEnhanceProjectScope(scope, signal)
  if (typeof result.original !== 'string' || typeof result.enhanced !== 'string') {
    throw new LlmEnhanceScopeError('The Prompt Enhance result did not match its request')
  }
  return { original: result.original, enhanced: result.enhanced }
}

async function recoverLlmEnhanceSubmission(
  request: Record<string, unknown>,
  scope: LlmEnhanceOperationScope,
  signal?: AbortSignal,
): Promise<LlmEnhanceOperationStatus> {
  const startedAt = Date.now()
  while (Date.now() - startedAt < LLM_PREPARATION_MAX_WAIT_MS) {
    throwIfAborted(signal)
    try {
      const existing = await fetchLlmEnhanceOperation(scope, signal)
      if (existing) return existing
      // A missing status is safe to re-submit only while the exact browser
      // project instance still owns this UUID and unchanged request body.
      await assertLlmEnhanceProjectScope(scope, signal)
      const submitted = await submitLlmEnhance(request, signal)
      assertLlmEnhanceStatusScope(submitted, scope)
      return submitted
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
      await waitForPreparationPoll(signal)
    }
  }
  throw new LlmEnhanceWaitError(
    'Prompt Enhance status is still unavailable. Reload to resume waiting.',
  )
}

export async function waitForLlmEnhanceOperation(
  scope: LlmEnhanceOperationScope,
  signal?: AbortSignal,
  initial?: LlmEnhanceOperationStatus,
  onStatus?: (status: LlmEnhanceOperationStatus) => void,
): Promise<LlmEnhanceResult> {
  const startedAt = Date.now()
  let operation: LlmEnhanceOperationStatus | null | undefined = initial
  while (!operation) {
    if (Date.now() - startedAt >= LLM_PREPARATION_MAX_WAIT_MS) {
      throw new LlmEnhanceWaitError(
        'Prompt Enhance is still running. Reload to resume waiting.',
      )
    }
    try {
      operation = await fetchLlmEnhanceOperation(scope, signal)
      if (!operation) {
        throw new LlmEnhanceWaitError('This Prompt Enhance request is no longer available.')
      }
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
      await waitForPreparationPoll(signal)
    }
  }
  assertLlmEnhanceStatusScope(operation, scope)
  onStatus?.(operation)
  while (operation.status === 'running') {
    if (Date.now() - startedAt >= LLM_PREPARATION_MAX_WAIT_MS) {
      throw new LlmEnhanceWaitError(
        'Prompt Enhance is still running. Reload to resume waiting.',
      )
    }
    await waitForPreparationPoll(signal)
    try {
      const next = await fetchLlmEnhanceOperation(scope, signal)
      if (!next) {
        throw new LlmEnhanceWaitError('This Prompt Enhance request is no longer available.')
      }
      operation = next
      onStatus?.(operation)
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
    }
  }
  if (operation.status !== 'completed' || !operation.result_available) {
    throw new Error(
      operation.error?.message
      || (operation.status === 'cancelled'
        ? 'Prompt enhancement was cancelled'
        : 'Prompt enhancement failed'),
    )
  }
  while (Date.now() - startedAt < LLM_PREPARATION_MAX_WAIT_MS) {
    try {
      return await fetchLlmEnhanceResult(scope, signal)
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
      await waitForPreparationPoll(signal)
    }
  }
  throw new LlmEnhanceWaitError(
    'Prompt Enhance finished, but its result is still unreachable. Reload to resume waiting.',
  )
}

export async function resumeLlmEnhancePrompt(
  scope: LlmEnhanceOperationScope,
  options?: Pick<LlmEnhanceRequestOptions, 'signal' | 'onOperationStatus'>,
): Promise<LlmEnhanceResult> {
  await assertLlmEnhanceProjectScope(scope, options?.signal)
  return waitForLlmEnhanceOperation(
    scope,
    options?.signal,
    undefined,
    options?.onOperationStatus,
  )
}

export async function cancelLlmEnhancePrompt(
  scope: LlmEnhanceOperationScope,
  signal?: AbortSignal,
): Promise<LlmEnhanceOperationStatus> {
  await assertLlmEnhanceProjectScope(scope, signal)
  const query = new URLSearchParams({ workspace: scope.workspace })
  const operationUrl = `${BASE}/api/v1/llm/operations/enhance/${encodeURIComponent(scope.requestId)}?${query}`
  const startedAt = Date.now()
  while (true) {
    throwIfAborted(signal)
    const res = await fetch(operationUrl, {
      method: 'DELETE', cache: 'no-store', signal,
    })
    if (res.status !== 404) {
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Prompt Enhance could not be cancelled' }))
        throw new Error(err.detail || 'Prompt Enhance could not be cancelled')
      }
      const status = await res.json() as LlmEnhanceOperationStatus
      assertLlmEnhanceStatusScope(status, scope)
      return status
    }

    // DELETE may race an ambiguous POST whose 202 response was lost before
    // the operation became visible. Never treat this 404 as cancellation: an
    // admitted worker could otherwise appear after the browser forgets it.
    let admitted: LlmEnhanceOperationStatus | null = null
    try {
      admitted = await fetchLlmEnhanceOperation(scope, signal)
    } catch (error) {
      throwIfAborted(signal)
      if (!isTransientRequestError(error)) throw error
    }
    if (admitted && admitted.status !== 'running') {
      return admitted
    }
    if (Date.now() - startedAt >= LLM_ENHANCE_CANCEL_ADMISSION_WAIT_MS) {
      throw new LlmEnhanceWaitError(
        'Prompt Enhance cancellation is still confirming. Reload to resume or cancel again.',
      )
    }
    await waitForLlmMutationRetry(signal)
  }
}

export async function llmEnhancePrompt(params: {
  workspace: string
  request_id: string
  project_instance: string
  prompt: string
  mode?: string
  model_type?: string
  temperature?: number
  image_path?: string
  image_paths?: string[]
  duration_seconds?: number
  window_count?: number
  window_size_seconds?: number
  preserve_global_timeline?: boolean
  activated_loras?: string[]
  tts_enhance_mode?: string
  tts_voice_count?: number
  max_new_tokens?: number
  explicit_output?: boolean
}, options: LlmEnhanceRequestOptions): Promise<LlmEnhanceResult> {
  const scope: LlmEnhanceOperationScope = {
    requestId: params.request_id,
    workspace: params.workspace,
    projectInstance: params.project_instance,
  }
  if (options.projectInstance !== params.project_instance) {
    throw new LlmEnhanceScopeError('The Prompt Enhance project fence did not match its request')
  }
  await prepareLlmForRequest(
    {
      workspace: params.workspace,
      purpose: 'enhance',
      model_type: params.model_type,
      vision_required: Boolean(params.image_path || params.image_paths?.length),
    },
    options,
  )
  await assertLlmEnhanceProjectScope(scope, options.signal)
  throwIfAborted(options.signal)
  // Persist the content-free recovery scope immediately before the first POST.
  await options.onSubmissionAttempted?.()
  throwIfAborted(options.signal)
  let operation: LlmEnhanceOperationStatus
  try {
    operation = await submitLlmEnhance(params, options.signal)
    assertLlmEnhanceStatusScope(operation, scope)
  } catch (error) {
    throwIfAborted(options.signal)
    if (!isTransientRequestError(error)) throw error
    // An accepted 202 may have been hidden by a proxy disconnect. Query this
    // UUID first and only then re-submit the exact request when still absent.
    operation = await recoverLlmEnhanceSubmission(params, scope, options.signal)
  }
  return waitForLlmEnhanceOperation(
    scope,
    options.signal,
    operation,
    options.onOperationStatus,
  )
}

export async function llmDescribeImage(params: {
  workspace: string
  image_path: string
  prompt?: string
  max_new_tokens?: number
}, options?: LlmRequestOptions): Promise<{ description: string }> {
  return withLlmPreparation(
    { workspace: params.workspace, purpose: 'configured' },
    options,
    async () => {
      const res = await fetch(`${BASE}/api/v1/llm/describe-image`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
        signal: options?.signal,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Image description failed' }))
        throw new Error(err.detail || 'Image description failed')
      }
      return res.json()
    },
  )
}

// --- Audio Analysis ---

export async function uploadAudio(file: File): Promise<{
  filename: string
  path: string
  url: string
  duration_seconds?: number | null
}> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/api/v1/upload-audio`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Upload failed' }))
    throw new Error(err.detail || 'Audio upload failed')
  }
  return res.json()
}

export async function analyzeAudio(params: {
  audio_path: string
  workspace?: string
  director_request_id?: string
  transcribe?: boolean
  extract_vocals?: boolean
  /** Known written lyrics (generated tracks) — seeds Whisper so the
   *  transcription snaps to the real words instead of mishearing
   *  sung vocals. Omit for uploads/unknown tracks. */
  lyrics_hint?: string
}): Promise<import('../types').AudioAnalysisResult> {
  const res = await fetch(`${BASE}/api/v1/audio/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Analysis failed' }))
    throw new Error(err.detail || 'Audio analysis failed')
  }
  return res.json()
}

/** Read live progress of the in-flight audio analyze call. Backed by
 *  audio_analysis._PROGRESS — updated at each phase boundary in the
 *  synchronous analyze() call. Polled by the Director sidebar to
 *  show "Loading transcription model (first use downloads ~300MB)..."
 *  vs "Transcribing audio..." instead of a single "Analyzing audio..."
 *  message for the entire 1-5 minute first-run wait. Returns empty
 *  step/detail when no analyze is in flight. */
export async function fetchAudioAnalyzeStatus(): Promise<{ step: string; detail: string }> {
  const res = await fetch(`${BASE}/api/v1/audio/analyze/status`)
  if (!res.ok) return { step: '', detail: '' }
  return res.json()
}

export async function suggestAudioClips(params: {
  analysis: import('../types').AudioAnalysisResult
  clip_duration: number
  total_duration?: number
}): Promise<{ clips: import('../types').SuggestedClip[] }> {
  const res = await fetch(`${BASE}/api/v1/audio/suggest-clips`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Clip suggestion failed' }))
    throw new Error(err.detail || 'Clip suggestion failed')
  }
  return res.json()
}

// --- Director ---

export async function planAnglePrompts(params: {
  workspace: string
  style_prompt: string
  num_angles?: number
}, options?: LlmRequestOptions): Promise<{ prompts: string[] }> {
  return preparedConfiguredPost(
    '/api/v1/director/plan-angle-prompts',
    params,
    options,
    'Angle prompt planning failed',
  )
}

export async function planClipPrompts(params: {
  workspace: string
  clips: import('../types').SuggestedClip[]
  style_prompt: string
  lyrics?: import('../types').LyricSegment[]
  bpm: number
}, options?: LlmRequestOptions): Promise<{ prompts: string[] }> {
  return preparedConfiguredPost(
    '/api/v1/director/plan-prompts',
    params,
    options,
    'Prompt planning failed',
  )
}

export async function planClipStructure(params: {
  analysis: import('../types').AudioAnalysisResult
  workspace: string
  director_request_id?: string
  energy_bias?: number
  fps?: number
  frames_steps?: number
  frames_minimum?: number
  total_duration?: number
  /** The Director's VIDEO model — the backend resolves fps/frame params
   *  from its model def. The fps/frames_* fields above reflect the
   *  Studio-selected model (possibly a music model) and are only a
   *  fallback when this is absent. */
  video_model?: string
}, options?: LlmRequestOptions): Promise<{ clips: import('../types').PlannedClip[] }> {
  return preparedConfiguredPost(
    '/api/v1/audio/plan-structure',
    params,
    options,
    'Structure planning failed',
  )
}

export async function classifySections(params: {
  analysis: import('../types').AudioAnalysisResult
  workspace: string
  director_request_id?: string
}, options?: LlmRequestOptions): Promise<{
  sections: import('../types').AudioSection[]
  song_structure: { label: string; display_label: string; start: number }[]
  method: 'llm' | 'heuristic'
}> {
  return preparedConfiguredPost(
    '/api/v1/director/classify-sections',
    params,
    options,
    'Section classification failed',
  )
}

export async function planClipPromptsAndImages(params: {
  workspace: string
  clips: import('../types').PlannedClip[]
  scene_description: string
  visual_style?: string
  explicit_output?: boolean
  lyrics?: import('../types').LyricSegment[]
  bpm: number
  reference_image_path?: string | null
  character_ref_paths?: string[]
  character_ref_labels?: string[]
  location_ref_paths?: string[]
  location_ref_labels?: string[]
  speaker_mappings?: Record<string, { name: string; role: string }>
  prompt_type?: 'image' | 'video' | 'both'
  existing_image_prompts?: string[]
}, options?: LlmRequestOptions): Promise<{ clip_plans: import('../types').ClipPlan[] }> {
  return preparedConfiguredPost(
    '/api/v1/director/plan-prompts-and-images',
    params,
    options,
    'Prompt and image planning failed',
  )
}

// --- Short Film Director ---

export async function planDialogueScenes(params: {
  workspace: string
  analysis: import('../types').AudioAnalysisResult
  pacing_bias?: number
  fps?: number
  frames_steps?: number
  frames_minimum?: number
}, options?: LlmRequestOptions): Promise<{ clips: import('../types').PlannedClip[] }> {
  return preparedConfiguredPost(
    '/api/v1/director/plan-dialogue-scenes',
    params,
    options,
    'Dialogue scene planning failed',
  )
}

export async function planShortFilmPrompts(params: {
  workspace: string
  clips: import('../types').PlannedClip[]
  scene_description: string
  visual_style?: string
  explicit_output?: boolean
  lyrics?: import('../types').LyricSegment[]
  reference_image_path?: string | null
  character_ref_paths?: string[]
  character_ref_labels?: string[]
  location_ref_paths?: string[]
  location_ref_labels?: string[]
  speaker_mappings?: Record<string, { name: string; role: string }>
  characters?: { name: string; description: string }[]
  prompt_type?: 'image' | 'video' | 'both'
  existing_image_prompts?: string[]
}, options?: LlmRequestOptions): Promise<{ clip_plans: import('../types').ClipPlan[] }> {
  return preparedConfiguredPost(
    '/api/v1/director/plan-short-film-prompts',
    params,
    options,
    'Short film prompt planning failed',
  )
}

export async function getLlmStreamStatus(): Promise<{ text: string; done: boolean }> {
  const res = await fetch(`${BASE}/api/v1/llm/stream-status`)
  if (!res.ok) return { text: '', done: true }
  return res.json()
}

export async function planShortFilmScript(params: {
  workspace: string
  story_description: string
  visual_style?: string
  explicit_output?: boolean
  characters?: { name: string; description: string }[]
  reference_image_path?: string | null
  character_ref_paths?: string[]
  character_ref_labels?: string[]
  location_ref_paths?: string[]
  location_ref_labels?: string[]
  target_duration?: number
  target_scenes?: number
  narrative_mode?: boolean
  fps?: number
  frames_steps?: number
  frames_minimum?: number
}, options?: LlmRequestOptions): Promise<{ clips: import('../types').PlannedClip[]; clip_plans: import('../types').ClipPlan[] }> {
  return preparedConfiguredPost(
    '/api/v1/director/plan-short-film-script',
    params,
    options,
    'Story planning failed',
  )
}

// --- CivitAI Browser ---

export async function fetchLoraDirectories(): Promise<{ directories: string[] }> {
  const res = await fetch(`${BASE}/api/v1/loras/directories`)
  if (!res.ok) throw new Error('Failed to fetch LoRA directories')
  return res.json()
}

export interface CivitAIModelFilter {
  label: string
  civitai_base: string
  search_query?: string
  default_dir?: string
}

export async function fetchCivitAIModelFilters(): Promise<{ filters: CivitAIModelFilter[] }> {
  const res = await fetch(`${BASE}/api/v1/civitai/base-models`)
  if (!res.ok) throw new Error('Failed to fetch model filters')
  return res.json()
}

export interface CheckpointArchitecture {
  architecture: string
  name: string
  family: string
  template_model_type: string
}

// List the architectures a full checkpoint can be imported as (video/image
// models we already support) + a best-guess default for the given CivitAI
// baseModel so the picker can pre-select it.
export async function fetchCheckpointArchitectures(
  baseModel?: string
): Promise<{ architectures: CheckpointArchitecture[]; suggested_architecture: string | null }> {
  const qs = baseModel ? `?base_model=${encodeURIComponent(baseModel)}` : ''
  const res = await fetch(`${BASE}/api/v1/civitai/checkpoint-architectures${qs}`)
  if (!res.ok) throw new Error('Failed to fetch checkpoint architectures')
  return res.json()
}

export interface InstalledCheckpoint {
  model_type: string
  name: string
  architecture: string
  civitai_model_id: number | null
  current_version_id: number | null
  base_model: string
  filename: string
  auto_quantize: boolean
  update_status: 'current' | 'available' | 'unknown' | 'removed'
  latest_version_id: number | null
  latest_published_at: string | null
  latest_changelog: string | null
  preview_url: string | null
}

// List CivitAI-imported checkpoints (registered finetunes) with update status.
export async function fetchInstalledCheckpoints(): Promise<{ checkpoints: InstalledCheckpoint[]; manifest_last_check_at: string | null }> {
  const res = await fetch(`${BASE}/api/v1/checkpoints/installed`)
  if (!res.ok) throw new Error('Failed to fetch installed checkpoints')
  return res.json()
}

// Query CivitAI for newer versions of every imported checkpoint.
export async function checkCheckpointUpdates(force = false): Promise<{ checked: number; updates_available: number; errors: number; skipped: boolean }> {
  const res = await fetch(`${BASE}/api/v1/checkpoints/check-updates?force=${force}`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to check checkpoint updates')
  return res.json()
}

export async function searchCivitAI(params: {
  query?: string; sort?: string; period?: string
  nsfw?: boolean; types?: string; baseModels?: string
  limit?: number; cursor?: string
}): Promise<import('../types').CivitAISearchResult> {
  const qs = new URLSearchParams()
  if (params.query) qs.set('query', params.query)
  if (params.sort) qs.set('sort', params.sort)
  if (params.period) qs.set('period', params.period)
  if (params.nsfw != null) qs.set('nsfw', String(params.nsfw))
  if (params.types) qs.set('types', params.types)
  if (params.baseModels) qs.set('baseModels', params.baseModels)
  if (params.limit) qs.set('limit', String(params.limit))
  if (params.cursor) qs.set('cursor', params.cursor)
  const res = await fetch(`${BASE}/api/v1/civitai/search?${qs}`)
  if (!res.ok) {
    // Pull the backend's `detail` if available — it carries the
    // human-readable reason (e.g. "CivitAI is currently in scheduled
    // maintenance") that the proxy synthesises for known states.
    let detail = ''
    try {
      const body = await res.json()
      detail = body?.detail || ''
    } catch { /* non-JSON body */ }
    const err = new Error(detail || `CivitAI search failed (HTTP ${res.status})`)
    ;(err as Error & { status?: number }).status = res.status
    throw err
  }
  return res.json()
}

export async function fetchCivitAIModel(modelId: number): Promise<import('../types').CivitAIModel> {
  const res = await fetch(`${BASE}/api/v1/civitai/model/${modelId}`)
  if (!res.ok) throw new Error('Failed to fetch model details')
  return res.json()
}

export async function startCivitAIDownload(params: {
  download_url: string; filename: string; target_arch: string
  model_id: number; version_id: number; trained_words: string[]
  model_name: string; images: { url: string }[]
  description?: string; version_description?: string; base_model?: string
  example_prompts?: string[]; tags?: string[]
  nsfw?: boolean; target_dir_name?: string; published_at?: string
  // Checkpoint imports: kind='checkpoint' routes the file into ckpts/ and
  // registers a finetune for target_architecture instead of saving a LoRA.
  // auto_quantize=true sets the finetune to load-time int8 (mmgp).
  kind?: string; target_architecture?: string; auto_quantize?: boolean
}): Promise<{ download_id: string }> {
  const res = await fetch(`${BASE}/api/v1/civitai/download`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Download failed' }))
    throw new Error(err.detail || 'Download failed')
  }
  return res.json()
}

export async function fetchCivitAIDownloads(): Promise<{ downloads: import('../types').CivitAIDownload[] }> {
  const res = await fetch(`${BASE}/api/v1/civitai/downloads`)
  if (!res.ok) throw new Error('Failed to fetch downloads')
  return res.json()
}

export async function generateLoraGuide(modelType: string, filename: string): Promise<{ guide: string }> {
  const res = await fetch(`${BASE}/api/v1/loras/generate-guide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_type: modelType, filename }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Guide generation failed' }))
    throw new Error(err.detail || 'Guide generation failed')
  }
  return res.json()
}

export async function fetchLoraGuide(modelType: string, filename: string): Promise<{ guide: string | null }> {
  const res = await fetch(`${BASE}/api/v1/loras/${encodeURIComponent(modelType)}/${encodeURIComponent(filename)}/guide`)
  if (!res.ok) return { guide: null }
  return res.json()
}

export async function importHuggingFaceLora(url: string, targetDir?: string, filename?: string): Promise<{
  status: string; download_id: string; filename: string; target_dir: string; repo_id?: string; base_model: string
}> {
  const res = await fetch(`${BASE}/api/v1/huggingface/import-lora`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, target_dir: targetDir || '', filename: filename || '' }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Import failed' }))
    throw new Error(err.error || 'Import failed')
  }
  return res.json()
}

export async function startLoraScan(options?: { modelType?: string; force?: boolean }): Promise<{ scan_id: string; total: number }> {
  const body: Record<string, unknown> = {}
  if (options?.modelType) body.model_type = options.modelType
  if (options?.force) body.force = true
  const res = await fetch(`${BASE}/api/v1/loras/scan-and-generate-guides`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Scan failed' }))
    throw new Error(err.detail || 'Scan failed')
  }
  return res.json()
}

export async function fetchLoraScanStatus(scanId: string): Promise<{
  status: string; current: number; total: number; message: string
  results: { filename: string; metadata?: string; guide?: string; error?: string }[]
}> {
  const res = await fetch(`${BASE}/api/v1/loras/scan-status/${scanId}`)
  if (!res.ok) throw new Error('Failed to fetch scan status')
  return res.json()
}

/** Per-LoRA update status. Mirrored from types/index.ts for use in
 *  the API layer without forcing a circular import. */
export type LoraUpdateStatus = 'current' | 'available' | 'unknown' | 'local' | 'removed'

export interface InstalledLora {
  filename: string
  directory: string
  /** File lives in a linked install's loras folder (read-only), not
   *  Maestro's own. Sidecars/guides for it live in Maestro's mirror. */
  linked?: boolean
  trained_words: string[]
  preview_url: string | null
  civitai_model_id: number | null
  hf_repo_id?: string | null
  has_guide: boolean
  name: string | null
  base_model: string | null
  nsfw: boolean
  /** Stable identifier that survives version updates. Format:
   *  `civitai:{modelId}` when the sidecar exposes a CivitAI modelId,
   *  otherwise `local:{filename}`. Used as the persistence key for
   *  per-LoRA settings (weight overrides, activations) so updating a
   *  LoRA from v1.2 → v1.5 carries those settings forward. */
  lora_id: string
  /** Update status from the cached LoRA-update manifest, populated by
   *  the backend on every /api/v1/loras/installed and
   *  /api/v1/loras/{model_type}/details call. The UI uses this to
   *  render badges. */
  update_status?: LoraUpdateStatus
  latest_version_id?: number | null
  current_version_id?: number | null
  latest_published_at?: string | null
  latest_changelog?: string | null
  /** On-disk size of the .safetensors file (null when unreadable). */
  size_bytes?: number | null
  /** When the file arrived: sidecar downloadedAt (CivitAI downloads) or
   *  the weight file's mtime (HF/hand-installed). ISO string. */
  downloaded_at?: string | null
  /** The version's CivitAI release date (publishedAt) — captured at
   *  download time, backfilled for older files by Check Updates. */
  released_at?: string | null
}

export async function fetchInstalledLoras(): Promise<{
  loras: InstalledLora[]
  /** ISO timestamp of the last full CivitAI check that populated the
   *  cached update manifest. UI shows "last checked X minutes ago". */
  manifest_last_check_at?: string | null
}> {
  const res = await fetch(`${BASE}/api/v1/loras/installed`)
  if (!res.ok) throw new Error('Failed to fetch installed LoRAs')
  return res.json()
}

// --- Storage (duplicates + usage analytics) ---

export interface StorageDuplicate {
  kind: 'checkpoint' | 'lora'
  filename: string
  rel_path: string
  primary_path: string
  size_bytes: number
  linked_path: string
  linked_size_bytes: number
  linked_install: string
}

export interface StorageUsageModel {
  model_type: string
  name: string
  size_bytes: number
  /** Bytes living in the primary (deletable) roots — what deleting frees. */
  primary_bytes: number
  /** Display name of the base model whose weights this entry aliases
   *  (finetunes with "URLs": "<base>") — deleting this row frees nothing. */
  alias_of?: string | null
  use_count: number
  last_used: number | null
}

export interface StorageUsageLora {
  filename: string
  directory: string
  linked: boolean
  size_bytes: number
  use_count: number
  last_used: number | null
}

export interface StorageUsage {
  models: StorageUsageModel[]
  /** Globally deduped — per-model sizes overlap on shared weights
   *  (base transformers, text encoders), so summing rows over-counts. */
  models_total_bytes: number
  loras: StorageUsageLora[]
  workspaces: { name: string; file_count: number; size_bytes: number }[]
  scanned_sidecars: number
}

export async function fetchStorageUsage(): Promise<StorageUsage> {
  const res = await fetch(`${BASE}/api/v1/storage/usage`)
  if (!res.ok) throw new Error('Failed to fetch storage usage')
  return res.json()
}

export async function fetchStorageDuplicates(): Promise<{ duplicates: StorageDuplicate[]; conflicts: StorageDuplicate[]; total_reclaimable_bytes: number }> {
  const res = await fetch(`${BASE}/api/v1/storage/duplicates`)
  if (!res.ok) throw new Error('Failed to scan for duplicates')
  return res.json()
}

export async function reclaimDuplicate(path: string): Promise<{ freed_bytes: number }> {
  const res = await fetch(`${BASE}/api/v1/storage/duplicates/reclaim`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Reclaim failed' }))
    throw new Error(err.detail || 'Reclaim failed')
  }
  return res.json()
}

export async function removeLinkedDuplicate(path: string): Promise<{ freed_bytes: number; recycled: boolean }> {
  const res = await fetch(`${BASE}/api/v1/storage/duplicates/remove-linked`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Remove failed' }))
    throw new Error(err.detail || 'Remove failed')
  }
  return res.json()
}

export async function deleteLoraFile(directory: string, filename: string): Promise<{ deleted: string; deferred: boolean }> {
  const params = new URLSearchParams({ directory: directory || '.', filename })
  const res = await fetch(`${BASE}/api/v1/loras/file?${params.toString()}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to delete LoRA' }))
    throw new Error(err.detail || 'Failed to delete LoRA')
  }
  return res.json()
}

/** Single entry in the cached LoRA-update manifest (one per
 *  civitai-sourced LoRA). The manifest itself is keyed by `lora_id`
 *  (e.g. `civitai:12345`) — see LoraUpdateManifest. */
export interface LoraManifestEntry {
  model_id: number
  current_version_id: number | null
  latest_version_id: number | null
  latest_published_at: string | null
  latest_changelog: string | null
  status: 'current' | 'available' | 'removed' | 'unknown'
  last_checked_at: string
}

export interface LoraUpdateManifest {
  _version: number
  last_full_check_at: string | null
  entries: Record<string, LoraManifestEntry>
}

export interface LoraUpdateCheckResult {
  /** Number of LoRAs with a `civitai:`-style lora_id that the backend
   *  considered for refresh during this call. */
  checked: number
  /** How many of the checked LoRAs have a newer version on CivitAI. */
  updates_available: number
  /** Per-LoRA error messages (network failures, deleted models, etc.).
   *  Empty array on success. */
  errors: string[]
  /** True when the backend skipped the refresh because the cached
   *  manifest is fresh (within the 24h window) and `force` was false.
   *  In that case `checked` and `updates_available` come from cache. */
  skipped: boolean
  /** Why the refresh was skipped, when `skipped: true`. Currently the
   *  only value is "fresh" but kept open for future cases. */
  reason?: string
  /** ISO timestamp of the most recent full check (the one whose data
   *  is reflected in `checked` / `updates_available`). */
  last_full_check_at?: string | null
}

/** Trigger a fresh CivitAI version check across every installed LoRA
 *  with a sidecar `modelId`. Updates the cached manifest the backend
 *  uses to populate per-LoRA `update_status` fields on subsequent
 *  /installed and /{model_type}/details calls.
 *
 *  Honours a 24h staleness window unless `force` is true:
 *    - `checkLoraUpdates(false)` — opportunistic; if the manifest is
 *      <24h old the backend short-circuits and returns the cached
 *      summary with `skipped: true`. Cheap to call on app startup.
 *    - `checkLoraUpdates(true)`  — bypass the window. Use for explicit
 *      "Check now" buttons in the UI; pulls from CivitAI even if a
 *      check happened minutes ago.
 *
 *  Returns the summary the UI shows in a toast. Throws on network/HTTP
 *  failure (call sites typically `.catch()` to keep UI responsive). */
export async function checkLoraUpdates(force = false): Promise<LoraUpdateCheckResult> {
  const url = `${BASE}/api/v1/loras/check-updates${force ? '?force=true' : ''}`
  const res = await fetch(url, { method: 'POST' })
  if (!res.ok) throw new Error(`Failed to check LoRA updates (${res.status})`)
  return res.json()
}

/** Read the cached LoRA-update manifest WITHOUT hitting CivitAI.
 *  Use this on app startup to populate badges immediately, then
 *  optionally call checkLoraUpdates() if the cache is stale. The
 *  manifest schema is documented in launch.py near the constant
 *  LORA_MANIFEST_VERSION. */
export async function fetchLoraUpdateManifest(): Promise<LoraUpdateManifest> {
  const res = await fetch(`${BASE}/api/v1/loras/update-manifest`)
  if (!res.ok) throw new Error('Failed to fetch LoRA update manifest')
  return res.json()
}

export async function fetchLoraDetails(modelType: string): Promise<{
  loras: import('../types').LoraInfo[]
  guidance_max_phases: number
  /** ISO timestamp of the last full CivitAI check that populated the
   *  cached update manifest. UI uses this to render "last checked X
   *  minutes ago" alongside the manual "Check updates" button. */
  manifest_last_check_at?: string | null
}> {
  const res = await fetch(`${BASE}/api/v1/loras/${encodeURIComponent(modelType)}/details`)
  if (!res.ok) throw new Error('Failed to fetch LoRA details')
  return res.json()
}

// --- Active model file downloads (HuggingFace etc.) ---

export interface ActiveDownload {
  file_id: string
  filename: string
  started_at: number
  last_active_at: number
  downloaded_bytes: number
  total_bytes: number | null
  status: 'downloading' | 'stalled' | 'retrying' | 'done' | 'incomplete'
  /** Seconds since the byte counter last advanced. UI uses this to
   *  flag stalled downloads (e.g. `> 30` → show "slow / retrying"). */
  seconds_since_progress: number
}

export async function fetchActiveDownloads(): Promise<{ downloads: ActiveDownload[] }> {
  const res = await fetch(`${BASE}/api/v1/downloads/active`)
  if (!res.ok) throw new Error(`Failed to fetch active downloads (${res.status})`)
  return res.json()
}
