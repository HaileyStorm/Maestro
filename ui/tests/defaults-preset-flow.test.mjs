import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

const UI_ROOT = new URL('..', import.meta.url).pathname

const source = relative => readFile(new URL(relative, import.meta.url), 'utf8')

const [advanced, store] = await Promise.all([
  source('../src/components/Sidebar/AdvancedSettings.tsx'),
  source('../src/stores/useStore.ts'),
])

function asDataModule(contents) {
  return `data:text/javascript;base64,${Buffer.from(contents).toString('base64')}`
}

let storeBundlePromise
let storeRealmSequence = 0
function storeBundle() {
  if (storeBundlePromise) return storeBundlePromise
  storeBundlePromise = build({
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
  }).then(result => result.outputFiles[0].text)
  return storeBundlePromise
}

async function loadStoreModuleFresh() {
  storeRealmSequence += 1
  return import(`${asDataModule(await storeBundle())}#defaults-preset-${storeRealmSequence}`)
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, reject, resolve }
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

async function waitForCondition(predicate, label, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(`Timed out waiting for ${label}`)
    await new Promise(resolve => setTimeout(resolve, 5))
  }
}

class StorageFake {
  values = new Map()
  getItem(key) { return this.values.get(key) ?? null }
  setItem(key, value) { this.values.set(key, String(value)) }
  removeItem(key) { this.values.delete(key) }
}

