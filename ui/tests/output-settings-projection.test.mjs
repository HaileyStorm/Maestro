import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

const UI_ROOT = new URL('..', import.meta.url).pathname
const profileSchema = JSON.parse(await readFile(
  new URL('../../app/services/generation_profile_fields.json', import.meta.url),
  'utf8',
))
const canonicalKeys = Object.keys(profileSchema.params)

function asDataModule(contents) {
  return `data:text/javascript;base64,${Buffer.from(contents).toString('base64')}`
}

let bundlePromise
let realmSequence = 0
function storeBundle() {
  bundlePromise ||= build({
    stdin: {
      contents: [
        "export { useStore } from './src/stores/useStore.ts'",
        "export { projectGenerationProfileParameters } from './src/lib/generationProfiles.ts'",
      ].join('\n'),
      resolveDir: UI_ROOT,
      loader: 'js',
    },
    bundle: true,
    format: 'esm',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
  }).then(result => result.outputFiles[0].text)
  return bundlePromise
}

async function loadFreshModule() {
  realmSequence += 1
  return import(`${asDataModule(await storeBundle())}#output-projection-${realmSequence}`)
}

class StorageFake {
  values = new Map()
  getItem(key) { return this.values.get(key) ?? null }
  setItem(key, value) { this.values.set(key, String(value)) }
  removeItem(key) { this.values.delete(key) }
}

function jsonResponse(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function options() {
  return {
    model_type: 'test_model',
    architecture: 'test',
    fps: 1,
    latent_size: 1,
    frames_steps: 1,
    frames_minimum: 1,
    frames_maximum: 1000,
    guidance_max_phases: 1,
    default_num_inference_steps: 20,
    default_guidance_scale: 5,
    sliding_window: true,
    sliding_window_defaults: {
      window_min: 1,
      window_default: 1,
      window_max: 1000,
      overlap_min: 0,
      overlap_default: 0,
      overlap_max: 999,
      discard_last_frames: 0,
    },
    ltx25_video_vae_choices: [{ value: 'fast', label: 'Fast' }, { value: 'nad', label: 'NAD' }],
    ltx25_video_vae_default: 'fast',
    resolutions: ['1280x720'],
  }
}

async function withFreshStore(action, optionOverrides = {}) {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  globalThis.fetch = async input => {
    const url = String(input)
    if (url.endsWith('/api/v1/model-options/test_model')) return jsonResponse({ ...options(), ...optionOverrides })
    if (url.endsWith('/api/v1/loras/test_model')) return jsonResponse({ loras: [], guidance_max_phases: 1 })
    throw new Error(`Unexpected output projection request: ${url}`)
  }
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: '127.0.0.1' },
    matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
  })
  globalThis.document = Object.assign(new EventTarget(), {
    hidden: false,
    createElement: () => ({ muted: false }),
  })
  try {
    return await action(await loadFreshModule())
  } finally {
    globalThis.fetch = originalFetch
    if (originalWindow === undefined) delete globalThis.window
    else globalThis.window = originalWindow
    if (originalDocument === undefined) delete globalThis.document
    else globalThis.document = originalDocument
    if (originalLocalStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = originalLocalStorage
    if (originalSessionStorage === undefined) delete globalThis.sessionStorage
    else globalThis.sessionStorage = originalSessionStorage
  }
}

function canonicalValue(key, descriptor) {
  const explicit = {
    resolution: '1280x720',
    video_length: 81,
    num_inference_steps: 20,
    guidance_scale: 0,
    seed: 0,
    image_mode: 0,
    repeat_generation: 7,
    sliding_window_size: 41,
    sliding_window_overlap: 0,
    sliding_window_discard_last_frames: 0,
    h3_style_workflow: null,
    video_prompt_type: '',
    audio_prompt_type: '',
    image_prompt_type: '',
    flow_shift: null,
    audio_scale: 0.1,
    ltx25_video_vae: 'fast',
    force_fps: '',
    duration_seconds: 0,
    custom_settings: {
      bpm: 128,
      pace: 0.75,
      vc_steps: 12,
      vc_cfg_rate: 0,
      h3_sol_dense_steps: 0,
      h3_spectrum_profile: '',
      auto_split_every_s: null,
    },
    delivery_resolution: '',
    delivery_fit: '',
    ge_alpha: 0,
    sample_solver: '',
    embedded_guidance_scale: 0,
    top_p: 0,
    top_k: null,
    alt_guidance_scale: 0,
  }
  if (Object.hasOwn(explicit, key)) return explicit[key]
  if (descriptor.enum) return descriptor.enum[0]
  if (descriptor.type === 'boolean') return false
  if (descriptor.type === 'number' || descriptor.type === 'integer') return descriptor.minimum ?? 0
  if (descriptor.type === 'string') return ''
  if (descriptor.type === 'string_array' || descriptor.type === 'number_array') return []
  if (descriptor.type === 'object') return {}
  throw new Error(`No test value for ${key}`)
}

