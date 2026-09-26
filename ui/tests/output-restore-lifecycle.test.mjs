import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'
import ts from 'typescript'

const UI_ROOT = new URL('..', import.meta.url).pathname
const source = relative => readFile(new URL(relative, import.meta.url), 'utf8')

function asDataModule(contents) {
  return `data:text/javascript;base64,${Buffer.from(contents).toString('base64')}`
}

let storeBundlePromise
let storeRealmSequence = 0
function storeBundle() {
  storeBundlePromise ||= build({
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
  return import(`${asDataModule(await storeBundle())}#output-restore-${storeRealmSequence}`)
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

async function waitFor(predicate, label, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(`Timed out waiting for ${label}`)
    await new Promise(resolve => setTimeout(resolve, 5))
  }
}

const families = [
  { id: 'ltx2', label: 'LTX 2', order: 0 },
  { id: 'h3', label: 'MiniMax H3', order: 1 },
  { id: 'tts', label: 'Audio', order: 2 },
]

const models = [
  {
    model_type: 'ltx2_25', name: 'LTX-2.5', family: 'ltx2', architecture: 'ltx2_25',
    is_i2v: true, is_t2v: true, guidance_max_phases: 1, fps: 25,
  },
  {
    model_type: 'minimax_h3', name: 'MiniMax H3', family: 'h3', architecture: 'minimax_h3',
    is_i2v: true, is_t2v: true, guidance_max_phases: 2, fps: 24,
  },
  {
    model_type: 'minimax_h3_ref2va', name: 'MiniMax H3 Ref2VA', family: 'h3', architecture: 'minimax_h3',
    is_i2v: true, is_t2v: true, guidance_max_phases: 2, fps: 24,
  },
  {
    model_type: 'kugelaudio_0_open', name: 'KugelAudio 0 Open', family: 'tts', architecture: 'kugelaudio',
    is_i2v: false, is_t2v: false, guidance_max_phases: 1, fps: 1,
  },
  {
    model_type: 'ace_step_v1_5_turbo_lm_4b', name: 'ACE-Step Turbo LM 4B', family: 'tts', architecture: 'ace_step',
    is_i2v: false, is_t2v: false, guidance_max_phases: 1, fps: 1,
  },
  {
    model_type: 'scenema_audio', name: 'Scenema Audio', family: 'tts', architecture: 'scenema_audio',
    is_i2v: false, is_t2v: false, guidance_max_phases: 0, fps: 1,
  },
]

function modelOptions(modelType) {
  if (modelType === 'kugelaudio_0_open' || modelType === 'scenema_audio' || modelType.startsWith('ace_step')) {
    return {
      model_type: modelType,
      architecture: 'kugelaudio',
      fps: 1,
      frames_steps: 1,
      latent_size: 1,
      frames_minimum: 0,
      frames_maximum: 0,
      guidance_max_phases: 1,
      sliding_window: false,
      default_num_inference_steps: 0,
      default_guidance_scale: 1,
      audio_only: true,
      max_voice_count: modelType === 'scenema_audio' ? 2 : 6,
      resolutions: [],
      supports_end_frame: false,
    }
  }
  const h3 = modelType.startsWith('minimax_h3')
  return {
    model_type: modelType,
    architecture: h3 ? 'minimax_h3' : 'ltx2_25',
    fps: h3 ? 24 : 25,
    frames_steps: h3 ? 4 : 8,
    latent_size: h3 ? 4 : 8,
    frames_minimum: h3 ? 17 : 9,
    frames_maximum: 257,
    guidance_max_phases: h3 ? 2 : 1,
    sliding_window: false,
    default_num_inference_steps: h3 ? 28 : 20,
    default_guidance_scale: 1,
    audio_only: false,
    resolutions: ['1344x768', '1280x720'],
    supports_end_frame: true,
  }
}

function modelDefaults(modelType) {
  return modelType.startsWith('minimax_h3')
    ? {
        h3_default_profile_id: 'high',
        num_inference_steps: 28,
        resolution: '1344x768',
        guidance_scale: 1,
        custom_settings: { h3_attention_engine: 'sol_attn' },
        tea_cache: 0,
      }
    : {}
}

function output(name) {
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
    created_at: 1_788_739_200,
    revision: `${name}-revision`,
    workspace: 'default',
    private: false,
    explicit: false,
  }
}

function sidecar(params, uploadFilenames = {}) {
  return { source: 'sidecar', params, upload_filenames: uploadFilenames }
}

function baseParams(overrides = {}) {
  return {
    model_type: 'ltx2_25',
    prompt: 'saved output A',
    negative_prompt: '',
    image_mode: 0,
    resolution: '1280x720',
    video_length: 81,
    num_inference_steps: 20,
    guidance_scale: 1,
    seed: 41,
    ...overrides,
  }
}

function configureGallery(useStore, entries, selected = 0) {
  const selectedEntry = entries[selected]
  useStore.setState(state => ({
    activeWorkspace: 'default',
    outputs: entries.map(entry => output(entry.name)),
    outputsTotal: entries.length,
    selectedOutput: selected,
    selectedOutputMetaName: selectedEntry?.name || null,
    selectedOutputMeta: selectedEntry?.meta || null,
    metadataLoading: false,
    families,
    models,
    modelsLoaded: true,
    generationMode: 'video',
    selectedModelPerMode: { ...state.selectedModelPerMode, video: 'ltx2_25' },
    modelOptions: null,
    params: {
      ...state.params,
      model_type: 'ltx2_25',
      prompt: 'current unsaved prompt',
      image_mode: 0,
    },
  }))
}

function delayedResponse(overrides, url, response) {
  const requested = deferred()
  const released = deferred()
  overrides.set(url, async () => {
    requested.resolve()
    await released.promise
    return response()
  })
  return { requested: requested.promise, release: released.resolve }
}

function delayedBlobBody(overrides, url, type = 'image/png') {
  const requested = deferred()
  const bodyRequested = deferred()
  const body = deferred()
  overrides.set(url, async () => {
    requested.resolve()
    return {
      ok: true,
      blob: async () => {
        bodyRequested.resolve()
        return body.promise
      },
    }
  })
  return {
    requested: Promise.all([requested.promise, bodyRequested.promise]),
    release: () => body.resolve(new Blob([`body:${url}`], { type })),
  }
}

async function withStore(action, setup = {}) {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  const originalCreateObjectURL = URL.createObjectURL
  const storage = new StorageFake()
  const fetchOverrides = new Map()
  const metadata = new Map()
  const videoElements = []
  const requests = []
  const alerts = []

  storage.setItem('maestro_mode_settings', JSON.stringify({
    generationMode: 'video',
    selectedModelPerMode: { video: 'ltx2_25' },
    savedParamsPerMode: {},
    savedLoraPerMode: {},
    savedPromptPerMode: {},
  }))
  globalThis.localStorage = storage
  globalThis.sessionStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert(message) { alerts.push(String(message)) },
    location: { hostname: '127.0.0.1' },
    matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
  })
  globalThis.document = Object.assign(new EventTarget(), {
    hidden: false,
    createElement(tag) {
      if (tag !== 'video') return {}
      const video = {
        src: '', muted: false, duration: 0, videoWidth: 0, videoHeight: 0,
        onloadedmetadata: null,
      }
      videoElements.push(video)
      return video
    },
  })
  URL.createObjectURL = value => `blob:test-${value instanceof File ? value.name : 'asset'}`
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    const method = init.method || 'GET'
    requests.push({ method, url })
    const override = fetchOverrides.get(url)
    if (override) return override(input, init)
    if (url === '/api/v1/models') return Response.json({ families, models })
    if (url === '/api/v1/model-visibility' && method === 'GET') {
      return Response.json({ configured: true, enabled_models: models.map(model => model.model_type), defaults_version: 10 })
    }
    if (url.startsWith('/api/v1/model-options/')) {
      return Response.json(modelOptions(decodeURIComponent(url.split('/').pop())))
    }
    if (url.startsWith('/api/v1/defaults/')) {
      return Response.json(modelDefaults(decodeURIComponent(url.split('/').pop())))
    }
    if (url === '/api/v1/loras/installed') return Response.json({ loras: [] })
    if (url === '/api/v1/loras/check-updates') return Response.json({ status: 'fresh' })
    if (url.startsWith('/api/v1/loras/')) return Response.json({ loras: [], guidance_max_phases: 2 })
    if (url === '/api/v1/h3/estimate') {
      return Response.json(setup.h3Estimate || { profiles: [], current: { estimate: null } })
    }
    for (const [name, value] of metadata) {
      if (url.startsWith(`/api/v1/outputs/${encodeURIComponent(name)}/metadata`)) return Response.json(value)
    }
    if (url.startsWith('/api/v1/outputs')) return Response.json({ outputs: [], total: 0 })
    throw new Error(`Unexpected output-restore request: ${method} ${url}`)
  }

  try {
    const { useStore } = await loadStoreModuleFresh()
    return await action({ alerts, fetchOverrides, metadata, requests, useStore, videoElements })
  } finally {
    await settleAsyncWork()
    globalThis.fetch = originalFetch
    URL.createObjectURL = originalCreateObjectURL
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

test('gallery actions select their card and immediately start that card operation', async () => {
  const component = await source('../src/components/MainContent/MediaFeedItem.tsx')
  const mainContent = await source('../src/components/MainContent/MainContent.tsx')
  for (const [handler, operation] of [
    ['handleLoadSettings', 'loadSettingsFromOutput'],
    ['handleReroll', 'rerollGeneration'],
  ]) {
    const start = component.indexOf(`const ${handler} = useCallback`)
    assert.notEqual(start, -1, `found ${handler}`)
    const end = component.indexOf('\n  }, [', start)
    assert.notEqual(end, -1, `found end of ${handler}`)
    const body = component.slice(start, end)
    assert.equal(body.includes('setTimeout'), false, `${handler} must not defer across another gallery selection`)
    assert.ok(body.indexOf('onSelect(index)') < body.indexOf(`${operation}()`), `${handler} selects before ${operation}`)
  }

  const explicitSelectionStart = mainContent.indexOf('const handleItemSelect = useCallback')
  assert.notEqual(explicitSelectionStart, -1, 'found explicit card selection handler')
  const explicitSelectionEnd = mainContent.indexOf('\n  }, [', explicitSelectionStart)
  assert.notEqual(explicitSelectionEnd, -1, 'found end of explicit card selection handler')
  const explicitSelection = mainContent.slice(explicitSelectionStart, explicitSelectionEnd)
  assert.ok(
    explicitSelection.indexOf('scrollTarget.current = null') < explicitSelection.indexOf('setSelectedOutput(index)'),
    'explicit card selection cancels stale thumbnail alignment before publishing selection',
  )
  assert.ok(
    explicitSelection.indexOf('isUserScrolling.current = false') < explicitSelection.indexOf('setSelectedOutput(index)'),
    'explicit card selection blocks queued visibility callbacks before publishing selection',
  )

  await withStore(async ({ fetchOverrides, metadata, useStore }) => {
    const a = { name: 'same-model-a.mp4', meta: sidecar(baseParams({ prompt: 'A must lose' })) }
    const b = { name: 'same-model-b.mp4', meta: sidecar(baseParams({ prompt: 'B metadata' })) }
    metadata.set(b.name, b.meta)
    configureGallery(useStore, [a, b])
    const optionsGate = delayedResponse(
      fetchOverrides,
      '/api/v1/model-options/ltx2_25',
      () => Response.json(modelOptions('ltx2_25')),
    )

    const restore = useStore.getState().loadSettingsFromOutput()
    await optionsGate.requested
    useStore.getState().setSelectedOutput(1)
    await waitFor(() => useStore.getState().selectedOutputMetaName === b.name, 'output B metadata')
    optionsGate.release()

    assert.equal(await restore, false)
    assert.equal(useStore.getState().selectedOutput, 1)
    assert.equal(useStore.getState().selectedOutputMetaName, b.name)
    assert.equal(useStore.getState().params.prompt, 'current unsaved prompt')
  })
})

test('Gallery still guide outputs cannot restore or reroll as ordinary video settings', async () => {
  const guideMetadata = {
    ...sidecar(baseParams({
      model_type: 'minimax_h3',
      prompt: 'guided output prompt',
      image_mode: 1,
      image_start: 'source-still.png',
    })),
    h3_guide_execution: {
      capability: 'gallery_still_fl2va',
      frame_index: 62,
      target_frames: 124,
      guide_count: 1,
      audio_guides: 0,
      video_guides: 0,
    },
  }

  await withStore(async ({ alerts, requests, useStore }) => {
    const guidedOutput = { name: 'guided-output.mp4', meta: guideMetadata }
    configureGallery(useStore, [guidedOutput])
    let generationCalls = 0
    useStore.setState({ startGeneration: async () => { generationCalls += 1 } })
    const initialParams = useStore.getState().params

    assert.equal(await useStore.getState().loadSettingsFromOutput(), false)
    assert.strictEqual(useStore.getState().params, initialParams)
    assert.match(alerts.at(-1), /Select the source still in Gallery.*Use still as guide/i)
    assert.deepEqual(requests, [], 'cached guide metadata is rejected before network or hydration work')

    await useStore.getState().rerollGeneration()
    assert.equal(generationCalls, 0)
    assert.strictEqual(useStore.getState().params, initialParams)
    assert.deepEqual(requests, [])
  })

  await withStore(async ({ alerts, metadata, requests, useStore }) => {
    const embeddedGuideMetadata = {
      source: 'embedded',
      params: baseParams({
        model_type: 'minimax_h3',
        prompt: 'embedded guided output prompt',
        image_mode: 1,
        image_start: 'source-still.png',
        custom_settings: { _h3_timeline_still_guide: { frame_index: 62 } },
      }),
      upload_filenames: {},
    }
    assert.equal(Object.hasOwn(embeddedGuideMetadata, 'h3_guide_execution'), false)
    const guidedOutput = { name: 'guide-embedded-metadata-refresh.mp4', meta: null }
    metadata.set(guidedOutput.name, embeddedGuideMetadata)
    configureGallery(useStore, [guidedOutput])
    const initialParams = useStore.getState().params

    assert.equal(await useStore.getState().loadSettingsFromOutput(), false)
    assert.strictEqual(useStore.getState().params, initialParams)
    assert.match(alerts.at(-1), /Select the source still in Gallery.*Use still as guide/i)
    assert.ok(
      requests.some(({ url }) => url.startsWith(`/api/v1/outputs/${encodeURIComponent(guidedOutput.name)}/metadata`)),
      'sidecarless metadata is refreshed before the embedded marker guard runs',
    )
    assert.equal(
      requests.some(({ url }) => url.startsWith('/api/v1/model-options/') || url.startsWith('/api/v1/file/')),
      false,
      'embedded guide metadata is rejected before model-option or image hydration requests',
    )
  })
})

test('Gallery still guide controls hide generic settings, recipe, and reroll actions', async () => {
  const [card, infoBar] = await Promise.all([
    source('../src/components/MainContent/MediaFeedItem.tsx'),
    source('../src/components/MainContent/VideoInfoBar.tsx'),
  ])
  assert.ok(card.includes('isH3GalleryStillGuideOutput(meta)'), 'the card checks the server guide capability')
  assert.ok(card.includes('H3_GALLERY_STILL_GUIDE_RESTORE_MESSAGE'), 'the card provides actionable guidance')
  assert.ok(card.includes('{!isGalleryStillGuideOutput && ('), 'the card gates its recipe/load/reroll controls')
  assert.ok(infoBar.includes('isH3GalleryStillGuideOutput(meta)'), 'the info bar checks the server guide capability')
  assert.ok(infoBar.includes('H3_GALLERY_STILL_GUIDE_RESTORE_MESSAGE'), 'the info bar provides actionable guidance')
  assert.ok(infoBar.includes('{!isGalleryStillGuideOutput && ('), 'the info bar gates load/reroll controls')
})

test('selecting another output immediately restores that card across generation modes', async () => {
  await withStore(async ({ metadata, requests, useStore }) => {
    const audio = {
      name: 'current-audio.wav',
      meta: sidecar(baseParams({ model_type: 'ace_step_v1_5_turbo_lm_4b', prompt: 'current song' })),
    }
    const video = {
      name: 'selected-video.mp4',
      meta: sidecar(baseParams({
        model_type: 'minimax_h3',
        prompt: 'restore this video',
        resolution: '608x352',
        video_length: 124,
      })),
    }
    metadata.set(video.name, video.meta)
    configureGallery(useStore, [audio, video])
    useStore.setState(state => ({
      generationMode: 'audio',
      selectedModelPerMode: { ...state.selectedModelPerMode, audio: 'ace_step_v1_5_turbo_lm_4b' },
      params: { ...state.params, model_type: 'ace_step_v1_5_turbo_lm_4b', prompt: 'current song' },
    }))

    useStore.getState().setSelectedOutput(1)
    assert.equal(await useStore.getState().loadSettingsFromOutput(), true)

    const state = useStore.getState()
    assert.equal(state.selectedOutput, 1)
    assert.equal(state.selectedOutputMetaName, video.name)
    assert.equal(state.generationMode, 'video')
    assert.equal(state.params.model_type, 'minimax_h3')
    assert.equal(state.params.prompt, 'restore this video')
    assert.equal(state.params.resolution, '608x352')
    assert.ok(requests.some(({ url }) => url === '/api/v1/model-options/minimax_h3'))
  })
})

test('multi-speaker output restores safe voice identities and replaces stale guide slots', async () => {
  await withStore(async ({ fetchOverrides, useStore }) => {
    const speech = {
      name: 'three-speaker.wav',
      meta: sidecar({
        ...baseParams({
          model_type: 'kugelaudio_0_open',
          prompt: 'Speaker 1: First\nSpeaker 2: Second\nSpeaker 3: Third',
          video_length: 0,
        }),
        _audio_sub_mode: 'speech',
        _tts_original_prompt: 'Alice: First\nBob: Second\nCarol: Third',
        _tts_voice_count: 3,
        _tts_speaker_name1: 'Alice',
        _tts_speaker_name2: 'Bob',
        _tts_speaker_name3: 'Carol',
        audio_guide: '/authorized/uploads/alice.wav',
        audio_guide2: '/authorized/uploads/bob.wav',
        audio_guide3: '/authorized/uploads/carol.wav',
      }, {
        audio_guide: 'alice.wav',
        audio_guide2: String.raw`C:\published\bob.wav`,
        audio_guide3: '/published/carol.wav',
      }),
    }
    configureGallery(useStore, [speech])
    useStore.setState(state => ({
      params: {
        ...state.params,
        audio_guide: '/stale/one.wav',
        audio_guide2: '/stale/two.wav',
        audio_guide3: '/stale/three.wav',
        audio_guide4: '/stale/four.wav',
        audio_guide5: '/stale/five.wav',
        audio_guide6: '/stale/six.wav',
      },
      ttsVoiceCount: 6,
      ttsVoices: Array.from({ length: 6 }, (_, index) => ({
        name: `Stale ${index + 1}`,
        filename: `stale-${index + 1}.wav`,
        path: `/stale/${index + 1}.wav`,
      })),
    }))

    assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
    const restored = useStore.getState()
    assert.equal(restored.generationMode, 'audio')
    assert.equal(restored.audioSubMode, 'speech')
    assert.equal(restored.ttsVoiceCount, 3)
    assert.deepEqual(restored.ttsVoices, [
      { name: 'Alice', filename: 'alice.wav', path: '/authorized/uploads/alice.wav' },
      { name: 'Bob', filename: 'bob.wav', path: '/authorized/uploads/bob.wav' },
      { name: 'Carol', filename: 'carol.wav', path: '/authorized/uploads/carol.wav' },
    ])
    assert.equal(restored.params.audio_guide, '/authorized/uploads/alice.wav')
    assert.equal(restored.params.audio_guide2, '/authorized/uploads/bob.wav')
    assert.equal(restored.params.audio_guide3, '/authorized/uploads/carol.wav')
    for (let i = 4; i <= 6; i += 1) assert.equal(restored.params[`audio_guide${i}`], '')

    let submitted
    fetchOverrides.set('/api/v1/generate', async (_url, init) => {
      submitted = JSON.parse(init.body)
      return Response.json({ job_id: 'synthetic-speech', status: 'queued' })
    })
    useStore.setState({ _pollRecoveredJob: () => {} })
    await useStore.getState().startGeneration()
    assert.ok(submitted, 'restored speech request was submitted')
    assert.equal(submitted.audio_guide, '/authorized/uploads/alice.wav')
    assert.equal(submitted.audio_guide2, '/authorized/uploads/bob.wav')
    assert.equal(submitted.audio_guide3, '/authorized/uploads/carol.wav')
    for (let i = 4; i <= 6; i += 1) assert.equal(Object.hasOwn(submitted, `audio_guide${i}`), false)
  })
})

test('music output without speaker rows preserves its ordinary source audio', async () => {
  await withStore(async ({ fetchOverrides, useStore }) => {
    const music = {
      name: 'source-audio-song.wav',
      meta: sidecar({
        ...baseParams({
          model_type: 'ace_step_v1_5_turbo_lm_4b',
          prompt: '[Verse] Keep the source',
          video_length: 0,
        }),
        _audio_sub_mode: 'music',
        _tts_voice_count: 0,
        audio_guide: '/authorized/uploads/source-song.wav',
      }, { audio_guide: 'source-song.wav' }),
    }
    configureGallery(useStore, [music])
    useStore.setState({
      ttsVoiceCount: 3,
      ttsVoices: [
        { name: 'Old A', filename: 'old-a.wav', path: '/stale/old-a.wav' },
        { name: 'Old B', filename: 'old-b.wav', path: '/stale/old-b.wav' },
        { name: 'Old C', filename: 'old-c.wav', path: '/stale/old-c.wav' },
      ],
    })

    assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
    const restored = useStore.getState()
    assert.equal(restored.generationMode, 'audio')
    assert.equal(restored.audioSubMode, 'music')
    assert.equal(restored.ttsVoiceCount, 0)
    assert.deepEqual(restored.ttsVoices, [])
    assert.equal(restored.params.audio_guide, '/authorized/uploads/source-song.wav')

    let submitted
    fetchOverrides.set('/api/v1/generate', async (_url, init) => {
      submitted = JSON.parse(init.body)
      return Response.json({ job_id: 'synthetic-music', status: 'queued' })
    })
    useStore.setState({ _pollRecoveredJob: () => {} })
    await useStore.getState().startGeneration()
    assert.equal(submitted.audio_guide, '/authorized/uploads/source-song.wav')
  })
})

test('manual Speech to Music switch ignores stale speaker rows and preserves source audio', async () => {
  await withStore(async ({ fetchOverrides, useStore }) => {
    useStore.setState(state => ({
      generationMode: 'audio',
      audioSubMode: 'speech',
      selectedModelPerAudioSubMode: {
        ...state.selectedModelPerAudioSubMode,
        music: 'ace_step_v1_5_turbo_lm_4b',
      },
      ttsVoiceCount: 2,
      ttsVoices: [
        { name: 'Old A', filename: 'old-a.wav', path: '/stale/old-a.wav' },
        { name: 'Old B', filename: 'old-b.wav', path: '/stale/old-b.wav' },
      ],
      params: {
        ...state.params,
        audio_guide: '/stale/old-a.wav',
        audio_guide2: '/stale/old-b.wav',
        audio_guide6: '/stale/old-f.wav',
        audio_prompt_type: 'AB',
      },
      audioGuideFilename: 'old-a.wav',
      audioGuide2Filename: 'old-b.wav',
    }))
    useStore.getState().setAudioSubMode('music')
    assert.equal(useStore.getState().audioSubMode, 'music')
    for (let i = 1; i <= 6; i += 1) {
      const key = i === 1 ? 'audio_guide' : `audio_guide${i}`
      assert.equal(Object.hasOwn(useStore.getState().params, key), false)
    }
    assert.equal(useStore.getState().audioGuideFilename, null)
    assert.equal(useStore.getState().audioGuide2Filename, null)
    assert.equal(String(useStore.getState().params.audio_prompt_type || ''), '')
    useStore.setState(state => ({
      params: {
        ...state.params,
        model_type: 'ace_step_v1_5_turbo_lm_4b',
        prompt: '[Verse] Keep this lyric intact',
        audio_guide: '/authorized/uploads/source-song.wav',
        audio_guide2: '/authorized/uploads/reference-timbre.wav',
        audio_prompt_type: 'AB',
      },
      modelOptions: modelOptions('ace_step_v1_5_turbo_lm_4b'),
    }))

    let submitted
    fetchOverrides.set('/api/v1/generate', async (_url, init) => {
      submitted = JSON.parse(init.body)
      return Response.json({ job_id: 'manual-switch-music', status: 'queued' })
    })
    useStore.setState({ _pollRecoveredJob: () => {} })
    await useStore.getState().startGeneration()

    assert.equal(submitted.audio_guide, '/authorized/uploads/source-song.wav')
    assert.equal(submitted.audio_guide2, '/authorized/uploads/reference-timbre.wav')
    for (let i = 3; i <= 6; i += 1) {
      assert.equal(Object.hasOwn(submitted, `audio_guide${i}`), false)
    }
    assert.equal(submitted.prompt, '[Verse] Keep this lyric intact')
    assert.equal(Object.hasOwn(submitted, '_tts_voice_count'), false)
    for (let i = 1; i <= 6; i += 1) {
      assert.equal(Object.hasOwn(submitted, `_tts_speaker_name${i}`), false)
    }
  })
})

test('Speech to Music to Speech round trip cannot resurrect detached voice files', async () => {
  await withStore(async ({ fetchOverrides, useStore }) => {
    useStore.setState(state => ({
      generationMode: 'audio',
      audioSubMode: 'speech',
      ttsVoiceCount: 2,
      ttsVoices: [
        { name: 'Alice', filename: 'alice.wav', path: '/stale/alice.wav' },
        { name: 'Bob', filename: 'bob.wav', path: '/stale/bob.wav' },
      ],
      params: {
        ...state.params,
        audio_guide: '/stale/alice.wav',
        audio_guide2: '/stale/bob.wav',
      },
    }))

    useStore.getState().setAudioSubMode('music')
    assert.deepEqual(useStore.getState().ttsVoices, [
      { name: 'Alice', filename: null, path: null },
      { name: 'Bob', filename: null, path: null },
    ])
    useStore.getState().setAudioSubMode('speech')
    useStore.setState(state => ({
      params: {
        ...state.params,
        model_type: 'kugelaudio_0_open',
        prompt: 'Alice: Reattach me explicitly',
      },
      modelOptions: modelOptions('kugelaudio_0_open'),
    }))

    let submitted
    fetchOverrides.set('/api/v1/generate', async (_url, init) => {
      submitted = JSON.parse(init.body)
      return Response.json({ job_id: 'round-trip-speech', status: 'queued' })
    })
    useStore.setState({ _pollRecoveredJob: () => {} })
    await useStore.getState().startGeneration()
    for (let i = 1; i <= 6; i += 1) {
      const key = i === 1 ? 'audio_guide' : `audio_guide${i}`
      assert.equal(Object.hasOwn(submitted, key), false)
    }
  })
})

test('Speech to Music switch resets the audio task so text-only Music can submit', async () => {
  await withStore(async ({ fetchOverrides, useStore }) => {
    useStore.setState(state => ({
      generationMode: 'audio',
      audioSubMode: 'speech',
      params: {
        ...state.params,
        audio_prompt_type: 'AB',
        audio_guide: '/stale/speaker-a.wav',
        audio_guide2: '/stale/speaker-b.wav',
      },
    }))
    useStore.getState().setAudioSubMode('music')
    useStore.setState(state => ({
      params: {
        ...state.params,
        model_type: 'ace_step_v1_5_turbo_lm_4b',
        prompt: '[Instrumental]',
      },
      modelOptions: modelOptions('ace_step_v1_5_turbo_lm_4b'),
    }))

    let submitted
    fetchOverrides.set('/api/v1/generate', async (_url, init) => {
      submitted = JSON.parse(init.body)
      return Response.json({ job_id: 'text-only-music', status: 'queued' })
    })
    useStore.setState({ _pollRecoveredJob: () => {} })
    await useStore.getState().startGeneration()
    assert.ok(submitted, 'text-only Music request was submitted')
    assert.equal(String(submitted.audio_prompt_type || ''), '')
    assert.equal(Object.hasOwn(submitted, 'audio_guide'), false)
    assert.equal(Object.hasOwn(submitted, 'audio_guide2'), false)
  })
})

test('text-only Speech clears prior voices and removing the final voice clears every guide', async () => {
  await withStore(async ({ fetchOverrides, useStore }) => {
    const speech = {
      name: 'text-only-speech.wav',
      meta: sidecar({
        ...baseParams({ model_type: 'kugelaudio_0_open', prompt: 'Narration only', video_length: 0 }),
        _audio_sub_mode: 'speech',
        _tts_original_prompt: 'Narration only',
        _tts_voice_count: 0,
      }),
    }
    configureGallery(useStore, [speech])
    useStore.setState(state => ({
      ttsVoiceCount: 2,
      ttsVoices: [
        { name: 'Old A', filename: 'old-a.wav', path: '/stale/old-a.wav' },
        { name: 'Old B', filename: 'old-b.wav', path: '/stale/old-b.wav' },
      ],
      params: {
        ...state.params,
        audio_guide: '/stale/old-a.wav',
        audio_guide2: '/stale/old-b.wav',
        audio_guide6: '/stale/old-f.wav',
      },
    }))

    assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
    assert.equal(useStore.getState().ttsVoiceCount, 0)
    assert.deepEqual(useStore.getState().ttsVoices, [])

    let submitted
    fetchOverrides.set('/api/v1/generate', async (_url, init) => {
      submitted = JSON.parse(init.body)
      return Response.json({ job_id: 'synthetic-text-speech', status: 'queued' })
    })
    useStore.setState({ _pollRecoveredJob: () => {} })
    await useStore.getState().startGeneration()
    for (let i = 1; i <= 6; i += 1) {
      const key = i === 1 ? 'audio_guide' : `audio_guide${i}`
      assert.equal(Object.hasOwn(submitted, key), false)
    }

    useStore.setState(state => ({
      ttsVoiceCount: 1,
      ttsVoices: [{ name: 'Temporary', filename: 'temporary.wav', path: '/uploads/temporary.wav' }],
      params: { ...state.params, audio_guide: '/uploads/temporary.wav', audio_guide6: '/stale/six.wav' },
    }))
    useStore.getState().removeTtsVoice(0)
    assert.equal(useStore.getState().ttsVoiceCount, 0)
    assert.deepEqual(useStore.getState().ttsVoices, [])
    for (let i = 1; i <= 6; i += 1) {
      const key = i === 1 ? 'audio_guide' : `audio_guide${i}`
      assert.equal(Object.hasOwn(useStore.getState().params, key), false)
    }
  })
})

test('malformed saved TTS voice counts stay within the six-slot contract', async () => {
  for (const [voiceCount, expected] of [[-4, 0], [1.5, 0], [99, 6]]) {
    await withStore(async ({ useStore }) => {
      const entry = {
        name: `malformed-${String(voiceCount)}.wav`,
        meta: sidecar({
          ...baseParams({ model_type: 'kugelaudio_0_open', prompt: 'Bounded voices', video_length: 0 }),
          _audio_sub_mode: 'speech',
          _tts_voice_count: voiceCount,
        }),
      }
      configureGallery(useStore, [entry])
      assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
      assert.equal(useStore.getState().ttsVoiceCount, expected)
      assert.equal(useStore.getState().ttsVoices.length, expected)
    })
  }
})

test('restored TTS voices respect the selected model voice limit', async () => {
  await withStore(async ({ useStore }) => {
    const entry = {
      name: 'bounded-scenema.wav',
      meta: sidecar({
        ...baseParams({ model_type: 'scenema_audio', prompt: 'Two voices only', video_length: 0 }),
        _audio_sub_mode: 'speech',
        _tts_voice_count: 99,
        _tts_speaker_name1: 'Alice',
        _tts_speaker_name2: 'Bob',
        _tts_speaker_name3: 'Carol',
        audio_guide: '/authorized/uploads/alice.wav',
        audio_guide2: '/authorized/uploads/bob.wav',
        audio_guide3: '/authorized/uploads/carol.wav',
      }),
    }
    configureGallery(useStore, [entry])
    assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
    assert.equal(useStore.getState().ttsVoiceCount, 2)
    assert.deepEqual(useStore.getState().ttsVoices.map(voice => voice.name), ['Alice', 'Bob'])
  })
})

test('manual parameter, UI, and authored-field edits win over delayed model-option hydration', async () => {
  const mappings = [{ id: 'manual-map', source: 'coat', target: 'jacket' }]
  const scenarios = [
    {
      label: 'parameter and catalog UI',
      mutate(state) {
        state.setParam('prompt', 'manual prompt while loading')
        state.setFilmGrainIntensity(0.73)
      },
      verify(state) {
        assert.equal(state.params.prompt, 'manual prompt while loading')
        assert.equal(state.filmGrainIntensity, 0.73)
      },
    },
    {
      label: 'music description',
      mutate(state) { state.setMusicDescription('manual song concept') },
      verify(state) { assert.equal(state.musicDescription, 'manual song concept') },
    },
    {
      label: 'repaint mappings',
      mutate(state) { state.setEditRepaintMappings(mappings) },
      verify(state) { assert.deepEqual(state.editRepaintMappings, mappings) },
    },
    {
      label: 'cached edit mask',
      mutate(_state, useStore) { useStore.setState({ editMasksPath: '/manual/mask.json' }) },
      verify(state) { assert.equal(state.editMasksPath, '/manual/mask.json') },
    },
  ]

  for (const scenario of scenarios) {
    await withStore(async ({ fetchOverrides, useStore }) => {
      const a = {
        name: `${scenario.label}.mp4`,
        meta: sidecar(baseParams({ prompt: 'saved prompt', film_grain_intensity: 0.2 })),
      }
      configureGallery(useStore, [a])
      const optionsGate = delayedResponse(
        fetchOverrides,
        '/api/v1/model-options/ltx2_25',
        () => Response.json(modelOptions('ltx2_25')),
      )

      const restore = useStore.getState().loadSettingsFromOutput()
      await optionsGate.requested
      scenario.mutate(useStore.getState(), useStore)
      optionsGate.release()

      assert.equal(await restore, false, scenario.label)
      scenario.verify(useStore.getState())
    })
  }
})

test('an unavailable saved model fails before reads or editor mutation and cannot reroll', async () => {
  await withStore(async ({ alerts, requests, useStore }) => {
    const missing = {
      name: 'missing-model.mp4',
      meta: sidecar(baseParams({ model_type: 'removed_catalog_model', prompt: 'must not apply' })),
    }
    configureGallery(useStore, [missing])
    const originalModeBucket = { prompt: 'original mode work', image_mode: 0, video_length: 81 }
    const originalVideoStash = { 0: { params: { prompt: 'original stash' } } }
    useStore.setState(state => ({
      savedParamsPerMode: { ...state.savedParamsPerMode, video: originalModeBucket },
      videoSubModeStash: originalVideoStash,
      selectedGenerationProfileId: 'current-studio-profile',
      selectedDirectorProfileId: 'current-director-profile',
    }))
    let generationCalls = 0
    useStore.setState({ startGeneration: async () => { generationCalls += 1 } })
    const before = useStore.getState()

    assert.equal(await useStore.getState().loadSettingsFromOutput(), false)
    assert.deepEqual(requests, [])
    assert.strictEqual(useStore.getState().params, before.params)
    assert.strictEqual(useStore.getState().savedParamsPerMode.video, originalModeBucket)
    assert.strictEqual(useStore.getState().videoSubModeStash, originalVideoStash)
    assert.equal(useStore.getState().generationMode, before.generationMode)
    assert.equal(useStore.getState().modelOptions, before.modelOptions)
    assert.equal(useStore.getState().selectedGenerationProfileId, 'current-studio-profile')
    assert.equal(useStore.getState().selectedDirectorProfileId, 'current-director-profile')
    assert.match(alerts.at(-1), /model is unavailable/i)

    await useStore.getState().rerollGeneration()
    await settleAsyncWork()
    assert.equal(generationCalls, 0)
    assert.deepEqual(requests, [])
    assert.strictEqual(useStore.getState().savedParamsPerMode.video, originalModeBucket)
  })
})

test('every direct restore write is covered by the shared UI catalog, authored guard, or lifecycle bookkeeping', async () => {
  const [storeText, schemaText] = await Promise.all([
    source('../src/stores/useStore.ts'),
    source('../../app/services/generation_profile_fields.json'),
  ])
  const schema = JSON.parse(schemaText)
  const sourceFile = ts.createSourceFile('useStore.ts', storeText, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  let restoreFunction = null
  const findRestore = node => {
    if (
      ts.isPropertyAssignment(node)
      && node.name.getText(sourceFile) === 'loadSettingsFromOutput'
    ) restoreFunction = node.initializer
    else if (!restoreFunction) ts.forEachChild(node, findRestore)
  }
  findRestore(sourceFile)
  assert.ok(restoreFunction, 'loadSettingsFromOutput exists')

  const unwrap = expression => {
    let current = expression
    while (
      ts.isAsExpression(current)
      || ts.isSatisfiesExpression(current)
      || ts.isParenthesizedExpression(current)
    ) current = current.expression
    return current
  }
  let authoredFields = []
  const findAuthoredFields = node => {
    if (ts.isVariableDeclaration(node) && node.name.getText(sourceFile) === 'authoredFields') {
      const initializer = unwrap(node.initializer)
      assert.ok(ts.isArrayLiteralExpression(initializer), 'authoredFields is a literal typed catalog')
      authoredFields = initializer.elements.map(element => element.text)
    }
    ts.forEachChild(node, findAuthoredFields)
  }
  findAuthoredFields(restoreFunction)
  assert.ok(authoredFields.length > 0, 'authoredFields guard is declared')

  const directWrites = new Set()
  const mergedSpreads = new Set()
  const unsupportedSetShapes = []
  const propertyName = property => property.name?.getText(sourceFile).replace(/^['"]|['"]$/g, '')
  const collectSpread = expression => {
    const value = unwrap(expression)
    if (ts.isObjectLiteralExpression(value)) {
      collectObject(value)
    } else if (ts.isConditionalExpression(value)) {
      collectSpread(value.whenTrue)
      collectSpread(value.whenFalse)
    } else if (ts.isIdentifier(value)) {
      mergedSpreads.add(value.text)
    } else {
      unsupportedSetShapes.push(value.getText(sourceFile))
    }
  }
  const collectObject = object => {
    for (const property of object.properties) {
      if (ts.isSpreadAssignment(property)) collectSpread(property.expression)
      else if (property.name) directWrites.add(propertyName(property))
    }
  }
  const collectSetWrites = node => {
    if (
      ts.isCallExpression(node)
      && ts.isIdentifier(node.expression)
      && node.expression.text === 'set'
    ) {
      let argument = node.arguments[0]
      if (ts.isArrowFunction(argument)) argument = unwrap(argument.body)
      if (ts.isObjectLiteralExpression(argument)) collectObject(argument)
      else unsupportedSetShapes.push(argument?.getText(sourceFile) || '(missing argument)')
    }
    ts.forEachChild(node, collectSetWrites)
  }
  collectSetWrites(restoreFunction)

  const lifecycleBookkeeping = new Set([
    'availableLoras',
    'h3EstimateError',
    'modelOptions',
    'modelOptionsLoading',
    'savedParamsPerMode',
    'selectedDirectorProfileId',
    'selectedGenerationProfileId',
    'selectedModelPerAudioSubMode',
    'selectedModelPerMode',
  ])
  const classified = new Set([
    ...Object.keys(schema.ui),
    ...authoredFields,
    ...lifecycleBookkeeping,
  ])
  assert.deepEqual(unsupportedSetShapes, [])
  assert.deepEqual([...mergedSpreads].sort(), ['restoredBlendMedia', 'timingState'])
  assert.deepEqual(
    [...directWrites].filter(field => !classified.has(field)).sort(),
    [],
    'new restore writes need a shared-schema, authored-field, or lifecycle classification',
  )
})

test('late standard and multi-clip image bodies cannot land on another selection', async () => {
  const scenarios = [
    {
      label: 'start, end, and references',
      params: baseParams({
        image_start: '/uploads/start-a.png',
        image_end: '/uploads/end-a.png',
        image_refs: ['/outputs/ref-a.png'],
      }),
      uploads: { image_start: 'start-a.png', image_end: 'end-a.png' },
      urls: ['/api/v1/uploads/start-a.png', '/api/v1/uploads/end-a.png', '/api/v1/file/ref-a.png'],
      verify(state) {
        assert.equal(state.startImage, null)
        assert.equal(state.endImage, null)
        assert.deepEqual(state.imageRefs, [])
      },
    },
    {
      label: 'multi-clip start images',
      params: baseParams({
        prompt: 'first clip\nsecond clip',
        multi_prompts_gen_type: 3,
        image_start: ['/uploads/clip-a.png', '/uploads/clip-b.png'],
      }),
      uploads: { image_start: ['clip-a.png', 'clip-b.png'] },
      urls: ['/api/v1/uploads/clip-a.png', '/api/v1/uploads/clip-b.png'],
      verify(state) {
        assert.equal(state.clips.length, 2)
        assert.deepEqual(state.clips.map(clip => clip.startImage), [null, null])
      },
    },
  ]

  for (const scenario of scenarios) {
    await withStore(async ({ fetchOverrides, metadata, useStore }) => {
      const a = { name: `${scenario.label}-a.mp4`, meta: sidecar(scenario.params, scenario.uploads) }
      const b = { name: `${scenario.label}-b.mp4`, meta: sidecar(baseParams({ prompt: 'new selection' })) }
      metadata.set(b.name, b.meta)
      configureGallery(useStore, [a, b])
      const bodies = scenario.urls.map(url => delayedBlobBody(fetchOverrides, url))

      await useStore.getState().loadSettingsFromOutput()
      await Promise.all(bodies.map(body => body.requested))
      useStore.getState().setSelectedOutput(1)
      await waitFor(() => useStore.getState().selectedOutputMetaName === b.name, `${scenario.label} output B metadata`)
      bodies.forEach(body => body.release())
      await settleAsyncWork()

      scenario.verify(useStore.getState())
    })
  }
})

test('late edit assets are fenced and a successful edit-video load preserves its saved trim', async () => {
  const staleCases = [
    {
      label: 'edit video',
      params: baseParams({ edit_sub_mode: 'retake', edit_video_path: '/outputs/edit-a.mp4', edit_end_time: 4 }),
      url: '/api/v1/file/edit-a.mp4',
      beforeRelease({ videoElements }) {
        assert.equal(videoElements.length, 1)
        Object.assign(videoElements[0], { duration: 12, videoWidth: 1280, videoHeight: 720 })
        videoElements[0].onloadedmetadata()
      },
      verify(state) {
        assert.equal(state.editVideoFile, null)
        assert.equal(state.editEndTime, 4)
      },
      type: 'video/mp4',
    },
    {
      label: 'repaint frame',
      params: baseParams({ edit_sub_mode: 'restyle', edit_repaint_target_frame: '/uploads/repaint-a.png' }),
      url: '/api/v1/file/repaint-a.png',
      beforeRelease() {},
      verify(state) { assert.equal(state.editRepaintFrameFile, null) },
      type: 'image/png',
    },
    {
      label: 'recast reference',
      params: baseParams({ edit_sub_mode: 'recast', edit_recast_ref_path: '/uploads/recast-a.png' }),
      url: '/api/v1/file/recast-a.png',
      beforeRelease() {},
      verify(state) { assert.equal(state.editRecastRefFile, null) },
      type: 'image/png',
    },
  ]

  for (const scenario of staleCases) {
    await withStore(async ({ fetchOverrides, metadata, useStore, videoElements }) => {
      const a = { name: `${scenario.label}-a.mp4`, meta: sidecar(scenario.params) }
      const b = { name: `${scenario.label}-b.mp4`, meta: sidecar(baseParams({ prompt: 'new selection' })) }
      metadata.set(b.name, b.meta)
      configureGallery(useStore, [a, b])
      const body = delayedBlobBody(fetchOverrides, scenario.url, scenario.type)

      await useStore.getState().loadSettingsFromOutput()
      scenario.beforeRelease({ videoElements })
      await body.requested
      useStore.getState().setSelectedOutput(1)
      await waitFor(() => useStore.getState().selectedOutputMetaName === b.name, `${scenario.label} output B metadata`)
      body.release()
      await settleAsyncWork()

      scenario.verify(useStore.getState())
    })
  }

  await withStore(async ({ fetchOverrides, metadata, useStore, videoElements }) => {
    let sourceFetches = 0
    const a = {
      name: 'metadata-after-selection-a.mp4',
      meta: sidecar(baseParams({ edit_sub_mode: 'retake', edit_video_path: '/outputs/metadata-after-selection.mp4' })),
    }
    const b = { name: 'metadata-after-selection-b.mp4', meta: sidecar(baseParams({ prompt: 'new selection' })) }
    metadata.set(b.name, b.meta)
    configureGallery(useStore, [a, b])
    fetchOverrides.set('/api/v1/file/metadata-after-selection.mp4', async () => {
      sourceFetches += 1
      return Response.json({})
    })

    await useStore.getState().loadSettingsFromOutput()
    assert.equal(videoElements.length, 1)
    useStore.getState().setSelectedOutput(1)
    await waitFor(() => useStore.getState().selectedOutputMetaName === b.name, 'post-selection metadata')
    Object.assign(videoElements[0], { duration: 12, videoWidth: 1280, videoHeight: 720 })
    videoElements[0].onloadedmetadata()
    await settleAsyncWork()

    assert.equal(sourceFetches, 0)
    assert.equal(useStore.getState().editVideoFile, null)
  })

  await withStore(async ({ fetchOverrides, useStore, videoElements }) => {
    const a = {
      name: 'trim-preservation.mp4',
      meta: sidecar(baseParams({
        edit_sub_mode: 'retake',
        edit_video_path: '/outputs/trim-source.mp4',
        edit_start_time: 1,
        edit_end_time: 4,
      })),
    }
    configureGallery(useStore, [a])
    const body = delayedBlobBody(fetchOverrides, '/api/v1/file/trim-source.mp4', 'video/mp4')

    await useStore.getState().loadSettingsFromOutput()
    assert.equal(videoElements.length, 1)
    Object.assign(videoElements[0], { duration: 12, videoWidth: 1920, videoHeight: 1080 })
    videoElements[0].onloadedmetadata()
    await body.requested
    body.release()
    await settleAsyncWork()

    const state = useStore.getState()
    assert.equal(state.editVideoFile?.name, 'trim-source.mp4')
    assert.equal(state.editVideoDuration, 12)
    assert.equal(state.editStartTime, 1)
    assert.equal(state.editEndTime, 4)
  })
})

test('reroll never generates when settings are missing or cancelled', async () => {
  await withStore(async ({ useStore }) => {
    let generationCalls = 0
    configureGallery(useStore, [])
    useStore.setState({ startGeneration: async () => { generationCalls += 1 } })

    await useStore.getState().rerollGeneration()
    await new Promise(resolve => setTimeout(resolve, 130))
    assert.equal(generationCalls, 0)
  })

  await withStore(async ({ fetchOverrides, useStore }) => {
    let generationCalls = 0
    const a = { name: 'cancelled-reroll.mp4', meta: sidecar(baseParams({ prompt: 'stale reroll' })) }
    configureGallery(useStore, [a])
    useStore.setState({ startGeneration: async () => { generationCalls += 1 } })
    const optionsGate = delayedResponse(
      fetchOverrides,
      '/api/v1/model-options/ltx2_25',
      () => Response.json(modelOptions('ltx2_25')),
    )

    const reroll = useStore.getState().rerollGeneration()
    await optionsGate.requested
    useStore.getState().setParam('prompt', 'manual cancellation')
    optionsGate.release()
    await reroll
    await new Promise(resolve => setTimeout(resolve, 130))

    assert.equal(generationCalls, 0)
    assert.equal(useStore.getState().params.prompt, 'manual cancellation')
  })
})

test('H3 estimate matches and fallback suggestions keep restored settings exact and custom', async () => {
  const exactSettings = {
    model_type: 'minimax_h3',
    num_inference_steps: 17,
    resolution: '1344x768',
    custom_settings: { h3_attention_engine: 'sdpa', h3_sol_tau: 1.25 },
    activated_loras: [],
    loras_multipliers: '',
    lora_weights: {},
    tea_cache: 0,
    spatial_upsampling: '',
    delivery_resolution: '',
    delivery_fit: '',
  }
  const fallbackSettings = {
    ...exactSettings,
    num_inference_steps: 28,
    custom_settings: { h3_attention_engine: 'sol_attn' },
  }
  const cases = [
    { id: 'quality', available: true, fallback: null },
    { id: 'fast', available: false, fallback: 'high' },
  ]

  for (const scenario of cases) {
    const exactProfile = {
      id: scenario.id,
      label: scenario.id,
      description: scenario.id,
      available: scenario.available,
      fallback_reason: scenario.available ? null : 'Use High instead',
      fallback_profile_id: scenario.fallback,
      download_required: false,
      download_components: [],
      estimate: null,
      settings: exactSettings,
    }
    const profiles = scenario.fallback
      ? [exactProfile, {
          ...exactProfile,
          id: 'high',
          label: 'high',
          available: true,
          fallback_reason: null,
          fallback_profile_id: null,
          settings: fallbackSettings,
        }]
      : [exactProfile]
    const estimate = { profiles, current: { estimate: null } }

    await withStore(async ({ useStore }) => {
      const duration = Math.round((97 / 24) * 1000) / 1000
      const params = {
        ...baseParams(),
        ...exactSettings,
        prompt: `exact ${scenario.id}`,
        image_mode: 0,
        video_length: 97,
        duration_seconds: duration,
        guidance_scale: 1,
        h3_adaptive_conditioning: false,
        h3_style_workflow: scenario.available ? 'saved-look' : '',
      }
      const a = { name: `h3-${scenario.id}.mp4`, meta: sidecar(params) }
      configureGallery(useStore, [a])
      useStore.setState({ h3StyleWorkflow: 'unrelated-look' })

      await useStore.getState().loadSettingsFromOutput()
      await settleAsyncWork()

      const state = useStore.getState()
      assert.equal(state.h3SelectedProfile, 'custom')
      assert.equal(state.h3StyleWorkflow, params.h3_style_workflow)
      assert.equal(state.params.h3_style_workflow, params.h3_style_workflow)
      assert.equal(state.params.model_type, exactSettings.model_type)
      assert.equal(state.params.num_inference_steps, exactSettings.num_inference_steps)
      assert.equal(state.params.resolution, exactSettings.resolution)
      assert.deepEqual(state.params.custom_settings, exactSettings.custom_settings)
      assert.equal(state.params.video_length, 97)
      assert.equal(state.durationSeconds, duration)
    }, { h3Estimate: estimate })
  }
})


test('published H3 final restores the authored whole-video request instead of its last segment', async () => {
  await withStore(async ({ useStore }) => {
    const brief = '[00:00-00:09] A red paper pinwheel. [00:09-00:18] The same pinwheel continues.'
    const params = baseParams({
      model_type: 'minimax_h3', prompt: 'OPENING VISUAL CARRY: previous clip', video_length: 226,
      multi_clip_info: {
        automatic_h3_longform: true, requested_frames: 436, global_prompt: brief,
      },
    })
    configureGallery(useStore, [{
      name: 'joined.mp4',
      meta: sidecar(params, { image_start: ['', ''], image_end: ['', ''] }),
    }])
    assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
    const restored = useStore.getState()
    assert.equal(restored.params.prompt, brief)
    assert.equal(restored.params.video_length, 436)
    assert.equal(restored.params.image_mode, 0)
    assert.ok(Math.abs(restored.durationSeconds - 436 / 24) < 0.01)
  })
})

test('published legacy H3 final with media inputs refuses an incomplete rerun', async () => {
  await withStore(async ({ alerts, useStore }) => {
    const params = baseParams({
      model_type: 'minimax_h3', prompt: 'last segment only', video_length: 226,
      multi_clip_info: {
        automatic_h3_longform: true, requested_frames: 436,
        global_prompt: 'whole video brief',
      },
    })
    configureGallery(useStore, [{
      name: 'joined-with-reference.mp4',
      meta: sidecar(params, { image_start: ['reference.png', ''] }),
    }])
    assert.equal(await useStore.getState().loadSettingsFromOutput(), false)
    assert.match(alerts.at(-1), /reattach the references/)
    assert.equal(useStore.getState().params.prompt, 'current unsaved prompt')
  })
})

test('Inpaint output restores and resubmits explicit target, inversion and prompt strength', async () => {
  for (const explicitTarget of ['', 'sky']) {
    for (const invert of [false, true]) {
      await withStore(async ({ fetchOverrides, useStore }) => {
        const entry = { name: 'inpaint.mp4', meta: sidecar(baseParams({
          edit_sub_mode: 'inpaint', edit_video_path: '/outputs/source.mp4',
          edit_target: 'detected sky', edit_sam_target: explicitTarget,
          edit_invert_mask: invert, retake_masks_path: '/outputs/mask.npy',
          guidance_scale: 2.25,
        })) }
        configureGallery(useStore, [entry])
        useStore.setState({ editSamTarget: 'old target', editInvertMask: !invert,
          editPromptStrength: 6, editMaskPreview: 'old-preview' })
        assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
        const restored = useStore.getState()
        assert.equal(restored.editSamTarget, explicitTarget)
        assert.equal(restored.editDetectedTarget, 'detected sky')
        assert.equal(restored.editInvertMask, invert)
        assert.equal(restored.editMasksPath, '/outputs/mask.npy')
        assert.equal(restored.editMaskPreview, null)
        assert.equal(restored.editPromptStrength, 2.25)
        let submitted
        fetchOverrides.set('/api/v1/inpaint', async (_url, init) => {
          submitted = JSON.parse(init.body)
          return Response.json({ job_id: 'synthetic-inpaint', status: 'queued' })
        })
        useStore.setState({ _pollRecoveredJob: () => {} })
        await useStore.getState().startGeneration()
        assert.ok(submitted, 'restored Inpaint request was submitted')
        assert.equal(submitted.sam_target, explicitTarget)
        assert.equal(submitted.invert_mask, invert)
        assert.equal(submitted.guidance_scale, 2.25)
        assert.equal(submitted.masks_path, '/outputs/mask.npy')
      })
    }
  }
})

test('legacy Inpaint target is retained and absent mask metadata clears previous output state', async () => {
  await withStore(async ({ useStore }) => {
    const entries = [
      { name: 'legacy.mp4', meta: sidecar(baseParams({ edit_sub_mode: 'inpaint', edit_target: 'sky' })) },
      { name: 'empty-inpaint.mp4', meta: sidecar(baseParams({ edit_sub_mode: 'inpaint' })) },
      { name: 'ordinary.mp4', meta: sidecar(baseParams()) },
    ]
    for (let i = 0; i < entries.length; i += 1) {
      configureGallery(useStore, entries, i)
      useStore.setState({ editSamTarget: 'old target', editInvertMask: true })
      useStore.setState({ editDetectedTarget: 'old detected', editMasksPath: '/outputs/old.npy', editMaskPreview: 'old-preview' })
      assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
      const state = useStore.getState()
      assert.equal(state.editSamTarget, i === 0 ? 'sky' : '')
      assert.equal(state.editDetectedTarget, i === 0 ? 'sky' : '')
      assert.equal(state.editInvertMask, false)
      assert.equal(state.editMasksPath, null)
      assert.equal(state.editMaskPreview, null)
    }
  })
})

test('editing the Inpaint target while output options load cancels restoration', async () => {
  await withStore(async ({ fetchOverrides, useStore }) => {
    configureGallery(useStore, [{ name: 'inpaint.mp4', meta: sidecar(baseParams({
      edit_sub_mode: 'inpaint', edit_sam_target: 'saved sky',
    })) }])
    const options = delayedResponse(fetchOverrides, '/api/v1/model-options/ltx2_25',
      () => Response.json(modelOptions('ltx2_25')))
    const restore = useStore.getState().loadSettingsFromOutput()
    await options.requested
    useStore.setState({ editSamTarget: 'new selection' })
    options.release()
    assert.equal(await restore, false)
    assert.equal(useStore.getState().editSamTarget, 'new selection')
    assert.equal(useStore.getState().params.prompt, 'current unsaved prompt')
  })
})


test('Retake restores the prompt-strength control that its request submits', async () => {
  for (const guidance of [0, 4.5]) {
    await withStore(async ({ fetchOverrides, useStore }) => {
      configureGallery(useStore, [{ name: 'retake.mp4', meta: sidecar(baseParams({
        edit_sub_mode: 'retake', edit_video_path: '/outputs/source.mp4', guidance_scale: guidance,
      })) }])
      useStore.setState({ editPromptStrength: 6 })
      assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
      assert.equal(useStore.getState().editPromptStrength, guidance)
      let submitted
      fetchOverrides.set('/api/v1/retake', async (_url, init) => {
        submitted = JSON.parse(init.body)
        return Response.json({ job_id: 'synthetic-retake', status: 'queued' })
      })
      useStore.setState({ _pollRecoveredJob: () => {} })
      await useStore.getState().startGeneration()
      assert.ok(submitted)
      assert.equal(submitted.guidance_scale, guidance)
    })
  }
})

test('Blend output restores only effective controls and requires explicit source reattachment', async () => {
  for (const scenario of [
    {
      mode: 'insert', requested: 5, effective: 5.04,
      prefix: 0, suffix: 0, anchor: 0.7,
    },
    {
      mode: 'overlap', requested: 4, effective: 4,
      prefix: 2, suffix: 1.5, anchor: 0.55,
    },
  ]) {
    await withStore(async ({ alerts, fetchOverrides, useStore }) => {
      const meta = {
        ...sidecar(baseParams({
          _blend_contract_version: 1,
          _blend_mode: scenario.mode,
          _blend_requested_duration_sec: scenario.requested,
          _blend_duration_sec: scenario.effective,
          _blend_motion_prefix_sec: scenario.prefix,
          _blend_motion_suffix_sec: scenario.suffix,
          _blend_fps: 25,
          input_video_strength: scenario.anchor,
        })),
        blend_contract: {
          version: 1,
          legacy: false,
          mode: scenario.mode,
          requested_duration_sec: scenario.requested,
          effective_duration_sec: scenario.effective,
          fps: 25,
          sources: {
            clip_a: { filename: 'first-source.mp4' },
            clip_b: { filename: 'second-source.mkv' },
          },
        },
      }
      configureGallery(useStore, [{ name: `blend-${scenario.mode}.mp4`, meta }])
      useStore.setState({
        blendMode: scenario.mode === 'insert' ? 'overlap' : 'insert',
        blendTransitionSec: 2,
        blendOverlapSec: 1,
        blendMotionPrefixSec: 0,
        blendMotionSuffixSec: 0,
        blendAnchorStrength: 1,
        blendClipA: new File(['old-a'], 'old-a.mp4', { type: 'video/mp4' }),
        blendClipAPath: '/uploads/old-a.mp4',
        blendClipAUrl: 'blob:old-a',
        blendClipADuration: 2,
        blendClipASourceName: 'old-a.mp4',
        blendClipB: new File(['old-b'], 'old-b.mp4', { type: 'video/mp4' }),
        blendClipBPath: '/uploads/old-b.mp4',
        blendClipBUrl: 'blob:old-b',
        blendClipBDuration: 2,
        blendClipBSourceName: 'old-b.mp4',
      })

      assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
      const restored = useStore.getState()
      assert.equal(restored.generationMode, 'video')
      assert.equal(restored.params.image_mode, 4)
      assert.equal(restored.blendMode, scenario.mode)
      assert.equal(restored.blendTransitionSec, scenario.mode === 'insert' ? scenario.requested : 2)
      assert.equal(restored.blendOverlapSec, scenario.mode === 'overlap' ? scenario.requested : 1)
      assert.equal(restored.blendMotionPrefixSec, scenario.prefix)
      assert.equal(restored.blendMotionSuffixSec, scenario.suffix)
      assert.equal(restored.blendAnchorStrength, scenario.anchor)
      assert.equal(restored.blendClipA, null)
      assert.equal(restored.blendClipAPath, '')
      assert.equal(restored.blendClipASourceName, 'first-source.mp4')
      assert.equal(restored.blendClipB, null)
      assert.equal(restored.blendClipBPath, '')
      assert.equal(restored.blendClipBSourceName, 'second-source.mkv')

      let submitted = false
      fetchOverrides.set('/api/v1/blend', async () => {
        submitted = true
        return Response.json({ job_id: 'unexpected-blend', status: 'queued' })
      })
      await useStore.getState().rerollGeneration()
      assert.equal(submitted, false)
      assert.equal(alerts.at(-1), 'Blend settings loaded. Reattach Clip A and Clip B before generating again.')

      useStore.getState().setBlendClipA(
        new File(['new-a'], 'reattached-a.mp4', { type: 'video/mp4' }),
        '/uploads/reattached-a.mp4', 'blob:reattached-a', 4,
      )
      await useStore.getState().rerollGeneration()
      assert.equal(submitted, false)
      assert.equal(useStore.getState().blendClipAPath, '/uploads/reattached-a.mp4')
      assert.equal(useStore.getState().blendClipBPath, '')
      assert.equal(alerts.at(-1), 'Blend settings loaded. Reattach Clip A and Clip B before generating again.')

      useStore.getState().setBlendClipB(
        new File(['new-b'], 'reattached-b.mkv', { type: 'video/x-matroska' }),
        '/uploads/reattached-b.mkv', 'blob:reattached-b', 5,
      )
      useStore.setState({ _pollRecoveredJob: () => {} })
      await useStore.getState().rerollGeneration()
      assert.equal(submitted, true)
      assert.equal(useStore.getState().blendClipAPath, '/uploads/reattached-a.mp4')
      assert.equal(useStore.getState().blendClipBPath, '/uploads/reattached-b.mkv')
    })
  }
})

test('legacy Blend output restores from its overlap field without requiring historical fps', async () => {
  await withStore(async ({ useStore }) => {
    const meta = {
      ...sidecar(baseParams({
        _blend_mode: 'insert',
        _blend_overlap_sec: 3,
      })),
      blend_contract: {
        version: 0,
        legacy: true,
        mode: 'overlap',
        requested_duration_sec: null,
        effective_duration_sec: 3,
        fps: null,
        sources: {
          clip_a: { filename: 'legacy-first.mp4' },
          clip_b: { filename: 'legacy-second.mp4' },
        },
      },
    }
    configureGallery(useStore, [{ name: 'legacy-blend.mp4', meta }])

    assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
    const restored = useStore.getState()
    assert.equal(restored.params.image_mode, 4)
    assert.equal(restored.blendMode, 'overlap')
    assert.equal(restored.blendOverlapSec, 3)
    assert.equal(restored.blendClipASourceName, 'legacy-first.mp4')
    assert.equal(restored.blendClipBSourceName, 'legacy-second.mp4')
  })
})

test('Blend output with controls outside the visible exact range fails closed', async () => {
  await withStore(async ({ alerts, useStore }) => {
    const meta = {
      ...sidecar(baseParams({
        _blend_contract_version: 1,
        _blend_mode: 'insert',
        _blend_requested_duration_sec: 12,
        _blend_duration_sec: 12,
        _blend_fps: 25,
      })),
      blend_contract: {
        version: 1, legacy: false, mode: 'insert',
        requested_duration_sec: 12, effective_duration_sec: 12, fps: 25,
        sources: {},
      },
    }
    configureGallery(useStore, [{ name: 'invalid-blend.mp4', meta }])
    assert.equal(await useStore.getState().loadSettingsFromOutput(), false)
    assert.equal(useStore.getState().params.prompt, 'current unsaved prompt')
    assert.deepEqual(alerts, ['Saved Blend settings cannot be restored exactly. Start a new blend.'])
  })
})

test('Blend output without both source descriptors fails before changing the editor', async () => {
  await withStore(async ({ alerts, useStore }) => {
    const meta = {
      ...sidecar(baseParams({
        _blend_contract_version: 1, _blend_mode: 'insert',
        _blend_requested_duration_sec: 5, _blend_duration_sec: 5.04, _blend_fps: 25,
      })),
      blend_contract: {
        version: 1, legacy: false, mode: 'insert',
        requested_duration_sec: 5, effective_duration_sec: 5.04, fps: 25,
        sources: { clip_a: { filename: 'only-a.mp4' } },
      },
    }
    configureGallery(useStore, [{ name: 'missing-source-blend.mp4', meta }])
    assert.equal(await useStore.getState().loadSettingsFromOutput(), false)
    assert.equal(useStore.getState().params.prompt, 'current unsaved prompt')
    assert.deepEqual(alerts, ['Saved Blend settings cannot be restored exactly. Start a new blend.'])
  })
})

test('editing a Blend control while model options load cancels restoration', async () => {
  await withStore(async ({ fetchOverrides, useStore }) => {
    const meta = {
      ...sidecar(baseParams({
        _blend_contract_version: 1, _blend_mode: 'overlap',
        _blend_requested_duration_sec: 4, _blend_duration_sec: 4, _blend_fps: 25,
        _blend_motion_prefix_sec: 1.52, _blend_motion_suffix_sec: 1, input_video_strength: 0.7,
      })),
      blend_contract: {
        version: 1, legacy: false, mode: 'overlap',
        requested_duration_sec: 4, effective_duration_sec: 4, fps: 25,
        sources: {
          clip_a: { filename: 'first.mp4' }, clip_b: { filename: 'second.mp4' },
        },
      },
    }
    configureGallery(useStore, [{ name: 'delayed-blend.mp4', meta }])
    const options = delayedResponse(fetchOverrides, '/api/v1/model-options/ltx2_25',
      () => Response.json(modelOptions('ltx2_25')))
    const restore = useStore.getState().loadSettingsFromOutput()
    await options.requested
    useStore.getState().setBlendMode('insert')
    useStore.getState().setBlendTransitionSec(6)
    options.release()
    assert.equal(await restore, false)
    assert.equal(useStore.getState().blendMode, 'insert')
    assert.equal(useStore.getState().blendTransitionSec, 6)
    assert.equal(useStore.getState().params.prompt, 'current unsaved prompt')
  })
})


test('every segmentation input invalidates cached masks while unrelated edits retain them', async () => {
  const mutations = [
    ['target', store => store.setState({ editSamTarget: 'tree' })],
    ['inversion', store => store.setState({ editInvertMask: true })],
    ['start', store => store.setState({ editStartTime: 1 })],
    ['end', store => store.setState({ editEndTime: 3 })],
    ['path', store => store.getState().setEditVideoPath('/outputs/other.mp4')],
    ['source upload', store => store.getState().setEditVideo(new File(['new'], 'source.mp4'), '/outputs/source.mp4', 'blob:new', 5, '1280x720')],
    ['resolution', store => store.getState().setParam('resolution', '640x480')],
    ['resolution preset', store => store.getState().setResolutionPreset('480p')],
    ['aspect ratio', store => store.getState().setAspectRatio('1:1')],
    ['project', store => store.setState({ activeWorkspace: 'other' })],
  ]
  for (const [label, mutate] of mutations) {
    await withStore(async ({ useStore }) => {
      useStore.setState(state => ({ activeWorkspace: 'default', generationMode: 'avatar', editSubMode: 'inpaint',
        editVideoPath: '/outputs/source.mp4', editSamTarget: 'sky', editInvertMask: false,
        editStartTime: 0, editEndTime: 5, params: { ...state.params, resolution: '1280x720' } }))
      useStore.setState({ editMasksPath: '/outputs/mask.npy', editMaskPreview: 'preview', editDetectedTarget: 'sky' })
      useStore.getState().setParam('guidance_scale', 4)
      assert.equal(useStore.getState().editMasksPath, '/outputs/mask.npy')
      mutate(useStore)
      assert.equal(useStore.getState().editMasksPath, null, label)
      assert.equal(useStore.getState().editMaskPreview, null, label)
      assert.equal(useStore.getState().editDetectedTarget, '', label)
    })
  }
})

test('mask previews ignore newer requests, selection changes and output restoration', async () => {
  for (const change of ['target', 'invert', 'range', 'source', 'resolution', 'project', 'restore', 'newer-preview', 'target-away-and-back', 'same-path-upload']) {
    await withStore(async ({ fetchOverrides, useStore }) => {
      configureGallery(useStore, [{ name: 'inpaint.mp4', meta: sidecar(baseParams({
        edit_sub_mode: 'inpaint', edit_video_path: '/outputs/restored.mp4',
        edit_sam_target: 'saved target', edit_target: 'saved detected', retake_masks_path: '/outputs/saved.npy',
      })) }])
      useStore.setState({ generationMode: 'avatar', editSubMode: 'inpaint', editVideoPath: '/outputs/source.mp4',
        editSamTarget: 'sky', editStartTime: 0, editEndTime: 5, editInvertMask: false })
      const response = deferred()
      fetchOverrides.set('/api/v1/segment/preview', async () => response.promise)
      const pending = useStore.getState().previewInpaintMask()
      await waitFor(() => useStore.getState().editMaskPreview === null, 'preview begins')
      if (change === 'target') useStore.setState({ editSamTarget: 'tree' })
      if (change === 'invert') useStore.setState({ editInvertMask: true })
      if (change === 'range') useStore.setState({ editEndTime: 3 })
      if (change === 'source') useStore.getState().setEditVideoPath('/outputs/new.mp4')
      if (change === 'same-path-upload') useStore.getState().setEditVideo(new File(['replacement'], 'source.mp4'), '/outputs/source.mp4', 'blob:new', 5, '1280x720')
      if (change === 'resolution') useStore.getState().setParam('resolution', '640x480')
      if (change === 'project') useStore.setState({ activeWorkspace: 'other' })
      if (change === 'target-away-and-back') {
        useStore.setState({ editSamTarget: 'tree' })
        useStore.setState({ editSamTarget: 'sky' })
      }
      if (change === 'restore') assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
      if (change === 'newer-preview') {
        fetchOverrides.set('/api/v1/segment/preview', async () => Response.json({ mask_preview: 'new-preview', target: 'new detected' }))
        assert.equal(await useStore.getState().previewInpaintMask(), true)
      }
      const expected = { preview: useStore.getState().editMaskPreview, target: useStore.getState().editDetectedTarget }
      response.resolve(Response.json({ mask_preview: 'stale-preview', target: 'stale detected' }))
      assert.equal(await pending, false, change)
      assert.equal(useStore.getState().editMaskPreview, expected.preview, change)
      assert.equal(useStore.getState().editDetectedTarget, expected.target, change)
    })
  }
})
