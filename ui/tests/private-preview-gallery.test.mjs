import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import test from 'node:test'

import { build } from 'esbuild'

import {
  hidePrivatePreview,
  hidePrivatePreviewsForWorkspace,
  privatePreviewIdentity,
  privatePreviewWasRevealed,
  privatePreviewWorkspaceHasRevealed,
  revealPrivatePreview,
  setPrivatePreviewsForWorkspaceRevealed,
  subscribePrivatePreviewChanges,
  subscribePrivatePreviewReveal,
} from '../src/lib/privatePreview.ts'
import { closeModalIfTop, installModalFocus } from '../src/lib/modalFocus.ts'
import { requestThumbnail } from '../src/lib/thumbnailCache.ts'

class SessionStorageFake {
  #values = new Map()

  get length() { return this.#values.size }
  key(index) { return [...this.#values.keys()][index] ?? null }
  getItem(key) { return this.#values.get(key) ?? null }
  setItem(key, value) { this.#values.set(key, String(value)) }
  removeItem(key) { this.#values.delete(key) }
}

const mainContentUrl = new URL('../src/components/MainContent/MainContent.tsx', import.meta.url)
const tabFilterUrl = new URL('../src/components/MainContent/TabFilter.tsx', import.meta.url)
const mediaFeedItemUrl = new URL('../src/components/MainContent/MediaFeedItem.tsx', import.meta.url)
const thumbnailsUrl = new URL('../src/components/MainContent/ThumbnailGallery.tsx', import.meta.url)
const referencesUrl = new URL('../src/components/Sidebar/ProjectReferenceLibrary.tsx', import.meta.url)
const storeUrl = new URL('../src/stores/useStore.ts', import.meta.url)
const privatePreviewUrl = new URL('../src/lib/privatePreview.ts', import.meta.url)

class FakeVideo {
  muted = false
  preload = ''
  videoWidth = 320
  videoHeight = 180
  onloadeddata = null
  onseeked = null
  onerror = null
  paused = 0
  loads = 0
  removed = 0
  source = ''

  set src(value) { this.source = value }
  get src() { return this.source }
  set currentTime(_value) { queueMicrotask(() => this.onseeked?.()) }
  get currentTime() { return 0.1 }
  pause() { this.paused += 1 }
  load() { this.loads += 1 }
  removeAttribute(name) {
    if (name !== 'src') return
    this.source = ''
    this.removed += 1
  }
}

class ThrowingSessionStorage {
  get length() { throw new Error('storage disabled') }
  key() { throw new Error('storage disabled') }
  getItem() { throw new Error('storage disabled') }
  setItem() { throw new Error('storage disabled') }
  removeItem() { throw new Error('storage disabled') }
}

class ModalTestDocument extends EventTarget {
  activeElement = null
  body
  defaultView = { getComputedStyle: () => ({ display: 'block', visibility: 'visible' }) }

  constructor() {
    super()
    this.appRoot = new ModalTestElement(this, 'app root')
    this.body = new ModalTestElement(this, 'body')
    this.body.style = { overflow: 'auto' }
  }

  getElementById(id) { return id === 'root' ? this.appRoot : null }
}

class ModalTestElement {
  attributes = new Map()
  descendants = new Set()
  focusable = []
  isConnected = true

  constructor(document, name) {
    this.document = document
    this.name = name
  }

  focus() { this.document.activeElement = this }
  hasAttribute(name) { return this.attributes.has(name) }
  getAttribute(name) { return this.attributes.get(name) ?? null }
  setAttribute(name, value = '') { this.attributes.set(name, String(value)) }
  removeAttribute(name) { this.attributes.delete(name) }
  contains(element) { return element === this || this.descendants.has(element) }
  querySelectorAll() { return this.focusable }
  closest() { return null }
}

function fakeThumbnailDocument(videos) {
  return {
    createElement(tag) {
      if (tag === 'video') {
        const video = new FakeVideo()
        videos.push(video)
        return video
      }
      if (tag === 'canvas') {
        return {
          width: 0,
          height: 0,
          getContext: () => ({ drawImage() {} }),
          toDataURL: () => 'data:image/webp;base64,thumbnail',
        }
      }
      throw new Error(`Unexpected element ${tag}`)
    },
  }
}

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 20; attempt++) {
    if (predicate()) return
    await new Promise(resolve => setTimeout(resolve, 0))
  }
  assert.fail('Condition did not become true')
}

async function loadThumbnailGalleryHarness() {
  const modules = new Map([
    ['react', `
      export function useState(initial) {
        const value = globalThis.__galleryStateValues?.length
          ? globalThis.__galleryStateValues.shift()
          : (typeof initial === 'function' ? initial() : initial)
        return [value, update => {
          const next = typeof update === 'function' ? update(value) : update
          ;(globalThis.__galleryStateUpdates ||= []).push(next)
        }]
      }
      export function useEffect(effect) {
        const cleanup = effect()
        if (typeof cleanup === 'function') globalThis.__galleryEffectCleanups.push(cleanup)
      }
      export function useRef(initial) {
        return {
          current: globalThis.__galleryRefValues?.length
            ? globalThis.__galleryRefValues.shift()
            : initial,
        }
      }
      export function useMemo(factory) { return factory() }
      export function useCallback(callback) { return callback }
    `],
    ['react/jsx-runtime', `
      export const Fragment = Symbol('Fragment')
      export function jsx(type, props, key) { return { type, props: props || {}, key } }
      export const jsxs = jsx
    `],
    ['react-dom', `
      export function createPortal(children, target) {
        ;(globalThis.__galleryPortalTargets ||= []).push(target)
        return globalThis.__galleryMountPortal?.(children, target) ?? children
      }
    `],
    ['lucide-react', `
      const icon = props => ({ type: 'svg', props: props || {} })
      export const Eye = icon
      export const EyeOff = icon
      export const Film = icon
      export const Music = icon
      export const PanelRightClose = icon
      export const PanelRightOpen = icon
      export const X = icon
    `],
    ['../../stores/useStore', `
      export function useStore(selector) { return selector(globalThis.__galleryStoreState) }
      useStore.getState = () => globalThis.__galleryStoreState
    `],
    ['../../lib/thumbnailCache', `
      export function requestThumbnail(src, cacheKey, signal) {
        globalThis.__galleryThumbnailRequests.push({ src, cacheKey, signal })
        return new Promise(() => {})
      }
    `],
    ['../../lib/useIsMobile', 'export function useIsMobile() { return Boolean(globalThis.__galleryIsMobile) }'],
    ['../../lib/modalFocus', `
      export function installModalFocus(options) {
        return globalThis.__galleryInstallModalFocus(options)
      }
      export function closeModalIfTop(document, dialog, onClose) {
        return globalThis.__galleryCloseModalIfTop(document, dialog, onClose)
      }
    `],
    ['../../lib/privatePreview', `
      export function privatePreviewIdentity(workspace, name, revision = '') {
        return workspace + '\u0000' + name + '\u0000' + revision
      }
      export function privatePreviewWasRevealed(identity) {
        return globalThis.__galleryRevealed.has(identity)
      }
      export function revealPrivatePreview(identity) { globalThis.__galleryRevealed.add(identity) }
      export function subscribePrivatePreviewChanges() { return () => {} }
    `],
  ])
  const result = await build({
    entryPoints: [new URL('../src/components/MainContent/ThumbnailGallery.tsx', import.meta.url).pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'thumbnail-gallery-test-mocks',
      setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => (
          modules.has(args.path) ? { path: args.path, namespace: 'gallery-test' } : undefined
        ))
        builder.onLoad({ filter: /.*/, namespace: 'gallery-test' }, args => ({
          contents: modules.get(args.path),
          loader: 'js',
        }))
      },
    }],
  })
  const compiledModule = { exports: {} }
  const require = createRequire(import.meta.url)
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(
    require,
    compiledModule,
    compiledModule.exports,
  )
  return compiledModule.exports.ThumbnailGallery
}

async function loadMediaFeedItemHarness() {
  const iconNames = [
    'Play', 'Pencil', 'RefreshCw', 'Copy', 'Trash2', 'Check', 'Combine',
    'Loader2', 'Heart', 'ArrowLeftToLine', 'Download', 'FolderInput',
    'Scissors', 'FastForward', 'BookMarked', 'EyeOff', 'Share2', 'Link2Off',
  ]
  const modules = new Map([
    ['react', `
      export function useState(initial) {
        return [typeof initial === 'function' ? initial() : initial, () => {}]
      }
      export function useEffect() {}
      export function useRef(initial) { return { current: initial } }
      export function useCallback(callback) { return callback }
    `],
    ['react/jsx-runtime', `
      export const Fragment = Symbol('Fragment')
      export function jsx(type, props, key) { return { type, props: props || {}, key } }
      export const jsxs = jsx
    `],
    ['lucide-react', `
      const icon = props => ({ type: 'svg', props: props || {} })
      ${iconNames.map(name => `export const ${name} = icon`).join('\n')}
    `],
    ['../Recipes/SaveRecipeDialog', 'export function SaveRecipeDialog() { return null }'],
    ['../../stores/useStore', `
      export function useStore(selector) { return selector(globalThis.__mediaFeedStore) }
      useStore.getState = () => globalThis.__mediaFeedStore
      useStore.setState = () => {}
    `],
    ['../../api/client', `
      export const createOutputShare = async () => ({})
      export const deleteOutputComponents = async () => ({ failed: [] })
      export const getUploadUrl = value => '/uploads/' + value
      export const fetchOutputMetadata = async () => null
      export const getFileUrl = name => '/files/' + name
      export const moveOutput = async () => {}
      export const revokeOutputShare = async () => {}
      export const uploadImage = async () => ({ path: '' })
    `],
    ['../../lib/modelDisplay', 'export function modelDisplayName() { return "" }'],
    ['../../lib/privatePreview', `
      export function privatePreviewIdentity(workspace, name, revision = '') {
        return workspace + '\\u0000' + name + '\\u0000' + revision
      }
      export function privatePreviewWasRevealed(identity) {
        return globalThis.__mediaFeedRevealed.has(identity)
      }
      export function revealPrivatePreview(identity) { globalThis.__mediaFeedRevealed.add(identity) }
      export function hidePrivatePreview(identity) { globalThis.__mediaFeedRevealed.delete(identity) }
      export function subscribePrivatePreviewReveal() { return () => {} }
    `],
  ])
  const result = await build({
    entryPoints: [mediaFeedItemUrl.pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'media-feed-item-test-mocks',
      setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => (
          modules.has(args.path) ? { path: args.path, namespace: 'media-feed-test' } : undefined
        ))
        builder.onLoad({ filter: /.*/, namespace: 'media-feed-test' }, args => ({
          contents: modules.get(args.path),
          loader: 'js',
        }))
      },
    }],
  })
  const compiledModule = { exports: {} }
  const require = createRequire(import.meta.url)
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(
    require,
    compiledModule,
    compiledModule.exports,
  )
  return compiledModule.exports.MediaFeedItem
}

