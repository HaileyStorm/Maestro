export interface ModelFamily {
  id: string
  label: string
  order: number
}

export type DirectorPipelineType = 'music_video' | 'short_film_audio' | 'short_film_story'
export type DirectorShotImageGuidance = 'auto' | 'prompt_only' | 'generate'
export type DirectorShotImagePolicy = 'generate' | 'prompt_only' | 'direct_references'
export type DirectorImageRole = 'creator' | 'editor'

export interface DirectorCapabilityResult {
  compatible: boolean
  reason: string
}

export interface DirectorImageRoleCapabilityResult {
  compatible: boolean
  reasons: string[]
}

export interface DirectorModelCompatibility {
  /** Flat fields remain readable for legacy combined-image clients. */
  image: DirectorCapabilityResult & {
    creator: DirectorImageRoleCapabilityResult
    editor: DirectorImageRoleCapabilityResult
  }
  video: Record<DirectorPipelineType | 'seamless', DirectorCapabilityResult>
  supports_audio_input: boolean
  generates_audio: boolean
  supports_voice_reference: boolean
  voice_reference_mode?: 'none' | 'id_lora'
  video_strategy?: 'rolling_window' | 'bounded_start_end'
  audio_input_mode?: 'none' | 'generic_audio_guide'
  reference_mode?: 'none' | 'start_frame' | 'start_end'
  shot_image_support?: 'required' | 'optional' | 'direct_references'
  supports_endpoint_continuity?: boolean
  clip_min_frames?: number | null
  clip_max_frames?: number | null
  clip_frame_step?: number | null
  max_image_refs: number | null
}

export interface ModelDef {
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
  /** Server catalog capability used by image-only workflows. */
  image_outputs?: boolean
  director?: DirectorModelCompatibility
  is_downloaded?: boolean
  /** Whether Maestro may fetch this recipe through the generic downloader. */
  downloadable?: boolean
  /** The source contract is complete enough for owner-managed installation. */
  manual_installation_ready?: boolean
  availability_status?: string
  manual_checkpoint_verification_required?: boolean
  manual_checkpoint_verified?: boolean
  /** Public, host-path-neutral instructions for owner-managed checkpoints. */
  manual_installation?: ModelManualInstallation
  supported_operations?: string[]
  automatic_routing?: boolean
  verified?: boolean
  default_for_operations?: string[]
  revenue_eligible?: boolean | null
  fine_tuning_eligible?: boolean | null
  derivative_tooling?: boolean | null
  // Upstream catalog metadata; Maestro does not gate model visibility with it.
  nsfw_only?: boolean
  update_status?: string
  required_host_terms?: ModelHostTermRequirement[]
}

export interface ModelManualInstallation {
  filename: string
  size_bytes: number
  sha256: string
  source_url: string
  download_url: string
  destination_hint: 'app/ckpts' | string
  local_verification_required: boolean
}

export interface ModelHostTermRequirement {
  term: HostTermId
  version: number
  title: string
  license_url: string
  review_mode: 'manual_self_review'
  notice: string
}

// Reference Studio v2 uses canonical asset names on the wire. Older project
// manifests may still contain setting/item/style aliases and remain readable.
export type ProjectReferenceAssetType =
  | 'character'
  | 'location'
  | 'prop'
  | 'vehicle'
  | 'creature'
  | 'wardrobe'
  | 'world'

export type ProjectReferenceLegacyAssetType = 'setting' | 'item' | 'machine' | 'accessory' | 'style'
export type ProjectReferenceIntent = 'exact_spec' | 'generic' | 'brainstorming'
export type ProjectReferenceDepth = 'compact' | 'standard' | 'comprehensive' | 'custom'
export type ProjectReferenceManagedLayoutAssistMode = 'off'
export type ProjectReferenceAnchorBasis = 'anatomy' | 'primary_outfit' | 'least_occluded'
export type ProjectReferenceAnchorPrivacy =
  | 'private_blurred' | 'private_visible' | 'project_blurred' | 'project_visible'
export type ProjectReferenceLegacyAnchorPrivacy = ProjectReferenceAnchorPrivacy | 'standard'
export type ProjectReferencePreset =
  | 'identity' | 'performance' | 'wardrobe' | 'underlayers'
  | 'spatial' | 'lighting'
  | 'materials' | 'product' | 'functional'
  | 'construction' | 'exterior' | 'interior' | 'mechanical'
  | 'anatomy' | 'behavior' | 'look' | 'accessories'
  | 'visual_language' | 'environment' | 'cinematography'
export type ProjectReferenceDetailOperation = 'auto' | 'crop' | 'enhance' | 'reconstruct'
export type ProjectReferenceDetailKind =
  | 'custom'
  | 'face' | 'hands' | 'marking' | 'markings' | 'garment' | 'accessory'
  | 'material' | 'fixture' | 'prop' | 'signage'
  | 'mechanism' | 'control' | 'interior'
  | 'limb' | 'surface' | 'closure' | 'seam'
  | 'lighting' | 'composition' | 'motion'

export interface ProjectReferenceDetailCallout {
  custom_id: string
  label: string
  kind: ProjectReferenceDetailKind
  operation: ProjectReferenceDetailOperation
  source_role: string
}

export interface ProjectReferenceTypeFieldItem {
  id: string
  label: string
  custom: boolean
  group: string
}

export type ProjectReferenceLoraScope = 'auto' | 'generation' | 'editing'

export type LoraParameterValue = string | number | boolean
export type LoraParameterType = 'enum' | 'number' | 'integer' | 'boolean' | 'text'

export interface LoraParameterOption {
  value: LoraParameterValue
  label: string
}

export interface LoraParameterDefinition {
  id: string
  label: string
  type: LoraParameterType
  description?: string
  required: boolean
  default?: LoraParameterValue
  scopes: ProjectReferenceLoraScope[]
  roles: string[]
  minimum?: number
  maximum?: number
  step?: number
  options?: LoraParameterOption[]
  min_length?: number
  max_length?: number
}

export interface LoraParameterSchema {
  schema_version: 1
  schema_digest: string
  schema_source?: 'maestro_sidecar' | 'civitai_sidecar' | 'server_known_contract'
  trigger_disclosure?: {
    source: 'server_known_contract'
    activation_phrases: Array<{
      parameter_id: string
      value: string | boolean
      text: string
    }>
    scopes: ['generation']
    roles: string[]
  }
  parameters: LoraParameterDefinition[]
}

/**
 * Director's new image-role wire is intentionally independent from the
 * legacy Studio LoRA blob. `id` is the server-catalog filename, and a
 * schema-backed selection carries the exact current digest plus values.
 */
export interface DirectorImageRoleLoraSelection {
  id: string
  multiplier: number
  parameter_schema_digest?: string
  parameter_values?: Record<string, LoraParameterValue>
}

export interface ProjectReferenceAdditionalLora {
  id: string
  multiplier: number
  scope: ProjectReferenceLoraScope
  parameter_schema_digest?: string
  parameter_values?: Record<string, LoraParameterValue>
}

export interface ProjectReferencePrivateAdditionalLora extends ProjectReferenceAdditionalLora {
  /** Owner-private replay proof; strip before submitting the public request shape. */
  parameter_values_digest?: string
  parameter_expansion_digest?: string
}

export interface ProjectReferenceAdditionalLoraSummary {
  applied: Array<{
    id: string
    weight: number
    requested_scope: ProjectReferenceLoraScope
    resolved_scope: ProjectReferenceLoraScope[]
    roles: string[]
    parameters?: ProjectReferenceLoraParameterSummary
  }>
  skipped: Array<{
    id: string
    weight: number
    requested_scope: ProjectReferenceLoraScope
    reason: string
    parameters?: ProjectReferenceLoraParameterSummary
  }>
}

export interface ProjectReferenceLoraParameterSummary {
  count: number
  ids: string[]
  schema_digest: string
  values_digest: string
  expansion_digest: string
}

