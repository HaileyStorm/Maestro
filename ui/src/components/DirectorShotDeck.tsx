import { ArrowRight, Camera, CheckCircle2, Clapperboard, Clock3, Music2, Sparkles } from 'lucide-react'

import type { H3ShotDeck, H3ShotDeckJson, H3ShotDeckShot } from '../lib/h3ShotDeck'

const SHOT_TONES = [
  'border-cyan-400/40 bg-cyan-400/10 text-cyan-200',
  'border-amber-400/40 bg-amber-400/10 text-amber-200',
  'border-violet-400/40 bg-violet-400/10 text-violet-200',
  'border-emerald-400/40 bg-emerald-400/10 text-emerald-200',
  'border-rose-400/40 bg-rose-400/10 text-rose-200',
]

function label(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase())
}

function scalarText(value: H3ShotDeckJson): string | null {
  if (typeof value === 'string') return value.trim() || null
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return null
}

function valueLines(value: H3ShotDeckJson, limit = 5): string[] {
  const scalar = scalarText(value)
  if (scalar) return [scalar]
  if (Array.isArray(value)) {
    return value.flatMap(item => valueLines(item, limit)).filter(Boolean).slice(0, limit)
  }
  if (!value || typeof value !== 'object') return []
  return Object.entries(value).flatMap(([key, item]) => {
    const direct = scalarText(item)
    if (direct) return [`${label(key)}: ${direct}`]
    return valueLines(item, 2).map(line => `${label(key)}: ${line}`)
  }).slice(0, limit)
}

function fieldLines(shot: H3ShotDeckShot, field: 'action' | 'camera' | 'audio' | 'handoff_out'): string[] {
  return valueLines(shot[field])
}

function time(value: number): string {
  if (Number.isInteger(value)) return `${value}s`
  return `${value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')}s`
}

function ShotDetail({ shot, tone }: { shot: H3ShotDeckShot; tone: string }) {
  const action = fieldLines(shot, 'action')
  const camera = fieldLines(shot, 'camera')
  const audio = fieldLines(shot, 'audio')
  const sceneCraft = [
    ...valueLines(shot.subjects, 3).map(line => `Subjects: ${line}`),
    ...valueLines(shot.spatial, 2).map(line => `Spatial: ${line}`),
    ...valueLines(shot.environment, 2).map(line => `Environment: ${line}`),
    ...valueLines(shot.lighting, 2).map(line => `Lighting: ${line}`),
  ]
  const handoff = [
    ...valueLines(shot.handoff_in, 3).map(line => `In: ${line}`),
    ...fieldLines(shot, 'handoff_out').map(line => `Out: ${line}`),
  ]
  return (
    <details className="group overflow-hidden rounded-xl border border-border/70 bg-bg-secondary/70 open:border-accent-blue/35">
      <summary className="mobile-control-target flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">
        <span className={`flex h-7 min-w-7 items-center justify-center rounded-md border px-1 text-[10px] font-bold ${tone}`}>
          {String(shot.index + 1).padStart(2, '0')}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[11px] font-semibold text-text-primary">{shot.scene || `Shot ${shot.index + 1}`}</span>
          <span className="flex flex-wrap items-center gap-x-2 text-[9px] text-text-muted">
            <span>{time(shot.start_sec)}–{time(shot.end_sec)}</span>
            <span>{time(shot.duration_sec)}</span>
          </span>
        </span>
        <span className="text-[9px] font-medium uppercase tracking-wide text-text-muted group-open:text-accent-blue">Details</span>
      </summary>
      <div className="grid gap-2 border-t border-border/50 p-3 sm:grid-cols-2">
        <DeckField icon={<Sparkles size={11} />} title="Action" lines={action} />
        <DeckField icon={<Camera size={11} />} title="Camera" lines={camera} />
        <DeckField icon={<Music2 size={11} />} title="Audio" lines={audio} />
        <DeckField icon={<ArrowRight size={11} />} title="Handoff" lines={handoff} />
        <div className="sm:col-span-2">
          <DeckField icon={<Clapperboard size={11} />} title="Scene craft" lines={sceneCraft} />
        </div>
        {shot.timed_cues.length > 0 && (
          <div className="sm:col-span-2">
            <DeckField icon={<Clock3 size={11} />} title="Timed cues" lines={valueLines(shot.timed_cues, 8)} />
          </div>
        )}
      </div>
    </details>
  )
}

