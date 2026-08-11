import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

const uiRoot = new URL('..', import.meta.url)
const storeUrl = new URL('../src/stores/useStore.ts', import.meta.url)
const mainUrl = new URL('../src/components/MainContent/MainContent.tsx', import.meta.url)

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

async function loadStoreMappers() {
  const source = `${await readFile(storeUrl, 'utf8')}\nexport { _jobStatusDetails, _mergeJobStatus, _newGenerationJobFromStatus, _queueJobDetails }\n`
  const result = await build({
    stdin: {
      contents: source,
      resolveDir: new URL('../src/stores/', import.meta.url).pathname,
      loader: 'ts',
    },
    bundle: true,
    format: 'esm',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
  })
  return import(asDataModule(result.outputFiles[0].text))
}

async function loadJobPlaceholder() {
  const source = `${await readFile(mainUrl, 'utf8')}\nexport { JobPlaceholder, QueuePanel }\n`
  const result = await build({
    stdin: {
      contents: source,
      resolveDir: new URL('../src/components/MainContent/', import.meta.url).pathname,
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
      name: 'resource-wait-ui-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'resource-wait' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'resource-wait' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide', namespace: 'resource-wait' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'resource-wait' }))
        bundle.onResolve({ filter: /^(\.\/TabFilter|\.\/ThumbnailGallery|\.\/MediaFeedItem|\.\.\/LlmChat|\.\.\/H3DeliveryRecoveryStatus)$/ }, () => ({ path: 'components', namespace: 'resource-wait' }))
        bundle.onResolve({ filter: /api\/client$/ }, () => ({ path: 'api', namespace: 'resource-wait' }))
        bundle.onResolve({ filter: /lib\/(privatePreview|useVisibilityPolling)$/ }, () => ({ path: 'lib', namespace: 'resource-wait' }))
        bundle.onResolve({ filter: /lib\/clipboard$/ }, () => ({ path: 'clipboard', namespace: 'resource-wait' }))
        bundle.onLoad({ filter: /.*/, namespace: 'resource-wait' }, args => {
          if (args.path === 'react') {
            return { contents: `
              export const useCallback = callback => callback
              export const useEffect = () => {}
              export const useId = () => 'resource-wait-id'
              export const useLayoutEffect = () => {}
              export const useMemo = callback => callback()
              export const useRef = initial => ({ current: initial })
              export const useState = initial => {
                const fallback = typeof initial === 'function' ? initial() : initial
                const value = globalThis.__resourceWaitStateOverrides?.length
                  ? globalThis.__resourceWaitStateOverrides.shift()
                  : fallback
                return [value, update => globalThis.__resourceWaitStateUpdates?.push(update)]
              }
            ` }
          }
          if (args.path === 'jsx-runtime') {
            return { contents: `
              export const Fragment = Symbol.for('resource-wait-fragment')
              export const jsx = (type, props, key) => ({ type, key, props: props || {} })
              export const jsxs = jsx
            ` }
          }
          if (args.path === 'lucide') {
            return { contents: `
              export const Film = 'Film', Play = 'Play', Square = 'Square', FolderOpen = 'FolderOpen', Plus = 'Plus', Check = 'Check', Loader2 = 'Loader2', X = 'X', BookMarked = 'BookMarked', Upload = 'Upload', Trash2 = 'Trash2', ListChecks = 'ListChecks', Eye = 'Eye', EyeOff = 'EyeOff', FolderInput = 'FolderInput', Lock = 'Lock', LockOpen = 'LockOpen', KeyRound = 'KeyRound', Pause = 'Pause', ArrowUp = 'ArrowUp', ArrowDown = 'ArrowDown'
            ` }
          }
          if (args.path === 'components') {
            return { contents: `
              export const TabFilter = () => null, ThumbnailGallery = () => null, MediaFeedItem = () => null, LlmChat = () => null, H3DeliveryRecoveryStatus = () => null
              export const OPEN_GALLERY_EVENT = 'open-gallery'
            ` }
          }
          if (args.path === 'api') {
            return { contents: `
              const record = (name, ...args) => globalThis.__resourceWaitApiCalls?.push([name, ...args])
              export const isBackendJobId = jobId => /^[0-9a-f]{8}$/i.test(jobId)
              export const fetchJobLog = async () => ({ events: [] })
              export const resumeQueue = async () => record('resumeQueue'), pauseQueueAfterOutput = async value => record('pauseQueueAfterOutput', value), setQueueOutputCount = async (id, value) => record('setQueueOutputCount', id, value), startQueueJobNext = async id => record('startQueueJobNext', id), setQueuePriority = async (id, value) => record('setQueuePriority', id, value), resumeQueueJob = async id => record('resumeQueueJob', id), holdQueueJob = async id => record('holdQueueJob', id)
            ` }
          }
          if (args.path === 'lib') {
            return { contents: `
              export const privatePreviewIdentity = () => '', privatePreviewWorkspaceHasRevealed = () => false, setPrivatePreviewsForWorkspaceRevealed = () => {}, subscribePrivatePreviewChanges = () => () => {}, boundedBackoffDelay = () => 0, useVisibilityPolling = () => {}
              export const POLL_INTERVAL_MS = {}
            ` }
          }
          if (args.path === 'clipboard') {
            return { contents: `
              export const copyTextToClipboard = async value => {
                globalThis.__resourceWaitCopiedText = value
                return globalThis.__resourceWaitCopyResult !== false
              }
            ` }
          }
          return { contents: 'export const useStore = selector => selector(globalThis.__resourceWaitStore)' }
        })
      },
    }],
  })
  return import(asDataModule(result.outputFiles[0].text))
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
  return elementText(value.props?.children)
}

