import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import test from 'node:test'

import { build } from 'esbuild'
import { compile } from 'tailwindcss'

import { closeModalIfTop, installModalFocus } from '../src/lib/modalFocus.ts'

const dialogUrl = new URL('../src/components/H3GenerationPlanDialog.tsx', import.meta.url)

class FakeDocument extends EventTarget {
  activeElement = null
  body = { name: 'document body', style: { overflow: 'auto' } }
  appRoot = null

  getElementById(id) { return id === 'root' ? this.appRoot : null }
}

class FakeElement {
  attributes = new Map()
  descendants = new Set()
  focusable = []
  isConnected = true

  constructor(document, name) {
    this.document = document
    this.name = name
  }

  focus() { this.document.activeElement = this }
  hasAttribute(name) { return this.attributes.has(name) }
  getAttribute(name) { return this.attributes.get(name) ?? null }
  setAttribute(name, value = '') { this.attributes.set(name, String(value)) }
  removeAttribute(name) { this.attributes.delete(name) }
  contains(element) { return element === this || this.descendants.has(element) }
  querySelectorAll() { return this.focusable }
  closest() { return null }
}

function modalFixture(document, name) {
  const dialog = new FakeElement(document, `${name} dialog`)
  const first = new FakeElement(document, `${name} first`)
  const last = new FakeElement(document, `${name} last`)
  const trigger = new FakeElement(document, `${name} trigger`)
  dialog.descendants = new Set([first, last, trigger])
  dialog.focusable = [first, last]
  return { dialog, first, last, trigger }
}

function dispatchKey(document, key) {
  const event = new Event('keydown', { cancelable: true })
  Object.defineProperty(event, 'key', { value: key })
  document.dispatchEvent(event)
  return event
}

function treeChildren(node) {
  if (Array.isArray(node)) return node
  if (!node || typeof node !== 'object') return []
  const children = node.props?.children
  return Array.isArray(children) ? children : children == null ? [] : [children]
}

function findNodes(node, predicate, matches = []) {
  if (node && typeof node === 'object' && predicate(node)) matches.push(node)
  for (const child of treeChildren(node)) findNodes(child, predicate, matches)
  return matches
}

function nodeText(node) {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  return treeChildren(node).map(nodeText).join('')
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

async function loadDialog() {
  const modules = new Map([
    ['react', `
      export function useState(initial) {
        const index = globalThis.__h3HookIndex++
        if (!(index in globalThis.__h3HookState)) {
          globalThis.__h3HookState[index] = typeof initial === 'function' ? initial() : initial
        }
        return [globalThis.__h3HookState[index], value => {
          const current = globalThis.__h3HookState[index]
          globalThis.__h3HookState[index] = typeof value === 'function' ? value(current) : value
        }]
      }
      export function useEffect(effect) {
        const cleanup = effect()
        if (typeof cleanup === 'function') globalThis.__h3Cleanups.push(cleanup)
      }
      export function useMemo(callback) { return callback() }
      export function useId() { return 'h3-duration-test-id' }
    `],
    ['react-dom', `
      export function createPortal(children, target) { return { ...children, portalTarget: target } }
    `],
    ['react/jsx-runtime', `
      export const Fragment = Symbol('Fragment')
      export function jsx(type, props, key) { return { type, props: props || {}, key } }
      export const jsxs = jsx
    `],
    ['lucide-react', `
      const icon = props => ({ type: 'svg', props: props || {} })
      export const AlertTriangle = icon, Check = icon, X = icon
    `],
    ['store', `
      export function useStore(selector) { return selector(globalThis.__h3Store) }
    `],
    ['host-terms', `
      export const HOST_TERM_NOTICES = {
        minimax_h3_ref2va: { text: 'Model terms.', version: '1', href: 'https://example.test', linkLabel: 'Terms' },
      }
    `],
    ['focus', `
      export function installModalFocus(options) {
        globalThis.__h3Installs.push(options)
        options.initialFocus.focus()
        return () => {
          globalThis.__h3Uninstalls += 1
          options.restoreFocus?.focus()
        }
      }
      export function closeModalIfTop(document, dialog, onClose) {
        globalThis.__h3CloseRequests.push(dialog)
        if (dialog !== globalThis.__h3TopDialog) return false
        onClose()
        return true
      }
    `],
  ])
  const result = await build({
    absWorkingDir: new URL('../', import.meta.url).pathname,
    entryPoints: [dialogUrl.pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'h3-plan-dialog-harness',
      setup(bundle) {
        bundle.onResolve({ filter: /.*/ }, args => {
          if (modules.has(args.path)) return { path: args.path, namespace: 'h3-plan' }
          if (args.path === 'react' || args.path === 'react-dom' || args.path === 'react/jsx-runtime' || args.path === 'lucide-react') {
            return { path: args.path, namespace: 'h3-plan' }
          }
          if (args.path.includes('stores/useStore')) return { path: 'store', namespace: 'h3-plan' }
          if (args.path.includes('lib/hostTerms')) return { path: 'host-terms', namespace: 'h3-plan' }
          if (args.path.includes('lib/modalFocus')) return { path: 'focus', namespace: 'h3-plan' }
          return null
        })
        bundle.onLoad({ filter: /.*/, namespace: 'h3-plan' }, args => ({
          contents: modules.get(args.path),
          loader: 'js',
        }))
      },
    }],
  })
  const compiled = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(
    createRequire(import.meta.url),
    compiled,
    compiled.exports,
  )
  return compiled.exports.H3GenerationPlanDialog
}

