import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import test from 'node:test'

import { build } from 'esbuild'
import { compile } from 'tailwindcss'

import { closeModalIfTop, installModalFocus } from '../src/lib/modalFocus.ts'

const directorUrl = new URL('../src/components/DirectorDashboard/DirectorDashboard.tsx', import.meta.url)
const directorPanelUrl = new URL('../src/components/Sidebar/DirectorPanel.tsx', import.meta.url)
const loraUrl = new URL('../src/components/LoraBrowser/LoraBrowser.tsx', import.meta.url)
const modelCardUrl = new URL('../src/components/LoraBrowser/ModelCard.tsx', import.meta.url)

class FakeDocument extends EventTarget {
  activeElement = null
  body = { style: { overflow: 'auto' } }
  appRoot = null

  getElementById(id) { return id === 'root' ? this.appRoot : null }
}

class FakeElement {
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

function modalFixture(document, name) {
  const dialog = new FakeElement(document, `${name} dialog`)
  const first = new FakeElement(document, `${name} first`)
  const last = new FakeElement(document, `${name} last`)
  const trigger = new FakeElement(document, `${name} trigger`)
  dialog.descendants = new Set([first, last, trigger])
  dialog.focusable = [first, last]
  return { dialog, first, last, trigger }
}

function dispatchKey(document, key, shiftKey = false) {
  const event = new Event('keydown', { cancelable: true })
  Object.defineProperties(event, {
    key: { value: key },
    shiftKey: { value: shiftKey },
  })
  document.dispatchEvent(event)
  return event
}

function treeChildren(node) {
  if (!node || typeof node !== 'object') return []
  const children = node.props?.children
  return Array.isArray(children) ? children : children == null ? [] : [children]
}

function findNodes(node, predicate, matches = []) {
  if (node && typeof node === 'object' && predicate(node)) matches.push(node)
  for (const child of treeChildren(node)) findNodes(child, predicate, matches)
  return matches
}

function createHarnessModules() {
  return new Map([
    ['react', `
      export class Component {}
      export function useState(initial) {
        const index = globalThis.__fullScreenHookIndex++
        if (!(index in globalThis.__fullScreenHookState)) {
          globalThis.__fullScreenHookState[index] = typeof initial === 'function' ? initial() : initial
        }
        return [globalThis.__fullScreenHookState[index], value => {
          const current = globalThis.__fullScreenHookState[index]
          globalThis.__fullScreenHookState[index] = typeof value === 'function' ? value(current) : value
        }]
      }
      export function useEffect(effect) {
        const cleanup = effect()
        if (typeof cleanup === 'function') globalThis.__fullScreenCleanups.push(cleanup)
      }
      export function useId() { return 'full-screen-' + globalThis.__fullScreenIdIndex++ }
      export function useCallback(callback) { return callback }
      export function useRef(initial) {
        const index = globalThis.__fullScreenRefIndex++
        if (!globalThis.__fullScreenRefs[index]) globalThis.__fullScreenRefs[index] = { current: initial }
        return globalThis.__fullScreenRefs[index]
      }
    `],
    ['react-dom', `
      export function createPortal(children, target) {
        return { ...children, portalTarget: target }
      }
    `],
    ['react/jsx-runtime', `
      export const Fragment = Symbol('Fragment')
      export function jsx(type, props, key) { return { type, props: props || {}, key } }
      export const jsxs = jsx
    `],
    ['lucide-react', `
      const icon = props => ({ type: 'svg', props: props || {} })
      export const AlertTriangle = icon, ArrowUpCircle = icon, BookOpen = icon
      export const Boxes = icon, Brain = icon, Camera = icon, Check = icon
      export const Clock = icon, Combine = icon, ExternalLink = icon, Film = icon
      export const HardDrive = icon, ImageIcon = icon, KeyRound = icon, Link2 = icon
      export const Loader2 = icon, Pencil = icon, Play = icon, RefreshCw = icon
      export const Search = icon, Sparkles = icon, Tag = icon, Trash2 = icon, X = icon
    `],
    ['store', `
      export function useStore(selector) { return selector(globalThis.__fullScreenStore) }
      useStore.getState = () => globalThis.__fullScreenStore
    `],
    ['focus', `
      export function installModalFocus(options) {
        globalThis.__fullScreenInstalls.push(options)
        return () => { globalThis.__fullScreenCleanupCount += 1 }
      }
      export function closeModalIfTop(document, dialog, onClose) {
        globalThis.__fullScreenCloseRequests.push(dialog)
        if (dialog !== globalThis.__fullScreenTopDialog) return false
        onClose()
        return true
      }
    `],
    ['api', `
      export function getFileUrl(path) { return '/files/' + path }
      export async function fetchCivitAIModelFilters() { return { filters: [] } }
      export async function startLoraScan() { return { scan_id: 'scan', total: 0 } }
      export async function fetchLoraScanStatus() { return { status: 'done', message: 'done', current: 0, total: 0, results: [] } }
      export async function fetchInstalledLoras() { return { loras: [] } }
      export async function importHuggingFaceLora() { return { filename: 'x', target_dir: 'x', base_model: '' } }
      export async function checkLoraUpdates() {}
      export async function deleteLoraFile() {}
    `],
    ['format', 'export function formatBytes(value) { return String(value) }'],
    ['model-card', 'export function ModelCard(props) { return { type: "model-card", props } }'],
    ['model-detail', 'export function ModelDetail(props) { return { type: "model-detail", props } }'],
    ['download-bar', 'export function DownloadBar() { return { type: "download-bar", props: {} } }'],
    ['installed-checkpoints', 'export function InstalledCheckpoints(props) { return { type: "installed-checkpoints", props } }'],
  ])
}

async function loadFullScreenComponent(entryUrl, exportName, exposeInner = false) {
  const modules = createHarnessModules()
  const result = await build({
    absWorkingDir: new URL('../', import.meta.url).pathname,
    entryPoints: [entryUrl.pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'full-screen-overlay-harness',
      setup(bundle) {
        bundle.onResolve({ filter: /.*/ }, args => {
          if (modules.has(args.path)) return { path: args.path, namespace: 'full-screen-test' }
          if (args.path === 'react' || args.path === 'react-dom' || args.path === 'react/jsx-runtime' || args.path === 'lucide-react') {
            return { path: args.path, namespace: 'full-screen-test' }
          }
          if (args.path.includes('stores/useStore')) return { path: 'store', namespace: 'full-screen-test' }
          if (args.path.includes('lib/modalFocus')) return { path: 'focus', namespace: 'full-screen-test' }
          if (args.path.includes('api/client')) return { path: 'api', namespace: 'full-screen-test' }
          if (args.path.includes('lib/format')) return { path: 'format', namespace: 'full-screen-test' }
          if (args.path.endsWith('/ModelCard')) return { path: 'model-card', namespace: 'full-screen-test' }
          if (args.path.endsWith('/ModelDetail')) return { path: 'model-detail', namespace: 'full-screen-test' }
          if (args.path.endsWith('/DownloadBar')) return { path: 'download-bar', namespace: 'full-screen-test' }
          if (args.path.endsWith('/InstalledCheckpoints')) return { path: 'installed-checkpoints', namespace: 'full-screen-test' }
          return null
        })
        bundle.onLoad({ filter: /.*/, namespace: 'full-screen-test' }, args => ({
          contents: modules.get(args.path),
          loader: 'js',
        }))
        if (exposeInner) {
          bundle.onLoad({ filter: /DirectorDashboard\.tsx$/ }, async args => ({
            contents: `${await readFile(args.path, 'utf8')}\nexport { DirectorDashboardInner }\n`,
            loader: 'tsx',
          }))
        }
      },
    }],
  })
  const compiled = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(
    createRequire(import.meta.url),
    compiled,
    compiled.exports,
  )
  return compiled.exports[exportName]
}

