import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

import {
  createDirectorImageRoleLoraSelection,
  DirectorRequestError,
  fetchDirectorCapabilities,
  getDirectorHostActionAccessState,
  preflightDirectorPipeline,
  startPipeline,
  toDirectorImageRoleLoraWire,
  validateDirectorImageRoleLoraSelections,
  waitForModelDownloadTerminal,
} from '../src/api/client.ts'

const UI_ROOT = fileURLToPath(new URL('..', import.meta.url))
const DIGEST = 'a'.repeat(64)
const BOOLEAN_DIGEST = 'c'.repeat(64)

const schema = {
  schema_version: 1,
  schema_digest: DIGEST,
  parameters: [
    {
      id: 'detail',
      label: 'Detail',
      type: 'integer',
      required: true,
      default: 2,
      scopes: ['generation'],
      roles: ['creator'],
      minimum: 0,
      maximum: 4,
      step: 1,
    },
    {
      id: 'finish',
      label: 'Finish',
      type: 'enum',
      required: true,
      default: 'matte',
      scopes: ['generation'],
      roles: ['creator'],
      options: [
        { label: 'Matte', value: 'matte' },
        { label: 'Gloss', value: 'gloss' },
      ],
    },
  ],
}

const booleanSchema = {
  schema_version: 1,
  schema_digest: BOOLEAN_DIGEST,
  parameters: [
    {
      id: 'required_toggle',
      label: 'Required toggle',
      type: 'boolean',
      required: true,
      scopes: ['generation'],
      roles: ['creator'],
    },
    {
      id: 'optional_toggle',
      label: 'Optional toggle',
      type: 'boolean',
      required: false,
      scopes: ['generation'],
      roles: ['creator'],
    },
  ],
}

function lora(filename, parameterSchema) {
  return {
    filename,
    trained_words: [],
    preview_url: null,
    civitai_model_id: null,
    recommended_weights: { default: 0.75, min: -1, max: 2 },
    has_guide: false,
    lora_id: `local:${filename}`,
    ...(parameterSchema ? { parameter_schema: parameterSchema } : {}),
  }
}

function capabilities(explicit = true) {
  const creator = explicit ? 'krea2_moody_mix_v7_fp8' : 'flux2_klein_9b'
  return {
    schema_version: 1,
    readiness_reason_values: [
      'director_incompatible',
      'manual_verification_required',
      'model_disabled',
      'model_not_downloaded',
      'model_terms_required',
      'model_unavailable',
    ],
    readiness_action_values: [
      'accept_terms',
      'download_model',
      'enable_model',
      'select_model',
      'verify_manual_checkpoint',
    ],
    image_roles: {
      creator: {
        resolved_model: creator,
        selection_source: explicit ? 'verified_manual_preference' : 'safe_fallback',
        candidates: [{
          model_type: creator,
          compatible: true,
          ready: true,
          reasons: [],
          actions: [],
          enabled: true,
          downloaded: true,
        }],
        lora_catalog_endpoint: '/api/v1/loras/{model_type}/details',
      },
      editor: {
        resolved_model: 'flux2_klein_9b',
        selection_source: 'fixed_default',
        candidates: [{
          model_type: 'flux2_klein_9b',
          compatible: true,
          ready: true,
          reasons: [],
          actions: [],
          enabled: true,
          downloaded: true,
        }],
        lora_catalog_endpoint: '/api/v1/loras/{model_type}/details',
      },
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

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

let storeHelperPromise
function loadStoreHelper() {
  if (storeHelperPromise) return storeHelperPromise
  storeHelperPromise = readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
    .then(source => build({
      stdin: {
        contents: `${source}\nexport { _captureDirectorImageRoleRequest, _refreshDirectorModelAdmissionCatalog, _resolveDirectorReferenceRows }\n`,
        resolveDir: fileURLToPath(new URL('../src/stores/', import.meta.url)),
        loader: 'ts',
      },
      bundle: true,
      format: 'esm',
      logLevel: 'silent',
      platform: 'node',
      treeShaking: true,
      write: false,
    }))
    .then(result => import(asDataModule(result.outputFiles[0].text)))
  return storeHelperPromise
}

async function loadFreshStoreHelper(tag) {
  const source = await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
  const result = await build({
    stdin: {
      contents: source,
      resolveDir: fileURLToPath(new URL('../src/stores/', import.meta.url)),
      loader: 'ts',
    },
    bundle: true,
    format: 'esm',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
  })
  return import(`${asDataModule(result.outputFiles[0].text)}#${tag}`)
}

let roleSelectorPromise
function loadRoleSelector() {
  if (roleSelectorPromise) return roleSelectorPromise
  roleSelectorPromise = build({
    stdin: {
      contents: "export { DirectorImageRoleLoraSelector } from './src/components/SettingsDrawer/DirectorLoraSelector.tsx'",
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
      name: 'director-role-selector-test-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'director-role' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'director-role' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide', namespace: 'director-role' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'director-role' }))
        bundle.onResolve({ filter: /^\.\/LoraSelector$/ }, () => ({ path: 'lora-selector', namespace: 'director-role' }))
        bundle.onResolve({ filter: /^\.\/loraSort$/ }, () => ({ path: 'lora-sort', namespace: 'director-role' }))
        bundle.onLoad({ filter: /.*/, namespace: 'director-role' }, args => {
          if (args.path === 'react') return { contents: `
            export const useEffect = () => {}
            export const useRef = value => ({ current: value })
            export const useCallback = callback => callback
            export const useState = initial => {
              const fallback = typeof initial === 'function' ? initial() : initial
              const value = globalThis.__directorRoleStateOverrides?.length
                ? globalThis.__directorRoleStateOverrides.shift()
                : fallback
              return [value, update => globalThis.__directorRoleStateUpdates?.push(update)]
            }
          ` }
          if (args.path === 'jsx-runtime') return { contents: `
            export const Fragment = Symbol.for('director-role-fragment')
            export const jsx = (type, props, key) => ({ type, key, props: props || {} })
            export const jsxs = jsx
          ` }
          if (args.path === 'lucide') return { contents: `
            export const Search = 'Search', X = 'X', Loader2 = 'Loader2', FolderOpen = 'FolderOpen', Globe = 'Globe', Sparkles = 'Sparkles', BookOpen = 'BookOpen'
          ` }
          if (args.path === 'store') return { contents: `
            export const useStore = selector => selector({ setLoraBrowserOpen() {} })
          ` }
          if (args.path === 'lora-selector') return { contents: `
            export const LoraGuideTooltip = () => null, LoraAgeChip = () => null, LoraSortToggle = () => null
          ` }
          return { contents: 'export const sortLoraNames = values => values' }
        })
      },
    }],
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return roleSelectorPromise
}

