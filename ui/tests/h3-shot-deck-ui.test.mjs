import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

import { h3ShotDeckFromProductionPlan, parseH3ShotDeck } from '../src/lib/h3ShotDeck.ts'

const UI_ROOT = fileURLToPath(new URL('..', import.meta.url))

function deck() {
  return {
    type: 'minimax_h3_shot_table',
    version: 1,
    surface: 'api_persisted_plan',
    authority: 'advisory',
    provenance: { source: 'MiniMax-AI/MiniMax-H3 skills', revision: 'abc123', adaptation: 'maestro_native' },
    fallback_policy: {
      latest_approved_asset_fallback: 'explicit_only',
      reuse_exact_reference_anchors_first: true,
      preserve_authored_dialogue_and_audio: true,
      retake_scope: 'shot',
    },
    shots: [
      {
        shot_id: 'shot-1', index: 0, start_sec: 0, end_sec: 4, duration_sec: 4,
        scene: 'Neon hallway reveal', subjects: [{ name: 'Mara' }], spatial: 'Center frame',
        environment: 'Rainy corridor', lighting: 'Blue edge light', action: ['Mara opens the door'],
        camera: { movement: 'Dolly in', framing: 'Medium wide' },
        audio: { ambience: 'Rain', music: 'Low pulse' }, handoff_in: null,
        handoff_out: 'Door motion carries forward', timed_cues: [{ at_sec: 3.5, cue: 'Door opens' }],
      },
      {
        shot_id: 'shot-2', index: 1, start_sec: 4, end_sec: 8, duration_sec: 4,
        scene: 'Room reaction', subjects: [], spatial: 'Reverse angle', environment: 'Dark room',
        lighting: 'Warm practical', action: ['A figure turns'], camera: { movement: 'Static' },
        audio: { dialogue: 'Ready.' }, handoff_in: 'Door motion carries forward',
        handoff_out: 'Hold on reaction', timed_cues: [],
      },
    ],
    qc_checklist: [
      { check: 'identity_and_reference_continuity', status: 'pending' },
      { check: 'action_camera_and_handoff', status: 'pending' },
    ],
  }
}

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

let componentPromise
function loadComponent() {
  if (componentPromise) return componentPromise
  componentPromise = build({
    stdin: {
      contents: "export { DirectorShotDeck } from './src/components/DirectorShotDeck.tsx'",
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
      name: 'shot-deck-test-react',
      setup(bundle) {
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'shot-deck-test' }))
        bundle.onLoad({ filter: /.*/, namespace: 'shot-deck-test' }, () => ({
          contents: `
            export const Fragment = Symbol.for('shot-deck-fragment')
            export const jsx = (type, props, key) => ({ type, key, props: props || {} })
            export const jsxs = jsx
          `,
        }))
      },
    }],
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return componentPromise
}

function flatten(value, result = []) {
  if (Array.isArray(value)) {
    value.forEach(item => flatten(item, result))
    return result
  }
  if (!value || typeof value !== 'object') return result
  if (typeof value.type === 'function') return flatten(value.type(value.props || {}), result)
  if ('type' in value && 'props' in value) result.push(value)
  flatten(value.props?.children, result)
  return result
}

function text(value) {
  if (Array.isArray(value)) return value.map(text).join('')
  if (value == null || typeof value === 'boolean') return ''
  if (typeof value !== 'object') return String(value)
  if (typeof value.type === 'function') return text(value.type(value.props || {}))
  return text(value.props?.children)
}

test('strict v1 parser accepts only the server-owned advisory surface', () => {
  const source = deck()
  const parsed = parseH3ShotDeck(source)
  assert.ok(parsed)
  assert.notEqual(parsed, source)
  assert.equal(parsed.shots.length, 2)
  assert.equal(h3ShotDeckFromProductionPlan({ workflow_template: source }, 'project-a')?.workspace, 'project-a')

  assert.equal(parseH3ShotDeck({ ...source, version: 2 }), null)
  assert.equal(parseH3ShotDeck({ ...source, authority: 'execution' }), null)
  assert.equal(parseH3ShotDeck({ ...source, shots: [{ ...source.shots[0], end_sec: 3 }] }), null)
  assert.equal(parseH3ShotDeck({ ...source, shots: [{ ...source.shots[0], private_path: '/tmp/private.mov' }] }), null)
  assert.equal(h3ShotDeckFromProductionPlan({}, 'project-a'), null)
})

test('Shot Deck renders a compact advisory beat ribbon, readable fields, and static QC', async () => {
  const { DirectorShotDeck } = await loadComponent()
  const tree = DirectorShotDeck({ deck: parseH3ShotDeck(deck()) })
  const elements = flatten(tree)
  const rendered = text(tree)

  assert.match(rendered, /Director Shot Deck/)
  assert.match(rendered, /Planning aid · Advisory · 2 shots · 8s/)
  assert.match(rendered, /Neon hallway reveal/)
  assert.match(rendered, /Dolly in/)
  assert.match(rendered, /Rain/)
  assert.match(rendered, /Subjects: Name: Mara/)
  assert.match(rendered, /Spatial: Center frame/)
  assert.match(rendered, /Environment: Rainy corridor/)
  assert.match(rendered, /Lighting: Blue edge light/)
  assert.match(rendered, /In: Door motion carries forward/)
  assert.match(rendered, /Door motion carries forward/)
  assert.match(rendered, /2 pending/)
  assert.equal(elements.filter(element => element.type === 'button' || element.type === 'input').length, 0)
  assert.ok(elements.some(element => element.type === 'ol' && element.props['aria-label'] === 'Shot timeline'))
  assert.ok(elements.some(element => element.type === 'summary' && String(element.props.className).includes('mobile-control-target')))
  assert.doesNotMatch(rendered, /private_path|\/tmp\/|workflow_template/)
})

test('Shot Deck layout wraps in the Director sidebar without a nested horizontal scroller', async () => {
  const source = await import('node:fs/promises').then(fs => fs.readFile(new URL('../src/components/DirectorShotDeck.tsx', import.meta.url), 'utf8'))
  assert.match(source, /repeat\(auto-fit,minmax\(78px,1fr\)\)/)
  assert.match(source, /break-words/)
  assert.doesNotMatch(source, /overflow-x-(?:auto|scroll)/)
  assert.match(source, /Planning aid · Advisory/)
})
