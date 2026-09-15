import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const bundle = build({
  stdin: { contents: "export { useStore } from './src/stores/useStore.ts'", resolveDir: new URL('..', import.meta.url).pathname, loader: 'js' },
  bundle: true, format: 'esm', platform: 'node', write: false, logLevel: 'silent',
}).then(result => result.outputFiles[0].text)
let realm = 0
const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const terms = accepted => ({ lawful_use: { accepted: false, version: 1 }, minimax_h3_ref2va: { accepted, version: 1 } })
function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}
async function withStore(fetch, run) {
  const names = ['fetch', 'window', 'document', 'localStorage', 'sessionStorage']
  const originals = Object.fromEntries(names.map(name => [name, globalThis[name]]))
  const storage = () => {
    const values = new Map()
    return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key) }
  }
  globalThis.localStorage = storage()
  globalThis.sessionStorage = storage()
  globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout, setInterval, clearInterval, location: { hostname: '127.0.0.1' }, matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }) })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.fetch = fetch
  try {
    const { useStore } = await import(`data:text/javascript;base64,${Buffer.from(await bundle).toString('base64')}#host-terms-${++realm}`)
    useStore.setState({ activeWorkspace: 'project-a' })
    await run(useStore)
  } finally {
    for (const name of names) {
      if (originals[name] === undefined) delete globalThis[name]
      else globalThis[name] = originals[name]
    }
  }
}

test('failed notice reads are coalesced until an explicit retry succeeds', async () => {
  let calls = 0
  let available = false
  await withStore(async () => {
    calls++
    return available ? response({ terms: terms(false) }) : response({}, 404)
  }, async store => {
    await Promise.all(Array.from({ length: 8 }, () => store.getState().loadHostTerms()))
    assert.equal(calls, 1)
    assert.equal(store.getState().hostTermsLoading, false)
    assert.match(store.getState().hostTermsError, /Failed to load/)
    for (let i = 0; i < 8; i++) await store.getState().loadHostTerms()
    assert.equal(calls, 1, 'loading effects must not turn a terminal failure into a request storm')
    available = true
    await store.getState().refreshHostTerms()
    assert.equal(calls, 2)
    assert.equal(store.getState().hostTermsError, null)
    assert.equal(store.getState().hostTerms.minimax_h3_ref2va.accepted, false)
    await store.getState().loadHostTerms()
    assert.equal(calls, 2)
  })
})

test('late project notice results cannot change the newly selected project', async () => {
  const first = deferred()
  const second = deferred()
  const requests = []
  await withStore(async input => {
    const workspace = new URL(String(input), 'http://localhost').searchParams.get('workspace')
    requests.push(workspace)
    return workspace === 'project-a' ? first.promise : second.promise
  }, async store => {
    const oldRead = store.getState().loadHostTerms()
    await new Promise(resolve => setImmediate(resolve))
    store.setState({ activeWorkspace: 'project-b' })
    const newRead = store.getState().loadHostTerms()
    first.resolve(response({ terms: terms(true) }))
    await oldRead
    assert.equal(store.getState().hostTerms, null)
    assert.equal(store.getState().hostTermsLoading, true)
    second.resolve(response({ terms: terms(false) }))
    await newRead
    assert.deepEqual(requests, ['project-a', 'project-b'])
    assert.equal(store.getState().hostTerms.minimax_h3_ref2va.accepted, false)
  })
})

test('an account change invalidates an in-flight notice read even for the same project name', async () => {
  const first = deferred()
  let calls = 0
  await withStore(async () => ++calls === 1 ? first.promise : response({ terms: terms(false) }), async store => {
    store.setState({ accountContext: { enabled: true, authenticated: true, account: { id: 'old-account' }, capabilities: [] }, accessContext: { accounts: { enabled: false, authenticated: false, account: null, capabilities: [] } } })
    const oldRead = store.getState().loadHostTerms()
    await new Promise(resolve => setImmediate(resolve))
    await store.getState().loadAccountContext(false)
    store.setState({ activeWorkspace: 'project-a' })
    const newRead = store.getState().loadHostTerms()
    first.resolve(response({ terms: terms(true) }))
    await Promise.all([oldRead, newRead])
    assert.equal(calls, 2)
    assert.equal(store.getState().hostTerms.minimax_h3_ref2va.accepted, false)
  })
})
