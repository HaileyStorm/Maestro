import { useCallback, useEffect, useMemo, useRef, useState, type TouchEvent } from 'react'
import { createPortal } from 'react-dom'
import { ArrowLeftRight, ChevronLeft, ChevronRight, Columns2, Eye, EyeOff, Film, Loader2, X } from 'lucide-react'
import type { OutputFile } from '../../types'
import { closeModalIfTop, installModalFocus } from '../../lib/modalFocus'
import {
  hidePrivatePreview,
  privatePreviewIdentity,
  privatePreviewWasRevealed,
  revealPrivatePreview,
  subscribePrivatePreviewChanges,
} from '../../lib/privatePreview'

interface Props {
  files: OutputFile[]
  initialIdentity: string
  restoreFocus: HTMLElement | null
  onClose: () => void
}

function identity(file: OutputFile): string {
  return privatePreviewIdentity(file.workspace, file.name, file.revision)
}

function ViewerImage({ file, alt, status, onReady, onFailure }: {
  file: OutputFile
  alt?: string
  status: 'loading' | 'ready' | 'error'
  onReady: () => void
  onFailure: () => void
}) {
  const [attempt, setAttempt] = useState(0)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryPending = useRef(false)
  useEffect(() => () => {
    if (retryTimer.current !== null) clearTimeout(retryTimer.current)
  }, [])

  const retry = () => {
    if (retryPending.current) return
    if (attempt >= 3) {
      onFailure()
      return
    }
    retryPending.current = true
    retryTimer.current = setTimeout(() => {
      retryPending.current = false
      setAttempt(attempt + 1)
    }, 400 * (attempt + 1))
  }
  const src = attempt === 0 ? file.url : `${file.url}${file.url.includes('?') ? '&' : '?'}viewer_retry=${attempt}`
  return (
    <img
      key={src}
      src={src}
      alt={alt ?? file.name}
      className={`h-full w-full object-contain ${status === 'loading' ? 'opacity-0' : ''}`}
      onLoad={event => {
        const image = event.currentTarget
        if (image.naturalWidth === 0 || image.naturalHeight === 0) retry()
        else onReady()
      }}
      onError={retry}
    />
  )
}

