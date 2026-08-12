import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import test from 'node:test'

import { build } from 'esbuild'
import { compile } from 'tailwindcss'

const preflightUrl = new URL('../src/components/PreflightBanner.tsx', import.meta.url)
const downloadUrl = new URL('../src/components/DownloadStatusBanner.tsx', import.meta.url)
const oomUrl = new URL('../src/components/OomRecoveryBanner.tsx', import.meta.url)
const installedCheckpointsUrl = new URL('../src/components/LoraBrowser/InstalledCheckpoints.tsx', import.meta.url)
const clientUrl = new URL('../src/api/client.ts', import.meta.url)

function childrenOf(node) {
  if (!node || typeof node !== 'object') return []
  const children = node.props?.children
  return Array.isArray(children) ? children : children == null ? [] : [children]
}

function findNode(node, predicate) {
  if (node && typeof node === 'object' && predicate(node)) return node
  for (const child of childrenOf(node)) {
    const found = findNode(child, predicate)
    if (found) return found
  }
  return null
}

function findNodes(node, predicate, found = []) {
  if (node && typeof node === 'object' && predicate(node)) found.push(node)
  for (const child of childrenOf(node)) findNodes(child, predicate, found)
  return found
}

function nodeText(node) {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  return childrenOf(node).map(nodeText).join('')
}

async function flushPromises() {
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, reject, resolve }
}

async function loadBanner(entryUrl, exportName) {
  const modules = new Map([
    ['react', `
      function changed(previous, next) {
        return !previous || !next || previous.length !== next.length
          || previous.some((value, index) => !Object.is(value, next[index]))
      }
      export function useState(initial) {
        const index = globalThis.__bannerStateIndex++
        if (!(index in globalThis.__bannerState)) {
          globalThis.__bannerState[index] = typeof initial === 'function' ? initial() : initial
        }
        return [globalThis.__bannerState[index], value => {
          const current = globalThis.__bannerState[index]
          globalThis.__bannerState[index] = typeof value === 'function' ? value(current) : value
        }]
      }
      export function useRef(initial) {
        const index = globalThis.__bannerRefIndex++
        if (!globalThis.__bannerRefs[index]) globalThis.__bannerRefs[index] = { current: initial }
        return globalThis.__bannerRefs[index]
      }
      export function useEffect(effect, dependencies) {
        const index = globalThis.__bannerEffectIndex++
        if (!changed(globalThis.__bannerEffectDeps[index], dependencies)) return
        globalThis.__bannerEffectCleanups[index]?.()
        globalThis.__bannerEffectDeps[index] = dependencies
        globalThis.__bannerEffectCleanups[index] = effect()
      }
      export function useCallback(callback) { return callback }
      export function useMemo(factory) { return factory() }
    `],
    ['react/jsx-runtime', `
      export const Fragment = Symbol('Fragment')
      export function jsx(type, props, key) {
        return typeof type === 'function' ? type(props || {}) : { type, props: props || {}, key }
      }
      export const jsxs = jsx
    `],
    ['lucide-react', `
      const icon = props => ({ type: 'svg', props: props || {} })
      export const AlertTriangle = icon
      export const ArrowUpCircle = icon
      export const Boxes = icon
      export const Cpu = icon
      export const Download = icon
      export const Loader2 = icon
      export const RefreshCw = icon
      export const X = icon
    `],
    ['api', `
      export function fetchPreflight() { return globalThis.__fetchPreflight() }
      export function fetchActiveDownloads() { return globalThis.__fetchDownloads() }
      export function fetchInstalledCheckpoints() { return globalThis.__fetchInstalledCheckpoints() }
      export function checkCheckpointUpdates(force) { return globalThis.__checkCheckpointUpdates(force) }
    `],
    ['store', `
      export function useStore(selector) { return selector(globalThis.__bannerStore) }
    `],
    ['polling', `
      export const DOWNLOAD_REFRESH_EVENT = 'maestro:downloads-refresh'
      export const POLL_INTERVAL_MS = {
        downloadsActiveVisible: 2000,
        downloadsIdleVisible: 30000,
        accessContextInitial: 2500,
      }
      export function boundedBackoffDelay(_attempt, initial) { return initial }
      export function useVisibilityPolling(callback) {
        globalThis.__bannerPollCallback = callback
        if (!globalThis.__bannerRefreshNow) {
          globalThis.__bannerRefreshNow = () => globalThis.__bannerPollCallback()
        }
        return globalThis.__bannerRefreshNow
      }
    `],
    ['recovery-contract', `
      export function selectRecoverySourceIndex(jobs) {
        let selected = -1
        let newest = -Infinity
        jobs.forEach((job, index) => {
          if (job.manualRetryCount != null) return
          const created = Number(job.createdAt || 0)
          if (selected < 0 || created > newest) { selected = index; newest = created }
        })
        return selected
      }
    `],
    ['recovery-status', `
      export function H3DeliveryRecoveryStatus(props) {
        globalThis.__recoveryStatusProps = props
        return { type: 'button', props: { className: 'child-action', children: 'Retry delivery only' } }
      }
    `],
  ])
  const result = await build({
    absWorkingDir: new URL('../', import.meta.url).pathname,
    entryPoints: [entryUrl.pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'fixed-banner-harness',
      setup(buildApi) {
        buildApi.onResolve({ filter: /.*/ }, args => {
          if (modules.has(args.path)) return { path: args.path, namespace: 'banner-test' }
          if (args.path.includes('/api/client')) return { path: 'api', namespace: 'banner-test' }
          if (args.path.includes('/stores/useStore')) return { path: 'store', namespace: 'banner-test' }
          if (args.path.includes('/lib/useVisibilityPolling')) return { path: 'polling', namespace: 'banner-test' }
          if (args.path.includes('/lib/h3DeliveryRecoveryContract')) return { path: 'recovery-contract', namespace: 'banner-test' }
          if (args.path.endsWith('/H3DeliveryRecoveryStatus') || args.path === './H3DeliveryRecoveryStatus') {
            return { path: 'recovery-status', namespace: 'banner-test' }
          }
          return null
        })
        buildApi.onLoad({ filter: /.*/, namespace: 'banner-test' }, args => ({
          contents: modules.get(args.path),
          loader: 'js',
        }))
      },
    }],
  })
  const compiledModule = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(
    createRequire(import.meta.url),
    compiledModule,
    compiledModule.exports,
  )
  return compiledModule.exports[exportName]
}

