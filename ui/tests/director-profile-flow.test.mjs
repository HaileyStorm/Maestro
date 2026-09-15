import { readFile } from 'node:fs/promises'
import ts from 'typescript'
import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const bundled = build({
  stdin: { contents: "export { useStore } from './src/stores/useStore.ts'", resolveDir: new URL('..', import.meta.url).pathname, loader: 'js' },
  bundle: true, format: 'esm', platform: 'node', write: false, logLevel: 'silent',
}).then(result => result.outputFiles[0].text)
let realm = 0

class StorageFake {
  values = new Map()
  getItem(key) { return this.values.get(key) ?? null }
  setItem(key, value) { this.values.set(key, String(value)) }
  removeItem(key) { this.values.delete(key) }
}
const json = body => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}
const videoDefaults = { resolution: '640x480', video_length: 81, num_inference_steps: 8, guidance_scale: 4, seed: -1, image_mode: 0, repeat_generation: 1, settings_version: 2.52 }
const ready = model_type => ({ model_type, compatible: true, ready: true, reasons: [], actions: [], enabled: true, downloaded: true })

async function withStore(action, { legacyRoles = false } = {}) {
  const names = ['fetch', 'window', 'document', 'localStorage', 'sessionStorage']
  const originals = Object.fromEntries(names.map(name => [name, globalThis[name]]))
  const records = new Map()
  const requests = []
  const control = { beforeOptions: null, optionsByModel: new Map(), beforePresets: null, creator: 'creator_a', videoLoras: [], phases: 1, beforeUpload: null }
  globalThis.localStorage = new StorageFake()
  if (legacyRoles) globalThis.localStorage.setItem('maestro:director-image-roles-v1', JSON.stringify({
    schema_version: 1, creator_model_override: '', editor_model_override: 'editor_a',
    creator_loras: [{ id: 'look.safetensors', multiplier: 0 }], editor_loras: [],
  }))
  globalThis.sessionStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: '127.0.0.1' },
    matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.fetch = async (input, init = {}) => {
    const url = new URL(String(input), 'http://localhost')
    const method = init.method || 'GET'
    requests.push({ url: url.pathname, method, body: init.body })
    if (url.pathname === '/api/v1/presets' && method === 'GET') {
      if (control.beforePresets) await control.beforePresets.promise
      return json({ presets: [...records.values()], director_profiles_supported: true })
    }
    if (url.pathname === '/api/v1/models') return json({ families: [], models: [ready('video_a'), ready('creator_a'), ready('editor_a')] })
    if (url.pathname === '/api/v1/model-visibility') return json({ configured: true, enabled_models: ['video_a'], defaults_version: 999 })
    if (url.pathname === '/api/v1/workspaces/active' && method === 'PUT') return json({})
    if (url.pathname === '/api/v1/presets' && method === 'POST') {
      const body = JSON.parse(init.body)
      const record = { ...body, revision: 'revision-1', created_at: 1 }
      records.set(record.id, record)
      return json(record)
    }
    if (url.pathname.startsWith('/api/v1/presets/') && method === 'PUT') {
      const id = decodeURIComponent(url.pathname.split('/').pop())
      const { expected_revision, ...body } = JSON.parse(init.body)
      assert.equal(expected_revision, records.get(id).revision)
      const record = { ...body, id, revision: 'revision-2', created_at: records.get(id).created_at }
      records.set(id, record)
      return json(record)
    }
    if (url.pathname.startsWith('/api/v1/model-options/')) {
      const modelType = decodeURIComponent(url.pathname.split('/').pop())
      const wait = control.optionsByModel.get(modelType) || control.beforeOptions
      if (wait) await wait.promise
      return json({ model_type: modelType, fps: 24, frames_steps: 8,
        frames_minimum: 1, frames_maximum: 241, guidance_max_phases: 1, default_num_inference_steps: 8,
        resolution_presets: { '720p': { values: { '16:9': '1280x720' } }, '1080p': { values: { '9:16': '1080x1920' } } } })
    }
    if (url.pathname.startsWith('/api/v1/defaults/')) return json(videoDefaults)
    if (url.pathname === '/api/v1/director/capabilities') return json({
      schema_version: 1, readiness_reason_values: [], readiness_action_values: [],
      image_roles: {
        creator: { resolved_model: control.creator, candidates: [ready(control.creator)], selection_source: 'fixed_default' },
        editor: { resolved_model: 'editor_a', candidates: [ready('editor_a')], selection_source: 'fixed_default' },
      },
    })
    if (url.pathname.endsWith('/details') && url.pathname.startsWith('/api/v1/loras/')) {
      return json({ loras: [{ filename: 'look.safetensors', name: 'Look', model_type: control.creator }] })
    }
    if (url.pathname.startsWith('/api/v1/loras/')) return json({ loras: control.videoLoras, guidance_max_phases: control.phases })
    if (url.pathname === '/api/v1/upload') {
      if (control.beforeUpload) await control.beforeUpload.promise
      return json({ path: '/job/uploaded.png' })
    }
    if (url.pathname === '/api/v1/director/queue') return json({ entries: [] })
    if (url.pathname === '/api/v1/director/preflight') {
      const body = JSON.parse(init.body)
      return json({ status: 'ready', resolved: {
        pipeline_type: body.pipeline_type, video_model: body.video_model,
        image_creator_model: body.image_creator_model, continuity_editor_model: body.continuity_editor_model,
        director_resolution_preset: body.director_resolution_preset,
        director_aspect_ratio: body.director_aspect_ratio,
        video_resolution: '1080x1920', image_resolution: '1080x1920',
      }, components: [] })
    }
    throw new Error(`Unexpected profile request: ${method} ${url.pathname}`)
  }
  try {
    const { useStore } = await import(`data:text/javascript;base64,${Buffer.from(await bundled).toString('base64')}#director-profile-${++realm}`)
    useStore.setState(state => ({
      sidebarMode: 'director', generationMode: 'audio', activeWorkspace: 'project-a', modelsLoaded: true,
      models: [ready('video_a'), ready('creator_a'), ready('editor_a')],
      params: { ...state.params, model_type: 'audio_a', prompt: 'Keep this audio prompt', seed: 999 },
      selectedModelPerMode: { audio: 'audio_a', video: 'video_a' },
      savedParamsPerMode: { video: { ...videoDefaults, model_type: 'video_a', guidance_scale: 0, seed: 0, image_refs: ['/job/unused.png'] } },
      savedLoraPerMode: { video: { activated_loras: [], loras_multipliers: '', loraWeights: {}, availableLoras: [] } },
      directorResolution: '1080p', directorAspectRatio: '9:16', directorSeamless: false,
      directorShotImageGuidance: 'prompt_only', directorVideoInferenceStepsByModel: { video_a: 20 },
      directorVideoMaxShotFramesByModel: { video_a: 121 }, directorVideoSpatialUpsampling: '',
      directorVideoFilmGrainIntensity: 0, directorVideoFilmGrainSaturation: 0,
      directorVideoSelfRefiner: 0, directorAudioScale: 0, directorIdentityGuidanceScale: 0,
      h3StyleWorkflow: '', directorImageCreatorModelOverride: '', directorImageEditorModelOverride: 'editor_a',
      directorImageRoleLoras: { creator: [{ id: 'look.safetensors', multiplier: 0 }], editor: [] },
      directorSceneDescription: 'Keep this Director scene', directorReferenceImagePath: '/job/reference.png',
    }))
    if (!legacyRoles) useStore.getState().setDirectorImageRoleLoras('creator', [{ id: 'look.safetensors', multiplier: 0 }], 'creator_a')
    await useStore.getState().loadPresets()
    await action(useStore, { records, requests, control })
  } finally {
    for (const name of names) {
      if (originals[name] === undefined) delete globalThis[name]
      else globalThis[name] = originals[name]
    }
  }
}

