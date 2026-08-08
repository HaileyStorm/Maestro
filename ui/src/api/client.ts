import type { ArtifactClass, DirectorRecoveryMetadata, OutputArtifactScope, OutputSearchFilters, ScailResolutionProfile } from '../types'

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
  director?: import('../types').DirectorModelCompatibility
  is_downloaded?: boolean
  // True when the model JSON declares `"nsfw_only": true` in its
  // model block. The UI hides it from selectors and the visibility
  // settings unless servicesConfig.nsfw_mode is enabled.
  nsfw_only?: boolean
  preferred_explicit_fl2va?: boolean
  update_status?: string
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
  created_at?: number
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
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
  current_segment_model?: string
  current_segment_reason?: string
  current_segment_boundary?: import('../types').H3SegmentBoundary | null
  events?: JobLogEvent[]
}

export interface QueueJobState extends QueueRecoveryMetadata {
  job_id: string
  status: 'queued' | 'running'
  priority: number
  held: boolean
  hold_after_output: boolean
  position: number | null
  wait_reason: QueueWaitReason | null
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
    active_total: number
  }
  jobs: QueueJobState[]
}

export type QueueWaitReason =
  | 'running'
  | 'held'
  | 'queue_paused'
  | 'registering'
  | 'waiting_for_turn'
  | 'waiting_for_active_generation'
  | 'waiting_for_other_user'
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
  initialized_mature_models: string[]
  defaults_version: number
}

export async function fetchModelVisibility(): Promise<ModelVisibilitySettings> {
  const res = await fetch(`${BASE}/api/v1/model-visibility`)
  if (!res.ok) throw new Error('Failed to fetch model visibility')
  return res.json()
}

export async function updateModelVisibility(params: {
  enabled_models: string[]
  initialized_mature_models: string[]
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

export async function downloadModel(modelType: string): Promise<{ status: ModelDownloadStatus; model_type: string }> {
  const res = await fetch(`${BASE}/api/v1/models/${encodeURIComponent(modelType)}/download`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to start model download')
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

export async function previewGenerationPlan(params: Record<string, unknown>): Promise<{
  requires_review: boolean
  plan: import('../types').H3SegmentPlan | null
  effective_model_type: string
  requirements: import('../types').H3GenerationRequirements
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
  description: string
  instrumental?: boolean
  seed?: number
  reference_image_path?: string
}): Promise<{ style: string; lyrics: string; raw: string }> {
  const res = await fetch(`${BASE}/api/v1/llm/write-song`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Song writing failed' }))
    throw new Error(err.detail || 'Song writing failed')
  }
  return res.json()
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
}

export interface WorkspacePasswordResult {
  password_protected: boolean
  unlocked: boolean
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

export async function createWorkspace(name: string, password?: string): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/workspaces`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, password: password || undefined }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to create workspace' }))
    throw new Error(err.detail || 'Failed to create workspace')
  }
}

export async function unlockWorkspace(name: string, password: string): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/workspaces/${encodeURIComponent(name)}/unlock`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unlock failed' }))
    throw new Error(err.detail || 'Unlock failed')
  }
}

export async function setWorkspacePassword(name: string, password: string): Promise<WorkspacePasswordResult> {
  const res = await fetch(`${BASE}/api/v1/workspaces/${encodeURIComponent(name)}/password`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
  const payload = await res.json().catch(() => null) as ({
    detail?: unknown
    password_protected?: unknown
    unlocked?: unknown
  } | null)
  if (!res.ok) {
    throw new Error(typeof payload?.detail === 'string' ? payload.detail : 'Password update failed')
  }
  return {
    password_protected: payload?.password_protected === true,
    unlocked: payload?.unlocked === true,
  }
}

// --- Project reference assets ---

export interface ProjectAssetOutput {
  id: string
  filename: string
  relative_path: string
  media_type: string
  label: string
  metadata: Record<string, unknown>
}

export interface ProjectAssetVariant {
  id: string
  variant_type: string
  label: string
  status: 'candidate' | 'kept' | 'rejected'
  outputs: ProjectAssetOutput[]
  metadata: Record<string, unknown>
}

export interface ProjectAsset {
  id: string
  asset_type: string
  name: string
  description: string
  tags: string[]
  variants: ProjectAssetVariant[]
  metadata: Record<string, unknown>
}

export async function fetchProjectAssets(project: string): Promise<ProjectAsset[]> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets`)
  if (!res.ok) throw new Error('Failed to load project references')
  const data = await res.json()
  return data.assets || []
}

export async function createProjectAsset(project: string, body: Record<string, unknown>): Promise<ProjectAsset> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to create reference card' }))
    throw new Error(err.detail || 'Failed to create reference card')
  }
  return res.json()
}

export async function generateProjectAssetReferences(project: string, body: Record<string, unknown>): Promise<{ job_id: string; asset: ProjectAsset }> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets/generate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to start reference generation' }))
    throw new Error(err.detail || 'Failed to start reference generation')
  }
  return res.json()
}

export async function addProjectAssetVariant(
  project: string, assetId: string, body: Record<string, unknown>,
): Promise<ProjectAssetVariant> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets/${encodeURIComponent(assetId)}/variants`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to import reference media' }))
    throw new Error(err.detail || 'Failed to import reference media')
  }
  return res.json()
}

export async function setProjectAssetVariantStatus(
  project: string, assetId: string, variantId: string, status: 'candidate' | 'kept' | 'rejected',
): Promise<ProjectAssetVariant> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets/${encodeURIComponent(assetId)}/variants/${encodeURIComponent(variantId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
  })
  if (!res.ok) throw new Error('Failed to update reference candidate')
  return res.json()
}

