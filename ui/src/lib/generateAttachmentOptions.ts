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

export const GENERATE_ATTACHMENT_DISABLED_TILE_CLASS =
  'disabled:cursor-not-allowed disabled:opacity-40'

function positiveCount(value: number | null | undefined): boolean {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
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
  // Adaptive routing can mix segment checkpoints; the selected model still
  // owns this add-list. H3 FL2VA and Ref2VA stay mutually exclusive here.
  void input.adaptiveConditioning
  const exclusive = (
    dedicatedRef2VA
    || h3Studio
    || input.mutuallyExclusiveConditioning === true
    || fl2vaOnlyProfile
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

  const acceptsFirstLastFrame = !dedicatedRef2VA && catalogFirstLast
  const acceptsReferenceImage = exclusive
    ? dedicatedRef2VA || (catalogRefImages && !catalogFirstLast && !fl2vaOnlyProfile)
    : catalogRefImages
  const acceptsReferenceVideo = exclusive
    ? dedicatedRef2VA || (catalogRefVideo && !catalogFirstLast && !fl2vaOnlyProfile)
    : catalogRefVideo
  const acceptsReferenceAudio = exclusive
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
