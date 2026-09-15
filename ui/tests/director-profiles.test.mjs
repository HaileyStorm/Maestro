import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const bundle = await build({
  entryPoints: [new URL('../src/lib/directorProfiles.ts', import.meta.url).pathname],
  bundle: true, platform: 'node', format: 'esm', write: false,
})
const profiles = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`)

function state() {
  return {
    directorResolution: '1080p', directorAspectRatio: '9:16',
    directorSeamless: false, directorShotImageGuidance: 'prompt_only',
    directorVideoInferenceStepsByModel: { video_a: 20, video_b: 12 },
    directorVideoMaxShotFramesByModel: { video_a: 121, video_b: 81 },
    directorVideoFilmGrainIntensity: 0, directorVideoFilmGrainSaturation: 0,
    directorVideoSelfRefiner: 0, directorAudioScale: 0,
    directorIdentityGuidanceScale: 0,
    h3StyleWorkflow: '',
    directorImageCreatorModelOverride: '', directorImageEditorModelOverride: 'editor_a',
    directorImageRoleLoras: {
      creator: [{ id: 'look.safetensors', multiplier: 0,
        parameter_schema_digest: 'a'.repeat(64), parameter_values: { strength: 0, enabled: false, variant: '' } }],
      editor: [],
    },
    directorSceneDescription: 'Job-local text', directorReferenceImagePath: '/job/ref.png',
    explicitOutput: false, activeWorkspace: 'private-workspace',
  }
}

test('Director capture preserves complete technical choices and detaches role parameters', () => {
  const source = state()
  const saved = profiles.captureDirectorProfileSettings(source, 'video_a', { creator: 'creator_a', editor: 'editor_a' })
  assert.deepEqual(Object.keys(saved).sort(), [
    'resolution', 'aspect_ratio', 'seamless', 'shot_image_guidance', 'video_inference_steps',
    'video_max_shot_frames', 'video_film_grain_intensity', 'video_film_grain_saturation',
    'video_self_refiner', 'audio_scale', 'identity_guidance_scale', 'h3_style_workflow', 'image_roles',
  ].sort())
  assert.equal(saved.video_inference_steps, 20)
  assert.equal(saved.video_max_shot_frames, 121)
  assert.equal(saved.audio_scale, 0)
  assert.equal(saved.seamless, false)
  assert.equal(saved.image_roles.creator.model_override, '')
  assert.equal(saved.image_roles.creator.lora_model_type, 'creator_a')
  assert.equal(saved.image_roles.editor.lora_model_type, null)
  const captured = structuredClone(saved)
  source.directorImageRoleLoras.creator[0].parameter_values.strength = 1
  assert.deepEqual(saved, captured)
  assert.ok(!JSON.stringify(saved).includes('Job-local'))
  assert.ok(!JSON.stringify(saved).includes('/job/'))
  assert.ok(!JSON.stringify(saved).includes('private-workspace'))
})

test('Director restore replaces only selected-model overrides and keeps automatic roles', () => {
  const source = state()
  delete source.directorVideoInferenceStepsByModel.video_a
  delete source.directorVideoMaxShotFramesByModel.video_a
  const saved = profiles.captureDirectorProfileSettings(source, 'video_a', { creator: 'creator_a', editor: 'editor_a' })
  const current = state()
  const before = structuredClone(current)
  const restored = profiles.restoreDirectorProfileSettings(saved, 'video_a', current)
  assert.deepEqual(current, before)
  assert.deepEqual(restored.directorVideoInferenceStepsByModel, { video_b: 12 })
  assert.deepEqual(restored.directorVideoMaxShotFramesByModel, { video_b: 81 })
  assert.equal(restored.directorImageCreatorModelOverride, '')
  assert.equal(restored.directorIdentityGuidanceScale, 0)
  assert.equal('directorSceneDescription' in restored, false)
  assert.equal('directorReferenceImagePath' in restored, false)
  assert.equal('activeWorkspace' in restored, false)
  assert.equal('explicitOutput' in restored, false)
  assert.equal('params' in restored, false)
  const roundtrip = profiles.captureDirectorProfileSettings(restored, 'video_a', { creator: 'creator_a', editor: 'editor_a' })
  assert.deepEqual(roundtrip, saved)
})

test('automatic role changes cannot silently reuse a model-bound LoRA', () => {
  const saved = profiles.captureDirectorProfileSettings(state(), 'video_a', { creator: 'creator_a', editor: 'editor_a' })
  assert.equal(profiles.directorProfileRoleMismatch(saved, { creator: 'creator_a', editor: 'new_editor' }), null)
  assert.equal(profiles.directorProfileRoleMismatch(saved, { creator: 'creator_b', editor: 'editor_a' }), 'creator')
})

test('incomplete or non-JSON technical choices cannot appear successfully captured', () => {
  const source = state()
  source.directorAudioScale = NaN
  assert.throws(() => profiles.captureDirectorProfileSettings(source, 'video_a', { creator: 'creator_a', editor: 'editor_a' }))
  const missing = state()
  delete missing.directorImageRoleLoras.creator[0].parameter_values
  assert.throws(() => profiles.captureDirectorProfileSettings(missing, 'video_a', { creator: 'creator_a', editor: 'editor_a' }))
  assert.throws(() => profiles.captureDirectorProfileSettings(state(), 'video_a', { creator: '', editor: 'editor_a' }))
})

test('Director profiles are explicitly distinguishable from ordinary and unknown profiles', () => {
  const director = { profile_version: 3, profile_context: 'director', mode: 'video', director_settings: {} }
  assert.equal(profiles.isDirectorProfile(director), true)
  for (const bad of [
    { ...director, profile_version: 2 }, { ...director, profile_context: undefined },
    { ...director, mode: 'audio' }, { ...director, director_settings: undefined },
  ]) assert.equal(profiles.isDirectorProfile(bad), false)
})

test('Director video capture binds overrides to their model and excludes job/envelope fields', () => {
  const defaults = { guidance_scale: 5, seed: -1, model_type: 'video_b', prompt: 'default text' }
  const snapshot = {
    model_type: 'video_a', guidance_scale: 0, seed: 0,
    prompt: 'other job', image_refs: ['/job/ref.png'], uiSettings: { durationSeconds: 4 },
    durationSeconds: 4, spatialUpsampling: 'other-upscaler',
    custom_settings: { h3_attention_engine: 'sdpa' },
  }
  assert.deepEqual(profiles.captureDirectorVideoParameters(defaults, snapshot, 'video_b'), { guidance_scale: 5, seed: -1 })
  assert.deepEqual(profiles.captureDirectorVideoParameters(defaults, snapshot, 'video_a'), {
    guidance_scale: 0, seed: 0, custom_settings: { h3_attention_engine: 'sdpa' },
  })
  assert.throws(() => profiles.captureDirectorVideoParameters(defaults, {
    ...snapshot, unknown_technical_setting: 1,
  }, 'video_a'))
})
