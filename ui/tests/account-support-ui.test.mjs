import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'
import {
  AccountApiError,
  acceptResponsibleUse,
  bootstrapAccount,
  changeAccountPassword,
  fetchH3LegalAccessState,
  fetchKreaOwnerPolicy,
  createServerAccount,
  createWorkspace,
  fetchAdminAccountSupport,
  fetchAccountContext,
  fetchAccountProjectMigration,
  fetchProjectAssets,
  fetchWorkspaces,
  setH3LegalAccessLocation,
  setKreaOwnerPolicy,
  fetchResponsibleUse,
  fetchSupportCatalog,
  fetchSupportSelf,
  loginAccount,
  registerAccount,
  logoutAccount,
  migrateAccountProjects,
  reauthenticateAccount,
  recordAdminAccountContribution,
  recoverAccount,
  revokeAccountSession,
  revokeAllAccountSessions,
  rotateAccountRecoveryCodes,
  setServerAccountDisabled,
  transitionAdminAccountFulfillment,
} from '../src/api/client.ts'
import { createAccountDrawerLifecycle } from '../src/components/AccountSupport/accountDrawerLifecycle.ts'
import {
  affectedPriorityNotice,
  nextAccountSupportTab,
  responsibleUseIsAccepted,
  supporterTierLabels,
  verifiedDevelopmentCostRecovery,
  visibleSupportProviders,
} from '../src/components/AccountSupport/supportPresentation.ts'

test('supporter tier labels humanize server identifiers for account and welcome copy', () => {
  assert.deepEqual(supporterTierLabels({
    one_time_tier: 'studio_supporter',
    recurring_tier: 'continuum-supporter',
  }), ['One-time Studio supporter', 'Recurring Continuum supporter'])
})

const componentUrl = new URL('../src/components/AccountSupport/AccountSupportDrawer.tsx', import.meta.url)
const supportPanelUrl = new URL('../src/components/AccountSupport/SupportPanel.tsx', import.meta.url)
const welcomeUrl = new URL('../src/components/WelcomeModal.tsx', import.meta.url)
const appUrl = new URL('../src/App.tsx', import.meta.url)
const uiRoot = new URL('..', import.meta.url).pathname

function jsonResponse(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

async function withFetchMock(handler, action) {
  const previous = globalThis.fetch
  globalThis.fetch = handler
  try {
    return await action()
  } finally {
    globalThis.fetch = previous
  }
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

test('account browser flow preserves account-owned projects and cross-user opacity across logout', async () => {
  let currentAccount = null
  let nonceSequence = 0
  const memberships = new Map([
    ['owner', new Set(['owner-project'])],
    ['member', new Set()],
  ])
  const createBodies = []

  await withFetchMock(async (url, init = {}) => {
    const path = String(url)
    const method = init.method || 'GET'
    const body = init.body ? JSON.parse(String(init.body)) : {}
    if (path.endsWith('/api/v1/account/nonce')) {
      nonceSequence += 1
      return jsonResponse({ nonce: `nonce-${nonceSequence}`, purpose: body.purpose, expires_in: 300 })
    }
    if (path.endsWith('/api/v1/account/login')) {
      currentAccount = body.username === 'Owner' ? 'owner' : 'member'
      return jsonResponse({
        account: {
          id: currentAccount, username: body.username, role: currentAccount === 'owner' ? 'owner' : 'user', disabled: false,
        },
        recovery_codes: [],
      })
    }
    if (path.endsWith('/api/v1/account/register')) {
      currentAccount = 'member'
      return jsonResponse({
        account: { id: 'member', username: body.username, role: 'user', disabled: false },
        recovery_codes: ['one-time-public-code'],
      })
    }
    if (path.endsWith('/api/v1/account/logout')) {
      currentAccount = null
      return jsonResponse({ status: 'logged_out' })
    }
    if (path.endsWith('/api/v1/workspaces') && method === 'POST') {
      if (!currentAccount) return jsonResponse({ detail: 'Authentication is required' }, 401)
      createBodies.push(body)
      memberships.get(currentAccount).add(body.name)
      return jsonResponse({ status: 'ok', name: body.name })
    }
    if (path.endsWith('/api/v1/workspaces')) {
      const names = currentAccount ? [...memberships.get(currentAccount)] : []
      return jsonResponse({
        workspaces: names.map(name => ({
          name, password_protected: false, unlocked: true,
          remember_policy: null, unlock_expires_at: null, unlock_idle_expires_at: null,
          project_role: 'owner', project_permissions: ['project.list', 'project.open'],
        })),
        active: names[0] || '',
      })
    }
    const assets = path.match(/\/api\/v1\/projects\/([^/]+)\/assets$/)
    if (assets) {
      const project = decodeURIComponent(assets[1])
      if (!currentAccount || !memberships.get(currentAccount).has(project)) {
        return jsonResponse({ detail: 'Project not found' }, 404)
      }
      return jsonResponse({ assets: [] })
    }
    throw new Error(`Unexpected browser-flow request: ${method} ${path}`)
  }, async () => {
    await loginAccount({ username: 'Owner', password: 'owner-password' })
    assert.deepEqual((await fetchWorkspaces()).workspaces.map(item => item.name), ['owner-project'])

    await logoutAccount()
    assert.deepEqual((await fetchWorkspaces()).workspaces, [])

    const registration = await registerAccount({ username: 'Member', password: 'member-password' })
    assert.equal(registration.account.role, 'user')
    assert.deepEqual(registration.recovery_codes, ['one-time-public-code'])
    assert.deepEqual((await fetchWorkspaces()).workspaces, [])
    await createWorkspace('member-project')
    assert.deepEqual(createBodies, [{ name: 'member-project' }])
    assert.deepEqual((await fetchWorkspaces()).workspaces.map(item => item.name), ['member-project'])
    await assert.rejects(fetchProjectAssets('owner-project'))

    await logoutAccount()
    await loginAccount({ username: 'Member', password: 'member-password' })
    assert.deepEqual((await fetchWorkspaces()).workspaces.map(item => item.name), ['member-project'])

    await logoutAccount()
    await loginAccount({ username: 'Owner', password: 'owner-password' })
    assert.deepEqual((await fetchWorkspaces()).workspaces.map(item => item.name), ['owner-project'])
    await assert.rejects(fetchProjectAssets('member-project'))
  })
})

test('account wrappers keep credentials in same-origin no-store bodies and bind one nonce per mutation', async () => {
  const calls = []
  await withFetchMock(async (url, init = {}) => {
    calls.push({ url: String(url), init })
    if (calls.length === 1) return jsonResponse({ nonce: 'single-use-nonce', purpose: 'bootstrap', expires_in: 300 })
    return jsonResponse({
      account: {
        id: 'account-id', username: 'Owner', role: 'owner', disabled: false,
        created_at: 1, has_email: true, passkey_credentials: 0,
        passkey_authentication_available: false,
      },
      recovery_codes: ['ONE-TIME-CODE'],
    })
  }, async () => {
    const result = await bootstrapAccount({
      username: 'Owner',
      password: 'a sufficiently long password',
      email: 'owner@example.invalid',
      deviceLabel: 'LAN browser',
    })
    assert.deepEqual(result.recovery_codes, ['ONE-TIME-CODE'])
  })

  assert.equal(calls.length, 2)
  assert.equal(calls[0].url, '/api/v1/account/nonce')
  assert.equal(calls[1].url, '/api/v1/account/bootstrap')
  for (const call of calls) {
    assert.equal(call.init.credentials, 'same-origin')
    assert.equal(call.init.cache, 'no-store')
    assert.equal(call.url.includes('Owner'), false)
    assert.equal(call.url.includes('password'), false)
    assert.equal(call.url.includes('owner@example'), false)
    assert.equal(call.url.includes('single-use-nonce'), false)
  }
  assert.deepEqual(JSON.parse(calls[0].init.body), { purpose: 'bootstrap' })
  assert.deepEqual(JSON.parse(calls[1].init.body), {
    username: 'Owner',
    password: 'a sufficiently long password',
    email: 'owner@example.invalid',
    device_label: 'LAN browser',
    nonce: 'single-use-nonce',
  })
})

test('public registration wrapper uses a register-bound nonce and cannot request a role', async () => {
  const calls = []
  await withFetchMock(async (url, init = {}) => {
    calls.push({ url: String(url), init })
    if (calls.length === 1) {
      return jsonResponse({ nonce: 'register-nonce', purpose: 'register', expires_in: 300 })
    }
    return jsonResponse({
      account: { id: 'public-user', username: 'NewUser', role: 'user', disabled: false },
      recovery_codes: ['one-time-code'],
    })
  }, async () => {
    const result = await registerAccount({
      username: 'NewUser', password: 'new-password', email: 'new@example.test', deviceLabel: 'Public browser',
    })
    assert.equal(result.account.role, 'user')
  })

  assert.deepEqual(JSON.parse(calls[0].init.body), { purpose: 'register' })
  assert.equal(calls[1].url, '/api/v1/account/register')
  assert.deepEqual(JSON.parse(calls[1].init.body), {
    username: 'NewUser', password: 'new-password', email: 'new@example.test',
    device_label: 'Public browser', nonce: 'register-nonce',
  })
  assert.equal('role' in JSON.parse(calls[1].init.body), false)
})

test('recovery and path-bound session revocation use exact purpose and encoded routes', async () => {
  const calls = []
  await withFetchMock(async (url, init = {}) => {
    calls.push({ url: String(url), init })
    if (String(url).endsWith('/nonce')) {
      const purpose = JSON.parse(init.body).purpose
      return jsonResponse({ nonce: `${purpose}-nonce`, purpose, expires_in: 300 })
    }
    if (String(url).endsWith('/recover')) {
      return jsonResponse({
        account: {
          id: 'a', username: 'Owner', role: 'owner', disabled: false,
          created_at: 1, has_email: false, passkey_credentials: 0,
          passkey_authentication_available: false,
        },
        recovery_codes: ['replacement'],
      })
    }
    return jsonResponse({ revoked: true, current: false })
  }, async () => {
    await recoverAccount({
      username: 'Owner', recoveryCode: 'OLD-CODE', newPassword: 'new sufficiently long password',
    })
    await revokeAccountSession('handle/with spaces')
  })

  assert.equal(JSON.parse(calls[0].init.body).purpose, 'recover')
  assert.equal(calls[1].url, '/api/v1/account/recover')
  assert.equal(JSON.parse(calls[2].init.body).purpose, 'revoke_session')
  assert.equal(calls[3].url, '/api/v1/account/sessions/handle%2Fwith%20spaces')
  assert.equal(calls[3].init.method, 'DELETE')
})

test('every remaining account mutation matches its exact nonce, method, and route contract', async () => {
  const calls = []
  await withFetchMock(async (url, init = {}) => {
    calls.push({ url: String(url), init })
    if (String(url).endsWith('/nonce')) {
      const purpose = JSON.parse(init.body).purpose
      return jsonResponse({ nonce: `${purpose}-nonce`, purpose, expires_in: 300 })
    }
    if (String(url).endsWith('/login')) return jsonResponse({ account: { username: 'Owner' } })
    if (String(url).endsWith('/logout')) return jsonResponse({ status: 'logged_out' })
    if (String(url).endsWith('/reauth')) return jsonResponse({ account: { username: 'Owner' }, reauthenticated_until: 10 })
    if (String(url).endsWith('/password')) return jsonResponse({ status: 'password_changed', other_sessions_revoked: true })
    if (String(url).endsWith('/recovery-codes')) return jsonResponse({ recovery_codes: ['code'] })
    if (String(url).endsWith('/revoke-all')) return jsonResponse({ revoked: 2, current_revoked: false })
    if (String(url).endsWith('/users')) return jsonResponse({ account: { username: 'Creator' }, recovery_codes: ['code'] })
    return jsonResponse({ status: 'updated' })
  }, async () => {
    await loginAccount({ username: 'Owner', password: 'long enough password' })
    await logoutAccount()
    await reauthenticateAccount('long enough password')
    await changeAccountPassword('a replacement long password')
    await rotateAccountRecoveryCodes()
    await revokeAllAccountSessions(true)
    await createServerAccount({ username: 'Creator', password: 'temporary long password' })
    await setServerAccountDisabled('account/id', true)
  })

  const mutations = calls.filter(call => !call.url.endsWith('/nonce'))
  assert.deepEqual(mutations.map(call => ({
    purpose: JSON.parse(call.init.body).nonce.replace(/-nonce$/, ''),
    method: call.init.method,
    url: call.url,
  })), [
    { purpose: 'login', method: 'POST', url: '/api/v1/account/login' },
    { purpose: 'revoke_session', method: 'POST', url: '/api/v1/account/logout' },
    { purpose: 'reauth', method: 'POST', url: '/api/v1/account/reauth' },
    { purpose: 'change_password', method: 'PUT', url: '/api/v1/account/password' },
    { purpose: 'rotate_recovery_codes', method: 'POST', url: '/api/v1/account/recovery-codes' },
    { purpose: 'revoke_all_sessions', method: 'POST', url: '/api/v1/account/sessions/revoke-all' },
    { purpose: 'create_account', method: 'POST', url: '/api/v1/account/users' },
    { purpose: 'disable_account', method: 'PUT', url: '/api/v1/account/users/account%2Fid' },
  ])
  assert.equal(JSON.parse(mutations[5].init.body).retain_current, true)
  assert.equal(JSON.parse(mutations[7].init.body).disabled, true)
})

test('closing the account drawer permanently invalidates in-flight secret display leases', async () => {
  const lifecycle = createAccountDrawerLifecycle()
  lifecycle.opened()
  const firstRequest = lifecycle.operationLease()
  assert.equal(firstRequest(), true)

  lifecycle.closed()
  await Promise.resolve()
  assert.equal(firstRequest(), false)

  lifecycle.opened()
  assert.equal(firstRequest(), false, 'reopening must not revive the old response')
  assert.equal(lifecycle.operationLease()(), true)
})

test('a successful sign-in hydrates current sessions and owner users without reopening the drawer', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  globalThis.localStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  const calls = []
  const account = {
    id: 'owner-id', username: 'Owner', role: 'owner', disabled: false,
    created_at: 1, has_email: false, passkey_credentials: 0,
    passkey_authentication_available: false,
  }
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    calls.push(url)
    if (url.endsWith('/account/nonce')) return jsonResponse({ nonce: 'login-nonce', purpose: 'login', expires_in: 300 })
    if (url.endsWith('/account/login')) return jsonResponse({ account })
    if (url.endsWith('/account/context')) return jsonResponse({
      enabled: true, authenticated: true, account,
      capabilities: ['account.self', 'accounts.admin', 'services.admin'],
      reauthenticated: true,
      passkey_authentication_available: false,
      bootstrap_available: false,
    })
    if (url.endsWith('/account/sessions')) return jsonResponse({ sessions: [{
      id: 'current-handle', device_label: 'Browser', remote_created: false,
      created_at: 1, last_seen_at: 2, expires_at: 3, current: true,
    }] })
    if (url.endsWith('/account/users')) return jsonResponse({ accounts: [account] })
    if (url.endsWith('/workspaces')) return jsonResponse({ workspaces: [{ name: 'owned-project', project_role: 'owner', project_permissions: ['project.read'] }], active: 'owned-project' })
    if (url.endsWith('/presets?workspace=owned-project')) return jsonResponse({ presets: [] })
    if (url.endsWith('/defaults/minimax_h3')) return jsonResponse({
      h3_default_profile_id: 'high', num_inference_steps: 28,
      resolution: '1344x768', custom_settings: { h3_attention_engine: 'sol_attn' },
      tea_cache: 0,
    })
    if (url.endsWith('/model-options/minimax_h3')) return jsonResponse({})
    throw new Error(`Unexpected account request: ${url} ${init.method || 'GET'}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
  })

  const bundled = await build({
    stdin: {
      contents: "export { useStore } from './src/stores/useStore.ts'",
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
  const { useStore } = await import(asDataModule(bundled.outputFiles[0].text))
  const anonymous = {
    enabled: true, authenticated: false, account: null, capabilities: [],
    reauthenticated: false, passkey_authentication_available: false,
  }
  useStore.setState({
    accessContext: { accounts: anonymous },
    accountContext: anonymous,
  })
  await useStore.getState().loginAccount({ username: 'Owner', password: 'long enough password' })

  assert.deepEqual(calls.slice(-8), [
    '/api/v1/account/login',
    '/api/v1/account/context',
    '/api/v1/workspaces',
    '/api/v1/presets?workspace=owned-project',
    '/api/v1/defaults/minimax_h3',
    '/api/v1/model-options/minimax_h3',
    '/api/v1/account/sessions',
    '/api/v1/account/users',
  ])
  assert.equal(useStore.getState().accountSessions[0].id, 'current-handle')
  assert.equal(useStore.getState().accountUsers[0].username, 'Owner')
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(useStore.getState().h3SelectedProfile, 'high')
  assert.equal(useStore.getState().params.num_inference_steps, 28)
})

test('account loaders ignore reverse-order and post-logout responses from a stale identity', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  globalThis.localStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })

  const accountA = {
    id: 'account-a', username: 'Account A', role: 'owner', disabled: false,
    created_at: 1, has_email: false, passkey_credentials: 0,
    passkey_authentication_available: false,
  }
  const accountB = { ...accountA, id: 'account-b', username: 'Account B' }
  const context = account => ({
    enabled: true, authenticated: Boolean(account), account,
    capabilities: account ? ['account.self', 'accounts.admin', 'services.admin'] : [],
    reauthenticated: Boolean(account), passkey_authentication_available: false,
    bootstrap_available: false,
  })
  const contextRequests = []
  const sessionRequests = []
  const userRequests = []
  const accessRequests = []
  let workspaceRequests = 0
  let logoutContext = false
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.endsWith('/access-context')) {
      const request = deferred()
      accessRequests.push(request)
      return request.promise
    }
    if (url.endsWith('/account/context')) {
      if (logoutContext) return jsonResponse(context(null))
      const request = deferred()
      contextRequests.push(request)
      return request.promise
    }
    if (url.endsWith('/account/sessions')) {
      const request = deferred()
      sessionRequests.push(request)
      return request.promise
    }
    if (url.endsWith('/account/users')) {
      const request = deferred()
      userRequests.push(request)
      return request.promise
    }
    if (url.endsWith('/workspaces')) {
      workspaceRequests += 1
      return jsonResponse(workspaceRequests === 2 ? {
        workspaces: [{ name: 'project-b', project_permissions: ['project.read'] }],
        active: 'project-b',
      } : { workspaces: [], active: '' })
    }
    if (url.endsWith('/presets?workspace=project-b')) return jsonResponse({ presets: [] })
    if (url.endsWith('/defaults/minimax_h3')) return jsonResponse({})
    if (url.endsWith('/model-options/minimax_h3')) return jsonResponse({})
    if (url.endsWith('/account/nonce')) {
      return jsonResponse({ nonce: 'logout-nonce', purpose: 'revoke_session', expires_in: 300 })
    }
    if (url.endsWith('/account/logout') && init.method === 'POST') {
      logoutContext = true
      return jsonResponse({ status: 'logged_out' })
    }
    throw new Error(`Unexpected account request: ${url} ${init.method || 'GET'}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
  })

  const bundled = await build({
    stdin: {
      contents: "export { useStore } from './src/stores/useStore.ts'",
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
  const { useStore } = await import(`${asDataModule(bundled.outputFiles[0].text)}#account-fencing`)

  useStore.setState({ accessContext: null, accountContext: null })
  const initialAccess = useStore.getState().loadAccessContext()
  await useStore.getState().loadAccountContext()
  accessRequests[0].resolve(jsonResponse({
    remote: false,
    project_password_required: false,
    project_names_visible: true,
    machine_controls: true,
    custom_model_sources: true,
    catalog_model_downloads: true,
    classic_ui: false,
    cloudflare_enabled: false,
    share_url: '',
    share_flow: '',
    accounts: context(accountA),
  }))
  await initialAccess
  assert.equal(useStore.getState().accountContext.account.id, accountA.id)

  const staleAccess = useStore.getState().loadAccessContext()
  const newerAccountContext = useStore.getState().loadAccountContext()
  contextRequests[0].resolve(jsonResponse(context(accountB)))
  await newerAccountContext
  assert.equal(workspaceRequests, 2, 'a passive account identity change refreshes project visibility')
  assert.equal(useStore.getState().activeWorkspace, 'project-b')
  accessRequests[1].resolve(jsonResponse({
    remote: false,
    project_password_required: false,
    project_names_visible: true,
    machine_controls: true,
    custom_model_sources: true,
    catalog_model_downloads: true,
    classic_ui: false,
    cloudflare_enabled: false,
    share_url: '',
    share_flow: '',
    accounts: context(accountA),
  }))
  await staleAccess
  assert.equal(useStore.getState().accountContext.account.id, accountB.id)
  assert.equal(useStore.getState().accessContext.accounts.account.id, accountB.id)
  contextRequests.length = 0

  useStore.setState({
    accessContext: { accounts: context(accountA) },
    accountContext: context(accountA),
  })

  const firstContext = useStore.getState().loadAccountContext()
  const secondContext = useStore.getState().loadAccountContext()
  contextRequests[1].resolve(jsonResponse(context(accountB)))
  await secondContext
  contextRequests[0].resolve(jsonResponse(context(accountA)))
  await firstContext
  assert.equal(useStore.getState().accountContext.account.id, accountB.id)

  useStore.setState({ accountContext: context(accountA) })
  const firstSessions = useStore.getState().loadAccountSessions()
  const secondSessions = useStore.getState().loadAccountSessions()
  sessionRequests[1].resolve(jsonResponse({ sessions: [{ id: 'new-session' }] }))
  await secondSessions
  sessionRequests[0].resolve(jsonResponse({ sessions: [{ id: 'old-session' }] }))
  await firstSessions
  assert.equal(useStore.getState().accountSessions[0].id, 'new-session')

  const staleSessions = useStore.getState().loadAccountSessions()
  const staleUsers = useStore.getState().loadAccountUsers()
  await useStore.getState().logoutAccount()
  sessionRequests[2].resolve(jsonResponse({ sessions: [{ id: 'stale-session' }] }))
  userRequests[0].resolve(jsonResponse({ accounts: [accountA] }))
  await Promise.all([staleSessions, staleUsers])
  assert.equal(useStore.getState().accountContext.account, null)
  assert.deepEqual(useStore.getState().accountSessions, [])
  assert.deepEqual(useStore.getState().accountUsers, [])
  assert.equal(useStore.getState().accountDetailsLoading, false)
})

test('superseded bootstrap and recovery responses never return one-time recovery codes', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalSessionStorage = globalThis.sessionStorage
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: 'localhost' },
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()

  const account = (id, username) => ({
    id, username, role: 'owner', disabled: false, created_at: 1,
    has_email: false, passkey_credentials: 0, passkey_authentication_available: false,
  })
  const accountA = account('account-a', 'Account A')
  const accountB = account('account-b', 'Account B')
  const context = current => ({
    enabled: true, authenticated: Boolean(current), account: current,
    capabilities: current ? ['account.self', 'accounts.admin', 'services.admin', 'owner.admin'] : [],
    reauthenticated: Boolean(current), passkey_authentication_available: false,
    activation_state: current ? 'ready' : 'setup_available', bootstrap_available: !current,
  })
  const bootstrapResponse = deferred()
  const recoveryResponse = deferred()
  let visibleAccount = accountB
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.endsWith('/account/nonce')) {
      const purpose = JSON.parse(init.body).purpose
      return jsonResponse({ nonce: `${purpose}-nonce`, purpose, expires_in: 300 })
    }
    if (url.endsWith('/account/bootstrap')) return bootstrapResponse.promise
    if (url.endsWith('/account/recover')) return recoveryResponse.promise
    if (url.endsWith('/access-context')) return jsonResponse({ remote: false, accounts: context(visibleAccount) })
    throw new Error(`Unexpected stale-secret request: ${url}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    globalThis.sessionStorage = originalSessionStorage
  })

  const bundled = await build({
    stdin: {
      contents: "export { useStore } from './src/stores/useStore.ts'",
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
  const { useStore } = await import(`${asDataModule(bundled.outputFiles[0].text)}#stale-secret-results`)
  const anonymous = context(null)
  useStore.setState({ accessContext: { remote: false, accounts: anonymous }, accountContext: anonymous })

  const staleBootstrap = useStore.getState().bootstrapAccount({
    username: 'Account A', password: 'long enough password', deviceLabel: 'Browser',
  })
  await Promise.resolve()
  await useStore.getState().loadAccessContext(false)
  bootstrapResponse.resolve(jsonResponse({ account: accountA, recovery_codes: ['BOOTSTRAP-SECRET'] }))
  assert.equal(await staleBootstrap, null)

  const staleRecovery = useStore.getState().recoverAccount({
    username: 'Account B', recoveryCode: 'old recovery secret',
    newPassword: 'replacement long password', deviceLabel: 'Browser',
  })
  await Promise.resolve()
  visibleAccount = accountA
  await useStore.getState().loadAccessContext(false)
  recoveryResponse.resolve(jsonResponse({ account: accountB, recovery_codes: ['RECOVERY-SECRET'] }))
  assert.equal(await staleRecovery, null)
})

test('same-account capability loss cancels deferred recovery-code and user-creation secrets', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: 'localhost' },
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.localStorage = new StorageFake()
  const owner = {
    id: 'owner', username: 'Owner', role: 'owner', disabled: false, created_at: 1,
    has_email: false, passkey_credentials: 0, passkey_authentication_available: false,
  }
  const context = (reauthenticated, capabilities = ['account.self', 'accounts.admin', 'services.admin']) => ({
    enabled: true, authenticated: true, account: owner, capabilities, reauthenticated,
    passkey_authentication_available: false, activation_state: 'ready', bootstrap_available: false,
  })
  const rotateResponse = deferred()
  const createResponse = deferred()
  let currentContext = context(true)
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    if (url.endsWith('/account/nonce')) {
      const purpose = JSON.parse(init.body).purpose
      return jsonResponse({ nonce: `${purpose}-nonce`, purpose, expires_in: 300 })
    }
    if (url.endsWith('/account/recovery-codes')) return rotateResponse.promise
    if (url.endsWith('/account/users') && init.method === 'POST') return createResponse.promise
    if (url.endsWith('/account/context')) return jsonResponse(currentContext)
    throw new Error(`Unexpected authorization-race request: ${url}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
  })

  const bundled = await build({
    stdin: {
      contents: "export { useStore } from './src/stores/useStore.ts'",
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
  const { useStore } = await import(`${asDataModule(bundled.outputFiles[0].text)}#secret-authorization-lease`)
  useStore.setState({ accountContext: currentContext })

  const staleRotation = useStore.getState().rotateAccountRecoveryCodes()
  currentContext = context(false)
  await useStore.getState().loadAccountContext(false)
  rotateResponse.resolve(jsonResponse({ recovery_codes: ['ROTATED-SECRET'] }))
  assert.equal(await staleRotation, null)

  currentContext = context(true)
  useStore.setState({ accountContext: currentContext })
  const staleCreation = useStore.getState().createServerAccount({
    username: 'New user', password: 'temporary long password',
  })
  currentContext = context(true, ['account.self'])
  await useStore.getState().loadAccountContext(false)
  createResponse.resolve(jsonResponse({ account: { ...owner, id: 'new-user' }, recovery_codes: ['CREATED-SECRET'] }))
  assert.equal(await staleCreation, null)
})

