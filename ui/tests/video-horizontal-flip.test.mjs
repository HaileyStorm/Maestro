import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build, transform } from 'esbuild'

const rootUrl = new URL('../', import.meta.url)
const asDataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`

test('hflip client submits an exact workspace, filename, and revision', async () => {
  const { submitToolHflip } = await import('../src/api/client.ts')
  const calls = []
  const previousFetch = globalThis.fetch
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init })
    return { ok: true, json: async () => ({ job_id: 'flip-job', status: 'queued' }) }
  }
  try {
    assert.deepEqual(
      await submitToolHflip({ workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7' }),
      { job_id: 'flip-job', status: 'queued' },
    )
  } finally {
    globalThis.fetch = previousFetch
  }
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, '/api/v1/tools/hflip')
  assert.equal(calls[0].init.method, 'POST')
  assert.equal(calls[0].init.headers['Content-Type'], 'application/json')
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7',
  })
})

async function loadFlipAction() {
  const source = await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
  const start = source.indexOf('  flipSelectedClip: async (output) => {')
  const end = source.indexOf('  sendClipToTools:', start)
  assert.ok(start >= 0 && end > start, 'flip action must stay in a bounded store region')
  const method = source.slice(start, end)
    .replace(/^  flipSelectedClip: async \(output\) => \{/, `export async function flipSelectedClip(output, context) {
      const { api, get, set, accountIdentityEpochValue, accountIdentityIsCurrent,
        discardStaleGenerationPlaceholder, isActiveGenerationJob } = context
      const _accountIdentityEpoch = accountIdentityEpochValue
      const _accountIdentityIsCurrent = accountIdentityIsCurrent
      const _discardStaleGenerationPlaceholder = discardStaleGenerationPlaceholder
      const _isActiveGenerationJob = isActiveGenerationJob`)
    .replace(/\n  \},\s*$/, '\n}')
  const result = await transform(method, { loader: 'ts', format: 'esm', target: 'es2022' })
  return import(asDataModule(result.code)).then(module => module.flipSelectedClip)
}

function makeActionContext({ submit, workspace = 'project-a', epoch = 1 } = {}) {
  let state = {
    activeWorkspace: workspace,
    jobs: [],
    _pollRecoveredJob: (...args) => pollCalls.push(args),
  }
  const pollCalls = []
  const submitted = []
  let currentEpoch = epoch
  const context = {
    api: {
      submitToolHflip: async params => {
        submitted.push(params)
        return submit ? submit(params) : { job_id: 'flip-job', status: 'queued' }
      },
    },
    get: () => state,
    set: update => {
      state = { ...state, ...(typeof update === 'function' ? update(state) : update) }
    },
    accountIdentityEpochValue: epoch,
    accountIdentityIsCurrent: value => value === currentEpoch,
    discardStaleGenerationPlaceholder: placeholder => {
      state = {
        ...state,
        jobs: state.jobs.filter(job => job !== placeholder),
        isGenerating: state.jobs.some(job => job !== placeholder && job.status === 'queued'),
      }
    },
    isActiveGenerationJob: job => ['preparing', 'waiting_for_plan_approval', 'queued', 'running'].includes(job.status),
  }
  return {
    context,
    get state() { return state },
    pollCalls,
    submitted,
    setWorkspace(value) { state = { ...state, activeWorkspace: value } },
    setEpoch(value) { currentEpoch = value },
  }
}

test('flip action preserves source identity, queues a scoped job, and polls the returned id', async () => {
  const flipSelectedClip = await loadFlipAction()
  const fixture = makeActionContext()
  globalThis.window = { dispatchEvent() {} }
  globalThis.CustomEvent = class CustomEvent { constructor(type) { this.type = type } }
  await flipSelectedClip({ workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7' }, fixture.context)
  assert.deepEqual(fixture.submitted, [{ workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7' }])
  assert.equal(fixture.state.jobs[0].id, 'flip-job')
  assert.equal(fixture.state.jobs[0].workspace, 'project-a')
  assert.equal(fixture.state.jobs[0].status, 'queued')
  assert.deepEqual(fixture.pollCalls, [['flip-job', 'project-a']])
})

test('late hflip responses cannot retain a placeholder after project or account changes', async () => {
  const flipSelectedClip = await loadFlipAction()
  let resolveSubmit
  const fixture = makeActionContext({ submit: () => new Promise(resolve => { resolveSubmit = resolve }) })
  const pending = flipSelectedClip({ workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7' }, fixture.context)
  assert.equal(fixture.state.jobs.length, 1)
  fixture.setWorkspace('project-b')
  resolveSubmit({ job_id: 'late-job', status: 'queued' })
  await pending
  assert.equal(fixture.state.jobs.length, 0)

  let resolveAccountSubmit
  const accountFixture = makeActionContext({ submit: () => new Promise(resolve => { resolveAccountSubmit = resolve }) })
  const accountPending = flipSelectedClip({ workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7' }, accountFixture.context)
  accountFixture.setEpoch(2)
  resolveAccountSubmit({ job_id: 'late-account-job', status: 'queued' })
  await accountPending
  assert.equal(accountFixture.state.jobs.length, 0)
})

const componentModules = new Map([
  ['react', `
    export function useState(initial) {
      const index = globalThis.__flipHookIndex++
      if (!(index in globalThis.__flipState)) globalThis.__flipState[index] = typeof initial === 'function' ? initial() : initial
      return [globalThis.__flipState[index], value => {
        const current = globalThis.__flipState[index]
        globalThis.__flipState[index] = typeof value === 'function' ? value(current) : value
      }]
    }
    export function useRef(initial) {
      const index = globalThis.__flipRefIndex++
      if (!(index in globalThis.__flipRefs)) globalThis.__flipRefs[index] = { current: initial }
      return globalThis.__flipRefs[index]
    }
    export function useEffect() {}
    export function useCallback(callback) { return callback }
  `],
  ['react/jsx-runtime', `
    export function jsx(type, props) { return { type, props: props || {} } }
    export const jsxs = jsx
    export const Fragment = Symbol('Fragment')
  `],
  ['lucide-react', `
    export const Play = 'Play'; export const Pencil = 'Pencil'; export const RefreshCw = 'RefreshCw';
    export const Copy = 'Copy'; export const Trash2 = 'Trash2'; export const Check = 'Check';
    export const Combine = 'Combine'; export const Loader2 = 'Loader2'; export const Heart = 'Heart';
    export const ArrowLeftToLine = 'ArrowLeftToLine'; export const Download = 'Download';
    export const FolderInput = 'FolderInput'; export const Scissors = 'Scissors';
    export const FastForward = 'FastForward'; export const BookMarked = 'BookMarked';
    export const EyeOff = 'EyeOff'; export const Share2 = 'Share2'; export const Link2Off = 'Link2Off';
    export const FlipHorizontal = 'FlipHorizontal'; export const Clapperboard = 'Clapperboard';
  `],
  ['../Recipes/SaveRecipeDialog', `export function SaveRecipeDialog() { return null }`],
  ['../../stores/useStore', `
    export function useStore(select) { return select(globalThis.__flipStore) }
    useStore.getState = () => globalThis.__flipStore
    useStore.setState = () => {}
    useStore.subscribe = () => () => {}
    export function currentAccountIdentityEpoch() { return 1 }
  `],
  ['../../lib/galleryContinuation', `
    export async function prepareGalleryContinuation() {}
    export function retainContinuationPreview() {}
  `],
  ['../../api/client', `
    export function getUploadUrl(value) { return value }
    export function getFileUrl(value) { return value }
    export async function fetchOutputMetadata() { return null }
    export async function createOutputShare() { return { configured_public_origin: false, share_path: '', public_url: '' } }
    export async function deleteOutputComponents() { return { failed: [] } }
    export async function moveOutput() {}
    export async function revokeOutputShare() { return 0 }
    export async function uploadImage() { return { path: '', url: '' } }
  `],
  ['../../lib/format', `export function formatGenerationDuration(value) { return String(value) }`],
  ['../../lib/modelDisplay', `export function modelDisplayName(value) { return value }`],
  ['../../lib/privatePreview', `
    export function hidePrivatePreview() {}
    export function privatePreviewIdentity(workspace, name, revision) { return workspace + ':' + name + ':' + revision }
    export function privatePreviewWasRevealed() { return false }
    export function revealPrivatePreview() {}
    export function subscribePrivatePreviewReveal() { return () => {} }
  `],
])

async function loadMediaFeedItem() {
  const result = await build({
    absWorkingDir: new URL('../', import.meta.url).pathname,
    entryPoints: [new URL('../src/components/MainContent/MediaFeedItem.tsx', import.meta.url).pathname],
    bundle: true,
    format: 'cjs',
    platform: 'node',
    jsx: 'automatic',
    write: false,
    logLevel: 'silent',
    plugins: [{
      name: 'video-info-bar-flip-harness',
      setup(bundle) {
        bundle.onResolve({ filter: /.*/ }, args => componentModules.has(args.path)
          ? { path: args.path, namespace: 'flip-test' }
          : undefined)
        bundle.onLoad({ filter: /.*/, namespace: 'flip-test' }, args => ({
          contents: componentModules.get(args.path), loader: 'js',
        }))
      },
    }],
  })
  const compiled = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(
    (await import('node:module')).createRequire(import.meta.url), compiled, compiled.exports,
  )
  return compiled.exports.MediaFeedItem
}

function resetComponentHarness() {
  globalThis.__flipHookIndex = 0
  globalThis.__flipRefIndex = 0
  globalThis.__flipEffectIndex = 0
  globalThis.__flipState ??= []
  globalThis.__flipRefs ??= []
  globalThis.__flipEffects ??= []
}

function render(Component, store) {
  globalThis.__flipStore = store
  resetComponentHarness()
  return Component({
    file: store.__file,
    index: 0,
    isActive: true,
    onSelect() {},
    onVisible() {},
    measurementEpoch: 0,
    onMeasured() {},
  })
}

function walk(node, matches = []) {
  if (!node || typeof node !== 'object') return matches
  if (node.type === 'button' || node.props?.role === 'status' || node.props?.role === 'alert') matches.push(node)
  const children = node.props?.children
  for (const child of Array.isArray(children) ? children : [children]) walk(child, matches)
  return matches
}

function outputState(flipSelectedClip) {
  const output = {
    name: 'clip.mp4', url: '/outputs/clip.mp4', type: 'video', mode: null,
    artifact_class: 'final', linked_component_count: 0, favorite: false, size: 1,
    created_at: 1, revision: 'rev-7', workspace: 'project-a', private: false, explicit: false,
  }
  return {
    __file: output,
    loadSettingsFromOutput() {}, rerollGeneration() {}, deleteSelectedOutput() {},
    rejoinClipGroup() {}, toggleFavorite() {}, setStartImage() {}, addImageRef() {},
    setContinueVideo() {}, setParam() {}, openRetakeDialog() {}, generationMode: 'video',
    workspaces: [], accessContext: null, browsingUploads: false, models: [],
    gallerySelectionMode: false, selectedOutputKeys: [], toggleOutputSelection() {},
    saveRecipeFromOutput: async () => {}, flipSelectedClip,
  }
}

test('mounted gallery item exposes a video-only accessible pending/error flip action', async () => {
  const Component = await loadMediaFeedItem()
  const metadata = { params: {}, upload_filenames: {} }
  globalThis.__flipState = [metadata, true]
  globalThis.__flipRefs = []
  globalThis.__flipEffects = []
  let resolveFlip
  const calls = []
  const pendingFlip = () => {
    calls.push('submitted')
    return new Promise(resolve => { resolveFlip = resolve })
  }
  const initial = render(Component, outputState(pendingFlip))
  const flipButton = walk(initial).find(node => /Flip .* horizontally/.test(node.props?.['aria-label'] || ''))
  assert.ok(flipButton)
  const pending = flipButton.props.onClick({ stopPropagation() {} })
  const during = render(Component, outputState(pendingFlip))
  const pendingButton = walk(during).find(node => /Flipping .* horizontally/.test(node.props?.['aria-label'] || ''))
  assert.equal(pendingButton.props.disabled, true)
  assert.equal(pendingButton.props['aria-busy'], true)
  assert.match(pendingButton.props.className, /\bmobile-control-target\b/)
  assert.match(pendingButton.props.className, /focus-visible:ring-2/)
  assert.match(pendingButton.props.className, /md:min-h-0/)
  assert.match(pendingButton.props.className, /md:min-w-0/)
  assert.equal(walk(during).find(node => node.props?.role === 'status')?.props['aria-live'], 'polite')
  assert.deepEqual(calls, ['submitted'])
  resolveFlip()
  await pending

  const failed = render(Component, outputState(async () => { throw new Error('source revision changed') }))
  const failedButton = walk(failed).find(node => /Flip .* horizontally/.test(node.props?.['aria-label'] || ''))
  await failedButton.props.onClick({ stopPropagation() {} })
  const afterFailure = render(Component, outputState(async () => { throw new Error('source revision changed') }))
  const alert = walk(afterFailure).find(node => node.props?.role === 'alert')
  assert.match(alert.props.children, /source revision changed/)
})

async function loadScopedPoller() {
  const source = await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
  const start = source.indexOf('  _pollRecoveredJob: (jobId, expectedWorkspace) => {')
  const end = source.indexOf('  reconnectJobs:', start)
  assert.ok(start >= 0 && end > start, 'scoped poller must stay in a bounded store region')
  const method = source.slice(start, end)
    .replace(/^  _pollRecoveredJob: \(jobId, expectedWorkspace\) => \{/, `export function pollRecoveredJob(jobId, expectedWorkspace, context) {
      const { accountEpoch, recoveryJobPolls, jobNeedsFastStatusPoll, createRefreshTracker,
        accountIdentityIsCurrent, api, get, set, mergeJobStatus, publishTerminalJobStatus,
        activeOutputRefreshDue, activePollMs, queuedSafetyMs } = context
      const _accountIdentityEpoch = accountEpoch
      const _recoveryJobPolls = recoveryJobPolls
      const _jobNeedsFastStatusPoll = jobNeedsFastStatusPoll
      const _createActiveOutputRefreshTracker = createRefreshTracker
      const _accountIdentityIsCurrent = accountIdentityIsCurrent
      const _activeOutputRefreshDue = activeOutputRefreshDue
      const ACTIVE_JOB_STATUS_POLL_MS = activePollMs
      const QUEUED_JOB_STATUS_SAFETY_MS = queuedSafetyMs
      const _mergeJobStatus = mergeJobStatus
      const _publishTerminalJobStatus = publishTerminalJobStatus`)
    .replace(/\n  \},\s*$/, '\n}')
  const result = await transform(method, { loader: 'ts', format: 'esm', target: 'es2022' })
  return import(asDataModule(result.code)).then(module => module.pollRecoveredJob)
}

test('scoped poller drops a late response after the active project changes', async () => {
  const pollRecoveredJob = await loadScopedPoller()
  let resolveStatus
  let state = {
    activeWorkspace: 'project-a',
    jobs: [{ id: 'flip-job', status: 'running', outputFiles: [], phase: '' }],
  }
  let refreshes = 0
  let loads = 0
  const listeners = new Map()
  globalThis.window = {
    setTimeout(callback) { return setImmediate(callback) },
    clearTimeout(timer) { clearImmediate(timer) },
  }
  globalThis.document = {
    hidden: false,
    addEventListener(type, callback) { listeners.set(type, callback) },
    removeEventListener(type) { listeners.delete(type) },
  }
  const recoveryJobPolls = new Map()
  const context = {
    accountEpoch: 1,
    recoveryJobPolls,
    jobNeedsFastStatusPoll: job => job.status === 'running',
    createRefreshTracker: () => ({ outputSignature: '', phase: '', lastRefreshAt: 0, pendingDelta: false, hasRefreshed: false }),
    accountIdentityIsCurrent: value => value === 1,
    api: { fetchJobStatus: () => new Promise(resolve => { resolveStatus = resolve }) },
    get: () => ({ ...state, refreshOutputs: async () => { refreshes += 1 }, loadOutputs: () => { loads += 1 } }),
    set: update => { state = { ...state, ...(typeof update === 'function' ? update(state) : update) } },
    mergeJobStatus: (job, status) => ({ ...job, ...status }),
    publishTerminalJobStatus() {},
    activeOutputRefreshDue: () => false,
    activePollMs: 1,
    queuedSafetyMs: 1,
  }
  pollRecoveredJob('flip-job', 'project-a', context)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(typeof resolveStatus, 'function', 'poller should request status immediately for a running job')
  state = { ...state, activeWorkspace: 'project-b' }
  resolveStatus({ status: 'completed', progress: 100, output_files: [] })
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(state.jobs, [{ id: 'flip-job', status: 'running', outputFiles: [], phase: '' }])
  assert.equal(refreshes, 0)
  assert.equal(loads, 0)
  assert.equal(recoveryJobPolls.size, 0)
})
