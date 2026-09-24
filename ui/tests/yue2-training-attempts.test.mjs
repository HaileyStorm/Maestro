import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const result = await build({
  entryPoints: [new URL('../src/components/Sidebar/yue2TrainingAttempts.ts', import.meta.url).pathname],
  bundle: true,
  format: 'esm',
  platform: 'node',
  write: false,
})
const { Yue2TrainingAttempts } = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`)

test('an ambiguous training response retries the original request without a second GPU job', () => {
  const attempts = new Yue2TrainingAttempts()
  const queued = new Set()
  const first = attempts.next('album-a', () => ({
    workspace: 'album-a', requestId: 'train-first-request',
    name: 'Original artist', tracks: [{ takeId: 'take-1', caption: 'warm piano', lyrics: '' }],
  }))
  queued.add(first.requestId) // The server accepted it; the response was lost.

  const retry = attempts.next('album-a', () => {
    throw new Error('Retry must not make a fresh request ID or read changed form inputs')
  })
  queued.add(retry.requestId)
  assert.deepEqual(retry, first)
  assert.equal(queued.size, 1)
  assert.equal(attempts.current('album-a')?.requestId, first.requestId)

  // Switching projects cannot replace or resolve the uncertain request.
  const other = attempts.next('album-b', () => ({ workspace: 'album-b', requestId: 'train-other-request' }))
  attempts.clear('album-a', other.requestId)
  assert.equal(attempts.current('album-a')?.requestId, first.requestId)
  attempts.clear('album-a', first.requestId)
  assert.equal(attempts.current('album-a'), undefined)
})
