import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { transform } from 'esbuild'
import ts from 'typescript'

const director = await readFile(new URL('../src/components/SettingsDrawer/DirectorLoraSelector.tsx', import.meta.url), 'utf8')
const profiles = await readFile(new URL('../src/components/Sidebar/GenerationProfiles.tsx', import.meta.url), 'utf8')
const picker = director.slice(director.indexOf('function DirectorProfileLoraPicker('), director.indexOf('/**\n * Standalone LoRA selector'))
const status = await readFile(new URL('../src/components/GenerationProfileRefreshStatus.tsx', import.meta.url), 'utf8')
const result = await transform(`
  let state
  const useStore = select => select(state)
  const useEffect = () => {}
  const FolderOpen = 'icon'
  const node = (type, props, ...children) => typeof type === 'function'
    ? type(props || {}) : { type, props: props || {}, children }
  ${status}
  ${picker}
  export function render(value) { state = value; return DirectorProfileLoraPicker({mode:'video', modelType:'test-model', availableLoras: state.availableLoras || []}) }
`, { loader: 'tsx', format: 'esm', jsxFactory: 'node' })
const { render } = await import(`data:text/javascript;base64,${Buffer.from(result.code).toString('base64')}`)
const walk = value => Array.isArray(value) ? value.flatMap(walk) : value && typeof value === 'object' ? [value, ...value.children.flatMap(walk)] : []
const text = value => Array.isArray(value) ? value.map(text).join('') : value && typeof value === 'object' ? value.children.map(text).join('') : typeof value === 'string' ? value : ''

test('Director shows profile loading and retry even without matching profiles', () => {
  let retries = 0
  const state = { presets: [], presetsLoading: false, presetsError: null, savedLoraPerMode: {}, loadPresets: () => { retries += 1 } }
  assert.equal(render(state), null)
  const loading = render({ ...state, presetsLoading: true })
  assert.match(text(loading), /Loading profiles/)
  for (const busy of [false, true]) {
    const failure = render({ ...state, presetsLoading: busy, presetsError: 'Profiles could not be refreshed.' })
    assert.match(text(failure), /Profiles could not be refreshed/)
    const retry = walk(failure).find(item => item.type === 'button')
    assert.equal(retry.props.disabled, busy)
    if (!busy) retry.props.onClick()
  }
  assert.equal(retries, 1)
})

test('Director retains scoped LoRA-only import through a transient refresh error', () => {
  const calls = []
  const preset = { id: 'saved', name: 'Saved look', mode: 'video', model_type: 'test-model', activated_loras: ['look.safetensors'], loras_multipliers: '0.5', lora_weights: { 'look.safetensors': [0.5] } }
  const tree = render({ presets: [preset], presetsLoading: false, presetsError: 'Profiles could not be refreshed.', availableLoras: ['look.safetensors'], savedLoraPerMode: { video: { availableLoras: ['stale.safetensors'] } }, loadPresets() {}, directorSetLora: (...args) => calls.push(args) })
  const button = walk(tree).find(item => item.props['aria-label'] === 'Use LoRAs from Saved look')
  button.props.onClick()
  assert.deepEqual(calls, [['video', preset.activated_loras, '0.5', preset.lora_weights, ['look.safetensors'], 'test-model']])
  assert.match(profiles, /<GenerationProfileRefreshStatus/)
})

