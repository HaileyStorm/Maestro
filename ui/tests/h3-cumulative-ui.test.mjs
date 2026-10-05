import assert from 'node:assert/strict'
import test from 'node:test'

import { build } from 'esbuild'
import { acceptedStudioSubmission } from './studioAdmissionFixture.mjs'

const STORE_ROOT = new URL('../src/stores/', import.meta.url).pathname
const cumulativePlan = { mode: 'cumulative_append', fps: 24, requested_frames: 141, published_frames: 141, window_count: 2, windows: [] }

function asDataModule(contents) {
  return `data:text/javascript;base64,${Buffer.from(contents).toString('base64')}`
}

let storeBundlePromise
let storeRealmSequence = 0
function storeBundle() {
  storeBundlePromise ||= build({
    stdin: {
      contents: `export * from './useStore'
import { useStore } from './useStore'
export { captureGenerationProfileSettings, restoreGenerationProfileSettings } from '../lib/generationProfiles'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { GenerateButton } from '../components/Sidebar/GenerateButton'
import { GlobalQueuePopover } from '../components/GlobalQueuePopover'
function renderControl(component) {
  const initial = useStore.getInitialState()
  const original = { ...initial }
  // SSR reads the initial snapshot; use the arranged state for this component check.
  Object.assign(initial, useStore.getState())
  try { return renderToStaticMarkup(createElement(component)) }
  finally { Object.assign(initial, original) }
}
export function renderGenerateButton() { return renderControl(GenerateButton) }
export function renderGlobalQueue() { return renderControl(GlobalQueuePopover) }
`,
      resolveDir: STORE_ROOT,
      loader: 'ts',
    },
    bundle: true,
    banner: { js: `import { createRequire } from 'node:module'; const require = createRequire(${JSON.stringify(import.meta.url)})` },
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
  }).then(result => result.outputFiles[0].text)
  return storeBundlePromise
}

async function loadStoreModuleFresh() {
  storeRealmSequence += 1
  return import(`${asDataModule(await storeBundle())}#h3-cumulative-submit-${storeRealmSequence}`)
}

class StorageFake {
  values = new Map()
  getItem(key) { return this.values.get(key) ?? null }
  setItem(key, value) { this.values.set(key, String(value)) }
  removeItem(key) { this.values.delete(key) }
}

async function settleAsyncWork() {
  for (let i = 0; i < 12; i += 1) await new Promise(resolve => setImmediate(resolve))
}

const modelIds = [
  'minimax_h3',
  'minimax_h3_pinkcherry_fl2va',
  'minimax_h3_w4a8_fl2va',
  'minimax_h3_ref2va',
]

const models = modelIds.map(model_type => ({
  model_type,
  name: model_type,
  family: 'h3',
  architecture: model_type === 'minimax_h3_ref2va' ? 'minimax_h3_ref2va' : 'minimax_h3',
  availability_status: 'available',
  execution_allowed: true,
  is_downloaded: true,
  is_i2v: true,
  is_t2v: true,
  guidance_max_phases: 2,
  fps: 24,
  default_for_operations: model_type === 'minimax_h3' ? ['video'] : [],
}))

function modelOptions(modelType = 'minimax_h3') {
  return {
    model_type: modelType,
    architecture: modelType === 'minimax_h3_ref2va' ? 'minimax_h3_ref2va' : 'minimax_h3',
    fps: 24,
    guidance_max_phases: 2,
    frames_steps: 4,
    frames_minimum: 17,
    frames_maximum: 257,
    default_num_inference_steps: 28,
    default_guidance_scale: 1,
    sliding_window: false,
    resolutions: ['1344x768'],
    supports_end_frame: true,
    h3_cumulative_append: true,
  }
}

function accelerationStatus(available) {
  return {
    sol_attn: { available: true, reason: null },
    sage2: { available, reason: available ? null : 'SageAttention2++ is not installed.' },
    w4a8: { available: true, reason: null },
    stats: {},
  }
}

function acceptedHostTerms() {
  return {
    terms: Object.fromEntries([
      'minimax_h3_ref2va',
      'explicit_content',
      'cloudflare_remote',
    ].map(term => [term, {
      current_version: 1,
      accepted_version: 1,
      accepted_at: '2026-09-08T00:00:00Z',
      accepted: true,
    }])),
  }
}