async function loadTabFilterHarness() {
  const modules = new Map([
    ['react', `
      export function useState(initial) { return globalThis.__tabFilterHooks.useState(initial) }
      export function useEffect(effect, dependencies) { return globalThis.__tabFilterHooks.useEffect(effect, dependencies) }
      export function useRef(initial) { return globalThis.__tabFilterHooks.useRef(initial) }
    `],
    ['react/jsx-runtime', `
      export const Fragment = Symbol('Fragment')
      export function jsx(type, props, key) { return { type, props: props || {}, key } }
      export const jsxs = jsx
    `],
    ['lucide-react', `
      const icon = props => ({ type: 'svg', props: props || {} })
      export const Heart = icon
      export const Film = icon
      export const Search = icon
      export const SlidersHorizontal = icon
      export const X = icon
    `],
    ['../../stores/useStore', `
      export function useStore(selector) { return selector(globalThis.__tabFilterStore) }
    `],
  ])
  const result = await build({
    entryPoints: [tabFilterUrl.pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'tab-filter-test-mocks',
      setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => (
          modules.has(args.path) ? { path: args.path, namespace: 'tab-filter-test' } : undefined
        ))
        builder.onLoad({ filter: /.*/, namespace: 'tab-filter-test' }, args => ({
          contents: modules.get(args.path),
          loader: 'js',
        }))
      },
    }],
  })
  const compiledModule = { exports: {} }
  const require = createRequire(import.meta.url)
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(
    require,
    compiledModule,
    compiledModule.exports,
  )
  return compiledModule.exports.TabFilter
}

function createTabFilterRuntime(stateSeeds = {}, storeOverrides = {}) {
  const states = []
  const stateInitialized = new Set()
  const refs = []
  const effects = []
  const cleanups = []
  let stateCursor = 0
  let refCursor = 0
  const calls = { media: [], artifact: [], search: [], reset: 0 }
  const store = {
    mediaFilter: 'all',
    outputArtifactScope: 'final',
    outputSearchQuery: '',
    selectedOutputKeys: ['default\u0000kept.png'],
    setMediaFilter(value) {
      calls.media.push(value)
      store.mediaFilter = value
      store.selectedOutputKeys = []
    },
    setOutputArtifactScope(value) {
      calls.artifact.push(value)
      store.outputArtifactScope = value
      store.selectedOutputKeys = []
    },
    setOutputSearchQuery(value) {
      calls.search.push(value)
      store.outputSearchQuery = value
    },
    resetGalleryFilters() { calls.reset += 1 },
    ...storeOverrides,
  }
  const listeners = new Map()
  const fakeDocument = {
    addEventListener(type, listener) { listeners.set(type, listener) },
    removeEventListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type)
    },
    dispatch(type, event) { listeners.get(type)?.(event) },
  }
  const fakeWindow = {
    setTimeout(callback) { callback(); return 1 },
    clearTimeout() {},
    requestAnimationFrame(callback) { callback(); return 1 },
    cancelAnimationFrame() {},
  }
  const hooks = {
    begin() {
      stateCursor = 0
      refCursor = 0
      effects.length = 0
    },
    useState(initial) {
      const index = stateCursor++
      if (!stateInitialized.has(index)) {
        states[index] = Object.hasOwn(stateSeeds, index)
          ? stateSeeds[index]
          : typeof initial === 'function' ? initial() : initial
        stateInitialized.add(index)
      }
      return [states[index], value => {
        states[index] = typeof value === 'function' ? value(states[index]) : value
      }]
    },
    useRef(initial) {
      const index = refCursor++
      if (!refs[index]) refs[index] = { current: initial }
      return refs[index]
    },
    useEffect(effect) { effects.push(effect) },
  }
  return {
    calls,
    cleanups,
    document: fakeDocument,
    hooks,
    refs,
    states,
    store,
    window: fakeWindow,
    flushEffects() {
      for (const effect of effects.splice(0)) {
        const cleanup = effect()
        if (typeof cleanup === 'function') cleanups.push(cleanup)
      }
    },
    cleanup() {
      for (const cleanup of cleanups.splice(0).reverse()) cleanup()
    },
  }
}

