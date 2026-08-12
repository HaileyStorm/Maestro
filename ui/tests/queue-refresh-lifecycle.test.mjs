import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { transform } from 'esbuild'

const mainUrl = new URL('../src/components/MainContent/MainContent.tsx', import.meta.url)

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

async function loadQueueRefreshLifecycle() {
  const source = await readFile(mainUrl, 'utf8')
  const start = source.indexOf('type QueueTabSnapshot')
  const end = source.indexOf('type ResourcePresentation', start)
  assert.notEqual(start, -1, 'queue refresh snapshot lifecycle must be present')
  assert.notEqual(end, -1, 'queue refresh snapshot lifecycle must have a bounded source region')
  const result = await transform(`${source.slice(start, end)}\nexport { queueRefreshIsStale, queueTabDisplayJobs, reduceQueueTabSnapshot }\n`, {
    format: 'esm',
    loader: 'ts',
    target: 'es2022',
  })
  return import(asDataModule(result.code))
}

function queueState(label, waiting, paused = false) {
  return {
    label,
    paused,
    pause_after_current: false,
    summary: {
      running: 1,
      waiting,
      held: 0,
      registering: 0,
      preparing: 0,
      approval_waiting: 0,
      active_total: waiting + 1,
    },
    jobs: [{ job_id: `${label}-job`, status: 'running', requested_outputs: 3 }],
  }
}

test('queue refresh retains the complete last success across transient failures', async () => {
  const { queueTabDisplayJobs, reduceQueueTabSnapshot } = await loadQueueRefreshLifecycle()
  const initial = { state: null, jobs: [], error: null, lastSuccessAt: null }
  const stateA = queueState('A', 4, true)
  const jobsA = [{ id: 'A-job', status: 'running' }]
  const successA = reduceQueueTabSnapshot(initial, {
    kind: 'success',
    state: stateA,
    jobs: jobsA,
    receivedAt: 1_725_000_000_000,
  })

  const staleA = reduceQueueTabSnapshot(successA, {
    kind: 'failure',
    error: 'Failed to load queue',
  })
  assert.strictEqual(staleA.state, stateA, 'rows and logical controls keep the scheduler snapshot')
  assert.strictEqual(staleA.jobs, jobsA, 'logical rows keep the matching /jobs snapshot')
  assert.strictEqual(staleA.state.summary, stateA.summary, 'totals are not reconstructed from /jobs')
  assert.equal(staleA.state.paused, true, 'machine-control state remains visible')
  assert.equal(staleA.lastSuccessAt, 1_725_000_000_000)
  assert.equal(staleA.error, 'Failed to load queue')
  assert.strictEqual(
    queueTabDisplayJobs(staleA, [{ id: 'newer-/jobs-row', status: 'failed' }]),
    jobsA,
    'a failed scheduler refresh cannot mix newer /jobs rows into snapshot A',
  )

  const stateB = queueState('B', 1)
  const jobsB = [{ id: 'B-job', status: 'running' }]
  const successB = reduceQueueTabSnapshot(staleA, {
    kind: 'success',
    state: stateB,
    jobs: jobsB,
    receivedAt: 1_725_000_015_000,
  })
  assert.strictEqual(successB.state, stateB, 'the next success atomically replaces stale data')
  assert.strictEqual(successB.jobs, jobsB)
  assert.equal(successB.error, null, 'the stale warning clears only on success')
  assert.equal(successB.lastSuccessAt, 1_725_000_015_000)
})

test('initial failure stays unavailable and abort or supersession is fenced', async () => {
  const { queueRefreshIsStale, queueTabDisplayJobs, reduceQueueTabSnapshot } = await loadQueueRefreshLifecycle()
  const initialFailure = reduceQueueTabSnapshot(
    { state: null, jobs: [], error: null, lastSuccessAt: null },
    { kind: 'failure', error: 'Authorization required' },
  )
  assert.equal(initialFailure.state, null)
  assert.equal(initialFailure.error, 'Authorization required')
  assert.deepEqual(
    queueTabDisplayJobs(initialFailure, [{ id: 'unverified-/jobs-row' }]),
    [],
    'initial queue failure never guesses scheduler contents from /jobs',
  )
  assert.equal(queueRefreshIsStale(7, 7, false), false)
  assert.equal(queueRefreshIsStale(6, 7, false), true, 'an older response cannot replace newer state')
  assert.equal(queueRefreshIsStale(7, 7, true), true, 'hidden/unmount aborts cannot announce failure')
})
