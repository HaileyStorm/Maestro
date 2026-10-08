import assert from 'node:assert/strict'
import test, { after, beforeEach } from 'node:test'
import { fileURLToPath } from 'node:url'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'
import { requestQueueView, subscribeQueueView } from '../src/lib/mainViewNavigation.ts'
import { createServer } from 'vite'

import { addEditorAudio, addEditorImage, addEditorTake, switchEditorTake, appendEditorClip, EditorExportSubmissionError, ProjectAssetRequestError, exportEditorProject, isBackendJobId, getEditorRetakes, dismissEditorRetake, openOutputInEditor, saveEditorProject } from '../src/api/client.ts'

// Expose the component's actual draft transformations only in this test loader.
// Production exports remain the component, and no duplicate implementation is tested.
const server = await createServer({
  configFile: false,
  root: fileURLToPath(new URL('../', import.meta.url)),
  appType: 'custom', logLevel: 'silent',
  server: { middlewareMode: true, watch: null },
  plugins: [{
    name: 'editor-test-transforms',
    transform(code, id) {
      if (id.endsWith('/src/editor/EditorWorkspace.tsx')) {
        return `${code}\nexport { changeTrim, moveClip, removeClip, availableVideos, addText, changeText, textLayers, renderedDuration, availableAudio, audioLayer, changeAudio, audioGain, imageLayers, changeImage, availableImages, imageLayout, retakeSelectedCut, prepareRetakeReview, readRetakeReview, returnRetakeResult, confirmEditorExportReceipt, editorExportKey, readEditorExport, writeEditorExport, useStore };`
      }
    },
  }],
})
const { changeTrim, moveClip, removeClip, availableVideos, addText, changeText, textLayers, renderedDuration, availableAudio, audioLayer, changeAudio, audioGain, imageLayers, changeImage, availableImages, imageLayout, retakeSelectedCut, prepareRetakeReview, readRetakeReview, returnRetakeResult, confirmEditorExportReceipt, editorExportKey, readEditorExport, writeEditorExport, useStore: editorTestStore } = await server.ssrLoadModule('/src/editor/EditorWorkspace.tsx')
after(() => server.close())
beforeEach(() => {
  const entries = new Map()
  globalThis.sessionStorage = {
    getItem: key => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value),
    removeItem: key => entries.delete(key),
  }
  editorTestStore.setState({ accountContext: { enabled: false } })
})

function sequenceProject() {
  return {
    id: 'first-source-draft', workspace: 'scene', revision: 5, name: 'Sequence',
    canvas: { width: 1280, height: 720, fps: 30, background: '#000000' },
    assets: Object.fromEntries(['a', 'b', 'c'].map((id, index) => [id, {
      id, name: `${id}.mp4`, type: 'video', origin: 'output', workspace: 'scene',
      output_id: `${id}.mp4`, output_revision: `sha256:${id}`, private: true,
      duration: 10 + index, width: 1280, height: 720, fps: 30, has_audio: true,
    }])),
    tracks: [{ id: 'video-main', name: 'Main video', type: 'video', items: [
      { id: 'clip-a', asset_id: 'a', start: 0, source_in: 1, duration: 3, speed: 1 },
      { id: 'clip-b', asset_id: 'b', start: 3, source_in: 2, duration: 4, speed: 1 },
      { id: 'clip-c', asset_id: 'c', start: 7, source_in: 0, duration: 5, speed: 1 },
    ] }, { id: 'retained-track', name: 'Retained', type: 'text', items: [] }],
  }
}

test('clip removal retains remaining trims, opening metadata and absolute overlays, and keeps one clip', () => {
  const original = sequenceProject()
  original.opening_source = { output_id: 'a.mp4', output_revision: 'sha256:a' }
  original.tracks.push({ id: 'images-main', type: 'video', items: [{ id: 'image', asset_id: 'image', start: 8, duration: 3 }] })
  original.assets.image = { id: 'image', output_id: 'still.png' }
  const removed = removeClip(original, 'clip-a')
  assert.deepEqual(removed.tracks[0].items.map(item => [item.id, item.start, item.source_in, item.duration]), [['clip-b', 0, 2, 4], ['clip-c', 4, 0, 5]])
  assert.equal(removed.opening_source, original.opening_source)
  assert.equal(removed.canvas, original.canvas)
  assert.equal(removed.tracks.at(-1), original.tracks.at(-1))
  assert.equal(removed.assets.a, undefined)
  assert.equal(removed.assets.image, original.assets.image)
  assert.equal(original.tracks[0].items.length, 3)
  assert.equal(availableVideos(removed, [{ workspace: 'scene', name:'a.mp4', type:'video', revision:'new-a' }]).length, 1)
  const one = removeClip(removed, 'clip-c')
  assert.equal(removeClip(one, 'clip-b'), one)
  assert.equal(removeClip(original, 'unknown'), original)
})

test('text CRUD retains absolute times and source identity through trim/reorder', () => {
  const original = sequenceProject()
  const titled = addText(original, 'text-one', 2, 4)
  const edited = changeText(titled, 'text-one', { text: 'Literal [v]; fiction', position: 'center' })
  assert.deepEqual(textLayers(edited).map(item => [item.start, item.duration, item.text]), [[2, 4, 'Literal [v]; fiction']])
  const reordered = moveClip(changeTrim(edited, 'clip-b', 3, 5), 'clip-c', -1)
  assert.deepEqual(textLayers(reordered), textLayers(edited))
  assert.equal(reordered.assets, original.assets)
  assert.equal(reordered.canvas, original.canvas)
  assert.deepEqual(textLayers(changeText(reordered, 'text-one', null)), [])
  assert.equal(textLayers(original).length, 0)
})

