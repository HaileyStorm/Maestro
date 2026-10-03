export type H3DirectionField = 'screen direction' | 'facing'
const CLIP_BOUNDARY = '\n---CLIP_BOUNDARY---\n'

function fieldPattern(field: H3DirectionField): RegExp {
  return field === 'screen direction'
    ? /^screen\s+direction[ \t]*:[ \t]*(.*)$/i
    : /^facing[ \t]*:[ \t]*(.*)$/i
}

/** Read only explicitly authored full-line fields; ordinary prose stays prose. */
export function readH3DirectionField(prompt: string, field: H3DirectionField): string {
  const pattern = fieldPattern(field)
  let value = ''
  for (const line of prompt.split(/\r?\n/)) {
    const match = pattern.exec(line)
    if (match) value = match[1]
  }
  return value
}

/** Edit the selected authored field without rewriting the rest of the prompt. */
export function writeH3DirectionField(prompt: string, field: H3DirectionField, input: string): string {
  const entered = input.replace(/[\r\n]+/g, ' ').trimStart()
  const value = entered.trim() ? entered : ''
  const pattern = fieldPattern(field)
  const parts = prompt.match(/[^\n]*\n|[^\n]+$/g) || []
  const matching = parts.map((part, index) => (
    pattern.test(part.replace(/\r?\n$/, '')) ? index : -1
  )).filter(index => index >= 0)
  const last = matching.at(-1)
  if (last === undefined) {
    if (!value) return prompt
    const newline = prompt.includes('\r\n') ? '\r\n' : '\n'
    return `${prompt}${prompt && !prompt.endsWith('\n') ? newline : ''}${field}: ${value}`
  }
  return parts.map((part, index) => {
    if (!matching.includes(index)) return part
    if (index !== last || !value) return ''
    const ending = part.endsWith('\r\n') ? '\r\n' : part.endsWith('\n') ? '\n' : ''
    return `${field}: ${value}${ending}`
  }).join('')
}

/** Keep each multiline clip intact through the existing native manifest API. */
export function multiclipPromptPayload(
  clips: readonly { prompt: string }[], singlePromptMode: boolean, fallback = '',
): { prompt: string; per_clip_prompts: string[] } {
  const shared = clips[0]?.prompt || fallback
  const prompts = clips.map(clip => singlePromptMode ? shared : clip.prompt || '')
  return { prompt: prompts.join(CLIP_BOUNDARY), per_clip_prompts: prompts }
}

/** New output metadata preserves complete clips; retain legacy newline restore. */
export function restoreMulticlipPrompts(prompt: string, perClipPrompts?: unknown): string[] {
  if (Array.isArray(perClipPrompts) && perClipPrompts.length
    && perClipPrompts.every(value => typeof value === 'string')) return [...perClipPrompts]
  return prompt.includes(CLIP_BOUNDARY)
    ? prompt.split(CLIP_BOUNDARY)
    : prompt.split('\n').map(value => value.trim()).filter(Boolean)
}
