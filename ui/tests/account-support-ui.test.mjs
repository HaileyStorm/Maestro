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
  createServerAccount,
  fetchAdminAccountSupport,
  fetchAccountContext,
  fetchResponsibleUse,
  fetchSupportCatalog,
  fetchSupportSelf,
  loginAccount,
  logoutAccount,
  reauthenticateAccount,
  recoverAccount,
  revokeAccountSession,
  revokeAllAccountSessions,
  rotateAccountRecoveryCodes,
  setServerAccountDisabled,
} from '../src/api/client.ts'
import { createAccountDrawerLifecycle } from '../src/components/AccountSupport/accountDrawerLifecycle.ts'
import {
  affectedPriorityNotice,
  availableSupportProviders,
  nextAccountSupportTab,
  responsibleUseIsAccepted,
} from '../src/components/AccountSupport/supportPresentation.ts'

const componentUrl = new URL('../src/components/AccountSupport/AccountSupportDrawer.tsx', import.meta.url)
const supportPanelUrl = new URL('../src/components/AccountSupport/SupportPanel.tsx', import.meta.url)
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

  assert.deepEqual(calls.slice(-5), [
    '/api/v1/account/nonce',
    '/api/v1/account/login',
    '/api/v1/account/context',
    '/api/v1/account/sessions',
    '/api/v1/account/users',
  ])
  assert.equal(useStore.getState().accountSessions[0].id, 'current-handle')
  assert.equal(useStore.getState().accountUsers[0].username, 'Owner')
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
      bootstrap_available: true,
    })
  }, async () => {
    const context = await fetchAccountContext()
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

const publicSupport = {
  schema_version: 1,
  provider_catalog: {
    schema_version: 1,
    provider_neutral: true,
    providers: [{
      provider_id: 'configured', display_name: 'Configured support', funding_modes: ['one_time'],
      description: 'Server-approved support.', enabled: true, configured: true,
      state: 'available', support_url: 'https://support.example.test/maestro',
    }, {
      provider_id: 'disabled', display_name: 'Disabled support', funding_modes: ['recurring'],
      description: 'Not configured.', enabled: false, configured: false,
      state: 'disabled', support_url: null,
    }],
  },
  benefit_availability: {
    scheduler_enforcement_enabled: false, effective_benefits: [], state: 'recorded_not_enforced',
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
        },
        benefits: {
          state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
          effective_benefits: [], recorded_eligibility: ['one_time_credit_eligibility'],
        },
      },
      responsible_use: responsibleUse,
    })
    if (String(url).endsWith('/support/responsible-use')) return jsonResponse(responsibleUse)
    if (String(url).endsWith('/support/responsible-use/accept')) {
      return jsonResponse({ status: { ...responsibleUse.status, accepted: true, state: 'accepted' } })
    }
    if (String(url).includes('/support/admin/accounts/')) return jsonResponse({
      account_support: {
        recorded: { event_count: 1, active_recurring_count: 1, amount_minor: 9999 },
        benefits: {
          state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
          effective_benefits: [], recorded_eligibility: ['periodic_credit_eligibility'],
        },
      },
      responsible_use: responsibleUse.status,
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
    assert.equal(catalog.provider_catalog.provider_neutral, true)
    assert.equal(self.account.event_count, 2)
    assert.equal(notice.notice.version, 1)
    assert.equal(accepted.status.accepted, true)
    assert.equal(admin.account.event_count, 1)
    const safe = JSON.stringify({ self, admin })
    assert.doesNotMatch(safe, /currency_totals_minor|amount_minor|subject_key|private@example|provider_secret/)
  })

  assert.deepEqual(calls.map(call => [call.init.method || 'GET', call.url]), [
    ['GET', '/api/v1/support/catalog'],
    ['GET', '/api/v1/support/self'],
    ['GET', '/api/v1/support/responsible-use'],
    ['POST', '/api/v1/support/responsible-use/accept'],
    ['GET', '/api/v1/support/admin/accounts/account%2Fid'],
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
  assert.equal(calls[0].init.body, undefined)
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
            export const useEffect = () => {}
            export const useId = () => 'account-title'
            export const useMemo = value => value()
            export const useRef = value => ({ current: value })
            export const useState = value => [value, () => {}]
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
          if (args.path === 'api') return { contents: 'export class AccountApiError extends Error {}' }
          if (args.path === 'focus') return { contents: 'export const installModalFocus = () => () => {}' }
          if (args.path === 'store') return { contents: 'export const useStore = selector => selector(globalThis.__accountStore)' }
          return null
        })
      },
    }],
  })
  return import(asDataModule(result.outputFiles[0].text))
}

test('Support trigger stays discoverable with accounts off and describes optional account state truthfully', async () => {
  const { AccountSupportButton } = await loadAccountButton()
  const setOpen = value => { globalThis.__accountOpen = value }
  globalThis.__accountStore = {
    accountContext: { enabled: false }, accountDrawerOpen: false, setAccountDrawerOpen: setOpen,
  }
  const disabled = AccountSupportButton({ compact: false })
  assert.equal(disabled.props['aria-label'], 'Open Support')
  disabled.props.onClick()
  assert.equal(globalThis.__accountOpen, true)

  globalThis.__accountStore.accountContext = { enabled: true, authenticated: false, account: null }
  const anonymous = AccountSupportButton({ compact: false })
  assert.equal(anonymous.props['aria-haspopup'], 'dialog')
  assert.equal(anonymous.props['aria-label'], 'Open Support')
  anonymous.props.onClick()
  assert.equal(globalThis.__accountOpen, true)

  globalThis.__accountStore.accountContext = {
    enabled: true, authenticated: true, account: { username: 'LAN Owner' },
  }
  const authenticated = AccountSupportButton({ compact: false })
  assert.equal(authenticated.props['aria-label'], 'Open Support and account for LAN Owner')
})