function checkpoint(modelType) {
  return {
    model_type: modelType,
    name: modelType,
    conditioning_mode: modelType === 'minimax_h3_ref2va' ? 'semantic_references' : 'first_last_frames',
    is_downloaded: true,
    managed_download: false,
    auto_download: false,
    terms_required: modelType === 'minimax_h3_ref2va',
    available: true,
    unavailable_reason: '',
  }
}

function plan() {
  return {
    kind: 'h3_segments',
    clip_count: 2,
    fps: 24,
    requested_frames: 48,
    planned_frames: 48,
    published_frames: 48,
    checkpoint_options: [
      checkpoint('minimax_h3'),
      checkpoint('minimax_h3_pinkcherry_fl2va'),
      checkpoint('minimax_h3_ref2va'),
    ],
    segments: [
      {
        index: 1,
        frames: 24,
        duration_seconds: 1,
        model_type: 'minimax_h3',
        model_reason: 'server first',
        edge_anchor_locked: false,
      },
      {
        index: 2,
        frames: 24,
        duration_seconds: 1,
        model_type: 'minimax_h3_ref2va',
        model_reason: 'server second',
        edge_anchor_locked: false,
        boundary_from_previous: { type: 'continuous' },
      },
    ],
  }
}

function planWithDuration() {
  const current = plan()
  return {
    ...current,
    duration_plan: {
      revision: 'h3dp1_test-revision',
      target_published_frames: 48,
      current_published_frames: 48,
      current_generated_frames: 64,
      fps: 24,
      snap_candidates: {
        nearest: {
          requested_published_frames: 48,
          candidate_published_frames: 47,
          segment_count: 2,
          generated_frames: [32, 32],
          segment_published_frames: [23, 24],
          confidence: 'high',
          applied: true,
          reason: 'Closest available length.',
        },
        down: {
          requested_published_frames: 48,
          candidate_published_frames: null,
          segment_count: null,
          generated_frames: [],
          segment_published_frames: [],
          confidence: 'unavailable',
          applied: false,
          reason: 'No proven segment-efficient boundary satisfies the selected snap mode.',
        },
      },
      segments: [
        { index: 1, published_frames: 24, min_published_frames: 20, max_published_frames: 32, grid_step: 1, grid_offset: 0, authored_locked: false, completed_locked: false, lock_reason: null },
        { index: 2, published_frames: 24, min_published_frames: 24, max_published_frames: 24, grid_step: 1, grid_offset: 0, authored_locked: true, completed_locked: false, lock_reason: 'authored' },
      ],
      redistribution_mode: 'none',
      outcome: 'exact',
      reason: 'The current plan matches the target.',
      residual_published_frames: 0,
    },
  }
}

function resetHarness(document, store, refs, nowMs = 1_000_000) {
  globalThis.document = document
  globalThis.HTMLElement = FakeElement
  globalThis.window = {
    setTimeout: () => 1,
    clearTimeout() {},
    setInterval: () => 2,
    clearInterval() {},
    alert(message) { globalThis.__h3Alerts.push(message) },
  }
  globalThis.__h3HookIndex = 0
  globalThis.__h3HookState = [
    ['minimax_h3', 'minimax_h3_ref2va'],
    ['continuous'],
    'review-job',
    nowMs,
    { current: refs.dialog },
    { current: refs.close },
    { current: null },
    { current: false },
    { current: null },
    { current: false },
  ]
  globalThis.__h3Store = store
  globalThis.__h3Cleanups = []
  globalThis.__h3Installs = []
  globalThis.__h3Uninstalls = 0
  globalThis.__h3CloseRequests = []
  globalThis.__h3TopDialog = refs.dialog
  globalThis.__h3Alerts = []
}