function flattenElements(value, result = []) {
  if (Array.isArray(value)) {
    for (const child of value) flattenElements(child, result)
    return result
  }
  if (!value || typeof value !== 'object') return result
  if ('type' in value && 'props' in value) result.push(value)
  if (typeof value.type === 'function') {
    flattenElements(value.type(value.props), result)
  }
  flattenElements(value.props?.children, result)
  return result
}

test('role LoRA helper emits exact strength-only and schema-sealed rows', () => {
  const simple = lora('grain.safetensors')
  const sealed = lora('finish.safetensors', schema)
  assert.deepEqual(createDirectorImageRoleLoraSelection(simple), {
    id: 'grain.safetensors',
    multiplier: 0.75,
  })
  assert.deepEqual(createDirectorImageRoleLoraSelection(sealed, 1.25), {
    id: 'finish.safetensors',
    multiplier: 1.25,
    parameter_schema_digest: DIGEST,
    parameter_values: { detail: 2, finish: 'matte' },
  })
  assert.deepEqual(validateDirectorImageRoleLoraSelections([
    createDirectorImageRoleLoraSelection(simple),
    createDirectorImageRoleLoraSelection(sealed),
  ], [simple, sealed]), [])
  assert.deepEqual(toDirectorImageRoleLoraWire([{
    ...createDirectorImageRoleLoraSelection(sealed),
    obsolete_client_key: 'must not cross the wire',
  }]), [createDirectorImageRoleLoraSelection(sealed)])

  const invalid = validateDirectorImageRoleLoraSelections([
    { id: '../grain.safetensors', multiplier: 1 },
    { id: 'finish.safetensors', multiplier: 11, parameter_schema_digest: 'b'.repeat(64), parameter_values: {} },
    { id: 'finish.safetensors', multiplier: 1, parameter_schema_digest: DIGEST, parameter_values: { detail: 2.5, finish: 'unknown' } },
  ], [simple, sealed])
  assert.match(invalid.join('\n'), /one catalog filename/)
  assert.match(invalid.join('\n'), /between -10 and 10/)
  assert.match(invalid.join('\n'), /schema changed/)
  assert.match(invalid.join('\n'), /Select each LoRA only once/)

  const booleanLora = lora('boolean.safetensors', booleanSchema)
  const missingRequired = createDirectorImageRoleLoraSelection(booleanLora)
  assert.match(
    validateDirectorImageRoleLoraSelections([missingRequired], [booleanLora]).join('\n'),
    /Required toggle is required/,
  )
  assert.deepEqual(validateDirectorImageRoleLoraSelections([{
    ...missingRequired,
    parameter_values: { required_toggle: false },
  }], [booleanLora]), [])
})

test('capabilities query and pipeline transport preserve the exact new-role wire', async t => {
  const originalFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (input, init = {}) => {
    calls.push({ url: String(input), init })
    if (String(input).startsWith('/api/v1/director/capabilities?')) return Response.json(capabilities())
    if (String(input).endsWith('/api/v1/director/pipeline/start')) return Response.json({ pipeline_id: 'pipeline-role-1' })
    throw new Error(`Unexpected request ${input}`)
  }
  t.after(() => { globalThis.fetch = originalFetch })

  const result = await fetchDirectorCapabilities(true)
  assert.equal(result.image_roles.creator.selection_source, 'verified_manual_preference')
  assert.equal(calls[0].url, '/api/v1/director/capabilities?explicit_output=true')
  assert.equal(calls[0].init.cache, 'no-store')
  assert.equal(calls[0].init.credentials, 'same-origin')

  const sealed = createDirectorImageRoleLoraSelection(lora('finish.safetensors', schema))
  await startPipeline({
    workspace: 'project one',
    image_creator_model: null,
    image_editor_model: 'flux2_klein_9b',
    image_creator_loras: [sealed],
    image_params: { resolution: '1280x720' },
    _director_request_id: 'must-stay-private',
  })
  const body = JSON.parse(String(calls[1].init.body))
  assert.equal(Object.hasOwn(body, 'image_creator_model'), true)
  assert.equal(body.image_creator_model, null)
  assert.equal(body.image_editor_model, 'flux2_klein_9b')
  assert.deepEqual(body.image_creator_loras, [sealed])
  assert.deepEqual(body.image_params, { resolution: '1280x720' })
  assert.equal(body.image_model, undefined)
  assert.equal(body.image_loras, undefined)
  assert.equal(body._director_request_id, undefined)
})

