import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Check, X } from 'lucide-react'
import { useStore } from '../stores/useStore'
import type { H3SegmentBoundary, H3SegmentPlan, H3SegmentPlanItem } from '../types'
import { HOST_TERM_NOTICES } from '../lib/hostTerms'
import { closeModalIfTop, installModalFocus } from '../lib/modalFocus'
import { formatApproximateDuration, formatMediaDuration } from '../lib/format'
import { H3DurationPlanBar } from './H3DurationPlanBar'

function formatPlanEstimateTime(seconds: number): string {
  return Number.isFinite(seconds) && seconds > 0
    ? formatApproximateDuration(seconds, 'calculating…')
    : 'calculating…'
}

type H3Model = H3SegmentPlanItem['model_type']
type BoundaryType = H3SegmentBoundary['type']
type DurationSnapMode = 'manual' | 'nearest' | 'down'
type DurationRedistribution = 'none' | 'next' | 'future'
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
  minimax_h3: 'FL2VA · follows start and end frames',
  minimax_h3_pinkcherry_fl2va: 'PinkCherry FL2VA · precise start and end frames',
  minimax_h3_w4a8_fl2va: 'Kijai W4A8 FL2VA · experimental lower-memory option',
  minimax_h3_ref2va: 'Ref2VA · follows reference images and recent motion',
}

const BOUNDARY_LABELS: Record<BoundaryType, string> = {
  continuous: 'Continuous motion',
  precut: 'Continue into cut',
  cut: 'Hard camera/scene cut',
  transition: 'Smooth transition',
}

const DURATION_MODE_LABELS: Record<DurationSnapMode, string> = {
  manual: 'Edit segments',
  nearest: 'Closest match',
  down: 'Shorter match',
}

const DURATION_UNAVAILABLE_REASON_LABELS = new Map<string, string>([
  ['Requested duration is outside the legal frame grid.', 'This length is not one of the available frame lengths.'],
  ['Duration oracle call limit was reached.', 'Continuum could not verify another suggested length.'],
  ['The authoritative planner found no legal candidate.', 'Continuum could not find a compatible suggested length.'],
  ['No proven segment-efficient boundary satisfies the selected snap mode.', 'Continuum could not find a matching length it can confidently suggest.'],
  ['The next segment is fixed, so no shorter option can be offered.', 'A fixed segment prevents a shorter suggested length.'],
])

function durationUnavailableReason(mode: Exclude<DurationSnapMode, 'manual'>, reason?: unknown): string {
  const mapped = typeof reason === 'string' ? DURATION_UNAVAILABLE_REASON_LABELS.get(reason) : undefined
  if (mapped) return mapped
  return mode === 'down'
    ? 'No shorter suggested length is available for this plan.'
    : 'No nearby suggested length is available for this plan.'
}

