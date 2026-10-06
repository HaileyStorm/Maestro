import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'
import { readCompositionRecoveryHandle, startCompositionRecovery } from '../src/lib/compositionRecovery.ts'
import { createLlmRequestId, fetchCompositionRecovery, submitCompositionRecovery } from '../src/api/client.ts'

const scope = { accountId: 'owner-a', workspace: 'project-a', jobId: 'a'.repeat(32), createdAt: 1000 }
const requestId = 'b6a6c2ec-fc34-4197-9008-e07bbaf27910'
const nextId = '5656c2ec-fc34-4197-9008-e07bbaf27911'
function storage() {
  const records = new Map()
  return { getItem: key => records.get(key) ?? null, setItem: (key, value) => records.set(key, value) }
}
const receipt = (id = requestId, status = 'accepted') => ({ job_id: scope.jobId, recovery_request_id: id, status, message: 'Recovery queued' })
const options = (store, transport, extra = {}) => ({
  scope, executionAttempt: 2, storage: store, transport, current: () => true, newRequestId: () => requestId, ...extra,
})

test('lost acknowledgement reconciles the persisted UUID; reload and double click never POST again', async () => {
  const store = storage()
  const calls = []
  const transport = {
    submit: async (job, body) => {
      assert.equal(readCompositionRecoveryHandle(scope, store).request.recovery_request_id, body.recovery_request_id)
      calls.push(['POST', job, body.recovery_request_id])
      throw new Error('response lost')
    },
    get: async (job, id) => { calls.push(['GET', job, id]); return receipt(id) },
  }
  assert.equal((await startCompositionRecovery(options(store, transport))).status, 'accepted')
  await startCompositionRecovery(options(store, transport))
  assert.deepEqual(calls.map(item => item[0]), ['POST', 'GET', 'GET'])
  assert.ok(calls.every(item => item[2] === requestId))
})

test('unknown acceptance remains GET-only after timeout, missing receipt, and reload', async () => {
  const store = storage()
  let posts = 0
  const transport = { submit: async () => { posts++; throw new Error('timeout') }, get: async () => { throw new Error('not found yet') } }
  await assert.rejects(startCompositionRecovery(options(store, transport)), /not found/)
  await assert.rejects(startCompositionRecovery(options(store, transport)), /not found/)
  assert.equal(posts, 1)
  assert.equal(readCompositionRecoveryHandle(scope, store).request.recovery_request_id, requestId)
})

test('unavailable or corrupted browser storage refuses dispatch', async () => {
  let posts = 0
  const transport = { submit: async () => { posts++; return receipt() }, get: async () => receipt() }
  await assert.rejects(startCompositionRecovery(options({ getItem: () => null, setItem: () => { throw new Error('quota') } }, transport)), /quota/)
  await assert.rejects(startCompositionRecovery(options({ getItem: () => '{bad', setItem: () => {} }, transport)))
  await assert.rejects(startCompositionRecovery(options({ getItem: () => null, setItem: () => {} }, transport)), /Nothing was submitted/)
  assert.equal(posts, 0)
})

test('only a fresh confirmation and positive rejection permit a new UUID for the same execution attempt', async () => {
  const store = storage()
  let posts = 0
  const transport = { submit: async (_job, body) => { posts++; return receipt(body.recovery_request_id, 'rejected') }, get: async () => receipt(requestId, 'rejected') }
  await startCompositionRecovery(options(store, transport))
  await startCompositionRecovery(options(store, transport, { newRequestId: () => nextId }))
  assert.equal(posts, 1)
  const value = await startCompositionRecovery(options(store, transport, { rejectedRequestId: requestId, newRequestId: () => nextId }))
  assert.equal(posts, 2)
  assert.equal(value.recovery_request_id, nextId)
})

test('account or project changes fence dispatch and acknowledgement-loss reconciliation', async () => {
  const store = storage()
  let active = true
  let gets = 0
  const transport = { submit: async () => { active = false; throw new Error('lost') }, get: async () => { gets++; return receipt() } }
  await assert.rejects(startCompositionRecovery(options(store, transport, { current: () => active })), /account or composition changed/)
  assert.equal(gets, 0)
  assert.equal(readCompositionRecoveryHandle({ ...scope, accountId: 'owner-b' }, store), null)
  assert.equal(readCompositionRecoveryHandle({ ...scope, workspace: 'project-b' }, store), null)
  assert.equal(readCompositionRecoveryHandle({ ...scope, createdAt: 1001 }, store), null)
})

test('concurrent confirmations after rejection reserve only one replacement request', async () => {
  const store = storage()
  let posts = 0
  const transport = { submit: async (_job, body) => { posts++; return receipt(body.recovery_request_id, 'rejected') }, get: async (_job, id) => receipt(id, 'rejected') }
  await startCompositionRecovery(options(store, transport))
  await Promise.all([
    startCompositionRecovery(options(store, transport, { rejectedRequestId: requestId, newRequestId: () => nextId })),
    startCompositionRecovery(options(store, transport, { rejectedRequestId: requestId, newRequestId: () => '7656c2ec-fc34-4197-9008-e07bbaf27911' })),
  ])
  assert.equal(posts, 2)
  assert.equal(readCompositionRecoveryHandle(scope, store).request.recovery_request_id, nextId)
})