test('account identity fences reverse-order projects and logout scrubs before preserving grant endpoints', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  globalThis.localStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
    location: { hostname: 'localhost' },
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })

  const accountA = {
    id: 'account-a', username: 'Account A', role: 'owner', disabled: false,
    created_at: 1, has_email: false, passkey_credentials: 0,
    passkey_authentication_available: false,
  }
  const accountB = { ...accountA, id: 'account-b', username: 'Account B' }
  const accountContext = account => ({
    enabled: true, authenticated: Boolean(account), account,
    capabilities: account ? ['account.self', 'accounts.admin', 'services.admin', 'owner.admin'] : [],
    reauthenticated: Boolean(account), passkey_authentication_available: false,
    activation_state: 'ready', bootstrap_available: false,
  })
  const staleProjects = deferred()
  const logoutResponse = deferred()
  const calls = []
  let workspaceCalls = 0
  let loggedOut = false
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    calls.push(url)
    if (url.endsWith('/workspaces')) {
      workspaceCalls += 1
      if (workspaceCalls === 1) return staleProjects.promise
      return jsonResponse(loggedOut ? { workspaces: [], active: '' } : {
        workspaces: [{
          name: 'project-b', project_role: 'owner',
          project_permissions: ['project.read', 'project.generate', 'project.lifecycle', 'project.delete'],
        }],
        active: 'project-b',
      })
    }
    if (url.endsWith('/account/nonce')) {
      const purpose = JSON.parse(init.body).purpose
      return jsonResponse({ nonce: `${purpose}-nonce`, purpose, expires_in: 300 })
    }
    if (url.endsWith('/account/login')) return jsonResponse({ account: accountB })
    if (url.endsWith('/account/logout')) return logoutResponse.promise
    if (url.endsWith('/account/context')) return jsonResponse(accountContext(loggedOut ? null : accountB))
    if (url.endsWith('/account/sessions')) return jsonResponse({ sessions: [] })
    if (url.endsWith('/account/users')) return jsonResponse({ accounts: [accountB] })
    if (url.endsWith('/presets?workspace=project-b')) return jsonResponse({ presets: [] })
    if (url.endsWith('/defaults/minimax_h3')) return jsonResponse({})
    if (url.endsWith('/model-options/minimax_h3')) return jsonResponse({})
    throw new Error(`Unexpected account/project request: ${url}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
  })

  const bundled = await build({
    stdin: {
      contents: "export { useStore } from './src/stores/useStore.ts'",
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
  const { useStore } = await import(`${asDataModule(bundled.outputFiles[0].text)}#project-identity-race`)
  useStore.setState({
    accessContext: { remote: false, accounts: accountContext(accountA) },
    accountContext: accountContext(accountA),
    workspaces: [{ name: 'project-a', project_permissions: ['project.read'] }],
    activeWorkspace: 'project-a',
    outputs: [{ name: 'old-output', workspace: 'project-a' }],
    outputsTotal: 1,
    jobs: [{ id: 'old-job', status: 'running', workspace: 'project-a' }],
    isGenerating: true,
  })

  const staleLoad = useStore.getState().loadWorkspaces()
  await useStore.getState().loginAccount({ username: 'Account B', password: 'long enough password' })
  assert.equal(useStore.getState().activeWorkspace, 'project-b')
  staleProjects.resolve(jsonResponse({
    workspaces: [{ name: 'project-a', project_permissions: ['project.read'] }],
    active: 'project-a',
  }))
  assert.equal(await staleLoad, false)
  assert.equal(useStore.getState().activeWorkspace, 'project-b')

  useStore.setState({
    outputs: [{ name: 'private-output', workspace: 'project-b' }],
    outputsTotal: 1,
    jobs: [{ id: 'private-job', status: 'running', workspace: 'project-b' }],
    isGenerating: true,
    toolsSourcePath: '/private/project-b/source.mp4',
    toolsSourceName: 'source.mp4',
    toolsSourceUrl: '/api/private/project-b/source.mp4',
    toolsRevoiceRefs: [{ filename: 'voice.wav', path: '/private/project-b/voice.wav' }, null],
    directorAudioFile: { name: 'private.wav' },
    directorAudioPath: '/private/project-b/audio.wav',
    directorReferenceImage: { name: 'private.png' },
    directorReferenceImagePath: '/private/project-b/reference.png',
    directorClipImages: [{ clipIndex: 0, filename: 'private.png' }],
    shortFilmPath: { kind: 'uploaded_story', value: '/private/project-b/story.txt' },
    startImage: { name: 'director-start.png' },
    endImage: { name: 'director-end.png' },
    continueVideo: { name: 'director-continue.mp4' },
    continueVideoPath: '/private/project-b/director-continue.mp4',
    continueVideoUrl: '/api/private/project-b/director-continue.mp4',
    audioGuideFilename: 'director-audio.wav',
    imageRefs: [{ name: 'director-reference.png' }],
    clips: [{ prompt: 'private director prompt', startImagePath: '/private/project-b/clip.png' }],
  })
  const logout = useStore.getState().logoutAccount()
  assert.deepEqual(useStore.getState().workspaces, [])
  assert.deepEqual(useStore.getState().outputs, [])
  assert.deepEqual(useStore.getState().jobs, [])
  assert.equal(useStore.getState().toolsSourcePath, null)
  assert.deepEqual(useStore.getState().toolsRevoiceRefs, [null, null])
  assert.equal(useStore.getState().directorAudioPath, null)
  assert.equal(useStore.getState().directorReferenceImagePath, null)
  assert.deepEqual(useStore.getState().directorClipImages, [])
  assert.equal(useStore.getState().shortFilmPath, null)
  assert.equal(useStore.getState().startImage, null)
  assert.equal(useStore.getState().endImage, null)
  assert.equal(useStore.getState().continueVideoPath, '')
  assert.equal(useStore.getState().audioGuideFilename, null)
  assert.deepEqual(useStore.getState().imageRefs, [])
  assert.deepEqual(useStore.getState().clips, [])
  loggedOut = true
  logoutResponse.resolve(jsonResponse({ status: 'logged_out' }))
  await logout
  assert.equal(useStore.getState().accountContext.account, null)
  assert.equal(calls.some(url => url.includes('/workspaces/lock')), false)
  assert.equal(calls.some(url => url.includes('/workspaces/lock-all')), false)
  const migrationCallsBefore = calls.filter(url => url.includes('/account/projects/migration')).length
  const disabled = { ...accountContext(null), enabled: false, activation_state: 'disabled' }
  useStore.setState({ accessContext: { remote: false, accounts: disabled }, accountContext: disabled })
  assert.equal(await useStore.getState().loadAccountProjectMigration(), null)
  assert.equal(
    calls.filter(url => url.includes('/account/projects/migration')).length,
    migrationCallsBefore,
  )
})

test('account context is explicitly no-store and structured server errors stay bounded', async () => {
  const calls = []
  await withFetchMock(async (url, init = {}) => {
    calls.push({ url: String(url), init })
    return jsonResponse({
      enabled: true,
      authenticated: false,
      account: null,
      capabilities: [],
      reauthenticated: false,
      passkey_authentication_available: false,
      activation_state: 'setup_available',
      bootstrap_available: true,
    })
  }, async () => {
    const context = await fetchAccountContext()
    assert.equal(context.activation_state, 'setup_available')
    assert.equal(context.bootstrap_available, true)
  })
  assert.equal(calls[0].init.credentials, 'same-origin')
  assert.equal(calls[0].init.cache, 'no-store')

  await withFetchMock(async () => jsonResponse({
    detail: { code: 'reauth_required', message: 'Recent password confirmation is required.' },
  }, 403, { 'Retry-After': '9' }), async () => {
    await assert.rejects(fetchAccountContext(), error => {
      assert.ok(error instanceof AccountApiError)
      assert.equal(error.code, 'reauth_required')
      assert.equal(error.status, 403)
      assert.equal(error.retryAfter, 9)
      return true
    })
  })
})

test('project setup status and migration use the exact explicit no-store account route', async () => {
  const calls = []
  await withFetchMock(async (url, init = {}) => {
    calls.push({ url: String(url), init })
    return jsonResponse({
      state: calls.length === 1 ? 'not_started' : 'active',
      enforced: calls.length > 1,
      project_count: calls.length > 1 ? 3 : 0,
      needs_attention: 0,
    })
  }, async () => {
    assert.equal((await fetchAccountProjectMigration()).state, 'not_started')
    assert.equal((await migrateAccountProjects()).state, 'active')
  })

  assert.deepEqual(calls.map(call => ({
    url: call.url,
    method: call.init.method || 'GET',
    credentials: call.init.credentials,
    cache: call.init.cache,
  })), [
    { url: '/api/v1/account/projects/migration', method: 'GET', credentials: 'same-origin', cache: 'no-store' },
    { url: '/api/v1/account/projects/migration', method: 'POST', credentials: 'same-origin', cache: 'no-store' },
  ])
})

const publicSupport = {
  schema_version: 1,
  provider_catalog: {
    schema_version: 1,
    provider_neutral: true,
    providers: [{
      provider_id: 'buy_me_a_coffee', display_name: 'Buy Me a Coffee', funding_modes: ['one_time', 'recurring'],
      description: 'One-time or recurring support.', enabled: true, configured: true,
      state: 'available', support_url: 'https://buymeacoffee.com/maestro',
    }, {
      provider_id: 'patreon', display_name: 'Patreon', funding_modes: ['recurring'],
      description: 'Recurring support.', enabled: true, configured: true,
      state: 'available', support_url: 'https://www.patreon.com/maestro',
    }, {
      provider_id: 'direct_compute_sponsorship', display_name: 'Direct compute sponsorship', funding_modes: ['one_time'],
      description: 'Sponsor Continuum compute directly.', enabled: false, configured: false,
      state: 'locked', support_url: null,
    }],
  },
  supporter_benefits: {
    schema_version: 1,
    currency: 'USD',
    credit_unit: 'maestro_credits',
    promotional_credits_enabled: true,
    one_time_bonus_cap: 1_000,
    one_time_validity_seconds: 90 * 24 * 60 * 60,
    recurring_validity_seconds: 35 * 24 * 60 * 60,
    one_time_tiers: [{
      tier: 'supporter', minimum_minor: 500, promotional_maestro_credits: 25,
      benefits: ['supporter_recognition', 'bounded_queue_priority', 'early_access_updates'],
    }, {
      tier: 'backer', minimum_minor: 2_500, promotional_maestro_credits: 150,
      benefits: ['supporter_recognition', 'bounded_queue_priority', 'early_access_updates', 'supporter_convenience'],
    }, {
      tier: 'sponsor', minimum_minor: 10_000, promotional_maestro_credits: 500,
      benefits: ['supporter_recognition', 'bounded_queue_priority', 'early_access_updates', 'supporter_convenience'],
    }],
    recurring_tiers: [{
      tier: 'member', minimum_minor: 300, promotional_maestro_credits: 25,
      benefits: ['supporter_recognition', 'bounded_queue_priority', 'early_access_updates'],
    }, {
      tier: 'sustainer', minimum_minor: 1_000, promotional_maestro_credits: 100,
      benefits: ['supporter_recognition', 'bounded_queue_priority', 'early_access_updates', 'supporter_convenience'],
    }, {
      tier: 'patron', minimum_minor: 2_500, promotional_maestro_credits: 250,
      benefits: ['supporter_recognition', 'bounded_queue_priority', 'early_access_updates', 'supporter_convenience'],
    }],
    terms: {
      cash_value: false, transferable: false, refundable: false,
      guaranteed_compute: false, guaranteed_service: false,
      unused_bonus_may_expire_or_be_revoked: true,
    },
    notice: 'Support is optional. Jobs remain schedulable without credits.',
  },
  benefit_availability: {
    scheduler_enforcement_enabled: false, effective_benefits: [], state: 'recorded_not_enforced',
  },
  development_cost_recovery: {
    target_minor: 100_000, currency: 'USD', state: 'locked',
  },
  support_priority: {
    scheduler_enforcement_enabled: false, effective_priority_boost: false, state: 'not_enabled',
    exclusions: [], notice: 'Submission remains available.',
  },
}

const responsibleUse = {
  notice: {
    document_id: 'maestro_responsible_use', version: 1, content_sha256: 'd'.repeat(64),
    digest_algorithm: 'sha256', title: 'Responsible use', paragraphs: ['Use Maestro lawfully.'],
  },
  status: {
    document_id: 'maestro_responsible_use', document_version: 1,
    content_sha256: 'd'.repeat(64), accepted: false, accepted_at: null, state: 'not_accepted',
  },
}

const recordedAllowance = {
  state: 'recorded_not_enforced',
  enforcement_enabled: false,
  unit: 'maestro_credits',
  as_of: '2026-08-11T09:00:00Z',
  effective_allowance: 460,
  sources: [
    {
      source: 'free', granted_allowance: 10, effective_allowance: 10,
      expires_at: null, status: 'active', refund_state: 'not_applicable',
    },
    {
      source: 'one_time_support', granted_allowance: 300, effective_allowance: 100,
      expires_at: '2026-08-11T10:00:00Z', status: 'active', refund_state: 'partial',
    },
    {
      source: 'recurring_support', granted_allowance: 350, effective_allowance: 350,
      expires_at: '2026-08-11T09:30:00Z', status: 'active', refund_state: 'none',
    },
  ],
}

const h3LegalAccessProjection = {
  declared: true,
  territory_code: 'US',
  availability_status: 'available',
  execution_allowed: true,
  license_revision: 'r12.3',
  license_sha256: '5f'.repeat(32),
  license_url: 'https://licenses.maestro/mini-max-h3-us.txt',
  location_source: 'manual_owner_declaration',
  network_location_used: false,
  written_authorization_supported: false,
}

const kreaOwnerPolicyProjection = {
  attested: true,
  availability_status: 'license_conditions_recorded',
  migration_required: false,
  local_execution_allowed: true,
  hosted_execution_allowed: false,
  maestro_content_filtering: false,
  manual_owner_review: true,
  role_use_scopes: { owner: 'noncommercial', user: 'commercial_under_1m' },
  declared_at_unix: 1_700_000_000,
  license_version: 'v1',
  license_date: '2026-06-22',
  license_url: 'https://www.krea.ai/krea-2-licensing',
  acceptable_use_url: 'https://www.krea.ai/krea-2-use-policy',
  content_handling: 'manual_owner_review',
}

const opaqueSupportKey = value => `key_${value.repeat(64)}`
const supportEventId = value => `evt_${value.repeat(32)}`

