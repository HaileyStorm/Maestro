import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { build } from 'esbuild'
import { fileURLToPath } from 'node:url'

const UI_ROOT = fileURLToPath(new URL('..', import.meta.url))

function asDataModule(source) {
  return 'data:text/javascript;base64,' + Buffer.from(source).toString('base64')
}

let guideModulePromise
function loadGuideModule() {
  if (guideModulePromise) return guideModulePromise
  guideModulePromise = build({
    stdin: {
      contents: "export { H3GuidePanel, H3_GUIDE_TARGET_FRAMES, isInteriorH3GuideFrame, resolveH3GuideModels, resolveH3GuideSelection } from './src/components/MainContent/H3GuidePanel.tsx'; export { submitH3GalleryStillGuide } from './src/api/client'",
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
      name: 'h3-guide-ui-test-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'h3-guide-test' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'h3-guide-test' }))
        bundle.onLoad({ filter: /.*/, namespace: 'h3-guide-test' }, args => {
          if (args.path === 'react') {
            return { contents: [
              'export const useState = initial => {',
              '  const states = globalThis.__h3GuideHookStates',
              '  const index = globalThis.__h3GuideHookIndex++',
              '  if (index >= states.length) states[index] = typeof initial === "function" ? initial() : initial',
              '  return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value }]',
              '}',
            ].join('\n') }
          }
          if (args.path === 'jsx-runtime') {
            return { contents: [
              'export const Fragment = Symbol.for("h3-guide-test-fragment")',
              'export const jsx = (type, props, key) => ({ type, key, props: props || {} })',
              'export const jsxs = jsx',
            ].join('\n') }
          }
          return { contents: '' }
        })
      },
    }],
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return guideModulePromise
}

function output(name, options = {}) {
  return {
    name,
    workspace: 'project-a',
    type: 'image',
    revision: 'revision-' + name,
    private: false,
    explicit: false,
    ...options,
  }
}

function key(file) {
  return file.workspace + '\0' + file.name
}

function model(modelType, options = {}) {
  return {
    model_type: modelType,
    name: modelType,
    family: 'video',
    architecture: 'h3',
    is_i2v: true,
    is_t2v: false,
    guidance_max_phases: 1,
    fps: 24,
    ...options,
  }
}

const GUIDE_MODELS = [
  model('minimax_h3'),
  model('minimax_h3_pinkcherry_fl2va'),
  model('minimax_h3_w4a8_fl2va'),
]

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
  globalThis.__h3GuideHookIndex = 0
  return Component(props)
}

function openPanel(Component, props) {
  const firstRender = renderPanel(Component, props)
  const openButton = flatten(firstRender).find(element => (
    element.type === 'button' && elementText(element) === 'Use still as guide'
  ))
  assert.ok(openButton)
  openButton.props.onClick()
  return renderPanel(Component, props)
}

function fillGuide(tree, { frame = '62', length = '141', prompt = 'Keep the figure centered.' } = {}) {
  findLabel(tree, 'Guide frame index, 0-based').props.onChange({ target: { value: frame } })
  findLabel(tree, 'Target clip length').props.onChange({ target: { value: length } })
  findLabel(tree, 'Describe the clip').props.onChange({ target: { value: prompt } })
}

function panelProps(options = {}) {
  return {
    workspace: 'project-a',
    still: output('guide.png', { private: true, explicit: true }),
    models: GUIDE_MODELS,
    enabledModels: new Set(GUIDE_MODELS.map(item => item.model_type)),
    modelsLoaded: true,
    isCurrentSelection: () => true,
    onQueued: async () => {},
    ...options,
  }
}

