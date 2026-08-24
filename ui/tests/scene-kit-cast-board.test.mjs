import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

import {
  groupSceneKitChoices,
  sceneKitChoiceKey,
  sceneKitOutputCount,
  toggleSceneKitChoice,
} from '../src/lib/sceneKit.ts'

const UI_ROOT = fileURLToPath(new URL('..', import.meta.url))
const asModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`

let storePromise
function loadStore() {
  if (storePromise) return storePromise
  storePromise = build({
    stdin: { contents: "export { useStore } from './src/stores/useStore.ts'", resolveDir: UI_ROOT, loader: 'js' },
    bundle: true,
    format: 'esm',
    platform: 'node',
    write: false,
    logLevel: 'silent',
  }).then(result => import(asModule(result.outputFiles[0].text)))
  return storePromise
}

const character = {
  key: sceneKitChoiceKey('asset-a', 'variant-a'),
  assetId: 'asset-a',
  variantId: 'variant-a',
  assetName: 'Mara',
  variantLabel: 'Production pack',
  kind: 'character',
  outputCount: 3,
  outputIds: ['mara-1', 'mara-2', 'mara-3'],
}
const location = {
  key: sceneKitChoiceKey('asset-b', 'variant-b'),
  assetId: 'asset-b',
  variantId: 'variant-b',
  assetName: 'Rooftop',
  variantLabel: 'Night pack',
  kind: 'location',
  outputCount: 2,
  outputIds: ['roof-1', 'roof-2'],
}

test('Scene Kit choices keep click order, group by role, and toggle by exact identity', () => {
  let choices = toggleSceneKitChoice([], character)
  choices = toggleSceneKitChoice(choices, location)
  assert.deepEqual(choices.map(choice => choice.key), [character.key, location.key])
  assert.equal(sceneKitOutputCount(choices), 5)
  assert.deepEqual(groupSceneKitChoices(choices), {
    characters: [character],
    locations: [location],
  })
  choices = toggleSceneKitChoice(choices, character)
  assert.deepEqual(choices, [location])
})

test('Director applies a staged reference kit in one aligned state change', async () => {
  const { useStore } = await loadStore()
  const existing = new File(['existing'], 'existing.png', { type: 'image/png' })
  const mara1 = new File(['mara-1'], 'mara-1.png', { type: 'image/png' })
  const mara2 = new File(['mara-2'], 'mara-2.png', { type: 'image/png' })
  const rooftop = new File(['roof'], 'roof.png', { type: 'image/png' })
  useStore.setState({
    directorCharacterRefs: [existing],
    directorCharacterRefPaths: ['/uploaded/existing.png'],
    directorCharacterRefLabels: ['Existing'],
    directorLocationRefs: [],
    directorLocationRefPaths: [],
    directorLocationRefLabels: [],
    directorShotDeck: { workspace: 'demo', signature: 'old', deck: {} },
    selectedModelPerMode: { ...useStore.getState().selectedModelPerMode, video: 'h3-video' },
    models: [{ model_type: 'h3-video', director: { max_image_refs: 8 } }],
  })
  let transitions = 0
  const unsubscribe = useStore.subscribe(() => { transitions += 1 })
  const applied = useStore.getState().directorApplyReferenceKit([
    { kind: 'character', file: mara1, label: 'Mara · Sheet 1' },
    { kind: 'character', file: mara2, label: 'Mara · Sheet 2' },
    { kind: 'location', file: rooftop, label: 'Rooftop' },
  ], 1, 'h3-video')
  unsubscribe()
  const state = useStore.getState()
  assert.equal(applied, true)
  assert.equal(transitions, 1)
  assert.deepEqual(state.directorCharacterRefs.map(file => file.name), ['existing.png', 'mara-1.png', 'mara-2.png'])
  assert.deepEqual(state.directorCharacterRefLabels, ['Existing', 'Mara · Sheet 1', 'Mara · Sheet 2'])
  assert.deepEqual(state.directorCharacterRefPaths, [])
  assert.deepEqual(state.directorLocationRefs.map(file => file.name), ['roof.png'])
  assert.deepEqual(state.directorLocationRefLabels, ['Rooftop'])
  assert.equal(state.directorShotDeck, null)
})

test('Director rejects stale capacity and path-only kits before changing any rows', async () => {
  const { useStore } = await loadStore()
  const file = new File(['new'], 'new.png', { type: 'image/png' })
  useStore.setState({
    directorReferenceImage: null,
    directorReferenceImagePath: null,
    directorCharacterRefs: [],
    directorCharacterRefPaths: ['/recovered/path-only.png'],
    directorCharacterRefLabels: ['Recovered'],
    directorLocationRefs: [],
    directorLocationRefPaths: [],
    directorLocationRefLabels: [],
    selectedModelPerMode: { ...useStore.getState().selectedModelPerMode, video: 'h3-video' },
    models: [{ model_type: 'h3-video', director: { max_image_refs: 1 } }],
  })
  assert.equal(useStore.getState().directorApplyReferenceKit([
    { kind: 'character', file, label: 'New' },
  ], 1, 'h3-video'), false)
  useStore.getState().directorAddCharacterRef(file)
  assert.deepEqual(useStore.getState().directorCharacterRefPaths, ['/recovered/path-only.png'])
  assert.deepEqual(useStore.getState().directorCharacterRefs, [])
  assert.match(useStore.getState().directorError, /saved path only/)

  useStore.setState({
    directorCharacterRefs: [file],
    directorCharacterRefPaths: [],
    directorCharacterRefLabels: ['New'],
  })
  assert.equal(useStore.getState().directorApplyReferenceKit([
    { kind: 'location', file, label: 'Roof' },
  ], 0, 'h3-video'), false)
  assert.deepEqual(useStore.getState().directorLocationRefs, [])
})

test('Cast Board stages every output before one commit and keeps legacy single apply', async () => {
  const source = await readFile(new URL('../src/components/Sidebar/ProjectReferenceLibrary.tsx', import.meta.url), 'utf8')
  assert.match(source, /Scene Kit · Cast Board/)
  assert.match(source, /Add to Scene Kit/)
  assert.match(source, /Attach kit to Director/)
  assert.match(source, /const entries: Array<\{ kind: 'character' \| 'location'; file: File; label: string \}> = \[\]/)
  assert.ok(source.indexOf('entries.push({') < source.indexOf('applyReferenceKit(entries,'))
  assert.equal((source.match(/applyReferenceKit\(/g) || []).length, 2)
  assert.match(source, /Director references changed while this reference was loading/)
  assert.match(source, /Nothing was added\./)
  assert.match(source, /void applyReference\(asset, variant\)/)
  assert.match(source, /directorReferenceLimit != null/)
  assert.match(source, /output\.id !== choice\.outputIds\[index\]/)
  assert.match(source, /sceneKitAccountFingerprint/)
  assert.match(source, /currentAccountIdentityEpoch\(\) === accountEpoch/)
  assert.match(source, /selectionStillCurrent\(\)/)
  assert.match(source, /aria-label=\{`\$\{sceneKitSelected \? 'Remove' : 'Add'\} \$\{asset\.name\}/)
  assert.match(source, /setSceneKitChoices\(\[\]\)/)
})

test('project changes clear Director reference files, paths, labels, and the transient board', async () => {
  const store = await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
  const library = await readFile(new URL('../src/components/Sidebar/ProjectReferenceLibrary.tsx', import.meta.url), 'utf8')
  assert.match(store, /activeWorkspace: name,[\s\S]*directorCharacterRefs: \[\],[\s\S]*directorLocationRefLabels: \[\]/)
  assert.match(store, /projectChanged \|\| previousAccessRevoked[\s\S]*directorReferenceImage: null/)
  assert.match(library, /previousProject\.current = project[\s\S]*setSceneKitChoices\(\[\]\)/)
})