test('Support wrappers use exact no-store envelopes and discard private contribution fields', async () => {
  const calls = []
  await withFetchMock(async (url, init = {}) => {
    calls.push({ url: String(url), init })
    if (String(url).endsWith('/support/catalog')) return jsonResponse(publicSupport)
    if (String(url).endsWith('/support/self')) return jsonResponse({
      ...publicSupport,
      account_support: {
        recorded: {
          event_count: 2, one_time_tier: 'backer', recurring_tier: null,
          active_recurring_count: 0, currency_totals_minor: { USD: 2500 },
          subject_key: 'private-subject', audit: [{ amount_minor: 2500 }],
          recorded_allowance: {
            ...recordedAllowance,
            account_id: 'private-allowance-account',
            amount_minor: 2500,
            currency_totals_minor: { USD: 2500 },
            audit: [{ provider: 'private-allowance-provider' }],
            sources: recordedAllowance.sources.map((source, index) => ({
              ...source,
              source_event_id: `private-source-event-${index}`,
              provider: 'private-source-provider',
              amount_minor: 2500,
              account_id: 'private-source-account',
            })),
          },
        },
        benefits: {
          state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
          effective_benefits: [], recorded_eligibility: ['supporter_recognition', 'bounded_queue_priority', 'early_access_updates', 'supporter_convenience'],
        },
      },
      responsible_use: responsibleUse,
    })
    if (String(url).endsWith('/support/responsible-use')) return jsonResponse(responsibleUse)
    if (String(url).endsWith('/support/responsible-use/accept')) {
      return jsonResponse({ status: { ...responsibleUse.status, accepted: true, state: 'accepted' } })
    }
    if (String(url).endsWith('/h3/legal-access')) {
      if ((init.method || 'GET') === 'PUT') {
        const body = JSON.parse(String(init.body))
        return jsonResponse({
          ...h3LegalAccessProjection,
          territory_code: body.territory_code,
          availability_status: 'available',
        })
      }
      return jsonResponse(h3LegalAccessProjection)
    }
    if (String(url).includes('/support/admin/accounts/')) return jsonResponse({
      account_support: {
        recorded: {
          event_count: 4, active_recurring_count: 0,
          currency_totals_minor: { USD: 2500, unsafe_currency: 9999 },
          subject_key: opaqueSupportKey('f'),
          unresolved: [{
            event_id: supportEventId('b'), reason: 'unresolved_or_mismatched_adjustment',
            email: 'private@example.test',
          }, { event_id: 'raw-event-id', reason: 'private_reason' }],
          fulfillment: [{
            target_event_id: supportEventId('a'), item: 'one_time_credit_grant', status: 'complete',
            audit_event_id: supportEventId('c'), actor_key: opaqueSupportKey('d'),
            proof_reference: opaqueSupportKey('c'), changed_at: '2026-08-11T09:10:00Z',
            notes: 'private fulfillment note',
          }],
          audit: [{
            sequence: 1, event_id: supportEventId('a'), provider: 'github_sponsors',
            source_event_key: opaqueSupportKey('a'), kind: 'one_time_contribution',
            occurred_at: '2026-08-11T09:00:00Z', received_at: '2026-08-11T09:00:01Z',
            amount_minor: 2500, currency: 'USD', contract_key: null, related_event_key: null,
            fulfillment_item: null, fulfillment_status: null, actor_key: null,
            account_id: 'private-account', email: 'private@example.test', invoice: 'private-invoice',
          }, {
            sequence: 2, event_id: supportEventId('c'), provider: 'github_sponsors',
            source_event_key: opaqueSupportKey('e'), kind: 'fulfillment_set',
            occurred_at: '2026-08-11T09:10:00Z', received_at: '2026-08-11T09:10:01Z',
            amount_minor: 0, currency: 'USD', contract_key: opaqueSupportKey('c'),
            related_event_key: opaqueSupportKey('a'), fulfillment_item: 'one_time_credit_grant',
            fulfillment_status: 'complete', actor_key: opaqueSupportKey('d'),
          }, {
            sequence: 3, event_id: supportEventId('d'), provider: 'github_sponsors',
            source_event_key: opaqueSupportKey('f'), kind: 'refund',
            occurred_at: '2026-08-11T09:20:00Z', received_at: '2026-08-11T09:20:01Z',
            amount_minor: 500, currency: 'USD', contract_key: opaqueSupportKey('c'),
            related_event_key: opaqueSupportKey('a'), fulfillment_item: null,
            fulfillment_status: null, actor_key: null,
          }, {
            sequence: 4, event_id: supportEventId('e'), provider: 'github_sponsors',
            source_event_key: opaqueSupportKey('0'), kind: 'recurring_canceled',
            occurred_at: '2026-08-11T09:30:00Z', received_at: '2026-08-11T09:30:01Z',
            amount_minor: 0, currency: 'USD', contract_key: opaqueSupportKey('c'),
            related_event_key: opaqueSupportKey('a'), fulfillment_item: null,
            fulfillment_status: null, actor_key: null,
          }],
          recorded_allowance: {
            ...recordedAllowance,
            effective_allowance: 350,
            sources: [recordedAllowance.sources[2]],
          },
        },
        benefits: {
          state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
          effective_benefits: [], recorded_eligibility: ['supporter_recognition', 'bounded_queue_priority', 'early_access_updates'],
        },
      },
      responsible_use: responsibleUse.status,
      development_cost_recovery: {
        target_minor: 100_000, currency: 'USD', state: 'recovered',
      },
      support_priority: publicSupport.support_priority,
      audit: [{ email: 'private@example.test', provider_secret: 'secret' }],
    })
    throw new Error(`Unexpected Support request: ${url}`)
  }, async () => {
    const catalog = await fetchSupportCatalog()
    const self = await fetchSupportSelf()
    const notice = await fetchResponsibleUse()
    const accepted = await acceptResponsibleUse({ documentVersion: 1, contentSha256: 'd'.repeat(64) })
    const admin = await fetchAdminAccountSupport('account/id')
    const transitioned = await transitionAdminAccountFulfillment('account/id', {
      target_event_id: supportEventId('a'),
      item: 'one_time_credit_grant',
      status: 'fulfilled',
      idempotency_key: opaqueSupportKey('9'),
      proof_reference: opaqueSupportKey('c'),
    })
    const recorded = await recordAdminAccountContribution('account/id', {
      source: 'buy_me_a_coffee',
      kind: 'one_time_contribution',
      amount_minor: 1250,
      currency: 'USD',
      target_event_id: null,
      idempotency_key: opaqueSupportKey('8'),
    })
    const h3LegalAccess = await fetchH3LegalAccessState()
    const updatedH3LegalAccess = await setH3LegalAccessLocation({
      territory_code: 'JP',
      owner_attested: true,
      license_revision: h3LegalAccess.license_revision,
      license_sha256: h3LegalAccess.license_sha256,
    })
    assert.equal(catalog.provider_catalog.provider_neutral, true)
    assert.equal(catalog.supporter_benefits.schema_version, 1)
    assert.equal(catalog.supporter_benefits.one_time_tiers[1].tier, 'backer')
    assert.deepEqual(catalog.supporter_benefits.one_time_tiers[1].benefits, [
      'supporter_recognition', 'bounded_queue_priority', 'early_access_updates', 'supporter_convenience',
    ])
    assert.deepEqual(catalog.development_cost_recovery, {
      target_minor: 100_000, currency: 'USD', state: 'locked',
    })
    assert.deepEqual(self.public.development_cost_recovery, catalog.development_cost_recovery)
    assert.equal(self.account.event_count, 2)
    assert.equal(notice.notice.version, 1)
    assert.equal(accepted.status.accepted, true)
    assert.equal(admin.account.event_count, 4)
    assert.deepEqual(admin.development_cost_recovery, {
      target_minor: 100_000, currency: 'USD', state: 'recovered',
    })
    assert.deepEqual(transitioned.development_cost_recovery, admin.development_cost_recovery)
    assert.deepEqual(recorded.development_cost_recovery, admin.development_cost_recovery)
    assert.deepEqual(self.account.recorded_allowance, recordedAllowance)
    assert.deepEqual(admin.account.recorded_allowance, {
      ...recordedAllowance,
      effective_allowance: 350,
      sources: [recordedAllowance.sources[2]],
    })
    assert.deepEqual(admin.audit.currency_totals_minor, { USD: 2500 })
    assert.deepEqual(admin.audit.discrepancies, [{
      event_id: supportEventId('b'), reason: 'unresolved_or_mismatched_adjustment',
    }])
    assert.deepEqual(admin.audit.fulfillment, [{
      target_event_id: supportEventId('a'), item: 'one_time_credit_grant', status: 'fulfilled',
      audit_event_id: supportEventId('c'), actor_reference: opaqueSupportKey('d'),
      proof_reference: opaqueSupportKey('c'),
      changed_at: '2026-08-11T09:10:00Z',
    }])
    assert.equal(h3LegalAccess.territory_code, 'US')
    assert.equal(updatedH3LegalAccess.territory_code, 'JP')
    assert.equal(updatedH3LegalAccess.availability_status, 'available')
    assert.deepEqual(admin.audit.events, [{
      sequence: 1, event_id: supportEventId('a'), provider: 'github_sponsors',
      source_reference: opaqueSupportKey('a'), kind: 'one_time_contribution',
      occurred_at: '2026-08-11T09:00:00Z', received_at: '2026-08-11T09:00:01Z',
      amount_minor: 2500, currency: 'USD', contract_reference: null, related_reference: null,
      fulfillment_item: null, fulfillment_status: null, actor_reference: null,
    }, {
      sequence: 2, event_id: supportEventId('c'), provider: 'github_sponsors',
      source_reference: opaqueSupportKey('e'), kind: 'fulfillment_set',
      occurred_at: '2026-08-11T09:10:00Z', received_at: '2026-08-11T09:10:01Z',
      amount_minor: 0, currency: 'USD', contract_reference: opaqueSupportKey('c'),
      related_reference: opaqueSupportKey('a'), fulfillment_item: 'one_time_credit_grant',
      fulfillment_status: 'fulfilled', actor_reference: opaqueSupportKey('d'),
    }, {
      sequence: 3, event_id: supportEventId('d'), provider: 'github_sponsors',
      source_reference: opaqueSupportKey('f'), kind: 'refund',
      occurred_at: '2026-08-11T09:20:00Z', received_at: '2026-08-11T09:20:01Z',
      amount_minor: 500, currency: 'USD', contract_reference: opaqueSupportKey('c'),
      related_reference: opaqueSupportKey('a'), fulfillment_item: null,
      fulfillment_status: null, actor_reference: null,
    }, {
      sequence: 4, event_id: supportEventId('e'), provider: 'github_sponsors',
      source_reference: opaqueSupportKey('0'), kind: 'recurring_canceled',
      occurred_at: '2026-08-11T09:30:00Z', received_at: '2026-08-11T09:30:01Z',
      amount_minor: 0, currency: 'USD', contract_reference: opaqueSupportKey('c'),
      related_reference: opaqueSupportKey('a'), fulfillment_item: null,
      fulfillment_status: null, actor_reference: null,
    }])
    assert.equal(admin.audit.incomplete, true)
    assert.equal(transitioned.audit.fulfillment[0].status, 'fulfilled')
    assert.equal(recorded.audit.events.length, 4)
    assert.doesNotMatch(JSON.stringify(self), /currency_totals_minor|amount_minor|subject_key|source_event|account_id|"audit"|private@example|private-allowance|private-source/)
    assert.doesNotMatch(JSON.stringify(admin), /subject_key|source_event_key|actor_key|account_id|unsafe_currency|private@example|private-invoice|provider_secret|private fulfillment note|private_reason/)
  })

  assert.deepEqual(calls.map(call => [call.init.method || 'GET', call.url]), [
    ['GET', '/api/v1/support/catalog'],
    ['GET', '/api/v1/support/self'],
    ['GET', '/api/v1/support/responsible-use'],
    ['POST', '/api/v1/support/responsible-use/accept'],
    ['GET', '/api/v1/support/admin/accounts/account%2Fid'],
    ['POST', '/api/v1/support/admin/accounts/account%2Fid/fulfillment'],
    ['POST', '/api/v1/support/admin/accounts/account%2Fid/contributions'],
    ['GET', '/api/v1/h3/legal-access'],
    ['PUT', '/api/v1/h3/legal-access'],
  ])
  for (const call of calls) {
    assert.equal(call.init.credentials, 'same-origin')
    assert.equal(call.init.cache, 'no-store')
    assert.equal(new Headers(call.init.headers).get('Accept'), 'application/json')
  }
  assert.deepEqual(JSON.parse(calls[3].init.body), {
    document_version: 1,
    content_sha256: 'd'.repeat(64),
  })
  assert.deepEqual(JSON.parse(calls[5].init.body), {
    target_event_id: supportEventId('a'),
    item: 'one_time_credit_grant',
    status: 'fulfilled',
    idempotency_key: opaqueSupportKey('9'),
    proof_reference: opaqueSupportKey('c'),
  })
  assert.deepEqual(JSON.parse(calls[6].init.body), {
    source: 'buy_me_a_coffee',
    kind: 'one_time_contribution',
    amount_minor: 1250,
    currency: 'USD',
    target_event_id: null,
    idempotency_key: opaqueSupportKey('8'),
  })
  assert.equal(calls[7].init.method || 'GET', 'GET')
  assert.equal(calls[8].init.method || 'GET', 'PUT')
  assert.deepEqual(JSON.parse(calls[8].init.body), {
    territory_code: 'JP',
    owner_attested: true,
    license_revision: h3LegalAccessProjection.license_revision,
    license_sha256: h3LegalAccessProjection.license_sha256,
  })
  assert.equal(calls[0].init.body, undefined)
})

test('Krea owner-policy wrappers use the exact v2 role map and no-store route', async () => {
  const calls = []
  await withFetchMock(async (url, init = {}) => {
    calls.push({ url: String(url), init })
    if ((init.method || 'GET') === 'PUT') {
      return jsonResponse({
        status: 'ok',
        attested: true,
        availability_status: 'license_conditions_recorded',
        migration_required: false,
        local_execution_allowed: true,
        hosted_execution_allowed: false,
        maestro_content_filtering: false,
        manual_owner_review: true,
        role_use_scopes: { owner: 'noncommercial', user: 'commercial_under_1m' },
        declared_at_unix: 1_700_000_001,
        content_handling: 'manual_owner_review',
      })
    }
    return jsonResponse(kreaOwnerPolicyProjection)
  }, async () => {
    const current = await fetchKreaOwnerPolicy()
    assert.deepEqual(current.role_use_scopes, {
      owner: 'noncommercial', user: 'commercial_under_1m',
    })
    const updated = await setKreaOwnerPolicy({
      owner_attested: true,
      manual_review_accepted: true,
      local_content_stays_local: true,
      attribution_accepted: true,
      role_use_scopes: { owner: 'noncommercial', user: 'commercial_under_1m' },
      license_version: current.license_version,
      license_date: current.license_date,
    })
    assert.equal(updated.status, 'ok')
    assert.equal('license_url' in updated, false)
    assert.equal('acceptable_use_url' in updated, false)
    assert.equal('license_version' in updated, false)
  })
  assert.equal(calls.length, 2)
  assert.equal(calls[0].url, '/api/v1/krea/owner-policy')
  assert.equal(calls[0].init.method || 'GET', 'GET')
  assert.equal(calls[1].url, '/api/v1/krea/owner-policy')
  assert.equal(calls[1].init.method, 'PUT')
  for (const call of calls) {
    assert.equal(call.init.credentials, 'same-origin')
    assert.equal(call.init.cache, 'no-store')
  }
  assert.deepEqual(JSON.parse(calls[1].init.body), {
    owner_attested: true,
    manual_review_accepted: true,
    local_content_stays_local: true,
    attribution_accepted: true,
    role_use_scopes: { owner: 'noncommercial', user: 'commercial_under_1m' },
    license_version: 'v1',
    license_date: '2026-06-22',
  })
  assert.equal('use_scope' in JSON.parse(calls[1].init.body), false)
})

test('Support recovery projections reject malformed or privacy-bearing shapes without retaining them', async () => {
  const privacyBearingRecovery = {
    target_minor: 100_000,
    currency: 'USD',
    state: 'recovered',
    recovered_minor: 100_000,
    events: [{ amount_minor: 100_000 }],
    subject: 'private-account',
  }
  await withFetchMock(async url => {
    if (String(url).endsWith('/support/catalog')) return jsonResponse({
      ...publicSupport,
      development_cost_recovery: privacyBearingRecovery,
      recovered_minor: 100_000,
      subject: 'private-account',
    })
    if (String(url).endsWith('/support/self')) return jsonResponse({
      ...publicSupport,
      development_cost_recovery: privacyBearingRecovery,
      responsible_use: responsibleUse,
      account_support: {},
    })
    return jsonResponse({
      account_support: {},
      responsible_use: responsibleUse.status,
      development_cost_recovery: privacyBearingRecovery,
      support_priority: publicSupport.support_priority,
    })
  }, async () => {
    const catalog = await fetchSupportCatalog()
    const self = await fetchSupportSelf()
    const admin = await fetchAdminAccountSupport('private-account')
    assert.equal(catalog.development_cost_recovery, null)
    assert.equal(self.public.development_cost_recovery, null)
    assert.equal(admin.development_cost_recovery, null)
    assert.doesNotMatch(
      JSON.stringify([catalog, self.public, admin.development_cost_recovery]),
      /recovered_minor|events|subject|private-account/,
    )
  })
})

test('Supporter benefit policy decoder rejects unknown perks and privacy-bearing fields as one closed projection', async () => {
  await withFetchMock(async () => jsonResponse({
    ...publicSupport,
    supporter_benefits: {
      ...publicSupport.supporter_benefits,
      private_subject: 'private-account',
      one_time_tiers: [{
        ...publicSupport.supporter_benefits.one_time_tiers[0],
        benefits: ['supporter_recognition', 'future_unknown_perk'],
      }],
    },
  }), async () => {
    const catalog = await fetchSupportCatalog()
    assert.equal(catalog.supporter_benefits, null)
    assert.doesNotMatch(JSON.stringify(catalog), /private_subject|private-account|future_unknown_perk/)
  })
})

test('Support self preserves the server-authored unmetered realm without activating queue perks', async () => {
  await withFetchMock(async () => jsonResponse({
    ...publicSupport,
    account_support: {
      recorded: {
        event_count: 1,
        one_time_tier: 'supporter',
        recurring_tier: null,
        active_recurring_count: 0,
        recorded_allowance: recordedAllowance,
      },
      benefits: {
        state: 'unmetered_realm',
        scheduler_enforcement_enabled: true,
        effective_benefits: [],
        recorded_eligibility: ['supporter_recognition', 'bounded_queue_priority'],
      },
    },
    responsible_use: responsibleUse,
  }), async () => {
    const self = await fetchSupportSelf()
    assert.equal(self.account.benefits.state, 'unmetered_realm')
    assert.deepEqual(self.account.benefits.effective_benefits, [])
    assert.deepEqual(self.account.benefits.recorded_eligibility, [
      'supporter_recognition', 'bounded_queue_priority',
    ])
  })
})

test('Support self preserves a server-authored active hosted allowance', async () => {
  await withFetchMock(async () => jsonResponse({
    ...publicSupport,
    account_support: {
      recorded: {
        event_count: 1,
        one_time_tier: 'backer',
        recurring_tier: null,
        active_recurring_count: 0,
        recorded_allowance: {
          ...recordedAllowance,
          state: 'active',
          enforcement_enabled: true,
        },
      },
      benefits: {
        state: 'active',
        scheduler_enforcement_enabled: true,
        effective_benefits: ['bounded_queue_priority'],
        recorded_eligibility: ['supporter_recognition', 'bounded_queue_priority'],
      },
    },
    responsible_use: responsibleUse,
  }), async () => {
    const self = await fetchSupportSelf()
    assert.equal(self.account.recorded_allowance.state, 'active')
    assert.equal(self.account.recorded_allowance.enforcement_enabled, true)
    assert.equal(self.account.recorded_allowance.effective_allowance, 460)
    assert.deepEqual(self.account.recorded_allowance.sources, recordedAllowance.sources)
  })
})

test('Support self fails closed on incoherent hosted benefit claims', async () => {
  await withFetchMock(async () => jsonResponse({
    ...publicSupport,
    account_support: {
      recorded: {
        event_count: 1,
        active_recurring_count: 0,
        recorded_allowance: {
          ...recordedAllowance,
          state: 'active',
          enforcement_enabled: true,
        },
      },
      benefits: {
        state: 'active',
        scheduler_enforcement_enabled: true,
        effective_benefits: [],
        recorded_eligibility: [],
      },
    },
    responsible_use: responsibleUse,
  }), async () => {
    const self = await fetchSupportSelf()
    assert.equal(self.account.benefits.state, 'recorded_not_enforced')
    assert.equal(self.account.benefits.scheduler_enforcement_enabled, false)
    assert.equal(Object.hasOwn(self.account, 'recorded_allowance'), false)
  })
})

test('Support self rejects an active hosted claim with zero allowance', async () => {
  await withFetchMock(async () => jsonResponse({
    ...publicSupport,
    account_support: {
      recorded: {
        event_count: 0,
        active_recurring_count: 0,
        recorded_allowance: {
          ...recordedAllowance,
          state: 'active',
          enforcement_enabled: true,
          effective_allowance: 0,
          sources: [],
        },
      },
      benefits: {
        state: 'active',
        scheduler_enforcement_enabled: true,
        effective_benefits: ['bounded_queue_priority'],
        recorded_eligibility: [],
      },
    },
    responsible_use: responsibleUse,
  }), async () => {
    const self = await fetchSupportSelf()
    assert.equal(self.account.benefits.state, 'recorded_not_enforced')
    assert.equal(Object.hasOwn(self.account, 'recorded_allowance'), false)
  })
})

