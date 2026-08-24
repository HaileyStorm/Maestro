import { useState, useRef, useEffect } from 'react'
import { Sparkles, Loader2, ChevronUp } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { controlFpsTotalFrames, effectiveSlidingWindowGeometry, globalTimelineEndSeconds, hasGlobalTimeline, usesStudioSegments } from '../../lib/timelinePrompt'
import { h3StyleWorkflowCatalogStateLabel, h3StyleWorkflowSupportsModel, h3StyleWorkflowSwatch, nextH3StyleWorkflowSurprise } from '../../lib/h3StyleWorkflows'
import { requestQueueView } from '../../lib/mainViewNavigation'
import { isExactH3PromptReviewTarget } from '../../lib/h3PromptReview'
import { H3PromptCoach } from './H3PromptCoach'

const placeholders: Record<string, string> = {
  image: 'Describe your image...',
  video: 'Describe your video...',
  audio: 'Enter text to speak or describe audio...',
  avatar: 'Describe your avatar animation...',
}

export function H3StyleWorkflowField({
  effectiveVideoModel,
  surface,
}: {
  effectiveVideoModel: string
  surface: 'Generate' | 'Director'
}) {
  const catalog = useStore(state => state.h3StyleWorkflowCatalog)
  const loading = useStore(state => state.h3StyleWorkflowCatalogLoading)
  const error = useStore(state => state.h3StyleWorkflowCatalogError)
  const selection = useStore(state => state.h3StyleWorkflow)
  const setSelection = useStore(state => state.setH3StyleWorkflow)
  const loadCatalog = useStore(state => state.loadH3StyleWorkflowCatalog)

  useEffect(() => {
    if (effectiveVideoModel) void loadCatalog()
  }, [effectiveVideoModel, loadCatalog])

  const supported = h3StyleWorkflowSupportsModel(catalog, effectiveVideoModel)
  if (!supported) {
    if (!loading && !error) return null
    return (
      <fieldset aria-label={`${surface} creative guide`} className="rounded-lg border border-border bg-bg-tertiary/50 p-2">
        <legend className="px-1 text-[10px] font-medium text-text-secondary">Creative guide jukebox</legend>
        {loading ? (
          <p role="status" className="text-[9px] text-text-muted">Loading creative guides…</p>
        ) : (
          <div className="rounded border border-amber-400/30 bg-amber-400/5 px-2 py-1 text-[9px] leading-relaxed text-amber-200">
            <p role="status">{error}</p>
            <button type="button" onClick={() => void loadCatalog(true)} className="mobile-control-target mt-1 rounded border border-amber-400/40 px-2 py-0.5 text-[8px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">Retry creative guides</button>
          </div>
        )}
      </fieldset>
    )
  }

  const selected = catalog?.styles.find(style => style.id === selection)
  const sourceRevision = catalog?.source_revision || catalog?.revision || 'unknown'
  const provenance = catalog?.provenance
  const styles = catalog?.styles || []
  const chooseSurprise = () => {
    const nextId = nextH3StyleWorkflowSurprise(styles, selection, sourceRevision)
    if (nextId) setSelection(nextId)
  }
  return (
    <fieldset aria-label={`${surface} creative guide`} className="rounded-lg border border-border bg-bg-tertiary/50 p-2">
      <legend className="px-1 text-[10px] font-medium text-text-secondary">Creative guide jukebox</legend>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-1.5">
        <p className="text-[9px] text-text-muted">Pick a recipe or let the jukebox choose.</p>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={chooseSurprise}
            disabled={loading || styles.length === 0}
            className="mobile-control-target inline-flex min-w-11 items-center justify-center gap-1 rounded border border-accent-blue/35 bg-accent-blue/10 px-2 py-1 text-[9px] font-medium text-accent-blue transition-colors hover:bg-accent-blue/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-50 md:min-h-0"
          >
            <span aria-hidden="true" className="text-[11px]">✦</span> Surprise me
          </button>
          <button
            type="button"
            onClick={() => setSelection('')}
            disabled={!selection}
            className="mobile-control-target min-w-11 rounded border border-border px-2 py-1 text-[9px] text-text-muted transition-colors hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-40 md:min-h-0"
          >
            Clear
          </button>
        </div>
      </div>
      <div className="grid max-h-[28rem] grid-cols-[repeat(auto-fit,minmax(132px,1fr))] gap-1.5 overflow-y-auto overscroll-contain pr-0.5" role="group" aria-label={`${surface} creative guide choices`}>
        {styles.map(style => {
          const isSelected = style.id === selection
          const swatch = h3StyleWorkflowSwatch(style.id)
          return (
            <button
              key={style.id}
              type="button"
              data-workflow-id={style.id}
              aria-pressed={isSelected}
              onClick={() => setSelection(style.id)}
              disabled={loading}
              className={`mobile-control-target group relative min-h-11 overflow-hidden rounded-lg border p-2 text-left transition-[border-color,background-color,transform] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-1 focus-visible:ring-offset-bg-tertiary disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none ${isSelected ? 'border-accent-blue bg-accent-blue/10' : 'border-border bg-bg-secondary hover:border-accent-blue/45 hover:bg-bg-hover'}`}
            >
              <span className={`relative mb-1.5 block h-9 overflow-hidden rounded-md border border-border/80 ${
                swatch === 'paper' ? 'bg-gradient-to-br from-accent-green/35 via-bg-hover to-accent-blue/20'
                  : swatch === 'dimensional' ? 'bg-gradient-to-br from-accent-blue/40 via-bg-active to-accent-green/20'
                    : swatch === 'polished' ? 'bg-gradient-to-r from-bg-active via-accent-blue/30 to-bg-hover'
                      : swatch === 'rhythmic' ? 'bg-gradient-to-br from-accent-blue/35 via-accent-green/25 to-bg-active'
                        : 'bg-gradient-to-r from-accent-green/25 via-bg-active to-accent-blue/40'
              }`} aria-hidden="true">
                <span className={`absolute bg-text-primary/20 ${swatch === 'paper' ? '-left-2 top-2 h-7 w-16 rotate-[-8deg] rounded-sm' : swatch === 'dimensional' ? 'left-4 top-1 h-7 w-7 rotate-12 rounded-lg shadow-lg' : swatch === 'polished' ? 'inset-x-3 top-3 h-2 rounded-full' : swatch === 'rhythmic' ? 'bottom-1 left-2 h-6 w-1.5 skew-x-[-12deg] shadow-[10px_-5px_0_var(--color-text-primary),20px_3px_0_var(--color-text-primary),30px_-2px_0_var(--color-text-primary)]' : '-right-2 top-2 h-8 w-20 rotate-6 rounded-[50%]'}`} />
              </span>
              <span className="block text-[10px] font-medium leading-tight text-text-primary">{style.label}</span>
              <span className="mt-1 block text-[8px] leading-snug text-text-muted">{style.description}</span>
              <span className="sr-only">Workflow ID: {style.id}.</span>
              {isSelected && (
                <span className="mt-1.5 inline-flex items-center gap-1 text-[8px] font-semibold uppercase tracking-wide text-accent-blue">
                  <span aria-hidden="true">✓</span> Selected
                </span>
              )}
            </button>
          )
        })}
      </div>
      {styles.length === 0 && !loading && <p className="rounded border border-border bg-bg-secondary px-2 py-2 text-[9px] text-text-muted">No creative guides are available right now.</p>}
      {loading && <p role="status" className="mt-1 text-[9px] text-text-muted">Loading creative guides…</p>}
      {selected ? (
        <p className="mt-1.5 text-[9px] leading-relaxed text-text-muted"><span className="font-medium text-text-secondary">Now playing: {selected.label}.</span> {selected.description}</p>
      ) : (
        <p className="mt-1.5 text-[9px] font-medium text-text-secondary">No guide selected · prompt only</p>
      )}
      <p className="mt-1 text-[9px] leading-relaxed text-text-muted">
        Choose an optional guide for pacing, framing, and finish. It works alongside Visual style; the original recipe may include details this guide does not apply.
      </p>
      <details className="mt-1 text-[8px] leading-relaxed text-text-muted">
        <summary className="mobile-control-target inline-flex cursor-pointer items-center rounded text-accent-blue hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">Source details</summary>
        <p className="mt-0.5">
          {catalog && <><a href={catalog.source} target="_blank" rel="noreferrer" className="mobile-control-target inline-flex items-center rounded text-accent-blue hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">MiniMax H3 recipe library</a> · </>}
          {catalog ? h3StyleWorkflowCatalogStateLabel(catalog) : 'Loading guide catalog'} · revision {sourceRevision}
          {provenance?.prompt_brief_provenance === 'maestro_adapted' ? ' · Maestro interpretation' : ''}
          {catalog?.update_error ? ' · last refresh unavailable' : ''}
        </p>
      </details>
      {error && (
        <div className="mt-1 text-[8px] leading-relaxed text-amber-200">
          <p role="status">{error}</p>
          <button type="button" onClick={() => void loadCatalog(true)} className="mobile-control-target mt-1 rounded border border-amber-400/40 px-2 py-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">Retry creative guides</button>
        </div>
      )}
    </fieldset>
  )
}

