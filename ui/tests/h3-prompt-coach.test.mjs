import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

const UI_ROOT = fileURLToPath(new URL('..', import.meta.url))

const pureBundle = await build({
  stdin: {
    contents: `
      export { isExactH3PromptReviewTarget, reviewH3Prompt } from './src/lib/h3PromptReview.ts'
      export { timelineMarkerSummary } from './src/lib/timelinePrompt.ts'
    `,
    resolveDir: UI_ROOT,
    loader: 'js',
  },
  bundle: true,
  format: 'esm',
  logLevel: 'silent',
  platform: 'browser',
  treeShaking: true,
  write: false,
})
const {
  isExactH3PromptReviewTarget,
  reviewH3Prompt,
  timelineMarkerSummary,
} = await import(asDataModule(pureBundle.outputFiles[0].text))

function baseInput(overrides = {}) {
  return {
    prompt: 'A freeform scene description.',
    modelType: 'minimax_h3',
    architecture: 'minimax_h3',
    imageCount: 0,
    videoCount: 0,
    audioCount: 0,
    hasStartAnchor: false,
    hasEndAnchor: false,
    durationSeconds: 10,
    adaptiveConditioning: true,
    ...overrides,
  }
}

function check(review, id) {
  return review.checks.find(item => item.id === id)
}

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
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