test('Support self rejects no-allowance priority copy with a positive allowance', async () => {
  await withFetchMock(async () => jsonResponse({
    ...publicSupport,
    account_support: {
      recorded: {
        event_count: 1,
        active_recurring_count: 0,
        recorded_allowance: recordedAllowance,
      },
      benefits: {
        state: 'hosted_priority_available',
        scheduler_enforcement_enabled: true,
        effective_benefits: [],
        recorded_eligibility: [],
      },
    },
    responsible_use: responsibleUse,
  }), async () => {
    const self = await fetchSupportSelf()
    assert.equal(self.account.benefits.state, 'recorded_not_enforced')
    assert.equal(Object.hasOwn(self.account, 'recorded_allowance'), false)
  })
})

test('Support account mapping preserves legacy responses without a recorded allowance', async () => {
  await withFetchMock(async () => jsonResponse({
    ...publicSupport,
    account_support: {
      recorded: { event_count: 1, active_recurring_count: 0 },
      benefits: {
        state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
        effective_benefits: [], recorded_eligibility: [],
      },
    },
    responsible_use: responsibleUse,
  }), async () => {
    const self = await fetchSupportSelf()
    assert.equal(Object.hasOwn(self.account, 'recorded_allowance'), false)
  })
})

test('Support admin decoder admits only the bounded manual-provider actor shape', async () => {
  await withFetchMock(async () => jsonResponse({
    account_support: {
      recorded: {
        event_count: 1,
        active_recurring_count: 0,
        currency_totals_minor: { USD: 1250 },
        audit: [{
          sequence: 1,
          event_id: supportEventId('1'),
          provider: 'manual_buy_me_a_coffee',
          source_event_key: opaqueSupportKey('1'),
          kind: 'one_time_contribution',
          occurred_at: '2026-08-12T09:00:00Z',
          received_at: '2026-08-12T09:00:01Z',
          amount_minor: 1250,
          currency: 'USD',
          contract_key: null,
          related_event_key: null,
          fulfillment_item: null,
          fulfillment_status: null,
          actor_key: opaqueSupportKey('2'),
          operator_note: 'must not cross the decoder',
        }],
        fulfillment: [],
        unresolved: [],
      },
      benefits: {
        state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
        effective_benefits: [], recorded_eligibility: [],
      },
    },
    responsible_use: responsibleUse.status,
    support_priority: publicSupport.support_priority,
  }), async () => {
    const admin = await fetchAdminAccountSupport('account-id')
    assert.equal(admin.audit.incomplete, false)
    assert.deepEqual(admin.audit.events, [{
      sequence: 1,
      event_id: supportEventId('1'),
      provider: 'manual_buy_me_a_coffee',
      source_reference: opaqueSupportKey('1'),
      kind: 'one_time_contribution',
      occurred_at: '2026-08-12T09:00:00Z',
      received_at: '2026-08-12T09:00:01Z',
      amount_minor: 1250,
      currency: 'USD',
      contract_reference: null,
      related_reference: null,
      fulfillment_item: null,
      fulfillment_status: null,
      actor_reference: opaqueSupportKey('2'),
    }])
    assert.doesNotMatch(JSON.stringify(admin), /operator_note|must not cross/)
  })
})

test('Support admin mapping normalizes legacy complete fulfillment without inventing proof', async () => {
  await withFetchMock(async () => jsonResponse({
    account_support: {
      recorded: {
        event_count: 1,
        active_recurring_count: 0,
        currency_totals_minor: {},
        audit: [],
        unresolved: [],
        fulfillment: [{
          target_event_id: supportEventId('a'),
          item: 'one_time_credit_grant',
          status: 'complete',
          audit_event_id: supportEventId('b'),
          actor_key: opaqueSupportKey('c'),
          changed_at: '2026-08-11T09:10:00Z',
        }],
      },
      benefits: {
        state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
        effective_benefits: [], recorded_eligibility: [],
      },
    },
    responsible_use: responsibleUse.status,
    support_priority: publicSupport.support_priority,
  }), async () => {
    const admin = await fetchAdminAccountSupport('legacy')
    assert.equal(admin.audit.incomplete, false)
    assert.deepEqual(admin.audit.fulfillment, [{
      target_event_id: supportEventId('a'),
      item: 'one_time_credit_grant',
      status: 'fulfilled',
      audit_event_id: supportEventId('b'),
      actor_reference: opaqueSupportKey('c'),
      proof_reference: null,
      changed_at: '2026-08-11T09:10:00Z',
    }])
  })
})

test('Support admin audit distinguishes complete empty records from malformed dropped rows', async () => {
  const base = {
    account_support: {
      recorded: {
        event_count: 0, active_recurring_count: 0,
        currency_totals_minor: {}, audit: [], fulfillment: [], unresolved: [],
      },
      benefits: {
        state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
        effective_benefits: [], recorded_eligibility: [],
      },
    },
    responsible_use: responsibleUse.status,
    support_priority: publicSupport.support_priority,
  }
  let malformed = false
  await withFetchMock(async () => jsonResponse(malformed ? {
    ...base,
    account_support: {
      ...base.account_support,
      recorded: {
        ...base.account_support.recorded,
        audit: [{
          sequence: 1, event_id: supportEventId('a'), provider: 'github_sponsors',
          source_event_key: opaqueSupportKey('a'), kind: 'one_time_contribution',
          occurred_at: '2026-08-11T09:00:00Z', received_at: '2026-08-11T09:00:01Z',
          amount_minor: 0, currency: 'USD', contract_key: null, related_event_key: null,
          fulfillment_item: null, fulfillment_status: null, actor_key: null,
          notes: 'private content must still be dropped',
        }],
      },
    },
  } : base), async () => {
    const complete = await fetchAdminAccountSupport('complete')
    assert.deepEqual(complete.audit, {
      currency_totals_minor: {}, events: [], fulfillment: [], discrepancies: [], incomplete: false,
    })
    malformed = true
    const incomplete = await fetchAdminAccountSupport('malformed')
    assert.equal(incomplete.audit.incomplete, true)
    assert.deepEqual(incomplete.audit.events, [])
    assert.doesNotMatch(JSON.stringify(incomplete), /private content|notes/)
  })
})

test('Support allowance mapping matches the backend unit boundary and rejects incomplete breakdowns', async () => {
  const unitAtBackendLimit = `a${'b'.repeat(63)}`
  const allowances = [
    { ...recordedAllowance, unit: unitAtBackendLimit },
    { ...recordedAllowance, effective_allowance: 999 },
    {
      ...recordedAllowance,
      sources: [
        ...recordedAllowance.sources,
        {
          source: 'future_private_source', granted_allowance: 0, effective_allowance: 0,
          expires_at: null, status: 'active', refund_state: 'none',
        },
      ],
    },
  ]
  await withFetchMock(async () => jsonResponse({
    ...publicSupport,
    account_support: {
      recorded: {
        event_count: 1,
        active_recurring_count: 0,
        recorded_allowance: allowances.shift(),
      },
      benefits: {
        state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
        effective_benefits: [], recorded_eligibility: [],
      },
    },
    responsible_use: responsibleUse,
  }), async () => {
    const boundary = await fetchSupportSelf()
    assert.equal(boundary.account.recorded_allowance.unit, unitAtBackendLimit)
    const mismatched = await fetchSupportSelf()
    assert.equal(Object.hasOwn(mismatched.account, 'recorded_allowance'), false)
    const incomplete = await fetchSupportSelf()
    assert.equal(Object.hasOwn(incomplete.account, 'recorded_allowance'), false)
  })
})

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

async function loadAccountButton() {
  const result = await build({
    entryPoints: [componentUrl.pathname],
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
    plugins: [{
      name: 'account-button-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'account-button' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'account-button' }))
        bundle.onResolve({ filter: /^react-dom$/ }, () => ({ path: 'react-dom', namespace: 'account-button' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide', namespace: 'account-button' }))
        bundle.onResolve({ filter: /api\/client$/ }, () => ({ path: 'api', namespace: 'account-button' }))
        bundle.onResolve({ filter: /lib\/modalFocus$/ }, () => ({ path: 'focus', namespace: 'account-button' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'account-button' }))
        bundle.onLoad({ filter: /.*/, namespace: 'account-button' }, args => {
          if (args.path === 'react') return { contents: `
            export const useCallback = value => value
            export const useEffect = effect => {
              if (globalThis.__accountRunEffects) effect()
            }
            export const useId = () => 'account-title'
            export const useMemo = value => value()
            export const useRef = value => {
              globalThis.__accountRefs ??= []
              const index = globalThis.__accountRefIndex ?? 0
              globalThis.__accountRefIndex = index + 1
              globalThis.__accountRefs[index] ??= { current: value }
              return globalThis.__accountRefs[index]
            }
            export const useState = value => {
              const initial = value === 'support' && globalThis.__accountActiveTab
                ? globalThis.__accountActiveTab
                : value === 'login' && globalThis.__accountEntryMode
                  ? globalThis.__accountEntryMode
                  : value
              return [initial, next => {
                globalThis.__accountStateWrites?.push({ initial, next: typeof next === 'function' ? next(initial) : next })
              }]
            }
          ` }
          if (args.path === 'jsx-runtime') return { contents: `
            export const Fragment = Symbol.for('fragment')
            export const jsx = (type, props, key) => ({ type, key, props: props || {} })
            export const jsxs = jsx
          ` }
          if (args.path === 'react-dom') return { contents: 'export const createPortal = value => value' }
          if (args.path === 'lucide') return { contents: `
            export const Check='Check', ExternalLink='ExternalLink', HeartHandshake='HeartHandshake', KeyRound='KeyRound', Loader2='Loader2', LogIn='LogIn', LogOut='LogOut', RefreshCw='RefreshCw', ShieldCheck='ShieldCheck', UserCog='UserCog', UserPlus='UserPlus', UserRound='UserRound', X='X'
          ` }
          if (args.path === 'api') return { contents: `
            export class AccountApiError extends Error {}
            export const registerAccount = async input => globalThis.__registerAccount?.(input) ?? null
            export const fetchKreaOwnerPolicy = async () => globalThis.__kreaOwnerPolicy ?? null
            export const setKreaOwnerPolicy = async input => globalThis.__setKreaOwnerPolicy?.(input) ?? null
            export const isDirectLoopbackHostname = hostname => hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1'
            export const isAccountProjectAccessActive = (context, migration = null) => context?.accounts?.enabled === true && (migration !== null ? migration.state === 'active' && migration.enforced === true : context.account_project_access_active === true)
          ` }
          if (args.path === 'focus') return { contents: 'export const closeModalIfTop = () => true; export const installModalFocus = () => () => {}' }
          if (args.path === 'store') return { contents: 'export const useStore = selector => selector(globalThis.__accountStore)' }
          return null
        })
      },
    }],
  })
  return import(asDataModule(result.outputFiles[0].text))
}

async function loadSupportPanel() {
  const result = await build({
    entryPoints: [supportPanelUrl.pathname],
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
    plugins: [{
      name: 'support-panel-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'support-panel' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'support-panel' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide', namespace: 'support-panel' }))
        bundle.onResolve({ filter: /api\/client$/ }, () => ({ path: 'api', namespace: 'support-panel' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'support-panel' }))
        bundle.onLoad({ filter: /.*/, namespace: 'support-panel' }, args => {
          if (args.path === 'react') return { contents: `
            export const useEffect = () => {}
            export const useMemo = value => value()
            export const useRef = value => ({ current: value })
            export const useState = value => {
              if (value === '' && globalThis.__supportSelectedUserIndex !== undefined) {
                return [globalThis.__supportSelectedUserIndex, () => {}]
              }
              if (value === null && globalThis.__supportNotice !== undefined) {
                return [globalThis.__supportNotice, () => {}]
              }
              if (value === 'buy_me_a_coffee' && globalThis.__supportManualSource !== undefined) {
                return [globalThis.__supportManualSource, () => {}]
              }
              if (value === 'one_time_contribution' && globalThis.__supportManualKind !== undefined) {
                return [globalThis.__supportManualKind, () => {}]
              }
              return [value, () => {}]
            }
          ` }
          if (args.path === 'jsx-runtime') return { contents: `
            export const Fragment = Symbol.for('fragment')
            export const jsx = (type, props, key) => ({ type, key, props: props || {} })
            export const jsxs = jsx
          ` }
          if (args.path === 'lucide') return { contents: `
            export const Check='Check', ExternalLink='ExternalLink', HeartHandshake='HeartHandshake', Loader2='Loader2', ShieldCheck='ShieldCheck'
          ` }
          if (args.path === 'api') return { contents: `
            export class AccountApiError extends Error {}
            export const isAccountProjectAccessActive = (context, migration = null) => context?.accounts?.enabled === true && (migration !== null ? migration.state === 'active' && migration.enforced === true : context.account_project_access_active === true)
            export const fetchKreaOwnerPolicy = async () => globalThis.__kreaOwnerPolicy ?? null
            export const setKreaOwnerPolicy = async input => globalThis.__setKreaOwnerPolicy?.(input) ?? null
          ` }
          if (args.path === 'store') return { contents: `
            export const useStore = selector => selector({
              supportH3LegalAccess: null,
              supportH3LegalAccessLoading: false,
              supportH3LegalAccessError: null,
              loadH3LegalAccessState: async () => null,
              setH3LegalAccessLocation: async () => null,
              ...globalThis.__supportStore,
            })
          ` }
          return null
        })
      },
    }],
  })
  return import(`${asDataModule(result.outputFiles[0].text)}#support-panel`)
}

function expandElement(value) {
  if (Array.isArray(value)) return value.map(expandElement)
  if (value === null || value === undefined || typeof value !== 'object') return value
  if (typeof value.type === 'function') return expandElement(value.type(value.props || {}))
  return {
    ...value,
    props: {
      ...(value.props || {}),
      children: expandElement(value.props?.children),
    },
  }
}

function elementText(value) {
  if (Array.isArray(value)) return value.map(elementText).join('')
  if (value === null || value === undefined || typeof value === 'boolean') return ''
  if (typeof value !== 'object') return String(value)
  return elementText(value.props?.children)
}

function findElements(value, predicate, found = []) {
  if (Array.isArray(value)) {
    for (const child of value) findElements(child, predicate, found)
  } else if (value && typeof value === 'object') {
    if (predicate(value)) found.push(value)
    findElements(value.props?.children, predicate, found)
  }
  return found
}