function GalleryComparison({ current, images }: { current: OutputFile; images: OutputFile[] }) {
  const currentIdentity = identity(current)
  const [beforeChoice, setBeforeChoice] = useState(() => identity(images.find(item => identity(item) !== currentIdentity) ?? current))
  const [afterChoice, setAfterChoice] = useState(currentIdentity)
  const [position, setPosition] = useState(50)
  const [imageStatus, setImageStatus] = useState<Record<string, 'ready' | 'error'>>({})
  const stageRef = useRef<HTMLDivElement>(null)
  const before = images.find(item => identity(item) === beforeChoice)
    ?? images.find(item => identity(item) !== currentIdentity)
  const after = images.find(item => identity(item) === afterChoice) ?? current
  const beforeIdentity = before && identity(before)
  const afterIdentity = identity(after)
  const beforeHidden = Boolean(before?.private && beforeIdentity && !privatePreviewWasRevealed(beforeIdentity))
  const afterHidden = Boolean(after.private && !privatePreviewWasRevealed(afterIdentity))
  const canCompare = Boolean(before && beforeIdentity && beforeIdentity !== afterIdentity)

  const setPositionFromPointer = (clientX: number) => {
    const bounds = stageRef.current?.getBoundingClientRect()
    if (!bounds?.width) return
    setPosition(Math.max(0, Math.min(100, Math.round((clientX - bounds.left) / bounds.width * 100))))
  }

  return (
    <div className="flex h-full w-full min-h-0 flex-col gap-3" onTouchStart={event => event.stopPropagation()} onTouchEnd={event => event.stopPropagation()}>
      <div className="flex flex-col gap-2 sm:flex-row">
        {(['Before', 'After'] as const).map(side => {
          const selected = side === 'Before' ? beforeIdentity : afterIdentity
          const other = side === 'Before' ? afterIdentity : beforeIdentity
          return (
            <label key={side} className="flex min-w-0 flex-1 items-center gap-2 text-xs text-white/75">
              <span className="w-12 shrink-0">{side}</span>
              <select
                aria-label={`${side} comparison image`}
                value={selected}
                onChange={event => side === 'Before' ? setBeforeChoice(event.target.value) : setAfterChoice(event.target.value)}
                className="min-h-11 min-w-0 flex-1 rounded-lg border border-white/25 bg-neutral-900 px-2 text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
              >
                {images.map(item => {
                  const itemIdentity = identity(item)
                  return <option key={itemIdentity} value={itemIdentity} disabled={itemIdentity === other}>{item.name}</option>
                })}
              </select>
              {side === 'Before' && before?.private && !beforeHidden && beforeIdentity && (
                <button type="button" onClick={() => hidePrivatePreview(beforeIdentity)} aria-label="Blur Before preview" className="min-h-11 rounded-lg px-2 hover:bg-white/10">Blur</button>
              )}
              {side === 'After' && after.private && !afterHidden && (
                <button type="button" onClick={() => hidePrivatePreview(afterIdentity)} aria-label="Blur After preview" className="min-h-11 rounded-lg px-2 hover:bg-white/10">Blur</button>
              )}
            </label>
          )
        })}
        <button type="button" disabled={!canCompare} onClick={() => { setBeforeChoice(afterIdentity); setAfterChoice(beforeIdentity!) }} className="flex min-h-11 items-center justify-center gap-1.5 rounded-lg border border-white/25 px-3 text-xs text-white disabled:opacity-40 hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"><ArrowLeftRight size={15} />Swap</button>
      </div>
      <div
        ref={stageRef}
        role="group"
        aria-label="Before and after image comparison"
        className="relative min-h-0 flex-1 overflow-hidden rounded-lg bg-white/5"
        style={{ touchAction: 'none' }}
        onTouchStart={event => event.stopPropagation()}
        onTouchEnd={event => event.stopPropagation()}
        onPointerDown={event => {
          if (!canCompare || beforeHidden || afterHidden) return
          event.currentTarget.setPointerCapture(event.pointerId)
          setPositionFromPointer(event.clientX)
        }}
        onPointerMove={event => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) setPositionFromPointer(event.clientX)
        }}
        onPointerUp={event => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
        }}
      >
        {!canCompare ? (
          <p className="absolute inset-0 grid place-items-center px-4 text-center text-sm text-white/75">Choose two different images from this project to compare.</p>
        ) : beforeHidden || afterHidden ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-4 text-center text-sm text-white/75">
            <p>Reveal both previews in this browser to compare them.</p>
            {beforeHidden && beforeIdentity && <button type="button" onClick={() => revealPrivatePreview(beforeIdentity)} className="min-h-11 rounded-full border border-white/30 px-5 hover:bg-white/10">Show Before preview</button>}
            {afterHidden && <button type="button" onClick={() => revealPrivatePreview(afterIdentity)} className="min-h-11 rounded-full border border-white/30 px-5 hover:bg-white/10">Show After preview</button>}
          </div>
        ) : (
          <>
            <div className="absolute inset-0">
              <ViewerImage key={afterIdentity} file={after} alt={`After: ${after.name}`} status={imageStatus[afterIdentity] ?? 'loading'} onReady={() => setImageStatus(status => ({ ...status, [afterIdentity]: 'ready' }))} onFailure={() => setImageStatus(status => ({ ...status, [afterIdentity]: 'error' }))} />
            </div>
            <div className="absolute inset-0 overflow-hidden" style={{ clipPath: `inset(0 ${100 - position}% 0 0)` }}>
              <ViewerImage key={beforeIdentity} file={before!} alt={`Before: ${before!.name}`} status={imageStatus[beforeIdentity!] ?? 'loading'} onReady={() => setImageStatus(status => ({ ...status, [beforeIdentity!]: 'ready' }))} onFailure={() => setImageStatus(status => ({ ...status, [beforeIdentity!]: 'error' }))} />
            </div>
            <div className="pointer-events-none absolute inset-y-0 w-px bg-white shadow-[0_0_8px_black]" style={{ left: `${position}%` }} aria-hidden="true" />
            <span className="pointer-events-none absolute left-3 top-3 rounded bg-black/65 px-2 py-1 text-xs">Before</span>
            <span className="pointer-events-none absolute right-3 top-3 rounded bg-black/65 px-2 py-1 text-xs">After</span>
            {(imageStatus[beforeIdentity!] === 'error' || imageStatus[afterIdentity] === 'error') && (
              <p role="alert" className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded bg-black/80 px-3 py-2 text-center text-xs">A comparison image could not be loaded. Choose another image.</p>
            )}
          </>
        )}
      </div>
      <label className="flex items-center gap-3 text-xs text-white/75">
        <span>Before</span>
        <input type="range" min="0" max="100" value={position} onChange={event => setPosition(Number(event.target.value))} aria-label="Comparison split" className="h-11 min-w-0 flex-1 accent-white" />
        <span>After</span>
      </label>
    </div>
  )
}