export type ProjectReferenceCharacterGender = 'woman' | 'man' | 'non_binary' | 'unspecified'
export type ProjectReferenceCharacterAnatomy = 'breasts' | 'vulva' | 'penis'

/** Owner-authored Character facts. Server-only sealing state is never exposed here. */
export interface ProjectReferenceCharacterProfileInput {
  gender: ProjectReferenceCharacterGender
  age?: number | null
  explicit_anatomy: ProjectReferenceCharacterAnatomy[]
}

export interface ProjectReferencePublicCharacterProfile {
  schema_version: 1
  gender: { present: boolean; commitment: string | null }
  age: { present: boolean; commitment: string | null }
  explicit_anatomy: { count: number; commitments: string[] }
}

export interface ProjectReferencePublicManagedCharacterCallouts {
  schema_version: 1
  active_count: number
  tombstone_count: number
  rename_count: number
  commitments: string[]
}

export interface ProjectReferenceAuthoredDetailCalloutSummary {
  custom_id: string
  kind: ProjectReferenceDetailKind
  requested_operation: ProjectReferenceDetailOperation
  source_role: string
  target_role: string
  label_digest: string
}

export interface ProjectReferenceManagedDetailCalloutSummary {
  managed: true
  requested_operation: ProjectReferenceDetailOperation
}

export type ProjectReferencePlannedDetailCallout =
  | ProjectReferenceAuthoredDetailCalloutSummary
  | ProjectReferenceManagedDetailCalloutSummary

export interface ProjectReferenceResolvedModel {
  requested_model: string
  resolved_model: string | null
  resolved_provider: string | null
}

export interface ProjectReferenceTypeFieldMap {
  character: { poses?: ProjectReferenceTypeFieldItem[]; outfits?: ProjectReferenceTypeFieldItem[] }
  location: { zones?: ProjectReferenceTypeFieldItem[]; lighting?: ProjectReferenceTypeFieldItem[] }
  prop: { functions?: ProjectReferenceTypeFieldItem[]; scale?: ProjectReferenceTypeFieldItem[] }
  vehicle: { views?: ProjectReferenceTypeFieldItem[]; mechanisms?: ProjectReferenceTypeFieldItem[] }
  creature: { poses?: ProjectReferenceTypeFieldItem[]; anatomy?: ProjectReferenceTypeFieldItem[] }
  wardrobe: { views?: ProjectReferenceTypeFieldItem[]; materials?: ProjectReferenceTypeFieldItem[] }
  world: { composition?: ProjectReferenceTypeFieldItem[]; lighting?: ProjectReferenceTypeFieldItem[] }
}

export type ProjectReferenceTypeFields<
  T extends ProjectReferenceAssetType = ProjectReferenceAssetType,
> = Partial<ProjectReferenceTypeFieldMap[T]>

export interface ProjectReferenceManagedLayoutAssist {
  schema_version: 1
  mode: ProjectReferenceManagedLayoutAssistMode
  id: null
  provenance: {
    kind: 'server_allowlist'
    version: 'managed-layout-v1'
  }
}

export type ProjectReferenceOperation = 'generation' | 'edit' | 'repair' | 'callout'
export type ProjectReferenceOperationStatus = 'standard' | 'applied' | 'skipped'

export interface ProjectReferenceModelSchedule {
  model: string
  steps: number
  guidance: number
  guidance_key: 'guidance_scale' | 'embedded_guidance_scale'
  source: 'model_default' | 'explicit'
}

export interface ProjectReferenceOperationRoute {
  status: ProjectReferenceOperationStatus
  requested_model: string | null
  resolved_model: string | null
  schedule: ProjectReferenceModelSchedule | null
  recipe_id?: string
  verification_status?: string
  reason?: string
}

export interface ProjectReferenceOperationRouting {
  requested_capability: 'standard' | 'unrestricted_local'
  operations: Record<ProjectReferenceOperation, ProjectReferenceOperationRoute>
}

export interface ProjectReferencePackPlan {
  schema_version: 2
  planner_version: 'reference-pack-v2' | string
  intent: ProjectReferenceIntent
  reference_type: ProjectReferenceAssetType
  depth: ProjectReferenceDepth
  preset: ProjectReferencePreset
  anchor_basis: ProjectReferenceAnchorBasis
  anchor_privacy: ProjectReferenceAnchorPrivacy
  private_output: boolean
  sheet_count: number
  detail_callout_count: number
  ordered_sheet_roles: string[]
  ordered_output_roles: string[]
  mode: 'production' | 'hybrid' | 'draft'
  candidate_count: number
  anchor_strategy: 'canonical_anchor' | 'draft_one_shot'
  generation_model?: string
  editor_model?: string | null
  user_loras?: { count: number; preserved: boolean }
  additional_loras?: ProjectReferenceAdditionalLoraSummary
  explicit_output?: boolean
  explicit_convenience?: boolean
  content_capability?: 'standard' | 'unrestricted_local'
  initial_blur?: boolean
  intelligence_policy?: 'standard_auto' | 'uncensored_auto'
  operation_routing: ProjectReferenceOperationRouting
  detail_callouts?: ProjectReferencePlannedDetailCallout[]
  authored_settings?: {
    seal: string
    /** Present on current plans; raw authored style remains owner-private. */
    style_present?: boolean
    style_commitment?: string
    type_fields: Array<{
      field: string
      items: Array<Pick<ProjectReferenceTypeFieldItem, 'id' | 'custom' | 'group'>>
    }>
    detail_callouts: ProjectReferencePlannedDetailCallout[]
    character_profile?: ProjectReferencePublicCharacterProfile
    managed_character_callouts?: ProjectReferencePublicManagedCharacterCallouts
  }
  planning?: ProjectReferenceResolvedModel
  review?: ProjectReferenceResolvedModel & {
    status?: string
    final_correction?: {
      assessment_version: string
      rubric_version: string
      reference_type: ProjectReferenceAssetType
      template_id: 'reference-residual-correction'
      template_version: string
      severity: 'minor_residual' | 'material_residual'
      affected_roles: string[]
      reason_codes: string[]
      failed_item_ids: string[]
      score_basis_points: number
      rendered_brief: string
      commitment: string | null
    }
  }
  managed_layout_assist: ProjectReferenceManagedLayoutAssist
  plan_seal: string
}

export interface Resolution {
  label: string
  value: string
}

export type H3PerformanceProfileId = 'draft' | 'fast' | 'quality' | 'high' | '1080p_delivery' | 'ultra' | '4k_delivery' | 'spectrum_experimental' | 'lightx2v_experimental'
export type H3EstimateConfidence = 'calibrating' | 'low' | 'medium' | 'high'

export interface H3SegmentCountEstimate {
  minimum: number
  maximum: number
  likely: number
  source: string
  confidence: H3EstimateConfidence
  reason: string
}

export interface H3PerformanceEstimate {
  seconds: number
  generation_seconds?: number
  postprocess_seconds?: number
  delivery_resolution?: string
  postprocess_method?: string | null
  range_seconds: { low: number; high: number }
  confidence: H3EstimateConfidence
  sample_count: number
  source: string
  model_load_seconds: number | null
  model_load_state: 'resident' | 'cold' | 'unknown'
  download_seconds: null
  matched_factors: string[]
  uncertainty_reasons: string[]
}

export interface H3PerformanceProfileSettings {
  model_type: string
  num_inference_steps: number
  resolution: string
  custom_settings: Record<string, unknown>
  activated_loras: string[]
  loras_multipliers: string
  lora_weights: Record<string, number[]>
  tea_cache?: number
  spatial_upsampling: string
  delivery_resolution: string
  delivery_fit: string
}

export interface H3PerformanceProfile {
  id: H3PerformanceProfileId
  label: string
  description: string
  available: boolean
  fallback_reason: string | null
  /** Server-authored first higher compatible profile for an unavailable
   *  selection. Null when this profile is already available or no safe
   *  higher fallback exists. */
  fallback_profile_id: H3PerformanceProfileId | null
  download_required: boolean
  download_components?: string[]
  delivery_resolution?: string
  settings: H3PerformanceProfileSettings
  estimate: H3PerformanceEstimate
  segment_count_estimate?: H3SegmentCountEstimate
}

