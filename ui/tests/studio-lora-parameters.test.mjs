import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'
import { createStudioLoraParameterSelection, studioLoraParameterSchema, validateStudioLoraParameters } from '../src/api/client.ts'

const filename = 'finish.safetensors', digest = 'a'.repeat(64)
const lora = { filename, parameter_execution: { studio_image_generation: true }, parameter_schema: {
  schema_version: 1, schema_digest: digest, parameters: [
    { id: 'toggle', label: 'Toggle', type: 'boolean', required: true, default: false, scopes: ['generation'], roles: [] },
    { id: 'amount', label: 'Amount', type: 'integer', required: true, default: 0, minimum: 0, maximum: 4, scopes: ['generation'], roles: [] },
    { id: 'text', label: 'Text', type: 'text', required: true, default: '', scopes: ['generation'], roles: [] },
  ],
} }

test('Studio requires executable server capability and retains typed empty defaults', () => {
  assert.equal(studioLoraParameterSchema({ ...lora, parameter_execution: undefined }), undefined)
  const selection = createStudioLoraParameterSelection(lora)
  assert.deepEqual(selection, { parameter_schema_digest: digest, parameter_values: { toggle: false, amount: 0, text: '' } })
  assert.deepEqual(validateStudioLoraParameters([filename], { [filename]: selection }, [lora]), [])
})
test('stale, unavailable, orphaned and invalid choices fail visibly', () => {
  const selected = createStudioLoraParameterSelection(lora), wire = { [filename]: selected }
  assert.match(validateStudioLoraParameters([], wire, [lora]).join(), /unselected/)
  assert.match(validateStudioLoraParameters([filename], wire, []).join(), /unavailable/)
  assert.match(validateStudioLoraParameters([filename], wire, [{ ...lora, parameter_schema: { ...lora.parameter_schema, schema_digest: 'b'.repeat(64) } }]).join(), /Remove and add/)
  assert.match(validateStudioLoraParameters([filename], { [filename]: { ...selected, parameter_values: [] } }, [lora]).join(), /invalid/)
  assert.match(validateStudioLoraParameters([filename], { [filename]: { ...selected, parameter_values: { toggle: 'false', amount: -1, unknown: true } } }, [lora]).join(), /Unknown parameter.*Yes or No.*at least/)
})

let bundle, realm = 0
async function withStore(run) {
  const originals = Object.fromEntries(['window', 'document', 'localStorage', 'sessionStorage', 'fetch'].map(key => [key, globalThis[key]]))
  const storage = () => { const values = new Map(); return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) } }
  const alerts = [], calls = []
  globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout, setInterval, clearInterval,
    alert: value => alerts.push(value), location: { hostname: '127.0.0.1' }, matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }) })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false, createElement: () => ({ muted: false }) })
  globalThis.localStorage = storage(); globalThis.sessionStorage = storage()
  globalThis.fetch = async input => {
    const url = String(input); calls.push(url)
    const body = url.includes('/loras/') ? url.includes('/details') ? { loras: [lora] } : { loras: [filename] }
      : url.includes('/model-options/') ? { guidance_max_phases: 1 } : url.includes('/defaults/') ? {} : null
    if (!body) throw new Error(`Unexpected request: ${url}`)
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    bundle ||= build({ stdin: { contents: "export { useStore } from './src/stores/useStore.ts'; export { captureGenerationProfileSettings, restoreGenerationProfileSettings } from './src/lib/generationProfiles.ts'", resolveDir: new URL('..', import.meta.url).pathname, loader: 'js' }, bundle: true, platform: 'node', format: 'esm', write: false }).then(result => result.outputFiles[0].text)
    const module = await import(`data:text/javascript;base64,${Buffer.from(await bundle).toString('base64')}#studio-lora-${++realm}`)
    const { useStore } = module
    useStore.setState(state => ({ generationMode: 'image', activeWorkspace: 'test-project',
      params: { ...state.params, model_type: 'flux2_klein_9b', image_mode: 1, prompt: 'authored', activated_loras: [filename], loras_multipliers: '0.75' },
      models: [{ model_type: 'flux2_klein_9b', family: 'flux2' }], modelOptions: { guidance_max_phases: 1 }, loraWeights: { [filename]: [0.75] }, availableLoras: [filename] }))
    await run(module, alerts, calls)
    await new Promise(resolve => setTimeout(resolve, 0))
  } finally { for (const [key, value] of Object.entries(originals)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value } }
}

