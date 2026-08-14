import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'
import {
  decodeSampleCampaignQueue,
  fetchSampleCampaignQueue,
} from '../src/api/client.ts'

const UI_ROOT = fileURLToPath(new URL('..', import.meta.url))
const mainUrl = new URL('../src/components/MainContent/MainContent.tsx', import.meta.url)
const storeUrl = new URL('../src/stores/useStore.ts', import.meta.url)

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function arm(name, overrides = {}) {
  return {
    job_id: `job-${name}`,
    arm: name,
    status: 'queued',
    queue_held: true,
    recovery_state: 'sample_campaign_held',
    resource_state: 'queued',
    progress: 0,
    output_available: false,
    output_count: 0,
    ...overrides,
  }
}

function pairEntry(overrides = {}) {
  const entry = {
    pair: {
      schema_version: 1,
      pair_id: 'pair-001',
      case_id: 'case-001',
      arms: ['maestro', 'control'],
      shared_generation: {
        same_normalized_prompt: true,
        same_normalized_inputs: true,
        same_model_revision: true,
        same_settings: true,
        same_seed: true,
        same_output_index: true,
        model_revision: 'model-v1',
        seed: '42',
        output_index: 0,
        input_count: 1,
      },
      intervention_delta: {
        maestro_only: ['maestro.temporal_guidance', 'maestro.workflow_lock'],
        control_only: [],
      },
      evaluation: {
        evidence_class: 'manifest_only',
        vlm_verdict: 'not_reviewed',
        human_verdict: 'not_reviewed',
      },
    },
    queue_state: 'held',
    arms: [arm('maestro'), arm('control')],
  }
  return {
    ...entry,
    ...overrides,
    pair: { ...entry.pair, ...(overrides.pair || {}) },
    arms: overrides.arms || entry.arms,
  }
}

function projection(entries = [pairEntry()]) {
  return { schema_version: 1, pairs: entries }
}

function completedArm(name) {
  return arm(name, {
    status: 'completed',
    queue_held: false,
    recovery_state: 'terminal',
    resource_state: 'released',
    progress: 100,
    output_available: true,
    output_count: 1,
  })
}

function blockedArm(name) {
  return arm(name, {
    status: 'failed',
    queue_held: false,
    recovery_state: 'blocked',
    resource_state: 'blocked',
  })
}

function terminalFailedArm(name) {
  return arm(name, {
    status: 'failed',
    queue_held: false,
    recovery_state: 'terminal',
    resource_state: 'released',
  })
}

function clone(value) {
  return structuredClone(value)
}

test('strict decoder rebuilds only the exact bounded manifest-only pair projection', () => {
  const decoded = decodeSampleCampaignQueue(projection())
  assert.equal(decoded.schema_version, 1)
  assert.equal(decoded.pairs.length, 1)
  assert.deepEqual(decoded.pairs[0].arms.map(value => value.arm), ['maestro', 'control'])
  assert.equal(decoded.pairs[0].pair.evaluation.evidence_class, 'manifest_only')
  assert.equal(decoded.pairs[0].pair.shared_generation.seed, '42')
  assert.equal(JSON.stringify(decoded).includes('prompt'), true, 'same_normalized_prompt is public parity metadata')
  assert.equal(JSON.stringify(decoded).includes('/private/'), false)

  const unbound = pairEntry({
    queue_state: 'outputs_unbound',
    arms: [completedArm('maestro'), completedArm('control')],
  })
  assert.equal(decodeSampleCampaignQueue(projection([unbound])).pairs[0].queue_state, 'outputs_unbound')

  const blocked = pairEntry({
    queue_state: 'blocked',
    arms: [terminalFailedArm('maestro'), arm('control')],
  })
  assert.equal(decodeSampleCampaignQueue(projection([blocked])).pairs[0].arms[0].status, 'failed')

  const releasedQueue = pairEntry({
    queue_state: 'running_arm',
    arms: [
      arm('maestro', {
        queue_held: false,
        recovery_state: 'sample_campaign_released',
      }),
      arm('control'),
    ],
  })
  assert.equal(decodeSampleCampaignQueue(projection([releasedQueue])).pairs[0].queue_state, 'running_arm')

  const boundedOutputs = pairEntry({
    queue_state: 'outputs_unbound',
    arms: [
      completedArm('maestro'),
      { ...completedArm('control'), progress: 37, output_count: 1_000 },
    ],
  })
  assert.equal(decodeSampleCampaignQueue(projection([boundedOutputs])).pairs[0].arms[1].output_count, 1_000)

  const maxSeed = clone(projection())
  maxSeed.pairs[0].pair.shared_generation.seed = '18446744073709551615'
  assert.equal(
    decodeSampleCampaignQueue(maxSeed).pairs[0].pair.shared_generation.seed,
    '18446744073709551615',
  )
})