export interface H3EstimateRequest {
  model_type: string
  duration_seconds: number
  window_seconds: number
  window_overlap: number
  prompt: string
  /** Director scenes are planned independently by the runtime. */
  segment_scenes?: { duration_seconds: number; prompt: string }[]
  h3_adaptive_conditioning: boolean
  manual_segment_ceiling: boolean
  num_inference_steps: number
  resolution: string
  custom_settings: Record<string, unknown>
  activated_loras: string[]
  loras_multipliers: string
  tea_cache?: number
  spatial_upsampling: string
  delivery_resolution?: string
  delivery_fit?: string
  reference_shape: {
    has_start: boolean
    has_end: boolean
    image_count: number
    video_count: number
    audio_count: number
  }
  explicit_output: boolean
}

export interface H3EstimateResponse {
  current: {
    estimate: H3PerformanceEstimate
    segment_count_estimate: H3SegmentCountEstimate
  }
  segment_count_estimate: H3SegmentCountEstimate
  profiles: H3PerformanceProfile[]
}

export interface GenerateParams {
  prompt: string
  /** ACE-Step "Music Caption" — style/genre/instruments/mood (music mode). */
  alt_prompt?: string
  model_type: string
  resolution: string
  video_length: number
  num_inference_steps: number
  guidance_scale: number
  seed: number
  image_mode: number
  negative_prompt: string
  repeat_generation: number
  activated_loras: string[]
  loras_multipliers: string
  image_start?: string | string[] | null
  image_end?: string | string[] | null
  multi_prompts_gen_type?: number
  sliding_window_size?: number
  sliding_window_overlap?: number
  sliding_window_discard_last_frames?: number
  /** Automatically choose FL2VA frame anchoring vs Ref2VA semantic/temporal
   * continuity per H3 segment. Enabled by default for long Studio videos. */
  h3_adaptive_conditioning?: boolean
  /** Explicit acknowledgement required whenever the effective plan loads the
   * separately licensed Ref2VA checkpoint. Filled from the local terms UI. */
  h3_ref2va_terms_accepted?: boolean
  /** One-shot durable server-side preparation before this generation. */
  enhance_before_generate?: boolean
  /** Exact server-catalog ID; never a client-authored workflow object/brief. */
  h3_style_workflow?: string
  h3_segment_overrides?: Array<{
    model_type: 'minimax_h3' | 'minimax_h3_pinkcherry_fl2va' | 'minimax_h3_w4a8_fl2va' | 'minimax_h3_ref2va'
    drop_semantic_refs?: boolean
    reason?: string
  }>
  h3_boundary_overrides?: Array<{ type: H3SegmentBoundary['type'] }>
  guidance_phases?: number
  video_prompt_type?: string
  audio_prompt_type?: string
  image_prompt_type?: string
  input_video_strength?: number
  flow_shift?: number
  audio_guide?: string
  audio_guide2?: string
  audio_guide3?: string
  audio_scale?: number
  video_guide?: string
  video_guide2?: string
  video_guide3?: string
  force_fps?: string
  image_refs?: string[]
  frames_positions?: string
  injection_strength?: number
  settings_version?: number
  self_refiner_setting?: number
  stage2_steps?: number
  generation_mode?: string
  per_clip_frames?: number[]
  remove_background_images_ref?: number
  // TTS-specific
  duration_seconds?: number
  pause_seconds?: number
  temperature?: number
  custom_settings?: Record<string, unknown>
  tea_cache?: number
  spatial_upsampling?: string
  delivery_resolution?: string
  delivery_fit?: string
  // Loose params: backend accepts additional optional fields. Declared
  // explicitly here so TypeScript narrows JSX children correctly (an
  // index signature widens explicit fields to `unknown` in some contexts).
  progressive_pipeline?: boolean
  single_stage_pipeline?: boolean
  // Runs the reference two-stage pipeline (baked-in TenStrip 10Eros V5
  // workflow config) instead of the standard one. Only sent for models
  // whose def declares reference_pipeline support.
  reference_pipeline?: boolean
  progressive_stage1_image_weight?: number
  progressive_stage2_steps?: number
  progressive_stage2_sigma?: number
  progressive_stage3_steps?: number
  progressive_stage3_sigma?: number
  progressive_stage3_image_weight?: number
  stg_scale?: number
  // STG only runs when the backend sees perturbation_switch === 2 with the
  // model-correct perturbation_layers; startGeneration derives the switch
  // from stg_scale and _applyModelDefaults supplies the layers/window.
  perturbation_switch?: number
  perturbation_layers?: number[]
  perturbation_start_perc?: number
  perturbation_end_perc?: number
  cfg_rescale?: number
  use_gradient_estimation?: boolean
  ge_gamma?: number
  ge_alpha?: number
  keyframe_conditioning_mode?: string
  keyframe_inject_mode?: string
  MMAudio_setting?: number
  MMAudio_prompt?: string
  MMAudio_neg_prompt?: string
  // Continue / Blend mode
  video_source?: string
  // TTS post-processing extras
  tts_dynaudnorm?: boolean
  tts_comp_threshold?: number
  tts_comp_attack?: number
  tts_comp_release?: number
  tts_comp_makeup?: number
  tts_voice_count?: number
}

/** OOM recovery metadata returned with failed jobs and pipelines.
 *  Generation OOMs omit `stage`; H3 delivery OOMs use path-safe fields to
 *  identify the completed native generation and delivery recovery state. */
export interface OomInfo {
  is_oom: true
  /** Present for an OOM in H3 delivery after native generation completed. */
  stage?: 'h3_delivery'
  /** The vram_safety_coefficient value in effect when the OOM happened. */
  current_coefficient: number
  /** Suggested next-lower coefficient (current - 0.10), or null when the
   *  current coefficient is already at the 0.50 floor. */
  suggested_coefficient: number | null
  /** Truncated stringified exception for UI display (≤300 chars). */
  message: string
  /** Validated exact-delivery target. Empty when the backend cannot safely
   *  represent the requested target. */
  requested_target?: string
  /** Whether Maestro still owns a private native result for recovery. */
  native_available?: boolean
  /** Number of automatic identical delivery retries already attempted. */
  retry_count?: number
  /** Present on a failed postprocess-only recovery child. The original
   *  source job remains the authority for refreshed recovery actions. */
  manual_retry_count?: number
  /** Whether a preserved native result makes the failure recoverable. */
  recoverable?: boolean
  /** Path-free backend recovery facts/capabilities. Unknown values must not
   *  be rendered as user actions. */
  actions?: string[]
}

export type LogicalJobKind = 'reference_pack_parent' | 'reference_pack_child'

export type AccountRole = 'owner' | 'user'

export interface AccountSummary {
  id: string
  username: string
  role: AccountRole
  disabled: boolean
  created_at: number
  has_email: boolean
  passkey_credentials: number
  passkey_authentication_available: boolean
}

export interface AccountContext {
  enabled: boolean
  authenticated: boolean
  account: AccountSummary | null
  capabilities: string[]
  reauthenticated: boolean
  passkey_authentication_available: boolean
  /** Present only on the dedicated account-context response. */
  bootstrap_available?: boolean
}

export interface AccountSession {
  id: string
  device_label: string
  remote_created: boolean
  created_at: number
  last_seen_at: number
  expires_at: number
  current: boolean
}

export type AccountNoncePurpose =
  | 'bootstrap'
  | 'login'
  | 'reauth'
  | 'recover'
  | 'change_password'
  | 'rotate_recovery_codes'
  | 'create_account'
  | 'disable_account'
  | 'revoke_session'
  | 'revoke_all_sessions'

export interface AccountAuthResult {
  account: AccountSummary
  recovery_codes?: string[]
}

export type SupportProviderState = 'disabled' | 'unconfigured' | 'available'

export interface SupportProvider {
  provider_id: string
  display_name: string
  funding_modes: string[]
  description: string
  enabled: boolean
  configured: boolean
  state: SupportProviderState
  support_url: string | null
}

