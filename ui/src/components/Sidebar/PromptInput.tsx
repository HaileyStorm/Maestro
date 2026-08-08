import { useState, useRef, useEffect } from 'react'
import { Sparkles, Loader2, ChevronUp, Brain, PenLine } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import * as api from '../../api/client'
import { controlFpsTotalFrames, effectiveSlidingWindowGeometry, globalTimelineEndSeconds, hasGlobalTimeline, usesStudioSegments } from '../../lib/timelinePrompt'

const placeholders: Record<string, string> = {
  image: 'Describe your image...',
  video: 'Describe your video...',
  audio: 'Enter text to speak or describe audio...',
  avatar: 'Describe your avatar animation...',
}

const H3_STYLE_PREF_KEY = 'maestro:h3-prepared-style'
const H3_STYLE_PREFIX_RE = /^H3 prepared style \[[^\]]+\]:[^\n]*(?:\n\n)?/
type H3PreparedStyle = { id: string; label: string; brief: string; description?: string }
const H3_PREPARED_STYLES: H3PreparedStyle[] = [
  { id: '', label: 'Unstyled · preserve my prompt', brief: '' },
  { id: 'papercraft-stop-motion-explainer', label: 'Papercraft stop-motion explainer', description: 'Tactile handmade paper explainers with layered sets, props, visual metaphors, motion, transitions, and sound.', brief: 'Tactile cut paper, layered diorama sets, handmade props, readable visual metaphors, staged stop-motion, and paper-like sound.' },
  { id: 'paper-collage-explainer-generator', label: 'Paper-collage explainer', description: 'Tactile halftone collage explainers built from approved stills and stop-motion clips.', brief: 'Halftone paper collage, tactile cutouts, abstract visual metaphors, stop-motion movement, and collage sound effects.' },
  { id: '3d-animation-short-generator', label: 'Stylized 3D animation short', description: 'Narrative 3D shorts with character, environment, shot, continuity, performance, camera, and audio planning.', brief: 'Stylized 3D narrative animation with consistent character cards, environments, performances, camera language, continuity, and sound.' },
  { id: 'minimalist-product-ad-generator', label: 'Minimalist product ad', description: 'Clean premium product shorts with concise copy, beat-synced typography, and polished camera language.', brief: 'Premium clean product film, concise on-screen copy, controlled typography, polished camera motion, and clear selling-point beats.' },
  { id: 'brand-promo-video-generator', label: 'Brand / product promo', description: 'Fact-grounded promotional shorts for products, sites, apps, shops, and personal projects.', brief: 'Fact-grounded promotional short with a clear narrative direction, capability and use-case beats, authorized assets, and a call to action.' },
  { id: 'music-video-subtitle-generator', label: 'Music video + lyric typography', description: 'Beat-aware connected music-video shots with lyric typography and long-work stitching guidance.', brief: 'Beat-reactive connected shots, spatial lyric typography, stable character and scene references, and audio-timed transitions.' },
  { id: 'co-op-game-intro-generator', label: 'Co-op game menu intro', description: 'Two-player character-led menu or opening animations with coordinated UI and interaction motion.', brief: 'Two-character game-menu opening with stable identity cues, coordinated player cards, UI copy, icons, and timed menu interaction.' },
  { id: 'handdrawn-live-video-generator', label: 'Hand-drawn + live-action fusion', description: 'Surreal shorts combining rough glowing hand-drawn animation with live-action spaces.', brief: 'Rough glowing hand-drawn animation interacting physically with live-action space, continuous morphing, and delayed handheld camera response.' },
]

function compactBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`
  return `${(value / 1024 ** 3).toFixed(1)} GiB`
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
  const [ttsMenuOpen, setTtsMenuOpen] = useState(false)
  const [h3PreparedStyle, setH3PreparedStyle] = useState(() => {
    try { return localStorage.getItem(H3_STYLE_PREF_KEY) || '' } catch { return '' }
  })
  const [availableH3Styles, setAvailableH3Styles] = useState<H3PreparedStyle[]>(H3_PREPARED_STYLES)
  const menuRef = useRef<HTMLDivElement>(null)

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
  const needsH3Guide = (generationMode === 'video' || generationMode === 'avatar')
    && String(modelOptions?.model_type || '').toLowerCase().startsWith('minimax_h3')
  useEffect(() => {
    if (!needsH3Guide) return
    let active = true
    api.fetchH3StyleWorkflows().then(catalog => {
      if (!active || !Array.isArray(catalog.styles) || catalog.styles.length === 0) return
      setAvailableH3Styles([
        H3_PREPARED_STYLES[0],
        ...catalog.styles.map(style => ({
          id: style.id,
          label: style.label,
          brief: style.prompt_brief,
          description: style.description,
        })),
      ])
    }).catch(() => { /* bundled catalog remains available offline */ })
    return () => { active = false }
  }, [needsH3Guide])
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
  const preparedStyle = availableH3Styles.find(style => style.id === h3PreparedStyle) || availableH3Styles[0]
  const applyPreparedStyle = () => {
    const authored = prompt.replace(H3_STYLE_PREFIX_RE, '').trimStart()
    const next = preparedStyle.id
      ? `H3 prepared style [${preparedStyle.label}]: ${preparedStyle.brief}\n\n${authored}`
      : authored
    setParam('prompt', next)
  }
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
      {needsH3Guide && generationMode === 'video' && (
        <div className="mb-1.5 rounded-lg border border-border bg-bg-tertiary/60 p-2">
          <div className="flex items-center gap-1.5">
            <select
              value={preparedStyle.id}
              onChange={event => {
                const value = event.target.value
                setH3PreparedStyle(value)
                try { localStorage.setItem(H3_STYLE_PREF_KEY, value) } catch { /* local preference only */ }
              }}
              className="min-w-0 flex-1 rounded border border-border bg-bg-primary px-2 py-1 text-[10px] text-text-primary"
              aria-label="H3 prepared style workflow"
            >
              {availableH3Styles.map(style => <option key={style.id || 'none'} value={style.id}>{style.label}</option>)}
            </select>
            <button type="button" onClick={applyPreparedStyle} className="rounded border border-accent-blue/40 px-2 py-1 text-[10px] text-accent-blue hover:bg-accent-blue/10">
              {preparedStyle.id ? 'Apply' : 'Remove'}
            </button>
          </div>
          <p className="mt-1 text-[9px] leading-relaxed text-text-muted">
            {preparedStyle.id
              ? preparedStyle.description || 'Official prepared visual workflow.'
              : 'No prepared workflow is added; your complete Studio prompt stays unchanged.'}{' '}
            <a href="https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills" target="_blank" rel="noreferrer" className="text-accent-blue hover:underline">Official H3 workflows</a>
          </p>
        </div>
      )}
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
