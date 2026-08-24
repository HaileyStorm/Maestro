import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

import {
  approveGenerationPlan,
  submitGeneration,
  waitForLlmEnhanceOperation,
} from '../src/api/client.ts'

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

let storeBundlePromise
let storeModulePromise
let freshStoreModuleSequence = 0
function storeBundle() {
  if (storeBundlePromise) return storeBundlePromise
  storeBundlePromise = build({
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
  }).then(result => result.outputFiles[0].text)
  return storeBundlePromise
}

function loadStoreModuleFresh() {
  freshStoreModuleSequence += 1
  const sequence = freshStoreModuleSequence
  return storeBundle().then(source => import(`${asDataModule(source)}#store-realm-${sequence}`))
}

function loadStoreModule() {
  if (storeModulePromise) return storeModulePromise
  storeModulePromise = loadStoreModuleFresh()
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

async function waitForCondition(predicate, label, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(`Timed out waiting for ${label}`)
    await new Promise(resolve => setTimeout(resolve, 5))
  }
}

class QueuedEnhanceLocksFake {
  lifetimeHeld = new Set()
  ledgerHeld = false
  ledgerQueue = []
  blockedLedger = null
  blockNextLedger = false

  request(name, options, callback) {
    assert.equal(options.mode, 'exclusive')
    if (name !== 'maestro-prompt-enhance-ledger-v2') {
      assert.equal(options.ifAvailable, true)
      if (this.lifetimeHeld.has(name)) return Promise.resolve(callback(null))
      this.lifetimeHeld.add(name)
      return Promise.resolve(callback({ name }))
    }
    assert.ok(options.signal instanceof AbortSignal)
    return new Promise((resolve, reject) => {
      const entry = { name, callback, resolve, reject, signal: options.signal, onAbort: null }
      entry.onAbort = () => {
        const queuedIndex = this.ledgerQueue.indexOf(entry)
        if (queuedIndex < 0) return
        this.ledgerQueue.splice(queuedIndex, 1)
        reject(new DOMException('Ledger lock request aborted', 'AbortError'))
      }
      entry.signal.addEventListener('abort', entry.onAbort, { once: true })
      if (this.ledgerHeld) this.ledgerQueue.push(entry)
      else this._startLedger(entry)
    })
  }

  blockNext() { this.blockNextLedger = true }

  releaseBlocked() {
    assert.ok(this.blockedLedger)
    const entry = this.blockedLedger
    this.blockedLedger = null
    this._runLedger(entry)
  }

  _startLedger(entry) {
    this.ledgerHeld = true
    if (this.blockNextLedger) {
      this.blockNextLedger = false
      this.blockedLedger = entry
      return
    }
    this._runLedger(entry)
  }

