import { useRef, useCallback, useEffect, useState } from 'react'
import { Upload, X } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { VideoTimelineSelector } from '../shared/VideoTimelineSelector'
import * as api from '../../api/client'

export function RetakeControls() {
  const editVideoFile = useStore(s => s.editVideoFile)
  const editVideoPath = useStore(s => s.editVideoPath)
  const editVideoUrl = useStore(s => s.editVideoUrl)
  const editVideoDuration = useStore(s => s.editVideoDuration)
  const editStartTime = useStore(s => s.editStartTime)
  const editEndTime = useStore(s => s.editEndTime)
  const editRetakeStrength = useStore(s => s.editRetakeStrength)
  const editRetakeEngine = useStore(s => s.editRetakeEngine)
  const editRegenerateAudio = useStore(s => s.editRegenerateAudio)
  const setEditVideo = useStore(s => s.setEditVideo)
  const clearEditVideo = useStore(s => s.clearEditVideo)
  const fileRef = useRef<HTMLInputElement>(null)
  const uploadEpochRef = useRef(0)
  const pendingObjectUrlRef = useRef<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const handleUpload = useCallback(async (file: File) => {
    const uploadEpoch = ++uploadEpochRef.current
    if (pendingObjectUrlRef.current) {
      URL.revokeObjectURL(pendingObjectUrlRef.current)
      pendingObjectUrlRef.current = null
    }
    setUploadError(null)
    try {
      const result = await api.uploadImage(file)
      if (uploadEpochRef.current !== uploadEpoch) return
      const url = URL.createObjectURL(file)
      pendingObjectUrlRef.current = url
      const video = document.createElement('video')
      video.src = url
      video.onloadedmetadata = () => {
        if (uploadEpochRef.current !== uploadEpoch) return
        const duration = video.duration && isFinite(video.duration) ? video.duration : 0
        const resolution = `${video.videoWidth}x${video.videoHeight}`
        pendingObjectUrlRef.current = null
        setUploadError(null)
        setEditVideo(file, result.path, url, duration, resolution)
      }
      video.onerror = () => {
        if (uploadEpochRef.current !== uploadEpoch) return
        URL.revokeObjectURL(url)
        pendingObjectUrlRef.current = null
        setUploadError('Maestro could not read that video. Choose the file again.')
      }
    } catch {
      if (uploadEpochRef.current === uploadEpoch) {
        setUploadError('The video could not be uploaded. Choose the file again.')
      }
    } finally {
      if (uploadEpochRef.current === uploadEpoch && fileRef.current) fileRef.current.value = ''
    }
  }, [setEditVideo])

  useEffect(() => () => {
    uploadEpochRef.current += 1
    if (pendingObjectUrlRef.current) URL.revokeObjectURL(pendingObjectUrlRef.current)
    pendingObjectUrlRef.current = null
  }, [])

  const handleClearVideo = useCallback(() => {
    uploadEpochRef.current += 1
    if (editVideoUrl.startsWith('blob:')) URL.revokeObjectURL(editVideoUrl)
    clearEditVideo()
  }, [clearEditVideo, editVideoUrl])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    const file = e.dataTransfer.files[0]
    if (file && file.type.startsWith('video/')) handleUpload(file)
  }, [handleUpload])

  return (
    <div className="space-y-3">
      {/* Video Upload or Timeline */}
      {!editVideoFile ? (
        <div onDragOver={e => e.preventDefault()} onDrop={handleDrop}>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="min-h-11 w-full cursor-pointer rounded-lg border-2 border-dashed border-border p-6 text-center transition-all hover:border-accent-blue/50 hover:bg-bg-hover/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
          >
            <Upload size={24} aria-hidden="true" className="mx-auto mb-2 text-text-muted" />
            <span className="block text-xs text-text-secondary">Drop a video or click to upload</span>
            <span className="mt-1 block text-[9px] text-text-muted">Select the part you want to edit, then describe the change</span>
          </button>
          <input ref={fileRef} type="file" accept="video/*" className="hidden"
            onChange={e => { if (e.target.files?.[0]) handleUpload(e.target.files[0]) }} />
        </div>
      ) : (
        <div className="relative">
          <button type="button" onClick={handleClearVideo} aria-label="Remove retake video"
            className="absolute right-1.5 top-1.5 z-20 flex min-h-11 min-w-11 items-center justify-center rounded-full bg-black/60 text-white/80 transition-colors hover:bg-black/80 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0 md:min-w-0 md:p-1">
            <X size={14} aria-hidden="true" />
          </button>
          <VideoTimelineSelector
            videoUrl={editVideoUrl}
            duration={editVideoDuration}
            startTime={editStartTime}
            endTime={editEndTime}
            onStartChange={t => useStore.setState({ editStartTime: t })}
            onEndChange={t => useStore.setState({ editEndTime: t })}
          />
          <p className="text-[9px] text-text-muted mt-1 truncate">{editVideoFile.name}</p>
        </div>
      )}

      {uploadError && (
        <div role="alert" className="rounded border border-red-500/30 bg-red-500/10 p-2 text-[10px] text-red-300">
          <p>{uploadError}</p>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="mt-1 min-h-11 rounded border border-red-400/40 px-2 font-medium text-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0"
          >
            Choose file again
          </button>
        </div>
      )}

      {/* Regenerate Audio toggle — native engine only */}
      {editRetakeEngine === 'native' && editVideoPath && (
        <label className="flex min-h-11 cursor-pointer items-center gap-2 md:min-h-0">
          <input type="checkbox" checked={editRegenerateAudio}
            onChange={e => useStore.setState({ editRegenerateAudio: e.target.checked })}
            className="w-3.5 h-3.5 rounded border-border accent-accent-blue" />
          <span className="text-[10px] text-text-secondary">Regenerate Audio</span>
          <span className="text-[9px] text-text-muted ml-auto">
            {editRegenerateAudio ? 'New audio' : 'Keep source'}
          </span>
        </label>
      )}

      {/* Strength — legacy engine only */}
      {editRetakeEngine === 'legacy' && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-[10px] text-text-muted uppercase tracking-wider">Retake Strength</label>
            <span className="text-[10px] text-text-secondary">{editRetakeStrength.toFixed(2)}</span>
          </div>
          <input type="range" aria-label="Retake strength" min={0.1} max={1} step={0.05} value={editRetakeStrength}
            onChange={e => useStore.setState({ editRetakeStrength: parseFloat(e.target.value) })} className="min-h-11 w-full md:min-h-0" />
        </div>
      )}

      {/* Engine toggle */}
      <div>
        <label className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">Retake Engine</label>
        <div role="group" aria-label="Retake engine" className="flex gap-1">
          <button type="button" aria-pressed={editRetakeEngine === 'native'} onClick={() => useStore.setState({ editRetakeEngine: 'native' })}
            className={`min-h-11 flex-1 rounded px-2 py-1.5 text-[10px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0 ${
              editRetakeEngine === 'native' ? 'bg-accent-blue text-white' : 'bg-bg-tertiary text-text-secondary hover:text-text-primary'
            }`}>
            Native
          </button>
          <button type="button" aria-pressed={editRetakeEngine === 'legacy'} onClick={() => useStore.setState({ editRetakeEngine: 'legacy' })}
            className={`min-h-11 flex-1 rounded px-2 py-1.5 text-[10px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0 ${
              editRetakeEngine === 'legacy' ? 'bg-accent-blue text-white' : 'bg-bg-tertiary text-text-secondary hover:text-text-primary'
            }`}>
            Compatibility
          </button>
        </div>
        <p className="text-[9px] text-text-muted mt-0.5">
          {editRetakeEngine === 'native'
            ? 'Lightricks denoise_mask — preserves source identity'
            : 'Classic strength-controlled mask blending'}
        </p>
      </div>
    </div>
  )
}
