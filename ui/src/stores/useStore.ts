import { create } from 'zustand'
import type { StoreApi } from 'zustand'
import type { GenerateParams, OutputFile, MediaFilter, OutputArtifactScope, AspectRatio, ResolutionPreset, ScailResolutionProfile, GenerationJob, H3SegmentPlan, H3PlanDecision, H3PerformanceEstimate, H3SegmentCountEstimate, H3PerformanceProfile, H3PerformanceProfileId, ModelFamily, ModelDef, GenerationMode, ModelOptions, SystemConfig, SettingsTab, OutputMetadata, MultiClip, ServicesConfig, HostTermId, HostTermsStatus, LlmStatus, LlmModelOption, AudioAnalysisResult, PlannedClip, ClipPlan, DirectorClipImage, DirectorImageGenProgress, DirectorImageRole, DirectorImageRoleLoraSelection, SpeakerMapping, DirectorSkill, DirectorShotImageGuidance, ShortFilmCharacter, ShortFilmPath, CivitAIModel, CivitAIDownload, PipelineListItem, PipelineRepairState, SavedPipelineState, SystemDetectResponse, SystemStats, RecastCharacterMapping, RepaintRegionMapping, AccountAuthResult, AccountContext, AccountProjectMigrationStatus, AccountSession, AccountSummary, ResponsibleUseProjection, SupportAdminProjection, SupportFulfillmentMutationInput, SupportManualContributionInput, SupportPublicProjection, SupportSelfProjection } from '../types'
import * as api from '../api/client'
import { applyThemePrefs, getStoredPrefs, type FamilyId, type ThemeMode, type ThemePrefs } from '../lib/theme'
import { HOST_TERM_NOTICES } from '../lib/hostTerms'
import { applyH3SegmentCeilingPolicy, hasManualH3SegmentCeiling } from '../lib/h3Submission'
import { alignStudioTotalFrames, alignTotalFrames, controlFpsTotalFrames, effectiveSlidingWindowGeometry, hasGlobalTimeline, usesStudioSegments } from '../lib/timelinePrompt'
import { hidePrivatePreviewsForWorkspace } from '../lib/privatePreview'
import { resolveSidebarNavigation, type ReferenceReturnMode, type SidebarMode } from '../lib/sidebarNavigation'
import {
  H3_STYLE_PREFIX_MIGRATION_KEY,
  H3_STYLE_WORKFLOW_PREF_KEY,
  captureH3StyleWorkflowRequest,
  h3StyleWorkflowSelectionIsCurrent,
  h3StyleWorkflowSupportsModel,
  resolveH3StyleWorkflowRequest,
  stripLegacyH3StylePrefix,
} from '../lib/h3StyleWorkflows'

const CIVIT_DOWNLOAD_POLL_MS = 2000
const CIVIT_DOWNLOAD_COMPLETED_VISIBLE_MS = 30_000
const H3_REF2VA_TERMS_ACK_KEY = 'maestro:minimax-h3-ref2va-terms-v1'
const H3_ATTENTION_ENGINE_KEY = 'maestro:h3-attention-engine'
const H3_STUDIO_MODELS = new Set([
  'minimax_h3',
  'minimax_h3_pinkcherry_fl2va',
  'minimax_h3_w4a8_fl2va',
  'minimax_h3_ref2va',
])
const H3_RESTORABLE_CUSTOM_KEYS = new Set([
  'h3_spectrum_profile',
  'h3_lightx2v_profile',
  'h3_attention_engine',
  'h3_sol_tau',
  'h3_sol_dense_steps',
  'h3_sol_dense_blocks',
  'h3_sol_min_tokens',
  'h3_turbo_profile',
])

type H3AttentionEngine = 'sdpa' | 'sol_attn' | 'sage2'

function _normalizeH3AttentionEngine(value: unknown): H3AttentionEngine {
  return value === 'sdpa' || value === 'sage2' ? value : 'sol_attn'
}

function _restorableH3CustomSettings(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([key]) => H3_RESTORABLE_CUSTOM_KEYS.has(key)),
  )
}

let _h3Ref2VATermsHostAccepted = false

export function h3Ref2VATermsAccepted(): boolean {
  return _h3Ref2VATermsHostAccepted
}

function _clearLegacyH3Ref2VATermsAcceptance(): void {
  try {
    localStorage.removeItem(H3_REF2VA_TERMS_ACK_KEY)
  } catch {
    // Server persistence remains authoritative when browser storage is unavailable.
  }
}

function _setH3Ref2VATermsAccepted(accepted: boolean): void {
  _h3Ref2VATermsHostAccepted = accepted
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('maestro:h3-ref2va-terms-change', { detail: accepted }))
  }
}

let _hostTermsOperationTail: Promise<void> = Promise.resolve()

function _queueHostTermsOperation<T>(operation: () => Promise<T>): Promise<T> {
  const result = _hostTermsOperationTail.then(operation, operation)
  _hostTermsOperationTail = result.then(() => undefined, () => undefined)
  return result
}
let _civitDownloadPollTask: Promise<void> | null = null
let _civitDownloadPollController: AbortController | null = null
let _civitDownloadPollRequested = false
const _civitRefreshedCheckpointDownloads = new Set<string>()
const DIRECTOR_REPAIR_POLL_MS = 2000
const DIRECTOR_REPAIR_ACTIVE = new Set(['queued', 'running', 'cancelling'])
const DIRECTOR_PREPARATION_STORAGE_KEY = 'maestro:director-preparation-v1'
const DIRECTOR_IMAGE_ROLES_STORAGE_KEY = 'maestro:director-image-roles-v1'
const ENHANCE_OPERATION_STORAGE_KEY = 'maestro:prompt-enhance-operations-v2'
const ENHANCE_FINGERPRINT_CLAIM_STORAGE_KEY = 'maestro:prompt-enhance-fingerprint-claim-v1'
const ENHANCE_OPERATION_MAX_AGE_MS = 2 * 60 * 60 * 1000
const ENHANCE_OPERATION_MAX_RECORDS = 8
const ENHANCE_FINGERPRINT_SALT_BYTES = 32
const ENHANCE_FINGERPRINT_TOKEN_BYTES = 32
const ENHANCE_FINGERPRINT_LOCK_TIMEOUT_MS = 1_000
const ENHANCE_LEDGER_LOCK_NAME = 'maestro-prompt-enhance-ledger-v2'
const ENHANCE_LEDGER_LOCK_TIMEOUT_MS = 1_000

interface PersistedDirectorImageRoles {
  schema_version: 1
  creator_model_override: string
  editor_model_override: string
  creator_loras: DirectorImageRoleLoraSelection[]
  editor_loras: DirectorImageRoleLoraSelection[]
}

function _loadDirectorImageRoles(): PersistedDirectorImageRoles | null {
  try {
    const parsed = JSON.parse(localStorage.getItem(DIRECTOR_IMAGE_ROLES_STORAGE_KEY) || 'null')
    if (!parsed || parsed.schema_version !== 1) return null
    return {
      schema_version: 1,
      creator_model_override: typeof parsed.creator_model_override === 'string'
        ? parsed.creator_model_override : '',
      editor_model_override: typeof parsed.editor_model_override === 'string'
        ? parsed.editor_model_override : '',
      creator_loras: Array.isArray(parsed.creator_loras) ? parsed.creator_loras : [],
      editor_loras: Array.isArray(parsed.editor_loras) ? parsed.editor_loras : [],
    }
  } catch {
    return null
  }
}

function _saveDirectorImageRoles(settings: PersistedDirectorImageRoles): void {
  try {
    localStorage.setItem(DIRECTOR_IMAGE_ROLES_STORAGE_KEY, JSON.stringify(settings))
  } catch {
    // Current-session state remains authoritative when storage is unavailable.
  }
}

const _initialDirectorImageRoles = _loadDirectorImageRoles()
const DIRECTOR_PIPELINE_ACTIVE_PHASES = new Set<api.PipelineStatus['phase']>([
  'registered',
  'planning',
  'polishing_prompts',
  'generating_images',
  'generating_video',
  'post_processing',
])
type DirectorRepairPoll = {
  operationId: string
  timer: number | null
}
type ActiveJobPoll = {
  timer: number | null
  wake: () => void
  stop: () => void
}
type TerminalJobWaiter = {
  resolve: (status: api.ApiJobStatus) => void
  reject: (error: Error) => void
  timer: number
}
const _directorRepairPolls = new Map<string, DirectorRepairPoll>()
const _directorRepairDiscoveries = new Map<string, object>()
const _recoveryJobPolls = new Map<string, ActiveJobPoll>()
const _terminalJobWaiters = new Map<string, TerminalJobWaiter>()
let _directorPreparationPoll: ReturnType<typeof setInterval> | null = null
type DirectorCapabilitiesKey = 'standard' | 'explicit'
const _directorCapabilitiesSeq: Record<DirectorCapabilitiesKey, number> = {
  standard: 0,
  explicit: 0,
}
const _directorCapabilitiesInFlight: Partial<Record<DirectorCapabilitiesKey, {
  token: symbol
  promise: Promise<api.DirectorCapabilities>
}>> = {}

function _directorCapabilitiesKey(explicitOutput: boolean): DirectorCapabilitiesKey {
  return explicitOutput ? 'explicit' : 'standard'
}
let _dashboardPipelineLoadToken = 0
let _dashboardPipelineListLoadToken = 0
type DashboardPipelineListRead = {
  workspace: string
  generation: number
  status: 'idle' | 'loading' | 'ready' | 'failed'
}
let _enhanceLlmRequestToken: symbol | null = null
let _enhanceStopWaiting: (() => void) | null = null
let _enhanceWaitSignal: AbortSignal | null = null
let _enhanceSubmissionAttemptedRequestId: string | null = null
let _enhancePromptEditGeneration = 0
let _volatileEnhanceFingerprintSalt: Uint8Array | null = null
let _enhanceFingerprintClaimPromise: Promise<Uint8Array> | null = null
let _enhanceFingerprintClaimRecord: EnhanceFingerprintClaimRecord | null = null
let _enhanceReloadRecoveryAvailable = false
let _enhanceFingerprintClaimRotatedStored = false
const _enhanceFingerprintLockRequests = new Set<Promise<unknown>>()
let _directorLlmRequestToken: symbol | null = null
let _directorPipelineLifecycleToken: symbol | null = null

function _beginWorkspaceLlmRequest(
  workspace: string,
  onLostOwnership?: () => void,
) {
  const controller = new AbortController()
  let ownershipLost = false
  const loseOwnership = () => {
    if (ownershipLost) return
    ownershipLost = true
    controller.abort()
    onLostOwnership?.()
  }
  const unsubscribe = useStore.subscribe(state => {
    if (state.activeWorkspace !== workspace) loseOwnership()
  })
  if (useStore.getState().activeWorkspace !== workspace) loseOwnership()
  return {
    signal: controller.signal,
    ownsWorkspace: () => useStore.getState().activeWorkspace === workspace,
    stopWaiting: loseOwnership,
    dispose: unsubscribe,
  }
}

function _beginEnhanceLlmRequest(workspace: string) {
  // A same-project successor owns the UI and stops the predecessor's browser
  // wait without cancelling its already-admitted server operation.
  _enhanceStopWaiting?.()
  const token = Symbol('enhance-llm-request')
  _enhanceLlmRequestToken = token
  const lifecycle = _beginWorkspaceLlmRequest(workspace, () => {
    if (_enhanceLlmRequestToken !== token) return
    _enhanceLlmRequestToken = null
    _enhanceStopWaiting = null
    _enhanceWaitSignal = null
    _enhanceSubmissionAttemptedRequestId = null
    useStore.setState({
      isEnhancing: false,
      enhanceStatus: null,
      enhanceRequestScope: null,
    })
  })
  _enhanceStopWaiting = lifecycle.stopWaiting
  _enhanceWaitSignal = lifecycle.signal
  return {
    signal: lifecycle.signal,
    ownsWorkspace: () => (
      lifecycle.ownsWorkspace() && _enhanceLlmRequestToken === token
    ),
    dispose: () => {
      if (_enhanceLlmRequestToken === token) _enhanceLlmRequestToken = null
      if (_enhanceStopWaiting === lifecycle.stopWaiting) _enhanceStopWaiting = null
      if (_enhanceWaitSignal === lifecycle.signal) _enhanceWaitSignal = null
      if (_enhanceLlmRequestToken === null) _enhanceSubmissionAttemptedRequestId = null
      lifecycle.dispose()
    },
  }
}

function _beginDirectorLlmRequest(workspace: string) {
  const token = Symbol('director-llm-request')
  _directorLlmRequestToken = token
  const previousStep = useStore.getState().directorStep
  const lifecycle = _beginWorkspaceLlmRequest(workspace, () => {
    if (_directorLlmRequestToken !== token) return
    _directorLlmRequestToken = null
    useStore.setState({
      directorLoading: false,
      directorLoadingMessage: null,
      directorStep: previousStep,
    })
  })
  return {
    signal: lifecycle.signal,
    ownsWorkspace: () => (
      lifecycle.ownsWorkspace() && _directorLlmRequestToken === token
    ),
    dispose: () => {
      if (_directorLlmRequestToken === token) _directorLlmRequestToken = null
      lifecycle.dispose()
    },
  }
}

function _beginDirectorPipelineLifecycle(workspace: string) {
  const token = Symbol('director-pipeline-lifecycle')
  _directorPipelineLifecycleToken = token
  const lifecycle = _beginWorkspaceLlmRequest(workspace)
  return {
    ownsWorkspace: () => (
      lifecycle.ownsWorkspace() && _directorPipelineLifecycleToken === token
    ),
    dispose: () => {
      if (_directorPipelineLifecycleToken === token) {
        _directorPipelineLifecycleToken = null
      }
      lifecycle.dispose()
    },
  }
}

function _isBrowserAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}
const ACTIVE_GENERATION_JOB_STATUSES = new Set<GenerationJob['status']>([
  'preparing',
  'waiting_for_plan_approval',
  'queued',
  'running',
])
let _h3PlanReviewSequence = 0
let _workspaceLoadSequence = 0

function _isActiveGenerationJob(job: Pick<GenerationJob, 'status'>): boolean {
  return ACTIVE_GENERATION_JOB_STATUSES.has(job.status)
}

function _discardStaleGenerationPlaceholder(placeholder: GenerationJob): void {
  useStore.setState(state => {
    if (!state.jobs.includes(placeholder)) return state
    const jobs = state.jobs.filter(job => job !== placeholder)
    return { jobs, isGenerating: jobs.some(_isActiveGenerationJob) }
  })
}

type StoredDirectorPreparation = { requestId: string; workspace: string }

type StoredEnhanceOperation = api.LlmEnhanceOperationScope & {
  accountFingerprint: string
  claimToken: string
  settingsFingerprint: string
  storedAt: number
}

type EnhanceFingerprintClaimRecord = {
  schemaVersion: 1
  token: string
  salt: string
}

function _boundedEnhanceFingerprint(value: unknown): string {
  const text = JSON.stringify(value)
  let left = 0x811c9dc5
  let right = 0x9e3779b9
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index)
    left = Math.imul(left ^ code, 0x01000193)
    right = Math.imul(right ^ code, 0x85ebca6b)
  }
  return `${(left >>> 0).toString(16).padStart(8, '0')}${(right >>> 0).toString(16).padStart(8, '0')}`
}

function _validStoredEnhanceOperation(value: unknown): value is StoredEnhanceOperation {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const parsed = value as Record<string, unknown>
  return (
    Object.keys(parsed).length === 7
    && Object.keys(parsed).every(key => [
      'requestId', 'workspace', 'projectInstance', 'accountFingerprint',
      'claimToken', 'settingsFingerprint', 'storedAt',
    ].includes(key))
    && typeof parsed.requestId === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(parsed.requestId)
    && typeof parsed.workspace === 'string'
    && parsed.workspace.length > 0
    && parsed.workspace.length <= 255
    && typeof parsed.projectInstance === 'string'
    && /^[0-9a-f]{64}$/i.test(parsed.projectInstance)
    && typeof parsed.accountFingerprint === 'string'
    && /^[0-9a-f]{16}$/i.test(parsed.accountFingerprint)
    && typeof parsed.claimToken === 'string'
    && /^[0-9a-f]{64}$/i.test(parsed.claimToken)
    && typeof parsed.settingsFingerprint === 'string'
    && /^[0-9a-f]{64}$/i.test(parsed.settingsFingerprint)
    && typeof parsed.storedAt === 'number'
    && Number.isFinite(parsed.storedAt)
    && parsed.storedAt >= Date.now() - ENHANCE_OPERATION_MAX_AGE_MS
    && parsed.storedAt <= Date.now() + 60_000
  )
}

function _loadStoredEnhanceOperations(): StoredEnhanceOperation[] {
  try {
    if (typeof localStorage === 'undefined') return []
    const parsed = JSON.parse(localStorage.getItem(ENHANCE_OPERATION_STORAGE_KEY) || 'null')
    if (
      !parsed
      || typeof parsed !== 'object'
      || Array.isArray(parsed)
      || parsed.schemaVersion !== 2
      || !Array.isArray(parsed.operations)
      || parsed.operations.length > ENHANCE_OPERATION_MAX_RECORDS
      || !parsed.operations.every(_validStoredEnhanceOperation)
    ) {
      return []
    }
    return [...parsed.operations].sort((left, right) => right.storedAt - left.storedAt)
  } catch {
    return []
  }
}

function _writeStoredEnhanceOperations(operations: StoredEnhanceOperation[]): boolean {
  try {
    if (typeof localStorage === 'undefined') return false
    if (operations.length === 0) {
      localStorage.removeItem(ENHANCE_OPERATION_STORAGE_KEY)
      return localStorage.getItem(ENHANCE_OPERATION_STORAGE_KEY) === null
    }
    const encoded = JSON.stringify({
      schemaVersion: 2,
      operations: operations
        .sort((left, right) => right.storedAt - left.storedAt)
        .slice(0, ENHANCE_OPERATION_MAX_RECORDS),
    })
    localStorage.setItem(ENHANCE_OPERATION_STORAGE_KEY, encoded)
    return localStorage.getItem(ENHANCE_OPERATION_STORAGE_KEY) === encoded
  } catch {
    return false
  }
}

function _ownedEnhanceFingerprintClaimToken(): string | null {
  if (!_enhanceReloadRecoveryAvailable) return null
  return _enhanceFingerprintClaimRecord?.token ?? null
}

function _realmOwnsStoredEnhanceOperation(operation: StoredEnhanceOperation): boolean {
  const claimToken = _ownedEnhanceFingerprintClaimToken()
  return claimToken !== null && operation.claimToken === claimToken
}

async function _runEnhanceLedgerMutation(
  mutation: () => boolean,
  signal?: AbortSignal,
): Promise<boolean> {
  const locks = globalThis.navigator?.locks
  if (!locks?.request || signal?.aborted) return false
  const controller = new AbortController()
  const onAbort = () => controller.abort()
  signal?.addEventListener('abort', onAbort, { once: true })
  const timer = globalThis.setTimeout(
    () => controller.abort(),
    ENHANCE_LEDGER_LOCK_TIMEOUT_MS,
  )
  try {
    return await locks.request(
      ENHANCE_LEDGER_LOCK_NAME,
      { mode: 'exclusive', signal: controller.signal },
      lock => Boolean(lock) && mutation(),
    )
  } catch {
    return false
  } finally {
    globalThis.clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
  }
}

async function _storeEnhanceOperation(
  operation: Omit<StoredEnhanceOperation, 'claimToken'>,
  signal?: AbortSignal,
): Promise<boolean> {
  // Persist only bounded recovery fences. Prompt/partial text, images, model
  // and provider names, credentials, and raw settings remain in memory.
  const claimToken = _ownedEnhanceFingerprintClaimToken()
  if (!claimToken) return false
  return _runEnhanceLedgerMutation(() => {
    const existing = _loadStoredEnhanceOperations()
    const sameRequest = existing.find(item => item.requestId === operation.requestId)
    if (sameRequest && sameRequest.claimToken !== claimToken) return false
    const remaining = existing.filter(item => item.requestId !== operation.requestId)
    // No realm may silently evict another bounded recovery fence. The current
    // browser wait remains valid, but reload recovery is unavailable until this
    // exact record can be durably represented.
    if (!sameRequest && remaining.length >= ENHANCE_OPERATION_MAX_RECORDS) return false
    return _writeStoredEnhanceOperations([{ ...operation, claimToken }, ...remaining])
  }, signal)
}

function _hasOwnedStoredEnhanceOperation(scope: api.LlmEnhanceOperationScope): boolean {
  return _loadStoredEnhanceOperations().some(item => (
    _sameEnhanceScope(item, scope) && _realmOwnsStoredEnhanceOperation(item)
  ))
}

async function _removeStoredEnhanceOperation(
  scope: api.LlmEnhanceOperationScope,
): Promise<boolean> {
  return _runEnhanceLedgerMutation(() => {
    const stored = _loadStoredEnhanceOperations()
    const matching = stored.find(item => _sameEnhanceScope(item, scope))
    if (!matching || !_realmOwnsStoredEnhanceOperation(matching)) return false
    return _writeStoredEnhanceOperations(stored.filter(item => item !== matching))
  })
}

async function _clearStoredEnhanceOperations(): Promise<boolean> {
  return _runEnhanceLedgerMutation(() => {
    const stored = _loadStoredEnhanceOperations()
    const retained = stored.filter(item => !_realmOwnsStoredEnhanceOperation(item))
    if (retained.length === stored.length) return false
    return _writeStoredEnhanceOperations(retained)
  })
}

function _findStoredEnhanceOperation(
  workspace: string,
  accountFingerprint: string,
): StoredEnhanceOperation | null {
  const accountOperations = _loadStoredEnhanceOperations().filter(item => (
    item.accountFingerprint === accountFingerprint
    && item.workspace === workspace
  ))
  return accountOperations.find(_realmOwnsStoredEnhanceOperation)
    ?? accountOperations[0]
    ?? null
}

function _sameEnhanceScope(
  left: api.LlmEnhanceOperationScope | null,
  right: api.LlmEnhanceOperationScope,
): boolean {
  return Boolean(
    left
    && left.requestId === right.requestId
    && left.workspace === right.workspace
    && left.projectInstance === right.projectInstance,
  )
}

function _terminalEnhanceStatus(
  status: api.LlmEnhanceOperationStatus,
): api.LlmEnhanceOperationStatus {
  if (status.status !== 'cancelled' && status.status !== 'failed') return status
  return {
    ...status,
    partial_text: '',
    generated_tokens_approx: 0,
    elapsed_seconds: 0,
    live_tps: null,
    average_tps: null,
  }
}

function _loadStoredDirectorPreparation(): StoredDirectorPreparation | null {
  try {
    if (typeof localStorage === 'undefined') return null
    const parsed = JSON.parse(localStorage.getItem(DIRECTOR_PREPARATION_STORAGE_KEY) || 'null')
    if (
      parsed
      && typeof parsed.requestId === 'string'
      && /^[0-9a-f]{32}$/i.test(parsed.requestId)
      && typeof parsed.workspace === 'string'
      && parsed.workspace
    ) return { requestId: parsed.requestId, workspace: parsed.workspace }
  } catch { /* storage is optional */ }
  return null
}

function _storeDirectorPreparation(requestId: string | null, workspace: string | null): void {
  try {
    if (typeof localStorage === 'undefined') return
    if (requestId && workspace) {
      localStorage.setItem(
        DIRECTOR_PREPARATION_STORAGE_KEY,
        JSON.stringify({ requestId, workspace }),
      )
    } else {
      localStorage.removeItem(DIRECTOR_PREPARATION_STORAGE_KEY)
    }
  } catch { /* in-memory state remains usable */ }
}

const _storedDirectorPreparation = _loadStoredDirectorPreparation()

function _stopDirectorPreparationPoll(): void {
  if (_directorPreparationPoll !== null) {
    clearInterval(_directorPreparationPoll)
    _directorPreparationPoll = null
  }
}

type OutpaintAspect = 'source' | '16:9' | '9:16' | '1:1' | '4:3' | '3:4'

const _OUTPAINT_ASPECT_RATIOS: Array<[Exclude<OutpaintAspect, 'source'>, number]> = [
  ['16:9', 16 / 9],
  ['9:16', 9 / 16],
  ['1:1', 1],
  ['4:3', 4 / 3],
  ['3:4', 3 / 4],
]

function _inferOutpaintAspect(width: number, height: number): OutpaintAspect | null {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null
  const ratio = width / height
  let nearest: Exclude<OutpaintAspect, 'source'> | null = null
  let nearestError = Number.POSITIVE_INFINITY
  for (const [aspect, target] of _OUTPAINT_ASPECT_RATIOS) {
    const relativeError = Math.abs(ratio - target) / target
    if (relativeError < nearestError) {
      nearest = aspect
      nearestError = relativeError
    }
  }
  // Grid alignment can move either dimension by several pixels. Four percent
  // safely recognizes those canvases without pretending an arbitrary ratio
  // is one of the six choices supported by the composer.
  return nearestError <= 0.04 ? nearest : null
}

function _repairNeedsPolling(repair: PipelineRepairState | null | undefined): boolean {
  return !!repair && DIRECTOR_REPAIR_ACTIVE.has(repair.status)
}

export function isDirectorPipelineActive(
  status: api.PipelineStatus | null | undefined,
): boolean {
  const activeStatus = status?.status === 'running'
    || (status?.status === 'queued' && status.phase === 'registered')
  return activeStatus && DIRECTOR_PIPELINE_ACTIVE_PHASES.has(status.phase)
}

function _stopDirectorRepairPoll(pid: string): void {
  const poll = _directorRepairPolls.get(pid)
  if (poll?.timer != null) window.clearTimeout(poll.timer)
  _directorRepairPolls.delete(pid)
}

function _downloadTimestampMs(value: number | null | undefined): number | null {
  const timestamp = Number(value)
  if (!Number.isFinite(timestamp) || timestamp <= 0) return null
  return timestamp < 1_000_000_000_000 ? timestamp * 1000 : timestamp
}

function _downloadNeedsPolling(download: CivitAIDownload, now: number): boolean {
  if (download.status === 'downloading') return true
  if (download.status !== 'completed') return false
  const completedAt = _downloadTimestampMs(download.completed_at)
  return completedAt !== null && now - completedAt < CIVIT_DOWNLOAD_COMPLETED_VISIBLE_MS
}

type H3EstimateState = {
  params: GenerateParams
  durationSeconds: number
  slidingWindowSeconds: number
  slidingWindowOverlap: number
  slidingWindowLocked: boolean
  spatialUpsampling: string
  startImage: unknown
  endImage: unknown
  imageRefs: unknown[]
  explicitOutput: boolean
}

function _buildH3EstimateRequest(
  state: H3EstimateState,
  modelType = state.params.model_type,
): import('../types').H3EstimateRequest {
  const semanticImages = Math.max(
    state.imageRefs.length,
    Array.isArray(state.params.image_refs) ? state.params.image_refs.length : 0,
  )
  return {
    model_type: modelType,
    duration_seconds: state.durationSeconds,
    window_seconds: state.slidingWindowSeconds,
    window_overlap: state.slidingWindowOverlap,
    prompt: String(state.params.prompt || ''),
    h3_adaptive_conditioning: state.params.h3_adaptive_conditioning !== false,
    manual_segment_ceiling: state.slidingWindowLocked,
    num_inference_steps: state.params.num_inference_steps,
    resolution: state.params.resolution,
    custom_settings: { ...(state.params.custom_settings || {}) },
    activated_loras: [...(state.params.activated_loras || [])],
    loras_multipliers: state.params.loras_multipliers || '',
    tea_cache: state.params.tea_cache,
    spatial_upsampling: state.spatialUpsampling,
    delivery_resolution: state.params.delivery_resolution,
    delivery_fit: state.params.delivery_fit,
    reference_shape: {
      has_start: !!(state.startImage || state.params.image_start),
      has_end: !!(state.endImage || state.params.image_end),
      image_count: semanticImages,
      video_count: [state.params.video_guide, state.params.video_guide2, state.params.video_guide3].filter(Boolean).length,
      audio_count: [state.params.audio_guide, state.params.audio_guide2, state.params.audio_guide3].filter(Boolean).length,
    },
    explicit_output: state.explicitOutput,
  }
}

function _stableJson(value: unknown): string {
  if (!value || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(_stableJson).join(',')}]`
  return `{${Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => `${JSON.stringify(key)}:${_stableJson(item)}`)
    .join(',')}}`
}

const H3_SOL_DEFAULTS: Record<string, number> = {
  h3_sol_tau: 1,
  h3_sol_dense_steps: 10,
  h3_sol_dense_blocks: 2,
  h3_sol_min_tokens: 4096,
}

function _canonicalH3ProfileCustomSettings(value: unknown): Record<string, unknown> {
  const normalized = _restorableH3CustomSettings(value)
  const engine = normalized.h3_attention_engine || 'sol_attn'
  if (engine === 'sol_attn') {
    for (const [key, defaultValue] of Object.entries(H3_SOL_DEFAULTS)) {
      if (normalized[key] === undefined) normalized[key] = defaultValue
    }
  } else {
    for (const key of Object.keys(H3_SOL_DEFAULTS)) delete normalized[key]
  }
  if (!normalized.h3_turbo_profile) delete normalized.h3_turbo_profile
  return normalized
}

export function h3ProfileMatches(
  profile: H3PerformanceProfile | undefined,
  params: GenerateParams,
  loraWeights: Record<string, number[]>,
  spatialUpsampling: string,
): boolean {
  if (!profile) return false
  const settings = profile.settings
  return (
    params.model_type === settings.model_type
    && params.num_inference_steps === settings.num_inference_steps
    && params.resolution === settings.resolution
    && _stableJson(_canonicalH3ProfileCustomSettings(params.custom_settings))
      === _stableJson(_canonicalH3ProfileCustomSettings(settings.custom_settings))
    && _stableJson(params.activated_loras || []) === _stableJson(settings.activated_loras || [])
    && (params.loras_multipliers || '') === (settings.loras_multipliers || '')
    && params.tea_cache === settings.tea_cache
    && spatialUpsampling === (settings.spatial_upsampling || '')
    && (params.delivery_resolution || '') === (settings.delivery_resolution || '')
    && (params.delivery_fit || '') === (settings.delivery_fit || '')
    && _stableJson(loraWeights) === _stableJson(settings.lora_weights || {})
  )
}

function _h3EstimateTotalSeconds(
  estimate: H3PerformanceEstimate | null | undefined,
): number | null {
  if (!estimate) return null
  const runSeconds = Number(estimate.seconds)
  if (!Number.isFinite(runSeconds) || runSeconds <= 0) return null
  const loadSeconds = estimate.model_load_state === 'resident'
    ? 0
    : Number(estimate.model_load_seconds || 0)
  const total = runSeconds + (
    Number.isFinite(loadSeconds) && loadSeconds > 0 ? loadSeconds : 0
  )
  return Number.isFinite(total) && total > 0 ? total : null
}

const RESOURCE_EXECUTION_STATES = new Set<api.ResourceExecutionState>([
  'queued',
  'admitted',
  'running',
  'preemption_requested',
  'resources_releasing',
  'restarting_on_accelerator',
  'blocked',
  'released',
])

function _normalizeResourceDescriptor(
  value: api.ResourceDescriptor | null | undefined,
  jobStatus: string,
): api.ResourceDescriptor | null | undefined {
  if (value === undefined || value === null) return value
  const raw = value as Partial<api.ResourceDescriptor>
  const intent: api.ResourceIntent = raw.intent === 'text' ? 'text' : 'generation'
  const execution: api.ResourceExecution = intent === 'text' && raw.execution === 'cpu'
    ? 'cpu'
    : 'standard'
  const preemptionMode: api.ResourcePreemptionMode = intent === 'text'
    && raw.preemption_mode === 'discard_restart'
    ? 'discard_restart'
    : 'none'
  const inferredState: api.ResourceExecutionState = jobStatus === 'running'
    ? 'running'
    : jobStatus === 'completed' || jobStatus === 'failed' || jobStatus === 'cancelled'
      ? 'released'
      : 'queued'
  const state = RESOURCE_EXECUTION_STATES.has(raw.state as api.ResourceExecutionState)
    ? raw.state as api.ResourceExecutionState
    : inferredState
  const executionAttempt = Number.isInteger(raw.execution_attempt)
    && Number(raw.execution_attempt) >= 1
    && Number(raw.execution_attempt) <= 1_000_000
    ? Number(raw.execution_attempt)
    : 1
  const liveCpuAttempt = intent === 'text'
    && execution === 'cpu'
    && preemptionMode === 'discard_restart'
    && (
      state === 'admitted'
      || state === 'running'
      || state === 'preemption_requested'
      || state === 'resources_releasing'
    )
  return {
    intent,
    execution,
    preemptible: liveCpuAttempt && raw.preemptible === true,
    preemption_mode: preemptionMode,
    state,
    execution_attempt: executionAttempt,
  }
}

function _isStaleResourceAttempt(
  descriptor: api.ResourceDescriptor | null | undefined,
  previous?: GenerationJob,
): boolean {
  if (!descriptor || !previous?.resourceDescriptor) return false
  return descriptor.execution_attempt < previous.resourceDescriptor.execution_attempt
}

function _resourceProgressMustReset(
  descriptor: api.ResourceDescriptor | null | undefined,
  previous?: GenerationJob,
): boolean {
  if (!descriptor) return false
  if (descriptor.state === 'resources_releasing' || descriptor.state === 'restarting_on_accelerator') {
    return true
  }
  const previousDescriptor = previous?.resourceDescriptor
  const previousAttempt = previous?.resourceDescriptor?.execution_attempt ?? 1
  return descriptor.execution_attempt > previousAttempt && (
    descriptor.preemption_mode === 'discard_restart'
    || previousDescriptor?.preemption_mode === 'discard_restart'
  )
}

function _jobStatusDetails(
  status: api.ApiJobStatus,
  previous?: GenerationJob,
): Partial<GenerationJob> {
  const submittedEstimate = status.h3_estimate || previous?.h3Estimate || null
  const estimatedTotal = _h3EstimateTotalSeconds(submittedEstimate)
  const resourceDescriptor = _normalizeResourceDescriptor(status.resource_descriptor, status.status)
  const resetDiscardedProgress = _resourceProgressMustReset(resourceDescriptor, previous)
  const exactTextEta = resourceDescriptor?.intent === 'text'
  return {
    createdAt: status.created_at,
    promptPreview: status.status === 'preparing' || status.status === 'waiting_for_plan_approval'
      ? ''
      : status.prompt_preview,
    activeWindowPrompt: status.status === 'preparing' || status.status === 'waiting_for_plan_approval'
      ? ''
      : status.active_window_prompt,
    modelType: status.model_type,
    generationMode: status.generation_mode,
    workspace: status.workspace,
    windowCurrent: status.window_current,
    windowTotal: status.window_total,
    windowStep: status.window_step,
    windowTotalSteps: status.window_total_steps,
    windowProgress: status.window_progress,
    overallProgress: status.overall_progress,
    progressIndeterminate: status.status === 'running' && status.progress_indeterminate === true,
    queueWaitReason: status.queue_wait_reason,
    ...(resourceDescriptor !== undefined
      ? { resourceDescriptor }
      : {}),
    ...(status.parent_job_id !== undefined
      ? { parentJobId: status.parent_job_id }
      : {}),
    ...(status.logical_job_kind !== undefined
      ? { logicalJobKind: status.logical_job_kind }
      : {}),
    ...(status.failed_child_job_id !== undefined
      ? { failedChildJobId: status.failed_child_job_id }
      : {}),
    ...(status.failed_child_status !== undefined
      ? { failedChildStatus: status.failed_child_status }
      : {}),
    ...(status.failed_child_reason !== undefined
      ? { failedChildReason: status.failed_child_reason }
      : {}),
    ...(status.failure_details !== undefined
      ? {
          failureDetails: status.failure_details == null
            ? null
            : {
                ...(typeof status.failure_details.code === 'string'
                  ? { code: status.failure_details.code }
                  : {}),
                ...(typeof status.failure_details.detail === 'string'
                  ? { detail: status.failure_details.detail }
                  : {}),
              },
        }
      : {}),
    h3SegmentPlan: status.h3_segment_plan,
    planReviewRequired: status.plan_review_required === true,
    ...(status.plan_review_terms_required != null
      ? { planReviewTermsRequired: status.plan_review_terms_required === true }
      : {}),
    planReviewDeadline: status.plan_review_deadline ?? null,
    currentSegmentModel: status.current_segment_model,
    currentSegmentReason: status.current_segment_reason,
    currentSegmentBoundary: status.current_segment_boundary,
    ...(submittedEstimate ? { h3Estimate: submittedEstimate } : {}),
    etaSeconds: resetDiscardedProgress
      ? null
      : status.status === 'running'
        ? (exactTextEta ? status.eta_seconds ?? null : status.eta_seconds ?? previous?.etaSeconds ?? estimatedTotal)
      : status.status === 'queued'
        ? (exactTextEta ? status.eta_seconds ?? null : previous?.etaSeconds ?? estimatedTotal)
        : null,
    subtaskEtaSeconds: status.status === 'running' && !resetDiscardedProgress
      ? (status.subtask_eta_seconds ?? null)
      : null,
    recoveryState: status.recovery_state ?? null,
    recoveryInterrupted: status.recovery_interrupted === true,
    recoveryBlocked: status.recovery_blocked === true,
    recoveryAttempt: status.recovery_attempt ?? 0,
    recoveryAttemptLimit: status.recovery_attempt_limit ?? 0,
    recoveryRerunsDenoise: status.recovery_reruns_denoise === true,
    recoveryReason: status.recovery_reason ?? null,
    recoveryReasonText: status.recovery_reason_text ?? null,
    recoveryActionable: status.recovery_actionable === true,
    recoveryActions: status.recovery_actions || [],
    estimateAfterResume: status.estimate_after_resume ?? null,
    logEvents: status.events,
  }
}

function _mergeJobStatus(job: GenerationJob, status: api.ApiJobStatus): GenerationJob {
  const resourceDescriptor = _normalizeResourceDescriptor(status.resource_descriptor, status.status)
  if (_isStaleResourceAttempt(resourceDescriptor, job)) return job
  const resetDiscardedProgress = _resourceProgressMustReset(resourceDescriptor, job)
  return {
    ...job,
    status: status.status,
    progress: resetDiscardedProgress ? 0 : status.progress / 100,
    step: resetDiscardedProgress ? 0 : status.step,
    totalSteps: resetDiscardedProgress ? 0 : status.total_steps,
    phase: resetDiscardedProgress ? '' : status.phase,
    message: status.message,
    outputFiles: status.output_files,
    error: status.error,
    oomInfo: status.oom_info ?? null,
    ..._jobStatusDetails(status, job),
    ...(resetDiscardedProgress ? {
      windowStep: 0,
      windowTotalSteps: 0,
      windowProgress: 0,
      overallProgress: 0,
      progressIndeterminate: true,
    } : {}),
  }
}

function _newGenerationJobFromStatus(status: api.ApiJobStatus): GenerationJob {
  return _mergeJobStatus({
    id: status.job_id,
    status: status.status,
    progress: 0,
    step: 0,
    totalSteps: 0,
    phase: '',
    message: '',
    outputFiles: [],
    error: null,
  }, status)
}

const ACTIVE_JOB_STATUS_POLL_MS = 2_000
const QUEUED_JOB_STATUS_SAFETY_MS = 300_000
const ACTIVE_OUTPUT_REFRESH_MIN_MS = 15_000
const ACTIVE_OUTPUT_REFRESH_SAFETY_MS = 30_000

function _waitForTerminalJobStatus(jobId: string, timeoutMs: number): Promise<api.ApiJobStatus> {
  return new Promise((resolve, reject) => {
    const previous = _terminalJobWaiters.get(jobId)
    if (previous) {
      window.clearTimeout(previous.timer)
      previous.reject(new Error(`Job ${jobId} acquired a replacement completion waiter`))
    }
    const timer = window.setTimeout(() => {
      _terminalJobWaiters.delete(jobId)
      reject(new Error(`Job ${jobId} timed out`))
    }, timeoutMs)
    _terminalJobWaiters.set(jobId, { resolve, reject, timer })
  })
}

function _publishTerminalJobStatus(status: api.ApiJobStatus): void {
  if (
    status.status !== 'completed'
    && status.status !== 'failed'
    && status.status !== 'cancelled'
  ) return
  const waiter = _terminalJobWaiters.get(status.job_id)
  if (!waiter) return
  _terminalJobWaiters.delete(status.job_id)
  window.clearTimeout(waiter.timer)
  waiter.resolve(status)
}

function _rejectTerminalJobWaiter(jobId: string, message: string): void {
  const waiter = _terminalJobWaiters.get(jobId)
  if (!waiter) return
  _terminalJobWaiters.delete(jobId)
  window.clearTimeout(waiter.timer)
  waiter.reject(new Error(message))
}

function _jobNeedsFastStatusPoll(job: GenerationJob): boolean {
  return job.status === 'preparing'
    || job.status === 'waiting_for_plan_approval'
    || job.status === 'running'
    || job.recoveryState === 'retrying'
}

function _queueJobDetails(
  status: api.QueueJobState,
  previous?: GenerationJob,
): Partial<GenerationJob> {
  const resourceDescriptor = _normalizeResourceDescriptor(status.resource_descriptor, status.status)
  if (_isStaleResourceAttempt(resourceDescriptor, previous)) return {}
  const resetDiscardedProgress = _resourceProgressMustReset(resourceDescriptor, previous)
  return {
    status: status.status,
    queueWaitReason: status.wait_reason,
    ...(resourceDescriptor !== undefined
      ? { resourceDescriptor }
      : {}),
    ...(status.parent_job_id !== undefined
      ? { parentJobId: status.parent_job_id }
      : {}),
    ...(status.logical_job_kind !== undefined
      ? { logicalJobKind: status.logical_job_kind }
      : {}),
    planReviewRequired: status.status === 'waiting_for_plan_approval',
    planReviewTermsRequired: status.plan_review_terms_required === true,
    planReviewDeadline: status.plan_review_deadline ?? null,
    etaSeconds: resetDiscardedProgress ? null : status.eta_seconds ?? null,
    subtaskEtaSeconds: status.status === 'running' && !resetDiscardedProgress
      ? (status.subtask_eta_seconds ?? null)
      : null,
    ...(resetDiscardedProgress ? {
      progress: 0,
      step: 0,
      totalSteps: 0,
      phase: '',
      windowStep: 0,
      windowTotalSteps: 0,
      windowProgress: 0,
      overallProgress: 0,
      progressIndeterminate: true,
    } : {}),
    recoveryState: status.recovery_state ?? null,
    recoveryInterrupted: status.recovery_interrupted === true,
    recoveryBlocked: status.recovery_blocked === true,
    recoveryAttempt: status.recovery_attempt ?? 0,
    recoveryAttemptLimit: status.recovery_attempt_limit ?? 0,
    recoveryRerunsDenoise: status.recovery_reruns_denoise === true,
    recoveryReason: status.recovery_reason ?? null,
    recoveryReasonText: status.recovery_reason_text ?? null,
    recoveryActionable: status.recovery_actionable === true,
    recoveryActions: status.recovery_actions || [],
    estimateAfterResume: status.estimate_after_resume ?? null,
  }
}

type ActiveOutputRefreshTracker = {
  outputSignature: string
  phase: string
  lastRefreshAt: number
  pendingDelta: boolean
  hasRefreshed: boolean
}

type GalleryRefreshClock = Pick<
  ActiveOutputRefreshTracker,
  'lastRefreshAt' | 'pendingDelta' | 'hasRefreshed'
>

function _coalescedGalleryRefreshDue(
  tracker: GalleryRefreshClock,
  changed: boolean,
  visible: boolean,
  now = Date.now(),
): boolean {
  tracker.pendingDelta = tracker.pendingDelta || changed
  const elapsed = now - tracker.lastRefreshAt
  const coalescedDeltaDue = tracker.pendingDelta
    && (!tracker.hasRefreshed || elapsed >= ACTIVE_OUTPUT_REFRESH_MIN_MS)
  const safetyRefreshDue = elapsed >= ACTIVE_OUTPUT_REFRESH_SAFETY_MS
  if (!visible || (!coalescedDeltaDue && !safetyRefreshDue)) return false
  tracker.lastRefreshAt = now
  tracker.pendingDelta = false
  tracker.hasRefreshed = true
  return true
}

function _activeOutputSignature(status: api.ApiJobStatus): string {
  return `${status.produced_outputs}:${JSON.stringify(status.output_files)}`
}

function _createActiveOutputRefreshTracker(
  job: Pick<GenerationJob, 'outputFiles' | 'phase'>,
  now = Date.now(),
): ActiveOutputRefreshTracker {
  return {
    outputSignature: `0:${JSON.stringify(job.outputFiles)}`,
    phase: job.phase,
    lastRefreshAt: now,
    pendingDelta: false,
    hasRefreshed: false,
  }
}

function _activeOutputRefreshDue(
  tracker: ActiveOutputRefreshTracker,
  status: api.ApiJobStatus,
  visible: boolean,
  now = Date.now(),
): boolean {
  const outputSignature = _activeOutputSignature(status)
  const outputChanged = outputSignature !== tracker.outputSignature
  const phasePublished = !!status.phase && status.phase !== tracker.phase
  tracker.outputSignature = outputSignature
  tracker.phase = status.phase
  return status.status === 'running' && _coalescedGalleryRefreshDue(
    tracker,
    outputChanged || phasePublished,
    visible,
    now,
  )
}

function _waitForDownloadPoll(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    if (signal.aborted) {
      resolve()
      return
    }
    const timer = window.setTimeout(done, ms)
    function done() {
      window.clearTimeout(timer)
      signal.removeEventListener('abort', done)
      resolve()
    }
    signal.addEventListener('abort', done, { once: true })
  })
}

// Vite can replace this module without a full page unload. Abort the old
// async loop so HMR never leaves an orphaned polling timer behind.
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    _civitDownloadPollController?.abort()
    _civitDownloadPollController = null
    _civitDownloadPollTask = null
    _civitDownloadPollRequested = false
    _civitRefreshedCheckpointDownloads.clear()
    for (const pid of _directorRepairPolls.keys()) {
      _stopDirectorRepairPoll(pid)
    }
    _directorRepairDiscoveries.clear()
    for (const poll of [..._recoveryJobPolls.values()]) {
      poll.stop()
    }
    for (const jobId of [..._terminalJobWaiters.keys()]) {
      _rejectTerminalJobWaiter(jobId, 'UI reloaded while waiting for generation')
    }
  })
}

// --- LocalStorage persistence for per-mode settings ---
const STORAGE_KEY = 'maestro_mode_settings'

// Persistence schema version. Bump when changing the LoRA-key strategy or
// adding fields that need migration. Currently:
//   v1: savedLoraPerMode is keyed by lora_id (e.g. `civitai:12345`) instead
//       of filename, so settings survive LoRA version bumps. A snapshot of
//       lora_id → filename at save time is embedded for fast load-time
//       translation; reconciliation against the fresh map (fetched from
//       /api/v1/loras/installed) happens after boot in `loadModels()`.
const _PERSIST_VERSION = 1

type LoraModeBlob = { activated_loras: string[]; loras_multipliers: string; loraWeights: Record<string, number[]>; availableLoras: string[] }

/** Per-mode params snapshot stored in localStorage. Holds whatever
 *  GenerateParams the user had set in that mode, plus a couple of
 *  top-level store fields (filmGrain*) that conceptually belong to
 *  the mode but live outside `params`. Each mode keeps its own
 *  complete snapshot so settings don't leak between modes — this
 *  fixed bugs where e.g. `repeat_generation: 10` set in image mode
 *  would queue up 10 videos when the user switched to video mode,
 *  or `video_prompt_type: 'KFI'` (frames injection) would persist
 *  on a mode where it didn't apply. Partial<GenerateParams> because
 *  the user almost never sets every field. */
type SavedModeParams = Partial<GenerateParams> & {
  filmGrainIntensity?: number
  filmGrainSaturation?: number
  /** Top-level store field (NOT in GenerateParams), saved per-mode so
   *  audio's 600/1800 slider.max doesn't leak into video on mode switch.
   *  See setGenerationMode for the save/restore wiring. */
  durationSeconds?: number
}

function _snapshotModeParams(params: GenerateParams): SavedModeParams {
  const snapshot: SavedModeParams = { ...params }
  delete snapshot.model_type
  delete snapshot.prompt
  delete snapshot.activated_loras
  delete snapshot.loras_multipliers
  return snapshot
}

function _restoreModeParams(snapshot?: SavedModeParams): SavedModeParams {
  const restored = { ...(snapshot || {}) }
  delete restored.filmGrainIntensity
  delete restored.filmGrainSaturation
  delete restored.durationSeconds
  return restored
}

interface PersistedModeSettings {
  generationMode: GenerationMode
  selectedModelPerMode: Partial<Record<GenerationMode, string>>
  savedParamsPerMode: Partial<Record<GenerationMode, SavedModeParams>>
  /** Runtime shape (filename-keyed). The on-disk shape is lora_id-keyed
   *  starting with v1; the persistence layer translates transparently. */
  savedLoraPerMode: Partial<Record<GenerationMode, LoraModeBlob>>
  /** Per-mode main prompt (lyrics in audio mode). Tracked separately from
   *  the params snapshot in memory. Still written for shape stability but
   *  NO LONGER rehydrated on boot — a refresh starts with a clean prompt
   *  (see the partial-hydration note in loadModels). */
  savedPromptPerMode?: Partial<Record<GenerationMode, string>>
  /** Snapshot of lora_id → filename captured at last save. Returned by
   *  `_loadSettings` for use in mid-session reconciliation when the fresh
   *  lora map arrives, so we can rewrite filenames that changed since save. */
  _loraFilenameSnapshot?: Record<string, string>
}

/** Build a lora_id-keyed copy of a single LoraModeBlob using filename → lora_id.
 *
 *  Multi-version disambiguation: if two filenames in the same blob share a
 *  lora_id (e.g. user keeps v1 + v2 of the same CivitAI model on disk for
 *  A/B testing), use a `{lora_id}#{filename}` suffix for the collision so
 *  each file's settings persist independently. Without this, the second
 *  file's loraWeights overwrite the first's via Object.fromEntries, and
 *  cross-session A/B silently loses one version's weights. */
function _modeBlobToLoraIdKeyed(
  m: LoraModeBlob,
  filenameToLoraId: Record<string, string>
): LoraModeBlob {
  const baseId = (fname: string) => filenameToLoraId[fname] || `local:${fname}`
  // Detect collisions across the whole blob: count how many filenames in
  // (activated_loras ∪ loraWeights ∪ availableLoras) map to each base id.
  const idCounts: Record<string, number> = {}
  const seen = new Set<string>([
    ...(m.activated_loras || []),
    ...Object.keys(m.loraWeights || {}),
    ...(m.availableLoras || []),
  ])
  for (const fname of seen) {
    const bid = baseId(fname)
    idCounts[bid] = (idCounts[bid] || 0) + 1
  }
  const id = (fname: string): string => {
    const bid = baseId(fname)
    return (idCounts[bid] || 0) > 1 ? `${bid}#${fname}` : bid
  }
  return {
    ...m,
    activated_loras: (m.activated_loras || []).map(id),
    loraWeights: Object.fromEntries(
      Object.entries(m.loraWeights || {}).map(([fname, w]) => [id(fname), w])
    ),
    availableLoras: (m.availableLoras || []).map(id),
  }
}

/** Reverse: lora_id-keyed blob → filename-keyed using lora_id → filename map.
 *
 *  Disambiguated keys (`{loraId}#{filename}`) carry the filename in the
 *  suffix — extract it directly so multi-version A/B state round-trips
 *  losslessly. */
function _modeBlobToFilenameKeyed(
  m: LoraModeBlob,
  loraIdToFilename: Record<string, string>
): LoraModeBlob {
  const fname = (id: string): string => {
    const hashIdx = id.indexOf('#')
    if (hashIdx > 0) return id.slice(hashIdx + 1)
    return loraIdToFilename[id] || (id.startsWith('local:') ? id.slice(6) : id)
  }
  return {
    ...m,
    activated_loras: (m.activated_loras || []).map(fname),
    loraWeights: Object.fromEntries(
      Object.entries(m.loraWeights || {}).map(([id, w]) => [fname(id), w])
    ),
    availableLoras: (m.availableLoras || []).map(fname),
  }
}

/**
 * Persist mode settings. The on-disk shape is lora_id-keyed (so that
 * filename changes from LoRA version bumps are transparent on reload),
 * with an embedded `_loraFilenameSnapshot` so the next load can translate
 * back to filenames immediately without waiting for the fresh map.
 *
 * If no map is provided (e.g. very early in boot before /installed has
 * returned), we skip translation and write the legacy filename-keyed shape
 * with no version flag. The next save with a populated map will upgrade it.
 */
/** Fields in SavedModeParams that hold file paths or per-gen ephemeral
 *  inputs which should NEVER persist across browser sessions. Persisting
 *  these caused the "ghost reference" bug: on page reload the cached
 *  paths would rehydrate from localStorage and the next generation
 *  would submit them, so users would silently get image-to-image edits
 *  against stale uploads they no longer had selected. Same pattern hit
 *  frame-injection positions in LTX-2 video mode and audio guide refs
 *  in TTS modes.
 *
 *  Rule of thumb: anything pointing to a path under app/uploads/ or any
 *  ephemeral per-job input belongs here. Anything the user genuinely
 *  wants remembered (model settings, slider values, video_prompt_type
 *  letter codes, etc.) stays out of this list and continues to persist.
 *
 *  Workaround for users on a Maestro version before this fix: use a
 *  private/incognito browser window (skips localStorage rehydration).
 */
const EPHEMERAL_PARAM_FIELDS: ReadonlyArray<keyof SavedModeParams> = [
  'image_start',
  'image_end',
  'image_refs',
  'video_guide',
  'video_guide2',
  'video_guide3',
  'video_source',
  'audio_guide',
  'audio_guide2',
  'audio_guide3',
  'frames_positions',
]

function _stripEphemeralParams(perMode: Partial<Record<GenerationMode, SavedModeParams>>): Partial<Record<GenerationMode, SavedModeParams>> {
  const cleaned: Partial<Record<GenerationMode, SavedModeParams>> = {}
  for (const [mode, params] of Object.entries(perMode || {})) {
    if (!params) continue
    const copy: SavedModeParams = { ...params }
    for (const field of EPHEMERAL_PARAM_FIELDS) {
      delete copy[field]
    }
    // The "T" temporal-alignment flag only means something alongside a
    // video_source — which is ephemeral-stripped above. Persisting a lone
    // "T" produced a ghost Advanced badge (counts as an active process
    // choice while displaying as nothing). Strip it on the way in AND out
    // so existing users' stale snapshots heal on next load. Only a TRAILING
    // "T" is the flag — an internal "T" is the depth_temporal control letter
    // (TVG/PTVG/TEVG) and a global strip silently downgraded those to plain
    // pose/spatial, so use /T$/.
    if (typeof copy.video_prompt_type === 'string' && copy.video_prompt_type.endsWith('T')) {
      copy.video_prompt_type = copy.video_prompt_type.replace(/T$/, '')
    }
    cleaned[mode as GenerationMode] = copy
  }
  return cleaned
}

function _saveSettings(
  state: PersistedModeSettings,
  filenameToLoraId?: Record<string, string>,
) {
  try {
    // Strip file-bearing / ephemeral fields BEFORE serializing so they
    // never round-trip through localStorage. The in-memory store keeps
    // them for the current session; only the persisted snapshot is
    // pruned. See EPHEMERAL_PARAM_FIELDS comment for the full rationale.
    const sanitizedParamsPerMode = _stripEphemeralParams(state.savedParamsPerMode || {})

    if (filenameToLoraId && Object.keys(filenameToLoraId).length > 0) {
      // Translate savedLoraPerMode → lora_id keys
      const translatedPerMode: Partial<Record<GenerationMode, LoraModeBlob>> = {}
      for (const [mode, m] of Object.entries(state.savedLoraPerMode || {})) {
        if (m) translatedPerMode[mode as GenerationMode] = _modeBlobToLoraIdKeyed(m, filenameToLoraId)
      }
      // Snapshot: lora_id → filename (so load can translate back instantly)
      const snapshot: Record<string, string> = {}
      for (const [fname, id] of Object.entries(filenameToLoraId)) snapshot[id] = fname
      const payload = {
        _version: _PERSIST_VERSION,
        _loraFilenameSnapshot: snapshot,
        generationMode: state.generationMode,
        selectedModelPerMode: state.selectedModelPerMode,
        savedParamsPerMode: sanitizedParamsPerMode,
        savedLoraPerMode: translatedPerMode,
        savedPromptPerMode: state.savedPromptPerMode,
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
    } else {
      // No map yet — write legacy filename-keyed shape, no version. Will be
      // upgraded on next save with a populated map. Still apply the ephemeral
      // strip on the way out.
      const sanitizedState = { ...state, savedParamsPerMode: sanitizedParamsPerMode }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(sanitizedState))
    }
  } catch { /* quota exceeded or private browsing */ }
}

function _loadSettings(): PersistedModeSettings | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    // v1+: savedLoraPerMode is lora_id-keyed; use the embedded snapshot to
    // translate back to filenames immediately. Reconciliation against the
    // fresh map happens in loadModels() once /installed returns.
    if (parsed && parsed._version === _PERSIST_VERSION && parsed._loraFilenameSnapshot) {
      const snapshot: Record<string, string> = parsed._loraFilenameSnapshot
      const translated: Partial<Record<GenerationMode, LoraModeBlob>> = {}
      for (const [mode, m] of Object.entries(parsed.savedLoraPerMode || {})) {
        if (m) translated[mode as GenerationMode] = _modeBlobToFilenameKeyed(m as LoraModeBlob, snapshot)
      }
      return {
        generationMode: parsed.generationMode,
        selectedModelPerMode: parsed.selectedModelPerMode || {},
        // Strip ephemeral file-bearing fields at load too — protects existing
        // users whose localStorage was written by a pre-fix version and still
        // contains stale image_start / image_refs / etc. paths. New saves will
        // be already-clean from _saveSettings; this is the migration safety
        // net so the first post-update page load can't immediately rehydrate
        // ghost references.
        savedParamsPerMode: _stripEphemeralParams(parsed.savedParamsPerMode || {}),
        savedLoraPerMode: translated,
        savedPromptPerMode: parsed.savedPromptPerMode || {},
        _loraFilenameSnapshot: snapshot,
      }
    }
    // Legacy (no version): blob is already filename-keyed, return as-is —
    // but still strip ephemeral fields out for the same migration-safety reason.
    const legacy = parsed as PersistedModeSettings
    return {
      ...legacy,
      savedParamsPerMode: _stripEphemeralParams(legacy.savedParamsPerMode || {}),
    }
  } catch { return null }
}

/** Fetch a model's defaults from the backend and merge primary fields
 *  into params. Shared between `selectModel` (explicit model pick) and
 *  `setGenerationMode` (mode switch where the per-mode active model
 *  may change). Without this, switching from LTX-2 (8 steps) to Flux 2
 *  Klein 9B (4 steps) or HiDream Dev (28 steps) would silently keep
 *  the slider at the previous model's value.
 *
 *  Only overrides "primary" model-tuned numeric fields. Leaves
 *  user-intent fields (prompt, seed, negative_prompt, resolution,
 *  repeat_generation, activated_loras) alone — those should survive
 *  model switches.
 *
 *  Race-safe: applies only if the same model is still active when
 *  the fetchDefaults promise resolves. Guards against rapid model
 *  switching from leaving a stale model's defaults applied.
 */
// String list — some of these (sample_solver, embedded_guidance_scale,
// audio_guidance_scale) aren't declared on GenerateParams but the
// params object is loose enough to carry them through to the backend.
const _PRIMARY_MODEL_DEFAULT_FIELDS: ReadonlyArray<string> = [
  'num_inference_steps',
  'guidance_scale',
  'flow_shift',
  'sample_solver',
  'embedded_guidance_scale',
  'audio_guidance_scale',
  // Perturbation config for the STG slider. These are inert unless
  // perturbation_switch === 2, which startGeneration derives from the
  // STG slider — the server-side fallback layers ([9]) are wrong for
  // LTX-2 22B (needs [28] from the model's settings file), so the
  // model-correct values must ride along with the request.
  // Deliberately NOT copied: perturbation_switch and stg_scale — older
  // generated settings files carry perturbation_switch: 2 / stg_scale: 1.0
  // from the settings-file era, and copying them would silently re-enable
  // STG on every generation.
  'perturbation_layers',
  'perturbation_start_perc',
  'perturbation_end_perc',
  // Default state of the Reference Pipeline toggle (10Eros defs set it to
  // true). Only copied when the model's settings carry the key, so models
  // without it keep whatever the user last chose — and startGeneration
  // strips it for models that lack the capability anyway. Unchecking the
  // toggle holds until the model is re-selected, same as steps/guidance.
  'reference_pipeline',
  // LM sampling knobs for the ACE-Step 1.5 family (and other LM-staged
  // audio models). Their handlers seed tuned values (temperature 0.85,
  // top_p 0.9, top_k off, LM CFG 2.5); without hydration the UI showed
  // and SENT its generic temperature 1.0. Only models whose defaults
  // carry these keys are affected — video model settings don't include
  // them, so nothing changes there.
  'temperature',
  'top_p',
  'top_k',
  'alt_guidance_scale',
  // Sliding-window timing is intentionally hydrated atomically by
  // loadModelOptions so top-level seconds, aligned frame params, overlap,
  // and Automatic/Manual state cannot race this defaults request.
  // Control-video coupling for the SCAIL-2 / Wan-Animate class:
  // force_fps "control" makes the output follow the guide video's frame
  // rate (user-reported: 25fps source came out 16fps without it), and
  // audio_prompt_type "R" remuxes the guide's audio track into the
  // output (user-reported: outputs were silent). Only the scail2 model
  // settings carry force_fps; every other model's audio_prompt_type
  // defaults to "" which matches the UI default, so nothing changes
  // elsewhere.
  'force_fps',
  'audio_prompt_type',
]

// Monotonic sequence for loadModelOptions staleness detection — only the
// most recently requested model's options may touch the store.
let _modelOptionsSeq = 0
let _modelDefaultsSeq = 0
let _directorResolutionOptionsSeq = 0
let _loraLoadSeq = 0
let _recipesLoadSeq = 0
let _h3EstimateSeq = 0
let _h3ProfileApplySeq = 0
let _h3CompatibilitySeq = 0
let _h3StyleWorkflowCatalogSeq = 0
let _legacyH3StylePrefixMigrationComplete = false

function _storedH3StyleWorkflow(): string {
  try { return localStorage.getItem(H3_STYLE_WORKFLOW_PREF_KEY) || '' } catch { return '' }
}

function _storeH3StyleWorkflow(value: string): void {
  try {
    if (value) localStorage.setItem(H3_STYLE_WORKFLOW_PREF_KEY, value)
    else localStorage.removeItem(H3_STYLE_WORKFLOW_PREF_KEY)
  } catch { /* local preference remains optional */ }
}

function _legacyH3StylePrefixMigrationWasCompleted(): boolean {
  if (_legacyH3StylePrefixMigrationComplete) return true
  try {
    _legacyH3StylePrefixMigrationComplete = localStorage.getItem(H3_STYLE_PREFIX_MIGRATION_KEY) === '1'
  } catch { /* this session still receives one bounded migration attempt */ }
  return _legacyH3StylePrefixMigrationComplete
}

function _completeLegacyH3StylePrefixMigration(): void {
  _legacyH3StylePrefixMigrationComplete = true
  try { localStorage.setItem(H3_STYLE_PREFIX_MIGRATION_KEY, '1') } catch { /* session marker remains */ }
}

export interface H3ModelProfileCompatibility {
  requestedProfileId: H3PerformanceProfileId
  compatible: boolean
  fallbackProfileId: H3PerformanceProfileId | null
  fallbackProfileLabel: string | null
  reason: string | null
  loading: boolean
}

const H3_PROFILE_PARAM_KEYS = new Set<keyof GenerateParams>([
  'model_type',
  'resolution',
  'num_inference_steps',
  'custom_settings',
  'activated_loras',
  'loras_multipliers',
  'tea_cache',
  'delivery_resolution',
  'delivery_fit',
])

function _applyModelDefaults(
  storeGet: () => {
    selectedModelPerMode: Partial<Record<GenerationMode, string>>
    generationMode: GenerationMode
    params: GenerateParams
  },
  storeSet: (fn: (s: {
    params: GenerateParams
    h3SelectedProfile: H3PerformanceProfileId | 'custom'
    h3ProfileApplying: H3PerformanceProfileId | null
    loraWeights: Record<string, number[]>
    spatialUpsampling: string
  }) => Partial<{
    params: GenerateParams
    h3SelectedProfile: H3PerformanceProfileId | 'custom'
    h3ProfileApplying: H3PerformanceProfileId | null
    loraWeights: Record<string, number[]>
    spatialUpsampling: string
  }>) => void,
  modelType: string,
): void {
  const seq = ++_modelDefaultsSeq
  api.fetchDefaults(modelType).then((d) => {
    if (seq !== _modelDefaultsSeq) return
    if (!d || typeof d !== 'object') return
    // Race guard: model may have been switched again while this fetch
    // was in flight. Apply only if still the active model in current mode.
    const state = storeGet()
    const active = state.selectedModelPerMode[state.generationMode]
    if (active !== modelType) return
    const overrides: Record<string, unknown> = {}
    for (const field of _PRIMARY_MODEL_DEFAULT_FIELDS) {
      if ((d as Record<string, unknown>)[field] !== undefined) {
        overrides[field] = (d as Record<string, unknown>)[field]
      }
    }
    const isH3 = H3_STUDIO_MODELS.has(modelType)
    if (isH3) {
      const defaults = d as Record<string, unknown>
      // Fresh H3 state is the backend's curated High bundle. This is a
      // narrow setting hydration: checkpoint identity, prompt, references,
      // privacy, explicit mode, and adaptive routing remain untouched.
      overrides.num_inference_steps = defaults.num_inference_steps
      overrides.resolution = defaults.resolution
      overrides.custom_settings = { ...(
        defaults.custom_settings as Record<string, unknown> | undefined
        || { h3_attention_engine: 'sol_attn' }
      ) }
      overrides.tea_cache = defaults.tea_cache ?? 0
      overrides.activated_loras = []
      overrides.loras_multipliers = ''
      overrides.delivery_resolution = undefined
      overrides.delivery_fit = undefined
    }
    if (Object.keys(overrides).length > 0) {
      storeSet(s => ({
        params: { ...s.params, ...overrides } as GenerateParams,
        ...(isH3 ? {
          h3SelectedProfile: 'high' as const,
          h3ProfileApplying: null,
          loraWeights: {},
          spatialUpsampling: '',
        } : {}),
      }))
    }
  }).catch(() => { /* fetch failure shouldn't break model switch */ })
}

// Family → generation mode mapping
const familyModeMap: Record<string, GenerationMode> = {
  flux: 'image',
  flux2: 'image',
  qwen: 'image',
  z_image: 'image',
  krea2: 'image',
  hidream: 'image',
  wan: 'video',
  wan2_2: 'video',
  hunyuan: 'video',
  hunyuan_1_5: 'video',
  ltxv: 'video',
  ltx2: 'video',
  kandinsky5: 'video',
  tts: 'audio',
  longcat: 'avatar',
}

// Model types classified as Avatar even though their family is primarily Video
const avatarModelTypes = new Set([
  'multitalk',
  'multitalk_720p',
  'fantasy',
  'infinitetalk',
  'infinitetalk_multi',
  'steadydancer',
  'i2v_2_2_multitalk',
  'animate',
  'hunyuan_avatar',
])

// Model types classified as Video Edit (Kiwi Edit, Chrono Edit)
const videoEditModelTypes = new Set([
  'kiwi_edit',
  'kiwi_edit_instruct_only',
  'kiwi_edit_reference_only',
  'chrono_edit',
  'chrono_edit_distill',
  'lucy_edit_fastwan',
  'lucy_edit_fastwan_1_1',
  // Dedicated to Edit → Recast; the general SCAIL Fast profile remains in
  // Studio Video/Animate.
  'scail2_14B_recast_fast',
])

// Audio sub-families: split the single "tts" family into Speech, Music, SFX
const audioSubFamilies: ModelFamily[] = [
  { id: 'tts_speech', label: 'Text to Speech', order: 200 },
  { id: 'tts_music', label: 'Music', order: 201 },
  { id: 'tts_sfx', label: 'Sound Effects', order: 202 },
]

// Model types that belong to the Music sub-family (everything else in
// tts → Speech). Membership is prefix-based for the known music model
// lines so newly added variants (e.g. new ACE-Step checkpoints)
// classify correctly without touching this file — the XL SFT models
// were invisible in the Music group because an id list here missed
// them. Keep the explicit set for one-off ids that don't share a
// prefix with their line.
const musicModelTypes = new Set<string>([])
const musicModelPrefixes = ['ace_step', 'heartmula']

function isMusicModelType(modelType: string): boolean {
  if (musicModelTypes.has(modelType)) return true
  return musicModelPrefixes.some(p => modelType.startsWith(p))
}

// Model types that belong to the SFX sub-family (MMAudio variants)
const sfxModelTypes = new Set([
  'mmaudio_v2',
  'mmaudio_nsfw',
])

// Virtual MMAudio model entries (injected into model list alongside backend models)
const SFX_VIRTUAL_MODELS: ModelDef[] = [
  { model_type: 'mmaudio_v2', name: 'MMAudio v2', family: 'tts', architecture: 'mmaudio', is_i2v: false, is_t2v: false, guidance_max_phases: 1, fps: 0, is_downloaded: true },
  { model_type: 'mmaudio_nsfw', name: 'MMAudio NSFW', family: 'tts', architecture: 'mmaudio', is_i2v: false, is_t2v: false, guidance_max_phases: 1, fps: 0, is_downloaded: false },
]

// Default enabled models (shown by default in selectors)
const DEFAULT_ENABLED_MODELS = new Set([
  // Image
  // Curated local image generation/edit families. Experimental uncensored
  // encoders remain normal visible choices; capability policy is independent.
  'flux2_dev',
  'flux2_klein_4b_uncensored',
  'flux2_klein_9b_uncensored',
  'flux2_klein_9b_pornmaster_v4_turbo_fp8_ponpoke',
  'flux2_klein_9b',
  'flux_krea',
  'flux_dev_kontext',
  'krea2_raw',
  'krea2_turbo',
  'krea2_raw_edit',
  'krea2_turbo_edit',
  'qwen_image_edit_2511_20B_fp8_lightning_8step',
  'qwen_image_edit_2511_nsfw',
  // Video
  // Keep LTX-2.3 Distilled available as a fast alternative. MiniMax H3
  // Base is the curated first-launch video default below.
  'ltx2_22B_distilled_1_1',
  // SCAIL-2 character animation (Animate a character with a control
  // video). Fast = lightx2v distill bundled (6 steps, no CFG, ~13x).
  'scail2_14B',
  'scail2_14B_fast',
  'scail2_14B_recast_fast',
  // MiniMax H3 Base: text, first/last-frame video, and native stereo audio.
  'minimax_h3',
  // Separate non-distilled Ref2VA base for semantic character/item/media refs.
  'minimax_h3_ref2va',
  // Audio — Speech
  'kugelaudio_0_open',
  'qwen3_tts_base',
  'qwen3_tts_customvoice',
  'qwen3_tts_voicedesign',
  // Audio — Music
  'ace_step_v1_5_turbo_lm_4b',
  'ace_step_v1_5_xl',
  'ace_step_v1_5_xl_turbo_lm_4b',
  'ace_step_v1_5_xl_sft',
  'ace_step_v1_5_xl_sft_lm_4b',
  // Audio — SFX
  'mmaudio_v2',
  'mmaudio_nsfw',
  // Avatar
  'animate',
])

/* Version of the curated defaults list above. enabledModels is a stored
 * whitelist, so existing installs never re-read DEFAULT_ENABLED_MODELS —
 * without this, entries added to the curated list in an update stay
 * invisible for everyone who ever opened the app before. Bump the
 * version when adding entries and list them under that version below:
 * they get merged into existing installs' whitelists exactly ONCE, so
 * a user who then disables them stays disabled forever. (This is
 * deliberately narrower than auto-enabling every unknown model — only
 * the curated list's own additions are pushed.) */
const DEFAULTS_VERSION = 9
const DEFAULTS_ADDED_IN: Record<number, string[]> = {
  // v1.2.0: the ACE-Step XL SFT pair; LM_4B becomes the music default.
  2: ['ace_step_v1_5_xl_sft', 'ace_step_v1_5_xl_sft_lm_4b'],
  // v1.3.0: SCAIL-2 character animation, base + lightx2v-distilled Fast.
  3: ['scail2_14B', 'scail2_14B_fast'],
  // Dedicated Recast recipe: native replacement + official I2V LightX point.
  4: ['scail2_14B_recast_fast'],
  // Krea 2 image generation + identity-preserving image editing.
  5: ['krea2_raw', 'krea2_turbo', 'krea2_raw_edit', 'krea2_turbo_edit'],
  // MiniMax H3 Base native audio-video generation.
  6: ['minimax_h3'],
  // MiniMax H3 semantic-reference checkpoint (separate from FL2VA).
  7: ['minimax_h3_ref2va'],
  // One bounded discovery migration for the expanded local image lineup.
  // Legacy server visibility stores have no disabled-model tombstones, so a
  // pre-v8 hide of an existing id cannot be distinguished from "never
  // curated" and may resurface once. A hide after v8 remains authoritative:
  // this version is never replayed. Durable historical tombstones require a
  // future visibility-API schema addition rather than origin-bound storage.
  8: [
    'flux2_dev',
    'flux2_klein_4b_uncensored',
    'flux2_klein_9b_uncensored',
    'flux_krea',
    'flux_dev_kontext',
    'qwen_image_edit_2511_20B_fp8_lightning_8step',
    'qwen_image_edit_2511_nsfw',
  ],
  // Manual experimental Klein 9B layered recipe. The backend omits it from
  // the catalog unless the creator/base/encoder term graph and source match.
  9: ['flux2_klein_9b_pornmaster_v4_turbo_fp8_ponpoke'],
}
const DEFAULTS_VERSION_KEY = 'maestro_defaults_version'

/* The music default changed in v1.2.0 (Turbo LM_4B -> SFT LM_4B).
 * A saved selection equal to the OLD default means the user was riding
 * the default rather than expressing a preference — follow them to the
 * new one, once, at the same version transition. Users who picked any
 * other model keep their choice. */
const OLD_MUSIC_DEFAULT = 'ace_step_v1_5_xl_turbo_lm_4b'
const NEW_MUSIC_DEFAULT = 'ace_step_v1_5_xl_sft_lm_4b'

const ENABLED_MODELS_KEY = 'maestro_enabled_models'
let _modelVisibilityHydrated = false
let _modelVisibilityDefaultsVersion = 1
let _modelVisibilitySaveTask: Promise<void> = Promise.resolve()
let _modelVisibilitySaveGeneration = 0

function _saveEnabledModels(models: Set<string>): Promise<void> {
  _modelVisibilitySaveGeneration += 1
  try {
    localStorage.setItem(ENABLED_MODELS_KEY, JSON.stringify([...models]))
  } catch { /* quota exceeded */ }
  const payload = {
    enabled_models: [...models],
    defaults_version: _modelVisibilityDefaultsVersion,
  }
  _modelVisibilitySaveTask = _modelVisibilitySaveTask
    .catch(() => { /* a later save should still run */ })
    .then(async () => {
      try {
        await api.updateModelVisibility(payload)
      } catch (error) {
        console.warn('Failed to persist model visibility:', error)
      }
    })
  return _modelVisibilitySaveTask
}

async function _refreshDirectorModelAdmissionCatalog(
  refreshCatalog: () => Promise<void>,
): Promise<void> {
  while (true) {
    const generation = _modelVisibilitySaveGeneration
    await _modelVisibilitySaveTask
    await refreshCatalog()
    await _modelVisibilitySaveTask
    if (_modelVisibilitySaveGeneration === generation) return
  }
}

interface DirectorReferenceRowsResult {
  paths: string[]
  labels: string[]
}

async function _resolveDirectorReferenceRows(
  files: File[],
  existingPaths: string[],
  labels: string[],
  component: 'character_reference' | 'location_reference',
  upload: (file: File) => Promise<{ path: string }>,
  assertCurrent: () => void = () => undefined,
): Promise<DirectorReferenceRowsResult> {
  // A shorter legacy path array may have been compacted after an upload
  // failure, so it cannot safely identify which selected file it belongs to.
  // Re-upload that selection instead of guessing and binding a path to the
  // wrong label. Complete aligned arrays remain reusable by exact index.
  const reusablePaths = files.length === 0 || existingPaths.length === files.length
    ? existingPaths
    : []
  const rowCount = files.length > 0 ? files.length : reusablePaths.length
  const rows = Array.from({ length: rowCount }, (_, index) => ({
    index,
    file: files[index],
    path: reusablePaths[index] || '',
    label: labels[index] || '',
  }))
  const settled = await Promise.allSettled(rows.map(async row => {
    if (row.path) return row
    if (!row.file) {
      throw new api.DirectorRequestError('director_reference_unavailable', component, row.index)
    }
    assertCurrent()
    try {
      const uploaded = await upload(row.file)
      assertCurrent()
      return { ...row, path: uploaded.path }
    } catch (error) {
      if (_isBrowserAbort(error)) throw error
      throw new api.DirectorRequestError('director_reference_unavailable', component, row.index)
    }
  }))
  const failed = settled.find(
    (result): result is PromiseRejectedResult => result.status === 'rejected',
  )
  if (failed) throw failed.reason
  const resolved = settled.map(result => (result as PromiseFulfilledResult<typeof rows[number]>).value)
  return {
    paths: resolved.map(row => row.path),
    labels: resolved.map(row => row.label),
  }
}

function _loadEnabledModels(): Set<string> | null {
  try {
    const raw = localStorage.getItem(ENABLED_MODELS_KEY)
    if (raw) return new Set(JSON.parse(raw))
  } catch { /* ignore */ }
  return null
}

// Default model_type per generation mode
const modeDefaultModel: Record<GenerationMode, string> = {
  image: 'flux2_klein_9b',
  video: 'minimax_h3',
  audio: 'kugelaudio_0_open',
  avatar: '',  // will fallback to first available
  tools: '',   // Tools is non-generative post-processing — owns no model
}

export function getFamilyMode(familyId: string): GenerationMode {
  return familyModeMap[familyId] || 'video'
}

/** Get the effective generation mode for a specific model (respects per-model overrides) */
export function getModelMode(modelType: string, familyId: string): GenerationMode {
  if (avatarModelTypes.has(modelType)) return 'avatar'
  if (familyId === 'longcat') return 'avatar'
  return getFamilyMode(familyId)
}

export function getFamiliesForMode(mode: GenerationMode, allFamilies: ModelFamily[], editSubMode?: string, audioSubMode?: string): ModelFamily[] {
  if (mode === 'avatar') {
    // Recast and Repaint run on SCAIL-2, which lives under the Wan 2.1
    // family. The remaining edit sub-modes use LTX models.
    if (editSubMode === 'recast' || editSubMode === 'restyle') {
      return allFamilies.filter(f => f.id === 'wan')
    }
    return allFamilies.filter(f => f.id === 'ltx2' || f.id === 'ltxv')
  }
  if (mode === 'audio') {
    // Filter to the active audio sub-mode family
    if (audioSubMode === 'speech') return audioSubFamilies.filter(f => f.id === 'tts_speech')
    if (audioSubMode === 'music') return audioSubFamilies.filter(f => f.id === 'tts_music')
    if (audioSubMode === 'sfx') return audioSubFamilies.filter(f => f.id === 'tts_sfx')
    if (audioSubMode === 'mixer') return []  // Mixer has no model selector
    return audioSubFamilies
  }
  return allFamilies.filter(f => getFamilyMode(f.id) === mode)
}

/** Get models for a family ID, optionally filtered by generation mode */
export function getModelsForFamily(familyId: string, allModels: ModelDef[], mode?: GenerationMode, editSubMode?: string): ModelDef[] {
  if (familyId === 'tts_speech') {
    return allModels.filter(m => m.family === 'tts' && !isMusicModelType(m.model_type) && !sfxModelTypes.has(m.model_type))
  }
  if (familyId === 'tts_music') {
    return allModels.filter(m => m.family === 'tts' && isMusicModelType(m.model_type))
  }
  if (familyId === 'tts_sfx') {
    return allModels.filter(m => m.family === 'tts' && sfxModelTypes.has(m.model_type))
  }
  const familyModels = allModels.filter(m => m.family === familyId)
  // When mode is specified and the family spans multiple modes, filter to matching models
  if (mode === 'avatar') {
    // Recast exposes its dedicated native-replacement Fast recipe plus HQ.
    if (editSubMode === 'recast') {
      return familyModels.filter(m =>
        m.model_type === 'scail2_14B_recast_fast'
        || m.model_type === 'scail2_14B'
      )
    }
    // Repaint intentionally mirrors Studio Video/Frames SCAIL Animate:
    // the edited first frame is the primary image and the source video
    // supplies motion/camera movement.
    if (editSubMode === 'restyle') {
      return familyModels.filter(m =>
        m.model_type === 'scail2_14B_fast'
        || m.model_type === 'scail2_14B'
      )
    }
    return familyModels.filter(m => !avatarModelTypes.has(m.model_type) && !videoEditModelTypes.has(m.model_type))
  }
  if (mode === 'video') {
    // For video mode: exclude models that are classified as avatar or video edit
    return familyModels.filter(m => !avatarModelTypes.has(m.model_type) && !videoEditModelTypes.has(m.model_type))
  }
  return familyModels
}

/** Get the display family ID for a model (handles audio sub-families) */
export function getDisplayFamily(model: ModelDef): string {
  if (model.family === 'tts') {
    if (sfxModelTypes.has(model.model_type)) return 'tts_sfx'
    if (isMusicModelType(model.model_type)) return 'tts_music'
    return 'tts_speech'
  }
  return model.family
}

// Transient: the LTX model selected before entering either SCAIL-2 edit
// workflow, so leaving Recast/Repaint restores the user's prior edit model.
let _preScail2AvatarModel = ''

const DEFAULT_RECAST_MAPPING: RecastCharacterMapping = {
  id: 'recast-a',
  target: 'person',
  refFile: null,
  refPath: '',
  refUrl: '',
  additionalRefs: [],
  referenceAlignedToSource: false,
}

function getDefaultModelForMode(mode: GenerationMode, families: ModelFamily[], models: ModelDef[]): string {
  // Try the preferred default first
  const preferred = modeDefaultModel[mode]
  if (preferred && models.some(m => m.model_type === preferred)) {
    return preferred
  }
  // Fallback: first model in first family of this mode
  const modeFamilies = getFamiliesForMode(mode, families)
  if (modeFamilies.length > 0) {
    const firstModel = getModelsForFamily(modeFamilies[0].id, models, mode)[0]
    if (firstModel) return firstModel.model_type
  }
  return ''
}

export type SystemConfigUpdateResult =
  | { ok: true; updated: Record<string, unknown> }
  | {
      ok: false
      code: 'cancelled' | 'request_failed' | 'timeout'
      message: string
    }

const SYSTEM_CONFIG_UPDATE_TIMEOUT_MS = 15_000
const SYSTEM_CONFIG_UPDATE_FAILURE_MESSAGE = 'System settings could not be updated. Check the connection and try again.'
const SYSTEM_CONFIG_UPDATE_TIMEOUT_MESSAGE = 'System settings took too long to update. Check the connection and try again.'
const SYSTEM_CONFIG_UPDATE_CANCELLED_MESSAGE = 'System settings update was interrupted. Try again.'
let _systemConfigUpdateSequence = 0
let _systemConfigUpdateController: AbortController | null = null

interface AppState {
  // Generation mode (top-level: image/video/audio/avatar)
  generationMode: GenerationMode
  setGenerationMode: (mode: GenerationMode) => void
  editSubMode: import('../types').EditSubMode
  setEditSubMode: (mode: import('../types').EditSubMode) => void
  // Edit mode state (persists across sub-mode switches)
  editVideoPath: string
  editVideoUrl: string
  editVideoFile: File | null
  editVideoDuration: number
  editVideoResolution: string  // "WxH" from source video
  editStartTime: number
  editEndTime: number
  editRetakeStrength: number
  /** CFG scale for prompt-driven edit modes. 1.0 = no CFG (the retake
   *  pipeline's legacy default — prompt barely influences the output).
   *  3.0-5.0 = strong prompt guidance (required for inpaint to actually
   *  replace content with prompt-specific pixels). */
  editPromptStrength: number
  /** LoRA strength for Edit Anything mode. 1.0 is the recommended start
   *  per the LoRA card; bump to 1.2 if the edit is too weak; lower below
   *  1.0 if the edit distorts unrelated content. */
  editAnythingLoraStrength: number
  /** Optional boundary-anchor images for Edit Anything. When set, the
   *  retake pipeline pins frame 0 / last frame of the edit range to these
   *  images instead of auto-extracting them from the source clip. Empty
   *  slots fall back to source frames — so if only the end anchor is set,
   *  the model morphs from source's actual start frame into the user's
   *  edited end frame across the range (the "Ironman suit forms over the
   *  man" effect). */
  editAnythingStartAnchor: string | null
  editAnythingEndAnchor: string | null
  /** SCAIL-2 Repaint edited first frame (uploaded or returned from Image mode). */
  editRepaintFrameFile: File | null
  editRepaintFramePath: string
  editRepaintFrameUrl: string
  /** Optional source-video → edited-frame semantic correspondences. */
  editRepaintMappings: RepaintRegionMapping[]
  /** Spatial quality profile shared with Recast's SCAIL-2 canvas logic. */
  editRepaintResolutionProfile: ScailResolutionProfile
  setEditRepaintFrame: (file: File | null, path: string, url: string) => void
  setEditRepaintMappings: (mappings: RepaintRegionMapping[]) => void
  /** Recast (SCAIL-2 Replace): who to swap out, as a SAM3 keyword. */
  editRecastTarget: string
  /** Number of matching people to track and replace (SCAIL-2 supports 1-5). */
  editRecastPersonCount: number
  /** Recast reference character image (uploaded path + preview URL). */
  editRecastRefFile: File | null
  editRecastRefPath: string
  editRecastRefUrl: string
  /** Explicit source-person → replacement mappings in stable SCAIL color order. */
  editRecastMappings: RecastCharacterMapping[]
  setEditRecastMappings: (mappings: RecastCharacterMapping[]) => void
  /** True when the reference preserves the selected source frame's layout. */
  editRecastRefAligned: boolean
  /** Remove unrelated reference scenery before SCAIL-2 encodes identity. */
  editRecastIsolateReference: boolean
  /** Derive a tighter same-character identity view when none is supplied. */
  editRecastAutoFaceDetail: boolean
  /** Rewrite and append Maestro's Recast identity/scene prompt guidance. */
  editRecastEnhancePrompt: boolean
  /** Strict source-pixel composite outside the tracked Recast target. */
  editRecastProtectBystanders: boolean
  /** Native SCAIL-2 color mapping for other visible identities. */
  editRecastPreserveBystanders: boolean
  /** Apply the official SCAIL-2 replacement Relighting LoRA. */
  editRecastUseRelighting: boolean
  /** Spatial quality profile, independent from the selected SCAIL-2 model. */
  editRecastResolutionProfile: ScailResolutionProfile
  setEditRecastRef: (file: File | null, path: string, url: string, aligned?: boolean) => void
  /** Round-trip marker for the "Edit Anchor in Image Mode" workflow.
   *  Populated when the user clicks "Edit Start" or "Edit End" on a
   *  boundary anchor slot. A banner at the top of the sidebar lets them
   *  apply the latest Image-mode output to that single anchor, then
   *  return to Edit Anything. Each anchor is its own independent
   *  round-trip — start and end can't both be in flight at once, but
   *  the user does them sequentially. */
  editReturnTarget: {
    /** Which anchor slot we're populating on return. */
    anchor: 'start' | 'end' | 'recast' | 'repaint'
    /** The pre-extracted source frame at the corresponding trim handle.
     *  This is the frame the user is editing in Image mode; if they
     *  cancel without applying, no anchor is set and the model falls
     *  back to extracting this same frame at generation time. */
    framePath: string
    /** The clip the user came from, so we can re-link them on return. */
    clipPath: string
    startTime: number
    endTime: number
    /** User's image-mode reference images / type before we hijacked the
     *  slot for the round-trip — restored on return so we don't nuke
     *  their existing image-mode workflow state. */
    savedImageRefs: File[]
    savedImageRefType: string
  } | null
  setEditAnythingStartAnchor: (path: string | null) => void
  setEditAnythingEndAnchor: (path: string | null) => void
  /** Extract one boundary frame from the source clip and switch the
   *  sidebar to Studio Image mode (using the proper setGenerationMode
   *  so the model + LoRA + image-mode params all swap correctly) with
   *  that frame loaded as image_start. */
  sendFrameToImageMode: (which: 'start' | 'end' | 'recast' | 'repaint') => Promise<void>
  /** Apply the latest Image-mode output to the requested anchor/reference,
   *  then return to Edit Anything or Recast. */
  applyOutputAsAnchor: () => Promise<void>
  /** Skip applying — return to Edit Anything with the anchor unset
   *  (model will fall back to source-extracted frame at generation time,
   *  giving the morph-from-source effect when only the OTHER anchor is
   *  set). */
  skipAnchorPhase: () => void
  /** Cancel the round-trip and return to Edit Anything. Same effect as
   *  skipAnchorPhase, but exposed separately for UI clarity. */
  cancelAnchorReturn: () => void
  editRetakeEngine: 'native' | 'legacy'
  editRegenerateAudio: boolean
  editSamTarget: string  // separate SAM segmentation target (noun phrase)
  editInvertMask: boolean  // invert SAM mask (select everything EXCEPT the target)
  editMasksPath: string | null  // cached SAM mask for inpaint
  editMaskPreview: string | null
  editDetectedTarget: string
  // Continue video state
  continueVideo: File | null
  continueVideoPath: string
  continueVideoUrl: string
  continueVideoDuration: number
  setContinueVideo: (file: File, path: string, url: string, duration: number) => void
  clearContinueVideo: () => void
  // Per-sub-mode working sets (Studio Video). Keyed by image_mode
  // (0 Frames / 2 Multi-Shot / 3 Extend / 4 Blend) — each sub-mode keeps
  // its own prompt, input tiles, and settings. See setParam('image_mode').
  videoSubModeStash: Partial<Record<number, VideoSubModeStash>>
  // Blend state
  blendClipA: File | null
  blendClipAPath: string
  blendClipAUrl: string
  blendClipADuration: number
  blendClipB: File | null
  blendClipBPath: string
  blendClipBUrl: string
  blendClipBDuration: number
  blendTransitionSec: number
  blendStrengthA: number
  blendStrengthB: number
  /** Seconds of Clip A's overlap tail used as video_source (motion prefix) for VE mode.
   *  0 = pure SE (single start-frame anchor, no motion continuity from A).
   *  1-2 = model extrapolates A's motion through the blend. */
  blendMotionPrefixSec: number
  /** Seconds of Clip B's overlap head used as video_end (motion suffix) —
   *  symmetric counterpart to motion prefix. 0 = single still anchor at
   *  blend end. 1-2 = model lands at B with real jogger stride/speed. */
  blendMotionSuffixSec: number
  /** input_video_strength for the VE anchors (video_source + image_end).
   *  1.0 = hard-lock both anchors → model averages between them (crossfade).
   *  0.5-0.8 = weaker anchors, model invents motion in between. */
  blendAnchorStrength: number
  setBlendClipA: (file: File, path: string, url: string, duration: number) => void
  setBlendClipB: (file: File, path: string, url: string, duration: number) => void
  clearBlendClipA: () => void
  clearBlendClipB: () => void
  setBlendTransitionSec: (sec: number) => void
  setBlendStrengthA: (v: number) => void
  setBlendStrengthB: (v: number) => void
  setBlendMotionPrefixSec: (v: number) => void
  setBlendMotionSuffixSec: (v: number) => void
  setBlendAnchorStrength: (v: number) => void
  blendMode: 'insert' | 'overlap'
  blendOverlapSec: number
  setBlendMode: (mode: 'insert' | 'overlap') => void
  setBlendOverlapSec: (sec: number) => void
  // Outpaint state
  // Padding kept in pixels (server contract: pad_top/bottom/left/right).
  // The new OutpaintCanvas computes these from canvas aspect + video position
  // on submit, but the store still surfaces the raw values so legacy callers
  // and metadata sidecars stay compatible.
  outpaintPadding: { top: number; bottom: number; left: number; right: number }
  setOutpaintPadding: (padding: { top: number; bottom: number; left: number; right: number }) => void
  outpaintResolutionPreset: 'auto' | '480p' | '540p' | '720p' | '1080p'
  setOutpaintResolutionPreset: (preset: 'auto' | '480p' | '540p' | '720p' | '1080p') => void
  // Canvas aspect ratio for the outpaint composer. 'source' means keep the
  // source clip's native aspect (no canvas extension — only useful when the
  // user wants to outpaint a single side via drag).
  outpaintAspect: '16:9' | '9:16' | '1:1' | '4:3' | '3:4' | 'source'
  setOutpaintAspect: (a: '16:9' | '9:16' | '1:1' | '4:3' | '3:4' | 'source') => void
  // Video frame position+size inside the canvas, normalized to canvas
  // dimensions (0–1). Default = centered, fully fit (no crop). User drags
  // to reposition; resize handles scale the source within the canvas.
  outpaintVideoBox: { x: number; y: number; w: number; h: number }
  setOutpaintVideoBox: (box: { x: number; y: number; w: number; h: number }) => void
  // Film-strip trim times (seconds). When end > start, server pre-trims
  // the source via ffmpeg before outpainting.
  outpaintTrimStart: number
  outpaintTrimEnd: number
  setOutpaintTrimStart: (t: number) => void
  setOutpaintTrimEnd: (t: number) => void
  outpaintSourcePreservation: number
  setOutpaintSourcePreservation: (v: number) => void
  outpaintLoraStrength: number
  setOutpaintLoraStrength: (v: number) => void
  // Official LTX-2.3 binary-mask conditioning plus multiscale source blend.
  // Enabled by default; false keeps the legacy black-sentinel path for A/B.
  outpaintMaskPreserving: boolean
  setOutpaintMaskPreserving: (v: boolean) => void
  outpaintPreserveSourceAudio: boolean
  setOutpaintPreserveSourceAudio: (v: boolean) => void
  // Lock source pixels: composite original source clip back into the source
  // rectangle of the outpainted output (post-process ffmpeg overlay).
  // Default OFF — the model's regenerated source area actually preserves
  // lip detail well, and a hard overlay creates a visible rectangle seam.
  // Kept for opt-in use cases that need pixel-perfect source area.
  outpaintLockSourcePixels: boolean
  setOutpaintLockSourcePixels: (v: boolean) => void
  // Trim sliding-window smear: cut the per-window-overlap frames at the
  // window 1→2 boundary in the output, where the IC-LoRA's prefix
  // conditioning produces a constant ~9-frame lag for the rest of the
  // clip. Default ON — fixes lip sync on multi-window outpaint.
  outpaintTrimSmear: boolean
  setOutpaintTrimSmear: (v: boolean) => void
  // Sliding-window controls for long-clip outpainting (auto-engages when
  // total_frames > windowSize). 0 = use model default (LTX-2: 241 frames).
  outpaintWindowSize: number
  setOutpaintWindowSize: (v: number) => void
  outpaintWindowOverlap: number
  setOutpaintWindowOverlap: (v: number) => void
  setEditVideoPath: (path: string) => void
  setEditVideo: (file: File | null, path: string, url: string, duration: number, resolution: string) => void
  clearEditVideo: () => void
  audioSubMode: import('../types').AudioSubMode
  setAudioSubMode: (mode: import('../types').AudioSubMode) => void
  // Music mode (ACE-Step): describe + LLM writes, or type Style/Lyrics directly.
  musicDescription: string
  setMusicDescription: (s: string) => void
  musicInstrumental: boolean
  setMusicInstrumental: (b: boolean) => void
  selectedModelPerAudioSubMode: Partial<Record<import('../types').AudioSubMode, string>>
  selectedModelPerMode: Partial<Record<GenerationMode, string>>
  savedLoraPerMode: Partial<Record<GenerationMode, { activated_loras: string[]; loras_multipliers: string; loraWeights: Record<string, number[]>; availableLoras: string[] }>>
  savedParamsPerMode: Partial<Record<GenerationMode, SavedModeParams>>
  savedPromptPerMode: Partial<Record<string, string>>
  /** Snapshot of lora_id → filename loaded from localStorage at boot.
   *  Used by `refreshLoraIdMap` reconciliation to rewrite filenames that
   *  changed since save (LoRA version updates). Internal-only; not part
   *  of the persisted runtime state. */
  _loraFilenameSnapshotAtLoad?: Record<string, string>

  // Generation params
  params: GenerateParams
  setParam: <K extends keyof GenerateParams>(key: K, value: GenerateParams[K]) => void
  setParams: (partial: Partial<GenerateParams>) => void
  setH3NativeResolution: (resolution: string) => void
  h3StyleWorkflow: string
  h3StyleWorkflowCatalog: api.H3StyleWorkflowCatalog | null
  h3StyleWorkflowCatalogLoading: boolean
  h3StyleWorkflowCatalogError: string | null
  setH3StyleWorkflow: (id: string) => void
  loadH3StyleWorkflowCatalog: (force?: boolean) => Promise<void>
  migrateLegacyH3StylePrompt: () => void

  // UI state
  settingsOpen: boolean
  toggleSettings: () => void
  setSettingsOpen: (open: boolean) => void
  sidebarOpen: boolean
  toggleSidebar: () => void
  setSidebarOpen: (open: boolean) => void
  openQueueAfterSubmit: boolean
  setOpenQueueAfterSubmit: (enabled: boolean) => void

  // Theme — see lib/theme.ts. Two-dimensional: a dark/light/auto mode
  // plus a theme family (each family has a dark and a light variant).
  // Persisted to localStorage; an inline script in index.html applies
  // the resolved theme to <html> BEFORE React mounts to avoid a flash
  // of the default theme.
  themePrefs: ThemePrefs
  setThemeMode: (mode: ThemeMode) => void
  setThemeFamily: (family: FamilyId) => void

  // Retake Dialog
  retakeDialogOpen: boolean
  retakeSourceFile: string | null
  openRetakeDialog: (filename: string) => void
  closeRetakeDialog: () => void

  // CivitAI LoRA Browser
  // Director Pipeline Dashboard
  dashboardOpen: boolean
  dashboardPipelineList: PipelineListItem[]
  dashboardPipelineListRead: DashboardPipelineListRead
  dashboardSelectedPipeline: SavedPipelineState | null
  dashboardLoading: boolean
  setDashboardOpen: (open: boolean) => void
  loadPipelineList: () => Promise<void>
  loadSavedPipeline: (pid: string) => Promise<void>
  tagClip: (pid: string, clipIndex: number, tag: string | null) => Promise<void>
  startPipelineRepair: (pid: string) => Promise<PipelineRepairState>
  cancelPipelineRepair: (pid: string) => Promise<PipelineRepairState>
  pollPipelineRepair: (pid: string, operationId: string) => void
  rerunClipImage: (pid: string, clipIndex: number, prompt?: string) => Promise<unknown>
  rerunClipVideo: (pid: string, clipIndex: number, prompt?: string) => Promise<unknown>
  rejoinPipelineClips: (pid: string) => Promise<unknown>
  resumePipeline: (pid: string) => Promise<void>
  deletePipeline: (pid: string) => Promise<void>

  // Recipes (one-click Studio presets)
  recipesOpen: boolean
  setRecipesOpen: (open: boolean) => void
  recipes: import('../api/client').RecipeCard[]
  recipesLoading: boolean
  recipesError: string | null
  loadRecipes: () => Promise<void>
  applyRecipe: (id: string) => Promise<{ missing: import('../api/client').RecipeLora[] }>
  saveRecipeFromOutput: (outputName: string, name: string, description: string, nsfw: boolean) => Promise<void>
  deleteRecipe: (id: string) => Promise<void>
  downloadRecipeLora: (lora: import('../api/client').RecipeLora, modelType: string) => Promise<void>

  loraBrowserOpen: boolean
  loraBrowserArch: string | null
  loraBrowserDefaultDir: string | null
  setLoraBrowserOpen: (open: boolean, arch?: string) => void
  setLoraBrowserDefaultDir: (dir: string | null) => void
  civitSearchResults: CivitAIModel[]
  civitSearchCursor: string | null
  civitSearchLoading: boolean
  civitSearchError: string | null
  civitSelectedModel: CivitAIModel | null
  civitDownloads: CivitAIDownload[]
  searchCivitAI: (params: Record<string, unknown>, append?: boolean) => Promise<void>
  selectCivitAIModel: (modelId: number) => Promise<void>
  clearCivitSelection: () => void
  startCivitAIDownload: (params: Record<string, unknown>) => Promise<void>
  pollCivitAIDownloads: () => void

  // Models & families (from API)
  families: ModelFamily[]
  models: ModelDef[]
  loadModels: () => Promise<void>
  modelsLoaded: boolean

  // Model visibility (favorites)
  enabledModels: Set<string>
  toggleModelEnabled: (modelType: string) => void
  resetEnabledModels: () => void
  setAllModelsEnabled: (enabled: boolean) => void
  /** Bulk-toggle a list of models (family-level enable/disable, issue #14). */
  setModelsEnabled: (modelTypes: string[], enabled: boolean) => void
  // ModelSelector "+N more" hint → open Settings and expand Enabled Models.
  modelVisibilityFocus: GenerationMode | null
  openModelVisibility: (mode: GenerationMode) => void
  openDirectorModelVisibility: () => void
  clearModelVisibilityFocus: () => void

  // Resolution helpers
  resolutionPreset: ResolutionPreset
  setResolutionPreset: (preset: ResolutionPreset) => void
  aspectRatio: AspectRatio
  setAspectRatio: (ratio: AspectRatio) => void

  // Duration
  durationSeconds: number
  setDurationSeconds: (s: number) => void

  // Sliding window
  slidingWindowSeconds: number
  setSlidingWindowSeconds: (s: number) => void
  slidingWindowOverlap: number
  setSlidingWindowOverlap: (frames: number) => void
  slidingWindowLocked: boolean
  setSlidingWindowLocked: (locked: boolean) => void

  // Real frame rate of the uploaded guide/control video (probed server-side
  // at upload). Used by force_fps="control" models (SCAIL-2 class) to
  // convert durationSeconds to frames at the rate the output will actually
  // play at, instead of the model's nominal fps.
  guideVideoFps: number | null
  setGuideVideoFps: (fps: number | null) => void
  guideVideoFrameCount: number | null
  setGuideVideoFrameCount: (frames: number | null) => void

  // Output count
  outputCount: number
  setOutputCount: (n: number) => void

  // Image uploads
  startImage: File | null
  endImage: File | null
  setStartImage: (f: File | null) => void
  setEndImage: (f: File | null) => void

  // Image references (for models with image_ref_choices)
  imageRefs: File[]
  imageRefType: string
  removeBackgroundRefs: boolean
  addImageRef: (file: File) => void
  removeImageRef: (index: number) => void
  reorderImageRefs: (from: number, to: number) => void
  setImageRefType: (type: string) => void
  setRemoveBackgroundRefs: (v: boolean) => void

  // Post-processing (shared for Studio mode)
  spatialUpsampling: string
  setSpatialUpsampling: (v: string) => void
  filmGrainIntensity: number
  setFilmGrainIntensity: (v: number) => void
  filmGrainSaturation: number
  setFilmGrainSaturation: (v: number) => void

  // Voice clone postprocessing (SeedVC). Replaces 1 or 2 voices in
  // a generated video's audio with user-supplied reference voice(s).
  // Applied after generation as a postprocessing step. See
  // app/postprocessing/voice_clone.py for backend logic.
  voiceCloneEnabled: boolean
  setVoiceCloneEnabled: (v: boolean) => void
  voiceCloneMode: 'single' | 'two'
  setVoiceCloneMode: (v: 'single' | 'two') => void
  // Up to 2 reference voices. Each entry tracks the uploaded filename
  // (display) + the server-side path the backend uses.
  voiceCloneRefs: { filename: string; path: string }[]
  setVoiceCloneRef: (index: number, ref: { filename: string; path: string } | null) => void

  // ── Tools area (standalone post-processing on an existing clip) ──────
  // Apply FlashVSR upscale or SeedVC revoice to any gallery output or an
  // uploaded clip, independent of a generation. See ToolsPanel.tsx + the
  // /api/v1/tools/* endpoints.
  toolsTool: 'upscale' | 'revoice' | 'blender'
  setToolsTool: (t: 'upscale' | 'revoice' | 'blender') => void
  /** Gallery filename (resolved against the workspace) OR an absolute upload path. */
  toolsSourcePath: string | null
  toolsSourceName: string | null
  toolsSourceUrl: string | null
  setToolsSource: (src: { path: string; name: string; url: string | null } | null) => void
  uploadToolsSource: (file: File) => Promise<boolean>
  toolsUpscaleMethod: string
  setToolsUpscaleMethod: (m: string) => void
  toolsRevoiceMode: 'single' | 'two'
  setToolsRevoiceMode: (m: 'single' | 'two') => void
  toolsRevoiceRefs: ({ filename: string; path: string } | null)[]
  setToolsRevoiceRef: (index: number, ref: { filename: string; path: string } | null) => void
  uploadToolsRevoiceRef: (index: number, file: File) => Promise<boolean>
  runTool: () => Promise<void>
  /** Gallery one-click: upscale a specific clip now, with the configured method. */
  quickUpscaleClip: (name: string, url: string | null) => Promise<void>
  /** Gallery one-click: load a clip into the Tools panel for a tool that needs
   *  setup before running (e.g. revoice needs voice references), and switch to it. */
  sendClipToTools: (name: string, url: string | null, tool: 'upscale' | 'revoice') => void

  // Director final-video post-processing controls.
  directorVideoSpatialUpsampling: string
  setDirectorVideoSpatialUpsampling: (v: string) => void
  directorVideoFilmGrainIntensity: number
  setDirectorVideoFilmGrainIntensity: (v: number) => void
  directorVideoFilmGrainSaturation: number
  setDirectorVideoFilmGrainSaturation: (v: number) => void
  directorVideoSelfRefiner: number
  setDirectorVideoSelfRefiner: (v: number) => void
  directorAudioScale: number
  setDirectorAudioScale: (v: number) => void

  // Audio guide (pre-filled by Director or manual upload)
  audioGuideFilename: string | null
  setAudioGuideFilename: (name: string | null) => void
  audioGuide2Filename: string | null
  setAudioGuide2Filename: (name: string | null) => void
  ttsSpeakerName1: string
  ttsSpeakerName2: string
  ttsSpeakerNamesManual: boolean
  setTtsSpeakerName1: (name: string) => void
  setTtsSpeakerName2: (name: string) => void
  _autoParseSpkeakerNames: (text: string, force?: boolean) => void
  // Dynamic multi-speaker (1-6 voices)
  ttsVoiceCount: number  // 0=text only, 1-6=voice clone count
  ttsVoices: { name: string; filename: string | null; path: string | null }[]
  setTtsVoiceCount: (count: number) => void
  setTtsVoiceName: (index: number, name: string) => void
  setTtsVoiceFile: (index: number, filename: string | null, path: string | null) => void
  addTtsVoice: () => void
  removeTtsVoice: (index: number) => void

  // Multi-clip state
  clips: MultiClip[]
  singlePromptMode: boolean
  setClipPrompt: (index: number, prompt: string) => void
  setClipStartImage: (index: number, file: File | null) => void
  setSinglePromptMode: (v: boolean) => void
  syncClipCount: () => void

  // Generation state (queue)
  jobs: GenerationJob[]
  isGenerating: boolean
  sampleCampaignPairs: api.SampleCampaignQueuePair[]
  refreshSampleCampaignQueue: (signal?: AbortSignal) => Promise<void>
  clearSampleCampaignQueue: () => void
  pendingH3Plan: H3SegmentPlan | null
  pendingH3PlanEstimate: H3PerformanceEstimate | null
  pendingH3PlanJobId: string | null
  pendingH3PlanWorkspace: string | null
  h3PlanReviewLoading: boolean
  h3PlanReviewError: string | null
  openH3PlanReview: (jobId: string) => Promise<void>
  closeH3PlanReview: () => void
  approveH3Plan: (decision: H3PlanDecision) => Promise<void>
  cancelH3Plan: () => Promise<void>
  startGeneration: () => Promise<void>
  stopGeneration: (jobId?: string) => void
  dismissJob: (jobId: string) => void
  reconcileQueueState: (queue: api.QueueState) => void
  reconnectJobs: (accountIdentityEpoch?: number) => Promise<void>
  resumeJobRecovery: (jobId: string) => Promise<void>
  retryJobRecovery: (jobId: string) => Promise<void>
  _pollRecoveredJob: (jobId: string) => void

  // LoRA state
  availableLoras: string[]
  lorasLoading: boolean
  loraWeights: Record<string, number[]>
  /** Map of LoRA filename → stable lora_id (e.g. `civitai:12345` for a
   *  CivitAI-sourced LoRA, `local:foo.safetensors` for hand-installed).
   *  Populated from /api/v1/loras/installed at boot and refreshed when
   *  LoRAs are added/removed. Used by the localStorage persistence layer
   *  to write update-resilient keys. */
  loraIdByFilename: Record<string, string>
  /** Reverse: lora_id → current filename. Used by reconciliation to
   *  detect when a saved filename has been renamed by a LoRA update. */
  filenameByLoraId: Record<string, string>
  /** Refresh `loraIdByFilename` / `filenameByLoraId` from the backend.
   *  Triggers reconciliation of savedLoraPerMode against the fresh map. */
  refreshLoraIdMap: () => Promise<void>
  loadLoras: (modelType: string) => Promise<void>
  toggleLora: (filename: string) => void
  /** Ensure the LTX-2.3 transition LoRA is downloaded and activated for
   *  blend mode. Called when blend mode is opened. Idempotent: no-op if
   *  the LoRA is already installed and activated. */
  ensureTransitionLoraForBlend: () => Promise<void>
  /** Ensure the Alissonerdx Edit Anything LoRA is downloaded. Called when
   *  the Edit Anything sub-mode is opened. Idempotent — no-op if already
   *  installed. Unlike the transition LoRA, this one is activated
   *  server-side by the /api/v1/edit-anything endpoint, not client-side,
   *  so the user's global LoRA list isn't touched. */
  ensureEditAnythingLora: () => Promise<void>
  setLoraWeight: (filename: string, phaseIndex: number, value: number) => void

  // Presets
  presets: import('../api/client').GenerationPreset[]
  presetsLoading: boolean
  loadPresets: () => Promise<void>
  savePreset: (name: string) => Promise<void>
  loadPreset: (preset: import('../api/client').GenerationPreset) => void
  deletePreset: (id: string) => Promise<void>

  // Model options
  modelOptions: ModelOptions | null
  modelOptionsLoading: boolean
  loadModelOptions: (modelType: string) => Promise<void>
  h3PerformanceProfiles: H3PerformanceProfile[]
  h3CurrentEstimate: H3PerformanceEstimate | null
  h3SegmentCountEstimate: H3SegmentCountEstimate | null
  h3EstimateLoading: boolean
  h3EstimateError: string | null
  h3SelectedProfile: H3PerformanceProfileId | 'custom'
  h3ProfileApplying: H3PerformanceProfileId | null
  h3ModelProfileCompatibility: Record<string, H3ModelProfileCompatibility | undefined>
  invalidateH3PerformanceEstimates: () => void
  refreshH3PerformanceEstimates: () => Promise<void>
  refreshH3ModelProfileCompatibility: (modelType: string) => Promise<void>
  normalizeH3EditableProfile: () => Promise<boolean>
  applyH3PerformanceProfile: (id: H3PerformanceProfileId) => Promise<void>

  // System config
  accessContext: api.AccessContext | null
  loadAccessContext: (refreshProjectsOnIdentityChange?: boolean) => Promise<api.AccessContext>
  accountContext: AccountContext | null
  accountContextLoading: boolean
  accountProjectMigration: AccountProjectMigrationStatus | null
  accountProjectMigrationLoading: boolean
  accountDrawerOpen: boolean
  accountSessions: AccountSession[]
  accountUsers: AccountSummary[]
  accountDetailsLoading: boolean
  supportCatalog: SupportPublicProjection | null
  supportCatalogLoading: boolean
  supportCatalogUnavailable: boolean
  supportSelf: SupportSelfProjection | null
  responsibleUse: ResponsibleUseProjection | null
  supportAdminAccountId: string | null
  supportAdmin: SupportAdminProjection | null
  supportDetailsLoading: boolean
  setAccountDrawerOpen: (open: boolean) => void
  loadAccountContext: (refreshProjectsOnIdentityChange?: boolean) => Promise<AccountContext | null>
  loadAccountProjectMigration: () => Promise<AccountProjectMigrationStatus | null>
  migrateAccountProjects: () => Promise<AccountProjectMigrationStatus | null>
  bootstrapAccount: (input: {
    username: string
    password: string
    email?: string
    deviceLabel?: string
  }) => Promise<AccountAuthResult | null>
  loginAccount: (input: {
    username: string
    password: string
    deviceLabel?: string
  }) => Promise<AccountAuthResult>
  logoutAccount: () => Promise<void>
  reauthenticateAccount: (password: string) => Promise<void>
  recoverAccount: (input: {
    username: string
    recoveryCode: string
    newPassword: string
    deviceLabel?: string
  }) => Promise<AccountAuthResult | null>
  changeAccountPassword: (newPassword: string) => Promise<void>
  rotateAccountRecoveryCodes: () => Promise<string[] | null>
  loadAccountSessions: () => Promise<void>
  revokeAccountSession: (sessionHandle: string) => Promise<boolean>
  revokeAllAccountSessions: (retainCurrent: boolean) => Promise<number>
  loadAccountUsers: () => Promise<void>
  createServerAccount: (input: {
    username: string
    password: string
    email?: string
  }) => Promise<AccountAuthResult | null>
  setServerAccountDisabled: (accountId: string, disabled: boolean) => Promise<void>
  loadSupportCatalog: () => Promise<SupportPublicProjection | null>
  loadSupportSelf: () => Promise<SupportSelfProjection | null>
  loadResponsibleUse: () => Promise<ResponsibleUseProjection | null>
  acceptResponsibleUse: (documentVersion: number, contentSha256: string) => Promise<void>
  loadSupportAdmin: (accountId: string) => Promise<SupportAdminProjection>
  transitionSupportFulfillment: (
    accountId: string,
    input: SupportFulfillmentMutationInput,
  ) => Promise<SupportAdminProjection>
  recordSupportContribution: (
    accountId: string,
    input: SupportManualContributionInput,
  ) => Promise<SupportAdminProjection>
  clearSupportAdmin: () => void
  systemConfig: SystemConfig | null
  systemConfigLoading: boolean
  loadSystemConfig: () => Promise<void>
  updateSystemConfig: (
    partial: Partial<SystemConfig>,
    signal?: AbortSignal,
  ) => Promise<SystemConfigUpdateResult>

  // Hardware detect — populated lazily when Settings → System opens.
  // Shared between AutoPerformanceCard (the readout) and the rest of
  // the System panel (e.g. the VRAM coefficient subtext that needs to
  // know the user's actual VRAM size, not a hardcoded 24GB).
  systemDetect: SystemDetectResponse | null
  loadSystemDetect: () => Promise<void>
  systemStats: SystemStats | null
  loadSystemStats: () => Promise<void>

  // Settings tab
  settingsTab: SettingsTab
  setSettingsTab: (tab: SettingsTab) => void

  // Select model (triggers side effects)
  selectModel: (modelType: string) => Promise<boolean>

  // Workspaces
  workspaces: api.Workspace[]
  activeWorkspace: string
  /** Gallery is showing the virtual "Uploads" view (browse-only — the
   *  server-side active workspace, and where generations save, is
   *  untouched). Entered via switchWorkspace('__uploads__'). */
  browsingUploads: boolean
  loadWorkspaces: () => Promise<boolean>
  switchWorkspace: (name: string) => Promise<boolean>
  createWorkspace: (name: string, password?: string) => Promise<void>
  unlockWorkspace: (name: string, password: string, remember: api.WorkspaceRememberPolicy) => Promise<api.WorkspaceUnlockResult>
  lockWorkspace: (name: string) => Promise<api.WorkspaceLockResult>
  lockAllWorkspaces: () => Promise<api.WorkspaceLockResult>
  deleteWorkspace: (name: string) => Promise<void>

  // Storage Manager overlay
  storageDashboardOpen: boolean
  setStorageDashboardOpen: (open: boolean) => void

  // LoRA picker sort order — store-backed (not per-component state) so
  // simultaneously mounted pickers (e.g. Director's Image + Video
  // accordions) stay in sync; persisted to localStorage.
  loraPickerSort: 'name' | 'newest'
  setLoraPickerSort: (sort: 'name' | 'newest') => void

  // Outputs
  outputs: OutputFile[]
  outputsTotal: number
  selectedOutput: number
  setSelectedOutput: (i: number) => void
  mediaFilter: MediaFilter
  outputArtifactScope: OutputArtifactScope
  outputSearchQuery: string
  setMediaFilter: (f: MediaFilter) => void
  setOutputArtifactScope: (scope: OutputArtifactScope) => void
  setOutputSearchQuery: (q: string) => void
  resetGalleryFilters: () => void
  filteredOutputs: () => OutputFile[]
  outputsLoading: boolean
  loadOutputs: () => Promise<boolean>
  loadMoreOutputs: () => Promise<void>
  refreshOutputs: () => Promise<void>
  toggleFavorite: (name: string) => Promise<void>
  gallerySelectionMode: boolean
  selectedOutputKeys: string[]
  setGallerySelectionMode: (enabled: boolean) => void
  toggleOutputSelection: (output: OutputFile) => void
  selectAllLoadedOutputs: () => void
  clearOutputSelection: () => void
  bulkMoveSelectedOutputs: (targetWorkspace: string) => Promise<string[]>
  bulkSetSelectedPrivacy: (privateOutput: boolean) => Promise<string[]>
  bulkDeleteSelectedOutputs: () => Promise<string[]>

  // Output metadata (lazy-loaded for selected output)
  selectedOutputMeta: OutputMetadata | null
  selectedOutputMetaName: string | null
  metadataLoading: boolean
  loadOutputMetadata: (name: string) => Promise<void>
  loadSettingsFromOutput: () => Promise<void>
  rerollGeneration: () => Promise<void>
  deleteSelectedOutput: (name?: string, workspace?: string) => Promise<void>
  rejoinClipGroup: (groupId: string) => Promise<void>

  // Services config
  servicesConfig: ServicesConfig | null
  servicesConfigLoading: boolean
  servicesConfigError: string | null
  clearServicesConfigError: () => void
  loadServicesConfig: () => Promise<void>
  updateServicesConfig: (partial: Partial<ServicesConfig>) => Promise<void>
  hostTerms: HostTermsStatus | null
  hostTermsLoading: boolean
  hostTermsError: string | null
  loadHostTerms: () => Promise<void>
  acceptHostTerm: (term: HostTermId) => Promise<boolean>
  /** Per-browser, per-job intent. This is deliberately not hydrated from the
   *  host's durable mature-capability setting or persisted to localStorage. */
  explicitOutput: boolean
  setExplicitOutput: (enabled: boolean) => void
  privateOutput: boolean
  setPrivateOutput: (enabled: boolean) => void

  // LLM state
  llmStatus: LlmStatus | null
  llmLoading: boolean
  llmModels: LlmModelOption[]
  loadLlmStatus: () => Promise<void>
  loadLlmModels: () => Promise<void>
  loadLlm: () => Promise<void>
  unloadLlm: () => Promise<void>

  // Prompt enhancement
  isEnhancing: boolean
  enhanceStatus: api.LlmPreparationStatus | api.LlmEnhanceOperationStatus | null
  enhanceRequestScope: api.LlmEnhanceOperationScope | null
  studioPromptEnhance: boolean
  setStudioPromptEnhance: (enabled: boolean) => void
  enhancePrompt: (ttsMode?: string) => Promise<boolean>
  resumeEnhancePrompt: () => Promise<boolean>
  cancelEnhancePrompt: () => Promise<void>

  // Director (Music Video Director)
  sidebarMode: SidebarMode
  /** Workspace to resume after Reference finishes a durable queue/apply action. */
  referenceReturnMode: ReferenceReturnMode
  directorStep: 'upload' | 'analyze' | 'structure' | 'style' | 'plan' | 'review' | 'generate_images' | 'plan_video' | 'review_video'
  directorAudioFile: File | null
  directorAudioPath: string | null
  directorAnalysis: AudioAnalysisResult | null
  directorPlannedClips: PlannedClip[]
  directorEnergyBias: number
  directorClipPlans: ClipPlan[]
  directorSceneDescription: string
  /** Empty means the Realistic product default unless freeform copy defines a style. */
  directorVisualStyle: string
  directorCustomVisualStyle: string
  directorLoading: boolean
  /** Sub-status for the current loading phase (e.g. "Loading
   *  transcription model (first use downloads ~300MB)..."). Set by
   *  the analyze polling loop in directorUploadAndAnalyze; read by
   *  the sidebar loading spinner. Falls back to a default like
   *  "Analyzing audio..." in the UI when null. */
  directorLoadingMessage: string | null
  directorError: string | null
  directorComponentError: api.DirectorComponentFailure | null
  directorReferenceImage: File | null
  directorReferenceImagePath: string | null
  directorCharacterRefs: File[]
  directorCharacterRefPaths: string[]
  directorCharacterRefLabels: string[]
  directorLocationRefs: File[]
  directorLocationRefPaths: string[]
  directorLocationRefLabels: string[]
  directorVoiceRef: File | null
  directorVoiceRefPath: string | null
  directorIdentityGuidanceScale: number
  setDirectorVoiceRef: (file: File | null) => void
  setDirectorIdentityGuidanceScale: (v: number) => void
  directorClipImages: DirectorClipImage[]
  directorImageGenProgress: DirectorImageGenProgress | null
  directorSpeakers: string[]
  directorSpeakerMappings: SpeakerMapping[]
  directorAutoMode: boolean
  directorSeamless: boolean
  directorShotImageGuidance: DirectorShotImageGuidance
  directorVideoInferenceStepsByModel: Record<string, number>
  directorVideoMaxShotFramesByModel: Record<string, number>
  directorSkill: DirectorSkill | null
  directorResolution: ResolutionPreset
  directorAspectRatio: AspectRatio
  directorResolutionModelType: string | null
  directorResolutionOptions: ModelOptions | null
  directorResolutionOptionsLoading: boolean
  directorResolutionOptionsError: string | null
  directorCapabilities: api.DirectorCapabilities | null
  directorCapabilitiesExplicitOutput: boolean | null
  directorCapabilitiesLoading: boolean
  directorCapabilitiesLoadingExplicitOutput: boolean | null
  directorCapabilitiesError: string | null
  directorModelVisibilityRefreshPending: boolean
  /** False only while a pre-role combined image preference remains visible. */
  directorImageRolesConfigured: boolean
  directorLegacyImageModel: string
  directorImageCreatorModelOverride: string
  directorImageEditorModelOverride: string
  directorImageRoleLoras: Record<DirectorImageRole, DirectorImageRoleLoraSelection[]>
  setDirectorAutoMode: (v: boolean) => void
  setDirectorSeamless: (v: boolean) => void
  setDirectorShotImageGuidance: (v: DirectorShotImageGuidance) => void
  setDirectorVideoInferenceSteps: (modelType: string, steps: number | null) => void
  setDirectorVideoMaxShotFrames: (modelType: string, frames: number | null) => void
  setDirectorSkill: (skill: DirectorSkill) => void
  setDirectorResolution: (preset: ResolutionPreset) => void
  setDirectorAspectRatio: (ratio: AspectRatio) => void
  loadDirectorResolutionOptions: (modelType: string) => Promise<ModelOptions | null>
  loadDirectorCapabilities: (options?: {
    explicitOutput?: boolean
    force?: boolean
  }) => Promise<api.DirectorCapabilities>
  activateDirectorImageRoles: () => void
  setDirectorImageRoleModel: (role: DirectorImageRole, modelType: string) => void
  setDirectorImageRoleLoras: (role: DirectorImageRole, selections: DirectorImageRoleLoraSelection[]) => void
  selectDirectorVideoModel: (modelType: string) => Promise<void>
  directorSetLora: (mode: 'image' | 'video', activated_loras: string[], loras_multipliers: string, loraWeights: Record<string, number[]>, availableLoras: string[]) => void
  setSidebarMode: (mode: SidebarMode) => void
  directorSetSpeakerMapping: (speakerId: string, name: string, role: SpeakerMapping['role']) => void
  directorInsertSpeakerMention: (speakerId: string) => void
  directorUploadAndAnalyze: (file: File) => Promise<void>
  // Music Video: generate-the-track source + song setup
  directorMusicSource: 'upload' | 'generate' | null
  directorSongDescription: string
  directorSongInstrumental: boolean
  directorSongStyle: string
  directorSongLyrics: string
  directorSongDuration: number
  directorTrackGenerating: boolean
  directorRequestId: string | null
  directorRequestWorkspace: string | null
  directorPreparationStatus: api.DirectorPreparationStatus | null
  setDirectorMusicSource: (s: 'upload' | 'generate' | null) => void
  setDirectorSongDescription: (v: string) => void
  setDirectorSongInstrumental: (v: boolean) => void
  setDirectorSongStyle: (v: string) => void
  setDirectorSongLyrics: (v: string) => void
  setDirectorSongDuration: (v: number) => void
  directorWriteSong: () => Promise<void>
  directorGenerateTrack: () => Promise<void>
  reconnectDirectorPreparation: () => Promise<void>
  directorAnalyzeAndPlan: (audioPath: string, opts?: { transcribe?: boolean; lyricsHint?: string }) => Promise<void>
  directorSetEnergyBias: (bias: number) => Promise<void>
  directorConfirmStructure: () => void
  directorSetSceneDescription: (prompt: string) => void
  setDirectorVisualStyle: (style: string) => void
  setDirectorCustomVisualStyle: (style: string) => void
  directorSetReferenceImage: (file: File | null) => void
  directorAddCharacterRef: (file: File) => void
  directorRemoveCharacterRef: (index: number) => void
  directorSetCharacterRefLabel: (index: number, label: string) => void
  directorReorderCharacterRefs: (from: number, to: number) => void
  directorAddLocationRef: (file: File) => void
  directorRemoveLocationRef: (index: number) => void
  directorSetLocationRefLabel: (index: number, label: string) => void
  directorReorderLocationRefs: (from: number, to: number) => void
  directorPlanPrompts: () => Promise<void>
  directorPlanVideoPrompts: () => Promise<void>
  directorGenerateStartImages: () => Promise<void>
  directorApplyToClips: () => void
  directorGenerate: () => void
  directorReset: () => void
  directorEditClipPlan: (index: number, field: 'video_prompt' | 'image_prompt', value: string) => void
  _uploadDirectorRefs: (lifecycle?: { ownsWorkspace: () => boolean }) => Promise<{ refImagePath: string | null; charPaths: string[]; locPaths: string[] }>

  // Short Film Director
  shortFilmCharacters: ShortFilmCharacter[]
  shortFilmPath: ShortFilmPath | null
  shortFilmTargetDuration: number
  shortFilmNarrative: boolean
  shortFilmSetCharacters: (characters: ShortFilmCharacter[]) => void
  shortFilmSetPath: (path: ShortFilmPath) => void
  shortFilmSetTargetDuration: (duration: number) => void
  shortFilmSetNarrative: (v: boolean) => void
  shortFilmUploadAndAnalyze: (file: File) => Promise<void>
  shortFilmSetPacingBias: (bias: number) => Promise<void>
  shortFilmPlanPrompts: () => Promise<void>
  shortFilmPlanVideoPrompts: () => Promise<void>
  shortFilmPlanFromStory: () => Promise<void>

  // Director Pipeline (server-side)
  pipelineId: string | null
  pipelineStatus: import('../api/client').PipelineStatus | null
  pipelinePolling: boolean
  startDirectorPipeline: () => Promise<void>
  continuePipeline: (updates?: { clip_plans?: Array<{ video_prompt: string; image_prompt: string }> }) => Promise<void>
  stopPipeline: () => Promise<void>
  pollPipelineStatus: () => void
}

async function _applyH3ServerProfile(
  profile: H3PerformanceProfile,
  id: H3PerformanceProfileId,
  seq: number,
  get: StoreApi<AppState>['getState'],
  set: StoreApi<AppState>['setState'],
): Promise<boolean> {
  set({ h3ProfileApplying: id, modelOptionsLoading: true, h3EstimateError: null })
  try {
    const target = profile.settings.model_type
    const [options, defaults, loras] = await Promise.all([
      api.fetchModelOptions(target),
      api.fetchDefaults(target),
      api.fetchLoras(target),
    ])
    if (seq !== _h3ProfileApplySeq) return false

    const state = get()
    if (state.generationMode !== 'video') return false
    const fps = options.fps || 24
    const durationFrames = alignStudioTotalFrames(Math.round(state.durationSeconds * fps), options)
    const durationSeconds = Math.round((durationFrames / fps) * 1000) / 1000
    const segmented = usesStudioSegments(options)
    const supportsWindows = options.sliding_window || segmented
    const swDefaults = options.sliding_window_defaults || {}
    const latent = Math.max(1, Math.trunc(options.latent_size || options.frames_steps || 4))
    const requestedWindow = state.slidingWindowLocked
      ? Math.round(state.slidingWindowSeconds * fps)
      : Math.trunc(
          swDefaults.window_default
          ?? options.default_sliding_window_size
          ?? (segmented ? options.frames_maximum : undefined)
          ?? Math.round(state.slidingWindowSeconds * fps),
        )
    const windowMin = Math.max(1, Math.trunc(swDefaults.window_min || (segmented ? options.frames_minimum : 1) || 1))
    const windowMax = Math.max(windowMin, Math.trunc(swDefaults.window_max || (segmented ? options.frames_maximum : requestedWindow) || requestedWindow))
    const boundedWindow = Math.min(windowMax, Math.max(windowMin, requestedWindow))
    const windowFrames = segmented
      ? alignTotalFrames(boundedWindow, options)
      : Math.floor((boundedWindow - 1) / latent) * latent + 1
    const windowSeconds = Math.round((windowFrames / fps) * 1000) / 1000
    const discard = swDefaults.discard_last_frames ?? 0
    const overlapMax = Math.max(0, windowFrames - discard - latent)
    const overlap = supportsWindows ? Math.max(
      Math.min(swDefaults.overlap_min ?? 0, overlapMax),
      Math.min(
        swDefaults.overlap_default ?? state.slidingWindowOverlap,
        swDefaults.overlap_max ?? overlapMax,
        overlapMax,
      ),
    ) : 0

    const defaultOverrides: Record<string, unknown> = {}
    for (const field of _PRIMARY_MODEL_DEFAULT_FIELDS) {
      if (defaults[field] !== undefined) defaultOverrides[field] = defaults[field]
    }
    const settings = profile.settings
    const nextParams: GenerateParams = {
      ...state.params,
      ...defaultOverrides,
      model_type: target,
      guidance_phases: options.guidance_max_phases,
      video_length: durationFrames,
      sliding_window_size: supportsWindows ? windowFrames : undefined,
      sliding_window_overlap: overlap,
      sliding_window_discard_last_frames: discard,
      num_inference_steps: settings.num_inference_steps,
      resolution: settings.resolution,
      custom_settings: { ...settings.custom_settings },
      activated_loras: [...settings.activated_loras],
      loras_multipliers: settings.loras_multipliers,
      tea_cache: settings.tea_cache,
      delivery_resolution: settings.delivery_resolution || undefined,
      delivery_fit: settings.delivery_fit || undefined,
    }
    const mode = state.generationMode
    const savedLoraPerMode = {
      ...state.savedLoraPerMode,
      [mode]: {
        activated_loras: [...settings.activated_loras],
        loras_multipliers: settings.loras_multipliers,
        loraWeights: { ...settings.lora_weights },
        availableLoras: [...loras.loras],
      },
    }
    const savedParamsPerMode = {
      ...state.savedParamsPerMode,
      [mode]: _snapshotModeParams(nextParams),
    }
    const selectedModelPerMode = {
      ...state.selectedModelPerMode,
      [mode]: target,
    }
    set({
      params: nextParams,
      selectedModelPerMode,
      modelOptions: options,
      modelOptionsLoading: false,
      availableLoras: [...loras.loras],
      lorasLoading: false,
      loraWeights: { ...settings.lora_weights },
      savedLoraPerMode,
      savedParamsPerMode,
      durationSeconds,
      ...(supportsWindows ? { slidingWindowSeconds: windowSeconds } : {}),
      slidingWindowOverlap: overlap,
      slidingWindowLocked: supportsWindows ? state.slidingWindowLocked : false,
      spatialUpsampling: settings.spatial_upsampling || '',
      h3SelectedProfile: id,
      h3ProfileApplying: null,
      h3CurrentEstimate: profile.estimate,
      ...(profile.segment_count_estimate
        ? { h3SegmentCountEstimate: profile.segment_count_estimate }
        : {}),
    })
    const applied = get()
    _saveSettings({
      generationMode: mode,
      selectedModelPerMode,
      savedParamsPerMode,
      savedLoraPerMode,
      savedPromptPerMode: applied.savedPromptPerMode,
    }, applied.loraIdByFilename)
    void get().refreshH3PerformanceEstimates()
    return true
  } catch (error) {
    if (seq !== _h3ProfileApplySeq) return false
    set({
      modelOptionsLoading: false,
      h3ProfileApplying: null,
      h3EstimateError: error instanceof Error ? error.message : 'Could not apply H3 performance profile',
    })
    return false
  }
}

const defaultParams: GenerateParams = {
  prompt: '',
  model_type: 'minimax_h3',
  resolution: '1344x768',
  video_length: 251,
  num_inference_steps: 20,
  guidance_scale: 1.0,
  seed: -1,
  image_mode: 0,
  negative_prompt: '',
  repeat_generation: 1,
  activated_loras: [],
  loras_multipliers: '',
  settings_version: 2.52,
  h3_adaptive_conditioning: true,
  custom_settings: { h3_attention_engine: 'sol_attn' },
  tea_cache: 0,
}

// ── Per-sub-mode working sets (Studio Video) ─────────────────────────
// Frames, Multi-Shot, Extend, and Blend each keep their OWN prompt,
// input tiles, and settings. Switching the ModeToggle stashes the
// outgoing sub-mode's full working set and restores the incoming one —
// so a Frames setup with a dozen injected keyframes survives a
// round-trip through Extend untouched. First visit to a sub-mode keeps
// the generic settings (steps, resolution, ...) but blanks the input
// spec, so Extend starts clean instead of inheriting Frames' inputs.
// In-memory only: after a reload the active sub-mode is restored (via
// savedParamsPerMode) and the others start blank again.
interface VideoSubModeStash {
  params: GenerateParams
  startImage: File | null
  endImage: File | null
  continueVideo: File | null
  continueVideoPath: string
  continueVideoUrl: string
  continueVideoDuration: number
  audioGuideFilename: string | null
  imageRefs: File[]
  imageRefType: string
  removeBackgroundRefs: boolean
  durationSeconds: number
  slidingWindowSeconds: number
  slidingWindowOverlap: number
  clips: MultiClip[]
  singlePromptMode: boolean
}

const captureVideoSubModeStash = (s: AppState): VideoSubModeStash => ({
  params: { ...s.params },
  startImage: s.startImage,
  endImage: s.endImage,
  continueVideo: s.continueVideo,
  continueVideoPath: s.continueVideoPath,
  continueVideoUrl: s.continueVideoUrl,
  continueVideoDuration: s.continueVideoDuration,
  audioGuideFilename: s.audioGuideFilename,
  imageRefs: s.imageRefs,
  imageRefType: s.imageRefType,
  removeBackgroundRefs: s.removeBackgroundRefs,
  durationSeconds: s.durationSeconds,
  slidingWindowSeconds: s.slidingWindowSeconds,
  slidingWindowOverlap: s.slidingWindowOverlap,
  clips: s.clips,
  singlePromptMode: s.singlePromptMode,
})

// The "input spec" — everything the Inputs panel + prompt box write into
// params. Blanked when entering a sub-mode with no stash yet; the
// generic generation settings (steps, resolution, guidance, ...) carry
// over and only diverge per-sub-mode once the user changes them there.
const BLANK_VIDEO_INPUT_PARAMS: Partial<GenerateParams> = {
  prompt: '',
  image_start: undefined,
  image_end: undefined,
  image_refs: undefined,
  frames_positions: undefined,
  injection_strength: undefined,
  video_prompt_type: '',
  image_prompt_type: '',
  audio_prompt_type: '',
  audio_guide: undefined,
  video_guide: undefined,
  video_source: undefined,
  input_video_strength: undefined,
}

const resolutionMap: Partial<Record<ResolutionPreset, Record<AspectRatio, string>>> = {
  'auto': {
    'auto': 'auto',
    '16:9': 'auto',
    '9:16': 'auto',
    '1:1': 'auto',
    '4:3': 'auto',
    '3:4': 'auto',
  },
  '480p': {
    'auto': 'auto_480p',
    '16:9': '848x480',
    '9:16': '480x848',
    '1:1': '672x672',
    '4:3': '736x544',
    '3:4': '544x736',
  },
  '540p': {
    'auto': 'auto_540p',
    '16:9': '960x544',
    '9:16': '544x960',
    '1:1': '736x736',
    '4:3': '832x608',
    '3:4': '608x832',
  },
  '720p': {
    'auto': 'auto_720p',
    '16:9': '1280x720',
    '9:16': '720x1280',
    '1:1': '1024x1024',
    '4:3': '1104x832',
    '3:4': '832x1104',
  },
  '1080p': {
    'auto': 'auto_1080p',
    '16:9': '1920x1088',
    '9:16': '1088x1920',
    '1:1': '1024x1024',
    '4:3': '1920x1088',
    '3:4': '1088x1920',
  },
}

export function resolveResolution(
  modelOptions: ModelOptions | null,
  preset: ResolutionPreset,
  ratio: AspectRatio,
): string {
  const modelValues = modelOptions?.resolution_presets?.[preset]?.values
  return modelValues?.[ratio]
    || modelValues?.['16:9']
    || resolutionMap[preset]?.[ratio]
    || resolutionMap[preset]?.['16:9']
    || '1280x720'
}

export function resolveDeclaredResolution(
  modelOptions: ModelOptions | null,
  preset: ResolutionPreset,
  ratio: AspectRatio,
): string | null {
  if (!modelOptions?.resolution_preset_order?.includes(preset)) return null
  if (ratio === 'auto' && modelOptions.supports_auto_aspect !== true) return null
  return modelOptions.resolution_presets?.[preset]?.values?.[ratio] || null
}

// Memoization cache for filteredOutputs — ensures stable references
let _foCachedOutputs: OutputFile[] = []
let _foCachedFilter: MediaFilter = 'all'
let _foCachedResult: OutputFile[] = []
let _outputsRequestGeneration = 0
let _metadataRequestGeneration = 0
let _settingsRestoreGeneration = 0
let _outputsPaginationActive = false

function computeFilteredOutputs(outputs: OutputFile[], mediaFilter: MediaFilter): OutputFile[] {
  if (outputs === _foCachedOutputs && mediaFilter === _foCachedFilter) {
    return _foCachedResult
  }
  _foCachedOutputs = outputs
  _foCachedFilter = mediaFilter
  if (mediaFilter === 'all') {
    _foCachedResult = outputs
  } else if (mediaFilter === 'videos') {
    _foCachedResult = outputs.filter(o => o.type === 'video')
  } else if (mediaFilter === 'images') {
    _foCachedResult = outputs.filter(o => o.type === 'image')
  } else if (mediaFilter === 'audio') {
    _foCachedResult = outputs.filter(o => o.type === 'audio')
  } else if (mediaFilter === 'avatars') {
    // "Edits" filter — show outputs from any of the Edit tab sub-modes.
    // Filter by `edit_sub_mode` (set by retake/inpaint/outpaint/restyle/
    // edit_anything endpoints) rather than `mode === 'avatar'`, because
    // those endpoints write `mode: 'video'` for backwards compatibility
    // and the old check produced an empty list. Falls back to mode check
    // for any legacy outputs that predate the edit_sub_mode tagging.
    _foCachedResult = outputs.filter(o => !!o.edit_sub_mode || o.mode === 'avatar')
  } else if (mediaFilter === 'multiclip') {
    // Backend already filters to multiclip + sliding window finals — pass through
    _foCachedResult = outputs
  } else if (mediaFilter === 'favorites') {
    _foCachedResult = outputs.filter(o => o.favorite)
  } else {
    _foCachedResult = outputs
  }
  return _foCachedResult
}

async function _ensureSelectedH3StyleWorkflowReady(getState: () => AppState): Promise<void> {
  const selected = getState().h3StyleWorkflow
  if (!selected) return
  if (!getState().h3StyleWorkflowCatalog && !getState().h3StyleWorkflowCatalogLoading) {
    await getState().loadH3StyleWorkflowCatalog()
  }
  const state = getState()
  if (!state.h3StyleWorkflowCatalog) {
    throw new Error('The selected H3 workflow could not be verified against the server catalog.')
  }
  if (!state.h3StyleWorkflow) {
    throw new Error(state.h3StyleWorkflowCatalogError || 'The saved H3 workflow is no longer available.')
  }
  const modelType = state.selectedModelPerMode.video
  if (h3StyleWorkflowSupportsModel(state.h3StyleWorkflowCatalog, modelType)
    && !h3StyleWorkflowSelectionIsCurrent(state.h3StyleWorkflowCatalog, state.h3StyleWorkflow)) {
    state.setH3StyleWorkflow('')
    throw new Error('The selected H3 workflow is no longer in the server catalog.')
  }
}

interface DirectorImageRoleRequestCapture {
  wire: Pick<api.DirectorV2PlanRequest, 'image_creator_model'>
    & Partial<Pick<api.DirectorV2PlanRequest,
      'image_editor_model' | 'image_creator_loras' | 'image_editor_loras'>>
  effective_creator_model: string
  effective_editor_model: string
}

const _initialLegacyDirectorImageModel = _initialDirectorImageRoles === null
  ? _loadSettings()?.selectedModelPerMode.image || ''
  : ''

async function _captureDirectorImageRoleRequest(
  getState: () => AppState,
  explicitOutput: boolean,
): Promise<DirectorImageRoleRequestCapture> {
  // A fresh server snapshot binds automatic resolution to the current literal
  // Explicit choice and current remote authorization/catalog visibility.
  const capabilities = await getState().loadDirectorCapabilities({
    explicitOutput,
    force: true,
  })
  const state = getState()
  const creatorOverride = state.directorImageCreatorModelOverride.trim()
  const editorOverride = state.directorImageEditorModelOverride.trim()
  const effectiveCreator = creatorOverride || capabilities.image_roles.creator.resolved_model
  const effectiveEditor = editorOverride || capabilities.image_roles.editor.resolved_model
  if (!effectiveCreator) throw new api.DirectorRequestError(
    'director_model_unavailable',
    'image_creator_model',
  )
  if (!effectiveEditor) throw new api.DirectorRequestError(
    'director_model_unavailable',
    'continuity_editor_model',
  )
  const roleModels = [
    ['creator', effectiveCreator, capabilities.image_roles.creator.candidates] as const,
    ['editor', effectiveEditor, capabilities.image_roles.editor.candidates] as const,
  ]
  for (const [role, modelType, candidates] of roleModels) {
    const candidate = candidates.find(item => item.model_type === modelType)
    const component = role === 'creator' ? 'image_creator_model' : 'continuity_editor_model'
    if (!candidate) throw new api.DirectorRequestError('director_model_unavailable', component)
    if (!candidate.compatible || !candidate.ready) {
      const code = candidate.reasons.includes('model_terms_required')
        ? 'director_model_terms_required'
        : candidate.reasons.includes('model_unavailable')
          ? 'director_model_unavailable'
          : 'director_model_not_ready'
      throw new api.DirectorRequestError(code, component)
    }
  }
  const creatorLoras = state.directorImageRoleLoras.creator
  const editorLoras = state.directorImageRoleLoras.editor
  for (const [role, modelType, selections] of [
    ['creator', effectiveCreator, creatorLoras] as const,
    ['editor', effectiveEditor, editorLoras] as const,
  ]) {
    if (selections.length === 0) continue
    let catalog: Awaited<ReturnType<typeof api.fetchLoraDetails>>
    try {
      catalog = await api.fetchLoraDetails(modelType)
    } catch {
      throw new api.DirectorRequestError(
        'director_role_lora_unavailable',
        role === 'creator' ? 'image_creator_lora' : 'continuity_editor_lora',
      )
    }
    const errors = api.validateDirectorImageRoleLoraSelections(selections, catalog.loras)
    if (errors.length > 0) {
      throw new api.DirectorRequestError(
        'director_role_lora_unavailable',
        role === 'creator' ? 'image_creator_lora' : 'continuity_editor_lora',
      )
    }
  }
  return {
    wire: {
      // Null is the exact new-role automatic-creator sentinel. Omitting all
      // role keys would intentionally select the legacy combined-image wire.
      image_creator_model: creatorOverride || null,
      ...(editorOverride ? { image_editor_model: editorOverride } : {}),
      ...(creatorLoras.length > 0 ? {
        image_creator_loras: api.toDirectorImageRoleLoraWire(creatorLoras),
      } : {}),
      ...(editorLoras.length > 0 ? {
        image_editor_loras: api.toDirectorImageRoleLoraWire(editorLoras),
      } : {}),
    },
    effective_creator_model: effectiveCreator,
    effective_editor_model: effectiveEditor,
  }
}

let _supportAdminRequestSequence = 0
let _supportCatalogRequestSequence = 0
let _supportSelfRequestSequence = 0
let _responsibleUseRequestSequence = 0
let _responsibleUseAcceptanceSequence = 0
let _accountContextRequestSequence = 0
let _accountSessionsRequestSequence = 0
let _accountUsersRequestSequence = 0
let _accountMutationRequestSequence = 0
let _accessContextRequestSequence = 0
let _accountProjectMigrationRequestSequence = 0
let _sampleCampaignQueueRequestSequence = 0
const _sampleCampaignKnownJobIds = new Set<string>()
let _accountIdentityEpoch = 0

function _accountIdentity(context: AccountContext | null | undefined): string {
  return context?.authenticated === true && context.account ? context.account.id : ''
}

function _enhanceAccountFingerprint(
  state: Pick<AppState, 'accountContext' | 'accessContext'>,
): string {
  return _boundedEnhanceFingerprint(
    _accountIdentity(state.accountContext ?? state.accessContext?.accounts) || 'local-owner',
  )
}

function _enhanceBytesToHex(bytes: ArrayBuffer | Uint8Array): string {
  return Array.from(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes))
    .map(value => value.toString(16).padStart(2, '0'))
    .join('')
}

function _readEnhanceFingerprintClaim(): EnhanceFingerprintClaimRecord | null {
  try {
    const parsed = JSON.parse(
      sessionStorage.getItem(ENHANCE_FINGERPRINT_CLAIM_STORAGE_KEY) || 'null',
    )
    if (
      !parsed
      || typeof parsed !== 'object'
      || Array.isArray(parsed)
      || Object.keys(parsed).length !== 3
      || parsed.schemaVersion !== 1
      || typeof parsed.token !== 'string'
      || !/^[0-9a-f]{64}$/i.test(parsed.token)
      || typeof parsed.salt !== 'string'
      || !/^[0-9a-f]{64}$/i.test(parsed.salt)
    ) return null
    return { schemaVersion: 1, token: parsed.token, salt: parsed.salt }
  } catch {
    return null
  }
}

function _writeEnhanceFingerprintClaim(record: EnhanceFingerprintClaimRecord): void {
  try {
    sessionStorage.setItem(ENHANCE_FINGERPRINT_CLAIM_STORAGE_KEY, JSON.stringify(record))
  } catch { /* same-realm identity remains usable; reload recovery fails closed */ }
}

function _newEnhanceFingerprintClaim(): EnhanceFingerprintClaimRecord {
  return {
    schemaVersion: 1,
    token: _enhanceBytesToHex(globalThis.crypto.getRandomValues(
      new Uint8Array(ENHANCE_FINGERPRINT_TOKEN_BYTES),
    )),
    salt: _enhanceBytesToHex(globalThis.crypto.getRandomValues(
      new Uint8Array(ENHANCE_FINGERPRINT_SALT_BYTES),
    )),
  }
}

async function _tryClaimEnhanceFingerprintToken(token: string): Promise<boolean> {
  const locks = globalThis.navigator?.locks
  if (!locks?.request) return false
  return new Promise(resolve => {
    let decided = false
    const holdForRealmLifetime = new Promise<void>(() => {})
    const timer = globalThis.setTimeout(() => {
      if (decided) return
      decided = true
      resolve(false)
    }, ENHANCE_FINGERPRINT_LOCK_TIMEOUT_MS)
    try {
      const request = locks.request(
        `maestro-prompt-enhance-tab-${token}`,
        { mode: 'exclusive', ifAvailable: true },
        lock => {
          if (decided) return undefined
          decided = true
          globalThis.clearTimeout(timer)
          if (!lock) {
            resolve(false)
            return undefined
          }
          resolve(true)
          return holdForRealmLifetime
        },
      )
      const tracked = Promise.resolve(request).catch(() => {
        if (decided) return
        decided = true
        globalThis.clearTimeout(timer)
        resolve(false)
      })
      _enhanceFingerprintLockRequests.add(tracked)
      void tracked.finally(() => _enhanceFingerprintLockRequests.delete(tracked))
    } catch {
      if (decided) return
      decided = true
      globalThis.clearTimeout(timer)
      resolve(false)
    }
  })
}

async function _initializeEnhanceFingerprintSalt(): Promise<Uint8Array> {
  const stored = _readEnhanceFingerprintClaim()
  if (stored && await _tryClaimEnhanceFingerprintToken(stored.token)) {
    _enhanceFingerprintClaimRecord = stored
    _enhanceReloadRecoveryAvailable = true
    _enhanceFingerprintClaimRotatedStored = false
    _volatileEnhanceFingerprintSalt = Uint8Array.from(
      stored.salt.match(/.{2}/g) ?? [], value => Number.parseInt(value, 16),
    )
    return _volatileEnhanceFingerprintSalt
  }

  // A copied/duplicated tab cannot acquire the predecessor's token lock.
  // Missing, failed, and timed-out Web Locks also rotate rather than trust
  // copied sessionStorage; reload recovery is unavailable unless this claim wins.
  const replacement = _newEnhanceFingerprintClaim()
  const replacementClaimed = await _tryClaimEnhanceFingerprintToken(replacement.token)
  _enhanceFingerprintClaimRecord = replacement
  _enhanceReloadRecoveryAvailable = replacementClaimed
  _enhanceFingerprintClaimRotatedStored = Boolean(stored)
  _writeEnhanceFingerprintClaim(replacement)
  _volatileEnhanceFingerprintSalt = Uint8Array.from(
    replacement.salt.match(/.{2}/g) ?? [], value => Number.parseInt(value, 16),
  )
  return _volatileEnhanceFingerprintSalt
}

async function _enhanceFingerprintSalt(): Promise<Uint8Array> {
  if (!_enhanceFingerprintClaimPromise) {
    _enhanceFingerprintClaimPromise = _initializeEnhanceFingerprintSalt()
  }
  const salt = await _enhanceFingerprintClaimPromise
  try {
    if (_enhanceFingerprintClaimRecord) {
      _writeEnhanceFingerprintClaim(_enhanceFingerprintClaimRecord)
    }
  } catch { /* cached same-realm claim remains authoritative */ }
  return salt
}

async function _enhanceSha256(bytes: ArrayBuffer): Promise<string> {
  if (!globalThis.crypto.subtle) {
    throw new Error('Prompt Enhance recovery requires browser SHA-256 support')
  }
  return _enhanceBytesToHex(await globalThis.crypto.subtle.digest('SHA-256', bytes))
}

async function _enhanceHmacSha256(value: unknown): Promise<string> {
  if (!globalThis.crypto.subtle) {
    throw new Error('Prompt Enhance recovery requires browser SHA-256 support')
  }
  const salt = await _enhanceFingerprintSalt()
  const keyBytes = new Uint8Array(salt.byteLength)
  keyBytes.set(salt)
  const key = await globalThis.crypto.subtle.importKey(
    'raw', keyBytes,
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  )
  const message = new TextEncoder().encode(JSON.stringify(value))
  return _enhanceBytesToHex(await globalThis.crypto.subtle.sign('HMAC', key, message))
}

async function _enhanceFileIdentity(file: File | null): Promise<readonly unknown[] | null> {
  if (!file) return null
  return [
    file.name,
    file.size,
    file.type,
    file.lastModified,
    file.webkitRelativePath,
    await _enhanceSha256(await file.arrayBuffer()),
  ]
}

async function _enhanceSettingsFingerprint(state: AppState): Promise<string> {
  const startPath = Array.isArray(state.params.image_start)
    ? [...state.params.image_start]
    : state.params.image_start
  const referencePaths = state.params.image_refs ? [...state.params.image_refs] : undefined
  const activatedLoras = [...state.params.activated_loras]
  const startImage = state.startImage
  const imageRefs = [...state.imageRefs]
  return _enhanceHmacSha256([
    state.generationMode,
    state.params.model_type,
    state.params.image_mode,
    state.params.multi_prompts_gen_type,
    state.durationSeconds,
    state.slidingWindowSeconds,
    state.slidingWindowOverlap,
    state.params.force_fps,
    state.params.video_guide,
    state.guideVideoFps,
    state.guideVideoFrameCount,
    state.ttsVoiceCount,
    state.explicitOutput,
    activatedLoras,
    startPath,
    referencePaths,
    await _enhanceFileIdentity(startImage),
    await Promise.all(imageRefs.map(file => _enhanceFileIdentity(file))),
  ])
}

function _advanceAccountIdentityEpoch(): void {
  _accountIdentityEpoch += 1
  _workspaceLoadSequence += 1
  _accountProjectMigrationRequestSequence += 1
  _sampleCampaignQueueRequestSequence += 1
  _sampleCampaignKnownJobIds.clear()
}

function _accountIdentityIsCurrent(epoch: number): boolean {
  return epoch === _accountIdentityEpoch
}

function _sampleCampaignJobIds(
  pairs: readonly api.SampleCampaignQueuePair[],
): Set<string> {
  return new Set(pairs.flatMap(entry => entry.arms.map(arm => arm.job_id)))
}

function _scrubAccountBoundProjectUi(state: AppState): Partial<AppState> {
  for (const workspace of state.workspaces) hidePrivatePreviewsForWorkspace(workspace.name)
  for (const poll of _recoveryJobPolls.values()) poll.stop()
  _recoveryJobPolls.clear()
  for (const jobId of [..._terminalJobWaiters.keys()]) {
    _rejectTerminalJobWaiter(jobId, 'Account changed while waiting for generation')
  }
  _outputsRequestGeneration += 1
  _metadataRequestGeneration += 1
  _h3PlanReviewSequence += 1
  _directorPipelineLifecycleToken = null
  _dashboardPipelineLoadToken += 1
  _dashboardPipelineListLoadToken += 1
  _stopDirectorPreparationPoll()
  _storeDirectorPreparation(null, null)
  _enhancePromptEditGeneration += 1
  // Initial account hydration may legitimately recover a record for that
  // same account. A real scrub/logout always has a previous context and must
  // remove every prior-account recovery fence.
  if (state.accountContext !== null) void _clearStoredEnhanceOperations()
  _enhanceStopWaiting?.()
  _enhanceLlmRequestToken = null
  _enhanceStopWaiting = null
  _enhanceWaitSignal = null
  _enhanceSubmissionAttemptedRequestId = null
  return {
    workspaces: [],
    activeWorkspace: '',
    browsingUploads: false,
    outputs: [],
    outputsTotal: 0,
    outputsLoading: false,
    selectedOutput: 0,
    selectedOutputMeta: null,
    selectedOutputMetaName: null,
    metadataLoading: false,
    selectedOutputKeys: [],
    gallerySelectionMode: false,
    jobs: [],
    isGenerating: false,
    isEnhancing: false,
    enhanceStatus: null,
    enhanceRequestScope: null,
    sampleCampaignPairs: [],
    params: { ...state.params, ...BLANK_VIDEO_INPUT_PARAMS },
    startImage: null,
    endImage: null,
    continueVideo: null,
    continueVideoPath: '',
    continueVideoUrl: '',
    continueVideoDuration: 0,
    audioGuideFilename: null,
    imageRefs: [],
    clips: [],
    videoSubModeStash: {},
    toolsSourcePath: null,
    toolsSourceName: null,
    toolsSourceUrl: null,
    toolsRevoiceRefs: [null, null],
    directorStep: 'upload',
    directorAudioFile: null,
    directorAudioPath: null,
    directorAnalysis: null,
    directorPlannedClips: [],
    directorClipPlans: [],
    directorSceneDescription: '',
    directorVisualStyle: '',
    directorCustomVisualStyle: '',
    directorLoadingMessage: null,
    directorError: null,
    directorComponentError: null,
    directorReferenceImage: null,
    directorReferenceImagePath: null,
    directorCharacterRefs: [],
    directorCharacterRefPaths: [],
    directorCharacterRefLabels: [],
    directorLocationRefs: [],
    directorLocationRefPaths: [],
    directorLocationRefLabels: [],
    directorVoiceRef: null,
    directorVoiceRefPath: null,
    directorClipImages: [],
    directorImageGenProgress: null,
    directorSpeakers: [],
    directorSpeakerMappings: [],
    directorMusicSource: null,
    directorSongDescription: '',
    directorSongStyle: '',
    directorSongLyrics: '',
    directorTrackGenerating: false,
    pipelineId: null,
    pipelineStatus: null,
    pipelinePolling: false,
    directorLoading: false,
    directorRequestId: null,
    directorRequestWorkspace: null,
    directorPreparationStatus: null,
    shortFilmCharacters: [],
    shortFilmPath: null,
    dashboardOpen: false,
    dashboardPipelineList: [],
    dashboardPipelineListRead: { workspace: '', generation: _dashboardPipelineListLoadToken, status: 'idle' },
    dashboardSelectedPipeline: null,
    dashboardLoading: false,
    pendingH3Plan: null,
    pendingH3PlanEstimate: null,
    pendingH3PlanJobId: null,
    pendingH3PlanWorkspace: null,
    h3PlanReviewLoading: false,
    h3PlanReviewError: null,
  }
}

function _invalidateAccountRequests(): void {
  _accessContextRequestSequence += 1
  _accountContextRequestSequence += 1
  _accountSessionsRequestSequence += 1
  _accountUsersRequestSequence += 1
  _accountProjectMigrationRequestSequence += 1
}

function _beginAccountMutation(advanceIdentity = true): number {
  _invalidateAccountRequests()
  if (advanceIdentity) _advanceAccountIdentityEpoch()
  return ++_accountMutationRequestSequence
}

export const useStore = create<AppState>((set, get) => ({
  // Generation mode
  generationMode: 'video',
  editSubMode: 'retake' as import('../types').EditSubMode,
  setEditSubMode: (mode: import('../types').EditSubMode) => {
    const s = get()
    const prev = s.editSubMode
    set({ editSubMode: mode })
    if (mode === prev || s.generationMode !== 'avatar') return
    // Recast uses SCAIL-2 Replace; Repaint uses the proven SCAIL-2 Animate
    // path from Studio Video/Frames. Swap recipes when moving between those
    // modes and restore the previous LTX edit model when leaving both.
    const current = (s.params.model_type as string) || ''
    const isScail2 = (mt: string) => s.models.find(m => m.model_type === mt)?.architecture === 'scail2_14B'
    const enteringScail2Edit = mode === 'recast' || mode === 'restyle'
    const leavingScail2Edit = prev === 'recast' || prev === 'restyle'
    if (enteringScail2Edit) {
      const valid = mode === 'recast'
        ? current === 'scail2_14B_recast_fast' || current === 'scail2_14B'
        : current === 'scail2_14B_fast' || current === 'scail2_14B'
      if (!valid) {
        if (!leavingScail2Edit && !isScail2(current)) {
          _preScail2AvatarModel = current
        }
        const preferred = mode === 'recast'
          ? 'scail2_14B_recast_fast'
          : 'scail2_14B_fast'
        const target = s.models.some(m => m.model_type === preferred)
          ? preferred
          : s.models.some(m => m.model_type === 'scail2_14B')
            ? 'scail2_14B'
            : undefined
        if (target) get().selectModel(target)
      }
    } else if (leavingScail2Edit && isScail2(current)) {
      const restore = _preScail2AvatarModel && s.models.some(m => m.model_type === _preScail2AvatarModel)
        ? _preScail2AvatarModel
        : getDefaultModelForMode('avatar', s.families, s.models)
      if (restore) get().selectModel(restore)
    }
  },
  editVideoPath: '',
  editVideoUrl: '',
  editVideoFile: null,
  editVideoDuration: 0,
  editVideoResolution: '',
  editStartTime: 0,
  editEndTime: 5,
  editRetakeStrength: 0.85,
  editPromptStrength: 3.5,
  editAnythingLoraStrength: 1.0,
  editAnythingStartAnchor: null,
  editAnythingEndAnchor: null,
  editRepaintFrameFile: null,
  editRepaintFramePath: '',
  editRepaintFrameUrl: '',
  editRepaintMappings: [],
  editRepaintResolutionProfile: '480p',
  setEditRepaintFrame: (file, path, url) => set({
    editRepaintFrameFile: file,
    editRepaintFramePath: path,
    editRepaintFrameUrl: url,
  }),
  setEditRepaintMappings: mappings => set({
    editRepaintMappings: mappings.slice(0, 5),
  }),
  editRecastTarget: 'person',
  editRecastPersonCount: 1,
  editRecastRefFile: null,
  editRecastRefPath: '',
  editRecastRefUrl: '',
  editRecastMappings: [{ ...DEFAULT_RECAST_MAPPING }],
  editRecastRefAligned: false,
  editRecastIsolateReference: true,
  editRecastAutoFaceDetail: true,
  editRecastEnhancePrompt: false,
  editRecastProtectBystanders: false,
  editRecastPreserveBystanders: true,
  editRecastUseRelighting: false,
  editRecastResolutionProfile: '480p',
  setEditRecastMappings: mappings => set({
    editRecastMappings: mappings,
    editRecastTarget: mappings[0]?.target || 'person',
    editRecastPersonCount: Math.min(5, Math.max(1, mappings.length || 1)),
    editRecastRefFile: mappings[0]?.refFile || null,
    editRecastRefPath: mappings[0]?.refPath || '',
    editRecastRefUrl: mappings[0]?.refUrl || '',
    editRecastRefAligned: mappings[0]?.referenceAlignedToSource === true,
  }),
  setEditRecastRef: (file, path, url, aligned = false) => set(s => ({
    editRecastRefFile: file,
    editRecastRefPath: path,
    editRecastRefUrl: url,
    editRecastRefAligned: aligned,
    editRecastMappings: [
      {
        ...(s.editRecastMappings[0] || DEFAULT_RECAST_MAPPING),
        refFile: file,
        refPath: path,
        refUrl: url,
        referenceAlignedToSource: aligned,
      },
      ...s.editRecastMappings.slice(1),
    ],
  })),
  editReturnTarget: null,
  setEditAnythingStartAnchor: (path: string | null) => set({ editAnythingStartAnchor: path }),
  setEditAnythingEndAnchor: (path: string | null) => set({ editAnythingEndAnchor: path }),
  sendFrameToImageMode: async (which: 'start' | 'end' | 'recast' | 'repaint') => {
    const state = get()
    const clipPath = state.editVideoPath
    if (!clipPath) {
      console.error('Edit Anything: no source video loaded')
      return
    }
    const startTime = state.editStartTime || 0
    const endTime = state.editEndTime || state.editVideoDuration || 0
    if (endTime <= startTime) {
      console.error('Edit Anything: invalid trim range')
      return
    }

    // Snapshot user's current image-mode reference state BEFORE the
    // hijack so we can restore it on return / skip / cancel and not
    // disturb their non-Edit-Anything Image-mode workflow.
    const savedImageRefs = state.imageRefs
    const savedImageRefType = state.imageRefType

    // Decide which timestamp to grab. End frame is one frame INSIDE the
    // exclusive end (at -0.04s = ~one frame at 25fps) so it matches what
    // the retake pipeline will pin during inference.
    const tStart = which === 'end' ? Math.max(0, endTime - 0.04) : startTime
    try {
      let framePath = ''
      let frameUrl = ''
      // Repaint can refine its existing edited frame. The first trip starts
      // from the source trim frame; later trips start from the applied result.
      if (which === 'repaint' && state.editRepaintFramePath) {
        framePath = state.editRepaintFramePath
        const frameName = framePath.replace(/\\/g, '/').split('/').pop() || ''
        frameUrl = state.editRepaintFrameUrl || api.getFileUrl(frameName)
      } else {
        const res = await fetch('/api/v1/extract-frames', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            video_path: clipPath,
            ...(which === 'end' ? { end_time: tStart } : { start_time: tStart }),
          }),
        })
        if (!res.ok) throw new Error(`extract-frames failed: ${res.status}`)
        const data = await res.json()
        framePath = (which === 'end' ? data.end_path : data.start_path) as string
        frameUrl = (which === 'end' ? data.end_url : data.start_url) as string
      }

      // Use setGenerationMode rather than poking generationMode directly.
      // This is the proper switch — it picks the right model for image
      // mode (auto-restoring the user's last image-mode model or the
      // family default), reloads LoRAs, and resets image_mode + the
      // resolution/aspect presets that go with image generation. Without
      // this, the model stays on whatever LTX-2 video model was active.
      get().setGenerationMode('image')

      // Load the extracted frame into Image mode's REFERENCE images list
      // (the "Reference Images" drop zone in the sidebar). This is the
      // i2i / image-edit input slot — distinct from video mode's
      // image_start (which is i2v's "first frame"). ImageRefSection's
      // own useEffect picks the right imageRefType when imageRefs goes
      // from empty to populated; we leave that to it.
      const blob = await fetch(frameUrl).then(r => r.blob())
      const file = new File([blob], `${which}_frame.png`, { type: blob.type || 'image/png' })
      set(s => ({
        // Replace any pre-existing refs with just our extracted frame
        // for the duration of the round-trip. Restored from the
        // editReturnTarget snapshot when we return.
        imageRefs: [file],
        imageRefType: '',  // let ImageRefSection re-set the default for the new model
        // Make sure no stale i2v fields are populated — those would land
        // in video mode's i2v slot, which isn't what we want here.
        startImage: null,
        params: { ...s.params, image_start: '', image_mode: 1 },
        editReturnTarget: {
          anchor: which,
          framePath,
          clipPath,
          startTime,
          endTime,
          savedImageRefs,
          savedImageRefType,
        },
      }))
    } catch (e) {
      console.error('Failed to send frame to Image mode:', e)
    }
  },
  applyOutputAsAnchor: async () => {
    const state = get()
    const target = state.editReturnTarget
    if (!target) return
    // Find the latest image-mode output (newest first in the outputs list).
    const latestImage = state.outputs.find(o => o.type === 'image')
    if (!latestImage) {
      console.error('Edit Anything return: no image-mode output yet to apply')
      return
    }
    // The backend resolver in /api/v1/edit-anything will look in the
    // active workspace's outputs/ for a bare filename, so passing the
    // gallery name is enough.
    const outputPath = latestImage.name

    if (target.anchor === 'recast') {
      set(s => ({
        editRecastRefFile: null,
        editRecastRefPath: outputPath,
        editRecastRefUrl: latestImage.url,
        editRecastRefAligned: true,
        editRecastMappings: [
          {
            ...(s.editRecastMappings[0] || DEFAULT_RECAST_MAPPING),
            refFile: null,
            refPath: outputPath,
            refUrl: latestImage.url,
            referenceAlignedToSource: true,
          },
          ...s.editRecastMappings.slice(1),
        ],
      }))
    } else if (target.anchor === 'repaint') {
      set({
        editRepaintFrameFile: null,
        editRepaintFramePath: outputPath,
        editRepaintFrameUrl: latestImage.url,
      })
    } else if (target.anchor === 'start') {
      set({ editAnythingStartAnchor: outputPath })
    } else {
      set({ editAnythingEndAnchor: outputPath })
    }

    // Restore the user's pre-round-trip image-mode reference state and
    // switch back to Edit Anything. setGenerationMode handles the model
    // swap so they land back on their video model with the right LoRAs.
    get().setGenerationMode('avatar')
    set({
      editSubMode: target.anchor === 'recast'
        ? 'recast'
        : target.anchor === 'repaint'
          ? 'restyle'
          : 'edit_anything',
      editReturnTarget: null,
      imageRefs: target.savedImageRefs,
      imageRefType: target.savedImageRefType,
    })
  },
  skipAnchorPhase: () => {
    // Skip = return to Edit Anything without setting the anchor. Empty
    // slot → ltx2.py falls back to the source-extracted frame at
    // generation time (the morph-from-source default).
    const target = get().editReturnTarget
    get().setGenerationMode('avatar')
    set({
      editSubMode: target?.anchor === 'recast'
        ? 'recast'
        : target?.anchor === 'repaint'
          ? 'restyle'
          : 'edit_anything',
      editReturnTarget: null,
      ...(target ? { imageRefs: target.savedImageRefs, imageRefType: target.savedImageRefType } : {}),
    })
  },
  cancelAnchorReturn: () => {
    const target = get().editReturnTarget
    get().setGenerationMode('avatar')
    set({
      editSubMode: target?.anchor === 'recast'
        ? 'recast'
        : target?.anchor === 'repaint'
          ? 'restyle'
          : 'edit_anything',
      editReturnTarget: null,
      ...(target ? { imageRefs: target.savedImageRefs, imageRefType: target.savedImageRefType } : {}),
    })
  },
  editRetakeEngine: 'native' as const,
  editRegenerateAudio: true,
  editSamTarget: '',
  editInvertMask: false,
  editMasksPath: null,
  editMaskPreview: null,
  editDetectedTarget: '',
  continueVideo: null,
  continueVideoPath: '',
  continueVideoUrl: '',
  continueVideoDuration: 0,
  videoSubModeStash: {},
  setContinueVideo: (file, path, url, duration) => set({
    continueVideo: file, continueVideoPath: path, continueVideoUrl: url, continueVideoDuration: duration,
  }),
  clearContinueVideo: () => {
    // Also strip "V" from image_prompt_type — removing the source video
    // means the user is no longer in extend mode, so any leftover "V"
    // flag would cause the backend to demand a video_source we just
    // cleared. startGeneration has a defensive strip at submit time as
    // well, but cleaning state here keeps things consistent for any UI
    // that reads image_prompt_type directly.
    const currentParams = useStore.getState().params
    const ipt = (currentParams.image_prompt_type as string) || ''
    set({
      continueVideo: null, continueVideoPath: '', continueVideoUrl: '', continueVideoDuration: 0,
      params: {
        ...currentParams,
        video_source: undefined,
        image_prompt_type: ipt.replace(/V/g, ''),
      },
    })
  },
  blendClipA: null, blendClipAPath: '', blendClipAUrl: '', blendClipADuration: 0,
  blendClipB: null, blendClipBPath: '', blendClipBUrl: '', blendClipBDuration: 0,
  blendTransitionSec: 5,
  blendStrengthA: 1.0,
  blendStrengthB: 0.7,
  blendMotionPrefixSec: 1.0,
  blendMotionSuffixSec: 1.0,
  blendAnchorStrength: 0.7,
  setBlendClipA: (file, path, url, duration) => set({
    blendClipA: file, blendClipAPath: path, blendClipAUrl: url, blendClipADuration: duration,
  }),
  setBlendClipB: (file, path, url, duration) => set({
    blendClipB: file, blendClipBPath: path, blendClipBUrl: url, blendClipBDuration: duration,
  }),
  clearBlendClipA: () => set({ blendClipA: null, blendClipAPath: '', blendClipAUrl: '', blendClipADuration: 0 }),
  clearBlendClipB: () => set({ blendClipB: null, blendClipBPath: '', blendClipBUrl: '', blendClipBDuration: 0 }),
  setBlendTransitionSec: (sec) => set({ blendTransitionSec: sec }),
  setBlendStrengthA: (v) => set({ blendStrengthA: v }),
  setBlendStrengthB: (v) => set({ blendStrengthB: v }),
  setBlendMotionPrefixSec: (v) => set({ blendMotionPrefixSec: v }),
  setBlendMotionSuffixSec: (v) => set({ blendMotionSuffixSec: v }),
  setBlendAnchorStrength: (v) => set({ blendAnchorStrength: v }),
  blendMode: 'overlap' as const,
  blendOverlapSec: 3,
  setBlendMode: (mode) => set({ blendMode: mode }),
  setBlendOverlapSec: (sec) => set({ blendOverlapSec: sec }),
  outpaintPadding: { top: 0, bottom: 0, left: 0, right: 0 },
  setOutpaintPadding: (padding) => set({ outpaintPadding: padding }),
  outpaintResolutionPreset: 'auto',
  setOutpaintResolutionPreset: (preset) => set({ outpaintResolutionPreset: preset }),
  // 'source' = canvas matches source aspect (no extension by default)
  outpaintAspect: 'source',
  setOutpaintAspect: (a) => set({ outpaintAspect: a }),
  // Default video box: full canvas (no padding). Will be re-fitted by the
  // OutpaintCanvas when the user picks a non-source aspect.
  outpaintVideoBox: { x: 0, y: 0, w: 1, h: 1 },
  setOutpaintVideoBox: (box) => set({ outpaintVideoBox: box }),
  outpaintTrimStart: 0,
  outpaintTrimEnd: 0,
  setOutpaintTrimStart: (t) => set({ outpaintTrimStart: t }),
  setOutpaintTrimEnd: (t) => set({ outpaintTrimEnd: t }),
  outpaintSourcePreservation: 1.0,
  setOutpaintSourcePreservation: (v) => set({ outpaintSourcePreservation: v }),
  outpaintLoraStrength: 1.0,
  setOutpaintLoraStrength: (v) => set({ outpaintLoraStrength: v }),
  outpaintMaskPreserving: true,
  setOutpaintMaskPreserving: (v) => set({ outpaintMaskPreserving: v }),
  outpaintPreserveSourceAudio: true,
  setOutpaintPreserveSourceAudio: (v) => set({ outpaintPreserveSourceAudio: v }),
  outpaintLockSourcePixels: false,  // default OFF — visible rectangle seam outweighs benefit
  setOutpaintLockSourcePixels: (v) => set({ outpaintLockSourcePixels: v }),
  outpaintTrimSmear: true,  // default ON — fixes the 9-frame stutter at window 1→2 boundary
  setOutpaintTrimSmear: (v) => set({ outpaintTrimSmear: v }),
  outpaintWindowSize: 241,  // LTX-2 default (~10s @ 24fps)
  setOutpaintWindowSize: (v) => set({ outpaintWindowSize: v }),
  outpaintWindowOverlap: 9,  // LTX-2 default
  setOutpaintWindowOverlap: (v) => set({ outpaintWindowOverlap: v }),
  setEditVideoPath: (path) => set({ editVideoPath: path }),
  setEditVideo: (file, path, url, duration, resolution) => set({
    editVideoFile: file, editVideoPath: path, editVideoUrl: url,
    editVideoDuration: duration, editVideoResolution: resolution,
    editEndTime: duration,
  }),
  clearEditVideo: () => set({
    editVideoFile: null, editVideoPath: '', editVideoUrl: '',
    editVideoDuration: 0, editVideoResolution: '', editStartTime: 0, editEndTime: 5,
    editMasksPath: null, editMaskPreview: null, editDetectedTarget: '',
  }),
  musicDescription: '',
  setMusicDescription: (s) => set({ musicDescription: s }),
  musicInstrumental: false,
  setMusicInstrumental: (b) => set({ musicInstrumental: b }),
  audioSubMode: 'speech' as import('../types').AudioSubMode,
  selectedModelPerAudioSubMode: {} as Partial<Record<import('../types').AudioSubMode, string>>,
  setAudioSubMode: (subMode) => {
    const { audioSubMode: prevSub, params, models } = get()
    if (subMode === prevSub) return
    // Save current model for the sub-mode we're leaving
    const savedModels = { ...get().selectedModelPerAudioSubMode, [prevSub]: params.model_type }
    // Determine model for target sub-mode
    const audioSubModeDefaults: Record<import('../types').AudioSubMode, string> = {
      speech: 'kugelaudio_0_open',
      // XL SFT LM_4B: the premium CFG variant + strongest LM — the
      // quality default. Turbo variants remain enabled for speed.
      music: 'ace_step_v1_5_xl_sft_lm_4b',
      sfx: 'mmaudio_v2',
      mixer: '',  // Mixer doesn't use a model — it's an ffmpeg-based tool
    }
    const saved = savedModels[subMode]
    const targetModel = (saved && models.some(m => m.model_type === saved))
      ? saved
      : audioSubModeDefaults[subMode]
    set({ audioSubMode: subMode, selectedModelPerAudioSubMode: savedModels })
    if (targetModel && models.some(m => m.model_type === targetModel)) {
      get().selectModel(targetModel)
    }
  },
  selectedModelPerMode: {},
  savedLoraPerMode: {},
  savedParamsPerMode: {},
  savedPromptPerMode: {} as Partial<Record<string, string>>,
  h3StyleWorkflow: _storedH3StyleWorkflow(),
  h3StyleWorkflowCatalog: null,
  h3StyleWorkflowCatalogLoading: false,
  h3StyleWorkflowCatalogError: null,

  setH3StyleWorkflow: (id) => {
    const catalog = get().h3StyleWorkflowCatalog
    if (id && !h3StyleWorkflowSelectionIsCurrent(catalog, id)) {
      _storeH3StyleWorkflow('')
      set({
        h3StyleWorkflow: '',
        h3StyleWorkflowCatalogError: 'That H3 workflow is no longer in the server catalog. Choose another workflow.',
      })
      return
    }
    _storeH3StyleWorkflow(id)
    set({ h3StyleWorkflow: id, h3StyleWorkflowCatalogError: null })
  },

  loadH3StyleWorkflowCatalog: async (force = false) => {
    const current = get()
    if (!force && (current.h3StyleWorkflowCatalog || current.h3StyleWorkflowCatalogLoading)) return
    const seq = ++_h3StyleWorkflowCatalogSeq
    set({ h3StyleWorkflowCatalogLoading: true, h3StyleWorkflowCatalogError: null })
    try {
      const catalog = await api.fetchH3StyleWorkflows()
      if (seq !== _h3StyleWorkflowCatalogSeq) return
      const selected = get().h3StyleWorkflow
      const selectionCurrent = h3StyleWorkflowSelectionIsCurrent(catalog, selected)
      if (!selectionCurrent) _storeH3StyleWorkflow('')
      set({
        h3StyleWorkflowCatalog: catalog,
        h3StyleWorkflowCatalogLoading: false,
        h3StyleWorkflow: selectionCurrent ? selected : '',
        h3StyleWorkflowCatalogError: selectionCurrent
          ? null
          : 'Your saved H3 workflow is no longer in the server catalog and was cleared.',
      })
    } catch {
      if (seq !== _h3StyleWorkflowCatalogSeq) return
      _storeH3StyleWorkflow('')
      set({
        h3StyleWorkflowCatalog: null,
        h3StyleWorkflowCatalogLoading: false,
        h3StyleWorkflow: '',
        h3StyleWorkflowCatalogError: 'The server H3 workflow catalog is unavailable. No workflow was selected.',
      })
    }
  },

  migrateLegacyH3StylePrompt: () => {
    if (_legacyH3StylePrefixMigrationWasCompleted()) return
    const current = get()
    const prompt = stripLegacyH3StylePrefix(current.params.prompt)
    const savedPromptPerMode = Object.fromEntries(
      Object.entries(current.savedPromptPerMode).map(([mode, saved]) => [
        mode,
        typeof saved === 'string' ? stripLegacyH3StylePrefix(saved) : saved,
      ]),
    )
    const changed = prompt !== current.params.prompt
      || Object.entries(savedPromptPerMode).some(([mode, saved]) => (
        current.savedPromptPerMode[mode] !== saved
      ))
    if (changed) {
      set(state => ({
        params: { ...state.params, prompt },
        savedPromptPerMode,
      }))
      const migrated = get()
      _saveSettings({
        generationMode: migrated.generationMode,
        selectedModelPerMode: migrated.selectedModelPerMode,
        savedParamsPerMode: migrated.savedParamsPerMode,
        savedLoraPerMode: migrated.savedLoraPerMode,
        savedPromptPerMode: migrated.savedPromptPerMode,
      }, migrated.loraIdByFilename)
    }
    _completeLegacyH3StylePrefixMigration()
  },

  setGenerationMode: (mode) => {
    if (mode !== get().generationMode) {
      ++_h3ProfileApplySeq
      set(state => ({
        h3ProfileApplying: null,
        h3SelectedProfile: 'custom',
        modelOptionsLoading: state.h3ProfileApplying ? false : state.modelOptionsLoading,
      }))
    }
    // Tools is a non-generative post-processing area — it owns no model, so
    // skip the per-mode model/LoRA/params RESTORE machinery entirely. We still
    // SAVE the leaving mode's state (prompt / model / LoRAs / params snapshot)
    // so returning to it restores correctly, leave `params` untouched (no model
    // load, no defaults reset), and persist the *previous* real mode as the
    // landing mode so a reload doesn't drop into Tools with no model loaded.
    if (mode === 'tools') {
      const s = get()
      const prev = s.generationMode
      if (prev === 'tools') { set({ generationMode: 'tools' }); return }
      const paramsSnapshot = _snapshotModeParams(s.params)
      const savedModels = { ...s.selectedModelPerMode, [prev]: s.params.model_type }
      const savedParams = {
        ...s.savedParamsPerMode,
        [prev]: { ...paramsSnapshot, filmGrainIntensity: s.filmGrainIntensity, filmGrainSaturation: s.filmGrainSaturation, durationSeconds: s.durationSeconds },
      }
      const savedLoras = {
        ...s.savedLoraPerMode,
        [prev]: { activated_loras: s.params.activated_loras || [], loras_multipliers: s.params.loras_multipliers || '', loraWeights: s.loraWeights, availableLoras: s.availableLoras },
      }
      const savedPrompts = { ...s.savedPromptPerMode, [prev]: s.params.prompt }
      set({
        generationMode: 'tools',
        selectedModelPerMode: savedModels,
        savedParamsPerMode: savedParams,
        savedLoraPerMode: savedLoras,
        savedPromptPerMode: savedPrompts,
      })
      _saveSettings({ generationMode: prev, selectedModelPerMode: savedModels, savedParamsPerMode: savedParams, savedLoraPerMode: savedLoras, savedPromptPerMode: savedPrompts }, s.loraIdByFilename)
      return
    }
    const { families, models, generationMode: prevMode, params, selectedModelPerMode, savedLoraPerMode, savedParamsPerMode, loraWeights, availableLoras, savedPromptPerMode } = get()
    // Save prompt for the mode we're leaving
    const savedPrompts = { ...savedPromptPerMode, [prevMode]: params.prompt }
    // Save current model + LoRA + params state for the mode we're leaving
    const savedModels = { ...selectedModelPerMode, [prevMode]: params.model_type }
    const savedLoras = {
      ...savedLoraPerMode,
      [prevMode]: {
        activated_loras: params.activated_loras || [],
        loras_multipliers: params.loras_multipliers || '',
        loraWeights,
        availableLoras,
      },
    }
    // Save the FULL params snapshot for the leaving mode. Strip the
    // fields that are tracked separately in their own per-mode state
    // structures (model_type → selectedModelPerMode, prompt →
    // savedPromptPerMode, activated_loras / loras_multipliers →
    // savedLoraPerMode) to avoid double-bookkeeping. Everything else
    // — including repeat_generation, negative_prompt, video_prompt_type,
    // video_guide, image_refs, frames_positions, MMAudio_*, etc. — is
    // captured here so it survives a switch-and-return AND doesn't
    // leak into other modes.
    const paramsSnapshot = _snapshotModeParams(params)
    const savedParams = {
      ...savedParamsPerMode,
      [prevMode]: {
        ...paramsSnapshot,
        filmGrainIntensity: get().filmGrainIntensity,
        filmGrainSaturation: get().filmGrainSaturation,
        // Save durationSeconds per-mode so audio's 600/1800 (Kugel/Scenema
        // slider max) doesn't leak into video on mode-switch back. Audio
        // mode's loadModelOptions still overrides with the slider.max on
        // model select, so this only matters for video/image/avatar.
        durationSeconds: get().durationSeconds,
      },
    }
    // Restore saved model for target mode, or fall back to default
    const savedModel = savedModels[mode]
    const restoredModel = savedModel && models.some(m => m.model_type === savedModel)
      ? savedModel
      : getDefaultModelForMode(mode, families, models)
    const newModelType = restoredModel || params.model_type
    // Restore saved LoRA state for target mode (if same model)
    const restoredLora = savedLoras[mode]
    const sameModel = restoredLora && savedModel === newModelType
    // Restore the saved params snapshot for the target mode. If the
    // user never visited this mode before, fall back to defaultParams
    // (NOT the previous mode's params — that's what caused the leak).
    const restoredSnapshot = savedParams[mode]
    // Extract film grain from snapshot (top-level store state, not in params)
    const restoredFilmGrain = restoredSnapshot
      ? { filmGrainIntensity: restoredSnapshot.filmGrainIntensity ?? 0, filmGrainSaturation: restoredSnapshot.filmGrainSaturation ?? 0.5 }
      : { filmGrainIntensity: 0, filmGrainSaturation: 0.5 }
    // Restore durationSeconds for the target mode. Non-audio modes (video,
    // avatar, image) fall back to 5s on first visit. Audio mode's
    // durationSeconds gets overridden by loadModelOptions when it sees
    // audio_only && duration_slider, so the snapshot value is mostly
    // ignored there — it's still saved for symmetry.
    const restoredDuration = restoredSnapshot && typeof restoredSnapshot.durationSeconds === 'number'
      ? restoredSnapshot.durationSeconds as number
      : 5
    // Strip filmGrain + durationSeconds keys before applying — they don't belong in params
    const restoredParams = _restoreModeParams(restoredSnapshot)
    // Restore saved prompt for target mode (or empty for first visit)
    const restoredPrompt = savedPrompts[mode] ?? ''

    set(() => ({
      generationMode: mode,
      selectedModelPerMode: savedModels,
      savedLoraPerMode: savedLoras,
      savedParamsPerMode: savedParams,
      savedPromptPerMode: savedPrompts,
      // Default to Auto resolution + aspect in image mode (matches reference image)
      ...(mode === 'image' ? { resolutionPreset: 'auto' as ResolutionPreset, aspectRatio: 'auto' as AspectRatio } : {}),
      ...restoredFilmGrain,
      durationSeconds: restoredDuration,
      // Build params from defaults + restored snapshot. We deliberately
      // do NOT spread `...s.params` here — that's the line that caused
      // every previous-mode field to leak into the new mode. Starting
      // from defaults ensures only the restored snapshot's fields (the
      // user's actual choices in this mode, or nothing on first visit)
      // are present. Then layer model_type / prompt / LoRAs from their
      // separate stores on top, plus the special image_mode logic.
      params: {
        ...defaultParams,
        ...restoredParams,
        model_type: newModelType,
        prompt: restoredPrompt,
        image_mode: mode === 'image' ? 1 : (restoredParams.image_mode ?? 0),
        activated_loras: sameModel ? restoredLora.activated_loras : [],
        loras_multipliers: sameModel ? restoredLora.loras_multipliers : '',
      },
      loraWeights: sameModel ? restoredLora.loraWeights : {},
      availableLoras: sameModel ? restoredLora.availableLoras : [],
    }))
    if (newModelType && !sfxModelTypes.has(newModelType)) {
      if (!sameModel) {
        get().loadLoras(newModelType)
      }
      // Mode switch counts as a model selection too — apply the new
      // model's defaults so numeric primaries (steps, CFG, flow_shift,
      // sample_solver) match what that model expects rather than what
      // the previous mode's model was using. See _applyModelDefaults
      // for the field list and rationale.
      if (restoredSnapshot) {
        get().loadModelOptions(newModelType)
        // loadModelOptions still refreshes capability/geometry state, but its
        // default-producing fields and the fresh High bundle must not
        // replace this mode's in-session Custom snapshot.
        ++_modelDefaultsSeq
      } else {
        _applyModelDefaults(get, set, newModelType)
        // Capture the just-issued defaults generation so model-options can
        // provide a fallback if /defaults fails. Later manual edits invalidate
        // both default-producing responses without suppressing capabilities.
        get().loadModelOptions(newModelType)
      }
    }
    if (restoredSnapshot && H3_STUDIO_MODELS.has(newModelType)) {
      void get().normalizeH3EditableProfile()
    }
    // Persist to localStorage
    _saveSettings({
      generationMode: mode,
      selectedModelPerMode: savedModels,
      savedParamsPerMode: savedParams,
      savedLoraPerMode: savedLoras,
      savedPromptPerMode: savedPrompts,
    }, get().loraIdByFilename)
  },

  params: { ...defaultParams },
  setParam: (key, value) => {
    if (key === 'prompt' && value !== get().params.prompt) {
      _enhancePromptEditGeneration += 1
    }
    // Per-sub-mode isolation: remember the outgoing sub-mode BEFORE the
    // param write flips image_mode (see videoSubModeStash).
    const prevImageMode = key === 'image_mode' ? ((get().params.image_mode as number) ?? 0) : null
    const profileSettingChanged = H3_PROFILE_PARAM_KEYS.has(key)
    if (profileSettingChanged) {
      ++_h3ProfileApplySeq
      ++_h3CompatibilitySeq
      ++_modelDefaultsSeq
    }
    set(s => ({
      params: { ...s.params, [key]: value },
      ...(profileSettingChanged ? {
        h3SelectedProfile: 'custom' as const,
        h3ProfileApplying: null,
        modelOptionsLoading: s.h3ProfileApplying ? false : s.modelOptionsLoading,
      } : {}),
    }))
    if (key === 'custom_settings' && value && typeof value === 'object') {
      const engine = (value as Record<string, unknown>).h3_attention_engine
      if (engine === 'sdpa' || engine === 'sol_attn' || engine === 'sage2') {
        try { localStorage.setItem(H3_ATTENTION_ENGINE_KEY, engine) } catch { /* storage unavailable */ }
      }
    }
    // Auto-parse speaker names from prompt whenever audio mode has at least
    // one voice slot. Previously gated on audio_prompt_type.includes('B')
    // (multi-voice only), but the user expects single-voice ("Peter: hello")
    // to populate voice slot 1 too. Voice-count gate covers both cases —
    // ttsVoiceCount > 0 means at least one voice clone is active.
    if (key === 'prompt' && typeof value === 'string' && get().generationMode === 'audio' && get().ttsVoiceCount > 0) {
      get()._autoParseSpkeakerNames(value)
    }
    // Handle sub-mode transitions (Frames / Multi-Shot / Extend / Blend)
    if (key === 'image_mode') {
      // Each Studio Video sub-mode is an ISOLATED working set: stash the
      // outgoing sub-mode's full state (prompt, input tiles, settings)
      // and bring back the incoming one. A sub-mode visited for the
      // first time keeps the generic settings but starts with blank
      // inputs — so Extend opens clean while the Frames setup (injected
      // keyframes and all) survives the round-trip untouched.
      const s1 = get()
      if (s1.generationMode === 'video' && typeof value === 'number' && prevImageMode !== null && value !== prevImageMode) {
        const stash = { ...s1.videoSubModeStash, [prevImageMode]: captureVideoSubModeStash(s1) }
        const saved = stash[value]
        if (saved) {
          set({
            videoSubModeStash: stash,
            // Model + LoRA selection stay shared across sub-modes — keep
            // the live values, restore everything else.
            params: {
              ...saved.params,
              image_mode: value,
              model_type: s1.params.model_type,
              activated_loras: s1.params.activated_loras,
              loras_multipliers: s1.params.loras_multipliers,
            },
            startImage: saved.startImage,
            endImage: saved.endImage,
            continueVideo: saved.continueVideo,
            continueVideoPath: saved.continueVideoPath,
            continueVideoUrl: saved.continueVideoUrl,
            continueVideoDuration: saved.continueVideoDuration,
            audioGuideFilename: saved.audioGuideFilename,
            imageRefs: saved.imageRefs,
            imageRefType: saved.imageRefType,
            removeBackgroundRefs: saved.removeBackgroundRefs,
            durationSeconds: saved.durationSeconds,
            slidingWindowSeconds: saved.slidingWindowSeconds,
            slidingWindowOverlap: saved.slidingWindowOverlap,
            clips: saved.clips,
            singlePromptMode: saved.singlePromptMode,
          })
        } else {
          set(s => ({
            videoSubModeStash: stash,
            params: { ...s.params, ...BLANK_VIDEO_INPUT_PARAMS },
            startImage: null,
            endImage: null,
            continueVideo: null,
            continueVideoPath: '',
            continueVideoUrl: '',
            continueVideoDuration: 0,
            audioGuideFilename: null,
            imageRefs: [],
            imageRefType: '',
            removeBackgroundRefs: false,
            // durationSeconds + sliding window intentionally carry over:
            // they're settings, not inputs — they diverge per sub-mode
            // only after the user changes them there.
          }))
        }
      }
      // Multi-clip transitions (after the stash swap so syncClipCount
      // sees the restored duration/params).
      if (value === 2) {
        get().syncClipCount()
      } else {
        set({ clips: [], singlePromptMode: false })
      }
    }
    // Snapshot the changed param into the current mode's IN-MEMORY
    // record so it survives a mode switch + return within this session.
    // Skip keys that are tracked in their own per-mode structures
    // (model_type, prompt, LoRA fields) to avoid double-bookkeeping.
    // Everything else — repeat_generation, negative_prompt,
    // num_inference_steps, video_prompt_type, video_guide, image_refs,
    // frames_positions, MMAudio_*, etc. — gets snapshotted here.
    //
    // Deliberately NOT written to localStorage: a page refresh starts
    // the working state (prompt, seed, LoRA selection, Advanced values)
    // from the model's defaults. v1.2.0 persisted every edit across
    // refreshes and users found the stale text/seeds surprising —
    // in-session mode-switch persistence is the wanted behavior,
    // refresh is a clean slate (see loadModels).
    if (key !== 'model_type' && key !== 'prompt' && key !== 'activated_loras' && key !== 'loras_multipliers') {
      const s = get()
      const mode = s.generationMode
      const paramsSnapshot = _snapshotModeParams(s.params)
      const updatedSavedParams = {
        ...s.savedParamsPerMode,
        [mode]: {
          ...paramsSnapshot,
          filmGrainIntensity: s.filmGrainIntensity,
          filmGrainSaturation: s.filmGrainSaturation,
        },
      }
      set({ savedParamsPerMode: updatedSavedParams })
    }
  },
  setParams: (partial) => {
    if (Object.prototype.hasOwnProperty.call(partial, 'prompt') && partial.prompt !== get().params.prompt) {
      _enhancePromptEditGeneration += 1
    }
    const profileSettingChanged = Object.keys(partial).some(key => H3_PROFILE_PARAM_KEYS.has(key as keyof GenerateParams))
    if (profileSettingChanged) {
      ++_h3ProfileApplySeq
      ++_h3CompatibilitySeq
      ++_modelDefaultsSeq
    }
    set(s => ({
      params: { ...s.params, ...partial },
      ...(profileSettingChanged ? {
        h3SelectedProfile: 'custom' as const,
        h3ProfileApplying: null,
        modelOptionsLoading: s.h3ProfileApplying ? false : s.modelOptionsLoading,
      } : {}),
    }))
  },
  setH3NativeResolution: (resolution) => {
    ++_h3ProfileApplySeq
    ++_h3CompatibilitySeq
    ++_modelDefaultsSeq
    set(state => ({
      params: {
        ...state.params,
        resolution,
        delivery_resolution: undefined,
        delivery_fit: undefined,
      },
      spatialUpsampling: '',
      h3SelectedProfile: 'custom',
      h3ProfileApplying: null,
    }))
  },

  settingsOpen: false,
  toggleSettings: () => get().setSettingsOpen(!get().settingsOpen),
  setSettingsOpen: (open) => {
    const refreshDirector = !open && get().directorModelVisibilityRefreshPending
    set({
      settingsOpen: open,
      ...(refreshDirector ? { directorModelVisibilityRefreshPending: false } : {}),
    })
    if (refreshDirector) {
      const explicitOutput = get().explicitOutput
      void _modelVisibilitySaveTask.then(() => (
        get().loadDirectorCapabilities({ explicitOutput, force: true })
      )).catch(() => { /* the Director readiness card retains its retry action */ })
    }
  },
  sidebarOpen: false,
  toggleSidebar: () => set(s => ({ sidebarOpen: !s.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  openQueueAfterSubmit: (() => {
    try { return localStorage.getItem('maestro:open-queue-after-submit') !== 'false' } catch { return true }
  })(),
  setOpenQueueAfterSubmit: (enabled) => {
    set({ openQueueAfterSubmit: enabled })
    try { localStorage.setItem('maestro:open-queue-after-submit', String(enabled)) } catch { /* preference only */ }
  },

  // Theme — initial value reads from localStorage (with legacy
  // single-theme migration) so it matches what the inline script in
  // index.html applied to <html>. The setters write to the DOM and
  // localStorage via applyThemePrefs, which also installs the OS
  // scheme listener that makes 'auto' live-switch.
  themePrefs: getStoredPrefs(),
  setThemeMode: (mode) => {
    const prefs = { ...get().themePrefs, mode }
    applyThemePrefs(prefs)
    set({ themePrefs: prefs })
  },
  setThemeFamily: (family) => {
    const prefs = { ...get().themePrefs, family }
    applyThemePrefs(prefs)
    set({ themePrefs: prefs })
  },

  // CivitAI LoRA Browser
  // Director Pipeline Dashboard
  retakeDialogOpen: false,
  retakeSourceFile: null,
  openRetakeDialog: (filename) => set({ retakeDialogOpen: true, retakeSourceFile: filename }),
  closeRetakeDialog: () => set({ retakeDialogOpen: false, retakeSourceFile: null }),

  dashboardOpen: false,
  dashboardPipelineList: [],
  dashboardPipelineListRead: { workspace: '', generation: _dashboardPipelineListLoadToken, status: 'idle' },
  dashboardSelectedPipeline: null,
  dashboardLoading: false,
  setDashboardOpen: (open) => {
    set({ dashboardOpen: open })
    if (open) {
      get().loadPipelineList()
      const selected = get().dashboardSelectedPipeline
      if (selected) get().loadSavedPipeline(selected.pipeline_id)
    }
  },
  loadPipelineList: async () => {
    const generation = ++_dashboardPipelineListLoadToken
    const workspace = get().activeWorkspace
    set({
      dashboardPipelineListRead: { workspace, generation, status: 'loading' },
    })
    try {
      const { pipelines } = await api.fetchPipelineList(workspace)
      if (generation !== _dashboardPipelineListLoadToken || get().activeWorkspace !== workspace) return
      set({
        dashboardPipelineList: pipelines,
        dashboardPipelineListRead: { workspace, generation, status: 'ready' },
      })

      // The repair worker belongs to the server, so a browser reload must
      // rediscover active operations and resume UI polling without requiring
      // the Dashboard to be opened first. Keep discovery separate from the
      // selected pipeline so bootstrapping never opens or changes the overlay.
      for (const item of pipelines) {
        if (!item.repair_status || !DIRECTOR_REPAIR_ACTIVE.has(item.repair_status)) continue
        if (_directorRepairPolls.has(item.id) || _directorRepairDiscoveries.has(item.id)) continue

        const discovery = {}
        _directorRepairDiscoveries.set(item.id, discovery)
        void api.fetchSavedPipeline(item.id, workspace).then(pipeline => {
          if (_directorRepairDiscoveries.get(item.id) !== discovery) return
          if (_directorRepairPolls.has(item.id)) return

          const repair = pipeline.repair
          if (_repairNeedsPolling(repair)) {
            get().pollPipelineRepair(item.id, repair!.operation_id)
            return
          }

          // The operation may have finished between the list and detail
          // requests. Reflect that terminal state and refresh newly-created
          // media instead of waiting for another Dashboard visit.
          set(s => ({
            dashboardPipelineList: s.dashboardPipelineList.map(entry =>
              entry.id === item.id
                ? { ...entry, repair_status: repair?.status || null }
                : entry),
          }))
          void get().loadOutputs()
        }).catch(e => {
          console.warn(`Failed to reconnect Director repair for ${item.id}:`, e)
        }).finally(() => {
          if (_directorRepairDiscoveries.get(item.id) === discovery) {
            _directorRepairDiscoveries.delete(item.id)
          }
        })
      }
    } catch (e) {
      if (generation !== _dashboardPipelineListLoadToken || get().activeWorkspace !== workspace) return
      console.error('Failed to load pipeline list:', e)
      set({
        dashboardPipelineListRead: { workspace, generation, status: 'failed' },
      })
    }
  },
  loadSavedPipeline: async (pid) => {
    const loadToken = ++_dashboardPipelineLoadToken
    set({ dashboardLoading: true })
    try {
      const pipeline = await api.fetchSavedPipeline(pid, get().activeWorkspace)
      if (loadToken !== _dashboardPipelineLoadToken) return
      set({ dashboardSelectedPipeline: pipeline, dashboardLoading: false })
      if (_repairNeedsPolling(pipeline.repair)) {
        get().pollPipelineRepair(pid, pipeline.repair!.operation_id)
      }
    } catch (e) {
      if (loadToken !== _dashboardPipelineLoadToken) return
      console.error('Failed to load pipeline:', e)
      set({ dashboardLoading: false })
    }
  },
  deletePipeline: async (pid) => {
    // Clear the selection AND drop the pid from the list in the same
    // update: the dashboard's auto-load effect selects pipelineList[0]
    // whenever selection is null, so a stale list would immediately
    // re-fetch the pipeline being deleted (re-mounting its <img>/<video>
    // elements and re-locking the files on Windows).
    _dashboardPipelineLoadToken += 1
    _dashboardPipelineListLoadToken += 1
    set(s => ({
      dashboardSelectedPipeline: null,
      dashboardPipelineList: s.dashboardPipelineList.filter(p => p.id !== pid),
    }))
    await api.deletePipeline(pid, get().activeWorkspace)
    await get().loadPipelineList()
    // Pipeline media were gallery items too — refresh the feed.
    get().loadOutputs()
    get().loadWorkspaces()
  },
  tagClip: async (pid, clipIndex, tag) => {
    try {
      await api.tagPipelineClip(pid, clipIndex, tag, get().activeWorkspace)
      // Update local state
      set(s => {
        if (!s.dashboardSelectedPipeline || s.dashboardSelectedPipeline.pipeline_id !== pid) return {}
        const clips = [...s.dashboardSelectedPipeline.clips]
        if (clipIndex < clips.length) {
          clips[clipIndex] = { ...clips[clipIndex], tag: tag as 'good' | 'needs_work' | null }
        }
        return { dashboardSelectedPipeline: { ...s.dashboardSelectedPipeline, clips } }
      })
    } catch (e) {
      console.error('Failed to tag clip:', e)
    }
  },
  startPipelineRepair: async (pid: string) => {
    const { repair } = await api.startPipelineRepair(pid, get().activeWorkspace)
    set(s => {
      const dashboardPipelineList = s.dashboardPipelineList.map(item =>
        item.id === pid ? { ...item, repair_status: repair.status } : item)
      if (!s.dashboardSelectedPipeline || s.dashboardSelectedPipeline.pipeline_id !== pid) {
        return { dashboardPipelineList }
      }
      return {
        dashboardPipelineList,
        dashboardSelectedPipeline: {
          ...s.dashboardSelectedPipeline,
          repair,
        },
      }
    })
    get().pollPipelineRepair(pid, repair.operation_id)
    return repair
  },
  cancelPipelineRepair: async (pid: string) => {
    const { repair } = await api.cancelPipelineRepair(pid, get().activeWorkspace)
    set(s => {
      const dashboardPipelineList = s.dashboardPipelineList.map(item =>
        item.id === pid ? { ...item, repair_status: repair.status } : item)
      if (!s.dashboardSelectedPipeline || s.dashboardSelectedPipeline.pipeline_id !== pid) {
        return { dashboardPipelineList }
      }
      return {
        dashboardPipelineList,
        dashboardSelectedPipeline: {
          ...s.dashboardSelectedPipeline,
          repair,
        },
      }
    })
    get().pollPipelineRepair(pid, repair.operation_id)
    return repair
  },
  pollPipelineRepair: (pid: string, operationId: string) => {
    const existing = _directorRepairPolls.get(pid)
    if (existing?.operationId === operationId) return
    if (existing) _stopDirectorRepairPoll(pid)

    const poll: DirectorRepairPoll = { operationId, timer: null }
    _directorRepairPolls.set(pid, poll)

    const tick = async () => {
      if (_directorRepairPolls.get(pid) !== poll) return
      poll.timer = null
      try {
        const pipeline = await api.fetchSavedPipeline(pid, get().activeWorkspace)
        if (_directorRepairPolls.get(pid) !== poll) return

        const repair = pipeline.repair
        set(s => {
          const dashboardPipelineList = s.dashboardPipelineList.map(item =>
            item.id === pid ? { ...item, repair_status: repair?.status || null } : item)
          if (!s.dashboardSelectedPipeline || s.dashboardSelectedPipeline.pipeline_id !== pid) {
            return { dashboardPipelineList }
          }
          return { dashboardPipelineList, dashboardSelectedPipeline: pipeline }
        })

        if (repair?.operation_id !== operationId) {
          _stopDirectorRepairPoll(pid)
          if (_repairNeedsPolling(repair)) {
            get().pollPipelineRepair(pid, repair!.operation_id)
          } else {
            void get().loadPipelineList()
            void get().loadOutputs()
          }
          return
        }
        if (!_repairNeedsPolling(repair)) {
          _stopDirectorRepairPoll(pid)
          void get().loadPipelineList()
          void get().loadOutputs()
          return
        }
      } catch (e) {
        console.warn(`Director repair poll failed for ${pid}; retrying:`, e)
      }

      if (_directorRepairPolls.get(pid) === poll) {
        poll.timer = window.setTimeout(tick, DIRECTOR_REPAIR_POLL_MS)
      }
    }

    void tick()
  },
  rerunClipImage: async (pid: string, clipIndex: number, prompt?: string) => {
    set({ dashboardLoading: true })
    try {
      const result = await api.rerunClipImage(pid, clipIndex, get().activeWorkspace, prompt)
      // Refresh the pipeline to get updated state
      const pipeline = await api.fetchSavedPipeline(pid, get().activeWorkspace)
      set({ dashboardSelectedPipeline: pipeline, dashboardLoading: false })
      // New files (rerun clip / rejoin video) land in the outputs folder —
      // refresh the gallery so they appear without a browser reload.
      get().loadOutputs()
      return result
    } catch (e) {
      console.error('Re-run image failed:', e)
      set({ dashboardLoading: false })
      throw e
    }
  },
  rerunClipVideo: async (pid: string, clipIndex: number, prompt?: string) => {
    set({ dashboardLoading: true })
    try {
      const result = await api.rerunClipVideo(pid, clipIndex, get().activeWorkspace, prompt)
      const pipeline = await api.fetchSavedPipeline(pid, get().activeWorkspace)
      set({ dashboardSelectedPipeline: pipeline, dashboardLoading: false })
      // New files (rerun clip / rejoin video) land in the outputs folder —
      // refresh the gallery so they appear without a browser reload.
      get().loadOutputs()
      return result
    } catch (e) {
      console.error('Re-run video failed:', e)
      set({ dashboardLoading: false })
      throw e
    }
  },
  rejoinPipelineClips: async (pid: string) => {
    set({ dashboardLoading: true })
    try {
      const result = await api.rejoinPipeline(pid, get().activeWorkspace)
      const pipeline = await api.fetchSavedPipeline(pid, get().activeWorkspace)
      set({ dashboardSelectedPipeline: pipeline, dashboardLoading: false })
      // New files (rerun clip / rejoin video) land in the outputs folder —
      // refresh the gallery so they appear without a browser reload.
      get().loadOutputs()
      return result
    } catch (e) {
      console.error('Rejoin failed:', e)
      set({ dashboardLoading: false })
      throw e
    }
  },
  resumePipeline: async (pid: string) => {
    // Resume only the authoritative recovery action for the exact saved
    // project, then reconnect the Director view so progress remains visible.
    const state = get()
    const selected = state.dashboardSelectedPipeline
    const workspace = selected?.pipeline_id === pid
      ? selected.workspace
      : state.dashboardPipelineList.find(item => item.id === pid)?.workspace
    if (!workspace) throw new Error('Director recovery project is unavailable')
    if (workspace !== state.activeWorkspace) {
      throw new Error('Switch to the Director pipeline project before resuming it')
    }
    const lifecycle = _beginDirectorPipelineLifecycle(workspace)
    try {
      if (!lifecycle.ownsWorkspace()) return
      const result = await api.resumePipeline(pid, workspace)
      if (!lifecycle.ownsWorkspace()) return
      if (
        result.status === 'paused'
        && result.next_action === 'continue'
        && result.actions?.includes('continue')
      ) {
        await api.continuePipeline(pid)
        if (!lifecycle.ownsWorkspace()) return
      }
      set({
        dashboardOpen: false,
        pipelineId: pid,
        pipelineStatus: null,
        pipelinePolling: true,
      })
      get().pollPipelineStatus()
    } finally {
      lifecycle.dispose()
    }
  },

  // ── Recipes (one-click Studio presets) ────────────────────────────
  recipesOpen: false,
  setRecipesOpen: (open) => {
    set({ recipesOpen: open })
    if (open) get().loadRecipes()
  },
  recipes: [],
  recipesLoading: false,
  recipesError: null,
  loadRecipes: async () => {
    const seq = ++_recipesLoadSeq
    const workspace = get().activeWorkspace
    set({ recipesLoading: true, recipesError: null })
    try {
      const { recipes } = await api.fetchRecipes(workspace)
      if (seq !== _recipesLoadSeq || get().activeWorkspace !== workspace) return
      set({ recipes, recipesLoading: false, recipesError: null })
    } catch (e) {
      if (seq !== _recipesLoadSeq || get().activeWorkspace !== workspace) return
      console.error('Failed to load recipes:', e)
      set({
        recipes: [],
        recipesLoading: false,
        recipesError: e instanceof Error ? e.message : 'Failed to load recipes',
      })
    }
  },
  applyRecipe: async (id) => {
    // Applies a recipe like Load Settings applies a saved output: switch
    // model + generation mode, land the tuned params in the active Studio
    // working set, and PREPOPULATE the prompt (a real, editable value — not
    // placeholder text) so the user just tweaks the subject. Seed and repeat
    // reset so a recipe reproduces a look, not a specific frame.
    const recipeWorkspace = get().activeWorkspace
    const recipe = await api.fetchRecipe(id, recipeWorkspace)
    if (get().activeWorkspace !== recipeWorkspace) {
      throw new Error('The active project changed before the recipe could be applied. Open Recipes and try again.')
    }
    const { models } = get()
    const model = models.find(m => m.model_type === recipe.model_type)
    const mode = model ? getModelMode(recipe.model_type, model.family) : ((recipe.mode as GenerationMode) || 'video')

    // Inventory must be authoritative before we claim any LoRA is missing.
    // loadLoras intentionally degrades failures to an empty UI list, which is
    // not strong enough for an apply decision and could prompt a duplicate
    // host install after a transient request failure.
    let availableLoras: string[] = []
    if (recipe.model_type) {
      try {
        availableLoras = (await api.fetchLoras(recipe.model_type)).loras
      } catch {
        throw new Error('Could not verify the LoRAs installed on this Maestro host. The recipe was not applied; try again.')
      }
      if (get().activeWorkspace !== recipeWorkspace) {
        throw new Error('The active project changed before the recipe could be applied. Open Recipes and try again.')
      }
    }

    const applySnapshot = get()
    const restoreInterruptedApply = () => {
      // First use the shared transition to leave the recipe mode cleanly,
      // then reinstate the exact pre-apply working/persisted state. Invalidate
      // transition requests so a late response cannot reintroduce partial
      // recipe state after this rollback.
      if (get().generationMode !== applySnapshot.generationMode) {
        get().setGenerationMode(applySnapshot.generationMode)
      }
      ++_modelOptionsSeq
      ++_modelDefaultsSeq
      ++_loraLoadSeq
      ++_h3ProfileApplySeq
      set({
        generationMode: applySnapshot.generationMode,
        selectedModelPerMode: applySnapshot.selectedModelPerMode,
        savedLoraPerMode: applySnapshot.savedLoraPerMode,
        savedParamsPerMode: applySnapshot.savedParamsPerMode,
        savedPromptPerMode: applySnapshot.savedPromptPerMode,
        params: applySnapshot.params,
        loraWeights: applySnapshot.loraWeights,
        availableLoras: applySnapshot.availableLoras,
        lorasLoading: false,
        modelOptions: applySnapshot.modelOptions,
        modelOptionsLoading: false,
        durationSeconds: applySnapshot.durationSeconds,
        slidingWindowSeconds: applySnapshot.slidingWindowSeconds,
        slidingWindowOverlap: applySnapshot.slidingWindowOverlap,
        slidingWindowLocked: applySnapshot.slidingWindowLocked,
        filmGrainIntensity: applySnapshot.filmGrainIntensity,
        filmGrainSaturation: applySnapshot.filmGrainSaturation,
        resolutionPreset: applySnapshot.resolutionPreset,
        aspectRatio: applySnapshot.aspectRatio,
        ttsVoiceCount: applySnapshot.ttsVoiceCount,
        ttsVoices: applySnapshot.ttsVoices,
        h3SelectedProfile: applySnapshot.h3SelectedProfile,
        h3ProfileApplying: applySnapshot.h3ProfileApplying,
      })
      _saveSettings({
        generationMode: applySnapshot.generationMode,
        selectedModelPerMode: applySnapshot.selectedModelPerMode,
        savedParamsPerMode: applySnapshot.savedParamsPerMode,
        savedLoraPerMode: applySnapshot.savedLoraPerMode,
        savedPromptPerMode: applySnapshot.savedPromptPerMode,
      }, applySnapshot.loraIdByFilename)
    }

    const activated = (recipe.loras || []).map(l => l.filename)
    const multipliers = (recipe.loras || []).map(l => String(l.multiplier ?? '1.0')).join(' ')
    const loraWeights: Record<string, number[]> = {}
    for (const l of recipe.loras || []) {
      loraWeights[l.filename] = String(l.multiplier ?? '1.0').split(';').map(Number)
    }

    // Preserve and cleanly leave the current generation mode before layering
    // in the recipe. Directly changing generationMode would leak Audio/Edit
    // fields into Studio and skip the normal per-mode restoration contract.
    const previousMode = get().generationMode
    ++_loraLoadSeq
    if (previousMode !== mode) {
      // Seed the target mode with the recipe model and the already-verified
      // inventory. This makes the shared transition restore the intended
      // model and prevents it from launching an untracked loadLoras request
      // for an older/default target model that could resolve after apply.
      set(s => ({
        selectedModelPerMode: { ...s.selectedModelPerMode, [mode]: recipe.model_type },
        savedLoraPerMode: {
          ...s.savedLoraPerMode,
          [mode]: { activated_loras: activated, loras_multipliers: multipliers, loraWeights, availableLoras },
        },
      }))
      get().setGenerationMode(mode)
    }
    // setGenerationMode may start H3 profile normalization as a detached
    // task. Recipe tuning is authoritative, so supersede that task after the
    // shared transition has issued it (and also cancel any same-mode task).
    ++_h3ProfileApplySeq

    if (recipe.model_type) {
      // Supersede the mode transition's model/default requests, then await the
      // final capability/geometry response before applying the recipe tuning.
      // Applying params last prevents late defaults from silently reverting
      // the recipe's steps, guidance, duration, or other tuned values.
      ++_modelDefaultsSeq
      await get().loadModelOptions(recipe.model_type)
      ++_modelDefaultsSeq
      if (get().activeWorkspace !== recipeWorkspace) {
        restoreInterruptedApply()
        throw new Error('The active project changed before the recipe could be applied. Open Recipes and try again.')
      }
      if (get().modelOptions?.model_type !== recipe.model_type) {
        restoreInterruptedApply()
        throw new Error('Could not load this recipe model\'s Studio options. The recipe was not applied; try again.')
      }
    }

    // No H3 profile response issued during model-options loading may commit
    // after the recipe's final tuned state.
    ++_h3ProfileApplySeq

    // Applying a Studio preset is also an explicit navigation action. Use the
    // shared transition so desktop and mobile cannot diverge on Director state.
    get().setSidebarMode('studio')

    set(s => ({
      generationMode: mode,
      // NOTE: do NOT close the overlay here. The RecipesOverlay closes
      // itself on success, but keeps itself open when the recipe needs
      // LoRAs you don't have — so it can show the download prompt. Closing
      // here made that prompt dead code (recipe applied, LoRA missing, user
      // generated → cryptic "Loras missing" failure with no guidance).
      params: {
        ...s.params,
        ...(recipe.params as Partial<GenerateParams>),
        model_type: recipe.model_type,
        prompt: recipe.prompt_example || '',
        activated_loras: activated,
        loras_multipliers: multipliers,
        seed: -1,
        repeat_generation: 1,
        // Recipes are look presets — land in the base Studio sub-mode
        // (Frames for video, image-output for image), not Extend/Blend.
        image_mode: mode === 'image' ? 1 : 0,
      },
      loraWeights,
      availableLoras,
      lorasLoading: false,
      selectedModelPerMode: { ...s.selectedModelPerMode, [mode]: recipe.model_type },
    }))

    // Recipe selection is a real Studio model selection, not a transient
    // preview. Persist it through the same per-mode settings channel so a
    // reload does not silently return to the pre-recipe model.
    const applied = get()
    _saveSettings({
      generationMode: applied.generationMode,
      selectedModelPerMode: applied.selectedModelPerMode,
      savedParamsPerMode: applied.savedParamsPerMode,
      savedLoraPerMode: applied.savedLoraPerMode,
      savedPromptPerMode: applied.savedPromptPerMode,
    }, applied.loraIdByFilename)

    if (recipe.model_type) {
      // Derive duration from video_length if the recipe carried one.
      const vlen = (recipe.params as Record<string, unknown>)?.video_length
      const fps = model?.fps || 16
      if (typeof vlen === 'number' && vlen > 0) {
        set({ durationSeconds: Math.round((vlen / fps) * 10) / 10 })
      }
    }

    const present = new Set(availableLoras.map(x => (x || '').replace(/\\/g, '/').split('/').pop() || ''))
    const missing = (recipe.loras || []).filter(l => !present.has(l.filename))
    return { missing }
  },
  saveRecipeFromOutput: async (outputName, name, description, nsfw) => {
    await api.saveRecipeFromOutput({ output_name: outputName, workspace: get().activeWorkspace, name, description, nsfw })
    if (get().recipesOpen) get().loadRecipes()
  },
  deleteRecipe: async (id) => {
    await api.deleteRecipe(id)
    set(s => ({ recipes: s.recipes.filter(r => r.id !== id) }))
  },
  downloadRecipeLora: async (lora, modelType) => {
    // Best-effort fetch of a recipe's LoRA from its CivitAI source. Portable
    // recipes carry a direct download_url in source_url; if it isn't a
    // CivitAI URL the backend rejects it and the UI falls back to the link.
    if (!lora.source_url) throw new Error('This recipe has no download source for that LoRA — install it manually.')
    const model = get().models.find(m => m.model_type === modelType)
    await api.startCivitAIDownload({
      download_url: lora.source_url,
      filename: lora.filename,
      // architecture (not family) is what the backend's get_lora_dir keys on,
      // so the LoRA lands in the same per-model dir the model loads from.
      target_arch: (model?.architecture as string) || '',
      model_id: 0, version_id: 0, trained_words: [],
      model_name: lora.filename, images: [],
    })
    get().pollCivitAIDownloads()
  },
  loraBrowserOpen: false,
  loraBrowserArch: null,
  loraBrowserDefaultDir: null,
  setLoraBrowserDefaultDir: (dir) => set({ loraBrowserDefaultDir: dir }),
  setLoraBrowserOpen: (open, arch) => {
    if (open) {
      set({ loraBrowserOpen: true, loraBrowserArch: arch || null, civitSearchResults: [], civitSearchCursor: null, civitSelectedModel: null })
      // Adopt downloads started by URL imports, recipes, or another browser
      // session instead of assuming this store initiated every transfer.
      get().pollCivitAIDownloads()
    } else {
      set({ loraBrowserOpen: false })
      // Refresh LoRA list after closing (may have downloaded new ones)
      const modelType = get().params.model_type
      if (modelType) get().loadLoras(modelType)
    }
  },
  civitSearchResults: [],
  civitSearchCursor: null,
  civitSearchLoading: false,
  civitSearchError: null,
  civitSelectedModel: null,
  civitDownloads: [],

  searchCivitAI: async (params, append = false) => {
    set({ civitSearchLoading: true, civitSearchError: null })
    try {
      const result = await api.searchCivitAI(params as Parameters<typeof api.searchCivitAI>[0])
      if (append) {
        set(s => ({
          civitSearchResults: [...s.civitSearchResults, ...result.items],
          civitSearchCursor: result.metadata?.nextCursor || null,
          civitSearchLoading: false,
        }))
      } else {
        set({
          civitSearchResults: result.items,
          civitSearchCursor: result.metadata?.nextCursor || null,
          civitSearchLoading: false,
          civitSelectedModel: null,
        })
      }
    } catch (e) {
      console.error('CivitAI search failed:', e)
      const msg = e instanceof Error ? e.message : 'CivitAI search failed'
      set({ civitSearchLoading: false, civitSearchError: msg })
    }
  },

  selectCivitAIModel: async (modelId) => {
    try {
      const model = await api.fetchCivitAIModel(modelId)
      set({ civitSelectedModel: model })
    } catch (e) {
      console.error('Failed to fetch model details:', e)
    }
  },

  clearCivitSelection: () => set({ civitSelectedModel: null }),

  startCivitAIDownload: async (params) => {
    try {
      await api.startCivitAIDownload(params as Parameters<typeof api.startCivitAIDownload>[0])
      get().pollCivitAIDownloads()
    } catch (e) {
      console.error('Download failed:', e)
    }
  },

  pollCivitAIDownloads: () => {
    // Mark every invocation, including calls made while the singleton loop is
    // awaiting an older request. The active loop consumes this before exit
    // and takes a new snapshot that was initiated after the caller arrived.
    _civitDownloadPollRequested = true
    if (_civitDownloadPollTask) return

    const controller = new AbortController()
    _civitDownloadPollController = controller
    const poll = async () => {
      let consecutiveErrors = 0
      try {
        while (!controller.signal.aborted) {
          _civitDownloadPollRequested = false
          try {
            const { downloads } = await api.fetchCivitAIDownloads()
            consecutiveErrors = 0
            set({ civitDownloads: downloads })

            // Checkpoint downloads are registered on the server before their
            // terminal record is published. Refresh here, in the singleton
            // poller, so navigating away from ModelDetail cannot skip it and
            // a Content-Disposition filename change cannot break matching.
            const completedCheckpoints = downloads.filter(download =>
              download.status === 'completed'
              && !!download.model_type
              && !_civitRefreshedCheckpointDownloads.has(download.id)
            )
            if (completedCheckpoints.length > 0) {
              try {
                await api.reloadModels()
                await get().loadModels()
                completedCheckpoints.forEach(download => {
                  _civitRefreshedCheckpointDownloads.add(download.id)
                })
              } catch (error) {
                // Keep the IDs unmarked so a later poll retries the refresh.
                console.warn('Checkpoint model refresh failed; will retry:', error)
              }
            }

            // A caller joined while this request was in flight. Its freshness
            // guarantee requires another request, even when this response has
            // no active/recent downloads and would normally end the loop.
            if (_civitDownloadPollRequested) continue

            // Keep taking snapshots while work is active and through the
            // completed row's 30-second display window. This guarantees a
            // caller that joins late still observes the terminal record.
            if (!downloads.some(download => _downloadNeedsPolling(download, Date.now()))) return
            await _waitForDownloadPoll(CIVIT_DOWNLOAD_POLL_MS, controller.signal)
          } catch (error) {
            if (controller.signal.aborted) return
            consecutiveErrors += 1
            if (_civitDownloadPollRequested) continue
            const knownWork = get().civitDownloads.some(download =>
              _downloadNeedsPolling(download, Date.now())
            )
            // Retry transient failures while the browser is open or known
            // work is active. A background adoption probe gets three retries
            // before yielding; a later caller can safely start a fresh loop.
            if (!get().loraBrowserOpen && !knownWork && consecutiveErrors > 3) {
              console.warn('Download polling paused after repeated errors:', error)
              return
            }
            const retryMs = Math.min(10_000, 1000 * (2 ** Math.min(consecutiveErrors - 1, 3)))
            await _waitForDownloadPoll(retryMs, controller.signal)
          }
        }
      } finally {
        if (_civitDownloadPollController === controller) {
          _civitDownloadPollController = null
          _civitDownloadPollTask = null
        }
      }
    }

    _civitDownloadPollTask = poll()
  },

  // Models & families
  families: [],
  models: [],
  modelsLoaded: false,
  enabledModels: _loadEnabledModels() ?? new Set(DEFAULT_ENABLED_MODELS),
  toggleModelEnabled: (modelType) => {
    set(s => {
      const next = new Set(s.enabledModels)
      if (next.has(modelType)) next.delete(modelType)
      else next.add(modelType)
      _saveEnabledModels(next)
      return { enabledModels: next }
    })
  },
  resetEnabledModels: () => {
    const next = new Set(DEFAULT_ENABLED_MODELS)
    _saveEnabledModels(next)
    set({ enabledModels: next })
  },
  setAllModelsEnabled: (enabled) => {
    if (enabled) {
      const all = new Set(get().models.map(m => m.model_type))
      _saveEnabledModels(all)
      set({ enabledModels: all })
    } else {
      const empty = new Set<string>()
      _saveEnabledModels(empty)
      set({ enabledModels: empty })
    }
  },
  setModelsEnabled: (modelTypes, enabled) => {
    set(s => {
      const next = new Set(s.enabledModels)
      for (const mt of modelTypes) {
        if (enabled) next.add(mt)
        else next.delete(mt)
      }
      _saveEnabledModels(next)
      return { enabledModels: next }
    })
  },
  // Open Settings → Performance and ask the Enabled Models section to
  // expand + scroll to the given mode (fired by the ModelSelector hint).
  modelVisibilityFocus: null,
  openModelVisibility: (mode) => set({
    settingsOpen: true,
    settingsTab: 'performance',
    modelVisibilityFocus: mode,
  }),
  openDirectorModelVisibility: () => set({
    settingsOpen: true,
    settingsTab: 'performance',
    modelVisibilityFocus: 'image',
    directorModelVisibilityRefreshPending: true,
  }),
  clearModelVisibilityFocus: () => set({ modelVisibilityFocus: null }),
  loadModels: async () => {
    try {
      const shouldHydrateVisibility = !_modelVisibilityHydrated
      const [data, visibility] = await Promise.all([
        api.fetchModels(),
        shouldHydrateVisibility
          ? api.fetchModelVisibility().catch(error => {
              console.warn('Failed to load model visibility:', error)
              return null
            })
          : Promise.resolve(null),
      ])
      const families = data.families
      const backendModels = data.models.map(m => ({
        model_type: m.model_type,
        name: m.name,
        description: m.description,
        selector_help: m.selector_help,
        lora_compatibility_note: m.lora_compatibility_note,
        family: m.family,
        architecture: m.architecture,
        is_i2v: m.is_i2v,
        is_t2v: m.is_t2v,
        guidance_max_phases: m.guidance_max_phases ?? 1,
        fps: m.fps ?? 16,
        supports_end_frame: m.supports_end_frame ?? false,
        supports_audio: m.supports_audio ?? false,
        supports_audio_input: m.supports_audio_input ?? false,
        generates_audio: m.generates_audio ?? false,
        supports_ref_images: m.supports_ref_images ?? false,
        director: m.director,
        is_downloaded: m.is_downloaded ?? false,
        downloadable: m.downloadable ?? true,
        manual_installation_ready: m.manual_installation_ready ?? false,
        availability_status: m.availability_status,
        manual_checkpoint_verification_required: m.manual_checkpoint_verification_required ?? false,
        manual_checkpoint_verified: m.manual_checkpoint_verified ?? false,
        manual_installation: m.manual_installation,
        supported_operations: m.supported_operations ?? [],
        automatic_routing: m.automatic_routing ?? false,
        verified: m.verified ?? false,
        default_for_operations: m.default_for_operations ?? [],
        revenue_eligible: m.revenue_eligible,
        fine_tuning_eligible: m.fine_tuning_eligible,
        derivative_tooling: m.derivative_tooling,
        nsfw_only: m.nsfw_only ?? false,
        update_status: m.update_status,
        required_host_terms: m.required_host_terms ?? [],
      }))
      // Inject virtual SFX (MMAudio) models alongside backend models
      const models = [...backendModels, ...SFX_VIRTUAL_MODELS]

      // Pinokio can assign a different web-server port on every launch.
      // Browser localStorage is origin-bound, so hydrate durable visibility
      // once from the server config and keep localStorage only as a
      // migration/cache layer.
      if (shouldHydrateVisibility && visibility) {
        let restoredModels: Set<string>
        if (visibility.configured) {
          restoredModels = new Set(visibility.enabled_models)
          _modelVisibilityDefaultsVersion = (
            visibility.defaults_version || 1
          )
        } else {
          const legacyModels = _loadEnabledModels()
          restoredModels = legacyModels ?? new Set(DEFAULT_ENABLED_MODELS)
          const legacyDefaultsVersion = parseInt(
            localStorage.getItem(DEFAULTS_VERSION_KEY) || '',
            10,
          )
          _modelVisibilityDefaultsVersion = legacyModels
            ? (legacyDefaultsVersion || 1)
            : DEFAULTS_VERSION
        }
        _modelVisibilityHydrated = true
        set({ enabledModels: restoredModels })
        if (!visibility.configured) _saveEnabledModels(restoredModels)
      }

      // One-time curated-defaults upgrade for existing installs (see
      // DEFAULTS_VERSION). Fresh installs already start from the full
      // DEFAULT_ENABLED_MODELS list; for them this only stamps the
      // version key.
      let migrateMusicDefault = false
      // A failed durable read is not evidence that visibility is
      // unconfigured. Defer every migration/write and retry on the next load,
      // so a new Pinokio origin cannot resurrect or overwrite server hides.
      if (!shouldHydrateVisibility || visibility !== null) {
        try {
          const storedVer = _modelVisibilityHydrated
            ? _modelVisibilityDefaultsVersion
            : (
              parseInt(
                localStorage.getItem(DEFAULTS_VERSION_KEY) || '1',
                10,
              ) || 1
            )
          if (storedVer < DEFAULTS_VERSION) {
            const additions: string[] = []
            for (let v = storedVer + 1; v <= DEFAULTS_VERSION; v++) {
              additions.push(...(DEFAULTS_ADDED_IN[v] || []))
            }
            const present = additions.filter(id => models.some(m => m.model_type === id))
            if (present.length > 0) {
              set(s => {
                const next = new Set(s.enabledModels)
                present.forEach(id => next.add(id))
                _saveEnabledModels(next)
                return { enabledModels: next }
              })
            }
            migrateMusicDefault = storedVer < 2
            _modelVisibilityDefaultsVersion = DEFAULTS_VERSION
            localStorage.setItem(DEFAULTS_VERSION_KEY, String(DEFAULTS_VERSION))
            _saveEnabledModels(get().enabledModels)
          }
        } catch { /* localStorage blocked — defaults only apply this session */ }
      }

      // Hydrate persisted per-mode settings from localStorage.
      //
      // Deliberately PARTIAL: the last generation mode and per-mode model
      // selections survive a page refresh; video is also seeded below so
      // Director never displays a model that is absent from its state. The working
      // state — prompt text and Advanced settings (seed, steps, LoRA
      // selection, …) — starts fresh from the model's defaults on every
      // load. The per-mode snapshots (savedParamsPerMode /
      // savedLoraPerMode / savedPromptPerMode) still carry edits across
      // MODE SWITCHES within a session, in-memory only. v1.2.0 restored
      // them here on refresh; stale text/seeds/LoRAs re-appearing after
      // a reload felt wrong, so a refresh is a clean slate again.
      const saved = _loadSettings()
      // v2 migration: users whose saved audio model IS the old music
      // default follow it to the new default (see NEW_MUSIC_DEFAULT).
      // (The old-model-params concern the migration used to handle is
      // gone: saved params no longer rehydrate, and the defaults
      // hydration below runs on every boot.)
      if (migrateMusicDefault && saved?.selectedModelPerMode?.audio === OLD_MUSIC_DEFAULT
          && models.some(m => m.model_type === NEW_MUSIC_DEFAULT)) {
        saved.selectedModelPerMode = { ...saved.selectedModelPerMode, audio: NEW_MUSIC_DEFAULT }
      }
      let mode = get().generationMode
      let initialModelType: string
      const savedVideoModel = (saved?.selectedModelPerMode?.video || '').trim()
      const initialVideoModelType = savedVideoModel && models.some(model => (
        model.model_type === savedVideoModel
        && getModelMode(model.model_type, model.family) === 'video'
      ))
        ? savedVideoModel
        : getDefaultModelForMode('video', families, models)

      if (saved) {
        // Restore saved generation mode
        mode = saved.generationMode || mode
        // Validate saved model for this mode still exists
        const savedModel = saved.selectedModelPerMode?.[mode]
        initialModelType = savedModel && models.some(model => (
          model.model_type === savedModel
          && getModelMode(model.model_type, model.family) === mode
        ))
          ? savedModel
          : getDefaultModelForMode(mode, families, models)
        const bootedIntoRecast = mode === 'avatar'
          && (initialModelType === 'scail2_14B_recast_fast' || initialModelType === 'scail2_14B')
        const bootedIntoRepaint = mode === 'avatar'
          && initialModelType === 'scail2_14B_fast'

        set(s => ({
          families,
          models,
          modelsLoaded: true,
          generationMode: mode,
          ...(bootedIntoRecast
            ? { editSubMode: 'recast' as const }
            : bootedIntoRepaint
              ? { editSubMode: 'restyle' as const }
              : {}),
          // Seed the VALIDATED boot model into the map (the saved entry
          // may point at a removed model) — _applyModelDefaults' race
          // guard compares against selectedModelPerMode[mode].
          selectedModelPerMode: {
            ...(saved.selectedModelPerMode || {}),
            video: initialVideoModelType,
            [mode]: initialModelType,
          },
          // Mode-shaping mirrored from setGenerationMode: booting into
          // image mode needs image_mode 1 + Auto resolution. These used
          // to arrive via the restored params snapshot.
          ...(mode === 'image' ? { resolutionPreset: 'auto' as ResolutionPreset, aspectRatio: 'auto' as AspectRatio } : {}),
          params: {
            ...s.params,
            model_type: initialModelType || s.params.model_type,
            ...(mode === 'image' ? { image_mode: 1 } : {}),
          },
        }))
      } else {
        initialModelType = getDefaultModelForMode(mode, families, models)
        set(s => ({
          families,
          models,
          modelsLoaded: true,
          selectedModelPerMode: { video: initialVideoModelType, [mode]: initialModelType },
          ...(mode === 'image' ? { resolutionPreset: 'auto' as ResolutionPreset, aspectRatio: 'auto' as AspectRatio } : {}),
          params: {
            ...s.params,
            model_type: initialModelType || s.params.model_type,
            ...(mode === 'image' ? { image_mode: 1 } : {}),
          },
        }))
      }

      // Load LoRAs, model options, and tuned defaults for the initial
      // model. The defaults hydration (steps, guidance, LM sampling…)
      // must run on every boot now that saved params don't rehydrate —
      // without it the sliders would show INITIAL_PARAMS' generic values
      // instead of the model's.
      const mt = initialModelType || get().params.model_type
      if (mt && !sfxModelTypes.has(mt)) {
        get().loadLoras(mt)
        _applyModelDefaults(get, set, mt)
        get().loadModelOptions(mt)
      }
      // Refresh the lora_id ↔ filename map from /installed and reconcile
      // any filename renames since save (LoRA version updates land here
      // transparently — saved weights/activations carry over to the new
      // filename without user intervention).
      get().refreshLoraIdMap()

    } catch (e) {
      console.error('Failed to load models:', e)
    }
  },

  resolutionPreset: '720p',
  setResolutionPreset: (preset) => {
    ++_h3ProfileApplySeq
    ++_modelDefaultsSeq
    const ratio = get().aspectRatio
    const resolution = resolveResolution(get().modelOptions, preset, ratio)
    set(s => ({
      resolutionPreset: preset,
      params: { ...s.params, resolution },
      h3SelectedProfile: 'custom',
      h3ProfileApplying: null,
    }))
  },

  aspectRatio: '16:9',
  setAspectRatio: (ratio) => {
    ++_h3ProfileApplySeq
    ++_modelDefaultsSeq
    const preset = get().resolutionPreset
    const resolution = resolveResolution(get().modelOptions, preset, ratio)
    set(s => ({
      aspectRatio: ratio,
      params: { ...s.params, resolution },
      h3SelectedProfile: 'custom',
      h3ProfileApplying: null,
    }))
  },

  durationSeconds: 5,
  setDurationSeconds: (s) => {
    const options = get().modelOptions
    const fps = options?.fps ?? 16
    const frames = options
      ? alignStudioTotalFrames(Math.round(s * fps), options)
      : Math.round(s * fps)
    const effectiveSeconds = Math.round((frames / fps) * 1000) / 1000
    set(state => ({
      durationSeconds: effectiveSeconds,
      params: { ...state.params, video_length: frames },
    }))
    get().syncClipCount()
  },

  guideVideoFps: null,
  setGuideVideoFps: (fps) => set({ guideVideoFps: fps }),
  guideVideoFrameCount: null,
  setGuideVideoFrameCount: (frames) => set({ guideVideoFrameCount: frames }),

  slidingWindowSeconds: 5,
  setSlidingWindowSeconds: (s) => {
    const options = get().modelOptions
    const fps = options?.fps ?? 16
    const segmented = usesStudioSegments(options)
    if (options && !options.sliding_window && !segmented) return
    const defaults = options?.sliding_window_defaults || {}
    const latent = Math.max(1, Math.trunc(options?.latent_size || options?.frames_steps || 4))
    const requested = Math.max(1, Math.round(s * fps))
    const minimum = Math.max(1, Math.trunc(defaults.window_min || (segmented ? options?.frames_minimum : 1) || 1))
    const maximum = Math.max(minimum, Math.trunc(defaults.window_max || (segmented ? options?.frames_maximum : requested) || requested))
    const clamped = Math.min(maximum, Math.max(minimum, requested))
    const frames = options && segmented
      ? alignTotalFrames(clamped, options)
      : Math.floor((clamped - 1) / latent) * latent + 1
    const effectiveSeconds = Math.round((frames / fps) * 1000) / 1000
    const discard = Math.max(0, Math.trunc(defaults.discard_last_frames || 0))
    const overlapMaximum = Math.max(0, Math.min(
      Math.trunc(defaults.overlap_max ?? frames),
      frames - discard - latent,
    ))
    set(state => ({
      slidingWindowSeconds: effectiveSeconds,
      slidingWindowOverlap: Math.min(state.slidingWindowOverlap, overlapMaximum),
      params: {
        ...state.params,
        sliding_window_size: frames,
        sliding_window_overlap: Math.min(state.slidingWindowOverlap, overlapMaximum),
      },
    }))
    get().syncClipCount()
  },

  slidingWindowOverlap: 5,
  setSlidingWindowOverlap: (frames) => {
    const state = get()
    const options = state.modelOptions
    const defaults = options?.sliding_window_defaults || {}
    const latent = Math.max(1, Math.trunc(options?.latent_size || options?.frames_steps || 4))
    const fps = options?.fps || 16
    const windowFrames = options
      ? effectiveSlidingWindowGeometry(
          state.durationSeconds,
          state.slidingWindowSeconds,
          state.slidingWindowOverlap,
          options,
        ).windowFrames
      : Math.max(1, Math.round(state.slidingWindowSeconds * fps))
    const discard = Math.max(0, Math.trunc(defaults.discard_last_frames || 0))
    const modelMinimum = Math.max(0, Math.trunc(defaults.overlap_min ?? 0))
    const modelMaximum = Math.max(modelMinimum, Math.trunc(defaults.overlap_max ?? frames))
    const safeMaximum = Math.max(0, windowFrames - discard - latent)
    const bounded = Math.max(Math.min(modelMinimum, safeMaximum), Math.min(Math.trunc(frames), modelMaximum, safeMaximum))
    const clamped = bounded > 0 ? Math.floor((bounded - 1) / latent) * latent + 1 : 0
    set(state => ({
      slidingWindowOverlap: clamped,
      params: { ...state.params, sliding_window_overlap: clamped },
    }))
  },
  slidingWindowLocked: false,
  setSlidingWindowLocked: (locked) => set({ slidingWindowLocked: locked }),

  outputCount: 1,
  setOutputCount: (n) => set(s => ({
    outputCount: n,
    params: { ...s.params, repeat_generation: n },
  })),

  startImage: null,
  endImage: null,
  setStartImage: (f) => {
    _settingsRestoreGeneration++
    set(s => ({
      startImage: f,
      params: f === null ? { ...s.params, image_start: undefined } : s.params,
    }))
  },
  setEndImage: (f) => {
    _settingsRestoreGeneration++
    set(s => ({
      endImage: f,
      params: f === null ? { ...s.params, image_end: undefined } : s.params,
    }))
  },

  // Image references
  imageRefs: [],
  imageRefType: '',
  removeBackgroundRefs: false,
  addImageRef: (file) => {
    _settingsRestoreGeneration++
    set(s => ({ imageRefs: [...s.imageRefs, file] }))
  },
  removeImageRef: (index) => {
    _settingsRestoreGeneration++
    set(s => {
      const updated = s.imageRefs.filter((_, i) => i !== index)
      const hasRestoredPaths = (s.params.image_refs?.length ?? 0) > 0
      return {
        imageRefs: updated,
        params: updated.length === 0 && !hasRestoredPaths
          ? { ...s.params, image_refs: undefined }
          : s.params,
      }
    })
  },
  reorderImageRefs: (from, to) => {
    _settingsRestoreGeneration++
    set(s => {
      const refs = [...s.imageRefs]
      const [moved] = refs.splice(from, 1)
      refs.splice(to, 0, moved)
      return { imageRefs: refs }
    })
  },
  setImageRefType: (type) => set({ imageRefType: type }),
  setRemoveBackgroundRefs: (v) => set({ removeBackgroundRefs: v }),

  // Voice clone postprocessing state — defaults are off / empty so
  // existing generations are unaffected.
  voiceCloneEnabled: false,
  setVoiceCloneEnabled: (v) => set({ voiceCloneEnabled: v }),
  voiceCloneMode: 'single',
  setVoiceCloneMode: (v) => set({ voiceCloneMode: v }),
  voiceCloneRefs: [],
  setVoiceCloneRef: (index, ref) => set(s => {
    const next = [...s.voiceCloneRefs]
    if (ref === null) {
      next.splice(index, 1)
    } else {
      while (next.length <= index) next.push({ filename: '', path: '' })
      next[index] = ref
    }
    return { voiceCloneRefs: next }
  }),

  // ── Tools area (standalone post-processing on an existing clip) ──────
  toolsTool: 'upscale',
  setToolsTool: (t) => set({ toolsTool: t }),
  toolsSourcePath: null,
  toolsSourceName: null,
  toolsSourceUrl: null,
  setToolsSource: (src) => set(src
    ? { toolsSourcePath: src.path, toolsSourceName: src.name, toolsSourceUrl: src.url }
    : { toolsSourcePath: null, toolsSourceName: null, toolsSourceUrl: null }),
  uploadToolsSource: async (file) => {
    const accountIdentityEpoch = _accountIdentityEpoch
    let uploaded: Awaited<ReturnType<typeof api.uploadImage>>
    try {
      uploaded = await api.uploadImage(file)
    } catch (error) {
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return false
      throw error
    }
    if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return false
    set({ toolsSourcePath: uploaded.path, toolsSourceName: file.name, toolsSourceUrl: uploaded.url })
    return true
  },
  toolsUpscaleMethod: 'flashvsr2',
  setToolsUpscaleMethod: (m) => set({ toolsUpscaleMethod: m }),
  toolsRevoiceMode: 'single',
  setToolsRevoiceMode: (m) => set({ toolsRevoiceMode: m }),
  toolsRevoiceRefs: [null, null],
  setToolsRevoiceRef: (index, ref) => set(s => {
    const next = [...s.toolsRevoiceRefs]
    while (next.length <= index) next.push(null)
    next[index] = ref
    return { toolsRevoiceRefs: next }
  }),
  uploadToolsRevoiceRef: async (index, file) => {
    const accountIdentityEpoch = _accountIdentityEpoch
    let uploaded: Awaited<ReturnType<typeof api.uploadAudio>>
    try {
      uploaded = await api.uploadAudio(file)
    } catch (error) {
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return false
      throw error
    }
    if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return false
    set(state => {
      const toolsRevoiceRefs = [...state.toolsRevoiceRefs]
      while (toolsRevoiceRefs.length <= index) toolsRevoiceRefs.push(null)
      toolsRevoiceRefs[index] = { filename: file.name, path: uploaded.path }
      return { toolsRevoiceRefs }
    })
    return true
  },
  runTool: async () => {
    const accountIdentityEpoch = _accountIdentityEpoch
    const s = get()
    const source = s.toolsSourcePath
    if (!source) return
    const tool = s.toolsTool

    // Revoice needs at least one resolved voice reference.
    const refPaths = s.toolsRevoiceRefs
      .filter((r): r is { filename: string; path: string } => !!r && !!r.path)
      .map(r => r.path)
    if (tool === 'revoice' && refPaths.length === 0) return

    // Placeholder job tile — mirrors the blend/edit submit pattern so the
    // progress shows in the main feed and the gallery refreshes on completion.
    const newJob: GenerationJob = {
      id: '', status: 'queued', progress: 0, step: 0, totalSteps: 0,
      phase: '', message: tool === 'upscale' ? 'Submitting upscale...' : 'Submitting revoice...',
      outputFiles: [], error: null, oomInfo: null,
    }
    set(st => ({ isGenerating: true, jobs: [newJob, ...st.jobs] }))

    try {
      const result = tool === 'upscale'
        ? await api.submitToolUpscale({ video_path: source, method: s.toolsUpscaleMethod, workspace: s.activeWorkspace })
        : await api.submitToolRevoice({ video_path: source, voice_ref_paths: refPaths, mode: s.toolsRevoiceMode, workspace: s.activeWorkspace })
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) {
        _discardStaleGenerationPlaceholder(newJob)
        return
      }

      set(st => ({
        jobs: st.jobs.map(j => j === newJob ? { ...j, id: result.job_id, status: 'queued', message: 'Queued...' } : j),
      }))
      get()._pollRecoveredJob(result.job_id)
      window.dispatchEvent(new CustomEvent('maestro:queue-refresh'))
    } catch (e) {
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) {
        _discardStaleGenerationPlaceholder(newJob)
        return
      }
      const msg = e instanceof Error ? e.message : (tool === 'upscale' ? 'Upscale failed' : 'Revoice failed')
      set(st => ({
        jobs: st.jobs.map(j => j === newJob ? { ...j, id: j.id || `tool-fail-${Date.now()}`, status: 'failed', message: msg, error: msg } : j),
        isGenerating: st.jobs.some(j => j !== newJob && _isActiveGenerationJob(j)),
      }))
      console.error(`Tool ${tool} failed:`, msg)
    }
  },
  quickUpscaleClip: async (name, url) => {
    // Point the Tools state at this clip and run an upscale immediately,
    // reusing runTool()'s submit+poll. The Tools panel reflects this clip
    // afterward (harmless — and convenient if the user opens it).
    set({ toolsTool: 'upscale', toolsSourcePath: name, toolsSourceName: name, toolsSourceUrl: url })
    await get().runTool()
  },
  sendClipToTools: (name, url, tool) => {
    set({ toolsTool: tool, toolsSourcePath: name, toolsSourceName: name, toolsSourceUrl: url })
    get().setGenerationMode('tools')
  },

  // Post-processing defaults (shared for Studio)
  spatialUpsampling: '',
  setSpatialUpsampling: (v) => {
    ++_h3ProfileApplySeq
    ++_modelDefaultsSeq
    set(state => ({
      spatialUpsampling: v,
      params: {
        ...state.params,
        delivery_resolution: undefined,
        delivery_fit: undefined,
      },
      h3SelectedProfile: 'custom',
      h3ProfileApplying: null,
    }))
  },
  filmGrainIntensity: 0,
  setFilmGrainIntensity: (v) => {
    set({ filmGrainIntensity: v })
    // Persist per mode
    const s = get()
    const mode = s.generationMode
    const updatedSavedParams = {
      ...s.savedParamsPerMode,
      [mode]: {
        num_inference_steps: s.params.num_inference_steps,
        guidance_scale: s.params.guidance_scale,
        resolution: s.params.resolution,
        seed: s.params.seed,
        filmGrainIntensity: v,
        filmGrainSaturation: s.filmGrainSaturation,
      },
    }
    set({ savedParamsPerMode: updatedSavedParams })
  },
  filmGrainSaturation: 0.5,
  setFilmGrainSaturation: (v) => {
    set({ filmGrainSaturation: v })
    const s = get()
    const mode = s.generationMode
    const updatedSavedParams = {
      ...s.savedParamsPerMode,
      [mode]: {
        num_inference_steps: s.params.num_inference_steps,
        guidance_scale: s.params.guidance_scale,
        resolution: s.params.resolution,
        seed: s.params.seed,
        filmGrainIntensity: s.filmGrainIntensity,
        filmGrainSaturation: v,
      },
    }
    set({ savedParamsPerMode: updatedSavedParams })
  },

  // Director-mode post-processing (separate image/video)
  directorVideoSpatialUpsampling: '',
  setDirectorVideoSpatialUpsampling: (v) => set({ directorVideoSpatialUpsampling: v }),
  directorVideoFilmGrainIntensity: 0,
  setDirectorVideoFilmGrainIntensity: (v) => set({ directorVideoFilmGrainIntensity: v }),
  directorVideoFilmGrainSaturation: 0.5,
  setDirectorVideoFilmGrainSaturation: (v) => set({ directorVideoFilmGrainSaturation: v }),
  directorVideoSelfRefiner: 0,
  setDirectorVideoSelfRefiner: (v) => set({ directorVideoSelfRefiner: v }),
  directorAudioScale: 1.0,
  setDirectorAudioScale: (v) => set({ directorAudioScale: v }),

  audioGuideFilename: null,
  setAudioGuideFilename: (name) => set({ audioGuideFilename: name }),
  audioGuide2Filename: null,
  setAudioGuide2Filename: (name) => set({ audioGuide2Filename: name }),
  ttsSpeakerName1: '',
  ttsSpeakerName2: '',
  ttsSpeakerNamesManual: false,
  setTtsSpeakerName1: (name) => {
    set(s => {
      const voices = [...s.ttsVoices]
      if (voices.length > 0) voices[0] = { ...voices[0], name }
      return { ttsSpeakerName1: name, ttsSpeakerNamesManual: true, ttsVoices: voices }
    })
  },
  setTtsSpeakerName2: (name) => {
    set(s => {
      const voices = [...s.ttsVoices]
      if (voices.length > 1) voices[1] = { ...voices[1], name }
      return { ttsSpeakerName2: name, ttsSpeakerNamesManual: true, ttsVoices: voices }
    })
  },
  _autoParseSpkeakerNames: (text: string, force?: boolean) => {
    // The manual flag prevents auto-parse from clobbering names the user
    // explicitly typed. `force=true` overrides it — used by the enhance
    // button since enhance generates a fresh script whose new names should
    // replace whatever the user had previously set.
    if (!force && get().ttsSpeakerNamesManual) return
    // Match anything before ":" at the start of a line (e.g. "Dr. Mary Jane O'Brien:")
    const matches = text.match(/^(.+?)\s*:/gm)
    if (!matches) return
    const names = [...new Set(matches.map(m => m.replace(/\s*:$/, '').trim()))]
    const voiceCount = get().ttsVoiceCount
    const voices = [...get().ttsVoices]
    // Ensure voices array is big enough
    while (voices.length < voiceCount) {
      voices.push({ name: '', filename: null, path: null })
    }
    for (let i = 0; i < Math.min(names.length, voiceCount); i++) {
      voices[i] = { ...voices[i], name: names[i] }
    }
    set({
      ttsVoices: voices,
      ttsSpeakerName1: names[0] || '',
      ttsSpeakerName2: names[1] || '',
      // Force-call (from enhance) resets the manual flag so subsequent
      // prompt edits can also auto-parse again. Non-force calls preserve
      // the flag (user manually edited a name; keep their state).
      ...(force ? { ttsSpeakerNamesManual: false } : {}),
    })
  },
  // Dynamic multi-speaker (1-6 voices)
  ttsVoiceCount: 0,
  ttsVoices: [],
  setTtsVoiceCount: (count) => {
    const prevCount = get().ttsVoiceCount
    const current = get().ttsVoices
    const voices = [...current]
    while (voices.length < count) {
      voices.push({ name: '', filename: null, path: null })
    }
    // Derive audio_prompt_type from voice count using the model's own selection
    // list. KugelAudio's selection = ["", "A", "AB"] → 0→"", 1→"A", 2+→"AB".
    // Scenema's selection = ["", "A2", "AB2"] → 0→"", 1→"A2", 2+→"AB2".
    // Other (non-Scenema/Kugel) audio-only models keep the legacy ""/A/AB
    // mapping for backward compat.
    const selection = (get().modelOptions?.audio_prompt_type_sources?.selection as string[] | undefined) || ['', 'A', 'AB']
    const audioType = selection[Math.min(count, selection.length - 1)]
    set(s => ({
      ttsVoiceCount: count,
      ttsVoices: voices.slice(0, Math.max(count, voices.length)),
      params: { ...s.params, audio_prompt_type: audioType + ((s.params.audio_prompt_type as string || '').replace(/[^NV]/g, '')) },
    }))
    // If user added voices to an existing prompt (e.g. typed/pasted a
    // dialogue script first, THEN added voice slots), parse the names
    // from the prompt and populate the voice fields. setParam's auto-parse
    // only fires when the prompt CHANGES — without this, growing the slot
    // count after the prompt is set leaves names un-populated. Use
    // force=true so the manual flag (which may have been set by an earlier
    // name edit or by settings restore) doesn't suppress the parse —
    // adding voices is an explicit mode-change action that should re-derive
    // names from the current prompt.
    if (count > prevCount) {
      const prompt = get().params.prompt
      if (typeof prompt === 'string' && prompt.trim()) {
        get()._autoParseSpkeakerNames(prompt, true)
      }
    }
  },
  setTtsVoiceName: (index, name) => {
    set(s => {
      const voices = [...s.ttsVoices]
      if (index < voices.length) voices[index] = { ...voices[index], name }
      return {
        ttsVoices: voices,
        ttsSpeakerNamesManual: true,
        // Keep legacy fields in sync
        ...(index === 0 ? { ttsSpeakerName1: name } : {}),
        ...(index === 1 ? { ttsSpeakerName2: name } : {}),
      }
    })
  },
  setTtsVoiceFile: (index, filename, path) => {
    set(s => {
      const voices = [...s.ttsVoices]
      if (index < voices.length) voices[index] = { ...voices[index], filename, path }
      return {
        ttsVoices: voices,
        // Keep legacy fields in sync
        ...(index === 0 ? { audioGuideFilename: filename } : {}),
        ...(index === 1 ? { audioGuide2Filename: filename } : {}),
      }
    })
  },
  addTtsVoice: () => {
    const count = get().ttsVoiceCount
    // Respect the model's declared max (e.g. Scenema = 2, Kugel = 6).
    // Defaults to 6 if the model_def doesn't specify max_voice_count.
    const maxVoiceCount = ((get().modelOptions as { max_voice_count?: number } | null)?.max_voice_count) ?? 6
    if (count >= maxVoiceCount) return
    get().setTtsVoiceCount(count + 1)
  },
  removeTtsVoice: (index) => {
    set(s => {
      const voices = s.ttsVoices.filter((_, i) => i !== index)
      const newCount = Math.max(0, s.ttsVoiceCount - 1)
      // Same model-aware mapping as setTtsVoiceCount above.
      const selection = (s.modelOptions?.audio_prompt_type_sources?.selection as string[] | undefined) || ['', 'A', 'AB']
      const audioType = selection[Math.min(newCount, selection.length - 1)]
      return {
        ttsVoices: voices,
        ttsVoiceCount: newCount,
        ttsSpeakerName1: voices[0]?.name || '',
        ttsSpeakerName2: voices[1]?.name || '',
        audioGuideFilename: voices[0]?.filename || null,
        audioGuide2Filename: voices[1]?.filename || null,
        params: { ...s.params, audio_prompt_type: audioType + ((s.params.audio_prompt_type as string || '').replace(/[^NV]/g, '')) },
      }
    })
  },

  // Multi-clip state
  clips: [],
  singlePromptMode: false,
  setClipPrompt: (index, prompt) => {
    const clips = [...get().clips]
    if (clips[index]) {
      clips[index] = { ...clips[index], prompt }
      set({ clips })
    }
  },
  setClipStartImage: (index, file) => {
    const clips = [...get().clips]
    if (clips[index]) {
      clips[index] = { ...clips[index], startImage: file }
      set({ clips })
    }
  },
  setSinglePromptMode: (v) => set({ singlePromptMode: v }),
  syncClipCount: () => {
    const { params, durationSeconds, slidingWindowSeconds, slidingWindowOverlap, modelOptions } = get()
    if (params.image_mode !== 2) return
    const fps = modelOptions?.fps ?? 16
    const overlapSeconds = slidingWindowOverlap / fps
    const effectiveWindow = slidingWindowSeconds - overlapSeconds
    const count = effectiveWindow > 0
      ? Math.max(1, Math.ceil((durationSeconds - overlapSeconds) / effectiveWindow))
      : Math.max(1, Math.ceil(durationSeconds / slidingWindowSeconds))
    const current = get().clips
    if (count === current.length) return
    if (count > current.length) {
      const newClips = [...current]
      for (let i = current.length; i < count; i++) {
        newClips.push({ prompt: '', startImage: null, startImagePath: null, endImage: null, endImagePath: null })
      }
      set({ clips: newClips })
    } else {
      set({ clips: current.slice(0, count) })
    }
  },

  jobs: [],
  isGenerating: false,
  sampleCampaignPairs: [],
  refreshSampleCampaignQueue: async (signal) => {
    const requestSequence = ++_sampleCampaignQueueRequestSequence
    const accountIdentityEpoch = _accountIdentityEpoch
    try {
      const projection = await api.fetchSampleCampaignQueue(signal)
      if (
        signal?.aborted
        || requestSequence !== _sampleCampaignQueueRequestSequence
        || !_accountIdentityIsCurrent(accountIdentityEpoch)
      ) return
      const sampleCampaignPairs = projection?.pairs ?? []
      const currentJobIds = _sampleCampaignJobIds([
        ...get().sampleCampaignPairs,
        ...sampleCampaignPairs,
      ])
      for (const jobId of currentJobIds) _sampleCampaignKnownJobIds.add(jobId)
      for (const jobId of _sampleCampaignKnownJobIds) _recoveryJobPolls.get(jobId)?.stop()
      set(state => {
        const jobs = state.jobs.filter(job => !_sampleCampaignKnownJobIds.has(job.id))
        return {
          sampleCampaignPairs,
          jobs,
          isGenerating: jobs.some(_isActiveGenerationJob),
        }
      })
    } catch {
      if (
        signal?.aborted
        || requestSequence !== _sampleCampaignQueueRequestSequence
        || !_accountIdentityIsCurrent(accountIdentityEpoch)
      ) return
      for (const jobId of _sampleCampaignJobIds(get().sampleCampaignPairs)) {
        _sampleCampaignKnownJobIds.add(jobId)
      }
      for (const jobId of _sampleCampaignKnownJobIds) _recoveryJobPolls.get(jobId)?.stop()
      set(state => {
        const jobs = state.jobs.filter(job => !_sampleCampaignKnownJobIds.has(job.id))
        return {
          sampleCampaignPairs: [],
          jobs,
          isGenerating: jobs.some(_isActiveGenerationJob),
        }
      })
    }
  },
  clearSampleCampaignQueue: () => {
    _sampleCampaignQueueRequestSequence += 1
    for (const jobId of _sampleCampaignJobIds(get().sampleCampaignPairs)) {
      _sampleCampaignKnownJobIds.add(jobId)
    }
    for (const jobId of _sampleCampaignKnownJobIds) _recoveryJobPolls.get(jobId)?.stop()
    set(state => {
      const jobs = state.jobs.filter(job => !_sampleCampaignKnownJobIds.has(job.id))
      return {
        sampleCampaignPairs: [],
        jobs,
        isGenerating: jobs.some(_isActiveGenerationJob),
      }
    })
  },
  pendingH3Plan: null,
  pendingH3PlanEstimate: null,
  pendingH3PlanJobId: null,
  pendingH3PlanWorkspace: null,
  h3PlanReviewLoading: false,
  h3PlanReviewError: null,
  openH3PlanReview: async (jobId) => {
    const initial = get().jobs.find(job => job.id === jobId)
    if (!initial || initial.status !== 'waiting_for_plan_approval' || !initial.workspace) return
    const workspace = initial.workspace
    if (get().activeWorkspace !== workspace) {
      window.alert(`Switch to project ${workspace} to review this plan.`)
      return
    }
    const sequence = ++_h3PlanReviewSequence
    set({
      pendingH3Plan: null,
      pendingH3PlanEstimate: null,
      pendingH3PlanJobId: jobId,
      pendingH3PlanWorkspace: workspace,
      h3PlanReviewLoading: true,
      h3PlanReviewError: null,
    })
    try {
      const status = initial.h3SegmentPlan ? null : await api.fetchJobStatus(jobId)
      const current = get().jobs.find(job => job.id === jobId)
      if (
        sequence !== _h3PlanReviewSequence
      ) return
      if (
        get().pendingH3PlanJobId !== jobId
        || get().pendingH3PlanWorkspace !== workspace
      ) return
      if (
        get().activeWorkspace !== workspace
        || !current
        || current.workspace !== workspace
        || current.status !== 'waiting_for_plan_approval'
        || (status != null && (
          status.job_id !== jobId
          || status.workspace !== workspace
          || (
            status.created_at != null
            && current.createdAt != null
            && current.createdAt !== initial.createdAt
            && status.created_at !== current.createdAt
          )
        ))
      ) {
        get().closeH3PlanReview()
        return
      }
      const plan = status?.h3_segment_plan || current.h3SegmentPlan || null
      if (!plan || (status && status.status !== 'waiting_for_plan_approval')) {
        throw new Error('The queued plan is not ready for review.')
      }
      set(s => ({
        pendingH3Plan: plan,
        pendingH3PlanEstimate: status?.h3_estimate || current.h3Estimate || null,
        pendingH3PlanJobId: jobId,
        pendingH3PlanWorkspace: workspace,
        h3PlanReviewLoading: false,
        jobs: status
          ? s.jobs.map(job => job.id === jobId ? _mergeJobStatus(job, status) : job)
          : s.jobs,
      }))
    } catch (error) {
      if (
        sequence !== _h3PlanReviewSequence
        || get().activeWorkspace !== workspace
        || get().pendingH3PlanJobId !== jobId
        || get().pendingH3PlanWorkspace !== workspace
      ) return
      set({
        h3PlanReviewLoading: false,
        h3PlanReviewError: error instanceof Error ? error.message : 'The queued plan could not be loaded.',
      })
    }
  },
  closeH3PlanReview: () => {
    _h3PlanReviewSequence += 1
    set({
      pendingH3Plan: null,
      pendingH3PlanEstimate: null,
      pendingH3PlanJobId: null,
      pendingH3PlanWorkspace: null,
      h3PlanReviewLoading: false,
      h3PlanReviewError: null,
    })
  },
  approveH3Plan: async (decision) => {
    const sequence = ++_h3PlanReviewSequence
    const {
      pendingH3PlanJobId: jobId,
      pendingH3PlanWorkspace: workspace,
      pendingH3Plan: plan,
    } = get()
    if (!jobId || !workspace || get().activeWorkspace !== workspace) return
    if (plan?.duration_plan && decision.planRevision !== plan.duration_plan.revision) {
      set({ h3PlanReviewError: 'The duration plan changed. Reopen the review and try again.' })
      return
    }
    set({ h3PlanReviewLoading: true, h3PlanReviewError: null })
    try {
      const result = await api.approveGenerationPlan(jobId, {
        workspace,
        segment_overrides: decision.segmentOverrides,
        boundary_overrides: decision.boundaryOverrides,
        h3_ref2va_terms_accepted: h3Ref2VATermsAccepted(),
        ...(decision.planRevision ? {
          plan_revision: decision.planRevision,
          duration_snap_mode: decision.durationSnapMode ?? 'manual',
          segment_duration_edits: (decision.segmentDurationEdits ?? []).map(edit => ({
            segment_index: edit.segmentIndex,
            published_frames: edit.publishedFrames,
          })),
          duration_redistribution: decision.durationRedistribution ?? 'none',
        } : {}),
      })
      if (sequence !== _h3PlanReviewSequence || get().activeWorkspace !== workspace || get().pendingH3PlanJobId !== jobId) return
      set(s => ({
        pendingH3Plan: null,
        pendingH3PlanEstimate: null,
        pendingH3PlanJobId: null,
        pendingH3PlanWorkspace: null,
        h3PlanReviewLoading: false,
        jobs: s.jobs.map(job => job.id === jobId ? {
          ...job,
          status: result.status,
          phase: 'registered',
          message: 'Queued...',
          planReviewRequired: false,
          planReviewTermsRequired: false,
          planReviewDeadline: null,
          h3SegmentPlan: result.h3_segment_plan,
          h3Estimate: result.h3_estimate,
          etaSeconds: _h3EstimateTotalSeconds(result.h3_estimate),
        } : job),
      }))
      get()._pollRecoveredJob(jobId)
      window.dispatchEvent(new CustomEvent('maestro:queue-refresh'))
    } catch (error) {
      if (sequence !== _h3PlanReviewSequence || get().activeWorkspace !== workspace || get().pendingH3PlanJobId !== jobId) return
      set({
        h3PlanReviewLoading: false,
        h3PlanReviewError: error instanceof Error ? error.message : 'The generation plan could not be approved.',
      })
    }
  },
  cancelH3Plan: async () => {
    const sequence = ++_h3PlanReviewSequence
    const { pendingH3PlanJobId: jobId, pendingH3PlanWorkspace: workspace } = get()
    if (!jobId || !workspace || get().activeWorkspace !== workspace) return
    set({ h3PlanReviewLoading: true, h3PlanReviewError: null })
    try {
      await api.cancelJob(jobId)
      if (sequence !== _h3PlanReviewSequence || get().activeWorkspace !== workspace || get().pendingH3PlanJobId !== jobId) return
      _recoveryJobPolls.get(jobId)?.stop()
      set(s => ({
        pendingH3Plan: null,
        pendingH3PlanEstimate: null,
        pendingH3PlanJobId: null,
        pendingH3PlanWorkspace: null,
        h3PlanReviewLoading: false,
        jobs: s.jobs.map(job => job.id === jobId ? {
          ...job,
          status: 'cancelled',
          message: 'Cancelled',
          planReviewRequired: false,
          planReviewTermsRequired: false,
          planReviewDeadline: null,
        } : job),
        isGenerating: s.jobs.some(job => job.id !== jobId && _isActiveGenerationJob(job)),
      }))
      window.dispatchEvent(new CustomEvent('maestro:queue-refresh'))
    } catch (error) {
      if (sequence !== _h3PlanReviewSequence || get().activeWorkspace !== workspace || get().pendingH3PlanJobId !== jobId) return
      set({
        h3PlanReviewLoading: false,
        h3PlanReviewError: error instanceof Error ? error.message : 'The generation could not be cancelled.',
      })
    }
  },

  startGeneration: async () => {
    const accountIdentityEpoch = _accountIdentityEpoch
    const ownsSubmission = () => _accountIdentityIsCurrent(accountIdentityEpoch)
    let state = get()
    const submissionWorkspace = state.activeWorkspace
    // Model changes update params.model_type immediately and load capabilities
    // asynchronously. Never submit against the previous model's limits or
    // conditioning contract during that short hand-off window.
    if (state.modelOptionsLoading) {
      window.alert('Model settings are still loading. Please wait a moment before generating.')
      return
    }
    if (state.h3StyleWorkflow
      && !state.h3StyleWorkflowCatalog
      && !state.h3StyleWorkflowCatalogLoading) {
      await state.loadH3StyleWorkflowCatalog()
      if (!ownsSubmission()) return
      state = get()
    }
    if (state.h3StyleWorkflow && state.h3StyleWorkflowCatalogLoading) {
      window.alert('The H3 workflow catalog is still loading. Please wait a moment before generating.')
      return
    }
    if (state.h3StyleWorkflow && !state.h3StyleWorkflowCatalog) {
      window.alert('The selected H3 workflow could not be verified against the server catalog and was not submitted.')
      return
    }
    if (state.h3StyleWorkflow
      && h3StyleWorkflowSupportsModel(state.h3StyleWorkflowCatalog, state.params.model_type)
      && !h3StyleWorkflowSelectionIsCurrent(state.h3StyleWorkflowCatalog, state.h3StyleWorkflow)) {
      state.setH3StyleWorkflow('')
      window.alert('The selected H3 workflow is no longer in the server catalog. Choose another workflow before generating.')
      return
    }
    const h3StudioModel = H3_STUDIO_MODELS.has(state.params.model_type)
    const h3AdaptiveConditioning = state.params.h3_adaptive_conditioning !== false
    const h3FixedRef2VA = (
      state.params.model_type === 'minimax_h3_ref2va'
      || state.modelOptions?.architecture === 'minimax_h3_ref2va'
      || state.modelOptions?.minimax_h3_conditioning_mode === 'semantic_references'
    )
    const imageCount = state.imageRefs.length + (state.params.image_refs?.length ?? 0)
    const videoCount = [state.params.video_guide, state.params.video_guide2, state.params.video_guide3].filter(Boolean).length
    const audioCount = [state.params.audio_guide, state.params.audio_guide2, state.params.audio_guide3].filter(Boolean).length
    const h3HasSemanticReferences = imageCount + videoCount + audioCount > 0
    const h3HasFrameAnchors = !!(
      state.startImage || state.endImage || state.params.image_start || state.params.image_end
    )
    if (h3StudioModel) {
      const primarySteps = Number(state.params.num_inference_steps)
      if (!Number.isInteger(primarySteps) || primarySteps < 2 || primarySteps > 50) {
        window.alert('MiniMax H3 inference steps must be a whole number from 2 to 50. The default is 20.')
        return
      }
      if (!h3AdaptiveConditioning && h3FixedRef2VA && h3HasFrameAnchors) {
        window.alert('Pinned Ref2VA cannot use first/last-frame anchors. Re-enable Automatically choose FL2VA / Ref2VA, remove the anchors, or select an FL2VA checkpoint.')
        return
      }
      if (!h3AdaptiveConditioning && !h3FixedRef2VA && h3HasSemanticReferences) {
        window.alert('Pinned FL2VA cannot use semantic references. Re-enable Automatically choose FL2VA / Ref2VA, remove the references, or select Ref2VA.')
        return
      }
      // Under Auto, the selected checkpoint is only a starting preference;
      // semantic inputs make Ref2VA certain here, while cut-driven Ref2VA is
      // discovered by durable server planning and gated on the exact job card.
      const h3WillUseRef2VA = (
        (!h3AdaptiveConditioning && h3FixedRef2VA)
        || (h3AdaptiveConditioning && h3HasSemanticReferences)
      )
      if (h3WillUseRef2VA && !h3Ref2VATermsAccepted()) {
        window.alert('Accept the MiniMax H3 Ref2VA model terms in Inputs before using semantic references or Ref2VA segments.')
        return
      }
      if (imageCount > 9 || videoCount > 3 || audioCount > 3 || imageCount + videoCount + audioCount > 12) {
        window.alert('MiniMax H3 Ref2VA allows up to 9 images, 3 videos, 3 audio clips, and 12 mixed reference files.')
        return
      }
      if (audioCount > imageCount + videoCount) {
        window.alert('MiniMax H3 Ref2VA needs at least as many visual references (images/videos) as audio references.')
        return
      }
      if (h3WillUseRef2VA && state.durationSeconds < 4) {
        window.alert('MiniMax H3 Ref2VA output duration must be at least 4 seconds. Longer Studio videos are split into native-length segments automatically.')
        return
      }
      const nativeMaximumSeconds = (
        (state.modelOptions?.frames_maximum || 0) / (state.modelOptions?.fps || 24)
      )
      if (
        h3AdaptiveConditioning
        && h3HasSemanticReferences
        && h3HasFrameAnchors
        && nativeMaximumSeconds > 0
        && state.durationSeconds <= nativeMaximumSeconds
      ) {
        window.alert(`FL2VA frame anchors and Ref2VA semantic references need separate H3 segments. Request more than ${nativeMaximumSeconds.toFixed(2)}s, or remove one conditioning type.`)
        return
      }
    }
    // Enhancement is durable job preparation. Submit the intent with the
    // original frozen request so the browser gets a job ID immediately and
    // never repeats the LLM work after a disconnect or refresh.
    const enhanceRequested = state.studioPromptEnhance
    if (enhanceRequested && !state.params.prompt.trim()) {
      window.alert('Enter a prompt before using Enhance before Generate.')
      return
    }
    const enhanceBeforeGenerate = enhanceRequested
    const usesDedicatedGenerationEndpoint = (
      (state.generationMode === 'video' && (state.params.image_mode as number) === 4)
      || (state.generationMode === 'avatar' && Boolean(state.editSubMode))
    )
    if (enhanceBeforeGenerate && usesDedicatedGenerationEndpoint) {
      window.alert('Enhance before Generate is available for standard Studio generations. Use the standalone Enhance action first for this Blend or Edit workflow.')
      return
    }

    // Validate: i2v-only models require a start image — Video mode only.
    // Edit sub-modes supply their own source media and validate in their
    // own branches (Recast runs the i2v-only SCAIL-2 against a source
    // video + reference image; this guard silently ate its clicks).
    const isI2vOnly = state.modelOptions?.i2v_class && !state.modelOptions?.t2v_class
    const hasStartImage = state.startImage || state.params.image_start
    const hasMultiClipImages = state.clips.some(c => c.startImage || c.startImagePath)
    if (state.generationMode === 'video' && isI2vOnly && !hasStartImage && !hasMultiClipImages) {
      console.error('This model requires a start image')
      // Could show a toast/notification here in the future
      return
    }

    // ── Video mode: Blend ──────────────────────────────────────────
    if (state.generationMode === 'video' && (state.params.image_mode as number) === 4) {
      if (!state.blendClipAPath || !state.blendClipBPath) return
      const prompt = (state.params.prompt as string || '').trim()

      const newJob: GenerationJob = {
        id: '', status: 'queued', progress: 0, step: 0, totalSteps: 0,
        phase: '', message: 'Submitting blend...', outputFiles: [], error: null, oomInfo: null,
      }
      set(s => ({ isGenerating: true, jobs: [newJob, ...s.jobs] }))

      try {
        const result = await api.submitBlend({
          clip_a_path: state.blendClipAPath,
          clip_b_path: state.blendClipBPath,
          prompt: prompt || 'smooth natural transition between the two clips',
          model_type: state.params.model_type as string,
          blend_mode: state.blendMode,
          overlap_sec: state.blendOverlapSec,
          // Blend-specific tuning knobs (exposed in BlendControls sliders)
          motion_prefix_sec: state.blendMotionPrefixSec,
          motion_suffix_sec: state.blendMotionSuffixSec,
          input_video_strength: state.blendAnchorStrength,
          seed: (state.params.seed as number) ?? -1,
          activated_loras: (state.params.activated_loras as string[]) || [],
          loras_multipliers: (state.params.loras_multipliers as string) || '',
          workspace: state.activeWorkspace,
          private_output: state.privateOutput,
          explicit_output: state.explicitOutput,
          // Pass the full Studio params so the backend can inherit the user's
          // progressive_pipeline / num_inference_steps / guidance_scale /
          // negative_prompt settings, matching what a manual SE generation
          // would have used. Blend-specific fields (image_start/end, video_length,
          // resolution, image_prompt_type) are overridden server-side.
          base_params: state.params as unknown as Record<string, unknown>,
        })
        if (!ownsSubmission()) {
          _discardStaleGenerationPlaceholder(newJob)
          return
        }

        set(s => ({
          jobs: s.jobs.map(j => j === newJob ? { ...j, id: result.job_id, status: 'queued', message: 'Queued...' } : j),
        }))
        get()._pollRecoveredJob(result.job_id)
        window.dispatchEvent(new CustomEvent('maestro:queue-refresh'))
      } catch (e) {
        if (!ownsSubmission()) {
          _discardStaleGenerationPlaceholder(newJob)
          return
        }
        const msg = e instanceof Error ? e.message : 'Blend failed'
        // Submit itself failed (pre-queue). Convert the placeholder to a
        // failed state in place so the user sees what went wrong instead of
        // the tile silently disappearing.
        set(s => ({
          jobs: s.jobs.map(j => j === newJob ? { ...j, id: j.id || `submit-fail-${Date.now()}`, status: 'failed', message: msg, error: msg } : j),
          isGenerating: s.jobs.some(j => j !== newJob && _isActiveGenerationJob(j)),
        }))
        console.error('Blend failed:', msg)
      }
      return
    }

    // ── Edit mode: Outpaint ────────────────────────────────────────
    if (state.generationMode === 'avatar' && state.editSubMode === 'outpaint') {
      if (!state.editVideoPath) return
      const prompt = (state.params.prompt as string || '').trim()

      // Resolve source pixel dimensions from the loaded video metadata.
      // We need them to convert the canvas-relative video box into absolute
      // pad_top/bottom/left/right pixel values that the server expects.
      const srcRes = state.editVideoResolution || ''
      const [srcWStr, srcHStr] = srcRes.split('x')
      const srcW = parseInt(srcWStr) || 0
      const srcH = parseInt(srcHStr) || 0
      if (srcW <= 0 || srcH <= 0) {
        console.error('Outpaint: source dimensions unknown')
        return
      }

      // Resolve canvas dimensions in source-pixel-space from the chosen aspect.
      // Canvas is grown so the source fits inside without cropping; pure
      // letterbox math.
      const aspect = state.outpaintAspect
      let canvasW = srcW, canvasH = srcH
      if (aspect !== 'source') {
        const [aw, ah] = aspect.split(':').map(Number)
        const target = aw / ah
        const srcRatio = srcW / srcH
        if (srcRatio > target) {
          canvasW = srcW
          canvasH = Math.round(srcW / target)
        } else {
          canvasH = srcH
          canvasW = Math.round(srcH * target)
        }
      }

      // The video box is canvas-relative (0–1). Convert to pixel pads.
      const box = state.outpaintVideoBox
      const videoX = Math.round(box.x * canvasW)
      const videoY = Math.round(box.y * canvasH)
      const videoW = Math.round(box.w * canvasW)
      const videoH = Math.round(box.h * canvasH)
      const padTop = Math.max(0, videoY)
      const padLeft = Math.max(0, videoX)
      const padBottom = Math.max(0, canvasH - videoY - videoH)
      const padRight = Math.max(0, canvasW - videoX - videoW)
      const totalPad = padTop + padBottom + padLeft + padRight
      if (totalPad === 0) return

      // Mirror the computed pads to outpaintPadding so metadata sidecars
      // and any older read paths still see the values.
      set({ outpaintPadding: { top: padTop, bottom: padBottom, left: padLeft, right: padRight } })

      // Optional film-strip trim: only send if user picked a non-trivial range.
      const trimStart = state.outpaintTrimStart || 0
      const trimEnd = state.outpaintTrimEnd || 0
      const sendTrim = trimEnd > trimStart && trimEnd > 0.05

      const newJob: GenerationJob = {
        id: '', status: 'queued', progress: 0, step: 0, totalSteps: 0,
        phase: '', message: 'Submitting outpaint...', outputFiles: [], error: null, oomInfo: null,
      }
      set(s => ({ isGenerating: true, jobs: [newJob, ...s.jobs] }))

      // Sliding window size: the Advanced Settings slider stores seconds.
      // Convert to frames using the loaded model's fps so the same value
      // round-trips between video and outpaint modes. Falls back to 25
      // (LTX-2 22B's native rate) if modelOptions hasn't loaded yet.
      const fps = (state.modelOptions?.fps as number) || 25
      const windowFrames = Math.max(
        1,
        Number(state.params.sliding_window_size) || Math.round(state.slidingWindowSeconds * fps),
      )
      const overlapFrames = Math.max(
        0,
        Number(state.params.sliding_window_overlap ?? state.slidingWindowOverlap ?? 0),
      )

      try {
        const result = await api.submitOutpaint({
          video_path: state.editVideoPath,
          prompt: prompt || 'extend the scene naturally',
          model_type: state.params.model_type as string,
          pad_top: padTop,
          pad_bottom: padBottom,
          pad_left: padLeft,
          pad_right: padRight,
          outpaint_aspect: state.outpaintAspect,
          resolution_preset: state.outpaintResolutionPreset,
          source_preservation: 1.0,
          outpaint_lora_strength: 1.0,
          mask_preserving_outpaint: state.outpaintMaskPreserving,
          preserve_source_audio: state.outpaintPreserveSourceAudio,
          lock_source_pixels: false,
          trim_window_smear: state.outpaintTrimSmear,
          ...(state.modelOptions?.sliding_window ? {
            sliding_window_size: windowFrames,
            sliding_window_overlap: overlapFrames,
          } : {}),
          ...(sendTrim ? { start_time: trimStart, end_time: trimEnd } : {}),
          num_inference_steps: (state.params.num_inference_steps as number) ?? undefined,
          guidance_scale: (state.params.guidance_scale as number) ?? undefined,
          negative_prompt: (state.params.negative_prompt as string) || undefined,
          seed: (state.params.seed as number) ?? -1,
          activated_loras: (state.params.activated_loras as string[]) || [],
          loras_multipliers: (state.params.loras_multipliers as string) || '',
          workspace: state.activeWorkspace,
          private_output: state.privateOutput,
          explicit_output: state.explicitOutput,
        })
        if (!ownsSubmission()) {
          _discardStaleGenerationPlaceholder(newJob)
          return
        }

        set(s => ({
          jobs: s.jobs.map(j => j === newJob ? { ...j, id: result.job_id, status: 'queued', message: 'Queued...' } : j),
        }))
        get()._pollRecoveredJob(result.job_id)
        window.dispatchEvent(new CustomEvent('maestro:queue-refresh'))
      } catch (e) {
        if (!ownsSubmission()) {
          _discardStaleGenerationPlaceholder(newJob)
          return
        }
        const msg = e instanceof Error ? e.message : 'Outpaint failed'
        // Submit itself failed (pre-queue). Convert the placeholder to a
        // failed state in place so the user sees what went wrong instead of
        // the tile silently disappearing.
        set(s => ({
          jobs: s.jobs.map(j => j === newJob ? { ...j, id: j.id || `submit-fail-${Date.now()}`, status: 'failed', message: msg, error: msg } : j),
          isGenerating: s.jobs.some(j => j !== newJob && _isActiveGenerationJob(j)),
        }))
        console.error('Outpaint failed:', msg)
      }
      return
    }

    // ── Edit mode: Recast (SCAIL-2 Replace) ─────────────────────
    // Standalone branch: the prompt is OPTIONAL here (the server has a
    // sensible default), unlike the shared edit block below which
    // hard-requires one.
    // Repaint is the easy front door to the proven Studio Video/Frames
    // SCAIL-2 Animate path: an edited first frame defines the finished look
    // while the source video supplies motion and camera movement.
    if (state.generationMode === 'avatar' && state.editSubMode === 'restyle') {
      if (!state.editVideoPath || !state.editRepaintFramePath) return
      const repaintMappings = state.editRepaintMappings.slice(0, 5)
      if (repaintMappings.some(mapping => !mapping.source.trim() || !mapping.target.trim())) return
      const promptText = ((state.params.prompt as string) || '').trim()
      const newJob: GenerationJob = {
        id: '', status: 'queued', progress: 0, step: 0, totalSteps: 0,
        phase: '', message: 'Submitting repaint...', outputFiles: [], error: null, oomInfo: null,
      }
      set(s => ({ isGenerating: true, jobs: [newJob, ...s.jobs] }))

      try {
        const repaintModel = (state.params.model_type as string) || ''
        const repaintIsScail2 = repaintModel === 'scail2_14B_fast' || repaintModel === 'scail2_14B'
        const result = await api.submitRepaint({
          video_path: state.editVideoPath,
          target_frame_path: state.editRepaintFramePath,
          region_mappings: repaintMappings.map(mapping => ({
            id: mapping.id,
            source: mapping.source.trim(),
            target: mapping.target.trim(),
          })),
          ...(promptText ? { prompt: promptText } : {}),
          resolution_profile: state.editRepaintResolutionProfile,
          ...(repaintIsScail2 ? {
            model_type: repaintModel,
            num_inference_steps: (state.params.num_inference_steps as number) ?? undefined,
            ...(repaintModel === 'scail2_14B' ? {
              guidance_scale: (state.params.guidance_scale as number) ?? undefined,
            } : {}),
          } : {}),
          start_time: state.editStartTime,
          end_time: state.editEndTime,
          seed: (state.params.seed as number) ?? -1,
          negative_prompt: (state.params.negative_prompt as string) || '',
          activated_loras: (state.params.activated_loras as string[]) || [],
          loras_multipliers: (state.params.loras_multipliers as string) || '',
          workspace: state.activeWorkspace,
          private_output: state.privateOutput,
          explicit_output: state.explicitOutput,
        })
        if (!ownsSubmission()) {
          _discardStaleGenerationPlaceholder(newJob)
          return
        }

        set(s => ({
          jobs: s.jobs.map(j => j === newJob
            ? { ...j, id: result.job_id, status: 'queued', message: 'Queued...' }
            : j),
        }))
        get()._pollRecoveredJob(result.job_id)
        window.dispatchEvent(new CustomEvent('maestro:queue-refresh'))
      } catch (e) {
        if (!ownsSubmission()) {
          _discardStaleGenerationPlaceholder(newJob)
          return
        }
        const msg = e instanceof Error ? e.message : 'Repaint failed'
        set(s => ({
          jobs: s.jobs.map(j => j === newJob
            ? { ...j, id: j.id || `submit-fail-${Date.now()}`, status: 'failed', message: msg, error: msg }
            : j),
          isGenerating: s.jobs.some(j => j !== newJob && _isActiveGenerationJob(j)),
        }))
        console.error('Repaint failed:', msg)
      }
      return
    }

    if (state.generationMode === 'avatar' && state.editSubMode === 'recast') {
      const recastMappings = state.editRecastMappings.slice(0, 5)
      if (
        !state.editVideoPath
        || recastMappings.length === 0
        || recastMappings.some(mapping => !mapping.target.trim() || !mapping.refPath)
      ) return
      const promptText = ((state.params.prompt as string) || '').trim()

      const newJob: GenerationJob = {
        id: '', status: 'queued', progress: 0, step: 0, totalSteps: 0,
        phase: '', message: 'Submitting recast...', outputFiles: [], error: null, oomInfo: null,
      }
      set(s => ({ isGenerating: true, jobs: [newJob, ...s.jobs] }))

      try {
        // Honor the selector's Recast SCAIL-2 choice (dedicated Fast vs
        // native base). Guard on
        // architecture so a stale LTX model_type can never reach the
        // recast endpoint — the server then falls back to Fast.
        const recastModel = (state.params.model_type as string) || ''
        const recastIsScail2 = state.models.find(m => m.model_type === recastModel)?.architecture === 'scail2_14B'
        const result = await api.submitRecast({
          video_path: state.editVideoPath,
          // Legacy fields remain populated for old sidecars/API clients, while
          // the explicit cards provide deterministic target/color assignment.
          ref_image_path: recastMappings[0].refPath,
          target: recastMappings[0].target || 'person',
          person_count: recastMappings.length,
          reference_aligned_to_source: recastMappings[0].referenceAlignedToSource,
          character_mappings: recastMappings.map(mapping => ({
            id: mapping.id,
            target: mapping.target.trim(),
            ref_image_path: mapping.refPath,
            additional_ref_image_paths: mapping.additionalRefs
              .map(reference => reference.path)
              .filter(Boolean),
            reference_aligned_to_source: mapping.referenceAlignedToSource,
          })),
          // Simplified Recast recipe: identity preparation and native
          // bystander preservation are automatic; prompt rewriting and the
          // seam-prone post-composite remain off. The backend still accepts
          // all legacy fields for saved/API callers.
          isolate_reference: true,
          auto_face_detail: true,
          enhance_prompt: false,
          protect_bystanders: false,
          preserve_bystanders: true,
          use_relighting: state.editRecastUseRelighting,
          resolution_profile: state.editRecastResolutionProfile,
          ...(promptText ? { prompt: promptText } : {}),
          ...(recastIsScail2 ? {
            model_type: recastModel,
            num_inference_steps: (state.params.num_inference_steps as number) ?? undefined,
            ...(recastModel === 'scail2_14B' ? {
              guidance_scale: (state.params.guidance_scale as number) ?? undefined,
            } : {}),
          } : {}),
          start_time: state.editStartTime,
          end_time: state.editEndTime,
          seed: (state.params.seed as number) ?? -1,
          negative_prompt: (state.params.negative_prompt as string) || '',
          activated_loras: (state.params.activated_loras as string[]) || [],
          loras_multipliers: (state.params.loras_multipliers as string) || '',
          workspace: state.activeWorkspace,
          private_output: state.privateOutput,
          explicit_output: state.explicitOutput,
        })
        if (!ownsSubmission()) {
          _discardStaleGenerationPlaceholder(newJob)
          return
        }

        set(s => ({
          jobs: s.jobs.map(j => j === newJob ? { ...j, id: result.job_id, status: 'queued', message: 'Queued...' } : j),
        }))
        get()._pollRecoveredJob(result.job_id)
        window.dispatchEvent(new CustomEvent('maestro:queue-refresh'))
      } catch (e) {
        if (!ownsSubmission()) {
          _discardStaleGenerationPlaceholder(newJob)
          return
        }
        const msg = e instanceof Error ? e.message : 'Recast failed'
        set(s => ({
          jobs: s.jobs.map(j => j === newJob ? { ...j, id: j.id || `submit-fail-${Date.now()}`, status: 'failed', message: msg, error: msg } : j),
          isGenerating: s.jobs.some(j => j !== newJob && _isActiveGenerationJob(j)),
        }))
        console.error('Recast failed:', msg)
      }
      return
    }

    // ── Edit mode: Retake / Inpaint / Edit Anything ─────────────
    if (state.generationMode === 'avatar' && (state.editSubMode === 'retake' || state.editSubMode === 'inpaint' || state.editSubMode === 'edit_anything')) {
      if (!state.editVideoPath) return
      const prompt = (state.params.prompt as string || '').trim()
      if (!prompt) return

      const newJob: GenerationJob = {
        id: '', status: 'queued', progress: 0, step: 0, totalSteps: 0,
        phase: '', message: 'Submitting...', outputFiles: [], error: null, oomInfo: null,
      }
      set(s => ({ isGenerating: true, jobs: [newJob, ...s.jobs] }))

      try {
        let result: { job_id: string }
        if (state.editSubMode === 'edit_anything') {
          result = await api.submitEditAnything({
            video_path: state.editVideoPath,
            prompt,
            model_type: state.params.model_type as string,
            start_time: state.editStartTime,
            end_time: state.editEndTime,
            lora_strength: state.editAnythingLoraStrength,
            retake_strength: state.editRetakeStrength,
            seed: (state.params.seed as number) ?? -1,
            // Edit Anything LoRA card: start with CFG=1 on distilled; raise
            // only if the edit is too weak. We route the user's global CFG
            // slider through so they can experiment.
            guidance_scale: (state.params.guidance_scale as number) ?? 1.0,
            num_inference_steps: (state.params.num_inference_steps as number) ?? 8,
            negative_prompt: (state.params.negative_prompt as string) || '',
            activated_loras: (state.params.activated_loras as string[]) || [],
            loras_multipliers: (state.params.loras_multipliers as string) || '',
            workspace: state.activeWorkspace,
            private_output: state.privateOutput,
            explicit_output: state.explicitOutput,
            // Optional boundary anchors. Empty values mean "use source
            // frames" (today's auto-extract behavior); ltx2.py treats
            // missing/null/empty path as "fall back to source".
            ...(state.editAnythingStartAnchor ? { start_anchor_path: state.editAnythingStartAnchor } : {}),
            ...(state.editAnythingEndAnchor ? { end_anchor_path: state.editAnythingEndAnchor } : {}),
          })
        } else if (state.editSubMode === 'inpaint') {
          result = await api.submitInpaint({
            video_path: state.editVideoPath,
            description: prompt,
            sam_target: state.editSamTarget || undefined,
            invert_mask: state.editInvertMask || undefined,
            start_time: state.editStartTime,
            end_time: state.editEndTime,
            model_type: state.params.model_type as string,
            seed: (state.params.seed as number) ?? -1,
            // Inpaint needs CFG > 1.0 to make the prompt actually influence
            // the masked region. The edit-specific editPromptStrength slider
            // (default 3.5) drives this; the global params.guidance_scale is
            // fine for normal generation but would silently default to 1.0
            // and silently break inpaint.
            guidance_scale: state.editPromptStrength,
            retake_strength: state.editRetakeStrength,
            num_inference_steps: (state.params.num_inference_steps as number) ?? 8,
            negative_prompt: (state.params.negative_prompt as string) || '',
            resolution: (state.params.resolution as string) || '',
            activated_loras: (state.params.activated_loras as string[]) || [],
            loras_multipliers: (state.params.loras_multipliers as string) || '',
            masks_path: state.editMasksPath || undefined,
            workspace: state.activeWorkspace,
            private_output: state.privateOutput,
            explicit_output: state.explicitOutput,
          })
        } else {
          result = await api.submitRetake({
            video_path: state.editVideoPath,
            start_time: state.editStartTime,
            end_time: state.editEndTime,
            prompt,
            model_type: state.params.model_type as string,
            retake_strength: state.editRetakeStrength,
            retake_engine: state.editRetakeEngine,
            regenerate_audio: state.editRegenerateAudio,
            seed: (state.params.seed as number) ?? -1,
            // Retake also benefits from CFG > 1.0 when the user provides a
            // prompt that should drive the regenerated region (e.g. new
            // outfit, different style). Previously stuck at 1.0 via
            // params.guidance_scale fallback — same silent bug as inpaint.
            guidance_scale: state.editPromptStrength,
            num_inference_steps: (state.params.num_inference_steps as number) ?? 8,
            negative_prompt: (state.params.negative_prompt as string) || '',
            resolution: (state.params.resolution as string) || '',
            activated_loras: (state.params.activated_loras as string[]) || [],
            loras_multipliers: (state.params.loras_multipliers as string) || '',
            workspace: state.activeWorkspace,
            private_output: state.privateOutput,
            explicit_output: state.explicitOutput,
          })
        }
        if (!ownsSubmission()) {
          _discardStaleGenerationPlaceholder(newJob)
          return
        }

        set(s => ({
          jobs: s.jobs.map(j => j === newJob ? { ...j, id: result.job_id, status: 'queued', message: 'Queued...' } : j),
        }))
        get()._pollRecoveredJob(result.job_id)
        window.dispatchEvent(new CustomEvent('maestro:queue-refresh'))
      } catch (e) {
        if (!ownsSubmission()) {
          _discardStaleGenerationPlaceholder(newJob)
          return
        }
        const msg = e instanceof Error ? e.message : 'Generation failed'
        // Submit itself failed (pre-queue). Convert the placeholder to a
        // failed state in place so the user sees what went wrong instead of
        // the tile silently disappearing.
        set(s => ({
          jobs: s.jobs.map(j => j === newJob ? { ...j, id: j.id || `submit-fail-${Date.now()}`, status: 'failed', message: msg, error: msg } : j),
          isGenerating: s.jobs.some(j => j !== newJob && _isActiveGenerationJob(j)),
        }))
        console.error('Edit generation failed:', msg)
      }
      return  // Don't fall through to normal generation
    }

    const params: Record<string, unknown> = {
      ...state.params,
      generation_mode: state.generationMode,
      workspace: submissionWorkspace,
      private_output: state.privateOutput,
      explicit_output: state.explicitOutput,
      h3_ref2va_terms_accepted: h3Ref2VATermsAccepted(),
    }
    // The prompt remains browser-authored. Only the exact catalog ID crosses
    // this boundary; the server resolves and compiles revision-bound guidance.
    delete params.h3_style_workflow
    const h3StyleWorkflow = resolveH3StyleWorkflowRequest(
      state.h3StyleWorkflowCatalog,
      state.params.model_type,
      state.h3StyleWorkflow,
    )
    if (h3StyleWorkflow) params.h3_style_workflow = h3StyleWorkflow

    // STG (Spatio-Temporal Guidance) wiring. The backend only runs STG when
    // perturbation_switch === 2 (skip-self-attention) — stg_scale alone is
    // inert. Derive the switch from the slider so an untouched slider keeps
    // the exact request shape from before this feature existed, and strip
    // all perturbation params for models without the capability so a stale
    // value can't leak across a model switch.
    if (state.modelOptions?.perturbation) {
      const stg = params.stg_scale as number | undefined
      if (stg !== undefined) {
        params.perturbation_switch = stg > 0 ? 2 : 0
      }
    } else {
      delete params.stg_scale
      delete params.perturbation_switch
      delete params.perturbation_layers
      delete params.perturbation_start_perc
      delete params.perturbation_end_perc
    }
    // Reference pipeline is a per-model capability — strip a stale toggle
    // value if the user switched to a model that doesn't support it.
    if (!(state.modelOptions as Record<string, unknown> | null)?.reference_pipeline) {
      delete params.reference_pipeline
    }

    // Tag avatar/edit-mode generations with their sub-mode so the gallery's
    // Edits filter and the loadSettingsFromOutput restore path can identify
    // them. Dedicated edit endpoints tag their jobs on the server; this is
    // retained for compatible generic edit submissions.
    if (state.generationMode === 'avatar' && state.editSubMode) {
      params.edit_sub_mode = state.editSubMode
    }

    // Default I2V / video-source strength. Distilled LTX-2 pipelines produce
    // noticeably better motion when the input anchor is at 0.7 instead of
    // tight-locked 1.0 — matches ComfyUI's reference distilled workflows
    // (stage 1 / single-stage both use 0.7). Dev and other families keep 1.0.
    // User can override via the slider; this only fires when the param isn't
    // already set.
    const _defaultIVS = (() => {
      const mt = (params.model_type as string) || ''
      return mt.includes('distilled') ? 0.7 : 1.0
    })()

    // force_fps="control" models (SCAIL-2 class) generate at the control
    // video's frame rate, but durationSeconds→video_length math uses the
    // model's nominal fps (16). Against a 25fps guide that under-counts
    // frames by a third: a "10s" request would cover only 6.4s of the
    // source performance. When the guide's real fps is known (probed at
    // upload), recompute the frame count at the rate the output will
    // actually play at.
    if (
      state.generationMode === 'video' &&
      params.video_guide &&
      params.force_fps === 'control' &&
      state.guideVideoFps && state.guideVideoFps > 0
    ) {
      // Cap at 30fps to match the server's follow-rate cap — a 60fps
      // guide would double the frame count (and sliding windows) for
      // no visible gain.
      const fpsUsed = Math.min(state.guideVideoFps, 30)
      params.video_length = Math.max(5, Math.round(state.durationSeconds * fpsUsed))
    }
    // Always tell the server what duration the user actually asked for.
    // For control-fps models the server recomputes video_length from
    // this at the guide's REAL frame rate — the durable fix for stale
    // restores (Load Settings from old sidecars carries frame counts
    // computed under the wrong fps) and for sessions where the guide's
    // fps never got probed. Underscore keys ride through harmlessly.
    if (state.generationMode === 'video' && params.video_guide) {
      ;(params as Record<string, unknown>)._duration_seconds = state.durationSeconds
    }

    // Smart multi-line prompt handling for video Frames mode. A complete
    // Studio prompt with global timestamps stays ONE structured prompt; the
    // backend maps it against the effective FPS and exact quantized windows.
    // Untimed multi-line prompts keep the legacy line-per-window behavior.
    if (state.generationMode === 'video' && state.params.image_mode !== 2) {
      const prompt = (params.prompt as string) || ''
      const segmentedStudio = usesStudioSegments(state.modelOptions)
      const hasSlidingWindow = (
        state.modelOptions?.sliding_window === true || segmentedStudio
      ) && state.durationSeconds > state.slidingWindowSeconds
      if (hasGlobalTimeline(prompt)) {
        params.multi_prompts_gen_type = 2
      } else if (segmentedStudio) {
        // H3 long-form planning repeats one complete Studio description per
        // native clip. Mode 1 would split a multi-line prompt and silently
        // discard all but the first line on this non-sliding model.
        params.multi_prompts_gen_type = 2
      } else if (hasSlidingWindow && prompt.includes('\n')) {
        // Sliding window: each line = one window prompt (rolling generation)
        params.multi_prompts_gen_type = 1
      } else if (!hasSlidingWindow && prompt.includes('\n')) {
        // No sliding window — send entire prompt as one (multi_prompts_gen_type=2 preserves newlines)
        params.multi_prompts_gen_type = 2
      }
    }

    // Post-processing settings
    if (state.spatialUpsampling) params.spatial_upsampling = state.spatialUpsampling
    if (state.filmGrainIntensity > 0) {
      params.film_grain_intensity = state.filmGrainIntensity
      params.film_grain_saturation = state.filmGrainSaturation
    }
    // Voice clone (SeedVC) — only send if the user explicitly enabled
    // it AND provided at least one reference. Backend defaults all three
    // params to falsy if absent (postprocessing step is a no-op).
    if (state.voiceCloneEnabled && state.voiceCloneRefs.length > 0) {
      const validRefs = state.voiceCloneRefs.filter(r => r && r.path)
      if (validRefs.length > 0) {
        params.voice_clone_enabled = true
        params.voice_clone_mode = state.voiceCloneMode
        // Pass server-side paths (already uploaded via /api/v1/upload-audio).
        params.voice_clone_refs = validRefs.map(r => r.path)
      }
    }

    // Image mode: force single frame + image output format
    // Backend uses image_mode > 0 to determine output as image (.jpg) vs video (.mp4)
    if (state.generationMode === 'image') {
      params.video_length = 1
      params.image_mode = 1
      // WanGP expects control input in image_guide (not video_guide) for image mode
      if (params.video_guide && !params.image_guide) {
        params.image_guide = params.video_guide
      }
    }

    // Audio mode: branch by sub-mode (Speech/Music vs SFX)
    if (state.generationMode === 'audio') {
      // Record the active sub-tab in the request so it lands in the
      // .meta.json sidecar — Load Settings uses it to restore Speech /
      // Music / SFX, not just the Audio tab. Underscore keys ride
      // through generation untouched, same as _tts_*. Music also saves
      // its song-writer inputs (UI-only, not consumed by generation).
      params._audio_sub_mode = state.audioSubMode
      if (state.audioSubMode === 'music') {
        params._music_description = state.musicDescription || ''
        params._music_instrumental = !!state.musicInstrumental
      }
      if (state.audioSubMode === 'sfx') {
        // SFX mode: use MMAudio to generate sound effects
        // MMAudio runs as post-processing on a video model, so use a video model as carrier
        const sfxModel = params.model_type as string
        const isSfxVirtual = sfxModel.startsWith('mmaudio_')
        if (isSfxVirtual) {
          // Swap virtual MMAudio model for a real video model; backend uses MMAudio params
          params.model_type = 'ltx2_22B_distilled_1_1'
          // Keep the virtual id so Load Settings can restore the SFX tab's
          // model selection (the sidecar otherwise records only the carrier).
          params._sfx_virtual_model = sfxModel
        }
        params.MMAudio_setting = 1
        // Always set MMAudio variant explicitly so backend doesn't fall back to server config
        params._mmaudio_variant = sfxModel === 'mmaudio_nsfw' ? 'nsfw' : 'v2'
        // Copy MMAudio prompt into main prompt field (for API validation & metadata)
        if (!params.prompt && params.MMAudio_prompt) {
          params.prompt = params.MMAudio_prompt
        }
        params.sfx_mode = true
        params.duration_seconds = state.durationSeconds
        // Generate a minimal video if no video_guide uploaded (1 frame), then run MMAudio
        if (!params.video_guide) {
          params.video_length = 17  // Minimum viable video for MMAudio (~1s)
          params.num_inference_steps = 4
        } else {
          params.video_length = 0  // No video gen needed — just run MMAudio on uploaded video
        }
        params.image_mode = 0
        // Clear video-specific params
        delete params.sliding_window_size
        delete params.sliding_window_overlap
        delete params.sliding_window_discard_last_frames
      } else {
        // Speech/Music TTS mode
        params.video_length = 0
        params.image_mode = 0
        params.multi_prompts_gen_type = 2  // Preserve full text as one prompt (don't split by newlines)
        // Save original prompt + speaker names before swap (for load settings)
        params._tts_original_prompt = params.prompt
        params._tts_speaker_name1 = state.ttsSpeakerName1 || ''
        params._tts_speaker_name2 = state.ttsSpeakerName2 || ''
        // Save all voice names for metadata
        for (let i = 0; i < state.ttsVoices.length; i++) {
          (params as Record<string, unknown>)[`_tts_speaker_name${i + 1}`] = state.ttsVoices[i]?.name || ''
        }
        params._tts_voice_count = state.ttsVoiceCount
        // Swap character names → Speaker N: for TTS multi-voice mode
        const escapeRegex = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        let text = params.prompt as string
        for (let i = 0; i < state.ttsVoices.length; i++) {
          const name = state.ttsVoices[i]?.name
          if (name) {
            text = text.replace(new RegExp(escapeRegex(name) + '\\s*:', 'gi'), `Speaker ${i + 1}:`)
          }
        }
        params.prompt = text
        // Set audio_guide paths for each voice (audio_guide, audio_guide2, audio_guide3, etc.)
        for (let i = 0; i < state.ttsVoices.length; i++) {
          const voice = state.ttsVoices[i]
          if (voice?.path) {
            const key = i === 0 ? 'audio_guide' : `audio_guide${i + 1}`
            params[key as keyof typeof params] = voice.path as never
          }
        }
        // TTS duration (max duration for the model to generate)
        if (state.modelOptions?.audio_only) {
          // Prefer the slider's `default` (some TTS models — e.g. DramaBox —
          // set default=0 to mean "auto-derive duration from prompt"); fall
          // back to `max` then 600.
          const ds = state.modelOptions.duration_slider
          const sliderDefault = ds?.default ?? ds?.max ?? 600
          params.duration_seconds = state.durationSeconds < 30 ? sliderDefault : state.durationSeconds
        }
        // Let the TTS model use its own defaults for steps/guidance if ours are video defaults
        if ((params.num_inference_steps as number) > 0 && state.modelOptions?.default_num_inference_steps == null) {
          params.num_inference_steps = 0
        }
        // Clear video-specific params
        delete params.sliding_window_size
        delete params.sliding_window_overlap
        delete params.sliding_window_discard_last_frames
      }
    }

    // Defensive cleanup: strip stale "V" (Source Video / extend) flag from
    // image_prompt_type when we're NOT entering the extend/continue path.
    //
    // The leak: when the user does a video extend (image_mode=3 +
    // continueVideoPath) and submits, the continue-mode branch below sets
    // params.image_prompt_type = "V". That mutation is on the local params
    // copy and shouldn't persist, BUT load-settings (loadSettingsFromOutput
    // at line 5284) DOES restore image_prompt_type from sidecar metadata
    // into state.params.image_prompt_type. So after extending a video and
    // then switching back to Frames mode via ModeToggle (which only flips
    // image_mode 3 -> 0, leaving image_prompt_type untouched), the next
    // generation carries forward image_prompt_type="V" from state.
    //
    // The single-clip and end-image handlers below only APPEND flags
    // (e.g. "S" + "V" -> "SV"), they never strip stale ones. So the "V"
    // survives, the backend (wgp.py:941-943) sees it and demands
    // video_source — but the user is in Frames mode with no source video.
    //
    // Symptom user reported: "I did a video extend and it worked. then I
    // switched to normal video mode and it keeps telling me to load a
    // source video. even after I refresh the page and try a new generation."
    //
    // Fix: strip "V" up-front unless we're going to re-add it in the
    // continue/extend branch below. The continue branch (image_mode === 3
    // + continueVideoPath) re-sets image_prompt_type = "V" wholesale, so
    // stripping here is safe — that branch puts it back.
    const willEnterContinueBranch = state.generationMode === 'video'
      && (params.image_mode === 3 || state.params.image_mode === 3)
      && !!state.continueVideoPath
    if (!willEnterContinueBranch) {
      const ipt = (params.image_prompt_type as string) || ''
      if (ipt.includes('V')) {
        params.image_prompt_type = ipt.replace(/V/g, '')
      }
      // Same stale-flag defense for the "T" temporal-alignment flag the
      // continue branch adds to video_prompt_type (see below). Without a
      // source video it's a backend no-op (alignment shift = 0 frames),
      // but stripping keeps restored-from-sidecar state from carrying it
      // into unrelated generations. Only the TRAILING "T" is that flag — an
      // internal "T" is the depth_temporal control letter (PTVG/TVG/TEVG),
      // and a global strip turned "Motion + Temporal Depth" (PTVG) into plain
      // "Transfer Human Motion" (PVG) at submit time, so use /T$/.
      const vptClean = (params.video_prompt_type as string) || ''
      if (vptClean.endsWith('T')) {
        params.video_prompt_type = vptClean.replace(/T$/, '')
      }
    }

    // Multi-clip path
    if (state.params.image_mode === 2) {
      const clips = state.clips
      const imagePaths: string[] = []
      const endImagePaths: string[] = []
      let hasAnyEndImage = false

      for (const clip of clips) {
        if (clip.startImage) {
          try {
            const result = await api.uploadImage(clip.startImage)
            if (!ownsSubmission()) return
            imagePaths.push(result.path)
          } catch (e) {
            if (!ownsSubmission()) return
            console.error('Failed to upload clip image:', e)
            imagePaths.push('')
          }
        } else if (clip.startImagePath) {
          imagePaths.push(clip.startImagePath)
        } else {
          imagePaths.push('')
        }

        // Upload end images (seamless mode)
        if (clip.endImage) {
          try {
            const result = await api.uploadImage(clip.endImage)
            if (!ownsSubmission()) return
            endImagePaths.push(result.path)
            hasAnyEndImage = true
          } catch (e) {
            if (!ownsSubmission()) return
            console.error('Failed to upload clip end image:', e)
            endImagePaths.push('')
          }
        } else if (clip.endImagePath) {
          endImagePaths.push(clip.endImagePath)
          hasAnyEndImage = true
        } else {
          endImagePaths.push('')
        }
      }

      let promptLines: string[]
      if (state.singlePromptMode) {
        const p: string = clips[0]?.prompt || (params.prompt as string) || ''
        promptLines = clips.map(() => p)
      } else {
        promptLines = clips.map(c => c.prompt || '')
      }

      params.prompt = promptLines.join('\n')
      params.image_start = imagePaths
      if (hasAnyEndImage) {
        params.image_end = endImagePaths
      }
      params.multi_prompts_gen_type = 3
      params.image_mode = 0
      params.image_prompt_type = hasAnyEndImage ? 'SE' : 'S'
      if (params.input_video_strength == null) params.input_video_strength = _defaultIVS
    }
    // Single I2V path: Upload images if present (new File upload takes priority)
    // Skip in image mode — startImage is for video I2V, not image generation
    else if (state.startImage && state.generationMode !== 'image') {
      try {
        const result = await api.uploadImage(state.startImage)
        if (!ownsSubmission()) return
        params.image_start = result.path
        params.image_mode = 0
        const ipt = (params.image_prompt_type as string) || ''
        if (!ipt.includes('S')) params.image_prompt_type = 'S' + ipt
        if (params.input_video_strength == null) params.input_video_strength = _defaultIVS
      } catch (e) {
        if (!ownsSubmission()) return
        console.error('Failed to upload start image:', e)
      }
    } else if (params.image_start && state.generationMode !== 'image') {
      // Re-roll case: image_start is already an absolute path from sidecar metadata
      params.image_mode = 0
      const ipt = (params.image_prompt_type as string) || ''
      if (!ipt.includes('S')) params.image_prompt_type = 'S' + ipt
      if (params.input_video_strength == null) params.input_video_strength = _defaultIVS
    }
    if (state.endImage) {
      try {
        const result = await api.uploadImage(state.endImage)
        if (!ownsSubmission()) return
        params.image_end = result.path
        const ipt = (params.image_prompt_type as string) || ''
        if (!ipt.includes('E')) params.image_prompt_type = ipt + 'E'
      } catch (e) {
        if (!ownsSubmission()) return
        console.error('Failed to upload end image:', e)
      }
    } else if (params.image_end) {
      const ipt = (params.image_prompt_type as string) || ''
      if (!ipt.includes('E')) params.image_prompt_type = ipt + 'E'
    }

    // Continue mode: set video_source and image_prompt_type="V"
    if (state.generationMode === 'video' && params.image_mode === 3 && state.continueVideoPath) {
      params.video_source = state.continueVideoPath
      params.image_prompt_type = 'V'
      params.image_mode = 0
      if (params.input_video_strength == null) params.input_video_strength = _defaultIVS
      // Temporal alignment: the UI scopes EVERYTHING to the new content —
      // durationSeconds is the extend length, and ControlVideoSection's
      // injected-frame positions are computed against that timeline. The
      // backend, however, defaults to interpreting frames_positions (and
      // control video / control audio alignment) against the FULL timeline
      // including the source clip (wgp.py: reset_control_aligment = "T" in
      // video_prompt_type; alignment_shift = source frames only when "T").
      // Without "T", a frame injected at "end of the new 20s" of a 10s clip
      // lands at the 20s mark of the 30s output — 10s early; on longer
      // sources the position can fall entirely INSIDE the source span and
      // visibly never happen. "T" = upstream's "Aligned to the beginning of
      // the First Window of the new Video Sample", which matches the UI.
      // Append the alignment flag as a TRAILING "T". Guard on endsWith, not
      // includes: a control value with an internal "T" is depth_temporal
      // (PTVG/TVG/TEVG), and an includes() guard would skip the append for
      // those — silently dropping temporal alignment on an extend that uses a
      // Temporal-Depth control video. endsWith adds the flag while leaving the
      // process letter intact; the display/persist/submit strips remove only
      // this trailing "T" again.
      const vptExtend = (params.video_prompt_type as string) || ''
      if (!vptExtend.endsWith('T')) {
        params.video_prompt_type = vptExtend + 'T'
      }
      // Compensate for the overlap frames the backend adds (video_length +
      // overlap - 1). Without this, a 20s request with a 20s window produces
      // 2 windows because the overlap pushes total frames past one window.
      const swDefaults = state.modelOptions?.sliding_window_defaults as Record<string, number> | undefined
      const overlap = swDefaults?.overlap_default ?? 9
      const overlapFrames = Math.max(0, overlap - 1)
      const currentFrames = (params.video_length as number) || 0
      if (currentFrames > overlapFrames) {
        params.video_length = currentFrames - overlapFrames
      }
    }

    // Safety net: Studio Video mode ALWAYS produces video. The sub-mode
    // branches above translate image_mode 2/3 (Multi-Shot/Extend) to 0 + other
    // flags, but a plain T2V gen (no start image) hits none of them — so a
    // stale non-zero image_mode (e.g. an I2V clip's settings loaded via the
    // pencil, or Extend mode left without a source video) would leak through
    // and the backend (is_image = image_mode > 0) would emit a single PNG
    // instead of a video. Force video output here, after the sub-mode branches
    // have already read image_mode.
    if (state.generationMode === 'video') {
      params.image_mode = 0
    }

    // Image references (from ImageRefSection / adaptive H3 Inputs). H3
    // semantic references do not use the legacy video_prompt_type letter
    // codes, and adaptive FL2VA selections must still upload them so the plan
    // can route the semantic segments to Ref2VA.
    const h3SemanticReferenceSubmission = (
      H3_STUDIO_MODELS.has(String(params.model_type || ''))
      && (
        params.h3_adaptive_conditioning !== false
        || params.model_type === 'minimax_h3_ref2va'
      )
    )
    if ((state.imageRefType || h3SemanticReferenceSubmission) && state.imageRefs.length > 0) {
      const refPaths: string[] = h3SemanticReferenceSubmission && Array.isArray(params.image_refs)
        ? [...(params.image_refs as string[])]
        : []
      for (const file of state.imageRefs) {
        try {
          const result = await api.uploadImage(file)
          if (!ownsSubmission()) return
          refPaths.push(result.path)
        } catch (e) {
          if (!ownsSubmission()) return
          console.error('Failed to upload reference image:', e)
        }
      }
      if (refPaths.length > 0) {
        params.image_refs = Array.from(new Set(refPaths))
        params.remove_background_images_ref = state.removeBackgroundRefs ? 1 : 0
        if (!h3SemanticReferenceSubmission) {
          // Merge legacy image-ref letter codes into video_prompt_type.
          let vpt = (params.video_prompt_type as string) || ''
          for (const letter of state.imageRefType) {
            if (!vpt.includes(letter)) vpt += letter
          }
          params.video_prompt_type = vpt
        }
      }
    } else if (params.image_refs && (params.image_refs as string[]).length > 0) {
      // Re-roll case: image_refs already populated from sidecar metadata
      params.remove_background_images_ref = params.remove_background_images_ref ?? 0
    } else {
      // No reference images attached for this submission. Strip any
      // image-ref letter codes from video_prompt_type that may have
      // persisted from an earlier task — without this, a user who
      // generates with refs once and then clears them gets stuck with
      // "I" (or other ref-letter codes) baked into the saved per-mode
      // params snapshot, which the backend rejects with "You must
      // provide at least one Reference Image". The backend has a
      // safety net that catches this too, but cleaning at the source
      // keeps the snapshot itself sensible.
      const vpt = (params.video_prompt_type as string) || ''
      if (vpt) {
        // Default ref letters used by Maestro when image refs are
        // present. If imageRefType is configured we trust that;
        // otherwise fall back to the conservative "I" — the most common
        // and the one we've actually observed leaking.
        const refLetters = state.imageRefType || 'I'
        let cleaned = vpt
        for (const letter of refLetters) {
          cleaned = cleaned.split(letter).join('')
        }
        if (cleaned !== vpt) {
          params.video_prompt_type = cleaned
        }
      }
      // Make sure no stale image_refs path list rides along either.
      if (params.image_refs !== undefined && (!params.image_refs || (params.image_refs as string[]).length === 0)) {
        delete params.image_refs
      }
    }

    // Voice reference (ID-LoRA) — upload if present, add to params
    if (state.directorVoiceRef) {
      let vrPath = state.directorVoiceRefPath
      if (!vrPath) {
        try {
          const uploaded = await api.uploadAudio(state.directorVoiceRef)
          if (!ownsSubmission()) return
          vrPath = uploaded.path
          set({ directorVoiceRefPath: vrPath })
        } catch {
          if (!ownsSubmission()) return
          /* skip */
        }
      }
      if (vrPath) {
        params.voice_reference = vrPath
        params.identity_guidance_scale = state.directorIdentityGuidanceScale
      }
    }

    const initialH3Estimate = String(params.model_type || '').startsWith('minimax_h3')
      ? state.h3CurrentEstimate
      : null
    // Uploads above can outlive a project switch. Never admit the frozen
    // request under a project that is no longer active in this browser.
    if (!ownsSubmission() || get().activeWorkspace !== submissionWorkspace) return
    params.enhance_before_generate = enhanceBeforeGenerate
    params.h3_ref2va_terms_accepted = h3Ref2VATermsAccepted()
    const durablePreparationExpected = enhanceBeforeGenerate
      || String(params.model_type || '').startsWith('minimax_h3')
    const newJob: GenerationJob = {
      id: '',
      createdAt: Date.now() / 1000,
      status: durablePreparationExpected ? 'preparing' : 'queued',
      progress: 0,
      step: 0,
      totalSteps: 0,
      phase: durablePreparationExpected
        ? enhanceBeforeGenerate ? 'enhancing_prompt' : 'planning_generation'
        : '',
      message: durablePreparationExpected
        ? enhanceBeforeGenerate ? 'Enhancing prompt' : 'Planning generation'
        : 'Submitting...',
      outputFiles: [],
      error: null,
      oomInfo: null,
      promptPreview: durablePreparationExpected ? '' : String(params.prompt || ''),
      modelType: String(params.model_type || ''),
      generationMode: state.generationMode,
      workspace: submissionWorkspace,
      h3Estimate: initialH3Estimate,
      h3SegmentPlan: null,
    }

    set(s => ({
      isGenerating: true,
      jobs: [newJob, ...s.jobs],
    }))

    try {
      applyH3SegmentCeilingPolicy(params, state.slidingWindowLocked)
      const { job_id, status, h3_estimate } = await api.submitGeneration(params)
      if (!ownsSubmission()) {
        _discardStaleGenerationPlaceholder(newJob)
        return
      }
      const submittedEstimate = h3_estimate || newJob.h3Estimate || null

      // Update the job with its server-assigned ID
      set(s => {
        const reconnectedJobExists = s.jobs.some(job => job !== newJob && job.id === job_id)
        return {
          jobs: reconnectedJobExists
            ? s.jobs.filter(job => job !== newJob)
            : s.jobs.map(job => job === newJob ? {
              ...job,
              id: job_id,
              status: status || 'queued',
              phase: status === 'preparing'
                ? enhanceBeforeGenerate ? 'enhancing_prompt' : 'planning_generation'
                : job.phase,
              message: status === 'preparing'
                ? enhanceBeforeGenerate ? 'Enhancing prompt' : 'Planning generation'
                : 'Queued...',
              h3Estimate: submittedEstimate,
              etaSeconds: _h3EstimateTotalSeconds(submittedEstimate),
            } : job),
          ...(enhanceBeforeGenerate && s.activeWorkspace === submissionWorkspace
            ? { studioPromptEnhance: false }
            : {}),
        }
      })

      // Queued cards use the shared queue snapshot for start transitions. A
      // slow per-card fallback check remains for missed events; the same poller
      // is woken and switches to 2s only once this job is truly executing.
      get()._pollRecoveredJob(job_id)
      window.dispatchEvent(new CustomEvent('maestro:queue-refresh'))
      window.dispatchEvent(new CustomEvent('maestro:downloads-refresh'))

    } catch (e) {
      if (!ownsSubmission()) {
        _discardStaleGenerationPlaceholder(newJob)
        return
      }
      const msg = e instanceof Error ? e.message : 'Generation failed'
      // Submit itself failed (pre-queue). Convert the placeholder to a failed
      // state in place so the user sees what happened, rather than making the
      // tile disappear and leaving them to wonder.
      set(s => ({
        jobs: s.jobs.map(j => j === newJob ? { ...j, id: j.id || `submit-fail-${Date.now()}`, status: 'failed', message: msg, error: msg } : j),
        isGenerating: s.jobs.some(j => j !== newJob && _isActiveGenerationJob(j)),
      }))
    }
  },

  stopGeneration: (jobId) => {
    if (jobId) {
      // Cancel specific job on backend, then remove from UI
      _recoveryJobPolls.get(jobId)?.stop()
      _rejectTerminalJobWaiter(jobId, 'Generation cancelled')
      api.cancelJob(jobId).catch(e => console.error('Cancel failed:', e))
      set(s => {
        const remaining = s.jobs.filter(j => j.id !== jobId)
        return { jobs: remaining, isGenerating: remaining.some(_isActiveGenerationJob) }
      })
    } else {
      // Cancel all jobs
      const jobs = get().jobs
      jobs.forEach(j => {
        if (j.id) {
          _recoveryJobPolls.get(j.id)?.stop()
          _rejectTerminalJobWaiter(j.id, 'Generation cancelled')
          api.cancelJob(j.id).catch(() => {})
        }
      })
      set({ jobs: [], isGenerating: false })
    }
  },

  // UI-only removal of a job tile (e.g. dismissing a failed/cancelled
  // placeholder). No backend call — the job is already terminal.
  dismissJob: (jobId) => {
    set(s => {
      const remaining = s.jobs.filter(j => j.id !== jobId)
      return {
        jobs: remaining,
        isGenerating: remaining.some(_isActiveGenerationJob),
      }
    })
  },

  reconcileQueueState: (queue) => {
    const queueJobs = new Map(queue.jobs.map(job => [job.job_id, job]))
    const previous = new Map(get().jobs.map(job => [job.id, job]))
    set(s => ({
      jobs: s.jobs.map(job => {
        const queueJob = queueJobs.get(job.id)
        return queueJob ? { ...job, ..._queueJobDetails(queueJob, job) } : job
      }),
    }))

    for (const job of get().jobs) {
      const queueJob = queueJobs.get(job.id)
      if (!queueJob) continue
      const before = previous.get(job.id)
      const becameFast = _jobNeedsFastStatusPoll(job)
        && (!before || !_jobNeedsFastStatusPoll(before))
      const recoveryChanged = before?.recoveryState !== job.recoveryState
      if (becameFast || recoveryChanged || !_recoveryJobPolls.has(job.id)) {
        get()._pollRecoveredJob(job.id)
      }
    }
  },

  resumeJobRecovery: async (jobId) => {
    const accountIdentityEpoch = _accountIdentityEpoch
    const needsRecoveryPoll = !get().jobs.some(job => job.id === jobId && _isActiveGenerationJob(job))
    let recoveryError: unknown = null
    try {
      await api.resumeQueueRecovery(jobId)
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return
    } catch (error) {
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return
      recoveryError = error
    }
    try {
      const status = await api.fetchJobStatus(jobId)
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return
      set(s => ({
        jobs: s.jobs.map(job => job.id !== jobId ? job : _mergeJobStatus(job, status)),
        isGenerating: ACTIVE_GENERATION_JOB_STATUSES.has(status.status)
          || s.jobs.some(job => job.id !== jobId && _isActiveGenerationJob(job)),
      }))
    } catch {
      // Preserve the bounded recovery endpoint error below.
    }
    if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return
    if (recoveryError) throw recoveryError
    if (needsRecoveryPoll) get()._pollRecoveredJob(jobId)
    await get().reconnectJobs(accountIdentityEpoch)
  },

  retryJobRecovery: async (jobId) => {
    const accountIdentityEpoch = _accountIdentityEpoch
    const needsRecoveryPoll = !get().jobs.some(job => job.id === jobId && _isActiveGenerationJob(job))
    let recoveryError: unknown = null
    try {
      await api.retryQueueRecovery(jobId)
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return
    } catch (error) {
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return
      recoveryError = error
    }
    try {
      const status = await api.fetchJobStatus(jobId)
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return
      set(s => ({
        jobs: s.jobs.map(job => job.id !== jobId ? job : _mergeJobStatus(job, status)),
        isGenerating: ACTIVE_GENERATION_JOB_STATUSES.has(status.status)
          || s.jobs.some(job => job.id !== jobId && _isActiveGenerationJob(job)),
      }))
    } catch {
      // Preserve the bounded recovery endpoint error below.
    }
    if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return
    if (recoveryError) throw recoveryError
    if (needsRecoveryPoll) get()._pollRecoveredJob(jobId)
    await get().reconnectJobs(accountIdentityEpoch)
  },

  _pollRecoveredJob: (jobId) => {
    const accountIdentityEpoch = _accountIdentityEpoch
    const existing = _recoveryJobPolls.get(jobId)
    if (existing) {
      existing.wake()
      return
    }
    const initialJob = get().jobs.find(job => job.id === jobId)
    if (!initialJob) return

    let consecutivePollFailures = 0
    let running = false
    let pendingWake = false
    let stopped = false
    const outputRefresh = _createActiveOutputRefreshTracker(initialJob)
    const poll: ActiveJobPoll = {
      timer: null,
      wake: () => {},
      stop: () => {},
    }

    const stop = () => {
      if (stopped) return
      stopped = true
      if (poll.timer !== null) window.clearTimeout(poll.timer)
      poll.timer = null
      document.removeEventListener('visibilitychange', onVisibilityChange)
      if (_recoveryJobPolls.get(jobId) === poll) {
        _recoveryJobPolls.delete(jobId)
      }
    }

    const scheduleNext = () => {
      if (stopped) return
      if (poll.timer !== null) window.clearTimeout(poll.timer)
      const current = get().jobs.find(job => job.id === jobId)
      if (!current) {
        stop()
        return
      }
      const delay = _jobNeedsFastStatusPoll(current)
        ? ACTIVE_JOB_STATUS_POLL_MS
        : QUEUED_JOB_STATUS_SAFETY_MS
      poll.timer = window.setTimeout(() => {
        poll.timer = null
        void tick(true)
      }, delay)
    }

    const tick = async (queuedSafety = false) => {
      if (stopped) return
      if (running) {
        pendingWake = true
        return
      }
      const current = get().jobs.find(job => job.id === jobId)
      if (!current) {
        stop()
        return
      }
      if (!_jobNeedsFastStatusPoll(current) && !queuedSafety) {
        scheduleNext()
        return
      }

      running = true
      try {
        const status = await api.fetchJobStatus(jobId)
        if (
          stopped
          || !_accountIdentityIsCurrent(accountIdentityEpoch)
          || _recoveryJobPolls.get(jobId) !== poll
        ) {
          stop()
          return
        }
        consecutivePollFailures = 0
        set(s => ({
          jobs: s.jobs.map(job => job.id !== jobId ? job : _mergeJobStatus(job, status)),
        }))
        _publishTerminalJobStatus(status)
        if (_activeOutputRefreshDue(outputRefresh, status, !document.hidden)) {
          void get().refreshOutputs()
        }
        if (status.status === 'completed') {
          stop()
          set(s => {
            const remaining = s.jobs.filter(job => job.id !== jobId)
            return {
              jobs: remaining,
              isGenerating: remaining.some(_isActiveGenerationJob),
            }
          })
          get().loadOutputs()
        } else if (status.status === 'failed' || status.status === 'cancelled') {
          // Terminal failures stay visible so their error/recovery controls
          // remain actionable; only the poller and global generating state stop.
          stop()
          set(s => ({
            isGenerating: s.jobs.some(job => (
              job.id !== jobId && _isActiveGenerationJob(job)
            )),
          }))
          get().loadOutputs()
        }
      } catch {
        if (stopped || _recoveryJobPolls.get(jobId) !== poll) {
          stop()
          return
        }
        // A transient disconnect must not strand the only poller for an
        // existing recovered card. Keep retrying; periodically reconcile the
        // owner list as an independent authoritative path.
        if (!get().jobs.some(job => job.id === jobId)) {
          stop()
          return
        }
        consecutivePollFailures += 1
        if (consecutivePollFailures >= 3) {
          consecutivePollFailures = 0
          void get().reconnectJobs(accountIdentityEpoch)
        }
      } finally {
        running = false
        if (!stopped) {
          if (pendingWake) {
            pendingWake = false
            poll.wake()
          } else {
            scheduleNext()
          }
        }
      }
    }

    const wake = () => {
      if (stopped) return
      if (poll.timer !== null) window.clearTimeout(poll.timer)
      poll.timer = null
      if (running) {
        pendingWake = true
        return
      }
      void tick(false)
    }

    function onVisibilityChange() {
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) {
        stop()
        return
      }
      if (document.hidden) return
      const current = get().jobs.find(job => job.id === jobId)
      if (!current || !_jobNeedsFastStatusPoll(current)) return
      outputRefresh.lastRefreshAt = Date.now()
      outputRefresh.pendingDelta = false
      outputRefresh.hasRefreshed = true
      void get().refreshOutputs()
      wake()
    }

    poll.wake = wake
    poll.stop = stop
    _recoveryJobPolls.set(jobId, poll)
    document.addEventListener('visibilitychange', onVisibilityChange)
    if (_jobNeedsFastStatusPoll(initialJob)) wake()
    else void tick(true)
  },

  reconnectJobs: async (accountIdentityEpoch = _accountIdentityEpoch) => {
    if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return
    // On page load, check backend for any active jobs and restore them
    try {
      const data = await api.fetchActiveJobs()
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return
      const ordinaryStatuses = data.jobs.filter(status => (
        !_sampleCampaignKnownJobIds.has(status.job_id)
      ))
      for (const jobId of _sampleCampaignKnownJobIds) _recoveryJobPolls.get(jobId)?.stop()
      if (data.jobs.length > 0 || _sampleCampaignKnownJobIds.size > 0) {
        const serverJobs = new Map(ordinaryStatuses.map(job => [job.job_id, job]))
        set(s => {
          const jobs = s.jobs.filter(job => !_sampleCampaignKnownJobIds.has(job.id)).map(job => {
            const status = serverJobs.get(job.id)
            return status ? _mergeJobStatus(job, status) : job
          })
          return { jobs, isGenerating: jobs.some(_isActiveGenerationJob) }
        })
        const existingIds = new Set(get().jobs.map(j => j.id))
        const newJobs: GenerationJob[] = ordinaryStatuses
          .filter(j => !existingIds.has(j.job_id))
          .map(_newGenerationJobFromStatus)
        if (newJobs.length > 0) {
          set(s => ({
            jobs: [...s.jobs, ...newJobs],
            isGenerating: newJobs.some(_isActiveGenerationJob)
              || s.jobs.some(_isActiveGenerationJob),
          }))
          console.log(`[Queue] Reconnected to ${newJobs.length} active job(s)`)
        }
        // Queue snapshots keep ordinary queued cards current; preparation,
        // review, execution, and retry states use the fast per-card poller.
        for (const status of ordinaryStatuses) {
          if (ACTIVE_GENERATION_JOB_STATUSES.has(status.status)) {
            get()._pollRecoveredJob(status.job_id)
          }
        }
        if (ordinaryStatuses.some(status => (
          status.status === 'completed'
          || status.status === 'failed'
          || status.status === 'cancelled'
        ))) {
          void get().loadOutputs()
        }
      }
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return
      await get().reconnectDirectorPreparation()
    } catch {
      // Backend might not have the endpoint yet, silently ignore
    }
  },

  // LoRA state
  availableLoras: [],
  lorasLoading: false,
  loraWeights: {},
  loraIdByFilename: {},
  filenameByLoraId: {},

  /**
   * Refresh the lora_id ↔ filename maps from /api/v1/loras/installed.
   * Called once at boot (from loadModels) and again whenever LoRAs may
   * have been added/removed (after CivitAI download, scan, etc.).
   *
   * Side effect: runs reconciliation against the persisted savedLoraPerMode.
   * If a saved filename no longer exists on disk but the snapshot lora_id
   * resolves to a different filename in the fresh map, the rename is
   * applied transparently — that's the LoRA-version-update flow.
   */
  refreshLoraIdMap: async () => {
    try {
      const { loras } = await api.fetchInstalledLoras()
      const byFilename: Record<string, string> = {}
      const byLoraId: Record<string, string> = {}
      for (const l of loras) {
        if (!l.lora_id || !l.filename) continue
        byFilename[l.filename] = l.lora_id
        // If two files share a lora_id (rare — user kept v1 + v2 side by
        // side), the last one wins. Reconciliation will prefer whichever
        // matches the saved filename.
        byLoraId[l.lora_id] = l.filename
      }
      // Reconcile: rewrite stale filenames in savedLoraPerMode using the
      // snapshot loaded from localStorage (lora_id → filename-at-save-time).
      const s = get()
      const snapshot = s._loraFilenameSnapshotAtLoad || {}
      const reconciled: typeof s.savedLoraPerMode = {}
      let changed = false
      for (const [mode, blob] of Object.entries(s.savedLoraPerMode)) {
        if (!blob) continue
        const renameFilename = (fname: string): string | null => {
          if (byFilename[fname]) return fname  // still on disk, no change
          // Stale: look up its lora_id in snapshot, then current filename in fresh map.
          // Walk snapshot backwards (lora_id → fname) to find the lora_id this filename had.
          let foundId: string | null = null
          for (const [id, snapFname] of Object.entries(snapshot)) {
            if (snapFname === fname) { foundId = id; break }
          }
          if (foundId && byLoraId[foundId]) {
            changed = true
            return byLoraId[foundId]  // renamed
          }
          // LoRA was deleted entirely.
          changed = true
          return null
        }
        const newActivated = (blob.activated_loras || [])
          .map(renameFilename)
          .filter((x): x is string => x !== null)
        const newWeights: Record<string, number[]> = {}
        for (const [fname, w] of Object.entries(blob.loraWeights || {})) {
          const renamed = renameFilename(fname)
          if (renamed) newWeights[renamed] = w
        }
        const newAvailable = (blob.availableLoras || [])
          .map(renameFilename)
          .filter((x): x is string => x !== null)
        reconciled[mode as GenerationMode] = {
          ...blob,
          activated_loras: newActivated,
          loraWeights: newWeights,
          availableLoras: newAvailable,
        }
      }
      if (changed) {
        // Also rewrite the in-memory runtime state if its keys are stale
        const renameRuntimeFilename = (fname: string): string | null => {
          if (byFilename[fname]) return fname
          let foundId: string | null = null
          for (const [id, snapFname] of Object.entries(snapshot)) {
            if (snapFname === fname) { foundId = id; break }
          }
          if (foundId && byLoraId[foundId]) return byLoraId[foundId]
          return null
        }
        const curActivated = (s.params.activated_loras || [])
          .map(renameRuntimeFilename)
          .filter((x): x is string => x !== null)
        const curWeights: Record<string, number[]> = {}
        for (const [fname, w] of Object.entries(s.loraWeights || {})) {
          const renamed = renameRuntimeFilename(fname)
          if (renamed) curWeights[renamed] = w
        }
        set(state => ({
          loraIdByFilename: byFilename,
          filenameByLoraId: byLoraId,
          savedLoraPerMode: reconciled,
          params: { ...state.params, activated_loras: curActivated },
          loraWeights: curWeights,
        }))
        // Persist the reconciled state so next boot doesn't need to redo it.
        const ns = get()
        _saveSettings({
          generationMode: ns.generationMode,
          selectedModelPerMode: ns.selectedModelPerMode,
          savedParamsPerMode: ns.savedParamsPerMode,
          savedLoraPerMode: ns.savedLoraPerMode,
          savedPromptPerMode: ns.savedPromptPerMode,
        }, byFilename)
      } else {
        set({ loraIdByFilename: byFilename, filenameByLoraId: byLoraId })
      }
      // Fire-and-forget: kick off an update check, debounced server-side
      // by a 24h staleness window. If the manifest is fresh, the backend
      // returns immediately without hitting CivitAI; if stale, it walks
      // the library and refreshes badges in the background. The user's
      // current LoraSelector instance will pick up new badges on its
      // next /details fetch (mode change or refresh).
      api.checkLoraUpdates(false).catch(() => {
        // Network failures here are non-fatal — the manual "Check" button
        // in the LoraSelector remains available for retries.
      })
    } catch {
      // Non-fatal. Persistence will keep using filename-keyed legacy shape
      // until the map populates on a subsequent attempt.
    }
  },

  loadLoras: async (modelType) => {
    const seq = ++_loraLoadSeq
    set({ lorasLoading: true })
    try {
      const data = await api.fetchLoras(modelType)
      if (seq !== _loraLoadSeq) return
      if (get().params.model_type !== modelType) {
        set({ lorasLoading: false })
        return
      }
      set({ availableLoras: data.loras, lorasLoading: false })
    } catch {
      if (seq !== _loraLoadSeq) return
      if (get().params.model_type !== modelType) {
        set({ lorasLoading: false })
        return
      }
      set({ availableLoras: [], lorasLoading: false })
    }
  },

  toggleLora: (filename) => {
    ++_h3ProfileApplySeq
    ++_modelDefaultsSeq
    const { params, loraWeights, modelOptions, generationMode, editSubMode } = get()
    const current = [...params.activated_loras]
    const idx = current.indexOf(filename)
    const newWeights = { ...loraWeights }
    // SCAIL-2 Recast is intentionally a single-phase pipeline even though
    // the shared Wan model family advertises support for up to three phases.
    const recastSinglePhase = generationMode === 'avatar' && editSubMode === 'recast'
    const phases = recastSinglePhase ? 1 : Math.max(1, modelOptions?.guidance_max_phases ?? 1)

    if (idx >= 0) {
      current.splice(idx, 1)
      delete newWeights[filename]
    } else {
      current.push(filename)
      newWeights[filename] = Array(phases).fill(1.0)
    }

    // Serialize multipliers
    const multipliers = current.map(name => {
      const w = newWeights[name] || [1.0]
      return Array.from(
        { length: phases },
        (_, i) => w[i] ?? w[w.length - 1] ?? 1.0,
      ).map(v => v.toFixed(2)).join(';')
    }).join(' ')

    set(s => ({
      loraWeights: newWeights,
      h3SelectedProfile: 'custom',
      h3ProfileApplying: null,
      params: {
        ...s.params,
        activated_loras: current,
        loras_multipliers: multipliers,
      },
    }))
    // Persist LoRA state
    const s = get()
    const mode = s.generationMode
    const updatedLoraPerMode = {
      ...s.savedLoraPerMode,
      [mode]: { activated_loras: current, loras_multipliers: multipliers, loraWeights: newWeights, availableLoras: s.availableLoras },
    }
    set({ savedLoraPerMode: updatedLoraPerMode })
    _saveSettings({ generationMode: mode, selectedModelPerMode: s.selectedModelPerMode, savedParamsPerMode: s.savedParamsPerMode, savedLoraPerMode: updatedLoraPerMode, savedPromptPerMode: s.savedPromptPerMode }, s.loraIdByFilename)
  },

  ensureTransitionLoraForBlend: async () => {
    const state = get()
    const modelType = state.params.model_type as string
    // Only applies to LTX-2 family models — the LoRA is trained for LTX-2.3
    if (!modelType || !modelType.startsWith('ltx2')) return

    const HF_URL = 'https://huggingface.co/valiantcat/LTX-2.3-Transition-LORA'
    const matchesTransitionLora = (name: string) => /transition/i.test(name)

    try {
      // Step 1: check if already installed
      let { loras } = await api.fetchLoras(modelType)
      let transitionFilename = loras.find(matchesTransitionLora)

      // Step 2: if not installed, trigger HF download
      if (!transitionFilename) {
        console.log('[Blend] Transition LoRA not found locally — downloading from HuggingFace')
        let result: { filename: string } | null = null
        try {
          result = await api.importHuggingFaceLora(HF_URL)
        } catch (e) {
          console.error('[Blend] Transition LoRA download request failed:', e)
          return
        }
        // Poll the LoRA list until the new file appears (download runs in
        // a backend thread). Cap at ~3 min total.
        const expectedFilename = result?.filename
        for (let i = 0; i < 90; i++) {
          await new Promise(r => setTimeout(r, 2000))
          const refreshed = await api.fetchLoras(modelType)
          loras = refreshed.loras
          const found = expectedFilename
            ? loras.find(l => l === expectedFilename || matchesTransitionLora(l))
            : loras.find(matchesTransitionLora)
          if (found) { transitionFilename = found; break }
        }
        if (!transitionFilename) {
          console.warn('[Blend] Transition LoRA download did not complete in time — skipping auto-activation')
          return
        }
        console.log(`[Blend] Transition LoRA ready: ${transitionFilename}`)
        // Refresh the in-store available LoRA list so the UI shows the new file
        try { await get().loadLoras(modelType) } catch { /* non-fatal */ }
      }

      // Step 3: ensure it's in activated_loras (but don't toggle-off if it
      // happens to already be there)
      const activated = (get().params.activated_loras as string[]) || []
      if (!activated.includes(transitionFilename)) {
        get().toggleLora(transitionFilename)
        console.log(`[Blend] Auto-activated transition LoRA: ${transitionFilename}`)
      }
    } catch (e) {
      console.error('[Blend] ensureTransitionLoraForBlend failed:', e)
    }
  },

  ensureEditAnythingLora: async () => {
    const state = get()
    const modelType = state.params.model_type as string
    if (!modelType || !modelType.startsWith('ltx2')) return

    const HF_URL = 'https://huggingface.co/Alissonerdx/LTX-LoRAs'
    // Must match EDIT_ANYTHING_LORA_FILENAME in app/launch.py. The endpoint
    // will activate this server-side regardless of the client's LoRA list,
    // so we only need to ensure the file is present on disk before the
    // user hits Generate.
    const EDIT_ANYTHING_FILENAME =
      'ltx23_edit_anything_global_rank128_v1_9000steps_adamw.safetensors'
    const matchesEditAnything = (name: string) =>
      name === EDIT_ANYTHING_FILENAME ||
      /edit_anything.*9000steps/i.test(name)

    try {
      const { loras } = await api.fetchLoras(modelType)
      const already = loras.find(matchesEditAnything)
      if (already) return

      console.log('[EditAnything] LoRA not found locally — downloading from HuggingFace')
      try {
        await api.importHuggingFaceLora(HF_URL, undefined, EDIT_ANYTHING_FILENAME)
      } catch (e) {
        console.error('[EditAnything] LoRA download request failed:', e)
        return
      }
      // Poll every 2s until the file appears (up to ~3 min)
      for (let i = 0; i < 90; i++) {
        await new Promise(r => setTimeout(r, 2000))
        const refreshed = await api.fetchLoras(modelType)
        if (refreshed.loras.find(matchesEditAnything)) {
          console.log(`[EditAnything] LoRA ready: ${EDIT_ANYTHING_FILENAME}`)
          try { await get().loadLoras(modelType) } catch { /* non-fatal */ }
          return
        }
      }
      console.warn('[EditAnything] LoRA download did not complete in time')
    } catch (e) {
      console.error('[EditAnything] ensureEditAnythingLora failed:', e)
    }
  },

  setLoraWeight: (filename, phaseIndex, value) => {
    ++_h3ProfileApplySeq
    ++_modelDefaultsSeq
    const { params, loraWeights, modelOptions, generationMode, editSubMode } = get()
    const newWeights = { ...loraWeights }
    if (!newWeights[filename]) return
    const recastSinglePhase = generationMode === 'avatar' && editSubMode === 'recast'
    const phases = recastSinglePhase ? 1 : Math.max(1, modelOptions?.guidance_max_phases ?? 1)
    if (phaseIndex < 0 || phaseIndex >= phases) return
    const currentWeights = newWeights[filename]
    newWeights[filename] = Array.from(
      { length: phases },
      (_, i) => currentWeights[i] ?? currentWeights[currentWeights.length - 1] ?? 1.0,
    )
    newWeights[filename][phaseIndex] = value

    // Reserialize
    const multipliers = params.activated_loras.map(name => {
      const w = newWeights[name] || [1.0]
      return Array.from(
        { length: phases },
        (_, i) => w[i] ?? w[w.length - 1] ?? 1.0,
      ).map(v => v.toFixed(2)).join(';')
    }).join(' ')

    set(s => ({
      loraWeights: newWeights,
      params: { ...s.params, loras_multipliers: multipliers },
      h3SelectedProfile: 'custom',
      h3ProfileApplying: null,
    }))
    // Persist LoRA state
    const s = get()
    const mode = s.generationMode
    const updatedLoraPerMode = {
      ...s.savedLoraPerMode,
      [mode]: { activated_loras: s.params.activated_loras, loras_multipliers: multipliers, loraWeights: newWeights, availableLoras: s.availableLoras },
    }
    set({ savedLoraPerMode: updatedLoraPerMode })
    _saveSettings({ generationMode: mode, selectedModelPerMode: s.selectedModelPerMode, savedParamsPerMode: s.savedParamsPerMode, savedLoraPerMode: updatedLoraPerMode, savedPromptPerMode: s.savedPromptPerMode }, s.loraIdByFilename)
  },

  // Presets
  presets: [],
  presetsLoading: false,

  loadPresets: async () => {
    set({ presetsLoading: true })
    try {
      const { presets } = await api.fetchPresets()
      set({ presets })
    } catch (e) {
      console.error('Failed to load presets:', e)
    } finally {
      set({ presetsLoading: false })
    }
  },

  savePreset: async (name) => {
    const { params, loraWeights, generationMode } = get()
    try {
      const preset = await api.createPreset({
        name,
        mode: generationMode,
        model_type: params.model_type,
        prompt: '',
        activated_loras: params.activated_loras,
        loras_multipliers: params.loras_multipliers,
        lora_weights: loraWeights,
        params: {
          num_inference_steps: params.num_inference_steps,
          guidance_scale: params.guidance_scale,
          resolution: params.resolution,
          seed: params.seed,
          negative_prompt: params.negative_prompt,
          flow_shift: params.flow_shift,
          self_refiner_setting: params.self_refiner_setting,
          stage2_steps: params.stage2_steps,
          tea_cache: params.tea_cache,
          custom_settings: _restorableH3CustomSettings(params.custom_settings),
        },
      })
      set(s => ({ presets: [...s.presets, preset] }))
    } catch (e) {
      console.error('Failed to save preset:', e)
    }
  },

  loadPreset: (preset) => {
    ++_h3ProfileApplySeq
    ++_h3CompatibilitySeq
    ++_modelDefaultsSeq
    const newParams: Partial<GenerateParams> = {
      model_type: preset.model_type,
      activated_loras: preset.activated_loras,
      loras_multipliers: preset.loras_multipliers,
      ...(preset.params as Partial<GenerateParams>),
    }
    if (preset.model_type.startsWith('minimax_h3')) {
      const restored = _restorableH3CustomSettings(newParams.custom_settings)
      const engine = _normalizeH3AttentionEngine(restored.h3_attention_engine)
      newParams.custom_settings = { h3_attention_engine: engine, ...restored }
      try {
        localStorage.setItem(H3_ATTENTION_ENGINE_KEY, engine)
      } catch {
        // The active generation still receives the preset when storage is unavailable.
      }
    }
    set(s => ({
      params: { ...s.params, ...newParams },
      selectedModelPerMode: {
        ...s.selectedModelPerMode,
        [s.generationMode]: preset.model_type,
      },
      loraWeights: preset.lora_weights || {},
      h3SelectedProfile: 'custom',
      h3ProfileApplying: null,
    }))
    if (H3_STUDIO_MODELS.has(preset.model_type)) {
      void get().normalizeH3EditableProfile()
    }
  },

  deletePreset: async (id) => {
    try {
      await api.deletePreset(id)
      set(s => ({ presets: s.presets.filter(p => p.id !== id) }))
    } catch (e) {
      console.error('Failed to delete preset:', e)
    }
  },

  // Model options
  modelOptions: null,
  modelOptionsLoading: false,
  h3PerformanceProfiles: [],
  h3CurrentEstimate: null,
  h3SegmentCountEstimate: null,
  h3EstimateLoading: false,
  h3EstimateError: null,
  h3SelectedProfile: 'high',
  h3ProfileApplying: null,
  h3ModelProfileCompatibility: {},

  invalidateH3PerformanceEstimates: () => {
    ++_h3EstimateSeq
    set({
      h3SegmentCountEstimate: null,
      h3EstimateLoading: true,
      h3EstimateError: null,
    })
  },

  refreshH3PerformanceEstimates: async () => {
    const seq = ++_h3EstimateSeq
    const state = get()
    const isH3 = state.generationMode === 'video' && (
      state.params.model_type.startsWith('minimax_h3')
      || String(state.modelOptions?.architecture || '').startsWith('minimax_h3')
    )
    if (!isH3) return
    set({ h3EstimateLoading: true, h3EstimateError: null })
    try {
      const response = await api.estimateH3Performance(_buildH3EstimateRequest(state))
      if (seq !== _h3EstimateSeq) return
      set({
        h3PerformanceProfiles: response.profiles,
        h3CurrentEstimate: response.current.estimate,
        h3SegmentCountEstimate: response.segment_count_estimate,
        h3EstimateLoading: false,
        h3EstimateError: null,
      })
    } catch (error) {
      if (seq !== _h3EstimateSeq) return
      set({
        h3PerformanceProfiles: [],
        h3CurrentEstimate: null,
        h3SegmentCountEstimate: null,
        h3EstimateLoading: false,
        h3EstimateError: error instanceof Error ? error.message : 'Could not estimate H3 performance',
        h3SelectedProfile: 'custom',
      })
    }
  },

  refreshH3ModelProfileCompatibility: async (modelType) => {
    const state = get()
    const requestedProfileId = state.h3SelectedProfile
    if (
      requestedProfileId === 'custom'
      || !H3_STUDIO_MODELS.has(state.params.model_type)
      || !H3_STUDIO_MODELS.has(modelType)
    ) return
    const seq = ++_h3CompatibilitySeq
    set(current => ({
      h3ModelProfileCompatibility: {
        ...current.h3ModelProfileCompatibility,
        [modelType]: {
          requestedProfileId,
          compatible: false,
          fallbackProfileId: null,
          fallbackProfileLabel: null,
          reason: null,
          loading: true,
        },
      },
    }))
    try {
      const request = _buildH3EstimateRequest(state, modelType)
      const requestSignature = _stableJson(request)
      const response = await api.estimateH3Performance(request)
      if (seq !== _h3CompatibilitySeq || get().h3SelectedProfile !== requestedProfileId) return
      if (requestSignature !== _stableJson(_buildH3EstimateRequest(get(), modelType))) {
        void get().refreshH3ModelProfileCompatibility(modelType)
        return
      }
      const requested = response.profiles.find(profile => profile.id === requestedProfileId)
      if (!requested) return
      const fallback = requested.fallback_profile_id
        ? response.profiles.find(profile => profile.id === requested.fallback_profile_id)
        : undefined
      set(current => ({
        h3ModelProfileCompatibility: {
          ...current.h3ModelProfileCompatibility,
          [modelType]: {
            requestedProfileId,
            compatible: requested.available,
            fallbackProfileId: requested.fallback_profile_id,
            fallbackProfileLabel: fallback?.label || null,
            reason: requested.fallback_reason,
            loading: false,
          },
        },
      }))
    } catch {
      if (seq !== _h3CompatibilitySeq) return
      set(current => ({
        h3ModelProfileCompatibility: {
          ...current.h3ModelProfileCompatibility,
          [modelType]: undefined,
        },
      }))
    }
  },

  normalizeH3EditableProfile: async () => {
    const state = get()
    if (state.generationMode !== 'video' || !H3_STUDIO_MODELS.has(state.params.model_type)) return false
    const seq = ++_h3ProfileApplySeq
    ++_h3EstimateSeq
    ++_h3CompatibilitySeq
    try {
      const request = _buildH3EstimateRequest(state)
      const requestSignature = _stableJson(request)
      const response = await api.estimateH3Performance(request)
      if (seq !== _h3ProfileApplySeq) return false
      if (requestSignature !== _stableJson(_buildH3EstimateRequest(get()))) {
        return get().normalizeH3EditableProfile()
      }
      const requested = response.profiles.find(profile => (
        h3ProfileMatches(profile, state.params, state.loraWeights, state.spatialUpsampling)
      ))
      if (!requested) return false
      if (requested.available) {
        set({ h3PerformanceProfiles: response.profiles })
        ++_modelOptionsSeq
        ++_modelDefaultsSeq
        return _applyH3ServerProfile(requested, requested.id, seq, get, set)
      }
      const fallback = requested.fallback_profile_id
        ? response.profiles.find(profile => (
            profile.id === requested.fallback_profile_id && profile.available
          ))
        : undefined
      if (!fallback) {
        set({ h3EstimateError: requested.fallback_reason || 'This restored H3 profile is no longer compatible.' })
        return false
      }
      ++_modelOptionsSeq
      ++_modelDefaultsSeq
      return _applyH3ServerProfile(fallback, fallback.id, seq, get, set)
    } catch (error) {
      if (seq === _h3ProfileApplySeq) {
        set({ h3EstimateError: error instanceof Error ? error.message : 'Could not normalize restored H3 settings' })
      }
      return false
    }
  },

  applyH3PerformanceProfile: async (id) => {
    const profile = get().h3PerformanceProfiles.find(item => item.id === id)
    if (!profile || !profile.available) {
      set({
        h3EstimateError: profile?.fallback_reason || 'This H3 performance profile is not available.',
      })
      return
    }
    const seq = ++_h3ProfileApplySeq
    ++_h3EstimateSeq
    ++_h3CompatibilitySeq
    ++_modelOptionsSeq
    ++_modelDefaultsSeq
    await _applyH3ServerProfile(profile, id, seq, get, set)
  },

  loadModelOptions: async (modelType) => {
    const seq = ++_modelOptionsSeq
    const defaultsSeq = _modelDefaultsSeq
    set({ modelOptionsLoading: true })
    try {
      const options = await api.fetchModelOptions(modelType)
      // Staleness guard: a newer loadModelOptions call was issued while this
      // fetch was in flight (rapid model switching, or a settings restore
      // that jumped models). Applying a superseded response would clobber
      // params (default steps/guidance) and modelOptions with the WRONG
      // model's values — last requested wins.
      if (seq !== _modelOptionsSeq) return
      const { durationSeconds, slidingWindowSeconds, slidingWindowLocked } = get()
      const fps = options.fps || 16
      // Set overlap from model defaults
      const swDefaults = (options as unknown as Record<string, unknown>).sliding_window_defaults as Record<string, number> | undefined
      const requestedDurationFrames = Math.round(durationSeconds * fps)
      const effectiveDurationFrames = alignStudioTotalFrames(requestedDurationFrames, options)
      const effectiveDurationSeconds = Math.round((effectiveDurationFrames / fps) * 1000) / 1000
      const latent = Math.max(1, Math.trunc(options.latent_size || options.frames_steps || 4))
      const segmented = usesStudioSegments(options)
      const supportsWindowPlanning = options.sliding_window || segmented
      const requestedWindowFrames = slidingWindowLocked
        ? Math.round(slidingWindowSeconds * fps)
        : Math.trunc(
            swDefaults?.window_default
            ?? options.default_sliding_window_size
            ?? (segmented ? options.frames_maximum : undefined)
            ?? Math.round(slidingWindowSeconds * fps),
          )
      const windowMinimum = Math.max(1, Math.trunc(swDefaults?.window_min || (segmented ? options.frames_minimum : 1) || 1))
      const windowMaximum = Math.max(windowMinimum, Math.trunc(swDefaults?.window_max || (segmented ? options.frames_maximum : requestedWindowFrames) || requestedWindowFrames))
      const clampedWindowFrames = Math.min(windowMaximum, Math.max(windowMinimum, requestedWindowFrames))
      const effectiveWindowFrames = segmented
        ? alignTotalFrames(clampedWindowFrames, options)
        : Math.floor((clampedWindowFrames - 1) / latent) * latent + 1
      const effectiveWindowSeconds = Math.round((effectiveWindowFrames / fps) * 1000) / 1000
      const discardDefault = swDefaults?.discard_last_frames ?? 0
      const safeOverlapMax = Math.max(0, effectiveWindowFrames - discardDefault - latent)
      const overlapDefault = options.sliding_window ? Math.max(
        Math.min(swDefaults?.overlap_min ?? 0, safeOverlapMax),
        Math.min(
          swDefaults?.overlap_default ?? 5,
          swDefaults?.overlap_max ?? safeOverlapMax,
          safeOverlapMax,
        ),
      ) : 0
      const paramUpdates: Record<string, unknown> = {
        guidance_phases: options.guidance_max_phases,
        video_length: effectiveDurationFrames,
        sliding_window_size: supportsWindowPlanning ? effectiveWindowFrames : undefined,
        sliding_window_overlap: overlapDefault,
        sliding_window_discard_last_frames: discardDefault,
      }
      // Apply model defaults for inference steps and guidance scale
      if (defaultsSeq === _modelDefaultsSeq && options.default_num_inference_steps != null) {
        paramUpdates.num_inference_steps = options.default_num_inference_steps
      }
      if (defaultsSeq === _modelDefaultsSeq && options.default_guidance_scale != null) {
        paramUpdates.guidance_scale = options.default_guidance_scale
      }
      // TTS default duration. Prefer the model's declared `default` (DramaBox
      // uses 0 = auto-derive from prompt); fall back to `max` (legacy behavior
      // for older TTS models that didn't declare a default), then 600.
      const ttsDefaults: Record<string, unknown> = {}
      if (options.audio_only && options.duration_slider) {
        const ds = options.duration_slider
        ttsDefaults.durationSeconds = ds.default ?? ds.max ?? 600
      }
      // Clamp current voice count to the new model's max_voice_count (e.g.
      // user had 5 voices on Kugel, switches to Scenema which caps at 2 —
      // trim slots 3-5 so the UI doesn't show ghost voices that the backend
      // would silently ignore).
      const newMaxVoiceCount = ((options as { max_voice_count?: number }).max_voice_count) ?? 6
      const currentVoiceCount = get().ttsVoiceCount
      if (currentVoiceCount > newMaxVoiceCount) {
        const trimmedVoices = get().ttsVoices.slice(0, newMaxVoiceCount)
        ttsDefaults.ttsVoiceCount = newMaxVoiceCount
        ttsDefaults.ttsVoices = trimmedVoices
        // Re-derive audio_prompt_type from the clamped count using the new
        // model's selection list.
        const selection = (options.audio_prompt_type_sources?.selection as string[] | undefined) || ['', 'A', 'AB']
        const audioType = selection[Math.min(newMaxVoiceCount, selection.length - 1)]
        paramUpdates.audio_prompt_type = audioType
      }
      // H3 model selection must not normalize away the opposite conditioning
      // class. Adaptive Studio deliberately combines FL2VA edge anchors with
      // Ref2VA semantic references and lets the reviewed segment plan route
      // them. With adaptive routing off, startGeneration rejects an
      // incompatible fixed checkpoint explicitly instead of silently deleting
      // the user's inputs here.
      set(s => ({
        ...ttsDefaults,
        modelOptions: options,
        modelOptionsLoading: false,
        ...(!options.audio_only ? { durationSeconds: effectiveDurationSeconds } : {}),
        ...(supportsWindowPlanning ? { slidingWindowSeconds: effectiveWindowSeconds } : {}),
        slidingWindowOverlap: overlapDefault,
        slidingWindowLocked: supportsWindowPlanning ? slidingWindowLocked : false,
        params: {
          ...s.params,
          ...paramUpdates,
        },
      }))
    } catch {
      // Same staleness rule as the success path — a superseded request's
      // failure must not null out the newer request's options.
      if (seq === _modelOptionsSeq) {
        set({ modelOptions: null, modelOptionsLoading: false })
      }
    }
  },

  // System config
  accessContext: null,
  loadAccessContext: async (refreshProjectsOnIdentityChange = true) => {
    const requestSequence = ++_accessContextRequestSequence
    const accountProjectionSequence = _accountContextRequestSequence
    const context = await api.fetchAccessContext()
    if (requestSequence !== _accessContextRequestSequence) return context
    const previous = get().accountContext
    const accountProjectionCurrent = accountProjectionSequence === _accountContextRequestSequence
    const next = accountProjectionCurrent ? context.accounts ?? null : previous
    const projectedAccessContext = accountProjectionCurrent
      ? context
      : { ...context, accounts: next ?? undefined }
    const accountIdentityChanged = _accountIdentity(previous) !== _accountIdentity(next)
    if (accountIdentityChanged) _advanceAccountIdentityEpoch()
    const projectUiScrub = accountIdentityChanged ? _scrubAccountBoundProjectUi(get()) : {}
    const supportIdentityChanged = previous?.account?.id !== next?.account?.id
      || previous?.capabilities.includes('account.self') !== next?.capabilities.includes('account.self')
    const supportAdminUnavailable = next?.authenticated !== true
      || next.account?.role !== 'owner'
      || next.reauthenticated !== true
      || !next.capabilities.includes('account.self')
      || !next.capabilities.includes('accounts.admin')
      || !next.capabilities.includes('services.admin')
    if (supportIdentityChanged) {
      _accountSessionsRequestSequence += 1
      _accountUsersRequestSequence += 1
      _supportSelfRequestSequence += 1
      _responsibleUseRequestSequence += 1
      _responsibleUseAcceptanceSequence += 1
      _supportAdminRequestSequence += 1
    } else if (supportAdminUnavailable) {
      _supportAdminRequestSequence += 1
    }
    set({
      accessContext: projectedAccessContext,
      accountContext: next,
      accountContextLoading: false,
      ...(accountIdentityChanged ? {
        ...projectUiScrub,
        accountProjectMigration: null,
        accountProjectMigrationLoading: false,
      } : {}),
      ...(supportIdentityChanged ? {
        accountSessions: [],
        accountUsers: [],
        accountDetailsLoading: false,
        supportSelf: null,
        responsibleUse: null,
        supportAdminAccountId: null,
        supportAdmin: null,
        supportDetailsLoading: false,
      } : {}),
      ...(!supportIdentityChanged && supportAdminUnavailable ? {
        supportAdminAccountId: null,
        supportAdmin: null,
        supportDetailsLoading: false,
      } : {}),
    })
    if (accountIdentityChanged && refreshProjectsOnIdentityChange) {
      await get().loadWorkspaces()
    }
    return context
  },
  accountContext: null,
  accountContextLoading: false,
  accountProjectMigration: null,
  accountProjectMigrationLoading: false,
  accountDrawerOpen: false,
  accountSessions: [],
  accountUsers: [],
  accountDetailsLoading: false,
  supportCatalog: null,
  supportCatalogLoading: false,
  supportCatalogUnavailable: false,
  supportSelf: null,
  responsibleUse: null,
  supportAdminAccountId: null,
  supportAdmin: null,
  supportDetailsLoading: false,
  setAccountDrawerOpen: (open) => {
    if (!open) _supportAdminRequestSequence += 1
    set(open
      ? { accountDrawerOpen: true }
      : {
          accountDrawerOpen: false,
          supportAdminAccountId: null,
          supportAdmin: null,
          supportDetailsLoading: false,
        })
  },
  loadAccountContext: async (refreshProjectsOnIdentityChange = true) => {
    const accessAccounts = get().accessContext?.accounts
    if (accessAccounts?.enabled !== true) {
      // A null access bootstrap is still in flight; do not supersede its
      // account projection merely because the drawer opened early. An
      // explicit accounts-disabled projection does invalidate older loads.
      if (accessAccounts) _accountContextRequestSequence += 1
      _accountSessionsRequestSequence += 1
      _accountUsersRequestSequence += 1
      _supportSelfRequestSequence += 1
      _responsibleUseRequestSequence += 1
      _responsibleUseAcceptanceSequence += 1
      _supportAdminRequestSequence += 1
      const identityChanged = _accountIdentity(get().accountContext) !== _accountIdentity(accessAccounts)
      if (identityChanged) _advanceAccountIdentityEpoch()
      const projectUiScrub = identityChanged ? _scrubAccountBoundProjectUi(get()) : {}
      set({
        ...projectUiScrub,
        accountContext: get().accessContext?.accounts ?? null,
        accountContextLoading: false,
        accountProjectMigration: null,
        accountProjectMigrationLoading: false,
        accountSessions: [],
        accountUsers: [],
        supportSelf: null,
        responsibleUse: null,
        supportAdminAccountId: null,
        supportAdmin: null,
        supportDetailsLoading: false,
      })
      if (identityChanged && refreshProjectsOnIdentityChange) {
        await get().loadWorkspaces()
      }
      return get().accessContext?.accounts ?? null
    }
    const requestSequence = ++_accountContextRequestSequence
    set({ accountContextLoading: true })
    try {
      const context = await api.fetchAccountContext()
      if (requestSequence !== _accountContextRequestSequence) return null
      const previous = get().accountContext
      const accountIdentityChanged = _accountIdentity(previous) !== _accountIdentity(context)
      if (accountIdentityChanged) _advanceAccountIdentityEpoch()
      const projectUiScrub = accountIdentityChanged ? _scrubAccountBoundProjectUi(get()) : {}
      const supportIdentityChanged = previous?.account?.id !== context.account?.id
        || previous?.capabilities.includes('account.self') !== context.capabilities.includes('account.self')
      const supportAdminUnavailable = context.authenticated !== true
        || context.account?.role !== 'owner'
        || context.reauthenticated !== true
        || !context.capabilities.includes('account.self')
        || !context.capabilities.includes('accounts.admin')
        || !context.capabilities.includes('services.admin')
      if (supportIdentityChanged) {
        _accountSessionsRequestSequence += 1
        _accountUsersRequestSequence += 1
        _supportSelfRequestSequence += 1
        _responsibleUseRequestSequence += 1
        _responsibleUseAcceptanceSequence += 1
        _supportAdminRequestSequence += 1
      } else if (supportAdminUnavailable) {
        _supportAdminRequestSequence += 1
      }
      set(state => {
        const identityChanged = state.accountContext?.account?.id !== context.account?.id
        const selfUnavailable = context.authenticated !== true
          || !context.capabilities.includes('account.self')
        return {
          ...projectUiScrub,
          accountContext: context,
          accountContextLoading: false,
          accessContext: state.accessContext
            ? { ...state.accessContext, accounts: context }
            : state.accessContext,
          ...(identityChanged || selfUnavailable ? {
            accountSessions: [],
            accountUsers: [],
            accountDetailsLoading: false,
            supportSelf: null,
            responsibleUse: null,
            supportAdminAccountId: null,
            supportAdmin: null,
            supportDetailsLoading: false,
          } : {}),
          ...(accountIdentityChanged ? {
            accountProjectMigration: null,
            accountProjectMigrationLoading: false,
          } : {}),
          ...(supportAdminUnavailable ? {
            supportAdminAccountId: null,
            supportAdmin: null,
            supportDetailsLoading: false,
          } : {}),
        }
      })
      if (accountIdentityChanged && refreshProjectsOnIdentityChange) {
        await get().loadWorkspaces()
      }
      return context
    } catch (error) {
      if (requestSequence === _accountContextRequestSequence) {
        set({ accountContextLoading: false })
      }
      throw error
    }
  },
  loadAccountProjectMigration: async () => {
    const state = get()
    const context = state.accountContext
    const directLoopback = typeof window !== 'undefined'
      && api.isDirectLoopbackHostname(window.location.hostname)
    if (
      state.accessContext?.accounts?.enabled !== true
      || state.accessContext.remote
      || !directLoopback
      || context?.authenticated !== true
      || context.account?.role !== 'owner'
      || context.reauthenticated !== true
      || !context.capabilities.includes('owner.admin')
    ) {
      _accountProjectMigrationRequestSequence += 1
      set({ accountProjectMigration: null, accountProjectMigrationLoading: false })
      return null
    }
    const requestSequence = ++_accountProjectMigrationRequestSequence
    set({ accountProjectMigrationLoading: true })
    try {
      const status = await api.fetchAccountProjectMigration()
      if (requestSequence !== _accountProjectMigrationRequestSequence) return null
      set({ accountProjectMigration: status, accountProjectMigrationLoading: false })
      return status
    } catch (error) {
      if (requestSequence === _accountProjectMigrationRequestSequence) {
        set({ accountProjectMigration: null, accountProjectMigrationLoading: false })
      }
      throw error
    }
  },
  migrateAccountProjects: async () => {
    const state = get()
    const context = state.accountContext
    const directLoopback = typeof window !== 'undefined'
      && api.isDirectLoopbackHostname(window.location.hostname)
    if (
      state.accessContext?.accounts?.enabled !== true
      || state.accessContext.remote
      || !directLoopback
      || context?.authenticated !== true
      || context.account?.role !== 'owner'
      || context.reauthenticated !== true
      || !context.capabilities.includes('owner.admin')
    ) throw new api.AccountApiError('Project setup is not available here.', {
      code: 'project_migration_unavailable',
      status: 403,
    })
    const requestSequence = ++_accountProjectMigrationRequestSequence
    set({ accountProjectMigrationLoading: true })
    try {
      const status = await api.migrateAccountProjects()
      if (requestSequence !== _accountProjectMigrationRequestSequence) return null
      _advanceAccountIdentityEpoch()
      set({
        ..._scrubAccountBoundProjectUi(get()),
        accountProjectMigration: status,
        accountProjectMigrationLoading: false,
      })
      if (!await get().loadWorkspaces()) {
        throw new Error('Project setup finished, but project access could not be refreshed')
      }
      return status
    } catch (error) {
      if (requestSequence === _accountProjectMigrationRequestSequence) {
        set({ accountProjectMigrationLoading: false })
      }
      throw error
    }
  },
  bootstrapAccount: async (input) => {
    const mutationSequence = _beginAccountMutation()
    const accountIdentityEpoch = _accountIdentityEpoch
    set(_scrubAccountBoundProjectUi(get()))
    let result: AccountAuthResult
    try {
      result = await api.bootstrapAccount(input)
    } catch (error) {
      if (
        mutationSequence !== _accountMutationRequestSequence
        || !_accountIdentityIsCurrent(accountIdentityEpoch)
      ) return null
      if (mutationSequence === _accountMutationRequestSequence) {
        await get().loadAccountContext(false).catch(() => null)
        await get().loadWorkspaces()
      }
      if (
        mutationSequence !== _accountMutationRequestSequence
        || !_accountIdentityIsCurrent(accountIdentityEpoch)
      ) return null
      throw error
    }
    if (
      mutationSequence !== _accountMutationRequestSequence
      || !_accountIdentityIsCurrent(accountIdentityEpoch)
    ) return null
    await get().loadAccountContext(false).catch(() => null)
    if (
      mutationSequence !== _accountMutationRequestSequence
      || get().accountContext?.account?.id !== result.account.id
    ) return null
    const resolvedIdentityEpoch = _accountIdentityEpoch
    await get().loadWorkspaces()
    if (
      mutationSequence !== _accountMutationRequestSequence
      || !_accountIdentityIsCurrent(resolvedIdentityEpoch)
      || get().accountContext?.account?.id !== result.account.id
    ) return null
    await Promise.all([
      get().loadAccountSessions().catch(() => undefined),
      get().loadAccountUsers().catch(() => undefined),
    ])
    if (
      mutationSequence !== _accountMutationRequestSequence
      || !_accountIdentityIsCurrent(resolvedIdentityEpoch)
      || get().accountContext?.account?.id !== result.account.id
    ) return null
    return result
  },
  loginAccount: async (input) => {
    const mutationSequence = _beginAccountMutation()
    set(_scrubAccountBoundProjectUi(get()))
    let result: AccountAuthResult
    try {
      result = await api.loginAccount(input)
    } catch (error) {
      if (mutationSequence === _accountMutationRequestSequence) {
        await get().loadAccountContext(false).catch(() => null)
        await get().loadWorkspaces()
      }
      throw error
    }
    if (mutationSequence !== _accountMutationRequestSequence) return result
    await get().loadAccountContext(false).catch(() => null)
    if (mutationSequence !== _accountMutationRequestSequence) return result
    await get().loadWorkspaces()
    if (mutationSequence !== _accountMutationRequestSequence) return result
    await Promise.all([
      get().loadAccountSessions().catch(() => undefined),
      get().loadAccountUsers().catch(() => undefined),
    ])
    return result
  },
  logoutAccount: async () => {
    const mutationSequence = _beginAccountMutation()
    set(_scrubAccountBoundProjectUi(get()))
    try {
      await api.logoutAccount()
    } catch (error) {
      if (mutationSequence === _accountMutationRequestSequence) {
        await get().loadAccountContext(false).catch(() => null)
        await get().loadWorkspaces()
      }
      throw error
    }
    if (mutationSequence !== _accountMutationRequestSequence) return
    _supportSelfRequestSequence += 1
    _responsibleUseRequestSequence += 1
    _responsibleUseAcceptanceSequence += 1
    _supportAdminRequestSequence += 1
    set(state => ({
      accountContext: state.accountContext
        ? {
            ...state.accountContext,
            authenticated: false,
            account: null,
            capabilities: [],
            reauthenticated: false,
          }
        : null,
      accountSessions: [],
      accountUsers: [],
      accountContextLoading: false,
      accountProjectMigration: null,
      accountProjectMigrationLoading: false,
      accountDetailsLoading: false,
      supportSelf: null,
      responsibleUse: null,
      supportAdminAccountId: null,
      supportAdmin: null,
      supportDetailsLoading: false,
    }))
    await get().loadAccountContext(false).catch(() => null)
    if (mutationSequence === _accountMutationRequestSequence) await get().loadWorkspaces()
  },
  reauthenticateAccount: async (password) => {
    const mutationSequence = _beginAccountMutation(false)
    await api.reauthenticateAccount(password)
    if (mutationSequence !== _accountMutationRequestSequence) return
    await get().loadAccountContext(false).catch(() => null)
    if (mutationSequence === _accountMutationRequestSequence) await get().loadWorkspaces()
  },
  recoverAccount: async (input) => {
    const mutationSequence = _beginAccountMutation()
    const accountIdentityEpoch = _accountIdentityEpoch
    set(_scrubAccountBoundProjectUi(get()))
    let result: AccountAuthResult
    try {
      result = await api.recoverAccount(input)
    } catch (error) {
      if (
        mutationSequence !== _accountMutationRequestSequence
        || !_accountIdentityIsCurrent(accountIdentityEpoch)
      ) return null
      if (mutationSequence === _accountMutationRequestSequence) {
        await get().loadAccountContext(false).catch(() => null)
        await get().loadWorkspaces()
      }
      if (
        mutationSequence !== _accountMutationRequestSequence
        || !_accountIdentityIsCurrent(accountIdentityEpoch)
      ) return null
      throw error
    }
    if (
      mutationSequence !== _accountMutationRequestSequence
      || !_accountIdentityIsCurrent(accountIdentityEpoch)
    ) return null
    await get().loadAccountContext(false).catch(() => null)
    if (
      mutationSequence !== _accountMutationRequestSequence
      || get().accountContext?.account?.id !== result.account.id
    ) return null
    const resolvedIdentityEpoch = _accountIdentityEpoch
    await get().loadWorkspaces()
    if (
      mutationSequence !== _accountMutationRequestSequence
      || !_accountIdentityIsCurrent(resolvedIdentityEpoch)
      || get().accountContext?.account?.id !== result.account.id
    ) return null
    await Promise.all([
      get().loadAccountSessions().catch(() => undefined),
      get().loadAccountUsers().catch(() => undefined),
    ])
    if (
      mutationSequence !== _accountMutationRequestSequence
      || !_accountIdentityIsCurrent(resolvedIdentityEpoch)
      || get().accountContext?.account?.id !== result.account.id
    ) return null
    return result
  },
  changeAccountPassword: async (newPassword) => {
    const mutationSequence = _beginAccountMutation(false)
    await api.changeAccountPassword(newPassword)
    if (mutationSequence !== _accountMutationRequestSequence) return
    await Promise.all([
      get().loadAccountContext().catch(() => null),
      get().loadAccountSessions().catch(() => undefined),
    ])
  },
  rotateAccountRecoveryCodes: async () => {
    const mutationSequence = _beginAccountMutation(false)
    const accountIdentityEpoch = _accountIdentityEpoch
    const accountId = get().accountContext?.account?.id
    const ownsAuthorization = () => {
      const context = get().accountContext
      return _accountIdentityIsCurrent(accountIdentityEpoch)
        && mutationSequence === _accountMutationRequestSequence
        && Boolean(accountId)
        && context?.authenticated === true
        && context.account?.id === accountId
        && context.reauthenticated === true
        && context.capabilities.includes('account.self')
    }
    let result: Awaited<ReturnType<typeof api.rotateAccountRecoveryCodes>>
    try {
      result = await api.rotateAccountRecoveryCodes()
    } catch (error) {
      if (!ownsAuthorization()) return null
      throw error
    }
    if (!ownsAuthorization()) return null
    return result.recovery_codes
  },
  loadAccountSessions: async () => {
    const requestSequence = ++_accountSessionsRequestSequence
    const context = get().accountContext
    const accountId = context?.authenticated === true ? context.account?.id : null
    if (!accountId || !context?.capabilities.includes('account.self')) {
      set({ accountSessions: [], accountDetailsLoading: false })
      return
    }
    set({ accountDetailsLoading: true })
    try {
      const result = await api.fetchAccountSessions()
      const current = get().accountContext
      if (
        requestSequence !== _accountSessionsRequestSequence
        || current?.authenticated !== true
        || current.account?.id !== accountId
        || !current.capabilities.includes('account.self')
      ) return
      set({ accountSessions: result.sessions, accountDetailsLoading: false })
    } catch (error) {
      const current = get().accountContext
      if (
        requestSequence === _accountSessionsRequestSequence
        && current?.authenticated === true
        && current.account?.id === accountId
      ) {
        set({ accountDetailsLoading: false })
      }
      throw error
    }
  },
  revokeAccountSession: async (sessionHandle) => {
    _invalidateAccountRequests()
    const result = await api.revokeAccountSession(sessionHandle)
    if (result.current) {
      _advanceAccountIdentityEpoch()
      set({
        ..._scrubAccountBoundProjectUi(get()),
        accountSessions: [],
        accountUsers: [],
        accountProjectMigration: null,
        accountProjectMigrationLoading: false,
      })
      await get().loadAccountContext(false).catch(() => null)
      await get().loadWorkspaces()
    } else {
      await get().loadAccountSessions()
    }
    return result.current
  },
  revokeAllAccountSessions: async (retainCurrent) => {
    _invalidateAccountRequests()
    const result = await api.revokeAllAccountSessions(retainCurrent)
    if (result.current_revoked) {
      _advanceAccountIdentityEpoch()
      set({
        ..._scrubAccountBoundProjectUi(get()),
        accountSessions: [],
        accountUsers: [],
        accountProjectMigration: null,
        accountProjectMigrationLoading: false,
      })
      await get().loadAccountContext(false).catch(() => null)
      await get().loadWorkspaces()
    } else {
      await get().loadAccountSessions()
    }
    return result.revoked
  },
  loadAccountUsers: async () => {
    const requestSequence = ++_accountUsersRequestSequence
    const context = get().accountContext
    const accountId = context?.authenticated === true ? context.account?.id : null
    if (
      !accountId
      || context?.reauthenticated !== true
      || !context.capabilities.includes('accounts.admin')
      || !context.capabilities.includes('services.admin')
    ) {
      set({
        accountUsers: [],
        accountDetailsLoading: false,
        supportAdminAccountId: null,
        supportAdmin: null,
      })
      return
    }
    set({ accountDetailsLoading: true })
    try {
      const result = await api.fetchServerAccounts()
      const current = get().accountContext
      if (
        requestSequence !== _accountUsersRequestSequence
        || current?.authenticated !== true
        || current.account?.id !== accountId
        || current.reauthenticated !== true
        || !current.capabilities.includes('accounts.admin')
        || !current.capabilities.includes('services.admin')
      ) return
      set({ accountUsers: result.accounts, accountDetailsLoading: false })
    } catch (error) {
      const current = get().accountContext
      if (
        requestSequence === _accountUsersRequestSequence
        && current?.authenticated === true
        && current.account?.id === accountId
      ) {
        set({ accountDetailsLoading: false })
      }
      throw error
    }
  },
  createServerAccount: async (input) => {
    const mutationSequence = _beginAccountMutation(false)
    const accountIdentityEpoch = _accountIdentityEpoch
    const accountId = get().accountContext?.account?.id
    const ownsAuthorization = () => {
      const context = get().accountContext
      return _accountIdentityIsCurrent(accountIdentityEpoch)
        && mutationSequence === _accountMutationRequestSequence
        && Boolean(accountId)
        && context?.authenticated === true
        && context.account?.id === accountId
        && context.reauthenticated === true
        && context.capabilities.includes('accounts.admin')
        && context.capabilities.includes('services.admin')
    }
    let result: AccountAuthResult
    try {
      result = await api.createServerAccount(input)
    } catch (error) {
      if (!ownsAuthorization()) return null
      throw error
    }
    if (!ownsAuthorization()) return null
    await get().loadAccountUsers()
    if (!ownsAuthorization()) return null
    return result
  },
  setServerAccountDisabled: async (accountId, disabled) => {
    await api.setServerAccountDisabled(accountId, disabled)
    await get().loadAccountUsers()
  },
  loadSupportCatalog: async () => {
    const requestSequence = ++_supportCatalogRequestSequence
    set({ supportCatalogLoading: true, supportCatalogUnavailable: false })
    try {
      const catalog = await api.fetchSupportCatalog()
      if (requestSequence === _supportCatalogRequestSequence) {
        set({
          supportCatalog: catalog,
          supportCatalogLoading: false,
          supportCatalogUnavailable: false,
        })
      }
      return catalog
    } catch {
      if (requestSequence === _supportCatalogRequestSequence) {
        set({
          supportCatalog: null,
          supportCatalogLoading: false,
          supportCatalogUnavailable: true,
        })
      }
      return null
    }
  },
  loadSupportSelf: async () => {
    const context = get().accountContext
    if (
      context?.authenticated !== true
      || !context.capabilities.includes('account.self')
    ) {
      _supportSelfRequestSequence += 1
      set({ supportSelf: null, supportDetailsLoading: false })
      return null
    }
    const accountId = context.account?.id
    const requestSequence = ++_supportSelfRequestSequence
    _responsibleUseAcceptanceSequence += 1
    set({ supportDetailsLoading: true })
    try {
      const projection = await api.fetchSupportSelf()
      if (requestSequence !== _supportSelfRequestSequence) return null
      const current = get().accountContext
      if (
        current?.authenticated !== true
        || !current.capabilities.includes('account.self')
        || current.account?.id !== accountId
      ) {
        set({ supportSelf: null, supportDetailsLoading: false })
        return null
      }
      set({
        supportSelf: projection,
        supportDetailsLoading: false,
      })
      return projection
    } catch (error) {
      if (requestSequence === _supportSelfRequestSequence) {
        set({ supportSelf: null, supportDetailsLoading: false })
      }
      throw error
    }
  },
  loadResponsibleUse: async () => {
    const context = get().accountContext
    if (
      context?.authenticated !== true
      || !context.capabilities.includes('account.self')
    ) {
      _responsibleUseRequestSequence += 1
      set({ responsibleUse: null, supportDetailsLoading: false })
      return null
    }
    const accountId = context.account?.id
    const requestSequence = ++_responsibleUseRequestSequence
    _responsibleUseAcceptanceSequence += 1
    set({ supportDetailsLoading: true })
    try {
      const projection = await api.fetchResponsibleUse()
      if (requestSequence !== _responsibleUseRequestSequence) return null
      const current = get().accountContext
      if (
        current?.authenticated !== true
        || !current.capabilities.includes('account.self')
        || current.account?.id !== accountId
      ) {
        set({ responsibleUse: null, supportDetailsLoading: false })
        return null
      }
      set({ responsibleUse: projection, supportDetailsLoading: false })
      return projection
    } catch (error) {
      if (requestSequence === _responsibleUseRequestSequence) {
        set({ responsibleUse: null, supportDetailsLoading: false })
      }
      throw error
    }
  },
  acceptResponsibleUse: async (documentVersion, contentSha256) => {
    const context = get().accountContext
    if (
      context?.authenticated !== true
      || !context.capabilities.includes('account.self')
    ) throw new Error('Sign in to acknowledge responsible use.')
    const accountId = context.account?.id
    const acceptanceSequence = ++_responsibleUseAcceptanceSequence
    const result = await api.acceptResponsibleUse({ documentVersion, contentSha256 })
    const current = get()
    const currentNotice = current.responsibleUse?.notice
      || current.supportSelf?.responsible_use.notice
    const bindingIsCurrent = acceptanceSequence === _responsibleUseAcceptanceSequence
      && current.accountContext?.authenticated === true
      && current.accountContext.capabilities.includes('account.self')
      && current.accountContext.account?.id === accountId
      && currentNotice?.version === documentVersion
      && currentNotice.content_sha256 === contentSha256
      && result.status.document_id === currentNotice.document_id
      && result.status.document_version === documentVersion
      && result.status.content_sha256 === contentSha256
    if (!bindingIsCurrent) {
      throw new Error('Responsible-use account or notice changed before acknowledgement completed.')
    }
    set(state => ({
      responsibleUse: state.responsibleUse
        ? { ...state.responsibleUse, status: result.status }
        : state.responsibleUse,
      supportSelf: state.supportSelf
        ? {
            ...state.supportSelf,
            responsible_use: {
              ...state.supportSelf.responsible_use,
              status: result.status,
            },
          }
        : state.supportSelf,
    }))
  },
  loadSupportAdmin: async (accountId) => {
    const invocationSequence = ++_supportAdminRequestSequence
    try {
      await get().loadAccountContext()
    } catch {
      if (invocationSequence === _supportAdminRequestSequence) {
        _supportAdminRequestSequence += 1
        set({ supportAdminAccountId: null, supportAdmin: null, supportDetailsLoading: false })
      }
      throw new Error('Recent owner access could not be confirmed for private Support details.')
    }
    if (invocationSequence !== _supportAdminRequestSequence) {
      throw new Error('Support account selection changed before owner access was confirmed.')
    }
    const context = get().accountContext
    const eligible = context?.authenticated === true
      && context.account?.role === 'owner'
      && context.reauthenticated === true
      && context.capabilities.includes('account.self')
      && context.capabilities.includes('accounts.admin')
      && context.capabilities.includes('services.admin')
      && get().accountDrawerOpen
      && get().accountUsers.some(account => account.id === accountId)
    if (!eligible) {
      _supportAdminRequestSequence += 1
      set({ supportAdminAccountId: null, supportAdmin: null, supportDetailsLoading: false })
      throw new Error('Choose a server-returned account after confirming owner access.')
    }
    const requestSequence = invocationSequence
    set({
      supportDetailsLoading: true,
      supportAdminAccountId: accountId,
      supportAdmin: null,
    })
    try {
      const projection = await api.fetchAdminAccountSupport(accountId)
      const current = get()
      if (
        requestSequence !== _supportAdminRequestSequence
        || current.supportAdminAccountId !== accountId
      ) return projection
      const stillEligible = current.accountContext?.authenticated === true
        && current.accountContext.account?.role === 'owner'
        && current.accountContext.reauthenticated === true
        && current.accountContext.capabilities.includes('account.self')
        && current.accountContext.capabilities.includes('accounts.admin')
        && current.accountContext.capabilities.includes('services.admin')
        && current.accountDrawerOpen
        && current.accountUsers.some(account => account.id === accountId)
      if (!stillEligible) {
        _supportAdminRequestSequence += 1
        set({ supportAdminAccountId: null, supportAdmin: null, supportDetailsLoading: false })
        throw new Error('Owner access changed while Support details were loading.')
      }
      set({ supportAdmin: projection, supportDetailsLoading: false })
      return projection
    } catch (error) {
      if (requestSequence === _supportAdminRequestSequence) {
        const accessRejected = error instanceof api.AccountApiError
          && (error.status === 401 || error.status === 403)
        if (accessRejected) _supportAdminRequestSequence += 1
        set({
          ...(accessRejected ? { supportAdminAccountId: null } : {}),
          supportAdmin: null,
          supportDetailsLoading: false,
        })
      }
      throw error
    }
  },
  transitionSupportFulfillment: async (accountId, input) => {
    const invocationSequence = ++_supportAdminRequestSequence
    try {
      await get().loadAccountContext()
    } catch {
      if (invocationSequence === _supportAdminRequestSequence) {
        _supportAdminRequestSequence += 1
        set({ supportAdminAccountId: null, supportAdmin: null, supportDetailsLoading: false })
      }
      throw new Error('Recent owner access could not be confirmed for fulfillment follow-up.')
    }
    if (invocationSequence !== _supportAdminRequestSequence) {
      throw new Error('Support account selection changed before fulfillment follow-up was confirmed.')
    }
    const context = get().accountContext
    const eligible = context?.authenticated === true
      && context.account?.role === 'owner'
      && context.reauthenticated === true
      && context.capabilities.includes('account.self')
      && context.capabilities.includes('accounts.admin')
      && context.capabilities.includes('services.admin')
      && get().accountDrawerOpen
      && get().accountUsers.some(account => account.id === accountId)
      && get().supportAdminAccountId === accountId
      && get().supportAdmin !== null
    if (!eligible) {
      _supportAdminRequestSequence += 1
      set({ supportAdminAccountId: null, supportAdmin: null, supportDetailsLoading: false })
      throw new Error('Choose a server-returned account after confirming owner access.')
    }
    try {
      const projection = await api.transitionAdminAccountFulfillment(accountId, input)
      const current = get()
      const stillEligible = invocationSequence === _supportAdminRequestSequence
        && current.supportAdminAccountId === accountId
        && current.accountContext?.authenticated === true
        && current.accountContext.account?.role === 'owner'
        && current.accountContext.reauthenticated === true
        && current.accountContext.capabilities.includes('account.self')
        && current.accountContext.capabilities.includes('accounts.admin')
        && current.accountContext.capabilities.includes('services.admin')
        && current.accountDrawerOpen
        && current.accountUsers.some(account => account.id === accountId)
      if (!stillEligible) {
        throw new Error('Owner access or Support selection changed while fulfillment follow-up was saving.')
      }
      set({ supportAdmin: projection, supportDetailsLoading: false })
      return projection
    } catch (error) {
      if (invocationSequence === _supportAdminRequestSequence) {
        const accountError = error instanceof api.AccountApiError ? error : null
        if (accountError && (accountError.status === 401 || accountError.status === 403)) {
          _supportAdminRequestSequence += 1
          set({ supportAdminAccountId: null, supportAdmin: null, supportDetailsLoading: false })
        } else if (accountError?.status === 409) {
          await get().loadSupportAdmin(accountId).catch(() => {
            set({ supportAdminAccountId: null, supportAdmin: null, supportDetailsLoading: false })
          })
        }
      }
      throw error
    }
  },
  recordSupportContribution: async (accountId, input) => {
    const invocationSequence = ++_supportAdminRequestSequence
    try {
      await get().loadAccountContext()
    } catch {
      if (invocationSequence === _supportAdminRequestSequence) {
        _supportAdminRequestSequence += 1
        set({ supportAdminAccountId: null, supportAdmin: null, supportDetailsLoading: false })
      }
      throw new Error('Recent owner access could not be confirmed for the manual contribution record.')
    }
    if (invocationSequence !== _supportAdminRequestSequence) {
      throw new Error('Support account selection changed before the manual contribution record was confirmed.')
    }
    const context = get().accountContext
    const eligible = context?.authenticated === true
      && context.account?.role === 'owner'
      && context.reauthenticated === true
      && context.capabilities.includes('account.self')
      && context.capabilities.includes('accounts.admin')
      && context.capabilities.includes('services.admin')
      && get().accountDrawerOpen
      && get().accountUsers.some(account => account.id === accountId)
      && get().supportAdminAccountId === accountId
      && get().supportAdmin !== null
    if (!eligible) {
      _supportAdminRequestSequence += 1
      set({ supportAdminAccountId: null, supportAdmin: null, supportDetailsLoading: false })
      throw new Error('Choose a server-returned account after confirming owner access.')
    }
    try {
      const projection = await api.recordAdminAccountContribution(accountId, input)
      const current = get()
      const stillEligible = invocationSequence === _supportAdminRequestSequence
        && current.supportAdminAccountId === accountId
        && current.accountContext?.authenticated === true
        && current.accountContext.account?.role === 'owner'
        && current.accountContext.reauthenticated === true
        && current.accountContext.capabilities.includes('account.self')
        && current.accountContext.capabilities.includes('accounts.admin')
        && current.accountContext.capabilities.includes('services.admin')
        && current.accountDrawerOpen
        && current.accountUsers.some(account => account.id === accountId)
      if (!stillEligible) {
        throw new Error('Owner access or Support selection changed while the manual contribution record was saving.')
      }
      set({ supportAdmin: projection, supportDetailsLoading: false })
      return projection
    } catch (error) {
      if (invocationSequence === _supportAdminRequestSequence) {
        const accountError = error instanceof api.AccountApiError ? error : null
        if (accountError && (accountError.status === 401 || accountError.status === 403)) {
          _supportAdminRequestSequence += 1
          set({ supportAdminAccountId: null, supportAdmin: null, supportDetailsLoading: false })
        } else if (accountError?.status === 409) {
          await get().loadSupportAdmin(accountId).catch(() => {
            set({ supportAdminAccountId: null, supportAdmin: null, supportDetailsLoading: false })
          })
        }
      }
      throw error
    }
  },
  clearSupportAdmin: () => {
    _supportAdminRequestSequence += 1
    set({ supportAdminAccountId: null, supportAdmin: null, supportDetailsLoading: false })
  },
  systemConfig: null,
  systemConfigLoading: false,
  loadSystemConfig: async () => {
    set({ systemConfigLoading: true })
    try {
      const config = await api.fetchSystemConfig()
      set({ systemConfig: config, systemConfigLoading: false })
    } catch (e) {
      console.error('Failed to load system config:', e)
      set({ systemConfigLoading: false })
    }
  },
  updateSystemConfig: async (partial, signal) => {
    const updateSequence = ++_systemConfigUpdateSequence
    _systemConfigUpdateController?.abort()
    const controller = new AbortController()
    _systemConfigUpdateController = controller
    const abortFromCaller = () => controller.abort()
    if (signal?.aborted) controller.abort()
    else signal?.addEventListener('abort', abortFromCaller, { once: true })
    let timedOut = false
    let timeoutId: number | undefined
    try {
      const response = await Promise.race([
        api.updateSystemConfig(partial, controller.signal),
        new Promise<never>((_, reject) => {
          timeoutId = window.setTimeout(() => {
            timedOut = true
            controller.abort()
            reject(new Error('system config update timed out'))
          }, SYSTEM_CONFIG_UPDATE_TIMEOUT_MS)
        }),
      ])
      if (controller.signal.aborted) throw new DOMException('System config update aborted', 'AbortError')
      set(s => ({
        systemConfig: s.systemConfig ? { ...s.systemConfig, ...partial } : null,
      }))
      if (updateSequence === _systemConfigUpdateSequence) {
        set({ systemConfigLoading: false })
      }
      return { ok: true, updated: response.updated }
    } catch (e) {
      const cancelled = controller.signal.aborted && !timedOut
      if (cancelled) {
        if (updateSequence === _systemConfigUpdateSequence) {
          set({ systemConfigLoading: false })
        }
      } else {
        console.error('Failed to update system config:', e)
        if (updateSequence === _systemConfigUpdateSequence) {
          set({ systemConfigLoading: true })
        }
        void api.fetchSystemConfig()
          .then(config => {
            if (updateSequence !== _systemConfigUpdateSequence) return
            set({ systemConfig: config, systemConfigLoading: false })
          })
          .catch(error => {
            console.error('Failed to reconcile system config:', error)
            if (updateSequence === _systemConfigUpdateSequence) {
              set({ systemConfigLoading: false })
            }
          })
      }
      return {
        ok: false,
        code: timedOut ? 'timeout' : cancelled ? 'cancelled' : 'request_failed',
        message: timedOut
          ? SYSTEM_CONFIG_UPDATE_TIMEOUT_MESSAGE
          : cancelled
            ? SYSTEM_CONFIG_UPDATE_CANCELLED_MESSAGE
            : SYSTEM_CONFIG_UPDATE_FAILURE_MESSAGE,
      }
    } finally {
      if (timeoutId !== undefined) window.clearTimeout(timeoutId)
      signal?.removeEventListener('abort', abortFromCaller)
      if (_systemConfigUpdateController === controller) {
        _systemConfigUpdateController = null
      }
    }
  },

  // Hardware detect — see type definition above. Initial value null;
  // populated when AutoPerformanceCard mounts (Settings → System).
  // Refreshed when the user clicks Re-detect on the auto card.
  systemDetect: null,
  loadSystemDetect: async () => {
    try {
      const detect = await api.fetchSystemDetect()
      set({ systemDetect: detect })
    } catch (e) {
      console.error('Failed to load system detect:', e)
    }
  },

  // Live hardware telemetry (HardwareStatusBar). Polled ~5s from the
  // component while mounted. Swallows a single failed tick (e.g. backend
  // restarting) instead of spamming the console at the polling cadence.
  systemStats: null,
  loadSystemStats: async () => {
    try {
      const stats = await api.fetchSystemStats()
      set({ systemStats: stats })
    } catch {
      /* transient poll failure — ignore this tick */
    }
  },

  // Settings tab
  settingsTab: 'performance' as SettingsTab,
  setSettingsTab: (tab) => set({ settingsTab: tab }),

  // Services config
  servicesConfig: null,
  servicesConfigLoading: false,
  servicesConfigError: null,
  clearServicesConfigError: () => set({ servicesConfigError: null }),
  hostTerms: null,
  hostTermsLoading: false,
  hostTermsError: null,
  explicitOutput: false,
  setExplicitOutput: (enabled) => {
    ++_h3CompatibilitySeq
    ++_directorCapabilitiesSeq.standard
    ++_directorCapabilitiesSeq.explicit
    delete _directorCapabilitiesInFlight.standard
    delete _directorCapabilitiesInFlight.explicit
    set({
      explicitOutput: enabled,
      directorCapabilities: null,
      directorCapabilitiesExplicitOutput: null,
      directorCapabilitiesLoading: false,
      directorCapabilitiesLoadingExplicitOutput: null,
      directorCapabilitiesError: null,
      // A deliberate Private-off after this remains honored. Only the
      // transition into explicit intent applies the safe default.
      ...(enabled ? { privateOutput: true } : {}),
    })
  },
  privateOutput: false,
  setPrivateOutput: (enabled) => set({ privateOutput: enabled }),
  loadServicesConfig: async () => {
    set({ servicesConfigLoading: true })
    try {
      const config = await api.fetchServicesConfig()
      set({
        servicesConfig: config,
        servicesConfigLoading: false,
        servicesConfigError: null,
      })
      void get().loadHostTerms()
    } catch (e) {
      console.error('Failed to load services config:', e)
      set({
        servicesConfigLoading: false,
        servicesConfigError: e instanceof Error ? e.message : 'Failed to load services settings',
      })
    }
  },
  updateServicesConfig: async (partial) => {
    set({ servicesConfigError: null })
    try {
      await api.updateServicesConfig(partial)
      await get().loadServicesConfig()
    } catch (e) {
      console.error('Failed to update services config:', e)
      const message = e instanceof Error ? e.message : 'Failed to update services settings'
      set({ servicesConfigError: message })
      // Refresh any server-authoritative coercion without erasing the
      // mutation error users need to see.
      await get().loadServicesConfig()
      set({ servicesConfigError: message })
    }
  },
  loadHostTerms: async () => {
    await _queueHostTermsOperation(async () => {
      const workspace = get().activeWorkspace
      if (!workspace) {
        _setH3Ref2VATermsAccepted(false)
        set({ hostTerms: null, hostTermsLoading: false, hostTermsError: null })
        return
      }
      set({ hostTermsLoading: true, hostTermsError: null })
      try {
        const result = await api.fetchHostTerms(workspace)
        if (result.terms.minimax_h3_ref2va.accepted) {
          _clearLegacyH3Ref2VATermsAcceptance()
        }
        _setH3Ref2VATermsAccepted(result.terms.minimax_h3_ref2va.accepted)
        set({
          hostTerms: result.terms,
          hostTermsLoading: false,
          hostTermsError: null,
        })
      } catch (error) {
        _setH3Ref2VATermsAccepted(false)
        set({
          hostTermsLoading: false,
          hostTermsError: error instanceof Error ? error.message : 'Failed to load host notice status',
        })
      }
    })
  },
  acceptHostTerm: async (term) => {
    return _queueHostTermsOperation(async () => {
      const state = get()
      const document = state.hostTerms?.[term]
      if (!state.activeWorkspace || !document) {
        set({ hostTermsError: 'Select and unlock a project before accepting this notice' })
        return false
      }
      if (document.current_version !== HOST_TERM_NOTICES[term].version) {
        set({ hostTermsError: 'The notice changed. Refresh Maestro to review the current version.' })
        return false
      }
      set({ hostTermsLoading: true, hostTermsError: null })
      try {
        const result = await api.acceptHostTerm(
          term,
          HOST_TERM_NOTICES[term].version,
          state.activeWorkspace,
        )
        if (term === 'minimax_h3_ref2va') {
          _clearLegacyH3Ref2VATermsAcceptance()
        }
        _setH3Ref2VATermsAccepted(result.terms.minimax_h3_ref2va.accepted)
        set({ hostTerms: result.terms, hostTermsLoading: false, hostTermsError: null })
        return true
      } catch (error) {
        set({
          hostTermsLoading: false,
          hostTermsError: error instanceof Error ? error.message : 'Host notice acceptance failed',
        })
        return false
      }
    })
  },

  // LLM state
  llmStatus: null,
  llmLoading: false,
  llmModels: [],
  loadLlmStatus: async () => {
    try {
      const status = await api.fetchLlmStatus()
      set({ llmStatus: status })
    } catch (e) {
      console.error('Failed to load LLM status:', e)
    }
  },
  loadLlmModels: async () => {
    try {
      const data = await api.fetchLlmModels()
      set({ llmModels: data.models })
    } catch (e) {
      console.error('Failed to load LLM models:', e)
    }
  },
  loadLlm: async () => {
    set({ llmLoading: true })
    try {
      const result = await api.loadLlm()
      set({ llmStatus: { loaded: result.loaded, model_id: result.model_id, device: result.device, provider: result.provider || '' }, llmLoading: false })
    } catch (e) {
      console.error('Failed to load LLM:', e)
      set({ llmLoading: false })
    }
  },
  unloadLlm: async () => {
    try {
      await api.unloadLlm()
      set({ llmStatus: { loaded: false, model_id: null, device: null, provider: '' } })
    } catch (e) {
      console.error('Failed to unload LLM:', e)
    }
  },

  // Prompt enhancement
  isEnhancing: false,
  enhanceStatus: null,
  enhanceRequestScope: null,
  studioPromptEnhance: false,
  setStudioPromptEnhance: (enabled) => set({ studioPromptEnhance: enabled }),
  enhancePrompt: async (ttsMode?: string) => {
    const { params, generationMode, startImage, imageRefs, activeWorkspace } = get()
    if (!params.prompt.trim()) return false
    const accountIdentityEpoch = _accountIdentityEpoch
    const accountFingerprint = _enhanceAccountFingerprint(get())
    const promptEditGeneration = _enhancePromptEditGeneration
    const requestState = get()
    const inputsRemainCurrent = () => {
      const current = get()
      return current.startImage === startImage
        && current.imageRefs.length === imageRefs.length
        && current.imageRefs.every((reference, index) => reference === imageRefs[index])
    }
    const lifecycle = _beginEnhanceLlmRequest(activeWorkspace)
    let scope: api.LlmEnhanceOperationScope | null = null
    let submissionAttempted = false
    let durableRecoveryStored = false
    let settingsFingerprint = ''
    set({ isEnhancing: true, enhanceStatus: null, enhanceRequestScope: null })
    try {
      settingsFingerprint = await _enhanceSettingsFingerprint(requestState)
      if (
        !lifecycle.ownsWorkspace()
        || _enhancePromptEditGeneration !== promptEditGeneration
        || get().params.prompt !== params.prompt
        || !inputsRemainCurrent()
      ) return false
      const catalog = await api.fetchLlmModels(activeWorkspace, lifecycle.signal)
      if (!lifecycle.ownsWorkspace()) return false
      if (!catalog.project_instance) {
        throw new api.LlmEnhanceScopeError('Could not open this project for Prompt Enhance')
      }
      scope = {
        requestId: api.createLlmRequestId(),
        workspace: activeWorkspace,
        projectInstance: catalog.project_instance,
      }
      set({ enhanceRequestScope: scope })

      // Collect images relevant to the CURRENT mode only
      const imagePaths: string[] = []

      if (generationMode === 'image') {
        // Image mode: send reference images only
        for (const ref of imageRefs) {
          try {
            const uploaded = await api.uploadImage(ref)
            if (!lifecycle.ownsWorkspace()) return false
            imagePaths.push(uploaded.path)
          } catch { /* best effort */ }
        }
      } else {
        // Video/Avatar mode: send start image only
        if (startImage) {
          try {
            const uploaded = await api.uploadImage(startImage)
            if (!lifecycle.ownsWorkspace()) return false
            imagePaths.push(uploaded.path)
          } catch { /* best effort */ }
        } else if (params.image_start && typeof params.image_start === 'string') {
          imagePaths.push(params.image_start as string)
        }
      }

      // Include duration/window info for video models
      const state = get()
      const windowCount = state.modelOptions
        ? effectiveSlidingWindowGeometry(
            state.durationSeconds,
            state.slidingWindowSeconds,
            state.slidingWindowOverlap,
            state.modelOptions,
            {
              totalFrames: controlFpsTotalFrames(
                state.durationSeconds,
                state.params.force_fps,
                state.params.video_guide,
                state.guideVideoFps,
                state.guideVideoFrameCount,
              ),
            },
          ).windowCount
        : 1
      const promptMode = Number(state.params.multi_prompts_gen_type ?? 0)
      const standaloneType1SlidingPrompt = (
        generationMode === 'video'
        && state.modelOptions?.sliding_window === true
        && state.durationSeconds > state.slidingWindowSeconds
        && windowCount > 1
        && !state.params.model_type.startsWith('minimax_h3')
        && !String(state.modelOptions?.architecture || '').startsWith('minimax_h3')
        && state.params.image_mode !== 2
        && (promptMode === 0 || promptMode === 1)
        && !hasGlobalTimeline(params.prompt)
      )

      // TTS dialogue needs more tokens for longer conversations
      const maxTokens = (generationMode === 'audio' && ttsMode) ? 2048 : undefined
      if (!lifecycle.ownsWorkspace() || !_sameEnhanceScope(get().enhanceRequestScope, scope)) {
        return false
      }
      if (
        !_accountIdentityIsCurrent(accountIdentityEpoch)
        || _enhanceAccountFingerprint(get()) !== accountFingerprint
        || _enhancePromptEditGeneration !== promptEditGeneration
        || get().params.prompt !== params.prompt
        || (await _enhanceSettingsFingerprint(get())) !== settingsFingerprint
        || !inputsRemainCurrent()
      ) return false

      const result = await api.llmEnhancePrompt({
        workspace: activeWorkspace,
        request_id: scope.requestId,
        project_instance: scope.projectInstance,
        prompt: params.prompt,
        mode: generationMode,
        model_type: params.model_type,
        max_new_tokens: maxTokens,
        image_paths: imagePaths.length > 0 ? imagePaths : undefined,
        duration_seconds: (generationMode === 'video' || generationMode === 'avatar') ? state.durationSeconds : undefined,
        window_count: standaloneType1SlidingPrompt ? windowCount : undefined,
        window_size_seconds: (generationMode === 'video' || generationMode === 'avatar') ? state.slidingWindowSeconds : undefined,
        preserve_global_timeline: generationMode === 'video' && hasGlobalTimeline(params.prompt),
        activated_loras: params.activated_loras.length > 0 ? params.activated_loras : undefined,
        tts_enhance_mode: ttsMode || undefined,
        tts_voice_count: state.ttsVoiceCount || undefined,
        explicit_output: state.explicitOutput,
      }, {
        projectInstance: scope.projectInstance,
        signal: lifecycle.signal,
        onPreparationStatus: status => {
          if (
            lifecycle.ownsWorkspace()
            && scope
            && _sameEnhanceScope(get().enhanceRequestScope, scope)
          ) set({ enhanceStatus: status })
        },
        onSubmissionAttempted: async () => {
          if (
            lifecycle.ownsWorkspace()
            && scope
            && _sameEnhanceScope(get().enhanceRequestScope, scope)
          ) {
            const storedRecovery = await _storeEnhanceOperation({
              ...scope,
              accountFingerprint,
              settingsFingerprint,
              storedAt: Date.now(),
            }, lifecycle.signal)
            if (
              !lifecycle.ownsWorkspace()
              || !scope
              || !_sameEnhanceScope(get().enhanceRequestScope, scope)
            ) {
              if (storedRecovery) await _removeStoredEnhanceOperation(scope)
              return
            }
            durableRecoveryStored = storedRecovery || durableRecoveryStored
            submissionAttempted = true
            _enhanceSubmissionAttemptedRequestId = scope.requestId
          }
        },
        onOperationStatus: status => {
          if (
            lifecycle.ownsWorkspace()
            && scope
            && _sameEnhanceScope(get().enhanceRequestScope, scope)
          ) set({ enhanceStatus: _terminalEnhanceStatus(status) })
        },
      })
      if (
        !lifecycle.ownsWorkspace()
        || !_sameEnhanceScope(get().enhanceRequestScope, scope)
      ) return false
      if (
        !_accountIdentityIsCurrent(accountIdentityEpoch)
        || _enhanceAccountFingerprint(get()) !== accountFingerprint
        || _enhancePromptEditGeneration !== promptEditGeneration
        || get().params.prompt !== result.original
        || result.original !== params.prompt
        || (await _enhanceSettingsFingerprint(get())) !== settingsFingerprint
        || !inputsRemainCurrent()
      ) {
        await _removeStoredEnhanceOperation(scope)
        return false
      }
      await _removeStoredEnhanceOperation(scope)
      set(s => ({
        params: { ...s.params, prompt: result.enhanced },
        enhanceStatus: null,
      }))
      // Auto-parse speaker names from the enhanced text whenever there are
      // voice slots to fill. Previously gated to dialogue mode only; the user
      // expects monologue enhance ("Peter: Hello world.") to also populate
      // voice slot 1 with "Peter". `force=true` overrides the manual flag
      // — enhance creates a fresh script, so previous user-edited names are
      // no longer relevant.
      if (ttsMode && get().ttsVoiceCount > 0) {
        get()._autoParseSpkeakerNames(result.enhanced, true)
      }
      return true
    } catch (e) {
      if (_isBrowserAbort(e) || !lifecycle.ownsWorkspace()) return false
      const reloadRecoveryUnavailable = (
        e instanceof api.LlmEnhanceWaitError
        && submissionAttempted
        && (!scope || !durableRecoveryStored || !_hasOwnedStoredEnhanceOperation(scope))
      )
      if (!(e instanceof api.LlmEnhanceWaitError && submissionAttempted && !reloadRecoveryUnavailable)) {
        if (scope) await _removeStoredEnhanceOperation(scope)
      }
      if (e instanceof api.LlmEnhanceScopeError) return false
      console.error('Failed to enhance prompt:', e)
      window.alert(reloadRecoveryUnavailable
        ? 'Prompt Enhance stopped waiting. This browser could not reserve private reload recovery, so reloading will not resume this request. You can try Prompt Enhance again.'
        : (e instanceof Error ? e.message : 'Prompt enhancement failed'))
      return false
    } finally {
      if (lifecycle.ownsWorkspace()) {
        set({
          isEnhancing: false,
          enhanceStatus: null,
          enhanceRequestScope: null,
        })
      }
      lifecycle.dispose()
    }
  },
  resumeEnhancePrompt: async () => {
    const accountIdentityEpoch = _accountIdentityEpoch
    const accountFingerprint = _enhanceAccountFingerprint(get())
    const initialStored = _findStoredEnhanceOperation(get().activeWorkspace, accountFingerprint)
    if (
      !initialStored
      || _enhanceLlmRequestToken !== null
      || get().enhanceRequestScope !== null
    ) return false
    try {
      await _enhanceFingerprintSalt()
    } catch {
      window.alert(
        'Prompt Enhance recovery was not applied because this tab could not exclusively reclaim its original private recovery key. The prior result was left unchanged.',
      )
      return false
    }
    const stored = _findStoredEnhanceOperation(get().activeWorkspace, accountFingerprint)
    if (_enhanceLlmRequestToken !== null || get().enhanceRequestScope !== null) return false
    if (!stored || !_realmOwnsStoredEnhanceOperation(stored)) {
      window.alert(
        'Prompt Enhance recovery was not applied because this tab could not exclusively reclaim its original private recovery key. The prior result was left unchanged.',
      )
      return false
    }
    const scope: api.LlmEnhanceOperationScope = {
      requestId: stored.requestId,
      workspace: stored.workspace,
      projectInstance: stored.projectInstance,
    }
    const promptEditGeneration = _enhancePromptEditGeneration
    const lifecycle = _beginEnhanceLlmRequest(scope.workspace)
    _enhanceSubmissionAttemptedRequestId = scope.requestId
    set({
      isEnhancing: true,
      enhanceStatus: null,
      enhanceRequestScope: scope,
    })
    try {
      const result = await api.resumeLlmEnhancePrompt(scope, {
        signal: lifecycle.signal,
        onOperationStatus: status => {
          if (
            lifecycle.ownsWorkspace()
            && _sameEnhanceScope(get().enhanceRequestScope, scope)
          ) set({ enhanceStatus: _terminalEnhanceStatus(status) })
        },
      })
      if (
        !lifecycle.ownsWorkspace()
        || !_sameEnhanceScope(get().enhanceRequestScope, scope)
      ) return false
      if (
        !_accountIdentityIsCurrent(accountIdentityEpoch)
        || _enhanceAccountFingerprint(get()) !== stored.accountFingerprint
        || _enhancePromptEditGeneration !== promptEditGeneration
        || get().params.prompt !== result.original
        || (await _enhanceSettingsFingerprint(get())) !== stored.settingsFingerprint
      ) {
        await _removeStoredEnhanceOperation(scope)
        if (_enhanceFingerprintClaimRotatedStored) {
          window.alert(
            'Prompt Enhance recovery was not applied because this tab could not exclusively reclaim its original private recovery key. The prior result was left unchanged.',
          )
        }
        return false
      }
      await _removeStoredEnhanceOperation(scope)
      set(state => ({
        params: { ...state.params, prompt: result.enhanced },
        enhanceStatus: null,
      }))
      if (get().generationMode === 'audio' && get().ttsVoiceCount > 0) {
        get()._autoParseSpkeakerNames(result.enhanced, true)
      }
      return true
    } catch (error) {
      if (_isBrowserAbort(error) || !lifecycle.ownsWorkspace()) return false
      if (!(error instanceof api.LlmEnhanceWaitError) || !_enhanceReloadRecoveryAvailable) {
        await _removeStoredEnhanceOperation(scope)
      }
      if (!(error instanceof api.LlmEnhanceScopeError)) {
        console.error('Failed to resume prompt enhancement:', error)
        window.alert(error instanceof Error ? error.message : 'Prompt enhancement failed')
      }
      return false
    } finally {
      if (lifecycle.ownsWorkspace()) {
        set({
          isEnhancing: false,
          enhanceStatus: null,
          enhanceRequestScope: null,
        })
      }
      lifecycle.dispose()
    }
  },
  cancelEnhancePrompt: async () => {
    const accountFingerprint = _enhanceAccountFingerprint(get())
    const scope = get().enhanceRequestScope
      ?? (() => {
        const stored = _findStoredEnhanceOperation(get().activeWorkspace, accountFingerprint)
        return stored ? {
          requestId: stored.requestId,
          workspace: stored.workspace,
          projectInstance: stored.projectInstance,
        } : null
      })()
    if (!scope) {
      if (get().isEnhancing) {
        _enhanceStopWaiting?.()
        set({ isEnhancing: false, enhanceStatus: null, enhanceRequestScope: null })
      }
      return
    }
    if (scope.workspace !== get().activeWorkspace) return
    if (
      _sameEnhanceScope(get().enhanceRequestScope, scope)
      && _enhanceSubmissionAttemptedRequestId !== scope.requestId
    ) {
      _enhanceStopWaiting?.()
      set({ isEnhancing: false, enhanceStatus: null, enhanceRequestScope: null })
      await _removeStoredEnhanceOperation(scope)
      return
    }
    try {
      const status = await api.cancelLlmEnhancePrompt(scope, _enhanceWaitSignal ?? undefined)
      if (_sameEnhanceScope(get().enhanceRequestScope, scope)) {
        set({ enhanceStatus: _terminalEnhanceStatus(status) })
      }
    } catch (error) {
      if (!(error instanceof api.LlmEnhanceScopeError)) {
        console.error('Failed to cancel prompt enhancement:', error)
        window.alert(error instanceof Error ? error.message : 'Prompt enhancement could not be cancelled')
        return
      }
    }
    await _removeStoredEnhanceOperation(scope)
    _enhanceStopWaiting?.()
    set({ isEnhancing: false, enhanceStatus: null, enhanceRequestScope: null })
  },

  // Director (Music Video Director)
  sidebarMode: 'studio' as const,
  referenceReturnMode: 'studio' as const,
  directorStep: 'upload',
  directorAudioFile: null,
  directorAudioPath: null,
  directorAnalysis: null,
  directorPlannedClips: [],
  directorEnergyBias: 0,
  directorClipPlans: [],
  directorSceneDescription: '',
  directorVisualStyle: '',
  directorCustomVisualStyle: '',
  directorLoading: false,
  directorLoadingMessage: null,
  directorError: null,
  directorComponentError: null,
  directorReferenceImage: null,
  directorReferenceImagePath: null,
  directorCharacterRefs: [],
  directorCharacterRefPaths: [],
  directorCharacterRefLabels: [],
  directorLocationRefs: [],
  directorLocationRefPaths: [],
  directorLocationRefLabels: [],
  directorVoiceRef: null,
  directorVoiceRefPath: null,
  directorIdentityGuidanceScale: 3.0,
  setDirectorVoiceRef: (file) => {
    if (file) {
      set({ directorVoiceRef: file, directorVoiceRefPath: null })
    } else {
      set({ directorVoiceRef: null, directorVoiceRefPath: null })
    }
  },
  setDirectorIdentityGuidanceScale: (v) => set({ directorIdentityGuidanceScale: v }),
  directorClipImages: [],
  directorImageGenProgress: null,
  directorSpeakers: [],
  directorSpeakerMappings: [],
  // Defaults per user preference (2026-06): Auto ON (hands-off pipeline is
  // the common flow), Seamless OFF (separate per-clip generations are easier
  // to retake/review than one rolling-window render).
  directorAutoMode: true,
  directorSeamless: false,
  directorShotImageGuidance: 'auto' as DirectorShotImageGuidance,
  directorSkill: null,
  directorMusicSource: null,
  directorSongDescription: '',
  directorSongInstrumental: false,
  directorSongStyle: '',
  directorSongLyrics: '',
  directorSongDuration: 120,
  directorTrackGenerating: false,
  directorRequestId: _storedDirectorPreparation?.requestId ?? null,
  directorRequestWorkspace: _storedDirectorPreparation?.workspace ?? null,
  directorPreparationStatus: null,
  setDirectorMusicSource: (s) => set({ directorMusicSource: s }),
  setDirectorSongDescription: (v) => set({ directorSongDescription: v }),
  setDirectorSongInstrumental: (v) => set({ directorSongInstrumental: v }),
  setDirectorSongStyle: (v) => set({ directorSongStyle: v }),
  setDirectorSongLyrics: (v) => set({ directorSongLyrics: v }),
  setDirectorSongDuration: (v) => set({ directorSongDuration: v }),
  reconnectDirectorPreparation: async () => {
    const accountIdentityEpoch = _accountIdentityEpoch
    const { directorRequestId, directorRequestWorkspace } = get()
    if (!directorRequestId || !directorRequestWorkspace) return
    try {
      const status = await api.fetchDirectorPreparation(
        directorRequestId,
        directorRequestWorkspace,
      )
      if (!_accountIdentityIsCurrent(accountIdentityEpoch)) return
      if (get().directorRequestId === directorRequestId) {
        set({ directorPreparationStatus: status })
      }
      if (status.status === 'completed') {
        _stopDirectorPreparationPoll()
      } else if (_directorPreparationPoll === null) {
        _directorPreparationPoll = setInterval(() => {
          if (!_accountIdentityIsCurrent(accountIdentityEpoch)) {
            _stopDirectorPreparationPoll()
            return
          }
          void useStore.getState().reconnectDirectorPreparation()
        }, 2000)
      }
    } catch {
      // A remote reload can reach this poll before the exact project has
      // been selected/unlocked again. Keep the public cursor so the next
      // authorized reconnect can recover the chain instead of duplicating it.
    }
  },
  directorResolution: '720p' as ResolutionPreset,
  directorAspectRatio: '16:9' as AspectRatio,
  directorResolutionModelType: null,
  directorResolutionOptions: null,
  directorResolutionOptionsLoading: false,
  directorResolutionOptionsError: null,
  directorCapabilities: null,
  directorCapabilitiesExplicitOutput: null,
  directorCapabilitiesLoading: false,
  directorCapabilitiesLoadingExplicitOutput: null,
  directorCapabilitiesError: null,
  directorModelVisibilityRefreshPending: false,
  directorImageRolesConfigured: _initialDirectorImageRoles !== null,
  directorLegacyImageModel: _initialLegacyDirectorImageModel,
  directorImageCreatorModelOverride: _initialDirectorImageRoles?.creator_model_override || '',
  directorImageEditorModelOverride: _initialDirectorImageRoles?.editor_model_override || '',
  directorImageRoleLoras: {
    creator: _initialDirectorImageRoles?.creator_loras || [],
    editor: _initialDirectorImageRoles?.editor_loras || [],
  },
  directorVideoInferenceStepsByModel: {},
  directorVideoMaxShotFramesByModel: {},
  shortFilmCharacters: [],
  shortFilmPath: null,
  shortFilmTargetDuration: 30,
  shortFilmNarrative: false,
  pipelineId: null,
  pipelineStatus: null,
  pipelinePolling: false,
  setDirectorAutoMode: (v) => set({ directorAutoMode: v }),
  setDirectorSeamless: (v) => set({ directorSeamless: v }),
  setDirectorShotImageGuidance: (v) => set({ directorShotImageGuidance: v }),
  setDirectorSkill: (skill) => {
    set({ directorSkill: skill })
    // Music director default for image-to-video reference strength is
    // 0.7 (loosens the lock to the start frame so motion can develop
    // naturally) rather than 1.0 (rigid frame). Only initialize when
    // the param is unset OR still at the global 1.0 default — preserves
    // any value the user has already adjusted in this session.
    //
    // Goes through setParam (not a direct `params` write) so the value
    // propagates into savedParamsPerMode.video — that's what the
    // Director pipeline reads when building video_params for the
    // submission. Without this routing the slider would show 0.7 but
    // the pipeline would still send 1.0.
    if (skill === 'music_video') {
      const current = get().params.input_video_strength
      if (current == null || current === 1.0) {
        get().setParam('input_video_strength', 0.7)
      }
    }
  },
  setDirectorResolution: (preset) => set({ directorResolution: preset }),
  setDirectorAspectRatio: (ratio) => set({ directorAspectRatio: ratio }),
  loadDirectorResolutionOptions: async (modelType) => {
    const target = modelType.trim()
    const seq = ++_directorResolutionOptionsSeq
    if (!target) {
      set({
        directorResolutionModelType: null,
        directorResolutionOptions: null,
        directorResolutionOptionsLoading: false,
        directorResolutionOptionsError: null,
      })
      return null
    }
    set({
      directorResolutionModelType: target,
      directorResolutionOptions: null,
      directorResolutionOptionsLoading: true,
      directorResolutionOptionsError: null,
    })
    try {
      const options = await api.fetchModelOptions(target)
      if (seq !== _directorResolutionOptionsSeq) return null
      if (options.model_type !== target) {
        throw new Error('The server returned resolution options for a different model.')
      }
      set({
        directorResolutionModelType: target,
        directorResolutionOptions: options,
        directorResolutionOptionsLoading: false,
        directorResolutionOptionsError: null,
      })
      return options
    } catch (error) {
      if (seq !== _directorResolutionOptionsSeq) return null
      set({
        directorResolutionOptions: null,
        directorResolutionOptionsLoading: false,
        directorResolutionOptionsError: error instanceof Error
          ? error.message
          : 'Could not load Director resolution options.',
      })
      return null
    }
  },
  setDirectorVideoInferenceSteps: (modelType, steps) => set(s => {
    const next = { ...s.directorVideoInferenceStepsByModel }
    if (steps == null || !Number.isFinite(steps)) delete next[modelType]
    else next[modelType] = Math.max(1, Math.min(50, Math.round(steps)))
    return { directorVideoInferenceStepsByModel: next }
  }),
  setDirectorVideoMaxShotFrames: (modelType, frames) => set(s => {
    const next = { ...s.directorVideoMaxShotFramesByModel }
    if (frames == null || !Number.isFinite(frames) || frames <= 0) delete next[modelType]
    else next[modelType] = Math.round(frames)
    return { directorVideoMaxShotFramesByModel: next }
  }),

  loadDirectorCapabilities: async (options = {}) => {
    const current = get()
    const explicitOutput = options.explicitOutput ?? current.explicitOutput
    const key = _directorCapabilitiesKey(explicitOutput)
    if (!options.force
      && current.directorCapabilities
      && current.directorCapabilitiesExplicitOutput === explicitOutput) {
      return current.directorCapabilities
    }
    if (!options.force && _directorCapabilitiesInFlight[key]) {
      return _directorCapabilitiesInFlight[key].promise
    }
    const seq = ++_directorCapabilitiesSeq[key]
    if (get().explicitOutput === explicitOutput) {
      set({
        directorCapabilitiesLoading: true,
        directorCapabilitiesLoadingExplicitOutput: explicitOutput,
        directorCapabilitiesError: null,
      })
    }
    const request = api.fetchDirectorCapabilities(explicitOutput)
    const token = Symbol('director-capabilities-request')
    const promise = (async () => {
      try {
        const capabilities = await request
        if (seq === _directorCapabilitiesSeq[key] && get().explicitOutput === explicitOutput) {
          set({
            directorCapabilities: capabilities,
            directorCapabilitiesExplicitOutput: explicitOutput,
            directorCapabilitiesLoading: false,
            directorCapabilitiesLoadingExplicitOutput: null,
            directorCapabilitiesError: null,
          })
        }
        return capabilities
      } catch (error) {
        if (seq === _directorCapabilitiesSeq[key] && get().explicitOutput === explicitOutput) {
          set({
            directorCapabilities: null,
            directorCapabilitiesExplicitOutput: null,
            directorCapabilitiesLoading: false,
            directorCapabilitiesLoadingExplicitOutput: null,
            directorCapabilitiesError: error instanceof Error
              ? error.message : 'Director image capabilities are unavailable.',
          })
        }
        throw error
      } finally {
        if (_directorCapabilitiesInFlight[key]?.token === token) {
          delete _directorCapabilitiesInFlight[key]
        }
      }
    })()
    _directorCapabilitiesInFlight[key] = { token, promise }
    return promise
  },

  activateDirectorImageRoles: () => {
    const current = get()
    const persisted: PersistedDirectorImageRoles = {
      schema_version: 1,
      creator_model_override: current.directorImageCreatorModelOverride,
      editor_model_override: current.directorImageEditorModelOverride,
      creator_loras: current.directorImageRoleLoras.creator,
      editor_loras: current.directorImageRoleLoras.editor,
    }
    _saveDirectorImageRoles(persisted)
    set({ directorImageRolesConfigured: true, directorLegacyImageModel: '' })
  },

  setDirectorImageRoleModel: (role, modelType) => {
    set(role === 'creator'
      ? { directorImageCreatorModelOverride: modelType, directorComponentError: null, directorError: null }
      : { directorImageEditorModelOverride: modelType, directorComponentError: null, directorError: null })
    get().activateDirectorImageRoles()
  },

  setDirectorImageRoleLoras: (role, selections) => {
    set(s => ({
      directorImageRoleLoras: { ...s.directorImageRoleLoras, [role]: selections },
      directorComponentError: null,
      directorError: null,
    }))
    get().activateDirectorImageRoles()
  },

  selectDirectorVideoModel: async (modelType) => {
    set({ directorComponentError: null, directorError: null })
    if (get().generationMode === 'video') {
      await get().selectModel(modelType)
      return
    }
    set(s => ({
      selectedModelPerMode: { ...s.selectedModelPerMode, video: modelType },
    }))
    get().loadModelOptions(modelType)
    const s = get()
    _saveSettings({
      generationMode: s.generationMode,
      selectedModelPerMode: s.selectedModelPerMode,
      savedParamsPerMode: s.savedParamsPerMode,
      savedLoraPerMode: s.savedLoraPerMode,
    }, s.loraIdByFilename)
  },

  directorSetLora: (mode, activated_loras, loras_multipliers, loraWeights, availableLoras) => {
    const s = get()
    const updatedLoraPerMode = {
      ...s.savedLoraPerMode,
      [mode]: { activated_loras, loras_multipliers, loraWeights, availableLoras },
    }
    set({
      savedLoraPerMode: updatedLoraPerMode,
    })
    _saveSettings({
      generationMode: s.generationMode,
      selectedModelPerMode: s.selectedModelPerMode,
      savedParamsPerMode: s.savedParamsPerMode,
      savedLoraPerMode: updatedLoraPerMode,
    }, s.loraIdByFilename)
  },

  directorSetSpeakerMapping: (speakerId, name, role) => {
    set(s => ({
      directorSpeakerMappings: s.directorSpeakerMappings.map(m =>
        m.speakerId === speakerId ? { ...m, name, role } : m
      ),
    }))
  },

  directorInsertSpeakerMention: (speakerId) => {
    set(s => ({
      directorSceneDescription: s.directorSceneDescription
        ? `${s.directorSceneDescription} @${speakerId}`
        : `@${speakerId}`,
    }))
  },

  setSidebarMode: (mode) => {
    const current = get()
    const transition = resolveSidebarNavigation(current, mode)
    if (mode === 'director') {
      if (current.sidebarMode !== 'director') {
        if (!current.directorAudioFile && !transition.preserveDirectorState) {
          set({
            sidebarMode: transition.sidebarMode,
            referenceReturnMode: transition.referenceReturnMode,
            directorStep: 'upload',
            directorError: null,
          })
        } else {
          set({
            sidebarMode: transition.sidebarMode,
            referenceReturnMode: transition.referenceReturnMode,
          })
        }
      }
    } else if (mode === 'reference') {
      if (current.sidebarMode === 'reference') return
      set({
        sidebarMode: transition.sidebarMode,
        referenceReturnMode: transition.referenceReturnMode,
      })
    } else {
      set({
        sidebarMode: transition.sidebarMode,
        referenceReturnMode: transition.referenceReturnMode,
      })
    }
  },

  directorUploadAndAnalyze: async (file) => {
    const accountIdentityEpoch = _accountIdentityEpoch
    const requestWorkspace = get().activeWorkspace
    const ownsRequest = () => (
      _accountIdentityIsCurrent(accountIdentityEpoch)
      && get().activeWorkspace === requestWorkspace
    )
    _stopDirectorPreparationPoll()
    _storeDirectorPreparation(null, null)
    set({
      directorLoading: true,
      directorLoadingMessage: 'Uploading audio...',
      directorError: null,
      directorAudioFile: file,
      directorStep: 'analyze',
      directorRequestId: null,
      directorRequestWorkspace: null,
      directorPreparationStatus: null,
    })
    try {
      const uploaded = await api.uploadAudio(file)
      if (!ownsRequest()) return
      await get().directorAnalyzeAndPlan(uploaded.path, { transcribe: true })
    } catch {
      if (!ownsRequest()) return
      set({
        directorLoading: false,
        directorLoadingMessage: null,
        directorError: 'The audio file could not be uploaded or analyzed. Try again.',
        directorStep: 'upload',
      })
    }
  },

  // Shared analyze → section-classify → plan-structure chain. Works for an
  // UPLOADED track or a GENERATED one — both converge here with an audio path
  // on disk and land on the 'structure' step, so everything downstream is
  // identical regardless of where the audio came from.
  directorAnalyzeAndPlan: async (audioPath, opts) => {
    const transcribe = opts?.transcribe !== false
    const workspace = get().activeWorkspace
    const lifecycle = _beginDirectorLlmRequest(workspace)
    const directorRequestId = get().directorRequestWorkspace === workspace
      ? get().directorRequestId
      : null
    set({
      directorAudioPath: audioPath,
      directorLoading: true,
      directorLoadingMessage: 'Analyzing audio...',
      directorError: null,
      directorStep: 'analyze',
    })
    // Poll the backend's audio-analyze status during the long synchronous
    // /audio/analyze call so the UI can show "Loading transcription model
    // (first use downloads ~300MB)..." vs "Transcribing audio..." instead of
    // a single "Analyzing audio..." for the entire first-run wait. Cleared on
    // success or failure in the finally block.
    let analyzePoll: ReturnType<typeof setInterval> | null = null
    const startAnalyzePolling = () => {
      analyzePoll = setInterval(async () => {
        try {
          if (!lifecycle.ownsWorkspace()) return
          const status = await api.fetchAudioAnalyzeStatus()
          if (!status.step || !lifecycle.ownsWorkspace()) return  // No analyze in flight or just cleared
          set({ directorLoadingMessage: `${status.detail}...` })
        } catch { /* polling errors are non-fatal */ }
      }, 1000)
    }
    const stopAnalyzePolling = () => {
      if (analyzePoll !== null) {
        clearInterval(analyzePoll)
        analyzePoll = null
      }
    }
    try {
      startAnalyzePolling()
      let analysis = await api.analyzeAudio({
        audio_path: audioPath,
        workspace,
        director_request_id: directorRequestId || undefined,
        transcribe,
        extract_vocals: transcribe,
        lyrics_hint: opts?.lyricsHint || undefined,
      })
      stopAnalyzePolling()
      if (!lifecycle.ownsWorkspace()) return

      // Try LLM-based section classification (falls back to heuristic)
      if (analysis.lyrics && analysis.lyrics.length > 0) {
        try {
          set({ directorLoadingMessage: 'Identifying sections (LLM)...' })
          const classified = await api.classifySections({
            analysis,
            workspace,
            director_request_id: directorRequestId || undefined,
          }, { signal: lifecycle.signal })
          analysis = {
            ...analysis,
            sections: classified.sections,
            song_structure: classified.song_structure || null,
          }
        } catch (error) {
          if (_isBrowserAbort(error) || !lifecycle.ownsWorkspace()) return
          // LLM not available — keep heuristic labels
        }
      }

      if (!lifecycle.ownsWorkspace()) return
      set({ directorAnalysis: analysis })

      // Extract unique speakers from diarized lyrics
      const speakers: string[] = []
      if (analysis.lyrics) {
        const seen = new Set<string>()
        for (const seg of analysis.lyrics) {
          if (seg.speaker && !seen.has(seg.speaker)) {
            seen.add(seg.speaker)
            speakers.push(seg.speaker)
          }
        }
      }
      const speakerMappings: SpeakerMapping[] = speakers.map(s => ({
        speakerId: s,
        name: '',
        role: '' as const,
      }))
      set({ directorSpeakers: speakers, directorSpeakerMappings: speakerMappings })

      // Plan beat-aligned clip structure
      set({ directorLoadingMessage: 'Planning clip structure...' })
      const structure = await api.planClipStructure({
        analysis,
        workspace,
        director_request_id: directorRequestId || undefined,
        energy_bias: get().directorEnergyBias,
        fps: get().modelOptions?.fps ?? 16,
        frames_steps: get().modelOptions?.frames_steps ?? 4,
        frames_minimum: get().modelOptions?.frames_minimum ?? 5,
        // Authoritative: the Director's video model (modelOptions above may
        // belong to a music model — e.g. ACE-Step after generating a track —
        // whose fps fallback of 16 used to shrink clips by 16/25).
        video_model: get().selectedModelPerMode.video || undefined,
      }, { signal: lifecycle.signal })
      if (!lifecycle.ownsWorkspace()) return
      // Music Video skips the manual clip-structure review step entirely —
      // the beat-aligned clips are used as-is. Short Film keeps it.
      const skipStructure = get().directorSkill === 'music_video'
      set({
        directorPlannedClips: structure.clips,
        directorStep: skipStructure ? 'style' : 'structure',
        directorLoading: false,
        directorLoadingMessage: null,
      })
    } catch (e: unknown) {
      if (_isBrowserAbort(e) || !lifecycle.ownsWorkspace()) return
      const msg = e instanceof Error ? e.message : 'Analysis failed'
      console.error('Director analysis failed:', e)
      set({ directorLoading: false, directorLoadingMessage: null, directorError: msg, directorStep: 'upload' })
      throw e
    } finally {
      stopAnalyzePolling()
      lifecycle.dispose()
    }
  },

  // Music Video: write the song (Style + Lyrics) from the description, with
  // the optional reference image informing the style via the vision LLM.
  // Throws on failure so the UI can surface it inline.
  directorWriteSong: async () => {
    const s = get()
    const description = s.directorSongDescription.trim()
    if (!description) return
    const lifecycle = _beginDirectorLlmRequest(s.activeWorkspace)
    let refPath = s.directorReferenceImagePath
    try {
      if (!refPath && s.directorReferenceImage) {
        try {
          refPath = (await api.uploadImage(s.directorReferenceImage)).path
          if (lifecycle.ownsWorkspace()) set({ directorReferenceImagePath: refPath })
        } catch { /* image upload is best-effort */ }
      }
      if (!lifecycle.ownsWorkspace()) return
      set({ directorError: null })
      const r = await api.writeSong({
        workspace: s.activeWorkspace,
        description,
        instrumental: s.directorSongInstrumental,
        reference_image_path: refPath || undefined,
      }, { signal: lifecycle.signal })
      if (!lifecycle.ownsWorkspace()) return
      set({
        directorSongStyle: r.style || '',
        directorSongLyrics: s.directorSongInstrumental ? '[Instrumental]' : (r.lyrics || ''),
      })
    } catch (error) {
      if (_isBrowserAbort(error) || !lifecycle.ownsWorkspace()) return
      throw error
    } finally {
      lifecycle.dispose()
    }
  },

  // Music Video: generate the track (writing the song first if the user only
  // gave a description), then hand off to the SAME analyze → plan-structure
  // chain the upload flow uses. In Auto mode, continue straight into the
  // pipeline so it's fully hands-off.
  directorGenerateTrack: async () => {
    const accountIdentityEpoch = _accountIdentityEpoch
    const s = get()
    const workspace = s.activeWorkspace
    const ownsTrackRequest = () => (
      _accountIdentityIsCurrent(accountIdentityEpoch)
      && get().activeWorkspace === workspace
    )
    const instrumental = s.directorSongInstrumental
    const description = s.directorSongDescription.trim()
    const style = s.directorSongStyle.trim()
    const lyrics = s.directorSongLyrics.trim()
    const restoredRequest = s.directorRequestWorkspace === s.activeWorkspace
      && !!s.directorRequestId
    if (!description && !style && !lyrics && !restoredRequest) {
      set({ directorError: 'Describe your song (or fill in Style / Lyrics) first.' })
      return
    }
    // Upload the reference image so it can inform BOTH the music and visuals.
    let refPath = s.directorReferenceImagePath
    if (!refPath && s.directorReferenceImage) {
      try {
        refPath = (await api.uploadImage(s.directorReferenceImage)).path
        if (!ownsTrackRequest()) return
        set({ directorReferenceImagePath: refPath })
      } catch {
        if (!ownsTrackRequest()) return
        /* image upload is best-effort */
      }
    }
    if (!ownsTrackRequest()) return
    set({
      directorTrackGenerating: true,
      directorError: null,
      directorLoading: true,
      directorLoadingMessage: 'Generating music track…',
      directorStep: 'analyze',
    })
    try {
      const musicRequest: api.DirectorMusicRequest = {
        description: description || undefined,
        style: style || undefined,
        lyrics: instrumental ? '[Instrumental]' : (lyrics || undefined),
        instrumental,
        duration_seconds: s.directorSongDuration,
        reference_image_path: refPath || undefined,
        workspace: workspace || undefined,
        private_output: s.privateOutput,
        explicit_output: s.explicitOutput,
      }
      // Reuse a restored public cursor for this exact project. Creating a new
      // preparation here would orphan the durable chain after a reload and
      // could duplicate its completed music unit.
      let directorRequestId = get().directorRequestWorkspace === workspace
        ? get().directorRequestId
        : null
      let preparation: api.DirectorPreparationStatus
      if (directorRequestId) {
        preparation = await api.fetchDirectorPreparation(directorRequestId, workspace)
      } else {
        // Register the durable chain before any long model work and retain the
        // public id immediately. The generate call receives the exact same
        // body plus that server-issued id.
        preparation = await api.startDirectorPreparation(musicRequest)
        directorRequestId = preparation.director_request_id
      }
      if (!ownsTrackRequest()) return
      _storeDirectorPreparation(directorRequestId, workspace)
      set({
        directorRequestId,
        directorRequestWorkspace: workspace,
        directorPreparationStatus: preparation,
      })
      void get().reconnectDirectorPreparation()
      // generateMusic is a BLOCKING POST — the browser only learns the job id
      // when it finishes — but the backend registers the job immediately. Run
      // the same discovery a fresh browser uses at page load (reconnectJobs:
      // deduped, self-polling) so the gallery shows a live placeholder card
      // during the render instead of nothing until LLM planning.
      const trackPromise = api.generateMusic({
        ...musicRequest,
        director_request_id: directorRequestId,
      })
      setTimeout(() => { void get().reconnectJobs(accountIdentityEpoch) }, 1200)
      setTimeout(() => { void get().reconnectJobs(accountIdentityEpoch) }, 5000)
      const r = await trackPromise
      if (!ownsTrackRequest()) return
      // Persist the (possibly LLM-written) song back into the editable fields.
      set({
        directorSongStyle: r.style || style,
        directorSongLyrics: instrumental ? '[Instrumental]' : (r.lyrics || lyrics),
        directorTrackGenerating: false,
      })
      // Pre-fill the scene description from the song brief so the visual
      // planner has context. The 'style' step shows it (editable); Auto mode
      // uses it directly.
      if (!get().directorSceneDescription.trim() && description) {
        set({ directorSceneDescription: description })
      }
      // Same analyze → plan-structure chain as the upload flow. Instrumental
      // tracks skip transcription (no lyrics to find). For vocal tracks we
      // KNOW the written lyrics — seed Whisper with them so the timed
      // transcription matches what ACE-Step actually sang.
      await get().directorAnalyzeAndPlan(r.audio_path, {
        transcribe: !instrumental,
        lyricsHint: instrumental ? undefined : (r.lyrics || lyrics || undefined),
      })
      if (!ownsTrackRequest()) return
      // The song description doubles as the scene description, so the manual
      // 'style' step isn't needed — proceed straight to planning. Auto runs the
      // full server-side pipeline; manual runs the frontend plan→review chain.
      if (get().directorStep === 'style') {
        if (get().directorAutoMode) {
          await get().startDirectorPipeline()
        } else {
          await get().directorPlanPrompts()
        }
      }
    } catch (e: unknown) {
      if (!ownsTrackRequest()) return
      const msg = e instanceof Error ? e.message : 'Music generation failed'
      console.error('Director music generation failed:', e)
      set({
        directorTrackGenerating: false,
        directorLoading: false,
        directorLoadingMessage: null,
        directorError: msg,
        directorStep: 'upload',
      })
    }
  },

  directorSetEnergyBias: async (bias) => {
    const { directorAnalysis, activeWorkspace } = get()
    if (!directorAnalysis) return
    const lifecycle = _beginDirectorLlmRequest(activeWorkspace)
    set({ directorLoading: true, directorEnergyBias: bias })
    try {
      const structure = await api.planClipStructure({
        analysis: directorAnalysis,
        workspace: activeWorkspace,
        director_request_id: get().directorRequestWorkspace === activeWorkspace
          ? get().directorRequestId || undefined
          : undefined,
        energy_bias: bias,
        fps: get().modelOptions?.fps ?? 16,
        frames_steps: get().modelOptions?.frames_steps ?? 4,
        frames_minimum: get().modelOptions?.frames_minimum ?? 5,
        video_model: get().selectedModelPerMode.video || undefined,
      }, { signal: lifecycle.signal })
      if (!lifecycle.ownsWorkspace()) return
      set({ directorPlannedClips: structure.clips, directorLoading: false })
    } catch (e: unknown) {
      if (_isBrowserAbort(e) || !lifecycle.ownsWorkspace()) return
      const msg = e instanceof Error ? e.message : 'Failed to update structure'
      set({ directorLoading: false, directorError: msg })
    } finally {
      lifecycle.dispose()
    }
  },

  directorConfirmStructure: () => {
    set({ directorStep: 'style', directorLoading: false })
  },

  directorSetReferenceImage: (file) => set({
    directorReferenceImage: file,
    directorReferenceImagePath: null,
  }),
  directorAddCharacterRef: (file) => set(s => ({
    directorCharacterRefs: [...s.directorCharacterRefs, file],
    directorCharacterRefLabels: [...s.directorCharacterRefLabels, ''],
  })),
  directorRemoveCharacterRef: (index) => set(s => ({
    directorCharacterRefs: s.directorCharacterRefs.filter((_, i) => i !== index),
    directorCharacterRefPaths: s.directorCharacterRefPaths.filter((_, i) => i !== index),
    directorCharacterRefLabels: s.directorCharacterRefLabels.filter((_, i) => i !== index),
  })),
  directorSetCharacterRefLabel: (index, label) => set(s => {
    const labels = [...s.directorCharacterRefLabels]
    labels[index] = label
    return { directorCharacterRefLabels: labels }
  }),
  directorReorderCharacterRefs: (from, to) => set(s => {
    const refs = [...s.directorCharacterRefs]
    const paths = [...s.directorCharacterRefPaths]
    const labels = [...s.directorCharacterRefLabels]
    const [rF] = refs.splice(from, 1); refs.splice(to, 0, rF)
    const [pF] = paths.splice(from, 1); paths.splice(to, 0, pF)
    const [lF] = labels.splice(from, 1); labels.splice(to, 0, lF)
    return { directorCharacterRefs: refs, directorCharacterRefPaths: paths, directorCharacterRefLabels: labels }
  }),
  directorAddLocationRef: (file) => set(s => ({
    directorLocationRefs: [...s.directorLocationRefs, file],
    directorLocationRefLabels: [...s.directorLocationRefLabels, ''],
  })),
  directorRemoveLocationRef: (index) => set(s => ({
    directorLocationRefs: s.directorLocationRefs.filter((_, i) => i !== index),
    directorLocationRefPaths: s.directorLocationRefPaths.filter((_, i) => i !== index),
    directorLocationRefLabels: s.directorLocationRefLabels.filter((_, i) => i !== index),
  })),
  directorSetLocationRefLabel: (index, label) => set(s => {
    const labels = [...s.directorLocationRefLabels]
    labels[index] = label
    return { directorLocationRefLabels: labels }
  }),
  directorReorderLocationRefs: (from, to) => set(s => {
    const refs = [...s.directorLocationRefs]
    const paths = [...s.directorLocationRefPaths]
    const labels = [...s.directorLocationRefLabels]
    const [rF] = refs.splice(from, 1); refs.splice(to, 0, rF)
    const [pF] = paths.splice(from, 1); paths.splice(to, 0, pF)
    const [lF] = labels.splice(from, 1); labels.splice(to, 0, lF)
    return { directorLocationRefs: refs, directorLocationRefPaths: paths, directorLocationRefLabels: labels }
  }),

  directorSetSceneDescription: (prompt) => set({ directorSceneDescription: prompt }),
  setDirectorVisualStyle: (style) => set({ directorVisualStyle: style }),
  setDirectorCustomVisualStyle: (style) => set({ directorCustomVisualStyle: style }),

  // Helper: upload all Director reference images (main + characters + locations)
  _uploadDirectorRefs: async (lifecycle) => {
    const s = get()
    const requireOwnership = () => {
      if (lifecycle && !lifecycle.ownsWorkspace()) {
        throw new DOMException('The browser stopped waiting', 'AbortError')
      }
    }
    requireOwnership()
    // Upload main reference
    let refImagePath = s.directorReferenceImagePath
    if (s.directorReferenceImage && !refImagePath) {
      const uploaded = await api.uploadImage(s.directorReferenceImage)
      requireOwnership()
      refImagePath = uploaded.path
      set({ directorReferenceImagePath: refImagePath })
    }
    // Upload character refs
    const charPaths = [...s.directorCharacterRefPaths]
    for (let i = charPaths.length; i < s.directorCharacterRefs.length; i++) {
      requireOwnership()
      const uploaded = await api.uploadImage(s.directorCharacterRefs[i])
      requireOwnership()
      charPaths.push(uploaded.path)
    }
    if (charPaths.length > s.directorCharacterRefPaths.length) {
      set({ directorCharacterRefPaths: charPaths })
    }
    // Upload location refs
    const locPaths = [...s.directorLocationRefPaths]
    for (let i = locPaths.length; i < s.directorLocationRefs.length; i++) {
      requireOwnership()
      const uploaded = await api.uploadImage(s.directorLocationRefs[i])
      requireOwnership()
      locPaths.push(uploaded.path)
    }
    if (locPaths.length > s.directorLocationRefPaths.length) {
      set({ directorLocationRefPaths: locPaths })
    }
    return { refImagePath, charPaths, locPaths }
  },

  directorPlanPrompts: async () => {
    const { directorPlannedClips, directorSceneDescription, directorAnalysis, activeWorkspace } = get()
    if (!directorPlannedClips.length || !directorSceneDescription.trim()) return
    const lifecycle = _beginDirectorLlmRequest(activeWorkspace)
    const requestExplicitOutput = get().explicitOutput
    set({ directorLoading: true, directorError: null, directorStep: 'plan' })
    try {
      await _ensureSelectedH3StyleWorkflowReady(get)
      // Upload all reference images
      const { refImagePath, charPaths, locPaths } = await get()._uploadDirectorRefs(lifecycle)
      const { directorCharacterRefLabels: charLabels, directorLocationRefLabels: locLabels } = get()
      const extraRefs = {
        ...(charPaths.length > 0 ? { character_ref_paths: charPaths, character_ref_labels: charLabels } : {}),
        ...(locPaths.length > 0 ? { location_ref_paths: locPaths, location_ref_labels: locLabels } : {}),
      }

      // Build speaker_mappings from user-assigned names (only those with names filled in)
      const speakerMappings: Record<string, { name: string; role: string }> = {}
      for (const m of get().directorSpeakerMappings) {
        if (m.name.trim()) {
          speakerMappings[m.speakerId] = { name: m.name, role: m.role }
        }
      }

      // Generate both image and video prompts
      // ?? not || — an explicit user-toggled `false` must be respected
      // (legacy v1 path); only fall back to true when servicesConfig
      // hasn't loaded yet or the field is undefined.
      const useV2 = get().servicesConfig?.use_director_v2 ?? true
      let plans: Array<{ video_prompt: string; image_prompt: string }>

      if (useV2) {
        const imageRoleRequest = await _captureDirectorImageRoleRequest(get, requestExplicitOutput)
        // Director v2: structured planning → rendering → validation
        const result = await api.directorV2Plan({
          workspace: activeWorkspace,
          skill_type: 'music_video',
          video_model: get().selectedModelPerMode.video,
          ...imageRoleRequest.wire,
          h3_style_workflow: resolveH3StyleWorkflowRequest(
            get().h3StyleWorkflowCatalog,
            get().selectedModelPerMode.video,
            get().h3StyleWorkflow,
          ),
          explicit_output: requestExplicitOutput,
          clips: directorPlannedClips,
          scene_description: directorSceneDescription,
          visual_style: get().directorVisualStyle === 'custom'
            ? get().directorCustomVisualStyle.trim() || undefined
            : get().directorVisualStyle || undefined,
          lyrics: directorAnalysis?.lyrics ?? undefined,
          bpm: directorAnalysis?.bpm ?? 120,
          reference_image_path: refImagePath ?? undefined,
          ...extraRefs,
          speaker_mappings: Object.keys(speakerMappings).length > 0 ? speakerMappings : undefined,
          prompt_type: 'both',
        }, { signal: lifecycle.signal })
        plans = result.clip_plans.map(p => ({
          video_prompt: p.video_prompt || '',
          image_prompt: p.image_prompt || '',
        }))
      } else {
        // Legacy: direct LLM prompt generation
        const result = await api.planClipPromptsAndImages({
          workspace: activeWorkspace,
          clips: directorPlannedClips,
          scene_description: directorSceneDescription,
          visual_style: get().directorVisualStyle === 'custom'
            ? get().directorCustomVisualStyle.trim() || undefined
            : get().directorVisualStyle || undefined,
          explicit_output: requestExplicitOutput,
          lyrics: directorAnalysis?.lyrics ?? undefined,
          bpm: directorAnalysis?.bpm ?? 120,
          reference_image_path: refImagePath,
          ...extraRefs,
          speaker_mappings: Object.keys(speakerMappings).length > 0 ? speakerMappings : undefined,
          prompt_type: 'both',
        }, { signal: lifecycle.signal })
        plans = result.clip_plans.map(p => ({
          video_prompt: p.video_prompt || '',
          image_prompt: p.image_prompt || '',
        }))
      }
      if (!lifecycle.ownsWorkspace()) return
      set({
        directorClipPlans: plans,
        directorStep: 'review',
        directorLoading: false,
      })

      // Auto-mode: skip review, proceed to image gen. directorGenerateStartImages
      // now generates an establishing/anchor image first when no reference was
      // provided, so every clip shares a consistent look (instead of skipping
      // images entirely as it used to).
      if (get().directorAutoMode) {
        get().directorGenerateStartImages()
      }
    } catch (e: unknown) {
      if (_isBrowserAbort(e) || !lifecycle.ownsWorkspace()) return
      const msg = e instanceof Error ? e.message : 'Planning failed'
      console.error('Director planning failed:', e)
      set({ directorLoading: false, directorError: msg, directorStep: 'style' })
    } finally {
      lifecycle.dispose()
    }
  },

  directorPlanVideoPrompts: async () => {
    const { directorPlannedClips, directorSceneDescription, directorAnalysis, directorClipPlans, directorReferenceImagePath, activeWorkspace } = get()
    if (!directorPlannedClips.length || !directorClipPlans.length) return
    const lifecycle = _beginDirectorLlmRequest(activeWorkspace)
    const requestExplicitOutput = get().explicitOutput
    set({ directorLoading: true, directorError: null, directorStep: 'plan_video' })
    try {
      // Build speaker_mappings
      const speakerMappings: Record<string, { name: string; role: string }> = {}
      for (const m of get().directorSpeakerMappings) {
        if (m.name.trim()) {
          speakerMappings[m.speakerId] = { name: m.name, role: m.role }
        }
      }

      // Phase 2: generate video prompts, passing existing image prompts as context
      const existingImagePrompts = directorClipPlans.map(p => p.image_prompt || '')
      const { directorCharacterRefPaths: crp, directorLocationRefPaths: lrp } = get()
      const result = await api.planClipPromptsAndImages({
        workspace: activeWorkspace,
        clips: directorPlannedClips,
        scene_description: directorSceneDescription,
        visual_style: get().directorVisualStyle === 'custom'
          ? get().directorCustomVisualStyle.trim() || undefined
          : get().directorVisualStyle || undefined,
        explicit_output: requestExplicitOutput,
        lyrics: directorAnalysis?.lyrics ?? undefined,
        bpm: directorAnalysis?.bpm ?? 120,
        reference_image_path: directorReferenceImagePath,
        ...(crp.length > 0 ? { character_ref_paths: crp } : {}),
        ...(lrp.length > 0 ? { location_ref_paths: lrp } : {}),
        speaker_mappings: Object.keys(speakerMappings).length > 0 ? speakerMappings : undefined,
        prompt_type: 'video',
        existing_image_prompts: existingImagePrompts,
      }, { signal: lifecycle.signal })
      // Merge video prompts into existing clip plans
      const updatedPlans = directorClipPlans.map((plan, i) => ({
        ...plan,
        video_prompt: result.clip_plans[i]?.video_prompt || '',
      }))
      if (!lifecycle.ownsWorkspace()) return
      set({
        directorClipPlans: updatedPlans,
        directorStep: 'review_video',
        directorLoading: false,
      })

      // Auto-mode: skip review, apply to editor and start generation
      if (get().directorAutoMode) {
        get().directorGenerate()
      }
    } catch (e: unknown) {
      if (_isBrowserAbort(e) || !lifecycle.ownsWorkspace()) return
      const msg = e instanceof Error ? e.message : 'Video prompt planning failed'
      console.error('Director video planning failed:', e)
      set({ directorLoading: false, directorError: msg, directorStep: 'generate_images' })
    } finally {
      lifecycle.dispose()
    }
  },

  directorEditClipPlan: (index, field, value) => {
    set(s => {
      const plans = [...s.directorClipPlans]
      if (plans[index]) {
        plans[index] = { ...plans[index], [field]: value }
      }
      return { directorClipPlans: plans }
    })
  },

  directorGenerateStartImages: async () => {
    const accountIdentityEpoch = _accountIdentityEpoch
    const initialState = get()
    const {
      directorClipPlans, directorPlannedClips, directorResolution,
      directorAspectRatio, directorSceneDescription,
    } = initialState
    if (!directorClipPlans.length) return

    // Seal one request-local role snapshot for the entire preview sequence.
    // The server selects Creator for reference-free anchors and Editor when
    // authorized references are present, then derives role-specific defaults.
    const requestWorkspace = initialState.activeWorkspace
    const ownsDirectorRequest = () => (
      _accountIdentityIsCurrent(accountIdentityEpoch)
      && get().activeWorkspace === requestWorkspace
    )
    const requireDirectorRequest = () => {
      if (!ownsDirectorRequest()) throw new Error('Account changed during Director generation')
    }
    const requestExplicitOutput = initialState.explicitOutput
    const requestPrivateOutput = initialState.privateOutput
    const imageRoleRequest = await _captureDirectorImageRoleRequest(get, requestExplicitOutput)
    if (!ownsDirectorRequest()) return
    const imageResolutionOptions = new Map<string, Promise<ModelOptions>>()

    const exactImageResolution = async (modelType: string): Promise<string> => {
      let request = imageResolutionOptions.get(modelType)
      if (!request) {
        request = api.fetchModelOptions(modelType)
        imageResolutionOptions.set(modelType, request)
      }
      const options = await request
      requireDirectorRequest()
      const resolution = resolveDeclaredResolution(
        options, directorResolution, directorAspectRatio,
      )
      if (!resolution) {
        throw new Error('The selected resolution is unavailable for this Director image role.')
      }
      return resolution
    }

    // Submit one image generation through the shared queue-aware job tracker,
    // then download the terminal result as a File.
    const genImage = async (prompt: string, refs: string[], label: string): Promise<{ file: File; filename: string }> => {
      const effectiveModel = refs.length > 0
        ? imageRoleRequest.effective_editor_model
        : imageRoleRequest.effective_creator_model
      const directorRes = await exactImageResolution(effectiveModel)
      requireDirectorRequest()
      const genParams = {
        ...imageRoleRequest.wire,
        prompt,
        image_refs: refs,
        image_mode: 1,
        // 'KI' carries an image reference; plain T2I (the anchor) needs no ref flag.
        video_prompt_type: refs.length ? 'KI' : '',
        resolution: directorRes,
        seed: -1,
        settings_version: 2.52,
        generation_mode: 'image',
        workspace: requestWorkspace,
        private_output: requestPrivateOutput,
        explicit_output: requestExplicitOutput,
        repeat_generation: 1,
        negative_prompt: '',
        video_length: 1,
      }
      const { job_id } = await api.submitGeneration(genParams)
      requireDirectorRequest()
      const directorJob: GenerationJob = {
        id: job_id,
        status: 'queued',
        progress: 0,
        step: 0,
        totalSteps: 0,
        phase: '',
        message: `Queued ${label}...`,
        outputFiles: [],
        error: null,
        oomInfo: null,
        promptPreview: prompt,
        modelType: effectiveModel,
        generationMode: 'image',
        workspace: requestWorkspace,
      }
      set(s => ({
        jobs: s.jobs.some(job => job.id === job_id)
          ? s.jobs
          : [directorJob, ...s.jobs],
        isGenerating: true,
      }))
      const terminalStatus = _waitForTerminalJobStatus(job_id, 600_000)
      get()._pollRecoveredJob(job_id)
      window.dispatchEvent(new CustomEvent('maestro:queue-refresh'))
      let status: api.ApiJobStatus
      try {
        status = await terminalStatus
        requireDirectorRequest()
      } catch (error) {
        if (error instanceof Error && error.message.endsWith('timed out')) {
          throw new Error(`${label} generation timed out`)
        }
        throw error
      }
      if (status.status !== 'completed') {
        throw new Error(status.error || `${label} generation ${status.status}`)
      }
      const outputFiles = status.output_files
      if (outputFiles.length === 0) throw new Error(`No output file for ${label}`)
      const filename = outputFiles[0]
      const imgRes = await fetch(api.getFileUrl(filename))
      requireDirectorRequest()
      const blob = await imgRes.blob()
      requireDirectorRequest()
      const file = new File([blob], filename, { type: blob.type || 'image/png' })
      return { file, filename }
    }

    // Auto-unload LLM before GPU-heavy image generation to free VRAM
    if (get().llmStatus?.loaded) {
      try {
        await api.unloadLlm()
        if (!ownsDirectorRequest()) return
        set({ llmStatus: { loaded: false, model_id: null, device: null, provider: '' } })
      } catch { /* best-effort */ }
    }

    set({ directorStep: 'generate_images', directorLoading: true, directorError: null, directorClipImages: [], directorImageGenProgress: null })

    try {
      // If no reference image was provided, generate a single establishing /
      // "anchor" image from the scene description and adopt it as the reference,
      // so every clip's start image shares a consistent look.
      let anchorMade = false
      if (!get().directorReferenceImage && !get().directorReferenceImagePath) {
        anchorMade = true
        set({
          directorImageGenProgress: {
            current: 0,
            total: directorClipPlans.length + 1,
            currentClipLabel: 'Establishing image…',
            status: 'generating',
          },
        })
        const anchorPrompt = directorSceneDescription.trim() || directorClipPlans[0]?.image_prompt || 'cinematic establishing shot'
        const { file: anchorFile } = await genImage(anchorPrompt, [], 'Establishing image')
        if (!ownsDirectorRequest()) return
        // Adopt as the reference image (uploaded just below via _uploadDirectorRefs).
        set({ directorReferenceImage: anchorFile, directorReferenceImagePath: null })
      }

      // Upload all reference images (main/anchor + character + location)
      const { refImagePath: refPath, charPaths, locPaths } = await get()._uploadDirectorRefs({
        ownsWorkspace: ownsDirectorRequest,
      })
      if (!ownsDirectorRequest()) return
      const allRefs = [refPath, ...charPaths, ...locPaths].filter(Boolean) as string[]

      const total = directorClipPlans.length + (anchorMade ? 1 : 0)
      const base = anchorMade ? 1 : 0
      const generatedImages: DirectorClipImage[] = []

      // Generate one start image per clip sequentially.
      for (let i = 0; i < directorClipPlans.length; i++) {
        const clip = directorPlannedClips[i]
        const plan = directorClipPlans[i]
        const clipLabel = `Clip ${i + 1} (${clip?.section_label || 'verse'})`
        set({
          directorImageGenProgress: { current: base + i, total, currentClipLabel: clipLabel, status: 'generating' },
        })
        const { file, filename } = await genImage(plan.image_prompt, allRefs, clipLabel)
        if (!ownsDirectorRequest()) return
        generatedImages.push({ clipIndex: i, prompt: plan.image_prompt, file, filename })
        set({ directorClipImages: [...generatedImages] })
      }

      set({
        directorImageGenProgress: { current: total, total, currentClipLabel: '', status: 'done' },
        directorLoading: false,
      })

      // Video prompts already generated in the combined LLM pass — go straight to review
      const hasVideoPrompts = get().directorClipPlans.some(p => p.video_prompt)
      if (hasVideoPrompts) {
        set({ directorStep: 'review_video' })
        if (get().directorAutoMode) {
          get().directorGenerate()
        }
      } else {
        // Fallback: if video prompts are missing, plan them separately
        get().directorPlanVideoPrompts()
      }
    } catch (e: unknown) {
      if (!ownsDirectorRequest()) return
      const msg = e instanceof Error ? e.message : 'Image generation failed'
      console.error('Director image generation failed:', e)
      set({
        directorLoading: false,
        directorError: msg,
        directorImageGenProgress: get().directorImageGenProgress
          ? { ...get().directorImageGenProgress!, status: 'error' }
          : null,
      })
    }
  },

  directorApplyToClips: () => {
    const { directorClipPlans, directorPlannedClips, directorAnalysis, directorClipImages,
            directorAudioPath, directorAudioFile, directorSeamless,
            directorVideoInferenceStepsByModel,
            selectedModelPerMode, savedParamsPerMode, savedLoraPerMode } = get()
    if (!directorClipPlans.length) return

    // Use the authoritative video-mode selection seeded during model hydration.
    const videoModel = (selectedModelPerMode.video || '').trim()
    if (!videoModel) {
      const error = new api.DirectorRequestError('director_model_unavailable', 'video_model')
      set({
        directorError: error.message,
        directorComponentError: { code: error.code, component: error.component, message: error.message },
      })
      return
    }
    const savedVideoParams = savedParamsPerMode.video
    const videoParams = savedVideoParams?.model_type === videoModel
      ? { ...savedVideoParams }
      : {}
    const directorSteps = directorVideoInferenceStepsByModel[videoModel]
    if (directorSteps != null) videoParams.num_inference_steps = directorSteps
    const videoLora = savedLoraPerMode.video

    const fps = get().modelOptions?.fps ?? 16
    const totalDuration = directorAnalysis?.duration ?? 180
    const totalDurationCapped = Math.min(totalDuration, 300)

    // Build clips with per-clip durations and images
    const clips: MultiClip[] = directorClipPlans.map((plan, i) => {
      const plannedClip = directorPlannedClips[i]
      const clipImage = directorClipImages.find(img => img.clipIndex === i)

      // Seamless mode: use next clip's start image as this clip's end image
      let endImage: File | null = null
      if (directorSeamless && i < directorClipPlans.length - 1) {
        const nextClipImage = directorClipImages.find(img => img.clipIndex === i + 1)
        endImage = nextClipImage?.file ?? null
      }

      return {
        prompt: plan.video_prompt,
        startImage: clipImage?.file ?? null,
        startImagePath: null,
        endImage,
        endImagePath: null,
        durationFrames: plannedClip?.duration_frames,
      }
    })

    // Build per-clip frame counts for variable-duration support
    const perClipFrames = clips.map(c => c.durationFrames ?? Math.round(5 * fps))
    const totalFrames = perClipFrames.reduce((sum, f) => sum + f, 0)
    const maxClipFrames = Math.max(...perClipFrames)

    // Auto-set soundtrack mode with the already-uploaded audio
    const audioParams: Record<string, unknown> = {}
    if (directorAudioPath) {
      audioParams.audio_prompt_type = 'A'
      audioParams.audio_guide = directorAudioPath
    }

    set(s => ({
      params: {
        ...s.params,
        ...(videoModel ? { model_type: videoModel } : {}),
        ...(videoParams || {}),
        ...(videoLora ? { activated_loras: videoLora.activated_loras, loras_multipliers: (videoLora.loras_multipliers || '').split(' ').map(m => m.split(';')[0]).join(' ') } : {}),
        image_mode: 2,
        video_length: totalFrames,
        sliding_window_size: maxClipFrames,
        per_clip_frames: perClipFrames,
        ...audioParams,
      },
      clips,
      singlePromptMode: false,
      durationSeconds: totalDurationCapped,
      slidingWindowSeconds: maxClipFrames / fps,
      audioGuideFilename: directorAudioFile?.name ?? null,
      sidebarMode: 'studio' as const,
    }))
  },

  directorGenerate: () => {
    const { directorClipPlans, directorPlannedClips, directorAnalysis,
            directorClipImages, directorAudioPath, directorAudioFile,
            directorSeamless, directorResolution, directorAspectRatio,
            directorVideoInferenceStepsByModel,
            selectedModelPerMode, savedParamsPerMode, savedLoraPerMode } = get()
    if (!directorClipPlans.length) return

    // Use the authoritative video-mode selection, then override resolution
    // with Director's choice.
    const videoModel = (selectedModelPerMode.video || '').trim()
    if (!videoModel) {
      const error = new api.DirectorRequestError('director_model_unavailable', 'video_model')
      set({
        directorError: error.message,
        directorComponentError: { code: error.code, component: error.component, message: error.message },
      })
      return
    }
    const resolutionState = get()
    const directorVideoOptions = (
      resolutionState.directorResolutionModelType === videoModel
      && resolutionState.directorResolutionOptions?.model_type === videoModel
    ) ? resolutionState.directorResolutionOptions : null
    const directorRes = resolveDeclaredResolution(
      directorVideoOptions, directorResolution, directorAspectRatio,
    )
    if (!directorRes) {
      set({
        directorError: 'The selected resolution is unavailable for this Director video model.',
      })
      return
    }
    const savedVideoParams = savedParamsPerMode.video || {}
    const matchingVideoParams = savedVideoParams.model_type === videoModel ? savedVideoParams : {}
    const defaultSteps = directorVideoOptions?.default_num_inference_steps ?? 8
    const videoParams = {
      ...matchingVideoParams,
      num_inference_steps: directorVideoOptions?.lock_inference_steps
        ? defaultSteps
        : (directorVideoInferenceStepsByModel[videoModel] ?? defaultSteps),
      guidance_scale: matchingVideoParams.guidance_scale ?? directorVideoOptions?.default_guidance_scale ?? 1,
      resolution: directorRes,
    }
    const videoLora = savedLoraPerMode.video

    const fps = directorVideoOptions?.fps ?? 16
    const totalDuration = directorAnalysis?.duration ?? 180
    const totalDurationCapped = Math.min(totalDuration, 300)

    const clips: MultiClip[] = directorClipPlans.map((plan, i) => {
      const plannedClip = directorPlannedClips[i]
      const clipImage = directorClipImages.find(img => img.clipIndex === i)

      // Seamless mode: use next clip's start image as this clip's end image
      let endImage: File | null = null
      if (directorSeamless && i < directorClipPlans.length - 1) {
        const nextClipImage = directorClipImages.find(img => img.clipIndex === i + 1)
        endImage = nextClipImage?.file ?? null
      }

      return {
        prompt: plan.video_prompt,
        startImage: clipImage?.file ?? null,
        startImagePath: null,
        endImage,
        endImagePath: null,
        durationFrames: plannedClip?.duration_frames,
      }
    })

    const perClipFrames = clips.map(c => c.durationFrames ?? Math.round(5 * fps))
    const totalFrames = perClipFrames.reduce((sum, f) => sum + f, 0)
    const maxClipFrames = Math.max(...perClipFrames)

    const audioParams: Record<string, unknown> = {}
    if (get().shortFilmPath === 'story') {
      // Path C: LTX generates video + audio from text (dialogue in quotes)
      audioParams.audio_prompt_type = ''
    } else if (directorAudioPath) {
      audioParams.audio_prompt_type = 'A'
      audioParams.audio_guide = directorAudioPath
    }

    // Apply director video post-processing to shared state (read by startGeneration)
    const vidSelfRefiner = get().directorVideoSelfRefiner

    set(s => ({
      params: {
        ...s.params,
        ...(videoModel ? { model_type: videoModel } : {}),
        ...(videoParams || {}),
        ...(videoLora ? { activated_loras: videoLora.activated_loras, loras_multipliers: (videoLora.loras_multipliers || '').split(' ').map(m => m.split(';')[0]).join(' ') } : {}),
        image_mode: 2,
        video_length: totalFrames,
        sliding_window_size: maxClipFrames,
        per_clip_frames: perClipFrames,
        self_refiner_setting: vidSelfRefiner,
        ...audioParams,
      },
      clips,
      singlePromptMode: false,
      durationSeconds: totalDurationCapped,
      slidingWindowSeconds: maxClipFrames / fps,
      audioGuideFilename: directorAudioFile?.name ?? null,
      // Apply director video post-processing to shared state
      spatialUpsampling: get().directorVideoSpatialUpsampling,
      filmGrainIntensity: get().directorVideoFilmGrainIntensity,
      filmGrainSaturation: get().directorVideoFilmGrainSaturation,
    }))

    setTimeout(() => get().startGeneration(), 200)
  },

  directorReset: () => {
    _stopDirectorPreparationPoll()
    _storeDirectorPreparation(null, null)
    _directorPipelineLifecycleToken = null
    ++_directorResolutionOptionsSeq
    set({
      sidebarMode: 'studio' as const,
      directorStep: 'upload',
      directorAudioFile: null,
      directorAudioPath: null,
      directorAnalysis: null,
      directorPlannedClips: [],
      directorEnergyBias: 0,
      directorClipPlans: [],
      directorSceneDescription: '',
      directorVisualStyle: '',
      directorCustomVisualStyle: '',
      directorLoading: false,
      directorError: null,
      directorComponentError: null,
      directorResolutionModelType: null,
      directorResolutionOptions: null,
      directorResolutionOptionsLoading: false,
      directorResolutionOptionsError: null,
      directorReferenceImage: null,
      directorReferenceImagePath: null,
      directorCharacterRefs: [],
      directorCharacterRefPaths: [],
      directorCharacterRefLabels: [],
      directorLocationRefs: [],
      directorLocationRefPaths: [],
      directorLocationRefLabels: [],
      directorVoiceRef: null,
      directorVoiceRefPath: null,
      directorClipImages: [],
      directorImageGenProgress: null,
      directorSpeakers: [],
      directorSpeakerMappings: [],
      directorAutoMode: true,
      directorSeamless: false,
      directorShotImageGuidance: 'auto' as DirectorShotImageGuidance,
      directorSkill: null,
      directorMusicSource: null,
      directorSongDescription: '',
      directorSongInstrumental: false,
      directorSongStyle: '',
      directorSongLyrics: '',
      directorSongDuration: 120,
      directorTrackGenerating: false,
      directorRequestId: null,
      directorRequestWorkspace: null,
      directorPreparationStatus: null,
      shortFilmCharacters: [],
      shortFilmPath: null,
      shortFilmTargetDuration: 30,
      shortFilmNarrative: false,
      pipelineId: null,
      pipelineStatus: null,
      pipelinePolling: false,
    })
  },

  // --- Short Film Director actions ---

  shortFilmSetCharacters: (characters) => set({ shortFilmCharacters: characters }),
  shortFilmSetPath: (path) => set({ shortFilmPath: path }),
  shortFilmSetTargetDuration: (duration) => set({ shortFilmTargetDuration: duration }),
  shortFilmSetNarrative: (v) => set({ shortFilmNarrative: v }),

  shortFilmUploadAndAnalyze: async (file) => {
    const requestWorkspace = get().activeWorkspace
    const lifecycle = _beginDirectorLlmRequest(requestWorkspace)
    set({
      directorLoading: true,
      directorLoadingMessage: 'Uploading audio...',
      directorError: null,
      directorAudioFile: file,
      directorStep: 'analyze',
    })
    // Same polling pattern as directorUploadAndAnalyze — see comment
    // there for the full rationale on /api/v1/audio/analyze/status.
    let analyzePoll: ReturnType<typeof setInterval> | null = null
    const startAnalyzePolling = () => {
      analyzePoll = setInterval(async () => {
        try {
          if (!lifecycle.ownsWorkspace()) return
          const status = await api.fetchAudioAnalyzeStatus()
          if (!status.step || !lifecycle.ownsWorkspace()) return
          set({ directorLoadingMessage: `${status.detail}...` })
        } catch { /* polling errors are non-fatal */ }
      }, 1000)
    }
    const stopAnalyzePolling = () => {
      if (analyzePoll !== null) {
        clearInterval(analyzePoll)
        analyzePoll = null
      }
    }
    try {
      const uploaded = await api.uploadAudio(file)
      if (!lifecycle.ownsWorkspace()) return
      set({ directorAudioPath: uploaded.path, directorLoadingMessage: 'Analyzing audio...' })

      startAnalyzePolling()
      const analysis = await api.analyzeAudio({
        audio_path: uploaded.path,
        workspace: requestWorkspace,
        transcribe: true,
        extract_vocals: true,
      })
      stopAnalyzePolling()
      if (!lifecycle.ownsWorkspace()) return

      set({ directorAnalysis: analysis })

      // Extract unique speakers from diarized lyrics
      const speakers: string[] = []
      if (analysis.lyrics) {
        const seen = new Set<string>()
        for (const seg of analysis.lyrics) {
          if (seg.speaker && !seen.has(seg.speaker)) {
            seen.add(seg.speaker)
            speakers.push(seg.speaker)
          }
        }
      }
      const speakerMappings: SpeakerMapping[] = speakers.map(s => ({
        speakerId: s,
        name: '',
        role: 'speaking' as const,
      }))
      set({ directorSpeakers: speakers, directorSpeakerMappings: speakerMappings })

      // Plan dialogue-paced clip structure (not beat-aligned)
      set({ directorLoadingMessage: 'Planning scenes...' })
      const structure = await api.planDialogueScenes({
        workspace: requestWorkspace,
        analysis,
        pacing_bias: get().directorEnergyBias,
        fps: get().modelOptions?.fps ?? 16,
        frames_steps: get().modelOptions?.frames_steps ?? 4,
        frames_minimum: get().modelOptions?.frames_minimum ?? 5,
      }, { signal: lifecycle.signal })
      if (!lifecycle.ownsWorkspace()) return
      set({
        directorPlannedClips: structure.clips,
        directorStep: 'structure',
        directorLoading: false,
        directorLoadingMessage: null,
      })
    } catch (e: unknown) {
      if (_isBrowserAbort(e) || !lifecycle.ownsWorkspace()) return
      const msg = e instanceof Error ? e.message : 'Analysis failed'
      console.error('Short film analysis failed:', e)
      set({ directorLoading: false, directorLoadingMessage: null, directorError: msg, directorStep: 'upload' })
    } finally {
      stopAnalyzePolling()
      lifecycle.dispose()
    }
  },

  shortFilmSetPacingBias: async (bias) => {
    const { directorAnalysis, activeWorkspace } = get()
    if (!directorAnalysis) return
    const lifecycle = _beginDirectorLlmRequest(activeWorkspace)
    set({ directorLoading: true, directorEnergyBias: bias })
    try {
      const structure = await api.planDialogueScenes({
        workspace: activeWorkspace,
        analysis: directorAnalysis,
        pacing_bias: bias,
        fps: get().modelOptions?.fps ?? 16,
        frames_steps: get().modelOptions?.frames_steps ?? 4,
        frames_minimum: get().modelOptions?.frames_minimum ?? 5,
      }, { signal: lifecycle.signal })
      if (!lifecycle.ownsWorkspace()) return
      set({ directorPlannedClips: structure.clips, directorLoading: false })
    } catch (e: unknown) {
      if (_isBrowserAbort(e) || !lifecycle.ownsWorkspace()) return
      const msg = e instanceof Error ? e.message : 'Failed to update structure'
      set({ directorLoading: false, directorError: msg })
    } finally {
      lifecycle.dispose()
    }
  },

  shortFilmPlanPrompts: async () => {
    const { directorPlannedClips, directorSceneDescription, directorAnalysis,
            shortFilmCharacters, activeWorkspace } = get()
    if (!directorPlannedClips.length || !directorSceneDescription.trim()) return
    const lifecycle = _beginDirectorLlmRequest(activeWorkspace)
    const requestExplicitOutput = get().explicitOutput
    set({ directorLoading: true, directorError: null, directorStep: 'plan' })
    try {
      await _ensureSelectedH3StyleWorkflowReady(get)
      // Upload all reference images
      const { refImagePath, charPaths, locPaths } = await get()._uploadDirectorRefs(lifecycle)
      const { directorCharacterRefLabels: charLabels, directorLocationRefLabels: locLabels } = get()
      const extraRefs = {
        ...(charPaths.length > 0 ? { character_ref_paths: charPaths, character_ref_labels: charLabels } : {}),
        ...(locPaths.length > 0 ? { location_ref_paths: locPaths, location_ref_labels: locLabels } : {}),
      }

      // Build speaker mappings
      const speakerMappings: Record<string, { name: string; role: string }> = {}
      for (const m of get().directorSpeakerMappings) {
        if (m.name.trim()) {
          speakerMappings[m.speakerId] = { name: m.name, role: m.role }
        }
      }

      // Generate prompts
      // ?? not || — an explicit user-toggled `false` must be respected
      // (legacy v1 path); only fall back to true when servicesConfig
      // hasn't loaded yet or the field is undefined.
      const useV2 = get().servicesConfig?.use_director_v2 ?? true
      let plans: Array<{ video_prompt: string; image_prompt: string }>

      if (useV2) {
        const imageRoleRequest = await _captureDirectorImageRoleRequest(get, requestExplicitOutput)
        const result = await api.directorV2Plan({
          workspace: activeWorkspace,
          skill_type: 'short_film',
          video_model: get().selectedModelPerMode.video,
          ...imageRoleRequest.wire,
          h3_style_workflow: resolveH3StyleWorkflowRequest(
            get().h3StyleWorkflowCatalog,
            get().selectedModelPerMode.video,
            get().h3StyleWorkflow,
          ),
          explicit_output: requestExplicitOutput,
          clips: directorPlannedClips,
          scene_description: directorSceneDescription,
          visual_style: get().directorVisualStyle === 'custom'
            ? get().directorCustomVisualStyle.trim() || undefined
            : get().directorVisualStyle || undefined,
          lyrics: directorAnalysis?.lyrics ?? undefined,
          reference_image_path: refImagePath ?? undefined,
          ...extraRefs,
          speaker_mappings: Object.keys(speakerMappings).length > 0 ? speakerMappings : undefined,
          characters: shortFilmCharacters.length > 0 ? shortFilmCharacters : undefined,
          prompt_type: 'both',
        }, { signal: lifecycle.signal })
        plans = result.clip_plans.map(p => ({
          video_prompt: p.video_prompt || '',
          image_prompt: p.image_prompt || '',
        }))
      } else {
        const result = await api.planShortFilmPrompts({
          workspace: activeWorkspace,
          clips: directorPlannedClips,
          scene_description: directorSceneDescription,
          visual_style: get().directorVisualStyle === 'custom'
            ? get().directorCustomVisualStyle.trim() || undefined
            : get().directorVisualStyle || undefined,
          explicit_output: requestExplicitOutput,
          lyrics: directorAnalysis?.lyrics ?? undefined,
          reference_image_path: refImagePath,
          ...extraRefs,
          speaker_mappings: Object.keys(speakerMappings).length > 0 ? speakerMappings : undefined,
          characters: shortFilmCharacters.length > 0 ? shortFilmCharacters : undefined,
          prompt_type: 'both',
        }, { signal: lifecycle.signal })
        plans = result.clip_plans.map(p => ({
          video_prompt: p.video_prompt || '',
          image_prompt: p.image_prompt || '',
        }))
      }
      if (!lifecycle.ownsWorkspace()) return
      set({
        directorClipPlans: plans,
        directorStep: 'review',
        directorLoading: false,
      })

      // Auto-mode: skip review
      if (get().directorAutoMode) {
        if (get().directorReferenceImage) {
          get().directorGenerateStartImages()
        } else {
          set({ directorStep: 'review_video' })
          get().directorGenerate()
        }
      }
    } catch (e: unknown) {
      if (_isBrowserAbort(e) || !lifecycle.ownsWorkspace()) return
      const msg = e instanceof Error ? e.message : 'Planning failed'
      console.error('Short film planning failed:', e)
      set({ directorLoading: false, directorError: msg, directorStep: 'style' })
    } finally {
      lifecycle.dispose()
    }
  },

  shortFilmPlanVideoPrompts: async () => {
    const { directorPlannedClips, directorSceneDescription, directorAnalysis,
            directorClipPlans, directorReferenceImagePath, shortFilmCharacters, activeWorkspace } = get()
    if (!directorPlannedClips.length || !directorClipPlans.length) return
    const lifecycle = _beginDirectorLlmRequest(activeWorkspace)
    const requestExplicitOutput = get().explicitOutput
    set({ directorLoading: true, directorError: null, directorStep: 'plan_video' })
    try {
      const speakerMappings: Record<string, { name: string; role: string }> = {}
      for (const m of get().directorSpeakerMappings) {
        if (m.name.trim()) {
          speakerMappings[m.speakerId] = { name: m.name, role: m.role }
        }
      }

      const existingImagePrompts = directorClipPlans.map(p => p.image_prompt || '')
      const { directorCharacterRefPaths: crp2, directorLocationRefPaths: lrp2 } = get()
      const result = await api.planShortFilmPrompts({
        workspace: activeWorkspace,
        clips: directorPlannedClips,
        scene_description: directorSceneDescription,
        visual_style: get().directorVisualStyle === 'custom'
          ? get().directorCustomVisualStyle.trim() || undefined
          : get().directorVisualStyle || undefined,
        explicit_output: requestExplicitOutput,
        lyrics: directorAnalysis?.lyrics ?? undefined,
        reference_image_path: directorReferenceImagePath,
        ...(crp2.length > 0 ? { character_ref_paths: crp2 } : {}),
        ...(lrp2.length > 0 ? { location_ref_paths: lrp2 } : {}),
        speaker_mappings: Object.keys(speakerMappings).length > 0 ? speakerMappings : undefined,
        characters: shortFilmCharacters.length > 0 ? shortFilmCharacters : undefined,
        prompt_type: 'video',
        existing_image_prompts: existingImagePrompts,
      }, { signal: lifecycle.signal })
      const updatedPlans = directorClipPlans.map((plan, i) => ({
        ...plan,
        video_prompt: result.clip_plans[i]?.video_prompt || '',
      }))
      if (!lifecycle.ownsWorkspace()) return
      set({
        directorClipPlans: updatedPlans,
        directorStep: 'review_video',
        directorLoading: false,
      })

      if (get().directorAutoMode) {
        get().directorGenerate()
      }
    } catch (e: unknown) {
      if (_isBrowserAbort(e) || !lifecycle.ownsWorkspace()) return
      const msg = e instanceof Error ? e.message : 'Video prompt planning failed'
      console.error('Short film video planning failed:', e)
      set({ directorLoading: false, directorError: msg, directorStep: 'generate_images' })
    } finally {
      lifecycle.dispose()
    }
  },

  shortFilmPlanFromStory: async () => {
    const { directorSceneDescription,
            shortFilmCharacters, shortFilmTargetDuration, shortFilmNarrative, activeWorkspace } = get()
    if (!directorSceneDescription.trim()) return
    const lifecycle = _beginDirectorLlmRequest(activeWorkspace)
    const requestExplicitOutput = get().explicitOutput
    set({ directorLoading: true, directorError: null, directorStep: 'plan' })
    try {
      await _ensureSelectedH3StyleWorkflowReady(get)
      // Upload all reference images
      const { refImagePath, charPaths, locPaths } = await get()._uploadDirectorRefs(lifecycle)
      const { directorCharacterRefLabels: charLabels, directorLocationRefLabels: locLabels } = get()
      const extraRefs = {
        ...(charPaths.length > 0 ? { character_ref_paths: charPaths, character_ref_labels: charLabels } : {}),
        ...(locPaths.length > 0 ? { location_ref_paths: locPaths, location_ref_labels: locLabels } : {}),
      }

      // ?? not || — an explicit user-toggled `false` must be respected
      // (legacy v1 path); only fall back to true when servicesConfig
      // hasn't loaded yet or the field is undefined.
      const useV2 = get().servicesConfig?.use_director_v2 ?? true
      let plans: Array<{ video_prompt: string; image_prompt: string }>
      let storyClips: PlannedClip[] | undefined

      if (useV2) {
        const imageRoleRequest = await _captureDirectorImageRoleRequest(get, requestExplicitOutput)
        const result = await api.directorV2Plan({
          workspace: activeWorkspace,
          skill_type: 'short_film',
          video_model: get().selectedModelPerMode.video,
          ...imageRoleRequest.wire,
          h3_style_workflow: resolveH3StyleWorkflowRequest(
            get().h3StyleWorkflowCatalog,
            get().selectedModelPerMode.video,
            get().h3StyleWorkflow,
          ),
          explicit_output: requestExplicitOutput,
          scene_description: directorSceneDescription,
          story_description: directorSceneDescription,
          visual_style: get().directorVisualStyle === 'custom'
            ? get().directorCustomVisualStyle.trim() || undefined
            : get().directorVisualStyle || undefined,
          characters: shortFilmCharacters.length > 0 ? shortFilmCharacters : undefined,
          reference_image_path: refImagePath ?? undefined,
          ...extraRefs,
          target_duration: shortFilmTargetDuration,
          narrative_mode: shortFilmNarrative,
          fps: get().modelOptions?.fps ?? 24,
          frames_steps: get().modelOptions?.frames_steps ?? 4,
          frames_minimum: get().modelOptions?.frames_minimum ?? 5,
          prompt_type: 'both',
        }, { signal: lifecycle.signal })
        plans = result.clip_plans.map(p => ({
          video_prompt: p.video_prompt || '',
          image_prompt: p.image_prompt || '',
        }))
        // Extract clips from production plan shots
        const pp = result.production_plan as {
          shots?: Array<{
            duration_sec?: number
            metadata?: { duration_frames?: number }
            narrative_role?: string
            scene_type?: string
          }>
        }
        if (pp?.shots) {
          let cumulative = 0
          storyClips = pp.shots.map((s): PlannedClip => {
            const clip = {
              start: cumulative,
              end: cumulative + (s.duration_sec || 15),
              duration_frames: s.metadata?.duration_frames || Math.round((s.duration_sec || 15) * (get().modelOptions?.fps ?? 24)),
              label: s.narrative_role || s.scene_type || 'scene',
              beat_count: 0,
            }
            cumulative += s.duration_sec || 15
            return clip as unknown as PlannedClip
          })
        }
      } else {
        const result = await api.planShortFilmScript({
          workspace: activeWorkspace,
          story_description: directorSceneDescription,
          visual_style: get().directorVisualStyle === 'custom'
            ? get().directorCustomVisualStyle.trim() || undefined
            : get().directorVisualStyle || undefined,
          explicit_output: requestExplicitOutput,
          characters: shortFilmCharacters.length > 0 ? shortFilmCharacters : undefined,
          reference_image_path: refImagePath ?? undefined,
          ...extraRefs,
          target_duration: shortFilmTargetDuration,
          narrative_mode: shortFilmNarrative,
          fps: get().modelOptions?.fps ?? 24,
          frames_steps: get().modelOptions?.frames_steps ?? 4,
          frames_minimum: get().modelOptions?.frames_minimum ?? 5,
        }, { signal: lifecycle.signal })
        storyClips = result.clips
        plans = result.clip_plans.map(p => ({
          video_prompt: p.video_prompt || '',
          image_prompt: p.image_prompt || '',
        }))
      }

      if (!lifecycle.ownsWorkspace()) return
      set({
        directorPlannedClips: storyClips || get().directorPlannedClips,
        directorClipPlans: plans,
        directorStep: 'review',
        directorLoading: false,
      })

      // Auto-mode: skip review steps
      if (get().directorAutoMode) {
        if (get().directorReferenceImage) {
          get().directorGenerateStartImages()
        } else {
          set({ directorStep: 'review_video' })
          get().directorGenerate()
        }
      }
    } catch (e: unknown) {
      if (_isBrowserAbort(e) || !lifecycle.ownsWorkspace()) return
      const msg = e instanceof Error ? e.message : 'Story planning failed'
      console.error('Short film story planning failed:', e)
      set({ directorLoading: false, directorError: msg, directorStep: 'style' })
    } finally {
      lifecycle.dispose()
    }
  },

  selectModel: async (modelType) => {
    const seq = ++_h3ProfileApplySeq
    ++_h3EstimateSeq
    ++_h3CompatibilitySeq
    const before = get()
    const currentMode = before.generationMode
    if (
      currentMode === 'video'
      && H3_STUDIO_MODELS.has(before.params.model_type)
      && H3_STUDIO_MODELS.has(modelType)
      && before.h3SelectedProfile !== 'custom'
    ) {
      set({
        h3ProfileApplying: before.h3SelectedProfile,
        modelOptionsLoading: true,
        h3EstimateError: null,
      })
      try {
        const request = _buildH3EstimateRequest(before, modelType)
        const requestSignature = _stableJson(request)
        const response = await api.estimateH3Performance(request)
        if (seq !== _h3ProfileApplySeq) return false
        if (requestSignature !== _stableJson(_buildH3EstimateRequest(get(), modelType))) {
          return get().selectModel(modelType)
        }
        const requested = response.profiles.find(
          profile => profile.id === before.h3SelectedProfile,
        )
        const resolved = requested?.available
          ? requested
          : requested?.fallback_profile_id
            ? response.profiles.find(profile => (
                profile.id === requested.fallback_profile_id && profile.available
              ))
            : undefined
        if (!resolved) {
          set({
            h3ProfileApplying: null,
            modelOptionsLoading: false,
            h3EstimateError: requested?.fallback_reason || 'The server did not provide a compatible H3 profile for this model.',
          })
          return false
        }
        ++_modelOptionsSeq
        ++_modelDefaultsSeq
        return _applyH3ServerProfile(resolved, resolved.id, seq, get, set)
      } catch (error) {
        if (seq === _h3ProfileApplySeq) {
          set({
            h3ProfileApplying: null,
            modelOptionsLoading: false,
            h3EstimateError: error instanceof Error ? error.message : 'Could not reconcile the H3 model and profile',
          })
        }
        return false
      }
    }
    set(s => ({
      params: {
        ...s.params,
        model_type: modelType,
        activated_loras: [],
        loras_multipliers: '',
      },
      selectedModelPerMode: { ...s.selectedModelPerMode, [currentMode]: modelType },
      loraWeights: {},
      availableLoras: [],
      h3SelectedProfile: 'custom',
      h3ProfileApplying: null,
    }))
    // Virtual SFX models don't have backend model options or LoRAs
    if (!sfxModelTypes.has(modelType)) {
      get().loadLoras(modelType)
      _applyModelDefaults(get, set, modelType)
      get().loadModelOptions(modelType)
    }
    // Persist to localStorage
    const s = get()
    _saveSettings({
      generationMode: s.generationMode,
      selectedModelPerMode: s.selectedModelPerMode,
      savedParamsPerMode: s.savedParamsPerMode,
      savedLoraPerMode: s.savedLoraPerMode,
    }, s.loraIdByFilename)
    return true
  },

  // Workspaces
  workspaces: [],
  activeWorkspace: 'default',
  browsingUploads: false,
  loadWorkspaces: async () => {
    const requestSequence = ++_workspaceLoadSequence
    const accountIdentityEpoch = _accountIdentityEpoch
    try {
      const data = await api.fetchWorkspaces()
      if (
        requestSequence !== _workspaceLoadSequence
        || accountIdentityEpoch !== _accountIdentityEpoch
      ) return false
      const before = get()
      const previousActive = before.activeWorkspace
      const projectChanged = data.active !== previousActive
      const nextWorkspaces = new Map(data.workspaces.map(workspace => [workspace.name, workspace]))
      const accountProjectAccessActive = api.isAccountProjectAccessActive(
        before.accessContext,
        before.accountProjectMigration,
      )
      const revokedWorkspaces = accountProjectAccessActive ? [] : before.workspaces
        .filter(workspace => (
          workspace.password_protected
          && workspace.unlocked === true
          && nextWorkspaces.get(workspace.name)?.unlocked !== true
        ))
        .map(workspace => workspace.name)
      const nextActiveWorkspace = nextWorkspaces.get(previousActive)
      const previousAccessRevoked = Boolean(previousActive) && (
        nextActiveWorkspace === undefined
        || (!accountProjectAccessActive && nextActiveWorkspace.unlocked === false)
      )
      const clearPendingPlan = before.pendingH3PlanWorkspace != null && (
        projectChanged || (
          previousAccessRevoked && before.pendingH3PlanWorkspace === previousActive
        )
      )
      if (projectChanged || previousAccessRevoked) {
        _directorPipelineLifecycleToken = null
        _dashboardPipelineLoadToken += 1
        _dashboardPipelineListLoadToken += 1
      }
      if (clearPendingPlan) _h3PlanReviewSequence += 1
      set(state => {
        const remainingJobs = previousAccessRevoked
          ? state.jobs.filter(job => job.workspace && job.workspace !== previousActive)
          : state.jobs
        return {
          workspaces: data.workspaces,
          activeWorkspace: data.active,
          selectedOutputKeys: [],
          gallerySelectionMode: false,
          ...(projectChanged || previousAccessRevoked ? {
            browsingUploads: false,
            outputs: [],
            outputsTotal: 0,
            selectedOutput: 0,
            selectedOutputMeta: null,
            pipelineId: null,
            pipelineStatus: null,
            pipelinePolling: false,
            directorLoading: false,
            dashboardOpen: false,
            dashboardPipelineList: [],
            dashboardPipelineListRead: { workspace: '', generation: _dashboardPipelineListLoadToken, status: 'idle' },
            dashboardSelectedPipeline: null,
            dashboardLoading: false,
          } : {}),
          ...(previousAccessRevoked ? {
            jobs: remainingJobs,
            isGenerating: remainingJobs.some(_isActiveGenerationJob),
          } : {}),
          ...(clearPendingPlan ? {
            pendingH3Plan: null,
            pendingH3PlanEstimate: null,
            pendingH3PlanJobId: null,
            pendingH3PlanWorkspace: null,
            h3PlanReviewLoading: false,
            h3PlanReviewError: null,
          } : {}),
        }
      })
      for (const workspace of new Set([
        ...revokedWorkspaces,
        ...(projectChanged && previousActive ? [previousActive] : []),
        ...(previousAccessRevoked ? [previousActive] : []),
      ])) hidePrivatePreviewsForWorkspace(workspace)
      // A reload keeps only the opaque Enhance request fence. Resume status
      // waiting after the authoritative active project has been restored.
      void get().resumeEnhancePrompt()
      if (get().accessContext?.remote && data.active) {
        void get().loadOutputs()
      }
      return true
    } catch (e) {
      if (
        requestSequence === _workspaceLoadSequence
        && accountIdentityEpoch === _accountIdentityEpoch
      ) console.error('Failed to load workspaces:', e)
      return false
    }
  },
  switchWorkspace: async (name) => {
    // Virtual "Uploads" view: browse the uploads folder WITHOUT touching
    // the server-side active workspace — generations keep saving to the
    // real workspace; uploads are read-only in the gallery.
    if (name === '__uploads__') {
      const activeWorkspace = get().activeWorkspace
      if (activeWorkspace) hidePrivatePreviewsForWorkspace(activeWorkspace)
      set({ browsingUploads: true, outputs: [], outputsTotal: 0, selectedOutput: 0, selectedOutputMeta: null, selectedOutputKeys: [] })
      return get().loadOutputs()
    }
    _directorPipelineLifecycleToken = null
    _dashboardPipelineLoadToken += 1
    _dashboardPipelineListLoadToken += 1
    try {
      const previousWorkspace = get().activeWorkspace
      await api.setActiveWorkspace(name)
      if (previousWorkspace && previousWorkspace !== name) {
        hidePrivatePreviewsForWorkspace(previousWorkspace)
      }
      set({
        browsingUploads: false,
        activeWorkspace: name,
        outputs: [],
        outputsTotal: 0,
        selectedOutput: 0,
        selectedOutputMeta: null,
        selectedOutputKeys: [],
        pipelineId: null,
        pipelineStatus: null,
        pipelinePolling: false,
        directorLoading: false,
        dashboardOpen: false,
        dashboardPipelineList: [],
        dashboardPipelineListRead: { workspace: '', generation: _dashboardPipelineListLoadToken, status: 'idle' },
        dashboardSelectedPipeline: null,
        dashboardLoading: false,
      })
      const loaded = await get().loadOutputs()
      void get().loadWorkspaces()
      return loaded && get().activeWorkspace === name && !get().browsingUploads
    } catch (e) {
      console.error('Failed to switch workspace:', e)
      return false
    }
  },
  createWorkspace: async (name, password) => {
    _directorPipelineLifecycleToken = null
    _dashboardPipelineLoadToken += 1
    _dashboardPipelineListLoadToken += 1
    try {
      const previousWorkspace = get().activeWorkspace
      const state = get()
      const accountProjectAccessActive = api.isAccountProjectAccessActive(
        state.accessContext,
        state.accountProjectMigration,
      )
      await api.createWorkspace(name, accountProjectAccessActive ? undefined : password, 'device')
      await api.setActiveWorkspace(name)
      if (previousWorkspace && previousWorkspace !== name) {
        hidePrivatePreviewsForWorkspace(previousWorkspace)
      }
      set({
        browsingUploads: false,
        activeWorkspace: name,
        outputs: [],
        outputsTotal: 0,
        selectedOutput: 0,
        selectedOutputMeta: null,
        selectedOutputKeys: [],
        pipelineId: null,
        pipelineStatus: null,
        pipelinePolling: false,
        directorLoading: false,
        dashboardOpen: false,
        dashboardPipelineList: [],
        dashboardPipelineListRead: { workspace: '', generation: _dashboardPipelineListLoadToken, status: 'idle' },
        dashboardSelectedPipeline: null,
        dashboardLoading: false,
      })
      get().loadOutputs()
      get().loadWorkspaces()
    } catch (e) {
      console.error('Failed to create workspace:', e)
      throw e
    }
  },
  unlockWorkspace: async (name, password, remember) => {
    const result = await api.unlockWorkspace(name, password, remember)
    // Re-authentication always starts from a blurred preview session.
    hidePrivatePreviewsForWorkspace(name)
    if (!await get().loadWorkspaces()) {
      throw new Error('Project unlocked, but its current access state could not be refreshed')
    }
    return result
  },
  lockWorkspace: async (name) => {
    const result = await api.lockWorkspace(name)
    hidePrivatePreviewsForWorkspace(name)
    const before = get()
    const lockedActiveWorkspace = name === before.activeWorkspace
    const clearPendingPlan = before.pendingH3PlanWorkspace === name
    if (clearPendingPlan) _h3PlanReviewSequence += 1
    set(state => {
      const remainingJobs = state.jobs.filter(job => (
        job.workspace ? job.workspace !== name : !lockedActiveWorkspace
      ))
      return {
        workspaces: state.workspaces.map(workspace => workspace.name === name ? {
          ...workspace,
          unlocked: false,
          remember_policy: null,
          unlock_expires_at: null,
          unlock_idle_expires_at: null,
        } : workspace),
        ...(lockedActiveWorkspace && state.accessContext?.remote ? { activeWorkspace: '' } : {}),
        jobs: remainingJobs,
        isGenerating: remainingJobs.some(_isActiveGenerationJob),
        ...(clearPendingPlan ? {
          pendingH3Plan: null,
          pendingH3PlanEstimate: null,
          pendingH3PlanJobId: null,
          pendingH3PlanWorkspace: null,
          h3PlanReviewLoading: false,
          h3PlanReviewError: null,
        } : {}),
      }
    })
    if (lockedActiveWorkspace) {
      _directorPipelineLifecycleToken = null
      _dashboardPipelineLoadToken += 1
      _dashboardPipelineListLoadToken += 1
      set({
        browsingUploads: false,
        outputs: [],
        outputsTotal: 0,
        selectedOutput: 0,
        selectedOutputMeta: null,
        selectedOutputKeys: [],
        gallerySelectionMode: false,
        pipelineId: null,
        pipelineStatus: null,
        pipelinePolling: false,
        directorLoading: false,
        dashboardOpen: false,
        dashboardPipelineList: [],
        dashboardPipelineListRead: { workspace: '', generation: _dashboardPipelineListLoadToken, status: 'idle' },
        dashboardSelectedPipeline: null,
        dashboardLoading: false,
      })
    }
    if (!await get().loadWorkspaces()) {
      throw new Error('Project locked, but its current access state could not be refreshed')
    }
    return result
  },
  lockAllWorkspaces: async () => {
    const before = get()
    const lockedWorkspaces = new Set(before.workspaces
      .filter(workspace => workspace.password_protected && workspace.unlocked)
      .map(workspace => workspace.name))
    const lockedActiveWorkspace = lockedWorkspaces.has(before.activeWorkspace)
    const clearPendingPlan = before.pendingH3PlanWorkspace != null
      && lockedWorkspaces.has(before.pendingH3PlanWorkspace)
    const result = await api.lockAllWorkspaces()
    for (const workspace of lockedWorkspaces) hidePrivatePreviewsForWorkspace(workspace)
    if (lockedActiveWorkspace) {
      _directorPipelineLifecycleToken = null
      _dashboardPipelineLoadToken += 1
      _dashboardPipelineListLoadToken += 1
    }
    if (clearPendingPlan) _h3PlanReviewSequence += 1
    set(state => {
      const remainingJobs = state.jobs.filter(job => (
        job.workspace
          ? !lockedWorkspaces.has(job.workspace)
          : !lockedActiveWorkspace
      ))
      return {
        workspaces: state.workspaces.map(workspace => lockedWorkspaces.has(workspace.name) ? {
          ...workspace,
          unlocked: false,
          remember_policy: null,
          unlock_expires_at: null,
          unlock_idle_expires_at: null,
        } : workspace),
        ...(lockedActiveWorkspace && state.accessContext?.remote ? { activeWorkspace: '' } : {}),
        ...(lockedActiveWorkspace ? {
          browsingUploads: false,
          outputs: [],
          outputsTotal: 0,
          selectedOutput: 0,
          selectedOutputMeta: null,
          selectedOutputKeys: [],
          gallerySelectionMode: false,
          pipelineId: null,
          pipelineStatus: null,
          pipelinePolling: false,
          directorLoading: false,
          dashboardOpen: false,
          dashboardPipelineList: [],
          dashboardPipelineListRead: { workspace: '', generation: _dashboardPipelineListLoadToken, status: 'idle' },
          dashboardSelectedPipeline: null,
          dashboardLoading: false,
        } : {}),
        jobs: remainingJobs,
        isGenerating: remainingJobs.some(_isActiveGenerationJob),
        ...(clearPendingPlan ? {
          pendingH3Plan: null,
          pendingH3PlanEstimate: null,
          pendingH3PlanJobId: null,
          pendingH3PlanWorkspace: null,
          h3PlanReviewLoading: false,
          h3PlanReviewError: null,
        } : {}),
      }
    })
    if (!await get().loadWorkspaces()) {
      throw new Error('Projects locked, but their current access state could not be refreshed')
    }
    return result
  },
  deleteWorkspace: async (name) => {
    // The server refuses 'default', refuses while anything generates, and
    // auto-switches to default when the deleted workspace was active —
    // its switched_to_default answer is authoritative (a client-side
    // activeWorkspace comparison could disagree after a desync and would
    // widen it by force-resetting state the server never changed).
    if (name === get().activeWorkspace) {
      _directorPipelineLifecycleToken = null
      _dashboardPipelineLoadToken += 1
      _dashboardPipelineListLoadToken += 1
    }
    const result = await api.deleteWorkspace(name)
    hidePrivatePreviewsForWorkspace(name)
    if (result.switched_to_default) {
      set({
        browsingUploads: false,
        activeWorkspace: 'default',
        outputs: [],
        outputsTotal: 0,
        selectedOutput: 0,
        selectedOutputMeta: null,
        selectedOutputKeys: [],
        pipelineId: null,
        pipelineStatus: null,
        pipelinePolling: false,
        directorLoading: false,
        dashboardOpen: false,
        dashboardPipelineList: [],
        dashboardPipelineListRead: { workspace: '', generation: _dashboardPipelineListLoadToken, status: 'idle' },
        dashboardSelectedPipeline: null,
        dashboardLoading: false,
      })
      get().loadOutputs()
    }
    get().loadWorkspaces()
  },

  storageDashboardOpen: false,
  setStorageDashboardOpen: (open) => set({ storageDashboardOpen: open }),

  loraPickerSort: (() => {
    try { return localStorage.getItem('maestro_lora_picker_sort') === 'newest' ? 'newest' as const : 'name' as const } catch { return 'name' as const }
  })(),
  setLoraPickerSort: (sort) => {
    try { localStorage.setItem('maestro_lora_picker_sort', sort) } catch { /* private mode */ }
    set({ loraPickerSort: sort })
  },

  outputs: [],
  outputsTotal: 0,
  gallerySelectionMode: false,
  selectedOutputKeys: [],
  setGallerySelectionMode: (enabled) => set({
    gallerySelectionMode: enabled,
    ...(!enabled ? { selectedOutputKeys: [] } : {}),
  }),
  toggleOutputSelection: (output) => set(s => {
    const key = `${output.workspace}\0${output.name}`
    const selected = new Set(s.selectedOutputKeys)
    if (selected.has(key)) selected.delete(key)
    else selected.add(key)
    return { selectedOutputKeys: [...selected], gallerySelectionMode: true }
  }),
  selectAllLoadedOutputs: () => set(s => ({
    gallerySelectionMode: true,
    selectedOutputKeys: s.filteredOutputs().map(output => `${output.workspace}\0${output.name}`),
  })),
  clearOutputSelection: () => set({ selectedOutputKeys: [] }),
  bulkMoveSelectedOutputs: async (targetWorkspace) => {
    const selected = new Set(get().selectedOutputKeys)
    const items = get().outputs
      .filter(output => selected.has(`${output.workspace}\0${output.name}`))
      .map(output => ({ name: output.name, workspace: output.workspace, revision: output.revision }))
    if (!items.length) return []
    const response = await api.bulkMoveOutputs(items, targetWorkspace)
    const errors = response.results.filter(result => !result.ok).map(result => result.error || result.name)
    set({ selectedOutputKeys: [], gallerySelectionMode: false })
    await get().loadOutputs()
    get().loadWorkspaces()
    return errors
  },
  bulkSetSelectedPrivacy: async (privateOutput) => {
    const selected = new Set(get().selectedOutputKeys)
    const items = get().outputs
      .filter(output => selected.has(`${output.workspace}\0${output.name}`))
      .map(output => ({ name: output.name, workspace: output.workspace, revision: output.revision }))
    if (!items.length) return []
    const response = await api.bulkSetOutputPrivacy(items, privateOutput)
    const errors = response.results.filter(result => !result.ok).map(result => result.error || result.name)
    set({ selectedOutputKeys: [], gallerySelectionMode: false })
    await get().loadOutputs()
    return errors
  },
  bulkDeleteSelectedOutputs: async () => {
    const selected = new Set(get().selectedOutputKeys)
    const items = get().outputs
      .filter(output => selected.has(`${output.workspace}\0${output.name}`))
      .map(output => ({ name: output.name, workspace: output.workspace, revision: output.revision }))
    if (!items.length) return []
    const response = await api.bulkDeleteOutputs(items, true)
    const errors = response.results.filter(result => !result.ok).map(result => result.error || result.name)
    set({ selectedOutputKeys: [], gallerySelectionMode: false })
    await get().loadOutputs()
    get().loadWorkspaces()
    return errors
  },
  selectedOutput: 0,
  setSelectedOutput: (i) => {
    set({ selectedOutput: i })
    const outputs = get().filteredOutputs()
    const output = outputs[i]
    if (output) {
      get().loadOutputMetadata(output.name)
    } else {
      _metadataRequestGeneration++
      set({ selectedOutputMeta: null, selectedOutputMetaName: null, metadataLoading: false })
    }
  },
  mediaFilter: 'all',
  outputArtifactScope: 'final',
  outputSearchQuery: '',
  setMediaFilter: (f) => {
    if (f === get().mediaFilter) return
    _metadataRequestGeneration++
    set({
      mediaFilter: f,
      selectedOutput: 0,
      selectedOutputMeta: null,
      selectedOutputMetaName: null,
      metadataLoading: false,
      selectedOutputKeys: [],
    })
    // Every media predicate is backend-paginated so a matching item can never
    // be stranded behind an unmatched first page.
    get().loadOutputs()
  },
  setOutputArtifactScope: (scope) => {
    if (scope === get().outputArtifactScope) return
    _metadataRequestGeneration++
    set({
      outputArtifactScope: scope,
      selectedOutput: 0,
      selectedOutputMeta: null,
      selectedOutputMetaName: null,
      metadataLoading: false,
      selectedOutputKeys: [],
    })
    get().loadOutputs()
  },
  setOutputSearchQuery: (q) => {
    if (q === get().outputSearchQuery) return
    _metadataRequestGeneration++
    set({
      outputSearchQuery: q,
      selectedOutput: 0,
      selectedOutputMeta: null,
      selectedOutputMetaName: null,
      metadataLoading: false,
      selectedOutputKeys: [],
    })
    // Reload on both entry and clear; otherwise a cleared search could leave
    // stale results under a client-side media filter.
    get().loadOutputs()
  },
  resetGalleryFilters: () => {
    const {
      mediaFilter, outputArtifactScope, outputSearchQuery,
    } = get()
    if (
      mediaFilter === 'all'
      && outputArtifactScope === 'final'
      && !outputSearchQuery
    ) return
    _metadataRequestGeneration++
    set({
      mediaFilter: 'all',
      outputArtifactScope: 'final',
      outputSearchQuery: '',
      selectedOutput: 0,
      selectedOutputMeta: null,
      selectedOutputMetaName: null,
      metadataLoading: false,
      selectedOutputKeys: [],
      gallerySelectionMode: false,
    })
    get().loadOutputs()
  },
  filteredOutputs: () => {
    const { outputs, mediaFilter } = get()
    return computeFilteredOutputs(outputs, mediaFilter)
  },

  outputsLoading: false,
  loadOutputs: async () => {
    const PAGE_SIZE = 100
    const { mediaFilter, outputArtifactScope, outputSearchQuery, browsingUploads, activeWorkspace } = get()
    const ws = browsingUploads ? '__uploads__' : activeWorkspace
    const requestGeneration = ++_outputsRequestGeneration
    set({ outputsLoading: true })
    try {
      const { outputs: apiOutputs, total } = await api.fetchOutputs(PAGE_SIZE, 0, {
        favoritesOnly: mediaFilter === 'favorites',
        multiclipOnly: mediaFilter === 'multiclip',
        search: outputSearchQuery.trim() || undefined,
        workspace: ws,
        artifactScope: outputArtifactScope,
        mediaType: mediaFilter,
      })
      if (requestGeneration !== _outputsRequestGeneration) return false
      const outputs: OutputFile[] = apiOutputs.map(o => ({
        name: o.name,
        url: o.url,
        type: o.type,
        mode: (o.mode as OutputFile['mode']) || null,
        edit_sub_mode: (o.edit_sub_mode as OutputFile['edit_sub_mode']) || null,
        artifact_class: o.artifact_class || 'final',
        linked_component_count: o.linked_component_count || 0,
        favorite: o.favorite || false,
        size: o.size,
        created_at: o.created_at,
        revision: o.revision,
        workspace: o.workspace,
        private: o.private,
        explicit: o.explicit,
      }))
      set({ outputs, outputsTotal: total, selectedOutput: 0, outputsLoading: false })
      if (outputs.length > 0) {
        get().loadOutputMetadata(outputs[0].name)
      } else {
        _metadataRequestGeneration++
        set({ selectedOutputMeta: null, selectedOutputMetaName: null, metadataLoading: false })
      }
      return true
    } catch (e) {
      console.error('Failed to load outputs:', e)
      if (requestGeneration === _outputsRequestGeneration) set({ outputsLoading: false })
      return false
    }
  },

  // Load next page of outputs (infinite scroll)
  loadMoreOutputs: async () => {
    const PAGE_SIZE = 100
    const { outputs: current, outputsTotal: total, mediaFilter, outputArtifactScope, outputSearchQuery, browsingUploads, activeWorkspace } = get()
    if (current.length >= total || get().outputsLoading || _outputsPaginationActive) return
    _outputsPaginationActive = true
    // Full list replacements still supersede pagination, but background
    // refresh defers while this page is active so scrolling cannot starve.
    const requestGeneration = ++_outputsRequestGeneration
    const fetchOptions = {
      favoritesOnly: mediaFilter === 'favorites',
      multiclipOnly: mediaFilter === 'multiclip',
      search: outputSearchQuery.trim() || undefined,
      workspace: browsingUploads ? '__uploads__' : activeWorkspace,
      artifactScope: outputArtifactScope,
      mediaType: mediaFilter,
    }
    try {
      const { outputs: apiOutputs, total: newTotal } = await api.fetchOutputs(
        PAGE_SIZE, current.length, fetchOptions,
      )
      if (requestGeneration !== _outputsRequestGeneration) return
      const more: OutputFile[] = apiOutputs.map(o => ({
        name: o.name,
        url: o.url,
        type: o.type,
        mode: (o.mode as OutputFile['mode']) || null,
        edit_sub_mode: (o.edit_sub_mode as OutputFile['edit_sub_mode']) || null,
        artifact_class: o.artifact_class || 'final',
        linked_component_count: o.linked_component_count || 0,
        favorite: o.favorite || false,
        size: o.size,
        created_at: o.created_at,
        revision: o.revision,
        workspace: o.workspace,
        private: o.private,
        explicit: o.explicit,
      }))
      const existingNames = new Set(current.map(output => output.name))
      const pageShifted = more.some(output => existingNames.has(output.name))
      if (pageShifted) {
        // Offset pagination shifts when a new newest output arrives between
        // pages. Appending/deduplicating would permanently skip that new head
        // item and can livelock with outputs.length < total. Rebase the loaded
        // prefix from offset zero at the desired accumulated depth.
        const rebaseLimit = Math.min(newTotal, current.length + PAGE_SIZE)
        const rebasedResponse = await api.fetchOutputs(rebaseLimit, 0, fetchOptions)
        if (requestGeneration !== _outputsRequestGeneration) return
        const rebased: OutputFile[] = rebasedResponse.outputs.map(o => ({
          name: o.name,
          url: o.url,
          type: o.type,
          mode: (o.mode as OutputFile['mode']) || null,
          edit_sub_mode: (o.edit_sub_mode as OutputFile['edit_sub_mode']) || null,
          artifact_class: o.artifact_class || 'final',
          linked_component_count: o.linked_component_count || 0,
          favorite: o.favorite || false,
          size: o.size,
          created_at: o.created_at,
          revision: o.revision,
          workspace: o.workspace,
          private: o.private,
          explicit: o.explicit,
        }))
        const selectedName = computeFilteredOutputs(current, mediaFilter)[get().selectedOutput]?.name
        const rebasedFiltered = computeFilteredOutputs(rebased, mediaFilter)
        const rebasedIndex = selectedName
          ? Math.max(0, rebasedFiltered.findIndex(output => output.name === selectedName))
          : 0
        set({
          outputs: rebased,
          outputsTotal: rebasedResponse.total,
          selectedOutput: rebasedIndex,
        })
        return
      }
      // No page shift: append the disjoint page.
      set(s => {
        const existingNames = new Set(s.outputs.map(o => o.name))
        const unique = more.filter(o => !existingNames.has(o.name))
        return { outputs: [...s.outputs, ...unique], outputsTotal: newTotal }
      })
    } catch {
      // Silent fail
    } finally {
      _outputsPaginationActive = false
    }
  },

  // Incremental refresh: only fetch the newest items to detect new outputs during generation
  refreshOutputs: async () => {
    try {
      if (get().outputsLoading || _outputsPaginationActive) return
      const { mediaFilter, outputArtifactScope, outputSearchQuery, browsingUploads, activeWorkspace } = get()
      const requestGeneration = ++_outputsRequestGeneration
      const refreshLimit = Math.max(50, get().outputs.length)
      const { outputs: apiOutputs, total } = await api.fetchOutputs(refreshLimit, 0, {
        favoritesOnly: mediaFilter === 'favorites',
        multiclipOnly: mediaFilter === 'multiclip',
        search: outputSearchQuery.trim() || undefined,
        workspace: browsingUploads ? '__uploads__' : activeWorkspace,
        artifactScope: outputArtifactScope,
        mediaType: mediaFilter,
      })
      if (requestGeneration !== _outputsRequestGeneration) return
      const fresh: OutputFile[] = apiOutputs.map(o => ({
        name: o.name,
        url: o.url,
        type: o.type,
        mode: (o.mode as OutputFile['mode']) || null,
        edit_sub_mode: (o.edit_sub_mode as OutputFile['edit_sub_mode']) || null,
        artifact_class: o.artifact_class || 'final',
        linked_component_count: o.linked_component_count || 0,
        favorite: o.favorite || false,
        size: o.size,
        created_at: o.created_at,
        revision: o.revision,
        workspace: o.workspace,
        private: o.private,
        explicit: o.explicit,
      }))
      const current = get().outputs
      const currentSelected = computeFilteredOutputs(current, mediaFilter)[get().selectedOutput]
      const selectedName = currentSelected?.name
      const freshNames = new Set(fresh.map(o => o.name))
      const retainedTail = total > refreshLimit
        ? current.slice(refreshLimit).filter(o => !freshNames.has(o.name))
        : []
      const merged = [...fresh, ...retainedTail]
      const filteredMerged = computeFilteredOutputs(merged, mediaFilter)
      const selectedIndex = selectedName
        ? Math.max(0, filteredMerged.findIndex(o => o.name === selectedName))
        : 0
      set({ outputs: merged, outputsTotal: total, selectedOutput: selectedIndex })
      const selectedAfterRefresh = filteredMerged[selectedIndex]
      const selectedRevisionChanged = Boolean(
        selectedAfterRefresh
        && currentSelected
        && selectedAfterRefresh.name === currentSelected.name
        && selectedAfterRefresh.revision !== currentSelected.revision
      )
      if (selectedAfterRefresh?.name !== selectedName || selectedRevisionChanged) {
        if (selectedAfterRefresh) {
          get().loadOutputMetadata(selectedAfterRefresh.name)
        } else {
          _metadataRequestGeneration++
          set({ selectedOutputMeta: null, selectedOutputMetaName: null, metadataLoading: false })
        }
      }
    } catch {
      // Silent fail for background refresh
    }
  },

  toggleFavorite: async (name) => {
    try {
      const result = await api.toggleFavorite(name, get().activeWorkspace)
      set(s => ({
        outputs: s.outputs.map(o => o.name === name ? { ...o, favorite: result.favorite } : o),
      }))
    } catch (e) {
      console.error('Failed to toggle favorite:', e)
    }
  },

  // Output metadata
  selectedOutputMeta: null,
  selectedOutputMetaName: null,
  metadataLoading: false,

  loadOutputMetadata: async (name) => {
    const requestGeneration = ++_metadataRequestGeneration
    set({ metadataLoading: true, selectedOutputMeta: null, selectedOutputMetaName: null })
    try {
      const requestedOutput = get().filteredOutputs().find(output => output.name === name)
      const meta = await api.fetchOutputMetadata(
        name,
        requestedOutput?.workspace || get().activeWorkspace,
      )
      const current = get().filteredOutputs()[get().selectedOutput]
      if (requestGeneration !== _metadataRequestGeneration || current?.name !== name) return
      set({ selectedOutputMeta: meta, selectedOutputMetaName: name, metadataLoading: false })
    } catch (e) {
      // Diagnostic: surface metadata-fetch failures (the usual cause of a
      // "Load Settings does nothing" report on slow/VPN links) instead of
      // swallowing them silently.
      console.error('[LoadSettings] fetchOutputMetadata FAILED for', name, '-', e)
      if (requestGeneration === _metadataRequestGeneration) {
        set({ selectedOutputMeta: null, selectedOutputMetaName: null, metadataLoading: false })
      }
    }
  },

  loadSettingsFromOutput: async () => {
    // Metadata is normally fetched in the background when an output is selected.
    // On a slow/high-latency link (e.g. the user is remote over VPN) that fetch
    // may not have landed — or may have failed — by the time "Load Settings" is
    // clicked, leaving selectedOutputMeta null and this a silent no-op. Re-fetch
    // on demand so the click is self-healing regardless of the background state.
    const pendingOutput = get().filteredOutputs()[get().selectedOutput]
    const pendingName = pendingOutput?.name
    if (!pendingName) return
    // A pending fresh-model hydration must never land after a sidecar restore
    // and replace the settings needed to reproduce that output.
    ++_h3ProfileApplySeq
    ++_modelDefaultsSeq
    const restoreGeneration = ++_settingsRestoreGeneration
    let selectedOutputMeta = get().selectedOutputMetaName === pendingName
      ? get().selectedOutputMeta
      : null
    console.log('[LoadSettings] clicked — meta present:', !!selectedOutputMeta?.params,
                '| metadataLoading:', get().metadataLoading, '| selectedOutput idx:', get().selectedOutput)
    if (!selectedOutputMeta?.params) {
      console.log('[LoadSettings] no meta yet — on-demand fetch for:', pendingOutput?.name ?? '(no output at index)')
      if (pendingOutput) {
        await get().loadOutputMetadata(pendingOutput.name)
        if (restoreGeneration !== _settingsRestoreGeneration) return
        const current = get().filteredOutputs()[get().selectedOutput]
        selectedOutputMeta = current?.name === pendingName && get().selectedOutputMetaName === pendingName
          ? get().selectedOutputMeta
          : null
        console.log('[LoadSettings] after on-demand fetch — params present:', !!selectedOutputMeta?.params,
                    '| source:', selectedOutputMeta?.source)
      }
    }
    if (!selectedOutputMeta?.params) {
      console.warn('[LoadSettings] ABORT — no params available after fetch attempt; button is a no-op')
      return
    }
    if (restoreGeneration !== _settingsRestoreGeneration) return
    const { models } = get()
    const p = selectedOutputMeta.params as Record<string, unknown>
    const uploadFilenames = selectedOutputMeta.upload_filenames as Record<string, string> | undefined
    const h3Longform = (
      p._h3_longform && typeof p._h3_longform === 'object'
        ? p._h3_longform as Record<string, unknown>
        : null
    )
    console.log('[LoadSettings] applying settings — model_type:', p.model_type, '| param keys:', Object.keys(p).length)

    let modelType = (p.model_type as string) || ''
    const requestedH3Checkpoint = String(p._h3_requested_checkpoint || '')
    if (H3_STUDIO_MODELS.has(requestedH3Checkpoint)) {
      modelType = requestedH3Checkpoint
    }
    if (!modelType) return

    // Migrate Recast sidecars made before the dedicated model existed. Those
    // jobs used the general I2V Fast accelerator with replacement conditioning;
    // loading them now should reproduce the corrected native-replacement recipe.
    const migratedLegacyRecast = p.edit_sub_mode === 'recast'
      && modelType === 'scail2_14B_fast'
      && models.some(m => m.model_type === 'scail2_14B_recast_fast')
    if (migratedLegacyRecast) modelType = 'scail2_14B_recast_fast'

    // SFX generations swap the virtual MMAudio model for a video carrier
    // at submit, so the sidecar records the carrier. Restore the virtual
    // id — resubmitting re-swaps it, and mode/sub-tab detection below
    // classifies it as audio/sfx instead of video.
    const sfxVirtual = p._sfx_virtual_model as string | undefined
    if ((p._audio_sub_mode === 'sfx' || p.sfx_mode) && sfxVirtual && models.some(m => m.model_type === sfxVirtual)) {
      modelType = sfxVirtual
    }

    // Per-sub-mode isolation: pencil-load may jump the sidebar to another
    // video sub-mode (or clobber the current one) by writing params
    // wholesale. Stash the active sub-mode's working set first so
    // in-progress work (e.g. a Frames setup) survives loading an Extend
    // clip's settings — switching back restores it.
    {
      const cur = get()
      if (cur.generationMode === 'video') {
        set({
          videoSubModeStash: {
            ...cur.videoSubModeStash,
            [(cur.params.image_mode as number) ?? 0]: captureVideoSubModeStash(cur),
          },
        })
      }
    }

    // Determine generation mode from model (respects per-model avatar overrides)
    const model = models.find(m => m.model_type === modelType)
    if (model) {
      const mode = getModelMode(modelType, model.family)
      set({ generationMode: mode })
      // Audio outputs restore the SUB-TAB too (Speech / Music / SFX) —
      // previously the pencil landed on the Audio tab but left whatever
      // sub-tab was last open. Newer sidecars record _audio_sub_mode;
      // older ones fall back to classifying the model. Direct set, NOT
      // setAudioSubMode — that would call selectModel and clobber the
      // params restored below.
      if (mode === 'audio') {
        const recordedSub = p._audio_sub_mode as import('../types').AudioSubMode | undefined
        const inferredSub: import('../types').AudioSubMode =
          sfxModelTypes.has(modelType) || p.sfx_mode ? 'sfx'
          : isMusicModelType(modelType) ? 'music'
          : 'speech'
        const subMode = (recordedSub === 'speech' || recordedSub === 'music' || recordedSub === 'sfx')
          ? recordedSub : inferredSub
        const restoredLyrics = (p._tts_original_prompt as string) || (p.prompt as string) || ''
        set(s => ({
          audioSubMode: subMode,
          selectedModelPerAudioSubMode: { ...s.selectedModelPerAudioSubMode, [subMode]: modelType },
          // Music: restore the song-writer inputs alongside the fields.
          // Older sidecars lack _music_description — clear rather than
          // leave a stale description that didn't produce this song
          // (instrumental still infers from the lyrics sentinel).
          ...(subMode === 'music' ? {
            musicDescription: (p._music_description as string) || '',
            musicInstrumental: !!p._music_instrumental
              || restoredLyrics.trim().toLowerCase() === '[instrumental]',
          } : {}),
        }))
      }
    }

    // Load model capabilities BEFORE applying the restored params.
    // loadModelOptions merges model-default steps/guidance into params when
    // its fetch resolves; it used to be fired at the END of this restore,
    // so the defaults landed after the sidecar values and silently reverted
    // num_inference_steps / guidance_scale on every pencil click. Awaiting
    // it here means defaults land first and the restored values win — and
    // modelOptions matches the restored model before rerollGeneration
    // submits (stale capabilities used to strip stg_scale/perturbation_*
    // from the request, which then poisoned the next sidecar with zeros).
    // (Virtual SFX models have no LoRAs/options endpoints — same guard
    // as boot.)
    if (!sfxModelTypes.has(modelType)) {
      get().loadLoras(modelType)
      await get().loadModelOptions(modelType)
    }
    if (restoreGeneration !== _settingsRestoreGeneration) return

    const automaticH3Longform = !!(
      h3Longform
      && ['minimax_h3', 'minimax_h3_pinkcherry_fl2va', 'minimax_h3_w4a8_fl2va', 'minimax_h3_ref2va'].includes(modelType)
    )
    const restoredManualH3SegmentCeiling = hasManualH3SegmentCeiling(
      { ...p, model_type: modelType },
      automaticH3Longform ? h3Longform : null,
    )

    // Automatic H3 planning transforms scalar anchors into sparse per-clip
    // arrays. Restore from the preserved Studio request, not from those
    // worker-only arrays.
    const restoredH3Start = automaticH3Longform
      ? (h3Longform?.original_image_start as string || '')
      : ''
    const restoredH3End = automaticH3Longform
      ? (h3Longform?.original_image_end as string || '')
      : ''
    // Detect I2V: if image_start was used or image_prompt_type contains "S"
    const hadStartImage = automaticH3Longform
      ? !!restoredH3Start
      : !!(p.image_start || (p.image_prompt_type as string || '').includes('S'))
    const hadEndImage = automaticH3Longform
      ? !!restoredH3End
      : !!(p.image_end || (p.image_prompt_type as string || '').includes('E'))

    // TTS restores names before Speaker 1/2 substitution. Edit workflows
    // restore the user's text rather than internal conditioning guidance.
    const originalPrompt = (
      automaticH3Longform
        ? (h3Longform?.global_prompt as string || '')
        : ''
    ) || (p._tts_original_prompt as string) || (
      p.edit_sub_mode === 'recast' && typeof p.edit_recast_raw_prompt === 'string'
        ? p.edit_recast_raw_prompt as string
        : p.edit_sub_mode === 'outpaint' && typeof p.edit_outpaint_raw_prompt === 'string'
          ? p.edit_outpaint_raw_prompt as string
          : p.prompt as string
    ) || ''

    // Build params from metadata
    // For image_mode: use 1 (I2V UI toggle) if start image was used, else 0
    const newParams: Partial<GenerateParams> = {
      prompt: originalPrompt,
      model_type: modelType,
      resolution: (p.resolution as string) || '1280x720',
      video_length: automaticH3Longform
        ? Number(h3Longform?.requested_frames || p.video_length || 81)
        : (p.video_length as number) || 81,
      num_inference_steps: H3_STUDIO_MODELS.has(modelType)
        ? Math.max(2, Math.min(50, Number(p.num_inference_steps) || 20))
        : migratedLegacyRecast ? 8 : (p.num_inference_steps as number) || 20,
      guidance_scale: migratedLegacyRecast ? 1 : (p.guidance_scale as number) || 5.0,
      seed: (p.seed as number) ?? -1,
      // Restore the ACTUAL saved output mode (0 = video, 1 = image). The old
      // `hadStartImage ? 1 : 0` was wrong: an I2V *video* clip has a start image
      // but image_mode 0 — inferring 1 from the start image put the UI in image-
      // output mode, so a later T2V (after clearing the start image) emitted a PNG.
      image_mode: automaticH3Longform ? 0 : ((p.image_mode as number) ?? 0),
      negative_prompt: (p.negative_prompt as string) || '',
      repeat_generation: 1,
      activated_loras: (p.activated_loras as string[]) || [],
      loras_multipliers: (p.loras_multipliers as string) || '',
      settings_version: p.settings_version as number,
    }
    if (H3_STUDIO_MODELS.has(modelType)) {
      newParams.h3_adaptive_conditioning = typeof p.h3_adaptive_conditioning === 'boolean'
        ? p.h3_adaptive_conditioning
        : typeof h3Longform?.adaptive_conditioning === 'boolean'
          ? h3Longform.adaptive_conditioning
          : true
    }

    // Copy optional fields — explicitly clear when absent to prevent stale values leaking
    newParams.sliding_window_size = automaticH3Longform
      ? restoredManualH3SegmentCeiling
        ? Number(h3Longform?.segment_frames_maximum || p.sliding_window_size || 0) || undefined
        : undefined
      : (p.sliding_window_size as number) ?? undefined
    newParams.sliding_window_overlap = (p.sliding_window_overlap as number) ?? undefined
    newParams.guidance_phases = (p.guidance_phases as number) ?? undefined
    newParams.video_prompt_type = (p.video_prompt_type as string) || ''
    newParams.audio_prompt_type = (p.audio_prompt_type as string) || ''
    newParams.image_prompt_type = (p.image_prompt_type as string) || ''
    newParams.input_video_strength = (p.input_video_strength as number) ?? undefined
    newParams.flow_shift = migratedLegacyRecast ? 1 : (p.flow_shift as number) ?? undefined
    newParams.self_refiner_setting = (p.self_refiner_setting as number) ?? undefined
    newParams.audio_guide = (p.audio_guide as string) || ''
    newParams.audio_guide2 = (p.audio_guide2 as string) || ''
    newParams.audio_guide3 = (p.audio_guide3 as string) || ''
    // Style / Music Caption (ACE-Step). Was never copied here, so the
    // pencil restored only the lyrics — clear when absent so a stale
    // caption can't leak into an unrelated restore.
    newParams.alt_prompt = (p.alt_prompt as string) || ''
    newParams.video_guide = (p.video_guide as string) || ''
    newParams.video_guide2 = (p.video_guide2 as string) || ''
    newParams.video_guide3 = (p.video_guide3 as string) || ''
    newParams.image_refs = Array.isArray(p.image_refs) ? (p.image_refs as string[]) : []
    newParams.frames_positions = (p.frames_positions as string) || ''
    newParams.injection_strength = (p.injection_strength as number) ?? undefined
    newParams.remove_background_images_ref = (p.remove_background_images_ref as number) ?? 0
    newParams.tea_cache = (p.tea_cache as number) ?? undefined
    const restoredH3Custom = _restorableH3CustomSettings(p.custom_settings)
    if (modelType.startsWith('minimax_h3')) {
      const restoredEngine = _normalizeH3AttentionEngine(restoredH3Custom.h3_attention_engine)
      newParams.custom_settings = {
        h3_attention_engine: restoredEngine,
        ...restoredH3Custom,
      }
      try {
        localStorage.setItem(H3_ATTENTION_ENGINE_KEY, restoredEngine)
      } catch {
        // The restored output settings still apply for this session.
      }
    } else {
      newParams.custom_settings = undefined
    }

    // Progressive 3-stage pipeline settings
    if (p.progressive_pipeline) {
      (newParams as Record<string, unknown>).progressive_pipeline = true;
      (newParams as Record<string, unknown>).progressive_stage1_image_weight = (p.progressive_stage1_image_weight as number) ?? 0.7;
      (newParams as Record<string, unknown>).progressive_stage2_steps = (p.progressive_stage2_steps as number) ?? 8;
      (newParams as Record<string, unknown>).progressive_stage3_steps = (p.progressive_stage3_steps as number) ?? 3;
      (newParams as Record<string, unknown>).progressive_stage2_sigma = (p.progressive_stage2_sigma as number) ?? 1.0;
      (newParams as Record<string, unknown>).progressive_stage3_sigma = (p.progressive_stage3_sigma as number) ?? 0.85;
      (newParams as Record<string, unknown>).progressive_stage3_image_weight = (p.progressive_stage3_image_weight as number) ?? 0.7
    }
    // Single-stage distilled mode — mutually exclusive with progressive above
    if (p.single_stage_pipeline) {
      (newParams as Record<string, unknown>).single_stage_pipeline = true;
      (newParams as Record<string, unknown>).progressive_pipeline = false;
    }
    // Reference two-stage pipeline (10Eros) — restore so re-generating an
    // STG-era sidecar reproduces the pipeline that made it.
    (newParams as Record<string, unknown>).reference_pipeline = (p.reference_pipeline as boolean) ?? undefined;

    // Advanced pipeline settings
    (newParams as Record<string, unknown>).stage2_steps = (p.stage2_steps as number) ?? undefined;
    (newParams as Record<string, unknown>).stg_scale = (p.stg_scale as number) ?? undefined;
    // Perturbation config rides along with stg_scale so re-generating an STG
    // run is faithful. Old sidecars (pre-STG-wiring) simply lack these keys.
    (newParams as Record<string, unknown>).perturbation_switch = (p.perturbation_switch as number) ?? undefined;
    (newParams as Record<string, unknown>).perturbation_layers = Array.isArray(p.perturbation_layers) ? (p.perturbation_layers as number[]) : undefined;
    (newParams as Record<string, unknown>).perturbation_start_perc = (p.perturbation_start_perc as number) ?? undefined;
    (newParams as Record<string, unknown>).perturbation_end_perc = (p.perturbation_end_perc as number) ?? undefined;
    (newParams as Record<string, unknown>).cfg_rescale = (p.cfg_rescale as number) ?? undefined;
    (newParams as Record<string, unknown>).modality_scale = (p.modality_scale as number) ?? undefined;
    (newParams as Record<string, unknown>).use_gradient_estimation = (p.use_gradient_estimation as boolean) ?? undefined;
    (newParams as Record<string, unknown>).ge_gamma = (p.ge_gamma as number) ?? undefined;
    (newParams as Record<string, unknown>).keyframe_conditioning_mode = (p.keyframe_conditioning_mode as string) ?? undefined;
    (newParams as Record<string, unknown>).keyframe_inject_mode = (p.keyframe_inject_mode as string) ?? undefined;
    (newParams as Record<string, unknown>).temperature = (p.temperature as number) ?? undefined;
    (newParams as Record<string, unknown>).audio_guidance_scale = (p.audio_guidance_scale as number) ?? undefined

    // Detect multi-clip output and reconstruct clips
    if (!automaticH3Longform && p.multi_prompts_gen_type === 3 && Array.isArray(p.image_start)) {
      // Director Mode joins per-clip prompts with `\n---CLIP_BOUNDARY---\n`
      // (see app/launch.py:7279). Studio Mode multi-shot joins with plain
      // `\n` (single-line prompts only). Split on the boundary token first
      // so Director prompts that contain their own newlines survive; fall
      // back to plain newline split for legacy Studio multi-clip sidecars
      // that don't carry the boundary marker.
      //
      // Before this fix: every internal `\n` in a Director clip prompt
      // became a clip break, doubling+ the clip count and leaving half of
      // them with the literal string `---CLIP_BOUNDARY---` as their prompt.
      // The visible symptom was "some prompts populate but others don't"
      // and start-image indices going to the wrong clips.
      const promptText = (p.prompt as string) || ''
      const CLIP_BOUNDARY = '\n---CLIP_BOUNDARY---\n'
      const promptLines = promptText.includes(CLIP_BOUNDARY)
        ? promptText.split(CLIP_BOUNDARY).map(s => s.trim()).filter(Boolean)
        : promptText.split('\n').map(s => s.trim()).filter(Boolean)
      const imagePaths = p.image_start as string[]
      // Per-clip durations (Director Mode populates this; Studio mode may not).
      // Saved by app/launch.py as part of raw_params before per-clip split;
      // survives onto the concat multiclip sidecar (see real sidecar example
      // in app/outputs/Testing04/...multiclip.meta.json line 13-26).
      const perClipFrames = Array.isArray(p.per_clip_frames) ? (p.per_clip_frames as number[]) : []
      // Per-clip keyframe images (Director Mode KFI feature). Array of arrays
      // — each inner array holds the keyframe paths for that clip. Studio
      // Mode multi-shot generations don't use this field today.
      const perClipKeyframes = Array.isArray(p.per_clip_keyframes) ? (p.per_clip_keyframes as string[][]) : []
      const clipCount = Math.max(promptLines.length, imagePaths.length, perClipFrames.length)
      const clips: MultiClip[] = []
      for (let i = 0; i < clipCount; i++) {
        clips.push({
          prompt: promptLines[i] || '',
          startImage: null,
          startImagePath: imagePaths[i] || null,
          endImage: null,
          endImagePath: null,
          durationFrames: perClipFrames[i] || undefined,
        })
      }
      set({ clips, singlePromptMode: false })
      newParams.image_mode = 2
      newParams.multi_prompts_gen_type = 3

      // Surface per-clip keyframes via image_refs + frames_positions so
      // ControlVideoSection's restore picks them up. NOTE: MultiClip's type
      // doesn't yet carry per-clip keyframes, so all clips' keyframes get
      // concatenated into a single image_refs array with "L" positions
      // (the same encoding launch.py uses at line 7353). Re-running the
      // generation will dispatch keyframes to clips by position order,
      // matching the original layout. Documented as a known limitation:
      // editing one clip's keyframes after restore affects the whole pool.
      if (perClipKeyframes.length > 0) {
        const flatRefs: string[] = []
        const flatPositions: string[] = []
        for (const clipKfs of perClipKeyframes) {
          if (Array.isArray(clipKfs)) {
            for (const kf of clipKfs) {
              if (kf) {
                flatRefs.push(kf)
                flatPositions.push('L')
              }
            }
          }
        }
        if (flatRefs.length > 0) {
          newParams.image_refs = flatRefs
          newParams.frames_positions = flatPositions.join(' ')
          // Ensure KFI is in video_prompt_type so ControlVideoSection
          // recognizes the inject-frame mode on restore.
          const vpt = newParams.video_prompt_type || ''
          if (!vpt.includes('KFI')) {
            newParams.video_prompt_type = vpt + 'KFI'
          }
        }
      }

      // Fetch clip images from upload URLs to show previews. Prefer
      // upload_filenames.image_start (already-extracted basenames) when
      // present; fall back to deriving basenames from params.image_start
      // paths so older sidecars without upload_filenames still restore.
      const uploadNames = Array.isArray(uploadFilenames?.image_start)
        ? uploadFilenames.image_start as string[]
        : imagePaths.map(p => (p || '').replace(/\\/g, '/').split('/').pop() || '')
      for (let i = 0; i < clipCount; i++) {
        const fname = uploadNames[i]
        if (fname) {
          const idx = i
          fetch(api.getUploadUrl(fname))
            .then(r => r.ok ? r.blob() : null)
            .then(blob => {
              if (!blob) return
              const file = new File([blob], fname, { type: blob.type })
              get().setClipStartImage(idx, file)
            })
            .catch(() => {})
        }
      }
    } else {
      // Set or clear attachment paths from sidecar
      newParams.image_start = automaticH3Longform
        ? restoredH3Start
        : (p.image_start ? (p.image_start as string) : '')
      if (automaticH3Longform) {
        newParams.prompt = originalPrompt
        newParams.image_mode = 0
        newParams.multi_prompts_gen_type = hasGlobalTimeline(originalPrompt) ? 2 : 0
      }
      set({ clips: [], singlePromptMode: automaticH3Longform })
    }
    newParams.image_end = automaticH3Longform
      ? restoredH3End
      : (p.image_end ? (p.image_end as string) : '')

    // Rebuild lora weights from multipliers string
    const loraWeights: Record<string, number[]> = {}
    const loras = newParams.activated_loras || []
    const multParts = (newParams.loras_multipliers || '').split(' ').filter(Boolean)
    for (let i = 0; i < loras.length; i++) {
      const parts = (multParts[i] || '1.00').split(';').map(Number)
      loraWeights[loras[i]] = parts
    }
    const restoredExplicitOutput = pendingOutput.explicit
      || selectedOutputMeta.explicit === true

    // Restore duration from metadata
    const restoredDuration = (p.duration_seconds as number) || 0
    // Restore post-processing settings from metadata
    const restoredSpatialUpsampling = (p.spatial_upsampling as string) || ''
    const restoredFilmGrainIntensity = (p.film_grain_intensity as number) || 0
    const restoredFilmGrainSaturation = (p.film_grain_saturation as number) || 0.5

    // Restore audio guide filename from upload_filenames. Fall back to
    // deriving basename from params.audio_guide for sidecars that pre-date
    // the upload_filenames extraction code.
    const _deriveBase = (val: unknown): string | null => {
      if (typeof val !== 'string' || !val) return null
      const bn = val.replace(/\\/g, '/').split('/').pop()
      return bn || null
    }
    const restoredAudioGuideFilename =
      (typeof uploadFilenames?.audio_guide === 'string' ? uploadFilenames.audio_guide : null)
      || _deriveBase(p.audio_guide)
    const restoredAudioGuide2Filename =
      (typeof uploadFilenames?.audio_guide2 === 'string' ? uploadFilenames.audio_guide2 : null)
      || _deriveBase(p.audio_guide2)
    // Restore TTS speaker names (1-6)
    const restoredSpeakerName1 = (p._tts_speaker_name1 as string) || ''
    const restoredSpeakerName2 = (p._tts_speaker_name2 as string) || ''
    const restoredVoiceCount = (p._tts_voice_count as number) || 0
    const restoredVoices: { name: string; filename: string | null; path: string | null }[] = []
    for (let i = 0; i < Math.max(restoredVoiceCount, 2); i++) {
      const name = (p[`_tts_speaker_name${i + 1}`] as string) || ''
      if (name || i < restoredVoiceCount) {
        restoredVoices.push({ name, filename: null, path: null })
      }
    }

    set(s => ({
      ...(restoredExplicitOutput
        ? { explicitOutput: true, privateOutput: true }
        : {}),
      params: { ...s.params, ...newParams },
      selectedModelPerMode: { ...s.selectedModelPerMode, [s.generationMode]: modelType },
      loraWeights,
      startImage: null,
      endImage: null,
      imageRefs: [],  // Clear — will repopulate below if image_refs exist
      outputCount: 1,
      ...(restoredDuration > 0 ? { durationSeconds: restoredDuration } : {}),
      spatialUpsampling: restoredSpatialUpsampling,
      filmGrainIntensity: restoredFilmGrainIntensity,
      filmGrainSaturation: restoredFilmGrainSaturation,
      audioGuideFilename: restoredAudioGuideFilename,
      audioGuide2Filename: restoredAudioGuide2Filename,
      // TTS state
      ...(restoredSpeakerName1 || restoredSpeakerName2 || restoredVoiceCount > 0 ? {
        ttsSpeakerName1: restoredSpeakerName1,
        ttsSpeakerName2: restoredSpeakerName2,
        ttsSpeakerNamesManual: true,
        ttsVoiceCount: restoredVoiceCount,
        ttsVoices: restoredVoices,
      } : {}),
      h3SelectedProfile: 'custom',
      h3ProfileApplying: null,
    }))
    if (H3_STUDIO_MODELS.has(modelType)) {
      void get().normalizeH3EditableProfile()
    }

    // Restore image refs as File objects (for image mode reference images)
    // Skip if this is a KFI (frames injection) output — those refs are handled by ControlVideoSection
    const imageRefPaths = newParams.image_refs || []
    const isKFI = (newParams.video_prompt_type || '').includes('KFI')
    if (imageRefPaths.length > 0 && !isKFI) {
      // Set the ref type from saved params
      const vpt = newParams.video_prompt_type || ''
      const refType = vpt.includes('K') && vpt.includes('I') ? 'KI' : vpt.includes('I') ? 'I' : 'KI'
      const restoreSemanticH3Paths = H3_STUDIO_MODELS.has(modelType)
      set({ imageRefType: restoreSemanticH3Paths ? '' : refType })

      // H3 keeps authorized server paths directly so a newly attached File is
      // merged instead of replacing the restored semantic set. Other model
      // families retain the legacy File rehydration behavior.
      if (!restoreSemanticH3Paths) {
        const refPromises = imageRefPaths.map(refPath => {
          const fname = refPath.replace(/\\/g, '/').split('/').pop() || ''
          if (!fname) return Promise.resolve(null)
          // /file searches active/all workspaces and uploads, so both generated
          // references and newly uploaded images restore correctly.
          const url = api.getFileUrl(fname)
          return fetch(url)
            .then(r => r.ok ? r.blob() : null)
            .then(blob => blob ? new File([blob], fname, { type: blob.type || 'image/png' }) : null)
            .catch(() => null)
        })
        Promise.all(refPromises).then(files => {
          if (restoreGeneration !== _settingsRestoreGeneration) return
          const ordered = files.filter((f): f is File => f !== null)
          set({ imageRefs: ordered })
        })
      }
    }

    // Restore timing through the same model-owned frame grid as the live
    // controls. Older sidecars may contain rounded seconds or a window from a
    // different model; clamp those instead of reintroducing hidden stale state.
    const restoredOptions = get().modelOptions
    const fps = restoredOptions?.fps || model?.fps || 16
    const restoredFrames = restoredOptions
      ? alignStudioTotalFrames(Number(newParams.video_length || 81), restoredOptions)
      : Math.max(1, Math.round(Number(newParams.video_length || 81)))
    newParams.video_length = restoredFrames
    const timingState: Partial<AppState> = {
      durationSeconds: Math.round((restoredFrames / fps) * 1000) / 1000,
    }
    if (restoredOptions && (restoredOptions.sliding_window || usesStudioSegments(restoredOptions))) {
      const defaults = restoredOptions.sliding_window_defaults || {}
      const latent = Math.max(1, Math.trunc(restoredOptions.latent_size || restoredOptions.frames_steps || 4))
      const requestedWindow = Math.max(1, Math.round(Number(newParams.sliding_window_size || defaults.window_default || restoredFrames)))
      const segmented = usesStudioSegments(restoredOptions)
      const minimum = Math.max(1, Math.trunc(defaults.window_min || (segmented ? restoredOptions.frames_minimum : 1) || 1))
      const maximum = Math.max(minimum, Math.trunc(defaults.window_max || (segmented ? restoredOptions.frames_maximum : requestedWindow) || requestedWindow))
      const clampedWindow = Math.min(maximum, Math.max(minimum, requestedWindow))
      const windowFrames = segmented
        ? alignTotalFrames(clampedWindow, restoredOptions)
        : Math.floor((clampedWindow - 1) / latent) * latent + 1
      const discard = Math.max(0, Math.trunc(defaults.discard_last_frames || 0))
      const safeOverlapMax = Math.max(0, Math.min(
        Math.trunc(defaults.overlap_max ?? windowFrames),
        windowFrames - discard - latent,
      ))
      const overlap = Math.max(
        Math.min(Math.trunc(defaults.overlap_min ?? 0), safeOverlapMax),
        Math.min(Math.trunc(Number(newParams.sliding_window_overlap ?? defaults.overlap_default ?? 0)), safeOverlapMax),
      )
      newParams.sliding_window_size = windowFrames
      newParams.sliding_window_overlap = overlap
      timingState.slidingWindowSeconds = Math.round((windowFrames / fps) * 1000) / 1000
      timingState.slidingWindowOverlap = overlap
      timingState.slidingWindowLocked = restoredManualH3SegmentCeiling
    } else {
      newParams.sliding_window_size = undefined
      newParams.sliding_window_overlap = undefined
      timingState.slidingWindowLocked = false
    }
    set(s => ({
      ...timingState,
      params: { ...s.params, ...newParams },
    }))

    // Derive resolution preset and aspect ratio
    const res = newParams.resolution || '1280x720'
    const declaredResolutionMaps = Object.entries(
      get().modelOptions?.resolution_presets || {},
    ).map(([preset, definition]) => [preset, definition?.values || {}] as const)
    const legacyResolutionMaps = Object.entries(resolutionMap)
    let restoredResolutionSelection = false
    for (const [preset, ratioMap] of [...declaredResolutionMaps, ...legacyResolutionMaps]) {
      for (const [ratio, value] of Object.entries(ratioMap)) {
        if (value === res) {
          set({
            resolutionPreset: preset as ResolutionPreset,
            aspectRatio: ratio as AspectRatio,
          })
          restoredResolutionSelection = true
          break
        }
      }
      if (restoredResolutionSelection) break
    }

    // Restore start/end images from upload URLs as File objects. Prefer
    // upload_filenames.image_{start,end} (basename); fall back to deriving
    // from the full path in params for sidecars missing upload_filenames.
    const h3StartUpload = automaticH3Longform && Array.isArray(uploadFilenames?.image_start)
      ? (uploadFilenames.image_start as unknown[]).find(name => typeof name === 'string' && name) as string | undefined
      : undefined
    const h3EndUpload = automaticH3Longform && Array.isArray(uploadFilenames?.image_end)
      ? [...(uploadFilenames.image_end as unknown[])].reverse().find(name => typeof name === 'string' && name) as string | undefined
      : undefined
    const startFile = h3StartUpload || (typeof uploadFilenames?.image_start === 'string'
      ? uploadFilenames.image_start
      : null) || _deriveBase(automaticH3Longform ? restoredH3Start : p.image_start)
    const endFile = h3EndUpload || (typeof uploadFilenames?.image_end === 'string'
      ? uploadFilenames.image_end
      : null) || _deriveBase(automaticH3Longform ? restoredH3End : p.image_end)
    if (hadStartImage && startFile) {
      fetch(api.getUploadUrl(startFile))
        .then(r => r.ok ? r.blob() : null)
        .then(blob => {
          if (!blob || restoreGeneration !== _settingsRestoreGeneration) return
          const file = new File([blob], startFile, { type: blob.type })
          set({ startImage: file })
        })
        .catch(() => {})
    }
    if (hadEndImage && endFile) {
      fetch(api.getUploadUrl(endFile))
        .then(r => r.ok ? r.blob() : null)
        .then(blob => {
          if (!blob || restoreGeneration !== _settingsRestoreGeneration) return
          const file = new File([blob], endFile, { type: blob.type })
          set({ endImage: file })
        })
        .catch(() => {})
    }

    // ── Edit Mode restore ───────────────────────────────────────────────
    // If the sidecar carries edit_sub_mode, this output was made by the
    // Retake / Inpaint / Outpaint / Restyle / Edit Anything sub-modes.
    // Switch the sidebar into the matching mode and re-populate the
    // sub-mode-specific controls. The standard restore above already set
    // generationMode from the model family, so we override here when the
    // sidecar tag is authoritative.
    const editSubMode = (p.edit_sub_mode as string) || ''
    if (editSubMode) {
      set({
        generationMode: 'avatar',
        editSubMode: editSubMode as 'retake' | 'inpaint' | 'restyle' | 'outpaint' | 'edit_anything' | 'recast',
      })

      // Re-link the source video. The sidecar stores either edit_video_path
      // (preferred — set by the new endpoints) or falls back to retake_video.
      // We fetch the file by URL so the EditVideoUpload UI shows the same
      // clip the user originally edited.
      const editVideoPath = (p.edit_video_path as string) || (p.retake_video as string) || ''
      if (editVideoPath) {
        const fname = editVideoPath.replace(/\\/g, '/').split('/').pop() || ''
        const url = api.getFileUrl(fname)
        // Probe metadata via a hidden <video> first so duration/resolution
        // are correct, then fetch the blob to populate editVideoFile.
        if (fname) {
          const video = document.createElement('video')
          video.src = url
          video.muted = true
          video.onloadedmetadata = () => {
            const duration = video.duration && isFinite(video.duration) ? video.duration : 0
            const resolution = `${video.videoWidth}x${video.videoHeight}`
            fetch(url)
              .then(r => r.ok ? r.blob() : null)
              .then(blob => {
                if (!blob) return
                const file = new File([blob], fname, { type: blob.type || 'video/mp4' })
                get().setEditVideo(file, editVideoPath, url, duration, resolution)
              })
              .catch(() => {})
          }
          // If metadata never loads (file moved/deleted), still set the path
          // so the user can re-attach manually.
          set({ editVideoPath, editVideoUrl: url })
        }
      }

      // Trim range — applies to retake, inpaint, edit_anything, outpaint.
      const trimStart = (p.edit_start_time as number) ?? (p.outpaint_trim_start as number)
      const trimEnd = (p.edit_end_time as number) ?? (p.outpaint_trim_end as number)
      if (trimStart != null && trimStart >= 0) {
        set({ editStartTime: trimStart })
        if (editSubMode === 'outpaint') set({ outpaintTrimStart: trimStart })
      }
      if (trimEnd != null && trimEnd > 0) {
        set({ editEndTime: trimEnd })
        if (editSubMode === 'outpaint') set({ outpaintTrimEnd: trimEnd })
      }

      // Sub-mode-specific knobs
      if (editSubMode === 'retake' || editSubMode === 'inpaint' || editSubMode === 'edit_anything') {
        if (p.retake_strength != null) set({ editRetakeStrength: p.retake_strength as number })
        if (p.retake_engine) set({ editRetakeEngine: p.retake_engine as 'native' | 'legacy' })
        if (p.regenerate_audio != null) set({ editRegenerateAudio: !!p.regenerate_audio })
      }
      if (editSubMode === 'inpaint') {
        if (p.edit_target) set({ editDetectedTarget: p.edit_target as string })
        if (p.retake_masks_path) set({ editMasksPath: p.retake_masks_path as string })
      }
      if (editSubMode === 'edit_anything') {
        if (p.edit_anything_lora_strength != null) {
          set({ editAnythingLoraStrength: p.edit_anything_lora_strength as number })
        }
      }
      if (editSubMode === 'restyle') {
        const savedRepaintMappings = Array.isArray(p.edit_repaint_region_mappings)
          ? p.edit_repaint_region_mappings
            .slice(0, 5)
            .map((raw, index): RepaintRegionMapping | null => {
              if (!raw || typeof raw !== 'object') return null
              const mapping = raw as Record<string, unknown>
              const source = String(mapping.source || '').trim()
              const target = String(mapping.target || '').trim()
              if (!source || !target) return null
              return {
                id: String(mapping.id || `repaint-${index + 1}`),
                source,
                target,
              }
            })
            .filter((mapping): mapping is RepaintRegionMapping => mapping !== null)
          : []
        const repaintFrame = String(p.edit_repaint_target_frame || p.image_start || '')
        const repaintFrameName = repaintFrame.replace(/\\/g, '/').split('/').pop() || ''
        set({
          editRepaintMappings: savedRepaintMappings,
          editRepaintResolutionProfile: p.edit_repaint_resolution_profile === '704p'
            ? '704p'
            : p.edit_repaint_resolution_profile === '512p'
              ? '512p'
              : '480p',
          editRepaintFrameFile: null,
          editRepaintFramePath: repaintFrame,
          editRepaintFrameUrl: repaintFrameName ? api.getFileUrl(repaintFrameName) : '',
        })
        if (repaintFrame && repaintFrameName) {
          const repaintUrl = api.getFileUrl(repaintFrameName)
          fetch(repaintUrl)
            .then(r => r.ok ? r.blob() : null)
            .then(blob => {
              if (!blob) return
              get().setEditRepaintFrame(
                new File([blob], repaintFrameName, { type: blob.type || 'image/png' }),
                repaintFrame,
                URL.createObjectURL(blob),
              )
            })
            .catch(() => {})
        }
      }
      if (editSubMode === 'recast') {
        const savedMappings = p.edit_recast_character_mappings
        if (Array.isArray(savedMappings)) {
          const restoredMappings = savedMappings
            .slice(0, 5)
            .map((raw, index): RecastCharacterMapping | null => {
              if (!raw || typeof raw !== 'object') return null
              const mapping = raw as Record<string, unknown>
              const refPath = String(mapping.ref_image_path || '')
              const target = String(mapping.target || '').trim()
              if (!refPath || !target) return null
              const refName = refPath.replace(/\\/g, '/').split('/').pop() || ''
              const additionalPaths = Array.isArray(mapping.additional_ref_image_paths)
                ? mapping.additional_ref_image_paths.map(path => String(path || '')).filter(Boolean)
                : []
              return {
                id: String(mapping.id || `recast-${index + 1}`),
                target,
                refFile: null,
                refPath,
                refUrl: api.getFileUrl(refName),
                additionalRefs: additionalPaths.map(path => {
                  const name = path.replace(/\\/g, '/').split('/').pop() || ''
                  return { file: null, path, url: api.getFileUrl(name) }
                }),
                referenceAlignedToSource: mapping.reference_aligned_to_source === true,
              }
            })
            .filter((mapping): mapping is RecastCharacterMapping => mapping !== null)
          if (restoredMappings.length > 0) {
            set({
              editRecastMappings: restoredMappings,
              editRecastTarget: restoredMappings[0].target,
              editRecastPersonCount: restoredMappings.length,
              editRecastRefFile: null,
              editRecastRefPath: restoredMappings[0].refPath,
              editRecastRefUrl: restoredMappings[0].refUrl,
              editRecastRefAligned: restoredMappings[0].referenceAlignedToSource,
            })
          }
        }
        if (!Array.isArray(savedMappings) || savedMappings.length === 0) {
          set(s => ({
            editRecastMappings: [{
              ...(s.editRecastMappings[0] || DEFAULT_RECAST_MAPPING),
              target: String(p.edit_recast_target || 'person'),
              referenceAlignedToSource: p.edit_recast_ref_aligned === true,
            }],
          }))
        }
        if (p.edit_recast_target) set({ editRecastTarget: p.edit_recast_target as string })
        if (p.edit_recast_person_count != null) {
          const count = Number(p.edit_recast_person_count)
          set({ editRecastPersonCount: Math.min(5, Math.max(1, Number.isFinite(count) ? Math.round(count) : 1)) })
        }
        set({
          editRecastIsolateReference: p.edit_recast_isolate_reference !== false,
          editRecastAutoFaceDetail: p.edit_recast_auto_face_detail !== false,
          editRecastEnhancePrompt: p.edit_recast_enhance_prompt === true,
          editRecastProtectBystanders: p.edit_recast_protect_bystanders === true,
          editRecastPreserveBystanders: p.edit_recast_preserve_bystanders !== undefined
            ? p.edit_recast_preserve_bystanders === true
            : p.edit_recast_preserve_scene_reference !== undefined
              ? p.edit_recast_preserve_scene_reference === true
              : true,
          editRecastUseRelighting: p.edit_recast_use_relighting === true,
          editRecastResolutionProfile: p.edit_recast_resolution_profile === '704p'
            ? '704p'
            : p.edit_recast_resolution_profile === '512p'
              ? '512p'
              : '480p',
        })
        const recastRef = (p.edit_recast_ref_path as string) || ''
        if (recastRef) {
          const refName = recastRef.replace(/\\/g, '/').split('/').pop() || ''
          // Recast references can be either uploads or Image-mode outputs.
          const refUrl = api.getFileUrl(refName)
          fetch(refUrl)
            .then(r => r.ok ? r.blob() : null)
            .then(blob => {
              if (!blob) return
              const file = new File([blob], refName, { type: blob.type || 'image/png' })
              get().setEditRecastRef(
                file,
                recastRef,
                URL.createObjectURL(file),
                p.edit_recast_ref_aligned === true,
              )
            })
            .catch(() => {})
        }
      }
      if (editSubMode === 'outpaint') {
        // Padding (pixels) — preserved as-is; the OutpaintCanvas reads
        // outpaintAspect + outpaintVideoBox to compose, but we also mirror
        // the pixel pads to outpaintPadding so legacy code paths line up.
        const padTop = (p.outpaint_pad_top as number) ?? 0
        const padBottom = (p.outpaint_pad_bottom as number) ?? 0
        const padLeft = (p.outpaint_pad_left as number) ?? 0
        const padRight = (p.outpaint_pad_right as number) ?? 0
        set({ outpaintPadding: { top: padTop, bottom: padBottom, left: padLeft, right: padRight } })

        const canvasW = (p._outpaint_canvas_w as number) || 0
        const canvasH = (p._outpaint_canvas_h as number) || 0
        const savedAspect = String(p.outpaint_aspect || '') as OutpaintAspect
        const validSavedAspect = (
          savedAspect === 'source'
          || _OUTPAINT_ASPECT_RATIOS.some(([aspect]) => aspect === savedAspect)
        )
        let restoredAspect: OutpaintAspect | null = validSavedAspect ? savedAspect : null
        if (!restoredAspect) {
          let aspectW = canvasW
          let aspectH = canvasH
          if (aspectW <= 0 || aspectH <= 0) {
            const resolutionMatch = /^(\d+)x(\d+)$/i.exec(String(p.resolution || '').trim())
            if (resolutionMatch) {
              aspectW = Number(resolutionMatch[1])
              aspectH = Number(resolutionMatch[2])
            }
          }
          restoredAspect = _inferOutpaintAspect(aspectW, aspectH)
        }
        if (restoredAspect) set({ outpaintAspect: restoredAspect })
        if (p.outpaint_resolution_preset) {
          set({ outpaintResolutionPreset: p.outpaint_resolution_preset as 'auto' | '480p' | '540p' | '720p' | '1080p' })
        }
        if (p.outpaint_source_preservation != null) {
          set({ outpaintSourcePreservation: p.outpaint_source_preservation as number })
        }
        if (p.outpaint_lora_strength_ui != null) {
          set({ outpaintLoraStrength: p.outpaint_lora_strength_ui as number })
        }
        if (p.outpaint_mask_preserving != null) {
          set({ outpaintMaskPreserving: !!p.outpaint_mask_preserving })
        }

        // Recompute the canvas-relative video box from saved pad pixels +
        // saved canvas dimensions, so the OutpaintCanvas reproduces the
        // exact composition. Falls back to centered-fit if anything is
        // missing.
        if (canvasW > 0 && canvasH > 0) {
          const savedX = (p._outpaint_overlay_x as number) ?? padLeft
          const savedY = (p._outpaint_overlay_y as number) ?? padTop
          const srcW = (p._outpaint_overlay_w as number)
            || (canvasW - padLeft - padRight)
          const srcH = (p._outpaint_overlay_h as number)
            || (canvasH - padTop - padBottom)
          if (srcW > 0 && srcH > 0) {
            set({
              outpaintVideoBox: {
                x: savedX / canvasW,
                y: savedY / canvasH,
                w: srcW / canvasW,
                h: srcH / canvasH,
              },
            })
          }
        }

        // Audio/sync toggles
        if (p._outpaint_preserve_audio != null) {
          set({ outpaintPreserveSourceAudio: !!p._outpaint_preserve_audio })
        }
        if (p._outpaint_lock_source_pixels != null) {
          set({ outpaintLockSourcePixels: !!p._outpaint_lock_source_pixels })
        }
        if (p._outpaint_trim_smear != null) {
          set({ outpaintTrimSmear: !!p._outpaint_trim_smear })
        }
      }
    }
  },

  rerollGeneration: async () => {
    // Await the (now async, self-healing) settings load before generating, so a
    // slow on-demand metadata fetch can't let the reroll fire with stale params.
    await get().loadSettingsFromOutput()
    // Small delay to let state settle, then generate
    setTimeout(() => get().startGeneration(), 100)
  },

  rejoinClipGroup: async (groupId) => {
    try {
      const result = await api.rejoinClips(groupId, get().activeWorkspace)
      // Refresh through the canonical mapper; /outputs returns `outputs`, not
      // the legacy `files` key used here previously.
      await get().loadOutputs()
      // Select the new file
      const allOutputs = get().filteredOutputs()
      const newIdx = allOutputs.findIndex(o => o.name === result.filename)
      if (newIdx >= 0) {
        set({ selectedOutput: newIdx })
        get().loadOutputMetadata(result.filename)
      }
    } catch (e) {
      console.error('Failed to rejoin clips:', e)
    }
  },

  deleteSelectedOutput: async (name, workspace) => {
    const output = name
      ? get().outputs.find(candidate => candidate.name === name)
      : get().filteredOutputs()[get().selectedOutput]
    if (!output) return

    try {
      const result = await api.deleteOutput(
        output.name,
        output.artifact_class === 'final' && output.linked_component_count > 0,
        workspace || get().activeWorkspace,
      )
      if (result.components?.failed?.length) {
        window.alert(
          `The final output was deleted, but ${result.components.failed.length} linked artifact(s) could not be removed. They remain visible for retry.`,
        )
      }
    } catch (e) {
      console.error('Failed to delete output:', e)
      window.alert(e instanceof Error ? e.message : 'Failed to delete output')
    } finally {
      // Cascades and partial cleanup can change several visible rows.
      await get().loadOutputs()
    }
  },

  // ── Director Pipeline (server-side) ──────────────────────────────
  startDirectorPipeline: async () => {
    const state = get()
    const requestWorkspace = state.activeWorkspace
    const { directorPlannedClips, directorSceneDescription,
            directorAudioPath, directorAnalysis, directorReferenceImagePath,
            directorAutoMode, directorSeamless, directorResolution, directorAspectRatio,
            directorShotImageGuidance, directorVideoInferenceStepsByModel,
            directorVideoMaxShotFramesByModel,
            savedParamsPerMode, savedLoraPerMode,
            directorSpeakerMappings, directorVideoSpatialUpsampling, directorVideoFilmGrainIntensity,
            directorVideoFilmGrainSaturation, directorVideoSelfRefiner,
            shortFilmPath, shortFilmCharacters, shortFilmTargetDuration,
            shortFilmNarrative } = state

    set({ directorError: null, directorComponentError: null })
    const lifecycle = _beginDirectorPipelineLifecycle(requestWorkspace)
    try {
    // Model visibility writes are serialized. Await the current tail before
    // and after the catalog refresh so an immediate Director submission
    // cannot race a just-enabled exact recipe or a one-time visibility write.
    await _refreshDirectorModelAdmissionCatalog(() => get().loadModels())
    if (!lifecycle.ownsWorkspace()) return
    await _ensureSelectedH3StyleWorkflowReady(get)
    const imageRoleRequest = await _captureDirectorImageRoleRequest(get, state.explicitOutput)
    const workflowRequestState = get()
    const selectedVideoPreference = (workflowRequestState.selectedModelPerMode.video || '').trim()
    if (!selectedVideoPreference) {
      throw new api.DirectorRequestError('director_model_unavailable', 'video_model')
    }
    const h3WorkflowRequest = captureH3StyleWorkflowRequest(
      workflowRequestState.h3StyleWorkflowCatalog,
      selectedVideoPreference,
      workflowRequestState.h3StyleWorkflow,
    )
    const selectedVideoModel = h3WorkflowRequest.video_model
    const pipelineType: api.DirectorPipelineType = shortFilmPath === 'story'
      ? 'short_film_story'
      : shortFilmPath === 'audio'
        ? 'short_film_audio'
        : 'music_video'

    // Resolve every selected local reference before claiming its presence to
    // preflight. All uploads settle, but paths and labels are committed only
    // when the complete indexed selection succeeds.
    const assertCurrent = () => {
      if (!lifecycle.ownsWorkspace()) {
        throw new DOMException('The browser stopped waiting', 'AbortError')
      }
    }
    const referenceUploads = await Promise.allSettled([
      (async () => {
        if (directorReferenceImagePath) return directorReferenceImagePath
        if (!state.directorReferenceImage) return null
        assertCurrent()
        try {
          const uploaded = await api.uploadImage(state.directorReferenceImage)
          assertCurrent()
          return uploaded.path
        } catch (error) {
          if (_isBrowserAbort(error)) throw error
          throw new api.DirectorRequestError('director_reference_unavailable', 'starting_image')
        }
      })(),
      _resolveDirectorReferenceRows(
        state.directorCharacterRefs,
        state.directorCharacterRefPaths,
        state.directorCharacterRefLabels,
        'character_reference',
        api.uploadImage,
        assertCurrent,
      ),
      _resolveDirectorReferenceRows(
        state.directorLocationRefs,
        state.directorLocationRefPaths,
        state.directorLocationRefLabels,
        'location_reference',
        api.uploadImage,
        assertCurrent,
      ),
    ])
    if (!lifecycle.ownsWorkspace()) return
    const failedReferenceUpload = referenceUploads.find(
      (result): result is PromiseRejectedResult => result.status === 'rejected',
    )
    if (failedReferenceUpload) throw failedReferenceUpload.reason
    const refImagePath = (
      referenceUploads[0] as PromiseFulfilledResult<string | null>
    ).value
    const characterReferences = (
      referenceUploads[1] as PromiseFulfilledResult<DirectorReferenceRowsResult>
    ).value
    const locationReferences = (
      referenceUploads[2] as PromiseFulfilledResult<DirectorReferenceRowsResult>
    ).value
    const charPaths = characterReferences.paths
    const charLabels = characterReferences.labels
    const locPaths = locationReferences.paths
    const locLabels = locationReferences.labels
    set({
      directorReferenceImagePath: refImagePath,
      directorCharacterRefPaths: charPaths,
      directorLocationRefPaths: locPaths,
    })

    const directorPreflight = await api.preflightDirectorPipeline({
      pipeline_type: pipelineType,
      explicit_output: state.explicitOutput,
      video_model: selectedVideoModel,
      image_creator_model: imageRoleRequest.effective_creator_model,
      ...(imageRoleRequest.wire.image_creator_loras ? {
        image_creator_loras: imageRoleRequest.wire.image_creator_loras,
      } : {}),
      continuity_editor_model: imageRoleRequest.effective_editor_model,
      ...(imageRoleRequest.wire.image_editor_loras ? {
        continuity_editor_loras: imageRoleRequest.wire.image_editor_loras,
      } : {}),
      director_resolution_preset: directorResolution,
      director_aspect_ratio: directorAspectRatio,
      reference_presence: {
        starting_image: Boolean(refImagePath),
        character: charPaths.length > 0,
        location: locPaths.length > 0,
      },
    })
    if (directorPreflight.resolved.pipeline_type !== pipelineType) {
      throw new api.DirectorRequestError('director_model_unavailable', 'video_model')
    }
    if (directorPreflight.resolved.video_model !== selectedVideoModel) {
      throw new api.DirectorRequestError('director_model_unavailable', 'video_model')
    }
    if (directorPreflight.resolved.image_creator_model !== imageRoleRequest.effective_creator_model) {
      throw new api.DirectorRequestError('director_model_unavailable', 'image_creator_model')
    }
    if (directorPreflight.resolved.continuity_editor_model !== imageRoleRequest.effective_editor_model) {
      throw new api.DirectorRequestError('director_model_unavailable', 'continuity_editor_model')
    }
    if (
      directorPreflight.resolved.director_resolution_preset !== directorResolution
      || directorPreflight.resolved.director_aspect_ratio !== directorAspectRatio
    ) {
      throw new Error('Director resolution preflight returned a different selection.')
    }
    if (!directorPreflight.resolved.video_resolution) {
      throw new Error('Director resolution preflight did not resolve the selected video canvas.')
    }
    if (!directorPreflight.resolved.image_resolution) {
      throw new Error('Director resolution preflight did not resolve the selected image canvas.')
    }
    const [videoModelDefaults, videoModelOptions] = await Promise.all([
      api.fetchDefaults(selectedVideoModel).catch(() => ({})),
      api.fetchModelOptions(selectedVideoModel).catch(() => null),
    ])
    if (!lifecycle.ownsWorkspace()) return
    const directorImageResolution = directorPreflight.resolved.image_resolution
    const directorVideoResolution = directorPreflight.resolved.video_resolution
    const fps = videoModelOptions?.fps ?? 16
    const savedVideoParams = savedParamsPerMode.video || {}
    const matchingVideoParams = savedVideoParams.model_type === selectedVideoModel ? savedVideoParams : {}
    const rawDefaultVideoSteps = (
      videoModelOptions?.default_num_inference_steps
      ?? (videoModelDefaults as Record<string, unknown>).num_inference_steps
      ?? 8
    )
    const parsedDefaultVideoSteps = Number(rawDefaultVideoSteps)
    const defaultVideoSteps = Number.isFinite(parsedDefaultVideoSteps) && parsedDefaultVideoSteps > 0
      ? Math.max(1, Math.min(50, Math.round(parsedDefaultVideoSteps)))
      : 8
    const configuredVideoSteps = directorVideoInferenceStepsByModel[selectedVideoModel]
    const directorVideoSteps = videoModelOptions?.lock_inference_steps
      ? defaultVideoSteps
      : (configuredVideoSteps ?? defaultVideoSteps)
    const directorMaxShotFrames = directorVideoMaxShotFramesByModel[selectedVideoModel]

    const selectedVideoDefinition = state.models.find(
      model => model.model_type === selectedVideoModel,
    )
    const supportsVoiceReference = selectedVideoDefinition?.director?.supports_voice_reference === true
    const voiceReferenceMode = selectedVideoDefinition?.director?.voice_reference_mode ?? 'none'

    // Upload voice reference if not already uploaded
    let voiceRefPath = state.directorVoiceRefPath
    if (supportsVoiceReference && !voiceRefPath && state.directorVoiceRef) {
      try {
        if (!lifecycle.ownsWorkspace()) return
        const uploaded = await api.uploadAudio(state.directorVoiceRef)
        if (!lifecycle.ownsWorkspace()) return
        voiceRefPath = uploaded.path
        set({ directorVoiceRefPath: voiceRefPath })
      } catch {
        if (!lifecycle.ownsWorkspace()) return
        /* skip */
      }
    }

    const directorVideoParams: Record<string, unknown> = {
      ...videoModelDefaults,
      ...matchingVideoParams,
      num_inference_steps: directorVideoSteps,
      resolution: directorVideoResolution,
    }
    if (H3_STUDIO_MODELS.has(selectedVideoModel)) {
      // Director owns `director_max_shot_frames` as its explicit ceiling.
      // Never leak a model/default rolling-window field into automatic H3
      // planning; the backend materializes execution geometry only after it
      // commits the deterministic segment plan.
      delete directorVideoParams.sliding_window_size
    }

    const pipelineParams: Record<string, unknown> = {
      pipeline_type: pipelineType,
      ...(state.directorRequestWorkspace === state.activeWorkspace && state.directorRequestId
        ? { director_request_id: state.directorRequestId }
        : {}),
      auto_mode: directorAutoMode,
      workspace: requestWorkspace,
      private_output: state.privateOutput,
      explicit_output: state.explicitOutput,
      scene_description: directorSceneDescription,
      visual_style: state.directorVisualStyle === 'custom'
        ? state.directorCustomVisualStyle.trim() || undefined
        : state.directorVisualStyle || undefined,
      h3_style_workflow: h3WorkflowRequest.h3_style_workflow,
      audio_path: directorAudioPath,
      reference_image_path: refImagePath,
      character_ref_paths: charPaths.length > 0 ? charPaths : undefined,
      character_ref_labels: charLabels.length > 0 ? charLabels : undefined,
      location_ref_paths: locPaths.length > 0 ? locPaths : undefined,
      location_ref_labels: locLabels.length > 0 ? locLabels : undefined,
      planned_clips: directorPlannedClips,
      seamless: directorSeamless,
      shot_image_guidance: directorShotImageGuidance,
      director_resolution_preset: directorResolution,
      director_aspect_ratio: directorAspectRatio,
      director_max_shot_frames: directorMaxShotFrames,
      fps,
      frames_steps: videoModelOptions?.frames_steps || 8,
      frames_minimum: videoModelOptions?.frames_minimum || 41,

      // Director v2 flag — see prior callsites: ?? not || so explicit
      // user toggle-off is respected (legacy v1), only fall back to
      // true when the field is undefined.
      use_director_v2: state.servicesConfig?.use_director_v2 ?? true,

      // LLM
      llm_model_id: state.servicesConfig?.llm_model_id || state.llmStatus?.model_id,
      llm_device: state.servicesConfig?.llm_device || state.llmStatus?.device,
      llm_provider: state.servicesConfig?.llm_provider || 'local',
      lyrics: directorAnalysis?.lyrics || '',
      bpm: directorAnalysis?.bpm,
      speaker_mappings: directorSpeakerMappings,
      characters: shortFilmCharacters,
      target_duration: shortFilmTargetDuration,
      narrative_mode: shortFilmNarrative,

      // Preflight resolves every role independently. Start sends those exact
      // sealed model IDs (plus the already validated role-local LoRAs), so a
      // vanished recipe cannot be silently substituted between the checks.
      image_creator_model: directorPreflight.resolved.image_creator_model,
      image_editor_model: directorPreflight.resolved.continuity_editor_model,
      ...(imageRoleRequest.wire.image_creator_loras ? {
        image_creator_loras: imageRoleRequest.wire.image_creator_loras,
      } : {}),
      ...(imageRoleRequest.wire.image_editor_loras ? {
        image_editor_loras: imageRoleRequest.wire.image_editor_loras,
      } : {}),
      // Only deliberate common overrides cross this boundary. Each role's
      // model-specific steps/guidance come from authoritative server defaults.
      image_params: {
        resolution: directorImageResolution,
      },
      // Video gen settings
      video_model: selectedVideoModel,
      video_params: directorVideoParams,
      video_loras: savedLoraPerMode.video || {},
      video_spatial_upsampling: directorVideoSpatialUpsampling,
      video_film_grain_intensity: directorVideoFilmGrainIntensity,
      video_film_grain_saturation: directorVideoFilmGrainSaturation,
      video_self_refiner: directorVideoSelfRefiner,
      audio_scale: get().directorAudioScale,

      // Voice identity (ID-LoRA). The CelebVHQ ID-LoRA auto-loads
      // for both dev and distilled pipelines when voice_reference is
      // set (see ltx2.get_loras_transformer).
      ...(supportsVoiceReference && voiceRefPath ? {
        voice_reference: voiceRefPath,
        ...(voiceReferenceMode === 'id_lora' ? {
          identity_guidance_scale: state.directorIdentityGuidanceScale,
        } : {}),
      } : {}),
    }

      if (!lifecycle.ownsWorkspace()) return
      const { pipeline_id } = await api.startPipeline(pipelineParams)
      if (!lifecycle.ownsWorkspace()) return
      get().activateDirectorImageRoles()
      _stopDirectorPreparationPoll()
      _storeDirectorPreparation(null, null)
      set({
        pipelineId: pipeline_id,
        pipelineStatus: null,
        pipelinePolling: true,
        directorStep: 'plan',
        directorLoading: true,
        directorError: null,
        directorComponentError: null,
        directorRequestId: null,
        directorRequestWorkspace: null,
        directorPreparationStatus: null,
      })
      get().pollPipelineStatus()
    } catch (e) {
      if (!lifecycle.ownsWorkspace()) return
      const msg = e instanceof Error ? e.message : 'Pipeline failed to start'
      set({
        directorError: msg,
        directorComponentError: e instanceof api.DirectorRequestError
          ? {
              code: e.code,
              component: e.component,
              message: e.message,
              ...(e.reference_index !== undefined ? { reference_index: e.reference_index } : {}),
            }
          : null,
      })
    } finally {
      lifecycle.dispose()
    }
  },

  continuePipeline: async (updates) => {
    const pid = get().pipelineId
    if (!pid) return
    const workspace = get().activeWorkspace
    try {
      await api.continuePipeline(pid, updates)
      if (get().pipelineId !== pid || get().activeWorkspace !== workspace) return
      set({ directorLoading: true, pipelineStatus: null })
    } catch (e) {
      console.error('Failed to continue pipeline:', e)
    }
  },

  stopPipeline: async () => {
    const pid = get().pipelineId
    if (!pid) return
    const workspace = get().activeWorkspace
    _directorPipelineLifecycleToken = null
    try {
      await api.stopPipeline(pid)
      if (get().pipelineId !== pid || get().activeWorkspace !== workspace) return
      set({ pipelineId: null, pipelineStatus: null, pipelinePolling: false, directorLoading: false })
    } catch (e) {
      console.error('Failed to stop pipeline:', e)
    }
  },

  pollPipelineStatus: () => {
    const pid = get().pipelineId
    if (!pid) return
    const workspace = get().activeWorkspace
    const outputRefresh: GalleryRefreshClock = {
      lastRefreshAt: Date.now(),
      pendingDelta: false,
      hasRefreshed: false,
    }
    let outputSignature = ''
    let nextPollMs = 2000

    const poll = async () => {
      if (
        !get().pipelinePolling
        || get().pipelineId !== pid
        || get().activeWorkspace !== workspace
      ) return

      try {
        const status = await api.fetchPipelineStatus(pid)
        if (
          !get().pipelinePolling
          || get().pipelineId !== pid
          || get().activeWorkspace !== workspace
        ) return
        if (status.id !== pid || status.workspace !== workspace) {
          throw new Error('Pipeline status identity mismatch')
        }
        const pipelineActive = isDirectorPipelineActive(status)
        const pipelineTerminal = status.status === 'completed'
          || status.status === 'failed'
          || status.status === 'cancelled'
        const pipelineBlocked = status.status === 'blocked'
        const pipelineFailed = status.status === 'failed' || status.status === 'cancelled'
        set({
          pipelineStatus: status,
          ...(pipelineActive ? {
            directorLoading: true,
          } : {
            directorLoading: false,
            directorLoadingMessage: null,
          }),
          ...((pipelineTerminal || pipelineBlocked) ? { pipelinePolling: false } : {}),
          ...(pipelineFailed ? {
            directorError: status.error || 'Pipeline stopped',
            directorImageGenProgress: get().directorImageGenProgress
              ? { ...get().directorImageGenProgress!, status: 'error' }
              : null,
          } : {}),
        })

        // Sync pipeline state to director UI state
        if (status.clip_plans?.length && !get().directorClipPlans.length) {
          set({
            directorClipPlans: status.clip_plans,
            directorStep: 'review',
          })
        }

        if (status.clip_images?.length) {
          // Strip empty filenames — those are failed-shot sentinels from the
          // pipeline (clip_images.append("") on exception). If we keep them,
          // downstream <img src={getFileUrl("")} /> hits /api/v1/file/ which
          // can resolve to a stale cached file rather than nothing, producing
          // the "same unrelated image over and over" symptom users see when
          // image gen fails (e.g. incompatible LoRA architecture).
          // clipIndex is captured BEFORE filtering so it stays aligned to
          // the original clip plan position even when failed shots drop out.
          const images = status.clip_images
            .map((filename, i) => ({
              clipIndex: i,
              prompt: status.clip_plans?.[i]?.image_prompt || '',
              file: null as unknown as File,
              filename,
            }))
            .filter(img => img.filename && img.filename.length > 0)
          set({ directorClipImages: images })
        }

        // Handle phase transitions only while the server still reports an
        // active run. A failed response retains its last phase, so phase alone
        // must never restart progress UI after the terminal state was applied.
        if (pipelineActive && status.phase === 'polishing_prompts') {
          set({
            directorImageGenProgress: {
              current: status.progress.current,
              total: status.progress.total,
              currentClipLabel: status.progress.message || 'Polishing prompts (3rd pass)...',
              status: 'generating',
            },
          })
        } else if (pipelineActive && status.phase === 'generating_images') {
          set({
            directorStep: 'generate_images',
            directorImageGenProgress: {
              current: status.progress.current,
              total: status.progress.total,
              currentClipLabel: status.progress.message,
              status: 'generating',
            },
          })
        } else if (pipelineActive && status.phase === 'generating_video') {
          set({ directorStep: 'review_video' })
        }

        if (
          pipelineActive
          && (status.phase === 'generating_images' || status.phase === 'generating_video')
        ) {
          const nextOutputSignature = JSON.stringify([
            status.phase,
            status.progress.current,
            status.progress.total,
            status.clip_images,
          ])
          const changed = nextOutputSignature !== outputSignature
          outputSignature = nextOutputSignature
          if (_coalescedGalleryRefreshDue(
            outputRefresh,
            changed,
            !document.hidden,
          )) {
            void get().refreshOutputs()
          }
        }

        // The exact pipeline status is the sole Director LLM telemetry
        // source. Poll faster only while planning or a transient pass is
        // active; other phases retain the lower background cadence.
        nextPollMs = pipelineActive && (
          status.phase === 'planning'
          || status.phase === 'polishing_prompts'
          || (status.llm_progress != null && !status.llm_progress.done)
        ) ? 400 : 2000

        // Handle pause
        if (status.status === 'paused') {
          set({ directorLoading: false })
          if (status.pause_reason === 'review_prompts') {
            set({ directorStep: 'review' })
          } else if (status.pause_reason === 'review_images') {
            set({ directorStep: 'review_video' })
          }
        }

        // Handle completion
        if (status.status === 'completed') {
          set({
            directorStep: 'review_video',
          })
          get().loadOutputs()
          return  // Stop polling
        }

        // Handle failure
        if (status.status === 'failed' || status.status === 'cancelled') {
          return  // Stop polling
        }

      } catch (e) {
        console.error('Pipeline poll error:', e)
      }

      // Continue polling
      if (
        get().pipelinePolling
        && get().pipelineId === pid
        && get().activeWorkspace === workspace
      ) {
        setTimeout(poll, nextPollMs)
      }
    }

    setTimeout(poll, 1000)
  },
}))