function configureBase(useStore) {
  useStore.setState(state => ({
    activeWorkspace: 'default',
    generationMode: 'video',
    models,
    modelsLoaded: true,
    modelOptions: modelOptions(),
    modelOptionsLoading: false,
    _pollRecoveredJob() {},
    durationSeconds: 141 / 24,
    selectedModelPerMode: { ...state.selectedModelPerMode, video: 'minimax_h3' },
    params: {
      ...state.params,
      model_type: 'minimax_h3',
      prompt: 'A precise camera move through a quiet room.',
      negative_prompt: '',
      image_mode: 0,
      resolution: '1344x768',
      video_length: 141,
      sliding_window_size: 124,
      h3_cumulative_append: true,
      num_inference_steps: 17,
      guidance_scale: 1,
      seed: 41,
      h3_adaptive_conditioning: true,
      h3_adaptive_fl2va_model: 'minimax_h3',
      h3_adaptive_ref2va_model: 'minimax_h3_ref2va',
      h3_fl2va_loras: [],
      h3_fl2va_loras_multipliers: '',
      h3_ref2va_loras: [],
      h3_ref2va_loras_multipliers: '',
      activated_loras: [],
      loras_multipliers: '',
      image_refs: [],
      video_guide: '',
      video_guide2: '',
      video_guide3: '',
      audio_guide: '',
      audio_guide2: '',
      audio_guide3: '',
      audio_prompt_type: '',
      custom_settings: { h3_attention_engine: 'sdpa' },
    },
  }))
}

async function withFreshStore(action, {
  acceleration = accelerationStatus(true),
  estimate = { profiles: [], current: { estimate: { seconds: 100 } }, segment_count_estimate: null },
} = {}) {
  const originalStackTraceLimit = Error.stackTraceLimit
  // Data-module stack URLs contain the entire bundled source. Keep failures bounded.
  Error.stackTraceLimit = 0
  const originals = {
    fetch: globalThis.fetch,
    window: globalThis.window,
    document: globalThis.document,
    localStorage: globalThis.localStorage,
    sessionStorage: globalThis.sessionStorage,
  }
  const requests = []
  const alerts = []
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    const request = { url, method: init.method || 'GET', body: init.body }
    requests.push(request)
    if (url === '/api/v1/host-terms?workspace=default') {
      return Response.json(acceptedHostTerms())
    }
    if (url === '/api/v1/h3/acceleration?probe=false') {
      if (typeof acceleration === 'function') return acceleration(request)
      if (acceleration instanceof Error) throw acceleration
      return Response.json(acceleration)
    }
    if (url === '/api/v1/model-options/minimax_h3') return Response.json(modelOptions())
    if (url.startsWith('/api/v1/loras/minimax_h3')) return Response.json({ loras: [], guidance_max_phases: 2 })
    if (url === '/api/v1/h3/estimate') return typeof estimate === 'function' ? estimate() : Response.json(estimate)
    if (url === '/api/v1/upload' && request.method === 'POST') {
      return Response.json({ filename: 'reference.png', path: '/uploads/reference.png', url: '/api/v1/file/reference.png' })
    }
    if (url === '/api/v1/generate' && request.method === 'POST') {
      return Response.json(acceptedStudioSubmission(JSON.parse(init.body), '22222222222222222222222222222222', {
        h3_estimate: null, h3_cumulative_plan: cumulativePlan, window_total: 2,
      }))
    }
    throw new Error(`Unexpected cumulative UI request: ${request.method} ${url}`)
  }
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout: () => 0,
    clearTimeout() {},
    setInterval: () => 0,
    clearInterval() {},
    alert(message) { alerts.push(String(message)) },
    location: { hostname: '127.0.0.1' },
    matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
  })
  globalThis.document = Object.assign(new EventTarget(), {
    hidden: false,
    createElement: () => ({ muted: false }),
  })
  try {
    const storeModule = await loadStoreModuleFresh()
    configureBase(storeModule.useStore)
    await storeModule.useStore.getState().loadHostTerms()
    requests.length = 0
    return await action({ alerts, requests, ...storeModule })
  } finally {
    await settleAsyncWork()
    for (const [name, value] of Object.entries(originals)) {
      if (value === undefined) delete globalThis[name]
      else globalThis[name] = value
    }
    Error.stackTraceLimit = originalStackTraceLimit
  }
}

function nonStatusRequests(requests) {
  return requests.filter(request => request.url !== '/api/v1/h3/acceleration?probe=false')
}

function assertNoSubmission(useStore, requests) {
  assert.deepEqual(useStore.getState().jobs, [])
  assert.equal(useStore.getState().isGenerating, false)
  assert.deepEqual(nonStatusRequests(requests), [])
}