test('Guide appears only for one current same-project image with generation permission', async () => {
  const { resolveH3GuideSelection } = await loadGuideModule()
  const guide = output('guide.png', { private: true, explicit: true })
  assert.equal(resolveH3GuideSelection([guide], [key(guide)], 'project-a', true), guide)
  assert.equal(resolveH3GuideSelection([guide], [key(guide)], 'project-a', false), null)
  assert.equal(resolveH3GuideSelection([guide], [], 'project-a', true), null)
  assert.equal(resolveH3GuideSelection([guide], [key(guide), key(guide)], 'project-a', true), null)
  assert.equal(resolveH3GuideSelection([guide], [key(guide)], 'project-b', true), null)
  assert.equal(resolveH3GuideSelection([output('clip.mp4', { type: 'video' })], [key(output('clip.mp4', { type: 'video' }))], 'project-a', true), null)
  assert.equal(resolveH3GuideSelection([output('old.png', { revision: '' })], [key(output('old.png', { revision: '' }))], 'project-a', true), null)
})

test('Guide exposes only enabled, executable base FL2VA catalog model', async () => {
  const { resolveH3GuideModels } = await loadGuideModule()
  const catalog = [
    ...GUIDE_MODELS,
    model('minimax_h3_ref2va'),
    model('minimax_h3_pinkcherry_fl2va', { availability_status: 'legal_blocked' }),
    model('minimax_h3_w4a8_fl2va', { execution_allowed: false }),
  ]
  assert.deepEqual(
    resolveH3GuideModels(GUIDE_MODELS, new Set(GUIDE_MODELS.map(item => item.model_type)), true)
      .map(item => item.model_type),
    ['minimax_h3'],
  )
  assert.deepEqual(
    resolveH3GuideModels(catalog, new Set(GUIDE_MODELS.map(item => item.model_type)), true)
      .map(item => item.model_type),
    ['minimax_h3'],
  )
  assert.deepEqual(resolveH3GuideModels(GUIDE_MODELS, new Set(['minimax_h3']), false), [])
  assert.deepEqual(resolveH3GuideModels(GUIDE_MODELS, new Set(), true), [])
})

test('target lengths stay on the 17n+5 grid and guide frames are exact interior indices', async () => {
  const { H3_GUIDE_TARGET_FRAMES, isInteriorH3GuideFrame } = await loadGuideModule()
  assert.equal(H3_GUIDE_TARGET_FRAMES[0], 124)
  assert.equal(H3_GUIDE_TARGET_FRAMES.at(-1), 345)
  assert.ok(H3_GUIDE_TARGET_FRAMES.every(frames => (frames - 5) % 17 === 0))
  assert.equal(H3_GUIDE_TARGET_FRAMES.length, 14)
  assert.equal(isInteriorH3GuideFrame('1', 124), true)
  assert.equal(isInteriorH3GuideFrame('122', 124), true)
  assert.equal(isInteriorH3GuideFrame('', 124), false)
  assert.equal(isInteriorH3GuideFrame('0', 124), false)
  assert.equal(isInteriorH3GuideFrame('123', 124), false)
  assert.equal(isInteriorH3GuideFrame('1.5', 124), false)
  assert.equal(isInteriorH3GuideFrame('1e2', 124), false)
})