async function loadProjectAssetPreviewHarness() {
  const modules = new Map([
    ['react', `
      export function useState(initial) {
        return [typeof initial === 'function' ? initial() : initial, () => {}]
      }
      export function useEffect(effect) {
        const cleanup = effect()
        if (typeof cleanup === 'function') globalThis.__referenceEffectCleanups.push(cleanup)
      }
      export const useLayoutEffect = useEffect
      export function useRef(initial) { return { current: initial } }
      export function useMemo(factory) { return factory() }
      export function useCallback(callback) { return callback }
    `],
    ['react/jsx-runtime', `
      export const Fragment = Symbol('Fragment')
      export function jsx(type, props, key) { return { type, props: props || {}, key } }
      export const jsxs = jsx
    `],
    ['lucide-react', `
      const icon = props => ({ type: 'svg', props: props || {} })
      export const Check = icon
      export const ChevronDown = icon
      export const EyeOff = icon
      export const FileUp = icon
      export const ImagePlus = icon
      export const Library = icon
      export const Loader2 = icon
      export const MapPin = icon
      export const Package = icon
      export const Pencil = icon
      export const RotateCcw = icon
      export const Trash2 = icon
      export const UserRound = icon
      export const X = icon
    `],
    ['../../stores/useStore', 'export function useStore() { return {} }'],
    ['./BlenderSceneTool', 'export function BlenderSceneTool() { return null }'],
    ['../../lib/hostTerms', 'export const HOST_TERM_NOTICES = {}'],
    ['../../lib/privatePreview', `
      export function privatePreviewIdentity(workspace, name, revision = '') {
        return workspace + '\\u0000' + name + '\\u0000' + revision
      }
      export function privatePreviewWasRevealed(identity) {
        return globalThis.__referenceRevealed.has(identity)
      }
      export function revealPrivatePreview(identity) {
        globalThis.__referenceRevealed.add(identity)
        globalThis.__referenceSessionChanges.push(['reveal', identity])
      }
      export function hidePrivatePreview(identity) {
        globalThis.__referenceRevealed.delete(identity)
        globalThis.__referenceSessionChanges.push(['hide', identity])
      }
      export function subscribePrivatePreviewReveal() { return () => {} }
    `],
    ['../../lib/useVisibilityPolling', `
      export const POLL_INTERVAL_MS = 1
      export function useVisibilityPolling() {}
    `],
  ])
  const result = await build({
    entryPoints: [referencesUrl.pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'project-asset-preview-test-mocks',
      setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => (
          modules.has(args.path) ? { path: args.path, namespace: 'reference-test' } : undefined
        ))
        builder.onLoad({ filter: /.*/, namespace: 'reference-test' }, args => ({
          contents: modules.get(args.path),
          loader: 'js',
        }))
        builder.onLoad({ filter: /ProjectReferenceLibrary\.tsx$/ }, async args => ({
          contents: `${await readFile(args.path, 'utf8')}\nexport { ProjectAssetPreview }\n`,
          loader: 'tsx',
        }))
      },
    }],
  })
  const compiledModule = { exports: {} }
  const require = createRequire(import.meta.url)
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(
    require,
    compiledModule,
    compiledModule.exports,
  )
  return compiledModule.exports.ProjectAssetPreview
}

function materialize(element) {
  if (Array.isArray(element)) return element.map(materialize)
  if (element === null || element === undefined || typeof element !== 'object') return element
  if (typeof element.type === 'function') return materialize(element.type(element.props))
  return {
    ...element,
    props: {
      ...element.props,
      children: materialize(element.props?.children),
    },
  }
}

function findElements(element, predicate, matches = []) {
  if (Array.isArray(element)) {
    for (const child of element) findElements(child, predicate, matches)
    return matches
  }
  if (element === null || element === undefined || typeof element !== 'object') return matches
  if (predicate(element)) matches.push(element)
  findElements(element.props?.children, predicate, matches)
  return matches
}

function cleanupGalleryEffects() {
  for (const cleanup of globalThis.__galleryEffectCleanups.splice(0).reverse()) cleanup()
}

function cleanupReferenceEffects() {
  for (const cleanup of globalThis.__referenceEffectCleanups.splice(0).reverse()) cleanup()
}

test('project bulk toggle covers none, some, all, pagination, and per-item overrides', t => {
  const originalWindow = globalThis.window
  const originalSessionStorage = globalThis.sessionStorage
  const eventTarget = new EventTarget()
  globalThis.window = eventTarget
  globalThis.sessionStorage = new SessionStorageFake()
  t.after(() => {
    globalThis.window = originalWindow
    globalThis.sessionStorage = originalSessionStorage
  })

  const alphaOne = privatePreviewIdentity('alpha', 'one.mp4', 'r1')
  const alphaTwo = privatePreviewIdentity('alpha', 'two.mp4', 'r2')
  const alphabet = privatePreviewIdentity('alphabet', 'keep.mp4', 'r1')
  const beta = privatePreviewIdentity('beta', 'keep.mp4', 'r1')
  assert.equal(privatePreviewWorkspaceHasRevealed('alpha'), false)
  assert.equal(privatePreviewWorkspaceHasRevealed('alpha', 'all'), false)
  assert.equal(privatePreviewWasRevealed(alphaOne), false)

  revealPrivatePreview(alphaOne)
  assert.equal(privatePreviewWorkspaceHasRevealed('alpha'), true)
  assert.equal(privatePreviewWorkspaceHasRevealed('alpha', 'all'), false)
  assert.equal(privatePreviewWasRevealed(alphaOne), true)
  assert.equal(privatePreviewWasRevealed(alphaTwo), false)
  revealPrivatePreview(alphabet)
  revealPrivatePreview(beta)

  const changes = []
  const itemChanges = []
  const unsubscribe = subscribePrivatePreviewChanges((identity, revealed, workspace) => {
    changes.push({ identity, revealed, workspace })
  })
  const unsubscribeAlphaOne = subscribePrivatePreviewReveal(alphaOne, revealed => {
    itemChanges.push(['one', revealed])
  })
  const unsubscribeAlphaTwo = subscribePrivatePreviewReveal(alphaTwo, revealed => {
    itemChanges.push(['two', revealed])
  })
  setPrivatePreviewsForWorkspaceRevealed('alpha', true)

  assert.equal(privatePreviewWasRevealed(alphaOne), true)
  assert.equal(privatePreviewWasRevealed(alphaTwo), true)
  assert.equal(privatePreviewWorkspaceHasRevealed('alpha', 'all'), true)
  const newlyPaginated = privatePreviewIdentity('alpha', 'new-page.mp4', 'r9')
  assert.equal(privatePreviewWasRevealed(newlyPaginated), true)
  assert.equal(privatePreviewWasRevealed(alphabet), true)
  assert.equal(privatePreviewWasRevealed(beta), true)
  assert.deepEqual(changes, [{ identity: null, revealed: true, workspace: 'alpha' }])
  assert.deepEqual(itemChanges, [['one', true], ['two', true]])

  changes.length = 0
  itemChanges.length = 0
  hidePrivatePreview(alphaOne)
  assert.equal(privatePreviewWasRevealed(alphaOne), false)
  assert.equal(privatePreviewWasRevealed(alphaTwo), true)
  assert.equal(privatePreviewWasRevealed(newlyPaginated), true)
  assert.equal(privatePreviewWorkspaceHasRevealed('alpha'), true)
  assert.equal(privatePreviewWorkspaceHasRevealed('alpha', 'all'), false)
  assert.deepEqual(itemChanges, [['one', false]])

  changes.length = 0
  itemChanges.length = 0
  revealPrivatePreview(alphaOne)
  assert.equal(privatePreviewWasRevealed(alphaOne), true)
  assert.equal(privatePreviewWorkspaceHasRevealed('alpha', 'all'), true)
  assert.deepEqual(itemChanges, [['one', true]])

  changes.length = 0
  itemChanges.length = 0
  hidePrivatePreviewsForWorkspace('alpha')
  assert.equal(privatePreviewWorkspaceHasRevealed('alpha'), false)
  assert.equal(privatePreviewWorkspaceHasRevealed('alpha', 'all'), false)
  assert.equal(privatePreviewWasRevealed(alphaOne), false)
  assert.equal(privatePreviewWasRevealed(alphaTwo), false)
  assert.equal(privatePreviewWasRevealed(newlyPaginated), false)
  assert.equal(privatePreviewWasRevealed(alphabet), true)
  assert.equal(privatePreviewWasRevealed(beta), true)
  assert.deepEqual(changes, [{ identity: null, revealed: false, workspace: 'alpha' }])
  assert.deepEqual(itemChanges, [['one', false], ['two', false]])

  unsubscribe()
  unsubscribeAlphaOne()
  unsubscribeAlphaTwo()
})

