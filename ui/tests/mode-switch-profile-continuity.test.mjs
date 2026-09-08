import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

const UI_ROOT = new URL('..', import.meta.url).pathname
const profileSchema = JSON.parse(await readFile(
  new URL('../../app/services/generation_profile_fields.json', import.meta.url),
  'utf8',
))
const globalModeUiKeys = new Set(['h3StyleWorkflow', 'directorIdentityGuidanceScale'])
const modeUiKeys = Object.keys(profileSchema.ui).filter(key => !globalModeUiKeys.has(key))

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
  return import(`${asDataModule(await storeBundle())}#mode-continuity-${storeRealmSequence}`)
}

class StorageFake {
  values = new Map()
  getItem(key) { return this.values.get(key) ?? null }
  setItem(key, value) { this.values.set(key, String(value)) }
  removeItem(key) { this.values.delete(key) }
}

function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

function jsonResponse(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function modelOptions(modelType, overrides = {}) {
  return {
    model_type: modelType,
    fps: 24,
    latent_size: 4,
    frames_minimum: 1,
    frames_maximum: 241,
    guidance_max_phases: 1,
    sliding_window: true,
    sliding_window_defaults: {
      window_min: 25,
      window_default: 121,
      window_max: 241,
      overlap_min: 0,
      overlap_default: 9,
      overlap_max: 120,
      discard_last_frames: 0,
    },
    ...overrides,
  }
}

async function withFreshStore(fetchHandler, action) {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  globalThis.fetch = fetchHandler
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: '127.0.0.1' },
    matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  try {
    const { useStore } = await loadStoreModuleFresh()
    return await action(useStore)
  } finally {
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

function changedUiSettings(initial) {
  const enumSelections = {
    voiceCloneMode: 'two',
    imageRefType: 'KI',
    audioSubMode: 'music',
    editSubMode: 'outpaint',
    resolutionPreset: '1080p',
    aspectRatio: '9:16',
    editVideoResolution: '1920x1080',
    editRepaintResolutionProfile: '704p',
    editRecastResolutionProfile: '704p',
    editRetakeEngine: 'legacy',
    blendMode: 'overlap',
    outpaintAspect: '9:16',
    outpaintResolutionPreset: '1080p',
  }
  const changed = {}
  for (const key of modeUiKeys) {
    const value = initial[key]
    if (key === 'ttsVoiceCount') changed[key] = 3
    else if (key === 'outputCount') changed[key] = 4
    else if (key === 'outpaintPadding') changed[key] = { top: 1, bottom: 2, left: 3, right: 4 }
    else if (key === 'outpaintVideoBox') changed[key] = { x: 0.1, y: 0.2, w: 0.7, h: 0.6 }
    else if (Object.hasOwn(enumSelections, key)) changed[key] = enumSelections[key]
    else if (typeof value === 'boolean') changed[key] = !value
    else if (typeof value === 'number') changed[key] = value === 0 ? 0.625 : value + 0.125
    else changed[key] = value
  }
  return changed
}

function installTwoModes(useStore) {
  const emptyLoras = {
    activated_loras: [],
    loras_multipliers: '',
    loraWeights: {},
    availableLoras: [],
  }
  useStore.setState(state => ({
    generationMode: 'video',
    models: [
      { model_type: 'test_video_model', family: 'video' },
      { model_type: 'test_image_model', family: 'image' },
    ],
    families: [],
    params: {
      ...state.params,
      model_type: 'test_video_model',
      prompt: 'video prompt',
      negative_prompt: 'video negative',
      seed: 4242,
      image_start: '/job-only/frame.png',
    },
    selectedModelPerMode: {
      video: 'test_video_model',
      image: 'test_image_model',
    },
    savedLoraPerMode: { video: emptyLoras, image: emptyLoras },
  }))
}

test('mode round-trip restores the complete profile UI envelope and fences late timing metadata', async () => {
  const videoOptions = deferred()
  await withFreshStore(async input => {
    const url = String(input)
    if (url.endsWith('/api/v1/model-options/test_video_model')) return videoOptions.promise
    if (url.endsWith('/api/v1/model-options/test_image_model')) return jsonResponse(modelOptions('test_image_model'))
    if (url.includes('/api/v1/defaults/')) return jsonResponse({})
    if (url.includes('/api/v1/loras/')) return jsonResponse({ loras: [], guidance_max_phases: 1 })
    throw new Error(`Unexpected mode continuity request: ${url}`)
  }, async useStore => {
    installTwoModes(useStore)
    const initial = useStore.getInitialState()
    const changed = changedUiSettings(initial)
    const media = { name: 'frame.png' }
    const directorDeck = { seal: 'keep-global-deck' }
    useStore.setState({
      ...changed,
      spatialUpsampling: 'flashvsr',
      h3StyleWorkflow: 'global-workflow',
      directorIdentityGuidanceScale: 6.25,
      directorShotDeck: directorDeck,
      startImage: media,
    })

    useStore.getState().setGenerationMode('image')
    const firstVisit = useStore.getState()
    for (const key of modeUiKeys) {
      const expected = key === 'resolutionPreset' || key === 'aspectRatio' ? 'auto' : initial[key]
      assert.deepEqual(firstVisit[key], expected, `${key} starts from its first-visit default`)
    }
    assert.equal(firstVisit.spatialUpsampling, initial.spatialUpsampling)
    assert.equal(firstVisit.h3StyleWorkflow, 'global-workflow')
    assert.equal(firstVisit.directorIdentityGuidanceScale, 6.25)
    assert.equal(firstVisit.directorShotDeck, directorDeck)

    useStore.getState().setGenerationMode('video')
    const restored = useStore.getState()
    for (const key of modeUiKeys) {
      assert.deepEqual(restored[key], changed[key], `${key} round-trips by generation mode`)
    }
    assert.equal(restored.spatialUpsampling, 'flashvsr')
    assert.equal(restored.params.spatial_upsampling, 'flashvsr')
    assert.equal(restored.params.prompt, 'video prompt')
    assert.equal(restored.params.negative_prompt, 'video negative')
    assert.equal(restored.params.seed, 4242)
    assert.equal(restored.params.image_start, '/job-only/frame.png')
    assert.equal(restored.startImage, media)
    assert.equal(restored.h3StyleWorkflow, 'global-workflow')
    assert.equal(restored.directorIdentityGuidanceScale, 6.25)
    assert.equal(restored.directorShotDeck, directorDeck)
    assert.equal(restored.ttsVoices.length >= changed.ttsVoiceCount, true)

    const saved = restored.savedParamsPerMode.video
    assert.deepEqual(Object.keys(saved.uiSettings).sort(), [...modeUiKeys].sort())
    assert.equal(Object.hasOwn(saved.uiSettings, 'h3StyleWorkflow'), false)
    assert.equal(Object.hasOwn(saved.uiSettings, 'directorIdentityGuidanceScale'), false)
    assert.equal(Object.hasOwn(saved, 'spatial_upsampling'), false)
    assert.equal(Object.hasOwn(restored.params, 'uiSettings'), false)
    const persisted = JSON.parse(globalThis.localStorage.getItem('maestro_mode_settings'))
    assert.equal(Object.hasOwn(persisted.savedParamsPerMode.video, 'image_start'), false)
    assert.equal(Object.hasOwn(persisted.savedParamsPerMode.video, 'uiSettings'), true)

    useStore.setState(state => ({
      durationSeconds: 23.75,
      slidingWindowSeconds: 11.5,
      slidingWindowOverlap: 37,
      slidingWindowLocked: true,
      params: {
        ...state.params,
        video_length: 570,
        sliding_window_size: 276,
        sliding_window_overlap: 37,
      },
    }))
    const editedTiming = useStore.getState()
    videoOptions.resolve(jsonResponse(modelOptions('test_video_model', {
      fps: 12,
      frames_maximum: 49,
      sliding_window_defaults: {
        window_min: 9,
        window_default: 25,
        window_max: 49,
        overlap_min: 0,
        overlap_default: 1,
        overlap_max: 8,
        discard_last_frames: 0,
      },
    })))
    await new Promise(resolve => setTimeout(resolve, 0))
    const afterMetadata = useStore.getState()
    assert.equal(afterMetadata.modelOptions.model_type, 'test_video_model')
    for (const key of ['durationSeconds', 'slidingWindowSeconds', 'slidingWindowOverlap', 'slidingWindowLocked']) {
      assert.equal(afterMetadata[key], editedTiming[key], `${key} survives the late metadata response`)
    }
    for (const key of ['video_length', 'sliding_window_size', 'sliding_window_overlap']) {
      assert.equal(afterMetadata.params[key], editedTiming.params[key], `${key} is not regenerated by metadata-only hydration`)
    }
  })
})

test('Tools round-trip preserves mode settings, prompt, media, and the global H3 workflow', async () => {
  await withFreshStore(async input => {
    const url = String(input)
    if (url.endsWith('/api/v1/model-options/test_video_model')) return jsonResponse(modelOptions('test_video_model'))
    throw new Error(`Unexpected Tools continuity request: ${url}`)
  }, async useStore => {
    installTwoModes(useStore)
    const changed = changedUiSettings(useStore.getInitialState())
    const media = { name: 'tools-return-frame.png' }
    const deck = { seal: 'tools-global-deck' }
    useStore.setState({
      ...changed,
      spatialUpsampling: 'flashvsr',
      h3StyleWorkflow: 'global-workflow',
      directorShotDeck: deck,
      startImage: media,
    })

    useStore.getState().setGenerationMode('tools')
    assert.equal(useStore.getState().generationMode, 'tools')
    useStore.getState().setGenerationMode('video')

    const restored = useStore.getState()
    for (const key of modeUiKeys) assert.deepEqual(restored[key], changed[key], `${key} survives Tools`)
    assert.equal(restored.spatialUpsampling, 'flashvsr')
    assert.equal(restored.params.prompt, 'video prompt')
    assert.equal(restored.startImage, media)
    assert.equal(restored.h3StyleWorkflow, 'global-workflow')
    assert.equal(restored.directorShotDeck, deck)
  })
})

test('a removed saved model falls back to the current model defaults', async () => {
  await withFreshStore(async input => {
    const url = String(input)
    if (url.endsWith('/api/v1/model-options/flux2_klein_9b')) {
      return jsonResponse(modelOptions('flux2_klein_9b', {
        default_num_inference_steps: 17,
        default_guidance_scale: 3.5,
      }))
    }
    if (url.endsWith('/api/v1/defaults/flux2_klein_9b')) {
      return jsonResponse({ num_inference_steps: 17, guidance_scale: 3.5 })
    }
    if (url.endsWith('/api/v1/loras/flux2_klein_9b')) {
      return jsonResponse({ loras: [], guidance_max_phases: 1 })
    }
    throw new Error(`Unexpected fallback request: ${url}`)
  }, async useStore => {
    installTwoModes(useStore)
    useStore.setState(state => ({
      models: [
        ...state.models.filter(model => model.model_type !== 'test_image_model'),
        { model_type: 'flux2_klein_9b', family: 'flux' },
      ],
      selectedModelPerMode: { ...state.selectedModelPerMode, image: 'removed_image_model' },
      savedParamsPerMode: {
        ...state.savedParamsPerMode,
        image: { num_inference_steps: 99, guidance_scale: 9 },
      },
    }))

    useStore.getState().setGenerationMode('image')
    await new Promise(resolve => setTimeout(resolve, 10))
    const state = useStore.getState()
    assert.equal(state.params.model_type, 'flux2_klein_9b')
    assert.equal(state.selectedModelPerMode.image, 'flux2_klein_9b')
    assert.equal(state.params.num_inference_steps, 17)
    assert.equal(state.params.guidance_scale, 3.5)
    assert.equal(state.modelOptions.model_type, 'flux2_klein_9b')
  })
})
