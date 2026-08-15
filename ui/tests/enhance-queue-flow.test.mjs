import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

import { approveGenerationPlan, submitGeneration } from '../src/api/client.ts'

const UI_ROOT = fileURLToPath(new URL('..', import.meta.url))

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

let dialogModulePromise
function loadDialogModule() {
  if (dialogModulePromise) return dialogModulePromise
  dialogModulePromise = build({
    stdin: {
      contents: "export { H3GenerationPlanDialog } from './src/components/H3GenerationPlanDialog.tsx'",
      resolveDir: UI_ROOT,
      loader: 'js',
    },
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
    plugins: [{
      name: 'plan-dialog-test-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'plan-test' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'plan-test' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide', namespace: 'plan-test' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'plan-test' }))
        bundle.onLoad({ filter: /.*/, namespace: 'plan-test' }, args => {
          if (args.path === 'react') {
            return { contents: `
              export const useEffect = () => {}
              export const useMemo = callback => callback()
              export const useId = () => 'h3-duration-test-id'
              export const useState = initial => globalThis.__maestroPlanHooks.useState(initial)
            ` }
          }
          if (args.path === 'jsx-runtime') {
            return { contents: `
              export const Fragment = Symbol.for('plan-test-fragment')
              export const jsx = (type, props, key) => ({ type, key, props: props || {} })
              export const jsxs = jsx
            ` }
          }
          if (args.path === 'lucide') {
            return { contents: "export const AlertTriangle = 'AlertTriangle'; export const Check = 'Check'; export const X = 'X'" }
          }
          return { contents: 'export const useStore = selector => selector(globalThis.__maestroPlanStore)' }
        })
      },
    }],
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return dialogModulePromise
}