function render(Dialog) {
  globalThis.__h3HookIndex = 0
  return Dialog()
}

test('H3 plan at 180 covers an existing priority-100 overlay and preserves nested locks and exact focus restoration', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const outerOpener = new FakeElement(document, 'Generate opener')
  const generate = modalFixture(document, 'Generate')
  const h3 = modalFixture(document, 'H3 plan')
  generate.dialog.descendants.add(h3.trigger)
  let generateCloses = 0
  let h3Closes = 0

  outerOpener.focus()
  const cleanupGenerate = installModalFocus({
    document,
    dialog: generate.dialog,
    initialFocus: generate.first,
    restoreFocus: outerOpener,
    appRoot,
    onClose: () => { generateCloses += 1 },
    priority: 100,
  })
  h3.trigger.focus()
  const cleanupH3 = installModalFocus({
    document,
    dialog: h3.dialog,
    initialFocus: h3.first,
    restoreFocus: h3.trigger,
    appRoot,
    onClose: () => { h3Closes += 1 },
    priority: 180,
  })

  assert.equal(document.activeElement, h3.first)
  assert.equal(generate.dialog.hasAttribute('inert'), true)
  assert.equal(appRoot.hasAttribute('inert'), true)
  assert.equal(document.body.style.overflow, 'hidden')
  assert.equal(closeModalIfTop(document, generate.dialog, () => { generateCloses += 1 }), false)
  assert.equal(closeModalIfTop(document, h3.dialog, () => { h3Closes += 1 }), true)
  assert.equal(generateCloses, 0)
  assert.equal(h3Closes, 1)
  assert.equal(dispatchKey(document, 'Escape').defaultPrevented, true)
  assert.equal(h3Closes, 2)

  cleanupH3()
  assert.equal(document.activeElement, h3.trigger)
  assert.equal(generate.dialog.hasAttribute('inert'), false)
  assert.equal(appRoot.hasAttribute('inert'), true)
  assert.equal(document.body.style.overflow, 'hidden')
  cleanupGenerate()
  assert.equal(document.activeElement, outerOpener)
  assert.equal(appRoot.hasAttribute('inert'), false)
  assert.equal(document.body.style.overflow, 'auto')
})

