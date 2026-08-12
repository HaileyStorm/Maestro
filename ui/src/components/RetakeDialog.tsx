import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { useStore } from '../stores/useStore'
import { VideoTimelineSelector } from './shared/VideoTimelineSelector'
import * as api from '../api/client'
import { modelDisplayName } from '../lib/modelDisplay'
import { closeModalIfTop, installModalFocus } from '../lib/modalFocus'

function retakeFrameCount(frameRange: string): number | null {
  const match = /^(\d+)-(\d+)(?:\/\d+)?$/.exec(frameRange.trim())
  if (!match) return null
  const start = Number(match[1])
  const end = Number(match[2])
  return Number.isSafeInteger(start) && Number.isSafeInteger(end) && end > start
    ? end - start
    : null
}

export function RetakeDialog() {
  const titleId = useId()
  const descriptionId = useId()
  const promptId = useId()
  const negativePromptId = useId()
  const seedId = useId()
  const stepsId = useId()
  const guidanceId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const restoreFocusRef = useRef<HTMLButtonElement | null>(null)
  const submittingRef = useRef(false)
  const requestEpochRef = useRef(0)
  const successTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retakeOpen = useStore(s => s.retakeDialogOpen)
  const retakeFile = useStore(s => s.retakeSourceFile)
  const closeRetake = useStore(s => s.closeRetakeDialog)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const loadOutputs = useStore(s => s.loadOutputs)
  const [startTime, setStartTime] = useState(0)
  const [endTime, setEndTime] = useState(5)
  const [duration, setDuration] = useState(0)
  const [prompt, setPrompt] = useState('')
  const [negPrompt, setNegPrompt] = useState('')
  const [regenerateAudio, setRegenerateAudio] = useState(true)
  const [seed, setSeed] = useState(-1)
  const [steps, setSteps] = useState(8)
  const [guidance, setGuidance] = useState(1.0)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const savedVideoModel = useStore(s => s.selectedModelPerMode?.video)
  const currentModel = useStore(s => s.params.model_type)
  const modelType = savedVideoModel || currentModel
  const models = useStore(s => s.models)
  const modelLabel = modelDisplayName(modelType, models)
  const activatedLoras = useStore(s => s.params.activated_loras) as string[] || []
  const lorasMultipliers = useStore(s => s.params.loras_multipliers) as string || ''

  const closeDialog = useCallback(() => {
    requestEpochRef.current += 1
    submittingRef.current = false
    if (successTimerRef.current !== null) {
      clearTimeout(successTimerRef.current)
      successTimerRef.current = null
    }
    setSubmitting(false)
    setError(null)
    setSuccess(null)
    closeRetake()
  }, [closeRetake])
  const requestClose = useCallback(() => {
    if (!submittingRef.current) closeDialog()
  }, [closeDialog])

  // Get video duration on open
  useEffect(() => {
    if (!retakeFile) return
    const video = document.createElement('video')
    video.src = api.getFileUrl(retakeFile)
    video.onloadedmetadata = () => {
      const dur = video.duration && isFinite(video.duration) ? video.duration : 10
      setDuration(dur)
      setEndTime(dur)
      setStartTime(0)
    }
  }, [retakeFile])

  useEffect(() => {
    if (!retakeOpen || !retakeFile || !dialogRef.current || !closeRef.current) return
    restoreFocusRef.current = document.activeElement instanceof HTMLButtonElement
      ? document.activeElement
      : null
    return installModalFocus({
      document,
      dialog: dialogRef.current,
      initialFocus: closeRef.current,
      restoreFocus: restoreFocusRef.current,
      appRoot: document.getElementById('root'),
      onClose: requestClose,
      priority: 100,
    })
  }, [requestClose, retakeFile, retakeOpen])

  useEffect(() => () => {
    requestEpochRef.current += 1
    submittingRef.current = false
    if (successTimerRef.current !== null) clearTimeout(successTimerRef.current)
    successTimerRef.current = null
  }, [])

  useEffect(() => {
    if (retakeOpen) return
    requestEpochRef.current += 1
    submittingRef.current = false
    if (successTimerRef.current !== null) clearTimeout(successTimerRef.current)
    successTimerRef.current = null
    setSubmitting(false)
  }, [retakeOpen])

  if (!retakeOpen || !retakeFile) return null

  const videoUrl = api.getFileUrl(retakeFile)

  const handleSubmit = async () => {
    if (!prompt || submittingRef.current) return
    const requestEpoch = requestEpochRef.current
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    setSuccess(null)
    try {
      const result = await api.submitRetake({
        video_path: retakeFile,
        start_time: startTime,
        end_time: endTime,
        prompt: prompt || 'retake',
        model_type: modelType as string,
        negative_prompt: negPrompt,
        seed,
        guidance_scale: guidance,
        num_inference_steps: steps,
        retake_engine: 'native',
        regenerate_audio: regenerateAudio,
        activated_loras: activatedLoras,
        loras_multipliers: lorasMultipliers,
        workspace: activeWorkspace,
      })
      if (requestEpochRef.current !== requestEpoch) return
      const frameCount = retakeFrameCount(result.retake_frames)
      setSuccess(frameCount === null
        ? 'Retake queued.'
        : `Retake queued for ${frameCount} ${frameCount === 1 ? 'frame' : 'frames'}.`)
      loadOutputs()
      successTimerRef.current = setTimeout(() => {
        successTimerRef.current = null
        if (requestEpochRef.current === requestEpoch) closeDialog()
      }, 1500)
    } catch {
      if (requestEpochRef.current === requestEpoch) setError('The retake could not be queued. Try again.')
    } finally {
      if (requestEpochRef.current === requestEpoch) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center"
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
        disabled={submitting}
        aria-label="Close Retake dialog"
        className="absolute inset-0 appearance-none border-0 bg-black/60 p-0"
        onClick={() => closeModalIfTop(document, dialogRef.current, requestClose)}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="relative flex min-h-0 max-h-[calc(100vh-1.5rem)] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-border bg-bg-secondary shadow-2xl supports-[height:100dvh]:max-h-[calc(100dvh-1.5rem)] sm:max-h-[92vh]"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-sm font-semibold text-text-primary">Retake</h2>
            <p id={descriptionId} className="text-[10px] text-text-muted">Select the part you want to fix, then describe the change</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            disabled={submitting}
            onClick={() => closeModalIfTop(document, dialogRef.current, requestClose)}
            aria-label="Close Retake dialog"
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-text-muted transition-colors hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0 md:min-w-0 md:p-1.5"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain p-4 [-webkit-overflow-scrolling:touch]">
          {/* Timeline selector */}
          <VideoTimelineSelector
            videoUrl={videoUrl}
            duration={duration}
            startTime={startTime}
            endTime={endTime}
            onStartChange={setStartTime}
            onEndChange={setEndTime}
          />

          {/* Prompt */}
          <div>
            <label htmlFor={promptId} className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
              What should happen in this section?
            </label>
            <textarea id={promptId} value={prompt}
              onChange={e => setPrompt(e.target.value)}
              placeholder="Describe the new content for the selected time range..."
              rows={2}
              className="min-h-11 w-full rounded-lg border border-border bg-bg-tertiary px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
              style={{ resize: 'vertical', minHeight: 48 }} />
          </div>

          {/* Regenerate Audio */}
          <label className="flex min-h-11 cursor-pointer items-center gap-2 md:min-h-0">
            <input type="checkbox" checked={regenerateAudio}
              onChange={e => setRegenerateAudio(e.target.checked)}
              className="w-3.5 h-3.5 rounded border-border accent-accent-blue" />
            <span className="text-xs text-text-secondary">Regenerate Audio</span>
            <span className="text-[9px] text-text-muted ml-auto">
              {regenerateAudio ? 'New audio from prompt' : 'Keep source audio'}
            </span>
          </label>

          {/* Advanced toggle */}
          <button onClick={() => setShowAdvanced(!showAdvanced)}
            type="button"
            aria-expanded={showAdvanced}
            className="min-h-11 text-[10px] text-text-muted transition-colors hover:text-text-primary md:min-h-0">
            {showAdvanced ? '▾' : '▸'} Advanced
          </button>
          {showAdvanced && (
            <div className="space-y-2 pl-2 border-l border-border/50">
              <div>
                <label htmlFor={negativePromptId} className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">Negative Prompt</label>
                <input id={negativePromptId} type="text" value={negPrompt}
                  onChange={e => setNegPrompt(e.target.value)}
                  placeholder="What to avoid..."
                  className="min-h-11 w-full rounded border border-border bg-bg-tertiary px-2.5 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:border-accent-blue focus:outline-none md:min-h-0" />
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label htmlFor={seedId} className="text-[9px] text-text-muted block mb-0.5">Seed</label>
                  <input id={seedId} type="number" value={seed} onChange={e => setSeed(parseInt(e.target.value) || -1)}
                    className="min-h-11 w-full rounded border border-border bg-bg-tertiary px-1.5 py-1 text-[10px] text-text-primary focus:border-accent-blue focus:outline-none md:min-h-0" />
                </div>
                <div>
                  <label htmlFor={stepsId} className="text-[9px] text-text-muted block mb-0.5">Steps</label>
                  <input id={stepsId} type="number" min={1} max={50} value={steps} onChange={e => setSteps(parseInt(e.target.value) || 8)}
                    className="min-h-11 w-full rounded border border-border bg-bg-tertiary px-1.5 py-1 text-[10px] text-text-primary focus:border-accent-blue focus:outline-none md:min-h-0" />
                </div>
                <div>
                  <label htmlFor={guidanceId} className="text-[9px] text-text-muted block mb-0.5">Guidance</label>
                  <input id={guidanceId} type="number" min={0} max={20} step={0.1} value={guidance}
                    onChange={e => setGuidance(parseFloat(e.target.value) || 1.0)}
                    className="min-h-11 w-full rounded border border-border bg-bg-tertiary px-1.5 py-1 text-[10px] text-text-primary focus:border-accent-blue focus:outline-none md:min-h-0" />
                </div>
              </div>
            </div>
          )}

          {/* Model + LoRA info */}
          <p className="text-[9px] text-text-muted">
            <span title={modelType}>Model: {modelLabel}</span> | Engine: Native
            {activatedLoras.length > 0 && ` | LoRAs: ${activatedLoras.length}`}
          </p>

          {/* Error/Success */}
          {error && <div role="alert" className="text-[10px] text-red-400 bg-red-500/10 border border-red-500/20 rounded px-2 py-1.5">{error}</div>}
          {success && <div role="status" className="text-[10px] text-indicator-success bg-green-500/10 border border-green-500/20 rounded px-2 py-1.5">{success}</div>}

          {/* Submit */}
          <button onClick={handleSubmit} disabled={submitting || !prompt}
            type="button"
            className="min-h-11 w-full rounded-lg bg-accent-blue py-2.5 text-sm font-medium text-white transition-colors hover:bg-accent-blue/80 disabled:cursor-not-allowed disabled:opacity-40">
            {submitting ? 'Submitting...' : 'Retake'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