test('catalog refresh never drops selected video LoRAs or silently changes their phases', async () => {
  const ast = ts.createSourceFile('DirectorLoraSelector.tsx', director, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let effect
  const visit = node => {
    if (ts.isCallExpression(node) && node.expression.getText(ast) === 'useEffect'
      && node.arguments[0]?.getText(ast).includes('api.fetchLoras(modelType)')) effect = node
    ts.forEachChild(node, visit)
  }
  visit(ast)
  assert.ok(effect)
  const compiled = await transform(effect.getText(ast), { loader: 'tsx', format: 'esm' })
  const execute = new Function('context', `
    const {useEffect,modelType,api,queueMicrotask,setLoading,setAvailableLoras,setPhases,
      setActivatedLoras,setLoraWeights,directorSetLora,loraWeights,mode,serializeMultipliers}=context
    ${compiled.code}
  `)
  for (const catalog of [
    { loras: ['new.safetensors'], guidance_max_phases: 1 },
    { loras: ['old.safetensors'], guidance_max_phases: 2 },
    { loras: ['old.safetensors'], guidance_max_phases: 0 },
  ]) {
    let selected = ['old.safetensors']
    let weights = { 'old.safetensors': [0] }
    const writes = []
    let available
    let phases
    execute({
      useEffect: effect => effect(), modelType: 'new-model', mode: 'video', queueMicrotask,
      api: { fetchLoras: async () => catalog }, setLoading() {},
      setAvailableLoras: value => { available = value }, setPhases: value => { phases = value },
      setActivatedLoras: update => { selected = typeof update === 'function' ? update(selected) : update },
      setLoraWeights: value => { weights = value }, loraWeights: weights,
      directorSetLora: (...args) => writes.push(args), serializeMultipliers: () => 'unused',
    })
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(available, catalog.loras)
    assert.equal(phases, Math.max(1, catalog.guidance_max_phases))
    assert.deepEqual(selected, ['old.safetensors'])
    assert.deepEqual(weights, { 'old.safetensors': [0] })
    assert.deepEqual(writes, [], 'a catalog response is not a user selection or confirmation')
  }
})

test('late LoRA details never rewrite saved weights for an existing selection', async () => {
  const ast = ts.createSourceFile('DirectorLoraSelector.tsx', director, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let effect
  const visit = node => {
    if (ts.isCallExpression(node) && node.expression.getText(ast) === 'useEffect'
      && node.arguments[0]?.getText(ast).includes('fetchLoraDetails(modelType)')) effect = node
    ts.forEachChild(node, visit)
  }
  visit(ast)
  assert.ok(effect)
  const compiled = await transform(effect.getText(ast), { loader: 'tsx', format: 'esm' })
  let resolveDetails
  const detailResponse = new Promise(resolve => { resolveDetails = resolve })
  const writes = []
  const weightUpdates = []
  let savedWeights = { 'stale.safetensors': [0] }
  const execute = new Function('context', `
    const {useEffect,modelType,fetchLoraDetails,loraDetailsRequest,activatedLoras,
      guideStatus,fetchLoraGuide,setLoraWeightRecs,setGuideTexts,setGuideStatus,
      setLoraDates,updateWeight}=context
    ${compiled.code}
  `)
  execute({
    useEffect: effect => effect(),
    modelType: 'model-b',
    fetchLoraDetails: () => detailResponse,
    loraDetailsRequest: { current: 0 },
    activatedLoras: ['stale.safetensors'],
    guideStatus: {},
    fetchLoraGuide: async () => ({ guide: null }),
    setLoraWeightRecs: value => { writes.push(['recommendations', value]) },
    setGuideTexts: update => { writes.push(['guides', update({})]) },
    setGuideStatus: update => { writes.push(['status', update({})]) },
    setLoraDates: value => { writes.push(['dates', value]) },
    updateWeight: (...args) => {
      weightUpdates.push(args)
      savedWeights = { 'stale.safetensors': [0.25] }
    },
  })
  resolveDetails({
    loras: [{
      filename: 'stale.safetensors',
      recommended_weights: { default: 0.25, min: 0, max: 2 },
    }],
  })
  await detailResponse
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(savedWeights, { 'stale.safetensors': [0] })
  assert.deepEqual(weightUpdates, [], 'recommendations stay advisory until a new LoRA is added')
  assert.ok(writes.some(([kind]) => kind === 'recommendations'))
})

test('ordinary edits retain the prior LoRA binding until explicit confirmation', async () => {
  const ast = ts.createSourceFile('DirectorLoraSelector.tsx', director, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let persistCallback
  let confirmSelection
  const visit = node => {
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'persist'
      && ts.isCallExpression(node.initializer)
      && node.initializer.expression.getText(ast) === 'useCallback') {
      persistCallback = node.initializer.arguments[0]
    }
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'confirmSelection') {
      confirmSelection = node.initializer
    }
    ts.forEachChild(node, visit)
  }
  visit(ast)
  assert.ok(persistCallback)
  assert.ok(confirmSelection)
  const compiled = await transform(`
    const persist = ${persistCallback.getText(ast)}
    const confirmSelection = ${confirmSelection.getText(ast)}
  `, { loader: 'tsx', format: 'cjs' })
  const execute = new Function('context', `
    const {mode,previousBinding,availableLoras,directorSetLora,serializeMultipliers,
      modelType,activatedLoras,loraWeights,phases,unavailable,setLoraWeights}=context
    ${compiled.code}
    return { persist, confirmSelection }
  `)
  const calls = []
  const weights = { 'look.safetensors': [0] }
  const context = {
    mode: 'video',
    previousBinding: 'model-a',
    availableLoras: ['look.safetensors'],
    directorSetLora: (...args) => calls.push(args),
    serializeMultipliers: () => '0',
    modelType: 'model-b',
    activatedLoras: ['look.safetensors'],
    loraWeights: weights,
    phases: 1,
    unavailable: [],
    setLoraWeights: () => {},
  }
  const { persist, confirmSelection: confirm } = execute(context)
  persist(['look.safetensors'], weights)
  assert.equal(calls.at(-1)[5], 'model-a')
  confirm()
  assert.equal(calls.at(-1)[5], 'model-b')

  const freshCalls = []
  const fresh = execute({
    ...context,
    previousBinding: undefined,
    directorSetLora: (...args) => freshCalls.push(args),
  })
  fresh.persist(['new.safetensors'], { 'new.safetensors': [0] })
  assert.equal(freshCalls.at(-1)[5], undefined, 'a first selection lets the store bind the current model')

  const legacyCalls = []
  const legacy = execute({
    ...context,
    previousBinding: '',
    directorSetLora: (...args) => legacyCalls.push(args),
  })
  legacy.persist(['legacy.safetensors'], { 'legacy.safetensors': [0] })
  assert.equal(legacyCalls.at(-1)[5], '', 'legacy unbound selections keep the explicit empty sentinel')
})