test('storage failure keeps session-only bulk and per-item controls coherent', t => {
  const originalWindow = globalThis.window
  const originalSessionStorage = globalThis.sessionStorage
  globalThis.window = new EventTarget()
  globalThis.sessionStorage = new ThrowingSessionStorage()
  t.after(() => {
    globalThis.window = originalWindow
    globalThis.sessionStorage = originalSessionStorage
  })

  const workspace = 'storage-failure-project'
  const first = privatePreviewIdentity(workspace, 'first.mp4', 'r1')
  const later = privatePreviewIdentity(workspace, 'later.mp4', 'r2')
  setPrivatePreviewsForWorkspaceRevealed(workspace, true)
  assert.equal(privatePreviewWorkspaceHasRevealed(workspace, 'all'), true)
  assert.equal(privatePreviewWasRevealed(first), true)
  assert.equal(privatePreviewWasRevealed(later), true)
  hidePrivatePreview(first)
  assert.equal(privatePreviewWorkspaceHasRevealed(workspace, 'all'), false)
  assert.equal(privatePreviewWasRevealed(first), false)
  assert.equal(privatePreviewWasRevealed(later), true)
  revealPrivatePreview(first)
  assert.equal(privatePreviewWasRevealed(first), true)
  assert.equal(privatePreviewWorkspaceHasRevealed(workspace, 'all'), true)
  hidePrivatePreviewsForWorkspace(workspace)
  assert.equal(privatePreviewWasRevealed(first), false)
  assert.equal(privatePreviewWasRevealed(later), false)
})

test('bulk activation reads live state across rapid repeats and workspace changes', t => {
  const originalWindow = globalThis.window
  const originalSessionStorage = globalThis.sessionStorage
  globalThis.window = new EventTarget()
  globalThis.sessionStorage = new SessionStorageFake()
  t.after(() => {
    globalThis.window = originalWindow
    globalThis.sessionStorage = originalSessionStorage
  })

  const activate = workspace => setPrivatePreviewsForWorkspaceRevealed(
    workspace,
    !privatePreviewWorkspaceHasRevealed(workspace, 'all'),
  )
  const alphaItem = privatePreviewIdentity('rapid-alpha', 'one.mp4', 'r1')
  const betaItem = privatePreviewIdentity('rapid-beta', 'one.mp4', 'r1')

  revealPrivatePreview(alphaItem)
  activate('rapid-alpha')
  assert.equal(privatePreviewWorkspaceHasRevealed('rapid-alpha', 'all'), true)
  assert.equal(privatePreviewWasRevealed(alphaItem), true)
  activate('rapid-alpha')
  assert.equal(privatePreviewWorkspaceHasRevealed('rapid-alpha'), false)
  assert.equal(privatePreviewWasRevealed(alphaItem), false)

  activate('rapid-beta')
  assert.equal(privatePreviewWorkspaceHasRevealed('rapid-beta', 'all'), true)
  assert.equal(privatePreviewWasRevealed(betaItem), true)
  assert.equal(privatePreviewWorkspaceHasRevealed('rapid-alpha'), false)
})

test('private audio and retry images acquire no media URL before reveal', async t => {
  const noop = () => {}
  globalThis.__mediaFeedRevealed = new Set()
  globalThis.__mediaFeedStore = {
    setSelectedOutput: noop,
    loadSettingsFromOutput: noop,
    rerollGeneration: noop,
    deleteSelectedOutput: noop,
    rejoinClipGroup: noop,
    toggleFavorite: noop,
    setStartImage: noop,
    addImageRef: noop,
    setContinueVideo: noop,
    setParam: noop,
    openRetakeDialog: noop,
    generationMode: 'video',
    workspaces: [],
    accessContext: null,
    browsingUploads: false,
    models: [],
    gallerySelectionMode: false,
    selectedOutputKeys: [],
    toggleOutputSelection: noop,
    saveRecipeFromOutput: noop,
  }
  t.after(() => {
    delete globalThis.__mediaFeedRevealed
    delete globalThis.__mediaFeedStore
  })

  const MediaFeedItem = await loadMediaFeedItemHarness()
  const render = file => materialize(MediaFeedItem({
    file: {
      favorite: false,
      linked_component_count: 0,
      artifact_class: 'final',
      revision: 'r1',
      workspace: 'private-media',
      private: true,
      ...file,
    },
    index: 0,
    isActive: true,
    onVisible: noop,
    measurementEpoch: 0,
    onMeasured: noop,
  }))
  const mediaSources = tree => findElements(tree, element => (
    element.type === 'img' || element.type === 'audio' || element.type === 'video'
  )).map(element => element.props?.src).filter(Boolean)

  const image = { name: 'private.png', type: 'image', url: '/private.png' }
  const audio = { name: 'private.wav', type: 'audio', url: '/private.wav' }
  assert.deepEqual(mediaSources(render(image)), [])
  assert.deepEqual(mediaSources(render(audio)), [])

  globalThis.__mediaFeedRevealed.add(privatePreviewIdentity('private-media', image.name, 'r1'))
  globalThis.__mediaFeedRevealed.add(privatePreviewIdentity('private-media', audio.name, 'r1'))
  assert.deepEqual(mediaSources(render(image)), ['/private.png'])
  assert.deepEqual(mediaSources(render(audio)), ['/private.wav'])
})

test('private image and video thumbnails acquire sources only while revealed', async t => {
  globalThis.__galleryEffectCleanups = []
  globalThis.__galleryThumbnailRequests = []
  globalThis.__galleryRevealed = new Set()
  const outputs = [
    {
      name: 'private-image.png',
      url: '/private-image.png',
      type: 'image',
      revision: 'image-r1',
      workspace: 'private-gallery',
      private: true,
    },
    {
      name: 'private-video.mp4',
      url: '/private-video.mp4',
      type: 'video',
      revision: 'video-r1',
      workspace: 'private-gallery',
      private: true,
    },
  ]
  globalThis.__galleryStoreState = {
    filteredOutputs: () => outputs,
    outputs,
    outputsTotal: outputs.length,
    outputsLoading: false,
  }
  t.after(() => {
    cleanupGalleryEffects()
    delete globalThis.__galleryEffectCleanups
    delete globalThis.__galleryThumbnailRequests
    delete globalThis.__galleryRevealed
    delete globalThis.__galleryStoreState
  })

  const ThumbnailGallery = await loadThumbnailGalleryHarness()
  const renderGallery = () => materialize(ThumbnailGallery({
    activeIndex: 0,
    onThumbnailClick() {},
  }))
  const sources = tree => findElements(tree, element => (
    typeof element.props?.src === 'string'
  )).map(element => element.props.src)

  const blurred = renderGallery()
  assert.deepEqual(sources(blurred), [])
  assert.deepEqual(globalThis.__galleryThumbnailRequests, [])

  const privateButtons = findElements(blurred, element => (
    element.type === 'button' && Number.isInteger(element.props?.['data-thumb-index'])
  ))
  assert.equal(privateButtons.length, 2)
  privateButtons[0].props.onClick()
  privateButtons[1].props.onClick()
  cleanupGalleryEffects()

  const revealed = renderGallery()
  assert.deepEqual(sources(revealed), ['/private-image.png'])
  assert.equal(globalThis.__galleryThumbnailRequests.length, 1)
  assert.equal(globalThis.__galleryThumbnailRequests[0].src, '/private-video.mp4')
  assert.equal(globalThis.__galleryThumbnailRequests[0].signal.aborted, false)

  cleanupGalleryEffects()
  assert.equal(globalThis.__galleryThumbnailRequests[0].signal.aborted, true)
  globalThis.__galleryRevealed.clear()
  const reblurred = renderGallery()
  assert.deepEqual(sources(reblurred), [])
  assert.equal(globalThis.__galleryThumbnailRequests.length, 1)

  cleanupGalleryEffects()
  globalThis.__galleryRevealed.add(privatePreviewIdentity(
    'private-gallery',
    'private-video.mp4',
    'video-r1',
  ))
  renderGallery()
  assert.equal(globalThis.__galleryThumbnailRequests.length, 2)
  assert.equal(globalThis.__galleryThumbnailRequests[1].signal.aborted, false)
  cleanupGalleryEffects()
  assert.equal(globalThis.__galleryThumbnailRequests[1].signal.aborted, true)
})