test('sequence title clock matches frame rounding and enforces the eight-layer limit', () => {
  let project = sequenceProject()
  project.canvas.fps = 24
  project.tracks[0].items.forEach(item => { item.duration = 0.55 })
  assert.equal(renderedDuration(project), 39 / 24)
  for (let index = 0; index < 8; index++) project = addText(project, `text-${index}`, 0, 1)
  assert.equal(addText(project, 'overflow', 0, 1), project)
})

test('sequence duration matches encoder decimals at frame and decimal ties', () => {
  for (const [duration, fps, frames] of [
    [2 / 3, 3.75, 3],
    [1.0048828124999998, 3.482993195545877, 3],
    [1.0048828125, 3.482993195545877, 3],
    [1.0048828125000002, 3.482993195545877, 4],
  ]) {
    const project = sequenceProject()
    project.canvas.fps = fps
    project.tracks[0].items = project.tracks[0].items.slice(0, 2)
    project.tracks[0].items.forEach(item => { item.duration = duration })
    assert.equal(renderedDuration(project), 2 * frames / fps)
    // Preserve the separate single-cut duration contract.
    project.tracks[0].items.pop()
    assert.equal(renderedDuration(project), duration)
  }
})

test('trim applies to the selected source and shifts only later sequence starts', () => {
  const original = sequenceProject()
  const next = changeTrim(original, 'clip-b', 3, 5)
  assert.deepEqual(next.tracks[0].items.map(item => [item.id, item.start, item.source_in, item.duration]), [
    ['clip-a', 0, 1, 3], ['clip-b', 3, 3, 2], ['clip-c', 5, 0, 5],
  ])
  assert.equal(next.assets, original.assets)
  assert.equal(next.canvas, original.canvas)
  assert.equal(next.tracks[1], original.tracks[1])
  assert.equal(next.id, original.id)
  assert.equal(next.revision, original.revision)
  assert.equal(original.tracks[0].items[1].duration, 4)
})

test('selected trim clamps to its own source duration and retains a nonempty cut', () => {
  const next = changeTrim(sequenceProject(), 'clip-b', 30, 50)
  const clip = next.tracks[0].items[1]
  assert.equal(clip.source_in, 10.9)
  assert.ok(Math.abs(clip.duration - 0.1) < 1e-9)
  assert.equal(clip.source_in + clip.duration, 11)
  const original = sequenceProject()
  assert.equal(changeTrim(original, 'missing-clip', 0, 2), original)
})

test('keyboard reorder retains exact clip and asset identities and recalculates sequential starts', () => {
  const original = sequenceProject()
  const earlier = moveClip(original, 'clip-c', -1)
  assert.deepEqual(earlier.tracks[0].items.map(item => [item.id, item.asset_id, item.start, item.source_in, item.duration]), [
    ['clip-a', 'a', 0, 1, 3], ['clip-c', 'c', 3, 0, 5], ['clip-b', 'b', 8, 2, 4],
  ])
  assert.equal(earlier.assets, original.assets)
  assert.equal(earlier.canvas, original.canvas)
  assert.equal(earlier.tracks[1], original.tracks[1])
  assert.equal(earlier.id, original.id)
  assert.equal(earlier.revision, original.revision)
  assert.deepEqual(moveClip(earlier, 'clip-c', 1), original)
  assert.equal(moveClip(original, 'clip-a', -1), original)
  assert.equal(moveClip(original, 'clip-c', 1), original)
})

test('append choices include current shared sources with listing tokens and retain privacy and ownership checks', () => {
  const outputs = [
    { name: 'a.mp4', workspace: 'scene', type: 'video', revision: '18fe-1234.0-0', private: true },
    { name: 'private-adult.mp4', workspace: 'scene', type: 'video', revision: 'current-d', private: true, explicit: true },
    { name: 'foreign.mp4', workspace: 'another scene', type: 'video', revision: 'current-e' },
    { name: 'unpinned.mp4', workspace: 'scene', type: 'video', revision: '' },
    { name: 'image.png', workspace: 'scene', type: 'image', revision: 'current-f' },
  ]
  assert.deepEqual(availableVideos(sequenceProject(), outputs), [outputs[0], outputs[1]])
  assert.deepEqual(availableVideos(sequenceProject(), [{ ...outputs[0], private: false }]), [])
  const ambiguous = sequenceProject()
  ambiguous.assets.separate = { ...ambiguous.assets.a, id: 'separate' }
  assert.deepEqual(availableVideos(ambiguous, [outputs[0]]), [])
})

test('shared source trims and take states stay independent through reorder and last-reference removal', () => {
  const original = sequenceProject()
  const first = original.tracks[0].items[0]
  first.take_asset_ids = ['a']
  first.take_states = { a: { source_in: 1, speed: 1 } }
  original.tracks[0].items[2] = { ...first, id: 'clip-a-second', start: 7, source_in: 6, duration: 2,
    take_states: { a: { source_in: 6, speed: 1 } } }
  delete original.assets.c
  const trimmed = changeTrim(original, 'clip-a-second', 7, 9)
  assert.deepEqual(trimmed.tracks[0].items[0], first)
  assert.deepEqual(trimmed.tracks[0].items[0].take_states, { a: { source_in: 1, speed: 1 } })
  assert.deepEqual(trimmed.tracks[0].items[2].take_states, { a: { source_in: 7, speed: 1 } })
  assert.deepEqual(original.tracks[0].items[2].take_states, { a: { source_in: 6, speed: 1 } })
  const moved = moveClip(trimmed, 'clip-a-second', -1)
  assert.deepEqual(moved.tracks[0].items.map(item => [item.id, item.asset_id, item.start]), [
    ['clip-a', 'a', 0], ['clip-a-second', 'a', 3], ['clip-b', 'b', 5],
  ])
  const remaining = removeClip(moved, 'clip-a')
  assert.equal(remaining.assets.a, original.assets.a)
  assert.equal(remaining.tracks[0].items[0].source_in, 7)
  assert.equal(remaining.tracks[1], original.tracks[1])
  const last = removeClip(remaining, 'clip-a-second')
  assert.equal(last.assets.a, undefined)
  assert.deepEqual(last.tracks[0].items.map(item => item.id), ['clip-b'])
  assert.equal(original.assets.a.duration, 10)
})