test('cumulative immediate and held submit preserve exact authored geometry without estimates', async () => {
  for (const mode of ['queue', 'generate']) await withFreshStore(async ({ requests, useStore, alerts }) => {
    useStore.setState({ slidingWindowLocked: false, h3CurrentEstimate: { seconds: 100 } })
    await useStore.getState().startGeneration(mode)
    assert.deepEqual(alerts, [])
    const submitted = requests.filter(r => r.url === '/api/v1/generate')
    assert.equal(submitted.length, 1)
    const body = JSON.parse(submitted[0].body)
    assert.equal(body.h3_cumulative_append, true)
    assert.equal(body.sliding_window_size, 124)
    assert.equal(body.video_length, 141)
    assert.equal(body.enhance_before_generate, false)
    assert.equal(body._queue_mode, mode === 'queue' ? 'held' : 'now')
    assert.equal('_h3_cumulative_append' in body, false)
    assert.equal(useStore.getState().jobs[0].h3Estimate ?? null, null)
    assert.equal(useStore.getState().jobs[0].status, 'queued')
    assert.deepEqual(useStore.getState().jobs[0].h3CumulativePlan, cumulativePlan)
    assert.equal(useStore.getState().jobs[0].windowTotal, 2)
  })
})

test('incompatible or unavailable cumulative selection blocks before uploads or jobs', async () => {
  const cases = [
    [s => s.setState({ modelOptions: { ...s.getState().modelOptions, h3_cumulative_append: false } }), /unavailable/],
    [s => s.setState({ studioPromptEnhance: true }), /Enhance/],
    [s => s.getState().setParam('h3_fl2va_loras', ['local.safetensors']), /LoRAs/],
    [s => s.getState().setParam('custom_settings', { h3_lightx2v_profile: 'h3_lightx2v_fl2v_4_v1' }), /LightX2V/],
    [s => s.getState().setParam('sliding_window_size', 123), /first-window/],
    [s => s.setState({ voiceCloneEnabled: true, voiceCloneRefs: [{ path: 'synthetic-voice.wav' }] }), /post-processing/],
  ]
  for (const [arrange, reason] of cases) await withFreshStore(async ({ useStore, requests, alerts }) => {
    arrange(useStore)
    const original = structuredClone(useStore.getState().params)
    await useStore.getState().startGeneration('queue')
    assertNoSubmission(useStore, requests)
    assert.deepEqual(useStore.getState().params, original)
    assert.match(alerts.at(-1) || '', reason)
  })
})

test('cumulative timing edits and estimate refresh keep exact frames and make no ordinary estimate request', async () => {
  await withFreshStore(async ({ useStore, requests }) => {
    useStore.getState().setDurationSeconds(141 / 24)
    assert.equal(useStore.getState().params.video_length, 141)
    useStore.setState({ h3CurrentEstimate: { seconds: 100 }, h3SegmentCountEstimate: { likely: 9 } })
    useStore.getState().setParam('h3_cumulative_append', true)
    await useStore.getState().refreshH3PerformanceEstimates()
    await useStore.getState().normalizeH3EditableProfile()
    assert.equal(useStore.getState().h3CurrentEstimate, null)
    assert.equal(useStore.getState().h3SegmentCountEstimate, null)
    assert.deepEqual(requests, [])
  })
})

test('model metadata refresh and Load Settings preserve exact cumulative geometry and clear legacy selection', async () => {
  await withFreshStore(async ({ useStore, alerts }) => {
    await useStore.getState().loadModelOptions('minimax_h3')
    assert.equal(useStore.getState().params.video_length, 141)
    assert.equal(useStore.getState().params.sliding_window_size, 124)
    assert.equal(useStore.getState().slidingWindowSeconds, 124 / 24)
    assert.equal(useStore.getState().slidingWindowLocked, true)
    const sidecar = structuredClone(useStore.getState().params)
    const output = {
      name: 'synthetic-cumulative.mp4', url: '/api/v1/file/synthetic-cumulative.mp4',
      type: 'video', mode: 'video', artifact_class: 'final', workspace: 'default',
      revision: 'synthetic', private: false, explicit: false, favorite: false,
      size: 1, created_at: '2026-10-03T00:00:00Z',
    }
    useStore.setState({
      outputs: [output], outputsTotal: 1, selectedOutput: 0,
      selectedOutputMetaName: output.name, selectedOutputMeta: { source: 'sidecar', params: sidecar },
    })
    assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
    assert.equal(useStore.getState().params.h3_cumulative_append, true)
    assert.equal(useStore.getState().params.video_length, 141)
    assert.equal(useStore.getState().params.sliding_window_size, 124)
    assert.equal(useStore.getState().durationSeconds, 141 / 24)
    assert.equal(useStore.getState().slidingWindowSeconds, 124 / 24)
    await settleAsyncWork()
    delete sidecar.h3_cumulative_append
    useStore.setState({ selectedOutputMeta: { source: 'sidecar', params: sidecar } })
    assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
    assert.equal(useStore.getState().params.h3_cumulative_append, false)
    assert.deepEqual(alerts, [])
  })
})

