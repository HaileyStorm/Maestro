import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'

import { build } from 'esbuild'

const componentUrl = new URL('../src/components/MainContent/MediaFeedItem.tsx', import.meta.url)

function createHooks(stateSeeds = {}) {
  const states = []
  const initialized = new Set()
  let cursor = 0
  return {
    begin() { cursor = 0 },
    useState(initial) {
      const index = cursor++
      if (!initialized.has(index)) {
        states[index] = Object.hasOwn(stateSeeds, index)
          ? stateSeeds[index]
          : typeof initial === 'function' ? initial() : initial
        initialized.add(index)
      }
      return [states[index], value => {
        states[index] = typeof value === 'function' ? value(states[index]) : value
      }]
    },
  }
}

async function loadHarness() {
  const icons = [
    'Play', 'Pencil', 'RefreshCw', 'Copy', 'Trash2', 'Check', 'Combine',
    'Loader2', 'Heart', 'ArrowLeftToLine', 'Download', 'FolderInput',
    'Scissors', 'FastForward', 'BookMarked', 'EyeOff', 'Share2', 'Link2Off',
  ]
  const modules = new Map([
    ['react', `
      export function useState(initial) { return globalThis.__outputShareHooks.useState(initial) }
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
      ${icons.map(name => `export const ${name} = icon`).join('\n')}
    `],
    ['../Recipes/SaveRecipeDialog', 'export function SaveRecipeDialog() { return null }'],
    ['../../stores/useStore', `
      export function useStore(selector) { return selector(globalThis.__outputShareStore) }
      useStore.getState = () => globalThis.__outputShareStore
      useStore.setState = () => {}
    `],
    ['../../api/client', `
      export const createOutputShare = (...args) => globalThis.__createOutputShare(...args)
      export const revokeOutputShare = (...args) => globalThis.__revokeOutputShare(...args)
      export const deleteOutputComponents = async () => ({ failed: [] })
      export const getUploadUrl = value => '/uploads/' + value
      export const fetchOutputMetadata = async () => null
      export const getFileUrl = (name, workspace) => '/files/' + workspace + '/' + name
      export const moveOutput = async () => {}
      export const uploadImage = async () => ({ path: '' })
    `],
    ['../../lib/format', 'export function formatGenerationDuration(value) { return String(value) }'],
    ['../../lib/modelDisplay', 'export function modelDisplayName() { return "" }'],
    ['../../lib/privatePreview', `
      export function privatePreviewIdentity(workspace, name, revision = '') {
        return workspace + '\\u0000' + name + '\\u0000' + revision
      }
      export function privatePreviewWasRevealed() { return false }
      export function revealPrivatePreview() {}
      export function hidePrivatePreview() {}
      export function subscribePrivatePreviewReveal() { return () => {} }
    `],
  ])
  const result = await build({
    entryPoints: [componentUrl.pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'mobile-output-sharing-mocks',
      setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => (
          modules.has(args.path) ? { path: args.path, namespace: 'output-share-test' } : undefined
        ))
        builder.onLoad({ filter: /.*/, namespace: 'output-share-test' }, args => ({
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

function nodeText(element) {
  if (Array.isArray(element)) return element.map(nodeText).join('')
  if (element === null || element === undefined || typeof element === 'boolean') return ''
  if (typeof element !== 'object') return String(element)
  return nodeText(element.props?.children)
}

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 30; attempt++) {
    if (predicate()) return
    await new Promise(resolve => setTimeout(resolve, 0))
  }
  assert.fail('Condition did not become true')
}

function createStore(accessContext = { cloudflare_enabled: false }) {
  const noop = () => {}
  return {
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
    generationMode: 'image',
    workspaces: [{ name: 'other-project' }],
    accessContext,
    browsingUploads: false,
    models: [],
    gallerySelectionMode: false,
    selectedOutputKeys: [],
    toggleOutputSelection: noop,
    saveRecipeFromOutput: noop,
    outputs: [],
    selectedOutput: 0,
    loadOutputs: async () => {},
  }
}

function output(overrides = {}) {
  return {
    name: 'final-shot.png',
    type: 'image',
    url: '/media/final-shot.png',
    favorite: false,
    linked_component_count: 0,
    artifact_class: 'final',
    revision: 'revision-7',
    workspace: 'film-project',
    private: false,
    explicit: false,
    ...overrides,
  }
}

function createRuntime(MediaFeedItem, file = output(), stateSeeds = {}) {
  const hooks = createHooks(stateSeeds)
  globalThis.__outputShareHooks = hooks
  const render = () => {
    hooks.begin()
    return materialize(MediaFeedItem({
      file,
      index: 0,
      isActive: true,
      onVisible() {},
      measurementEpoch: 0,
      onMeasured() {},
    }))
  }
  return { render }
}

function button(tree, label) {
  return findElements(tree, element => (
    element.type === 'button' && element.props?.['aria-label'] === label
  ))[0]
}

function statusText(tree) {
  const status = findElements(tree, element => element.props?.role === 'status')[0]
  return status ? nodeText(status) : ''
}

function installBrowser(t, { navigator, confirm = () => true, execCommand = () => true } = {}) {
  const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const copiedAreas = []
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: navigator ?? {},
  })
  globalThis.window = { confirm, location: { origin: 'http://192.168.0.12:8188' } }
  globalThis.document = {
    body: {
      appendChild() {},
      removeChild() {},
    },
    createElement(tag) {
      if (tag === 'textarea') {
        const area = { value: '', style: {}, select() {} }
        copiedAreas.push(area)
        return area
      }
      if (tag === 'a') return { click() {} }
      throw new Error(`Unexpected element ${tag}`)
    },
    execCommand,
  }
  t.after(() => {
    if (originalNavigator) Object.defineProperty(globalThis, 'navigator', originalNavigator)
    else delete globalThis.navigator
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    delete globalThis.__outputShareHooks
    delete globalThis.__outputShareStore
    delete globalThis.__createOutputShare
    delete globalThis.__revokeOutputShare
  })
  return { copiedAreas }
}

test('mobile output controls keep 44px touch targets while desktop compacts them', async t => {
  const MediaFeedItem = await loadHarness()
  globalThis.__outputShareStore = createStore()
  globalThis.__createOutputShare = async () => ({})
  globalThis.__revokeOutputShare = async () => 1
  installBrowser(t)
  const { render } = createRuntime(MediaFeedItem, output({ linked_component_count: 3 }))
  const tree = render()
  const labels = [
    'Download final-shot.png',
    'Share final-shot.png — create an output-only link, not project access',
    'Revoke any active output-only link for final-shot.png',
    'Move final-shot.png to another project',
    'Use final-shot.png as an input image',
    'Remove 3 related files for final-shot.png; keep the finished output',
    'Add final-shot.png to favorites',
    'Delete final-shot.png',
  ]
  for (const label of labels) {
    const control = button(tree, label)
    assert.ok(control, label)
    assert.match(control.props.className, /min-h-11/)
    assert.match(control.props.className, /min-w-11/)
    assert.match(control.props.className, /md:min-h-0/)
    assert.match(control.props.className, /md:min-w-0/)
  }
  assert.equal(button(tree, 'Add final-shot.png to favorites').props['aria-pressed'], false)
  assert.equal(button(tree, 'Move final-shot.png to another project').props['aria-haspopup'], 'menu')
})

test('metadata and video actions share the mobile target and naming contract', async t => {
  const MediaFeedItem = await loadHarness()
  globalThis.__outputShareStore = createStore()
  globalThis.__createOutputShare = async () => ({})
  globalThis.__revokeOutputShare = async () => 1
  installBrowser(t)
  const file = output({
    name: 'assembled-scene.mp4',
    type: 'video',
    url: '/media/assembled-scene.mp4',
  })
  const metadata = {
    params: {
      prompt: 'private prompt used only to reveal the copy control',
      multi_clip_info: { group_id: 'group-1', index: 0, total: 3 },
    },
  }
  const { render } = createRuntime(MediaFeedItem, file, { 0: metadata, 1: true })
  const tree = render()
  const labels = [
    'Save as Recipe — reuse this look with one click',
    'Load generation settings from assembled-scene.mp4',
    'Regenerate assembled-scene.mp4 with the same settings',
    'Retake — regenerate a time region',
    'Extend assembled-scene.mp4 with new content',
    'Rejoin all 3 clips for assembled-scene.mp4',
    'Copy prompt from assembled-scene.mp4',
    'Use the current frame from assembled-scene.mp4 as a reference image',
    'Download assembled-scene.mp4',
  ]
  for (const label of labels) {
    const control = button(tree, label)
    assert.ok(control, label)
    assert.match(control.props.className, /min-h-11/)
    assert.match(control.props.className, /min-w-11/)
    assert.match(control.props.className, /md:min-h-0/)
    assert.match(control.props.className, /md:min-w-0/)
  }
})

test('revocation remains discoverable after reload and reports an idempotent no-op truthfully', async t => {
  const MediaFeedItem = await loadHarness()
  const revokes = []
  globalThis.__outputShareStore = createStore()
  globalThis.__createOutputShare = async () => ({})
  globalThis.__revokeOutputShare = async (...args) => { revokes.push(args); return 0 }
  installBrowser(t)
  const { render } = createRuntime(MediaFeedItem)
  const revoke = button(render(), 'Revoke any active output-only link for final-shot.png')
  assert.ok(revoke, 'revoke is visible without component-held share state')
  revoke.props.onClick({ stopPropagation() {} })
  await waitFor(() => revokes.length === 1)
  assert.deepEqual(revokes, [['final-shot.png', 'film-project']])
  assert.equal(
    statusText(render()),
    'No active output link was found. Project access is unchanged.',
  )
})

test('native sharing sends only the exact output capability and preserves confirmed revocation', async t => {
  const MediaFeedItem = await loadHarness()
  const creates = []
  const revokes = []
  const shared = []
  const confirmations = []
  let clipboardWrites = 0
  globalThis.__outputShareStore = createStore()
  globalThis.__createOutputShare = async (...args) => {
    creates.push(args)
    return {
      public_url: 'https://share.maestro.example/output/token-123',
      share_path: '/output/token-123',
      configured_public_origin: true,
    }
  }
  globalThis.__revokeOutputShare = async (...args) => { revokes.push(args); return 1 }
  installBrowser(t, {
    navigator: {
      async share(data) { shared.push(data) },
      clipboard: { async writeText() { clipboardWrites += 1 } },
    },
    confirm(message) { confirmations.push(message); return true },
  })
  const { render } = createRuntime(MediaFeedItem, output({
    private: true,
    prompt: 'secret prompt bytes must never leave',
  }))
  button(render(), 'Share final-shot.png — create an output-only link, not project access').props.onClick({ stopPropagation() {} })
  await waitFor(() => shared.length === 1)
  assert.deepEqual(creates, [['final-shot.png', 'film-project', 'revision-7']])
  assert.deepEqual(shared[0], {
    title: 'final-shot.png',
    text: 'Read-only link to this output. It does not grant project access.',
    url: 'https://share.maestro.example/output/token-123',
  })
  assert.equal(clipboardWrites, 0)
  assert.doesNotMatch(JSON.stringify(shared[0]), /secret prompt bytes/)
  let tree = render()
  assert.equal(
    statusText(tree),
    'Public read-only output link shared. It does not grant project access.',
  )
  const revoke = button(tree, 'Revoke any active output-only link for final-shot.png')
  assert.ok(revoke)
  revoke.props.onClick({ stopPropagation() {} })
  await waitFor(() => revokes.length === 1)
  assert.deepEqual(revokes, [['final-shot.png', 'film-project']])
  assert.match(confirmations[0], /view only this output/)
  assert.match(confirmations[1], /Project access will not change/)
  tree = render()
  assert.equal(statusText(tree), 'Output link revoked. Project access is unchanged.')
})

test('clipboard fallback labels local-origin limits and survives native-share failure', async t => {
  const MediaFeedItem = await loadHarness()
  let nativeAttempts = 0
  let clipboardAttempts = 0
  globalThis.__outputShareStore = createStore({ cloudflare_enabled: true })
  globalThis.__createOutputShare = async () => ({
    public_url: null,
    share_path: '/output/token-local',
    configured_public_origin: false,
  })
  globalThis.__revokeOutputShare = async () => 1
  const browser = installBrowser(t, {
    navigator: {
      async share() { nativeAttempts += 1; throw new Error('native sheet unavailable') },
      clipboard: { async writeText() { clipboardAttempts += 1; throw new Error('insecure context') } },
    },
  })
  const { render } = createRuntime(MediaFeedItem)
  button(render(), 'Share final-shot.png — create an output-only link, not project access').props.onClick({ stopPropagation() {} })
  await waitFor(() => statusText(render()).includes('Local-address output link copied'))
  assert.equal(nativeAttempts, 1)
  assert.equal(clipboardAttempts, 1)
  assert.equal(browser.copiedAreas[0].value, 'http://192.168.0.12:8188/output/token-local')
  assert.equal(
    statusText(render()),
    "Local-address output link copied. The same path works through Maestro's Cloudflare address, but this link itself may not open off your network. It does not grant project access.",
  )
})

test('failed copy reports an accessible error while leaving the revocable capability visible', async t => {
  const MediaFeedItem = await loadHarness()
  globalThis.__outputShareStore = createStore()
  globalThis.__createOutputShare = async () => ({
    public_url: null,
    share_path: '/output/token-active',
    configured_public_origin: false,
  })
  globalThis.__revokeOutputShare = async () => 1
  installBrowser(t, { execCommand: () => false })
  const { render } = createRuntime(MediaFeedItem)
  button(render(), 'Share final-shot.png — create an output-only link, not project access').props.onClick({ stopPropagation() {} })
  await waitFor(() => statusText(render()) === 'Clipboard is unavailable in this browser')
  const tree = render()
  const status = findElements(tree, element => element.props?.role === 'status')[0]
  assert.equal(status.props['aria-live'], 'polite')
  assert.ok(button(tree, 'Revoke any active output-only link for final-shot.png'))
})

test('revocation cancellation leaves the capability active and makes no request', async t => {
  const MediaFeedItem = await loadHarness()
  let revokes = 0
  let confirmations = 0
  globalThis.__outputShareStore = createStore()
  globalThis.__createOutputShare = async () => ({
    public_url: 'https://share.maestro.example/output/token-keep',
    share_path: '/output/token-keep',
    configured_public_origin: true,
  })
  globalThis.__revokeOutputShare = async () => { revokes += 1; return 1 }
  installBrowser(t, {
    navigator: { clipboard: { async writeText() {} } },
    confirm() { confirmations += 1; return confirmations === 1 },
  })
  const { render } = createRuntime(MediaFeedItem, output({ private: true }))
  button(render(), 'Share final-shot.png — create an output-only link, not project access').props.onClick({ stopPropagation() {} })
  await waitFor(() => Boolean(button(render(), 'Revoke any active output-only link for final-shot.png')))
  button(render(), 'Revoke any active output-only link for final-shot.png').props.onClick({ stopPropagation() {} })
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(revokes, 0)
  assert.ok(button(render(), 'Revoke any active output-only link for final-shot.png'))
})
