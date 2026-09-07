import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

const STORE_ROOT = new URL('../src/stores/', import.meta.url).pathname

function asDataModule(contents) {
  return `data:text/javascript;base64,${Buffer.from(contents).toString('base64')}`
}

let storeBundlePromise
let storeRealmSequence = 0
function storeBundle() {
  storeBundlePromise ||= readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8').then(source => build({
    stdin: {
      contents: `${source}\nexport { _saveSettings as saveSettingsForTest, _loadSettings as loadSettingsForTest }`,
      resolveDir: STORE_ROOT,
      loader: 'ts',
    },
    bundle: true,
    format: 'esm',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
  })).then(result => result.outputFiles[0].text)
  return storeBundlePromise
}

async function loadStoreModuleFresh() {
  storeRealmSequence += 1
  return import(`${asDataModule(await storeBundle())}#h3-adaptive-state-${storeRealmSequence}`)
}

class StorageFake {
  values = new Map()
  getItem(key) { return this.values.get(key) ?? null }
  setItem(key, value) { this.values.set(key, String(value)) }
  removeItem(key) { this.values.delete(key) }
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

async function settleAsyncWork() {
  for (let i = 0; i < 16; i += 1) await new Promise(resolve => setImmediate(resolve))
}

async function waitForCondition(predicate, label, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(`Timed out waiting for ${label}`)
    await new Promise(resolve => setTimeout(resolve, 5))
  }
}

const families = [
  { id: 'h3', label: 'MiniMax H3', order: 0 },
  { id: 'flux', label: 'Flux', order: 1 },
]

const h3Models = [
  ['minimax_h3', 'MiniMax H3'],
  ['minimax_h3_pinkcherry_fl2va', 'MiniMax H3 PinkCherry'],
  ['minimax_h3_w4a8_fl2va', 'MiniMax H3 W4A8'],
  ['minimax_h3_ref2va', 'MiniMax H3 Ref2VA'],
].map(([model_type, name]) => ({
  model_type,
  name,
  family: 'h3',
  architecture: 'minimax_h3',
  is_i2v: true,
  is_t2v: true,
  guidance_max_phases: 2,
  fps: 24,
  default_for_operations: model_type === 'minimax_h3' ? ['video'] : [],
}))

const models = [
  ...h3Models,
  {
    model_type: 'flux2_klein_9b', name: 'Flux 2 Klein', family: 'flux',
    architecture: 'flux', is_i2v: true, is_t2v: true,
    guidance_max_phases: 1, fps: 1, default_for_operations: ['image'],
  },
]

function modelOptions(modelType) {
  return {
    model_type: modelType,
    architecture: modelType.startsWith('minimax_h3') ? 'minimax_h3' : 'flux',
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
  }
}

function modelDefaults() {
  return {
    h3_default_profile_id: 'high',
    num_inference_steps: 28,
    resolution: '1344x768',
    guidance_scale: 1,
    custom_settings: { h3_attention_engine: 'sol_attn' },
    tea_cache: 0,
  }
}

function output(name, explicit = false) {
  return {
    name,
    url: `/api/v1/file/${name}`,
    type: 'video',
    mode: 'video',
    edit_sub_mode: null,
    artifact_class: 'final',
    linked_component_count: 0,
    favorite: false,
    size: 1,
    created_at: '2026-09-07T00:00:00Z',
    revision: `${name}-revision`,
    workspace: 'default',
    private: false,
    explicit,
  }
}

function setRestorableOutput(useStore, params, name = 'h3-adaptive.mp4') {
  useStore.setState({
    outputs: [output(name)],
    outputsTotal: 1,
    selectedOutput: 0,
    selectedOutputMetaName: name,
    selectedOutputMeta: { source: 'sidecar', params, upload_filenames: {} },
  })
}

function baseFetch(input, init = {}) {
  const url = String(input)
  const method = init.method || 'GET'
  if (url === '/api/v1/models') return Promise.resolve(Response.json({ families, models }))
  if (url === '/api/v1/model-visibility' && method === 'GET') {
    return Promise.resolve(Response.json({
      configured: true,
      enabled_models: models.map(model => model.model_type),
      defaults_version: 10,
    }))
  }
  if (url.startsWith('/api/v1/defaults/')) return Promise.resolve(Response.json(modelDefaults()))
  if (url.startsWith('/api/v1/model-options/')) {
    return Promise.resolve(Response.json(modelOptions(decodeURIComponent(url.split('/').pop()))))
  }
  if (url === '/api/v1/loras/installed') return Promise.resolve(Response.json({ loras: [] }))
  if (url === '/api/v1/loras/check-updates') return Promise.resolve(Response.json({ status: 'fresh' }))
  if (url.startsWith('/api/v1/loras/')) {
    const model = decodeURIComponent(url.split('/').pop())
    return Promise.resolve(Response.json({ loras: [`${model}.safetensors`], guidance_max_phases: 2 }))
  }
  if (url === '/api/v1/h3/estimate') {
    return Promise.resolve(Response.json({ profiles: [], current: { estimate: null } }))
  }
  throw new Error(`Unexpected adaptive-state request: ${method} ${url}`)
}