test('strict decoder rejects private additions, unsupported evidence, invalid lifecycle, and non-atomic pairs', () => {
  const cases = []

  const privateRoot = clone(projection())
  privateRoot.private_path = '/private/project/output.mp4'
  cases.push(privateRoot)

  const privatePair = clone(projection())
  privatePair.pairs[0].pair.prompt_digest = 'a'.repeat(64)
  cases.push(privatePair)

  const reviewed = clone(projection())
  reviewed.pairs[0].pair.evaluation.evidence_class = 'vlm_reviewed'
  reviewed.pairs[0].pair.evaluation.vlm_verdict = 'maestro_preferred'
  cases.push(reviewed)

  const reversed = clone(projection())
  reversed.pairs[0].arms.reverse()
  cases.push(reversed)

  const mismatchedOutputs = clone(projection())
  mismatchedOutputs.pairs[0].arms[0].output_available = true
  cases.push(mismatchedOutputs)

  const falseRunning = clone(projection())
  falseRunning.pairs[0].queue_state = 'running_arm'
  cases.push(falseRunning)

  const falseBlocked = clone(projection())
  falseBlocked.pairs[0].queue_state = 'blocked'
  cases.push(falseBlocked)

  for (const invalidSeed of [42, '01', '18446744073709551616']) {
    const invalid = clone(projection())
    invalid.pairs[0].pair.shared_generation.seed = invalidSeed
    cases.push(invalid)
  }

  const tooMany = projection(Array.from({ length: 101 }, (_, index) => pairEntry({
    pair: { pair_id: `pair-${String(index).padStart(3, '0')}` },
    arms: [
      arm('maestro', { job_id: `maestro-${index}` }),
      arm('control', { job_id: `control-${index}` }),
    ],
  })))
  cases.push(tooMany)

  for (const value of cases) {
    assert.throws(() => decodeSampleCampaignQueue(value), /response is invalid/)
  }
})

test('sample queue fetch is same-origin no-store and treats owner-hidden routes as absent', async () => {
  const previous = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init })
    return jsonResponse(projection())
  }
  try {
    const result = await fetchSampleCampaignQueue()
    assert.equal(result?.pairs.length, 1)
  } finally {
    globalThis.fetch = previous
  }
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, '/api/v1/sample-campaign/queue')
  assert.equal(calls[0].init.credentials, 'same-origin')
  assert.equal(calls[0].init.cache, 'no-store')
  assert.equal(calls[0].init.headers.Accept, 'application/json')

  for (const status of [403, 404]) {
    globalThis.fetch = async () => jsonResponse({ detail: 'hidden' }, status)
    try {
      assert.equal(await fetchSampleCampaignQueue(), null)
    } finally {
      globalThis.fetch = previous
    }
  }
})

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

let storeModulePromise
function buildStoreModule(cacheKey) {
  return build({
    stdin: {
      contents: "export { useStore } from './src/stores/useStore.ts'",
      resolveDir: UI_ROOT,
      loader: 'js',
    },
    bundle: true,
    format: 'esm',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
  }).then(result => import(`${asDataModule(result.outputFiles[0].text)}#${cacheKey}`))
}

function loadStoreModule() {
  if (!storeModulePromise) storeModulePromise = buildStoreModule('shared')
  return storeModulePromise
}

