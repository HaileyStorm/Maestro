import { useLayoutEffect, useRef, useState } from 'react'
import { Clapperboard, Music, Sparkles, Loader2 } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import * as api from '../../api/client'
import type { GenerateParams } from '../../types'
import { MusicLyricPlayground } from './MusicLyricPlayground'

const TEXTAREA_BASE =
  'w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary ' +
  'placeholder:text-text-muted focus:outline-none focus:border-accent-blue resize-none overflow-hidden'

// Textarea that grows to fit its content (no inner scrollbar — the sidebar
// already scrolls). Re-measures on every value change, so it also expands
// when the writing assistant fills it in. A min-height keeps it from collapsing when empty.
export function AutoGrowTextarea({
  value,
  onChange,
  placeholder,
  extraClass = '',
  ariaLabel,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  extraClass?: string
  ariaLabel: string
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value])
  return (
    <textarea
      ref={ref}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      aria-label={ariaLabel}
      className={`${TEXTAREA_BASE} ${extraClass}`}
    />
  )
}

function StyleField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Style / Music Caption</label>
      <AutoGrowTextarea
        value={value}
        onChange={onChange}
        placeholder="Describe it like you're briefing musicians — genre, instruments, mood, production, vocals. e.g. dreamy bedroom-pop with shimmering reverb guitars and warm analog synths, soft breathy female vocals, nostalgic and intimate, gently mid-tempo"
        extraClass="min-h-[3.5rem]"
        ariaLabel="Style and music caption"
      />
    </div>
  )
}

function LyricsField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Lyrics</label>
      <AutoGrowTextarea
        value={value}
        onChange={onChange}
        placeholder={'[Verse]\nYour lyrics here…\n[Chorus]\n…'}
        extraClass="min-h-[8rem] font-mono"
        ariaLabel="Lyrics"
      />
    </div>
  )
}

export function MusicControls() {
  const description = useStore(s => s.musicDescription)
  const setDescription = useStore(s => s.setMusicDescription)
  const instrumental = useStore(s => s.musicInstrumental)
  const setInstrumental = useStore(s => s.setMusicInstrumental)
  const params = useStore(s => s.params)
  const setParam = useStore(s => s.setParam)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const sendMusicToDirector = useStore(s => s.sendMusicToDirector)

  const style = (params.alt_prompt as string) || ''
  const lyrics = (params.prompt as string) || ''
  const modelType = String(params.model_type || '')
  const isMusic3 = modelType === 'minimax_music3'
  const [writing, setWriting] = useState(false)
  const [writeError, setWriteError] = useState<string | null>(null)

  // alt_prompt = Music Caption (style); prompt = Lyrics. Both remain the
  // shared music-model submission fields.
  const setStyle = (v: string) => setParam('alt_prompt' as keyof GenerateParams, v)
  const setLyrics = (v: string) => setParam('prompt', v)

  const toggleInstrumental = (on: boolean) => {
    setInstrumental(on)
    if (on) {
      setLyrics('[Instrumental]')
    } else if (lyrics.trim().toLowerCase() === '[instrumental]') {
      setLyrics('')
    }
  }

  const handleWriteSong = async () => {
    if (!description.trim() || writing) return
    const requestWorkspace = activeWorkspace
    const requestDescription = description.trim()
    const requestInstrumental = instrumental
    const requestModelType = modelType
    const requestStyle = style
    const requestLyrics = lyrics
    const controller = new AbortController()
    const unsubscribe = useStore.subscribe(state => {
      if (state.activeWorkspace !== requestWorkspace) controller.abort()
    })
    const requestIsCurrent = () => {
      const current = useStore.getState()
      return current.activeWorkspace === requestWorkspace
        && current.musicDescription.trim() === requestDescription
        && current.musicInstrumental === requestInstrumental
        && String(current.params.model_type || '') === requestModelType
        && String(current.params.alt_prompt || '') === requestStyle
        && String(current.params.prompt || '') === requestLyrics
    }
    setWriting(true)
    setWriteError(null)
    try {
      const r = await api.writeSong({
        workspace: requestWorkspace,
        description: requestDescription,
        instrumental: requestInstrumental,
        model_type: requestModelType || undefined,
      }, { signal: controller.signal })
      if (!requestIsCurrent()) return
      if (r.style) setStyle(r.style)
      setLyrics(requestInstrumental ? '[Instrumental]' : (r.lyrics || ''))
    } catch (e) {
      if (controller.signal.aborted || !requestIsCurrent()) return
      setWriteError(e instanceof Error ? e.message : 'Song writing failed')
    } finally {
      unsubscribe()
      setWriting(false)
    }
  }

  return (
    <div className="space-y-3">
      {/* Header + instrumental toggle */}
      <div className="flex items-center justify-between">
        <label className="text-[11px] text-text-muted uppercase tracking-wider flex items-center gap-1.5">
          <Music size={12} /> Song
        </label>
        <label className="flex items-center gap-1.5 cursor-pointer group text-[10px] text-text-secondary hover:text-text-primary transition-colors">
          <input
            type="checkbox"
            checked={instrumental}
            onChange={e => toggleInstrumental(e.target.checked)}
            className="accent-accent-blue"
          />
          Instrumental
        </label>
      </div>

      {/* Describe → ask the configured assistant for an editable draft */}
      <div className="space-y-2">
        <div>
          <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Describe your song</label>
          <AutoGrowTextarea
            value={description}
            onChange={setDescription}
            placeholder="e.g. an upbeat synthwave track about late-night city driving — nostalgic but hopeful"
            extraClass="min-h-[4.5rem]"
            ariaLabel="Describe your song"
          />
        </div>
        <button
          onClick={handleWriteSong}
          disabled={!description.trim() || writing}
          className={`w-full px-4 py-2 rounded-lg flex items-center justify-center gap-1.5 font-medium text-xs transition-all ${
            !description.trim() || writing
              ? 'bg-bg-tertiary text-text-muted cursor-not-allowed border border-border'
              : 'bg-cta shadow-accent-glow text-cta-foreground hover:ring-2 hover:ring-accent-blue/40'
          }`}
        >
          {writing ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
          {writing ? 'Writing…' : 'Write Song'}
        </button>
        {writeError && <p className="text-[10px] text-red-400 leading-snug">{writeError}</p>}
        <p className="text-[10px] text-text-muted leading-snug">
          Your configured AI writing assistant can draft the Style{instrumental ? '' : ' + Lyrics'} from your description. Review and edit the draft below, or write either field yourself, then Generate.
        </p>
      </div>

      {/* Style + Lyrics (editable, auto-sizing). Lyrics hidden when instrumental. */}
      <StyleField value={style} onChange={setStyle} />
      {isMusic3
        ? instrumental
          ? (
              <section aria-label="Instrumental Music3 handoff" className="rounded-xl border border-accent-blue/20 bg-bg-secondary/70 p-2.5">
                <p className="text-[10px] text-text-secondary">Instrumental mode uses the Music Caption and the canonical [Instrumental] control tag.</p>
                <button type="button" onClick={sendMusicToDirector} className="mobile-control-target mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg bg-accent-blue px-3 text-[10px] font-semibold text-white hover:bg-accent-blue-hover">
                  <Clapperboard size={12} /> Send to Director
                </button>
                <p className="mt-1 text-[9px] text-text-muted">Copies this setup into Music Video Director. It does not start generation.</p>
              </section>
            )
          : <MusicLyricPlayground lyrics={lyrics} onChange={setLyrics} onSendToDirector={sendMusicToDirector} />
        : !instrumental && <LyricsField value={lyrics} onChange={setLyrics} />}
    </div>
  )
}