function resetRenderedHarness(document, refs, store, activeElement) {
  globalThis.document = document
  globalThis.HTMLElement = FakeElement
  globalThis.__fullScreenHookIndex = 0
  globalThis.__fullScreenHookState = []
  globalThis.__fullScreenRefIndex = 0
  globalThis.__fullScreenRefs = refs
  globalThis.__fullScreenIdIndex = 0
  globalThis.__fullScreenCleanups = []
  globalThis.__fullScreenCleanupCount = 0
  globalThis.__fullScreenInstalls = []
  globalThis.__fullScreenCloseRequests = []
  globalThis.__fullScreenTopDialog = null
  globalThis.__fullScreenStore = store
  document.activeElement = activeElement
}

test('Director and Model Browser form a distinct priority stack with top-only dismissal and focus restoration', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const outerOpener = new FakeElement(document, 'Director opener')
  const director = modalFixture(document, 'Director')
  const browser = modalFixture(document, 'Model Browser')
  director.dialog.descendants.add(browser.trigger)
  document.appRoot = appRoot
  let directorCloses = 0
  let browserCloses = 0

  outerOpener.focus()
  const cleanupDirector = installModalFocus({
    document, dialog: director.dialog, initialFocus: director.first,
    restoreFocus: outerOpener, appRoot, onClose: () => { directorCloses += 1 }, priority: 90,
  })
  browser.trigger.focus()
  const cleanupBrowser = installModalFocus({
    document, dialog: browser.dialog, initialFocus: browser.first,
    restoreFocus: browser.trigger, appRoot, onClose: () => { browserCloses += 1 }, priority: 95,
  })

  assert.equal(document.activeElement, browser.first)
  assert.equal(director.dialog.hasAttribute('inert'), true)
  assert.equal(appRoot.hasAttribute('inert'), true)
  assert.equal(document.body.style.overflow, 'hidden')
  assert.equal(closeModalIfTop(document, director.dialog, () => { directorCloses += 1 }), false)
  assert.equal(closeModalIfTop(document, browser.dialog, () => { browserCloses += 1 }), true)
  assert.equal(directorCloses, 0)
  assert.equal(browserCloses, 1)
  browser.last.focus()
  assert.equal(dispatchKey(document, 'Tab').defaultPrevented, true)
  assert.equal(document.activeElement, browser.first)
  assert.equal(dispatchKey(document, 'Escape').defaultPrevented, true)
  assert.equal(browserCloses, 2)

  cleanupBrowser()
  assert.equal(document.activeElement, browser.trigger)
  assert.equal(director.dialog.hasAttribute('inert'), false)
  assert.equal(dispatchKey(document, 'Escape').defaultPrevented, true)
  assert.equal(directorCloses, 1)
  cleanupDirector()
  assert.equal(document.activeElement, outerOpener)
  assert.equal(appRoot.hasAttribute('inert'), false)
  assert.equal(document.body.style.overflow, 'auto')
})