async function withFreshStore(action, options = {}) {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const alerts = []
  const storage = options.storage || new StorageFake()
  globalThis.fetch = options.fetchHandler || baseFetch
  globalThis.localStorage = storage
  globalThis.sessionStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval,
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
    return await action({ alerts, storage, ...storeModule })
  } finally {
    await settleAsyncWork()
    globalThis.fetch = originalFetch
    if (originalWindow === undefined) delete globalThis.window
    else globalThis.window = originalWindow
    if (originalDocument === undefined) delete globalThis.document
    else globalThis.document = originalDocument
    if (originalLocalStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = originalLocalStorage
    if (originalSessionStorage === undefined) delete globalThis.sessionStorage
    else globalThis.sessionStorage = originalSessionStorage
  }
}

test('missing and null architecture lists inherit independently while explicit empty stays clear', async () => {
  await withFreshStore(async ({ useStore }) => {
    const shared = 'folder/shared.safetensors'
    const turbo = 'folder/minimax_h3_turbo_sla_4step_comfyui_bf16.safetensors'
    const dasiwa = 'folder/dasiwa_ref2va_hybrid_v1_4step.safetensors'
    useStore.setState(state => ({
      modelOptions: modelOptions('minimax_h3'),
      params: {
        ...state.params,
        model_type: 'minimax_h3',
        h3_adaptive_conditioning: false,
        activated_loras: [shared, turbo, dasiwa],
        loras_multipliers: '0.25;0.5 0.6 0.8',
        h3_fl2va_loras: undefined,
        h3_fl2va_loras_multipliers: undefined,
        h3_ref2va_loras: null,
        h3_ref2va_loras_multipliers: undefined,
      },
    }))
    useStore.getState().setParam('h3_adaptive_conditioning', true)
    const inherited = useStore.getState().params
    assert.deepEqual(inherited.h3_fl2va_loras, [shared, turbo])
    assert.equal(inherited.h3_fl2va_loras_multipliers, '0.25;0.5 0.6')
    assert.deepEqual(inherited.h3_ref2va_loras, [shared, dasiwa])
    assert.equal(inherited.h3_ref2va_loras_multipliers, '0.25;0.5 0.8')

    useStore.setState(state => ({
      params: {
        ...state.params,
        h3_adaptive_conditioning: false,
        h3_fl2va_loras: [],
        h3_fl2va_loras_multipliers: '',
        h3_ref2va_loras: undefined,
        h3_ref2va_loras_multipliers: undefined,
      },
    }))
    useStore.getState().setParam('h3_adaptive_conditioning', true)
    assert.deepEqual(useStore.getState().params.h3_fl2va_loras, [])
    assert.deepEqual(useStore.getState().params.h3_ref2va_loras, [shared, dasiwa])
  })
})

test('split editors preserve independent weights for the same full asset path', async () => {
  await withFreshStore(async ({ useStore }) => {
    const sameAsset = 'project/loras/shared.safetensors'
    useStore.setState(state => ({
      modelOptions: modelOptions('minimax_h3'),
      params: {
        ...state.params,
        model_type: 'minimax_h3',
        h3_adaptive_conditioning: true,
        activated_loras: ['legacy.safetensors'],
        loras_multipliers: '0.7',
        h3_fl2va_loras: [sameAsset],
        h3_fl2va_loras_multipliers: '0.25;0.5',
        h3_ref2va_loras: [sameAsset],
        h3_ref2va_loras_multipliers: '0.8;0.85',
      },
    }))
    useStore.getState().setH3ArchitectureLoraWeight('ref2va', sameAsset, 1, 0.9)
    let params = useStore.getState().params
    assert.equal(params.h3_fl2va_loras_multipliers, '0.25;0.5')
    assert.equal(params.h3_ref2va_loras_multipliers, '0.8;0.9')
    assert.deepEqual(params.activated_loras, ['legacy.safetensors'])
    assert.equal(params.loras_multipliers, '0.7')

    useStore.getState().toggleH3ArchitectureLora('fl2va', sameAsset)
    params = useStore.getState().params
    assert.deepEqual(params.h3_fl2va_loras, [])
    assert.deepEqual(params.h3_ref2va_loras, [sameAsset])
    assert.equal(params.h3_ref2va_loras_multipliers, '0.8;0.9')
  })
})