  _runLedger(entry) {
    entry.signal.removeEventListener('abort', entry.onAbort)
    Promise.resolve()
      .then(() => entry.callback({ name: entry.name }))
      .then(entry.resolve, entry.reject)
      .finally(() => {
        this.ledgerHeld = false
        const next = this.ledgerQueue.shift()
        if (next) this._startLedger(next)
      })
  }
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

test('Prompt Enhance waits through queued then running before fetching its scoped result', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const requestId = '0f6f2163-42f7-41d0-a260-79b697081d96'
  const canonicalRequestId = requestId.replaceAll('-', '')
  const projectInstance = 'a'.repeat(64)
  const scope = {
    requestId,
    workspace: 'queued-project',
    projectInstance,
  }
  const statuses = []
  let statusPolls = 0
  let resultFetches = 0
  globalThis.window = {
    setTimeout: (callback, _delay) => setTimeout(callback, 0),
    clearTimeout,
  }
  globalThis.fetch = async input => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      assert.match(url, /workspace=queued-project/)
      return jsonResponse({ models: [], guides: [], project_instance: projectInstance })
    }
    if (url.includes(`/api/v1/llm/operations/enhance/${requestId}/result?`)) {
      resultFetches += 1
      assert.match(url, /workspace=queued-project/)
      return jsonResponse({ original: 'private original', enhanced: 'private enhanced' })
    }
    if (url.includes(`/api/v1/llm/operations/enhance/${requestId}?`)) {
      statusPolls += 1
      assert.match(url, /workspace=queued-project/)
      const status = statusPolls === 1 ? 'running' : 'completed'
      return jsonResponse({
        request_id: canonicalRequestId,
        operation_kind: 'enhance',
        status,
        phase: status,
        stage: status,
        pass: 1,
        pass_limit: 1,
        attempt: 1,
        attempt_limit: 1,
        partial_text: status === 'running' ? 'private partial' : '',
        generated_tokens_approx: status === 'running' ? 2 : 0,
        elapsed_seconds: 1,
        live_tps: null,
        average_tps: null,
        result_available: status === 'completed',
        retryable: false,
      })
    }
    throw new Error(`unexpected queued Enhance request ${url}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    if (originalWindow === undefined) delete globalThis.window
    else globalThis.window = originalWindow
  })

  const result = await waitForLlmEnhanceOperation(
    scope,
    undefined,
    {
      request_id: canonicalRequestId,
      operation_kind: 'enhance',
      status: 'queued',
      phase: 'queued',
      stage: 'queued',
      pass: 0,
      pass_limit: 1,
      attempt: 0,
      attempt_limit: 1,
      partial_text: '',
      generated_tokens_approx: 0,
      elapsed_seconds: 0,
      live_tps: null,
      average_tps: null,
      result_available: false,
      retryable: false,
    },
    status => statuses.push(status.status),
  )

  assert.deepEqual(statuses, ['queued', 'running', 'completed'])
  assert.equal(statusPolls, 2)
  assert.equal(resultFetches, 1)
  assert.deepEqual(result, {
    original: 'private original',
    enhanced: 'private enhanced',
  })
})

test('standalone Enhance sends exact count only for type-1 sliding prompts', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
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
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: {
      locks: {
        request(name, options, callback) {
          assert.equal(options.mode, 'exclusive')
          if (name === 'maestro-prompt-enhance-ledger-v2') {
            assert.ok(options.signal instanceof AbortSignal)
            return Promise.resolve(callback({ name }))
          }
          assert.equal(options.ifAvailable, true)
          return Promise.resolve(callback({ name }))
        },
      },
    },
  })

  const enhancedRequests = []
  const projectInstance = 'a'.repeat(64)
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      return jsonResponse({ models: [], guides: [], project_instance: projectInstance })
    }
    if (url.endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({ operation_id: 'enhance-ready', status: 'ready', phase: 'ready', retryable: false }, 202)
    }
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      const body = JSON.parse(String(init.body))
      enhancedRequests.push(body)
      return jsonResponse({
        request_id: body.request_id.replaceAll('-', ''),
        operation_kind: 'enhance',
        status: 'completed', phase: 'completed', stage: 'completed',
        pass: 1, pass_limit: 1, attempt: 1, attempt_limit: 1,
        partial_text: `${body.prompt} enhanced`, generated_tokens_approx: 2,
        elapsed_seconds: 1, live_tps: null, average_tps: 2,
        result_available: true, retryable: false,
      }, 202)
    }
    if (url.includes('/api/v1/llm/operations/enhance/') && url.includes('/result?')) {
      const requestId = decodeURIComponent(url.split('/enhance/')[1].split('/')[0])
      const body = enhancedRequests.find(item => item.request_id === requestId)
      assert.ok(body)
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
    if (originalNavigatorDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
    } else {
      delete globalThis.navigator
    }
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

test('standalone Enhance hands status to Queue and defers a scoped result until Generate opens', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: 'localhost' },
    matchMedia: () => ({ matches: false }),
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { locks: { request: (name, options, callback) => Promise.resolve(callback({ name })) } },
  })

  const projectInstance = 'e'.repeat(64)
  let enhancePosts = 0
  let enhanceStatusReads = 0
  let generationCalls = 0
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      return jsonResponse({ models: [], guides: [], project_instance: projectInstance })
    }
    if (url.endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({ operation_id: 'queue-card-ready', status: 'ready', phase: 'ready', retryable: false }, 202)
    }
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      enhancePosts += 1
      const body = JSON.parse(String(init.body))
      return jsonResponse({
        request_id: body.request_id.replaceAll('-', ''), operation_kind: 'enhance',
        status: 'queued', phase: 'queued', stage: 'queued',
        pass: 1, pass_limit: 1, attempt: 1, attempt_limit: 1,
        partial_text: '', generated_tokens_approx: 0,
        elapsed_seconds: 0, live_tps: null, average_tps: null,
        result_available: false, retryable: false,
      }, 202)
    }
    if (url.includes('/api/v1/llm/operations/enhance/') && !url.includes('/result?')) {
      enhanceStatusReads += 1
      const completed = enhanceStatusReads > 1
      return jsonResponse({
        request_id: url.match(/enhance\/([^?]+)/)?.[1] || '', operation_kind: 'enhance',
        status: completed ? 'completed' : 'running',
        phase: completed ? 'completed' : 'generating',
        stage: completed ? 'completed' : 'llm',
        pass: 1, pass_limit: 1, attempt: 1, attempt_limit: 1,
        partial_text: completed ? 'exact enhanced result' : '',
        generated_tokens_approx: completed ? 3 : 0,
        elapsed_seconds: completed ? 1 : 0,
        live_tps: null, average_tps: completed ? 3 : null,
        result_available: completed, retryable: false,
      })
    }
    if (url.includes('/api/v1/llm/operations/enhance/') && url.includes('/result?')) {
      return jsonResponse({ original: 'exact original', enhanced: 'exact enhanced result' })
    }
    throw new Error(`unexpected queue-card request ${url}`)
  }

  const { useStore } = await loadStoreModuleFresh()
  const baseState = useStore.getState()
  t.after(() => {
    useStore.setState(baseState, true)
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
    if (originalNavigatorDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
    } else {
      delete globalThis.navigator
    }
  })

  useStore.setState({
    activeWorkspace: 'queue-card-project',
    generationMode: 'video',
    sidebarMode: 'director',
    sidebarOpen: true,
    startImage: null,
    imageRefs: [],
    modelOptions: null,
    params: { ...baseState.params, prompt: 'exact original', model_type: 'test-model' },
    startGeneration: async () => { generationCalls += 1 },
  })

  const pendingEnhance = useStore.getState().enhancePrompt()
  await waitForCondition(
    () => useStore.getState().enhanceQueueCard?.phase === 'queued',
    'queued Enhance card phase',
  )
  assert.equal(useStore.getState().enhanceQueueCard?.phase, 'queued')
  assert.equal(await pendingEnhance, true)
  assert.equal(enhancePosts, 1)
  assert.equal(generationCalls, 0)
  assert.equal(useStore.getState().params.prompt, 'exact original')
  assert.deepEqual(useStore.getState().enhanceQueueCard?.result, {
    original: 'exact original', enhanced: 'exact enhanced result',
  })
  assert.equal(useStore.getState().enhanceQueueCard?.resultApplied, false)
  const pendingRecovery = globalThis.localStorage.getItem('maestro:prompt-enhance-operations-v2')
  assert.notEqual(pendingRecovery, null)
  assert.equal(pendingRecovery.includes('exact original'), false)
  assert.equal(pendingRecovery.includes('exact enhanced result'), false)

  useStore.getState().setSidebarMode('studio')
  await waitForCondition(
    () => useStore.getState().params.prompt === 'exact enhanced result',
    'deferred Enhance result applied when Generate opened',
  )
  assert.equal(generationCalls, 0, 'opening Generate must not auto-generate')
  assert.equal(useStore.getState().enhanceQueueCard?.resultApplied, true)
  assert.equal(globalThis.localStorage.getItem('maestro:prompt-enhance-operations-v2'), null)

  assert.equal(await useStore.getState().useCompletedEnhanceAndGenerate(), true)
  assert.equal(generationCalls, 1)
  useStore.setState({ activeWorkspace: 'different-project' })
  assert.equal(useStore.getState().enhanceQueueCard, null)
})

test('Prompt Enhance UI opens Queue synchronously and keeps result text out of generic queue metadata', async () => {
  const [promptInput, mainContent, client] = await Promise.all([
    readFile(new URL('../src/components/Sidebar/PromptInput.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/MainContent/MainContent.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8'),
  ])
  assert.match(promptInput, /requestQueueView\(\)\s*\n\s*void enhancePrompt/)
  assert.doesNotMatch(promptInput, /line-clamp-2/)
  assert.match(mainContent, /data-prompt-enhance-queue-card/)
  assert.match(mainContent, /Use &amp; Generate/)
  assert.match(mainContent, /logicalJobKind === 'prompt_enhancement'/)
  assert.match(mainContent, /enhanceQueueCard\.phase === 'queued'/)
  assert.match(client, /generic `\/api\/v1\/queue` projection must stay content-free/)
})

test('Prompt Enhance fences project instances, keeps stale results inert, and cancels explicitly', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
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
  const documentTarget = new EventTarget()
  Object.defineProperty(documentTarget, 'visibilityState', { value: 'hidden', configurable: true })
  globalThis.document = documentTarget
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { locks: { request: (name, options, callback) => Promise.resolve(callback({ name })) } },
  })

  const projectInstances = {
    'project one': '1'.repeat(64),
    'project two': '2'.repeat(64),
  }
  const posts = []
  const deletes = []
  const statusChecks = []
  const editResult = deferred()
  let firstRequestId = ''
  let editRequestId = ''
  let editResultRequested = false
  const operationStatus = (requestId, overrides = {}) => ({
    request_id: requestId.replaceAll('-', ''),
    operation_kind: 'enhance',
    status: 'running', phase: 'generating', stage: 'llm',
    pass: 1, pass_limit: 1, attempt: 1, attempt_limit: 1,
    partial_text: 'scoped partial', generated_tokens_approx: 2,
    elapsed_seconds: 0.5, live_tps: 4, average_tps: null,
    result_available: false, retryable: false,
    ...overrides,
  })
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      const workspace = new URL(url, 'http://localhost').searchParams.get('workspace')
      return jsonResponse({
        models: [], guides: [], project_instance: projectInstances[workspace],
      })
    }
    if (url.endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({ operation_id: 'enhance-ready', status: 'ready', phase: 'ready', retryable: false }, 202)
    }
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      const body = JSON.parse(String(init.body))
      posts.push(body)
      assert.equal(body.project_instance, projectInstances[body.workspace])
      if (body.prompt === 'first prompt') firstRequestId = body.request_id
      if (body.prompt === 'successor prompt') {
        return jsonResponse(operationStatus(body.request_id, {
          status: 'completed', phase: 'completed', stage: 'completed',
          partial_text: 'successor enhanced', live_tps: null,
          average_tps: 3, result_available: true,
        }), 202)
      }
      if (body.prompt === 'edit fence') {
        editRequestId = body.request_id
        return jsonResponse(operationStatus(body.request_id, {
          status: 'completed', phase: 'completed', stage: 'completed',
          partial_text: 'stale enhancement', live_tps: null,
          average_tps: 3, result_available: true,
        }), 202)
      }
      return jsonResponse(operationStatus(body.request_id), 202)
    }
    if (url.includes('/api/v1/llm/operations/enhance/') && init.method === 'DELETE') {
      const requestId = decodeURIComponent(url.split('/enhance/')[1].split('?')[0])
      deletes.push({ requestId, url })
      return jsonResponse(operationStatus(requestId, {
        status: 'cancelled', phase: 'cancelled', stage: 'cancelled',
        partial_text: '', generated_tokens_approx: 0, elapsed_seconds: 0,
        live_tps: null, average_tps: null,
      }))
    }
    if (url.includes('/api/v1/llm/operations/enhance/') && url.includes('/result?')) {
      if (editRequestId && url.includes(editRequestId)) {
        editResultRequested = true
        return editResult.promise
      }
      if (firstRequestId && url.includes(firstRequestId)) {
        return jsonResponse({ original: 'first prompt', enhanced: 'reloaded first enhancement' })
      }
      return jsonResponse({ original: 'successor prompt', enhanced: 'successor enhanced' })
    }
    if (url.includes('/api/v1/llm/operations/enhance/')) {
      statusChecks.push(url)
      if (firstRequestId && url.includes(firstRequestId)) {
        return jsonResponse(operationStatus(firstRequestId, {
          status: 'completed', phase: 'completed', stage: 'completed',
          partial_text: 'reloaded first enhancement', live_tps: null,
          result_available: true,
        }))
      }
      throw new Error('hidden Prompt Enhance must not poll')
    }
    throw new Error(`unexpected scoped Enhance request ${url}`)
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
    if (originalNavigatorDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
    } else {
      delete globalThis.navigator
    }
  })
  useStore.setState({
    activeWorkspace: 'project one', generationMode: 'video',
    startImage: null, imageRefs: [], modelOptions: null,
    params: {
      ...baseState.params,
      prompt: 'first prompt', model_type: 'test-model', image_mode: 0,
    },
  })

  const first = useStore.getState().enhancePrompt()
  await waitForCondition(() => posts.length >= 1, 'first scoped Enhance POST')
  assert.equal(posts.length, 1)
  const persistenceKey = 'maestro:prompt-enhance-operations-v2'
  const persisted = JSON.parse(globalThis.localStorage.getItem(persistenceKey))
  assert.deepEqual(Object.keys(persisted).sort(), ['operations', 'schemaVersion'])
  assert.equal(persisted.schemaVersion, 2)
  assert.deepEqual(Object.keys(persisted.operations[0]).sort(), [
    'accountFingerprint', 'claimToken', 'projectInstance', 'requestId',
    'settingsFingerprint', 'storedAt', 'workspace',
  ])
  assert.equal(JSON.stringify(persisted).includes('first prompt'), false)
  assert.equal(persisted.operations[0].workspace, 'project one')
  assert.equal(JSON.stringify(persisted).includes('test-model'), false)
  persisted.operations[0].storedAt = Date.now() - (46 * 60 * 1000)
  globalThis.localStorage.setItem(persistenceKey, JSON.stringify(persisted))

  // Switching projects aborts only the browser wait. A successor owns all UI
  // adoption even if the first server operation continues independently.
  useStore.setState({ activeWorkspace: 'project two' })
  assert.equal(await first, false)
  assert.equal(deletes.length, 0)
  useStore.setState(state => ({
    params: { ...state.params, prompt: 'successor prompt' },
  }))
  assert.equal(await useStore.getState().enhancePrompt(), true)
  assert.equal(useStore.getState().params.prompt, 'successor enhanced')
  const afterSuccess = JSON.parse(globalThis.localStorage.getItem(persistenceKey))
  assert.equal(afterSuccess.operations.length, 1)
  assert.equal(afterSuccess.operations[0].workspace, 'project one')
  assert.ok(afterSuccess.operations[0].storedAt <= Date.now() - (45 * 60 * 1000))
  assert.equal(statusChecks.length, 0)

  useStore.getState().setParam('prompt', 'edit fence')
  const staleEdit = useStore.getState().enhancePrompt()
  await waitForCondition(() => editResultRequested, 'edit-fenced Enhance result request')
  assert.equal(editResultRequested, true)
  useStore.getState().setParam('prompt', 'newer same-project edit')
  editResult.resolve(jsonResponse({ original: 'edit fence', enhanced: 'must stay inert' }))
  assert.equal(await staleEdit, false)
  assert.equal(useStore.getState().params.prompt, 'newer same-project edit')

  useStore.getState().setParam('prompt', 'cancel me')
  const cancelWait = useStore.getState().enhancePrompt()
  await waitForCondition(() => posts.length >= 4, 'cancellable Enhance admission')
  assert.equal(useStore.getState().enhanceStatus.partial_text, 'scoped partial')
  assert.equal(useStore.getState().enhanceStatus.live_tps, 4)
  await useStore.getState().cancelEnhancePrompt()
  assert.equal(await cancelWait, false)
  assert.equal(deletes.length, 1)
  assert.match(deletes[0].url, /workspace=project\+two/)
  assert.equal(useStore.getState().isEnhancing, false)
  assert.equal(useStore.getState().enhanceStatus, null)
  assert.equal(JSON.parse(globalThis.localStorage.getItem(persistenceKey)).operations[0].workspace, 'project one')

  Object.defineProperty(documentTarget, 'visibilityState', { value: 'visible', configurable: true })
  useStore.setState({ activeWorkspace: 'project one' })
  useStore.getState().setParam('prompt', 'first prompt')
  assert.equal(await useStore.getState().resumeEnhancePrompt(), true)
  assert.equal(posts.length, 4, 'reload recovery must not issue a duplicate Enhance POST')
  assert.equal(useStore.getState().params.prompt, 'reloaded first enhancement')
  assert.equal(globalThis.localStorage.getItem(persistenceKey), null)
})

test('Prompt Enhance image identity fences reject path and equal-count reference swaps fresh and after resume', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
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
  let visibility = 'visible'
  const documentTarget = new EventTarget()
  Object.defineProperty(documentTarget, 'visibilityState', { get: () => visibility })
  globalThis.document = documentTarget
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { locks: { request: (name, options, callback) => Promise.resolve(callback({ name })) } },
  })

  const instance = 'd'.repeat(64)
  const requestPrompts = new Map()
  const resultWaiters = new Map([
    ['fresh path identity', deferred()],
    ['fresh reference identity', deferred()],
  ])
  const resultRequested = new Set()
  const postedPrompts = []
  const operationStatus = (requestId, overrides = {}) => ({
    request_id: requestId.replaceAll('-', ''), operation_kind: 'enhance',
    status: 'running', phase: 'generating', stage: 'llm', pass: 1, pass_limit: 1,
    attempt: 1, attempt_limit: 1, partial_text: '', generated_tokens_approx: 0,
    elapsed_seconds: 0, live_tps: null, average_tps: null,
    result_available: false, retryable: false, ...overrides,
  })
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      return jsonResponse({ models: [], guides: [], project_instance: instance })
    }
    if (url.endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({ operation_id: 'image-fence-ready', status: 'ready', phase: 'ready', retryable: false }, 202)
    }
    if (url.endsWith('/api/v1/upload')) {
      return jsonResponse({ filename: 'reference.png', path: '/private/uploaded-reference.png', url: '/reference' })
    }
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      const body = JSON.parse(String(init.body))
      requestPrompts.set(body.request_id, body.prompt)
      postedPrompts.push(body.prompt)
      const fresh = body.prompt.startsWith('fresh ')
      return jsonResponse(operationStatus(body.request_id, fresh ? {
        status: 'completed', phase: 'completed', stage: 'completed', result_available: true,
      } : {}), 202)
    }
    if (url.includes('/api/v1/llm/operations/enhance/') && url.includes('/result?')) {
      const requestId = decodeURIComponent(url.split('/enhance/')[1].split('/result?')[0])
      const prompt = requestPrompts.get(requestId)
      const waiter = resultWaiters.get(prompt)
      if (waiter) {
        resultRequested.add(prompt)
        return waiter.promise
      }
      return jsonResponse({ original: prompt, enhanced: `${prompt} enhanced` })
    }
    if (url.includes('/api/v1/llm/operations/enhance/')) {
      const requestId = decodeURIComponent(url.split('/enhance/')[1].split('?')[0])
      return jsonResponse(operationStatus(requestId, {
        status: 'completed', phase: 'completed', stage: 'completed', result_available: true,
      }))
    }
    throw new Error(`unexpected image-fence request ${url}`)
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
    if (originalNavigatorDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
    } else {
      delete globalThis.navigator
    }
  })
  const file = contents => new File([contents], 'same-reference.png', {
    type: 'image/png', lastModified: 1234,
  })
  const referenceA = file('AAAA')
  const referenceB = file('BBBB')
  assert.equal(referenceA.name, referenceB.name)
  assert.equal(referenceA.size, referenceB.size)
  assert.equal(referenceA.type, referenceB.type)
  assert.equal(referenceA.lastModified, referenceB.lastModified)

  useStore.setState({
    activeWorkspace: 'fresh-path', generationMode: 'video', startImage: null,
    imageRefs: [], modelOptions: null,
    params: {
      ...baseState.params, prompt: 'fresh path identity', model_type: 'test-model',
      image_mode: 0, image_start: '/private/original-start.png',
    },
  })
  const freshPath = useStore.getState().enhancePrompt()
  await waitForCondition(
    () => resultRequested.has('fresh path identity'),
    'fresh path-fence result request',
  )
  useStore.getState().setParam('image_start', '/private/replacement-start.png')
  resultWaiters.get('fresh path identity').resolve(jsonResponse({
    original: 'fresh path identity', enhanced: 'stale path result',
  }))
  assert.equal(await freshPath, false)
  assert.equal(useStore.getState().params.prompt, 'fresh path identity')

  useStore.setState(state => ({
    activeWorkspace: 'fresh-refs', generationMode: 'image', imageRefs: [referenceA],
    params: {
      ...state.params, prompt: 'fresh reference identity', image_start: undefined,
      image_refs: undefined,
    },
  }))
  const freshRefs = useStore.getState().enhancePrompt()
  await waitForCondition(
    () => resultRequested.has('fresh reference identity'),
    'fresh reference-fence result request',
  )
  useStore.setState({ imageRefs: [referenceB] })
  resultWaiters.get('fresh reference identity').resolve(jsonResponse({
    original: 'fresh reference identity', enhanced: 'stale reference result',
  }))
  assert.equal(await freshRefs, false)
  assert.equal(useStore.getState().params.prompt, 'fresh reference identity')

  visibility = 'hidden'
  useStore.setState(state => ({
    activeWorkspace: 'resume-path', generationMode: 'video', imageRefs: [],
    params: {
      ...state.params, prompt: 'resume path identity', image_start: '/private/resume-original.png',
      image_refs: undefined,
    },
  }))
  const waitingPath = useStore.getState().enhancePrompt()
  await waitForCondition(
    () => postedPrompts.includes('resume path identity'),
    'resumable path-fence admission',
  )
  const persistedPath = globalThis.localStorage.getItem('maestro:prompt-enhance-operations-v2')
  assert.equal(persistedPath.includes('/private/resume-original.png'), false)
  assert.match(
    JSON.parse(persistedPath).operations[0].settingsFingerprint,
    /^[0-9a-f]{64}$/,
  )
  const privacyClaim = globalThis.sessionStorage.getItem('maestro:prompt-enhance-fingerprint-claim-v1')
  assert.match(JSON.parse(privacyClaim).salt, /^[0-9a-f]{64}$/)
  assert.match(JSON.parse(privacyClaim).token, /^[0-9a-f]{64}$/)
  assert.equal(privacyClaim.includes('/private/'), false)
  useStore.setState({ activeWorkspace: 'parking' })
  assert.equal(await waitingPath, false)
  useStore.getState().setParam('image_start', '/private/resume-replacement.png')
  useStore.setState({ activeWorkspace: 'resume-path' })
  visibility = 'visible'
  assert.equal(await useStore.getState().resumeEnhancePrompt(), false)
  assert.equal(useStore.getState().params.prompt, 'resume path identity')

  visibility = 'hidden'
  useStore.setState(state => ({
    activeWorkspace: 'resume-refs', generationMode: 'image', imageRefs: [referenceA],
    params: {
      ...state.params, prompt: 'resume reference identity', image_start: undefined,
      image_refs: ['/private/reference-a.png'],
    },
  }))
  const waitingRefs = useStore.getState().enhancePrompt()
  await waitForCondition(
    () => postedPrompts.includes('resume reference identity'),
    'resumable reference-fence admission',
  )
  const persistedRefs = globalThis.localStorage.getItem('maestro:prompt-enhance-operations-v2')
  assert.equal(persistedRefs.includes('same-reference.png'), false)
  assert.equal(persistedRefs.includes('AAAA'), false)
  assert.equal(persistedRefs.includes('BBBB'), false)
  useStore.setState({ activeWorkspace: 'parking' })
  assert.equal(await waitingRefs, false)
  useStore.setState({ activeWorkspace: 'resume-refs', imageRefs: [referenceB] })
  visibility = 'visible'
  assert.equal(await useStore.getState().resumeEnhancePrompt(), false)
  assert.equal(useStore.getState().params.prompt, 'resume reference identity')
})

test('Prompt Enhance Web Lock claim preserves reload and rotates copied or unclaimable salts', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
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
  globalThis.document = Object.assign(new EventTarget(), { visibilityState: 'visible' })
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  globalThis.fetch = async input => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      return jsonResponse({ models: [], guides: [], project_instance: null })
    }
    throw new Error(`unexpected salt-scope request ${url}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
    if (originalNavigatorDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
    } else {
      delete globalThis.navigator
    }
  })

  class ExclusiveLocksFake {
    held = new Set()
    request(name, options, callback) {
      assert.equal(options.mode, 'exclusive')
      if (name === 'maestro-prompt-enhance-ledger-v2') {
        assert.ok(options.signal instanceof AbortSignal)
        return Promise.resolve(callback({ name }))
      }
      assert.equal(options.ifAvailable, true)
      if (this.held.has(name)) return Promise.resolve(callback(null))
      this.held.add(name)
      return Promise.resolve(callback({ name }))
    }
    releaseAll() { this.held.clear() }
  }
  const claimKey = 'maestro:prompt-enhance-fingerprint-claim-v1'
  const runRealm = async label => {
    const { useStore } = await loadStoreModuleFresh()
    const baseState = useStore.getState()
    useStore.setState({
      activeWorkspace: `salt-${label}`,
      startImage: null,
      imageRefs: [],
      params: { ...baseState.params, prompt: `salt ${label}`, model_type: 'test-model' },
    })
    assert.equal(await useStore.getState().enhancePrompt(), false)
    const claim = JSON.parse(globalThis.sessionStorage.getItem(claimKey))
    assert.deepEqual(Object.keys(claim).sort(), ['salt', 'schemaVersion', 'token'])
    assert.equal(claim.schemaVersion, 1)
    assert.match(claim.token, /^[0-9a-f]{64}$/)
    assert.match(claim.salt, /^[0-9a-f]{64}$/)
    assert.equal(JSON.stringify(claim).includes('/private/'), false)
    return claim
  }

  const locks = new ExclusiveLocksFake()
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true, value: { locks },
  })
  const originalClaim = await runRealm('original')

  // A reload begins after its predecessor realm releases the token lock and
  // therefore retains the exact token and salt.
  locks.releaseAll()
  const reloadClaim = await runRealm('reload')
  assert.deepEqual(reloadClaim, originalClaim)

  // A duplicated/opener-copied tab sees the predecessor lock held. This is
  // fail-closed even if Navigation Timing would have called it a reload.
  const duplicateSession = new StorageFake()
  duplicateSession.setItem(claimKey, JSON.stringify(reloadClaim))
  globalThis.sessionStorage = duplicateSession
  const duplicateClaim = await runRealm('duplicate')
  assert.notEqual(duplicateClaim.token, reloadClaim.token)
  assert.notEqual(duplicateClaim.salt, reloadClaim.salt)

  const copiedClaim = JSON.stringify(duplicateClaim)
  globalThis.sessionStorage = new StorageFake()
  globalThis.sessionStorage.setItem(claimKey, copiedClaim)
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {} })
  const missingLocksClaim = await runRealm('missing-locks')
  assert.notDeepEqual(missingLocksClaim, duplicateClaim)

  globalThis.sessionStorage = new StorageFake()
  globalThis.sessionStorage.setItem(claimKey, copiedClaim)
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { locks: { request: async () => { throw new Error('locks unavailable') } } },
  })
  const erroredLocksClaim = await runRealm('errored-locks')
  assert.notDeepEqual(erroredLocksClaim, duplicateClaim)
})

