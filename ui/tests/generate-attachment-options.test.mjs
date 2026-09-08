import assert from 'node:assert/strict'
import test from 'node:test'

import {
  GENERATE_ATTACHMENT_LABELS,
  acceptedProjectReferenceKinds,
  filterProjectReferenceChoices,
  orderGenerateAttachmentOptions,
  resolveGenerateAttachmentCapabilities,
  resolveH3InputCompatibility,
} from '../src/lib/generateAttachmentOptions.ts'

const ids = options => options.map(option => option.id)
const enabledIds = options => options.filter(option => option.enabled).map(option => option.id)
const disabledIds = options => options.filter(option => !option.enabled).map(option => option.id)

test('H3 Ref2VA disables first/last and keeps it last after project refs', () => {
  const options = orderGenerateAttachmentOptions(resolveGenerateAttachmentCapabilities({
    modelType: 'minimax_h3_ref2va',
    architecture: 'minimax_h3_ref2va',
    conditioningMode: 'semantic_references',
    mutuallyExclusiveConditioning: true,
    supportsRefImages: true,
    supportsEndFrame: false,
    supportsAudioInput: true,
    referenceImageMaxCount: 9,
    referenceVideoMaxCount: 3,
    referenceAudioMaxCount: 3,
    adaptiveConditioning: false,
  }))
  assert.deepEqual(ids(options), [
    'project_reference',
    'reference_image',
    'reference_video',
    'reference_audio',
    'first_last_frame',
  ])
  assert.deepEqual(enabledIds(options), [
    'project_reference',
    'reference_image',
    'reference_video',
    'reference_audio',
  ])
  assert.deepEqual(disabledIds(options), ['first_last_frame'])
  assert.equal(options.at(-1).label, GENERATE_ATTACHMENT_LABELS.first_last_frame)
  assert.match(options.at(-1).reason, /first\/last frames/)
  assert.equal(options.at(-1).enabled, false)
})

test('H3 FL2VA disables reference kinds and keeps them last', () => {
  const options = orderGenerateAttachmentOptions(resolveGenerateAttachmentCapabilities({
    modelType: 'minimax_h3',
    architecture: 'minimax_h3',
    conditioningMode: 'first_last_frames',
    mutuallyExclusiveConditioning: true,
    supportsEndFrame: true,
    supportsRefImages: false,
    adaptiveConditioning: false,
  }))
  assert.equal(options[0].id, 'project_reference')
  assert.equal(options[0].enabled, true)
  assert.deepEqual(enabledIds(options), ['project_reference', 'first_last_frame'])
  assert.deepEqual(disabledIds(options), [
    'reference_image',
    'reference_video',
    'reference_audio',
  ])
  assert.equal(options.at(-1).id, 'reference_audio')
  assert.match(options.find(option => option.id === 'reference_image').reason, /first\/last frames, not reference images/)
})

test('explicit adaptive H3 offers edge anchors and semantic media across planned segments', () => {
  for (const modelType of ['minimax_h3', 'minimax_h3_ref2va']) {
    const caps = resolveGenerateAttachmentCapabilities({
      modelType, mutuallyExclusiveConditioning: true, supportsEndFrame: false,
      supportsRefImages: false, adaptiveConditioning: true,
    })
    assert.deepEqual(enabledIds(orderGenerateAttachmentOptions(caps)), [
      'project_reference', 'first_last_frame', 'reference_image', 'reference_video', 'reference_audio',
    ])
    assert.deepEqual(disabledIds(orderGenerateAttachmentOptions(caps)), [])
  }
})

test('LightX2V and Spectrum stay FL2VA-only even if ref flags leak', () => {
  for (const h3ProfileId of ['lightx2v_experimental', 'spectrum_experimental']) {
    const options = orderGenerateAttachmentOptions(resolveGenerateAttachmentCapabilities({
      modelType: 'minimax_h3',
      architecture: 'minimax_h3',
      conditioningMode: 'first_last_frames',
      supportsEndFrame: true,
      supportsRefImages: true,
      hasImageRefChoices: true,
      h3ProfileId,
    }))
    assert.deepEqual(enabledIds(options), ['project_reference', 'first_last_frame'], h3ProfileId)
    assert.ok(disabledIds(options).includes('reference_image'), h3ProfileId)
  }
})

test('audio-incapable models disable audio and move it to the end', () => {
  const options = orderGenerateAttachmentOptions(resolveGenerateAttachmentCapabilities({
    modelType: 'wan_i2v',
    supportsEndFrame: true,
    supportsRefImages: false,
    supportsAudioInput: false,
  }))
  assert.deepEqual(enabledIds(options), ['project_reference', 'first_last_frame'])
  assert.equal(options.at(-1).id, 'reference_audio')
  assert.equal(options.at(-1).enabled, false)
  assert.match(options.at(-1).reason, /audio references/)
})

test('project references come first and filter to accepted kinds', () => {
  const ref2va = resolveGenerateAttachmentCapabilities({
    modelType: 'minimax_h3_ref2va',
    architecture: 'minimax_h3_ref2va',
    conditioningMode: 'semantic_references',
    mutuallyExclusiveConditioning: true,
  })
  const fl2va = resolveGenerateAttachmentCapabilities({
    modelType: 'minimax_h3',
    architecture: 'minimax_h3',
    conditioningMode: 'first_last_frames',
    mutuallyExclusiveConditioning: true,
    supportsEndFrame: true,
    adaptiveConditioning: false,
  })
  const choices = [
    { key: 'img', kind: 'image' },
    { key: 'vid', kind: 'video' },
    { key: 'aud', kind: 'audio' },
  ]

  assert.deepEqual(acceptedProjectReferenceKinds(ref2va), ['image', 'video', 'audio'])
  assert.deepEqual(filterProjectReferenceChoices(choices, ref2va).map(item => item.key), ['img', 'vid', 'aud'])
  assert.deepEqual(acceptedProjectReferenceKinds(fl2va), ['image'])
  assert.deepEqual(filterProjectReferenceChoices(choices, fl2va).map(item => item.key), ['img'])
  assert.equal(orderGenerateAttachmentOptions(ref2va)[0].id, 'project_reference')
  assert.equal(orderGenerateAttachmentOptions(fl2va)[0].id, 'project_reference')
})