const descriptor = {
  intent: 'generation',
  execution: 'standard',
  preemptible: false,
  preemption_mode: 'none',
  state: 'queued',
  execution_attempt: 1,
}

const cpuDescriptor = {
  intent: 'text',
  execution: 'cpu',
  preemptible: true,
  preemption_mode: 'discard_restart',
  state: 'running',
  execution_attempt: 7,
}

test('status and queue mappers preserve the bounded resource descriptor across legacy responses', async () => {
  const { _jobStatusDetails, _queueJobDetails } = await loadStoreMappers()
  const fromStatus = _jobStatusDetails({
    status: 'queued',
    queue_wait_reason: 'resource_wait',
    resource_descriptor: descriptor,
  })
  const fromQueue = _queueJobDetails({
    status: 'queued',
    wait_reason: 'resource_wait',
    resource_descriptor: descriptor,
  })

  assert.equal(fromStatus.queueWaitReason, 'resource_wait')
  assert.deepEqual(fromStatus.resourceDescriptor, descriptor)
  assert.equal(fromQueue.queueWaitReason, 'resource_wait')
  assert.deepEqual(fromQueue.resourceDescriptor, descriptor)

  const legacyStatus = _jobStatusDetails({ status: 'queued', queue_wait_reason: 'waiting_for_turn' })
  const legacyQueue = _queueJobDetails({ status: 'queued', wait_reason: 'waiting_for_turn' })
  assert.equal(Object.hasOwn(legacyStatus, 'resourceDescriptor'), false)
  assert.equal(Object.hasOwn(legacyQueue, 'resourceDescriptor'), false)
  assert.deepEqual({ resourceDescriptor: descriptor, ...legacyStatus }.resourceDescriptor, descriptor)
  assert.deepEqual({ resourceDescriptor: descriptor, ...legacyQueue }.resourceDescriptor, descriptor)

  const oldPartialDescriptor = {
    intent: 'generation',
    execution: 'standard',
    preemptible: false,
  }
  const normalizedLegacy = _jobStatusDetails({
    status: 'running',
    resource_descriptor: oldPartialDescriptor,
  })
  assert.deepEqual(normalizedLegacy.resourceDescriptor, {
    ...descriptor,
    state: 'running',
  })
})

test('discard-restart attempt changes reset CPU progress and ETA across status and queue paths', async () => {
  const { _mergeJobStatus, _newGenerationJobFromStatus, _queueJobDetails } = await loadStoreMappers()
  const previous = {
    id: 'cpu-text',
    status: 'running',
    progress: 0.72,
    step: 72,
    totalSteps: 100,
    phase: 'cpu_decode',
    message: 'CPU text',
    outputFiles: [],
    error: null,
    windowStep: 72,
    windowTotalSteps: 100,
    windowProgress: 72,
    overallProgress: 72,
    etaSeconds: 40,
    subtaskEtaSeconds: 35,
    resourceDescriptor: cpuDescriptor,
  }
  const restartDescriptor = {
    ...cpuDescriptor,
    execution: 'standard',
    preemptible: false,
    preemption_mode: 'none',
    state: 'restarting_on_accelerator',
    execution_attempt: 8,
  }
  const status = {
    job_id: previous.id,
    status: 'running',
    progress: 72,
    step: 72,
    total_steps: 100,
    phase: 'old_cpu_phase',
    message: 'Restarting',
    output_files: [],
    error: null,
    window_step: 72,
    window_total_steps: 100,
    window_progress: 72,
    overall_progress: 72,
    queue_wait_reason: 'running',
    resource_descriptor: restartDescriptor,
    eta_seconds: null,
    subtask_eta_seconds: null,
  }
  const merged = _mergeJobStatus(previous, status)
  assert.equal(merged.progress, 0)
  assert.equal(merged.step, 0)
  assert.equal(merged.totalSteps, 0)
  assert.equal(merged.phase, '')
  assert.equal(merged.windowStep, 0)
  assert.equal(merged.windowProgress, 0)
  assert.equal(merged.overallProgress, 0)
  assert.equal(merged.etaSeconds, null)
  assert.equal(merged.subtaskEtaSeconds, null)
  assert.equal(merged.progressIndeterminate, true)
  assert.equal(merged.resourceDescriptor.execution_attempt, 8)

  const fromQueue = _queueJobDetails({
    job_id: previous.id,
    status: 'running',
    wait_reason: 'running',
    resource_descriptor: restartDescriptor,
    eta_seconds: null,
    subtask_eta_seconds: null,
  }, previous)
  assert.equal(fromQueue.progress, 0)
  assert.equal(fromQueue.step, 0)
  assert.equal(fromQueue.phase, '')
  assert.equal(fromQueue.etaSeconds, null)
  assert.equal(fromQueue.progressIndeterminate, true)

  const reconnected = _newGenerationJobFromStatus(status)
  assert.equal(reconnected.progress, 0)
  assert.equal(reconnected.step, 0)
  assert.equal(reconnected.totalSteps, 0)
  assert.equal(reconnected.phase, '')
  assert.equal(reconnected.windowStep, 0)
  assert.equal(reconnected.windowProgress, 0)
  assert.equal(reconnected.overallProgress, 0)
  assert.equal(reconnected.etaSeconds, null)
  assert.equal(reconnected.progressIndeterminate, true)

  const stale = _mergeJobStatus(merged, {
    ...status,
    resource_descriptor: cpuDescriptor,
    progress: 99,
    eta_seconds: 1,
  })
  assert.equal(stale, merged)
})

