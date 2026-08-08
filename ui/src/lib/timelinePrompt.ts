import type { ModelOptions } from '../types'

const CLOCK = String.raw`(?:(?:\d{1,2}:){1,2}\d{1,2}(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:sec(?:ond)?s?|s)?`

const RANGE_LINE = new RegExp(
  String.raw`^\s*(?:[-*]\s*)?[\[(]?\s*(${CLOCK})\s*(?:-|–|—|\bto\b)\s*(${CLOCK})\s*[\])]?\s*:?[ \t]*(\S.*)$`,
  'i',
)
const POINT_LINE = new RegExp(
  String.raw`^\s*(?:[-*]\s*)?\(?\s*at\s+(${CLOCK})\s*(?:[:,]|[-–—])?[ \t]*(\S.*)$`,
  'i',
)
const BARE_POINT_LINE = new RegExp(
  String.raw`^\s*(?:[-*]\s*)?[\[(]?\s*(${CLOCK})\s*[\])]?\s*(?:[:,]|[-–—])?[ \t]+(\S.*)$`,
  'i',
)
const SHOT_PREFIX_LINE = /^\s*(\[\s*(?:shot|scene)\s+\d+(?:\s*[^\]|]*)?\])\s*(.*)$/i
const SHOT_WITH_TIME_LINE = new RegExp(
  String.raw`^\s*\[\s*(?:shot|scene)\s+\d+(?:\s*[^\]|]*)?\s*(?:\||@|,|[-–—])\s*(${CLOCK})\s*\]\s*:?[ \t]*(\S.*)$`,
  'i',
)

function timelineLineParts(line: string): { content: string; shotLabel: boolean; shotTime: number | null } {
  const shotTimeMatch = SHOT_WITH_TIME_LINE.exec(line)
  if (shotTimeMatch) {
    return { content: '', shotLabel: true, shotTime: timelineSeconds(shotTimeMatch[1]) }
  }
  const shotPrefix = SHOT_PREFIX_LINE.exec(line)
  return {
    content: shotPrefix ? shotPrefix[2].trim() : line,
    shotLabel: !!shotPrefix,
    shotTime: null,
  }
}

function barePointSeconds(content: string, shotLabel: boolean): number | null {
  const point = BARE_POINT_LINE.exec(content)
  if (!point) return null
  const markerLikeTime = shotLabel
    || content.startsWith('[')
    || content.startsWith('(')
    || point[1].includes(':')
    || /(?:sec(?:ond)?s?|s)\s*$/i.test(point[1])
  return markerLikeTime ? timelineSeconds(point[1]) : null
}

function timelineSeconds(raw: string): number | null {
  const token = raw.trim().replace(/\s*(?:sec(?:ond)?s?|s)\s*$/i, '')
  const parts = token.split(':').map(Number)
  if (!parts.length || parts.length > 3 || parts.some(part => !Number.isFinite(part) || part < 0)) return null
  return parts.reduce((seconds, part) => seconds * 60 + part, 0)
}

/** True when Studio should preserve a multi-line prompt as one global timeline. */
export function hasGlobalTimeline(prompt: string): boolean {
  for (const rawLine of (prompt || '').replace(/\r\n?/g, '\n').split('\n')) {
    const line = rawLine.trim()
    if (!line) continue
    const parts = timelineLineParts(line)
    if (parts.shotTime != null) return true
    const range = RANGE_LINE.exec(parts.content)
    if (range) {
      const markerLine = parts.content.replace(/^\s*[-*]\s*/, '')
      const hasTimeMarker = markerLine.startsWith('[') || markerLine.startsWith('(')
        || range[1].includes(':') || range[2].includes(':')
        || /(?:sec(?:ond)?s?|s)\s*$/i.test(range[1])
        || /(?:sec(?:ond)?s?|s)\s*$/i.test(range[2])
      const start = timelineSeconds(range[1])
      const end = timelineSeconds(range[2])
      if (hasTimeMarker && start != null && end != null && end > start) return true
    }
    const point = POINT_LINE.exec(parts.content)
    if (point && timelineSeconds(point[1]) != null) return true
    if (barePointSeconds(parts.content, parts.shotLabel) != null) return true
  }
  return false
}

/** Furthest authored timestamp, used to auto-expand Studio duration. */
export function globalTimelineEndSeconds(prompt: string): number | null {
  let latest: number | null = null
  for (const rawLine of (prompt || '').replace(/\r\n?/g, '\n').split('\n')) {
    const line = rawLine.trim()
    if (!line) continue
    const parts = timelineLineParts(line)
    if (parts.shotTime != null) latest = Math.max(latest ?? 0, parts.shotTime)
    const range = RANGE_LINE.exec(parts.content)
    if (range) {
      const markerLine = parts.content.replace(/^\s*[-*]\s*/, '')
      const hasTimeMarker = markerLine.startsWith('[') || markerLine.startsWith('(')
        || range[1].includes(':') || range[2].includes(':')
        || /(?:sec(?:ond)?s?|s)\s*$/i.test(range[1])
        || /(?:sec(?:ond)?s?|s)\s*$/i.test(range[2])
      const start = timelineSeconds(range[1])
      const end = timelineSeconds(range[2])
      if (hasTimeMarker && start != null && end != null && end > start) {
        latest = Math.max(latest ?? 0, end)
      }
      continue
    }
    const point = POINT_LINE.exec(parts.content)
    const at = point ? timelineSeconds(point[1]) : null
    const bareAt = barePointSeconds(parts.content, parts.shotLabel)
    if (at != null) latest = Math.max(latest ?? 0, at)
    if (bareAt != null) latest = Math.max(latest ?? 0, bareAt)
  }
  return latest
}

