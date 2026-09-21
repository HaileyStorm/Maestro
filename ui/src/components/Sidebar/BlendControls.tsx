import { useState, useRef, useCallback, useEffect } from 'react'
import { X, Film, ArrowRight } from 'lucide-react'
import { currentAccountIdentityEpoch, useStore } from '../../stores/useStore'
import * as api from '../../api/client'
import { BLEND_MEDIA_ACCEPT, isBlendMediaFile, isBlendVideoFile } from './blendMediaTypes'

function ClipDropZone({ label, file, url, duration, sourceName, onUpload, onClear }: {
  label: string
  file: File | null
  url: string
  duration: number
  sourceName: string
  onUpload: (file: File) => void
  onClear: () => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const isVideo = file ? isBlendVideoFile(file) : false

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (f) onUpload(f)
  }, [onUpload])

  if (file) {
    return (
      <div className="relative rounded-lg overflow-hidden border border-border flex-1">
        {isVideo
          ? <video src={url} className="w-full h-16 object-cover" muted />
          : <img src={url} className="w-full h-16 object-cover" alt="" />
        }
        <button
          onClick={onClear}
          className="absolute top-0.5 right-0.5 p-0.5 rounded-full bg-black/60 text-white hover:bg-red-600 transition-colors"
        >
          <X size={10} />
        </button>
        <div className="absolute bottom-0.5 left-0.5 text-[8px] bg-black/60 text-white px-1 py-0.5 rounded">
          {label}{duration > 0 ? ` ${duration.toFixed(1)}s` : ''}
        </div>
      </div>
    )
  }

  return (
    <div
      onDragOver={e => e.preventDefault()}
      onDrop={handleDrop}
      onClick={() => fileRef.current?.click()}
      className="flex-1 border-2 border-dashed border-border rounded-lg p-3 text-center cursor-pointer hover:border-accent-blue transition-colors"
    >
      <Film size={14} className="mx-auto mb-1 text-text-muted" />
      <p className="text-[10px] text-text-secondary">{label}</p>
      {sourceName && <p className="mt-0.5 truncate text-[9px] text-indicator-warning">Reattach {sourceName}</p>}
      <input
        ref={fileRef}
        type="file"
        accept={BLEND_MEDIA_ACCEPT}
        className="hidden"
        onChange={e => {
          const f = e.target.files?.[0]
          e.currentTarget.value = ''
          if (f) onUpload(f)
        }}
      />
    </div>
  )
}