test('status, jobs, and queue mappings preserve plan-terms wait and owner child relation parity', async () => {
  const { _jobStatusDetails, _queueJobDetails } = await loadStoreMappers()
  const fromStatusOrJobs = _jobStatusDetails({
    status: 'waiting_for_plan_approval',
    queue_wait_reason: 'waiting_for_plan_terms',
    plan_review_terms_required: true,
    parent_job_id: 'reference-parent',
  })
  const fromQueue = _queueJobDetails({
    status: 'waiting_for_plan_approval',
    wait_reason: 'waiting_for_plan_terms',
    plan_review_terms_required: true,
    parent_job_id: 'reference-parent',
  })

  assert.equal(fromStatusOrJobs.queueWaitReason, 'waiting_for_plan_terms')
  assert.equal(fromStatusOrJobs.planReviewTermsRequired, true)
  assert.equal(fromStatusOrJobs.parentJobId, 'reference-parent')
  assert.equal(fromQueue.queueWaitReason, 'waiting_for_plan_terms')
  assert.equal(fromQueue.planReviewTermsRequired, true)
  assert.equal(fromQueue.parentJobId, 'reference-parent')

  const legacyStatus = _jobStatusDetails({ status: 'queued', queue_wait_reason: 'waiting_for_turn' })
  const legacyQueue = _queueJobDetails({ status: 'queued', wait_reason: 'waiting_for_turn' })
  assert.equal(Object.hasOwn(legacyStatus, 'parentJobId'), false)
  assert.equal(Object.hasOwn(legacyQueue, 'parentJobId'), false)
})

test('status mappings preserve terminal Reference child correlation and allowlist failure details', async () => {
  const { _jobStatusDetails, _newGenerationJobFromStatus } = await loadStoreMappers()
  const status = {
    job_id: 'faceb00c',
    status: 'failed',
    progress: 0,
    step: 0,
    total_steps: 0,
    phase: 'reference_generation',
    message: 'Reference generation failed',
    output_files: [],
    error: 'Reference child failed',
    failed_child_job_id: 'deadbeef',
    failed_child_status: 'failed',
    failed_child_reason: 'reference_child_failed',
    failure_details: {
      code: 'reference_image_generation_failed',
      detail: 'The image worker stopped before publishing an output.',
      traceback: '/private/path/worker.py:17',
      nested: { raw: 'must not cross the UI mapping boundary' },
    },
  }

  const details = _jobStatusDetails(status)
  assert.equal(details.failedChildJobId, 'deadbeef')
  assert.equal(details.failedChildStatus, 'failed')
  assert.equal(details.failedChildReason, 'reference_child_failed')
  assert.deepEqual(details.failureDetails, {
    code: 'reference_image_generation_failed',
    detail: 'The image worker stopped before publishing an output.',
  })

  const reconnected = _newGenerationJobFromStatus(status)
  assert.equal(reconnected.failedChildJobId, 'deadbeef')
  assert.deepEqual(reconnected.failureDetails, details.failureDetails)

  const legacy = _jobStatusDetails({ status: 'failed' })
  assert.equal(Object.hasOwn(legacy, 'failedChildJobId'), false)
  assert.equal(Object.hasOwn(legacy, 'failureDetails'), false)
})