test('Director save update and load round-trip all technical settings without changing active Audio', async () => {
  await withStore(async (store, { records }) => {
    const audio = store.getState().params
    await store.getState().savePreset('Shoot', 'director')
    const profile = store.getState().presets[0]
    assert.equal(profile.profile_version, 3)
    assert.equal(profile.profile_context, 'director')
    assert.equal(profile.mode, 'video')
    assert.equal(profile.model_type, 'video_a')
    assert.equal(profile.params.guidance_scale, 0)
    assert.equal(profile.params.seed, 0)
    assert.equal('image_refs' in profile.params, false)
    assert.equal('ui_settings' in profile, false)
    assert.equal(profile.director_settings.image_roles.creator.model_override, '')
    assert.equal(profile.director_settings.image_roles.creator.lora_model_type, 'creator_a')
    store.setState({ directorAudioScale: 2 })
    assert.equal(await store.getState().updatePreset(profile), true)
    const updated = store.getState().presets[0]
    assert.equal(updated.id, profile.id)
    assert.equal(updated.director_settings.audio_scale, 2)
    assert.equal(records.size, 1)
    store.setState({ directorResolution: '480p', directorAspectRatio: '16:9', directorAudioScale: 1,
      directorImageRoleLoras: { creator: [], editor: [] }, directorVideoInferenceStepsByModel: {} })
    assert.equal(await store.getState().loadPreset(updated), true)
    const loaded = store.getState()
    assert.equal(loaded.generationMode, 'audio')
    assert.equal(loaded.params, audio)
    assert.equal(loaded.directorResolution, '1080p')
    assert.equal(loaded.directorAspectRatio, '9:16')
    assert.equal(loaded.directorAudioScale, 2)
    assert.equal(loaded.directorVideoInferenceStepsByModel.video_a, 20)
    assert.equal(loaded.directorImageRoleLoras.creator[0].multiplier, 0)
    assert.equal(loaded.savedParamsPerMode.video.model_type, 'video_a')
    assert.equal(loaded.savedParamsPerMode.video.guidance_scale, 0)
    assert.equal(loaded.directorSceneDescription, 'Keep this Director scene')
    assert.equal(loaded.directorReferenceImagePath, '/job/reference.png')
  })
})

