import assert from 'node:assert/strict'
import test from 'node:test'

import {
  AccountApiError,
  fetchResourceReleasePreview,
  releaseResources,
} from '../src/api/client.ts'

test('resource release reads the owner preview and posts the exact selected labels', async t => {
  const originalFetch = globalThis.fetch
  const calls = []
  const preview = {
    activity_token: 'activity-v1',
    running: 1,
    queued: 2,
    preparing: 0,
    director_running: 1,
    loaded: ['generation model', 'Director assistant'],
    queue_paused: false,
    director_queue_paused: false,
  }
  const released = {
    released: ['Director assistant'],
    failures: [],
    stopped_jobs: 1,
    stopped_pipelines: 1,
    queue_paused: true,
    director_queue_paused: true,
  }
  t.after(() => { globalThis.fetch = originalFetch })
  globalThis.fetch = async (input, init = {}) => {
    calls.push({ url: String(input), init })
    return Response.json(init.method === 'POST' ? released : preview)
  }

  assert.deepEqual(await fetchResourceReleasePreview(), preview)
  assert.deepEqual(await releaseResources({
    activity_token: preview.activity_token,
    confirm_active: true,
    stop_running: true,
    targets: ['Director assistant'],
  }), released)

  assert.equal(calls.length, 2)
  assert.equal(calls[0].url, '/api/v1/system/resource-release')
  assert.equal(calls[0].init.method, undefined)
  assert.equal(calls[0].init.credentials, 'same-origin')
  assert.equal(calls[0].init.cache, 'no-store')
  assert.equal(calls[1].url, '/api/v1/system/resource-release')
  assert.equal(calls[1].init.method, 'POST')
  assert.deepEqual(JSON.parse(calls[1].init.body), {
    activity_token: 'activity-v1',
    confirm_active: true,
    stop_running: true,
    targets: ['Director assistant'],
  })
})

test('resource release preserves conflict status for refreshing a stale preview', async t => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  globalThis.fetch = async () => Response.json({
    detail: { code: 'resource_activity_changed', message: 'Queue activity changed.' },
  }, { status: 409 })

  await assert.rejects(
    releaseResources({ activity_token: 'stale', confirm_active: false, stop_running: false }),
    error => error instanceof AccountApiError
      && error.status === 409
      && error.code === 'resource_activity_changed'
      && error.message === 'Queue activity changed.',
  )
})
