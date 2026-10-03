import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import test from 'node:test'
import { build } from 'esbuild'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
let loaded
async function modules() {
  if (loaded) return loaded
  const bundle = await build({
    stdin: {
      contents: "export * from './src/lib/h3DirectionFields'; export { MultiClipEditor } from './src/components/Sidebar/MultiClipEditor'",
      resolveDir: root, loader: 'js',
    },
    bundle: true, format: 'esm', platform: 'node', jsx: 'automatic', write: false,
    plugins: [{ name: 'direction-ui-runtime', setup(b) {
      b.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'direction-test' }))
      b.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx', namespace: 'direction-test' }))
      b.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'icons', namespace: 'direction-test' }))
      b.onLoad({ filter: /.*/, namespace: 'direction-test' }, ({ path }) => ({ contents:
        path === 'store' ? 'export const useStore = selector => selector(globalThis.__directionState)' :
        path === 'jsx' ? 'export const Fragment=Symbol.for("direction-fragment"); export const jsx=(type,props,key)=>({type,props:props||{},key}); export const jsxs=jsx' :
        'export const Upload=()=>null; export const X=()=>null',
      }))
    } }],
  })
  loaded = await import('data:text/javascript;base64,' + Buffer.from(bundle.outputFiles[0].text).toString('base64'))
  return loaded
}

function nodes(tree, type) {
  if (tree == null || typeof tree !== 'object') return []
  if (Array.isArray(tree)) return tree.flatMap(item => nodes(item, type))
  return [...(tree.type === type ? [tree] : []), ...nodes(tree.props?.children, type)]
}

function state({ shared = false, model = 'minimax_h3' } = {}) {
  const s = {
    clips: [{ prompt: '[Shot 1] Walk past the camera.\nfacing: screen right', startImage: null }, { prompt: '[Shot 2] Camera cuts closer.', startImage: null }],
    singlePromptMode: shared, slidingWindowSeconds: 5, params: { model_type: model }, modelOptions: null,
    setSinglePromptMode: value => { s.singlePromptMode = value },
    setClipPrompt: (index, value) => { s.clips[index].prompt = value }, setClipStartImage: () => {},
  }
  globalThis.__directionState = s
  return s
}

test('only full-line authored direction fields are read, including the last duplicate', async () => {
  const { readH3DirectionField } = await modules()
  assert.equal(readH3DirectionField('She is facing screen right.', 'facing'), '')
  assert.equal(readH3DirectionField('[Shot 1] screen direction: left', 'screen direction'), '')
  assert.equal(readH3DirectionField('FACING: left\nfacing: right', 'facing'), 'right')
})

test('changing or clearing one field preserves other prompt bytes and CRLF', async () => {
  const { writeH3DirectionField, readH3DirectionField } = await modules()
  const before = '[Shot 1]  Walk.\r\nFACING: left\r\n[Shot 2] Keep the camera.\r\nscreen direction: forward\r\n'
  const after = writeH3DirectionField(before, 'facing', 'right')
  assert.equal(after, before.replace('FACING: left', 'facing: right'))
  assert.equal(writeH3DirectionField(after, 'facing', ''), before.replace('FACING: left\r\n', ''))
  assert.equal(readH3DirectionField(after, 'screen direction'), 'forward')
})

test('editing duplicates resolves the authored field without altering other lines', async () => {
  const { writeH3DirectionField } = await modules()
  assert.equal(writeH3DirectionField('facing: left\n[Shot 1] Dance.\nfacing: right', 'facing', 'toward camera'), '[Shot 1] Dance.\nfacing: toward camera')
  assert.equal(writeH3DirectionField('Adult violent controversial creative scene.', 'screen direction', 'left\nto right'), 'Adult violent controversial creative scene.\nscreen direction: left to right')
})

test('controlled direction input retains an in-progress trailing space', async () => {
  const { writeH3DirectionField, readH3DirectionField } = await modules()
  let prompt = '[Shot 1] Walk.'
  for (const value of ['moves', 'moves ', 'moves left', 'moves left ']) {
    prompt = writeH3DirectionField(prompt, 'screen direction', value)
    assert.equal(readH3DirectionField(prompt, 'screen direction'), value)
  }
})