test('Director transport distinguishes structured model roles from legacy media failures', async t => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })

  globalThis.fetch = async () => Response.json({ detail: 'Model not found' }, { status: 404 })
  await assert.rejects(startPipeline({}), error => {
    assert.match(error.message, /selected Director model is unavailable/i)
    assert.doesNotMatch(error.message, /selected reference/i)
    return true
  })

  globalThis.fetch = async () => Response.json({
    detail: 'Unauthorized media: selected reference not found',
  }, { status: 404 })
  await assert.rejects(startPipeline({}), /could not access a selected reference/i)

  globalThis.fetch = async () => Response.json({
    code: 'director_model_unavailable',
    component: 'image_creator_model',
    message: 'private catalog membership must not be reflected',
  }, { status: 404 })
  await assert.rejects(startPipeline({}), error => {
    assert.ok(error instanceof DirectorRequestError)
    assert.equal(error.code, 'director_model_unavailable')
    assert.equal(error.component, 'image_creator_model')
    assert.match(error.message, /Image creator is unavailable in this session/)
    assert.doesNotMatch(error.message, /private catalog membership/)
    return true
  })
})

test('Director preflight sends only the exact content-free PinkCherry, Moody, and Qwen roles', async t => {
  const originalFetch = globalThis.fetch
  let call
  globalThis.fetch = async (input, init = {}) => {
    call = { url: String(input), init }
    return Response.json({
      status: 'ready',
      resolved: {
        pipeline_type: 'short_film_story',
        video_model: 'minimax_h3_pinkcherry_fl2va',
        image_creator_model: 'krea2_moody_mix_v7_fp8',
        continuity_editor_model: 'qwen_image_edit_2511_nsfw',
        director_resolution_preset: '720p',
        director_aspect_ratio: '16:9',
        video_resolution: '1280x704',
        image_resolution: '1280x720',
      },
      components: [
        { component: 'video_model', status: 'ready' },
        { component: 'image_creator_model', status: 'ready' },
        { component: 'continuity_editor_model', status: 'ready' },
        { component: 'image_creator_lora', status: 'not_required' },
        { component: 'continuity_editor_lora', status: 'ready' },
      ],
    })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  const editorLora = { id: 'qwen-image-edit-uncensored.safetensors', multiplier: 1 }
  const result = await preflightDirectorPipeline({
    pipeline_type: 'short_film_story',
    explicit_output: true,
    video_model: 'minimax_h3_pinkcherry_fl2va',
    image_creator_model: 'krea2_moody_mix_v7_fp8',
    continuity_editor_model: 'qwen_image_edit_2511_nsfw',
    continuity_editor_loras: [editorLora],
    director_resolution_preset: '720p',
    director_aspect_ratio: '16:9',
    reference_presence: { starting_image: true, character: true, location: false },
  })
  assert.equal(result.status, 'ready')
  assert.equal(call.url, '/api/v1/director/preflight')
  assert.equal(call.init.cache, 'no-store')
  assert.equal(call.init.credentials, 'same-origin')
  assert.deepEqual(JSON.parse(String(call.init.body)), {
    pipeline_type: 'short_film_story',
    explicit_output: true,
    video_model: 'minimax_h3_pinkcherry_fl2va',
    image_creator_model: 'krea2_moody_mix_v7_fp8',
    continuity_editor_model: 'qwen_image_edit_2511_nsfw',
    continuity_editor_loras: [editorLora],
    director_resolution_preset: '720p',
    director_aspect_ratio: '16:9',
    reference_presence: { starting_image: true, character: true, location: false },
  })
  for (const forbidden of ['filename', 'path', 'prompt', 'media']) {
    assert.doesNotMatch(String(call.init.body), new RegExp(forbidden, 'i'))
  }
})

test('Director preflight preserves every closed failing role without exposing server catalog text', async t => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  const request = {
    pipeline_type: 'music_video',
    explicit_output: true,
    video_model: 'minimax_h3_pinkcherry_fl2va',
    image_creator_model: 'krea2_moody_mix_v7_fp8',
    continuity_editor_model: 'qwen_image_edit_2511_nsfw',
    director_resolution_preset: '720p',
    director_aspect_ratio: '16:9',
    reference_presence: { starting_image: true, character: true, location: true },
  }
  const failures = [
    ['director_model_unavailable', 'video_model', 404],
    ['director_model_not_ready', 'image_creator_model', 409],
    ['director_model_terms_required', 'continuity_editor_model', 409],
    ['director_role_lora_unavailable', 'image_creator_lora', 404],
    ['director_role_lora_unavailable', 'continuity_editor_lora', 409],
    ['director_reference_unavailable', 'character_reference', 404],
    ['director_reference_unavailable', 'location_reference', 404],
    ['director_reference_unavailable', 'starting_image', 403],
  ]
  for (const [code, component, status] of failures) {
    globalThis.fetch = async () => Response.json({
      code,
      component,
      message: 'server-private membership detail',
    }, { status })
    await assert.rejects(preflightDirectorPipeline(request), error => {
      assert.ok(error instanceof DirectorRequestError)
      assert.equal(error.code, code)
      assert.equal(error.component, component)
      assert.doesNotMatch(error.message, /server-private membership/)
      return true
    })
  }
})