test('Support renders only server-authored account activation readiness as passive, privacy-bounded status', async t => {
  const { SupportPanel } = await loadSupportPanel()
  t.after(() => { delete globalThis.__supportStore })
  const context = {
    enabled: false,
    authenticated: false,
    account: null,
    capabilities: [],
    reauthenticated: false,
    passkey_authentication_available: false,
    bootstrap_available: true,
  }
  globalThis.__supportStore = {
    accountContext: context,
    accountUsers: [], supportCatalog: publicSupport, supportCatalogLoading: false,
    supportCatalogUnavailable: false, supportSelf: null, responsibleUse: null,
    supportAdmin: null, supportAdminAccountId: null, supportDetailsLoading: false,
    loadSupportCatalog: async () => null, loadSupportSelf: async () => null,
    loadResponsibleUse: async () => null, acceptResponsibleUse: async () => null,
    loadSupportAdmin: async () => null, clearSupportAdmin: () => {},
  }

  const expected = new Map([
    ['disabled', ['Accounts are optional and off', 'No account setup or sign-in is required to keep using Maestro.']],
    ['setup_available', ['Owner setup is available', 'Create the first owner account from the Account tab while using Maestro directly on this computer. Maestro will not create an account automatically.']],
    ['setup_requires_loopback', ['Open Maestro on this computer to continue', 'For security, create the first owner account by opening Maestro directly on the computer where it is running. Setup details are hidden on this connection.']],
    ['disable_bootstrap', ['Owner setup is complete', 'Restart Maestro after turning off first-owner setup in its account configuration.']],
    ['ready', ['Account access is ready', 'Sign-in and account controls are available. Existing project access may still depend on this browser or a project password.']],
    ['unavailable', ['Account setup status is unavailable', 'Maestro could not determine whether account setup is ready, so setup is unavailable from this connection.']],
  ])

  for (const [activationState, copy] of expected) {
    globalThis.__supportStore.accountContext = { ...context, activation_state: activationState }
    const tree = expandElement(SupportPanel())
    const readinessSections = findElements(tree, node => node.props?.['aria-label'] === 'Account setup status')
    assert.equal(readinessSections.length, 1)
    const readinessText = elementText(readinessSections[0])
    assert.match(readinessText, new RegExp(copy[0].replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    assert.match(readinessText, new RegExp(copy[1].replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    assert.equal(findElements(readinessSections[0], node => node.type === 'button' || node.type === 'a').length, 0)
    assert.equal(findElements(readinessSections[0], node => node.props?.role === 'status').length, 1)
    assert.doesNotMatch(readinessText, /credential|passkey|account count|created at|timestamp|store path|provider|payment|credit/i)
  }

  globalThis.__supportStore.accountContext = { ...context, enabled: true, activation_state: 'ready' }
  globalThis.__supportStore.accessContext = {
    accounts: { enabled: true },
    account_project_access_active: true,
    project_password_required: false,
  }
  const activeReadinessText = elementText(findElements(
    expandElement(SupportPanel()),
    node => node.props?.['aria-label'] === 'Account setup status',
  )[0])
  assert.match(activeReadinessText, /Project access follows your account membership/)
  assert.doesNotMatch(activeReadinessText, /project password/)
  globalThis.__supportStore.accessContext = undefined

  globalThis.__supportStore.accountContext = { ...context, activation_state: 'unknown_future_state' }
  const malformedText = elementText(findElements(
    expandElement(SupportPanel()),
    node => node.props?.['aria-label'] === 'Account setup status',
  )[0])
  assert.match(malformedText, /Account setup status is unavailable/)
  globalThis.__supportStore.accountContext = {
    ...context,
    enabled: true,
    bootstrap_available: true,
  }
  const fallbackText = elementText(findElements(
    expandElement(SupportPanel()),
    node => node.props?.['aria-label'] === 'Account setup status',
  )[0])
  assert.match(fallbackText, /Account setup status is unavailable/)
  assert.doesNotMatch(fallbackText, /Owner setup is available/)
})

test('Support panel renders a semantic mobile-safe recorded allowance without overstating enforcement', async () => {
  const { SupportPanel } = await loadSupportPanel()
  const account = {
    event_count: 2, one_time_tier: 'backer', recurring_tier: 'member', active_recurring_count: 1,
    recorded_allowance: recordedAllowance,
    owner_test_credits: {
      state: 'active', available_units: 900, used_units: 100, target_balance: 1_000,
      last_activity_at: '2026-08-24T12:00:00Z',
    },
    benefits: {
      state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
      effective_benefits: [], recorded_eligibility: [
        'supporter_recognition', 'bounded_queue_priority', 'early_access_updates', 'supporter_convenience',
      ],
    },
  }
  globalThis.__supportStore = {
    accountContext: {
      enabled: true, authenticated: true, account: { id: 'current-account' },
      capabilities: ['account.self'], reauthenticated: false,
    },
    accountUsers: [], supportCatalog: publicSupport, supportCatalogLoading: false,
    supportCatalogUnavailable: false,
    supportSelf: { public: publicSupport, account, responsible_use: responsibleUse },
    responsibleUse: null, supportAdmin: null, supportAdminAccountId: null,
    supportDetailsLoading: false,
    loadSupportCatalog: async () => null, loadSupportSelf: async () => null,
    loadResponsibleUse: async () => null, acceptResponsibleUse: async () => null,
    loadSupportAdmin: async () => null, clearSupportAdmin: () => {},
  }

  const tree = expandElement(SupportPanel())
  const text = elementText(tree)
  assert.match(text, /Support first helps cover \$1,000 in development costs/)
  assert.match(text, /After that, it can help fund hosting Maestro Continuum with more compute/)
  assert.match(text, /\$1,000 development-cost target/)
  assert.match(text, /Direct compute sponsorship/i)
  assert.match(text, /Vast\.ai compute sponsorship stays locked/i)
  assert.match(text, /Zero-credit work remains schedulable/i)
  assert.match(text, /checks net recorded USD support after refunds/)
  assert.match(text, /running total and contribution history stay private/)
  assert.match(text, /thank-you benefits/)
  assert.match(text, /Supporter tiers and perks/)
  assert.match(text, /One-time support/)
  assert.match(text, /Recurring support/)
  assert.match(text, /Supporter\$5\+/)
  assert.match(text, /Backer\$25\+/)
  assert.match(text, /Sponsor\$100\+/)
  assert.match(text, /Member\$3\+/)
  assert.match(text, /Published eligibility/)
  assert.match(text, /Early-access and convenience items remain recorded eligibility until Maestro explicitly delivers them/)
  assert.match(text, /One-time Backer/)
  assert.match(text, /Recurring Member/)
  assert.match(text, /Delivered or active now: Supporter recognition/)
  assert.match(text, /Recorded eligibility, not active yet: Bounded hosted queue priority · Early access updates · Supporter convenience features/)
  assert.match(text, /Buy Me a Coffee/)
  assert.match(text, /Patreon/)
  const supportLinks = findElements(tree, node => node.type === 'a' && String(node.props?.href || '').includes('maestro'))
  assert.deepEqual(supportLinks.map(node => node.props.href), [
    'https://buymeacoffee.com/maestro',
    'https://www.patreon.com/maestro',
  ])
  for (const link of supportLinks) {
    assert.equal(link.props.target, '_blank')
    assert.equal(link.props.rel, 'noopener noreferrer')
    assert.match(link.props.className, /\bmin-h-11\b/)
    assert.equal(link.props.onClick, undefined)
  }
  const unavailableSupport = findElements(tree, node => node.props?.['aria-disabled'] === 'true')
  assert.equal(unavailableSupport.length, 1)
  assert.match(text, /Hosted queue allowance · inactive/)
  assert.match(text, /Recorded amount: 460 maestro credits/)
  assert.match(text, /460 maestro credits/)
  assert.match(text, /Recorded as of/)
  assert.match(text, /not active on the current host and does not change generation, queueing, or retries/)
  assert.match(text, /Recorded status: active/)
  assert.match(text, /Recorded amount:/)
  assert.match(text, /Free allowance/)
  assert.match(text, /One-time support/)
  assert.match(text, /Recurring support/)
  assert.match(text, /Partial refund recorded/)
  assert.match(text, /Technical details · owner credit test/)
  assert.match(text, /900 available · 100 used/)
  assert.match(text, /without changing real credits, access, or queue priority/)
  assert.equal(findElements(tree, node => node.props?.['aria-label'] === 'Owner credit test details').length, 1)
  assert.doesNotMatch(text, /spendable|remaining|private-source|source-event|provider/i)

  const allowanceSection = findElements(tree, node => node.props?.['aria-label'] === 'Recorded hosted queue allowance')
  assert.equal(allowanceSection.length, 1)
  assert.match(allowanceSection[0].props.className, /\bmin-w-0\b/)
  const sourceList = findElements(tree, node => node.type === 'ul' && node.props?.['aria-label'] === 'Allowance breakdown')
  assert.equal(sourceList.length, 1)
  assert.match(sourceList[0].props.className, /\bgrid-cols-1\b/)
  assert.match(sourceList[0].props.className, /\bmin-w-0\b/)
  assert.equal(findElements(sourceList[0], node => node.type === 'li').length, 3)

  globalThis.__supportStore.supportSelf = {
    public: publicSupport,
    account: {
      ...account,
      benefits: {
        state: 'active', scheduler_enforcement_enabled: true,
        effective_benefits: ['bounded_queue_priority'], recorded_eligibility: [],
      },
      recorded_allowance: {
        ...recordedAllowance,
        state: 'active',
        enforcement_enabled: true,
      },
    },
    responsible_use: responsibleUse,
  }
  const activeTree = expandElement(SupportPanel())
  const activeText = elementText(activeTree)
  assert.match(activeText, /active hosted queue allowance/i)
  assert.match(activeText, /Eligible jobs can receive bounded priority/)
  assert.match(activeText, /jobs without enough allowance still remain eligible/)
  assert.equal(findElements(
    activeTree,
    node => node.props?.['aria-label'] === 'Active hosted queue allowance',
  ).length, 1)

  globalThis.__supportStore.supportSelf = {
    public: publicSupport,
    account: {
      ...account,
      recorded_allowance: {
        ...recordedAllowance,
        sources: Array.from({ length: 25 }, () => recordedAllowance.sources[0]),
      },
    },
    responsible_use: responsibleUse,
  }
  const boundedTree = expandElement(SupportPanel())
  const boundedList = findElements(boundedTree, node => node.type === 'ul' && node.props?.['aria-label'] === 'Allowance breakdown')[0]
  assert.equal(findElements(boundedList, node => node.type === 'li').length, 20)
  assert.match(elementText(boundedTree), /5 additional items are not shown/)

  globalThis.__supportStore.supportSelf = {
    public: publicSupport,
    account: {
      ...account,
      owner_test_credits: { ...account.owner_test_credits, state: 'unavailable' },
    },
    responsible_use: responsibleUse,
  }
  assert.match(elementText(expandElement(SupportPanel())), /Test accounting is temporarily unavailable/)

  globalThis.__supportStore.supportSelf = {
    public: publicSupport,
    account: { ...account, recorded_allowance: undefined },
    responsible_use: responsibleUse,
  }
  const legacyText = elementText(expandElement(SupportPanel()))
  assert.doesNotMatch(legacyText, /Recorded hosted queue allowance|Allowance breakdown/)

  const recoveredPublic = structuredClone(publicSupport)
  recoveredPublic.development_cost_recovery.state = 'recovered'
  recoveredPublic.provider_catalog.providers[2] = {
    ...recoveredPublic.provider_catalog.providers[2],
    enabled: true,
    configured: true,
    state: 'available',
    support_url: 'https://support.operator.com/maestro',
  }
  globalThis.__supportStore.supportCatalog = recoveredPublic
  globalThis.__supportStore.supportSelf = {
    public: recoveredPublic,
    account: { ...account, recorded_allowance: undefined },
    responsible_use: responsibleUse,
  }
  const recoveredTree = expandElement(SupportPanel())
  const recoveredText = elementText(recoveredTree)
  assert.match(recoveredText, /Development costs recovered/)
  assert.match(recoveredText, /initial \$1,000 development-cost target has been reached/)
  assert.match(recoveredText, /Vast\.ai compute sponsorship may now use the operator-configured destination/)
  assert.equal(
    findElements(recoveredTree, node => node.type === 'a' && node.props?.href === 'https://support.operator.com/maestro').length,
    1,
  )
})

test('Support panel shows owner-only H3 legal-access controls and hides them for non-owners', async t => {
  const { SupportPanel } = await loadSupportPanel()
  t.after(() => {
    delete globalThis.__supportStore
    delete globalThis.__supportNotice
  })
  let h3PanelState = {
    ...h3LegalAccessProjection,
    territory_code: 'US',
    availability_status: 'legal_blocked',
    execution_allowed: false,
  }
  const ownerContext = {
    enabled: true, authenticated: true, account: { id: 'owner-account', role: 'owner' },
    capabilities: ['account.self', 'accounts.admin', 'services.admin'], reauthenticated: true,
    passkey_authentication_available: false,
  }
  globalThis.__supportStore = {
    accountContext: ownerContext,
    accountUsers: [], supportCatalog: publicSupport, supportCatalogLoading: false,
    supportCatalogUnavailable: false, supportSelf: null, responsibleUse: null,
    supportAdmin: null, supportAdminAccountId: null, supportDetailsLoading: false,
    supportH3LegalAccessLoading: false, supportH3LegalAccessError: null,
    supportH3LegalAccess: h3PanelState,
    loadSupportCatalog: async () => null, loadSupportSelf: async () => null,
    loadResponsibleUse: async () => null, acceptResponsibleUse: async () => null,
    loadSupportAdmin: async () => null, clearSupportAdmin: () => {},
    loadH3LegalAccessState: async () => h3PanelState,
    setH3LegalAccessLocation: async input => {
      h3PanelState = { ...h3PanelState, ...input, availability_status: 'available', execution_allowed: true }
      globalThis.__supportStore.supportH3LegalAccess = h3PanelState
      return h3PanelState
    },
  }
  let tree = expandElement(SupportPanel())
  const h3Section = findElements(tree, node => node.props?.['aria-label'] === 'H3 legal-access location')
  assert.equal(h3Section.length, 1)
  assert.match(elementText(tree), /H3 legal-access location/)
  assert.match(elementText(h3Section[0]), /Availability: Blocked by the current license/)
  assert.match(elementText(h3Section[0]), /I confirm that the selected country is where this computer will physically run MiniMax H3/)
  assert.match(elementText(h3Section[0]), /does not infer this declaration from an IP address, VPN, or network location/)
  assert.doesNotMatch(elementText(h3Section[0]), /Use network-detected location/)
  const attestation = findElements(h3Section[0], node => node.type === 'input' && node.props?.type === 'checkbox')[0]
  assert.equal(attestation.props.checked, false)
  const saveButton = findElements(tree, node => node.type === 'button' && elementText(node) === 'Confirm and save')[0]
  assert.equal(saveButton.props.disabled, true)

  h3PanelState = {
    ...h3PanelState,
    territory_code: 'JP',
    availability_status: 'available',
    execution_allowed: true,
  }
  globalThis.__supportStore.supportH3LegalAccess = h3PanelState
  tree = expandElement(SupportPanel())
  const jpSelect = findElements(tree, node => node.type === 'select')[0]
  assert.equal(jpSelect.props.value, 'JP')
  assert.match(elementText(tree), /Availability: Allowed/)
  assert.match(elementText(tree), /MiniMax H3 execution: allowed/)

  globalThis.__supportStore.accountContext = {
    ...ownerContext,
    account: { ...ownerContext.account, role: 'user', reauthenticated: false },
  }
  tree = expandElement(SupportPanel())
  assert.equal(findElements(tree, node => node.props?.['aria-label'] === 'H3 legal-access location').length, 0)
})

test('Support panel shows an owner-only fixed Krea v2 role map with fenced direct API state', async t => {
  const { SupportPanel } = await loadSupportPanel()
  t.after(() => {
    delete globalThis.__supportStore
  })
  const ownerContext = {
    enabled: true, authenticated: true, account: { id: 'owner-account', role: 'owner' },
    capabilities: ['account.self', 'accounts.admin', 'services.admin'], reauthenticated: true,
    passkey_authentication_available: false,
  }
  globalThis.__supportStore = {
    accountContext: ownerContext,
    accountUsers: [], supportCatalog: publicSupport, supportCatalogLoading: false,
    supportCatalogUnavailable: false, supportSelf: null, responsibleUse: null,
    supportAdmin: null, supportAdminAccountId: null, supportDetailsLoading: false,
    loadSupportCatalog: async () => null, loadSupportSelf: async () => null,
    loadResponsibleUse: async () => null, acceptResponsibleUse: async () => null,
    loadSupportAdmin: async () => null, clearSupportAdmin: () => {},
  }
  let tree = expandElement(SupportPanel())
  const sections = findElements(tree, node => node.props?.['aria-label'] === 'Krea 2 license roles')
  assert.equal(sections.length, 1)
  const text = elementText(sections[0])
  assert.match(text, /Owner accountNoncommercial use/)
  assert.match(text, /User accountCommercial use under \$1M/)
  assert.match(text, /browser request cannot choose or change its own scope/)
  assert.doesNotMatch(text, /execution ready/i)

  globalThis.__supportStore.accountContext = {
    ...ownerContext,
    account: { ...ownerContext.account, role: 'user' },
    reauthenticated: false,
  }
  tree = expandElement(SupportPanel())
  assert.equal(findElements(tree, node => node.props?.['aria-label'] === 'Krea 2 license roles').length, 0)

  const source = await readFile(supportPanelUrl, 'utf8')
  assert.match(source, /owner: 'noncommercial',[^]*user: 'commercial_under_1m'/)
  assert.match(source, /owner_attested: true,[^]*manual_review_accepted: true,[^]*local_content_stays_local: true,[^]*attribution_accepted: true/)
  assert.match(source, /await setKreaOwnerPolicy\([^]*const refreshed = await fetchKreaOwnerPolicy\(\)/)
  assert.match(source, /const epoch = \+\+kreaOwnerPolicyEpochRef\.current/)
  assert.match(source, /if \(epoch !== kreaOwnerPolicyEpochRef\.current\) return/)
  assert.match(source, /kreaOwnerPolicyEpochRef\.current \+= 1[^]*\}, \[accountId, ownerSupport\]\)/)
  assert.match(source, /I attest that locally processed content remains on this host\./)
  assert.match(source, /Krea 2 license conditions recorded\. Model files, creator terms, project access, and runtime readiness remain separate\./)
  assert.equal([...source.matchAll(/checked=\{krea(?:OwnerAttested|ManualReviewAccepted|LocalContentAccepted|AttributionAccepted)\}/g)].length, 4)
  assert.doesNotMatch(source, /content_classifier|prompt_filter|execution ready/i)
})

test('Support panel renders a bounded owner audit with fulfillment controls, loading, empty, error, and stale-selection states', async t => {
  const {
    SupportPanel,
    manualContributionRetryIdentity,
    manualContributionTargetState,
  } = await loadSupportPanel()
  t.after(() => {
    delete globalThis.__supportSelectedUserIndex
    delete globalThis.__supportNotice
    delete globalThis.__supportStore
    delete globalThis.__supportTransitions
    delete globalThis.__supportManualContributions
    delete globalThis.__supportManualDeferred
    delete globalThis.__supportManualReject
    delete globalThis.__supportManualSource
    delete globalThis.__supportManualKind
  })
  globalThis.__supportSelectedUserIndex = '0'
  const account = {
    id: 'selected-account', username: 'Selected', role: 'user', disabled: false,
    created_at: 1, has_email: false, passkey_credentials: 0,
    passkey_authentication_available: false,
  }
  const summary = {
    event_count: 42, one_time_tier: 'supporter', recurring_tier: null, active_recurring_count: 0,
    benefits: {
      state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
      effective_benefits: [], recorded_eligibility: ['supporter_recognition', 'bounded_queue_priority'],
    },
  }
  const events = Array.from({ length: 42 }, (_, index) => ({
    sequence: index + 1,
    event_id: `evt_${(index + 1).toString(16).padStart(32, '0')}`,
    provider: 'github_sponsors',
    source_reference: `key_${(index + 1).toString(16).padStart(64, '0')}`,
    kind: ['one_time_contribution', 'refund', 'chargeback', 'recurring_canceled'][index % 4],
    occurred_at: '2026-08-11T09:00:00Z', received_at: '2026-08-11T09:00:01Z',
    amount_minor: index % 4 === 3 ? 0 : 100, currency: 'USD',
    contract_reference: null, related_reference: null, fulfillment_item: null,
    fulfillment_status: null, actor_reference: null,
  }))
  const audit = {
    currency_totals_minor: { USD: 2500 },
    events,
    discrepancies: [{
      event_id: supportEventId('b'), reason: 'unresolved_or_mismatched_adjustment',
    }],
    fulfillment: ['pending', 'in_progress', 'fulfilled', 'declined', 'reversed'].map((status, index) => ({
      target_event_id: `evt_${(index + 1).toString(16).padStart(32, '0')}`,
      item: ['one_time_credit_grant', 'retention_follow_up', 'backdated_follow_up'][index]
        || `credit_grant_${index + 1}`,
      status,
      audit_event_id: `evt_${(index + 11).toString(16).padStart(32, '0')}`,
      actor_reference: opaqueSupportKey('d'),
      proof_reference: null,
      changed_at: `2026-08-11T09:1${index}:00Z`,
    })),
    incomplete: false,
  }
  globalThis.__supportStore = {
    accountContext: {
      enabled: true, authenticated: true, account: { id: 'owner-account', role: 'owner' },
      capabilities: ['account.self', 'accounts.admin', 'services.admin'], reauthenticated: true,
    },
    accountUsers: [account], supportCatalog: publicSupport, supportCatalogLoading: false,
    supportCatalogUnavailable: false, supportSelf: null, responsibleUse: null,
    supportAdmin: {
      account: summary,
      audit,
      responsible_use: responsibleUse.status,
      development_cost_recovery: publicSupport.development_cost_recovery,
      support_priority: publicSupport.support_priority,
    },
    supportAdminAccountId: account.id, supportDetailsLoading: false,
    loadSupportCatalog: async () => null, loadSupportSelf: async () => null,
    loadResponsibleUse: async () => null, acceptResponsibleUse: async () => null,
    loadSupportAdmin: async () => null,
    transitionSupportFulfillment: async (_accountId, input) => {
      globalThis.__supportTransitions.push(input)
      return null
    },
    recordSupportContribution: async (_accountId, input) => {
      globalThis.__supportManualContributions.push(input)
      if (globalThis.__supportManualDeferred) return globalThis.__supportManualDeferred.promise
      if (globalThis.__supportManualReject) throw new Error('ambiguous network failure')
      return null
    },
    clearSupportAdmin: () => {},
  }
  globalThis.__supportTransitions = []
  globalThis.__supportManualContributions = []

  const tree = expandElement(SupportPanel())
  const text = elementText(tree)
  assert.match(text, /Private support history and follow-up/)
  assert.match(text, /never processes a payment here/i)
  assert.match(text, /Contribution records can grant Maestro queue credits/i)
  assert.match(text, /does not process a payment[^]*may grant Maestro queue credits/i)
  assert.doesNotMatch(text, /recorded_not_enforced|opaque|audit|proof|loopback|cookie|fulfillment|minor unit|terminal|sequence/i)
  const manualOptionValues = findElements(tree, node => node.type === 'option')
    .map(node => node.props.value)
  assert.deepEqual(
    ['buy_me_a_coffee', 'patreon', 'direct_compute_sponsorship'].filter(value => manualOptionValues.includes(value)),
    ['buy_me_a_coffee', 'patreon', 'direct_compute_sponsorship'],
  )
  const directManualOption = findElements(
    tree,
    node => node.type === 'option' && node.props?.value === 'direct_compute_sponsorship',
  )[0]
  assert.equal(directManualOption.props.disabled, undefined)
  assert.match(elementText(directManualOption), /record only; excluded from target and perks/i)
  assert.match(text, /does not detect, collect, or automatically refund Vast\.ai sponsorships/i)

  globalThis.__supportManualSource = 'direct_compute_sponsorship'
  globalThis.__supportManualKind = 'one_time_contribution'
  const lockedDirectTree = expandElement(SupportPanel())
  const lockedKinds = findElements(
    lockedDirectTree,
    node => node.type === 'option' && ['one_time_contribution', 'refund', 'chargeback'].includes(node.props?.value),
  ).map(node => node.props.value)
  assert.deepEqual(lockedKinds, ['one_time_contribution', 'refund', 'chargeback'])
  const lockedDirectRecordButton = findElements(lockedDirectTree, node => (
    node.type === 'button' && elementText(node) === 'Save contribution record'
  ))[0]
  lockedDirectRecordButton.props.onClick()
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual({
    ...globalThis.__supportManualContributions.at(-1),
    idempotency_key: '<opaque>',
  }, {
    source: 'direct_compute_sponsorship', kind: 'one_time_contribution', amount_minor: 1,
    currency: 'USD', target_event_id: null, idempotency_key: '<opaque>',
  })
  delete globalThis.__supportManualSource
  delete globalThis.__supportManualKind
  globalThis.__supportManualContributions = []

  globalThis.__supportStore.supportAdmin = {
    ...globalThis.__supportStore.supportAdmin,
    development_cost_recovery: {
      target_minor: 100_000, currency: 'USD', state: 'recovered',
    },
  }
  const recoveredOwnerTree = expandElement(SupportPanel())
  const recoveredDirectOption = findElements(
    recoveredOwnerTree,
    node => node.type === 'option' && node.props?.value === 'direct_compute_sponsorship',
  )[0]
  assert.equal(recoveredDirectOption.props.disabled, undefined)
  assert.doesNotMatch(elementText(recoveredDirectOption), /locked/i)

  globalThis.__supportStore.supportAdmin = {
    ...globalThis.__supportStore.supportAdmin,
    development_cost_recovery: {
      target_minor: 100_000, currency: 'USD', state: 'locked',
    },
  }
  const relockedOwnerTree = expandElement(SupportPanel())
  assert.equal(findElements(
    relockedOwnerTree,
    node => node.type === 'option' && node.props?.value === 'direct_compute_sponsorship',
  )[0].props.disabled, undefined)

  const recoveredCatalog = structuredClone(publicSupport)
  recoveredCatalog.development_cost_recovery.state = 'recovered'
  recoveredCatalog.provider_catalog.providers[2] = {
    ...recoveredCatalog.provider_catalog.providers[2],
    enabled: true,
    configured: true,
    state: 'available',
    support_url: 'https://support.operator.com/maestro',
  }
  globalThis.__supportStore.supportCatalog = recoveredCatalog
  const freshRelockTree = expandElement(SupportPanel())
  assert.equal(findElements(
    freshRelockTree,
    node => node.type === 'a' && node.props?.href === 'https://support.operator.com/maestro',
  ).length, 0)
  assert.doesNotMatch(elementText(freshRelockTree), /Development costs recovered/)

  globalThis.__supportStore.supportAdmin = {
    ...globalThis.__supportStore.supportAdmin,
    development_cost_recovery: {
      target_minor: 100_000, currency: 'USD', state: 'recovered',
    },
  }
  const freshRecoveryTree = expandElement(SupportPanel())
  assert.equal(findElements(
    freshRecoveryTree,
    node => node.type === 'a' && node.props?.href === 'https://support.operator.com/maestro',
  ).length, 1)
  assert.match(elementText(freshRecoveryTree), /Development costs recovered/)

  globalThis.__supportStore.supportAdmin = {
    ...globalThis.__supportStore.supportAdmin,
    development_cost_recovery: {
      target_minor: 100_000, currency: 'USD', state: 'recovered', recovered_minor: 100_000,
    },
  }
  const malformedOwnerTree = expandElement(SupportPanel())
  assert.equal(findElements(
    malformedOwnerTree,
    node => node.type === 'option' && node.props?.value === 'direct_compute_sponsorship',
  )[0].props.disabled, undefined)
  assert.equal(findElements(
    malformedOwnerTree,
    node => node.type === 'a' && node.props?.href === 'https://support.operator.com/maestro',
  ).length, 0)
  assert.doesNotMatch(elementText(malformedOwnerTree), /Development costs recovered/)

  globalThis.__supportStore.supportAdmin = {
    ...globalThis.__supportStore.supportAdmin,
    development_cost_recovery: null,
  }
  const missingOwnerTree = expandElement(SupportPanel())
  assert.equal(findElements(
    missingOwnerTree,
    node => node.type === 'option' && node.props?.value === 'direct_compute_sponsorship',
  )[0].props.disabled, undefined)
  assert.equal(findElements(
    missingOwnerTree,
    node => node.type === 'a' && node.props?.href === 'https://support.operator.com/maestro',
  ).length, 0)
  assert.doesNotMatch(elementText(missingOwnerTree), /Development costs recovered/)
  assert.deepEqual(
    ['one_time_contribution', 'recurring_started', 'recurring_renewed', 'recurring_canceled', 'refund', 'chargeback']
      .filter(value => manualOptionValues.includes(value)),
    ['one_time_contribution', 'recurring_started', 'recurring_renewed', 'recurring_canceled', 'refund', 'chargeback'],
    'ordinary support sources retain every lifecycle kind independently of public-link marketing modes',
  )
  const recordOptionLabels = findElements(tree, node => node.type === 'option')
    .map(elementText)
    .filter(label => /\bRecord \d+\b/.test(label))
  assert.ok(recordOptionLabels.length > 1)
  assert.equal(new Set(recordOptionLabels).size, recordOptionLabels.length, 'contribution choices remain distinguishable')
  assert.match(text, /Enter cents for USD: 2500 = \$25\.00/)
  assert.match(text, /Amount \(USD cents\)/)
  assert.match(text, /2,500 cents \(\$25\.00\)/)
  assert.match(text, /One-time contribution|Refund|Chargeback|Recurring support canceled/)
  assert.match(text, /Adjustment does not match a recorded contribution/)
  assert.match(text, /One-time credit record · Pending/)
  assert.match(text, /Result-retention follow-up · In progress/)
  assert.match(text, /Backdated follow-up · Fulfilled/)
  assert.match(text, /Showing the 40 newest history items; 2 older items are not shown/)
  assert.doesNotMatch(text, /private@example|customer|invoice|payment method|prompt|media|job log/i)
  const eventLists = findElements(tree, node => node.type === 'ul' && node.props?.['aria-label'] === 'Private contribution history')
  assert.equal(eventLists.length, 1)
  assert.equal(findElements(eventLists[0], node => node.type === 'li').length, 40)
  const followUpList = findElements(tree, node => node.type === 'ul' && node.props?.['aria-label'] === 'Recorded support follow-up')[0]
  const knownFollowUpRow = findElements(followUpList, node => (
    node.type === 'li' && elementText(node).includes('One-time credit record')
  ))[0]
  const knownFollowUpLabel = findElements(knownFollowUpRow, node => (
    node.type === 'span' && String(node.props?.className || '').includes('font-semibold')
  ))[0]
  assert.equal(elementText(knownFollowUpLabel), 'One-time credit record')
  assert.doesNotMatch(elementText(knownFollowUpLabel), /one_time_credit_grant/)
  const knownFollowUpTechnicalDetails = findElements(knownFollowUpRow, node => (
    node.type === 'details' && elementText(node).includes('Follow-up type key: one_time_credit_grant')
  ))[0]
  assert.ok(knownFollowUpTechnicalDetails, 'the raw follow-up key stays under Technical details')
  assert.equal(
    elementText(findElements(knownFollowUpTechnicalDetails, node => node.type === 'summary')[0]),
    'Technical details',
  )
  const transitionButtons = findElements(tree, node => (
    node.type === 'button' && /^Mark /.test(elementText(node))
  ))
  assert.deepEqual(transitionButtons.map(elementText).sort(), [
    'Mark declined', 'Mark declined', 'Mark fulfilled',
    'Mark fulfilled', 'Mark in progress', 'Mark reversed',
  ])
  for (const button of transitionButtons) button.props.onClick()
  await Promise.resolve()
  assert.deepEqual(globalThis.__supportTransitions.map(input => input.status).sort(), [
    'declined', 'declined', 'fulfilled', 'fulfilled', 'in_progress', 'reversed',
  ])
  assert.equal(globalThis.__supportTransitions.every(input => input.proof_reference === null), true)
  assert.equal(globalThis.__supportTransitions.every(input => /^key_[0-9a-f]{64}$/.test(input.idempotency_key)), true)
  const manualRecordButton = findElements(tree, node => (
    node.type === 'button' && elementText(node) === 'Save contribution record'
  ))[0]
  assert.ok(manualRecordButton)
  assert.match(manualRecordButton.props.className, /\bmin-h-11\b/)
  globalThis.__supportManualDeferred = deferred()
  manualRecordButton.props.onClick()
  manualRecordButton.props.onClick()
  await Promise.resolve()
  assert.equal(
    globalThis.__supportManualContributions.length,
    1,
    'a second click cannot duplicate a contribution while the first request is in flight',
  )
  globalThis.__supportManualDeferred.resolve(null)
  await new Promise(resolve => setImmediate(resolve))
  delete globalThis.__supportManualDeferred
  globalThis.__supportManualContributions = []
  globalThis.__supportManualReject = true
  manualRecordButton.props.onClick()
  await new Promise(resolve => setImmediate(resolve))
  manualRecordButton.props.onClick()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(globalThis.__supportManualContributions.length, 2)
  assert.equal(
    globalThis.__supportManualContributions[0].idempotency_key,
    globalThis.__supportManualContributions[1].idempotency_key,
    'an unchanged ambiguous retry reuses its opaque key',
  )
  globalThis.__supportManualReject = false
  manualRecordButton.props.onClick()
  await new Promise(resolve => setImmediate(resolve))
  manualRecordButton.props.onClick()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(globalThis.__supportManualContributions.length, 4)
  assert.equal(
    globalThis.__supportManualContributions[1].idempotency_key,
    globalThis.__supportManualContributions[2].idempotency_key,
    'the successful attempt finishes the same ambiguous retry',
  )
  assert.notEqual(
    globalThis.__supportManualContributions[2].idempotency_key,
    globalThis.__supportManualContributions[3].idempotency_key,
    'a definitive success rotates the next record key',
  )
  assert.deepEqual({
    ...globalThis.__supportManualContributions[3],
    idempotency_key: '<opaque>',
  }, {
    source: 'buy_me_a_coffee', kind: 'one_time_contribution', amount_minor: 1,
    currency: 'USD', target_event_id: null, idempotency_key: '<opaque>',
  })
  assert.match(globalThis.__supportManualContributions[3].idempotency_key, /^key_[0-9a-f]{64}$/)

  const retainedRetryIdentities = new Map()
  const originalFingerprint = JSON.stringify([
    'buy_me_a_coffee', 'one_time_contribution', 1, 'USD', null,
  ])
  const editedFingerprint = JSON.stringify([
    'patreon', 'one_time_contribution', 1, 'USD', null,
  ])
  const ambiguousOriginalKey = manualContributionRetryIdentity(
    retainedRetryIdentities,
    originalFingerprint,
  )
  const editedKey = manualContributionRetryIdentity(
    retainedRetryIdentities,
    editedFingerprint,
  )
  const restoredOriginalKey = manualContributionRetryIdentity(
    retainedRetryIdentities,
    originalFingerprint,
  )
  assert.notEqual(editedKey, ambiguousOriginalKey)
  assert.equal(
    restoredOriginalKey,
    ambiguousOriginalKey,
    'ambiguous failure then edit and exact restore retains the original retry identity',
  )
  retainedRetryIdentities.delete(originalFingerprint)
  assert.notEqual(
    manualContributionRetryIdentity(retainedRetryIdentities, originalFingerprint),
    ambiguousOriginalKey,
    'definitive success rotates only the completed fingerprint identity',
  )

  const largeEvents = Array.from({ length: 20_000 }, (_, index) => ({
    sequence: index + 1,
    event_id: `evt_${(index + 1).toString(16).padStart(32, '0')}`,
    provider: 'manual_buy_me_a_coffee',
    source_reference: `key_${(index + 1).toString(16).padStart(64, '0')}`,
    kind: 'one_time_contribution',
    occurred_at: '2026-08-12T09:00:00Z',
    received_at: '2026-08-12T09:00:01Z',
    amount_minor: 100,
    currency: 'USD',
    contract_reference: null,
    related_reference: null,
    fulfillment_item: null,
    fulfillment_status: null,
    actor_reference: opaqueSupportKey('d'),
  }))
  let eventVisits = 0
  const observedEvents = new Proxy(largeEvents, {
    get(target, property, receiver) {
      if (typeof property === 'string' && /^\d+$/.test(property)) eventVisits += 1
      return Reflect.get(target, property, receiver)
    },
  })
  const largeTargetState = manualContributionTargetState(
    observedEvents,
    'buy_me_a_coffee',
    'USD',
  )
  assert.equal(largeTargetState.matchingFundingEvents.length, 20_000)
  assert.ok(eventVisits <= largeEvents.length * 2, 'manual target preprocessing remains linear')
  const recurringContract = opaqueSupportKey('e')
  const recurringStarted = {
    ...largeEvents[0], sequence: 1, event_id: supportEventId('1'),
    source_reference: opaqueSupportKey('1'), kind: 'recurring_started',
    contract_reference: recurringContract, occurred_at: '2026-08-12T09:00:00Z',
  }
  const recurringRenewed = {
    ...largeEvents[0], sequence: 2, event_id: supportEventId('2'),
    source_reference: opaqueSupportKey('2'), kind: 'recurring_renewed',
    contract_reference: recurringContract, occurred_at: '2026-08-12T11:00:00Z',
  }
  const earlierCancellationAppendedLater = {
    ...largeEvents[0], sequence: 3, event_id: supportEventId('3'),
    source_reference: opaqueSupportKey('3'), kind: 'recurring_canceled', amount_minor: 0,
    contract_reference: recurringContract, related_reference: opaqueSupportKey('1'),
    occurred_at: '2026-08-12T10:00:00Z',
  }
  const outOfOrderState = manualContributionTargetState([
    recurringStarted, recurringRenewed, earlierCancellationAppendedLater,
  ], 'buy_me_a_coffee', 'USD')
  assert.deepEqual(
    [...outOfOrderState.activeRecurringTargets],
    [recurringRenewed.event_id],
    'active recurring target ordering matches backend occurred_at then sequence authority',
  )
  globalThis.__supportStore.supportAdmin = {
    ...globalThis.__supportStore.supportAdmin,
    audit: {
      currency_totals_minor: { USD: 2_000_000 },
      events: largeEvents,
      discrepancies: [],
      fulfillment: [],
      incomplete: false,
    },
  }
  const largeAuditTree = expandElement(SupportPanel())
  assert.equal(findElements(
    findElements(largeAuditTree, node => node.props?.['aria-label'] === 'Private contribution history')[0],
    node => node.type === 'li',
  ).length, 40)

  globalThis.__supportStore.supportAdmin = {
    ...globalThis.__supportStore.supportAdmin,
    account: { ...summary, event_count: 0 },
    audit: { currency_totals_minor: {}, events: [], discrepancies: [], fulfillment: [], incomplete: false },
  }
  const emptyText = elementText(expandElement(SupportPanel()))
  assert.match(emptyText, /No net contribution total is recorded/)
  assert.match(emptyText, /No contribution activity is recorded/)
  assert.match(emptyText, /No items need review/)
  assert.match(emptyText, /No support follow-up is recorded/)

  globalThis.__supportStore.supportAdmin = {
    ...globalThis.__supportStore.supportAdmin,
    audit: { currency_totals_minor: {}, events: [], discrepancies: [], fulfillment: [], incomplete: true },
  }
  const incompleteText = elementText(expandElement(SupportPanel()))
  assert.match(incompleteText, /Some support data could not be loaded/)
  assert.match(incompleteText, /A blank section does not necessarily mean there are no records/)
  assert.match(incompleteText, /Contribution history is incomplete/)
  assert.doesNotMatch(incompleteText, /No contribution activity is recorded/)

  globalThis.__supportStore.supportAdmin = null
  globalThis.__supportStore.supportDetailsLoading = true
  const loadingText = elementText(expandElement(SupportPanel()))
  assert.match(loadingText, /Loading private support history/)
  assert.doesNotMatch(loadingText, /Private support history is unavailable/)

  globalThis.__supportStore.supportDetailsLoading = false
  globalThis.__supportNotice = { kind: 'error', text: 'Support details could not be refreshed.' }
  const errorTree = expandElement(SupportPanel())
  assert.match(elementText(errorTree), /Support details could not be refreshed/)
  assert.match(elementText(errorTree), /Private support history is unavailable/)
  assert.equal(findElements(errorTree, node => node.props?.role === 'alert').length, 1)

  delete globalThis.__supportNotice
  globalThis.__supportStore.supportAdmin = { account: summary, audit, responsible_use: responsibleUse.status, support_priority: publicSupport.support_priority }
  globalThis.__supportStore.supportAdminAccountId = 'stale-account'
  const staleText = elementText(expandElement(SupportPanel()))
  assert.doesNotMatch(staleText, /Private support history and follow-up/)

  globalThis.__supportStore.accountContext = {
    ...globalThis.__supportStore.accountContext,
    account: { id: 'ordinary-account', role: 'user' },
  }
  globalThis.__supportStore.supportAdminAccountId = account.id
  const nonOwnerText = elementText(expandElement(SupportPanel()))
  assert.doesNotMatch(nonOwnerText, /Manage support records|Private support history and follow-up/)
})

test('Support trigger stays discoverable with accounts off and describes optional account state truthfully', async () => {
  const { AccountSupportButton } = await loadAccountButton()
  const setOpen = value => { globalThis.__accountOpen = value }
  globalThis.__accountStore = {
    accountContext: { enabled: false }, accountDrawerOpen: false, setAccountDrawerOpen: setOpen,
  }
  const disabled = AccountSupportButton({ compact: false })
  assert.equal(disabled.props['aria-label'], 'Open support')
  assert.equal(elementText(disabled), 'Support')
  disabled.props.onClick()
  assert.equal(globalThis.__accountOpen, true)

  globalThis.__accountStore.accountContext = { enabled: true, authenticated: false, account: null }
  const anonymous = AccountSupportButton({ compact: false })
  assert.equal(anonymous.props['aria-haspopup'], 'dialog')
  assert.equal(anonymous.props['aria-label'], 'Open sign in and account help')
  assert.equal(elementText(anonymous), 'Sign in')
  anonymous.props.onClick()
  assert.equal(globalThis.__accountOpen, true)

  globalThis.__accountStore.accountContext = {
    enabled: true, authenticated: true, account: { username: 'LAN Owner' },
  }
  const authenticated = AccountSupportButton({ compact: false })
  assert.equal(authenticated.props['aria-label'], 'Open account and support')
  assert.equal(elementText(authenticated), 'Account & support')
})

test('public registration preserves one-time codes when post-registration hydration fails', async t => {
  const { AccountSupportDrawer } = await loadAccountButton()
  const previousWindow = globalThis.window
  const previousDocument = globalThis.document
  const previousHTMLElement = globalThis.HTMLElement
  globalThis.window = { location: { hostname: '127.0.0.1' } }
  globalThis.document = { activeElement: null }
  globalThis.HTMLElement = class HTMLElementFake {}
  globalThis.__accountActiveTab = 'account'
  globalThis.__accountEntryMode = 'register'
  globalThis.__accountRunEffects = true
  globalThis.__accountRefs = []
  globalThis.__accountRefIndex = 0
  globalThis.__accountStateWrites = []
  globalThis.__registerAccount = async () => ({
    account: { id: 'registered-account', username: 'New user', role: 'user' },
    recovery_codes: ['code-one', 'code-two'],
  })
  let authenticatedHandoffs = 0
  const noOp = async () => null
  globalThis.__accountStore = {
    accountDrawerOpen: true,
    setAccountDrawerOpen() {},
    accountContext: {
      enabled: true, authenticated: false, account: null, capabilities: [],
      reauthenticated: false, activation_state: 'ready', bootstrap_available: false,
      public_registration_available: true,
    },
    accessContext: { remote: false, accounts: { enabled: true } },
    accountProjectMigration: null,
    accountProjectMigrationLoading: false,
    accountContextLoading: false,
    accountSessions: [],
    accountUsers: [],
    accountDetailsLoading: false,
    loadAccountContext: async () => { throw new Error('temporary refresh failure') },
    loadAccountProjectMigration: noOp,
    migrateAccountProjects: noOp,
    bootstrapAccount: noOp,
    loginAccount: noOp,
    logoutAccount: noOp,
    reauthenticateAccount: noOp,
    recoverAccount: noOp,
    changeAccountPassword: noOp,
    rotateAccountRecoveryCodes: noOp,
    loadAccountSessions: noOp,
    revokeAccountSession: noOp,
    revokeAllAccountSessions: noOp,
    loadAccountUsers: noOp,
    createServerAccount: noOp,
    setServerAccountDisabled: noOp,
  }
  t.after(() => {
    globalThis.window = previousWindow
    globalThis.document = previousDocument
    globalThis.HTMLElement = previousHTMLElement
    delete globalThis.__accountActiveTab
    delete globalThis.__accountEntryMode
    delete globalThis.__accountRunEffects
    delete globalThis.__accountRefs
    delete globalThis.__accountRefIndex
    delete globalThis.__accountStateWrites
    delete globalThis.__registerAccount
    delete globalThis.__accountStore
  })

  const tree = expandElement(AccountSupportDrawer({
    required: true,
    onAuthenticated: () => { authenticatedHandoffs += 1 },
  }))
  const registerForm = findElements(
    tree,
    node => node.type === 'form' && /Create account/.test(elementText(node)),
  )[0]
  assert.ok(registerForm)
  registerForm.props.onSubmit({ preventDefault() {} })
  await new Promise(resolve => setImmediate(resolve))

  assert.ok(globalThis.__accountStateWrites.some(write => (
    Array.isArray(write.next) && write.next.join(',') === 'code-one,code-two'
  )), JSON.stringify(globalThis.__accountStateWrites))
  assert.equal(authenticatedHandoffs, 0, 'required bootstrap must wait until the codes are acknowledged')

  globalThis.__accountStore.accountContext = {
    ...globalThis.__accountStore.accountContext,
    authenticated: true,
    account: { id: 'registered-account', username: 'New user', role: 'user' },
  }
  globalThis.__accountStateWrites = []
  globalThis.__accountRefIndex = 0
  globalThis.__registerAccount = async () => ({
    account: globalThis.__accountStore.accountContext.account,
    recovery_codes: ['code-one', 'code-two'],
  })
  AccountSupportDrawer({ required: true, onAuthenticated: () => { authenticatedHandoffs += 1 } })
  assert.equal(
    globalThis.__accountStateWrites.some(write => Array.isArray(write.initial) && Array.isArray(write.next)),
    false,
    'the exact post-registration identity transition must not clear the newly issued codes',
  )
})

test('account drawer cannot dispatch a disable mutation for the current owner but can disable another user', async t => {
  const { AccountSupportDrawer } = await loadAccountButton()
  const previousWindow = globalThis.window
  const previousDocument = globalThis.document
  const mutationCalls = []
  const owner = {
    id: 'owner-account', username: 'Owner', role: 'owner', disabled: false,
    created_at: 1, has_email: false, passkey_credentials: 0,
    passkey_authentication_available: false,
  }
  const other = {
    ...owner, id: 'other-account', username: 'Other user', role: 'user',
  }
  const noOp = async () => null
  globalThis.window = { location: { hostname: '127.0.0.1' } }
  globalThis.document = {}
  globalThis.__accountActiveTab = 'account'
  globalThis.__accountStore = {
    accountDrawerOpen: true,
    setAccountDrawerOpen: () => {},
    accountContext: {
      enabled: true, authenticated: true, account: owner,
      capabilities: ['account.self', 'accounts.admin', 'services.admin', 'owner.admin'],
      reauthenticated: true, passkey_authentication_available: false,
      activation_state: 'ready', bootstrap_available: false,
    },
    accessContext: { remote: false, accounts: { enabled: true } },
    accountProjectMigration: null,
    accountProjectMigrationLoading: false,
    accountContextLoading: false,
    accountSessions: [],
    accountUsers: [owner, other],
    accountDetailsLoading: false,
    loadAccountContext: noOp,
    loadAccountProjectMigration: noOp,
    migrateAccountProjects: noOp,
    bootstrapAccount: noOp,
    loginAccount: noOp,
    logoutAccount: noOp,
    reauthenticateAccount: noOp,
    recoverAccount: noOp,
    changeAccountPassword: noOp,
    rotateAccountRecoveryCodes: noOp,
    loadAccountSessions: noOp,
    revokeAccountSession: noOp,
    revokeAllAccountSessions: noOp,
    loadAccountUsers: noOp,
    createServerAccount: noOp,
    setServerAccountDisabled: async (accountId, disabled) => {
      mutationCalls.push({ accountId, disabled })
    },
  }
  t.after(() => {
    globalThis.window = previousWindow
    globalThis.document = previousDocument
    delete globalThis.__accountActiveTab
    delete globalThis.__accountStore
  })

  const tree = expandElement(AccountSupportDrawer())
  const disableButtons = findElements(
    tree,
    node => node.type === 'button' && elementText(node) === 'Disable',
  )
  assert.equal(disableButtons.length, 2)
  assert.equal(disableButtons[0].props.disabled, true)
  assert.equal(disableButtons[1].props.disabled, false)
  assert.match(elementText(tree), /current owner account cannot be disabled/)

  const activate = async button => {
    if (button.props.disabled) return
    button.props.onClick()
    await new Promise(resolve => setImmediate(resolve))
  }
  await activate(disableButtons[0])
  assert.deepEqual(mutationCalls, [], 'disabled owner control must not reach the mutation/nonce path')
  await activate(disableButtons[1])
  assert.deepEqual(mutationCalls, [{ accountId: other.id, disabled: true }])
})

test('account and Support error copy maps backend codes and HTTP states without exposing raw server jargon', async () => {
  const [{ safeAccountErrorMessage, safeAccountHttpErrorMessage }, { safeSupportErrorMessage }] = await Promise.all([
    loadAccountButton(),
    loadSupportPanel(),
  ])
  assert.equal(
    safeAccountErrorMessage('bootstrap_complete'),
    'The first owner account already exists. Refresh account status, then sign in.',
  )
  assert.equal(
    safeAccountErrorMessage('account_request_failed', 9),
    'The account request could not be completed. Try again in about 9 seconds.',
  )
  for (const status of [403, 404, 409, 423, 503]) {
    const generic = safeAccountHttpErrorMessage(status)
    assert.equal(generic, 'The account request could not be completed.')
    assert.doesNotMatch(generic, /project|setup|unlock|access/i)
  }
  assert.equal(
    safeAccountHttpErrorMessage(404, 'account_request_failed', 0, 'project-migration'),
    'Project setup is not available on this Maestro host.',
  )
  assert.equal(
    safeAccountHttpErrorMessage(423, 'account_request_failed', 0, 'project-migration'),
    'Project access changed while setup was running. Refresh project access, then try again.',
  )
  assert.equal(
    safeAccountHttpErrorMessage(503, 'account_request_failed', 9, 'project-migration'),
    'Project access is temporarily unavailable. Try again after Maestro is ready. Try again in about 9 seconds.',
  )
  assert.equal(
    safeAccountHttpErrorMessage(409, 'project_migration_needs_attention', 0, 'project-migration'),
    'Some existing project folders need attention. Resolve each listed project on this computer, then retry. Removing a project is a separate action that Maestro will ask you to confirm. Account-based project filtering remains off, and existing project access stays unchanged.',
  )
  assert.equal(
    safeAccountHttpErrorMessage(409, 'project_migration_needs_attention'),
    'The account request could not be completed.',
  )
  assert.equal(
    safeSupportErrorMessage('responsible_use_notice_changed'),
    'The notice changed. Review the updated notice, then try again.',
  )
  assert.equal(safeSupportErrorMessage('unknown_backend_code'), 'Support details could not be refreshed.')
  assert.doesNotMatch(
    [
      safeAccountErrorMessage('account_request_failed'),
      safeAccountErrorMessage('bootstrap_complete'),
      safeSupportErrorMessage('unknown_backend_code'),
    ].join(' '),
    /loopback|bootstrap|raw private server detail/i,
  )
})

test('Support links require an available server HTTPS URL and priority copy requires an affected record', async () => {
  const catalog = structuredClone(publicSupport)
  catalog.provider_catalog.providers[0].support_url = 'https://buymeacoffee.com/maestro?contact=private'
  catalog.provider_catalog.providers[1].support_url = 'http://www.patreon.com/maestro'
  assert.deepEqual(
    visibleSupportProviders(catalog).map(provider => [provider.provider_id, provider.support_url]),
    [
      ['buy_me_a_coffee', null],
      ['patreon', null],
      ['direct_compute_sponsorship', null],
    ],
  )
  assert.deepEqual(visibleSupportProviders(catalog).filter(provider => provider.support_url), [])

  const safeCatalog = structuredClone(publicSupport)
  assert.deepEqual(
    visibleSupportProviders(safeCatalog).filter(provider => provider.support_url).map(provider => provider.provider_id),
    ['buy_me_a_coffee', 'patreon'],
  )

  const staleDirect = structuredClone(publicSupport)
  staleDirect.provider_catalog.providers[2] = {
    ...staleDirect.provider_catalog.providers[2],
    state: 'available',
    support_url: 'https://support.operator.com/maestro',
  }
  assert.equal(visibleSupportProviders(staleDirect)[2].support_url, null)
  assert.deepEqual(verifiedDevelopmentCostRecovery(staleDirect), {
    target_minor: 100_000, currency: 'USD', state: 'locked',
  })

  const malformedRecovery = structuredClone(staleDirect)
  malformedRecovery.development_cost_recovery.target_minor = 1
  assert.equal(verifiedDevelopmentCostRecovery(malformedRecovery), null)
  assert.equal(visibleSupportProviders(malformedRecovery)[2].support_url, null)

  const privacyBearingRecovery = structuredClone(staleDirect)
  privacyBearingRecovery.development_cost_recovery.recovered_minor = 100_000
  privacyBearingRecovery.development_cost_recovery.events = []
  privacyBearingRecovery.development_cost_recovery.subject = 'private-account'
  assert.equal(verifiedDevelopmentCostRecovery(privacyBearingRecovery), null)
  assert.equal(visibleSupportProviders(privacyBearingRecovery)[2].support_url, null)

  const missingRecovery = structuredClone(staleDirect)
  delete missingRecovery.development_cost_recovery
  assert.equal(verifiedDevelopmentCostRecovery(missingRecovery), null)
  assert.equal(visibleSupportProviders(missingRecovery)[2].support_url, null)

  const recovered = structuredClone(staleDirect)
  recovered.development_cost_recovery.state = 'recovered'
  assert.equal(visibleSupportProviders(recovered)[2].support_url, 'https://support.operator.com/maestro')

  const policy = {
    ...publicSupport.support_priority,
    exclusions: [{
      capability_id: 'exact-model', support_priority_eligible: false,
      marker: 'creator_terms_exclude_support_priority',
    }],
    notice: 'This exact model is excluded; submission remains available.',
  }
  const baseAccount = {
    event_count: 1, one_time_tier: 'supporter', recurring_tier: null,
    active_recurring_count: 0,
    benefits: {
      state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
      effective_benefits: [], recorded_eligibility: ['supporter_record'],
    },
  }
  assert.equal(affectedPriorityNotice(null, policy), null)
  assert.equal(affectedPriorityNotice(baseAccount, policy), null)
  assert.equal(affectedPriorityNotice({
    ...baseAccount,
    benefits: { ...baseAccount.benefits, recorded_eligibility: ['bounded_queue_priority'] },
  }, policy), policy.notice)

  assert.equal(nextAccountSupportTab('support', 'ArrowRight'), 'account')
  assert.equal(nextAccountSupportTab('account', 'ArrowLeft'), 'support')
  assert.equal(nextAccountSupportTab('account', 'Home'), 'support')
  assert.equal(nextAccountSupportTab('support', 'End'), 'account')
  assert.equal(nextAccountSupportTab('support', 'Enter'), null)
  assert.equal(responsibleUseIsAccepted(responsibleUse), false)
  assert.equal(responsibleUseIsAccepted({
    ...responsibleUse,
    status: { ...responsibleUse.status, accepted: true, state: 'accepted' },
  }), true)
  assert.equal(responsibleUseIsAccepted({
    notice: { ...responsibleUse.notice, version: 2, content_sha256: 'e'.repeat(64) },
    status: { ...responsibleUse.status, accepted: true, state: 'accepted' },
  }), false)
})

test('Support store loads public catalog with accounts off and gates self and admin on live capabilities', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  class StorageFake {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  globalThis.localStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout, setInterval, clearInterval, alert() {},
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  const calls = []
  let deferAdmin = false
  let deferFulfillment = false
  let deferContribution = false
  let contributionErrorStatus = 0
  let deferAccountContext = false
  let adminErrorStatus = 0
  let deferSelf = false
  let deferAcceptance = false
  let catalogPayload = publicSupport
  let adminRecoveryState = 'locked'
  let nextAccountContext = null
  let nextAccessAccounts = null
  const pendingSelf = []
  const pendingAcceptance = []
  const pendingAdmins = []
  const pendingFulfillment = []
  const pendingContributions = []
  const pendingAccountContexts = []
  const waitForPendingAdmins = async count => {
    for (let attempt = 0; attempt < 20 && pendingAdmins.length < count; attempt += 1) {
      await Promise.resolve()
    }
    assert.ok(pendingAdmins.length >= count, `expected ${count} pending admin request(s)`)
  }
  const waitForPendingFulfillment = async count => {
    for (let attempt = 0; attempt < 20 && pendingFulfillment.length < count; attempt += 1) {
      await Promise.resolve()
    }
    assert.ok(pendingFulfillment.length >= count, `expected ${count} pending fulfillment request(s)`)
  }
  const waitForPendingContributions = async count => {
    for (let attempt = 0; attempt < 20 && pendingContributions.length < count; attempt += 1) {
      await Promise.resolve()
    }
    assert.ok(pendingContributions.length >= count, `expected ${count} pending contribution request(s)`)
  }
  const allowancePayload = effectiveAllowance => ({
    ...recordedAllowance,
    effective_allowance: effectiveAllowance,
    sources: [{
      ...recordedAllowance.sources[0],
      granted_allowance: effectiveAllowance,
      effective_allowance: effectiveAllowance,
    }],
  })
  const adminPayload = eventCount => ({
    account_support: {
      recorded: {
        event_count: eventCount,
        active_recurring_count: 0,
        recorded_allowance: allowancePayload(eventCount * 100),
      },
      benefits: {
        state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
        effective_benefits: [], recorded_eligibility: [],
      },
    },
    responsible_use: responsibleUse.status,
    development_cost_recovery: {
      ...publicSupport.development_cost_recovery,
      state: adminRecoveryState,
    },
    support_priority: publicSupport.support_priority,
  })
  const selfPayload = (eventCount, responsible = responsibleUse) => ({
    ...publicSupport,
    account_support: {
      recorded: {
        event_count: eventCount,
        active_recurring_count: 0,
        recorded_allowance: allowancePayload(eventCount * 100),
      },
      benefits: {
        state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
        effective_benefits: [], recorded_eligibility: [],
      },
    },
    responsible_use: responsible,
  })
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    calls.push({ url, init })
    if (url.endsWith('/access-context') && nextAccessAccounts) return jsonResponse({
      remote: false, project_password_required: false, project_names_visible: true,
      machine_controls: true, custom_model_sources: true, catalog_model_downloads: true,
      classic_ui: false, cloudflare_enabled: false, share_url: '', share_flow: '',
      accounts: nextAccessAccounts,
    })
    if (url.endsWith('/support/catalog')) return jsonResponse(catalogPayload)
    if (url.endsWith('/account/context') && nextAccountContext) {
      if (deferAccountContext) return new Promise(resolve => pendingAccountContexts.push(resolve))
      return jsonResponse(nextAccountContext)
    }
    if (url.endsWith('/workspaces')) return jsonResponse({ workspaces: [], active: '' })
    if (url.endsWith('/support/self')) {
      if (deferSelf) return new Promise(resolve => { pendingSelf.push({ url, resolve }) })
      return jsonResponse(selfPayload(1))
    }
    if (url.endsWith('/support/responsible-use/accept')) {
      if (deferAcceptance) return new Promise(resolve => { pendingAcceptance.push(resolve) })
      return jsonResponse({ status: { ...responsibleUse.status, accepted: true, state: 'accepted' } })
    }
    if (url.endsWith('/fulfillment')) {
      if (deferFulfillment) return new Promise(resolve => { pendingFulfillment.push(resolve) })
      return jsonResponse(adminPayload(4))
    }
    if (url.endsWith('/contributions')) {
      if (contributionErrorStatus) return jsonResponse({
        detail: { code: 'manual_contribution_invalid', message: 'bounded failure' },
      }, contributionErrorStatus)
      if (deferContribution) return new Promise(resolve => { pendingContributions.push(resolve) })
      return jsonResponse(adminPayload(5))
    }
    if (url.includes('/support/admin/accounts/')) {
      if (adminErrorStatus) return jsonResponse({
        detail: { code: 'owner_required', message: 'raw private server detail' },
      }, adminErrorStatus)
      if (deferAdmin) return new Promise(resolve => { pendingAdmins.push({ url, resolve }) })
      return jsonResponse(adminPayload(1))
    }
    throw new Error(`Unexpected Support request: ${url} ${init.method || 'GET'}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
  })

  const bundled = await build({
    stdin: {
      contents: "export { useStore } from './src/stores/useStore.ts'",
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
  const { useStore } = await import(`${asDataModule(bundled.outputFiles[0].text)}#support-store`)
  const disabled = {
    enabled: false, authenticated: false, account: null, capabilities: [],
    reauthenticated: false, passkey_authentication_available: false,
  }
  useStore.setState({ accessContext: { accounts: disabled }, accountContext: disabled })
  await useStore.getState().loadSupportCatalog()
  assert.deepEqual(calls.map(call => call.url), ['/api/v1/support/catalog'])
  assert.equal(useStore.getState().supportCatalog.provider_catalog.provider_neutral, true)
  assert.equal(await useStore.getState().loadSupportSelf(), null)
  assert.equal(calls.length, 1)

  const account = {
    id: 'server-account', username: 'Owner', role: 'owner', disabled: false,
    created_at: 1, has_email: false, passkey_credentials: 0,
    passkey_authentication_available: false,
  }
  const restrictedOwnerContext = {
    enabled: true, authenticated: true, account,
    capabilities: ['account.self', 'accounts.admin'], reauthenticated: true,
    passkey_authentication_available: false,
  }
  nextAccountContext = restrictedOwnerContext
  useStore.setState({
    accessContext: { accounts: restrictedOwnerContext },
    accountContext: restrictedOwnerContext,
    accountUsers: [account],
    accountDrawerOpen: true,
  })
  deferSelf = true
  const olderSelf = useStore.getState().loadSupportSelf()
  assert.equal(pendingSelf.length, 1)
  catalogPayload = {
    ...publicSupport,
    provider_catalog: {
      ...publicSupport.provider_catalog,
      providers: publicSupport.provider_catalog.providers.map(provider => ({
        ...provider,
        description: `${provider.description} Current catalog.`,
      })),
    },
  }
  await useStore.getState().loadSupportCatalog()
  pendingSelf.shift().resolve(jsonResponse(selfPayload(1)))
  await olderSelf
  deferSelf = false
  assert.match(
    useStore.getState().supportCatalog.provider_catalog.providers[0].description,
    /Current catalog/,
    'an older self projection must not overwrite the newer public catalog',
  )
  await assert.rejects(useStore.getState().loadSupportAdmin(account.id), /selection changed|server-returned account/)
  assert.deepEqual(calls.map(call => call.url), [
    '/api/v1/support/catalog', '/api/v1/support/self', '/api/v1/support/catalog', '/api/v1/account/context',
  ])

  const fullOwnerContext = {
    ...restrictedOwnerContext,
    capabilities: ['account.self', 'accounts.admin', 'services.admin'],
  }
  nextAccountContext = fullOwnerContext
  useStore.setState(state => ({
    accessContext: { ...(state.accessContext || {}), accounts: fullOwnerContext },
    accountContext: fullOwnerContext,
  }))
  await useStore.getState().loadSupportAdmin(account.id)
  assert.equal(calls.at(-1).url, '/api/v1/support/admin/accounts/server-account')
  assert.equal(useStore.getState().supportAdmin.account.recorded_allowance.effective_allowance, 100)
  assert.equal(useStore.getState().supportAdmin.development_cost_recovery.state, 'locked')
  const fulfillmentInput = {
    target_event_id: supportEventId('a'), item: 'one_time_credit_grant', status: 'pending',
    idempotency_key: opaqueSupportKey('9'), proof_reference: null,
  }
  await useStore.getState().transitionSupportFulfillment(account.id, fulfillmentInput)
  assert.equal(calls.at(-1).url, '/api/v1/support/admin/accounts/server-account/fulfillment')
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), fulfillmentInput)
  assert.equal(useStore.getState().supportAdmin.account.event_count, 4)
  adminRecoveryState = 'recovered'
  const contributionInput = {
    source: 'direct_compute_sponsorship', kind: 'one_time_contribution', amount_minor: 2500,
    currency: 'USD', target_event_id: null, idempotency_key: opaqueSupportKey('7'),
  }
  await useStore.getState().recordSupportContribution(account.id, contributionInput)
  assert.equal(calls.at(-1).url, '/api/v1/support/admin/accounts/server-account/contributions')
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), contributionInput)
  assert.equal(useStore.getState().supportAdmin.account.event_count, 5)
  assert.equal(useStore.getState().supportAdmin.development_cost_recovery.state, 'recovered')

  adminRecoveryState = 'locked'
  await useStore.getState().recordSupportContribution(account.id, {
    ...contributionInput,
    source: 'buy_me_a_coffee',
    kind: 'refund',
    target_event_id: supportEventId('a'),
    idempotency_key: opaqueSupportKey('5'),
  })
  assert.equal(useStore.getState().supportAdmin.development_cost_recovery.state, 'locked')

  deferContribution = true
  const staleContribution = useStore.getState().recordSupportContribution(account.id, {
    ...contributionInput, idempotency_key: opaqueSupportKey('6'),
  })
  await waitForPendingContributions(1)
  useStore.getState().clearSupportAdmin()
  pendingContributions.shift()(jsonResponse(adminPayload(6)))
  await assert.rejects(staleContribution, /access or Support selection changed/)
  assert.equal(useStore.getState().supportAdmin, null)
  deferContribution = false
  await useStore.getState().loadSupportAdmin(account.id)

  contributionErrorStatus = 400
  await assert.rejects(useStore.getState().recordSupportContribution(account.id, contributionInput))
  assert.equal(useStore.getState().supportAdminAccountId, account.id)
  assert.notEqual(useStore.getState().supportAdmin, null)

  contributionErrorStatus = 409
  await assert.rejects(useStore.getState().recordSupportContribution(account.id, contributionInput))
  assert.equal(useStore.getState().supportAdminAccountId, account.id)
  assert.notEqual(useStore.getState().supportAdmin, null, 'a conflict refreshes the current private audit')

  contributionErrorStatus = 403
  await assert.rejects(useStore.getState().recordSupportContribution(account.id, contributionInput))
  assert.equal(useStore.getState().supportAdminAccountId, null)
  assert.equal(useStore.getState().supportAdmin, null)
  contributionErrorStatus = 0
  await useStore.getState().loadSupportAdmin(account.id)

  deferFulfillment = true
  const staleFulfillment = useStore.getState().transitionSupportFulfillment(account.id, {
    ...fulfillmentInput, status: 'in_progress', idempotency_key: opaqueSupportKey('8'),
  })
  await waitForPendingFulfillment(1)
  useStore.getState().clearSupportAdmin()
  pendingFulfillment.shift()(jsonResponse(adminPayload(5)))
  await assert.rejects(staleFulfillment, /access or Support selection changed/)
  assert.equal(useStore.getState().supportAdmin, null)
  deferFulfillment = false
  await useStore.getState().loadSupportAdmin(account.id)
  const adminCallCount = calls.filter(call => call.url.includes('/support/admin/accounts/')).length
  await assert.rejects(useStore.getState().loadSupportAdmin('not-returned'), /server-returned account/)
  assert.equal(calls.filter(call => call.url.includes('/support/admin/accounts/')).length, adminCallCount)

  adminErrorStatus = 403
  await assert.rejects(useStore.getState().loadSupportAdmin(account.id))
  assert.equal(useStore.getState().supportAdminAccountId, null)
  assert.equal(useStore.getState().supportAdmin, null)
  assert.equal(useStore.getState().supportDetailsLoading, false)
  adminErrorStatus = 0

  deferAdmin = true
  const staleAdmin = useStore.getState().loadSupportAdmin(account.id)
  await waitForPendingAdmins(1)
  useStore.setState(state => ({
    accountContext: { ...state.accountContext, reauthenticated: false },
  }))
  pendingAdmins.shift().resolve(jsonResponse(adminPayload(1)))
  await assert.rejects(staleAdmin, /access changed/)
  assert.equal(useStore.getState().supportAdmin, null)

  const secondAccount = { ...account, id: 'second-account', username: 'Second' }
  useStore.setState(state => ({
    accountContext: { ...state.accountContext, reauthenticated: true },
    accountUsers: [account, secondAccount],
  }))
  nextAccountContext = { ...fullOwnerContext, reauthenticated: true }
  deferAccountContext = true
  const staleContextSelection = useStore.getState().loadSupportAdmin(account.id)
  const currentContextSelection = useStore.getState().loadSupportAdmin(secondAccount.id)
  assert.equal(pendingAccountContexts.length, 2)
  pendingAccountContexts[1](jsonResponse(nextAccountContext))
  await waitForPendingAdmins(1)
  pendingAccountContexts[0](jsonResponse(nextAccountContext))
  await assert.rejects(staleContextSelection, /selection changed/)
  pendingAdmins.shift().resolve(jsonResponse(adminPayload(2)))
  await currentContextSelection
  assert.equal(useStore.getState().supportAdminAccountId, secondAccount.id)
  assert.equal(useStore.getState().supportAdmin.account.event_count, 2)
  pendingAccountContexts.length = 0
  deferAccountContext = false
  pendingAdmins.length = 0

  const firstSelection = useStore.getState().loadSupportAdmin(account.id)
  const secondSelection = useStore.getState().loadSupportAdmin(secondAccount.id)
  await waitForPendingAdmins(1)
  assert.equal(useStore.getState().supportAdmin, null, 'a new selection clears the old projection')
  await assert.rejects(firstSelection, /selection changed/)
  const secondPending = pendingAdmins.find(item => item.url.endsWith('/second-account'))
  secondPending.resolve(jsonResponse(adminPayload(2)))
  await secondSelection
  pendingAdmins.length = 0
  assert.equal(useStore.getState().supportAdminAccountId, secondAccount.id)
  assert.equal(useStore.getState().supportAdmin.account.event_count, 2)
  assert.equal(useStore.getState().supportAdmin.account.recorded_allowance.effective_allowance, 200)

  const ownerContext = {
    ...useStore.getState().accountContext,
    capabilities: ['account.self', 'accounts.admin', 'services.admin'],
    reauthenticated: true,
  }
  nextAccessAccounts = { ...ownerContext, reauthenticated: false }
  await useStore.getState().loadAccessContext()
  assert.equal(useStore.getState().accountContext.account.id, ownerContext.account.id)
  assert.equal(useStore.getState().supportAdminAccountId, null)
  assert.equal(useStore.getState().supportAdmin, null)
  assert.equal(useStore.getState().supportDetailsLoading, false)
  assert.deepEqual(useStore.getState().accountUsers.map(item => item.id), [account.id, secondAccount.id])

  useStore.setState({
    accessContext: { ...useStore.getState().accessContext, accounts: ownerContext },
    accountContext: ownerContext,
    accountUsers: [account, secondAccount],
  })
  nextAccessAccounts = { ...ownerContext, capabilities: ['account.self', 'accounts.admin'] }
  const capabilityLossAdmin = useStore.getState().loadSupportAdmin(secondAccount.id)
  await waitForPendingAdmins(1)
  await useStore.getState().loadAccessContext()
  assert.equal(useStore.getState().supportDetailsLoading, false)
  const capabilityLossPending = pendingAdmins.filter(item => item.url.endsWith('/second-account')).at(-1)
  capabilityLossPending.resolve(jsonResponse(adminPayload(3)))
  await capabilityLossAdmin
  assert.equal(useStore.getState().supportAdminAccountId, null)
  assert.equal(useStore.getState().supportAdmin, null, 'stale admin response stays invalid after access capability loss')

  nextAccountContext = {
    enabled: true, authenticated: true, account: secondAccount,
    capabilities: ['account.self'], reauthenticated: false,
    passkey_authentication_available: false,
  }
  useStore.setState(state => ({
    accessContext: { ...(state.accessContext || {}), accounts: state.accountContext },
  }))
  await useStore.getState().loadAccountContext()
  assert.equal(useStore.getState().supportSelf, null)
  assert.equal(useStore.getState().responsibleUse, null)
  assert.equal(useStore.getState().supportAdmin, null)
  assert.deepEqual(useStore.getState().accountUsers, [])

  deferSelf = true
  useStore.setState({ accountContext: {
    ...nextAccountContext, account, reauthenticated: false,
  } })
  const firstSelf = useStore.getState().loadSupportSelf()
  useStore.setState({ accountContext: nextAccountContext })
  const secondSelf = useStore.getState().loadSupportSelf()
  pendingSelf[1].resolve(jsonResponse(selfPayload(2)))
  await secondSelf
  pendingSelf[0].resolve(jsonResponse(selfPayload(1)))
  await firstSelf
  assert.equal(useStore.getState().supportSelf.account.event_count, 2)
  assert.equal(useStore.getState().supportSelf.account.recorded_allowance.effective_allowance, 200)

  deferAcceptance = true
  useStore.setState({
    accountContext: { ...nextAccountContext, account },
    responsibleUse,
  })
  const staleIdentityAcceptance = useStore.getState().acceptResponsibleUse(1, 'd'.repeat(64))
  useStore.setState({ accountContext: nextAccountContext, responsibleUse })
  pendingAcceptance.shift()(jsonResponse({
    status: { ...responsibleUse.status, accepted: true, state: 'accepted' },
  }))
  await assert.rejects(staleIdentityAcceptance, /account or notice changed/)
  assert.equal(useStore.getState().responsibleUse.status.accepted, false)

  const responsibleUseV2 = {
    notice: { ...responsibleUse.notice, version: 2, content_sha256: 'e'.repeat(64) },
    status: {
      ...responsibleUse.status, document_version: 2,
      content_sha256: 'e'.repeat(64),
    },
  }
  useStore.setState({ accountContext: nextAccountContext, responsibleUse })
  const staleNoticeAcceptance = useStore.getState().acceptResponsibleUse(1, 'd'.repeat(64))
  useStore.setState({ responsibleUse: responsibleUseV2 })
  pendingAcceptance.shift()(jsonResponse({
    status: { ...responsibleUse.status, accepted: true, state: 'accepted' },
  }))
  await assert.rejects(staleNoticeAcceptance, /account or notice changed/)
  assert.equal(responsibleUseIsAccepted(useStore.getState().responsibleUse), false)
})