test('mobile thumbnail overlay joins the modal stack with top-only dismissal and focus restoration', async t => {
  globalThis.__galleryEffectCleanups = []
  globalThis.__galleryThumbnailRequests = []
  globalThis.__galleryRevealed = new Set()
  globalThis.__galleryIsMobile = true
  globalThis.__galleryStateValues = []
  globalThis.__galleryStateUpdates = []
  globalThis.__galleryRefValues = []
  globalThis.__galleryPortalTargets = []
  globalThis.__galleryMountPortal = (children, target) => {
    const mount = element => {
      if (Array.isArray(element)) {
        for (const child of element) mount(child)
        return
      }
      if (element === null || element === undefined || typeof element !== 'object') return
      if (element.props?.ref?.current) target.descendants.add(element.props.ref.current)
      mount(element.props?.children)
    }
    mount(children)
    return children
  }
  globalThis.__galleryInstallModalFocus = installModalFocus
  globalThis.__galleryCloseModalIfTop = closeModalIfTop
  globalThis.__galleryStoreState = {
    filteredOutputs: () => [{
      name: 'private-image.png',
      url: '/private-image.png',
      type: 'image',
      revision: 'r1',
      workspace: 'mobile-private',
      private: true,
    }],
    outputs: [],
    outputsTotal: 1,
    outputsLoading: false,
  }
  let toggles = 0
  const selected = []
  const originalDocument = globalThis.document
  t.after(() => {
    cleanupGalleryEffects()
    delete globalThis.__galleryEffectCleanups
    delete globalThis.__galleryThumbnailRequests
    delete globalThis.__galleryRevealed
    delete globalThis.__galleryIsMobile
    delete globalThis.__galleryStoreState
    delete globalThis.__galleryStateValues
    delete globalThis.__galleryStateUpdates
    delete globalThis.__galleryRefValues
    delete globalThis.__galleryPortalTargets
    delete globalThis.__galleryMountPortal
    delete globalThis.__galleryInstallModalFocus
    delete globalThis.__galleryCloseModalIfTop
    globalThis.document = originalDocument
  })

  const modalDocument = new ModalTestDocument()
  globalThis.document = modalDocument
  const outerOpener = new ModalTestElement(modalDocument, 'outer opener')
  const parentDialog = new ModalTestElement(modalDocument, 'parent dialog')
  const parentClose = new ModalTestElement(modalDocument, 'parent close')
  const openerNode = new ModalTestElement(modalDocument, 'thumbnail opener')
  const closeNode = new ModalTestElement(modalDocument, 'thumbnail close')
  const dialogNode = new ModalTestElement(modalDocument, 'thumbnail dialog')
  parentDialog.focusable = [parentClose, openerNode]
  parentDialog.descendants = new Set([parentClose, openerNode])
  dialogNode.focusable = [closeNode]
  dialogNode.descendants = new Set([closeNode])
  outerOpener.focus()
  let parentCloseRequests = 0
  const cleanupParent = installModalFocus({
    document: modalDocument,
    dialog: parentDialog,
    initialFocus: parentClose,
    restoreFocus: outerOpener,
    appRoot: modalDocument.appRoot,
    onClose: () => { parentCloseRequests += 1 },
    priority: 60,
  })

  const ThumbnailGallery = await loadThumbnailGalleryHarness()
  globalThis.__galleryStateValues = [false, false]
  globalThis.__galleryRefValues = [openerNode, closeNode, dialogNode]
  let tree = materialize(ThumbnailGallery({
    activeIndex: 0,
    onThumbnailClick(index) { selected.push(index) },
    privatePreviewControl: {
      workspace: 'mobile-private',
      state: 'some',
      onToggle() { toggles += 1 },
    },
  }))
  const opener = findElements(tree, element => element.props?.['aria-controls'] === 'mobile-thumbnail-panel')[0]
  assert.ok(opener)
  assert.equal(opener.props.type, 'button')
  assert.match(opener.props.className, /min-h-11 min-w-11/)
  opener.props.onClick()
  assert.deepEqual(globalThis.__galleryStateUpdates, [true])

  cleanupGalleryEffects()
  globalThis.__galleryStateValues = [false, true]
  globalThis.__galleryStateUpdates = []
  globalThis.__galleryRefValues = [openerNode, closeNode, dialogNode]
  tree = materialize(ThumbnailGallery({
    activeIndex: 0,
    onThumbnailClick(index) { selected.push(index) },
    privatePreviewControl: {
      workspace: 'mobile-private',
      state: 'some',
      onToggle() { toggles += 1 },
    },
  }))
  assert.equal(globalThis.__galleryPortalTargets.at(-1), modalDocument.body)
  assert.equal(modalDocument.body.contains(dialogNode), true)
  assert.equal(modalDocument.appRoot.contains(dialogNode), false)
  assert.equal(modalDocument.activeElement, closeNode)
  assert.equal(modalDocument.appRoot.hasAttribute('inert'), true)
  assert.equal(parentDialog.hasAttribute('inert'), true)
  assert.equal(dialogNode.hasAttribute('inert'), false)
  assert.equal(modalDocument.body.style.overflow, 'hidden')

  const action = findElements(tree, element => (
    element.type === 'button'
    && element.props?.['aria-label'] === 'Reveal all remaining private previews for project mobile-private'
  ))[0]
  assert.ok(action)
  assert.equal(action.props.type, 'button')
  assert.equal(action.props['aria-pressed'], 'mixed')
  assert.match(action.props.className, /min-h-11/)
  action.props.onClick()
  assert.equal(toggles, 1)

  const closer = findElements(tree, element => element.props?.['aria-label'] === 'Hide thumbnails')[0]
  assert.ok(closer)
  assert.equal(closer.props.type, 'button')
  assert.match(closer.props.className, /min-h-11 min-w-11/)

  const backdrop = findElements(tree, element => (
    element.type === 'button' && element.props?.['aria-label'] === 'Close thumbnail history'
  ))[0]
  assert.ok(backdrop)

  const upperDialog = new ModalTestElement(modalDocument, 'upper dialog')
  const upperClose = new ModalTestElement(modalDocument, 'upper close')
  upperDialog.focusable = [upperClose]
  upperDialog.descendants = new Set([upperClose])
  let upperCloseRequests = 0
  const cleanupUpper = installModalFocus({
    document: modalDocument,
    dialog: upperDialog,
    initialFocus: upperClose,
    restoreFocus: closeNode,
    appRoot: modalDocument.appRoot,
    onClose: () => { upperCloseRequests += 1 },
    priority: 100,
  })
  assert.equal(dialogNode.hasAttribute('inert'), true)
  globalThis.__galleryStateUpdates = []
  backdrop.props.onClick()
  closer.props.onClick()
  assert.deepEqual(globalThis.__galleryStateUpdates, [], 'covered history cannot dismiss')

  const thumbnail = findElements(tree, element => Number.isInteger(element.props?.['data-thumb-index']))[0]
  assert.ok(thumbnail)
  thumbnail.props.onClick()
  assert.deepEqual(selected, [], 'covered history cannot select')
  assert.equal(globalThis.__galleryRevealed.size, 0, 'covered history cannot reveal')
  assert.deepEqual(globalThis.__galleryStateUpdates, [], 'covered selection cannot mutate state')

  const escape = new Event('keydown', { cancelable: true })
  Object.defineProperty(escape, 'key', { value: 'Escape' })
  modalDocument.dispatchEvent(escape)
  assert.equal(upperCloseRequests, 1)
  assert.equal(parentCloseRequests, 0)
  cleanupUpper()
  assert.equal(modalDocument.activeElement, closeNode)
  assert.equal(dialogNode.hasAttribute('inert'), false)

  globalThis.__galleryStateUpdates = []
  thumbnail.props.onClick()
  assert.deepEqual(selected, [0])
  assert.deepEqual(globalThis.__galleryStateUpdates, [false, 1])
  cleanupGalleryEffects()
  assert.equal(modalDocument.activeElement, openerNode)
  assert.equal(parentDialog.hasAttribute('inert'), false)
  assert.equal(modalDocument.body.style.overflow, 'hidden')

  const parentEscape = new Event('keydown', { cancelable: true })
  Object.defineProperty(parentEscape, 'key', { value: 'Escape' })
  modalDocument.dispatchEvent(parentEscape)
  assert.equal(parentCloseRequests, 1)
  cleanupParent()
  assert.equal(modalDocument.activeElement, outerOpener)
  assert.equal(modalDocument.appRoot.hasAttribute('inert'), false)
  assert.equal(modalDocument.body.style.overflow, 'auto')
})

