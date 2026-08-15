import { useState, useRef, useEffect } from 'react'
import { Sparkles, Loader2, ChevronUp, Brain, PenLine } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { controlFpsTotalFrames, effectiveSlidingWindowGeometry, globalTimelineEndSeconds, hasGlobalTimeline, usesStudioSegments } from '../../lib/timelinePrompt'
import { h3StyleWorkflowCatalogStateLabel, h3StyleWorkflowSupportsModel } from '../../lib/h3StyleWorkflows'

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
    if (!error || loading) return null
    return (
      <div className="rounded border border-amber-400/30 bg-amber-400/5 px-2 py-1 text-[9px] leading-relaxed text-amber-200">
        <p role="status">{error}</p>
        <button type="button" onClick={() => void loadCatalog(true)} className="mobile-control-target mt-1 rounded border border-amber-400/40 px-2 py-0.5 text-[8px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">Retry creative guides</button>
      </div>
    )
  }

  const selected = catalog?.styles.find(style => style.id === selection)
  const sourceRevision = catalog?.source_revision || catalog?.revision || 'unknown'
  const provenance = catalog?.provenance
  return (
    <fieldset aria-label={`${surface} creative guide`} className="rounded-lg border border-border bg-bg-tertiary/50 p-2">
      <legend className="px-1 text-[10px] font-medium text-text-secondary">Creative guide</legend>
      <select
        aria-label={`${surface} creative guide`}
        value={selection}
        disabled={loading}
        onChange={event => setSelection(event.target.value)}
        className="mobile-control-target w-full rounded border border-border bg-bg-secondary px-2 py-1.5 text-[10px] text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50"
      >
        <option value="">Follow my prompt without a guide</option>
        {catalog?.styles.map(style => <option key={style.id} value={style.id}>{style.label}</option>)}
      </select>
      {selected && <p className="mt-1 text-[9px] leading-relaxed text-text-muted">{selected.description}</p>}
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
  const enhanceStatus = useStore(s => s.enhanceStatus)
  const cancelEnhancePrompt = useStore(s => s.cancelEnhancePrompt)
  const durationSeconds = useStore(s => s.durationSeconds)
  const slidingWindowSeconds = useStore(s => s.slidingWindowSeconds)
  const slidingWindowOverlap = useStore(s => s.slidingWindowOverlap)
  const modelOptions = useStore(s => s.modelOptions)
  const guideVideoFps = useStore(s => s.guideVideoFps)
  const guideVideoFrameCount = useStore(s => s.guideVideoFrameCount)
  const forceFps = useStore(s => s.params.force_fps)
  const videoGuide = useStore(s => s.params.video_guide)
  const studioPromptEnhance = useStore(s => s.studioPromptEnhance)
  const setStudioPromptEnhance = useStore(s => s.setStudioPromptEnhance)
  const servicesConfig = useStore(s => s.servicesConfig)
  const systemConfig = useStore(s => s.systemConfig)
  const llmModels = useStore(s => s.llmModels)
  const imageMode = useStore(s => s.params.image_mode)
  const effectiveVideoModel = useStore(s => s.params.model_type)
  const h3StyleWorkflowCatalog = useStore(s => s.h3StyleWorkflowCatalog)
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
  const enhancerModeLabels: Record<number, string> = {
    1: 'Llama 3.2 + Florence2',
    2: 'LlamaJoy + Florence2',
    3: 'Qwen3.5 4B Abliterated',
    4: 'Qwen3.5 9B Abliterated',
  }
  const needsH3Guide = generationMode === 'video'
    && h3StyleWorkflowSupportsModel(h3StyleWorkflowCatalog, effectiveVideoModel)
  const wangpEnhancerMode = Number(systemConfig?.enhancer_enabled || 0)
  const dedicatedEnhancerId = modelOptions?.prompt_enhancer_model || servicesConfig?.enhance_llm_model_id || ''
  const configuredEnhancerId = dedicatedEnhancerId || servicesConfig?.llm_model_id || ''
  const configuredEnhancerLabel = llmModels.find(model => model.id === configuredEnhancerId)?.label || configuredEnhancerId
  const enhancerModelLabel = wangpEnhancerMode > 0 && !needsH3Guide
    ? enhancerModeLabels[wangpEnhancerMode] || `Wan2GP enhancer mode ${wangpEnhancerMode}`
    : configuredEnhancerLabel || 'Writing assistant'
  const routeEnhanceStatus = enhanceStatus && 'request_id' in enhanceStatus
    ? enhanceStatus
    : null
  const enhancePhase = String(enhanceStatus?.phase || 'loading')
  const enhanceIsThinking = enhancePhase === 'thinking' || enhancePhase === 'detecting'
  const enhanceIsWriting = Boolean(routeEnhanceStatus?.partial_text)
    || ['prefill', 'inference', 'generating', 'finalizing', 'retrying'].includes(enhancePhase)
  const enhanceStageLabels: Record<string, string> = {
    queued: 'Queued',
    loading: 'Preparing writing tools',
    wangp: 'Drafting with the prompt enhancer',
    llm: 'Drafting with your writing assistant',
    llm_fallback: 'Continuing with your writing assistant',
    inference: 'Writing your revision',
  }
  const enhanceStage = routeEnhanceStatus
    ? enhanceStageLabels[routeEnhanceStatus.stage]
      || enhanceStageLabels[routeEnhanceStatus.phase]
      || 'Writing your revision'
    : enhanceStatus?.phase === 'ready'
      ? 'Writing your revision'
      : `Preparing ${enhancerModelLabel}`
  const enhanceTps = routeEnhanceStatus?.live_tps ?? routeEnhanceStatus?.average_tps
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
    enhancePrompt(mode)
  }

  // grow shrink-0: fill spare vertical space when the sidebar is roomy, but
  // never shrink below the textarea's min-height. Dropping the old
  // `flex-1 min-h-0` stops the wrapper from collapsing under the textarea
  // (which made it overflow and overlap the section below).
  return (
    <div className="relative grow shrink-0 flex flex-col">
      {generationMode === 'video' && <div className="mb-1.5"><H3StyleWorkflowField effectiveVideoModel={effectiveVideoModel} surface="Generate" /></div>}
      {/* Enhance status indicator */}
      {isEnhancing && (
        <div className="flex min-h-11 items-center gap-2 rounded-t-lg border border-b-0 border-border bg-bg-tertiary/80 px-2 py-1 text-[10px] text-text-muted">
          {!enhanceIsThinking && !enhanceIsWriting ? (
            <>
              <Loader2 size={10} className="text-text-muted animate-spin" />
              <span>{enhanceStage}…</span>
            </>
          ) : enhanceIsThinking ? (
            <>
              <Brain size={10} className="text-chip-purple animate-pulse" />
              <span>Working on your prompt · {enhanceStage}…</span>
            </>
          ) : (
            <>
              <PenLine size={10} className="text-accent-blue animate-pulse" />
              <span>{enhanceStage}…</span>
            </>
          )}
          {enhanceTps != null && (
            <span className="whitespace-nowrap tabular-nums">{enhanceTps.toFixed(1)} tok/s</span>
          )}
          <button
            type="button"
            onClick={() => void cancelEnhancePrompt()}
            className="mobile-control-target ml-auto inline-flex min-h-11 shrink-0 items-center rounded px-2 text-[10px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            aria-label="Cancel prompt enhancement"
          >
            Cancel
          </button>
        </div>
      )}
      {isEnhancing && routeEnhanceStatus?.partial_text && (
        <div
          role="status"
          aria-live="polite"
          className="line-clamp-2 border-x border-border bg-bg-tertiary/60 px-2 py-1 text-[10px] leading-relaxed text-text-secondary"
        >
          {routeEnhanceStatus.partial_text}
        </div>
      )}
      <textarea
        value={prompt}
        onChange={e => setParam('prompt', e.target.value)}
        placeholder={usesWindows
          ? `Describe the whole video; add [00:00-00:10] timed beats for ${windowCount} planned ${usesSegmentedStudio ? 'shots' : 'sections'}`
          : modePlaceholder}
        className="w-full flex-1 rounded-lg border border-border bg-bg-tertiary px-3 py-2 pr-14 text-sm text-text-primary placeholder:text-text-muted transition-colors focus:border-accent-blue focus:outline-none md:pr-10"
        style={{ resize: 'none', minHeight: 112 }}
      />
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
                onClick={() => enhancePrompt(defaultMode)}
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
            onClick={() => enhancePrompt()}
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
