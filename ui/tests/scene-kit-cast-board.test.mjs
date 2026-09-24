import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

import {
  groupSceneKitChoices,
  projectAssetGenerateOutputsAreImages,
  projectAssetGenerateReferenceFromChoice,
  reconcileProjectAssetGenerateReferences,
  sameOrderedProjectAssetGenerateReferences,
  sameProjectAssetGenerateReference,
  sceneKitChoiceKey,
  sceneKitOutputCount,
  toggleSceneKitChoice,
} from '../src/lib/sceneKit.ts'
import { submitGeneration } from '../src/api/client.ts'

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

test('Generate project references preserve click order and exact output order', () => {
  assert.equal(projectAssetGenerateOutputsAreImages(['image/png', 'image/webp']), true)
  assert.equal(projectAssetGenerateOutputsAreImages([]), false)
  assert.equal(projectAssetGenerateOutputsAreImages(['image/png', 'video/mp4']), false)
  const first = projectAssetGenerateReferenceFromChoice(character)
  const second = projectAssetGenerateReferenceFromChoice(location)
  assert.deepEqual(first, {
    asset_id: 'asset-a',
    variant_id: 'variant-a',
    output_ids: ['mara-1', 'mara-2', 'mara-3'],
  })
  assert.deepEqual([first, second].map(reference => reference.asset_id), ['asset-a', 'asset-b'])
  assert.equal(sameProjectAssetGenerateReference(first, {
    ...first,
    output_ids: ['mara-2', 'mara-1', 'mara-3'],
  }), false)
  assert.equal(sameOrderedProjectAssetGenerateReferences([first, second], [first, second]), true)
  assert.equal(sameOrderedProjectAssetGenerateReferences([first, second], [second, first]), false)
  assert.equal(sameOrderedProjectAssetGenerateReferences([first], [{
    ...first,
    output_ids: ['mara-1', 'mara-3', 'mara-2'],
  }]), false)
  assert.deepEqual(
    reconcileProjectAssetGenerateReferences([first, second], [
      { ...first, output_ids: ['changed-output'] }, second,
    ]),
    [second],
  )
})

test('Generate staging is ordered, project-scoped, and invalidated when outputs change', async () => {
  const { useStore } = await loadStore()
  const project = 'scene-kit-generate-project'
  const first = projectAssetGenerateReferenceFromChoice(character)
  const second = projectAssetGenerateReferenceFromChoice(location)
  useStore.setState({
    activeWorkspace: project,
    projectAssetRefs: [],
    projectAssetRefScope: null,
  })
  useStore.getState().toggleProjectAssetRef(first)
  useStore.getState().toggleProjectAssetRef(second)
  assert.deepEqual(useStore.getState().projectAssetRefs, [first, second])
  assert.equal(useStore.getState().projectAssetRefScope.workspace, project)
  assert.equal(typeof useStore.getState().projectAssetRefScope.accountIdentityEpoch, 'number')
  useStore.getState().reconcileProjectAssetRefs(project, [
    { ...first, output_ids: ['new-output'] }, second,
  ])
  assert.deepEqual(useStore.getState().projectAssetRefs, [second])
  useStore.getState().reconcileProjectAssetRefs(project, [
    { ...second, output_ids: ['new-location-output'] },
  ])
  assert.deepEqual(useStore.getState().projectAssetRefs, [])
  assert.equal(useStore.getState().projectAssetRefScope, null)
})

test('Generate sends project reference descriptors separately from uploaded image refs', async () => {
  const originalFetch = globalThis.fetch
  const reference = projectAssetGenerateReferenceFromChoice(character)
  let requestBody
  globalThis.fetch = async (input, init = {}) => {
    assert.match(String(input), /\/api\/v1\/generate$/)
    requestBody = JSON.parse(init.body)
    return Response.json({ job_id: 'scene-kit-generate-job' })
  }
  try {
    await submitGeneration({
      model_type: 'video-model',
      image_refs: ['/uploaded/studio-reference.png'],
      project_asset_refs: [reference],
    })
  } finally {
    globalThis.fetch = originalFetch
  }
  assert.deepEqual(requestBody.project_asset_refs, [reference])
  assert.deepEqual(requestBody.image_refs, ['/uploaded/studio-reference.png'])
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
  assert.match(source, /Add to Generate/)
  assert.match(source, /projectAssetGenerateOutputsAreImages/)
  assert.match(source, /Generate staging supports image outputs only/)
  assert.match(source, /Generate references/)
  assert.match(source, /projectAssetGenerateReferenceFromChoice/)
  assert.match(source, /reconcileProjectAssetRefs\(project, availableProjectAssetRefs\)/)
  assert.match(source, /assetsSnapshotProject !== project/)
  assert.match(source, /Remove \$\{label\} from Generate/)
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
  const inputs = await readFile(new URL('../src/components/Sidebar/InputsPanel.tsx', import.meta.url), 'utf8')
  assert.match(store, /activeWorkspace: name,[\s\S]*directorCharacterRefs: \[\],[\s\S]*directorLocationRefLabels: \[\]/)
  assert.match(store, /projectChanged \|\| previousAccessRevoked[\s\S]*directorReferenceImage: null/)
  assert.match(store, /projectChanged \|\| previousAccessRevoked[\s\S]*projectAssetRefs: \[\]/)
  assert.match(store, /projectAssetRefsForSubmission\.length > 0[\s\S]*params\.project_asset_refs = projectAssetRefsForSubmission/)
  assert.match(store, /stagedProjectAssetRefScopeMatches[\s\S]*sameOrderedProjectAssetGenerateReferences\([\s\S]*Scene Kit references changed while this generation was preparing/)
  assert.match(library, /previousProject\.current = project[\s\S]*setSceneKitChoices\(\[\]\)/)
  assert.match(inputs, /Scene Kit: \{projectAssetRefs\.length\} selection[\s\S]*Review in References/)
  assert.match(inputs, /clearProjectAssetRefs/)
})