test('private and initial-blur reference images acquire a source only while revealed', async t => {
  const originalFetch = globalThis.fetch
  const backendCalls = []
  globalThis.fetch = (...args) => {
    backendCalls.push(args)
    throw new Error('Reference preview must not mutate backend state')
  }
  globalThis.__referenceEffectCleanups = []
  globalThis.__referenceRevealed = new Set()
  globalThis.__referenceSessionChanges = []
  t.after(() => {
    cleanupReferenceEffects()
    globalThis.fetch = originalFetch
    delete globalThis.__referenceEffectCleanups
    delete globalThis.__referenceRevealed
    delete globalThis.__referenceSessionChanges
  })

  const ProjectAssetPreview = await loadProjectAssetPreviewHarness()
  const cases = [
    {
      name: 'private',
      output: {
        id: 'private-image',
        relative_path: 'private/image.png',
        media_type: 'image/png',
        metadata: { private: true, initial_blur: false },
      },
    },
    {
      name: 'initial-blur',
      output: {
        id: 'initial-blur-image',
        relative_path: 'public/initial-blur.png',
        media_type: 'image/png',
        metadata: { private: false, initial_blur: true },
      },
    },
  ]

  for (const previewCase of cases) {
    globalThis.__referenceSessionChanges.length = 0
    const renderPreview = () => materialize(ProjectAssetPreview({
      project: 'reference-project',
      assetId: 'asset-1',
      output: previewCase.output,
      label: `${previewCase.name} preview`,
    }))
    const expectedIdentity = privatePreviewIdentity(
      'reference-project',
      `asset:asset-1:${previewCase.output.id}`,
      previewCase.output.relative_path,
    )
    const expectedSource = `/api/v1/projects/reference-project/assets/media/${previewCase.output.relative_path}`

    const blurred = renderPreview()
    const blurredImages = findElements(blurred, element => element.type === 'img')
    assert.equal(blurredImages.length, 1)
    assert.equal(blurredImages[0].props.src, undefined)
    const revealButton = findElements(blurred, element => (
      element.type === 'button' && element.props?.title?.includes('reveal for this browser session')
    ))[0]
    assert.ok(revealButton)
    revealButton.props.onClick()
    assert.deepEqual(globalThis.__referenceSessionChanges, [['reveal', expectedIdentity]])
    assert.deepEqual(backendCalls, [])
    cleanupReferenceEffects()

    const revealed = renderPreview()
    const revealedImages = findElements(revealed, element => element.type === 'img')
    assert.equal(revealedImages.length, 1)
    assert.equal(revealedImages[0].props.src, expectedSource)
    const blurButton = findElements(revealed, element => (
      element.type === 'button' && element.props?.title?.startsWith('Blur this')
    ))[0]
    assert.ok(blurButton)
    blurButton.props.onClick()
    assert.deepEqual(globalThis.__referenceSessionChanges, [
      ['reveal', expectedIdentity],
      ['hide', expectedIdentity],
    ])
    assert.deepEqual(backendCalls, [])
    cleanupReferenceEffects()

    const reblurred = renderPreview()
    const reblurredImages = findElements(reblurred, element => element.type === 'img')
    assert.equal(reblurredImages.length, 1)
    assert.equal(reblurredImages[0].props.src, undefined)
    assert.deepEqual(backendCalls, [])
    cleanupReferenceEffects()
  }
})