test('store restore and requested-Explicit snapshots survive overlapping force loads', async () => {
  const originalLocalStorage = globalThis.localStorage
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const stored = new Map([[
    'maestro:director-image-roles-v1',
    JSON.stringify({
      schema_version: 1,
      creator_model_override: '',
      editor_model_override: '',
      creator_loras: [],
      editor_loras: [],
    }),
  ]])
  globalThis.localStorage = {
    getItem(key) { return stored.get(key) ?? null },
    setItem(key, value) { stored.set(key, String(value)) },
    removeItem(key) { stored.delete(key) },
  }
  globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout, setInterval, clearInterval, alert() {} })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  try {
    const { _captureDirectorImageRoleRequest, useStore } = await loadStoreHelper()
    assert.equal(useStore.getState().directorImageRolesConfigured, true)
    assert.deepEqual(useStore.getState().directorImageRoleLoras, { creator: [], editor: [] })

    const state = {
      directorCapabilities: null,
      directorCapabilitiesError: null,
      directorImageCreatorModelOverride: '',
      directorImageEditorModelOverride: '',
      directorImageRoleLoras: { creator: [], editor: [] },
      async loadDirectorCapabilities() { return capabilities(true) },
    }
    const automatic = await _captureDirectorImageRoleRequest(() => state, true)
    assert.deepEqual(automatic.wire, { image_creator_model: null })
    assert.equal(automatic.effective_creator_model, 'krea2_moody_mix_v7_fp8')
    assert.equal(automatic.effective_editor_model, 'flux2_klein_9b')

    state.directorImageCreatorModelOverride = 'krea2_moody_mix_v7_fp8'
    state.directorImageEditorModelOverride = 'flux2_klein_9b'
    const deliberate = await _captureDirectorImageRoleRequest(() => state, true)
    assert.deepEqual(deliberate.wire, {
      image_creator_model: 'krea2_moody_mix_v7_fp8',
      image_editor_model: 'flux2_klein_9b',
    })

    state.directorImageCreatorModelOverride = ''
    state.directorImageEditorModelOverride = ''
    state.loadDirectorCapabilities = async () => ({
      ...capabilities(true),
      image_roles: {
        creator: { ...capabilities(true).image_roles.creator, resolved_model: null, candidates: [] },
        editor: { ...capabilities(true).image_roles.editor, resolved_model: null, candidates: [] },
      },
    })
    await assert.rejects(
      _captureDirectorImageRoleRequest(() => state, true),
      /Image creator is unavailable in this session/,
    )

    const originalFetch = globalThis.fetch
    const requests = []
    const standard = deferred()
    const explicit = deferred()
    globalThis.fetch = async input => {
      const url = String(input)
      requests.push(url)
      if (url.endsWith('explicit_output=false')) return standard.promise
      if (url.endsWith('explicit_output=true')) return explicit.promise
      throw new Error(`Unexpected request ${url}`)
    }
    try {
      useStore.getState().setExplicitOutput(false)
      const standardLoad = useStore.getState().loadDirectorCapabilities({ explicitOutput: false, force: true })
      useStore.getState().setExplicitOutput(true)
      const explicitLoad = useStore.getState().loadDirectorCapabilities({ explicitOutput: true, force: true })
      explicit.resolve(Response.json(capabilities(true)))
      assert.equal((await explicitLoad).image_roles.creator.selection_source, 'verified_manual_preference')
      standard.resolve(Response.json(capabilities(false)))
      assert.equal((await standardLoad).image_roles.creator.selection_source, 'safe_fallback')
      assert.equal(useStore.getState().directorCapabilitiesExplicitOutput, true)
      assert.equal(useStore.getState().directorCapabilities?.image_roles.creator.selection_source, 'verified_manual_preference')
      assert.deepEqual(requests, [
        '/api/v1/director/capabilities?explicit_output=false',
        '/api/v1/director/capabilities?explicit_output=true',
      ])

      // A captured-old Standard refresh can begin after the current Explicit
      // refresh, but must not displace the current key when it completes later.
      const currentExplicit = deferred()
      const capturedStandard = deferred()
      globalThis.fetch = async input => String(input).endsWith('explicit_output=true')
        ? currentExplicit.promise
        : capturedStandard.promise
      useStore.getState().setExplicitOutput(true)
      const currentExplicitLoad = useStore.getState().loadDirectorCapabilities({
        explicitOutput: true,
        force: true,
      })
      const capturedStandardLoad = useStore.getState().loadDirectorCapabilities({
        explicitOutput: false,
        force: true,
      })
      assert.equal(useStore.getState().directorCapabilitiesLoadingExplicitOutput, true)
      currentExplicit.resolve(Response.json(capabilities(true)))
      await currentExplicitLoad
      assert.equal(useStore.getState().directorCapabilitiesExplicitOutput, true)
      capturedStandard.resolve(Response.json(capabilities(false)))
      assert.equal((await capturedStandardLoad).image_roles.creator.selection_source, 'safe_fallback')
      assert.equal(useStore.getState().directorCapabilitiesExplicitOutput, true)
      assert.equal(useStore.getState().directorCapabilities?.image_roles.creator.selection_source, 'verified_manual_preference')

      const invalidated = deferred()
      globalThis.fetch = async () => invalidated.promise
      const pending = useStore.getState().loadDirectorCapabilities({ explicitOutput: true, force: true })
      assert.equal(useStore.getState().directorCapabilitiesLoadingExplicitOutput, true)
      useStore.getState().setExplicitOutput(false)
      assert.equal(useStore.getState().directorCapabilitiesLoading, false)
      assert.equal(useStore.getState().directorCapabilitiesLoadingExplicitOutput, null)
      assert.equal(useStore.getState().directorCapabilities, null)
      invalidated.resolve(Response.json(capabilities(true)))
      await pending
      assert.equal(useStore.getState().directorCapabilities, null)

      const modelA = deferred()
      const modelB = deferred()
      globalThis.fetch = async input => {
        const url = String(input)
        if (url.endsWith('/api/v1/model-options/model-a')) return modelA.promise
        if (url.endsWith('/api/v1/model-options/model-b')) return modelB.promise
        throw new Error(`Unexpected request ${url}`)
      }
      useStore.getState().setDirectorResolution('1080p')
      const loadA = useStore.getState().loadDirectorResolutionOptions('model-a')
      const loadB = useStore.getState().loadDirectorResolutionOptions('model-b')
      modelB.resolve(Response.json({
        model_type: 'model-b',
        resolution_preset_order: ['720p'],
        resolution_presets: {
          '720p': { label: 'Native', values: { '16:9': '1280x704' } },
        },
        supports_auto_aspect: false,
      }))
      await loadB
      modelA.resolve(Response.json({
        model_type: 'model-a',
        resolution_preset_order: ['1080p'],
        resolution_presets: {
          '1080p': { label: 'Old', values: { '16:9': '1920x1088' } },
        },
        supports_auto_aspect: false,
      }))
      await loadA
      assert.equal(useStore.getState().directorResolutionModelType, 'model-b')
      assert.equal(useStore.getState().directorResolutionOptions?.model_type, 'model-b')
      assert.equal(useStore.getState().directorResolution, '1080p')

      let settingsRefreshes = 0
      globalThis.fetch = async input => {
        assert.equal(String(input), '/api/v1/director/capabilities?explicit_output=false')
        settingsRefreshes += 1
        return Response.json(capabilities(false))
      }
      useStore.getState().openDirectorModelVisibility()
      assert.equal(useStore.getState().directorModelVisibilityRefreshPending, true)
      useStore.getState().setSettingsOpen(false)
      await new Promise(resolve => setTimeout(resolve, 0))
      assert.equal(settingsRefreshes, 1)
      assert.equal(useStore.getState().directorCapabilitiesExplicitOutput, false)
    } finally {
      globalThis.fetch = originalFetch
    }
  } finally {
    globalThis.localStorage = originalLocalStorage
    globalThis.window = originalWindow
    globalThis.document = originalDocument
  }
})

