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
        return [typeof initial === 'function' ? initial() : initial, () => {}]
      }
      export function useEffect(effect) {
        const cleanup = effect()
        if (typeof cleanup === 'function') globalThis.__galleryEffectCleanups.push(cleanup)
      }
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
    ['../../lib/useIsMobile', 'export function useIsMobile() { return false }'],
    ['../../lib/privatePreview', `
      export function privatePreviewIdentity(workspace, name, revision = '') {
        return workspace + '\\u0000' + name + '\\u0000' + revision
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
  assert.equal(privatePreviewWasRevealed(alphaOne), false)

  revealPrivatePreview(alphaOne)
  assert.equal(privatePreviewWorkspaceHasRevealed('alpha'), true)
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
  assert.deepEqual(itemChanges, [['one', false]])

  changes.length = 0
  itemChanges.length = 0
  revealPrivatePreview(alphaOne)
  assert.equal(privatePreviewWasRevealed(alphaOne), true)
  assert.deepEqual(itemChanges, [['one', true]])

  changes.length = 0
  itemChanges.length = 0
  hidePrivatePreviewsForWorkspace('alpha')
  assert.equal(privatePreviewWorkspaceHasRevealed('alpha'), false)
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
  assert.equal(privatePreviewWasRevealed(first), true)
  assert.equal(privatePreviewWasRevealed(later), true)
  hidePrivatePreview(first)
  assert.equal(privatePreviewWasRevealed(first), false)
  assert.equal(privatePreviewWasRevealed(later), true)
  revealPrivatePreview(first)
  assert.equal(privatePreviewWasRevealed(first), true)
  hidePrivatePreviewsForWorkspace(workspace)
  assert.equal(privatePreviewWasRevealed(first), false)
  assert.equal(privatePreviewWasRevealed(later), false)
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

  assert.match(main, /privatePreviewWorkspaceHasRevealed\(activeWorkspace\)/)
  assert.match(main, /setPrivatePreviewsForWorkspaceRevealed\(/)
  assert.match(main, /\? 'Blur all' : 'Reveal all'/)
  assert.match(main, /aria-pressed=\{anyProjectPrivatePreviewRevealed\}/)
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

  assert.match(item, /src=\{privateBlurred \? undefined : file\.url\}/)
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