test('a late Director profile load cannot mutate a different project', async () => {
  await withStore(async (store, { control }) => {
    await store.getState().savePreset('Shoot', 'director')
    const profile = store.getState().presets[0]
    control.beforeOptions = deferred()
    const pending = store.getState().loadPreset(profile)
    await new Promise(resolve => setImmediate(resolve))
    store.setState({ activeWorkspace: 'project-b', directorResolution: '480p', directorAudioScale: 3 })
    control.beforeOptions.resolve()
    assert.equal(await pending, false)
    assert.equal(store.getState().activeWorkspace, 'project-b')
    assert.equal(store.getState().directorResolution, '480p')
    assert.equal(store.getState().directorAudioScale, 3)
  })
})

test('a changed automatic image role does not silently rebind saved LoRAs', async () => {
  await withStore(async (store, { control }) => {
    await store.getState().savePreset('Shoot', 'director')
    const profile = store.getState().presets[0]
    control.creator = 'creator_b'
    store.setState({ directorAudioScale: 4 })
    await assert.rejects(store.getState().loadPreset(profile))
    assert.equal(store.getState().directorAudioScale, 4)
    assert.equal(store.getState().directorComponentError?.component, 'image_creator_lora')
    assert.ok(store.getState().presets.includes(profile), 'saved profile remains available for repair')
  })
})

test('Director preparation failures reject save without creating a record or changing the setup', async () => {
  await withStore(async (store, { records, requests, control }) => {
    const before = store.getState()
    control.creator = ''
    await assert.rejects(store.getState().savePreset('Unavailable shoot', 'director'))
    assert.equal(records.size, 0)
    assert.equal(requests.some(request => request.url === '/api/v1/presets' && request.method === 'POST'), false)
    assert.equal(store.getState().params, before.params)
    assert.equal(store.getState().directorResolution, before.directorResolution)
    assert.equal(store.getState().directorComponentError?.component, 'image_creator_model')
  })
})

