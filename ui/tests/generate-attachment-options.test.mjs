import assert from 'node:assert/strict'
import test from 'node:test'

import {
  GENERATE_ATTACHMENT_LABELS,
  acceptedProjectReferenceKinds,
  filterProjectReferenceChoices,
  orderGenerateAttachmentOptions,
  resolveGenerateAttachmentCapabilities,
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
    adaptiveConditioning: true,
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