export async function deleteProjectAssetVariant(
  project: string, assetId: string, variantId: string,
): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/projects/${encodeURIComponent(project)}/assets/${encodeURIComponent(assetId)}/variants/${encodeURIComponent(variantId)}`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to delete reference candidate' }))
    throw new Error(err.detail || 'Failed to delete reference candidate')
  }
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

export interface PipelineStatus extends DirectorRecoveryMetadata {
  id: string
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
  llm_streaming: boolean
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

export async function fetchRecipes(): Promise<{ recipes: RecipeCard[] }> {
  const res = await fetch(`${BASE}/api/v1/recipes`)
  if (!res.ok) throw new Error('Failed to load recipes')
  return res.json()
}

export async function fetchRecipe(id: string): Promise<Recipe> {
  const res = await fetch(`${BASE}/api/v1/recipes/${encodeURIComponent(id)}`)
  if (!res.ok) throw new Error('Recipe not found')
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
  skill_type: string
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

export async function directorV2Plan(params: DirectorV2PlanRequest): Promise<DirectorV2PlanResponse> {
  const res = await fetch(`${BASE}/api/v1/director/v2/plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Plan failed' }))
    throw new Error(err.detail || 'Director v2 plan failed')
  }
  return res.json()
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

// --- LLM Service ---

export async function fetchLlmStatus(): Promise<import('../types').LlmStatus> {
  const res = await fetch(`${BASE}/api/v1/llm/status`)
  if (!res.ok) throw new Error('Failed to fetch LLM status')
  return res.json()
}

