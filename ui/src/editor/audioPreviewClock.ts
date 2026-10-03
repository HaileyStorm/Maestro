import type { EditorProject } from '../api/client'

type AudioLayer = EditorProject['tracks'][number]['items'][number]

export function audioLayerGain(layer: AudioLayer, sourceTime: number) {
  const elapsed = sourceTime - layer.source_in
  if (layer.muted || elapsed < 0 || elapsed >= layer.duration) return 0
  const fadeIn = layer.fade_in ?? 0
  const fadeOut = layer.fade_out ?? 0
  return (layer.volume ?? 1) * (fadeIn > 0 ? Math.min(1, elapsed / fadeIn) : 1)
    * (fadeOut > 0 ? Math.min(1, (layer.duration - elapsed) / fadeOut) : 1)
}

export function audioPreviewTarget(layer: AudioLayer, videoSourceTime: number,
  clipSourceIn: number, clipDuration: number, clipOffset: number) {
  const local = videoSourceTime - clipSourceIn
  const elapsed = clipOffset + local - layer.start
  if (![videoSourceTime, clipSourceIn, clipDuration, clipOffset].every(Number.isFinite)
    || local < 0 || local >= clipDuration || elapsed < 0 || elapsed >= layer.duration
    || layer.muted || layer.volume === 0) return null
  const sourceTime = layer.source_in + elapsed
  return { sourceTime, gain: audioLayerGain(layer, sourceTime) }
}
