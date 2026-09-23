import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, Loader2, Music2, RefreshCw, Sparkles, Square } from 'lucide-react'
import * as api from '../../api/client'
import {
  preferredYue2Checkpoint,
  resolveYue2CheckpointSelection,
  resolveYue2GenerationSettings,
  reviewedAbcForContinuation,
  sameYue2ComposeDraft,
  USE_PREFERRED_YUE2_CHECKPOINT,
  yue2CheckpointSelectionKey,
  yue2LyricDensityWarning,
} from './yue2GenerationSettings'
import type { Yue2CheckpointSelection } from './yue2GenerationSettings'

type Props = {
  workspace: string
  description: string
  style: string
  lyrics: string
  instrumental: boolean
  onStyle: (value: string) => void
  onLyrics: (value: string) => void
}

const fieldClass = 'w-full rounded-lg border border-border bg-bg-tertiary px-3 py-2 text-sm text-text-primary focus:border-accent-blue focus:outline-none'

export function Yue2Controls({ workspace, description, style, lyrics, instrumental, onStyle, onLyrics }: Props) {
  const [status, setStatus] = useState<api.Yue2Status | null>(null)
  const [tracks, setTracks] = useState<api.Yue2Track[]>([])
  const [title, setTitle] = useState('Untitled YuE2 song')
  const [language, setLanguage] = useState('English')
  const [abc, setAbc] = useState('')
  const [planFirst, setPlanFirst] = useState(true)
  const [selectedLoras, setSelectedLoras] = useState<Record<string, number>>({})
  const [checkpointSelections, setCheckpointSelections] = useState<Record<string, Yue2CheckpointSelection>>({})
  const [busy, setBusy] = useState<'compose' | 'submit' | 'continue' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [guides, setGuides] = useState<string[]>([])
  const [reviewTake, setReviewTake] = useState<string | null>(null)
  const [reviewedAbc, setReviewedAbc] = useState<string | null>(null)
  const [reviewLoading, setReviewLoading] = useState(false)
  const [reviewError, setReviewError] = useState<string | null>(null)
  const workspaceRef = useRef(workspace)
  const refreshSequence = useRef(0)
  const composeSequence = useRef(0)
  const planSequence = useRef(0)
  const abcRef = useRef(abc)
  const composeDraftRef = useRef({ workspace, description, language, instrumental, style, lyrics, abc })
  workspaceRef.current = workspace
  abcRef.current = abc
  composeDraftRef.current = { workspace, description, language, instrumental, style, lyrics, abc }

  const refresh = useCallback(async () => {
    if (!workspace) return
    const requestWorkspace = workspace
    const sequence = ++refreshSequence.current
    try {
      const [nextStatus, library] = await Promise.all([
        api.fetchYue2Status(requestWorkspace),
        api.fetchYue2Library(requestWorkspace),
      ])
      if (workspaceRef.current !== requestWorkspace || refreshSequence.current !== sequence) return
      setStatus(nextStatus)
      setTracks(library.tracks.filter(track => track.project === requestWorkspace))
      setError(null)
    } catch (cause) {
      if (workspaceRef.current !== requestWorkspace || refreshSequence.current !== sequence) return
      setError(cause instanceof Error ? cause.message : 'YuE2 status is unavailable')
    }
  }, [workspace])

  useEffect(() => {
    refreshSequence.current += 1
    composeSequence.current += 1
    planSequence.current += 1
    setStatus(null)
    setTracks([])
    setTitle('Untitled YuE2 song')
    setAbc('')
    setBusy(null)
    setError(null)
    setGuides([])
    setReviewTake(null)
    setReviewedAbc(null)
    setReviewLoading(false)
    setReviewError(null)
  }, [workspace])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (!tracks.some(track => ['queued', 'running'].includes(track.status))) return
    const timer = window.setInterval(() => { void refresh() }, 2000)
    return () => window.clearInterval(timer)
  }, [refresh, tracks])

  const loadPlan = useCallback(async (take: api.Yue2Track) => {
    const requestWorkspace = workspace
    const requestAbc = abcRef.current
    const sequence = ++planSequence.current
    setReviewTake(take.id)
    setReviewedAbc(null)
    setReviewLoading(true)
    setReviewError(null)
    try {
      const plan = await api.fetchYue2Plan(take.id, requestWorkspace)
      if (workspaceRef.current !== requestWorkspace || planSequence.current !== sequence) return
      if (!plan.reviewable) throw new Error('This YuE2 score is no longer available for review. Refresh its status.')
      if (abcRef.current !== requestAbc) throw new Error('The ABC score changed while its saved plan was loading. Retry score review.')
      setAbc(plan.abc)
      setReviewedAbc(plan.abc)
    } catch (cause) {
      if (workspaceRef.current !== requestWorkspace || planSequence.current !== sequence) return
      setReviewError(cause instanceof Error ? cause.message : 'Score review is unavailable')
    } finally {
      if (workspaceRef.current === requestWorkspace && planSequence.current === sequence) setReviewLoading(false)
    }
  }, [workspace])

  useEffect(() => {
    const waiting = tracks.find(track => track.project === workspace && track.status === 'needs-review')
    if (!waiting || reviewTake === waiting.id) return
    void loadPlan(waiting)
  }, [loadPlan, reviewTake, tracks, workspace])

  const projectTracks = useMemo(() => tracks.filter(track => track.project === workspace), [tracks, workspace])
  const activeTrack = useMemo(
    () => projectTracks.find(track => ['needs-review', 'running', 'queued'].includes(track.status)) || projectTracks[0],
    [projectTracks],
  )
  const selectedGroups = useMemo(
    () => (status?.loras || []).filter(group => selectedLoras[group.id] !== undefined),
    [selectedLoras, status?.loras],
  )
  const resolvedGeneration = useMemo(() => {
    try {
      return { settings: resolveYue2GenerationSettings(selectedGroups), error: null }
    } catch (cause) {
      return {
        settings: null,
        error: cause instanceof Error ? cause.message : 'The selected LoRA defaults are incompatible.',
      }
    }
  }, [selectedGroups])
  const resolvedLoraCheckpoints = useMemo(
    () => selectedGroups.map(group => ({
      group,
      strength: selectedLoras[group.id],
      ...resolveYue2CheckpointSelection(group, checkpointSelections[group.id]),
    })),
    [checkpointSelections, selectedGroups, selectedLoras],
  )
  const checkpointError = resolvedLoraCheckpoints.find(result => result.error)?.error || null
  const lyricDensityWarning = useMemo(
    () => yue2LyricDensityWarning(lyrics, abc, language, instrumental),
    [lyrics, abc, language, instrumental],
  )

  const compose = async () => {
    if (!description.trim() || busy) return
    const requestWorkspace = workspace
    const requestDraft = { ...composeDraftRef.current }
    const sequence = ++composeSequence.current
    setBusy('compose'); setError(null)
    try {
      const result = await api.composeYue2({ workspace: requestWorkspace, description: description.trim(), language, instrumental })
      if (composeSequence.current !== sequence || !sameYue2ComposeDraft(composeDraftRef.current, requestDraft)) return
      onStyle(result.style)
      onLyrics(instrumental ? '[Instrumental]' : result.lyrics)
      setAbc(result.abc)
      setGuides(result.guides)
    } catch (cause) {
      if (workspaceRef.current !== requestWorkspace || composeSequence.current !== sequence) return
      setError(cause instanceof Error ? cause.message : 'YuE2 composition drafting failed')
    } finally {
      if (workspaceRef.current === requestWorkspace && composeSequence.current === sequence) setBusy(null)
    }
  }

  const submit = async () => {
    if (!style.trim() || !lyrics.trim() || busy) return
    if (!resolvedGeneration.settings || checkpointError) {
      setError(resolvedGeneration.error || checkpointError)
      return
    }
    setBusy('submit'); setError(null)
    const requestWorkspace = workspace
    const loras = resolvedLoraCheckpoints.flatMap(({ checkpoint, strength }) => (
      strength && checkpoint ? [{ id: checkpoint.id, sha256: checkpoint.sha256, strength }] : []
    ))
    const generation = resolvedGeneration.settings
    try {
      await api.submitYue2({
        workspace: requestWorkspace,
        requestId: `maestro-${crypto.randomUUID()}`,
        form: {
          description, title, lyrics, style, count: 1, project: requestWorkspace,
          cot: generation.cot,
          abc: generation.cot === 'off' ? '' : abc,
          planFirst: generation.cot === 'off' ? false : planFirst,
          cfg_scale: String(generation.cfgScale),
          ode_steps: generation.odeSteps,
          semantic: {
            temperature: 0.88, top_p: 0.96, top_k: 115,
            repetition_penalty: 1.2, penalty_window: 50,
            min_tokens: 200, max_tokens: generation.maxTokens,
          },
          score: {
            temperature: 0.8, top_p: 0.925, top_k: 40,
            repetition_penalty: 1.005, penalty_window: 100,
            min_tokens: 32, max_tokens: 4096,
          },
          loras,
        },
      })
      await refresh()
    } catch (cause) {
      if (workspaceRef.current !== requestWorkspace) return
      setError(cause instanceof Error ? cause.message : 'YuE2 generation could not be queued')
    } finally {
      if (workspaceRef.current === requestWorkspace) setBusy(null)
    }
  }

  const continuePlan = async () => {
    if (!activeTrack || busy || reviewedAbc === null) return
    const requestWorkspace = workspace
    const requestTake = activeTrack.id
    const editedAbc = reviewedAbcForContinuation(reviewedAbc, abc)
    setBusy('continue'); setError(null)
    try {
      await api.continueYue2(requestTake, requestWorkspace, editedAbc)
      if (workspaceRef.current !== requestWorkspace) return
      // Keep the consumed take marked until the refreshed library replaces its old review row.
      setReviewTake(requestTake)
      setReviewedAbc(null)
      setReviewError(null)
      await refresh()
    } catch (cause) {
      if (workspaceRef.current !== requestWorkspace) return
      setError(cause instanceof Error ? cause.message : 'YuE2 score could not be continued')
    } finally {
      if (workspaceRef.current === requestWorkspace) setBusy(null)
    }
  }

  return (
    <section aria-label="YuE2 composer" className="space-y-3 rounded-xl border border-accent-blue/25 bg-bg-secondary/70 p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="flex items-center gap-1.5 text-xs font-semibold text-text-primary"><Music2 size={13} /> YuE2 Composer</h3>
          <p className="mt-0.5 text-[9px] text-text-muted">
            {status?.available ? `${status.model} · ${status.sampleRate?.toLocaleString()} Hz · ${status.queue}` : status?.message || 'Checking Sound/Vision…'}
          </p>
        </div>
        <button type="button" aria-label="Refresh YuE2 status" onClick={() => void refresh()} className="mobile-control-target text-text-muted hover:text-text-primary"><RefreshCw size={13} /></button>
      </div>

      {status?.license && <p className="rounded bg-amber-500/10 px-2 py-1 text-[9px] text-amber-200">YuE2 weights: {status.license} (non-commercial).</p>}

      <div className="grid grid-cols-2 gap-2">
        <label className="text-[9px] uppercase tracking-wider text-text-muted">Title
          <input value={title} onChange={event => setTitle(event.target.value)} className={`${fieldClass} mt-1 text-xs`} />
        </label>
        <label className="text-[9px] uppercase tracking-wider text-text-muted">Lyric language
          <input value={language} onChange={event => setLanguage(event.target.value)} className={`${fieldClass} mt-1 text-xs`} />
        </label>
      </div>

      <button type="button" onClick={() => void compose()} disabled={!status?.available || !description.trim() || !!busy} className="mobile-control-target flex w-full items-center justify-center gap-1.5 rounded-lg border border-accent-blue/30 bg-accent-blue/10 px-3 text-[10px] font-semibold text-accent-blue hover:bg-accent-blue/20 disabled:opacity-40">
        {busy === 'compose' ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
        Build lyrics + score from local guides
      </button>
      <p className="text-[9px] leading-snug text-text-muted">Uses relevant local music guides to draft the lyrics and score. If English lyrics clearly exceed the Vocal notes, it makes one local revision. Review both before rendering.</p>
      {guides.length > 0 && <p className="text-[9px] text-text-muted">Guides: {guides.join(', ')}</p>}

      <label className="block text-[9px] uppercase tracking-wider text-text-muted">ABC score
        <textarea value={abc} onChange={event => setAbc(event.target.value)} disabled={reviewLoading} placeholder={'X:1\nM:4/4\nV: Vocal\n…\nV: Ins\n…'} className={`${fieldClass} mt-1 min-h-[8rem] resize-y font-mono text-[10px] disabled:cursor-wait disabled:opacity-60`} />
      </label>
      {lyricDensityWarning && <p role="status" className="rounded bg-amber-500/10 px-2 py-1 text-[10px] text-amber-200">{lyricDensityWarning}</p>}

      {(status?.loras?.length || 0) > 0 && (
        <div className="space-y-1.5">
          <p className="text-[9px] uppercase tracking-wider text-text-muted">Native LoRAs</p>
          {status!.loras.map(group => {
            const selected = selectedLoras[group.id] !== undefined
            const checkpoints = Array.isArray(group.checkpoints) ? group.checkpoints : []
            const preferredCheckpoint = preferredYue2Checkpoint(group)
            const savedCheckpoint = checkpointSelections[group.id]
            const selectedCheckpointValue = savedCheckpoint
              ? yue2CheckpointSelectionKey(savedCheckpoint)
              : USE_PREFERRED_YUE2_CHECKPOINT
            return (
              <div key={group.id} className="space-y-1 rounded-lg border border-border px-2 py-1.5">
                <div className="flex items-center gap-2">
                  <label className="flex min-w-0 flex-1 items-center gap-2 text-[10px] text-text-secondary">
                    <input type="checkbox" checked={selected} onChange={event => setSelectedLoras(current => {
                      const next = { ...current }
                      if (event.target.checked && Object.keys(next).length < 4) next[group.id] = 1
                      else delete next[group.id]
                      return next
                    })} className="accent-accent-blue" />
                    <span className="truncate">{group.name} · {group.kind}</span>
                  </label>
                  {selected && <input aria-label={`${group.name} strength`} type="number" min="0.1" max="2" step="0.05" value={selectedLoras[group.id]} onChange={event => setSelectedLoras(current => ({ ...current, [group.id]: Number(event.target.value) }))} className="w-16 rounded border border-border bg-bg-tertiary px-1 py-0.5 text-[10px] text-text-primary" />}
                </div>
                {selected && (
                  <label className="block text-[9px] text-text-muted">Checkpoint
                    <select
                      aria-label={`${group.name} checkpoint`}
                      value={selectedCheckpointValue}
                      onChange={event => setCheckpointSelections(current => {
                        const next = { ...current }
                        if (event.target.value === USE_PREFERRED_YUE2_CHECKPOINT) {
                          delete next[group.id]
                        } else {
                          const checkpoint = checkpoints.find(candidate => yue2CheckpointSelectionKey(candidate) === event.target.value)
                          if (!checkpoint) return current
                          next[group.id] = { id: checkpoint.id, sha256: checkpoint.sha256 }
                        }
                        return next
                      })}
                      className="mt-0.5 w-full rounded border border-border bg-bg-tertiary px-1.5 py-1 text-[9px] text-text-primary"
                    >
                      <option value={USE_PREFERRED_YUE2_CHECKPOINT}>
                        {preferredCheckpoint
                          ? `Preferred · ${preferredCheckpoint.label || preferredCheckpoint.filename} · ${preferredCheckpoint.id} · SHA-256 ${preferredCheckpoint.sha256}`
                          : 'Preferred checkpoint unavailable'}
                      </option>
                      {savedCheckpoint && !checkpoints.some(candidate => candidate.id === savedCheckpoint.id && candidate.sha256 === savedCheckpoint.sha256) && (
                        <option value={yue2CheckpointSelectionKey(savedCheckpoint)}>
                          Unavailable · {savedCheckpoint.id} · SHA-256 {savedCheckpoint.sha256}
                        </option>
                      )}
                      {checkpoints.map(checkpoint => {
                        const isPreferred = preferredCheckpoint?.id === checkpoint.id && preferredCheckpoint.sha256 === checkpoint.sha256
                        const step = checkpoint.step == null ? '' : ` · step ${checkpoint.step}`
                        return (
                          <option key={yue2CheckpointSelectionKey(checkpoint)} value={yue2CheckpointSelectionKey(checkpoint)}>
                            {checkpoint.label || checkpoint.filename}{step} · {checkpoint.id} · SHA-256 {checkpoint.sha256}{isPreferred ? ' · preferred' : ''}
                          </option>
                        )
                      })}
                    </select>
                  </label>
                )}
              </div>
            )
          })}
        </div>
      )}

      {resolvedGeneration.settings && selectedGroups.length > 0 && (
        <p className="rounded bg-bg-tertiary px-2 py-1 text-[9px] text-text-muted">
          LoRA settings · {resolvedGeneration.settings.cot === 'off' ? 'no ABC planning' : `${resolvedGeneration.settings.cot} ABC`}
          {' · '}ODE {resolvedGeneration.settings.odeSteps} · CFG {resolvedGeneration.settings.cfgScale}
        </p>
      )}
      {resolvedGeneration.error && <p className="text-[10px] text-red-400">{resolvedGeneration.error}</p>}
      {checkpointError && <p className="text-[10px] text-red-400">{checkpointError}</p>}

      <label className="flex items-center gap-2 text-[10px] text-text-secondary">
        <input type="checkbox" checked={planFirst && resolvedGeneration.settings?.cot !== 'off'} disabled={resolvedGeneration.settings?.cot === 'off'} onChange={event => setPlanFirst(event.target.checked)} className="accent-accent-blue" />
        Pause for ABC review before rendering
      </label>

      {activeTrack?.status === 'needs-review' ? (
        reviewError ? (
          <button type="button" onClick={() => void loadPlan(activeTrack)} disabled={reviewLoading} className="mobile-control-target flex w-full items-center justify-center gap-1.5 rounded-lg border border-red-400/30 bg-red-400/10 px-3 text-[10px] font-semibold text-red-300 hover:bg-red-400/20 disabled:opacity-40">
            {reviewLoading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} Retry score review
          </button>
        ) : (
          <button type="button" onClick={() => void continuePlan()} disabled={reviewLoading || reviewedAbc === null || !abc.trim() || !!busy} className="mobile-control-target flex w-full items-center justify-center gap-1.5 rounded-lg bg-cta px-3 text-[10px] font-semibold text-cta-foreground hover:ring-2 hover:ring-accent-blue/40 disabled:opacity-40">
            {reviewLoading || busy === 'continue' ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />} {reviewLoading ? 'Loading score review…' : 'Continue with reviewed score'}
          </button>
        )
      ) : (
        <button type="button" onClick={() => void submit()} disabled={!status?.available || !style.trim() || !lyrics.trim() || !!busy || !resolvedGeneration.settings || !!checkpointError || ['queued', 'running'].includes(activeTrack?.status || '')} className="mobile-control-target flex w-full items-center justify-center gap-1.5 rounded-lg bg-cta px-3 text-[10px] font-semibold text-cta-foreground hover:ring-2 hover:ring-accent-blue/40 disabled:opacity-40">
          {busy === 'submit' ? <Loader2 size={12} className="animate-spin" /> : <Music2 size={12} />} Generate with YuE2
        </button>
      )}

      <section aria-label="YuE2 project library" className="space-y-1.5">
        <div className="flex items-center justify-between text-[10px]">
          <h4 className="font-semibold text-text-primary">My Music</h4>
          <span className="text-text-muted">{projectTracks.length} {projectTracks.length === 1 ? 'take' : 'takes'} in this project</span>
        </div>
        {projectTracks.length === 0 && <p className="text-[10px] text-text-muted">YuE2 takes will appear here.</p>}
        <div className="max-h-96 space-y-1.5 overflow-y-auto">
          {projectTracks.map(track => (
            <article key={track.id} aria-label={`${track.title} · ${track.status}`} className="space-y-1.5 rounded-lg border border-border bg-bg-tertiary/60 p-2 text-[10px]">
              <div className="flex items-start justify-between gap-2">
                <span className="min-w-0 truncate font-medium text-text-primary" title={track.title}>{track.title}</span>
                <span className="shrink-0 text-text-muted">{track.status} · {track.stage}</span>
              </div>
              {track.duration > 0 && <p className="text-text-muted">{Math.round(track.duration)}s audio</p>}
              {track.error && <p className="text-red-400">{track.error}</p>}
              {(track.truncated?.abc || track.truncated?.semantic) && <p className="text-amber-300">This take reached a generation limit. Check the ending before reusing it.</p>}
              {track.warnings?.map(warning => <p key={warning} className="text-amber-300">{warning}</p>)}
              {track.status === 'succeeded' && (
                <div className="space-y-1">
                  <audio controls preload="none" className="w-full" src={api.yue2AudioUrl(track.id, workspace)} />
                  <a href={api.yue2AudioUrl(track.id, workspace, 'wav')} download={`${track.id}.wav`} className="inline-block text-accent-blue hover:underline">Download WAV</a>
                </div>
              )}
              {['queued', 'running'].includes(track.status) && <button type="button" onClick={() => void api.cancelYue2(track.id, workspace).then(refresh).catch(cause => setError(cause instanceof Error ? cause.message : 'YuE2 cancellation failed'))} className="flex items-center gap-1 text-red-300 hover:text-red-200"><Square size={10} /> Cancel</button>}
            </article>
          ))}
        </div>
      </section>
      {reviewError && <p className="text-[10px] text-red-400">{reviewError}</p>}
      {error && <p className="text-[10px] text-red-400">{error}</p>}
    </section>
  )
}
