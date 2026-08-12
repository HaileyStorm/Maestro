import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Check, X } from 'lucide-react'
import { useStore } from '../stores/useStore'
import type { H3SegmentBoundary, H3SegmentPlan, H3SegmentPlanItem } from '../types'
import { HOST_TERM_NOTICES } from '../lib/hostTerms'
import { closeModalIfTop, installModalFocus } from '../lib/modalFocus'

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
      }, 0)
      return () => window.clearTimeout(reset)
    }
    const timer = window.setTimeout(() => {
      setModels(plan.segments.map(segment => segment.model_type))
      setBoundaries(plan.segments.slice(1).map(segment => segment.boundary_from_previous?.type || 'continuous'))
      setEditorJobId(planJobId)
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
        || reviewSecondsRemaining === 0
        || reviewLoadingRef.current
        || operationRef.current
      ) return
      const blockedIndex = models.findIndex((model, index) => getModelBlockedReason(index, model))
      if (blockedIndex >= 0) {
        window.alert(getModelBlockedReason(blockedIndex, models[blockedIndex]) || 'This checkpoint is unavailable for the selected segment.')
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
                    disabled={hostTermsLoading || reviewLoading}
                    onClick={() => { void acceptHostTerm('minimax_h3_ref2va') }}
                    className="min-h-11 w-full shrink-0 rounded border border-violet-400/50 px-3 py-2 text-violet-200 transition-colors hover:bg-violet-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 disabled:opacity-50 sm:w-auto"
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
                      <select disabled={!editsReady || reviewLoading} value={boundaries[index - 1]} onChange={event => changeBoundary(index - 1, event.target.value as BoundaryType)} aria-label={`Boundary before segment ${segment.index}`} className="ml-auto min-h-11 max-w-full rounded border border-border bg-bg-primary px-2 py-1 text-[10px] text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50">
                        {Object.entries(BOUNDARY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    )}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <select disabled={!editsReady || reviewLoading} value={models[index]} onChange={event => changeModel(index, event.target.value as H3Model)} aria-label={`Checkpoint for segment ${segment.index}`} className="min-h-11 max-w-full rounded border border-border bg-bg-primary px-2 py-1 text-[11px] text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50">
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

        <div className="flex max-h-[45vh] shrink-0 flex-wrap items-center gap-2 overflow-y-auto overscroll-contain border-t border-border px-4 py-3 [-webkit-overflow-scrolling:touch]">
          <div className="mr-auto min-w-0 text-[10px] text-text-muted">
            <p className="truncate">Job {planJobId} · Project {planWorkspace}</p>
            <p role="status" aria-live="polite" className="mt-1 text-amber-200">
              {reviewSecondsRemaining == null
                ? !ref2vaTermsAccepted && (planReviewTermsRequired || needsRef2VA)
                  ? 'Approval required to accept Ref2VA terms'
                  : 'Explicit plan approval required'
                : reviewSecondsRemaining > 0
                  ? `Server auto-accepts this frozen plan in ${Math.ceil(reviewSecondsRemaining)}s`
                  : 'Server is auto-accepting this frozen plan…'}
            </p>
            {reviewError && <p role="alert" className="mt-1 text-red-300">{reviewError}</p>}
          </div>
          <button type="button" disabled={reviewLoading} onClick={cancelGeneration} className="min-h-11 w-full rounded border border-red-400/40 px-3 py-2 text-xs text-red-300 transition-colors hover:bg-red-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:cursor-wait disabled:opacity-50 sm:w-auto">Cancel generation</button>
          <button type="button" disabled={reviewLoading || reviewSecondsRemaining === 0 || invalidSelections.length > 0} onClick={submit} className="flex min-h-11 w-full items-center justify-center gap-1.5 rounded bg-accent-blue px-3 py-2 text-xs font-medium text-white transition-[filter] hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-bg-secondary disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"><Check size={13} aria-hidden="true" />{reviewLoading ? 'Applying…' : 'Approve & resume'}</button>
        </div>
      </div>
    </div>
  )
  return typeof document === 'undefined' ? dialog : createPortal(dialog, document.body)
}
