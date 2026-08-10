import type {
  ArtifactClass,
  DirectorRecoveryMetadata,
  OutputArtifactScope,
  OutputSearchFilters,
  ProjectReferenceAnchorBasis,
  ProjectReferenceAnchorPrivacy,
  ProjectReferenceAdditionalLora,
  ProjectReferenceAdditionalLoraSummary,
  ProjectReferenceAssetType,
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
  ScailResolutionProfile,
} from '../types'

export type {
  ProjectReferenceAssetType,
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

export async function fetchH3StyleWorkflows(): Promise<{
  source: string
  revision: string
  checked_at: number | null
  update_status: 'updated' | 'cached' | 'bundled_fallback' | 'offline_fallback'
  update_error?: string
  styles: Array<{
    id: string
    label: string
    description: string
    prompt_brief: string
  }>
}> {
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
  project_password_required: boolean
  project_names_visible: boolean
  machine_controls: boolean
  custom_model_sources: boolean
  catalog_model_downloads: boolean
  classic_ui: boolean
  cloudflare_enabled: boolean
  share_url: string
  share_flow: string
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

export interface Workspace {
  name: string
  path?: string
  file_count?: number
  password_protected?: boolean
  unlocked?: boolean
  remember_policy?: WorkspaceRememberPolicy | null
  unlock_expires_at?: number | null
  unlock_idle_expires_at?: number | null
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
  const res = await fetch(`${BASE}/api/v1/workspaces`)
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
  roles?: {
    sheets?: string[]
    repaired?: string[]
  }
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
  content_capability?: 'standard' | 'unrestricted_local'
  initial_blur?: boolean
  intelligence_policy?: 'standard_auto' | 'uncensored_auto'
}

export interface FreshProjectReferenceGenerationRequest extends ProjectReferenceGenerationSettings {
  asset_id?: never
  parent_variant_id?: never
  edit_instruction?: never
  name: string
  asset_type: ProjectReferenceAssetType
  description?: string
  tags?: string[]
  poses?: string
  outfits?: string
  style?: string
  genre?: string
}

export interface ExistingProjectReferenceGenerationRequest extends ProjectReferenceGenerationSettings {
  asset_id: string
  parent_variant_id?: string
  edit_instruction?: string
  asset_type?: ProjectReferenceAssetType
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
  }
  review_policy: {
    mandatory_for_content_capabilities: Array<'unrestricted_local'>
    mandatory_when_explicit_output: true
    off_allowed_for_content_capabilities: Array<'standard'>
    mandatory_contract: 'explicit_unrestricted_fidelity_v1'
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
    type_fields: ProjectReferenceTypeFields
    detail_callouts: ProjectReferenceDetailCallout[]
  }
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

export function selectProjectReferenceModel(
  models: readonly ProjectReferenceModelCatalogEntry[],
  current: string,
  preferred = '',
): string {
  if (models.some(model => model.model_type === current)) return current
  if (preferred && models.some(model => model.model_type === preferred)) return preferred
  return models[0]?.model_type ?? ''
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
  content_capability?: 'standard' | 'unrestricted_local'
  initial_blur?: boolean
  intelligence_policy?: 'standard_auto' | 'uncensored_auto'
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
    if (!contract) return false
    const exactModel = reviewModels.find(model => (
      model.id === contract.resolved_model
      && (model.provider ?? 'local') === contract.resolved_provider
    ))
    return Boolean(exactModel) && (modelId === 'auto_local'
      || (modelId === contract.resolved_model
        && (!provider || provider === contract.resolved_provider)))
  }
  if (modelId === 'auto_local') {
    return reviewModels.some(model => (model.provider ?? 'local') === 'local')
  }
  return reviewModels.some(model => (
    model.id === modelId && (!provider || (model.provider ?? 'local') === provider)
  ))
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

/** Private labels are required to replay any custom public authored identity. */
export function projectReferenceRetryNeedsPrivateAuthoring(
  variant: ProjectAssetVariant,
): boolean {
  const authored = variant.metadata.reference_pack?.authored_settings
  return authored?.type_fields.some(field => field.items.some(item => item.custom)) === true
    || authored?.detail_callouts.some(callout => callout.kind === 'custom') === true
}

function resolveProjectReferenceRetryAuthoredSettings(
  packMetadata: ProjectReferencePackVariantMetadata,
  fallback: ProjectReferenceRetrySettings,
  capabilities?: ProjectReferenceCapabilities,
): Pick<ProjectReferenceRetrySettings, 'type_fields' | 'detail_callouts'> {
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
  const resolvedCallouts: ProjectReferenceDetailCallout[] = []
  for (const publicCallout of summary.detail_callouts) {
    const localCallout = hasExactPrivateSnapshot && publicCallout.kind === 'custom'
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
    type_fields: resolvedFields as ProjectReferenceTypeFields,
    detail_callouts: resolvedCallouts,
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
    if (authoredSettings.type_fields !== undefined) {
      settings.type_fields = authoredSettings.type_fields
    }
    settings.managed_layout_assist = 'off'
    settings.preset = packMetadata.preset ?? fallback.preset
    settings.anchor_basis = packMetadata.anchor_basis ?? fallback.anchor_basis
    if (authoredSettings.detail_callouts !== undefined) {
      settings.detail_callouts = authoredSettings.detail_callouts
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
  if (!res.ok) throw projectAssetRequestError(res.status, 'Failed to load Reference Studio capabilities')
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

export async function generateProjectAssetReferences(
  project: string,
  body: ProjectReferenceGenerationRequest,
): Promise<ProjectReferenceGenerationResponse> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets/generate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) throw projectAssetRequestError(res.status, 'Failed to start reference generation')
  return res.json()
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
    const payload = await res.json().catch(() => ({})) as { detail?: unknown; error?: unknown }
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
    if (res.status === 404 && /unauthorized media|not found/i.test(detail)) {
      throw new Error(`Director could not access a selected reference${detail ? `: ${detail}` : '.'}`)
    }
    throw new Error(detail || `Failed to start Director pipeline (HTTP ${res.status})`)
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
  workspace: string
  skill_type: string
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
  platform?: string
  style?: string
  prompt_type?: string
  director_flags?: Record<string, boolean>
}

export interface DirectorV2PlanResponse {
  clip_plans: Array<{ video_prompt: string; image_prompt: string }>
  production_plan: Record<string, unknown>
  skill_type: string
}

export async function directorV2Plan(
  params: DirectorV2PlanRequest,
  options?: LlmRequestOptions,
): Promise<DirectorV2PlanResponse> {
  return withLlmPreparation(
    { workspace: params.workspace, purpose: 'configured' },
    options,
    async () => {
      const res = await fetch(`${BASE}/api/v1/director/v2/plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
        signal: options?.signal,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Plan failed' }))
        throw new Error(err.detail || 'Director v2 plan failed')
      }
      return res.json()
    },
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
  partial: Partial<import('../types').SystemConfig>
): Promise<{ status: string; updated: Record<string, unknown> }> {
  const res = await fetch(`${BASE}/api/v1/system-config`, {
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

// A 31B local model can take substantially longer to download/load than an
// HTTP proxy will keep one inference request open. Preparation uses short
// requests and permits a bounded 45-minute wait without placing creative
// content in the prepare payload.
const LLM_PREPARATION_MAX_WAIT_MS = 45 * 60 * 1000
const LLM_PREPARATION_VISIBLE_POLL_MS = 1_000

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
  params?: { model_id?: string; device?: string },
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

export async function fetchLlmModels(workspace?: string): Promise<{
  models: import('../types').LlmModelOption[]
  guides: import('../types').LlmPromptGuideOption[]
  project_instance?: string
}> {
  const query = workspace
    ? `?${new URLSearchParams({ workspace })}`
    : ''
  const res = await fetch(`${BASE}/api/v1/llm/models${query}`)
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

export async function llmEnhancePrompt(params: {
  workspace: string
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
}, options?: LlmRequestOptions): Promise<{ original: string; enhanced: string }> {
  return withLlmPreparation(
    {
      workspace: params.workspace,
      purpose: 'enhance',
      model_type: params.model_type,
      vision_required: Boolean(params.image_path || params.image_paths?.length),
    },
    options,
    async () => {
      const res = await fetch(`${BASE}/api/v1/llm/enhance-prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
        signal: options?.signal,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Enhancement failed' }))
        throw new Error(err.detail || 'Enhancement failed')
      }
      return res.json()
    },
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
   *  flag stalled downloads (e.g. `> 15` → show "slow / retrying"). */
  seconds_since_progress: number
}

export async function fetchActiveDownloads(): Promise<{ downloads: ActiveDownload[] }> {
  const res = await fetch(`${BASE}/api/v1/downloads/active`)
  if (!res.ok) throw new Error(`Failed to fetch active downloads (${res.status})`)
  return res.json()
}
