import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'
import { fileURLToPath } from 'node:url'

const UI_ROOT = fileURLToPath(new URL('..', import.meta.url))

function asDataModule(source) {
  return 'data:text/javascript;base64,' + Buffer.from(source).toString('base64')
}

let bridgeModulePromise
function loadBridgeModule() {
  if (bridgeModulePromise) return bridgeModulePromise
  bridgeModulePromise = build({
    stdin: {
      contents: "export { H3BridgePanel, H3_BRIDGE_GENERATED_FRAMES, resolveH3BridgeSelection } from './src/components/MainContent/H3BridgePanel.tsx'; export { submitH3Bridge } from './src/api/client'",
      resolveDir: UI_ROOT,
      loader: 'js',
    },
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'h3-bridge-ui-test-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'h3-bridge-test' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'h3-bridge-test' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide-react', namespace: 'h3-bridge-test' }))
        bundle.onLoad({ filter: /.*/, namespace: 'h3-bridge-test' }, args => {
          if (args.path === 'react') {
            return { contents: [
              'export const useState = initial => {',
              '  const states = globalThis.__h3BridgeHookStates',
              '  const index = globalThis.__h3BridgeHookIndex++',
              '  if (index >= states.length) states[index] = typeof initial === "function" ? initial() : initial',
              '  return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value }]',
              '}',
            ].join('\n') }
          }
          if (args.path === 'jsx-runtime') {
            return { contents: [
              'export const Fragment = Symbol.for("h3-bridge-test-fragment")',
              'export const jsx = (type, props, key) => ({ type, key, props: props || {} })',
              'export const jsxs = jsx',
            ].join('\n') }
          }
          return { contents: 'export const ArrowLeftRight = () => null; export const LoaderCircle = () => null' }
        })
      },
    }],
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return bridgeModulePromise
}

function output(name, options = {}) {
  return {
    name,
    workspace: 'project-a',
    type: 'video',
    revision: 'revision-' + name,
    private: false,
    ...options,
  }
}

function key(file) {
  return file.workspace + '\0' + file.name
}

function flatten(value, result = []) {
  if (Array.isArray(value)) {
    value.forEach(child => flatten(child, result))
    return result
  }
  if (!value || typeof value !== 'object') return result
  if ('type' in value && 'props' in value) {
    result.push(value)
    flatten(value.props.children, result)
  }
  return result
}

function elementText(value) {
  if (Array.isArray(value)) return value.map(elementText).join('')
  if (value == null || typeof value === 'boolean') return ''
  if (typeof value !== 'object') return String(value)
  return elementText(value.props?.children)
}

function findLabel(tree, label) {
  return flatten(tree).find(element => element.props['aria-label'] === label)
}

function renderPanel(Component, props) {
  globalThis.__h3BridgeHookIndex = 0
  return Component(props)
}

function openPanel(Component, props) {
  const firstRender = renderPanel(Component, props)
  const openButton = flatten(firstRender).find(element => (
    element.type === 'button' && elementText(element) === 'Bridge two videos'
  ))
  assert.ok(openButton)
  openButton.props.onClick()
  return renderPanel(Component, props)
}

function chooseClipsAndFill(tree, prompt = 'Carry the motion into the next shot.') {
  findLabel(tree, 'Clip A, first video').props.onChange({ target: { value: 'later.mp4' } })
  findLabel(tree, 'Clip B, next video').props.onChange({ target: { value: 'earlier.mp4' } })
  findLabel(tree, 'Describe the bridge').props.onChange({ target: { value: prompt } })
  findLabel(tree, 'Added bridge duration').props.onChange({ target: { value: '141' } })
  findLabel(tree, 'Variation').props.onChange({ target: { value: '2' } })
}