test('each split editor remains usable while the opposite architecture list is malformed', async () => {
  await withFreshStore(async ({ alerts, useStore }) => {
    const fl = 'project/loras/fl-shared.safetensors'
    const ref = 'project/loras/ref-shared.safetensors'
    useStore.setState(state => ({
      modelOptions: modelOptions('minimax_h3'),
      params: {
        ...state.params,
        model_type: 'minimax_h3',
        h3_fl2va_loras: [fl],
        h3_fl2va_loras_multipliers: '0.2;0.3',
        h3_ref2va_loras: 'malformed-ref-list',
      },
    }))
    useStore.getState().setH3ArchitectureLoraWeight('fl2va', fl, 1, 0.45)
    assert.equal(useStore.getState().params.h3_fl2va_loras_multipliers, '0.2;0.45')
    assert.equal(useStore.getState().params.h3_ref2va_loras, 'malformed-ref-list')
    useStore.getState().toggleH3ArchitectureLora('fl2va', fl)
    assert.deepEqual(useStore.getState().params.h3_fl2va_loras, [])
    assert.equal(useStore.getState().params.h3_ref2va_loras, 'malformed-ref-list')

    useStore.setState(state => ({
      params: {
        ...state.params,
        h3_fl2va_loras: { malformed: true },
        h3_ref2va_loras: [ref],
        h3_ref2va_loras_multipliers: '0.7;0.8',
      },
    }))
    useStore.getState().setH3ArchitectureLoraWeight('ref2va', ref, 0, 0.65)
    assert.equal(useStore.getState().params.h3_ref2va_loras_multipliers, '0.65;0.8')
    assert.deepEqual(useStore.getState().params.h3_fl2va_loras, { malformed: true })
    useStore.getState().toggleH3ArchitectureLora('ref2va', ref)
    assert.deepEqual(useStore.getState().params.h3_ref2va_loras, [])
    assert.deepEqual(useStore.getState().params.h3_fl2va_loras, { malformed: true })
    assert.deepEqual(alerts, [])
  })
})

test('catalog rename reconciliation keeps each architecture weight string paired', async () => {
  await withFreshStore(async ({ useStore }) => {
    useStore.setState(state => ({
      _loraFilenameSnapshotAtLoad: { 'civitai:42': 'old-name.safetensors' },
      savedParamsPerMode: {
        video: {
          h3_fl2va_loras: ['old-name.safetensors'],
          h3_fl2va_loras_multipliers: '0.2;0.3',
          h3_ref2va_loras: ['old-name.safetensors'],
          h3_ref2va_loras_multipliers: '0.8;0.9',
        },
      },
      params: {
        ...state.params,
        h3_fl2va_loras: ['old-name.safetensors'],
        h3_fl2va_loras_multipliers: '0.2;0.3',
        h3_ref2va_loras: ['old-name.safetensors'],
        h3_ref2va_loras_multipliers: '0.8;0.9',
      },
    }))
    await useStore.getState().refreshLoraIdMap()
    const current = useStore.getState()
    assert.deepEqual(current.params.h3_fl2va_loras, ['new-name.safetensors'])
    assert.equal(current.params.h3_fl2va_loras_multipliers, '0.2;0.3')
    assert.deepEqual(current.params.h3_ref2va_loras, ['new-name.safetensors'])
    assert.equal(current.params.h3_ref2va_loras_multipliers, '0.8;0.9')
    assert.equal(current.savedParamsPerMode.video.h3_fl2va_loras_multipliers, '0.2;0.3')
    assert.equal(current.savedParamsPerMode.video.h3_ref2va_loras_multipliers, '0.8;0.9')
  }, {
    fetchHandler(input, init) {
      if (String(input) === '/api/v1/loras/installed') {
        return Promise.resolve(Response.json({
          loras: [{ filename: 'new-name.safetensors', lora_id: 'civitai:42' }],
        }))
      }
      return baseFetch(input, init)
    },
  })
})

