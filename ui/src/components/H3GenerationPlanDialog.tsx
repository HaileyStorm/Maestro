import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Check, Pause, X } from 'lucide-react'
import { useStore } from '../stores/useStore'
import type { H3SegmentBoundary, H3SegmentPlan, H3SegmentPlanItem } from '../types'
import { HOST_TERM_NOTICES } from '../lib/hostTerms'

const REQUIRE_REVIEW_KEY = 'maestro:h3-plan-require-review'
const COUNTDOWN_SECONDS = 8

function compactTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return 'calculating…'
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))}s`
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.round(seconds % 60)
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`
}

type H3Model = H3SegmentPlanItem['model_type']
type BoundaryType = H3SegmentBoundary['type']
type H3CheckpointOption = {
  model_type: H3Model
  name: string
  conditioning_mode: 'first_last_frames' | 'semantic_references'
  is_downloaded: boolean
  managed_download: boolean
  auto_download: boolean
  terms_required: boolean
  available: boolean
  unavailable_reason: string
}
type H3PlanWithCheckpointOptions = H3SegmentPlan & {
  checkpoint_options?: H3CheckpointOption[]
}

const H3_MODEL_IDS = new Set<H3Model>([
  'minimax_h3',
  'minimax_h3_pinkcherry_fl2va',
  'minimax_h3_w4a8_fl2va',
  'minimax_h3_ref2va',
])

const MODEL_LABELS: Record<H3Model, string> = {
  minimax_h3: 'FL2VA · frame anchor',
  minimax_h3_pinkcherry_fl2va: 'PinkCherry FL2VA · explicit frame anchor',
  minimax_h3_w4a8_fl2va: 'Kijai W4A8 FL2VA · experimental low-memory anchor',
  minimax_h3_ref2va: 'Ref2VA · semantic/temporal refs',
}

const BOUNDARY_LABELS: Record<BoundaryType, string> = {
  continuous: 'Continuous motion',
  precut: 'Continue into cut',
  cut: 'Hard camera/scene cut',
  transition: 'Transition boundary (conditioning)',
}