test('the panel requires an explicit frame and submits exact image revision, FL2VA model, prompt, and length', async () => {
  const { H3GuidePanel } = await loadGuideModule()
  const originalFetch = globalThis.fetch
  const requests = []
  let queued = 0
  globalThis.__h3GuideHookStates = []
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options })
    return Response.json({ job_id: 'guide-job', status: 'queued', held: false })
  }
  try {
    const props = panelProps({ onQueued: async () => { queued += 1 } })
    let tree = openPanel(H3GuidePanel, props)
    assert.equal(findLabel(tree, 'Guide frame index, 0-based').props.value, '')
    assert.equal(flatten(tree).find(element => element.type === 'button' && elementText(element) === 'Create guided clip').props.disabled, true)
    assert.match(elementText(tree), /one Gallery still as a visual guide for one interior frame/)
    assert.match(elementText(tree), /base FL2VA model only; PinkCherry and W4A8 variants are not included/)
    assert.match(elementText(tree), /does not use guide video, audio, or multiple guide images/)
    fillGuide(tree)
    const modelSelector = findLabel(tree, 'FL2VA model')
    assert.equal(flatten(modelSelector).filter(element => element.type === 'option').length, 1)
    tree = renderPanel(H3GuidePanel, props)
    await flatten(tree).find(element => element.type === 'form').props.onSubmit({ preventDefault() {} })

    assert.equal(queued, 1)
    assert.equal(requests.length, 1)
    assert.equal(requests[0].url, '/api/v1/h3/gallery-still-guide')
    assert.equal(requests[0].options.method, 'POST')
    assert.equal(requests[0].options.headers['Content-Type'], 'application/json')
    assert.deepEqual(JSON.parse(requests[0].options.body), {
      workspace: 'project-a',
      name: 'guide.png',
      revision: 'revision-guide.png',
      frame_index: 62,
      model_type: 'minimax_h3',
      prompt: 'Keep the figure centered.',
      settings: { video_length: 141 },
      private_output: true,
      explicit_output: true,
    })

    const publicStillProps = {
      ...props,
      still: output('public-guide.png', { private: false, explicit: false }),
    }
    tree = renderPanel(H3GuidePanel, publicStillProps)
    await flatten(tree).find(element => element.type === 'form').props.onSubmit({ preventDefault() {} })
    assert.equal(queued, 2)
    assert.equal(requests.length, 2)
    const publicRequest = JSON.parse(requests[1].options.body)
    assert.equal(publicRequest.name, 'public-guide.png')
    assert.equal(publicRequest.private_output, false)
    assert.equal(publicRequest.explicit_output, false)
  } finally {
    globalThis.fetch = originalFetch
    delete globalThis.__h3GuideHookStates
    delete globalThis.__h3GuideHookIndex
  }
})

test('the panel does not submit edge or fractional frame indices', async () => {
  const { H3GuidePanel } = await loadGuideModule()
  const originalFetch = globalThis.fetch
  let requests = 0
  globalThis.__h3GuideHookStates = []
  globalThis.fetch = async () => {
    requests += 1
    return Response.json({ job_id: 'guide-job', status: 'queued' })
  }
  try {
    const props = panelProps()
    let tree = openPanel(H3GuidePanel, props)
    fillGuide(tree, { frame: '0' })
    tree = renderPanel(H3GuidePanel, props)
    await flatten(tree).find(element => element.type === 'form').props.onSubmit({ preventDefault() {} })
    assert.equal(requests, 0)

    findLabel(tree, 'Guide frame index, 0-based').props.onChange({ target: { value: '1.5' } })
    tree = renderPanel(H3GuidePanel, props)
    await flatten(tree).find(element => element.type === 'form').props.onSubmit({ preventDefault() {} })
    assert.equal(requests, 0)

    findLabel(tree, 'Guide frame index, 0-based').props.onChange({ target: { value: '140' } })
    tree = renderPanel(H3GuidePanel, props)
    await flatten(tree).find(element => element.type === 'form').props.onSubmit({ preventDefault() {} })
    assert.equal(requests, 0)
  } finally {
    globalThis.fetch = originalFetch
    delete globalThis.__h3GuideHookStates
    delete globalThis.__h3GuideHookIndex
  }
})

test('accepted guide stays pending until the normal Queue reconnect callback finishes', async () => {
  const { H3GuidePanel } = await loadGuideModule()
  const originalFetch = globalThis.fetch
  let finishReconnect
  let reconnectStarted = false
  globalThis.__h3GuideHookStates = []
  globalThis.fetch = async () => Response.json({ job_id: 'guide-job', status: 'preparing', held: true })
  try {
    const props = panelProps({
      onQueued: () => {
        reconnectStarted = true
        return new Promise(resolve => { finishReconnect = resolve })
      },
    })
    let tree = openPanel(H3GuidePanel, props)
    fillGuide(tree)
    tree = renderPanel(H3GuidePanel, props)
    const submitted = flatten(tree).find(element => element.type === 'form').props.onSubmit({ preventDefault() {} })
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(reconnectStarted, true)
    tree = renderPanel(H3GuidePanel, props)
    assert.equal(flatten(tree).find(element => elementText(element) === 'Adding to Queue…')?.props.disabled, true)
    finishReconnect()
    await submitted
    tree = renderPanel(H3GuidePanel, props)
    assert.equal(flatten(tree).find(element => elementText(element) === 'Create guided clip')?.props.disabled, false)
  } finally {
    globalThis.fetch = originalFetch
    delete globalThis.__h3GuideHookStates
    delete globalThis.__h3GuideHookIndex
  }
})

