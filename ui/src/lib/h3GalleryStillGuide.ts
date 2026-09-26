export const H3_GALLERY_STILL_GUIDE_RESTORE_MESSAGE =
  'This output used a Gallery still as a guide. Select the source still in Gallery, then choose “Use still as guide” again.'

export function isH3GalleryStillGuideOutput(metadata: unknown): boolean {
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) return false
  const record = metadata as Record<string, unknown>
  const execution = record.h3_guide_execution
  if (execution && typeof execution === 'object' && !Array.isArray(execution)
    && (execution as Record<string, unknown>).capability === 'gallery_still_fl2va') return true

  // Sidecarless and damaged-sidecar metadata can fall back to the embedded
  // WGP params. Recognize only the exact internal marker path; never inspect
  // prompt text or infer the mode from other media settings.
  const params = record.params
  if (!params || typeof params !== 'object' || Array.isArray(params)) return false
  const customSettings = (params as Record<string, unknown>).custom_settings
  if (!customSettings || typeof customSettings !== 'object' || Array.isArray(customSettings)) return false
  return Object.prototype.hasOwnProperty.call(
    customSettings,
    '_h3_timeline_still_guide',
  )
}
