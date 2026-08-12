import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

const uiRoot = new URL('..', import.meta.url)
const storeUrl = new URL('../src/stores/useStore.ts', import.meta.url)
const mainUrl = new URL('../src/components/MainContent/MainContent.tsx', import.meta.url)
const generateButtonUrl = new URL('../src/components/Sidebar/GenerateButton.tsx', import.meta.url)
const navigationUrl = new URL('../src/lib/mainViewNavigation.ts', import.meta.url)
const queueProjectionUrl = new URL('../src/lib/queueProjection.ts', import.meta.url)

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
  const source = `${await readFile(mainUrl, 'utf8')}\nexport { describeResourceExecution, JobPlaceholder, QueuePanel }\n`
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
              export const useEffect = callback => globalThis.__resourceWaitEffects?.push(callback)
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
              export const fetchProjectAssets = async project => globalThis.__resourceWaitFetchProjectAssets?.(project) ?? []
              export const projectReferenceJobQualitySummary = (assets, jobId) => globalThis.__resourceWaitSummarizeQuality?.(assets, jobId) ?? null
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

async function loadGenerateButton() {
  const result = await build({
    entryPoints: [generateButtonUrl.pathname],
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
    plugins: [{
      name: 'generate-button-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'generate-button' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'generate-button' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide', namespace: 'generate-button' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'generate-button' }))
        bundle.onResolve({ filter: /H3PerformanceProfiles$/ }, () => ({ path: 'h3', namespace: 'generate-button' }))
        bundle.onLoad({ filter: /.*/, namespace: 'generate-button' }, args => {
          if (args.path === 'react') return { contents: 'export const useEffect = () => {}; export const useState = initial => [initial, () => {}]' }
          if (args.path === 'jsx-runtime') return { contents: 'export const jsx = (type, props, key) => ({ type, key, props: props || {} }); export const jsxs = jsx' }
          if (args.path === 'lucide') return { contents: "export const Play = 'Play', AlertTriangle = 'AlertTriangle'" }
          if (args.path === 'h3') return { contents: 'export const H3EstimateBadge = () => null' }
          return { contents: 'export const useStore = selector => selector(globalThis.__resourceWaitStore)' }
        })
      },
    }],
  })
  return import(asDataModule(result.outputFiles[0].text))
}

