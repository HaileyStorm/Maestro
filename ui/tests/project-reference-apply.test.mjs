import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import ts from 'typescript'

const componentUrl = new URL('../src/components/Sidebar/InputsPanel.tsx', import.meta.url)
const attachmentOptionsUrl = new URL('../src/lib/generateAttachmentOptions.ts', import.meta.url)

async function loadApplyTransaction() {
  const [source, attachmentOptions] = await Promise.all([
    readFile(componentUrl, 'utf8'),
    readFile(attachmentOptionsUrl, 'utf8'),
  ])
  const start = source.indexOf('const OFFSET_PRESETS')
  const end = source.indexOf('export function InputsPanel()', start)
  assert.ok(start >= 0 && end > start, 'project-reference apply transaction must remain extractable')
  const compiled = ts.transpileModule(`${attachmentOptions}\n${source.slice(start, end)}\nexport { executeProjectReferenceApply, commitPreparedProjectReferenceApply, executeSemanticMediaAdmission, resolveRestoredSemanticImageRemoval }`, {
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
  videoDurationStatus: 'ready',
  audioDurationStatus: 'ready',
  h3StudioWorkflow: true,
  durationSeconds: 6,
  framesMaximum: 120,
  fps: 24,
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

test('a generic multi-image frame pack assigns start, end, then every inject without overwriting', async () => {
  const { executeProjectReferenceApply } = await loadApplyTransaction()
  const runtime = transactionOperations()

  await executeProjectReferenceApply(
    [image(1), image(2), image(3), image(4)],
    baseState({
      acceptsReferenceImage: false,
      acceptsReferenceVideo: false,
      acceptsReferenceAudio: false,
      acceptsFirstLastFrame: true,
      h3StudioWorkflow: false,
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

test('an H3 frame pack rejects a third image before download instead of creating KFI semantic input', async () => {
  const { executeProjectReferenceApply } = await loadApplyTransaction()
  const runtime = transactionOperations()

  await assert.rejects(
    executeProjectReferenceApply(
      [image(1), image(2), image(3)],
      baseState({
        acceptsReferenceImage: false,
        acceptsReferenceVideo: false,
        acceptsReferenceAudio: false,
        acceptsFirstLastFrame: true,
        maxSemanticImages: null,
        supportsInject: false,
      }),
      runtime.operations,
    ),
    /no available frame slot/,
  )
  assert.deepEqual(runtime.calls.downloads, [])
  assert.deepEqual(runtime.calls.uploads, [])
  assert.deepEqual(runtime.calls.commits, [])
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
      h3StudioWorkflow: false,
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
        h3StudioWorkflow: false,
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
      baseState({
        semanticImageCount: 2,
        audioPaths: ['/existing/audio.wav'],
        semanticMixedCount: 3,
        audioDurationTotal: 14,
      }),
      aggregate.operations,
    ),
    /total at most 15 seconds/,
  )
  assert.equal(aggregate.calls.uploads.length, 0)
  assert.equal(aggregate.calls.commits.length, 0)
})

test('H3 project packs validate staged visual and audio inputs as one order-independent result', async () => {
  const { executeProjectReferenceApply } = await loadApplyTransaction()
  for (const entries of [
    [media('audio', 1), image(1)],
    [image(1), media('audio', 1)],
  ]) {
    const runtime = transactionOperations()
    await executeProjectReferenceApply(entries, baseState({}), runtime.operations)
    assert.equal(runtime.calls.commits.length, 1)
    assert.deepEqual(
      runtime.calls.commits[0].entries.map(entry => entry.destination),
      entries[0].kind === 'audio'
        ? ['audio', 'semantic-image']
        : ['semantic-image', 'audio'],
    )
  }

  const audioOnly = transactionOperations()
  await assert.rejects(
    executeProjectReferenceApply([media('audio', 2)], baseState({}), audioOnly.operations),
    /one image or video for each audio reference/,
  )
  assert.deepEqual(audioOnly.calls.downloads, [])
  assert.deepEqual(audioOnly.calls.commits, [])
})

test('H3 project packs reject short mixed conditioning before any download and allow it above native maximum', async () => {
  const { executeProjectReferenceApply } = await loadApplyTransaction()
  const short = transactionOperations()
  await assert.rejects(
    executeProjectReferenceApply(
      [image(1)],
      baseState({ hasStart: true, durationSeconds: 5 }),
      short.operations,
    ),
    /Request more than 5\.00s/,
  )
  assert.deepEqual(short.calls.downloads, [])
  assert.deepEqual(short.calls.commits, [])

  const long = transactionOperations()
  await executeProjectReferenceApply(
    [image(1)],
    baseState({ hasStart: true, durationSeconds: 5.01 }),
    long.operations,
  )
  assert.equal(long.calls.commits.length, 1)
})

test('H3 project packs wait for existing relevant durations and fail closed when a restored duration is unavailable', async () => {
  const { executeProjectReferenceApply } = await loadApplyTransaction()
  const pendingVideo = transactionOperations()
  await assert.rejects(
    executeProjectReferenceApply(
      [media('video', 1)],
      baseState({
        videoPaths: ['/existing/video.mp4'],
        semanticMixedCount: 1,
        videoDurationStatus: 'pending',
      }),
      pendingVideo.operations,
    ),
    /still being checked/,
  )
  assert.deepEqual(pendingVideo.calls.downloads, [])

  const unavailableAudio = transactionOperations()
  await assert.rejects(
    executeProjectReferenceApply(
      [media('audio', 1)],
      baseState({
        semanticImageCount: 2,
        audioPaths: ['/existing/audio.wav'],
        semanticMixedCount: 3,
        audioDurationStatus: 'unavailable',
      }),
      unavailableAudio.operations,
    ),
    /duration is unavailable/,
  )
  assert.deepEqual(unavailableAudio.calls.downloads, [])

  const independentImage = transactionOperations()
  await executeProjectReferenceApply(
    [image(1)],
    baseState({
      videoPaths: ['/existing/video.mp4'],
      semanticMixedCount: 1,
      videoDurationStatus: 'unavailable',
    }),
    independentImage.operations,
  )
  assert.equal(independentImage.calls.commits.length, 1)
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

test('the live callback fences account, project, compatibility inputs, selection, and Generate input identity', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const callbackStart = source.indexOf('const applyProjectReferenceChoice = async')
  const callbackEnd = source.indexOf('\n  const removeSemanticAudio', callbackStart)
  const callback = source.slice(callbackStart, callbackEnd)
  assert.match(callback, /currentAccountIdentityEpoch\(\) === submittedAccountEpoch/)
  assert.match(callback, /current\.activeWorkspace === submittedProject/)
  assert.match(callback, /current\.params === submittedParams/)
  assert.match(callback, /current\.durationSeconds === submittedStore\.durationSeconds/)
  assert.match(callback, /current\.modelOptions === submittedStore\.modelOptions/)
  assert.match(callback, /current\.generationMode === submittedStore\.generationMode/)
  assert.match(callback, /currentOutputs\.every\(\(output, index\) => output\.id === submittedOutputIds\[index\]\)/)
  assert.match(callback, /await executeProjectReferenceApply\(/)
  assert.match(callback, /setVideos: syncSemanticVideoRefs/)
  assert.match(callback, /setAudio: syncSemanticAudioRefs/)
})

test('legacy H3 KFI semantic removal keeps refs and positions aligned, then clears only KFI state', async () => {
  const { resolveRestoredSemanticImageRemoval } = await loadApplyTransaction()
  const partial = resolveRestoredSemanticImageRemoval(
    ['/uploads/a.png', '/uploads/b.png', '/uploads/c.png'],
    1,
    'W1:25 W2:50 W3:75',
    'VKFI+A',
  )
  assert.deepEqual(partial, {
    imageRefs: ['/uploads/a.png', '/uploads/c.png'],
    framesPositions: 'W1:25 W3:75',
    videoPromptType: 'VKFI+A',
    legacyKfi: true,
  })

  const last = resolveRestoredSemanticImageRemoval(
    ['/uploads/c.png'],
    0,
    'W3:75',
    'VKFI+A',
  )
  assert.deepEqual(last, {
    imageRefs: undefined,
    framesPositions: undefined,
    videoPromptType: 'V+A',
    legacyKfi: true,
  })

  const ordinary = resolveRestoredSemanticImageRemoval(
    ['/uploads/semantic-a.png', '/uploads/semantic-b.png'],
    0,
    'unchanged metadata',
    'V+A',
  )
  assert.deepEqual(ordinary, {
    imageRefs: ['/uploads/semantic-b.png'],
    framesPositions: 'unchanged metadata',
    videoPromptType: 'V+A',
    legacyKfi: false,
  })
})

test('semantic media admission serializes concurrent video work and rejects deferred mode or duration changes', async () => {
  const { executeSemanticMediaAdmission } = await loadApplyTransaction()
  const guard = { current: null }
  let finishMeasure
  let measurements = 0
  const commits = []
  const first = executeSemanticMediaAdmission(guard, {
    measure: () => {
      measurements += 1
      return new Promise(resolve => { finishMeasure = resolve })
    },
    validate: duration => duration,
    upload: async () => '/uploads/video-1.mp4',
    isCurrent: () => true,
    commit: path => commits.push(path),
  })
  const concurrent = await executeSemanticMediaAdmission(guard, {
    measure: async () => { measurements += 1; return 3 },
    validate: duration => duration,
    upload: async () => '/uploads/video-2.mp4',
    isCurrent: () => true,
    commit: path => commits.push(path),
  })
  assert.equal(concurrent, 'busy')
  assert.equal(measurements, 1)
  finishMeasure(3)
  assert.equal(await first, 'committed')
  assert.deepEqual(commits, ['/uploads/video-1.mp4'])

  for (const changedField of ['generationMode', 'durationSeconds']) {
    const state = { generationMode: 'video', durationSeconds: 6 }
    const captured = { ...state }
    let release
    let uploads = 0
    const pending = executeSemanticMediaAdmission(guard, {
      measure: () => new Promise(resolve => { release = resolve }),
      validate: duration => duration,
      upload: async () => { uploads += 1; return '/uploads/stale.mp4' },
      isCurrent: () => state.generationMode === captured.generationMode
        && state.durationSeconds === captured.durationSeconds,
      commit: () => { throw new Error('stale admission must not commit') },
    })
    if (changedField === 'generationMode') state.generationMode = 'tools'
    else state.durationSeconds = 12
    release(3)
    assert.equal(await pending, 'stale', changedField)
    assert.equal(uploads, 0, changedField)
  }
})

test('semantic media admission checks current state again after upload without deleting the unused result', async () => {
  const { executeSemanticMediaAdmission } = await loadApplyTransaction()
  const guard = { current: null }
  let current = true
  let finishUpload
  let commits = 0
  const pending = executeSemanticMediaAdmission(guard, {
    measure: async () => 3,
    validate: duration => duration,
    upload: () => new Promise(resolve => { finishUpload = resolve }),
    isCurrent: () => current,
    commit: () => { commits += 1 },
  })
  await Promise.resolve()
  current = false
  finishUpload('/uploads/unused-video.mp4')
  assert.equal(await pending, 'stale')
  assert.equal(commits, 0)
  assert.equal(guard.current, null)
})

test('a retained semantic media picker rejects an old render before measuring or uploading', async () => {
  const { executeSemanticMediaAdmission } = await loadApplyTransaction()
  const guard = { current: null }
  let measurements = 0
  let uploads = 0
  let commits = 0
  const result = await executeSemanticMediaAdmission(guard, {
    measure: async () => { measurements += 1; return 3 },
    validate: duration => duration,
    upload: async () => { uploads += 1; return '/uploads/stale-picker.mp4' },
    isCurrent: () => false,
    commit: () => { commits += 1 },
  })

  assert.equal(result, 'stale')
  assert.equal(measurements, 0)
  assert.equal(uploads, 0)
  assert.equal(commits, 0)
  assert.equal(guard.current, null)
})