test('rendered full-screen surfaces portal to body and route backdrop and X through the modal stack', async () => {
  const DirectorDashboardInner = await loadFullScreenComponent(directorUrl, 'DirectorDashboardInner', true)
  const directorDocument = new FakeDocument()
  const directorApp = new FakeElement(directorDocument, 'app root')
  const directorOpener = new FakeElement(directorDocument, 'Director opener')
  const directorAutoLoadRef = { current: null }
  const directorDialog = new FakeElement(directorDocument, 'Director dialog')
  const directorClose = new FakeElement(directorDocument, 'Director close')
  directorDocument.appRoot = directorApp
  let directorCloseCalls = 0
  resetRenderedHarness(directorDocument, [directorAutoLoadRef, { current: directorDialog }, { current: directorClose }], {
    setDashboardOpen(open) { if (!open) directorCloseCalls += 1 },
    dashboardPipelineList: [],
    dashboardPipelineListRead: { workspace: 'default', generation: 1, status: 'ready' },
    dashboardSelectedPipeline: null, dashboardLoading: false,
    loadSavedPipeline() {}, tagClip() {}, async startPipelineRepair() {}, async cancelPipelineRepair() {},
    async rerunClipImage() {}, async rerunClipVideo() {}, async rejoinPipelineClips() {},
    async resumePipeline() {}, async deletePipeline() {},
  }, directorOpener)
  const directorTree = DirectorDashboardInner()
  const directorInstall = globalThis.__fullScreenInstalls.at(-1)
  assert.equal(directorTree.portalTarget, directorDocument.body)
  assert.equal(directorInstall.dialog, directorDialog)
  assert.equal(directorInstall.initialFocus, directorClose)
  assert.equal(directorInstall.restoreFocus, directorOpener)
  assert.equal(directorInstall.appRoot, directorApp)
  assert.equal(directorInstall.priority, 90)
  const directorDialogNode = findNodes(directorTree, node => node.props?.role === 'dialog')[0]
  assert.equal(directorDialogNode.props['aria-modal'], 'true')
  const directorPipelineSelector = findNodes(directorTree, node => node.type === 'select')[0]
  assert.equal(directorPipelineSelector.props['aria-label'], 'Select Director pipeline')
  const directorControls = findNodes(directorTree, node => node.props?.['aria-label'] === 'Close Director dashboard')
  assert.equal(directorControls.length, 2)
  globalThis.__fullScreenTopDialog = null
  for (const control of directorControls) control.props.onClick()
  assert.equal(directorCloseCalls, 0)
  globalThis.__fullScreenTopDialog = directorDialog
  directorControls[1].props.onClick()
  assert.equal(directorCloseCalls, 1)

  const LoraBrowser = await loadFullScreenComponent(loraUrl, 'LoraBrowser')
  const browserDocument = new FakeDocument()
  const browserApp = new FakeElement(browserDocument, 'app root')
  const browserOpener = new FakeElement(browserDocument, 'Model Browser opener')
  const browserDialog = new FakeElement(browserDocument, 'Model Browser dialog')
  const browserClose = new FakeElement(browserDocument, 'Model Browser close')
  browserDocument.appRoot = browserApp
  let browserCloseCalls = 0
  resetRenderedHarness(browserDocument, [
    { current: browserDialog }, { current: browserClose }, { current: null }, { current: undefined },
  ], {
    loraBrowserOpen: true,
    setLoraBrowserOpen(open) { if (!open) browserCloseCalls += 1 },
    civitSearchResults: [{ id: 1 }], civitSearchCursor: null, civitSearchLoading: false,
    civitSelectedModel: null, civitSearchError: null, servicesConfig: { civitai_api_key_set: false },
    searchCivitAI() {}, selectCivitAIModel() {}, clearCivitSelection() {},
    setLoraBrowserDefaultDir() {}, pollCivitAIDownloads() {}, setSettingsOpen() {}, setSettingsTab() {},
  }, browserOpener)
  const browserTree = LoraBrowser()
  const browserInstall = globalThis.__fullScreenInstalls.at(-1)
  assert.equal(browserTree.portalTarget, browserDocument.body)
  assert.equal(browserInstall.dialog, browserDialog)
  assert.equal(browserInstall.initialFocus, browserClose)
  assert.equal(browserInstall.restoreFocus, browserOpener)
  assert.equal(browserInstall.appRoot, browserApp)
  assert.equal(browserInstall.priority, 95)
  const browserDialogNode = findNodes(browserTree, node => node.props?.role === 'dialog')[0]
  assert.equal(browserDialogNode.props['aria-modal'], 'true')
  const browserChrome = findNodes(browserTree, node => /max-h-\[55%\]/.test(node.props?.className || ''))[0]
  assert.ok(browserChrome, 'all non-content Model Browser chrome is inside one bounded scroller')
  assert.equal(findNodes(browserChrome, node => node.type === 'a' && node.props?.href === 'https://civitai.com/user/account').length, 1)
  assert.ok(findNodes(browserChrome, node => node.type === 'select').length >= 3)
  const browserMain = findNodes(browserTree, node => /min-h-11 flex-1 overflow-hidden/.test(node.props?.className || ''))[0]
  assert.ok(browserMain, 'active downloads cannot collapse the model grid below one touch-target row')
  const browserControls = findNodes(browserTree, node => node.props?.['aria-label'] === 'Close model browser')
  assert.equal(browserControls.length, 2)
  globalThis.__fullScreenTopDialog = directorDialog
  for (const control of browserControls) control.props.onClick()
  assert.equal(browserCloseCalls, 0)
  globalThis.__fullScreenTopDialog = browserDialog
  browserControls[0].props.onClick()
  assert.equal(browserCloseCalls, 1)
})