test('Prompt Enhance duplicate rejection preserves the source-owned recovery record', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  class ExclusiveLocksFake {
    held = new Set()
    request(name, options, callback) {
      assert.equal(options.mode, 'exclusive')
      if (name === 'maestro-prompt-enhance-ledger-v2') {
        assert.ok(options.signal instanceof AbortSignal)
        return Promise.resolve(callback({ name }))
      }
      assert.equal(options.ifAvailable, true)
      if (this.held.has(name)) return Promise.resolve(callback(null))
      this.held.add(name)
      return Promise.resolve(callback({ name }))
    }
  }
  const alerts = []
  let visibility = 'hidden'
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval,
    alert(message) { alerts.push(String(message)) },
    location: { hostname: 'localhost' },
  })
  const documentTarget = new EventTarget()
  Object.defineProperty(documentTarget, 'visibilityState', { get: () => visibility })
  globalThis.document = documentTarget
  globalThis.localStorage = new StorageFake()
  const sourceSession = new StorageFake()
  globalThis.sessionStorage = sourceSession
  const locks = new ExclusiveLocksFake()
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true, value: { locks },
  })
  const projectInstance = 'f'.repeat(64)
  let requestId = ''
  let statusRequests = 0
  let resultRequests = 0
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      return jsonResponse({ models: [], guides: [], project_instance: projectInstance })
    }
    if (url.endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({ operation_id: 'owner-ready', status: 'ready', phase: 'ready', retryable: false }, 202)
    }
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      const body = JSON.parse(String(init.body))
      requestId = body.request_id
      return jsonResponse({
        request_id: requestId.replaceAll('-', ''), operation_kind: 'enhance',
        status: 'running', phase: 'generating', stage: 'llm', pass: 1, pass_limit: 1,
        attempt: 1, attempt_limit: 1, partial_text: 'source partial',
        generated_tokens_approx: 1, elapsed_seconds: 1, live_tps: 1,
        average_tps: null, result_available: false, retryable: false,
      }, 202)
    }
    if (url.includes('/api/v1/llm/operations/enhance/') && url.includes('/result?')) {
      resultRequests += 1
      return jsonResponse({ original: 'source-owned prompt', enhanced: 'source-owned result' })
    }
    if (url.includes('/api/v1/llm/operations/enhance/')) {
      statusRequests += 1
      return jsonResponse({
        request_id: requestId.replaceAll('-', ''), operation_kind: 'enhance',
        status: 'completed', phase: 'completed', stage: 'completed', pass: 1, pass_limit: 1,
        attempt: 1, attempt_limit: 1, partial_text: 'source-owned result',
        generated_tokens_approx: 2, elapsed_seconds: 1, live_tps: null,
        average_tps: 2, result_available: true, retryable: false,
      })
    }
    throw new Error(`unexpected owner-fence request ${url}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
    if (originalNavigatorDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
    } else {
      delete globalThis.navigator
    }
  })

  const configureRealm = useStore => {
    const baseState = useStore.getState()
    useStore.setState({
      activeWorkspace: 'source-owned-workspace', generationMode: 'video',
      startImage: null, imageRefs: [], modelOptions: null,
      params: {
        ...baseState.params, prompt: 'source-owned prompt', model_type: 'test-model',
        image_mode: 0, image_start: undefined, image_refs: undefined,
      },
    })
  }
  const ledgerKey = 'maestro:prompt-enhance-operations-v2'
  const claimKey = 'maestro:prompt-enhance-fingerprint-claim-v1'
  const { useStore: sourceStore } = await loadStoreModuleFresh()
  configureRealm(sourceStore)
  const sourceWait = sourceStore.getState().enhancePrompt()
  await waitForCondition(
    () => requestId !== '' && globalThis.localStorage.getItem(ledgerKey) !== null,
    'source-owned recovery admission',
  )
  sourceStore.setState({ activeWorkspace: 'source-parking' })
  assert.equal(await sourceWait, false)
  const sourceClaim = JSON.parse(sourceSession.getItem(claimKey))
  const sourceLedger = globalThis.localStorage.getItem(ledgerKey)
  const sourceRecord = JSON.parse(sourceLedger).operations[0]
  assert.equal(sourceRecord.claimToken, sourceClaim.token)
  assert.equal(sourceLedger.includes('source-owned prompt'), false)

  const copiedSession = new StorageFake()
  copiedSession.setItem(claimKey, JSON.stringify(sourceClaim))
  globalThis.sessionStorage = copiedSession
  const { useStore: duplicateStore } = await loadStoreModuleFresh()
  configureRealm(duplicateStore)
  assert.equal(await duplicateStore.getState().resumeEnhancePrompt(), false)
  assert.equal(globalThis.localStorage.getItem(ledgerKey), sourceLedger)
  assert.equal(statusRequests, 0)
  assert.equal(resultRequests, 0)
  assert.match(alerts.at(-1), /could not exclusively reclaim/)

  for (const unavailableLocks of [
    {},
    { locks: { request: async () => { throw new Error('locks unavailable') } } },
  ]) {
    const unavailableSession = new StorageFake()
    unavailableSession.setItem(claimKey, JSON.stringify(sourceClaim))
    globalThis.sessionStorage = unavailableSession
    Object.defineProperty(globalThis, 'navigator', {
      configurable: true, value: unavailableLocks,
    })
    const { useStore: unavailableStore } = await loadStoreModuleFresh()
    configureRealm(unavailableStore)
    assert.equal(await unavailableStore.getState().resumeEnhancePrompt(), false)
    assert.equal(globalThis.localStorage.getItem(ledgerKey), sourceLedger)
    assert.match(alerts.at(-1), /could not exclusively reclaim/)
  }

  visibility = 'visible'
  globalThis.sessionStorage = sourceSession
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true, value: { locks },
  })
  sourceStore.setState({ activeWorkspace: 'source-owned-workspace' })
  assert.equal(await sourceStore.getState().resumeEnhancePrompt(), true)
  assert.equal(sourceStore.getState().params.prompt, 'source-owned result')
  assert.equal(statusRequests, 1)
  assert.equal(resultRequests, 1)
  assert.equal(globalThis.localStorage.getItem(ledgerKey), null)
})

test('Prompt Enhance delayed reload claim cannot steal a manual successor', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  class DelayedLocksFake {
    held = new Set()
    delayed = false
    waiters = []
    request(name, options, callback) {
      assert.equal(options.mode, 'exclusive')
      if (name === 'maestro-prompt-enhance-ledger-v2') {
        assert.ok(options.signal instanceof AbortSignal)
        return Promise.resolve(callback({ name }))
      }
      assert.equal(options.ifAvailable, true)
      if (this.held.has(name)) return Promise.resolve(callback(null))
      if (!this.delayed) {
        this.held.add(name)
        return Promise.resolve(callback({ name }))
      }
      return new Promise(resolve => this.waiters.push({ name, callback, resolve }))
    }
    releaseAll() { this.held.clear() }
    delayNext() { this.delayed = true }
    grantAll() {
      this.delayed = false
      for (const waiter of this.waiters.splice(0)) {
        this.held.add(waiter.name)
        waiter.resolve(waiter.callback({ name: waiter.name }))
      }
    }
  }
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: 'localhost' },
  })
  let visibility = 'hidden'
  const documentTarget = new EventTarget()
  Object.defineProperty(documentTarget, 'visibilityState', { get: () => visibility })
  globalThis.document = documentTarget
  globalThis.localStorage = new StorageFake()
  const sourceSession = new StorageFake()
  globalThis.sessionStorage = sourceSession
  const locks = new DelayedLocksFake()
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true, value: { locks },
  })
  const projectInstance = 'e'.repeat(64)
  const requests = new Map()
  let oldStatusRequests = 0
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      return jsonResponse({ models: [], guides: [], project_instance: projectInstance })
    }
    if (url.endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({ operation_id: 'race-ready', status: 'ready', phase: 'ready', retryable: false }, 202)
    }
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      const body = JSON.parse(String(init.body))
      requests.set(body.request_id, body.prompt)
      const completed = body.prompt === 'manual successor'
      return jsonResponse({
        request_id: body.request_id.replaceAll('-', ''), operation_kind: 'enhance',
        status: completed ? 'completed' : 'running',
        phase: completed ? 'completed' : 'generating',
        stage: completed ? 'completed' : 'llm', pass: 1, pass_limit: 1,
        attempt: 1, attempt_limit: 1, partial_text: completed ? 'manual result' : 'old partial',
        generated_tokens_approx: 1, elapsed_seconds: 1, live_tps: null,
        average_tps: 1, result_available: completed, retryable: false,
      }, 202)
    }
    if (url.includes('/api/v1/llm/operations/enhance/') && url.includes('/result?')) {
      const id = decodeURIComponent(url.split('/enhance/')[1].split('/')[0])
      const original = requests.get(id)
      return jsonResponse({ original, enhanced: original === 'manual successor' ? 'manual result' : 'old result' })
    }
    if (url.includes('/api/v1/llm/operations/enhance/')) {
      oldStatusRequests += 1
      throw new Error('delayed recovery must not poll after a successor starts')
    }
    throw new Error(`unexpected delayed-claim request ${url}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
    if (originalNavigatorDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
    } else {
      delete globalThis.navigator
    }
  })
  const configure = (useStore, prompt) => {
    const baseState = useStore.getState()
    useStore.setState({
      activeWorkspace: 'delayed-claim-workspace', generationMode: 'video',
      startImage: null, imageRefs: [], modelOptions: null,
      params: {
        ...baseState.params, prompt, model_type: 'test-model', image_mode: 0,
        image_start: undefined, image_refs: undefined,
      },
    })
  }
  const ledgerKey = 'maestro:prompt-enhance-operations-v2'
  const claimKey = 'maestro:prompt-enhance-fingerprint-claim-v1'
  const { useStore: sourceStore } = await loadStoreModuleFresh()
  configure(sourceStore, 'delayed recovery')
  const sourceWait = sourceStore.getState().enhancePrompt()
  await waitForCondition(
    () => requests.size === 1 && globalThis.localStorage.getItem(ledgerKey) !== null,
    'delayed-claim source admission',
  )
  sourceStore.setState({ activeWorkspace: 'delayed-source-parking' })
  assert.equal(await sourceWait, false)
  const sourceClaim = sourceSession.getItem(claimKey)
  const sourceLedger = globalThis.localStorage.getItem(ledgerKey)

  locks.releaseAll()
  locks.delayNext()
  globalThis.sessionStorage = new StorageFake()
  globalThis.sessionStorage.setItem(claimKey, sourceClaim)
  visibility = 'visible'
  const { useStore: reloadStore } = await loadStoreModuleFresh()
  configure(reloadStore, 'delayed recovery')
  const delayedResume = reloadStore.getState().resumeEnhancePrompt()
  await waitForCondition(() => locks.waiters.length === 1, 'delayed recovery lock request')
  reloadStore.getState().setParam('prompt', 'manual successor')
  const successor = reloadStore.getState().enhancePrompt()
  assert.equal(reloadStore.getState().isEnhancing, true)
  locks.grantAll()
  assert.equal(await delayedResume, false)
  assert.equal(await successor, true)
  assert.equal(reloadStore.getState().params.prompt, 'manual result')
  assert.equal(oldStatusRequests, 0)
  const remaining = JSON.parse(globalThis.localStorage.getItem(ledgerKey)).operations
  assert.equal(remaining.length, 1)
  assert.equal(remaining[0].requestId, JSON.parse(sourceLedger).operations[0].requestId)
})

