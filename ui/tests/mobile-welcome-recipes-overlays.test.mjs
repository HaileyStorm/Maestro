import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import test from 'node:test'

import { build } from 'esbuild'
import { compile } from 'tailwindcss'

import { closeModalIfTop, installModalFocus } from '../src/lib/modalFocus.ts'

const welcomeUrl = new URL('../src/components/WelcomeModal.tsx', import.meta.url)
const recipesUrl = new URL('../src/components/Recipes/RecipesOverlay.tsx', import.meta.url)

class FakeDocument extends EventTarget {
  activeElement = null
  body = { style: { overflow: 'auto' } }
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

async function loadOverlayComponent(entryUrl, exportName) {
  const modules = new Map([
    ['react', `
      export function useState(initial) {
        const index = globalThis.__overlayHookIndex++
        if (!(index in globalThis.__overlayHookState)) {
          globalThis.__overlayHookState[index] = typeof initial === 'function' ? initial() : initial
        }
        return [globalThis.__overlayHookState[index], value => {
          const current = globalThis.__overlayHookState[index]
          globalThis.__overlayHookState[index] = typeof value === 'function' ? value(current) : value
        }]
      }
      export function useEffect(effect) {
        const cleanup = effect()
        if (typeof cleanup === 'function') globalThis.__overlayCleanups.push(cleanup)
      }
      export function useId() { return 'overlay-' + globalThis.__overlayIdIndex++ }
      export function useCallback(callback) { return callback }
      export function useRef(initial) {
        const index = globalThis.__overlayRefIndex++
        if (!globalThis.__overlayRefs[index]) globalThis.__overlayRefs[index] = { current: initial }
        return globalThis.__overlayRefs[index]
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
      export const AlertTriangle = icon
      export const ArrowRight = icon
      export const BookMarked = icon
      export const Check = icon
      export const Clapperboard = icon
      export const Cuboid = icon
      export const Download = icon
      export const ExternalLink = icon
      export const FolderLock = icon
      export const Gauge = icon
      export const HardDrive = icon
      export const Layers = icon
      export const ListRestart = icon
      export const Loader2 = icon
      export const MessageSquare = icon
      export const Play = icon
      export const ShieldCheck = icon
      export const Sparkles = icon
      export const Trash2 = icon
      export const Upload = icon
      export const WandSparkles = icon
      export const X = icon
    `],
    ['store', `
      export function useStore(selector) { return selector(globalThis.__overlayStore) }
      useStore.getState = () => globalThis.__overlayStore
    `],
    ['branding', `
      export const PRODUCT_NAME = 'Maestro'
      export const PRODUCT_NAME_VISUAL = 'Maestro'
      export const PRODUCT_PROVENANCE = 'Continuum'
    `],
    ['changelog', `
      export const CURRENT_RELEASE = { version: 'test', summary: 'summary', highlights: [] }
      export const CHANGELOG_MANIFEST = { whyContinuum: [] }
    `],
    ['api', `export async function importRecipe() {}`],
    ['focus', `
      export function installModalFocus(options) {
        globalThis.__overlayInstalls.push(options)
        return () => { globalThis.__overlayCleanupCount += 1 }
      }
      export function closeModalIfTop(document, dialog, onClose) {
        globalThis.__overlayTopCloseDialogs.push(dialog)
        if (!globalThis.__overlayAllowTopClose) return false
        onClose()
        return true
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
      name: 'overlay-harness',
      setup(buildApi) {
        buildApi.onResolve({ filter: /.*/ }, args => {
          if (modules.has(args.path)) return { path: args.path, namespace: 'overlay-test' }
          if (args.path.endsWith('/stores/useStore') || args.path.includes('stores/useStore')) return { path: 'store', namespace: 'overlay-test' }
          if (args.path.endsWith('/lib/branding') || args.path.includes('lib/branding')) return { path: 'branding', namespace: 'overlay-test' }
          if (args.path.endsWith('/lib/changelog') || args.path.includes('lib/changelog')) return { path: 'changelog', namespace: 'overlay-test' }
          if (args.path.endsWith('/api/client') || args.path.includes('api/client')) return { path: 'api', namespace: 'overlay-test' }
          if (args.path.endsWith('/lib/modalFocus') || args.path.includes('lib/modalFocus')) return { path: 'focus', namespace: 'overlay-test' }
          return null
        })
        buildApi.onLoad({ filter: /.*/, namespace: 'overlay-test' }, args => ({
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

function resetOverlayHarness(refs, store, activeElement) {
  globalThis.document = {
    activeElement,
    body: { name: 'document body', style: { overflow: '' } },
    createElement: () => ({ click() {} }),
    getElementById: id => ({ id }),
  }
  globalThis.localStorage = { getItem: () => null, setItem() {} }
  globalThis.HTMLElement = FakeElement
  globalThis.__overlayHookIndex = 0
  globalThis.__overlayHookState = []
  globalThis.__overlayRefIndex = 0
  globalThis.__overlayRefs = refs
  globalThis.__overlayIdIndex = 0
  globalThis.__overlayCleanups = []
  globalThis.__overlayInstalls = []
  globalThis.__overlayCleanupCount = 0
  globalThis.__overlayTopCloseDialogs = []
  globalThis.__overlayAllowTopClose = true
  globalThis.__overlayStore = store
}

test('rendered Welcome and Recipes capture their actual openers, priorities, portals, and top-close controls', async () => {
  const WelcomeModal = await loadOverlayComponent(welcomeUrl, 'WelcomeModal')
  const welcomeDocument = new FakeDocument()
  const welcomeOpener = new FakeElement(welcomeDocument, 'captured Welcome opener')
  const welcomeDialog = new FakeElement(welcomeDocument, 'rendered Welcome dialog')
  const welcomeStart = new FakeElement(welcomeDocument, 'rendered Welcome start')
  resetOverlayHarness(
    [{ current: welcomeDialog }, { current: welcomeStart }],
    {
      accessContext: { remote: false, cloudflare_enabled: false },
      activeWorkspace: 'project-a',
      setSidebarMode() {},
    },
    welcomeOpener,
  )
  const welcomeTree = WelcomeModal()
  const welcomeInstall = globalThis.__overlayInstalls.at(-1)
  assert.equal(welcomeTree.portalTarget, globalThis.document.body)
  assert.equal(welcomeInstall.dialog, welcomeDialog)
  assert.equal(welcomeInstall.initialFocus, welcomeStart)
  assert.equal(welcomeInstall.restoreFocus, welcomeOpener)
  assert.equal(welcomeInstall.priority, 120)
  const welcomeCloseControls = findNodes(welcomeTree, node => node.props?.['aria-label'] === 'Close welcome to Maestro')
  assert.equal(welcomeCloseControls.length, 2)
  globalThis.__overlayAllowTopClose = false
  for (const control of welcomeCloseControls) control.props.onClick()
  assert.deepEqual(globalThis.__overlayTopCloseDialogs, [welcomeDialog, welcomeDialog])

  const RecipesOverlay = await loadOverlayComponent(recipesUrl, 'RecipesOverlay')
  const recipesDocument = new FakeDocument()
  const recipesOpener = new FakeElement(recipesDocument, 'captured Recipes opener')
  const recipesDialog = new FakeElement(recipesDocument, 'rendered Recipes dialog')
  const recipesClose = new FakeElement(recipesDocument, 'rendered Recipes close')
  let recipesSetOpenCalls = 0
  resetOverlayHarness(
    [{ current: recipesDialog }, { current: recipesClose }, { current: false }],
    {
      recipesOpen: true,
      setRecipesOpen() { recipesSetOpenCalls += 1 },
      recipes: [],
      recipesLoading: false,
      recipesError: null,
      async applyRecipe() { return { missing: [] } },
      async deleteRecipe() {},
      loadRecipes() {},
      servicesConfig: { civitai_api_key_set: false },
      setSettingsOpen() {},
      setSettingsTab() {},
      accessContext: { machine_controls: false },
      async downloadRecipeLora() {},
    },
    recipesOpener,
  )
  const recipesTree = RecipesOverlay()
  const recipesInstall = globalThis.__overlayInstalls.at(-1)
  assert.equal(recipesTree.portalTarget, globalThis.document.body)
  assert.equal(recipesInstall.dialog, recipesDialog)
  assert.equal(recipesInstall.initialFocus, recipesClose)
  assert.equal(recipesInstall.restoreFocus, recipesOpener)
  assert.equal(recipesInstall.priority, 100)
  const recipesCloseControls = findNodes(recipesTree, node => node.props?.['aria-label'] === 'Close recipes')
  assert.equal(recipesCloseControls.length, 2)
  globalThis.__overlayAllowTopClose = false
  for (const control of recipesCloseControls) control.props.onClick()
  assert.equal(recipesSetOpenCalls, 0, 'covered rendered Recipes controls cannot close it')
  globalThis.__overlayAllowTopClose = true
  recipesCloseControls[0].props.onClick()
  assert.equal(recipesSetOpenCalls, 1)
  assert.equal(globalThis.__overlayTopCloseDialogs.at(-1), recipesDialog)
})

test('Welcome at priority 120 covers Recipes at 100 without losing locks or exact focus restoration', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const recipes = modalFixture(document, 'Recipes')
  const welcome = modalFixture(document, 'Welcome')
  const outerOpener = new FakeElement(document, 'recipes opener')
  let recipesCloses = 0
  let welcomeCloses = 0

  outerOpener.focus()
  const cleanupRecipes = installModalFocus({
    document,
    dialog: recipes.dialog,
    initialFocus: recipes.first,
    restoreFocus: outerOpener,
    appRoot,
    onClose: () => { recipesCloses += 1 },
    priority: 100,
  })
  recipes.trigger.focus()
  const cleanupWelcome = installModalFocus({
    document,
    dialog: welcome.dialog,
    initialFocus: welcome.first,
    restoreFocus: recipes.trigger,
    appRoot,
    onClose: () => { welcomeCloses += 1 },
    priority: 120,
  })

  assert.equal(document.activeElement, welcome.first)
  assert.equal(recipes.dialog.hasAttribute('inert'), true)
  assert.equal(welcome.dialog.hasAttribute('inert'), false)
  assert.equal(appRoot.hasAttribute('inert'), true)
  assert.equal(document.body.style.overflow, 'hidden')
  assert.equal(closeModalIfTop(document, recipes.dialog, () => { recipesCloses += 1 }), false)
  assert.equal(closeModalIfTop(document, welcome.dialog, () => { welcomeCloses += 1 }), true)
  assert.equal(recipesCloses, 0)
  assert.equal(welcomeCloses, 1)

  welcome.last.focus()
  assert.equal(dispatchKey(document, 'Tab').defaultPrevented, true)
  assert.equal(document.activeElement, welcome.first)
  assert.equal(dispatchKey(document, 'Tab', true).defaultPrevented, true)
  assert.equal(document.activeElement, welcome.last)
  assert.equal(dispatchKey(document, 'Escape').defaultPrevented, true)
  assert.equal(welcomeCloses, 2)
  assert.equal(recipesCloses, 0)

  cleanupWelcome()
  assert.equal(document.activeElement, recipes.trigger)
  assert.equal(recipes.dialog.hasAttribute('inert'), false)
  assert.equal(appRoot.hasAttribute('inert'), true)
  assert.equal(document.body.style.overflow, 'hidden')
  assert.equal(dispatchKey(document, 'Escape').defaultPrevented, true)
  assert.equal(recipesCloses, 1)

  cleanupRecipes()
  assert.equal(document.activeElement, outerOpener)
  assert.equal(appRoot.hasAttribute('inert'), false)
  assert.equal(document.body.style.overflow, 'auto')
})

test('a later Recipes mount cannot cover or steal focus from higher-priority Welcome', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const welcome = modalFixture(document, 'Welcome')
  const recipes = modalFixture(document, 'Recipes')

  const cleanupWelcome = installModalFocus({
    document,
    dialog: welcome.dialog,
    initialFocus: welcome.first,
    restoreFocus: welcome.trigger,
    appRoot,
    onClose: () => {},
    priority: 120,
  })
  const cleanupRecipes = installModalFocus({
    document,
    dialog: recipes.dialog,
    initialFocus: recipes.first,
    restoreFocus: recipes.trigger,
    appRoot,
    onClose: () => {},
    priority: 100,
  })

  assert.equal(document.activeElement, welcome.first)
  assert.equal(recipes.dialog.hasAttribute('inert'), true)
  assert.equal(welcome.dialog.hasAttribute('inert'), false)
  cleanupRecipes()
  cleanupWelcome()
})

test('Welcome and Recipes source geometry covers 320/390/430 widths and 320/767/768 heights', async () => {
  const [welcome, recipes] = await Promise.all([
    readFile(welcomeUrl, 'utf8'),
    readFile(recipesUrl, 'utf8'),
  ])

  for (const source of [welcome, recipes]) {
    assert.match(source, /createPortal\(/)
    assert.match(source, /document\.body/)
    assert.match(source, /role="dialog"/)
    assert.match(source, /aria-modal="true"/)
    assert.match(source, /installModalFocus\(\{/)
    assert.match(source, /closeModalIfTop\(/)
    assert.match(source, /h-\[100vh\][^"\n]*supports-\[height:100dvh\]:h-\[100dvh\]/)
    assert.match(source, /overflow-y-auto/)
    for (const edge of ['top', 'right', 'bottom', 'left']) {
      assert.match(source, new RegExp(`safe-area-inset-${edge}`))
    }
    assert.doesNotMatch(source, /document\.addEventListener\('keydown'/)
  }
  assert.match(welcome, /priority: 120/)
  assert.match(welcome, /max-h-full/)
  assert.match(welcome, /max-h-\[55%\][^"\n]*overflow-y-auto/)
  assert.match(recipes, /priority: 100/)
  assert.match(recipes, /min-h-11 min-w-11/)
  assert.match(recipes, /min-h-11 min-w-11 shrink-0 items-center justify-center[^>]+>Dismiss/)
  assert.match(recipes, /confirmDelete !== card\.id/)
  assert.match(recipes, /setSettingsTab\('integrations'\); setSettingsOpen\(true\)/)

  const utilities = await compile('@tailwind utilities;')
  const compiled = utilities.build([
    'h-[100vh]',
    'supports-[height:100dvh]:h-[100dvh]',
    'max-h-full',
    'max-h-[55%]',
    'min-h-11',
    'min-w-11',
  ])
  assert.match(compiled, /height: 100vh/)
  assert.match(compiled, /@supports \(height:\s*100dvh\)/)
  assert.match(compiled, /height: 100dvh/)
  assert.match(compiled, /max-height: 100%/)
  assert.match(compiled, /max-height: 55%/)

  const zoomCases = [
    { physicalWidth: 640, zoom: 2, expectedCssWidth: 320 },
    { physicalWidth: 780, zoom: 2, expectedCssWidth: 390 },
    { physicalWidth: 860, zoom: 2, expectedCssWidth: 430 },
  ]
  const zoomedWidths = zoomCases.map(({ physicalWidth, zoom, expectedCssWidth }) => {
    const cssWidth = physicalWidth / zoom
    assert.equal(cssWidth, expectedCssWidth)
    return cssWidth
  })
  assert.deepEqual(zoomedWidths, [320, 390, 430], 'the full narrow matrix represents 200% zoomed surfaces')

  for (const width of [320, 390, 430]) {
    for (const height of [320, 767, 768]) {
      const landscape = width > height
      const safe = landscape
        ? { top: 0, right: 24, bottom: 21, left: 24 }
        : { top: 47, right: 0, bottom: 34, left: 0 }
      const usableWidth = width - Math.max(8, safe.left) - Math.max(8, safe.right)
      const usableHeight = height - Math.max(8, safe.top) - Math.max(8, safe.bottom)
      assert.ok(usableWidth >= 44, `${width}x${height} retains a 44px-wide control lane`)
      assert.ok(usableHeight >= 44, `${width}x${height} retains a 44px-high scroll viewport`)
      assert.ok(usableWidth <= width && usableHeight <= height)
      assert.ok(120 > 100, 'Welcome remains above Recipes at every viewport')
      if (height === 320) {
        const mobileFooterCap = usableHeight * 0.55
        const estimatedTwoHundredPercentFooterContent = 140 * 2
        assert.ok(mobileFooterCap < usableHeight - 44, `${width}x${height} reserves scrollable room above the Welcome footer`)
        assert.ok(
          estimatedTwoHundredPercentFooterContent > mobileFooterCap,
          `${width}x${height} requires and enables footer scrolling at 200% text size`,
        )
      }
    }
  }
})
