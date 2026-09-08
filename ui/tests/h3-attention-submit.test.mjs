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
      contents: source,
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
  return import(`${asDataModule(await storeBundle())}#h3-attention-submit-${storeRealmSequence}`)
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
    durationSeconds: 4.1,
    selectedModelPerMode: { ...state.selectedModelPerMode, video: 'minimax_h3' },
    params: {
      ...state.params,
      model_type: 'minimax_h3',
      prompt: 'A precise camera move through a quiet room.',
      negative_prompt: '',
      image_mode: 0,
      resolution: '1344x768',
      video_length: 97,
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
      custom_settings: { h3_attention_engine: 'sage2' },
    },
  }))
}

async function withFreshStore(action, { acceleration = accelerationStatus(true) } = {}) {
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
    if (url === '/api/v1/upload' && request.method === 'POST') {
      return Response.json({ filename: 'reference.png', path: '/uploads/reference.png', url: '/api/v1/file/reference.png' })
    }
    if (url === '/api/v1/generate' && request.method === 'POST') {
      return Response.json({ job_id: 'sage-held', status: 'queued', held: true })
    }
    throw new Error(`Unexpected attention-submit request: ${request.method} ${url}`)
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

test('Sage submission rejects incompatible checkpoints and semantic Base routes before uploads or jobs', async () => {
  const cases = [
    {
      name: 'W4A8 adaptive checkpoint',
      arrange(useStore) {
        useStore.getState().setParam('h3_adaptive_fl2va_model', 'minimax_h3_w4a8_fl2va')
      },
      reason: /requires Base H3/,
    },
    {
      name: 'PinkCherry adaptive checkpoint',
      arrange(useStore) {
        useStore.getState().setParam('h3_adaptive_fl2va_model', 'minimax_h3_pinkcherry_fl2va')
      },
      reason: /requires Base H3/,
    },
    {
      name: 'pinned Ref2VA checkpoint',
      arrange(useStore) {
        useStore.setState(state => ({
          modelOptions: modelOptions('minimax_h3_ref2va'),
          params: {
            ...state.params,
            model_type: 'minimax_h3_ref2va',
            h3_adaptive_conditioning: false,
          },
        }))
      },
      reason: /requires Base H3/,
    },
    {
      name: 'semantic reference on Base H3',
      arrange(useStore) {
        useStore.getState().addImageRef(new File(['reference'], 'reference.png', { type: 'image/png' }))
      },
      reason: /cannot run with reference media/,
    },
  ]

  for (const scenario of cases) {
    await withFreshStore(async ({ alerts, requests, useStore }) => {
      scenario.arrange(useStore)
      const paramsBefore = structuredClone(useStore.getState().params)
      const refsBefore = useStore.getState().imageRefs
      await useStore.getState().startGeneration('queue')
      assertNoSubmission(useStore, requests)
      assert.deepEqual(useStore.getState().params, paramsBefore, scenario.name)
      assert.equal(useStore.getState().imageRefs, refsBefore, scenario.name)
      assert.match(alerts.at(-1) || '', scenario.reason, scenario.name)
      assert.equal(requests.some(request => request.url.includes('/h3/acceleration')), false, scenario.name)
    })
  }
})

test('Sage submission fails closed when availability is false or cannot be read', async () => {
  const cases = [
    { name: 'reported unavailable', acceleration: accelerationStatus(false) },
    { name: 'status request failure', acceleration: new Error('status offline') },
  ]

  for (const scenario of cases) {
    await withFreshStore(async ({ alerts, requests, useStore }) => {
      const paramsBefore = structuredClone(useStore.getState().params)
      await useStore.getState().startGeneration('queue')
      assertNoSubmission(useStore, requests)
      assert.deepEqual(useStore.getState().params, paramsBefore, scenario.name)
      assert.equal(requests.filter(request => request.url.includes('/h3/acceleration')).length, 1, scenario.name)
      assert.match(alerts.at(-1) || '', /SageAttention2\+\+ is unavailable/, scenario.name)
    }, { acceleration: scenario.acceleration })
  }
})

test('available Sage Base submission preserves the selected engine and creates one held queue job', async () => {
  await withFreshStore(async ({ requests, useStore }) => {
    await useStore.getState().startGeneration('queue')

    assert.deepEqual(requests.map(request => request.url), [
      '/api/v1/h3/acceleration?probe=false',
      '/api/v1/generate',
    ])
    const generate = requests[1]
    const body = JSON.parse(generate.body)
    assert.equal(body._queue_mode, 'held')
    assert.equal(body.custom_settings.h3_attention_engine, 'sage2')
    assert.equal(useStore.getState().params.custom_settings.h3_attention_engine, 'sage2')
    assert.equal(useStore.getState().jobs.length, 1)
    assert.deepEqual(
      {
        id: useStore.getState().jobs[0].id,
        held: useStore.getState().jobs[0].held,
        status: useStore.getState().jobs[0].status,
      },
      { id: 'sage-held', held: true, status: 'queued' },
    )
  })
})

test('a late Sage status response cannot submit after the selected request changes', async () => {
  const cases = [
    {
      name: 'engine',
      mutate(useStore) {
        useStore.getState().setParam('custom_settings', { h3_attention_engine: 'sdpa' })
      },
      verify(useStore) {
        assert.equal(useStore.getState().params.custom_settings.h3_attention_engine, 'sdpa')
      },
    },
    {
      name: 'adaptive model',
      mutate(useStore) {
        useStore.getState().setParam('h3_adaptive_fl2va_model', 'minimax_h3_pinkcherry_fl2va')
      },
      verify(useStore) {
        assert.equal(useStore.getState().params.h3_adaptive_fl2va_model, 'minimax_h3_pinkcherry_fl2va')
      },
    },
    {
      name: 'reference media',
      mutate(useStore) {
        useStore.getState().addImageRef(new File(['late'], 'late.png', { type: 'image/png' }))
      },
      verify(useStore) {
        assert.equal(useStore.getState().imageRefs.length, 1)
      },
    },
    {
      name: 'duration',
      mutate(useStore) {
        useStore.getState().setDurationSeconds(8)
      },
      verify(useStore) {
        assert.equal(useStore.getState().durationSeconds, 7.875)
        assert.equal(useStore.getState().params.video_length, 189)
      },
    },
    {
      name: 'project',
      mutate(useStore) {
        useStore.setState({ activeWorkspace: 'another-project' })
      },
      verify(useStore) {
        assert.equal(useStore.getState().activeWorkspace, 'another-project')
      },
    },
  ]

  for (const scenario of cases) {
    const statusRequested = deferred()
    const releaseStatus = deferred()
    await withFreshStore(async ({ requests, useStore }) => {
      const start = useStore.getState().startGeneration('queue')
      const phase = await Promise.race([
        statusRequested.promise.then(() => 'requested'),
        start.then(() => 'completed'),
      ])
      assert.equal(phase, 'requested', scenario.name)
      scenario.mutate(useStore)
      releaseStatus.resolve(Response.json(accelerationStatus(true)))
      await start
      assertNoSubmission(useStore, requests)
      assert.deepEqual(requests.map(request => request.url), ['/api/v1/h3/acceleration?probe=false'])
      scenario.verify(useStore)
    }, {
      acceleration() {
        statusRequested.resolve()
        return releaseStatus.promise
      },
    })
  }
})