test('actual component edits the intended clip and mirrors manual prompt edits', async () => {
  const { MultiClipEditor } = await modules()
  const s = state()
  let controls = nodes(MultiClipEditor(), 'input').filter(n => n.props['aria-label'])
  assert.equal(controls.length, 4)
  const nextDirection = controls.find(n => n.props['aria-label'] === 'Clip 2 screen direction')
  nextDirection.props.onChange({ target: { value: 'moves right to left' } })
  assert.equal(s.clips[0].prompt, '[Shot 1] Walk past the camera.\nfacing: screen right')
  assert.equal(s.clips[1].prompt, '[Shot 2] Camera cuts closer.\nscreen direction: moves right to left')
  s.clips[1].prompt += '\nfacing: toward camera'
  controls = nodes(MultiClipEditor(), 'input').filter(n => n.props['aria-label'])
  assert.equal(controls.find(n => n.props['aria-label'] === 'Clip 2 subject facing').props.value, 'toward camera')
})

test('shared prompt uses the first clip and disables later controls; other models hide them', async () => {
  const { MultiClipEditor } = await modules()
  state({ shared: true })
  const inputs = nodes(MultiClipEditor(), 'input').filter(n => n.props['aria-label'])
  assert.equal(inputs[3].props.value, 'screen right')
  assert.equal(inputs[2].props.disabled, true)
  assert.equal(inputs[3].props.disabled, true)
  state({ model: 'ltx2' })
  assert.equal(nodes(MultiClipEditor(), 'details').length, 0)
})

test('model switches ignore stale options and unsupported aliases', async () => {
  const { MultiClipEditor } = await modules()
  const s = state({ model: 'ltx2' })
  s.modelOptions = { model_type: 'minimax_h3', architecture: 'minimax_h3' }
  assert.equal(nodes(MultiClipEditor(), 'details').length, 0)
  s.params.model_type = 'owner_h3_variant'
  assert.equal(nodes(MultiClipEditor(), 'details').length, 0)
  s.modelOptions = { model_type: 'owner_h3_variant', architecture: 'minimax_h3' }
  assert.equal(nodes(MultiClipEditor(), 'details').length, 0)
  s.params.model_type = 'minimax_h3_future_variant'
  assert.equal(nodes(MultiClipEditor(), 'details').length, 0)
  for (const model of ['minimax_h3', 'minimax_h3_pinkcherry_fl2va', 'minimax_h3_w4a8_fl2va', 'minimax_h3_ref2va']) {
    s.params.model_type = model
    assert.equal(nodes(MultiClipEditor(), 'details').length, 2)
  }
})

test('multiline and blank clip entries survive the actual backend manifest parser', async () => {
  const { multiclipPromptPayload } = await modules()
  const prompts = ['[Shot 1] Walk.\nfacing: screen right', '', '[Shot 3] Turn.\nscreen direction: right to left']
  const payload = multiclipPromptPayload(prompts.map(prompt => ({ prompt })), false)
  const python = process.platform === 'win32' ? '../app/env/Scripts/python.exe' : '../app/env/bin/python'
  const result = JSON.parse(execFileSync(python, ['-c',
    'import json,sys;sys.path.insert(0,"../app");from services.multiclip_inputs import multiclip_prompt_inputs;p=json.load(sys.stdin);print(json.dumps(multiclip_prompt_inputs(p,separator="__unused_separator__")[0]))',
  ], { cwd: root, input: JSON.stringify(payload), encoding: 'utf8' }))
  assert.deepEqual(result, prompts)
  assert.deepEqual(multiclipPromptPayload([{ prompt: '' }, { prompt: 'unused' }], true, 'shared\nfacing: forward').per_clip_prompts, ['shared\nfacing: forward', 'shared\nfacing: forward'])
})

test('output restore preserves multiline direction fields and clip/image positions', async () => {
  const { multiclipPromptPayload, restoreMulticlipPrompts } = await modules()
  const prompts = ['[Shot 1] Walk.\nfacing: screen right', '', '  [Shot 3] Turn.\nscreen direction: right to left  ']
  const payload = multiclipPromptPayload(prompts.map(prompt => ({ prompt })), false)
  assert.deepEqual(restoreMulticlipPrompts(payload.prompt), prompts)
  // A retained array takes precedence, including literal boundary-like text.
  assert.deepEqual(restoreMulticlipPrompts('legacy fallback', prompts), prompts)
  assert.deepEqual(restoreMulticlipPrompts(' first\n\n second '), ['first', 'second'])
})