test('Prompt Enhance persistence failures preserve foreign ledgers and disable reload recovery', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalDateNow = Date.now
  const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  class FailingStorage extends StorageFake {
    setItem(key, value) {
      if (key === 'maestro:prompt-enhance-operations-v2') throw new Error('quota full')
      super.setItem(key, value)
    }
  }
  const alerts = []
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval,
    alert(message) { alerts.push(String(message)) },
    location: { hostname: 'localhost' },
  })
  globalThis.document = Object.assign(new EventTarget(), { visibilityState: 'visible' })
  const ledgerKey = 'maestro:prompt-enhance-operations-v2'
  const fullStorage = new StorageFake()
  const foreignOperations = Array.from({ length: 8 }, (_, index) => ({
    requestId: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
    workspace: `foreign-${index}`,
    projectInstance: 'a'.repeat(64),
    accountFingerprint: 'b'.repeat(16),
    claimToken: String(index + 1).repeat(64).slice(0, 64),
    settingsFingerprint: 'c'.repeat(64),
    storedAt: originalDateNow(),
  }))
  fullStorage.setItem(ledgerKey, JSON.stringify({ schemaVersion: 2, operations: foreignOperations }))
  globalThis.localStorage = fullStorage
  globalThis.sessionStorage = new StorageFake()
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { locks: { request: (name, options, callback) => Promise.resolve(callback({ name })) } },
  })
  const fullLedger = fullStorage.getItem(ledgerKey)
  const projectInstance = 'd'.repeat(64)
  const requestPrompts = new Map()
  let forceTimeout = false
  let timeoutTick = 0
  Date.now = () => forceTimeout
    ? originalDateNow() + (++timeoutTick * 10_000_000)
    : originalDateNow()
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      return jsonResponse({ models: [], guides: [], project_instance: projectInstance })
    }
    if (url.endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({ operation_id: 'persistence-ready', status: 'ready', phase: 'ready', retryable: false }, 202)
    }
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      const body = JSON.parse(String(init.body))
      requestPrompts.set(body.request_id, body.prompt)
      const completed = body.prompt === 'full ledger current wait'
      if (!completed) forceTimeout = true
      return jsonResponse({
        request_id: body.request_id.replaceAll('-', ''), operation_kind: 'enhance',
        status: completed ? 'completed' : 'running',
        phase: completed ? 'completed' : 'generating', stage: completed ? 'completed' : 'llm',
        pass: 1, pass_limit: 1, attempt: 1, attempt_limit: 1,
        partial_text: completed ? 'current wait result' : 'still running',
        generated_tokens_approx: 1, elapsed_seconds: 1, live_tps: null,
        average_tps: 1, result_available: completed, retryable: false,
      }, 202)
    }
    if (url.includes('/api/v1/llm/operations/enhance/') && url.includes('/result?')) {
      const id = decodeURIComponent(url.split('/enhance/')[1].split('/')[0])
      const original = requestPrompts.get(id)
      return jsonResponse({ original, enhanced: 'current wait result' })
    }
    throw new Error(`unexpected persistence request ${url}`)
  }
  t.after(() => {
    Date.now = originalDateNow
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
    if (originalNavigatorDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
    } else {
      delete globalThis.navigator
    }
  })
  const { useStore } = await loadStoreModuleFresh()
  const configure = prompt => {
    const state = useStore.getState()
    useStore.setState({
      activeWorkspace: 'persistence-workspace', generationMode: 'video',
      startImage: null, imageRefs: [], modelOptions: null,
      params: {
        ...state.params, prompt, model_type: 'test-model', image_mode: 0,
        image_start: undefined, image_refs: undefined,
      },
    })
  }
  configure('full ledger current wait')
  assert.equal(await useStore.getState().enhancePrompt(), true)
  assert.equal(useStore.getState().params.prompt, 'current wait result')
  assert.equal(fullStorage.getItem(ledgerKey), fullLedger)

  forceTimeout = false
  timeoutTick = 0
  globalThis.localStorage = new FailingStorage()
  configure('write failure timeout')
  const originalConsoleError = console.error
  console.error = () => {}
  try {
    assert.equal(await useStore.getState().enhancePrompt(), false)
  } finally {
    console.error = originalConsoleError
  }
  assert.equal(globalThis.localStorage.getItem(ledgerKey), null)
  assert.match(alerts.at(-1), /reloading will not resume this request/)
})