export function H3GenerationPlanDialog() {
  const plan = useStore(s => s.pendingH3Plan)
  const planEstimate = useStore(s => s.pendingH3PlanEstimate)
  const approve = useStore(s => s.approveH3Plan)
  const cancel = useStore(s => s.cancelH3Plan)
  const selectModel = useStore(s => s.selectModel)
  const h3SelectedProfile = useStore(s => s.h3SelectedProfile)
  const h3ProfileApplying = useStore(s => s.h3ProfileApplying)
  const h3Profiles = useStore(s => s.h3PerformanceProfiles)
  const pinkCompatibility = useStore(
    s => s.h3ModelProfileCompatibility.minimax_h3_pinkcherry_fl2va,
  )
  const refreshH3Compatibility = useStore(s => s.refreshH3ModelProfileCompatibility)
  const availableModels = useStore(s => s.models)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const hostTerms = useStore(s => s.hostTerms)
  const hostTermsLoading = useStore(s => s.hostTermsLoading)
  const hostTermsError = useStore(s => s.hostTermsError)
  const loadHostTerms = useStore(s => s.loadHostTerms)
  const acceptHostTerm = useStore(s => s.acceptHostTerm)
  const [models, setModels] = useState<H3Model[]>([])
  const [boundaries, setBoundaries] = useState<BoundaryType[]>([])
  const [seconds, setSeconds] = useState(COUNTDOWN_SECONDS)
  const [paused, setPaused] = useState(false)
  const ref2vaTermsAccepted = hostTerms?.minimax_h3_ref2va.accepted === true
  const [requireReview, setRequireReview] = useState(() => {
    try { return localStorage.getItem(REQUIRE_REVIEW_KEY) === 'true' } catch { return false }
  })

  useEffect(() => {
    if (activeWorkspace && !hostTerms && !hostTermsLoading) void loadHostTerms()
  }, [activeWorkspace, hostTerms, hostTermsLoading, loadHostTerms])

  useEffect(() => {
    if (!plan) return
    const timer = window.setTimeout(() => {
      setModels(plan.segments.map(segment => segment.model_type))
      setBoundaries(plan.segments.slice(1).map(segment => segment.boundary_from_previous?.type || 'continuous'))
      setSeconds(COUNTDOWN_SECONDS)
      try {
        const accepted = useStore.getState().hostTerms?.minimax_h3_ref2va.accepted === true
        setPaused(
          localStorage.getItem(REQUIRE_REVIEW_KEY) === 'true'
          || (plan.segments.some(segment => segment.model_type === 'minimax_h3_ref2va') && !accepted)
        )
      } catch {
        setPaused(false)
      }
    }, 0)
    return () => window.clearTimeout(timer)
  }, [plan])

  useEffect(() => {
    if (!plan || h3SelectedProfile === 'custom') return
    void refreshH3Compatibility('minimax_h3_pinkcherry_fl2va')
  }, [plan, h3SelectedProfile, refreshH3Compatibility])

  const switchCount = useMemo(
    () => models.slice(1).filter((model, index) => model !== models[index]).length,
    [models],
  )
  const serverOptions = (plan as H3PlanWithCheckpointOptions | null)?.checkpoint_options
  const checkpointOptions: H3CheckpointOption[] = serverOptions?.length
    ? serverOptions
    : availableModels
      .filter(model => H3_MODEL_IDS.has(model.model_type as H3Model))
      .map(model => ({
        model_type: model.model_type as H3Model,
        name: model.name,
        conditioning_mode: model.model_type === 'minimax_h3_ref2va' ? 'semantic_references' : 'first_last_frames',
        is_downloaded: model.is_downloaded === true,
        managed_download: false,
        auto_download: false,
        terms_required: model.model_type === 'minimax_h3_ref2va',
        available: model.is_downloaded === true,
        unavailable_reason: model.is_downloaded === true
          ? ''
          : 'Availability requires a current server-authored generation plan.',
      }))
  const optionByModel = new Map(checkpointOptions.map(option => [option.model_type, option]))
  const getModelBlockedReason = (index: number, model: H3Model) => {
    const option = optionByModel.get(model)
    if (!option) return 'This checkpoint is not in the server-managed H3 catalog.'
    if (!option.available) return option.unavailable_reason || 'This checkpoint is unavailable.'
    if (model === 'minimax_h3_ref2va' && plan?.segments[index]?.edge_anchor_locked) {
      return 'Ref2VA cannot honor this segment’s supplied first/final-frame anchor.'
    }
    if (!option.is_downloaded && !option.auto_download) {
      return 'This checkpoint is not installed and has no managed download.'
    }
    return ''
  }

  const submit = () => {
    if (!plan) return
    const blockedIndex = models.findIndex((model, index) => getModelBlockedReason(index, model))
    if (blockedIndex >= 0) {
      window.alert(getModelBlockedReason(blockedIndex, models[blockedIndex]) || 'This checkpoint is unavailable for the selected segment.')
      setPaused(true)
      return
    }
    if (models.includes('minimax_h3_ref2va') && !ref2vaTermsAccepted) {
      window.alert('Accept the MiniMax H3 Ref2VA model terms before submitting this plan.')
      setPaused(true)
      return
    }
    approve({
      segmentOverrides: models.map((model, index) => ({
        model_type: model,
        drop_semantic_refs: model !== 'minimax_h3_ref2va',
        reason: model === plan.segments[index]?.model_type
          ? plan.segments[index].model_reason
          : 'user plan override',
      })),
      boundaryOverrides: boundaries.map(type => ({ type })),
    })
  }

  useEffect(() => {
    if (!plan || paused || requireReview || (models.includes('minimax_h3_ref2va') && !ref2vaTermsAccepted)) return
    const timer = window.setTimeout(() => {
      if (seconds <= 0) submit()
      else setSeconds(value => value - 1)
    }, seconds <= 0 ? 0 : 1000)
    return () => window.clearTimeout(timer)
    // submit intentionally uses the latest render's editable plan.
  }, [plan, paused, requireReview, seconds, models, ref2vaTermsAccepted]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!plan) return null
  const planFps = plan.fps || 24
  const planPublishedFrames = plan.published_frames || plan.requested_frames
  const planLoadSeconds = planEstimate?.model_load_state === 'resident'
    ? 0
    : Number(planEstimate?.model_load_seconds || 0)
  const needsRef2VA = models.includes('minimax_h3_ref2va')
  const modelStatus = (option: H3CheckpointOption) => (
    option.is_downloaded
      ? 'installed'
      : option.auto_download
        ? 'will auto-download'
        : 'not installed'
  )
  const requestedProfileLabel = h3Profiles.find(
    profile => profile.id === pinkCompatibility?.requestedProfileId,
  )?.label || pinkCompatibility?.requestedProfileId
  const pinkReconciliationLabel = (
    pinkCompatibility?.requestedProfileId === h3SelectedProfile
    && pinkCompatibility.loading === false
    && !pinkCompatibility.compatible
  )
    ? `${requestedProfileLabel || 'Current profile'} incompatible; selecting PinkCherry also selects ${pinkCompatibility.fallbackProfileLabel || pinkCompatibility.fallbackProfileId || 'the server fallback'}`
    : ''
  const optionLabel = (index: number, option: H3CheckpointOption) => {
    const reason = getModelBlockedReason(index, option.model_type)
    const profileWarning = option.model_type === 'minimax_h3_pinkcherry_fl2va'
      ? pinkReconciliationLabel
      : ''
    return `${MODEL_LABELS[option.model_type] || option.name} · ${modelStatus(option)}${profileWarning ? ` · ${profileWarning}` : ''}${reason ? ` · unavailable: ${reason}` : ''}`
  }
  const invalidSelections = models
    .map((model, index) => getModelBlockedReason(index, model))
    .filter(Boolean)
  const missingModels = Array.from(new Set(models))
    .map(model => optionByModel.get(model))
    .filter((option): option is H3CheckpointOption => Boolean(
      option && !option.is_downloaded && option.auto_download,
    ))

  const changeModel = async (index: number, model: H3Model) => {
    setPaused(true)
    const reconciled = await selectModel(model)
    if (!reconciled) {
      window.alert('This checkpoint could not be reconciled with the active H3 performance profile.')
      return
    }
    setModels(values => values.map((value, i) => i === index ? model : value))
  }
  const changeBoundary = (index: number, type: BoundaryType) => {
    setPaused(true)
    setBoundaries(values => values.map((value, i) => i === index ? type : value))
  }
  const setReviewPreference = (enabled: boolean) => {
    setRequireReview(enabled)
    setPaused(enabled)
    try { localStorage.setItem(REQUIRE_REVIEW_KEY, String(enabled)) } catch { /* local preference only */ }
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center overflow-y-auto overscroll-contain bg-black/70 p-3 [-webkit-overflow-scrolling:touch]"
      style={{
        paddingTop: 'max(0.75rem, env(safe-area-inset-top))',
        paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))',
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Review long video plan"
    >
      <div className="flex max-h-[calc(100dvh-1.5rem)] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-border bg-bg-secondary shadow-2xl sm:max-h-[92vh]">
        <div className="flex items-start justify-between border-b border-border px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">Review long-video plan</h2>
            <p className="mt-0.5 text-[11px] text-text-muted">
              Planned segments {plan.clip_count} · {(planPublishedFrames / planFps).toFixed(2)}s published
              {plan.planned_frames !== planPublishedFrames && ` · ${(plan.planned_frames / planFps).toFixed(2)}s generated`}
              {' '}· {switchCount} checkpoint switch{switchCount === 1 ? '' : 'es'}
            </p>
            {planEstimate && (
              <p className="mt-0.5 text-[10px] text-text-muted" title={`Confidence: ${planEstimate.confidence}. ${planEstimate.uncertainty_reasons.join('; ')}`}>
                Planned time {compactTime(planEstimate.seconds + planLoadSeconds)}
                {' '}· range {compactTime(planEstimate.range_seconds.low + planLoadSeconds)}–{compactTime(planEstimate.range_seconds.high + planLoadSeconds)}
              </p>
            )}
          </div>
          <button onClick={cancel} className="rounded p-1 text-text-muted hover:bg-bg-hover hover:text-text-primary" aria-label="Cancel generation"><X size={16} /></button>
        </div>

        <div className="min-h-0 overflow-y-auto overscroll-contain p-4 [-webkit-overflow-scrolling:touch]">
          <div className="mb-3 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-2.5 text-[10px] text-text-secondary">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-400" />
            <span>FL2VA continues motion from frame anchors. Ref2VA uses semantic references and recent context. A final end frame locks the last segment to FL2VA. Semantic references remain attached to the plan but are not consumed by FL2VA segments.</span>
          </div>
          {needsRef2VA && (
            <div className="mb-3 rounded-lg border border-violet-500/30 bg-violet-500/5 p-2.5 text-[10px] text-text-secondary">
              <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-start">
                <span className="flex-1">
                  {ref2vaTermsAccepted ? 'MiniMax H3 Ref2VA model terms are accepted for this host. ' : `${HOST_TERM_NOTICES.minimax_h3_ref2va.text} Notice v${HOST_TERM_NOTICES.minimax_h3_ref2va.version}. `}
                  <a href={HOST_TERM_NOTICES.minimax_h3_ref2va.href} target="_blank" rel="noreferrer" className="text-accent-blue hover:underline">{HOST_TERM_NOTICES.minimax_h3_ref2va.linkLabel}</a>.
                </span>
                {!ref2vaTermsAccepted && hostTerms && (
                  <button
                    type="button"
                    disabled={hostTermsLoading}
                    onClick={() => { setPaused(true); void acceptHostTerm('minimax_h3_ref2va') }}
                    className="w-full shrink-0 rounded border border-violet-400/50 px-2 py-1 text-violet-200 hover:bg-violet-500/10 disabled:opacity-50 sm:w-auto sm:px-1.5 sm:py-0.5"
                  >
                    Accept for this host
                  </button>
                )}
              </div>
              {!ref2vaTermsAccepted && hostTermsError && (
                <p className="mt-1 text-red-300">{hostTermsError}</p>
              )}
            </div>
          )}
          {missingModels.length > 0 && (
            <div className="mb-3 rounded-lg border border-blue-500/25 bg-blue-500/5 p-2.5 text-[10px] text-text-secondary">
              Will auto-download after approval: {missingModels.map(option => MODEL_LABELS[option.model_type]).join(', ')}. These are server-managed checkpoints; this plan never selects a custom URL.
            </div>
          )}

          <div className="space-y-2">
            {plan.segments.map((segment, index) => {
              const selectedOption = optionByModel.get(models[index])
              const selectedBlockedReason = getModelBlockedReason(index, models[index])
              const generatedFrames = segment.generated_frames ?? segment.frames
              const publishedFrames = segment.published_frames ?? generatedFrames
              const generatedSeconds = segment.generated_duration_seconds ?? segment.duration_seconds
              const publishedSeconds = segment.published_duration_seconds ?? generatedSeconds
              return (
                <div key={segment.index} className={`rounded-lg border p-3 ${models[index] === 'minimax_h3_ref2va' ? 'border-violet-500/35 bg-violet-500/5' : models[index] === 'minimax_h3_pinkcherry_fl2va' ? 'border-rose-500/35 bg-rose-500/5' : 'border-blue-500/35 bg-blue-500/5'}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-semibold text-text-primary">Segment {segment.index}</span>
                    <span className="text-[10px] text-text-muted">
                      {publishedSeconds.toFixed(2)}s published · {publishedFrames}f
                      {generatedFrames !== publishedFrames && ` · ${generatedSeconds.toFixed(2)}s generated · ${generatedFrames}f`}
                    </span>
                    {index > 0 && (
                      <select value={boundaries[index - 1]} onChange={event => changeBoundary(index - 1, event.target.value as BoundaryType)} className="ml-auto rounded border border-border bg-bg-primary px-2 py-1 text-[10px] text-text-secondary">
                        {Object.entries(BOUNDARY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    )}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <select disabled={h3ProfileApplying !== null} value={models[index]} onChange={event => { void changeModel(index, event.target.value as H3Model) }} className="rounded border border-border bg-bg-primary px-2 py-1 text-[11px] text-text-primary disabled:cursor-wait disabled:opacity-60">
                      {checkpointOptions.map(option => (
                        <option
                          key={option.model_type}
                          value={option.model_type}
                          disabled={Boolean(getModelBlockedReason(index, option.model_type))}
                        >
                          {optionLabel(index, option)}
                        </option>
                      ))}
                    </select>
                    {selectedOption && (
                      <span className={`rounded px-1.5 py-0.5 text-[9px] ${selectedOption.is_downloaded ? 'bg-emerald-500/15 text-emerald-200' : selectedOption.auto_download ? 'bg-blue-500/15 text-blue-200' : 'bg-amber-500/15 text-amber-200'}`}>
                        {modelStatus(selectedOption)}
                      </span>
                    )}
                    {h3ProfileApplying !== null && (
                      <span className="text-[9px] text-blue-200">Reconciling performance profile…</span>
                    )}
                    <span className="text-[10px] text-text-muted">{segment.model_reason}</span>
                    {segment.edge_anchor_locked && (
                      <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-[9px] text-sky-200">
                        {index === plan.segments.length - 1 ? 'Final end frame reserved' : 'First frame reserved'}
                      </span>
                    )}
                    {index > 0 && (
                      <span className="text-[9px] text-text-muted">
                        {models[index] === models[index - 1] ? 'Checkpoint retained' : 'Checkpoint switch'}
                      </span>
                    )}
                  </div>
                  {models[index] !== 'minimax_h3_ref2va' && (
                    <p className="mt-1 text-[9px] text-text-muted">FL2VA uses this segment's frame/continuity anchors; supplied semantic references are not applied on this segment.</p>
                  )}
                  {selectedBlockedReason && (
                    <p className="mt-1 text-[9px] text-red-300">Unavailable: {selectedBlockedReason}</p>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        <div className="shrink-0 flex flex-wrap items-center gap-2 border-t border-border px-4 py-3">
          <label className="mr-auto flex items-center gap-2 text-[10px] text-text-muted">
            <input type="checkbox" checked={requireReview} onChange={event => setReviewPreference(event.target.checked)} />
            Always require explicit approval
          </label>
          {!requireReview && (
            <button
              onClick={() => setPaused(true)}
              disabled={paused}
              className="flex items-center gap-1 rounded border border-border px-2.5 py-1.5 text-[10px] text-text-secondary hover:bg-bg-hover disabled:cursor-default disabled:opacity-60"
            >
              <Pause size={12} />{paused ? 'Countdown dismissed' : `Dismiss countdown · ${seconds}s`}
            </button>
          )}
          <button onClick={cancel} className="rounded border border-border px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-hover">Cancel</button>
          <button disabled={h3ProfileApplying !== null || invalidSelections.length > 0} onClick={submit} className="flex items-center gap-1.5 rounded bg-accent-blue px-3 py-1.5 text-xs font-medium text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"><Check size={13} />Generate now</button>
        </div>
      </div>
    </div>
  )
}