test('Support store can load and mutate H3 legal-access location with owner gating', async t => {
  const originalFetch = globalThis.fetch
  const calls = []
  let h3Location = h3LegalAccessProjection
  const ownerContext = {
    enabled: true,
    authenticated: true,
    account: { id: 'owner-account', username: 'Owner', role: 'owner', disabled: false },
    capabilities: ['account.self', 'accounts.admin', 'services.admin'],
    reauthenticated: true,
    passkey_authentication_available: false,
  }
  const userContext = {
    ...ownerContext,
    account: { ...ownerContext.account, id: 'user-account', role: 'user' },
    reauthenticated: false,
  }
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    calls.push({ url, init })
    if (url.endsWith('/h3/legal-access')) {
      if ((init.method || 'GET') === 'PUT') {
        h3Location = {
          ...h3Location,
          territory_code: init?.body ? JSON.parse(init.body).territory_code : h3Location.territory_code,
        }
        return jsonResponse(h3Location)
      }
      return jsonResponse(h3Location)
    }
    throw new Error(`Unexpected Support request: ${url}`)
  }
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const bundled = await build({
    stdin: {
      contents: "export { useStore } from './src/stores/useStore.ts'",
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
  const { useStore } = await import(`${asDataModule(bundled.outputFiles[0].text)}#support-store-h3`)

  useStore.setState({
    accessContext: { accounts: ownerContext },
    accountContext: userContext,
    supportH3LegalAccess: null,
    supportH3LegalAccessLoading: false,
    supportH3LegalAccessError: null,
  })
  assert.equal(await useStore.getState().loadH3LegalAccessState(), null)
  assert.equal(calls.length, 0)

  useStore.setState({
    accountContext: ownerContext,
    accessContext: { accounts: ownerContext },
  })
  const loaded = await useStore.getState().loadH3LegalAccessState()
  assert.equal(loaded?.territory_code, 'US')
  assert.equal(calls.at(-1).url, '/api/v1/h3/legal-access')
  assert.equal(calls.at(-1).init.method || 'GET', 'GET')
  assert.equal(useStore.getState().supportH3LegalAccess?.territory_code, 'US')
  assert.equal(useStore.getState().supportH3LegalAccess?.availability_status, 'available')

  const updated = await useStore.getState().setH3LegalAccessLocation({
    territory_code: 'JP',
    owner_attested: true,
    license_revision: h3LegalAccessProjection.license_revision,
    license_sha256: h3LegalAccessProjection.license_sha256,
  })
  assert.equal(updated?.territory_code, 'JP')
  assert.equal(updated?.availability_status, 'available')
  assert.equal(useStore.getState().supportH3LegalAccess?.territory_code, 'JP')
  assert.equal(calls.at(-1).url, '/api/v1/h3/legal-access')
  assert.equal(calls.at(-1).init.method || 'GET', 'PUT')
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), {
    territory_code: 'JP',
    owner_attested: true,
    license_revision: h3LegalAccessProjection.license_revision,
    license_sha256: h3LegalAccessProjection.license_sha256,
  })

  useStore.setState({
    accountContext: { ...ownerContext, account: { ...ownerContext.account, role: 'user' }, reauthenticated: false },
  })
  await assert.rejects(useStore.getState().setH3LegalAccessLocation({
    territory_code: 'CA',
    owner_attested: true,
    license_revision: h3LegalAccessProjection.license_revision,
    license_sha256: h3LegalAccessProjection.license_sha256,
  }))
  assert.equal(useStore.getState().supportH3LegalAccessLoading, false)
  assert.equal(useStore.getState().supportH3LegalAccessError, 'Sign in as a reauthenticated owner to update H3 location.')
  assert.equal(calls.filter(call => call.url.endsWith('/h3/legal-access')).length, 2)
})