let storeModulePromise
function loadStoreModule() {
  if (storeModulePromise) return storeModulePromise
  storeModulePromise = build({
    stdin: {
      contents: "export { useStore } from './src/stores/useStore.ts'",
      resolveDir: UI_ROOT,
      loader: 'js',
    },
    bundle: true,
    format: 'esm',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return storeModulePromise
}

function plan(modelType = 'minimax_h3', checkpointOptions) {
  return {
    kind: 'h3_segments',
    clip_count: 1,
    fps: 24,
    requested_frames: 24,
    planned_frames: 24,
    published_frames: 24,
    segments: [{
      index: 1,
      frames: 24,
      duration_seconds: 1,
      model_type: modelType,
      model_reason: 'server plan',
      edge_anchor_locked: false,
    }],
    ...(checkpointOptions === undefined ? {} : { checkpoint_options: checkpointOptions }),
  }
}

function checkpoint(modelType, overrides = {}) {
  return {
    model_type: modelType,
    name: modelType,
    conditioning_mode: modelType === 'minimax_h3_ref2va' ? 'semantic_references' : 'first_last_frames',
    is_downloaded: true,
    managed_download: false,
    auto_download: false,
    terms_required: false,
    available: true,
    unavailable_reason: '',
    ...overrides,
  }
}

function flattenElements(value, result = []) {
  if (Array.isArray(value)) {
    for (const child of value) flattenElements(child, result)
    return result
  }
  if (!value || typeof value !== 'object') return result
  if ('type' in value && 'props' in value) result.push(value)
  flattenElements(value.props?.children, result)
  return result
}

function elementText(value) {
  if (Array.isArray(value)) return value.map(elementText).join('')
  if (value == null || typeof value === 'boolean') return ''
  if (typeof value !== 'object') return String(value)
  return elementText(value.props?.children)
}

function installPlanRenderState(store, currentPlan, nowMs = 1_000_000) {
  const jobId = 'review-job'
  const workspace = 'project one'
  const slots = [
    currentPlan.segments.map(segment => segment.model_type),
    currentPlan.segments.slice(1).map(() => 'continuous'),
    jobId,
    nowMs,
  ]
  const hooks = {
    cursor: 0,
    useState(initial) {
      const index = this.cursor++
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial
      return [slots[index], update => {
        slots[index] = typeof update === 'function' ? update(slots[index]) : update
      }]
    },
  }
  Object.assign(store, {
    pendingH3Plan: currentPlan,
    pendingH3PlanEstimate: null,
    pendingH3PlanJobId: jobId,
    pendingH3PlanWorkspace: workspace,
    jobs: [{ id: jobId, status: 'waiting_for_plan_approval', workspace, planReviewDeadline: 2_000 }],
    activeWorkspace: workspace,
    h3PlanReviewLoading: false,
    h3PlanReviewError: null,
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
    hostTermsLoading: false,
    hostTermsError: null,
    loadHostTerms() {},
    acceptHostTerm() {},
    closeH3PlanReview() {},
    cancelH3Plan() {},
  })
  globalThis.__maestroPlanStore = store
  globalThis.__maestroPlanHooks = hooks
  return {
    slots,
    render(Dialog) {
      hooks.cursor = 0
      return Dialog()
    },
  }
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, reject, resolve }
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function apiJobStatus(jobId, workspace, currentPlan, createdAt) {
  return {
    job_id: jobId,
    created_at: createdAt,
    status: 'waiting_for_plan_approval',
    progress: 0,
    step: 0,
    total_steps: 0,
    phase: 'plan_review',
    message: 'Review required',
    output_files: [],
    error: null,
    prompt_preview: '',
    active_window_prompt: '',
    model_type: 'minimax_h3',
    generation_mode: 'video',
    workspace,
    window_current: 0,
    window_total: 0,
    window_step: 0,
    window_total_steps: 0,
    window_progress: 0,
    overall_progress: 0,
    queue_priority: 0,
    queue_held: false,
    hold_after_output: false,
    queue_position: 0,
    queue_wait_reason: null,
    queue_reorder_reason: null,
    queue_residency_bypass_count: 0,
    queue_residency_bypassed_waiters: 0,
    requested_outputs: 1,
    produced_outputs: 0,
    queue: { paused: false, pause_after_current: false },
    h3_segment_plan: currentPlan,
  }
}

test('generation admission carries enhancement intent in the single durable request', async t => {
  const originalFetch = globalThis.fetch
  const requests = []
  globalThis.fetch = async (url, init) => {
    requests.push({ url: String(url), body: JSON.parse(String(init?.body)) })
    return new Response(JSON.stringify({
      job_id: 'a1b2c3d4',
      status: 'preparing',
      h3_estimate: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  const result = await submitGeneration({
    workspace: 'project one',
    prompt: 'browser-owned original',
    enhance_before_generate: true,
  })

  assert.equal(result.job_id, 'a1b2c3d4')
  assert.equal(result.status, 'preparing')
  assert.equal(requests.length, 1)
  assert.equal(requests[0].url, '/api/v1/generate')
  assert.equal(requests[0].body.enhance_before_generate, true)
})

test('standalone Enhance sends exact count only for type-1 sliding prompts', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: 'localhost' },
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()

  const enhancedRequests = []
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({ operation_id: 'enhance-ready', status: 'ready' })
    }
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      const body = JSON.parse(String(init.body))
      enhancedRequests.push(body)
      return jsonResponse({ original: body.prompt, enhanced: `${body.prompt} enhanced` })
    }
    throw new Error(`unexpected enhance request ${url}`)
  }

  const { useStore } = await loadStoreModule()
  const baseState = useStore.getState()
  t.after(() => {
    useStore.setState(baseState, true)
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
  })

  const modelOptions = {
    model_type: 'ltx_test', architecture: 'ltx_test', sliding_window: true,
    fps: 16, latent_size: 4, frames_steps: 4, frames_minimum: 1,
    sliding_window_defaults: { overlap_default: 5, discard_last_frames: 0 },
  }
  useStore.setState({
    activeWorkspace: 'project one',
    generationMode: 'video',
    startImage: null,
    imageRefs: [],
    modelOptions,
    durationSeconds: 10,
    slidingWindowSeconds: 3,
    slidingWindowOverlap: 5,
    params: {
      ...baseState.params,
      prompt: 'Frames prompt',
      model_type: 'ltx_test',
      image_mode: 2,
      multi_prompts_gen_type: 3,
    },
  })
  assert.equal(await useStore.getState().enhancePrompt(), true)

  useStore.setState(state => ({
    params: {
      ...state.params,
      prompt: 'Sliding prompt',
      image_mode: 0,
      multi_prompts_gen_type: 1,
    },
  }))
  assert.equal(await useStore.getState().enhancePrompt(), true)

  assert.equal(enhancedRequests.length, 2)
  assert.equal('window_count' in enhancedRequests[0], false)
  assert.equal(enhancedRequests[1].window_count, 4)
})

test('account identity changes fence deferred generation submission and active-job discovery', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  const events = []
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: 'localhost' },
  })
  globalThis.window.addEventListener('maestro:queue-refresh', () => events.push('queue'))
  globalThis.window.addEventListener('maestro:downloads-refresh', () => events.push('downloads'))
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
  })

  const account = (id, username) => ({
    id, username, role: 'owner', disabled: false, created_at: 1,
    has_email: false, passkey_credentials: 0, passkey_authentication_available: false,
  })
  const context = current => ({
    enabled: true,
    authenticated: Boolean(current),
    account: current,
    capabilities: current ? ['account.self', 'owner.admin'] : [],
    reauthenticated: Boolean(current),
    passkey_authentication_available: false,
    activation_state: 'ready',
    bootstrap_available: false,
  })
  const accountA = account('account-a', 'Account A')
  const accountB = account('account-b', 'Account B')
  const submission = deferred()
  const migration = deferred()
  const discovery = deferred()
  let discoverNext = false
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.endsWith('/api/v1/generate')) return submission.promise
    if (url.endsWith('/api/v1/account/projects/migration') && init.method === 'POST') {
      return migration.promise
    }
    if (url.endsWith('/api/v1/jobs')) {
      if (!discoverNext) return jsonResponse({ jobs: [] })
      return discovery.promise
    }
    if (url.endsWith('/api/v1/account/nonce')) {
      return jsonResponse({ nonce: 'logout-nonce', purpose: 'revoke_session', expires_in: 300 })
    }
    if (url.endsWith('/api/v1/account/logout') && init.method === 'POST') {
      return jsonResponse({ status: 'logged_out' })
    }
    if (url.endsWith('/api/v1/account/context')) return jsonResponse(context(null))
    if (url.endsWith('/api/v1/access-context')) {
      return jsonResponse({ remote: false, accounts: context(accountB) })
    }
    if (url.endsWith('/api/v1/workspaces')) return jsonResponse({ workspaces: [], active: '' })
    throw new Error(`unexpected identity-race request ${url}`)
  }

  const { useStore } = await loadStoreModule()
  const originalPoll = useStore.getState()._pollRecoveredJob
  const originalReconnectDirector = useStore.getState().reconnectDirectorPreparation
  let polls = 0
  let directorReconnects = 0
  const baseState = useStore.getState()
  useStore.setState({
    accessContext: { remote: false, accounts: context(accountA) },
    accountContext: context(accountA),
    activeWorkspace: 'same-project-name',
    workspaces: [{ name: 'same-project-name', project_permissions: ['project.read', 'project.generate'] }],
    generationMode: 'image',
    modelOptions: null,
    modelOptionsLoading: false,
    h3StyleWorkflow: '',
    params: {
      ...baseState.params,
      prompt: 'deferred request',
      model_type: 'test_image_model',
      image_mode: 1,
    },
    jobs: [],
    isGenerating: false,
    _pollRecoveredJob() { polls += 1 },
    reconnectDirectorPreparation: async () => { directorReconnects += 1 },
  })

  const pendingSubmit = useStore.getState().startGeneration()
  await Promise.resolve()
  assert.equal(useStore.getState().jobs.length, 1, 'submission placeholder should be visible before logout')
  await useStore.getState().logoutAccount()
  assert.deepEqual(useStore.getState().jobs, [], 'logout synchronously scrubs the old placeholder')
  const newerIdentityJob = {
    id: 'newer-identity-job', status: 'queued', progress: 0, step: 0, totalSteps: 0,
    phase: '', message: 'Queued...', outputFiles: [], error: null, oomInfo: null,
    workspace: 'same-project-name',
  }
  useStore.setState({ jobs: [newerIdentityJob], isGenerating: true })
  submission.resolve(jsonResponse({ job_id: 'stale-submit', status: 'queued', h3_estimate: null }))
  await pendingSubmit
  assert.deepEqual(useStore.getState().jobs, [newerIdentityJob], 'stale cleanup preserves newer identity jobs')
  assert.equal(polls, 0)
  assert.deepEqual(events, [])

  useStore.setState({
    accessContext: { remote: false, accounts: context(accountA) },
    accountContext: context(accountA),
    accountProjectMigration: null,
    accountProjectMigrationLoading: false,
  })
  const pendingMigration = useStore.getState().migrateAccountProjects()
  await Promise.resolve()
  await useStore.getState().loadAccessContext(false)
  migration.resolve(jsonResponse({
    schema_version: 2,
    state: 'active',
    project_count: 7,
    assigned_count: 7,
    quarantined_count: 0,
    needs_attention: 0,
    migrated_at: 1,
    owner_account_id: 'account-a',
  }))
  assert.equal(await pendingMigration, null, 'stale migration completion is not presented as success')
  assert.equal(useStore.getState().accountProjectMigration, null)

  useStore.setState({
    accessContext: { remote: false, accounts: context(accountA) },
    accountContext: context(accountA),
    activeWorkspace: 'same-project-name',
    workspaces: [{ name: 'same-project-name', project_permissions: ['project.read', 'project.generate'] }],
    jobs: [],
    isGenerating: false,
  })
  discoverNext = true
  const pendingDiscovery = useStore.getState().reconnectJobs()
  await Promise.resolve()
  await useStore.getState().loadAccessContext(false)
  discovery.resolve(jsonResponse({
    jobs: [{ ...apiJobStatus('stale-discovery', 'same-project-name', plan(), 1), status: 'running' }],
  }))
  await pendingDiscovery
  assert.deepEqual(useStore.getState().jobs, [])
  assert.equal(polls, 0)
  assert.equal(directorReconnects, 0)

  useStore.setState({
    _pollRecoveredJob: originalPoll,
    reconnectDirectorPreparation: originalReconnectDirector,
  })
})