test('legacy automatic LoRAs require explicit model confirmation before becoming a profile', async () => {
  await withStore(async (store, { records }) => {
    assert.equal(store.getState().directorImageRoleLoraModels.creator, null)
    await assert.rejects(store.getState().savePreset('Legacy setup', 'director'))
    assert.equal(records.size, 0)
    assert.equal(store.getState().directorComponentError?.component, 'image_creator_lora')
    const selections = store.getState().directorImageRoleLoras.creator
    assert.equal(selections.length, 1, 'unbound selections remain available for review')
    // Same public action as the visible Use these LoRAs control.
    store.getState().setDirectorImageRoleLoras('creator', selections, 'creator_a')
    await store.getState().savePreset('Confirmed setup', 'director')
    assert.equal(store.getState().presets[0].director_settings.image_roles.creator.lora_model_type, 'creator_a')
    assert.equal(JSON.parse(localStorage.getItem('maestro:director-image-roles-v1')).creator_lora_model, 'creator_a')
  }, { legacyRoles: true })
})

test('switching projects clears Director support before the new profile response arrives', async () => {
  await withStore(async (store, { control, requests }) => {
    store.setState({ selectedDirectorProfileId: 'old-profile', loadOutputs: async () => true, loadWorkspaces: async () => {} })
    assert.equal(store.getState().directorProfilesSupported, true)
    control.beforePresets = deferred()
    assert.equal(await store.getState().switchWorkspace('project-b'), true)
    assert.equal(store.getState().directorProfilesSupported, false)
    assert.equal(store.getState().selectedDirectorProfileId, '')
    await assert.rejects(store.getState().savePreset('Too early', 'director'))
    assert.equal(requests.some(request => request.url === '/api/v1/presets' && request.method === 'POST'), false)
    control.beforePresets.resolve()
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(store.getState().directorProfilesSupported, true)
  })
})

test('Director profile load preserves pending Audio metadata and supersedes old Director metadata', async () => {
  await withStore(async (store, { control }) => {
    await store.getState().savePreset('Shoot', 'director')
    const profile = store.getState().presets[0]
    const audioWait = deferred()
    const directorWait = deferred()
    control.optionsByModel.set('audio_a', audioWait)
    control.optionsByModel.set('old_video', directorWait)
    const audio = store.getState().loadModelOptions('audio_a')
    const oldDirector = store.getState().loadDirectorResolutionOptions('old_video')
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(store.getState().modelOptionsLoading, true)
    assert.equal(await store.getState().loadPreset(profile), true)
    assert.equal(store.getState().directorResolutionOptions.model_type, 'video_a')
    audioWait.resolve()
    directorWait.resolve()
    await Promise.all([audio, oldDirector])
    assert.equal(store.getState().modelOptionsLoading, false)
    assert.equal(store.getState().modelOptions.model_type, 'audio_a')
    assert.equal(store.getState().directorResolutionOptions.model_type, 'video_a')
    assert.equal(store.getState().directorResolutionModelType, 'video_a')
  })
})

test('video LoRAs retain their model binding until explicit confirmation for another model', async () => {
  await withStore(async (store, { control }) => {
    control.videoLoras = ['video-look.safetensors']
    store.getState().directorSetLora('video', control.videoLoras, '0', { 'video-look.safetensors': [0] }, control.videoLoras, 'video_a')
    await store.getState().savePreset('Shoot', 'director')
    const profile = store.getState().presets[0]
    const original = store.getState().savedLoraPerMode.video
    await store.getState().selectDirectorVideoModel('video_b')
    await assert.rejects(store.getState().updatePreset(profile))
    assert.equal(store.getState().savedLoraPerMode.video, original, 'model switch must not discard the saved selection')
    assert.equal(store.getState().presets[0], profile)
    store.getState().directorSetLora('video', original.activated_loras, original.loras_multipliers,
      original.loraWeights, control.videoLoras, 'video_b')
    assert.equal(await store.getState().updatePreset(profile), true)
    const updated = store.getState().presets[0]
    assert.equal(updated.model_type, 'video_b')
    assert.deepEqual(updated.activated_loras, ['video-look.safetensors'])
    assert.deepEqual(updated.lora_weights, { 'video-look.safetensors': [0] })
  })
})