test('a later interrupted execution uses a fresh request while stale execution state refuses dispatch', async () => {
  const store = storage()
  const posts = []
  const transport = { submit: async (_job, body) => { posts.push(body); return receipt(body.recovery_request_id) }, get: async () => receipt() }
  await startCompositionRecovery(options(store, transport))
  await startCompositionRecovery(options(store, transport, { executionAttempt: 3, newRequestId: () => nextId }))
  await assert.rejects(startCompositionRecovery(options(store, transport)), /composition changed/)
  assert.deepEqual(posts.map(body => body.expected_execution_attempt), [2, 3])
})

test('wire requests use dedicated scoped POST and GET; mismatched results cannot be adopted', async () => {
  const original = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, init) => {
    calls.push([url, init])
    return { ok: true, json: async () => receipt() }
  }
  try {
    await submitCompositionRecovery(scope.jobId, { recovery_request_id: requestId, expected_created_at: scope.createdAt, expected_execution_attempt: 2, confirmed: true })
    await fetchCompositionRecovery(scope.jobId, requestId)
    assert.match(calls[0][0], /\/composition-recovery$/)
    assert.equal(calls[0][1].method, 'POST')
    assert.equal(JSON.parse(calls[0][1].body).confirmed, true)
    assert.match(calls[1][0], new RegExp(`/composition-recovery/${requestId}$`))
    assert.equal(calls[1][1].method, 'GET')
    assert.equal(calls[1][1].body, undefined)
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...receipt(), job_id: 'another-job' }) })
    await assert.rejects(fetchCompositionRecovery(scope.jobId, requestId), /unexpected result/)
  } finally { globalThis.fetch = original }
})

let componentModule
async function loadRecoveryComponent() {
  if (componentModule) return componentModule
  const modules = new Map([
    ['react', `
      export const useRef = initial => globalThis.__compositionHooks.ref(initial)
      export const useState = initial => globalThis.__compositionHooks.state(initial)
      export const useEffect = (effect, dependencies) => globalThis.__compositionHooks.effect(effect, dependencies)
    `],
    ['react/jsx-runtime', `
      export const Fragment = Symbol('Fragment')
      export const jsx = (type, props) => ({ type, props })
      export const jsxs = jsx
    `],
    ['../stores/useStore', `
      export const useStore = selector => selector(globalThis.__compositionStore)
      export const currentAccountIdentityEpoch = () => globalThis.__compositionEpoch
    `],
    ['../api/client', `
      export const fetchJobStatus = (...args) => globalThis.__compositionTransport.job(...args)
      export const fetchCompositionRecovery = (...args) => globalThis.__compositionTransport.get(...args)
      export const submitCompositionRecovery = (...args) => globalThis.__compositionTransport.submit(...args)
      export const createLlmRequestId = () => globalThis.__compositionTransport.newRequestId()
    `],
  ])
  const compiled = await build({
    entryPoints: [new URL('../src/components/CompositionRecoveryStatus.tsx', import.meta.url).pathname],
    bundle: true, format: 'esm', platform: 'node', jsx: 'automatic', write: false,
    plugins: [{ name: 'composition-lifecycle-test', setup(builder) {
      builder.onResolve({ filter: /.*/ }, args => modules.has(args.path)
        ? { path: args.path, namespace: 'composition-test' } : undefined)
      builder.onLoad({ filter: /.*/, namespace: 'composition-test' }, args => ({ contents: modules.get(args.path) }))
    } }],
  })
  componentModule = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
  return componentModule
}

function componentHarness(Component, store, transport) {
  const slots = []
  let cursor = 0
  let effects = []
  let tree
  const previous = new Map(['__compositionHooks', '__compositionStore', '__compositionTransport', '__compositionEpoch', 'sessionStorage']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  globalThis.sessionStorage = store
  globalThis.__compositionEpoch = 1
  globalThis.__compositionStore = {
    accountContext: { authenticated: true, account: { id: scope.accountId } },
    reconnectJobs: async () => { transport.reconnects++ },
  }
  globalThis.__compositionTransport = transport
  globalThis.__compositionHooks = {
    ref(initial) { const index = cursor++; return slots[index] ??= { current: initial } },
    state(initial) { const index = cursor++; slots[index] ??= { value: initial }; return [slots[index].value, value => { slots[index].value = value }] },
    effect(effect, dependencies) {
      const index = cursor++
      const previous = slots[index]
      if (!previous || dependencies.some((value, position) => value !== previous.dependencies[position])) {
        effects.push(() => { previous?.cleanup?.(); slots[index] = { dependencies, cleanup: effect() } })
      }
    },
  }
  const text = node => Array.isArray(node) ? node.map(text).join('')
    : !node || typeof node === 'boolean' ? '' : typeof node !== 'object' ? String(node) : text(node.props?.children)
  const nodes = node => Array.isArray(node) ? node.flatMap(nodes)
    : !node || typeof node !== 'object' ? [] : [node, ...nodes(node.props?.children)]
  return {
    render(props = { jobId: scope.jobId, workspace: scope.workspace, createdAt: scope.createdAt }) {
      cursor = 0
      tree = Component(props)
      const commit = effects; effects = []; commit.forEach(effect => effect())
      return tree
    },
    click(label) {
      const button = nodes(tree).find(node => node.type === 'button' && text(node) === label)
      assert.ok(button, `Missing ${label}`)
      assert.equal(button.props.disabled, false)
      button.props.onClick()
    },
    text: () => text(tree),
    unmount() { slots.forEach(slot => slot?.cleanup?.()) },
    dispose() {
      slots.forEach(slot => slot?.cleanup?.())
      for (const [key, descriptor] of previous) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor)
        else delete globalThis[key]
      }
    },
  }
}
const flush = () => new Promise(resolve => setImmediate(resolve))
function deferred() {
  let resolve
  return { promise: new Promise(done => { resolve = done }), resolve: value => resolve(value) }
}