test('dedicated store slice clears failures and removes sample arms from generic reconnect cards', async () => {
  const { useStore } = await loadStoreModule()
  const previous = globalThis.fetch
  useStore.setState({
    sampleCampaignPairs: [],
    jobs: [
      { id: 'job-maestro', status: 'queued' },
      { id: 'ordinary-job', status: 'queued' },
    ],
    isGenerating: true,
  })
  globalThis.fetch = async () => jsonResponse(projection())
  try {
    await useStore.getState().refreshSampleCampaignQueue()
  } finally {
    globalThis.fetch = previous
  }
  assert.equal(useStore.getState().sampleCampaignPairs.length, 1)
  assert.deepEqual(useStore.getState().jobs.map(job => job.id), ['ordinary-job'])

  useStore.setState(state => ({
    jobs: [...state.jobs, { id: 'job-control', status: 'queued' }],
    isGenerating: true,
  }))
  globalThis.fetch = async () => jsonResponse({ schema_version: 1, pairs: [{ private_path: '/private' }] })
  try {
    await useStore.getState().refreshSampleCampaignQueue()
  } finally {
    globalThis.fetch = previous
  }
  assert.deepEqual(useStore.getState().sampleCampaignPairs, [], 'invalid data clears the prior pair')
  assert.deepEqual(useStore.getState().jobs.map(job => job.id), ['ordinary-job'])

  useStore.setState(state => ({
    sampleCampaignPairs: decodeSampleCampaignQueue(projection()).pairs,
    jobs: [...state.jobs, { id: 'job-maestro', status: 'queued' }],
    isGenerating: true,
  }))
  globalThis.fetch = async () => jsonResponse({ detail: 'not owner' }, 403)
  try {
    await useStore.getState().refreshSampleCampaignQueue()
  } finally {
    globalThis.fetch = previous
  }
  assert.deepEqual(useStore.getState().sampleCampaignPairs, [], '403 clears the prior pair')
  assert.deepEqual(useStore.getState().jobs.map(job => job.id), ['ordinary-job'])

  const reconnectCalls = []
  globalThis.fetch = async url => {
    reconnectCalls.push(String(url))
    return jsonResponse({ jobs: [{ job_id: 'job-maestro', status: 'running' }] })
  }
  try {
    await useStore.getState().reconnectJobs()
  } finally {
    globalThis.fetch = previous
  }
  assert.deepEqual(useStore.getState().jobs.map(job => job.id), ['ordinary-job'])
  assert.deepEqual(reconnectCalls, ['/api/v1/jobs'], '403 clears samples without disabling ordinary reconnect')

  let resolveLate
  globalThis.fetch = () => new Promise(resolve => { resolveLate = resolve })
  const lateRefresh = useStore.getState().refreshSampleCampaignQueue()
  useStore.getState().clearSampleCampaignQueue()
  resolveLate(jsonResponse(projection()))
  await lateRefresh
  globalThis.fetch = previous
  assert.deepEqual(useStore.getState().sampleCampaignPairs, [], 'late success cannot repopulate a cleared slice')
})

test('non-owner sample-route absence never disables ordinary job reconnect', async () => {
  const { useStore } = await buildStoreModule('non-owner-reconnect')
  const previous = globalThis.fetch
  const calls = []
  useStore.setState({
    accessContext: { remote: true, machine_controls: false },
    accountContext: {
      authenticated: true,
      reauthenticated: false,
      capabilities: ['account.self'],
    },
    jobs: [{ id: 'ordinary-job', status: 'queued' }],
    isGenerating: true,
  })
  globalThis.fetch = url => {
    const path = String(url)
    calls.push(path)
    if (path === '/api/v1/jobs') {
      return Promise.resolve(jsonResponse({ jobs: [] }))
    }
    return Promise.resolve(jsonResponse({}, 404))
  }
  try {
    await useStore.getState().reconnectJobs()
  } finally {
    globalThis.fetch = previous
  }
  assert.deepEqual(calls, ['/api/v1/jobs'])
  assert.deepEqual(useStore.getState().jobs.map(job => job.id), ['ordinary-job'])
})

let sampleSectionPromise
async function loadSampleSection() {
  if (sampleSectionPromise) return sampleSectionPromise
  const source = await readFile(mainUrl, 'utf8')
  const start = source.indexOf('function sampleInterventionLabel')
  const end = source.indexOf('function QueuePanel(', start)
  assert.notEqual(start, -1)
  assert.notEqual(end, -1)
  sampleSectionPromise = build({
    stdin: {
      contents: `${source.slice(start, end)}\nexport { SampleCampaignQueueSection }`,
      resolveDir: UI_ROOT,
      loader: 'tsx',
    },
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
    plugins: [{
      name: 'sample-queue-jsx-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({
          path: 'jsx-runtime', namespace: 'sample-queue-test',
        }))
        bundle.onLoad({ filter: /.*/, namespace: 'sample-queue-test' }, () => ({
          contents: `
            export const Fragment = Symbol.for('sample-fragment')
            export const jsx = (type, props, key) => ({ type, key, props: props || {} })
            export const jsxs = jsx
          `,
        }))
      },
    }],
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return sampleSectionPromise
}

function flattenElements(value, result = []) {
  if (Array.isArray(value)) {
    for (const child of value) flattenElements(child, result)
    return result
  }
  if (!value || typeof value !== 'object') return result
  if ('type' in value && 'props' in value) result.push(value)
  flattenElements(value.props?.children, result)
  return result
}

function elementText(value) {
  if (Array.isArray(value)) return value.map(elementText).join('')
  if (value == null || typeof value === 'boolean') return ''
  if (typeof value !== 'object') return String(value)
  if (typeof value.type === 'function') return elementText(value.type(value.props || {}))
  return elementText(value.props?.children)
}