test('resource-wait job card renders durable queued copy without execution warnings', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
  }
  t.after(() => { globalThis.__resourceWaitStore = previousStore })

  const { JobPlaceholder } = await loadJobPlaceholder()
  const tree = JobPlaceholder({
    job: {
      id: 'owned-resource-job',
      status: 'queued',
      progress: 0,
      step: 0,
      totalSteps: 0,
      phase: '',
      message: 'Queued',
      outputFiles: [],
      error: null,
      queueWaitReason: 'resource_wait',
      resourceDescriptor: descriptor,
    },
    onStop() {},
    onDismiss() {},
  })
  const elements = flattenElements(tree)
  const wait = elements.find(element => elementText(element) === 'Waiting for generation resources')
  assert.ok(wait)
  assert.equal(
    wait.props.title,
    'This job is durably queued and will start when the generation lane is available.',
  )
  const renderedText = elementText(tree)
  assert.match(renderedText, /GPU generation queued/)
  assert.doesNotMatch(renderedText, /CPU|restart|fairness|residen|preempt/i)
})

test('backend job cards expose a compact accessible copy control for the Job ID', async t => {
  const previousStore = globalThis.__resourceWaitStore
  const previousCopiedText = globalThis.__resourceWaitCopiedText
  const previousStateUpdates = globalThis.__resourceWaitStateUpdates
  const previousStateOverrides = globalThis.__resourceWaitStateOverrides
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
  }
  globalThis.__resourceWaitStateUpdates = []
  t.after(() => {
    globalThis.__resourceWaitStore = previousStore
    globalThis.__resourceWaitCopiedText = previousCopiedText
    globalThis.__resourceWaitStateUpdates = previousStateUpdates
    globalThis.__resourceWaitStateOverrides = previousStateOverrides
  })

  const { JobPlaceholder } = await loadJobPlaceholder()
  const tree = JobPlaceholder({
    job: {
      id: 'faceb00c',
      status: 'queued',
      progress: 0,
      step: 0,
      totalSteps: 0,
      phase: '',
      message: 'Queued',
      outputFiles: [],
      error: null,
    },
    onStop() {},
    onDismiss() {},
  })
  const elements = flattenElements(tree)
  const copyable = elements.find(element => element.type?.name === 'CopyableJobId')
  assert.ok(copyable)
  const copyTree = copyable.type(copyable.props)
  const copyElements = flattenElements(copyTree)
  const copy = copyElements.find(element => element.props?.['aria-label'] === 'Copy job id faceb00c')
  assert.ok(copy)
  assert.match(elementText(copy), /Job IDfaceb00cCopy/)
  assert.ok(copyElements.find(element => element.props?.role === 'status' && element.props?.['aria-live'] === 'polite'))

  copy.props.onClick()
  await Promise.resolve()
  await Promise.resolve()
  assert.equal(globalThis.__resourceWaitCopiedText, 'faceb00c')
  assert.deepEqual(globalThis.__resourceWaitStateUpdates, ['copied'])

  globalThis.__resourceWaitStateOverrides = ['copied']
  const copiedTree = copyable.type(copyable.props)
  assert.match(elementText(copiedTree), /Job IDfaceb00cCopied/)
  assert.match(elementText(copiedTree), /Job ID faceb00c copied/)
})

test('Reference parent failure renders only allowlisted child diagnostics', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
  }
  t.after(() => { globalThis.__resourceWaitStore = previousStore })

  const { JobPlaceholder } = await loadJobPlaceholder()
  const tree = JobPlaceholder({
    job: {
      id: 'faceb00c',
      status: 'failed',
      progress: 0,
      step: 0,
      totalSteps: 0,
      phase: 'reference_generation',
      message: 'Reference generation failed',
      outputFiles: [],
      error: 'Reference child failed',
      failedChildJobId: 'deadbeef',
      failedChildStatus: 'failed',
      failedChildReason: 'reference_child_failed',
      failureDetails: {
        code: 'reference_image_generation_failed',
        detail: 'The image worker stopped before publishing an output.',
        traceback: '/private/path/worker.py:17',
      },
    },
    onStop() {},
    onDismiss() {},
  })
  const text = elementText(tree)
  assert.match(text, /Reference Generation Failed/)
  const childId = flattenElements(tree).find(element => (
    element.type?.name === 'CopyableJobId' && element.props?.jobId === 'deadbeef'
  ))
  assert.equal(childId?.props.label, 'Child job ID')
  assert.match(text, /Child status: failed/)
  assert.match(text, /Reason: reference_child_failed/)
  assert.match(text, /Code: reference_image_generation_failed/)
  assert.match(text, /Detail: The image worker stopped before publishing an output\./)
  assert.doesNotMatch(text, /private\/path|traceback|nested|raw/)
})