test('Bridge appears only for two current same-project videos with generation permission', async () => {
  const { resolveH3BridgeSelection } = await loadBridgeModule()
  const earlier = output('earlier.mp4', { private: true })
  const later = output('later.mp4')
  const outputs = [earlier, later]
  const selectedKeys = [key(later), key(earlier)]

  const candidates = resolveH3BridgeSelection(outputs, selectedKeys, 'project-a', true)
  assert.deepEqual(candidates?.map(file => file.name), ['later.mp4', 'earlier.mp4'])
  assert.equal(candidates?.[1].private, true)
  assert.equal(resolveH3BridgeSelection(outputs, selectedKeys, 'project-a', false), null)
  assert.equal(resolveH3BridgeSelection(outputs, selectedKeys.slice(0, 1), 'project-a', true), null)
  assert.equal(resolveH3BridgeSelection([...outputs, output('third.mp4')], [...selectedKeys, key(output('third.mp4'))], 'project-a', true), null)
  assert.equal(resolveH3BridgeSelection([earlier, output('later.mp4', { workspace: 'project-b' })], selectedKeys, 'project-a', true), null)
  assert.equal(resolveH3BridgeSelection([earlier, output('still.png', { type: 'image' })], [key(earlier), key(output('still.png', { type: 'image' }))], 'project-a', true), null)
  assert.equal(resolveH3BridgeSelection([earlier, output('later.mp4', { revision: '' })], selectedKeys, 'project-a', true), null)
})

test('duration choices cover the legal frame steps and default to 124 frames', async () => {
  const { H3_BRIDGE_GENERATED_FRAMES } = await loadBridgeModule()
  assert.equal(H3_BRIDGE_GENERATED_FRAMES.length, 15)
  assert.equal(H3_BRIDGE_GENERATED_FRAMES[0], 107)
  assert.equal(H3_BRIDGE_GENERATED_FRAMES[1], 124)
  assert.equal(H3_BRIDGE_GENERATED_FRAMES.at(-1), 345)
  assert.ok(H3_BRIDGE_GENERATED_FRAMES.every(frames => (
    frames >= 107 && frames <= 345 && (frames - 107) % 17 === 0
  )))
})

test('duration choices show added frames after trimming the hidden conditioning frames', async () => {
  const { H3BridgePanel } = await loadBridgeModule()
  globalThis.__h3BridgeHookStates = []
  try {
    const tree = openPanel(H3BridgePanel, {
      workspace: 'project-a',
      clips: [output('earlier.mp4'), output('later.mp4')],
      isCurrentSelection: () => true,
      onQueued: () => {},
    })
    const duration = findLabel(tree, 'Added bridge duration')
    assert.equal(duration.props.value, 124)
    const defaultOption = flatten(duration).find(element => (
      element.type === 'option' && element.props.value === 124
    ))
    assert.equal(elementText(defaultOption), 'About 3.3 seconds added · 80 frames (124 generated)')
    assert.ok(flatten(tree).some(element => (
      element.type === 'p' && elementText(element).includes('44 hidden conditioned frames (22 at each end) are trimmed')
    )))
  } finally {
    delete globalThis.__h3BridgeHookStates
    delete globalThis.__h3BridgeHookIndex
  }
})

