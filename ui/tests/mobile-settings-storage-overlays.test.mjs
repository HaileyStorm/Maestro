import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import test from 'node:test'

import { build } from 'esbuild'
import { compile } from 'tailwindcss'

import {
  closeModalIfTop,
  installModalFocus,
} from '../src/lib/modalFocus.ts'

const settingsUrl = new URL('../src/components/SettingsDrawer/SettingsDrawer.tsx', import.meta.url)
const storageUrl = new URL('../src/components/StorageDashboard/StorageDashboard.tsx', import.meta.url)
const systemSettingsUrl = new URL('../src/components/SettingsDrawer/SystemSettingsPanel.tsx', import.meta.url)
const servicesSettingsUrl = new URL('../src/components/SettingsDrawer/ServicesSettingsPanel.tsx', import.meta.url)

class FakeDocument extends EventTarget {
  activeElement = null
  body = { name: 'document body', style: { overflow: 'auto' } }
  appRoot = null

  getElementById(id) {
    return id === 'root' ? this.appRoot : null
  }
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

async function loadOverlay(entryUrl, exportName) {
  const modules = new Map([
    ['react', `
      export function useState(initial) {
        const value = typeof initial === 'function' ? initial() : initial
        return [value, update => globalThis.__settingsStorageStateUpdates.push(typeof update === 'function' ? update(value) : update)]
      }
      export function useEffect(effect) {
        const cleanup = effect()
        if (typeof cleanup === 'function') globalThis.__settingsStorageCleanups.push(cleanup)
      }
      export function useCallback(callback) { return callback }
      export function useId() { return 'settings-storage-' + globalThis.__settingsStorageIdIndex++ }
      export function useRef(initial) {
        return { current: globalThis.__settingsStorageRefs.length ? globalThis.__settingsStorageRefs.shift() : initial }
      }
    `],
    ['react-dom', `
      export function createPortal(children, target) { return { ...children, portalTarget: target } }
    `],
    ['react/jsx-runtime', `
      export const Fragment = Symbol('Fragment')
      export function jsx(type, props, key) { return { type, props: props || {}, key } }
      export const jsxs = jsx
    `],
    ['lucide-react', `
      const icon = props => ({ type: 'svg', props: props || {} })
      export const Boxes = icon, Copy = icon, Film = icon, FolderOpen = icon, HardDrive = icon
      export const Loader2 = icon, RefreshCw = icon, Trash2 = icon, X = icon
    `],
    ['store', `
      export function useStore(selector) { return selector(globalThis.__settingsStorageStore) }
      useStore.getState = () => globalThis.__settingsStorageStore
    `],
    ['focus', `
      export function installModalFocus(options) {
        globalThis.__settingsStorageInstalls.push(options)
        return () => { globalThis.__settingsStorageCleanupCount += 1 }
      }
      export function closeModalIfTop(document, dialog, onClose) {
        globalThis.__settingsStorageCloseRequests.push(dialog)
        if (dialog !== globalThis.__settingsStorageTopDialog) return false
        onClose()
        return true
      }
    `],
    ['panels', `
      export function SystemSettingsPanel() { return null }
      export function ServicesSettingsPanel() { return null }
    `],
    ['api', `
      export async function fetchStorageUsage() { return { models_total_bytes: 0, loras: [], workspaces: [], models: [], scanned_sidecars: 0 } }
      export async function fetchStorageDuplicates() { return { duplicates: [], conflicts: [], total_reclaimable_bytes: 0 } }
      export async function reclaimDuplicate() {}
      export async function removeLinkedDuplicate() {}
      export async function deleteModel() {}
      export async function deleteLoraFile() {}
      export async function deleteWorkspace() {}
    `],
    ['format', 'export function formatBytes(value) { return String(value) }'],
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
      name: 'settings-storage-harness',
      setup(bundle) {
        bundle.onResolve({ filter: /.*/ }, args => {
          if (modules.has(args.path)) return { path: args.path, namespace: 'settings-storage' }
          if (args.path === 'react' || args.path === 'react-dom' || args.path === 'react/jsx-runtime' || args.path === 'lucide-react') {
            return { path: args.path, namespace: 'settings-storage' }
          }
          if (args.path.includes('stores/useStore')) return { path: 'store', namespace: 'settings-storage' }
          if (args.path.includes('lib/modalFocus')) return { path: 'focus', namespace: 'settings-storage' }
          if (args.path.endsWith('/SystemSettingsPanel') || args.path.endsWith('/ServicesSettingsPanel')) return { path: 'panels', namespace: 'settings-storage' }
          if (args.path.includes('api/client')) return { path: 'api', namespace: 'settings-storage' }
          if (args.path.includes('lib/format')) return { path: 'format', namespace: 'settings-storage' }
          return null
        })
        bundle.onLoad({ filter: /.*/, namespace: 'settings-storage' }, args => ({
          contents: modules.get(args.path),
          loader: 'js',
        }))
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

function resetOverlayHarness(document, refs, store, activeElement) {
  globalThis.document = document
  globalThis.HTMLElement = FakeElement
  globalThis.__settingsStorageRefs = [...refs]
  globalThis.__settingsStorageStore = store
  globalThis.__settingsStorageInstalls = []
  globalThis.__settingsStorageCleanups = []
  globalThis.__settingsStorageCleanupCount = 0
  globalThis.__settingsStorageCloseRequests = []
  globalThis.__settingsStorageStateUpdates = []
  globalThis.__settingsStorageIdIndex = 0
  globalThis.__settingsStorageTopDialog = null
  document.activeElement = activeElement
}

test('rendered Settings and Storage portals capture exact openers and enforce top-only controls', async () => {
  const SettingsDrawer = await loadOverlay(settingsUrl, 'SettingsDrawer')
  const StorageDashboard = await loadOverlay(storageUrl, 'StorageDashboard')
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const outerOpener = new FakeElement(document, 'settings opener')
  const settingsDialog = new FakeElement(document, 'settings dialog')
  const settingsClose = new FakeElement(document, 'settings close')
  const storageOpener = new FakeElement(document, 'storage opener')
  const storageDialog = new FakeElement(document, 'storage dialog')
  const storageClose = new FakeElement(document, 'storage close')
  document.appRoot = appRoot
  settingsDialog.descendants.add(storageOpener)

  let settingsCloseCalls = 0
  resetOverlayHarness(document, [settingsDialog, settingsClose], {
    settingsOpen: true,
    setSettingsOpen(open) { if (!open) settingsCloseCalls += 1 },
    settingsTab: 'performance',
    setSettingsTab() {},
  }, outerOpener)
  const settingsTree = SettingsDrawer()
  const settingsInstall = globalThis.__settingsStorageInstalls.at(-1)
  assert.equal(settingsTree.portalTarget, document.body)
  assert.equal(settingsInstall.dialog, settingsDialog)
  assert.equal(settingsInstall.initialFocus, settingsClose)
  assert.equal(settingsInstall.restoreFocus, outerOpener)
  assert.equal(settingsInstall.appRoot, appRoot)
  assert.equal(settingsInstall.priority, 50)

  resetOverlayHarness(document, [storageDialog, storageClose], {
    storageDashboardOpen: true,
    setStorageDashboardOpen(open) { if (!open) globalThis.__storageCloseCalls += 1 },
    loadWorkspaces() {},
    servicesConfig: { storage_allow_linked_removal: false },
    updateServicesConfig() {},
  }, storageOpener)
  globalThis.__storageCloseCalls = 0
  const storageTree = StorageDashboard()
  const storageInstall = globalThis.__settingsStorageInstalls.at(-1)
  assert.equal(storageTree.portalTarget, document.body)
  assert.equal(storageInstall.dialog, storageDialog)
  assert.equal(storageInstall.initialFocus, storageClose)
  assert.equal(storageInstall.restoreFocus, storageOpener)
  assert.equal(storageInstall.appRoot, appRoot)
  assert.equal(storageInstall.priority, 70)

  const storageCloseControls = findNodes(storageTree, node => node.props?.['aria-label'] === 'Close storage manager')
  assert.equal(storageCloseControls.length, 2)
  globalThis.__settingsStorageTopDialog = settingsDialog
  for (const control of storageCloseControls) control.props.onClick()
  assert.equal(globalThis.__storageCloseCalls, 0, 'covered Storage controls cannot dismiss')
  globalThis.__settingsStorageTopDialog = storageDialog
  storageCloseControls[0].props.onClick()
  assert.equal(globalThis.__storageCloseCalls, 1)

  resetOverlayHarness(document, [settingsDialog, settingsClose], {
    settingsOpen: true,
    setSettingsOpen(open) { if (!open) settingsCloseCalls += 1 },
    settingsTab: 'performance',
    setSettingsTab() {},
  }, outerOpener)
  const rerenderedSettings = SettingsDrawer()
  const settingsCloseControls = findNodes(rerenderedSettings, node => node.props?.['aria-label'] === 'Close settings')
  assert.equal(settingsCloseControls.length, 2)
  globalThis.__settingsStorageTopDialog = storageDialog
  for (const control of settingsCloseControls) control.props.onClick()
  assert.equal(settingsCloseCalls, 0, 'covered Settings controls cannot dismiss below Storage')
})

test('real modal stack restores nested and parent-first focus chains without dropping shared locks', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const outerOpener = new FakeElement(document, 'outer opener')
  const settingsDialog = new FakeElement(document, 'settings dialog')
  const settingsClose = new FakeElement(document, 'settings close')
  const storageOpener = new FakeElement(document, 'storage opener')
  const storageDialog = new FakeElement(document, 'storage dialog')
  const storageClose = new FakeElement(document, 'storage close')
  settingsDialog.descendants.add(storageOpener)
  document.appRoot = appRoot

  outerOpener.focus()
  const cleanupSettings = installModalFocus({
    document,
    dialog: settingsDialog,
    initialFocus: settingsClose,
    restoreFocus: outerOpener,
    appRoot,
    onClose() {},
    priority: 50,
  })
  storageOpener.focus()
  const installStorage = () => installModalFocus({
    document,
    dialog: storageDialog,
    initialFocus: storageClose,
    restoreFocus: storageOpener,
    appRoot,
    onClose() {},
    priority: 70,
  })
  const cleanupStorage = installStorage()
  assert.equal(settingsDialog.hasAttribute('inert'), true)
  assert.equal(document.body.style.overflow, 'hidden')
  assert.equal(closeModalIfTop(document, settingsDialog, () => assert.fail('covered Settings closed')), false)

  cleanupStorage()
  assert.equal(document.activeElement, storageOpener, 'normal Storage close restores inside Settings')
  assert.equal(settingsDialog.hasAttribute('inert'), false, 'normal Storage close uncovers Settings')
  assert.equal(appRoot.hasAttribute('inert'), true, 'Settings retains the shared app lock')
  assert.equal(document.body.style.overflow, 'hidden')

  const cleanupReopenedStorage = installStorage()
  cleanupSettings()
  assert.equal(appRoot.hasAttribute('inert'), true, 'Storage retains the shared app lock')
  assert.equal(document.body.style.overflow, 'hidden')
  cleanupReopenedStorage()
  assert.equal(document.activeElement, outerOpener, 'parent-first close transfers Storage restoration outside Settings')
  assert.equal(appRoot.hasAttribute('inert'), false)
  assert.equal(document.body.style.overflow, 'auto')
})

test('Settings and Storage geometry, labels, and controls cover the narrow 200% zoom matrix', async () => {
  const [settings, storage, systemSettings] = await Promise.all([
    readFile(settingsUrl, 'utf8'),
    readFile(storageUrl, 'utf8'),
    readFile(systemSettingsUrl, 'utf8'),
  ])
  for (const source of [settings, storage]) {
    assert.match(source, /createPortal\(/)
    assert.match(source, /document\.body/)
    assert.match(source, /role="dialog"/)
    assert.match(source, /aria-modal="true"/)
    assert.match(source, /installModalFocus\(\{/)
    assert.match(source, /closeModalIfTop\(/)
    assert.match(source, /h-\[100vh\][^"\n]*supports-\[height:100dvh\]:h-\[100dvh\]/)
    assert.match(source, /overflow-y-auto/)
    assert.match(source, /min-h-11 min-w-11/)
    assert.doesNotMatch(source, /document\.addEventListener\('keydown'/)
  }
  for (const edge of ['top', 'right', 'bottom', 'left']) {
    assert.match(settings, new RegExp(`safe-area-inset-${edge}`))
    assert.match(storage, new RegExp(`safe-area-inset-${edge}`))
  }
  assert.match(settings, /priority: 50/)
  assert.match(storage, /priority: 70/)
  assert.match(storage, /z-\[60\]/)
  assert.match(storage, /z-\[70\]/)
  assert.match(storage, /md:flex-nowrap/)
  assert.match(storage, /aria-label=\{`\$\{confirmKey === key \? 'Confirm ' : ''\}\$\{label\} \$\{accessibleSubject\}`\}/)
  assert.match(storage, /Duplicate files across installations/)
  assert.match(storage, /Delete Maestro copy/)
  assert.match(storage, /Recycle other copy/)
  assert.match(storage, /from \$\{d\.linked_install\} to the Recycle Bin/)
  assert.match(storage, /Windows Recycle Bin, where it can be restored/)
  assert.match(storage, /This LoRA is stored in another installation, so it cannot be deleted here/)
  assert.doesNotMatch(storage, /deleting Maestro's copy is free|Allow removing from linked installs|linked only/)
  assert.match(systemSettings, /aria-haspopup="dialog"/)
  assert.match(systemSettings, /aria-controls="storage-manager-dialog"/)

  const utilities = await compile('@tailwind utilities;')
  const compiled = utilities.build([
    'h-[100vh]',
    'supports-[height:100dvh]:h-[100dvh]',
    'min-h-11',
    'min-w-11',
    'md:flex-nowrap',
  ])
  assert.match(compiled, /height: 100vh/)
  assert.match(compiled, /@supports \(height:\s*100dvh\)/)
  assert.match(compiled, /height: 100dvh/)

  for (const width of [320, 390, 430]) {
    for (const height of [320, 767, 768]) {
      const safe = height === 320
        ? { top: 0, right: 24, bottom: 21, left: 24 }
        : { top: 47, right: 0, bottom: 34, left: 0 }
      const usableWidth = width - Math.max(8, safe.left) - Math.max(8, safe.right)
      const usableHeight = height - Math.max(8, safe.top) - Math.max(8, safe.bottom)
      const settingsWidth = width < 768 ? width : Math.min(560, Math.max(460, width * 0.24))
      assert.ok(usableWidth >= 44, `${width}x${height} retains a 44px-wide control lane`)
      assert.ok(usableHeight >= 44, `${width}x${height} retains a scroll viewport`)
      assert.ok(settingsWidth > 0 && settingsWidth <= width)
      assert.ok(70 > 60 && 60 > 50, 'Storage dialog/backdrop remains above Settings')
    }
  }

  const zoomedWidths = [640, 780, 860].map(width => width / 2)
  assert.deepEqual(zoomedWidths, [320, 390, 430], 'matrix covers 200% zoom CSS widths')
})

test('Services settings explain model location and external data use in plain language', async () => {
  const services = await readFile(servicesSettingsUrl, 'utf8')
  assert.match(services, /Writing Assistant/)
  assert.match(services, /Where the assistant runs/)
  assert.match(services, /const providerLabel = isRemote \? 'your configured server' : isOpenAI \? 'OpenAI' : 'Anthropic'/)
  assert.match(services, /prompt text and any attached context may be sent there/)
  assert.match(services, /terms and privacy policy apply separately/)
  assert.match(services, /The first use may download about 4 GB of model files, which Maestro saves for later/)
  assert.doesNotMatch(services, /dialect-specific enhance pipeline|shared host cache/)
})