test('models with no attachment kinds still list project refs last and disabled', () => {
  const options = orderGenerateAttachmentOptions(resolveGenerateAttachmentCapabilities({
    modelType: 'flux_t2i',
    supportsEndFrame: false,
    supportsRefImages: false,
    supportsAudioInput: false,
  }))
  assert.equal(options.at(-1).id, 'project_reference')
  assert.equal(options.at(-1).enabled, false)
  assert.ok(options.every(option => option.enabled === false))
})

test('labels stay honest and items are not removed when disabled', () => {
  const options = orderGenerateAttachmentOptions(resolveGenerateAttachmentCapabilities({
    modelType: 'minimax_h3_ref2va',
    architecture: 'minimax_h3_ref2va',
    conditioningMode: 'semantic_references',
  }))
  assert.equal(options.find(option => option.id === 'first_last_frame').label, 'First / last frame')
  assert.equal(options.find(option => option.id === 'reference_image').label, 'Reference image')
  assert.deepEqual(
    new Set(options.map(option => option.label)),
    new Set(Object.values(GENERATE_ATTACHMENT_LABELS)),
  )
})

test('short H3 videos keep frame and semantic additions mutually eligible only before either is chosen', () => {
  const atNativeBoundary = resolveH3InputCompatibility({
    durationSeconds: 5,
    framesMaximum: 120,
    fps: 24,
    hasFrameInputs: true,
    imageCount: 1,
    videoCount: 0,
    audioCount: 0,
  })
  assert.equal(atNativeBoundary.nativeMaximumSeconds, 5)
  assert.equal(atNativeBoundary.hasShortMixedInputs, true)
  assert.equal(atNativeBoundary.canAddFrame, false)
  assert.equal(atNativeBoundary.canAddImage, false)
  assert.equal(atNativeBoundary.canAddVideo, false)
  assert.match(atNativeBoundary.invalidReason, /more than 5\.00s/)

  const frameOnly = resolveH3InputCompatibility({
    durationSeconds: 5,
    framesMaximum: 120,
    fps: 24,
    hasFrameInputs: true,
    imageCount: 0,
    videoCount: 0,
    audioCount: 0,
  })
  assert.equal(frameOnly.canAddFrame, true)
  assert.equal(frameOnly.canAddImage, false)
  assert.match(frameOnly.semanticReason, /Remove start\/end frames/)

  const textOnlyRef2VA = resolveH3InputCompatibility({
    durationSeconds: 5,
    framesMaximum: 120,
    fps: 24,
    hasFrameInputs: false,
    imageCount: 0,
    videoCount: 0,
    audioCount: 0,
  })
  assert.equal(textOnlyRef2VA.invalidReason, null)
  assert.equal(textOnlyRef2VA.canAddImage, true)
  assert.equal(textOnlyRef2VA.canAddAudio, false)
})

test('long H3 videos allow mixed inputs and report exact currently addable counts', () => {
  const compatibility = resolveH3InputCompatibility({
    durationSeconds: 5.01,
    framesMaximum: 120,
    fps: 24,
    hasFrameInputs: true,
    imageCount: 2,
    videoCount: 1,
    audioCount: 1,
  })
  assert.equal(compatibility.hasShortMixedInputs, false)
  assert.equal(compatibility.invalidReason, null)
  assert.equal(compatibility.canAddFrame, true)
  assert.equal(compatibility.canAddImage, true)
  assert.equal(compatibility.canAddVideo, true)
  assert.equal(compatibility.canAddAudio, true)
  assert.deepEqual(compatibility.remaining, {
    images: 7,
    videos: 2,
    audio: 2,
    mixed: 8,
    pairedAudio: 2,
  })

  const oneMixedSlot = resolveH3InputCompatibility({
    durationSeconds: 20,
    framesMaximum: 120,
    fps: 24,
    hasFrameInputs: false,
    imageCount: 8,
    videoCount: 2,
    audioCount: 1,
  })
  assert.deepEqual(oneMixedSlot.remaining, {
    images: 1,
    videos: 1,
    audio: 1,
    mixed: 1,
    pairedAudio: 9,
  })
})

test('H3 audio eligibility never exceeds the available visual pairings', () => {
  const paired = resolveH3InputCompatibility({
    durationSeconds: 20,
    framesMaximum: 120,
    fps: 24,
    hasFrameInputs: false,
    imageCount: 1,
    videoCount: 1,
    audioCount: 2,
  })
  assert.equal(paired.remaining.audio, 0)
  assert.equal(paired.canAddAudio, false)
  assert.match(paired.audioReason, /Add an image or video/)

  const invalidRestoredState = resolveH3InputCompatibility({
    durationSeconds: 20,
    framesMaximum: 120,
    fps: 24,
    hasFrameInputs: false,
    imageCount: 1,
    videoCount: 0,
    audioCount: 2,
  })
  assert.match(invalidRestoredState.invalidReason, /one image or video for each audio/)
  assert.equal(invalidRestoredState.canAddImage, true)
})