test('Editor export job IDs remain usable in Queue and job logs', () => {
  assert.equal(isBackendJobId('a1b2c3d4e5f6471889abcdef01234567'), true)
  assert.equal(isBackendJobId('faceb00c'), true)
  assert.equal(isBackendJobId('a1b2c3d4e5f6471889abcdef01234567/other'), false)
})

test('Editor requests keep the project, source revision and autosave revision together', async () => {
  const previous = globalThis.fetch
  const calls = []
  const project = {
    id: 'cut-1', workspace: 'scene a', revision: 3, name: 'Cut',
    canvas: { width: 1920, height: 1080, fps: 30, background: '#000000' },
    assets: {}, tracks: [],
  }
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init, body: JSON.parse(init.body) })
    return { ok: true, json: async () => ({ project }) }
  }
  try {
    assert.equal(await openOutputInEditor('scene a', 'clip #1.mp4', 'sha256:abc'), project)
    assert.equal(await saveEditorProject('scene a', project), project)
    assert.match(calls[0].url, /\/projects\/scene%20a\/editor\/projects$/)
    assert.deepEqual(calls[0].body, { output_name: 'clip #1.mp4', output_revision: 'sha256:abc' })
    assert.equal(calls[0].init.method, 'POST')
    assert.match(calls[1].url, /\/projects\/scene%20a\/editor\/projects\/cut-1$/)
    assert.equal(calls[1].body.expected_revision, 3)
    assert.equal(calls[1].init.method, 'PUT')
  } finally {
    globalThis.fetch = previous
  }
})

test('Editor save reports stale revisions as a safe, actionable conflict', async () => {
  const previous = globalThis.fetch
  globalThis.fetch = async () => ({ ok: false, status: 409 })
  try {
    await assert.rejects(
      saveEditorProject('scene', { id: 'cut', revision: 2 }),
      error => error.status === 409 && error.message.includes('reopen it'),
    )
  } finally {
    globalThis.fetch = previous
  }
})

test('Editor append pins the saved draft and exact Gallery output revision', async () => {
  const previous = globalThis.fetch
  const calls = []
  const project = sequenceProject()
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init })
    return { ok: true, json: async () => ({ project }) }
  }
  try {
    assert.equal(await appendEditorClip('scene a', { ...project, id: 'cut #1' }, 'next #2.mp4', 'gallery-stat-v2'), project)
    assert.match(calls[0].url, /\/projects\/scene%20a\/editor\/projects\/cut%20%231\/clips$/)
    assert.equal(calls[0].init.method, 'POST')
    assert.deepEqual(JSON.parse(calls[0].init.body), {
      expected_revision: 5, output_name: 'next #2.mp4', output_revision: 'gallery-stat-v2',
    })
  } finally {
    globalThis.fetch = previous
  }
})

test('Editor append surfaces stale draft conflicts and the bounded clip limit', async () => {
  const previous = globalThis.fetch
  try {
    globalThis.fetch = async () => ({ ok: false, status: 409 })
    await assert.rejects(appendEditorClip('scene', sequenceProject(), 'd.mp4', 'current-d'),
      error => error.status === 409 && error.message.includes('reopen it'))
    globalThis.fetch = async () => ({ ok: false, status: 422 })
    await assert.rejects(appendEditorClip('scene', sequenceProject(), 'd.mp4', 'current-d'),
      error => error.status === 422 && error.message.includes('up to 8 clips'))
  } finally {
    globalThis.fetch = previous
  }
})

test('Editor export submits only the saved project revision to its project route', async () => {
  const previous = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init })
    return { ok: true, json: async () => ({ job_id: 'e'.repeat(32), status: 'queued' }) }
  }
  try {
    assert.deepEqual(await exportEditorProject('scene a', { id: 'cut #1', revision: 4 }), {
      job_id: 'e'.repeat(32), status: 'queued',
    })
    assert.match(calls[0].url, /\/projects\/scene%20a\/editor\/projects\/cut%20%231\/exports$/)
    assert.equal(calls[0].init.method, 'POST')
    assert.deepEqual(JSON.parse(calls[0].init.body), { expected_revision: 4 })
  } finally {
    globalThis.fetch = previous
  }
})

test('Editor export reports a changed draft as an actionable conflict', async () => {
  const previous = globalThis.fetch
  globalThis.fetch = async () => ({ ok: false, status: 409 })
  try {
    await assert.rejects(
      exportEditorProject('scene', { id: 'cut', revision: 2 }),
      error => error.status === 409 && error.message.includes('reopen the video'),
    )
  } finally {
    globalThis.fetch = previous
  }
})

async function editorExportHandlers(state) {
  const source = await readFile(new URL('../src/editor/EditorWorkspace.tsx', import.meta.url), 'utf8')
  const ast = ts.createSourceFile('EditorWorkspace.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const declarations = new Map()
  const visit = node => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)
      && ['handleExport', 'refreshExportQueue', 'restoreExport', 'handleExportAnotherCopy'].includes(node.name.text)) declarations.set(node.name.text, node.getText(ast))
    ts.forEachChild(node, visit)
  }
  visit(ast)
  assert.equal(declarations.size, 4)
  const code = ts.transpileModule(`
    const { source, project, scope, editVersion, saving, exporting, appending, acceptedExport, queueConfirmation, isCurrent, useStore, exportEditorProject, EditorExportSubmissionError, ProjectAssetRequestError, confirmEditorExportReceipt, editorExportKey, readEditorExport, writeEditorExport } = context
    const useCallback = callback => callback
    const resetExport = () => { setExportState('idle'); acceptedExport.current = null; setExportNotice('') }
    const canTrim = true, titlesOutOfRange = false, titlesTooShort = false, audioOutOfRange = false, imageInvalidRange = false, saveState = 'saved'
    let exportState = context.exportState
    const setExportState = value => { exportState = value; context.exportState = value }
    const setExportError = value => { context.error = value }
    const setExportNotice = value => { context.notice = value }
    const setQueueRefreshPending = value => { context.pending = value }
    const projectReferenceSafeErrorMessage = error => error.message
    const ${declarations.get('restoreExport')}
    const ${declarations.get('refreshExportQueue')}
    const ${declarations.get('handleExport')}
    const ${declarations.get('handleExportAnotherCopy')}
  `, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  return new Function('context', `${code}\nreturn { handleExport, refreshExportQueue, restoreExport, handleExportAnotherCopy }`)(state)
}

