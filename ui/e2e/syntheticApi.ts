import { expect, type Page, type Route } from '@playwright/test'

const E2E_PORT = process.env.MAESTRO_E2E_PORT
const E2E_TOKEN = process.env.MAESTRO_E2E_RUN_TOKEN
const portPattern = /^(?:[1-9]|[1-9]\d{1,3}|[1-5]\d{4}|6[0-4]\d{3}|65[0-4]\d{2}|655[0-2]\d|6553[0-5])$/
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
if (!E2E_PORT || !portPattern.test(E2E_PORT) || !E2E_TOKEN || !uuidPattern.test(E2E_TOKEN)) {
  throw new Error('Synthetic browser tests require a canonical positive port and UUID-v4 run token')
}
const APP_ORIGIN = `http://127.0.0.1:${E2E_PORT}`
const viteCachePath = process.env.MAESTRO_VITE_CACHE_DIR?.replaceAll('\\', '/').replace(/^\/+/, '')
const VITE_CACHE_PREFIX = viteCachePath ? `/@fs/${viteCachePath}/deps/` : null
const STATIC_PATHS = new Set([
  '/',
  '/index.html',
  '/favicon.svg',
  '/maestro.svg',
  '/@vite/client',
  '/@react-refresh',
  '/node_modules/vite/dist/client/env.mjs',
])
const STATIC_PREFIXES = [
  '/src/',
  '/node_modules/.vite/deps/',
]

function isAllowedStaticRequest(method: string, pathname: string): boolean {
  return (method === 'GET' || method === 'HEAD') && (
    STATIC_PATHS.has(pathname)
    || STATIC_PREFIXES.some(prefix => pathname.startsWith(prefix))
    || (VITE_CACHE_PREFIX !== null && pathname.startsWith(VITE_CACHE_PREFIX))
  )
}

const EMPTY_QUEUE = {
  paused: false,
  pause_after_current: false,
  summary: {
    running: 0,
    waiting: 1,
    held: 0,
    registering: 0,
    preparing: 0,
    approval_waiting: 0,
    active_total: 1,
  },
  jobs: [{
    job_id: 'synthetic-queue-item',
    status: 'queued',
    priority: 0,
    position: 1,
    held: false,
    hold_after_output: false,
    wait_reason: 'waiting_for_turn',
    queue_reorder_reason: 'queue_order',
    requested_outputs: 1,
    produced_outputs: 0,
    eta_seconds: 12,
    subtask_eta_seconds: null,
  }],
}

const MODEL_OPTIONS = {
  model_type: 'minimax_h3',
  architecture: 'minimax_h3',
  guidance_max_phases: 1,
  lock_guidance_phases: false,
  sliding_window: false,
  motion_amplitude: false,
  flow_shift: false,
  tea_cache: false,
  returns_audio: false,
  any_audio_prompt: false,
  audio_scale_name: 'Audio scale',
  lock_inference_steps: false,
  lock_guidance_scale: false,
  no_negative_prompt: false,
  i2v_class: true,
  t2v_class: true,
  image_outputs: true,
  supports_end_frame: true,
  guide_preprocessing: null,
  guide_custom_choices: null,
  image_ref_choices: null,
  audio_prompt_type_sources: null,
  background_removal_label: null,
  sample_solvers: null,
  self_refiner: false,
  self_refiner_max_plans: 0,
  sliding_window_defaults: null,
  fps: 24,
  frames_minimum: 25,
  frames_steps: 24,
  latent_size: 24,
  frames_maximum: 241,
  frame_alignment_modulus: 24,
  frame_alignment_remainder: 1,
  frame_alignment_mode: 'nearest',
  default_num_inference_steps: 30,
  default_guidance_scale: 5,
  default_video_length: 121,
  hide_resolution_presets: false,
  resolutions: [{ label: '768×432', value: '768x432' }],
  input_video_strength_label: 'Input strength',
  vae_upsampler_modes: [],
  audio_only: false,
  duration_slider: null,
  pause_between_sentences: false,
  temperature_enabled: false,
  custom_settings_def: null,
}