export function BlendControls() {
  const blendClipA = useStore(s => s.blendClipA)
  const blendClipAUrl = useStore(s => s.blendClipAUrl)
  const blendClipADuration = useStore(s => s.blendClipADuration)
  const blendClipASourceName = useStore(s => s.blendClipASourceName)
  const blendClipB = useStore(s => s.blendClipB)
  const blendClipBUrl = useStore(s => s.blendClipBUrl)
  const blendClipBDuration = useStore(s => s.blendClipBDuration)
  const blendClipBSourceName = useStore(s => s.blendClipBSourceName)
  const setBlendClipA = useStore(s => s.setBlendClipA)
  const setBlendClipB = useStore(s => s.setBlendClipB)
  const clearBlendClipA = useStore(s => s.clearBlendClipA)
  const clearBlendClipB = useStore(s => s.clearBlendClipB)
  const transitionSec = useStore(s => s.blendTransitionSec)
  const setTransitionSec = useStore(s => s.setBlendTransitionSec)
  const blendMode = useStore(s => s.blendMode)
  const setBlendMode = useStore(s => s.setBlendMode)
  const overlapSec = useStore(s => s.blendOverlapSec)
  const setOverlapSec = useStore(s => s.setBlendOverlapSec)
  const motionPrefixSec = useStore(s => s.blendMotionPrefixSec)
  const setMotionPrefixSec = useStore(s => s.setBlendMotionPrefixSec)
  const motionSuffixSec = useStore(s => s.blendMotionSuffixSec)
  const setMotionSuffixSec = useStore(s => s.setBlendMotionSuffixSec)
  const anchorStrength = useStore(s => s.blendAnchorStrength)
  const setAnchorStrength = useStore(s => s.setBlendAnchorStrength)
  const ensureTransitionLoraForBlend = useStore(s => s.ensureTransitionLoraForBlend)

  // On blend mode mount, make sure the LTX-2.3 transition LoRA is installed
  // and active. Idempotent — fires once per mount; the helper itself no-ops
  // if the LoRA is already installed + activated.
  useEffect(() => {
    void ensureTransitionLoraForBlend()
  }, [ensureTransitionLoraForBlend])

  const [error, setError] = useState<string | null>(null)
  const uploadSequence = useRef({ A: 0, B: 0 })
  const pendingVideo = useRef<Partial<Record<'A' | 'B', () => void>>>({})

  useEffect(() => {
    const scope = () => {
      const state = useStore.getState()
      return JSON.stringify([currentAccountIdentityEpoch(), state.activeWorkspace,
        state.generationMode, state.params.image_mode, state.selectedOutput,
        state.selectedOutputMetaName, state.blendRestoreSourceKey])
    }
    const invalidate = () => {
      for (const target of ['A', 'B'] as const) {
        uploadSequence.current[target]++
        pendingVideo.current[target]?.()
      }
    }
    let previous = scope()
    // Observe each transition, including leaving and returning to one project
    // before React renders again or a pending upload finishes.
    const unsubscribe = useStore.subscribe(() => {
      const next = scope()
      if (next === previous) return
      previous = next
      invalidate()
      setError(null)
    })
    return () => { unsubscribe(); invalidate() }
  }, [])

  const uploadClip = useCallback(async (file: File, target: 'A' | 'B') => {
    const sequence = ++uploadSequence.current[target]
    pendingVideo.current[target]?.()
    const workspace = useStore.getState().activeWorkspace
    const accountEpoch = currentAccountIdentityEpoch()
    const current = () => {
      const state = useStore.getState()
      return sequence === uploadSequence.current[target]
        && accountEpoch === currentAccountIdentityEpoch()
        && state.activeWorkspace === workspace
        && state.generationMode === 'video' && state.params.image_mode === 4
    }
    setError(null)
    if (!isBlendMediaFile(file)) {
      setError('Choose a PNG, JPEG, WebP, BMP, TIFF, MP4, MKV, AVI, MOV, or WebM file.')
      return
    }
    try {
      const result = await api.uploadImage(file)
      if (!current()) return
      const url = URL.createObjectURL(file)
      if (isBlendVideoFile(file)) {
        const video = document.createElement('video')
        let finished = false
        let timer = 0
        const cleanup = (retainUrl = false) => {
          if (finished) return
          finished = true
          window.clearTimeout(timer)
          video.onloadedmetadata = null
          video.onerror = null
          video.removeAttribute('src')
          video.load()
          if (!retainUrl) URL.revokeObjectURL(url)
          if (pendingVideo.current[target] === abort) delete pendingVideo.current[target]
        }
        const abort = () => cleanup()
        const fail = () => {
          if (finished) return
          cleanup()
          if (current()) setError('Could not read this video. Try another file.')
        }
        pendingVideo.current[target] = abort
        video.onloadedmetadata = () => {
          if (finished) return
          if (!current()) { cleanup(); return }
          const duration = video.duration
          if (!Number.isFinite(duration) || duration <= 0) { fail(); return }
          cleanup(true)
          if (target === 'A') {
            setBlendClipA(file, result.path, url, duration)
          } else {
            setBlendClipB(file, result.path, url, duration)
          }
        }
        video.onerror = fail
        video.preload = 'metadata'
        timer = window.setTimeout(fail, 15_000)
        video.src = url
      } else {
        if (target === 'A') {
          setBlendClipA(file, result.path, url, 0)
        } else {
          setBlendClipB(file, result.path, url, 0)
        }
      }
    } catch {
      if (current()) setError('Upload failed. Try again.')
    }
  }, [setBlendClipA, setBlendClipB])

  const bothLoaded = !!blendClipA && !!blendClipB

  return (
    <div className="space-y-3">
      {/* Overlap / Insert toggle — first choice, above the clip drop zones */}
      <div>
        <div className="flex bg-bg-tertiary rounded-lg p-0.5 border border-border">
          <button
            onClick={() => setBlendMode('overlap')}
            className={`flex-1 text-[10px] py-1.5 rounded-md transition-all ${
              blendMode === 'overlap' ? 'bg-bg-active text-text-primary' : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            Overlap
          </button>
          <button
            onClick={() => setBlendMode('insert')}
            className={`flex-1 text-[10px] py-1.5 rounded-md transition-all ${
              blendMode === 'insert' ? 'bg-bg-active text-text-primary' : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            Insert
          </button>
        </div>
        <p className="text-[9px] text-text-muted mt-1">
          {blendMode === 'insert'
            ? 'Adds new footage between clips. Total duration increases.'
            : 'Replaces the end of A and start of B with one transition, shortening the total by the overlap.'}
        </p>
      </div>

      {/* Clip A → Arrow → Clip B */}
      <div className="flex items-center gap-1.5">
        <ClipDropZone
          label="Clip A"
          file={blendClipA}
          url={blendClipAUrl}
          duration={blendClipADuration}
          sourceName={blendClipASourceName}
          onUpload={f => uploadClip(f, 'A')}
          onClear={() => {
            uploadSequence.current.A++
            pendingVideo.current.A?.()
            clearBlendClipA()
          }}
        />
        <ArrowRight size={16} className="text-text-muted shrink-0" />
        <ClipDropZone
          label="Clip B"
          file={blendClipB}
          url={blendClipBUrl}
          duration={blendClipBDuration}
          sourceName={blendClipBSourceName}
          onUpload={f => uploadClip(f, 'B')}
          onClear={() => {
            uploadSequence.current.B++
            pendingVideo.current.B?.()
            clearBlendClipB()
          }}
        />
      </div>

      {error && <p className="text-[10px] text-red-400">{error}</p>}

      {!blendClipA && !blendClipB && (blendClipASourceName || blendClipBSourceName) && (
        <p className="text-[10px] text-indicator-warning">
          Saved settings are loaded. Reattach the original sources; Maestro does not reuse a file by name alone.
        </p>
      )}

      {/* Transition duration (Insert mode) */}
      {blendMode === 'insert' && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-[10px] text-text-muted">Transition Duration</label>
            <span className="text-[10px] text-text-secondary">{transitionSec}s</span>
          </div>
          <input
            type="range"
            min={2} max={10} step={1}
            value={transitionSec}
            onChange={e => setTransitionSec(parseInt(e.target.value))}
            className="w-full"
          />
        </div>
      )}

      {/* Overlap duration (Overlap mode) */}
      {blendMode === 'overlap' && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-[10px] text-text-muted">Overlap Duration</label>
            <span className="text-[10px] text-text-secondary">{overlapSec}s</span>
          </div>
          <input
            type="range"
            min={1} max={Math.min(8, Math.floor(Math.min(blendClipADuration || 99, blendClipBDuration || 99) / 2))}
            step={1}
            value={overlapSec}
            onChange={e => setOverlapSec(parseInt(e.target.value))}
            className="w-full"
          />
          <p className="text-[9px] text-text-muted mt-0.5">
            Trims {overlapSec}s from the end of A and start of B, generates a {overlapSec}s replacement.
          </p>
        </div>
      )}

      {/* Motion continuity + anchor strength (Overlap mode only) */}
      {bothLoaded && blendMode === 'overlap' && (
        <div className="space-y-2">
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-[10px] text-text-muted">Motion Prefix</label>
              <span className="text-[10px] text-text-secondary">{motionPrefixSec.toFixed(2)}s</span>
            </div>
            <input
              type="range"
              min={0} max={Math.min(3, Math.floor(overlapSec * 0.7))} step="any"
              value={motionPrefixSec}
              onChange={e => setMotionPrefixSec(parseFloat(e.target.value))}
              className="w-full"
            />
            <p className="text-[9px] text-text-muted mt-0.5">
              {motionPrefixSec === 0
                ? 'Pure start+end mode — no motion carried from Clip A'
                : `First ${motionPrefixSec.toFixed(2)}s of blend replays Clip A's tail so rotation/pan carries through`}
            </p>
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-[10px] text-text-muted">Motion Suffix</label>
              <span className="text-[10px] text-text-secondary">{motionSuffixSec.toFixed(2)}s</span>
            </div>
            <input
              type="range"
              min={0} max={Math.min(3, Math.floor(overlapSec * 0.7))} step="any"
              value={motionSuffixSec}
              onChange={e => setMotionSuffixSec(parseFloat(e.target.value))}
              className="w-full"
            />
            <p className="text-[9px] text-text-muted mt-0.5">
              {motionSuffixSec === 0
                ? 'Single end-frame anchor — model may slow-mo into the landing'
                : `Last ${motionSuffixSec.toFixed(2)}s of blend previews Clip B's head so motion lands at real speed`}
            </p>
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-[10px] text-text-muted">Anchor Strength</label>
              <span className="text-[10px] text-text-secondary">{anchorStrength.toFixed(2)}</span>
            </div>
            <input
              type="range"
              min={0.3} max={1.0} step={0.05}
              value={anchorStrength}
              onChange={e => setAnchorStrength(parseFloat(e.target.value))}
              className="w-full"
            />
            <p className="text-[9px] text-text-muted mt-0.5">
              Higher = tighter lock to Clip A/B endpoints (may crossfade).
              Lower = AI invents more motion between them (may drift).
              Start at 0.7.
            </p>
          </div>
        </div>
      )}

      {bothLoaded && (
        <div className="text-[10px] text-text-muted text-center bg-bg-tertiary rounded-lg px-2 py-1.5 border border-border/50">
          {blendMode === 'insert'
            ? `Output: Clip A + ${transitionSec}s transition + Clip B`
            : `Output: Clip A (−${overlapSec}s) + ${overlapSec}s transition + Clip B (−${overlapSec}s)`
          }
        </div>
      )}

      {!bothLoaded && (blendClipA || blendClipB) && (
        <p className="text-[10px] text-indicator-warning text-center">Add both clips to enable blending</p>
      )}
    </div>
  )
}
