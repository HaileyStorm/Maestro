/** Same media kinds as classifyStudioReferenceMedia — kept local so this helper stays import-free. */
export type GenerateAttachmentMediaKind = 'image' | 'video' | 'audio'

/**
 * Generate add-references list ordering and disable rules.
 *
 * Reuses catalog flags already exposed on ModelDef / ModelOptions:
 * supports_end_frame, supports_ref_images, supports_audio_input,
 * image_ref_choices, max_image_refs, semantic count limits,
 * minimax_h3_conditioning_mode, and director.reference_mode.
 * Does not invent a second capability matrix.
 *
 * InputsPanel (reserved) should call these helpers and render every option
 * returned here. Incompatible items stay visible, disabled, and last.
 */

export type GenerateAttachmentId =
  | 'project_reference'
  | 'first_last_frame'
  | 'reference_image'
  | 'reference_video'
  | 'reference_audio'

export const GENERATE_ATTACHMENT_LABELS: Record<GenerateAttachmentId, string> = {
  project_reference: 'Project reference',
  first_last_frame: 'First / last frame',
  reference_image: 'Reference image',
  reference_video: 'Reference video',
  reference_audio: 'Reference audio',
}

const ENABLED_KIND_ORDER: Exclude<GenerateAttachmentId, 'project_reference'>[] = [
  'first_last_frame',
  'reference_image',
  'reference_video',
  'reference_audio',
]

const FL2VA_ONLY_H3_PROFILES = new Set([
  'spectrum_experimental',
  'lightx2v_experimental',
])

export interface GenerateAttachmentCatalogInput {
  modelType?: string | null
  architecture?: string | null
  supportsEndFrame?: boolean
  supportsRefImages?: boolean
  supportsAudioInput?: boolean
  hasImageRefChoices?: boolean
  maxImageRefs?: number | null
  referenceImageMaxCount?: number | null
  referenceVideoMaxCount?: number | null
  referenceAudioMaxCount?: number | null
  conditioningMode?: string | null
  mutuallyExclusiveConditioning?: boolean
  adaptiveConditioning?: boolean
  directorReferenceMode?: string | null
  directorSupportsAudioInput?: boolean
  h3ProfileId?: string | null
}

export interface GenerateAttachmentCapabilities {
  acceptsFirstLastFrame: boolean
  acceptsReferenceImage: boolean
  acceptsReferenceVideo: boolean
  acceptsReferenceAudio: boolean
  acceptsAnyReference: boolean
}

export interface GenerateAttachmentOption {
  id: GenerateAttachmentId
  label: string
  enabled: boolean
  reason: string | null
}

export const H3_REFERENCE_LIMITS = {
  images: 9,
  videos: 3,
  audio: 3,
  mixed: 12,
} as const

export interface H3InputCompatibilityInput {
  durationSeconds: number
  framesMaximum?: number | null
  fps?: number | null
  hasFrameInputs: boolean
  imageCount: number
  videoCount: number
  audioCount: number
}

export interface H3InputCompatibility {
  nativeMaximumSeconds: number | null
  hasSemanticInputs: boolean
  hasShortMixedInputs: boolean
  remaining: {
    images: number
    videos: number
    audio: number
    mixed: number
    pairedAudio: number
  }
  canAddFrame: boolean
  canAddImage: boolean
  canAddVideo: boolean
  canAddAudio: boolean
  frameReason: string | null
  semanticReason: string | null
  audioReason: string | null
  invalidReason: string | null
}

export const GENERATE_ATTACHMENT_DISABLED_TILE_CLASS =
  'disabled:cursor-not-allowed disabled:opacity-40'

