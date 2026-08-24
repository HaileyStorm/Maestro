import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

import {
  isAccountProjectAccessActive,
  lockAllWorkspaces,
  lockWorkspace,
  unlockWorkspace,
} from '../src/api/client.ts'

const mainContentUrl = new URL('../src/components/MainContent/MainContent.tsx', import.meta.url)
const storeUrl = new URL('../src/stores/useStore.ts', import.meta.url)
const appUrl = new URL('../src/App.tsx', import.meta.url)
const accountDrawerUrl = new URL('../src/components/AccountSupport/AccountSupportDrawer.tsx', import.meta.url)
const toolsPanelUrl = new URL('../src/components/Sidebar/ToolsPanel.tsx', import.meta.url)
const sidebarUrl = new URL('../src/components/Sidebar/Sidebar.tsx', import.meta.url)
const referenceLibraryUrl = new URL('../src/components/Sidebar/ProjectReferenceLibrary.tsx', import.meta.url)
const clientUrl = new URL('../src/api/client.ts', import.meta.url)
const uiRoot = new URL('..', import.meta.url).pathname

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

async function loadAccountDrawerRuntime() {
  const bundled = await build({
    stdin: {
      contents: "export { AccountSupportDrawer } from './src/components/AccountSupport/AccountSupportDrawer.tsx'",
      resolveDir: uiRoot,
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
      name: 'account-drawer-access-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'drawer-test' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'drawer-test' }))
        bundle.onResolve({ filter: /^react-dom$/ }, () => ({ path: 'react-dom', namespace: 'drawer-test' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide', namespace: 'drawer-test' }))
        bundle.onResolve({ filter: /api\/client$/ }, () => ({ path: 'api', namespace: 'drawer-test' }))
        bundle.onResolve({ filter: /lib\/modalFocus$/ }, () => ({ path: 'focus', namespace: 'drawer-test' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'drawer-test' }))
        bundle.onResolve({ filter: /accountDrawerLifecycle$/ }, () => ({ path: 'lifecycle', namespace: 'drawer-test' }))
        bundle.onResolve({ filter: /\.\/SupportPanel$/ }, () => ({ path: 'support-panel', namespace: 'drawer-test' }))
        bundle.onResolve({ filter: /\.\/supportPresentation$/ }, () => ({ path: 'presentation', namespace: 'drawer-test' }))
        bundle.onLoad({ filter: /.*/, namespace: 'drawer-test' }, args => {
          if (args.path === 'react') return { contents: `
            export const useCallback = value => value
            export const useEffect = effect => globalThis.__drawerEffects.push(effect)
            export const useId = () => 'drawer-test-id'
            export const useRef = value => ({ current: value })
            export const useState = value => [value === 'support' ? 'account' : value, () => {}]
          ` }
          if (args.path === 'jsx-runtime') return { contents: `
            export const Fragment = Symbol.for('fragment')
            export const jsx = (type, props, key) => ({ type, key, props: props || {} })
            export const jsxs = jsx
          ` }
          if (args.path === 'react-dom') return { contents: 'export const createPortal = value => value' }
          if (args.path === 'lucide') return { contents: `
            export const Check='Check', HeartHandshake='HeartHandshake', KeyRound='KeyRound', Loader2='Loader2', LogIn='LogIn', LogOut='LogOut', RefreshCw='RefreshCw', ShieldCheck='ShieldCheck', UserCog='UserCog', UserPlus='UserPlus', UserRound='UserRound', X='X'
          ` }
          if (args.path === 'api') return { contents: `
            export class AccountApiError extends Error {}
            export const isAccountProjectAccessActive = (context, migration = null) => context?.accounts?.enabled === true && (migration !== null ? migration.state === 'active' && migration.enforced === true : context.account_project_access_active === true)
            export const registerAccount = async () => undefined
          ` }
          if (args.path === 'focus') return { contents: 'export const closeModalIfTop = () => true; export const installModalFocus = () => () => {}' }
          if (args.path === 'store') return { contents: 'export const useStore = selector => selector(globalThis.__drawerStore)' }
          if (args.path === 'lifecycle') return { contents: `
            export const createAccountDrawerLifecycle = () => ({
              opened() {}, closed() {}, operationLease: () => () => true,
            })
          ` }
          if (args.path === 'support-panel') return { contents: 'export const SupportPanel = () => null' }
          if (args.path === 'presentation') return { contents: 'export const nextAccountSupportTab = () => null' }
          return null
        })
      },
    }],
  })
  return import(`${asDataModule(bundled.outputFiles[0].text)}#drawer-access`)
}

function renderedText(node) {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(renderedText).join(' ')
  return renderedText(node.props?.children)
}

function renderedElements(node, predicate, found = []) {
  if (Array.isArray(node)) {
    for (const child of node) renderedElements(child, predicate, found)
  } else if (node && typeof node === 'object') {
    if (predicate(node)) found.push(node)
    renderedElements(node.props?.children, predicate, found)
  }
  return found
}

test('server-authored account cutover decides whether project passwords apply', () => {
  const accounts = {
    enabled: true,
    authenticated: true,
    account: null,
    capabilities: [],
    reauthenticated: false,
    passkey_authentication_available: false,
    activation_state: 'ready',
  }
  const context = {
    accounts,
    account_project_access_active: true,
    project_password_required: false,
  }

  assert.equal(isAccountProjectAccessActive(context), true)
  assert.equal(isAccountProjectAccessActive({ ...context, accounts: { ...accounts, enabled: false } }), false)
  assert.equal(isAccountProjectAccessActive({ ...context, account_project_access_active: false }), false)
  assert.equal(isAccountProjectAccessActive({ accounts, project_password_required: false }), false)
  assert.equal(isAccountProjectAccessActive(context, {
    state: 'disabled', enforced: false, project_count: 0, needs_attention: 0,
  }), true)
  assert.equal(isAccountProjectAccessActive(context, {
    state: 'not_started', enforced: false, project_count: 1, needs_attention: 0,
  }), true)
  assert.equal(isAccountProjectAccessActive(context, {
    state: 'needs_attention', enforced: false, project_count: 1, needs_attention: 1,
  }), true)
  assert.equal(isAccountProjectAccessActive({ ...context, account_project_access_active: false }, {
    state: 'active', enforced: true, project_count: 1, needs_attention: 0,
  }), true)
  assert.equal(isAccountProjectAccessActive(context, {
    state: 'active', enforced: false, project_count: 1, needs_attention: 0,
  }), true)
})

test('account drawer keeps sealed active access authoritative over stale migration detail', async () => {
  const bundled = await build({
    stdin: {
      contents: "export { isAccountProjectAccessActiveForDrawer } from './src/components/AccountSupport/AccountSupportDrawer.tsx'",
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
  const { isAccountProjectAccessActiveForDrawer } = await import(asDataModule(bundled.outputFiles[0].text))
  const context = {
    accounts: { enabled: true },
    account_project_access_active: true,
  }

  assert.equal(isAccountProjectAccessActiveForDrawer(context, null), true)
  assert.equal(isAccountProjectAccessActiveForDrawer(context, {
    state: 'not_started', enforced: false, project_count: 3, needs_attention: 0,
  }), true)
  assert.equal(isAccountProjectAccessActiveForDrawer(context, {
    state: 'needs_attention', enforced: false, project_count: 3, needs_attention: 1,
  }), true)
  assert.equal(isAccountProjectAccessActiveForDrawer({
    ...context, account_project_access_active: false,
  }, {
    state: 'active', enforced: true, project_count: 3, needs_attention: 0,
  }), true)
})

test('stable active account access renders no migration setup and schedules no detail request', async t => {
  const { AccountSupportDrawer } = await loadAccountDrawerRuntime()
  const previousWindow = globalThis.window
  const previousDocument = globalThis.document
  const previousHTMLElement = globalThis.HTMLElement
  t.after(() => {
    globalThis.window = previousWindow
    globalThis.document = previousDocument
    globalThis.HTMLElement = previousHTMLElement
    delete globalThis.__drawerEffects
    delete globalThis.__drawerStore
  })
  globalThis.HTMLElement = class HTMLElement {}
  globalThis.document = {
    activeElement: null,
    getElementById: () => null,
  }

  for (const testCase of [
    {
      hostname: 'maestro.example.test',
      remote: true,
      reauthenticated: false,
      migration: null,
    },
    {
      hostname: '127.0.0.1',
      remote: false,
      reauthenticated: true,
      migration: { state: 'needs_attention', enforced: false, project_count: 3, needs_attention: 1 },
    },
  ]) {
    globalThis.window = { location: { hostname: testCase.hostname } }
    let migrationRequests = 0
    const accountContext = {
      enabled: true,
      authenticated: true,
      account: { id: 'owner', username: 'Owner', role: 'owner' },
      capabilities: ['account.self', 'owner.admin'],
      reauthenticated: testCase.reauthenticated,
      passkey_authentication_available: false,
      activation_state: 'ready',
    }
    const asyncNoop = async () => undefined
    globalThis.__drawerEffects = []
    globalThis.__drawerStore = {
      accountDrawerOpen: true,
      setAccountDrawerOpen() {},
      accountContext,
      accessContext: {
        remote: testCase.remote,
        accounts: accountContext,
        account_project_access_active: true,
      },
      accountContextLoading: false,
      accountProjectMigration: testCase.migration,
      accountProjectMigrationLoading: false,
      accountSessions: [],
      accountUsers: [],
      accountDetailsLoading: false,
      loadAccountContext: async () => accountContext,
      loadAccountProjectMigration: async () => { migrationRequests += 1 },
      migrateAccountProjects: asyncNoop,
      bootstrapAccount: asyncNoop,
      loginAccount: asyncNoop,
      logoutAccount: asyncNoop,
      reauthenticateAccount: asyncNoop,
      recoverAccount: asyncNoop,
      changeAccountPassword: asyncNoop,
      rotateAccountRecoveryCodes: asyncNoop,
      loadAccountSessions: asyncNoop,
      revokeAccountSession: asyncNoop,
      revokeAllAccountSessions: asyncNoop,
      loadAccountUsers: asyncNoop,
      createServerAccount: asyncNoop,
      setServerAccountDisabled: asyncNoop,
    }

    const text = renderedText(AccountSupportDrawer())
    for (const effect of globalThis.__drawerEffects) effect()
    await new Promise(resolve => setImmediate(resolve))

    assert.match(text, /Project access follows your account membership/)
    assert.doesNotMatch(text, /Connect existing projects/)
    assert.doesNotMatch(text, /Confirm the owner password above before connecting existing projects/)
    assert.doesNotMatch(text, /Account-based project filtering is not enabled yet/)
    assert.equal(migrationRequests, 0)
  }
})

test('account drawer explains and disables the sole owner row', async t => {
  const { AccountSupportDrawer } = await loadAccountDrawerRuntime()
  const previousWindow = globalThis.window
  const previousDocument = globalThis.document
  const previousHTMLElement = globalThis.HTMLElement
  t.after(() => {
    globalThis.window = previousWindow
    globalThis.document = previousDocument
    globalThis.HTMLElement = previousHTMLElement
    delete globalThis.__drawerEffects
    delete globalThis.__drawerStore
  })
  globalThis.window = { location: { hostname: '127.0.0.1' } }
  globalThis.HTMLElement = class HTMLElement {}
  globalThis.document = { activeElement: null, getElementById: () => null }

  const owner = {
    id: 'owner', username: 'Owner', role: 'owner', disabled: false,
    created_at: 1, has_email: false, passkey_credentials: 0,
    passkey_authentication_available: false,
  }
  const user = { ...owner, id: 'user', username: 'Creator', role: 'user' }
  const accountContext = {
    enabled: true,
    authenticated: true,
    account: owner,
    capabilities: ['account.self', 'accounts.admin', 'services.admin'],
    reauthenticated: true,
    passkey_authentication_available: false,
    activation_state: 'ready',
  }
  const asyncNoop = async () => undefined
  globalThis.__drawerEffects = []
  globalThis.__drawerStore = {
    accountDrawerOpen: true,
    setAccountDrawerOpen() {},
    accountContext,
    accessContext: {
      remote: false,
      accounts: accountContext,
      account_project_access_active: true,
    },
    accountContextLoading: false,
    accountProjectMigration: null,
    accountProjectMigrationLoading: false,
    accountSessions: [],
    accountUsers: [owner, user],
    accountDetailsLoading: false,
    loadAccountContext: async () => accountContext,
    loadAccountProjectMigration: asyncNoop,
    migrateAccountProjects: asyncNoop,
    bootstrapAccount: asyncNoop,
    loginAccount: asyncNoop,
    logoutAccount: asyncNoop,
    reauthenticateAccount: asyncNoop,
    recoverAccount: asyncNoop,
    changeAccountPassword: asyncNoop,
    rotateAccountRecoveryCodes: asyncNoop,
    loadAccountSessions: asyncNoop,
    revokeAccountSession: asyncNoop,
    revokeAllAccountSessions: asyncNoop,
    loadAccountUsers: asyncNoop,
    createServerAccount: asyncNoop,
    setServerAccountDisabled: asyncNoop,
  }

  const tree = AccountSupportDrawer()
  const ownerRows = renderedElements(tree, node => node.type === 'div' && node.key === owner.id)
  const userRows = renderedElements(tree, node => node.type === 'div' && node.key === user.id)
  assert.equal(ownerRows.length, 1)
  assert.equal(userRows.length, 1)
  assert.match(renderedText(ownerRows[0]), /current owner account cannot be disabled/)
  assert.doesNotMatch(renderedText(userRows[0]), /cannot be disabled/)
  const ownerButtons = renderedElements(
    ownerRows[0],
    node => node.type === 'button' && renderedText(node) === 'Disable',
  )
  const userButtons = renderedElements(
    userRows[0],
    node => node.type === 'button' && renderedText(node) === 'Disable',
  )
  assert.equal(ownerButtons.length, 1)
  assert.equal(userButtons.length, 1)
  assert.equal(ownerButtons[0].props.disabled, true)
  assert.equal(userButtons[0].props.disabled, false)
})

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
  assert.match(selector, /accountProjectAccessActive = api\.isAccountProjectAccessActive\(accessContext, accountProjectMigration\)/)
  assert.match(selector, /legacyProjectPasswordAccess = !accountProjectAccessActive/)
  assert.match(selector, /legacyProjectPasswordAccess && ws\.password_protected && !ws\.unlocked/)
  assert.match(selector, /legacyProjectPasswordAccess && ws\.password_protected && \(/)
  assert.match(selector, /legacyProjectPasswordAccess && !remote && \(!ws\.password_protected \|\| ws\.unlocked\)/)
  assert.match(selector, /legacyProjectPasswordAccess && unlockTarget/)
  assert.match(selector, /legacyProjectPasswordAccess && <input[\s\S]*type="password"[\s\S]*Required password/)
  assert.match(selector, /createWorkspace\(name, accountProjectAccessActive \? undefined : newPassword \|\| undefined\)/)
  assert.match(selector, /accountProjectAccessActive \? 'New project' : 'New Workspace'/)
  assert.match(selector, /ws\.password_protected && ws\.unlocked/)
  assert.match(selector, /<LockOpen size=\{12\}/)
  assert.match(selector, /aria-label=\{ws\.unlocked \? `Lock \$\{ws\.name\}` : `Unlock \$\{ws\.name\}`\}/)
  assert.match(selector, /event\.stopPropagation\(\)/)
  assert.match(selector, /await lockWorkspace\(name\)/)
  assert.match(selector, /await lockAllWorkspaces\(\)/)
  assert.match(selector, /if \(accountProjectAccessActive \|\| unlockingTarget \|\| lockingAll \|\| lockingTarget\) return/)
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
  assert.match(main, /accountProjectAccessActive \? 'Open project and resume' : 'Unlock project and resume'/)

  assert.match(store, /const result = await api\.lockWorkspace\(name\)[\s\S]*await get\(\)\.loadWorkspaces\(\)/)
  assert.match(store, /const result = await api\.lockAllWorkspaces\(\)[\s\S]*await get\(\)\.loadWorkspaces\(\)/)
  assert.match(store, /api\.isAccountProjectAccessActive\([\s\S]*before\.accountProjectMigration/)
  assert.match(store, /nextActiveWorkspace === undefined[\s\S]*!accountProjectAccessActive && nextActiveWorkspace\.unlocked === false/)
  assert.match(store, /api\.createWorkspace\(name, accountProjectAccessActive \? undefined : password, 'device'\)/)
  assert.match(store, /outputs: \[\],[\s\S]*selectedOutputKeys: \[\]/)
  assert.match(store, /pendingH3Plan: null,[\s\S]*pendingH3PlanWorkspace: null/)
  assert.match(store, /workspaces: state\.workspaces\.map\(workspace => workspace\.name === name[\s\S]*unlocked: false/)
  assert.match(store, /if \(!await get\(\)\.loadWorkspaces\(\)\) \{[\s\S]*current access state could not be refreshed/)
  assert.match(store, /const previousAccessRevoked = Boolean\(previousActive\)[\s\S]*nextActiveWorkspace === undefined[\s\S]*nextActiveWorkspace\.unlocked === false/)
  assert.match(store, /const requestSequence = \+\+_workspaceLoadSequence[\s\S]*requestSequence !== _workspaceLoadSequence/)
  assert.match(store, /previousAccessRevoked[\s\S]*state\.jobs\.filter\(job => job\.workspace && job\.workspace !== previousActive\)/)
  assert.match(store, /job\.workspace \? job\.workspace !== name : !lockedActiveWorkspace/)
  assert.match(store, /job\.workspace[\s\S]*!lockedWorkspaces\.has\(job\.workspace\)[\s\S]*!lockedActiveWorkspace/)
})

test('active account access removes project-password locks from reference entry points', async () => {
  const [sidebar, referenceLibrary] = await Promise.all([
    readFile(sidebarUrl, 'utf8'),
    readFile(referenceLibraryUrl, 'utf8'),
  ])

  assert.match(sidebar, /isAccountProjectAccessActive\(accessContext, accountProjectMigration\)/)
  assert.match(sidebar, /referenceLocked = !accountProjectAccessActive && workspaces\.some/)
  assert.match(referenceLibrary, /isAccountProjectAccessActive\(accessContext, accountProjectMigration\)/)
  assert.match(referenceLibrary, /projectExplicitlyLocked = !accountProjectAccessActive && workspaces\.some/)
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

test('active account cutover presents a non-dismissible sign-in gate before project UI', async () => {
  const [app, drawer] = await Promise.all([
    readFile(appUrl, 'utf8'),
    readFile(accountDrawerUrl, 'utf8'),
  ])

  assert.match(app, /accountAuthenticationRequired = accessContext\?\.accounts\?\.enabled === true[\s\S]*accessContext\.account_project_access_active === true[\s\S]*accountContext\?\.authenticated !== true/)
  assert.match(app, /bootstrapState === 'ready' && accountAuthenticationRequired[\s\S]*setAccountDrawerOpen\(true\)/)
  assert.match(app, /<AccountSupportDrawer required=\{accountAuthenticationRequired\} \/>/)
  assert.match(drawer, /required \? 'account' : 'support'/)
  assert.match(drawer, /if \(required\) return/)
  assert.match(drawer, /required \? 'Sign in to Maestro'/)
  assert.match(drawer, /Sign in before project names, uploads, or creative tools become available/)
  assert.match(drawer, /onClose: required \? \(\) => \{\} : closeDrawer/)
})

test('public account entry offers sign in, exact-gated signup, and recovery without owner claims', async () => {
  const [drawer, client, store, types] = await Promise.all([
    readFile(accountDrawerUrl, 'utf8'),
    readFile(clientUrl, 'utf8'),
    readFile(storeUrl, 'utf8'),
    readFile(new URL('../src/types/index.ts', import.meta.url), 'utf8'),
  ])

  assert.match(drawer, /publicRegistrationAvailable = context\?\.public_registration_available === true/)
  assert.match(drawer, /\['login', 'Sign in'\], \['register', 'Create account'\], \['recover', 'Recover'\]/)
  assert.match(drawer, /Your account starts with no projects/)
  assert.match(drawer, /const result = await registerAccount\(\{ username, password, email, deviceLabel \}\)/)
  assert.doesNotMatch(drawer, /registerAccount\(\{[^}]*role/)
  assert.match(client, /'register', '\/api\/v1\/account\/register'/)
  assert.doesNotMatch(store, /registerAccount: async/)
  assert.match(types, /public_registration_available\?: boolean/)
  assert.match(types, /\| 'register'/)
})

test('signed-out account access hides the virtual Uploads content scope', async () => {
  const main = await readFile(mainContentUrl, 'utf8')
  const selector = main.slice(main.indexOf('function WorkspaceSelector()'), main.indexOf('function stripTimeSuffix'))

  assert.match(selector, /const accountAuthenticated = accessContext\?\.accounts\?\.authenticated === true/)
  assert.match(selector, /const accountContentAvailable = !accountProjectAccessActive \|\| accountAuthenticated/)
  assert.match(selector, /if \(!accountContentAvailable\) return/)
  assert.match(selector, /disabled=\{!accountContentAvailable\}/)
  assert.match(selector, /accountContentAvailable && !requiredProject && <div[^>]*>[\s\S]*Uploads/)
  assert.match(selector, /Sign in to view projects and uploads/)
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
  assert.match(selector, /canCreateProject = accessContext\?\.account_project_creation_requires_account !== true[\s\S]*accessContext\?\.accounts\?\.authenticated === true/)
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
  assert.match(drawer, /const directLoopback = accessContext\?\.remote === false[^]*&& directLoopbackBrowser\(\)[^]*const accountProjectAccessActive = isAccountProjectAccessActiveForDrawer/)
  assert.doesNotMatch(drawer, /isAccountProjectAccessActive\([^]*?\)\s*&& directLoopbackBrowser\(\)/)
  assert.match(drawer, /Maestro will not make this change automatically/)
  assert.match(drawer, /Connect existing projects to this owner/)
  assert.match(drawer, /\{migrationOwner && !accountProjectAccessActive && \(/)
  assert.match(drawer, /projectMigration\?\.state === 'needs_attention'/)
  assert.match(drawer, /Account-based project filtering is not enabled yet/)
  assert.match(drawer, /Existing browser and project-password access stays unchanged/)
  assert.match(drawer, /refresh-project-setup[^]*projectMigrationErrorMessage/)
  assert.match(drawer, /project_migration_needs_attention/)
  const identityScrub = drawer.slice(
    drawer.indexOf('const previousIdentity = accountIdentityRef.current'),
    drawer.indexOf("if (!open || activeTab !== 'account' || !migrationAvailable || accountProjectAccessActive)"),
  )
  assert.match(identityScrub, /previousIdentity === accountIdentity[^]*clearSensitive\(\)[^]*setNotice\(null\)/)
  assert.doesNotMatch(identityScrub, /lifecycleRef\.current\.(?:closed|opened)/)
  assert.match(drawer, /if \(!open \|\| activeTab !== 'account' \|\| !migrationAvailable \|\| accountProjectAccessActive\) return/)
  const migrationSurface = drawer.slice(
    drawer.indexOf('{migrationOwner && !accountProjectAccessActive && ('),
    drawer.indexOf('{selfService && !context.reauthenticated && ('),
  )
  assert.match(migrationSurface, /Connect existing projects/)
  assert.match(migrationSurface, /migrationAvailable &&/)
  assert.match(migrationSurface, /!directLoopback \? \(/)
  assert.match(migrationSurface, /!context\.reauthenticated \? \(/)
  assert.doesNotMatch(migrationSurface, /projectMigration\?\.state === 'active'/)
  assert.doesNotMatch(drawer, /Project access is on/)
  assert.doesNotMatch(store.slice(store.indexOf('loadAccountProjectMigration:'), store.indexOf('bootstrapAccount:')), /lockAllWorkspaces|lockWorkspace/)
})

test('logout scrub clears account-bound UI without revoking independent project grants', async () => {
  const store = await readFile(storeUrl, 'utf8')
  const scrub = store.slice(store.indexOf('function _scrubAccountBoundProjectUi'), store.indexOf('function _invalidateAccountRequests'))
  const logout = store.slice(store.indexOf('logoutAccount: async'), store.indexOf('reauthenticateAccount: async'))

  for (const expected of [
    'workspaces: []', 'activeWorkspace: \'\'', 'outputs: []', 'jobs: []',
    '...BLANK_VIDEO_INPUT_PARAMS,', 'presets: []', 'loraWeights: {}',
    'spatialUpsampling: \'\'', "h3SelectedProfile: 'custom'",
    'savedParamsPerMode: {}', 'savedLoraPerMode: {}', 'savedPromptPerMode: {}',
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
  assert.match(scrub, /localStorage\.removeItem\(STORAGE_KEY\)/)
  assert.ok(logout.indexOf('_scrubAccountBoundProjectUi(get())') < logout.indexOf('await api.logoutAccount()'))
  assert.doesNotMatch(logout, /lockAllWorkspaces|lockWorkspace|revoke_workspace|project-password/)
  const deleteFlow = store.slice(
    store.indexOf('deleteWorkspace: async'),
    store.indexOf('storageDashboardOpen:', store.indexOf('deleteWorkspace: async')),
  )
  assert.match(deleteFlow, /switched_to_default[^]*presets: \[\][^]*void get\(\)\.loadPresets\(\)/)
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