async function loadMainViewNavigation() {
  const result = await build({
    entryPoints: [navigationUrl.pathname],
    bundle: true,
    format: 'esm',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
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

test('standard text execution is described as accelerated text', async () => {
  const { describeResourceExecution } = await loadJobPlaceholder()
  const running = describeResourceExecution({
    intent: 'text',
    execution: 'standard',
    preemptible: false,
    preemption_mode: 'none',
    state: 'running',
    execution_attempt: 1,
  })
  assert.equal(running.label, 'Text using GPU acceleration')
  assert.match(running.title, /planning or review task/i)
  assert.match(running.title, /GPU acceleration/i)

  const queued = describeResourceExecution({
    intent: 'text',
    execution: 'standard',
    preemptible: false,
    preemption_mode: 'none',
    state: 'queued',
    execution_attempt: 1,
  })
  assert.equal(queued.label, 'Accelerated text queued')
})

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
    logical_job_kind: 'reference_pack_child',
  })
  const fromQueue = _queueJobDetails({
    status: 'waiting_for_plan_approval',
    wait_reason: 'waiting_for_plan_terms',
    plan_review_terms_required: true,
    parent_job_id: 'reference-parent',
    logical_job_kind: 'reference_pack_child',
  })

  assert.equal(fromStatusOrJobs.queueWaitReason, 'waiting_for_plan_terms')
  assert.equal(fromStatusOrJobs.planReviewTermsRequired, true)
  assert.equal(fromStatusOrJobs.parentJobId, 'reference-parent')
  assert.equal(fromStatusOrJobs.logicalJobKind, 'reference_pack_child')
  assert.equal(fromQueue.queueWaitReason, 'waiting_for_plan_terms')
  assert.equal(fromQueue.planReviewTermsRequired, true)
  assert.equal(fromQueue.parentJobId, 'reference-parent')
  assert.equal(fromQueue.logicalJobKind, 'reference_pack_child')

  const legacyStatus = _jobStatusDetails({ status: 'queued', queue_wait_reason: 'waiting_for_turn' })
  const legacyQueue = _queueJobDetails({ status: 'queued', wait_reason: 'waiting_for_turn' })
  assert.equal(Object.hasOwn(legacyStatus, 'parentJobId'), false)
  assert.equal(Object.hasOwn(legacyQueue, 'parentJobId'), false)
  assert.equal(Object.hasOwn(legacyStatus, 'logicalJobKind'), false)
  assert.equal(Object.hasOwn(legacyQueue, 'logicalJobKind'), false)
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

test('resource-wait job card clearly remains queued without execution warnings', async t => {
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
  const wait = elements.find(element => elementText(element) === 'Waiting for available GPU resources')
  assert.ok(wait)
  assert.equal(
    wait.props.title,
    'This generation is still in the queue. It will start when enough GPU resources are available, without interrupting a generation already running.',
  )
  const renderedText = elementText(tree)
  assert.match(renderedText, /Generation queued/)
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

test('Reference queue card presents recommended fidelity without exposing private reviewer text', async t => {
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
      status: 'completed',
      progress: 1,
      step: 1,
      totalSteps: 1,
      phase: '',
      message: 'Reference packs ready',
      outputFiles: ['opaque-output'],
      error: null,
      logicalJobKind: 'reference_pack_parent',
      workspace: 'project',
    },
    referenceQuality: {
      candidateCount: 2,
      variantLabel: 'Candidate 2',
      presentation: {
        stateLabel: 'Fidelity review deferred',
        gradeLabel: 'Ungraded',
        scoreLabel: null,
        residualSummary: null,
        correctionAvailable: false,
        recommended: true,
        preliminary: true,
        notice: 'This candidate remains usable; compare it yourself until fidelity review is available.',
        tone: 'deferred',
      },
    },
    onStop() {},
    onDismiss() {},
  })
  const text = elementText(tree)
  assert.match(text, /Reference packs ready/)
  assert.doesNotMatch(text, /Overall ETA/)
  assert.match(text, /Recommended · Fidelity review deferred · Ungraded/)
  assert.match(text, /Early recommendation · not graded yet/)
  assert.match(text, /remains usable/)
  assert.match(text, /2 candidates remain available in Reference/)
  assert.doesNotMatch(text, /provider|exception|private|commitment/i)

  const residualTree = JobPlaceholder({
    job: {
      id: 'faceb00d', status: 'completed', progress: 1, step: 1, totalSteps: 1,
      phase: '', message: 'Reference packs ready', outputFiles: ['opaque-output'], error: null,
      logicalJobKind: 'reference_pack_parent', workspace: 'project',
    },
    referenceQuality: {
      candidateCount: 1,
      variantLabel: 'Candidate 1',
      presentation: {
        stateLabel: 'Fidelity reviewed',
        gradeLabel: 'Minor residuals',
        scoreLabel: '83.5%',
        residualSummary: 'Differences: style, identity',
        correctionAvailable: true,
        recommended: true,
        preliminary: false,
        notice: 'This candidate remains usable; review the noted differences before keeping it.',
        tone: 'residual',
      },
    },
    onStop() {},
    onDismiss() {},
  })
  const residualText = elementText(residualTree)
  assert.match(residualText, /Recommended · Fidelity reviewed · Minor residuals · 83\.5%/)
  assert.match(residualText, /Differences: style, identity/)
  assert.match(residualText, /Suggestions for improving the result are available/)
  assert.match(residualText, /remains usable/)

  const source = await readFile(mainUrl, 'utf8')
  assert.match(source, /job\.logicalJobKind === 'reference_pack_parent'[\s\S]*?job\.status === 'completed'/)
  assert.match(source, /api\.fetchProjectAssets\(project\)/)
  assert.match(source, /api\.projectReferenceJobQualitySummary\(/)
  assert.doesNotMatch(source, /quality\.warning|rendered_brief|private_authored_settings/)
})

test('Reference queue quality lookup is terminal-only, project-deduplicated, fenced, and nonfatal', async t => {
  const previousStore = globalThis.__resourceWaitStore
  const previousEffects = globalThis.__resourceWaitEffects
  const previousUpdates = globalThis.__resourceWaitStateUpdates
  const previousFetch = globalThis.__resourceWaitFetchProjectAssets
  const previousSummarize = globalThis.__resourceWaitSummarizeQuality
  t.after(() => {
    globalThis.__resourceWaitStore = previousStore
    globalThis.__resourceWaitEffects = previousEffects
    globalThis.__resourceWaitStateUpdates = previousUpdates
    globalThis.__resourceWaitFetchProjectAssets = previousFetch
    globalThis.__resourceWaitSummarizeQuality = previousSummarize
  })

  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    resumeJobRecovery() {},
    retryJobRecovery() {},
    openH3PlanReview() {},
    h3PlanReviewError: null,
  }
  const deferred = () => {
    let resolve
    let reject
    const promise = new Promise((accept, decline) => { resolve = accept; reject = decline })
    return { promise, resolve, reject }
  }
  const makeQueueCardJob = (id, status, logicalJobKind, workspace) => ({
    id, status, logicalJobKind, workspace,
    progress: status === 'completed' ? 1 : 0,
    step: 0, totalSteps: 0, phase: '', message: '', outputFiles: [], error: null,
  })
  const jobs = [
    makeQueueCardJob('complete-a1', 'completed', 'reference_pack_parent', 'project-a'),
    makeQueueCardJob('complete-a2', 'completed', 'reference_pack_parent', 'project-a'),
    makeQueueCardJob('complete-b1', 'completed', 'reference_pack_parent', 'project-b'),
    makeQueueCardJob('running-a', 'running', 'reference_pack_parent', 'project-a'),
    makeQueueCardJob('ordinary-a', 'completed', undefined, 'project-a'),
  ]
  const fetches = []
  const firstA = deferred()
  const firstB = deferred()
  globalThis.__resourceWaitFetchProjectAssets = project => {
    fetches.push(project)
    return project === 'project-a' ? firstA.promise : firstB.promise
  }
  globalThis.__resourceWaitSummarizeQuality = (assets, jobId) => assets.length > 0
    ? { candidateCount: 1, variantLabel: jobId, presentation: { tone: 'pass' } }
    : null
  globalThis.__resourceWaitEffects = []
  globalThis.__resourceWaitStateUpdates = []

  const { QueuePanel } = await loadJobPlaceholder()
  QueuePanel({ jobs, onStop() {}, onDismiss() {}, queue: null, queueError: null, async refreshQueue() {} })
  assert.equal(globalThis.__resourceWaitEffects.length, 1)
  const staleCleanup = globalThis.__resourceWaitEffects[0]()
  assert.deepEqual(fetches, ['project-a', 'project-b'], 'two jobs in one project share one authorized asset fetch')
  assert.equal(fetches.includes('running-a'), false)
  staleCleanup()
  firstA.resolve([{ project: 'a' }])
  firstB.resolve([{ project: 'b' }])
  await Promise.all([firstA.promise, firstB.promise])
  await Promise.resolve()
  await Promise.resolve()
  assert.deepEqual(globalThis.__resourceWaitStateUpdates, [], 'cleanup fences every late result')

  const secondA = deferred()
  const secondB = deferred()
  fetches.length = 0
  globalThis.__resourceWaitFetchProjectAssets = project => {
    fetches.push(project)
    return project === 'project-a' ? secondA.promise : secondB.promise
  }
  globalThis.__resourceWaitEffects = []
  globalThis.__resourceWaitStateUpdates = []
  QueuePanel({ jobs, onStop() {}, onDismiss() {}, queue: null, queueError: null, async refreshQueue() {} })
  const currentCleanup = globalThis.__resourceWaitEffects[0]()
  secondA.resolve([{ project: 'a' }])
  secondB.reject(new Error('PRIVATE_ASSET_FETCH_FAILURE'))
  await Promise.allSettled([secondA.promise, secondB.promise])
  await Promise.resolve()
  await Promise.resolve()
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(globalThis.__resourceWaitStateUpdates.length, 1)
  assert.deepEqual(Object.keys(globalThis.__resourceWaitStateUpdates[0]).sort(), ['complete-a1', 'complete-a2'])
  assert.deepEqual(fetches, ['project-a', 'project-b'])
  currentCleanup()
})