test('Director reference uploads settle by stable selection index and report the failing row', async () => {
  const { _resolveDirectorReferenceRows } = await loadStoreHelper()
  const files = ['alpha.png', 'beta.png', 'gamma.png'].map(name => ({ name }))
  const pending = new Map()
  const upload = file => {
    const request = deferred()
    pending.set(file.name, request)
    return request.promise
  }
  const resolving = _resolveDirectorReferenceRows(
    files,
    [],
    ['Alpha', 'Beta', 'Gamma'],
    'character_reference',
    upload,
  )
  while (pending.size < 3) await Promise.resolve()
  pending.get('gamma.png').resolve({ path: '/uploads/gamma.png' })
  pending.get('beta.png').resolve({ path: '/uploads/beta.png' })
  pending.get('alpha.png').resolve({ path: '/uploads/alpha.png' })
  assert.deepEqual(await resolving, {
    paths: ['/uploads/alpha.png', '/uploads/beta.png', '/uploads/gamma.png'],
    labels: ['Alpha', 'Beta', 'Gamma'],
  })

  const failedPending = new Map()
  const failing = _resolveDirectorReferenceRows(
    files,
    ['/legacy/compacted-first.png'],
    ['Alpha', 'Beta', 'Gamma'],
    'location_reference',
    file => {
      const request = deferred()
      failedPending.set(file.name, request)
      return request.promise
    },
  )
  while (failedPending.size < 3) await Promise.resolve()
  failedPending.get('gamma.png').resolve({ path: '/uploads/gamma.png' })
  failedPending.get('beta.png').reject(new Error('bounded upload failure'))
  failedPending.get('alpha.png').resolve({ path: '/uploads/alpha.png' })
  await assert.rejects(failing, error => {
    assert.equal(error.name, 'DirectorRequestError')
    assert.equal(error.code, 'director_reference_unavailable')
    assert.equal(error.component, 'location_reference')
    assert.equal(error.reference_index, 1)
    assert.match(error.message, /Location reference 2 could not be accessed/)
    assert.doesNotMatch(error.message, /bounded upload failure/)
    return true
  })
  assert.deepEqual([...failedPending.keys()], ['alpha.png', 'beta.png', 'gamma.png'])
})

test('model hydration seeds the authoritative video default and preserves a valid saved choice', async t => {
  const originalLocalStorage = globalThis.localStorage
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalFetch = globalThis.fetch
  const stored = new Map()
  globalThis.localStorage = {
    getItem(key) { return stored.get(key) ?? null },
    setItem(key, value) { stored.set(key, String(value)) },
    removeItem(key) { stored.delete(key) },
  }
  globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout, setInterval, clearInterval, alert() {} })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  const families = [
    { id: 'flux2', label: 'Flux 2', order: 1 },
    { id: 'minimax', label: 'MiniMax', order: 2 },
    { id: 'ltx2', label: 'LTX 2', order: 3 },
  ]
  const model = (model_type, family, image = false) => ({
    model_type,
    name: model_type,
    family,
    architecture: family,
    is_i2v: !image,
    is_t2v: !image,
    guidance_max_phases: 1,
    fps: 16,
    image_outputs: image,
  })
  const models = [
    model('flux2_klein_9b', 'flux2', true),
    model('minimax_h3', 'minimax'),
    model('ltx2_22B_distilled_1_1', 'ltx2'),
  ]
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url === '/api/v1/models') return Response.json({ families, models })
    if (url === '/api/v1/model-visibility' && (init.method || 'GET') === 'GET') {
      return Response.json({ configured: true, enabled_models: models.map(item => item.model_type), defaults_version: 9 })
    }
    if (url.includes('/api/v1/loras/installed')) return Response.json({ loras: [] })
    if (url.includes('/api/v1/loras/')) return Response.json({ loras: [] })
    if (url.includes('/api/v1/models/') && url.endsWith('/options')) return Response.json({})
    if (url.includes('/api/v1/defaults/')) return Response.json({})
    throw new Error(`Unexpected model hydration request: ${url}`)
  }
  t.after(() => {
    globalThis.localStorage = originalLocalStorage
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.fetch = originalFetch
  })

  stored.set('maestro_mode_settings', JSON.stringify({
    generationMode: 'image',
    selectedModelPerMode: { image: 'flux2_klein_9b', video: '' },
    savedParamsPerMode: {},
    savedLoraPerMode: {},
    savedPromptPerMode: {},
  }))
  const { useStore } = await loadFreshStoreHelper('video-default-seed')
  await useStore.getState().loadModels()
  assert.equal(useStore.getState().generationMode, 'image')
  assert.equal(useStore.getState().selectedModelPerMode.video, 'minimax_h3')

  stored.set('maestro_mode_settings', JSON.stringify({
    generationMode: 'image',
    selectedModelPerMode: { image: 'flux2_klein_9b', video: 'ltx2_22B_distilled_1_1' },
    savedParamsPerMode: {},
    savedLoraPerMode: {},
    savedPromptPerMode: {},
  }))
  await useStore.getState().loadModels()
  assert.equal(useStore.getState().selectedModelPerMode.video, 'ltx2_22B_distilled_1_1')
})