export interface SupportPriorityExclusion {
  capability_id: string
  support_priority_eligible: boolean
  marker: string
  creator_term?: string
}

export interface SupportPriorityPolicy {
  scheduler_enforcement_enabled: boolean
  effective_priority_boost: boolean
  state: string
  exclusions: SupportPriorityExclusion[]
  notice: string
}

export interface SupportPublicProjection {
  schema_version: number
  provider_catalog: {
    schema_version: number
    provider_neutral: boolean
    providers: SupportProvider[]
  }
  benefit_availability: {
    scheduler_enforcement_enabled: boolean
    effective_benefits: string[]
    state: string
  }
  support_priority: SupportPriorityPolicy
}

export interface ResponsibleUseNotice {
  document_id: string
  version: number
  content_sha256: string
  digest_algorithm: 'sha256'
  title: string
  paragraphs: string[]
}

export interface ResponsibleUseStatus {
  document_id: string
  document_version: number
  content_sha256: string
  accepted: boolean
  accepted_at: string | null
  state: string
}

export interface ResponsibleUseProjection {
  notice: ResponsibleUseNotice
  status: ResponsibleUseStatus
}

export type SupportAllowanceSourceKind = 'free' | 'one_time_support' | 'recurring_support'
export type SupportAllowanceStatus = 'active' | 'inactive' | 'refunded' | 'expired' | 'capped' | 'canceled'
export type SupportAllowanceRefundState = 'not_applicable' | 'none' | 'partial' | 'full' | 'excess'

export interface SupportRecordedAllowanceSource {
  source: SupportAllowanceSourceKind
  granted_allowance: number
  effective_allowance: number
  expires_at: string | null
  status: SupportAllowanceStatus
  refund_state: SupportAllowanceRefundState
}

export interface SupportRecordedAllowance {
  state: 'recorded_not_enforced'
  enforcement_enabled: false
  unit: string
  as_of: string
  effective_allowance: number
  sources: SupportRecordedAllowanceSource[]
}

/** Deliberately excludes raw contribution totals, subjects, and audit events. */
export interface SupportAccountSummary {
  event_count: number
  one_time_tier: string | null
  recurring_tier: string | null
  active_recurring_count: number
  recorded_allowance?: SupportRecordedAllowance
  benefits: {
    state: string
    scheduler_enforcement_enabled: boolean
    effective_benefits: string[]
    recorded_eligibility: string[]
  }
}

export interface SupportSelfProjection {
  public: SupportPublicProjection
  account: SupportAccountSummary
  responsible_use: ResponsibleUseProjection
}

export type SupportAdminEventKind =
  | 'one_time_contribution'
  | 'recurring_started'
  | 'recurring_renewed'
  | 'refund'
  | 'chargeback'
  | 'recurring_canceled'
  | 'fulfillment_set'
  | 'account_link_verified'
  | 'account_link_revoked'

export type SupportFulfillmentStatus = 'pending' | 'complete' | 'declined'

export interface SupportAdminAuditEvent {
  sequence: number
  event_id: string
  provider: string
  source_reference: string
  kind: SupportAdminEventKind
  occurred_at: string
  received_at: string
  amount_minor: number
  currency: string
  contract_reference: string | null
  related_reference: string | null
  fulfillment_item: string | null
  fulfillment_status: SupportFulfillmentStatus | null
  actor_reference: string | null
}

export interface SupportAdminFulfillment {
  target_event_id: string | null
  item: string
  status: SupportFulfillmentStatus
  audit_event_id: string
  actor_reference: string
  changed_at: string
}

export interface SupportAdminDiscrepancy {
  event_id: string
  reason: 'unresolved_or_mismatched_adjustment' | 'adjustments_exceed_contribution'
}

export interface SupportAdminAudit {
  currency_totals_minor: Record<string, number>
  events: SupportAdminAuditEvent[]
  fulfillment: SupportAdminFulfillment[]
  discrepancies: SupportAdminDiscrepancy[]
  incomplete: boolean
}

export interface SupportAdminProjection {
  account: SupportAccountSummary
  audit: SupportAdminAudit
  responsible_use: ResponsibleUseStatus
  support_priority: SupportPriorityPolicy
}

export interface GenerationJob {
  id: string
  /** Server creation time in epoch seconds when known. */
  createdAt?: number
  status: 'preparing' | 'waiting_for_plan_approval' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number
  step: number
  totalSteps: number
  phase: string
  message: string
  outputFiles: string[]
  error: string | null
  /** Allowlisted, privacy-safe failure envelope. Unknown backend keys are dropped. */
  failureDetails?: {
    code?: string | null
    detail?: string | null
  } | null
  /** Terminal Reference-child correlation supplied on the owning parent. */
  failedChildJobId?: string | null
  failedChildStatus?: 'failed' | 'cancelled' | 'blocked' | null
  failedChildReason?: string | null
  /** Present on failed generation or delivery jobs that ran out of VRAM. */
  oomInfo?: OomInfo | null
  promptPreview?: string
  activeWindowPrompt?: string
  modelType?: string
  generationMode?: string
  workspace?: string
  windowCurrent?: number
  windowTotal?: number
  windowStep?: number
  windowTotalSteps?: number
  windowProgress?: number
  overallProgress?: number
  /** True when the active named phase has no truthful numeric denominator. */
  progressIndeterminate?: boolean
  queueWaitReason?: import('../api/client').QueueWaitReason | null
  /** Owner-scoped, closed execution facts. A higher attempt means earlier
   *  CPU progress/ETA was discarded and must not be presented as reusable. */
  resourceDescriptor?: import('../api/client').ResourceDescriptor | null
  /** Owner-scoped live relation for an internal child generation. */
  parentJobId?: string | null
  /** Exact server-authored logical queue role; absent for legacy and non-Reference jobs. */
  logicalJobKind?: LogicalJobKind
  h3SegmentPlan?: H3SegmentPlan | null
  planReviewRequired?: boolean
  planReviewTermsRequired?: boolean
  /** Server-authored absolute Unix epoch seconds; null outside plan review. */
  planReviewDeadline?: number | null
  currentSegmentModel?: string
  currentSegmentReason?: string
  currentSegmentBoundary?: H3SegmentBoundary | null
  etaSeconds?: number | null
  subtaskEtaSeconds?: number | null
  /** Frozen privacy-safe estimate returned at submission/status time. Kept
   *  until live step timing provides a more precise ETA. */
  h3Estimate?: H3PerformanceEstimate | null
  /** Generic restart recovery is intentionally separate from delivery-only
   *  H3 OOM recovery. These values are bounded, path-free server facts. */
  recoveryState?: import('../api/client').QueueRecoveryState | null
  recoveryInterrupted?: boolean
  recoveryBlocked?: boolean
  recoveryAttempt?: number
  recoveryAttemptLimit?: number
  recoveryRerunsDenoise?: boolean
  recoveryReason?: import('../api/client').QueueRecoveryReason | null
  recoveryReasonText?: string | null
  recoveryActionable?: boolean
  recoveryActions?: import('../api/client').QueueRecoveryAction[]
  /** Frozen estimate shown only as work expected after a safe resume. */
  estimateAfterResume?: H3PerformanceEstimate | null
  logEvents?: import('../api/client').JobLogEvent[]
}

export interface H3SegmentBoundary {
  type: 'continuous' | 'precut' | 'cut' | 'transition'
  at_seconds: number
  source: string
  event?: string
}

export interface H3SegmentPlanItem {
  index: number
  /** Compatibility aliases: both retain the generated geometry. */
  frames: number
  duration_seconds: number
  generated_frames: number
  published_frames: number
  generated_duration_seconds: number
  published_duration_seconds: number
  model_type: 'minimax_h3' | 'minimax_h3_pinkcherry_fl2va' | 'minimax_h3_w4a8_fl2va' | 'minimax_h3_ref2va'
  model_reason: string
  edge_anchor_locked: boolean
  switch_from_previous: boolean
  boundary_from_previous: H3SegmentBoundary | null
  prompt_preview?: string
}