const json = (route: Route, body: unknown, status = 200) => route.fulfill({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

export interface SyntheticApiController {
  setQueueFailure(failing: boolean): void
  setQueueHeld(held: boolean): void
  setQueueDelay(delayMs: number): void
  assertClean(): Promise<void>
  takeUnexpected(): string[]
}

export async function installSyntheticApi(page: Page): Promise<SyntheticApiController> {
  const unexpected: string[] = []
  let queueFailure = false
  let queueHeld = false
  let queueDelayMs = 0
  const context = page.context()

  await context.routeWebSocket(/.*/, async socket => {
    const url = new URL(socket.url())
    if (
      url.protocol === 'ws:'
      && url.hostname === '127.0.0.1'
      && url.port === E2E_PORT
      && url.pathname === '/'
    ) {
      // Satisfy Vite's injected HMR client without connecting the browser-side
      // socket to the server. Direct server upgrades are rejected separately.
      return
    }
    unexpected.push(`websocket ${url.origin}${url.pathname}`)
    await socket.close({ code: 1008, reason: 'Synthetic harness blocks unknown WebSockets' })
  })

  await context.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())

    if (url.origin !== APP_ORIGIN) {
      unexpected.push(`external ${request.method()} ${url.origin}${url.pathname}`)
      await route.abort('blockedbyclient')
      return
    }

    if (isAllowedStaticRequest(request.method(), url.pathname)) {
      await route.continue()
      return
    }

    if (!url.pathname.startsWith('/api/')) {
      unexpected.push(`unknown ${request.method()} ${url.pathname}`)
      await route.abort('blockedbyclient')
      return
    }

    const key = `${request.method()} ${url.pathname}`
    switch (key) {
      case 'GET /api/v1/access-context':
        await json(route, {
          remote: false,
          project_password_required: false,
          project_names_visible: true,
          machine_controls: false,
          custom_model_sources: false,
          catalog_model_downloads: false,
          classic_ui: false,
          cloudflare_enabled: false,
          share_url: '',
          share_flow: 'disabled',
        })
        return
      case 'GET /api/v1/workspaces':
        await json(route, {
          workspaces: [{ name: 'Synthetic project', password_protected: false, unlocked: true }],
          active: 'Synthetic project',
        })
        return
      case 'GET /api/v1/models':
        await json(route, {
          families: [{ id: 'h3', label: 'H3', order: 1 }],
          models: [{
            model_type: 'minimax_h3',
            name: 'Synthetic H3',
            family: 'h3',
            architecture: 'minimax_h3',
            is_i2v: true,
            is_t2v: true,
            guidance_max_phases: 1,
            fps: 24,
            supports_end_frame: true,
            is_downloaded: true,
            downloadable: false,
            supported_operations: ['video', 'image'],
          }],
        })
        return
      case 'GET /api/v1/model-visibility':
        await json(route, { configured: true, enabled_models: ['minimax_h3'], defaults_version: 1 })
        return
      case 'PUT /api/v1/model-visibility':
        await json(route, { configured: true, enabled_models: ['minimax_h3'], defaults_version: 1 })
        return
      case 'GET /api/v1/model-options/minimax_h3':
        await json(route, MODEL_OPTIONS)
        return
      case 'GET /api/v1/defaults/minimax_h3':
        await json(route, {
          num_inference_steps: 30,
          guidance_scale: 5,
          video_length: 121,
          resolution: '768x432',
        })
        return
      case 'GET /api/v1/loras/minimax_h3':
        await json(route, { loras: [], guidance_max_phases: 1 })
        return
      case 'GET /api/v1/loras/installed':
        await json(route, { loras: [], manifest_last_check_at: null })
        return
      case 'GET /api/v1/loras/minimax_h3/details':
        await json(route, { loras: [], guidance_max_phases: 1, manifest_last_check_at: null })
        return
      case 'POST /api/v1/loras/check-updates':
        await json(route, {
          checked: 0,
          updates_available: 0,
          errors: [],
          skipped: true,
          reason: 'fresh',
          last_full_check_at: null,
        })
        return
      case 'GET /api/v1/services-config':
        await json(route, {
          llm_model_id: '',
          llm_device: 'auto',
          llm_provider: 'local',
          llm_remote_url: '',
          enhance_llm_model_id: '',
          enhance_llm_device: 'auto',
          google_api_key: '',
          google_api_key_set: false,
          openai_api_key: '',
          openai_api_key_set: false,
          anthropic_api_key: '',
          anthropic_api_key_set: false,
          use_director_v2: false,
          nsfw_mode: false,
          director_prompt_polish: 'off',
          civitai_api_key: '',
          civitai_api_key_set: false,
          voice_reference_enabled: false,
          ltx_progressive_pipeline: false,
          show_experimental: false,
          auto_performance: true,
          director_multishot_lora_mode: false,
          flashvsr_mode: 1,
          flashvsr_topk_ratio: 0,
          flashvsr_backend: 'auto',
        })
        return
      case 'GET /api/v1/host-terms':
        await json(route, { detail: 'Synthetic fixture has no host notices.' }, 404)
        return
      case 'GET /api/v1/llm/status':
        await json(route, { loaded: false, model_id: null, device: null, provider: '' })
        return
      case 'GET /api/v1/llm/models':
        await json(route, { models: [], guides: [], project_instance: 'synthetic-project-instance' })
        return
      case 'GET /api/v1/jobs':
        await json(route, { jobs: [{
          job_id: 'synthetic-queue-item',
          created_at: 1,
          status: 'queued',
          progress: 0,
          step: 0,
          total_steps: 1,
          phase: 'queued',
          message: 'Waiting in the synthetic queue',
          output_files: [],
          error: null,
          prompt_preview: '',
          active_window_prompt: '',
          model_type: 'minimax_h3',
          generation_mode: 'video',
          workspace: 'Synthetic project',
          window_current: 0,
          window_total: 0,
          window_step: 0,
          window_total_steps: 0,
          window_progress: 0,
          overall_progress: 0,
          queue_priority: 0,
          queue_held: false,
          hold_after_output: false,
          queue_position: 1,
          queue_wait_reason: 'waiting_for_turn',
          queue_reorder_reason: 'queue_order',
          queue_residency_bypass_count: 0,
          queue_residency_bypassed_waiters: 0,
          requested_outputs: 1,
          produced_outputs: 0,
          eta_seconds: 12,
          subtask_eta_seconds: null,
          queue: { paused: false, pause_after_current: false },
        }] })
        return
      case 'GET /api/v1/status/synthetic-queue-item':
        await json(route, {
          job_id: 'synthetic-queue-item',
          created_at: 1,
          status: 'queued',
          progress: 0,
          step: 0,
          total_steps: 1,
          phase: 'queued',
          message: 'Waiting in the synthetic queue',
          output_files: [],
          error: null,
          prompt_preview: '',
          active_window_prompt: '',
          model_type: 'minimax_h3',
          generation_mode: 'video',
          workspace: 'Synthetic project',
          window_current: 0,
          window_total: 0,
          window_step: 0,
          window_total_steps: 0,
          window_progress: 0,
          overall_progress: 0,
          queue_priority: 0,
          queue_held: queueHeld,
          hold_after_output: false,
          queue_position: 1,
          queue_wait_reason: queueHeld ? 'held' : 'waiting_for_turn',
          queue_reorder_reason: 'queue_order',
          queue_residency_bypass_count: 0,
          queue_residency_bypassed_waiters: 0,
          requested_outputs: 1,
          produced_outputs: 0,
          eta_seconds: 12,
          subtask_eta_seconds: null,
          queue: { paused: false, pause_after_current: false },
        })
        return
      case 'GET /api/v1/queue':
        if (queueDelayMs > 0) {
          await new Promise(resolve => setTimeout(resolve, queueDelayMs))
        }
        if (queueFailure) {
          await json(route, { detail: 'Synthetic transient queue failure' }, 503)
          return
        }
        await json(route, {
          ...EMPTY_QUEUE,
          summary: {
            ...EMPTY_QUEUE.summary,
            waiting: queueHeld ? 0 : 1,
            held: queueHeld ? 1 : 0,
          },
          jobs: EMPTY_QUEUE.jobs.map(job => ({
            ...job,
            held: queueHeld,
            wait_reason: queueHeld ? 'held' : 'waiting_for_turn',
          })),
        })
        return
      case 'GET /api/v1/presets':
        await json(route, { presets: [] })
        return
      case 'GET /api/v1/outputs':
        await json(route, { outputs: [], total: 0 })
        return
      case 'GET /api/v1/recipes':
        await json(route, { recipes: [] })
        return
      case 'GET /api/v1/director/pipelines':
        await json(route, { pipelines: [] })
        return
      case 'GET /api/v1/support/catalog':
        await json(route, {
          schema_version: 1,
          provider_catalog: {
            schema_version: 1,
            provider_neutral: true,
            providers: [],
          },
          benefit_availability: {
            scheduler_enforcement_enabled: false,
            effective_benefits: [],
            state: 'recorded_not_enforced',
          },
          support_priority: {
            scheduler_enforcement_enabled: false,
            effective_priority_boost: false,
            state: 'recorded_not_enforced',
            exclusions: [],
            notice: 'Synthetic fixture does not apply support priority.',
          },
        })
        return
      case 'GET /api/v1/blender/status':
        await json(route, {
          installed: false,
          ready: false,
          mcp_attested: false,
          runtime_attested: false,
          bridge_ready: false,
          recovery_action: 'Synthetic fixture',
          workspace: 'Synthetic project',
          bridge: '',
          blender_min_version: '',
          blender_version: null,
          arbitrary_code: false,
          max_total_frames: 0,
        })
        return
      case 'GET /api/v1/h3/style-workflows':
        await json(route, {
          source: 'synthetic',
          revision: 'synthetic-v1',
          source_revision: 'synthetic-v1',
          checked_at: null,
          update_status: 'bundled_fallback',
          supported_model_types: ['minimax_h3'],
          provenance: {
            workflow_identity_source: 'official_minimax_h3_skill',
            workflow_source: 'synthetic',
            prompt_brief_provenance: 'maestro_adapted',
            surface: 'huggingface_hub_canvas',
            supported_prompt_schemas: [],
            supported_h3_modes: [],
            supported_model_types: ['minimax_h3'],
          },
          styles: [],
        })
        return
      case 'POST /api/v1/h3/estimate':
        await json(route, { detail: 'Synthetic fixture does not estimate runtime.' }, 503)
        return
      case 'GET /api/v1/h3/acceleration':
        await json(route, {
          dense_sdpa: { available: true, default: true, quality: 'exact' },
          sol_attn: {
            available: false,
            default: false,
            approximate: true,
            repository: '',
            required_revision: '',
            installed_revision: null,
            hardware_ok: false,
            error: null,
          },
          sage2: {
            available: false,
            default: false,
            approximate: true,
            validated: false,
            repository: '',
            version: '',
            required_revision: '',
            installed_revision: null,
            hardware_ok: false,
            reason: 'Synthetic fixture',
            validation_reason: null,
            validation_record_sha256: null,
            validated_profiles: [],
            validated_model_types: [],
            last_unavailable_reason: null,
            model_status: {},
            turbo_status: 'unavailable',
          },
          w4a8: {
            available: false,
            default: false,
            experimental: true,
            repository: '',
            revision: '',
            runtime_revision: '',
            compatible_models: [],
            conditioning_mode: '',
            reason: 'Synthetic fixture',
          },
          stats: {},
        })
        return
      case 'GET /api/v1/h3/benchmark':
        await json(route, { records: [] })
        return
      case 'GET /api/v1/h3/evaluation/catalog':
        await json(route, { pinned_as_of: 'synthetic-v1', profiles: {} })
        return
      case 'GET /api/v1/projects/Synthetic%20project/assets':
        await json(route, { assets: [] })
        return
      case 'GET /api/v1/projects/Synthetic%20project/assets/reference-capabilities':
        await json(route, {
          schema_version: 2,
          planner_version: 'synthetic-reference-v2',
          intents: ['exact_spec', 'generic', 'brainstorming'],
          depths: {
            compact: { sheet_count: 1 },
            standard: { sheet_count: 2 },
            comprehensive: { sheet_count: 3 },
            custom: { minimum: 1, maximum: 4, default: 2 },
          },
          reference_types: [{
            id: 'character',
            presets: [{
              id: 'identity',
              label: 'Identity',
              ordered_roles: ['canonical_identity'],
              valid_source_roles: ['canonical_identity'],
              detail_operations: ['auto'],
            }],
            type_fields: [],
            detail_kinds: [{ id: 'face', label: 'Face' }],
            supports_custom_details: true,
          }],
          detail_operations: ['auto'],
          lora_scopes: ['auto', 'generation', 'editing'],
          content_capabilities: ['standard'],
          intelligence_policies: ['standard_auto'],
          uncensored_auto_review: {
            requested_model: 'auto_local',
            resolved_model: '',
            resolved_provider: 'local',
            vision_required: true,
            required_projector: '',
            installed: false,
            projector_available: false,
            vision_capable: false,
            resident: false,
            vision_available: false,
            loading: false,
            loading_phase: null,
            setup_state: 'missing_model',
            queue_ready: false,
          },
          explicit_generation_model: {
            preferred_order: [],
            resolved_model: '',
            fallback_model: '',
            selection_source: 'fallback',
            candidates: [],
          },
          review_policy: {
            mandatory_for_content_capabilities: [],
            mandatory_when_explicit_output: true,
            off_allowed_for_content_capabilities: ['standard'],
            mandatory_contract: 'explicit_unrestricted_fidelity_v1',
          },
          character_profile: {
            schema_version: 1,
            genders: ['woman', 'man', 'non_binary', 'unspecified'],
            age: { optional: true, minimum: 0, maximum: 999 },
            explicit_anatomy: ['breasts', 'vulva', 'penis'],
            explicit_convenience: { supported: false, requires_explicit_output: true },
          },
          max_candidate_count: 1,
          max_repair_attempts: 0,
          default_models: { generation_model: '', editor_model: '' },
        })
        return
      default:
        unexpected.push(`unknown ${key}`)
        await route.abort('blockedbyclient')
    }
  })

  return {
    setQueueFailure(failing) {
      queueFailure = failing
    },
    setQueueHeld(held) {
      queueHeld = held
    },
    setQueueDelay(delayMs) {
      queueDelayMs = Math.max(0, Math.floor(delayMs))
    },
    async assertClean() {
      expect(unexpected, 'No unknown API or external requests may escape the synthetic harness').toEqual([])
    },
    takeUnexpected() {
      return unexpected.splice(0, unexpected.length)
    },
  }
}
