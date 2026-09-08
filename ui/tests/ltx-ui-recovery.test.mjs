import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

const UI_ROOT = new URL('..', import.meta.url).pathname
const source = relative => readFile(new URL(relative, import.meta.url), 'utf8')

const [types, store, inputs, advanced] = await Promise.all([
  source('../src/types/index.ts'),
  source('../src/stores/useStore.ts'),
  source('../src/components/Sidebar/InputsPanel.tsx'),
  source('../src/components/Sidebar/AdvancedSettings.tsx'),
])

function sliceBetween(contents, startMarker, endMarker) {
  const start = contents.indexOf(startMarker)
  assert.notEqual(start, -1, `found ${startMarker}`)
  const end = contents.indexOf(endMarker, start)
  assert.notEqual(end, -1, `found ${endMarker}`)
  return contents.slice(start, end)
}

test('LTX audio and decoder contracts remain typed and use the native controls', () => {
  assert.match(types, /ltx25_video_vae\?: 'fast' \| 'nad'/)
  assert.match(types, /infer_audio_prompt_from_guide\?: boolean/)
  assert.match(types, /ltx25_video_vae_choices\?: \{[^]*value: 'fast' \| 'nad'[^]*experimental\?: boolean[^]*\}\[\] \| null/)
  assert.match(types, /ltx25_video_vae_default\?: 'fast' \| 'nad'/)

  const soundtrack = sliceBetween(
    inputs,
    '{/* Option strip — soundtrack: audio strength + processing flags */}',
    '{/* Option strip — control-video audio stays independent from motion. */}',
  )
  assert.match(soundtrack, /h3StudioWorkflow \? \(/)
  assert.match(soundtrack, /H3 preserves this soundtrack and uses it to condition the new video/)
  assert.match(soundtrack, /params\.audio_scale \?\? 1\.0/)
  assert.match(soundtrack, /setParam\('audio_scale', parseFloat\(e\.target\.value\)\)/)
  assert.match(soundtrack, /architecture === 'ltx2_25' \? 1\.0 : 3\.0/)
  assert.match(soundtrack, /Isolate vocals for better lip sync/)
  assert.match(soundtrack, /keeping the original song in the finished video/)
  assert.doesNotMatch(soundtrack, /modality_scale/)

  assert.match(advanced, /LTX-2\.5 Video Decoder/)
  assert.match(advanced, /setParam\('ltx25_video_vae', e\.target\.value as 'fast' \| 'nad'\)/)
  assert.match(advanced, /choice\.experimental \? ' \(Experimental\)' : ''/)
  assert.match(advanced, /LTX-2\.5 NAD VAE/)
})

test('submission and output restore repair only the declared LTX values', () => {
  const submit = sliceBetween(
    store,
    "const params: Record<string, unknown> = {",
    '// Default I2V / video-source strength.',
  )
  assert.match(submit, /infer_audio_prompt_from_guide === true/)
  assert.match(submit, /\(params\.image_mode \?\? 0\) === 0/)
  assert.match(submit, /params\.audio_guide/)
  assert.match(submit, /!params\.video_guide \|\| !String\(params\.video_prompt_type \|\| ''\)\.includes\('V'\)/)
  assert.match(submit, /typeof params\.audio_prompt_type === 'string'/)
  assert.match(submit, /!\[\.\.\.'AK2'\]\.some\(letter => audioPromptType\.includes\(letter\)\)/)
  assert.match(submit, /params\.audio_prompt_type = `A\$\{audioPromptType\}`/)
  assert.match(submit, /ltx25_video_vae_choices\?\.length/)
  assert.match(submit, /choice => choice\.value === params\.ltx25_video_vae/)
  assert.match(submit, /delete params\.ltx25_video_vae/)

  const modelOptions = sliceBetween(
    store,
    'loadModelOptions: async (',
    '// System config',
  )
  assert.match(modelOptions, /choice => choice\.value === currentVideoVae/)
  assert.match(modelOptions, /paramUpdates\.ltx25_video_vae/)

  const restore = sliceBetween(
    store,
    'loadSettingsFromOutput: async () =>',
    'rerollGeneration: async () =>',
  )
  assert.match(restore, /const restoredLtx25VideoVae = restoredModelOptions\?\.ltx25_video_vae_choices/)
  assert.match(restore, /choice => choice\.value === p\.ltx25_video_vae/)
  assert.match(restore, /newParams\.ltx25_video_vae = restoredLtx25VideoVae/)
  assert.match(restore, /typeof p\.audio_scale === 'number' && Number\.isFinite\(p\.audio_scale\)/)
  assert.match(restore, /Math\.min\(restoredAudioScaleMaximum, Math\.max\(0\.1, p\.audio_scale\)\)/)
  assert.match(restore, /newParams\.audio_scale = restoredAudioScale/)
})

function asDataModule(contents) {
  return `data:text/javascript;base64,${Buffer.from(contents).toString('base64')}`
}

let storeBundlePromise
let storeRealmSequence = 0
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

async function loadStoreModuleFresh() {
  storeRealmSequence += 1
  return import(`${asDataModule(await storeBundle())}#ltx-ui-${storeRealmSequence}`)
}

class StorageFake {
  values = new Map()
  getItem(key) { return this.values.get(key) ?? null }
  setItem(key, value) { this.values.set(key, String(value)) }
  removeItem(key) { this.values.delete(key) }
}

const families = [{ id: 'ltx2', label: 'LTX 2', order: 1 }]
const models = [
  {
    model_type: 'ltx2_22B_distilled_1_1',
    name: 'LTX-2.3 Distilled',
    family: 'ltx2',
    architecture: 'ltx2',
    is_i2v: true,
    is_t2v: true,
    guidance_max_phases: 1,
    fps: 16,
  },
  {
    model_type: 'ltx2_25',
    name: 'LTX-2.5 Distilled',
    family: 'ltx2',
    architecture: 'ltx2_25',
    is_i2v: true,
    is_t2v: true,
    guidance_max_phases: 1,
    fps: 25,
  },
]

function options(modelType) {
  return {
    model_type: modelType,
    architecture: modelType === 'ltx2_25' ? 'ltx2_25' : 'ltx2',
    fps: modelType === 'ltx2_25' ? 25 : 16,
    frames_steps: 8,
    latent_size: 8,
    frames_minimum: 9,
    frames_maximum: 257,
    guidance_max_phases: 1,
    sliding_window: false,
    default_num_inference_steps: 20,
    default_guidance_scale: 1,
    audio_only: false,
    ...(modelType === 'ltx2_25' ? {
      ltx25_video_vae_choices: [
        { value: 'fast', label: 'Fast', description: 'Fast decoder' },
        { value: 'nad', label: 'NAD', description: 'Experimental decoder', experimental: true },
      ],
      ltx25_video_vae_default: 'fast',
    } : {}),
  }
}

async function settleAsyncWork() {
  for (let i = 0; i < 12; i += 1) await new Promise(resolve => setImmediate(resolve))
}

function setRestorableOutput(useStore, params, name = 'ltx-restore.mp4') {
  useStore.setState({
    outputs: [{
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
      revision: 'ltx-restore-revision',
      workspace: 'default',
      private: false,
      explicit: false,
    }],
    outputsTotal: 1,
    selectedOutput: 0,
    selectedOutputMetaName: name,
    selectedOutputMeta: {
      source: 'sidecar',
      params,
      upload_filenames: {},
    },
  })
}

async function withVisibility(visibility, action) {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const localStorage = new StorageFake()
  const sessionStorage = new StorageFake()
  const visibilityWrites = []
  const generationRequests = []
  localStorage.setItem('maestro_mode_settings', JSON.stringify({
    generationMode: 'video',
    selectedModelPerMode: { video: 'ltx2_22B_distilled_1_1' },
    savedParamsPerMode: {},
    savedLoraPerMode: {},
    savedPromptPerMode: {},
  }))
  globalThis.localStorage = localStorage
  globalThis.sessionStorage = sessionStorage
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: '127.0.0.1' },
    matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    const method = init.method || 'GET'
    if (url === '/api/v1/models') return Response.json({ families, models })
    if (url === '/api/v1/model-visibility' && method === 'GET') return Response.json(visibility)
    if (url === '/api/v1/model-visibility' && method === 'PUT') {
      const body = JSON.parse(String(init.body))
      visibilityWrites.push(body)
      return Response.json({ configured: true, ...body })
    }
    if (url === '/api/v1/loras/installed') return Response.json({ loras: [] })
    if (url.startsWith('/api/v1/loras/')) return Response.json({ loras: [], guidance_max_phases: 1 })
    if (url.startsWith('/api/v1/defaults/')) return Response.json({})
    if (url.startsWith('/api/v1/model-options/')) {
      return Response.json(options(decodeURIComponent(url.split('/').pop())))
    }
    if (url === '/api/v1/generate' && method === 'POST') {
      generationRequests.push(JSON.parse(String(init.body)))
      return Response.json({ job_id: 'ltx-ui-test', status: 'queued' })
    }
    if (url === '/api/v1/status/ltx-ui-test') {
      return Response.json({
        job_id: 'ltx-ui-test', status: 'cancelled', progress: 0,
        step: 0, total_steps: 0, phase: '', message: 'Cancelled',
        output_files: [], error: null, oom_info: null,
      })
    }
    if (url.startsWith('/api/v1/outputs')) return Response.json({ outputs: [], total: 0 })
    throw new Error(`Unexpected LTX UI request: ${method} ${url}`)
  }
  try {
    const { useStore } = await loadStoreModuleFresh()
    return await action({ generationRequests, localStorage, useStore, visibilityWrites })
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

test('v9 visibility discovers LTX-2.5 once without changing the current video model', async () => {
  await withVisibility({
    configured: true,
    enabled_models: ['ltx2_22B_distilled_1_1'],
    defaults_version: 9,
  }, async ({ localStorage, useStore, visibilityWrites }) => {
    await useStore.getState().loadModels()
    await settleAsyncWork()
    assert.equal(useStore.getState().enabledModels.has('ltx2_25'), true)
    assert.equal(useStore.getState().params.model_type, 'ltx2_22B_distilled_1_1')
    assert.equal(useStore.getState().selectedModelPerMode.video, 'ltx2_22B_distilled_1_1')
    assert.equal(localStorage.getItem('maestro_defaults_version'), '10')
    const writesAfterMigration = visibilityWrites.length
    assert.ok(writesAfterMigration > 0)
    assert.equal(visibilityWrites.at(-1).defaults_version, 10)
    assert.equal(visibilityWrites.at(-1).enabled_models.includes('ltx2_25'), true)

    await useStore.getState().loadModels()
    await settleAsyncWork()
    assert.equal(visibilityWrites.length, writesAfterMigration)
  })
})

test('fresh defaults include LTX-2.5 while a v10 hide stays hidden', async () => {
  await withVisibility({
    configured: false,
    enabled_models: [],
    defaults_version: 0,
  }, async ({ useStore }) => {
    await useStore.getState().loadModels()
    await settleAsyncWork()
    assert.equal(useStore.getState().enabledModels.has('ltx2_25'), true)
    assert.equal(useStore.getState().params.model_type, 'ltx2_22B_distilled_1_1')
  })

  await withVisibility({
    configured: true,
    enabled_models: ['ltx2_22B_distilled_1_1'],
    defaults_version: 10,
  }, async ({ useStore, visibilityWrites }) => {
    await useStore.getState().loadModels()
    await settleAsyncWork()
    assert.equal(useStore.getState().enabledModels.has('ltx2_25'), false)
    assert.equal(useStore.getState().params.model_type, 'ltx2_22B_distilled_1_1')
    assert.equal(useStore.getState().selectedModelPerMode.video, 'ltx2_22B_distilled_1_1')
    assert.equal(visibilityWrites.length, 0)
  })
})

test('model-option hydration selects the declared decoder default and preserves valid NAD', async () => {
  await withVisibility({
    configured: true,
    enabled_models: ['ltx2_22B_distilled_1_1', 'ltx2_25'],
    defaults_version: 10,
  }, async ({ useStore }) => {
    useStore.setState(state => ({
      params: { ...state.params, model_type: 'ltx2_25', ltx25_video_vae: 'obsolete' },
    }))
    await useStore.getState().loadModelOptions('ltx2_25')
    assert.equal(useStore.getState().params.ltx25_video_vae, 'fast')

    useStore.setState(state => ({
      params: { ...state.params, ltx25_video_vae: 'nad' },
    }))
    await useStore.getState().loadModelOptions('ltx2_25')
    assert.equal(useStore.getState().params.ltx25_video_vae, 'nad')
  })
})

test('cross-model Load Settings hydrates LTX-2.5 options and restores NAD safely', async () => {
  await withVisibility({
    configured: true,
    enabled_models: ['ltx2_22B_distilled_1_1', 'ltx2_25'],
    defaults_version: 10,
  }, async ({ useStore }) => {
    await useStore.getState().loadModels()
    await settleAsyncWork()
    assert.equal(useStore.getState().params.model_type, 'ltx2_22B_distilled_1_1')

    setRestorableOutput(useStore, {
      model_type: 'ltx2_25',
      prompt: 'Restore this LTX-2.5 shot.',
      image_mode: 0,
      video_length: 81,
      ltx25_video_vae: 'nad',
      audio_scale: { malformed: true },
    })
    await useStore.getState().loadSettingsFromOutput()
    await settleAsyncWork()

    const restored = useStore.getState()
    assert.equal(restored.params.model_type, 'ltx2_25')
    assert.equal(restored.selectedModelPerMode.video, 'ltx2_25')
    assert.equal(restored.modelOptions?.model_type, 'ltx2_25')
    assert.equal(restored.params.ltx25_video_vae, 'nad')
    assert.equal(restored.params.audio_scale, 1.0)

    useStore.setState(state => ({
      params: { ...state.params, model_type: 'ltx2_22B_distilled_1_1' },
      selectedModelPerMode: {
        ...state.selectedModelPerMode,
        video: 'ltx2_22B_distilled_1_1',
      },
    }))
    await useStore.getState().loadModelOptions('ltx2_22B_distilled_1_1')
    setRestorableOutput(useStore, {
      model_type: 'ltx2_25',
      prompt: 'Restore a finite soundtrack strength.',
      image_mode: 0,
      video_length: 81,
      ltx25_video_vae: 'nad',
      audio_scale: 0.7,
    }, 'ltx-restore-finite.mp4')
    await useStore.getState().loadSettingsFromOutput()
    assert.equal(useStore.getState().params.audio_scale, 0.7)
  })
})

test('a manual model switch wins over delayed Load Settings option hydration', async () => {
  await withVisibility({
    configured: true,
    enabled_models: ['ltx2_22B_distilled_1_1', 'ltx2_25'],
    defaults_version: 10,
  }, async ({ useStore }) => {
    await useStore.getState().loadModels()
    await settleAsyncWork()
    setRestorableOutput(useStore, {
      model_type: 'ltx2_25',
      prompt: 'This restore must lose the race.',
      image_mode: 0,
      video_length: 81,
      ltx25_video_vae: 'nad',
      audio_scale: 0.7,
    }, 'ltx-delayed-restore.mp4')

    const baseFetch = globalThis.fetch
    let signalRequested
    const requested = new Promise(resolve => { signalRequested = resolve })
    let releaseResponse
    const released = new Promise(resolve => { releaseResponse = resolve })
    globalThis.fetch = async (input, init) => {
      if (String(input) === '/api/v1/model-options/ltx2_25') {
        signalRequested()
        await released
        return Response.json(options('ltx2_25'))
      }
      return baseFetch(input, init)
    }

    const restore = useStore.getState().loadSettingsFromOutput()
    await requested
    await useStore.getState().selectModel('ltx2_22B_distilled_1_1')
    releaseResponse()
    await restore
    await settleAsyncWork()

    assert.equal(useStore.getState().params.model_type, 'ltx2_22B_distilled_1_1')
    assert.equal(useStore.getState().selectedModelPerMode.video, 'ltx2_22B_distilled_1_1')
    assert.equal(useStore.getState().modelOptions?.model_type, 'ltx2_22B_distilled_1_1')
    assert.notEqual(useStore.getState().params.ltx25_video_vae, 'nad')
  })
})

test('generation heals a soundtrack mode and invalid decoder in the request copy', async () => {
  await withVisibility({
    configured: true,
    enabled_models: ['ltx2_22B_distilled_1_1', 'ltx2_25'],
    defaults_version: 10,
  }, async ({ generationRequests, useStore }) => {
    const ltxOptions = {
      ...options('ltx2_25'),
      infer_audio_prompt_from_guide: true,
      audio_prompt_type_sources: {
        choices: [['Soundtrack', 'A'], ['Control video', 'K'], ['Video to audio', '2']],
      },
    }
    useStore.setState(state => ({
      generationMode: 'video',
      modelOptions: ltxOptions,
      modelOptionsLoading: false,
      params: {
        ...state.params,
        model_type: 'ltx2_25',
        prompt: 'A dancer performs to the uploaded song.',
        image_mode: 0,
        audio_guide: '/uploads/song.wav',
        audio_prompt_type: 'NV',
        audio_scale: 0.7,
        ltx25_video_vae: 'obsolete',
      },
    }))

    await useStore.getState().startGeneration('now')
    await settleAsyncWork()
    assert.equal(generationRequests.length, 1)
    assert.equal(generationRequests[0].audio_prompt_type, 'ANV')
    assert.equal(generationRequests[0].audio_scale, 0.7)
    assert.equal(generationRequests[0].ltx25_video_vae, 'fast')
    assert.equal(generationRequests[0]._queue_mode, 'now')
    // Repair happens on the frozen request, so the editable UI state remains
    // exactly what the user loaded until they choose a visible control.
    assert.equal(useStore.getState().params.audio_prompt_type, 'NV')
    assert.equal(useStore.getState().params.ltx25_video_vae, 'obsolete')

    for (const [loadedValue, expectedValue] of [
      ['KNV', 'KNV'],
      [['K'], 'A'],
      [{ source: 'K' }, 'A'],
    ]) {
      useStore.setState(state => ({
        params: { ...state.params, audio_prompt_type: loadedValue },
      }))
      await useStore.getState().startGeneration('now')
      await settleAsyncWork()
      assert.equal(generationRequests.at(-1).audio_prompt_type, expectedValue)
    }

    useStore.setState(state => ({
      params: { ...state.params, image_mode: 1, audio_prompt_type: 'NV' },
    }))
    await useStore.getState().startGeneration('now')
    await settleAsyncWork()
    assert.equal(generationRequests.at(-1).audio_prompt_type, 'NV')
  })
})