test('bridge submission errors give useful guidance for media limits, missing sources, and unavailable H3', async () => {
  const { submitH3Bridge } = await loadBridgeModule()
  const originalFetch = globalThis.fetch
  const request = {
    workspace: 'project-a',
    model_type: 'minimax_h3_ref2va',
    clip_a: { name: 'earlier.mp4', revision: 'revision-earlier.mp4' },
    clip_b: { name: 'later.mp4', revision: 'revision-later.mp4' },
    prompt: 'Carry the motion into the next shot.',
    generated_frames: 124,
  }
  const sourceLengthMessage = 'Each selected video must be readable and contain at least 56 normalized frames at 24 fps (about 2.3 seconds). Refresh Gallery or choose longer videos, then retry.'
  const sourceSizeMessage = 'Each source video must be 8 GiB or smaller. Choose smaller videos, refresh Gallery, and try again.'
  const sourceDurationMessage = 'Each source video must be 30 minutes or shorter. Choose shorter videos, refresh Gallery, and try again.'
  const sourceDimensionsMessage = 'Each source video must be no larger than 4096 pixels per side and 12 megapixels. Choose lower-resolution videos, then retry.'
  const cases = [
    { status: 400, body: { detail: 'Bridge cuts need at least 56 source frames of motion at 24 fps' }, message: sourceLengthMessage },
    { status: 400, body: { detail: 'clip_a: media source could not be read' }, message: sourceLengthMessage },
    { status: 400, body: { detail: 'clip_b: media source is unavailable' }, message: sourceLengthMessage },
    { status: 400, body: { detail: 'clip_a: media probe failed' }, message: sourceLengthMessage },
    { status: 400, body: { detail: 'clip_a: media file exceeds the 8 GiB size limit' }, message: sourceSizeMessage },
    { status: 400, body: { detail: 'clip_b: source media exceeds the 30-minute duration limit' }, message: sourceDurationMessage },
    { status: 400, body: { detail: 'source dimensions exceed 4096 pixels per side or 12 megapixels' }, message: sourceDimensionsMessage },
    { status: 400, body: { detail: 'clip_a: source duration is unavailable or ambiguous' }, message: 'H3 Bridge could not verify a source video’s duration. Choose a video with readable duration metadata, then retry.' },
    { status: 413, body: { detail: 'media file exceeds the 8 GiB size limit' }, message: sourceSizeMessage },
    { status: 422, body: { detail: 'source dimensions exceed 4096 pixels per side or 12 megapixels' }, message: sourceDimensionsMessage },
    { status: 404, body: { detail: 'Output not found: earlier.mp4' }, message: 'A selected video is no longer available. Refresh Gallery, select two current videos, and try again.' },
    { status: 404, body: { detail: 'Not Found' }, message: 'H3 Bridge is unavailable in this version of Maestro. Update and restart Maestro, then try again.' },
    { status: 405, body: { detail: 'Method Not Allowed' }, message: 'H3 Bridge is unavailable in the running Maestro server. Restart Maestro and refresh this page, then try again.' },
    { status: 404, body: { detail: 'Model not found' }, message: 'H3 Bridge or a selected video is unavailable. Refresh Gallery and retry; if Bridge remains unavailable, update and restart Maestro.' },
    { status: 451, body: {}, message: 'MiniMax H3 is unavailable under the current license. Accepting model terms alone does not grant access; the required written MiniMax license must be in place.' },
    { status: 503, body: {}, message: 'MiniMax H3 is not ready on this installation. Check that the local model and required access terms are available, then retry.' },
  ]
  try {
    for (const item of cases) {
      globalThis.fetch = async () => Response.json(item.body, { status: item.status })
      await assert.rejects(submitH3Bridge(request), { message: item.message })
    }
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('the form requires explicit A/B order and queues the exact prompt and selected revisions', async () => {
  const { H3BridgePanel } = await loadBridgeModule()
  const clips = [output('earlier.mp4', { private: true }), output('later.mp4')]
  const prompt = '  Carry the motion into the next shot.\n'
  const requests = []
  let queued = 0
  const originalFetch = globalThis.fetch
  globalThis.__h3BridgeHookStates = []
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options })
    return Response.json({ job_id: 'bridge-job', status: 'queued' })
  }
  try {
    let tree = openPanel(H3BridgePanel, {
      workspace: 'project-a',
      clips,
      isCurrentSelection: () => true,
      onQueued: () => { queued += 1 },
    })
    assert.equal(findLabel(tree, 'Clip A, first video').props.value, '')
    assert.equal(findLabel(tree, 'Clip B, next video').props.value, '')
    chooseClipsAndFill(tree, prompt)
    tree = renderPanel(H3BridgePanel, {
      workspace: 'project-a',
      clips,
      isCurrentSelection: () => true,
      onQueued: () => { queued += 1 },
    })
    const form = flatten(tree).find(element => element.type === 'form')
    await form.props.onSubmit({ preventDefault() {} })

    assert.equal(queued, 1)
    assert.equal(requests.length, 1)
    assert.equal(requests[0].url, '/api/v1/h3/bridge')
    assert.equal(requests[0].options.method, 'POST')
    assert.equal(requests[0].options.headers['Content-Type'], 'application/json')
    assert.deepEqual(JSON.parse(requests[0].options.body), {
      workspace: 'project-a',
      model_type: 'minimax_h3_ref2va',
      clip_a: { name: 'later.mp4', revision: 'revision-later.mp4' },
      clip_b: { name: 'earlier.mp4', revision: 'revision-earlier.mp4' },
      prompt,
      generated_frames: 141,
      reroll_index: 2,
    })
    assert.equal(Object.hasOwn(JSON.parse(requests[0].options.body).clip_a, 'end_frame'), false)
    assert.equal(Object.hasOwn(JSON.parse(requests[0].options.body).clip_b, 'start_frame'), false)
  } finally {
    globalThis.fetch = originalFetch
    delete globalThis.__h3BridgeHookStates
    delete globalThis.__h3BridgeHookIndex
  }
})