function exportStateFixture() {
  const state = {
    source: { workspace: 'scene' }, project: sequenceProject(), scope: { current: 1 },
    editVersion: { current: 0 }, saving: { current: false }, exporting: { current: false }, appending: { current: false },
    acceptedExport: { current: null }, queueConfirmation: { current: 0 }, exportState: 'idle',
    exportEditorProject, EditorExportSubmissionError, ProjectAssetRequestError, editorExportKey, readEditorExport, writeEditorExport,
    confirmEditorExportReceipt: (...args) => confirmEditorExportReceipt(...args, 5),
    jobs: [], reconnectCount: 0,
  }
  state.isCurrent = epoch => epoch === state.scope.current
  state.useStore = { getState: () => ({ jobs: state.jobs, reconnectJobs: async () => { state.reconnectCount += 1 } }) }
  return state
}

test('accepted Editor export retains its job through missing discovery and refreshes without another POST', async t => {
  const previous = globalThis.fetch
  let posts = 0
  const jobId = 'e'.repeat(32)
  globalThis.fetch = async (_url, init) => { assert.equal(init.method, 'POST'); posts += 1; return Response.json({ job_id: jobId, status: 'queued' }) }
  t.after(() => { globalThis.fetch = previous })
  const state = exportStateFixture()
  const handlers = await editorExportHandlers(state)
  await handlers.handleExport()
  await new Promise(resolve => setTimeout(resolve, 10))
  assert.equal(state.exportState, 'queued')
  assert.equal(state.acceptedExport.current.jobId, jobId)
  assert.equal(state.pending, false)
  assert.match(state.notice, /was accepted.*has not confirmed/)
  await handlers.handleExport()
  assert.equal(posts, 1)
  state.jobs = [{ id: jobId }]
  await handlers.refreshExportQueue(state.acceptedExport.current)
  assert.equal(state.notice, '')
  assert.equal(state.reconnectCount, 2)
  assert.equal(posts, 1)
})

test('Editor Queue confirmation expires and ignores changed editor/account scope or newer edits', async () => {
  assert.equal(await confirmEditorExportReceipt('e'.repeat(32), () => new Promise(() => {}), () => [], () => true, 5), false)
  assert.equal(await confirmEditorExportReceipt('e'.repeat(32), async () => {}, () => [{ id: 'f'.repeat(32) }], () => true, 5), false)
  for (const change of ['scope', 'editVersion', 'acceptedExport']) {
    const state = exportStateFixture()
    let finish
    state.useStore = { getState: () => ({ jobs: [{ id: 'e'.repeat(32) }], reconnectJobs: () => new Promise(resolve => { finish = resolve }) }) }
    const handlers = await editorExportHandlers(state)
    const receipt = { jobId: 'e'.repeat(32), epoch: 1, version: 0 }
    state.acceptedExport.current = receipt
    state.notice = 'Current draft notice'
    const pending = handlers.refreshExportQueue(receipt)
    if (change === 'acceptedExport') state.acceptedExport.current = null
    else state[change].current += 1
    finish()
    await pending
    assert.equal(state.notice, 'Current draft notice')
  }
})

test('uncertain Editor submission stays blocked while a definite rejection permits correction', async t => {
  const previous = globalThis.fetch
  t.after(() => { globalThis.fetch = previous })
  for (const reply of [() => { throw new Error('network lost') }, () => Response.json({}, { status: 503 }), () => Response.json({ job_id: '../bad', status: 'queued' }), () => ({ ok: true, json: async () => { throw new Error('broken body') } })]) {
    sessionStorage.removeItem(editorExportKey(sequenceProject()))
    let posts = 0
    globalThis.fetch = async () => { posts += 1; return reply() }
    const state = exportStateFixture()
    const { handleExport } = await editorExportHandlers(state)
    await handleExport()
    assert.equal(state.exportState, 'unconfirmed')
    assert.match(state.notice, /Check Queue before exporting/)
    assert.equal(state.reconnectCount, 0)
    await handleExport()
    assert.equal(posts, 1)
  }
  sessionStorage.removeItem(editorExportKey(sequenceProject()))
  globalThis.fetch = async () => Response.json({}, { status: 409 })
  const rejected = exportStateFixture()
  await (await editorExportHandlers(rejected)).handleExport()
  assert.equal(rejected.exportState, 'idle')
  assert.match(rejected.error, /reopen the video/)
})