export function H3GenerationPlanDialog() {
  const plan = useStore(s => s.pendingH3Plan)
  const planEstimate = useStore(s => s.pendingH3PlanEstimate)
  const planJobId = useStore(s => s.pendingH3PlanJobId)
  const planWorkspace = useStore(s => s.pendingH3PlanWorkspace)
  const planJobStatus = useStore(s => (
    s.pendingH3PlanJobId
      ? s.jobs.find(job => job.id === s.pendingH3PlanJobId)?.status ?? null
      : null
  ))
  const planReviewDeadline = useStore(s => (
    s.pendingH3PlanJobId
      ? s.jobs.find(job => job.id === s.pendingH3PlanJobId)?.planReviewDeadline ?? null
      : null
  ))
  const planReviewTermsRequired = useStore(s => (
    s.pendingH3PlanJobId
      ? s.jobs.find(job => job.id === s.pendingH3PlanJobId)?.planReviewTermsRequired === true
      : false
  ))
  const reviewLoading = useStore(s => s.h3PlanReviewLoading)
  const reviewError = useStore(s => s.h3PlanReviewError)
  const approve = useStore(s => s.approveH3Plan)
  const cancel = useStore(s => s.cancelH3Plan)
  const close = useStore(s => s.closeH3PlanReview)
  const availableModels = useStore(s => s.models)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const hostTerms = useStore(s => s.hostTerms)
  const hostTermsLoading = useStore(s => s.hostTermsLoading)
  const hostTermsError = useStore(s => s.hostTermsError)
  const loadHostTerms = useStore(s => s.loadHostTerms)
  const acceptHostTerm = useStore(s => s.acceptHostTerm)
  const [models, setModels] = useState<H3Model[]>([])
  const [boundaries, setBoundaries] = useState<BoundaryType[]>([])
  const [editorJobId, setEditorJobId] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [dialogRef] = useState<{ current: HTMLDivElement | null }>(() => ({ current: null }))
  const [closeRef] = useState<{ current: HTMLButtonElement | null }>(() => ({ current: null }))
  const [restoreFocusRef] = useState<{ current: HTMLElement | null }>(() => ({ current: null }))
  const [operationRef] = useState<{ current: boolean }>(() => ({ current: false }))
  const [modalIdentityRef] = useState<{ current: string | null }>(() => ({ current: null }))
  const [reviewLoadingRef] = useState<{ current: boolean }>(() => ({ current: false }))
  const [durationSnapMode, setDurationSnapMode] = useState<DurationSnapMode>('manual')
  const [durationFrames, setDurationFrames] = useState<number[]>([])
  const [durationRedistribution, setDurationRedistribution] = useState<DurationRedistribution>('none')
  const ref2vaTermsAccepted = hostTerms?.minimax_h3_ref2va.accepted === true

  const requestClose = useMemo(() => () => {
    if (reviewLoadingRef.current || operationRef.current) return
    close()
  }, [close, operationRef, reviewLoadingRef])

  useEffect(() => {
    if (activeWorkspace && !hostTerms && !hostTermsLoading) void loadHostTerms()
  }, [activeWorkspace, hostTerms, hostTermsLoading, loadHostTerms])

  useEffect(() => {
    reviewLoadingRef.current = reviewLoading
  }, [reviewLoading, reviewLoadingRef])

  useEffect(() => {
    if (!plan || !planJobId) {
      const reset = window.setTimeout(() => {
        setModels([])
        setBoundaries([])
        setEditorJobId(null)
        setDurationSnapMode('manual')
        setDurationFrames([])
        setDurationRedistribution('none')
      }, 0)
      return () => window.clearTimeout(reset)
    }
    const timer = window.setTimeout(() => {
      setModels(plan.segments.map(segment => segment.model_type))
      setBoundaries(plan.segments.slice(1).map(segment => segment.boundary_from_previous?.type || 'continuous'))
      setEditorJobId(planJobId)
      setDurationSnapMode('manual')
      setDurationFrames(plan.duration_plan?.segments.map(segment => segment.published_frames) ?? [])
      setDurationRedistribution(plan.duration_plan?.redistribution_mode ?? 'none')
    }, 0)
    return () => window.clearTimeout(timer)
  }, [plan, planJobId])

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 250)
    return () => window.clearInterval(timer)
  }, [])

  const planOpen = Boolean(plan && planJobId && planWorkspace)
  useEffect(() => {
    if (!planOpen || !planJobId || !planWorkspace || !dialogRef.current || !closeRef.current) return
    const modalIdentity = `${planWorkspace}\u0000${planJobId}`
    if (modalIdentityRef.current !== modalIdentity) {
      restoreFocusRef.current = (
        typeof HTMLElement !== 'undefined' && document.activeElement instanceof HTMLElement
      ) ? document.activeElement : null
      modalIdentityRef.current = modalIdentity
    }
    const uninstall = installModalFocus({
      document,
      dialog: dialogRef.current,
      initialFocus: closeRef.current,
      restoreFocus: restoreFocusRef.current,
      appRoot: document.getElementById('root'),
      onClose: requestClose,
      priority: 180,
    })
    return () => {
      uninstall()
      if (modalIdentityRef.current === modalIdentity) modalIdentityRef.current = null
    }
  }, [closeRef, dialogRef, modalIdentityRef, planJobId, planOpen, planWorkspace, requestClose, restoreFocusRef])

  useEffect(() => {
    if (planWorkspace && activeWorkspace !== planWorkspace) close()
  }, [activeWorkspace, close, planWorkspace])

  useEffect(() => {
    if (plan && planJobStatus !== 'waiting_for_plan_approval') close()
  }, [close, plan, planJobStatus])

  const switchCount = useMemo(
    () => models.slice(1).filter((model, index) => model !== models[index]).length,
    [models],
  )
  const editsReady = Boolean(
    plan
    && editorJobId === planJobId
    && models.length === plan.segments.length
    && boundaries.length === Math.max(0, plan.segments.length - 1),
  )
  const durationPlan = plan?.duration_plan
  const durationOutcomeReason = durationPlan?.outcome === 'acceptable'
    ? 'Continuum can use this length, but it does not exactly match your original target.'
    : durationPlan?.outcome === 'insufficient_capacity'
      ? 'The editable segments cannot be adjusted enough to reach your original target.'
      : 'The current plan matches your original target.'
  const selectedSnapCandidate = durationSnapMode === 'manual'
    ? null
    : durationPlan?.snap_candidates[durationSnapMode]
  const selectedSnapAvailable = durationSnapMode === 'manual' || Boolean(
    selectedSnapCandidate
    && selectedSnapCandidate.applied
    && selectedSnapCandidate.confidence === 'high'
    && selectedSnapCandidate.candidate_published_frames != null,
  )
  const durationEditsReady = !durationPlan || (
    durationFrames.length === durationPlan.segments.length
    && durationFrames.every((value, index) => {
      const segment = durationPlan.segments[index]
      return Number.isSafeInteger(value)
        && value >= segment.min_published_frames
        && value <= segment.max_published_frames
        && (value - segment.grid_offset) % segment.grid_step === 0
        && (!(segment.authored_locked || segment.completed_locked) || value === segment.published_frames)
    })
  )
  const reviewSecondsRemaining = planReviewDeadline == null
    ? null
    : Math.max(0, planReviewDeadline - nowMs / 1000)
  const serverOptions = (plan as H3PlanWithCheckpointOptions | null)?.checkpoint_options
  const checkpointOptions: H3CheckpointOption[] = serverOptions !== undefined
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
          : 'This model is not available for the current plan.',
      }))
  const optionByModel = new Map(checkpointOptions.map(option => [option.model_type, option]))
  const getModelBlockedReason = (index: number, model: H3Model) => {
    const option = optionByModel.get(model)
    if (!option) return 'This model is not available for the current plan.'
    if (!option.available) return option.unavailable_reason || 'This model is unavailable.'
    if (model === 'minimax_h3_ref2va' && plan?.segments[index]?.edge_anchor_locked) {
      return 'Ref2VA cannot use the supplied start or end frame for this segment.'
    }
    if (!option.is_downloaded && !option.auto_download) {
      return 'This model is not installed and cannot be downloaded automatically.'
    }
    return ''
  }

  const runIfTop = (action: () => void) => {
    if (typeof document === 'undefined') {
      action()
      return true
    }
    return closeModalIfTop(document, dialogRef.current, action)
  }

  const submit = () => {
    runIfTop(() => {
      if (
        !plan || !editsReady
        || (durationSnapMode === 'manual' && !durationEditsReady)
        || !selectedSnapAvailable
        || reviewSecondsRemaining === 0
        || reviewLoadingRef.current
        || operationRef.current
      ) return
      const blockedIndex = models.findIndex((model, index) => getModelBlockedReason(index, model))
      if (blockedIndex >= 0) {
        window.alert(getModelBlockedReason(blockedIndex, models[blockedIndex]) || 'This model is unavailable for the selected segment.')
        return
      }
      if (models.includes('minimax_h3_ref2va') && !ref2vaTermsAccepted) {
        window.alert('Accept the MiniMax H3 Ref2VA model terms before submitting this plan.')
        return
      }
      operationRef.current = true
      void Promise.resolve(approve({
        segmentOverrides: models.map((model, index) => ({
          model_type: model,
          drop_semantic_refs: model !== 'minimax_h3_ref2va',
          reason: model === plan.segments[index]?.model_type
            ? plan.segments[index].model_reason
            : 'user plan override',
        })),
        boundaryOverrides: boundaries.map(type => ({ type })),
        ...(durationPlan ? {
          planRevision: durationPlan.revision,
          durationSnapMode,
          segmentDurationEdits: durationSnapMode === 'manual'
            ? durationFrames.flatMap((publishedFrames, index) => (
                publishedFrames === durationPlan.segments[index].published_frames
                  ? []
                  : [{ segmentIndex: durationPlan.segments[index].index, publishedFrames }]
              ))
            : [],
          durationRedistribution: durationSnapMode === 'manual' ? durationRedistribution : 'none',
        } : {}),
      })).finally(() => { operationRef.current = false })
    })
  }

  const cancelGeneration = () => {
    runIfTop(() => {
      if (reviewLoadingRef.current || operationRef.current) return
      operationRef.current = true
      void Promise.resolve(cancel()).finally(() => { operationRef.current = false })
    })
  }

  if (!plan || !planJobId || !planWorkspace) return null
  const planFps = plan.fps || 24
  const planPublishedFrames = plan.published_frames || plan.requested_frames
  const planLoadSeconds = planEstimate?.model_load_state === 'resident'
    ? 0
    : Number(planEstimate?.model_load_seconds || 0)
  const needsRef2VA = editsReady && models.includes('minimax_h3_ref2va')
  const modelStatus = (option: H3CheckpointOption) => (
    option.is_downloaded
      ? 'installed'
      : option.auto_download
        ? 'will auto-download'
        : 'not installed'
  )
  const optionLabel = (index: number, option: H3CheckpointOption) => {
    const reason = getModelBlockedReason(index, option.model_type)
    return `${MODEL_LABELS[option.model_type] || option.name} · ${modelStatus(option)}${reason ? ` · unavailable: ${reason}` : ''}`
  }
  const invalidSelections = editsReady
    ? models.map((model, index) => getModelBlockedReason(index, model)).filter(Boolean)
    : ['Loading plan editor']
  const durationBlocked = (
    durationSnapMode === 'manual' && !durationEditsReady
  ) || !selectedSnapAvailable
  const missingModels = Array.from(new Set(models))
    .map(model => optionByModel.get(model))
    .filter((option): option is H3CheckpointOption => Boolean(
      option && !option.is_downloaded && option.auto_download,
    ))

  const changeModel = (index: number, model: H3Model) => {
    setModels(values => values.map((value, i) => i === index ? model : value))
  }
  const changeBoundary = (index: number, type: BoundaryType) => {
    setBoundaries(values => values.map((value, i) => i === index ? type : value))
  }

  const dialog = (
    <div
      className="fixed inset-0 z-[180] flex h-[100vh] items-center justify-center overflow-hidden supports-[height:100dvh]:h-[100dvh]"
      style={{
        paddingTop: 'max(0.75rem, env(safe-area-inset-top))',
        paddingRight: 'max(0.75rem, env(safe-area-inset-right))',
        paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))',
        paddingLeft: 'max(0.75rem, env(safe-area-inset-left))',
      }}
    >
      <button
        type="button"
        tabIndex={-1}
        disabled={reviewLoading}
        aria-label="Close long-video plan review"
        className="absolute inset-0 appearance-none border-0 bg-black/70 p-0"
        onClick={() => closeModalIfTop(document, dialogRef.current, requestClose)}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="h3-plan-dialog-title"
        aria-describedby="h3-plan-dialog-description"
        className="relative flex min-h-0 max-h-[calc(100vh-1.5rem)] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-border bg-bg-secondary shadow-2xl supports-[height:100dvh]:max-h-[calc(100dvh-1.5rem)] motion-reduce:[&_*]:transition-none motion-reduce:[&_*]:animate-none sm:max-h-[92vh]"
      >
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 id="h3-plan-dialog-title" className="text-sm font-semibold text-text-primary">Review long-video plan</h2>
            <p id="h3-plan-dialog-description" className="mt-0.5 text-[11px] text-text-muted">
              {plan.clip_count} segment{plan.clip_count === 1 ? '' : 's'} · {formatMediaDuration(planPublishedFrames / planFps)} final video
              {plan.planned_frames !== planPublishedFrames && ` · ${formatMediaDuration(plan.planned_frames / planFps)} generated`}
              {' '}· {switchCount} model change{switchCount === 1 ? '' : 's'}
            </p>
            {planEstimate && (
              <p className="mt-0.5 text-[10px] text-text-muted" title={`Confidence: ${planEstimate.confidence}. ${planEstimate.uncertainty_reasons.join('; ')}`}>
                Planned time {formatPlanEstimateTime(planEstimate.seconds + planLoadSeconds)}
                {' '}· range {formatPlanEstimateTime(planEstimate.range_seconds.low + planLoadSeconds)}–{formatPlanEstimateTime(planEstimate.range_seconds.high + planLoadSeconds)}
              </p>
            )}
          </div>
          <button
            ref={closeRef}
            type="button"
            disabled={reviewLoading}
            onClick={() => closeModalIfTop(document, dialogRef.current, requestClose)}
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-text-muted transition-colors hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-wait disabled:opacity-50"
            aria-label="Close long-video plan review"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="min-h-0 overflow-y-auto overscroll-contain flex-1 p-4 [-webkit-overflow-scrolling:touch]">
          <div className="mb-3 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-2.5 text-[10px] text-text-secondary">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-400" />
            <span>Choose FL2VA when exact start or end frames matter. Choose Ref2VA when reference images and recent motion matter. If you supplied a final frame, the last segment must use FL2VA. FL2VA keeps your references saved but does not use them.</span>
          </div>
          {needsRef2VA && (
            <div className="mb-3 rounded-lg border border-violet-500/30 bg-violet-500/5 p-2.5 text-[10px] text-text-secondary">
              <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-start">
                <span className="flex-1">
                  {ref2vaTermsAccepted ? 'MiniMax H3 Ref2VA model terms are accepted on this computer. ' : `${HOST_TERM_NOTICES.minimax_h3_ref2va.text} Notice v${HOST_TERM_NOTICES.minimax_h3_ref2va.version}. `}
                  <a href={HOST_TERM_NOTICES.minimax_h3_ref2va.href} target="_blank" rel="noreferrer" className="text-accent-blue hover:underline">{HOST_TERM_NOTICES.minimax_h3_ref2va.linkLabel}</a>.
                </span>
                {!ref2vaTermsAccepted && hostTerms && (
                  <button
                    type="button"
                    disabled={hostTermsLoading || reviewLoading}
                    onClick={() => { void acceptHostTerm('minimax_h3_ref2va') }}
                    className="min-h-11 w-full shrink-0 rounded border border-violet-400/50 px-3 py-2 text-violet-200 transition-colors hover:bg-violet-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 disabled:opacity-50 sm:w-auto"
                  >
                    Accept on this computer
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
              Continuum will download these models after you approve: {missingModels.map(option => MODEL_LABELS[option.model_type]).join(', ')}. Downloads come from Continuum’s built-in model list.
            </div>
          )}

          {durationPlan && (
            <section aria-labelledby="h3-duration-controls-title" className="mb-3 space-y-3 rounded-lg border border-border bg-bg-primary/30 p-3">
              <div>
                <h3 id="h3-duration-controls-title" className="text-xs font-semibold text-text-primary">Final video length</h3>
                <p className="mt-0.5 text-[10px] leading-relaxed text-text-muted">
                  Edit the segments yourself, or choose a suggested length. Continuum checks every frame count when you approve.
                </p>
              </div>
              <fieldset disabled={reviewLoading} className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-3">
                <legend className="sr-only">Duration mode</legend>
                {(['manual', 'nearest', 'down'] as const).map(mode => {
                  const candidate = mode === 'manual' ? null : durationPlan.snap_candidates[mode]
                  const technicalReason = typeof candidate?.reason === 'string' && candidate.reason.trim()
                    ? candidate.reason
                    : null
                  const available = mode === 'manual' || Boolean(
                    candidate?.applied
                    && candidate.confidence === 'high'
                    && candidate.candidate_published_frames != null,
                  )
                  const detail = mode === 'manual'
                    ? `${durationPlan.current_published_frames} frames in final video`
                    : available
                      ? `${candidate?.candidate_published_frames} frames · ${candidate?.segment_count} segments`
                      : durationUnavailableReason(mode, candidate?.reason)
                  return (
                    <div key={mode} className={`min-h-11 min-w-0 rounded border px-3 py-2 ${available ? 'border-border bg-bg-secondary' : 'border-border/60 bg-bg-tertiary/30 opacity-60'}`}>
                      <label className="flex min-w-0 items-start gap-2">
                        <input
                          type="radio"
                          name="h3-duration-mode"
                          value={mode}
                          checked={durationSnapMode === mode}
                          disabled={!available || reviewLoading}
                          onChange={() => setDurationSnapMode(mode)}
                          className="mt-0.5"
                        />
                        <span className="min-w-0">
                          <span className="block text-[10px] font-semibold text-text-primary">{DURATION_MODE_LABELS[mode]}</span>
                          <span className="block break-words text-[9px] leading-relaxed text-text-muted">{detail}</span>
                        </span>
                      </label>
                      {!available && technicalReason && (
                        <details className="ml-5 mt-1 text-[9px] leading-relaxed text-text-muted">
                          <summary className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">Technical details</summary>
                          <p className="mt-1 break-words">{technicalReason}</p>
                        </details>
                      )}
                    </div>
                  )
                })}
              </fieldset>

              <div className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2">
                {durationPlan.segments.map((segment, index) => {
                  const locked = segment.authored_locked || segment.completed_locked
                  return (
                    <label key={segment.index} className="min-w-0 rounded border border-border/70 p-2 text-[10px] text-text-secondary">
                      <span className="flex min-w-0 items-center justify-between gap-2">
                        <span className="font-semibold text-text-primary">Segment {segment.index} frames in final video</span>
                        {locked && <span className="shrink-0 rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] text-amber-200">Cannot edit</span>}
                      </span>
                      <input
                        type="number"
                        inputMode="numeric"
                        min={segment.min_published_frames}
                        max={segment.max_published_frames}
                        step={segment.grid_step}
                        value={durationFrames[index] ?? segment.published_frames}
                        disabled={reviewLoading || durationSnapMode !== 'manual' || locked}
                        aria-label={`Final video frames for segment ${segment.index}`}
                        onChange={event => {
                          const value = event.currentTarget.valueAsNumber
                          setDurationFrames(values => values.map((current, itemIndex) => itemIndex === index ? value : current))
                        }}
                        className="mt-1 min-h-11 w-full rounded border border-border bg-bg-primary px-2 py-1 text-sm text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-60"
                      />
                      <span className="mt-1 block break-words text-[9px] text-text-muted">
                        Allowed range {segment.min_published_frames}–{segment.max_published_frames} frames
                        {segment.completed_locked && ' · already generated'}
                        {segment.authored_locked && !segment.completed_locked && ' · fixed by the original plan'}
                      </span>
                    </label>
                  )
                })}
              </div>

              <label className="block min-w-0 text-[10px] text-text-secondary">
                Preserve the original target after manual edits
                <select
                  value={durationRedistribution}
                  disabled={reviewLoading || durationSnapMode !== 'manual'}
                  onChange={event => setDurationRedistribution(event.currentTarget.value as DurationRedistribution)}
                aria-label="How to keep the original video length"
                  className="mt-1 min-h-11 w-full rounded border border-border bg-bg-primary px-2 py-1 text-[11px] text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-60"
                >
                  <option value="none">Allow the final length to differ</option>
                  <option value="next">Adjust the next editable segment</option>
                  <option value="future">Spread the adjustment across later editable segments</option>
                </select>
              </label>

              <H3DurationPlanBar
                targetPublishedFrames={durationPlan.target_published_frames}
                currentPublishedFrames={durationPlan.current_published_frames}
                currentGeneratedFrames={durationPlan.current_generated_frames}
                currentMinusTargetFrames={-durationPlan.residual_published_frames}
                outcome={durationPlan.outcome}
                reason={durationOutcomeReason}
              />
              <p role="status" className="break-words text-[9px] leading-relaxed text-text-muted">
                This chart shows the saved plan. Your changes are checked and applied when you approve, so the totals update after approval.
              </p>
              {durationSnapMode === 'manual' && !durationEditsReady && (
                <p role="alert" className="break-words text-[9px] leading-relaxed text-red-300">
                  Enter a value within the shown range, using the allowed step size.
                </p>
              )}
            </section>
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
                      {formatMediaDuration(publishedSeconds)} final video · {publishedFrames}f
                      {generatedFrames !== publishedFrames && ` · ${formatMediaDuration(generatedSeconds)} generated · ${generatedFrames}f`}
                    </span>
                    {index > 0 && (
                      <select disabled={!editsReady || reviewLoading} value={boundaries[index - 1]} onChange={event => changeBoundary(index - 1, event.target.value as BoundaryType)} aria-label={`Boundary before segment ${segment.index}`} className="ml-auto min-h-11 max-w-full rounded border border-border bg-bg-primary px-2 py-1 text-[10px] text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50">
                        {Object.entries(BOUNDARY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    )}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <select disabled={!editsReady || reviewLoading} value={models[index]} onChange={event => changeModel(index, event.target.value as H3Model)} aria-label={`Model for segment ${segment.index}`} className="min-h-11 max-w-full rounded border border-border bg-bg-primary px-2 py-1 text-[11px] text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50">
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
                    <span className="text-[10px] text-text-muted">
                      {models[index] === 'minimax_h3_ref2va'
                        ? 'Uses reference images and recent motion'
                        : 'Follows this segment’s frame anchors'}
                    </span>
                    {segment.edge_anchor_locked && (
                      <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-[9px] text-sky-200">
                        {index === plan.segments.length - 1 ? 'Uses your final frame' : 'Uses your first frame'}
                      </span>
                    )}
                    {index > 0 && (
                      <span className="text-[9px] text-text-muted">
                        {models[index] === models[index - 1] ? 'Same model as previous segment' : 'Model changes here'}
                      </span>
                    )}
                  </div>
                  {models[index] !== 'minimax_h3_ref2va' && (
                    <p className="mt-1 text-[9px] text-text-muted">FL2VA follows this segment’s start, end, and continuity frames. It keeps reference images saved but does not use them for this segment.</p>
                  )}
                  {selectedBlockedReason && (
                    <p className="mt-1 text-[9px] text-red-300">Unavailable: {selectedBlockedReason}</p>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        <div className="flex max-h-[45vh] shrink-0 flex-wrap items-center gap-2 overflow-y-auto overscroll-contain border-t border-border px-4 py-3 [-webkit-overflow-scrolling:touch]">
          <div className="mr-auto min-w-0 text-[10px] text-text-muted">
            <p className="truncate">Job {planJobId} · Project {planWorkspace}</p>
            <p role="status" aria-live="polite" className="mt-1 text-amber-200">
              {reviewSecondsRemaining == null
                ? !ref2vaTermsAccepted && (planReviewTermsRequired || needsRef2VA)
                  ? 'Accept the Ref2VA terms before approving'
                  : 'Review the plan, then approve to continue'
                : reviewSecondsRemaining > 0
                  ? `Continuum will approve the saved plan unchanged in ${Math.ceil(reviewSecondsRemaining)}s`
                  : 'Approving the saved plan unchanged…'}
            </p>
            {reviewError && <p role="alert" className="mt-1 text-red-300">{reviewError}</p>}
          </div>
          <button type="button" disabled={reviewLoading} onClick={cancelGeneration} className="min-h-11 w-full rounded border border-red-400/40 px-3 py-2 text-xs text-red-300 transition-colors hover:bg-red-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:cursor-wait disabled:opacity-50 sm:w-auto">Cancel generation</button>
          <button type="button" disabled={reviewLoading || reviewSecondsRemaining === 0 || invalidSelections.length > 0 || durationBlocked} onClick={submit} className="flex min-h-11 w-full items-center justify-center gap-1.5 rounded bg-accent-blue px-3 py-2 text-xs font-medium text-white transition-[filter] hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-bg-secondary disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"><Check size={13} aria-hidden="true" />{reviewLoading ? 'Applying…' : 'Approve & resume'}</button>
        </div>
      </div>
    </div>
  )
  return typeof document === 'undefined' ? dialog : createPortal(dialog, document.body)
}
