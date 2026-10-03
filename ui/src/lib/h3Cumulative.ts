import type { GenerateParams } from '../types'

export const H3_CUMULATIVE_FIRST_WINDOWS = Array.from({ length: 20 }, (_, i) => 22 + i * 17)

export function h3CumulativeSelectionError(
  params: Partial<GenerateParams>, available: boolean | undefined, mode: string,
  extras: { references?: boolean; enhancement?: boolean; postprocessing?: boolean } = {},
): string | null {
  if (params.h3_cumulative_append !== true) return null
  if (available !== true) return 'Cumulative timeline is unavailable on this host. Turn it off to use ordinary generation.'
  if (mode !== 'video' || params.model_type !== 'minimax_h3' || ![undefined, 0].includes(params.image_mode)) {
    return 'Cumulative timeline requires Base H3 in Video mode, without Frames or Extend.'
  }
  if (extras.enhancement) return 'Turn off Enhance before Generate for cumulative timeline.'
  if (extras.references || [
    'image_start', 'image_end', 'image_refs', 'video_source', 'video_guide', 'video_guide2', 'video_guide3',
    'audio_source', 'audio_guide', 'audio_guide2', 'audio_guide3', 'audio_guide4', 'audio_guide5', 'audio_guide6',
  ].some(key => {
    const value = (params as Record<string, unknown>)[key]
    return Array.isArray(value) ? value.length > 0 : !!value
  })) return 'Remove reference media for cumulative timeline, or turn this mode off.'
  const custom = params.custom_settings || {}
  if (['h3_turbo_profile', 'h3_spectrum_profile', 'h3_lightx2v_profile'].some(key => key in custom)) {
    return 'Turn off Turbo, Spectrum and LightX2V for cumulative timeline.'
  }
  if (params.activated_loras?.length || params.h3_fl2va_loras?.length || params.h3_ref2va_loras?.length) {
    return 'Remove LoRAs for cumulative timeline, or turn this mode off.'
  }
  if (extras.postprocessing || params.h3_native_boundary_conditioning || params.tea_cache || params.delivery_resolution) {
    return 'Turn off clip continuity, caching and post-processing for cumulative timeline.'
  }
  if (!H3_CUMULATIVE_FIRST_WINDOWS.includes(Number(params.sliding_window_size))) {
    return 'Choose a valid first-window length for cumulative timeline.'
  }
  return null
}