test('Queue navigation survives the Editor replacing MainContent and consumes the pending request once', async t => {
  const previous = globalThis.window
  globalThis.window = new EventTarget()
  t.after(() => { globalThis.window = previous })
  // Editor has unmounted MainContent, so no listener exists at activation time.
  requestQueueView()
  let opens = 0
  const unsubscribe = subscribeQueueView(() => { opens += 1 })
  assert.equal(opens, 1)
  requestQueueView()
  assert.equal(opens, 2)
  unsubscribe()
  const unsubscribeAgain = subscribeQueueView(() => { opens += 1 })
  assert.equal(opens, 2, 'remount must not repeat a consumed navigation')
  unsubscribeAgain()
  const source = await readFile(new URL('../src/editor/EditorWorkspace.tsx', import.meta.url), 'utf8')
  const ast = ts.createSourceFile('EditorWorkspace.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let click
  const visit = node => {
    if (ts.isJsxAttribute(node) && node.name.getText(ast) === 'onClick'
      && node.initializer?.getText(ast).includes('requestQueueView()')) click = node.initializer.expression
    ts.forEachChild(node, visit)
  }
  visit(ast)
  assert.ok(click)
  const actualClick = ts.transpileModule(`const click = ${click.getText(ast)}`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  let current = false, closed = 0
  const activate = new Function('isCurrent', 'scope', 'closeEditor', 'requestQueueView', `${actualClick}; return click`)(
    () => current, { current: 1 }, () => { closed += 1 }, requestQueueView)
  activate()
  assert.equal(closed, 0)
  current = true
  activate()
  assert.equal(closed, 1)
  const mounted = subscribeQueueView(() => { opens += 1 })
  assert.equal(opens, 3)
  mounted()
})

test('lost export reply remains fenced after reopening the same saved cut, including after server completion', async t => {
  const previous = globalThis.fetch
  t.after(() => { globalThis.fetch = previous })
  let posts = 0
  globalThis.fetch = async () => {
    posts += 1
    assert.ok(readEditorExport(editorExportKey(sequenceProject())), 'intent must precede POST')
    throw new Error('accepted on server, reply lost')
  }
  const original = exportStateFixture()
  await (await editorExportHandlers(original)).handleExport()
  const reopened = exportStateFixture()
  reopened.jobs = [{ id: 'e'.repeat(32), status: 'completed' }]
  const handlers = await editorExportHandlers(reopened)
  handlers.restoreExport(reopened.project, reopened.scope.current)
  assert.equal(reopened.exportState, 'unconfirmed')
  await handlers.handleExport()
  handlers.handleExportAnotherCopy()
  await handlers.handleExport()
  assert.equal(posts, 1, 'an unrelated terminal card cannot resolve an ambiguous receipt')
  reopened.project = { ...reopened.project, revision: reopened.project.revision + 1 }
  const edited = exportStateFixture()
  edited.project = reopened.project
  await (await editorExportHandlers(edited)).handleExport()
  assert.equal(posts, 2, 'an actual new saved revision can be exported')
})

test('accepted receipt survives navigation before the reply and allows an explicit copy only after its exact job terminates', async t => {
  const previous = globalThis.fetch
  t.after(() => { globalThis.fetch = previous })
  let finish, posts = 0
  globalThis.fetch = async () => { posts += 1; return new Promise(resolve => { finish = resolve }) }
  const original = exportStateFixture()
  const pending = (await editorExportHandlers(original)).handleExport()
  original.scope.current += 1
  finish(Response.json({ job_id: 'e'.repeat(32), status: 'queued' }))
  await pending
  const reopened = exportStateFixture()
  const handlers = await editorExportHandlers(reopened)
  handlers.restoreExport(reopened.project, 1)
  assert.equal(reopened.exportState, 'queued')
  assert.equal(reopened.acceptedExport.current.jobId, 'e'.repeat(32))
  reopened.jobs = [{ id: 'f'.repeat(32), status: 'completed' }, { id: 'e'.repeat(32), status: 'running' }]
  handlers.handleExportAnotherCopy()
  await handlers.handleExport()
  assert.equal(posts, 1)
  reopened.jobs[1].status = 'completed'
  handlers.handleExportAnotherCopy()
  assert.equal(readEditorExport(editorExportKey(reopened.project)), null)
  globalThis.fetch = async () => { posts += 1; return Response.json({ job_id: 'f'.repeat(32), status: 'queued' }) }
  await handlers.handleExport()
  assert.equal(posts, 2)
  await new Promise(resolve => setTimeout(resolve, 10))
})

test('export receipts isolate accounts and projects and refuse unreadable or unwritable recovery before POST', async t => {
  const previous = globalThis.fetch
  t.after(() => { globalThis.fetch = previous })
  const project = sequenceProject()
  const local = editorExportKey(project)
  assert.equal(writeEditorExport(local, null, { token: 'intent', jobId: null }), true)
  assert.equal(readEditorExport(editorExportKey({ ...project, workspace: 'other' })), null)
  editorTestStore.setState({ accountContext: { enabled: true, authenticated: true, account: { id: 'owner-a' } } })
  assert.equal(readEditorExport(editorExportKey(project)), null)
  editorTestStore.setState({ accountContext: { enabled: false } })
  let posts = 0
  globalThis.fetch = async () => { posts += 1; throw new Error('must not submit') }
  for (const storage of [
    { getItem: () => '{malformed', setItem: () => {} },
    { getItem: () => null, setItem: () => { throw new Error('quota') } },
    { getItem: () => { throw new Error('denied') } },
  ]) {
    globalThis.sessionStorage = storage
    const state = exportStateFixture()
    await (await editorExportHandlers(state)).handleExport()
    assert.equal(state.exportState, 'unconfirmed')
  }
  assert.equal(posts, 0)
})


test('audio edits retain identity, video/title lanes and absolute time through reorder', () => {
  const project = sequenceProject()
  project.tracks.push({ id: 'audio-main', type: 'audio', name: 'Audio', items: [{ id: 'bed', asset_id: 'sound', source_in: 1, start: 2, duration: 3, speed: 1, volume: 1, muted: false }] })
  const changed = changeAudio(project, { source_in: 4, start: 1, duration: 2, volume: 0.25, muted: true, fade_in: 1.5, fade_out: 2, asset_id: 'foreign', id: 'forged' })
  assert.equal(audioLayer(changed).asset_id, 'sound')
  assert.equal(audioLayer(changed).id, 'bed')
  assert.equal(changed.tracks[0], project.tracks[0])
  assert.equal(changed.assets, project.assets)
  assert.deepEqual(audioLayer(moveClip(changed, 'clip-c', -1)), audioLayer(changed))
  assert.equal(audioLayer(changeAudio(changed, null)), undefined)
})

test('audio audition ramps follow source-relative trim, gain, mute and overlapping fades', () => {
  const layer = { source_in: 4, duration: 2, volume: 0.5, muted: false, fade_in: 0.5, fade_out: 1 }
  for (const [time, expected] of [[3.9, 0], [4, 0], [4.25, 0.25], [4.5, 0.5], [5, 0.5], [5.5, 0.25], [6, 0]]) {
    assert.equal(audioGain(layer, time), expected)
  }
  assert.equal(audioGain({ ...layer, muted: true }, 5), 0)
  assert.equal(audioGain({ ...layer, fade_in: 2, fade_out: 2 }, 5), 0.125)
  assert.equal(audioGain({ source_in: 4, duration: 2, volume: 0.5 }, 4), 0.5)
  const project = sequenceProject()
  project.tracks.push({ id: 'audio-main', items: [{ id: 'bed', asset_id: 'sound', ...layer }] })
  const shortened = audioLayer(changeAudio(project, { duration: 0.25 }))
  assert.deepEqual([shortened.fade_in, shortened.fade_out], [0.25, 0.25])
  assert.equal(audioLayer(project).duration, 2)
})

test('audio choices and import pin the current same-project Gallery revision', async () => {
  const project = sequenceProject()
  const outputs = [{ name: 'fiction.wav', workspace: 'scene', type: 'audio', revision: 'current', private: true, explicit: true },
    { name: 'other.wav', workspace: 'elsewhere', type: 'audio', revision: 'other' },
    { name: 'unsealed.wav', workspace: 'scene', type: 'audio' }, { name: 'video.mp4', workspace: 'scene', type: 'video', revision: 'video' }]
  assert.deepEqual(availableAudio(project, outputs), [outputs[0]])
  const previous = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return { ok: true, json: async () => ({ project }) } }
  try {
    assert.equal(await addEditorAudio('scene', project, outputs[0].name, 'current'), project)
    assert.match(calls[0].url, /\/editor\/projects\/first-source-draft\/audio$/)
    assert.deepEqual(JSON.parse(calls[0].init.body), { expected_revision: 5, output_name: 'fiction.wav', output_revision: 'current' })
  } finally { globalThis.fetch = previous }
})


test('image appearance and interval retain source identity, absolute timing and other lanes', () => {
  const project = sequenceProject()
  project.tracks.push({ id: 'images-main', type: 'video', items: [{ id: 'overlay', asset_id: 'still', start: 2, duration: 3, source_in: 0, speed: 1, size: 0.25, opacity: 1, position: 'center' }] })
  const changed = changeImage(project, 'overlay', { start: 1, duration: 2, opacity: 0.5, size: 1, position: 'bottom', id: 'forged', asset_id: 'foreign' })
  assert.equal(imageLayers(changed)[0].asset_id, 'still'); assert.equal(imageLayers(changed)[0].id, 'overlay')
  assert.equal(changed.tracks[0], project.tracks[0]); assert.equal(changed.assets, project.assets)
  assert.deepEqual(imageLayers(moveClip(changed, 'clip-c', -1)), imageLayers(changed))
  assert.deepEqual(imageLayers(changeImage(changed, 'overlay', null)), [])
  assert.deepEqual(availableImages(project, [{ name: 'yes.png', workspace: 'scene', type: 'image', revision: 'pin' }, { name: 'other.png', workspace: 'other', type: 'image', revision: 'pin' }, { name: 'missing.png', workspace: 'scene', type: 'image' }]).map(item => item.name), ['yes.png'])
  for (const position of ['top', 'center', 'bottom']) {
    const layout = imageLayout(128, 72, 16, 8, 1, position)
    assert.ok(layout.x >= 0 && layout.y >= 0 && layout.x + layout.width <= 100 && layout.y + layout.height <= 100)
  }
})

test('image import pins Gallery identity and CAS without accepting a client path', async () => {
  const previous = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return { ok: true, json: async () => ({ project: { id: 'cut' } }) } }
  try {
    assert.deepEqual(await addEditorImage('scene a', { id: 'cut #1', revision: 5 }, 'logo.png', 'pin'), { id: 'cut' })
    assert.match(calls[0].url, /scene%20a\/editor\/projects\/cut%20%231\/image$/)
    assert.deepEqual(JSON.parse(calls[0].init.body), { expected_revision: 5, output_name: 'logo.png', output_revision: 'pin' })
  } finally { globalThis.fetch = previous }
})