test('same-project capability downgrade aborts a pending Director profile load', async () => {
  await withStore(async (store, { control }) => {
    await store.getState().savePreset('Shoot', 'director')
    const profile = store.getState().presets[0]
    control.beforeOptions = deferred()
    const pending = store.getState().loadPreset(profile)
    await new Promise(resolve => setImmediate(resolve))
    store.setState({ directorProfilesSupported: false, directorAudioScale: 4 })
    control.beforeOptions.resolve()
    assert.equal(await pending, false)
    assert.equal(store.getState().directorAudioScale, 4)
  })
})


test('Director submission retains the newest settings when metadata finishes late', async () => {
  await withStore(async (store, { control, requests }) => {
    control.beforeOptions = deferred()
    const pending = store.getState().startDirectorPipeline('queue')
    await new Promise(resolve => setImmediate(resolve))
    assert.ok(requests.some(r => r.url.startsWith('/api/v1/model-options/')))
    store.setState({ directorAudioScale: 3 })
    control.beforeOptions.resolve()
    await pending
    assert.equal(requests.some(r => r.url === '/api/v1/director/queue'), false)
    assert.match(store.getState().directorError, /settings changed/)
    assert.equal(store.getState().directorAudioScale, 3)
    control.beforeOptions = null
    await store.getState().startDirectorPipeline('queue')
    const queued = requests.find(r => r.url === '/api/v1/director/queue')
    assert.ok(queued, store.getState().directorError)
    assert.equal(JSON.parse(queued.body).params.audio_scale, 3)
  })
})

test('a reference changed during upload is not overwritten or submitted', async () => {
  await withStore(async (store, { control, requests }) => {
    store.setState({ directorReferenceImagePath: null,
      directorReferenceImage: new File(['old'], 'old.png', { type: 'image/png' }) })
    control.beforeUpload = deferred()
    const pending = store.getState().startDirectorPipeline('queue')
    await new Promise(resolve => setImmediate(resolve))
    assert.ok(requests.some(r => r.url === '/api/v1/upload'))
    const replacement = new File(['new'], 'new.png', { type: 'image/png' })
    store.setState({ directorReferenceImage: replacement, directorReferenceImagePath: '/job/new.png' })
    control.beforeUpload.resolve()
    await pending
    assert.equal(store.getState().directorReferenceImage, replacement)
    assert.equal(store.getState().directorReferenceImagePath, '/job/new.png')
    assert.equal(requests.some(r => r.url === '/api/v1/director/queue'), false)
  })
})

test('changed video LoRA phase counts require confirmation before saving or submitting', async () => {
  await withStore(async (store, { control, requests }) => {
    control.videoLoras = ['video-look.safetensors']
    store.getState().directorSetLora('video', control.videoLoras, '0', { 'video-look.safetensors': [0] }, control.videoLoras, 'video_a')
    control.phases = 2
    await assert.rejects(store.getState().savePreset('Old phases', 'director'), /weights/)
    await store.getState().startDirectorPipeline('queue')
    assert.match(store.getState().directorError, /weights/)
    assert.equal(requests.some(r => r.url === '/api/v1/director/queue'), false)
    store.getState().directorSetLora('video', control.videoLoras, '0;0', { 'video-look.safetensors': [0, 0] }, control.videoLoras, 'video_a')
    await store.getState().startDirectorPipeline('queue')
    const queued = requests.find(r => r.url === '/api/v1/director/queue')
    assert.ok(queued, store.getState().directorError)
    assert.equal(JSON.parse(queued.body).params.video_loras.loras_multipliers, '0;0')
  })
})

test('leaving video mode retains Director LoRAs awaiting model confirmation', async () => {
  await withStore(async (store, { control }) => {
    control.videoLoras = ['video-look.safetensors']
    store.setState(s => ({ generationMode: 'video', params: { ...s.params, model_type: 'video_a' } }))
    store.getState().directorSetLora('video', control.videoLoras, '0', { 'video-look.safetensors': [0] }, control.videoLoras, 'video_a')
    const saved = store.getState().savedLoraPerMode.video
    await store.getState().selectDirectorVideoModel('video_b')
    store.getState().setGenerationMode('tools')
    assert.equal(store.getState().savedLoraPerMode.video, saved)
    assert.equal(store.getState().savedLoraPerMode.video.model_type, 'video_a')
  })
})


