import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

const loraSelectorSource = await readFile(
  new URL('../src/components/SettingsDrawer/LoraSelector.tsx', import.meta.url),
  'utf8',
)
const generateButtonSource = await readFile(
  new URL('../src/components/Sidebar/GenerateButton.tsx', import.meta.url),
  'utf8',
)

function asDataModule(contents) {
  return `data:text/javascript;base64,${Buffer.from(contents).toString('base64')}`
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

function renderTree(value) {
  if (Array.isArray(value)) return value.map(renderTree)
  if (!value || typeof value !== 'object') return value
  if (typeof value.type === 'function') return renderTree(value.type(value.props || {}))
  return {
    ...value,
    props: {
      ...value.props,
      children: renderTree(value.props?.children),
    },
  }
}

function textContent(value) {
  if (Array.isArray(value)) return value.map(textContent).join('')
  if (value == null || typeof value === 'boolean') return ''
  if (typeof value === 'object') return textContent(value.props?.children)
  return String(value)
}

let controlsPromise
function loadControls() {
  if (controlsPromise) return controlsPromise
  controlsPromise = build({
    stdin: {
      contents: `
        export { ModelSelector } from './src/components/Sidebar/ModelSelector.tsx'
        export { GenerateButton } from './src/components/Sidebar/GenerateButton.tsx'
      `,
      resolveDir: new URL('..', import.meta.url).pathname,
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
      name: 'adaptive-control-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'adaptive-controls' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'adaptive-controls' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'icons', namespace: 'adaptive-controls' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'adaptive-controls' }))
        bundle.onResolve({ filter: /api\/client$/ }, () => ({ path: 'api', namespace: 'adaptive-controls' }))
        bundle.onResolve({ filter: /InfoTooltip$/ }, () => ({ path: 'tooltip', namespace: 'adaptive-controls' }))
        bundle.onResolve({ filter: /lib\/manualInstallation$/ }, () => ({ path: 'manual', namespace: 'adaptive-controls' }))
        bundle.onResolve({ filter: /H3PerformanceProfiles$/ }, () => ({ path: 'estimate', namespace: 'adaptive-controls' }))
        bundle.onResolve({ filter: /lib\/queueProjection$/ }, () => ({ path: 'queue', namespace: 'adaptive-controls' }))
        bundle.onLoad({ filter: /.*/, namespace: 'adaptive-controls' }, args => {
          if (args.path === 'react') return { contents: `
            export const useEffect = () => {}
            export const useState = initial => {
              const value = globalThis.__maestroAdaptiveStateValues?.length
                ? globalThis.__maestroAdaptiveStateValues.shift()
                : (typeof initial === 'function' ? initial() : initial)
              return [value, update => globalThis.__maestroAdaptiveStateUpdates?.push(
                typeof update === 'function' ? update(value) : update,
              )]
            }
            export const useRef = initial => ({ current: initial })
          ` }
          if (args.path === 'jsx-runtime') return { contents: `
            export const Fragment = Symbol.for('adaptive-controls-fragment')
            export const jsx = (type, props, key) => ({ type, key, props: props || {} })
            export const jsxs = jsx
          ` }
          if (args.path === 'icons') return { contents: `
            const icon = props => ({ type: 'svg', props })
            export const AlertTriangle = icon
            export const Check = icon
            export const ChevronDown = icon
            export const HardDrive = icon
            export const ListPlus = icon
            export const Loader2 = icon
            export const Play = icon
            export const Plus = icon
          ` }
          if (args.path === 'store') return { contents: `
            export const useStore = selector => selector(globalThis.__maestroAdaptiveControlsStore)
            export const getFamiliesForMode = (_mode, families) => families
            export const getModelsForFamily = (family, models) => models.filter(model => model.family === family)
          ` }
          if (args.path === 'api') return { contents: `
            export const fetchH3AccelerationStatus = async () => ({
              w4a8: { available: true, reason: '' },
              sage2: { available: true, reason: '' },
            })
            export const verifyManualCheckpoint = async () => ({})
          ` }
          if (args.path === 'tooltip') return { contents: 'export const InfoTooltip = () => null' }
          if (args.path === 'manual') return { contents: `
            export const formatManualInstallationBytes = value => String(value)
            export const manualInstallationDestination = () => 'app/ckpts'
          ` }
          if (args.path === 'estimate') return { contents: 'export const H3EstimateBadge = () => null' }
          return { contents: 'export const projectLogicalQueue = () => ({ visibleJobs: [] })' }
        })
      },
    }],
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return controlsPromise
}

function model(model_type, name) {
  return {
    model_type,
    name,
    family: 'video',
    architecture: model_type,
    is_i2v: true,
    is_t2v: true,
    guidance_max_phases: 1,
    fps: 24,
    is_downloaded: true,
    downloadable: true,
    execution_allowed: true,
    required_host_terms: [],
  }
}

function adaptiveStore(overrides = {}) {
  const models = [
    model('minimax_h3', 'MiniMax H3 FL2VA'),
    model('minimax_h3_pinkcherry_fl2va', 'PinkCherry FL2VA'),
    model('minimax_h3_w4a8_fl2va', 'Kijai W4A8 FL2VA'),
    { ...model('minimax_h3_ref2va', 'MiniMax H3 Ref2VA'), supports_ref_images: true },
  ]
  return {
    jobs: [],
    startGeneration() {},
    setSidebarOpen() {},
    modelOptionsLoading: false,
    modelsLoaded: true,
    activeWorkspace: 'project',
    models,
    families: [{ id: 'video', label: 'Video', order: 1 }],
    enabledModels: new Set(models.map(entry => entry.model_type)),
    generationMode: 'video',
    editSubMode: 'recast',
    audioSubMode: 'music',
    params: {
      model_type: 'minimax_h3',
      h3_adaptive_conditioning: true,
      h3_adaptive_fl2va_model: 'minimax_h3',
      h3_adaptive_ref2va_model: 'minimax_h3_ref2va',
      custom_settings: {},
      image_mode: 0,
      image_start: '',
      activated_loras: [],
      loras_multipliers: '',
    },
    modelOptions: { architecture: 'minimax_h3' },
    selectModel: async () => true,
    selectAdaptiveH3Model: async () => true,
    h3SelectedProfile: 'custom',
    h3PerformanceProfiles: [],
    h3ModelProfileCompatibility: { minimax_h3_pinkcherry_fl2va: null },
    refreshH3ModelProfileCompatibility: async () => {},
    openModelVisibility() {},
    accessContext: { machine_controls: true },
    hostTerms: {},
    hostTermsLoading: false,
    hostTermsError: null,
    loadHostTerms: async () => {},
    acceptHostTerm: async () => {},
    loadModels: async () => {},
    explicitOutput: false,
    spatialUpsampling: 'none',
    h3CurrentEstimate: null,
    h3EstimateLoading: false,
    startImage: null,
    endImage: null,
    imageRefs: [],
    editVideoPath: '',
    outpaintVideoBox: { x: 0, y: 0, w: 1, h: 1 },
    ...overrides,
  }
}

test('adaptive model groups render both selected checkpoints with stable accessible names', async t => {
  const { ModelSelector } = await loadControls()
  const previous = globalThis.__maestroAdaptiveControlsStore
  t.after(() => { globalThis.__maestroAdaptiveControlsStore = previous })
  globalThis.__maestroAdaptiveControlsStore = adaptiveStore()

  const tree = renderTree(ModelSelector())
  const elements = flattenElements(tree)
  const triggers = elements.filter(element => element.type === 'button' && element.props['aria-haspopup'] === 'dialog')
  assert.deepEqual(
    triggers.map(trigger => trigger.props['aria-label']),
    [
      'Text & frames model: MiniMax H3 FL2VA',
      'References model: MiniMax H3 Ref2VA',
    ],
  )
  assert.equal(elements.filter(element => textContent(element) === 'Text & frames').length, 1)
  assert.equal(elements.filter(element => textContent(element) === 'References').length, 1)
})

test('invalid and missing saved checkpoint identities stay visible for explicit repair', async t => {
  const { ModelSelector } = await loadControls()
  const previous = globalThis.__maestroAdaptiveControlsStore
  t.after(() => { globalThis.__maestroAdaptiveControlsStore = previous })

  const invalid = adaptiveStore()
  invalid.params = { ...invalid.params, h3_adaptive_fl2va_model: 'minimax_h3_ref2va' }
  globalThis.__maestroAdaptiveControlsStore = invalid
  let tree = renderTree(ModelSelector())
  let elements = flattenElements(tree)
  let trigger = elements.find(element => element.props?.['aria-label']?.startsWith('Text & frames model:'))
  assert.equal(trigger?.props['aria-label'], 'Text & frames model: MiniMax H3 Ref2VA')
  assert.match(textContent(tree), /does not belong in text & frames/)

  const missing = adaptiveStore()
  missing.params = { ...missing.params, h3_adaptive_fl2va_model: 'removed_fl2va_checkpoint' }
  globalThis.__maestroAdaptiveControlsStore = missing
  tree = renderTree(ModelSelector())
  elements = flattenElements(tree)
  trigger = elements.find(element => element.props?.['aria-label']?.startsWith('Text & frames model:'))
  assert.equal(trigger?.props['aria-label'], 'Text & frames model: removed_fl2va_checkpoint')
  assert.match(textContent(tree), /saved checkpoint is no longer available/)
})

test('Generate is disabled for invalid or catalog-missing adaptive checkpoints', async t => {
  const { GenerateButton } = await loadControls()
  const previous = globalThis.__maestroAdaptiveControlsStore
  t.after(() => { globalThis.__maestroAdaptiveControlsStore = previous })

  const invalid = adaptiveStore()
  invalid.params = { ...invalid.params, h3_adaptive_fl2va_model: 'minimax_h3_ref2va' }
  globalThis.__maestroAdaptiveControlsStore = invalid
  let buttons = flattenElements(renderTree(GenerateButton())).filter(element => element.type === 'button')
  assert.equal(buttons[0]?.props.disabled, true)
  assert.match(textContent(buttons[0]), /Choose H3 models/)
  assert.equal(buttons[0]?.props.title, 'Choose an available FL2VA model for text and frames.')

  const missing = adaptiveStore()
  missing.models = missing.models.filter(entry => entry.model_type !== 'minimax_h3_ref2va')
  globalThis.__maestroAdaptiveControlsStore = missing
  buttons = flattenElements(renderTree(GenerateButton())).filter(element => element.type === 'button')
  assert.equal(buttons[0]?.props.disabled, true)
  assert.match(textContent(buttons[0]), /Model unavailable/)
  assert.match(buttons[0]?.props.title, /no longer in the model catalog/)
})

test('Generate blocks incompatible saved Sage2 and repairs only the selected engine', async t => {
  const { GenerateButton } = await loadControls()
  const previousStore = globalThis.__maestroAdaptiveControlsStore
  const previousStateValues = globalThis.__maestroAdaptiveStateValues
  const previousStateUpdates = globalThis.__maestroAdaptiveStateUpdates
  t.after(() => {
    globalThis.__maestroAdaptiveControlsStore = previousStore
    globalThis.__maestroAdaptiveStateValues = previousStateValues
    globalThis.__maestroAdaptiveStateUpdates = previousStateUpdates
  })

  const changes = []
  const store = adaptiveStore({ imageRefs: [{ id: 'local-reference' }] })
  store.params = {
    ...store.params,
    custom_settings: { h3_attention_engine: 'sage2', h3_sol_tau: 1.7 },
  }
  store.setParam = (...args) => changes.push(args)
  globalThis.__maestroAdaptiveControlsStore = store
  globalThis.__maestroAdaptiveStateValues = [
    false,
    {
      w4a8: { available: true, reason: '' },
      sage2: { available: true, reason: '' },
    },
  ]
  globalThis.__maestroAdaptiveStateUpdates = []

  const tree = renderTree(GenerateButton())
  const buttons = flattenElements(tree).filter(element => element.type === 'button')
  assert.equal(buttons[0]?.props.disabled, true)
  assert.match(textContent(buttons[0]), /Change H3 setup/)
  assert.equal(buttons[0]?.props.title, 'SageAttention2++ cannot run with reference media. Remove the references or use Dense SDPA.')
  const repair = buttons.find(button => textContent(button) === 'Use Dense SDPA')
  assert.ok(repair)
  repair.props.onClick()
  assert.deepEqual(changes, [[
    'custom_settings',
    { h3_attention_engine: 'sdpa', h3_sol_tau: 1.7 },
  ]])
})

test('Generate shares one acceleration request between W4A8 and Sage2 checks', () => {
  const effect = generateButtonSource.slice(
    generateButtonSource.indexOf('useEffect(() => {\n    if (!usesW4a8 && !usesSage2) return'),
    generateButtonSource.indexOf('// Check if i2v-only model'),
  )
  assert.match(effect, /if \(!usesW4a8 && !usesSage2\) return/)
  assert.equal(effect.match(/fetchH3AccelerationStatus\(false\)/g)?.length, 1)
  assert.match(effect, /w4a8:/)
  assert.match(effect, /sage2:/)
})

test('invalid adaptive model IDs are gated before every LoRA model endpoint', () => {
  const assertBefore = (source, gate, request, message) => {
    const gateIndex = source.indexOf(gate)
    const requestIndex = source.indexOf(request)
    assert.notEqual(gateIndex, -1, `${message}: missing gate`)
    assert.notEqual(requestIndex, -1, `${message}: missing request`)
    assert.ok(gateIndex < requestIndex, message)
  }
  assert.match(loraSelectorSource, /h3AdaptiveSelectionError/)
  assert.match(
    loraSelectorSource,
    /const detailModelTypes = useMemo\([\s\S]*?h3SelectionError[\s\S]*?EMPTY_LORAS/,
  )

  const detailsCallback = loraSelectorSource.slice(
    loraSelectorSource.indexOf('const fetchCurrentLoraDetails'),
    loraSelectorSource.indexOf('// Trigger a fresh CivitAI check'),
  )
  assertBefore(
    detailsCallback,
    'if (h3SelectionError)',
    'detailModelTypes.map(type => fetchLoraDetails(type))',
    'details callback rejects an invalid pair before mapping model IDs to detail URLs',
  )

  const manualCheck = loraSelectorSource.slice(
    loraSelectorSource.indexOf('const handleCheckUpdates'),
    loraSelectorSource.indexOf('// Count of LoRAs'),
  )
  assertBefore(
    manualCheck,
    'if (h3SelectionError)',
    'await checkLoraUpdates(true)',
    'manual update check stops before its detail refetch path',
  )

  const detailsEffect = loraSelectorSource.slice(
    loraSelectorSource.indexOf('// Load LoRA details'),
    loraSelectorSource.indexOf('const handleGenerateGuide'),
  )
  assertBefore(
    detailsEffect,
    'if (!modelType || h3SelectionError) return',
    'fetchLoraGuide(guideModelType, lora)',
    'automatic guide and detail loading stops before using an invalid model ID',
  )

  const generateGuide = loraSelectorSource.slice(
    loraSelectorSource.indexOf('const handleGenerateGuide'),
    loraSelectorSource.indexOf('// Recast owns'),
  )
  assertBefore(
    generateGuide,
    'if (h3SelectionError) return',
    'await generateLoraGuide(guideModelType, filename)',
    'guide generation stops before using an invalid model ID',
  )
})
