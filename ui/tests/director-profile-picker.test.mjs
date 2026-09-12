import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { transform } from 'esbuild'

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
  export function render(value) { state = value; return DirectorProfileLoraPicker({mode:'video', modelType:'test-model'}) }
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
  const tree = render({ presets: [preset], presetsLoading: false, presetsError: 'Profiles could not be refreshed.', savedLoraPerMode: { video: { availableLoras: ['look.safetensors'] } }, loadPresets() {}, directorSetLora: (...args) => calls.push(args) })
  const button = walk(tree).find(item => item.props['aria-label'] === 'Use LoRAs from Saved look')
  button.props.onClick()
  assert.deepEqual(calls, [['video', preset.activated_loras, '0.5', preset.lora_weights, ['look.safetensors']]])
  assert.match(profiles, /<GenerationProfileRefreshStatus/)
})
