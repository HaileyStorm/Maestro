import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import ts from 'typescript'

const componentUrl = new URL('../src/components/Sidebar/InputsPanel.tsx', import.meta.url)

async function loadApplyTransaction() {
  const source = await readFile(componentUrl, 'utf8')
  const start = source.indexOf('const OFFSET_PRESETS')
  const end = source.indexOf('export function InputsPanel()', start)
  assert.ok(start >= 0 && end > start, 'project-reference apply transaction must remain extractable')
  const compiled = ts.transpileModule(`${source.slice(start, end)}\nexport { executeProjectReferenceApply, commitPreparedProjectReferenceApply }`, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
}

const image = index => ({
  key: `image-${index}`,
  filename: `image-${index}.png`,
  mediaType: 'image/png',
  kind: 'image',
  source: { index },
})

const media = (kind, index) => ({
  key: `${kind}-${index}`,
  filename: `${kind}-${index}.${kind === 'video' ? 'mp4' : 'wav'}`,
  mediaType: `${kind}/${kind === 'video' ? 'mp4' : 'wav'}`,
  kind,
  source: { index },
})

const baseState = overrides => ({
  acceptsFirstLastFrame: false,
  acceptsReferenceImage: true,
  acceptsReferenceVideo: true,
  acceptsReferenceAudio: true,
  semanticTermsAccepted: true,
  semanticTermsRequired: true,
  semanticImageCount: 0,
  maxSemanticImages: 9,
  videoPaths: [],
  maxReferenceVideos: 3,
  audioPaths: [],
  maxReferenceAudio: 3,
  semanticMixedCount: 0,
  maxMixedReferences: 12,
  videoDurationTotal: 0,
  audioDurationTotal: 0,
  minReferenceDuration: 2,
  maxReferenceDuration: 15,
  maxVideoDurationTotal: 15,
  maxAudioDurationTotal: 15,
  hasStart: false,
  hasEnd: false,
  isExtend: false,
  supportsEndFrame: true,
  supportsInject: true,
  ...overrides,
})

function transactionOperations(overrides = {}) {
  const calls = { downloads: [], uploads: [], commits: [] }
  return {
    calls,
    operations: {
      async download(entry) {
        calls.downloads.push(entry.key)
        return { name: entry.filename }
      },
      async duration(_file, kind) {
        return kind === 'video' ? 3 : 2
      },
      async upload(file, kind) {
        calls.uploads.push([kind, file.name])
        return `/uploads/${file.name}`
      },
      isCurrent() { return true },
      commit(prepared) { calls.commits.push(prepared) },
      ...overrides,
    },
  }
}

test('a multi-image frame pack assigns start, end, then every inject without overwriting', async () => {
  const { executeProjectReferenceApply } = await loadApplyTransaction()
  const runtime = transactionOperations()

  await executeProjectReferenceApply(
    [image(1), image(2), image(3), image(4)],
    baseState({
      acceptsReferenceImage: false,
      acceptsReferenceVideo: false,
      acceptsReferenceAudio: false,
      acceptsFirstLastFrame: true,
      semanticTermsAccepted: false,
      semanticImageCount: 0,
      maxSemanticImages: null,
    }),
    runtime.operations,
  )

  assert.equal(runtime.calls.commits.length, 1)
  assert.deepEqual(
    runtime.calls.commits[0].entries.map(entry => [entry.key, entry.destination, entry.uploadedPath]),
    [
      ['image-1', 'frame-start', null],
      ['image-2', 'frame-end', null],
      ['image-3', 'frame-inject', '/uploads/image-3.png'],
      ['image-4', 'frame-inject', '/uploads/image-4.png'],
    ],
  )
  assert.deepEqual(runtime.calls.uploads, [
    ['image', 'image-3.png'],
    ['image', 'image-4.png'],
  ])
})

test('semantic image capacity fails before download and never switches the image to a frame', async () => {
  const { executeProjectReferenceApply } = await loadApplyTransaction()
  const runtime = transactionOperations()

  await assert.rejects(
    executeProjectReferenceApply(
      [image(1)],
      baseState({
        acceptsFirstLastFrame: true,
        semanticImageCount: 9,
        maxSemanticImages: 9,
        semanticMixedCount: 9,
      }),
      runtime.operations,
    ),
    /needs 1 image slots, but only 0 remain/,
  )
  assert.deepEqual(runtime.calls.downloads, [])
  assert.deepEqual(runtime.calls.uploads, [])
  assert.deepEqual(runtime.calls.commits, [])
})

test('video and audio packs append in authored order and retain measured durations', async () => {
  const { executeProjectReferenceApply } = await loadApplyTransaction()
  const runtime = transactionOperations({
    async duration(file) {
      return {
        'video-1.mp4': 3.25,
        'video-2.mp4': 4.5,
        'audio-1.wav': 2.75,
        'audio-2.wav': 3.5,
      }[file.name]
    },
  })

  await executeProjectReferenceApply(
    [media('video', 1), media('audio', 1), media('video', 2), media('audio', 2)],
    baseState({
      videoPaths: ['/existing/video.mp4'],
      audioPaths: ['/existing/audio.wav'],
      semanticMixedCount: 2,
      videoDurationTotal: 2,
      audioDurationTotal: 2,
    }),
    runtime.operations,
  )

  const [prepared] = runtime.calls.commits
  assert.deepEqual(prepared.videoPaths, [
    '/existing/video.mp4',
    '/uploads/video-1.mp4',
    '/uploads/video-2.mp4',
  ])
  assert.deepEqual(prepared.audioPaths, [
    '/existing/audio.wav',
    '/uploads/audio-1.wav',
    '/uploads/audio-2.wav',
  ])
  assert.deepEqual(
    prepared.entries.map(entry => [entry.key, entry.destination, entry.duration]),
    [
      ['video-1', 'video', 3.25],
      ['audio-1', 'audio', 2.75],
      ['video-2', 'video', 4.5],
      ['audio-2', 'audio', 3.5],
    ],
  )
})

test('generic project references accept readable long video and audio without H3 duration caps', async () => {
  const { executeProjectReferenceApply } = await loadApplyTransaction()
  const runtime = transactionOperations({ async duration() { return 45 } })

  await executeProjectReferenceApply(
    [media('video', 1), media('audio', 1)],
    baseState({
      semanticTermsRequired: false,
      minReferenceDuration: null,
      maxReferenceDuration: null,
      maxVideoDurationTotal: null,
      maxAudioDurationTotal: null,
    }),
    runtime.operations,
  )

  assert.equal(runtime.calls.commits.length, 1)
  assert.deepEqual(runtime.calls.commits[0].entries.map(entry => entry.duration), [45, 45])

  const unreadable = transactionOperations({ async duration() { return Number.POSITIVE_INFINITY } })
  await assert.rejects(
    executeProjectReferenceApply(
      [media('video', 2)],
      baseState({
        semanticTermsRequired: false,
        minReferenceDuration: null,
        maxReferenceDuration: null,
        maxVideoDurationTotal: null,
        maxAudioDurationTotal: null,
      }),
      unreadable.operations,
    ),
    /must be a readable clip/,
  )
  assert.equal(unreadable.calls.uploads.length, 0)
  assert.equal(unreadable.calls.commits.length, 0)
})

test('H3 project references retain per-clip and aggregate duration bounds', async () => {
  const { executeProjectReferenceApply } = await loadApplyTransaction()
  const tooLong = transactionOperations({ async duration() { return 15.1 } })
  await assert.rejects(
    executeProjectReferenceApply([media('video', 1)], baseState({}), tooLong.operations),
    /between 2 and 15 seconds/,
  )
  assert.equal(tooLong.calls.uploads.length, 0)
  assert.equal(tooLong.calls.commits.length, 0)

  const aggregate = transactionOperations({ async duration() { return 2 } })
  await assert.rejects(
    executeProjectReferenceApply(
      [media('audio', 1)],
      baseState({ audioPaths: ['/existing/audio.wav'], audioDurationTotal: 14 }),
      aggregate.operations,
    ),
    /total at most 15 seconds/,
  )
  assert.equal(aggregate.calls.uploads.length, 0)
  assert.equal(aggregate.calls.commits.length, 0)
})

test('semantic destinations require accepted terms while a frame-only image remains available', async () => {
  const { executeProjectReferenceApply } = await loadApplyTransaction()
  const semantic = transactionOperations()
  await assert.rejects(
    executeProjectReferenceApply(
      [image(1)],
      baseState({ acceptsFirstLastFrame: true, semanticTermsAccepted: false }),
      semantic.operations,
    ),
    /Accept the MiniMax H3 reference-media terms/,
  )
  assert.equal(semantic.calls.commits.length, 0)

  const frame = transactionOperations()
  await executeProjectReferenceApply(
    [image(1)],
    baseState({
      acceptsReferenceImage: false,
      acceptsReferenceVideo: false,
      acceptsReferenceAudio: false,
      acceptsFirstLastFrame: true,
      semanticTermsAccepted: false,
      maxSemanticImages: null,
    }),
    frame.operations,
  )
  assert.equal(frame.calls.commits[0].entries[0].destination, 'frame-start')
})

test('download failure or a changed project selection commits no Generate inputs', async () => {
  const { executeProjectReferenceApply } = await loadApplyTransaction()
  const failed = transactionOperations({
    async download(entry) {
      failed.calls.downloads.push(entry.key)
      if (entry.key === 'image-2') throw new Error('download failed')
      return { name: entry.filename }
    },
  })
  await assert.rejects(
    executeProjectReferenceApply(
      [image(1), image(2)],
      baseState({ acceptsReferenceImage: true }),
      failed.operations,
    ),
    /download failed/,
  )
  assert.equal(failed.calls.commits.length, 0)

  let current = true
  const stale = transactionOperations({
    async download(entry) {
      stale.calls.downloads.push(entry.key)
      current = false
      return { name: entry.filename }
    },
    isCurrent() { return current },
  })
  await assert.rejects(
    executeProjectReferenceApply([image(1)], baseState({}), stale.operations),
  )
  assert.equal(stale.calls.commits.length, 0)
  assert.equal(stale.calls.uploads.length, 0)
})

test('the real commit prepares every inject preview before mutating and cleans up on failure', async () => {
  const { commitPreparedProjectReferenceApply } = await loadApplyTransaction()
  const mutations = []
  const revoked = []
  let previews = 0
  const entry = (key, destination, uploadedPath = null) => ({
    ...image(key),
    destination,
    file: { name: `${key}.png` },
    duration: null,
    uploadedPath,
  })

  assert.throws(() => commitPreparedProjectReferenceApply({
    entries: [
      entry('semantic', 'semantic-image'),
      entry('start', 'frame-start'),
      entry('inject-1', 'frame-inject', '/uploads/inject-1.png'),
      entry('inject-2', 'frame-inject', '/uploads/inject-2.png'),
    ],
    videoPaths: [],
    audioPaths: [],
  }, [], 1, {
    createPreview() {
      previews += 1
      if (previews === 2) throw new Error('preview allocation failed')
      return 'blob:first-preview'
    },
    revokePreview(url) { revoked.push(url) },
    addSemanticImage() { mutations.push('semantic') },
    setStart() { mutations.push('start') },
    setEnd() { mutations.push('end') },
    setFrames() { mutations.push('frames') },
    setVideos() { mutations.push('videos') },
    setAudio() { mutations.push('audio') },
    setDurations() { mutations.push('durations') },
  }), /preview allocation failed/)

  assert.deepEqual(mutations, [])
  assert.deepEqual(revoked, ['blob:first-preview'])
})

test('the live callback fences account, project, selection, and Generate input identity', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const callbackStart = source.indexOf('const applyProjectReferenceChoice = async')
  const callbackEnd = source.indexOf('\n  const removeSemanticAudio', callbackStart)
  const callback = source.slice(callbackStart, callbackEnd)
  assert.match(callback, /currentAccountIdentityEpoch\(\) === submittedAccountEpoch/)
  assert.match(callback, /current\.activeWorkspace === submittedProject/)
  assert.match(callback, /current\.params === submittedParams/)
  assert.match(callback, /currentOutputs\.every\(\(output, index\) => output\.id === submittedOutputIds\[index\]\)/)
  assert.match(callback, /await executeProjectReferenceApply\(/)
  assert.match(callback, /setVideos: syncSemanticVideoRefs/)
  assert.match(callback, /setAudio: syncSemanticAudioRefs/)
})