test('saved technical profiles round-trip cumulative selection without private worker state', async () => {
  await withFreshStore(async ({ useStore, captureGenerationProfileSettings, restoreGenerationProfileSettings }) => {
    const current = useStore.getState()
    const saved = captureGenerationProfileSettings(current.params, current)
    assert.equal(saved.params.h3_cumulative_append, true)
    assert.equal(saved.params.video_length, 141)
    assert.equal(saved.params.sliding_window_size, 124)
    assert.equal('_h3_cumulative_append' in saved.params, false)
    const restored = restoreGenerationProfileSettings(saved, { h3_cumulative_append: false }, {})
    assert.equal(restored.params.h3_cumulative_append, true)
    const legacy = structuredClone(saved)
    delete legacy.params.h3_cumulative_append
    assert.equal(restoreGenerationProfileSettings(legacy, current.params, {}).params.h3_cumulative_append, undefined)
  })
})

test('selecting cumulative mode discards an in-flight ordinary estimate and turning it off restores estimation', async () => {
  let finishEstimate
  let estimateRequests = 0
  const pending = new Promise(resolve => { finishEstimate = resolve })
  const response = { profiles: [], current: { estimate: { seconds: 100 } }, segment_count_estimate: { likely: 2 } }
  await withFreshStore(async ({ useStore }) => {
    useStore.getState().setParam('h3_cumulative_append', false)
    const refresh = useStore.getState().refreshH3PerformanceEstimates()
    assert.equal(estimateRequests, 1)
    useStore.getState().setParam('h3_cumulative_append', true)
    finishEstimate(Response.json(response))
    await refresh
    assert.equal(useStore.getState().h3CurrentEstimate, null)
    assert.equal(useStore.getState().h3SegmentCountEstimate, null)
    assert.equal(useStore.getState().h3EstimateLoading, false)
    useStore.getState().setParam('h3_cumulative_append', false)
    await useStore.getState().refreshH3PerformanceEstimates()
    assert.equal(estimateRequests, 2)
    assert.equal(useStore.getState().h3CurrentEstimate.seconds, 100)
    assert.equal(useStore.getState().h3SegmentCountEstimate.likely, 2)
  }, { estimate: () => ++estimateRequests === 1 ? pending : Response.json(response) })
})

test('rendered Generate and held Queue controls omit calibrating badges in cumulative mode', async () => {
  await withFreshStore(async ({ useStore, renderGenerateButton }) => {
    useStore.setState({ h3CurrentEstimate: null, h3EstimateLoading: false })
    const cumulative = renderGenerateButton()
    assert.doesNotMatch(cumulative, /calibrating|Collecting enough local timing data/)
    useStore.getState().setParam('prompt', '')
    assert.doesNotMatch(renderGenerateButton(), /calibrating|Collecting enough local timing data/)
    useStore.getState().setParam('h3_cumulative_append', false)
    assert.match(renderGenerateButton(), /calibrating/)
  })
})


test('rendered queue controls exclude terminal retained holds and count all live Studio stages', async () => {
  await withFreshStore(async ({ useStore, renderGenerateButton, renderGlobalQueue }) => {
    const row = status => ({ id: status, status, held: true, step: 0, totalSteps: 0, progress: 0 })
    const terminalRows = ['cancelled', 'failed', 'completed'].map(row)
    useStore.setState({ jobs: terminalRows, directorQueue: null, pipelineId: null, pipelineStatus: null })
    assert.match(renderGenerateButton(), />Generate</)
    assert.doesNotMatch(renderGenerateButton(), /Go \(/)
    assert.match(renderGlobalQueue(), /aria-label="Generation queue, 0 items"/)
    for (const status of ['queued', 'running', 'preparing', 'waiting_for_plan_approval']) {
      useStore.setState({ jobs: [...terminalRows, row(status)] })
      assert.match(renderGenerateButton(), />Go \(1\)</, status)
      assert.match(renderGlobalQueue(), /aria-label="Generation queue, 1 item"/, status)
    }
  })
})