test('rendered H3 plan portals to body, captures its opener, and fences every modal action', async t => {
  const Dialog = await loadDialog()
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const opener = new FakeElement(document, 'Review plan opener')
  const dialog = new FakeElement(document, 'rendered H3 dialog')
  const closeButton = new FakeElement(document, 'rendered H3 close')
  document.appRoot = appRoot
  document.activeElement = opener
  const approval = deferred()
  const cancellation = deferred()
  const approvals = []
  let cancels = 0
  let closes = 0
  const currentPlan = plan()
  const store = {
    pendingH3Plan: currentPlan,
    pendingH3PlanEstimate: null,
    pendingH3PlanJobId: 'review-job',
    pendingH3PlanWorkspace: 'project one',
    jobs: [{
      id: 'review-job',
      status: 'waiting_for_plan_approval',
      planReviewDeadline: 2_000,
      planReviewTermsRequired: false,
    }],
    h3PlanReviewLoading: false,
    h3PlanReviewError: null,
    models: [],
    activeWorkspace: 'project one',
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
    hostTermsLoading: false,
    hostTermsError: null,
    loadHostTerms() {},
    acceptHostTerm() {},
    closeH3PlanReview() { closes += 1 },
    approveH3Plan(payload) { approvals.push(payload); return approval.promise },
    cancelH3Plan() { cancels += 1; return cancellation.promise },
  }
  resetHarness(document, store, { dialog, close: closeButton })
  t.after(() => {
    delete globalThis.document
    delete globalThis.window
    delete globalThis.HTMLElement
  })

  let tree = render(Dialog)
  assert.equal(tree.portalTarget, document.body)
  const install = globalThis.__h3Installs.at(-1)
  assert.equal(install.dialog, dialog)
  assert.equal(install.initialFocus, closeButton)
  assert.equal(install.restoreFocus, opener)
  assert.equal(install.appRoot, appRoot)
  assert.equal(install.priority, 180)
  assert.equal(document.activeElement, closeButton)
  const renderedDialog = findNodes(tree, node => node.props?.role === 'dialog')[0]
  assert.equal(renderedDialog.props['aria-modal'], 'true')
  assert.equal(renderedDialog.props['aria-labelledby'], 'h3-plan-dialog-title')
  assert.equal(renderedDialog.props['aria-describedby'], 'h3-plan-dialog-description')

  const closeControls = findNodes(tree, node => node.props?.['aria-label'] === 'Close long-video plan review')
  assert.equal(closeControls.length, 2)
  globalThis.__h3TopDialog = new FakeElement(document, 'covering dialog')
  for (const control of closeControls) control.props.onClick()
  assert.equal(closes, 0)
  assert.deepEqual(globalThis.__h3CloseRequests, [dialog, dialog])
  globalThis.__h3TopDialog = dialog
  closeControls[1].props.onClick()
  assert.equal(closes, 1)

  const modelOne = findNodes(tree, node => node.props?.['aria-label'] === 'Model for segment 1')[0]
  const boundaryTwo = findNodes(tree, node => node.props?.['aria-label'] === 'Boundary before segment 2')[0]
  modelOne.props.onChange({ target: { value: 'minimax_h3_pinkcherry_fl2va' } })
  boundaryTwo.props.onChange({ target: { value: 'cut' } })
  tree = render(Dialog)
  const approveButton = findNodes(tree, node => node.type === 'button' && nodeText(node).includes('Approve & resume'))[0]
  approveButton.props.onClick()
  approveButton.props.onClick()
  assert.equal(approvals.length, 1, 'same-render double approval is fenced')
  assert.deepEqual(approvals[0], {
    segmentOverrides: [
      {
        model_type: 'minimax_h3_pinkcherry_fl2va',
        drop_semantic_refs: true,
        reason: 'user plan override',
      },
      {
        model_type: 'minimax_h3_ref2va',
        drop_semantic_refs: false,
        reason: 'server second',
      },
    ],
    boundaryOverrides: [{ type: 'cut' }],
  })
  approval.resolve()
  await approval.promise
  await Promise.resolve()

  tree = render(Dialog)
  const cancelButton = findNodes(tree, node => node.type === 'button' && nodeText(node).includes('Cancel generation'))[0]
  cancelButton.props.onClick()
  cancelButton.props.onClick()
  assert.equal(cancels, 1, 'same-render double cancellation is fenced')
  cancellation.resolve()
  await cancellation.promise
  await Promise.resolve()

  assert.ok(globalThis.__h3Cleanups.length >= 3)
  for (const cleanup of globalThis.__h3Cleanups) cleanup()
  assert.equal(globalThis.__h3Uninstalls, globalThis.__h3Installs.length)
  assert.equal(document.activeElement, opener)
})

test('loading and expired review states cannot dismiss or resubmit stale plan work', async t => {
  const Dialog = await loadDialog()
  const document = new FakeDocument()
  const dialog = new FakeElement(document, 'rendered H3 dialog')
  const closeButton = new FakeElement(document, 'rendered H3 close')
  document.appRoot = new FakeElement(document, 'app root')
  document.activeElement = new FakeElement(document, 'opener')
  let closes = 0
  let approvals = 0
  let cancels = 0
  const currentPlan = plan()
  const store = {
    pendingH3Plan: currentPlan,
    pendingH3PlanEstimate: null,
    pendingH3PlanJobId: 'review-job',
    pendingH3PlanWorkspace: 'project one',
    jobs: [{ id: 'review-job', status: 'waiting_for_plan_approval', planReviewDeadline: 2_000 }],
    h3PlanReviewLoading: true,
    h3PlanReviewError: 'Still applying the current decision.',
    models: [],
    activeWorkspace: 'project one',
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
    hostTermsLoading: false,
    hostTermsError: null,
    loadHostTerms() {},
    acceptHostTerm() {},
    closeH3PlanReview() { closes += 1 },
    async approveH3Plan() { approvals += 1 },
    async cancelH3Plan() { cancels += 1 },
  }
  resetHarness(document, store, { dialog, close: closeButton }, 2_000_000)
  t.after(() => {
    delete globalThis.document
    delete globalThis.window
    delete globalThis.HTMLElement
  })

  const tree = render(Dialog)
  for (const control of findNodes(tree, node => node.props?.['aria-label'] === 'Close long-video plan review')) {
    assert.equal(control.props.disabled, true)
    control.props.onClick()
  }
  const approve = findNodes(tree, node => node.type === 'button' && nodeText(node).includes('Applying…'))[0]
  const cancel = findNodes(tree, node => node.type === 'button' && nodeText(node).includes('Cancel generation'))[0]
  assert.equal(approve.props.disabled, true)
  assert.equal(cancel.props.disabled, true)
  approve.props.onClick()
  cancel.props.onClick()
  assert.equal(closes, 0)
  assert.equal(approvals, 0)
  assert.equal(cancels, 0)
  assert.match(nodeText(tree), /Approving the saved plan unchanged/)
  assert.equal(findNodes(tree, node => node.props?.role === 'alert').length, 1)
})