test('Prompt Enhance global ledger lock preserves concurrent two-owner appends', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
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
  globalThis.document = Object.assign(new EventTarget(), { visibilityState: 'visible' })
  globalThis.localStorage = new StorageFake()
  const ownerASession = new StorageFake()
  globalThis.sessionStorage = ownerASession
  const locks = new QueuedEnhanceLocksFake()
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true, value: { locks },
  })
  const projectInstance = '9'.repeat(64)
  const postWaiters = new Map([
    ['atomic owner a', deferred()],
    ['atomic owner b', deferred()],
  ])
  const posts = []
  const requestPrompts = new Map()
  const completedStatus = (requestId, prompt) => ({
    request_id: requestId.replaceAll('-', ''), operation_kind: 'enhance',
    status: 'completed', phase: 'completed', stage: 'completed', pass: 1, pass_limit: 1,
    attempt: 1, attempt_limit: 1, partial_text: `${prompt} result`,
    generated_tokens_approx: 1, elapsed_seconds: 1, live_tps: null,
    average_tps: 1, result_available: true, retryable: false,
  })
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      return jsonResponse({ models: [], guides: [], project_instance: projectInstance })
    }
    if (url.endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({ operation_id: 'atomic-ready', status: 'ready', phase: 'ready', retryable: false }, 202)
    }
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      const body = JSON.parse(String(init.body))
      posts.push(body.prompt)
      requestPrompts.set(body.request_id, body.prompt)
      return postWaiters.get(body.prompt).promise
    }
    if (url.includes('/api/v1/llm/operations/enhance/') && url.includes('/result?')) {
      const id = decodeURIComponent(url.split('/enhance/')[1].split('/')[0])
      const prompt = requestPrompts.get(id)
      return jsonResponse({ original: prompt, enhanced: `${prompt} result` })
    }
    throw new Error(`unexpected atomic-append request ${url}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
    if (originalNavigatorDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
    } else {
      delete globalThis.navigator
    }
  })
  const configure = (useStore, workspace, prompt) => {
    const state = useStore.getState()
    useStore.setState({
      activeWorkspace: workspace, generationMode: 'video', startImage: null,
      imageRefs: [], modelOptions: null,
      params: {
        ...state.params, prompt, model_type: 'test-model', image_mode: 0,
        image_start: undefined, image_refs: undefined,
      },
    })
  }
  const ledgerKey = 'maestro:prompt-enhance-operations-v2'
  const { useStore: ownerA } = await loadStoreModuleFresh()
  configure(ownerA, 'atomic-a', 'atomic owner a')
  locks.blockNext()
  const pendingA = ownerA.getState().enhancePrompt()
  await waitForCondition(() => locks.blockedLedger !== null, 'first owner ledger lock')

  globalThis.sessionStorage = new StorageFake()
  const { useStore: ownerB } = await loadStoreModuleFresh()
  configure(ownerB, 'atomic-b', 'atomic owner b')
  const pendingB = ownerB.getState().enhancePrompt()
  await waitForCondition(() => locks.ledgerQueue.length === 1, 'second owner queued append')
  assert.equal(posts.length, 0)
  locks.releaseBlocked()
  await waitForCondition(() => posts.length === 2, 'serialized owner POSTs')
  const stored = JSON.parse(globalThis.localStorage.getItem(ledgerKey)).operations
  assert.deepEqual(new Set(stored.map(item => item.workspace)), new Set(['atomic-a', 'atomic-b']))

  for (const [prompt, waiter] of postWaiters) {
    const requestId = [...requestPrompts].find(([, value]) => value === prompt)[0]
    waiter.resolve(jsonResponse(completedStatus(requestId, prompt), 202))
  }
  assert.equal(await pendingA, true)
  assert.equal(await pendingB, true)
  assert.equal(globalThis.localStorage.getItem(ledgerKey), null)
})

test('Prompt Enhance global ledger lock serializes append ahead of another owner removal', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
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
  let visibility = 'hidden'
  const documentTarget = new EventTarget()
  Object.defineProperty(documentTarget, 'visibilityState', { get: () => visibility })
  globalThis.document = documentTarget
  globalThis.localStorage = new StorageFake()
  const ownerASession = new StorageFake()
  globalThis.sessionStorage = ownerASession
  const locks = new QueuedEnhanceLocksFake()
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true, value: { locks },
  })
  const projectInstance = '8'.repeat(64)
  const requests = new Map()
  let appendPosts = 0
  const runningStatus = (requestId, prompt) => ({
    request_id: requestId.replaceAll('-', ''), operation_kind: 'enhance',
    status: 'running', phase: 'generating', stage: 'llm', pass: 1, pass_limit: 1,
    attempt: 1, attempt_limit: 1, partial_text: `${prompt} partial`,
    generated_tokens_approx: 1, elapsed_seconds: 1, live_tps: 1,
    average_tps: null, result_available: false, retryable: false,
  })
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      return jsonResponse({ models: [], guides: [], project_instance: projectInstance })
    }
    if (url.endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({ operation_id: 'atomic-remove-ready', status: 'ready', phase: 'ready', retryable: false }, 202)
    }
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      const body = JSON.parse(String(init.body))
      requests.set(body.prompt, body.request_id)
      if (body.prompt === 'append owner') appendPosts += 1
      return jsonResponse(runningStatus(body.request_id, body.prompt), 202)
    }
    if (init.method === 'DELETE' && url.includes('/api/v1/llm/operations/enhance/')) {
      const id = decodeURIComponent(url.split('/enhance/')[1].split('?')[0])
      return jsonResponse({
        ...runningStatus(id, 'remove owner'), status: 'cancelled', phase: 'cancelled',
        stage: 'cancelled', partial_text: '', generated_tokens_approx: 0,
        elapsed_seconds: 0, live_tps: null,
      })
    }
    throw new Error(`unexpected append-remove request ${url}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
    if (originalNavigatorDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
    } else {
      delete globalThis.navigator
    }
  })
  const configure = (useStore, workspace, prompt) => {
    const state = useStore.getState()
    useStore.setState({
      activeWorkspace: workspace, generationMode: 'video', startImage: null,
      imageRefs: [], modelOptions: null,
      params: {
        ...state.params, prompt, model_type: 'test-model', image_mode: 0,
        image_start: undefined, image_refs: undefined,
      },
    })
  }
  const ledgerKey = 'maestro:prompt-enhance-operations-v2'
  const { useStore: ownerA } = await loadStoreModuleFresh()
  configure(ownerA, 'remove-workspace', 'remove owner')
  const seed = ownerA.getState().enhancePrompt()
  await waitForCondition(
    () => requests.has('remove owner') && globalThis.localStorage.getItem(ledgerKey) !== null,
    'remove-owner seed',
  )
  ownerA.setState({ activeWorkspace: 'remove-parking' })
  assert.equal(await seed, false)

  globalThis.sessionStorage = new StorageFake()
  const { useStore: ownerB } = await loadStoreModuleFresh()
  configure(ownerB, 'append-workspace', 'append owner')
  locks.blockNext()
  const append = ownerB.getState().enhancePrompt()
  await waitForCondition(() => locks.blockedLedger !== null, 'blocked append mutation')
  ownerA.setState({ activeWorkspace: 'remove-workspace' })
  const remove = ownerA.getState().cancelEnhancePrompt()
  await waitForCondition(() => locks.ledgerQueue.length === 1, 'queued owner removal')
  locks.releaseBlocked()
  await remove
  await waitForCondition(() => appendPosts === 1, 'append POST after serialized storage')
  ownerB.setState({ activeWorkspace: 'append-parking' })
  assert.equal(await append, false)
  const remaining = JSON.parse(globalThis.localStorage.getItem(ledgerKey)).operations
  assert.equal(remaining.length, 1)
  assert.equal(remaining[0].workspace, 'append-workspace')
})

