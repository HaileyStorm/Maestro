import { useEffect, useState } from 'react'
import { Lock, Unlock } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { controlFpsTotalFrames, effectiveSlidingWindowGeometry, hasGlobalTimeline, usesStudioSegments } from '../../lib/timelinePrompt'
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
  const showSlidingWindow = windowCount > 1

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
  const promptLineCount = useStore(s => s.params.prompt.split('\n').filter((l: string) => l.trim()).length)
  const globalTimeline = useStore(s => hasGlobalTimeline(s.params.prompt))

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <label className="text-[11px] text-text-muted uppercase tracking-wider">Duration</label>
        <span className="text-xs text-text-secondary">
          {duration >= 60 ? `${Math.floor(duration / 60)}m${duration % 60 ? ` ${duration % 60}s` : ''}` : `${duration}s`}
          {showSlidingWindow && (
            <span className="text-text-muted ml-1">({windowCount} {usesSegments ? 'segments' : 'win'})</span>
          )}
        </span>
      </div>
      <input
        type="range"
        min={durationMin}
        max={durationMax}
        step={durationStep}
        value={duration}
        onChange={e => setDuration(Number(e.target.value))}
      />
      {showSlidingWindow && !isMultiClip && (
        <div className="text-[10px] text-text-muted mt-1">
          {windowCount} {usesSegments ? 'segments' : 'windows'} of up to {windowSize.toFixed(windowSize % 1 ? 2 : 0)}s &middot; {globalTimeline ? (
            'global timeline mapped automatically'
          ) : (
            <>{promptLineCount}/{windowCount} prompts{promptLineCount < windowCount && ' (last reused)'}</>
          )}
        </div>
      )}
      {supportsWindowPlanning && !isMultiClip && (
        <div className="mt-3 rounded-lg border border-border bg-bg-tertiary/60 p-2.5">
          <div className="flex items-center justify-between mb-1.5">
            <div className="flex items-center gap-1.5">
              <label className="text-[11px] text-text-muted uppercase tracking-wider">{usesSegments ? 'Segment size' : 'Window size'}</label>
              <button
                type="button"
                onClick={() => setLocked(!locked)}
                className={`flex items-center gap-1 rounded px-1 py-0.5 text-[9px] ${locked ? 'text-accent-blue' : 'text-text-muted hover:text-text-secondary'}`}
                title={locked ? 'Manual window size — click for Automatic' : 'Automatic window size — click to edit manually'}
              >
                {locked ? <Lock size={10} /> : <Unlock size={10} />}
                {locked ? 'Manual' : 'Automatic'}
              </button>
            </div>
            <span className="text-xs text-text-secondary">
              {windowSize.toFixed(windowSize % 1 ? 2 : 0)}s · {geometry?.windowFrames ?? Math.round(windowSize * fps)}f · {windowCount} {usesSegments ? 'seg' : 'win'}
            </span>
          </div>
          <input
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
              ? `Each segment stays within H3's ${windowMax.toFixed(2)}s legal aligned maximum (~15s nominal) and is joined automatically. The published result is trimmed to the requested length on H3's 17-frame grid.`
              : 'Effective aligned value for this model. Larger windows use more VRAM; smaller windows create more joins.'}
          </p>
          {usesSegments && (
            <label className="mt-2 flex items-start gap-2 rounded-md border border-border/70 bg-bg-primary/40 p-2 text-[10px] text-text-secondary">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={h3AdaptiveConditioning}
                onChange={event => setParam('h3_adaptive_conditioning', event.target.checked)}
              />
              <span>
                <span className="font-medium text-text-primary">Automatically choose FL2VA / Ref2VA per segment</span>
                <span className="mt-0.5 block text-text-muted">On by default. Maestro uses the supplied anchors/references and cut timing, carries temporal context where supported, minimizes checkpoint switches, then shows an editable plan before queueing.</span>
              </span>
            </label>
          )}
          {usesSegments && (
            <div className="mt-2">
              <button type="button" onClick={() => setEvaluationOpen(value => !value)} className="text-[9px] text-accent-blue hover:underline">
                {evaluationOpen ? 'Hide' : 'Show'} evaluated H3 engine / encoder profiles
              </button>
              {evaluationOpen && (
                <div className="mt-1.5 space-y-1 rounded-md border border-border/70 bg-bg-primary/40 p-2">
                  {evaluationError && <p className="text-[9px] text-red-300">{evaluationError}</p>}
                  {evaluationCatalog && Object.values(evaluationCatalog.profiles).map(profile => (
                    <div key={profile.id} className="flex items-start justify-between gap-2 text-[9px]">
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
          Single-window model · effective aligned output {geometry?.totalFrames ?? Math.round(duration * fps)} frames at {fps} fps.
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
            <label className="text-[11px] text-text-muted uppercase tracking-wider">Window Overlap</label>
            <span className="text-xs text-text-secondary">{overlap}f ({overlapSeconds}s)</span>
          </div>
          <input
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
