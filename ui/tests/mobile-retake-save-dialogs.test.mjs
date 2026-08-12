import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import test from 'node:test'

import { build } from 'esbuild'
import { compile } from 'tailwindcss'

import { closeModalIfTop, installModalFocus } from '../src/lib/modalFocus.ts'

const uiRoot = new URL('../', import.meta.url)
const retakeUrl = new URL('../src/components/RetakeDialog.tsx', import.meta.url)
const retakeControlsUrl = new URL('../src/components/Sidebar/RetakeControls.tsx', import.meta.url)
const saveUrl = new URL('../src/components/Recipes/SaveRecipeDialog.tsx', import.meta.url)
const mediaFeedUrl = new URL('../src/components/MainContent/MediaFeedItem.tsx', import.meta.url)

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

function dispatchKey(document, key, shiftKey = false) {
  const event = new Event('keydown', { cancelable: true })
  Object.defineProperties(event, {
    key: { value: key },
    shiftKey: { value: shiftKey },
  })
  document.dispatchEvent(event)
  return event
}

function fixture(document, name) {
  const dialog = new FakeElement(document, `${name} dialog`)
  const first = new FakeElement(document, `${name} first`)
  const last = new FakeElement(document, `${name} last`)
  const trigger = new FakeElement(document, `${name} trigger`)
  dialog.descendants = new Set([first, last, trigger])
  dialog.focusable = [first, last]
  return { dialog, first, last, trigger }
}

test('Save and Retake form a priority-ordered modal pair with top-only dismissal and focus wrapping', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const outerTrigger = new FakeElement(document, 'gallery save trigger')
  const save = fixture(document, 'Save')
  const retake = fixture(document, 'Retake')
  save.dialog.descendants.add(retake.trigger)
  let saveCloses = 0
  let retakeCloses = 0

  outerTrigger.focus()
  const cleanupSave = installModalFocus({
    document,
    dialog: save.dialog,
    initialFocus: save.first,
    restoreFocus: outerTrigger,
    appRoot,
    onClose: () => { saveCloses += 1 },
    priority: 100,
  })
  retake.trigger.focus()
  const cleanupRetake = installModalFocus({
    document,
    dialog: retake.dialog,
    initialFocus: retake.first,
    restoreFocus: retake.trigger,
    appRoot,
    onClose: () => { retakeCloses += 1 },
    priority: 100,
  })

  assert.equal(document.activeElement, retake.first)
  assert.equal(save.dialog.hasAttribute('inert'), true)
  assert.equal(retake.dialog.hasAttribute('inert'), false)
  assert.equal(appRoot.hasAttribute('inert'), true)
  assert.equal(document.body.style.overflow, 'hidden')
  assert.equal(closeModalIfTop(document, save.dialog, () => { saveCloses += 1 }), false)
  assert.equal(closeModalIfTop(document, retake.dialog, () => { retakeCloses += 1 }), true)
  assert.equal(saveCloses, 0)
  assert.equal(retakeCloses, 1)

  retake.last.focus()
  assert.equal(dispatchKey(document, 'Tab').defaultPrevented, true)
  assert.equal(document.activeElement, retake.first)
  assert.equal(dispatchKey(document, 'Tab', true).defaultPrevented, true)
  assert.equal(document.activeElement, retake.last)
  assert.equal(dispatchKey(document, 'Escape').defaultPrevented, true)
  assert.equal(retakeCloses, 2)
  assert.equal(saveCloses, 0)

  cleanupRetake()
  assert.equal(document.activeElement, retake.trigger)
  assert.equal(save.dialog.hasAttribute('inert'), false)
  assert.equal(appRoot.hasAttribute('inert'), true)
  assert.equal(document.body.style.overflow, 'hidden')
  assert.equal(dispatchKey(document, 'Escape').defaultPrevented, true)
  assert.equal(saveCloses, 1, 'second Escape reaches Save only after Retake is removed')
  cleanupSave()
  assert.equal(document.activeElement, outerTrigger)
  assert.equal(appRoot.hasAttribute('inert'), false)
  assert.equal(document.body.style.overflow, 'auto')
})

test('programmatic covered-parent close retains exact outer focus restoration', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const outerTrigger = new FakeElement(document, 'gallery opener')
  const save = fixture(document, 'Save')
  const retake = fixture(document, 'Retake')
  save.dialog.descendants.add(retake.trigger)

  outerTrigger.focus()
  const cleanupSave = installModalFocus({
    document,
    dialog: save.dialog,
    initialFocus: save.first,
    restoreFocus: outerTrigger,
    appRoot,
    onClose: () => {},
    priority: 100,
  })
  retake.trigger.focus()
  const cleanupRetake = installModalFocus({
    document,
    dialog: retake.dialog,
    initialFocus: retake.first,
    restoreFocus: retake.trigger,
    appRoot,
    onClose: () => {},
    priority: 100,
  })

  cleanupSave()
  assert.equal(document.activeElement, retake.first)
  assert.equal(appRoot.hasAttribute('inert'), true)
  cleanupRetake()
  assert.equal(document.activeElement, outerTrigger)
  assert.equal(appRoot.hasAttribute('inert'), false)
  assert.equal(document.body.style.overflow, 'auto')
})

