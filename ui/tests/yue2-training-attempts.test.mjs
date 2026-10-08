import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const result = await build({
  entryPoints: [new URL('../src/components/Sidebar/yue2TrainingAttempts.ts', import.meta.url).pathname],
  bundle: true,
  format: 'esm',
  platform: 'node',
  write: false,
  plugins: [{ name: 'training-attempt-account', setup(bundle) {
    bundle.onResolve({ filter: /stores\/useStore/ }, () => ({ path: 'store', namespace: 'training-test' }))
    bundle.onLoad({ filter: /.*/, namespace: 'training-test' }, () => ({
      contents: `export const currentAccountIdentityEpoch = () => 0; export const useStore = { subscribe() {}, getState: () => ({ accountContext: { enabled: true, authenticated: true, account: { id: globalThis.trainingOwner || 'owner-a' } } }) };`, loader: 'js',
    }))
  } }],
})
const { Yue2TrainingAttempts } = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`)

function resetStorage() {
  globalThis.trainingOwner = 'owner-a'
  const entries = new Map()
  globalThis.sessionStorage = { getItem: key => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value) }
  return entries
}

test('an ambiguous training response retries the original request without a second GPU job', () => {
  resetStorage()
  const attempts = new Yue2TrainingAttempts()
  const queued = new Set()
  const first = attempts.next(0, 'album-a', () => ({
    workspace: 'album-a', requestId: 'train-first-request',
    name: 'Original artist', tracks: [{ takeId: 'take-1', caption: 'warm piano', lyrics: '' }],
  }))
  queued.add(first.requestId) // The server accepted it; the response was lost.

  const retry = attempts.next(0, 'album-a', () => {
    throw new Error('Retry must not make a fresh request ID or read changed form inputs')
  })
  queued.add(retry.requestId)
  assert.deepEqual(retry, first)
  assert.equal(attempts.ambiguous(0, 'album-a', first.requestId), true, 'explicit repeat cannot prove the retained first POST was rejected')
  assert.equal(queued.size, 1)
  assert.equal(attempts.current(0, 'album-a')?.requestId, first.requestId)

  // Switching projects cannot replace or resolve the uncertain request.
  const other = attempts.next(0, 'album-b', () => ({ workspace: 'album-b', requestId: 'train-other-request', tracks: [] }))
  attempts.clear(0, 'album-a', other.requestId)
  assert.equal(attempts.current(0, 'album-a')?.requestId, first.requestId)
  attempts.clear(0, 'album-a', first.requestId)
  assert.equal(attempts.current(0, 'album-a'), undefined)
})


test('training attempts prune actual identity changes and stale epoch operations preserve the newer map', () => {
  resetStorage()
  let epoch = 5
  const attempts = new Yue2TrainingAttempts(() => epoch)
  const original = attempts.next(epoch, 'shared-name', () => ({
    workspace: 'shared-name', requestId: 'private-a', tracks: [{ takeId: 'take-a', caption: 'caption-a', lyrics: 'lyrics-a' }],
  }))
  assert.equal(Object.isFrozen(original.tracks[0]), true)
  attempts.markAmbiguous(epoch, 'shared-name', original.requestId)
  assert.equal(attempts.ambiguous(epoch, 'shared-name', original.requestId), true)
  attempts.next(epoch, 'another-project', () => ({ workspace: 'another-project', requestId: 'another', tracks: [] }))
  assert.strictEqual(attempts.next(epoch, 'shared-name', () => { throw new Error('Project roundtrip must preserve the attempt') }), original)
  epoch++
  globalThis.trainingOwner = 'owner-b'
  attempts.prune()
  assert.equal(attempts.current(epoch, 'shared-name'), undefined)
  const current = attempts.next(epoch, 'shared-name', () => ({ workspace: 'shared-name', requestId: 'new-account', tracks: [] }))
  assert.equal(attempts.current(epoch - 1, 'shared-name'), undefined)
  assert.equal(attempts.next(epoch - 1, 'shared-name', () => { throw new Error('Stale create must not run') }), undefined)
  attempts.clear(epoch - 1, 'shared-name', current.requestId)
  attempts.markAmbiguous(epoch - 1, 'shared-name', current.requestId)
  assert.equal(attempts.ambiguous(epoch, 'shared-name', current.requestId), false)
  assert.strictEqual(attempts.current(epoch, 'shared-name'), current, 'old settlement must not prune or clear the new identity')
  attempts.clear(epoch, 'shared-name', original.requestId)
  assert.strictEqual(attempts.current(epoch, 'shared-name'), current)
  attempts.clear(epoch, 'shared-name', current.requestId)
  assert.equal(attempts.current(epoch, 'shared-name'), undefined)
})


test('fresh training state restores uncertainty with stable identity, not the fresh epoch number', () => {
  const entries = resetStorage()
  const original = new Yue2TrainingAttempts(() => 9).next(9, 'album', () => ({
    workspace: 'album', requestId: 'retained-original', tracks: [{ takeId: 'take', caption: 'caption', lyrics: 'lyrics' }],
  }))
  const restored = new Yue2TrainingAttempts(() => 0)
  assert.deepEqual(restored.current(0, 'album'), original)
  assert.equal(restored.ambiguous(0, 'album', original.requestId), true)
  assert.equal(Object.isFrozen(restored.current(0, 'album').tracks[0]), true)
  globalThis.trainingOwner = 'owner-b'
  assert.equal(restored.current(0, 'album'), undefined)
  restored.next(0, 'album', () => ({ workspace: 'album', requestId: 'owner-b-id', tracks: [] }))
  globalThis.trainingOwner = 'owner-a'
  assert.deepEqual(restored.current(0, 'album'), original)
  assert.equal(entries.size, 2)
})

test('training ledger fails closed on unreadable storage and retains all sixteen unresolved projects', () => {
  const entries = resetStorage()
  const attempts = new Yue2TrainingAttempts()
  for (let i = 0; i < 16; i++) attempts.next(0, 'album-' + i, () => ({ workspace: 'album-' + i, requestId: 'id-' + i, tracks: [] }))
  const restored = new Yue2TrainingAttempts()
  assert.throws(() => restored.next(0, 'overflow', () => ({ workspace: 'overflow', requestId: 'overflow', tracks: [] })), /Confirm an unresolved/)
  assert.equal(restored.current(0, 'album-0').requestId, 'id-0')
  const [key, raw] = [...entries][0]
  entries.set(key, '{malformed')
  assert.equal(restored.current(0, 'album-0'), undefined)
  assert.equal(restored.unavailable(0), true)
  assert.throws(() => restored.next(0, 'album-0', () => ({ workspace: 'album-0', requestId: 'fresh', tracks: [] })), /could not be read/)
  assert.equal(entries.get(key), '{malformed')
  entries.set(key, raw)
  sessionStorage.setItem = () => { throw new Error('read only') }
  restored.clear(0, 'album-0', 'id-0')
  assert.equal(restored.current(0, 'album-0').requestId, 'id-0', 'failed settlement preserves the uncertainty fence')
  assert.equal(restored.ambiguous(0, 'album-0', 'id-0'), true)
})