test('Reference parent failure renders only allowlisted child diagnostics', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
    models: [],
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
    onDismiss() { globalThis.__resourceWaitDismissed = true },
    logOpen: true,
    logEvents: [{
      at: 1,
      status: 'waiting_for_plan_approval',
      progress: 20,
      message: 'Plan ready',
      phase: 'planning_generation',
      step: 1,
      total_steps: 5,
    }, {
      at: 2,
      status: 'server_only_status',
      progress: 25,
      message: 'Still working',
      phase: 'server_phase',
      step: 1,
      total_steps: 5,
    }],
  })
  const text = elementText(tree)
  assert.match(text, /Reference Generation Failed/)
  const childId = flattenElements(tree).find(element => (
    element.type?.name === 'CopyableJobId' && element.props?.jobId === 'deadbeef'
  ))
  assert.equal(childId?.props.label, 'Child job ID')
  assert.match(text, /Child status: Failed/)
  assert.match(text, /Reason: reference_child_failed/)
  assert.match(text, /Code: reference_image_generation_failed/)
  assert.match(text, /Detail: The image worker stopped before publishing an output\./)
  assert.match(text, /Waiting for plan review · 20%/)
  assert.match(text, /Status update · 25%/)
  assert.doesNotMatch(text, /server_only_status/)
  assert.doesNotMatch(text, /private\/path|traceback|nested|raw/)

  const dismiss = flattenElements(tree).find(element => element.props?.['aria-label'] === 'Dismiss generation')
  assert.equal(dismiss?.props.title, 'Dismiss generation')
  assert.equal(dismiss?.props.type, 'button')
  globalThis.__resourceWaitDismissed = false
  dismiss.props.onClick()
  assert.equal(globalThis.__resourceWaitDismissed, true)
})