test('persistence round-trips cross-architecture stable-ID collisions without merging filenames or weights', async () => {
  await withFreshStore(async ({ loadSettingsForTest, saveSettingsForTest, storage }) => {
    const fl = 'project/loras/fl-v1.safetensors'
    const ref = 'project/loras/ref-v2.safetensors'
    saveSettingsForTest({
      generationMode: 'video',
      selectedModelPerMode: { video: 'minimax_h3' },
      savedParamsPerMode: {
        video: {
          h3_fl2va_loras: [fl],
          h3_fl2va_loras_multipliers: '0.2;0.3',
          h3_ref2va_loras: [ref],
          h3_ref2va_loras_multipliers: '0.8;0.9',
        },
      },
      savedLoraPerMode: {},
      savedPromptPerMode: {},
    }, {
      [fl]: 'civitai:42',
      [ref]: 'civitai:42',
    })

    const persisted = JSON.parse(storage.getItem('maestro_mode_settings'))
    assert.deepEqual(persisted.savedParamsPerMode.video.h3_fl2va_loras, [`civitai:42#${fl}`])
    assert.deepEqual(persisted.savedParamsPerMode.video.h3_ref2va_loras, [`civitai:42#${ref}`])

    const loaded = loadSettingsForTest()
    assert.deepEqual(loaded.savedParamsPerMode.video.h3_fl2va_loras, [fl])
    assert.equal(loaded.savedParamsPerMode.video.h3_fl2va_loras_multipliers, '0.2;0.3')
    assert.deepEqual(loaded.savedParamsPerMode.video.h3_ref2va_loras, [ref])
    assert.equal(loaded.savedParamsPerMode.video.h3_ref2va_loras_multipliers, '0.8;0.9')
  })
})

test("local LoRA filenames containing '#' round-trip in adaptive and generic persisted state", async () => {
  await withFreshStore(async ({ loadSettingsForTest, saveSettingsForTest, storage }) => {
    const plainAsset = 'project/loras/plain.safetensors'
    const hashAsset = 'project/loras/a#b.safetensors'
    saveSettingsForTest({
      generationMode: 'video',
      selectedModelPerMode: { video: 'minimax_h3' },
      savedParamsPerMode: {
        video: {
          h3_fl2va_loras: [plainAsset, hashAsset],
          h3_fl2va_loras_multipliers: '0.25;0.5 0.35;0.55',
          h3_ref2va_loras: [plainAsset, hashAsset],
          h3_ref2va_loras_multipliers: '0.75;0.8 0.85;0.9',
        },
      },
      savedLoraPerMode: {
        video: {
          activated_loras: [hashAsset],
          loras_multipliers: '0.75',
          loraWeights: { [hashAsset]: [0.75] },
          availableLoras: [hashAsset],
        },
      },
      savedPromptPerMode: {},
    }, { 'catalog-only.safetensors': 'civitai:99' })

    const persisted = JSON.parse(storage.getItem('maestro_mode_settings'))
    assert.deepEqual(persisted.savedParamsPerMode.video.h3_fl2va_loras, [
      `local:${plainAsset}`,
      `local:${hashAsset}`,
    ])
    assert.deepEqual(persisted.savedParamsPerMode.video.h3_ref2va_loras, [
      `local:${plainAsset}`,
      `local:${hashAsset}`,
    ])
    assert.deepEqual(persisted.savedLoraPerMode.video.activated_loras, [`local:${hashAsset}`])

    const loaded = loadSettingsForTest()
    assert.deepEqual(loaded.savedParamsPerMode.video.h3_fl2va_loras, [plainAsset, hashAsset])
    assert.equal(loaded.savedParamsPerMode.video.h3_fl2va_loras_multipliers, '0.25;0.5 0.35;0.55')
    assert.deepEqual(loaded.savedParamsPerMode.video.h3_ref2va_loras, [plainAsset, hashAsset])
    assert.equal(loaded.savedParamsPerMode.video.h3_ref2va_loras_multipliers, '0.75;0.8 0.85;0.9')
    assert.deepEqual(loaded.savedLoraPerMode.video.activated_loras, [hashAsset])
    assert.deepEqual(loaded.savedLoraPerMode.video.availableLoras, [hashAsset])
    assert.deepEqual(loaded.savedLoraPerMode.video.loraWeights, { [hashAsset]: [0.75] })
  })
})