export interface H3DurationSnapCandidate {
  requested_published_frames: number
  candidate_published_frames: number | null
  segment_count: number | null
  generated_frames: number[]
  segment_published_frames: number[]
  confidence: 'high' | 'low' | 'unavailable'
  applied: boolean
  reason: string
}

export interface H3DurationPlanSegment {
  index: number
  published_frames: number
  min_published_frames: number
  max_published_frames: number
  grid_step: number
  grid_offset: number
  authored_locked: boolean
  completed_locked: boolean
  lock_reason: string | null
}

export interface H3DurationPlan {
  revision: string
  target_published_frames: number
  current_published_frames: number
  current_generated_frames: number
  fps: number
  snap_candidates: Record<'nearest' | 'down', H3DurationSnapCandidate>
  segments: H3DurationPlanSegment[]
  redistribution_mode: 'none' | 'next' | 'future'
  outcome: 'exact' | 'acceptable' | 'insufficient_capacity'
  reason: string
  /** Server-authored target minus current frames. */
  residual_published_frames: number
}

export interface H3SegmentPlan {
  kind: 'h3_segments'
  clip_count: number
  fps: number
  requested_frames: number
  planned_frames: number
  published_frames: number
  adaptive_conditioning: boolean
  checkpoint_switches: number
  segments: H3SegmentPlanItem[]
  duration_plan?: H3DurationPlan
}

export interface H3GenerationRequirements {
  models: Array<{
    model_type: H3SegmentPlanItem['model_type']
    is_downloaded: boolean
    terms_required: boolean
    auto_download: boolean
  }>
  ref2va_terms_required: boolean
  all_downloaded: boolean
}

export interface H3PlanDecision {
  segmentOverrides: NonNullable<GenerateParams['h3_segment_overrides']>
  boundaryOverrides: NonNullable<GenerateParams['h3_boundary_overrides']>
  planRevision?: string
  durationSnapMode?: 'manual' | 'nearest' | 'down'
  segmentDurationEdits?: Array<{ segmentIndex: number; publishedFrames: number }>
  durationRedistribution?: 'none' | 'next' | 'future'
}

export interface OutputFile {
  name: string
  url: string
  type: 'video' | 'image' | 'audio'
  mode: GenerationMode | null
  /** Edit sub-mode tag from the .meta.json sidecar params (set by the
   *  retake/inpaint/outpaint/restyle/edit_anything endpoints). The gallery's
   *  Edits filter checks this to identify edit-mode outputs regardless of
   *  the parent `mode`, since e.g. outpaint endpoints write mode='video'. */
  edit_sub_mode?: EditSubMode | null
  artifact_class: ArtifactClass
  linked_component_count: number
  favorite: boolean
  size: number
  created_at: number
  /** Backend identity token covering both media bytes and sidecar metadata. */
  revision: string
  workspace: string
  private: boolean
  explicit: boolean
}

export interface OutputSearchFilters {
  model?: string
  lora?: string
  seed?: string
  reference?: '' | 'with' | 'without'
  after?: string
  before?: string
}

/**
 * Gallery media kind plus legacy saved-view aliases. The first four values
 * are the composable media facet; the remaining values retain the existing
 * Edits, Multi-clip, and Favorites views.
 */
export type MediaFilter = 'all' | 'images' | 'videos' | 'audio' | 'avatars' | 'multiclip' | 'favorites'
export type ArtifactClass = 'final' | 'component' | 'window' | 'temporary'
export type OutputArtifactScope = 'final' | 'all' | 'components' | 'component' | 'window' | 'temporary'
export type AspectRatio = 'auto' | '16:9' | '9:16' | '1:1' | '4:3' | '3:4'
export type ResolutionPreset = 'auto' | '480p' | '540p' | '720p' | '768p' | '1080p'
export type ScailResolutionProfile = '480p' | '512p' | '704p'
/** Backward-compatible name for saved Recast/API callers. */
export type RecastResolutionProfile = ScailResolutionProfile
export type GenerationMode = 'image' | 'video' | 'audio' | 'avatar' | 'tools'
export type EditSubMode = 'retake' | 'inpaint' | 'restyle' | 'outpaint' | 'edit_anything' | 'recast'
export type AudioSubMode = 'speech' | 'music' | 'sfx' | 'mixer'

export interface RecastReferenceAsset {
  file: File | null
  path: string
  url: string
}

export interface RecastCharacterMapping {
  id: string
  target: string
  refFile: File | null
  refPath: string
  refUrl: string
  additionalRefs: RecastReferenceAsset[]
  referenceAlignedToSource: boolean
}

/** Optional SCAIL-2 Repaint correspondence. The source phrase is tracked
 * through the control video and the target phrase is segmented in the edited
 * first frame; both receive the same stable semantic color. */
export interface RepaintRegionMapping {
  id: string
  source: string
  target: string
}

export interface ChoiceConfig {
  selection?: string[]
  choices?: [string, string][]
  labels?: Record<string, string>
  default?: string
  label?: string
  show_label?: boolean
  letters_filter?: string
}

export interface ModelOptions {
  model_type: string
  architecture: string
  guidance_max_phases: number
  lock_guidance_phases: boolean
  sliding_window: boolean
  motion_amplitude: boolean
  flow_shift: boolean
  tea_cache: boolean
  returns_audio: boolean
  any_audio_prompt: boolean
  audio_scale_name: string
  lock_inference_steps: boolean
  lock_guidance_scale: boolean
  no_negative_prompt: boolean
  i2v_class: boolean
  t2v_class: boolean
  image_outputs: boolean
  supports_end_frame: boolean
  guide_preprocessing: ChoiceConfig | null
  guide_custom_choices: ChoiceConfig | null
  image_ref_choices: ChoiceConfig | null
  audio_prompt_type_sources: ChoiceConfig | null
  background_removal_label: string | null
  max_image_refs?: number | null
  minimax_h3_reference_mode?: boolean
  minimax_h3_conditioning_mode?: 'semantic_references' | 'first_last_frames'
  minimax_h3_conditioning_modes_mutually_exclusive?: boolean
  reference_image_max_count?: number
  reference_video_max_count?: number
  reference_audio_max_count?: number
  mixed_reference_max_count?: number
  semantic_reference_limits?: {
    image_count: number
    video_count: number
    audio_count: number
    mixed_file_count: number
    output_duration_seconds: { min: number; max: number }
    reference_video_duration_seconds: { min: number; max: number; total_max: number }
    reference_audio_duration_seconds: { min: number; max: number; total_max: number }
  }
  sample_solvers: [string, string][] | null
  self_refiner: boolean
  self_refiner_max_plans: number
  sliding_window_defaults: Record<string, number> | null
  // LTX-2 Dev pipeline capabilities (guidance controls in Advanced Settings)
  perturbation?: boolean
  reference_pipeline?: boolean
  cfg_star?: boolean
  adaptive_projected_guidance?: boolean
  audio_guidance?: boolean
  prompt_enhancer_model?: string | null
  fps: number
  frames_minimum: number
  frames_steps: number
  latent_size: number
  frames_maximum?: number | null
  frame_alignment_modulus?: number
  frame_alignment_remainder?: number
  frame_alignment_mode?: 'floor' | 'ceil' | 'nearest'
  default_num_inference_steps: number | null
  default_guidance_scale: number | null
  default_video_length?: number | null
  default_sliding_window_size?: number | null
  hide_resolution_presets: boolean
  resolution_presets?: Partial<Record<ResolutionPreset, {
    label: string
    experimental?: boolean
    hint?: string
    values: Partial<Record<AspectRatio, string>>
  }>> | null
  resolution_preset_order?: ResolutionPreset[] | null
  supports_auto_aspect?: boolean
  /** Model-native legal canvases. H3 uses these exact values rather than
   *  generic 480p/720p aspect-ratio approximations. */
  resolutions?: Resolution[]
  input_video_strength_label: string
  vae_upsampler_modes: number[]
  // TTS-specific
  audio_only: boolean
  duration_slider: { label: string; min: number; max: number; increment: number; default: number } | null
  pause_between_sentences: boolean
  temperature_enabled: boolean
  custom_settings_def: { id: string; label: string; name: string; type: string }[] | null
}

