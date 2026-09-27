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

test('browser-copy client submits only the selected project video identity', async () => {
  const { submitToolBrowserCopy } = await import('../src/api/client.ts')
  const calls = []
  const previousFetch = globalThis.fetch
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init })
    return { ok: true, json: async () => ({ job_id: 'copy-job', status: 'queued' }) }
  }
  try {
    assert.deepEqual(
      await submitToolBrowserCopy({ workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7' }),
      { job_id: 'copy-job', status: 'queued' },
    )
  } finally {
    globalThis.fetch = previousFetch
  }
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, '/api/v1/tools/browser-copy')
  assert.equal(calls[0].init.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7',
  })
})

async function loadFlipAction() {
  const source = await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
  const start = source.indexOf('  flipSelectedClip: async (output) => {')
  const end = source.indexOf('  createBrowserCopy:', start)
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

async function loadBrowserCopyAction() {
  const source = await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
  const start = source.indexOf('  createBrowserCopy: async (output) => {')
  const end = source.indexOf('  sendClipToTools:', start)
  assert.ok(start >= 0 && end > start, 'browser-copy action must stay in a bounded store region')
  const method = source.slice(start, end)
    .replace(/^  createBrowserCopy: async \(output\) => \{/, `export async function createBrowserCopy(output, context) {
      const { api, get, set, accountIdentityEpochValue, accountIdentityIsCurrent,
        discardStaleGenerationPlaceholder, isActiveGenerationJob } = context
      const _accountIdentityEpoch = accountIdentityEpochValue
      const _accountIdentityIsCurrent = accountIdentityIsCurrent
      const _discardStaleGenerationPlaceholder = discardStaleGenerationPlaceholder
      const _isActiveGenerationJob = isActiveGenerationJob`)
    .replace(/\n  \},\s*$/, '\n}')
  const result = await transform(method, { loader: 'ts', format: 'esm', target: 'es2022' })
  return import(asDataModule(result.code)).then(module => module.createBrowserCopy)
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
      submitToolBrowserCopy: async params => {
        submitted.push(params)
        return submit ? submit(params) : { job_id: 'copy-job', status: 'queued' }
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

test('browser-copy action queues and polls the exact project output', async () => {
  const createBrowserCopy = await loadBrowserCopyAction()
  const fixture = makeActionContext()
  globalThis.window = { dispatchEvent() {} }
  globalThis.CustomEvent = class CustomEvent { constructor(type) { this.type = type } }
  assert.equal(await createBrowserCopy({ workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7' }, fixture.context), 'copy-job')
  assert.deepEqual(fixture.submitted, [{ workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7' }])
  assert.equal(fixture.state.jobs[0].id, 'copy-job')
  assert.equal(fixture.state.jobs[0].status, 'queued')
  assert.deepEqual(fixture.pollCalls, [['copy-job', 'project-a']])

  const existing = makeActionContext({ submit: async () => ({ job_id: 'copy-already-running', status: 'running' }) })
  assert.equal(await createBrowserCopy({ workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7' }, existing.context), 'copy-already-running')
  assert.equal(existing.state.jobs[0].status, 'running')
  assert.deepEqual(existing.pollCalls, [['copy-already-running', 'project-a']])
})

test('browser-copy action drops stale account/project replies and refuses a wrong selection', async () => {
  const createBrowserCopy = await loadBrowserCopyAction()
  const wrongProject = makeActionContext({ workspace: 'project-b' })
  await assert.rejects(
    createBrowserCopy({ workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7' }, wrongProject.context),
    /current project/,
  )
  assert.equal(wrongProject.submitted.length, 0)

  let resolveSubmit
  const fixture = makeActionContext({ submit: () => new Promise(resolve => { resolveSubmit = resolve }) })
  const pending = createBrowserCopy({ workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7' }, fixture.context)
  fixture.setWorkspace('project-b')
  resolveSubmit({ job_id: 'late-copy', status: 'queued' })
  assert.equal(await pending, null)
  assert.equal(fixture.state.jobs.length, 0)

  let resolveAccountSubmit
  const accountFixture = makeActionContext({ submit: () => new Promise(resolve => { resolveAccountSubmit = resolve }) })
  const accountPending = createBrowserCopy({ workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7' }, accountFixture.context)
  accountFixture.setEpoch(2)
  resolveAccountSubmit({ job_id: 'late-account-copy', status: 'queued' })
  assert.equal(await accountPending, null)
  assert.equal(accountFixture.state.jobs.length, 0)
})

test('Gallery viewer offers a browser copy only after a revealed video fails to decode', async () => {
  const modules = new Map([
    ['react', `
      export function useState(initial) {
        const index = globalThis.__copyViewerHook++
        if (!(index in globalThis.__copyViewerState)) globalThis.__copyViewerState[index] = typeof initial === 'function' ? initial() : initial
        return [globalThis.__copyViewerState[index], value => {
          globalThis.__copyViewerState[index] = typeof value === 'function' ? value(globalThis.__copyViewerState[index]) : value
        }]
      }
      export function useRef(initial) { return { current: initial } }
      export function useEffect() {}
      export function useMemo(factory) { return factory() }
      export function useCallback(callback) { return callback }
    `],
    ['react/jsx-runtime', `
      export function jsx(type, props) { return { type, props: props || {} } }
      export const jsxs = jsx
      export const Fragment = Symbol('Fragment')
    `],
    ['react-dom', 'export function createPortal(children) { return children }'],
    ['lucide-react', `
      export const ArrowLeftRight='ArrowLeftRight', ChevronLeft='ChevronLeft', ChevronRight='ChevronRight';
      export const Columns2='Columns2', Eye='Eye', EyeOff='EyeOff', Film='Film', Loader2='Loader2', X='X';
    `],
    ['../../stores/useStore', 'export function useStore(select) { return select(globalThis.__copyViewerStore) }'],
    ['../../lib/modalFocus', `
      export function installModalFocus() { return () => {} }
      export function closeModalIfTop(_doc, _dialog, onClose) { onClose() }
    `],
    ['../../lib/privatePreview', `
      export function privatePreviewIdentity(workspace, name, revision) { return workspace + ':' + name + ':' + revision }
      export function privatePreviewWasRevealed() { return globalThis.__copyViewerRevealed }
      export function revealPrivatePreview() { globalThis.__copyViewerRevealed = true }
      export function hidePrivatePreview() { globalThis.__copyViewerRevealed = false }
      export function subscribePrivatePreviewChanges() { return () => {} }
    `],
  ])
  const result = await build({
    absWorkingDir: new URL('../', import.meta.url).pathname,
    entryPoints: [new URL('../src/components/MainContent/GalleryViewer.tsx', import.meta.url).pathname],
    bundle: true, format: 'cjs', platform: 'node', jsx: 'automatic', write: false, logLevel: 'silent',
    plugins: [{ name: 'browser-copy-viewer-mocks', setup(bundle) {
      bundle.onResolve({ filter: /.*/ }, args => modules.has(args.path)
        ? { path: args.path, namespace: 'copy-viewer-test' } : undefined)
      bundle.onLoad({ filter: /.*/, namespace: 'copy-viewer-test' }, args => ({
        contents: modules.get(args.path), loader: 'js',
      }))
    } }],
  })
  const compiled = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(
    (await import('node:module')).createRequire(import.meta.url), compiled, compiled.exports,
  )
  const Viewer = compiled.exports.GalleryViewer
  const file = {
    workspace: 'project-a', name: 'clip.mp4', revision: 'rev-7',
    url: '/api/v1/file/clip.mp4?workspace=project-a', type: 'video', private: true, size: 2048,
  }
  const calls = []
  globalThis.__copyViewerStore = { jobs: [], createBrowserCopy: async selected => {
    calls.push(selected)
    globalThis.__copyViewerStore.jobs = [{ id: 'copy-job', status: 'queued' }]
    return 'copy-job'
  } }
  globalThis.MediaError = { MEDIA_ERR_DECODE: 3, MEDIA_ERR_SRC_NOT_SUPPORTED: 4 }
  globalThis.document = { body: {}, getElementById: () => null }
  globalThis.__copyViewerState = []
  globalThis.__copyViewerRevealed = false
  let viewerFiles = [file]
  let initialIdentity = 'project-a:clip.mp4:rev-7'
  const renderViewer = () => {
    globalThis.__copyViewerHook = 0
    return Viewer({ files: viewerFiles, initialIdentity, restoreFocus: null, onClose() {} })
  }
  const allNodes = (node, found = []) => {
    if (!node || typeof node !== 'object') return found
    found.push(node)
    const children = node.props?.children
    for (const child of Array.isArray(children) ? children : [children]) allNodes(child, found)
    return found
  }
  const mediaInfo = () => allNodes(renderViewer()).find(node => node.type === 'p' && String(node.props?.children).includes('Media info:'))
  assert.equal(allNodes(renderViewer()).some(node => node.props?.children === 'Create H.264 browser copy'), false)
  assert.equal(mediaInfo(), undefined)
  globalThis.__copyViewerRevealed = true
  const video = allNodes(renderViewer()).find(node => node.type === 'video')
  assert.ok(video)
  assert.equal(String(mediaInfo()?.props.children).includes('2 KB'), true)
  video.props.onLoadedMetadata({ currentTarget: { videoWidth: 640, videoHeight: 360, duration: 5.17 } })
  const measured = mediaInfo()
  assert.match(String(measured?.props.children), /640 × 360.*5.17s.*2 KB/)
  viewerFiles = [file, { ...file, name: 'other.mp4', revision: 'rev-8', size: 4096 }]
  allNodes(renderViewer()).find(node => node.props?.['aria-label'] === 'Next media').props.onClick()
  const nextInfo = mediaInfo()
  assert.equal(String(nextInfo?.props.children).includes('640 × 360'), false)
  assert.equal(String(nextInfo?.props.children).includes('4 KB'), true)
  allNodes(renderViewer()).find(node => node.props?.['aria-label'] === 'Previous media').props.onClick()
  viewerFiles = [file]
  video.props.onLoadedMetadata({ currentTarget: { videoWidth: 0, videoHeight: 0 } })
  const fallback = allNodes(renderViewer())
  const copyButton = fallback.find(node => node.type === 'button' && node.props?.children === 'Create H.264 browser copy')
  assert.ok(copyButton)
  assert.equal(fallback.some(node => node.type === 'a' && node.props?.download === 'clip.mp4'), true)
  await copyButton.props.onClick()
  assert.deepEqual(calls, [file])
  const queued = allNodes(renderViewer())
  const queuedButton = queued.find(node => node.type === 'button' && node.props?.children === 'Browser copy queued')
  assert.equal(queuedButton.props.disabled, true)
  assert.equal(queued.some(node => node.props?.role === 'status' && String(node.props?.children).includes('copy is in Queue')), true)

  globalThis.__copyViewerStore.jobs = [{ id: 'copy-job', status: 'failed', error: 'The copy failed.' }]
  const failed = allNodes(renderViewer())
  const retryButton = failed.find(node => node.type === 'button' && node.props?.children === 'Retry H.264 browser copy')
  assert.equal(retryButton.props.disabled, false)
  assert.equal(failed.some(node => node.props?.role === 'alert' && node.props?.children === 'The copy failed.'), true)

  globalThis.__copyViewerStore.jobs = []
  viewerFiles = [file, { ...file, name: 'clip_browser_copy_copy-job.mp4', revision: 'rev-8' }]
  const completed = allNodes(renderViewer())
  assert.equal(completed.some(node => node.type === 'button' && node.props?.children === 'Browser copy ready'), true)
  assert.equal(completed.some(node => node.props?.role === 'status' && String(node.props?.children).includes('ready in Gallery')), true)

  viewerFiles = [file]
  const missing = allNodes(renderViewer())
  assert.equal(missing.some(node => node.type === 'button' && node.props?.children === 'Retry H.264 browser copy'), true)
  assert.equal(missing.some(node => node.props?.role === 'status' && String(node.props?.children).includes('no longer in Queue')), true)

  globalThis.__copyViewerState = []
  globalThis.__copyViewerStore.jobs = []
  const unsupported = allNodes(renderViewer()).find(node => node.type === 'video')
  unsupported.props.onError({ currentTarget: { error: { code: 4 } } })
  assert.equal(allNodes(renderViewer()).some(node => node.type === 'button' && node.props?.children === 'Create H.264 browser copy'), true)

  globalThis.__copyViewerState = []
  const networkError = allNodes(renderViewer()).find(node => node.type === 'video')
  networkError.props.onError({ currentTarget: { error: { code: 2 } } })
  const genericFailure = allNodes(renderViewer())
  assert.equal(genericFailure.some(node => node.type === 'button' && node.props?.children === 'Create H.264 browser copy'), false)
  assert.equal(genericFailure.some(node => node.props?.children === 'This media could not be loaded. Try opening it from the Gallery again.'), true)

  globalThis.__copyViewerState = []
  viewerFiles = [{ ...file, name: 'still.png', revision: 'rev-9', type: 'image', size: 1024 }]
  initialIdentity = 'project-a:still.png:rev-9'
  const imageView = allNodes(renderViewer()).find(node => typeof node.type === 'function')
  assert.ok(imageView)
  const image = imageView.type(imageView.props)
  image.props.onLoad({ currentTarget: { naturalWidth: 1024, naturalHeight: 768 } })
  const imageInfo = mediaInfo()
  assert.match(String(imageInfo?.props.children), /1024 × 768.*1 KB/)
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
  ['../../lib/format', `export function formatGenerationDuration(value) { return String(value) }
export function formatMediaDuration(value) { return String(value) }`],
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