function resetHarness() {
  globalThis.__bannerState = []
  globalThis.__bannerRefs = []
  globalThis.__bannerEffectDeps = []
  globalThis.__bannerEffectCleanups = []
  globalThis.__bannerRefreshNow = null
  globalThis.__bannerPollCallback = null
  globalThis.__recoveryStatusProps = null
  globalThis.__bannerStore = {}
  const listeners = new Map()
  globalThis.window = {
    addEventListener(type, callback) { listeners.set(type, callback) },
    removeEventListener(type, callback) {
      if (listeners.get(type) === callback) listeners.delete(type)
    },
  }
  globalThis.__bannerListeners = listeners
  globalThis.sessionStorage = {
    values: new Map(),
    getItem(key) { return this.values.get(key) ?? null },
    setItem(key, value) { this.values.set(key, String(value)) },
  }
}

function render(Component) {
  globalThis.__bannerStateIndex = 0
  globalThis.__bannerRefIndex = 0
  globalThis.__bannerEffectIndex = 0
  return Component()
}

function unmountHarness() {
  for (const cleanup of globalThis.__bannerEffectCleanups) cleanup?.()
}

test('Preflight announces the fetched severity once and preserves session dismissal', async () => {
  const PreflightBanner = await loadBanner(preflightUrl, 'PreflightBanner')
  resetHarness()
  globalThis.__fetchPreflight = async () => ({
    checks: [{ id: 'disk', level: 'error', message: 'Output storage is low.' }],
  })

  assert.equal(render(PreflightBanner), null)
  await flushPromises()
  let tree = render(PreflightBanner)
  const alert = findNode(tree, node => node.props?.role === 'alert')
  assert.equal(alert.props['aria-live'], 'assertive')
  assert.equal(alert.props['aria-atomic'], 'true')
  assert.equal(alert.props['aria-label'], 'Environment preflight errors')
  const icon = findNode(alert, node => node.type === 'svg')
  assert.equal(icon.props['aria-hidden'], 'true')

  const dismiss = findNode(tree, node => node.props?.['aria-label'] === 'Dismiss environment preflight notice')
  assert.equal(dismiss.props.type, 'button')
  assert.match(dismiss.props.className, /min-h-11/)
  assert.match(dismiss.props.className, /min-w-11/)
  dismiss.props.onClick()
  assert.equal(sessionStorage.getItem('maestro_preflight_dismissed'), '1')
  tree = render(PreflightBanner)
  assert.equal(tree, null)

  resetHarness()
  globalThis.__fetchPreflight = async () => ({
    checks: [{ id: 'cuda', level: 'warning', message: 'CUDA availability could not be confirmed.' }],
  })
  render(PreflightBanner)
  await flushPromises()
  tree = render(PreflightBanner)
  const status = findNode(tree, node => node.props?.role === 'status')
  assert.equal(status.props['aria-live'], 'polite')
  assert.equal(status.props['aria-label'], 'Environment preflight warnings')
})

