import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { build } from 'esbuild'
import ts from 'typescript'

const schema = JSON.parse(await readFile(new URL('../../app/services/generation_profile_fields.json', import.meta.url), 'utf8'))
const bundle = await build({
  entryPoints: [new URL('../src/lib/generationProfiles.ts', import.meta.url).pathname],
  bundle: true, platform: 'node', format: 'esm', write: false,
})
const profiles = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`)

test('every declared generation parameter has an explicit profile classification', async () => {
  const source = ts.createSourceFile('types.ts', await readFile(new URL('../src/types/index.ts', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true)
  const interfaces = source.statements.filter(ts.isInterfaceDeclaration)
  const classified = new Set([...Object.keys(schema.params), ...Object.keys(schema.excluded_params)])
  for (const name of ['GenerateParams', 'H3AdaptiveSelection']) {
    const declaration = interfaces.find(node => node.name.text === name)
    assert.ok(declaration, name)
    for (const member of declaration.members) {
      if (!ts.isPropertySignature(member)) continue
      assert.ok(classified.has(member.name.getText(source)), `${name}.${member.name.getText(source)} must be classified`)
    }
  }
})

test('full snapshots preserve zeros, false, empty selections, timing and detached custom settings', () => {
  const params = {
    model_type: 'minimax_h3', prompt: 'current authored text', negative_prompt: 'current negative',
    image_refs: ['/job-only/ref.png'], num_inference_steps: 28, guidance_scale: 0, seed: 0,
    resolution: '1344x768', video_length: 501, h3_adaptive_conditioning: false,
    h3_fl2va_loras: [], h3_fl2va_loras_multipliers: '', h3_ref2va_loras: ['ref.safetensors'],
    custom_settings: { h3_attention_engine: 'sdpa', h3_sol_tau: 1.25 },
  }
  const ui = {
    durationSeconds: 20.875, slidingWindowSeconds: 10.875, slidingWindowOverlap: 17,
    slidingWindowLocked: true, spatialUpsampling: '', filmGrainIntensity: 0,
    filmGrainSaturation: 0.7, voiceCloneEnabled: false, activeWorkspace: 'job-only',
  }
  const saved = profiles.captureGenerationProfileSettings(params, ui)
  const expected = structuredClone(saved)
  params.custom_settings.h3_sol_tau = 2
  params.h3_ref2va_loras.push('later.safetensors')
  assert.deepEqual(saved, expected)
  assert.equal(saved.profile_version, 2)
  for (const key of ['prompt', 'negative_prompt', 'image_refs', 'model_type']) assert.equal(key in saved.params, false)
  assert.equal('activeWorkspace' in saved.ui_settings, false)
  const currentParams = { prompt: 'another job', image_refs: ['/another/ref.png'], cfg_rescale: 0.8, custom_settings: { h3_sol_tau: 2 } }
  const restored = profiles.restoreGenerationProfileSettings(saved, currentParams, { voiceCloneMode: 'single' })
  assert.equal(restored.params.prompt, 'another job')
  assert.deepEqual(restored.params.image_refs, ['/another/ref.png'])
  assert.equal('cfg_rescale' in restored.params, false)
  assert.deepEqual(restored.params.h3_fl2va_loras, [])
  assert.equal(restored.params.guidance_scale, 0)
  assert.equal(restored.params.h3_adaptive_conditioning, false)
  assert.equal(restored.uiSettings.slidingWindowOverlap, 17)
  assert.equal(restored.uiSettings.filmGrainIntensity, 0)
  assert.equal(restored.uiSettings.voiceCloneEnabled, false)
  assert.equal(restored.uiSettings.voiceCloneMode, 'single')
})

test('unclassified settings prevent incomplete saving and non-finite values are not coerced', () => {
  assert.throws(() => profiles.captureGenerationProfileSettings({ new_technical_knob: 3 }, {}), /not supported/)
  assert.throws(() => profiles.captureGenerationProfileSettings({ custom_settings: { new_custom_knob: 3 } }, {}), /not supported/)
  assert.throws(() => profiles.captureGenerationProfileSettings({ guidance_scale: NaN }, {}), /cannot be saved/)
})

test('actual submission projection honors an explicit empty upscaler over a stale parameter', async () => {
  const source = ts.createSourceFile('store.ts', await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true)
  let start
  function find(node) {
    if (ts.isPropertyAssignment(node) && node.name.getText(source) === 'startGeneration') start = node.initializer
    ts.forEachChild(node, find)
  }
  find(source)
  const statement = start.body.statements.find(node => node.getText(source).includes('params.spatial_upsampling'))
  assert.ok(statement)
  const code = ts.transpileModule(statement.getText(source), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  const project = new Function('params', 'state', code)
  const params = { spatial_upsampling: 'stale-upscaler' }
  project(params, { spatialUpsampling: '' })
  assert.equal(params.spatial_upsampling, '')
  project(params, { spatialUpsampling: 'selected-upscaler' })
  assert.equal(params.spatial_upsampling, 'selected-upscaler')
})

test('unknown profile versions cannot fall back to partial legacy restore', () => {
  assert.throws(() => profiles.restoreGenerationProfileSettings({ profile_version: 3, params: {} }, { seed: 42 }, {}), /newer version/)
})