test('mode switches retain split state but a clean refresh restores only both checkpoint choices', async () => {
  const storage = new StorageFake()
  await withFreshStore(async ({ useStore }) => {
    await useStore.getState().loadModels()
    await settleAsyncWork()
    const sameAsset = 'project/loras/shared.safetensors'
    useStore.setState(state => ({
      explicitOutput: true,
      h3SelectedProfile: 'custom',
      params: {
        ...state.params,
        prompt: 'session-only prompt',
        num_inference_steps: 41,
        h3_adaptive_conditioning: true,
        h3_fl2va_loras: [sameAsset],
        h3_fl2va_loras_multipliers: '0.2;0.3',
        h3_ref2va_loras: [sameAsset],
        h3_ref2va_loras_multipliers: '0.8;0.9',
      },
    }))
    assert.equal(
      await useStore.getState().selectAdaptiveH3Model('ref2va', 'minimax_h3_ref2va'),
      true,
    )
    assert.equal(
      await useStore.getState().selectAdaptiveH3Model('fl2va', 'minimax_h3_w4a8_fl2va'),
      true,
    )
    useStore.getState().setGenerationMode('image')
    useStore.getState().setGenerationMode('video')
    await settleAsyncWork()
    const restored = useStore.getState().params
    assert.equal(restored.h3_adaptive_fl2va_model, 'minimax_h3_w4a8_fl2va')
    assert.equal(restored.h3_adaptive_ref2va_model, 'minimax_h3_ref2va')
    assert.deepEqual(restored.h3_fl2va_loras, [sameAsset])
    assert.equal(restored.h3_fl2va_loras_multipliers, '0.2;0.3')
    assert.equal(restored.h3_ref2va_loras_multipliers, '0.8;0.9')
  }, { storage })

  await withFreshStore(async ({ useStore }) => {
    await useStore.getState().loadModels()
    await settleAsyncWork()
    const clean = useStore.getState()
    assert.equal(clean.params.model_type, 'minimax_h3_w4a8_fl2va')
    assert.equal(clean.params.h3_adaptive_fl2va_model, 'minimax_h3_w4a8_fl2va')
    assert.equal(clean.params.h3_adaptive_ref2va_model, 'minimax_h3_ref2va')
    assert.equal(Object.hasOwn(clean.params, 'h3_fl2va_loras'), false)
    assert.equal(Object.hasOwn(clean.params, 'h3_ref2va_loras'), false)
    assert.equal(clean.params.prompt, '')
    assert.equal(clean.params.num_inference_steps, 28)
    assert.equal(clean.explicitOutput, false)
  }, { storage })
})

test('invalid adaptive IDs are visible and never reach an inventory endpoint', async () => {
  let loraRequests = 0
  await withFreshStore(async ({ alerts, useStore }) => {
    useStore.setState(state => ({
      params: {
        ...state.params,
        model_type: 'minimax_h3',
        h3_adaptive_conditioning: true,
        h3_adaptive_fl2va_model: 'removed-private-checkpoint',
        h3_adaptive_ref2va_model: 'minimax_h3_ref2va',
      },
    }))
    await useStore.getState().loadLoras('minimax_h3')
    assert.equal(loraRequests, 0)
    assert.match(useStore.getState().h3EstimateError, /available FL2VA model/)

    const before = useStore.getState().params.h3_adaptive_fl2va_model
    assert.equal(
      await useStore.getState().selectAdaptiveH3Model('fl2va', 'unknown-fl2va'),
      false,
    )
    assert.equal(useStore.getState().params.h3_adaptive_fl2va_model, before)
    assert.match(alerts.at(-1), /available FL2VA model/)

    setRestorableOutput(useStore, {
      model_type: 'minimax_h3',
      prompt: 'invalid restore',
      h3_adaptive_conditioning: true,
      h3_adaptive_fl2va_model: 'retired-checkpoint',
      h3_adaptive_ref2va_model: 'minimax_h3_ref2va',
    }, 'invalid-adaptive.mp4')
    await useStore.getState().loadSettingsFromOutput()
    assert.match(alerts.at(-1), /available FL2VA model/)
    assert.equal(loraRequests, 0)
  }, {
    fetchHandler(input, init) {
      if (String(input).startsWith('/api/v1/loras/')) loraRequests += 1
      return baseFetch(input, init)
    },
  })
})

test('invalid model pairs can be repaired one side at a time in either order', async () => {
  await withFreshStore(async ({ useStore }) => {
    const resetInvalidPair = () => useStore.setState(state => ({
      generationMode: 'video',
      selectedModelPerMode: { video: 'minimax_h3' },
      modelOptions: modelOptions('minimax_h3'),
      params: {
        ...state.params,
        model_type: 'minimax_h3',
        h3_adaptive_conditioning: true,
        h3_adaptive_fl2va_model: 'invalid-fl',
        h3_adaptive_ref2va_model: 'invalid-ref',
      },
    }))

    resetInvalidPair()
    assert.equal(await useStore.getState().selectAdaptiveH3Model('fl2va', 'minimax_h3_w4a8_fl2va'), true)
    assert.equal(useStore.getState().params.h3_adaptive_fl2va_model, 'minimax_h3_w4a8_fl2va')
    assert.equal(useStore.getState().params.h3_adaptive_ref2va_model, 'invalid-ref')
    assert.match(useStore.getState().h3EstimateError, /available Ref2VA model/)
    assert.equal(await useStore.getState().selectAdaptiveH3Model('ref2va', 'minimax_h3_ref2va'), true)
    assert.equal(useStore.getState().params.h3_adaptive_ref2va_model, 'minimax_h3_ref2va')

    resetInvalidPair()
    assert.equal(await useStore.getState().selectAdaptiveH3Model('ref2va', 'minimax_h3_ref2va'), true)
    assert.equal(useStore.getState().params.h3_adaptive_fl2va_model, 'invalid-fl')
    assert.equal(useStore.getState().params.h3_adaptive_ref2va_model, 'minimax_h3_ref2va')
    assert.match(useStore.getState().h3EstimateError, /available FL2VA model/)
    assert.equal(await useStore.getState().selectAdaptiveH3Model('fl2va', 'minimax_h3_pinkcherry_fl2va'), true)
    assert.equal(useStore.getState().params.h3_adaptive_fl2va_model, 'minimax_h3_pinkcherry_fl2va')
  })
})

