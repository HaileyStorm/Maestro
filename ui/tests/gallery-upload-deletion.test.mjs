import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const bundle = await build({
  entryPoints: ['src/stores/useStore.ts'], bundle: true, format: 'esm',
  platform: 'node', write: false, logLevel: 'silent',
})
const { useStore } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`)

function fixture(t, workspace = '__uploads__') {
  const alerts = []
  t.mock.method(console, 'error', () => {})
  t.mock.method(globalThis, 'fetch', () => assert.fail('Unexpected request'))
  const previousWindow = globalThis.window
  globalThis.window = { alert: message => alerts.push(message) }
  t.after(() => { globalThis.window = previousWindow })
  let refreshes = 0
  useStore.setState({
    activeWorkspace: 'project-a', browsingUploads: workspace === '__uploads__',
    outputs: [{ name: 'same name.png', workspace, type: 'image', artifact_class: 'final', linked_component_count: 2 }],
    selectedOutput: 0,
    loadOutputs: async () => { refreshes += 1 },
  })
  return { alerts, refreshes: () => refreshes }
}

function response(value, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}

test('Uploads deletion uses only the ordinary-upload endpoint and never cascades output components', async t => {
  const runtime = fixture(t)
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push([url, options.method])
    return response({ deleted: 'same name.png' })
  })
  await useStore.getState().deleteSelectedOutput('same name.png', '__uploads__')
  assert.deepEqual(requests, [['/api/v1/uploads/same%20name.png', 'DELETE']])
  assert.equal(runtime.refreshes(), 1)
  assert.deepEqual(runtime.alerts, [])
})

test('A retained upload stays visible and reports the server in-use explanation without a retry', async t => {
  const runtime = fixture(t)
  let requests = 0
  t.mock.method(globalThis, 'fetch', async () => {
    requests += 1
    return response({ detail: 'This upload is still in use' }, 409)
  })
  t.mock.method(console, 'error', () => {})
  await useStore.getState().deleteSelectedOutput('same name.png', '__uploads__')
  assert.equal(requests, 1)
  assert.deepEqual(runtime.alerts, ['This upload is still in use'])
  assert.equal(useStore.getState().outputs[0].name, 'same name.png')
  assert.equal(runtime.refreshes(), 1)
})

test('A departed Gallery cannot receive a late deletion error or refresh, even after returning', async t => {
  const runtime = fixture(t)
  let resolve
  const pending = new Promise(done => { resolve = done })
  let requests = 0
  t.mock.method(globalThis, 'fetch', () => { requests += 1; return pending })
  t.mock.method(console, 'error', () => {})
  const operation = useStore.getState().deleteSelectedOutput('same name.png', '__uploads__')
  useStore.setState({ browsingUploads: false })
  useStore.setState({ browsingUploads: true })
  resolve(response({ detail: 'This upload is still in use' }, 409))
  await operation
  assert.equal(requests, 1)
  assert.equal(runtime.refreshes(), 0)
  assert.deepEqual(runtime.alerts, [])
})

test('Stale card workspace cannot redirect deletion to a same-named row', async t => {
  const runtime = fixture(t, 'project-b')
  await useStore.getState().deleteSelectedOutput('same name.png', 'project-a')
  assert.equal(runtime.refreshes(), 0)
})

test('Project output deletion retains its captured workspace and related-file cleanup', async t => {
  const runtime = fixture(t, 'project-a')
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push([url, options.method])
    return response({ deleted: 'same name.png', components: { failed: ['part.mp4'] } })
  })
  await useStore.getState().deleteSelectedOutput('same name.png', 'project-a')
  assert.deepEqual(requests, [['/api/v1/outputs/same%20name.png?delete_components=true&workspace=project-a', 'DELETE']])
  assert.match(runtime.alerts[0], /1 linked artifact/)
  assert.equal(runtime.refreshes(), 1)
})