test('Prompt Enhance cancel during queued pre-POST persistence stays entirely local', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
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
  globalThis.document = Object.assign(new EventTarget(), { visibilityState: 'visible' })
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  const locks = new QueuedEnhanceLocksFake()
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true, value: { locks },
  })
  const foreignRelease = deferred()
  const foreignController = new AbortController()
  const foreignLedger = locks.request(
    'maestro-prompt-enhance-ledger-v2',
    { mode: 'exclusive', signal: foreignController.signal },
    () => foreignRelease.promise,
  )
  let posts = 0
  let deletes = 0
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      return jsonResponse({ models: [], guides: [], project_instance: '7'.repeat(64) })
    }
    if (url.endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({ operation_id: 'pre-post-ready', status: 'ready', phase: 'ready', retryable: false }, 202)
    }
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      posts += 1
      throw new Error('pre-POST cancellation must not submit')
    }
    if (init.method === 'DELETE') {
      deletes += 1
      throw new Error('pre-POST cancellation must not delete')
    }
    throw new Error(`unexpected pre-POST cancellation request ${url}`)
  }
  t.after(() => {
    foreignController.abort()
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
    if (originalNavigatorDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
    } else {
      delete globalThis.navigator
    }
  })
  const { useStore } = await loadStoreModuleFresh()
  const baseState = useStore.getState()
  useStore.setState({
    activeWorkspace: 'pre-post-cancel', generationMode: 'video', startImage: null,
    imageRefs: [], modelOptions: null,
    params: {
      ...baseState.params, prompt: 'cancel queued persistence', model_type: 'test-model',
      image_mode: 0, image_start: undefined, image_refs: undefined,
    },
  })
  const pending = useStore.getState().enhancePrompt()
  await waitForCondition(
    () => locks.ledgerQueue.length === 1 && useStore.getState().enhanceRequestScope !== null,
    'queued pre-POST persistence',
  )
  const cancel = useStore.getState().cancelEnhancePrompt()
  await waitForCondition(
    () => !useStore.getState().isEnhancing && locks.ledgerQueue.length === 1,
    'local pre-POST abort and queued cleanup',
  )
  assert.equal(posts, 0)
  assert.equal(deletes, 0)
  assert.equal(globalThis.localStorage.getItem('maestro:prompt-enhance-operations-v2'), null)
  foreignRelease.resolve()
  await foreignLedger
  await cancel
  assert.equal(await pending, false)
  assert.equal(posts, 0)
  assert.equal(deletes, 0)
  assert.equal(useStore.getState().enhanceRequestScope, null)
  assert.equal(globalThis.localStorage.getItem('maestro:prompt-enhance-operations-v2'), null)
})

