import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, Loader2, Music2, RefreshCw, Sparkles, Square } from 'lucide-react'
import * as api from '../../api/client'
import { preferredYue2Checkpoint, resolveYue2GenerationSettings, reviewedAbcForContinuation, sameYue2ComposeDraft } from './yue2GenerationSettings'

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
    if (!resolvedGeneration.settings) {
      setError(resolvedGeneration.error)
      return
    }
    setBusy('submit'); setError(null)
    const requestWorkspace = workspace
    const loras = (status?.loras || []).flatMap(group => {
      const strength = selectedLoras[group.id]
      const checkpoint = preferredYue2Checkpoint(group)
      return strength && checkpoint ? [{ id: checkpoint.id, sha256: checkpoint.sha256, strength }] : []
    })
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
      setReviewTake(null)
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
      <p className="text-[9px] leading-snug text-text-muted">Selects a bounded set of installed music Markdown guides for the local LLM, then validates structured style, lyrics, and ABC. It does not invoke Codex skills.</p>
      {guides.length > 0 && <p className="text-[9px] text-text-muted">Guides: {guides.join(', ')}</p>}

      <label className="block text-[9px] uppercase tracking-wider text-text-muted">ABC score
        <textarea value={abc} onChange={event => setAbc(event.target.value)} disabled={reviewLoading} placeholder={'X:1\nM:4/4\nV: Vocal\n…\nV: Ins\n…'} className={`${fieldClass} mt-1 min-h-[8rem] resize-y font-mono text-[10px] disabled:cursor-wait disabled:opacity-60`} />
      </label>

      {(status?.loras?.length || 0) > 0 && (
        <div className="space-y-1.5">
          <p className="text-[9px] uppercase tracking-wider text-text-muted">Native LoRAs</p>
          {status!.loras.map(group => {
            const selected = selectedLoras[group.id] !== undefined
            return (
              <div key={group.id} className="flex items-center gap-2 rounded-lg border border-border px-2 py-1.5">
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
        <button type="button" onClick={() => void submit()} disabled={!status?.available || !style.trim() || !lyrics.trim() || !!busy || !resolvedGeneration.settings || ['queued', 'running'].includes(activeTrack?.status || '')} className="mobile-control-target flex w-full items-center justify-center gap-1.5 rounded-lg bg-cta px-3 text-[10px] font-semibold text-cta-foreground hover:ring-2 hover:ring-accent-blue/40 disabled:opacity-40">
          {busy === 'submit' ? <Loader2 size={12} className="animate-spin" /> : <Music2 size={12} />} Generate with YuE2
        </button>
      )}

      {activeTrack && (
        <div className="space-y-1.5 rounded-lg border border-border bg-bg-tertiary/60 p-2 text-[10px]">
          <div className="flex items-center justify-between gap-2"><span className="truncate text-text-primary">{activeTrack.title}</span><span className="text-text-muted">{activeTrack.status} · {activeTrack.stage}</span></div>
          {activeTrack.error && <p className="text-red-400">{activeTrack.error}</p>}
          {activeTrack.warnings?.map(warning => <p key={warning} className="text-amber-300">{warning}</p>)}
          {activeTrack.status === 'succeeded' && <audio controls preload="metadata" className="w-full" src={api.yue2AudioUrl(activeTrack.id, workspace)} />}
          {['queued', 'running'].includes(activeTrack.status) && <button type="button" onClick={() => void api.cancelYue2(activeTrack.id, workspace).then(refresh)} className="flex items-center gap-1 text-red-300 hover:text-red-200"><Square size={10} /> Cancel</button>}
        </div>
      )}
      {reviewError && <p className="text-[10px] text-red-400">{reviewError}</p>}
      {error && <p className="text-[10px] text-red-400">{error}</p>}
    </section>
  )
}
