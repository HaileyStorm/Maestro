import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

const uiRoot = fileURLToPath(new URL('..', import.meta.url))

function deferred() {
  let resolve
  let reject
  const promise = new Promise((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, reject, resolve }
}

async function loadStore() {
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
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`
  return import(moduleUrl)
}

async function flushPromises() {
  await Promise.resolve()
  await Promise.resolve()
}

test('system config updates return bounded success, rejection, and timeout results without late local mutation', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  const originalConsoleError = console.error
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
  const loggedErrors = []
  console.error = (...args) => { loggedErrors.push(args) }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
    console.error = originalConsoleError
  })

  const { useStore } = await loadStore()
  useStore.setState({ systemConfig: { vram_safety_coefficient: 0.9 } })

  const calls = []
  globalThis.fetch = async (input, init = {}) => {
    calls.push({ url: String(input), init })
    return Response.json({
      status: 'updated',
      updated: { vram_safety_coefficient: 0.82 },
    })
  }
  const success = await useStore.getState().updateSystemConfig({ vram_safety_coefficient: 0.82 })
  assert.deepEqual(success, {
    ok: true,
    updated: { vram_safety_coefficient: 0.82 },
  })
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, '/api/v1/system-config')
  assert.equal(calls[0].init.method, 'PUT')
  assert.deepEqual(JSON.parse(calls[0].init.body), { vram_safety_coefficient: 0.82 })
  assert.equal(useStore.getState().systemConfig.vram_safety_coefficient, 0.82)

  useStore.setState({ systemConfig: { vram_safety_coefficient: 0.9 } })
  globalThis.fetch = async (_input, init = {}) => {
    if (init.method === 'PUT') {
      return Response.json({ detail: 'private backend detail must not escape' }, { status: 500 })
    }
    return Response.json({ vram_safety_coefficient: 0.9 })
  }
  const failure = await useStore.getState().updateSystemConfig({ vram_safety_coefficient: 0.75 })
  assert.equal(failure.ok, false)
  assert.equal(failure.code, 'request_failed')
  assert.equal(failure.message, 'System settings could not be updated. Check the connection and try again.')
  assert.doesNotMatch(failure.message, /private backend detail/)
  await flushPromises()
  assert.equal(useStore.getState().systemConfig.vram_safety_coefficient, 0.9)

  const delayedReconciliation = deferred()
  let overlappingRequest = 0
  globalThis.fetch = async (_input, init = {}) => {
    overlappingRequest += 1
    if (overlappingRequest === 1) {
      assert.equal(init.method, 'PUT')
      return Response.json({ detail: 'first update failed' }, { status: 500 })
    }
    if (overlappingRequest === 2) {
      assert.equal(init.method, undefined)
      return delayedReconciliation.promise
    }
    assert.equal(init.method, 'PUT')
    return Response.json({
      status: 'updated',
      updated: { vram_safety_coefficient: 0.82 },
    })
  }
  const overlappingFailure = await useStore.getState().updateSystemConfig({ vram_safety_coefficient: 0.75 })
  assert.equal(overlappingFailure.ok, false)
  const overlappingRetry = await useStore.getState().updateSystemConfig({ vram_safety_coefficient: 0.82 })
  assert.equal(overlappingRetry.ok, true)
  assert.equal(useStore.getState().systemConfig.vram_safety_coefficient, 0.82)
  delayedReconciliation.resolve(Response.json({ vram_safety_coefficient: 0.9 }))
  await flushPromises()
  assert.equal(
    useStore.getState().systemConfig.vram_safety_coefficient,
    0.82,
    'failed update reconciliation cannot overwrite a newer successful retry',
  )

  const staleCancelledReconciliation = deferred()
  const cancelledUpdate = new AbortController()
  let cancellationOverlapRequest = 0
  globalThis.fetch = async (_input, init = {}) => {
    cancellationOverlapRequest += 1
    if (cancellationOverlapRequest === 1) {
      return Response.json({ detail: 'overlapped update failed' }, { status: 500 })
    }
    if (cancellationOverlapRequest === 2) {
      return staleCancelledReconciliation.promise
    }
    return new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => {
        reject(new DOMException('cancelled by caller', 'AbortError'))
      }, { once: true })
    })
  }
  const beforeCancellation = await useStore.getState().updateSystemConfig({ vram_safety_coefficient: 0.79 })
  assert.equal(beforeCancellation.ok, false)
  assert.equal(useStore.getState().systemConfigLoading, true)
  const cancelledPromise = useStore.getState().updateSystemConfig(
    { vram_safety_coefficient: 0.78 },
    cancelledUpdate.signal,
  )
  cancelledUpdate.abort()
  const cancelledResult = await cancelledPromise
  assert.equal(cancelledResult.ok, false)
  assert.equal(cancelledResult.code, 'cancelled')
  assert.equal(useStore.getState().systemConfigLoading, false, 'current cancellation clears stale reconciliation loading')
  staleCancelledReconciliation.resolve(Response.json({ vram_safety_coefficient: 0.9 }))
  await flushPromises()
  assert.equal(useStore.getState().systemConfigLoading, false, 'stale reconciliation cannot restore loading')
  assert.equal(useStore.getState().systemConfig.vram_safety_coefficient, 0.82)

  let supersededSignal = null
  let supersedingRequest = 0
  globalThis.fetch = async (_input, init = {}) => {
    supersedingRequest += 1
    if (supersedingRequest === 1) {
      supersededSignal = init.signal
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          reject(new DOMException('superseded', 'AbortError'))
        }, { once: true })
      })
    }
    return Response.json({
      status: 'updated',
      updated: { vram_safety_coefficient: 0.8 },
    })
  }
  const supersededUpdate = useStore.getState().updateSystemConfig({ vram_safety_coefficient: 0.81 })
  const supersedingUpdate = useStore.getState().updateSystemConfig({ vram_safety_coefficient: 0.8 })
  const [supersededResult, supersedingResult] = await Promise.all([supersededUpdate, supersedingUpdate])
  assert.equal(supersededSignal.aborted, true)
  assert.equal(supersededResult.ok, false)
  assert.equal(supersededResult.code, 'cancelled')
  assert.equal(supersedingResult.ok, true)
  assert.equal(useStore.getState().systemConfig.vram_safety_coefficient, 0.8)

  const pendingPut = deferred()
  const realWindowSetTimeout = window.setTimeout
  const realWindowClearTimeout = window.clearTimeout
  let timeoutDelay = null
  let clearedTimer = null
  let timeoutSignal = null
  window.setTimeout = (callback, delay) => {
    timeoutDelay = delay
    queueMicrotask(callback)
    return 17
  }
  window.clearTimeout = timer => { clearedTimer = timer }
  useStore.setState({ systemConfig: { vram_safety_coefficient: 0.9 } })
  globalThis.fetch = async (_input, init = {}) => {
    if (init.method === 'PUT') {
      timeoutSignal = init.signal
      return pendingPut.promise
    }
    return Response.json({ vram_safety_coefficient: 0.9 })
  }
  const timeout = await useStore.getState().updateSystemConfig({ vram_safety_coefficient: 0.7 })
  assert.equal(timeout.ok, false)
  assert.equal(timeout.code, 'timeout')
  assert.equal(timeout.message, 'System settings took too long to update. Check the connection and try again.')
  assert.equal(timeoutDelay, 15_000)
  assert.equal(clearedTimer, 17)
  assert.equal(timeoutSignal.aborted, true, 'timeout aborts the underlying PUT signal')
  assert.equal(useStore.getState().systemConfig.vram_safety_coefficient, 0.9)

  window.setTimeout = realWindowSetTimeout
  window.clearTimeout = realWindowClearTimeout
  pendingPut.resolve(Response.json({
    status: 'updated',
    updated: { vram_safety_coefficient: 0.7 },
  }))
  await flushPromises()
  assert.equal(useStore.getState().systemConfig.vram_safety_coefficient, 0.9, 'late timed-out response cannot merge locally')
  assert.equal(loggedErrors.length, 4)
})