test('confirmed Director video LoRAs survive leaving the active video mode', async () => {
  await withStore(async (store, { control }) => {
    control.videoLoras = ['video-look.safetensors']
    store.setState(s => ({ generationMode: 'video', params: { ...s.params, model_type: 'video_a' } }))
    store.getState().directorSetLora('video', control.videoLoras, '0', { 'video-look.safetensors': [0] }, control.videoLoras, 'video_a')
    assert.deepEqual(store.getState().params.activated_loras, control.videoLoras)
    store.getState().setGenerationMode('tools')
    assert.deepEqual(store.getState().savedLoraPerMode.video.activated_loras, control.videoLoras)
    assert.equal(store.getState().savedLoraPerMode.video.loras_multipliers, '0')
  })
})


test('a real admission catalog refresh preserves working video settings and submits them', async () => {
  await withStore(async (store, { requests }) => {
    const snapshot = store.getState().savedParamsPerMode.video
    await store.getState().startDirectorPipeline('queue')
    assert.equal(store.getState().savedParamsPerMode.video, snapshot)
    assert.ok(requests.some(r => r.url === '/api/v1/models'))
    const queued = requests.find(r => r.url === '/api/v1/director/queue')
    assert.ok(queued, store.getState().directorError)
    assert.equal(JSON.parse(queued.body).params.video_params.guidance_scale, 0)
    assert.equal(JSON.parse(queued.body).params.video_params.seed, 0)
  })
})


test('image-role picker edits and removals preserve the old model until confirmation', async () => {
  const source = await readFile(new URL('../src/components/Sidebar/DirectorChat.tsx', import.meta.url), 'utf8')
  const ast = ts.createSourceFile('DirectorChat.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let callback
  function visit(node) {
    if (ts.isJsxAttribute(node) && node.name.getText(ast) === 'onChange'
      && node.initializer?.expression?.getText(ast).includes('setSelections(role, next,')) {
      callback = node.initializer.expression.getText(ast)
    }
    ts.forEachChild(node, visit)
  }
  visit(ast)
  assert.ok(callback)
  const makeChange = new Function('setSelections', 'role', 'needsLoraConfirmation', 'boundModel', 'effectiveModel', `return (${callback})`)
  await withStore(async (store, { control }) => {
    store.getState().setDirectorImageRoleLoras('creator', [
      { id: 'look.safetensors', multiplier: 1 }, { id: 'second.safetensors', multiplier: 1 },
    ], 'creator_a')
    control.creator = 'creator_b'
    store.getState().setDirectorImageRoleModel('creator', 'creator_b')
    const change = makeChange(store.getState().setDirectorImageRoleLoras, 'creator', true, 'creator_a', 'creator_b')
    change([{ id: 'look.safetensors', multiplier: 0 }, { id: 'second.safetensors', multiplier: 1 }])
    assert.equal(store.getState().directorImageRoleLoraModels.creator, 'creator_a')
    change([{ id: 'look.safetensors', multiplier: 0 }])
    assert.equal(store.getState().directorImageRoleLoraModels.creator, 'creator_a')
    await assert.rejects(store.getState().savePreset('Unconfirmed role', 'director'))
    store.getState().setDirectorImageRoleLoras('creator', store.getState().directorImageRoleLoras.creator, 'creator_b')
    await store.getState().savePreset('Confirmed role', 'director')
    assert.equal(store.getState().presets[0].director_settings.image_roles.creator.lora_model_type, 'creator_b')
    const legacyChange = makeChange(store.getState().setDirectorImageRoleLoras, 'creator', true, null, 'creator_b')
    legacyChange([{ id: 'look.safetensors', multiplier: 0 }])
    assert.equal(store.getState().directorImageRoleLoraModels.creator, null)
  })
})
