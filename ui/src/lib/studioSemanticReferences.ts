export const STUDIO_SEMANTIC_VIDEO_KEYS = ['video_guide', 'video_guide2', 'video_guide3'] as const
export const STUDIO_SEMANTIC_AUDIO_KEYS = ['audio_guide', 'audio_guide2', 'audio_guide3'] as const

export type StudioReferenceKind = 'image' | 'video' | 'audio'

export function classifyStudioReferenceMedia(mediaType: string | undefined, fileName = ''): StudioReferenceKind {
  const type = (mediaType || '').toLowerCase()
  const name = fileName.toLowerCase()
  if (type.startsWith('video/') || /\.(mp4|webm|mkv|mov|avi)$/.test(name)) return 'video'
  if (type.startsWith('audio/') || /\.(wav|mp3|flac|ogg|m4a)$/.test(name)) return 'audio'
  return 'image'
}

export function nextSemanticSlotPaths(
  current: Array<string | undefined>,
  path: string,
  limit: number,
): string[] {
  const existing = current.filter((item): item is string => typeof item === 'string' && item.length > 0)
  if (existing.includes(path) || existing.length >= limit) return existing
  return [...existing, path]
}

export function semanticVideoPromptType(count: number): string {
  if (count <= 0) return ''
  return `V${'+'.repeat(Math.max(0, count - 1))}-`
}

export function semanticAudioPromptType(count: number): string {
  return 'ABC'.slice(0, Math.max(0, Math.min(3, count)))
}
