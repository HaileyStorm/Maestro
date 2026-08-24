export const MUSIC3_SECTION_TAGS = [
  'Intro', 'Verse', 'Pre-Chorus', 'Chorus', 'Post-Chorus',
  'Bridge', 'Instrumental', 'Solo', 'Guitar Solo', 'Outro',
] as const

export type Music3SectionTag = typeof MUSIC3_SECTION_TAGS[number]

export interface MusicLyricSection {
  id: string
  tagLine: string | null
  tag: Music3SectionTag | null
  valid: boolean
  lines: string[]
}

const TAG_LINE = /^\s*\[(.+)]\s*$/
const CANONICAL = new Map(MUSIC3_SECTION_TAGS.map(tag => [tag.toLocaleLowerCase(), tag]))

export function parseMusicSections(lyrics: string): MusicLyricSection[] {
  const lines = String(lyrics || '').replaceAll('\r\n', '\n').replaceAll('\r', '\n').split('\n')
  if (lines.length === 1 && lines[0] === '') return []
  const sections: MusicLyricSection[] = []
  let current: MusicLyricSection | null = null
  const push = () => {
    if (!current) return
    sections.push({ ...current, id: `music-section-${sections.length + 1}` })
  }
  for (const line of lines) {
    const match = TAG_LINE.exec(line)
    if (match && !match[1].includes('[') && !match[1].includes(']')) {
      push()
      const raw = match[1].trim()
      const tag = CANONICAL.get(raw.toLocaleLowerCase()) ?? null
      current = {
        id: '',
        tagLine: line,
        tag,
        valid: tag !== null,
        lines: [],
      }
      continue
    }
    if (!current) {
      current = { id: '', tagLine: null, tag: null, valid: true, lines: [] }
    }
    current.lines.push(line)
  }
  push()
  return sections
}

export function serializeMusicSections(sections: readonly MusicLyricSection[]): string {
  return sections.flatMap(section => [
    ...(section.tagLine === null ? [] : [section.tagLine]),
    ...section.lines,
  ]).join('\n')
}

export function updateMusicSection(
  sections: readonly MusicLyricSection[],
  index: number,
  update: Partial<Pick<MusicLyricSection, 'tagLine' | 'tag' | 'valid' | 'lines'>>,
): MusicLyricSection[] {
  return sections.map((section, sectionIndex) => (
    sectionIndex === index ? { ...section, ...update } : section
  ))
}

export function setMusicSectionTag(
  sections: readonly MusicLyricSection[],
  index: number,
  tag: Music3SectionTag,
): MusicLyricSection[] {
  return updateMusicSection(sections, index, {
    tag,
    tagLine: `[${tag}]`,
    valid: true,
  })
}

export function moveMusicSection(
  sections: readonly MusicLyricSection[],
  index: number,
  delta: -1 | 1,
): MusicLyricSection[] {
  const target = index + delta
  if (index < 0 || index >= sections.length || target < 0 || target >= sections.length) return [...sections]
  const next = [...sections]
  const [section] = next.splice(index, 1)
  next.splice(target, 0, section)
  return next.map((item, sectionIndex) => ({ ...item, id: `music-section-${sectionIndex + 1}` }))
}

export function appendMusicSection(
  sections: readonly MusicLyricSection[],
  tag: Music3SectionTag = 'Verse',
): MusicLyricSection[] {
  return [
    ...sections,
    {
      id: `music-section-${sections.length + 1}`,
      tagLine: `[${tag}]`,
      tag,
      valid: true,
      lines: [],
    },
  ]
}

export function removeMusicSection(
  sections: readonly MusicLyricSection[],
  index: number,
): MusicLyricSection[] {
  return sections
    .filter((_, sectionIndex) => sectionIndex !== index)
    .map((item, sectionIndex) => ({ ...item, id: `music-section-${sectionIndex + 1}` }))
}