export interface SystemConfig {
  // Optional Maestro-base compatibility version from older/current backends.
  // Product identity comes from the UI-bundled Continuum branding constants.
  app_version?: string
  attention_mode: string
  transformer_quantization: string
  vae_config: number
  compile: string
  video_profile: number
  image_profile: number
  audio_profile: number
  video_output_codec: string
  image_output_codec: string
  enhancer_enabled: number
  prompt_enhancer_quantization: string
  attention_modes_available: string[]
  vram_safety_coefficient: number
  // Linked model folders (absolute paths outside the Maestro install,
  // e.g. an existing Wan2GP install's ckpts). Searched read-only for
  // already-downloaded checkpoints; new downloads always go to Maestro's
  // own ckpts folder.
  model_folders: string[]
}

export interface ModelFolderCandidate {
  app: string
  path: string
  files: number
  folders: number
  size_gb: number
  linked: boolean
}

export interface OutputMetadata {
  source: 'sidecar' | 'embedded' | 'none'
  params: Record<string, unknown> | null
  private?: boolean
  explicit?: boolean
  upload_filenames?: Record<string, string | string[]>
  job_id?: string
  generation_time?: number
  created_at?: number
}

export interface MultiClip {
  prompt: string
  startImage: File | null
  startImagePath: string | null
  endImage: File | null
  endImagePath: string | null
  durationFrames?: number
}

export type SettingsTab = 'performance' | 'integrations'

export type HostTermId =
  | 'lawful_use'
  | 'minimax_h3_ref2va'
  | 'bfl_flux1_self_review'
  | 'bfl_flux2_self_review'
  | 'krea2_self_review'
  | 'civitai_2731187_3209007_creator_terms'
  | 'civitai_2764429_3211049_creator_terms'
  | 'ponpoke_flux2_klein_4b_self_review'
  | 'ponpoke_flux2_klein_9b_self_review'
  | 'civitai_2382648_2973304_creator_terms'

export interface HostTermBinding {
  license_id: string
  repository: string
  revision: string
  license_repository?: string
  license_revision?: string
  covered_repositories?: Array<{
    repository: string
    revision: string
  }>
  source_url?: string
  creator?: string
  model_id?: number
  model_version_id?: number
  file_id?: number
  filename?: string
  file_size_bytes?: number
  file_sha256?: string
  recipe_graph?: Record<string, unknown>
  creator_restrictions?: {
    allowNoCredit: boolean
    allowDerivatives: boolean
    allowCommercialUse: string[]
  }
  underlying_base_license?: string
}

export interface HostTermStatus {
  current_version: number
  accepted_version: number | null
  accepted_at: string | null
  accepted: boolean
  binding?: HostTermBinding
}

export interface HostTermsStatus {
  lawful_use: HostTermStatus
  minimax_h3_ref2va: HostTermStatus
  bfl_flux1_self_review: HostTermStatus
  bfl_flux2_self_review: HostTermStatus
  krea2_self_review: HostTermStatus
  civitai_2731187_3209007_creator_terms: HostTermStatus
  civitai_2764429_3211049_creator_terms: HostTermStatus
  ponpoke_flux2_klein_4b_self_review: HostTermStatus
  ponpoke_flux2_klein_9b_self_review: HostTermStatus
  civitai_2382648_2973304_creator_terms: HostTermStatus
}

export interface ServicesConfig {
  llm_model_id: string
  llm_device: string
  llm_provider: string
  llm_remote_url: string
  enhance_llm_model_id: string
  enhance_llm_device: string
  google_api_key: string
  google_api_key_set: boolean
  openai_api_key: string
  openai_api_key_set: boolean
  anthropic_api_key: string
  anthropic_api_key_set: boolean
  use_director_v2: boolean
  nsfw_mode: boolean
  director_prompt_polish: 'off' | 'full_guide' | 'light_guide' | 'third_pass'
  civitai_api_key: string
  civitai_api_key_set: boolean
  voice_reference_enabled: boolean
  ltx_progressive_pipeline: boolean
  /** Master gate for experimental / power-user features. When false
   *  (default), the Services panel hides Director v2 engine, Voice
   *  Reference, external API keys (Google/OpenAI/Anthropic), and the
   *  Studio prompt enhancer config; the Edit mode picker hides
   *  Inpaint. Flipping this on surfaces all of them. */
  show_experimental: boolean
  /** Storage Manager opt-in: allow removing duplicate files FROM linked
   *  installs (Recycle Bin only). Default off — informed consent. */
  storage_allow_linked_removal?: boolean
  /** Performance auto-tune master switch. When true (default for fresh
   *  installs), Settings → System Performance shows a single auto card
   *  with detected hardware + recommended profile, and the underlying
   *  knobs collapse under "Show advanced settings". When false (set
   *  automatically on migration for pre-existing installs), the
   *  advanced fields are visible by default and the user is in
   *  manual mode. Editing any field while auto is on flips this off. */
  auto_performance: boolean
  /** Multi-shot LoRA mode. When true, Pass 2 emits storyboard-format
   *  video_prompts for 20s shots, letting an IC-LoRA (e.g. Maque AI
   *  LTX-2.3 IC-LoRA) cut between camera angles inside a single
   *  generation. Short reaction shots (≤15s) and long sustained
   *  shots (≥40s) keep the regular single-camera flowing format.
   *  User must also have the matching LoRA in their video_loras
   *  selection for the cuts to actually render. */
  director_multishot_lora_mode: boolean
  /** FlashVSR (DiT super-resolution) spatial-upsampling settings.
   *  flashvsr_mode: 1=tiny, 2=full, 3=tiny-long. topk_ratio 0..4 (sparse-attn
   *  density). backend: 'auto' | 'triton_sparse' | 'sparge'. */
  flashvsr_mode: number
  flashvsr_topk_ratio: number
  flashvsr_backend: string
}

// Performance Auto-Tune (Settings → System Performance card) — backed
// by GET /api/v1/system-detect and POST /api/v1/system-detect/apply.
// The card shows the user's detected hardware + the recommended
// profile in plain English; the apply endpoint writes the
// recommendation into wgp_config.json.

/** Hardware detection result from /api/v1/system-detect. Mirrors the
 *  schema documented in app/services/hardware_detect.py — keep in sync
 *  if you add new probe fields there. */
export interface HardwareInfo {
  cuda_available: boolean
  gpu_name: string
  gpu_vram_gb: number
  gpu_capability: string  // e.g. "sm89", "sm120", or "" if no CUDA
  ram_gb: number
  cpu_count: number
  ram_tier: 'high' | 'low' | 'very_low'
  vram_tier: 'high' | 'low' | 'tight' | 'none'
  supports_fp8: boolean
  supports_nvfp4: boolean
  supports_sage: boolean
  supports_sage2: boolean
  supports_flash: boolean
  supports_triton: boolean
}

/** Recommended settings the auto-tune engine produced for the detected
 *  hardware. Underscore-prefixed fields are display-only metadata —
 *  the rest are config keys that get written to wgp_config.json. */
export interface RecommendedSettings {
  video_profile: number
  image_profile: number
  audio_profile: number
  transformer_quantization: 'int8' | 'fp8' | 'bf16'
  vae_config: number
  vram_safety_coefficient: number
  attention_mode: string
  compile: string
  /** Friendly label for the auto card, e.g. "Profile 1 — Optimized for fastest generation" */
  _recommendation_label: string
  /** Verbose reason string for tooltips and debug logs */
  _recommendation_reason: string
}

/** Response shape from GET /api/v1/system-detect. */
export interface SystemDetectResponse {
  hardware: HardwareInfo
  recommended: RecommendedSettings
  auto_enabled: boolean
}