test('account drawer keeps secrets ephemeral and uses the shared accessible modal contract', async () => {
  const [source, supportSource, appSource] = await Promise.all([
    readFile(componentUrl, 'utf8'),
    readFile(supportPanelUrl, 'utf8'),
    readFile(appUrl, 'utf8'),
  ])
  assert.doesNotMatch(source, /localStorage|sessionStorage|console\./)
  assert.match(source, /installModalFocus\(/)
  assert.match(source, /role="dialog"/)
  assert.match(source, /aria-modal="true"/)
  assert.match(source, /setOneTimeCodes\(\[\]\)/)
  assert.match(source, /Maestro will not show this set again/)
  assert.match(source, /registerAccount\(\{ username, password, email, deviceLabel \}\)/)
  assert.match(source, /const dismissOneTimeCodes = useCallback\([\s\S]*setOneTimeCodes\(\[\]\)[\s\S]*onAuthenticated\?\.\(\)/)
  assert.match(source, /registerAccount\([\s\S]*setOneTimeCodes\(codes\)[\s\S]*await loadContext\(\)/)
  assert.match(appSource, /onAuthenticated=\{accountRecovery \? finishAccountRecovery : undefined\}/)
  assert.match(appSource, /const finishAccountRecovery = \(\) => \{[\s\S]*setAccountDrawerOpen\(false\)[\s\S]*retryBootstrap\(\)/)
  assert.match(appSource, /const retryBootstrap = \(\) => \{[\s\S]*setBootstrapAttempt\(value => value \+ 1\)/)
  assert.match(source, /await loadContext\(\)/)
  assert.match(source, /await login\(\{ username, password, deviceLabel \}\)[\s\S]*await loadSessions\(\)/)
  assert.match(source, /await loadContext\(\)[\s\S]*await loadSessions\(\)/)
  assert.doesNotMatch(source, /state => state\.registerAccount/)
  assert.match(source, /context\.capabilities\.includes\('accounts\.admin'\)/)
  assert.match(source, /context\.bootstrap_available === true/)
  assert.match(source, /user\.has_email \? ' · email recorded' : ''/)
  assert.doesNotMatch(source, /recovery email recorded/)
  assert.match(source, /role="tablist"/)
  assert.match(source, /tabIndex=\{activeTab === 'support' \? 0 : -1\}/)
  assert.match(source, /onKeyDown=\{event => handleTabKeyDown\(event, 'account'\)\}/)
  assert.match(source, /aria-labelledby=\{accountTabId\}/)
  assert.match(source, /if \(tab === 'support'\)[^]*lifecycleRef\.current\.closed\(\)[^]*clearSensitive\(\)/)
  assert.match(source, /<SupportPanel \/>/)
  const submitButtons = [...source.matchAll(/<button\b(?:(?!<\/button>)[\s\S])*?<\/button>/g)]
    .map(match => match[0])
    .filter(button => /type="submit"/.test(button))
  const primaryAuthActions = [
    'Create owner account',
    'Sign in',
    'Confirm password',
    'Create user',
  ]
  for (const label of primaryAuthActions) {
    const actions = submitButtons.filter(button => button.includes(`\n                    ${label}\n`)
      || button.includes(`\n                  ${label}\n`)
      || button.includes(`\n                          ${label}\n`))
    assert.equal(actions.length, 1, `${label} must remain one exact primary submit action`)
    assert.match(actions[0], /className="[^"]*\bbg-bg-active\b[^"]*\btext-text-primary\b[^"]*\bhover:bg-bg-hover\b[^"]*\bdisabled:opacity-100\b[^"]*"/)
    assert.doesNotMatch(actions[0], /\bbg-accent-blue\b|\btext-white\b|\bhover:opacity-90\b|\bdisabled:opacity-50\b/)
  }
  const headerCloseButton = source.match(/<button\s+ref=\{closeRef\}[^]*?<\/button>/)?.[0]
  assert.ok(headerCloseButton, 'the visible Support drawer close button must remain present')
  assert.match(headerCloseButton, /className="[^"]*\bh-11\b[^"]*\bw-11\b[^"]*\bp-0\b[^"]*\bmd:h-auto\b[^"]*\bmd:w-auto\b[^"]*\bmd:p-1\.5\b[^"]*"/)
  const scrollRegion = source.match(/<div\s+role="region"[^]*?className="[^"]*\boverflow-y-auto\b[^"]*"\s*>/)?.[0]
  assert.ok(scrollRegion, 'the drawer scroll container must remain an explicit accessible region')
  assert.match(scrollRegion, /aria-label=\{accountsEnabled \? 'Support and account content' : 'Support content'\}/)
  assert.match(scrollRegion, /tabIndex=\{activeTab === 'support' \? 0 : -1\}/)
  assert.match(supportSource, /rel="noopener noreferrer"/)
  assert.match(supportSource, /Acknowledging this notice does not review, restrict, or approve what you create/)
  assert.match(supportSource, /Support first helps cover \$1,000 in development costs/)
  assert.match(supportSource, /After that, it can help fund hosting Maestro Continuum with more compute/)
  assert.match(supportSource, /running total and contribution history stay private/)
  assert.doesNotMatch(supportSource, /direct compute sponsorship/i)
  assert.match(supportSource, /thank-you benefits/)
  assert.match(supportSource, /Support benefits are not active/)
  assert.match(supportSource, /not active on the current host and does not change generation, queueing, or retries/)
  assert.match(supportSource, /summary\.benefits\.state === 'active'/)
  assert.match(supportSource, /Active hosted queue allowance/)
  assert.match(supportSource, /Technical details · owner credit test/)
  assert.match(supportSource, /Auto-refills to/)
  assert.match(supportSource, /without changing real credits, access, or queue priority/)
  assert.doesNotMatch(supportSource, /localStorage|sessionStorage|console\.|subject_key|source_event_key|account_id|email|customer|invoice|payment_method|credential|secret|@|\$600|tax|end-to-end|passkey/i)
  assert.doesNotMatch(supportSource, /['"]SLA['"]/)
  assert.match(supportSource, /available only after the owner recently confirmed their password/i)
  assert.match(supportSource, /summary\.benefits\.state === 'recorded_not_enforced'/)
  assert.match(supportSource, /function adminSupportErrorMessage[^]*Private support history could not be refreshed[^]*Confirm the owner password and try again/)
  assert.match(supportSource, /const chooseAdminAccount[^]*setNotice\(\{ kind: 'error', text: adminSupportErrorMessage\(error\) \}\)/)
  assert.match(supportSource, /PRIVATE_SUPPORT_AUDIT_DISPLAY_TTL_MS = 4 \* 60 \* 1000/)
  assert.match(supportSource, /if \(adminAccountId !== selectedAdminAccountId\) \{[^]*setSelectedUserIndex\(''\)[^]*clearAdmin\(\)/)
  assert.match(supportSource, /window\.setTimeout[^]*supportAdminAccountId === displayAccountId[^]*supportAdmin === displayProjection[^]*setSelectedUserIndex\(''\)[^]*clearAdmin\(\)/)
  assert.match(supportSource, /useEffect\(\(\) => \(\) => \{[^]*adminSelectionEpochRef\.current \+= 1[^]*clearAdmin\(\)[^]*\}, \[clearAdmin\]\)/)
  assert.match(supportSource, /pending: \['in_progress', 'fulfilled', 'declined'\]/)
  assert.match(supportSource, /fulfilled: \['reversed'\]/)
  assert.match(supportSource, /className="min-h-11[^]*Record pending follow-up/)
  assert.equal([...supportSource.matchAll(/Record \{event\.sequence\.toLocaleString\(\)\}/g)].length, 2)
  assert.doesNotMatch(supportSource, /<textarea|Fulfillment note/i)
  assert.doesNotMatch(supportSource, /onClick=.*(?:refund|chargeback|payment|provider)/i)
  assert.match(source, /Existing project access may also depend on this browser or a project password/)
  assert.match(source, /Sign out all account sessions/)
  assert.match(source, /These controls affect account sign-in only[^]*Separate browser or project-password access is unchanged/)
  assert.match(source, /Any separate browser or project-password access remains unchanged/)
  assert.match(source, /Direct or local-network sign-in/)
  assert.match(source, /accountsEnabled[^]*View optional ways to support Maestro\. Support does not change access or available controls\./)
  assert.match(source, /safeAccountHttpErrorMessage\(error\.status, error\.code, error\.retryAfter\)/)
  assert.match(source, /safeAccountHttpErrorMessage\([^]*'project-migration'/)
  assert.match(supportSource, /safeSupportErrorMessage\(error\.code, error\.retryAfter\)/)
  assert.doesNotMatch(source, /return `\$\{error\.message\}/)
  assert.doesNotMatch(supportSource, /return `\$\{error\.message\}/)
  assert.doesNotMatch(source, /account cookie|local bootstrap|same-origin secure cookies|Project access stays separate|signing out other devices/i)
  assert.match(appSource, /context\.accounts\?\.enabled === true/)
  assert.match(appSource, /AccountSupportDrawer/)
})

test('account drawer uses the eight-character minimum for every account password-setting field', async () => {
  const source = await readFile(componentUrl, 'utf8')
  assert.equal([...source.matchAll(/minLength=\{8\}/g)].length, 9)
  assert.doesNotMatch(source, /minLength=\{12\}/)
  assert.equal([...source.matchAll(/label="Confirm password"/g)].length, 2)
  assert.equal([...source.matchAll(/label="Confirm new password"/g)].length, 2)
  assert.match(source, /aria-label=\{passwordVisible \? `Hide \$\{label\.toLowerCase\(\)\}` : `Show \$\{label\.toLowerCase\(\)\}`\}/)
  assert.match(source, /className="[^\"]*min-h-11[^\"]*min-w-11[^\"]*"/)
  assert.match(source, /Copy all/)
  assert.match(source, /Download/)
  assert.match(source, /I stored these recovery codes somewhere private/)
  assert.match(source, /disabled=\{!savedAcknowledged\}/)
  assert.match(source, /Continue with saved codes/)
})

test('Welcome shows account membership, donor tier, and only delivered or active perks without financial detail', async () => {
  const source = await readFile(welcomeUrl, 'utf8')
  assert.match(source, /Account membership and supporter status/)
  assert.match(source, /supporterTierLabels\(supporterAccount\)/)
  assert.match(source, /supporter_recognition/)
  assert.match(source, /effective_benefits/)
  assert.match(source, /Delivered or active now/)
  assert.match(source, /No supporter tier is currently recorded/)
  assert.doesNotMatch(source, /amount_minor|currency_totals|audit|event_count|recorded_allowance|effective_allowance/)
})

test('account tab skips the scroll region while keeping the first sign-in field tabbable', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const scrollRegion = source.match(/<div\s+role="region"[^]*?className="[^"]*\boverflow-y-auto\b[^"]*"\s*>/)?.[0]
  assert.ok(scrollRegion, 'the shared drawer scroll region must remain present')
  assert.match(scrollRegion, /tabIndex=\{activeTab === 'support' \? 0 : -1\}/)

  const fieldInput = source.match(/function Field\([^]*?return \([^]*?<input[^]*?\/>/)?.[0]
  assert.ok(fieldInput, 'the shared Field input must remain present')
  assert.match(fieldInput, /tabIndex=\{0\}/)

  const signInForm = source.match(/<h3 className="text-xs font-semibold text-text-primary">Sign in<\/h3>[^]*?<\/form>/)?.[0]
  assert.ok(signInForm, 'the account sign-in form must remain present')
  assert.match(signInForm.match(/<Field\b[^>]*\/>/)?.[0] || '', /label="Username"/)
})