test('store keeps detached model-bound values across modes and removes them with a LoRA', () => withStore(async ({ useStore }) => {
  const selected = createStudioLoraParameterSelection(lora)
  useStore.getState().setLoraParameters('other', filename, selected)
  assert.equal(useStore.getState().params.lora_parameters, undefined)
  useStore.getState().setLoraParameters('flux2_klein_9b', filename, selected)
  selected.parameter_values.amount = 3
  assert.equal(useStore.getState().params.lora_parameters[filename].parameter_values.amount, 0)
  useStore.getState().setGenerationMode('tools'); useStore.getState().setGenerationMode('image')
  assert.equal(useStore.getState().params.lora_parameters[filename].parameter_values.toggle, false)
  assert.equal(useStore.getState().params.loras_multipliers, '0.75')
  useStore.getState().toggleLora(filename)
  assert.deepEqual(useStore.getState().params.lora_parameters, {})
}))
test('profile capture and restore detach exact schema choices', () => withStore(async ({ useStore, captureGenerationProfileSettings, restoreGenerationProfileSettings }) => {
  const wire = { [filename]: createStudioLoraParameterSelection(lora) }
  useStore.setState(state => ({ params: { ...state.params, lora_parameters: wire } }))
  const saved = captureGenerationProfileSettings(useStore.getState().params, useStore.getState())
  wire[filename].parameter_values.amount = 4
  const restored = restoreGenerationProfileSettings(saved, {}, useStore.getInitialState())
  assert.equal(restored.params.lora_parameters[filename].parameter_values.amount, 0)
  assert.equal(restored.params.lora_parameters[filename].parameter_values.toggle, false)
}))
test('actual submission rejects stale choices before dispatch', () => withStore(async ({ useStore }, alerts, calls) => {
  const selected = createStudioLoraParameterSelection(lora); selected.parameter_schema_digest = 'b'.repeat(64)
  useStore.getState().setLoraParameters('flux2_klein_9b', filename, selected)
  await useStore.getState().startGeneration()
  assert.ok(alerts.some(message => message.includes('Remove and add')))
  assert.ok(calls.some(url => url.includes('/details')))
  assert.equal(calls.some(url => url.includes('/generate')), false)
}))
test('enhancement and specialized paths reject choices; explicit model changes clear them', () => withStore(async ({ useStore }, alerts, calls) => {
  useStore.getState().setLoraParameters('flux2_klein_9b', filename, createStudioLoraParameterSelection(lora))
  useStore.setState({ studioPromptEnhance: true }); await useStore.getState().startGeneration()
  assert.ok(alerts.some(message => message.includes('plain Image')))
  assert.equal(calls.length, 0)
  useStore.setState({ studioPromptEnhance: false })
  for (const field of ['progressive_pipeline', 'single_stage_pipeline', 'reference_pipeline']) {
    useStore.setState(state => ({ params: { ...state.params, [field]: true } }))
    await useStore.getState().startGeneration()
    assert.ok(alerts.at(-1).includes('specialized image pipelines'))
    assert.ok(useStore.getState().params.lora_parameters[filename])
    useStore.setState(state => ({ params: { ...state.params, [field]: undefined } }))
  }
  assert.equal(calls.length, 0)
  await useStore.getState().selectModel('another-image-model')
  assert.equal(useStore.getState().params.lora_parameters, undefined)
}))

