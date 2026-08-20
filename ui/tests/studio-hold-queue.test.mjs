import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const generateButton = await readFile(
  new URL('../src/components/Sidebar/GenerateButton.tsx', import.meta.url),
  'utf8',
)
const directorChat = await readFile(
  new URL('../src/components/Sidebar/DirectorChat.tsx', import.meta.url),
  'utf8',
)
const client = await readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const store = await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
const queueProjection = await readFile(
  new URL('../src/lib/queueProjection.ts', import.meta.url),
  'utf8',
)

function slice(source, start, end) {
  const begin = source.indexOf(start)
  assert.notEqual(begin, -1, `missing start ${start}`)
  const stop = source.indexOf(end, begin)
  assert.notEqual(stop, -1, `missing end ${end}`)
  return source.slice(begin, stop)
}

test('Studio ListPlus holds without taking the generating lock', () => {
  assert.match(generateButton, /handleClick\('queue'\)/)
  assert.match(
    generateButton,
    /Hold current Studio settings in the queue without starting generation/,
  )
  assert.match(generateButton, /if \(mode === 'now'\) setSidebarOpen\(false\)/)
  const generation = slice(store, "startGeneration: async (mode = 'now') => {", 'stopGeneration: (jobId)')
  assert.match(generation, /const holdForQueue = mode === 'queue'/)
  assert.match(generation, /isGenerating: holdForQueue \? s\.isGenerating : true/)
  assert.match(generation, /await api\.submitGeneration\(params, holdForQueue\)/)
  assert.match(generation, /Ready - waiting for Start Queue/)
})

test('submitGeneration posts Continuum _queue_mode held or now', () => {
  const submit = slice(
    client,
    'export async function submitGeneration(',
    'export interface GenerationPlanApprovalRequest',
  )
  assert.match(submit, /_queue_mode: holdForQueue \? 'held' : 'now'/)
})

test('Director Add to Queue calls enqueueDirectorPipeline', () => {
  assert.match(directorChat, /queueCurrentDirectorPipeline\(\)/)
  assert.match(
    directorChat,
    /Hold this complete project in the persistent queue without starting it/,
  )
  const queueEntry = slice(
    store,
    'queueCurrentDirectorPipeline: async () => {',
    'startDirectorPipeline: async',
  )
  assert.match(queueEntry, /startDirectorPipeline\('queue'\)/)
  const pipeline = slice(
    store,
    "startDirectorPipeline: async (mode = 'now') => {",
    'const { pipeline_id } = await api.startPipeline(pipelineParams)',
  )
  assert.match(pipeline, /await api\.enqueueDirectorPipeline\(pipelineParams\)/)
})

test('queue badge uses Continuum held flag instead of status held', () => {
  const statuses = slice(
    queueProjection,
    "const ACTIVE_STATUSES = new Set<GenerationJob['status']>([",
    '])',
  )
  assert.match(statuses, /'queued'/)
  assert.match(statuses, /'running'/)
  assert.doesNotMatch(statuses, /'held'/)
  assert.match(queueProjection, /queueJob\?\.held \|\| publicJob\.held/)
  assert.match(
    generateButton,
    /job\.status === 'queued' \|\| job\.status === 'running' \|\| job\.held/,
  )
})