test('image row edit and removal retain ordered overlapping neighbors', () => {
  const project = sequenceProject()
  project.tracks.push({ id: 'images-main', items: [
    { id: 'a', asset_id: 'still-a', start: 0, duration: 3 },
    { id: 'b', asset_id: 'still-b', start: 1, duration: 3 },
  ] })
  const changed = changeImage(project, 'b', { opacity: 0, id: 'forged', asset_id: 'foreign' })
  assert.equal(imageLayers(changed)[0], imageLayers(project)[0])
  assert.deepEqual(imageLayers(changed).map(item => item.id), ['a', 'b'])
  assert.equal(imageLayers(changed)[1].asset_id, 'still-b')
  assert.deepEqual(imageLayers(changeImage(changed, 'a', null)), [imageLayers(changed)[1]])
  assert.deepEqual(imageLayers(moveClip(changed, 'clip-c', -1)), imageLayers(changed))
})


test('alternate take trims and removal preserve per-clip state and shared retained sources', () => {
  const project = sequenceProject()
  const first = project.tracks[0].items[0]
  first.take_asset_ids = ['a', 'b']
  first.take_states = { a: { source_in: 1, speed: 1 }, b: { source_in: 0.5, speed: 1 } }
  const second = project.tracks[0].items[1]
  second.take_asset_ids = ['b', 'a']
  second.take_states = { b: { source_in: 2, speed: 1 }, a: { source_in: 0, speed: 1 } }
  const trimmed = changeTrim(project, first.id, 2, 4)
  assert.deepEqual(trimmed.tracks[0].items[0].take_states, { a: { source_in: 2, speed: 1 }, b: { source_in: 0.5, speed: 1 } })
  assert.equal(trimmed.tracks[0].items[1].take_states, second.take_states)
  assert.equal(trimmed.tracks[0].items[1].source_in, second.source_in)
  assert.deepEqual(first.take_states.a, { source_in: 1, speed: 1 })
  const removed = removeClip(trimmed, first.id)
  assert.equal(removed.assets.a, project.assets.a)
  assert.equal(removed.assets.b, project.assets.b)
  const last = removeClip(removed, second.id)
  assert.equal(last.assets.a, undefined)
  assert.equal(last.assets.b, undefined)
  assert.equal(last.assets.c, project.assets.c)
})