test('preemptible CPU-only owner card is visibly slower and keeps an unknown ETA unknown', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
  }
  t.after(() => { globalThis.__resourceWaitStore = previousStore })

  const { JobPlaceholder } = await loadJobPlaceholder()
  const tree = JobPlaceholder({
    job: {
      id: 'cpu-text',
      status: 'running',
      progress: 0.4,
      step: 4,
      totalSteps: 10,
      phase: 'Prompt enhancement',
      message: 'Enhancing',
      outputFiles: [],
      error: null,
      etaSeconds: null,
      resourceDescriptor: cpuDescriptor,
    },
    onStop() {},
    onDismiss() {},
  })
  const renderedText = elementText(tree)
  assert.match(renderedText, /CPU-only text · slower/)
  assert.match(renderedText, /Slower CPU work may be discarded and restarted only when acceleration is predicted to deliver sooner/)
  assert.match(renderedText, /Overall ETA unknown/)
  const badge = flattenElements(tree).find(element => elementText(element) === 'CPU-only text · slower')
  assert.equal(
    badge?.props.title,
    'CPU text is slower and may be discarded and restarted with acceleration only when that is predicted to deliver sooner.',
  )
})

test('ordinary CPU-only owner card never implies discard or restart', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
  }
  t.after(() => { globalThis.__resourceWaitStore = previousStore })

  const { JobPlaceholder } = await loadJobPlaceholder()
  const tree = JobPlaceholder({
    job: {
      id: 'ordinary-cpu-text',
      status: 'running',
      progress: 0.4,
      step: 4,
      totalSteps: 10,
      phase: 'Prompt enhancement',
      message: 'Enhancing',
      outputFiles: [],
      error: null,
      etaSeconds: null,
      resourceDescriptor: {
        ...cpuDescriptor,
        preemptible: false,
        preemption_mode: 'none',
      },
    },
    onStop() {},
    onDismiss() {},
  })
  const renderedText = elementText(tree)
  assert.match(renderedText, /CPU-only text · slower/)
  assert.match(renderedText, /Overall ETA unknown/)
  assert.doesNotMatch(renderedText, /discard|restart|deliver sooner/i)
  const badge = flattenElements(tree).find(element => (
    elementText(element) === 'CPU-only text · slower' && element.props.title
  ))
  assert.equal(
    badge?.props.title,
    'This text step is using CPU-only execution, which is slower than acceleration.',
  )
})

test('preemption, release, and acceleration restart states never present CPU progress as reusable', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
  }
  t.after(() => { globalThis.__resourceWaitStore = previousStore })

  const { JobPlaceholder } = await loadJobPlaceholder()
  const expected = [
    ['preemption_requested', 'Acceleration restart requested', 'CPU progress will be discarded before restart'],
    ['resources_releasing', 'Releasing CPU resources', 'CPU progress is discarded'],
    ['restarting_on_accelerator', 'Restarting with acceleration', 'ETA remains unknown until measured'],
  ]
  for (const [state, label, warning] of expected) {
    const tree = JobPlaceholder({
      job: {
        id: `cpu-${state}`,
        status: 'running',
        progress: state === 'preemption_requested' ? 0.4 : 0,
        step: state === 'preemption_requested' ? 4 : 0,
        totalSteps: state === 'preemption_requested' ? 10 : 0,
        phase: '',
        message: '',
        outputFiles: [],
        error: null,
        etaSeconds: null,
        resourceDescriptor: {
          ...cpuDescriptor,
          execution: state === 'restarting_on_accelerator' ? 'standard' : 'cpu',
          preemptible: state === 'preemption_requested',
          state,
          execution_attempt: state === 'restarting_on_accelerator' ? 8 : 7,
        },
      },
      onStop() {},
      onDismiss() {},
    })
    assert.match(elementText(tree), new RegExp(label))
    assert.match(elementText(tree), new RegExp(warning))
    assert.match(elementText(tree), /Overall ETA unknown/)
  }
})

test('legacy host-wait copy remains distinct from resource admission', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
  }
  t.after(() => { globalThis.__resourceWaitStore = previousStore })

  const { JobPlaceholder } = await loadJobPlaceholder()
  const tree = JobPlaceholder({
    job: {
      id: 'legacy-host-wait',
      status: 'queued',
      progress: 0,
      step: 0,
      totalSteps: 0,
      phase: '',
      message: 'Queued',
      outputFiles: [],
      error: null,
      queueWaitReason: 'waiting_for_active_generation',
    },
    onStop() {},
    onDismiss() {},
  })
  const elements = flattenElements(tree)
  const wait = elements.find(element => elementText(element) === 'Waiting for another generation on this host')
  assert.ok(wait)
  assert.equal(wait.props.title, undefined)
})