test('enabling adaptive mode exposes invalid saved checkpoint choices for repair', async () => {
  await withFreshStore(async ({ alerts, useStore }) => {
    useStore.setState(state => ({
      generationMode: 'video',
      selectedModelPerMode: { video: 'minimax_h3_ref2va' },
      params: {
        ...state.params,
        model_type: 'minimax_h3_ref2va',
        h3_adaptive_conditioning: false,
        h3_adaptive_fl2va_model: 'invalid-saved-fl',
        h3_adaptive_ref2va_model: 'invalid-saved-ref',
      },
    }))
    useStore.getState().setParam('h3_adaptive_conditioning', true)
    await settleAsyncWork()
    const params = useStore.getState().params
    assert.equal(params.h3_adaptive_conditioning, true)
    assert.equal(params.h3_adaptive_fl2va_model, 'invalid-saved-fl')
    assert.equal(params.h3_adaptive_ref2va_model, 'invalid-saved-ref')
    assert.match(alerts.at(-1), /available FL2VA model/)
  })
})

test('malformed saved LoRAs do not hide adaptive or model repair, but submission remains strict', async () => {
  let generationRequests = 0
  await withFreshStore(async ({ alerts, useStore }) => {
    useStore.setState(state => ({
      generationMode: 'video',
      selectedModelPerMode: { video: 'minimax_h3' },
      modelOptions: modelOptions('minimax_h3'),
      modelOptionsLoading: false,
      params: {
        ...state.params,
        model_type: 'minimax_h3',
        prompt: 'A camera moves through a quiet room.',
        num_inference_steps: 28,
        h3_adaptive_conditioning: false,
        h3_adaptive_fl2va_model: 'invalid-fl',
        h3_adaptive_ref2va_model: 'invalid-ref',
        h3_fl2va_loras: 'malformed-fl-list',
        h3_fl2va_loras_multipliers: '0.2',
        h3_ref2va_loras: ['project/loras/ref.safetensors'],
        h3_ref2va_loras_multipliers: 'not-a-number',
      },
    }))

    useStore.getState().setParam('h3_adaptive_conditioning', true)
    await settleAsyncWork()
    let params = useStore.getState().params
    assert.equal(params.h3_adaptive_conditioning, true)
    assert.equal(params.h3_fl2va_loras, 'malformed-fl-list')
    assert.equal(params.h3_ref2va_loras_multipliers, 'not-a-number')

    useStore.setState(state => ({
      params: {
        ...state.params,
        model_type: 'minimax_h3',
        h3_adaptive_conditioning: false,
        activated_loras: 'malformed-shared-list',
        loras_multipliers: 'not-a-number',
        h3_fl2va_loras: undefined,
        h3_fl2va_loras_multipliers: undefined,
        h3_ref2va_loras: null,
        h3_ref2va_loras_multipliers: undefined,
      },
    }))
    useStore.getState().setParam('h3_adaptive_conditioning', true)
    await settleAsyncWork()
    params = useStore.getState().params
    assert.equal(params.h3_adaptive_conditioning, true)
    assert.equal(params.activated_loras, 'malformed-shared-list')
    assert.equal(params.loras_multipliers, 'not-a-number')
    assert.equal(params.h3_fl2va_loras, undefined)
    assert.equal(params.h3_ref2va_loras, null)

    useStore.setState(state => ({
      params: {
        ...state.params,
        h3_adaptive_fl2va_model: 'invalid-fl',
        h3_adaptive_ref2va_model: 'invalid-ref',
        h3_fl2va_loras: 'malformed-fl-list',
        h3_fl2va_loras_multipliers: '0.2',
        h3_ref2va_loras: ['project/loras/ref.safetensors'],
        h3_ref2va_loras_multipliers: 'not-a-number',
      },
    }))

    assert.equal(await useStore.getState().selectAdaptiveH3Model('fl2va', 'minimax_h3_w4a8_fl2va'), true)
    assert.equal(useStore.getState().params.h3_fl2va_loras, 'malformed-fl-list')
    assert.equal(useStore.getState().params.h3_ref2va_loras_multipliers, 'not-a-number')
    assert.equal(await useStore.getState().selectAdaptiveH3Model('ref2va', 'minimax_h3_ref2va'), true)
    assert.equal(useStore.getState().params.h3_adaptive_ref2va_model, 'minimax_h3_ref2va')

    useStore.setState(state => ({
      modelOptionsLoading: false,
      params: {
        ...state.params,
        h3_fl2va_loras: ['project/loras/fl.safetensors'],
        h3_fl2va_loras_multipliers: 'not-a-number',
        h3_ref2va_loras: ['project/loras/ref.safetensors'],
        h3_ref2va_loras_multipliers: '0.8;0.9',
      },
    }))
    await useStore.getState().startGeneration()
    params = useStore.getState().params
    assert.equal(generationRequests, 0)
    assert.equal(params.h3_fl2va_loras_multipliers, 'not-a-number')
    assert.match(alerts.at(-1), /finite numbers/)
  }, {
    fetchHandler(input, init) {
      if (String(input) === '/api/v1/generate') {
        generationRequests += 1
        return Promise.resolve(Response.json({ job_id: 'must-not-submit' }))
      }
      return baseFetch(input, init)
    },
  })
})