test('duration controls preserve server authority, locks, revision, and exact approval intent', async t => {
  const Dialog = await loadDialog()
  const document = new FakeDocument()
  const dialog = new FakeElement(document, 'rendered H3 dialog')
  const closeButton = new FakeElement(document, 'rendered H3 close')
  document.appRoot = new FakeElement(document, 'app root')
  document.activeElement = new FakeElement(document, 'opener')
  const approvals = []
  const currentPlan = planWithDuration()
  const store = {
    pendingH3Plan: currentPlan,
    pendingH3PlanEstimate: null,
    pendingH3PlanJobId: 'review-job',
    pendingH3PlanWorkspace: 'project one',
    jobs: [{ id: 'review-job', status: 'waiting_for_plan_approval', planReviewDeadline: 2_000 }],
    h3PlanReviewLoading: false,
    h3PlanReviewError: null,
    models: [],
    activeWorkspace: 'project one',
    hostTerms: { minimax_h3_ref2va: { accepted: true } },
    hostTermsLoading: false,
    hostTermsError: null,
    loadHostTerms() {},
    acceptHostTerm() {},
    closeH3PlanReview() {},
    approveH3Plan(payload) { approvals.push(payload) },
    cancelH3Plan() {},
  }
  resetHarness(document, store, { dialog, close: closeButton })
  globalThis.__h3HookState[10] = 'manual'
  globalThis.__h3HookState[11] = [24, 24]
  globalThis.__h3HookState[12] = 'none'
  t.after(() => {
    delete globalThis.document
    delete globalThis.window
    delete globalThis.HTMLElement
  })

  let tree = render(Dialog)
  const bar = findNodes(tree, node => node.type?.name === 'H3DurationPlanBar')[0]
  assert.ok(bar)
  assert.deepEqual(bar.props, {
    targetPublishedFrames: 48,
    currentPublishedFrames: 48,
    currentGeneratedFrames: 64,
    currentMinusTargetFrames: -0,
    outcome: 'exact',
    reason: 'The current plan matches your original target.',
  })
  const frameInputs = findNodes(tree, node => String(node.props?.['aria-label'] || '').startsWith('Final video frames for segment'))
  assert.equal(frameInputs.length, 2)
  assert.equal(frameInputs[0].props.disabled, false)
  assert.equal(frameInputs[1].props.disabled, true)
  const technicalReason = 'No proven segment-efficient boundary satisfies the selected snap mode.'
  assert.equal(currentPlan.duration_plan.snap_candidates.down.reason, technicalReason)
  let shorterLabel = findNodes(tree, node => node.type === 'label' && nodeText(node).includes('Shorter match'))[0]
  assert.match(nodeText(shorterLabel), /Continuum could not find a matching length it can confidently suggest/)
  assert.doesNotMatch(nodeText(shorterLabel), new RegExp(technicalReason.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  const technicalDetails = findNodes(tree, node => node.type === 'details' && nodeText(node).includes(technicalReason))[0]
  assert.ok(technicalDetails)
  assert.equal(technicalDetails.props.open, undefined)
  assert.match(nodeText(technicalDetails), /Technical details/)

  const unknownReason = '__proto__'
  currentPlan.duration_plan.snap_candidates.down.reason = unknownReason
  tree = render(Dialog)
  shorterLabel = findNodes(tree, node => node.type === 'label' && nodeText(node).includes('Shorter match'))[0]
  assert.match(nodeText(shorterLabel), /No shorter suggested length is available for this plan/)
  assert.doesNotMatch(nodeText(shorterLabel), new RegExp(unknownReason))
  assert.equal(currentPlan.duration_plan.snap_candidates.down.reason, unknownReason)
  const unknownDetails = findNodes(tree, node => node.type === 'details' && nodeText(node).includes(unknownReason))[0]
  assert.match(nodeText(unknownDetails), /Technical details/)
  assert.equal(unknownDetails.props.open, undefined)
  assert.match(nodeText(tree), /chart shows the saved plan/i)

  frameInputs[0].props.onChange({ currentTarget: { valueAsNumber: 23 } })
  const redistribution = findNodes(tree, node => node.props?.['aria-label'] === 'How to keep the original video length')[0]
  redistribution.props.onChange({ currentTarget: { value: 'future' } })
  tree = render(Dialog)
  const approveButton = findNodes(tree, node => node.type === 'button' && nodeText(node).includes('Approve & resume'))[0]
  assert.equal(approveButton.props.disabled, false)
  approveButton.props.onClick()
  assert.equal(approvals.length, 1)
  assert.deepEqual(approvals[0].planRevision, 'h3dp1_test-revision')
  assert.equal(approvals[0].durationSnapMode, 'manual')
  assert.deepEqual(approvals[0].segmentDurationEdits, [{ segmentIndex: 1, publishedFrames: 23 }])
  assert.equal(approvals[0].durationRedistribution, 'future')
  await Promise.resolve()

  globalThis.__h3HookState[10] = 'nearest'
  globalThis.__h3HookState[11] = [99, 24]
  tree = render(Dialog)
  const nearestApprove = findNodes(tree, node => node.type === 'button' && nodeText(node).includes('Approve & resume'))[0]
  assert.equal(nearestApprove.props.disabled, false, 'server snap is independent of stale disabled manual fields')
  assert.equal(findNodes(tree, node => node.props?.role === 'alert').length, 0)
  nearestApprove.props.onClick()
  assert.equal(approvals.length, 2)
  assert.equal(approvals[1].durationSnapMode, 'nearest')
  assert.deepEqual(approvals[1].segmentDurationEdits, [])
  assert.equal(approvals[1].durationRedistribution, 'none')
})

test('H3 plan mobile shell compiles four-edge safe areas, dynamic viewport, scrolling, touch, zoom, and reduced-motion rules', async () => {
  const source = await readFile(dialogUrl, 'utf8')
  assert.match(source, /Choose FL2VA when exact start or end frames matter/)
  assert.match(source, /Edit the segments yourself, or choose a suggested length/)
  assert.match(source, /Continuum will approve the saved plan unchanged/)
  assert.doesNotMatch(source, /server-authored|frozen server plan|frame geometry|server range|server locked|Checkpoint retained|Checkpoint switch/i)
  assert.match(source, /priority: 180/)
  assert.match(source, /z-\[180\]/)
  for (const edge of ['top', 'right', 'bottom', 'left']) {
    assert.match(source, new RegExp(`safe-area-inset-${edge}`))
  }
  for (const token of [
    'h-[100vh]',
    'supports-[height:100dvh]:h-[100dvh]',
    'max-h-[calc(100vh-1.5rem)]',
    'supports-[height:100dvh]:max-h-[calc(100dvh-1.5rem)]',
    'max-h-[45vh]',
    'overflow-y-auto',
    'overscroll-contain',
    'min-h-11',
    'w-full',
    'max-w-full',
    'motion-reduce:[&_*]:transition-none',
    'motion-reduce:[&_*]:animate-none',
  ]) assert.ok(source.includes(token), `missing responsive token ${token}`)

  const candidates = [
    'h-[100vh]',
    'supports-[height:100dvh]:h-[100dvh]',
    'max-h-[calc(100vh-1.5rem)]',
    'supports-[height:100dvh]:max-h-[calc(100dvh-1.5rem)]',
    'max-h-[45vh]',
    'overflow-y-auto',
    'min-h-[44px]',
    'motion-reduce:[&_*]:transition-none',
  ]
  const utilities = await compile('@tailwind utilities;')
  const css = utilities.build(candidates)
  assert.doesNotMatch(source, /md:min-h-0|md:min-w-0/)
  assert.match(css, /100dvh/)
  assert.match(css, /prefers-reduced-motion/)
  assert.match(css, /min-height:\s*44px/)
})
