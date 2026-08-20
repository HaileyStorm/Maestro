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
const globalQueuePopover = await readFile(
  new URL('../src/components/GlobalQueuePopover.tsx', import.meta.url),
  'utf8',
)
const appShell = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8')

const ACTIVE_PROJECTION_STATUSES = new Set([
  'preparing',
  'waiting_for_plan_approval',
  'queued',
  'running',
])

function job(overrides) {
  return {
    id: 'job-1',
    status: 'queued',
    held: false,
    step: 0,
    totalSteps: 0,
    progress: 0,
    ...overrides,
  }
}

function projectHeldSummary(jobs, queueJobs = []) {
  const queueById = new Map(queueJobs.map(row => [row.job_id, row]))
  const summary = { held: 0, waiting: 0, running: 0, active_total: 0 }
  for (const publicJob of jobs) {
    if (!ACTIVE_PROJECTION_STATUSES.has(publicJob.status)) continue
    summary.active_total += 1
    const queueJob = queueById.get(publicJob.id)
    if (queueJob?.held || publicJob.held) summary.held += 1
    else if ((queueJob?.status ?? publicJob.status) === 'running') summary.running += 1
    else summary.waiting += 1
  }
  return summary
}

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

test('GlobalQueuePopover keeps Continuum held jobs without requiring status held', () => {
  const active = slice(
    globalQueuePopover,
    'const ACTIVE_JOB_STATUSES = new Set([',
    '])',
  )
  assert.match(active, /'queued'/)
  assert.match(active, /'running'/)
  assert.match(
    globalQueuePopover,
    /jobs\.filter\(job => ACTIVE_JOB_STATUSES\.has\(job\.status\) \|\| job\.held\)/,
  )
  assert.match(
    globalQueuePopover,
    /studioJobs\.filter\(job => job\.held \|\| job\.status === 'held'\)/,
  )
  assert.match(globalQueuePopover, /if \(studioHeldCount > 0\) await startStudioQueue\(\)/)
  assert.match(globalQueuePopover, /const label = job\.held/)
  assert.match(globalQueuePopover, /job\.message \|\| 'Ready - waiting for Start Queue'/)
  assert.doesNotMatch(
    globalQueuePopover,
    /release_held|_start_held_studio_queue|_run_held_studio_jobs/,
  )
})

test('Start queue releases Studio holds before Director dispatch', () => {
  const startAll = slice(globalQueuePopover, 'const startAllQueues = async () => {', 'return (')
  assert.match(startAll, /if \(startingAll\) return/)
  assert.match(startAll, /if \(studioHeldCount > 0\) await startStudioQueue\(\)/)
  assert.match(startAll, /if \(\s*startableDirectorCount > 0/)
  assert.match(startAll, /await startDirectorQueue\(\)/)
  const startButton = slice(
    globalQueuePopover,
    '{studioHeldCount > 0 ? (',
    ') : (',
  )
  assert.match(startButton, /Start queue/)
  assert.match(
    startButton,
    /Start all held Studio jobs, then any held Director projects/,
  )
})

test('App mounts GlobalQueuePopover on header and compact chrome', () => {
  assert.match(
    appShell,
    /import \{ GlobalQueuePopover \} from '\.\/components\/GlobalQueuePopover'/,
  )
  assert.match(appShell, /<GlobalQueuePopover iconSize=\{20\} panelAlign="header-edge" \/>/)
  assert.match(appShell, /<GlobalQueuePopover iconSize=\{16\} \/>/)
})

test('ListPlus hold stays off for Avatar and image_mode 4', () => {
  assert.match(
    generateButton,
    /const queueSupported = generationMode !== 'avatar' && imageMode !== 4/,
  )
  assert.match(generateButton, /if \(mode === 'queue' && !queueSupported\) return/)
  assert.match(
    generateButton,
    /Hold is unavailable for this Avatar or edit workflow/,
  )
})

test('client startStudioQueue posts Continuum jobs queue start', () => {
  const start = slice(
    client,
    'export async function startStudioQueue() {',
    'export type DirectorQueueEntry',
  )
  assert.match(start, /\$\{BASE\}\/api\/v1\/jobs\/queue\/start/)
  assert.match(start, /method: 'POST'/)
  assert.doesNotMatch(start, /release_held|_start_held_studio_queue/)
})

test('projectLogicalQueue counts queued Continuum holds via job.held', () => {
  const activeFn = slice(
    queueProjection,
    'export function isActiveLogicalQueueJob(job: GenerationJob): boolean {',
    '}',
  )
  assert.match(activeFn, /ACTIVE_STATUSES\.has\(job\.status\)/)
  assert.doesNotMatch(activeFn, /job\.held/)
  assert.match(queueProjection, /if \(queueJob\?\.held \|\| publicJob\.held\) summary\.held \+= 1/)

  const held = projectHeldSummary([
    job({ id: 'held-1', status: 'queued', held: true, message: 'Ready - waiting for Start Queue' }),
  ])
  assert.equal(held.held, 1)
  assert.equal(held.waiting, 0)
  assert.equal(held.running, 0)
  assert.equal(held.active_total, 1)

  const waiting = projectHeldSummary([job({ id: 'wait-1', status: 'queued', held: false })])
  assert.equal(waiting.held, 0)
  assert.equal(waiting.waiting, 1)

  const fromQueueRow = projectHeldSummary(
    [job({ id: 'held-2', status: 'queued', held: false })],
    [{ job_id: 'held-2', status: 'queued', held: true }],
  )
  assert.equal(fromQueueRow.held, 1)

  const leftoverStatus = projectHeldSummary([job({ id: 'legacy', status: 'held', held: true })])
  assert.equal(leftoverStatus.held, 0)
  assert.equal(leftoverStatus.active_total, 0)

  const completedHold = projectHeldSummary([job({ id: 'done-1', status: 'completed', held: true })])
  assert.equal(completedHold.held, 0)
  assert.equal(completedHold.active_total, 0)
})

test('GlobalQueuePopover studio filter treats job.held as sufficient', () => {
  const ACTIVE_JOB_STATUSES = new Set([
    'held',
    'preparing',
    'waiting_for_plan_approval',
    'queued',
    'running',
  ])
  const studioJobs = (jobs) => jobs.filter(row => ACTIVE_JOB_STATUSES.has(row.status) || row.held)
  const studioHeldCount = (jobs) => studioJobs(jobs).filter(row => row.held || row.status === 'held').length

  const queuedHold = [job({ id: 'q1', status: 'queued', held: true })]
  assert.equal(studioJobs(queuedHold).length, 1)
  assert.equal(studioHeldCount(queuedHold), 1)

  const leftoverStatus = [job({ id: 'legacy', status: 'held', held: false })]
  assert.equal(studioJobs(leftoverStatus).length, 1)
  assert.equal(studioHeldCount(leftoverStatus), 1)

  const completedHold = [job({ id: 'done', status: 'completed', held: true })]
  assert.equal(studioJobs(completedHold).length, 1)
  assert.equal(studioHeldCount(completedHold), 1)

  const finished = [job({ id: 'fin', status: 'completed', held: false })]
  assert.equal(studioJobs(finished).length, 0)
  assert.equal(studioHeldCount(finished), 0)
})
