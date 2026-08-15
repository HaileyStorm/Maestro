import { useEffect, useState } from 'react'
import { Lock, Unlock } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { controlFpsTotalFrames, effectiveSlidingWindowGeometry, hasGlobalTimeline, usesStudioSegments } from '../../lib/timelinePrompt'
import { formatMediaDuration } from '../../lib/format'
import * as api from '../../api/client'

export function DurationSlider() {
  const duration = useStore(s => s.durationSeconds)
  const setDuration = useStore(s => s.setDurationSeconds)
  const windowSize = useStore(s => s.slidingWindowSeconds)
  const setWindowSize = useStore(s => s.setSlidingWindowSeconds)
  const overlap = useStore(s => s.slidingWindowOverlap)
  const locked = useStore(s => s.slidingWindowLocked)
  const setLocked = useStore(s => s.setSlidingWindowLocked)
  const modelOptions = useStore(s => s.modelOptions)
  const guideVideoFps = useStore(s => s.guideVideoFps)
  const guideVideoFrameCount = useStore(s => s.guideVideoFrameCount)
  const forceFps = useStore(s => s.params.force_fps)
  const videoGuide = useStore(s => s.params.video_guide)
  const h3AdaptiveConditioning = useStore(s => s.params.h3_adaptive_conditioning !== false)
  const setParam = useStore(s => s.setParam)
  const h3SegmentEstimate = useStore(s => s.h3SegmentCountEstimate)
  const h3EstimateLoading = useStore(s => s.h3EstimateLoading)
  const invalidateH3Estimates = useStore(s => s.invalidateH3PerformanceEstimates)
  const refreshH3Estimates = useStore(s => s.refreshH3PerformanceEstimates)
  const h3ReferenceShapeKey = useStore(s => [
    Number(Boolean(s.startImage || s.params.image_start)),
    Number(Boolean(s.endImage || s.params.image_end)),
    Math.max(s.imageRefs.length, Array.isArray(s.params.image_refs) ? s.params.image_refs.length : 0),
    Number(Boolean(s.params.video_guide)),
    Number(Boolean(s.params.video_guide2)),
    Number(Boolean(s.params.video_guide3)),
    Number(Boolean(s.params.audio_guide)),
    Number(Boolean(s.params.audio_guide2)),
    Number(Boolean(s.params.audio_guide3)),
  ].join(':'))
  const fps = modelOptions?.fps ?? 16
  const supportsSliding = modelOptions?.sliding_window === true
  const usesSegments = usesStudioSegments(modelOptions)
  const supportsWindowPlanning = supportsSliding || usesSegments
  const swDefaults = modelOptions?.sliding_window_defaults || {}
  const durationMin = modelOptions ? modelOptions.frames_minimum / fps : 1
  const durationMax = usesSegments ? 300 : modelOptions?.frames_maximum ? modelOptions.frames_maximum / fps : 300
  const durationStep = usesSegments ? 1 : Math.max(
    1 / fps,
    (modelOptions?.frame_alignment_modulus || modelOptions?.frames_steps || 1) / fps,
  )
  const windowMin = Math.max(1 / fps, (swDefaults.window_min ?? (usesSegments ? modelOptions?.frames_minimum : Math.round(3 * fps)) ?? 1) / fps)
  const windowMax = Math.max(windowMin, (swDefaults.window_max ?? (usesSegments ? modelOptions?.frames_maximum : Math.round(40 * fps)) ?? Math.round(40 * fps)) / fps)
  const windowStep = Math.max(1 / fps, (swDefaults.window_step ?? (usesSegments ? modelOptions?.frame_alignment_modulus : 1) ?? 1) / fps)
  const frameOverrides = {
    totalFrames: controlFpsTotalFrames(duration, forceFps, videoGuide, guideVideoFps, guideVideoFrameCount),
  }
  const [evaluationOpen, setEvaluationOpen] = useState(false)
  const [evaluationCatalog, setEvaluationCatalog] = useState<Awaited<ReturnType<typeof api.fetchH3EvaluationCatalog>> | null>(null)
  const [evaluationError, setEvaluationError] = useState('')

  useEffect(() => {
    if (!usesSegments || !evaluationOpen || evaluationCatalog) return
    api.fetchH3EvaluationCatalog()
      .then(value => { setEvaluationCatalog(value); setEvaluationError('') })
      .catch(error => setEvaluationError(error instanceof Error ? error.message : 'Catalog unavailable'))
  }, [usesSegments, evaluationOpen, evaluationCatalog])

  const geometry = modelOptions
    ? effectiveSlidingWindowGeometry(duration, windowSize, overlap, modelOptions, frameOverrides)
    : null
  const windowCount = geometry?.windowCount ?? 1
  const plannedUnitLabel = usesSegments
    ? windowCount === 1 ? 'shot' : 'shots'
    : windowCount === 1 ? 'section' : 'sections'
  const showSlidingWindow = windowCount > 1

  const prompt = useStore(s => s.params.prompt)
  useEffect(() => {
    if (!usesSegments) return
    invalidateH3Estimates()
    const timer = window.setTimeout(() => {
      void refreshH3Estimates()
    }, 300)
    return () => window.clearTimeout(timer)
  }, [
    duration, h3AdaptiveConditioning, h3ReferenceShapeKey,
    invalidateH3Estimates, locked, overlap, prompt, refreshH3Estimates,
    usesSegments, windowSize,
  ])

  // Auto-track within the selected model's declared window range. Manual
  // movement locks the value; model switching rehydrates/clamps in the store.
  //
  // The +1s buffer is the fix for an observed bug: when duration was
  // set EXACTLY equal to sliding window size, wgp's internal latent-
  // step quantization could land video_length one step ABOVE
  // sliding_window_size after rounding, causing a single-window clip
  // to split into two windows and produce a stutter at the boundary.
  // Adding a small buffer guarantees sliding_window stays comfortably
  // above video_length after quantization. The cost — user sees
  // "Window: 20s" for a 19s clip — is trivial; the benefit is
  // single-window generation always works as intended.
  useEffect(() => {
    if (supportsWindowPlanning && !locked) {
      const desired = usesSegments
        ? windowMax
        : Math.min(windowMax, Math.max(windowMin, duration + 1))
      if (Math.abs(desired - windowSize) > windowStep / 2) setWindowSize(desired)
    }
  }, [duration, locked, supportsWindowPlanning, usesSegments, windowMin, windowMax, windowStep]) // eslint-disable-line react-hooks/exhaustive-deps

  const imageMode = useStore(s => s.params.image_mode)
  const isMultiClip = imageMode === 2
  const promptLineCount = prompt.split('\n').filter((l: string) => l.trim()).length
  const globalTimeline = useStore(s => hasGlobalTimeline(s.params.prompt))
  const estimatedSegmentLabel = h3SegmentEstimate
    ? h3SegmentEstimate.minimum === h3SegmentEstimate.maximum
      ? String(h3SegmentEstimate.likely)
      : `${h3SegmentEstimate.minimum}–${h3SegmentEstimate.maximum} (likely ${h3SegmentEstimate.likely})`
    : h3EstimateLoading ? 'calculating…' : 'unavailable'

  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <label htmlFor="studio-duration-seconds" className="text-[11px] text-text-muted uppercase tracking-wider">Duration</label>
        <span className="text-xs text-text-secondary">
          {formatMediaDuration(duration)}
          {(usesSegments || showSlidingWindow) && (
            <span className="text-text-muted ml-1">
              ({usesSegments
                ? `Estimated shots ${estimatedSegmentLabel}`
                : `${windowCount} ${plannedUnitLabel}`})
            </span>
          )}
        </span>
      </div>
      <input
        id="studio-duration-seconds"
        type="range"
        min={durationMin}
        max={durationMax}
        step={durationStep}
        value={duration}
        onChange={e => setDuration(Number(e.target.value))}
      />
      {(usesSegments || showSlidingWindow) && !isMultiClip && (
        <div className="text-[10px] text-text-muted mt-1">
          {usesSegments ? (
            <span title={h3SegmentEstimate?.reason}>
              {`Estimated shots ${estimatedSegmentLabel}`}
              {' '}· maximum {formatMediaDuration(windowSize)} each
              {globalTimeline ? ' · authored timestamps stay exact' : ' · prompt beats can produce shorter, uneven shots'}
            </span>
          ) : <>
            {windowCount} sections of up to {formatMediaDuration(windowSize)} &middot; {globalTimeline ? (
              'full-video timing mapped automatically'
            ) : (
              <>{promptLineCount}/{windowCount} descriptions{promptLineCount < windowCount && ' (last reused)'}</>
            )}
          </>}
        </div>
      )}
      {supportsWindowPlanning && !isMultiClip && (
        <div className="mt-3 rounded-lg border border-border bg-bg-tertiary/60 p-2.5">
          <div className="mb-1.5 flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
            <div className="flex min-w-0 flex-wrap items-center gap-1.5">
              <label htmlFor="studio-window-seconds" className="text-[11px] text-text-muted uppercase tracking-wider">{usesSegments ? 'Maximum shot length' : 'Maximum section length'}</label>
              <button
                type="button"
                onClick={() => setLocked(!locked)}
                aria-pressed={locked}
                className={`mobile-control-target flex items-center justify-center gap-1 rounded px-2 py-0.5 text-[9px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${locked ? 'text-accent-blue' : 'text-text-muted hover:text-text-secondary'}`}
                title={usesSegments
                  ? locked
                    ? 'Maximum shot length is set manually — click for Automatic planning'
                    : 'Shot length is planned automatically — click to set a maximum'
                  : locked ? 'Maximum section length is set manually — click for Automatic' : 'Section length is automatic — click to edit manually'}
              >
                {locked ? <Lock size={10} /> : <Unlock size={10} />}
                {locked ? 'Manual' : 'Automatic'}
              </button>
            </div>
            <span className="text-xs text-text-secondary">
              {formatMediaDuration(windowSize)} · {geometry?.windowFrames ?? Math.round(windowSize * fps)}f · {windowCount} {plannedUnitLabel}
            </span>
          </div>
          <input
            id="studio-window-seconds"
            type="range"
            min={windowMin}
            max={windowMax}
            step={windowStep}
            value={windowSize}
            onChange={event => {
              setWindowSize(Number(event.target.value))
              if (!locked) setLocked(true)
            }}
          />
          <p className="mt-1 text-[9px] text-text-muted">
            {usesSegments
              ? locked
                ? `This is a hard maximum: no planned shot exceeds ${formatMediaDuration(windowSize)}. Prompt-led shots can be shorter or uneven, and the final export is trimmed to the duration above.`
                : `Automatic planning can vary shot length up to ${formatMediaDuration(windowMax)} to follow your prompt. Authored timestamps remain exact, and the final export is trimmed to the duration above.`
              : 'Larger sections use more VRAM; smaller sections create more joins.'}
          </p>
          {usesSegments && (
            <label className="mobile-control-target mt-2 flex cursor-pointer items-start gap-2 rounded-md border border-border/70 bg-bg-primary/40 p-2 text-[10px] text-text-secondary">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={h3AdaptiveConditioning}
                onChange={event => setParam('h3_adaptive_conditioning', event.target.checked)}
              />
              <span>
                <span className="font-medium text-text-primary">Match each shot to its references automatically</span>
                <span className="mt-0.5 block text-text-muted">On by default. The job appears on its card immediately while Maestro uses your start/end images, reference media, and cut timing to choose the best mode for each shot. Videos with more than one shot pause briefly for plan review, then continue automatically if you leave the plan unchanged and accept any required model terms.</span>
              </span>
            </label>
          )}
          {usesSegments && (
            <details className="mt-1 pl-2 text-[8px] leading-relaxed text-text-muted">
              <summary className="mobile-control-target inline-flex cursor-pointer items-center rounded text-accent-blue hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">Mode details</summary>
              When enabled, Maestro may choose FL2VA or Ref2VA for each shot.
            </details>
          )}
          {usesSegments && (
            <div className="mt-2">
              <button
                type="button"
                aria-expanded={evaluationOpen}
                aria-controls="h3-evaluated-profiles"
                onClick={() => setEvaluationOpen(value => !value)}
                className="mobile-control-target inline-flex items-center rounded text-left text-[9px] text-accent-blue hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
              >
                {evaluationOpen ? 'Hide' : 'Show'} technical comparison profiles
              </button>
              {evaluationOpen && (
                <div id="h3-evaluated-profiles" className="mt-1.5 space-y-1 rounded-md border border-border/70 bg-bg-primary/40 p-2">
                  {evaluationError && <p className="text-[9px] text-red-300">{evaluationError}</p>}
                  {evaluationCatalog && Object.values(evaluationCatalog.profiles).map(profile => (
                    <div key={profile.id} className="flex flex-wrap items-start justify-between gap-2 text-[9px]">
                      <span className="text-text-secondary">{profile.label}</span>
                      <span className={`shrink-0 rounded px-1 ${profile.experimental ? 'bg-amber-500/15 text-amber-300' : 'bg-accent-green/15 text-accent-green'}`}>
                        {profile.experimental ? 'experimental · opt-in' : 'official · default'}
                      </span>
                    </div>
                  ))}
                  {evaluationCatalog && <p className="pt-1 text-[8px] text-text-muted">Evaluation profiles are for comparison only; Maestro will not select them automatically.</p>}
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {!supportsWindowPlanning && modelOptions && (
        <p className="mt-1 text-[9px] text-text-muted">
          Creates this clip in one pass · {geometry?.totalFrames ?? Math.round(duration * fps)} frames at {fps} fps.
        </p>
      )}
    </div>
  )
}

/** Exposed for Advanced Settings popup */
export function WindowSettings() {
  const studioDuration = useStore(s => s.durationSeconds)
  const generationMode = useStore(s => s.generationMode)
  const editSubMode = useStore(s => s.editSubMode)
  const outpaintTrimStart = useStore(s => s.outpaintTrimStart)
  const outpaintTrimEnd = useStore(s => s.outpaintTrimEnd)
  const editVideoDuration = useStore(s => s.editVideoDuration)
  const windowSize = useStore(s => s.slidingWindowSeconds)
  const overlap = useStore(s => s.slidingWindowOverlap)
  const setOverlap = useStore(s => s.setSlidingWindowOverlap)
  const modelOptions = useStore(s => s.modelOptions)
  const guideVideoFps = useStore(s => s.guideVideoFps)
  const guideVideoFrameCount = useStore(s => s.guideVideoFrameCount)
  const forceFps = useStore(s => s.params.force_fps)
  const videoGuide = useStore(s => s.params.video_guide)
  const isOutpaint = generationMode === 'avatar' && editSubMode === 'outpaint'
  const trimmedOutpaintDuration = outpaintTrimEnd > outpaintTrimStart
    ? outpaintTrimEnd - outpaintTrimStart
    : editVideoDuration
  const duration = isOutpaint ? trimmedOutpaintDuration : studioDuration

  const fps = modelOptions?.fps ?? 16
  const swDefaults = (modelOptions as Record<string, unknown> | null)?.sliding_window_defaults as Record<string, number> | undefined
  const overlapMin = swDefaults?.overlap_min ?? 1
  const overlapMax = swDefaults?.overlap_max ?? 97
  const overlapStep = swDefaults?.overlap_step ?? 4
  const latent = Math.max(1, modelOptions?.latent_size ?? modelOptions?.frames_steps ?? 4)
  const discardFrames = Math.max(0, swDefaults?.discard_last_frames ?? 0)
  const geometry = modelOptions
    ? effectiveSlidingWindowGeometry(duration, windowSize, overlap, modelOptions, {
        totalFrames: controlFpsTotalFrames(duration, forceFps, videoGuide, guideVideoFps, guideVideoFrameCount),
      })
    : null
  const safeOverlapMax = Math.max(0, Math.min(
    overlapMax,
    (geometry?.windowFrames ?? Math.round(windowSize * fps)) - discardFrames - latent,
  ))
  const safeOverlapMin = Math.min(overlapMin, safeOverlapMax)
  const overlapSeconds = Math.round((overlap / fps) * 10) / 10
  const windowCount = geometry?.windowCount ?? 1
  const showSlidingWindow = windowCount > 1

  if (!modelOptions?.sliding_window) return null

  return (
    <div className="space-y-3">
      {showSlidingWindow && overlapStep > 0 && (
        <div>
          <p className="mb-2 text-[9px] text-text-muted">Window size and Automatic/Manual mode are in the main Studio duration panel.</p>
          <div className="flex items-center justify-between mb-1.5">
            <label htmlFor="advanced-window-overlap" className="text-[11px] text-text-muted uppercase tracking-wider">Window Overlap</label>
            <span className="text-xs text-text-secondary">{overlap}f ({overlapSeconds}s)</span>
          </div>
          <input
            id="advanced-window-overlap"
            type="range"
            min={safeOverlapMin}
            max={safeOverlapMax}
            step={overlapStep || 1}
            value={overlap}
            onChange={e => setOverlap(Number(e.target.value))}
          />
        </div>
      )}
    </div>
  )
}