test('account identity and Director leases reject deferred uploads without seeding new state', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: 'localhost' },
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
  })

  const account = id => ({
    id, username: id, role: 'owner', disabled: false, created_at: 1,
    has_email: false, passkey_credentials: 0, passkey_authentication_available: false,
  })
  const context = current => ({
    enabled: true, authenticated: true, account: current,
    capabilities: ['account.self', 'owner.admin'], reauthenticated: true,
    passkey_authentication_available: false, activation_state: 'ready', bootstrap_available: false,
  })
  const accountA = account('account-a')
  const accountB = account('account-b')
  let visibleAccount = accountA
  const sourceUpload = deferred()
  const voiceUpload = deferred()
  const directorUpload = deferred()
  let imageUploadCount = 0
  globalThis.fetch = async input => {
    const url = String(input)
    if (url.endsWith('/api/v1/access-context')) {
      return jsonResponse({ remote: false, accounts: context(visibleAccount) })
    }
    if (url.endsWith('/api/v1/upload-audio')) return voiceUpload.promise
    if (url.endsWith('/api/v1/upload')) {
      imageUploadCount += 1
      return imageUploadCount === 1 ? sourceUpload.promise : directorUpload.promise
    }
    throw new Error(`unexpected upload-race request ${url}`)
  }

  const { useStore } = await loadStoreModule()
  await useStore.getState().loadAccessContext(false)
  useStore.setState({
    accountContext: context(accountA),
    accessContext: { remote: false, accounts: context(accountA) },
    toolsSourcePath: null,
    toolsSourceName: null,
    toolsSourceUrl: null,
    toolsRevoiceRefs: [null, null],
  })

  const sourceFile = new File(['source'], 'old-source.mp4', { type: 'video/mp4' })
  const staleSource = useStore.getState().uploadToolsSource(sourceFile)
  visibleAccount = accountB
  await useStore.getState().loadAccessContext(false)
  sourceUpload.resolve(jsonResponse({ filename: 'old-source.mp4', path: '/private/account-a/source.mp4', url: '/stale' }))
  assert.equal(await staleSource, false)
  assert.equal(useStore.getState().toolsSourcePath, null)

  const voiceFile = new File(['voice'], 'old-voice.wav', { type: 'audio/wav' })
  const staleVoice = useStore.getState().uploadToolsRevoiceRef(0, voiceFile)
  visibleAccount = accountA
  await useStore.getState().loadAccessContext(false)
  voiceUpload.resolve(jsonResponse({ filename: 'old-voice.wav', path: '/private/account-b/voice.wav', url: '/stale' }))
  assert.equal(await staleVoice, false)
  assert.deepEqual(useStore.getState().toolsRevoiceRefs, [null, null])

  const referenceFile = new File(['image'], 'old-reference.png', { type: 'image/png' })
  useStore.setState({ directorReferenceImage: referenceFile, directorReferenceImagePath: null })
  let ownsDirectorRequest = true
  const staleDirectorRefs = useStore.getState()._uploadDirectorRefs({
    ownsWorkspace: () => ownsDirectorRequest,
  })
  ownsDirectorRequest = false
  directorUpload.resolve(jsonResponse({ filename: 'old-reference.png', path: '/private/old-reference.png', url: '/stale' }))
  await assert.rejects(staleDirectorRefs, error => error?.name === 'AbortError')
  assert.equal(useStore.getState().directorReferenceImagePath, null)
})