/** Response shape from POST /api/v1/system-detect/apply. */
export interface SystemDetectApplyResponse {
  status: string
  hardware: HardwareInfo
  applied: Record<string, unknown>
  label: string
  reason: string
  /** True when one of the *_profile keys changed — UI should show
   *  "changes take effect on next model load" toast. */
  profile_changed: boolean
}

// CivitAI Browser types
export interface CivitAIModel {
  id: number
  name: string
  description?: string
  type: string
  nsfw: boolean
  tags: string[]
  creator: { username: string; image: string | null }
  stats: { downloadCount: number; favoriteCount: number; thumbsUpCount: number; rating: number; ratingCount: number }
  modelVersions: CivitAIModelVersion[]
}

export interface CivitAIModelVersion {
  id: number
  name: string
  baseModel: string
  trainedWords: string[]
  files: CivitAIFile[]
  images: CivitAIImage[]
  description?: string
  localArch?: string | null
  /** Version release date from CivitAI — persisted into the download
   *  sidecar so My LoRAs can sort by newest release. */
  publishedAt?: string
}

export interface CivitAIFile {
  id: number
  name: string
  sizeKB: number
  type: string
  downloadUrl: string
  metadata: { format?: string; size?: string; fp?: string }
}

export interface CivitAIImage {
  url: string
  type: string
  width: number
  height: number
  nsfwLevel: number
  meta?: { prompt?: string; negativePrompt?: string; steps?: number; cfgScale?: number; sampler?: string }
}

export interface CivitAISearchResult {
  items: CivitAIModel[]
  metadata: { nextCursor?: string; totalItems?: number }
}

export interface CivitAIDownload {
  id: string
  filename: string
  status: 'downloading' | 'completed' | 'failed'
  progress: number
  bytes_downloaded: number
  bytes_total: number
  error: string | null
  /** Unix timestamps (seconds) supplied by the download registry. */
  started_at: number | null
  completed_at: number | null
  /** Present after a downloaded checkpoint is registered as a model. */
  model_type?: string | null
  // Non-fatal warnings raised after the download finished — most
  // commonly the architecture-mismatch warning when a Klein-4B-trained
  // LoRA lands in flux2_klein_9b/ or vice versa. UI shows these inline
  // on the download row.
  warnings?: string[]
}

export interface LoraWeightPhase {
  phase: number
  default: number
  min: number
  max: number
  label: string
}

export interface LoraRecommendedWeights {
  source?: 'civitai' | 'default'
  default: number
  min: number
  max: number
  phases?: LoraWeightPhase[]
}

export interface LoraInfo {
  filename: string
  trained_words: string[]
  preview_url: string | null
  civitai_model_id: number | null
  recommended_weights: LoraRecommendedWeights | null
  has_guide: boolean
  guide?: string | null
  /** Upstream CivitAI catalog metadata; not used to gate Maestro behavior. */
  nsfw?: boolean
  /** ISO timestamp of when the file was downloaded — sidecar `downloadedAt`
   *  when present, else the weight file's mtime. Shown as an age chip in
   *  the Studio/Director LoRA pickers. */
  downloaded_at?: string | null
  /** ISO timestamp of the CivitAI version's publish date (sidecar
   *  `publishedAt`). Null for HF/hand-installed LoRAs without sidecar data. */
  released_at?: string | null
  /** Stable identifier that survives version updates.
   *  Format: `civitai:{modelId}` when sidecar has a CivitAI modelId,
   *  otherwise `local:{filename}`. Use this as the persistence key for
   *  activations, weights, and other LoRA-keyed state instead of the
   *  filename, so updating a LoRA from v1.2 → v1.5 carries settings forward. */
  lora_id: string
  /** Optional public input contract. Private trigger/template expansion stays server-side. */
  parameter_schema?: LoraParameterSchema
  /** Update status from the cached CivitAI manifest. Populated by
   *  /api/v1/loras/check-updates and surfaced through this endpoint
   *  without an extra round-trip. The UI uses this to render badges. */
  update_status?: LoraUpdateStatus
  latest_version_id?: number | null
  current_version_id?: number | null
  latest_published_at?: string | null
  latest_changelog?: string | null
}

/** Per-LoRA update state surfaced from the cached manifest.
 *  - `current`:   sidecar version matches CivitAI's latest
 *  - `available`: a newer version exists on CivitAI
 *  - `unknown`:   not yet checked, no sidecar, or transient API failure
 *  - `local`:     no CivitAI sidecar at all (hand-installed / personal LoRA)
 *  - `removed`:   CivitAI returned 404 (creator unpublished or deleted) */
export type LoraUpdateStatus = 'current' | 'available' | 'unknown' | 'local' | 'removed'

export interface LlmStatus {
  loaded: boolean
  model_id: string | null
  device: string | null
  requested_device?: string | null
  provider: string
  vision_available?: boolean
  backend?: string | null
  loading?: boolean
  loading_model_id?: string | null
  loading_phase?: string | null
  download?: {
    model_id?: string
    filename?: string
    phase?: string | null
    downloaded_bytes?: number
    total_bytes?: number | null
    seconds_since_progress?: number | null
  } | null
  runtime?: {
    backend?: string | null
    build?: number | null
    devices?: string[]
    effective_profile?: Record<string, unknown>
    timings?: Record<string, number>
    speed?: LlmSpeedEstimate
    fallback_reason?: string | null
  }
}

export interface LlmSpeedEstimate {
  prompt_tokens_per_second: number | null
  generation_tokens_per_second: number | null
  source: 'measured' | 'calibrated' | 'heuristic' | 'unavailable'
  confidence: 'measured' | 'high' | 'medium' | 'low' | 'unavailable'
  reason: string
  sample_count: number
  backend: string
}

/** Live hardware telemetry for the sidebar status indicators.
 *  Backs HardwareStatusBar; polled ~5s via GET /api/v1/system-stats. */
export interface SystemStats {
  cpu: { percent: number }
  ram: { percent: number; used_gb: number; total_gb: number }
  gpu: {
    available: boolean
    /** Headline GPU utilization. On Windows this is the 3D-engine perf
     *  counter (matches Task Manager); elsewhere the NVML/nvidia-smi value. */
    percent: number
    /** NVML / nvidia-smi compute utilization, kept for the tooltip. */
    compute_percent?: number
    vram_used_gb: number
    vram_total_gb: number
    vram_percent: number
  }
  /** Generation model currently resident in VRAM (WGP/mmgp). `loaded`
   *  distinguishes "actually in memory now" from "last/selected type". */
  model: { name: string | null; model_type: string | null; loaded: boolean }
}

export interface LlmModelOption {
  id: string
  label: string
  size_hint: string
  source?: string
  provider?: string
  installed?: boolean
  downloaded?: boolean
  current?: boolean
  configured?: boolean
  loaded?: boolean
  loading?: boolean
  loading_phase?: string | null
  backend?: string
  effective_device?: string | null
  vision_capable?: boolean
  vision_available?: boolean | null
  projector_available?: boolean
  native_vision?: boolean
  speed?: LlmSpeedEstimate
  download?: {
    phase?: string | null
    downloaded_bytes?: number
    total_bytes?: number | null
    seconds_since_progress?: number | null
  } | null
  runtime_profile?: {
    backend?: string
    device?: string
    gpu_layers?: number | string
    threads?: number
    threads_batch?: number
    context_size?: number
    batch_size?: number
    ubatch_size?: number
    flash_attention?: boolean | string
    cache_type_k?: string
    cache_type_v?: string
    slots?: number
    prompt_cache?: boolean
    projector_offload?: boolean
  }
  description?: string
}

export interface LlmPromptGuideOption {
  id: string
  label: string
  description?: string
  target_mode?: 'video'
  target_model_prefixes?: string[]
}

export interface LlmChatMessage {
  role: 'user' | 'assistant'
  content: string
  attachments?: Array<{
    kind: 'image'
    name: string
  }>
  performance?: {
    average_tps: number | null
    generated_tokens_approx?: number | null
    elapsed_seconds?: number | null
  }
}

