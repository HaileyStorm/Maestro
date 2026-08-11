import { useState, useRef, useEffect } from 'react'
import { Sparkles, Loader2, ChevronUp, Brain, PenLine } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import * as api from '../../api/client'
import { controlFpsTotalFrames, effectiveSlidingWindowGeometry, globalTimelineEndSeconds, hasGlobalTimeline, usesStudioSegments } from '../../lib/timelinePrompt'
import { h3StyleWorkflowCatalogStateLabel, h3StyleWorkflowSupportsModel } from '../../lib/h3StyleWorkflows'

const placeholders: Record<string, string> = {
  image: 'Describe your image...',
  video: 'Describe your video...',
  audio: 'Enter text to speak or describe audio...',
  avatar: 'Describe your avatar animation...',
}

function compactBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`
  return `${(value / 1024 ** 3).toFixed(1)} GiB`
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
        <button type="button" onClick={() => void loadCatalog(true)} className="mt-1 rounded border border-amber-400/40 px-1.5 py-0.5 text-[8px]">Retry H3 catalog</button>
      </div>
    )
  }

  const selected = catalog?.styles.find(style => style.id === selection)
  const sourceRevision = catalog?.source_revision || catalog?.revision || 'unknown'
  const provenance = catalog?.provenance
  return (
    <fieldset aria-label={`${surface} H3 workflow`} className="rounded-lg border border-border bg-bg-tertiary/50 p-2">
      <legend className="px-1 text-[10px] font-medium text-text-secondary">H3 workflow</legend>
      <select
        aria-label={`${surface} H3 style workflow`}
        value={selection}
        disabled={loading}
        onChange={event => setSelection(event.target.value)}
        className="w-full rounded border border-border bg-bg-secondary px-2 py-1.5 text-[10px] text-text-primary disabled:opacity-50"
      >
        <option value="">No H3 workflow</option>
        {catalog?.styles.map(style => <option key={style.id} value={style.id}>{style.label}</option>)}
      </select>
      {selected && <p className="mt-1 text-[9px] leading-relaxed text-text-muted">{selected.description}</p>}
      <p className="mt-1 text-[9px] leading-relaxed text-text-muted">
        Official MiniMax H3 Hub/canvas workflow metadata, adapted by Maestro as server-owned guidance. This does not reproduce the complete upstream workflow, and it stays separate from Visual style.
      </p>
      <p className="mt-1 text-[8px] leading-relaxed text-text-muted">
        {catalog && <><a href={catalog.source} target="_blank" rel="noreferrer" className="text-accent-blue hover:underline">Official source</a> · </>}
        {catalog ? h3StyleWorkflowCatalogStateLabel(catalog) : 'Loading server catalog'} · source revision {sourceRevision}
        {provenance?.prompt_brief_provenance === 'maestro_adapted' ? ' · Maestro-adapted brief' : ''}
        {catalog?.update_error ? ' · last refresh unavailable' : ''}
      </p>
      {error && (
        <div className="mt-1 text-[8px] leading-relaxed text-amber-200">
          <p role="status">{error}</p>
          <button type="button" onClick={() => void loadCatalog(true)} className="mt-1 rounded border border-amber-400/40 px-1.5 py-0.5">Retry H3 catalog</button>
        </div>
      )}
    </fieldset>
  )
}

function useEnhanceStatus(
  isEnhancing: boolean,
  expectedModelId: string,
  expectedModelLabel: string,
  tracksLlm: boolean,
) {
  const [status, setStatus] = useState<{
    phase: 'loading' | 'thinking' | 'writing' | 'idle'
    chars: number
    detail?: string
  }>({ phase: 'idle', chars: 0 })

  useEffect(() => {
    if (!isEnhancing) return
    let active = true
    const initialStatusTimer = window.setTimeout(() => {
      if (active) setStatus({ phase: 'loading', chars: 0, detail: `Preparing ${expectedModelLabel}...` })
    }, 0)
    const poll = async () => {
      let streamStarted = false
      while (active) {
        try {
          if (tracksLlm && !streamStarted) {
            const llmData = await api.fetchLlmStatus()
            const loadingId = String(llmData.loading_model_id || llmData.download?.model_id || '')
            const matchesExpected = !expectedModelId || loadingId === expectedModelId
            const expectedLoaded = Boolean(llmData.loaded && (!expectedModelId || llmData.model_id === expectedModelId))
            if (!expectedLoaded || (llmData.loading && matchesExpected)) {
              const download = matchesExpected ? llmData.download : null
              const total = Number(download?.total_bytes || 0)
              const downloaded = Number(download?.downloaded_bytes || 0)
              const percent = total > 0 ? Math.min(100, Math.round(downloaded * 100 / total)) : null
              const byteProgress = downloaded > 0
                ? ` · ${compactBytes(downloaded)}${total > 0 ? ` / ${compactBytes(total)}` : ''}`
                : ''
              const percentProgress = percent != null ? ` · ${percent}%` : ''
              const filename = String(download?.filename || '').toLowerCase()
              const loadingPhase = String(llmData.loading_phase || download?.phase || '')
              let activity = `Preparing ${expectedModelLabel}`
              if (matchesExpected && (filename.includes('mmproj') || filename.includes('projector'))) {
                activity = 'Downloading vision projector'
              } else if (matchesExpected && loadingPhase === 'downloading_runtime') {
                activity = 'Downloading accelerated LLM runtime'
              } else if (matchesExpected && loadingPhase === 'building_runtime') {
                activity = 'Building accelerated LLM runtime'
              } else if (matchesExpected && loadingPhase === 'downloading') {
                activity = `Downloading ${expectedModelLabel}`
              } else if (matchesExpected && loadingPhase) {
                activity = `${loadingPhase.replaceAll('_', ' ')} · ${expectedModelLabel}`
              }
              setStatus({
                phase: 'loading',
                chars: 0,
                detail: `${activity}${byteProgress}${percentProgress}`,
              })
              await new Promise(r => setTimeout(r, 800))
              continue
            }
          }
          const res = await fetch('/api/v1/llm/stream-status')
          if (res.ok && active) {
            const data = await res.json()
            const text = (data.text || '') as string
            if (text.length > 0) streamStarted = true
            const hasThinking = text.includes('<think>') || text.includes('<thinking>')
            const thinkingClosed = text.includes('</think>') || text.includes('</thinking>')
            if (hasThinking && !thinkingClosed) {
              setStatus({ phase: 'thinking', chars: text.length, detail: expectedModelLabel })
            } else if (text.length > 0) {
              setStatus({ phase: 'writing', chars: text.length, detail: expectedModelLabel })
            } else if (!streamStarted) {
              setStatus({ phase: 'loading', chars: 0, detail: `Preparing ${expectedModelLabel}...` })
            }
            if (data.done) break
          }
        } catch { /* ignore */ }
        await new Promise(r => setTimeout(r, 800))
      }
    }
    poll()
    return () => {
      active = false
      window.clearTimeout(initialStatusTimer)
    }
  }, [expectedModelId, expectedModelLabel, isEnhancing, tracksLlm])

  return isEnhancing ? status : { phase: 'idle' as const, chars: 0 }
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
    : configuredEnhancerLabel || 'Configured Director LLM'
  const tracksEnhancerLlm = !(wangpEnhancerMode > 0 && !needsH3Guide)
  const enhanceStatus = useEnhanceStatus(
    isEnhancing,
    tracksEnhancerLlm ? configuredEnhancerId : '',
    enhancerModelLabel,
    tracksEnhancerLlm,
  )
  const enhancerFooter = !isAudioOnly
  const modePlaceholder = generationMode === 'avatar' && editSubMode === 'recast'
    ? 'Describe the finished video and replacement characters...'
    : generationMode === 'avatar' && editSubMode === 'restyle'
      ? 'Describe the finished video...'
      : (placeholders[generationMode] || 'Describe your content...')

  // Close TTS menu on outside click
  useEffect(() => {
    if (!ttsMenuOpen) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setTtsMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [ttsMenuOpen])

  // grow shrink-0: fill spare vertical space when the sidebar is roomy, but
  // never shrink below the textarea's min-height. Dropping the old
  // `flex-1 min-h-0` stops the wrapper from collapsing under the textarea
  // (which made it overflow and overlap the section below).
  return (
    <div className="relative grow shrink-0 flex flex-col">
      {generationMode === 'video' && <div className="mb-1.5"><H3StyleWorkflowField effectiveVideoModel={effectiveVideoModel} surface="Generate" /></div>}
      {/* Enhance status indicator */}
      {isEnhancing && enhanceStatus.phase !== 'idle' && (
        <div className="flex items-center gap-1.5 px-2 py-1 text-[10px] text-text-muted bg-bg-tertiary/80 rounded-t-lg border border-b-0 border-border">
          {enhanceStatus.phase === 'loading' ? (
            <>
              <Loader2 size={10} className="text-text-muted animate-spin" />
              <span>{enhanceStatus.detail || `Preparing ${enhancerModelLabel}...`}</span>
            </>
          ) : enhanceStatus.phase === 'thinking' ? (
            <>
              <Brain size={10} className="text-chip-purple animate-pulse" />
              <span>{`Thinking with ${enhanceStatus.detail || enhancerModelLabel}...`}</span>
            </>
          ) : (
            <>
              <PenLine size={10} className="text-accent-blue animate-pulse" />
              <span>{`Writing with ${enhanceStatus.detail || enhancerModelLabel}...`}</span>
            </>
          )}
        </div>
      )}
      <textarea
        value={prompt}
        onChange={e => setParam('prompt', e.target.value)}
        placeholder={usesWindows
          ? `Describe the whole video; add [00:00-00:10] timed beats for ${windowCount} automatic windows`
          : modePlaceholder}
        className="w-full flex-1 bg-bg-tertiary border border-border rounded-lg px-3 py-2 pr-10 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors"
        style={{ resize: 'none', minHeight: 112 }}
      />
      {usesWindows && (
        <div className="px-1 pt-1 text-[10px] text-text-muted">
          {usesGlobalTimeline
            ? `Global timeline detected — timestamps will be clipped and rebased automatically per ${usesSegmentedStudio ? 'segment' : 'window'}.`
            : `Use [00:00-00:10] descriptions for one global timeline; plain lines remain one prompt per ${usesSegmentedStudio ? 'segment' : 'window'}.`}
        </div>
      )}
      {enhancerFooter && (
        <label
          className="mt-1 flex cursor-pointer items-start gap-2 rounded-md px-1 py-1 text-[10px] text-text-muted"
          title={`Enhance once before generation with ${enhancerModelLabel}`}
        >
          <input
            type="checkbox"
            checked={studioPromptEnhance}
            disabled={isEnhancing}
            onChange={event => setStudioPromptEnhance(event.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 accent-accent-blue"
          />
          <span className="min-w-0 leading-tight">
            <span className="block text-text-secondary">Enhance before Generate</span>
            <span className="block truncate">
              {usesGlobalTimeline ? `Model: ${enhancerModelLabel} · global timeline is preserved as authored (timestamps locked)` : `Model: ${enhancerModelLabel}`}
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
                onClick={() => enhancePrompt(defaultMode)}
                disabled={isEnhancing}
                title={isMultiVoice
                  ? `Write ${voiceCount}-person dialogue (use dropdown to switch to speech)`
                  : 'Write a speech (use dropdown to switch to dialogue)'}
                className="p-1.5 rounded-l-md text-text-muted hover:text-accent-blue hover:bg-bg-hover transition-colors disabled:opacity-50"
              >
                {isEnhancing ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              </button>
              <button
                onClick={() => setTtsMenuOpen(!ttsMenuOpen)}
                disabled={isEnhancing}
                className="p-1.5 rounded-r-md text-text-muted hover:text-accent-blue hover:bg-bg-hover transition-colors disabled:opacity-50 border-l border-border"
              >
                <ChevronUp size={10} />
              </button>
            </div>
            {ttsMenuOpen && (
              <div className="absolute bottom-full right-0 mb-1 bg-bg-secondary border border-border rounded-lg shadow-lg overflow-hidden min-w-[220px] z-50">
                <button
                  onClick={() => { setTtsMenuOpen(false); enhancePrompt('monologue') }}
                  className="w-full text-left px-3 py-2 text-[11px] text-text-secondary hover:bg-bg-hover transition-colors"
                >
                  Write Speech
                  <span className="block text-[9px] text-text-muted">Single speaker, with thinking</span>
                </button>
                <button
                  onClick={() => { setTtsMenuOpen(false); enhancePrompt('monologue_fast') }}
                  className="w-full text-left px-3 py-2 text-[11px] text-text-secondary hover:bg-bg-hover transition-colors border-t border-border"
                >
                  Write Speech
                  <span className="block text-[9px] text-text-muted">Single speaker, faster</span>
                </button>
                {supportsDialogue && (
                  <>
                    <button
                      onClick={() => { setTtsMenuOpen(false); enhancePrompt('dialogue') }}
                      className="w-full text-left px-3 py-2 text-[11px] text-text-secondary hover:bg-bg-hover transition-colors border-t border-border"
                    >
                      {voiceCount >= 2 ? `Write ${voiceCount}-Person Dialogue` : 'Write Dialogue (2 speakers)'}
                      <span className="block text-[9px] text-text-muted">With thinking — more creative</span>
                    </button>
                    <button
                      onClick={() => { setTtsMenuOpen(false); enhancePrompt('dialogue_fast') }}
                      className="w-full text-left px-3 py-2 text-[11px] text-text-secondary hover:bg-bg-hover transition-colors border-t border-border"
                    >
                      {voiceCount >= 2 ? `Write ${voiceCount}-Person Dialogue` : 'Write Dialogue (2 speakers)'}
                      <span className="block text-[9px] text-text-muted">No thinking — faster</span>
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        ) : (
          <button
            onClick={() => enhancePrompt()}
            disabled={isEnhancing}
            title="Enhance prompt with AI"
            className={`absolute right-2 ${usesWindows ? 'bottom-12' : 'bottom-7'} p-1.5 rounded-md text-text-muted hover:text-accent-blue hover:bg-bg-hover transition-colors disabled:opacity-50`}
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