async function withFreshStore(fetchHandler, action) {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  globalThis.fetch = fetchHandler
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: '127.0.0.1' },
    matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  try {
    const { useStore } = await loadStoreModuleFresh()
    return await action(useStore)
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

function h3Defaults(role) {
  const owner = role === 'owner'
  return {
    h3_default_profile_id: owner ? 'high' : 'quality',
    num_inference_steps: owner ? 28 : 23,
    resolution: owner ? '1344x768' : '960x544',
    guidance_scale: 1,
    custom_settings: { h3_attention_engine: 'sol_attn' },
    tea_cache: 0,
    activated_loras: [],
    loras_multipliers: '',
    spatial_upsampling: '',
    delivery_resolution: '',
    delivery_fit: '',
  }
}

function h3ModelOptions(
  modelType = 'minimax_h3',
  resolutions = ['1344x768', '960x544'],
) {
  return {
    model_type: modelType,
    architecture: 'h3',
    fps: 24,
    guidance_max_phases: 1,
    frames_steps: 4,
    frames_minimum: 17,
    frames_maximum: 257,
    default_num_inference_steps: 28,
    default_guidance_scale: 1,
    sliding_window: false,
    resolutions,
    supports_end_frame: true,
    minimax_h3_reference_mode: modelType === 'minimax_h3_ref2va',
    reference_image_max_count: modelType === 'minimax_h3_ref2va' ? 9 : 0,
  }
}

function accountContext(role) {
  return {
    enabled: true,
    authenticated: true,
    account: { id: `${role}-account`, username: role, role, disabled: false },
    capabilities: ['account.self'],
    reauthenticated: true,
    passkey_authentication_available: false,
    bootstrap_available: false,
  }
}

function accessContext(role) {
  return {
    remote: false,
    account_project_access_active: true,
    account_project_creation_requires_account: true,
    project_password_required: false,
    project_names_visible: true,
    machine_controls: true,
    custom_model_sources: true,
    catalog_model_downloads: true,
    classic_ui: false,
    cloudflare_enabled: false,
    share_url: '',
    share_flow: '',
    accounts: accountContext(role),
  }
}

function anonymousContext() {
  return {
    enabled: true,
    authenticated: false,
    account: null,
    capabilities: [],
    reauthenticated: false,
    passkey_authentication_available: false,
    bootstrap_available: false,
  }
}

function highProfile() {
  return {
    id: 'high',
    label: 'High',
    description: 'High',
    available: true,
    fallback_reason: null,
    fallback_profile_id: null,
    download_required: false,
    download_components: [],
    estimate: null,
    settings: {
      ...h3Defaults('owner'),
      model_type: 'minimax_h3',
      lora_weights: {},
    },
  }
}

async function settleInOrder(first, second) {
  first()
  await new Promise(resolve => setImmediate(resolve))
  second()
  await new Promise(resolve => setImmediate(resolve))
}

function sliceBetween(contents, startMarker, endMarker) {
  const start = contents.indexOf(startMarker)
  assert.notEqual(start, -1, `found ${startMarker}`)
  const end = contents.indexOf(endMarker, start)
  assert.notEqual(end, -1, `found ${endMarker}`)
  return contents.slice(start, end)
}

test('fresh H3 role defaults are exact and late hydration is epoch fenced', () => {
  const hydration = sliceBetween(
    store,
    'function _applyModelDefaults(',
    '// Family → generation mode mapping',
  )
  assert.match(hydration, /const seq = \+\+_modelDefaultsSeq/)
  assert.match(hydration, /const accountIdentityEpoch = _accountIdentityEpoch/)
  assert.match(hydration, /const h3ProfileEpoch = _h3ProfileApplySeq/)
  assert.match(hydration, /const requestedMode = storeGet\(\)\.generationMode/)
  assert.match(hydration, /accountIdentityEpoch !== _accountIdentityEpoch/)
  assert.match(hydration, /h3ProfileEpoch !== _h3ProfileApplySeq/)
  assert.match(hydration, /state\.generationMode !== requestedMode/)
  assert.match(hydration, /options\.omittedOnly/)
  assert.match(hydration, /currentParams\[field\] == null/)
  assert.match(hydration, /selectedProfile = state\.h3SelectedProfile/)

  const identityAdvance = sliceBetween(
    store,
    'function _advanceAccountIdentityEpoch()',
    'function _accountIdentityIsCurrent',
  )
  for (const sequence of [
    '_modelOptionsSeq', '_modelDefaultsSeq', '_h3ProfileApplySeq',
  ]) assert.match(identityAdvance, new RegExp(`${sequence} \\+= 1`))

  const modelOptions = sliceBetween(
    store,
    'loadModelOptions: async (modelType)',
    '// System config',
  )
  assert.match(modelOptions, /!H3_STUDIO_MODELS\.has\(modelType\)/)
  assert.match(modelOptions, /modelOptionsOwnPrimaryDefaults/)

  const accountFlows = sliceBetween(store, 'accessContext: null,', 'reauthenticateAccount:')
  assert.ok(
    accountFlows.match(/\{ omittedOnly: true \}/g)?.length >= 5,
    'account bootstrap, login, logout, recovery, and delayed context refresh use omitted-only hydration',
  )
  assert.ok(
    accountFlows.match(/void get\(\)\.loadModelOptions\(modelType\)/g)?.length >= 5,
    'every identity hydration establishes a current-model capability successor',
  )

  const initial = sliceBetween(store, 'const defaultParams: GenerateParams = {', '// ── Per-sub-mode working sets')
  assert.match(initial, /resolution: '1344x768'/)
  assert.match(initial, /num_inference_steps: 28/)
})

test('fresh model hydration is role-exact in both response orders', async t => {
  for (const role of ['owner', 'user']) {
    for (const order of ['defaults-first', 'options-first']) {
      await t.test(`${role} ${order}`, async () => {
        const defaultsRequest = deferred()
        const optionsRequest = deferred()
        let defaultsStarted = false
        let optionsStarted = false
        await withFreshStore(async input => {
          const url = String(input)
          if (url.endsWith('/api/v1/models')) return jsonResponse({
            families: [{ id: 'h3', label: 'H3', order: 0 }],
            models: [{
              model_type: 'minimax_h3', name: 'MiniMax H3', family: 'h3',
              architecture: 'h3', is_i2v: true, is_t2v: true,
              guidance_max_phases: 1, fps: 24, is_downloaded: true,
              default_for_operations: ['video'],
            }],
          })
          if (url.endsWith('/api/v1/model-visibility')) return jsonResponse({
            configured: true, enabled_models: ['minimax_h3'], defaults_version: 1,
          })
          if (url.endsWith('/api/v1/defaults/minimax_h3')) {
            defaultsStarted = true
            return defaultsRequest.promise
          }
          if (url.endsWith('/api/v1/model-options/minimax_h3')) {
            optionsStarted = true
            return optionsRequest.promise
          }
          if (url.endsWith('/api/v1/loras/minimax_h3')) {
            return jsonResponse({ loras: [], guidance_max_phases: 1 })
          }
          if (url.endsWith('/api/v1/loras/installed')) return jsonResponse({ loras: [] })
          if (url.endsWith('/api/v1/loras/check-updates')) return jsonResponse({ status: 'fresh' })
          throw new Error(`Unexpected fresh hydration request: ${url}`)
        }, async useStore => {
          const loading = useStore.getState().loadModels()
          await waitForCondition(
            () => defaultsStarted && optionsStarted,
            `${role} fresh defaults and options requests`,
          )
          const resolveDefaults = () => defaultsRequest.resolve(jsonResponse(h3Defaults(role)))
          const resolveOptions = () => optionsRequest.resolve(jsonResponse(h3ModelOptions()))
          await settleInOrder(
            order === 'defaults-first' ? resolveDefaults : resolveOptions,
            order === 'defaults-first' ? resolveOptions : resolveDefaults,
          )
          await loading
          const expected = h3Defaults(role)
          await waitForCondition(
            () => useStore.getState().h3SelectedProfile === expected.h3_default_profile_id
              && useStore.getState().modelOptionsLoading === false,
            `${role} final fresh hydration`,
          )
          assert.equal(useStore.getState().params.num_inference_steps, expected.num_inference_steps)
          assert.equal(useStore.getState().params.resolution, expected.resolution)
          assert.equal(useStore.getState().h3SelectedProfile, expected.h3_default_profile_id)
          assert.equal(useStore.getState().modelOptions?.model_type, 'minimax_h3')
          assert.ok(useStore.getState().modelOptions?.resolutions?.includes(expected.resolution))
          assert.equal(useStore.getState().modelOptions?.supports_end_frame, true)
        })
      })
    }
  }
})

test('identity transition rejects pre-auth model-options in both response orders', async t => {
  for (const role of ['owner', 'user']) {
    for (const order of ['role-first', 'old-options-first']) {
      await t.test(`${role} ${order}`, async () => {
        const oldOptionsRequest = deferred()
        const currentOptionsRequest = deferred()
        const roleDefaultsRequest = deferred()
        let roleDefaultsStarted = false
        let optionsCalls = 0
        await withFreshStore(async input => {
          const url = String(input)
          if (url.endsWith('/api/v1/model-options/minimax_h3')) {
            optionsCalls += 1
            return optionsCalls === 1 ? oldOptionsRequest.promise : currentOptionsRequest.promise
          }
          if (url.endsWith('/api/v1/access-context')) return jsonResponse(accessContext(role))
          if (url.endsWith('/api/v1/workspaces')) return jsonResponse({
            workspaces: [{ name: 'role-project', project_role: 'owner', project_permissions: ['project.read'] }],
            active: 'role-project',
          })
          if (url.endsWith('/api/v1/presets?workspace=role-project')) return jsonResponse({ presets: [] })
          if (url.endsWith('/api/v1/defaults/minimax_h3')) {
            roleDefaultsStarted = true
            return roleDefaultsRequest.promise
          }
          throw new Error(`Unexpected identity options request: ${url}`)
        }, async useStore => {
          const anonymous = anonymousContext()
          useStore.setState({
            accessContext: { ...accessContext('owner'), accounts: anonymous },
            accountContext: anonymous,
            generationMode: 'video',
            selectedModelPerMode: { video: 'minimax_h3' },
            activeWorkspace: 'pre-auth-project',
            workspaces: [{ name: 'pre-auth-project' }],
          })
          const oldOptions = useStore.getState().loadModelOptions('minimax_h3')
          const identity = useStore.getState().loadAccessContext()
          await identity
          await waitForCondition(
            () => roleDefaultsStarted && optionsCalls === 2,
            `${role} role defaults and current options requests`,
          )
          const expected = h3Defaults(role)
          const resolveRole = () => {
            roleDefaultsRequest.resolve(jsonResponse(expected))
            currentOptionsRequest.resolve(jsonResponse(h3ModelOptions(
              'minimax_h3', [expected.resolution, '768x768'],
            )))
          }
          const resolveOld = () => oldOptionsRequest.resolve(jsonResponse(h3ModelOptions()))
          await settleInOrder(
            order === 'role-first' ? resolveRole : resolveOld,
            order === 'role-first' ? resolveOld : resolveRole,
          )
          await oldOptions
          await waitForCondition(
            () => useStore.getState().h3SelectedProfile === expected.h3_default_profile_id
              && useStore.getState().modelOptions?.model_type === 'minimax_h3',
            `${role} identity hydration`,
          )
          assert.equal(useStore.getState().params.num_inference_steps, expected.num_inference_steps)
          assert.equal(useStore.getState().params.resolution, expected.resolution)
          assert.equal(useStore.getState().modelOptionsLoading, false)
          assert.ok(useStore.getState().modelOptions?.resolutions?.includes(expected.resolution))
          assert.equal(useStore.getState().modelOptions?.supports_end_frame, true)
        })
      })
    }
  }
})

test('identity capability refetch cannot overwrite a concurrent model switch', async t => {
  for (const role of ['owner', 'user']) {
    for (const order of ['identity-first', 'switch-first']) {
      await t.test(`${role} ${order}`, async () => {
        const oldBaseOptions = deferred()
        const identityBaseOptions = deferred()
        const identityBaseDefaults = deferred()
        const switchedOptions = deferred()
        const switchedDefaults = deferred()
        let baseOptionsCalls = 0
        let baseDefaultsCalls = 0
        let switchedOptionsStarted = false
        let switchedDefaultsStarted = false
        await withFreshStore(async input => {
          const url = String(input)
          if (url.endsWith('/api/v1/model-options/minimax_h3')) {
            baseOptionsCalls += 1
            return baseOptionsCalls === 1 ? oldBaseOptions.promise : identityBaseOptions.promise
          }
          if (url.endsWith('/api/v1/defaults/minimax_h3')) {
            baseDefaultsCalls += 1
            return identityBaseDefaults.promise
          }
          if (url.endsWith('/api/v1/model-options/minimax_h3_ref2va')) {
            switchedOptionsStarted = true
            return switchedOptions.promise
          }
          if (url.endsWith('/api/v1/defaults/minimax_h3_ref2va')) {
            switchedDefaultsStarted = true
            return switchedDefaults.promise
          }
          if (url.endsWith('/api/v1/loras/minimax_h3_ref2va')) {
            return jsonResponse({ loras: [], guidance_max_phases: 1 })
          }
          if (url.endsWith('/api/v1/access-context')) return jsonResponse(accessContext(role))
          if (url.endsWith('/api/v1/workspaces')) return jsonResponse({
            workspaces: [{ name: 'role-project', project_role: 'owner', project_permissions: ['project.read'] }],
            active: 'role-project',
          })
          if (url.endsWith('/api/v1/presets?workspace=role-project')) return jsonResponse({ presets: [] })
          throw new Error(`Unexpected identity model-switch request: ${url}`)
        }, async useStore => {
          const anonymous = anonymousContext()
          useStore.setState({
            accessContext: { ...accessContext('owner'), accounts: anonymous },
            accountContext: anonymous,
            generationMode: 'video',
            selectedModelPerMode: { video: 'minimax_h3' },
            activeWorkspace: 'pre-auth-project',
            workspaces: [{ name: 'pre-auth-project' }],
          })
          const oldOptions = useStore.getState().loadModelOptions('minimax_h3')
          await useStore.getState().loadAccessContext()
          await waitForCondition(
            () => baseOptionsCalls === 2 && baseDefaultsCalls === 1,
            `${role} identity successor requests before model switch`,
          )
          assert.equal(await useStore.getState().selectModel('minimax_h3_ref2va'), true)
          await waitForCondition(
            () => switchedOptionsStarted && switchedDefaultsStarted,
            `${role} switched-model requests`,
          )

          const expected = h3Defaults(role)
          const resolveIdentity = () => {
            oldBaseOptions.resolve(jsonResponse(h3ModelOptions()))
            identityBaseOptions.resolve(jsonResponse(h3ModelOptions('minimax_h3', [expected.resolution])))
            identityBaseDefaults.resolve(jsonResponse(expected))
          }
          const resolveSwitch = () => {
            switchedOptions.resolve(jsonResponse(h3ModelOptions(
              'minimax_h3_ref2va', [expected.resolution, '768x768'],
            )))
            switchedDefaults.resolve(jsonResponse(expected))
          }
          await settleInOrder(
            order === 'identity-first' ? resolveIdentity : resolveSwitch,
            order === 'identity-first' ? resolveSwitch : resolveIdentity,
          )
          await oldOptions
          await waitForCondition(
            () => useStore.getState().modelOptions?.model_type === 'minimax_h3_ref2va'
              && useStore.getState().h3SelectedProfile === expected.h3_default_profile_id,
            `${role} switched model capabilities`,
          )
          assert.equal(useStore.getState().params.model_type, 'minimax_h3_ref2va')
          assert.equal(useStore.getState().params.num_inference_steps, expected.num_inference_steps)
          assert.equal(useStore.getState().params.resolution, expected.resolution)
          assert.ok(useStore.getState().modelOptions?.resolutions?.includes(expected.resolution))
          assert.equal(useStore.getState().modelOptions?.minimax_h3_reference_mode, true)
          assert.equal(useStore.getState().modelOptions?.reference_image_max_count, 9)
        })
      })
    }
  }
})

test('identity transition rejects a pre-auth H3 profile in both response orders', async t => {
  for (const role of ['owner', 'user']) {
    for (const order of ['role-first', 'old-profile-first']) {
      await t.test(`${role} ${order}`, async () => {
        const oldProfileOptions = deferred()
        const currentProfileOptions = deferred()
        const oldProfileDefaults = deferred()
        const oldProfileLoras = deferred()
        const roleDefaultsRequest = deferred()
        let defaultCalls = 0
        let optionsCalls = 0
        await withFreshStore(async input => {
          const url = String(input)
          if (url.endsWith('/api/v1/model-options/minimax_h3')) {
            optionsCalls += 1
            return optionsCalls === 1 ? oldProfileOptions.promise : currentProfileOptions.promise
          }
          if (url.endsWith('/api/v1/defaults/minimax_h3')) {
            defaultCalls += 1
            return defaultCalls === 1 ? oldProfileDefaults.promise : roleDefaultsRequest.promise
          }
          if (url.endsWith('/api/v1/loras/minimax_h3')) return oldProfileLoras.promise
          if (url.endsWith('/api/v1/access-context')) return jsonResponse(accessContext(role))
          if (url.endsWith('/api/v1/workspaces')) return jsonResponse({
            workspaces: [{ name: 'role-project', project_role: 'owner', project_permissions: ['project.read'] }],
            active: 'role-project',
          })
          if (url.endsWith('/api/v1/presets?workspace=role-project')) return jsonResponse({ presets: [] })
          throw new Error(`Unexpected identity profile request: ${url}`)
        }, async useStore => {
          const anonymous = anonymousContext()
          useStore.setState({
            accessContext: { ...accessContext('owner'), accounts: anonymous },
            accountContext: anonymous,
            generationMode: 'video',
            selectedModelPerMode: { video: 'minimax_h3' },
            h3PerformanceProfiles: [highProfile()],
            activeWorkspace: 'pre-auth-project',
            workspaces: [{ name: 'pre-auth-project' }],
          })
          const oldProfile = useStore.getState().applyH3PerformanceProfile('high')
          await waitForCondition(() => defaultCalls === 1, 'old profile requests')
          const identity = useStore.getState().loadAccessContext()
          await identity
          await waitForCondition(
            () => defaultCalls === 2 && optionsCalls === 2,
            `${role} role requests after profile`,
          )

          const resolveOldProfile = () => {
            oldProfileOptions.resolve(jsonResponse(h3ModelOptions()))
            oldProfileDefaults.resolve(jsonResponse(h3Defaults('owner')))
            oldProfileLoras.resolve(jsonResponse({ loras: [], guidance_max_phases: 1 }))
          }
          const resolveRole = () => {
            roleDefaultsRequest.resolve(jsonResponse(h3Defaults(role)))
            currentProfileOptions.resolve(jsonResponse(h3ModelOptions()))
          }
          await settleInOrder(
            order === 'role-first' ? resolveRole : resolveOldProfile,
            order === 'role-first' ? resolveOldProfile : resolveRole,
          )
          await oldProfile
          const expected = h3Defaults(role)
          await waitForCondition(
            () => useStore.getState().h3SelectedProfile === expected.h3_default_profile_id,
            `${role} final role profile`,
          )
          assert.equal(useStore.getState().params.num_inference_steps, expected.num_inference_steps)
          assert.equal(useStore.getState().params.resolution, expected.resolution)
          assert.equal(useStore.getState().h3SelectedProfile, expected.h3_default_profile_id)
          assert.equal(useStore.getState().modelOptions?.model_type, 'minimax_h3')
        })
      })
    }
  }
})

test('manual and loaded-preset values win over late model hydration', async t => {
  await t.test('manual values', async () => {
    const defaultsRequest = deferred()
    const optionsRequest = deferred()
    await withFreshStore(async input => {
      const url = String(input)
      if (url.endsWith('/api/v1/defaults/minimax_h3')) return defaultsRequest.promise
      if (url.endsWith('/api/v1/model-options/minimax_h3')) return optionsRequest.promise
      if (url.endsWith('/api/v1/loras/minimax_h3')) {
        return jsonResponse({ loras: [], guidance_max_phases: 1 })
      }
      throw new Error(`Unexpected manual hydration request: ${url}`)
    }, async useStore => {
      await useStore.getState().selectModel('minimax_h3')
      useStore.getState().setParam('num_inference_steps', 31)
      useStore.getState().setH3NativeResolution('1024x768')
      await settleInOrder(
        () => defaultsRequest.resolve(jsonResponse(h3Defaults('user'))),
        () => optionsRequest.resolve(jsonResponse(h3ModelOptions())),
      )
      await waitForCondition(() => useStore.getState().modelOptionsLoading === false, 'manual model options')
      assert.equal(useStore.getState().params.num_inference_steps, 31)
      assert.equal(useStore.getState().params.resolution, '1024x768')
      assert.equal(useStore.getState().h3SelectedProfile, 'custom')
    })
  })

  await t.test('loaded preset values', async () => {
    const defaultsRequest = deferred()
    const optionsRequest = deferred()
    const preset = {
      id: 'preset_scope_safe',
      name: 'Exact authored settings',
      mode: 'video',
      model_type: 'minimax_h3',
      activated_loras: [],
      loras_multipliers: '',
      lora_weights: {},
      spatial_upsampling: '',
      params: {
        num_inference_steps: 32,
        guidance_scale: 1,
        resolution: '1024x768',
        seed: 9,
        tea_cache: 0,
        custom_settings: { h3_attention_engine: 'sol_attn' },
      },
      created_at: 1,
    }
    await withFreshStore(async input => {
      const url = String(input)
      if (url.endsWith('/api/v1/defaults/minimax_h3')) return defaultsRequest.promise
      if (url.endsWith('/api/v1/model-options/minimax_h3')) return optionsRequest.promise
      if (url.endsWith('/api/v1/loras/minimax_h3')) {
        return jsonResponse({ loras: [], guidance_max_phases: 1 })
      }
      if (url.endsWith('/api/v1/presets?workspace=preset-project')) {
        return jsonResponse({ presets: [preset] })
      }
      throw new Error(`Unexpected preset hydration request: ${url}`)
    }, async useStore => {
      useStore.setState({ activeWorkspace: 'preset-project' })
      await useStore.getState().selectModel('minimax_h3')
      await useStore.getState().loadPresets()
      useStore.getState().loadPreset(useStore.getState().presets[0])
      await settleInOrder(
        () => optionsRequest.resolve(jsonResponse(h3ModelOptions())),
        () => defaultsRequest.resolve(jsonResponse(h3Defaults('user'))),
      )
      await waitForCondition(() => useStore.getState().modelOptionsLoading === false, 'preset model options')
      assert.equal(useStore.getState().params.num_inference_steps, 32)
      assert.equal(useStore.getState().params.resolution, '1024x768')
      assert.equal(useStore.getState().params.seed, 9)
      assert.equal(useStore.getState().h3SelectedProfile, 'custom')
    })
  })
})

test('preset save waits for scoped insertion before clearing and reports bounded status', () => {
  const manager = sliceBetween(advanced, 'function PresetManager()', '/** Active advanced features')
  assert.match(manager, /const \[saving, setSaving\] = useState\(false\)/)
  assert.match(manager, /await savePreset\(name\)/)
  assert.ok(
    manager.indexOf('await savePreset(name)') < manager.indexOf("setSaveName('')"),
    'the name clears only after save resolution',
  )
  assert.ok(
    manager.indexOf('await savePreset(name)') < manager.indexOf('setShowSave(false)'),
    'the form closes only after save resolution',
  )
  assert.match(manager, /disabled=\{!saveName\.trim\(\) \|\| saving\}/)
  assert.match(manager, /aria-busy=\{saving\}/)
  assert.match(manager, /Saving…/)
  assert.match(manager, /role="status"/)
  assert.match(manager, /aria-live="polite"/)
  assert.match(manager, /Preset saved\./)
  assert.match(manager, /Preset save could not be confirmed\. Check your connection and try again\./)
  assert.doesNotMatch(manager, /saveNotice[^\n]*error instanceof Error/)
})

test('preset store confirms account-project scope and keeps Recipes separate', () => {
  const presetStore = sliceBetween(store, '// Presets\n  presets: []', '// Model options')
  const save = sliceBetween(presetStore, 'savePreset: async (name)', 'loadPreset: (preset)')
  const load = sliceBetween(presetStore, 'loadPreset: (preset)', 'deletePreset: async')

  assert.match(save, /await api\.createPreset\(activeWorkspace/)
  assert.match(save, /accountIdentityEpoch !== _accountIdentityEpoch/)
  assert.match(save, /get\(\)\.activeWorkspace !== activeWorkspace/)
  assert.match(save, /_presetScopes\.set\(preset/)
  assert.match(save, /get\(\)\.presets\.includes\(preset\)/)
  assert.doesNotMatch(save, /console\.(?:error|warn)/)
  assert.doesNotMatch(save, /catch\s*\(/)

  assert.match(load, /const scope = _presetScopes\.get\(preset\)/)
  assert.match(load, /scope\.accountIdentityEpoch !== _accountIdentityEpoch/)
  assert.match(load, /scope\.workspace !== get\(\)\.activeWorkspace/)
  assert.match(load, /!get\(\)\.presets\.includes\(preset\)/)
  assert.match(load, /\+\+_modelDefaultsSeq/)
  assert.doesNotMatch(presetStore, /\/recipes|Recipes|applyRecipe/)
  assert.doesNotMatch(presetStore, /prompt:|negative_prompt|image_refs/)
})