function treeChildren(node) {
  if (!node || typeof node !== 'object') return []
  const children = node.props?.children
  return Array.isArray(children) ? children : children == null ? [] : [children]
}

function findNode(node, predicate) {
  if (node && typeof node === 'object' && predicate(node)) return node
  for (const child of treeChildren(node)) {
    const match = findNode(child, predicate)
    if (match) return match
  }
  return null
}

function findNodes(node, predicate, matches = []) {
  if (node && typeof node === 'object' && predicate(node)) matches.push(node)
  for (const child of treeChildren(node)) findNodes(child, predicate, matches)
  return matches
}

function nodeText(node) {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  return treeChildren(node).map(nodeText).join('')
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

async function flushPromises() {
  await Promise.resolve()
  await Promise.resolve()
}

async function loadDialogComponent(entryUrl, exportName) {
  const modules = new Map([
    ['react', `
      export function useState(initial) {
        const index = globalThis.__dialogHookIndex++
        if (!(index in globalThis.__dialogHookState)) {
          globalThis.__dialogHookState[index] = typeof initial === 'function' ? initial() : initial
        }
        return [globalThis.__dialogHookState[index], value => {
          const current = globalThis.__dialogHookState[index]
          globalThis.__dialogHookState[index] = typeof value === 'function' ? value(current) : value
        }]
      }
      export function useEffect(effect) {
        const cleanup = effect()
        if (typeof cleanup === 'function') globalThis.__dialogCleanups.push(cleanup)
      }
      export function useId() { return 'dialog-' + globalThis.__dialogIdIndex++ }
      export function useCallback(callback) { return callback }
      export function useRef(initial) {
        const index = globalThis.__dialogRefIndex++
        if (!globalThis.__dialogRefs[index]) globalThis.__dialogRefs[index] = { current: initial }
        return globalThis.__dialogRefs[index]
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
      export const BookMarked = icon
      export const Loader2 = icon
      export const Upload = icon
      export const X = icon
    `],
    ['../stores/useStore', `
      export function useStore(selector) { return selector(globalThis.__retakeStore) }
      useStore.setState = update => Object.assign(globalThis.__retakeStore, update)
    `],
    ['../../stores/useStore', `
      export function useStore(selector) { return selector(globalThis.__retakeStore) }
      useStore.setState = update => Object.assign(globalThis.__retakeStore, update)
    `],
    ['./shared/VideoTimelineSelector', `
      export function VideoTimelineSelector(props) { return { type: 'timeline', props } }
    `],
    ['../api/client', `
      export function getFileUrl(path) { return '/files/' + path }
      export async function submitRetake(payload) {
        globalThis.__retakePayloads.push(payload)
        if (globalThis.__retakeSubmit) return globalThis.__retakeSubmit(payload)
        return { retake_frames: '0-17/300' }
      }
    `],
    ['../../api/client', `
      export async function uploadImage(file) { return globalThis.__retakeUpload(file) }
    `],
    ['../lib/modelDisplay', `
      export function modelDisplayName(model) { return 'Display ' + model }
    `],
    ['../lib/modalFocus', `
      export function installModalFocus(options) {
        globalThis.__dialogInstalls.push(options)
        return () => { globalThis.__dialogUninstalls += 1 }
      }
      export function closeModalIfTop(document, dialog, onClose) {
        globalThis.__dialogTopCloseRequests.push(dialog)
        if (globalThis.__dialogAllowTopClose) onClose()
        return globalThis.__dialogAllowTopClose
      }
    `],
    ['../../lib/modalFocus', `
      export function installModalFocus(options) {
        globalThis.__dialogInstalls.push(options)
        return () => { globalThis.__dialogUninstalls += 1 }
      }
      export function closeModalIfTop(document, dialog, onClose) {
        globalThis.__dialogTopCloseRequests.push(dialog)
        if (globalThis.__dialogAllowTopClose) onClose()
        return globalThis.__dialogAllowTopClose
      }
    `],
  ])
  const result = await build({
    entryPoints: [entryUrl.pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'dialog-test-mocks',
      setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => (
          modules.has(args.path) ? { path: args.path, namespace: 'dialog-test' } : undefined
        ))
        builder.onLoad({ filter: /.*/, namespace: 'dialog-test' }, args => ({
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
  return compiledModule.exports[exportName]
}

async function loadMediaFeedItemHarness() {
  const iconNames = [
    'Play', 'Pencil', 'RefreshCw', 'Copy', 'Trash2', 'Check', 'Combine', 'Loader2',
    'Heart', 'ArrowLeftToLine', 'Download', 'FolderInput', 'Scissors', 'FastForward',
    'BookMarked', 'EyeOff', 'Share2', 'Link2Off',
  ]
  const modules = new Map([
    ['react', `
      export function useState(initial) {
        const index = globalThis.__dialogHookIndex++
        if (!(index in globalThis.__dialogHookState)) {
          globalThis.__dialogHookState[index] = typeof initial === 'function' ? initial() : initial
        }
        return [globalThis.__dialogHookState[index], value => {
          const current = globalThis.__dialogHookState[index]
          globalThis.__dialogHookState[index] = typeof value === 'function' ? value(current) : value
        }]
      }
      export function useEffect(effect) {
        const cleanup = effect()
        if (typeof cleanup === 'function') globalThis.__dialogCleanups.push(cleanup)
      }
      export function useCallback(callback) { return callback }
      export function useRef(initial) {
        const index = globalThis.__dialogRefIndex++
        if (!globalThis.__dialogRefs[index]) globalThis.__dialogRefs[index] = { current: initial }
        return globalThis.__dialogRefs[index]
      }
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
    ['../Recipes/SaveRecipeDialog', `
      export function SaveRecipeDialog(props) { return { type: 'save-recipe-dialog', props } }
    `],
    ['../../stores/useStore', `
      export function useStore(selector) { return selector(globalThis.__mediaStore) }
    `],
    ['../../api/client', `
      export async function createOutputShare() { return {} }
      export async function deleteOutputComponents() { return {} }
      export async function fetchOutputMetadata() { return {} }
      export function getFileUrl(path) { return '/files/' + path }
      export function getUploadUrl(path) { return '/uploads/' + path }
      export async function moveOutput() { return {} }
      export async function revokeOutputShare() { return {} }
      export async function uploadImage() { return {} }
    `],
    ['../../lib/modelDisplay', `
      export function modelDisplayName(model) { return model }
    `],
    ['../../lib/privatePreview', `
      export function hidePrivatePreview() {}
      export function privatePreviewIdentity(workspace, name, revision = '') { return workspace + ':' + name + ':' + revision }
      export function privatePreviewWasRevealed() { return false }
      export function revealPrivatePreview() {}
      export function subscribePrivatePreviewReveal() { return () => {} }
    `],
  ])
  const result = await build({
    entryPoints: [mediaFeedUrl.pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'media-feed-dialog-test-mocks',
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

function resetDialogHarness(refs) {
  const video = { src: '', onloadedmetadata: null, onerror: null, duration: 0, videoWidth: 0, videoHeight: 0 }
  globalThis.document = {
    activeElement: null,
    body: { name: 'document body', style: { overflow: '' } },
    createElement: () => {
      globalThis.__retakeVideo = video
      return video
    },
    getElementById: id => ({ id }),
  }
  globalThis.__dialogHookIndex = 0
  globalThis.__dialogHookState = []
  globalThis.__dialogRefIndex = 0
  globalThis.__dialogRefs = refs
  globalThis.__dialogIdIndex = 0
  globalThis.__dialogCleanups = []
  globalThis.__dialogInstalls = []
  globalThis.__dialogUninstalls = 0
  globalThis.__dialogTopCloseRequests = []
  globalThis.__dialogAllowTopClose = true
  globalThis.__retakePayloads = []
  globalThis.__retakeSubmit = null
  globalThis.__retakeUpload = async () => ({ path: '/uploaded/video.mp4' })
  globalThis.__retakeVideo = null
}

function beginRender() {
  globalThis.__dialogHookIndex = 0
  globalThis.__dialogRefIndex = 0
  globalThis.__dialogIdIndex = 0
}

test('bundled Save dialog portals, installs focus, closes top-only, and preserves recipe payload', async () => {
  const SaveRecipeDialog = await loadDialogComponent(saveUrl, 'SaveRecipeDialog')
  const dialog = { name: 'save dialog' }
  const nameInput = { name: 'name input' }
  const opener = { name: 'save trigger' }
  resetDialogHarness([{ current: dialog }, { current: nameInput }])
  let cancelCount = 0
  const saves = []
  const props = {
    onCancel: () => { cancelCount += 1 },
    onSave: async (...args) => { saves.push(args) },
    restoreFocusRef: { current: opener },
  }

  let tree = SaveRecipeDialog(props)
  assert.equal(tree.portalTarget, globalThis.document.body)
  const modal = findNode(tree, node => node.props?.role === 'dialog')
  assert.equal(modal.props['aria-modal'], 'true')
  assert.ok(modal.props['aria-labelledby'])
  assert.ok(modal.props['aria-describedby'])
  assert.equal(globalThis.__dialogInstalls.at(-1).priority, 100)
  assert.equal(globalThis.__dialogInstalls.at(-1).initialFocus, nameInput)
  assert.equal(globalThis.__dialogInstalls.at(-1).restoreFocus, opener)

  const closeControls = findNodes(tree, node => node.props?.['aria-label'] === 'Close Save Recipe dialog')
  assert.equal(closeControls.length, 2)
  globalThis.__dialogAllowTopClose = false
  for (const control of closeControls) control.props.onClick()
  assert.equal(cancelCount, 0, 'covered backdrop and X cannot close Save')
  globalThis.__dialogAllowTopClose = true
  const backdrop = closeControls.find(node => node.props.tabIndex === -1)
  backdrop.props.onClick()
  assert.equal(cancelCount, 1)
  assert.equal(globalThis.__dialogTopCloseRequests.at(-1), dialog)

  const name = findNode(tree, node => node.props?.placeholder === 'e.g. Cinematic Film Look')
  const nameLabel = findNode(tree, node => node.type === 'label' && nodeText(node) === 'Name')
  assert.equal(nameLabel.props.htmlFor, name.props.id)
  name.props.onChange({ target: { value: '  My Recipe  ' } })
  beginRender()
  tree = SaveRecipeDialog(props)
  const description = findNode(tree, node => node.props?.placeholder === "When to use it, what it's good for…")
  const descriptionLabel = findNode(tree, node => node.type === 'label' && nodeText(node) === 'Description (optional)')
  assert.equal(descriptionLabel.props.htmlFor, description.props.id)
  description.props.onChange({ target: { value: '  Notes  ' } })
  beginRender()
  tree = SaveRecipeDialog(props)
  const submit = findNode(tree, node => node.type === 'button' && nodeText(node) === 'Save Recipe')
  await Promise.all([submit.props.onClick(), submit.props.onClick()])
  assert.deepEqual(saves, [['My Recipe', 'Notes', false]])
})

test('bundled Save dialog exposes loading, fences duplicate submit, and recovers visibly from failure', async () => {
  const SaveRecipeDialog = await loadDialogComponent(saveUrl, 'SaveRecipeDialog')
  const pending = deferred()
  resetDialogHarness([{ current: {} }, { current: {} }])
  const props = {
    onCancel: () => {},
    onSave: () => pending.promise,
    restoreFocusRef: { current: {} },
  }
  let tree = SaveRecipeDialog(props)
  findNode(tree, node => node.props?.placeholder === 'e.g. Cinematic Film Look')
    .props.onChange({ target: { value: 'Recipe' } })
  beginRender()
  tree = SaveRecipeDialog(props)
  const submit = findNode(tree, node => node.type === 'button' && nodeText(node) === 'Save Recipe')
  const first = submit.props.onClick()
  const duplicate = submit.props.onClick()
  beginRender()
  tree = SaveRecipeDialog(props)
  assert.ok(findNode(tree, node => node.type === 'button' && nodeText(node).includes('Saving…')).props.disabled)
  assert.ok(findNode(tree, node => node.type === 'button' && nodeText(node) === 'Cancel').props.disabled)
  for (const control of findNodes(tree, node => node.props?.['aria-label'] === 'Close Save Recipe dialog')) {
    assert.equal(control.props.disabled, true)
  }
  pending.resolve()
  await Promise.all([first, duplicate])

  resetDialogHarness([{ current: {} }, { current: {} }])
  const failedProps = {
    ...props,
    onSave: async () => { throw new Error('Recipe storage unavailable') },
  }
  tree = SaveRecipeDialog(failedProps)
  findNode(tree, node => node.props?.placeholder === 'e.g. Cinematic Film Look')
    .props.onChange({ target: { value: 'Recipe' } })
  beginRender()
  tree = SaveRecipeDialog(failedProps)
  await findNode(tree, node => node.type === 'button' && nodeText(node) === 'Save Recipe').props.onClick()
  beginRender()
  tree = SaveRecipeDialog(failedProps)
  assert.equal(nodeText(findNode(tree, node => node.props?.role === 'alert')), 'Recipe storage unavailable')
  assert.equal(findNode(tree, node => node.type === 'button' && nodeText(node) === 'Cancel').props.disabled, false)
})

test('bundled Retake dialog portals, captures exact active trigger, and preserves retake payload', async () => {
  class FakeButton {}
  globalThis.HTMLButtonElement = FakeButton
  const opener = new FakeButton()
  const dialog = { name: 'retake dialog' }
  const close = { name: 'retake close' }
  resetDialogHarness([{ current: dialog }, { current: close }, { current: null }])
  globalThis.document.activeElement = opener
  let loadOutputs = 0
  globalThis.__retakeStore = {
    retakeDialogOpen: true,
    retakeSourceFile: 'clip.mp4',
    closeRetakeDialog: () => {},
    activeWorkspace: 'workspace-a',
    loadOutputs: () => { loadOutputs += 1 },
    selectedModelPerMode: { video: 'video-model' },
    params: {
      model_type: 'fallback-model',
      activated_loras: ['detail-lora'],
      loras_multipliers: '0.75',
    },
    models: [],
  }
  const RetakeDialog = await loadDialogComponent(retakeUrl, 'RetakeDialog')
  let tree = RetakeDialog()
  assert.equal(tree.portalTarget, globalThis.document.body)
  assert.equal(globalThis.__dialogInstalls.at(-1).priority, 100)
  assert.equal(globalThis.__dialogInstalls.at(-1).initialFocus, close)
  assert.equal(globalThis.__dialogInstalls.at(-1).restoreFocus, opener)
  const modal = findNode(tree, node => node.props?.role === 'dialog')
  assert.equal(modal.props['aria-modal'], 'true')

  const prompt = findNode(tree, node => node.props?.placeholder === 'Describe the new content for the selected time range...')
  assert.equal(
    findNode(tree, node => node.type === 'label' && nodeText(node) === 'What should happen in this section?').props.htmlFor,
    prompt.props.id,
  )
  prompt.props.onChange({ target: { value: 'replacement action' } })
  globalThis.__dialogHookState[12] = true
  beginRender()
  tree = RetakeDialog()
  for (const [label, placeholder] of [
    ['Negative Prompt', 'What to avoid...'],
    ['Seed', undefined],
    ['Steps', undefined],
    ['Guidance', undefined],
  ]) {
    const labelNode = findNode(tree, node => node.type === 'label' && nodeText(node) === label)
    const control = findNode(tree, node => (
      node.props?.id === labelNode.props.htmlFor
      && (placeholder === undefined || node.props.placeholder === placeholder)
    ))
    assert.ok(control, `${label} has a programmatic control association`)
  }
  const submit = findNode(tree, node => node.type === 'button' && nodeText(node) === 'Retake')
  assert.equal(submit.props.disabled, false)
  await Promise.all([submit.props.onClick(), submit.props.onClick()])
  assert.deepEqual(globalThis.__retakePayloads, [{
    video_path: 'clip.mp4',
    start_time: 0,
    end_time: 5,
    prompt: 'replacement action',
    model_type: 'video-model',
    negative_prompt: '',
    seed: -1,
    guidance_scale: 1,
    num_inference_steps: 8,
    retake_engine: 'native',
    regenerate_audio: true,
    activated_loras: ['detail-lora'],
    loras_multipliers: '0.75',
    workspace: 'workspace-a',
  }])
  assert.equal(loadOutputs, 1)
  beginRender()
  tree = RetakeDialog()
  assert.equal(nodeText(findNode(tree, node => node.props?.role === 'status')), 'Retake queued for 17 frames.')
})

test('Retake loading, failure, and close-reopen lifecycle ignore every stale completion', async () => {
  class FakeButton {}
  globalThis.HTMLButtonElement = FakeButton
  const pending = deferred()
  resetDialogHarness([{ current: {} }, { current: {} }, { current: null }])
  globalThis.document.activeElement = new FakeButton()
  let closeCount = 0
  let loadOutputs = 0
  globalThis.__retakeStore = {
    retakeDialogOpen: true,
    retakeSourceFile: 'clip.mp4',
    closeRetakeDialog: () => {
      closeCount += 1
      globalThis.__retakeStore.retakeDialogOpen = false
    },
    activeWorkspace: 'workspace-a',
    loadOutputs: () => { loadOutputs += 1 },
    selectedModelPerMode: { video: 'video-model' },
    params: { model_type: 'fallback', activated_loras: [], loras_multipliers: '' },
    models: [],
  }
  globalThis.__retakeSubmit = () => pending.promise
  const RetakeDialog = await loadDialogComponent(retakeUrl, 'RetakeDialog')
  let tree = RetakeDialog()
  findNode(tree, node => node.props?.placeholder === 'Describe the new content for the selected time range...')
    .props.onChange({ target: { value: 'replacement' } })
  beginRender()
  tree = RetakeDialog()
  const submit = findNode(tree, node => node.type === 'button' && nodeText(node) === 'Retake')
  const first = submit.props.onClick()
  const duplicate = submit.props.onClick()
  assert.equal(globalThis.__retakePayloads.length, 1)
  beginRender()
  tree = RetakeDialog()
  assert.ok(findNode(tree, node => node.type === 'button' && nodeText(node) === 'Submitting...').props.disabled)

  const closeControls = findNodes(tree, node => node.props?.['aria-label'] === 'Close Retake dialog')
  for (const control of closeControls) {
    assert.equal(control.props.disabled, true)
    control.props.onClick()
  }
  assert.equal(closeCount, 0, 'all interactive dismissal is blocked consistently while submitting')
  globalThis.__retakeStore.closeRetakeDialog()
  beginRender()
  assert.equal(RetakeDialog(), null, 'programmatic owner close runs the closed lifecycle fence')
  assert.equal(closeCount, 1)
  globalThis.__retakeStore.retakeDialogOpen = true
  beginRender()
  tree = RetakeDialog()
  assert.ok(tree, 'a new Retake can open before the old request settles')
  pending.resolve({ retake_frames: 9 })
  await Promise.all([first, duplicate])
  assert.equal(closeCount, 1, 'old success cannot close the reopened dialog')
  assert.equal(loadOutputs, 0, 'old success cannot refresh or publish UI state')

  resetDialogHarness([{ current: {} }, { current: {} }, { current: null }])
  globalThis.document.activeElement = new FakeButton()
  globalThis.__retakeStore.retakeDialogOpen = true
  globalThis.__retakeSubmit = async () => { throw new Error('Retake service unavailable') }
  tree = RetakeDialog()
  findNode(tree, node => node.props?.placeholder === 'Describe the new content for the selected time range...')
    .props.onChange({ target: { value: 'replacement' } })
  beginRender()
  tree = RetakeDialog()
  await findNode(tree, node => node.type === 'button' && nodeText(node) === 'Retake').props.onClick()
  beginRender()
  tree = RetakeDialog()
  const alert = nodeText(findNode(tree, node => node.props?.role === 'alert'))
  assert.equal(alert, 'The retake could not be queued. Try again.')
  assert.doesNotMatch(alert, /service unavailable/i)
  assert.equal(findNode(tree, node => node.type === 'button' && nodeText(node) === 'Retake').props.disabled, false)
})

test('Retake upload reports bounded inline failure and preserves accessible retry and metadata flow', async t => {
  const RetakeControls = await loadDialogComponent(retakeControlsUrl, 'RetakeControls')
  const originalCreateObjectURL = URL.createObjectURL
  const originalRevokeObjectURL = URL.revokeObjectURL
  const revoked = []
  URL.createObjectURL = () => 'blob:retake-video'
  URL.revokeObjectURL = url => { revoked.push(url) }
  t.after(() => {
    URL.createObjectURL = originalCreateObjectURL
    URL.revokeObjectURL = originalRevokeObjectURL
  })

  const input = { clicks: 0, value: 'clip.mp4', click() { this.clicks += 1 } }
  resetDialogHarness([{ current: input }])
  const store = {
    editVideoFile: null,
    editVideoPath: '',
    editVideoUrl: '',
    editVideoDuration: 0,
    editStartTime: 0,
    editEndTime: 0,
    editRetakeStrength: 0.5,
    editRetakeEngine: 'native',
    editRegenerateAudio: true,
    setEditVideo() {},
    clearEditVideo() {},
  }
  globalThis.__retakeStore = store
  globalThis.__retakeUpload = async () => { throw new Error('/private/uploads/clip.mp4 failed') }

  let tree = RetakeControls()
  const upload = findNode(tree, node => node.type === 'button' && /Drop a video/.test(nodeText(node)))
  assert.equal(upload.props.type, 'button')
  assert.match(upload.props.className, /min-h-11/)
  assert.match(upload.props.className, /focus-visible:ring-2/)
  findNode(tree, node => node.type === 'input' && node.props?.type === 'file')
    .props.onChange({ target: { files: [{ name: 'clip.mp4', type: 'video/mp4' }] } })
  await flushPromises()
  beginRender()
  tree = RetakeControls()
  const uploadAlert = findNode(tree, node => node.props?.role === 'alert')
  assert.equal(nodeText(uploadAlert), 'The video could not be uploaded. Choose the file again.Choose file again')
  assert.doesNotMatch(nodeText(uploadAlert), /private|uploads|clip\.mp4 failed/i)
  const retry = findNode(uploadAlert, node => node.type === 'button' && nodeText(node) === 'Choose file again')
  assert.match(retry.props.className, /min-h-11/)
  retry.props.onClick()
  assert.equal(input.clicks, 1)
  assert.equal(input.value, '')

  const selected = []
  resetDialogHarness([{ current: input }])
  globalThis.__retakeStore = {
    ...store,
    setEditVideo(...args) {
      selected.push(args)
      Object.assign(globalThis.__retakeStore, {
        editVideoFile: args[0], editVideoPath: args[1], editVideoUrl: args[2],
        editVideoDuration: args[3], editEndTime: args[3],
      })
    },
  }
  const file = { name: 'clip.mp4', type: 'video/mp4' }
  tree = RetakeControls()
  findNode(tree, node => node.type === 'input' && node.props?.type === 'file')
    .props.onChange({ target: { files: [file] } })
  await flushPromises()
  globalThis.__retakeVideo.duration = 3.5
  globalThis.__retakeVideo.videoWidth = 1280
  globalThis.__retakeVideo.videoHeight = 720
  globalThis.__retakeVideo.onloadedmetadata()
  assert.deepEqual(selected, [[file, '/uploaded/video.mp4', 'blob:retake-video', 3.5, '1280x720']])
  beginRender()
  tree = RetakeControls()
  const remove = findNode(tree, node => node.props?.['aria-label'] === 'Remove retake video')
  assert.equal(remove.props.type, 'button')
  assert.match(remove.props.className, /min-h-11/)
  assert.match(remove.props.className, /min-w-11/)
  remove.props.onClick()
  assert.deepEqual(revoked, ['blob:retake-video'])
  const native = findNode(tree, node => node.type === 'button' && nodeText(node) === 'Native')
  const compatibility = findNode(tree, node => node.type === 'button' && nodeText(node) === 'Compatibility')
  assert.equal(native.props['aria-pressed'], true)
  assert.equal(compatibility.props['aria-pressed'], false)
  assert.match(native.props.className, /min-h-11/)
  assert.match(compatibility.props.className, /min-h-11/)
  const engineGroup = findNode(tree, node => node.props?.role === 'group' && node.props?.['aria-label'] === 'Retake engine')
  assert.ok(engineGroup)
})

test('Retake upload latest selection wins and unmounted completions stay inert', async t => {
  const RetakeControls = await loadDialogComponent(retakeControlsUrl, 'RetakeControls')
  const originalCreateObjectURL = URL.createObjectURL
  const originalRevokeObjectURL = URL.revokeObjectURL
  URL.createObjectURL = file => `blob:${file.name}`
  URL.revokeObjectURL = () => {}
  t.after(() => {
    URL.createObjectURL = originalCreateObjectURL
    URL.revokeObjectURL = originalRevokeObjectURL
  })
  const uploads = new Map()
  const selected = []
  resetDialogHarness([{ current: { value: '' } }])
  globalThis.__retakeStore = {
    editVideoFile: null, editVideoPath: '', editVideoUrl: '', editVideoDuration: 0,
    editStartTime: 0, editEndTime: 0, editRetakeStrength: 0.5,
    editRetakeEngine: 'native', editRegenerateAudio: true,
    setEditVideo(...args) { selected.push(args) }, clearEditVideo() {},
  }
  globalThis.__retakeUpload = file => {
    const pending = deferred()
    uploads.set(file.name, pending)
    return pending.promise
  }
  const firstFile = { name: 'first.mp4', type: 'video/mp4' }
  const secondFile = { name: 'second.mp4', type: 'video/mp4' }
  let tree = RetakeControls()
  let picker = findNode(tree, node => node.type === 'input' && node.props?.type === 'file')
  picker.props.onChange({ target: { files: [firstFile] } })
  beginRender()
  tree = RetakeControls()
  picker = findNode(tree, node => node.type === 'input' && node.props?.type === 'file')
  picker.props.onChange({ target: { files: [secondFile] } })
  uploads.get('second.mp4').resolve({ path: '/uploads/second.mp4' })
  await flushPromises()
  globalThis.__retakeVideo.duration = 4
  globalThis.__retakeVideo.videoWidth = 640
  globalThis.__retakeVideo.videoHeight = 480
  globalThis.__retakeVideo.onloadedmetadata()
  uploads.get('first.mp4').resolve({ path: '/uploads/first.mp4' })
  await flushPromises()
  assert.deepEqual(selected, [[secondFile, '/uploads/second.mp4', 'blob:second.mp4', 4, '640x480']])

  beginRender()
  tree = RetakeControls()
  picker = findNode(tree, node => node.type === 'input' && node.props?.type === 'file')
  const lateFile = { name: 'late.mp4', type: 'video/mp4' }
  picker.props.onChange({ target: { files: [lateFile] } })
  for (const cleanup of globalThis.__dialogCleanups) cleanup?.()
  uploads.get('late.mp4').resolve({ path: '/uploads/late.mp4' })
  await flushPromises()
  assert.equal(selected.length, 1)
})

test('MediaFeed lifecycle epoch prevents an old Save completion from closing a reopened dialog', async () => {
  const MediaFeedItem = await loadMediaFeedItemHarness()
  const pending = deferred()
  const trigger = { focus() {} }
  resetDialogHarness([
    { current: trigger }, { current: 0 }, { current: false }, { current: undefined },
    { current: null }, { current: null }, { current: null },
  ])
  globalThis.__dialogHookState[0] = { params: { prompt: 'test', model_type: 'model' } }
  globalThis.__dialogHookState[1] = true
  globalThis.__mediaStore = {
    setSelectedOutput() {}, loadSettingsFromOutput() {}, rerollGeneration() {},
    deleteSelectedOutput() {}, rejoinClipGroup() {}, toggleFavorite() {}, setStartImage() {},
    addImageRef() {}, setContinueVideo() {}, setParam() {}, openRetakeDialog() {},
    generationMode: 'video', workspaces: [], accessContext: {}, browsingUploads: false,
    models: [], gallerySelectionMode: false, selectedOutputKeys: [], toggleOutputSelection() {},
    saveRecipeFromOutput: () => pending.promise,
  }
  const props = {
    file: { name: 'clip.mp4', workspace: 'workspace-a', revision: '1', type: 'video', private: false },
    index: 0,
    isActive: false,
    onVisible() {},
    measurementEpoch: 0,
    onMeasured() {},
  }
  let tree = MediaFeedItem(props)
  const opener = findNode(tree, node => node.props?.['aria-label'] === 'Save as Recipe — reuse this look with one click')
  const retakeOpener = findNode(tree, node => node.props?.['aria-label'] === 'Retake — regenerate a time region')
  for (const control of [opener, retakeOpener]) {
    assert.match(control.props.className, /min-h-11/)
    assert.match(control.props.className, /min-w-11/)
  }
  opener.props.onClick({ stopPropagation() {} })
  beginRender()
  tree = MediaFeedItem(props)
  let saveDialog = findNode(tree, node => typeof node.type === 'function' && node.props?.restoreFocusRef)
  const oldCompletion = saveDialog.props.onSave('Recipe', 'Notes', false)
  saveDialog.props.onCancel()
  beginRender()
  tree = MediaFeedItem(props)
  findNode(tree, node => node.props?.['aria-label'] === 'Save as Recipe — reuse this look with one click')
    .props.onClick({ stopPropagation() {} })
  beginRender()
  tree = MediaFeedItem(props)
  assert.ok(findNode(tree, node => typeof node.type === 'function' && node.props?.restoreFocusRef))
  pending.resolve()
  await oldCompletion
  beginRender()
  tree = MediaFeedItem(props)
  assert.ok(findNode(tree, node => typeof node.type === 'function' && node.props?.restoreFocusRef), 'old completion leaves reopened Save mounted')
})

test('dialog source covers narrow landscape, breakpoint, safe-area, and touch geometry contracts', async () => {
  const [retake, save, mediaFeed] = await Promise.all([
    readFile(retakeUrl, 'utf8'),
    readFile(saveUrl, 'utf8'),
    readFile(mediaFeedUrl, 'utf8'),
  ])
  for (const source of [retake, save]) {
    assert.match(source, /createPortal\(/)
    assert.match(source, /role="dialog"/)
    assert.match(source, /aria-modal="true"/)
    assert.match(source, /aria-labelledby=/)
    assert.match(source, /aria-describedby=/)
    assert.match(source, /max-h-\[calc\(100vh-1\.5rem\)\]/)
    assert.match(source, /supports-\[height:100dvh\]:max-h-\[calc\(100dvh-1\.5rem\)\]/)
    for (const edge of ['top', 'right', 'bottom', 'left']) {
      assert.match(source, new RegExp(`safe-area-inset-${edge}`))
    }
    assert.doesNotMatch(source, /fixed inset-0[^\n]*\bp-4\b/)
  }
  assert.match(retake, /priority: 100/)
  assert.match(save, /priority: 100/)
  assert.match(retake, /z-\[100\]/)
  assert.match(save, /z-\[100\]/)
  assert.match(mediaFeed, /saveRecipeTriggerRef/)
  assert.match(mediaFeed, /restoreFocusRef=\{saveRecipeTriggerRef\}/)
  assert.match(mediaFeed, /event\.currentTarget\.focus\(\)[\s\S]*openRetakeDialog/)

  const utilities = await compile('@tailwind utilities;')
  const compiledUtilities = utilities.build([
    'max-h-[calc(100vh-1.5rem)]',
    'supports-[height:100dvh]:max-h-[calc(100dvh-1.5rem)]',
    'min-h-11',
    'min-w-11',
  ])
  assert.match(compiledUtilities, /max-height: calc\(100vh - 1\.5rem\)/)
  assert.match(compiledUtilities, /@supports \(height:\s*100dvh\)/)
  assert.match(compiledUtilities, /max-height: calc\(100dvh - 1\.5rem\)/)

  const SaveRecipeDialog = await loadDialogComponent(saveUrl, 'SaveRecipeDialog')
  resetDialogHarness([{ current: {} }, { current: {} }])
  const saveTree = SaveRecipeDialog({
    onCancel() {},
    async onSave() {},
    restoreFocusRef: { current: {} },
  })
  class FakeButton {}
  globalThis.HTMLButtonElement = FakeButton
  resetDialogHarness([{ current: {} }, { current: {} }, { current: null }])
  globalThis.document.activeElement = new FakeButton()
  globalThis.__retakeStore = {
    retakeDialogOpen: true,
    retakeSourceFile: 'clip.mp4',
    closeRetakeDialog() {}, activeWorkspace: 'workspace-a', loadOutputs() {},
    selectedModelPerMode: { video: 'video-model' },
    params: { model_type: 'fallback', activated_loras: [], loras_multipliers: '' },
    models: [],
  }
  const RetakeDialog = await loadDialogComponent(retakeUrl, 'RetakeDialog')
  globalThis.__dialogHookState[12] = true
  beginRender()
  const retakeTree = RetakeDialog()

  for (const tree of [saveTree, retakeTree]) {
    for (const edge of ['Top', 'Right', 'Bottom', 'Left']) {
      assert.match(tree.props.style[`padding${edge}`], new RegExp(`safe-area-inset-${edge.toLowerCase()}`))
    }
    const dialog = findNode(tree, node => node.props?.role === 'dialog')
    assert.match(dialog.props.className, /max-h-\[calc\(100vh-1\.5rem\)\]/)
    assert.match(dialog.props.className, /supports-\[height:100dvh\]:max-h-\[calc\(100dvh-1\.5rem\)\]/)
  }

  const saveTargets = findNodes(saveTree, node => (
    node.type === 'button' && node.props.tabIndex !== -1
    || node.type === 'input'
    || node.type === 'textarea'
  ))
  for (const target of saveTargets) assert.match(target.props.className, /min-h-(?:11|\[5\.5rem\])/, `Save target ${nodeText(target) || target.type}`)
  const retakeTargets = findNodes(retakeTree, node => (
    node.type === 'button' && node.props.tabIndex !== -1
    || (node.type === 'input' && node.props.type !== 'checkbox')
    || node.type === 'textarea'
  ))
  for (const target of retakeTargets) assert.match(target.props.className, /min-h-11/, `Retake target ${nodeText(target) || target.type}`)
  const checkbox = findNode(retakeTree, node => node.type === 'input' && node.props.type === 'checkbox')
  const checkboxLabel = findNode(retakeTree, node => node.type === 'label' && treeChildren(node).includes(checkbox))
  assert.match(checkboxLabel.props.className, /min-h-11/)

  const viewportMatrix = [
    [320, 320], [390, 320], [430, 320],
    [320, 767], [390, 767], [430, 767],
    [320, 768], [390, 768], [430, 768],
  ]
  for (const [width, height] of viewportMatrix) {
    const safeArea = height === 320
      ? { top: 0, right: 24, bottom: 21, left: 24 }
      : { top: 47, right: 0, bottom: 34, left: 0 }
    const padding = {
      top: Math.max(12, safeArea.top),
      right: Math.max(12, safeArea.right),
      bottom: Math.max(12, safeArea.bottom),
      left: Math.max(12, safeArea.left),
    }
    const availableWidth = width - padding.left - padding.right
    const availableHeight = height - padding.top - padding.bottom
    for (const [name, maxWidth] of [['Save', 420], ['Retake', 512]]) {
      const renderedWidth = Math.min(maxWidth, availableWidth)
      const renderedHeight = Math.min(height - 24, availableHeight)
      assert.ok(renderedWidth > 0 && renderedWidth <= availableWidth, `${name} ${width}x${height} respects inline safe-area width`)
      assert.ok(renderedHeight > 0 && renderedHeight <= availableHeight, `${name} ${width}x${height} respects compiled dvh and safe-area height`)
    }
  }
})
