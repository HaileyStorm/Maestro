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
      contents: 'export const currentAccountIdentityEpoch = () => 0; export const useStore = { subscribe() {} };', loader: 'js',
    }))
  } }],
})
const { Yue2TrainingAttempts } = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`)

test('an ambiguous training response retries the original request without a second GPU job', () => {
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
