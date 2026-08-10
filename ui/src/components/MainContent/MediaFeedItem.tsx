import { useState, useRef, useEffect, useCallback, type CSSProperties, type KeyboardEvent } from 'react'
import { Play, Pencil, RefreshCw, Copy, Trash2, Check, Combine, Loader2, Heart, ArrowLeftToLine, Download, FolderInput, Scissors, FastForward, BookMarked, EyeOff, Share2, Link2Off } from 'lucide-react'
import { SaveRecipeDialog } from '../Recipes/SaveRecipeDialog'
import { useStore } from '../../stores/useStore'
import { createOutputShare, deleteOutputComponents, getUploadUrl, fetchOutputMetadata, getFileUrl, moveOutput, revokeOutputShare, uploadImage } from '../../api/client'
import type { OutputFile, OutputMetadata } from '../../types'
import { modelDisplayName } from '../../lib/modelDisplay'
import {
  hidePrivatePreview as forgetPrivatePreviewReveal,
  privatePreviewIdentity,
  privatePreviewWasRevealed,
  revealPrivatePreview as rememberPrivatePreviewReveal,
  subscribePrivatePreviewReveal,
} from '../../lib/privatePreview'

interface Props {
  file: OutputFile
  index: number
  isActive: boolean
  onVisible: (index: number) => void
  measurementEpoch: number
  onMeasured: (identity: string, epoch: number, height: number) => void
  style?: CSSProperties
}

/** Image component that retries loading if the file isn't fully written yet.
 *
 * Backstops the backend's atomic image-write guarantee in two ways:
 *   1. onError — fires when the request fails outright (404 during the
 *      tiny window between job-complete signal and file existence).
 *   2. onLoad with naturalWidth === 0 — fires when the backend returned
 *      bytes the browser couldn't decode (truncated/corrupt body that
 *      still produced a 200 OK with matching Content-Length). The
 *      browser silently shows an empty box in this case; without the
 *      check the user sees a half-image and feels they need to refresh
 *      the page (which loses Studio prompts/settings/reference images).
 */
function RetryImage({ url, alt }: { url: string; alt: string }) {
  const [src, setSrc] = useState(url)
  const retries = useRef(0)
  const maxRetries = 5

  const scheduleRetry = useCallback(() => {
    if (retries.current < maxRetries) {
      retries.current++
      setTimeout(() => {
        setSrc(`${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`)
      }, 800 * retries.current)
    }
  }, [url])

  const handleError = useCallback(() => {
    scheduleRetry()
  }, [scheduleRetry])

  const handleLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    // Truncated body that decoded to nothing — browser fired onLoad
    // (Content-Length matched) but produced a 0×0 image. Treat as
    // failure and retry with a cache-busted URL.
    const img = e.currentTarget
    if (img.naturalWidth === 0 || img.naturalHeight === 0) {
      scheduleRetry()
    }
  }, [scheduleRetry])

  return (
    <img
      key={src}
      src={src}
      alt={alt}
      className="w-full h-full object-contain"
      onError={handleError}
      onLoad={handleLoad}
    />
  )
}