test('plan approval binds overrides to the exact encoded job and project', async t => {
  const originalFetch = globalThis.fetch
  let request
  globalThis.fetch = async (url, init) => {
    request = { url: String(url), body: JSON.parse(String(init?.body)) }
    return new Response(JSON.stringify({
      job_id: 'job/id',
      status: 'queued',
      h3_segment_plan: { kind: 'h3_segments', segments: [] },
      h3_estimate: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  await approveGenerationPlan('job/id', {
    workspace: 'project one',
    segment_overrides: [{ model_type: 'minimax_h3' }],
    boundary_overrides: [{ type: 'cut' }],
    h3_ref2va_terms_accepted: false,
    plan_revision: 'h3dp1_exact',
    duration_snap_mode: 'manual',
    segment_duration_edits: [{ segment_index: 1, published_frames: 23 }],
    duration_redistribution: 'future',
  })

  assert.equal(request.url, '/api/v1/generate/job%2Fid/plan/approve')
  assert.deepEqual(request.body, {
    workspace: 'project one',
    segment_overrides: [{ model_type: 'minimax_h3' }],
    boundary_overrides: [{ type: 'cut' }],
    h3_ref2va_terms_accepted: false,
    plan_revision: 'h3dp1_exact',
    duration_snap_mode: 'manual',
    segment_duration_edits: [{ segment_index: 1, published_frames: 23 }],
    duration_redistribution: 'future',
  })
})

test('stale approval conflicts return one bounded error without reflecting detail', async t => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(
    JSON.stringify({ detail: 'State changed; refresh first.' }),
    { status: 409, headers: { 'Content-Type': 'application/json' } },
  )
  t.after(() => { globalThis.fetch = originalFetch })

  await assert.rejects(
    approveGenerationPlan('a1b2c3d4', {
      workspace: 'project one',
      segment_overrides: [],
      boundary_overrides: [],
    }),
    /The plan review state changed\. Refresh and try again\./,
  )
})

test('store maps one revision-fenced duration decision and rejects a stale local plan', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    alert() {},
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
  })

  const currentPlan = {
    ...plan(),
    duration_plan: {
      revision: 'h3dp1_current',
      target_published_frames: 24,
      current_published_frames: 24,
      current_generated_frames: 24,
      fps: 24,
      snap_candidates: {},
      segments: [],
      redistribution_mode: 'none',
      outcome: 'exact',
      reason: 'Exact.',
      residual_published_frames: 0,
    },
  }
  let requestBody
  globalThis.fetch = async (url, init) => {
    assert.match(String(url), /\/api\/v1\/generate\/duration-job\/plan\/approve$/)
    requestBody = JSON.parse(String(init?.body))
    return new Response(JSON.stringify({
      job_id: 'duration-job',
      status: 'queued',
      h3_segment_plan: currentPlan,
      h3_estimate: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }

  const { useStore } = await loadStoreModule()
  const originalPoll = useStore.getState()._pollRecoveredJob
  useStore.setState({
    activeWorkspace: 'project one',
    jobs: [{ id: 'duration-job', status: 'waiting_for_plan_approval', workspace: 'project one' }],
    pendingH3Plan: currentPlan,
    pendingH3PlanEstimate: null,
    pendingH3PlanJobId: 'duration-job',
    pendingH3PlanWorkspace: 'project one',
    h3PlanReviewLoading: false,
    h3PlanReviewError: null,
    _pollRecoveredJob() {},
  })

  await useStore.getState().approveH3Plan({
    segmentOverrides: [],
    boundaryOverrides: [],
    planRevision: 'h3dp1_current',
    durationSnapMode: 'manual',
    segmentDurationEdits: [{ segmentIndex: 1, publishedFrames: 23 }],
    durationRedistribution: 'future',
  })
  assert.deepEqual(requestBody, {
    workspace: 'project one',
    segment_overrides: [],
    boundary_overrides: [],
    h3_ref2va_terms_accepted: false,
    plan_revision: 'h3dp1_current',
    duration_snap_mode: 'manual',
    segment_duration_edits: [{ segment_index: 1, published_frames: 23 }],
    duration_redistribution: 'future',
  })

  requestBody = undefined
  useStore.setState({
    jobs: [{ id: 'duration-job', status: 'waiting_for_plan_approval', workspace: 'project one' }],
    pendingH3Plan: currentPlan,
    pendingH3PlanJobId: 'duration-job',
    pendingH3PlanWorkspace: 'project one',
  })
  await useStore.getState().approveH3Plan({
    segmentOverrides: [],
    boundaryOverrides: [],
    planRevision: 'h3dp1_stale',
  })
  assert.equal(requestBody, undefined)
  assert.match(useStore.getState().h3PlanReviewError, /duration plan changed/i)
  useStore.setState({ _pollRecoveredJob: originalPoll })
})

test('plan editor uses a present server catalog verbatim and falls back only for legacy plans', async t => {
  const originalWindow = globalThis.window
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    alert() {},
  })
  t.after(() => { globalThis.window = originalWindow })

  const { H3GenerationPlanDialog } = await loadDialogModule()
  const store = {
    models: [
      { model_type: 'minimax_h3', name: 'Base', is_downloaded: true },
      { model_type: 'minimax_h3_ref2va', name: 'Ref2VA', is_downloaded: true },
    ],
    approveH3Plan() {},
    selectModel() { throw new Error('plan review must not select the Studio model') },
    h3SelectedProfile: 'custom',
  }

  const durable = installPlanRenderState(store, plan('minimax_h3', []))
  const durableSelect = flattenElements(durable.render(H3GenerationPlanDialog))
    .find(element => element.type === 'select')
  assert.ok(durableSelect)
  assert.equal(flattenElements(durableSelect.props.children).filter(element => element.type === 'option').length, 0)

  const legacy = installPlanRenderState(store, plan('minimax_h3'))
  const legacySelect = flattenElements(legacy.render(H3GenerationPlanDialog))
    .find(element => element.type === 'select')
  assert.ok(legacySelect)
  assert.deepEqual(
    flattenElements(legacySelect.props.children)
      .filter(element => element.type === 'option')
      .map(element => element.props.value),
    ['minimax_h3', 'minimax_h3_ref2va'],
  )
})

test('plan editor rejects unavailable overrides without mutating Studio model or profile', async t => {
  const originalWindow = globalThis.window
  const alerts = []
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    alert(message) { alerts.push(message) },
  })
  t.after(() => { globalThis.window = originalWindow })

  const { H3GenerationPlanDialog } = await loadDialogModule()
  const approvals = []
  let studioSelections = 0
  const store = {
    models: [{ model_type: 'minimax_h3_ref2va', name: 'legacy-only', is_downloaded: true }],
    approveH3Plan(decision) { approvals.push(decision) },
    selectModel() { studioSelections += 1 },
    h3SelectedProfile: 'quality',
  }
  const currentPlan = plan('minimax_h3', [
    checkpoint('minimax_h3', {
      available: false,
      unavailable_reason: 'disabled by the current server plan',
    }),
    checkpoint('minimax_h3_pinkcherry_fl2va'),
  ])
  const rendered = installPlanRenderState(store, currentPlan)

  let elements = flattenElements(rendered.render(H3GenerationPlanDialog))
  let approve = elements.find(element => (
    element.type === 'button' && elementText(element).includes('Approve & resume')
  ))
  assert.ok(approve)
  assert.equal(approve.props.disabled, true)
  approve.props.onClick()
  assert.equal(approvals.length, 0)
  assert.match(alerts[0], /disabled by the current server plan/)

  const modelSelect = elements.find(element => element.type === 'select')
  modelSelect.props.onChange({ target: { value: 'minimax_h3_pinkcherry_fl2va' } })
  elements = flattenElements(rendered.render(H3GenerationPlanDialog))
  approve = elements.find(element => (
    element.type === 'button' && elementText(element).includes('Approve & resume')
  ))
  assert.equal(approve.props.disabled, false)
  approve.props.onClick()

  assert.equal(studioSelections, 0)
  assert.equal(store.h3SelectedProfile, 'quality')
  assert.equal(approvals.length, 1)
  assert.equal(approvals[0].segmentOverrides[0].model_type, 'minimax_h3_pinkcherry_fl2va')
})

