import { timelineMarkerSummary, type TimelineMarkerSummary } from './timelinePrompt'

export type H3PromptReviewStatus = 'noted' | 'info' | 'consider'
export type H3PromptStructure = 'freeform' | 'base' | 'ref2va' | 'mixed'

export interface H3PromptReviewCheck {
  id: string
  label: string
  status: H3PromptReviewStatus
  detail: string
}

export interface H3PromptReviewInput {
  prompt: string
  modelType: string
  architecture?: string | null
  imageCount: number
  videoCount: number
  audioCount: number
  hasStartAnchor: boolean
  hasEndAnchor: boolean
  durationSeconds: number
  adaptiveConditioning: boolean
}

export interface H3PromptReview {
  structure: H3PromptStructure
  timeline: TimelineMarkerSummary
  media: {
    imageCount: number
    videoCount: number
    audioCount: number
    totalCount: number
    expectedOrdinalCount: number
    mentionedOrdinalCount: number
    unexpectedOrdinalCount: number
  }
  checks: H3PromptReviewCheck[]
}

const EXACT_H3_MODEL_TYPES = new Set([
  'minimax_h3',
  'minimax_h3_pinkcherry_fl2va',
  'minimax_h3_w4a8_fl2va',
  'minimax_h3_ref2va',
])
const EXACT_H3_ARCHITECTURES = new Set(['minimax_h3', 'minimax_h3_ref2va'])
const BASE_FIELDS = ['subject_definitions', 'integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music'] as const
const REF2VA_FIELDS = ['subject_definitions', 'summary', 'retention_analysis', 'detailed_description', 'overall_soundscape', 'non_diegetic_music'] as const
type CanonicalField = typeof BASE_FIELDS[number] | typeof REF2VA_FIELDS[number]
const KNOWN_FIELD_SET: ReadonlySet<string> = new Set([...BASE_FIELDS, ...REF2VA_FIELDS])
const MAX_STRUCTURAL_PROMPT_CHARS = 65_536
const MAX_TOP_LEVEL_LINES = 512
const MAX_TOP_LEVEL_ENTRIES = 256

interface TopLevelFieldEntry {
  label: string
  hasPayload: boolean
}

function isCanonicalField(label: string): label is CanonicalField {
  return KNOWN_FIELD_SET.has(label)
}

function boundedCount(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.min(99, Math.trunc(value))) : 0
}

function hasLiteralField(prompt: string, field: string): boolean {
  const escaped = field.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`^\\s*${escaped}\\s*:`, 'im').test(prompt)
}

function countPresentFields(prompt: string, fields: readonly string[]): number {
  return fields.filter(field => hasLiteralField(prompt, field)).length
}

function topLevelFieldEntries(prompt: string): TopLevelFieldEntry[] {
  const lines = prompt.split('\n').slice(0, MAX_TOP_LEVEL_LINES)
  const entries: TopLevelFieldEntry[] = []
  let current: TopLevelFieldEntry | null = null
  for (const line of lines) {
    if (entries.length >= MAX_TOP_LEVEL_ENTRIES) break
    const match = /^\s*([a-z][a-z0-9_ ]{0,63})\s*:\s*(.*)$/i.exec(line)
    if (match) {
      current = { label: match[1].trim().toLowerCase(), hasPayload: match[2].trim().length > 0 }
      entries.push(current)
    } else if (current && line.trim()) {
      current.hasPayload = true
    }
  }
  return entries
}

function expectedOrdinals(imageCount: number, videoCount: number, audioCount: number): string[] {
  const result: string[] = []
  for (let index = 1; index <= imageCount; index += 1) result.push(`<Picture ${index}>`)
  for (let index = 1; index <= videoCount; index += 1) result.push(`<Video ${index}>`)
  for (let index = 1; index <= audioCount; index += 1) result.push(`<Audio ${index}>`)
  return result
}

export function isExactH3PromptReviewTarget(modelType: string, architecture?: string | null): boolean {
  if (!EXACT_H3_MODEL_TYPES.has(modelType)) return false
  return !architecture || EXACT_H3_ARCHITECTURES.has(architecture)
}