test('model download polling emits progress, reaches terminal state, and cancels before fetch', async t => {
  const originalFetch = globalThis.fetch
  const statuses = []
  let calls = 0
  globalThis.fetch = async input => {
    assert.equal(String(input), '/api/v1/models/downloads/status')
    calls += 1
    if (calls === 1) throw new Error('transient status read')
    return Response.json({
      downloads: {
        moody: calls === 2
          ? { status: 'downloading', error: null }
          : { status: 'completed', error: null },
      },
    })
  }
  t.after(() => { globalThis.fetch = originalFetch })
  const terminal = await waitForModelDownloadTerminal('moody', {
    isCurrent: () => true,
    wait: async () => {},
    onStatus: status => statuses.push(status),
  })
  assert.deepEqual(terminal, { status: 'completed', error: null })
  assert.deepEqual(statuses, ['downloading', 'completed'])
  assert.equal(calls, 3)

  assert.deepEqual(await waitForModelDownloadTerminal('moody', {
    isCurrent: () => false,
    wait: async () => {},
  }), { status: 'cancelled', error: null })
  assert.equal(calls, 3)
})

test('Director admission refresh waits for the latest enabled-model persistence', async t => {
  const originalFetch = globalThis.fetch
  const visibilityWrites = [deferred(), deferred()]
  let writeIndex = 0
  globalThis.fetch = async (input, init = {}) => {
    assert.equal(String(input), '/api/v1/model-visibility')
    assert.equal(init.method, 'PUT')
    const write = visibilityWrites[writeIndex]
    writeIndex += 1
    if (!write) throw new Error('Unexpected extra visibility write')
    return write.promise
  }
  t.after(() => { globalThis.fetch = originalFetch })

  const { _refreshDirectorModelAdmissionCatalog, useStore } = await loadStoreHelper()
  useStore.getState().toggleModelEnabled('minimax_h3_pinkcherry_fl2va')
  const phases = []
  const refresh = _refreshDirectorModelAdmissionCatalog(async () => {
    phases.push('catalog')
    if (phases.length === 1) useStore.getState().toggleModelEnabled('krea2_moody_mix_v7_fp8')
  })
  await Promise.resolve()
  await Promise.resolve()
  assert.deepEqual(phases, [])
  visibilityWrites[0].resolve(Response.json({
    configured: true,
    enabled_models: [...useStore.getState().enabledModels],
    defaults_version: 9,
  }))
  while (writeIndex < 2) await new Promise(resolve => setTimeout(resolve, 0))
  assert.deepEqual(phases, ['catalog'])
  let settled = false
  void refresh.then(() => { settled = true })
  await Promise.resolve()
  assert.equal(settled, false)
  visibilityWrites[1].resolve(Response.json({
    configured: true,
    enabled_models: [...useStore.getState().enabledModels],
    defaults_version: 9,
  }))
  await refresh
  assert.deepEqual(phases, ['catalog', 'catalog'])
})

test('Director host actions distinguish loading, local authority, and LAN sessions', () => {
  const base = {
    remote: false,
    project_password_required: false,
    project_names_visible: true,
    machine_controls: true,
    custom_model_sources: true,
    catalog_model_downloads: true,
    classic_ui: false,
    cloudflare_enabled: false,
    share_url: '',
    share_flow: '',
  }
  assert.equal(getDirectorHostActionAccessState(null), 'loading')
  assert.equal(getDirectorHostActionAccessState(base), 'local')
  assert.equal(getDirectorHostActionAccessState({
    ...base,
    remote: true,
    machine_controls: false,
    custom_model_sources: false,
    catalog_model_downloads: false,
  }), 'lan')
})

