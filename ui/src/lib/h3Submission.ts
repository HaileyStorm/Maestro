const H3_STUDIO_MODELS = new Set([
  'minimax_h3',
  'minimax_h3_pinkcherry_fl2va',
  'minimax_h3_w4a8_fl2va',
  'minimax_h3_ref2va',
])

export function isH3StudioModel(modelType: unknown): boolean {
  return H3_STUDIO_MODELS.has(String(modelType || ''))
}

/**
 * Keep an H3 segment ceiling only when the user explicitly locked it.
 * Presence is the server contract: omitting the field enables profile-owned
 * segment pressure, while a supplied value is an exact manual ceiling.
 */
export function applyH3SegmentCeilingPolicy<T extends Record<string, unknown>>(
  params: T,
  locked: boolean,
): T {
  if (isH3StudioModel(params.model_type) && !locked) {
    delete params.sliding_window_size
  }
  return params
}

export function hasManualH3SegmentCeiling(
  params: Record<string, unknown>,
  longform: Record<string, unknown> | null | undefined,
): boolean {
  if (!isH3StudioModel(params.model_type)) return false
  if (longform) return longform.manual_segment_ceiling === true
  return params.sliding_window_size !== undefined
    && params.sliding_window_size !== null
    && params.sliding_window_size !== ''
}