export function GalleryViewer({ files, initialIdentity, restoreFocus, onClose }: Props) {
  const media = useMemo(() => {
    const initialFile = files.find(file => identity(file) === initialIdentity)
    return initialFile
      ? files.filter(file => file.type !== 'audio' && file.workspace === initialFile.workspace)
      : []
  }, [files, initialIdentity])
  const [selectedIdentity, setSelectedIdentity] = useState(initialIdentity)
  const [compareMode, setCompareMode] = useState(false)
  const [revealVersion, setRevealVersion] = useState(0)
  const [mediaStatus, setMediaStatus] = useState<{ identity: string; state: 'ready' | 'error' } | null>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const touchStart = useRef<{ x: number; y: number } | null>(null)
  const requestedIndex = media.findIndex(file => identity(file) === selectedIdentity)
  const selectedIndex = requestedIndex >= 0
    ? requestedIndex
    : media.findIndex(file => identity(file) === initialIdentity)
  const file = media[selectedIndex]
  const fileIdentity = file && identity(file)
  const currentStatus = mediaStatus?.identity === fileIdentity ? mediaStatus.state : 'loading'
  const images = media.filter(item => item.type === 'image')
  const comparisonOpen = compareMode && file?.type === 'image'
  void revealVersion
  const privateHidden = Boolean(file?.private && fileIdentity && !privatePreviewWasRevealed(fileIdentity))

  const close = useCallback(() => {
    closeModalIfTop(document, dialogRef.current, onClose)
  }, [onClose])

  useEffect(() => {
    const dialog = dialogRef.current
    const initialFocus = closeRef.current
    if (!dialog || !initialFocus) return
    return installModalFocus({
      document,
      dialog,
      initialFocus,
      restoreFocus,
      appRoot: document.getElementById('root'),
      onClose: close,
      priority: 90,
    })
  }, [close, restoreFocus])

  useEffect(() => subscribePrivatePreviewChanges(() => setRevealVersion(version => version + 1)), [])

  const selectIndex = useCallback((index: number) => {
    if (index < 0 || index >= media.length) return
    setSelectedIdentity(identity(media[index]!))
    setCompareMode(false)
  }, [media])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!dialogRef.current?.contains(document.activeElement)) return
      if (event.target instanceof Element && event.target.closest('input,select,textarea,video,audio,[contenteditable="true"],[role="slider"]')) return
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault()
        selectIndex(selectedIndex + (event.key === 'ArrowLeft' ? -1 : 1))
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [selectIndex, selectedIndex])

  const handleTouchStart = (event: TouchEvent<HTMLDivElement>) => {
    const touch = event.changedTouches[0]
    if (touch) touchStart.current = { x: touch.clientX, y: touch.clientY }
  }
  const handleTouchEnd = (event: TouchEvent<HTMLDivElement>) => {
    const start = touchStart.current
    touchStart.current = null
    const touch = event.changedTouches[0]
    if (!start || !touch) return
    const x = touch.clientX - start.x
    const y = touch.clientY - start.y
    if (Math.abs(x) > 70 && Math.abs(x) > Math.abs(y) * 1.5) {
      selectIndex(selectedIndex + (x < 0 ? 1 : -1))
    }
  }

  if (!file || !fileIdentity) return null

  return createPortal(
    <div className="fixed inset-0 z-[100] bg-black text-white" onClick={close}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Gallery viewer"
        className="flex h-full max-h-[100dvh] flex-col"
        onClick={event => event.stopPropagation()}
      >
        <header className="flex min-h-14 items-center gap-3 border-b border-white/10 px-3 sm:px-6">
          <div className="min-w-0 flex-1">
            <p className="truncate text-[11px] text-white/55">{file.workspace} · {selectedIndex + 1} of {media.length}</p>
            <h2 className="truncate text-sm font-medium" title={file.name}>{file.name}</h2>
          </div>
          {file.type === 'image' && (
            <button type="button" onClick={() => setCompareMode(mode => !mode)} disabled={!comparisonOpen && images.length < 2} aria-pressed={comparisonOpen} title={!comparisonOpen && images.length < 2 ? 'This project needs two Gallery images to compare' : undefined} className="flex min-h-11 items-center gap-2 rounded-full px-3 text-xs text-white disabled:opacity-40 hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"><Columns2 size={17} />{comparisonOpen ? 'Close comparison' : 'Compare'}</button>
          )}
          <button
            ref={closeRef}
            type="button"
            onClick={close}
            aria-label="Close Gallery viewer"
            className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
          ><X size={20} /></button>
        </header>

        <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden px-12 py-3 sm:px-20 sm:py-5" data-media-status={comparisonOpen ? 'compare' : privateHidden ? 'hidden' : currentStatus} onTouchStart={handleTouchStart} onTouchEnd={handleTouchEnd}>
          {comparisonOpen ? (
            <GalleryComparison key={fileIdentity} current={file} images={images} />
          ) : privateHidden ? (
            <div className="flex max-w-xs flex-col items-center gap-3 text-center">
              <EyeOff size={28} className="text-white/65" aria-hidden="true" />
              <p className="text-sm text-white/75">This preview is blurred in this browser.</p>
              <button
                type="button"
                onClick={() => revealPrivatePreview(fileIdentity)}
                className="flex min-h-11 items-center gap-2 rounded-full border border-white/30 px-5 text-sm hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
              ><Eye size={16} /> Show preview</button>
            </div>
          ) : currentStatus === 'error' ? (
            <div role="alert" className="max-w-xs text-center text-sm text-white/75">This media could not be loaded. Try opening it from the Gallery again.</div>
          ) : (
            <>
              {currentStatus === 'loading' && (
                <div role="status" className="absolute flex items-center gap-2 text-sm text-white/70"><Loader2 size={17} className="animate-spin motion-reduce:animate-none" />Loading media...</div>
              )}
              {file.type === 'video' ? (
                <video key={fileIdentity} src={file.url} controls playsInline preload="metadata" className={`h-full w-full object-contain ${currentStatus === 'loading' ? 'opacity-0' : ''}`} onLoadedMetadata={() => setMediaStatus({ identity: fileIdentity, state: 'ready' })} onError={() => setMediaStatus({ identity: fileIdentity, state: 'error' })} />
              ) : (
                <ViewerImage key={fileIdentity} file={file} status={currentStatus} onReady={() => setMediaStatus({ identity: fileIdentity, state: 'ready' })} onFailure={() => setMediaStatus({ identity: fileIdentity, state: 'error' })} />
              )}
            </>
          )}
          <button
            type="button"
            aria-label="Previous media"
            disabled={selectedIndex === 0}
            onClick={() => selectIndex(selectedIndex - 1)}
            className="absolute left-1 flex h-11 w-11 items-center justify-center rounded-full bg-black/55 text-white disabled:opacity-25 hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white sm:left-5"
          ><ChevronLeft size={22} /></button>
          <button
            type="button"
            aria-label="Next media"
            disabled={selectedIndex === media.length - 1}
            onClick={() => selectIndex(selectedIndex + 1)}
            className="absolute right-1 flex h-11 w-11 items-center justify-center rounded-full bg-black/55 text-white disabled:opacity-25 hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white sm:right-5"
          ><ChevronRight size={22} /></button>
          {file.private && !privateHidden && (
            <button
              type="button"
              onClick={() => hidePrivatePreview(fileIdentity)}
              className="absolute right-3 top-3 flex min-h-11 items-center gap-2 rounded-full bg-black/65 px-3 text-xs hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white sm:right-6"
            ><EyeOff size={15} /> Blur preview</button>
          )}
        </div>

        <nav aria-label="Gallery media" className="shrink-0 border-t border-white/10 bg-black/60 px-3 pb-[max(12px,env(safe-area-inset-bottom))] pt-3 sm:px-6">
          <div className="flex max-h-24 gap-2 overflow-x-auto" role="list">
            {media.map((item, index) => {
              const itemIdentity = identity(item)
              const hidden = item.private && !privatePreviewWasRevealed(itemIdentity)
              return (
                <div key={itemIdentity} role="listitem" className="shrink-0">
                  <button
                    type="button"
                    aria-label={`${index + 1}: ${item.name}${hidden ? ', blurred' : ''}`}
                    aria-current={index === selectedIndex ? 'true' : undefined}
                    onClick={() => selectIndex(index)}
                    className={`flex h-16 w-20 items-center justify-center overflow-hidden rounded-lg border bg-white/5 text-white/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white ${index === selectedIndex ? 'border-white' : 'border-white/15 hover:border-white/50'}`}
                  >
                    {hidden ? <EyeOff size={18} /> : item.type === 'image' ? <img src={item.url} alt="" loading="lazy" className="h-full w-full object-cover" /> : <Film size={18} />}
                  </button>
                </div>
              )
            })}
          </div>
          <p className="mt-2 text-[11px] text-white/60">Use arrow keys or swipe to browse</p>
        </nav>
      </div>
    </div>,
    document.body,
  )
}