test('plan-terms wait renders explicit bounded card copy instead of a generic phase', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    hostTerms: { minimax_h3_ref2va: { accepted: false } },
  }
  t.after(() => { globalThis.__resourceWaitStore = previousStore })

  const { JobPlaceholder } = await loadJobPlaceholder()
  const tree = JobPlaceholder({
    job: {
      id: 'terms-wait',
      status: 'waiting_for_plan_approval',
      progress: 0,
      step: 0,
      totalSteps: 0,
      phase: 'internal_phase_name',
      message: 'Internal message',
      outputFiles: [],
      error: null,
      queueWaitReason: 'waiting_for_plan_terms',
      planReviewTermsRequired: true,
    },
    onStop() {},
    onDismiss() {},
  })
  const renderedText = elementText(tree)
  assert.match(renderedText, /Waiting for required model terms/)
  assert.match(renderedText, /Approval required to accept model terms/)
  assert.doesNotMatch(renderedText, /Ref2VA/)
  assert.doesNotMatch(renderedText, /Review plan/)
  assert.doesNotMatch(renderedText, /internal_phase_name|Internal message/)
})

test('owner queue row shows the same resource wait while global summary stays aggregate-only', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    resumeJobRecovery() {},
    retryJobRecovery() {},
    openH3PlanReview() {},
    h3PlanReviewError: null,
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
  }
  t.after(() => { globalThis.__resourceWaitStore = previousStore })

  const { QueuePanel } = await loadJobPlaceholder()
  const summary = {
    running: 0,
    waiting: 1,
    held: 0,
    registering: 0,
    preparing: 0,
    approval_waiting: 0,
    active_total: 2,
  }
  const tree = QueuePanel({
    jobs: [{
      id: 'owned-resource-job',
      status: 'queued',
      progress: 0,
      step: 0,
      totalSteps: 0,
      phase: '',
      message: 'Queued',
      outputFiles: [],
      error: null,
      queueWaitReason: 'resource_wait',
      resourceDescriptor: descriptor,
    }],
    onStop() {},
    onDismiss() {},
    queueError: null,
    refreshQueue: async () => {},
    queue: {
      paused: false,
      pause_after_current: false,
      summary,
      jobs: [{
        job_id: 'owned-resource-job',
        status: 'queued',
        priority: 0,
        held: false,
        hold_after_output: false,
        position: 1,
        wait_reason: 'resource_wait',
        resource_descriptor: descriptor,
        queue_reorder_reason: 'queue_order',
        queue_residency_bypass_count: 0,
        queue_residency_bypassed_waiters: 0,
        requested_outputs: 1,
        produced_outputs: 0,
      }],
    },
  })
  const elements = flattenElements(tree)
  const ownerRow = elements.find(element => (
    element.props?.title === 'This job is durably queued and will start when the generation lane is available.'
    && elementText(element).includes('Waiting for generation resources')
  ))
  assert.ok(ownerRow)
  const summaryElement = elements.find(element => (
    elementText(element) === '0 running · 0 preparing · 0 awaiting review · 1 waiting · 0 held · 0 registering'
  ))
  assert.ok(summaryElement)
  assert.doesNotMatch(elementText(summaryElement), /resource|generation lane|standard|preempt/i)
})

test('owner queue row exposes CPU preemption truth while queue help stays bounded and candid', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    resumeJobRecovery() {},
    retryJobRecovery() {},
    openH3PlanReview() {},
    h3PlanReviewError: null,
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
  }
  t.after(() => { globalThis.__resourceWaitStore = previousStore })

  const { QueuePanel } = await loadJobPlaceholder()
  const preempting = {
    ...cpuDescriptor,
    state: 'preemption_requested',
  }
  const summary = {
    running: 1,
    waiting: 0,
    held: 0,
    registering: 0,
    preparing: 0,
    approval_waiting: 0,
    active_total: 3,
  }
  const tree = QueuePanel({
    jobs: [{
      id: 'cpu-preempting',
      status: 'running',
      progress: 0.5,
      step: 5,
      totalSteps: 10,
      phase: 'Prompt enhancement',
      message: 'Enhancing',
      outputFiles: [],
      error: null,
      resourceDescriptor: preempting,
      etaSeconds: null,
    }],
    onStop() {},
    onDismiss() {},
    queueError: null,
    refreshQueue: async () => {},
    queue: {
      paused: false,
      pause_after_current: false,
      summary,
      jobs: [{
        job_id: 'cpu-preempting',
        status: 'running',
        priority: 1,
        held: false,
        hold_after_output: false,
        position: null,
        wait_reason: 'running',
        resource_descriptor: preempting,
        queue_reorder_reason: 'queue_order',
        queue_residency_bypass_count: 0,
        queue_residency_bypassed_waiters: 0,
        requested_outputs: 1,
        produced_outputs: 0,
      }],
    },
  })
  const elements = flattenElements(tree)
  const rowBadge = elements.find(element => (
    element.props?.['data-resource-state'] === 'preemption_requested'
    && elementText(element) === 'Acceleration restart requested'
  ))
  assert.ok(rowBadge)
  assert.match(rowBadge.props.title, /discarded and restarted from zero/)

  const renderedText = elementText(tree)
  assert.match(renderedText, /How queue priority works/)
  assert.match(renderedText, /User-set priority is applied before queue-order and residency choices/)
  assert.match(renderedText, /Reusing the exact loaded model can reorder otherwise eligible work/)
  assert.match(renderedText, /Recent-service fair share is planned but is not active in this build/)
  assert.match(renderedText, /Only CPU work explicitly marked preemptible may be discarded and restarted from zero, and only when acceleration is predicted to deliver sooner/)
  assert.doesNotMatch(renderedText, /user[_ -]?id|device[_ -]?id|model[_ -]?id|VRAM|RAM|hostname|path/i)

  const summaryElement = elements.find(element => (
    elementText(element) === '1 running · 0 preparing · 0 awaiting review · 0 waiting · 0 held · 0 registering'
  ))
  assert.ok(summaryElement)
  assert.doesNotMatch(elementText(summaryElement), /CPU|GPU|resource|model|fair|preempt/i)
})