test('paired owner section stays plain, responsive, review-honest, and control-free', async () => {
  const { SampleCampaignQueueSection } = await loadSampleSection()
  const decoded = decodeSampleCampaignQueue(projection([
    pairEntry({
      queue_state: 'outputs_unbound',
      arms: [completedArm('maestro'), completedArm('control')],
    }),
  ]))
  const tree = SampleCampaignQueueSection({ pairs: decoded.pairs })
  const elements = flattenElements(tree)
  const text = elementText(tree)
  assert.match(text, /Comparative samples/)
  assert.match(text, /Both runs finished, but their outputs are not yet linked as review evidence/)
  assert.match(text, /Maestro workflow/)
  assert.match(text, /Matched comparison workflow/)
  assert.match(text, /No visual-model review or owner review has been recorded yet/)
  assert.match(text, /Temporal guidance/)
  assert.equal(elements.some(element => element.type === 'button'), false)
  assert.ok(elements.some(element => /grid-cols-1/.test(element.props?.className || '')
    && /sm:grid-cols-2/.test(element.props?.className || '')))
  assert.ok(elements.some(element => /break-words/.test(element.props?.className || '')))
  assert.doesNotMatch(text, /pair-001|case-001|job-maestro|job-control|model-v1/)
  assert.doesNotMatch(text, /manifest_only|outputs_unbound|sample_campaign|sha256|private|digest/i)
  assert.doesNotMatch(text, /\b(?:Hold|Resume|Priority|Start next)\b/)

  const blocked = decodeSampleCampaignQueue(projection([pairEntry({
    queue_state: 'blocked',
    arms: [blockedArm('maestro'), arm('control')],
  })]))
  assert.match(
    elementText(SampleCampaignQueueSection({ pairs: blocked.pairs })),
    /stopped before both sides were ready/i,
  )

  const controlOnly = decodeSampleCampaignQueue(projection([pairEntry({
    pair: {
      intervention_delta: {
        maestro_only: [],
        control_only: ['comparison.extra_pass'],
      },
    },
  })]))
  const controlOnlyText = elementText(SampleCampaignQueueSection({ pairs: controlOnly.pairs }))
  assert.match(controlOnlyText, /Matched runs compare two workflow variants using the same generation setup\./)
  assert.match(controlOnlyText, /The comparison adds Extra pass; Maestro runs without those changes\./)
  assert.doesNotMatch(controlOnlyText, /without selected Maestro changes/i)

  const readyToStart = decodeSampleCampaignQueue(projection([pairEntry({
    queue_state: 'running_arm',
    arms: [
      arm('maestro', {
        queue_held: false,
        recovery_state: 'sample_campaign_released',
      }),
      arm('control'),
    ],
  })]))
  const readyText = elementText(SampleCampaignQueueSection({ pairs: readyToStart.pairs }))
  assert.match(readyText, /One side is running or ready to start\./)
  assert.doesNotMatch(readyText, /Generating one side/)
})

test('campaign refresh shares the existing visibility loop and never enters ordinary queue APIs', async () => {
  const [main, store] = await Promise.all([
    readFile(mainUrl, 'utf8'),
    readFile(storeUrl, 'utf8'),
  ])
  const refreshStart = main.indexOf('const refreshQueue = useCallback')
  const refreshEnd = main.indexOf('useEffect(() => {', refreshStart)
  const refreshRegion = main.slice(refreshStart, refreshEnd)
  assert.match(refreshRegion, /Promise\.all\(\[/)
  assert.match(refreshRegion, /fetchQueueState\(controller\.signal\)/)
  assert.match(refreshRegion, /refreshSampleCampaignQueue\(controller\.signal\)/)
  assert.match(main, /sampleCampaignJobIds/)
  assert.match(main, /jobs: queueState\.jobs\.filter\(job => !sampleJobIds\.has\(job\.job_id\)\)/)

  const sampleSliceStart = store.indexOf('sampleCampaignPairs: [],\n  refreshSampleCampaignQueue:')
  const sampleSliceEnd = store.indexOf('pendingH3Plan: null', sampleSliceStart)
  const sampleSlice = store.slice(sampleSliceStart, sampleSliceEnd)
  assert.match(sampleSlice, /fetchSampleCampaignQueue\(signal\)/)
  assert.match(sampleSlice, /sampleCampaignPairs: \[\]/)
  assert.doesNotMatch(sampleSlice, /localStorage|sessionStorage|reconcileQueueState|_pollRecoveredJob/)
})