let componentModulePromise
function loadComponentModule() {
  if (componentModulePromise) return componentModulePromise
  componentModulePromise = build({
    stdin: {
      contents: "export { H3PromptCoach } from './src/components/Sidebar/H3PromptCoach.tsx'",
      resolveDir: UI_ROOT,
      loader: 'js',
    },
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'browser',
    treeShaking: true,
    write: false,
    plugins: [{
      name: 'h3-prompt-coach-test-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'coach-test' }))
        bundle.onLoad({ filter: /.*/, namespace: 'coach-test' }, () => ({ contents: `
          export const Fragment = Symbol.for('h3-prompt-coach-fragment')
          export const jsx = (type, props, key) => ({ type, key, props: props || {} })
          export const jsxs = jsx
        ` }))
      },
    }],
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return componentModulePromise
}

test('review gating uses only exact H3 model IDs and architectures', () => {
  for (const modelType of [
    'minimax_h3',
    'minimax_h3_pinkcherry_fl2va',
    'minimax_h3_w4a8_fl2va',
    'minimax_h3_ref2va',
  ]) assert.equal(isExactH3PromptReviewTarget(modelType, null), true)
  assert.equal(isExactH3PromptReviewTarget('other', 'minimax_h3'), false)
  assert.equal(isExactH3PromptReviewTarget('other', 'minimax_h3_ref2va'), false)
  assert.equal(isExactH3PromptReviewTarget('minimax_h3', 'minimax_h3'), true)
  assert.equal(isExactH3PromptReviewTarget('minimax_h3', 'minimax_h3_spoof'), false)
  assert.equal(isExactH3PromptReviewTarget('minimax_h3_spoof', null), false)
  assert.equal(isExactH3PromptReviewTarget('other', 'minimax_h3_spoof'), false)
  assert.equal(reviewH3Prompt(baseInput({ modelType: 'other', architecture: 'other' })), null)
})

test('freeform is valid information and review never mutates prompt bytes', () => {
  const prompt = 'Line one\r\n\r\nLine two — keep every byte.'
  const before = Buffer.from(prompt)
  const review = reviewH3Prompt(baseInput({ prompt }))
  assert.equal(review.structure, 'freeform')
  assert.equal(check(review, 'prompt-structure').status, 'info')
  assert.match(check(review, 'prompt-structure').detail, /Freeform is valid/)
  assert.deepEqual(Buffer.from(prompt), before)
  assert.deepEqual(review.checks.map(item => item.id), [
    'prompt-structure', 'model-family', 'timeline-markers', 'timeline-coverage', 'reference-counts',
  ])
})

test('Base, Ref2VA, and mixed canonical fields are counted without rewriting', () => {
  const base = reviewH3Prompt(baseInput({ prompt: `
subject_definitions: subjects
integrated_multimodal_description: A shot.
overall_soundscape: Room tone.
non_diegetic_music: N/A
` }))
  assert.equal(base.structure, 'base')
  assert.equal(check(base, 'prompt-structure').status, 'noted')
  assert.equal(check(base, 'prompt-structure').detail, '4/4 canonical fields found.')
  assert.equal(check(base, 'field-order').status, 'noted')
  assert.equal(check(base, 'field-values').status, 'noted')

  const missingBaseField = reviewH3Prompt(baseInput({ prompt: `
integrated_multimodal_description: A shot.
overall_soundscape: Room tone.
non_diegetic_music: N/A
` }))
  assert.equal(missingBaseField.structure, 'base')
  assert.equal(check(missingBaseField, 'prompt-structure').status, 'consider')
  assert.equal(check(missingBaseField, 'prompt-structure').detail, '3/4 canonical fields found.')

  const disorder = reviewH3Prompt(baseInput({ prompt: `
overall_soundscape: first
subject_definitions: subjects
integrated_multimodal_description: shot
overall_soundscape: duplicate
non_diegetic_music: N/A
` }))
  assert.equal(check(disorder, 'field-order').status, 'consider')
  assert.match(check(disorder, 'field-order').detail, /1 duplicate fields · [1-9]\d* out-of-order fields/)

  const partialShared = reviewH3Prompt(baseInput({ prompt: `
subject_definitions: subjects
overall_soundscape: room tone
` }))
  assert.equal(partialShared.structure, 'freeform')
  assert.equal(check(partialShared, 'prompt-structure').status, 'consider')
  assert.match(check(partialShared, 'prompt-structure').detail, /known top-level fields found without a complete Base or Ref2VA family/)

  const ref2vaPrompt = `
subject_definitions: definitions
summary: summary
retention_analysis: retention
detailed_description: details
overall_soundscape: sound
non_diegetic_music: N/A
`
  const ref2va = reviewH3Prompt(baseInput({ prompt: ref2vaPrompt }))
  assert.equal(ref2va.structure, 'ref2va')
  assert.equal(check(ref2va, 'prompt-structure').detail, '6/6 canonical fields found.')

  const mixed = reviewH3Prompt(baseInput({
    prompt: `integrated_multimodal_description: base\n${ref2vaPrompt}`,
  }))
  assert.equal(mixed.structure, 'mixed')
  assert.equal(check(mixed, 'prompt-structure').status, 'consider')
})

test('canonical field spans distinguish empty headers from following shot payloads', () => {
  const emptyBase = reviewH3Prompt(baseInput({ prompt: `
subject_definitions:
integrated_multimodal_description:
[Shot 1] | audiovisual_description: a complete visual record
overall_soundscape:
non_diegetic_music:
` }))
  assert.equal(emptyBase.structure, 'base')
  assert.equal(check(emptyBase, 'field-values').status, 'consider')
  assert.equal(check(emptyBase, 'field-values').detail, '3/4 canonical field entries have no payload before the next top-level field.')

  const emptyRef2va = reviewH3Prompt(baseInput({ prompt: `
subject_definitions:
summary:
retention_analysis:
detailed_description:
[Shot 1] | audiovisual_description: a complete visual record
overall_soundscape:
non_diegetic_music:
` }))
  assert.equal(emptyRef2va.structure, 'ref2va')
  assert.equal(check(emptyRef2va, 'field-values').status, 'consider')
  assert.equal(check(emptyRef2va, 'field-values').detail, '5/6 canonical field entries have no payload before the next top-level field.')
})

test('model-family mismatch follows the server effective Ref2VA formula exactly', () => {
  const basePrompt = `
subject_definitions: subjects
integrated_multimodal_description: visual
overall_soundscape: sound
non_diegetic_music: N/A
`
  const ref2vaPrompt = `
subject_definitions: subjects
summary: summary
retention_analysis: retention
detailed_description: visual
overall_soundscape: sound
non_diegetic_music: N/A
`
  const pinnedRef2vaOnBase = reviewH3Prompt(baseInput({
    modelType: 'minimax_h3_ref2va', prompt: basePrompt, adaptiveConditioning: false,
  }))
  assert.equal(check(pinnedRef2vaOnBase, 'model-family').status, 'consider')
  assert.match(check(pinnedRef2vaOnBase, 'model-family').detail, /Effective Ref2VA routing and structured Base fields/)

  const adaptiveRef2vaWithoutRefsOnBase = reviewH3Prompt(baseInput({
    modelType: 'minimax_h3_ref2va', prompt: basePrompt, adaptiveConditioning: true,
  }))
  assert.equal(check(adaptiveRef2vaWithoutRefsOnBase, 'model-family').status, 'info')

  const adaptiveRef2vaWithoutRefsOnRef = reviewH3Prompt(baseInput({
    modelType: 'minimax_h3_ref2va', prompt: ref2vaPrompt, adaptiveConditioning: true,
  }))
  assert.equal(check(adaptiveRef2vaWithoutRefsOnRef, 'model-family').status, 'consider')

  const adaptiveFl2vaWithoutRefs = reviewH3Prompt(baseInput({
    modelType: 'minimax_h3', prompt: ref2vaPrompt, adaptiveConditioning: true,
  }))
  assert.equal(check(adaptiveFl2vaWithoutRefs, 'model-family').status, 'consider')

  const adaptiveFl2vaWithRefs = reviewH3Prompt(baseInput({
    modelType: 'minimax_h3', prompt: ref2vaPrompt, adaptiveConditioning: true, imageCount: 1,
  }))
  assert.equal(check(adaptiveFl2vaWithRefs, 'model-family').status, 'info')

  const pinnedFl2vaWithRefs = reviewH3Prompt(baseInput({
    modelType: 'minimax_h3', prompt: ref2vaPrompt, adaptiveConditioning: false, imageCount: 1,
  }))
  assert.equal(check(pinnedFl2vaWithRefs, 'model-family').status, 'consider')

  const adaptiveRef2vaWithRefs = reviewH3Prompt(baseInput({
    modelType: 'minimax_h3_ref2va', prompt: ref2vaPrompt, adaptiveConditioning: true, audioCount: 1,
  }))
  assert.equal(check(adaptiveRef2vaWithRefs, 'model-family').status, 'info')
})

test('unknown structured headers including spaced labels terminate spans and stay anonymous', () => {
  const review = reviewH3Prompt(baseInput({ prompt: `
integrated_multimodal_description:
Unexpected Field: value
made_up_field: value
another_unknown_field: value
overall_soundscape: sound
non_diegetic_music: N/A
` }))
  const unexpected = check(review, 'unexpected-fields')
  assert.equal(unexpected.status, 'consider')
  assert.equal(unexpected.detail, '3 unrecognized structured top-level labels found.')
  assert.equal(check(review, 'field-values').status, 'consider')
  assert.match(check(review, 'field-values').detail, /canonical field entries have no payload/)
  const serialized = JSON.stringify(review)
  assert.doesNotMatch(serialized, /Unexpected Field|made_up_field|another_unknown_field/i)
})

test('timeline review is bounded and counts structural ranges and points', () => {
  const prompt = `${Array.from({ length: 140 }, (_, index) => `[00:${String(index % 60).padStart(2, '0')}-00:${String((index % 60) + 1).padStart(2, '0')}] beat`).join('\n')}\n${'x'.repeat(70_000)}`
  const summary = timelineMarkerSummary(prompt)
  assert.equal(summary.markerCount, 128)
  assert.equal(summary.rangeCount, 128)
  assert.equal(summary.pointCount, 0)
  assert.equal(summary.malformedReversedCount, 0)
  assert.equal(summary.truncated, true)
})

test('range-only coverage reports starts, gaps, overlaps, duration delta, and reversed ranges', () => {
  const review = reviewH3Prompt(baseInput({
    durationSeconds: 10,
    prompt: `
[00:00-00:03] first
[00:04-00:06] second
[00:05-00:08] overlap
[00:09-00:07] reversed
at 00:10 point only
`,
  }))
  assert.deepEqual(review.timeline, {
    markerCount: 4,
    rangeCount: 3,
    pointCount: 1,
    malformedReversedCount: 1,
    coverageStartSeconds: 0,
    coverageEndSeconds: 8,
    gapCount: 1,
    overlapCount: 1,
    endDeltaSeconds: -2,
    truncated: false,
  })
  const coverage = check(review, 'timeline-coverage')
  assert.equal(coverage.status, 'consider')
  assert.match(coverage.detail, /Starts at 0s · 1 gaps · 1 overlaps · ends at 8s versus selected 10s \(delta -2s\) · 1 reversed ranges ignored/)

  const pointOnly = reviewH3Prompt(baseInput({ prompt: 'at 00:10 point cue', durationSeconds: 10 }))
  assert.equal(check(pointOnly, 'timeline-coverage').status, 'info')
  assert.match(check(pointOnly, 'timeline-coverage').detail, /1 point cues found; point cues do not establish coverage/)
})

test('media ordinals and anchor conditioning remain exact and advisory', () => {
  const review = reviewH3Prompt(baseInput({
    prompt: `
<Picture 1> and <Picture 2> establish identity. <Video 1> supplies motion.
<Audio 1> supplies voice. <Audio 9> is stale.
opening blocking: first pose
final blocking: last pose
camera: slow push
`,
    imageCount: 2,
    videoCount: 1,
    audioCount: 2,
    hasStartAnchor: true,
    hasEndAnchor: true,
  }))
  assert.deepEqual(review.media, {
    imageCount: 2,
    videoCount: 1,
    audioCount: 2,
    totalCount: 5,
    expectedOrdinalCount: 5,
    mentionedOrdinalCount: 4,
    unexpectedOrdinalCount: 1,
  })
  assert.equal(check(review, 'reference-ordinals').status, 'consider')
  assert.equal(check(review, 'start-conditioning').status, 'info')
  assert.equal(check(review, 'end-conditioning').status, 'info')
  assert.match(check(review, 'start-conditioning').detail, /start frame owns the opening state/)
  assert.match(check(review, 'end-conditioning').detail, /end frame owns the final state/)

  const noAnchors = reviewH3Prompt(baseInput({ prompt: 'opening blocking: prose\nfinal blocking: prose' }))
  assert.equal(check(noAnchors, 'start-conditioning'), undefined)
  assert.equal(check(noAnchors, 'end-conditioning'), undefined)
  assert.doesNotMatch(JSON.stringify(review), /opening blocking|final blocking/i)
})

test('content-neutral structural twins produce identical reviews without subject wording', () => {
  const structuralPrefix = `integrated_multimodal_description:\n[00:00-00:05] `
  const neutral = `${structuralPrefix}Adults discuss a community garden while the action continues with <Picture 1>.\noverall_soundscape: room tone\nnon_diegetic_music: N/A`
  const sensitive = `${structuralPrefix}Adults stage violent, controversial sexual theatre while the camera crash-zooms with <Picture 1>.\noverall_soundscape: room tone\nnon_diegetic_music: N/A`
  const input = {
    imageCount: 1,
    videoCount: 0,
    audioCount: 0,
    hasStartAnchor: false,
    hasEndAnchor: false,
  }
  const neutralReview = reviewH3Prompt(baseInput({ ...input, prompt: neutral }))
  const sensitiveReview = reviewH3Prompt(baseInput({ ...input, prompt: sensitive }))
  assert.deepEqual(neutralReview, sensitiveReview)
  const serialized = JSON.stringify(sensitiveReview).toLowerCase()
  for (const token of ['sexual', 'violent', 'controversial', 'theatre', 'adult', 'camera', 'crash-zoom']) {
    assert.equal(serialized.includes(token), false)
  }
})

test('native coach UI is collapsed, non-color-coded, and never overclaims readiness', async () => {
  const { H3PromptCoach } = await loadComponentModule()
  const tree = H3PromptCoach(baseInput())
  assert.equal(tree.type, 'details')
  const elements = flattenElements(tree)
  const summary = elements.find(element => element.type === 'summary')
  assert.match(summary.props.className, /mobile-control-target/)
  assert.match(summary.props.className, /focus-visible:ring-2/)
  assert.ok(elements.some(element => element.type === 'ul'))
  assert.ok(elements.filter(element => element.type === 'li').every(element => element.props['data-check-id']))
  assert.match(elementText(tree), /Prompt CoachStructural review only · your prompt is unchanged/)
  const politeStatus = elements.find(element => element.props?.role === 'status')
  assert.equal(politeStatus.props['aria-live'], 'polite')
  assert.equal(politeStatus.props['aria-atomic'], 'true')
  assert.match(elementText(politeStatus), /^Prompt Coach structural review updated: \d+ to consider, \d+ total checks\.$/)
  assert.doesNotMatch(elementText(politeStatus), /Prompt structure|Timeline markers|Attached references/)
  assert.doesNotMatch(elementText(tree), /production.ready|ready for production|prompt is ready/i)
  assert.equal(H3PromptCoach(baseInput({ modelType: 'other', architecture: 'other' })), null)
})

test('coach source stays local, pure, content-neutral, and wired without prompt mutation', async () => {
  const [review, component, promptInput] = await Promise.all([
    readFile(new URL('../src/lib/h3PromptReview.ts', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/Sidebar/H3PromptCoach.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/Sidebar/PromptInput.tsx', import.meta.url), 'utf8'),
  ])
  for (const source of [review, component]) {
    assert.doesNotMatch(source, /fetch\(|useEffect|useStore|localStorage|sessionStorage|setParam|enhancePrompt|moderation|classif(?:y|ication)|rewrite/i)
  }
  assert.doesNotMatch(review, /opening blocking|final blocking|\bcamera\b/i)
  assert.match(component, /Structural review only · your prompt is unchanged/)
  assert.match(component, /<details/)
  assert.match(component, /<ul/)
  assert.match(promptInput, /<H3PromptCoach/)
  assert.match(promptInput, /showH3PromptCoach/)
  assert.match(promptInput, /const showH3PromptCoach = Boolean\(prompt\.trim\(\)\)/)
  assert.match(component, /role="status" aria-live="polite" aria-atomic="true"/)
  assert.ok(promptInput.indexOf('<textarea') < promptInput.indexOf('<H3PromptCoach'))
})