test('Prompt Enhance cancel before POST stops locally without issuing a server DELETE', async t => {
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
  globalThis.document = Object.assign(new EventTarget(), { visibilityState: 'visible' })
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  const upload = deferred()
  let posts = 0
  let deletes = 0
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.includes('/api/v1/llm/models?')) {
      return jsonResponse({ models: [], guides: [], project_instance: 'c'.repeat(64) })
    }
    if (url.endsWith('/api/v1/upload')) return upload.promise
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      posts += 1
      throw new Error('cancelled request must not POST')
    }
    if (init.method === 'DELETE') {
      deletes += 1
      throw new Error('pre-admission cancel must not DELETE')
    }
    throw new Error(`unexpected pre-admission request ${url}`)
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
  useStore.setState({
    activeWorkspace: 'cancel-before-post', generationMode: 'video',
    startImage: new File(['image'], 'start.png', { type: 'image/png' }),
    imageRefs: [], modelOptions: null,
    params: { ...baseState.params, prompt: 'cancel before admission', model_type: 'test-model' },
  })
  const pending = useStore.getState().enhancePrompt()
  await waitForCondition(
    () => Boolean(useStore.getState().enhanceRequestScope),
    'pre-admission Enhance scope',
  )
  assert.notEqual(useStore.getState().enhanceRequestScope, null)
  await useStore.getState().cancelEnhancePrompt()
  upload.resolve(jsonResponse({ filename: 'start.png', path: '/private/start.png', url: '/image' }))
  assert.equal(await pending, false)
  assert.equal(posts, 0)
  assert.equal(deletes, 0)
  assert.equal(globalThis.localStorage.getItem('maestro:prompt-enhance-operations-v2'), null)
})