test('gallery privacy, revocation, and virtualization contracts are wired without durable mutation', async () => {
  const [main, item, thumbnails, references, store, privatePreview] = await Promise.all([
    readFile(mainContentUrl, 'utf8'),
    readFile(mediaFeedItemUrl, 'utf8'),
    readFile(thumbnailsUrl, 'utf8'),
    readFile(referencesUrl, 'utf8'),
    readFile(storeUrl, 'utf8'),
    readFile(privatePreviewUrl, 'utf8'),
  ])

  assert.match(main, /privatePreviewWorkspaceHasRevealed\(activeWorkspace, 'all'\)/)
  assert.match(main, /privatePreviewWorkspaceHasRevealed\(activeWorkspace\) \? 'some' : 'none'/)
  assert.match(main, /setPrivatePreviewsForWorkspaceRevealed\(/)
  assert.match(main, /\? 'Blur all'[\s\S]*'Reveal all remaining'[\s\S]*'Reveal all'/)
  assert.match(main, /aria-pressed=\{privatePreviewActionPressed\}/)
  assert.match(main, /min-h-11[^"]*md:min-h-0/)
  assert.doesNotMatch(main, /min-h-11[^"]*sm:min-h-0/)
  assert.match(main, /Browser-session preview only; project access unchanged\./)
  assert.match(main, /activeWorkspace && !browsingUploads/)
  assert.match(main, /Map<string, \{ height: number; epoch: number \}>/)
  assert.match(main, /measurement\?\.epoch === measurementEpoch/)
  assert.match(main, /if \(epoch !== measurementEpoch\) return/)
  assert.match(main, /currentOutputIdentities\.current\.has\(identity\)/)
  assert.match(main, /estimatedItemHeight, measurementVersion\]/)
  assert.match(main, /key=\{identity\}/)
  assert.match(main, /selectedOutputIdentity/)
  assert.match(main, /viewportAnchor/)
  assert.match(main, /intraItemOffset/)
  assert.match(main, /galleryScopeKey/)
  assert.match(main, /feedRef\.current\.scrollTop = scrollTop/)
  const containerObserver = main.slice(
    main.indexOf('// Measure container on mount and resize'),
    main.indexOf('const getItemHeight'),
  )
  assert.match(containerObserver, /lastMeasuredContainerWidth\.current/)
  assert.match(containerObserver, /\}, \[mainView\]\)/)
  assert.match(main, /scrollTop - OVERSCAN \* estimatedItemHeight/)
  assert.match(main, /scrollTop \+ containerHeight \+ OVERSCAN \* estimatedItemHeight/)
  assert.match(main, /scopeFence\.current\.generation !== targetAtStart\.scopeGeneration/)
  assert.match(main, /listFence\.current\.generation !== targetAtStart\.listGeneration/)
  assert.match(main, /requestAnimationFrame\(\(\) => requestAnimationFrame\(align\)\)/)
  const thumbnailClick = main.slice(
    main.indexOf('const handleThumbnailClick'),
    main.indexOf('// Infinite scroll:', main.indexOf('const handleThumbnailClick')),
  )
  assert.ok(thumbnailClick.indexOf('viewportAnchor.current = { identity, intraItemOffset: 0 }') >= 0)
  assert.ok(
    thumbnailClick.indexOf('viewportAnchor.current = { identity, intraItemOffset: 0 }')
      < thumbnailClick.indexOf('setSelectedOutput(index)'),
  )
  assert.ok(thumbnailClick.indexOf('setSelectedOutput(index)') < thumbnailClick.indexOf('feedEl.scrollTo'))

  const withheldFeedMedia = item.slice(
    item.indexOf('{privateBlurred ? ('),
    item.indexOf(") : file.type === 'video'"),
  )
  assert.notEqual(withheldFeedMedia, '')
  assert.doesNotMatch(withheldFeedMedia, /file\.url|<audio|<video|RetryImage/)
  assert.match(item, /<video[\s\S]*src=\{file\.url\}/)
  assert.match(item, /<audio[^>]*src=\{file\.url\}/)
  assert.match(item, /<RetryImage[^>]*url=\{file\.url\}/)
  assert.match(item, /if \(previous && previous !== video\) releaseVideoSource\(previous\)/)
  assert.match(item, /ref=\{setVideoElement\}/)
  assert.match(item, /video\.pause\(\)[\s\S]*video\.removeAttribute\('src'\)[\s\S]*video\.load\(\)/)
  assert.doesNotMatch(item, /video\.play\(/)
  assert.match(item, /focus-within:z-20/)
  assert.match(item, /event\.target !== event\.currentTarget/)
  assert.match(item, /event\.key !== 'Enter' && event\.key !== ' '/)
  assert.match(item, /aria-label=\{`Reveal blurred preview for \$\{file\.name\}`\}/)
  assert.match(item, /aria-label=\{`Blur preview for \$\{file\.name\}`\}/)
  assert.doesNotMatch(item, /Re-blur/)

  assert.match(thumbnails, /const privateBlurred = file\.private && !privateRevealed/)
  const withheldThumbnail = thumbnails.slice(
    thumbnails.indexOf('{privateBlurred ? ('),
    thumbnails.indexOf(") : file.type === 'video'"),
  )
  assert.notEqual(withheldThumbnail, '')
  assert.doesNotMatch(withheldThumbnail, /file\.url|<img|<VideoThumbnail|requestThumbnail/)
  assert.match(thumbnails, /\) : file\.type === 'video' \? \([\s\S]*<VideoThumbnail[\s\S]*src=\{file\.url\}/)
  assert.match(thumbnails, /<img src=\{file\.url\}/)
  assert.match(thumbnails, /requestThumbnail\(src, cacheKey, controller\.signal\)/)
  assert.match(thumbnails, /controller\.abort\(\)/)
  assert.doesNotMatch(thumbnails, /autoPlay|video\.play\(/)
  assert.match(thumbnails, /key=\{privateIdentity\}/)
  assert.match(thumbnails, /Reveal blurred preview and select/)
  assert.match(thumbnails, /aria-controls="mobile-thumbnail-panel"/)
  assert.match(thumbnails, /createPortal\([\s\S]*document\.body/)
  assert.match(thumbnails, /inert=\{!mobileOpen\}/)
  assert.match(thumbnails, /installModalFocus\(\{/)
  assert.match(thumbnails, /closeModalIfTop\(document, mobileDialogRef\.current/)
  assert.match(thumbnails, /appRoot: document\.getElementById\('root'\)/)
  assert.match(thumbnails, /priority: 70/)
  assert.match(thumbnails, /role="dialog"/)
  assert.match(thumbnails, /aria-modal=\{mobileOpen \? true : undefined\}/)
  assert.doesNotMatch(thumbnails, /addEventListener\('keydown'|requestAnimationFrame\(\(\) => mobileOpenerRef/)
  const thumbnailActivation = thumbnails.slice(
    thumbnails.indexOf('onClick={() => {', thumbnails.indexOf('data-thumb-index={idx}')),
    thumbnails.indexOf('className={`absolute', thumbnails.indexOf('data-thumb-index={idx}')),
  )
  assert.ok(thumbnailActivation.indexOf('if (onMobileClick && !onMobileClick()) return') >= 0)
  assert.ok(
    thumbnailActivation.indexOf('if (onMobileClick && !onMobileClick()) return')
      < thumbnailActivation.indexOf('revealPrivatePreview(privateIdentity)'),
  )
  assert.ok(
    thumbnailActivation.indexOf('if (onMobileClick && !onMobileClick()) return')
      < thumbnailActivation.indexOf('onThumbnailClick(idx)'),
  )
  assert.match(thumbnails, /min-h-11 min-w-11/)
  assert.match(thumbnails, /Reveal all remaining/)
  assert.match(thumbnails, /Browser-session preview only; project access unchanged\./)
  assert.doesNotMatch(thumbnails, /Re-blur/)
  const projectAssetPreview = references.slice(
    references.indexOf('function ProjectAssetPreview'),
    references.indexOf('export function ProjectReferenceLibrary'),
  )
  assert.match(projectAssetPreview, /const needsInitialBlur = projectAssetOutputNeedsInitialBlur\(output\)/)
  assert.equal(
    projectAssetPreview.match(/src=\{privateBlurred \? undefined : getProjectAssetMediaUrl/g)?.length,
    2,
  )
  assert.match(projectAssetPreview, /: <img[\s\S]*src=\{privateBlurred \? undefined : getProjectAssetMediaUrl/)
  assert.match(projectAssetPreview, /subscribePrivatePreviewReveal\(identity, syncReveal\)/)
  assert.match(projectAssetPreview, /video\.pause\(\)[\s\S]*video\.removeAttribute\('src'\)[\s\S]*video\.load\(\)/)
  assert.doesNotMatch(projectAssetPreview, /video\.play\(|fetch\(|setProjectAssetVariantStatus/)
  assert.doesNotMatch(main, /setPrivatePreviewsForWorkspaceRevealed[\s\S]{0,200}bulkSetSelectedPrivacy/)
  assert.doesNotMatch(main, /fetch\([^)]*private-preview/)
  assert.doesNotMatch(main, /setPrivatePreviewsForWorkspaceRevealed[\s\S]{0,200}(privateOutput|explicitOutput|accessContext)\s*:/)
  assert.doesNotMatch(privatePreview, /fetch\(|localStorage|\.\.\/api/)

  const switchScope = store.slice(store.indexOf('switchWorkspace: async'), store.indexOf('createWorkspace: async'))
  assert.match(switchScope, /hidePrivatePreviewsForWorkspace\(activeWorkspace\)/)
  assert.match(switchScope, /hidePrivatePreviewsForWorkspace\(previousWorkspace\)/)
  for (const symbol of ['createWorkspace: async', 'unlockWorkspace: async', 'lockWorkspace: async', 'lockAllWorkspaces: async', 'deleteWorkspace: async']) {
    const start = store.indexOf(symbol)
    assert.notEqual(start, -1)
    assert.match(store.slice(start, start + 3500), /hidePrivatePreviewsForWorkspace/)
  }
  const loadWorkspaceScope = store.slice(store.indexOf('loadWorkspaces: async'), store.indexOf('switchWorkspace: async'))
  assert.match(loadWorkspaceScope, /revokedWorkspaces/)
  assert.match(loadWorkspaceScope, /hidePrivatePreviewsForWorkspace\(workspace\)/)
})

test('gallery uses compact composable media and artifact facets', async () => {
  const [filters, main, store, client] = await Promise.all([
    readFile(tabFilterUrl, 'utf8'),
    readFile(mainContentUrl, 'utf8'),
    readFile(storeUrl, 'utf8'),
    readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8'),
  ])

  assert.match(filters, /aria-controls="gallery-filter-popover"/)
  assert.match(filters, /aria-labelledby="gallery-filter-title"/)
  assert.match(filters, /Media, artifact, and metadata selections all combine\./)
  for (const label of ['All', 'Images', 'Videos', 'Audio', 'Finals', 'Components', 'Windows', 'Temporary']) {
    assert.match(filters, new RegExp(`label: '${label}'`))
  }
  assert.doesNotMatch(filters, /ResizeObserver|overflow-x-auto/)
  assert.match(filters, /resetGalleryFilters\(\)/)
  assert.match(store, /resetGalleryFilters: \(\) =>/)
  assert.match(store, /mediaFilter: 'all'/)
  assert.match(store, /outputArtifactScope: 'final'/)
  assert.match(store, /if \(f === get\(\)\.mediaFilter\) return/)
  assert.match(client, /params\.set\('artifact_scope'/)
  assert.match(client, /params\.set\('media_type'/)

  const scopeKey = main.slice(main.indexOf('const galleryScopeKey'), main.indexOf('const outputIdentities'))
  assert.match(scopeKey, /mediaFilter/)
  assert.match(scopeKey, /outputArtifactScope/)
  assert.match(scopeKey, /outputSearchQuery/)
})

test('active Gallery media and quick-view choices are behavioral no-ops', async t => {
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const TabFilter = await loadTabFilterHarness()
  t.after(() => {
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    delete globalThis.__tabFilterHooks
    delete globalThis.__tabFilterStore
  })

  const activeMedia = createTabFilterRuntime({ 1: true })
  globalThis.window = activeMedia.window
  globalThis.document = activeMedia.document
  globalThis.__tabFilterHooks = activeMedia.hooks
  globalThis.__tabFilterStore = activeMedia.store
  activeMedia.hooks.begin()
  const mediaTree = materialize(TabFilter())
  const allMedia = findElements(mediaTree, element => (
    element.type === 'button' && element.props?.['data-gallery-filter-initial'] === ''
  ))[0]
  assert.ok(allMedia)
  allMedia.props.onClick()
  assert.deepEqual(activeMedia.calls.media, [])
  assert.deepEqual(activeMedia.store.selectedOutputKeys, ['default\u0000kept.png'])

  const activeQuickView = createTabFilterRuntime(
    { 1: true },
    { mediaFilter: 'favorites' },
  )
  globalThis.window = activeQuickView.window
  globalThis.document = activeQuickView.document
  globalThis.__tabFilterHooks = activeQuickView.hooks
  globalThis.__tabFilterStore = activeQuickView.store
  activeQuickView.hooks.begin()
  const quickViewTree = materialize(TabFilter())
  const favorites = findElements(quickViewTree, element => (
    element.type === 'button'
    && element.props?.['aria-pressed'] === true
    && String(element.props?.children).includes('Favorites')
  ))[0]
  assert.ok(favorites)
  favorites.props.onClick()
  assert.deepEqual(activeQuickView.calls.media, [])
  assert.deepEqual(activeQuickView.store.selectedOutputKeys, ['default\u0000kept.png'])
})

test('Gallery search X clears only free text and preserves structured filters', async t => {
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const TabFilter = await loadTabFilterHarness()
  const runtime = createTabFilterRuntime(
    { 0: true },
    { outputSearchQuery: 'portrait model:"h3" reference:"with"' },
  )
  globalThis.window = runtime.window
  globalThis.document = runtime.document
  globalThis.__tabFilterHooks = runtime.hooks
  globalThis.__tabFilterStore = runtime.store
  t.after(() => {
    runtime.cleanup()
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    delete globalThis.__tabFilterHooks
    delete globalThis.__tabFilterStore
  })

  runtime.hooks.begin()
  const tree = materialize(TabFilter())
  const clearSearch = findElements(tree, element => (
    element.type === 'button'
    && element.props?.['aria-label'] === 'Clear search text and close search'
  ))[0]
  assert.ok(clearSearch)
  clearSearch.props.onClick()
  assert.equal(runtime.calls.search.at(-1), 'model:"h3" reference:"with"')
  assert.equal(runtime.states[0], false)
  assert.deepEqual(runtime.states[3], { model: 'h3', reference: 'with' })
})

test('Gallery filter popover transfers focus and dismisses on Escape or outside pointer', async t => {
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const TabFilter = await loadTabFilterHarness()
  t.after(() => {
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    delete globalThis.__tabFilterHooks
    delete globalThis.__tabFilterStore
  })

  const escapeRuntime = createTabFilterRuntime()
  globalThis.window = escapeRuntime.window
  globalThis.document = escapeRuntime.document
  globalThis.__tabFilterHooks = escapeRuntime.hooks
  globalThis.__tabFilterStore = escapeRuntime.store
  escapeRuntime.hooks.begin()
  let tree = materialize(TabFilter())
  let trigger = findElements(tree, element => element.props?.['aria-controls'] === 'gallery-filter-popover')[0]
  const triggerNode = { focusCount: 0, focus() { this.focusCount += 1 }, contains() { return false } }
  trigger.props.ref.current = triggerNode
  trigger.props.onClick()
  assert.equal(escapeRuntime.states[1], true)

  escapeRuntime.hooks.begin()
  tree = materialize(TabFilter())
  trigger = findElements(tree, element => element.props?.['aria-controls'] === 'gallery-filter-popover')[0]
  trigger.props.ref.current = triggerNode
  const dialog = findElements(tree, element => element.props?.id === 'gallery-filter-popover')[0]
  const initialControl = { focusCount: 0, focus() { this.focusCount += 1 } }
  dialog.props.ref.current = {
    contains() { return false },
    querySelector() { return initialControl },
  }
  escapeRuntime.flushEffects()
  assert.equal(initialControl.focusCount, 1)
  let prevented = 0
  let propagationStopped = 0
  escapeRuntime.document.dispatch('keydown', {
    key: 'Escape',
    preventDefault() { prevented += 1 },
    stopPropagation() { propagationStopped += 1 },
  })
  assert.equal(escapeRuntime.states[1], false)
  assert.equal(prevented, 1)
  assert.equal(propagationStopped, 1)
  assert.equal(triggerNode.focusCount, 1)
  escapeRuntime.cleanup()

  const outsideRuntime = createTabFilterRuntime({ 1: true })
  globalThis.window = outsideRuntime.window
  globalThis.document = outsideRuntime.document
  globalThis.__tabFilterHooks = outsideRuntime.hooks
  globalThis.__tabFilterStore = outsideRuntime.store
  outsideRuntime.hooks.begin()
  tree = materialize(TabFilter())
  trigger = findElements(tree, element => element.props?.['aria-controls'] === 'gallery-filter-popover')[0]
  const outsideTrigger = { focusCount: 0, focus() { this.focusCount += 1 }, contains() { return false } }
  trigger.props.ref.current = outsideTrigger
  const outsideDialog = findElements(tree, element => element.props?.id === 'gallery-filter-popover')[0]
  outsideDialog.props.ref.current = {
    contains() { return false },
    querySelector() { return { focus() {} } },
  }
  outsideRuntime.flushEffects()
  outsideRuntime.document.dispatch('pointerdown', { target: {} })
  assert.equal(outsideRuntime.states[1], false)
  assert.equal(outsideTrigger.focusCount, 0)
  outsideRuntime.cleanup()
})

test('last thumbnail consumer abort releases an active private-video decode', async t => {
  const originalDocument = globalThis.document
  const videos = []
  globalThis.document = fakeThumbnailDocument(videos)
  t.after(() => { globalThis.document = originalDocument })

  const controller = new AbortController()
  const result = requestThumbnail('/private-active.mp4', 'abort-active', controller.signal)
  await waitFor(() => videos.length === 1)
  assert.equal(videos[0].source, '/private-active.mp4')

  controller.abort()
  assert.equal(await result, null)
  assert.equal(videos[0].source, '')
  assert.equal(videos[0].paused, 1)
  assert.equal(videos[0].removed, 1)
  assert.equal(videos[0].loads, 1)
})

test('thumbnail cancellation is consumer-aware and drops queued work', async t => {
  const originalDocument = globalThis.document
  const videos = []
  globalThis.document = fakeThumbnailDocument(videos)
  t.after(() => { globalThis.document = originalDocument })

  const firstController = new AbortController()
  const sharedController = new AbortController()
  const queuedController = new AbortController()
  const first = requestThumbnail('/shared.mp4', 'shared-active', firstController.signal)
  const shared = requestThumbnail('/shared.mp4', 'shared-active', sharedController.signal)
  const queued = requestThumbnail('/queued.mp4', 'queued-cancelled', queuedController.signal)
  queuedController.abort()

  await waitFor(() => videos.length === 1)
  firstController.abort()
  assert.equal(await first, null)
  assert.equal(videos[0].source, '/shared.mp4')

  videos[0].onloadeddata()
  assert.equal(await shared, 'data:image/webp;base64,thumbnail')
  assert.equal(await queued, null)
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(videos.length, 1)
  assert.equal(videos[0].source, '')
})