test('the Queue component fences deferred replacement POST on unmount and on leaving then returning to a project', async () => {
  const { CompositionRecoveryStatus } = await loadRecoveryComponent()
  for (const departure of ['unmount', 'project-return']) {
    const store = storage()
    await startCompositionRecovery(options(store, { submit: async () => receipt(requestId, 'rejected'), get: async () => receipt(requestId, 'rejected') }))
    const pending = deferred()
    let gets = 0
    const transport = {
      reconnects: 0, posts: 0,
      job: async () => ({ created_at: scope.createdAt, workspace: scope.workspace, recovery_actions: ['recover_composition'], composition_recovery_execution_attempt: 2 }),
      get: async () => ++gets === 1 ? receipt(requestId, 'rejected') : pending.promise,
      submit: async () => { transport.posts++; return receipt(nextId) },
    }
    const harness = componentHarness(CompositionRecoveryStatus, store, transport)
    try {
      harness.render(); await flush(); harness.render()
      harness.click('Review composition recovery'); await flush(); harness.render()
      harness.click('Confirm recovery'); assert.equal(gets, 2)
      if (departure === 'unmount') harness.unmount()
      else {
        harness.render({ jobId: scope.jobId, workspace: 'project-b', createdAt: scope.createdAt })
        harness.render()
      }
      pending.resolve(receipt(requestId, 'rejected')); await flush()
      assert.equal(transport.posts, 0, departure)
      assert.equal(transport.reconnects, 0, departure)
      assert.equal(readCompositionRecoveryHandle(scope, store).request.recovery_request_id, requestId)
    } finally { harness.dispose() }
  }
})

test('unmounted status checks cannot reconnect jobs and restoration cannot adopt an old account epoch', async () => {
  const { CompositionRecoveryStatus } = await loadRecoveryComponent()
  for (const operation of ['check', 'restore']) {
    const store = storage()
    await startCompositionRecovery(options(store, { submit: async () => receipt(requestId, 'pending'), get: async () => receipt(requestId, 'pending') }))
    const pending = deferred()
    let gets = 0
    const transport = {
      reconnects: 0,
      get: async () => ++gets === 1 && operation === 'check' ? receipt(requestId, 'pending') : pending.promise,
      submit: async () => { throw new Error('unexpected POST') },
    }
    const harness = componentHarness(CompositionRecoveryStatus, store, transport)
    try {
      harness.render(); await flush(); harness.render()
      if (operation === 'check') { harness.click('Check recovery status'); harness.unmount() }
      else globalThis.__compositionEpoch++
      pending.resolve({ ...receipt(), message: 'Old accepted result' }); await flush()
      assert.equal(transport.reconnects, 0)
      if (operation === 'restore') { harness.render(); assert.doesNotMatch(harness.text(), /Old accepted result/) }
    } finally { harness.dispose() }
  }
})

test('actual Queue confirmation supports LAN origins without crypto.randomUUID', async () => {
  const { CompositionRecoveryStatus } = await loadRecoveryComponent()
  const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
  const store = storage()
  const transport = {
    reconnects: 0, posts: 0, newRequestId: createLlmRequestId,
    job: async () => ({ created_at: scope.createdAt, workspace: scope.workspace, recovery_actions: ['recover_composition'], composition_recovery_execution_attempt: 2 }),
    get: async (_job, id) => receipt(id),
    submit: async (_job, request) => { transport.posts++; return receipt(request.recovery_request_id) },
  }
  const harness = componentHarness(CompositionRecoveryStatus, store, transport)
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: { getRandomValues: bytes => bytes.fill(17) } })
  try {
    harness.render(); await flush(); harness.render()
    harness.click('Review composition recovery'); await flush(); harness.render()
    harness.click('Confirm recovery'); await flush(); harness.render()
    assert.equal(transport.posts, 1)
    assert.equal(transport.reconnects, 1)
    assert.match(readCompositionRecoveryHandle(scope, store).request.recovery_request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  } finally {
    harness.dispose()
    if (originalCrypto) Object.defineProperty(globalThis, 'crypto', originalCrypto)
    else delete globalThis.crypto
  }
})