test('a stale Gallery revision leaves Queue unchanged with useful guidance', async () => {
  const { H3GuidePanel } = await loadGuideModule()
  const originalFetch = globalThis.fetch
  let queued = 0
  globalThis.__h3GuideHookStates = []
  globalThis.fetch = async () => Response.json({ detail: 'source revision changed' }, { status: 409 })
  try {
    const props = panelProps({ onQueued: async () => { queued += 1 } })
    let tree = openPanel(H3GuidePanel, props)
    fillGuide(tree)
    tree = renderPanel(H3GuidePanel, props)
    await flatten(tree).find(element => element.type === 'form').props.onSubmit({ preventDefault() {} })
    tree = renderPanel(H3GuidePanel, props)
    assert.match(elementText(tree), /selected Gallery image changed/)
    assert.equal(queued, 0)
  } finally {
    globalThis.fetch = originalFetch
    delete globalThis.__h3GuideHookStates
    delete globalThis.__h3GuideHookIndex
  }
})

test('Guide API maps stale, pending, unsupported, missing-source, terms, and model-readiness errors', async () => {
  const { submitH3GalleryStillGuide } = await loadGuideModule()
  const originalFetch = globalThis.fetch
  const request = {
    workspace: 'project-a',
    name: 'guide.png',
    revision: 'revision-guide.png',
    frame_index: 62,
    model_type: 'minimax_h3',
    prompt: 'Keep the figure centered.',
    settings: { video_length: 124 },
  }
  const cases = [
    { status: 400, detail: 'frame_index must be interior', message: 'Choose an exact 0-based interior frame from 1 to 122.' },
    { status: 400, detail: 'model_type is not supported', message: 'Choose an enabled FL2VA model from the model list, then try again.' },
    { status: 409, detail: 'source revision changed', message: 'The selected Gallery image changed. Refresh Gallery, select the current image, and try again.' },
    { status: 409, detail: 'a guide job is already active', message: 'A guide request is already active for this project. Check Queue before starting another.' },
    { status: 404, detail: 'Output not found: guide.png', message: 'The selected Gallery image is no longer available. Refresh Gallery and select a current image.' },
    { status: 404, detail: 'Not Found', message: 'H3 Guide is not available in the running Maestro server. Update and restart Maestro, then refresh this page.' },
    { status: 451, detail: '', message: 'The selected FL2VA model is unavailable under its current license. Check model access in Settings, then retry.' },
    { status: 503, detail: '', message: 'The selected FL2VA model is not ready on this installation. Finish its setup or check model access, then retry.' },
  ]
  try {
    for (const item of cases) {
      globalThis.fetch = async () => Response.json(item.detail ? { detail: item.detail } : {}, { status: item.status })
      await assert.rejects(submitH3GalleryStillGuide(request), { message: item.message })
    }
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('Gallery integration reconnects accepted guide jobs before opening Queue', async () => {
  const source = await readFile(new URL('../src/components/MainContent/MainContent.tsx', import.meta.url), 'utf8')
  const start = source.indexOf('<H3GuidePanel')
  const end = source.indexOf('\n          />', start)
  assert.notEqual(start, -1)
  assert.notEqual(end, -1)
  const integration = source.slice(start, end)
  assert.match(integration, /await useStore\.getState\(\)\.reconnectJobs\(\)/)
  assert.match(integration, /requestQueueView\(\)/)
  assert.match(integration, /QUEUE_REFRESH_EVENT/)
  assert.match(source, /resolveH3GuideSelection\(outputs, selected, activeWorkspace, canGenerateBridge\)/)
})