export interface AudioBeat {
  time: number
  strength: number
}

export interface AudioSection {
  start: number
  end: number
  label: string
  energy: number
}

export interface LyricSegment {
  start: number
  end: number
  text: string
  speaker?: string | null
}

export interface SongStructureEntry {
  label: string
  display_label: string
  start: number
}

export interface AudioAnalysisResult {
  duration: number
  sample_rate: number
  bpm: number
  beats: AudioBeat[]
  downbeats: number[]
  sections: AudioSection[]
  onset_envelope: number[]
  lyrics: LyricSegment[] | null
  vocals_path: string | null
  song_structure?: SongStructureEntry[] | null
}

export interface SuggestedClip {
  start: number
  end: number
  section_label: string
  energy: number
  suggested_prompt_hint: string
}

export interface PlannedClip extends SuggestedClip {
  beat_count: number
  duration_frames: number
  dominant_speaker?: string | null
}

export interface SpeakerMapping {
  speakerId: string
  name: string
  role: 'rapping' | 'singing' | 'speaking' | ''
}

export interface ClipPlan {
  video_prompt: string
  image_prompt: string
}

/** Partial plan returned from single-phase LLM calls */
export interface PartialClipPlan {
  video_prompt?: string
  image_prompt?: string
}

export interface DirectorClipImage {
  clipIndex: number
  prompt: string
  file: File
  filename: string
}

export interface DirectorImageGenProgress {
  current: number
  total: number
  currentClipLabel: string
  status: 'generating' | 'polling' | 'downloading' | 'done' | 'error'
}

export type DirectorSkill = 'music_video' | 'short_film' | 'podcast' | 'viral_video'
export type ShortFilmPath = 'audio' | 'story'

export interface ShortFilmCharacter {
  name: string
  description: string
}

export interface ShortFilmScene {
  scene_number: number
  title: string
  start: number
  end: number
  duration_frames: number
  characters: string[]
  dialogue: string[]
  action: string
  mood: string
}

// ── Director v2 Schema Types ──────────────────────────────────────────

export interface DirectorFlags {
  use_shared_shot_schema?: boolean
  use_mode_specific_renderers?: boolean
  use_prompt_validation?: boolean
  use_prompt_compression?: boolean
  use_llm_refinement?: boolean
  aggressive_compression?: boolean
  log_validation_details?: boolean
  log_compression_deltas?: boolean
}

export interface SubjectRef {
  visual_description: string
  character_id?: string
  position_or_relation?: string
}

export interface DialogueBeat {
  spoken_text: string
  speaker_id?: string
  delivery?: string
  physical_cue?: string
  priority?: 'low' | 'medium' | 'high'
}

export interface CameraPlan {
  framing: string
  angle?: string
  movement?: string
  movement_intensity?: 'static' | 'subtle' | 'moderate' | 'dynamic'
  lens_feel?: string
  reframing_notes?: string
}

export interface AudioPlan {
  mode: 'generated_audio' | 'audio_driven' | 'dialogue_driven' | 'music_driven' | 'ambient_only'
  ambience?: string
  effects?: string[]
  vocal_style?: string
  timing_anchor?: 'audio' | 'video' | 'balanced'
  lip_sync_critical?: boolean
}

export interface ShotPlan {
  shot_id: string
  index: number
  duration_sec: number
  skill_type: DirectorSkill
  scene_goal: string
  narrative_role?: string
  scene_type?: string
  source_mode_preference?: 't2v' | 'i2v' | 'a2v' | 'retake' | 'extend'
  image_strategy?: 'reference_edit' | 'reference_inspired' | 'fresh_generation' | 'none'
  continuity_strategy?: 'independent' | 'continuous' | 'extend_previous'
  subjects_on_screen: SubjectRef[]
  spatial_setup: string
  environment: string
  visual_style: string
  lighting: string
  mood: string
  action_beats: string[]
  performance_beats?: string[]
  dialogue_beats?: DialogueBeat[]
  camera_plan: CameraPlan
  audio_plan: AudioPlan
  ending_beat: string
  constraints?: string[]
  continuity_refs?: string[]
  metadata?: Record<string, unknown>
}

export interface CharacterProfile {
  id: string
  physical_description: string
  display_name?: string
  wardrobe?: string
  voice_description?: string
}

export interface ProductionPlan {
  skill_type: DirectorSkill
  shots: ShotPlan[]
  title?: string
  global_style?: string
  total_duration_sec?: number
  characters?: CharacterProfile[]
  continuity_notes?: string[]
}

export interface DirectorV2PlanResponse {
  clip_plans: Array<{ video_prompt: string; image_prompt: string }>
  production_plan: ProductionPlan
  skill_type: DirectorSkill
}

// ── Director Pipeline Dashboard ──────────────────────────────────────────

export interface PipelineClipState {
  index: number
  planned_clip: PlannedClip | null
  image_prompt: string
  video_prompt: string
  keyframe_prompts: string[]
  window_prompts: string[]
  window_count: number
  image_prompt_pre_polish: string | null
  video_prompt_pre_polish: string | null
  window_prompts_pre_polish: string[] | null
  keyframe_prompts_pre_polish: string[] | null
  start_image_filename: string | null
  keyframe_filenames: string[]
  video_filename: string | null
  video_stale?: boolean
  tag: 'good' | 'needs_work' | null
  image_gen_time_sec: number | null
  video_gen_time_sec: number | null
}

export type PipelineRepairStatus =
  | 'queued'
  | 'running'
  | 'cancelling'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export interface PipelineRepairState {
  operation_id: string
  status: PipelineRepairStatus
  phase: 'queued' | 'images' | 'videos' | 'rejoin' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  current: number
  total: number
  clip_index: number | null
  message: string
  error: string | null
  error_code?: string | null
  failure_details?: Record<string, unknown> | null
  cancel_requested?: boolean
  started_at: number
  updated_at: number
  completed_at: number | null
  result_filename: string | null
}

export type DirectorRecoveryState =
  | 'blocked_input_changed'
  | 'blocked_remote_reauth'
  | 'interrupted'
  | 'paused'
  | 'retrying'
  | 'terminal'

export type DirectorRecoveryReason =
  | 'input_missing_or_changed'
  | 'owner_reauthentication_required'

export type DirectorRecoveryAction = 'resume' | 'continue'

export interface DirectorRecoveryMetadata {
  phase?: string
  recovery_state?: DirectorRecoveryState | null
  recovery_blocked?: boolean
  recovery_reason?: DirectorRecoveryReason | null
  recovery_reason_text?: string | null
  recovery_actions?: DirectorRecoveryAction[]
}

export interface SavedPipelineState extends DirectorRecoveryMetadata {
  version: number
  pipeline_id: string
  created_at: number
  completed_at: number | null
  status: string
  pipeline_type: string
  scene_description: string
  workspace: string
  reference_image_path: string | null
  auto_mode: boolean
  seamless: boolean
  /** Present only on legacy combined-image checkpoints. */
  image_model?: string
  /** New role checkpoints preserve the automatic null sentinel as submitted. */
  image_creator_model?: string | null
  image_editor_model?: string | null
  image_creator_loras?: DirectorImageRoleLoraSelection[]
  image_editor_loras?: DirectorImageRoleLoraSelection[]
  video_model: string
  /** Effective saved behavior. Missing on legacy projects, which require images. */
  shot_image_policy?: DirectorShotImagePolicy
  shot_image_guidance?: DirectorShotImageGuidance
  /** Always null in public state; retained only to ignore legacy files safely. */
  llm_log: null
  /** Content-free aggregate available on checkpoints created by newer hosts. */
  llm_planning_time_sec?: number | null
  clips: PipelineClipState[]
  output_files: string[]
  total_time_sec: number | null
  repair?: PipelineRepairState | null
}

export interface PipelineListItem extends DirectorRecoveryMetadata {
  id: string
  status: string
  pipeline_type: string
  created_at: number
  clip_count: number
  output_count: number
  scene_description: string
  workspace: string
  repair_status?: PipelineRepairStatus | null
}