test('delayed defaults cannot overwrite newer split LoRA edits', async () => {
  const delayedDefaults = deferred()
  let defaultsStarted = false
  await withFreshStore(async ({ useStore }) => {
    useStore.setState(state => ({
      generationMode: 'video',
      selectedModelPerMode: { video: 'minimax_h3' },
      modelOptions: modelOptions('minimax_h3'),
      params: {
        ...state.params,
        model_type: 'minimax_h3',
        h3_adaptive_conditioning: true,
        h3_adaptive_fl2va_model: 'minimax_h3',
        h3_adaptive_ref2va_model: 'minimax_h3_ref2va',
        h3_fl2va_loras: ['fl-old.safetensors'],
        h3_fl2va_loras_multipliers: '0.2;0.3',
        h3_ref2va_loras: ['ref-old.safetensors'],
        h3_ref2va_loras_multipliers: '0.8;0.9',
      },
    }))
    assert.equal(
      await useStore.getState().selectAdaptiveH3Model('fl2va', 'minimax_h3_w4a8_fl2va'),
      true,
    )
    await waitForCondition(() => defaultsStarted, 'delayed W4A8 defaults')
    useStore.setState(state => ({
      params: {
        ...state.params,
        h3_fl2va_loras: ['fl-new.safetensors'],
        h3_fl2va_loras_multipliers: '0.4;0.45',
        h3_ref2va_loras: ['ref-new.safetensors'],
        h3_ref2va_loras_multipliers: '0.7;0.75',
      },
    }))
    delayedDefaults.resolve(Response.json(modelDefaults()))
    await settleAsyncWork()
    const current = useStore.getState().params
    assert.deepEqual(current.h3_fl2va_loras, ['fl-new.safetensors'])
    assert.equal(current.h3_fl2va_loras_multipliers, '0.4;0.45')
    assert.deepEqual(current.h3_ref2va_loras, ['ref-new.safetensors'])
    assert.equal(current.h3_ref2va_loras_multipliers, '0.7;0.75')
  }, {
    fetchHandler(input, init) {
      if (String(input) === '/api/v1/defaults/minimax_h3_w4a8_fl2va') {
        defaultsStarted = true
        return delayedDefaults.promise
      }
      return baseFetch(input, init)
    },
  })
})