export function MediaFeedItem({ file, index, isActive, onVisible, measurementEpoch, onMeasured, style }: Props) {
  const setSelectedOutput = useStore(s => s.setSelectedOutput)
  const loadSettingsFromOutput = useStore(s => s.loadSettingsFromOutput)
  const rerollGeneration = useStore(s => s.rerollGeneration)
  const deleteOutput = useStore(s => s.deleteSelectedOutput)
  const rejoinClipGroup = useStore(s => s.rejoinClipGroup)
  const toggleFavorite = useStore(s => s.toggleFavorite)
  const setStartImage = useStore(s => s.setStartImage)
  const addImageRef = useStore(s => s.addImageRef)
  const setContinueVideo = useStore(s => s.setContinueVideo)
  const setParam = useStore(s => s.setParam)
  const openRetakeDialog = useStore(s => s.openRetakeDialog)
  const generationMode = useStore(s => s.generationMode)
  const workspaces = useStore(s => s.workspaces)
  const accessContext = useStore(s => s.accessContext)
  // Virtual Uploads view: browse-only. Move/favorite/delete resolve
  // against the active OUTPUT workspace server-side, so they can't act
  // on upload files — hide them. Download + send-to-input still work
  // (serve_file falls back to the uploads folder).
  const browsingUploads = useStore(s => s.browsingUploads)
  // Used to translate the raw model_type slug (e.g.
  // "ltx2_22B_distilled_1_1") in the per-clip metadata bar into the
  // human-readable display name (e.g. "LTX-2.3 Distilled 1.1 22B")
  // via modelDisplayName().
  const models = useStore(s => s.models)
  const gallerySelectionMode = useStore(s => s.gallerySelectionMode)
  const selectedOutputKeys = useStore(s => s.selectedOutputKeys)
  const toggleOutputSelection = useStore(s => s.toggleOutputSelection)

  const saveRecipeFromOutput = useStore(s => s.saveRecipeFromOutput)

  const [meta, setMeta] = useState<OutputMetadata | null>(null)
  const [metaLoaded, setMetaLoaded] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [confirmCleanup, setConfirmCleanup] = useState(false)
  const [cleaningComponents, setCleaningComponents] = useState(false)
  const [cleanupError, setCleanupError] = useState('')
  const [showSaveRecipe, setShowSaveRecipe] = useState(false)
  const confirmRef = useRef(false)
  const timeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined)
  const [copied, setCopied] = useState(false)
  const [rejoining, setRejoining] = useState(false)
  const [sentToInput, setSentToInput] = useState(false)
  const [showMoveMenu, setShowMoveMenu] = useState(false)
  const [moving, setMoving] = useState(false)
  const privateRevealKey = privatePreviewIdentity(file.workspace, file.name, file.revision)
  const [revealedPrivateKey, setRevealedPrivateKey] = useState(() =>
    file.private && privatePreviewWasRevealed(privateRevealKey) ? privateRevealKey : '',
  )
  const privateRevealed = file.private && revealedPrivateKey === privateRevealKey
  const privateBlurred = file.private && !privateRevealed
  const [shareUrl, setShareUrl] = useState('')
  const [sharing, setSharing] = useState(false)
  const [shareMessage, setShareMessage] = useState('')
  const moveRef = useRef<HTMLDivElement>(null)
  const itemRef = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)

  const releaseVideoSource = useCallback((video: HTMLVideoElement | null) => {
    if (!video) return
    video.pause()
    video.removeAttribute('src')
    video.load()
  }, [])

  const setVideoElement = useCallback((video: HTMLVideoElement | null) => {
    const previous = videoRef.current
    if (previous && previous !== video) releaseVideoSource(previous)
    videoRef.current = video
  }, [releaseVideoSource])

  useEffect(() => () => {
    clearTimeout(timeoutRef.current)
  }, [])

  useEffect(() => {
    const syncReveal = (revealed = privatePreviewWasRevealed(privateRevealKey)) => {
      setRevealedPrivateKey(file.private && revealed ? privateRevealKey : '')
    }
    syncReveal()
    return subscribePrivatePreviewReveal(privateRevealKey, syncReveal)
  }, [file.private, privateRevealKey])

  const revealPrivatePreview = () => {
    rememberPrivatePreviewReveal(privateRevealKey)
    setRevealedPrivateKey(privateRevealKey)
  }

  const hidePrivatePreview = () => {
    forgetPrivatePreviewReveal(privateRevealKey)
    setRevealedPrivateKey('')
  }

  // Measure actual height and report to parent
  useEffect(() => {
    const el = itemRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      const height = entries[0].borderBoxSize?.[0]?.blockSize ?? entries[0].contentRect.height
      onMeasured(privateRevealKey, measurementEpoch, height)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [measurementEpoch, onMeasured, privateRevealKey])

  // IntersectionObserver to detect visibility (for active tracking)
  useEffect(() => {
    const el = itemRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          onVisible(index)
        }
      },
      { threshold: 0.5 }
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [index, onVisible])

  // Lazy load metadata when first visible
  useEffect(() => {
    if (metaLoaded) return
    const el = itemRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setMetaLoaded(true)
          fetchOutputMetadata(file.name, file.workspace).then(setMeta).catch(() => {})
        }
      },
      { threshold: 0.1 }
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [file.workspace, file.name, file.revision, metaLoaded])

  // Blurred private videos keep no decoder/network source. Revealing restores
  // the source through React, but this lifecycle intentionally never calls play().
  // Non-active videos are also paused without changing their source.
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (privateBlurred) releaseVideoSource(video)
    else if (!isActive) video.pause()
  }, [isActive, privateBlurred, releaseVideoSource])

  const params = meta?.params as Record<string, unknown> | null
  const uploadFilenames = meta?.upload_filenames

  const prompt = (params?._tts_original_prompt as string) || (params?.prompt as string) || ''
  const modelType = (params?.model_type as string) || ''
  const modelLabel = modelDisplayName(modelType, models)
  const isAudio = file.type === 'audio'
  const resolution = isAudio ? '' : ((params?.resolution as string) || '')
  const seed = params?.seed as number | undefined
  const generationTime = meta?.generation_time
  const activatedLoras = Array.isArray(params?.activated_loras)
    ? params.activated_loras.filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
    : []
  const loraMultipliers = typeof params?.loras_multipliers === 'string'
    ? params.loras_multipliers.split(/\s+/).filter(Boolean)
    : []
  const loraProvenance = activatedLoras.map((value, loraIndex) => ({
    name: value.replace(/\\/g, '/').split('/').pop() || value,
    weight: loraMultipliers[loraIndex]?.split(';')[0] || '1',
  }))
  const referenceCount = (value: unknown) => Array.isArray(value)
    ? value.filter(Boolean).length
    : value ? 1 : 0
  const semanticReferenceKinds: Array<[string, number]> = [
    ['image', Math.max(referenceCount(params?.image_refs), referenceCount(uploadFilenames?.image_refs))] as [string, number],
    ['video', Math.max(
      referenceCount(params?.video_guide) + referenceCount(params?.video_guide2) + referenceCount(params?.video_guide3) + referenceCount(params?.video_source),
      referenceCount(uploadFilenames?.video_guide) + referenceCount(uploadFilenames?.video_guide2) + referenceCount(uploadFilenames?.video_guide3) + referenceCount(uploadFilenames?.video_source),
    )] as [string, number],
    ['audio', Math.max(
      referenceCount(params?.audio_guide) + referenceCount(params?.audio_guide2) + referenceCount(params?.audio_guide3),
      referenceCount(uploadFilenames?.audio_guide) + referenceCount(uploadFilenames?.audio_guide2) + referenceCount(uploadFilenames?.audio_guide3),
    )] as [string, number],
  ].filter(([, count]) => count > 0)
  const semanticReferenceTotal = semanticReferenceKinds.reduce((total, [, count]) => total + count, 0)
  const selectionKey = `${file.workspace}\0${file.name}`
  const isSelected = selectedOutputKeys.includes(selectionKey)

  const multiClipInfo = params?.multi_clip_info as { group_id: string; index: number; total: number } | undefined
  const groupId = multiClipInfo?.group_id
  const clipIndex = multiClipInfo?.index
  const clipTotal = multiClipInfo?.total

  const rawStart = uploadFilenames?.image_start
  const rawEnd = uploadFilenames?.image_end
  const imageStartFile = Array.isArray(rawStart) ? (rawStart.find((f: string) => f) || null) : rawStart
  const imageEndFile = Array.isArray(rawEnd) ? (rawEnd.find((f: string) => f) || null) : rawEnd

  const handleSelect = useCallback(() => {
    setSelectedOutput(index)
  }, [index, setSelectedOutput])

  const handleCardKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    handleSelect()
  }, [handleSelect])

  const handleLoadSettings = useCallback(() => {
    setSelectedOutput(index)
    setTimeout(() => loadSettingsFromOutput(), 50)
  }, [index, setSelectedOutput, loadSettingsFromOutput])

  const handleReroll = useCallback(() => {
    setSelectedOutput(index)
    setTimeout(() => rerollGeneration(), 50)
  }, [index, setSelectedOutput, rerollGeneration])

  const handleCopyPrompt = () => {
    if (!prompt) return
    // navigator.clipboard requires secure context; fallback to execCommand
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(prompt).then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      }).catch(() => {
        // Fallback
        const ta = document.createElement('textarea')
        ta.value = prompt
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      })
    } else {
      const ta = document.createElement('textarea')
      ta.value = prompt
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  const copyText = async (value: string) => {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
      return
    }
    const area = document.createElement('textarea')
    area.value = value
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    document.execCommand('copy')
    document.body.removeChild(area)
  }

  const handleShare = async () => {
    if (sharing) return
    if (!shareUrl && (file.private || file.explicit) && !window.confirm(
      "Anyone with this high-entropy link can view this one output without the project password. Creating the link does not change the output's Private preview flag. Continue?",
    )) return
    setSharing(true)
    setShareMessage('')
    try {
      let url = shareUrl
      let publicOrigin = true
      if (!url) {
        const result = await createOutputShare(file.name, file.workspace, file.revision)
        publicOrigin = result.configured_public_origin
        url = result.public_url || new URL(result.share_path, window.location.origin).toString()
        setShareUrl(url)
      }
      await copyText(url)
      setShareMessage(publicOrigin || !accessContext?.cloudflare_enabled
        ? 'Share link copied'
        : 'Link copied from this local address; it also works through your Cloudflare address. Configure a public share address in Maestro for one-click links.')
    } catch (error) {
      setShareMessage(error instanceof Error ? error.message : 'Could not create share link')
    } finally {
      setSharing(false)
    }
  }

  const handleRevokeShare = async () => {
    if (sharing) return
    setSharing(true)
    try {
      await revokeOutputShare(file.name, file.workspace)
      setShareUrl('')
      setShareMessage('Share link revoked')
    } catch (error) {
      setShareMessage(error instanceof Error ? error.message : 'Could not revoke share link')
    } finally {
      setSharing(false)
    }
  }

  const handleDelete = async () => {
    if (!confirmRef.current) {
      confirmRef.current = true
      setConfirmDelete(true)
      clearTimeout(timeoutRef.current)
      timeoutRef.current = setTimeout(() => {
        confirmRef.current = false
        setConfirmDelete(false)
      }, 3000)
      return
    }
    clearTimeout(timeoutRef.current)
    confirmRef.current = false
    setConfirmDelete(false)
    // Release video element src to unlock the file on Windows
    if (videoRef.current) {
      videoRef.current.pause()
      videoRef.current.removeAttribute('src')
      videoRef.current.load()
    }
    // The backend serves videos with share-delete semantics and handles any
    // remaining lock itself. Delete immediately with the workspace captured
    // by this item so a workspace switch cannot redirect a delayed action to
    // a same-named output elsewhere.
    await deleteOutput(file.name, file.workspace)
  }

  const handleComponentCleanup = async () => {
    if (!confirmCleanup) {
      setConfirmCleanup(true)
      setTimeout(() => setConfirmCleanup(false), 3000)
      return
    }
    setConfirmCleanup(false)
    setCleaningComponents(true)
    setCleanupError('')
    try {
      const result = await deleteOutputComponents(file.name, file.workspace)
      if (result.failed.length) {
        setCleanupError(
          `${result.failed.length} linked artifact(s) could not be removed; the final output was preserved.`,
        )
      }
    } catch (e) {
      console.error('Failed to clean linked components:', e)
      setCleanupError(e instanceof Error ? e.message : 'Component cleanup failed')
    } finally {
      await useStore.getState().loadOutputs()
      setCleaningComponents(false)
    }
  }

  const handleRejoin = async () => {
    if (!groupId) return
    setRejoining(true)
    try {
      await rejoinClipGroup(groupId)
    } finally {
      setRejoining(false)
    }
  }

  // Close move menu on outside click
  useEffect(() => {
    if (!showMoveMenu) return
    const handler = (e: MouseEvent) => {
      if (moveRef.current && !moveRef.current.contains(e.target as Node)) setShowMoveMenu(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showMoveMenu])

  const handleMove = async (targetWs: string) => {
    setMoving(true)
    setShowMoveMenu(false)
    try {
      await moveOutput(file.name, targetWs, file.workspace)
      // Immediately remove from local state (source may still exist during deferred cleanup)
      const store = useStore.getState()
      const filtered = store.outputs.filter(o => o.name !== file.name)
      useStore.setState({ outputs: filtered, selectedOutput: Math.min(store.selectedOutput, Math.max(0, filtered.length - 1)) })
    } catch (e) {
      console.error('Move failed:', e)
    } finally {
      setMoving(false)
    }
  }

  const handleSendToInput = async () => {
    if (file.type !== 'image') return
    try {
      const res = await fetch(getFileUrl(file.name, file.workspace))
      const blob = await res.blob()
      const imageFile = new File([blob], file.name, { type: blob.type || 'image/png' })
      if (generationMode === 'image') {
        addImageRef(imageFile)
      } else {
        setStartImage(imageFile)
      }
      setSentToInput(true)
      setTimeout(() => setSentToInput(false), 2000)
    } catch (e) {
      console.error('Failed to send image to input:', e)
    }
  }

  // Capture the frame the video preview is currently SHOWING (canvas grab
  // of the <video> element at its currentTime — same-origin, so no taint)
  // and append it to the Reference tiles. Pairs with SCAIL-2: scrub to the
  // pose you want, one click, it's your character reference.
  const handleSendFrameToRefs = async () => {
    if (file.type !== 'video') return
    try {
      let video = videoRef.current
      if (!video || video.videoWidth === 0) {
        // Preview has not decoded enough pixels yet — decode frame 0 offscreen.
        video = document.createElement('video')
        video.src = getFileUrl(file.name, file.workspace)
        video.muted = true
        await new Promise<void>((resolve, reject) => {
          video!.onloadeddata = () => resolve()
          video!.onerror = () => reject(new Error('video load failed'))
        })
      }
      const canvas = document.createElement('canvas')
      canvas.width = video.videoWidth
      canvas.height = video.videoHeight
      const ctx = canvas.getContext('2d')
      if (!ctx) throw new Error('canvas unavailable')
      ctx.drawImage(video, 0, 0)
      const blob: Blob = await new Promise((resolve, reject) =>
        canvas.toBlob(b => (b ? resolve(b) : reject(new Error('frame capture failed'))), 'image/png')
      )
      const stem = file.name.replace(/\.[^.]+$/, '')
      const frameFile = new File([blob], `${stem}_t${video.currentTime.toFixed(2)}s.png`, { type: 'image/png' })
      addImageRef(frameFile)
      setSentToInput(true)
      setTimeout(() => setSentToInput(false), 2000)
    } catch (e) {
      console.error('Failed to capture video frame:', e)
    }
  }

  const handleContinueFrom = async () => {
    if (file.type !== 'video') return
    try {
      const res = await fetch(getFileUrl(file.name, file.workspace))
      const blob = await res.blob()
      const videoFile = new File([blob], file.name, { type: blob.type || 'video/mp4' })
      const url = URL.createObjectURL(videoFile)
      const video = document.createElement('video')
      video.src = url
      video.onloadedmetadata = async () => {
        const duration = video.duration && isFinite(video.duration) ? video.duration : 0
        const uploaded = await uploadImage(videoFile)
        // Switch sub-mode FIRST: the switch stashes the current sub-mode's
        // working set and opens Extend's own slate. Setting the source
        // after keeps it from being wiped by that swap.
        setParam('image_mode', 3)
        setContinueVideo(videoFile, uploaded.path, url, duration)
      }
    } catch (e) {
      console.error('Failed to load video for continuation:', e)
    }
  }

  return (
    <div
      ref={itemRef}
      data-feed-index={index}
      data-feed-identity={encodeURIComponent(privateRevealKey)}
      role="group"
      tabIndex={0}
      aria-current={isActive ? 'true' : undefined}
      aria-label={`${file.name}. Press Enter or Space to select`}
      style={style}
      className={`rounded-xl border-2 overflow-hidden transition-colors focus-within:z-20 ${
        // Active frame: theme-aware bezel via frame-active-gradient.
        //
        // Default theme: linear gradient with both stops set to
        // accent-blue → reads as a flat 2px blue ring (preserves
        // prior visual exactly).
        //
        // Golden Hour: a conic-gradient override (see index.css)
        // sweeps "spotlight stops" around the perimeter — bright
        // orange / gold / ember at three asymmetric angles, with
        // bg-primary in between so those sections of the border
        // blend into the surrounding panel. The effect reads as
        // "stage lights catching the edge of the asset at random
        // points" rather than a uniform halo or solid line.
        //
        // shadow-active-ring is now minimal (just a 6px / 15% wash)
        // because the visual character lives ON the bezel itself,
        // not as an outward glow.
        isActive
          ? 'z-10 border-transparent frame-active-gradient shadow-active-ring'
          : 'border-border bg-bg-tertiary'
      }`}
      onClick={handleSelect}
      onKeyDown={handleCardKeyDown}
    >
      {/* Media player — bg-media-canvas keeps the letterbox dark even on light themes */}
      <div className="w-full aspect-video flex items-center justify-center bg-media-canvas relative overflow-hidden">
        {gallerySelectionMode && (
          <label
            className="absolute left-2 top-2 z-30 flex cursor-pointer items-center gap-1.5 rounded-full bg-black/70 px-2 py-1 text-[10px] text-white"
            onClick={event => event.stopPropagation()}
          >
            <input
              type="checkbox"
              checked={isSelected}
              onChange={() => toggleOutputSelection(file)}
              className="accent-blue-500"
            />
            Select
          </label>
        )}
        <div className={`w-full h-full flex items-center justify-center transition-[filter] duration-200 ${
          privateBlurred ? 'blur-2xl' : ''
        }`} inert={privateBlurred}>
        {file.type === 'video' ? (
          <video
            ref={setVideoElement}
            key={file.url}
            src={privateBlurred ? undefined : file.url}
            preload={privateBlurred ? 'none' : 'metadata'}
            controls
            loop
            className="w-full h-full object-contain"
            muted={!isActive}
          />
        ) : file.type === 'audio' ? (
          <div className="flex flex-col items-center gap-4">
            <div className="w-16 h-16 rounded-2xl bg-bg-active flex items-center justify-center">
              <Play size={24} className="text-text-muted" />
            </div>
            <p className="text-xs text-text-muted mb-2">{file.name}</p>
            <audio key={file.url} src={file.url} controls className="w-64" />
          </div>
        ) : (
          <RetryImage key={file.url} url={file.url} alt={file.name} />
        )}
        </div>
        {privateBlurred && (
          <button
            type="button"
            onClick={(event) => { event.stopPropagation(); revealPrivatePreview() }}
            aria-label={`Reveal blurred preview for ${file.name}`}
            className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-black/25 text-white"
            title="Click, tap, or press Enter to Reveal for this browser session"
          >
            <EyeOff size={24} />
            <span className="rounded-full bg-black/60 px-3 py-1 text-[11px]">Blurred preview — click to Reveal</span>
          </button>
        )}
        {file.private && privateRevealed && (
          <button
            type="button"
            onClick={(event) => { event.stopPropagation(); hidePrivatePreview() }}
            aria-label={`Blur preview for ${file.name}`}
            className="absolute right-2 top-2 z-10 rounded-full bg-black/65 p-1.5 text-white/80 hover:text-white"
            title="Blur this preview"
          >
            <EyeOff size={13} />
          </button>
        )}
      </div>

      {/* Inline info bar */}
      <div className="px-3 py-2 flex items-center gap-2 min-h-[40px]">
        {imageStartFile && (
          <img
            src={getUploadUrl(imageStartFile)}
            alt="Start"
            className="w-7 h-7 rounded border border-border object-cover shrink-0"
            title="Start image"
          />
        )}
        {imageEndFile && (
          <img
            src={getUploadUrl(imageEndFile)}
            alt="End"
            className="w-7 h-7 rounded border border-border object-cover shrink-0"
            title="End image"
          />
        )}

        <div className="flex-1 min-w-0">
          {params ? (
            <>
              <div className="text-xs text-text-secondary truncate">
                {modelLabel && <span className="font-medium" title={modelType}>{modelLabel}</span>}
                {resolution && <span className="text-text-muted"> &middot; {resolution}</span>}
                {seed != null && seed >= 0 && <span className="text-text-muted"> &middot; seed {seed}</span>}
                {generationTime != null && <span className="text-text-muted"> &middot; {generationTime}s</span>}
                {clipIndex != null && clipTotal != null && (
                  <span className="text-accent-blue"> &middot; clip {clipIndex + 1}/{clipTotal}</span>
                )}
              </div>
              {prompt && (
                <div className="text-[11px] text-text-muted truncate mt-0.5" title={prompt}>
                  {prompt}
                </div>
              )}
              {(loraProvenance.length > 0 || semanticReferenceTotal > 0) && (
                <div className="mt-1 flex flex-wrap items-center gap-1 text-[9px]">
                  {loraProvenance.slice(0, 2).map(lora => (
                    <span
                      key={`${lora.name}:${lora.weight}`}
                      className="max-w-44 truncate rounded bg-fuchsia-500/10 px-1.5 py-0.5 text-fuchsia-200"
                      title={`LoRA ${lora.name} · weight ${lora.weight}`}
                    >
                      LoRA · {lora.name} ×{lora.weight}
                    </span>
                  ))}
                  {loraProvenance.length > 2 && (
                    <span
                      className="rounded bg-fuchsia-500/10 px-1.5 py-0.5 text-fuchsia-200"
                      title={loraProvenance.map(lora => `${lora.name} ×${lora.weight}`).join(', ')}
                    >
                      +{loraProvenance.length - 2} LoRA
                    </span>
                  )}
                  {semanticReferenceTotal > 0 && (
                    <span
                      className="rounded bg-violet-500/10 px-1.5 py-0.5 text-violet-200"
                      title={`Semantic references: ${semanticReferenceKinds.map(([kind, count]) => `${count} ${kind}`).join(', ')}`}
                    >
                      Refs · {semanticReferenceKinds.map(([kind, count]) => `${count} ${kind}`).join(' · ')}
                    </span>
                  )}
                </div>
              )}
            </>
          ) : metaLoaded ? (
            <div className="text-[11px] text-text-muted truncate">{file.name}</div>
          ) : (
            <div className="text-[11px] text-text-muted animate-pulse">Loading...</div>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-0.5 shrink-0" onClick={e => e.stopPropagation()}>
          {params && (
            <>
              <button
                onClick={(e) => { e.stopPropagation(); setShowSaveRecipe(true) }}
                className="p-1.5 rounded-lg hover:bg-bg-hover text-text-secondary hover:text-accent-blue transition-colors"
                title="Save as Recipe — reuse this look with one click"
              >
                <BookMarked size={13} />
              </button>
              <button
                onClick={handleLoadSettings}
                className="p-1.5 rounded-lg hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-colors"
                title="Load settings"
              >
                <Pencil size={13} />
              </button>
              <button
                onClick={handleReroll}
                className="p-1.5 rounded-lg hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-colors"
                title="Re-generate with same settings"
              >
                <RefreshCw size={13} />
              </button>
              {file.type === 'video' && (
                <>
                  <button
                    onClick={() => openRetakeDialog(file.name)}
                    className="p-1.5 rounded-lg hover:bg-bg-hover text-text-secondary hover:text-indicator-warning transition-colors"
                    title="Retake — regenerate a time region"
                  >
                    <Scissors size={13} />
                  </button>
                  <button
                    onClick={handleContinueFrom}
                    className="p-1.5 rounded-lg hover:bg-bg-hover text-text-secondary hover:text-accent-blue transition-colors"
                    title="Extend this video with new content"
                  >
                    <FastForward size={13} />
                  </button>
                </>
              )}
              {groupId && (
                <button
                  onClick={handleRejoin}
                  disabled={rejoining}
                  className="p-1.5 rounded-lg hover:bg-bg-hover text-accent-blue hover:text-accent-blue-hover transition-colors disabled:opacity-50"
                  title={`Rejoin all ${clipTotal} clips in this group`}
                >
                  {rejoining ? <Loader2 size={13} className="animate-spin" /> : <Combine size={13} />}
                </button>
              )}
              <button
                onClick={handleCopyPrompt}
                className="p-1.5 rounded-lg hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-colors"
                title="Copy prompt"
              >
                {copied ? <Check size={13} className="text-accent-green" /> : <Copy size={13} />}
              </button>
            </>
          )}
          {file.type === 'image' && (
            <button
              onClick={(e) => { e.stopPropagation(); handleSendToInput() }}
              className={`p-1.5 rounded-lg transition-colors ${
                sentToInput
                  ? 'text-accent-green'
                  : 'hover:bg-bg-hover text-text-secondary hover:text-accent-blue'
              }`}
              title={generationMode === 'image' ? 'Use as input image' : 'Use as start frame'}
            >
              {sentToInput ? <Check size={13} /> : <ArrowLeftToLine size={13} />}
            </button>
          )}
          {file.type === 'video' && (
            <button
              onClick={(e) => { e.stopPropagation(); handleSendFrameToRefs() }}
              className={`p-1.5 rounded-lg transition-colors ${
                sentToInput
                  ? 'text-accent-green'
                  : 'hover:bg-bg-hover text-text-secondary hover:text-accent-blue'
              }`}
              title="Use current frame as reference image"
            >
              {sentToInput ? <Check size={13} /> : <ArrowLeftToLine size={13} />}
            </button>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation()
              const link = document.createElement('a')
              link.href = getFileUrl(file.name, file.workspace)
              link.download = file.name
              document.body.appendChild(link)
              link.click()
              document.body.removeChild(link)
            }}
            className="p-1.5 rounded-lg hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-colors"
            title="Download"
          >
            <Download size={13} />
          </button>
          {!browsingUploads && (
            <>
              <button
                onClick={(event) => { event.stopPropagation(); void handleShare() }}
                disabled={sharing}
                className={`p-1.5 rounded-lg transition-colors ${
                  shareUrl
                    ? 'text-accent-green hover:bg-bg-hover'
                    : 'hover:bg-bg-hover text-text-secondary hover:text-accent-blue'
                } disabled:opacity-50`}
                title={shareUrl ? 'Copy this output’s share link again' : 'Create and copy a read-only link to only this output'}
              >
                {sharing ? <Loader2 size={13} className="animate-spin" /> : shareUrl ? <Check size={13} /> : <Share2 size={13} />}
              </button>
              {shareUrl && (
                <button
                  onClick={(event) => { event.stopPropagation(); void handleRevokeShare() }}
                  disabled={sharing}
                  className="p-1.5 rounded-lg text-text-secondary transition-colors hover:bg-bg-hover hover:text-red-400 disabled:opacity-50"
                  title="Revoke this output’s share link"
                >
                  <Link2Off size={13} />
                </button>
              )}
            </>
          )}
          {/* Move to workspace */}
          {!browsingUploads && (
          <div className="relative" ref={moveRef}>
            <button
              onClick={(e) => { e.stopPropagation(); setShowMoveMenu(!showMoveMenu) }}
              disabled={moving}
              className={`p-1.5 rounded-lg transition-colors ${
                moving ? 'text-accent-blue animate-pulse' : 'hover:bg-bg-hover text-text-secondary hover:text-text-primary'
              }`}
              title="Move to workspace"
            >
              <FolderInput size={13} />
            </button>
            {showMoveMenu && (
              <div className="absolute right-0 bottom-full mb-1 w-40 bg-bg-secondary border border-border rounded-lg shadow-lg z-50 overflow-hidden" onClick={e => e.stopPropagation()}>
                <div className="px-2 py-1 border-b border-border">
                  <span className="text-[9px] text-text-muted uppercase tracking-wider">Move to</span>
                </div>
                <div className="max-h-[150px] overflow-y-auto">
                  {workspaces.filter(ws => ws.name !== file.workspace).map(ws => (
                    <button
                      key={ws.name}
                      onClick={() => handleMove(ws.name)}
                      className="w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors"
                    >
                      {ws.name}
                    </button>
                  ))}
                  {workspaces.filter(ws => ws.name !== file.workspace).length === 0 && (
                    <div className="px-3 py-2 text-[10px] text-text-muted">No other workspaces</div>
                  )}
                </div>
              </div>
            )}
          </div>
          )}
          {!browsingUploads && (
          file.artifact_class === 'final' && file.linked_component_count > 0 && (
          <>
          <button
            onClick={(e) => { e.stopPropagation(); handleComponentCleanup() }}
            disabled={cleaningComponents}
            className={`px-2 py-1.5 rounded-lg transition-colors text-[10px] font-medium ${
              confirmCleanup
                ? 'bg-amber-500/20 text-amber-300'
                : 'hover:bg-bg-hover text-text-secondary hover:text-amber-300'
            }`}
            title={confirmCleanup ? 'Click again to delete linked components' : 'Delete linked component, window, and temporary outputs'}
          >
            {cleaningComponents ? 'Cleaning…' : confirmCleanup ? 'Clean?' : `Clean ${file.linked_component_count}`}
          </button>
          {cleanupError && (
            <span className="max-w-40 truncate text-[9px] text-red-400" title={cleanupError}>
              Cleanup incomplete
            </span>
          )}
          </>
          )
          )}
          {!browsingUploads && (
          <button
            onClick={(e) => { e.stopPropagation(); toggleFavorite(file.name) }}
            className={`p-1.5 rounded-lg transition-colors ${
              file.favorite
                ? 'text-red-400 hover:text-red-300'
                : 'hover:bg-bg-hover text-text-secondary hover:text-red-400'
            }`}
            title={file.favorite ? 'Remove from favorites' : 'Add to favorites'}
          >
            <Heart size={13} fill={file.favorite ? 'currentColor' : 'none'} />
          </button>
          )}
          {!browsingUploads && (
          <button
            onClick={handleDelete}
            className={`p-1.5 rounded-lg transition-colors flex items-center gap-1 ${
              confirmDelete
                ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                : 'hover:bg-bg-hover text-text-secondary hover:text-red-400'
            }`}
            title={confirmDelete
              ? file.linked_component_count > 0
                ? `Click again to delete this output and ${file.linked_component_count} linked parts`
                : 'Click again to confirm delete'
              : 'Delete output'}
          >
            <Trash2 size={13} />
            {confirmDelete && (
              <span className="text-[11px] font-medium">
                {file.linked_component_count > 0 ? `Delete + ${file.linked_component_count}?` : 'Delete?'}
              </span>
            )}
          </button>
          )}
          {shareMessage && (
            <span className="max-w-56 truncate text-[9px] text-text-muted" title={shareMessage}>
              {shareMessage}
            </span>
          )}
        </div>
      </div>
      {showSaveRecipe && (
        <SaveRecipeDialog
          onCancel={() => setShowSaveRecipe(false)}
          onSave={async (name, description, nsfw) => {
            await saveRecipeFromOutput(file.name, name, description, nsfw)
            setShowSaveRecipe(false)
          }}
        />
      )}
    </div>
  )
}