test('one-shot plan hydration and the sole recurring poller fence all late winners', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    alert() {},
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
  })

  const pending = new Map()
  const requests = []
  let reconnectRequests = 0
  globalThis.fetch = async url => {
    if (String(url).includes('/api/v1/cancel/')) {
      return new Response(null, { status: 200 })
    }
    if (String(url).endsWith('/api/v1/jobs')) {
      reconnectRequests += 1
      return new Response(JSON.stringify({ jobs: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    const match = /\/api\/v1\/status\/([^/?]+)/.exec(String(url))
    assert.ok(match, `unexpected request ${url}`)
    const jobId = decodeURIComponent(match[1])
    requests.push(jobId)
    const configured = pending.get(jobId)
    const request = Array.isArray(configured) ? configured.shift() : configured
    assert.ok(request, `missing response for ${jobId}`)
    const response = await request.promise
    return new Response(JSON.stringify(response), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const { useStore } = await loadStoreModule()
  const reset = jobs => useStore.setState({
    activeWorkspace: 'project one',
    jobs,
    pendingH3Plan: null,
    pendingH3PlanEstimate: null,
    pendingH3PlanJobId: null,
    pendingH3PlanWorkspace: null,
    h3PlanReviewLoading: false,
    h3PlanReviewError: null,
  })
  const reviewJob = (id, createdAt, hydratedPlan = null) => ({
    id,
    createdAt,
    status: 'waiting_for_plan_approval',
    workspace: 'project one',
    h3SegmentPlan: hydratedPlan,
  })

  reset([reviewJob('cached', 1, plan())])
  await useStore.getState().openH3PlanReview('cached')
  assert.equal(requests.length, 0)
  assert.equal(useStore.getState().pendingH3PlanJobId, 'cached')
  useStore.getState().closeH3PlanReview()

  const switched = deferred()
  pending.set('switching', switched)
  reset([reviewJob('switching', 2)])
  const switching = useStore.getState().openH3PlanReview('switching')
  await Promise.resolve()
  assert.deepEqual(requests, ['switching'])
  assert.equal(useStore.getState().pendingH3PlanWorkspace, 'project one')
  useStore.setState({ activeWorkspace: 'project two' })
  switched.resolve(apiJobStatus('switching', 'project one', plan(), 2))
  await switching
  assert.equal(useStore.getState().pendingH3PlanJobId, null)
  assert.equal(useStore.getState().h3PlanReviewLoading, false)

  const validOwner = deferred()
  pending.set('valid-owner', validOwner)
  reset([reviewJob('valid-owner', 3)])
  const validOpen = useStore.getState().openH3PlanReview('valid-owner')
  await useStore.getState().openH3PlanReview('missing-job')
  validOwner.resolve(apiJobStatus('valid-owner', 'project one', plan(), 3))
  await validOpen
  assert.equal(useStore.getState().pendingH3PlanJobId, 'valid-owner')
  useStore.getState().closeH3PlanReview()

  const provisional = deferred()
  pending.set('fresh-job', provisional)
  reset([reviewJob('fresh-job', 10)])
  const freshOpen = useStore.getState().openH3PlanReview('fresh-job')
  provisional.resolve(apiJobStatus('fresh-job', 'project one', plan(), 11))
  await freshOpen
  assert.equal(useStore.getState().pendingH3PlanJobId, 'fresh-job')
  assert.equal(useStore.getState().jobs[0].createdAt, 11)
  useStore.getState().closeH3PlanReview()

  const first = deferred()
  const second = deferred()
  pending.set('first', first)
  pending.set('second', second)
  reset([reviewJob('first', 3), reviewJob('second', 4)])
  const firstOpen = useStore.getState().openH3PlanReview('first')
  const secondOpen = useStore.getState().openH3PlanReview('second')
  second.resolve(apiJobStatus('second', 'project one', plan('minimax_h3_ref2va'), 4))
  await secondOpen
  first.resolve(apiJobStatus('first', 'project one', plan(), 3))
  await firstOpen
  assert.equal(useStore.getState().pendingH3PlanJobId, 'second')
  assert.equal(useStore.getState().pendingH3Plan.segments[0].model_type, 'minimax_h3_ref2va')

  const replaced = deferred()
  pending.set('reused-id', replaced)
  reset([reviewJob('reused-id', 5)])
  const replacedOpen = useStore.getState().openH3PlanReview('reused-id')
  useStore.setState({ jobs: [reviewJob('reused-id', 6)] })
  replaced.resolve(apiJobStatus('reused-id', 'project one', plan(), 5))
  await replacedOpen
  assert.equal(useStore.getState().pendingH3PlanJobId, null)
  assert.equal(useStore.getState().pendingH3Plan, null)
  assert.deepEqual(requests, [
    'switching',
    'valid-owner',
    'fresh-job',
    'first',
    'second',
    'reused-id',
  ])

  const stalePoll = deferred()
  const replacementPoll = deferred()
  pending.set('polled-id', [stalePoll, replacementPoll])
  reset([{ ...reviewJob('polled-id', 7), status: 'running' }])
  useStore.getState()._pollRecoveredJob('polled-id')
  await Promise.resolve()
  useStore.getState().stopGeneration('polled-id')
  useStore.setState({ jobs: [{ ...reviewJob('polled-id', 8), status: 'running' }] })
  useStore.getState()._pollRecoveredJob('polled-id')
  await Promise.resolve()
  stalePoll.resolve({
    ...apiJobStatus('polled-id', 'project one', plan(), 7),
    status: 'completed',
  })
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(useStore.getState().jobs.length, 1)
  assert.equal(useStore.getState().jobs[0].createdAt, 8)
  assert.equal(useStore.getState().jobs[0].status, 'running')
  useStore.getState().stopGeneration('polled-id')
  replacementPoll.resolve({
    ...apiJobStatus('polled-id', 'project one', plan(), 8),
    status: 'cancelled',
  })
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(useStore.getState().jobs.length, 0)

  const firstFailure = deferred()
  const secondFailure = deferred()
  const staleFailure = deferred()
  const currentPoll = deferred()
  pending.set('failed-poll-id', [firstFailure, secondFailure, staleFailure, currentPoll])
  reset([{ ...reviewJob('failed-poll-id', 9), status: 'running' }])
  useStore.getState()._pollRecoveredJob('failed-poll-id')
  await Promise.resolve()
  firstFailure.reject(new Error('transient one'))
  await new Promise(resolve => setTimeout(resolve, 0))
  useStore.getState()._pollRecoveredJob('failed-poll-id')
  secondFailure.reject(new Error('transient two'))
  await new Promise(resolve => setTimeout(resolve, 0))
  useStore.getState()._pollRecoveredJob('failed-poll-id')
  await Promise.resolve()
  useStore.getState().stopGeneration('failed-poll-id')
  useStore.setState({ jobs: [{ ...reviewJob('failed-poll-id', 10), status: 'running' }] })
  useStore.getState()._pollRecoveredJob('failed-poll-id')
  await Promise.resolve()
  staleFailure.reject(new Error('stale third failure'))
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(reconnectRequests, 0)
  assert.equal(useStore.getState().jobs[0].createdAt, 10)
  useStore.getState().stopGeneration('failed-poll-id')
  currentPoll.resolve({
    ...apiJobStatus('failed-poll-id', 'project one', plan(), 10),
    status: 'cancelled',
  })
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(useStore.getState().jobs.length, 0)
})

test('same-poller wakes coalesce behind one in-flight request and stop at terminal state', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }

  let nextTimerId = 1
  const timers = new Map()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout(callback, delay) {
      const id = nextTimerId++
      timers.set(id, { callback, delay })
      return id
    },
    clearTimeout(id) { timers.delete(id) },
    setInterval,
    clearInterval,
    alert() {},
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
  })

  const first = deferred()
  const second = deferred()
  const responses = [first, second]
  let requestCount = 0
  let inFlight = 0
  let maxInFlight = 0
  globalThis.fetch = async url => {
    assert.match(String(url), /\/api\/v1\/status\/coalesced-job$/)
    const response = responses.shift()
    assert.ok(response, 'unexpected overlapping or third status request')
    requestCount += 1
    inFlight += 1
    maxInFlight = Math.max(maxInFlight, inFlight)
    try {
      const status = await response.promise
      return new Response(JSON.stringify(status), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    } finally {
      inFlight -= 1
    }
  }

  const { useStore } = await loadStoreModule()
  let outputLoads = 0
  useStore.setState({
    activeWorkspace: 'project one',
    jobs: [{
      id: 'coalesced-job',
      createdAt: 21,
      status: 'running',
      workspace: 'project one',
    }],
    isGenerating: true,
    loadOutputs: async () => { outputLoads += 1 },
    refreshOutputs: async () => {},
  })

  useStore.getState()._pollRecoveredJob('coalesced-job')
  await Promise.resolve()
  assert.equal(requestCount, 1)
  assert.equal(inFlight, 1)
  assert.equal(timers.size, 0)

  useStore.getState()._pollRecoveredJob('coalesced-job')
  useStore.getState()._pollRecoveredJob('coalesced-job')
  useStore.getState()._pollRecoveredJob('coalesced-job')
  await Promise.resolve()
  assert.equal(requestCount, 1)
  assert.equal(inFlight, 1)
  assert.equal(maxInFlight, 1)
  assert.equal(timers.size, 0)

  first.resolve({
    ...apiJobStatus('coalesced-job', 'project one', plan(), 21),
    status: 'running',
  })
  for (let attempt = 0; attempt < 20 && requestCount < 2; attempt++) {
    await new Promise(resolve => setImmediate(resolve))
  }
  assert.equal(requestCount, 2)
  assert.equal(inFlight, 1)
  assert.equal(maxInFlight, 1)
  assert.equal(timers.size, 0)

  second.resolve({
    ...apiJobStatus('coalesced-job', 'project one', plan(), 21),
    status: 'completed',
  })
  for (let attempt = 0; attempt < 20 && useStore.getState().jobs.length > 0; attempt++) {
    await new Promise(resolve => setImmediate(resolve))
  }
  assert.equal(useStore.getState().jobs.length, 0)
  assert.equal(useStore.getState().isGenerating, false)
  assert.equal(requestCount, 2)
  assert.equal(inFlight, 0)
  assert.equal(maxInFlight, 1)
  assert.equal(timers.size, 0)
  assert.equal(outputLoads, 1)

  await new Promise(resolve => setImmediate(resolve))
  assert.equal(requestCount, 2)
  assert.equal(timers.size, 0)
})