test('Support links require an available server HTTPS URL and priority copy requires an affected record', async () => {
  const catalog = structuredClone(publicSupport)
  catalog.provider_catalog.providers.push(
    { ...catalog.provider_catalog.providers[0], provider_id: 'query', support_url: 'https://support.example.test/maestro?contact=private' },
    { ...catalog.provider_catalog.providers[0], provider_id: 'http', support_url: 'http://support.example.test/maestro' },
    { ...catalog.provider_catalog.providers[0], provider_id: 'unconfigured', state: 'unconfigured' },
  )
  assert.deepEqual(availableSupportProviders(catalog).map(provider => provider.provider_id), ['configured'])

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
    benefits: { ...baseAccount.benefits, recorded_eligibility: ['one_time_credit_eligibility'] },
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
  let deferSelf = false
  let deferAcceptance = false
  let nextAccountContext = null
  const pendingSelf = []
  const pendingAcceptance = []
  const pendingAdmins = []
  const adminPayload = eventCount => ({
    account_support: {
      recorded: { event_count: eventCount, active_recurring_count: 0 },
      benefits: {
        state: 'recorded_not_enforced', scheduler_enforcement_enabled: false,
        effective_benefits: [], recorded_eligibility: [],
      },
    },
    responsible_use: responsibleUse.status,
    support_priority: publicSupport.support_priority,
  })
  const selfPayload = (eventCount, responsible = responsibleUse) => ({
    ...publicSupport,
    account_support: {
      recorded: { event_count: eventCount, active_recurring_count: 0 },
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
    if (url.endsWith('/support/catalog')) return jsonResponse(publicSupport)
    if (url.endsWith('/account/context') && nextAccountContext) return jsonResponse(nextAccountContext)
    if (url.endsWith('/support/self')) {
      if (deferSelf) return new Promise(resolve => { pendingSelf.push({ url, resolve }) })
      return jsonResponse(selfPayload(1))
    }
    if (url.endsWith('/support/responsible-use/accept')) {
      if (deferAcceptance) return new Promise(resolve => { pendingAcceptance.push(resolve) })
      return jsonResponse({ status: { ...responsibleUse.status, accepted: true, state: 'accepted' } })
    }
    if (url.includes('/support/admin/accounts/')) {
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
  useStore.setState({
    accountContext: {
      enabled: true, authenticated: true, account,
      capabilities: ['account.self', 'accounts.admin'], reauthenticated: true,
      passkey_authentication_available: false,
    },
    accountUsers: [account],
    accountDrawerOpen: true,
  })
  await useStore.getState().loadSupportSelf()
  await assert.rejects(useStore.getState().loadSupportAdmin(account.id), /server-returned account/)
  assert.deepEqual(calls.map(call => call.url), ['/api/v1/support/catalog', '/api/v1/support/self'])

  useStore.setState(state => ({
    accountContext: { ...state.accountContext, capabilities: ['account.self', 'accounts.admin', 'services.admin'] },
  }))
  await useStore.getState().loadSupportAdmin(account.id)
  assert.equal(calls.at(-1).url, '/api/v1/support/admin/accounts/server-account')
  await assert.rejects(useStore.getState().loadSupportAdmin('not-returned'), /server-returned account/)
  assert.equal(calls.at(-1).url, '/api/v1/support/admin/accounts/server-account')

  deferAdmin = true
  const staleAdmin = useStore.getState().loadSupportAdmin(account.id)
  await Promise.resolve()
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
  const firstSelection = useStore.getState().loadSupportAdmin(account.id)
  const secondSelection = useStore.getState().loadSupportAdmin(secondAccount.id)
  assert.equal(useStore.getState().supportAdmin, null, 'a new selection clears the old projection')
  const secondPending = pendingAdmins.find(item => item.url.endsWith('/second-account'))
  const firstPending = pendingAdmins.find(item => item.url.endsWith('/server-account'))
  secondPending.resolve(jsonResponse(adminPayload(2)))
  await secondSelection
  firstPending.resolve(jsonResponse(adminPayload(1)))
  await firstSelection
  assert.equal(useStore.getState().supportAdminAccountId, secondAccount.id)
  assert.equal(useStore.getState().supportAdmin.account.event_count, 2)

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
  assert.match(source, /context\.capabilities\.includes\('accounts\.admin'\)/)
  assert.match(source, /context\.bootstrap_available === true/)
  assert.match(source, /role="tablist"/)
  assert.match(source, /tabIndex=\{activeTab === 'support' \? 0 : -1\}/)
  assert.match(source, /onKeyDown=\{event => handleTabKeyDown\(event, 'account'\)\}/)
  assert.match(source, /aria-labelledby=\{accountTabId\}/)
  assert.match(source, /if \(tab === 'support'\)[^]*lifecycleRef\.current\.closed\(\)[^]*clearSensitive\(\)/)
  assert.match(source, /<SupportPanel \/>/)
  assert.match(supportSource, /rel="noopener noreferrer"/)
  assert.match(supportSource, /This is an acknowledgement[^]*not moderation, classification, or permission/)
  assert.match(supportSource, /already spent hundreds on Codex/)
  assert.match(supportSource, /When support is sufficient, I will host Maestro \/ Continuum with more compute/)
  assert.match(supportSource, /not enforced yet/)
  assert.doesNotMatch(supportSource, /localStorage|sessionStorage|console\.|currency_totals_minor|amount_minor|subject_key|source_event_key|@|\$600|SLA|tax|end-to-end|passkey/i)
  assert.match(appSource, /context\.accounts\?\.enabled === true/)
  assert.match(appSource, /AccountSupportDrawer/)
})
