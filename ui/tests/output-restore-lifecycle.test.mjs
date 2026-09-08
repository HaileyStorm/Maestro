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
]

function modelOptions(modelType) {
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
    assert.ok(body.indexOf('setSelectedOutput(index)') < body.indexOf(`${operation}()`), `${handler} selects before ${operation}`)
  }

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
    'selectedModelPerAudioSubMode',
    'selectedModelPerMode',
  ])
  const classified = new Set([
    ...Object.keys(schema.ui),
    ...authoredFields,
    ...lifecycleBookkeeping,
  ])
  assert.deepEqual(unsupportedSetShapes, [])
  assert.deepEqual([...mergedSpreads].sort(), ['timingState'])
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