test('queue card uses catalog model names and bounded segment presentation', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
    models: [{ model_type: 'minimax_h3_pinkcherry_fl2va', name: 'Catalog PinkCherry Model' }],
  }
  t.after(() => { globalThis.__resourceWaitStore = previousStore })

  const segment = (index, modelType, modelReason, boundaryType) => ({
    index,
    frames: 49,
    duration_seconds: 2,
    generated_frames: 49,
    published_frames: 49,
    generated_duration_seconds: 2,
    published_duration_seconds: 2,
    model_type: modelType,
    model_reason: modelReason,
    edge_anchor_locked: false,
    switch_from_previous: index > 1,
    boundary_from_previous: boundaryType ? { type: boundaryType, at_seconds: 2, source: 'server' } : null,
  })
  const { JobPlaceholder } = await loadJobPlaceholder()
  const tree = JobPlaceholder({
    job: {
      id: 'catalog-card',
      status: 'running',
      progress: 0.2,
      step: 2,
      totalSteps: 10,
      phase: 'Generating',
      message: '',
      outputFiles: [],
      error: null,
      workspace: 'project-a',
      modelType: 'minimax_h3_pinkcherry_fl2va',
      windowCurrent: 2,
      windowTotal: 3,
      currentSegmentModel: 'minimax_h3_pinkcherry_fl2va',
      currentSegmentReason: 'raw_current_model_reason',
      h3SegmentPlan: {
        kind: 'h3_segments',
        clip_count: 3,
        fps: 24,
        requested_frames: 147,
        planned_frames: 147,
        published_frames: 147,
        adaptive_conditioning: true,
        checkpoint_switches: 2,
        segments: [
          segment(1, 'minimax_h3_pinkcherry_fl2va', 'raw_catalog_reason', null),
          segment(2, 'minimax_h3_ref2va', 'raw_reference_reason', 'transition'),
          segment(3, 'server_only_model', 'raw_unknown_reason', 'server_only_boundary'),
        ],
      },
    },
    onStop() {},
    onDismiss() {},
  })

  const text = elementText(tree)
  assert.match(text, /Catalog PinkCherry Model · Project: project-a/)
  assert.match(text, /Smooth transition/)
  assert.match(text, /Boundary details unavailable/)
  assert.match(text, /Catalog PinkCherry Model: Follows this segment’s frame anchors/)
  assert.doesNotMatch(text, /minimax_h3|server_only_model|raw_current_model_reason/)

  const segmentTitles = flattenElements(tree)
    .map(element => element.props?.title)
    .filter(title => typeof title === 'string' && title.startsWith('Segment '))
  assert.equal(segmentTitles.length, 3)
  assert.match(segmentTitles[0], /Catalog PinkCherry Model/)
  assert.match(segmentTitles[1], /Ref2VA video model.*Uses reference images and recent motion.*Smooth transition/)
  assert.match(segmentTitles[2], /Model details unavailable.*Boundary details unavailable/)
  assert.doesNotMatch(segmentTitles.join(' '), /raw_catalog_reason|raw_reference_reason|raw_unknown_reason|server_only_model|server_only_boundary/)

  const unknownModelTree = JobPlaceholder({
    job: {
      id: 'unknown-model-card',
      status: 'queued',
      progress: 0,
      step: 0,
      totalSteps: 0,
      phase: '',
      message: '',
      outputFiles: [],
      error: null,
      modelType: 'server_only_job_model',
    },
    onStop() {},
    onDismiss() {},
  })
  assert.match(elementText(unknownModelTree), /Model details unavailable/)
  assert.doesNotMatch(elementText(unknownModelTree), /server_only_job_model/)
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
  assert.match(renderedText, /Text using CPU · slower/)
  assert.match(renderedText, /Maestro may restart this task from the beginning with GPU acceleration, but only when that is expected to finish sooner/)
  assert.match(renderedText, /Overall ETA unknown/)
  const badge = flattenElements(tree).find(element => elementText(element) === 'Text using CPU · slower')
  assert.equal(
    badge?.props.title,
    'This text task is running on the CPU, so it may be slower. Maestro will restart it with GPU acceleration only if starting over is expected to finish sooner.',
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
  assert.match(renderedText, /Text using CPU · slower/)
  assert.match(renderedText, /Overall ETA unknown/)
  assert.doesNotMatch(renderedText, /discard|restart|deliver sooner/i)
  const badge = flattenElements(tree).find(element => (
    elementText(element) === 'Text using CPU · slower' && element.props.title
  ))
  assert.equal(
    badge?.props.title,
    'This text task is using the CPU, which is usually slower than GPU acceleration.',
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
    ['preemption_requested', 'Faster restart requested', 'If the switch proceeds'],
    ['resources_releasing', 'Preparing to restart faster', 'CPU progress was discarded'],
    ['restarting_on_accelerator', 'Restarting with GPU acceleration', 'ETA is not known yet'],
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

test('running recovery never presents itself as merely queued', async t => {
  const previousStore = globalThis.__resourceWaitStore
  globalThis.__resourceWaitStore = {
    accessContext: { machine_controls: false },
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
  }
  t.after(() => { globalThis.__resourceWaitStore = previousStore })

  const { JobPlaceholder } = await loadJobPlaceholder()
  const tree = JobPlaceholder({
    job: {
      id: 'running-recovery',
      status: 'running',
      recoveryState: 'retrying',
      progress: 0,
      step: 0,
      totalSteps: 0,
      phase: '',
      message: '',
      outputFiles: [],
      error: null,
    },
    onStop() {},
    onDismiss() {},
  })
  assert.match(elementText(tree), /Recovery Running/)
  assert.doesNotMatch(elementText(tree), /Recovery Queued/)
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
    element.props?.title === 'This generation is still in the queue. It will start when enough GPU resources are available, without interrupting a generation already running.'
    && elementText(element).includes('Waiting for available GPU resources')
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
    && elementText(element) === 'Faster restart requested'
  ))
  assert.ok(rowBadge)
  assert.match(rowBadge.props.title, /If Maestro can make the switch/)

  const renderedText = elementText(tree)
  assert.match(renderedText, /How queue priority works/)
  assert.match(renderedText, /Ready jobs start by priority. When priorities match, Maestro usually keeps their queue order/)
  assert.match(renderedText, /A job may start sooner when it can reuse a model that is already loaded/)
  assert.match(renderedText, /Jobs that have waited a long time keep their place/)
  assert.match(renderedText, /Queued generations do not interrupt work already running/)
  assert.match(renderedText, /Only a restartable CPU text task may start over with GPU acceleration/)
  assert.doesNotMatch(renderedText, /residency|starvation|authoritative|fair share|preemptible/i)
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

test('Reference logical queue folds physical children while controls retain exact scheduler and cancel targets', async t => {
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
      waiting: 37,
      held: 0,
      registering: 0,
      preparing: 0,
      approval_waiting: 0,
      active_total: 37,
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
    message: 'Public parent label that does not match its child',
    resourceDescriptor: parentDescriptor,
    logicalJobKind: 'reference_pack_parent',
  })
  const child = makeJob('reference-child', parent.id, {
    message: 'Unrelated internal scheduler label',
    resourceDescriptor: descriptor,
    logicalJobKind: 'reference_pack_child',
  })
  const parentQueue = makeQueueJob(parent.id, {
    resource_descriptor: parentDescriptor,
    logical_job_kind: 'reference_pack_parent',
  })
  const childQueue = makeQueueJob(child.id, {
    parent_job_id: parent.id,
    logical_job_kind: 'reference_pack_child',
  })
  // The backend public jobs response suppresses this ordinary child, while
  // the authorized queue response retains it as the scheduler target.
  const projectedTree = render([parent], [parentQueue, childQueue])
  const projectedElements = flattenElements(projectedTree)
  const duplicateSuppressed = projectedElements.filter(element => element.type === JobPlaceholder)
  assert.equal(duplicateSuppressed.length, 1)
  assert.equal(duplicateSuppressed[0].props.job.id, parent.id)
  assert.equal(duplicateSuppressed[0].props.job.message, 'Public parent label that does not match its child')
  assert.match(elementText(projectedTree), /Generation queued/)
  assert.match(elementText(projectedTree), /ETA 2m · current task 30s/)
  assert.match(elementText(projectedTree), /0 running · 0 preparing · 0 awaiting review · 1 waiting/)
  assert.doesNotMatch(elementText(projectedTree), /37 waiting|37 active/)
  assert.match(elementText(projectedTree), /Waiting in queue/)
  assert.doesNotMatch(elementText(projectedTree), /2 of 1|1 job ahead/)
  const mainSource = await readFile(mainUrl, 'utf8')
  assert.match(mainSource, /ETA \{compactEta\(currentEtaSeconds\)\}/)
  assert.match(mainSource, /currentSubtaskEtaSeconds != null \? ` · task \$\{compactEta\(currentSubtaskEtaSeconds\)\}`/)
  assert.doesNotMatch(mainSource, /ETA \{compactEta\(currentJob\.etaSeconds\)\}/)

  assert.equal(
    renderCards([parent, child], [parentQueue, childQueue]).length,
    1,
    'a reconnect response that still includes the marked child must project identically',
  )

  projectedElements.find(element => element.props?.title === 'Raise priority')?.props.onClick()
  projectedElements.find(element => elementText(element) === 'Hold')?.props.onClick()
  duplicateSuppressed[0].props.onStop()
  assert.deepEqual(calls.slice(0, 3), [
    ['setQueuePriority', child.id, 4],
    ['holdQueueJob', child.id],
    ['stop', parent.id],
  ])

  const foldedCases = [
    ['held', parent, child, { ...childQueue, held: true }],
    ['hold-after-output', parent, child, { ...childQueue, hold_after_output: true }],
    ['terminal-parent', { ...parent, status: 'completed' }, child, childQueue],
    ['terminal-child', parent, { ...child, status: 'completed' }, childQueue],
  ]
  for (const [label, candidateParent, candidateChild, candidateQueue] of foldedCases) {
    assert.equal(
      renderCards([candidateParent, candidateChild], [parentQueue, candidateQueue]).length,
      1,
      `${label} child remains an internal projection`,
    )
  }

  const cancelledChild = { ...child, status: 'cancelled' }
  assert.equal(
    renderCards([parent, cancelledChild], [parentQueue]).length,
    1,
    'a terminal child omitted from the live queue must not reappear during the cancel race',
  )
  assert.equal(
    renderCards([{ ...parent, status: 'cancelled' }, child], [childQueue]).length,
    1,
    'a cancelled public parent remains the monotonic card even while its child row is briefly live',
  )

  const actionableCases = [
    ['resource-blocked-live-row', child, { ...childQueue, resource_descriptor: { ...descriptor, state: 'blocked' } }],
    ['recovery-actionable', { ...child, recoveryActionable: true }, { ...childQueue, recovery_actionable: true }],
    ['recovery-state-blocked', { ...child, recoveryState: 'blocked_preparation' }, { ...childQueue, recovery_state: 'blocked_preparation' }],
    ['recovery-interrupted', { ...child, recoveryState: 'interrupted', recoveryInterrupted: true }, { ...childQueue, recovery_state: 'interrupted', recovery_interrupted: true }],
    ['recovery-actions-live-row', child, { ...childQueue, recovery_actions: ['retry'] }],
  ]
  for (const [label, candidateChild, candidateQueue] of actionableCases) {
    assert.equal(
      renderCards([parent, candidateChild], [parentQueue, candidateQueue]).length,
      2,
      `${label} child requires its own actionable card`,
    )
  }

  const correlatedFailedParent = makeJob(parent.id, undefined, {
    status: 'failed',
    failedChildJobId: child.id,
    failedChildStatus: 'failed',
    failedChildReason: 'reference_child_failed',
  })
  const failedChild = { ...child, status: 'failed' }
  const correlatedCards = renderCards(
    [correlatedFailedParent, failedChild],
    [parentQueue, childQueue],
  )
  assert.equal(correlatedCards.length, 1, 'correlated terminal child should not create a duplicate failed card')
  assert.equal(correlatedCards[0].props.job.id, parent.id)

  const uncorrelatedFailedParent = {
    ...parent,
    status: 'failed',
    failedChildJobId: 'different-child',
  }
  assert.equal(
    renderCards([uncorrelatedFailedParent, failedChild], [parentQueue]).length,
    2,
    'a failed child remains visible unless the parent carries its exact reverse correlation',
  )

  const actionableFailedChild = {
    ...failedChild,
    status: 'failed',
    recoveryActionable: true,
    recoveryActions: ['retry'],
  }
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

  const legacyParent = makeJob('legacy-parent', undefined, { resourceDescriptor: parentDescriptor })
  const legacyChild = makeJob('legacy-child-with-parent', legacyParent.id, { resourceDescriptor: descriptor })
  assert.equal(
    renderCards(
      [legacyParent, legacyChild],
      [makeQueueJob(legacyParent.id), makeQueueJob(legacyChild.id, { parent_job_id: legacyParent.id })],
    ).length,
    2,
    'legacy text/non-text resource hints are not exact enough to fold an unmarked relation',
  )

  const legacyFailedParent = makeJob('legacy-failed-parent', undefined, {
    status: 'failed',
    failedChildJobId: 'legacy-failed-child',
  })
  const legacyFailedChild = makeJob('legacy-failed-child', legacyFailedParent.id, { status: 'failed' })
  assert.equal(
    renderCards([legacyFailedParent, legacyFailedChild], []).length,
    1,
    'legacy terminal reverse correlation remains exact evidence for folding the child',
  )

  assert.equal(
    renderCards(
      [parent, child],
      [parentQueue, { ...childQueue, logical_job_kind: 'reference_pack_parent' }],
    ).length,
    2,
    'conflicting public and queue markers fail closed instead of folding',
  )

  const unrelatedParentVisible = renderCards(
    [makeJob('other-parent'), child],
    [makeQueueJob('other-parent'), childQueue],
  )
  assert.equal(unrelatedParentVisible.length, 2)

  const projectionSource = await readFile(queueProjectionUrl, 'utf8')
  assert.doesNotMatch(projectionSource, /resourceIntent|intent === 'text'|intent !== 'text'/)
  assert.match(projectionSource, /publicKind !== undefined && queueKind !== undefined && publicKind !== queueKind/)
})