test('full-screen source and compiled utilities cover safe-area, short-height, 200% zoom, and 767/768 contracts', async () => {
  const [director, browser, modelCard] = await Promise.all([
    readFile(directorUrl, 'utf8'),
    readFile(loraUrl, 'utf8'),
    readFile(modelCardUrl, 'utf8'),
  ])
  for (const source of [director, browser]) {
    assert.match(source, /createPortal\(/)
    assert.match(source, /document\.body/)
    assert.match(source, /role="dialog"/)
    assert.match(source, /aria-modal="true"/)
    assert.match(source, /aria-labelledby=/)
    assert.match(source, /installModalFocus\(\{/)
    assert.match(source, /closeModalIfTop\(/)
    assert.match(source, /h-\[100vh\][^"\n]*supports-\[height:100dvh\]:h-\[100dvh\]/)
    assert.match(source, /max-h-\[55%\][^"\n]*overflow-y-auto/)
    assert.match(source, /min-h-(?:0|11) flex-1/)
    assert.match(source, /\[&_a\]:min-h-11/)
    assert.match(source, /\[&_a\]:min-w-11/)
    assert.match(source, /\[&_button\]:min-h-11/)
    assert.match(source, /\[&_button\]:min-w-11/)
    assert.match(source, /motion-reduce:\[&_<\*>\]:transition-none|motion-reduce:\[&_\*\]:transition-none/)
    for (const edge of ['top', 'right', 'bottom', 'left']) {
      assert.match(source, new RegExp(`safe-area-inset-${edge}`))
    }
    assert.doesNotMatch(source, /document\.addEventListener\('keydown'/)
  }
  assert.match(director, /priority: 90/)
  assert.match(browser, /priority: 95/)
  assert.doesNotMatch(browser, /autoFocus/)
  assert.doesNotMatch(director, /md:\[&_(?:a|button|input|select|textarea)\]:min-[hw]-0/)
  assert.doesNotMatch(browser, /md:\[&_(?:a|button|input|select|textarea)\]:min-[hw]-0/)
  assert.match(browser, /min-h-11 flex-1 overflow-hidden/)
  assert.match(browser, /Hugging Face repository or CivitAI model URL/)
  assert.match(browser, /Downloading \$\{result\.filename\}\. Follow progress in the download bar\./)
  assert.doesNotMatch(browser, /Downloading \$\{result\.filename\} →/)
  assert.match(browser, />Redo all</)
  assert.doesNotMatch(browser, />Regen</)
  assert.doesNotMatch(browser, /Paste a HuggingFace model URL/)
  assert.match(browser, /lora\.hf_repo_id \? 'Hugging Face' : 'Local only'/)
  for (const label of ['LoRA', 'Textual Inversion', 'Aesthetic Gradient', 'ControlNet', 'Motion Module']) {
    assert.match(modelCard, new RegExp(`['"]${label}['"]`))
  }
  assert.match(modelCard, /formatModelType\(model\.type\)/)
  assert.match(modelCard, /aria-label=\{`View \$\{model\.name\} details`\}/)
  assert.doesNotMatch(modelCard, />\{model\.type\}</)

  const utilities = await compile('@tailwind utilities;')
  const compiled = utilities.build([
    'h-[100vh]',
    'supports-[height:100dvh]:h-[100dvh]',
    'max-h-[55%]',
    'min-h-11',
    'min-w-11',
    '[&_button]:min-h-11',
    '[&_button]:min-w-11',
  ])
  assert.match(compiled, /height: 100vh/)
  assert.match(compiled, /@supports \(height:\s*100dvh\)/)
  assert.match(compiled, /height: 100dvh/)
  assert.match(compiled, /max-height: 55%/)

  const touchTargetFloor = [director, browser].every(source => (
    source.includes('[&_button]:min-h-11')
    && source.includes('[&_button]:min-w-11')
    && source.includes('[&_a]:min-h-11')
  )) ? 44 : 0
  const viewports = [
    [320, 568], [390, 844], [568, 320], [767, 430], [768, 432],
  ]
  for (const [width, height] of viewports) {
    const landscape = width > height
    const safe = landscape
      ? { top: 0, right: 24, bottom: 21, left: 24 }
      : { top: 47, right: 0, bottom: 34, left: 0 }
    const usableWidth = width - Math.max(8, safe.left) - Math.max(8, safe.right)
    const usableHeight = height - Math.max(8, safe.top) - Math.max(8, safe.bottom)
    const chromeCap = usableHeight * 0.55
    const activeDownloadBar = 44
    const scrollingContentFloor = usableHeight - chromeCap - activeDownloadBar
    assert.ok(usableWidth >= 44, `${width}x${height} retains a 44px-wide target lane`)
    assert.ok(scrollingContentFloor >= 44, `${width}x${height} retains a bounded content scroller with an active download bar`)
    assert.ok(touchTargetFloor >= 44, `${width}px retains 44px controls on both sides of the 767/768 layout breakpoint`)
    assert.ok(95 > 90, 'Model Browser remains above Director Dashboard when nested')
  }

  const zoomedWidths = [640, 780, 1534, 1536].map(width => width / 2)
  assert.deepEqual(zoomedWidths, [320, 390, 767, 768], 'matrix includes 200% zoom at both breakpoint edges')
})

test('Director dashboard presents bounded production, fix, and prompt-refinement copy', async () => {
  const dashboard = await readFile(directorUrl, 'utf8')

  for (const copy of [
    "music_video: 'Music video'",
    "short_film_audio: 'Audio-led short film'",
    "short_film_story: 'Story short film'",
    "|| 'Director production'",
    "crashed: 'Stopped unexpectedly'",
    "|| 'Status unavailable'",
    "cancelling: 'Stopping fixes'",
    "|| 'Fix status unavailable'",
  ]) assert.match(dashboard, new RegExp(copy.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))

  assert.match(dashboard, /pipelineTypeCopy\(p\.pipeline_type\)/)
  assert.match(dashboard, /pipelineStatusCopy\(p\.status\)/)
  assert.match(dashboard, /repairStatusCopy\(p\.repair_status\)/)
  assert.match(dashboard, /pipelineStatusCopy\(selectedPipeline\.status\)/)
  assert.match(dashboard, /pipelineTypeCopy\(selectedPipeline\.pipeline_type\)/)
  assert.match(dashboard, /repairStatusCopy\(repair\?\.status \|\| ''\)/)
  assert.doesNotMatch(dashboard, /\{repair\?\.message \|\| 'Repairing'\}/)
  assert.match(dashboard, /<summary[^>]*>Technical details<\/summary>[\s\S]*?Reported type: \{selectedPipeline\.pipeline_type\}[\s\S]*?Reported status: \{selectedPipeline\.status\}[\s\S]*?Fix note: \{repair\.message\}/)
  assert.doesNotMatch(dashboard, /prompt polish diff|Before Polish|No changes from polish/)
  for (const copy of ['prompt refinements', 'Original', 'Refined', 'No prompt refinements were needed']) {
    assert.match(dashboard, new RegExp(copy))
  }
})

test('Director panel translates image progress with an unknown fallback and optional raw detail', async () => {
  const panel = await readFile(directorPanelUrl, 'utf8')

  for (const copy of [
    "generating: 'Creating image'",
    "polling: 'Waiting for image'",
    "downloading: 'Saving image'",
    "done: 'Image ready'",
    "error: 'Image needs attention'",
    "|| 'Image status unavailable'",
  ]) assert.match(panel, new RegExp(copy.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  assert.match(panel, /imageStatusCopy\(imageGenProgress\.status\)/)
  assert.doesNotMatch(panel, /` — \$\{imageGenProgress\.status\}`/)
  assert.match(panel, /<summary[^>]*>Technical details<\/summary>[\s\S]*?Reported state: \{imageGenProgress\.status\}/)
})