test('an older adaptive inventory cannot publish after switching to pinned mode', async () => {
  const oldFl = deferred()
  const oldRef = deferred()
  let baseCalls = 0
  await withFreshStore(async ({ useStore }) => {
    useStore.setState(state => ({
      generationMode: 'video',
      params: {
        ...state.params,
        model_type: 'minimax_h3',
        h3_adaptive_conditioning: true,
        h3_adaptive_fl2va_model: 'minimax_h3',
        h3_adaptive_ref2va_model: 'minimax_h3_ref2va',
      },
    }))
    const oldInventory = useStore.getState().loadLoras('minimax_h3')
    await waitForCondition(() => baseCalls === 1, 'old adaptive inventory')
    useStore.getState().setParam('h3_adaptive_conditioning', false)
    await waitForCondition(
      () => useStore.getState().availableLoras.includes('pinned-only.safetensors'),
      'new pinned inventory',
    )
    oldFl.resolve(Response.json({ loras: ['old-fl.safetensors'], guidance_max_phases: 2 }))
    oldRef.resolve(Response.json({ loras: ['old-ref.safetensors'], guidance_max_phases: 2 }))
    await oldInventory
    assert.deepEqual(useStore.getState().availableLoras, ['pinned-only.safetensors'])
    assert.equal(useStore.getState().params.h3_adaptive_conditioning, false)
  }, {
    fetchHandler(input, init) {
      const url = String(input)
      if (url === '/api/v1/loras/minimax_h3') {
        baseCalls += 1
        return baseCalls === 1
          ? oldFl.promise
          : Promise.resolve(Response.json({ loras: ['pinned-only.safetensors'], guidance_max_phases: 2 }))
      }
      if (url === '/api/v1/loras/minimax_h3_ref2va') return oldRef.promise
      return baseFetch(input, init)
    },
  })
})

test('cross-model Load Settings fetches inventory only after the final adaptive identity', async () => {
  const inventoryRequests = []
  let activeStore = null
  await withFreshStore(async ({ useStore }) => {
    activeStore = useStore
    useStore.setState(state => ({
      families,
      models,
      modelsLoaded: true,
      generationMode: 'video',
      selectedModelPerMode: { video: 'minimax_h3' },
      modelOptions: modelOptions('minimax_h3'),
      params: {
        ...state.params,
        model_type: 'minimax_h3',
        h3_adaptive_conditioning: false,
      },
    }))
    const sameAsset = 'project/loras/shared.safetensors'
    setRestorableOutput(useStore, {
      model_type: 'minimax_h3_w4a8_fl2va',
      prompt: 'restore adaptive identity first',
      image_mode: 0,
      video_length: 257,
      h3_adaptive_conditioning: true,
      h3_adaptive_fl2va_model: 'minimax_h3_w4a8_fl2va',
      h3_adaptive_ref2va_model: 'minimax_h3_ref2va',
      activated_loras: [],
      loras_multipliers: '',
      h3_fl2va_loras: [sameAsset],
      h3_fl2va_loras_multipliers: '0.2;0.3',
      h3_ref2va_loras: [sameAsset],
      h3_ref2va_loras_multipliers: '0.8;0.9',
    })
    await useStore.getState().loadSettingsFromOutput()
    await settleAsyncWork()
    assert.deepEqual(
      inventoryRequests.map(request => request.model),
      ['minimax_h3_w4a8_fl2va', 'minimax_h3_ref2va'],
    )
    for (const request of inventoryRequests) {
      assert.equal(request.active, 'minimax_h3_w4a8_fl2va')
      assert.equal(request.adaptive, true)
      assert.equal(request.fl, 'minimax_h3_w4a8_fl2va')
      assert.equal(request.ref, 'minimax_h3_ref2va')
    }
    assert.equal(useStore.getState().params.h3_fl2va_loras_multipliers, '0.2;0.3')
    assert.equal(useStore.getState().params.h3_ref2va_loras_multipliers, '0.8;0.9')

    inventoryRequests.length = 0
    setRestorableOutput(useStore, {
      model_type: 'minimax_h3_ref2va',
      prompt: 'restore pinned reference model',
      image_mode: 0,
      video_length: 257,
      h3_adaptive_conditioning: false,
      activated_loras: ['pinned-ref.safetensors'],
      loras_multipliers: '0.75',
    }, 'h3-pinned.mp4')
    await useStore.getState().loadSettingsFromOutput()
    await settleAsyncWork()
    assert.deepEqual(inventoryRequests.map(request => request.model), ['minimax_h3_ref2va'])
    assert.equal(inventoryRequests[0].active, 'minimax_h3_ref2va')
    assert.equal(inventoryRequests[0].adaptive, false)
    assert.equal(useStore.getState().params.h3_adaptive_conditioning, false)
  }, {
    fetchHandler(input, init) {
      const url = String(input)
      if (url.startsWith('/api/v1/loras/') && url !== '/api/v1/loras/installed' && url !== '/api/v1/loras/check-updates') {
        const state = activeStore.getState()
        inventoryRequests.push({
          model: decodeURIComponent(url.split('/').pop()),
          active: state.params.model_type,
          adaptive: state.params.h3_adaptive_conditioning !== false,
          fl: state.params.h3_adaptive_fl2va_model,
          ref: state.params.h3_adaptive_ref2va_model,
        })
      }
      return baseFetch(input, init)
    },
  })
})