test('role LoRA component exposes accessible controls and a responsive parameter grid', async () => {
  const { DirectorImageRoleLoraSelector } = await loadRoleSelector()
  const sealed = createDirectorImageRoleLoraSelection(lora('finish.safetensors', schema))
  globalThis.__directorRoleStateOverrides = [[lora('finish.safetensors', schema)], false, '', '']
  globalThis.__directorRoleStateUpdates = []
  const changes = []
  const tree = DirectorImageRoleLoraSelector({
    role: 'creator',
    modelType: 'krea2_moody_mix_v7_fp8',
    selections: [sealed],
    onChange: value => changes.push(value),
  })
  const elements = flattenElements(tree)
  assert.ok(elements.some(element => element.type === 'input' && element.props['aria-label'] === 'Search Creator LoRAs'))
  const multiplier = elements.find(element => element.type === 'input' && element.props['aria-label'] === 'finish.safetensors multiplier')
  assert.ok(multiplier)
  multiplier.props.onChange({ target: { valueAsNumber: 1.5 } })
  assert.equal(changes.at(-1)[0].multiplier, 1.5)
  assert.ok(elements.some(element => element.type === 'button' && element.props['aria-label'] === 'Remove finish.safetensors'))
  assert.ok(elements.some(element => typeof element.props.className === 'string' && element.props.className.includes('sm:grid-cols-2')))
  for (const id of ['director-finish-safetensors-detail', 'director-finish-safetensors-finish']) {
    assert.ok(elements.some(element => element.props.id === id))
    assert.ok(elements.some(element => element.type === 'label' && element.props.htmlFor === id))
  }

  const booleanSelection = {
    id: 'boolean.safetensors',
    multiplier: 1,
    parameter_schema_digest: BOOLEAN_DIGEST,
    parameter_values: {},
  }
  globalThis.__directorRoleStateOverrides = [[lora('boolean.safetensors', booleanSchema)], false, '', '']
  const booleanChanges = []
  const booleanTree = DirectorImageRoleLoraSelector({
    role: 'creator',
    modelType: 'krea2_moody_mix_v7_fp8',
    selections: [booleanSelection],
    onChange: value => booleanChanges.push(value),
  })
  const booleanElements = flattenElements(booleanTree)
  for (const id of [
    'director-boolean-safetensors-required_toggle',
    'director-boolean-safetensors-optional_toggle',
  ]) {
    const select = booleanElements.find(element => element.type === 'select' && element.props.id === id)
    assert.ok(select)
    assert.equal(select.props.value, '')
    assert.ok(flattenElements(select.props.children).some(element => element.type === 'option' && element.props.value === ''))
  }
  const required = booleanElements.find(element => element.type === 'select' && element.props.id.endsWith('required_toggle'))
  required.props.onChange({ target: { value: 'false' } })
  assert.equal(booleanChanges.at(-1)[0].parameter_values.required_toggle, false)
  required.props.onChange({ target: { value: '' } })
  assert.equal(Object.hasOwn(booleanChanges.at(-1)[0].parameter_values, 'required_toggle'), false)
})