export interface SlidingWindowGeometry {
  fps: number
  totalFrames: number
  windowFrames: number
  overlapFrames: number
  discardFrames: number
  reuseFrames: number
  windowCount: number
}

export interface SlidingWindowFrameOverrides {
  /** Effective total requested frames after control-video FPS handling. */
  totalFrames?: number
  /** Effective window frames when the backend hydrates a non-nominal value. */
  windowFrames?: number
  /** Display/effective FPS; does not implicitly rescale the window. */
  fps?: number
}

/** Models that make long Studio videos as joined native-length clips. */
export function usesStudioSegments(options: ModelOptions | null | undefined): boolean {
  return !!options && (
    options.model_type === 'minimax_h3'
    || options.model_type === 'minimax_h3_ref2va'
    || options.architecture === 'minimax_h3'
    || options.architecture === 'minimax_h3_ref2va'
  )
}

/** Keep a long Studio timeline intact while native per-clip limits stay enforced. */
export function alignStudioTotalFrames(frameCount: number, options: ModelOptions): number {
  const requested = Math.max(1, Math.trunc(frameCount))
  const nativeMaximum = options.frames_maximum == null ? null : Math.trunc(options.frames_maximum)
  if (usesStudioSegments(options) && nativeMaximum != null && requested > nativeMaximum) {
    return requested
  }
  return alignTotalFrames(requested, options)
}

/** Match submit-time SCAIL/control-video total-frame hydration. */
export function controlFpsTotalFrames(
  durationSeconds: number,
  forceFps: unknown,
  videoGuide: unknown,
  guideVideoFps: number | null | undefined,
  guideVideoFrameCount?: number | null,
): number | undefined {
  if (forceFps !== 'control' || !videoGuide || !guideVideoFps || guideVideoFps <= 0) return undefined
  const requested = Math.max(5, Math.round(durationSeconds * Math.min(guideVideoFps, 30)))
  const effectiveGuideFrames = guideVideoFrameCount && guideVideoFrameCount > 0
    ? Math.round(guideVideoFrameCount * Math.min(guideVideoFps, 30) / guideVideoFps)
    : null
  return effectiveGuideFrames
    ? Math.min(requested, effectiveGuideFrames)
    : requested
}

export function alignTotalFrames(frameCount: number, options: ModelOptions): number {
  let frames = Math.trunc(frameCount)
  const minimum = Math.max(1, Math.trunc(options.frames_minimum || 1))
  const maximum = options.frames_maximum == null ? null : Math.trunc(options.frames_maximum)
  frames = Math.max(minimum, frames)
  if (maximum != null) frames = Math.min(maximum, frames)
  const modulus = Math.max(0, Math.trunc(options.frame_alignment_modulus || 0))
  if (modulus > 0) {
    const remainder = Math.trunc(options.frame_alignment_remainder ?? 1) % modulus
    const delta = ((frames - remainder) % modulus + modulus) % modulus
    const mode = options.frame_alignment_mode || 'floor'
    if (delta) {
      if (mode === 'ceil') frames += modulus - delta
      else if (mode === 'nearest') frames += delta >= modulus / 2 ? modulus - delta : -delta
      else frames -= delta
    }
    if (maximum != null && frames > maximum) frames -= modulus
    if (frames < minimum) frames += modulus
    return frames
  }
  const latent = Math.max(1, Math.trunc(options.latent_size || options.frames_steps || 4))
  let aligned = Math.floor((frames - 1) / latent) * latent + 1
  if (aligned < minimum) aligned += latent
  if (maximum != null && aligned > maximum) aligned -= latent
  return Math.max(1, aligned)
}

/** Mirror WGP's effective frame quantization and sliding-window count. */
export function effectiveSlidingWindowGeometry(
  durationSeconds: number,
  windowSeconds: number,
  requestedOverlapFrames: number,
  options: ModelOptions,
  overrides: SlidingWindowFrameOverrides = {},
): SlidingWindowGeometry {
  const fps = Math.max(0.001, overrides.fps || options.fps || 16)
  const latent = Math.max(1, Math.trunc(options.latent_size || options.frames_steps || 4))
  const segmented = usesStudioSegments(options)
  const totalFrames = alignStudioTotalFrames(
    overrides.totalFrames ?? Math.round(durationSeconds * fps), options,
  )
  const requestedWindow = Math.max(
    1, Math.trunc(overrides.windowFrames ?? Math.round(windowSeconds * (options.fps || 16))),
  )
  const windowFrames = segmented
    ? alignTotalFrames(requestedWindow, options)
    : Math.floor((requestedWindow - 1) / latent) * latent + 1
  const defaults = options.sliding_window_defaults || {}
  const defaultOverlap = Math.trunc(defaults.overlap_default || 0)
  let overlapFrames = Math.max(0, Math.trunc(requestedOverlapFrames || 0))
  if (overlapFrames !== 0 && overlapFrames !== defaultOverlap) {
    overlapFrames = Math.floor((overlapFrames - 1) / latent) * latent + 1
  }
  const discardFrames = Math.floor(Math.max(0, Math.trunc(defaults.discard_last_frames || 0)) / latent) * latent
  const reuseFrames = Math.min(Math.max(0, windowFrames - latent), overlapFrames)
  const stride = windowFrames - discardFrames - reuseFrames
  const windowCount = (options.sliding_window || segmented) && totalFrames > windowFrames && stride > 0
    ? 1 + Math.ceil((totalFrames - windowFrames + discardFrames) / stride)
    : 1
  return { fps, totalFrames, windowFrames, overlapFrames, discardFrames, reuseFrames, windowCount }
}