function output(name = 'technical.mp4') {
  return {
    name,
    url: `/api/v1/file/${name}`,
    type: 'video',
    mode: 'video',
    artifact_class: 'final',
    linked_component_count: 0,
    favorite: false,
    size: 1,
    created_at: '2026-09-07T00:00:00Z',
    revision: `${name}-revision`,
    workspace: 'default',
    private: false,
    explicit: false,
  }
}

function installOutput(useStore, params, name = 'technical.mp4') {
  useStore.setState(state => ({
    models: [{
      model_type: 'test_model',
      name: 'Test model',
      family: 'test',
      architecture: 'test',
      fps: 1,
      default_for_operations: ['video'],
    }],
    families: [{ id: 'test', label: 'Test', order: 0 }],
    outputs: [output(name)],
    outputsTotal: 1,
    selectedOutput: 0,
    selectedOutputMetaName: name,
    selectedOutputMeta: { source: 'sidecar', params, upload_filenames: {} },
    params: {
      ...state.params,
      model_type: 'test_model',
    },
  }))
}

test('canonical projection is closed, presence-aware, and independent of profile validation', async () => {
  await withFreshStore(async ({ projectGenerationProfileParameters }) => {
    const source = Object.fromEntries(
      Object.entries(profileSchema.params).map(([key, descriptor]) => [key, canonicalValue(key, descriptor)]),
    )
    source._private_runtime_token = 'must-not-project'
    source.prompt = 'creative content stays sidecar-owned'
    source.custom_settings._private_runtime_token = 'must-not-project'
    source.custom_settings.image_ref_keyword_content = 'creative content stays sidecar-owned'
    source.custom_settings.unknown_legacy_knob = false
    const projected = projectGenerationProfileParameters(source)
    assert.deepEqual(Object.keys(projected).sort(), [...canonicalKeys].sort())
    for (const key of canonicalKeys) {
      if (key === 'custom_settings') continue
      assert.deepEqual(projected[key], source[key], key)
    }
    assert.deepEqual(projected.custom_settings, {
      bpm: 128,
      pace: 0.75,
      vc_steps: 12,
      vc_cfg_rate: 0,
      h3_sol_dense_steps: 0,
      h3_spectrum_profile: '',
      auto_split_every_s: null,
    })
    assert.equal(Object.hasOwn(projected, '_private_runtime_token'), false)
    assert.equal(Object.hasOwn(projected, 'prompt'), false)

    const omitted = projectGenerationProfileParameters({})
    assert.deepEqual(Object.keys(omitted).sort(), [...canonicalKeys].sort())
    for (const key of canonicalKeys) assert.equal(omitted[key], undefined, `${key} clears when omitted`)
  })
})

test('Load Settings restores every canonical technical key with only documented runtime transforms', async () => {
  await withFreshStore(async ({ useStore }) => {
    const source = Object.fromEntries(
      Object.entries(profileSchema.params).map(([key, descriptor]) => [key, canonicalValue(key, descriptor)]),
    )
    Object.assign(source, {
      model_type: 'test_model',
      prompt: 'restore this prompt',
      negative_prompt: 'restore this negative prompt',
      activated_loras: [],
      loras_multipliers: '',
      film_grain_intensity: 0,
      film_grain_saturation: 0,
      _private_runtime_token: 'must-not-project',
    })
    installOutput(useStore, source)
    await useStore.getState().loadSettingsFromOutput()
    const restored = useStore.getState()

    const expected = { ...source, repeat_generation: 1 }
    for (const key of canonicalKeys) {
      assert.deepEqual(restored.params[key], expected[key], key)
    }
    assert.equal(Object.hasOwn(restored.params, '_private_runtime_token'), false)
    assert.equal(restored.params.prompt, 'restore this prompt')
    assert.equal(restored.params.negative_prompt, 'restore this negative prompt')
    assert.equal(restored.outputCount, 1, 'output restore deliberately resets batch size')
    assert.equal(restored.filmGrainIntensity, 0)
    assert.equal(restored.filmGrainSaturation, 0)
  })
})