test('owner queue row renders the exact plan-terms wait reason', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    resumeJobRecovery() {},
    retryJobRecovery() {},
    openH3PlanReview() {},
    h3PlanReviewError: null,
    hostTerms: { minimax_h3_ref2va: { accepted: false } },
  }
  t.after(() => { globalThis.__resourceWaitStore = previousStore })

  const { QueuePanel } = await loadJobPlaceholder()
  const job = {
    id: 'terms-wait',
    status: 'waiting_for_plan_approval',
    progress: 0,
    step: 0,
    totalSteps: 0,
    phase: 'internal_phase_name',
    message: 'Internal message',
    outputFiles: [],
    error: null,
    queueWaitReason: 'waiting_for_plan_terms',
    planReviewTermsRequired: true,
  }
  const queueJob = {
    job_id: job.id,
    status: 'waiting_for_plan_approval',
    priority: 0,
    held: false,
    hold_after_output: false,
    position: null,
    wait_reason: 'waiting_for_plan_terms',
    plan_review_terms_required: true,
    queue_reorder_reason: 'queue_order',
    queue_residency_bypass_count: 0,
    queue_residency_bypassed_waiters: 0,
    requested_outputs: 1,
    produced_outputs: 0,
  }
  const tree = QueuePanel({
    jobs: [job],
    onStop() {},
    onDismiss() {},
    queueError: null,
    refreshQueue: async () => {},
    queue: {
      paused: false,
      pause_after_current: false,
      summary: {
        running: 0,
        waiting: 1,
        held: 0,
        registering: 0,
        preparing: 0,
        approval_waiting: 1,
        active_total: 1,
      },
      jobs: [queueJob],
    },
  })
  assert.match(elementText(tree), /Awaiting plan review · Waiting for required model terms/)
})

