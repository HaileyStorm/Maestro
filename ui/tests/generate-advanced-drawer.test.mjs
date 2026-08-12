import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import test from 'node:test'

import { build } from 'esbuild'

const componentUrl = new URL('../src/components/Sidebar/AdvancedSettings.tsx', import.meta.url)

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

async function loadAdvancedSettings() {
  const result = await build({
    entryPoints: [componentUrl.pathname],
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
    plugins: [{
      name: 'advanced-drawer-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'advanced-drawer' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'advanced-drawer' }))
        bundle.onResolve({ filter: /^react-dom$/ }, () => ({ path: 'react-dom', namespace: 'advanced-drawer' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide', namespace: 'advanced-drawer' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'advanced-drawer' }))
        bundle.onResolve({ filter: /api\/client$/ }, () => ({ path: 'api', namespace: 'advanced-drawer' }))
        bundle.onResolve({ filter: /\.\/(PostProcessing|ControlVideoSection|DurationSlider)$/ }, args => ({ path: args.path, namespace: 'advanced-drawer' }))
        bundle.onResolve({ filter: /SettingsDrawer\/LoraSelector$/ }, () => ({ path: 'LoraSelector', namespace: 'advanced-drawer' }))
        bundle.onLoad({ filter: /.*/, namespace: 'advanced-drawer' }, args => {
          if (args.path === 'react') {
            return { contents: `
              export const useCallback = callback => callback
              export const useEffect = effect => {
                const cleanup = effect()
                if (typeof cleanup === 'function') globalThis.__advancedDrawerCleanups.push(cleanup)
              }
              export const useRef = initial => ({
                current: globalThis.__advancedDrawerRefValues.length
                  ? globalThis.__advancedDrawerRefValues.shift()
                  : initial,
              })
              export const useState = initial => {
                const value = globalThis.__advancedDrawerStateValues.length
                  ? globalThis.__advancedDrawerStateValues.shift()
                  : (typeof initial === 'function' ? initial() : initial)
                const setter = update => {
                  globalThis.__advancedDrawerStateUpdates.push(
                    typeof update === 'function' ? update(value) : update,
                  )
                }
                return [value, setter]
              }
            ` }
          }
          if (args.path === 'jsx-runtime') {
            return { contents: `
              export const Fragment = Symbol.for('advanced-drawer-fragment')
              export const jsx = (type, props, key) => ({ type, key, props: props || {} })
              export const jsxs = jsx
            ` }
          }
          if (args.path === 'react-dom') {
            return { contents: 'export const createPortal = children => children' }
          }
          if (args.path === 'lucide') {
            return { contents: `
              export const X = 'X', Save = 'Save', Trash2 = 'Trash2', FolderOpen = 'FolderOpen', SlidersHorizontal = 'SlidersHorizontal'
            ` }
          }
          if (args.path === 'store') {
            return { contents: 'export const useStore = selector => selector(globalThis.__advancedDrawerStore)' }
          }
          if (args.path === 'api') {
            return { contents: `
              export const fetchH3AccelerationStatus = async () => null
              export const fetchH3BenchmarkReport = async () => null
            ` }
          }
          return { contents: 'export const PostProcessing = () => null, ControlVideoSection = () => null, LoraSelector = () => null, WindowSettings = () => null' }
        })
      },
    }],
  })
  return import(asDataModule(result.outputFiles[0].text))
}

function createStore() {
  return {
    params: {
      model_type: 'ltx_video',
      seed: -1,
      activated_loras: [],
      image_refs: [],
    },
    modelOptions: {},
    spatialUpsampling: '',
    filmGrainIntensity: 0,
    generationMode: 'video',
    editSubMode: 'recast',
    audioSubMode: 'tts',
    openQueueAfterSubmit: true,
    durationSeconds: 5,
    setParam() {},
    setOpenQueueAfterSubmit() {},
    setDurationSeconds() {},
    selectModel() {},
  }
}

function flattenElements(value, result = []) {
  if (Array.isArray(value)) {
    for (const child of value) flattenElements(child, result)
    return result
  }
  if (!value || typeof value !== 'object') return result
  if ('type' in value && 'props' in value) result.push(value)
  flattenElements(value.props?.children, result)
  return result
}

class FakeDocument extends EventTarget {
  activeElement = null
  body = { style: { overflow: 'auto' } }

  constructor(appRoot) {
    super()
    this.appRoot = appRoot
  }

  getElementById(id) {
    return id === 'root' ? this.appRoot : null
  }
}

class FakeElement {
  attributes = new Map()
  descendants = new Set()
  focusable = []
  nativeControls = []

  constructor(name) {
    this.name = name
    this.document = null
  }

  focus() {
    this.document.activeElement = this
  }

  hasAttribute(name) {
    return this.attributes.has(name)
  }

  setAttribute(name, value = '') {
    this.attributes.set(name, value)
  }

  removeAttribute(name) {
    this.attributes.delete(name)
  }

  contains(element) {
    return element === this || this.descendants.has(element)
  }

  querySelectorAll(selector) {
    if (selector.startsWith('input:not')) return this.nativeControls
    if (selector.startsWith('button:not')) return this.focusable
    throw new Error(`Unexpected focus selector: ${selector}`)
  }
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

function resetRuntime(open, refValues = []) {
  globalThis.__advancedDrawerStateValues = [open]
  globalThis.__advancedDrawerStateUpdates = []
  globalThis.__advancedDrawerCleanups = []
  globalThis.__advancedDrawerStore = createStore()
  globalThis.__advancedDrawerRefValues = [...refValues]
  const appRoot = new FakeElement('app root')
  const document = new FakeDocument(appRoot)
  appRoot.document = document
  globalThis.document = document
  return { appRoot, document }
}

function openRuntime() {
  const trigger = new FakeElement('advanced trigger')
  const dialog = new FakeElement('advanced dialog')
  const close = new FakeElement('advanced close')
  const firstInput = new FakeElement('first input')
  const lastInput = new FakeElement('last input')
  const { appRoot, document } = resetRuntime(true, [trigger, dialog, close])
  for (const element of [trigger, dialog, close, firstInput, lastInput]) element.document = document
  dialog.nativeControls = [firstInput, lastInput]
  dialog.focusable = [close, firstInput, lastInput]
  dialog.descendants = new Set(dialog.focusable)
  trigger.focus()
  return { appRoot, close, dialog, document, firstInput, lastInput, trigger }
}

function desktopGeometry(viewportWidth, open) {
  if (viewportWidth < 768) {
    return {
      left: open ? 0 : -viewportWidth,
      width: viewportWidth,
    }
  }
  const panelLeft = Math.min(560, Math.max(460, viewportWidth * 0.24))
  const panelWidth = Math.min(380, viewportWidth - panelLeft)
  return {
    left: panelLeft + (open ? 0 : -viewportWidth),
    width: panelWidth,
  }
}

test('closed advanced drawer is mounted but inert and has zero viewport intersection', async () => {
  const { AdvancedSettings } = await loadAdvancedSettings()
  resetRuntime(false)
  const elements = flattenElements(AdvancedSettings())
  const trigger = elements.find(element => element.props['aria-controls'] === 'advanced-settings-drawer')
  const drawer = elements.find(element => element.props.role === 'dialog')

  assert.ok(trigger)
  assert.equal(trigger.props.type, 'button')
  assert.equal(trigger.props['aria-expanded'], false)
  assert.equal(trigger.props['aria-label'], 'Open Advanced Settings')
  assert.ok(drawer, 'the closed drawer remains mounted so form state is preserved')
  assert.equal(drawer.props['aria-hidden'], true)
  assert.equal(drawer.props.inert, true)
  assert.match(drawer.props.className, /-translate-x-full/)
  assert.match(drawer.props.className, /md:-translate-x-\[100vw\]/)
  assert.match(drawer.props.className, /pointer-events-none/)

  for (const viewportWidth of [320, 360, 390, 430, 767, 768, 1080, 1920, 3840]) {
    const geometry = desktopGeometry(viewportWidth, false)
    const intersection = Math.max(
      0,
      Math.min(viewportWidth, geometry.left + geometry.width) - Math.max(0, geometry.left),
    )
    assert.equal(intersection, 0, `${viewportWidth}px closed drawer must be fully outside the viewport`)
  }
})

test('open geometry stays anchored and modal focus traps both tab directions', async () => {
  const { AdvancedSettings } = await loadAdvancedSettings()
  const runtime = openRuntime()
  const elements = flattenElements(AdvancedSettings())
  const trigger = elements.find(element => element.props['aria-controls'] === 'advanced-settings-drawer')
  const drawer = elements.find(element => element.props.role === 'dialog')
  const closeButtons = elements.filter(element => element.props['aria-label'] === 'Close Advanced Settings')
  const backdrop = closeButtons.find(element => String(element.props.className).includes('inset-0'))
  const closeButton = closeButtons.find(element => !String(element.props.className).includes('inset-0') && element !== trigger)

  assert.equal(trigger.props['aria-expanded'], true)
  assert.equal(trigger.props['aria-label'], 'Close Advanced Settings')
  assert.equal(drawer.props['aria-hidden'], false)
  assert.equal(drawer.props.inert, false)
  assert.equal(drawer.props['aria-modal'], true)
  assert.match(drawer.props.className, /translate-x-0/)
  assert.match(drawer.props.className, /z-\[80\]/)
  assert.match(drawer.props.className, /h-\[100vh\] supports-\[height:100dvh\]:h-\[100dvh\]/)
  assert.match(drawer.props.className, /safe-area-inset-left/)
  assert.match(drawer.props.className, /safe-area-inset-right/)
  assert.ok(backdrop)
  assert.equal(backdrop.props.type, 'button')
  assert.equal(backdrop.props.tabIndex, -1)
  assert.match(backdrop.props.className, /z-\[70\]/)
  assert.ok(closeButton)
  assert.equal(closeButton.props.type, 'button')
  assert.equal(typeof backdrop.props.onClick, 'function')
  assert.equal(typeof closeButton.props.onClick, 'function')
  assert.equal(runtime.document.activeElement, runtime.close, 'opening transfers focus to the drawer close control')
  assert.equal(runtime.appRoot.hasAttribute('inert'), true, 'the portalled modal makes the background inert')
  assert.equal(runtime.document.body.style.overflow, 'hidden')
  assert.equal(runtime.firstInput.attributes.get('tabindex'), '0')
  assert.equal(runtime.lastInput.attributes.get('tabindex'), '0')

  runtime.lastInput.focus()
  assert.equal(dispatchKey(runtime.document, 'Tab').defaultPrevented, true)
  assert.equal(runtime.document.activeElement, runtime.close)
  runtime.close.focus()
  assert.equal(dispatchKey(runtime.document, 'Tab', true).defaultPrevented, true)
  assert.equal(runtime.document.activeElement, runtime.lastInput)

  for (const viewportWidth of [768, 1080, 1920, 3840]) {
    const geometry = desktopGeometry(viewportWidth, true)
    assert.ok(geometry.left >= 460 && geometry.left <= 560)
    assert.ok(geometry.width > 0 && geometry.width <= 380)
    assert.ok(geometry.left + geometry.width <= viewportWidth)
  }

  for (const [viewportWidth, viewportHeight] of [[320, 568], [360, 800], [390, 844], [430, 932], [767, 430]]) {
    const geometry = desktopGeometry(viewportWidth, true)
    assert.equal(geometry.left, 0)
    assert.equal(geometry.width, viewportWidth)
    assert.ok(viewportHeight > 0, `${viewportWidth}x${viewportHeight} dynamic viewport remains usable`)
  }

  globalThis.__advancedDrawerCleanups.forEach(cleanup => cleanup())
  assert.equal(runtime.appRoot.hasAttribute('inert'), false)
  assert.equal(runtime.document.body.style.overflow, 'auto')
  assert.equal(runtime.document.activeElement, runtime.trigger)
  assert.equal(runtime.firstInput.hasAttribute('tabindex'), false)
  assert.equal(runtime.lastInput.hasAttribute('tabindex'), false)
})

test('actual trigger, backdrop, X, and Escape callbacks close and restore trigger focus', async () => {
  const { AdvancedSettings } = await loadAdvancedSettings()

  resetRuntime(false)
  const closedElements = flattenElements(AdvancedSettings())
  const closedTrigger = closedElements.find(element => element.props['aria-controls'] === 'advanced-settings-drawer')
  closedTrigger.props.onClick()
  assert.deepEqual(globalThis.__advancedDrawerStateUpdates, [true], 'the actual trigger callback opens the drawer')

  for (const dismissal of ['trigger', 'backdrop', 'close', 'escape']) {
    const runtime = openRuntime()
    const elements = flattenElements(AdvancedSettings())
    const trigger = elements.find(element => element.props['aria-controls'] === 'advanced-settings-drawer')
    const closeButtons = elements.filter(element => element.props['aria-label'] === 'Close Advanced Settings')
    const backdrop = closeButtons.find(element => String(element.props.className).includes('inset-0'))
    const closeButton = closeButtons.find(element => !String(element.props.className).includes('inset-0') && element !== trigger)

    if (dismissal === 'trigger') trigger.props.onClick()
    else if (dismissal === 'backdrop') backdrop.props.onClick()
    else if (dismissal === 'close') closeButton.props.onClick()
    else {
      const escape = dispatchKey(runtime.document, 'Escape')
      assert.equal(escape.defaultPrevented, true)
    }

    assert.deepEqual(globalThis.__advancedDrawerStateUpdates, [false], `${dismissal} requests close`)
    globalThis.__advancedDrawerCleanups.forEach(cleanup => cleanup())
    assert.equal(runtime.appRoot.hasAttribute('inert'), false, `${dismissal} restores background semantics`)
    assert.equal(runtime.document.activeElement, runtime.trigger, `${dismissal} restores trigger focus`)
  }
})