test('take clients pin clip and CAS identity and explain saved-range failures', async () => {
  const project = sequenceProject()
  const previous = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return { ok: true, json: async () => ({ project }) } }
  try {
    assert.equal(await addEditorTake('scene', project, 'clip-a', 'b.mp4', 'current'), project)
    assert.equal(await switchEditorTake('scene', project, 'clip-a', 'b'), project)
    assert.match(calls[0].url, /\/clips\/clip-a\/takes$/)
    assert.deepEqual(JSON.parse(calls[0].init.body), { expected_revision: 5, output_name: 'b.mp4', output_revision: 'current' })
    assert.match(calls[1].url, /\/clips\/clip-a\/take$/)
    assert.deepEqual(JSON.parse(calls[1].init.body), { expected_revision: 5, asset_id: 'b' })
    globalThis.fetch = async () => ({ ok: false, status: 422 })
    await assert.rejects(switchEditorTake('scene', project, 'clip-a', 'b'), /Shorten the clip before switching/)
    assert.equal(calls.length, 2)
  } finally { globalThis.fetch = previous }
})


test('one alternate clip keeps its project frame clock rather than the selected source cadence', () => {
  const project = sequenceProject()
  project.tracks[0].items = project.tracks[0].items.slice(0,1)
  project.canvas.fps = 60
  const clip = project.tracks[0].items[0]
  clip.asset_id = 'b'; clip.duration = 1.01
  project.assets.b.fps = 24
  assert.equal(renderedDuration(project),1.01)
  clip.take_asset_ids = ['a','b']
  clip.take_states = {a:{source_in:1,speed:1},b:{source_in:0,speed:1}}
  assert.equal(renderedDuration(project),61/60)
})


test('Retake review uses active alternate source and exact sped cut without changing draft', async () => {
  const project = sequenceProject()
  const clip = project.tracks[0].items[0]
  clip.take_asset_ids = ['a', 'b']
  clip.take_states = { a: { source_in: 1, speed: 1 }, b: { source_in: 2.5, speed: 2 } }
  clip.asset_id = 'b'
  clip.source_in = 2.5
  clip.duration = 1.25
  clip.speed = 2
  project.assets.b.output_revision = 'sha256:' + 'b'.repeat(64)
  const before = JSON.stringify(project)
  assert.deepEqual(retakeSelectedCut(project, clip.id), { filename: 'b.mp4', context: {
    workspace: 'scene', revision: project.assets.b.output_revision, start: 2.5, end: 5,
    editor_origin: { editor_id: project.id, editor_revision: project.revision, clip_id: clip.id, asset_id: 'b' },
  } })
  assert.equal(JSON.stringify(project), before)
  assert.equal(retakeSelectedCut(project, 'missing'), null)
  clip.duration = 100
  assert.equal(retakeSelectedCut(project, clip.id), null)
})

test('Retake save barrier includes newer pending edits and refuses failed or racing saves', async () => {
  let dirty = true
  let saves = 0
  const save = async () => { saves++; dirty = false; return { allEditsSaved: true } }
  assert.equal(await prepareRetakeReview(Promise.resolve({ allEditsSaved: false }), () => true, () => dirty, save), true)
  assert.equal(saves, 1)
  dirty = true
  assert.equal(await prepareRetakeReview(Promise.resolve(null), () => true, () => dirty, save), false)
  assert.equal(saves, 1)
  assert.equal(await prepareRetakeReview(null, () => true, () => dirty, async () => ({ allEditsSaved: false })), false)
  let resolve
  const pending = new Promise(r => { resolve = r })
  let current = true
  const opened = prepareRetakeReview(pending, () => current, () => dirty, save)
  current = false
  resolve({ allEditsSaved: true })
  assert.equal(await opened, false)
  assert.equal(saves, 1)
})


test('actual Retake store opening identity is invalidated by workspace ABA and close/reopen', async () => {
  const { useStore } = await server.ssrLoadModule('/src/stores/useStore.ts')
  useStore.setState({ activeWorkspace: 'scene' })
  const context = { workspace: 'scene', revision: 'sha256:' + 'a'.repeat(64), start: 2, end: 4 }
  useStore.getState().openRetakeDialog('alternate.mp4', context)
  const opened = useStore.getState().retakeOpeningEpoch
  context.start = 100
  assert.equal(useStore.getState().retakeSourceContext.start, 2)
  useStore.setState({ activeWorkspace: 'other' })
  useStore.setState({ activeWorkspace: 'scene' })
  assert.equal(useStore.getState().retakeDialogOpen, false)
  assert.ok(useStore.getState().retakeOpeningEpoch > opened)
  useStore.getState().openRetakeDialog('gallery.mp4')
  const reopened = useStore.getState().retakeOpeningEpoch
  assert.equal(useStore.getState().retakeSourceContext, null)
  useStore.getState().closeRetakeDialog()
  useStore.getState().openRetakeDialog('gallery.mp4')
  assert.ok(useStore.getState().retakeOpeningEpoch > reopened)
  useStore.getState().closeRetakeDialog()
})


