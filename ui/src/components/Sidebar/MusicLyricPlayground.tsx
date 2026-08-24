import { ArrowDown, ArrowUp, Clapperboard, Plus, Trash2 } from 'lucide-react'
import { useMemo } from 'react'

import {
  MUSIC3_SECTION_TAGS,
  appendMusicSection,
  moveMusicSection,
  parseMusicSections,
  removeMusicSection,
  serializeMusicSections,
  setMusicSectionTag,
  updateMusicSection,
  type Music3SectionTag,
} from '../../lib/musicSections'

const TONES = [
  'border-violet-400/30 bg-violet-400/[0.06]',
  'border-cyan-400/30 bg-cyan-400/[0.06]',
  'border-amber-400/30 bg-amber-400/[0.06]',
  'border-emerald-400/30 bg-emerald-400/[0.06]',
]

export function MusicLyricPlayground({
  lyrics,
  onChange,
  onSendToDirector,
}: {
  lyrics: string
  onChange: (lyrics: string) => void
  onSendToDirector: () => void
}) {
  const sections = useMemo(() => parseMusicSections(lyrics), [lyrics])
  const commit = (next: ReturnType<typeof parseMusicSections>) => onChange(serializeMusicSections(next))
  const invalidCount = sections.filter(section => !section.valid).length

  return (
    <section aria-labelledby="music3-playground-title" className="space-y-2.5 rounded-xl border border-accent-blue/20 bg-bg-secondary/70 p-2.5">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 id="music3-playground-title" className="text-[11px] font-semibold text-text-primary">Music3 Lyric Playground</h3>
          <p className="text-[9px] text-text-muted">Arrange sections without changing your Music Caption.</p>
        </div>
        <span role="status" aria-live="polite" className={`rounded-full px-2 py-1 text-[8px] font-medium ${invalidCount ? 'bg-amber-400/10 text-amber-300' : 'bg-indicator-success/10 text-indicator-success'}`}>
          {invalidCount ? `${invalidCount} tag${invalidCount === 1 ? '' : 's'} to fix` : `${sections.length} sections`}
        </span>
      </div>

      {sections.length > 0 && (
        <ol aria-label="Song section order" className="grid grid-cols-[repeat(auto-fit,minmax(58px,1fr))] gap-1">
          {sections.map((section, index) => (
            <li key={`strip-${section.id}`} className={`min-w-0 rounded-md border px-1.5 py-1 text-[8px] ${TONES[index % TONES.length]}`}>
              <span className="block truncate font-semibold text-text-primary">{section.tag || 'Opening'}</span>
              <span className="text-text-muted">{section.lines.filter(Boolean).length} lines</span>
            </li>
          ))}
        </ol>
      )}

      <div className="space-y-2">
        {sections.map((section, index) => (
          <article key={section.id} className={`overflow-hidden rounded-lg border ${TONES[index % TONES.length]}`}>
            <div className="flex flex-wrap items-center gap-1.5 border-b border-border/50 px-2 py-1.5">
              <span className="flex h-6 min-w-6 items-center justify-center rounded bg-bg-primary/50 text-[9px] font-bold text-text-secondary">{index + 1}</span>
              {section.tagLine === null ? (
                <span className="flex-1 text-[10px] font-medium text-text-primary">Untagged opening</span>
              ) : (
                <select
                  aria-label={`Section ${index + 1} type`}
                  value={section.tag || ''}
                  onChange={event => commit(setMusicSectionTag(sections, index, event.target.value as Music3SectionTag))}
                  className={`mobile-control-target min-w-0 flex-1 rounded border bg-bg-tertiary px-1.5 py-1 text-[10px] focus:outline-none focus:border-accent-blue md:min-h-0 ${section.valid ? 'border-border text-text-primary' : 'border-amber-400/40 text-amber-300'}`}
                >
                  {!section.tag && <option value="" disabled>Fix: {section.tagLine}</option>}
                  {MUSIC3_SECTION_TAGS.map(tag => <option key={tag} value={tag}>{tag}</option>)}
                </select>
              )}
              <button type="button" aria-label={`Move section ${index + 1} up`} disabled={index === 0} onClick={() => commit(moveMusicSection(sections, index, -1))} className="mobile-control-target flex items-center justify-center rounded text-text-muted hover:text-text-primary disabled:opacity-30"><ArrowUp size={12} /></button>
              <button type="button" aria-label={`Move section ${index + 1} down`} disabled={index === sections.length - 1} onClick={() => commit(moveMusicSection(sections, index, 1))} className="mobile-control-target flex items-center justify-center rounded text-text-muted hover:text-text-primary disabled:opacity-30"><ArrowDown size={12} /></button>
              <button type="button" aria-label={`Remove section ${index + 1}`} onClick={() => commit(removeMusicSection(sections, index))} className="mobile-control-target flex items-center justify-center rounded text-text-muted hover:text-red-400"><Trash2 size={12} /></button>
            </div>
            <div className="p-2">
              <textarea
                aria-label={`Section ${index + 1} lyrics`}
                value={section.lines.join('\n')}
                onChange={event => commit(updateMusicSection(sections, index, { lines: event.target.value.split('\n') }))}
                placeholder={section.tag === 'Instrumental' ? 'Leave blank for an instrumental passage.' : 'Write this section…'}
                rows={4}
                className="min-h-[5rem] w-full resize-y rounded-lg border border-border bg-bg-tertiary px-3 py-2 font-mono text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue"
              />
            </div>
          </article>
        ))}
      </div>

      {sections.length === 0 && <p className="rounded-lg border border-dashed border-border p-3 text-center text-[10px] text-text-muted">Add a section to start arranging lyrics.</p>}

      <div className="grid grid-cols-[auto_1fr] gap-1.5">
        <button type="button" onClick={() => commit(appendMusicSection(sections))} className="mobile-control-target flex items-center justify-center gap-1 rounded-lg border border-border px-2 text-[10px] text-text-secondary hover:bg-bg-hover hover:text-text-primary"><Plus size={12} /> Section</button>
        <button type="button" onClick={onSendToDirector} disabled={!lyrics.trim()} className="mobile-control-target flex items-center justify-center gap-1.5 rounded-lg bg-accent-blue px-3 text-[10px] font-semibold text-white hover:bg-accent-blue-hover disabled:cursor-not-allowed disabled:opacity-40"><Clapperboard size={12} /> Send to Director</button>
      </div>
      <p className="text-[9px] leading-snug text-text-muted">Copies this editable song into Music Video Director. It does not start generation.</p>
    </section>
  )
}