function positiveCount(value: number | null | undefined): boolean {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

function remaining(limit: number, count: number): number {
  return Math.max(0, limit - Math.max(0, count))
}

/**
 * Resolve the H3 input contract against one complete prospective state.
 * Callers can use the add affordances for one-file choices or invalidReason
 * to preflight a staged project pack atomically.
 */
export function resolveH3InputCompatibility(
  input: H3InputCompatibilityInput,
): H3InputCompatibility {
  const imageCount = Math.max(0, input.imageCount)
  const videoCount = Math.max(0, input.videoCount)
  const audioCount = Math.max(0, input.audioCount)
  const visualCount = imageCount + videoCount
  const mixedCount = visualCount + audioCount
  const nativeMaximumSeconds = (
    positiveCount(input.framesMaximum) && positiveCount(input.fps)
      ? (input.framesMaximum as number) / (input.fps as number)
      : null
  )
  const hasSemanticInputs = mixedCount > 0
  const shortOutput = (
    nativeMaximumSeconds != null
    && Number.isFinite(input.durationSeconds)
    && input.durationSeconds <= nativeMaximumSeconds
  )
  const hasShortMixedInputs = shortOutput && input.hasFrameInputs && hasSemanticInputs
  const durationLabel = nativeMaximumSeconds == null
    ? null
    : `${nativeMaximumSeconds.toFixed(2)}s`
  const semanticReason = shortOutput && input.hasFrameInputs
    ? `Remove start/end frames or request more than ${durationLabel} before adding reference media.`
    : null
  const frameReason = shortOutput && hasSemanticInputs
    ? `Remove reference media or request more than ${durationLabel} before adding frames.`
    : null
  const mixedRemaining = remaining(H3_REFERENCE_LIMITS.mixed, mixedCount)
  const pairedAudio = Math.max(0, visualCount - audioCount)
  const imageRemaining = Math.min(remaining(H3_REFERENCE_LIMITS.images, imageCount), mixedRemaining)
  const videoRemaining = Math.min(remaining(H3_REFERENCE_LIMITS.videos, videoCount), mixedRemaining)
  const audioRemaining = Math.min(
    remaining(H3_REFERENCE_LIMITS.audio, audioCount),
    mixedRemaining,
    pairedAudio,
  )
  const audioReason = semanticReason
    ?? (pairedAudio < 1 ? 'Add an image or video before adding another audio reference.' : null)
  const invalidReason = hasShortMixedInputs
    ? `Start/end frames and reference media need separate H3 segments. Request more than ${durationLabel}, or remove one input type.`
    : audioCount > visualCount
      ? 'MiniMax H3 needs at least one image or video for each audio reference.'
      : null

  return {
    nativeMaximumSeconds,
    hasSemanticInputs,
    hasShortMixedInputs,
    remaining: {
      images: imageRemaining,
      videos: videoRemaining,
      audio: audioRemaining,
      mixed: mixedRemaining,
      pairedAudio,
    },
    canAddFrame: frameReason == null,
    canAddImage: semanticReason == null && imageRemaining > 0,
    canAddVideo: semanticReason == null && videoRemaining > 0,
    canAddAudio: audioReason == null
      && audioRemaining > 0,
    frameReason,
    semanticReason,
    audioReason,
    invalidReason,
  }
}

export function isDedicatedRef2VAModel(input: GenerateAttachmentCatalogInput): boolean {
  const modelType = String(input.modelType || '')
  const architecture = String(input.architecture || '')
  return (
    modelType === 'minimax_h3_ref2va'
    || architecture === 'minimax_h3_ref2va'
    || input.conditioningMode === 'semantic_references'
  )
}

export function isFl2vaOnlyH3Profile(profileId: string | null | undefined): boolean {
  return FL2VA_ONLY_H3_PROFILES.has(String(profileId || ''))
}

export function resolveGenerateAttachmentCapabilities(
  input: GenerateAttachmentCatalogInput,
): GenerateAttachmentCapabilities {
  const dedicatedRef2VA = isDedicatedRef2VAModel(input)
  const h3Studio = (
    String(input.modelType || '').startsWith('minimax_h3')
    || String(input.architecture || '').startsWith('minimax_h3')
  )
  const fl2vaOnlyProfile = isFl2vaOnlyH3Profile(input.h3ProfileId)
  const adaptiveHybrid = (
    h3Studio
    && input.adaptiveConditioning === true
    && !fl2vaOnlyProfile
  )
  const exclusive = (
    !adaptiveHybrid
    && (
      dedicatedRef2VA
      || h3Studio
      || input.mutuallyExclusiveConditioning === true
      || fl2vaOnlyProfile
    )
  )
  const catalogRefImages = (
    input.supportsRefImages === true
    || input.hasImageRefChoices === true
    || positiveCount(input.maxImageRefs)
    || positiveCount(input.referenceImageMaxCount)
  )
  const catalogFirstLast = (
    input.supportsEndFrame === true
    || input.directorReferenceMode === 'start_frame'
    || input.directorReferenceMode === 'start_end'
    || input.conditioningMode === 'first_last_frames'
    || (h3Studio && !dedicatedRef2VA)
  )
  const catalogRefVideo = dedicatedRef2VA || positiveCount(input.referenceVideoMaxCount)
  const catalogRefAudio = (
    dedicatedRef2VA
    || input.supportsAudioInput === true
    || input.directorSupportsAudioInput === true
    || positiveCount(input.referenceAudioMaxCount)
  )

  const acceptsFirstLastFrame = adaptiveHybrid
    ? true
    : !dedicatedRef2VA && catalogFirstLast
  const acceptsReferenceImage = adaptiveHybrid
    ? true
    : exclusive
      ? dedicatedRef2VA || (catalogRefImages && !catalogFirstLast && !fl2vaOnlyProfile)
      : catalogRefImages
  const acceptsReferenceVideo = adaptiveHybrid
    ? true
    : exclusive
      ? dedicatedRef2VA || (catalogRefVideo && !catalogFirstLast && !fl2vaOnlyProfile)
      : catalogRefVideo
  const acceptsReferenceAudio = adaptiveHybrid
    ? true
    : exclusive
      ? (dedicatedRef2VA && catalogRefAudio) || (!catalogFirstLast && catalogRefAudio && !fl2vaOnlyProfile)
      : catalogRefAudio
  const acceptsAnyReference = (
    acceptsFirstLastFrame
    || acceptsReferenceImage
    || acceptsReferenceVideo
    || acceptsReferenceAudio
  )

  return {
    acceptsFirstLastFrame,
    acceptsReferenceImage,
    acceptsReferenceVideo,
    acceptsReferenceAudio,
    acceptsAnyReference,
  }
}

export function generateAttachmentDisabledReason(
  id: GenerateAttachmentId,
  capabilities: GenerateAttachmentCapabilities,
): string | null {
  if (id === 'project_reference') {
    return capabilities.acceptsAnyReference
      ? null
      : 'This model does not accept project references.'
  }
  if (id === 'first_last_frame') {
    return capabilities.acceptsFirstLastFrame
      ? null
      : 'This model uses reference media, not first/last frames.'
  }
  if (id === 'reference_image') {
    return capabilities.acceptsReferenceImage
      ? null
      : 'This model uses first/last frames, not reference images.'
  }
  if (id === 'reference_video') {
    return capabilities.acceptsReferenceVideo
      ? null
      : 'This model does not accept reference videos.'
  }
  return capabilities.acceptsReferenceAudio
    ? null
    : 'This model does not accept audio references.'
}

function optionEnabled(id: GenerateAttachmentId, capabilities: GenerateAttachmentCapabilities): boolean {
  if (id === 'project_reference') return capabilities.acceptsAnyReference
  if (id === 'first_last_frame') return capabilities.acceptsFirstLastFrame
  if (id === 'reference_image') return capabilities.acceptsReferenceImage
  if (id === 'reference_video') return capabilities.acceptsReferenceVideo
  return capabilities.acceptsReferenceAudio
}

export function orderGenerateAttachmentOptions(
  capabilities: GenerateAttachmentCapabilities,
): GenerateAttachmentOption[] {
  const make = (id: GenerateAttachmentId): GenerateAttachmentOption => {
    const enabled = optionEnabled(id, capabilities)
    return {
      id,
      label: GENERATE_ATTACHMENT_LABELS[id],
      enabled,
      reason: enabled ? null : generateAttachmentDisabledReason(id, capabilities),
    }
  }

  const enabledKinds = ENABLED_KIND_ORDER.filter(id => optionEnabled(id, capabilities)).map(make)
  const disabledKinds = ENABLED_KIND_ORDER.filter(id => !optionEnabled(id, capabilities)).map(make)
  const project = make('project_reference')

  if (project.enabled) {
    return [project, ...enabledKinds, ...disabledKinds]
  }
  return [...enabledKinds, ...disabledKinds, project]
}

export function acceptedProjectReferenceKinds(
  capabilities: GenerateAttachmentCapabilities,
): GenerateAttachmentMediaKind[] {
  const kinds: GenerateAttachmentMediaKind[] = []
  if (capabilities.acceptsFirstLastFrame || capabilities.acceptsReferenceImage) kinds.push('image')
  if (capabilities.acceptsReferenceVideo) kinds.push('video')
  if (capabilities.acceptsReferenceAudio) kinds.push('audio')
  return kinds
}

export function projectReferenceKindAccepted(
  kind: GenerateAttachmentMediaKind,
  capabilities: GenerateAttachmentCapabilities,
): boolean {
  return acceptedProjectReferenceKinds(capabilities).includes(kind)
}

export function filterProjectReferenceChoices<T extends { kind: GenerateAttachmentMediaKind }>(
  choices: readonly T[],
  capabilities: GenerateAttachmentCapabilities,
): T[] {
  return choices.filter(choice => projectReferenceKindAccepted(choice.kind, capabilities))
}