test('account identity changes fence deferred generation submission and active-job discovery', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
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
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { locks: { request: (name, options, callback) => Promise.resolve(callback({ name })) } },
  })
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
    if (originalNavigatorDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor)
    } else {
      delete globalThis.navigator
    }
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
  let enhancePosts = 0
  let discoverNext = false
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.endsWith('/api/v1/generate')) return submission.promise
    if (url.includes('/api/v1/llm/models?')) {
      return jsonResponse({ models: [], guides: [], project_instance: 'a'.repeat(64) })
    }
    if (url.endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({ operation_id: 'account-enhance', status: 'ready', phase: 'ready', retryable: false }, 202)
    }
    if (url.endsWith('/api/v1/llm/enhance-prompt')) {
      enhancePosts += 1
      const body = JSON.parse(String(init.body))
      assert.equal(body.project_instance, 'a'.repeat(64))
      return jsonResponse({
        request_id: body.request_id.replaceAll('-', ''), operation_kind: 'enhance',
        status: 'running', phase: 'generating', stage: 'llm', pass: 1, pass_limit: 1,
        attempt: 1, attempt_limit: 1, partial_text: 'old-account partial',
        generated_tokens_approx: 1, elapsed_seconds: 1, live_tps: 1,
        average_tps: null, result_available: false, retryable: false,
      }, 202)
    }
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

  const pendingEnhance = useStore.getState().enhancePrompt()
  await waitForCondition(() => enhancePosts >= 1, 'account-scoped Enhance admission')
  assert.equal(enhancePosts, 1)
  assert.notEqual(globalThis.localStorage.getItem('maestro:prompt-enhance-operations-v2'), null)
  const pendingSubmit = useStore.getState().startGeneration()
  await Promise.resolve()
  assert.equal(useStore.getState().jobs.length, 1, 'submission placeholder should be visible before logout')
  await useStore.getState().logoutAccount()
  assert.equal(await pendingEnhance, false)
  assert.equal(globalThis.localStorage.getItem('maestro:prompt-enhance-operations-v2'), null)
  assert.equal(useStore.getState().isEnhancing, false)
  assert.equal(useStore.getState().enhanceRequestScope, null)
  assert.equal(useStore.getState().enhanceQueueCard, null)
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
  await waitForCondition(() => requestCount >= 2, 'coalesced recovery follow-up')
  assert.equal(requestCount, 2)
  assert.equal(inFlight, 1)
  assert.equal(maxInFlight, 1)
  assert.equal(timers.size, 0)

  second.resolve({
    ...apiJobStatus('coalesced-job', 'project one', plan(), 21),
    status: 'completed',
  })
  await waitForCondition(
    () => useStore.getState().jobs.length === 0,
    'terminal recovered job cleanup',
  )
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