export function PromptInput() {
  const prompt = useStore(s => s.params.prompt)
  const setParam = useStore(s => s.setParam)
  const generationMode = useStore(s => s.generationMode)
  const editSubMode = useStore(s => s.editSubMode)
  const enhancePrompt = useStore(s => s.enhancePrompt)
  const isEnhancing = useStore(s => s.isEnhancing)
  const durationSeconds = useStore(s => s.durationSeconds)
  const slidingWindowSeconds = useStore(s => s.slidingWindowSeconds)
  const slidingWindowOverlap = useStore(s => s.slidingWindowOverlap)
  const modelOptions = useStore(s => s.modelOptions)
  const h3ReviewImageCount = useStore(s => Math.max(
    s.imageRefs.length,
    Array.isArray(s.params.image_refs) ? s.params.image_refs.length : 0,
  ))
  const h3ReviewVideoCount = useStore(s => [s.params.video_guide, s.params.video_guide2, s.params.video_guide3].filter(Boolean).length)
  const h3ReviewAudioCount = useStore(s => [s.params.audio_guide, s.params.audio_guide2, s.params.audio_guide3].filter(Boolean).length)
  const h3ReviewHasStartAnchor = useStore(s => Boolean(s.startImage) || (Array.isArray(s.params.image_start)
    ? s.params.image_start.some(Boolean)
    : Boolean(s.params.image_start)))
  const h3ReviewHasEndAnchor = useStore(s => Boolean(s.endImage) || (Array.isArray(s.params.image_end)
    ? s.params.image_end.some(Boolean)
    : Boolean(s.params.image_end)))
  const h3ReviewAdaptiveConditioning = useStore(s => s.params.h3_adaptive_conditioning !== false)
  const guideVideoFps = useStore(s => s.guideVideoFps)
  const guideVideoFrameCount = useStore(s => s.guideVideoFrameCount)
  const forceFps = useStore(s => s.params.force_fps)
  const videoGuide = useStore(s => s.params.video_guide)
  const studioPromptEnhance = useStore(s => s.studioPromptEnhance)
  const setStudioPromptEnhance = useStore(s => s.setStudioPromptEnhance)
  const imageMode = useStore(s => s.params.image_mode)
  const effectiveVideoModel = useStore(s => s.params.model_type)
  const migrateLegacyH3StylePrompt = useStore(s => s.migrateLegacyH3StylePrompt)
  const [ttsMenuOpen, setTtsMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const ttsMenuTriggerRef = useRef<HTMLButtonElement>(null)
  const ttsPopupRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    migrateLegacyH3StylePrompt()
  }, [migrateLegacyH3StylePrompt])

  const isAudioOnly = modelOptions?.audio_only
  const voiceCount = useStore(s => s.ttsVoiceCount)
  const isMultiVoice = voiceCount >= 2
  // Does the active TTS model support multi-speaker output? Scenema, Kugel,
  // Qwen3-TTS, Index-TTS2 all do (max_voice_count >= 2 in their handlers).
  // Single-speaker-only engines leave it undefined; default 6 is the legacy
  // "any multi-speaker engine" assumption. Falling back to >1 keeps both
  // dialogue and monologue enhance available unless a model declares itself
  // single-speaker.
  const maxVoiceCount = ((modelOptions as { max_voice_count?: number } | null)?.max_voice_count) ?? 6
  const supportsDialogue = maxVoiceCount > 1
  // Main Sparkles button default: dialogue when the user has actually added
  // 2+ voice slots, monologue otherwise. The dropdown lets the user override
  // either way regardless of voice slot count.
  const defaultMode: 'dialogue' | 'monologue' = isMultiVoice ? 'dialogue' : 'monologue'
  const usesSegmentedStudio = usesStudioSegments(modelOptions)
  const showH3PromptCoach = Boolean(prompt.trim())
    && isExactH3PromptReviewTarget(effectiveVideoModel, modelOptions?.architecture)
  const supportsSlidingWindows = modelOptions?.sliding_window === true || usesSegmentedStudio
  const windowCount = modelOptions
    ? effectiveSlidingWindowGeometry(
        durationSeconds, slidingWindowSeconds, slidingWindowOverlap, modelOptions,
        { totalFrames: controlFpsTotalFrames(durationSeconds, forceFps, videoGuide, guideVideoFps, guideVideoFrameCount) },
      ).windowCount
    : 1
  const usesWindows = generationMode === 'video' && supportsSlidingWindows && windowCount > 1 && imageMode !== 2
  const globalTimelineDetected = hasGlobalTimeline(prompt)
  const authoredTimelineEnd = globalTimelineEndSeconds(prompt)
  const setDurationSeconds = useStore(s => s.setDurationSeconds)
  const usesGlobalTimeline = usesWindows && globalTimelineDetected

  // A complete Studio timeline owns its total duration. Pasting a prompt that
  // reaches 01:00 should not silently leave a 15-second generation selected.
  // Only expand here; the user can still deliberately add extra tail time.
  useEffect(() => {
    if (
      generationMode === 'video'
      && imageMode !== 2
      && authoredTimelineEnd != null
      && authoredTimelineEnd > durationSeconds + 0.01
    ) {
      setDurationSeconds(authoredTimelineEnd)
    }
  }, [authoredTimelineEnd, durationSeconds, generationMode, imageMode, setDurationSeconds])
  const enhancerFooter = !isAudioOnly
  const modePlaceholder = generationMode === 'avatar' && editSubMode === 'recast'
    ? 'Describe the finished video and replacement characters...'
    : generationMode === 'avatar' && editSubMode === 'restyle'
      ? 'Describe the finished video...'
      : (placeholders[generationMode] || 'Describe your content...')

  // The split-button disclosure owns focus while open and returns it to its
  // trigger on Escape or a completed keyboard/pointer choice.
  useEffect(() => {
    if (!ttsMenuOpen) return
    const focusFrame = window.requestAnimationFrame(() => {
      ttsPopupRef.current?.querySelector<HTMLButtonElement>('button:not([disabled])')?.focus()
    })
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setTtsMenuOpen(false)
    }
    const keyHandler = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      setTtsMenuOpen(false)
      window.requestAnimationFrame(() => ttsMenuTriggerRef.current?.focus())
    }
    document.addEventListener('mousedown', handler)
    document.addEventListener('keydown', keyHandler)
    return () => {
      window.cancelAnimationFrame(focusFrame)
      document.removeEventListener('mousedown', handler)
      document.removeEventListener('keydown', keyHandler)
    }
  }, [ttsMenuOpen])

  const runTtsEnhancement = (mode: 'monologue' | 'monologue_fast' | 'dialogue' | 'dialogue_fast') => {
    ttsMenuTriggerRef.current?.focus()
    setTtsMenuOpen(false)
    requestQueueView()
    void enhancePrompt(mode)
  }

  const runEnhancement = () => {
    requestQueueView()
    void enhancePrompt()
  }

  // grow shrink-0: fill spare vertical space when the sidebar is roomy, but
  // never shrink below the textarea's min-height. Dropping the old
  // `flex-1 min-h-0` stops the wrapper from collapsing under the textarea
  // (which made it overflow and overlap the section below).
  return (
    <div className="relative grow shrink-0 flex flex-col">
      {generationMode === 'video' && <div className="mb-1.5"><H3StyleWorkflowField effectiveVideoModel={effectiveVideoModel} surface="Generate" /></div>}
      <textarea
        value={prompt}
        onChange={e => setParam('prompt', e.target.value)}
        placeholder={usesWindows
          ? `Describe the whole video; add [00:00-00:10] timed beats for ${windowCount} planned ${usesSegmentedStudio ? 'shots' : 'sections'}`
          : modePlaceholder}
        className="w-full flex-1 rounded-lg border border-border bg-bg-tertiary px-3 py-2 pr-14 text-sm text-text-primary placeholder:text-text-muted transition-colors focus:border-accent-blue focus:outline-none md:pr-10"
        style={{ resize: 'none', minHeight: 112 }}
      />
      {generationMode === 'video' && showH3PromptCoach && (
        <div className="mt-1.5">
          <H3PromptCoach
            prompt={prompt}
            modelType={effectiveVideoModel}
            architecture={modelOptions?.architecture}
            imageCount={h3ReviewImageCount}
            videoCount={h3ReviewVideoCount}
            audioCount={h3ReviewAudioCount}
            hasStartAnchor={h3ReviewHasStartAnchor}
            hasEndAnchor={h3ReviewHasEndAnchor}
            durationSeconds={durationSeconds}
            adaptiveConditioning={h3ReviewAdaptiveConditioning}
          />
        </div>
      )}
      {usesWindows && (
        <div className="px-1 pt-1 text-[10px] text-text-muted">
          {usesGlobalTimeline
            ? `Full-video timing detected — timestamps are split across each planned ${usesSegmentedStudio ? 'shot' : 'section'} without changing their place in the video.`
            : `Use [00:00-00:10] descriptions to time the whole video; plain lines provide one description per planned ${usesSegmentedStudio ? 'shot' : 'section'}.`}
        </div>
      )}
      {enhancerFooter && (
        <label
          className="mobile-control-target mt-1 flex cursor-pointer items-start gap-2 rounded-md px-1 py-1 text-[10px] text-text-muted"
          title="Improve the prompt once before generation"
        >
          <input
            type="checkbox"
            checked={studioPromptEnhance}
            disabled={isEnhancing}
            onChange={event => setStudioPromptEnhance(event.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 accent-accent-blue"
          />
          <span className="min-w-0 leading-tight">
            <span className="block text-text-secondary">Improve before Generate</span>
            <span className="block break-words md:truncate">
              {usesGlobalTimeline ? 'Keeps your full-video timing and timestamps' : 'Adds detail and structure to your prompt'}
            </span>
          </span>
        </label>
      )}
      {prompt.trim() && (
        isAudioOnly ? (
          /* TTS: mode-aware split button. Main button uses default mode based
             on voice-slot count; dropdown exposes both Speech and Dialogue
             explicitly so the user can override regardless of voice count.
             Previously the dropdown labels switched with isMultiVoice, leaving
             no way to enhance into dialogue format without first adding voice
             slots — bad UX trap especially with audio_mode_from_voice_count
             models like Scenema where the user may want a generated-voice
             dialogue script as a starting point. */
          <div ref={menuRef} className={`absolute right-2 ${usesWindows ? 'bottom-7' : 'bottom-2'}`}>
            <div className="flex items-center">
              <button
                type="button"
                onClick={() => runTtsEnhancement(defaultMode)}
                disabled={isEnhancing}
                title={isMultiVoice
                  ? `Write ${voiceCount}-person dialogue (use dropdown to switch to speech)`
                  : 'Write a speech (use dropdown to switch to dialogue)'}
                aria-label={isMultiVoice
                  ? `Write ${voiceCount}-person dialogue (use dropdown to switch to speech)`
                  : 'Write a speech (use dropdown to switch to dialogue)'}
                className="mobile-control-target flex items-center justify-center rounded-l-md p-1.5 text-text-muted transition-colors hover:bg-bg-hover hover:text-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50"
              >
                {isEnhancing ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              </button>
              <button
                ref={ttsMenuTriggerRef}
                type="button"
                onClick={() => setTtsMenuOpen(!ttsMenuOpen)}
                disabled={isEnhancing}
                aria-label="Choose writing mode"
                aria-expanded={ttsMenuOpen}
                aria-controls="prompt-enhancement-menu"
                className="mobile-control-target flex items-center justify-center rounded-r-md border-l border-border p-1.5 text-text-muted transition-colors hover:bg-bg-hover hover:text-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50"
              >
                <ChevronUp size={10} />
              </button>
            </div>
            {ttsMenuOpen && (
              <div ref={ttsPopupRef} id="prompt-enhancement-menu" className="absolute bottom-full right-0 z-50 mb-1 min-w-[220px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-border bg-bg-secondary shadow-lg">
                <button
                  type="button"
                  onClick={() => runTtsEnhancement('monologue')}
                  className="mobile-control-target w-full px-3 py-2 text-left text-[11px] text-text-secondary transition-colors hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue"
                >
                  Write Speech
                  <span className="block text-[9px] text-text-muted">Single speaker, more detailed</span>
                </button>
                <button
                  type="button"
                  onClick={() => runTtsEnhancement('monologue_fast')}
                  className="mobile-control-target w-full border-t border-border px-3 py-2 text-left text-[11px] text-text-secondary transition-colors hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue"
                >
                  Write Speech
                  <span className="block text-[9px] text-text-muted">Single speaker, faster</span>
                </button>
                {supportsDialogue && (
                  <>
                    <button
                      type="button"
                      onClick={() => runTtsEnhancement('dialogue')}
                      className="mobile-control-target w-full border-t border-border px-3 py-2 text-left text-[11px] text-text-secondary transition-colors hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue"
                    >
                      {voiceCount >= 2 ? `Write ${voiceCount}-Person Dialogue` : 'Write Dialogue (2 speakers)'}
                      <span className="block text-[9px] text-text-muted">More detailed and creative</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => runTtsEnhancement('dialogue_fast')}
                      className="mobile-control-target w-full border-t border-border px-3 py-2 text-left text-[11px] text-text-secondary transition-colors hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue"
                    >
                      {voiceCount >= 2 ? `Write ${voiceCount}-Person Dialogue` : 'Write Dialogue (2 speakers)'}
                      <span className="block text-[9px] text-text-muted">Faster draft</span>
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        ) : (
          <button
            type="button"
            onClick={runEnhancement}
            disabled={isEnhancing}
            title="Improve prompt"
            aria-label="Improve prompt"
            className={`mobile-control-target absolute right-2 ${usesWindows ? 'bottom-[72px] md:bottom-12' : 'bottom-[52px] md:bottom-7'} flex items-center justify-center rounded-md p-1.5 text-text-muted transition-colors hover:bg-bg-hover hover:text-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50`}
          >
            {isEnhancing ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Sparkles size={14} />
            )}
          </button>
        )
      )}
    </div>
  )
}
