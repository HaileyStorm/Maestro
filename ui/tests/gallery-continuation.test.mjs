import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const result = await build({ entryPoints: ['src/lib/galleryContinuation.ts'], bundle: true, format: 'esm', platform: 'node', write: false })
const { prepareGalleryContinuation, readContinuationDuration, retainContinuationPreview } = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`)

test('committed preview survives stashing but releases after its last reference', t => {
  const revoked = []
  t.mock.method(URL, 'revokeObjectURL', url => revoked.push(url))
  let refs = ['blob:owned']
  let listener
  let unsubscribed = 0
  retainContinuationPreview('blob:owned', () => refs, callback => {
    listener = callback
    return () => { unsubscribed += 1 }
  })
  refs = ['', 'blob:owned']
  listener()
  assert.deepEqual(revoked, [])
  refs = ['blob:replacement']
  listener()
  listener()
  assert.deepEqual(revoked, ['blob:owned'])
  assert.equal(unsubscribed, 1)
})

test('Gallery continuation retains scoped source and transfers preview ownership only on commit', async t => {
  t.mock.method(globalThis, 'fetch', async url => {
    assert.equal(url, '/file?workspace=project-a&name=clip.mp4')
    return new Response(new Blob(['video'], { type: 'video/mp4' }))
  })
  const revoked = []
  t.mock.method(URL, 'revokeObjectURL', url => revoked.push(url))
  const order = []
  const ok = await prepareGalleryContinuation({
    sourceUrl: '/file?workspace=project-a&name=clip.mp4', filename: 'clip.mp4',
    signal: new AbortController().signal, isCurrent: () => true,
    readDuration: async () => { order.push('metadata'); return 4.5 },
    upload: async file => { assert.equal(file.name, 'clip.mp4'); order.push('upload'); return { path: 'owned-upload.mp4' } },
    commit: (file, path, url, duration) => {
      order.push('commit'); assert.equal(path, 'owned-upload.mp4'); assert.equal(duration, 4.5); assert.ok(url.startsWith('blob:'))
    },
  })
  assert.equal(ok, true)
  assert.deepEqual(order, ['metadata', 'upload', 'commit'])
  assert.deepEqual(revoked, [])
})

test('project/account change during upload cannot commit and releases preview', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('video'))
  const revoked = []
  t.mock.method(URL, 'revokeObjectURL', url => revoked.push(url))
  let current = true
  const ok = await prepareGalleryContinuation({
    sourceUrl: '/authorized', filename: 'clip.mp4', signal: new AbortController().signal,
    isCurrent: () => current, readDuration: async () => 5,
    upload: async () => { current = false; return { path: 'old-project.mp4' } },
    commit: () => assert.fail('stale upload committed'),
  })
  assert.equal(ok, false)
  assert.equal(revoked.length, 1)
})

test('metadata errors and failed downloads never upload or commit', async t => {
  for (const badDownload of [true, false]) {
    const fetchMock = t.mock.method(globalThis, 'fetch', async () => new Response('data', { status: badDownload ? 403 : 200 }))
    await assert.rejects(prepareGalleryContinuation({
      sourceUrl: '/source', filename: 'clip.mp4', signal: new AbortController().signal,
      isCurrent: () => true, readDuration: async () => { throw new Error('metadata rejected') },
      upload: () => assert.fail('must not upload'), commit: () => assert.fail('must not commit'),
    }), badDownload ? /downloaded/ : /metadata rejected/)
    fetchMock.mock.restore()
  }
})

test('metadata decoder handles error and cancellation with released source', async t => {
  const originalDocument = globalThis.document
  t.after(() => { if (originalDocument === undefined) delete globalThis.document; else globalThis.document = originalDocument })
  for (const cancel of [false, true]) {
    const controller = new AbortController()
    let removed = false
    const video = { duration: 5, load() {}, removeAttribute() { removed = true } }
    globalThis.document = { createElement: () => video }
    const pending = readContinuationDuration('blob:source', controller.signal)
    assert.equal(video.src, 'blob:source')
    if (cancel) controller.abort()
    else video.onerror()
    await assert.rejects(pending, cancel ? /selection changed/ : /could not be read/)
    assert.equal(removed, true)
    assert.equal(video.onloadedmetadata, null)
    assert.equal(video.onerror, null)
  }
})