test('Director source keeps image roles explicit and one final-video post-process authority', async () => {
  const [director, store] = await Promise.all([
    readFile(new URL('../src/components/Sidebar/DirectorChat.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8'),
  ])
  for (const label of ['Image creator', 'Continuity editor', 'Creator LoRAs', 'Editor LoRAs', 'Legacy combined image model']) {
    assert.match(director, new RegExp(label))
  }
  assert.equal((director.match(/id="director-final-video-upsampling"/g) || []).length, 1)
  assert.equal((director.match(/id="director-final-video-film-grain"/g) || []).length, 1)
  assert.doesNotMatch(director, /director-intermediate-image-(upsampling|film-grain)/)
  assert.match(store, /image_creator_model: creatorOverride \|\| null/)
  assert.match(director, /s\.directorLegacyImageModel/)
  assert.doesNotMatch(director, /legacyImageModel = useStore\(s => s\.selectedModelPerMode\.image/)
  assert.match(store, /\.\.\.imageRoleRequest\.wire/)
  assert.doesNotMatch(store, /image_model: selectedImageCreatorModel/)
  assert.doesNotMatch(store, /image_spatial_upsampling:/)
  assert.doesNotMatch(store, /image_film_grain_(intensity|saturation):/)
  assert.doesNotMatch(store, /directorImage(SpatialUpsampling|FilmGrain)/)
  assert.doesNotMatch(store, /loadDirectorFromPipeline/)
  const previewStart = store.indexOf('directorGenerateStartImages: async () =>')
  const previewEnd = store.indexOf('directorApplyToClips:', previewStart)
  assert.ok(previewStart >= 0 && previewEnd > previewStart)
  const previewSource = store.slice(previewStart, previewEnd)
  assert.match(previewSource, /const imageRoleRequest = await _captureDirectorImageRoleRequest\(get, requestExplicitOutput\)/)
  assert.match(previewSource, /\.\.\.imageRoleRequest\.wire/)
  assert.doesNotMatch(previewSource, /selectedModelPerMode\.image|savedParamsPerMode\.image|savedLoraPerMode\.image/)
  assert.doesNotMatch(previewSource, /activated_loras|loras_multipliers|model_type:/)
  assert.match(store, /_initialDirectorImageRoles/)
  assert.match(store, /directorImageRolesConfigured: _initialDirectorImageRoles !== null/)
  for (const match of store.matchAll(/_captureDirectorImageRoleRequest\(get, ([^)]+)\)/g)) {
    assert.match(match[1], /requestExplicitOutput|state\.explicitOutput/)
  }
  assert.match(director, /Loading host permissions…/)
  assert.match(director, /waitForModelDownloadTerminal/)
  assert.match(director, /openDirectorModelVisibility/)
  assert.match(store, /_refreshDirectorModelAdmissionCatalog\(\(\) => get\(\)\.loadModels\(\)\)/)
  assert.match(store, /preflightDirectorPipeline\(\{/)
  assert.match(store, /pipeline_type: pipelineType/)
  assert.match(store, /director_resolution_preset: directorResolution/)
  assert.match(store, /director_aspect_ratio: directorAspectRatio/)
  assert.match(store, /directorPreflight\.resolved\.video_resolution/)
  assert.match(store, /directorPreflight\.resolved\.image_resolution/)
  assert.match(previewSource, /resolveDeclaredResolution\(/)
  assert.doesNotMatch(previewSource, /resolveResolution\(null/)
  assert.ok(store.indexOf('const referenceUploads = await Promise.allSettled') < store.indexOf('const directorPreflight = await api.preflightDirectorPipeline'))
  assert.match(store, /character_ref_labels: charLabels\.length > 0 \? charLabels : undefined/)
  assert.match(store, /location_ref_labels: locLabels\.length > 0 \? locLabels : undefined/)
  assert.doesNotMatch(store.slice(store.indexOf('startDirectorPipeline: async () =>'), store.indexOf('continuePipeline:', store.indexOf('startDirectorPipeline: async () =>'))), /skip failed uploads|Failed to upload reference image for pipeline/)
  assert.match(store, /continuity_editor_model: imageRoleRequest\.effective_editor_model/)
  assert.doesNotMatch(director, /const fallbackModel = compatibleModels/)
  assert.doesNotMatch(director, /selectedModelPerMode\.video \|\| 'ltx2_22B_distilled_1_1'/)
  assert.match(store, /video: initialVideoModelType/)
  for (const component of ['video_model', 'character_reference', 'location_reference', 'starting_image']) {
    assert.match(director, new RegExp(`data-director-component=(?:"|\\{)${component}`))
  }
  assert.match(director, /data-director-component=\{modelComponent\}/)
  assert.match(director, /data-director-component=\{loraComponent\}/)
  assert.match(director, /role === 'creator'\s*\? 'image_creator_model' : 'continuity_editor_model'/)
  assert.match(director, /role === 'creator'\s*\? 'image_creator_lora' : 'continuity_editor_lora'/)
  assert.match(director, /componentError && !isShortFilm && DIRECTOR_MODEL_COMPONENTS\.has\(componentError\.component\)/)
  assert.match(director, /aria-label="Director model recovery"/)
  assert.match(director, /clearDirectorComponentError\('starting_image'\)/)
  assert.match(director, /clearDirectorComponentError\(type === 'char' \? 'character_reference' : 'location_reference'\)/)
  assert.match(director, /scrollIntoView\(\{ behavior: 'smooth', block: 'center' \}\)/)
  assert.match(director, /select:not\(:disabled\)/)
  assert.match(director, /:not\(\.hidden\):not\(\.sr-only\):not\(\[hidden\]\)/)
  assert.match(director, /\.focus\(\{ preventScroll: true \}\)/)
})

test('Director mobile dashboard and output selectors keep accessible targets without horizontal overflow', async () => {
  const [director, css, sidebar] = await Promise.all([
    readFile(new URL('../src/components/Sidebar/DirectorChat.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/index.css', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/Sidebar/Sidebar.tsx', import.meta.url), 'utf8'),
  ])
  const dashboardStart = director.indexOf('onClick={() => useStore.getState().setDashboardOpen(true)}')
  const dashboardEnd = director.indexOf('</button>', dashboardStart)
  const aspectStart = director.indexOf('function DirectorAspectRatioSelector()')
  const aspectEnd = director.indexOf('function DirectorResolutionSelector()', aspectStart)
  const resolutionStart = aspectEnd
  const resolutionEnd = director.indexOf('function SkillSelector(', resolutionStart)

  assert.ok(dashboardStart >= 0 && dashboardEnd > dashboardStart)
  assert.ok(aspectStart >= 0 && aspectEnd > aspectStart)
  assert.ok(resolutionStart >= 0 && resolutionEnd > resolutionStart)

  const dashboard = director.slice(dashboardStart, dashboardEnd)
  const aspect = director.slice(aspectStart, aspectEnd)
  const resolution = director.slice(resolutionStart, resolutionEnd)

  assert.match(dashboard, /aria-haspopup="dialog"/)
  assert.match(dashboard, /aria-label="Open Director pipeline dashboard"/)
  assert.match(dashboard, /mobile-control-target/)
  assert.match(dashboard, /focus-visible:ring-2/)

  assert.match(aspect, /role="radiogroup" aria-label="Director aspect ratio"/)
  assert.match(aspect, /grid-cols-2[^"`]*sm:grid-cols-5/)
  assert.match(aspect, /role="radio"/)
  assert.match(aspect, /aria-checked=\{ratio === p\.value\}/)
  assert.match(aspect, /tabIndex=\{ratio === p\.value \? 0 : -1\}/)
  assert.match(aspect, /mobile-control-target min-w-0/)
  assert.match(aspect, /focus-visible:ring-2/)
  assert.match(aspect, /event\.key === 'ArrowRight'/)

  assert.match(resolution, /role="radiogroup" aria-label="Director resolution"/)
  assert.match(resolution, /grid-cols-2[^"`]*sm:grid-cols-4/)
  assert.match(resolution, /role="radio"/)
  assert.match(resolution, /aria-checked=\{resolution === p\}/)
  assert.match(resolution, /selectedPresetAvailable = availablePresets\.includes\(resolution\)/)
  assert.match(resolution, /aria-disabled=\{!available\}/)
  assert.match(resolution, /disabled=\{!available\}/)
  assert.match(resolution, /tabIndex=\{available && \(resolution === p \|\| \(!selectedPresetAvailable && index === 1\)\) \? 0 : -1\}/)
  assert.match(resolution, /Loading exact model resolutions/)
  assert.match(resolution, /carried selection is unavailable/)
  assert.doesNotMatch(resolution, /\['480p', '540p', '720p', '1080p'\]/)
  assert.match(resolution, /mobile-control-target min-w-0/)
  assert.match(resolution, /focus-visible:ring-2/)
  assert.match(resolution, /setResolution\(nextPreset\)/)

  const mobileCss = css.slice(css.indexOf('@media (max-width: 767px)'))
  assert.match(mobileCss, /\.mobile-control-target\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/s)
  assert.doesNotMatch(css, /@media \(max-width: 768px\)[\s\S]*\.mobile-control-target/)
  assert.match(sidebar, /w-\[380px\] max-w-\[85vw\]/)
  assert.ok((320 * 0.85) - 32 >= (2 * 44) + 6, 'two 44px controls and their gap fit the 320px drawer content box')
})