test('a changed selection does not redirect the user after a delayed queue response', async () => {
  const { H3BridgePanel } = await loadBridgeModule()
  const clips = [output('earlier.mp4'), output('later.mp4')]
  const originalFetch = globalThis.fetch
  let resolveFetch
  const response = new Promise(resolve => { resolveFetch = resolve })
  let current = true
  let queued = 0
  globalThis.__h3BridgeHookStates = []
  globalThis.fetch = async () => response
  try {
    let tree = openPanel(H3BridgePanel, {
      workspace: 'project-a',
      clips,
      isCurrentSelection: () => current,
      onQueued: () => { queued += 1 },
    })
    chooseClipsAndFill(tree)
    tree = renderPanel(H3BridgePanel, {
      workspace: 'project-a',
      clips,
      isCurrentSelection: () => current,
      onQueued: () => { queued += 1 },
    })
    const submit = flatten(tree).find(element => element.type === 'form').props.onSubmit
    const pending = submit({ preventDefault() {} })
    current = false
    resolveFetch(Response.json({ job_id: 'bridge-job', status: 'queued' }))
    await pending
    assert.equal(queued, 0)
  } finally {
    globalThis.fetch = originalFetch
    delete globalThis.__h3BridgeHookStates
    delete globalThis.__h3BridgeHookIndex
  }
})

test('a stale source revision gives a useful error and leaves the Queue alone', async () => {
  const { H3BridgePanel } = await loadBridgeModule()
  const clips = [output('earlier.mp4'), output('later.mp4')]
  const originalFetch = globalThis.fetch
  let queued = 0
  globalThis.__h3BridgeHookStates = []
  globalThis.fetch = async () => new Response('{}', { status: 409 })
  try {
    let tree = openPanel(H3BridgePanel, {
      workspace: 'project-a',
      clips,
      isCurrentSelection: () => true,
      onQueued: () => { queued += 1 },
    })
    chooseClipsAndFill(tree)
    tree = renderPanel(H3BridgePanel, {
      workspace: 'project-a',
      clips,
      isCurrentSelection: () => true,
      onQueued: () => { queued += 1 },
    })
    await flatten(tree).find(element => element.type === 'form').props.onSubmit({ preventDefault() {} })
    tree = renderPanel(H3BridgePanel, {
      workspace: 'project-a',
      clips,
      isCurrentSelection: () => true,
      onQueued: () => { queued += 1 },
    })
    const alert = flatten(tree).find(element => element.props.role === 'alert')
    assert.equal(elementText(alert), 'One of the selected videos changed. Refresh Gallery, select them again, and retry.')
    assert.equal(queued, 0)
  } finally {
    globalThis.fetch = originalFetch
    delete globalThis.__h3BridgeHookStates
    delete globalThis.__h3BridgeHookIndex
  }
})