export function reviewH3Prompt(input: H3PromptReviewInput): H3PromptReview | null {
  if (!isExactH3PromptReviewTarget(input.modelType, input.architecture)) return null

  const prompt = String(input.prompt || '').slice(0, MAX_STRUCTURAL_PROMPT_CHARS)
  const imageCount = boundedCount(input.imageCount)
  const videoCount = boundedCount(input.videoCount)
  const audioCount = boundedCount(input.audioCount)
  const totalCount = imageCount + videoCount + audioCount
  const basePresent = countPresentFields(prompt, BASE_FIELDS)
  const ref2vaPresent = countPresentFields(prompt, REF2VA_FIELDS)
  const baseVisual = hasLiteralField(prompt, 'integrated_multimodal_description')
  const ref2vaVisual = hasLiteralField(prompt, 'detailed_description')
  const ref2vaSignal = ref2vaVisual
    || hasLiteralField(prompt, 'summary')
    || hasLiteralField(prompt, 'retention_analysis')
  const structure: H3PromptStructure = baseVisual && ref2vaVisual
    ? 'mixed'
    : hasLiteralField(prompt, 'integrated_multimodal_description') ? 'base'
      : ref2vaSignal ? 'ref2va' : 'freeform'
  const timeline = timelineMarkerSummary(input.prompt, input.durationSeconds)
  const topLevelEntries = topLevelFieldEntries(prompt)
  const knownEntries = topLevelEntries.filter(
    (entry): entry is TopLevelFieldEntry & { label: CanonicalField } => isCanonicalField(entry.label),
  )
  const knownFields = knownEntries.map(entry => entry.label)
  const unexpectedFieldCount = topLevelEntries.filter(entry => !isCanonicalField(entry.label)).length
  const ordinals = expectedOrdinals(imageCount, videoCount, audioCount)
  const ordinalSet = new Set(ordinals)
  const literalOrdinals = new Set(Array.from(
    prompt.matchAll(/<(?:Picture|Video|Audio) [1-9]\d*>/g),
    match => match[0],
  ))
  const mentionedOrdinalCount = ordinals.filter(ordinal => prompt.includes(ordinal)).length
  const unexpectedOrdinalCount = Array.from(literalOrdinals).filter(ordinal => !ordinalSet.has(ordinal)).length
  const checks: H3PromptReviewCheck[] = []

  if (structure === 'freeform') {
    const partialKnownFields = knownFields.length > 0
    checks.push({
      id: 'prompt-structure',
      label: 'Prompt structure',
      status: partialKnownFields ? 'consider' : 'info',
      detail: partialKnownFields
        ? `${knownFields.length} known top-level fields found without a complete Base or Ref2VA family. Freeform remains valid.`
        : 'Freeform is valid. Canonical field structure is optional.',
    })
  } else if (structure === 'mixed') {
    checks.push({
      id: 'prompt-structure',
      label: 'Prompt structure',
      status: 'consider',
      detail: `Base fields ${basePresent}/${BASE_FIELDS.length} · Ref2VA fields ${ref2vaPresent}/${REF2VA_FIELDS.length}. Consider using one field family.`,
    })
  } else {
    const present = structure === 'base' ? basePresent : ref2vaPresent
    const expected = structure === 'base' ? BASE_FIELDS.length : REF2VA_FIELDS.length
    checks.push({
      id: 'prompt-structure',
      label: structure === 'base' ? 'Base fields' : 'Ref2VA fields',
      status: present === expected ? 'noted' : 'consider',
      detail: `${present}/${expected} canonical fields found.`,
    })
  }

  if (knownFields.length > 0) {
    const expectedOrder = structure === 'ref2va' ? REF2VA_FIELDS : BASE_FIELDS
    const expectedPosition = new Map(expectedOrder.map((field, index) => [field, index]))
    const relevant = knownFields.filter(field => expectedPosition.has(field))
    const duplicateCount = knownFields.length - new Set(knownFields).size
    let outOfOrderCount = 0
    let lastPosition = -1
    for (const field of relevant) {
      const position = expectedPosition.get(field) ?? -1
      if (position < lastPosition) outOfOrderCount += 1
      lastPosition = Math.max(lastPosition, position)
    }
    checks.push({
      id: 'field-order',
      label: 'Top-level field order',
      status: duplicateCount || outOfOrderCount || structure === 'mixed' ? 'consider' : 'noted',
      detail: `${duplicateCount} duplicate fields · ${outOfOrderCount} out-of-order fields.`,
    })
    const emptyValueCount = knownEntries.filter(entry => !entry.hasPayload).length
    checks.push({
      id: 'field-values',
      label: 'Canonical field values',
      status: emptyValueCount ? 'consider' : 'noted',
      detail: `${emptyValueCount}/${knownEntries.length} canonical field entries have no payload before the next top-level field.`,
    })
  }

  if (unexpectedFieldCount > 0) {
    checks.push({
      id: 'unexpected-fields',
      label: 'Other top-level fields',
      status: 'consider',
      detail: `${unexpectedFieldCount} unrecognized structured top-level labels found.`,
    })
  }

  const semanticReferenceCount = totalCount
  const effectiveRef2va = input.adaptiveConditioning
    ? semanticReferenceCount > 0
    : input.modelType === 'minimax_h3_ref2va'
  const ref2vaBaseMismatch = effectiveRef2va && structure === 'base'
  const fl2vaRef2vaMismatch = !effectiveRef2va && structure === 'ref2va'
  checks.push({
    id: 'model-family',
    label: 'Model and field family',
    status: ref2vaBaseMismatch || fl2vaRef2vaMismatch ? 'consider' : 'info',
    detail: ref2vaBaseMismatch
      ? 'Effective Ref2VA routing and structured Base fields use different prompt families.'
      : fl2vaRef2vaMismatch
        ? 'Effective FL2VA routing and structured Ref2VA fields use different prompt families.'
        : 'No static model-family mismatch is indicated by the current structure and routing inputs.',
  })

  checks.push({
    id: 'timeline-markers',
    label: 'Timeline markers',
    status: timeline.markerCount ? 'noted' : 'info',
    detail: timeline.markerCount
      ? `${timeline.markerCount} marker${timeline.markerCount === 1 ? '' : 's'} found (${timeline.rangeCount} ranges · ${timeline.pointCount} points).${timeline.truncated ? ' Review bounded at the scan limit.' : ''}`
      : `No timeline markers found.${timeline.truncated ? ' Review bounded at the scan limit.' : ' Plain prose is valid.'}`,
  })

  const duration = Number.isFinite(input.durationSeconds) && input.durationSeconds >= 0
    ? input.durationSeconds
    : 0
  const coverageNeedsAttention = timeline.malformedReversedCount > 0
    || timeline.gapCount > 0
    || timeline.overlapCount > 0
    || (timeline.endDeltaSeconds != null && Math.abs(timeline.endDeltaSeconds) > 0.01)
    || (timeline.coverageStartSeconds != null && timeline.coverageStartSeconds > 0.01)
  checks.push({
    id: 'timeline-coverage',
    label: 'Range coverage',
    status: timeline.rangeCount === 0 ? 'info' : coverageNeedsAttention ? 'consider' : 'noted',
    detail: timeline.rangeCount === 0
      ? `${timeline.pointCount} point cues found; point cues do not establish coverage for the selected ${duration}-second duration. ${timeline.malformedReversedCount} reversed ranges ignored.`
      : `Starts at ${timeline.coverageStartSeconds ?? 0}s · ${timeline.gapCount} gaps · ${timeline.overlapCount} overlaps · ends at ${timeline.coverageEndSeconds ?? 0}s versus selected ${duration}s (delta ${timeline.endDeltaSeconds ?? 0}s) · ${timeline.malformedReversedCount} reversed ranges ignored.`,
  })

  checks.push({
    id: 'reference-counts',
    label: 'Attached references',
    status: totalCount ? 'noted' : 'info',
    detail: `${imageCount} pictures · ${videoCount} videos · ${audioCount} audio clips · ${totalCount} total.`,
  })

  if (totalCount > 0) {
    checks.push({
      id: 'reference-ordinals',
      label: 'Reference ordinals',
      status: mentionedOrdinalCount === ordinals.length && unexpectedOrdinalCount === 0 ? 'noted' : 'consider',
      detail: `${mentionedOrdinalCount}/${ordinals.length} attached-media ordinals named exactly · ${unexpectedOrdinalCount} outside the attached range.`,
    })
  }

  if (input.hasStartAnchor) {
    checks.push({
      id: 'start-conditioning',
      label: 'Start conditioning',
      status: 'info',
      detail: 'The attached start frame owns the opening state.',
    })
  }

  if (input.hasEndAnchor) {
    checks.push({
      id: 'end-conditioning',
      label: 'End conditioning',
      status: 'info',
      detail: 'The attached end frame owns the final state.',
    })
  }

  return {
    structure,
    timeline,
    media: {
      imageCount,
      videoCount,
      audioCount,
      totalCount,
      expectedOrdinalCount: ordinals.length,
      mentionedOrdinalCount,
      unexpectedOrdinalCount,
    },
    checks,
  }
}