test('Reference parent projects only an ordinary live child and scheduler controls keep exact targets', async t => {
  const previousStore = globalThis.__resourceWaitStore
  const previousCalls = globalThis.__resourceWaitApiCalls
  const calls = []
  globalThis.__resourceWaitApiCalls = calls
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: true },
    resumeJobRecovery() {},
    retryJobRecovery() {},
    openH3PlanReview() {},
    h3PlanReviewError: null,
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
  }
  t.after(() => {
    globalThis.__resourceWaitStore = previousStore
    globalThis.__resourceWaitApiCalls = previousCalls
  })

  const { JobPlaceholder, QueuePanel } = await loadJobPlaceholder()
  const makeJob = (id, parentJobId, overrides = {}) => ({
    id,
    ...(parentJobId === undefined ? {} : { parentJobId }),
    status: 'queued',
    progress: 0,
    step: 0,
    totalSteps: 0,
    phase: '',
    message: 'Queued',
    outputFiles: [],
    error: null,
    ...overrides,
  })
  const makeQueueJob = (jobId, overrides = {}) => ({
    job_id: jobId,
    status: 'queued',
    priority: 3,
    held: false,
    hold_after_output: false,
    position: 2,
    wait_reason: 'resource_wait',
    resource_descriptor: descriptor,
    queue_reorder_reason: 'queue_order',
    queue_residency_bypass_count: 0,
    queue_residency_bypassed_waiters: 0,
    requested_outputs: 1,
    produced_outputs: 0,
    eta_seconds: 90,
    subtask_eta_seconds: 30,
    ...overrides,
  })
  const baseQueue = {
    paused: false,
    pause_after_current: false,
    summary: {
      running: 0,
      waiting: 2,
      held: 0,
      registering: 0,
      preparing: 0,
      approval_waiting: 0,
      active_total: 2,
    },
  }
  const render = (jobs, queueJobs) => QueuePanel({
    jobs,
    onStop(jobId) { calls.push(['stop', jobId]) },
    onDismiss() {},
    queueError: null,
    refreshQueue: async () => {},
    queue: { ...baseQueue, jobs: queueJobs },
  })
  const renderCards = (jobs, queueJobs) => flattenElements(render(jobs, queueJobs))
    .filter(element => element.type === JobPlaceholder)

  const parentDescriptor = {
    ...descriptor,
    intent: 'text',
  }
  const parent = makeJob('reference-parent', undefined, {
    message: 'Orchestrating reference pack',
    resourceDescriptor: parentDescriptor,
  })
  const child = makeJob('reference-child', parent.id, { resourceDescriptor: parentDescriptor })
  const parentQueue = makeQueueJob(parent.id, { resource_descriptor: parentDescriptor })
  const childQueue = makeQueueJob(child.id)
  const projectedTree = render([parent, child], [parentQueue, childQueue])
  const projectedElements = flattenElements(projectedTree)
  const duplicateSuppressed = projectedElements.filter(element => element.type === JobPlaceholder)
  assert.equal(duplicateSuppressed.length, 1)
  assert.equal(duplicateSuppressed[0].props.job.id, parent.id)
  assert.equal(duplicateSuppressed[0].props.job.message, 'Orchestrating reference pack')
  assert.match(elementText(projectedTree), /GPU generation queued/)
  assert.match(elementText(projectedTree), /ETA 2m · current task 30s/)

  projectedElements.find(element => element.props?.title === 'Raise priority')?.props.onClick()
  projectedElements.find(element => elementText(element) === 'Hold')?.props.onClick()
  duplicateSuppressed[0].props.onStop()
  assert.deepEqual(calls.slice(0, 3), [
    ['setQueuePriority', child.id, 4],
    ['holdQueueJob', child.id],
    ['stop', parent.id],
  ])

  const visibleCases = [
    ['held', parent, child, { ...childQueue, held: true }],
    ['hold-after-output', parent, child, { ...childQueue, hold_after_output: true }],
    ['resource-blocked-live-row', parent, child, { ...childQueue, resource_descriptor: { ...descriptor, state: 'blocked' } }],
    ['recovery-actionable', parent, makeJob(child.id, parent.id, { recoveryActionable: true }), { ...childQueue, recovery_actionable: true }],
    ['recovery-state-blocked', parent, makeJob(child.id, parent.id, { recoveryState: 'blocked_preparation' }), { ...childQueue, recovery_state: 'blocked_preparation' }],
    ['recovery-interrupted', parent, makeJob(child.id, parent.id, { recoveryState: 'interrupted', recoveryInterrupted: true }), { ...childQueue, recovery_state: 'interrupted', recovery_interrupted: true }],
    ['recovery-actions-live-row', parent, child, { ...childQueue, recovery_actions: ['retry'] }],
    ['terminal-parent', makeJob(parent.id, undefined, { status: 'completed' }), child, childQueue],
    ['terminal-child', parent, makeJob(child.id, parent.id, { status: 'completed' }), childQueue],
  ]
  for (const [label, candidateParent, candidateChild, candidateQueue] of visibleCases) {
    assert.equal(
      renderCards([candidateParent, candidateChild], [parentQueue, candidateQueue]).length,
      2,
      `${label} child must retain its own actionable card`,
    )
  }

  const correlatedFailedParent = makeJob(parent.id, undefined, {
    status: 'failed',
    failedChildJobId: child.id,
    failedChildStatus: 'failed',
    failedChildReason: 'reference_child_failed',
  })
  const failedChild = makeJob(child.id, parent.id, { status: 'failed' })
  const correlatedCards = renderCards(
    [correlatedFailedParent, failedChild],
    [parentQueue, childQueue],
  )
  assert.equal(correlatedCards.length, 1, 'correlated terminal child should not create a duplicate failed card')
  assert.equal(correlatedCards[0].props.job.id, parent.id)

  const actionableFailedChild = makeJob(child.id, parent.id, {
    status: 'failed',
    recoveryActionable: true,
    recoveryActions: ['retry'],
  })
  assert.equal(
    renderCards([correlatedFailedParent, actionableFailedChild], [parentQueue, childQueue]).length,
    2,
    'an actionable terminal child must retain its own card',
  )

  const orphanVisible = renderCards([child], [childQueue])
  assert.equal(orphanVisible.length, 1)
  assert.equal(orphanVisible[0].props.job.id, child.id)

  const legacyVisible = renderCards([makeJob('legacy-child')], [makeQueueJob('legacy-child')])
  assert.equal(legacyVisible.length, 1)

  const unrelatedParentVisible = renderCards(
    [makeJob('other-parent'), child],
    [makeQueueJob('other-parent'), childQueue],
  )
  assert.equal(unrelatedParentVisible.length, 2)
})
