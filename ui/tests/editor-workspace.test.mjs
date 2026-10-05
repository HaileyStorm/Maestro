import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

import { addEditorAudio, addEditorImage, appendEditorClip, exportEditorProject, isBackendJobId, openOutputInEditor, saveEditorProject } from '../src/api/client.ts'

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
        return `${code}\nexport { changeTrim, moveClip, removeClip, availableVideos, addText, changeText, textLayers, renderedDuration, availableAudio, audioLayer, changeAudio, audioGain, imageLayers, changeImage, availableImages, imageLayout };`
      }
    },
  }],
})
const { changeTrim, moveClip, removeClip, availableVideos, addText, changeText, textLayers, renderedDuration, availableAudio, audioLayer, changeAudio, audioGain, imageLayers, changeImage, availableImages, imageLayout } = await server.ssrLoadModule('/src/editor/EditorWorkspace.tsx')
after(() => server.close())

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
    return { ok: true, json: async () => ({ job_id: 'export-1', status: 'queued' }) }
  }
  try {
    assert.deepEqual(await exportEditorProject('scene a', { id: 'cut #1', revision: 4 }), {
      job_id: 'export-1', status: 'queued',
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
