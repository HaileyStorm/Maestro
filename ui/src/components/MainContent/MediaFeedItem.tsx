import { useState, useRef, useEffect, useCallback, type CSSProperties, type KeyboardEvent } from 'react'
import { Play, Pencil, RefreshCw, Copy, Trash2, Check, Combine, Loader2, Heart, ArrowLeftToLine, Download, FolderInput, Scissors, FastForward, BookMarked, EyeOff, Share2, Link2Off } from 'lucide-react'
import { SaveRecipeDialog } from '../Recipes/SaveRecipeDialog'
import { useStore } from '../../stores/useStore'
import { createOutputShare, deleteOutputComponents, getUploadUrl, fetchOutputMetadata, getFileUrl, moveOutput, revokeOutputShare, uploadImage } from '../../api/client'
import type { OutputFile, OutputMetadata } from '../../types'
import { formatGenerationDuration } from '../../lib/format'
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
  const saveRecipeTriggerRef = useRef<HTMLButtonElement>(null)
  const saveRecipeEpochRef = useRef(0)
  const closeSaveRecipeDialog = useCallback(() => {
    saveRecipeEpochRef.current += 1
    setShowSaveRecipe(false)
  }, [])
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
  const [sharePublicOrigin, setSharePublicOrigin] = useState<boolean | null>(null)
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
      try {
        await navigator.clipboard.writeText(value)
        return
      } catch {
        // Insecure/local browser contexts often expose Clipboard but reject
        // writes. Fall through to the older, synchronous copy path.
      }
    }
    const area = document.createElement('textarea')
    area.value = value
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const copied = document.execCommand('copy')
    document.body.removeChild(area)
    if (!copied) throw new Error('Clipboard is unavailable in this browser')
  }

  const shareResultMessage = (method: 'shared' | 'copied', publicOrigin: boolean) => {
    if (publicOrigin) {
      return `Public read-only output link ${method}. It does not grant project access.`
    }
    if (accessContext?.cloudflare_enabled) {
      return `Local-address output link ${method}. The same path works through Maestro's Cloudflare address, but this link itself may not open off your network. It does not grant project access.`
    }
    return `Local-network output link ${method}. It may not open outside this network. It does not grant project access.`
  }

  const handleShare = async () => {
    if (sharing) return
    if (!shareUrl && (file.private || file.explicit) && !window.confirm(
      "Anyone with this link can view only this output without entering the project password. The link does not change whether its Gallery preview is blurred. Continue?",
    )) return
    setSharing(true)
    setShareMessage('')
    try {
      let url = shareUrl
      let publicOrigin = sharePublicOrigin ?? false
      if (!url) {
        const result = await createOutputShare(file.name, file.workspace, file.revision)
        publicOrigin = result.configured_public_origin
        url = result.public_url || new URL(result.share_path, window.location.origin).toString()
        setShareUrl(url)
        setSharePublicOrigin(publicOrigin)
      }
      if (navigator.share) {
        try {
          await navigator.share({
            title: file.name,
            text: 'Read-only link to this output. It does not grant project access.',
            url,
          })
          setShareMessage(shareResultMessage('shared', publicOrigin))
          return
        } catch (error) {
          if (error instanceof DOMException && error.name === 'AbortError') {
            setShareMessage('Share cancelled. The output link is still active and project access is unchanged.')
            return
          }
          // Native sharing may reject unsupported payloads or fail after its
          // sheet opens. Preserve the already-created capability and provide
          // the dependable copy fallback.
        }
      }
      await copyText(url)
      setShareMessage(shareResultMessage('copied', publicOrigin))
    } catch (error) {
      setShareMessage(error instanceof Error ? error.message : 'Could not create share link')
    } finally {
      setSharing(false)
    }
  }

  const handleRevokeShare = async () => {
    if (sharing) return
    if (!window.confirm(
      'Revoke any active read-only link for this output? Anyone using one will lose access to this output. Project access will not change.',
    )) return
    setSharing(true)
    setShareMessage('')
    try {
      const revoked = await revokeOutputShare(file.name, file.workspace)
      setShareUrl('')
      setSharePublicOrigin(null)
      setShareMessage(revoked > 0
        ? 'Output link revoked. Project access is unchanged.'
        : 'No active output link was found. Project access is unchanged.')
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
          `${result.failed.length} related file(s) could not be removed. The finished output was kept.`,
        )
      }
    } catch (e) {
      console.error('Failed to clean linked components:', e)
      setCleanupError(e instanceof Error ? e.message : 'Related files could not be removed')
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
        {privateBlurred ? (
          <div className="flex h-full w-full items-center justify-center bg-bg-active text-text-muted">
            <EyeOff size={24} aria-hidden="true" />
          </div>
        ) : file.type === 'video' ? (
          <video
            ref={setVideoElement}
            key={privateRevealKey}
            src={file.url}
            preload="metadata"
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
            <audio key={privateRevealKey} src={file.url} controls className="w-64" />
          </div>
        ) : (
          <RetryImage key={privateRevealKey} url={file.url} alt={file.name} />
        )}
        </div>
        {privateBlurred && (
          <button
            type="button"
            onClick={(event) => { event.stopPropagation(); revealPrivatePreview() }}
            aria-label={`Reveal blurred preview for ${file.name}`}
            className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-black/25 text-white"
            title="Show this preview in this browser"
          >
            <EyeOff size={24} />
            <span className="rounded-full bg-black/60 px-3 py-1 text-[11px]">Blurred preview — select to show</span>
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
      <div className="flex min-h-[40px] flex-col gap-2 px-3 py-2 md:flex-row md:items-center">
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
                {generationTime != null && (
                  <span
                  className="text-text-muted"
                  title="Recorded generation time"
                  >
                    {' '}&middot; {formatGenerationDuration(generationTime)}
                  </span>
                )}
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
        <div
          className="flex shrink-0 flex-wrap items-center justify-end gap-0.5"
          role="group"
          aria-label={`Actions for ${file.name}`}
          onClick={e => e.stopPropagation()}
        >
          {params && (
            <>
              <button
                ref={saveRecipeTriggerRef}
                onClick={(e) => {
                  e.stopPropagation()
                  saveRecipeEpochRef.current += 1
                  setShowSaveRecipe(true)
                }}
                type="button"
                aria-haspopup="dialog"
                aria-expanded={showSaveRecipe}
                aria-label="Save as Recipe — reuse this look with one click"
                className="min-h-11 min-w-11 rounded-lg p-1.5 text-text-secondary transition-colors hover:bg-bg-hover hover:text-accent-blue md:min-h-0 md:min-w-0"
                title="Save as Recipe — reuse this look with one click"
              >
                <BookMarked size={13} />
              </button>
              <button
                onClick={handleLoadSettings}
                type="button"
                aria-label={`Load generation settings from ${file.name}`}
                className="min-h-11 min-w-11 rounded-lg p-1.5 text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary md:min-h-0 md:min-w-0"
                title="Load settings"
              >
                <Pencil size={13} />
              </button>
              <button
                onClick={handleReroll}
                type="button"
                aria-label={`Regenerate ${file.name} with the same settings`}
                className="min-h-11 min-w-11 rounded-lg p-1.5 text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary md:min-h-0 md:min-w-0"
                title="Re-generate with same settings"
              >
                <RefreshCw size={13} />
              </button>
              {file.type === 'video' && (
                <>
                  <button
                    onClick={event => {
                      event.currentTarget.focus()
                      openRetakeDialog(file.name)
                    }}
                    type="button"
                    aria-haspopup="dialog"
                    aria-label="Retake — regenerate a time region"
                    className="min-h-11 min-w-11 rounded-lg p-1.5 text-text-secondary transition-colors hover:bg-bg-hover hover:text-indicator-warning md:min-h-0 md:min-w-0"
                    title="Retake — regenerate a time region"
                  >
                    <Scissors size={13} />
                  </button>
                  <button
                    onClick={handleContinueFrom}
                    type="button"
                    aria-label={`Extend ${file.name} with new content`}
                    className="min-h-11 min-w-11 rounded-lg p-1.5 text-text-secondary transition-colors hover:bg-bg-hover hover:text-accent-blue md:min-h-0 md:min-w-0"
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
                  type="button"
                  aria-label={`Rejoin all ${clipTotal} clips for ${file.name}`}
                  className="min-h-11 min-w-11 rounded-lg p-1.5 text-accent-blue transition-colors hover:bg-bg-hover hover:text-accent-blue-hover disabled:opacity-50 md:min-h-0 md:min-w-0"
                  title={`Rejoin all ${clipTotal} clips in this group`}
                >
                  {rejoining ? <Loader2 size={13} className="animate-spin" /> : <Combine size={13} />}
                </button>
              )}
              <button
                onClick={handleCopyPrompt}
                type="button"
                aria-label={copied ? `Prompt copied from ${file.name}` : `Copy prompt from ${file.name}`}
                className="min-h-11 min-w-11 rounded-lg p-1.5 text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary md:min-h-0 md:min-w-0"
                title="Copy prompt"
              >
                {copied ? <Check size={13} className="text-accent-green" /> : <Copy size={13} />}
              </button>
            </>
          )}
          {file.type === 'image' && (
            <button
              onClick={(e) => { e.stopPropagation(); handleSendToInput() }}
              type="button"
              aria-label={generationMode === 'image'
                ? `Use ${file.name} as an input image`
                : `Use ${file.name} as a start frame`}
              className={`min-h-11 min-w-11 rounded-lg p-1.5 transition-colors md:min-h-0 md:min-w-0 ${
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
              type="button"
              aria-label={`Use the current frame from ${file.name} as a reference image`}
              className={`min-h-11 min-w-11 rounded-lg p-1.5 transition-colors md:min-h-0 md:min-w-0 ${
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
            aria-label={`Download ${file.name}`}
            className="min-h-11 min-w-11 rounded-lg p-1.5 text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary md:min-h-0 md:min-w-0"
            title="Download"
          >
            <Download size={13} />
          </button>
          {!browsingUploads && (
            <>
              <button
                onClick={(event) => { event.stopPropagation(); void handleShare() }}
                disabled={sharing}
                aria-label={shareUrl
                  ? `Share ${file.name} again — output-only link, not project access`
                  : `Share ${file.name} — create an output-only link, not project access`}
                className={`min-h-11 min-w-11 rounded-lg p-1.5 transition-colors md:min-h-0 md:min-w-0 ${
                  shareUrl
                    ? 'text-accent-green hover:bg-bg-hover'
                    : 'hover:bg-bg-hover text-text-secondary hover:text-accent-blue'
                } disabled:opacity-50`}
                title={shareUrl ? 'Copy this output’s share link again' : 'Create and copy a read-only link to only this output'}
              >
                {sharing ? <Loader2 size={13} className="animate-spin" /> : shareUrl ? <Check size={13} /> : <Share2 size={13} />}
              </button>
              <button
                onClick={(event) => { event.stopPropagation(); void handleRevokeShare() }}
                disabled={sharing}
                aria-label={`Revoke any active output-only link for ${file.name}`}
                className="min-h-11 min-w-11 rounded-lg p-1.5 text-text-secondary transition-colors hover:bg-bg-hover hover:text-red-400 disabled:opacity-50 md:min-h-0 md:min-w-0"
                title="Revoke any active share link for this output"
              >
                <Link2Off size={13} />
              </button>
            </>
          )}
          {/* Move to workspace */}
          {!browsingUploads && (
          <div className="relative" ref={moveRef}>
            <button
              onClick={(e) => { e.stopPropagation(); setShowMoveMenu(!showMoveMenu) }}
              disabled={moving}
              type="button"
              aria-haspopup="menu"
              aria-expanded={showMoveMenu}
              aria-label={`Move ${file.name} to another project`}
              className={`min-h-11 min-w-11 rounded-lg p-1.5 transition-colors md:min-h-0 md:min-w-0 ${
                moving ? 'text-accent-blue animate-pulse' : 'hover:bg-bg-hover text-text-secondary hover:text-text-primary'
              }`}
              title="Move to workspace"
            >
              <FolderInput size={13} />
            </button>
            {showMoveMenu && (
              <div className="absolute right-0 bottom-full mb-1 w-48 bg-bg-secondary border border-border rounded-lg shadow-lg z-50 overflow-hidden" role="menu" onClick={e => e.stopPropagation()}>
                <div className="px-2 py-1 border-b border-border">
                  <span className="text-[9px] text-text-muted uppercase tracking-wider">Move to</span>
                </div>
                <div className="max-h-[150px] overflow-y-auto">
                  {workspaces.filter(ws => ws.name !== file.workspace).map(ws => (
                    <button
                      key={ws.name}
                      onClick={() => handleMove(ws.name)}
                      role="menuitem"
                      className="min-h-11 w-full px-3 py-1.5 text-left text-xs text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary md:min-h-0"
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
            type="button"
            aria-label={confirmCleanup
              ? `Confirm removal of ${file.linked_component_count} related files for ${file.name}; keep the finished output`
              : `Remove ${file.linked_component_count} related files for ${file.name}; keep the finished output`}
            className={`min-h-11 min-w-11 rounded-lg px-2 py-1.5 text-[10px] font-medium transition-colors md:min-h-0 md:min-w-0 ${
              confirmCleanup
                ? 'bg-amber-500/20 text-amber-300'
                : 'hover:bg-bg-hover text-text-secondary hover:text-amber-300'
            }`}
            title={confirmCleanup ? `Delete ${file.linked_component_count} related files and keep this finished output?` : 'Delete related parts, generation steps, and temporary files while keeping this finished output'}
          >
            {cleaningComponents ? 'Removing…' : confirmCleanup ? 'Delete related files?' : `Remove ${file.linked_component_count} related`}
          </button>
          {cleanupError && (
            <span className="max-w-40 truncate text-[9px] text-red-400" title={cleanupError}>
              Some related files remain
            </span>
          )}
          </>
          )
          )}
          {!browsingUploads && (
          <button
            onClick={(e) => { e.stopPropagation(); toggleFavorite(file.name) }}
            type="button"
            aria-pressed={file.favorite}
            aria-label={file.favorite ? `Remove ${file.name} from favorites` : `Add ${file.name} to favorites`}
            className={`min-h-11 min-w-11 rounded-lg p-1.5 transition-colors md:min-h-0 md:min-w-0 ${
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
            type="button"
            aria-label={confirmDelete
              ? `Confirm permanent deletion of ${file.name}`
              : `Delete ${file.name}`}
            className={`flex min-h-11 min-w-11 items-center gap-1 rounded-lg p-1.5 transition-colors md:min-h-0 md:min-w-0 ${
              confirmDelete
                ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                : 'hover:bg-bg-hover text-text-secondary hover:text-red-400'
            }`}
            title={confirmDelete
              ? file.linked_component_count > 0
                ? `Click again to permanently delete this output and ${file.linked_component_count} related files`
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
            <span
              className="w-full text-right text-[10px] leading-4 text-text-muted md:max-w-56 md:truncate"
              role="status"
              aria-live="polite"
              title={shareMessage}
            >
              {shareMessage}
            </span>
          )}
        </div>
      </div>
      {showSaveRecipe && (
        <SaveRecipeDialog
          onCancel={closeSaveRecipeDialog}
          restoreFocusRef={saveRecipeTriggerRef}
          onSave={async (name, description, nsfw) => {
            const requestEpoch = saveRecipeEpochRef.current
            await saveRecipeFromOutput(file.name, name, description, nsfw)
            if (saveRecipeEpochRef.current === requestEpoch) closeSaveRecipeDialog()
          }}
        />
      )}
    </div>
  )
}