test('Generate count uses the same logical Reference projection as queue cards', async t => {
  const previousStore = globalThis.__resourceWaitStore
  t.after(() => { globalThis.__resourceWaitStore = previousStore })
  const textDescriptor = { ...descriptor, intent: 'text' }
  globalThis.__resourceWaitStore = {
    jobs: [{
      id: 'reference-parent',
      status: 'queued',
      progress: 0,
      step: 0,
      totalSteps: 0,
      phase: '',
      message: 'First visible label',
      outputFiles: [],
      error: null,
      resourceDescriptor: textDescriptor,
      logicalJobKind: 'reference_pack_parent',
    }, {
      id: 'reference-child',
      parentJobId: 'reference-parent',
      status: 'running',
      progress: 0,
      step: 0,
      totalSteps: 0,
      phase: '',
      message: 'Different physical label',
      outputFiles: [],
      error: null,
      resourceDescriptor: descriptor,
      logicalJobKind: 'reference_pack_child',
    }],
    startGeneration() {},
    setSidebarOpen() {},
    modelOptionsLoading: false,
    activeWorkspace: 'project',
    models: [],
    params: { model_type: 'model', custom_settings: {} },
    hostTerms: {},
    generationMode: 'video',
    modelOptions: null,
    h3CurrentEstimate: null,
    h3EstimateLoading: false,
    h3PerformanceProfiles: [],
    startImage: null,
    editSubMode: null,
    editVideoPath: null,
    outpaintVideoBox: { x: 0, y: 0, w: 1, h: 1 },
  }

  const { GenerateButton } = await loadGenerateButton()
  assert.match(elementText(GenerateButton()), /Go \(1\)/)
  assert.doesNotMatch(elementText(GenerateButton()), /Go \(2\)/)
})