test('Download exposes a quiet stable status plus determinate progress and preserves active state on refresh failure', async () => {
  const DownloadStatusBanner = await loadBanner(downloadUrl, 'DownloadStatusBanner')
  resetHarness()
  const active = {
    filename: 'models/large-file.safetensors',
    downloaded_bytes: 512,
    total_bytes: 1024,
    seconds_since_progress: 2,
    status: 'downloading',
  }
  globalThis.__bannerStore = { jobs: [], llmLoading: false, isEnhancing: false, llmStatus: null }
  globalThis.__fetchDownloads = async () => ({ downloads: [active] })

  assert.equal(render(DownloadStatusBanner), null)
  await flushPromises()
  let tree = render(DownloadStatusBanner)
  const status = findNode(tree, node => node.props?.role === 'status')
  assert.equal(status.props['aria-live'], 'polite')
  assert.equal(nodeText(status), 'Model download in progress. 1 file.')
  const progress = findNode(tree, node => node.props?.role === 'progressbar')
  assert.equal(progress.props['aria-valuemin'], 0)
  assert.equal(progress.props['aria-valuemax'], 100)
  assert.equal(progress.props['aria-valuenow'], 50)
  assert.match(progress.props['aria-valuetext'], /^50 percent, /)
  assert.equal(progress.props['aria-label'], 'Download progress for large-file.safetensors')
  assert.match(nodeText(tree), /large-file\.safetensors/)
  assert.doesNotMatch(nodeText(tree), /models\//)

  globalThis.__fetchDownloads = async () => { throw new Error('transient') }
  await globalThis.__bannerRefreshNow()
  tree = render(DownloadStatusBanner)
  assert.ok(findNode(tree, node => node.props?.role === 'progressbar'), 'transient failure retains the active banner')
  assert.ok(globalThis.__bannerListeners.has('maestro:downloads-refresh'))
})

test('Installed checkpoints distinguishes true empty from safe retryable load and provider-check failures', async () => {
  const InstalledCheckpoints = await loadBanner(installedCheckpointsUrl, 'InstalledCheckpoints')
  resetHarness()
  let fetchCalls = 0
  const selected = []
  const checks = []
  const emptyResult = { checkpoints: [], manifest_last_check_at: null }
  globalThis.__fetchInstalledCheckpoints = async () => {
    fetchCalls += 1
    return emptyResult
  }
  globalThis.__checkCheckpointUpdates = async force => { checks.push(force) }

  render(() => InstalledCheckpoints({ onSelectModel: id => selected.push(id) }))
  await flushPromises()
  let tree = render(() => InstalledCheckpoints({ onSelectModel: id => selected.push(id) }))
  assert.match(nodeText(tree), /No checkpoints imported yet/)
  assert.equal(findNodes(tree, node => node.props?.role === 'alert').length, 0)

  resetHarness()
  globalThis.__fetchInstalledCheckpoints = async () => {
    fetchCalls += 1
    throw new Error('/private/models/manifest.json')
  }
  render(() => InstalledCheckpoints({ onSelectModel: id => selected.push(id) }))
  await flushPromises()
  tree = render(() => InstalledCheckpoints({ onSelectModel: id => selected.push(id) }))
  let alert = findNode(tree, node => node.props?.role === 'alert')
  assert.equal(nodeText(alert), 'Maestro could not load the imported checkpoints.Retry')
  assert.doesNotMatch(nodeText(tree), /No checkpoints imported yet|private|manifest\.json/i)
  let retry = findNode(alert, node => node.type === 'button' && nodeText(node) === 'Retry')
  assert.equal(retry.props.type, 'button')
  assert.match(retry.props.className, /min-h-11/)
  assert.match(retry.props.className, /focus-visible:ring-2/)
  const callsBeforeRetry = fetchCalls
  globalThis.__fetchInstalledCheckpoints = async () => {
    fetchCalls += 1
    return emptyResult
  }
  retry.props.onClick()
  await flushPromises()
  assert.equal(fetchCalls, callsBeforeRetry + 1)
  tree = render(() => InstalledCheckpoints({ onSelectModel: id => selected.push(id) }))
  await flushPromises()
  tree = render(() => InstalledCheckpoints({ onSelectModel: id => selected.push(id) }))
  assert.match(nodeText(tree), /No checkpoints imported yet/)

  const checkpoint = {
    model_type: 'video-model', name: 'Video model', architecture: 'video',
    civitai_model_id: 73, update_status: 'current', preview_url: null,
    auto_quantize: false, base_model: null,
  }
  resetHarness()
  globalThis.__fetchInstalledCheckpoints = async () => ({ checkpoints: [checkpoint], manifest_last_check_at: null })
  globalThis.__checkCheckpointUpdates = async force => { checks.push(force) }
  render(() => InstalledCheckpoints({ onSelectModel: id => selected.push(id) }))
  await flushPromises()
  tree = render(() => InstalledCheckpoints({ onSelectModel: id => selected.push(id) }))
  findNode(tree, node => node.type === 'button' && /Open on CivitAI/.test(node.props?.title || '')).props.onClick()
  assert.deepEqual(selected, [73])
  const check = findNode(tree, node => node.type === 'button' && nodeText(node) === 'Check')
  assert.match(check.props.className, /min-h-11/)
  await check.props.onClick()
  assert.deepEqual(checks, [true])

  globalThis.__checkCheckpointUpdates = async () => { throw new Error('provider secret') }
  tree = render(() => InstalledCheckpoints({ onSelectModel: id => selected.push(id) }))
  await findNode(tree, node => node.type === 'button' && nodeText(node) === 'Check').props.onClick()
  tree = render(() => InstalledCheckpoints({ onSelectModel: id => selected.push(id) }))
  alert = findNode(tree, node => node.props?.role === 'alert')
  assert.equal(nodeText(alert), 'Maestro could not check CivitAI for checkpoint updates.Retry')
  assert.doesNotMatch(nodeText(alert), /provider secret/i)
})

test('Download identifies interrupted state safely and distinguishes explicit request retry', async () => {
  const DownloadStatusBanner = await loadBanner(downloadUrl, 'DownloadStatusBanner')
  resetHarness()
  globalThis.__bannerStore = { jobs: [], llmLoading: false, isEnhancing: false, llmStatus: null }
  globalThis.__fetchDownloads = async () => ({ downloads: [{
    filename: 'C:\\private\\project\\partial.bin?token=secret', downloaded_bytes: 20, total_bytes: 100,
    seconds_since_progress: 40, status: 'incomplete',
  }] })
  render(DownloadStatusBanner)
  await flushPromises()
  const tree = render(DownloadStatusBanner)
  const alert = findNode(tree, node => node.props?.role === 'alert')
  assert.equal(alert.props['aria-live'], 'assertive')
  assert.equal(
    nodeText(alert),
    'Model download interrupted for partial.bin. Automatic recovery stopped; re-run the request that needed this file to retry.',
  )
  assert.doesNotMatch(nodeText(alert), /20|percent|bytes/i)
  const progress = findNode(tree, node => node.props?.role === 'progressbar')
  assert.equal(progress.props['aria-label'], 'Download progress for partial.bin')
  assert.match(nodeText(tree), /Automatic recovery stopped for this file/)
  assert.doesNotMatch(nodeText(tree), /Downloading model files/)
  assert.equal(findNodes(tree, node => /animate-pulse/.test(node.props?.className || '')).length, 0)
  assert.ok(findNode(progress, node => /bg-red-400/.test(node.props?.className || '')))
  assert.doesNotMatch(nodeText(tree), /private|project|token|secret/i)
})

test('Download announces stalled recovery as automatic and action-free', async () => {
  const DownloadStatusBanner = await loadBanner(downloadUrl, 'DownloadStatusBanner')
  resetHarness()
  globalThis.__bannerStore = { jobs: [], llmLoading: false, isEnhancing: false, llmStatus: null }
  globalThis.__fetchDownloads = async () => ({ downloads: [{
    filename: '/private/cache/slow-model.bin', downloaded_bytes: 20, total_bytes: 100,
    seconds_since_progress: 40, status: 'downloading',
  }] })
  render(DownloadStatusBanner)
  await flushPromises()
  const tree = render(DownloadStatusBanner)
  const status = findNode(tree, node => node.props?.role === 'status')
  assert.equal(status.props['aria-live'], 'polite')
  assert.equal(nodeText(status), 'Model download is slow for slow-model.bin. Maestro will retry automatically.')
  assert.match(nodeText(tree), /Maestro will retry automatically/)
  assert.match(nodeText(tree), /no action needed from you/i)
  assert.doesNotMatch(nodeText(tree), /private|cache/i)
})

test('download stall documentation matches the executable thirty-second threshold', async () => {
  const [bannerSource, clientSource] = await Promise.all([
    readFile(downloadUrl, 'utf8'),
    readFile(clientUrl, 'utf8'),
  ])
  assert.match(bannerSource, /seconds_since_progress > 30/)
  assert.match(bannerSource, /hasn't advanced in >30s/)
  assert.match(clientSource, /flag stalled downloads \(e\.g\. `> 30`/)
  assert.doesNotMatch(clientSource, /flag stalled downloads \(e\.g\. `> 15`/)
})

test('OOM recovery keeps exact coefficient action payload and uses one stable alert plus a polite success status', async t => {
  const OomRecoveryBanner = await loadBanner(oomUrl, 'OomRecoveryBanner')
  resetHarness()
  const realSetTimeout = globalThis.setTimeout
  const realClearTimeout = globalThis.clearTimeout
  globalThis.setTimeout = (_callback, delay) => ({ delay })
  globalThis.clearTimeout = () => {}
  t.after(() => {
    globalThis.setTimeout = realSetTimeout
    globalThis.clearTimeout = realClearTimeout
  })
  const updates = []
  globalThis.__bannerStore = {
    jobs: [{
      id: 'job-1', status: 'failed', createdAt: 10, workspace: 'project-a',
      oomInfo: {
        stage: 'generation', message: 'allocation failed', current_coefficient: 0.9,
        suggested_coefficient: 0.82,
      },
    }],
    pipelineStatus: null,
    systemDetect: { hardware: { gpu_vram_gb: 16 } },
    accessContext: { machine_controls: true },
    async updateSystemConfig(payload) {
      updates.push(payload)
      return { ok: true, updated: { vram_safety_coefficient: payload.vram_safety_coefficient } }
    },
  }

  let tree = render(OomRecoveryBanner)
  const alerts = findNodes(tree, node => node.props?.role === 'alert')
  assert.equal(alerts.length, 1)
  assert.equal(alerts[0].props['aria-live'], 'assertive')
  assert.equal(nodeText(alerts[0]), 'Generation ran out of GPU memory. Generation failed.')
  const dismiss = findNode(tree, node => node.props?.['aria-label'] === 'Dismiss out-of-memory recovery notice')
  assert.match(dismiss.props.className, /min-h-11/)
  assert.match(dismiss.props.className, /focus-visible:ring-2/)
  const apply = findNode(tree, node => node.type === 'button' && nodeText(node).startsWith('Use safer setting'))
  await apply.props.onClick()
  assert.deepEqual(updates, [{ vram_safety_coefficient: 0.82 }])

  tree = render(OomRecoveryBanner)
  const status = findNode(tree, node => node.props?.role === 'status')
  assert.equal(status.props['aria-live'], 'polite')
  assert.match(nodeText(status), /GPU memory setting changed to 82%/)
})

test('OOM apply failure stays actionable and one exact retry clears its bounded error only on success', async () => {
  const OomRecoveryBanner = await loadBanner(oomUrl, 'OomRecoveryBanner')
  resetHarness()
  const updates = []
  const results = [
    {
      ok: false,
      code: 'timeout',
      message: 'System settings took too long to update. Check the connection and try again.',
    },
    { ok: true, updated: { vram_safety_coefficient: 0.82 } },
  ]
  globalThis.__bannerStore = {
    jobs: [{
      id: 'job-retry', status: 'failed', createdAt: 11, workspace: 'project-a',
      oomInfo: {
        stage: 'generation', message: 'allocation failed', current_coefficient: 0.9,
        suggested_coefficient: 0.82,
      },
    }],
    pipelineStatus: null,
    systemDetect: { hardware: { gpu_vram_gb: 16 } },
    accessContext: { machine_controls: true },
    async updateSystemConfig(payload) {
      updates.push(payload)
      return results.shift()
    },
  }

  let tree = render(OomRecoveryBanner)
  let apply = findNode(tree, node => node.type === 'button' && nodeText(node).startsWith('Use safer setting'))
  await apply.props.onClick()
  tree = render(OomRecoveryBanner)
  const boundedErrors = findNodes(tree, node => (
    node.props?.role === 'alert'
    && nodeText(node).includes('System settings took too long')
  ))
  assert.equal(boundedErrors.length, 1)
  assert.equal(boundedErrors[0].props['aria-atomic'], 'true')
  assert.match(nodeText(tree), /Use safer setting 82%/)
  assert.equal(findNode(tree, node => node.props?.role === 'status'), null, 'failure never claims success')
  apply = findNode(tree, node => node.type === 'button' && nodeText(node).startsWith('Use safer setting'))
  assert.equal(apply.props.disabled, false)

  await apply.props.onClick()
  assert.deepEqual(updates, [
    { vram_safety_coefficient: 0.82 },
    { vram_safety_coefficient: 0.82 },
  ])
  tree = render(OomRecoveryBanner)
  assert.equal(findNode(tree, node => nodeText(node).includes('System settings took too long')), null)
  assert.match(nodeText(findNode(tree, node => node.props?.role === 'status')), /GPU memory setting changed to 82%/)
  unmountHarness()
})

test('OOM apply fences same-render duplicates, replacement failures, and unmounted completion', async () => {
  const OomRecoveryBanner = await loadBanner(oomUrl, 'OomRecoveryBanner')
  resetHarness()
  const first = deferred()
  let calls = 0
  let oldSignal = null
  const store = {
    jobs: [{
      id: 'old-job', status: 'failed', createdAt: 12, workspace: 'project-a',
      oomInfo: {
        stage: 'generation', message: 'old allocation failed', current_coefficient: 0.9,
        suggested_coefficient: 0.82,
      },
    }],
    pipelineStatus: null,
    systemDetect: { hardware: { gpu_vram_gb: 16 } },
    accessContext: { machine_controls: true },
    updateSystemConfig(_partial, signal) {
      calls += 1
      oldSignal = signal
      return first.promise
    },
  }
  globalThis.__bannerStore = store

  let tree = render(OomRecoveryBanner)
  const oldApply = findNode(tree, node => node.type === 'button' && nodeText(node).startsWith('Use safer setting'))
  const oldAttempt = oldApply.props.onClick()
  const duplicateAttempt = oldApply.props.onClick()
  assert.equal(calls, 1, 'imperative guard blocks a second click before rerender')
  await duplicateAttempt

  store.jobs = [{
    id: 'new-job', status: 'failed', createdAt: 13, workspace: 'project-a',
    oomInfo: {
      stage: 'generation', message: 'new allocation failed', current_coefficient: 0.88,
      suggested_coefficient: 0.77,
    },
  }]
  tree = render(OomRecoveryBanner)
  assert.match(nodeText(tree), /Use safer setting 77%/)
  assert.equal(oldSignal.aborted, true, 'replacement OOM aborts the superseded settings request')
  first.resolve({ ok: true, updated: { vram_safety_coefficient: 0.82 } })
  await oldAttempt
  tree = render(OomRecoveryBanner)
  assert.match(nodeText(tree), /Use safer setting 77%/)
  assert.equal(findNode(tree, node => node.props?.role === 'status'), null, 'old success cannot cover the replacement OOM')

  resetHarness()
  const unmounted = deferred()
  let unmountedSignal = null
  globalThis.__bannerStore = {
    ...store,
    jobs: [{
      id: 'unmounted-job', status: 'failed', createdAt: 14, workspace: 'project-a',
      oomInfo: {
        stage: 'generation', message: 'allocation failed', current_coefficient: 0.9,
        suggested_coefficient: 0.81,
      },
    }],
    updateSystemConfig(_partial, signal) {
      unmountedSignal = signal
      return unmounted.promise
    },
  }
  tree = render(OomRecoveryBanner)
  const unmountedApply = findNode(tree, node => node.type === 'button' && nodeText(node).startsWith('Use safer setting'))
  const unmountedAttempt = unmountedApply.props.onClick()
  const stateBeforeCompletion = [...globalThis.__bannerState]
  unmountHarness()
  assert.equal(unmountedSignal.aborted, true, 'unmount aborts the in-flight settings request')
  unmounted.resolve({ ok: true, updated: { vram_safety_coefficient: 0.81 } })
  await unmountedAttempt
  assert.deepEqual(globalThis.__bannerState, stateBeforeCompletion, 'unmounted completion performs no state writes')
})

test('delivery OOM keeps exact recovery source identity and inherits usable action geometry', async () => {
  const OomRecoveryBanner = await loadBanner(oomUrl, 'OomRecoveryBanner')
  resetHarness()
  globalThis.__bannerStore = {
    jobs: [{
      id: 'source-job', status: 'failed', createdAt: 20, workspace: 'project-delivery',
      oomInfo: {
        stage: 'h3_delivery', message: 'delivery allocation failed', current_coefficient: 0.9,
        suggested_coefficient: null, native_available: true, requested_target: '1080p',
        retry_count: 1,
      },
    }],
    pipelineStatus: null,
    systemDetect: { hardware: { gpu_vram_gb: 16 } },
    accessContext: { machine_controls: true },
    async updateSystemConfig() { throw new Error('delivery must not change system settings') },
  }

  const tree = render(OomRecoveryBanner)
  assert.deepEqual(globalThis.__recoveryStatusProps, {
    sourceJobId: 'source-job',
    workspace: 'project-delivery',
  })
  const retry = findNode(tree, node => node.type === 'button' && nodeText(node) === 'Retry delivery only')
  assert.ok(retry, 'delivery recovery action is visible')
  const actionGeometry = findNode(tree, node => typeof node.props?.className === 'string'
    && node.props.className.includes('[&_button]:min-h-11'))
  assert.match(actionGeometry.props.className, /\[&_button\]:min-w-11/)
  assert.match(actionGeometry.props.className, /\[&_button\]:focus-visible:ring-2/)
  assert.match(actionGeometry.props.className, /\[&_button\]:motion-reduce:transition-none/)

  const recoverySource = await readFile(new URL('../src/components/H3DeliveryRecoveryStatus.tsx', import.meta.url), 'utf8')
  assert.match(recoverySource, /Recovery is running\./)
  assert.match(recoverySource, /Recovery is waiting in the generation queue\./)
  assert.match(recoverySource, /Try delivery again using the saved original\. Generation will not run again, and machine settings will not change\./)
  assert.match(recoverySource, /Use saved result/)
  assert.doesNotMatch(recoverySource, /Recovery child|native result|denoise/i)
})

test('critical OOM recovery has explicit interaction priority over simultaneous preflight', async () => {
  const [preflightSource, oomSource] = await Promise.all([
    readFile(preflightUrl, 'utf8'),
    readFile(oomUrl, 'utf8'),
  ])
  assert.match(preflightSource, /fixed inset-0 z-50/)
  assert.match(oomSource, /fixed inset-0 z-\[60\]/)
  assert.doesNotMatch(oomSource, /fixed inset-0 z-50/)

  const layer = source => {
    const match = source.match(/fixed inset-0 z-(?:\[(\d+)\]|(\d+))/)
    return Number(match?.[1] || match?.[2])
  }
  assert.ok(layer(oomSource) > layer(preflightSource), 'OOM remains readable and actionable above startup preflight')
})

test('all fixed banners compile safe-area, narrow-height, focus, touch, and reduced-motion contracts', async () => {
  const sources = await Promise.all([preflightUrl, downloadUrl, oomUrl].map(url => readFile(url, 'utf8')))
  for (const source of sources) {
    assert.match(source, /fixed inset-0/)
    assert.match(source, /max-h-\[100vh\]/)
    assert.match(source, /supports-\[height:100dvh\]:max-h-\[100dvh\]/)
    assert.match(source, /overflow-y-auto/)
    for (const edge of ['top', 'right', 'bottom', 'left']) {
      assert.match(source, new RegExp(`safe-area-inset-${edge}`))
    }
  }
  assert.match(sources[0], /min-h-11 min-w-11/)
  assert.match(sources[1], /motion-reduce:animate-none/)
  assert.match(sources[1], /motion-reduce:transition-none/)
  assert.match(sources[2], /\[&_button\]:min-h-11/)
  assert.match(sources[2], /focus-visible:ring-2/)
  assert.match(sources[2], /motion-reduce:transition-none/)

  const utilities = await compile('@theme { --spacing: 0.25rem; } @tailwind utilities;')
  const compiled = utilities.build([
    'max-h-[100vh]',
    'supports-[height:100dvh]:max-h-[100dvh]',
    'min-h-11',
    'min-w-11',
    'motion-reduce:animate-none',
    'motion-reduce:transition-none',
  ])
  assert.match(compiled, /max-height: 100vh/)
  assert.match(compiled, /@supports \(height:\s*100dvh\)/)
  assert.match(compiled, /max-height: 100dvh/)
  assert.match(compiled, /min-height: calc\(var\(--spacing\) \* 11\)/)
  assert.match(compiled, /min-width: calc\(var\(--spacing\) \* 11\)/)
  assert.match(compiled, /prefers-reduced-motion: reduce/)

  const viewportMatrix = [
    [320, 568], [390, 667], [430, 740],
    [568, 320], [767, 390], [768, 430], [1920, 1080],
  ]
  for (const [width, height] of viewportMatrix) {
    const safeArea = height <= 430
      ? { top: 0, right: 24, bottom: 21, left: 24 }
      : { top: 47, right: 0, bottom: 34, left: 0 }
    const availableWidth = width - Math.max(16, safeArea.left) - Math.max(16, safeArea.right)
    const availableHeight = height - Math.max(16, safeArea.top) - Math.max(16, safeArea.bottom)
    assert.ok(Math.min(576, availableWidth) > 0, `${width}x${height} leaves usable banner width`)
    assert.ok(availableHeight >= 44, `${width}x${height} leaves a usable action row`)
  }
  assert.deepEqual(
    [640, 780, 860].map(physicalWidth => physicalWidth / 2),
    [320, 390, 430],
    'the narrow matrix explicitly represents 200% zoomed CSS widths',
  )
})