export async function loadLlm(
  params?: { model_id?: string; device?: string }
): Promise<import('../types').LlmStatus & { status: string }> {
  const res = await fetch(`${BASE}/api/v1/llm/load`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params || {}),
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

export async function llmChat(params: {
  workspace: string
  model_id: string
  messages: import('../types').LlmChatMessage[]
  guide_ids: string[]
  image_paths?: string[]
  max_new_tokens?: number
}, signal?: AbortSignal): Promise<{ text: string; model_id: string; guide_ids: string[] }> {
  const request = {
    ...params,
    // Attachment display metadata is browser-local. Only role/content and
    // one-use upload references cross the API boundary.
    messages: params.messages.map(({ role, content }) => ({ role, content })),
  }
  const res = await fetch(`${BASE}/api/v1/llm/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Chat request failed' }))
    throw new Error(err.detail || 'Chat request failed')
  }
  return res.json()
}

export async function llmEnhancePrompt(params: {
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
}): Promise<{ original: string; enhanced: string }> {
  const res = await fetch(`${BASE}/api/v1/llm/enhance-prompt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Enhancement failed' }))
    throw new Error(err.detail || 'Enhancement failed')
  }
  return res.json()
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
  style_prompt: string
  num_angles?: number
}): Promise<{ prompts: string[] }> {
  const res = await fetch(`${BASE}/api/v1/director/plan-angle-prompts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Angle prompt planning failed' }))
    throw new Error(err.detail || 'Angle prompt planning failed')
  }
  return res.json()
}

export async function planClipPrompts(params: {
  clips: import('../types').SuggestedClip[]
  style_prompt: string
  lyrics?: import('../types').LyricSegment[]
  bpm: number
}): Promise<{ prompts: string[] }> {
  const res = await fetch(`${BASE}/api/v1/director/plan-prompts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Prompt planning failed' }))
    throw new Error(err.detail || 'Prompt planning failed')
  }
  return res.json()
}

export async function planClipStructure(params: {
  analysis: import('../types').AudioAnalysisResult
  workspace?: string
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
}): Promise<{ clips: import('../types').PlannedClip[] }> {
  const res = await fetch(`${BASE}/api/v1/audio/plan-structure`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Structure planning failed' }))
    throw new Error(err.detail || 'Structure planning failed')
  }
  return res.json()
}

export async function classifySections(params: {
  analysis: import('../types').AudioAnalysisResult
  workspace?: string
  director_request_id?: string
}): Promise<{
  sections: import('../types').AudioSection[]
  song_structure: { label: string; display_label: string; start: number }[]
  method: 'llm' | 'heuristic'
}> {
  const res = await fetch(`${BASE}/api/v1/director/classify-sections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Classification failed' }))
    throw new Error(err.detail || 'Section classification failed')
  }
  return res.json()
}

export async function planClipPromptsAndImages(params: {
  clips: import('../types').PlannedClip[]
  scene_description: string
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
}): Promise<{ clip_plans: import('../types').ClipPlan[] }> {
  const res = await fetch(`${BASE}/api/v1/director/plan-prompts-and-images`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Prompt and image planning failed' }))
    throw new Error(err.detail || 'Prompt and image planning failed')
  }
  return res.json()
}

// --- Short Film Director ---

export async function planDialogueScenes(params: {
  analysis: import('../types').AudioAnalysisResult
  pacing_bias?: number
  fps?: number
  frames_steps?: number
  frames_minimum?: number
}): Promise<{ clips: import('../types').PlannedClip[] }> {
  const res = await fetch(`${BASE}/api/v1/director/plan-dialogue-scenes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Dialogue scene planning failed' }))
    throw new Error(err.detail || 'Dialogue scene planning failed')
  }
  return res.json()
}

export async function planShortFilmPrompts(params: {
  clips: import('../types').PlannedClip[]
  scene_description: string
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
}): Promise<{ clip_plans: import('../types').ClipPlan[] }> {
  const res = await fetch(`${BASE}/api/v1/director/plan-short-film-prompts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Short film prompt planning failed' }))
    throw new Error(err.detail || 'Short film prompt planning failed')
  }
  return res.json()
}

export async function getLlmStreamStatus(): Promise<{ text: string; done: boolean }> {
  const res = await fetch(`${BASE}/api/v1/llm/stream-status`)
  if (!res.ok) return { text: '', done: true }
  return res.json()
}

export async function planShortFilmScript(params: {
  story_description: string
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
}): Promise<{ clips: import('../types').PlannedClip[]; clip_plans: import('../types').ClipPlan[] }> {
  const res = await fetch(`${BASE}/api/v1/director/plan-short-film-script`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Story planning failed' }))
    throw new Error(err.detail || 'Story planning failed')
  }
  return res.json()
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
  /** True when the user manually overrode CivitAI's NSFW classification
   *  via /api/v1/loras/nsfw-override. */
  nsfw_overridden?: boolean
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