function DeckField({ icon, title, lines }: { icon: React.ReactNode; title: string; lines: string[] }) {
  return (
    <section className="min-w-0 rounded-lg bg-bg-tertiary/60 p-2" aria-label={title}>
      <h4 className="mb-1 flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wider text-text-muted">
        {icon}{title}
      </h4>
      {lines.length > 0 ? (
        <ul className="space-y-0.5 text-[10px] leading-relaxed text-text-secondary">
          {lines.map((line, index) => <li key={`${title}-${index}`} className="break-words">{line}</li>)}
        </ul>
      ) : <p className="text-[10px] text-text-muted">Not authored</p>}
    </section>
  )
}

export function DirectorShotDeck({ deck }: { deck: H3ShotDeck }) {
  const end = deck.shots.at(-1)?.end_sec ?? 0
  return (
    <section aria-labelledby="director-shot-deck-title" className="overflow-hidden rounded-2xl border border-accent-blue/25 bg-gradient-to-b from-accent-blue/[0.08] to-bg-secondary shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-border/60 px-3 py-3">
        <div className="flex min-w-0 items-start gap-2">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-blue/15 text-accent-blue">
            <Clapperboard size={16} />
          </span>
          <div className="min-w-0">
            <h3 id="director-shot-deck-title" className="text-xs font-semibold text-text-primary">Director Shot Deck</h3>
            <p className="text-[9px] text-text-muted">Planning aid · Advisory · {deck.shots.length} shots · {time(end)}</p>
          </div>
        </div>
        <span className="rounded-full border border-accent-blue/30 bg-accent-blue/10 px-2 py-1 text-[9px] font-medium text-accent-blue">H3 plan</span>
      </div>

      <div className="space-y-3 p-3">
        <ol aria-label="Shot timeline" className="grid grid-cols-[repeat(auto-fit,minmax(78px,1fr))] gap-1.5">
          {deck.shots.map((shot, index) => (
            <li key={shot.shot_id} className={`min-w-0 rounded-lg border px-2 py-1.5 ${SHOT_TONES[index % SHOT_TONES.length]}`}>
              <span className="block text-[9px] font-bold">{String(shot.index + 1).padStart(2, '0')}</span>
              <span className="block truncate text-[9px] opacity-90">{time(shot.duration_sec)}</span>
            </li>
          ))}
        </ol>

        <div className="space-y-1.5">
          {deck.shots.map((shot, index) => (
            <ShotDetail key={shot.shot_id} shot={shot} tone={SHOT_TONES[index % SHOT_TONES.length]} />
          ))}
        </div>

        <details className="rounded-xl border border-border/60 bg-bg-secondary/60">
          <summary className="mobile-control-target flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-[10px] font-medium text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">
            <span className="flex items-center gap-1.5"><CheckCircle2 size={12} /> QC checklist</span>
            <span className="rounded-full bg-amber-400/10 px-2 py-0.5 text-[9px] text-amber-300">{deck.qc_checklist.length} pending</span>
          </summary>
          <ul className="space-y-1 border-t border-border/50 p-3">
            {deck.qc_checklist.map(item => (
              <li key={item.check} className="flex items-start justify-between gap-2 text-[10px] text-text-secondary">
                <span className="break-words">{label(item.check)}</span>
                <span className="shrink-0 rounded-full border border-amber-400/25 bg-amber-400/10 px-1.5 py-0.5 text-[8px] font-medium uppercase tracking-wide text-amber-300">Pending</span>
              </li>
            ))}
          </ul>
        </details>
      </div>
    </section>
  )
}