test('rendered picker exposes editable parameters only on supported image paths', async () => {
  const runtime = await build({ stdin: {
    contents: "export { LoraSelector } from './src/components/SettingsDrawer/LoraSelector.tsx'",
    resolveDir: new URL('..', import.meta.url).pathname, loader: 'js',
  }, bundle: true, platform: 'node', format: 'esm', jsx: 'automatic', write: false,
  plugins: [{ name: 'picker-runtime', setup(bundle) {
    for (const [filter, path] of [[/^react$/, 'react'], [/^react\/jsx-runtime$/, 'jsx'], [/^react-dom$/, 'dom'], [/^lucide-react$/, 'icons'], [/stores\/useStore$/, 'store']]) {
      bundle.onResolve({ filter }, () => ({ path, namespace: 'picker-test' }))
    }
    bundle.onLoad({ filter: /.*/, namespace: 'picker-test' }, ({ path }) => ({ contents: {
      react: `export const useEffect=()=>{}, useRef=value=>({current:value}), useCallback=f=>f, useMemo=f=>f();
        export const useState=initial=>[globalThis.__pickerStates.length ? globalThis.__pickerStates.shift() : (typeof initial==='function'?initial():initial), ()=>{}];`,
      jsx: `export const Fragment=Symbol.for('picker-fragment'), jsx=(type,props)=>({type,props:props||{}}), jsxs=jsx;`,
      dom: `export const createPortal=value=>value;`,
      icons: `export const Search='Search',X='X',Loader2='Loader2',Globe='Globe',Sparkles='Sparkles',BookOpen='BookOpen',Info='Info',ArrowUpCircle='ArrowUpCircle',RefreshCw='RefreshCw',ArrowDownAZ='ArrowDownAZ',Clock='Clock';`,
      store: `export const useStore=selector=>selector(globalThis.__pickerStore); useStore.getState=()=>globalThis.__pickerStore;`,
    }[path] }))
  } }] })
  const { LoraSelector } = await import(`data:text/javascript;base64,${Buffer.from(runtime.outputFiles[0].text).toString('base64')}`)
  function elements(value, result = []) {
    if (Array.isArray(value)) { value.forEach(child => elements(child, result)); return result }
    if (!value || typeof value !== 'object') return result
    result.push(value)
    if (typeof value.type === 'function') elements(value.type(value.props), result)
    elements(value.props?.children, result)
    return result
  }
  const wire = { [filename]: createStudioLoraParameterSelection(lora) }
  try {
    for (const field of [null, 'progressive_pipeline', 'single_stage_pipeline', 'reference_pipeline', 'studioPromptEnhance']) {
      globalThis.__pickerStates = ['', {}, {}, {}, '', { modelType: 'flux2_klein_9b', loras: [lora] }]
      globalThis.__pickerStore = { params: { model_type: 'flux2_klein_9b', image_mode: 1, activated_loras: [filename], loras_multipliers: '0.75', lora_parameters: wire,
        ...(field && field !== 'studioPromptEnhance' ? { [field]: true } : {}) },
        models: [{ model_type: 'flux2_klein_9b', family: 'flux2' }], generationMode: 'image', studioPromptEnhance: field === 'studioPromptEnhance',
        availableLoras: [filename], loraWeights: { [filename]: [0.75] }, modelOptions: { guidance_max_phases: 1 }, loraPickerSort: 'name' }
      const rendered = elements(LoraSelector())
      assert.equal(rendered.some(node => node.type === 'legend' && node.props.children === 'LoRA settings'), field === null, String(field))
      assert.equal(rendered.some(node => node.type === 'label' && [node.props.children].flat().some(child => typeof child === 'string' && child.startsWith('Toggle'))), field === null, String(field))
      assert.equal(globalThis.__pickerStore.params.lora_parameters, wire)
    }
  } finally { delete globalThis.__pickerStates; delete globalThis.__pickerStore }
})
