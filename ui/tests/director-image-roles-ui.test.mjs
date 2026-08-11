import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

import {
  createDirectorImageRoleLoraSelection,
  fetchDirectorCapabilities,
  getDirectorHostActionAccessState,
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
        contents: `${source}\nexport { _captureDirectorImageRoleRequest }\n`,
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
      /default is unavailable in this session/,
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
})