test('Reference queue navigation uses one payload-free event with exact cleanup', async t => {
  const previousWindow = globalThis.window
  const testWindow = new EventTarget()
  globalThis.window = testWindow
  t.after(() => { globalThis.window = previousWindow })

  const { OPEN_QUEUE_VIEW_EVENT, requestQueueView, subscribeQueueView } = await loadMainViewNavigation()
  const seen = []
  const listener = event => seen.push(event)
  const cleanupFirst = subscribeQueueView(listener)
  const cleanupDuplicate = subscribeQueueView(listener)

  requestQueueView()
  requestQueueView()
  assert.equal(seen.length, 2, 'the browser de-duplicates the identical listener registration')
  assert.ok(seen.every(event => event.type === OPEN_QUEUE_VIEW_EVENT))
  assert.ok(seen.every(event => !('detail' in event)), 'the navigation event carries no job or content payload')

  cleanupFirst()
  requestQueueView()
  assert.equal(seen.length, 2, 'cleanup removes the exact registered listener')
  cleanupDuplicate()

  const mainSource = await readFile(mainUrl, 'utf8')
  assert.match(mainSource, /const openQueue = \(\) => setMainView\('queue'\)/)
  assert.match(mainSource, /return subscribeQueueView\(openQueue\)/)
  assert.match(mainSource, /if \(newActiveJob && openQueueAfterSubmit\) setMainView\('queue'\)/)
})
