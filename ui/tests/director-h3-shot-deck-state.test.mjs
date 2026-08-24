import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

const UI_ROOT = fileURLToPath(new URL('..', import.meta.url))

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

let storePromise
function loadStore() {
  if (storePromise) return storePromise
  storePromise = build({
    stdin: {
      contents: "export { useStore } from './src/stores/useStore.ts'",
      resolveDir: UI_ROOT,
      loader: 'js',
    },
    bundle: true,
    format: 'esm',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return storePromise
}

const scopedDeck = {
  workspace: 'project-a',
  deck: {
    type: 'minimax_h3_shot_table',
    version: 1,
    surface: 'api_persisted_plan',
    authority: 'advisory',
    provenance: { source: 'MiniMax', revision: 'rev', adaptation: 'maestro_native' },
    fallback_policy: {
      latest_approved_asset_fallback: 'explicit_only',
      reuse_exact_reference_anchors_first: true,
      preserve_authored_dialogue_and_audio: true,
      retake_scope: 'shot',
    },
    shots: [],
    qc_checklist: [],
  },
}

test('Director creative edits clear a previously adopted Shot Deck', async () => {
  const { useStore } = await loadStore()
  useStore.setState({
    activeWorkspace: 'project-a',
    directorShotDeck: scopedDeck,
    directorClipPlans: [{ video_prompt: 'before', image_prompt: 'image' }],
  })
  useStore.getState().directorEditClipPlan(0, 'video_prompt', 'after')
  assert.equal(useStore.getState().directorShotDeck, null)

  useStore.setState({ directorShotDeck: scopedDeck })
  useStore.getState().directorSetSceneDescription('changed scene')
  assert.equal(useStore.getState().directorShotDeck, null)

  useStore.setState({ directorShotDeck: scopedDeck })
  useStore.getState().setDirectorSkill('short_film')
  assert.equal(useStore.getState().directorShotDeck, null)

  useStore.setState({ directorShotDeck: scopedDeck })
  useStore.getState().setExplicitOutput(true)
  assert.equal(useStore.getState().directorShotDeck, null)

  useStore.setState({
    directorShotDeck: scopedDeck,
    directorSpeakerMappings: [{ speakerId: 'S1', name: 'Mara', role: 'lead' }],
  })
  useStore.getState().directorSetSpeakerMapping('S1', 'Mara', 'supporting')
  assert.equal(useStore.getState().directorShotDeck, null)

  useStore.setState({ directorShotDeck: scopedDeck })
  useStore.getState().setDirectorImageRoleLoras('creator', [])
  assert.equal(useStore.getState().directorShotDeck, null)

})

test('all Director v2 result owners adopt exact server decks after their freshness fences', async () => {
  const source = await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
  assert.match(source, /directorShotDeck: ScopedH3ShotDeck \| null/)
  assert.match(source, /directorShotDeck: recoveredShotDeck/)
  assert.equal((source.match(/shotDeck = h3ShotDeckFromProductionPlan\(result\.production_plan, activeWorkspace\)/g) || []).length, 3)
  assert.match(source, /pipelineShotDeck = h3ShotDeckFromProductionPlan/)
  assert.match(source, /let shotDeckAdopted = false/)
  assert.match(source, /const adoptPipelineShotDeck = !shotDeckAdopted && pipelineShotDeck !== null/)
  assert.match(source, /if \(adoptPipelineShotDeck\) shotDeckAdopted = true/)
  assert.match(source, /selectDirectorVideoModel: async \(modelType\) => \{\s*set\(\{ directorComponentError: null, directorError: null, directorShotDeck: null \}\)/)

  const pipelineRequest = source.slice(
    source.indexOf('const pipelineParams:'),
    source.indexOf('if (!lifecycle.ownsWorkspace()) return', source.indexOf('const pipelineParams:')),
  )
  assert.doesNotMatch(pipelineRequest, /directorShotDeck|workflow_template/)
})

test('project, account, planning, and reset paths clear stale decks', async () => {
  const source = await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
  assert.match(source, /function _scrubAccountBoundProjectUi[\s\S]*?directorShotDeck: null/)
  assert.match(source, /directorReset: \(\) => \{[\s\S]*?directorShotDeck: null/)
  assert.match(source, /resumePipeline: async \(pid: string\) => \{[\s\S]*?pipelineId: pid,[\s\S]*?directorShotDeck: null/)
  assert.ok((source.match(/directorStep: 'plan', directorShotDeck: null/g) || []).length >= 3)
  assert.match(source, /activeWorkspace: name,[\s\S]*?directorShotDeck: null/)
})
