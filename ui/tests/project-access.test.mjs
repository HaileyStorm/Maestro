import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

import {
  lockAllWorkspaces,
  lockWorkspace,
  unlockWorkspace,
} from '../src/api/client.ts'

const mainContentUrl = new URL('../src/components/MainContent/MainContent.tsx', import.meta.url)
const storeUrl = new URL('../src/stores/useStore.ts', import.meta.url)
const appUrl = new URL('../src/App.tsx', import.meta.url)
const accountDrawerUrl = new URL('../src/components/AccountSupport/AccountSupportDrawer.tsx', import.meta.url)
const toolsPanelUrl = new URL('../src/components/Sidebar/ToolsPanel.tsx', import.meta.url)
const clientUrl = new URL('../src/api/client.ts', import.meta.url)
const uiRoot = new URL('..', import.meta.url).pathname

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

test('workspace access API sends explicit remember policy and server-side revocations', async t => {
  const originalFetch = globalThis.fetch
  const requests = []
  globalThis.fetch = async (url, init = {}) => {
    requests.push({
      url: String(url),
      method: init.method,
      body: init.body == null ? null : JSON.parse(String(init.body)),
    })
    if (String(url).endsWith('/unlock')) {
      return new Response(JSON.stringify({
        unlocked: true,
        remember_policy: 'device',
        unlock_expires_at: 2_000_000_000,
        unlock_idle_expires_at: 1_999_000_000,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    return new Response(JSON.stringify({
      unlocked: false,
      locked_count: 1,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  const unlock = await unlockWorkspace('alpha/beta', 'transient password', 'device')
  const one = await lockWorkspace('alpha/beta')
  const all = await lockAllWorkspaces()

  assert.equal(unlock.remember_policy, 'device')
  assert.equal(unlock.unlock_idle_expires_at, 1_999_000_000)
  assert.deepEqual(one, { unlocked: false, locked_count: 1 })
  assert.deepEqual(all, { unlocked: false, locked_count: 1 })
  assert.deepEqual(requests, [
    {
      url: '/api/v1/workspaces/alpha%2Fbeta/unlock',
      method: 'POST',
      body: { password: 'transient password', remember: 'device' },
    },
    {
      url: '/api/v1/workspaces/alpha%2Fbeta/lock',
      method: 'POST',
      body: null,
    },
    {
      url: '/api/v1/workspaces/lock-all',
      method: 'POST',
      body: null,
    },
  ])
})

test('selector keeps row selection separate from independent project locks', async () => {
  const [main, store] = await Promise.all([
    readFile(mainContentUrl, 'utf8'),
    readFile(storeUrl, 'utf8'),
  ])
  const selector = main.slice(
    main.indexOf('function WorkspaceSelector()'),
    main.indexOf('function stripTimeSuffix'),
  )

  assert.match(selector, /workspaces\.map\(ws =>/)
  assert.match(selector, /ws\.password_protected && ws\.unlocked/)
  assert.match(selector, /<LockOpen size=\{12\}/)
  assert.match(selector, /aria-label=\{ws\.unlocked \? `Lock \$\{ws\.name\}` : `Unlock \$\{ws\.name\}`\}/)
  assert.match(selector, /event\.stopPropagation\(\)/)
  assert.match(selector, /await lockWorkspace\(name\)/)
  assert.match(selector, /await lockAllWorkspaces\(\)/)
  assert.match(selector, /if \(unlockingTarget \|\| lockingAll \|\| lockingTarget\) return/)
  assert.match(selector, /disabled=\{unlockingTarget !== null \|\| lockingAll \|\| lockingTarget !== null\}/)
  assert.match(selector, /Remember this device/)
  assert.match(selector, /setUnlockRemember\(event\.target\.checked \? 'device' : 'session'\)/)
  assert.match(selector, /beginUnlock\(ws\.name, null, true\)/)
  assert.match(selector, /if \(selectAfter\) \{[\s\S]*await switchWorkspace\(target\)/)
  assert.match(selector, /else \{[\s\S]*beginUnlock\(ws\.name\)/)
  assert.match(selector, /const nextExpiry = Math\.min\(\.\.\.expiries\)/)
  assert.match(selector, /const refreshed = await loadWorkspaces\(\)/)
  assert.match(selector, /window\.setTimeout\(\(\) => void refreshAtExpiry\(\), 5000\)/)
  assert.match(selector, /nextExpiry <= nowSeconds[\s\S]*\? 5000/)
  assert.match(selector, /await switchWorkspace\(ws\.name\)/)
  assert.doesNotMatch(selector, /localStorage|sessionStorage/)

  assert.match(store, /const result = await api\.lockWorkspace\(name\)[\s\S]*await get\(\)\.loadWorkspaces\(\)/)
  assert.match(store, /const result = await api\.lockAllWorkspaces\(\)[\s\S]*await get\(\)\.loadWorkspaces\(\)/)
  assert.match(store, /outputs: \[\],[\s\S]*selectedOutputKeys: \[\]/)
  assert.match(store, /pendingH3Plan: null,[\s\S]*pendingH3PlanWorkspace: null/)
  assert.match(store, /workspaces: state\.workspaces\.map\(workspace => workspace\.name === name[\s\S]*unlocked: false/)
  assert.match(store, /if \(!await get\(\)\.loadWorkspaces\(\)\) \{[\s\S]*current access state could not be refreshed/)
  assert.match(store, /const previousAccessRevoked = Boolean\(previousActive\)[\s\S]*workspace\.unlocked !== false/)
  assert.match(store, /const requestSequence = \+\+_workspaceLoadSequence[\s\S]*requestSequence !== _workspaceLoadSequence/)
  assert.match(store, /previousAccessRevoked[\s\S]*state\.jobs\.filter\(job => job\.workspace && job\.workspace !== previousActive\)/)
  assert.match(store, /job\.workspace \? job\.workspace !== name : !lockedActiveWorkspace/)
  assert.match(store, /job\.workspace[\s\S]*!lockedWorkspaces\.has\(job\.workspace\)[\s\S]*!lockedActiveWorkspace/)
})

test('account identity is established before the store-fenced startup project load', async () => {
  const [app, store] = await Promise.all([
    readFile(appUrl, 'utf8'),
    readFile(storeUrl, 'utf8'),
  ])
  const bootstrap = app.slice(app.indexOf('useEffect(() => {'), app.indexOf('// Backend-driven load/enhance transitions'))

  assert.ok(bootstrap.indexOf('loadAccessContext(false)') < bootstrap.indexOf('loadAccountContext(false)'))
  assert.ok(bootstrap.indexOf('loadAccountContext(false)') < bootstrap.indexOf('loadWorkspaces()'))
  assert.doesNotMatch(bootstrap, /api\.fetchWorkspaces/)
  assert.match(store, /const accountIdentityEpoch = _accountIdentityEpoch[\s\S]*accountIdentityEpoch !== _accountIdentityEpoch/)
  assert.match(store, /function _beginAccountMutation\(advanceIdentity = true\)[\s\S]*if \(advanceIdentity\) _advanceAccountIdentityEpoch\(\)/)
  assert.match(store, /reauthenticateAccount: async[\s\S]*_beginAccountMutation\(false\)/)
  assert.match(store, /changeAccountPassword: async[\s\S]*_beginAccountMutation\(false\)/)
  assert.match(store, /bootstrapAccount: async[\s\S]*loadAccountContext\(\)[\s\S]*loadWorkspaces\(\)/)
  assert.match(store, /loginAccount: async[\s\S]*loadAccountContext\(\)[\s\S]*loadWorkspaces\(\)/)
  assert.match(store, /recoverAccount: async[\s\S]*loadAccountContext\(\)[\s\S]*loadWorkspaces\(\)/)
  assert.match(store, /reauthenticateAccount: async[\s\S]*loadAccountContext\(\)[\s\S]*loadWorkspaces\(\)/)
  assert.match(store, /logoutAccount: async[\s\S]*_scrubAccountBoundProjectUi\(get\(\)\)[\s\S]*loadWorkspaces\(\)/)
})

test('project actions use exact per-project permissions without account-role inference', async () => {
  const main = await readFile(mainContentUrl, 'utf8')
  const permissionHelper = main.slice(
    main.indexOf('function workspaceAllowsPermission('),
    main.indexOf('const H3_MODEL_FALLBACK_LABELS'),
  )
  const selector = main.slice(main.indexOf('function WorkspaceSelector()'), main.indexOf('function stripTimeSuffix'))
  const queue = main.slice(main.indexOf('function QueuePanel('), main.indexOf('function GalleryBulkToolbar'))
  const bulk = main.slice(main.indexOf('function GalleryBulkToolbar()'), main.indexOf('function PipelinePlaceholder()'))
  const pipeline = main.slice(main.indexOf('function PipelinePlaceholder()'), main.indexOf('export function MainContent()'))

  assert.match(permissionHelper, /workspace\.project_permissions === undefined[\s\S]*workspace\.project_permissions\.includes\(permission\)/)
  assert.doesNotMatch(permissionHelper, /account.*role|role.*account/i)
  assert.match(selector, /workspaceAllowsPermission\(ws, 'project\.lifecycle'\)/)
  assert.match(selector, /workspaceAllowsPermission\(ws, 'project\.delete'\)/)
  assert.match(selector, /canCreateProject = !accountsEnabled \|\| accessContext\?\.accounts\?\.authenticated === true/)
  assert.match(queue, /workspaceAllowsPermission\([\s\S]*'project\.generate'/)
  assert.match(queue, /canManageGeneration && info\.status === 'queued'/)
  assert.match(queue, /canManageGeneration=\{canManageGeneration\}/)
  assert.match(bulk, /selectedOutputs\.every\(output => workspaceAllowsPermission\([\s\S]*'project\.mutate'/)
  assert.match(bulk, /mutableTargets[\s\S]*workspaceAllowsPermission\(workspace, 'project\.mutate'\)/)
  assert.match(bulk, /run\(canMoveSelection, \(\) => moveSelected\(target\)\)/)
  assert.match(pipeline, /projectActionVisibility\(activeProject\)\.generate/)
  assert.match(pipeline, /\{canStopPipeline && <button/)

  const permissions = {
    owner: ['project.open', 'project.read', 'project.mutate', 'project.generate', 'project.lifecycle', 'project.delete'],
    editor: ['project.open', 'project.read', 'project.mutate', 'project.generate'],
    viewer: ['project.open', 'project.read'],
  }
  assert.equal(permissions.owner.includes('project.delete'), true)
  assert.equal(permissions.editor.includes('project.generate'), true)
  assert.equal(permissions.editor.includes('project.lifecycle'), false)
  assert.equal(permissions.viewer.includes('project.generate'), false)

  const bundled = await build({
    stdin: {
      contents: "export { projectActionVisibility } from './src/components/MainContent/MainContent.tsx'",
      resolveDir: uiRoot,
      loader: 'js',
    },
    bundle: true,
    format: 'esm',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
  })
  const { projectActionVisibility } = await import(asDataModule(bundled.outputFiles[0].text))
  assert.deepEqual(projectActionVisibility({ name: 'owner-project', project_permissions: permissions.owner }), {
    mutate: true, generate: true, lifecycle: true, delete: true,
  })
  assert.deepEqual(projectActionVisibility({ name: 'editor-project', project_permissions: permissions.editor }), {
    mutate: true, generate: true, lifecycle: false, delete: false,
  })
  assert.deepEqual(projectActionVisibility({ name: 'viewer-project', project_permissions: permissions.viewer }), {
    mutate: false, generate: false, lifecycle: false, delete: false,
  })
})

test('migration is explicit, loopback-owner gated, and accounts-off paths make no migration request', async () => {
  const [drawer, store, client] = await Promise.all([
    readFile(accountDrawerUrl, 'utf8'),
    readFile(storeUrl, 'utf8'),
    readFile(clientUrl, 'utf8'),
  ])

  assert.match(client, /fetchAccountProjectMigration[\s\S]*\/api\/v1\/account\/projects\/migration/)
  assert.match(client, /migrateAccountProjects[\s\S]*method: 'POST'/)
  assert.match(store, /accessContext\?\.accounts\?\.enabled !== true[\s\S]*return null/)
  assert.match(store, /context\.account\?\.role !== 'owner'/)
  assert.match(store, /!context\.capabilities\.includes\('owner\.admin'\)/)
  assert.match(store, /api\.isDirectLoopbackHostname\(window\.location\.hostname\)/)
  assert.match(drawer, /Maestro will not make this change automatically/)
  assert.match(drawer, /Connect existing projects to this owner/)
  assert.match(drawer, /projectMigration\?\.state === 'needs_attention'/)
  assert.match(drawer, /Account-based project filtering is not enabled yet/)
  assert.match(drawer, /Existing browser and project-password access stays unchanged/)
  assert.match(drawer, /refresh-project-setup[^]*projectMigrationErrorMessage/)
  assert.match(drawer, /project_migration_needs_attention/)
  const identityScrub = drawer.slice(
    drawer.indexOf('const previousIdentity = accountIdentityRef.current'),
    drawer.indexOf("if (!open || activeTab !== 'account' || !migrationAvailable)"),
  )
  assert.match(identityScrub, /previousIdentity === accountIdentity[^]*clearSensitive\(\)[^]*setNotice\(null\)/)
  assert.doesNotMatch(identityScrub, /lifecycleRef\.current\.(?:closed|opened)/)
  assert.doesNotMatch(drawer, /Project access is on/)
  assert.doesNotMatch(store.slice(store.indexOf('loadAccountProjectMigration:'), store.indexOf('bootstrapAccount:')), /lockAllWorkspaces|lockWorkspace/)
})

test('logout scrub clears account-bound UI without revoking independent project grants', async () => {
  const store = await readFile(storeUrl, 'utf8')
  const scrub = store.slice(store.indexOf('function _scrubAccountBoundProjectUi'), store.indexOf('function _invalidateAccountRequests'))
  const logout = store.slice(store.indexOf('logoutAccount: async'), store.indexOf('reauthenticateAccount: async'))

  for (const expected of [
    'workspaces: []', 'activeWorkspace: \'\'', 'outputs: []', 'jobs: []',
    'params: { ...state.params, ...BLANK_VIDEO_INPUT_PARAMS }',
    'startImage: null', 'endImage: null', 'continueVideo: null',
    'continueVideoPath: \'\'', 'continueVideoUrl: \'\'', 'audioGuideFilename: null',
    'imageRefs: []', 'clips: []', 'videoSubModeStash: {}',
    'pipelineId: null', 'dashboardPipelineList: []', 'pendingH3Plan: null',
    'toolsSourcePath: null', 'toolsSourceName: null', 'toolsSourceUrl: null',
    'toolsRevoiceRefs: [null, null]', 'directorAudioFile: null', 'directorAudioPath: null',
    'directorAnalysis: null', 'directorPlannedClips: []', 'directorClipPlans: []',
    'directorReferenceImage: null', 'directorReferenceImagePath: null',
    'directorCharacterRefs: []', 'directorCharacterRefPaths: []',
    'directorLocationRefs: []', 'directorLocationRefPaths: []',
    'directorVoiceRef: null', 'directorVoiceRefPath: null', 'directorClipImages: []',
    'shortFilmCharacters: []', 'shortFilmPath: null',
  ]) assert.match(scrub, new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  assert.ok(logout.indexOf('_scrubAccountBoundProjectUi(get())') < logout.indexOf('await api.logoutAccount()'))
  assert.doesNotMatch(logout, /lockAllWorkspaces|lockWorkspace|revoke_workspace|project-password/)
})

test('Tools uploads use store-owned identity fences and Director preview uploads keep their request lease', async () => {
  const [store, tools] = await Promise.all([
    readFile(storeUrl, 'utf8'),
    readFile(toolsPanelUrl, 'utf8'),
  ])
  assert.match(tools, /const uploadSource = useStore\(s => s\.uploadToolsSource\)/)
  assert.match(tools, /const uploadRevoiceRef = useStore\(s => s\.uploadToolsRevoiceRef\)/)
  assert.doesNotMatch(tools, /api\.upload(?:Image|Audio)/)
  assert.doesNotMatch(tools, /console\.(?:error|log)/)
  assert.match(store, /uploadToolsSource: async[^]*_accountIdentityIsCurrent\(accountIdentityEpoch\)/)
  assert.match(store, /uploadToolsRevoiceRef: async[^]*_accountIdentityIsCurrent\(accountIdentityEpoch\)/)
  assert.match(store, /rotateAccountRecoveryCodes: async[^]*_beginAccountMutation\(false\)[^]*mutationSequence === _accountMutationRequestSequence/)
  assert.match(store, /createServerAccount: async[^]*_beginAccountMutation\(false\)[^]*mutationSequence === _accountMutationRequestSequence/)
  const previewStart = store.indexOf('directorGenerateStartImages: async')
  const preview = store.slice(previewStart, store.indexOf('directorApplyToClips:', previewStart))
  assert.match(preview, /_uploadDirectorRefs\(\{[^]*ownsWorkspace: ownsDirectorRequest/)
})