test('Retake result discovery is GET-only on reopening and drops late scope/clip responses', async () => {
  const previous = globalThis.fetch
  const calls = []
  const row = { job_id: 'faceb00c', clip_id: 'clip-a', status: 'completed', outputs: [{ name: 'retake.mp4', revision: 'sha256:' + 'd'.repeat(64) }], conflict: false }
  try {
    globalThis.fetch = async (url, init) => {
      calls.push({ url, method: init?.method ?? 'GET' })
      return { ok: true, json: async () => ({ retakes: [row] }) }
    }
    for (let reopen = 0; reopen < 2; reopen++) {
      assert.deepEqual(await readRetakeReview('scene', 'saved edit', 'clip-a', () => true), [row])
    }
    assert.deepEqual(calls.map(item => item.method), ['GET', 'GET'])
    assert.ok(calls.every(item => item.url.endsWith('/editor/projects/saved%20edit/retakes')))
    let release
    const pending = new Promise(resolve => { release = resolve })
    globalThis.fetch = async () => { await pending; return { ok: true, json: async () => ({ retakes: [row] }) } }
    let sequence = 0
    const captured = sequence
    const late = readRetakeReview('scene', 'saved edit', 'clip-a', () => sequence === captured)
    sequence++ // account/workspace/selection replacement, including a return to the original name
    release()
    assert.equal(await late, null)
  } finally { globalThis.fetch = previous }
})

test('explicit Retake result return saves newer edits and adds only an inactive take at current CAS', async () => {
  const previous = globalThis.fetch
  const original = sequenceProject()
  const output = { name: 'retake.mp4', revision: 'sha256:' + 'e'.repeat(64) }
  const review = { job_id: 'faceb00c', clip_id: 'clip-a', status: 'completed', outputs: [output], conflict: false }
  let project = original
  let dirty = true
  const order = []
  const response = { ...original, revision: 7, assets: { ...original.assets, alt: { ...original.assets.a, output_id: output.name, output_revision: output.revision } }, tracks: original.tracks.map((track, index) => index ? track : { ...track, items: track.items.map((clip, i) => i ? clip : { ...clip, take_asset_ids: ['a', 'alt'], take_states: { a: { source_in: 1, speed: 1 }, alt: { source_in: 1, speed: 1 } } }) }) }
  try {
    globalThis.fetch = async (url, init) => {
      order.push('POST')
      assert.ok(url.endsWith('/retakes/faceb00c/take'))
      assert.equal(init.method, 'POST')
      assert.deepEqual(JSON.parse(init.body), { expected_revision: 6, output_name: output.name, output_revision: output.revision })
      return { ok: true, json: async () => ({ project: response, reused: false }) }
    }
    const prepare = () => prepareRetakeReview(Promise.resolve({ allEditsSaved: false }), () => true, () => dirty, async () => {
      order.push('save'); project = { ...original, revision: 6 }; dirty = false; return { allEditsSaved: true }
    })
    const returned = await returnRetakeResult(review, output, 'clip-a', () => true, prepare, () => project)
    assert.deepEqual(order, ['save', 'POST'])
    assert.equal(returned.tracks[0].items[0].asset_id, 'a')
    assert.equal(returned.tracks[0].items[0].source_in, 1)
    assert.equal(returned.tracks[0].items[0].duration, 3)
    assert.deepEqual(returned.canvas, original.canvas)
    assert.equal(original.assets.alt, undefined)
    order.length = 0
    for (const options of [
      { row: { ...review, conflict: true }, current: () => true, prepare: async () => true },
      { row: review, current: () => true, prepare: async () => false },
      { row: review, current: () => false, prepare: async () => true },
    ]) assert.equal(await returnRetakeResult(options.row, output, 'clip-a', options.current, options.prepare, () => project), null)
    assert.equal(order.length, 0)
    let release
    const pending = new Promise(resolve => { release = resolve })
    let sequence = 0
    globalThis.fetch = async () => { order.push('POST'); await pending; return { ok: true, json: async () => ({ project: response, reused: false }) } }
    const captured = sequence
    const late = returnRetakeResult(review, output, 'clip-a', () => sequence === captured, async () => true, () => project)
    await Promise.resolve(); await Promise.resolve()
    sequence++
    release()
    assert.equal(await late, null, 'accepted response cannot replace a new Editor/account/clip scope')
  } finally { globalThis.fetch = previous }
})


test('Retake review rejects malformed completion identities and explicit dismissal only closes review', async () => {
  const previous = globalThis.fetch
  const revision = 'sha256:' + 'a'.repeat(64)
  const row = { job_id: 'faceb00c', clip_id: 'clip-a', status: 'completed', outputs: [{ name: 'retake.mp4', revision }], conflict: false }
  try {
    for (const outputs of [[{ name: '../foreign.mp4', revision }], [{ name: 'retake.mp4', revision: 'wrong' }]]) {
      globalThis.fetch = async () => ({ ok: true, json: async () => ({ retakes: [{ ...row, outputs }] }) })
      await assert.rejects(getEditorRetakes('scene', 'saved edit'), /could not be verified/)
    }
    const calls = []
    globalThis.fetch = async (url, init) => {
      calls.push({ url, method: init?.method ?? 'GET', body: init?.body })
      return { ok: true, json: async () => init?.method === 'POST' ? { dismissed: true } : { retakes: [] } }
    }
    await dismissEditorRetake('scene', 'saved edit', 'faceb00c')
    assert.deepEqual(await getEditorRetakes('scene', 'saved edit'), [])
    assert.deepEqual(calls.map(call => call.method), ['POST', 'GET'])
    assert.ok(calls[0].url.endsWith('/retakes/faceb00c/dismiss'))
    assert.equal(calls[0].body, '{}')
    assert.ok(calls.every(call => !/cancel|delete|generate|retake$/.test(call.url)))
  } finally { globalThis.fetch = previous }
})