test('Load Settings restores declared non-H3 custom tuning and clears omitted custom tuning', async () => {
  await withFreshStore(async ({ useStore }) => {
    const technicalCustom = {
      bpm: 126,
      pace: 0.8,
      vc_steps: 24,
      vc_cfg_rate: 0,
      h3_spectrum_profile: '',
      auto_split_every_s: null,
      _private_runtime_token: 'must-not-project',
      image_ref_keyword_content: 'creative content stays sidecar-owned',
    }
    installOutput(useStore, {
      model_type: 'test_model', prompt: 'custom tuning', resolution: '1280x720',
      video_length: 81, num_inference_steps: 20, guidance_scale: 0,
      seed: 0, image_mode: 0, repeat_generation: 1,
      custom_settings: technicalCustom,
    }, 'custom-settings.mp4')
    await useStore.getState().loadSettingsFromOutput()
    assert.deepEqual(useStore.getState().params.custom_settings, {
      bpm: 126,
      pace: 0.8,
      vc_steps: 24,
      vc_cfg_rate: 0,
      h3_spectrum_profile: '',
      auto_split_every_s: null,
    })

    installOutput(useStore, {
      model_type: 'test_model', prompt: 'custom omitted', resolution: '1280x720',
      video_length: 81, num_inference_steps: 20, guidance_scale: 0,
      seed: 0, image_mode: 0, repeat_generation: 1,
    }, 'custom-settings-omitted.mp4')
    await useStore.getState().loadSettingsFromOutput()
    assert.equal(useStore.getState().params.custom_settings, undefined)
  })
})

test('Load Settings clears omitted optional technical keys instead of retaining current values', async () => {
  await withFreshStore(async ({ useStore }) => {
    const minimal = {
      model_type: 'test_model', prompt: 'minimal sidecar', resolution: '1280x720',
      video_length: 81, num_inference_steps: 20, guidance_scale: 0,
      seed: 0, image_mode: 0, repeat_generation: 1,
    }
    installOutput(useStore, minimal, 'omitted.mp4')
    const staleCanonical = Object.fromEntries(
      Object.entries(profileSchema.params).map(([key, descriptor]) => [key, canonicalValue(key, descriptor)]),
    )
    useStore.setState(state => ({
      params: {
        ...state.params,
        ...staleCanonical,
        model_type: 'test_model',
      },
    }))
    await useStore.getState().loadSettingsFromOutput()
    const restored = useStore.getState().params
    const intentionalDefaults = {
      sliding_window_size: 1,
      sliding_window_overlap: 0,
      audio_scale: 1,
      ltx25_video_vae: undefined,
      custom_settings: undefined,
    }
    for (const key of canonicalKeys) {
      const expected = Object.hasOwn(minimal, key)
        ? minimal[key]
        : Object.hasOwn(intentionalDefaults, key)
          ? intentionalDefaults[key]
          : undefined
      assert.deepEqual(restored[key], expected, `${key} clears or takes its documented model default`)
    }
  })
})

test('audio output duration restores seconds and auto duration instead of the video frame count', async () => {
  for (const duration of [600, 0]) {
    await withFreshStore(async ({ useStore }) => {
      installOutput(useStore, {
        model_type: 'test_model', prompt: 'saved speech', resolution: '1280x720',
        video_length: 81, num_inference_steps: 20, guidance_scale: 1,
        seed: 1, image_mode: 0, duration_seconds: duration,
      })
      useStore.setState(state => ({ models: state.models.map(model => ({ ...model, family: 'tts' })) }))
      assert.equal(await useStore.getState().loadSettingsFromOutput(), true)
      assert.equal(useStore.getState().generationMode, 'audio')
      assert.equal(useStore.getState().durationSeconds, duration)
      assert.equal(useStore.getState().params.duration_seconds, duration)
    }, { audio_only: true, sliding_window: false, duration_slider: { min: 0, max: 1800, default: 0 } })
  }
})
